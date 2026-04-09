"""Streamlined IBT-to-STO pipeline — zero required flags.

Auto-detects car, track, wing angle, and fuel from IBT headers.
Accumulates calibration data from every input IBT before solving.

Usage::

    # Simplest form — just IBT files:
    python -m pipeline.optimize session1.ibt session2.ibt --sto output.sto

    # With optional overrides:
    python -m pipeline.optimize *.ibt --sto out.sto --scenario race --verbose

Everything else (car, track, wing, fuel, calibration) is auto-detected.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from car_model.registry import (
    CarIdentity,
    TrackIdentity,
    resolve_car_from_ibt,
    resolve_track_from_ibt,
    supported_car_names,
)
from track_model.ibt_parser import IBTFile


class OptimizeError(RuntimeError):
    """User-facing error from the optimize pipeline."""


# ─── IBT validation ────────────────────────────────────────────────────────

def _validate_ibt_consistency(
    ibt_paths: list[str],
) -> tuple[CarIdentity, TrackIdentity, IBTFile]:
    """Verify all IBTs are from the same car and track.

    Returns the resolved (car, track, first_ibt) on success.
    Raises OptimizeError with a descriptive message on mismatch.
    """
    if not ibt_paths:
        raise OptimizeError("No IBT files provided.")

    first_ibt = IBTFile(ibt_paths[0])
    car = resolve_car_from_ibt(first_ibt)
    if car is None:
        raw = first_ibt.car_info().get("car", "Unknown")
        supported = ", ".join(supported_car_names())
        raise OptimizeError(
            f"Unknown car '{raw}' in {Path(ibt_paths[0]).name}. "
            f"Supported cars: {supported}"
        )

    track = resolve_track_from_ibt(first_ibt)

    # Check remaining IBTs for consistency
    if len(ibt_paths) > 1:
        car_counts: dict[str, int] = {car.display_name: 1}
        track_counts: dict[str, int] = {track.display_name: 1}

        for path in ibt_paths[1:]:
            ibt = IBTFile(path)
            c = resolve_car_from_ibt(ibt)
            t = resolve_track_from_ibt(ibt)
            c_name = c.display_name if c else ibt.car_info().get("car", "Unknown")
            t_name = t.display_name if t else "Unknown"
            car_counts[c_name] = car_counts.get(c_name, 0) + 1
            track_counts[t_name] = track_counts.get(t_name, 0) + 1

        if len(car_counts) > 1:
            breakdown = ", ".join(f"{n} ({c} files)" for n, c in car_counts.items())
            raise OptimizeError(
                f"All IBTs must be from the same car. Found: {breakdown}"
            )
        if len(track_counts) > 1:
            breakdown = ", ".join(f"{n} ({c} files)" for n, c in track_counts.items())
            raise OptimizeError(
                f"All IBTs must be from the same track. Found: {breakdown}"
            )

    return car, track, first_ibt


# ─── Calibration accumulation ──────────────────────────────────────────────

def _auto_calibrate_from_ibts(
    car_canonical: str,
    ibt_paths: list[str],
    *,
    verbose: bool = False,
) -> tuple[int, int]:
    """Extract calibration points from IBTs, merge with existing, refit.

    Returns ``(n_total_points, n_unique_setups)``.
    """
    from car_model.auto_calibrate import (
        extract_point_from_ibt,
        fit_models_from_points,
        load_calibration_points,
        save_calibrated_models,
        save_calibration_points,
    )

    existing = load_calibration_points(car_canonical)
    seen_ids = {pt.session_id for pt in existing}

    added = 0
    for path in ibt_paths:
        pt = extract_point_from_ibt(path, car_canonical)
        if pt is not None and pt.session_id not in seen_ids:
            existing.append(pt)
            seen_ids.add(pt.session_id)
            added += 1

    if added > 0:
        save_calibration_points(car_canonical, existing)
        if verbose:
            print(f"  [calibrate] Added {added} new calibration point(s) "
                  f"({len(existing)} total)")

    # Refit models if we have enough data
    n_unique = 0
    if len(existing) >= 5:
        models = fit_models_from_points(car_canonical, existing)
        n_unique = models.n_unique_setups
        save_calibrated_models(car_canonical, models)
        if verbose:
            print(f"  [calibrate] Models fitted from {n_unique} unique setups")
            # Print per-model R² if available
            for attr in ("front_ride_height", "rear_ride_height",
                         "heave_spring_defl_static"):
                model = getattr(models, attr, None)
                if model and model.r_squared is not None:
                    label = attr.replace("_", " ").title()
                    print(f"    {label}: R²={model.r_squared:.3f}")
    elif verbose:
        print(f"  [calibrate] {len(existing)} point(s) — need 5+ unique "
              f"setups for model fitting")

    return len(existing), n_unique


# ─── Produce args builder ──────────────────────────────────────────────────

def _build_produce_args(
    car: CarIdentity,
    ibt_paths: list[str],
    first_ibt: IBTFile,
    *,
    sto_path: str | None = None,
    json_path: str | None = None,
    scenario_profile: str | None = None,
    search_mode: str | None = None,
    verbose: bool = False,
    free: bool = False,
) -> argparse.Namespace:
    """Build an argparse.Namespace matching produce()'s expected interface."""
    from analyzer.setup_reader import CurrentSetup

    setup = CurrentSetup.from_ibt(first_ibt, car_canonical=car.canonical)

    args = argparse.Namespace(
        car=car.canonical,
        ibt=ibt_paths if len(ibt_paths) >= 1 else ibt_paths[0],
        wing=setup.wing_angle_deg or None,
        fuel=setup.fuel_l or None,
        lap=None,
        balance=None,
        tolerance=0.1,
        sto=sto_path,
        json=json_path,
        setup_json=None,
        track=None,
        report_only=False,
        no_learn=False,
        legacy_solver=False,
        min_lap_time=None,
        outlier_pct=0.115,
        stint=False,
        stint_threshold=1.5,
        stint_select="longest",
        stint_max_laps=40,
        verbose=verbose,
        explore_legal_space=free or (search_mode is not None),
        search_budget=1000,
        keep_weird=False,
        search_mode=search_mode,
        top_n=1,
        search_family=None,
        explore=False,
        scenario_profile=scenario_profile,
        objective_profile="balanced",
        learn=False,
        auto_learn=False,
        delta_card=False,
        mode="safe",
        free=free,
    )
    return args


# ─── Main orchestrator ─────────────────────────────────────────────────────

def optimize(
    ibt_paths: list[str],
    *,
    sto_path: str | None = None,
    json_path: str | None = None,
    scenario_profile: str | None = None,
    search_mode: str | None = None,
    no_calibrate: bool = False,
    verbose: bool = False,
    dry_run: bool = False,
    free: bool = False,
) -> None:
    """Run the full optimize pipeline: validate → calibrate → solve → output.

    Args:
        ibt_paths: One or more IBT file paths.
        sto_path: Output .sto file path.
        json_path: Output JSON summary path.
        scenario_profile: "single_lap_safe", "quali", "sprint", or "race".
        search_mode: "quick", "standard", "exhaustive", or "maximum".
        no_calibrate: Skip calibration accumulation step.
        verbose: Show detailed output.
        dry_run: Validate and calibrate only, don't solve.
        free: Enable legal-manifold search.
    """
    # ── Step 1: Validate IBTs ──
    print("Scanning IBT files...")
    for p in ibt_paths:
        if not Path(p).exists():
            raise OptimizeError(f"IBT file not found: {p}")

    car, track, first_ibt = _validate_ibt_consistency(ibt_paths)
    print(f"  Car:   {car.display_name}")
    print(f"  Track: {track.display_name} — {track.config}")
    print(f"  Files: {len(ibt_paths)} IBT(s)")

    # ── Step 2: Calibration accumulation ──
    if not no_calibrate:
        print("\nAccumulating calibration data...")
        n_total, n_unique = _auto_calibrate_from_ibts(
            car.canonical, ibt_paths, verbose=True,
        )
        # Show calibration gate status
        from car_model.cars import get_car
        from car_model.auto_calibrate import load_calibrated_models, apply_to_car
        from car_model.calibration_gate import CalibrationGate

        car_obj = get_car(car.canonical)
        cal_models = load_calibrated_models(car.canonical)
        if cal_models:
            apply_to_car(car_obj, cal_models)
        gate = CalibrationGate(car_obj, track.display_name)
        report = gate.full_report()
        if report.any_blocked:
            print(f"\n{report.format_header()}")
        else:
            solved = len(report.solved_steps)
            print(f"  Calibration gate: {solved}/6 steps ready")

    if dry_run:
        print("\n[dry-run] Stopping before solve.")
        return

    # ── Step 3: Delegate to existing pipeline ──
    print("\nRunning physics solver...")
    args = _build_produce_args(
        car, ibt_paths, first_ibt,
        sto_path=sto_path,
        json_path=json_path,
        scenario_profile=scenario_profile,
        search_mode=search_mode,
        verbose=verbose,
        free=free,
    )

    from pipeline.produce import produce
    produce(args)


# ─── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Streamlined IBT-to-STO pipeline. Auto-detects car, track, wing, "
            "and fuel from IBT headers. Accumulates calibration data "
            "automatically."
        ),
    )
    parser.add_argument(
        "ibt", nargs="+", metavar="IBT",
        help="IBT telemetry file(s). All must be from the same car and track.",
    )
    parser.add_argument(
        "--sto", type=str, default=None,
        help="Export iRacing .sto setup file",
    )
    parser.add_argument(
        "--json", type=str, default=None,
        help="Save full JSON summary to file",
    )
    parser.add_argument(
        "--scenario", type=str, default=None, dest="scenario_profile",
        choices=["single_lap_safe", "quali", "sprint", "race"],
        help="Scenario profile (default: single_lap_safe)",
    )
    parser.add_argument(
        "--search-mode", type=str, default=None, dest="search_mode",
        choices=["quick", "standard", "exhaustive", "maximum"],
        help="Legal-manifold search mode (implies --free)",
    )
    parser.add_argument(
        "--free", action="store_true",
        help="Search the full legal setup manifold",
    )
    parser.add_argument(
        "--no-calibrate", action="store_true", dest="no_calibrate",
        help="Skip automatic calibration accumulation",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show detailed output",
    )
    parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Validate and calibrate only — don't run the solver",
    )

    args = parser.parse_args()

    try:
        optimize(
            ibt_paths=args.ibt,
            sto_path=args.sto,
            json_path=args.json,
            scenario_profile=args.scenario_profile,
            search_mode=args.search_mode,
            no_calibrate=args.no_calibrate,
            verbose=args.verbose,
            dry_run=args.dry_run,
            free=args.free,
        )
    except OptimizeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
