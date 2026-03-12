"""Uncertainty quantification for solver outputs.

Propagates input measurement uncertainty through the solver to produce
confidence intervals on recommended parameter values. Distinguishes
between epistemic (reducible) and aleatoric (irreducible) uncertainty.

Key outputs:
- Confidence classification (HIGH/MEDIUM/LOW) per parameter
- 1-sigma uncertainty bands
- Dominant uncertainty source identification
- Recommendations for reducing epistemic uncertainty
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


class UncertaintyType(Enum):
    """Whether uncertainty is reducible or inherent."""
    EPISTEMIC = "epistemic"    # reducible by gathering more data
    ALEATORIC = "aleatoric"    # inherent randomness, irreducible


@dataclass
class UncertaintySource:
    """A single source of input uncertainty."""
    name: str
    value: float               # nominal value
    sigma: float               # 1-sigma uncertainty
    sigma_pct: float           # sigma / value * 100
    uncertainty_type: UncertaintyType
    reduction_action: str = ""  # how to reduce (epistemic only)


@dataclass
class OutputUncertainty:
    """Uncertainty on a single solver output."""
    parameter: str
    value: float
    units: str
    sigma: float               # 1-sigma propagated uncertainty
    sigma_pct: float           # sigma / value * 100
    confidence: str            # "high" | "medium" | "low"
    dominant_source: str       # which input contributes most
    contributions: dict[str, float] = field(default_factory=dict)  # source → σ² contribution


@dataclass
class UncertaintyReport:
    """Complete uncertainty analysis for a solver run."""
    input_sources: list[UncertaintySource] = field(default_factory=list)
    output_uncertainties: list[OutputUncertainty] = field(default_factory=list)
    experimentation_recommendations: list[str] = field(default_factory=list)

    def summary(self, width: int = 63) -> str:
        """ASCII summary for engineering report."""
        lines = [
            "=" * width,
            "  UNCERTAINTY QUANTIFICATION",
            "=" * width,
        ]

        # Input uncertainties
        lines.append("")
        lines.append("  INPUT UNCERTAINTY SOURCES")
        lines.append("  " + "-" * (width - 4))
        for src in self.input_sources:
            utype = "E" if src.uncertainty_type == UncertaintyType.EPISTEMIC else "A"
            lines.append(
                f"    [{utype}] {src.name:<28s} "
                f"±{src.sigma_pct:>5.1f}%"
            )

        # Output uncertainties
        lines.append("")
        lines.append("  OUTPUT CONFIDENCE")
        lines.append("  " + "-" * (width - 4))
        for out in self.output_uncertainties:
            conf_tag = {"high": "HIGH", "medium": "MED ", "low": "LOW "}[out.confidence]
            lines.append(
                f"    [{conf_tag}] {out.parameter:<24s} "
                f"{out.value:>7.1f} ± {out.sigma:>5.1f} {out.units}"
            )
            lines.append(
                f"           Dominant: {out.dominant_source}"
            )

        # Experimentation recommendations
        if self.experimentation_recommendations:
            lines.append("")
            lines.append("  RECOMMENDED EXPERIMENTS")
            lines.append("  " + "-" * (width - 4))
            for rec in self.experimentation_recommendations:
                lines.append(f"    → {rec}")

        lines.append("=" * width)
        return "\n".join(lines)


# ── Standard input uncertainty definitions ─────────────────────────────

def get_input_uncertainties(
    shock_vel_p99_front_mps: float = 0.260,
    shock_vel_p99_rear_mps: float = 0.324,
    m_eff_front_kg: float = 228.0,
    m_eff_rear_kg: float = 2395.3,
    n_laps: int = 5,
) -> list[UncertaintySource]:
    """Define standard input uncertainty sources.

    Args:
        shock_vel_p99_*: Track-measured p99 shock velocities
        m_eff_*: Calibrated effective masses
        n_laps: Number of laps in the telemetry sample

    Returns:
        List of UncertaintySource objects
    """
    # Lap-to-lap shock velocity variation reduces with more data
    # σ_v scales as 1/sqrt(n_laps) for the mean estimate
    base_v_sigma_pct = 0.15  # 15% base variation (single lap)
    v_sigma_pct = base_v_sigma_pct / math.sqrt(max(n_laps, 1))

    sources = [
        UncertaintySource(
            name="Front shock vel p99",
            value=shock_vel_p99_front_mps,
            sigma=shock_vel_p99_front_mps * v_sigma_pct,
            sigma_pct=v_sigma_pct * 100,
            uncertainty_type=UncertaintyType.ALEATORIC,
            reduction_action=(
                f"Run more laps (currently {n_laps}; "
                f"10+ laps reduces to ±{base_v_sigma_pct/math.sqrt(10)*100:.1f}%)"
            ),
        ),
        UncertaintySource(
            name="Rear shock vel p99",
            value=shock_vel_p99_rear_mps,
            sigma=shock_vel_p99_rear_mps * v_sigma_pct,
            sigma_pct=v_sigma_pct * 100,
            uncertainty_type=UncertaintyType.ALEATORIC,
        ),
        UncertaintySource(
            name="Front effective mass",
            value=m_eff_front_kg,
            sigma=m_eff_front_kg * 0.05,
            sigma_pct=5.0,
            uncertainty_type=UncertaintyType.EPISTEMIC,
            reduction_action="Validate with measured heave frequency at known spring rate",
        ),
        UncertaintySource(
            name="Rear effective mass",
            value=m_eff_rear_kg,
            sigma=m_eff_rear_kg * 0.05,
            sigma_pct=5.0,
            uncertainty_type=UncertaintyType.EPISTEMIC,
            reduction_action="Validate with measured third spring frequency",
        ),
        UncertaintySource(
            name="Aero compression model",
            value=15.0,  # mm, typical front compression
            sigma=1.0,
            sigma_pct=6.7,
            uncertainty_type=UncertaintyType.EPISTEMIC,
            reduction_action="Compare predicted vs measured RH at multiple speeds",
        ),
        UncertaintySource(
            name="Ride height sensor offset",
            value=0.0,
            sigma=0.5,
            sigma_pct=0.0,  # offset, not ratio
            uncertainty_type=UncertaintyType.ALEATORIC,
        ),
        UncertaintySource(
            name="DF balance target",
            value=50.14,
            sigma=0.5,
            sigma_pct=1.0,
            uncertainty_type=UncertaintyType.EPISTEMIC,
            reduction_action="Validate with driver feedback on aero balance",
        ),
    ]

    return sources


# ── Propagation for each solver step ──────────────────────────────────

def propagate_heave_uncertainty(
    v_p99_mps: float,
    m_eff_kg: float,
    k_nmm: float,
    excursion_mm: float,
    axle: str,
    n_laps: int = 5,
) -> OutputUncertainty:
    """Propagate uncertainty to heave spring recommendation.

    k_min = m_eff * (v_p99 * 1000 / rh_dynamic)^2 / 1000
    Since k ∝ v² * m, we have:
    (σ_k/k)² = (2 * σ_v/v)² + (σ_m/m)²
    """
    base_v_sigma_pct = 0.15 / math.sqrt(max(n_laps, 1))
    sigma_m_frac = 0.05

    # Variance contributions
    v_contribution = (2 * base_v_sigma_pct) ** 2
    m_contribution = sigma_m_frac ** 2

    total_var = v_contribution + m_contribution
    sigma_k_frac = math.sqrt(total_var)
    sigma_k = k_nmm * sigma_k_frac

    # Confidence classification
    if sigma_k_frac < 0.05:
        confidence = "high"
    elif sigma_k_frac < 0.15:
        confidence = "medium"
    else:
        confidence = "low"

    # Dominant source
    if v_contribution > m_contribution:
        dominant = f"shock velocity p99 (±{base_v_sigma_pct*100:.1f}%)"
    else:
        dominant = f"effective mass (±{sigma_m_frac*100:.0f}%)"

    return OutputUncertainty(
        parameter=f"{axle}_heave_spring",
        value=k_nmm,
        units="N/mm",
        sigma=round(sigma_k, 1),
        sigma_pct=round(sigma_k_frac * 100, 1),
        confidence=confidence,
        dominant_source=dominant,
        contributions={
            "shock_vel_p99": v_contribution,
            "effective_mass": m_contribution,
        },
    )


def propagate_lltd_uncertainty(
    lltd: float,
    k_roll_front: float,
    k_roll_rear: float,
    rarb_sensitivity: float,
) -> OutputUncertainty:
    """Propagate uncertainty to LLTD estimate.

    LLTD = K_front / (K_front + K_rear)

    Uncertainty dominated by ARB stiffness calibration (±10%)
    and corner spring rate (±5%).
    """
    sigma_k_frac = 0.10  # ±10% ARB stiffness uncertainty
    # ∂LLTD/∂K_front = K_rear / (K_front + K_rear)²
    k_total = k_roll_front + k_roll_rear
    if k_total <= 0:
        return OutputUncertainty(
            parameter="LLTD",
            value=lltd * 100,
            units="%",
            sigma=5.0,
            sigma_pct=10.0,
            confidence="low",
            dominant_source="insufficient roll stiffness data",
        )

    dLLTD_dKf = k_roll_rear / (k_total ** 2)
    sigma_lltd = abs(dLLTD_dKf * k_roll_front * sigma_k_frac)

    confidence = "high" if sigma_lltd < 0.02 else "medium" if sigma_lltd < 0.05 else "low"

    return OutputUncertainty(
        parameter="LLTD",
        value=round(lltd * 100, 1),
        units="%",
        sigma=round(sigma_lltd * 100, 1),
        sigma_pct=round(sigma_lltd / max(lltd, 0.01) * 100, 1),
        confidence=confidence,
        dominant_source=f"ARB stiffness calibration (±{sigma_k_frac*100:.0f}%)",
    )


def propagate_damper_uncertainty(
    click: int,
    axle: str,
    regime: str,
    direction: str,
) -> OutputUncertainty:
    """Uncertainty on damper click recommendation.

    Damper click mapping has inherent uncertainty from:
    - Force-per-click calibration (±15%)
    - Desired force from ζ calculation (±10%)
    - Temperature variation in shim-stack (±14-16% for shim-stack, ±4% for DSSV)
    """
    # Damper recommendations inherently have higher uncertainty
    sigma_clicks = 1.5  # ±1.5 clicks typical
    sigma_pct = sigma_clicks / max(click, 1) * 100

    confidence = "low"  # damper clicks are always low confidence
    if sigma_pct < 15:
        confidence = "medium"

    return OutputUncertainty(
        parameter=f"{axle}_{regime}_{direction}",
        value=float(click),
        units="clicks",
        sigma=sigma_clicks,
        sigma_pct=round(sigma_pct, 1),
        confidence=confidence,
        dominant_source="force-per-click calibration + temperature variation",
    )


# ── Experimentation recommendations ───────────────────────────────────

def recommend_experiments(
    output_uncertainties: list[OutputUncertainty],
    n_laps: int = 5,
) -> list[str]:
    """Generate recommendations for reducing uncertainty.

    Identifies cases where:
    - Two parameter values are within 1σ (try both)
    - Constraint is binding with high uncertainty (test on track)
    - Epistemic uncertainty dominates (collect more data)
    """
    recs = []

    # Check if more laps would help
    if n_laps < 10:
        recs.append(
            f"Run {10 - n_laps} more laps to reduce shock velocity "
            f"uncertainty from ±{15/math.sqrt(n_laps):.1f}% "
            f"to ±{15/math.sqrt(10):.1f}%"
        )

    # Check for low-confidence outputs
    low_conf = [o for o in output_uncertainties if o.confidence == "low"]
    if low_conf:
        params = ", ".join(o.parameter for o in low_conf[:3])
        recs.append(
            f"Parameters with LOW confidence ({params}): "
            "validate with on-track A/B testing"
        )

    # Check for outputs where ±1σ spans a garage step
    for out in output_uncertainties:
        if out.units == "N/mm" and out.sigma >= 10:
            recs.append(
                f"{out.parameter}: uncertainty ±{out.sigma:.0f} {out.units} "
                f"spans one garage step. Try both "
                f"{out.value - 10:.0f} and {out.value + 10:.0f} on track."
            )
        elif out.units == "clicks" and out.sigma >= 1.0:
            recs.append(
                f"{out.parameter}: uncertainty ±{out.sigma:.1f} clicks. "
                f"Try clicks {max(1, int(out.value - 1))} and "
                f"{int(out.value + 1)} on track."
            )

    return recs


# ── Full uncertainty report builder ───────────────────────────────────

def build_uncertainty_report(
    front_heave_nmm: float,
    rear_third_nmm: float,
    front_excursion_mm: float,
    rear_excursion_mm: float,
    v_p99_front_mps: float = 0.260,
    v_p99_rear_mps: float = 0.324,
    m_eff_front_kg: float = 228.0,
    m_eff_rear_kg: float = 2395.3,
    lltd: float = 0.50,
    k_roll_front: float = 5500.0,
    k_roll_rear: float = 5500.0,
    rarb_sensitivity: float = 0.01,
    damper_clicks: dict[str, int] | None = None,
    n_laps: int = 5,
) -> UncertaintyReport:
    """Build complete uncertainty report for a solver run."""

    report = UncertaintyReport()

    # Input sources
    report.input_sources = get_input_uncertainties(
        v_p99_front_mps, v_p99_rear_mps,
        m_eff_front_kg, m_eff_rear_kg,
        n_laps,
    )

    # Heave spring uncertainties
    report.output_uncertainties.append(propagate_heave_uncertainty(
        v_p99_front_mps, m_eff_front_kg, front_heave_nmm,
        front_excursion_mm, "front", n_laps,
    ))
    report.output_uncertainties.append(propagate_heave_uncertainty(
        v_p99_rear_mps, m_eff_rear_kg, rear_third_nmm,
        rear_excursion_mm, "rear", n_laps,
    ))

    # LLTD uncertainty
    report.output_uncertainties.append(propagate_lltd_uncertainty(
        lltd, k_roll_front, k_roll_rear, rarb_sensitivity,
    ))

    # Damper uncertainties
    if damper_clicks:
        for key, click in damper_clicks.items():
            parts = key.split("_", 2)
            if len(parts) >= 3:
                axle, regime, direction = parts[0], parts[1], parts[2]
                report.output_uncertainties.append(
                    propagate_damper_uncertainty(click, axle, regime, direction)
                )

    # Experimentation recommendations
    report.experimentation_recommendations = recommend_experiments(
        report.output_uncertainties, n_laps,
    )

    return report
