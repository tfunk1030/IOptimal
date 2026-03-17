from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from solver.candidate_ranker import CandidateScore, combine_candidate_score, score_from_prediction
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
    setup_cluster: Any | None = None,
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
    incremental_solution = _build_family_solution("incremental", authority_session, produced_solution, setup_cluster=setup_cluster)
    reset_solution = _build_family_solution("baseline_reset", authority_session, produced_solution, setup_cluster=setup_cluster)
    compromise_solution = _build_family_solution("compromise", authority_session, produced_solution, setup_cluster=setup_cluster)

    incremental_notes = [
        f"Authority session: {getattr(authority_session, 'label', 'unknown')}",
        f"Authority score: {authority_conf:.3f}",
        f"Overhaul classification: {overhaul_class}",
    ]
    incremental = SetupCandidate(
        family="incremental",
        description="Refine current authority setup concept",
        step1=incremental_solution.get("step1"),
        step2=incremental_solution.get("step2"),
        step3=incremental_solution.get("step3"),
        step4=incremental_solution.get("step4"),
        step5=incremental_solution.get("step5"),
        step6=incremental_solution.get("step6"),
        supporting=incremental_solution.get("supporting"),
        confidence=round(min(1.0, authority_conf * 0.75 + overhaul_conf * 0.15 + 0.1), 3),
        reasons=incremental_notes,
    )
    if produced_solution and hasattr(authority_session, "setup") and hasattr(authority_session, "measured"):
        incremental.predicted, incremental_prediction_conf = predict_candidate_telemetry(
            current_setup=authority_session.setup,
            baseline_measured=authority_session.measured,
            step2=incremental.step2,
            step4=incremental.step4,
            supporting=incremental.supporting,
            corrections=prediction_corrections,
        )
        incremental.confidence = round(min(1.0, (incremental.confidence + incremental_prediction_conf.overall) / 2.0), 3)
    else:
        incremental.predicted = PredictedTelemetry(
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
        )
    incremental.score = score_from_prediction(
        baseline_measured=getattr(authority_session, "measured", None),
        predicted=incremental.predicted,
        prediction_confidence=incremental.confidence,
        disruption_cost=0.15,
        notes=incremental_notes,
    )
    incremental.score.total = round(max(0.0, incremental.score.total - envelope_distance * 0.01 - setup_distance * 0.005), 3)

    reset_notes = [
        f"Best benchmark session: {getattr(best_session, 'label', 'unknown')}",
        f"Envelope distance: {envelope_distance:.3f}",
        f"Setup distance: {setup_distance:.3f}",
    ]
    reset = SetupCandidate(
        family="baseline_reset",
        description="Reset toward a healthier validated baseline",
        step1=reset_solution.get("step1"),
        step2=reset_solution.get("step2"),
        step3=reset_solution.get("step3"),
        step4=reset_solution.get("step4"),
        step5=reset_solution.get("step5"),
        step6=reset_solution.get("step6"),
        supporting=reset_solution.get("supporting"),
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
    reset.score = score_from_prediction(
        baseline_measured=getattr(authority_session, "measured", None),
        predicted=reset.predicted,
        prediction_confidence=reset.confidence,
        disruption_cost=0.75 if overhaul_class == "minor_tweak" else 0.55,
        notes=reset_notes,
    )
    reset.score.total = round(min(1.0, reset.score.total + legality_ok * 0.04 + envelope_distance * 0.01), 3)

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
            step1=compromise_solution.get("step1"),
            step2=compromise_solution.get("step2"),
            step3=compromise_solution.get("step3"),
            step4=compromise_solution.get("step4"),
            step5=compromise_solution.get("step5"),
            step6=compromise_solution.get("step6"),
            supporting=compromise_solution.get("supporting"),
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
            notes=compromise_notes + [
                "Compromise score is blended from predicted incremental and reset outcomes.",
            ],
        )
        if overhaul_class == "moderate_rework":
            compromise.score.total = round(compromise.score.total + 0.12, 3)
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


def _build_family_solution(
    family: str,
    authority_session: Any,
    produced_solution: dict[str, Any],
    *,
    setup_cluster: Any | None = None,
) -> dict[str, Any]:
    if not produced_solution:
        return {}
    setup = getattr(authority_session, "setup", None)
    result = {name: copy.deepcopy(value) for name, value in produced_solution.items()}
    if setup is None or family == "baseline_reset":
        if family == "baseline_reset" and setup_cluster is not None:
            _apply_cluster_center(result, setup_cluster)
        return result

    if family == "incremental":
        blend = 0.7  # preserve more of the current setup region
    elif family == "compromise":
        blend = 0.45
    else:
        blend = 0.0

    step1 = result.get("step1")
    if step1 is not None:
        _blend_obj(step1, {
            "front_pushrod_offset_mm": getattr(setup, "front_pushrod_mm", None),
            "rear_pushrod_offset_mm": getattr(setup, "rear_pushrod_mm", None),
            "static_front_rh_mm": getattr(setup, "static_front_rh_mm", None),
            "static_rear_rh_mm": getattr(setup, "static_rear_rh_mm", None),
            "dynamic_front_rh_mm": getattr(setup, "front_rh_at_speed_mm", None),
            "dynamic_rear_rh_mm": getattr(setup, "rear_rh_at_speed_mm", None),
            "df_balance_pct": getattr(setup, "df_balance_pct", None),
        }, blend)
        if hasattr(step1, "static_front_rh_mm") and hasattr(step1, "static_rear_rh_mm"):
            step1.rake_static_mm = round(step1.static_rear_rh_mm - step1.static_front_rh_mm, 3)

    step2 = result.get("step2")
    if step2 is not None:
        _blend_obj(step2, {
            "front_heave_nmm": getattr(setup, "front_heave_nmm", None),
            "perch_offset_front_mm": getattr(setup, "front_heave_perch_mm", None),
            "rear_third_nmm": getattr(setup, "rear_third_nmm", None),
            "perch_offset_rear_mm": getattr(setup, "rear_third_perch_mm", None),
        }, blend)

    step3 = result.get("step3")
    if step3 is not None:
        _blend_obj(step3, {
            "front_torsion_od_mm": getattr(setup, "front_torsion_od_mm", None),
            "rear_spring_rate_nmm": getattr(setup, "rear_spring_nmm", None),
            "rear_spring_perch_mm": getattr(setup, "rear_spring_perch_mm", None),
        }, blend)

    step4 = result.get("step4")
    if step4 is not None:
        _blend_obj(step4, {
            "front_arb_blade_start": getattr(setup, "front_arb_blade", None),
            "rear_arb_blade_start": getattr(setup, "rear_arb_blade", None),
            "rarb_blade_slow_corner": getattr(setup, "rear_arb_blade", None),
            "rarb_blade_fast_corner": getattr(setup, "rear_arb_blade", None),
        }, blend, integer=True)
        if hasattr(setup, "front_arb_size") and getattr(setup, "front_arb_size", None):
            step4.front_arb_size = setup.front_arb_size if family == "incremental" else step4.front_arb_size
        if hasattr(setup, "rear_arb_size") and getattr(setup, "rear_arb_size", None):
            step4.rear_arb_size = setup.rear_arb_size if family == "incremental" else step4.rear_arb_size

    step5 = result.get("step5")
    if step5 is not None:
        _blend_obj(step5, {
            "front_camber_deg": getattr(setup, "front_camber_deg", None),
            "rear_camber_deg": getattr(setup, "rear_camber_deg", None),
            "front_toe_mm": getattr(setup, "front_toe_mm", None),
            "rear_toe_mm": getattr(setup, "rear_toe_mm", None),
        }, blend)

    step6 = result.get("step6")
    if step6 is not None:
        for corner_name, prefix in (("lf", "front"), ("rf", "front"), ("lr", "rear"), ("rr", "rear")):
            corner = getattr(step6, corner_name, None)
            if corner is None:
                continue
            _blend_obj(corner, {
                "ls_comp": getattr(setup, f"{prefix}_ls_comp", None),
                "ls_rbd": getattr(setup, f"{prefix}_ls_rbd", None),
                "hs_comp": getattr(setup, f"{prefix}_hs_comp", None),
                "hs_rbd": getattr(setup, f"{prefix}_hs_rbd", None),
                "hs_slope": getattr(setup, f"{prefix}_hs_slope", None),
            }, blend, integer=True)

    supporting = result.get("supporting")
    if supporting is not None:
        _blend_obj(supporting, {
            "brake_bias_pct": getattr(setup, "brake_bias_pct", None),
            "diff_preload_nm": getattr(setup, "diff_preload_nm", None),
            "tc_gain": getattr(setup, "tc_gain", None),
            "tc_slip": getattr(setup, "tc_slip", None),
        }, blend, integer_fields={"tc_gain", "tc_slip"})
        for field in ("brake_bias_target", "brake_bias_migration", "front_master_cyl_mm", "rear_master_cyl_mm", "pad_compound"):
            if hasattr(setup, field) and hasattr(supporting, field):
                setattr(supporting, field, getattr(setup, field))
    return result


def _apply_cluster_center(result: dict[str, Any], setup_cluster: Any) -> None:
    center = getattr(setup_cluster, "center", {}) or {}
    if not center:
        return
    step1 = result.get("step1")
    if step1 is not None:
        for field, cluster_key in (
            ("front_pushrod_offset_mm", "front_pushrod_mm"),
            ("rear_pushrod_offset_mm", "rear_pushrod_mm"),
        ):
            if hasattr(step1, field) and cluster_key in center:
                setattr(step1, field, center[cluster_key])
    step2 = result.get("step2")
    if step2 is not None:
        for field, cluster_key in (
            ("front_heave_nmm", "front_heave_nmm"),
            ("rear_third_nmm", "rear_third_nmm"),
        ):
            if hasattr(step2, field) and cluster_key in center:
                setattr(step2, field, center[cluster_key])
    step3 = result.get("step3")
    if step3 is not None:
        for field, cluster_key in (
            ("front_torsion_od_mm", "front_torsion_od_mm"),
            ("rear_spring_rate_nmm", "rear_spring_nmm"),
        ):
            if hasattr(step3, field) and cluster_key in center:
                setattr(step3, field, center[cluster_key])
    step4 = result.get("step4")
    if step4 is not None:
        for field, cluster_key in (
            ("front_arb_blade_start", "front_arb_blade"),
            ("rear_arb_blade_start", "rear_arb_blade"),
            ("rarb_blade_slow_corner", "rear_arb_blade"),
            ("rarb_blade_fast_corner", "rear_arb_blade"),
        ):
            if hasattr(step4, field) and cluster_key in center:
                setattr(step4, field, int(round(center[cluster_key])))
    step5 = result.get("step5")
    if step5 is not None:
        for field, cluster_key in (
            ("front_camber_deg", "front_camber_deg"),
            ("rear_camber_deg", "rear_camber_deg"),
            ("front_toe_mm", "front_toe_mm"),
            ("rear_toe_mm", "rear_toe_mm"),
        ):
            if hasattr(step5, field) and cluster_key in center:
                setattr(step5, field, center[cluster_key])
    supporting = result.get("supporting")
    if supporting is not None:
        for field, cluster_key in (
            ("brake_bias_pct", "brake_bias_pct"),
            ("diff_preload_nm", "diff_preload_nm"),
            ("tc_gain", "tc_gain"),
            ("tc_slip", "tc_slip"),
        ):
            if hasattr(supporting, field) and cluster_key in center:
                value = center[cluster_key]
                setattr(supporting, field, int(round(value)) if field.startswith("tc_") else value)


def _blend_obj(obj: Any, targets: dict[str, Any], blend: float, integer: bool = False, integer_fields: set[str] | None = None) -> None:
    integer_fields = integer_fields or set()
    for field, target in targets.items():
        if target is None or not hasattr(obj, field):
            continue
        current = getattr(obj, field)
        if current is None:
            continue
        try:
            current_val = float(current)
            target_val = float(target)
        except (TypeError, ValueError):
            if blend >= 0.5:
                setattr(obj, field, target)
            continue
        blended = current_val * (1.0 - blend) + target_val * blend
        if integer or field in integer_fields:
            setattr(obj, field, int(round(blended)))
        else:
            setattr(obj, field, round(blended, 4))


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
