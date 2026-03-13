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
from solver.rake_solver import RakeSolver, reconcile_ride_heights
from solver.heave_solver import HeaveSolver
from solver.corner_spring_solver import CornerSpringSolver
from solver.arb_solver import ARBSolver
from solver.wheel_geometry_solver import WheelGeometrySolver
from solver.damper_solver import DamperSolver
from output.report import print_full_setup_report, save_json_summary
from output.setup_writer import write_sto
from solver.supporting_solver import compute_brake_bias
from solver.learned_corrections import apply_learned_corrections

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
    parser.add_argument("--learn", action="store_true",
                        help="Apply empirical corrections from accumulated session data")
    parser.add_argument("--space", action="store_true",
                        help="Run setup space exploration (feasible region and flat-bottom analysis)")
    parser.add_argument("--stint-laps", type=int, default=30,
                        help="Stint length for stint analysis (default: 30)")

    args = parser.parse_args()

    # Load car model
    car = get_car(args.car)
    if not args.report_only:
        print(f"Car: {car.name}")

    # Apply learned corrections if requested
    learned = None
    if args.learn:
        learned = apply_learned_corrections(
            car.canonical_name, args.track, min_sessions=2, verbose=True
        )
        if learned.applied:
            # Override calibration constants with empirical values
            if learned.heave_m_eff_front_kg is not None:
                car.heave_spring.front_m_eff_kg = learned.heave_m_eff_front_kg
            if learned.heave_m_eff_rear_kg is not None:
                car.heave_spring.rear_m_eff_kg = learned.heave_m_eff_rear_kg
            if learned.aero_compression_front_mm is not None:
                car.aero_compression.front_compression_mm = learned.aero_compression_front_mm
            if learned.aero_compression_rear_mm is not None:
                car.aero_compression.rear_compression_mm = learned.aero_compression_rear_mm
            if learned.calibrated_front_roll_gain is not None:
                car.geometry.front_roll_gain = learned.calibrated_front_roll_gain
            if learned.calibrated_rear_roll_gain is not None:
                car.geometry.rear_roll_gain = learned.calibrated_rear_roll_gain
            print()

    # Confidence check — warn if car model has ESTIMATE parameters
    confidence = car.estimate_confidence()
    estimate_params = [p for p, v in confidence.items() if "ESTIMATE" in v]
    if estimate_params:
        params_str = ", ".join(estimate_params)
        print(f"[confidence] {car.name}: {params_str} — outputs less reliable")
        print(f"  (Run pipeline with real IBT to calibrate these values)")
        print()

    # Load aero surfaces
    surfaces = load_car_surfaces(car.canonical_name)
    if args.wing not in surfaces:
        available = sorted(surfaces.keys())
        print(f"ERROR: Wing angle {args.wing}° not available. Available: {available}")
        sys.exit(1)
    surface = surfaces[args.wing]
    if not args.report_only:
        print(f"Aero surface: {surface}")

    # Load track profile
    track = find_track_profile(args.track)
    if not args.report_only:
        print(f"Track: {track.track_name} — {track.track_config}")
        print(f"Best lap: {track.best_lap_time_s:.3f}s")
        print()

    # ─── Step 1: Rake / Ride Heights ─────────────────────────────────
    if not args.report_only:
        print("=" * 60)
        if not args.report_only: print("Running Step 1: Rake / Ride Heights...")
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
    if not args.report_only: print("Running Step 2: Heave / Third Springs...")
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
    if not args.report_only: print("Running Step 3: Corner Springs...")
    print()

    corner_solver = CornerSpringSolver(car, track)
    step3 = corner_solver.solve(
        front_heave_nmm=step2.front_heave_nmm,
        rear_third_nmm=step2.rear_third_nmm,
        fuel_load_l=args.fuel,
    )

    if not args.json and not args.report_only:
        print(step3.summary())

    # Convert rear spring rate to wheel rate (MR^2) for downstream solvers
    rear_wheel_rate_nmm = step3.rear_spring_rate_nmm * car.corner_spring.rear_motion_ratio ** 2

    # ─── RH Reconciliation (after step2+step3 provide actual spring values) ──
    reconcile_ride_heights(
        car, step1, step2, step3,
        verbose=not args.json and not args.report_only,
    )
    if not args.json and not args.report_only:
        print()

    # ─── Step 4: Anti-Roll Bars ────────────────────────────────────────
    print()
    if not args.report_only: print("Running Step 4: Anti-Roll Bars...")
    print()

    arb_solver = ARBSolver(car, track)
    step4 = arb_solver.solve(
        front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
        rear_wheel_rate_nmm=rear_wheel_rate_nmm,
    )

    if not args.json and not args.report_only:
        print(step4.summary())

    # ─── Step 5: Wheel Geometry ────────────────────────────────────────
    print()
    if not args.report_only: print("Running Step 5: Wheel Geometry...")
    print()

    geom_solver = WheelGeometrySolver(car, track)
    _camber_conf = ("calibrated"
                    if learned and learned.calibrated_front_roll_gain is not None
                    else "estimated")
    step5 = geom_solver.solve(
        k_roll_total_nm_deg=step4.k_roll_front_total + step4.k_roll_rear_total,
        front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
        rear_wheel_rate_nmm=rear_wheel_rate_nmm,
        fuel_load_l=args.fuel,
        camber_confidence=_camber_conf,
    )

    if not args.json and not args.report_only:
        print(step5.summary())

    # ─── Step 6: Dampers ──────────────────────────────────────────────
    print()
    if not args.report_only: print("Running Step 6: Dampers...")
    print()

    damper_solver = DamperSolver(car, track)
    step6 = damper_solver.solve(
        front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
        rear_wheel_rate_nmm=rear_wheel_rate_nmm,
        front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
        rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
        fuel_load_l=args.fuel,
    )

    if not args.json and not args.report_only:
        print(step6.summary())

    # ─── Constraint proximity analysis (binding constraints) ──────────
    try:
        from solver.sensitivity import build_sensitivity_report
        sensitivity_report = build_sensitivity_report(
            step1=step1,
            step2=step2,
            arb_lltd=step4.lltd_achieved,
            arb_lltd_target=step4.lltd_target,
            rarb_sensitivity=step4.rarb_sensitivity_per_blade,
        )
        binding = sensitivity_report.binding_constraints()
        if binding and not args.report_only:
            print()
            print("[constraints] Near-binding constraints:")
            for c in binding:
                print(f"  !! {c.name}: {c.actual_value:.1f} / {c.limit_value:.1f} {c.units} "
                      f"(slack {c.slack_pct:+.1f}%)")
                if c.binding_explanation:
                    print(f"     → {c.binding_explanation}")
    except Exception:
        pass  # constraint analysis is advisory

    # ─── Extra analyses (stint, sector, sensitivity, space) ───────────
    stint_result = None
    sector_result = None
    sensitivity_result = None
    space_result = None

    try:
        from solver.stint_model import analyze_stint
        stint_result = analyze_stint(
            car=car,
            stint_laps=args.stint_laps,
            base_heave_nmm=step2.front_heave_nmm,
            base_third_nmm=step2.rear_third_nmm,
            v_p99_front_mps=track.shock_vel_p99_front_mps,
            v_p99_rear_mps=track.shock_vel_p99_rear_mps,
        )
    except Exception as e:
        if not args.report_only:
            print(f"[stint] Skipped: {e}")

    try:
        from solver.sector_compromise import SectorCompromise
        from solver.supporting_solver import compute_brake_bias as _cbias_sec
        _bias_sec, _ = _cbias_sec(car, fuel_load_l=args.fuel)
        sector_result = SectorCompromise(track).analyze(
            step1=step1, step2=step2, step4=step4,
            base_bias_pct=_bias_sec,
        )
    except Exception as e:
        if not args.report_only:
            print(f"[sector] Skipped: {e}")

    try:
        from solver.laptime_sensitivity import compute_laptime_sensitivity
        from solver.supporting_solver import compute_brake_bias as _cbias
        _bias, _ = _cbias(car, fuel_load_l=args.fuel)
        sensitivity_result = compute_laptime_sensitivity(
            track=track,
            step1=step1, step2=step2, step3=step3,
            step4=step4, step5=step5,
            brake_bias_pct=_bias,
        )
    except Exception as e:
        if not args.report_only:
            print(f"[sensitivity] Skipped: {e}")

    if args.space:
        try:
            from solver.setup_space import explore_setup_space
            space_result = explore_setup_space(
                track=track,
                step1=step1, step2=step2, step3=step3, step4=step4,
                sensitivity=sensitivity_result,
            )
            if not args.report_only and not args.json:
                print()
                print(space_result.summary())
        except Exception as e:
            print(f"[space] Skipped: {e}")

    # ─── Step 6b: Differential (standalone defaults) ──────────────────
    diff_result = None
    try:
        from solver.diff_solver import DiffSolver
        diff_result = DiffSolver.solve_defaults(car, track=track)
        if not args.report_only and not args.json:
            print()
            print(diff_result.summary())
    except Exception as e:
        if not args.report_only:
            print(f"[diff] Skipped: {e}")

    # ─── Full Setup Report ─────────────────────────────────────────────
    print()
    print()
    # Compute supporting params for standalone report (brake bias, diff defaults)
    _supporting = None
    try:
        from solver.supporting_solver import compute_brake_bias
        from solver.diff_solver import DiffSolver
        from analyzer.driver_style import DriverProfile
        from analyzer.extract import MeasuredState

        _bias, _ = compute_brake_bias(car, fuel_load_l=args.fuel)
        _diff = DiffSolver.solve_defaults(car, track)  # uses neutral driver defaults

        class _StandaloneSupporting:
            brake_bias_pct = _bias
            diff_preload_nm = _diff.preload_nm
            diff_ramp_coast = _diff.coast_ramp_deg
            diff_ramp_drive = _diff.drive_ramp_deg
            diff_clutch_plates = _diff.clutch_plates

        _supporting = _StandaloneSupporting()
    except Exception:
        pass

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
        stint_result=stint_result,
        sector_result=sector_result,
        sensitivity_result=sensitivity_result,
        space_result=space_result,
        supporting=_supporting,
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
        brake_bias, bias_reasoning = compute_brake_bias(
            car, fuel_load_l=args.fuel
        )
        print(f"\nBrake bias (physics): {brake_bias:.1f}%  [{bias_reasoning}]")
        sto_path = write_sto(
            car_name=car.name,
            track_name=f"{track.track_name} — {track.track_config}",
            wing=args.wing,
            fuel_l=args.fuel,
            step1=step1, step2=step2, step3=step3,
            step4=step4, step5=step5, step6=step6,
            output_path=args.sto,
            car_canonical=car.canonical_name,
            brake_bias_pct=brake_bias,
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
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
