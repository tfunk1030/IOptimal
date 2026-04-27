from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from car_model.setup_registry import (
    diff_ramp_option_index,
    internal_solver_value,
    public_output_value,
    snap_supporting_field_value,
)

logger = logging.getLogger(__name__)
from solver.candidate_ranker import CandidateScore, score_from_prediction
from solver.solve_chain import (
    SolveChainInputs,
    SolveChainOverrides,
    SolveChainResult,
    materialize_overrides,
)


STEP6_FIELDS = ("ls_comp", "ls_rbd", "hs_comp", "hs_rbd", "hs_slope")

# BMW torsion bar OD discrete options (iRacing garage)
_TORSION_OD_OPTIONS = [13.90, 14.34, 14.76, 15.14, 15.51, 15.86, 16.19, 16.51, 16.81, 17.11, 17.39, 17.67, 17.94, 18.20]


def _snap_nearest(value: float, options: list[float]) -> float:
    return min(options, key=lambda x: abs(x - value))


def _snap_step(value: float, step: float, lo: float | None = None, hi: float | None = None) -> float:
    snapped = round(round(value / step) * step, 4)
    if lo is not None:
        snapped = max(lo, snapped)
    if hi is not None:
        snapped = min(hi, snapped)
    return snapped


def _snap_option(value: float, options: list[float]) -> float:
    return min(options, key=lambda x: abs(float(x) - float(value)))


def _step_option(value: float, options: list[float], delta: int) -> float:
    if not options:
        return value
    ordered = sorted(float(item) for item in options)
    nearest_idx = min(range(len(ordered)), key=lambda idx: abs(ordered[idx] - float(value)))
    target_idx = max(0, min(len(ordered) - 1, nearest_idx + int(delta)))
    return ordered[target_idx]


def _step_string_option(value: str, options: list[str], delta: int, *, default_idx: int = 0) -> str:
    if not options:
        return value
    normalized = value if value in options else options[max(0, min(len(options) - 1, default_idx))]
    idx = max(0, min(len(options) - 1, options.index(normalized) + int(delta)))
    return options[idx]


def _snap_targets_to_garage(targets: dict[str, Any], car: Any | None = None) -> None:
    """Snap all blended target values to valid iRacing garage increments."""
    gr = getattr(car, "garage_ranges", None) if car is not None else None
    _car_name = getattr(car, "canonical_name", "unknown") if car else "unknown"
    if gr is None and _car_name != "unknown":
        logger.warning(
            "Car '%s' has no garage_ranges — candidate search will use BMW "
            "fallback ranges which may be wrong for this car",
            _car_name,
        )
    s1 = targets["step1"]
    s2 = targets["step2"]
    s3 = targets["step3"]
    s5 = targets["step5"]
    sup = targets["supporting"]

    # Step 1: pushrods (0.5 mm step)
    pushrod_step = getattr(gr, "pushrod_resolution_mm", 0.5) or 0.5
    pushrod_lo = getattr(gr, "front_pushrod_mm", (-40.0, 40.0))[0] if gr is not None else -40.0
    pushrod_hi = getattr(gr, "front_pushrod_mm", (-40.0, 40.0))[1] if gr is not None else 40.0
    for f in ("front_pushrod_offset_mm", "rear_pushrod_offset_mm"):
        if f in s1 and isinstance(s1[f], (int, float)):
            s1[f] = _snap_step(s1[f], pushrod_step, pushrod_lo, pushrod_hi)

    # Step 2: heave spring (10 N/mm), third spring (10 N/mm), perches (1 mm / 0.5 mm)
    heave_step = getattr(gr, "heave_spring_resolution_nmm", 10.0) or 10.0
    front_heave_range = getattr(gr, "front_heave_nmm", (0.0, 900.0)) if gr is not None else (0.0, 900.0)
    rear_heave_range = getattr(gr, "rear_third_nmm", (0.0, 900.0)) if gr is not None else (0.0, 900.0)
    front_perch_step = (
        getattr(gr, "front_heave_perch_resolution_mm", None)
        or getattr(gr, "perch_resolution_mm", 1.0)
        or 1.0
    )
    rear_perch_step = (
        getattr(gr, "rear_third_perch_resolution_mm", None)
        or getattr(gr, "perch_resolution_mm", 1.0)
        or 1.0
    )
    front_perch_range = getattr(gr, "front_heave_perch_mm", (-100.0, 100.0)) if gr is not None else (-100.0, 100.0)
    rear_perch_range = getattr(gr, "rear_third_perch_mm", (-100.0, 100.0)) if gr is not None else (-100.0, 100.0)
    if "front_heave_nmm" in s2 and isinstance(s2["front_heave_nmm"], (int, float)):
        s2["front_heave_nmm"] = _snap_step(s2["front_heave_nmm"], heave_step, front_heave_range[0], front_heave_range[1])
    if "rear_third_nmm" in s2 and isinstance(s2["rear_third_nmm"], (int, float)):
        s2["rear_third_nmm"] = _snap_step(s2["rear_third_nmm"], heave_step, rear_heave_range[0], rear_heave_range[1])
    if "perch_offset_front_mm" in s2 and isinstance(s2["perch_offset_front_mm"], (int, float)):
        s2["perch_offset_front_mm"] = _snap_step(s2["perch_offset_front_mm"], front_perch_step, front_perch_range[0], front_perch_range[1])
    if "perch_offset_rear_mm" in s2 and isinstance(s2["perch_offset_rear_mm"], (int, float)):
        s2["perch_offset_rear_mm"] = _snap_step(s2["perch_offset_rear_mm"], rear_perch_step, rear_perch_range[0], rear_perch_range[1])

    # Step 3: torsion OD (discrete), rear spring (5 N/mm step), rear spring perch (0.5 mm)
    torsion_range = getattr(gr, "front_torsion_od_mm", (13.9, 18.2)) if gr is not None else (13.9, 18.2)
    rear_spring_range = getattr(gr, "rear_spring_nmm", (100.0, 300.0)) if gr is not None else (100.0, 300.0)
    rear_spring_step = getattr(gr, "rear_spring_resolution_nmm", 5.0) or 5.0
    rear_spring_perch_range = getattr(gr, "rear_spring_perch_mm", (25.0, 45.0)) if gr is not None else (25.0, 45.0)
    rear_spring_perch_step = getattr(gr, "rear_spring_perch_resolution_mm", 0.5) or 0.5
    if "front_torsion_od_mm" in s3 and isinstance(s3["front_torsion_od_mm"], (int, float)):
        if getattr(car, "canonical_name", "") == "ferrari":
            s3["front_torsion_od_mm"] = _snap_step(s3["front_torsion_od_mm"], rear_spring_step, torsion_range[0], torsion_range[1])
        else:
            # Use car-specific torsion OD options. Cars without torsion bars
            # (Porsche, Acura) should have an empty list — leave the value
            # untouched rather than silently snapping to BMW options.
            csm = getattr(car, "corner_spring", None) if car is not None else None
            options = getattr(csm, "front_torsion_od_options", None)
            if options:
                s3["front_torsion_od_mm"] = _snap_nearest(s3["front_torsion_od_mm"], list(options))
            elif csm is not None and getattr(csm, "front_torsion_c", 0.0) > 0:
                # Car has torsion bar physics but no explicit option list:
                # fall back to the BMW grid (legacy behavior).
                s3["front_torsion_od_mm"] = _snap_nearest(s3["front_torsion_od_mm"], _TORSION_OD_OPTIONS)
    if "rear_spring_rate_nmm" in s3 and isinstance(s3["rear_spring_rate_nmm"], (int, float)):
        s3["rear_spring_rate_nmm"] = _snap_step(s3["rear_spring_rate_nmm"], rear_spring_step, rear_spring_range[0], rear_spring_range[1])
    if "rear_spring_perch_mm" in s3 and isinstance(s3["rear_spring_perch_mm"], (int, float)):
        s3["rear_spring_perch_mm"] = _snap_step(
            s3["rear_spring_perch_mm"],
            rear_spring_perch_step,
            rear_spring_perch_range[0],
            rear_spring_perch_range[1],
        )

    # Use car's actual ARB blade count when available (Porsche has 1-16, BMW 1-5)
    _arb = getattr(car, "arb", None) if car is not None else None
    _rear_blade_max = getattr(_arb, "rear_blade_count", None) if _arb else None
    if _rear_blade_max is not None:
        arb_range = (1, int(_rear_blade_max))
    elif gr is not None:
        arb_range = getattr(gr, "arb_blade", (1, 5))
    else:
        arb_range = (1, 5)
    for field in ("front_arb_blade_start", "rarb_blade_slow_corner", "rarb_blade_fast_corner", "rear_arb_blade_start", "farb_blade_locked"):
        target = targets["step4"]
        if field in target and isinstance(target[field], (int, float)):
            target[field] = int(round(_clamp(float(target[field]), arb_range[0], arb_range[1])))

    # Step 5: camber (0.1 deg step), toe (0.5 mm step)
    camber_front = getattr(gr, "camber_front_deg", (-5.0, 0.0)) if gr is not None else (-5.0, 0.0)
    camber_rear = getattr(gr, "camber_rear_deg", (-4.0, 0.0)) if gr is not None else (-4.0, 0.0)
    toe_front = getattr(gr, "toe_front_mm", (-3.0, 3.0)) if gr is not None else (-3.0, 3.0)
    toe_rear = getattr(gr, "toe_rear_mm", (-2.0, 3.0)) if gr is not None else (-2.0, 3.0)
    for f in ("front_camber_deg", "rear_camber_deg"):
        if f in s5 and isinstance(s5[f], (int, float)):
            limits = camber_front if "front" in f else camber_rear
            s5[f] = round(_clamp(round(round(s5[f] / 0.1) * 0.1, 1), limits[0], limits[1]), 1)
    for f in ("front_toe_mm", "rear_toe_mm"):
        if f in s5 and isinstance(s5[f], (int, float)):
            limits = toe_front if "front" in f else toe_rear
            s5[f] = _snap_step(s5[f], 0.1, limits[0], limits[1])

    # Supporting: centralized registry-backed snapping.
    for field_name, field_value in list(sup.items()):
        sup[field_name] = snap_supporting_field_value(car, field_name, field_value)
    if "pad_compound" in sup:
        pad_options = list(getattr(gr, "brake_pad_compound_options", []) or ["Low", "Medium", "High"]) if gr is not None else ["Low", "Medium", "High"]
        if sup["pad_compound"] not in pad_options:
            sup["pad_compound"] = pad_options[min(len(pad_options) - 1, 1)]


def _cluster_center_issues(car: Any | None, setup_cluster: Any | None) -> list[str]:
    center = getattr(setup_cluster, "center", {}) or {}
    if not center:
        return ["setup cluster has no center"]
    if car is None:
        return []
    gr = getattr(car, "garage_ranges", None) if car is not None else None
    issues: list[str] = []
    checks = [
        ("front_pushrod_mm", getattr(gr, "front_pushrod_mm", None)),
        ("rear_pushrod_mm", getattr(gr, "rear_pushrod_mm", None)),
        ("front_heave_nmm", getattr(gr, "front_heave_nmm", None)),
        ("rear_third_nmm", getattr(gr, "rear_third_nmm", None)),
        ("front_torsion_od_mm", getattr(gr, "front_torsion_od_mm", None)),
        ("rear_spring_nmm", getattr(gr, "rear_spring_nmm", None)),
        ("front_arb_blade", getattr(gr, "arb_blade", None)),
        ("rear_arb_blade", getattr(gr, "arb_blade", None)),
        ("front_camber_deg", getattr(gr, "camber_front_deg", None)),
        ("rear_camber_deg", getattr(gr, "camber_rear_deg", None)),
        ("front_toe_mm", getattr(gr, "toe_front_mm", None)),
        ("rear_toe_mm", getattr(gr, "toe_rear_mm", None)),
        ("diff_preload_nm", getattr(gr, "diff_preload_nm", None)),
    ]
    for key, limits in checks:
        value = _safe_float(center.get(key))
        if value is None or limits is None:
            continue
        if value < float(limits[0]) or value > float(limits[1]):
            issues.append(f"{key} center {value:.3f} is outside legal range {limits}")
    brake_bias = _safe_float(center.get("brake_bias_pct"))
    baseline_bias = _safe_float(getattr(car, "brake_bias_pct", None)) if car is not None else None
    if brake_bias is not None and baseline_bias is not None:
        if brake_bias < baseline_bias - 10.0 or brake_bias > baseline_bias + 10.0:
            issues.append(
                f"brake_bias_pct center {brake_bias:.3f} is implausible for {getattr(car, 'canonical_name', 'car')}"
            )
    return issues


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


def _get_metric(source: Any, key: str) -> Any:
    """Get a metric from either a dict or an object."""
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)


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


def _extract_target_maps(base_result: SolveChainResult, car: Any | None = None) -> dict[str, Any]:
    # Guard against None steps (blocked by calibration gate)
    s1, s2, s3, s4, s5, s6 = (
        base_result.step1, base_result.step2, base_result.step3,
        base_result.step4, base_result.step5, base_result.step6,
    )
    return {
        "step1": {
            "front_pushrod_offset_mm": s1.front_pushrod_offset_mm,
            "rear_pushrod_offset_mm": s1.rear_pushrod_offset_mm,
            "static_front_rh_mm": s1.static_front_rh_mm,
            "static_rear_rh_mm": s1.static_rear_rh_mm,
        } if s1 is not None else {},
        # Step 2 emits an empty target dict when (a) calibration gate skipped
        # the step (s2 is None) OR (b) the car has no heave/third architecture
        # and HeaveSolution.null() was used (present=False). An empty dict
        # cleanly skips the snap pass for these fields downstream — see
        # _snap_targets_to_garage which only reads keys that are present.
        "step2": {
            "front_heave_nmm": public_output_value(car, "front_heave_nmm", s2.front_heave_nmm),
            "rear_third_nmm": public_output_value(car, "rear_third_nmm", s2.rear_third_nmm),
            "perch_offset_front_mm": s2.perch_offset_front_mm,
            "perch_offset_rear_mm": s2.perch_offset_rear_mm,
        } if s2 is not None and getattr(s2, "present", True) else {},
        "step3": {
            "front_torsion_od_mm": public_output_value(car, "front_torsion_od_mm", s3.front_torsion_od_mm),
            "rear_spring_rate_nmm": public_output_value(car, "rear_spring_rate_nmm", s3.rear_spring_rate_nmm),
            "rear_spring_perch_mm": (
                0.0 if getattr(car, "canonical_name", "") == "ferrari" else s3.rear_spring_perch_mm
            ),
        } if s3 is not None else {},
        "step4": {
            "front_arb_size": s4.front_arb_size,
            "front_arb_blade_start": s4.front_arb_blade_start,
            "rear_arb_size": s4.rear_arb_size,
            "rear_arb_blade_start": s4.rear_arb_blade_start,
            "rarb_blade_slow_corner": s4.rarb_blade_slow_corner,
            "rarb_blade_fast_corner": s4.rarb_blade_fast_corner,
            "farb_blade_locked": s4.farb_blade_locked,
        } if s4 is not None else {},
        "step5": {
            "front_camber_deg": s5.front_camber_deg,
            "rear_camber_deg": s5.rear_camber_deg,
            "front_toe_mm": s5.front_toe_mm,
            "rear_toe_mm": s5.rear_toe_mm,
        } if s5 is not None else {},
        "step6": ({
            corner_name: {
                field: getattr(getattr(s6, corner_name), field)
                for field in STEP6_FIELDS
            }
            for corner_name in ("lf", "rf", "lr", "rr")
        } if s6 is not None else {}),
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
            "diff_clutch_plates": getattr(base_result.supporting, "diff_clutch_plates", 6),
            "diff_ramp_option_idx": getattr(base_result.supporting, "diff_ramp_option_idx", 1),
            "diff_ramp_angles": getattr(base_result.supporting, "diff_ramp_angles", ""),
            "fuel_l": getattr(base_result.supporting, "fuel_l", 0.0),
            "fuel_low_warning_l": getattr(base_result.supporting, "fuel_low_warning_l", 0.0),
            "fuel_target_l": getattr(base_result.supporting, "fuel_target_l", 0.0),
            "gear_stack": getattr(base_result.supporting, "gear_stack", ""),
            "roof_light_color": getattr(base_result.supporting, "roof_light_color", ""),
        },
        "step4_arb_size": ({
            "front_arb_size": s4.front_arb_size,
            "rear_arb_size": s4.rear_arb_size,
        } if s4 is not None else {}),
    }


def canonical_params_to_overrides(
    base_result: SolveChainResult,
    params: dict[str, Any],
    *,
    car: Any | None = None,
) -> SolveChainOverrides:
    """Convert canonical legal-search params into snapped solve-chain overrides."""
    targets = _extract_target_maps(base_result, car)
    explicit_supporting_fields: set[str] = set()

    def _set_step6(axle: str, field: str, value: Any) -> None:
        corners = ("lf", "rf") if axle == "front" else ("lr", "rr")
        for corner_name in corners:
            targets["step6"][corner_name][field] = int(round(float(value)))

    direct_step_map: dict[str, tuple[str, str]] = {
        "front_pushrod_offset_mm": ("step1", "front_pushrod_offset_mm"),
        "rear_pushrod_offset_mm": ("step1", "rear_pushrod_offset_mm"),
        "front_heave_spring_nmm": ("step2", "front_heave_nmm"),
        "front_heave_index": ("step2", "front_heave_nmm"),
        "front_heave_perch_mm": ("step2", "perch_offset_front_mm"),
        "rear_third_spring_nmm": ("step2", "rear_third_nmm"),
        "rear_heave_index": ("step2", "rear_third_nmm"),
        "rear_third_perch_mm": ("step2", "perch_offset_rear_mm"),
        "front_torsion_od_mm": ("step3", "front_torsion_od_mm"),
        "front_torsion_bar_index": ("step3", "front_torsion_od_mm"),
        "rear_spring_rate_nmm": ("step3", "rear_spring_rate_nmm"),
        "rear_torsion_bar_index": ("step3", "rear_spring_rate_nmm"),
        "rear_spring_perch_mm": ("step3", "rear_spring_perch_mm"),
        "front_camber_deg": ("step5", "front_camber_deg"),
        "rear_camber_deg": ("step5", "rear_camber_deg"),
        "front_toe_mm": ("step5", "front_toe_mm"),
        "rear_toe_mm": ("step5", "rear_toe_mm"),
        "brake_bias_pct": ("supporting", "brake_bias_pct"),
        "brake_bias_target": ("supporting", "brake_bias_target"),
        "brake_bias_migration": ("supporting", "brake_bias_migration"),
        "front_master_cyl_mm": ("supporting", "front_master_cyl_mm"),
        "rear_master_cyl_mm": ("supporting", "rear_master_cyl_mm"),
        "pad_compound": ("supporting", "pad_compound"),
        "diff_preload_nm": ("supporting", "diff_preload_nm"),
        "diff_clutch_plates": ("supporting", "diff_clutch_plates"),
        "diff_ramp_option_idx": ("supporting", "diff_ramp_option_idx"),
        "tc_gain": ("supporting", "tc_gain"),
        "tc_slip": ("supporting", "tc_slip"),
        "fuel_l": ("supporting", "fuel_l"),
        "fuel_low_warning_l": ("supporting", "fuel_low_warning_l"),
        "fuel_target_l": ("supporting", "fuel_target_l"),
        "gear_stack": ("supporting", "gear_stack"),
        "roof_light_color": ("supporting", "roof_light_color"),
    }

    for key, value in params.items():
        if value is None:
            continue
        if key in direct_step_map:
            step_name, field_name = direct_step_map[key]
            targets[step_name][field_name] = value
            if step_name == "supporting":
                explicit_supporting_fields.add(field_name)
            continue
        if key == "front_arb_size":
            targets["step4_arb_size"]["front_arb_size"] = value
            continue
        if key == "rear_arb_size":
            targets["step4_arb_size"]["rear_arb_size"] = value
            continue
        if key == "front_arb_blade":
            blade = int(round(float(value)))
            targets["step4"]["front_arb_blade_start"] = blade
            targets["step4"]["farb_blade_locked"] = blade
            continue
        if key == "rear_arb_blade":
            blade = int(round(float(value)))
            targets["step4"]["rear_arb_blade_start"] = blade
            targets["step4"]["rarb_blade_slow_corner"] = blade
            targets["step4"]["rarb_blade_fast_corner"] = blade
            continue
        if key == "front_ls_comp":
            _set_step6("front", "ls_comp", value)
            continue
        if key == "front_ls_rbd":
            _set_step6("front", "ls_rbd", value)
            continue
        if key == "front_hs_comp":
            _set_step6("front", "hs_comp", value)
            continue
        if key == "front_hs_rbd":
            _set_step6("front", "hs_rbd", value)
            continue
        if key == "front_hs_slope":
            _set_step6("front", "hs_slope", value)
            continue
        if key == "rear_ls_comp":
            _set_step6("rear", "ls_comp", value)
            continue
        if key == "rear_ls_rbd":
            _set_step6("rear", "ls_rbd", value)
            continue
        if key == "rear_hs_comp":
            _set_step6("rear", "hs_comp", value)
            continue
        if key == "rear_hs_rbd":
            _set_step6("rear", "hs_rbd", value)
            continue
        if key == "rear_hs_slope":
            _set_step6("rear", "hs_slope", value)
            continue
        if key in {"diff_ramp_angles", "rear_diff_ramp_label"}:
            current_idx = targets["supporting"].get("diff_ramp_option_idx", 1)
            targets["supporting"]["diff_ramp_option_idx"] = diff_ramp_option_index(
                getattr(car, "canonical_name", ""),
                diff_ramp_angles=value,
                default=int(round(float(current_idx))) if current_idx is not None else 1,
            )
            explicit_supporting_fields.add("diff_ramp_option_idx")

    _snap_targets_to_garage(targets, car)
    overrides = _target_overrides(base_result, targets, car=car)
    for field_name in explicit_supporting_fields:
        target_value = targets["supporting"].get(field_name)
        if getattr(base_result.supporting, field_name, None) != target_value:
            overrides.supporting[field_name] = target_value
    return overrides


def _apply_cluster_center(targets: dict[str, Any], setup_cluster: Any, *, car_name: str = "bmw") -> None:
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
    if "brake_bias_target" in center:
        _set_numeric(targets["supporting"], "brake_bias_target", center["brake_bias_target"])
    if "brake_bias_migration" in center:
        _set_numeric(targets["supporting"], "brake_bias_migration", center["brake_bias_migration"])
    if "front_master_cyl_mm" in center:
        _set_numeric(targets["supporting"], "front_master_cyl_mm", center["front_master_cyl_mm"])
    if "rear_master_cyl_mm" in center:
        _set_numeric(targets["supporting"], "rear_master_cyl_mm", center["rear_master_cyl_mm"])
    if "pad_compound" in center:
        targets["supporting"]["pad_compound"] = center["pad_compound"]
    if "diff_preload_nm" in center:
        _set_numeric(targets["supporting"], "diff_preload_nm", center["diff_preload_nm"])
    if "tc_gain" in center:
        targets["supporting"]["tc_gain"] = int(round(center["tc_gain"]))
    if "tc_slip" in center:
        targets["supporting"]["tc_slip"] = int(round(center["tc_slip"]))
    if "diff_clutch_plates" in center:
        targets["supporting"]["diff_clutch_plates"] = int(round(center["diff_clutch_plates"]))
    if "diff_ramp_option_idx" in center:
        targets["supporting"]["diff_ramp_option_idx"] = int(round(center["diff_ramp_option_idx"]))
    elif "diff_ramp_coast" in center or "diff_ramp_drive" in center:
        ramp_idx = diff_ramp_option_index(
            car_name,
            coast=center.get("diff_ramp_coast"),
            drive=center.get("diff_ramp_drive"),
            default=1,
        )
        if ramp_idx is not None:
            targets["supporting"]["diff_ramp_option_idx"] = ramp_idx
    if "front_arb_size" in center:
        if "step4_arb_size" in targets:
            targets["step4_arb_size"]["front_arb_size"] = center["front_arb_size"]
    if "rear_arb_size" in center:
        if "step4_arb_size" in targets:
            targets["step4_arb_size"]["rear_arb_size"] = center["rear_arb_size"]


def _apply_family_state_adjustments(
    targets: dict[str, Any],
    *,
    car: Any | None = None,
    family: str,
    aggregate_measured: dict[str, float] | None = None,
    overhaul_class: str,
    envelope_distance: float,
    setup_distance: float,
    cluster_seeded: bool = False,
    # Backward compat: if aggregate_measured not provided, extract from authority_session
    authority_session: Any = None,
) -> None:
    if aggregate_measured is not None:
        measured = aggregate_measured
    else:
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
            (((_safe_float(_get_metric(measured, "front_heave_travel_used_pct")) or 0.0) - 80.0) / 20.0),
            (((_safe_float(_get_metric(measured, "pitch_range_braking_deg")) or 0.0) - 0.9) / 0.8),
            (_safe_float(_get_metric(measured, "bottoming_event_count_front_clean")) or 0.0) / 6.0,
        ),
        0.0,
        1.25,
    )
    rear_support = _clamp(
        max(
            (((_safe_float(_get_metric(measured, "rear_rh_std_mm")) or 0.0) - 6.0) / 4.0),
            (_safe_float(_get_metric(measured, "bottoming_event_count_rear_clean")) or 0.0) / 6.0,
        ),
        0.0,
        1.25,
    )
    entry_push = _clamp(((_safe_float(_get_metric(measured, "understeer_low_speed_deg")) or 0.0) - 0.9) / 1.2, 0.0, 1.0)
    high_speed_push = _clamp(
        (((_safe_float(_get_metric(measured, "understeer_high_speed_deg")) or 0.0) - (_safe_float(_get_metric(measured, "understeer_low_speed_deg")) or 0.0)) - 0.2) / 0.8,
        0.0,
        1.0,
    )
    exit_instability = _clamp(
        max(
            (((_safe_float(_get_metric(measured, "rear_power_slip_ratio_p95")) or 0.0) - 0.07) / 0.06),
            (((_safe_float(_get_metric(measured, "body_slip_p95_deg")) or 0.0) - 3.2) / 2.5),
        ),
        0.0,
        1.2,
    )
    front_lock = _clamp(((_safe_float(_get_metric(measured, "front_braking_lock_ratio_p95")) or 0.0) - 0.06) / 0.05, 0.0, 1.0)

    is_ferrari = getattr(car, "canonical_name", "") == "ferrari"

    _adjust_numeric(targets["step1"], "front_pushrod_offset_mm", 0.8 * front_support * family_intensity, decimals=3)

    if not cluster_seeded:
        # Driver-loaded heave anchor: when the base solver already returned
        # values matching the driver's current setup (within snap step), the
        # heave/third have been validated and shouldn't be scaled by the
        # family heuristic. Scaling on top of an anchor causes 1-step drift
        # away from the driver-validated operating point. Look up the loaded
        # values from authority_session.setup if available.
        _curr_setup = getattr(authority_session, "setup", None) if authority_session else None
        _curr_fheave = float(getattr(_curr_setup, "front_heave_nmm", 0.0) or 0.0) if _curr_setup else 0.0
        _curr_rthird = float(getattr(_curr_setup, "rear_third_nmm", 0.0) or 0.0) if _curr_setup else 0.0
        _base_fheave = float(targets["step2"].get("front_heave_nmm", 0.0) or 0.0)
        _base_rthird = float(targets["step2"].get("rear_third_nmm", 0.0) or 0.0)
        _fheave_anchored = _curr_fheave > 0 and abs(_base_fheave - _curr_fheave) <= 30.0
        _rthird_anchored = _curr_rthird > 0 and abs(_base_rthird - _curr_rthird) <= 30.0

        if is_ferrari:
            _adjust_numeric(
                targets["step2"],
                "front_heave_nmm",
                round(1.5 * front_support * family_intensity),
                decimals=0,
            )
            _adjust_numeric(
                targets["step2"],
                "rear_third_nmm",
                round(1.5 * rear_support * family_intensity),
                decimals=0,
            )
        else:
            if not _fheave_anchored:
                _scale_numeric(targets["step2"], "front_heave_nmm", 1.0 + 0.12 * front_support * family_intensity, decimals=3)
            if not _rthird_anchored:
                _scale_numeric(targets["step2"], "rear_third_nmm", 1.0 + 0.12 * rear_support * family_intensity, decimals=3)
    _adjust_numeric(targets["step2"], "perch_offset_front_mm", 1.5 * front_support * family_intensity, decimals=3)
    _adjust_numeric(targets["step2"], "perch_offset_rear_mm", 2.0 * rear_support * family_intensity, decimals=3)

    if is_ferrari:
        _adjust_numeric(
            targets["step3"],
            "front_torsion_od_mm",
            -round(max(entry_push, high_speed_push) * 2.0 * family_intensity),
            decimals=0,
        )
        _adjust_numeric(
            targets["step3"],
            "rear_spring_rate_nmm",
            round((1.5 * rear_support - 1.5 * exit_instability) * family_intensity),
            decimals=0,
        )
    else:
        _adjust_numeric(targets["step3"], "front_torsion_od_mm", -0.12 * entry_push * family_intensity, decimals=4)
        _adjust_numeric(
            targets["step3"],
            "rear_spring_rate_nmm",
            (8.0 * rear_support - 6.0 * exit_instability) * family_intensity,
            decimals=3,
        )

    arb_delta = int(round((entry_push + high_speed_push - exit_instability) * family_intensity))
    # Clamp to the car-specific rear ARB blade range, NOT a hardcoded 1-6
    # (which was a BMW assumption). Porsche's rear ARB has blade range 1-16;
    # driver-validated operating point is blade=10 — unreachable with hi=6.
    _arb_hi = (
        int(getattr(getattr(car, "arb", None), "rear_blade_count", 6))
        if car is not None else 6
    ) or 6
    _adjust_integer(targets["step4"], "rear_arb_blade_start", arb_delta, lo=1, hi=_arb_hi)
    _adjust_integer(targets["step4"], "rarb_blade_slow_corner", arb_delta, lo=1, hi=_arb_hi)
    _adjust_integer(targets["step4"], "rarb_blade_fast_corner", arb_delta, lo=1, hi=_arb_hi)

    _adjust_numeric(targets["step5"], "front_camber_deg", -0.12 * entry_push * family_intensity, decimals=3)
    _adjust_numeric(targets["step5"], "rear_camber_deg", -0.08 * exit_instability * family_intensity, decimals=3)
    _adjust_numeric(targets["step5"], "front_toe_mm", -0.05 * entry_push * family_intensity, decimals=3)

    if targets.get("step6"):
        # Damper adjustment bounds and direction are per-car. The sign convention
        # of "stiffer = +N clicks" only holds for higher-stiffer polarity (BMW,
        # Aston, Porsche 992, Mercedes, Ferrari, Acura, Lambo, Mustang). For
        # inverted-polarity cars (Audi R8 LMS, McLaren 720S, Corvette Z06) the
        # same intent of "+N stiffer" must invert to "-N clicks". Bounds also
        # come from the per-car damper range (e.g. Porsche 992 = 0-12, BMW =
        # 0-11; future inverted cars: McLaren HS = 0-50).
        d = getattr(car, "damper", None) if car is not None else None
        polarity = getattr(d, "click_polarity", "higher_stiffer")
        polarity_sign = 1 if polarity == "higher_stiffer" else -1
        hs_comp_lo, hs_comp_hi = (
            d.hs_comp_range if d is not None else (0, 20)
        )
        ls_rbd_lo, ls_rbd_hi = (
            d.ls_rbd_range if d is not None else (0, 20)
        )
        for corner_name in ("lf", "rf"):
            if corner_name in targets["step6"]:
                _adjust_integer(
                    targets["step6"][corner_name],
                    "hs_comp",
                    polarity_sign * int(round(1.5 * front_support * family_intensity)),
                    lo=hs_comp_lo,
                    hi=hs_comp_hi,
                )
                _adjust_integer(
                    targets["step6"][corner_name],
                    "ls_rbd",
                    polarity_sign * int(round((front_support + front_lock) * family_intensity)),
                    lo=ls_rbd_lo,
                    hi=ls_rbd_hi,
                )
        for corner_name in ("lr", "rr"):
            if corner_name in targets["step6"]:
                _adjust_integer(
                    targets["step6"][corner_name],
                    "hs_comp",
                    polarity_sign * int(round(1.5 * rear_support * family_intensity)),
                    lo=hs_comp_lo,
                    hi=hs_comp_hi,
                )
                _adjust_integer(
                    targets["step6"][corner_name],
                    "ls_rbd",
                    polarity_sign * int(round(rear_support * family_intensity)),
                    lo=ls_rbd_lo,
                    hi=ls_rbd_hi,
                )

    _adjust_numeric(targets["supporting"], "brake_bias_pct", -0.3 * front_lock * family_intensity, decimals=3)
    _adjust_numeric(targets["supporting"], "brake_bias_target", -0.5 * front_lock * family_intensity, decimals=3)
    _adjust_numeric(targets["supporting"], "brake_bias_migration", -0.35 * front_lock * family_intensity, decimals=3)
    _adjust_numeric(targets["supporting"], "diff_preload_nm", 5.0 * exit_instability * family_intensity, decimals=3)
    _adjust_integer(
        targets["supporting"],
        "diff_ramp_option_idx",
        int(round((0.8 * entry_push - 1.2 * exit_instability) * family_intensity)),
        lo=0,
        hi=2,
    )
    _adjust_integer(
        targets["supporting"],
        "diff_clutch_plates",
        int(round(2.0 * exit_instability * family_intensity)),
        lo=2,
        hi=6,
    )
    _adjust_integer(targets["supporting"], "tc_gain", int(round(exit_instability * family_intensity)), lo=1, hi=10)
    _adjust_integer(targets["supporting"], "tc_slip", int(round(0.8 * exit_instability * family_intensity)), lo=1, hi=10)
    mc_options = [15.9, 16.8, 17.8, 19.1, 20.6, 22.2, 23.8]
    pad_options = ["Low", "Medium", "High"]
    if front_lock > 0.2:
        targets["supporting"]["front_master_cyl_mm"] = _step_option(
            float(targets["supporting"].get("front_master_cyl_mm", 19.1)),
            mc_options,
            -1,
        )
        targets["supporting"]["rear_master_cyl_mm"] = _step_option(
            float(targets["supporting"].get("rear_master_cyl_mm", 20.6)),
            mc_options,
            +1,
        )
        targets["supporting"]["pad_compound"] = _step_string_option(
            str(targets["supporting"].get("pad_compound", "Medium") or "Medium"),
            pad_options,
            -1,
            default_idx=1,
        )
    elif front_lock < 0.05 and family != "incremental":
        targets["supporting"]["front_master_cyl_mm"] = _step_option(
            float(targets["supporting"].get("front_master_cyl_mm", 19.1)),
            mc_options,
            +1,
        )
        targets["supporting"]["rear_master_cyl_mm"] = _step_option(
            float(targets["supporting"].get("rear_master_cyl_mm", 20.6)),
            mc_options,
            -1,
        )
        targets["supporting"]["pad_compound"] = _step_string_option(
            str(targets["supporting"].get("pad_compound", "Medium") or "Medium"),
            pad_options,
            +1,
            default_idx=1,
        )


def _target_overrides(base_result: SolveChainResult, targets: dict[str, Any], *, car: Any | None = None) -> SolveChainOverrides:
    overrides = SolveChainOverrides()
    for field_name, value in targets["step1"].items():
        if getattr(base_result.step1, field_name) != value:
            overrides.step1[field_name] = value
    for field_name, value in targets["step2"].items():
        base_value = public_output_value(car, field_name, getattr(base_result.step2, field_name))
        if base_value != value:
            overrides.step2[field_name] = value
    for field_name, value in targets["step3"].items():
        base_value = public_output_value(car, field_name, getattr(base_result.step3, field_name))
        if base_value != value:
            overrides.step3[field_name] = value
    if base_result.step4 is not None:
        for field_name, value in targets["step4"].items():
            if getattr(base_result.step4, field_name) != value:
                overrides.step4[field_name] = value
    if base_result.step5 is not None:
        for field_name, value in targets["step5"].items():
            if getattr(base_result.step5, field_name) != value:
                overrides.step5[field_name] = value
    if base_result.step6 is not None:
        for corner_name, fields in targets["step6"].items():
            corner_overrides: dict[str, Any] = {}
            base_corner = getattr(base_result.step6, corner_name, None)
            if base_corner is None:
                continue
            for field_name, value in fields.items():
                if getattr(base_corner, field_name) != value:
                    corner_overrides[field_name] = value
            if corner_overrides:
                overrides.step6[corner_name] = corner_overrides
    for field_name, value in targets["supporting"].items():
        if hasattr(base_result.supporting, field_name):
            if getattr(base_result.supporting, field_name) != value:
                overrides.supporting[field_name] = value
    # ARB size changes go into step4 overrides (only if step4 is present)
    if base_result.step4 is not None:
        for field_name, value in targets.get("step4_arb_size", {}).items():
            if getattr(base_result.step4, field_name, None) != value:
                overrides.step4[field_name] = value
    return overrides


def _state_risk(authority_session: Any) -> float:
    diagnosis = getattr(authority_session, "diagnosis", None)
    issues = getattr(diagnosis, "state_issues", []) or []
    if not issues:
        return 0.0
    return round(sum(getattr(issue, "severity", 0.0) * getattr(issue, "confidence", 0.0) for issue in issues), 3)


def _baseline_loss_ms(authority_session: Any) -> float:
    """Aggregate confidence-weighted estimated lap time loss from all state issues."""
    diagnosis = getattr(authority_session, "diagnosis", None)
    issues = getattr(diagnosis, "state_issues", []) or []
    if not issues:
        return 0.0
    return round(sum(
        getattr(issue, "estimated_loss_ms", 0.0) * getattr(issue, "confidence", 0.0)
        for issue in issues
    ), 1)


def _estimate_candidate_disruption(current_session: Any, candidate: SetupCandidate) -> float:
    setup = getattr(current_session, "setup", None)
    if setup is None or candidate.step1 is None:
        return 0.5

    car_name = str(getattr(setup, "adapter_name", "") or "").lower()
    is_ferrari = "ferrari" in car_name

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
    _append(getattr(setup, "front_heave_nmm", None), getattr(candidate.step2, "front_heave_nmm", None), 1.0 if is_ferrari else 25.0)
    _append(getattr(setup, "rear_third_nmm", None), getattr(candidate.step2, "rear_third_nmm", None), 1.0 if is_ferrari else 150.0)
    _append(getattr(setup, "front_torsion_od_mm", None), getattr(candidate.step3, "front_torsion_od_mm", None), 1.0 if is_ferrari else 1.0)
    _append(getattr(setup, "rear_spring_nmm", None), getattr(candidate.step3, "rear_spring_rate_nmm", None), 1.0 if is_ferrari else 35.0)
    _append(getattr(setup, "rear_arb_blade", None), getattr(candidate.step4, "rear_arb_blade_start", None), 2.0)
    _append(getattr(setup, "front_camber_deg", None), getattr(candidate.step5, "front_camber_deg", None), 0.5)
    _append(getattr(setup, "rear_camber_deg", None), getattr(candidate.step5, "rear_camber_deg", None), 0.4)
    _append(getattr(setup, "front_hs_comp", None), getattr(getattr(candidate.step6, "lf", None), "hs_comp", None), 3.0)
    _append(getattr(setup, "rear_hs_comp", None), getattr(getattr(candidate.step6, "lr", None), "hs_comp", None), 3.0)
    _append(getattr(setup, "brake_bias_pct", None), getattr(candidate.supporting, "brake_bias_pct", None), 0.8)
    _append(getattr(setup, "brake_bias_target", None), getattr(candidate.supporting, "brake_bias_target", None), 1.0)
    _append(getattr(setup, "brake_bias_migration", None), getattr(candidate.supporting, "brake_bias_migration", None), 1.0)
    _append(getattr(setup, "front_master_cyl_mm", None), getattr(candidate.supporting, "front_master_cyl_mm", None), 1.6)
    _append(getattr(setup, "rear_master_cyl_mm", None), getattr(candidate.supporting, "rear_master_cyl_mm", None), 1.6)
    _append(getattr(setup, "diff_preload_nm", None), getattr(candidate.supporting, "diff_preload_nm", None), 20.0)
    _append(getattr(setup, "diff_clutch_plates", None), getattr(candidate.supporting, "diff_clutch_plates", None), 2.0)
    _append(
        diff_ramp_option_index(
            car_name or "bmw",
            diff_ramp_angles=getattr(setup, "diff_ramp_angles", None),
            default=1,
        ),
        getattr(candidate.supporting, "diff_ramp_option_idx", None),
        1.0,
    )
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
    current_session: Any | None = None,
    aggregate_measured: dict[str, float] | None = None,
) -> list[SetupCandidate]:
    if base_result is None or solve_inputs is None:
        return []

    # Default current_session to authority_session for backward compat (single-IBT)
    if current_session is None:
        current_session = authority_session

    overhaul_class = getattr(overhaul_assessment, "classification", "minor_tweak")
    overhaul_conf = float(getattr(overhaul_assessment, "confidence", 0.55) or 0.55)
    authority_conf = float((authority_score or {}).get("score", 0.6) or 0.6)
    state_risk = _state_risk(authority_session)
    loss_ms = _baseline_loss_ms(authority_session)
    family_descriptions = {
        "incremental": "Conservative physics-driven corrections with minimal disruption.",
        "compromise": "Moderate physics-driven corrections balancing safety and grip.",
        "baseline_reset": "Full physics-optimal output from the 6-step solver.",
    }
    # Family priors: reward the family that matches the overhaul classification.
    # The prior is the "right tool for the job" bonus.
    family_prior = {
        "incremental": 0.06 if overhaul_class == "minor_tweak" else 0.02,
        "compromise": 0.06 if overhaul_class == "moderate_rework" else 0.02,
        "baseline_reset": 0.06 if overhaul_class == "baseline_reset" else 0.03,
    }
    # Family penalties: penalize over-aggressive changes when not warranted.
    # baseline_reset should only win when the car genuinely needs a full rework.
    family_penalty = {
        "incremental": 0.0,
        "compromise": 0.03 if overhaul_class == "minor_tweak" else 0.0,
        "baseline_reset": (
            0.03 if overhaul_class == "minor_tweak"
            else 0.02 if overhaul_class == "moderate_rework"
            else 0.0
        ),
    }
    car = getattr(solve_inputs, "car", None)

    candidates: list[SetupCandidate] = []
    for family in ("incremental", "compromise", "baseline_reset"):
        preblocked_reason: str | None = None
        preblocked_notes: list[str] = []
        # Hard gate: baseline_reset requires overhaul assessment to justify it,
        # OR a large setup cluster distance (>= 1.5) that independently signals wrong region.
        # A soft prior/penalty alone is insufficient — this prevents unnecessary solver blows.
        if family == "baseline_reset" and overhaul_class != "baseline_reset":
            large_setup_distance = setup_distance >= 1.5 or envelope_distance >= 1.5
            if not large_setup_distance:
                preblocked_reason = (
                    f"Overhaul assessment '{overhaul_class}' does not justify baseline_reset "
                    f"(requires 'baseline_reset' classification or setup_distance >= 1.5)"
                )
                preblocked_notes = [
                    f"Overhaul class: {overhaul_class}, setup_distance: {setup_distance:.2f}, envelope_distance: {envelope_distance:.2f}",
                    "Materialized for legality/reporting, but baseline_reset remains non-selectable without overhaul justification.",
                ]

        if family == "baseline_reset" and setup_cluster is not None:
            cluster_issues = _cluster_center_issues(car, setup_cluster)
            if cluster_issues:
                candidate = SetupCandidate(
                    family=family,
                    description=family_descriptions[family],
                    selectable=False,
                    status="blocked",
                    failure_reason="; ".join(cluster_issues),
                    notes=[
                        f"Authority session: {getattr(authority_session, 'label', 'unknown')}",
                        f"Best session: {getattr(best_session, 'label', 'unknown')}",
                        "Baseline-reset cluster blocked before materialization.",
                    ] + cluster_issues,
                )
                candidates.append(candidate)
                continue
        targets = _extract_target_maps(base_result, car)
        # No authority blending — solver output IS the physics-optimal starting point.
        # Candidate families differ by aggressiveness of state adjustments, not by
        # how much to anchor back to a historical setup.
        cluster_seeded = family == "baseline_reset" and setup_cluster is not None
        if cluster_seeded:
            _apply_cluster_center(targets, setup_cluster, car_name=getattr(car, "canonical_name", "bmw"))
        _apply_family_state_adjustments(
            targets,
            car=car,
            family=family,
            aggregate_measured=aggregate_measured,
            authority_session=authority_session,
            overhaul_class=overhaul_class,
            envelope_distance=envelope_distance,
            setup_distance=setup_distance,
            cluster_seeded=cluster_seeded,
        )
        _snap_targets_to_garage(targets, car)
        overrides = _target_overrides(base_result, targets, car=car)
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
                if preblocked_reason is not None:
                    candidate.selectable = False
                    candidate.status = "blocked"
                    candidate.failure_reason = preblocked_reason
                    candidate.notes.extend(preblocked_notes)
                else:
                    candidate.status = "ready"
                candidate.notes.extend(result.notes)
        except Exception as exc:
            candidate.selectable = False
            candidate.status = "failed"
            candidate.failure_reason = str(exc)
            candidate.notes.append(f"Materialization failed: {exc}")

        raw_disruption = _estimate_candidate_disruption(current_session, candidate)
        # Amplify disruption for more aggressive families — bigger changes carry
        # more prediction risk and driver adaptation cost.
        disruption_multiplier = {"incremental": 0.7, "compromise": 1.0, "baseline_reset": 1.1}.get(family, 1.0)
        disruption_cost = min(0.95, raw_disruption * disruption_multiplier)
        legal_ok = bool(getattr(candidate.legality, "valid", False))
        candidate.score = score_from_prediction(
            baseline_measured=getattr(current_session, "measured", None),
            predicted=candidate.predicted,
            prediction_confidence=candidate.confidence,
            disruption_cost=disruption_cost,
            envelope_distance=envelope_distance,
            setup_distance=setup_distance,
            legal_ok=legal_ok,
            authority_score=authority_conf,
            state_risk=state_risk,
            baseline_loss_ms=loss_ms,
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
                "front_arb_size": getattr(candidate.step4, "front_arb_size", None),
                "front_arb_blade_start": getattr(candidate.step4, "front_arb_blade_start", None),
                "farb_blade_locked": getattr(candidate.step4, "farb_blade_locked", None),
                "rear_arb_size": getattr(candidate.step4, "rear_arb_size", None),
                "rear_arb_blade_start": getattr(candidate.step4, "rear_arb_blade_start", None),
                "rarb_blade_slow_corner": getattr(candidate.step4, "rarb_blade_slow_corner", None),
                "rarb_blade_fast_corner": getattr(candidate.step4, "rarb_blade_fast_corner", None),
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
                "front_hs_comp": getattr(getattr(candidate.step6, "lf", None), "hs_comp", None),
                "front_hs_rbd": getattr(getattr(candidate.step6, "lf", None), "hs_rbd", None),
                "front_hs_slope": getattr(getattr(candidate.step6, "lf", None), "hs_slope", None),
                "rear_ls_comp": getattr(getattr(candidate.step6, "lr", None), "ls_comp", None),
                "rear_ls_rbd": getattr(getattr(candidate.step6, "lr", None), "ls_rbd", None),
                "rear_hs_comp": getattr(getattr(candidate.step6, "lr", None), "hs_comp", None),
                "rear_hs_rbd": getattr(getattr(candidate.step6, "lr", None), "hs_rbd", None),
                "rear_hs_slope": getattr(getattr(candidate.step6, "lr", None), "hs_slope", None),
                "lf": {
                    "ls_comp": getattr(getattr(candidate.step6, "lf", None), "ls_comp", None),
                    "ls_rbd": getattr(getattr(candidate.step6, "lf", None), "ls_rbd", None),
                    "hs_comp": getattr(getattr(candidate.step6, "lf", None), "hs_comp", None),
                    "hs_rbd": getattr(getattr(candidate.step6, "lf", None), "hs_rbd", None),
                    "hs_slope": getattr(getattr(candidate.step6, "lf", None), "hs_slope", None),
                },
                "rf": {
                    "ls_comp": getattr(getattr(candidate.step6, "rf", None), "ls_comp", None),
                    "ls_rbd": getattr(getattr(candidate.step6, "rf", None), "ls_rbd", None),
                    "hs_comp": getattr(getattr(candidate.step6, "rf", None), "hs_comp", None),
                    "hs_rbd": getattr(getattr(candidate.step6, "rf", None), "hs_rbd", None),
                    "hs_slope": getattr(getattr(candidate.step6, "rf", None), "hs_slope", None),
                },
                "lr": {
                    "ls_comp": getattr(getattr(candidate.step6, "lr", None), "ls_comp", None),
                    "ls_rbd": getattr(getattr(candidate.step6, "lr", None), "ls_rbd", None),
                    "hs_comp": getattr(getattr(candidate.step6, "lr", None), "hs_comp", None),
                    "hs_rbd": getattr(getattr(candidate.step6, "lr", None), "hs_rbd", None),
                    "hs_slope": getattr(getattr(candidate.step6, "lr", None), "hs_slope", None),
                },
                "rr": {
                    "ls_comp": getattr(getattr(candidate.step6, "rr", None), "ls_comp", None),
                    "ls_rbd": getattr(getattr(candidate.step6, "rr", None), "ls_rbd", None),
                    "hs_comp": getattr(getattr(candidate.step6, "rr", None), "hs_comp", None),
                    "hs_rbd": getattr(getattr(candidate.step6, "rr", None), "hs_rbd", None),
                    "hs_slope": getattr(getattr(candidate.step6, "rr", None), "hs_slope", None),
                },
                "c_hs_front": getattr(candidate.step6, "c_hs_front", None),
                "c_hs_rear": getattr(candidate.step6, "c_hs_rear", None),
            },
            "supporting": {
                "brake_bias_pct": getattr(candidate.supporting, "brake_bias_pct", None),
                "brake_bias_target": getattr(candidate.supporting, "brake_bias_target", None),
                "brake_bias_migration": getattr(candidate.supporting, "brake_bias_migration", None),
                "front_master_cyl_mm": getattr(candidate.supporting, "front_master_cyl_mm", None),
                "rear_master_cyl_mm": getattr(candidate.supporting, "rear_master_cyl_mm", None),
                "pad_compound": getattr(candidate.supporting, "pad_compound", None),
                "diff_preload_nm": getattr(candidate.supporting, "diff_preload_nm", None),
                "diff_ramp_option_idx": getattr(candidate.supporting, "diff_ramp_option_idx", None),
                "diff_ramp_angles": getattr(candidate.supporting, "diff_ramp_angles", None),
                "diff_ramp_coast": getattr(candidate.supporting, "diff_ramp_coast", None),
                "diff_ramp_drive": getattr(candidate.supporting, "diff_ramp_drive", None),
                "diff_clutch_plates": getattr(candidate.supporting, "diff_clutch_plates", None),
                "tc_gain": getattr(candidate.supporting, "tc_gain", None),
                "tc_slip": getattr(candidate.supporting, "tc_slip", None),
                "fuel_l": getattr(candidate.supporting, "fuel_l", None),
                "fuel_low_warning_l": getattr(candidate.supporting, "fuel_low_warning_l", None),
                "fuel_target_l": getattr(candidate.supporting, "fuel_target_l", None),
                "gear_stack": getattr(candidate.supporting, "gear_stack", None),
                "roof_light_color": getattr(candidate.supporting, "roof_light_color", None),
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
