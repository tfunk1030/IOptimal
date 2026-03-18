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
from types import SimpleNamespace

from aero_model import load_car_surfaces
from aero_model.gradient import compute_gradients
from analyzer.adaptive_thresholds import compute_adaptive_thresholds
from analyzer.diagnose import diagnose
from analyzer.driver_style import analyze_driver, refine_driver_with_measured
from analyzer.extract import extract_measurements
from analyzer.segment import segment_lap
from analyzer.setup_reader import CurrentSetup
from analyzer.setup_schema import apply_live_control_overrides, build_setup_schema
from analyzer.telemetry_truth import summarize_signal_quality
from car_model.cars import get_car
from output.setup_writer import write_sto
from pipeline.report import generate_report
from solver.candidate_search import candidate_to_dict, generate_candidate_families
from solver.arb_solver import ARBSolver
from solver.corner_spring_solver import CornerSpringSolver
from solver.damper_solver import DamperSolver
from solver.heave_solver import HeaveSolver
from solver.modifiers import SolverModifiers, compute_modifiers
from solver.learned_corrections import apply_learned_corrections
from solver.predictor import predict_candidate_telemetry
from solver.rake_solver import RakeSolver, reconcile_ride_heights
from solver.supporting_solver import SupportingSolver
from solver.wheel_geometry_solver import WheelGeometrySolver
from solver.decision_trace import build_parameter_decisions
from solver.full_setup_optimizer import optimize_if_supported
from solver.legality_engine import validate_solution_legality
from solver.solve_chain import SolveChainInputs, run_base_solve
from track_model.build_profile import build_profile
from track_model.ibt_parser import IBTFile


def _compute_single_session_authority(
    diagnosis, session_context, measured, envelope_distance: float, setup_distance: float,
) -> dict:
    """Compute authority score for single-IBT path (no cross-session lap comparison)."""
    assessment_scores = {"fast": 1.0, "competitive": 0.8, "compromised": 0.45, "dangerous": 0.1}
    diag_score = assessment_scores.get(diagnosis.assessment, 0.6)
    context_score = session_context.overall_score if session_context else 0.5

    # Signal quality
    signal_map = getattr(measured, "telemetry_signals", {}) or {}
    signal_score = 0.45
    if signal_map:
        trusted = [s.confidence for s in signal_map.values()
                   if s.quality == "trusted" and s.value is not None]
        proxy = [s.confidence for s in signal_map.values()
                 if s.quality == "proxy" and s.value is not None]
        unresolved = [n for n, s in signal_map.items()
                      if s.quality in {"unknown", "broken"} or s.conflict_state != "clear"]
        signal_score = 0.0
        if trusted:
            signal_score += min(0.75, sum(trusted) / len(trusted) * 0.75)
        if proxy:
            signal_score += min(0.2, sum(proxy) / len(proxy) * 0.2)
        signal_score = max(0.0, signal_score - min(0.2, len(unresolved) * 0.02))

    # State risk
    state_issues = getattr(diagnosis, "state_issues", [])
    state_risk = sum(
        getattr(i, "severity", 0.0) * getattr(i, "confidence", 0.0)
        for i in state_issues[:6]
    )
    state_score = max(0.0, 1.0 - min(1.0, state_risk / 3.0))

    # Combined (no lap component in single-IBT)
    score = diag_score * 0.25 + context_score * 0.25 + signal_score * 0.25 + state_score * 0.25

    # Penalties
    critical = sum(1 for p in diagnosis.problems if getattr(p, "severity", "") == "critical")
    significant = sum(1 for p in diagnosis.problems if getattr(p, "severity", "") == "significant")
    score -= min(0.35, critical * 0.18 + significant * 0.05)
    score -= min(0.18, envelope_distance * 0.035)
    score -= min(0.12, setup_distance * 0.025)
    if session_context and not session_context.comparable_to_baseline:
        score *= 0.82

    return {"session": "S1", "score": round(max(0.0, min(1.0, score)), 3)}


def _find_lap_indices(ibt: IBTFile, lap_num: int) -> tuple[int, int] | None:
    """Find sample indices for a specific lap number."""
    for ln, s, e in ibt.lap_boundaries():
        if ln == lap_num:
            return (s, e)
    return None


def produce(
    args: argparse.Namespace,
    _return_result: bool = False,
    _emit_report: bool = True,
    _compact_report: bool | None = None,
) -> None | dict:
    """Run the full setup production pipeline.

    Args:
        args: Parsed CLI args.
        _return_result: If True, return a dict of solver outputs (used by
            batch/multi-IBT compare mode) instead of returning None.
        _emit_report: If True, print the final engineering report.
        _compact_report: Override compact/full report selection. Defaults to
            compact when ``report_only`` is enabled.
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
                balance_target=getattr(args, "balance", None) or get_car(args.car).default_df_balance_pct,
                sto_path=args.sto,
                json_path=args.json,
                setup_json_path=getattr(args, "setup_json", None),
                verbose=getattr(args, "verbose", False),
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

    # Resolve DF balance target from car model if not explicitly set
    if getattr(args, "balance", None) is None:
        args.balance = car.default_df_balance_pct
        log(f"Using car-specific DF balance target: {args.balance:.2f}%")

    # ── Parse IBT ──
    ibt = IBTFile(ibt_path)
    log(f"IBT: {ibt_path}")
    log(f"  Samples: {ibt.record_count}, Tick rate: {ibt.tick_rate} Hz")

    # Auto-detect min_lap_time if not explicitly set
    if getattr(args, "min_lap_time", None) is None:
        _all_lts = [t for _, t, _, _ in ibt.lap_times(min_time=30.0) if t < 300]
        _fastest = min(_all_lts) if _all_lts else None
        args.min_lap_time = max(60.0, _fastest * 0.95) if _fastest else 60.0

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

    # ── Phase A: Build track profile from IBT (or load saved profile) ──
    track_hint = getattr(args, "track", None)
    saved_profile_path = None
    if track_hint:
        from track_model.profile import TrackProfile
        _candidate = Path("data/tracks") / f"{track_hint.lower().replace(' ', '_')}.json"
        if _candidate.exists():
            saved_profile_path = _candidate
    if saved_profile_path:
        log(f"\nLoading saved track profile: {saved_profile_path}")
        track = TrackProfile.load(saved_profile_path)
        log(f"  Track: {track.track_name} — {track.track_config}")
        log(f"  Best lap: {track.best_lap_time_s:.3f}s")
    else:
        log("\nBuilding track profile from IBT...")
        track = build_profile(ibt_path)
        log(f"  Track: {track.track_name} — {track.track_config}")
        log(f"  Best lap: {track.best_lap_time_s:.3f}s")

    # ── Phase B: Extract telemetry ──
    log("Extracting telemetry measurements...")
    measured = extract_measurements(
        ibt_path,
        car,
        lap=args.lap,
        min_lap_time=getattr(args, "min_lap_time", 108.0),
        outlier_pct=getattr(args, "outlier_pct", 0.115),
    )
    live_override_notes = apply_live_control_overrides(current_setup, measured)
    log(f"  Lap {measured.lap_number}: {measured.lap_time_s:.3f}s")
    for note in live_override_notes:
        log(f"  Live override: {note}")

    setup_schema = build_setup_schema(
        car=car,
        ibt_path=ibt_path,
        current_setup=current_setup,
        measured=measured,
    )
    setup_json_path = getattr(args, "setup_json", None)
    if setup_json_path:
        setup_json_output = Path(setup_json_path)
        setup_json_output.parent.mkdir(parents=True, exist_ok=True)
        with open(setup_json_output, "w", encoding="utf-8") as f:
            json.dump(setup_schema.to_dict(), f, indent=2, default=str)
        log(f"  Setup schema JSON: {setup_json_output}")
        if not args.sto and not args.json and not _return_result:
            return None

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
    from analyzer.driver_style import separate_driver_noise
    driver_comp, setup_comp, noise_reason = separate_driver_noise(driver, measured)
    driver.setup_noise_index = setup_comp
    driver.noise_reasoning = noise_reason
    log(f"  {driver.summary()}")
    log(f"  Noise separation: driver={driver_comp:.2f}, setup={setup_comp:.2f} ({noise_reason})")

    # ── Phase E: Compute adaptive thresholds & diagnose handling ──
    log("Computing adaptive thresholds...")
    adaptive_thresh = compute_adaptive_thresholds(track, car, driver)
    if adaptive_thresh.adaptations:
        for a in adaptive_thresh.adaptations:
            log(f"  {a}")
    else:
        log("  Using baseline thresholds (no adaptations)")

    log("Diagnosing handling...")
    diagnosis = diagnose(
        measured,
        current_setup,
        car,
        thresholds=adaptive_thresh,
        driver=driver,
        corners=corners,
    )
    log(f"  Assessment: {diagnosis.assessment}")
    log(f"  Problems: {len(diagnosis.problems)}")

    # ── Phase E.5: Build session context + query learner for envelope/cluster ──
    from analyzer.context import build_session_context
    session_context = build_session_context(measured, current_setup, diagnosis)
    log(f"  Session context: thermal={session_context.thermal_validity:.2f}, "
        f"pace={session_context.pace_validity:.2f}, overall={session_context.overall_score:.2f}")

    envelope_distance = 0.0
    setup_distance_val = 0.0
    setup_cluster = None
    try:
        from learner.knowledge_store import KnowledgeStore
        from learner.envelope import build_telemetry_envelope, compute_envelope_distance
        from learner.setup_clusters import build_setup_cluster, compute_setup_distance

        _store = KnowledgeStore()
        _obs_list = _store.list_observations(car=car.canonical_name, track=track.track_name)

        _healthy_obs = [
            o for o in _obs_list
            if o.get("diagnosis", {}).get("assessment") in {"fast", "competitive"}
        ]
        if len(_healthy_obs) < 3:
            _healthy_obs = sorted(
                _obs_list,
                key=lambda o: o.get("performance", {}).get("best_lap_time_s", 999),
            )[:min(3, len(_obs_list))]

        if len(_healthy_obs) >= 2:
            _telem_dicts = [o.get("telemetry", {}) for o in _healthy_obs]
            _source_labels = [o.get("session_id", "") for o in _healthy_obs]
            _envelope = build_telemetry_envelope(
                _telem_dicts, source_sessions=_source_labels,
            )
            _env_dist = compute_envelope_distance(measured, _envelope)
            envelope_distance = _env_dist.total_score

            # Setup cluster: map observation keys to CurrentSetup attribute names
            _SETUP_KEY_MAP = {
                "front_pushrod": "front_pushrod_mm",
                "rear_pushrod": "rear_pushrod_mm",
                "torsion_bar_od_mm": "front_torsion_od_mm",
            }
            _setup_dicts = []
            for o in _healthy_obs:
                raw = o.get("setup", {})
                mapped = {}
                for k, v in raw.items():
                    mapped[_SETUP_KEY_MAP.get(k, k)] = v
                _setup_dicts.append(mapped)

            setup_cluster = build_setup_cluster(
                _setup_dicts, member_sessions=_source_labels,
                label="historical healthy cluster",
            )
            _setup_dist = compute_setup_distance(current_setup, setup_cluster)
            setup_distance_val = _setup_dist.distance_score

            log(f"  Learner: {len(_healthy_obs)} historical sessions, "
                f"envelope_dist={envelope_distance:.2f}, setup_dist={setup_distance_val:.2f}")
    except Exception:
        log("  Learner: no historical data available (using defaults)")

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

        # Advisory: compare free-mode L/D when running in pinned mode
        if not args.free:
            try:
                free_step1 = rake_solver.solve(
                    target_balance=target_balance,
                    balance_tolerance=args.tolerance,
                    fuel_load_l=fuel,
                    pin_front_min=False,
                )
                ld_delta = free_step1.ld_ratio - step1.ld_ratio
                if ld_delta > 0.005:
                    log(f"\n  [free-opt] Free optimization L/D: {free_step1.ld_ratio:.3f} "
                        f"vs pinned: {step1.ld_ratio:.3f} ({ld_delta:+.3f})")
                    log(f"  [free-opt] Free front RH: {free_step1.dynamic_front_rh_mm:.1f}mm "
                        f"rear: {free_step1.dynamic_rear_rh_mm:.1f}mm")
                    if ld_delta > 0.02:
                        log("  [free-opt] ** Significant L/D gain — consider --free mode **")
            except Exception:
                pass  # Free opt comparison is advisory only

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
            surface=surface,
            track=track,
            target_balance=target_balance,
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
                surface=surface,
                track=track,
                target_balance=target_balance,
            )

        # Step 4: ARBs (with LLTD offset)
        log("\nRunning Step 4: Anti-Roll Bars...")
        arb_solver = ARBSolver(car, track)
        step4 = arb_solver.solve(
            front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
            lltd_offset=modifiers.lltd_offset,
            current_rear_arb_size=getattr(current_setup, "rear_arb_size", None),
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
            surface=surface,
            track=track,
            target_balance=target_balance,
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
    solve_inputs = SolveChainInputs(
        car=car,
        surface=surface,
        track=track,
        measured=measured,
        driver=driver,
        diagnosis=diagnosis,
        current_setup=current_setup,
        target_balance=target_balance,
        fuel_load_l=fuel,
        wing_angle=wing,
        modifiers=modifiers,
        prediction_corrections={},
        balance_tolerance=args.tolerance,
        pin_front_min=not args.free,
        legacy_solver=getattr(args, "legacy_solver", False),
        camber_confidence=_camber_conf,
    )
    base_solve_result = run_base_solve(solve_inputs)
    step1 = base_solve_result.step1
    step2 = base_solve_result.step2
    step3 = base_solve_result.step3
    step4 = base_solve_result.step4
    step5 = base_solve_result.step5
    step6 = base_solve_result.step6
    supporting = base_solve_result.supporting
    legal_validation = base_solve_result.legal_validation
    decision_trace = base_solve_result.decision_trace
    solve_notes = list(base_solve_result.notes)

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
    supporting = base_solve_result.supporting
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
    # Only for indexed params where we can't map index → physical stiffness.
    # Solver computes dampers, geometry, brake/diff/TC from telemetry.
    if car.canonical_name == "ferrari":
        _cs = current_setup
        # Pushrod — keep IBT values
        step1.front_pushrod_offset_mm = _cs.front_pushrod_mm
        step1.rear_pushrod_offset_mm = _cs.rear_pushrod_mm
        # Springs — indexed dropdowns, can't map to physical stiffness
        step2.front_heave_nmm = _cs.front_heave_nmm
        step2.perch_offset_front_mm = _cs.front_heave_perch_mm
        step2.rear_third_nmm = _cs.rear_third_nmm
        step2.perch_offset_rear_mm = _cs.rear_third_perch_mm
        step3.front_torsion_od_mm = _cs.front_torsion_od_mm
        step3.rear_spring_rate_nmm = _cs.rear_spring_nmm
        step3.rear_spring_perch_mm = 0.0
        # ARBs — stiffness uncalibrated, pass through size (solver computes blade)
        step4.front_arb_size = _cs.front_arb_size
        step4.rear_arb_size = _cs.rear_arb_size
        # Geometry, dampers, brake/diff/TC — solver computes from telemetry

    # ── Phase J: Output ──
    legal_validation = validate_solution_legality(
        car=car,
        track_name=track.track_name,
        step1=step1,
        step2=step2,
        step3=step3,
        fuel_l=fuel,
        step5=step5,
    )
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
        fallback_reasons=list(getattr(measured, "fallback_reasons", []) or []),
    )

    base_solve_result.step1 = step1
    base_solve_result.step2 = step2
    base_solve_result.step3 = step3
    base_solve_result.step4 = step4
    base_solve_result.step5 = step5
    base_solve_result.step6 = step6
    base_solve_result.supporting = supporting
    base_solve_result.legal_validation = legal_validation
    base_solve_result.decision_trace = decision_trace

    authority_score = _compute_single_session_authority(
        diagnosis, session_context, measured, envelope_distance, setup_distance_val,
    )
    single_session = SimpleNamespace(
        label="S1",
        setup=current_setup,
        measured=measured,
        driver=driver,
        diagnosis=diagnosis,
        session_context=session_context,
    )
    generated_candidates = generate_candidate_families(
        authority_session=single_session,
        best_session=single_session,
        overhaul_assessment=getattr(diagnosis, "overhaul_assessment", None),
        authority_score=authority_score,
        envelope_distance=envelope_distance,
        setup_distance=setup_distance_val,
        base_result=base_solve_result,
        solve_inputs=solve_inputs,
        setup_cluster=setup_cluster,
        current_session=single_session,
        aggregate_measured=None,  # single-IBT: falls back to authority_session.measured
    )
    selected_candidate = next((candidate for candidate in generated_candidates if candidate.selected), None)
    selected_candidate_applied = False
    if selected_candidate is not None:
        solve_notes.append(
            f"Candidate family selected: {selected_candidate.family} "
            f"(score {selected_candidate.score.total if selected_candidate.score else 0.0:.3f})"
        )
        if selected_candidate.selectable and selected_candidate.result is not None:
            selected_candidate_applied = True
            step1 = selected_candidate.result.step1
            step2 = selected_candidate.result.step2
            step3 = selected_candidate.result.step3
            step4 = selected_candidate.result.step4
            step5 = selected_candidate.result.step5
            step6 = selected_candidate.result.step6
            supporting = selected_candidate.result.supporting
            legal_validation = selected_candidate.result.legal_validation
            decision_trace = selected_candidate.result.decision_trace
            solve_notes.append(
                f"Applied rematerialized {selected_candidate.family} candidate result to final report/JSON/export payloads."
            )

    if args.sto and car.canonical_name == "ferrari":
        solve_notes.append(
            "Ferrari native .sto export is disabled in read-first mode; no setup file was written."
        )
        print("\nFerrari native .sto export is disabled in read-first mode; use --setup-json for setup inspection.")
    elif args.sto:
        # Final garage correlation check before writing .sto
        from output.garage_validator import validate_and_fix_garage_correlation
        garage_warnings = validate_and_fix_garage_correlation(
            car, step1, step2, step3, step5,
            fuel_l=fuel, track_name=track.track_name,
        )
        for w in garage_warnings:
            print(f"[garage] {w}")

        _extra_kw = {}
        if car.canonical_name == "ferrari":
            # Indexed params: pass through from IBT (can't map index → physical)
            _extra_kw["front_tb_turns"] = current_setup.torsion_bar_turns
            _extra_kw["rear_tb_turns"] = current_setup.rear_torsion_bar_turns
            # Supporting params: solver computes from telemetry
            _extra_kw["tyre_pressure_kpa"] = supporting.tyre_cold_fl_kpa
            _extra_kw["brake_bias_pct"] = supporting.brake_bias_pct
            _extra_kw["brake_bias_target"] = supporting.brake_bias_target
            _extra_kw["brake_bias_migration"] = supporting.brake_bias_migration
            _extra_kw["front_master_cyl_mm"] = supporting.front_master_cyl_mm
            _extra_kw["rear_master_cyl_mm"] = supporting.rear_master_cyl_mm
            _extra_kw["pad_compound"] = supporting.pad_compound
            # Ferrari diff ramp uses labels ("More Locking"/"Less Locking")
            if supporting.diff_ramp_coast >= 45:
                _extra_kw["diff_coast_drive_ramp"] = "Less Locking"
            else:
                _extra_kw["diff_coast_drive_ramp"] = "More Locking"
            _extra_kw["diff_clutch_plates"] = supporting.diff_clutch_plates
            _extra_kw["diff_preload_nm"] = supporting.diff_preload_nm
            _extra_kw["tc_gain"] = supporting.tc_gain
            _extra_kw["tc_slip"] = supporting.tc_slip
        else:
            _extra_kw["tyre_pressure_kpa"] = supporting.tyre_cold_fl_kpa
            _extra_kw["brake_bias_pct"] = supporting.brake_bias_pct
            _extra_kw["brake_bias_target"] = supporting.brake_bias_target
            _extra_kw["brake_bias_migration"] = supporting.brake_bias_migration
            _extra_kw["front_master_cyl_mm"] = supporting.front_master_cyl_mm
            _extra_kw["rear_master_cyl_mm"] = supporting.rear_master_cyl_mm
            _extra_kw["pad_compound"] = supporting.pad_compound
            _extra_kw["diff_coast_drive_ramp"] = f"{supporting.diff_ramp_coast}/{supporting.diff_ramp_drive}"
            _extra_kw["diff_clutch_plates"] = supporting.diff_clutch_plates
            _extra_kw["diff_preload_nm"] = supporting.diff_preload_nm
            _extra_kw["tc_gain"] = supporting.tc_gain
            _extra_kw["tc_slip"] = supporting.tc_slip

        sto_path = write_sto(
            car_name=car.name,
            track_name=f"{track.track_name} — {track.track_config}",
            wing=wing,
            fuel_l=fuel,
            step1=step1, step2=step2, step3=step3,
            step4=step4, step5=step5, step6=step6,
            output_path=args.sto,
            car_canonical=car.canonical_name,
            **_extra_kw,
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
            "signal_quality_summary": summarize_signal_quality(measured),
            "telemetry_bundle": measured.telemetry_bundle,
            "setup_schema": setup_schema.to_dict(),
            "generated_candidates": [candidate_to_dict(candidate) for candidate in generated_candidates],
            "selected_candidate_family": getattr(selected_candidate, "family", None),
            "selected_candidate_score": (
                selected_candidate.score.total
                if selected_candidate is not None and selected_candidate.score is not None
                else None
            ),
            "selected_candidate_applied": selected_candidate_applied,
            "legal_validation": legal_validation.to_dict() if legal_validation is not None else None,
            "decision_trace": [decision.to_dict() for decision in decision_trace],
            "solver_notes": solve_notes,
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
    report_compact = quiet if _compact_report is None else _compact_report

    if _emit_report and not quiet:
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
        prediction_corrections={},
        selected_candidate_family=getattr(selected_candidate, "family", None),
        selected_candidate_score=(
            selected_candidate.score.total
            if selected_candidate is not None and selected_candidate.score is not None
            else None
        ),
        solve_context_lines=solve_notes,
        compact=report_compact,
    )
    if _emit_report:
        print(report)

    # ── Build return dict if requested (multi-IBT batch mode) ──
    _result: dict | None = None
    if _return_result:
        _result = {
            "car": car,
            "track": track,
            "report": report,
            "lap_time_s": measured.lap_time_s,
            "lap_number": measured.lap_number,
            "measured": measured,
            "driver": driver,
            "diagnosis": diagnosis,
            "corners": corners,
            "modifiers": modifiers,
            "current_setup": current_setup,
            "setup_schema": setup_schema,
            "wing": wing,
            "fuel_l": fuel,
            "target_balance": target_balance,
            "step1": step1,
            "step2": step2,
            "step3": step3,
            "step4": step4,
            "step5": step5,
            "step6": step6,
            "supporting": supporting,
            "generated_candidates": generated_candidates,
            "selected_candidate_family": getattr(selected_candidate, "family", None),
            "selected_candidate_score": (
                selected_candidate.score.total
                if selected_candidate is not None and selected_candidate.score is not None
                else None
            ),
            "selected_candidate_applied": selected_candidate_applied,
            "legal_validation": legal_validation,
            "decision_trace": decision_trace,
            "solver_notes": solve_notes,
        }

    # ── Phase L: Auto-learn (default: on, --no-learn disables) ──
    if not getattr(args, "no_learn", False):
        try:
            from learner.ingest import ingest_ibt
            from learner.knowledge_store import KnowledgeStore

            predicted_telemetry, _prediction_conf = predict_candidate_telemetry(
                current_setup=current_setup,
                baseline_measured=measured,
                step1=step1,
                step2=step2,
                step3=step3,
                step4=step4,
                step5=step5,
                step6=step6,
                supporting=supporting,
            )
            # Build solver predictions dict for the feedback loop.
            # These are what the solver PREDICTED for this session's telemetry.
            solver_predictions = {
                "front_rh_std_mm": getattr(step2, "front_rh_sigma_mm", 0.0),
                "rear_rh_std_mm": getattr(step2, "rear_rh_sigma_mm", 0.0),
                "lltd_predicted": getattr(step4, "lltd_achieved", 0.0),
                "body_roll_predicted_deg_per_g": getattr(step4, "roll_gradient_deg_per_g", 0.0),
                "front_bottoming_predicted": getattr(step2, "bottoming_events_front", 0),
                "front_heave_travel_used_pct": predicted_telemetry.front_heave_travel_used_pct,
                "front_excursion_mm": predicted_telemetry.front_excursion_mm,
                "braking_pitch_deg": predicted_telemetry.braking_pitch_deg,
                "front_lock_p95": predicted_telemetry.front_lock_p95,
                "rear_power_slip_p95": predicted_telemetry.rear_power_slip_p95,
                "body_slip_p95_deg": predicted_telemetry.body_slip_p95_deg,
                "understeer_low_deg": predicted_telemetry.understeer_low_deg,
                "understeer_high_deg": predicted_telemetry.understeer_high_deg,
                "front_pressure_hot_kpa": predicted_telemetry.front_pressure_hot_kpa,
                "rear_pressure_hot_kpa": predicted_telemetry.rear_pressure_hot_kpa,
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


def produce_result(
    args: argparse.Namespace,
    *,
    emit_report: bool = True,
    compact_report: bool | None = None,
) -> dict:
    """Run the full pipeline and return structured results for comparison.

    Wraps ``produce(_return_result=True)`` and returns a dict with solver,
    diagnosis, and report data for downstream callers.

    Args:
        args: Parsed pipeline args namespace.
        emit_report: If True, print the final report.
        compact_report: Override compact/full report selection.

    Returns:
        dict with keys: lap_time_s, lap_number, current_setup,
        step1..step6, supporting, report
    """
    result = produce(
        args,
        _return_result=True,
        _emit_report=emit_report,
        _compact_report=compact_report,
    )
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
    parser.add_argument("--track", type=str, default=None,
                        help="Track name hint (e.g., silverstone). If a saved profile exists at "
                             "data/tracks/{name}.json it will be loaded; otherwise the track "
                             "profile is derived from the IBT as usual.")
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
    parser.add_argument("--setup-json", type=str, default=None,
                        help="Save the canonical setup schema / Ferrari LDX correlation JSON and exit if used alone")
    parser.add_argument("--report-only", action="store_true",
                        help="Print only the final report (skip per-step details)")
    parser.add_argument("--no-learn", action="store_true",
                        help="Disable auto-learning (skip empirical corrections and session ingest)")
    parser.add_argument("--legacy-solver", action="store_true",
                        help="Force the legacy sequential solver path for BMW/Sebring validation")
    # Lap selection / filtering
    parser.add_argument("--min-lap-time", type=float, default=None, dest="min_lap_time",
                        help="Minimum valid lap time in seconds (default: auto-detected as "
                             "fastest observed lap × 0.95, floored at 60s). Set explicitly to "
                             "override — e.g. 108.0 for BMW/Sebring.")
    parser.add_argument("--outlier-pct", type=float, default=0.115, dest="outlier_pct",
                        help="Max fractional deviation above median to accept (default: 0.115 = 11.5%%). "
                             "Drops anomalously slow laps. Pass 0 to disable.")
    # Stint analysis
    parser.add_argument("--stint", action="store_true",
                        help="Enable stint analysis: analyze all qualifying laps, "
                             "run solver at start/mid/end conditions, produce compromise setup")
    parser.add_argument("--stint-threshold", type=float, default=1.5, dest="stint_threshold",
                        help="Max %% slower than fastest lap to include (default: 1.5)")
    parser.add_argument("--verbose", action="store_true",
                        help="Show full reasoning dump (multi-IBT mode)")
    # Legacy flags (kept for backward-compat; no-op since auto is default)
    parser.add_argument("--learn", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--auto-learn", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    produce(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
