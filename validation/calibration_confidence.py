"""Calibration confidence / sample-efficiency reporter.

Computes the sample-efficiency curve (LOO R2 vs n_samples) for a given
``(car, track)`` pair from the on-disk calibration corpus, fits a saturating
model ``R2(n) = R2_max x (1 - exp(-n/tau))``, and forecasts how many additional
calibration sessions are predicted to be needed to reach a target R2 (default
``R2_THRESHOLD_BLOCK = 0.85``).

The report is purely diagnostic. It does **not** modify any calibration model
file on disk; it merely re-fits the regression on randomly drawn subsets of
the existing point list and records the resulting LOO R2 for each model.

Usage::

    python -m validation.calibration_confidence --car cadillac --track silverstone
    python -m validation.calibration_confidence --car bmw --track sebring \\
        --target front_static_rh_mm
    python -m validation.calibration_confidence --car porsche --track algarve \\
        --gate-r2 0.95
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np

from car_model.auto_calibrate import (
    CalibrationPoint,
    FittedModel,
    _MIN_SESSIONS_FOR_FIT,
    _setup_fingerprint,
    fit_models_from_points,
    load_calibration_points,
)
from car_model.calibration_gate import R2_THRESHOLD_BLOCK, R2_THRESHOLD_WARN
from car_model.registry import resolve_car, supported_car_names, track_key

# Subset sizes evaluated. Truncated below n_total. Each size is bootstrapped
# K times and the LOO R2 values are averaged.
_DEFAULT_SUBSET_SIZES = [3, 5, 7, 10, 15, 20, 25, 30]
_DEFAULT_K_SUBSAMPLES = 10

# Order matters for display; keep grouped by axle / model family.
_TARGETS = [
    "front_ride_height",
    "rear_ride_height",
    "torsion_bar_turns",
    "torsion_bar_defl",
    "torsion_bar_defl_direct",
    "front_shock_defl_static",
    "rear_shock_defl_static",
    "heave_spring_defl_static",
    "heave_spring_defl_max",
    "heave_slider_defl_static",
    "rear_spring_defl_static",
    "rear_spring_defl_max",
    "third_spring_defl_static",
    "third_spring_defl_max",
    "third_slider_defl_static",
    "third_slider_defl_direct",
]


# -----------------------------------------------------------------------------
# Data structures
# -----------------------------------------------------------------------------

@dataclass
class SubsetResult:
    """LOO R2 statistics for one (target, n) cell."""
    target: str
    n: int
    mean_r2: float
    std_r2: float
    n_successful_fits: int
    n_attempts: int


@dataclass
class ForecastResult:
    """Saturating-curve forecast for one target."""
    target: str
    r2_max: float | None
    tau: float | None
    n_required_at_gate: int | None
    gate_r2: float
    fit_succeeded: bool
    note: str = ""


# -----------------------------------------------------------------------------
# Track filtering
# -----------------------------------------------------------------------------

def _filter_points_by_track(
    points: list[CalibrationPoint],
    track: str,
) -> list[CalibrationPoint]:
    """Return points whose ``track`` field maps to the same canonical key.

    The match is done through ``car_model.registry.track_key`` so that
    ``"Algarve"`` matches ``"Autodromo Internacional do Algarve"`` etc.
    """
    if not track:
        return list(points)
    target_key = track_key(track)
    if not target_key:
        return list(points)
    return [pt for pt in points if track_key(pt.track) == target_key]


# -----------------------------------------------------------------------------
# Subset evaluation
# -----------------------------------------------------------------------------

def _model_loo_r2(model: FittedModel | None) -> float | None:
    """Extract the honest generalization metric from a fitted model.

    Prefers ``q_squared`` (LOO R2); falls back to ``r_squared`` only when
    LOO is unavailable (n<5 fit). Returns ``None`` if the model is missing
    or uncalibrated with no usable score.
    """
    if model is None:
        return None
    if not model.is_calibrated:
        # An uncalibrated model still carries an R2 value but we treat it
        # as 0.0 to avoid rewarding overfit fits with R2~1 / LOO=inf.
        return 0.0
    if model.q_squared is not None and not math.isnan(model.q_squared):
        return float(model.q_squared)
    if model.r_squared is not None and not math.isnan(model.r_squared):
        return float(model.r_squared)
    return None


def _evaluate_subset(
    car_canonical: str,
    points: list[CalibrationPoint],
    target: str,
) -> float | None:
    """Fit on the given subset and return LOO R2 for one target."""
    models = fit_models_from_points(car_canonical, points)
    return _model_loo_r2(getattr(models, target, None))


def measure_sample_efficiency(
    car_canonical: str,
    points: list[CalibrationPoint],
    targets: list[str],
    subset_sizes: list[int],
    k_subsamples: int,
    seed: int = 17,
) -> dict[str, list[SubsetResult]]:
    """Compute LOO R2 vs subset size, per target.

    For each ``n`` in *subset_sizes* (skipping values >= len(points) except
    the n_total endpoint), draw ``k_subsamples`` random subsets of size ``n``
    and fit the regression. Aggregate LOO R2 as mean +/- std across subsamples.
    """
    n_total = len(points)
    if n_total < _MIN_SESSIONS_FOR_FIT:
        return {t: [] for t in targets}

    sizes = [n for n in subset_sizes if n <= n_total]
    if n_total not in sizes:
        sizes.append(n_total)
    sizes = sorted(set(sizes))

    rng = random.Random(seed)

    results: dict[str, list[SubsetResult]] = {t: [] for t in targets}
    for n in sizes:
        # n == n_total is deterministic -- only one full subset exists.
        k = 1 if n == n_total else k_subsamples
        per_target_scores: dict[str, list[float]] = {t: [] for t in targets}
        for _ in range(k):
            if n == n_total:
                subset = list(points)
            else:
                subset = rng.sample(points, n)
            models = fit_models_from_points(car_canonical, subset)
            for t in targets:
                score = _model_loo_r2(getattr(models, t, None))
                if score is not None:
                    per_target_scores[t].append(score)
        for t in targets:
            scores = per_target_scores[t]
            if scores:
                arr = np.asarray(scores, dtype=float)
                results[t].append(SubsetResult(
                    target=t,
                    n=n,
                    mean_r2=float(arr.mean()),
                    std_r2=float(arr.std(ddof=0)),
                    n_successful_fits=int(arr.size),
                    n_attempts=k,
                ))
            else:
                results[t].append(SubsetResult(
                    target=t,
                    n=n,
                    mean_r2=float("nan"),
                    std_r2=float("nan"),
                    n_successful_fits=0,
                    n_attempts=k,
                ))
    return results


# -----------------------------------------------------------------------------
# Saturating curve forecast
# -----------------------------------------------------------------------------

def _saturating(n: np.ndarray, r2_max: float, tau: float) -> np.ndarray:
    return r2_max * (1.0 - np.exp(-n / max(tau, 1e-6)))


def fit_forecast(
    target: str,
    samples: list[SubsetResult],
    gate_r2: float,
) -> ForecastResult:
    """Fit ``R2(n) = R2_max(1 - exp(-n/tau))`` and forecast n_required.

    Falls back to ``"insufficient data for forecast"`` when fewer than three
    distinct (n, R2) points are available or when the curve fit fails.
    """
    valid = [
        (s.n, s.mean_r2)
        for s in samples
        if s.n_successful_fits > 0 and not math.isnan(s.mean_r2)
    ]
    distinct_ns = {n for n, _ in valid}
    if len(distinct_ns) < 3:
        return ForecastResult(
            target=target,
            r2_max=None,
            tau=None,
            n_required_at_gate=None,
            gate_r2=gate_r2,
            fit_succeeded=False,
            note="insufficient data for forecast (need >=3 distinct n)",
        )

    xs = np.asarray([n for n, _ in valid], dtype=float)
    ys = np.asarray([r2 for _, r2 in valid], dtype=float)

    try:
        from scipy.optimize import curve_fit
        # Bound R2_max in [0, 1] and tau in [0.5, 1000] sample-counts.
        popt, _ = curve_fit(
            _saturating, xs, ys,
            p0=(min(0.99, max(ys.max(), 0.5)), max(xs.mean(), 5.0)),
            bounds=([0.0, 0.5], [1.0, 1000.0]),
            maxfev=5000,
        )
        r2_max, tau = float(popt[0]), float(popt[1])
    except Exception as exc:  # noqa: BLE001
        return ForecastResult(
            target=target,
            r2_max=None,
            tau=None,
            n_required_at_gate=None,
            gate_r2=gate_r2,
            fit_succeeded=False,
            note=f"curve fit failed: {exc!s}",
        )

    if r2_max <= gate_r2 + 1e-6:
        # The asymptote is below the gate -- forecast n is undefined.
        return ForecastResult(
            target=target,
            r2_max=r2_max,
            tau=tau,
            n_required_at_gate=None,
            gate_r2=gate_r2,
            fit_succeeded=True,
            note=(
                f"asymptote R2_max={r2_max:.3f} below gate {gate_r2:.2f} -- "
                "current feature pool can't reach the gate threshold; "
                "consider richer features, more varied setups, or lowering the gate"
            ),
        )

    ratio = 1.0 - gate_r2 / r2_max
    n_req = -tau * math.log(max(ratio, 1e-9))
    return ForecastResult(
        target=target,
        r2_max=r2_max,
        tau=tau,
        n_required_at_gate=int(math.ceil(n_req)),
        gate_r2=gate_r2,
        fit_succeeded=True,
    )


# -----------------------------------------------------------------------------
# Reporting
# -----------------------------------------------------------------------------

def _format_target_curve(
    target: str,
    samples: list[SubsetResult],
    forecast: ForecastResult,
    n_total: int,
) -> str:
    """Return a multi-line string describing one target's curve + forecast."""
    lines = [f"  Target: {target}"]
    if not samples:
        lines.append("    (no successful fits at any subset size)")
        return "\n".join(lines)
    for s in samples:
        if s.n_successful_fits == 0:
            lines.append(
                f"    n={s.n:<4d}: no successful fit "
                f"(0/{s.n_attempts} subsamples produced a model)"
            )
            continue
        forecast_tag = ""
        if forecast.fit_succeeded and forecast.r2_max is not None:
            extrapolated = forecast.r2_max * (
                1.0 - math.exp(-s.n / max(forecast.tau or 1e-6, 1e-6))
            )
            if abs(extrapolated - s.mean_r2) > 0.05 and s.n > n_total:
                # never happens (s.n <= n_total) but kept defensive
                forecast_tag = "  (forecast)"
        lines.append(
            f"    n={s.n:<4d}: R2 = {s.mean_r2:0.3f} +/- {s.std_r2:0.3f}  "
            f"({s.n_successful_fits}/{s.n_attempts} subsamples){forecast_tag}"
        )

    if forecast.fit_succeeded and forecast.r2_max is not None:
        if forecast.n_required_at_gate is None:
            lines.append(
                f"    Forecast: R2_max ~ {forecast.r2_max:0.3f} (asymptote), "
                f"tau ~ {forecast.tau:0.1f} samples"
            )
            if forecast.note:
                lines.append(f"    Note: {forecast.note}")
        else:
            extra = max(forecast.n_required_at_gate - n_total, 0)
            verdict = (
                f"already meets gate (R2>={forecast.gate_r2:.2f})"
                if extra == 0
                else f"need ~{extra} more sessions"
            )
            lines.append(
                f"    Forecast: R2_max ~ {forecast.r2_max:0.3f}, "
                f"tau ~ {forecast.tau:0.1f}; reach R2={forecast.gate_r2:.2f} "
                f"at n~{forecast.n_required_at_gate}  -> {verdict}"
            )
    elif forecast.note:
        lines.append(f"    Forecast: {forecast.note}")
    return "\n".join(lines)


def _current_r2_for(samples: list[SubsetResult], n_total: int) -> float | None:
    for s in samples:
        if s.n == n_total and s.n_successful_fits > 0:
            return s.mean_r2
    return None


def render_report(
    car_canonical: str,
    car_display: str,
    track_display: str,
    track_canonical: str,
    n_total: int,
    n_unique: int,
    sample_curve: dict[str, list[SubsetResult]],
    forecasts: dict[str, ForecastResult],
    gate_r2: float,
) -> str:
    """Render the final ASCII report."""
    lines: list[str] = []
    lines.append("")
    lines.append(
        f"CALIBRATION CONFIDENCE for {car_display} at {track_display or '(any track)'}"
    )
    lines.append("=" * 78)
    lines.append(f"  Canonical car key:   {car_canonical}")
    lines.append(f"  Canonical track key: {track_canonical or '(unfiltered)'}")
    lines.append(f"  Total points:        {n_total}")
    lines.append(f"  Unique setups:       {n_unique}")
    lines.append(f"  Gate threshold:      R2 >= {gate_r2:.2f}")
    lines.append("")

    if n_total == 0:
        lines.append("  no data -- nothing to report")
        return "\n".join(lines)
    if n_unique < _MIN_SESSIONS_FOR_FIT:
        remaining = _MIN_SESSIONS_FOR_FIT - n_unique
        lines.append(
            f"  insufficient data: {n_unique}/{_MIN_SESSIONS_FOR_FIT} unique "
            f"setups -- need {remaining} more before any model can be fit"
        )
        return "\n".join(lines)

    lines.append("Sample-efficiency curve (LOO R2 vs n_samples):")
    any_targets_evaluated = False
    for t in _TARGETS:
        if t not in sample_curve:
            continue
        samples = sample_curve[t]
        if not samples:
            continue
        # If every subset failed, skip silently.
        if all(s.n_successful_fits == 0 for s in samples):
            continue
        any_targets_evaluated = True
        forecast = forecasts.get(t) or ForecastResult(
            target=t, r2_max=None, tau=None, n_required_at_gate=None,
            gate_r2=gate_r2, fit_succeeded=False, note="not forecast",
        )
        lines.append(_format_target_curve(t, samples, forecast, n_total))
        lines.append("")

    if not any_targets_evaluated:
        lines.append(
            "  (no target produced a successful fit at any subset size -- "
            "calibration corpus may be too narrow / single-setup)"
        )
        return "\n".join(lines)

    # Recommendation summary across targets.
    lines.append("RECOMMENDATION")
    lines.append("-" * 78)
    needs: list[tuple[str, int]] = []
    saturated: list[str] = []
    asymptote_below_gate: list[tuple[str, float]] = []
    no_forecast: list[str] = []
    for t, fc in forecasts.items():
        cur = _current_r2_for(sample_curve.get(t, []), n_total)
        if cur is None:
            continue
        if not fc.fit_succeeded:
            no_forecast.append(t)
            continue
        if fc.r2_max is not None and fc.r2_max <= gate_r2 + 1e-6:
            asymptote_below_gate.append((t, fc.r2_max))
            continue
        if cur >= gate_r2 - 1e-6:
            saturated.append(t)
        elif fc.n_required_at_gate is not None:
            extra = max(fc.n_required_at_gate - n_total, 0)
            if extra == 0:
                saturated.append(t)
            else:
                needs.append((t, extra))

    if saturated:
        lines.append(
            "  Targets meeting gate today: "
            + ", ".join(sorted(saturated))
        )
    if needs:
        worst = max(needs, key=lambda x: x[1])
        lines.append(
            f"  Need ~{worst[1]} more calibration sessions to meet the gate "
            f"on the slowest-saturating target ({worst[0]})."
        )
        for t, extra in sorted(needs, key=lambda x: -x[1]):
            lines.append(f"    - {t}: +{extra} sessions")
    if asymptote_below_gate:
        lines.append(
            "  Asymptote below gate (more data alone won't help -- "
            "consider richer features or wider setup variation):"
        )
        for t, r2m in asymptote_below_gate:
            lines.append(f"    - {t}: R2_max ~ {r2m:0.3f}")
    if no_forecast:
        lines.append(
            "  No forecast (need >=3 distinct subset sizes with successful fits): "
            + ", ".join(sorted(no_forecast))
        )

    if not (saturated or needs or asymptote_below_gate or no_forecast):
        lines.append("  (no targets evaluated -- see per-target notes above)")

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Calibration confidence reporter: fits the regression on subsets "
            "of size 3, 5, 7, 10, 15, 20, 25, n_total and forecasts how many "
            "more sessions are needed to reach R2=0.85."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--car", required=True, help="Canonical car key (any form accepted by car_model.registry.resolve_car).")
    parser.add_argument("--track", default="", help="Track name (any form; matched via track_key). Default: pool all tracks.")
    parser.add_argument("--target", default=None, help="Restrict to a single target attribute on CarCalibrationModels (e.g. front_ride_height).")
    parser.add_argument(
        "--gate-r2", type=float, default=R2_THRESHOLD_BLOCK,
        help=f"R2 threshold to forecast against (default {R2_THRESHOLD_BLOCK} = R2_THRESHOLD_BLOCK).",
    )
    parser.add_argument(
        "--k-subsamples", type=int, default=_DEFAULT_K_SUBSAMPLES,
        help=f"Number of random subsamples per subset size (default {_DEFAULT_K_SUBSAMPLES}).",
    )
    parser.add_argument("--seed", type=int, default=17, help="RNG seed for reproducibility.")
    parser.add_argument("--max-n", type=int, default=30, help="Cap on subset sizes evaluated (default 30).")
    return parser.parse_args(argv)


def _resolve_or_print_help(name: str) -> str | None:
    """Resolve a car name to its canonical key or print supported names."""
    identity = resolve_car(name)
    if identity is None:
        return None
    return identity.canonical


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Suppress per-fit overfit warnings from auto_calibrate. The whole point
    # of this report is to tell the user about fit quality across subset
    # sizes -- we'd emit hundreds of warnings otherwise.
    logging.getLogger("car_model.auto_calibrate").setLevel(logging.ERROR)

    canonical = _resolve_or_print_help(args.car)
    if canonical is None:
        print(
            f"Unknown car: {args.car!r}. Supported: {supported_car_names()}",
            file=sys.stderr,
        )
        return 2

    identity = resolve_car(args.car)
    car_display = identity.display_name if identity else args.car

    if identity is not None and args.car.lower() != canonical:
        # Note: resolve_car will substring-match aggressively (e.g. typing
        # "bmw_m4_gt3" can resolve to "bmw" in branches without GT3 entries).
        # Surface this so the user notices the substitution rather than
        # silently fitting on BMW data.
        if not (args.car.lower() == canonical.lower() or args.car == identity.display_name):
            print(
                f"NOTE: '{args.car}' resolved to canonical '{canonical}' "
                f"({car_display}). If this is wrong, check the registry.",
                file=sys.stderr,
            )

    points_all = load_calibration_points(canonical)
    if not points_all:
        print(
            f"\nCALIBRATION CONFIDENCE for {car_display} at {args.track or '(any track)'}\n"
            f"{'=' * 78}\n"
            f"  no data -- calibration corpus is empty for car '{canonical}'."
        )
        return 0

    track_canonical = track_key(args.track) if args.track else ""
    points = _filter_points_by_track(points_all, args.track)

    if not points:
        print(
            f"\nCALIBRATION CONFIDENCE for {car_display} at {args.track}\n"
            f"{'=' * 78}\n"
            f"  no data -- {len(points_all)} points exist for '{canonical}', "
            f"but none match track key '{track_canonical}'."
        )
        return 0

    seen: set[tuple] = set()
    for pt in points:
        seen.add(_setup_fingerprint(pt))
    n_unique = len(seen)

    # Subset sizes capped at min(n_total, --max-n). The full-corpus point is
    # always included so the report shows "current LOO R2" directly.
    cap = min(len(points), max(args.max_n, _MIN_SESSIONS_FOR_FIT))
    sizes = [n for n in _DEFAULT_SUBSET_SIZES if n <= cap]
    if cap not in sizes:
        sizes.append(cap)

    if args.target is not None:
        if args.target not in _TARGETS:
            print(
                f"Unknown target: {args.target!r}. Choose from: {_TARGETS}",
                file=sys.stderr,
            )
            return 2
        targets = [args.target]
    else:
        targets = list(_TARGETS)

    sample_curve = measure_sample_efficiency(
        canonical, points, targets, sizes,
        k_subsamples=max(args.k_subsamples, 1),
        seed=args.seed,
    )
    forecasts = {
        t: fit_forecast(t, sample_curve.get(t, []), args.gate_r2)
        for t in targets
    }

    # Track display: prefer the IBT-recorded form from the matched points, falling back to the user input.
    track_display = ""
    for pt in points:
        if pt.track:
            track_display = pt.track
            break
    if not track_display:
        track_display = args.track

    report = render_report(
        car_canonical=canonical,
        car_display=car_display,
        track_display=track_display,
        track_canonical=track_canonical,
        n_total=len(points),
        n_unique=n_unique,
        sample_curve=sample_curve,
        forecasts=forecasts,
        gate_r2=args.gate_r2,
    )
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
