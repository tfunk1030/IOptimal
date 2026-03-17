from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from solver.candidate_ranker import CandidateScore, combine_candidate_score
from solver.predictor import PredictedTelemetry, PredictionConfidence, predict_candidate_telemetry


@dataclass
class SetupCandidate:
    family: str
    description: str
    step1: object | None = None
    step2: object | None = None
    step3: object | None = None
    step4: object | None = None
    step5: object | None = None
    step6: object | None = None
    supporting: object | None = None
    predicted: object | None = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    score: CandidateScore | None = None
    selected: bool = False


def generate_candidate_families(
    *,
    authority_session: Any,
    best_session: Any,
    overhaul_assessment: Any | None,
    legal_validation: Any | None,
    authority_score: dict[str, object] | None = None,
    envelope_distance: float = 0.0,
    setup_distance: float = 0.0,
    produced_solution: dict[str, Any] | None = None,
    prediction_corrections: dict[str, float] | None = None,
) -> list[SetupCandidate]:
    """Generate minimal PR4a candidate families.

    This first pass creates two family-level candidates:
    - incremental: keep iterating from the current authority setup concept
    - baseline_reset: move toward the newly produced reset-capable solution

    The function is intentionally metadata-driven so it can operate even in
    reduced environments where the full solver is not runnable in tests.
    """
    produced_solution = produced_solution or {}
    overhaul_class = getattr(overhaul_assessment, "classification", "minor_tweak")
    overhaul_conf = float(getattr(overhaul_assessment, "confidence", 0.55) or 0.55)
    authority_conf = float((authority_score or {}).get("score", 0.6) or 0.6)
    legality_ok = 1.0 if legal_validation is None or getattr(legal_validation, "valid", True) else 0.55

    incremental_notes = [
        f"Authority session: {getattr(authority_session, 'label', 'unknown')}",
        f"Authority score: {authority_conf:.3f}",
        f"Overhaul classification: {overhaul_class}",
    ]
    incremental = SetupCandidate(
        family="incremental",
        description="Refine current authority setup concept",
        confidence=round(min(1.0, authority_conf * 0.75 + overhaul_conf * 0.15 + 0.1), 3),
        reasons=incremental_notes,
        predicted=PredictedTelemetry(
            front_heave_travel_used_pct=_safe_attr(authority_session, "measured", "front_heave_travel_used_pct"),
            front_excursion_mm=_safe_attr(authority_session, "measured", "front_rh_excursion_measured_mm"),
            rear_rh_std_mm=_safe_attr(authority_session, "measured", "rear_rh_std_mm"),
            braking_pitch_deg=_safe_attr(authority_session, "measured", "pitch_range_braking_deg"),
            front_lock_p95=_safe_attr(authority_session, "measured", "front_braking_lock_ratio_p95"),
            rear_power_slip_p95=_safe_attr(authority_session, "measured", "rear_power_slip_ratio_p95"),
            body_slip_p95_deg=_safe_attr(authority_session, "measured", "body_slip_p95_deg"),
            understeer_low_deg=_safe_attr(authority_session, "measured", "understeer_low_speed_deg"),
            understeer_high_deg=_safe_attr(authority_session, "measured", "understeer_high_speed_deg"),
            front_pressure_hot_kpa=_safe_attr(authority_session, "measured", "front_pressure_mean_kpa"),
            rear_pressure_hot_kpa=_safe_attr(authority_session, "measured", "rear_pressure_mean_kpa"),
        ),
    )
    incremental.score = combine_candidate_score(
        safety=max(0.2, 0.85 - envelope_distance * 0.08),
        performance=max(0.2, authority_conf),
        stability=max(0.2, 0.8 - setup_distance * 0.05),
        confidence=incremental.confidence,
        disruption_cost=0.15,
        notes=incremental_notes,
    )

    reset_notes = [
        f"Best benchmark session: {getattr(best_session, 'label', 'unknown')}",
        f"Envelope distance: {envelope_distance:.3f}",
        f"Setup distance: {setup_distance:.3f}",
    ]
    reset = SetupCandidate(
        family="baseline_reset",
        description="Reset toward a healthier validated baseline",
        step1=produced_solution.get("step1"),
        step2=produced_solution.get("step2"),
        step3=produced_solution.get("step3"),
        step4=produced_solution.get("step4"),
        step5=produced_solution.get("step5"),
        step6=produced_solution.get("step6"),
        supporting=produced_solution.get("supporting"),
        confidence=round(min(1.0, overhaul_conf * 0.6 + legality_ok * 0.25 + 0.15), 3),
        reasons=reset_notes,
    )
    if produced_solution and hasattr(authority_session, "setup") and hasattr(authority_session, "measured"):
        reset.predicted, reset_prediction_conf = predict_candidate_telemetry(
            current_setup=authority_session.setup,
            baseline_measured=authority_session.measured,
            step2=produced_solution.get("step2"),
            step4=produced_solution.get("step4"),
            supporting=produced_solution.get("supporting"),
            corrections=prediction_corrections,
        )
        reset.confidence = round(min(1.0, (reset.confidence + reset_prediction_conf.overall) / 2.0), 3)
    reset.score = combine_candidate_score(
        safety=max(0.25, legality_ok * 0.9 + min(0.25, envelope_distance * 0.04)),
        performance=max(0.2, 0.7 + min(0.15, envelope_distance * 0.03)),
        stability=max(0.2, 0.72 + min(0.18, setup_distance * 0.03)),
        confidence=reset.confidence,
        disruption_cost=0.75 if overhaul_class == "minor_tweak" else 0.55,
        notes=reset_notes,
    )

    if overhaul_class == "baseline_reset":
        reset.score.total = round(reset.score.total + 0.08, 3)
        incremental.score.total = round(max(0.0, incremental.score.total - 0.08), 3)
        reset.reasons.append("Reset candidate boosted because overhaul classification is baseline_reset.")
    elif overhaul_class == "moderate_rework":
        reset.score.total = round(reset.score.total + 0.03, 3)
        incremental.reasons.append("Incremental candidate kept viable despite broader rework need.")
    else:
        incremental.score.total = round(incremental.score.total + 0.04, 3)
        reset.reasons.append("Reset candidate penalized because overhaul classification remains minor_tweak.")

    candidates = [incremental, reset]

    if produced_solution and hasattr(authority_session, "measured"):
        compromise_pred = _blend_predictions(incremental.predicted, reset.predicted)
        compromise_notes = [
            "Blend authority-session drivability with reset-family safety improvements.",
            f"Authority score: {authority_conf:.3f}",
            f"Overhaul classification: {overhaul_class}",
        ]
        compromise = SetupCandidate(
            family="compromise",
            description="Blend current authority concept with safer reset direction",
            step1=produced_solution.get("step1"),
            step2=produced_solution.get("step2"),
            step3=produced_solution.get("step3"),
            step4=produced_solution.get("step4"),
            step5=produced_solution.get("step5"),
            step6=produced_solution.get("step6"),
            supporting=produced_solution.get("supporting"),
            predicted=compromise_pred,
            confidence=round(min(1.0, (incremental.confidence + reset.confidence) / 2.0), 3),
            reasons=compromise_notes,
        )
        compromise.score = combine_candidate_score(
            safety=max(0.2, ((incremental.score.safety if incremental.score else 0.5) + (reset.score.safety if reset.score else 0.5)) / 2.0),
            performance=max(0.2, ((incremental.score.performance if incremental.score else 0.5) + (reset.score.performance if reset.score else 0.5)) / 2.0 + 0.03),
            stability=max(0.2, ((incremental.score.stability if incremental.score else 0.5) + (reset.score.stability if reset.score else 0.5)) / 2.0),
            confidence=compromise.confidence,
            disruption_cost=0.4 if overhaul_class == "moderate_rework" else 0.5,
            notes=compromise_notes,
        )
        if overhaul_class == "moderate_rework":
            compromise.score.total = round(compromise.score.total + 0.06, 3)
            compromise.reasons.append("Compromise candidate boosted because overhaul classification is moderate_rework.")
        candidates.append(compromise)

    winner = max(candidates, key=lambda candidate: candidate.score.total if candidate.score is not None else -1.0)
    winner.selected = True
    return candidates


def _safe_attr(obj: Any, parent: str, field: str) -> float | None:
    parent_obj = getattr(obj, parent, None)
    if parent_obj is None:
        return None
    try:
        value = getattr(parent_obj, field, None)
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _blend_predictions(
    incremental: PredictedTelemetry | None,
    reset: PredictedTelemetry | None,
) -> PredictedTelemetry | None:
    if incremental is None and reset is None:
        return None
    if incremental is None:
        return reset
    if reset is None:
        return incremental

    def _avg(lhs: float | None, rhs: float | None) -> float | None:
        if lhs is None and rhs is None:
            return None
        if lhs is None:
            return rhs
        if rhs is None:
            return lhs
        return round((lhs + rhs) / 2.0, 4)

    return PredictedTelemetry(
        front_heave_travel_used_pct=_avg(incremental.front_heave_travel_used_pct, reset.front_heave_travel_used_pct),
        front_excursion_mm=_avg(incremental.front_excursion_mm, reset.front_excursion_mm),
        rear_rh_std_mm=_avg(incremental.rear_rh_std_mm, reset.rear_rh_std_mm),
        braking_pitch_deg=_avg(incremental.braking_pitch_deg, reset.braking_pitch_deg),
        front_lock_p95=_avg(incremental.front_lock_p95, reset.front_lock_p95),
        rear_power_slip_p95=_avg(incremental.rear_power_slip_p95, reset.rear_power_slip_p95),
        body_slip_p95_deg=_avg(incremental.body_slip_p95_deg, reset.body_slip_p95_deg),
        understeer_low_deg=_avg(incremental.understeer_low_deg, reset.understeer_low_deg),
        understeer_high_deg=_avg(incremental.understeer_high_deg, reset.understeer_high_deg),
        front_pressure_hot_kpa=_avg(incremental.front_pressure_hot_kpa, reset.front_pressure_hot_kpa),
        rear_pressure_hot_kpa=_avg(incremental.rear_pressure_hot_kpa, reset.rear_pressure_hot_kpa),
    )
