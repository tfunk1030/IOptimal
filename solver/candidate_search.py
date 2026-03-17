from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from solver.candidate_ranker import CandidateScore, score_from_prediction
from solver.solve_chain import (
    SolveChainInputs,
    SolveChainOverrides,
    SolveChainResult,
    materialize_overrides,
)


STEP6_FIELDS = ("ls_comp", "ls_rbd", "hs_comp", "hs_rbd", "hs_slope")


@dataclass
class SetupCandidate:
    family: str
    description: str
    overrides: SolveChainOverrides = field(default_factory=SolveChainOverrides)
    result: SolveChainResult | None = None
    step1: object | None = None
    step2: object | None = None
    step3: object | None = None
    step4: object | None = None
    step5: object | None = None
    step6: object | None = None
    supporting: object | None = None
    legality: object | None = None
    predicted: object | None = None
    confidence: float = 0.0
    score: CandidateScore | None = None
    selectable: bool = True
    status: str = "ready"
    failure_reason: str = ""
    notes: list[str] = field(default_factory=list)
    selected: bool = False

    @property
    def reasons(self) -> list[str]:
        """Backward-compatible alias for older report/debug call sites."""
        return self.notes


def _safe_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _set_numeric(mapping: dict[str, Any], field: str, value: float, *, decimals: int = 4) -> None:
    if field not in mapping:
        return
    mapping[field] = round(float(value), decimals)


def _adjust_numeric(mapping: dict[str, Any], field: str, delta: float, *, decimals: int = 4) -> None:
    current = _safe_float(mapping.get(field))
    if current is None:
        return
    mapping[field] = round(current + delta, decimals)


def _scale_numeric(mapping: dict[str, Any], field: str, factor: float, *, decimals: int = 4) -> None:
    current = _safe_float(mapping.get(field))
    if current is None:
        return
    mapping[field] = round(current * factor, decimals)


def _adjust_integer(mapping: dict[str, Any], field: str, delta: int, *, lo: int | None = None, hi: int | None = None) -> None:
    try:
        value = int(round(float(mapping.get(field)))) + int(delta)
    except (TypeError, ValueError):
        return
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    mapping[field] = value


def _blend_value(current: Any, target: Any, blend: float, *, integer: bool = False) -> Any:
    try:
        current_val = float(current)
        target_val = float(target)
    except (TypeError, ValueError):
        return target if blend >= 0.5 else current
    blended = current_val * (1.0 - blend) + target_val * blend
    return int(round(blended)) if integer else round(blended, 4)


def _extract_target_maps(base_result: SolveChainResult) -> dict[str, Any]:
    return {
        "step1": {
            "front_pushrod_offset_mm": base_result.step1.front_pushrod_offset_mm,
            "rear_pushrod_offset_mm": base_result.step1.rear_pushrod_offset_mm,
            "static_front_rh_mm": base_result.step1.static_front_rh_mm,
            "static_rear_rh_mm": base_result.step1.static_rear_rh_mm,
        },
        "step2": {
            "front_heave_nmm": base_result.step2.front_heave_nmm,
            "rear_third_nmm": base_result.step2.rear_third_nmm,
            "perch_offset_front_mm": base_result.step2.perch_offset_front_mm,
            "perch_offset_rear_mm": base_result.step2.perch_offset_rear_mm,
        },
        "step3": {
            "front_torsion_od_mm": base_result.step3.front_torsion_od_mm,
            "rear_spring_rate_nmm": base_result.step3.rear_spring_rate_nmm,
            "rear_spring_perch_mm": base_result.step3.rear_spring_perch_mm,
        },
        "step4": {
            "front_arb_size": base_result.step4.front_arb_size,
            "front_arb_blade_start": base_result.step4.front_arb_blade_start,
            "rear_arb_size": base_result.step4.rear_arb_size,
            "rear_arb_blade_start": base_result.step4.rear_arb_blade_start,
            "rarb_blade_slow_corner": base_result.step4.rarb_blade_slow_corner,
            "rarb_blade_fast_corner": base_result.step4.rarb_blade_fast_corner,
            "farb_blade_locked": base_result.step4.farb_blade_locked,
        },
        "step5": {
            "front_camber_deg": base_result.step5.front_camber_deg,
            "rear_camber_deg": base_result.step5.rear_camber_deg,
            "front_toe_mm": base_result.step5.front_toe_mm,
            "rear_toe_mm": base_result.step5.rear_toe_mm,
        },
        "step6": {
            corner_name: {
                field: getattr(getattr(base_result.step6, corner_name), field)
                for field in STEP6_FIELDS
            }
            for corner_name in ("lf", "rf", "lr", "rr")
        },
        "supporting": {
            "brake_bias_pct": base_result.supporting.brake_bias_pct,
            "brake_bias_target": getattr(base_result.supporting, "brake_bias_target", 0.0),
            "brake_bias_migration": getattr(base_result.supporting, "brake_bias_migration", 0.0),
            "front_master_cyl_mm": getattr(base_result.supporting, "front_master_cyl_mm", 0.0),
            "rear_master_cyl_mm": getattr(base_result.supporting, "rear_master_cyl_mm", 0.0),
            "pad_compound": getattr(base_result.supporting, "pad_compound", ""),
            "diff_preload_nm": base_result.supporting.diff_preload_nm,
            "tc_gain": base_result.supporting.tc_gain,
            "tc_slip": base_result.supporting.tc_slip,
        },
    }


def _blend_toward_authority_setup(targets: dict[str, Any], authority_session: Any, family: str) -> None:
    setup = getattr(authority_session, "setup", None)
    if setup is None or family == "baseline_reset":
        return
    blend = 0.82 if family == "incremental" else 0.52
    targets["step1"]["front_pushrod_offset_mm"] = _blend_value(
        targets["step1"]["front_pushrod_offset_mm"], getattr(setup, "front_pushrod_mm", None), blend
    )
    targets["step1"]["rear_pushrod_offset_mm"] = _blend_value(
        targets["step1"]["rear_pushrod_offset_mm"], getattr(setup, "rear_pushrod_mm", None), blend
    )
    targets["step1"]["static_front_rh_mm"] = _blend_value(
        targets["step1"]["static_front_rh_mm"], getattr(setup, "static_front_rh_mm", None), blend
    )
    targets["step1"]["static_rear_rh_mm"] = _blend_value(
        targets["step1"]["static_rear_rh_mm"], getattr(setup, "static_rear_rh_mm", None), blend
    )

    for field, setup_field in (
        ("front_heave_nmm", "front_heave_nmm"),
        ("perch_offset_front_mm", "front_heave_perch_mm"),
        ("rear_third_nmm", "rear_third_nmm"),
        ("perch_offset_rear_mm", "rear_third_perch_mm"),
    ):
        targets["step2"][field] = _blend_value(targets["step2"][field], getattr(setup, setup_field, None), blend)

    for field, setup_field in (
        ("front_torsion_od_mm", "front_torsion_od_mm"),
        ("rear_spring_rate_nmm", "rear_spring_nmm"),
        ("rear_spring_perch_mm", "rear_spring_perch_mm"),
    ):
        targets["step3"][field] = _blend_value(targets["step3"][field], getattr(setup, setup_field, None), blend)

    for field, setup_field in (
        ("front_arb_blade_start", "front_arb_blade"),
        ("rear_arb_blade_start", "rear_arb_blade"),
        ("rarb_blade_slow_corner", "rear_arb_blade"),
        ("rarb_blade_fast_corner", "rear_arb_blade"),
    ):
        targets["step4"][field] = _blend_value(targets["step4"][field], getattr(setup, setup_field, None), blend, integer=True)
    if family == "incremental" and getattr(setup, "front_arb_size", ""):
        targets["step4"]["front_arb_size"] = setup.front_arb_size
    if family == "incremental" and getattr(setup, "rear_arb_size", ""):
        targets["step4"]["rear_arb_size"] = setup.rear_arb_size

    for field, setup_field in (
        ("front_camber_deg", "front_camber_deg"),
        ("rear_camber_deg", "rear_camber_deg"),
        ("front_toe_mm", "front_toe_mm"),
        ("rear_toe_mm", "rear_toe_mm"),
    ):
        targets["step5"][field] = _blend_value(targets["step5"][field], getattr(setup, setup_field, None), blend)

    for corner_name, prefix in (("lf", "front"), ("rf", "front"), ("lr", "rear"), ("rr", "rear")):
        for field, setup_field in (
            ("ls_comp", f"{prefix}_ls_comp"),
            ("ls_rbd", f"{prefix}_ls_rbd"),
            ("hs_comp", f"{prefix}_hs_comp"),
            ("hs_rbd", f"{prefix}_hs_rbd"),
            ("hs_slope", f"{prefix}_hs_slope"),
        ):
            targets["step6"][corner_name][field] = _blend_value(
                targets["step6"][corner_name][field],
                getattr(setup, setup_field, None),
                blend,
                integer=True,
            )

    for field, setup_field in (
        ("brake_bias_pct", "brake_bias_pct"),
        ("diff_preload_nm", "diff_preload_nm"),
        ("tc_gain", "tc_gain"),
        ("tc_slip", "tc_slip"),
    ):
        targets["supporting"][field] = _blend_value(
            targets["supporting"][field],
            getattr(setup, setup_field, None),
            blend,
            integer=field.startswith("tc_"),
        )
    for field in ("brake_bias_target", "brake_bias_migration", "front_master_cyl_mm", "rear_master_cyl_mm", "pad_compound"):
        if hasattr(setup, field):
            targets["supporting"][field] = getattr(setup, field)


def _apply_cluster_center(targets: dict[str, Any], setup_cluster: Any) -> None:
    center = getattr(setup_cluster, "center", {}) or {}
    if not center:
        return
    if "front_pushrod_mm" in center:
        _set_numeric(targets["step1"], "front_pushrod_offset_mm", center["front_pushrod_mm"])
    if "rear_pushrod_mm" in center:
        _set_numeric(targets["step1"], "rear_pushrod_offset_mm", center["rear_pushrod_mm"])
    if "front_heave_nmm" in center:
        _set_numeric(targets["step2"], "front_heave_nmm", center["front_heave_nmm"])
    if "rear_third_nmm" in center:
        _set_numeric(targets["step2"], "rear_third_nmm", center["rear_third_nmm"])
    if "front_torsion_od_mm" in center:
        _set_numeric(targets["step3"], "front_torsion_od_mm", center["front_torsion_od_mm"])
    if "rear_spring_nmm" in center:
        _set_numeric(targets["step3"], "rear_spring_rate_nmm", center["rear_spring_nmm"])
    if "front_arb_blade" in center:
        for field in ("front_arb_blade_start", "farb_blade_locked"):
            targets["step4"][field] = int(round(center["front_arb_blade"]))
    if "rear_arb_blade" in center:
        for field in ("rear_arb_blade_start", "rarb_blade_slow_corner", "rarb_blade_fast_corner"):
            targets["step4"][field] = int(round(center["rear_arb_blade"]))
    if "front_camber_deg" in center:
        _set_numeric(targets["step5"], "front_camber_deg", center["front_camber_deg"])
    if "rear_camber_deg" in center:
        _set_numeric(targets["step5"], "rear_camber_deg", center["rear_camber_deg"])
    if "front_toe_mm" in center:
        _set_numeric(targets["step5"], "front_toe_mm", center["front_toe_mm"])
    if "rear_toe_mm" in center:
        _set_numeric(targets["step5"], "rear_toe_mm", center["rear_toe_mm"])
    if "brake_bias_pct" in center:
        _set_numeric(targets["supporting"], "brake_bias_pct", center["brake_bias_pct"])
    if "diff_preload_nm" in center:
        _set_numeric(targets["supporting"], "diff_preload_nm", center["diff_preload_nm"])
    if "tc_gain" in center:
        targets["supporting"]["tc_gain"] = int(round(center["tc_gain"]))
    if "tc_slip" in center:
        targets["supporting"]["tc_slip"] = int(round(center["tc_slip"]))


def _apply_family_state_adjustments(
    targets: dict[str, Any],
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

    _adjust_numeric(targets["step1"], "front_pushrod_offset_mm", 0.8 * front_support * family_intensity, decimals=3)

    if not cluster_seeded:
        _scale_numeric(targets["step2"], "front_heave_nmm", 1.0 + 0.12 * front_support * family_intensity, decimals=3)
        _scale_numeric(targets["step2"], "rear_third_nmm", 1.0 + 0.12 * rear_support * family_intensity, decimals=3)
    _adjust_numeric(targets["step2"], "perch_offset_front_mm", 1.5 * front_support * family_intensity, decimals=3)
    _adjust_numeric(targets["step2"], "perch_offset_rear_mm", 2.0 * rear_support * family_intensity, decimals=3)

    _adjust_numeric(targets["step3"], "front_torsion_od_mm", -0.12 * entry_push * family_intensity, decimals=4)
    _adjust_numeric(
        targets["step3"],
        "rear_spring_rate_nmm",
        (8.0 * rear_support - 6.0 * exit_instability) * family_intensity,
        decimals=3,
    )

    arb_delta = int(round((entry_push + high_speed_push - exit_instability) * family_intensity))
    _adjust_integer(targets["step4"], "rear_arb_blade_start", arb_delta, lo=1, hi=6)
    _adjust_integer(targets["step4"], "rarb_blade_slow_corner", arb_delta, lo=1, hi=6)
    _adjust_integer(targets["step4"], "rarb_blade_fast_corner", arb_delta, lo=1, hi=6)

    _adjust_numeric(targets["step5"], "front_camber_deg", -0.12 * entry_push * family_intensity, decimals=3)
    _adjust_numeric(targets["step5"], "rear_camber_deg", -0.08 * exit_instability * family_intensity, decimals=3)
    _adjust_numeric(targets["step5"], "front_toe_mm", -0.05 * entry_push * family_intensity, decimals=3)

    for corner_name in ("lf", "rf"):
        _adjust_integer(targets["step6"][corner_name], "hs_comp", int(round(1.5 * front_support * family_intensity)), lo=0, hi=20)
        _adjust_integer(targets["step6"][corner_name], "ls_rbd", int(round((front_support + front_lock) * family_intensity)), lo=0, hi=20)
    for corner_name in ("lr", "rr"):
        _adjust_integer(targets["step6"][corner_name], "hs_comp", int(round(1.5 * rear_support * family_intensity)), lo=0, hi=20)
        _adjust_integer(targets["step6"][corner_name], "ls_rbd", int(round(rear_support * family_intensity)), lo=0, hi=20)

    _adjust_numeric(targets["supporting"], "brake_bias_pct", -0.3 * front_lock * family_intensity, decimals=3)
    _adjust_numeric(targets["supporting"], "diff_preload_nm", 5.0 * exit_instability * family_intensity, decimals=3)
    _adjust_integer(targets["supporting"], "tc_gain", int(round(exit_instability * family_intensity)), lo=1, hi=10)
    _adjust_integer(targets["supporting"], "tc_slip", int(round(0.8 * exit_instability * family_intensity)), lo=1, hi=10)


def _target_overrides(base_result: SolveChainResult, targets: dict[str, Any]) -> SolveChainOverrides:
    overrides = SolveChainOverrides()
    for field_name, value in targets["step1"].items():
        if getattr(base_result.step1, field_name) != value:
            overrides.step1[field_name] = value
    for field_name, value in targets["step2"].items():
        if getattr(base_result.step2, field_name) != value:
            overrides.step2[field_name] = value
    for field_name, value in targets["step3"].items():
        if getattr(base_result.step3, field_name) != value:
            overrides.step3[field_name] = value
    for field_name, value in targets["step4"].items():
        if getattr(base_result.step4, field_name) != value:
            overrides.step4[field_name] = value
    for field_name, value in targets["step5"].items():
        if getattr(base_result.step5, field_name) != value:
            overrides.step5[field_name] = value
    for corner_name, fields in targets["step6"].items():
        corner_overrides: dict[str, Any] = {}
        base_corner = getattr(base_result.step6, corner_name)
        for field_name, value in fields.items():
            if getattr(base_corner, field_name) != value:
                corner_overrides[field_name] = value
        if corner_overrides:
            overrides.step6[corner_name] = corner_overrides
    for field_name, value in targets["supporting"].items():
        if getattr(base_result.supporting, field_name) != value:
            overrides.supporting[field_name] = value
    return overrides


def _state_risk(authority_session: Any) -> float:
    diagnosis = getattr(authority_session, "diagnosis", None)
    issues = getattr(diagnosis, "state_issues", []) or []
    if not issues:
        return 0.0
    return round(sum(getattr(issue, "severity", 0.0) * getattr(issue, "confidence", 0.0) for issue in issues), 3)


def _estimate_candidate_disruption(authority_session: Any, candidate: SetupCandidate) -> float:
    setup = getattr(authority_session, "setup", None)
    if setup is None or candidate.step1 is None:
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


def generate_candidate_families(
    *,
    authority_session: Any,
    best_session: Any,
    overhaul_assessment: Any | None,
    authority_score: dict[str, object] | None = None,
    envelope_distance: float = 0.0,
    setup_distance: float = 0.0,
    base_result: SolveChainResult | None = None,
    solve_inputs: SolveChainInputs | None = None,
    setup_cluster: Any | None = None,
) -> list[SetupCandidate]:
    if base_result is None or solve_inputs is None:
        return []

    overhaul_class = getattr(overhaul_assessment, "classification", "minor_tweak")
    overhaul_conf = float(getattr(overhaul_assessment, "confidence", 0.55) or 0.55)
    authority_conf = float((authority_score or {}).get("score", 0.6) or 0.6)
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
        targets = _extract_target_maps(base_result)
        _blend_toward_authority_setup(targets, authority_session, family)
        cluster_seeded = family == "baseline_reset" and setup_cluster is not None
        if cluster_seeded:
            _apply_cluster_center(targets, setup_cluster)
        _apply_family_state_adjustments(
            targets,
            family=family,
            authority_session=authority_session,
            overhaul_class=overhaul_class,
            envelope_distance=envelope_distance,
            setup_distance=setup_distance,
            cluster_seeded=cluster_seeded,
        )
        overrides = _target_overrides(base_result, targets)
        candidate = SetupCandidate(
            family=family,
            description=family_descriptions[family],
            overrides=overrides,
            notes=[
                f"Authority session: {getattr(authority_session, 'label', 'unknown')}",
                f"Best session: {getattr(best_session, 'label', 'unknown')}",
                f"Authority score: {authority_conf:.3f}",
                f"Overhaul classification: {overhaul_class}",
            ],
        )
        try:
            result = materialize_overrides(base_result, overrides, solve_inputs)
            candidate.result = result
            candidate.step1 = result.step1
            candidate.step2 = result.step2
            candidate.step3 = result.step3
            candidate.step4 = result.step4
            candidate.step5 = result.step5
            candidate.step6 = result.step6
            candidate.supporting = result.supporting
            candidate.legality = result.legal_validation
            candidate.predicted = result.prediction
            candidate.confidence = round(
                min(
                    1.0,
                    result.prediction_confidence.overall * 0.65 + authority_conf * 0.2 + overhaul_conf * 0.15,
                ),
                3,
            )
            if not result.legal_validation.valid:
                candidate.selectable = False
                candidate.status = "illegal"
                candidate.failure_reason = "; ".join(result.legal_validation.messages[:2]) or "candidate failed legality validation"
                candidate.notes.append(candidate.failure_reason)
            else:
                candidate.status = "ready"
                candidate.notes.extend(result.notes)
        except Exception as exc:
            candidate.selectable = False
            candidate.status = "failed"
            candidate.failure_reason = str(exc)
            candidate.notes.append(f"Materialization failed: {exc}")

        disruption_cost = _estimate_candidate_disruption(authority_session, candidate)
        legal_ok = bool(getattr(candidate.legality, "valid", False))
        candidate.score = score_from_prediction(
            baseline_measured=getattr(authority_session, "measured", None),
            predicted=candidate.predicted,
            prediction_confidence=candidate.confidence,
            disruption_cost=disruption_cost,
            envelope_distance=envelope_distance,
            setup_distance=setup_distance,
            legal_ok=legal_ok,
            authority_score=authority_conf,
            state_risk=state_risk,
            notes=candidate.notes + [
                f"Disruption cost: {disruption_cost:.3f}",
                f"Envelope distance: {envelope_distance:.3f}",
                f"Setup distance: {setup_distance:.3f}",
            ],
        )
        if family_prior[family]:
            candidate.score.total = round(min(1.0, candidate.score.total + family_prior[family]), 3)
            candidate.notes.append(f"Context prior applied for {family}: +{family_prior[family]:.2f}.")
        if family_penalty[family]:
            candidate.score.total = round(max(0.0, candidate.score.total - family_penalty[family]), 3)
            candidate.notes.append(f"Context penalty applied for {family}: -{family_penalty[family]:.2f}.")
        candidates.append(candidate)

    selectable = [candidate for candidate in candidates if candidate.selectable]
    if selectable:
        winner = max(selectable, key=lambda candidate: candidate.score.total if candidate.score is not None else -1.0)
        winner.selected = True
    return candidates


def candidate_to_dict(candidate: SetupCandidate) -> dict[str, Any]:
    return {
        "family": candidate.family,
        "description": candidate.description,
        "selected": candidate.selected,
        "selectable": candidate.selectable,
        "status": candidate.status,
        "failure_reason": candidate.failure_reason or None,
        "confidence": candidate.confidence,
        "notes": list(candidate.notes),
        "overrides": candidate.overrides.to_dict(),
        "predicted": (
            candidate.predicted.to_dict()
            if getattr(candidate, "predicted", None) is not None
            else None
        ),
        "legality": (
            candidate.legality.to_dict()
            if getattr(candidate, "legality", None) is not None and hasattr(candidate.legality, "to_dict")
            else {"valid": bool(getattr(candidate.legality, "valid", False))}
            if getattr(candidate, "legality", None) is not None
            else None
        ),
        "outputs": {
            "step1": {
                "front_pushrod_offset_mm": getattr(candidate.step1, "front_pushrod_offset_mm", None),
                "rear_pushrod_offset_mm": getattr(candidate.step1, "rear_pushrod_offset_mm", None),
                "static_front_rh_mm": getattr(candidate.step1, "static_front_rh_mm", None),
                "static_rear_rh_mm": getattr(candidate.step1, "static_rear_rh_mm", None),
            },
            "step2": {
                "front_heave_nmm": getattr(candidate.step2, "front_heave_nmm", None),
                "rear_third_nmm": getattr(candidate.step2, "rear_third_nmm", None),
                "perch_offset_front_mm": getattr(candidate.step2, "perch_offset_front_mm", None),
                "perch_offset_rear_mm": getattr(candidate.step2, "perch_offset_rear_mm", None),
                "front_bottoming_margin_mm": getattr(candidate.step2, "front_bottoming_margin_mm", None),
                "travel_margin_front_mm": getattr(candidate.step2, "travel_margin_front_mm", None),
            },
            "step3": {
                "front_torsion_od_mm": getattr(candidate.step3, "front_torsion_od_mm", None),
                "rear_spring_rate_nmm": getattr(candidate.step3, "rear_spring_rate_nmm", None),
                "rear_spring_perch_mm": getattr(candidate.step3, "rear_spring_perch_mm", None),
            },
            "step4": {
                "front_arb_blade_start": getattr(candidate.step4, "front_arb_blade_start", None),
                "rear_arb_blade_start": getattr(candidate.step4, "rear_arb_blade_start", None),
                "lltd_achieved": getattr(candidate.step4, "lltd_achieved", None),
            },
            "step5": {
                "front_camber_deg": getattr(candidate.step5, "front_camber_deg", None),
                "rear_camber_deg": getattr(candidate.step5, "rear_camber_deg", None),
                "front_toe_mm": getattr(candidate.step5, "front_toe_mm", None),
                "rear_toe_mm": getattr(candidate.step5, "rear_toe_mm", None),
                "front_camber_dynamic_deg": getattr(candidate.step5, "front_camber_dynamic_deg", None),
                "rear_camber_dynamic_deg": getattr(candidate.step5, "rear_camber_dynamic_deg", None),
            },
            "step6": {
                "front_ls_comp": getattr(getattr(candidate.step6, "lf", None), "ls_comp", None),
                "front_ls_rbd": getattr(getattr(candidate.step6, "lf", None), "ls_rbd", None),
                "rear_ls_comp": getattr(getattr(candidate.step6, "lr", None), "ls_comp", None),
                "rear_ls_rbd": getattr(getattr(candidate.step6, "lr", None), "ls_rbd", None),
                "c_hs_front": getattr(candidate.step6, "c_hs_front", None),
                "c_hs_rear": getattr(candidate.step6, "c_hs_rear", None),
            },
            "supporting": {
                "brake_bias_pct": getattr(candidate.supporting, "brake_bias_pct", None),
                "diff_preload_nm": getattr(candidate.supporting, "diff_preload_nm", None),
                "tc_gain": getattr(candidate.supporting, "tc_gain", None),
                "tc_slip": getattr(candidate.supporting, "tc_slip", None),
            },
        },
        "score": (
            {
                "total": candidate.score.total,
                "safety": candidate.score.safety,
                "performance": candidate.score.performance,
                "stability": candidate.score.stability,
                "confidence": candidate.score.confidence,
                "disruption_cost": candidate.score.disruption_cost,
                "notes": candidate.score.notes,
            }
            if candidate.score is not None
            else None
        ),
    }
