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
import re
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
    - Springs (GTP): front heave, rear third, front torsion OD/index, rear spring
    - Springs (GT3, W7.1 audit BLOCKER #6): front/rear paired coil rate, bump
      rubber gap front/rear, splitter height. For GTP cars these getattrs return
      0.0 so the appended tuple slots are no-ops. For GT3 cars these are the
      actual differentiators — without them, two GT3 IBTs varying only by front
      coil rate would collapse to the same fingerprint and one would be silently
      dropped at L2726/L2847/L3300/L3318/L3379/L3410.
    - Perches: front heave perch, rear third perch, rear spring perch
    - Geometry: front/rear pushrod, front/rear camber
    - ARB: front/rear size (string) and blade (integer) — affect roll stiffness
      and must be distinguished for accurate regression coverage
    - Load: fuel level
    - Dampers: LF/LR LS/HS comp/rbd clicks — same suspension with different
      damper clicks drives shock-velocity telemetry differences the regression
      sees, so sessions must count as distinct setups for fitting purposes.
    """
    return (
        # Track — different tracks produce different ride heights and deflections
        # at the same setup due to aero load, surface, and speed profile differences.
        # Pooling cross-track data causes 27x-103x LOO/train overfitting ratios.
        # TODO(W7.x audit COSMETIC #25): re-read once more GT3 tracks land — today
        # all 3 GT3 IBTs are at Spielberg + Nürburgring so the track key carries
        # most of the variance.
        str(getattr(pt, "track", "") or ""),
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
        # ── GT3 paired-coil + bump-rubber + splitter (W7.1, audit BLOCKER #6) ──
        # Append-to-tuple so legacy GTP keys stay equivalent (these slots are 0.0
        # for GTP). W7.2 will add the corresponding fields to ``CalibrationPoint``
        # itself; until then these getattrs return 0.0 and are no-ops. Once W7.2
        # populates them from GT3 IBTs, this key correctly distinguishes setups
        # that differ only by front/rear coil rate or bump rubber gap.
        round(float(getattr(pt, "front_corner_spring_nmm", 0.0)), 1),
        round(float(getattr(pt, "rear_corner_spring_nmm", 0.0)), 1),
        round(float(getattr(pt, "front_bump_rubber_gap_mm", 0.0)), 1),
        round(float(getattr(pt, "rear_bump_rubber_gap_mm", 0.0)), 1),
        round(float(getattr(pt, "splitter_height_mm", 0.0)), 1),
        # Damper clicks (Unit 2) — appended at END so existing fingerprints
        # don't shift. getattr-with-default keeps legacy CalibrationPoint JSON
        # (no damper fields) loadable; legacy points all collapse to
        # (0,0,0,0,0,0,0,0). New differentiation only fires when fresh
        # extract_point_from_ibt populates these fields.
        round(getattr(pt, "lf_ls_comp", 0.0), 1),
        round(getattr(pt, "lf_hs_comp", 0.0), 1),
        round(getattr(pt, "lf_ls_rbd", 0.0), 1),
        round(getattr(pt, "lf_hs_rbd", 0.0), 1),
        round(getattr(pt, "lr_ls_comp", 0.0), 1),
        round(getattr(pt, "lr_hs_comp", 0.0), 1),
        round(getattr(pt, "lr_ls_rbd", 0.0), 1),
        round(getattr(pt, "lr_hs_rbd", 0.0), 1),
        # Lap number (Unit D1) — disambiguates per-lap CalibrationPoints from
        # the same IBT. Setup features are identical across laps, so without
        # this slot all per-lap rows collapse to one fingerprint and the
        # regression sees N=1. Legacy single-point-per-IBT files have
        # lap_number=0 and remain equivalent to the old fingerprint shape.
        round(getattr(pt, "lap_number", 0), 0),
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

    # ── Roll telemetry ──
    roll_gradient_deg_per_g: float = 0.0
    # Backward-compatible storage for the RH-based roll_distribution_proxy.
    # This is NOT true wheel-load LLTD and must not calibrate ARB/LLTD targets.
    lltd_measured: float = 0.0

    # ── Optional: decrypted .sto physics rates (from setupdelta.com) ──
    # When available, these give EXACT N/mm rates for indexed spring cars.
    front_side_spring_rate_nmm: float = 0.0   # from fSideSpringRateNpm / 1000
    rear_side_spring_rate_nmm: float = 0.0    # from rSideSpringRateNpm / 1000
    front_heave_rate_nmm: float = 0.0         # front heave spring physics rate
    rear_heave_rate_nmm: float = 0.0          # rear heave/third physics rate

    # ── GT3 paired-coil + bump-rubber + splitter fields (W7.2) ───────────
    # GT3 cars (BMW M4 GT3 EVO, Aston Martin Vantage GT3 EVO, Porsche 911
    # GT3 R 992) use paired front coils + paired rear coils + bump rubber gap
    # × 2 axles + splitter height. They have no heave/third springs and no
    # front torsion bar (`heave_spring=None`, `front_torsion_c=0.0`), so the
    # GTP fields above stay 0.0 for GT3 IBTs — the std-filter at L1293 drops
    # them automatically. Field names align with `learner/observation.py`
    # (W6.3) and `car_model/garage.py:GarageSetupState` (W7.1) so the same
    # canonical names propagate observation → calibration → garage.
    #
    # NOTE: until varied-spring GT3 IBTs land (gated on W10.1 capture), these
    # will be (near-)constant in the dataset and the regression will be
    # intercept-only. The scaffolding below ensures no further code changes
    # are needed once the data arrives.
    front_corner_spring_nmm: float = 0.0
    rear_corner_spring_nmm: float = 0.0
    front_bump_rubber_gap_mm: float = 0.0
    rear_bump_rubber_gap_mm: float = 0.0
    splitter_height_mm: float = 0.0

    # ── Provenance: synthesised vs real (Unit 9 — virtual data anchors) ──
    # When True, this point was generated from car physics in
    # ``car_model.calibration.virtual_anchors`` rather than ingested from
    # an IBT session. Real-data dedupe / min-sessions / display logic
    # filters on this flag so synthesised points never inflate the
    # ``len(unique) >= _MIN_SESSIONS_FOR_FIT`` gate that decides whether
    # fitting is attempted.
    synthesized: bool = False

    # ── Damper clicks (Unit 2 — left-side; right-side mirrors for per-corner cars) ──
    # For per-corner cars (BMW, Cadillac, Ferrari) lf maps to LeftFront and lr
    # to LeftRear. For heave-architecture cars (Porsche/Acura) lf maps to
    # FrontHeave and lr to RearHeave/LeftRear depending on car. The reader
    # exposes these as setup.front_* / setup.rear_* (already normalized).
    # These are part of the setup fingerprint so click-sweep IBTs at otherwise
    # identical suspension count as distinct calibration points.
    lf_ls_comp: float = 0.0
    lf_hs_comp: float = 0.0
    lf_ls_rbd: float = 0.0
    lf_hs_rbd: float = 0.0
    lr_ls_comp: float = 0.0
    lr_hs_comp: float = 0.0
    lr_ls_rbd: float = 0.0
    lr_hs_rbd: float = 0.0

    # ── Per-corner per-phase telemetry (Unit D3) ──
    # Flat dict of {f"corner_{idx}_{phase}_{metric}" -> float}; populated by
    # `analyzer.segment.compute_corner_phase_metrics`. Empty dict if no corners
    # were detected or extraction failed. Idx is 1-based and stable across
    # IBTs of the same track (corners are sorted by lap distance).
    # Phases: entry / mid / exit. Metrics: understeer_deg, body_slip_deg,
    # lat_g, long_g, throttle_pos, brake_pos.
    corner_phase_metrics: dict[str, float] = field(default_factory=dict)

    # ── Per-lap covariates (Unit D1 — every lap is data) ─────────────
    # ``lap_number == 0`` means "best-lap-aggregated" (legacy single-point
    # per IBT); a non-zero value identifies a specific lap. lap_number is
    # part of the setup fingerprint (see ``_setup_key``) so per-lap rows
    # from the same IBT do NOT collapse to one regression sample. Per-lap
    # variance correlates with fuel/tyre/driver factors and IS signal,
    # not noise.
    #
    # Note: the existing ``lap_time_s`` field above is already populated
    # from ``measured.lap_time_s``, which honours the ``lap`` argument
    # to ``extract_measurements`` — when ``lap_idx`` is set, ``lap_time_s``
    # is the per-lap time. ``lap_number`` disambiguates which lap.
    lap_number: int = 0
    fuel_remaining_l: float = 0.0
    tyre_temp_avg_c: float = 0.0
    driver_aggression_idx: float = 0.0


@dataclass
class FittedModel:
    """Fitted regression coefficients for one calibration model.

    Confidence tiers (computed by ``_compute_tier``):
      - ``high``         R² ≥ 0.85, LOO/train < 2.0, n ≥ 3 × n_features
      - ``medium``       R² ≥ 0.70, LOO/train < 5.0, n ≥ 2 × n_features
      - ``low``          R² ≥ 0.30, LOO/train < 20.0 (still usable, with warning)
      - ``insufficient`` anything below — solver must NOT use this model

    The legacy ``is_calibrated`` boolean is kept for backward compatibility and
    is derived as ``confidence_tier != "insufficient"``.  New code should read
    ``confidence_tier`` directly.
    """
    name: str
    feature_names: list[str]
    coefficients: list[float]   # [intercept, beta_1, beta_2, ...]
    r_squared: float = 0.0
    rmse: float = 0.0
    loo_rmse: float = 0.0
    n_samples: int = 0
    is_calibrated: bool = True
    q_squared: float | None = None  # LOO R² = 1 - (LOO_RMSE² × n) / SS_total
    confidence_tier: str = "insufficient"  # "high"|"medium"|"low"|"insufficient"


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
    track: str = ""  # "" = pooled/all-tracks (legacy default)
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
    torsion_bar_defl_direct: FittedModel | None = None  # direct (non-load) form
    third_slider_defl_direct: FittedModel | None = None  # direct (from setup features)

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

    # Per-(corner, phase, metric) regressions (Unit D3)
    # Keyed by the same "corner_{idx}_{phase}_{metric}" string used in
    # CalibrationPoint.corner_phase_metrics. Each FittedModel uses the
    # _UNIVERSAL_POOL features so downstream consumers (Unit P1) can predict
    # phase-level effects from setup deltas. Only triplets present in ≥
    # _MIN_SESSIONS_FOR_FIT distinct setups with non-trivial variance are fit.
    corner_phase_models: dict[str, FittedModel] = field(default_factory=dict)

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


def _safe_track_slug(track: str) -> str:
    """Return a filesystem-safe slug for *track*.

    Only lower-case letters, digits, and underscores are kept.  Anything else
    (including path separators, spaces, dots, and ``..`` sequences) is replaced
    with ``_``, so user-supplied track names cannot cause path traversal.
    """
    slug = re.sub(r"[^a-z0-9_]", "_", track.lower())
    # Collapse consecutive underscores and strip leading/trailing ones
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "unknown"


def _track_slug(track_name: str) -> str:
    """Resolve a track display name to its canonical short slug.

    Wraps :func:`car_model.registry.track_key` so callers in this module
    (and tests) get the alias-aware short slug ("Red Bull Ring Grand Prix"
    → ``"spielberg"``, "Sebring International Raceway" → ``"sebring"``)
    rather than a raw underscore-substituted display name. Per audit
    finding #11 (W7.2), the per-track partition writer must use this
    rather than ``pt.track.replace(" ", "_")`` so unknown long display
    names land on conventional short slugs.
    """
    if not track_name:
        return ""
    from car_model.registry import track_key as _track_key
    return _track_key(track_name)


def _models_path_for_track(car: str, track: str) -> Path:
    """Per-track model file: models_{slug}.json.

    The track string is sanitised through :func:`_safe_track_slug` before
    being embedded in the filename, preventing path traversal.
    """
    return _data_dir(car) / f"models_{_safe_track_slug(track)}.json"


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


def load_calibrated_models(car: str, track: str = "") -> CarCalibrationModels | None:
    """Load fitted models.

    When *track* is provided, try the per-track model file first
    (``models_{track}.json``).  Fall back to the pooled model if the
    per-track file doesn't exist or has insufficient data.

    Returns None if no calibration data exists at all.
    """
    if track:
        p_track = _models_path_for_track(car, track)
        if p_track.exists():
            with open(p_track, encoding="utf-8") as f:
                raw = json.load(f)
            m = _dict_to_models(raw)
            if m.n_unique_setups >= _MIN_SESSIONS_FOR_FIT:
                return m
    # Pooled / fallback
    p = _models_path(car)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    # Legacy migrations: proxy-derived LLTD targets must not override curated
    # car definitions. Keep the status note for provenance, but clear the value.
    # Stub models.json files use a flat string for "status" (e.g. "uncalibrated"),
    # so guard the dict access.
    _status = raw.get("status")
    if isinstance(_status, dict) and _status.get("lltd_target", "").startswith("DISABLED"):
        raw["measured_lltd_target"] = None
    return _dict_to_models(raw)


def save_calibrated_models(car: str, models: CarCalibrationModels, track: str = "") -> None:
    if track:
        p = _models_path_for_track(car, track)
    else:
        p = _models_path(car)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(_models_to_dict(models), f, indent=2)


def _merge_car_wide_fields(
    car: str,
    dest: CarCalibrationModels,
    source: CarCalibrationModels | None,
    *,
    verbose: bool = False,
) -> None:
    """Copy non-track-specific fields from *source* into *dest* (in-place).

    Fields covered:
    - Damper zeta targets (set by ``validation/calibrate_dampers.py``)
    - Spring lookup tables (set by STO import / expand pass)
    - Status dict keys not already present in *dest*

    This ensures that per-track model files are self-contained — loading a
    per-track model via ``load_calibrated_models(car, track=...)`` returns all
    the same car-wide data as the pooled model would.
    """
    if source is None:
        return

    # Preserve damper zeta
    if source.front_ls_zeta is not None and dest.front_ls_zeta is None:
        dest.front_ls_zeta = source.front_ls_zeta
        dest.rear_ls_zeta = source.rear_ls_zeta
        dest.front_hs_zeta = source.front_hs_zeta
        dest.rear_hs_zeta = source.rear_hs_zeta
        dest.zeta_n_sessions = source.zeta_n_sessions

    # Preserve spring lookup tables (and expand if they only have 1 entry)
    if source.front_torsion_lookup and not dest.front_torsion_lookup:
        lut_f = source.front_torsion_lookup
        n_before = len(lut_f.entries)
        lut_f = expand_torsion_lookup_from_physics(car, lut_f, axle="front")
        if verbose and len(lut_f.entries) > n_before:
            print(f"  Expanded front torsion lookup: {n_before} -> {len(lut_f.entries)} entries")
        dest.front_torsion_lookup = lut_f

    if source.rear_torsion_lookup and not dest.rear_torsion_lookup:
        lut_r = source.rear_torsion_lookup
        n_before = len(lut_r.entries)
        lut_r = expand_torsion_lookup_from_physics(car, lut_r, axle="rear")
        if verbose and len(lut_r.entries) > n_before:
            print(f"  Expanded rear torsion lookup: {n_before} -> {len(lut_r.entries)} entries")
        dest.rear_torsion_lookup = lut_r

    # Merge status dict: preserve keys from source that dest hasn't computed
    for k, v in source.status.items():
        if k not in dest.status:
            dest.status[k] = v


def _models_to_dict(m: CarCalibrationModels) -> dict:
    d = asdict(m)
    return d


def _dict_to_models(d: dict) -> CarCalibrationModels:
    """Reconstruct CarCalibrationModels from a plain dict."""

    def _to_fitted(raw: dict | None) -> FittedModel | None:
        if raw is None:
            return None
        kwargs = {k: v for k, v in raw.items() if k in FittedModel.__dataclass_fields__}
        # Backward-compat: legacy models.json files saved before the tier
        # system existed don't carry a ``confidence_tier`` key.  Derive one
        # so the gate doesn't see every legacy model as "insufficient".
        if "confidence_tier" not in kwargs:
            kwargs["confidence_tier"] = tier_from_raw_model(raw) or (
                "medium" if kwargs.get("is_calibrated", False) else "insufficient"
            )
        return FittedModel(**kwargs)

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
        "torsion_bar_defl_direct",
        "third_slider_defl_direct",
    ]
    lookup_keys = ["front_heave_lookup", "rear_heave_lookup", "front_torsion_lookup", "rear_torsion_lookup"]

    kwargs: dict[str, Any] = {}
    for k, v in d.items():
        if k in regression_keys:
            kwargs[k] = _to_fitted(v)
        elif k in lookup_keys:
            kwargs[k] = _to_lookup(v)
        elif k == "corner_phase_models":
            # Dict of {triplet_key: FittedModel-as-dict}
            if isinstance(v, dict):
                kwargs[k] = {
                    name: fitted
                    for name, fitted in (
                        (n, _to_fitted(raw)) for n, raw in v.items()
                    )
                    if fitted is not None
                }
            else:
                kwargs[k] = {}
        elif k in CarCalibrationModels.__dataclass_fields__:
            kwargs[k] = v

    # Stub models.json files store ``status`` as a flat string ("uncalibrated"),
    # but rich-format calibrated models store it as a dict. The dataclass
    # declares dict[str, str]; coerce flat strings to a dict here so downstream
    # callsites (calibration_status, _build_subsystem_status, etc.) can safely
    # call ``.get()`` without per-site isinstance guards.
    _status = kwargs.get("status")
    if isinstance(_status, str):
        kwargs["status"] = {"_legacy_stub": _status}

    return CarCalibrationModels(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# IBT extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_point_from_ibt(
    ibt_path: str | Path,
    car_name: str = "",
    lap_idx: int | None = None,
) -> CalibrationPoint | None:
    """Extract a CalibrationPoint from an IBT file.

    Reads ALL data from the IBT session info YAML (setup + computed values)
    and from the telemetry channels (measured outcomes).

    When ``lap_idx`` is None (default) the legacy best-lap-aggregated
    behavior is preserved (back-compat for existing callers). When
    ``lap_idx`` is an int, telemetry is extracted from that specific lap
    so per-lap variance feeds the regression instead of being collapsed
    to a per-IBT mean. The setup-fingerprint hash includes ``lap_number``
    (set by the caller via ``pt.lap_number = lap_idx``) so the regression
    sees N rows instead of 1.
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
    fuel_remaining_l = 0.0
    tyre_temp_avg_c = 0.0
    driver_aggression_idx = 0.0

    car_obj = _get_dummy_car(car_name)
    try:
        from analyzer.extract import extract_measurements
        measured = extract_measurements(str(ibt_path), car_obj, lap=lap_idx)
        dynamic_front_rh = measured.mean_front_rh_at_speed_mm or 0.0
        dynamic_rear_rh = measured.mean_rear_rh_at_speed_mm or 0.0
        front_sigma = measured.front_rh_std_mm or 0.0
        rear_sigma = measured.rear_rh_std_mm or 0.0
        front_shock_p99 = measured.front_shock_vel_p99_mps or 0.0
        rear_shock_p99 = measured.rear_shock_vel_p99_mps or 0.0
        lap_time = measured.lap_time_s or 0.0
        roll_grad = getattr(measured, "roll_gradient_measured_deg_per_g", None) or 0.0
        lltd_m = getattr(measured, "roll_distribution_proxy", None)
        if lltd_m is None:
            lltd_m = getattr(measured, "lltd_measured", None)
        lltd_m = lltd_m or 0.0

        # Per-lap fuel level — captures fuel burn across the stint so the
        # regression can disambiguate fuel-driven RH/deflection drift from
        # setup-driven changes within a single IBT.
        fuel_remaining_l = (
            getattr(measured, "fuel_level_at_measurement_l", None)
            or float(setup.fuel_l)
            or 0.0
        )
        temps = [
            t for t in (
                getattr(measured, "front_carcass_mean_c", None),
                getattr(measured, "rear_carcass_mean_c", None),
            ) if t is not None
        ]
        tyre_temp_avg_c = float(sum(temps) / len(temps)) if temps else 0.0
        # Front shock-vel p99 is an honest per-lap aggression indicator
        # (kerb riding + braking bumps). The regression treats it as a
        # covariate when explaining per-lap RH variance.
        driver_aggression_idx = float(front_shock_p99)
    except Exception as e:
        # Telemetry extraction is optional for calibration; skip gracefully
        import logging
        logging.getLogger(__name__).debug("Telemetry extraction skipped: %s", e)
        roll_grad = 0.0
        lltd_m = 0.0

    # Per-corner per-phase metrics (Unit D3). Best-effort: any failure (missing
    # channels, no valid lap, bad corner detection) yields an empty dict so
    # calibration still proceeds.
    corner_phase_metrics: dict[str, float] = {}
    try:
        from analyzer.segment import compute_corner_phase_metrics
        best = ibt.best_lap_indices()
        if best is not None:
            cp_start, cp_end = best
            corner_phase_metrics = compute_corner_phase_metrics(
                ibt, cp_start, cp_end, car=car_obj,
            )
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(
            "Corner-phase metric extraction skipped: %s", e
        )
        corner_phase_metrics = {}

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
        # Historical field name retained for JSON compatibility.  Value is the
        # roll_distribution_proxy, not a true LLTD calibration target.
        lltd_measured=lltd_m,
        # Damper clicks — setup_reader normalizes to front_* / rear_* (LF/LR
        # for per-corner cars; FrontHeave/RearHeave for heave-architecture).
        lf_ls_comp=float(setup.front_ls_comp),
        lf_hs_comp=float(setup.front_hs_comp),
        lf_ls_rbd=float(setup.front_ls_rbd),
        lf_hs_rbd=float(setup.front_hs_rbd),
        lr_ls_comp=float(setup.rear_ls_comp),
        lr_hs_comp=float(setup.rear_hs_comp),
        lr_ls_rbd=float(setup.rear_ls_rbd),
        lr_hs_rbd=float(setup.rear_hs_rbd),
        # Per-corner per-phase metrics (Unit D3). May be empty if no valid
        # corners were detected; downstream regression fitting tolerates this.
        corner_phase_metrics=corner_phase_metrics,
        lap_number=int(lap_idx) if lap_idx is not None else 0,
        fuel_remaining_l=float(fuel_remaining_l),
        tyre_temp_avg_c=float(tyre_temp_avg_c),
        driver_aggression_idx=float(driver_aggression_idx),
    )


def _get_dummy_car(car_name: str):
    """Get a minimal car object for telemetry extraction.

    Refuses to silently fall back to BMW when ``car_name`` is empty/None — that
    would silently apply BMW physics (index decode, m_eff, deflection
    coefficients) to whichever car happened to be calibrating. Returns None
    instead so callers handle missing-car explicitly.
    """
    if not car_name:
        import logging
        logging.getLogger(__name__).warning(
            "_get_dummy_car called with empty car_name; refusing to default to BMW. "
            "Caller should pass an explicit car name."
        )
        return None
    try:
        from car_model.cars import get_car
        return get_car(car_name)
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

def _compute_tier(
    r2: float,
    loo_rmse: float,
    train_rmse: float,
    n_samples: int,
    n_features: int,
    *,
    noise_floor_rmse: float | None = None,
) -> str:
    """Classify a fitted model into one of four confidence tiers.

    The tiers replace the binary ``is_calibrated`` flag.  Solver consumers
    use ALL non-``insufficient`` tiers; the tier label tells them how much
    confidence to attach to the prediction.

    Tier definitions:
      - ``high``         R² ≥ 0.85, LOO/train < 2.0, n ≥ 3 × n_features
      - ``medium``       R² ≥ 0.70, LOO/train < 5.0, n ≥ 2 × n_features
      - ``low``          R² ≥ 0.30, LOO/train < 20.0
      - ``insufficient`` anything below — solver must NOT use this model

    Train-RMSE floor (the metric-validity fix): when the regression has
    enough degrees of freedom to drive train residuals below physical
    measurement noise, the LOO/train ratio diverges to astronomical
    values that say nothing about generalization (the model just fit
    sub-noise micro-variation).  We floor ``train_rmse`` at the
    measurement-noise estimate before computing the ratio:

      - If ``noise_floor_rmse`` is provided (e.g. within-IBT std of the
        same metric, populated by ``Observation.setup_noise_floor_*``)
        we use that — that's the true measurement noise of the channel.
      - Otherwise we fall back to ``loo_rmse * 0.1`` as a heuristic
        floor: a model whose train RMSE is < 10% of its LOO RMSE is by
        definition fitting numerical noise; further driving the ratio
        up is meaningless.

    NaN ``loo_rmse`` (n < 5, LOO skipped) is treated as ratio = 0 — the
    n-bound checks in the higher tiers still gate small-sample cases.
    """
    # Hard floors: too few samples or terrible fit → insufficient.
    if r2 < 0.30:
        return "insufficient"
    if n_samples < max(n_features, 1):
        return "insufficient"

    if not np.isnan(loo_rmse):
        # Measurement-noise floor for train RMSE (see docstring).
        if noise_floor_rmse is not None and noise_floor_rmse > 0:
            train_floor = float(noise_floor_rmse)
        else:
            # Heuristic: floor at max(absolute_min, 10% of LOO).  10% of
            # LOO is the boundary where ratio loses meaning — anything
            # smaller is sub-noise overfit, not better generalization.
            train_floor = max(1e-3, 0.10 * loo_rmse)
        loo_ratio = loo_rmse / max(train_rmse, train_floor)
    else:
        # LOO not computed (n < 5).  Treat as best-case for ratio, but the
        # n_samples checks below still keep us in "low" or "insufficient".
        loo_ratio = 0.0

    if r2 >= 0.85 and loo_ratio < 2.0 and n_samples >= 3 * max(n_features, 1):
        return "high"
    if r2 >= 0.70 and loo_ratio < 5.0 and n_samples >= 2 * max(n_features, 1):
        return "medium"
    if r2 >= 0.30 and loo_ratio < 20.0:
        return "low"
    return "insufficient"


_TIER_NAMES = ("high", "medium", "low", "insufficient")


def tier_from_raw_model(raw: dict | None) -> str | None:
    """Extract / derive a ``confidence_tier`` from a raw fitted-model dict.

    Returns the explicit ``confidence_tier`` field when present, otherwise
    derives one from the saved (R², RMSE, LOO RMSE, n_samples, feature
    names) using :func:`_compute_tier` so legacy on-disk models pre-dating
    the tier system are still classified consistently.  Returns None when
    *raw* is not a dict or has no usable fields.
    """
    if not isinstance(raw, dict):
        return None
    explicit = raw.get("confidence_tier")
    if isinstance(explicit, str) and explicit.lower() in _TIER_NAMES:
        return explicit.lower()
    try:
        loo = raw.get("loo_rmse")
        loo_val = float(loo) if loo is not None else float("nan")
    except (TypeError, ValueError):
        loo_val = float("nan")
    try:
        feats = raw.get("feature_names") or []
        return _compute_tier(
            float(raw.get("r_squared", 0.0) or 0.0),
            loo_val,
            float(raw.get("rmse", 0.0) or 0.0),
            int(raw.get("n_samples", 0) or 0),
            len(feats) if isinstance(feats, list) else 0,
        )
    except (TypeError, ValueError):
        return None


def _fit(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: list[str],
    model_name: str,
    *,
    noise_floor_rmse: float | None = None,
) -> FittedModel:
    """Fit y = X @ beta via least squares with LOO cross-validation.

    ``noise_floor_rmse`` (optional) is the measurement-noise floor for the
    target channel — typically the within-IBT lap-to-lap std of the same
    output, populated by ``Observation.setup_noise_floor_*``.  When the
    regression is over-parameterised relative to the number of distinct
    physical setups, train RMSE drives below this physical floor; the
    LOO/train ratio then explodes for non-physical reasons.  Floor train
    RMSE at ``noise_floor_rmse`` before computing the ratio so the tier
    classification reflects real generalization, not numerical noise.
    """
    ones = np.ones((X.shape[0], 1))
    X_aug = np.hstack([ones, X])

    # Guard: underdetermined system (more parameters than samples) produces
    # meaningless R² = 1.0 and unstable coefficients. Require n > n_params.
    n_params = X_aug.shape[1]  # features + intercept
    if X.shape[0] <= n_params:
        import logging
        logging.getLogger(__name__).warning(
            "Model '%s': underdetermined (%d samples, %d parameters) — "
            "marking as insufficient",
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
            confidence_tier="insufficient",
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
        q_squared = None
    else:
        loo_rmse = float(np.sqrt(np.mean(loo_errors ** 2)))
        # Q² (predicted R², LOO R²): generalisation quality metric.
        # Q² = 1 - (LOO_RMSE² × n) / SS_total
        q_squared = float(1.0 - (loo_rmse ** 2 * n) / max(ss_tot, 1e-12))

    # Classify into a confidence tier (high/medium/low/insufficient). This is
    # the new truth for "is this model usable?". ``is_calibrated`` is derived
    # from this tier for backward compatibility (Mission Principle 4).
    # Replaces the legacy R2_THRESHOLD_BLOCK / MIN_R2_FLOOR binary gate.
    n_features = X.shape[1]
    tier = _compute_tier(
        r2, loo_rmse, rmse, n, n_features,
        noise_floor_rmse=noise_floor_rmse,
    )
    is_cal = tier != "insufficient"

    # Surface non-fatal warnings about generalisation quality.
    _overfit_warnings: list[str] = []
    if not np.isnan(loo_rmse) and loo_rmse > 2.0 * max(rmse, 1e-6) and n >= 5:
        _overfit_warnings.append(
            f"LOO RMSE ({loo_rmse:.3f}) > 2x training RMSE ({rmse:.3f}) — "
            f"possible overfit"
        )
    if n < _min_sessions_for_features(n_features):
        _overfit_warnings.append(
            f"Only {n} samples for {n_features} features (recommend "
            f"{_min_sessions_for_features(n_features)}+)"
        )
    if tier == "low":
        _overfit_warnings.append(
            f"tier=low (R²={r2:.3f}, LOO/train={loo_rmse / max(rmse, 1e-6):.1f}x) — "
            f"model is usable but predictions carry reduced confidence"
        )
    elif tier == "insufficient" and r2 >= 0.30 and n >= 5 and not np.isnan(loo_rmse):
        _overfit_warnings.append(
            f"tier=insufficient (R²={r2:.3f}, LOO/train={loo_rmse / max(rmse, 1e-6):.0f}x) — "
            f"model REJECTED, solver will skip"
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
        q_squared=q_squared,
        confidence_tier=tier,
    )


def _col(rows: list[dict], key: str) -> np.ndarray:
    return np.array([r[key] for r in rows], dtype=float)


def _select_features(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: list[str],
    max_features: int | None = None,
    seed_features: list[str] | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Forward feature selection to prevent overfitting on small datasets.

    When n_samples < 3 * n_features, selects a subset of features via
    greedy forward selection using leave-one-out RMSE as the criterion.

    *seed_features*: names of features that MUST be included before
    greedy selection begins — these are physics-critical features (e.g.
    compliance terms) that the greedy search might drop due to
    multicollinearity but that are essential for correct extrapolation.
    Only features that exist in *feature_names* and have non-zero
    variance are seeded.

    Returns (X_reduced, names_reduced).  If no reduction is needed the
    inputs are returned unchanged.
    """
    n_samples, n_features = X.shape
    if max_features is None:
        # Cap features at 1/3 of samples — matches _min_sessions_for_features()
        # which requires 3 * n_features samples for a healthy fit.
        max_features = max(1, min(n_samples // 3, 25))
    # Only skip selection when we have 3x more samples than features —
    # the standard statistical threshold for stable linear regression.
    if n_features <= max_features and n_samples >= 3 * n_features:
        return X, feature_names

    # Forward selection: start with seed features, then greedily add.
    selected: list[int] = []
    remaining = list(range(n_features))

    # Seed physics-critical features before greedy search
    if seed_features:
        name_to_idx = {n: i for i, n in enumerate(feature_names)}
        for sf in seed_features:
            idx = name_to_idx.get(sf)
            if idx is not None and idx in remaining:
                # Only seed if the feature has variance (not constant)
                if np.std(X[:, idx]) > 1e-9:
                    selected.append(idx)
                    remaining.remove(idx)

    best_overall_loo = float("inf")
    # Seeds count against the feature budget — otherwise seeding 3 features
    # on 7 samples (max_features=2) produces a 5-feature model that overfits.
    budget_remaining = max(0, max_features - len(selected))
    for _ in range(budget_remaining):
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
            # LOO RMSE — the honest generalization metric
            loo_sq = 0.0
            for i in range(n_samples):
                mask = np.ones(n_samples, dtype=bool)
                mask[i] = False
                b, *_ = np.linalg.lstsq(X_aug[mask], y[mask], rcond=None)
                loo_sq += (y[i] - X_aug[i] @ b) ** 2
            loo_rmse = float(np.sqrt(loo_sq / n_samples))
            if loo_rmse < best_loo:
                best_loo = loo_rmse
                best_idx = idx
        if best_idx < 0:
            break
        # Stop if LOO is degrading — more features hurt generalization
        if best_loo > best_overall_loo * 1.05 and len(selected) >= 3:
            break
        selected.append(best_idx)
        remaining.remove(best_idx)
        if best_loo < best_overall_loo:
            best_overall_loo = best_loo
        if best_loo < 0.01:
            break

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

    # ── Unit 9: virtual-anchor-relaxed minimum gate ──
    # Real data alone needs ``_MIN_SESSIONS_FOR_FIT`` (5) unique setups; with
    # physics-anchored virtual rows we accept as few as 2 real points provided
    # the virtual count brings total samples ≥ _MIN_SESSIONS_FOR_FIT. The
    # generated anchors are cached and reused below so we avoid a duplicate
    # generation pass when the gate passes.
    _MIN_REAL_WITH_VIRTUAL_ANCHORS = 2
    _virtual_anchors_by_target: dict[str, list] = {}
    if _car_obj is not None and len(unique) >= _MIN_REAL_WITH_VIRTUAL_ANCHORS:
        try:
            from car_model.calibration.virtual_anchors import (
                generate_virtual_anchors as _gen_anchors,
                supported_targets as _supp_targets,
            )
            for _t in _supp_targets():
                _a = _gen_anchors(_car_obj, _t)
                if _a:
                    _virtual_anchors_by_target[_t] = _a
        except Exception as _e:
            import logging
            logging.getLogger(__name__).debug(
                "virtual anchor generation skipped: %s", _e,
            )

    _virtual_count = sum(len(v) for v in _virtual_anchors_by_target.values())
    _effective_n = len(unique) + _virtual_count
    if len(unique) < _MIN_SESSIONS_FOR_FIT and _effective_n < _MIN_SESSIONS_FOR_FIT:
        models.status["deflection_model"] = (
            f"insufficient data ({len(unique)}/{_MIN_SESSIONS_FOR_FIT} unique "
            f"setups; +{_virtual_count} virtual = {_effective_n})"
        )
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
    # GT3 cars have ``heave_spring=None`` and ``front_torsion_c=0.0``, so the
    # index→N/mm decode path below is skipped entirely. The GT3 corner spring
    # rates arrive in N/mm directly (no index conversion needed) and there are
    # no heave/third/torsion fields to decode. W7.2 audit BLOCKER #10.
    _arch = getattr(_car_obj, "suspension_arch", None) if _car_obj else None
    _is_gt3_fit = _arch is not None and not _arch.has_heave_third
    if _car_obj is not None and not _is_gt3_fit:
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

    # ── Unit 9: append physics-anchored virtual rows ──
    # Anchors live AFTER the real-data dedupe gate so they never inflate
    # ``models.n_unique_setups``; they DO contribute as samples to the
    # least-squares solve, anchoring intercept and asymptote when sparse.
    _virtual_anchor_index: dict[str, list[int]] = {}
    for _target, _anchors in _virtual_anchors_by_target.items():
        _idx_start = len(rows)
        for _pt in _anchors:
            rows.append(asdict(_pt))
        _virtual_anchor_index[_target] = list(range(_idx_start, len(rows)))

    def col(name: str) -> np.ndarray:
        return _col(rows, name)

    # Mask used by std-checks and direct _fit() callsites that don't have a
    # virtual_anchors target — drops ALL synthesised rows so y=0.0 sentinels
    # for unrelated targets don't poison the regression.
    if _virtual_anchor_index:
        _real_only_mask = np.ones(len(rows), dtype=bool)
        for _idxs in _virtual_anchor_index.values():
            for _i in _idxs:
                _real_only_mask[_i] = False
    else:
        _real_only_mask = None

    def _real(arr: np.ndarray) -> np.ndarray:
        """Slice an array down to real (non-synthesised) rows."""
        return arr if _real_only_mask is None else arr[_real_only_mask]

    heave = col("front_heave_setting")
    od4 = col("front_torsion_od_mm") ** 4

    # ── Universal feature pool ──
    # Every regression model draws from this pool. Features with < 2 unique
    # values or near-zero variance are auto-excluded per model.
    _rear_third = col("rear_third_setting")
    _rear_spring = col("rear_spring_setting")
    _UNIVERSAL_POOL = [
        (col("front_pushrod_mm"), "front_pushrod"),
        (col("rear_pushrod_mm"), "rear_pushrod"),
        (heave, "front_heave"),
        (_rear_third, "rear_third"),
        (_rear_spring, "rear_spring"),
        (col("front_torsion_od_mm"), "torsion_od"),
        (col("front_heave_perch_mm"), "front_heave_perch"),
        (col("rear_third_perch_mm"), "rear_third_perch"),
        (col("rear_spring_perch_mm"), "rear_spring_perch"),
        (col("front_camber_deg"), "front_camber"),
        (col("fuel_l"), "fuel"),
        (1.0 / np.maximum(heave, 1.0), "inv_front_heave"),
        (1.0 / np.maximum(_rear_third, 1.0), "inv_rear_third"),
        (1.0 / np.maximum(_rear_spring, 1.0), "inv_rear_spring"),
        (1.0 / np.maximum(od4, 1.0), "inv_od4"),
        (col("rear_camber_deg"), "rear_camber"),
        (col("wing_deg"), "wing"),
        # ARB blade has zero effect on any garage output (confirmed via
        # isolated-change analysis: blade 6 vs 8 with everything else constant
        # produces 0.000mm difference on ALL outputs). Excluded to prevent
        # spurious correlations from inflating feature count.
    ]
    # Only pushrod² terms added — pushrod linkage geometry is nonlinear
    # (lever ratio changes with angle). All other interactions removed
    # after holdout testing showed they overfit (2.3mm on real IBTs).
    _push_f = col("front_pushrod_mm")
    _push_r = col("rear_pushrod_mm")
    _UNIVERSAL_POOL.append((_push_f ** 2, "front_pushrod_sq"))
    _UNIVERSAL_POOL.append((_push_r ** 2, "rear_pushrod_sq"))
    # Fuel × compliance: fuel weight compresses springs proportional to 1/k.
    # The linear fuel and 1/spring terms alone can't capture this interaction.
    _fuel = col("fuel_l")
    _UNIVERSAL_POOL.append((_fuel / np.maximum(_rear_spring, 1.0), "fuel_x_inv_spring"))
    _UNIVERSAL_POOL.append((_fuel / np.maximum(_rear_third, 1.0), "fuel_x_inv_third"))
    # Torsion bar preload (Ferrari/Acura only; zero for BMW/Porsche → auto-excluded
    # by the std-check in _pool_to_matrix).
    # Physics: the torsion bar preload turns add an angular offset to the bar.
    # iRacing reports TOTAL bar deflection (preload + elastic), so preload turns
    # directly affect the reported defl value. They also govern load-sharing:
    # more front preload → torsion bar carries more corner load → heave spring
    # carries less → heave_spring_defl_static decreases (r = −0.83 from IBT).
    _UNIVERSAL_POOL.append((col("torsion_bar_turns"), "torsion_turns"))
    _UNIVERSAL_POOL.append((col("rear_torsion_bar_turns"), "rear_torsion_turns"))

    # ── GT3 paired-coil + bump-rubber + splitter features (W7.2) ─────────
    # For GTP IBTs these columns are all zeros (CalibrationPoint defaults),
    # so the std-filter at L1293 drops them automatically — same dict, no
    # GTP behavioural change. For GT3 IBTs the legacy GTP features above are
    # zero and these are populated. Compliance physics: defl ∝ F/k, so
    # `inv_front_corner_spring` (1/k) drives static RH and deflection just as
    # `inv_front_heave` does for GTP. `splitter_height` is a downforce/balance
    # axis; `bump_rubber_gap` affects platform stiffness in the bump zone.
    _front_coil = col("front_corner_spring_nmm")
    _rear_coil = col("rear_corner_spring_nmm")
    _UNIVERSAL_POOL.append((_front_coil, "front_corner_spring"))
    _UNIVERSAL_POOL.append((_rear_coil, "rear_corner_spring"))
    _UNIVERSAL_POOL.append((1.0 / np.maximum(_front_coil, 1.0), "inv_front_corner_spring"))
    _UNIVERSAL_POOL.append((1.0 / np.maximum(_rear_coil, 1.0), "inv_rear_corner_spring"))
    _UNIVERSAL_POOL.append((col("front_bump_rubber_gap_mm"), "front_bump_rubber_gap"))
    _UNIVERSAL_POOL.append((col("rear_bump_rubber_gap_mm"), "rear_bump_rubber_gap"))
    _UNIVERSAL_POOL.append((col("splitter_height_mm"), "splitter_height"))
    # Fuel × compliance for GT3 (mirror of GTP fuel_x_inv_third / inv_spring).
    _UNIVERSAL_POOL.append(
        (_fuel / np.maximum(_front_coil, 1.0), "fuel_x_inv_front_corner_spring")
    )
    _UNIVERSAL_POOL.append(
        (_fuel / np.maximum(_rear_coil, 1.0), "fuel_x_inv_rear_corner_spring")
    )

    # ── D1 per-lap covariates (Unit D1 wiring) ─────────────────────────────
    # When ingested via --all-laps, ``CalibrationPoint`` carries lap-specific
    # values for fuel_remaining_l (vs fuel_l = stint start), tyre_temp_avg_c,
    # and driver_aggression_idx (front_shock_vel_p99 as a proxy).  Per-lap
    # variance in these IS signal — laps with hot tyres at low fuel have
    # different RH/deflection than the same setup with cold tyres at full
    # fuel. Add linear + interaction features so the regression can use them.
    #
    # **Bimodal-data guard:** legacy rows ingested before D1 have
    # tyre_temp_avg_c == 0.0 (default).  If left as zero, the regression
    # mis-interprets "tyre_temp=0" as a real cold-tyre datapoint and
    # learns a spurious slope between cold/warm rows that's actually
    # legacy-vs-per-lap data noise.  Replace zeros with the median of
    # the non-zero values so legacy rows contribute neutrally on the
    # tyre_temp / aggression axes (the rest of their features still vary,
    # so they contribute to OTHER coefficients honestly).
    _tyre_t_raw = col("tyre_temp_avg_c")
    _tyre_t_warm = _tyre_t_raw[_tyre_t_raw > 10.0]
    _tyre_t_median = float(np.median(_tyre_t_warm)) if _tyre_t_warm.size > 0 else 60.0
    _tyre_t = np.where(_tyre_t_raw > 10.0, _tyre_t_raw, _tyre_t_median)
    _aggr_raw = col("driver_aggression_idx")
    _aggr_nonzero = _aggr_raw[_aggr_raw > 1e-3]
    _aggr_median = float(np.median(_aggr_nonzero)) if _aggr_nonzero.size > 0 else 0.0
    _aggr = np.where(_aggr_raw > 1e-3, _aggr_raw, _aggr_median)
    _UNIVERSAL_POOL.append((_tyre_t, "tyre_temp"))
    _UNIVERSAL_POOL.append((_aggr, "driver_aggression"))
    # Tyre temp × spring compliance: hotter tyres = lower vertical stiffness
    # (smaller tyre k); shifts effective RH at speed proportional to 1/k.
    _UNIVERSAL_POOL.append((_tyre_t / np.maximum(_rear_spring, 1.0),
                            "tyre_temp_x_inv_spring"))
    _UNIVERSAL_POOL.append((_tyre_t / np.maximum(_rear_third, 1.0),
                            "tyre_temp_x_inv_third"))
    _UNIVERSAL_POOL.append((_tyre_t / np.maximum(_front_coil, 1.0),
                            "tyre_temp_x_inv_front_corner_spring"))
    # Driver aggression × damper-domain proxy (front_shock_vel p99): high
    # aggression on soft springs = more bottoming, on stiff = more grip
    # loss. Captures driver-style × setup interaction.
    _UNIVERSAL_POOL.append((_aggr / np.maximum(_rear_spring, 1.0),
                            "aggression_x_inv_spring"))
    _UNIVERSAL_POOL.append((_aggr / np.maximum(_rear_third, 1.0),
                            "aggression_x_inv_third"))
    # Per-lap fuel from D1 (separate from stint-start fuel_l). When the
    # legacy row has fuel_remaining_l=0 the std-filter drops this; on
    # per-lap rows the linear and squared terms capture fuel-burn drift.
    _fuel_rem = col("fuel_remaining_l")
    _UNIVERSAL_POOL.append((_fuel_rem, "fuel_remaining"))
    _UNIVERSAL_POOL.append((_fuel_rem * _fuel_rem, "fuel_remaining_sq"))

    # ── Physics-aware per-output feature pools ──
    # Each garage output is driven by features from a specific axle. The
    # universal forward selection (LOO RMSE-driven, physics-blind) was picking
    # cross-axis features as proxies due to multicollinearity in the small
    # calibration datasets — e.g. Ferrari front_ride_height picked
    # `inv_rear_spring` (coefficient -21934!) instead of `torsion_od`/`inv_od4`
    # despite Ferrari having a front torsion bar. Per-output pools eliminate
    # this by construction: front outputs only see front-axis features.
    _FRONT_AXIS_NAMES = {
        "front_pushrod", "front_pushrod_sq",
        "front_heave", "inv_front_heave",
        "front_heave_perch",
        "torsion_od", "inv_od4",
        "front_camber",
        "torsion_turns",       # front torsion bar preload (Ferrari/Acura; 0→excluded on BMW/Porsche)
        # GT3 paired-coil (W7.2) — zero on GTP IBTs, populated on GT3 IBTs.
        "front_corner_spring", "inv_front_corner_spring",
        "front_bump_rubber_gap",
        "fuel_x_inv_front_corner_spring",
        # D1 per-lap covariates: tyre warmup affects front-axle tyre rate.
        "tyre_temp_x_inv_front_corner_spring",
    }
    _REAR_AXIS_NAMES = {
        "rear_pushrod", "rear_pushrod_sq",
        "rear_third", "inv_rear_third",
        "rear_spring", "inv_rear_spring",
        "rear_third_perch", "rear_spring_perch",
        "rear_camber",
        "fuel_x_inv_spring", "fuel_x_inv_third",
        "rear_torsion_turns",  # rear torsion bar preload (Ferrari/Acura; 0→excluded on BMW/Porsche)
        # GT3 paired-coil (W7.2) — zero on GTP IBTs, populated on GT3 IBTs.
        # Splitter height affects rear via the aero balance shift it induces.
        "rear_corner_spring", "inv_rear_corner_spring",
        "rear_bump_rubber_gap",
        "splitter_height",
        "fuel_x_inv_rear_corner_spring",
        # D1 per-lap covariates: rear axle carries fuel weight directly.
        "tyre_temp_x_inv_spring", "tyre_temp_x_inv_third",
        "aggression_x_inv_spring", "aggression_x_inv_third",
    }
    _GLOBAL_NAMES = {
        "fuel", "wing",
        # D1 per-lap covariates that affect both axles symmetrically.
        "tyre_temp", "driver_aggression",
        "fuel_remaining", "fuel_remaining_sq",
    }

    def _filter_pool(allowed_names: set[str]) -> list[tuple]:
        return [(arr, name) for (arr, name) in _UNIVERSAL_POOL if name in allowed_names]

    _FRONT_POOL = _filter_pool(_FRONT_AXIS_NAMES | _GLOBAL_NAMES)
    _REAR_POOL = _filter_pool(_REAR_AXIS_NAMES | _GLOBAL_NAMES)

    def _pool_to_matrix(pool=None, row_mask: np.ndarray | None = None):
        """Build X matrix and names from feature pool, excluding constants.

        ``row_mask`` (Unit 9): boolean array selecting which rows to include
        in the X arrays. Used to drop synthesised virtual anchors when their
        inclusion makes the LOO fit worse than the real-only fit.
        """
        if pool is None:
            pool = _UNIVERSAL_POOL
        X_cols, names = [], []
        for arr, name in pool:
            arr_view = arr if row_mask is None else arr[row_mask]
            if len(np.unique(arr_view)) >= 2 and np.std(arr_view) > 1e-6:
                X_cols.append(arr_view)
                names.append(name)
        return X_cols, names

    def _virtual_target_for(col_name: str) -> str | None:
        """Map a y-column name → the virtual_anchors target key, or None."""
        col_to_target = {
            "static_front_rh_mm": "front_static_rh",
            "static_rear_rh_mm": "rear_static_rh",
            "front_shock_defl_static_mm": "front_shock_defl_static",
            "rear_shock_defl_static_mm": "rear_shock_defl_static",
            "rear_spring_defl_static_mm": "rear_spring_defl_static",
            "third_spring_defl_static_mm": "third_spring_defl_static",
            "heave_spring_defl_static_mm": "heave_spring_defl_static",
        }
        return col_to_target.get(col_name)

    def _row_mask_for_target(target_col_name: str, include_virtual: bool) -> np.ndarray | None:
        """Return a boolean row mask. None means "all rows".

        When ``include_virtual`` is False, virtual rows for non-matching
        targets are still included if they wrote to a different output
        column — but rows whose synthesised target is the *current* target
        get dropped. This keeps the augmentation per-target rather than
        global so different regression outputs can independently opt in/out.
        """
        if not _virtual_anchor_index:
            return None
        n_rows = len(rows)
        mask = np.ones(n_rows, dtype=bool)
        target_key = _virtual_target_for(target_col_name)
        if target_key is None:
            # No virtual anchors registered for this output; drop ALL synth rows
            # so virtual data for unrelated targets doesn't bleed into y=0.0.
            for idxs in _virtual_anchor_index.values():
                for i in idxs:
                    mask[i] = False
            return mask
        # Drop virtual rows that target a DIFFERENT output (their y for the
        # current target is 0.0 from the dataclass default — would poison y).
        for tgt_key, idxs in _virtual_anchor_index.items():
            if tgt_key == target_key:
                if not include_virtual:
                    for i in idxs:
                        mask[i] = False
            else:
                for i in idxs:
                    mask[i] = False
        return mask

    def _fit_one_pool(target_col_name, model_name, pool, min_std=0.5,
                      seed_features=None, include_virtual: bool = True):
        """Internal: fit a single pool. Returns FittedModel or None.

        ``include_virtual`` (Unit 9): when False, virtual anchors for THIS
        target are excluded from the X/y matrices. Used by _fit_from_pool
        to compare augmented-vs-real-only LOO RMSE.
        """
        row_mask = _row_mask_for_target(target_col_name, include_virtual)
        y_full = col(target_col_name)
        y = y_full if row_mask is None else y_full[row_mask]
        if np.std(y) < min_std:
            return None
        X_cols, names = _pool_to_matrix(pool, row_mask=row_mask)
        if not X_cols:
            return None
        X = np.column_stack(X_cols)
        X, names = _select_features(X, y, names, seed_features=seed_features)
        return _fit(X, y, names, model_name)

    def _fit_with_anchor_check(target_col_name, model_name, pool, min_std=0.5,
                                seed_features=None):
        """Fit twice (with and without virtual anchors); return the better one.

        This is the LOO-guarded augmentation. Virtual anchors only ship if
        they don't make the model demonstrably worse on real-data
        leave-one-out.

        Selection rules (in priority order):
          1. If the augmented fit is force-uncalibrated by ``_fit``'s
             LOO/train > 10x guard but the real-only fit is calibrated,
             return the real-only fit.
          2. Otherwise compare LOO RMSE on the model's own row set; the
             augmented fit wins when its LOO is lower OR within 5% of
             real-only LOO (small ties favour anchored intercept).
        """
        target_key = _virtual_target_for(target_col_name)
        # No virtual anchors for this target — single fit, single result.
        if target_key is None or target_key not in _virtual_anchor_index:
            return _fit_one_pool(target_col_name, model_name, pool, min_std,
                                  seed_features=seed_features,
                                  include_virtual=False)
        augmented = _fit_one_pool(target_col_name, model_name, pool, min_std,
                                   seed_features=seed_features,
                                   include_virtual=True)
        real_only = _fit_one_pool(target_col_name, model_name + "_real",
                                   pool, min_std,
                                   seed_features=seed_features,
                                   include_virtual=False)
        if augmented is None:
            return real_only
        if real_only is None:
            return augmented
        import logging
        _log = logging.getLogger(__name__)
        # Rule 1: if anchor-augmented fit is force-uncalibrated but real
        # alone is calibrated, prefer real-only.
        if real_only.is_calibrated and not augmented.is_calibrated:
            _log.info(
                "Virtual anchors disabled for '%s': augmented LOO/train "
                "ratio rejected, real-only fit accepted",
                model_name,
            )
            return real_only
        a_loo = augmented.loo_rmse
        r_loo = real_only.loo_rmse
        if (not np.isnan(a_loo) and not np.isnan(r_loo)
                and r_loo < a_loo * 0.95):
            _log.info(
                "Virtual anchors disabled for '%s': real-only LOO=%.3f "
                "beats augmented LOO=%.3f",
                model_name, r_loo, a_loo,
            )
            real_only.name = model_name
            return real_only
        return augmented

    def _fit_from_pool(target_col_name, model_name, pool=None, min_std=0.5,
                       fallback_pool=None, seed_features=None):
        """Fit a model from a feature pool with optional fallback.

        When ``fallback_pool`` is provided, fit BOTH pools and return whichever
        gives lower LOO RMSE. This implements the "physics-aware first, fall
        back to universal if it generalizes better" strategy: per-output pools
        eliminate cross-axis pollution where it helps (Ferrari front_ride_height),
        but the universal pool wins for outputs where cross-axis features were
        serving as effective regularization (e.g. Porsche, Acura small datasets
        with multicollinear physics terms).
        """
        # Unit 9 inner: _fit_with_anchor_check wraps _fit_one_pool with
        # virtual-data-anchor augmentation (selects augmented vs real-only
        # by LOO comparison; falls back to real-only when augmented fails
        # the LOO/train guard).
        primary = _fit_with_anchor_check(target_col_name, model_name, pool,
                                          min_std, seed_features=seed_features)
        free_fit = primary
        if fallback_pool is not None and primary is not None:
            fallback = _fit_with_anchor_check(target_col_name,
                                               model_name + "_fallback",
                                               fallback_pool, min_std,
                                               seed_features=seed_features)
            if fallback is not None:
                # Compare LOO RMSE — the honest generalization metric
                p_loo = primary.loo_rmse
                f_loo = fallback.loo_rmse
                if not np.isnan(p_loo) and not np.isnan(f_loo) and f_loo < p_loo:
                    import logging
                    logging.getLogger(__name__).info(
                        "Pool fallback for '%s': universal pool LOO=%.3f beats "
                        "physics-aware LOO=%.3f",
                        model_name, f_loo, p_loo,
                    )
                    # Fallback wins — restore the original model name
                    fallback.name = model_name
                    free_fit = fallback
                else:
                    free_fit = primary

        # Unit 6 outer: compliance-anchored physics fit. 2-parameter α/β
        # against F_aero / k_total — converges with ~5 setups vs ~21 for
        # the free fit. Keep whichever has lower LOO RMSE.
        from car_model.calibration import maybe_replace_with_anchored
        return maybe_replace_with_anchored(
            free_fit, rows, _car_obj, target_col_name,
        )

    # ─── 1. Front Ride Height ───
    # Use real-only std for the gate so virtual-anchor y=0 sentinels for
    # OTHER targets (rear RH, deflections) don't artificially inflate the
    # std and trigger an unwanted fit. The actual fit (_fit_from_pool) will
    # include the front_static_rh virtual rows via _row_mask_for_target.
    _front_rh_std = np.std(_real(col("static_front_rh_mm")))
    if _front_rh_std > 0.5:
        models.front_ride_height = _fit_from_pool(
            "static_front_rh_mm", "front_ride_height",
            pool=_FRONT_POOL, fallback_pool=_UNIVERSAL_POOL)
    elif len(unique) >= _MIN_SESSIONS_FOR_FIT and _front_rh_std > 0:
        # Near-constant front RH: create a constant model (intercept-only)
        _mean_frh = float(np.mean(_real(col("static_front_rh_mm"))))
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
    # Seed compliance features: rear RH depends on 1/k_third and 1/k_spring
    # via compliance physics (defl ∝ F/k). Without these seeds, forward
    # selection can drop them due to multicollinearity with other features,
    # causing 3mm+ errors at extreme spring settings.
    _REAR_RH_SEEDS = ["inv_rear_third", "inv_rear_spring", "rear_pushrod"]
    if np.std(_real(col("static_rear_rh_mm"))) > 0.5:
        models.rear_ride_height = _fit_from_pool(
            "static_rear_rh_mm", "rear_ride_height",
            pool=_REAR_POOL, fallback_pool=_UNIVERSAL_POOL,
            seed_features=_REAR_RH_SEEDS)

    # ─── 3. Torsion Bar Turns ───
    _tb_turns = col("torsion_bar_turns")
    _tb_turns_real = _real(_tb_turns)
    _tb_valid = _tb_turns_real[_tb_turns_real > 0]
    if len(_tb_valid) > 0 and np.std(_tb_turns_real) > 0.005:
        X = np.column_stack([
            _real(1.0 / np.maximum(heave, 1.0)),
            _real(col("front_heave_perch_mm")),
            _real(col("front_torsion_od_mm")),
        ])
        models.torsion_bar_turns = _fit(
            X, _real(col("torsion_bar_turns")),
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
    if np.std(_real(col("torsion_bar_defl_mm"))) > 0.5:
        # Fit the load form (defl * OD^4) for DeflectionModel compatibility.
        # Real-only (no virtual anchors) — torsion_bar_defl_mm is not a
        # virtual_anchors target, so synth rows would have y=0 and poison
        # the load-form fit.
        y_load = _real(col("torsion_bar_defl_mm") * od4)
        X_load = np.column_stack([_real(heave), _real(col("front_heave_perch_mm"))])
        models.torsion_bar_defl = _fit(
            X_load, y_load,
            ["front_heave", "front_heave_perch"],
            "torsion_bar_defl_load",
        )
        # Also fit a DIRECT model on torsion_bar_defl_mm itself (for
        # DirectRegression bypass of DeflectionModel's load/k_torsion path).
        # Uses universal feature pool for maximum accuracy.
        # Front torsion bar — front-axis only (Ferrari/BMW have a front torsion bar)
        models.torsion_bar_defl_direct = _fit_from_pool(
            "torsion_bar_defl_mm", "torsion_bar_defl_direct",
            pool=_FRONT_POOL, fallback_pool=_UNIVERSAL_POOL)

    # ─── 5–7. Heave deflections (static, max, slider) ───
    # Heave spring is on the front axle for all 4 cars (BMW/Porsche/Ferrari/Acura),
    # so heave outputs use the front-axis pool first; fall back to universal if
    # the universal pool generalizes better (LOO RMSE).
    models.heave_spring_defl_static = _fit_from_pool(
        "heave_spring_defl_static_mm", "heave_spring_defl_static",
        pool=_FRONT_POOL, fallback_pool=_UNIVERSAL_POOL)
    models.heave_spring_defl_max = _fit_from_pool(
        "heave_spring_defl_max_mm", "heave_spring_defl_max",
        pool=_FRONT_POOL, fallback_pool=_UNIVERSAL_POOL, min_std=1.0)
    models.heave_slider_defl_static = _fit_from_pool(
        "heave_slider_defl_static_mm", "heave_slider_defl_static",
        pool=_FRONT_POOL, fallback_pool=_UNIVERSAL_POOL)

    # ─── 8. Front Shock Deflection Static ───
    models.front_shock_defl_static = _fit_from_pool(
        "front_shock_defl_static_mm", "front_shock_defl_static",
        pool=_FRONT_POOL, fallback_pool=_UNIVERSAL_POOL)

    # ─── 9. Rear Shock Deflection Static ───
    # Rear shock deflection depends on rear pushrod (geometric), rear third perch
    # (load path through third/heave spring vs. corner shock), and rear third
    # spring stiffness (how much the third spring carries).
    # ─── 9. Rear Shock Deflection Static ───
    models.rear_shock_defl_static = _fit_from_pool(
        "rear_shock_defl_static_mm", "rear_shock_defl_static",
        pool=_REAR_POOL, fallback_pool=_UNIVERSAL_POOL)

    # ─── 10. Rear Spring Deflection Static ───
    # Compliance physics: defl ∝ F/k (1/spring) under aero load. Include
    # cross-spring effect (third), perches, and pushrod for a complete model.
    models.rear_spring_defl_static = _fit_from_pool(
        "rear_spring_defl_static_mm", "rear_spring_defl_static",
        pool=_REAR_POOL, fallback_pool=_UNIVERSAL_POOL)

    # ─── 11. Rear Spring Deflection Max ───
    models.rear_spring_defl_max = _fit_from_pool(
        "rear_spring_defl_max_mm", "rear_spring_defl_max",
        pool=_REAR_POOL, fallback_pool=_UNIVERSAL_POOL, min_std=1.0)

    # ─── 12. Third Spring Deflection Static ───
    # Same compliance pattern as rear_spring_defl_static.
    models.third_spring_defl_static = _fit_from_pool(
        "third_spring_defl_static_mm", "third_spring_defl_static",
        pool=_REAR_POOL, fallback_pool=_UNIVERSAL_POOL)

    # ─── 13. Third Spring Deflection Max ───
    models.third_spring_defl_max = _fit_from_pool(
        "third_spring_defl_max_mm", "third_spring_defl_max",
        pool=_REAR_POOL, fallback_pool=_UNIVERSAL_POOL, min_std=1.0)

    # ─── 14. Third Slider Static ───
    # Fit BOTH the chained model (from third_spring_defl) and a direct model
    # (from setup features). The direct model bypasses chained error amplification.
    # Real-only for the chained fit — third_slider y is not a virtual target,
    # so synth rows would have y=0 and poison the slope.
    if np.std(_real(col("third_slider_defl_static_mm"))) > 0.5:
        X = np.column_stack([_real(col("third_spring_defl_static_mm"))])
        models.third_slider_defl_static = _fit(
            X, _real(col("third_slider_defl_static_mm")),
            ["third_spring_defl"],
            "third_slider_defl_static",
        )
    # Direct third slider model from setup features (third spring is on the rear axle)
    models.third_slider_defl_direct = _fit_from_pool(
        "third_slider_defl_static_mm", "third_slider_defl_direct",
        pool=_REAR_POOL, fallback_pool=_UNIVERSAL_POOL)

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
    # heave_index_unvalidated is a shared (front+rear) validation flag: when True,
    # the index→N/mm mapping has not been verified and index-space settings must be skipped.
    _rear_index_unvalidated = _heave_model is not None and getattr(_heave_model, "heave_index_unvalidated", False)
    m_effs_rear: list[tuple[float, float]] = []  # (setting, m_eff) tuples
    for pt in unique:
        if pt.rear_shock_vel_p99_mps > 0.01 and pt.rear_sigma_mm > 0.1 and pt.rear_third_setting > 0:
            if _rear_uses_index:
                if _rear_index_unvalidated:
                    continue  # skip — index→N/mm mapping not verified for rear
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

    # ─── 15c. Per-corner per-phase regressions (Unit D3) ────────────────────
    # For every (corner_id, phase, metric) triplet observed in ≥
    # _MIN_SESSIONS_FOR_FIT distinct setups with non-trivial variance, fit a
    # regression using _UNIVERSAL_POOL features. Unit P1 (sibling) consumes
    # these to produce per-corner predicted impacts of setup deltas.
    # Implements Principle 6 (corner-by-corner causal): aggregate metrics alone
    # cannot tell the user *which* corners a change will hurt or help.
    try:
        # Collect every key seen across all unique calibration points.
        triplet_to_values: dict[str, list[float]] = {}
        triplet_to_indices: dict[str, list[int]] = {}
        for idx, pt in enumerate(unique):
            cpm = getattr(pt, "corner_phase_metrics", None) or {}
            for key, val in cpm.items():
                if not isinstance(val, (int, float)):
                    continue
                if not np.isfinite(val):
                    continue
                triplet_to_values.setdefault(key, []).append(float(val))
                triplet_to_indices.setdefault(key, []).append(idx)

        # Need enough coverage AND variance to fit. Same gates as the other
        # regressions (3:1 sample-to-feature, std > epsilon).
        # Minimum: every unique setup must report this triplet so the X matrix
        # rows align — requires len(values) == len(unique).
        # Variance gate: std > 0.01 in metric units (deg / g / fraction).
        n_fit_triplets = 0
        for key, values in triplet_to_values.items():
            if len(values) != len(unique):
                continue
            if len(values) < _MIN_SESSIONS_FOR_FIT:
                continue
            arr = np.asarray(values, dtype=float)
            if np.std(arr) < 0.01:
                continue
            X_cols, names = _pool_to_matrix(_UNIVERSAL_POOL)
            if not X_cols:
                continue
            X = np.column_stack(X_cols)
            X, names = _select_features(X, arr, names)
            fitted = _fit(X, arr, names, key)
            # Keep only models that pass the calibration gate; otherwise the
            # fit will mislead Unit P1 with high-R²-but-LOO-collapsed terms.
            if fitted.is_calibrated:
                models.corner_phase_models[key] = fitted
                n_fit_triplets += 1

        if n_fit_triplets > 0:
            models.status["corner_phase_models"] = (
                f"calibrated ({n_fit_triplets} (corner, phase, metric) triplets fit)"
            )
        elif triplet_to_values:
            models.status["corner_phase_models"] = (
                f"insufficient data ({len(triplet_to_values)} triplets observed, "
                f"none passed gate)"
            )
    except Exception as _cpm_err:
        import logging
        logging.getLogger(__name__).debug(
            "Corner-phase model fitting skipped: %s", _cpm_err
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

# Legacy threshold preserved for status messages.  The tier system has fully
# replaced this as the gate: ``confidence_tier == "insufficient"`` is the
# canonical signal for "do not apply this model".
_OVERFIT_LOO_TRAIN_RATIO = 10.0


def _is_overfit(model) -> bool:
    """Return True if a fitted model is too unreliable to apply.

    Under the tier system this delegates to ``confidence_tier == "insufficient"``
    so the legacy LOO/train > 10× heuristic and the new tier rules cannot
    drift apart.  Models at tier ``low`` (LOO/train 10×–20×) are NOT flagged
    as overfit — the solver applies them with a warning, in line with
    Principle 4 (continuous learning, tiered confidence).

    Used by ``_mk_direct()`` and ``apply_to_car()`` to refuse catastrophically
    overfit models for garage prediction or .sto serialization.
    """
    if model is None:
        return False
    tier = getattr(model, "confidence_tier", None)
    if tier is not None:
        return tier == "insufficient"
    # Backward-compat path for legacy FittedModel dicts loaded before tiers
    # existed: fall through to the original LOO/train threshold.
    rmse = getattr(model, "rmse", None)
    loo_rmse = getattr(model, "loo_rmse", None)
    n = getattr(model, "n_samples", 0)
    if rmse is None or loo_rmse is None or n is None or n < 5:
        return False
    if np.isnan(loo_rmse):
        return False
    return loo_rmse > _OVERFIT_LOO_TRAIN_RATIO * max(rmse, 1e-6)


_SAFETY_CRITICAL_DIRECT_MODELS = frozenset({
    "heave_spring_defl_max",
    "third_spring_defl_max",
    "rear_spring_defl_max",
    "heave_spring_defl_static",
    "third_spring_defl_static",
    "rear_spring_defl_static",
    "front_shock_defl_static",
    "rear_shock_defl_static",
    "third_slider_defl_static",
    "heave_slider_defl_static",
    "front_ride_height",
    "rear_ride_height",
})


def _mk_direct(fitted_model) -> "DirectRegression | None":
    """Build a DirectRegression from a fitted model.

    Tier policy (matches apply_to_car safety-critical gate):
      - ``insufficient``: rejected (R²<0.30, overfit, or n<features).
      - ``low``: rejected for safety-critical model names (deflection /
        travel-budget / static-RH).  These feed legality + heave travel
        budget calculations, where unreliable predictions produce
        non-physical outputs (e.g. DeflMax≈0 → INVALID setup).
      - ``low`` non-safety-critical, ``medium``, ``high``: accepted.

    Returns None when rejected.
    """
    if fitted_model is None or not fitted_model.coefficients:
        return None
    if fitted_model.r_squared < 0.30 and len(fitted_model.feature_names) > 0:
        import logging
        logging.getLogger(__name__).info(
            "DirectRegression skipped for '%s' — R²=%.3f too low",
            fitted_model.name, fitted_model.r_squared,
        )
        return None
    if _is_overfit(fitted_model):
        import logging
        ratio = fitted_model.loo_rmse / max(fitted_model.rmse, 1e-6)
        tier = getattr(fitted_model, "confidence_tier", "n/a")
        logging.getLogger(__name__).warning(
            "DirectRegression skipped for '%s' — tier=%s, LOO/train ratio %.0fx",
            fitted_model.name, tier, ratio,
        )
        return None
    # Safety-critical gate (a): low-tier deflection / RH models can produce
    # non-physical predictions at default lap-conditions (e.g. DeflMax≈0)
    # that corrupt the legality engine.
    tier = getattr(fitted_model, "confidence_tier", "high")
    if tier == "low" and fitted_model.name in _SAFETY_CRITICAL_DIRECT_MODELS:
        import logging
        logging.getLogger(__name__).warning(
            "DirectRegression skipped for '%s' — tier=low and "
            "safety-critical (would corrupt legality / travel-budget). "
            "Garage predictions for this output fall back to physics defaults.",
            fitted_model.name,
        )
        return None
    # Safety-critical gate (b): physical-range sanity check on the
    # intercept.  A deflection_max regression intercept of -119 mm or
    # 0 mm is non-physical regardless of how good R²/LOO look (per-track
    # fits with limited data can produce high-confidence-but-wrong
    # coefficients).  Spring max-deflection intercepts must be in a
    # plausible range (50–250 mm for heave/third/rear; the regression
    # adjusts via slope×spring_rate from there).
    if (fitted_model.name in _SAFETY_CRITICAL_DIRECT_MODELS
            and "defl_max" in fitted_model.name
            and len(fitted_model.coefficients) > 0):
        intercept = fitted_model.coefficients[0]
        if not (50.0 <= intercept <= 250.0):
            import logging
            logging.getLogger(__name__).warning(
                "DirectRegression skipped for '%s' — intercept %.1f mm "
                "outside physical range [50, 250] (overfit per-track "
                "regression with non-physical coefficients).  Garage "
                "predictions fall back to physics defaults.",
                fitted_model.name, intercept,
            )
            return None
    from car_model.garage import DirectRegression
    return DirectRegression.from_model(
        fitted_model.coefficients,
        fitted_model.feature_names,
        confidence_tier=getattr(fitted_model, "confidence_tier", "low"),
    )


def _mk_direct_torsion(fitted_model, torsion_c: float) -> "DirectRegression | None":
    """Build a DirectRegression for torsion bar deflection.

    Uses the direct-form model (fitted on torsion_bar_defl_mm itself, not the
    load form) when available.
    """
    # The fitted_model here is models.torsion_bar_defl_direct (direct form)
    if fitted_model is None or not fitted_model.is_calibrated:
        return None
    from car_model.garage import DirectRegression
    return DirectRegression.from_model(
        fitted_model.coefficients,
        fitted_model.feature_names,
        confidence_tier=getattr(fitted_model, "confidence_tier", "low"),
    )


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
        # Front RH not calibrated.  If the calibration data shows near-constant
        # front RH (e.g. Acura), use the mean as a constant intercept.  If there
        # is NO data at all, return None — the calibration gate blocks this car.
        import logging
        _logger = logging.getLogger(__name__)
        car_name = getattr(car_obj, "canonical_name", "")
        pts = load_calibration_points(car_name) if car_name else []
        valid_rhs = [p.static_front_rh_mm for p in pts if p.static_front_rh_mm > 0]
        if not valid_rhs:
            _logger.warning(
                "Front RH uncalibrated for %s and no calibration data — "
                "cannot build GarageOutputModel", car_name)
            return None
        front_intercept = sum(valid_rhs) / len(valid_rhs)
        _logger.info(
            "Front RH uncalibrated for %s — constant model mean=%.1f mm "
            "from %d points (std=%.3f mm)",
            car_name, front_intercept, len(valid_rhs),
            (sum((r - front_intercept)**2 for r in valid_rhs) / len(valid_rhs))**0.5,
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

    # Heave defl max from calibration.
    # Initialise from car's HeaveSpringModel physics defaults (106.43,
    # -0.310 for Dallara LMDh; populated per-car).  Only overwrite with
    # regression coefficients when the model is non-safety-critical or
    # tier ≥ medium — at tier=low the regression can produce coefficients
    # that give DeflMax≈0 mm at typical heave rates, corrupting the
    # legality engine.
    defl_max_intercept = float(getattr(hsm, "heave_spring_defl_max_intercept_mm", 106.43) or 106.43)
    defl_max_slope = float(getattr(hsm, "heave_spring_defl_max_slope", -0.310) or -0.310)
    _dm_tier = getattr(models.heave_spring_defl_max, "confidence_tier", "high") if models.heave_spring_defl_max else None
    _dm_safe = (_dm_tier in ("high", "medium")) or (
        _dm_tier == "low"
        and "heave_spring_defl_max" not in _SAFETY_CRITICAL_DIRECT_MODELS
    )
    if models.heave_spring_defl_max and _dm_safe:
        dm = models.heave_spring_defl_max
        _candidate_intercept = dm.coefficients[0] if len(dm.coefficients) > 0 else defl_max_intercept
        # Physical-range sanity: spring max-deflection intercepts must be
        # in the plausible [50, 250] mm range.  Per-track fits with
        # limited variance can produce high-confidence-but-wrong
        # coefficients (intercept=-119 mm, etc.).  Reject and use
        # physics fallback when the fitted intercept is unphysical.
        if 50.0 <= _candidate_intercept <= 250.0:
            defl_max_intercept = _candidate_intercept
            for i, feat in enumerate(dm.feature_names):
                if i + 1 < len(dm.coefficients) and feat == "front_heave":
                    defl_max_slope = dm.coefficients[i + 1]
        else:
            import logging
            logging.getLogger(__name__).warning(
                "GarageOutputModel: heave_spring_defl_max intercept "
                "%.1f mm outside physical [50, 250] range — falling "
                "back to physics default %.1f mm.",
                _candidate_intercept, defl_max_intercept,
            )

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
        default_rear_camber_deg=car_obj.geometry.rear_camber_baseline_deg,
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
        # Direct regressions — bypass DeflectionModel for sub-0.1mm accuracy
        _direct_front_rh=_mk_direct(models.front_ride_height),
        _direct_rear_rh=_mk_direct(models.rear_ride_height),
        _direct_front_shock=_mk_direct(models.front_shock_defl_static),
        _direct_heave_defl_static=_mk_direct(models.heave_spring_defl_static),
        _direct_heave_slider=_mk_direct(models.heave_slider_defl_static),
        _direct_heave_defl_max=_mk_direct(models.heave_spring_defl_max),
        _direct_rear_shock=_mk_direct(models.rear_shock_defl_static),
        _direct_torsion_defl=_mk_direct_torsion(models.torsion_bar_defl_direct, csm.front_torsion_c),
        _direct_rear_spring_defl=_mk_direct(models.rear_spring_defl_static),
        _direct_rear_spring_defl_max=_mk_direct(models.rear_spring_defl_max),
        _direct_third_defl=_mk_direct(models.third_spring_defl_static),
        _direct_third_defl_max=_mk_direct(models.third_spring_defl_max),
        # third_slider: prefer direct model (from setup features) over chained
        # model (from predicted third_spring_defl) to avoid error amplification.
        _direct_third_slider=_mk_direct(models.third_slider_defl_direct),
    )


# Apply calibrated models to car object
# ─────────────────────────────────────────────────────────────────────────────

def apply_to_car(car_obj, models: CarCalibrationModels) -> list[str]:
    """Apply fitted models to a car object from car_model/cars.py.

    Modifies the car object in-place. Returns list of applied correction notes.

    Tier handling (Principle 4 — continuous learning, tiered confidence):
      - ``high`` / ``medium`` — applied silently
      - ``low`` — applied with a logged warning and an "applied at low tier"
        note in the returned list
      - ``insufficient`` — skipped entirely (no application, no silent corruption)

    Models flagged as ``insufficient`` (the new tier name for the legacy
    LOO/train > 10× guard) are skipped and their names recorded for a
    single combined warning.
    """
    applied = []
    _skipped_overfit: list[str] = []
    _low_tier_applied: list[str] = []

    if not models:
        return applied

    # ── GT3 short-circuit (W7.2 audit BLOCKER #22) ──
    # GT3 cars have ``heave_spring=None`` and ``front_torsion_c=0.0``. Every
    # write block below targets GTP-shaped attributes (``car.heave_spring.*``,
    # ``car.deflection.heave_*``, ``car.corner_spring.front_torsion_c``) and
    # silently swallows AttributeError on GT3, leaving the car uncalibrated
    # without explanation. Until varied-spring GT3 IBTs land (gated on W10.1
    # capture), the regression fits are intercept-only — there is nothing to
    # write into the corner_spring / garage_ranges fields anyway. Detect GT3
    # structurally via ``suspension_arch.has_heave_third`` and return early
    # with a documented note so callers know calibration ran but produced no
    # usable corrections.
    _arch = getattr(car_obj, "suspension_arch", None)
    _is_gt3 = _arch is not None and not _arch.has_heave_third
    if _is_gt3:
        import logging as _logging
        _gt3_logger = _logging.getLogger(__name__)
        _n_setups = getattr(models, "n_unique_setups", 0)
        _gt3_logger.info(
            "apply_to_car: GT3 path for '%s' — %d unique setups; regression "
            "fits are intercept-only until varied-spring IBT data lands "
            "(W10.1). No GTP-shaped writes attempted.",
            getattr(car_obj, "canonical_name", "<unknown>"),
            _n_setups,
        )
        applied.append(
            f"GT3 calibration applied (intercept-only — {_n_setups} unique setups; "
            "varied-spring IBT data needed for full regression fit, see W10.1)"
        )
        # TODO(W10.1): once 5+ varied-front-coil-rate IBTs land for the same
        # GT3 car at the same track, write fitted compliance back into
        # ``car_obj.corner_spring.front_baseline_rate_nmm`` (intercept of the
        # regression on ``static_front_rh_mm`` vs ``inv_front_corner_spring``
        # gives a baseline; the slope gives compliance). ``garage_ranges``
        # bump_rubber_gap / splitter_height bounds are driver-tuned, not
        # regression outputs, so do NOT overwrite those.
        return applied

    # Deflection-model names whose predictions feed legality / travel-budget
    # checks (heave bottoming, available-travel margin).  These must be
    # tier ≥ medium because tier=low predictions can produce non-physical
    # values (e.g. DeflMax≈0) that flag the setup INVALID.  Low-tier
    # predictions are useful as scoring inputs but unreliable for
    # safety-critical static-travel headroom calculations.
    _SAFETY_CRITICAL_MODEL_NAMES = frozenset({
        "heave_spring_defl_max",
        "third_spring_defl_max",
        "rear_spring_defl_max",
        "heave_spring_defl_static",
        "third_spring_defl_static",
        "rear_spring_defl_static",
        "front_shock_defl_static",
        "rear_shock_defl_static",
        "third_slider_defl_static",
        "heave_slider_defl_static",
        "front_ride_height",
        "rear_ride_height",
    })

    def _ok(m, min_coefs: int = 1) -> bool:
        """Return True if model is usable.

        Tier policy (Principle 4 — continuous learning, tiered confidence):
          - ``insufficient``: rejected unconditionally.
          - ``low``: rejected for safety-critical models (deflection /
            travel-budget / static-RH); accepted for advisory models
            (per-corner-phase, sensitivity scoring).
          - ``high`` / ``medium``: accepted.

        Safety-critical models that escape this gate would corrupt the
        legality engine (e.g. predict DeflMax≈0 → "0 mm available travel"
        → all setups marked INVALID).  We'd rather fall back to physics
        defaults than ship an INVALID recommended setup.
        """
        if m is None or len(m.coefficients) < min_coefs:
            return False
        # Never apply a model that the fitting process itself flagged as uncalibrated.
        if not getattr(m, "is_calibrated", True):
            return False
        if _is_overfit(m):
            if m.name not in _skipped_overfit:
                _skipped_overfit.append(m.name)
            return False
        tier = getattr(m, "confidence_tier", "high")
        if tier == "low":
            if m.name in _SAFETY_CRITICAL_MODEL_NAMES:
                # Reject — would feed legality / travel-budget logic with
                # unreliable predictions.  Use physics fallback instead.
                if m.name not in _skipped_overfit:
                    _skipped_overfit.append(f"{m.name} (low-tier, safety-critical)")
                return False
            if m.name not in _low_tier_applied:
                _low_tier_applied.append(m.name)
        return True

    # Apply DeflectionModel coefficients — apply whatever is available
    # (Porsche has no front torsion bar, so front_shock_defl_static may be None)
    _defl_applied = False
    if (_ok(models.heave_spring_defl_static)
            or _ok(models.front_shock_defl_static)
            or _ok(models.rear_shock_defl_static)):
        try:
            defl = car_obj.deflection
            fs = models.front_shock_defl_static
            if _ok(fs, 2):
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
            if _ok(rs, 2):
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
            if _ok(hs, 4):
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
            if _ok(rsd):
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
            if _ok(tsd):
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
            if _ok(tbd, 3):
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
            if _ok(rsm, 2):
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
            if _ok(tsm, 2):
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
            if _ok(tsl, 2):
                defl.third_slider_intercept = tsl.coefficients[0]
                if tsl.feature_names and tsl.feature_names[0] in (
                        "third_spring_defl_static", "third_defl_static",
                        "third_spring_defl"):
                    defl.third_slider_spring_defl_coeff = tsl.coefficients[1]
                _defl_applied = True

            # Heave spring deflection max
            hdm = models.heave_spring_defl_max
            if _ok(hdm, 2):
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
    if _ok(models.front_ride_height) and _ok(models.rear_ride_height):
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
        # Existing GOM (e.g. BMW hand-calibrated): rebuild from calibration
        # models to ensure ALL coefficients (RH + deflection) are up to date.
        new_gom = build_garage_output_model(car_obj, models)
        if new_gom is not None:
            # Preserve track-specific metadata from the original GOM
            old_gom = car_obj.garage_output_model
            new_gom.name = old_gom.name
            new_gom.track_keywords = old_gom.track_keywords
            new_gom.front_rh_floor_mm = old_gom.front_rh_floor_mm
            new_gom.max_slider_mm = old_gom.max_slider_mm
            new_gom.min_static_defl_mm = old_gom.min_static_defl_mm
            new_gom.max_torsion_bar_defl_mm = old_gom.max_torsion_bar_defl_mm
            new_gom.torsion_bar_defl_safety_margin_mm = old_gom.torsion_bar_defl_safety_margin_mm
            # Use calibrated deflection model
            _car_defl = car_obj.deflection
            if _car_defl.is_calibrated:
                new_gom.deflection = _car_defl
            car_obj.garage_output_model = new_gom
            applied.append("GarageOutputModel rebuilt from calibration models")

    # Surface tier-related model handling (Principle 4 — continuous learning,
    # tiered confidence).  ``insufficient`` models were skipped entirely;
    # ``low`` models were applied but predictions carry reduced confidence.
    if _skipped_overfit:
        import logging
        logging.getLogger(__name__).warning(
            "apply_to_car: skipped %d insufficient-tier model(s) "
            "(LOO/train > %.0fx or R² < 0.30): %s",
            len(_skipped_overfit), _OVERFIT_LOO_TRAIN_RATIO,
            ", ".join(sorted(_skipped_overfit)),
        )
        applied.append(
            f"⚠ skipped {len(_skipped_overfit)} insufficient model(s): "
            f"{', '.join(sorted(_skipped_overfit))}"
        )
    if _low_tier_applied:
        import logging
        logging.getLogger(__name__).warning(
            "apply_to_car: applied %d low-tier model(s) "
            "(R²≥0.30 or LOO/train≥2×): %s",
            len(_low_tier_applied),
            ", ".join(sorted(_low_tier_applied)),
        )
        applied.append(
            f"⚠ applied {len(_low_tier_applied)} low-tier model(s) "
            f"(reduced confidence): {', '.join(sorted(_low_tier_applied))}"
        )

    return applied


# ─────────────────────────────────────────────────────────────────────────────
# Sweep protocol generator (Model 3 of Claude Code's plan)
# ─────────────────────────────────────────────────────────────────────────────

_GT3_CARS = ("bmw_m4_gt3", "aston_martin_vantage_gt3", "porsche_992_gt3r")


_GT3_PROTOCOL_HINT = """\
GT3 calibration protocol (placeholder — see docs/calibration_guide.md)

GT3 cars (BMW M4 GT3 EVO, Aston Martin Vantage GT3 EVO, Porsche 911 GT3 R)
have a paired-coil suspension architecture: NO heave springs, NO third
springs, NO front torsion bar. The calibration sweep is therefore quite
different from the GTP recipe.

Steps:
  1. Vary front corner spring rate across the legal garage range (e.g.
     BMW M4 GT3 EVO: 190–340 N/mm, step 10 N/mm). Take an IBT for each
     setting (3+ clean laps each).
  2. Repeat for rear corner spring rate.
  3. Optionally: vary bump rubber gap (front + rear) and splitter height
     to populate those axes; these are typically driver-tuned and don't
     need varied-spring sweeps.
  4. Run: python -m car_model.auto_calibrate --car {car} --ibt-dir <dir>
  5. Until 5+ varied-spring IBTs land at the same track, the regression
     fits are intercept-only and apply_to_car() does not write anything
     into car.corner_spring.* / car.garage_ranges.* (W10.1 unblocks this).
"""


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


def _car_protocol_hint(car: str) -> str:
    """Return a human-readable protocol hint for *car*.

    GT3 cars get a generic GT3 paired-coil hint (W7.2 audit DEGRADED #18) so
    they no longer fall through to the BMW GTP hint, which references
    nonexistent heave / torsion bar parameters. Other cars use the existing
    per-car hint dict.
    """
    if car in _GT3_CARS:
        return _GT3_PROTOCOL_HINT.format(car=car)
    if car in _CAR_PROTOCOL_HINTS:
        # Backwards-compat: format the dict block similar to legacy callers.
        h = _CAR_PROTOCOL_HINTS[car]
        return h.get("extra_note", "") if isinstance(h, dict) else str(h)
    # Unknown car: keep the legacy fall-through to BMW so existing GTP
    # consumers still work. Future audit cleanups may tighten this.
    legacy = _CAR_PROTOCOL_HINTS.get("bmw", {})
    return legacy.get("extra_note", "") if isinstance(legacy, dict) else str(legacy)


def generate_protocol(car: str, verbose: bool = True) -> str:
    """Generate step-by-step iRacing calibration sweep instructions.

    Based on Claude Code's calibration plan (docs/auto_calibration_plan.md):
    Each step changes ONE parameter to isolate physics effects.

    For GT3 cars (W7.2 audit DEGRADED #18) the protocol diverges entirely
    from the GTP heave/torsion sweep — there are no heave springs, no front
    torsion bar — so we emit the generic GT3 hint instead of falling through
    to the BMW GTP hint dict.
    """
    points = load_calibration_points(car)
    models = load_calibrated_models(car)
    if car in _GT3_CARS:
        # GT3: short-circuit to the paired-coil protocol. The full status
        # block below assumes GTP-shaped models so we render a focused GT3
        # block instead.
        n_unique = len({_setup_key(pt) for pt in points})
        return (
            f"\n{'=' * 60}\n"
            f"  {car.upper()} Calibration Protocol\n"
            f"  (GT3 paired-coil architecture)\n"
            f"{'=' * 60}\n\n"
            f"  {n_unique} unique setups collected so far "
            f"(need {_MIN_SESSIONS_FOR_FIT} minimum)\n\n"
            f"{_GT3_PROTOCOL_HINT.format(car=car)}"
        )
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
                    "confidence_tier": getattr(m, "confidence_tier", "insufficient"),
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
        _tier_icon = {
            "high": "✅",
            "medium": "✅",
            "low": "⚠️",
            "insufficient": "❌",
        }
        for name, m in s["fitted_models"].items():
            tier = m.get("confidence_tier", "insufficient")
            bar = _tier_icon.get(tier, "❌")
            tier_tag = f"  tier={tier}"
            print(
                f"    {bar} {name:<35} R²={m['r_squared']:.3f}  "
                f"RMSE={m['rmse']:.2f}  n={m['n']}{tier_tag}"
            )

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
# Calibration sweep generator
# ─────────────────────────────────────────────────────────────────────────────

def _run_generate_sweep(car_name: str, ibt_path_str: str) -> None:
    """Generate calibration sweep .sto files from a baseline IBT session.

    Reads the driver's current setup from the IBT, then creates 7-9 varied
    setups (each changing ONE parameter) that maximise calibration information.
    Each .sto can be loaded in iRacing — a single outlap generates one
    calibration point (the IBT header contains all the garage ground truth).
    """
    from car_model.cars import get_car
    from analyzer.setup_reader import CurrentSetup
    from track_model.ibt_parser import IBTFile

    ibt_path = Path(ibt_path_str)
    if not ibt_path.exists():
        print(f"  [error] IBT file not found: {ibt_path}", file=sys.stderr)
        return

    car = get_car(car_name)
    ibt = IBTFile(str(ibt_path))
    setup = CurrentSetup.from_ibt(ibt, car_canonical=car_name)
    gr = car.garage_ranges

    out_dir = Path("output") / "calibration_sweep" / car_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build variation plan: each entry = (name, field_to_change, new_value)
    variations: list[tuple[str, dict]] = [("baseline", {})]

    # Heave spring sweep (3 levels spanning the range)
    heave_lo, heave_hi = gr.front_heave_nmm
    heave_base = setup.front_heave_nmm
    heave_step = gr.heave_spring_resolution_nmm
    heave_vals = sorted(set([
        max(heave_lo, round((heave_lo + (heave_hi - heave_lo) * 0.15) / heave_step) * heave_step),
        round(heave_base / heave_step) * heave_step,
        min(heave_hi, round((heave_lo + (heave_hi - heave_lo) * 0.85) / heave_step) * heave_step),
    ]))
    for i, h in enumerate(heave_vals):
        if abs(h - heave_base) > heave_step * 0.5:
            variations.append((f"heave_{int(h)}", {"front_heave_nmm": h}))

    # Rear spring sweep (for cars with coil springs)
    if gr.rear_spring_nmm[1] > gr.rear_spring_nmm[0]:
        rs_lo, rs_hi = gr.rear_spring_nmm
        rs_base = setup.rear_spring_nmm
        rs_step = gr.rear_spring_resolution_nmm
        rs_vals = sorted(set([
            max(rs_lo, round((rs_lo + (rs_hi - rs_lo) * 0.2) / rs_step) * rs_step),
            min(rs_hi, round((rs_lo + (rs_hi - rs_lo) * 0.8) / rs_step) * rs_step),
        ]))
        for r in rs_vals:
            if abs(r - rs_base) > rs_step * 0.5:
                variations.append((f"rspring_{int(r)}", {"rear_spring_nmm": r}))

    # Rear third/heave sweep
    if gr.rear_third_nmm[1] > gr.rear_third_nmm[0]:
        rt_lo, rt_hi = gr.rear_third_nmm
        rt_base = setup.rear_third_nmm
        rt_step = heave_step
        rt_vals = sorted(set([
            max(rt_lo, round((rt_lo + (rt_hi - rt_lo) * 0.2) / rt_step) * rt_step),
            min(rt_hi, round((rt_lo + (rt_hi - rt_lo) * 0.8) / rt_step) * rt_step),
        ]))
        for r in rt_vals:
            if abs(r - rt_base) > rt_step * 0.5:
                variations.append((f"third_{int(r)}", {"rear_third_nmm": r}))

    # Pushrod sweep (front ±4mm)
    push_res = gr.pushrod_resolution_mm
    push_lo, push_hi = gr.front_pushrod_mm
    for delta in [-4.0, 4.0]:
        new_push = round((setup.front_pushrod_mm + delta) / push_res) * push_res
        new_push = max(push_lo, min(push_hi, new_push))
        if abs(new_push - setup.front_pushrod_mm) > push_res * 0.5:
            label = "push_neg4" if delta < 0 else "push_pos4"
            variations.append((label, {"front_pushrod_mm": new_push}))

    # Camber sweep (front +0.5, -0.5 from base)
    cam_lo, cam_hi = gr.camber_front_deg
    for delta in [-0.5, 0.5]:
        new_cam = round((setup.front_camber_deg + delta) * 10) / 10
        new_cam = max(cam_lo, min(cam_hi, new_cam))
        if abs(new_cam - setup.front_camber_deg) > 0.05:
            label = f"camber_{new_cam:.1f}".replace("-", "neg").replace(".", "p")
            variations.append((label, {"front_camber_deg": new_cam}))

    # Write .sto files
    print(f"\n  Generating {len(variations)} calibration sweep setups:")
    print(f"  Output directory: {out_dir}\n")

    try:
        from output.setup_writer import write_sto_from_setup
    except ImportError:
        write_sto_from_setup = None

    for name, overrides in variations:
        sto_path = out_dir / f"cal_{name}.sto"
        # Build the modified setup description
        changes = []
        for field, val in overrides.items():
            base_val = getattr(setup, field, "?")
            changes.append(f"{field}: {base_val} -> {val}")

        if write_sto_from_setup is not None:
            try:
                write_sto_from_setup(setup, sto_path, overrides=overrides, car=car)
                print(f"    {sto_path.name:30s} {'(baseline)' if not overrides else ', '.join(changes)}")
            except Exception as e:
                print(f"    {sto_path.name:30s} [SKIP] {e}")
        else:
            # Fallback: just print the instructions
            print(f"    {name:30s} {'(baseline)' if not overrides else ', '.join(changes)}")

    print(f"\n  -- Calibration Protocol --")
    print(f"  1. Copy .sto files to: ~/Documents/iRacing/setups/{car_name}/")
    print(f"  2. In iRacing, go to the track and open the garage")
    print(f"  3. For each .sto file:")
    print(f"     a. Load the setup")
    print(f"     b. Drive 1 outlap (exit pit, complete 1 lap, pit in)")
    print(f"     c. This creates an IBT file with complete garage ground truth")
    print(f"  4. After all sessions, run:")
    print(f"     python -m car_model.auto_calibrate --car {car_name} --ibt-dir <your_telemetry_dir>")
    print(f"  5. Verify accuracy:")
    print(f"     python -m car_model.auto_calibrate --car {car_name} --verify")


def _run_verify(car_name: str) -> None:
    """Verify garage model accuracy against all calibration points.

    For each CalibrationPoint, runs the GarageOutputModel forward prediction
    and compares against the IBT ground truth. Reports per-field RMSE and
    max error so the user can see exactly where accuracy is good/bad.
    """
    from car_model.cars import get_car
    from car_model.garage import GarageSetupState

    car = get_car(car_name)
    points = load_calibration_points(car_name)
    if not points:
        print(f"  No calibration data for {car_name}. Run calibration first.")
        return

    garage_model = car.active_garage_output_model(None)
    if garage_model is None:
        print(f"  No garage model for {car_name}. Run calibration first.")
        return

    # Decode indices for indexed cars
    _hsm = car.heave_spring
    _csm = car.corner_spring

    fields = [
        ("front_static_rh_mm", "static_front_rh_mm"),
        ("rear_static_rh_mm", "static_rear_rh_mm"),
        ("heave_spring_defl_static_mm", "heave_spring_defl_static_mm"),
        ("rear_spring_defl_static_mm", "rear_spring_defl_static_mm"),
        ("third_spring_defl_static_mm", "third_spring_defl_static_mm"),
        ("front_shock_defl_static_mm", "front_shock_defl_static_mm"),
        ("rear_shock_defl_static_mm", "rear_shock_defl_static_mm"),
    ]

    import numpy as np
    errors: dict[str, list[float]] = {f[0]: [] for f in fields}
    n_valid = 0

    for pt in points:
        # Decode settings for indexed cars
        heave = pt.front_heave_setting
        third = pt.rear_third_setting
        rspring = pt.rear_spring_setting
        torsion_od = pt.front_torsion_od_mm

        if _hsm.front_setting_index_range and heave <= _hsm.front_setting_index_range[1] + 0.5:
            heave = _hsm.front_rate_from_setting(heave)
        if _hsm.rear_setting_index_range and third <= _hsm.rear_setting_index_range[1] + 0.5:
            third = _hsm.rear_rate_from_setting(third)
        if hasattr(_csm, 'rear_setting_index_range') and _csm.rear_setting_index_range and rspring <= _csm.rear_setting_index_range[1] + 0.5:
            rspring = _csm.rear_bar_rate_from_setting(rspring)
        if hasattr(_csm, 'front_setting_index_range') and _csm.front_setting_index_range and torsion_od <= _csm.front_setting_index_range[1] + 0.5:
            torsion_od = _csm.front_torsion_od_from_setting(torsion_od)

        state = GarageSetupState(
            front_pushrod_mm=pt.front_pushrod_mm,
            rear_pushrod_mm=pt.rear_pushrod_mm,
            front_heave_nmm=heave,
            front_heave_perch_mm=pt.front_heave_perch_mm,
            rear_third_nmm=third,
            rear_third_perch_mm=pt.rear_third_perch_mm,
            front_torsion_od_mm=torsion_od,
            rear_spring_nmm=rspring,
            rear_spring_perch_mm=pt.rear_spring_perch_mm,
            front_camber_deg=pt.front_camber_deg,
            rear_camber_deg=pt.rear_camber_deg,
            fuel_l=pt.fuel_l,
            wing_deg=pt.wing_deg,
            torsion_bar_turns=pt.torsion_bar_turns,
            rear_torsion_bar_turns=pt.rear_torsion_bar_turns,
        )

        predicted = garage_model.predict(state)
        n_valid += 1

        for pred_field, truth_field in fields:
            truth = getattr(pt, truth_field, 0.0)
            pred = getattr(predicted, pred_field, 0.0)
            if truth > 0:
                errors[pred_field].append(pred - truth)

    if n_valid == 0:
        print(f"  No valid points for verification.")
        return

    print(f"\n{'=' * 63}")
    print(f"  GARAGE MODEL VERIFICATION — {car_name.upper()} ({n_valid} points)")
    print(f"{'=' * 63}")
    print(f"  {'Output':<35s} {'RMSE':>7s} {'MaxErr':>7s} {'Bias':>7s}  Status")
    print(f"  {'-'*35} {'-'*7} {'-'*7} {'-'*7}  ------")

    all_ok = True
    for pred_field, _ in fields:
        errs = errors[pred_field]
        if not errs:
            print(f"  {pred_field:<35s} {'N/A':>7s}")
            continue
        arr = np.array(errs)
        rmse = float(np.sqrt(np.mean(arr ** 2)))
        max_err = float(np.max(np.abs(arr)))
        bias = float(np.mean(arr))
        is_rh = "rh" in pred_field
        threshold = 1.0 if is_rh else 2.0
        status = "OK" if max_err < threshold else "WARN"
        if status == "WARN":
            all_ok = False
        print(f"  {pred_field:<35s} {rmse:>6.2f}mm {max_err:>6.2f}mm {bias:>+6.2f}mm  {status}")

    print(f"{'=' * 63}")
    if all_ok:
        print(f"  All outputs within tolerance.")
    else:
        print(f"  Some outputs exceed tolerance — consider more calibration data.")
    print()


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
    # Pull car choices from the canonical registry so adding a new car here
    # doesn't drift from ``car_model/cars.py:_CARS``. Fall back to the legacy
    # static list if the import fails (e.g. partial install).
    try:
        from car_model.cars import _CARS as _ALL_CARS
        _car_choices = sorted(_ALL_CARS.keys())
    except Exception:
        _car_choices = [
            "bmw", "cadillac", "ferrari", "acura", "porsche",
            "bmw_m4_gt3", "aston_martin_vantage_gt3", "porsche_992_gt3r",
        ]
    parser.add_argument("--car", required=True,
                        choices=_car_choices,
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
    parser.add_argument("--generate-sweep", default=None, metavar="IBT",
                        help="Generate calibration sweep .sto files from a baseline IBT session")
    parser.add_argument("--verify", action="store_true",
                        help="Verify garage model accuracy against all IBT calibration points")
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

    if args.generate_sweep:
        _run_generate_sweep(car, args.generate_sweep)
        return

    if args.verify:
        _run_verify(car)
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
        _merge_car_wide_fields(car, models, existing_saved, verbose=True)

        save_calibrated_models(car, models)
        print(f"  [OK] Models saved to {_models_path(car)}")

        # ── Per-track models ──
        # Partition points by track and fit separate models per track.
        # This prevents cross-track contamination (e.g. Laguna Seca sessions
        # corrupting Algarve ride height models).
        from car_model.registry import track_key as _track_key
        track_groups: dict[str, list[CalibrationPoint]] = {}
        for pt in new_points:
            tk = _track_key(pt.track) if pt.track else ""
            if tk:
                track_groups.setdefault(tk, []).append(pt)

        if len(track_groups) > 1:
            print(f"\n  Fitting per-track models ({len(track_groups)} tracks)...")
            for tk, track_pts in sorted(track_groups.items()):
                tk_unique = set()
                for pt in track_pts:
                    tk_unique.add(_setup_key(pt))
                if len(tk_unique) < _MIN_SESSIONS_FOR_FIT:
                    print(f"    {tk}: {len(tk_unique)} unique setups (need {_MIN_SESSIONS_FOR_FIT}) — skipped")
                    continue
                tk_models = fit_models_from_points(car, track_pts)
                tk_models.track = tk
                # Merge car-wide fields (zeta, lookup tables, status) from the
                # freshly-saved pooled model so per-track files are self-contained.
                _merge_car_wide_fields(car, tk_models, models, verbose=False)
                save_calibrated_models(car, tk_models, track=tk)
                _best_r2 = max(
                    (m.r_squared for m in [tk_models.front_ride_height, tk_models.rear_ride_height] if m is not None),
                    default=0.0,
                )
                print(f"    {tk}: {len(tk_unique)} setups, best R²={_best_r2:.3f} -> {_models_path_for_track(car, tk)}")
        elif len(track_groups) == 1:
            # Single track — pooled model IS the per-track model, save alias
            tk = next(iter(track_groups))
            models.track = tk
            save_calibrated_models(car, models, track=tk)
            print(f"  [OK] Per-track model saved to {_models_path_for_track(car, tk)}")
    else:
        remaining = _MIN_SESSIONS_FOR_FIT - n_unique
        print(f"\n  ⏳ Need {remaining} more unique-setup sessions before fitting (have {n_unique}/{_MIN_SESSIONS_FOR_FIT})")
        print(f"     Tip: Run sessions with different heave springs, torsion bars, or pushrods")

    print_status(car)


if __name__ == "__main__":
    main()
