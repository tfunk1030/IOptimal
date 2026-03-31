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
from solver.full_setup_optimizer import optimize_if_supported
from output.report import print_full_setup_report, save_json_summary, to_public_output_payload
from output.setup_writer import write_sto
from solver.scenario_profiles import resolve_scenario_name, should_run_legal_manifold_search
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


def _apply_candidate_params_to_steps(
    params: dict[str, object],
    *,
    step1: object,
    step2: object,
    step3: object,
    step4: object,
    step5: object,
    step6: object,
) -> None:
    direct_fields = {
        "front_pushrod_offset_mm": (step1, "front_pushrod_offset_mm"),
        "rear_pushrod_offset_mm": (step1, "rear_pushrod_offset_mm"),
        "front_rh_static_mm": (step1, "static_front_rh_mm"),
        "rear_rh_static_mm": (step1, "static_rear_rh_mm"),
        "front_heave_spring_nmm": (step2, "front_heave_nmm"),
        "front_heave_perch_mm": (step2, "perch_offset_front_mm"),
        "rear_third_spring_nmm": (step2, "rear_third_nmm"),
        "rear_third_perch_mm": (step2, "perch_offset_rear_mm"),
        "front_torsion_od_mm": (step3, "front_torsion_od_mm"),
        "rear_spring_rate_nmm": (step3, "rear_spring_rate_nmm"),
        "rear_spring_perch_mm": (step3, "rear_spring_perch_mm"),
        "front_arb_size": (step4, "front_arb_size"),
        "rear_arb_size": (step4, "rear_arb_size"),
        "front_camber_deg": (step5, "front_camber_deg"),
        "rear_camber_deg": (step5, "rear_camber_deg"),
        "front_toe_mm": (step5, "front_toe_mm"),
        "rear_toe_mm": (step5, "rear_toe_mm"),
    }
    for key, target in direct_fields.items():
        value = params.get(key)
        if value is None:
            continue
        target_obj, field_name = target
        if hasattr(target_obj, field_name):
            setattr(target_obj, field_name, value)

    front_arb_blade = params.get("front_arb_blade")
    if front_arb_blade is not None:
        for field_name in ("front_arb_blade_start", "farb_blade_locked"):
            if hasattr(step4, field_name):
                setattr(step4, field_name, int(round(float(front_arb_blade))))

    rear_arb_blade = params.get("rear_arb_blade")
    if rear_arb_blade is not None:
        for field_name in ("rear_arb_blade_start", "rarb_blade_slow_corner", "rarb_blade_fast_corner"):
            if hasattr(step4, field_name):
                setattr(step4, field_name, int(round(float(rear_arb_blade))))

    axle_damper_fields = {
        "front_ls_comp": ("ls_comp", ("lf", "rf")),
        "front_ls_rbd": ("ls_rbd", ("lf", "rf")),
        "front_hs_comp": ("hs_comp", ("lf", "rf")),
        "front_hs_rbd": ("hs_rbd", ("lf", "rf")),
        "front_hs_slope": ("hs_slope", ("lf", "rf")),
        "rear_ls_comp": ("ls_comp", ("lr", "rr")),
        "rear_ls_rbd": ("ls_rbd", ("lr", "rr")),
        "rear_hs_comp": ("hs_comp", ("lr", "rr")),
        "rear_hs_rbd": ("hs_rbd", ("lr", "rr")),
        "rear_hs_slope": ("hs_slope", ("lr", "rr")),
    }
    for key, (field_name, corners) in axle_damper_fields.items():
        value = params.get(key)
        if value is None:
            continue
        for corner_name in corners:
            corner = getattr(step6, corner_name, None)
            if corner is not None and hasattr(corner, field_name):
                setattr(corner, field_name, int(round(float(value))))


def main():
    parser = argparse.ArgumentParser(
        description="GTP Setup Solver — Physics-based setup calculator"
    )
    parser.add_argument("--car", required=True, help="Car name (e.g., bmw)")
    parser.add_argument("--track", required=True, help="Track name (e.g., sebring)")
    parser.add_argument("--wing", required=True, type=float, help="Wing angle (degrees)")
    parser.add_argument("--balance", type=float, default=None,
                        help="Target DF balance %% (default: car-specific)")
    parser.add_argument("--tolerance", type=float, default=0.1,
                        help="Balance tolerance %% (default: 0.1)")
    parser.add_argument("--fuel", type=float, default=89.0,
                        help="Fuel load in liters (default: 89)")
    parser.add_argument("--mid-stint", action="store_true",
                        help="Optimize for mid-stint conditions (half fuel)")
    parser.add_argument("--free", action="store_true",
                        help="Search the legal setup manifold from a pinned baseline seed")
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
    parser.add_argument("--explore", action="store_true",
                        help="[EXPERIMENTAL] Run unconstrained parameter space exploration (ignores best-practice constraints)")
    parser.add_argument("--bayesian", action="store_true",
                        help="[EXPERIMENTAL] Run Bayesian optimization over full legal parameter space — research only, not validated")
    parser.add_argument("--multi-speed", action="store_true",
                        help="[EXPERIMENTAL] Run multi-speed compromise analysis (low/mid/high speed regimes) — research only")
    parser.add_argument("--stint-laps", type=int, default=30,
                        help="Stint length for stint analysis (default: 30)")
    parser.add_argument("--legacy-solver", action="store_true",
                        help="Force the legacy sequential solver path for BMW/Sebring validation")
    parser.add_argument("--legal-search", action="store_true",
                        help="Run legal-manifold search after physics solver")
    parser.add_argument("--search-budget", type=int, default=1000,
                        help="Number of candidates for legal-space search (default: 1000)")
    parser.add_argument("--scenario-profile", type=str, default="single_lap_safe",
                        choices=["single_lap_safe", "quali", "sprint", "race"],
                        dest="scenario_profile",
                        help="Scenario objective profile for legal-manifold search")
    parser.add_argument("--objective-profile", type=str,
                        choices=["single_lap_safe", "quali", "sprint", "race"],
                        dest="scenario_profile",
                        help="Legacy alias for --scenario-profile")

    args = parser.parse_args()
    run_solver(args)


def run_solver(args: "argparse.Namespace") -> None:
    """Run the standalone physics solver with a pre-parsed args object.

    Called by both ``main()`` (direct invocation) and the unified
    ``__main__.py`` entry point when no IBT is provided.
    """
    # Fill in defaults for args that differ between unified and standalone CLI
    if not hasattr(args, "fuel") or args.fuel is None:
        args.fuel = 89.0
    if not hasattr(args, "balance") or args.balance is None:
        args.balance = None  # resolved from car model below
    if not hasattr(args, "tolerance"):
        args.tolerance = 0.1
    if not hasattr(args, "free"):
        args.free = False
    if not hasattr(args, "json"):
        args.json = False
    if not hasattr(args, "save"):
        args.save = getattr(args, "json_path", None)
    if not hasattr(args, "report_only"):
        args.report_only = False
    if not hasattr(args, "space"):
        args.space = False
    if not hasattr(args, "explore"):
        args.explore = False
    if not hasattr(args, "bayesian"):
        args.bayesian = False
    if not hasattr(args, "multi_speed"):
        args.multi_speed = False
    if not hasattr(args, "mid_stint"):
        args.mid_stint = False
    if not hasattr(args, "stint_laps"):
        args.stint_laps = 30
    if not hasattr(args, "learn"):
        args.learn = not getattr(args, "no_learn", False)
    if not hasattr(args, "legacy_solver"):
        args.legacy_solver = False
    if not hasattr(args, "scenario_profile") or not args.scenario_profile:
        args.scenario_profile = "single_lap_safe"

    quiet = bool(args.report_only)
    free_mode = bool(args.free)
    resolved_scenario = resolve_scenario_name(getattr(args, "scenario_profile", None))

    def log(message: str = "") -> None:
        if not quiet:
            print(message)

    # Load car model
    car = get_car(args.car)
    log(f"Car: {car.name}")

    # Resolve DF balance target from car model if not explicitly set
    if args.balance is None:
        args.balance = car.default_df_balance_pct
        log(f"Using car-specific DF balance target: {args.balance:.2f}%")

    # Mid-stint optimization: use half fuel load
    if args.mid_stint:
        original_fuel = args.fuel
        args.fuel = args.fuel * 0.5
        log(f"[mid-stint] Optimizing for half fuel: {args.fuel:.0f} L (was {original_fuel:.0f} L)")
    if free_mode:
        log(f"[free-opt] Legal-manifold search enabled from a pinned seed ({resolved_scenario}).")

    # Apply learned corrections if requested
    learned = None
    if args.learn:
        learned = apply_learned_corrections(
            car.canonical_name, args.track, min_sessions=2, verbose=not quiet
        )
        if learned.applied:
            # Override calibration constants with empirical values
            if learned.heave_m_eff_front_kg is not None:
                car.heave_spring.front_m_eff_kg = learned.heave_m_eff_front_kg
            if learned.heave_m_eff_rear_kg is not None:
                car.heave_spring.rear_m_eff_kg = learned.heave_m_eff_rear_kg
            # aero_compression overrides intentionally omitted — see pipeline/produce.py note
            if learned.calibrated_front_roll_gain is not None:
                car.geometry.front_roll_gain = learned.calibrated_front_roll_gain
            if learned.calibrated_rear_roll_gain is not None:
                car.geometry.rear_roll_gain = learned.calibrated_rear_roll_gain
            log()

    # Confidence check — warn if car model has ESTIMATE parameters
    confidence = car.estimate_confidence()
    estimate_params = [p for p, v in confidence.items() if "ESTIMATE" in v]
    if estimate_params:
        params_str = ", ".join(estimate_params)
        log(f"[confidence] {car.name}: {params_str} - outputs less reliable")
        log("  (Run pipeline with real IBT to calibrate these values)")
        log()

    # Load aero surfaces
    surfaces = load_car_surfaces(car.canonical_name)
    if args.wing not in surfaces:
        available = sorted(surfaces.keys())
        print(f"ERROR: Wing angle {args.wing}° not available. Available: {available}")
        sys.exit(1)
    surface = surfaces[args.wing]
    log(f"Aero surface: {surface}")

    # Load track profile (try IBT-derived first, fall back to generic)
    try:
        track = find_track_profile(args.track)
        log(f"Track: {track.track_name} - {track.track_config}")
    except FileNotFoundError:
        from track_model.generic_profiles import generate_generic_profile
        track = generate_generic_profile(name=args.track, car=args.car)
        log(f"Track: {track.track_name} (generic profile — no IBT data)")
        log(f"  Note: Run with IBT telemetry for accurate results")
    log(f"Best lap: {track.best_lap_time_s:.3f}s")
    log()

    _camber_conf = ("calibrated"
                    if learned and learned.calibrated_front_roll_gain is not None
                    else "estimated")

    optimized = optimize_if_supported(
        car=car,
        surface=surface,
        track=track,
        target_balance=args.balance,
        balance_tolerance=args.tolerance,
        fuel_load_l=args.fuel,
        pin_front_min=True,
        wing_angle=args.wing,
        legacy_solver=args.legacy_solver,
        camber_confidence=_camber_conf,
    )

    if optimized is not None:
        log("=" * 60)
        log("Running BMW/Sebring constrained optimizer...")
        log(f"  Target DF balance: {args.balance:.2f}% ± {args.tolerance:.2f}%")
        log(f"  Fuel load: {args.fuel:.0f} L")
        log()
        step1 = optimized.step1
        step2 = optimized.step2
        step3 = optimized.step3
        step4 = optimized.step4
        step5 = optimized.step5
        step6 = optimized.step6
        rear_wheel_rate_nmm = step3.rear_spring_rate_nmm * car.corner_spring.rear_motion_ratio ** 2
    else:
        # ─── Step 1: Rake / Ride Heights ─────────────────────────────────
        log("=" * 60)
        log("Running Step 1: Rake / Ride Heights...")
        log(f"  Target DF balance: {args.balance:.2f}% ± {args.tolerance:.2f}%")
        log(f"  Fuel load: {args.fuel:.0f} L")
        log()

        rake_solver = RakeSolver(car, surface, track)
        step1 = rake_solver.solve(
            target_balance=args.balance,
            balance_tolerance=args.tolerance,
            fuel_load_l=args.fuel,
            pin_front_min=True,
        )

        if not args.json and not args.report_only:
            print(step1.summary())

        # ─── Step 2: Heave / Third Springs ─────────────────────────────────
        log()
        log("Running Step 2: Heave / Third Springs...")
        log()

        heave_solver = HeaveSolver(car, track)
        step2 = heave_solver.solve(
            dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
            dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
            front_pushrod_mm=step1.front_pushrod_offset_mm,
            rear_pushrod_mm=step1.rear_pushrod_offset_mm,
            fuel_load_l=args.fuel,
            front_camber_deg=car.geometry.front_camber_baseline_deg,
        )

        if not args.json and not args.report_only:
            print(step2.summary())

        # ─── Step 3: Corner Springs ────────────────────────────────────────
        log()
        log("Running Step 3: Corner Springs...")
        log()

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
        heave_solver.reconcile_solution(
            step1,
            step2,
            step3,
            fuel_load_l=args.fuel,
            front_camber_deg=car.geometry.front_camber_baseline_deg,
            verbose=not args.json and not args.report_only,
        )
        reconcile_ride_heights(
            car, step1, step2, step3,
            fuel_load_l=args.fuel,
            track_name=track.track_name,
            verbose=not args.json and not args.report_only,
            surface=surface,
            track=track,
            target_balance=args.balance,
        )
        if not args.json and not args.report_only:
            log()

        # One refinement pass: heave sizing depends on HS damper work, and the
        # damper solve depends on the modal heave/third rates. Solve once,
        # re-size heave/third against that damper state, then continue.
        damper_solver = DamperSolver(car, track)
        provisional_step6 = damper_solver.solve(
            front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
            front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
            rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
            fuel_load_l=args.fuel,
            front_heave_nmm=step2.front_heave_nmm,
            rear_third_nmm=step2.rear_third_nmm,
        )
        refined_step2 = heave_solver.solve(
            dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
            dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
            front_pushrod_mm=step1.front_pushrod_offset_mm,
            rear_pushrod_mm=step1.rear_pushrod_offset_mm,
            front_torsion_od_mm=step3.front_torsion_od_mm,
            rear_spring_nmm=step3.rear_spring_rate_nmm,
            rear_spring_perch_mm=step3.rear_spring_perch_mm,
            rear_third_perch_mm=step2.perch_offset_rear_mm,
            fuel_load_l=args.fuel,
            front_camber_deg=car.geometry.front_camber_baseline_deg,
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
                fuel_load_l=args.fuel,
            )
            rear_wheel_rate_nmm = step3.rear_spring_rate_nmm * car.corner_spring.rear_motion_ratio ** 2
            heave_solver.reconcile_solution(
                step1,
                step2,
                step3,
                fuel_load_l=args.fuel,
                front_camber_deg=car.geometry.front_camber_baseline_deg,
                front_hs_damper_nsm=provisional_step6.c_hs_front,
                verbose=False,
            )
            reconcile_ride_heights(
                car, step1, step2, step3,
                fuel_load_l=args.fuel,
                track_name=track.track_name,
                verbose=False,
                surface=surface,
                track=track,
                target_balance=args.balance,
            )

        # ─── Step 4: Anti-Roll Bars ────────────────────────────────────────
        log()
        log("Running Step 4: Anti-Roll Bars...")
        log()

        arb_solver = ARBSolver(car, track)
        step4 = arb_solver.solve(
            front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
        )

        if not args.json and not args.report_only:
            print(step4.summary())

        # ─── Step 5: Wheel Geometry ────────────────────────────────────────
        log()
        log("Running Step 5: Wheel Geometry...")
        log()

        geom_solver = WheelGeometrySolver(car, track)
        step5 = geom_solver.solve(
            k_roll_total_nm_deg=step4.k_roll_front_total + step4.k_roll_rear_total,
            front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
            fuel_load_l=args.fuel,
            camber_confidence=_camber_conf,
        )

        if not args.json and not args.report_only:
            print(step5.summary())

        reconcile_ride_heights(
            car, step1, step2, step3,
            step5=step5,
            fuel_load_l=args.fuel,
            track_name=track.track_name,
            verbose=False,
            surface=surface,
            track=track,
            target_balance=args.balance,
        )

        # ─── Step 6: Dampers ──────────────────────────────────────────────
        log()
        log("Running Step 6: Dampers...")
        log()

        step6 = damper_solver.solve(
            front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
            front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
            rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
            fuel_load_l=args.fuel,
            front_heave_nmm=step2.front_heave_nmm,
            rear_third_nmm=step2.rear_third_nmm,
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
            car=car,
        )
        binding = sensitivity_report.binding_constraints()
        if binding and not args.report_only:
            log()
            log("[constraints] Near-binding constraints:")
            for c in binding:
                log(f"  !! {c.name}: {c.actual_value:.1f} / {c.limit_value:.1f} {c.units} "
                    f"(slack {c.slack_pct:+.1f}%)")
                if c.binding_explanation:
                    log(f"     → {c.binding_explanation}")
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
        log(f"[stint] Skipped: {e}")

    try:
        from solver.sector_compromise import SectorCompromise
        from solver.supporting_solver import compute_brake_bias as _cbias_sec
        _bias_sec, _ = _cbias_sec(car, fuel_load_l=args.fuel)
        sector_result = SectorCompromise(track).analyze(
            step1=step1, step2=step2, step4=step4,
            base_bias_pct=_bias_sec,
        )
    except Exception as e:
        log(f"[sector] Skipped: {e}")

    try:
        from solver.laptime_sensitivity import compute_laptime_sensitivity
        from solver.supporting_solver import compute_brake_bias as _cbias
        _bias, _ = _cbias(car, fuel_load_l=args.fuel)
        sensitivity_result = compute_laptime_sensitivity(
            track=track,
            step1=step1, step2=step2, step3=step3,
            step4=step4, step5=step5,
            brake_bias_pct=_bias,
            step6=step6 if 'step6' in dir() else None,
            supporting=supporting if 'supporting' in dir() else None,
            measured=None,
            wing=getattr(args, 'wing', 17.0),
        )
    except Exception as e:
        log(f"[sensitivity] Skipped: {e}")

    if args.space:
        try:
            from solver.setup_space import explore_setup_space
            space_result = explore_setup_space(
                track=track,
                step1=step1, step2=step2, step3=step3, step4=step4,
                sensitivity=sensitivity_result,
            )
            if not args.report_only and not args.json:
                log()
                log(space_result.summary())
        except Exception as e:
            log(f"[space] Skipped: {e}")

    # ─── Multi-Speed Compromise Analysis (--multi-speed) ──────────────
    if args.multi_speed:
        log("[EXPERIMENTAL] Multi-speed solver is research-only and not validated for production use.")
        try:
            from solver.multi_speed_solver import MultiSpeedSolver
            from car_model.cars import CarModel as _CM
            rear_wheel_rate_nmm = (step3.rear_spring_rate_nmm
                                   * car.corner_spring.rear_motion_ratio ** 2)
            ms_result = MultiSpeedSolver(car, track).analyze(
                front_heave_nmm=step2.front_heave_nmm,
                rear_third_nmm=step2.rear_third_nmm,
                front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
                rear_wheel_rate_nmm=rear_wheel_rate_nmm,
                dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
            )
            if not args.report_only and not args.json:
                log()
                log(ms_result.summary())
        except Exception as e:
            log(f"[multi-speed] Skipped: {e}")

    # ─── Unconstrained Explorer (--explore) ───────────────────────────
    if args.explore:
        log("[EXPERIMENTAL] Setup explorer is research-only and not validated for production use.")
        try:
            from solver.explorer import SetupExplorer
            explore_result = SetupExplorer(car, surface, track).explore(
                target_balance=args.balance,
                fuel_load_l=args.fuel,
            )
            if not args.report_only and not args.json:
                log()
                log(explore_result.summary())
        except Exception as e:
            log(f"[explore] Skipped: {e}")

    # ─── Bayesian Optimization (--bayesian) ───────────────────────────
    if args.bayesian:
        log("[EXPERIMENTAL] Bayesian optimizer is research-only and not validated for production use.")
        try:
            from solver.bayesian_optimizer import BayesianOptimizer
            physics_baseline = {
                "front_heave_nmm": step2.front_heave_nmm,
                "rear_third_nmm": step2.rear_third_nmm,
                "rear_spring_nmm": step3.rear_spring_rate_nmm,
                "front_camber_deg": step5.front_camber_deg,
                "rear_camber_deg": step5.rear_camber_deg,
                "front_arb_blade": step4.front_arb_blade_start,
                "rear_arb_blade": step4.rear_arb_blade_start,
            }
            bo_result = BayesianOptimizer(car, track).optimize(
                physics_baseline=physics_baseline
            )
            if not args.report_only and not args.json:
                log()
                log(bo_result.summary())
        except Exception as e:
            log(f"[bayesian] Skipped: {e}")

    # ─── Legal-Manifold Search (--legal-search) ───────────────────────
    if should_run_legal_manifold_search(
        free_mode=free_mode,
        explicit_search=getattr(args, "legal_search", False),
        search_mode=None,
        scenario_name=resolved_scenario,
    ):
        try:
            from solver.legal_search import run_legal_search

            baseline_brake_bias, _ = compute_brake_bias(car, fuel_load_l=args.fuel)
            baseline_params = {
                "front_pushrod_offset_mm": step1.front_pushrod_offset_mm,
                "rear_pushrod_offset_mm": step1.rear_pushrod_offset_mm,
                "front_heave_spring_nmm": step2.front_heave_nmm,
                "rear_third_spring_nmm": step2.rear_third_nmm,
                "rear_spring_rate_nmm": step3.rear_spring_rate_nmm,
                "front_camber_deg": step5.front_camber_deg,
                "rear_camber_deg": step5.rear_camber_deg,
                "front_arb_blade": step4.front_arb_blade_start,
                "rear_arb_blade": step4.rear_arb_blade_start,
                "brake_bias_pct": baseline_brake_bias,
                "diff_preload_nm": 20.0,
                "front_ls_comp": step6.lf.ls_comp,
                "front_ls_rbd": step6.lf.ls_rbd,
                "front_hs_comp": step6.lf.hs_comp,
                "front_hs_rbd": step6.lf.hs_rbd,
                "rear_ls_comp": step6.lr.ls_comp,
                "rear_ls_rbd": step6.lr.ls_rbd,
                "rear_hs_comp": step6.lr.hs_comp,
                "rear_hs_rbd": step6.lr.hs_rbd,
            }
            search_budget = getattr(args, "search_budget", 1000)
            ls_result = run_legal_search(
                car=car,
                track=track,
                baseline_params=baseline_params,
                budget=search_budget,
                scenario_profile=resolved_scenario,
            )
            selected = ls_result.accepted_best or ls_result.best_robust
            if selected is not None and getattr(selected, "params", None):
                _apply_candidate_params_to_steps(
                    getattr(selected, "params", {}),
                    step1=step1,
                    step2=step2,
                    step3=step3,
                    step4=step4,
                    step5=step5,
                    step6=step6,
                )
            if not args.report_only and not args.json:
                log()
                log(ls_result.summary())
        except Exception as e:
            log(f"[legal-search] Skipped: {e}")

    # ─── Step 6b: Differential (standalone defaults) ──────────────────
    diff_result = None
    try:
        from solver.diff_solver import DiffSolver
        diff_result = DiffSolver.solve_defaults(car, track=track)
        if not args.report_only and not args.json:
            log()
            log(diff_result.summary())
    except Exception as e:
        log(f"[diff] Skipped: {e}")

    # ─── Full Setup Report ─────────────────────────────────────────────
    if not quiet:
        print()
        print()
    # Compute supporting params for standalone report (brake bias, diff defaults)
    _supporting = None
    try:
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

    # ── RunTrace for track-only path ──
    try:
        from output.run_trace import RunTrace
        _rt = RunTrace()
        _rt.record_car_track(car.canonical_name, f"{track.track_name} — {track.track_config}", wing_angle=args.wing)
        _rt.record_solver_path("sequential", reason="Track-only mode (no IBT) — all signals at physics defaults")
        _rt.record_step(1, step1)
        _rt.record_step(2, step2)
        _rt.record_step(3, step3)
        _rt.record_step(4, step4)
        _rt.record_step(5, step5)
        _rt.record_step(6, step6)
        _rt.record_calibration()
        _rt.add_note("Track-only mode: no telemetry signals — solver used physics defaults for all targets.")
        _verbose = getattr(args, "verbose", False)
        _rt.print_report(verbose=_verbose)
    except Exception:
        pass  # RunTrace is non-critical — never break solver output

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
        car=car,
        fuel_l=args.fuel,
        compact=quiet,
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
        # Final garage correlation check before writing .sto
        from output.garage_validator import validate_and_fix_garage_correlation
        garage_warnings = validate_and_fix_garage_correlation(
            car, step1, step2, step3, step5,
            fuel_l=args.fuel, track_name=track.track_name,
        )
        for w in garage_warnings:
            print(f"[garage] {w}")

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
        output = {
            "step1_rake": to_public_output_payload(car.canonical_name, step1),
            "step2_heave": to_public_output_payload(car.canonical_name, step2),
            "step3_corner": to_public_output_payload(car.canonical_name, step3),
            "step4_arb": to_public_output_payload(car.canonical_name, step4),
            "step5_geometry": to_public_output_payload(car.canonical_name, step5),
            "step6_dampers": to_public_output_payload(car.canonical_name, step6),
        }
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
