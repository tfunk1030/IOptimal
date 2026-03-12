"""CLI entry point for the setup solver.

Usage:
    python -m solver.solve --car bmw --track sebring --wing 17
    python -m solver.solve --car bmw --track sebring --wing 17 --balance 50.14
    python -m solver.solve --car bmw --track sebring --wing 17 --fuel 12
    python -m solver.solve --car bmw --track sebring --wing 17 --json
    python -m solver.solve --car bmw --track sebring --wing 17 --save output/bmw_sebring.json
    python -m solver.solve --car bmw --track sebring --wing 17 --sto output/bmw_sebring.sto
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aero_model import load_car_surfaces
from car_model import get_car
from track_model.profile import TrackProfile
from solver.rake_solver import RakeSolver
from solver.heave_solver import HeaveSolver
from solver.corner_spring_solver import CornerSpringSolver
from solver.arb_solver import ARBSolver
from solver.wheel_geometry_solver import WheelGeometrySolver
from solver.damper_solver import DamperSolver
from output.report import print_full_setup_report, save_json_summary
from output.setup_writer import write_sto

TRACKS_DIR = Path(__file__).parent.parent / "data" / "tracks"


def find_track_profile(track_name: str) -> TrackProfile:
    """Find and load a track profile by partial name match.

    When multiple files match, prefers:
    1. Files ending in '_latest' (most recently generated profile)
    2. Among remaining matches, the most recently modified file
    """
    track_files = list(TRACKS_DIR.glob("*.json"))
    if not track_files:
        raise FileNotFoundError(f"No track profiles in {TRACKS_DIR}")

    # Collect all matching files
    matches = [f for f in track_files if track_name.lower() in f.stem.lower()]

    if not matches:
        available = [f.stem for f in track_files]
        raise FileNotFoundError(
            f"No track profile matching '{track_name}'. Available: {available}"
        )

    # Prefer '_latest' suffix, then most recently modified
    latest = [f for f in matches if f.stem.endswith("_latest")]
    if latest:
        return TrackProfile.load(latest[0])

    # Fall back to most recently modified
    matches.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return TrackProfile.load(matches[0])


def main():
    parser = argparse.ArgumentParser(
        description="GTP Setup Solver — Physics-based setup calculator"
    )
    parser.add_argument("--car", required=True, help="Car name (e.g., bmw)")
    parser.add_argument("--track", required=True, help="Track name (e.g., sebring)")
    parser.add_argument("--wing", required=True, type=float, help="Wing angle (degrees)")
    parser.add_argument("--balance", type=float, default=50.14,
                        help="Target DF balance %% (default: 50.14)")
    parser.add_argument("--tolerance", type=float, default=0.1,
                        help="Balance tolerance %% (default: 0.1)")
    parser.add_argument("--fuel", type=float, default=89.0,
                        help="Fuel load in liters (default: 89)")
    parser.add_argument("--free", action="store_true",
                        help="Free optimization (don't pin front RH at sim floor)")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON instead of human-readable")
    parser.add_argument("--save", type=str, default=None,
                        help="Save full JSON summary to file")
    parser.add_argument("--sto", type=str, default=None,
                        help="Export iRacing .sto setup file")
    parser.add_argument("--report-only", action="store_true",
                        help="Print only the garage setup sheet (skip per-step details)")

    args = parser.parse_args()

    # Load car model
    car = get_car(args.car)
    print(f"Car: {car.name}")

    # Load aero surfaces
    surfaces = load_car_surfaces(car.canonical_name)
    if args.wing not in surfaces:
        available = sorted(surfaces.keys())
        print(f"ERROR: Wing angle {args.wing}° not available. Available: {available}")
        sys.exit(1)
    surface = surfaces[args.wing]
    print(f"Aero surface: {surface}")

    # Load track profile
    track = find_track_profile(args.track)
    print(f"Track: {track.track_name} — {track.track_config}")
    print(f"Best lap: {track.best_lap_time_s:.3f}s")
    print()

    # ─── Step 1: Rake / Ride Heights ─────────────────────────────────
    print("=" * 60)
    print("Running Step 1: Rake / Ride Heights...")
    print(f"  Target DF balance: {args.balance:.2f}% ± {args.tolerance:.2f}%")
    print(f"  Fuel load: {args.fuel:.0f} L")
    print()

    rake_solver = RakeSolver(car, surface, track)
    step1 = rake_solver.solve(
        target_balance=args.balance,
        balance_tolerance=args.tolerance,
        fuel_load_l=args.fuel,
        pin_front_min=not args.free,
    )

    if not args.json and not args.report_only:
        print(step1.summary())

    # ─── Step 2: Heave / Third Springs ─────────────────────────────────
    print()
    print("Running Step 2: Heave / Third Springs...")
    print()

    heave_solver = HeaveSolver(car, track)
    step2 = heave_solver.solve(
        dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
        dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
    )

    if not args.json and not args.report_only:
        print(step2.summary())

    # ─── Step 3: Corner Springs ────────────────────────────────────────
    print()
    print("Running Step 3: Corner Springs...")
    print()

    corner_solver = CornerSpringSolver(car, track)
    step3 = corner_solver.solve(
        front_heave_nmm=step2.front_heave_nmm,
        rear_third_nmm=step2.rear_third_nmm,
        fuel_load_l=args.fuel,
    )

    if not args.json and not args.report_only:
        print(step3.summary())

    # ─── Step 4: Anti-Roll Bars ────────────────────────────────────────
    print()
    print("Running Step 4: Anti-Roll Bars...")
    print()

    arb_solver = ARBSolver(car, track)
    step4 = arb_solver.solve(
        front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
        rear_wheel_rate_nmm=step3.rear_spring_rate_nmm,
    )

    if not args.json and not args.report_only:
        print(step4.summary())

    # ─── Step 5: Wheel Geometry ────────────────────────────────────────
    print()
    print("Running Step 5: Wheel Geometry...")
    print()

    geom_solver = WheelGeometrySolver(car, track)
    step5 = geom_solver.solve(
        k_roll_total_nm_deg=step4.k_roll_front_total + step4.k_roll_rear_total,
        front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
        rear_wheel_rate_nmm=step3.rear_spring_rate_nmm,
    )

    if not args.json and not args.report_only:
        print(step5.summary())

    # ─── Step 6: Dampers ──────────────────────────────────────────────
    print()
    print("Running Step 6: Dampers...")
    print()

    damper_solver = DamperSolver(car, track)
    step6 = damper_solver.solve(
        front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
        rear_wheel_rate_nmm=step3.rear_spring_rate_nmm,
        front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
        rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
        fuel_load_l=args.fuel,
    )

    if not args.json and not args.report_only:
        print(step6.summary())

    # ─── Full Setup Report ─────────────────────────────────────────────
    print()
    print()
    report = print_full_setup_report(
        car_name=car.name,
        track_name=f"{track.track_name} — {track.track_config}",
        wing=args.wing,
        target_balance=args.balance,
        step1=step1,
        step2=step2,
        step3=step3,
        step4=step4,
        step5=step5,
        step6=step6,
    )
    print(report)

    # ─── JSON / Save ──────────────────────────────────────────────────
    if args.save:
        save_json_summary(
            car_name=car.name,
            track_name=f"{track.track_name} — {track.track_config}",
            wing=args.wing,
            step1=step1, step2=step2, step3=step3,
            step4=step4, step5=step5, step6=step6,
            output_path=args.save,
        )
        print(f"\nJSON summary saved to: {args.save}")

    if args.sto:
        sto_path = write_sto(
            car_name=car.name,
            track_name=f"{track.track_name} — {track.track_config}",
            wing=args.wing,
            fuel_l=args.fuel,
            step1=step1, step2=step2, step3=step3,
            step4=step4, step5=step5, step6=step6,
            output_path=args.sto,
        )
        print(f"\niRacing .sto setup saved to: {sto_path}")

    if args.json:
        import dataclasses
        output = {
            "step1_rake": dataclasses.asdict(step1),
            "step2_heave": dataclasses.asdict(step2),
            "step3_corner": dataclasses.asdict(step3),
            "step4_arb": dataclasses.asdict(step4),
            "step5_geometry": dataclasses.asdict(step5),
            "step6_dampers": dataclasses.asdict(step6),
        }
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
