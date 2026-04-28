"""D-optimal calibration recommender CLI.

Given existing ``CalibrationPoint`` data for a car/track, recommend the next
setup that maximises information gain for the regression design matrix.

The criterion is **D-optimality**: pick ``x_new`` that maximises
``log det(X^T X + x_new x_new^T)`` (with Tikhonov regularisation for sparse
data). Each candidate is sampled via Latin hypercube sampling over the legal
setup space defined by ``car.setup_registry`` and per-car physics models.

Usage::

    python -m car_model.calibration_recommender --car cadillac --track laguna_seca
    python -m car_model.calibration_recommender --car ferrari --track hockenheim --n-recommendations 3

Outputs (per recommendation): the LHS-sampled setup snapped to legal
quantisation, expected information gain in nats, and a short plain-English
description. Ferrari/Acura indexed parameters are reported as integer indices,
not decoded N/mm. Cars without existing calibration_points get a "baseline-
extreme" sweep (one setup per axis extreme) to bootstrap the design matrix.

This module is fully standalone — it does not modify any solver or pipeline
code path. It is purely advisory.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

import numpy as np

from car_model.auto_calibrate import CalibrationPoint, load_calibration_points
from car_model.cars import CarModel, get_car
from car_model.registry import resolve_car, track_key
from car_model.setup_registry import CAR_FIELD_SPECS, get_car_spec


# ─────────────────────────────────────────────────────────────────────────────
# Axis definitions
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SetupAxis:
    """One quantitatively variable setup axis used as a candidate dimension."""
    name: str                 # CalibrationPoint attribute / display name
    display_name: str         # Human-readable label
    unit: str                 # "N/mm", "mm", "deg", "index", "blade"
    lo: float                 # legal range lower bound
    hi: float                 # legal range upper bound
    resolution: float         # quantisation step (1.0 for indices)
    is_index: bool = False    # True for Ferrari indexed fields (display as int)
    is_blade: bool = False    # True for ARB blade (display as "n / max")
    blade_max: int = 0        # max blade count when is_blade=True


def _safe_resolution(resolution: float | None, default: float) -> float:
    if resolution is None or resolution <= 0:
        return default
    return resolution


def _quantise(value: float, axis: SetupAxis) -> float:
    """Snap a continuous candidate to the legal quantisation grid."""
    span = axis.hi - axis.lo
    if span <= 0:
        return axis.lo
    step = axis.resolution if axis.resolution > 0 else span / 100.0
    n_steps = round((value - axis.lo) / step)
    snapped = axis.lo + n_steps * step
    snapped = max(axis.lo, min(axis.hi, snapped))
    if axis.is_index or axis.is_blade:
        snapped = float(round(snapped))
    return snapped


# ─────────────────────────────────────────────────────────────────────────────
# Per-car axis enumeration — first-class, no BMW fallbacks
# ─────────────────────────────────────────────────────────────────────────────

def _add_axis_from_spec(
    axes: list[SetupAxis],
    car_canonical: str,
    canonical_key: str,
    cp_attr: str,
    *,
    default_lo: float,
    default_hi: float,
    default_res: float,
    unit: str,
    is_index: bool = False,
    display_name: str | None = None,
) -> None:
    """Append an axis using setup_registry range when available."""
    spec = get_car_spec(car_canonical, canonical_key)
    lo = default_lo
    hi = default_hi
    res = default_res
    if spec is not None:
        if spec.range_min is not None:
            lo = float(spec.range_min)
        if spec.range_max is not None:
            hi = float(spec.range_max)
        res = _safe_resolution(spec.resolution, default_res)
    if hi <= lo:
        return  # axis not present / not adjustable
    axes.append(SetupAxis(
        name=cp_attr,
        display_name=display_name or cp_attr,
        unit=unit,
        lo=lo,
        hi=hi,
        resolution=res,
        is_index=is_index,
    ))


def enumerate_axes(car: CarModel, car_canonical: str) -> list[SetupAxis]:
    """Build the candidate-search axis list for a specific car.

    Each axis is sourced first from ``car.setup_registry``, with a fallback to
    the car's physics models (``corner_spring``, ``heave_spring``,
    ``garage_ranges``, ``arb``). No values are taken from another car; if a
    parameter doesn't apply to this car (e.g. heave on a hypothetical GT3
    car), the axis is omitted.
    """
    axes: list[SetupAxis] = []

    # Architecture sniffing — Ferrari uses indices; Acura/Ferrari use rear
    # torsion bar instead of coil. We probe car_canonical for first-class
    # treatment rather than the model object's attributes.
    is_ferrari = car_canonical == "ferrari"
    is_acura = car_canonical == "acura"
    is_porsche = car_canonical == "porsche"

    # Optional GT3 architecture support (forward-compat — only fires if the
    # codebase has been extended with `suspension_arch.has_heave_third`).
    has_heave_third = True
    arch = getattr(car, "suspension_arch", None)
    if arch is not None:
        has_heave_third = bool(getattr(arch, "has_heave_third", True))

    # ── Step 2: heave / third ──
    if has_heave_third:
        _add_axis_from_spec(
            axes, car_canonical, "front_heave_spring_nmm", "front_heave_setting",
            default_lo=20.0, default_hi=200.0, default_res=10.0,
            unit="" if is_ferrari else "N/mm",
            is_index=is_ferrari,
            display_name="front_heave_index" if is_ferrari else "front_heave_spring_nmm",
        )
        _add_axis_from_spec(
            axes, car_canonical, "rear_third_spring_nmm", "rear_third_setting",
            default_lo=20.0, default_hi=200.0, default_res=10.0,
            unit="" if is_ferrari else "N/mm",
            is_index=is_ferrari,
            display_name="rear_third_index" if is_ferrari else "rear_third_spring_nmm",
        )
        _add_axis_from_spec(
            axes, car_canonical, "front_heave_perch_mm", "front_heave_perch_mm",
            default_lo=-30.0, default_hi=30.0, default_res=0.5, unit="mm",
        )
        _add_axis_from_spec(
            axes, car_canonical, "rear_third_perch_mm", "rear_third_perch_mm",
            default_lo=20.0, default_hi=55.0, default_res=1.0, unit="mm",
        )

    # ── Step 3: corner springs ──
    # Front torsion bar (BMW/Cadillac/Ferrari/Acura — Porsche uses corner coils)
    cs = getattr(car, "corner_spring", None)
    if not is_porsche and cs is not None:
        # Use car's own corner_spring physics for OD range when registry doesn't cover it
        od_lo, od_hi = cs.front_torsion_od_range_mm
        od_step = cs.front_torsion_od_step_mm
        spec = get_car_spec(car_canonical, "front_torsion_od_mm")
        if spec is not None:
            if spec.range_min is not None:
                od_lo = float(spec.range_min)
            if spec.range_max is not None:
                od_hi = float(spec.range_max)
            od_step = _safe_resolution(spec.resolution, od_step)
        if od_hi > od_lo:
            axes.append(SetupAxis(
                name="front_torsion_od_mm",
                display_name="front_torsion_bar_index" if is_ferrari else "front_torsion_od_mm",
                unit="" if is_ferrari else "mm",
                lo=od_lo, hi=od_hi, resolution=od_step,
                is_index=is_ferrari,
            ))

    # Rear corner spring — coil (BMW/Cadillac/Porsche) or torsion (Ferrari/Acura)
    rear_torsion_c = getattr(cs, "rear_torsion_c", None) if cs is not None else None
    if rear_torsion_c is not None and cs is not None:
        # Rear torsion bar — Acura uses OD mm (range from setup_registry/cars), Ferrari uses index
        if is_acura:
            spec = get_car_spec(car_canonical, "rear_torsion_od_mm")
            lo = float(spec.range_min) if (spec and spec.range_min is not None) else cs.rear_torsion_od_range_mm[0]
            hi = float(spec.range_max) if (spec and spec.range_max is not None) else cs.rear_torsion_od_range_mm[1]
            res = _safe_resolution(spec.resolution if spec else None, cs.rear_torsion_od_step_mm)
            if hi > lo:
                axes.append(SetupAxis("rear_spring_setting", "rear_torsion_od_mm", "mm",
                                      lo, hi, res))
        elif is_ferrari:
            _add_axis_from_spec(
                axes, car_canonical, "rear_spring_rate_nmm", "rear_spring_setting",
                default_lo=0.0, default_hi=18.0, default_res=1.0, unit="",
                is_index=True, display_name="rear_torsion_bar_index",
            )
    else:
        # Rear coil spring
        # Porsche's setup_registry key is "rear_spring_nmm"; BMW/Cadillac use "rear_spring_rate_nmm".
        spec = get_car_spec(car_canonical, "rear_spring_rate_nmm")
        if spec is None:
            spec = get_car_spec(car_canonical, "rear_spring_nmm")
        lo = 100.0
        hi = 300.0
        res = 10.0
        if cs is not None:
            lo, hi = cs.rear_spring_range_nmm
            res = cs.rear_spring_step_nmm
        if spec is not None:
            if spec.range_min is not None:
                lo = float(spec.range_min)
            if spec.range_max is not None:
                hi = float(spec.range_max)
            res = _safe_resolution(spec.resolution, res)
        if hi > lo:
            axes.append(SetupAxis(
                name="rear_spring_setting",
                display_name="rear_spring_rate_nmm",
                unit="N/mm", lo=lo, hi=hi, resolution=res,
            ))

    _add_axis_from_spec(
        axes, car_canonical, "rear_spring_perch_mm", "rear_spring_perch_mm",
        default_lo=20.0, default_hi=55.0, default_res=0.5, unit="mm",
    )

    # ── Step 1: pushrods (rake) ──
    _add_axis_from_spec(
        axes, car_canonical, "front_pushrod_offset_mm", "front_pushrod_mm",
        default_lo=-40.0, default_hi=40.0, default_res=0.5, unit="mm",
    )
    _add_axis_from_spec(
        axes, car_canonical, "rear_pushrod_offset_mm", "rear_pushrod_mm",
        default_lo=-40.0, default_hi=40.0, default_res=0.5, unit="mm",
    )

    # ── Step 4: ARB blades (integer; size axis stays at driver's choice) ──
    arb = getattr(car, "arb", None)
    if arb is not None:
        front_max = int(getattr(arb, "front_blade_count", 5))
        rear_max = int(getattr(arb, "rear_blade_count", 5))
        if front_max >= 1:
            axes.append(SetupAxis("front_arb_blade", "front_arb_blade", "blade",
                                  1.0, float(front_max), 1.0,
                                  is_blade=True, blade_max=front_max))
        if rear_max >= 1:
            axes.append(SetupAxis("rear_arb_blade", "rear_arb_blade", "blade",
                                  1.0, float(rear_max), 1.0,
                                  is_blade=True, blade_max=rear_max))

    # ── Step 5: camber (small effect on ride height; included for completeness) ──
    _add_axis_from_spec(
        axes, car_canonical, "front_camber_deg", "front_camber_deg",
        default_lo=-2.9, default_hi=0.0, default_res=0.1, unit="deg",
    )
    _add_axis_from_spec(
        axes, car_canonical, "rear_camber_deg", "rear_camber_deg",
        default_lo=-1.9, default_hi=0.0, default_res=0.1, unit="deg",
    )

    return axes


# ─────────────────────────────────────────────────────────────────────────────
# Feature derivation — mirrors auto_calibrate _UNIVERSAL_POOL physics
# ─────────────────────────────────────────────────────────────────────────────

def _inv(x: float) -> float:
    return 1.0 / max(x, 1.0)


def _features_from_setup(values: dict[str, float]) -> dict[str, float]:
    """Build the regression feature row for a CalibrationPoint-like dict.

    Mirrors the universal pool used in ``auto_calibrate.fit_models_from_points``
    so that information-gain scoring matches the regression that will actually
    be fit. Returns a name→value dict so callers can project to a subset of
    feature names without index bookkeeping. Constant-axis features (zero
    variance across the dataset) are pruned later by the matrix builder.
    """
    def g(k: str) -> float:
        v = values.get(k, 0.0)
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    heave = g("front_heave_setting")
    third = g("rear_third_setting")
    spring = g("rear_spring_setting")
    od = g("front_torsion_od_mm")
    push_f = g("front_pushrod_mm")
    push_r = g("rear_pushrod_mm")
    fuel = g("fuel_l")

    return {
        "front_pushrod": push_f,
        "rear_pushrod": push_r,
        "front_heave": heave,
        "rear_third": third,
        "rear_spring": spring,
        "torsion_od": od,
        "front_heave_perch": g("front_heave_perch_mm"),
        "rear_third_perch": g("rear_third_perch_mm"),
        "rear_spring_perch": g("rear_spring_perch_mm"),
        "front_camber": g("front_camber_deg"),
        "fuel": fuel,
        "inv_front_heave": _inv(heave),
        "inv_rear_third": _inv(third),
        "inv_rear_spring": _inv(spring),
        "inv_od4": _inv(od ** 4),
        "rear_camber": g("rear_camber_deg"),
        "wing": g("wing_deg"),
        "front_pushrod_sq": push_f * push_f,
        "rear_pushrod_sq": push_r * push_r,
        "fuel_x_inv_spring": fuel * _inv(spring),
        "fuel_x_inv_third": fuel * _inv(third),
    }


_FEATURE_NAMES: list[str] = list(_features_from_setup({}).keys())


def _cp_to_dict(pt: CalibrationPoint) -> dict[str, float]:
    return {
        "front_heave_setting": pt.front_heave_setting,
        "rear_third_setting": pt.rear_third_setting,
        "rear_spring_setting": pt.rear_spring_setting,
        "front_torsion_od_mm": pt.front_torsion_od_mm,
        "front_pushrod_mm": pt.front_pushrod_mm,
        "rear_pushrod_mm": pt.rear_pushrod_mm,
        "front_heave_perch_mm": pt.front_heave_perch_mm,
        "rear_third_perch_mm": pt.rear_third_perch_mm,
        "rear_spring_perch_mm": pt.rear_spring_perch_mm,
        "front_camber_deg": pt.front_camber_deg,
        "rear_camber_deg": pt.rear_camber_deg,
        "fuel_l": pt.fuel_l,
        "wing_deg": pt.wing_deg,
        "front_arb_blade": float(pt.front_arb_blade or 0),
        "rear_arb_blade": float(pt.rear_arb_blade or 0),
    }


def _build_design_matrix(
    points: list[CalibrationPoint],
) -> tuple[np.ndarray, list[str]]:
    """Build the design matrix and prune zero-variance columns.

    Returns (X, kept_feature_names). Zero-variance columns are dropped because
    they contribute nothing to ``det(X^T X)`` and would force regularisation
    to dominate the ranking.
    """
    if not points:
        return np.zeros((0, len(_FEATURE_NAMES))), list(_FEATURE_NAMES)
    rows = [
        [feats[name] for name in _FEATURE_NAMES]
        for feats in (_features_from_setup(_cp_to_dict(p)) for p in points)
    ]
    X = np.asarray(rows, dtype=float)
    keep_idx = [i for i in range(X.shape[1]) if np.std(X[:, i]) > 1e-9]
    if not keep_idx:
        # Degenerate — keep all so caller can still compute determinant
        return X, list(_FEATURE_NAMES)
    return X[:, keep_idx], [_FEATURE_NAMES[i] for i in keep_idx]


# ─────────────────────────────────────────────────────────────────────────────
# Track filtering
# ─────────────────────────────────────────────────────────────────────────────

def _filter_points_by_track(points: list[CalibrationPoint], track: str) -> list[CalibrationPoint]:
    """Filter calibration points to those matching the requested track."""
    target = track_key(track)
    if not target:
        return list(points)
    out = []
    for p in points:
        if not p.track:
            continue
        if track_key(p.track) == target:
            out.append(p)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Candidate generation + D-optimality scoring
# ─────────────────────────────────────────────────────────────────────────────

def _sample_candidates(
    axes: list[SetupAxis],
    n: int,
    seed: int = 0,
) -> list[dict[str, float]]:
    """Latin-hypercube sample ``n`` candidate setups within the legal axes."""
    if not axes:
        return []
    try:
        from scipy.stats import qmc
        engine = qmc.LatinHypercube(d=len(axes), seed=seed)
        unit = engine.random(n=n)
    except Exception:
        rng = np.random.default_rng(seed)
        unit = rng.random((n, len(axes)))

    candidates: list[dict[str, float]] = []
    for row in unit:
        cand: dict[str, float] = {}
        for axis, u in zip(axes, row):
            raw = axis.lo + float(u) * (axis.hi - axis.lo)
            cand[axis.name] = _quantise(raw, axis)
        candidates.append(cand)
    return candidates


def _candidate_to_features(
    cand: dict[str, float],
    template: dict[str, float],
    kept_names: list[str],
) -> np.ndarray:
    merged = dict(template)
    merged.update(cand)
    feats = _features_from_setup(merged)
    return np.asarray([feats[n] for n in kept_names], dtype=float)


def _info_gain(
    XtX_reg: np.ndarray,
    log_det_current: float,
    x_new: np.ndarray,
) -> float:
    """log det(M + x x^T) − log det(M).

    For a rank-1 update this equals ``log(1 + x^T M^{-1} x)``. We use slogdet
    directly for numerical robustness against ill-conditioned matrices.
    """
    M_new = XtX_reg + np.outer(x_new, x_new)
    sign, logdet = np.linalg.slogdet(M_new)
    if sign <= 0:
        return -np.inf
    return float(logdet - log_det_current)


def rank_candidates(
    points: list[CalibrationPoint],
    axes: list[SetupAxis],
    n_samples: int = 200,
    n_recommendations: int = 5,
    seed: int = 0,
    ridge: float = 1e-3,
) -> tuple[list[tuple[float, dict[str, float]]], dict]:
    """Return top-N candidates ranked by D-optimal information gain."""
    X, kept_names = _build_design_matrix(points)
    n_features = X.shape[1] if X.size else 0

    # Build M = X^T X + ridge * I
    if n_features == 0:
        M = np.array([[ridge]])
        log_det_current = float(np.log(ridge))
    else:
        M = X.T @ X + ridge * np.eye(n_features)
        sign, logdet = np.linalg.slogdet(M)
        log_det_current = float(logdet) if sign > 0 else float(np.log(ridge) * n_features)

    # Representative template: mean of existing setups (so unsearched axes
    # in the candidate dict take typical values when computing features).
    # Falls back to per-axis midpoints when no calibration data exists.
    template: dict[str, float] = {}
    if points:
        point_dicts = [_cp_to_dict(p) for p in points]
        for k in point_dicts[0].keys():
            template[k] = float(np.mean([d[k] for d in point_dicts]))
    template.setdefault("fuel_l", 50.0)
    template.setdefault("wing_deg", 17.0)
    if template.get("fuel_l", 0.0) == 0.0:
        template["fuel_l"] = 50.0
    if template.get("wing_deg", 0.0) == 0.0:
        template["wing_deg"] = 17.0
    for axis in axes:
        template.setdefault(axis.name, 0.5 * (axis.lo + axis.hi))

    candidates = _sample_candidates(axes, n_samples, seed=seed)
    fallback_x = np.array([1.0])
    scored: list[tuple[float, dict[str, float]]] = []
    for cand in candidates:
        x_new = _candidate_to_features(cand, template, kept_names) if n_features > 0 else fallback_x
        gain = _info_gain(M, log_det_current, x_new)
        if not np.isfinite(gain):
            continue
        scored.append((gain, cand))
    scored.sort(key=lambda t: t[0], reverse=True)

    diag = {
        "n_existing_points": len(points),
        "n_features_kept": n_features,
        "n_candidates_sampled": len(candidates),
        "n_candidates_scored": len(scored),
        "log_det_current": log_det_current,
        "kept_feature_names": kept_names,
    }
    return scored[:n_recommendations], diag


# ─────────────────────────────────────────────────────────────────────────────
# Baseline-extreme bootstrap (zero-data case)
# ─────────────────────────────────────────────────────────────────────────────

def baseline_extremes(axes: list[SetupAxis], n: int = 5) -> list[dict[str, float]]:
    """Return a small set of corner setups spanning each axis to bootstrap.

    With no calibration data, D-optimality has no reference; return one setup
    per axis pinned to its low/high extreme (alternating). The first candidate
    is the all-midpoint baseline.
    """
    out: list[dict[str, float]] = []
    if not axes:
        return out
    out.append({a.name: _quantise(0.5 * (a.lo + a.hi), a) for a in axes})
    for i, axis in enumerate(axes):
        if len(out) >= n:
            break
        cand = {a.name: _quantise(0.5 * (a.lo + a.hi), a) for a in axes}
        cand[axis.name] = axis.hi if i % 2 == 0 else axis.lo
        cand[axis.name] = _quantise(cand[axis.name], axis)
        out.append(cand)
    return out[:n]


# ─────────────────────────────────────────────────────────────────────────────
# Output formatting
# ─────────────────────────────────────────────────────────────────────────────

def _format_value(axis: SetupAxis, value: float) -> str:
    if axis.is_blade:
        return f"{int(round(value))} / {axis.blade_max}"
    if axis.is_index:
        return f"index {int(round(value))}"
    if axis.unit == "deg":
        return f"{value:+.1f} {axis.unit}"
    if axis.resolution >= 1.0:
        return f"{int(round(value))} {axis.unit}".rstrip()
    return f"{value:.2f} {axis.unit}".rstrip()


def format_recommendations(
    car: CarModel,
    car_canonical: str,
    track_display: str,
    axes: list[SetupAxis],
    scored: list[tuple[float, dict[str, float]]],
    diag: dict,
    bootstrap: list[dict[str, float]] | None = None,
) -> str:
    name = getattr(car, "display_name", None) or getattr(car, "name", car_canonical)
    lines: list[str] = []
    lines.append(f"CALIBRATION RECOMMENDER for {name} at {track_display}")
    lines.append(f"Existing calibration_points: {diag['n_existing_points']}")
    if diag["n_existing_points"] == 0:
        lines.append("Current model R²: not fitted (no data — emitting baseline-extreme bootstrap)")
    elif diag["n_features_kept"] == 0:
        lines.append("Current model R²: not fitted (insufficient setup variance)")
    else:
        lines.append(
            f"Current model: {diag['n_features_kept']} features kept, "
            f"log det(X^T X + ridge*I) = {diag['log_det_current']:+.2f}"
        )
    lines.append("")

    if bootstrap is not None:
        lines.append("BASELINE-EXTREME BOOTSTRAP (no calibration data yet):")
        lines.append("Run these in any order — each one explores a distinct axis extreme.")
        lines.append("")
        for i, cand in enumerate(bootstrap, start=1):
            lines.append(f"#{i}  Bootstrap setup")
            for axis in axes:
                if axis.name in cand:
                    lines.append(f"      {axis.display_name} = {_format_value(axis, cand[axis.name])}")
            lines.append("")
        return "\n".join(lines)

    lines.append("RECOMMENDED NEXT SETUPS (ranked by information gain):")
    lines.append("")
    if not scored:
        lines.append("(No candidates passed the determinant check — try increasing --n-samples.)")
        return "\n".join(lines)
    for rank, (gain, cand) in enumerate(scored, start=1):
        lines.append(f"#{rank}  Information gain: {gain:+.2f} nats")
        for axis in axes:
            if axis.name in cand:
                lines.append(f"      {axis.display_name} = {_format_value(axis, cand[axis.name])}")
        lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def recommend(
    car_canonical: str,
    track: str,
    n_recommendations: int = 5,
    n_samples: int = 200,
    seed: int = 0,
) -> str:
    """High-level convenience: return the formatted recommendation report."""
    car = get_car(car_canonical, apply_calibration=False)
    axes = enumerate_axes(car, car_canonical)
    if not axes:
        return (
            f"No tunable axes found for car '{car_canonical}'. "
            "This usually means car.setup_registry has no entries for this car."
        )

    points = load_calibration_points(car_canonical)
    track_pts = _filter_points_by_track(points, track)

    if not track_pts:
        bootstrap = baseline_extremes(axes, n=max(n_recommendations, 5))
        return format_recommendations(
            car, car_canonical, track, axes, scored=[],
            diag={"n_existing_points": 0, "n_features_kept": 0,
                  "n_candidates_sampled": 0, "n_candidates_scored": 0,
                  "log_det_current": float("nan"), "kept_feature_names": []},
            bootstrap=bootstrap,
        )

    scored, diag = rank_candidates(
        track_pts, axes,
        n_samples=n_samples,
        n_recommendations=n_recommendations,
        seed=seed,
    )
    return format_recommendations(car, car_canonical, track, axes, scored, diag)


def main(argv: list[str] | None = None) -> int:
    available = sorted(CAR_FIELD_SPECS.keys())
    parser = argparse.ArgumentParser(
        prog="python -m car_model.calibration_recommender",
        description="Recommend the next-best setup to maximise calibration information gain.",
    )
    parser.add_argument("--car", required=True,
                        help=f"Car canonical name. Supported: {', '.join(available)}")
    parser.add_argument("--track", required=True,
                        help="Track name (e.g. 'sebring', 'laguna_seca', 'hockenheim').")
    parser.add_argument("--n-recommendations", type=int, default=5,
                        help="Number of top candidates to return (default 5).")
    parser.add_argument("--n-samples", type=int, default=200,
                        help="Latin-hypercube sample size (default 200).")
    parser.add_argument("--seed", type=int, default=0,
                        help="Sampling seed for reproducibility (default 0).")
    args = parser.parse_args(argv)

    # Resolve the car. We deliberately reject substring-only matches so
    # "bmw_m4_gt3" never silently aliases to the GTP BMW M Hybrid V8 —
    # protecting against the BMW-leakage that this codebase has fought.
    raw = args.car.strip()
    raw_lower = raw.lower()
    car_canonical: str | None = None
    if raw_lower in CAR_FIELD_SPECS:
        car_canonical = raw_lower
    else:
        identity = resolve_car(raw)
        if identity is not None:
            # Accept only when the input is unambiguously the resolved car
            # (canonical, display_name, screen_name, sto_id, or aero_folder).
            exact_keys = {
                identity.canonical.lower(),
                identity.display_name.lower(),
                identity.screen_name.lower(),
                identity.sto_id.lower(),
                identity.aero_folder.lower(),
            }
            if raw_lower in exact_keys:
                car_canonical = identity.canonical

    if car_canonical is None:
        print(f"Error: unknown car '{raw}'. Supported: {', '.join(available)}",
              file=sys.stderr)
        return 2
    if car_canonical not in CAR_FIELD_SPECS:
        print(f"Error: car '{car_canonical}' has no setup_registry entries yet. "
              f"Supported: {', '.join(available)}", file=sys.stderr)
        return 2

    try:
        report = recommend(
            car_canonical, args.track,
            n_recommendations=args.n_recommendations,
            n_samples=args.n_samples,
            seed=args.seed,
        )
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(report)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
