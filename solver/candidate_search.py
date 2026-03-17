from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from solver.candidate_ranker import CandidateScore, score_from_prediction
from solver.predictor import PredictedTelemetry, predict_candidate_telemetry


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


def _safe_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _adjust_numeric(obj: Any, field: str, delta: float, *, decimals: int = 3) -> None:
    current = _safe_float(getattr(obj, field, None))
    if current is None:
        return
    setattr(obj, field, round(current + delta, decimals))


def _scale_numeric(obj: Any, field: str, factor: float, *, decimals: int = 3) -> None:
    current = _safe_float(getattr(obj, field, None))
    if current is None:
        return
    setattr(obj, field, round(current * factor, decimals))


def _adjust_integer(obj: Any, field: str, delta: int, *, lo: int | None = None, hi: int | None = None) -> None:
    current = getattr(obj, field, None)
    try:
        if current is None:
            return
        value = int(round(float(current))) + int(delta)
    except (TypeError, ValueError):
        return
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    setattr(obj, field, value)


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
    """Generate materially distinct candidate setup families.

    Families are generated from the existing solved setup while preserving the
    current architecture:
    - incremental: small-disruption continuation of the authority concept
    - compromise: moderate reset toward a healthier compromise family
    - baseline_reset: strong move toward the healthy/validated baseline family
    """
    produced_solution = produced_solution or {}
    if not produced_solution:
        return []

    overhaul_class = getattr(overhaul_assessment, "classification", "minor_tweak")
    overhaul_conf = float(getattr(overhaul_assessment, "confidence", 0.55) or 0.55)
    authority_conf = float((authority_score or {}).get("score", 0.6) or 0.6)
    legality_ok = legal_validation is None or getattr(legal_validation, "valid", True)
    state_risk = _state_risk(authority_session)
    family_descriptions = {
        "incremental": "Refine the authority setup with minimal disruption.",
        "compromise": "Blend authority drivability with healthier-family safety margins.",
        "baseline_reset": "Rebase the setup toward the healthy validated family.",
    }
    family_prior = {
        "incremental": 0.03 if overhaul_class == "minor_tweak" else 0.0,
        "compromise": 0.03 if overhaul_class == "moderate_rework" else 0.0,
        "baseline_reset": 0.03 if overhaul_class == "baseline_reset" else 0.0,
    }
    family_penalty = {
        "incremental": 0.0,
        "compromise": 0.0,
        "baseline_reset": 0.02 if overhaul_class == "minor_tweak" and envelope_distance < 1.5 else 0.0,
    }

    candidates: list[SetupCandidate] = []
    for family in ("incremental", "compromise", "baseline_reset"):
        family_solution = _build_family_solution(
            family,
            authority_session,
            produced_solution,
            setup_cluster=setup_cluster,
        )
        _apply_family_state_adjustments(
            family_solution,
            family=family,
            authority_session=authority_session,
            overhaul_class=overhaul_class,
            envelope_distance=envelope_distance,
            setup_distance=setup_distance,
            cluster_seeded=family == "baseline_reset" and setup_cluster is not None,
        )
        candidate = SetupCandidate(
            family=family,
            description=family_descriptions[family],
            step1=family_solution.get("step1"),
            step2=family_solution.get("step2"),
            step3=family_solution.get("step3"),
            step4=family_solution.get("step4"),
            step5=family_solution.get("step5"),
            step6=family_solution.get("step6"),
            supporting=family_solution.get("supporting"),
            reasons=[
                f"Authority session: {getattr(authority_session, 'label', 'unknown')}",
                f"Authority score: {authority_conf:.3f}",
                f"Overhaul classification: {overhaul_class}",
            ],
        )
        if hasattr(authority_session, "setup") and hasattr(authority_session, "measured"):
            candidate.predicted, prediction_conf = predict_candidate_telemetry(
                current_setup=authority_session.setup,
                baseline_measured=authority_session.measured,
                step1=candidate.step1,
                step2=candidate.step2,
                step3=candidate.step3,
                step4=candidate.step4,
                step5=candidate.step5,
                step6=candidate.step6,
                supporting=candidate.supporting,
                corrections=prediction_corrections,
            )
            candidate.confidence = round(
                min(
                    1.0,
                    prediction_conf.overall * 0.65
                    + authority_conf * 0.2
                    + overhaul_conf * 0.15,
                ),
                3,
            )
        else:
            candidate.predicted = PredictedTelemetry(
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
            candidate.confidence = round(min(1.0, authority_conf * 0.7 + overhaul_conf * 0.3), 3)

        disruption_cost = _estimate_candidate_disruption(authority_session, candidate)
        candidate.score = score_from_prediction(
            baseline_measured=getattr(authority_session, "measured", None),
            predicted=candidate.predicted,
            prediction_confidence=candidate.confidence,
            disruption_cost=disruption_cost,
            envelope_distance=envelope_distance,
            setup_distance=setup_distance,
            legal_ok=legality_ok,
            authority_score=authority_conf,
            state_risk=state_risk,
            notes=candidate.reasons + [
                f"Disruption cost: {disruption_cost:.3f}",
                f"Envelope distance: {envelope_distance:.3f}",
                f"Setup distance: {setup_distance:.3f}",
            ],
        )
        if family_prior[family]:
            candidate.score.total = round(min(1.0, candidate.score.total + family_prior[family]), 3)
            candidate.reasons.append(f"Context prior applied for {family}: +{family_prior[family]:.2f}.")
        if family_penalty[family]:
            candidate.score.total = round(max(0.0, candidate.score.total - family_penalty[family]), 3)
            candidate.reasons.append(f"Context penalty applied for {family}: -{family_penalty[family]:.2f}.")
        candidates.append(candidate)

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
        blend = 0.82  # stay close to the authority setup
    elif family == "compromise":
        blend = 0.52
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


def _state_risk(authority_session: Any) -> float:
    diagnosis = getattr(authority_session, "diagnosis", None)
    issues = getattr(diagnosis, "state_issues", []) or []
    if not issues:
        return 0.0
    return round(sum(getattr(issue, "severity", 0.0) * getattr(issue, "confidence", 0.0) for issue in issues), 3)


def _estimate_candidate_disruption(authority_session: Any, candidate: SetupCandidate) -> float:
    setup = getattr(authority_session, "setup", None)
    if setup is None:
        return 0.5

    terms: list[float] = []

    def _append(current: Any, target: Any, scale: float) -> None:
        try:
            if current is None or target is None or scale <= 0:
                return
            terms.append(min(1.0, abs(float(target) - float(current)) / scale))
        except (TypeError, ValueError):
            return

    _append(getattr(setup, "front_pushrod_mm", None), getattr(candidate.step1, "front_pushrod_offset_mm", None), 4.0)
    _append(getattr(setup, "rear_pushrod_mm", None), getattr(candidate.step1, "rear_pushrod_offset_mm", None), 4.0)
    _append(getattr(setup, "front_heave_nmm", None), getattr(candidate.step2, "front_heave_nmm", None), 25.0)
    _append(getattr(setup, "rear_third_nmm", None), getattr(candidate.step2, "rear_third_nmm", None), 150.0)
    _append(getattr(setup, "front_torsion_od_mm", None), getattr(candidate.step3, "front_torsion_od_mm", None), 1.0)
    _append(getattr(setup, "rear_spring_nmm", None), getattr(candidate.step3, "rear_spring_rate_nmm", None), 35.0)
    _append(getattr(setup, "rear_arb_blade", None), getattr(candidate.step4, "rear_arb_blade_start", None), 2.0)
    _append(getattr(setup, "front_camber_deg", None), getattr(candidate.step5, "front_camber_deg", None), 0.5)
    _append(getattr(setup, "rear_camber_deg", None), getattr(candidate.step5, "rear_camber_deg", None), 0.4)
    _append(getattr(setup, "front_hs_comp", None), getattr(getattr(candidate.step6, "lf", None), "hs_comp", None), 3.0)
    _append(getattr(setup, "rear_hs_comp", None), getattr(getattr(candidate.step6, "lr", None), "hs_comp", None), 3.0)
    _append(getattr(setup, "brake_bias_pct", None), getattr(candidate.supporting, "brake_bias_pct", None), 0.8)
    _append(getattr(setup, "diff_preload_nm", None), getattr(candidate.supporting, "diff_preload_nm", None), 20.0)
    _append(getattr(setup, "tc_gain", None), getattr(candidate.supporting, "tc_gain", None), 2.0)
    _append(getattr(setup, "tc_slip", None), getattr(candidate.supporting, "tc_slip", None), 2.0)

    if not terms:
        return 0.5
    return round(max(0.05, min(0.95, sum(terms) / len(terms))), 3)


def _apply_family_state_adjustments(
    result: dict[str, Any],
    *,
    family: str,
    authority_session: Any,
    overhaul_class: str,
    envelope_distance: float,
    setup_distance: float,
    cluster_seeded: bool = False,
) -> None:
    measured = getattr(authority_session, "measured", None)
    if measured is None:
        return

    family_intensity = {
        "incremental": 0.35,
        "compromise": 0.7,
        "baseline_reset": 1.0,
    }.get(family, 0.5)
    if overhaul_class == "baseline_reset" and family == "baseline_reset":
        family_intensity += 0.1
    if family == "baseline_reset" and (envelope_distance >= 2.0 or setup_distance >= 2.0):
        family_intensity += 0.05

    front_support = _clamp(
        max(
            (((_safe_float(getattr(measured, "front_heave_travel_used_pct", None)) or 0.0) - 80.0) / 20.0),
            (((_safe_float(getattr(measured, "pitch_range_braking_deg", None)) or 0.0) - 0.9) / 0.8),
            (_safe_float(getattr(measured, "bottoming_event_count_front_clean", None)) or 0.0) / 6.0,
        ),
        0.0,
        1.25,
    )
    rear_support = _clamp(
        max(
            (((_safe_float(getattr(measured, "rear_rh_std_mm", None)) or 0.0) - 6.0) / 4.0),
            (_safe_float(getattr(measured, "bottoming_event_count_rear_clean", None)) or 0.0) / 6.0,
        ),
        0.0,
        1.25,
    )
    entry_push = _clamp(((_safe_float(getattr(measured, "understeer_low_speed_deg", None)) or 0.0) - 0.9) / 1.2, 0.0, 1.0)
    high_speed_push = _clamp(
        (((_safe_float(getattr(measured, "understeer_high_speed_deg", None)) or 0.0) - (_safe_float(getattr(measured, "understeer_low_speed_deg", None)) or 0.0)) - 0.2) / 0.8,
        0.0,
        1.0,
    )
    exit_instability = _clamp(
        max(
            (((_safe_float(getattr(measured, "rear_power_slip_ratio_p95", None)) or 0.0) - 0.07) / 0.06),
            (((_safe_float(getattr(measured, "body_slip_p95_deg", None)) or 0.0) - 3.2) / 2.5),
        ),
        0.0,
        1.2,
    )
    front_lock = _clamp(((_safe_float(getattr(measured, "front_braking_lock_ratio_p95", None)) or 0.0) - 0.06) / 0.05, 0.0, 1.0)

    step1 = result.get("step1")
    if step1 is not None:
        _adjust_numeric(step1, "front_pushrod_offset_mm", 0.8 * front_support * family_intensity, decimals=3)
        if hasattr(step1, "rake_static_mm") and hasattr(step1, "static_front_rh_mm") and hasattr(step1, "static_rear_rh_mm"):
            try:
                step1.rake_static_mm = round(step1.static_rear_rh_mm - step1.static_front_rh_mm, 3)
            except TypeError:
                pass

    step2 = result.get("step2")
    if step2 is not None:
        if not cluster_seeded:
            _scale_numeric(step2, "front_heave_nmm", 1.0 + 0.12 * front_support * family_intensity, decimals=3)
            _scale_numeric(step2, "rear_third_nmm", 1.0 + 0.12 * rear_support * family_intensity, decimals=3)
        _adjust_numeric(step2, "perch_offset_front_mm", 1.5 * front_support * family_intensity, decimals=3)
        _adjust_numeric(step2, "perch_offset_rear_mm", 2.0 * rear_support * family_intensity, decimals=3)

    step3 = result.get("step3")
    if step3 is not None:
        _adjust_numeric(step3, "front_torsion_od_mm", -0.12 * entry_push * family_intensity, decimals=4)
        _adjust_numeric(
            step3,
            "rear_spring_rate_nmm",
            (8.0 * rear_support - 6.0 * exit_instability) * family_intensity,
            decimals=3,
        )

    step4 = result.get("step4")
    if step4 is not None:
        arb_delta = int(round((entry_push + high_speed_push - exit_instability) * family_intensity))
        _adjust_integer(step4, "rear_arb_blade_start", arb_delta, lo=1, hi=6)
        _adjust_integer(step4, "rarb_blade_slow_corner", arb_delta, lo=1, hi=6)
        _adjust_integer(step4, "rarb_blade_fast_corner", arb_delta, lo=1, hi=6)

    step5 = result.get("step5")
    if step5 is not None:
        _adjust_numeric(step5, "front_camber_deg", -0.12 * entry_push * family_intensity, decimals=3)
        _adjust_numeric(step5, "rear_camber_deg", -0.08 * exit_instability * family_intensity, decimals=3)
        _adjust_numeric(step5, "front_toe_mm", -0.05 * entry_push * family_intensity, decimals=3)

    step6 = result.get("step6")
    if step6 is not None:
        for corner_name in ("lf", "rf"):
            corner = getattr(step6, corner_name, None)
            if corner is None:
                continue
            _adjust_integer(corner, "hs_comp", int(round(1.5 * front_support * family_intensity)), lo=0, hi=20)
            _adjust_integer(corner, "ls_rbd", int(round((front_support + front_lock) * family_intensity)), lo=0, hi=20)
        for corner_name in ("lr", "rr"):
            corner = getattr(step6, corner_name, None)
            if corner is None:
                continue
            _adjust_integer(corner, "hs_comp", int(round(1.5 * rear_support * family_intensity)), lo=0, hi=20)
            _adjust_integer(corner, "ls_rbd", int(round(rear_support * family_intensity)), lo=0, hi=20)

    supporting = result.get("supporting")
    if supporting is not None:
        _adjust_numeric(supporting, "brake_bias_pct", -0.3 * front_lock * family_intensity, decimals=3)
        _adjust_numeric(supporting, "diff_preload_nm", 5.0 * exit_instability * family_intensity, decimals=3)
        _adjust_integer(supporting, "tc_gain", int(round(exit_instability * family_intensity)), lo=1, hi=10)
        _adjust_integer(supporting, "tc_slip", int(round(0.8 * exit_instability * family_intensity)), lo=1, hi=10)


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
