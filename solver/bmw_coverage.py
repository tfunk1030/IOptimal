from __future__ import annotations

from typing import Any

from analyzer.telemetry_truth import get_signal
from car_model.setup_registry import (
    diff_ramp_option_index,
    diff_ramp_string_for_option,
    get_car_spec,
    get_field,
    iter_fields,
)


BMW_LOCAL_REFINE_FIELDS: set[str] = {
    "front_heave_perch_mm",
    "rear_third_perch_mm",
    "rear_spring_perch_mm",
}

BMW_DETERMINISTIC_CONTEXT_FIELDS: set[str] = {
    "fuel_l",
    "fuel_low_warning_l",
    "fuel_target_l",
    "gear_stack",
    "roof_light_color",
    "brake_bias_migration_gain",
    "hybrid_rear_drive_enabled",
    "hybrid_rear_drive_corner_pct",
}

BMW_COMPUTED_DISPLAY_FIELDS: set[str] = {
    "diff_ramp_angles",
}

BMW_SIGNAL_REQUIREMENTS: dict[str, list[str]] = {
    "wing_angle_deg": [
        "mean_front_rh_at_speed_mm",
        "mean_rear_rh_at_speed_mm",
        "front_rh_std_mm",
        "rear_rh_std_mm",
        "splitter_rh_p01_mm",
    ],
    "front_pushrod_offset_mm": [
        "mean_front_rh_at_speed_mm",
        "front_rh_std_mm",
        "splitter_rh_p01_mm",
    ],
    "rear_pushrod_offset_mm": [
        "mean_rear_rh_at_speed_mm",
        "rear_rh_std_mm",
        "rear_power_slip_ratio_p95",
    ],
    "front_heave_spring_nmm": [
        "front_heave_travel_used_pct",
        "bottoming_event_count_front_clean",
        "front_rh_std_mm",
        "splitter_rh_p01_mm",
    ],
    "front_heave_perch_mm": [
        "front_heave_travel_used_pct",
        "bottoming_event_count_front_clean",
        "front_rh_std_mm",
    ],
    "rear_third_spring_nmm": [
        "rear_heave_travel_used_pct",
        "bottoming_event_count_rear_clean",
        "rear_rh_std_mm",
        "rear_power_slip_ratio_p95",
    ],
    "rear_third_perch_mm": [
        "rear_heave_travel_used_pct",
        "bottoming_event_count_rear_clean",
        "rear_rh_std_mm",
    ],
    "front_torsion_od_mm": [
        "understeer_low_speed_deg",
        "body_slip_p95_deg",
        "front_carcass_mean_c",
        "front_pressure_mean_kpa",
    ],
    "rear_spring_rate_nmm": [
        "rear_power_slip_ratio_p95",
        "understeer_low_speed_deg",
        "rear_carcass_mean_c",
        "rear_pressure_mean_kpa",
    ],
    "rear_spring_perch_mm": [
        "rear_power_slip_ratio_p95",
        "rear_rh_std_mm",
        "rear_carcass_mean_c",
    ],
    "front_arb_size": [
        "understeer_low_speed_deg",
        "understeer_high_speed_deg",
        "lltd_measured",
        "roll_gradient_measured_deg_per_g",
    ],
    "front_arb_blade": [
        "understeer_low_speed_deg",
        "understeer_high_speed_deg",
        "lltd_measured",
        "roll_gradient_measured_deg_per_g",
    ],
    "rear_arb_size": [
        "rear_power_slip_ratio_p95",
        "body_slip_p95_deg",
        "lltd_measured",
        "roll_gradient_measured_deg_per_g",
    ],
    "rear_arb_blade": [
        "rear_power_slip_ratio_p95",
        "body_slip_p95_deg",
        "lltd_measured",
        "roll_gradient_measured_deg_per_g",
    ],
    "front_camber_deg": [
        "understeer_mean_deg",
        "front_carcass_mean_c",
        "front_pressure_mean_kpa",
    ],
    "rear_camber_deg": [
        "rear_power_slip_ratio_p95",
        "rear_carcass_mean_c",
        "rear_pressure_mean_kpa",
    ],
    "front_toe_mm": [
        "understeer_mean_deg",
        "yaw_rate_correlation",
        "body_slip_p95_deg",
    ],
    "rear_toe_mm": [
        "rear_power_slip_ratio_p95",
        "yaw_rate_correlation",
        "body_slip_p95_deg",
    ],
    "front_ls_comp": [
        "front_rh_settle_time_ms",
        "front_shock_oscillation_hz",
        "front_shock_vel_p99_mps",
        "front_rh_std_mm",
    ],
    "front_ls_rbd": [
        "front_rh_settle_time_ms",
        "front_shock_oscillation_hz",
        "front_shock_vel_p99_mps",
        "front_rh_std_mm",
    ],
    "front_hs_comp": [
        "front_rh_settle_time_ms",
        "front_shock_oscillation_hz",
        "front_shock_vel_p99_mps",
        "front_rh_std_hs_mm",
    ],
    "front_hs_rbd": [
        "front_rh_settle_time_ms",
        "front_shock_oscillation_hz",
        "front_shock_vel_p99_mps",
        "front_rh_std_hs_mm",
    ],
    "front_hs_slope": [
        "front_rh_settle_time_ms",
        "front_shock_oscillation_hz",
        "front_shock_vel_p99_mps",
        "front_rh_std_hs_mm",
    ],
    "rear_ls_comp": [
        "rear_rh_settle_time_ms",
        "rear_shock_oscillation_hz",
        "rear_shock_vel_p99_mps",
        "rear_rh_std_mm",
    ],
    "rear_ls_rbd": [
        "rear_rh_settle_time_ms",
        "rear_shock_oscillation_hz",
        "rear_shock_vel_p99_mps",
        "rear_rh_std_mm",
    ],
    "rear_hs_comp": [
        "rear_rh_settle_time_ms",
        "rear_shock_oscillation_hz",
        "rear_shock_vel_p99_mps",
        "rear_rh_std_mm",
    ],
    "rear_hs_rbd": [
        "rear_rh_settle_time_ms",
        "rear_shock_oscillation_hz",
        "rear_shock_vel_p99_mps",
        "rear_rh_std_mm",
    ],
    "rear_hs_slope": [
        "rear_rh_settle_time_ms",
        "rear_shock_oscillation_hz",
        "rear_shock_vel_p99_mps",
        "rear_rh_std_mm",
    ],
    "brake_bias_pct": [
        "front_braking_lock_ratio_p95",
        "hydraulic_brake_split_pct",
        "pitch_range_braking_deg",
        "pitch_mean_braking_deg",
        "abs_active_pct",
    ],
    "brake_bias_target": [
        "front_braking_lock_ratio_p95",
        "hydraulic_brake_split_pct",
        "pitch_range_braking_deg",
        "abs_active_pct",
    ],
    "brake_bias_migration": [
        "front_braking_lock_ratio_p95",
        "hydraulic_brake_split_pct",
        "pitch_range_braking_deg",
        "abs_active_pct",
    ],
    "front_master_cyl_mm": [
        "front_braking_lock_ratio_p95",
        "hydraulic_brake_split_pct",
        "pitch_range_braking_deg",
        "abs_active_pct",
    ],
    "rear_master_cyl_mm": [
        "front_braking_lock_ratio_p95",
        "hydraulic_brake_split_pct",
        "pitch_range_braking_deg",
        "abs_active_pct",
    ],
    "pad_compound": [
        "front_braking_lock_ratio_p95",
        "hydraulic_brake_split_pct",
        "pitch_range_braking_deg",
        "abs_active_pct",
    ],
    "diff_preload_nm": [
        "rear_power_slip_ratio_p95",
        "understeer_low_speed_deg",
        "body_slip_p95_deg",
    ],
    "diff_ramp_option_idx": [
        "rear_power_slip_ratio_p95",
        "understeer_low_speed_deg",
        "body_slip_p95_deg",
    ],
    "diff_ramp_angles": [
        "rear_power_slip_ratio_p95",
        "understeer_low_speed_deg",
        "body_slip_p95_deg",
    ],
    "diff_clutch_plates": [
        "rear_power_slip_ratio_p95",
        "understeer_low_speed_deg",
        "body_slip_p95_deg",
    ],
    "tc_gain": [
        "rear_power_slip_ratio_p95",
        "body_slip_p95_deg",
    ],
    "tc_slip": [
        "rear_power_slip_ratio_p95",
        "body_slip_p95_deg",
    ],
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _car_name(car: Any | None) -> str:
    if isinstance(car, str):
        return car.lower()
    return str(getattr(car, "canonical_name", "bmw") or "bmw").lower()


def _arb_size_index(labels: list[str] | tuple[str, ...] | None, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value)))
    options = [str(item) for item in (labels or ())]
    text = str(value)
    if text in options:
        return options.index(text)
    return None


def parameter_classification(field_name: str) -> str:
    if field_name in BMW_LOCAL_REFINE_FIELDS:
        return "local_refine"
    if field_name in BMW_DETERMINISTIC_CONTEXT_FIELDS:
        return "deterministic_context"
    if field_name in BMW_COMPUTED_DISPLAY_FIELDS:
        return "computed_display"
    return "search"


def required_signals_for_field(field_name: str) -> list[str]:
    return list(BMW_SIGNAL_REQUIREMENTS.get(field_name, []))


def bmw_coverage_fields() -> list[str]:
    fields = [
        field.canonical_key
        for field in iter_fields(kind="settable")
        if get_car_spec("bmw", field.canonical_key) is not None and field.canonical_key != "front_diff_preload_nm"
    ]
    if get_car_spec("bmw", "roof_light_color") is not None and "roof_light_color" not in fields:
        fields.append("roof_light_color")
    return fields


def current_setup_value(field_name: str, current_setup: Any, *, car_name: str = "bmw") -> Any:
    if current_setup is None:
        return None
    if field_name == "front_pushrod_offset_mm":
        return getattr(current_setup, "front_pushrod_mm", None)
    if field_name == "rear_pushrod_offset_mm":
        return getattr(current_setup, "rear_pushrod_mm", None)
    if field_name == "front_heave_spring_nmm":
        return getattr(current_setup, "front_heave_nmm", None)
    if field_name == "rear_third_spring_nmm":
        return getattr(current_setup, "rear_third_nmm", None)
    if field_name == "rear_spring_rate_nmm":
        return getattr(current_setup, "rear_spring_nmm", None)
    if field_name == "front_arb_blade":
        return getattr(current_setup, "front_arb_blade", None)
    if field_name == "rear_arb_blade":
        return getattr(current_setup, "rear_arb_blade", None)
    if field_name in {
        "front_ls_comp",
        "front_ls_rbd",
        "front_hs_comp",
        "front_hs_rbd",
        "front_hs_slope",
        "rear_ls_comp",
        "rear_ls_rbd",
        "rear_hs_comp",
        "rear_hs_rbd",
        "rear_hs_slope",
    }:
        return getattr(current_setup, field_name, None)
    if field_name == "diff_ramp_option_idx":
        return diff_ramp_option_index(
            car_name,
            diff_ramp_angles=getattr(current_setup, "diff_ramp_angles", None),
            default=1,
        )
    return getattr(current_setup, field_name, None)


def solved_value(
    field_name: str,
    *,
    car: Any | None = None,
    wing: Any | None = None,
    current_setup: Any | None = None,
    step1: Any | None = None,
    step2: Any | None = None,
    step3: Any | None = None,
    step4: Any | None = None,
    step5: Any | None = None,
    step6: Any | None = None,
    supporting: Any | None = None,
) -> Any:
    car_name = _car_name(car)
    if field_name == "wing_angle_deg":
        return wing if wing is not None else getattr(current_setup, "wing_angle_deg", None)
    if field_name == "front_pushrod_offset_mm":
        return getattr(step1, "front_pushrod_offset_mm", None)
    if field_name == "rear_pushrod_offset_mm":
        return getattr(step1, "rear_pushrod_offset_mm", None)
    if field_name == "front_heave_spring_nmm":
        return getattr(step2, "front_heave_nmm", None)
    if field_name == "front_heave_perch_mm":
        return getattr(step2, "perch_offset_front_mm", None)
    if field_name == "rear_third_spring_nmm":
        return getattr(step2, "rear_third_nmm", None)
    if field_name == "rear_third_perch_mm":
        return getattr(step2, "perch_offset_rear_mm", None)
    if field_name == "front_torsion_od_mm":
        return getattr(step3, "front_torsion_od_mm", None)
    if field_name == "rear_spring_rate_nmm":
        return getattr(step3, "rear_spring_rate_nmm", None)
    if field_name == "rear_spring_perch_mm":
        return getattr(step3, "rear_spring_perch_mm", None)
    if field_name == "front_arb_size":
        return getattr(step4, "front_arb_size", None)
    if field_name == "front_arb_blade":
        return getattr(step4, "front_arb_blade_start", None)
    if field_name == "rear_arb_size":
        return getattr(step4, "rear_arb_size", None)
    if field_name == "rear_arb_blade":
        return getattr(step4, "rear_arb_blade_start", None)
    if field_name == "front_camber_deg":
        return getattr(step5, "front_camber_deg", None)
    if field_name == "rear_camber_deg":
        return getattr(step5, "rear_camber_deg", None)
    if field_name == "front_toe_mm":
        return getattr(step5, "front_toe_mm", None)
    if field_name == "rear_toe_mm":
        return getattr(step5, "rear_toe_mm", None)
    if field_name == "front_ls_comp":
        return getattr(getattr(step6, "lf", None), "ls_comp", None)
    if field_name == "front_ls_rbd":
        return getattr(getattr(step6, "lf", None), "ls_rbd", None)
    if field_name == "front_hs_comp":
        return getattr(getattr(step6, "lf", None), "hs_comp", None)
    if field_name == "front_hs_rbd":
        return getattr(getattr(step6, "lf", None), "hs_rbd", None)
    if field_name == "front_hs_slope":
        return getattr(getattr(step6, "lf", None), "hs_slope", None)
    if field_name == "rear_ls_comp":
        return getattr(getattr(step6, "lr", None), "ls_comp", None)
    if field_name == "rear_ls_rbd":
        return getattr(getattr(step6, "lr", None), "ls_rbd", None)
    if field_name == "rear_hs_comp":
        return getattr(getattr(step6, "lr", None), "hs_comp", None)
    if field_name == "rear_hs_rbd":
        return getattr(getattr(step6, "lr", None), "hs_rbd", None)
    if field_name == "rear_hs_slope":
        return getattr(getattr(step6, "lr", None), "hs_slope", None)
    if field_name == "diff_ramp_option_idx":
        option_idx = getattr(supporting, "diff_ramp_option_idx", None)
        if option_idx is not None:
            return _safe_int(option_idx, 1)
        return diff_ramp_option_index(
            car_name,
            coast=getattr(supporting, "diff_ramp_coast", None),
            drive=getattr(supporting, "diff_ramp_drive", None),
            diff_ramp_angles=getattr(supporting, "diff_ramp_angles", None),
            default=1,
        )
    if field_name == "diff_ramp_angles":
        option_idx = solved_value(
            "diff_ramp_option_idx",
            car=car,
            current_setup=current_setup,
            supporting=supporting,
        )
        if option_idx is None:
            option_idx = current_setup_value("diff_ramp_option_idx", current_setup, car_name=car_name)
        if option_idx is not None:
            return diff_ramp_string_for_option(
                car_name,
                option_idx,
                ferrari_label=car_name == "ferrari",
            )
        return getattr(supporting, "diff_ramp_angles", None)
    candidate = getattr(supporting, field_name, None)
    if candidate is not None:
        return candidate
    return current_setup_value(field_name, current_setup, car_name=car_name)


def _search_metadata_for_field(
    field_name: str,
    *,
    step3: Any | None,
    step4: Any | None,
    step5: Any | None,
    supporting: Any | None,
) -> tuple[str, list[str]]:
    for container in (step3, step4, step5, supporting):
        if container is None:
            continue
        status_map = getattr(container, "parameter_search_status", {}) or {}
        evidence_map = getattr(container, "parameter_search_evidence", {}) or {}
        if field_name in status_map:
            return str(status_map[field_name]), list(evidence_map.get(field_name, []))
    return "not_searched", []


def build_search_baseline(
    *,
    car: Any,
    wing: Any,
    current_setup: Any | None = None,
    step1: Any,
    step2: Any,
    step3: Any,
    step4: Any,
    step5: Any,
    step6: Any,
    supporting: Any,
) -> dict[str, Any]:
    front_arb_labels = list(getattr(getattr(car, "arb", None), "front_size_labels", []) or [])
    rear_arb_labels = list(getattr(getattr(car, "arb", None), "rear_size_labels", []) or [])
    return {
        "wing_angle_deg": wing,
        "front_pushrod_offset_mm": getattr(step1, "front_pushrod_offset_mm", None),
        "rear_pushrod_offset_mm": getattr(step1, "rear_pushrod_offset_mm", None),
        "front_heave_spring_nmm": getattr(step2, "front_heave_nmm", None),
        "rear_third_spring_nmm": getattr(step2, "rear_third_nmm", None),
        "front_torsion_od_mm": getattr(step3, "front_torsion_od_mm", None),
        "rear_spring_rate_nmm": getattr(step3, "rear_spring_rate_nmm", None),
        "front_arb_blade": getattr(step4, "front_arb_blade_start", None),
        "rear_arb_blade": getattr(step4, "rear_arb_blade_start", None),
        "front_arb_size": _arb_size_index(front_arb_labels, getattr(step4, "front_arb_size", None)),
        "rear_arb_size": _arb_size_index(rear_arb_labels, getattr(step4, "rear_arb_size", None)),
        "front_camber_deg": getattr(step5, "front_camber_deg", None),
        "rear_camber_deg": getattr(step5, "rear_camber_deg", None),
        "front_toe_mm": getattr(step5, "front_toe_mm", None),
        "rear_toe_mm": getattr(step5, "rear_toe_mm", None),
        "front_ls_comp": getattr(getattr(step6, "lf", None), "ls_comp", None),
        "front_ls_rbd": getattr(getattr(step6, "lf", None), "ls_rbd", None),
        "front_hs_comp": getattr(getattr(step6, "lf", None), "hs_comp", None),
        "front_hs_rbd": getattr(getattr(step6, "lf", None), "hs_rbd", None),
        "front_hs_slope": getattr(getattr(step6, "lf", None), "hs_slope", None),
        "rear_ls_comp": getattr(getattr(step6, "lr", None), "ls_comp", None),
        "rear_ls_rbd": getattr(getattr(step6, "lr", None), "ls_rbd", None),
        "rear_hs_comp": getattr(getattr(step6, "lr", None), "hs_comp", None),
        "rear_hs_rbd": getattr(getattr(step6, "lr", None), "hs_rbd", None),
        "rear_hs_slope": getattr(getattr(step6, "lr", None), "hs_slope", None),
        "brake_bias_pct": getattr(supporting, "brake_bias_pct", None),
        "diff_preload_nm": getattr(supporting, "diff_preload_nm", None),
        "diff_ramp_option_idx": solved_value(
            "diff_ramp_option_idx",
            car=car,
            current_setup=current_setup,
            supporting=supporting,
        ),
        "diff_clutch_plates": getattr(supporting, "diff_clutch_plates", None),
        "tc_gain": getattr(supporting, "tc_gain", None),
        "tc_slip": getattr(supporting, "tc_slip", None),
    }


def build_parameter_coverage(
    *,
    car: Any,
    wing: Any | None,
    current_setup: Any,
    step1: Any,
    step2: Any,
    step3: Any,
    step4: Any,
    step5: Any,
    step6: Any,
    supporting: Any,
) -> dict[str, dict[str, Any]]:
    coverage: dict[str, dict[str, Any]] = {}
    for field_name in bmw_coverage_fields():
        field_def = get_field(field_name)
        classification = parameter_classification(field_name)
        current_value = current_setup_value(field_name, current_setup, car_name=_car_name(car))
        proposed_value = solved_value(
            field_name,
            car=car,
            wing=wing,
            current_setup=current_setup,
            step1=step1,
            step2=step2,
            step3=step3,
            step4=step4,
            step5=step5,
            step6=step6,
            supporting=supporting,
        )
        search_status, search_evidence = _search_metadata_for_field(
            field_name,
            step3=step3,
            step4=step4,
            step5=step5,
            supporting=supporting,
        )
        if classification not in {"search", "local_refine"}:
            search_status = ""
            search_evidence = []
        coverage[field_name] = {
            "classification": classification,
            "unit": getattr(field_def, "unit", ""),
            "solver_step": getattr(field_def, "solver_step", None),
            "kind": getattr(field_def, "kind", ""),
            "current_value": current_value,
            "proposed_value": proposed_value,
            "changed": current_value != proposed_value,
            "search_status": search_status,
            "search_evidence": search_evidence,
        }
    return coverage


def build_telemetry_coverage(*, measured: Any) -> dict[str, dict[str, Any]]:
    coverage: dict[str, dict[str, Any]] = {}
    for field_name in bmw_coverage_fields():
        signal_names = required_signals_for_field(field_name)
        signals = {name: get_signal(measured, name).to_dict() for name in signal_names}
        usable = sum(
            1
            for name in signal_names
            if get_signal(measured, name).usable(allow_proxy=True)
        )
        coverage[field_name] = {
            "classification": parameter_classification(field_name),
            "required_signals": signal_names,
            "usable_signals": usable,
            "coverage_ratio": round(usable / len(signal_names), 3) if signal_names else 1.0,
            "signals": signals,
        }
    return coverage
