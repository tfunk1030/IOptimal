from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from solver.candidate_search import _extract_target_maps, _snap_targets_to_garage, _target_overrides

if TYPE_CHECKING:
    from analyzer.segment import CornerAnalysis
    from solver.solve_chain import SolveChainInputs, SolveChainResult


_DIFF_FIELDS = ("diff_preload_nm", "diff_ramp_option_idx", "diff_clutch_plates")
_GEOMETRY_FIELDS = ("front_toe_mm", "rear_toe_mm", "front_camber_deg", "rear_camber_deg")
_REAR_ARB_FIELDS = ("rear_arb_size", "rear_arb_blade")
_TARGETED_FIELDS = _DIFF_FIELDS + _GEOMETRY_FIELDS + _REAR_ARB_FIELDS


@dataclass
class RotationTelemetryState:
    entry_push: float
    exit_push: float
    instability: float
    traction_risk: float
    front_thermal: float
    rear_thermal: float
    long_exit_bias: float
    evidence: list[str] = field(default_factory=list)


@dataclass
class RotationSearchResult:
    result: "SolveChainResult"
    searched_fields: tuple[str, ...]
    base_score: float
    selected_score: float
    notes: list[str] = field(default_factory=list)
    telemetry_state: RotationTelemetryState | None = None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _snap_step(value: float, step: float, lo: float, hi: float) -> float:
    snapped = round(float(value) / step) * step
    digits = max(0, len(str(step).split(".")[1])) if "." in str(step) else 0
    return round(_clamp(snapped, lo, hi), digits)


def _is_bmw_sebring(car: Any, track: Any) -> bool:
    return (
        getattr(car, "canonical_name", "").lower() == "bmw"
        and "sebring" in getattr(track, "track_name", "").lower()
    )


def _size_index(labels: list[str], value: Any) -> int:
    if value in labels:
        return labels.index(value)
    return 0


def _adjacent_labels(labels: list[str], value: Any) -> list[str]:
    if not labels:
        return []
    idx = _size_index(labels, value)
    allowed = {idx}
    if idx > 0:
        allowed.add(idx - 1)
    if idx < len(labels) - 1:
        allowed.add(idx + 1)
    return [labels[i] for i in sorted(allowed)]


def _float_candidates(
    base_value: float,
    *,
    deltas: tuple[float, ...],
    step: float,
    bounds: tuple[float, float],
) -> list[float]:
    lo, hi = bounds
    values = {_snap_step(base_value + delta, step, lo, hi) for delta in deltas}
    values.add(_snap_step(base_value, step, lo, hi))
    return sorted(values)


def _long_exit_bias(corners: list["CornerAnalysis"] | None) -> tuple[float, list[str]]:
    if not corners:
        return 0.0, []
    long_exit_terms: list[float] = []
    details: list[str] = []
    for corner in corners:
        exit_window = max(0.0, _safe_float(getattr(corner, "exit_phase_s", 0.0)))
        throttle_delay = max(0.0, _safe_float(getattr(corner, "throttle_delay_s", 0.0)))
        exit_loss = max(0.0, _safe_float(getattr(corner, "exit_loss_s", 0.0)))
        understeer = max(0.0, _safe_float(getattr(corner, "understeer_mean_deg", 0.0)) - 0.9)
        exit_speed = _safe_float(getattr(corner, "exit_speed_kph", 0.0))
        if exit_window < 0.8 or exit_speed < 120.0:
            continue
        severity = exit_window * 0.45 + throttle_delay * 1.25 + exit_loss * 2.1 + understeer * 0.8
        if severity <= 0.0:
            continue
        long_exit_terms.append(severity)
        details.append(
            f"corner {getattr(corner, 'corner_id', '?')}: exit_loss={exit_loss:.2f}s throttle_delay={throttle_delay:.2f}s"
        )
    if not long_exit_terms:
        return 0.0, []
    return float(sum(long_exit_terms) / len(long_exit_terms)), details[:3]


def _build_rotation_state(measured: Any, corners: list["CornerAnalysis"] | None) -> RotationTelemetryState:
    understeer_low = _safe_float(getattr(measured, "understeer_low_speed_deg", 0.0))
    understeer_high = _safe_float(
        getattr(measured, "understeer_high_speed_deg", getattr(measured, "understeer_mean_deg", 0.0))
    )
    yaw_corr = _safe_float(getattr(measured, "yaw_rate_correlation", 1.0), 1.0)
    rear_power_slip = _safe_float(
        getattr(measured, "rear_power_slip_ratio_p95", getattr(measured, "rear_slip_ratio_p95", 0.0))
    )
    body_slip = _safe_float(getattr(measured, "body_slip_p95_deg", 0.0))
    front_temp = _safe_float(getattr(measured, "front_carcass_mean_c", 0.0))
    rear_temp = _safe_float(getattr(measured, "rear_carcass_mean_c", 0.0))
    front_pressure = _safe_float(getattr(measured, "front_pressure_mean_kpa", 0.0))
    rear_pressure = _safe_float(getattr(measured, "rear_pressure_mean_kpa", 0.0))
    long_exit_bias, corner_evidence = _long_exit_bias(corners)

    entry_push = max(0.0, understeer_low - 0.75) * 1.05 + max(0.0, 0.92 - yaw_corr) * 0.55
    exit_push = (
        max(0.0, understeer_low - 0.85) * 1.10
        + max(0.0, understeer_high - max(understeer_low, 0.8)) * 0.65
        + max(0.0, 0.94 - yaw_corr) * 0.80
        + long_exit_bias * 0.70
    )
    instability = (
        max(0.0, body_slip - 3.4) * 0.55
        + max(0.0, rear_power_slip - 0.082) * 16.0
        + max(0.0, 0.90 - yaw_corr) * 0.85
    )
    traction_risk = max(0.0, rear_power_slip - 0.075) * 20.0 + max(0.0, body_slip - 3.1) * 0.35
    front_thermal = max(0.0, front_temp - 95.0) / 4.5 + max(0.0, front_pressure - 169.0) / 4.0
    rear_thermal = max(0.0, rear_temp - 95.0) / 4.5 + max(0.0, rear_pressure - 169.0) / 4.0
    evidence = [
        f"entry_push={entry_push:.2f}",
        f"exit_push={exit_push:.2f}",
        f"instability={instability:.2f}",
        f"traction_risk={traction_risk:.2f}",
        f"long_exit_bias={long_exit_bias:.2f}",
    ]
    evidence.extend(corner_evidence)
    return RotationTelemetryState(
        entry_push=entry_push,
        exit_push=exit_push,
        instability=instability,
        traction_risk=traction_risk,
        front_thermal=front_thermal,
        rear_thermal=rear_thermal,
        long_exit_bias=long_exit_bias,
        evidence=evidence,
    )


def _candidate_search_space(base_result: "SolveChainResult", car: Any) -> dict[str, list[Any]]:
    gr = getattr(car, "garage_ranges", None)
    geo = getattr(car, "geometry", None)
    rear_labels = list(getattr(getattr(car, "arb", None), "rear_size_labels", []) or [])
    base_support = base_result.supporting
    base_step4 = base_result.step4
    base_step5 = base_result.step5
    preload_step = getattr(gr, "diff_preload_step_nm", 5.0) or 5.0
    preload_bounds = getattr(gr, "diff_preload_nm", (0.0, 150.0))
    front_toe_bounds = getattr(geo, "front_toe_range_mm", getattr(gr, "toe_front_mm", (-3.0, 3.0)))
    rear_toe_bounds = getattr(geo, "rear_toe_range_mm", getattr(gr, "toe_rear_mm", (-2.0, 3.0)))
    front_camber_bounds = getattr(geo, "front_camber_range_deg", getattr(gr, "camber_front_deg", (-2.9, 0.0)))
    rear_camber_bounds = getattr(geo, "rear_camber_range_deg", getattr(gr, "camber_rear_deg", (-1.9, 0.0)))
    front_toe_step = getattr(geo, "front_toe_step_mm", 0.1) or 0.1
    rear_toe_step = getattr(geo, "rear_toe_step_mm", 0.1) or 0.1
    front_camber_step = getattr(geo, "front_camber_step_deg", 0.1) or 0.1
    rear_camber_step = getattr(geo, "rear_camber_step_deg", 0.1) or 0.1
    arb_range = getattr(gr, "arb_blade", (1, 5))

    return {
        "diff_preload_nm": _float_candidates(
            _safe_float(getattr(base_support, "diff_preload_nm", 20.0)),
            deltas=(-15.0, -10.0, -5.0, 0.0, 5.0, 10.0, 15.0),
            step=preload_step,
            bounds=preload_bounds,
        ),
        "diff_ramp_option_idx": list(range(len(getattr(gr, "diff_coast_drive_ramp_options", [(40, 65), (45, 70), (50, 75)])))),
        "diff_clutch_plates": list(getattr(gr, "diff_clutch_plates_options", [2, 4, 6])),
        "front_toe_mm": _float_candidates(
            _safe_float(getattr(base_step5, "front_toe_mm", 0.0)),
            deltas=(-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3),
            step=front_toe_step,
            bounds=front_toe_bounds,
        ),
        "rear_toe_mm": _float_candidates(
            _safe_float(getattr(base_step5, "rear_toe_mm", 0.0)),
            deltas=(-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3),
            step=rear_toe_step,
            bounds=rear_toe_bounds,
        ),
        "front_camber_deg": _float_candidates(
            _safe_float(getattr(base_step5, "front_camber_deg", -2.1)),
            deltas=(-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3),
            step=front_camber_step,
            bounds=front_camber_bounds,
        ),
        "rear_camber_deg": _float_candidates(
            _safe_float(getattr(base_step5, "rear_camber_deg", -1.8)),
            deltas=(-0.2, -0.1, 0.0, 0.1, 0.2),
            step=rear_camber_step,
            bounds=rear_camber_bounds,
        ),
        "rear_arb_size": _adjacent_labels(rear_labels, getattr(base_step4, "rear_arb_size", None)),
        "rear_arb_blade_start": [
            blade for blade in sorted({
                int(_clamp(_safe_int(getattr(base_step4, "rear_arb_blade_start", 1)) + delta, arb_range[0], arb_range[1]))
                for delta in (-2, -1, 0, 1, 2)
            })
        ],
    }


def _geometry_and_arb_profiles(space: dict[str, list[Any]], base_result: "SolveChainResult") -> list[dict[str, Any]]:
    base_step4 = base_result.step4
    base_step5 = base_result.step5
    base_front_toe = _safe_float(getattr(base_step5, "front_toe_mm", 0.0))
    base_rear_toe = _safe_float(getattr(base_step5, "rear_toe_mm", 0.0))
    base_front_camber = _safe_float(getattr(base_step5, "front_camber_deg", 0.0))
    base_rear_camber = _safe_float(getattr(base_step5, "rear_camber_deg", 0.0))
    base_blade = _safe_int(getattr(base_step4, "rear_arb_blade_start", 1), 1)
    base_size = getattr(base_step4, "rear_arb_size", None)

    def _nearest(values: list[Any], target: Any) -> Any:
        if target in values:
            return target
        if not values:
            return target
        if isinstance(target, (int, float)):
            return min(values, key=lambda value: abs(float(value) - float(target)))
        return values[0]

    profiles = [
        {"name": "hold", "front_toe_mm": _nearest(space["front_toe_mm"], base_front_toe), "rear_toe_mm": _nearest(space["rear_toe_mm"], base_rear_toe), "front_camber_deg": _nearest(space["front_camber_deg"], base_front_camber), "rear_camber_deg": _nearest(space["rear_camber_deg"], base_rear_camber), "rear_arb_size": _nearest(space["rear_arb_size"], base_size), "rear_arb_blade_start": _nearest(space["rear_arb_blade_start"], base_blade)},
        {"name": "free_exit_rotation", "front_toe_mm": _nearest(space["front_toe_mm"], base_front_toe - 0.2), "rear_toe_mm": _nearest(space["rear_toe_mm"], base_rear_toe - 0.1), "front_camber_deg": _nearest(space["front_camber_deg"], base_front_camber - 0.2), "rear_camber_deg": _nearest(space["rear_camber_deg"], base_rear_camber + 0.1), "rear_arb_size": _nearest(space["rear_arb_size"], base_size), "rear_arb_blade_start": _nearest(space["rear_arb_blade_start"], base_blade + 1)},
        {"name": "entry_and_mid_support", "front_toe_mm": _nearest(space["front_toe_mm"], base_front_toe - 0.1), "rear_toe_mm": _nearest(space["rear_toe_mm"], base_rear_toe), "front_camber_deg": _nearest(space["front_camber_deg"], base_front_camber - 0.3), "rear_camber_deg": _nearest(space["rear_camber_deg"], base_rear_camber), "rear_arb_size": _nearest(space["rear_arb_size"], base_size), "rear_arb_blade_start": _nearest(space["rear_arb_blade_start"], base_blade + 1)},
        {"name": "stability_bias", "front_toe_mm": _nearest(space["front_toe_mm"], base_front_toe + 0.1), "rear_toe_mm": _nearest(space["rear_toe_mm"], base_rear_toe + 0.2), "front_camber_deg": _nearest(space["front_camber_deg"], base_front_camber + 0.1), "rear_camber_deg": _nearest(space["rear_camber_deg"], base_rear_camber - 0.1), "rear_arb_size": _nearest(space["rear_arb_size"], base_size), "rear_arb_blade_start": _nearest(space["rear_arb_blade_start"], base_blade - 1)},
        {"name": "traction_safe_rotation", "front_toe_mm": _nearest(space["front_toe_mm"], base_front_toe - 0.1), "rear_toe_mm": _nearest(space["rear_toe_mm"], base_rear_toe + 0.1), "front_camber_deg": _nearest(space["front_camber_deg"], base_front_camber - 0.1), "rear_camber_deg": _nearest(space["rear_camber_deg"], base_rear_camber + 0.1), "rear_arb_size": _nearest(space["rear_arb_size"], base_size), "rear_arb_blade_start": _nearest(space["rear_arb_blade_start"], base_blade)},
        {"name": "rear_platform_rotation", "front_toe_mm": _nearest(space["front_toe_mm"], base_front_toe), "rear_toe_mm": _nearest(space["rear_toe_mm"], base_rear_toe - 0.2), "front_camber_deg": _nearest(space["front_camber_deg"], base_front_camber - 0.1), "rear_camber_deg": _nearest(space["rear_camber_deg"], base_rear_camber + 0.1), "rear_arb_size": _nearest(space["rear_arb_size"], base_size), "rear_arb_blade_start": _nearest(space["rear_arb_blade_start"], base_blade + 1)},
    ]
    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for profile in profiles:
        key = (profile["front_toe_mm"], profile["rear_toe_mm"], profile["front_camber_deg"], profile["rear_camber_deg"], profile["rear_arb_size"], profile["rear_arb_blade_start"])
        deduped[key] = profile
    return list(deduped.values())


def _rear_arb_live_targets(base_result: "SolveChainResult", blade_start: int) -> tuple[int, int]:
    slow = int(max(1, blade_start))
    base_fast = _safe_int(getattr(base_result.step4, "rarb_blade_fast_corner", blade_start + 2), blade_start + 2)
    fast = int(_clamp(max(base_fast, blade_start + 2), 1, 5))
    return slow, fast


def _score_controls(
    *,
    targets: dict[str, Any],
    base_result: "SolveChainResult",
    telemetry: RotationTelemetryState,
    car: Any,
) -> tuple[float, list[str]]:
    base_step4 = base_result.step4
    base_step5 = base_result.step5
    base_support = base_result.supporting
    rear_labels = list(getattr(getattr(car, "arb", None), "rear_size_labels", []) or [])

    preload_delta = (_safe_float(getattr(base_support, "diff_preload_nm", 0.0)) - _safe_float(targets["diff_preload_nm"])) / 5.0
    ramp_delta = _safe_int(targets["diff_ramp_option_idx"]) - _safe_int(getattr(base_support, "diff_ramp_option_idx", 1), 1)
    plate_delta = (_safe_int(getattr(base_support, "diff_clutch_plates", 6), 6) - _safe_int(targets["diff_clutch_plates"], 6)) / 2.0
    front_toe_out_delta = (_safe_float(getattr(base_step5, "front_toe_mm", 0.0)) - _safe_float(targets["front_toe_mm"])) / 0.1
    rear_toe_out_delta = (_safe_float(getattr(base_step5, "rear_toe_mm", 0.0)) - _safe_float(targets["rear_toe_mm"])) / 0.1
    front_camber_delta = (_safe_float(getattr(base_step5, "front_camber_deg", 0.0)) - _safe_float(targets["front_camber_deg"])) / 0.1
    rear_camber_relief = (_safe_float(targets["rear_camber_deg"]) - _safe_float(getattr(base_step5, "rear_camber_deg", 0.0))) / 0.1
    rear_arb_stiffness = (
        (_safe_int(targets["rear_arb_blade_start"], 1) - _safe_int(getattr(base_step4, "rear_arb_blade_start", 1), 1))
        + (_size_index(rear_labels, targets["rear_arb_size"]) - _size_index(rear_labels, getattr(base_step4, "rear_arb_size", None))) * 1.25
    )
    rotation_priority = max(0.0, telemetry.exit_push - telemetry.instability - telemetry.traction_risk * 0.5)
    preload_exit_gain = 0.14 + min(0.18, rotation_priority * 0.12)
    preload_instability_cost = max(0.06, 0.15 - min(0.08, rotation_priority * 0.05))

    predicted_exit = telemetry.exit_push - 0.34 * ramp_delta - 0.19 * plate_delta - preload_exit_gain * preload_delta
    predicted_exit -= 0.16 * front_toe_out_delta + 0.14 * front_camber_delta + 0.12 * rear_toe_out_delta + 0.24 * rear_arb_stiffness
    predicted_exit += max(0.0, -rear_camber_relief) * 0.05

    predicted_entry = telemetry.entry_push - 0.18 * ramp_delta - 0.13 * front_toe_out_delta
    predicted_entry -= 0.10 * front_camber_delta + 0.08 * rear_toe_out_delta + 0.10 * rear_arb_stiffness

    predicted_instability = telemetry.instability + 0.22 * ramp_delta + 0.18 * plate_delta + preload_instability_cost * preload_delta
    predicted_instability += 0.17 * rear_arb_stiffness + 0.08 * front_toe_out_delta + 0.12 * rear_toe_out_delta

    predicted_traction = telemetry.traction_risk + 0.20 * ramp_delta + 0.15 * plate_delta + 0.13 * preload_delta
    predicted_traction += 0.16 * rear_arb_stiffness + max(0.0, -rear_camber_relief) * 0.09

    predicted_front_thermal = telemetry.front_thermal + 0.08 * max(0.0, front_toe_out_delta) + 0.12 * max(0.0, front_camber_delta)
    predicted_front_thermal -= 0.05 * max(0.0, rear_arb_stiffness)

    predicted_rear_thermal = telemetry.rear_thermal + 0.06 * max(0.0, rear_arb_stiffness) + 0.08 * max(0.0, -rear_camber_relief)

    predicted_exit = max(0.0, predicted_exit)
    predicted_entry = max(0.0, predicted_entry)
    predicted_instability = max(0.0, predicted_instability)
    predicted_traction = max(0.0, predicted_traction)
    predicted_front_thermal = max(0.0, predicted_front_thermal)
    predicted_rear_thermal = max(0.0, predicted_rear_thermal)

    exit_weight = 3.0 + min(1.4, telemetry.long_exit_bias * 0.45)
    total = (
        exit_weight * predicted_exit
        + 1.35 * predicted_entry
        + 2.45 * predicted_instability
        + 2.15 * predicted_traction
        + 1.10 * predicted_front_thermal
        + 0.75 * predicted_rear_thermal
    )
    change_cost = (
        abs(preload_delta) * 0.03 + abs(ramp_delta) * 0.09 + abs(plate_delta) * 0.10
        + abs(front_toe_out_delta) * 0.03 + abs(rear_toe_out_delta) * 0.03
        + abs(front_camber_delta) * 0.025 + abs(rear_camber_relief) * 0.02
        + abs(rear_arb_stiffness) * 0.05
    )
    total += change_cost
    evidence = [
        f"pred_exit={predicted_exit:.2f}",
        f"pred_entry={predicted_entry:.2f}",
        f"pred_instability={predicted_instability:.2f}",
        f"pred_traction={predicted_traction:.2f}",
    ]
    return total, evidence


def _build_candidate_targets(
    base_targets: dict[str, Any],
    *,
    diff_preload_nm: float,
    diff_ramp_option_idx: int,
    diff_clutch_plates: int,
    front_toe_mm: float,
    rear_toe_mm: float,
    front_camber_deg: float,
    rear_camber_deg: float,
    rear_arb_size: str,
    rear_arb_blade_start: int,
    base_result: "SolveChainResult",
    car: Any,
) -> dict[str, Any]:
    targets = copy.deepcopy(base_targets)
    targets["supporting"]["diff_preload_nm"] = diff_preload_nm
    targets["supporting"]["diff_ramp_option_idx"] = diff_ramp_option_idx
    targets["supporting"]["diff_clutch_plates"] = diff_clutch_plates
    targets["step5"]["front_toe_mm"] = front_toe_mm
    targets["step5"]["rear_toe_mm"] = rear_toe_mm
    targets["step5"]["front_camber_deg"] = front_camber_deg
    targets["step5"]["rear_camber_deg"] = rear_camber_deg
    slow, fast = _rear_arb_live_targets(base_result, rear_arb_blade_start)
    targets["step4"]["rear_arb_size"] = rear_arb_size
    targets["step4"]["rear_arb_blade_start"] = rear_arb_blade_start
    targets["step4"]["rarb_blade_slow_corner"] = slow
    targets["step4"]["rarb_blade_fast_corner"] = fast
    _snap_targets_to_garage(targets, car)
    return targets


def _candidate_summary(targets: dict[str, Any], car: Any) -> str:
    idx = _safe_int(targets["supporting"]["diff_ramp_option_idx"], 1)
    options = list(getattr(getattr(car, "garage_ranges", None), "diff_coast_drive_ramp_options", [(40, 65), (45, 70), (50, 75)]))
    idx = max(0, min(len(options) - 1, idx)) if options else 0
    coast, drive = options[idx] if options else (45, 70)
    return (
        f"diff {targets['supporting']['diff_preload_nm']:.0f}Nm / {coast}/{drive} / {targets['supporting']['diff_clutch_plates']} plates; "
        f"toe F{targets['step5']['front_toe_mm']:+.1f} R{targets['step5']['rear_toe_mm']:+.1f}; "
        f"camber F{targets['step5']['front_camber_deg']:+.1f} R{targets['step5']['rear_camber_deg']:+.1f}; "
        f"RARB {targets['step4']['rear_arb_size']}/{int(targets['step4']['rear_arb_blade_start'])}"
    )


def _search_metadata(
    *,
    base_result: "SolveChainResult",
    selected_result: "SolveChainResult",
    telemetry: RotationTelemetryState,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    status: dict[str, str] = {}
    evidence: dict[str, list[str]] = {}

    def _set(field_name: str, base_value: Any, selected_value: Any, rationale: str) -> None:
        status[field_name] = "searched_and_changed" if base_value != selected_value else "searched_and_kept"
        evidence[field_name] = list(telemetry.evidence) + [rationale]

    _set("diff_preload_nm", getattr(base_result.supporting, "diff_preload_nm", None), getattr(selected_result.supporting, "diff_preload_nm", None), "Preload was searched against exit-push, instability, and rear power-slip evidence.")
    _set("diff_ramp_option_idx", getattr(base_result.supporting, "diff_ramp_option_idx", None), getattr(selected_result.supporting, "diff_ramp_option_idx", None), "The legal coupled ramp option was searched with extra weight on Sebring long-exit understeer.")
    _set("diff_clutch_plates", getattr(base_result.supporting, "diff_clutch_plates", None), getattr(selected_result.supporting, "diff_clutch_plates", None), "Clutch plate count was searched both toward less lock and more lock based on exit and traction evidence.")
    _set("front_toe_mm", getattr(base_result.step5, "front_toe_mm", None), getattr(selected_result.step5, "front_toe_mm", None), "Front toe was searched from low-speed push, yaw correlation, and front thermal support.")
    _set("rear_toe_mm", getattr(base_result.step5, "rear_toe_mm", None), getattr(selected_result.step5, "rear_toe_mm", None), "Rear toe was searched for the traction and rotation compromise instead of staying stability-only.")
    _set("front_camber_deg", getattr(base_result.step5, "front_camber_deg", None), getattr(selected_result.step5, "front_camber_deg", None), "Front camber was searched using loaded-front support, exit push, and tyre thermal evidence.")
    _set("rear_camber_deg", getattr(base_result.step5, "rear_camber_deg", None), getattr(selected_result.step5, "rear_camber_deg", None), "Rear camber was searched from traction demand, body-slip stability, and rear tyre support.")
    _set("rear_arb_size", getattr(base_result.step4, "rear_arb_size", None), getattr(selected_result.step4, "rear_arb_size", None), "Rear ARB size was searched as part of the BMW/Sebring rotation-control stage.")
    _set("rear_arb_blade", getattr(base_result.step4, "rear_arb_blade_start", None), getattr(selected_result.step4, "rear_arb_blade_start", None), "Rear ARB blade was searched with long-exit corner weighting instead of staying at the baseline blade.")
    return status, evidence


def _apply_metadata(
    selected_result: "SolveChainResult",
    *,
    base_result: "SolveChainResult",
    telemetry: RotationTelemetryState,
) -> None:
    status, evidence = _search_metadata(base_result=base_result, selected_result=selected_result, telemetry=telemetry)
    selected_result.step4.parameter_search_status = {key: value for key, value in status.items() if key in _REAR_ARB_FIELDS}
    selected_result.step4.parameter_search_evidence = {key: value for key, value in evidence.items() if key in _REAR_ARB_FIELDS}
    selected_result.step5.parameter_search_status = {key: value for key, value in status.items() if key in _GEOMETRY_FIELDS}
    selected_result.step5.parameter_search_evidence = {key: value for key, value in evidence.items() if key in _GEOMETRY_FIELDS}
    selected_result.supporting.parameter_search_status = {key: value for key, value in status.items() if key in _DIFF_FIELDS}
    selected_result.supporting.parameter_search_evidence = {key: value for key, value in evidence.items() if key in _DIFF_FIELDS}


def _refresh_decision_trace(result: "SolveChainResult", inputs: "SolveChainInputs") -> None:
    from solver.decision_trace import build_parameter_decisions

    result.decision_trace = build_parameter_decisions(
        car_name=inputs.car.canonical_name,
        current_setup=inputs.current_setup,
        measured=inputs.measured,
        step1=result.step1,
        step2=result.step2,
        step3=result.step3,
        step4=result.step4,
        step5=result.step5,
        step6=result.step6,
        supporting=result.supporting,
        legality=result.legal_validation,
        fallback_reasons=list(getattr(inputs.measured, "fallback_reasons", []) or []),
    )


def _targeted_status_maps(result: "SolveChainResult") -> tuple[dict[str, str], dict[str, list[str]]]:
    status: dict[str, str] = {}
    evidence: dict[str, list[str]] = {}
    for source in (getattr(result, "step4", None), getattr(result, "step5", None), getattr(result, "supporting", None)):
        if source is None:
            continue
        status.update(getattr(source, "parameter_search_status", {}) or {})
        for field_name, lines in (getattr(source, "parameter_search_evidence", {}) or {}).items():
            evidence[field_name] = list(lines or [])
    return status, evidence


def _has_targeted_search_metadata(result: "SolveChainResult") -> bool:
    status, evidence = _targeted_status_maps(result)
    return any(field in status or field in evidence for field in _TARGETED_FIELDS)


def _copy_targeted_metadata(*, source: "SolveChainResult", destination: "SolveChainResult") -> None:
    source_status, source_evidence = _targeted_status_maps(source)
    for target_obj, fields in (
        (destination.step4, _REAR_ARB_FIELDS),
        (destination.step5, _GEOMETRY_FIELDS),
        (destination.supporting, _DIFF_FIELDS),
    ):
        existing_status = dict(getattr(target_obj, "parameter_search_status", {}) or {})
        existing_evidence = dict(getattr(target_obj, "parameter_search_evidence", {}) or {})
        for field_name in fields:
            if field_name in source_status:
                existing_status[field_name] = source_status[field_name]
            if field_name in source_evidence:
                existing_evidence[field_name] = list(source_evidence[field_name])
        target_obj.parameter_search_status = existing_status
        target_obj.parameter_search_evidence = existing_evidence


def _rotation_preservation_overrides(
    *,
    rotation_result: "SolveChainResult",
    candidate_result: "SolveChainResult",
) -> "SolveChainOverrides":
    from solver.solve_chain import SolveChainOverrides

    overrides = SolveChainOverrides()
    for field_name in _DIFF_FIELDS:
        rotation_value = getattr(rotation_result.supporting, field_name, None)
        if getattr(candidate_result.supporting, field_name, None) != rotation_value:
            overrides.supporting[field_name] = rotation_value
    for field_name in _GEOMETRY_FIELDS:
        rotation_value = getattr(rotation_result.step5, field_name, None)
        if getattr(candidate_result.step5, field_name, None) != rotation_value:
            overrides.step5[field_name] = rotation_value

    rear_arb_fields = (
        "rear_arb_size",
        "rear_arb_blade_start",
        "rarb_blade_slow_corner",
        "rarb_blade_fast_corner",
    )
    if any(
        getattr(candidate_result.step4, field_name, None) != getattr(rotation_result.step4, field_name, None)
        for field_name in rear_arb_fields
    ):
        for field_name in rear_arb_fields:
            overrides.step4[field_name] = getattr(rotation_result.step4, field_name, None)
    return overrides


def preserve_candidate_rotation_controls(
    *,
    rotation_result: "SolveChainResult",
    candidate_result: "SolveChainResult" | None,
    inputs: "SolveChainInputs",
) -> tuple["SolveChainResult" | None, bool]:
    if candidate_result is None:
        return None, False
    if not _is_bmw_sebring(inputs.car, inputs.track):
        return candidate_result, False
    if not _has_targeted_search_metadata(rotation_result):
        return candidate_result, False

    from solver.solve_chain import materialize_overrides

    overrides = _rotation_preservation_overrides(
        rotation_result=rotation_result,
        candidate_result=candidate_result,
    )
    preserved_controls = overrides.earliest_step() is not None
    if preserved_controls:
        preserved_result = materialize_overrides(candidate_result, overrides, inputs)
    else:
        preserved_result = copy.deepcopy(candidate_result)
    _copy_targeted_metadata(source=rotation_result, destination=preserved_result)
    _refresh_decision_trace(preserved_result, inputs)
    preserved_result.notes = list(
        dict.fromkeys(
            list(getattr(candidate_result, "notes", []) or [])
            + list(getattr(preserved_result, "notes", []) or [])
            + ["Preserved BMW/Sebring rotation-control search metadata on the final candidate result."]
        )
    )
    preserved_result.candidate_vetoes = list(getattr(candidate_result, "candidate_vetoes", []) or [])
    preserved_result.optimizer_used = getattr(candidate_result, "optimizer_used", False)
    return preserved_result, preserved_controls


def search_rotation_controls(
    *,
    base_result: "SolveChainResult",
    inputs: "SolveChainInputs",
) -> RotationSearchResult | None:
    if not _is_bmw_sebring(inputs.car, inputs.track):
        return None

    from solver.solve_chain import materialize_overrides

    telemetry = _build_rotation_state(inputs.measured, getattr(inputs, "corners", None))
    base_targets = _extract_target_maps(base_result)
    base_flat = {
        "diff_preload_nm": getattr(base_result.supporting, "diff_preload_nm", None),
        "diff_ramp_option_idx": getattr(base_result.supporting, "diff_ramp_option_idx", None),
        "diff_clutch_plates": getattr(base_result.supporting, "diff_clutch_plates", None),
        "front_toe_mm": getattr(base_result.step5, "front_toe_mm", None),
        "rear_toe_mm": getattr(base_result.step5, "rear_toe_mm", None),
        "front_camber_deg": getattr(base_result.step5, "front_camber_deg", None),
        "rear_camber_deg": getattr(base_result.step5, "rear_camber_deg", None),
        "rear_arb_size": getattr(base_result.step4, "rear_arb_size", None),
        "rear_arb_blade_start": getattr(base_result.step4, "rear_arb_blade_start", None),
    }
    base_score, base_evidence = _score_controls(targets=base_flat, base_result=base_result, telemetry=telemetry, car=inputs.car)

    space = _candidate_search_space(base_result, inputs.car)
    profiles = _geometry_and_arb_profiles(space, base_result)
    abstract_candidates: list[tuple[float, dict[str, Any], list[str], str]] = []
    for preload in space["diff_preload_nm"]:
        for ramp_idx in space["diff_ramp_option_idx"]:
            for plates in space["diff_clutch_plates"]:
                for profile in profiles:
                    candidate_targets = _build_candidate_targets(base_targets, diff_preload_nm=preload, diff_ramp_option_idx=ramp_idx, diff_clutch_plates=plates, front_toe_mm=profile["front_toe_mm"], rear_toe_mm=profile["rear_toe_mm"], front_camber_deg=profile["front_camber_deg"], rear_camber_deg=profile["rear_camber_deg"], rear_arb_size=profile["rear_arb_size"], rear_arb_blade_start=profile["rear_arb_blade_start"], base_result=base_result, car=inputs.car)
                    flat_targets = {
                        "diff_preload_nm": candidate_targets["supporting"]["diff_preload_nm"],
                        "diff_ramp_option_idx": candidate_targets["supporting"]["diff_ramp_option_idx"],
                        "diff_clutch_plates": candidate_targets["supporting"]["diff_clutch_plates"],
                        "front_toe_mm": candidate_targets["step5"]["front_toe_mm"],
                        "rear_toe_mm": candidate_targets["step5"]["rear_toe_mm"],
                        "front_camber_deg": candidate_targets["step5"]["front_camber_deg"],
                        "rear_camber_deg": candidate_targets["step5"]["rear_camber_deg"],
                        "rear_arb_size": candidate_targets["step4"]["rear_arb_size"],
                        "rear_arb_blade_start": candidate_targets["step4"]["rear_arb_blade_start"],
                    }
                    score, evidence = _score_controls(targets=flat_targets, base_result=base_result, telemetry=telemetry, car=inputs.car)
                    abstract_candidates.append((score, candidate_targets, evidence, profile["name"]))
    abstract_candidates.sort(key=lambda item: item[0])

    best_result = copy.deepcopy(base_result)
    best_score = base_score
    notes = [
        f"BMW/Sebring rotation search scored {len(abstract_candidates)} abstract candidates.",
        f"Telemetry rotation state: {'; '.join(telemetry.evidence[:5])}.",
        f"Base rotation score {base_score:.3f}: {'; '.join(base_evidence[:4])}.",
    ]
    materialized = 0
    for _abstract_score, candidate_targets, _evidence, profile_name in abstract_candidates[:12]:
        overrides = _target_overrides(base_result, candidate_targets)
        candidate_result = materialize_overrides(base_result, overrides, inputs)
        materialized += 1
        selected_targets = {
            "diff_preload_nm": getattr(candidate_result.supporting, "diff_preload_nm", None),
            "diff_ramp_option_idx": getattr(candidate_result.supporting, "diff_ramp_option_idx", None),
            "diff_clutch_plates": getattr(candidate_result.supporting, "diff_clutch_plates", None),
            "front_toe_mm": getattr(candidate_result.step5, "front_toe_mm", None),
            "rear_toe_mm": getattr(candidate_result.step5, "rear_toe_mm", None),
            "front_camber_deg": getattr(candidate_result.step5, "front_camber_deg", None),
            "rear_camber_deg": getattr(candidate_result.step5, "rear_camber_deg", None),
            "rear_arb_size": getattr(candidate_result.step4, "rear_arb_size", None),
            "rear_arb_blade_start": getattr(candidate_result.step4, "rear_arb_blade_start", None),
        }
        candidate_score, candidate_evidence = _score_controls(targets=selected_targets, base_result=base_result, telemetry=telemetry, car=inputs.car)
        if candidate_score + 1e-9 < best_score - 0.02:
            best_score = candidate_score
            best_result = candidate_result
            notes.append(f"Selected rotation profile '{profile_name}' at {candidate_score:.3f}: {_candidate_summary(candidate_targets, inputs.car)} | {'; '.join(candidate_evidence[:4])}")

    if materialized == 0:
        notes.append("Rotation search found no materializable BMW/Sebring candidates; keeping the base solve.")
    elif best_score >= base_score - 0.02:
        notes.append("Rotation search searched the BMW/Sebring rotation-control set and kept the base values.")
    else:
        notes.append(f"Materialized {materialized} BMW/Sebring rotation candidates and selected the lowest-penalty combination.")

    _apply_metadata(best_result, base_result=base_result, telemetry=telemetry)
    _refresh_decision_trace(best_result, inputs)
    return RotationSearchResult(
        result=best_result,
        searched_fields=_TARGETED_FIELDS,
        base_score=round(base_score, 4),
        selected_score=round(best_score, 4),
        notes=notes,
        telemetry_state=telemetry,
    )
