"""Sensitivity analysis and constraint proximity reporting.

For each solver step, computes:
- Constraint slack (distance from each constraint boundary)
- Parameter sensitivity (partial derivatives of outputs w.r.t. inputs)
- Binding vs slack classification
- Confidence bands from input uncertainty propagation

This module does NOT modify solver behavior — it reports on solver results
to help engineers understand margin, sensitivity, and confidence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from solver.rake_solver import RakeSolution
    from solver.heave_solver import HeaveSolution
    from solver.arb_solver import ARBSolution
    from solver.damper_solver import DamperSolution


class ConstraintStatus(Enum):
    """How close a constraint is to its boundary."""
    BINDING = "binding"           # slack < 1%
    NEAR_BINDING = "near_binding"  # slack < 10%
    MODERATE = "moderate"          # slack 10-50%
    SLACK = "slack"               # slack > 50%


@dataclass
class ConstraintProximity:
    """Analysis of a single constraint's proximity to its boundary."""
    name: str
    description: str
    actual_value: float
    limit_value: float
    units: str
    slack: float              # limit - actual (positive = satisfied)
    slack_pct: float          # slack / |limit| * 100
    status: ConstraintStatus
    step: int                 # solver step (1-6)
    binding_explanation: str = ""  # what would need to change to relax


@dataclass
class ParameterSensitivity:
    """Sensitivity of an output to an input change."""
    output_name: str
    input_name: str
    derivative: float         # ∂output/∂input
    input_units: str
    output_units: str
    interpretation: str       # e.g. "1 mm rear RH → 0.3% DF balance shift"


@dataclass
class ConfidenceBand:
    """Uncertainty band on a solver output."""
    parameter: str
    value: float
    units: str
    sigma: float              # 1-sigma uncertainty
    confidence: str           # "high" | "medium" | "low"
    dominant_uncertainty: str  # which input contributes most


@dataclass
class SensitivityReport:
    """Complete sensitivity analysis for a solver run."""
    constraints: list[ConstraintProximity] = field(default_factory=list)
    sensitivities: list[ParameterSensitivity] = field(default_factory=list)
    confidence_bands: list[ConfidenceBand] = field(default_factory=list)

    def binding_constraints(self) -> list[ConstraintProximity]:
        """Return only binding or near-binding constraints."""
        return [c for c in self.constraints
                if c.status in (ConstraintStatus.BINDING, ConstraintStatus.NEAR_BINDING)]

    def summary(self, width: int = 63) -> str:
        """ASCII summary for engineering report."""
        lines = [
            "=" * width,
            "  SENSITIVITY ANALYSIS",
            "=" * width,
        ]

        # Constraint proximity
        lines.append("")
        lines.append("  CONSTRAINT PROXIMITY")
        lines.append("  " + "-" * (width - 4))

        for c in self.constraints:
            icon = {
                ConstraintStatus.BINDING: "!!",
                ConstraintStatus.NEAR_BINDING: "! ",
                ConstraintStatus.MODERATE: "  ",
                ConstraintStatus.SLACK: "  ",
            }[c.status]
            lines.append(
                f"  {icon} {c.name:<30s} "
                f"{c.actual_value:>7.1f} / {c.limit_value:>7.1f} {c.units:<5s} "
                f"({c.slack_pct:+.1f}%)"
            )
            if c.status in (ConstraintStatus.BINDING, ConstraintStatus.NEAR_BINDING):
                if c.binding_explanation:
                    lines.append(f"     → {c.binding_explanation}")

        # Parameter sensitivities
        if self.sensitivities:
            lines.append("")
            lines.append("  PARAMETER SENSITIVITY")
            lines.append("  " + "-" * (width - 4))
            for s in self.sensitivities:
                lines.append(f"    {s.interpretation}")

        # Confidence bands
        if self.confidence_bands:
            lines.append("")
            lines.append("  CONFIDENCE BANDS")
            lines.append("  " + "-" * (width - 4))
            for cb in self.confidence_bands:
                conf_tag = {"high": "HIGH", "medium": "MED ", "low": "LOW "}[cb.confidence]
                lines.append(
                    f"    [{conf_tag}] {cb.parameter:<24s} "
                    f"{cb.value:>7.1f} ± {cb.sigma:>5.1f} {cb.units}"
                )

        lines.append("=" * width)
        return "\n".join(lines)


# ── Constraint proximity analysis ──────────────────────────────────────

def _classify_slack(slack_pct: float) -> ConstraintStatus:
    """Classify constraint status from slack percentage."""
    abs_slack = abs(slack_pct)
    if abs_slack < 1.0:
        return ConstraintStatus.BINDING
    elif abs_slack < 10.0:
        return ConstraintStatus.NEAR_BINDING
    elif abs_slack < 50.0:
        return ConstraintStatus.MODERATE
    return ConstraintStatus.SLACK


def analyze_step1_constraints(step1: RakeSolution) -> list[ConstraintProximity]:
    """Analyze Step 1 (rake) constraint proximity."""
    constraints = []

    # Vortex burst margin
    if step1.vortex_burst_threshold_mm > 0:
        slack = step1.vortex_burst_margin_mm
        limit = step1.vortex_burst_threshold_mm
        slack_pct = (slack / limit * 100) if limit != 0 else 0
        constraints.append(ConstraintProximity(
            name="Vortex burst margin",
            description="Front dynamic RH p99 minimum vs aero stall threshold",
            actual_value=step1.front_rh_min_p99_mm,
            limit_value=step1.vortex_burst_threshold_mm,
            units="mm",
            slack=slack,
            slack_pct=slack_pct,
            status=_classify_slack(slack_pct),
            step=1,
            binding_explanation=(
                "Reduce front shock velocity (stiffer heave) or raise front RH"
                if slack_pct < 10 else ""
            ),
        ))

    # DF balance target (how close achieved balance is to target)
    # Use a ±1% window as the "limit"
    balance_error = abs(step1.df_balance_pct - 50.14)  # default target
    constraints.append(ConstraintProximity(
        name="DF balance accuracy",
        description="Achieved DF balance vs target",
        actual_value=step1.df_balance_pct,
        limit_value=50.14,
        units="%",
        slack=1.0 - balance_error,
        slack_pct=(1.0 - balance_error) / 1.0 * 100,
        status=_classify_slack((1.0 - balance_error) / 1.0 * 100),
        step=1,
    ))

    return constraints


def analyze_step2_constraints(step2: HeaveSolution) -> list[ConstraintProximity]:
    """Analyze Step 2 (heave/third) constraint proximity."""
    constraints = []

    # Front bottoming margin
    slack = step2.front_bottoming_margin_mm
    limit = step2.front_dynamic_rh_mm
    slack_pct = (slack / limit * 100) if limit > 0 else 0
    constraints.append(ConstraintProximity(
        name="Front bottoming margin",
        description="Front excursion p99 vs dynamic ride height",
        actual_value=step2.front_excursion_at_rate_mm,
        limit_value=step2.front_dynamic_rh_mm,
        units="mm",
        slack=slack,
        slack_pct=slack_pct,
        status=_classify_slack(slack_pct),
        step=2,
        binding_explanation=(
            f"Binding constraint is {step2.front_binding_constraint}. "
            "Stiffen front heave or raise front dynamic RH."
            if slack_pct < 10 else ""
        ),
    ))

    # Rear bottoming margin
    slack = step2.rear_bottoming_margin_mm
    limit = step2.rear_dynamic_rh_mm
    slack_pct = (slack / limit * 100) if limit > 0 else 0
    constraints.append(ConstraintProximity(
        name="Rear bottoming margin",
        description="Rear excursion p99 vs dynamic ride height",
        actual_value=step2.rear_excursion_at_rate_mm,
        limit_value=step2.rear_dynamic_rh_mm,
        units="mm",
        slack=slack,
        slack_pct=slack_pct,
        status=_classify_slack(slack_pct),
        step=2,
        binding_explanation=(
            f"Binding constraint is {step2.rear_binding_constraint}. "
            "Stiffen rear third or raise rear dynamic RH."
            if slack_pct < 10 else ""
        ),
    ))

    # Front sigma vs target
    sigma_target = 8.0  # mm default
    slack = sigma_target - step2.front_sigma_at_rate_mm
    slack_pct = (slack / sigma_target * 100) if sigma_target > 0 else 0
    constraints.append(ConstraintProximity(
        name="Front RH sigma",
        description="Front ride height std dev vs platform stability target",
        actual_value=step2.front_sigma_at_rate_mm,
        limit_value=sigma_target,
        units="mm",
        slack=slack,
        slack_pct=slack_pct,
        status=_classify_slack(slack_pct),
        step=2,
    ))

    # Rear sigma vs target
    sigma_target_rear = 10.0  # mm default
    slack = sigma_target_rear - step2.rear_sigma_at_rate_mm
    slack_pct = (slack / sigma_target_rear * 100) if sigma_target_rear > 0 else 0
    constraints.append(ConstraintProximity(
        name="Rear RH sigma",
        description="Rear ride height std dev vs platform stability target",
        actual_value=step2.rear_sigma_at_rate_mm,
        limit_value=sigma_target_rear,
        units="mm",
        slack=slack,
        slack_pct=slack_pct,
        status=_classify_slack(slack_pct),
        step=2,
    ))

    return constraints


# ── Parameter sensitivity (analytical derivatives) ─────────────────────

def compute_heave_sensitivities(
    v_p99_front_mps: float,
    v_p99_rear_mps: float,
    m_eff_front_kg: float,
    m_eff_rear_kg: float,
    k_front_nmm: float,
    k_rear_nmm: float,
) -> list[ParameterSensitivity]:
    """Compute analytical sensitivities for heave/third solver.

    excursion = v_p99 * sqrt(m_eff / k)
    ∂excursion/∂k = -0.5 * v_p99 * sqrt(m_eff) * k^(-3/2)
    """
    sensitivities = []

    # Front: ∂excursion/∂k_front
    k_front_nm = k_front_nmm * 1000.0
    d_exc_dk_front = -0.5 * v_p99_front_mps * math.sqrt(m_eff_front_kg) * k_front_nm ** (-1.5)
    # Convert to mm per N/mm: multiply by 1000 (mm/m) / 1000 (N/mm to N/m) = 1.0
    d_exc_dk_front_mm_per_nmm = d_exc_dk_front * 1e6  # m/(N/m) → mm/(N/mm)

    sensitivities.append(ParameterSensitivity(
        output_name="front_excursion_mm",
        input_name="front_heave_nmm",
        derivative=d_exc_dk_front_mm_per_nmm,
        input_units="N/mm",
        output_units="mm",
        interpretation=(
            f"+10 N/mm front heave → {d_exc_dk_front_mm_per_nmm * 10:+.1f}mm excursion change"
        ),
    ))

    # Rear: ∂excursion/∂k_rear
    k_rear_nm = k_rear_nmm * 1000.0
    d_exc_dk_rear = -0.5 * v_p99_rear_mps * math.sqrt(m_eff_rear_kg) * k_rear_nm ** (-1.5)
    d_exc_dk_rear_mm_per_nmm = d_exc_dk_rear * 1e6

    sensitivities.append(ParameterSensitivity(
        output_name="rear_excursion_mm",
        input_name="rear_third_nmm",
        derivative=d_exc_dk_rear_mm_per_nmm,
        input_units="N/mm",
        output_units="mm",
        interpretation=(
            f"+10 N/mm rear third → {d_exc_dk_rear_mm_per_nmm * 10:+.1f}mm excursion change"
        ),
    ))

    return sensitivities


def compute_damping_ratio_sensitivity(
    damping_coeff_nsm: float,
    spring_rate_nmm: float,
    mass_kg: float,
    axle: str,
) -> ParameterSensitivity:
    """Compute ∂ζ/∂k for a damper-spring-mass system.

    ζ = c / (2 * sqrt(k * m))
    ∂ζ/∂k = -c / (4 * k * sqrt(k * m))
    """
    k_nm = spring_rate_nmm * 1000.0
    dz_dk = -damping_coeff_nsm / (4.0 * k_nm * math.sqrt(k_nm * mass_kg))
    # Convert to per N/mm
    dz_dk_per_nmm = dz_dk * 1000.0

    return ParameterSensitivity(
        output_name=f"{axle}_damping_ratio",
        input_name=f"{axle}_spring_rate_nmm",
        derivative=dz_dk_per_nmm,
        input_units="N/mm",
        output_units="ratio",
        interpretation=(
            f"+10 N/mm {axle} spring → {dz_dk_per_nmm * 10:+.4f} damping ratio change"
        ),
    )


# ── Confidence bands (uncertainty propagation) ─────────────────────────

# Input uncertainty assumptions (calibrated from BMW Sebring validation)
INPUT_UNCERTAINTIES = {
    "ride_height_mm": 0.5,          # ±0.5mm sensor noise
    "shock_vel_p99_pct": 0.10,      # ±10% lap-to-lap variation
    "aero_compression_mm": 1.0,     # ±1.0mm model calibration
    "m_eff_pct": 0.05,              # ±5% effective mass model
    "df_balance_pct": 0.5,          # ±0.5% OptimumG baseline
}


def compute_heave_confidence(
    v_p99_mps: float,
    m_eff_kg: float,
    k_nmm: float,
    excursion_mm: float,
    axle: str,
) -> ConfidenceBand:
    """Propagate input uncertainty to heave spring recommendation.

    excursion = v * sqrt(m/k)
    k_min = m * (v*1000/rh)^2 / 1000

    Dominant uncertainties: v_p99 (±10%), m_eff (±5%)
    σ_k/k ≈ sqrt((2*σ_v/v)^2 + (σ_m/m)^2)   [from k ∝ v^2 * m]
    """
    sigma_v_frac = INPUT_UNCERTAINTIES["shock_vel_p99_pct"]
    sigma_m_frac = INPUT_UNCERTAINTIES["m_eff_pct"]

    # k ∝ v^2 * m → σ_k/k = sqrt((2σ_v/v)^2 + (σ_m/m)^2)
    sigma_k_frac = math.sqrt((2 * sigma_v_frac) ** 2 + sigma_m_frac ** 2)
    sigma_k = k_nmm * sigma_k_frac

    # Determine confidence level
    if sigma_k_frac < 0.05:
        confidence = "high"
    elif sigma_k_frac < 0.15:
        confidence = "medium"
    else:
        confidence = "low"

    # Dominant contributor
    if 2 * sigma_v_frac > sigma_m_frac:
        dominant = f"shock velocity p99 uncertainty (±{sigma_v_frac*100:.0f}%)"
    else:
        dominant = f"effective mass uncertainty (±{sigma_m_frac*100:.0f}%)"

    return ConfidenceBand(
        parameter=f"{axle}_heave_spring",
        value=k_nmm,
        units="N/mm",
        sigma=round(sigma_k, 1),
        confidence=confidence,
        dominant_uncertainty=dominant,
    )


def compute_excursion_confidence(
    v_p99_mps: float,
    m_eff_kg: float,
    k_nmm: float,
    excursion_mm: float,
    axle: str,
) -> ConfidenceBand:
    """Uncertainty on the predicted excursion value.

    excursion = v * sqrt(m/k)
    σ_exc/exc = sqrt((σ_v/v)^2 + (0.5*σ_m/m)^2 + (0.5*σ_k/k)^2)

    Since k is our output, we use v and m uncertainty only.
    """
    sigma_v_frac = INPUT_UNCERTAINTIES["shock_vel_p99_pct"]
    sigma_m_frac = INPUT_UNCERTAINTIES["m_eff_pct"]

    sigma_exc_frac = math.sqrt(sigma_v_frac ** 2 + (0.5 * sigma_m_frac) ** 2)
    sigma_exc = excursion_mm * sigma_exc_frac

    if sigma_exc_frac < 0.05:
        confidence = "high"
    elif sigma_exc_frac < 0.15:
        confidence = "medium"
    else:
        confidence = "low"

    return ConfidenceBand(
        parameter=f"{axle}_excursion_p99",
        value=excursion_mm,
        units="mm",
        sigma=round(sigma_exc, 1),
        confidence=confidence,
        dominant_uncertainty=f"shock velocity p99 (±{sigma_v_frac*100:.0f}%)",
    )


# ── Full sensitivity report builder ───────────────────────────────────

def build_sensitivity_report(
    step1: RakeSolution,
    step2: HeaveSolution,
    arb_lltd: float = 0.0,
    arb_lltd_target: float = 0.0,
    rarb_sensitivity: float = 0.0,
    car: "CarModel | None" = None,
) -> SensitivityReport:
    """Build complete sensitivity report from solver solutions.

    Args:
        step1: Rake solution (Step 1)
        step2: Heave solution (Step 2)
        arb_lltd: Achieved LLTD from Step 4 (optional)
        arb_lltd_target: Target LLTD from Step 4 (optional)
        rarb_sensitivity: ΔLLTD per RARB blade step (optional)
        car: Car model for per-car m_eff values (optional, falls back to BMW defaults)
    """
    report = SensitivityReport()

    # Step 1 constraints
    report.constraints.extend(analyze_step1_constraints(step1))

    # Step 2 constraints
    report.constraints.extend(analyze_step2_constraints(step2))

    # Step 4 LLTD constraint (if provided)
    if arb_lltd > 0 and arb_lltd_target > 0:
        lltd_error = abs(arb_lltd - arb_lltd_target)
        slack_pct = (1.0 - lltd_error / 0.05) * 100  # 5% window
        report.constraints.append(ConstraintProximity(
            name="LLTD accuracy",
            description="Achieved LLTD vs OptimumG target",
            actual_value=arb_lltd * 100,
            limit_value=arb_lltd_target * 100,
            units="%",
            slack=(0.05 - lltd_error) * 100,
            slack_pct=slack_pct,
            status=_classify_slack(slack_pct),
            step=4,
            binding_explanation=(
                f"RARB sensitivity: {rarb_sensitivity:.4f} LLTD/blade"
                if rarb_sensitivity > 0 else ""
            ),
        ))

    # Heave sensitivities — use per-car m_eff if available, else BMW defaults
    _m_eff_front = 228.0  # BMW fallback
    _m_eff_rear = 2395.3  # BMW fallback
    if car is not None:
        _hs = getattr(car, "heave_spring", None)
        if _hs is not None:
            _m_eff_front = getattr(_hs, "front_m_eff_kg", _m_eff_front)
            _m_eff_rear = getattr(_hs, "rear_m_eff_kg", _m_eff_rear)
    report.sensitivities.extend(compute_heave_sensitivities(
        v_p99_front_mps=step2.front_shock_vel_p99_mps,
        v_p99_rear_mps=step2.rear_shock_vel_p99_mps,
        m_eff_front_kg=_m_eff_front,
        m_eff_rear_kg=_m_eff_rear,
        k_front_nmm=step2.front_heave_nmm,
        k_rear_nmm=step2.rear_third_nmm,
    ))

    # RARB sensitivity (if provided)
    if rarb_sensitivity > 0:
        report.sensitivities.append(ParameterSensitivity(
            output_name="LLTD",
            input_name="rear_arb_blade",
            derivative=rarb_sensitivity,
            input_units="blade",
            output_units="ratio",
            interpretation=(
                f"1 RARB blade step → {rarb_sensitivity*100:+.2f}% LLTD shift"
            ),
        ))

    # Confidence bands
    report.confidence_bands.append(compute_heave_confidence(
        v_p99_mps=step2.front_shock_vel_p99_mps,
        m_eff_kg=228.0,
        k_nmm=step2.front_heave_nmm,
        excursion_mm=step2.front_excursion_at_rate_mm,
        axle="front",
    ))
    report.confidence_bands.append(compute_heave_confidence(
        v_p99_mps=step2.rear_shock_vel_p99_mps,
        m_eff_kg=2395.3,
        k_nmm=step2.rear_third_nmm,
        excursion_mm=step2.rear_excursion_at_rate_mm,
        axle="rear",
    ))
    report.confidence_bands.append(compute_excursion_confidence(
        v_p99_mps=step2.front_shock_vel_p99_mps,
        m_eff_kg=228.0,
        k_nmm=step2.front_heave_nmm,
        excursion_mm=step2.front_excursion_at_rate_mm,
        axle="front",
    ))

    return report
