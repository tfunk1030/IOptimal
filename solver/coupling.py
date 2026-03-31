"""Parameter coupling map for the GTP setup solver.

Many setup parameters have downstream effects on other quantities that the
solver computes sequentially. This module makes those couplings explicit so:

1. Engineers understand the knock-on effects of a change before making it.
2. The solver can print coupling notes in the output report.
3. Future multi-objective optimization can use sensitivity data.

Usage:
    from solver.coupling import explain_change
    print(explain_change("rear_perch_offset_mm", +2.0))
    # -> "rear_perch_offset_mm +2.0 mm -> rear_static_rh_mm +0.19 mm, df_balance_pct -0.06 %"
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CoupledEffect:
    """A single downstream effect of changing one parameter."""
    downstream_param: str   # Parameter that changes as a result
    gain: float             # How much it changes per unit of the source
    unit_source: str        # Unit of the source parameter
    unit_effect: str        # Unit of the downstream parameter
    note: str = ""          # Optional physics explanation

    def describe(self, delta: float) -> str:
        """Describe the effect of a delta change in the source parameter."""
        effect = self.gain * delta
        sign = "+" if effect >= 0 else ""
        return (
            f"{self.downstream_param} {sign}{effect:.3g} {self.unit_effect}"
            + (f"  # {self.note}" if self.note else "")
        )


# ─────────────────────────────────────────────────────────────────
# COUPLING_CHAINS: parameter → list of CoupledEffect objects
#
# IMPORTANT: All gains below are calibrated from BMW Sebring data.
# For non-BMW cars, these gains are APPROXIMATE — the linearisation
# points (OD=13.9mm, heave=30 N/mm, rear perch model) differ by chassis.
# Sources: SKILL.md, per-car-quirks.md, empirical regressions (March 2026).
# ─────────────────────────────────────────────────────────────────

COUPLING_CHAINS: dict[str, list[CoupledEffect]] = {

    # ── Rear perch offset (mm) ──────────────────────────────────────
    # Each +1 mm of rear perch offset raises rear static RH ≈ +0.096 mm
    # (very weak — confirmed across 13 BMW sessions, R²=0.97 rear RH model).
    # Higher rear RH shifts aero balance rearward (less front DF) → -0.03 %/mm
    "rear_perch_offset_mm": [
        CoupledEffect(
            downstream_param="rear_static_rh_mm",
            gain=0.096,
            unit_source="mm",
            unit_effect="mm",
            note="calibrated rear RH model (13 sessions, R²=0.97)",
        ),
        CoupledEffect(
            downstream_param="df_balance_pct",
            gain=-0.03,
            unit_source="mm",
            unit_effect="%",
            note="higher rear RH increases rear DF, shifts balance rearward",
        ),
    ],

    # ── Front torsion bar OD (mm) ───────────────────────────────────
    # Torsion bar rate scales as OD^4. Around the BMW baseline (13.9 mm),
    # each +1 mm OD ≈ +8.5 N/mm wheel rate (linearized at OD=13.9).
    # Stiffer front corner spring → more front roll stiffness → LLTD increases.
    "torsion_bar_od_mm": [
        CoupledEffect(
            downstream_param="front_wheel_rate_nmm",
            gain=8.5,
            unit_source="mm",
            unit_effect="N/mm",
            note="linearized OD^4 sensitivity around BMW baseline 13.9 mm",
        ),
        CoupledEffect(
            downstream_param="lltd_pct",
            gain=0.8,
            unit_source="mm",
            unit_effect="%",
            note="stiffer front spring → more front roll stiffness → higher LLTD",
        ),
    ],

    # ── Rear ARB blade ─────────────────────────────────────────────
    # Empirical from BMW Sebring: each blade step adds ~3.02% LLTD shift.
    # Sign is NEGATIVE: stiffer rear ARB moves load transfer rearward,
    # meaning the front axle GAINS grip → LLTD (front load transfer share) DECREASES.
    "rear_arb_blade": [
        CoupledEffect(
            downstream_param="lltd_pct",
            gain=-3.02,
            unit_source="blade",
            unit_effect="%",
            note="stiffer RARB moves load transfer to rear → front GAINS grip → LLTD decreases",
        ),
    ],

    # ── Front heave spring rate (N/mm) ─────────────────────────────
    # Each +1 N/mm heave rate → +0.3 mm bottoming margin (less excursion).
    # Also raises natural frequency: Δf ≈ 0.02 Hz per N/mm (around 30 N/mm baseline).
    "front_heave_nmm": [
        CoupledEffect(
            downstream_param="front_bottoming_margin_mm",
            gain=0.3,
            unit_source="N/mm",
            unit_effect="mm",
            note="stiffer heave → smaller p99 RH excursion → more bottoming margin",
        ),
        CoupledEffect(
            downstream_param="front_natural_freq_hz",
            gain=0.02,
            unit_source="N/mm",
            unit_effect="Hz",
            note="linearized around 30 N/mm baseline, 230 kg effective mass",
        ),
    ],

    # ── Fuel load (liters) ─────────────────────────────────────────
    # BMW M Hybrid: fuel tank is rear-biased. Each liter adds ~0.02% to rear
    # weight distribution. More rear mass → lower heave spring needed per kg.
    "fuel_load_l": [
        CoupledEffect(
            downstream_param="rear_weight_dist_pct",
            gain=0.02,
            unit_source="L",
            unit_effect="%",
            note="rear-biased fuel tank; 1 L ≈ 0.8 kg → slight rear bias",
        ),
        CoupledEffect(
            downstream_param="heave_required_nmm",
            gain=0.3,
            unit_source="L",
            unit_effect="N/mm",
            note="more mass → more suspension travel under aero load → stiffer heave needed",
        ),
    ],
}


def explain_change(param: str, delta: float) -> str:
    """Return a formatted string describing the downstream effects of changing a parameter.

    Args:
        param: Parameter name (must be a key in COUPLING_CHAINS)
        delta: Change in the parameter (positive or negative)

    Returns:
        Human-readable coupling description, e.g.:
        "rear_perch_offset_mm +2.0 mm -> rear_static_rh_mm +0.19 mm, df_balance_pct -0.06 %"

    If the parameter is not in the coupling map, returns a fallback message.
    """
    if param not in COUPLING_CHAINS:
        return (
            f"{param}: no coupling model defined "
            f"(known params: {', '.join(sorted(COUPLING_CHAINS))})"
        )

    effects = COUPLING_CHAINS[param]
    sign = "+" if delta >= 0 else ""
    # Determine unit of source from first effect
    unit_src = effects[0].unit_source if effects else ""
    parts = [f"{param} {sign}{delta:g} {unit_src}"]
    parts.append("->")
    effect_strs = [e.describe(delta) for e in effects]
    parts.append(", ".join(effect_strs))
    return " ".join(parts)


def coupling_notes_for_report(
    torsion_od_delta: float = 0.0,
    rear_arb_delta: int = 0,
    fuel_load_l: float = 89.0,
    fuel_ref_l: float = 89.0,
) -> list[str]:
    """Generate concise coupling notes for the output report.

    Args:
        torsion_od_delta: Change in torsion bar OD from baseline (mm)
        rear_arb_delta: Change in rear ARB blade from baseline (int)
        fuel_load_l: Actual fuel load (L)
        fuel_ref_l: Reference fuel load for comparison (L)

    Returns:
        List of coupling note strings (empty if all deltas are zero)
    """
    notes = []

    if abs(torsion_od_delta) >= 0.05:
        notes.append(explain_change("torsion_bar_od_mm", torsion_od_delta))

    if rear_arb_delta != 0:
        notes.append(explain_change("rear_arb_blade", float(rear_arb_delta)))

    fuel_delta = fuel_load_l - fuel_ref_l
    if abs(fuel_delta) >= 5.0:
        notes.append(explain_change("fuel_load_l", fuel_delta))

    return notes
