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
from analyzer.driver_style import analyze_driver, refine_driver_with_measured
from analyzer.extract import extract_measurements
from analyzer.segment import segment_lap
from analyzer.setup_reader import CurrentSetup
from analyzer.telemetry_truth import signals_to_dict, summarize_signal_quality
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
from solver.full_setup_optimizer import optimize_if_supported
from track_model.build_profile import build_profile
from track_model.ibt_parser import IBTFile


def _find_lap_indices(ibt: IBTFile, lap_num: int) -> tuple[int, int] | None:
    """Find sample indices for a specific lap number."""
    for ln, s, e in ibt.lap_boundaries():
        if ln == lap_num:
            return (s, e)
    return None


def produce(args: argparse.Namespace, _return_result: bool = False) -> None | dict:
    """Run the full setup production pipeline.

    Args:
        args: Parsed CLI args.
        _return_result: If True, return a dict of solver outputs (used by
            batch/multi-IBT compare mode) instead of returning None.
    """

    # ── Normalize IBT path(s) ──
    ibt_arg = args.ibt
    if isinstance(ibt_arg, list):
        if len(ibt_arg) >= 2:
            # Multiple IBTs → delegate to reasoning engine
            from pipeline.reason import reason_and_solve

            reason_and_solve(
                car_name=args.car,
                ibt_paths=ibt_arg,
                wing=args.wing,
                fuel=args.fuel,
                balance_target=getattr(args, "balance", 50.14),
                sto_path=args.sto,
                json_path=args.json,
            )
            return None
        ibt_path = ibt_arg[0]
    else:
        ibt_path = ibt_arg  # backward compat for programmatic callers

    quiet = bool(getattr(args, "report_only", False))

    def log(message: str = "") -> None:
        if not quiet:
            print(message)

    # ── Load car model ──
    car = get_car(args.car)
    log(f"Car: {car.name}")

    # ── Parse IBT ──
    ibt = IBTFile(ibt_path)
    log(f"IBT: {ibt_path}")
    log(f"  Samples: {ibt.record_count}, Tick rate: {ibt.tick_rate} Hz")

    # ── Auto-detect from session info ──
    current_setup = CurrentSetup.from_ibt(ibt)
    wing = args.wing or current_setup.wing_angle_deg
    fuel = args.fuel or current_setup.fuel_l or 89.0
    log(f"  Wing: {wing}°, Fuel: {fuel:.0f} L")

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
            # NOTE: aero_compression_{front,rear}_mm are intentionally NOT applied here.
            # The IBT LFrideHeight/LRrideHeight sensor channels are in a different
            # coordinate frame than the aero maps (AeroCalc reference). Applying the
            # sensor-measured compression to the aero map solver produces inflated
            # static RH recommendations (+10-15mm error). The car-model values in
            # cars.py are calibrated directly from AeroCalculator IBT fields and
            # are the correct reference for the aero solver.
            if learned.calibrated_front_roll_gain is not None:
                car.geometry.front_roll_gain = learned.calibrated_front_roll_gain
            if learned.calibrated_rear_roll_gain is not None:
                car.geometry.rear_roll_gain = learned.calibrated_rear_roll_gain
            log(f"[learn] Applied {n_corrections} corrections from {n_sessions} sessions ({car_track_label})")
        else:
            log(f"[learn] Physics-only ({n_sessions} sessions for {car_track_label})")

    # ── Load aero surfaces ──
    surfaces = load_car_surfaces(car.canonical_name)
    if wing not in surfaces:
        available = sorted(surfaces.keys())
        print(f"ERROR: Wing angle {wing}° not available. Available: {available}")
        sys.exit(1)
    surface = surfaces[wing]

    # ── Phase A: Build track profile from IBT ──
    log("\nBuilding track profile from IBT...")
    track = build_profile(ibt_path)
    log(f"  Track: {track.track_name} — {track.track_config}")
    log(f"  Best lap: {track.best_lap_time_s:.3f}s")

    # ── Phase B: Extract telemetry ──
    log("Extracting telemetry measurements...")
    measured = extract_measurements(
        ibt_path, car,
        lap=args.lap,
        min_lap_time=getattr(args, "min_lap_time", 108.0),
        outlier_pct=getattr(args, "outlier_pct", 0.115),
    )
    log(f"  Lap {measured.lap_number}: {measured.lap_time_s:.3f}s")

    # ── Phase B.5: Stint evolution (if --stint) ──
    stint_evolution = None
    if getattr(args, "stint", False):
        from analyzer.stint_analysis import analyze_stint_evolution
        log("\nAnalyzing stint evolution (all qualifying laps)...")
        stint_evolution = analyze_stint_evolution(
            ibt_path=ibt_path,
            car=car,
            threshold_pct=getattr(args, "stint_threshold", 1.5),
            min_lap_time=getattr(args, "min_lap_time", 108.0),
            ibt=ibt,
        )
        log(f"  {stint_evolution.qualifying_lap_count}/{stint_evolution.total_lap_count} "
            f"laps within {stint_evolution.threshold_pct}% of fastest "
            f"({stint_evolution.fastest_lap_time_s:.3f}s)")
        if stint_evolution.rates:
            log(f"  Fuel burn: {stint_evolution.rates.fuel_burn_l_per_lap:.2f} L/lap")
            log(f"  Understeer drift: {stint_evolution.rates.understeer_deg_per_lap:+.3f} deg/lap")
            log(f"  Grip trend: {stint_evolution.rates.peak_lat_g_per_lap:+.4f} g/lap")
            log(f"  Lap time trend: {stint_evolution.rates.lap_time_s_per_lap:+.3f} s/lap")

    # ── Phase C: Segment corners ──
    log("Segmenting lap into corners...")
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
    log(f"  Detected {len(corners)} corners")

    corner_classes = {}
    for c in corners:
        corner_classes[c.speed_class] = corner_classes.get(c.speed_class, 0) + 1
    for cls, cnt in sorted(corner_classes.items()):
        log(f"    {cls}: {cnt}")

    # ── Phase D: Analyze driver style ──
    log("Analyzing driver style...")
    driver = analyze_driver(ibt, corners, car, tick_rate=ibt.tick_rate)
    # Refine consistency using in-car adjustment counts from telemetry
    refine_driver_with_measured(driver, measured)
    log(f"  {driver.summary()}")

    # ── Phase E: Compute adaptive thresholds & diagnose handling ──
    log("Computing adaptive thresholds...")
    adaptive_thresh = compute_adaptive_thresholds(track, car, driver)
    if adaptive_thresh.adaptations:
        for a in adaptive_thresh.adaptations:
            log(f"  {a}")
    else:
        log("  Using baseline thresholds (no adaptations)")

    log("Diagnosing handling...")
    diagnosis = diagnose(measured, current_setup, car, thresholds=adaptive_thresh)
    log(f"  Assessment: {diagnosis.assessment}")
    log(f"  Problems: {len(diagnosis.problems)}")

    # ── Phase F: Compute aero gradients ──
    log("Computing aero gradients...")
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
    log(f"  DF balance: {aero_grad.df_balance_pct:.2f}%, L/D: {aero_grad.ld_ratio:.3f}")
    log(f"  Aero window: F±{aero_grad.front_rh_window_mm:.1f}mm, R±{aero_grad.rear_rh_window_mm:.1f}mm")

    # ── Phase G: Compute solver modifiers ──
    log("Computing solver modifiers...")
    modifiers = compute_modifiers(diagnosis, driver, measured)
    if modifiers.reasons:
        for r in modifiers.reasons:
            log(f"  {r}")
    else:
        log("  No modifiers applied (baseline solver)")

    # ── Phase H: Run 6-step solver with modifiers ──
    log()
    target_balance = args.balance + modifiers.df_balance_offset_pct
    _camber_conf = ("calibrated"
                    if learned and learned.calibrated_front_roll_gain is not None
                    else "estimated")

    optimized = optimize_if_supported(
        car=car,
        surface=surface,
        track=track,
        target_balance=target_balance,
        balance_tolerance=args.tolerance,
        fuel_load_l=fuel,
        pin_front_min=not args.free,
        wing_angle=wing,
        legacy_solver=getattr(args, "legacy_solver", False),
        damping_ratio_scale=modifiers.damping_ratio_scale,
        lltd_offset=modifiers.lltd_offset,
        measured=measured,
        camber_confidence=_camber_conf,
        front_heave_floor_nmm=modifiers.front_heave_min_floor_nmm,
        rear_third_floor_nmm=modifiers.rear_third_min_floor_nmm,
        front_heave_perch_target_mm=modifiers.front_heave_perch_target_mm,
    )

    if optimized is not None:
        log("=" * 60)
        log(f"Running BMW/Sebring constrained optimizer (target balance: {target_balance:.2f}%)...")
        step1 = optimized.step1
        step2 = optimized.step2
        step3 = optimized.step3
        step4 = optimized.step4
        step5 = optimized.step5
        step6 = optimized.step6
        _apply_damper_modifiers(step6, modifiers, car)
        rear_wheel_rate_nmm = step3.rear_spring_rate_nmm * car.corner_spring.rear_motion_ratio ** 2
    else:
        # Step 1: Rake
        log("=" * 60)
        log(f"Running Step 1: Rake (target balance: {target_balance:.2f}%)...")
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
        log("\nRunning Step 2: Heave / Third Springs...")
        heave_solver = HeaveSolver(car, track)
        step2 = heave_solver.solve(
            dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
            dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
            front_heave_perch_target_mm=modifiers.front_heave_perch_target_mm,
            front_pushrod_mm=step1.front_pushrod_offset_mm,
            rear_pushrod_mm=step1.rear_pushrod_offset_mm,
            fuel_load_l=fuel,
            front_camber_deg=current_setup.front_camber_deg or car.geometry.front_camber_baseline_deg,
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
                front_heave_perch_target_mm=modifiers.front_heave_perch_target_mm,
                front_pushrod_mm=step1.front_pushrod_offset_mm,
                rear_pushrod_mm=step1.rear_pushrod_offset_mm,
                fuel_load_l=fuel,
                front_camber_deg=current_setup.front_camber_deg or car.geometry.front_camber_baseline_deg,
            )
        if not args.report_only:
            print(step2.summary())

        # Step 3: Corner Springs
        log("\nRunning Step 3: Corner Springs...")
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
        heave_solver.reconcile_solution(
            step1,
            step2,
            step3,
            fuel_load_l=fuel,
            front_camber_deg=current_setup.front_camber_deg or car.geometry.front_camber_baseline_deg,
            verbose=not args.report_only,
        )
        reconcile_ride_heights(
            car, step1, step2, step3,
            fuel_load_l=fuel,
            track_name=track.track_name,
            verbose=not args.report_only,
        )

        # One fixed-point refinement pass: dampers depend on heave mode, and
        # heave sizing now accounts for damper work. Run a provisional damper
        # solve, then re-size heave/third once against that damper state.
        damper_solver = DamperSolver(car, track)
        provisional_step6 = damper_solver.solve(
            front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
            front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
            rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
            fuel_load_l=fuel,
            damping_ratio_scale=modifiers.damping_ratio_scale,
            measured=measured,
            front_heave_nmm=step2.front_heave_nmm,
            rear_third_nmm=step2.rear_third_nmm,
        )
        refined_step2 = heave_solver.solve(
            dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
            dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
            front_heave_floor_nmm=modifiers.front_heave_min_floor_nmm,
            rear_third_floor_nmm=modifiers.rear_third_min_floor_nmm,
            front_heave_perch_target_mm=modifiers.front_heave_perch_target_mm,
            front_pushrod_mm=step1.front_pushrod_offset_mm,
            rear_pushrod_mm=step1.rear_pushrod_offset_mm,
            front_torsion_od_mm=step3.front_torsion_od_mm,
            rear_spring_nmm=step3.rear_spring_rate_nmm,
            rear_spring_perch_mm=step3.rear_spring_perch_mm,
            rear_third_perch_mm=step2.perch_offset_rear_mm,
            fuel_load_l=fuel,
            front_camber_deg=current_setup.front_camber_deg or car.geometry.front_camber_baseline_deg,
            front_hs_damper_nsm=provisional_step6.c_hs_front,
            rear_hs_damper_nsm=provisional_step6.c_hs_rear,
        )
        if (
            abs(refined_step2.front_heave_nmm - step2.front_heave_nmm) > 0.05
            or abs(refined_step2.rear_third_nmm - step2.rear_third_nmm) > 0.05
            or abs(refined_step2.perch_offset_front_mm - step2.perch_offset_front_mm) > 0.05
        ):
            step2 = refined_step2
            step3 = corner_solver.solve(
                front_heave_nmm=step2.front_heave_nmm,
                rear_third_nmm=step2.rear_third_nmm,
                fuel_load_l=fuel,
            )
            rear_wheel_rate_nmm = step3.rear_spring_rate_nmm * car.corner_spring.rear_motion_ratio ** 2
            heave_solver.reconcile_solution(
                step1,
                step2,
                step3,
                fuel_load_l=fuel,
                front_camber_deg=current_setup.front_camber_deg or car.geometry.front_camber_baseline_deg,
                front_hs_damper_nsm=provisional_step6.c_hs_front,
                verbose=False,
            )
            reconcile_ride_heights(
                car, step1, step2, step3,
                fuel_load_l=fuel,
                track_name=track.track_name,
                verbose=False,
            )

        # Step 4: ARBs (with LLTD offset)
        log("\nRunning Step 4: Anti-Roll Bars...")
        arb_solver = ARBSolver(car, track)
        step4 = arb_solver.solve(
            front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
            lltd_offset=modifiers.lltd_offset,
        )
        if not args.report_only:
            print(step4.summary())

        # Step 5: Wheel Geometry
        log("\nRunning Step 5: Wheel Geometry...")
        geom_solver = WheelGeometrySolver(car, track)
        step5 = geom_solver.solve(
            k_roll_total_nm_deg=step4.k_roll_front_total + step4.k_roll_rear_total,
            front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
            fuel_load_l=fuel,
            camber_confidence=_camber_conf,
        )
        if not args.report_only:
            print(step5.summary())

        reconcile_ride_heights(
            car, step1, step2, step3,
            step5=step5,
            fuel_load_l=fuel,
            track_name=track.track_name,
            verbose=False,
        )

        # Step 6: Dampers (with damping ratio scale and click offsets)
        log("\nRunning Step 6: Dampers...")
        step6 = damper_solver.solve(
            front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
            front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
            rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
            fuel_load_l=fuel,
            damping_ratio_scale=modifiers.damping_ratio_scale,
            measured=measured,
            front_heave_nmm=step2.front_heave_nmm,
            rear_third_nmm=step2.rear_third_nmm,
        )
        # Apply damper click offsets from modifiers
        _apply_damper_modifiers(step6, modifiers, car)
        if not args.report_only:
            print(step6.summary())

    # ── Phase H.5: Multi-solve stint compromise (if --stint) ──
    stint_compromise_info: list[str] = []
    if stint_evolution is not None and stint_evolution.qualifying_lap_count >= 3:
        log("\nRunning multi-solve stint compromise (start/mid/end)...")
        _stint_solves: dict[str, tuple] = {}
        for _label, _snap in [("start", stint_evolution.start_snapshot),
                               ("mid", stint_evolution.mid_snapshot),
                               ("end", stint_evolution.end_snapshot)]:
            _fuel_at = _snap.fuel_level_l or fuel
            _rs = RakeSolver(car, surface, track)
            _s1 = _rs.solve(
                target_balance=target_balance,
                balance_tolerance=args.tolerance,
                fuel_load_l=_fuel_at,
                pin_front_min=not args.free,
            )
            _hs = HeaveSolver(car, track)
            _s2 = _hs.solve(
                dynamic_front_rh_mm=_s1.dynamic_front_rh_mm,
                dynamic_rear_rh_mm=_s1.dynamic_rear_rh_mm,
                front_pushrod_mm=_s1.front_pushrod_offset_mm,
                rear_pushrod_mm=_s1.rear_pushrod_offset_mm,
                fuel_load_l=_fuel_at,
                front_camber_deg=current_setup.front_camber_deg or car.geometry.front_camber_baseline_deg,
            )
            _cs = CornerSpringSolver(car, track)
            _s3 = _cs.solve(
                front_heave_nmm=_s2.front_heave_nmm,
                rear_third_nmm=_s2.rear_third_nmm,
                fuel_load_l=_fuel_at,
            )
            _rwr = _s3.rear_spring_rate_nmm * car.corner_spring.rear_motion_ratio ** 2
            _as = ARBSolver(car, track)
            _s4 = _as.solve(
                front_wheel_rate_nmm=_s3.front_wheel_rate_nmm,
                rear_wheel_rate_nmm=_rwr,
                lltd_offset=modifiers.lltd_offset,
            )
            _gs = WheelGeometrySolver(car, track)
            _s5 = _gs.solve(
                k_roll_total_nm_deg=_s4.k_roll_front_total + _s4.k_roll_rear_total,
                front_wheel_rate_nmm=_s3.front_wheel_rate_nmm,
                rear_wheel_rate_nmm=_rwr,
                fuel_load_l=_fuel_at,
                camber_confidence=_camber_conf,
            )
            _ds = DamperSolver(car, track)
            _s6 = _ds.solve(
                front_wheel_rate_nmm=_s3.front_wheel_rate_nmm,
                rear_wheel_rate_nmm=_rwr,
                front_dynamic_rh_mm=_s1.dynamic_front_rh_mm,
                rear_dynamic_rh_mm=_s1.dynamic_rear_rh_mm,
                fuel_load_l=_fuel_at,
                damping_ratio_scale=modifiers.damping_ratio_scale,
                measured=measured,
                front_heave_nmm=_s2.front_heave_nmm,
                rear_third_nmm=_s2.rear_third_nmm,
            )
            _stint_solves[_label] = (_s1, _s2, _s3, _s4, _s5, _s6)
            log(f"  [{_label}] fuel={_fuel_at:.1f}L heave={_s2.front_heave_nmm:.0f} "
                f"third={_s2.rear_third_nmm:.0f} LLTD={_s4.lltd_achieved:.1f}%")

        # Compromise: safety-binding springs, averaged balance, mid-stint dampers
        _start = _stint_solves["start"]
        _mid = _stint_solves["mid"]
        _end = _stint_solves["end"]

        # Safety-binding: max heave/third across conditions (full fuel is heaviest)
        max_heave = max(s[1].front_heave_nmm for s in _stint_solves.values())
        max_third = max(s[1].rear_third_nmm for s in _stint_solves.values())
        if max_heave > step2.front_heave_nmm:
            stint_compromise_info.append(
                f"Heave spring: {max_heave:.0f} N/mm (safety-bound from start condition, "
                f"was {step2.front_heave_nmm:.0f})"
            )
            step2.front_heave_nmm = max_heave
        if max_third > step2.rear_third_nmm:
            stint_compromise_info.append(
                f"Third spring: {max_third:.0f} N/mm (safety-bound from start condition, "
                f"was {step2.rear_third_nmm:.0f})"
            )
            step2.rear_third_nmm = max_third

        # Averaged LLTD target: use mid-stint ARB solution
        avg_lltd = sum(s[3].lltd_achieved for s in _stint_solves.values()) / 3.0
        stint_compromise_info.append(
            f"LLTD target: {avg_lltd:.1f}% (avg of start {_start[3].lltd_achieved:.1f}% / "
            f"mid {_mid[3].lltd_achieved:.1f}% / end {_end[3].lltd_achieved:.1f}%)"
        )
        # Use mid-stint ARB solution as best compromise
        step4 = _mid[3]

        # Camber/toe: average from three conditions
        avg_front_camber = sum(s[4].front_camber_deg for s in _stint_solves.values()) / 3.0
        avg_rear_camber = sum(s[4].rear_camber_deg for s in _stint_solves.values()) / 3.0
        step5.front_camber_deg = round(avg_front_camber, 2)
        step5.rear_camber_deg = round(avg_rear_camber, 2)

        # Dampers: use mid-stint solution (best single compromise)
        step6 = _mid[5]
        _apply_damper_modifiers(step6, modifiers, car)
        stint_compromise_info.append("Dampers: mid-stint condition (best single compromise)")

        log(f"\n  Stint compromise applied ({len(stint_compromise_info)} adjustments)")
        for info in stint_compromise_info:
            log(f"    {info}")

    # ── Phase I: Compute supporting params ──
    log("\nComputing supporting parameters...")
    supporting_solver = SupportingSolver(car, driver, measured, diagnosis, track=track)
    supporting = supporting_solver.solve()
    log(f"  {supporting.summary()}")

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
            evolution=stint_evolution,
        )
    except (KeyError, AttributeError) as e:
        stint_result = None
        log(f"[stint] Skipped: missing data ({e})")
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
        log(f"[sector] Skipped: missing data ({e})")
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
        log(f"[sensitivity] Skipped: missing data ({e})")
    except (TypeError, NameError) as e:
        raise  # re-raise programming errors — don't hide bugs

    # ── Phase I.8: Ferrari indexed parameter passthrough ──
    # Ferrari uses indexed values (0-8, 0-18) for heave springs and torsion bar OD,
    # not physical N/mm or mm values. The solver computes physical values that are
    # meaningless for Ferrari. Pass through the IBT's current indexed values instead.
    if car.canonical_name == "ferrari":
        _cs = current_setup
        step2.front_heave_nmm = _cs.front_heave_nmm          # index 0-8
        step2.perch_offset_front_mm = _cs.front_heave_perch_mm
        step2.rear_third_nmm = _cs.rear_third_nmm            # index 0-9 (rear heave)
        step2.perch_offset_rear_mm = _cs.rear_third_perch_mm
        step3.front_torsion_od_mm = _cs.front_torsion_od_mm  # index 0-18
        step3.rear_spring_rate_nmm = _cs.rear_spring_nmm     # rear torsion bar OD index
        step3.rear_spring_perch_mm = 0.0                      # N/A for Ferrari
        supporting.brake_bias_pct = current_setup.brake_bias_pct
        supporting.diff_preload_nm = current_setup.diff_preload_nm
        supporting.diff_clutch_plates = current_setup.diff_clutch_plates
        supporting.tc_gain = current_setup.tc_gain
        supporting.tc_slip = current_setup.tc_slip
        if "more" in (current_setup.diff_ramp_angles or "").lower():
            supporting.diff_ramp_coast = 40
            supporting.diff_ramp_drive = 65
        else:
            supporting.diff_ramp_coast = 50
            supporting.diff_ramp_drive = 75

    # ── Phase J: Output ──
    legal_validation = validate_solution_legality(
        car=car,
        track_name=track.track_name,
        step1=step1,
        step2=step2,
        step3=step3,
        step5=step5,
        fuel_l=fuel,
    )
    for warning in legal_validation.warnings:
        log(f"[garage] {warning}")

    decision_trace = build_parameter_decisions(
        car_name=car.canonical_name,
        current_setup=current_setup,
        measured=measured,
        step1=step1,
        step2=step2,
        step3=step3,
        step4=step4,
        step5=step5,
        step6=step6,
        supporting=supporting,
        legality=legal_validation,
        fallback_reasons=list(getattr(measured, "fallback_reasons", [])),
    )
    telemetry_quality_lines = summarize_signal_quality(measured)
    legality_lines = [
        f"Legality: {'validated' if legal_validation.valid else 'warning'}",
        *(f"Garage: {msg}" for msg in legal_validation.messages[:2]),
    ]
    decision_trace_lines = [
        f"{decision.parameter}: {decision.current_value} -> {decision.proposed_value} {decision.unit}".rstrip()
        for decision in decision_trace[:6]
        if not decision.blocked_reason
    ]

    if args.sto:
        # Pass IBT torsion bar turns for Ferrari (solver can't compute these)
        _tb_kw = {}
        if car.canonical_name == "ferrari":
            _tb_kw["front_tb_turns"] = current_setup.torsion_bar_turns
            _tb_kw["rear_tb_turns"] = current_setup.rear_torsion_bar_turns

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
            diff_coast_drive_ramp=(
                ("More Locking" if supporting.diff_ramp_coast <= 45 else "Less Locking")
                if car.canonical_name == "ferrari"
                else f"{supporting.diff_ramp_coast}/{supporting.diff_ramp_drive}"
            ),
            diff_clutch_plates=supporting.diff_clutch_plates,
            diff_preload_nm=supporting.diff_preload_nm,
            tc_gain=supporting.tc_gain,
            tc_slip=supporting.tc_slip,
            **_tb_kw,
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
            "telemetry_quality": signals_to_dict(getattr(measured, "telemetry_signals", {})),
            "extraction_attempts": getattr(measured, "extraction_attempts", []),
            "signal_conflicts": getattr(measured, "signal_conflicts", []),
            "fallback_reasons": getattr(measured, "fallback_reasons", []),
            "parameter_evidence": [decision.to_dict() for decision in decision_trace],
            "decision_trace": [decision.to_dict() for decision in decision_trace],
            "legal_validation": legal_validation.to_dict(),
        }
        with open(json_path, "w") as f:
            json.dump(output, f, indent=2, default=str)
        print(f"\nJSON summary saved to: {json_path}")

    # ── Phase K: Engineering report ──
    if not quiet:
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
        target_balance=target_balance,
        stint_result=stint_result,
        sector_result=sector_result,
        sensitivity_result=sensitivity_result,
        stint_evolution=stint_evolution,
        stint_compromise_info=stint_compromise_info,
        telemetry_quality_lines=telemetry_quality_lines,
        legality_lines=legality_lines,
        decision_trace_lines=decision_trace_lines,
        compact=quiet,
    )
    print(report)

    # ── Build return dict if requested (multi-IBT batch mode) ──
    _result: dict | None = None
    if _return_result:
        _result = {
            "report": report,
            "lap_time_s": measured.lap_time_s,
            "lap_number": measured.lap_number,
            "current_setup": current_setup,
            "step1": step1,
            "step2": step2,
            "step3": step3,
            "step4": step4,
            "step5": step5,
            "step6": step6,
            "supporting": supporting,
            "decision_trace": decision_trace,
            "legal_validation": legal_validation,
        }

    # ── Phase L: Auto-learn (default: on, --no-learn disables) ──
    if not getattr(args, "no_learn", False):
        try:
            from learner.ingest import ingest_ibt
            from learner.knowledge_store import KnowledgeStore

            # Build solver predictions dict for the feedback loop.
            # These are what the solver PREDICTED for this session's telemetry.
            solver_predictions = {
                "front_rh_std_mm": getattr(step2, "front_rh_sigma_mm", 0.0),
                "rear_rh_std_mm": getattr(step2, "rear_rh_sigma_mm", 0.0),
                "lltd_predicted": getattr(step4, "lltd_achieved", 0.0),
                "body_roll_predicted_deg_per_g": getattr(step4, "roll_gradient_deg_per_g", 0.0),
                "front_bottoming_predicted": getattr(step2, "bottoming_events_front", 0),
            }

            store = KnowledgeStore()
            result = ingest_ibt(
                car_name=args.car,
                ibt_path=ibt_path,
                wing=wing,
                lap=args.lap,
                store=store,
                verbose=False,
            )

            # Attach solver predictions to the stored observation
            session_id = result.get("session_id", "")
            if session_id:
                obs_data = store.load_observation(session_id)
                if obs_data:
                    obs_data["solver_predictions"] = solver_predictions
                    store.save_observation(session_id, obs_data)

            if result.get("new_learnings"):
                for ln in result["new_learnings"]:
                    log(f"[learn] {ln}")
        except Exception as e:
            print(f"[learn] Ingest failed: {e} (setup production was not affected)")

    return _result


def produce_result(args: argparse.Namespace) -> dict:
    """Run the full pipeline and return structured results for comparison.

    Wraps ``produce(_return_result=True)`` — prints the report and returns
    a dict with all solver outputs for use by the multi-IBT batch runner.

    Returns:
        dict with keys: lap_time_s, lap_number, current_setup,
        step1..step6, supporting, report
    """
    result = produce(args, _return_result=True)
    if result is None:
        raise RuntimeError("produce_result: produce() did not return data")
    return result


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
    parser.add_argument("--ibt", required=True, nargs="+",
                        help="Path(s) to IBT telemetry file(s). "
                             "When 2+ files given, delegates to the reasoning engine.")
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
    parser.add_argument("--legacy-solver", action="store_true",
                        help="Force the legacy sequential solver path for BMW/Sebring validation")
    # Lap selection / filtering
    parser.add_argument("--min-lap-time", type=float, default=108.0, dest="min_lap_time",
                        help="Minimum valid lap time in seconds (default: 108.0). "
                             "Laps shorter than this are always excluded as partial/out-laps.")
    parser.add_argument("--outlier-pct", type=float, default=0.115, dest="outlier_pct",
                        help="Max fractional deviation above median to accept (default: 0.115 = 11.5%%). "
                             "Drops anomalously slow laps. Pass 0 to disable.")
    # Stint analysis
    parser.add_argument("--stint", action="store_true",
                        help="Enable stint analysis: analyze all qualifying laps, "
                             "run solver at start/mid/end conditions, produce compromise setup")
    parser.add_argument("--stint-threshold", type=float, default=1.5, dest="stint_threshold",
                        help="Max %% slower than fastest lap to include (default: 1.5)")
    # Legacy flags (kept for backward-compat; no-op since auto is default)
    parser.add_argument("--learn", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--auto-learn", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    produce(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
