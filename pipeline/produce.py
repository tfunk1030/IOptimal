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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from aero_model import load_car_surfaces
from aero_model.gradient import compute_gradients
from analyzer.adaptive_thresholds import compute_adaptive_thresholds
from analyzer.diagnose import diagnose
from analyzer.driver_style import analyze_driver, refine_driver_with_measured
from analyzer.extract import extract_measurements
from analyzer.segment import segment_lap
from analyzer.stint_analysis import build_stint_dataset, dataset_to_evolution
from analyzer.setup_reader import CurrentSetup
from analyzer.setup_schema import apply_live_control_overrides, build_setup_schema
from analyzer.telemetry_truth import summarize_signal_quality
from car_model.cars import get_car
from car_model.calibration_gate import CalibrationGate
from output.report import to_public_output_payload
from car_model.setup_registry import public_output_value
from output.setup_writer import write_sto
from pipeline.report import generate_report
from solver.candidate_search import canonical_params_to_overrides, candidate_to_dict, generate_candidate_families
from solver.modifiers import compute_modifiers
from solver.learned_corrections import apply_learned_corrections
from solver.predictor import predict_candidate_telemetry
from solver.scenario_profiles import resolve_scenario_name, should_run_legal_manifold_search
from solver.bmw_coverage import (
    build_parameter_coverage,
    build_search_baseline,
    build_telemetry_coverage,
)
from solver.bmw_rotation_search import preserve_candidate_rotation_controls
from solver.decision_trace import build_parameter_decisions
from solver.legality_engine import validate_solution_legality
from solver.solve_chain import SolveChainInputs, run_base_solve
from solver.stint_reasoner import solve_stint_compromise
from track_model.build_profile import build_profile
from track_model.ibt_parser import IBTFile


class PipelineInputError(RuntimeError):
    """User-facing pipeline input error."""


def _normalize_grid_search_params_for_overrides(params: dict[str, object] | None) -> dict[str, object]:
    """Normalize grid-search param aliases to canonical override keys.

    Grid search can surface keys that don't match solve-chain field names
    directly (e.g. *_spring_nmm, *_arb_blade, *_toe_deg aliases). Convert those
    into the canonical param set expected by canonical_params_to_overrides().
    """
    raw = dict(params or {})
    normalized = dict(raw)
    alias_map = {
        "front_heave_nmm": "front_heave_spring_nmm",
        "rear_third_nmm": "rear_third_spring_nmm",
        "rear_spring_nmm": "rear_spring_rate_nmm",
        "front_arb_blade_start": "front_arb_blade",
        "rear_arb_blade_start": "rear_arb_blade",
        # Pipeline/solver canonical toe units are mm. If a *_toe_deg alias is
        # emitted by search output, route it to toe_mm for override consumption.
        "front_toe_deg": "front_toe_mm",
        "rear_toe_deg": "rear_toe_mm",
    }
    for source_key, target_key in alias_map.items():
        if source_key in raw and target_key not in normalized:
            normalized[target_key] = raw[source_key]
    return normalized


def _wrap_no_valid_laps_error(
    exc: Exception,
    *,
    ibt_path: str,
    car_name: str,
    track_hint: str | None = None,
) -> Exception:
    if "No valid laps found in IBT file" not in str(exc):
        return exc
    track_example = track_hint or "<track>"
    return PipelineInputError(
        f"No usable complete timed lap was detected in IBT lap telemetry: {Path(ibt_path).name}. "
        "The file can still contain a full session recording, but pipeline.produce needs at least one valid completed lap "
        "to build the track profile and extract telemetry. "
        f"If you only need a track-profile-based baseline, run "
        f"`python -m solver.solve --car {car_name} --track {track_example} ...` instead."
    )


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

    # In-car adjustment penalty (frequent changes reduce telemetry authority)
    total_adjustments = (
        getattr(measured, "brake_bias_adjustments", 0) +
        getattr(measured, "tc_adjustments", 0)
    )
    if total_adjustments > 5:
        adjustment_penalty = min(0.5, 0.05 * total_adjustments)
        score *= max(0.5, 1.0 - adjustment_penalty)

    return {"session": "S1", "score": round(max(0.0, min(1.0, score)), 3)}


def _find_lap_indices(ibt: IBTFile, lap_num: int) -> tuple[int, int] | None:
    """Find sample indices for a specific lap number."""
    for ln, s, e in ibt.lap_boundaries():
        if ln == lap_num:
            return (s, e)
    return None


def _stint_selection_payload(dataset) -> dict | None:
    if dataset is None:
        return None
    return {
        "mode": dataset.stint_select,
        "segments": [
            {
                "segment_id": segment.segment_id,
                "start_lap": segment.start_lap,
                "end_lap": segment.end_lap,
                "lap_count": segment.lap_count,
                "source_label": segment.source_label,
                "break_reasons": list(segment.break_reasons),
            }
            for segment in dataset.segments
        ],
        "selected_segments": [
            {
                "segment_id": segment.segment_id,
                "start_lap": segment.start_lap,
                "end_lap": segment.end_lap,
                "lap_count": segment.lap_count,
                "source_label": segment.source_label,
            }
            for segment in dataset.selected_segments
        ],
        "notes": list(dataset.selection_notes),
    }


def _stint_lap_payload(dataset) -> list[dict]:
    if dataset is None:
        return []
    return [
        {
            "lap_number": lap.lap_number,
            "lap_time_s": lap.lap_time_s,
            "fuel_level_l": lap.fuel_level_l,
            "progress": lap.progress,
            "phase": lap.phase,
            "quality": {
                "status": lap.quality.status,
                "direct_weight": lap.quality.direct_weight,
                "trend_weight": lap.quality.trend_weight,
                "flags": list(lap.quality.flags),
            },
            "selected_for_evaluation": lap.selected_for_evaluation,
            "source_label": lap.source_label,
        }
        for lap in dataset.usable_laps
    ]


def _apply_calibration_step_blocks(
    *,
    step1,
    step2,
    step3,
    step4,
    step5,
    step6,
    blocked_steps: set[int],
) -> tuple[object, object, object, object, object, object]:
    """Null blocked solver steps so uncalibrated values never leak to outputs."""
    if not blocked_steps:
        return step1, step2, step3, step4, step5, step6
    if 1 in blocked_steps:
        step1 = None
    if 2 in blocked_steps:
        step2 = None
    if 3 in blocked_steps:
        step3 = None
    if 4 in blocked_steps:
        step4 = None
    if 5 in blocked_steps:
        step5 = None
    if 6 in blocked_steps:
        step6 = None
    return step1, step2, step3, step4, step5, step6


def _resolve_scenario_profile(args: argparse.Namespace) -> str:
    explicit = getattr(args, "scenario_profile", None)
    if explicit:
        return resolve_scenario_name(explicit)
    if getattr(args, "stint", False):
        if getattr(args, "stint_select", "all") == "last":
            return "sprint"
        return "race"
    # --mode aggressive → use quali scenario (all weights active, enables k-NN)
    # --mode safe (default) → use single_lap_safe scenario
    if getattr(args, "mode", "safe") == "aggressive":
        return resolve_scenario_name("quali")
    return resolve_scenario_name(getattr(args, "objective_profile", None))


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
    free_mode = bool(getattr(args, "free", False))
    scenario_profile_name = _resolve_scenario_profile(args)

    # ── Resolve car name early (multi-IBT branch needs it) ──
    from car_model.registry import resolve_car as _resolve_car_name_fn
    _car_id = _resolve_car_name_fn(args.car)
    if _car_id:
        args.car = _car_id.canonical

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
                explore_legal_space=getattr(args, "explore_legal_space", False),
                search_budget=getattr(args, "search_budget", 1000),
                keep_weird=getattr(args, "keep_weird", False),
                scenario_profile=scenario_profile_name,
                stint=getattr(args, "stint", False),
                stint_select=getattr(args, "stint_select", "all"),
                stint_max_laps=getattr(args, "stint_max_laps", 40),
                stint_threshold=getattr(args, "stint_threshold", 1.5),
            )
            return None
        ibt_path = ibt_arg[0]
    else:
        ibt_path = ibt_arg  # backward compat for programmatic callers

    quiet = bool(getattr(args, "report_only", False))

    def log(message: str = "") -> None:
        if not quiet:
            print(message)

    # ── Load car model and apply calibration data if available ──
    car = get_car(args.car)
    try:
        from car_model.auto_calibrate import load_calibrated_models, apply_to_car
        cal_models = load_calibrated_models(car.canonical_name)
        if cal_models:
            notes = apply_to_car(car, cal_models)
            for note in notes:
                log(f"  [calibration] {note}")
    except FileNotFoundError:
        pass  # No calibration data exists — use defaults from cars.py
    except Exception as exc:
        # Keep pipeline behavior resilient but never swallow calibration failures.
        log(f"  [WARNING] Calibration loading failed: {exc}")
        log("  [WARNING] Using car defaults from cars.py")
    log(f"Car: {car.name}")

    # ── Run trace (data provenance) ──
    from output.run_trace import RunTrace
    run_trace = RunTrace()

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
    current_setup = CurrentSetup.from_ibt(ibt, car_canonical=car.canonical_name)
    wing = args.wing or current_setup.wing_angle_deg
    fuel = args.fuel or current_setup.fuel_l or 89.0
    log(f"  Wing: {wing}°, Fuel: {fuel:.0f} L")

    # ── Apply learned corrections (default: auto, --no-learn to disable) ──
    learned = None
    n_sessions = 0
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
            # Only apply learned m_eff if no calibrated value exists from auto_calibrate.
            # The calibrated m_eff (from heave sweep) is more reliable than the empirical
            # estimate (from lap-wide statistics that include kerb events and braking pitch).
            # Only apply learned m_eff if the learned value is within 2x of the car's
            # existing value. The empirical m_eff from lap-wide statistics includes kerb
            # events, braking pitch, and direction changes that inflate the estimate.
            # A calibrated m_eff (from heave sweep or cars.py) is more reliable.
            # If the learned value is >2x the existing, it's contaminated.
            if learned.heave_m_eff_front_kg is not None:
                _existing_front = car.heave_spring.front_m_eff_kg
                if _existing_front > 0 and learned.heave_m_eff_front_kg <= _existing_front * 2.0:
                    car.heave_spring.front_m_eff_kg = learned.heave_m_eff_front_kg
            if learned.heave_m_eff_rear_kg is not None:
                _existing_rear = car.heave_spring.rear_m_eff_kg
                if _existing_rear > 0 and learned.heave_m_eff_rear_kg <= _existing_rear * 2.0:
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
        # Try both naming conventions: "{slug}.json" (legacy) and "{slug}_{config}.json" (autosave)
        _hint_slug = track_hint.lower().replace(" ", "_")
        for _pattern in [f"{_hint_slug}_*.json", f"{_hint_slug}.json"]:
            _matches = sorted(Path("data/tracks").glob(_pattern))
            if _matches:
                saved_profile_path = _matches[0]
                break
    if saved_profile_path:
        log(f"\nLoading saved track profile: {saved_profile_path}")
        track = TrackProfile.load(saved_profile_path)
        log(f"  Track: {track.track_name} — {track.track_config}")
        log(f"  Best lap: {track.best_lap_time_s:.3f}s")
    else:
        log("\nBuilding track profile from IBT...")
        try:
            track = build_profile(ibt_path)
        except Exception as exc:
            wrapped = _wrap_no_valid_laps_error(
                exc,
                ibt_path=ibt_path,
                car_name=args.car,
                track_hint=track_hint,
            )
            if wrapped is not exc:
                raise wrapped from None
            raise
        log(f"  Track: {track.track_name} — {track.track_config}")
        log(f"  Best lap: {track.best_lap_time_s:.3f}s")
        run_trace.record_car_track(car.canonical_name, track.track_name, wing_angle=getattr(args, "wing", None))
        run_trace.record_calibration()

        # Auto-save track profile for future runs (avoids rebuilding each time)
        from car_model.registry import track_slug as _reg_track_slug
        _save_slug = _reg_track_slug(track.track_name, track.track_config or "default")
        _save_path = Path("data/tracks") / f"{_save_slug}.json"
        _save_path.parent.mkdir(parents=True, exist_ok=True)
        # Overwrite when: missing, stub (<2 KB), or new profile has a faster lap
        _should_save = not _save_path.exists() or _save_path.stat().st_size < 2000
        if not _should_save and _save_path.exists():
            try:
                import json as _json
                _existing = _json.loads(_save_path.read_text())
                _existing_lap = _existing.get("best_lap_time_s", float("inf"))
                if track.best_lap_time_s < _existing_lap:
                    _should_save = True
            except Exception:
                pass
        if _should_save:
            track.save(_save_path)
            log(f"  Track profile saved: {_save_path}")

    # ── Phase B: Extract telemetry ──
    log("Extracting telemetry measurements...")
    try:
        measured = extract_measurements(
            ibt_path,
            car,
            lap=args.lap,
            min_lap_time=getattr(args, "min_lap_time", 108.0),
            outlier_pct=getattr(args, "outlier_pct", 0.115),
        )
    except Exception as exc:
        wrapped = _wrap_no_valid_laps_error(
            exc,
            ibt_path=ibt_path,
            car_name=args.car,
            track_hint=track_hint,
        )
        if wrapped is not exc:
            raise wrapped from None
        raise
    live_override_notes = apply_live_control_overrides(current_setup, measured)
    log(f"  Lap {measured.lap_number}: {measured.lap_time_s:.3f}s")
    for note in live_override_notes:
        log(f"  Live override: {note}")
    run_trace.record_signals(measured)

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
    stint_dataset = None
    stint_evolution = None
    if getattr(args, "stint", False):
        log("\nBuilding full-stint dataset...")
        stint_dataset = build_stint_dataset(
            ibt_path=ibt_path,
            car=car,
            stint_select=getattr(args, "stint_select", "longest"),
            stint_max_laps=getattr(args, "stint_max_laps", 40),
            threshold_pct=getattr(args, "stint_threshold", 1.5),
            min_lap_time=getattr(args, "min_lap_time", 108.0),
            ibt=ibt,
        )
        stint_evolution = dataset_to_evolution(stint_dataset)
        log(
            f"  Selected {len(stint_dataset.selected_segments)} segment(s), "
            f"{len(stint_dataset.usable_laps)} usable laps, "
            f"{len(stint_dataset.evaluation_laps)} evaluation laps"
        )
        if stint_dataset.selected_segments:
            ranges = ", ".join(
                f"{segment.start_lap}-{segment.end_lap}"
                for segment in stint_dataset.selected_segments
            )
            log(f"  Segment range(s): {ranges}")
        if stint_dataset.selection_notes:
            for note in stint_dataset.selection_notes[:3]:
                log(f"  {note}")
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
    # Pass start/end so throttle/steering analysis uses the same lap as
    # extract_measurements() and segment_lap() — avoids metric mismatch.
    driver = analyze_driver(
        ibt, corners, car,
        tick_rate=ibt.tick_rate,
        selected_lap_start=start,
        selected_lap_end=end,
    )
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
    modifiers = compute_modifiers(diagnosis, driver, measured, car=car)
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

    # All solve orchestration is handled by run_base_solve() below.

    # ── Phase H.5: Multi-solve stint compromise (if --stint) ──
    # Load known-bad setup fingerprints from learner (if available)
    # Observations with validation_failed=True contribute hard-veto clusters
    # that prevent the solver from re-recommending the same setup cluster.
    failed_clusters = []
    try:
        from learner.knowledge_store import KnowledgeStore
        from solver.setup_fingerprint import ValidationCluster

        ks = KnowledgeStore()
        obs_list = ks.list_observations(
            car=car.canonical_name,
            track=track.track_name.split()[0].lower(),
        )
        for obs_data in obs_list:
            if obs_data.get("validation_failed", False):
                fp_data = obs_data.get("setup_fingerprint")
                if fp_data and isinstance(fp_data, dict):
                    try:
                        from solver.setup_fingerprint import SetupFingerprint
                        fp = SetupFingerprint(**fp_data)
                        failed_clusters.append(
                            ValidationCluster(
                                fingerprint=fp,
                                validated_failed=True,
                                penalty_mode="hard",
                            )
                        )
                    except (TypeError, KeyError):
                        pass  # Malformed fingerprint data — skip
        if failed_clusters:
            log(f"[veto] Loaded {len(failed_clusters)} hard-veto clusters from learner store")
    except Exception:
        # Learner not available or no data — skip veto mechanism gracefully
        pass

    # ── Calibration gate ──
    _track_short = track.track_name.split()[0].lower() if hasattr(track, "track_name") else args.track
    cal_gate = CalibrationGate(car, _track_short)
    cal_report = cal_gate.full_report()
    _steps_blocked: set[int] = set()

    if cal_report.any_blocked:
        log()
        log(f"[calibration] {cal_gate.summary_line()}")
        for sr in cal_report.step_reports:
            if sr.blocked:
                _steps_blocked.add(sr.step_number)
                log(sr.instructions_text())
        log()

    # Surface weak steps prominently — these produce output but the
    # underlying calibration is below threshold (R²<0.85, manual override
    # contradicts auto-cal, etc.). User must read warnings.
    if cal_report.any_weak:
        log()
        log("=" * 70)
        log("WEAK CALIBRATION DETECTED — output is produced but verify before use")
        log("=" * 70)
        for sr in cal_report.step_reports:
            if sr.weak_block:
                log(f"  Step {sr.step_number} ({sr.step_name}):")
                for sub in sr.missing:
                    log(f"    {sub.name}: {sub.confidence_label()} {sub.source}")
                    for w in sub.warnings:
                        log(f"      ! {w}")
            elif sr.weak_upstream:
                log(
                    f"  Step {sr.step_number} ({sr.step_name}): "
                    f"inherits weak upstream input from Step {sr.weak_upstream_step}"
                )
        log("=" * 70)
        log()

    # Always show confidence report — full per-subsystem provenance.
    _confidence_text = cal_report.format_confidence_report(cal_gate.subsystems())
    if _confidence_text:
        log()
        log(_confidence_text)
        log()

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
        pin_front_min=True,
        scenario_profile=scenario_profile_name,
        legacy_solver=getattr(args, "legacy_solver", False),
        camber_confidence=_camber_conf,
        failed_validation_clusters=failed_clusters,
        corners=corners,
        optimization_mode=getattr(args, "opt_mode", "driver") or "driver",
    )
    base_solve_result = run_base_solve(solve_inputs)
    step1 = base_solve_result.step1
    step2 = base_solve_result.step2
    step3 = base_solve_result.step3
    step4 = base_solve_result.step4
    step5 = base_solve_result.step5
    step6 = base_solve_result.step6
    supporting = base_solve_result.supporting

    # ── Enforce calibration gate on solver outputs ──
    # Null-out any steps that were blocked by calibration.  run_base_solve()
    # runs unconditionally (it doesn't know about the gate), so we must
    # prevent uncalibrated step values from reaching .sto / JSON output.
    # --force bypasses this: all steps pass through with UNCALIBRATED warnings.
    _force = getattr(args, "force", False)
    if _force and _steps_blocked:
        log()
        log("=" * 70)
        log("⚠  --force: BYPASSING CALIBRATION GATE")
        log(f"   Steps {sorted(_steps_blocked)} are UNCALIBRATED — output is ESTIMATE ONLY")
        log("   Do NOT trust these values without independent verification.")
        log("=" * 70)
        log()
        _steps_blocked = set()  # clear so nothing gets nulled

    step1, step2, step3, step4, step5, step6 = _apply_calibration_step_blocks(
        step1=step1,
        step2=step2,
        step3=step3,
        step4=step4,
        step5=step5,
        step6=step6,
        blocked_steps=_steps_blocked,
    )
    legal_validation = base_solve_result.legal_validation
    decision_trace = base_solve_result.decision_trace
    solve_notes = list(base_solve_result.notes)

    # ── Record solver path and steps in RunTrace ──
    _rt_path = getattr(base_solve_result, "solver_path", "sequential")
    _rt_reason = (
        "BMW/Sebring garage model active — constrained SciPy optimizer used"
        if getattr(base_solve_result, "optimizer_used", False)
        else "Sequential 6-step physics solver"
    )
    run_trace.record_solver_path(_rt_path, reason=_rt_reason)
    run_trace.record_step(1, step1, physics_override=False)
    run_trace.record_step(2, step2, physics_override=False)
    run_trace.record_step(3, step3, physics_override=False)
    run_trace.record_step(4, step4, physics_override=False)
    run_trace.record_step(5, step5)
    run_trace.record_step(6, step6)
    run_trace.record_step(7, supporting)
    run_trace.record_legality(legal_validation)

    stint_compromise_info: list[str] = []
    stint_solve = None
    if stint_dataset is not None and len(stint_dataset.usable_laps) >= 5:
        log("\nRunning full-stint compromise solve...")
        stint_solve = solve_stint_compromise(
            dataset=stint_dataset,
            base_inputs=solve_inputs,
            base_result=base_solve_result,
        )
        step1 = stint_solve.result.step1
        step2 = stint_solve.result.step2
        step3 = stint_solve.result.step3
        step4 = stint_solve.result.step4
        step5 = stint_solve.result.step5
        step6 = stint_solve.result.step6
        supporting = stint_solve.result.supporting
        legal_validation = stint_solve.result.legal_validation
        decision_trace = stint_solve.result.decision_trace
        base_solve_result = stint_solve.result
        stint_compromise_info = list(stint_solve.notes)
        # Re-apply gate after stint rematerialization so blocked steps stay blocked.
        step1, step2, step3, step4, step5, step6 = _apply_calibration_step_blocks(
            step1=step1,
            step2=step2,
            step3=step3,
            step4=step4,
            step5=step5,
            step6=step6,
            blocked_steps=_steps_blocked,
        )
        log(f"  Objective: {stint_solve.objective['total']:.4f}")
        for info in stint_compromise_info[:5]:
            log(f"  {info}")
    elif stint_dataset is not None:
        stint_compromise_info.append("Full-stint solve fell back to single-lap mode due to insufficient usable laps.")

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
    selected_candidate_family_output = getattr(selected_candidate, "family", None)
    selected_candidate_score_output = (
        selected_candidate.score.total
        if selected_candidate is not None and selected_candidate.score is not None
        else None
    )
    if selected_candidate is not None:
        solve_notes.append(
            f"Candidate family selected: {selected_candidate.family} "
            f"(score {selected_candidate.score.total if selected_candidate.score else 0.0:.3f})"
        )
        if selected_candidate.selectable and selected_candidate.result is not None:
            selected_candidate_result, preserved_rotation_controls = preserve_candidate_rotation_controls(
                rotation_result=base_solve_result,
                candidate_result=selected_candidate.result,
                inputs=solve_inputs,
            )
            if selected_candidate_result is None:
                selected_candidate_result = selected_candidate.result
                preserved_rotation_controls = False
            selected_candidate.result = selected_candidate_result
            selected_candidate.step1 = selected_candidate_result.step1
            selected_candidate.step2 = selected_candidate_result.step2
            selected_candidate.step3 = selected_candidate_result.step3
            selected_candidate.step4 = selected_candidate_result.step4
            selected_candidate.step5 = selected_candidate_result.step5
            selected_candidate.step6 = selected_candidate_result.step6
            selected_candidate.supporting = selected_candidate_result.supporting
            selected_candidate.legality = selected_candidate_result.legal_validation
            selected_candidate.predicted = selected_candidate_result.prediction
            selected_candidate_applied = True
            step1 = selected_candidate_result.step1
            step2 = selected_candidate_result.step2
            step3 = selected_candidate_result.step3
            step4 = selected_candidate_result.step4
            step5 = selected_candidate_result.step5
            step6 = selected_candidate_result.step6
            supporting = selected_candidate_result.supporting
            legal_validation = selected_candidate_result.legal_validation
            decision_trace = selected_candidate_result.decision_trace
            step1, step2, step3, step4, step5, step6 = _apply_calibration_step_blocks(
                step1=step1,
                step2=step2,
                step3=step3,
                step4=step4,
                step5=step5,
                step6=step6,
                blocked_steps=_steps_blocked,
            )
            selected_candidate_family_output = selected_candidate.family
            selected_candidate_score_output = (
                selected_candidate.score.total if selected_candidate.score is not None else None
            )
            run_trace.candidate_family = selected_candidate_family_output
            run_trace.candidate_score = selected_candidate_score_output
            if selected_candidate.score is not None:
                run_trace.record_objective(
                    getattr(selected_candidate.score, "breakdown", None),
                    float(selected_candidate_score_output or 0.0),
                    scoring_system="ObjectiveFunction (candidate family)",
                )
            solve_notes.append(
                f"Applied rematerialized {selected_candidate.family} candidate result to final report/JSON/export payloads."
            )
            if preserved_rotation_controls:
                solve_notes.append(
                    "Preserved BMW/Sebring second-stage rotation controls after candidate-family rematerialization."
                )

    # ── Legal-manifold search (--explore-legal-space / --search-mode) ─────
    search_mode = getattr(args, "search_mode", None)
    do_legal_search = should_run_legal_manifold_search(
        free_mode=free_mode,
        explicit_search=getattr(args, "explore_legal_space", False),
        search_mode=search_mode,
        scenario_name=scenario_profile_name,
    )
    _search_ready = all(s is not None for s in (step1, step2, step3, step4, step5, step6))
    if do_legal_search and not _search_ready:
        _search_reason = (
            f"blocked steps {sorted(_steps_blocked)}"
            if _steps_blocked
            else "base solve did not materialize all 6 steps"
        )
        log(f"[legal-search] Skipped: requires all 6 calibrated solver steps ({_search_reason}).")
        run_trace.add_warning(
            f"Legal-manifold search skipped: requires all 6 calibrated steps ({_search_reason})."
        )
        solve_notes.append(
            f"Skipped legal-manifold search because not all 6 calibrated steps were available ({_search_reason})."
        )
    elif do_legal_search:
        try:
            baseline_params = {
                key: value
                for key, value in build_search_baseline(
                    car=car,
                    wing=wing,
                    current_setup=current_setup,
                    step1=step1,
                    step2=step2,
                    step3=step3,
                    step4=step4,
                    step5=step5,
                    step6=step6,
                    supporting=supporting,
                ).items()
                if value is not None
            }

            if search_mode is not None:
                # ── GridSearchEngine: hierarchical structured search ────────
                from solver.grid_search import GridSearchEngine
                from solver.legal_space import LegalSpace
                from solver.objective import ObjectiveFunction

                _track_name = getattr(track, "track_name", "") or getattr(track, "name", "")
                space = LegalSpace.from_car(car, track_name=_track_name)
                objective = ObjectiveFunction(
                    car,
                    track,
                    explore=getattr(args, "explore", False),
                    scenario_profile=scenario_profile_name,
                )
                # Pre-stash session telemetry for batch scoring
                if measured is not None:
                    objective.set_session_context(measured=measured, driver=driver)

                engine = GridSearchEngine(
                    space=space,
                    objective=objective,
                    car=car,
                    track=track,
                    progress_cb=log,
                )
                search_family = getattr(args, "search_family", None)
                explore_mode = getattr(args, "explore", False)
                if explore_mode:
                    objective.explore = True
                    log("  [EXPLORE] k-NN empirical weight zeroed — pure physics search")
                gs_result = engine.run(budget=search_mode, progress=True, family=search_family, explore=explore_mode)
                log()
                log(gs_result.summary())
                # ── Apply grid search best result to final output ────────────
                # Previously this was silently discarded — now we rematerialize
                # the best candidate through the solve chain.
                _gs_best = gs_result.best_robust or gs_result.best_overall
                if _gs_best is not None and not _gs_best.hard_vetoed:
                    try:
                        from solver.solve_chain import materialize_overrides
                        _gs_params = _normalize_grid_search_params_for_overrides(_gs_best.params or {})
                        _gs_overrides = canonical_params_to_overrides(
                            base_solve_result,
                            _gs_params,
                            car=car,
                        )
                        if _gs_overrides.earliest_step() is None:
                            solve_notes.append(
                                f"Grid search best ({_gs_best.family}, score={_gs_best.score:+.1f}ms) "
                                "mapped to no effective solve-chain overrides; base solve result retained."
                            )
                            run_trace.add_warning(
                                f"Grid search best candidate ({_gs_best.family}) produced no effective overrides."
                            )
                        else:
                            _gs_materialized = materialize_overrides(
                                base_solve_result, _gs_overrides, solve_inputs
                            )
                            step1 = _gs_materialized.step1
                            step2 = _gs_materialized.step2
                            step3 = _gs_materialized.step3
                            step4 = _gs_materialized.step4
                            step5 = _gs_materialized.step5
                            step6 = _gs_materialized.step6
                            supporting = _gs_materialized.supporting
                            legal_validation = _gs_materialized.legal_validation
                            decision_trace = _gs_materialized.decision_trace
                            step1, step2, step3, step4, step5, step6 = _apply_calibration_step_blocks(
                                step1=step1,
                                step2=step2,
                                step3=step3,
                                step4=step4,
                                step5=step5,
                                step6=step6,
                                blocked_steps=_steps_blocked,
                            )
                            selected_candidate_family_output = (
                                f"{scenario_profile_name}:grid_{_gs_best.family}"
                            )
                            selected_candidate_score_output = _gs_best.score
                            selected_candidate_applied = True
                            run_trace.record_solver_path("grid_search", reason=f"--search-mode {search_mode}")
                            run_trace.candidate_family = selected_candidate_family_output
                            run_trace.candidate_score = selected_candidate_score_output
                            run_trace.record_objective(
                                _gs_best.breakdown,
                                float(_gs_best.score),
                                scoring_system="ObjectiveFunction (grid search)",
                            )
                            solve_notes.append(
                                f"Applied grid search best candidate "
                                f"({_gs_best.family}, score={_gs_best.score:+.1f}ms) "
                                f"from {search_mode} search — rematerialized through solve chain."
                            )
                    except Exception as e:
                        solve_notes.append(
                            f"Grid search found best={_gs_best.family} ({_gs_best.score:+.1f}ms) "
                            f"but rematerialization failed: {e} — base solve result retained."
                        )
                        run_trace.add_warning(f"Grid search rematerialization failed: {e}")

                # ── vetoed / empty candidate fallback notes ─────────────────
                if _gs_best is None:
                    solve_notes.append(
                        "Grid search found no acceptable candidates — base solve result retained."
                    )
                elif _gs_best.hard_vetoed and not selected_candidate_applied:
                    solve_notes.append(
                        f"Grid search best ({_gs_best.family}) was hard-vetoed — base solve result retained."
                    )

                # ── --top-n: print ranked comparison table for top N candidates ──
                # Shows alternative setups from the search pool beyond rank-1.
                # Rank-1 (best robust/overall) has already been applied to the output.
                # This table is informational — useful for manual review or
                # multi-setup export workflows.
                top_n_req = getattr(args, "top_n", 1)
                if top_n_req > 1 and gs_result.top_candidates:
                    top_pool = [
                        e for e in gs_result.top_candidates if not e.hard_vetoed
                    ][:top_n_req]
                    if top_pool:
                        log()
                        log(f"  ── TOP-{len(top_pool)} CANDIDATES (--top-n {top_n_req}) ──────────────────────────────────────────────────────────────────────────────────────────────")
                        log(f"  {'Rank':<5} {'Score':>8}  {'Family':<18}  {'Wing':>5}  {'FH-Spg':>7}  {'R3-Spg':>7}  {'Trsn':>6}  {'FARB':>5}  {'RARB':>5}  Penalties")
                        log(f"  {'-'*5} {'-'*8}  {'-'*18}  {'-'*5}  {'-'*7}  {'-'*7}  {'-'*6}  {'-'*5}  {'-'*5}  {'-'*40}")
                        for rank, ev in enumerate(top_pool, start=1):
                            p = ev.params or {}
                            penalty_str = "; ".join(ev.soft_penalties[:2]) if ev.soft_penalties else "—"
                            marker = " ← applied" if rank == 1 else ""
                            log(
                                f"  {rank:<5} {ev.score:>+8.1f}  "
                                f"{(ev.family or ''):<18}  "
                                f"{p.get('wing_angle_deg', 0):>5.0f}  "
                                f"{p.get('front_heave_spring_nmm', 0):>7.1f}  "
                                f"{p.get('rear_third_spring_nmm', 0):>7.1f}  "
                                f"{p.get('front_torsion_od_mm', 0):>6.2f}  "
                                f"{int(p.get('front_arb_blade', 0)):>5}  "
                                f"{int(p.get('rear_arb_blade', 0)):>5}  "
                                f"{penalty_str}{marker}"
                            )
                        log(f"  {'-'*5} {'-'*8}  {'-'*18}  {'-'*5}  {'-'*7}  {'-'*7}  {'-'*6}  {'-'*5}  {'-'*5}  {'-'*40}")
                        log(f"  Rank 1 applied to setup output. Use --top-n 1 to suppress this table.")
                        solve_notes.append(
                            f"Top-{len(top_pool)} candidates surfaced via --top-n "
                            f"(best score={top_pool[0].score:+.1f}ms, "
                            f"worst={top_pool[-1].score:+.1f}ms)."
                        )
                run_trace.search_mode = search_mode
            else:
                # ── Original random family search ───────────────────────────
                from solver.legal_search import run_legal_search
                search_budget = getattr(args, "search_budget", 1000)
                keep_weird = getattr(args, "keep_weird", True)

                ls_result = run_legal_search(
                    car=car,
                    track=track,
                    baseline_params=baseline_params,
                    budget=search_budget,
                    measured=measured,
                    driver_profile=driver,
                    session_count=1,
                    keep_weird=keep_weird,
                    base_result=base_solve_result,
                    solve_inputs=solve_inputs,
                    scenario_profile=scenario_profile_name,
                )
                log()
                log(ls_result.summary())
                if ls_result.accepted_best_result is not None and ls_result.accepted_best is not None:
                    accepted_result = ls_result.accepted_best_result
                    step1 = accepted_result.step1
                    step2 = accepted_result.step2
                    step3 = accepted_result.step3
                    step4 = accepted_result.step4
                    step5 = accepted_result.step5
                    step6 = accepted_result.step6
                    supporting = accepted_result.supporting
                    legal_validation = accepted_result.legal_validation
                    decision_trace = accepted_result.decision_trace
                    step1, step2, step3, step4, step5, step6 = _apply_calibration_step_blocks(
                        step1=step1,
                        step2=step2,
                        step3=step3,
                        step4=step4,
                        step5=step5,
                        step6=step6,
                        blocked_steps=_steps_blocked,
                    )
                    selected_candidate_family_output = f"{scenario_profile_name}:{ls_result.accepted_best.family}"
                    selected_candidate_score_output = ls_result.accepted_best.score
                    selected_candidate_applied = True
                    run_trace.record_solver_path("legal_search", reason=f"scenario={scenario_profile_name}")
                    run_trace.candidate_family = selected_candidate_family_output
                    run_trace.candidate_score = float(selected_candidate_score_output or 0.0)
                    solve_notes.append(
                        f"Applied legal-manifold scenario pick {ls_result.accepted_best.family} "
                        f"for {scenario_profile_name} after full legality + prediction sanity checks."
                    )
                else:
                    solve_notes.append(
                        f"Legal-manifold search found no fully accepted {scenario_profile_name} candidate."
                    )
        except Exception as e:
            log(f"[legal-search] Skipped: {e}")

    # NOTE (2026-03-31): The Ferrari in-place mutation block was removed here.
    # garage_validator.py, setup_writer.py, and legality_engine.py each make
    # their own deep copies and call public_output_value() internally.
    # Mutating the shared solver step objects caused double-conversion bugs
    # (physical → index → corrupted index on second pass).

    # ── Update RunTrace with final legality and notes ──
    run_trace.record_legality(legal_validation)
    for _n in solve_notes:
        run_trace.add_note(_n)

    # ── Print RunTrace ──
    _verbose = getattr(args, "verbose", False)
    run_trace.print_report(verbose=_verbose)

    if args.sto:
        # Final garage correlation check before writing .sto
        from output.garage_validator import validate_and_fix_garage_correlation
        garage_warnings = validate_and_fix_garage_correlation(
            car, step1, step2, step3, step5,
            fuel_l=fuel, track_name=track.track_name,
        )
        for w in garage_warnings:
            print(f"[garage] {w}")

        # Print ESTIMATE warnings for uncalibrated models
        estimate_warnings = []
        if hasattr(car, 'deflection') and not getattr(car.deflection, 'is_calibrated', True):
            estimate_warnings.append(
                "Deflection predictions use uncalibrated model — verify garage display values manually"
            )
        if hasattr(car, 'ride_height_model') and not getattr(car.ride_height_model, 'is_calibrated', True):
            estimate_warnings.append(
                "Ride height predictions use uncalibrated model"
            )
        if hasattr(car, 'damper') and not getattr(car.damper, 'zeta_is_calibrated', True):
            estimate_warnings.append(
                "Damper zeta targets are conservative defaults — verify damper feel on track"
            )
        garage_model = getattr(car, "active_garage_output_model", lambda _track: None)(track.track_name)
        if garage_model is None:
            estimate_warnings.append(
                "No garage output model — .sto display values are physics estimates only"
            )

        for w in estimate_warnings:
            print(f"[ESTIMATE] {w}")

        _extra_kw = {}
        if car.canonical_name == "ferrari":
            _extra_kw["tyre_pressure_kpa"] = supporting.tyre_cold_fl_kpa
            _extra_kw["brake_bias_pct"] = supporting.brake_bias_pct
            _extra_kw["brake_bias_target"] = supporting.brake_bias_target
            _extra_kw["brake_bias_migration"] = supporting.brake_bias_migration
            _extra_kw["brake_bias_migration_gain"] = current_setup.brake_bias_migration_gain
            _extra_kw["front_master_cyl_mm"] = supporting.front_master_cyl_mm
            _extra_kw["rear_master_cyl_mm"] = supporting.rear_master_cyl_mm
            _extra_kw["pad_compound"] = supporting.pad_compound
            _extra_kw["diff_coast_drive_ramp"] = (
                getattr(supporting, "diff_ramp_angles", "")
                or ("Less Locking" if supporting.diff_ramp_coast >= 45 else "More Locking")
            )
            _extra_kw["diff_clutch_plates"] = supporting.diff_clutch_plates
            _extra_kw["diff_preload_nm"] = supporting.diff_preload_nm
            _extra_kw["front_diff_preload_nm"] = current_setup.front_diff_preload_nm
            _extra_kw["tc_gain"] = supporting.tc_gain
            _extra_kw["tc_slip"] = supporting.tc_slip
            _extra_kw["fuel_low_warning_l"] = getattr(supporting, "fuel_low_warning_l", fuel)
            _extra_kw["fuel_target_l"] = getattr(supporting, "fuel_target_l", None)
            _extra_kw["gear_stack"] = getattr(supporting, "gear_stack", "")
            _extra_kw["speed_in_first_kph"] = current_setup.speed_in_first_kph
            _extra_kw["speed_in_second_kph"] = current_setup.speed_in_second_kph
            _extra_kw["speed_in_third_kph"] = current_setup.speed_in_third_kph
            _extra_kw["speed_in_fourth_kph"] = current_setup.speed_in_fourth_kph
            _extra_kw["speed_in_fifth_kph"] = current_setup.speed_in_fifth_kph
            _extra_kw["speed_in_sixth_kph"] = current_setup.speed_in_sixth_kph
            _extra_kw["speed_in_seventh_kph"] = current_setup.speed_in_seventh_kph
            _extra_kw["roof_light_color"] = getattr(supporting, "roof_light_color", "")
            _extra_kw["hybrid_rear_drive_enabled"] = current_setup.hybrid_rear_drive_enabled
            _extra_kw["hybrid_rear_drive_corner_pct"] = current_setup.hybrid_rear_drive_corner_pct
        else:
            _extra_kw["tyre_pressure_kpa"] = supporting.tyre_cold_fl_kpa
            _extra_kw["tyre_pressure_fl_kpa"] = supporting.tyre_cold_fl_kpa
            _extra_kw["tyre_pressure_fr_kpa"] = supporting.tyre_cold_fr_kpa
            _extra_kw["tyre_pressure_rl_kpa"] = supporting.tyre_cold_rl_kpa
            _extra_kw["tyre_pressure_rr_kpa"] = supporting.tyre_cold_rr_kpa
            _extra_kw["brake_bias_pct"] = supporting.brake_bias_pct
            _extra_kw["brake_bias_target"] = supporting.brake_bias_target
            _extra_kw["brake_bias_migration"] = supporting.brake_bias_migration
            _extra_kw["front_master_cyl_mm"] = supporting.front_master_cyl_mm
            _extra_kw["rear_master_cyl_mm"] = supporting.rear_master_cyl_mm
            _extra_kw["pad_compound"] = supporting.pad_compound
            _extra_kw["diff_coast_drive_ramp"] = (
                getattr(supporting, "diff_ramp_angles", "")
                or f"{supporting.diff_ramp_coast}/{supporting.diff_ramp_drive}"
            )
            _extra_kw["diff_clutch_plates"] = supporting.diff_clutch_plates
            _extra_kw["diff_preload_nm"] = supporting.diff_preload_nm
            _extra_kw["tc_gain"] = supporting.tc_gain
            _extra_kw["tc_slip"] = supporting.tc_slip
            _extra_kw["fuel_low_warning_l"] = getattr(supporting, "fuel_low_warning_l", fuel)
            _extra_kw["gear_stack"] = getattr(supporting, "gear_stack", "")
            _extra_kw["roof_light_color"] = getattr(supporting, "roof_light_color", "")

        if step1 is None or step2 is None or step3 is None:
            print("\n[sto] Cannot write .sto — steps 1-3 are required but blocked by calibration.")
            print("  Follow the calibration instructions above to enable .sto output.")
        else:
            if _steps_blocked:
                print(f"\n[sto] NOTE: Steps {sorted(_steps_blocked)} are uncalibrated (set to None).")
                print("  .sto will use garage defaults for those steps.")
                print("  Only calibrated step values are physics-validated.")

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
        json_path = Path(args.json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        parameter_coverage = build_parameter_coverage(
            car=car,
            wing=wing,
            current_setup=current_setup,
            step1=step1,
            step2=step2,
            step3=step3,
            step4=step4,
            step5=step5,
            step6=step6,
            supporting=supporting,
        )
        telemetry_coverage = build_telemetry_coverage(measured=measured)
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
            "parameter_coverage": parameter_coverage,
            "telemetry_coverage": telemetry_coverage,
            "stint_selection": _stint_selection_payload(stint_dataset),
            "stint_laps": _stint_lap_payload(stint_dataset),
            "stint_phases": getattr(stint_solve, "phase_summaries", getattr(stint_dataset, "phase_summaries", {})),
            "stint_objective": getattr(stint_solve, "objective", None),
            "stint_confidence": (
                stint_solve.confidence
                if stint_solve is not None
                else getattr(stint_dataset, "confidence", None)
            ),
            "scenario_profile": scenario_profile_name,
            "fallback_mode": (
                stint_solve.fallback_mode
                if stint_solve is not None
                else getattr(stint_dataset, "fallback_mode", None)
            ),
            "selected_candidate_family": selected_candidate_family_output,
            "selected_candidate_score": selected_candidate_score_output,
            "selected_candidate_applied": selected_candidate_applied,
            "legal_validation": legal_validation.to_dict() if legal_validation is not None else None,
            "decision_trace": [decision.to_dict() for decision in decision_trace],
            "solver_notes": solve_notes,
            "step1_rake": to_public_output_payload(car.canonical_name, step1),
            "step2_heave": to_public_output_payload(car.canonical_name, step2),
            "step3_corner": to_public_output_payload(car.canonical_name, step3),
            "step4_arb": to_public_output_payload(car.canonical_name, step4),
            "step5_geometry": to_public_output_payload(car.canonical_name, step5),
            "step6_dampers": to_public_output_payload(car.canonical_name, step6),
            "supporting": to_public_output_payload(car.canonical_name, supporting),
            "calibration_blocked": sorted(_steps_blocked) if _steps_blocked else [],
            "calibration_instructions": cal_report.format_header() if _steps_blocked else "",
            # Provenance: where each calibrated subsystem's value came from.
            # User can audit exactly what's data-derived vs what's weak/missing.
            "calibration_provenance": cal_gate.provenance(),
            "calibration_weak_steps": cal_report.weak_steps,
            "calibration_weak_upstream_steps": cal_report.weak_upstream_steps,
            "calibration_weak_upstream_by_step": {
                str(sr.step_number): sr.weak_upstream_step
                for sr in cal_report.step_reports
                if sr.weak_upstream and sr.weak_upstream_step is not None
            },
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
        fuel_l=fuel,
        target_balance=target_balance,
        stint_result=stint_result,
        sector_result=sector_result,
        sensitivity_result=sensitivity_result,
        stint_evolution=stint_evolution,
        stint_compromise_info=stint_compromise_info,
        prediction_corrections={},
        selected_candidate_family=selected_candidate_family_output,
        selected_candidate_score=selected_candidate_score_output,
        solve_context_lines=solve_notes,
        compact=report_compact,
    )
    if _emit_report:
        print(report)
        if _steps_blocked:
            print()
            print("=" * 63)
            print(f"  CALIBRATION: {len(_steps_blocked)} step(s) use unproven data")
            print(f"  Steps {sorted(_steps_blocked)} are NOT calibrated for {car.name}")
            print("=" * 63)
            print(cal_report.format_header())
            print()

    # ── Delta card output (--delta-card flag) ────────────────────────
    if getattr(args, "delta_card", False) and current_setup is not None:
        try:
            from output.delta_card import format_delta_card

            # Build current params dict from IBT-extracted setup.
            # For Ferrari (and any car with indexed springs), use raw_indexed_fields
            # so current values are in the same display units as the solver output
            # (public_output_value converts N/mm → index for Ferrari).
            _raw_idx = getattr(current_setup, "raw_indexed_fields", {}) or {}
            _current_dict: dict = {
                "front_rh_static": getattr(current_setup, "static_front_rh_mm", None),
                "rear_rh_static": getattr(current_setup, "static_rear_rh_mm", None),
                # Pushrod — always show so user knows when spring/RH change requires pushrod adjustment
                "front_pushrod_mm": getattr(current_setup, "front_pushrod_mm", None),
                "rear_pushrod_mm": getattr(current_setup, "rear_pushrod_mm", None),
                # Use indexed fields when available (Ferrari), else raw N/mm (BMW)
                "front_heave_nmm": _raw_idx.get("front_heave_index") if _raw_idx else getattr(current_setup, "front_heave_nmm", None),
                "rear_third_nmm": _raw_idx.get("rear_heave_index") if _raw_idx else getattr(current_setup, "rear_third_nmm", None),
                "torsion_bar_od_mm": _raw_idx.get("front_torsion_bar_index") if _raw_idx else getattr(current_setup, "front_torsion_od_mm", None),
                "rear_spring_nmm": _raw_idx.get("rear_torsion_bar_index") if _raw_idx else getattr(current_setup, "rear_spring_nmm", None),
                "front_arb_blade": getattr(current_setup, "front_arb_blade", None),
                "rear_arb_blade": getattr(current_setup, "rear_arb_blade_start", None) or getattr(current_setup, "rear_arb_blade", None),
                "front_arb_size": getattr(current_setup, "front_arb_size", None),
                "rear_arb_size": getattr(current_setup, "rear_arb_size", None),
                "front_camber_deg": getattr(current_setup, "front_camber_deg", None),
                "rear_camber_deg": getattr(current_setup, "rear_camber_deg", None),
                "front_toe_mm": getattr(current_setup, "front_toe_mm", None),
                "rear_toe_mm": getattr(current_setup, "rear_toe_mm", None),
                "diff_preload_nm": getattr(current_setup, "diff_preload_nm", None),
                "diff_clutch_plates": getattr(current_setup, "diff_clutch_plates", None),
                "tc_gain": getattr(current_setup, "tc_gain", None),
                "tc_slip": getattr(current_setup, "tc_slip", None),
                "brake_bias_pct": getattr(current_setup, "brake_bias_pct", None),
                "wing_angle_deg": getattr(current_setup, "wing_angle_deg", None),
            }
            # Strip None values so detect_changes skips them cleanly
            _current_dict = {k: v for k, v in _current_dict.items() if v is not None}

            # Build recommended params dict from solver steps
            # All spring/torsion values go through public_output_value to match IBT display units
            _recommended_dict: dict = {
                "front_rh_static": step1.static_front_rh_mm,
                "rear_rh_static": step1.static_rear_rh_mm,
                # Pushrod — solver recalculates this when spring/RH changes (rake_solver.py)
                "front_pushrod_mm": getattr(step1, "front_pushrod_offset_mm", None),
                "rear_pushrod_mm": getattr(step1, "rear_pushrod_offset_mm", None),
                # Use public_output_value to convert to display units (e.g. N/mm → index for Ferrari)
                "front_heave_nmm": public_output_value(car, "front_heave_nmm", step2.front_heave_nmm),
                "rear_third_nmm": public_output_value(car, "rear_third_nmm", step2.rear_third_nmm),
                "torsion_bar_od_mm": public_output_value(car, "front_torsion_od_mm", step3.front_torsion_od_mm),
                "rear_spring_nmm": public_output_value(car, "rear_spring_rate_nmm", step3.rear_spring_rate_nmm),
                "front_arb_blade": step4.front_arb_blade_start,
                "rear_arb_blade": step4.rear_arb_blade_start,
                "front_arb_size": step4.front_arb_size,
                "rear_arb_size": step4.rear_arb_size,
                "front_camber_deg": step5.front_camber_deg,
                "rear_camber_deg": step5.rear_camber_deg,
                "front_toe_mm": step5.front_toe_mm,
                "rear_toe_mm": step5.rear_toe_mm,
                "wing_angle_deg": wing,
            }
            if supporting is not None:
                _recommended_dict.update({
                    "diff_preload_nm": getattr(supporting, "diff_preload_nm", None),
                    "diff_clutch_plates": getattr(supporting, "diff_clutch_plates", None),
                    "tc_gain": getattr(supporting, "tc_gain", None),
                    "tc_slip": getattr(supporting, "tc_slip", None),
                    "brake_bias_pct": getattr(supporting, "brake_bias_pct", None),
                })
            # Add damper clicks
            for _corner_name, _corner_obj in (
                ("lf", step6.lf), ("rf", step6.rf), ("lr", step6.lr), ("rr", step6.rr)
            ):
                _recommended_dict[f"{_corner_name}_ls_comp_clicks"] = getattr(_corner_obj, "ls_comp", None)
                _recommended_dict[f"{_corner_name}_hs_comp_clicks"] = getattr(_corner_obj, "hs_comp", None)
                _recommended_dict[f"{_corner_name}_ls_rbd_clicks"] = getattr(_corner_obj, "ls_rbd", None)
                _recommended_dict[f"{_corner_name}_hs_rbd_clicks"] = getattr(_corner_obj, "hs_rbd", None)
            # Strip None values
            _recommended_dict = {k: v for k, v in _recommended_dict.items() if v is not None}

            _delta_session_count = learned.session_count if learned is not None else 0

            _best_lap = getattr(track, "best_lap_time_s", None)

            delta_output = format_delta_card(
                current=_current_dict,
                recommended=_recommended_dict,
                car=car.canonical_name,
                track=track.track_name,
                session_count=_delta_session_count,
                best_lap_s=_best_lap,
                mode=getattr(args, "mode", "safe"),
                full_setup_str=report,
            )
            print("\n")
            print(delta_output)
        except Exception as _dc_exc:
            print(f"\n[delta-card] Warning: could not generate delta card: {_dc_exc}")

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
            "stint_dataset": stint_dataset,
            "stint_solve": stint_solve,
            "scenario_profile": scenario_profile_name,
            "selected_candidate_family": selected_candidate_family_output,
            "selected_candidate_score": selected_candidate_score_output,
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
                "rear_power_slip_p95": predicted_telemetry.rear_power_slip_ratio_p95,
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

            # ── Auto-update heave calibration from this IBT run ──────────
            # Every new IBT teaches the system the actual (heave → σ) relationship.
            # When you run 380 N/mm, drop in the IBT, and σ comes back elevated,
            # the solver automatically down-scores that heave range going forward.
            try:
                from solver.heave_calibration import HeaveCalibration
                _setup = result.get("setup", {})
                _tel = result.get("telemetry", {})
                _perf = result.get("performance", {})
                _heave = _setup.get("front_heave_nmm") or _setup.get("front_heave_spring_nmm")
                _sigma = _tel.get("front_rh_std_mm")
                if _heave and _sigma:
                    _track_raw = result.get("track", "unknown")
                    _track_slug = str(_track_raw).lower().split()[0]
                    _car_raw = result.get("car", "unknown")
                    _car_slug = str(_car_raw).lower()
                    _cal = HeaveCalibration.load(_car_slug, _track_slug)
                    _cal.add_run(
                        heave_nmm=float(_heave),
                        sigma_mm=float(_sigma),
                        rear_sigma_mm=_tel.get("rear_rh_std_mm"),
                        shock_vel_p99=_tel.get("front_shock_vel_p99_mps"),
                        dominant_freq_hz=_tel.get("front_dominant_freq_hz"),
                        heave_travel_pct=_tel.get("front_heave_travel_used_pct"),
                        best_lap_s=_perf.get("best_lap_time_s"),
                        session_ts=result.get("timestamp", ""),
                    )
                    _cal.save()
                    log(f"[heave_cal] Updated {_car_slug}/{_track_slug}: "
                        f"heave={_heave:.0f} N/mm → σ={_sigma:.2f}mm "
                        f"(n={sum(s.n for s in _cal.summary)} total runs)")
            except Exception as _e:
                log(f"[heave_cal] Calibration update skipped: {_e}")

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


    # _apply_damper_modifiers removed — use solver.solve_chain.apply_damper_modifiers


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
    parser.add_argument("--balance", type=float, default=None,
                        help="Target DF balance %% (default: car-specific)")
    parser.add_argument("--tolerance", type=float, default=0.1,
                        help="Balance tolerance %% (default: 0.1)")
    parser.add_argument("--fuel", type=float, default=None,
                        help="Fuel load in liters (auto-detected if not specified)")
    parser.add_argument("--free", action="store_true",
                        help="Search the legal setup manifold from the pinned baseline and apply the best accepted candidate")
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
    parser.add_argument("--force", action="store_true",
                        help="Bypass calibration gate — output all solver steps even if uncalibrated. "
                             "Values are ESTIMATES only, marked with [UNCALIBRATED] warnings.")
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
                        help="Enable full-stint analysis and solve one compromise setup across the selected stint.")
    parser.add_argument("--stint-threshold", type=float, default=1.5, dest="stint_threshold",
                        help="Backward-compatible soft outlier/reporting threshold for stint lap quality (default: 1.5)")
    parser.add_argument("--stint-select", type=str, default="longest", dest="stint_select",
                        choices=["longest", "last", "all"],
                        help="Which green-run stint segment(s) to use for the full-stint solve (default: longest)")
    parser.add_argument("--stint-max-laps", type=int, default=40, dest="stint_max_laps",
                        help="Maximum number of stint laps to score directly; longer stints keep phase-preserving representatives (default: 40)")
    parser.add_argument("--verbose", action="store_true",
                        help="Show full reasoning dump (multi-IBT mode)")
    # Legal-space search mode
    parser.add_argument("--explore-legal-space", action="store_true",
                        help="Run legal-manifold search after physics solver")
    parser.add_argument("--search-budget", type=int, default=1000,
                        help="Number of candidates to evaluate in legal-space search (default: 1000)")
    parser.add_argument("--keep-weird", action="store_true",
                        help="Retain unconventional but legal candidates in results")
    parser.add_argument("--search-mode", type=str, default=None,
                        choices=["quick", "standard", "exhaustive", "maximum"],
                        dest="search_mode",
                        help=(
                            "Hierarchical grid search mode (uses GridSearchEngine). "
                            "quick=~3s, standard=~4min, exhaustive=~80min, maximum=~5h (overnight). "
                            "When set, uses structured Sobol+grid search instead of "
                            "random family sampling. Implies --explore-legal-space. "
                            "Combine with --top-n to surface multiple ranked alternatives."
                        ))
    parser.add_argument("--top-n", type=int, default=1, dest="top_n",
                        help=(
                            "Number of top candidates to output as full setup sheets "
                            "after --search-mode. Default=1 (only best). "
                            "Use --top-n 5 to see top 5 full setups ranked by score."
                        ))
    parser.add_argument("--family", type=str, default=None,
                        choices=["robust", "aggressive", "balanced"],
                        dest="search_family",
                        help=(
                            "Setup family to bias the Layer 1 Sobol search toward. "
                            "robust=low wing + soft springs (mechanical safety margin), "
                            "aggressive=high wing + stiff springs (max DF extraction), "
                            "balanced=full-range uniform coverage (default). "
                            "Only applies with --search-mode."
                        ))
    parser.add_argument("--explore", action="store_true", default=False,
                        dest="explore",
                        help=(
                            "Exploration mode: disables k-NN empirical anchoring (w=0.0), "
                            "forces 'balanced' Sobol family (no region bias), and boosts "
                            "L1 sample budget for wider initial coverage. "
                            "Use to validate that the current optimal is not a learned local "
                            "minimum. Combine with --search-mode standard or exhaustive. "
                            "Results show pure-physics recommendations unconstrained by "
                            "historical session data."
                        ))
    parser.add_argument("--scenario-profile", type=str, default=None,
                        choices=["single_lap_safe", "quali", "sprint", "race"],
                        help="Scenario objective and sanity profile to use for legal-manifold search")
    parser.add_argument("--opt-mode", type=str, default="driver",
                        choices=["driver", "physics"], dest="opt_mode",
                        help="Optimization mode: 'driver' (anchor to driver's setup, default) "
                             "or 'physics' (pure physics-optimal, ignore driver anchors)")
    parser.add_argument("--objective-profile", type=str, default="balanced",
                        choices=["robust", "aggressive", "balanced"],
                        help="Legacy objective alias. 'balanced' maps to the default single-lap-safe scenario.")
    # Legacy flags (kept for backward-compat; no-op since auto is default)
    parser.add_argument("--learn", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--auto-learn", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--delta-card", action="store_true", default=False,
                        dest="delta_card",
                        help="Output a delta card showing only changes vs current setup, with confidence tiers")
    parser.add_argument("--mode", choices=["safe", "aggressive"], default="safe",
                        dest="mode",
                        help="Output mode for delta card: safe (HIGH+PIN only) or aggressive (all changes). "
                             "Also affects scenario: safe→single_lap_safe, aggressive→quali")
    args = parser.parse_args()
    try:
        produce(args)
    except PipelineInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
