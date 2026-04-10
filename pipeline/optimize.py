"""Streamlined IBT-to-STO pipeline — zero required flags.

Auto-detects car, track, wing angle, and fuel from IBT headers.
Accumulates calibration data from every input IBT before solving.

Usage::

    # Simplest form — just IBT files (auto-names output .sto):
    python -m pipeline.optimize session1.ibt session2.ibt session3.ibt

    # With explicit output path and scenario:
    python -m pipeline.optimize *.ibt --sto output.sto --scenario race

    # Check calibration status without running solver:
    python -m pipeline.optimize --status --car porsche

Everything else (car, track, wing, fuel, calibration) is auto-detected.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from car_model.registry import (
    CarIdentity,
    TrackIdentity,
    resolve_car,
    resolve_car_from_ibt,
    resolve_track_from_ibt,
    supported_car_names,
    track_slug,
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


# ─── Best-lap selection ───────────────────────────────────────────────────

def _select_best_ibt(ibt_paths: list[str]) -> tuple[str, float]:
    """Scan all IBTs and return the path with the fastest valid lap.

    Returns ``(best_path, best_lap_time_s)``.  Falls back to the first
    path if no IBT contains a valid lap.
    """
    best_path, best_time = ibt_paths[0], float("inf")
    for path in ibt_paths:
        try:
            ibt = IBTFile(path)
            rng = ibt.best_lap_indices(min_time=60.0)
            if rng is None:
                continue
            _start, end = rng
            lap_times = ibt.channel("LapCurrentLapTime")
            t = float(lap_times[end])
            if 0 < t < best_time:
                best_path, best_time = path, t
        except Exception:
            continue
    return best_path, best_time


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


# ─── Calibration progress reporting ──────────────────────────────────────

def _format_calibration_progress(
    car_canonical: str,
    n_total: int,
    n_unique: int,
    gate_report,
) -> str:
    """Build a human-readable calibration progress summary.

    Includes data status, model quality, solver readiness, and
    actionable next-steps when calibration is incomplete.
    """
    from car_model.auto_calibrate import (
        _setup_key,
        load_calibrated_models,
        load_calibration_points,
    )

    lines: list[str] = []

    # ── Section 1: Data collection status ──
    points = load_calibration_points(car_canonical)
    unique_keys = set()
    for pt in points:
        try:
            unique_keys.add(_setup_key(pt))
        except Exception:
            pass
    n_unique_actual = len(unique_keys) if unique_keys else n_unique

    if n_unique_actual >= 5:
        lines.append(f"  Data: {n_total} sessions, {n_unique_actual} unique setups")
    else:
        need = max(0, 5 - n_unique_actual)
        lines.append(
            f"  Data: {n_total} sessions, {n_unique_actual} unique setups "
            f"(need {need} more for model fitting)"
        )
        # Suggest what to vary
        if points:
            _suggest_variation(points, lines)

    # ── Section 2: Model quality ──
    models = load_calibrated_models(car_canonical)
    if models and n_unique_actual >= 5:
        r2_parts = []
        for attr, label in [
            ("front_ride_height", "RH front"),
            ("rear_ride_height", "RH rear"),
            ("heave_spring_defl_static", "Defl"),
        ]:
            m = getattr(models, attr, None)
            if m and m.r_squared is not None:
                r2_parts.append(f"{label} R\u00b2={m.r_squared:.3f}")
        if r2_parts:
            lines.append(f"  Models: {' | '.join(r2_parts)}")

    # ── Section 3: Solver readiness ──
    solved = len(gate_report.solved_steps)
    blocked_nums = gate_report.blocked_steps if hasattr(gate_report, "blocked_steps") else []
    weak_nums = gate_report.weak_steps if hasattr(gate_report, "weak_steps") else []

    if not blocked_nums:
        suffix = ""
        if weak_nums:
            suffix = f" ({len(weak_nums)} with warnings)"
        lines.append(f"  Solver: {solved}/6 steps ready{suffix}")
    else:
        lines.append(f"  Solver: {solved}/6 steps ready, {len(blocked_nums)} blocked")
        # Extract step details from step_reports
        for r in gate_report.step_reports:
            if r.blocked:
                if r.blocked_by_step:
                    lines.append(f"    Step {r.step_number} ({r.step_name}): blocked by Step {r.blocked_by_step}")
                elif r.missing:
                    missing_names = ", ".join(s.name for s in r.missing)
                    lines.append(f"    Step {r.step_number} ({r.step_name}): needs {missing_names}")
                else:
                    lines.append(f"    Step {r.step_number} ({r.step_name}): blocked")

    return "\n".join(lines)


def _suggest_variation(points, lines: list[str]) -> None:
    """Analyze which parameters have been varied and suggest the next one."""
    if len(points) < 2:
        lines.append(
            "    Tip: Run sessions with different front heave spring values "
            "(e.g., 100, 200, 300 N/mm)"
        )
        return

    # Check which setup parameters have been varied
    varied = set()
    first = points[0]
    for pt in points[1:]:
        if abs(pt.front_heave_setting - first.front_heave_setting) > 1:
            varied.add("front_heave")
        if abs(pt.rear_third_setting - first.rear_third_setting) > 1:
            varied.add("rear_third")
        if abs(pt.front_pushrod_mm - first.front_pushrod_mm) > 0.5:
            varied.add("pushrod")
        if abs(pt.front_heave_perch_mm - first.front_heave_perch_mm) > 0.5:
            varied.add("perch")
        if abs(pt.front_camber_deg - first.front_camber_deg) > 0.1:
            varied.add("camber")

    # Suggest the most impactful unvaried parameter
    priority = [
        ("front_heave", "front heave spring (e.g., try 3 different values)"),
        ("rear_third", "rear third spring"),
        ("pushrod", "front/rear pushrod offset"),
        ("perch", "heave/third perch offset"),
        ("camber", "front camber"),
    ]
    for key, desc in priority:
        if key not in varied:
            lines.append(f"    Tip: Vary {desc} between sessions to improve calibration")
            return

    lines.append("    Tip: Run more sessions with different spring combinations")


# ─── Auto-name output ─────────────────────────────────────────────────────

def _auto_sto_path(car: CarIdentity, track: TrackIdentity) -> str:
    """Generate an auto-named .sto path that doesn't overwrite existing files."""
    slug = track_slug(track.display_name, track.config)
    base = f"{car.canonical}_{slug}"
    candidate = Path(f"{base}.sto")
    if not candidate.exists():
        return str(candidate)
    # Add numeric suffix
    for i in range(2, 100):
        candidate = Path(f"{base}_{i}.sto")
        if not candidate.exists():
            return str(candidate)
    return str(Path(f"{base}_new.sto"))


# ─── Produce args builder ──────────────────────────────────────────────────

def _build_produce_args(
    car: CarIdentity,
    ibt_paths: list[str],
    best_ibt: IBTFile,
    *,
    sto_path: str | None = None,
    json_path: str | None = None,
    scenario_profile: str | None = None,
    search_mode: str | None = None,
    verbose: bool = False,
    free: bool = False,
    opt_mode: str = "driver",
    lap: int | None = None,
    balance: float | None = None,
    learn: bool = False,
    auto_learn: bool = False,
    delta_card: bool = False,
    mode: str = "safe",
    fresh_profile: bool = False,
    force: bool = False,
) -> argparse.Namespace:
    """Build an argparse.Namespace matching produce()'s expected interface."""
    from analyzer.setup_reader import CurrentSetup

    setup = CurrentSetup.from_ibt(best_ibt, car_canonical=car.canonical)

    args = argparse.Namespace(
        car=car.canonical,
        ibt=ibt_paths,
        wing=setup.wing_angle_deg or None,
        fuel=setup.fuel_l or None,
        lap=lap,
        balance=balance,
        tolerance=0.1,
        sto=sto_path,
        json=json_path,
        setup_json=None,
        track=None,
        report_only=False,
        no_learn=not learn and not auto_learn,
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
        learn=learn,
        auto_learn=auto_learn,
        delta_card=delta_card,
        mode=mode,
        free=free,
        opt_mode=opt_mode,
        fresh_profile=fresh_profile,
        force=force,
    )
    return args


# ─── Summary report ──────────────────────────────────────────────────────

def _print_summary(
    car: CarIdentity,
    track: TrackIdentity,
    result: dict | None,
    best_lap_time: float,
    best_ibt_name: str,
    n_ibts: int,
    n_unique: int,
    sto_path: str | None,
    scenario_profile: str | None,
) -> None:
    """Print a compact summary after the solver completes."""
    title = f"{car.display_name} @ {track.display_name}"
    if track.config:
        title += f" — {track.config}"
    bar = "\u2550" * 60

    print(f"\n{bar}")
    print(f"  {title}")
    print(bar)

    # Sessions
    if best_lap_time < float("inf"):
        print(f"  Sessions:     {n_ibts} IBT(s), best lap {best_lap_time:.2f}s ({best_ibt_name})")
    else:
        print(f"  Sessions:     {n_ibts} IBT(s)")

    # Calibration
    if n_unique > 0:
        cal_str = f"{n_unique} unique setups"
        if result:
            # Try to extract R² from the result
            try:
                from car_model.auto_calibrate import load_calibrated_models
                models = load_calibrated_models(car.canonical)
                if models:
                    r2_parts = []
                    fr = getattr(models, "front_ride_height", None)
                    rr = getattr(models, "rear_ride_height", None)
                    if fr and fr.r_squared is not None:
                        r2_parts.append(f"RH R\u00b2={fr.r_squared:.2f}/{rr.r_squared:.2f}" if rr and rr.r_squared else f"RH R\u00b2={fr.r_squared:.2f}")
                    if r2_parts:
                        cal_str += f", {r2_parts[0]}"
            except Exception:
                pass
        print(f"  Calibration:  {cal_str}")

    # Solver steps
    if result:
        steps_solved = sum(
            1 for k in ("step1", "step2", "step3", "step4", "step5", "step6")
            if result.get(k) is not None
        )
        scenario = result.get("scenario_profile", scenario_profile or "single_lap_safe")
        print(f"  Solver:       {steps_solved}/6 steps solved ({scenario})")

        # Driver profile
        driver = result.get("driver")
        if driver:
            try:
                traits = []
                if hasattr(driver, "trail_brake_classification"):
                    traits.append(f"{driver.trail_brake_classification} trail-brake")
                if hasattr(driver, "steering_smoothness"):
                    sm = driver.steering_smoothness
                    if sm and sm > 0.7:
                        traits.append("smooth steering")
                    elif sm and sm < 0.3:
                        traits.append("aggressive steering")
                if traits:
                    print(f"  Driver:       {', '.join(traits)}")
            except Exception:
                pass

    # Output
    if sto_path:
        print(f"  Output:       {sto_path}")

    print(bar)


# ─── Status command ───────────────────────────────────────────────────────

def _run_status(car_name: str) -> None:
    """Print calibration status and cached track profiles for a car."""
    from car_model.auto_calibrate import load_calibrated_models, load_calibration_points
    from car_model.cars import get_car
    from car_model.calibration_gate import CalibrationGate

    identity = resolve_car(car_name)
    if identity is None:
        supported = ", ".join(supported_car_names())
        print(f"ERROR: Unknown car '{car_name}'. Supported: {supported}",
              file=sys.stderr)
        raise SystemExit(1)

    canonical = identity.canonical
    print(f"\n{identity.display_name} Calibration Status")
    print("-" * 50)

    # Load calibration points
    points = load_calibration_points(canonical)
    models = load_calibrated_models(canonical)

    from car_model.auto_calibrate import _setup_key
    unique_keys = set()
    for pt in points:
        try:
            unique_keys.add(_setup_key(pt))
        except Exception:
            pass
    n_unique = len(unique_keys)

    print(f"  Sessions: {len(points)} total, {n_unique} unique setups")

    # Model quality
    if models and n_unique >= 5:
        r2_parts = []
        for attr, label in [
            ("front_ride_height", "RH front"),
            ("rear_ride_height", "RH rear"),
            ("heave_spring_defl_static", "Defl"),
        ]:
            m = getattr(models, attr, None)
            if m and m.r_squared is not None:
                r2_parts.append(f"{label} R\u00b2={m.r_squared:.3f}")
        if r2_parts:
            print(f"  Models:   {', '.join(r2_parts)}")
        else:
            print("  Models:   not yet fitted")
    elif n_unique < 5:
        print(f"  Models:   need {5 - n_unique} more unique setups for fitting")
    else:
        print("  Models:   not yet fitted")

    # Calibration gate
    try:
        car_obj = get_car(canonical)
        if models:
            from car_model.auto_calibrate import apply_to_car
            apply_to_car(car_obj, models)
        gate = CalibrationGate(car_obj, "status_check")
        report = gate.full_report()
        solved = len(report.solved_steps)
        print(f"  Gate:     {solved}/6 steps ready")
        for r in report.step_reports:
            if r.blocked:
                if r.blocked_by_step:
                    print(f"            Step {r.step_number} ({r.step_name}): blocked by Step {r.blocked_by_step}")
                elif r.missing:
                    missing_names = ", ".join(s.name for s in r.missing)
                    print(f"            Step {r.step_number} ({r.step_name}): needs {missing_names}")
                else:
                    print(f"            Step {r.step_number} ({r.step_name}): blocked")
    except Exception as e:
        print(f"  Gate:     error — {e}")

    # Cached track profiles
    tracks_dir = Path("data/tracks")
    if tracks_dir.exists():
        profiles = sorted(tracks_dir.glob("*.json"))
        if profiles:
            print(f"\nCached Track Profiles:")
            for p in profiles:
                try:
                    data = json.loads(p.read_text())
                    lap = data.get("best_lap_time_s", 0)
                    name = data.get("track_name", p.stem)
                    if lap > 0:
                        print(f"  {p.name:<40s} {lap:.2f}s ({name})")
                    else:
                        print(f"  {p.name:<40s} (no valid laps)")
                except Exception:
                    print(f"  {p.name:<40s} (unreadable)")

    print()


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
    no_sto: bool = False,
    opt_mode: str = "driver",
    lap: int | None = None,
    balance: float | None = None,
    learn: bool = False,
    auto_learn: bool = False,
    delta_card: bool = False,
    mode: str = "safe",
    fresh_profile: bool = False,
    force: bool = False,
) -> None:
    """Run the full optimize pipeline: validate → calibrate → solve → output.

    Args:
        ibt_paths: One or more IBT file paths.
        sto_path: Output .sto file path (auto-named if None and no_sto=False).
        json_path: Output JSON summary path.
        scenario_profile: "single_lap_safe", "quali", "sprint", or "race".
        search_mode: "quick", "standard", "exhaustive", or "maximum".
        no_calibrate: Skip calibration accumulation step.
        verbose: Show detailed output.
        dry_run: Validate and calibrate only, don't solve.
        free: Enable legal-manifold search.
        no_sto: Suppress automatic .sto generation.
        opt_mode: "driver" (anchor to loaded setup) or "physics" (pure physics).
        lap: Force specific lap number for analysis.
        balance: Override DF balance target %.
        learn: Store learner data from this run.
        auto_learn: Enable auto-learning feedback loop.
        delta_card: Output delta card (changes vs current).
        mode: "safe" or "aggressive" output mode.
        fresh_profile: Force rebuild of track profile even if cached.
        force: Bypass calibration gate — output all steps even if uncalibrated.
    """
    # ── Step 1: Validate IBTs ──
    print("Scanning IBT files...")
    for p in ibt_paths:
        if not Path(p).exists():
            raise OptimizeError(f"IBT file not found: {p}")

    car, track, _first_ibt = _validate_ibt_consistency(ibt_paths)
    print(f"  Car:   {car.display_name}")
    print(f"  Track: {track.display_name} — {track.config}")
    print(f"  Files: {len(ibt_paths)} IBT(s)")

    # ── Step 1.5: Select best-lap IBT ──
    best_ibt_path, best_lap_time = _select_best_ibt(ibt_paths)
    best_ibt_name = Path(best_ibt_path).name
    best_ibt = IBTFile(best_ibt_path)

    if len(ibt_paths) > 1:
        if best_lap_time < float("inf"):
            print(f"  Best lap: {best_lap_time:.2f}s ({best_ibt_name})")
        # Reorder so best-lap IBT is first (produce() uses first for analysis)
        reordered = [best_ibt_path] + [p for p in ibt_paths if p != best_ibt_path]
        ibt_paths = reordered

    # ── Step 2: Calibration accumulation ──
    n_total, n_unique = 0, 0
    if not no_calibrate:
        print("\nAccumulating calibration data...")
        n_total, n_unique = _auto_calibrate_from_ibts(
            car.canonical, ibt_paths, verbose=True,
        )

    # Show calibration gate + progress
    from car_model.cars import get_car
    from car_model.auto_calibrate import load_calibrated_models, apply_to_car
    from car_model.calibration_gate import CalibrationGate

    car_obj = get_car(car.canonical)
    cal_models = load_calibrated_models(car.canonical)
    if cal_models:
        apply_to_car(car_obj, cal_models)
    gate = CalibrationGate(car_obj, track.display_name)
    report = gate.full_report()

    progress = _format_calibration_progress(car.canonical, n_total, n_unique, report)
    print(f"\n{progress}")

    if dry_run:
        print("\n[dry-run] Stopping before solve.")
        return

    # ── Step 2.5: Auto-name .sto output ──
    if not sto_path and not no_sto:
        sto_path = _auto_sto_path(car, track)
        print(f"\n  Auto-named output: {sto_path}")

    # ── Step 3: Delegate to existing pipeline ──
    print("\nRunning physics solver...")
    args = _build_produce_args(
        car, ibt_paths, best_ibt,
        sto_path=sto_path,
        json_path=json_path,
        scenario_profile=scenario_profile,
        search_mode=search_mode,
        verbose=verbose,
        free=free,
        opt_mode=opt_mode,
        lap=lap,
        balance=balance,
        learn=learn,
        auto_learn=auto_learn,
        delta_card=delta_card,
        mode=mode,
        fresh_profile=fresh_profile,
        force=force,
    )

    from pipeline.produce import produce
    result = produce(args, _return_result=True)

    # ── Step 4: Summary ──
    _print_summary(
        car=car,
        track=track,
        result=result,
        best_lap_time=best_lap_time,
        best_ibt_name=best_ibt_name,
        n_ibts=len(ibt_paths),
        n_unique=n_unique,
        sto_path=sto_path,
        scenario_profile=scenario_profile,
    )


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
        "ibt", nargs="*", metavar="IBT",
        help="IBT telemetry file(s). All must be from the same car and track.",
    )
    parser.add_argument(
        "--sto", type=str, default=None,
        help="Export iRacing .sto setup file (auto-named if omitted)",
    )
    parser.add_argument(
        "--no-sto", action="store_true", dest="no_sto",
        help="Suppress automatic .sto generation",
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

    # ── Advanced options ──
    parser.add_argument(
        "--opt-mode", type=str, default="driver", dest="opt_mode",
        choices=["driver", "physics"],
        help="Optimization mode: 'driver' anchors to loaded setup, "
             "'physics' uses pure physics (default: driver)",
    )
    parser.add_argument(
        "--lap", type=int, default=None,
        help="Force specific lap number for analysis",
    )
    parser.add_argument(
        "--balance", type=float, default=None,
        help="Override DF balance target %%",
    )
    parser.add_argument(
        "--learn", action="store_true",
        help="Store learner data from this run",
    )
    parser.add_argument(
        "--auto-learn", action="store_true", dest="auto_learn",
        help="Enable auto-learning feedback loop",
    )
    parser.add_argument(
        "--delta-card", action="store_true", dest="delta_card",
        help="Output delta card (changes vs current setup)",
    )
    parser.add_argument(
        "--mode", type=str, default="safe", choices=["safe", "aggressive"],
        help="Output mode (default: safe)",
    )
    parser.add_argument(
        "--fresh-profile", action="store_true", dest="fresh_profile",
        help="Force rebuild of track profile even if cached",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Bypass calibration gate — output all solver steps even if "
             "uncalibrated. Values are ESTIMATES only.",
    )

    # ── Status mode ──
    parser.add_argument(
        "--status", action="store_true",
        help="Show calibration status and cached profiles (no IBTs needed)",
    )
    parser.add_argument(
        "--car", type=str, default=None,
        help="Car name for --status mode (e.g., 'porsche', 'bmw')",
    )

    args = parser.parse_args()

    # Handle --status mode
    if args.status:
        if not args.car:
            print("ERROR: --status requires --car (e.g., --car porsche)",
                  file=sys.stderr)
            raise SystemExit(1)
        try:
            _run_status(args.car)
        except OptimizeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        return

    # Normal mode requires IBT files
    if not args.ibt:
        parser.error("IBT files are required (or use --status --car <name>)")

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
            no_sto=args.no_sto,
            opt_mode=args.opt_mode,
            lap=args.lap,
            balance=args.balance,
            learn=args.learn,
            auto_learn=args.auto_learn,
            delta_card=args.delta_card,
            mode=args.mode,
            fresh_profile=args.fresh_profile,
            force=args.force,
        )
    except OptimizeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
