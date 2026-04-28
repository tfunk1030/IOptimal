"""Per-corner phase causal regression evaluation.

Unit P1: predict per-corner-phase impact of a candidate setup using fitted
regression models that live on ``CarCalibrationModels.corner_phase_models``
(populated by sibling unit D3 once the data-side work lands).

This module is the *consumer* side. If D3 has not landed yet, the dict is
absent or empty and every public function degrades gracefully (returns
empty results / no-op behaviour) so the pipeline never crashes.

Public surface
--------------
- ``predict_corner_phase_impact(car_models, parameter_changes, baseline_setup)``
    Map of ``"corner<id>__<phase>__<metric>"`` -> predicted delta in metric.
- ``find_pareto_dominant(candidates, baseline_impacts)``
    Subset of candidates whose predicted impacts do not make any corner-phase
    worse than the baseline.
- ``pareto_summary(impacts, *, worse_threshold)``
    Compact text summary: counts of improved / worsened / unchanged
    corner-phase metrics.
- ``parse_cpm_key(key)`` and ``format_corner_impact_lines(corners, impacts)``
    Helpers used by the engineering report.

Key conventions
---------------
- Model keys follow ``f"corner{corner_id}__{phase}__{metric}"`` where:
  - ``phase`` ∈ {``entry``, ``mid``, ``exit``} (any string is accepted)
  - ``metric`` is the scalar telemetry signal name (e.g. ``understeer_deg``)
- "Better" direction per metric is encoded in ``METRIC_BETTER_DIRECTION``;
  unknown metrics default to "lower-is-better" (consistent with most
  loss-style telemetry signals like understeer, body_slip, time_loss).
- Setup dicts use the same flat feature names as ``auto_calibrate``'s
  ``_UNIVERSAL_POOL`` (``front_pushrod``, ``front_heave``, ``rear_third``,
  ``rear_spring``, ``torsion_od``, ``front_camber``, ``fuel`` ...).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

# ----------------------------------------------------------------------
# Metric direction map: how to interpret a *positive* delta in the metric.
# Values are either "lower" (lower is better) or "higher" (higher is better).
# Unknown metrics default to "lower" — most causal-regression metrics are
# loss-style (understeer, body_slip, time_loss, shock_vel, ...).
# ----------------------------------------------------------------------
METRIC_BETTER_DIRECTION: dict[str, str] = {
    # Handling balance / loss metrics — lower is better
    "understeer": "lower",
    "understeer_deg": "lower",
    "body_slip": "lower",
    "body_slip_deg": "lower",
    "time_loss_s": "lower",
    "delta_to_min_time_s": "lower",
    "trail_brake_pct": "lower",  # context-dependent but usually a cost
    "front_shock_vel_p95_mps": "lower",
    "rear_shock_vel_p95_mps": "lower",
    "front_shock_vel_p99_mps": "lower",
    "rear_shock_vel_p99_mps": "lower",
    "entry_pitch_severity": "lower",
    "aero_collapse_severity": "lower",
    "exit_slip_severity": "lower",
    "kerb_severity_max": "lower",
    # Performance metrics — higher is better
    "lat_g": "higher",
    "peak_lat_g": "higher",
    "apex_speed_kph": "higher",
    "exit_speed_kph": "higher",
    "throttle_progressiveness": "higher",
    "stability_margin": "higher",
    "corner_confidence": "higher",
}

_CPM_KEY_RE = re.compile(r"^corner(?P<id>\d+)__(?P<phase>[^_]+(?:_[^_]+)*?)__(?P<metric>.+)$")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _is_finite(value: Any) -> bool:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    return not (math.isnan(f) or math.isinf(f))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


def _feature_value(setup: Mapping[str, Any], name: str) -> float:
    """Compute a feature value from a flat setup dict, including engineered
    interaction/inverse terms used by the universal feature pool.

    Setup dict keys are the *raw* parameter names (``front_heave``,
    ``rear_spring``, ``torsion_od`` ...). Engineered features are derived
    on demand here so callers don't need to know which interactions a
    particular fitted model picked.
    """
    if name in setup:
        return _safe_float(setup[name])

    # Inverse / compliance features: 1 / max(rate, 1.0)
    if name == "inv_front_heave":
        return 1.0 / max(_safe_float(setup.get("front_heave")), 1.0)
    if name == "inv_rear_third":
        return 1.0 / max(_safe_float(setup.get("rear_third")), 1.0)
    if name == "inv_rear_spring":
        return 1.0 / max(_safe_float(setup.get("rear_spring")), 1.0)
    if name == "inv_od4":
        od = _safe_float(setup.get("torsion_od"))
        return 1.0 / max(od ** 4, 1.0)

    # Pushrod²
    if name == "front_pushrod_sq":
        return _safe_float(setup.get("front_pushrod")) ** 2
    if name == "rear_pushrod_sq":
        return _safe_float(setup.get("rear_pushrod")) ** 2

    # Fuel × compliance
    if name == "fuel_x_inv_spring":
        fuel = _safe_float(setup.get("fuel"))
        return fuel / max(_safe_float(setup.get("rear_spring")), 1.0)
    if name == "fuel_x_inv_third":
        fuel = _safe_float(setup.get("fuel"))
        return fuel / max(_safe_float(setup.get("rear_third")), 1.0)

    # Unknown feature — treat as zero so the model contribution vanishes
    # rather than crashing. This mirrors how _pool_to_matrix in auto_calibrate
    # auto-excludes zero-variance features.
    return 0.0


def _evaluate_model(model: Any, setup: Mapping[str, Any]) -> float:
    """Evaluate a FittedModel-like object against a setup dict.

    Expects ``model.coefficients = [intercept, beta_1, ..., beta_k]`` and
    ``model.feature_names = [name_1, ..., name_k]``. Returns NaN-safe 0.0
    on shape mismatches.
    """
    coeffs = list(getattr(model, "coefficients", []) or [])
    names = list(getattr(model, "feature_names", []) or [])
    if not coeffs:
        return 0.0
    intercept = _safe_float(coeffs[0])
    betas = coeffs[1:]
    # Defensive: if names/betas mismatch in length, evaluate the smaller of the two.
    n = min(len(betas), len(names))
    total = intercept
    for i in range(n):
        total += _safe_float(betas[i]) * _feature_value(setup, names[i])
    return total


# ----------------------------------------------------------------------
# Setup dict construction from solver steps (used by produce.py)
# ----------------------------------------------------------------------

def setup_dict_from_steps(
    *,
    step1: Any,
    step2: Any,
    step3: Any,
    step5: Any,
    fuel_l: float = 0.0,
    wing_deg: float | None = None,
) -> dict[str, float]:
    """Build a flat feature dict matching the auto_calibrate _UNIVERSAL_POOL
    naming convention from the solver step solutions.

    Missing fields silently default to 0.0; engineered features
    (``inv_front_heave`` etc.) are derived on demand by ``_feature_value``.
    """
    setup: dict[str, float] = {}

    # Step 1 — pushrods, ride heights
    setup["front_pushrod"] = _safe_float(getattr(step1, "front_pushrod_offset_mm", 0.0))
    setup["rear_pushrod"] = _safe_float(getattr(step1, "rear_pushrod_offset_mm", 0.0))

    # Step 2 — heave / third + perches
    setup["front_heave"] = _safe_float(getattr(step2, "front_heave_nmm", 0.0))
    setup["rear_third"] = _safe_float(getattr(step2, "rear_third_nmm", 0.0))
    setup["front_heave_perch"] = _safe_float(getattr(step2, "perch_offset_front_mm", 0.0))
    setup["rear_third_perch"] = _safe_float(getattr(step2, "perch_offset_rear_mm", 0.0))

    # Step 3 — corner springs
    setup["torsion_od"] = _safe_float(getattr(step3, "front_torsion_od_mm", 0.0))
    setup["rear_spring"] = _safe_float(getattr(step3, "rear_spring_rate_nmm", 0.0))
    setup["rear_spring_perch"] = _safe_float(getattr(step3, "rear_spring_perch_mm", 0.0))
    setup["torsion_turns"] = _safe_float(getattr(step3, "front_torsion_bar_turns", 0.0))
    setup["rear_torsion_turns"] = _safe_float(getattr(step3, "rear_torsion_bar_turns", 0.0))

    # Step 5 — geometry
    setup["front_camber"] = _safe_float(getattr(step5, "front_camber_deg", 0.0))
    setup["rear_camber"] = _safe_float(getattr(step5, "rear_camber_deg", 0.0))

    # Globals
    setup["fuel"] = _safe_float(fuel_l)
    if wing_deg is not None:
        setup["wing"] = _safe_float(wing_deg)
    return setup


def setup_dict_from_current(current_setup: Any, *, fuel_l: float | None = None) -> dict[str, float]:
    """Build a flat feature dict from a CurrentSetup (analyzer/setup_reader)."""
    setup: dict[str, float] = {}
    if current_setup is None:
        return setup
    setup["front_pushrod"] = _safe_float(getattr(current_setup, "front_pushrod_mm", 0.0))
    setup["rear_pushrod"] = _safe_float(getattr(current_setup, "rear_pushrod_mm", 0.0))
    setup["front_heave"] = _safe_float(getattr(current_setup, "front_heave_nmm", 0.0))
    setup["rear_third"] = _safe_float(getattr(current_setup, "rear_third_nmm", 0.0))
    setup["front_heave_perch"] = _safe_float(getattr(current_setup, "front_heave_perch_mm", 0.0))
    setup["rear_third_perch"] = _safe_float(getattr(current_setup, "rear_third_perch_mm", 0.0))
    setup["torsion_od"] = _safe_float(getattr(current_setup, "front_torsion_od_mm", 0.0))
    setup["rear_spring"] = _safe_float(getattr(current_setup, "rear_spring_nmm", 0.0))
    setup["rear_spring_perch"] = _safe_float(getattr(current_setup, "rear_spring_perch_mm", 0.0))
    setup["torsion_turns"] = _safe_float(getattr(current_setup, "torsion_bar_turns", 0.0))
    setup["rear_torsion_turns"] = _safe_float(getattr(current_setup, "rear_torsion_bar_turns", 0.0))
    setup["front_camber"] = _safe_float(getattr(current_setup, "front_camber_deg", 0.0))
    setup["rear_camber"] = _safe_float(getattr(current_setup, "rear_camber_deg", 0.0))
    setup["fuel"] = _safe_float(
        fuel_l if fuel_l is not None else getattr(current_setup, "fuel_l", 0.0)
    )
    setup["wing"] = _safe_float(getattr(current_setup, "wing_angle_deg", 0.0))
    return setup


# ----------------------------------------------------------------------
# Core API: predict per-corner-phase impact
# ----------------------------------------------------------------------

def predict_corner_phase_impact(
    car_models: Any,
    parameter_changes: Mapping[str, float],
    baseline_setup: Mapping[str, Any],
) -> dict[str, float]:
    """For each (corner_id, phase, metric) triplet with a fitted, calibrated
    model, predict the delta in outcome from the parameter changes.

    Args:
        car_models: ``CarCalibrationModels`` (or any object with attribute
            ``corner_phase_models``). If the attribute is missing or empty
            (sibling unit D3 hasn't landed), an empty dict is returned.
        parameter_changes: New values for the changed setup keys. Keys
            should match the auto_calibrate raw feature names.
        baseline_setup: The full baseline setup dict. Keys not in
            ``parameter_changes`` are taken from here.

    Returns:
        ``{cpm_key: delta_value}`` where ``delta_value = new_pred - base_pred``.
        Models with ``is_calibrated=False`` or empty coefficients are skipped.
    """
    if car_models is None:
        return {}
    cpm = getattr(car_models, "corner_phase_models", None) or {}
    if not cpm:
        return {}

    new_setup: dict[str, Any] = dict(baseline_setup)
    for k, v in parameter_changes.items():
        new_setup[k] = v

    impacts: dict[str, float] = {}
    for key, model in cpm.items():
        if model is None:
            continue
        if not getattr(model, "is_calibrated", True):
            continue
        try:
            base_pred = _evaluate_model(model, baseline_setup)
            new_pred = _evaluate_model(model, new_setup)
        except Exception:
            # Be conservative: a single broken model must not break the report.
            continue
        delta = new_pred - base_pred
        if not _is_finite(delta):
            continue
        impacts[key] = delta
    return impacts


# ----------------------------------------------------------------------
# Pareto frontier helpers
# ----------------------------------------------------------------------

def parse_cpm_key(key: str) -> tuple[int, str, str] | None:
    """Parse a ``corner<id>__<phase>__<metric>`` key. Returns None on mismatch."""
    m = _CPM_KEY_RE.match(key)
    if not m:
        return None
    try:
        cid = int(m.group("id"))
    except ValueError:
        return None
    return cid, m.group("phase"), m.group("metric")


def _is_worsening(metric: str, delta: float, *, worse_threshold: float) -> bool:
    """Return True if ``delta`` makes ``metric`` worse beyond a small noise band."""
    if abs(delta) < worse_threshold:
        return False
    direction = METRIC_BETTER_DIRECTION.get(metric, "lower")
    if direction == "higher":
        return delta < 0  # decreased a higher-is-better metric -> worse
    # "lower" is better -> a positive delta is worse
    return delta > 0


def _is_improving(metric: str, delta: float, *, worse_threshold: float) -> bool:
    if abs(delta) < worse_threshold:
        return False
    direction = METRIC_BETTER_DIRECTION.get(metric, "lower")
    if direction == "higher":
        return delta > 0
    return delta < 0


def any_corner_worse(
    candidate_impacts: Mapping[str, float],
    baseline_impacts: Mapping[str, float] | None,
    *,
    worse_threshold: float = 1e-6,
) -> bool:
    """True if ``candidate_impacts`` worsens any corner-phase metric vs baseline.

    ``baseline_impacts`` is the impact vector of the *no-change* setup
    (typically all zeros — the baseline is the baseline). When None, the
    baseline is taken to be the zero vector. A candidate is worsening if
    its delta is in the "worse" direction beyond ``worse_threshold``.
    """
    base = dict(baseline_impacts or {})
    for key, delta in candidate_impacts.items():
        parsed = parse_cpm_key(key)
        if parsed is None:
            continue
        _, _phase, metric = parsed
        # Compare candidate delta to baseline delta on the same key.
        baseline_delta = _safe_float(base.get(key, 0.0))
        net = delta - baseline_delta
        if _is_worsening(metric, net, worse_threshold=worse_threshold):
            return True
    return False


def find_pareto_dominant(
    candidates: Iterable[Any],
    baseline_impacts: Mapping[str, float] | None = None,
    *,
    worse_threshold: float = 1e-6,
) -> list[Any]:
    """Among ``candidates``, return those whose ``corner_impacts`` don't make
    any corner-phase worse vs ``baseline_impacts``.

    A candidate without a ``corner_impacts`` attribute is skipped (treated
    as having no information — never Pareto-dominant by definition, since
    we can't prove it doesn't worsen anything).
    """
    dominant: list[Any] = []
    for cand in candidates:
        impacts = getattr(cand, "corner_impacts", None)
        if not impacts:
            continue
        if not any_corner_worse(impacts, baseline_impacts, worse_threshold=worse_threshold):
            dominant.append(cand)
    return dominant


# Backward-compat alias (private name used from candidate_search before public rename)
_any_corner_worse = any_corner_worse


@dataclass
class ParetoSummary:
    improved: int
    worsened: int
    unchanged: int
    improved_keys: list[str]
    worsened_keys: list[str]

    @property
    def total(self) -> int:
        return self.improved + self.worsened + self.unchanged


def pareto_summary(
    impacts: Mapping[str, float],
    *,
    worse_threshold: float = 1e-6,
) -> ParetoSummary:
    """Bucket impact deltas into improved / worsened / unchanged."""
    improved: list[str] = []
    worsened: list[str] = []
    unchanged_ct = 0
    for key, delta in impacts.items():
        parsed = parse_cpm_key(key)
        if parsed is None:
            unchanged_ct += 1
            continue
        _, _phase, metric = parsed
        if _is_improving(metric, delta, worse_threshold=worse_threshold):
            improved.append(key)
        elif _is_worsening(metric, delta, worse_threshold=worse_threshold):
            worsened.append(key)
        else:
            unchanged_ct += 1
    return ParetoSummary(
        improved=len(improved),
        worsened=len(worsened),
        unchanged=unchanged_ct,
        improved_keys=improved,
        worsened_keys=worsened,
    )


# ----------------------------------------------------------------------
# Report formatting helpers
# ----------------------------------------------------------------------

def _format_metric_label(metric: str, delta: float, *, worse_threshold: float = 1e-6) -> str:
    """Human-readable line fragment: ``understeer +0.12° (worsens)``."""
    direction = METRIC_BETTER_DIRECTION.get(metric, "lower")
    if abs(delta) < worse_threshold:
        verdict = "neutral"
    elif _is_improving(metric, delta, worse_threshold=worse_threshold):
        verdict = "improves"
    else:
        verdict = "worsens"
    sign = "+" if delta >= 0 else ""
    # Pick a unit suffix based on metric name suffix conventions
    if metric.endswith("_deg") or metric.endswith("understeer"):
        unit = "°"
    elif metric.endswith("_g") or metric in ("lat_g", "peak_lat_g"):
        unit = "g"
    elif metric.endswith("_mps"):
        unit = " m/s"
    elif metric.endswith("_kph"):
        unit = " kph"
    elif metric.endswith("_s"):
        unit = " s"
    else:
        unit = ""
    return f"{metric} {sign}{delta:.2f}{unit} ({verdict})"


def format_corner_impact_lines(
    corners: Iterable[Any],
    impacts: Mapping[str, float],
    *,
    worse_threshold: float = 1e-6,
) -> list[str]:
    """Build human-readable per-corner impact lines for the engineering report.

    The output is grouped by corner_id and within each corner by phase, with
    a ``Net:`` summary line classifying the corner as a clear improvement,
    marginal improvement, marginal regression, or clear regression.
    """
    if not impacts:
        return []

    # Index corners by id for quick metadata lookup
    corner_by_id: dict[int, Any] = {}
    for c in corners or ():
        cid = getattr(c, "corner_id", None)
        if cid is not None:
            try:
                corner_by_id[int(cid)] = c
            except (TypeError, ValueError):
                continue

    # Bucket impacts: corner_id -> phase -> [(metric, delta), ...]
    grouped: dict[int, dict[str, list[tuple[str, float]]]] = {}
    for key, delta in impacts.items():
        parsed = parse_cpm_key(key)
        if parsed is None:
            continue
        cid, phase, metric = parsed
        grouped.setdefault(cid, {}).setdefault(phase, []).append((metric, delta))

    lines: list[str] = []
    phase_order = ["entry", "mid", "apex", "exit"]

    for cid in sorted(grouped):
        meta = corner_by_id.get(cid)
        header = f"Corner {cid}"
        if meta is not None:
            speed_class = getattr(meta, "speed_class", "")
            direction = getattr(meta, "direction", "")
            apex = getattr(meta, "apex_speed_kph", None)
            extras: list[str] = []
            if speed_class and direction:
                extras.append(f"{speed_class}-speed {direction}")
            elif speed_class:
                extras.append(f"{speed_class}-speed")
            if apex is not None:
                try:
                    extras.append(f"{float(apex):.0f} kph apex")
                except (TypeError, ValueError):
                    pass
            if extras:
                header += " (" + ", ".join(extras) + ")"
        header += ":"
        lines.append(header)

        phases = grouped[cid]
        ordered_phases = [p for p in phase_order if p in phases] + [
            p for p in phases if p not in phase_order
        ]
        net_improve = 0
        net_worsen = 0
        for phase in ordered_phases:
            for metric, delta in phases[phase]:
                if _is_improving(metric, delta, worse_threshold=worse_threshold):
                    net_improve += 1
                elif _is_worsening(metric, delta, worse_threshold=worse_threshold):
                    net_worsen += 1
                lines.append(
                    f"  {phase}: {_format_metric_label(metric, delta, worse_threshold=worse_threshold)}"
                )

        # Net verdict
        if net_improve == 0 and net_worsen == 0:
            verdict = "no measurable change"
        elif net_worsen == 0:
            verdict = "clear improvement"
        elif net_improve == 0:
            verdict = "clear regression"
        elif net_improve > net_worsen:
            verdict = "marginal improvement"
        elif net_worsen > net_improve:
            verdict = "marginal regression"
        else:
            verdict = "mixed"
        lines.append(f"  Net: {verdict}")

    return lines


def format_pareto_tradeoff_lines(
    impacts: Mapping[str, float],
    *,
    worse_threshold: float = 1e-6,
) -> list[str]:
    """Build a short PARETO TRADEOFFS block summarising improved/worsened
    corners and (when available) the net of the absolute deltas as a
    rough proxy for tradeoff direction.
    """
    if not impacts:
        return []
    summary = pareto_summary(impacts, worse_threshold=worse_threshold)
    if summary.improved == 0 and summary.worsened == 0:
        return []

    # Group by corner for a corner-level view
    improved_corners: set[int] = set()
    worsened_corners: set[int] = set()
    sum_improved_abs = 0.0
    sum_worsened_abs = 0.0
    for key, delta in impacts.items():
        parsed = parse_cpm_key(key)
        if parsed is None:
            continue
        cid, _phase, metric = parsed
        if _is_improving(metric, delta, worse_threshold=worse_threshold):
            improved_corners.add(cid)
            sum_improved_abs += abs(delta)
        elif _is_worsening(metric, delta, worse_threshold=worse_threshold):
            worsened_corners.add(cid)
            sum_worsened_abs += abs(delta)

    lines: list[str] = []
    lines.append(
        f"This setup improves {len(improved_corners)} corner(s) "
        f"and worsens {len(worsened_corners)} corner(s)."
    )
    if worsened_corners:
        lines.append(f"  Worsened corners: {sorted(worsened_corners)}")
    if improved_corners:
        lines.append(f"  Improved corners: {sorted(improved_corners)}")
    if summary.worsened == 0:
        lines.append("Pareto-dominant: no corner-phase metric regresses.")
    else:
        if sum_improved_abs > sum_worsened_abs:
            net = "net improvement (sum of absolute improvements outweighs regressions)."
        elif sum_improved_abs < sum_worsened_abs:
            net = (
                "net regression (sum of absolute regressions outweighs improvements) — "
                "consider re-running with --search-mode legal."
            )
        else:
            net = "balanced tradeoff."
        lines.append(f"Tradeoff: {net}")
    return lines
