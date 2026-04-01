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
_MIN_SESSIONS_FOR_FIT = 5   # minimum unique-setup sessions before fitting
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
    m_eff_rate_table: list[dict] = field(default_factory=list)
    # Each entry: {"setting": float, "m_eff_kg": float}

    # Calibrated LLTD target
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
        setup = CurrentSetup.from_ibt(ibt)
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
    except Exception:
        # Telemetry extraction is optional for calibration; skip gracefully
        pass

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
    )


def _get_dummy_car(car_name: str):
    """Get a minimal car object for telemetry extraction."""
    try:
        from car_model.cars import get_car
        return get_car(car_name or "bmw")
    except Exception:
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
    loo_rmse = float(np.sqrt(np.mean(loo_errors ** 2)))

    return FittedModel(
        name=model_name,
        feature_names=feature_names,
        coefficients=beta.tolist(),
        r_squared=r2,
        rmse=rmse,
        loo_rmse=loo_rmse,
        n_samples=n,
        is_calibrated=True,
    )


def _col(rows: list[dict], key: str) -> np.ndarray:
    return np.array([r[key] for r in rows], dtype=float)


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
    except Exception:
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

    def col(name: str) -> np.ndarray:
        return _col(rows, name)

    heave = col("front_heave_setting")
    od4 = col("front_torsion_od_mm") ** 4

    # ─── 1. Front Ride Height ───
    if np.std(col("static_front_rh_mm")) > 0.5:
        X = np.column_stack([
            heave, col("front_heave_perch_mm"), col("front_camber_deg"),
            col("fuel_l"), col("front_pushrod_mm"), col("front_torsion_od_mm"),
        ])
        models.front_ride_height = _fit(
            X, col("static_front_rh_mm"),
            ["front_heave", "front_heave_perch", "front_camber", "fuel", "front_pushrod", "torsion_od"],
            "front_ride_height",
        )

    # ─── 2. Rear Ride Height ───
    if np.std(col("static_rear_rh_mm")) > 0.5:
        X = np.column_stack([
            col("rear_pushrod_mm"), col("rear_third_setting"),
            col("rear_spring_setting"), col("rear_third_perch_mm"),
            col("fuel_l"), col("rear_spring_perch_mm"),
        ])
        models.rear_ride_height = _fit(
            X, col("static_rear_rh_mm"),
            ["rear_pushrod", "rear_third", "rear_spring", "rear_third_perch", "fuel", "rear_spring_perch"],
            "rear_ride_height",
        )

    # ─── 3. Torsion Bar Turns ───
    if np.std(col("torsion_bar_turns")) > 0.005:
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
        # Convert heave settings to N/mm for indexed cars before taking reciprocal
        heave_nmm = heave.copy()
        if _car_obj is not None and _car_obj.heave_spring.front_setting_index_range is not None:
            for i in range(len(heave_nmm)):
                heave_nmm[i] = _car_obj.heave_spring.front_rate_from_setting(heave_nmm[i])
        X_recip = np.column_stack([
            1.0 / np.maximum(heave_nmm, 1.0),   # 1/heave_nmm — spring compliance
            heave_perch,                          # perch offset — load path
            1.0 / np.maximum(torsion_od ** 4, 1.0),  # 1/OD^4 — torsion bar compliance
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
        X = np.column_stack([
            col("front_pushrod_mm"),
            col("front_heave_perch_mm"),
            heave,
            col("front_torsion_od_mm"),
        ])
        models.front_shock_defl_static = _fit(
            X, col("front_shock_defl_static_mm"),
            ["front_pushrod", "front_heave_perch", "front_heave", "torsion_od"],
            "front_shock_defl_static",
        )

    # ─── 9. Rear Shock Deflection Static ───
    # Rear shock deflection depends on rear pushrod (geometric), rear third perch
    # (load path through third/heave spring vs. corner shock), and rear third
    # spring stiffness (how much the third spring carries).
    if np.std(col("rear_shock_defl_static_mm")) > 0.5:
        X = np.column_stack([
            col("rear_pushrod_mm"),
            col("rear_third_perch_mm"),
            col("rear_third_setting"),
            col("rear_spring_setting"),
        ])
        models.rear_shock_defl_static = _fit(
            X, col("rear_shock_defl_static_mm"),
            ["rear_pushrod", "rear_third_perch", "rear_third", "rear_spring"],
            "rear_shock_defl_static",
        )

    # ─── 10. Rear Spring Deflection Static ───
    if np.std(col("rear_spring_defl_static_mm")) > 0.5:
        y_load = col("rear_spring_defl_static_mm") * col("rear_spring_setting")
        X = np.column_stack([col("rear_spring_perch_mm")])
        models.rear_spring_defl_static = _fit(
            X, y_load,
            ["rear_spring_perch"],
            "rear_spring_defl_static_load",
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
    if np.std(col("third_spring_defl_static_mm")) > 0.5:
        y_load = col("third_spring_defl_static_mm") * col("rear_third_setting")
        X = np.column_stack([col("rear_third_perch_mm")])
        models.third_spring_defl_static = _fit(
            X, y_load,
            ["rear_third_perch"],
            "third_spring_defl_static_load",
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
    # Roll gradient = total_weight * cg_height / (K_arb_front + K_arb_rear + K_springs)
    # If we hold springs constant and vary ARB, delta in roll gradient gives ARB delta.
    # Requires: 2+ sessions with SAME springs but DIFFERENT ARB sizes.
    arb_calibration_points = []
    for pt in unique:
        if pt.aero_df_balance_pct > 0 and pt.front_arb_size and pt.front_arb_blade > 0:
            arb_key = (pt.front_arb_size, pt.front_arb_blade, pt.rear_arb_size, pt.rear_arb_blade)
            spring_key = (round(pt.front_heave_setting, 1), round(pt.front_torsion_od_mm, 3))
            arb_calibration_points.append({
                "arb_key": arb_key,
                "spring_key": spring_key,
                "front_size": pt.front_arb_size,
                "front_blade": pt.front_arb_blade,
                "rear_size": pt.rear_arb_size,
                "rear_blade": pt.rear_arb_blade,
            })

    # Check if we have sessions with identical springs but different ARB
    spring_groups: dict[tuple, list[dict]] = {}
    for ap in arb_calibration_points:
        sk = ap["spring_key"]
        spring_groups.setdefault(sk, []).append(ap)

    arb_varied_groups = {k: v for k, v in spring_groups.items() if len(set(ap["arb_key"] for ap in v)) >= 2}
    if arb_varied_groups:
        unique_arb_configs = {ap["arb_key"] for group in arb_varied_groups.values() for ap in group}
        models.status["arb_stiffness"] = (
            f"data available ({len(arb_varied_groups)} spring groups, {len(unique_arb_configs)} ARB configs) "
            f"— full back-solve requires telemetry roll gradient data (currently deferred)"
        )
    else:
        models.status["arb_stiffness"] = "insufficient controlled data (need sessions with same springs, different ARB)"

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
            m = k * (pt.front_sigma_mm / pt.front_shock_vel_p99_mps) ** 2
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
    m_effs_rear = []
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
            m = k * (pt.rear_sigma_mm / pt.rear_shock_vel_p99_mps) ** 2
            if 100 < m < 5000:  # plausible range for rear (heavier: aero + third spring)
                m_effs_rear.append(m)

    if len(m_effs_rear) >= 3:
        models.m_eff_rear_kg = float(np.mean(m_effs_rear))
        models.status["m_eff_rear"] = f"constant ({len(m_effs_rear)} points, mean {models.m_eff_rear_kg:.0f} kg)"

    # ─── 16. Measured LLTD target ───
    lltds = []
    for pt in unique:
        if pt.lf_corner_weight_n > 0 and pt.lr_corner_weight_n > 0:
            total_f = pt.lf_corner_weight_n + pt.rf_corner_weight_n
            total_r = pt.lr_corner_weight_n + pt.rr_corner_weight_n
            total = total_f + total_r
            if total > 0:
                weight_dist = total_f / total
                lltds.append(weight_dist)
    if len(lltds) >= 3:
        models.measured_lltd_target = float(np.mean(lltds))
        models.status["lltd_target"] = f"calibrated ({len(lltds)} sessions, mean {models.measured_lltd_target:.3f})"

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
# Apply calibrated models to car object
# ─────────────────────────────────────────────────────────────────────────────

def apply_to_car(car_obj, models: CarCalibrationModels) -> list[str]:
    """Apply fitted models to a car object from car_model/cars.py.

    Modifies the car object in-place. Returns list of applied correction notes.
    """
    applied = []

    if not models:
        return applied

    # Apply DeflectionModel coefficients
    if models.front_shock_defl_static and models.heave_spring_defl_static:
        try:
            defl = car_obj.deflection
            fs = models.front_shock_defl_static
            if len(fs.coefficients) >= 2:
                defl.shock_front_intercept = fs.coefficients[0]
                defl.shock_front_pushrod_coeff = fs.coefficients[1]
            rs = models.rear_shock_defl_static
            if rs and len(rs.coefficients) >= 2:
                defl.shock_rear_intercept = rs.coefficients[0]
                defl.shock_rear_pushrod_coeff = rs.coefficients[1]
            hs = models.heave_spring_defl_static
            # Reciprocal fit: [intercept, 1/heave_nmm, perch, 1/OD^4]
            # Must have exactly 4 coefficients matching DeflectionModel semantics
            if hs and len(hs.coefficients) >= 4:
                defl.heave_defl_intercept = hs.coefficients[0]
                defl.heave_defl_inv_heave_coeff = hs.coefficients[1]  # 1/heave_nmm
                defl.heave_defl_perch_coeff = hs.coefficients[2]      # perch_mm
                defl.heave_defl_inv_od4_coeff = hs.coefficients[3]    # 1/OD^4
            defl.is_calibrated = True
            applied.append(f"DeflectionModel updated from {models.n_unique_setups} IBT sessions")
        except AttributeError:
            pass

    # Apply RideHeightModel coefficients
    # Map calibration feature names to RideHeightModel attribute names
    _FRONT_RH_COEFF_MAP = {
        "front_heave": "front_coeff_heave_nmm",
        "front_camber": "front_coeff_camber_deg",
    }
    _REAR_RH_COEFF_MAP = {
        "rear_pushrod": "rear_coeff_pushrod",
        "rear_third": "rear_coeff_third_nmm",
        "rear_spring": "rear_coeff_rear_spring",
        "rear_third_perch": "rear_coeff_heave_perch",
        "fuel": "rear_coeff_fuel_l",
        "rear_spring_perch": "rear_coeff_spring_perch",
    }
    if models.front_ride_height and models.rear_ride_height:
        try:
            rh = car_obj.ride_height_model
            fr = models.front_ride_height
            if len(fr.coefficients) >= 2:
                rh.front_intercept = fr.coefficients[0]
                # Apply mapped coefficients (coefficients[1:] match feature_names order)
                for i, feat in enumerate(fr.feature_names):
                    attr = _FRONT_RH_COEFF_MAP.get(feat)
                    if attr and hasattr(rh, attr) and (i + 1) < len(fr.coefficients):
                        setattr(rh, attr, fr.coefficients[i + 1])
            rr = models.rear_ride_height
            if len(rr.coefficients) >= 2:
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

    # Apply measured LLTD target
    if models.measured_lltd_target is not None:
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
            applied.append(
                f"AeroCompression updated: front={models.aero_front_compression_mm:.1f}mm "
                f"rear={models.aero_rear_compression_mm:.1f}mm "
                f"({models.aero_n_sessions} sessions)"
            )
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
        except Exception:
            pass

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
    lines.append(f"  python -m ioptimal calibrate --car {car} --ibt-dir ~/Documents/iRacing/telemetry/ --dry-run")
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
    if s.get("lltd_target"):
        print(f"  LLTD target:    {s['lltd_target']:.3f} (calibrated)")

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
            print("→ already in dataset")
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

        # Preserve existing spring lookup tables (and expand if they only have 1 entry)
        existing_saved = load_calibrated_models(car)
        if existing_saved:
            if existing_saved.front_torsion_lookup and not models.front_torsion_lookup:
                lut_f = existing_saved.front_torsion_lookup
                # Retroactively expand single-entry lookups with k ∝ OD⁴ extrapolation
                n_before = len(lut_f.entries)
                lut_f = expand_torsion_lookup_from_physics(car, lut_f, axle="front")
                if len(lut_f.entries) > n_before:
                    print(f"  ↳ Expanded front torsion lookup: {n_before} → {len(lut_f.entries)} entries (k∝OD⁴)")
                models.front_torsion_lookup = lut_f
            if existing_saved.rear_torsion_lookup and not models.rear_torsion_lookup:
                lut_r = existing_saved.rear_torsion_lookup
                n_before = len(lut_r.entries)
                lut_r = expand_torsion_lookup_from_physics(car, lut_r, axle="rear")
                if len(lut_r.entries) > n_before:
                    print(f"  ↳ Expanded rear torsion lookup: {n_before} → {len(lut_r.entries)} entries (k∝OD⁴)")
                models.rear_torsion_lookup = lut_r

        save_calibrated_models(car, models)
        print(f"  ✅ Models saved to {_models_path(car)}")
    else:
        remaining = _MIN_SESSIONS_FOR_FIT - n_unique
        print(f"\n  ⏳ Need {remaining} more unique-setup sessions before fitting (have {n_unique}/{_MIN_SESSIONS_FOR_FIT})")
        print(f"     Tip: Run sessions with different heave springs, torsion bars, or pushrods")

    print_status(car)


if __name__ == "__main__":
    main()
