"""Runtime loaders for published calibration artifacts.

These helpers are intentionally tolerant: if a model file is missing or
malformed, runtime falls back to the built-in car definitions.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from calibration.models import FittedModelArtifact, SetupSchemaFile
from car_model.garage import GarageOutputModel


RUNTIME_ROOT = Path(__file__).resolve().parent.parent / "data" / "calibration" / "models"
SCHEMA_ROOT = Path(__file__).resolve().parent.parent / "data" / "setup_schema"

TRACK_ALIAS_FALLBACKS: dict[str, tuple[str, ...]] = {
    "hockenheimring_baden_württemberg": ("hockenheim",),
    "sebring_international_raceway": ("sebring",),
    "silverstone_circuit": ("silverstone",),
}


def _track_slug(track_name: str | None) -> str:
    if not track_name:
        return "global"
    return (
        str(track_name)
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
        .replace(".", "")
    )


def _candidate_track_slugs(track_name: str | None) -> list[str]:
    primary = _track_slug(track_name)
    if primary == "global":
        return ["global"]
    seen: set[str] = {primary}
    candidates: list[str] = [primary]

    for alias in TRACK_ALIAS_FALLBACKS.get(primary, ()):
        alias_slug = _track_slug(alias)
        if alias_slug not in seen:
            candidates.append(alias_slug)
            seen.add(alias_slug)

    # Generic fallback: first token of slug (e.g. "hockenheimring_..." -> "hockenheimring").
    # Helps absorb verbose track names while keeping deterministic order.
    first_token = primary.split("_", 1)[0]
    if first_token and first_token not in seen:
        candidates.append(first_token)
        seen.add(first_token)

    # Additional generic fallback for common long-form names ending in "ring".
    if first_token.endswith("ring") and len(first_token) > 4:
        ring_base = first_token[:-4]
        if ring_base and ring_base not in seen:
            candidates.append(ring_base)
            seen.add(ring_base)

    return candidates


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@lru_cache(maxsize=64)
def load_setup_schema(car_name: str) -> SetupSchemaFile | None:
    path = SCHEMA_ROOT / f"{car_name}.json"
    payload = _load_json(path)
    if payload is None:
        return None
    try:
        return SetupSchemaFile.from_dict(payload)
    except Exception:
        return None


@lru_cache(maxsize=256)
def load_fitted_model(car_name: str, track_name: str | None, model_filename: str) -> FittedModelArtifact | None:
    for track_slug in _candidate_track_slugs(track_name):
        path = RUNTIME_ROOT / car_name / track_slug / model_filename
        payload = _load_json(path)
        if payload is None:
            continue
        try:
            return FittedModelArtifact.from_dict(payload)
        except Exception:
            continue
    return None


def load_support_tier(car_name: str, track_name: str | None) -> str | None:
    for track_slug in _candidate_track_slugs(track_name):
        payload = _load_json(RUNTIME_ROOT / car_name / track_slug / "support_tier.json")
        if payload is None:
            continue
        tier = payload.get("support_tier")
        if tier is not None:
            return str(tier)
    return None


def _allows_runtime_model_override(car_name: str, track_name: str | None) -> bool:
    """Gate runtime model overrides by published support tier."""
    tier = (load_support_tier(car_name, track_name) or "").strip().lower()
    # Unsupported tiers should not override built-in car models, because sparse
    # artifacts can produce degenerate intercept-only (or zero) models.
    return tier in {"partial", "calibrated"}


def telemetry_model_corrections(car_name: str, track_name: str | None) -> dict[str, float]:
    """Extract bounded additive predictor corrections from a published telemetry model."""
    if not _allows_runtime_model_override(car_name, track_name):
        return {}
    artifact = load_fitted_model(car_name, track_name, "telemetry_model.json")
    if artifact is None:
        return {}
    models = dict(artifact.parameters.get("models") or {})
    corrections: dict[str, float] = {}
    mapping = {
        "front_heave_travel_used_pct": "front_heave_travel_used_pct",
        "front_rh_excursion_measured_mm": "front_excursion_mm",
        "rear_rh_std_mm": "rear_rh_std_mm",
        "pitch_range_braking_deg": "braking_pitch_deg",
        "front_braking_lock_ratio_p95": "front_lock_p95",
        "rear_power_slip_ratio_p95": "rear_power_slip_p95",
        "body_slip_p95_deg": "body_slip_p95_deg",
        "understeer_low_speed_deg": "understeer_low_deg",
        "understeer_high_speed_deg": "understeer_high_deg",
    }
    for source_key, predictor_key in mapping.items():
        model = dict(models.get(source_key) or {})
        intercept = model.get("intercept")
        if intercept is None:
            continue
        try:
            corrections[predictor_key] = float(intercept)
        except (TypeError, ValueError):
            continue
    return corrections


def load_runtime_telemetry_model(car_name: str, track_name: str | None) -> dict[str, float]:
    """Public runtime API for published telemetry-model predictor corrections."""
    return telemetry_model_corrections(car_name, track_name)


def load_runtime_ride_height_model(car_name: str, track_name: str | None):
    if not _allows_runtime_model_override(car_name, track_name):
        return None
    artifact = load_fitted_model(car_name, track_name, "ride_height_model.json")
    if artifact is None:
        return None
    from car_model.cars import RideHeightModel
    front = dict(artifact.parameters.get("front") or {})
    rear = dict(artifact.parameters.get("rear") or {})
    front_coeffs = dict(front.get("coefficients") or {})
    rear_coeffs = dict(rear.get("coefficients") or {})
    return RideHeightModel(
        front_intercept=float(front.get("intercept") or 30.0),
        front_coeff_heave_nmm=float(
            front_coeffs.get("front_heave_spring_nmm")
            or front_coeffs.get("front_heave_setting_index")
            or front_coeffs.get("front_heave_nmm")
            or 0.0
        ),
        front_coeff_camber_deg=float(front_coeffs.get("front_camber_deg") or 0.0),
        rear_intercept=float(rear.get("intercept") or 0.0),
        rear_coeff_pushrod=float(
            rear_coeffs.get("rear_pushrod_offset_mm")
            or rear_coeffs.get("rear_pushrod_mm")
            or 0.0
        ),
        rear_coeff_third_nmm=float(
            rear_coeffs.get("rear_third_spring_nmm")
            or rear_coeffs.get("rear_heave_setting_index")
            or rear_coeffs.get("rear_third_nmm")
            or 0.0
        ),
        rear_coeff_rear_spring=float(
            rear_coeffs.get("rear_spring_rate_nmm")
            or rear_coeffs.get("rear_torsion_bar_index")
            or rear_coeffs.get("rear_spring_nmm")
            or 0.0
        ),
        rear_coeff_heave_perch=float(
            rear_coeffs.get("front_heave_perch_mm")
            or rear_coeffs.get("front_heave_perch")
            or 0.0
        ),
        rear_coeff_fuel_l=float(rear_coeffs.get("fuel_l") or 0.0),
        rear_coeff_spring_perch=float(rear_coeffs.get("rear_spring_perch_mm") or 0.0),
        rear_loo_rmse_mm=float(artifact.metrics.get("rear_rmse") or 0.0),
        front_loo_rmse_mm=float(artifact.metrics.get("front_rmse") or 0.0),
    )


def load_runtime_garage_model(car_name: str, track_name: str | None, *, fallback_name: str | None = None) -> GarageOutputModel | None:
    if not _allows_runtime_model_override(car_name, track_name):
        return None
    artifact = load_fitted_model(car_name, track_name, "garage_model.json")
    if artifact is None:
        return None
    models = dict(artifact.parameters.get("models") or {})
    front = dict(models.get("static_front_rh_mm") or {})
    rear = dict(models.get("static_rear_rh_mm") or {})
    torsion = dict(models.get("torsion_bar_turns") or {})
    heave_static = dict(models.get("heave_spring_defl_static_mm") or {})
    heave_max = dict(models.get("heave_spring_defl_max_mm") or {})
    slider_static = dict(models.get("heave_slider_defl_static_mm") or {})
    slider_max = dict(models.get("heave_slider_defl_max_mm") or {})

    front_coeffs = dict(front.get("coefficients") or {})
    rear_coeffs = dict(rear.get("coefficients") or {})
    torsion_coeffs = dict(torsion.get("coefficients") or {})
    heave_coeffs = dict(heave_static.get("coefficients") or {})
    slider_coeffs = dict(slider_static.get("coefficients") or {})

    def _co(dct: dict[str, Any], *keys: str) -> float:
        for key in keys:
            if key in dct and dct[key] is not None:
                return float(dct[key])
        return 0.0

    return GarageOutputModel(
        name=fallback_name or f"{car_name} {_track_slug(track_name)} runtime garage truth",
        track_keywords=(str(track_name).lower(),) if track_name else tuple(),
        front_intercept=float(front.get("intercept") or 0.0),
        front_coeff_pushrod=_co(front_coeffs, "front_pushrod_offset_mm", "front_pushrod_mm"),
        front_coeff_heave_nmm=_co(front_coeffs, "front_heave_spring_nmm", "front_heave_setting_index", "front_heave_nmm"),
        front_coeff_heave_perch_mm=_co(front_coeffs, "front_heave_perch_mm"),
        front_coeff_torsion_od_mm=_co(front_coeffs, "front_torsion_od_mm", "front_torsion_bar_index"),
        front_coeff_camber_deg=_co(front_coeffs, "front_camber_deg"),
        front_coeff_fuel_l=_co(front_coeffs, "fuel_l"),
        rear_intercept=float(rear.get("intercept") or 0.0),
        rear_coeff_pushrod=_co(rear_coeffs, "rear_pushrod_offset_mm", "rear_pushrod_mm"),
        rear_coeff_third_nmm=_co(rear_coeffs, "rear_third_spring_nmm", "rear_heave_setting_index", "rear_third_nmm"),
        rear_coeff_third_perch_mm=_co(rear_coeffs, "rear_third_perch_mm", "rear_heave_perch_mm"),
        rear_coeff_rear_spring_nmm=_co(rear_coeffs, "rear_spring_rate_nmm", "rear_torsion_bar_index", "rear_spring_nmm"),
        rear_coeff_rear_spring_perch_mm=_co(rear_coeffs, "rear_spring_perch_mm"),
        rear_coeff_front_heave_perch_mm=_co(rear_coeffs, "front_heave_perch_mm"),
        rear_coeff_fuel_l=_co(rear_coeffs, "fuel_l"),
        torsion_turns_intercept=float(torsion.get("intercept") or 0.0),
        torsion_turns_coeff_heave_nmm=_co(torsion_coeffs, "front_heave_spring_nmm", "front_heave_setting_index", "front_heave_nmm"),
        torsion_turns_coeff_heave_perch_mm=_co(torsion_coeffs, "front_heave_perch_mm"),
        torsion_turns_coeff_torsion_od_mm=_co(torsion_coeffs, "front_torsion_od_mm", "front_torsion_bar_index"),
        torsion_turns_coeff_front_rh_mm=_co(torsion_coeffs, "static_front_rh_mm"),
        heave_defl_intercept=float(heave_static.get("intercept") or 0.0),
        heave_defl_coeff_heave_nmm=_co(heave_coeffs, "front_heave_spring_nmm", "front_heave_setting_index", "front_heave_nmm"),
        heave_defl_coeff_heave_perch_mm=_co(heave_coeffs, "front_heave_perch_mm"),
        heave_defl_coeff_torsion_od_mm=_co(heave_coeffs, "front_torsion_od_mm", "front_torsion_bar_index"),
        heave_defl_coeff_front_pushrod_mm=_co(heave_coeffs, "front_pushrod_offset_mm", "front_pushrod_mm"),
        heave_defl_coeff_front_rh_mm=_co(heave_coeffs, "static_front_rh_mm"),
        heave_spring_defl_max_intercept_mm=float(heave_max.get("intercept") or 0.0),
        heave_spring_defl_max_slope=_co(
            dict(heave_max.get("coefficients") or {}),
            "front_heave_spring_nmm", "front_heave_setting_index", "front_heave_nmm",
        ),
        slider_intercept=float(slider_static.get("intercept") or 0.0),
        slider_coeff_heave_nmm=_co(slider_coeffs, "front_heave_spring_nmm", "front_heave_setting_index", "front_heave_nmm"),
        slider_coeff_heave_perch_mm=_co(slider_coeffs, "front_heave_perch_mm"),
        slider_coeff_torsion_od_mm=_co(slider_coeffs, "front_torsion_od_mm", "front_torsion_bar_index"),
        slider_coeff_front_pushrod_mm=_co(slider_coeffs, "front_pushrod_offset_mm", "front_pushrod_mm"),
        slider_coeff_front_rh_mm=_co(slider_coeffs, "static_front_rh_mm"),
        max_slider_mm=float(slider_max.get("intercept") or 45.0) if slider_max else 45.0,
    )


# Backward-compatible aliases for internal callers built during implementation.
build_runtime_ride_height_model = load_runtime_ride_height_model
build_runtime_garage_model = load_runtime_garage_model
build_runtime_telemetry_model = load_runtime_telemetry_model
