"""Discrepancy classification, cascade detection, and confidence scoring.

Classifies each comparison as:
- confirmed: prediction matches reality within tolerance
- tweak: small discrepancy, parameter adjustment within current framework
- rethink: large discrepancy, model assumption is wrong

Detects cascade errors: if Step 1 ride heights are wrong, downstream
steps may be wrong because of the cascade, not independent errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from validator.compare import Comparison
from validator.extract import MeasuredState


# Step weights for confidence scoring.
# Higher weight = more impact on confidence when wrong.
# Step 1 errors cascade to everything, so they carry most weight.
STEP_WEIGHTS = {
    1: 25,
    2: 20,
    3: 10,
    4: 20,
    5: 5,
    6: 20,
}


@dataclass
class ValidationResult:
    """Complete validation outcome."""
    overall_verdict: str           # "good_setup" / "needs_tweaking" / "rethink"
    confidence_score: int          # 0-100
    confirmed: list[Comparison] = field(default_factory=list)
    tweaks: list[Comparison] = field(default_factory=list)
    rethinks: list[Comparison] = field(default_factory=list)
    cascade_chain: list[str] = field(default_factory=list)


def classify_comparison(comp: Comparison) -> str:
    """Classify a single comparison as 'confirmed', 'tweak', or 'rethink'."""
    abs_delta = abs(comp.delta)

    # For count-based comparisons (bottoming events, vortex burst)
    if comp.units == "events":
        if comp.measured <= comp.tolerance_abs:
            return "confirmed"
        elif comp.measured <= comp.rethink_abs:
            return "tweak"
        else:
            return "rethink"

    # For continuous comparisons
    if abs_delta <= comp.tolerance_abs:
        return "confirmed"
    elif abs_delta <= comp.rethink_abs:
        return "tweak"
    else:
        return "rethink"


def classify_discrepancies(
    comparisons: list[Comparison],
    measured: MeasuredState,
    solver_json: dict,
) -> ValidationResult:
    """Classify all comparisons and compute overall verdict.

    Args:
        comparisons: List of Comparison objects from compare_all()
        measured: MeasuredState for cascade detection
        solver_json: Solver output for cascade recomputation

    Returns:
        ValidationResult with classifications, confidence, and cascade chain
    """
    confirmed = []
    tweaks = []
    rethinks = []

    for comp in comparisons:
        label = classify_comparison(comp)
        if label == "confirmed":
            confirmed.append(comp)
        elif label == "tweak":
            tweaks.append(comp)
        else:
            rethinks.append(comp)

    # --- Cascade detection ---
    cascade_chain = _detect_cascade(comparisons, rethinks, tweaks)

    # --- Overall verdict ---
    verdict = _compute_verdict(comparisons, rethinks, tweaks)

    # --- Confidence score ---
    score = _compute_confidence(comparisons, confirmed, tweaks, rethinks)

    return ValidationResult(
        overall_verdict=verdict,
        confidence_score=score,
        confirmed=confirmed,
        tweaks=tweaks,
        rethinks=rethinks,
        cascade_chain=cascade_chain,
    )


def _compute_verdict(
    comparisons: list[Comparison],
    rethinks: list[Comparison],
    tweaks: list[Comparison],
) -> str:
    """Determine overall verdict from classifications."""

    # Rethink triggers:
    # 1. Step 1 aero compression rethink (V^2 model fundamentally wrong)
    #    NOTE: aero compression comparison has widened tolerances to account
    #    for the sensor-vs-AeroCalc reference frame offset.  A rethink here
    #    means the V^2 scaling itself is broken, not just a frame offset.
    step1_rh_rethink = any(
        c.step == 1 and c.parameter.startswith("aero_compression") and c in rethinks
        for c in comparisons
    )

    # 2. Vortex burst events > threshold (safety-critical)
    vortex_rethink = any(
        c.parameter == "vortex_burst_events" and c in rethinks
        for c in comparisons
    )

    # 3. Both LLTD and roll_gradient rethink (balance model failed)
    lltd_rethink = any(
        c.parameter == "lltd" and c in rethinks for c in comparisons
    )
    roll_grad_rethink = any(
        c.parameter == "roll_gradient_deg_per_g" and c in rethinks
        for c in comparisons
    )
    balance_rethink = lltd_rethink and roll_grad_rethink

    # 4. Severe understeer or oversteer (handling fundamentally wrong)
    handling_rethink = any(
        c.parameter in ("understeer_mean_deg", "understeer_speed_gradient")
        and c in rethinks for c in comparisons
    )

    # 5. 3+ rethink-level items
    many_rethinks = len(rethinks) >= 3

    if (step1_rh_rethink or vortex_rethink or balance_rethink
            or handling_rethink or many_rethinks):
        return "rethink"

    if tweaks:
        return "needs_tweaking"

    return "good_setup"


def _compute_confidence(
    comparisons: list[Comparison],
    confirmed: list[Comparison],
    tweaks: list[Comparison],
    rethinks: list[Comparison],
) -> int:
    """Compute confidence score 0-100.

    Starts at 100, deducts based on severity and step importance.
    """
    score = 100.0

    for comp in tweaks:
        weight = STEP_WEIGHTS.get(comp.step, 10)
        if comp.tolerance_abs > 0:
            severity = abs(comp.delta) / comp.tolerance_abs - 1.0
        else:
            severity = 1.0
        score -= weight * severity * 0.5

    for comp in rethinks:
        weight = STEP_WEIGHTS.get(comp.step, 10)
        score -= weight * 2.0

    return max(0, min(100, round(score)))


def _detect_cascade(
    comparisons: list[Comparison],
    rethinks: list[Comparison],
    tweaks: list[Comparison],
) -> list[str]:
    """Detect which errors are caused by upstream Step 1 errors.

    If Step 1 ride heights are wrong, downstream steps inherit the error.
    This separates cascade errors from independent errors.
    """
    cascade_notes = []

    # Check if Step 1 has an aero compression error
    step1_front_delta = 0.0
    step1_rear_delta = 0.0
    step1_has_error = False

    for comp in comparisons:
        if comp.step == 1 and comp.parameter == "aero_compression_front_mm":
            step1_front_delta = comp.delta
            if comp in tweaks or comp in rethinks:
                step1_has_error = True
        if comp.step == 1 and comp.parameter == "aero_compression_rear_mm":
            step1_rear_delta = comp.delta
            if comp in tweaks or comp in rethinks:
                step1_has_error = True

    if not step1_has_error:
        return cascade_notes

    # Aero compression error cascades to:
    # - Step 2: excursion margin changes (different dynamic RH)
    # - Step 4: roll stiffness at different RH might change
    # - Step 5: body roll at different RH changes camber calc

    if abs(step1_front_delta) > 2.0:
        cascade_notes.append(
            f"Step 1 front aero compression off by {step1_front_delta:+.1f}mm. "
            f"Steps 2/4/5 may inherit this error."
        )

    if abs(step1_rear_delta) > 3.0:
        cascade_notes.append(
            f"Step 1 rear aero compression off by {step1_rear_delta:+.1f}mm. "
            f"Steps 2/4/5 may inherit this error."
        )

    # Check Step 2 excursion: would it be correct with true RH?
    for comp in comparisons:
        if comp.step == 2 and comp.parameter == "front_excursion_p99_mm":
            if comp in tweaks or comp in rethinks:
                # The excursion model is independent of RH. If excursion
                # prediction is wrong, it is NOT a cascade from Step 1.
                # It is an m_eff or shock velocity error.
                cascade_notes.append(
                    f"Step 2 excursion error is INDEPENDENT of Step 1 RH. "
                    f"Check m_eff calibration or shock velocity input."
                )

    return cascade_notes
