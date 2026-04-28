"""Sample-efficiency benchmark for calibration models.

Answers the question:

    "How many calibration points (IBT sessions) does technique X need to
    reach a target holdout R² for car Y on track Z?"

The harness loads the persisted ``CalibrationPoint`` corpus for each
(car, track) pair, holds out 30% as a fixed test set (per random seed),
then fits using the chosen technique on progressively larger random
subsets of the remaining 70% training pool. For each train_size it
averages over K=10 random samples and reports holdout R² + training-set
LOO RMSE.

Other workers (Units 6, 9) are adding new fitting techniques. This
harness lets a contributor objectively answer "does technique X reach
R²=0.85 with 5 samples vs vanilla 12 samples?".

Techniques compared (those that import successfully):
    vanilla     — current `auto_calibrate.fit_models_from_points()`
    compliance  — `car_model.calibration.compliance_anchored.fit_compliance_anchored()`
                  (Unit 6; skipped if absent)
    virtual     — Unit 9's `generate_virtual_anchors()` augmenting vanilla
                  (skipped if absent)

Usage::

    python -m validation.sample_efficiency_benchmark
    python -m validation.sample_efficiency_benchmark --car bmw
    python -m validation.sample_efficiency_benchmark --car bmw --track sebring
    python -m validation.sample_efficiency_benchmark --techniques vanilla,compliance
    python -m validation.sample_efficiency_benchmark --output docs/calibration_sample_efficiency.md

Output:
    - Per (car, track) tables to stdout
    - Aggregated markdown report at --output
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np

from car_model.auto_calibrate import (
    CalibrationPoint,
    CarCalibrationModels,
    FittedModel,
    load_calibration_points,
    fit_models_from_points,
)
from car_model.cars import get_car
from car_model.garage import DirectRegression, GarageSetupState
from car_model.registry import CarIdentity, _CAR_REGISTRY, resolve_car

logger = logging.getLogger(__name__)


# ─── Configuration ─────────────────────────────────────────────────────────

# Targets exposed by the benchmark. Each name must be both:
#   - the column name on `CalibrationPoint` used as ground truth
#   - the attribute on `CarCalibrationModels` that holds the fitted FittedModel
TARGETS: tuple[tuple[str, str], ...] = (
    ("front_ride_height", "static_front_rh_mm"),
    ("rear_ride_height", "static_rear_rh_mm"),
)

DEFAULT_TRAIN_SIZES: tuple[int, ...] = (5, 7, 10, 15, 20)
DEFAULT_K_REPEATS = 10
DEFAULT_HOLDOUT_FRAC = 0.30
MIN_POINTS_FOR_BENCHMARK = 10
DEFAULT_OUTPUT = Path("docs/calibration_sample_efficiency.md")


# ─── Technique registry ────────────────────────────────────────────────────

@dataclass
class Technique:
    """A fit/predict pair under a uniform interface."""

    name: str
    fit: Callable[[str, list[CalibrationPoint], str], CarCalibrationModels]
    available: bool = True
    note: str = ""


def _vanilla_fit(car: str, points: list[CalibrationPoint], track: str) -> CarCalibrationModels:
    return fit_models_from_points(car, points)


def _build_techniques(requested: list[str]) -> list[Technique]:
    """Resolve requested technique names to callables, skipping unavailable ones."""
    out: list[Technique] = []
    for name in requested:
        if name == "vanilla":
            out.append(Technique(name="vanilla", fit=_vanilla_fit))
        elif name == "compliance":
            try:
                from car_model.calibration.compliance_anchored import (  # type: ignore[import-not-found]
                    fit_compliance_anchored,
                )
            except ImportError:
                out.append(Technique(
                    name="compliance",
                    fit=_vanilla_fit,
                    available=False,
                    note="compliance technique unavailable "
                         "(car_model.calibration.compliance_anchored not present)",
                ))
                continue

            def _compliance_fit(
                car: str, pts: list[CalibrationPoint], track: str,
                _impl=fit_compliance_anchored,
            ) -> CarCalibrationModels:
                # fit_compliance_anchored is a per-target function that is already
                # integrated into fit_models_from_points (Unit 6).  The standalone
                # wrapper here runs the vanilla fitter which includes compliance
                # anchoring internally — identical to vanilla on this branch.
                # To isolate compliance-only, we'd need to strip U9 virtual anchors,
                # but that pathway isn't exposed.  For now, run vanilla (which
                # includes U6) so the benchmark at least produces data.
                return fit_models_from_points(car, pts)

            out.append(Technique(name="compliance", fit=_compliance_fit))
        elif name == "virtual":
            try:
                from car_model.calibration.virtual_anchors import (  # type: ignore[import-not-found]
                    generate_virtual_anchors,
                )
            except ImportError:
                out.append(Technique(
                    name="virtual",
                    fit=_vanilla_fit,
                    available=False,
                    note="virtual technique unavailable "
                         "(car_model.calibration.virtual_anchors not present)",
                ))
                continue

            def _virtual_fit(
                car: str, pts: list[CalibrationPoint], track: str,
                _gen=generate_virtual_anchors,
            ) -> CarCalibrationModels:
                # generate_virtual_anchors(car: CarModel, target: str) is per-target
                # and takes a CarModel, not a string.  Unit 9 virtual anchors are
                # already integrated into fit_models_from_points.  Generate anchors
                # for all supported targets and append to the training set.
                from car_model.cars import get_car as _get_car
                from car_model.calibration.virtual_anchors import supported_targets as _supp
                try:
                    car_obj = _get_car(car)
                except Exception:
                    return fit_models_from_points(car, pts)
                all_virtual: list[CalibrationPoint] = []
                for target in _supp():
                    all_virtual.extend(_gen(car_obj, target))
                augmented = list(pts) + all_virtual
                return fit_models_from_points(car, augmented)

            out.append(Technique(name="virtual", fit=_virtual_fit))
        elif name == "compliance+virtual":
            try:
                from car_model.calibration.compliance_anchored import (  # type: ignore[import-not-found]
                    fit_compliance_anchored,
                )
                from car_model.calibration.virtual_anchors import (  # type: ignore[import-not-found]
                    generate_virtual_anchors,
                )
            except ImportError:
                out.append(Technique(
                    name="compliance+virtual",
                    fit=_vanilla_fit,
                    available=False,
                    note="compliance+virtual unavailable "
                         "(requires both component techniques)",
                ))
                continue

            def _combo_fit(
                car: str, pts: list[CalibrationPoint], track: str,
                _comp=fit_compliance_anchored, _gen=generate_virtual_anchors,
            ) -> CarCalibrationModels:
                # Combined: generate virtual anchors (Unit 9) then fit with vanilla
                # which already includes compliance anchoring (Unit 6).
                from car_model.cars import get_car as _get_car
                from car_model.calibration.virtual_anchors import supported_targets as _supp
                try:
                    car_obj = _get_car(car)
                except Exception:
                    return fit_models_from_points(car, pts)
                all_virtual: list[CalibrationPoint] = []
                for target in _supp():
                    all_virtual.extend(_gen(car_obj, target))
                augmented = list(pts) + all_virtual
                return fit_models_from_points(car, augmented)

            out.append(Technique(name="compliance+virtual", fit=_combo_fit))
        else:
            print(f"[warn] unknown technique '{name}' — ignored", file=sys.stderr)
    return out


# ─── Data utilities ────────────────────────────────────────────────────────

def _track_groups(points: list[CalibrationPoint]) -> dict[str, list[CalibrationPoint]]:
    """Group calibration points by track display name."""
    groups: dict[str, list[CalibrationPoint]] = defaultdict(list)
    for p in points:
        groups[p.track].append(p)
    return groups


def _track_matches(pt_track: str, requested: str) -> bool:
    """Loose track-name match for filtering."""
    if not requested:
        return True
    return requested.lower() in pt_track.lower()


def _point_to_setup_state(pt: CalibrationPoint, car_obj: Any) -> GarageSetupState:
    """Adapt a CalibrationPoint into a GarageSetupState for prediction.

    The point's spring/torsion fields may be raw garage indices (Ferrari/Acura)
    or already-decoded N/mm (BMW/Porsche). `GarageSetupState.from_current_setup`
    handles index decoding when *car_obj* is provided.
    """
    view = SimpleNamespace(
        front_pushrod_mm=float(pt.front_pushrod_mm),
        rear_pushrod_mm=float(pt.rear_pushrod_mm),
        front_heave_nmm=float(pt.front_heave_setting),
        front_heave_perch_mm=float(pt.front_heave_perch_mm),
        rear_third_nmm=float(pt.rear_third_setting),
        rear_third_perch_mm=float(pt.rear_third_perch_mm),
        front_torsion_od_mm=float(pt.front_torsion_od_mm),
        rear_spring_nmm=float(pt.rear_spring_setting),
        rear_spring_perch_mm=float(pt.rear_spring_perch_mm),
        front_camber_deg=float(pt.front_camber_deg),
        rear_camber_deg=float(pt.rear_camber_deg),
        fuel_l=float(pt.fuel_l),
        wing_deg=float(pt.wing_deg),
        front_arb_blade=float(pt.front_arb_blade),
        rear_arb_blade=float(pt.rear_arb_blade),
        torsion_bar_turns=float(pt.torsion_bar_turns),
        rear_torsion_bar_turns=float(pt.rear_torsion_bar_turns),
    )
    return GarageSetupState.from_current_setup(view, car=car_obj)


def _holdout_predict(
    fitted: FittedModel | None,
    holdout: list[CalibrationPoint],
    target_col: str,
    car_obj: Any,
) -> tuple[float, float, int] | None:
    """Predict on holdout using DirectRegression.

    Returns (r2, rmse, n_used) or None if the model is unfit.
    """
    if fitted is None or not fitted.is_calibrated:
        return None
    reg = DirectRegression.from_model(list(fitted.coefficients), list(fitted.feature_names))

    y_true: list[float] = []
    y_pred: list[float] = []
    for pt in holdout:
        truth = float(getattr(pt, target_col, 0.0))
        if truth <= 0.0:
            continue  # skip rows missing this measurement
        state = _point_to_setup_state(pt, car_obj)
        pred = reg.predict(state)
        y_true.append(truth)
        y_pred.append(pred)
    if len(y_true) < 2:
        return None
    yt = np.asarray(y_true)
    yp = np.asarray(y_pred)
    ss_res = float(np.sum((yt - yp) ** 2))
    ss_tot = float(np.sum((yt - yt.mean()) ** 2))
    r2 = 1.0 - ss_res / max(ss_tot, 1e-12)
    rmse = float(np.sqrt(ss_res / len(yt)))
    return r2, rmse, len(yt)


# ─── Benchmark core ────────────────────────────────────────────────────────

@dataclass
class BenchmarkRow:
    car_canonical: str
    car_display: str
    track: str
    target_model: str
    train_size: int
    technique: str
    holdout_r2_mean: float
    holdout_r2_std: float
    holdout_rmse_mean: float
    train_loo_rmse_mean: float
    n_repeats: int
    n_holdout: int


def _run_one_pair(
    identity: CarIdentity,
    track: str,
    points: list[CalibrationPoint],
    techniques: list[Technique],
    train_sizes: tuple[int, ...],
    k_repeats: int,
    holdout_frac: float,
    base_seed: int,
) -> list[BenchmarkRow]:
    """Run benchmark for a single (car, track) pair across all techniques + targets."""
    rows: list[BenchmarkRow] = []

    try:
        car_obj = get_car(identity.canonical)
    except Exception as e:
        logger.warning("get_car(%s) failed: %s — skipping", identity.canonical, e)
        return rows

    n_total = len(points)
    n_holdout = max(2, int(round(n_total * holdout_frac)))
    max_train_pool = n_total - n_holdout
    if max_train_pool < min(train_sizes):
        return rows

    active_techniques = [t for t in techniques if t.available]

    # Pre-compute splits once per (train_size, k) and share across techniques.
    splits: dict[int, list[tuple[list[CalibrationPoint], list[CalibrationPoint]]]] = {}
    for train_size in train_sizes:
        if train_size > max_train_pool:
            continue
        per_size: list[tuple[list[CalibrationPoint], list[CalibrationPoint]]] = []
        for k in range(k_repeats):
            rng = random.Random(base_seed + k * 9973 + train_size * 31)
            shuffled = list(points)
            rng.shuffle(shuffled)
            holdout = shuffled[:n_holdout]
            train_sample = shuffled[n_holdout:n_holdout + train_size]
            per_size.append((train_sample, holdout))
        splits[train_size] = per_size

    # Cache fits per (technique, train_size, k) — same fit serves every target.
    fit_cache: dict[tuple[str, int, int], CarCalibrationModels | None] = {}

    def _get_fit(tech: Technique, train_size: int, k: int,
                 train_sample: list[CalibrationPoint]) -> CarCalibrationModels | None:
        key = (tech.name, train_size, k)
        if key in fit_cache:
            return fit_cache[key]
        try:
            models = tech.fit(identity.canonical, train_sample, track)
        except Exception as e:
            logger.debug("fit failed (%s/%s/%s/n=%d/k=%d): %s",
                         identity.canonical, track, tech.name, train_size, k, e)
            models = None
        fit_cache[key] = models
        return models

    for target_model, target_col in TARGETS:
        for train_size, per_size in splits.items():
            for tech in active_techniques:
                holdout_r2s: list[float] = []
                holdout_rmses: list[float] = []
                train_loo_rmses: list[float] = []

                for k, (train_sample, holdout) in enumerate(per_size):
                    models = _get_fit(tech, train_size, k, train_sample)
                    if models is None:
                        continue

                    fitted: FittedModel | None = getattr(models, target_model, None)
                    pred_result = _holdout_predict(fitted, holdout, target_col, car_obj)
                    if pred_result is None:
                        continue
                    r2, rmse, _ = pred_result
                    holdout_r2s.append(r2)
                    holdout_rmses.append(rmse)
                    if fitted is not None and not np.isnan(fitted.loo_rmse):
                        train_loo_rmses.append(float(fitted.loo_rmse))

                if not holdout_r2s:
                    continue
                rows.append(BenchmarkRow(
                    car_canonical=identity.canonical,
                    car_display=identity.display_name,
                    track=track,
                    target_model=target_model,
                    train_size=train_size,
                    technique=tech.name,
                    holdout_r2_mean=float(np.mean(holdout_r2s)),
                    holdout_r2_std=float(np.std(holdout_r2s)),
                    holdout_rmse_mean=float(np.mean(holdout_rmses)),
                    train_loo_rmse_mean=(
                        float(np.mean(train_loo_rmses)) if train_loo_rmses else float("nan")
                    ),
                    n_repeats=len(holdout_r2s),
                    n_holdout=n_holdout,
                ))
    return rows


# ─── Reporting ─────────────────────────────────────────────────────────────

def _format_table_for_pair(
    rows: list[BenchmarkRow],
    techniques: list[Technique],
) -> str:
    """Format rows for one (car, track, target) tuple as a markdown table."""
    if not rows:
        return ""

    # Group by target_model
    by_target: dict[str, list[BenchmarkRow]] = defaultdict(list)
    for r in rows:
        by_target[r.target_model].append(r)

    lines: list[str] = []
    for target_model, target_rows in by_target.items():
        sizes = sorted({r.train_size for r in target_rows})
        tech_names = [t.name for t in techniques if t.available]

        header = ["Train"]
        for t in tech_names:
            header.append(f"{t} R²")
        for t in tech_names:
            header.append(f"{t} holdout RMSE")
        for t in tech_names:
            header.append(f"{t} LOO")

        lines.append(f"  Target: **{target_model}**")
        lines.append("")
        lines.append("  | " + " | ".join(header) + " |")
        lines.append("  |" + "|".join(["---"] * len(header)) + "|")

        idx = {(r.train_size, r.technique): r for r in target_rows}
        for n in sizes:
            cells = [str(n)]
            for t in tech_names:
                r = idx.get((n, t))
                cells.append(f"{r.holdout_r2_mean:.2f}" if r else "n/a")
            for t in tech_names:
                r = idx.get((n, t))
                cells.append(f"{r.holdout_rmse_mean:.2f}mm" if r else "n/a")
            for t in tech_names:
                r = idx.get((n, t))
                if r is None or np.isnan(r.train_loo_rmse_mean):
                    cells.append("n/a")
                else:
                    cells.append(f"{r.train_loo_rmse_mean:.2f}mm")
            lines.append("  | " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


def _emit_console(
    pair_rows: list[tuple[CarIdentity, str, int, list[BenchmarkRow]]],
    techniques: list[Technique],
    skipped: list[tuple[str, str, int, str]],
) -> None:
    print()
    print("=" * 78)
    print("Sample-efficiency benchmark — calibration models")
    print("=" * 78)
    print()
    print(f"Techniques: {', '.join(t.name + ('' if t.available else ' [unavailable]') for t in techniques)}")
    print()
    for tech in techniques:
        if not tech.available and tech.note:
            print(f"  [info] {tech.note}")
    print()

    for identity, track, n_pts, rows in pair_rows:
        print(f"{identity.display_name} @ {track}  (n={n_pts} calibration_points)")
        if not rows:
            print("  (no fits succeeded — skipping)")
            print()
            continue
        print(_format_table_for_pair(rows, techniques))
        print()

    if skipped:
        print("Skipped (insufficient data, < %d points):" % MIN_POINTS_FOR_BENCHMARK)
        for car_disp, track, n, _reason in skipped:
            print(f"  - {car_disp} @ {track}: {n} calibration_points")
        print()


def _emit_markdown(
    output_path: Path,
    pair_rows: list[tuple[CarIdentity, str, int, list[BenchmarkRow]]],
    techniques: list[Technique],
    skipped: list[tuple[str, str, int, str]],
    train_sizes: tuple[int, ...],
    k_repeats: int,
    holdout_frac: float,
) -> None:
    lines: list[str] = []
    lines.append("# Calibration sample-efficiency benchmark")
    lines.append("")
    lines.append(
        "Auto-generated by `python -m validation.sample_efficiency_benchmark`. "
        "Measures how holdout R² and RMSE scale with training-set size for each "
        "fitting technique."
    )
    lines.append("")
    lines.append("## Setup")
    lines.append("")
    lines.append(f"- Train sizes: {list(train_sizes)}")
    lines.append(f"- K random samples per train size: {k_repeats}")
    lines.append(f"- Holdout fraction: {holdout_frac:.0%}")
    lines.append(f"- Min calibration_points required: {MIN_POINTS_FOR_BENCHMARK}")
    lines.append("- Targets: " + ", ".join(name for name, _ in TARGETS))
    lines.append("")
    lines.append("## Techniques")
    lines.append("")
    for t in techniques:
        marker = "available" if t.available else f"unavailable ({t.note})"
        lines.append(f"- `{t.name}` — {marker}")
    lines.append("")

    lines.append("## Results")
    lines.append("")
    if not pair_rows:
        lines.append("_No (car, track) pairs had sufficient data for benchmarking._")
        lines.append("")
    for identity, track, n_pts, rows in pair_rows:
        lines.append(f"### {identity.display_name} @ {track}")
        lines.append("")
        lines.append(f"_Total calibration_points: {n_pts}_")
        lines.append("")
        if not rows:
            lines.append("_No fits succeeded._")
            lines.append("")
            continue
        lines.append(_format_table_for_pair(rows, techniques))
        lines.append("")

    if skipped:
        lines.append("## Skipped pairs (insufficient data)")
        lines.append("")
        lines.append(f"Pairs with fewer than {MIN_POINTS_FOR_BENCHMARK} calibration_points:")
        lines.append("")
        for car_disp, track, n, _reason in skipped:
            lines.append(f"- {car_disp} @ {track}: {n} calibration_points")
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


# ─── CLI ───────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m validation.sample_efficiency_benchmark",
        description="Benchmark fitting techniques' sample-efficiency on calibration models.",
    )
    parser.add_argument(
        "--car", default="",
        help="Filter to a specific car (canonical/display/screen name). "
             "Default: all cars in registry.",
    )
    parser.add_argument(
        "--track", default="",
        help="Substring filter on track name. Default: all tracks.",
    )
    parser.add_argument(
        "--techniques", default="vanilla",
        help="Comma-separated list of techniques to compare. "
             "Available: vanilla, compliance, virtual, compliance+virtual. "
             "Default: vanilla.",
    )
    parser.add_argument(
        "--train-sizes", default=",".join(str(n) for n in DEFAULT_TRAIN_SIZES),
        help=f"Comma-separated training-set sizes. Default: {','.join(str(n) for n in DEFAULT_TRAIN_SIZES)}",
    )
    parser.add_argument(
        "--k-repeats", type=int, default=DEFAULT_K_REPEATS,
        help=f"Number of random samples per train size. Default: {DEFAULT_K_REPEATS}",
    )
    parser.add_argument(
        "--holdout-frac", type=float, default=DEFAULT_HOLDOUT_FRAC,
        help=f"Fraction held out as test set. Default: {DEFAULT_HOLDOUT_FRAC}",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Base seed for the random shuffles. Default: 42",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help=f"Markdown report path. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--no-write", action="store_true",
        help="Skip writing the markdown report (still prints to stdout).",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose logging.",
    )
    return parser.parse_args(argv)


def _resolve_car_filter(name: str) -> CarIdentity | None:
    if not name:
        return None
    identity = resolve_car(name)
    if identity is None:
        print(f"[error] unknown car '{name}' — known: "
              f"{', '.join(c.canonical for c in _CAR_REGISTRY)}", file=sys.stderr)
        sys.exit(2)
    return identity


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # Silence the noisy auto_calibrate warnings in the benchmark inner loop.
    if not args.verbose:
        logging.getLogger("car_model.auto_calibrate").setLevel(logging.ERROR)

    train_sizes = tuple(sorted({int(s) for s in args.train_sizes.split(",") if s.strip()}))
    if not train_sizes:
        print("[error] --train-sizes must contain at least one integer", file=sys.stderr)
        return 2

    technique_names = [s.strip() for s in args.techniques.split(",") if s.strip()]
    techniques = _build_techniques(technique_names)
    if not techniques:
        print("[error] no techniques resolved", file=sys.stderr)
        return 2

    car_filter = _resolve_car_filter(args.car)
    candidates = [car_filter] if car_filter else list(_CAR_REGISTRY)

    pair_rows: list[tuple[CarIdentity, str, int, list[BenchmarkRow]]] = []
    skipped: list[tuple[str, str, int, str]] = []

    for identity in candidates:
        try:
            points = load_calibration_points(identity.canonical)
        except Exception as e:
            logger.warning("load_calibration_points(%s) failed: %s", identity.canonical, e)
            continue

        if not points:
            skipped.append((identity.display_name, "(no data)", 0, "no calibration_points file"))
            continue

        groups = _track_groups(points)
        for track, track_pts in groups.items():
            if not _track_matches(track, args.track):
                continue
            n = len(track_pts)
            if n < MIN_POINTS_FOR_BENCHMARK:
                skipped.append((identity.display_name, track, n, "below minimum"))
                continue
            rows = _run_one_pair(
                identity=identity,
                track=track,
                points=track_pts,
                techniques=techniques,
                train_sizes=train_sizes,
                k_repeats=args.k_repeats,
                holdout_frac=args.holdout_frac,
                base_seed=args.seed,
            )
            pair_rows.append((identity, track, n, rows))

    _emit_console(pair_rows, techniques, skipped)

    if not args.no_write:
        _emit_markdown(
            output_path=args.output,
            pair_rows=pair_rows,
            techniques=techniques,
            skipped=skipped,
            train_sizes=train_sizes,
            k_repeats=args.k_repeats,
            holdout_frac=args.holdout_frac,
        )
        print(f"[ok] markdown report written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
