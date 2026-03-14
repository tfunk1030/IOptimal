"""GTP Setup Producer — unified IBT->.sto physics pipeline.

Orchestrates the full flow:
  IBT -> telemetry extraction -> corner segmentation -> driver style analysis
  -> handling diagnosis -> aero gradient analysis -> solver modifiers
  -> 6-step constraint solver -> supporting parameter solver
  -> .sto output + engineering report

Usage:
    python -m pipeline.produce --car bmw --ibt path/to/session.ibt --wing 17
    python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --sto output.sto
    python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --lap 25 --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aero_model import load_car_surfaces
from aero_model.gradient import compute_gradients
from analyzer.adaptive_thresholds import compute_adaptive_thresholds
from analyzer.diagnose import diagnose
from analyzer.driver_style import analyze_driver
from analyzer.extract import extract_measurements
from analyzer.segment import segment_lap
from analyzer.setup_reader import CurrentSetup
from car_model.cars import get_car
from output.setup_writer import write_sto
from pipeline.report import generate_report
from solver.arb_solver import ARBSolver
from solver.corner_spring_solver import CornerSpringSolver
from solver.damper_solver import DamperSolver
from solver.heave_solver import HeaveSolver
from solver.modifiers import SolverModifiers, compute_modifiers
from solver.learned_corrections import apply_learned_corrections
from solver.rake_solver import RakeSolver, reconcile_ride_heights
from solver.supporting_solver import SupportingSolver
from solver.wheel_geometry_solver import WheelGeometrySolver
from track_model.build_profile import build_profile
from track_model.ibt_parser import IBTFile


def _find_lap_indices(ibt: IBTFile, lap_num: int) -> tuple[int, int] | None:
    """Find sample indices for a specific lap number."""
    for ln, s, e in ibt.lap_boundaries():
        if ln == lap_num:
            return (s, e)
    return None


def produce(args: argparse.Namespace) -> None:
    """Run the full setup production pipeline."""

    # ── Load car model ──
    car = get_car(args.car)
    print(f"Car: {car.name}")

    # ── Parse IBT ──
    ibt = IBTFile(args.ibt)
    print(f"IBT: {args.ibt}")
    print(f"  Samples: {ibt.record_count}, Tick rate: {ibt.tick_rate} Hz")

    # ── Auto-detect from session info ──
    current_setup = CurrentSetup.from_ibt(ibt)
    wing = args.wing or current_setup.wing_angle_deg
    fuel = args.fuel or current_setup.fuel_l or 89.0
    print(f"  Wing: {wing}°, Fuel: {fuel:.0f} L")

    # ── Apply learned corrections (default: auto, --no-learn to disable) ──
    learned = None
    if not getattr(args, "no_learn", False):
        track_info = ibt.track_info()
        track_name = track_info.get("track_name", "")
        learned = apply_learned_corrections(
            car.canonical_name, track_name, min_sessions=3, verbose=False
        )
        n_corrections = len(learned.applied)
        n_sessions = learned.session_count
        car_track_label = f"{car.canonical_name}/{track_name.lower().split()[0]}"
        if n_corrections > 0 and learned.session_count >= 3:
            # Apply corrections to car model
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
            print(f"[learn] Applied {n_corrections} corrections from {n_sessions} sessions ({car_track_label})")
        else:
            print(f"[learn] Physics-only ({n_sessions} sessions for {car_track_label})")

    # ── Load aero surfaces ──
    surfaces = load_car_surfaces(car.canonical_name)
    if wing not in surfaces:
        available = sorted(surfaces.keys())
        print(f"ERROR: Wing angle {wing}° not available. Available: {available}")
        sys.exit(1)
    surface = surfaces[wing]

    # ── Phase A: Build track profile from IBT ──
    print("\nBuilding track profile from IBT...")
    track = build_profile(args.ibt)
    print(f"  Track: {track.track_name} — {track.track_config}")
    print(f"  Best lap: {track.best_lap_time_s:.3f}s")

    # ── Phase B: Extract telemetry ──
    print("Extracting telemetry measurements...")
    measured = extract_measurements(
        args.ibt, car,
        lap=args.lap,
        min_lap_time=getattr(args, "min_lap_time", 108.0),
        outlier_pct=getattr(args, "outlier_pct", 0.115),
    )
    print(f"  Lap {measured.lap_number}: {measured.lap_time_s:.3f}s")

    # ── Phase C: Segment corners ──
    print("Segmenting lap into corners...")
    if args.lap:
        lap_indices = _find_lap_indices(ibt, args.lap)
    else:
        lap_indices = ibt.best_lap_indices(
            min_time=getattr(args, "min_lap_time", 108.0),
            outlier_pct=getattr(args, "outlier_pct", 0.115),
        )

    if lap_indices is None:
        print("ERROR: Could not find lap indices")
        sys.exit(1)

    start, end = lap_indices
    corners = segment_lap(ibt, start, end, car=car, tick_rate=ibt.tick_rate)
    print(f"  Detected {len(corners)} corners")

    corner_classes = {}
    for c in corners:
        corner_classes[c.speed_class] = corner_classes.get(c.speed_class, 0) + 1
    for cls, cnt in sorted(corner_classes.items()):
        print(f"    {cls}: {cnt}")

    # ── Phase D: Analyze driver style ──
    print("Analyzing driver style...")
    driver = analyze_driver(ibt, corners, car, tick_rate=ibt.tick_rate)
    print(f"  {driver.summary()}")

    # ── Phase E: Compute adaptive thresholds & diagnose handling ──
    print("Computing adaptive thresholds...")
    adaptive_thresh = compute_adaptive_thresholds(track, car, driver)
    if adaptive_thresh.adaptations:
        for a in adaptive_thresh.adaptations:
            print(f"  {a}")
    else:
        print("  Using baseline thresholds (no adaptations)")

    print("Diagnosing handling...")
    diagnosis = diagnose(measured, current_setup, car, thresholds=adaptive_thresh)
    print(f"  Assessment: {diagnosis.assessment}")
    print(f"  Problems: {len(diagnosis.problems)}")

    # ── Phase F: Compute aero gradients ──
    print("Computing aero gradients...")
    # Use measured ride heights if available, otherwise solver defaults
    front_rh_for_grad = measured.mean_front_rh_at_speed_mm or 15.0
    rear_rh_for_grad = measured.mean_rear_rh_at_speed_mm or 40.0
    aero_grad = compute_gradients(
        surface, car,
        front_rh=front_rh_for_grad,
        rear_rh=rear_rh_for_grad,
        front_rh_sigma_mm=measured.front_rh_std_mm,
        rear_rh_sigma_mm=measured.rear_rh_std_mm,
    )
    print(f"  DF balance: {aero_grad.df_balance_pct:.2f}%, L/D: {aero_grad.ld_ratio:.3f}")
    print(f"  Aero window: F±{aero_grad.front_rh_window_mm:.1f}mm, R±{aero_grad.rear_rh_window_mm:.1f}mm")

    # ── Phase G: Compute solver modifiers ──
    print("Computing solver modifiers...")
    modifiers = compute_modifiers(diagnosis, driver, measured)
    if modifiers.reasons:
        for r in modifiers.reasons:
            print(f"  {r}")
    else:
        print("  No modifiers applied (baseline solver)")

    # ── Phase H: Run 6-step solver with modifiers ──
    print()
    target_balance = args.balance + modifiers.df_balance_offset_pct

    # Step 1: Rake
    print("=" * 60)
    print(f"Running Step 1: Rake (target balance: {target_balance:.2f}%)...")
    rake_solver = RakeSolver(car, surface, track)
    step1 = rake_solver.solve(
        target_balance=target_balance,
        balance_tolerance=args.tolerance,
        fuel_load_l=fuel,
        pin_front_min=not args.free,
    )
    if not args.report_only:
        print(step1.summary())

    # Step 2: Heave
    print("\nRunning Step 2: Heave / Third Springs...")
    heave_solver = HeaveSolver(car, track)
    step2 = heave_solver.solve(
        dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
        dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
    )
    # Apply modifier floor constraints (check both independently)
    needs_re_solve = (
        (modifiers.front_heave_min_floor_nmm > 0 and step2.front_heave_nmm < modifiers.front_heave_min_floor_nmm)
        or (modifiers.rear_third_min_floor_nmm > 0 and step2.rear_third_nmm < modifiers.rear_third_min_floor_nmm)
    )
    if needs_re_solve:
        step2 = heave_solver.solve(
            dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
            dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
            front_heave_floor_nmm=modifiers.front_heave_min_floor_nmm,
            rear_third_floor_nmm=modifiers.rear_third_min_floor_nmm,
        )
    if not args.report_only:
        print(step2.summary())

    # Step 3: Corner Springs
    print("\nRunning Step 3: Corner Springs...")
    corner_solver = CornerSpringSolver(car, track)
    step3 = corner_solver.solve(
        front_heave_nmm=step2.front_heave_nmm,
        rear_third_nmm=step2.rear_third_nmm,
        fuel_load_l=fuel,
    )
    if not args.report_only:
        print(step3.summary())

    # Convert rear spring rate to wheel rate (MR^2) for downstream solvers
    rear_wheel_rate_nmm = step3.rear_spring_rate_nmm * car.corner_spring.rear_motion_ratio ** 2

    # RH reconciliation: refine static RH with actual spring values
    reconcile_ride_heights(car, step1, step2, step3, verbose=not args.report_only)

    # Step 4: ARBs (with LLTD offset)
    print("\nRunning Step 4: Anti-Roll Bars...")
    arb_solver = ARBSolver(car, track)
    step4 = arb_solver.solve(
        front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
        rear_wheel_rate_nmm=rear_wheel_rate_nmm,
        lltd_offset=modifiers.lltd_offset,
    )
    if not args.report_only:
        print(step4.summary())

    # Step 5: Wheel Geometry
    print("\nRunning Step 5: Wheel Geometry...")
    geom_solver = WheelGeometrySolver(car, track)
    _camber_conf = ("calibrated"
                    if learned and learned.calibrated_front_roll_gain is not None
                    else "estimated")
    step5 = geom_solver.solve(
        k_roll_total_nm_deg=step4.k_roll_front_total + step4.k_roll_rear_total,
        front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
        rear_wheel_rate_nmm=rear_wheel_rate_nmm,
        fuel_load_l=fuel,
        camber_confidence=_camber_conf,
    )
    if not args.report_only:
        print(step5.summary())

    # Step 6: Dampers (with damping ratio scale and click offsets)
    print("\nRunning Step 6: Dampers...")
    damper_solver = DamperSolver(car, track)
    step6 = damper_solver.solve(
        front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
        rear_wheel_rate_nmm=rear_wheel_rate_nmm,
        front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
        rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
        fuel_load_l=fuel,
        damping_ratio_scale=modifiers.damping_ratio_scale,
    )
    # Apply damper click offsets from modifiers
    _apply_damper_modifiers(step6, modifiers, car)
    if not args.report_only:
        print(step6.summary())

    # ── Phase I: Compute supporting params ──
    print("\nComputing supporting parameters...")
    supporting_solver = SupportingSolver(car, driver, measured, diagnosis, track=track)
    supporting = supporting_solver.solve()
    print(f"  {supporting.summary()}")

    # ── Phase I.5: Stint analysis ──
    stint_result = None
    try:
        from solver.stint_model import analyze_stint
        stint_result = analyze_stint(
            car=car,
            stint_laps=getattr(args, "stint_laps", 30),
            base_heave_nmm=step2.front_heave_nmm,
            base_third_nmm=step2.rear_third_nmm,
            v_p99_front_mps=track.shock_vel_p99_front_mps,
            v_p99_rear_mps=track.shock_vel_p99_rear_mps,
        )
    except (KeyError, AttributeError) as e:
        stint_result = None
        if not args.report_only:
            print(f"[stint] Skipped: missing data ({e})")
    except (TypeError, NameError) as e:
        raise  # re-raise programming errors — don't hide bugs

    # ── Phase I.6: Sector compromise analysis ──
    sector_result = None
    try:
        from solver.sector_compromise import SectorCompromise
        sector_solver = SectorCompromise(track)
        sector_result = sector_solver.analyze(
            step1=step1, step2=step2, step4=step4,
            base_bias_pct=supporting.brake_bias_pct,
            base_camber_deg=step5.front_camber_deg,
        )
    except (KeyError, AttributeError) as e:
        sector_result = None
        if not args.report_only:
            print(f"[sector] Skipped: missing data ({e})")
    except (TypeError, NameError) as e:
        raise  # re-raise programming errors — don't hide bugs

    # ── Phase I.7: Lap time sensitivity ──
    sensitivity_result = None
    try:
        from solver.laptime_sensitivity import compute_laptime_sensitivity
        sensitivity_result = compute_laptime_sensitivity(
            track=track,
            step1=step1, step2=step2, step3=step3,
            step4=step4, step5=step5,
            brake_bias_pct=supporting.brake_bias_pct,
        )
    except (KeyError, AttributeError) as e:
        sensitivity_result = None
        if not args.report_only:
            print(f"[sensitivity] Skipped: missing data ({e})")
    except (TypeError, NameError) as e:
        raise  # re-raise programming errors — don't hide bugs

    # ── Phase J: Output ──
    if args.sto:
        sto_path = write_sto(
            car_name=car.name,
            track_name=f"{track.track_name} — {track.track_config}",
            wing=wing,
            fuel_l=fuel,
            step1=step1, step2=step2, step3=step3,
            step4=step4, step5=step5, step6=step6,
            output_path=args.sto,
            car_canonical=car.canonical_name,
            tyre_pressure_kpa=supporting.tyre_cold_fl_kpa,
            brake_bias_pct=supporting.brake_bias_pct,
            diff_coast_drive_ramp=f"{supporting.diff_ramp_coast}/{supporting.diff_ramp_drive}",
            diff_clutch_plates=supporting.diff_clutch_plates,
            diff_preload_nm=supporting.diff_preload_nm,
            tc_gain=supporting.tc_gain,
            tc_slip=supporting.tc_slip,
        )
        print(f"\niRacing .sto setup saved to: {sto_path}")

    if args.json:
        import dataclasses
        json_path = Path(args.json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        output = {
            "car": car.name,
            "track": f"{track.track_name} — {track.track_config}",
            "wing": wing,
            "fuel_l": fuel,
            "lap_time_s": measured.lap_time_s,
            "lap_number": measured.lap_number,
            "driver_style": driver.style,
            "assessment": diagnosis.assessment,
            "step1_rake": dataclasses.asdict(step1),
            "step2_heave": dataclasses.asdict(step2),
            "step3_corner": dataclasses.asdict(step3),
            "step4_arb": dataclasses.asdict(step4),
            "step5_geometry": dataclasses.asdict(step5),
            "step6_dampers": dataclasses.asdict(step6),
            "supporting": dataclasses.asdict(supporting),
        }
        with open(json_path, "w") as f:
            json.dump(output, f, indent=2, default=str)
        print(f"\nJSON summary saved to: {json_path}")

    # ── Phase K: Engineering report ──
    print()
    print()
    report = generate_report(
        car=car,
        track=track,
        measured=measured,
        driver=driver,
        diagnosis=diagnosis,
        corners=corners,
        aero_grad=aero_grad,
        modifiers=modifiers,
        step1=step1, step2=step2, step3=step3,
        step4=step4, step5=step5, step6=step6,
        supporting=supporting,
        current_setup=current_setup,
        wing=wing,
        stint_result=stint_result,
        sector_result=sector_result,
        sensitivity_result=sensitivity_result,
    )
    print(report)

    # ── Phase L: Auto-learn (default: on, --no-learn disables) ──
    if not getattr(args, "no_learn", False):
        try:
            from learner.ingest import ingest_ibt
            result = ingest_ibt(
                car_name=args.car,
                ibt_path=args.ibt,
                wing=wing,
                lap=args.lap,
                verbose=False,
            )
            if result.get("new_learnings"):
                for ln in result["new_learnings"]:
                    print(f"[learn] {ln}")
        except Exception as e:
            print(f"[learn] Ingest failed: {e} (setup production was not affected)")


def _apply_damper_modifiers(
    step6: object,
    modifiers: SolverModifiers,
    car: object,
) -> None:
    """Apply click offsets from modifiers to damper solution in-place."""
    if not any([
        modifiers.front_ls_rbd_offset,
        modifiers.rear_ls_rbd_offset,
        modifiers.front_hs_comp_offset,
        modifiers.rear_hs_comp_offset,
    ]):
        return

    d = car.damper
    lo_ls, hi_ls = d.ls_comp_range
    lo_hs, hi_hs = d.hs_comp_range

    def clamp_click(val: int, lo: int, hi: int) -> int:
        return max(lo, min(hi, val))

    # Apply offsets to all four corners
    for corner in [step6.lf, step6.rf]:
        corner.ls_rbd = clamp_click(corner.ls_rbd + modifiers.front_ls_rbd_offset, lo_ls, hi_ls)
        corner.hs_comp = clamp_click(corner.hs_comp + modifiers.front_hs_comp_offset, lo_hs, hi_hs)

    for corner in [step6.lr, step6.rr]:
        corner.ls_rbd = clamp_click(corner.ls_rbd + modifiers.rear_ls_rbd_offset, lo_ls, hi_ls)
        corner.hs_comp = clamp_click(corner.hs_comp + modifiers.rear_hs_comp_offset, lo_hs, hi_hs)


def main():
    parser = argparse.ArgumentParser(
        description="GTP Setup Producer — IBT->.sto physics pipeline"
    )
    parser.add_argument("--car", required=True, help="Car name (e.g., bmw)")
    parser.add_argument("--ibt", required=True, help="Path to IBT telemetry file")
    parser.add_argument("--wing", type=float, default=None,
                        help="Wing angle (auto-detected from IBT if not specified)")
    parser.add_argument("--lap", type=int, default=None,
                        help="Lap number to analyze (default: best lap)")
    parser.add_argument("--balance", type=float, default=50.14,
                        help="Target DF balance %% (default: 50.14)")
    parser.add_argument("--tolerance", type=float, default=0.1,
                        help="Balance tolerance %% (default: 0.1)")
    parser.add_argument("--fuel", type=float, default=None,
                        help="Fuel load in liters (auto-detected if not specified)")
    parser.add_argument("--free", action="store_true",
                        help="Free optimization (don't pin front RH at sim floor)")
    parser.add_argument("--sto", type=str, default=None,
                        help="Export iRacing .sto setup file")
    parser.add_argument("--json", type=str, default=None,
                        help="Save full JSON summary to file")
    parser.add_argument("--report-only", action="store_true",
                        help="Print only the final report (skip per-step details)")
    parser.add_argument("--no-learn", action="store_true",
                        help="Disable auto-learning (skip empirical corrections and session ingest)")
    # Lap selection / filtering
    parser.add_argument("--min-lap-time", type=float, default=108.0, dest="min_lap_time",
                        help="Minimum valid lap time in seconds (default: 108.0). "
                             "Laps shorter than this are always excluded as partial/out-laps.")
    parser.add_argument("--outlier-pct", type=float, default=0.115, dest="outlier_pct",
                        help="Max fractional deviation above median to accept (default: 0.115 = 11.5%%). "
                             "Drops anomalously slow laps. Pass 0 to disable.")
    # Legacy flags (kept for backward-compat; no-op since auto is default)
    parser.add_argument("--learn", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--auto-learn", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    produce(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
