"""Automatic calibration engine for IOptimal car models.

This module accumulates setup data from IBT sessions and automatically fits
calibration models that replace BMW ESTIMATE defaults with per-car ground truth.

Key insight: The IBT session info YAML contains:
  1. All garage-settable parameters (spring indices, damper clicks, pushrods, etc.)
  2. ALL iRacing-computed display values as ground truth (corner weights, ride
     heights, deflections, torsion bar turns, shock deflections, etc.)

These two layers together are sufficient to calibrate ALL regression models.
No .sto decryption, no garage screenshots, no manual data entry required.

SPRING INDEX → N/mm LOOKUP (Ferrari/Acura only)
  For indexed spring cars, the heave spring index→N/mm mapping is NOT directly
  available in the IBT YAML. Two calibration paths are supported:

  Path A (automatic, slower): Multiple IBT sessions varying heave spring index.
    We infer k = effective_load / heave_defl_static from corner weights + heave
    deflection display values. Requires 3+ sessions at different heave indices.

  Path B (instant, requires external tool): Provide a setupdelta.com JSON dump
    containing fSideSpringRateNpm / rSideSpringRateNpm fields (the decrypted
    physics rates). Run:
      python -m car_model.auto_calibrate --car ferrari --sto-json setup.json

Usage:
    # Auto-calibrate from a directory of IBT files:
    python -m car_model.auto_calibrate --car ferrari --ibt-dir data/telemetry/

    # Calibrate from explicit IBT file list:
    python -m car_model.auto_calibrate --car ferrari --ibt s1.ibt s2.ibt s3.ibt

    # Add setupdelta.com decrypted JSON for spring lookup (optional but precise):
    python -m car_model.auto_calibrate --car ferrari --sto-json decoded.json

    # Check calibration status:
    python -m car_model.auto_calibrate --car ferrari --status

    # Apply calibrated models to solver automatically (called by produce.py):
    from car_model.auto_calibrate import load_calibrated_models
    load_calibrated_models(car)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_CALIBRATION_DIR = PROJECT_ROOT / "data" / "calibration"
_MIN_SESSIONS_FOR_FIT = 5   # absolute minimum unique-setup sessions before fitting


def _min_sessions_for_features(n_features: int) -> int:
    """Scale the minimum session count with feature count.

    Rule of thumb: at least 3x the number of features, with a floor of 5.
    A 6-feature model needs at least 18 sessions to avoid overfitting.
    """
    return max(_MIN_SESSIONS_FOR_FIT, 3 * n_features)
_MIN_SESSIONS_FOR_SPRING_LOOKUP = 3  # sessions at different heave indices


def _setup_key(pt) -> tuple:
    """Canonical setup key for uniqueness detection.

    Includes ALL parameters that affect static ride heights, deflections,
    corner weights, and roll stiffness. Used consistently across fitting,
    status, CLI, and protocol to avoid counting discrepancies.

    Parameters included:
    - Springs: front heave, rear third, front torsion OD/index, rear spring
    - Perches: front heave perch, rear third perch, rear spring perch
    - Geometry: front/rear pushrod, front/rear camber
    - ARB: front/rear size (string) and blade (integer) — affect roll stiffness
      and must be distinguished for accurate regression coverage
    - Load: fuel level
    """
    return (
        round(pt.front_heave_setting, 1),
        round(pt.rear_third_setting, 1),
        round(pt.front_heave_perch_mm, 1),
        round(pt.rear_third_perch_mm, 1),
        round(pt.front_torsion_od_mm, 3),
        round(pt.rear_spring_setting, 1),
        round(pt.rear_spring_perch_mm, 1),
        round(pt.front_pushrod_mm, 1),
        round(pt.rear_pushrod_mm, 1),
        round(pt.front_camber_deg, 1),
        round(pt.rear_camber_deg, 1),
        round(pt.fuel_l, 0),
        # ARB configuration — affects roll stiffness, LLTD, and lateral load transfer.
        # Must be part of the fingerprint so sessions with different ARB settings
        # (same springs/pushrods) count as distinct calibration points.
        str(pt.front_arb_size or ""),
        int(pt.front_arb_blade or 0),
        str(pt.rear_arb_size or ""),
        int(pt.rear_arb_blade or 0),
    )

# Alias for backward compatibility
_setup_fingerprint = _setup_key


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CalibrationPoint:
    """Setup + computed values extracted from one IBT session.

    All inputs come from IBT session info YAML (always available).
    The spring index→N/mm physics rate is optional (requires decrypted .sto).
    """
    session_id: str = ""
    ibt_path: str = ""
    track: str = ""
    timestamp: str = ""

    # ── Garage settings (what the user configured) ──
    wing_deg: float = 0.0
    fuel_l: float = 0.0
    front_heave_setting: float = 0.0    # N/mm (BMW) or index (Ferrari/Acura)
    rear_third_setting: float = 0.0     # N/mm or index
    front_heave_perch_mm: float = 0.0
    rear_third_perch_mm: float = 0.0
    front_torsion_od_mm: float = 0.0   # OD mm (BMW/Acura) or index (Ferrari)
    rear_spring_setting: float = 0.0   # N/mm (BMW) or OD index (Ferrari/Acura)
    rear_spring_perch_mm: float = 0.0
    front_pushrod_mm: float = 0.0
    rear_pushrod_mm: float = 0.0
    front_camber_deg: float = 0.0
    rear_camber_deg: float = 0.0
    front_arb_size: str = ""
    rear_arb_size: str = ""
    front_arb_blade: int = 0
    rear_arb_blade: int = 0

    # ── iRacing-computed display values (ground truth from IBT for calibration) ──
    static_front_rh_mm: float = 0.0
    static_rear_rh_mm: float = 0.0
    torsion_bar_turns: float = 0.0
    rear_torsion_bar_turns: float = 0.0
    torsion_bar_defl_mm: float = 0.0
    rear_torsion_bar_defl_mm: float = 0.0
    front_shock_defl_static_mm: float = 0.0
    front_shock_defl_max_mm: float = 0.0
    rear_shock_defl_static_mm: float = 0.0
    rear_shock_defl_max_mm: float = 0.0
    heave_spring_defl_static_mm: float = 0.0
    heave_spring_defl_max_mm: float = 0.0
    heave_slider_defl_static_mm: float = 0.0
    heave_slider_defl_max_mm: float = 0.0
    rear_spring_defl_static_mm: float = 0.0
    rear_spring_defl_max_mm: float = 0.0
    third_spring_defl_static_mm: float = 0.0
    third_spring_defl_max_mm: float = 0.0
    third_slider_defl_static_mm: float = 0.0
    third_slider_defl_max_mm: float = 0.0
    lf_corner_weight_n: float = 0.0
    rf_corner_weight_n: float = 0.0
    lr_corner_weight_n: float = 0.0
    rr_corner_weight_n: float = 0.0
    aero_df_balance_pct: float = 0.0
    aero_ld_ratio: float = 0.0
    front_rh_at_speed_mm: float = 0.0
    rear_rh_at_speed_mm: float = 0.0

    # ── Telemetry outcomes (from IBT time-series) ──
    dynamic_front_rh_mm: float = 0.0
    dynamic_rear_rh_mm: float = 0.0
    front_sigma_mm: float = 0.0
    rear_sigma_mm: float = 0.0
    front_shock_vel_p99_mps: float = 0.0
    rear_shock_vel_p99_mps: float = 0.0
    lap_time_s: float = 0.0
    assessment: str = ""

    # ── Roll/LLTD telemetry (for ARB and roll gain calibration) ──
    roll_gradient_deg_per_g: float = 0.0
    lltd_measured: float = 0.0

    # ── Optional: decrypted .sto physics rates (from setupdelta.com) ──
    # When available, these give EXACT N/mm rates for indexed spring cars.
    front_side_spring_rate_nmm: float = 0.0   # from fSideSpringRateNpm / 1000
    rear_side_spring_rate_nmm: float = 0.0    # from rSideSpringRateNpm / 1000
    front_heave_rate_nmm: float = 0.0         # front heave spring physics rate
    rear_heave_rate_nmm: float = 0.0          # rear heave/third physics rate


@dataclass
class FittedModel:
    """Fitted regression coefficients for one calibration model."""
    name: str
    feature_names: list[str]
    coefficients: list[float]   # [intercept, beta_1, beta_2, ...]
    r_squared: float = 0.0
    rmse: float = 0.0
    loo_rmse: float = 0.0
    n_samples: int = 0
    is_calibrated: bool = True


@dataclass
class SpringLookupTable:
    """Maps garage index/setting → physical spring rate N/mm."""
    setting_key: str              # e.g., "front_heave_index", "front_torsion_od"
    entries: list[dict] = field(default_factory=list)
    # Each entry: {"setting": float, "rate_nmm": float, "source": str}
    is_calibrated: bool = False
    method: str = "unknown"       # "decrypted_sto", "physics_inference", "estimate"


@dataclass
class CarCalibrationModels:
    """All fitted calibration models for one car."""
    car: str
    n_sessions: int = 0
    n_unique_setups: int = 0
    calibration_complete: bool = False

    # Regression models (fitted from IBT data)
    front_ride_height: FittedModel | None = None
    rear_ride_height: FittedModel | None = None
    torsion_bar_turns: FittedModel | None = None
    torsion_bar_defl: FittedModel | None = None
    front_shock_defl_static: FittedModel | None = None
    front_shock_defl_max: FittedModel | None = None
    rear_shock_defl_static: FittedModel | None = None
    rear_shock_defl_max: FittedModel | None = None
    heave_spring_defl_static: FittedModel | None = None
    heave_spring_defl_max: FittedModel | None = None
    heave_slider_defl_static: FittedModel | None = None
    rear_spring_defl_static: FittedModel | None = None
    rear_spring_defl_max: FittedModel | None = None
    third_spring_defl_static: FittedModel | None = None
    third_spring_defl_max: FittedModel | None = None
    third_slider_defl_static: FittedModel | None = None

    # Spring lookup tables (for indexed spring cars)
    front_heave_lookup: SpringLookupTable | None = None
    rear_heave_lookup: SpringLookupTable | None = None
    front_torsion_lookup: SpringLookupTable | None = None
    rear_torsion_lookup: SpringLookupTable | None = None

    # Effective mass calibration
    m_eff_front_kg: float | None = None
    m_eff_rear_kg: float | None = None
    m_eff_is_rate_dependent: bool = False
    m_eff_rate_table: list[dict] = field(default_factory=list)          # front (legacy name)
    m_eff_rear_rate_table: list[dict] = field(default_factory=list)     # rear
    # Each entry: {"setting": float, "m_eff_kg": float}

    # Calibrated LLTD target.
    # Deprecated: proxy-derived LLTD targets are no longer persisted or applied.
    measured_lltd_target: float | None = None

    # Damper zeta targets (from fastest sessions)
    front_ls_zeta: float | None = None
    rear_ls_zeta: float | None = None
    front_hs_zeta: float | None = None
    rear_hs_zeta: float | None = None
    zeta_n_sessions: int = 0

    # Aero compression (Model 3) — from IBT AeroCalculator (ground truth)
    aero_front_compression_mm: float | None = None
    aero_rear_compression_mm: float | None = None
    aero_n_sessions: int = 0

    # Calibration status per component
    status: dict[str, str] = field(default_factory=dict)
    # e.g., {"deflection_model": "calibrated (R²=0.93)", "spring_lookup": "partial (3/9 indices)"}


# ─────────────────────────────────────────────────────────────────────────────
# Calibration data I/O
# ─────────────────────────────────────────────────────────────────────────────

def _data_dir(car: str) -> Path:
    d = _CALIBRATION_DIR / car
    d.mkdir(parents=True, exist_ok=True)
    return d


def _points_path(car: str) -> Path:
    return _data_dir(car) / "calibration_points.json"


def _models_path(car: str) -> Path:
    return _data_dir(car) / "models.json"


def load_calibration_points(car: str) -> list[CalibrationPoint]:
    p = _points_path(car)
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    points = []
    for d in raw:
        # Use only fields that exist in the dataclass
        known = {k: v for k, v in d.items() if k in CalibrationPoint.__dataclass_fields__}
        points.append(CalibrationPoint(**known))
    return points


def save_calibration_points(car: str, points: list[CalibrationPoint]) -> None:
    p = _points_path(car)
    with open(p, "w", encoding="utf-8") as f:
        json.dump([asdict(pt) for pt in points], f, indent=2)


def load_calibrated_models(car: str) -> CarCalibrationModels | None:
    """Load fitted models. Returns None if no calibration data exists."""
    p = _models_path(car)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    # Legacy migrations: proxy-derived LLTD targets must not override curated
    # car definitions. Keep the status note for provenance, but clear the value.
    if raw.get("status", {}).get("lltd_target", "").startswith("DISABLED"):
        raw["measured_lltd_target"] = None
    return _dict_to_models(raw)


def save_calibrated_models(car: str, models: CarCalibrationModels) -> None:
    p = _models_path(car)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(_models_to_dict(models), f, indent=2)


def _models_to_dict(m: CarCalibrationModels) -> dict:
    d = asdict(m)
    return d


def _dict_to_models(d: dict) -> CarCalibrationModels:
    """Reconstruct CarCalibrationModels from a plain dict."""

    def _to_fitted(raw: dict | None) -> FittedModel | None:
        if raw is None:
            return None
        return FittedModel(**{k: v for k, v in raw.items() if k in FittedModel.__dataclass_fields__})

    def _to_lookup(raw: dict | None) -> SpringLookupTable | None:
        if raw is None:
            return None
        return SpringLookupTable(**{k: v for k, v in raw.items() if k in SpringLookupTable.__dataclass_fields__})

    regression_keys = [
        "front_ride_height", "rear_ride_height", "torsion_bar_turns", "torsion_bar_defl",
        "front_shock_defl_static", "front_shock_defl_max",
        "rear_shock_defl_static", "rear_shock_defl_max",
        "heave_spring_defl_static", "heave_spring_defl_max",
        "heave_slider_defl_static",
        "rear_spring_defl_static", "rear_spring_defl_max",
        "third_spring_defl_static", "third_spring_defl_max",
        "third_slider_defl_static",
    ]
    lookup_keys = ["front_heave_lookup", "rear_heave_lookup", "front_torsion_lookup", "rear_torsion_lookup"]

    kwargs: dict[str, Any] = {}
    for k, v in d.items():
        if k in regression_keys:
            kwargs[k] = _to_fitted(v)
        elif k in lookup_keys:
            kwargs[k] = _to_lookup(v)
        elif k in CarCalibrationModels.__dataclass_fields__:
            kwargs[k] = v

    return CarCalibrationModels(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# IBT extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_point_from_ibt(ibt_path: str | Path, car_name: str = "") -> CalibrationPoint | None:
    """Extract a CalibrationPoint from an IBT file.

    Reads ALL data from the IBT session info YAML (setup + computed values)
    and from the telemetry channels (measured outcomes).
    """
    from track_model.ibt_parser import IBTFile
    from analyzer.setup_reader import CurrentSetup

    try:
        ibt = IBTFile(str(ibt_path))
        setup = CurrentSetup.from_ibt(ibt, car_canonical=car_name or None)
    except Exception as e:
        print(f"  [skip] {Path(ibt_path).name}: {e}", file=sys.stderr)
        return None

    # Skip sessions with no valid calibration data
    if setup.lf_corner_weight_n <= 0:
        return None
    if setup.front_heave_nmm <= 0 and setup.raw_indexed_fields.get("front_heave_index", 0) <= 0:
        return None

    # Extract session identity
    ti = ibt.track_info()
    track = ti.get("track_name", "unknown")

    # Use raw indexed fields for Ferrari/Acura; N/mm values for BMW/Cadillac
    raw = setup.raw_indexed_fields
    front_heave_setting = raw.get("front_heave_index", setup.front_heave_nmm)
    rear_heave_setting = raw.get("rear_heave_index", setup.rear_third_nmm)
    front_torsion_setting = raw.get("front_torsion_bar_index", setup.front_torsion_od_mm)
    rear_spring_setting = raw.get("rear_torsion_bar_index", setup.rear_spring_nmm)

    # Extract telemetry outcomes
    dynamic_front_rh = 0.0
    dynamic_rear_rh = 0.0
    front_sigma = 0.0
    rear_sigma = 0.0
    front_shock_p99 = 0.0
    rear_shock_p99 = 0.0
    lap_time = 0.0

    try:
        from analyzer.extract import extract_measurements
        measured = extract_measurements(str(ibt_path), _get_dummy_car(car_name))
        dynamic_front_rh = measured.mean_front_rh_at_speed_mm or 0.0
        dynamic_rear_rh = measured.mean_rear_rh_at_speed_mm or 0.0
        front_sigma = measured.front_rh_std_mm or 0.0
        rear_sigma = measured.rear_rh_std_mm or 0.0
        front_shock_p99 = measured.front_shock_vel_p99_mps or 0.0
        rear_shock_p99 = measured.rear_shock_vel_p99_mps or 0.0
        lap_time = measured.lap_time_s or 0.0
        roll_grad = getattr(measured, "roll_gradient_measured_deg_per_g", None) or 0.0
        lltd_m = getattr(measured, "lltd_measured", None) or 0.0
    except Exception as e:
        # Telemetry extraction is optional for calibration; skip gracefully
        import logging
        logging.getLogger(__name__).debug("Telemetry extraction skipped: %s", e)
        roll_grad = 0.0
        lltd_m = 0.0

    import hashlib
    session_id = hashlib.sha256((str(ibt_path) + str(Path(ibt_path).stat().st_mtime)).encode()).hexdigest()[:16]

    return CalibrationPoint(
        session_id=session_id,
        ibt_path=str(ibt_path),
        track=track,
        wing_deg=setup.wing_angle_deg,
        fuel_l=setup.fuel_l,
        front_heave_setting=front_heave_setting,
        rear_third_setting=rear_heave_setting,
        front_heave_perch_mm=setup.front_heave_perch_mm,
        rear_third_perch_mm=setup.rear_third_perch_mm,
        front_torsion_od_mm=float(front_torsion_setting),
        rear_spring_setting=float(rear_spring_setting),
        rear_spring_perch_mm=setup.rear_spring_perch_mm,
        front_pushrod_mm=setup.front_pushrod_mm,
        rear_pushrod_mm=setup.rear_pushrod_mm,
        front_camber_deg=setup.front_camber_deg,
        rear_camber_deg=setup.rear_camber_deg,
        front_arb_size=setup.front_arb_size,
        rear_arb_size=setup.rear_arb_size,
        front_arb_blade=setup.front_arb_blade,
        rear_arb_blade=setup.rear_arb_blade,
        # iRacing-computed ground truth (from IBT YAML)
        static_front_rh_mm=setup.static_front_rh_mm,
        static_rear_rh_mm=setup.static_rear_rh_mm,
        torsion_bar_turns=setup.torsion_bar_turns,
        rear_torsion_bar_turns=setup.rear_torsion_bar_turns,
        torsion_bar_defl_mm=setup.torsion_bar_defl_mm,
        rear_torsion_bar_defl_mm=setup.rear_torsion_bar_defl_mm,
        front_shock_defl_static_mm=setup.front_shock_defl_static_mm,
        front_shock_defl_max_mm=setup.front_shock_defl_max_mm,
        rear_shock_defl_static_mm=setup.rear_shock_defl_static_mm,
        rear_shock_defl_max_mm=setup.rear_shock_defl_max_mm,
        heave_spring_defl_static_mm=setup.heave_spring_defl_static_mm,
        heave_spring_defl_max_mm=setup.heave_spring_defl_max_mm,
        heave_slider_defl_static_mm=setup.heave_slider_defl_static_mm,
        heave_slider_defl_max_mm=setup.heave_slider_defl_max_mm,
        rear_spring_defl_static_mm=setup.rear_spring_defl_static_mm,
        rear_spring_defl_max_mm=setup.rear_spring_defl_max_mm,
        third_spring_defl_static_mm=setup.third_spring_defl_static_mm,
        third_spring_defl_max_mm=setup.third_spring_defl_max_mm,
        third_slider_defl_static_mm=setup.third_slider_defl_static_mm,
        third_slider_defl_max_mm=setup.third_slider_defl_max_mm,
        lf_corner_weight_n=setup.lf_corner_weight_n,
        rf_corner_weight_n=setup.rf_corner_weight_n,
        lr_corner_weight_n=setup.lr_corner_weight_n,
        rr_corner_weight_n=setup.rr_corner_weight_n,
        aero_df_balance_pct=setup.df_balance_pct,
        aero_ld_ratio=setup.ld_ratio,
        front_rh_at_speed_mm=setup.front_rh_at_speed_mm,
        rear_rh_at_speed_mm=setup.rear_rh_at_speed_mm,
        # Telemetry outcomes
        dynamic_front_rh_mm=dynamic_front_rh,
        dynamic_rear_rh_mm=dynamic_rear_rh,
        front_sigma_mm=front_sigma,
        rear_sigma_mm=rear_sigma,
        front_shock_vel_p99_mps=front_shock_p99,
        rear_shock_vel_p99_mps=rear_shock_p99,
        lap_time_s=lap_time,
        roll_gradient_deg_per_g=roll_grad,
        lltd_measured=lltd_m,
    )


def _get_dummy_car(car_name: str):
    """Get a minimal car object for telemetry extraction."""
    try:
        from car_model.cars import get_car
        return get_car(car_name or "bmw")
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("Could not load car model '%s': %s", car_name, e)
        return None


def ingest_sto_json(car: str, sto_json_path: str | Path) -> dict[str, float]:
    """Parse physics rate fields from a setupdelta.com decrypted .sto JSON.

    The JSON should be the array of rows from setupdelta.com's analysis,
    as in ferrari.json. Returns a dict of field→value for internal physics fields
    AND extracted garage settings (for auto-detecting torsion bar indices).

    Useful fields extracted:
        fSideSpringRateNpm  → front corner spring rate (N/m, ÷1000 = N/mm)
        rSideSpringRateNpm  → rear corner spring rate (N/m, ÷1000 = N/mm)
        _front_torsion_od_setting  → front Torsion bar O.D. garage index (auto-detected)
        _rear_torsion_od_setting   → rear Torsion bar O.D. garage index (auto-detected)
        _front_heave_setting       → front Heave spring garage index (auto-detected)
        _rear_heave_setting        → rear Heave spring garage index (auto-detected)
    """
    with open(sto_json_path, encoding="utf-8") as f:
        data = json.load(f)

    # Handle both formats: array of rows OR {carName, rows} object
    rows = data if isinstance(data, list) else data.get("rows", [])

    result: dict[str, float] = {}

    # Track whether we've seen the front vs rear Torsion bar / Heave spring rows
    _front_torsion_seen = False
    _rear_torsion_seen = False
    _front_heave_seen = False
    _rear_heave_seen = False

    for row in rows:
        label = row.get("label", "")
        section = row.get("section", "") or ""
        tab = row.get("tab", "") or ""
        is_mapped = row.get("is_mapped", True)
        val = row.get("metric_value") or row.get("value")

        # ── Internal physics fields (unmapped, lowercase starting with f/r/h) ──
        if label and label[0] in ("f", "r", "h") and not is_mapped and val is not None:
            try:
                result[label] = float(str(val).split()[0])
            except (ValueError, IndexError):
                pass

        # ── Auto-detect garage settings from mapped rows ──
        if is_mapped and val is not None:
            try:
                numeric_val = float(str(val).split()[0])
            except (ValueError, IndexError):
                numeric_val = None

            if label == "Torsion bar O.D." and numeric_val is not None:
                # First occurrence = Front (Left Front), second = Rear (Left Rear)
                front_sections = {"Left Front", "Right Front", "Front"}
                rear_sections = {"Left Rear", "Right Rear", "Rear"}
                if section in front_sections and not _front_torsion_seen:
                    result["_front_torsion_od_setting"] = numeric_val
                    _front_torsion_seen = True
                elif (section in rear_sections or _front_torsion_seen) and not _rear_torsion_seen:
                    result["_rear_torsion_od_setting"] = numeric_val
                    _rear_torsion_seen = True

            elif label == "Heave spring" and numeric_val is not None:
                front_sections = {"Left Front", "Right Front", "Front"}
                rear_sections = {"Left Rear", "Right Rear", "Rear"}
                if (section in front_sections or not _front_heave_seen) and not _front_heave_seen:
                    result["_front_heave_setting"] = numeric_val
                    _front_heave_seen = True
                elif not _rear_heave_seen:
                    result["_rear_heave_setting"] = numeric_val
                    _rear_heave_seen = True

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Regression fitting
# ─────────────────────────────────────────────────────────────────────────────

def _fit(X: np.ndarray, y: np.ndarray, feature_names: list[str], model_name: str) -> FittedModel:
    """Fit y = X @ beta via least squares with LOO cross-validation."""
    ones = np.ones((X.shape[0], 1))
    X_aug = np.hstack([ones, X])

    # Guard: underdetermined system (more parameters than samples) produces
    # meaningless R² = 1.0 and unstable coefficients. Require n > n_params.
    n_params = X_aug.shape[1]  # features + intercept
    if X.shape[0] <= n_params:
        import logging
        logging.getLogger(__name__).warning(
            "Model '%s': underdetermined (%d samples, %d parameters) — "
            "marking as uncalibrated",
            model_name, X.shape[0], n_params,
        )
        return FittedModel(
            name=model_name,
            feature_names=feature_names,
            coefficients=[0.0] * n_params,
            r_squared=0.0,
            rmse=float("inf"),
            loo_rmse=float("inf"),
            n_samples=X.shape[0],
            is_calibrated=False,
        )

    beta, *_ = np.linalg.lstsq(X_aug, y, rcond=None)
    y_pred = X_aug @ beta
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = float(1.0 - ss_res / max(ss_tot, 1e-12))
    rmse = float(np.sqrt(ss_res / len(y)))

    # Leave-one-out CV
    n = len(y)
    loo_errors = np.zeros(n)
    if n >= 5:
        for i in range(n):
            mask = np.ones(n, dtype=bool)
            mask[i] = False
            X_train, y_train = X_aug[mask], y[mask]
            b, *_ = np.linalg.lstsq(X_train, y_train, rcond=None)
            loo_errors[i] = y[i] - X_aug[i] @ b
    # LOO RMSE: use NaN when skipped (n<5) to avoid misleading "0.0 = perfect" display
    if n < 5:
        loo_rmse = float("nan")
    else:
        loo_rmse = float(np.sqrt(np.mean(loo_errors ** 2)))

    # A model is only considered calibrated if its R² meets the gate threshold.
    # Writing an under-threshold model to disk would let it appear "calibrated" on
    # the next load before the gate re-checks. Guard here prevents that.
    from car_model.calibration_gate import R2_THRESHOLD_BLOCK
    is_cal = r2 >= R2_THRESHOLD_BLOCK

    # Overfit warning: if LOO RMSE is more than 2x training RMSE, the model
    # may not generalize. Also warn if sample-to-feature ratio is below 3:1.
    n_features = X.shape[1]
    _overfit_warnings: list[str] = []
    if loo_rmse > 2.0 * max(rmse, 1e-6) and n >= 5:
        _overfit_warnings.append(
            f"LOO RMSE ({loo_rmse:.3f}) > 2x training RMSE ({rmse:.3f}) — "
            f"possible overfit"
        )
    if n < _min_sessions_for_features(n_features):
        _overfit_warnings.append(
            f"Only {n} samples for {n_features} features (recommend "
            f"{_min_sessions_for_features(n_features)}+)"
        )
    if _overfit_warnings:
        import logging
        _logger = logging.getLogger(__name__)
        for w in _overfit_warnings:
            _logger.warning("Model '%s': %s", model_name, w)

    return FittedModel(
        name=model_name,
        feature_names=feature_names,
        coefficients=beta.tolist(),
        r_squared=r2,
        rmse=rmse,
        loo_rmse=loo_rmse,
        n_samples=n,
        is_calibrated=is_cal,
    )


def _col(rows: list[dict], key: str) -> np.ndarray:
    return np.array([r[key] for r in rows], dtype=float)


def _select_features(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: list[str],
    max_features: int | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Forward feature selection to prevent overfitting on small datasets.

    When n_samples < 3 * n_features, selects a subset of features via
    greedy forward selection using leave-one-out RMSE as the criterion.

    Returns (X_reduced, names_reduced).  If no reduction is needed the
    inputs are returned unchanged.
    """
    n_samples, n_features = X.shape
    if max_features is None:
        max_features = max(1, n_samples // 3)
    if n_features <= max_features:
        return X, feature_names

    # Forward selection: start empty, greedily add the feature that gives
    # the best LOO RMSE at each step.
    selected: list[int] = []
    remaining = list(range(n_features))

    for _ in range(max_features):
        best_idx = -1
        best_loo = float("inf")
        for idx in remaining:
            trial = selected + [idx]
            X_trial = X[:, trial]
            ones = np.ones((n_samples, 1))
            X_aug = np.hstack([ones, X_trial])
            n_params = X_aug.shape[1]
            if n_samples <= n_params:
                continue
            # Compute LOO RMSE
            loo_sq = 0.0
            for i in range(n_samples):
                mask = np.ones(n_samples, dtype=bool)
                mask[i] = False
                b, *_ = np.linalg.lstsq(X_aug[mask], y[mask], rcond=None)
                pred_i = X_aug[i] @ b
                loo_sq += (y[i] - pred_i) ** 2
            loo_rmse = float(np.sqrt(loo_sq / n_samples))
            if loo_rmse < best_loo:
                best_loo = loo_rmse
                best_idx = idx
        if best_idx < 0:
            break
        selected.append(best_idx)
        remaining.remove(best_idx)

    if not selected:
        return X, feature_names

    import logging
    _logger = logging.getLogger(__name__)
    selected_names = [feature_names[i] for i in selected]
    dropped = [feature_names[i] for i in range(n_features) if i not in selected]
    _logger.info(
        "Feature selection: %d→%d features (kept %s, dropped %s)",
        n_features, len(selected), selected_names, dropped,
    )

    return X[:, selected], selected_names


# ─────────────────────────────────────────────────────────────────────────────
# Spring lookup table construction
# ─────────────────────────────────────────────────────────────────────────────

def build_spring_lookup_from_sto_json(
    car: str, sto_json: dict[str, float], front_setting: float, rear_setting: float
) -> tuple[SpringLookupTable, SpringLookupTable]:
    """Build front/rear spring lookup tables from a decrypted .sto JSON.

    The JSON should contain fSideSpringRateNpm and rSideSpringRateNpm
    (from setupdelta.com decode). We build one data point from this session.
    """
    front_rate_nmm = sto_json.get("fSideSpringRateNpm", 0.0) / 1000.0
    rear_rate_nmm = sto_json.get("rSideSpringRateNpm", 0.0) / 1000.0

    # Load existing lookup to accumulate
    existing_models = load_calibrated_models(car)
    front_lookup = (existing_models.front_torsion_lookup if existing_models else None) or SpringLookupTable(
        setting_key="front_torsion_od", method="decrypted_sto"
    )
    rear_lookup = (existing_models.rear_torsion_lookup if existing_models else None) or SpringLookupTable(
        setting_key="rear_torsion_od", method="decrypted_sto"
    )

    # Add this data point if not already present
    existing_f = {e["setting"] for e in front_lookup.entries}
    if front_setting > 0 and front_rate_nmm > 0 and front_setting not in existing_f:
        front_lookup.entries.append({
            "setting": front_setting,
            "rate_nmm": front_rate_nmm,
            "source": "decrypted_sto",
        })
        front_lookup.entries.sort(key=lambda e: e["setting"])
        front_lookup.is_calibrated = True
        front_lookup.method = "decrypted_sto"

    existing_r = {e["setting"] for e in rear_lookup.entries}
    if rear_setting > 0 and rear_rate_nmm > 0 and rear_setting not in existing_r:
        rear_lookup.entries.append({
            "setting": rear_setting,
            "rate_nmm": rear_rate_nmm,
            "source": "decrypted_sto",
        })
        rear_lookup.entries.sort(key=lambda e: e["setting"])
        rear_lookup.is_calibrated = True
        rear_lookup.method = "decrypted_sto"

    return front_lookup, rear_lookup


# Per-car indexed spring configuration (index → OD in mm).
# Used for physics-based extrapolation of spring rates across all indices
# from a single calibrated anchor point (k ∝ OD⁴ for solid torsion bars).
_INDEXED_SPRING_OD_MAPS: dict[str, dict] = {
    "ferrari": {
        "front": {"index_range": (0, 18), "od_range_mm": (20.0, 24.0)},
        "rear":  {"index_range": (0, 18), "od_range_mm": (20.0, 24.0)},  # ESTIMATE — same hardware
    },
    "acura": {
        "front": {"index_range": (0, 18), "od_range_mm": (13.9, 15.86)},
        "rear":  {"index_range": (0, 18), "od_range_mm": (13.9, 18.20)},
    },
}


def _index_to_od(idx: float, index_range: tuple, od_range_mm: tuple) -> float:
    """Linearly map a garage index to physical torsion bar OD (mm)."""
    idx_lo, idx_hi = index_range
    od_lo, od_hi = od_range_mm
    if abs(idx_hi - idx_lo) < 1e-9:
        return od_lo
    return od_lo + (idx - idx_lo) / (idx_hi - idx_lo) * (od_hi - od_lo)


def expand_torsion_lookup_from_physics(
    car: str, lookup: SpringLookupTable, axle: str = "front"
) -> SpringLookupTable:
    """Extrapolate spring rates for all indices using k ∝ OD⁴ from anchor point(s).

    Physics basis: for a solid cylindrical torsion bar, k_wheel ∝ d⁴ / L where d
    is the bar diameter and L is the effective length. With a linear index→OD mapping
    (as used in iRacing's garage slider), knowing ONE (index, N/mm) pair lets us
    calibrate the constant C in k = C·OD⁴ and fill in ALL 0–18 indices.

    Only fills entries that don't already have a "decrypted_sto" source.
    Requires car to be in _INDEXED_SPRING_OD_MAPS.
    """
    if not lookup.entries or car not in _INDEXED_SPRING_OD_MAPS:
        return lookup

    car_map = _INDEXED_SPRING_OD_MAPS[car].get(axle)
    if car_map is None:
        return lookup

    index_range = car_map["index_range"]
    od_range_mm = car_map["od_range_mm"]

    # Collect high-quality anchor points (STO-verified or physics-inferred)
    anchor_od4: list[float] = []
    anchor_k: list[float] = []
    for e in lookup.entries:
        if e.get("source") in ("decrypted_sto", "physics_inference"):
            od = _index_to_od(e["setting"], index_range, od_range_mm)
            anchor_od4.append(od ** 4)
            anchor_k.append(e["rate_nmm"])

    if not anchor_od4:
        return lookup

    # Fit C constant: k = C·OD⁴  (no intercept — physics constraint)
    od4_arr = np.array(anchor_od4)
    k_arr = np.array(anchor_k)
    C = float(np.dot(od4_arr, k_arr) / np.dot(od4_arr, od4_arr))

    # Generate entries for all integer indices that don't yet have STO data
    existing_sto_settings = {e["setting"] for e in lookup.entries if e.get("source") == "decrypted_sto"}
    idx_lo, idx_hi = int(index_range[0]), int(index_range[1])
    added_count = 0
    for idx in range(idx_lo, idx_hi + 1):
        if float(idx) not in existing_sto_settings:
            od = _index_to_od(float(idx), index_range, od_range_mm)
            k_est = C * od ** 4
            # Remove any stale physics_extrapolated entry at this index first
            lookup.entries = [e for e in lookup.entries if e["setting"] != float(idx)]
            lookup.entries.append({
                "setting": float(idx),
                "rate_nmm": round(k_est, 4),
                "source": "physics_extrapolated",
            })
            added_count += 1

    lookup.entries.sort(key=lambda e: e["setting"])
    if added_count > 0:
        # Update method tag — preserve "decrypted_sto" as primary if anchors exist
        n_sto = len([e for e in lookup.entries if e.get("source") == "decrypted_sto"])
        lookup.method = f"decrypted_sto+physics_extrapolated" if n_sto > 0 else "physics_extrapolated"

    return lookup


def interpolate_spring_rate(lookup: SpringLookupTable, setting: float) -> float | None:
    """Interpolate spring rate from lookup table. Returns None if insufficient data."""
    entries = lookup.entries
    if not entries:
        return None
    if len(entries) == 1:
        # Only one data point — return that rate (no extrapolation without OD map context).
        # Call expand_torsion_lookup_from_physics() first if richer coverage is needed.
        return entries[0]["rate_nmm"]
    # Linear interpolation/extrapolation
    settings = np.array([e["setting"] for e in entries])
    rates = np.array([e["rate_nmm"] for e in entries])
    return float(np.interp(setting, settings, rates))


# ─────────────────────────────────────────────────────────────────────────────
# Main calibration pipeline
# ─────────────────────────────────────────────────────────────────────────────

def fit_models_from_points(car: str, points: list[CalibrationPoint]) -> CarCalibrationModels:
    """Fit all calibration models from accumulated data points."""
    models = CarCalibrationModels(car=car, n_sessions=len(points))

    # Load the car object for index→N/mm decoding (Ferrari/Acura use indices,
    # BMW uses physical rates). Needed for m_eff and any physics-derived fits.
    try:
        from car_model.cars import get_car
        _car_obj = get_car(car)
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("Car model load failed in fit_models: %s", e)
        _car_obj = None

    # Deduplicate by unique setup configuration (exclude telemetry-only differences)
    seen: set[tuple] = set()
    unique: list[CalibrationPoint] = []
    for pt in points:
        key = _setup_fingerprint(pt)
        if key not in seen:
            seen.add(key)
            unique.append(pt)

    models.n_unique_setups = len(unique)

    if len(unique) < _MIN_SESSIONS_FOR_FIT:
        models.status["deflection_model"] = f"insufficient data ({len(unique)}/{_MIN_SESSIONS_FOR_FIT} unique setups)"
        return models

    rows = [asdict(pt) for pt in unique]

    # ─── Universal index→N/mm conversion ───────────────────────────────────
    # For indexed cars (Ferrari, Acura), the calibration points store raw
    # garage indices for spring rates and torsion bar OD. ALL downstream
    # fits need physical N/mm and mm values, especially compliance features
    # (1/k) which would be garbage on indices (1/3 != 1/75_N_mm).
    # Convert ONCE here so every fit uses correct physical values.
    #
    # IMPORTANT: Some calibration datasets contain MIXED data — some points
    # with raw indices and others with pre-decoded physical rates. We detect
    # this by checking if the value exceeds the index range maximum. If it
    # does, the value is already in physical units and should NOT be converted.
    if _car_obj is not None:
        _hsm = _car_obj.heave_spring
        _csm = _car_obj.corner_spring

        def _needs_index_decode(value: float, idx_range: tuple[float, float] | None) -> bool:
            """Return True if the value looks like a raw index, not a physical rate."""
            if idx_range is None:
                return False
            # If the value is within the index range (with a small margin for
            # floating-point), it's a raw index. If it's above the max index,
            # it's already a decoded physical value (e.g., 50 N/mm vs index 5).
            return value <= idx_range[1] + 0.5

        for row in rows:
            # Front heave setting → N/mm
            if _needs_index_decode(row["front_heave_setting"],
                                   _hsm.front_setting_index_range):
                row["front_heave_setting"] = _hsm.front_rate_from_setting(
                    row["front_heave_setting"]
                )
            # Rear third/heave setting → N/mm
            if _needs_index_decode(row["rear_third_setting"],
                                   _hsm.rear_setting_index_range):
                row["rear_third_setting"] = _hsm.rear_rate_from_setting(
                    row["rear_third_setting"]
                )
            # Front torsion OD → physical mm
            if (hasattr(_csm, "front_setting_index_range")
                    and _needs_index_decode(row["front_torsion_od_mm"],
                                           _csm.front_setting_index_range)):
                row["front_torsion_od_mm"] = _csm.front_torsion_od_from_setting(
                    row["front_torsion_od_mm"]
                )
            # Rear spring setting → N/mm (Ferrari rear torsion bar index,
            # or Acura rear torsion bar index)
            if (hasattr(_csm, "rear_setting_index_range")
                    and _needs_index_decode(row["rear_spring_setting"],
                                           _csm.rear_setting_index_range)):
                row["rear_spring_setting"] = _csm.rear_bar_rate_from_setting(
                    row["rear_spring_setting"]
                )

    def col(name: str) -> np.ndarray:
        return _col(rows, name)

    heave = col("front_heave_setting")
    od4 = col("front_torsion_od_mm") ** 4

    # ─── 1. Front Ride Height ───
    # Physics-based feature selection: heave spring compression under aero
    # load is proportional to 1/k (compliance), not k. Using 1/heave as the
    # feature matches the underlying physics and dramatically improves fit
    # quality for cars with varied heave spring rates.
    _front_rh_std = np.std(col("static_front_rh_mm"))
    if _front_rh_std > 0.5:
        _inv_heave = 1.0 / np.maximum(heave, 1.0)
        _front_rh_candidates = [
            (col("front_pushrod_mm"), "front_pushrod"),
            (_inv_heave, "inv_front_heave"),
            (col("front_heave_perch_mm"), "front_heave_perch"),
            (col("front_camber_deg"), "front_camber"),
            (col("fuel_l"), "fuel"),
            (col("front_torsion_od_mm"), "torsion_od"),
        ]
        _front_rh_X = []
        _front_rh_names = []
        for arr, name in _front_rh_candidates:
            _n_unique = len(np.unique(arr))
            _std = np.std(arr)
            # Include features with at least 2 unique values AND non-zero
            # variance. Fewer restrictive thresholds so perches and camber
            # (which often have few unique values) still contribute.
            if _n_unique >= 2 and _std > 1e-6:
                _front_rh_X.append(arr)
                _front_rh_names.append(name)
        if _front_rh_X:
            X = np.column_stack(_front_rh_X)
            X, _front_rh_names = _select_features(
                X, col("static_front_rh_mm"), _front_rh_names)
            models.front_ride_height = _fit(
                X, col("static_front_rh_mm"),
                _front_rh_names,
                "front_ride_height",
            )

    elif len(unique) >= _MIN_SESSIONS_FOR_FIT and _front_rh_std > 0:
        # Near-constant front RH: create a constant model (intercept-only)
        _mean_frh = float(np.mean(col("static_front_rh_mm")))
        models.front_ride_height = FittedModel(
            name="front_ride_height",
            feature_names=[],
            coefficients=[_mean_frh],
            r_squared=1.0,
            rmse=float(_front_rh_std),
            loo_rmse=float(_front_rh_std),
            n_samples=len(unique),
            is_calibrated=True,
        )

    # ─── 2. Rear Ride Height ───
    if np.std(col("static_rear_rh_mm")) > 0.5:
        # Compliance model for BOTH third spring AND rear corner spring.
        # Static rear RH under aero load = baseline - F_third/k_third - F_spring/k_spring
        # so the regression features are 1/k_third and 1/k_spring, not the stiffnesses.
        # This matches the physics and dramatically improves fit quality.
        _rear_third = col("rear_third_setting")
        _rear_spring = col("rear_spring_setting")
        _inv_third = 1.0 / np.maximum(_rear_third, 1.0)
        _inv_spring = 1.0 / np.maximum(_rear_spring, 1.0)
        _rear_rh_candidates = [
            (col("rear_pushrod_mm"), "rear_pushrod"),
            (_inv_third, "inv_rear_third"),
            (_inv_spring, "inv_rear_spring"),
            (col("rear_third_perch_mm"), "rear_third_perch"),
            (col("rear_spring_perch_mm"), "rear_spring_perch"),
            (col("fuel_l"), "fuel"),
        ]
        _rear_rh_X = []
        _rear_rh_names = []
        for arr, name in _rear_rh_candidates:
            _n_unique = len(np.unique(arr))
            _std = np.std(arr)
            # Include features with at least 2 unique values and non-zero variance.
            # LOO validation in the _fit call guards against overfit.
            if _n_unique >= 2 and _std > 1e-6:
                _rear_rh_X.append(arr)
                _rear_rh_names.append(name)
        if _rear_rh_X:
            X = np.column_stack(_rear_rh_X)
            X, _rear_rh_names = _select_features(
                X, col("static_rear_rh_mm"), _rear_rh_names)
            models.rear_ride_height = _fit(
                X, col("static_rear_rh_mm"),
                _rear_rh_names,
                "rear_ride_height",
            )

    # ─── 3. Torsion Bar Turns ───
    _tb_turns = col("torsion_bar_turns")
    _tb_valid = _tb_turns[_tb_turns > 0]
    if len(_tb_valid) > 0 and np.std(_tb_turns) > 0.005:
        X = np.column_stack([
            1.0 / np.maximum(heave, 1.0),
            col("front_heave_perch_mm"),
            col("front_torsion_od_mm"),
        ])
        models.torsion_bar_turns = _fit(
            X, col("torsion_bar_turns"),
            ["1/front_heave", "front_heave_perch", "torsion_od"],
            "torsion_bar_turns",
        )
    elif len(_tb_valid) >= _MIN_SESSIONS_FOR_FIT:
        # Near-constant torsion turns: use mean as constant model
        _mean_turns = float(np.mean(_tb_valid))
        models.torsion_bar_turns = FittedModel(
            name="torsion_bar_turns",
            feature_names=[],
            coefficients=[_mean_turns],
            r_squared=1.0,
            rmse=float(np.std(_tb_valid)),
            loo_rmse=float(np.std(_tb_valid)),
            n_samples=len(unique),
            is_calibrated=True,
        )

    # ─── 4. Torsion Bar Deflection ───
    if np.std(col("torsion_bar_defl_mm")) > 0.5:
        y_load = col("torsion_bar_defl_mm") * od4
        X = np.column_stack([heave, col("front_heave_perch_mm")])
        models.torsion_bar_defl = _fit(
            X, y_load,
            ["front_heave", "front_heave_perch"],
            "torsion_bar_defl_load",
        )

    # ─── 5. Heave Spring Deflection Static ───
    # Physics: heave_defl ≈ A/k_heave + B*perch + C/OD^4 + intercept
    # DeflectionModel.heave_spring_defl_static() uses this reciprocal form.
    # Features MUST match DeflectionModel semantics so that apply_to_car()
    # can map coefficients[1:] directly to (inv_heave, perch, inv_od4).
    # Previous polynomial fit (heave, perch, heave², perch², heave*perch, od)
    # was semantically incompatible — its coefficients were wrongly assigned
    # to reciprocal fields, producing incorrect deflection predictions.
    if np.std(col("heave_spring_defl_static_mm")) > 0.5:
        heave_perch = col("front_heave_perch_mm")
        torsion_od = col("front_torsion_od_mm")
        # heave is already in N/mm after universal index conversion above
        X_recip = np.column_stack([
            1.0 / np.maximum(heave, 1.0),         # 1/heave_nmm — spring compliance
            heave_perch,                            # perch offset — load path
            1.0 / np.maximum(torsion_od ** 4, 1.0), # 1/OD^4 — torsion bar compliance
        ])
        models.heave_spring_defl_static = _fit(
            X_recip, col("heave_spring_defl_static_mm"),
            ["inv_heave_nmm", "front_heave_perch", "inv_od4"],
            "heave_spring_defl_static",
        )

    # ─── 6. Heave Spring Deflection Max ───
    if np.std(col("heave_spring_defl_max_mm")) > 1.0:
        X = np.column_stack([heave])
        models.heave_spring_defl_max = _fit(
            X, col("heave_spring_defl_max_mm"),
            ["front_heave"],
            "heave_spring_defl_max",
        )

    # ─── 7. Heave Slider Deflection Static ───
    if np.std(col("heave_slider_defl_static_mm")) > 0.5:
        X = np.column_stack([heave, col("front_heave_perch_mm"), col("front_torsion_od_mm")])
        models.heave_slider_defl_static = _fit(
            X, col("heave_slider_defl_static_mm"),
            ["front_heave", "front_heave_perch", "torsion_od"],
            "heave_slider_defl_static",
        )

    # ─── 8. Front Shock Deflection Static ───
    # Shock deflection depends on pushrod (geometric), heave perch (load path
    # through heave spring vs. corner shock), heave spring stiffness (how much
    # heave spring carries), and torsion bar stiffness (corner load sharing).
    if np.std(col("front_shock_defl_static_mm")) > 0.5:
        _fs_names = ["front_pushrod", "front_heave_perch", "front_heave", "torsion_od"]
        X = np.column_stack([
            col("front_pushrod_mm"),
            col("front_heave_perch_mm"),
            heave,
            col("front_torsion_od_mm"),
        ])
        X, _fs_names = _select_features(
            X, col("front_shock_defl_static_mm"), _fs_names)
        models.front_shock_defl_static = _fit(
            X, col("front_shock_defl_static_mm"),
            _fs_names,
            "front_shock_defl_static",
        )

    # ─── 9. Rear Shock Deflection Static ───
    # Rear shock deflection depends on rear pushrod (geometric), rear third perch
    # (load path through third/heave spring vs. corner shock), and rear third
    # spring stiffness (how much the third spring carries).
    if np.std(col("rear_shock_defl_static_mm")) > 0.5:
        # Compliance physics: rear shock deflection depends on aero load
        # divided by spring stiffness, plus pushrod offset and perches.
        _rspring = col("rear_spring_setting")
        _rthird = col("rear_third_setting")
        _rs_names = ["rear_pushrod", "inv_rear_third", "inv_rear_spring",
                     "rear_third_perch", "rear_spring_perch"]
        X = np.column_stack([
            col("rear_pushrod_mm"),
            1.0 / np.maximum(_rthird, 1.0),
            1.0 / np.maximum(_rspring, 1.0),
            col("rear_third_perch_mm"),
            col("rear_spring_perch_mm"),
        ])
        X, _rs_names = _select_features(
            X, col("rear_shock_defl_static_mm"), _rs_names)
        models.rear_shock_defl_static = _fit(
            X, col("rear_shock_defl_static_mm"),
            _rs_names,
            "rear_shock_defl_static",
        )

    # ─── 10. Rear Spring Deflection Static ───
    # Compliance physics: defl ∝ F/k (1/spring) under aero load. Include
    # cross-spring effect (third), perches, and pushrod for a complete model.
    if np.std(col("rear_spring_defl_static_mm")) > 0.5:
        _rspring = col("rear_spring_setting")
        _rthird = col("rear_third_setting")
        _rsd_names = ["inv_rear_spring", "inv_rear_third", "rear_spring_perch",
                      "rear_third_perch", "rear_pushrod"]
        X = np.column_stack([
            1.0 / np.maximum(_rspring, 1.0),
            1.0 / np.maximum(_rthird, 1.0),
            col("rear_spring_perch_mm"),
            col("rear_third_perch_mm"),
            col("rear_pushrod_mm"),
        ])
        X, _rsd_names = _select_features(
            X, col("rear_spring_defl_static_mm"), _rsd_names)
        models.rear_spring_defl_static = _fit(
            X, col("rear_spring_defl_static_mm"),
            _rsd_names,
            "rear_spring_defl_static",
        )

    # ─── 11. Rear Spring Deflection Max ───
    if np.std(col("rear_spring_defl_max_mm")) > 1.0:
        X = np.column_stack([col("rear_spring_setting"), col("rear_spring_perch_mm")])
        models.rear_spring_defl_max = _fit(
            X, col("rear_spring_defl_max_mm"),
            ["rear_spring", "rear_spring_perch"],
            "rear_spring_defl_max",
        )

    # ─── 12. Third Spring Deflection Static ───
    # Same compliance pattern as rear_spring_defl_static.
    if np.std(col("third_spring_defl_static_mm")) > 0.5:
        _rspring = col("rear_spring_setting")
        _rthird = col("rear_third_setting")
        _tsd_names = ["inv_rear_third", "inv_rear_spring", "rear_third_perch",
                      "rear_spring_perch", "rear_pushrod"]
        X = np.column_stack([
            1.0 / np.maximum(_rthird, 1.0),
            1.0 / np.maximum(_rspring, 1.0),
            col("rear_third_perch_mm"),
            col("rear_spring_perch_mm"),
            col("rear_pushrod_mm"),
        ])
        X, _tsd_names = _select_features(
            X, col("third_spring_defl_static_mm"), _tsd_names)
        models.third_spring_defl_static = _fit(
            X, col("third_spring_defl_static_mm"),
            _tsd_names,
            "third_spring_defl_static",
        )

    # ─── 13. Third Spring Deflection Max ───
    if np.std(col("third_spring_defl_max_mm")) > 1.0:
        X = np.column_stack([col("rear_third_setting"), col("rear_third_perch_mm")])
        models.third_spring_defl_max = _fit(
            X, col("third_spring_defl_max_mm"),
            ["rear_third", "rear_third_perch"],
            "third_spring_defl_max",
        )

    # ─── 14. Third Slider Static ───
    if np.std(col("third_slider_defl_static_mm")) > 0.5:
        X = np.column_stack([col("third_spring_defl_static_mm")])
        models.third_slider_defl_static = _fit(
            X, col("third_slider_defl_static_mm"),
            ["third_spring_defl"],
            "third_slider_defl_static",
        )

    # ─── 13b. Aero compression (Model 3) ───────────────────────────────────────
    # iRacing AeroCalculator provides FrontRhAtSpeed and RearRhAtSpeed, and we
    # have the static RH from the garage display. Their difference IS the aero
    # compression. This is already ground truth — no regression needed.
    aero_comps_front = []
    aero_comps_rear = []
    for pt in unique:
        if pt.front_rh_at_speed_mm > 0 and pt.static_front_rh_mm > 0:
            comp_f = pt.static_front_rh_mm - pt.front_rh_at_speed_mm
            if 1.0 < comp_f < 50.0:  # plausible compression range
                aero_comps_front.append(comp_f)
        if pt.rear_rh_at_speed_mm > 0 and pt.static_rear_rh_mm > 0:
            comp_r = pt.static_rear_rh_mm - pt.rear_rh_at_speed_mm
            if 1.0 < comp_r < 80.0:
                aero_comps_rear.append(comp_r)

    if len(aero_comps_front) >= 2:
        models.aero_front_compression_mm = float(np.mean(aero_comps_front))
        models.aero_rear_compression_mm = float(np.mean(aero_comps_rear)) if aero_comps_rear else None
        models.aero_n_sessions = len(aero_comps_front)
        rear_str = f"{models.aero_rear_compression_mm:.1f}mm" if models.aero_rear_compression_mm is not None else "n/a"
        models.status["aero_compression"] = (
            f"calibrated from IBT AeroCalculator ({len(aero_comps_front)} sessions, "
            f"front={models.aero_front_compression_mm:.1f}mm "
            f"rear={rear_str})"
        )

    # ─── 13c. ARB Roll Stiffness (Model 7) ─────────────────────────────────────
    # Back-solve ARB stiffness from measured roll gradient (deg/g).
    # Physics: roll_gradient = m*g*h_cg / K_total_roll
    # K_total_roll = K_arb_front + K_arb_rear + K_spring_roll_front + K_spring_roll_rear
    # If springs are held constant between sessions and only ARB varies,
    # the change in roll gradient gives the ARB stiffness delta.
    # Requires: 2+ sessions with SAME springs but DIFFERENT ARB sizes AND roll gradient.
    arb_calibration_points = []
    for pt in unique:
        if (pt.aero_df_balance_pct > 0 and pt.front_arb_size and pt.front_arb_blade > 0
                and pt.roll_gradient_deg_per_g > 0.1):
            arb_key = (pt.front_arb_size, pt.front_arb_blade, pt.rear_arb_size, pt.rear_arb_blade)
            spring_key = (round(pt.front_heave_setting, 1), round(pt.front_torsion_od_mm, 3),
                          round(pt.rear_third_setting, 1), round(pt.rear_spring_setting, 1))
            arb_calibration_points.append({
                "arb_key": arb_key,
                "spring_key": spring_key,
                "front_size": pt.front_arb_size,
                "front_blade": pt.front_arb_blade,
                "rear_size": pt.rear_arb_size,
                "rear_blade": pt.rear_arb_blade,
                "roll_gradient": pt.roll_gradient_deg_per_g,
            })

    # Group by spring config; find groups where ARB varies
    spring_groups: dict[tuple, list[dict]] = {}
    for ap in arb_calibration_points:
        sk = ap["spring_key"]
        spring_groups.setdefault(sk, []).append(ap)

    arb_varied_groups = {k: v for k, v in spring_groups.items() if len(set(ap["arb_key"] for ap in v)) >= 2}
    if arb_varied_groups and _car_obj is not None:
        # Back-solve: K_total = m*g*h_cg / roll_gradient_rad_per_g
        # K_total = K_springs + K_arb_total
        # With 2+ ARB configs at same springs, K_springs cancels in the delta.
        m_kg = _car_obj.total_mass(50.0)  # approximate with half fuel
        h_cg_m = _car_obj.corner_spring.cg_height_mm / 1000.0
        mg_h = m_kg * 9.81 * h_cg_m  # N·m

        # Collect (arb_config_label, K_total) pairs.
        # Units: k_total is in N·m/deg (matches arb_model.*_roll_stiffness units).
        # Original code stored N·m/rad here while comparing against N·m/deg
        # predictions — a 57.3× units mismatch that produced the 170787% bogus
        # error and forced arb_calibrated=False. Now both sides are N·m/deg.
        DEG_PER_RAD = 180.0 / 3.14159265358979
        arb_k_totals: dict[tuple, list[float]] = {}
        for group in arb_varied_groups.values():
            for ap in group:
                rg_rad = ap["roll_gradient"] * (3.14159265358979 / 180.0)
                if rg_rad > 0.001:
                    k_total_nm_per_rad = mg_h / rg_rad
                    k_total = k_total_nm_per_rad / DEG_PER_RAD  # → N·m/deg
                    arb_k_totals.setdefault(ap["arb_key"], []).append(k_total)

        # Average K_total per ARB config
        arb_k_avg: dict[tuple, float] = {}
        for ak, vals in arb_k_totals.items():
            arb_k_avg[ak] = float(np.mean(vals))

        if len(arb_k_avg) >= 2:
            # K_springs is the same across all configs (we controlled for it).
            # Use the softest measured config as the baseline to infer K_springs.
            sorted_configs = sorted(arb_k_avg.items(), key=lambda x: x[1])
            k_min = sorted_configs[0][1]
            n_configs = len(sorted_configs)

            # Estimate K_total measurement noise floor from REPLICATE sessions
            # within the same ARB config.  std(K_total) at fixed ARB is pure
            # roll-gradient noise (driver consistency, brake/throttle, line).
            # IMPORTANT: `unique` is deduplicated by setup fingerprint, so replicate
            # sessions of the same setup collapse to one entry — we must read
            # noise from the FULL `points` list, not from `unique`.
            raw_arb_k: dict[tuple, list[float]] = {}
            for _p in points:
                if (_p.aero_df_balance_pct > 0 and _p.front_arb_size
                        and _p.front_arb_blade > 0
                        and _p.roll_gradient_deg_per_g > 0.1):
                    _rg_rad = _p.roll_gradient_deg_per_g * (3.14159265358979 / 180.0)
                    if _rg_rad > 0.001:
                        _ak = (_p.front_arb_size, _p.front_arb_blade,
                               _p.rear_arb_size, _p.rear_arb_blade)
                        # N·m/deg to match arb_k_totals units (see DEG_PER_RAD above)
                        raw_arb_k.setdefault(_ak, []).append((mg_h / _rg_rad) / DEG_PER_RAD)
            noise_samples: list[float] = []
            for _vals in raw_arb_k.values():
                if len(_vals) >= 2:
                    noise_samples.append(float(np.std(_vals, ddof=1)))
            noise_floor = (
                float(np.sqrt(np.mean([s * s for s in noise_samples])))
                if noise_samples else 0.0
            )

            # Validate: compare measured K_total deltas against the car model's
            # predicted ARB stiffness deltas.
            arb_model = _car_obj.arb
            baseline_key = sorted_configs[0][0]
            bf_size, bf_blade, br_size, br_blade = baseline_key
            k_arb_baseline = (
                arb_model.front_roll_stiffness(bf_size, bf_blade)
                + arb_model.rear_roll_stiffness(br_size, br_blade)
            )

            max_relative_error = 0.0
            max_predicted_delta = 0.0
            n_compared = 0
            for arb_key, k_total in sorted_configs[1:]:
                delta_k_measured = k_total - k_min
                fs, fb, rs, rb = arb_key
                k_arb_predicted = (
                    arb_model.front_roll_stiffness(fs, fb)
                    + arb_model.rear_roll_stiffness(rs, rb)
                )
                delta_k_predicted = k_arb_predicted - k_arb_baseline
                if delta_k_predicted > 0:
                    rel_err = abs(delta_k_measured - delta_k_predicted) / delta_k_predicted
                    max_relative_error = max(max_relative_error, rel_err)
                    max_predicted_delta = max(max_predicted_delta, delta_k_predicted)
                    n_compared += 1

            # Noise gate: if the largest predicted delta is below 2x the noise
            # floor of the K_total measurement, the back-solve cannot resolve
            # the signal.  Mark INCONCLUSIVE (None) so the gate treats this as
            # "manual hand-cal trusted, no auto-validation possible" rather
            # than as a contradiction.
            if (
                n_compared > 0
                and noise_floor > 0
                and max_predicted_delta < 2.0 * noise_floor
            ):
                models.status["arb_stiffness"] = (
                    f"INCONCLUSIVE: roll-gradient noise floor "
                    f"({noise_floor:.0f} N·m/deg) exceeds max predicted ARB "
                    f"delta ({max_predicted_delta:.0f} N·m/deg) across "
                    f"{n_configs} configs. Signal is below measurement noise — "
                    f"cannot validate or invalidate cars.py manual values. "
                    f"Trusting hand-calibration."
                )
                models.status["arb_calibrated"] = None
            elif n_compared > 0 and max_relative_error <= 0.20:
                models.status["arb_stiffness"] = (
                    f"roll gradient validated: {n_configs} ARB configs, "
                    f"{len(arb_varied_groups)} spring groups, "
                    f"max error {max_relative_error:.1%} (within 20% tolerance)"
                )
                models.status["arb_calibrated"] = True
            else:
                models.status["arb_stiffness"] = (
                    f"roll gradient data collected ({n_configs} ARB configs, "
                    f"{len(arb_varied_groups)} spring groups) but model stiffness "
                    f"does NOT match measured deltas (max error {max_relative_error:.1%}, "
                    f"need ≤20%). ARB stiffness values in cars.py need updating."
                )
                models.status["arb_calibrated"] = False
        else:
            models.status["arb_stiffness"] = (
                f"data available but insufficient variation "
                f"(need 2+ ARB configs with roll gradient; have {len(arb_k_avg)})"
            )
    elif arb_calibration_points:
        n_with_rg = sum(1 for ap in arb_calibration_points if ap.get("roll_gradient", 0) > 0.1)
        models.status["arb_stiffness"] = (
            f"insufficient controlled data (need sessions with same springs, different ARB "
            f"AND roll gradient; have {n_with_rg} points with roll data)"
        )
    else:
        models.status["arb_stiffness"] = "insufficient data (need sessions with varied ARB settings and driving telemetry)"

    # ─── 14. Roll gradient consistency check ───────────────────────────────────
    # We measure whether roll gradient is stable across sessions (low CV).
    # A stable roll gradient validates the total roll stiffness model, but does
    # NOT constitute a calibration of the front/rear roll gain split.
    # Roll gains require per-corner suspension geometry data (camber change vs
    # body roll) which is not available from IBT telemetry alone.
    # Therefore: we track gradient stability for informational purposes only;
    # we do NOT set roll_gains_calibrated=True or copy cars.py defaults as if
    # they were measured — that would misrepresent unfit values as "calibrated".
    roll_grads = [pt.roll_gradient_deg_per_g for pt in unique if pt.roll_gradient_deg_per_g > 0.1]
    if len(roll_grads) >= 3:
        rg_mean = float(np.mean(roll_grads))
        rg_std = float(np.std(roll_grads))
        models.status["roll_gradient_mean"] = rg_mean
        models.status["roll_gradient_n_sessions"] = len(roll_grads)
        models.status["roll_gradient_cv"] = float(rg_std / max(rg_mean, 0.01))
        # Only flag as stable (not "calibrated") when CV < 30%
        if rg_std / max(rg_mean, 0.01) < 0.30:
            models.status["roll_gradient_stable"] = True
            # Explicitly NOT setting roll_gains_calibrated: the roll gradient
            # constrains total roll stiffness but cannot split front/rear gains.
            # Roll gains require geometry measurements (camber-per-g sweep) or
            # direct kinematic data from suspension geometry models.
        else:
            models.status["roll_gradient_stable"] = False

    # ─── 15. m_eff from telemetry ───
    # m_eff = k_nmm * (sigma_mm / shock_vel_p99)^2
    # For indexed cars (Ferrari/Acura), decode setting→N/mm via the car's
    # HeaveSpringModel. Skip if decoder is unvalidated to avoid garbage m_eff.
    _heave_model = _car_obj.heave_spring if _car_obj else None
    _front_uses_index = _heave_model is not None and _heave_model.front_setting_index_range is not None
    _front_index_unvalidated = _heave_model is not None and getattr(_heave_model, "heave_index_unvalidated", False)
    m_effs_front = []
    for pt in unique:
        if pt.front_shock_vel_p99_mps > 0.01 and pt.front_sigma_mm > 0.1 and pt.front_heave_setting > 0:
            if _front_uses_index:
                if _front_index_unvalidated:
                    continue  # skip — index→N/mm mapping not verified for this car
                k = _heave_model.front_rate_from_setting(pt.front_heave_setting)
            else:
                k = pt.front_heave_setting  # already N/mm (BMW)
            if k <= 0:
                continue
            # m_eff = k_N/m * (excursion_m / v_mps)^2, excursion = sigma * 2.33
            # Unit conversion: k_N/m = k_nmm*1000, excursion_m = sigma_mm*2.33/1000
            m = k * (pt.front_sigma_mm * 2.33) ** 2 / (1000.0 * pt.front_shock_vel_p99_mps ** 2)
            if 50 < m < 3000:  # plausible range
                m_effs_front.append((pt.front_heave_setting, m))

    if len(m_effs_front) >= 3:
        settings = np.array([x[0] for x in m_effs_front])
        masses = np.array([x[1] for x in m_effs_front])
        # Check if rate-dependent (more than 20% variation across spring range)
        if np.std(masses) / np.mean(masses) > 0.20 and np.std(settings) > 5.0:
            models.m_eff_is_rate_dependent = True
            models.m_eff_rate_table = [
                {"setting": float(s), "m_eff_kg": float(m)}
                for s, m in sorted(m_effs_front)
            ]
            models.status["m_eff"] = f"rate-dependent ({len(m_effs_front)} points, range {np.min(masses):.0f}-{np.max(masses):.0f} kg)"
        else:
            models.m_eff_front_kg = float(np.mean(masses))
            models.status["m_eff"] = f"constant ({len(m_effs_front)} points, mean {models.m_eff_front_kg:.0f} kg)"

    # ─── 15b. Rear m_eff from telemetry ───
    _rear_uses_index = _heave_model is not None and _heave_model.rear_setting_index_range is not None
    m_effs_rear: list[tuple[float, float]] = []  # (setting, m_eff) tuples
    for pt in unique:
        if pt.rear_shock_vel_p99_mps > 0.01 and pt.rear_sigma_mm > 0.1 and pt.rear_third_setting > 0:
            if _rear_uses_index:
                if _front_index_unvalidated:
                    continue  # skip — index→N/mm mapping not verified
                k = _heave_model.rear_rate_from_setting(pt.rear_third_setting)
            else:
                k = pt.rear_third_setting  # already N/mm (BMW)
            if k <= 0:
                continue
            # Same unit-corrected formula as front
            m = k * (pt.rear_sigma_mm * 2.33) ** 2 / (1000.0 * pt.rear_shock_vel_p99_mps ** 2)
            if 100 < m < 5000:  # plausible range for rear (heavier: aero + third spring)
                m_effs_rear.append((pt.rear_third_setting, m))

    if len(m_effs_rear) >= 3:
        rear_settings = np.array([x[0] for x in m_effs_rear])
        rear_masses = np.array([x[1] for x in m_effs_rear])
        # Same rate-dependence check as front: >20% CV and >5 N/mm setting spread
        if np.std(rear_masses) / np.mean(rear_masses) > 0.20 and np.std(rear_settings) > 5.0:
            models.m_eff_rear_rate_table = [
                {"setting": float(s), "m_eff_kg": float(m)}
                for s, m in sorted(m_effs_rear)
            ]
            # Keep scalar mean as fallback
            models.m_eff_rear_kg = float(np.mean(rear_masses))
            models.status["m_eff_rear"] = (
                f"rate-dependent ({len(m_effs_rear)} points, "
                f"range {np.min(rear_masses):.0f}-{np.max(rear_masses):.0f} kg)"
            )
        else:
            models.m_eff_rear_kg = float(np.mean(rear_masses))
            models.status["m_eff_rear"] = (
                f"constant ({len(m_effs_rear)} points, mean {models.m_eff_rear_kg:.0f} kg)"
            )

    # ─── 16. Measured LLTD target ───
    # IBT 'lltd_measured' is actually roll_distribution_proxy — a geometric
    # ratio (t_f^3/(t_f^3+t_r^3)) insensitive to spring stiffness. NOT usable
    # as a calibration target. ARB solver uses OptimumG/Milliken physics formula
    # instead. To calibrate real LLTD, need wheel-force telemetry or controlled
    # per-axle ARB lap-time correlation (10+ sessions).
    models.status["lltd_target"] = (
        "DISABLED — IBT 'lltd_measured' is a geometric proxy "
        "(t_f^3/(t_f^3+t_r^3)), not true LLTD. ARB solver uses OptimumG physics."
    )

    # Build calibration completeness status
    fitted_count = sum(1 for attr in [
        models.front_ride_height, models.rear_ride_height,
        models.front_shock_defl_static, models.rear_shock_defl_static,
        models.heave_spring_defl_static, models.heave_spring_defl_max,
    ] if attr is not None)

    if fitted_count >= 4:
        models.calibration_complete = True
        best_r2 = max(
            (m.r_squared for m in [
                models.front_ride_height, models.rear_ride_height,
                models.heave_spring_defl_static,
            ] if m is not None),
            default=0.0,
        )
        models.status["deflection_model"] = f"calibrated ({len(unique)} unique setups, best R²={best_r2:.2f})"
    else:
        models.status["deflection_model"] = f"partial ({fitted_count}/6 models fitted, {len(unique)} unique setups)"

    return models


# ─────────────────────────────────────────────────────────────────────────────
# Build GarageOutputModel from calibration regressions
# ─────────────────────────────────────────────────────────────────────────────

def build_garage_output_model(car_obj, models: CarCalibrationModels):
    """Build a GarageOutputModel from fitted calibration regressions.

    Returns a GarageOutputModel if enough data exists, else None.
    Requires at minimum: front RH model (from cars.py) + heave deflection model (from calibration).
    """
    from car_model.garage import GarageOutputModel

    rh = car_obj.ride_height_model

    hsm = car_obj.heave_spring
    csm = car_obj.corner_spring

    if rh.front_is_calibrated:
        # Front RH coefficients from the car's calibrated RideHeightModel
        front_intercept = rh.front_intercept
        front_coeff_pushrod = rh.front_coeff_pushrod
        front_coeff_heave = rh.front_coeff_heave_nmm
        front_coeff_inv_heave = rh.front_coeff_inv_heave
        front_coeff_camber = rh.front_coeff_camber_deg
        front_coeff_perch = rh.front_coeff_perch
        front_coeff_torsion_od = getattr(rh, "front_coeff_torsion_od", 0.0)
    else:
        # Fallback: use mean front RH from calibration data as constant model.
        # This handles cars (e.g. Acura) where front RH has near-zero variance
        # and no regression can be fit.
        import logging
        _logger = logging.getLogger(__name__)
        car_name = getattr(car_obj, "canonical_name", "")
        pts = load_calibration_points(car_name) if car_name else []
        valid_rhs = [p.static_front_rh_mm for p in pts if p.static_front_rh_mm > 0]
        if valid_rhs:
            front_intercept = sum(valid_rhs) / len(valid_rhs)
            _logger.info(
                "Front RH uncalibrated for %s — using mean=%.1f mm from %d points",
                car_name, front_intercept, len(valid_rhs),
            )
        else:
            front_intercept = rh.front_intercept if rh.front_intercept > 0 else 30.0
            _logger.info(
                "Front RH uncalibrated for %s — using default=%.1f mm",
                car_name, front_intercept,
            )
        front_coeff_pushrod = 0.0
        front_coeff_heave = 0.0
        front_coeff_inv_heave = 0.0
        front_coeff_camber = 0.0
        front_coeff_perch = 0.0
        front_coeff_torsion_od = 0.0

    # Rear RH coefficients
    rear_has_coeffs = (
        abs(rh.rear_coeff_pushrod) + abs(rh.rear_coeff_third_nmm)
        + abs(rh.rear_coeff_inv_third) + abs(rh.rear_coeff_rear_spring)
    ) > 1e-9
    if rear_has_coeffs or rh.rear_intercept > 1e-9:
        rear_intercept = rh.rear_intercept
        rear_coeff_pushrod = rh.rear_coeff_pushrod
        rear_coeff_third = rh.rear_coeff_third_nmm
        rear_coeff_inv_third = rh.rear_coeff_inv_third
        rear_coeff_rear_spring = rh.rear_coeff_rear_spring
        rear_coeff_inv_rear_spring = rh.rear_coeff_inv_spring
        rear_coeff_third_perch = rh.rear_coeff_heave_perch  # heave_perch field stores third_perch coeff
        rear_coeff_rear_spring_perch = rh.rear_coeff_spring_perch
        rear_coeff_fuel = rh.rear_coeff_fuel_l
    else:
        # Fallback: mean rear RH from calibration data (uncalibrated car)
        import logging
        _logger = logging.getLogger(__name__)
        car_name = getattr(car_obj, "canonical_name", "")
        pts = load_calibration_points(car_name) if car_name else []
        valid_rhs = [p.static_rear_rh_mm for p in pts if p.static_rear_rh_mm > 0]
        rear_intercept = sum(valid_rhs) / len(valid_rhs) if valid_rhs else 45.0
        _logger.info(
            "Rear RH uncalibrated for %s — using mean=%.1f mm",
            car_name, rear_intercept,
        )
        rear_coeff_pushrod = 0.0
        rear_coeff_third = 0.0
        rear_coeff_inv_third = 0.0
        rear_coeff_rear_spring = 0.0
        rear_coeff_inv_rear_spring = 0.0
        rear_coeff_third_perch = 0.0
        rear_coeff_rear_spring_perch = 0.0
        rear_coeff_fuel = 0.0

    # Heave deflection from calibration models
    heave_defl_intercept = 0.0
    heave_defl_coeff_heave = 0.0
    heave_defl_coeff_perch = 0.0
    heave_defl_coeff_inv_heave = 0.0
    heave_defl_coeff_inv_od4 = 0.0
    if models.heave_spring_defl_static:
        hs_defl = models.heave_spring_defl_static
        coeffs = hs_defl.coefficients
        heave_defl_intercept = coeffs[0] if len(coeffs) > 0 else 0.0
        # Map feature names to coefficients — inverse features go to separate fields
        for i, feat in enumerate(hs_defl.feature_names):
            if i + 1 < len(coeffs):
                if feat == "inv_heave_nmm":
                    heave_defl_coeff_inv_heave = coeffs[i + 1]  # 1/heave coefficient
                elif feat == "front_heave" or feat == "heave_nmm":
                    heave_defl_coeff_heave = coeffs[i + 1]  # direct heave coefficient
                elif feat == "front_heave_perch":
                    heave_defl_coeff_perch = coeffs[i + 1]
                elif feat == "inv_od4":
                    heave_defl_coeff_inv_od4 = coeffs[i + 1]  # 1/OD^4 coefficient

    # Heave defl max from calibration
    defl_max_intercept = 0.0
    defl_max_slope = 0.0
    if models.heave_spring_defl_max:
        dm = models.heave_spring_defl_max
        defl_max_intercept = dm.coefficients[0] if len(dm.coefficients) > 0 else 0.0
        for i, feat in enumerate(dm.feature_names):
            if i + 1 < len(dm.coefficients) and feat == "front_heave":
                defl_max_slope = dm.coefficients[i + 1]

    # Torsion turns from calibration
    torsion_turns_intercept = 0.0
    torsion_turns_coeff_heave_nmm = 0.0
    torsion_turns_coeff_heave_perch_mm = 0.0
    torsion_turns_coeff_torsion_od_mm = 0.0
    if models.torsion_bar_turns:
        tt = models.torsion_bar_turns
        torsion_turns_intercept = tt.coefficients[0] if len(tt.coefficients) > 0 else 0.0
        for i, feat in enumerate(tt.feature_names):
            if i + 1 < len(tt.coefficients):
                if feat == "1/front_heave":
                    # The fitted model uses 1/heave as a feature but GOM's
                    # predict_torsion_turns uses heave_nmm (linear). Fold the
                    # inverse-heave contribution into the intercept at the
                    # car's baseline heave rate.
                    baseline_heave = car_obj.front_heave_spring_nmm
                    if baseline_heave > 0:
                        torsion_turns_intercept += tt.coefficients[i + 1] / baseline_heave
                elif feat == "front_heave_perch":
                    torsion_turns_coeff_heave_perch_mm = tt.coefficients[i + 1]
                elif feat == "torsion_od":
                    torsion_turns_coeff_torsion_od_mm = tt.coefficients[i + 1]

    # Slider from calibration
    slider_intercept = 0.0
    slider_coeff_heave = 0.0
    slider_coeff_perch = 0.0
    slider_coeff_torsion_od = 0.0
    if models.heave_slider_defl_static:
        sl = models.heave_slider_defl_static
        slider_intercept = sl.coefficients[0] if len(sl.coefficients) > 0 else 0.0
        for i, feat in enumerate(sl.feature_names):
            if i + 1 < len(sl.coefficients):
                if feat == "front_heave":
                    slider_coeff_heave = sl.coefficients[i + 1]
                elif feat == "front_heave_perch":
                    slider_coeff_perch = sl.coefficients[i + 1]
                elif feat == "torsion_od":
                    slider_coeff_torsion_od = sl.coefficients[i + 1]

    # Defaults from car baseline
    pg = car_obj.pushrod
    return GarageOutputModel(
        name=f"{car_obj.canonical_name}_auto",
        track_keywords=(),  # applies to all tracks
        # Defaults from car baseline
        default_front_pushrod_mm=pg.front_pushrod_default_mm,
        default_rear_pushrod_mm=pg.rear_pushrod_default_mm,
        default_front_heave_nmm=car_obj.front_heave_spring_nmm,
        default_front_heave_perch_mm=hsm.perch_offset_front_baseline_mm,
        default_rear_third_nmm=car_obj.rear_third_spring_nmm,
        default_rear_third_perch_mm=hsm.perch_offset_rear_baseline_mm,
        default_front_torsion_od_mm=(csm.front_torsion_od_options[0]
                                     if csm.front_torsion_od_options else 0.0),
        default_rear_spring_nmm=csm.rear_spring_range_nmm[0],
        default_rear_spring_perch_mm=csm.rear_spring_perch_baseline_mm,
        default_front_camber_deg=car_obj.geometry.front_camber_baseline_deg,
        front_rh_floor_mm=car_obj.min_front_rh_static,
        max_slider_mm=45.0,
        heave_spring_defl_max_intercept_mm=defl_max_intercept,
        heave_spring_defl_max_slope=defl_max_slope,
        # Front RH regression (linear + compliance)
        front_intercept=front_intercept,
        front_coeff_pushrod=front_coeff_pushrod,
        front_coeff_heave_nmm=front_coeff_heave,
        front_coeff_inv_heave_nmm=front_coeff_inv_heave,
        front_coeff_heave_perch_mm=front_coeff_perch,
        front_coeff_torsion_od_mm=front_coeff_torsion_od,
        front_coeff_camber_deg=front_coeff_camber,
        front_coeff_fuel_l=0.0,
        # Rear RH regression (linear + compliance)
        rear_intercept=rear_intercept,
        rear_coeff_pushrod=rear_coeff_pushrod,
        rear_coeff_third_nmm=rear_coeff_third,
        rear_coeff_inv_third_nmm=rear_coeff_inv_third,
        rear_coeff_third_perch_mm=rear_coeff_third_perch,
        rear_coeff_rear_spring_nmm=rear_coeff_rear_spring,
        rear_coeff_inv_rear_spring_nmm=rear_coeff_inv_rear_spring,
        rear_coeff_rear_spring_perch_mm=rear_coeff_rear_spring_perch,
        rear_coeff_front_heave_perch_mm=0.0,
        rear_coeff_fuel_l=rear_coeff_fuel,
        # Heave deflection
        heave_defl_intercept=heave_defl_intercept,
        heave_defl_coeff_heave_nmm=heave_defl_coeff_heave,
        heave_defl_coeff_heave_perch_mm=heave_defl_coeff_perch,
        heave_defl_coeff_inv_heave_nmm=heave_defl_coeff_inv_heave,
        heave_defl_coeff_inv_od4=heave_defl_coeff_inv_od4,
        # Torsion turns
        torsion_turns_intercept=torsion_turns_intercept,
        torsion_turns_coeff_heave_nmm=torsion_turns_coeff_heave_nmm,
        torsion_turns_coeff_heave_perch_mm=torsion_turns_coeff_heave_perch_mm,
        torsion_turns_coeff_torsion_od_mm=torsion_turns_coeff_torsion_od_mm,
        # Slider
        slider_intercept=slider_intercept,
        slider_coeff_heave_nmm=slider_coeff_heave,
        slider_coeff_heave_perch_mm=slider_coeff_perch,
        slider_coeff_torsion_od_mm=slider_coeff_torsion_od,
        # Torsion bar C constant (0 for Porsche/non-torsion cars)
        torsion_bar_rate_c=csm.front_torsion_c,
        # Wire in the DeflectionModel so predict() can compute rear shock,
        # rear/third spring deflections, torsion bar deflection, etc.
        deflection=car_obj.deflection if car_obj.deflection.is_calibrated else None,
    )


# Apply calibrated models to car object
# ─────────────────────────────────────────────────────────────────────────────

def apply_to_car(car_obj, models: CarCalibrationModels) -> list[str]:
    """Apply fitted models to a car object from car_model/cars.py.

    Modifies the car object in-place. Returns list of applied correction notes.
    """
    applied = []

    if not models:
        return applied

    # Apply DeflectionModel coefficients — apply whatever is available
    # (Porsche has no front torsion bar, so front_shock_defl_static may be None)
    _defl_applied = False
    if models.heave_spring_defl_static or models.front_shock_defl_static or models.rear_shock_defl_static:
        try:
            defl = car_obj.deflection
            fs = models.front_shock_defl_static
            if fs and len(fs.coefficients) >= 2:
                _fs_map = {
                    "front_pushrod": "shock_front_pushrod_coeff",
                    "front_heave_perch": "front_shock_defl_heave_perch_coeff",
                    "torsion_od": "front_shock_defl_torsion_od_coeff",
                    "front_heave": "front_shock_defl_heave_coeff",
                }
                defl.shock_front_intercept = fs.coefficients[0]
                defl.shock_front_pushrod_coeff = 0.0
                defl.front_shock_defl_heave_perch_coeff = 0.0
                defl.front_shock_defl_torsion_od_coeff = 0.0
                defl.front_shock_defl_heave_coeff = 0.0
                _has_extra = False
                for i, feat in enumerate(fs.feature_names):
                    coeff = fs.coefficients[i + 1] if i + 1 < len(fs.coefficients) else 0.0
                    attr = _fs_map.get(feat)
                    if attr:
                        setattr(defl, attr, coeff)
                        if feat != "front_pushrod":
                            _has_extra = True
                defl.front_shock_defl_direct = _has_extra
                _defl_applied = True
            # Generic mapping helper: zero target attrs first, then apply
            # whichever named features exist in the fitted model.
            def _apply_named(model, intercept_attr, feature_to_attr,
                             direct_flag_attr=None):
                if not model:
                    return False
                # Zero all mapped attrs to prevent stale coefficients
                for attr in feature_to_attr.values():
                    if hasattr(defl, attr):
                        setattr(defl, attr, 0.0)
                if intercept_attr and hasattr(defl, intercept_attr):
                    setattr(defl, intercept_attr, model.coefficients[0])
                for i, feat in enumerate(model.feature_names):
                    coeff = model.coefficients[i + 1] if (i + 1) < len(model.coefficients) else 0.0
                    attr = feature_to_attr.get(feat)
                    if attr and hasattr(defl, attr):
                        setattr(defl, attr, coeff)
                if direct_flag_attr and hasattr(defl, direct_flag_attr):
                    setattr(defl, direct_flag_attr, True)
                return True

            # Rear shock defl static (intercept + pushrod + compliance + perches)
            rs = models.rear_shock_defl_static
            _REAR_SHOCK_MAP = {
                "rear_pushrod": "shock_rear_pushrod_coeff",
                "inv_rear_third": "rear_shock_defl_inv_third_coeff",
                "inv_rear_spring": "rear_shock_defl_inv_spring_coeff",
                "rear_third_perch": "rear_shock_defl_third_perch_coeff",
                "rear_spring_perch": "rear_shock_defl_spring_perch_coeff",
            }
            if rs and len(rs.coefficients) >= 2:
                _has_compliance = any(
                    f in (rs.feature_names or [])
                    for f in ("inv_rear_third", "inv_rear_spring",
                              "rear_third_perch", "rear_spring_perch")
                )
                if _has_compliance:
                    _apply_named(rs, "shock_rear_intercept", _REAR_SHOCK_MAP)
                    defl.rear_shock_defl_direct = True
                else:
                    # Legacy: just intercept + pushrod coefficient
                    defl.shock_rear_intercept = rs.coefficients[0]
                    if len(rs.coefficients) >= 2:
                        defl.shock_rear_pushrod_coeff = rs.coefficients[1]
                    defl.rear_shock_defl_direct = False
                _defl_applied = True

            # Heave spring deflection static (reciprocal fit)
            hs = models.heave_spring_defl_static
            if hs and len(hs.coefficients) >= 4:
                defl.heave_defl_intercept = hs.coefficients[0]
                defl.heave_defl_inv_heave_coeff = hs.coefficients[1]
                defl.heave_defl_perch_coeff = hs.coefficients[2]
                defl.heave_defl_inv_od4_coeff = hs.coefficients[3]
                _defl_applied = True

            # Rear spring deflection static (compliance + perches + pushrod)
            _REAR_SPRING_DEFL_MAP = {
                "rear_spring": "rear_spring_defl_rate_coeff",
                "inv_rear_spring": "rear_spring_defl_inv_rate_coeff",
                "rear_third": "rear_spring_defl_third_coeff",
                "inv_rear_third": "rear_spring_defl_inv_third_coeff",
                "rear_spring_perch": "rear_spring_defl_perch_coeff",
                "rear_third_perch": "rear_spring_defl_third_perch_coeff",
                "rear_pushrod": "rear_spring_defl_pushrod_coeff",
            }
            rsd = models.rear_spring_defl_static
            if rsd:
                # Only enable direct mode when the fit includes the
                # compliance terms; otherwise fall back to legacy load-balance.
                _has_compliance = any(
                    f in (rsd.feature_names or [])
                    for f in ("inv_rear_spring", "inv_rear_third")
                )
                if _has_compliance:
                    _apply_named(rsd, "rear_spring_defl_intercept",
                                 _REAR_SPRING_DEFL_MAP,
                                 direct_flag_attr="rear_spring_defl_direct")
                else:
                    # Legacy load-balance form; set perch coeff and eff load.
                    if "rear_spring_perch" in (rsd.feature_names or []):
                        defl.rear_spring_eff_load = rsd.coefficients[0]
                        defl.rear_spring_perch_coeff = rsd.coefficients[1]
                    defl.rear_spring_defl_direct = False
                _defl_applied = True

            # Third spring deflection static (same compliance pattern)
            _THIRD_SPRING_DEFL_MAP = {
                "rear_third": "third_spring_defl_third_coeff",
                "inv_rear_third": "third_spring_defl_inv_third_coeff",
                "rear_spring": "third_spring_defl_spring_coeff",
                "inv_rear_spring": "third_spring_defl_inv_spring_coeff",
                "rear_third_perch": "third_spring_defl_perch_coeff",
                "rear_spring_perch": "third_spring_defl_spring_perch_coeff",
                "rear_pushrod": "third_spring_defl_pushrod_coeff",
            }
            tsd = models.third_spring_defl_static
            if tsd:
                _has_compliance = any(
                    f in (tsd.feature_names or [])
                    for f in ("inv_rear_third", "inv_rear_spring")
                )
                if _has_compliance:
                    _apply_named(tsd, "third_spring_defl_intercept",
                                 _THIRD_SPRING_DEFL_MAP,
                                 direct_flag_attr="third_spring_defl_direct")
                else:
                    if "rear_third_perch" in (tsd.feature_names or []):
                        defl.third_spring_eff_load = tsd.coefficients[0]
                        defl.third_spring_perch_coeff = tsd.coefficients[1]
                    defl.third_spring_defl_direct = False
                _defl_applied = True

            # Torsion bar deflection (load-balance model: defl = load / k_torsion)
            # The fit stores y = defl * OD^4; the DeflectionModel uses
            # load / (C * OD^4), so coefficients must be scaled by C_torsion.
            tbd = models.torsion_bar_defl
            if tbd and len(tbd.coefficients) >= 3:
                C_torsion = car_obj.corner_spring.front_torsion_c
                if C_torsion > 0:
                    defl.tb_load_intercept = tbd.coefficients[0] * C_torsion
                    for i, feat in enumerate(tbd.feature_names):
                        if i + 1 < len(tbd.coefficients):
                            if feat == "front_heave":
                                defl.tb_load_heave_coeff = tbd.coefficients[i + 1] * C_torsion
                            elif feat == "front_heave_perch":
                                defl.tb_load_perch_coeff = tbd.coefficients[i + 1] * C_torsion
                    _defl_applied = True

            # Rear spring deflection max (intercept + rate_coeff * rate + perch_coeff * perch)
            rsm = models.rear_spring_defl_max
            if rsm and len(rsm.coefficients) >= 2:
                defl.rear_spring_defl_max_intercept = rsm.coefficients[0]
                for i, feat in enumerate(rsm.feature_names):
                    if i + 1 < len(rsm.coefficients):
                        if feat in ("rear_spring", "inv_rear_spring"):
                            defl.rear_spring_defl_max_rate_coeff = rsm.coefficients[i + 1]
                        elif feat == "rear_spring_perch":
                            defl.rear_spring_defl_max_perch_coeff = rsm.coefficients[i + 1]
                _defl_applied = True

            # Third spring deflection max
            tsm = models.third_spring_defl_max
            if tsm and len(tsm.coefficients) >= 2:
                defl.third_spring_defl_max_intercept = tsm.coefficients[0]
                for i, feat in enumerate(tsm.feature_names):
                    if i + 1 < len(tsm.coefficients):
                        if feat in ("rear_third", "inv_rear_third"):
                            defl.third_spring_defl_max_rate_coeff = tsm.coefficients[i + 1]
                        elif feat == "rear_third_perch":
                            defl.third_spring_defl_max_perch_coeff = tsm.coefficients[i + 1]
                _defl_applied = True

            # Third slider deflection static
            tsl = models.third_slider_defl_static
            if tsl and len(tsl.coefficients) >= 2:
                defl.third_slider_intercept = tsl.coefficients[0]
                if tsl.feature_names and tsl.feature_names[0] in (
                        "third_spring_defl_static", "third_defl_static",
                        "third_spring_defl"):
                    defl.third_slider_spring_defl_coeff = tsl.coefficients[1]
                _defl_applied = True

            # Heave spring deflection max
            hdm = models.heave_spring_defl_max
            if hdm and len(hdm.coefficients) >= 2:
                defl_max_intercept_val = hdm.coefficients[0]
                defl_max_slope_val = 0.0
                for i, feat in enumerate(hdm.feature_names):
                    if feat == "front_heave" and i + 1 < len(hdm.coefficients):
                        defl_max_slope_val = hdm.coefficients[i + 1]
                # These are on the GarageOutputModel, not DeflectionModel.
                # They'll be picked up by build_garage_output_model() later.

            if _defl_applied:
                defl.is_calibrated = True
                applied.append(f"DeflectionModel updated from {models.n_unique_setups} IBT sessions")
        except AttributeError:
            pass

    # Apply RideHeightModel coefficients
    # Map calibration feature names to RideHeightModel attribute names
    _FRONT_RH_COEFF_MAP = {
        "front_heave": "front_coeff_heave_nmm",
        "inv_front_heave": "front_coeff_inv_heave",
        "front_camber": "front_coeff_camber_deg",
        "front_pushrod": "front_coeff_pushrod",
        "front_heave_perch": "front_coeff_perch",
        "torsion_od": "front_coeff_torsion_od",
    }
    _REAR_RH_COEFF_MAP = {
        "rear_pushrod": "rear_coeff_pushrod",
        "rear_third": "rear_coeff_third_nmm",
        "inv_rear_third": "rear_coeff_inv_third",
        "rear_spring": "rear_coeff_rear_spring",
        "inv_rear_spring": "rear_coeff_inv_spring",
        "rear_third_perch": "rear_coeff_heave_perch",
        "fuel": "rear_coeff_fuel_l",
        "rear_spring_perch": "rear_coeff_spring_perch",
    }
    if models.front_ride_height and models.rear_ride_height:
        try:
            rh = car_obj.ride_height_model
            fr = models.front_ride_height
            if len(fr.coefficients) >= 2:
                # Zero ALL front coefficients first so stale values from
                # cars.py don't contaminate the new model's predictions.
                for attr in _FRONT_RH_COEFF_MAP.values():
                    if hasattr(rh, attr):
                        setattr(rh, attr, 0.0)
                intercept = fr.coefficients[0]
                # Apply mapped coefficients (coefficients[1:] match feature_names order)
                # Unmapped features get their contribution baked into the intercept
                # using the mean calibration value from the dataset.
                _UNMAPPED_DEFAULTS = {
                    "fuel": 58.0,       # calibration fuel level
                }
                for i, feat in enumerate(fr.feature_names):
                    coeff = fr.coefficients[i + 1] if (i + 1) < len(fr.coefficients) else 0.0
                    attr = _FRONT_RH_COEFF_MAP.get(feat)
                    if attr and hasattr(rh, attr):
                        setattr(rh, attr, coeff)
                    elif feat in _UNMAPPED_DEFAULTS:
                        # Absorb constant feature into intercept
                        intercept += coeff * _UNMAPPED_DEFAULTS[feat]
                rh.front_intercept = intercept
            rr = models.rear_ride_height
            if len(rr.coefficients) >= 2:
                # Zero ALL rear coefficients first to prevent stale values
                for attr in _REAR_RH_COEFF_MAP.values():
                    if hasattr(rh, attr):
                        setattr(rh, attr, 0.0)
                rh.rear_intercept = rr.coefficients[0]
                for i, feat in enumerate(rr.feature_names):
                    attr = _REAR_RH_COEFF_MAP.get(feat)
                    if attr and hasattr(rh, attr) and (i + 1) < len(rr.coefficients):
                        setattr(rh, attr, rr.coefficients[i + 1])
            rh.is_calibrated = True
            applied.append(f"RideHeightModel updated (front R²={fr.r_squared:.2f}, rear R²={rr.r_squared:.2f})")
        except AttributeError:
            pass

    # Apply m_eff (front and rear)
    if models.m_eff_front_kg is not None:
        try:
            car_obj.heave_spring.front_m_eff_kg = models.m_eff_front_kg
            applied.append(f"m_eff_front updated: {models.m_eff_front_kg:.0f} kg")
        except AttributeError:
            pass
    if models.m_eff_rear_kg is not None:
        try:
            car_obj.heave_spring.rear_m_eff_kg = models.m_eff_rear_kg
            applied.append(f"m_eff_rear updated: {models.m_eff_rear_kg:.0f} kg")
        except AttributeError:
            pass

    # Apply rate-dependent m_eff tables (front and rear). When the calibration
    # data shows significant variation with spring rate, populate the lookup
    # table so the heave solver interpolates rather than using a scalar mean.
    if getattr(models, "m_eff_rate_table", None):
        try:
            # models.m_eff_rate_table was historically populated only for front;
            # we preserve that for backward compatibility but also distribute
            # to whichever axle it belongs to based on status note.
            status_note = models.status.get("m_eff", "") if hasattr(models, "status") else ""
            front_table = list(models.m_eff_rate_table)
            car_obj.heave_spring.m_eff_front_rate_table = front_table
            applied.append(
                f"m_eff_front rate table: {len(front_table)} points "
                f"({status_note})"
            )
        except (AttributeError, TypeError):
            pass
    if getattr(models, "m_eff_rear_rate_table", None):
        try:
            rear_table = list(models.m_eff_rear_rate_table)
            car_obj.heave_spring.m_eff_rear_rate_table = rear_table
            applied.append(
                f"m_eff_rear rate table: {len(rear_table)} points"
            )
        except (AttributeError, TypeError):
            pass

    # Apply measured LLTD target only when explicitly allowed by the status.
    lltd_status = models.status.get("lltd_target", "") if hasattr(models, "status") else ""
    if models.measured_lltd_target is not None and not str(lltd_status).startswith("DISABLED"):
        try:
            car_obj.measured_lltd_target = models.measured_lltd_target
            applied.append(f"LLTD target updated: {models.measured_lltd_target:.3f}")
        except AttributeError:
            pass

    # Apply aero compression (Model 3)
    if models.aero_front_compression_mm is not None:
        try:
            car_obj.aero_compression.front_compression_mm = models.aero_front_compression_mm
            if models.aero_rear_compression_mm is not None:
                car_obj.aero_compression.rear_compression_mm = models.aero_rear_compression_mm
            car_obj.aero_compression.is_calibrated = True
            _rear_str = (f"{models.aero_rear_compression_mm:.1f}mm"
                         if models.aero_rear_compression_mm is not None else "n/a")
            applied.append(
                f"AeroCompression updated: front={models.aero_front_compression_mm:.1f}mm "
                f"rear={_rear_str} "
                f"({models.aero_n_sessions} sessions)"
            )
        except AttributeError:
            pass

    # Apply damper zeta targets (from calibrate_dampers or fastest-session analysis)
    if models.front_ls_zeta is not None and models.rear_ls_zeta is not None:
        try:
            car_obj.damper.zeta_ls_comp = models.front_ls_zeta
            car_obj.damper.zeta_ls_rbd = models.rear_ls_zeta
            car_obj.damper.zeta_target_ls_front = models.front_ls_zeta
            car_obj.damper.zeta_target_ls_rear = models.rear_ls_zeta
            if models.front_hs_zeta is not None:
                car_obj.damper.zeta_hs_comp = models.front_hs_zeta
                car_obj.damper.zeta_target_hs_front = models.front_hs_zeta
            if models.rear_hs_zeta is not None:
                car_obj.damper.zeta_hs_rbd = models.rear_hs_zeta
                car_obj.damper.zeta_target_hs_rear = models.rear_hs_zeta
            car_obj.damper.zeta_is_calibrated = True
            applied.append(
                f"Damper zeta calibrated: LS front={models.front_ls_zeta:.2f}, "
                f"LS rear={models.rear_ls_zeta:.2f} ({models.zeta_n_sessions} sessions)"
            )
        except AttributeError:
            pass

    # Apply ARB calibration flag (set by ARB back-solve in fit_models_from_points)
    if models.status.get("arb_calibrated"):
        try:
            car_obj.arb.is_calibrated = True
            applied.append("ARB stiffness calibrated from roll gradient data")
        except AttributeError:
            pass

    # Apply roll gain calibration
    if models.status.get("roll_gains_calibrated"):
        try:
            front_rg = models.status.get("roll_gain_front")
            rear_rg = models.status.get("roll_gain_rear")
            if front_rg is not None:
                car_obj.geometry.front_roll_gain = float(front_rg)
            if rear_rg is not None:
                car_obj.geometry.rear_roll_gain = float(rear_rg)
            car_obj.geometry.roll_gains_calibrated = True
            applied.append(f"Roll gains calibrated: front={front_rg}, rear={rear_rg}")
        except AttributeError:
            pass

    # Apply spring lookup tables
    if models.front_torsion_lookup and models.front_torsion_lookup.is_calibrated:
        try:
            car_obj.heave_spring._auto_lookup_front = models.front_torsion_lookup.entries
            applied.append(f"Front spring lookup: {len(models.front_torsion_lookup.entries)} entries")
        except AttributeError:
            pass

    if models.rear_torsion_lookup and models.rear_torsion_lookup.is_calibrated:
        try:
            car_obj.heave_spring._auto_lookup_rear = models.rear_torsion_lookup.entries
            applied.append(f"Rear spring lookup: {len(models.rear_torsion_lookup.entries)} entries")
        except AttributeError:
            pass

    # Back-calculate the C constant (k = C·OD⁴) from the calibrated front torsion
    # lookup so that CornerSpringModel.torsion_bar_rate(od_mm) returns correct calibrated
    # wheel rates rather than using the initial physics-derived C from cars.py.
    #
    # This is needed for indexed torsion bar cars (Ferrari, Acura) where cars.py
    # stores a C constant derived from garage deflection screenshots, but the STO/telemetry
    # calibration produces a different (more accurate) C constant. Without this update,
    # the corner spring solver calls torsion_bar_rate(od_mm) and gets the wrong rate.
    if (
        models.front_torsion_lookup
        and models.front_torsion_lookup.is_calibrated
        and getattr(car_obj, "canonical_name", "") in _INDEXED_SPRING_OD_MAPS
    ):
        try:
            car_map = _INDEXED_SPRING_OD_MAPS[car_obj.canonical_name].get("front")
            if car_map:
                # Use all calibrated entries (STO-verified or physics-extrapolated)
                entries = [
                    e for e in models.front_torsion_lookup.entries
                    if e.get("source") in ("decrypted_sto", "physics_extrapolated")
                ]
                if entries:
                    # Fit C via no-intercept least squares: k = C·OD⁴
                    od4_arr = np.array([
                        _index_to_od(e["setting"], car_map["index_range"], car_map["od_range_mm"]) ** 4
                        for e in entries
                    ])
                    k_arr = np.array([e["rate_nmm"] for e in entries])
                    C_calibrated = float(np.dot(od4_arr, k_arr) / np.dot(od4_arr, od4_arr))
                    old_C = car_obj.corner_spring.front_torsion_c
                    car_obj.corner_spring.front_torsion_c = C_calibrated
                    applied.append(
                        f"corner_spring.front_torsion_c updated: "
                        f"{old_C:.7f} → {C_calibrated:.7f} "
                        f"(from {len(entries)}-entry calibrated lookup)"
                    )
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("Spring lookup application failed: %s", e)

    # Auto-build GarageOutputModel from calibration regressions if the car
    # doesn't already have one. This enables RH/pushrod reconciliation and
    # legality validation for non-BMW cars that have been calibrated.
    # NOTE: The auto-built model is used for RH prediction and pushrod inversion
    # but NOT for heave spring travel budget constraints — those use the physics-
    # only path because the auto-built deflection models haven't been validated
    # against full garage sweep data. Hand-calibrated garage models (BMW) override this.
    if car_obj.garage_output_model is None:
        garage_model = build_garage_output_model(car_obj, models)
        if garage_model is not None:
            # Mark as auto-built so the heave solver knows to use physics path for travel
            garage_model._auto_built = True
            car_obj.garage_output_model = garage_model
            applied.append(
                f"GarageOutputModel auto-built from calibration "
                f"(front RH RMSE={car_obj.ride_height_model.front_loo_rmse_mm:.2f}mm)"
            )
    else:
        # Existing GOM (e.g. BMW hand-calibrated): update its deflection model
        # reference to the calibrated one so deflection predictions use fitted
        # coefficients instead of empty defaults.
        gom = car_obj.garage_output_model
        _car_defl = car_obj.deflection
        if _car_defl.is_calibrated:
            # Always replace — car.deflection has fitted coefficients from
            # calibration data while gom.deflection may be a default stub.
            gom.deflection = _car_defl
            applied.append("GarageOutputModel.deflection updated to calibrated model")
        # Update heave_defl_max from calibration if available
        if models.heave_spring_defl_max and models.heave_spring_defl_max.is_calibrated:
            hdm = models.heave_spring_defl_max
            gom.heave_spring_defl_max_intercept_mm = hdm.coefficients[0]
            for i, feat in enumerate(hdm.feature_names):
                if feat == "front_heave" and i + 1 < len(hdm.coefficients):
                    gom.heave_spring_defl_max_slope = hdm.coefficients[i + 1]
            applied.append(
                f"GarageOutputModel heave_defl_max updated "
                f"(intercept={gom.heave_spring_defl_max_intercept_mm:.2f}, "
                f"slope={gom.heave_spring_defl_max_slope:.4f})"
            )

    return applied


# ─────────────────────────────────────────────────────────────────────────────
# Sweep protocol generator (Model 3 of Claude Code's plan)
# ─────────────────────────────────────────────────────────────────────────────

_CAR_PROTOCOL_HINTS: dict[str, dict] = {
    "ferrari": {
        "spring_param": "front torsion bar OD",
        "spring_range_low": "index 2",
        "spring_range_mid": "index 9",
        "spring_range_high": "index 16",
        "heave_param": "front heave spring",
        "heave_range_low": "index 1",
        "heave_range_high": "index 7",
        "pushrod_delta": "+8mm and -8mm",
        "extra_note": "Ferrari: torsion bars affect ALL 4 corners. Change one index at a time.",
    },
    "acura": {
        "spring_param": "front torsion bar OD",
        "spring_range_low": "13.90mm",
        "spring_range_mid": "14.76mm",
        "spring_range_high": "15.86mm",
        "heave_param": "front heave spring",
        "heave_range_low": "90 N/mm",
        "heave_range_high": "280 N/mm",
        "pushrod_delta": "+10mm and -10mm",
        "extra_note": "Acura: front heave damper is always bottomed — this is normal. Don't change it.",
    },
    "cadillac": {
        "spring_param": "front torsion bar OD",
        "spring_range_low": "13.90mm",
        "spring_range_mid": "15.14mm",
        "spring_range_high": "16.51mm",
        "heave_param": "front heave spring",
        "heave_range_low": "30 N/mm",
        "heave_range_high": "120 N/mm",
        "pushrod_delta": "+5mm and -5mm",
        "extra_note": "Cadillac shares Dallara platform with BMW but needs its own compression calibration.",
    },
    "porsche": {
        "spring_param": "front torsion bar OD",
        "spring_range_low": "13.90mm",
        "spring_range_mid": "15.50mm",
        "spring_range_high": "17.50mm",
        "heave_param": "front heave spring",
        "heave_range_low": "30 N/mm",
        "heave_range_high": "130 N/mm",
        "pushrod_delta": "+5mm and -5mm",
        "extra_note": "Porsche uses Multimatic chassis (different from Dallara). Needs from-scratch calibration.",
    },
    "bmw": {
        "spring_param": "front torsion bar OD",
        "spring_range_low": "13.90mm",
        "spring_range_mid": "15.14mm",
        "spring_range_high": "16.81mm",
        "heave_param": "front heave spring",
        "heave_range_low": "30 N/mm",
        "heave_range_high": "90 N/mm",
        "pushrod_delta": "+3mm and -3mm",
        "extra_note": "BMW is fully calibrated. Running this protocol validates the auto-calibration accuracy.",
    },
}


def generate_protocol(car: str, verbose: bool = True) -> str:
    """Generate step-by-step iRacing calibration sweep instructions.

    Based on Claude Code's calibration plan (docs/auto_calibration_plan.md):
    Each step changes ONE parameter to isolate physics effects.
    """
    points = load_calibration_points(car)
    models = load_calibrated_models(car)
    hints = _CAR_PROTOCOL_HINTS.get(car, _CAR_PROTOCOL_HINTS["bmw"])

    unique: set[tuple] = set()
    for pt in points:
        unique.add(_setup_key(pt))
    n_unique = len(unique)

    # Assess what's calibrated
    has_aero = models is not None and models.aero_front_compression_mm is not None
    has_defl = models is not None and models.heave_spring_defl_static is not None
    has_rh = models is not None and models.front_ride_height is not None
    has_spring_lookup = models is not None and models.front_torsion_lookup is not None
    has_meff = models is not None and models.m_eff_front_kg is not None

    lines = []
    lines.append(f"\n{'=' * 60}")
    lines.append(f"  {car.upper()} Calibration Protocol")
    lines.append(f"  (Based on IOptimal auto-calibration plan)")
    lines.append(f"{'=' * 60}")
    lines.append(f"\nCurrent calibration status:")
    lines.append(f"  [{'OK' if has_aero  else '!!'}] Aero compression:     {'CALIBRATED' if has_aero  else 'needs calibration'}")
    lines.append(f"  [{'OK' if has_defl  else '!!'}] Deflection models:    {'CALIBRATED' if has_defl  else 'needs calibration'}")
    lines.append(f"  [{'OK' if has_rh    else '!!'}] Ride height model:    {'CALIBRATED' if has_rh    else 'needs calibration'}")
    lines.append(f"  [{'OK' if has_spring_lookup else '!!'}] Spring lookup table: {'CALIBRATED' if has_spring_lookup else 'needs calibration'}")
    lines.append(f"  [{'OK' if has_meff  else '~~'}] Effective mass (m_eff): {'CALIBRATED' if has_meff else 'ESTIMATED (optional)'}")
    lines.append(f"\n  {n_unique} unique setups collected so far (need {_MIN_SESSIONS_FOR_FIT} minimum)")

    if hints.get("extra_note"):
        lines.append(f"\n  ⚠️  {hints['extra_note']}")

    if has_aero and has_defl and has_rh and has_spring_lookup:
        lines.append(f"\n  ✅ All critical models calibrated! Only m_eff improvement remains.")
        if not has_meff:
            lines.append(f"\n  Optional: Run 3+ sessions varying {hints['heave_param']}")
            lines.append(f"  ({hints['heave_range_low']}, middle, {hints['heave_range_high']})")
            lines.append(f"  to calibrate m_eff for more accurate heave spring sizing.")
        lines.append(f"\n{'=' * 60}\n")
        return "\n".join(lines)

    lines.append(f"\n{'─' * 60}")
    lines.append(f"QUICK SWEEP (~30 min in iRacing):")
    lines.append(f"  Calibrates: aero compression + torsion bar C + deflection model + RH model")
    lines.append(f"{'─' * 60}")

    step = 1
    lines.append(f"\n  Preparation:")
    lines.append(f"  - Open iRacing → Practice at any permanent track (Sebring recommended)")
    lines.append(f"  - Load a known baseline setup")
    lines.append(f"  - Make sure you complete 3+ CLEAN laps per configuration")
    lines.append(f"  - Save the IBT after each run (iRacing does this automatically)")

    lines.append(f"\n  The Sweep:")

    lines.append(f"\n  Step {step}: BASELINE (current setup as-is)")
    lines.append(f"    → Drive 3 clean laps → IBT auto-saved → proceed to Step {step+1}")
    step += 1

    if not has_spring_lookup:
        lines.append(f"\n  Step {step}: Change {hints['spring_param']} to {hints['spring_range_low']}")
        lines.append(f"    → Keep EVERYTHING else the same")
        lines.append(f"    → Drive 3 clean laps → IBT auto-saved")
        step += 1

        lines.append(f"\n  Step {step}: Change {hints['spring_param']} to {hints['spring_range_mid']}")
        lines.append(f"    → Drive 3 clean laps → IBT auto-saved")
        step += 1

        lines.append(f"\n  Step {step}: Change {hints['spring_param']} to {hints['spring_range_high']}")
        lines.append(f"    → Drive 3 clean laps → IBT auto-saved")
        step += 1

    if not has_rh:
        lines.append(f"\n  Step {step}: Reset to baseline. Change front pushrod by {hints['pushrod_delta']}")
        lines.append(f"    → Drive 3 clean laps → IBT auto-saved")
        step += 1

    if not has_defl:
        lines.append(f"\n  Step {step}: Change {hints['heave_param']} to {hints['heave_range_low']}")
        lines.append(f"    → Drive 3 clean laps → IBT auto-saved")
        step += 1

    lines.append(f"\n  After: run the following command:")
    lines.append(f"  python -m ioptimal calibrate --car {car} --ibt-dir ~/Documents/iRacing/telemetry/")
    lines.append(f"\n  Expected: aero compression + torsion bar + deflection + RH models calibrated")

    lines.append(f"\n{'─' * 60}")
    lines.append(f"FULL SWEEP (~60 min): Adds deflection model + m_eff calibration")
    lines.append(f"{'─' * 60}")
    lines.append(f"\n  Do all Quick Sweep steps above, plus:")
    lines.append(f"\n  Step {step}: Change heave perch to max negative value (e.g., -50mm)")
    lines.append(f"    → Drive 3 clean laps → IBT auto-saved")
    step += 1

    lines.append(f"\n  Step {step}: Change heave perch to mid-range")
    lines.append(f"    → Drive 3 clean laps → IBT auto-saved")
    step += 1

    lines.append(f"\n  Step {step}: Change rear pushrod by +8mm (affects rear RH model)")
    lines.append(f"    → Drive 3 clean laps → IBT auto-saved")
    step += 1

    lines.append(f"\n  After: run:")
    lines.append(f"  python -m ioptimal calibrate --car {car} --ibt-dir ~/Documents/iRacing/telemetry/")
    lines.append(f"\n  Expected: ALL 7 models calibrated")

    lines.append(f"\n{'─' * 60}")
    lines.append(f"EXISTING FILES PATH: If you have past IBT sessions, try pointing at them first.")
    lines.append(f"  python -m ioptimal calibrate --car {car} --ibt-dir ~/Documents/iRacing/telemetry/")
    lines.append(f"  (Natural setup variation from racing may already cover most of the sweep)")
    lines.append(f"{'=' * 60}\n")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Status reporting
# ─────────────────────────────────────────────────────────────────────────────

def calibration_status(car: str) -> dict[str, Any]:
    """Return a status dict summarizing calibration completeness for a car."""
    points = load_calibration_points(car)
    models = load_calibrated_models(car)

    unique: set[tuple] = set()
    for pt in points:
        unique.add(_setup_key(pt))

    status: dict[str, Any] = {
        "car": car,
        "n_sessions": len(points),
        "n_unique_setups": len(unique),
        "min_sessions_needed": _MIN_SESSIONS_FOR_FIT,
        "ready_to_calibrate": len(unique) >= _MIN_SESSIONS_FOR_FIT,
        "models_fitted": models is not None and models.calibration_complete,
        "component_status": models.status if models else {},
    }

    if models:
        # Report each fitted model
        regression_fields = [
            "front_ride_height", "rear_ride_height",
            "front_shock_defl_static", "rear_shock_defl_static",
            "heave_spring_defl_static", "heave_spring_defl_max",
        ]
        fitted_models = {}
        for fname in regression_fields:
            m = getattr(models, fname, None)
            if m is not None:
                fitted_models[fname] = {
                    "r_squared": round(m.r_squared, 3),
                    "rmse": round(m.rmse, 3),
                    "n": m.n_samples,
                }
        status["fitted_models"] = fitted_models

        # Spring lookups
        lookup_info = {}
        for lname in ["front_heave_lookup", "rear_heave_lookup", "front_torsion_lookup", "rear_torsion_lookup"]:
            lut = getattr(models, lname, None)
            if lut is not None:
                lookup_info[lname] = {
                    "entries": len(lut.entries),
                    "method": lut.method,
                    "is_calibrated": lut.is_calibrated,
                }
        status["spring_lookups"] = lookup_info
        status["m_eff_front_kg"] = models.m_eff_front_kg
        status["lltd_target"] = models.measured_lltd_target
        status["lltd_target_status"] = models.status.get("lltd_target")

    # Recommendations
    recommendations = []
    if len(unique) < _MIN_SESSIONS_FOR_FIT:
        remaining = _MIN_SESSIONS_FOR_FIT - len(unique)
        recommendations.append(
            f"Need {remaining} more unique-setup sessions. "
            f"Vary your heave springs, torsion bars, and pushrods across sessions."
        )
    if models and not models.front_torsion_lookup:
        recommendations.append(
            "No spring lookup table yet. Either: (a) provide a setupdelta.com JSON "
            "via --sto-json flag, or (b) run 3+ sessions with different torsion bar OD settings."
        )
    if models and models.calibration_complete:
        recommendations.append(
            f"Calibration complete! {len(unique)} unique setups. "
            "The solver will use your car-specific models automatically."
        )

    status["recommendations"] = recommendations
    return status


def print_status(car: str) -> None:
    """Print a human-readable calibration status report."""
    s = calibration_status(car)
    print(f"\n{'=' * 60}")
    print(f"  IOptimal Calibration Status: {car.upper()}")
    print(f"{'=' * 60}")
    print(f"  Sessions collected:   {s['n_sessions']}")
    print(f"  Unique setups:        {s['n_unique_setups']} / {s['min_sessions_needed']} minimum")
    print(f"  Ready to calibrate:   {'YES' if s['ready_to_calibrate'] else 'NO'}")
    print(f"  Models fitted:        {'YES' if s['models_fitted'] else 'NO'}")

    if s.get("fitted_models"):
        print(f"\n  Regression models:")
        for name, m in s["fitted_models"].items():
            bar = "✅" if m["r_squared"] >= 0.80 else ("⚠️" if m["r_squared"] >= 0.50 else "❌")
            print(f"    {bar} {name:<35} R²={m['r_squared']:.3f}  RMSE={m['rmse']:.2f}  n={m['n']}")

    if s.get("spring_lookups"):
        print(f"\n  Spring lookup tables:")
        for name, lut in s["spring_lookups"].items():
            bar = "✅" if lut["is_calibrated"] else "⏳"
            print(f"    {bar} {name:<25} {lut['entries']} entries  method={lut['method']}")

    if s.get("m_eff_front_kg"):
        print(f"\n  m_eff_front:    {s['m_eff_front_kg']:.0f} kg (calibrated)")
    if s.get("lltd_target") is not None:
        print(f"  LLTD target:    {s['lltd_target']:.3f} (calibrated)")
    elif s.get("lltd_target_status"):
        print(f"  LLTD target:    {s['lltd_target_status']}")

    if s.get("component_status"):
        print(f"\n  Component status:")
        for comp, status_str in s["component_status"].items():
            print(f"    {comp}: {status_str}")

    if s["recommendations"]:
        print(f"\n  Recommendations:")
        for rec in s["recommendations"]:
            print(f"    → {rec}")
    print(f"{'=' * 60}\n")


# ─────────────────────────────────────────────────────────────────────────────
# CLI entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto-calibrate IOptimal car models from IBT sessions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Calibrate from a directory of IBT files:
  python -m car_model.auto_calibrate --car ferrari --ibt-dir data/telemetry/

  # Calibrate from explicit IBT files:
  python -m car_model.auto_calibrate --car ferrari --ibt s1.ibt s2.ibt s3.ibt

  # Add setupdelta.com JSON for spring lookup table:
  python -m car_model.auto_calibrate --car ferrari --sto-json decoded.json

  # Check calibration status:
  python -m car_model.auto_calibrate --car ferrari --status
""",
    )
    parser.add_argument("--car", required=True,
                        choices=["bmw", "cadillac", "ferrari", "acura", "porsche"],
                        help="Car to calibrate")
    parser.add_argument("--ibt", nargs="+", default=None,
                        help="One or more IBT files to add to the calibration dataset")
    parser.add_argument("--ibt-dir", default=None,
                        help="Directory to scan for IBT files matching the car name")
    parser.add_argument("--sto-json", default=None,
                        help="Path to a setupdelta.com decrypted .sto JSON file (for spring lookup table)")
    parser.add_argument("--status", action="store_true",
                        help="Print calibration status for the car and exit")
    parser.add_argument("--refit", action="store_true",
                        help="Re-fit models from all accumulated data points")
    parser.add_argument("--clear", action="store_true",
                        help="Clear all calibration data for the car")
    parser.add_argument("--protocol", action="store_true",
                        help="Generate per-car calibration sweep instructions (what to do in iRacing)")
    args = parser.parse_args()

    car = args.car

    if args.clear:
        pts_path = _points_path(car)
        mdls_path = _models_path(car)
        if pts_path.exists():
            pts_path.unlink()
        if mdls_path.exists():
            mdls_path.unlink()
        print(f"Cleared calibration data for {car}.")
        return

    if args.status:
        print_status(car)
        return

    if args.protocol:
        print(generate_protocol(car))
        return

    # Collect IBT files to process
    ibt_paths: list[Path] = []
    if args.ibt:
        for p in args.ibt:
            path = Path(p)
            if path.exists():
                ibt_paths.append(path)
            else:
                print(f"  [warn] IBT file not found: {p}", file=sys.stderr)

    if args.ibt_dir:
        ibt_dir = Path(args.ibt_dir)
        for pattern in [f"*{car}*.ibt", "*.ibt"]:
            found = list(ibt_dir.glob(pattern))
            if found:
                ibt_paths.extend(found)
                break
        ibt_paths = list(set(ibt_paths))  # deduplicate

    # Load existing calibration points
    existing_points = load_calibration_points(car)
    existing_ids = {pt.session_id for pt in existing_points}
    existing_fingerprints = {_setup_key(pt) for pt in existing_points}
    new_points = list(existing_points)

    # Extract data from new IBT files
    added = 0
    new_unique = 0
    for ibt_path in sorted(ibt_paths):
        print(f"  Processing {ibt_path.name}...", end=" ")
        pt = extract_point_from_ibt(ibt_path, car)
        if pt is None:
            print("❌ (no valid data)")
            continue
        if pt.session_id in existing_ids:
            print("-> already in dataset")
            continue
        new_points.append(pt)
        existing_ids.add(pt.session_id)
        added += 1
        fp = _setup_key(pt)
        is_new_unique = fp not in existing_fingerprints
        if is_new_unique:
            new_unique += 1
            existing_fingerprints.add(fp)
        unique_tag = " 🆕 NEW unique setup!" if is_new_unique else ""
        print(f"✅ (heave={pt.front_heave_setting:.1f}, pushrod={pt.front_pushrod_mm:.1f}mm, RH={pt.static_front_rh_mm:.1f}/{pt.static_rear_rh_mm:.1f}mm){unique_tag}")

    if added > 0 or args.refit:
        save_calibration_points(car, new_points)
        print(f"\n  Dataset: {len(new_points)} total sessions ({added} new)")

    # Process setupdelta.com JSON if provided
    if args.sto_json:
        sto_path = Path(args.sto_json)
        if not sto_path.exists():
            print(f"  [error] STO JSON not found: {sto_path}", file=sys.stderr)
        else:
            print(f"\n  Processing setupdelta.com JSON: {sto_path.name}")
            sto_data = ingest_sto_json(car, sto_path)
            f_npm = sto_data.get("fSideSpringRateNpm", 0)
            r_npm = sto_data.get("rSideSpringRateNpm", 0)
            if f_npm > 0 or r_npm > 0:
                print(f"  Found spring rates: front={f_npm/1000:.1f} N/mm, rear={r_npm/1000:.1f} N/mm")

                # Auto-detect the garage settings from the JSON (Torsion bar O.D. rows)
                setting_f = sto_data.get("_front_torsion_od_setting")
                setting_r = sto_data.get("_rear_torsion_od_setting")

                if setting_f is not None:
                    print(f"  Auto-detected front torsion OD index: {setting_f:.0f} (from Torsion bar O.D. row)")
                else:
                    setting_f = float(input("  Enter the front torsion bar OD index used for that setup: ").strip())

                if setting_r is not None:
                    print(f"  Auto-detected rear torsion OD index: {setting_r:.0f} (from Torsion bar O.D. row)")
                else:
                    setting_r = float(input("  Enter the rear torsion bar OD index used for that setup: ").strip())

                lut_f, lut_r = build_spring_lookup_from_sto_json(car, sto_data, setting_f, setting_r)

                # Auto-expand remaining indices using k ∝ OD⁴ from the STO anchor(s)
                lut_f = expand_torsion_lookup_from_physics(car, lut_f, axle="front")
                lut_r = expand_torsion_lookup_from_physics(car, lut_r, axle="rear")

                existing_models = load_calibrated_models(car) or CarCalibrationModels(car=car)
                existing_models.front_torsion_lookup = lut_f
                existing_models.rear_torsion_lookup = lut_r
                save_calibrated_models(car, existing_models)
                n_sto_f = len([e for e in lut_f.entries if e.get("source") == "decrypted_sto"])
                n_sto_r = len([e for e in lut_r.entries if e.get("source") == "decrypted_sto"])
                print(f"  Updated spring lookup: front={len(lut_f.entries)} entries "
                      f"({n_sto_f} STO-verified + {len(lut_f.entries)-n_sto_f} physics-extrapolated), "
                      f"rear={len(lut_r.entries)} entries "
                      f"({n_sto_r} STO-verified + {len(lut_r.entries)-n_sto_r} physics-extrapolated)")
            else:
                print(f"  [warn] No fSideSpringRateNpm / rSideSpringRateNpm found in JSON", file=sys.stderr)

    # Fit models if enough data — use the canonical key for consistency
    unique: set[tuple] = set()
    for pt in new_points:
        unique.add(_setup_key(pt))

    n_unique = len(unique)
    if n_unique >= _MIN_SESSIONS_FOR_FIT or args.refit:
        print(f"\n  Fitting calibration models from {n_unique} unique setups...")
        models = fit_models_from_points(car, new_points)

        # Preserve fields from existing models.json that auto_calibrate doesn't compute
        # (e.g. zeta from calibrate_dampers, spring lookups from STO import)
        existing_saved = load_calibrated_models(car)
        if existing_saved:
            # Preserve damper zeta (set by validation/calibrate_dampers.py)
            if existing_saved.front_ls_zeta is not None and models.front_ls_zeta is None:
                models.front_ls_zeta = existing_saved.front_ls_zeta
                models.rear_ls_zeta = existing_saved.rear_ls_zeta
                models.front_hs_zeta = existing_saved.front_hs_zeta
                models.rear_hs_zeta = existing_saved.rear_hs_zeta
                models.zeta_n_sessions = existing_saved.zeta_n_sessions
            # Preserve spring lookup tables (and expand if they only have 1 entry)
            if existing_saved.front_torsion_lookup and not models.front_torsion_lookup:
                lut_f = existing_saved.front_torsion_lookup
                # Retroactively expand single-entry lookups with k proportional to OD^4 extrapolation
                n_before = len(lut_f.entries)
                lut_f = expand_torsion_lookup_from_physics(car, lut_f, axle="front")
                if len(lut_f.entries) > n_before:
                    print(f"  Expanded front torsion lookup: {n_before} -> {len(lut_f.entries)} entries")
                models.front_torsion_lookup = lut_f
            if existing_saved.rear_torsion_lookup and not models.rear_torsion_lookup:
                lut_r = existing_saved.rear_torsion_lookup
                n_before = len(lut_r.entries)
                lut_r = expand_torsion_lookup_from_physics(car, lut_r, axle="rear")
                if len(lut_r.entries) > n_before:
                    print(f"  Expanded rear torsion lookup: {n_before} -> {len(lut_r.entries)} entries")
                models.rear_torsion_lookup = lut_r
            # Merge status dict: preserve keys from previous runs that this run didn't compute
            # (e.g. roll_gains_calibrated from a previous run, arb status, etc.)
            for k, v in existing_saved.status.items():
                if k not in models.status:
                    models.status[k] = v

        save_calibrated_models(car, models)
        print(f"  [OK] Models saved to {_models_path(car)}")
    else:
        remaining = _MIN_SESSIONS_FOR_FIT - n_unique
        print(f"\n  ⏳ Need {remaining} more unique-setup sessions before fitting (have {n_unique}/{_MIN_SESSIONS_FOR_FIT})")
        print(f"     Tip: Run sessions with different heave springs, torsion bars, or pushrods")

    print_status(car)


if __name__ == "__main__":
    main()
