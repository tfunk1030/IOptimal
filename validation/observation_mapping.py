"""Shared observation-to-canonical normalization for validation and calibration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from car_model.setup_registry import diff_ramp_option_index, diff_ramp_string_for_option


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return int(default)
        return int(round(float(value)))
    except (TypeError, ValueError):
        return int(default)


def _pick(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping.get(key) not in (None, ""):
            return mapping.get(key)
    return default


def _avg_damper(
    dampers: Mapping[str, Any],
    left_key: str,
    right_key: str,
    field: str,
    default: float,
) -> float:
    left = _float((dampers.get(left_key) or {}).get(field), default)
    right = _float((dampers.get(right_key) or {}).get(field), default)
    return round((left + right) / 2.0, 4)


def normalize_setup_to_canonical_params(
    setup: Mapping[str, Any],
    *,
    car: str | None = None,
) -> dict[str, float | int | str]:
    """Normalize an observation setup payload onto canonical registry field names."""
    adapter_name = str(car or setup.get("adapter_name") or "bmw").strip().lower()
    dampers = setup.get("dampers", {}) or {}

    params: dict[str, float | int | str] = {
        "wing_angle_deg": _float(_pick(setup, "wing_angle_deg", "wing"), 17.0),
        "front_heave_spring_nmm": _float(_pick(setup, "front_heave_spring_nmm", "front_heave_nmm"), 50.0),
        "rear_third_spring_nmm": _float(_pick(setup, "rear_third_spring_nmm", "rear_third_nmm"), 450.0),
        "rear_spring_rate_nmm": _float(_pick(setup, "rear_spring_rate_nmm", "rear_spring_nmm"), 160.0),
        "front_torsion_od_mm": _float(_pick(setup, "front_torsion_od_mm", "torsion_bar_od_mm"), 14.34),
        "front_pushrod_offset_mm": _float(_pick(setup, "front_pushrod_offset_mm", "front_pushrod"), -26.0),
        "rear_pushrod_offset_mm": _float(_pick(setup, "rear_pushrod_offset_mm", "rear_pushrod"), -22.0),
        "front_rh_static_mm": _float(_pick(setup, "front_rh_static_mm", "front_rh_static"), 0.0),
        "rear_rh_static_mm": _float(_pick(setup, "rear_rh_static_mm", "rear_rh_static"), 0.0),
        "front_camber_deg": _float(setup.get("front_camber_deg"), -2.9),
        "rear_camber_deg": _float(setup.get("rear_camber_deg"), -1.9),
        "front_toe_mm": _float(setup.get("front_toe_mm"), -0.4),
        "rear_toe_mm": _float(setup.get("rear_toe_mm"), 0.0),
        "front_arb_size": str(_pick(setup, "front_arb_size", default="Soft") or "Soft"),
        "rear_arb_size": str(_pick(setup, "rear_arb_size", default="Medium") or "Medium"),
        "front_arb_blade": _int(_pick(setup, "front_arb_blade"), 1),
        "rear_arb_blade": _int(_pick(setup, "rear_arb_blade"), 3),
        "front_ls_comp": _avg_damper(dampers, "lf", "rf", "ls_comp", 7.0),
        "front_ls_rbd": _avg_damper(dampers, "lf", "rf", "ls_rbd", 7.0),
        "front_hs_comp": _avg_damper(dampers, "lf", "rf", "hs_comp", 5.0),
        "front_hs_rbd": _avg_damper(dampers, "lf", "rf", "hs_rbd", 5.0),
        "front_hs_slope": _avg_damper(dampers, "lf", "rf", "hs_slope", 10.0),
        "rear_ls_comp": _avg_damper(dampers, "lr", "rr", "ls_comp", 6.0),
        "rear_ls_rbd": _avg_damper(dampers, "lr", "rr", "ls_rbd", 7.0),
        "rear_hs_comp": _avg_damper(dampers, "lr", "rr", "hs_comp", 5.0),
        "rear_hs_rbd": _avg_damper(dampers, "lr", "rr", "hs_rbd", 8.0),
        "rear_hs_slope": _avg_damper(dampers, "lr", "rr", "hs_slope", 10.0),
        "brake_bias_pct": _float(setup.get("brake_bias_pct"), 50.0),
        "brake_bias_target": _float(setup.get("brake_bias_target"), 0.0),
        "brake_bias_migration": _float(setup.get("brake_bias_migration"), 0.0),
        "front_master_cyl_mm": _float(setup.get("front_master_cyl_mm"), 19.1),
        "rear_master_cyl_mm": _float(setup.get("rear_master_cyl_mm"), 19.1),
        "pad_compound": str(setup.get("pad_compound") or "Medium"),
        "diff_preload_nm": _float(setup.get("diff_preload_nm"), 20.0),
        "diff_clutch_plates": _int(setup.get("diff_clutch_plates"), 6),
        "tc_gain": _int(setup.get("tc_gain"), 4),
        "tc_slip": _int(setup.get("tc_slip"), 4),
        "fuel_l": _float(_pick(setup, "fuel_l", "fuel_level_l"), 0.0),
        "fuel_low_warning_l": _float(setup.get("fuel_low_warning_l"), 0.0),
        "fuel_target_l": _float(setup.get("fuel_target_l"), 0.0),
        "gear_stack": str(setup.get("gear_stack") or ""),
        "roof_light_color": str(setup.get("roof_light_color") or ""),
    }

    ramp_idx = diff_ramp_option_index(
        adapter_name,
        coast=setup.get("diff_ramp_coast"),
        drive=setup.get("diff_ramp_drive"),
        diff_ramp_angles=_pick(setup, "diff_ramp_angles", "diff_ramp_label"),
        default=1,
    )
    if ramp_idx is not None:
        params["diff_ramp_option_idx"] = int(ramp_idx)
        params["diff_ramp_angles"] = diff_ramp_string_for_option(
            adapter_name,
            ramp_idx,
            ferrari_label=adapter_name == "ferrari",
        )

    return params


SIGNAL_FALLBACKS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "front_heave_travel_used_pct": (("front_heave_travel_used_pct",), ()),
    # front_excursion_mm fallback hierarchy (in priority order):
    #   1. front_rh_excursion_measured_mm  — DIRECT: p99 deviation from at-speed mean RH (best)
    #   2. front_rh_std_mm                 — FALLBACK tier-1: at-speed RH std (~3× scale, same family)
    #   3. front_heave_defl_p99_mm         — FALLBACK tier-2: spring deflection proxy (different physics)
    # front_rh_std_mm is preferred over heave_defl because it comes from the same RH channel
    # family. Scale differs (~3:1 vs excursion) but Spearman rank correlation is preserved.
    # Reduces "missing" rate from ~24% → ~3% on the current BMW/Sebring corpus.
    "front_excursion_mm": (
        ("front_rh_excursion_measured_mm",),
        ("front_rh_std_mm", "front_heave_defl_p99_mm"),
    ),
    "rear_rh_std_mm": (("rear_rh_std_mm",), ()),
    "braking_pitch_deg": (("pitch_range_braking_deg",), ("pitch_range_deg",)),
    "front_lock_p95": (("front_braking_lock_ratio_p95",), ("front_brake_pressure_peak_bar",)),
    "rear_power_slip_p95": (("rear_power_slip_ratio_p95",), ("tc_intervention_pct",)),
    "body_slip_p95_deg": (("body_slip_p95_deg",), ()),
    "understeer_low_deg": (("understeer_low_speed_deg",), ("understeer_mean_deg",)),
    "understeer_high_deg": (("understeer_high_speed_deg",), ("understeer_mean_deg",)),
    "front_pressure_hot_kpa": (("front_pressure_mean_kpa",), ("lf_pressure_kpa", "rf_pressure_kpa")),
    "rear_pressure_hot_kpa": (("rear_pressure_mean_kpa",), ("lr_pressure_kpa", "rr_pressure_kpa")),
}

# Calibrated scale factors for fallback signals.
#
# When a fallback field is used in place of the primary signal, the raw value
# is multiplied by this factor to bring it onto the same absolute scale as the
# primary signal.  Spearman rank correlation is scale-invariant, so these
# factors do NOT affect validation rankings — they correct absolute values used
# in penalty calculations (e.g. excursion > 12 mm → bottoming risk).
#
# Derivation (BMW Sebring corpus, n=33 observations with both signals present):
#
#   braking_pitch_deg / pitch_range_deg:
#     mean = 0.756, median = 0.734.  Using 0.75 (rounded median).
#     Physical basis: pitch excursion during braking events is a subset of the
#     full-lap pitch range; cornering and kerb strikes inflate the full range.
#
#   front_excursion_mm / front_rh_std_mm:
#     mean = 3.011, median = 3.000.  Using 3.0.
#     Physical basis: p99 excursion ≈ 3σ for a roughly Gaussian RH distribution
#     at speed (central limit theorem + constant-radius cornering assumption).
#     Already documented in comments above but was never applied in code.
#
# Format: (metric, fallback_field) → scale_factor
FALLBACK_SCALE_FACTORS: dict[tuple[str, str], float] = {
    # braking pitch: pitch_range_deg is ~33% larger than pitch_range_braking_deg
    ("braking_pitch_deg", "pitch_range_deg"): 0.75,
    # front excursion: front_rh_std_mm is p99 ÷ 3 of the true excursion
    ("front_excursion_mm", "front_rh_std_mm"): 3.0,
}


def resolve_validation_signals(telemetry: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Resolve direct vs fallback signal coverage for validation reporting.

    Returns a dict keyed by metric name.  Each entry has:
      - "value"  : float or None — the resolved value (scaled when fallback)
      - "source" : "direct" | "fallback" | "missing"
      - "fields" : list[str] — telemetry field(s) used

    Scale correction:
      When a fallback field is used, FALLBACK_SCALE_FACTORS is checked for a
      (metric, fallback_field) entry.  If found, the raw telemetry value is
      multiplied by that factor before being stored.  This ensures absolute
      values are on the same scale as the primary signal, which matters for
      penalty calculations in objective.py even though Spearman rank correlation
      is scale-invariant.
    """
    resolved: dict[str, dict[str, Any]] = {}
    for metric, (primary_paths, fallback_paths) in SIGNAL_FALLBACKS.items():
        value = None
        source = "missing"
        used_fields: list[str] = []

        for field in primary_paths:
            raw = telemetry.get(field)
            if raw not in (None, ""):
                value = _float(raw, 0.0)
                source = "direct"
                used_fields = [field]
                break

        if source == "missing":
            for field in fallback_paths:
                raw = telemetry.get(field)
                if raw not in (None, ""):
                    raw_val = _float(raw, 0.0)
                    scale = FALLBACK_SCALE_FACTORS.get((metric, field), 1.0)
                    value = round(raw_val * scale, 4)
                    source = "fallback"
                    used_fields = [field]
                    break

        if source == "missing" and len(fallback_paths) > 1:
            scaled_values = []
            for field in fallback_paths:
                raw = telemetry.get(field)
                if raw not in (None, ""):
                    raw_val = _float(raw, 0.0)
                    scale = FALLBACK_SCALE_FACTORS.get((metric, field), 1.0)
                    scaled_values.append(raw_val * scale)
            if scaled_values:
                value = round(sum(scaled_values) / len(scaled_values), 4)
                source = "fallback"
                used_fields = list(fallback_paths)

        resolved[metric] = {
            "value": value,
            "source": source,
            "fields": used_fields,
        }
    return resolved
