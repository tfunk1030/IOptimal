"""Ingest — analyze an IBT, store observation, detect deltas, update models.

This is the main entry point for the learning system. Each call:
1. Runs the analyzer pipeline on the IBT (extract, segment, driver, diagnose)
2. Builds a structured observation
3. Stores it in the knowledge store
4. Compares against the most recent prior session (delta detection)
5. Updates empirical models with all accumulated data
6. Generates updated insights
7. Prints what was learned

Usage:
    python -m learner.ingest --car bmw --ibt path/to/session.ibt          # all-laps default
    python -m learner.ingest --car bmw --ibt path/to/session.ibt --wing 17
    python -m learner.ingest --car bmw --ibt path/to/session.ibt --single-lap   # legacy: best lap only
    python -m learner.ingest --car bmw --ibt path/to/session.ibt --lap 5        # specific lap (single)
    python -m learner.ingest --status                    # show what we know
    python -m learner.ingest --car bmw --track sebring --recall  # knowledge dump
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from learner.knowledge_store import KnowledgeStore
from learner.observation import Observation, build_observation
from learner.delta_detector import detect_delta, SessionDelta
from learner.empirical_models import fit_models
from learner.recall import KnowledgeRecall
from learner.sanity import (
    filter_plausible_lap_times,
    is_plausible_lap_time,
    select_all_valid_laps,
    select_valid_lap,
)


def _run_analyzer(car_name: str, ibt_path: str, wing: float | None = None,
                   lap: int | None = None):
    """Run the analyzer pipeline and return all intermediate results.

    Follows the same flow as pipeline/produce.py but returns intermediate
    objects instead of writing output files.

    Returns: (track_profile, measured, current_setup, driver, diagnosis, corners, ibt)
    """
    from analyzer.adaptive_thresholds import compute_adaptive_thresholds
    from analyzer.diagnose import diagnose
    from analyzer.driver_style import analyze_driver, refine_driver_with_measured
    from analyzer.extract import extract_measurements
    from analyzer.segment import segment_lap
    from analyzer.setup_reader import CurrentSetup
    from analyzer.setup_schema import apply_live_control_overrides
    from car_model.cars import get_car
    from track_model.build_profile import build_profile
    from track_model.ibt_parser import IBTFile

    car = get_car(car_name)
    ibt = IBTFile(ibt_path)
    track = build_profile(ibt_path)

    # Accumulate into per-(track, car) store (non-critical for ingest)
    try:
        from track_model.track_store import TrackProfileStore
        from car_model.registry import track_slug as _track_slug
        _slug = _track_slug(track.track_name, track.track_config or "default")
        _car_slug = car_name.lower().replace(" ", "_")
        _store = TrackProfileStore(_slug, _car_slug)
        _store.add_session(track, session_id=Path(ibt_path).stem)
    except Exception:
        pass

    lap_num, start, end, _lap_time = select_valid_lap(
        ibt,
        car=car_name,
        track=track.track_name,
        lap=lap,
    )

    # Extract measurements with the same validated lap used below.
    measured = extract_measurements(ibt_path, car, lap=lap_num)

    setup = CurrentSetup.from_ibt(ibt, car_canonical=car.canonical_name)
    apply_live_control_overrides(setup, measured)
    corners = segment_lap(ibt, start, end, car=car, tick_rate=ibt.tick_rate)
    # Pass the same lap indices to analyze_driver so throttle/steering analysis
    # is computed on the same lap as corners, extract_measurements, and segment_lap.
    driver = analyze_driver(
        ibt, corners, car,
        tick_rate=ibt.tick_rate,
        selected_lap_start=start,
        selected_lap_end=end,
    )
    refine_driver_with_measured(driver, measured)
    thresholds = compute_adaptive_thresholds(track, car, driver)
    diag = diagnose(measured, setup, car, thresholds)

    return track, measured, setup, driver, diag, corners, ibt


def _update_auto_calibration(
    *,
    car_name: str,
    ibt_path: str,
    session_id: str,
    assessment: str,
    lap_time_s: float,
    verbose: bool,
) -> dict:
    """Append one session-level calibration point and auto-fit models when ready."""
    result: dict = {}
    try:
        from car_model.auto_calibrate import (
            load_calibration_points,
            extract_point_from_ibt,
            save_calibration_points,
            fit_models_from_points,
            save_calibrated_models,
            load_calibrated_models,
        )
        cal_points = load_calibration_points(car_name)
        existing_ids = {pt.session_id for pt in cal_points}
        if session_id in existing_ids:
            return result

        pt = extract_point_from_ibt(ibt_path, car_name)
        if pt is None:
            return result

        pt.session_id = session_id
        pt.assessment = assessment
        pt.lap_time_s = lap_time_s
        cal_points.append(pt)
        save_calibration_points(car_name, cal_points)

        result["cal_point_added"] = True

        # ── Per-track calibration ──
        # Group points by track to avoid cross-track contamination.
        # Same setup at different tracks produces different ride heights /
        # deflections due to aero load, surface, and speed profile differences.
        # Pooling cross-track data causes 27x-103x LOO/train overfitting.
        from car_model.auto_calibrate import _setup_key
        from car_model.registry import track_key as _track_key

        track_groups: dict[str, list] = {}
        for p2 in cal_points:
            tk = _track_key(p2.track) if p2.track else ""
            if tk:
                track_groups.setdefault(tk, []).append(p2)

        n_unique_total = len({_setup_key(p2) for p2 in cal_points})
        result["cal_unique_setups"] = n_unique_total

        for tk, track_pts in track_groups.items():
            tk_unique = len({_setup_key(p2) for p2 in track_pts})
            if tk_unique < 5:
                continue

            cal_models = fit_models_from_points(car_name, track_pts)
            cal_models.track = tk

            # Preserve zeta and torsion lookups from existing models
            existing_saved = load_calibrated_models(car_name, track=tk)
            if not existing_saved:
                existing_saved = load_calibrated_models(car_name)
            if existing_saved:
                if existing_saved.front_ls_zeta is not None and cal_models.front_ls_zeta is None:
                    cal_models.front_ls_zeta = existing_saved.front_ls_zeta
                    cal_models.rear_ls_zeta = existing_saved.rear_ls_zeta
                    cal_models.front_hs_zeta = existing_saved.front_hs_zeta
                    cal_models.rear_hs_zeta = existing_saved.rear_hs_zeta
                    cal_models.zeta_n_sessions = existing_saved.zeta_n_sessions
                if existing_saved.front_torsion_lookup and not cal_models.front_torsion_lookup:
                    cal_models.front_torsion_lookup = existing_saved.front_torsion_lookup
                if existing_saved.rear_torsion_lookup and not cal_models.rear_torsion_lookup:
                    cal_models.rear_torsion_lookup = existing_saved.rear_torsion_lookup
                for k, v in existing_saved.status.items():
                    if k not in cal_models.status:
                        cal_models.status[k] = v

            save_calibrated_models(car_name, cal_models, track=tk)
            if cal_models.calibration_complete:
                result["new_learning"] = (
                    f"Auto-calibration complete for {tk}: {tk_unique} unique setups, "
                    f"deflection model fitted."
                )
            if verbose:
                print(
                    f"  [calibrate] {tk}: {tk_unique} unique setups — "
                    f"models {'fitted' if cal_models.calibration_complete else 'pending'}"
                )
    except Exception as exc:
        if verbose:
            print(f"  [calibrate] Skipped: {exc}")
    return result


def ingest_ibt(
    car_name: str,
    ibt_path: str,
    wing: float | None = None,
    lap: int | None = None,
    store: KnowledgeStore | None = None,
    verbose: bool = True,
) -> dict:
    """Full ingest cycle: analyze -> observe -> delta -> models -> insights.

    Returns dict with: observation, delta (if any), models, new_learnings
    """
    store = store or KnowledgeStore()

    if verbose:
        print(f"\n{'='*60}")
        print(f"  LEARNER: Ingesting {Path(ibt_path).name}")
        print(f"{'='*60}\n")

    # ── 1. Run analyzer ──────────────────────────────────────────────
    if verbose:
        print("Phase 1: Analyzing IBT...")
    track, measured, setup, driver, diag, corners, ibt = _run_analyzer(
        car_name, ibt_path, wing, lap
    )

    # ── 2. Build observation ─────────────────────────────────────────
    session_id = store.session_id_from_ibt(ibt_path, car_name, track.track_name)

    if store.has_observation(session_id):
        if verbose:
            print(f"  Session {session_id} already ingested — updating.")

    obs = build_observation(
        session_id=session_id,
        ibt_path=ibt_path,
        car_name=car_name,
        track_profile=track,
        measured_state=measured,
        current_setup=setup,
        driver_profile_obj=driver,
        diagnosis_obj=diag,
        corners=corners,
    )

    store.save_observation(session_id, obs.to_dict())
    if verbose:
        print(f"  Observation stored: {session_id}")
        print(f"  Lap time: {diag.lap_time_s:.3f}s")
        print(f"  Assessment: {diag.assessment}")
        print(f"  Problems: {len(diag.problems)}")

    # ── Auto-update garage model ─────────────────────────────────────
    # Every IBT analysis automatically builds/updates the per-car-per-track
    # garage model. New car = first IBT bootstraps the entire model.
    # No manual intervention needed.
    try:
        from car_model.garage_model import GarageModelBuilder
        obs_dict = obs.to_dict()
        track_name = getattr(track, "track_name", None) or getattr(track, "name", None) or str(track)
        GarageModelBuilder.update_from_observation(car_name.lower(), track_name, obs_dict)
        if verbose:
            print(f"  Garage model updated: {car_name}/{track_name}")
    except Exception as _gm_err:
        # Never block IBT analysis due to garage model failure
        if verbose:
            print(f"  [garage_model] Update skipped: {_gm_err}")

    # ── 3. Update index ──────────────────────────────────────────────
    idx = store.load_index()
    if session_id not in idx["sessions"]:
        idx["sessions"].append(session_id)
    idx["total_observations"] = len(idx["sessions"])
    if car_name not in idx["cars_seen"]:
        idx["cars_seen"].append(car_name)
    track_key = f"{track.track_name}_{track.track_config}"
    if track_key not in idx["tracks_seen"]:
        idx["tracks_seen"].append(track_key)
    store.save_index(idx)

    # ── 4. Delta detection against prior sessions ────────────────────
    delta_result = None
    track_key_short = track.track_name.lower().split()[0]

    prior_obs = store.list_observations(car=car_name, track=track.track_name)
    # Filter to sessions BEFORE this one (by timestamp or position)
    prior_obs = [o for o in prior_obs if o["session_id"] != session_id]

    if prior_obs:
        latest_prior = prior_obs[-1]

        if verbose:
            print(f"\nPhase 2: Comparing against {latest_prior['session_id']}...")

        obs_before = Observation.from_dict(latest_prior)
        obs_after = obs

        delta_result = detect_delta(obs_before, obs_after)

        # Store delta
        delta_id = f"{car_name}_{track_key_short}_delta_{len(prior_obs):03d}"
        store.save_delta(delta_id, delta_result.to_dict())
        idx["total_deltas"] = idx.get("total_deltas", 0) + 1
        store.save_index(idx)

        if verbose:
            print(f"  Setup changes: {delta_result.num_setup_changes}")
            print(f"  Controlled experiment: {delta_result.controlled_experiment}")
            print(f"  Confidence: {delta_result.confidence_level}")
            if delta_result.lap_time_delta_s != 0:
                faster = "FASTER" if delta_result.lap_time_delta_s < 0 else "SLOWER"
                print(f"  Lap time: {abs(delta_result.lap_time_delta_s):.3f}s {faster}")
            print(f"  Key finding: {delta_result.key_finding}")

            if delta_result.hypotheses:
                high_conf = [h for h in delta_result.hypotheses if h.confidence >= 0.5]
                if high_conf:
                    print(f"\n  Causal hypotheses (>=50% confidence):")
                    for h in high_conf[:5]:
                        match = "Y" if h.direction_match else "N"
                        print(f"    [{match}] {h.mechanism} (conf={h.confidence:.0%})")
    else:
        if verbose:
            print("\nPhase 2: First session for this car/track — no delta to compute.")

    # ── 5. Update empirical models ───────────────────────────────────
    if verbose:
        print(f"\nPhase 3: Updating empirical models...")

    all_obs = store.list_observations(car=car_name, track=track.track_name)
    all_deltas = store.list_deltas(car=car_name, track=track.track_name)

    models = fit_models(all_obs, all_deltas, car_name, track.track_name)
    model_id = f"{car_name}_{track_key_short}_empirical".lower()
    store.save_model(model_id, models.to_dict())

    if verbose:
        print(f"  Observations in model: {models.observation_count}")
        print(f"  Relationships fitted: {len(models.relationships)}")
        if models.corrections:
            print(f"  Corrections computed: {len(models.corrections)}")
            for k, v in models.corrections.items():
                if isinstance(v, (int, float)):
                    print(f"    {k}: {v:.4f}")
        if models.most_sensitive_parameters:
            print(f"  Most impactful parameters:")
            for param, sens in models.most_sensitive_parameters[:3]:
                print(f"    {param}: {sens:+.4f} s/unit")

    # ── 6. Generate insights ─────────────────────────────────────────
    insights = _generate_insights(all_obs, all_deltas, models, car_name, track.track_name)
    insight_id = f"{car_name}_{track_key_short}_insights".lower()
    store.save_insights(insight_id, insights)

    if verbose:
        print(f"\nPhase 4: Insights updated.")
        for insight in insights.get("key_insights", [])[:5]:
            print(f"  * {insight}")

    # ── 7. Update cross-track global model ───────────────────────────
    try:
        from learner.cross_track import build_global_model
        global_model = build_global_model(car_name, store)
        if verbose and global_model.total_sessions >= 2:
            print(f"\nPhase 5: Global model updated.")
            print(f"  {global_model.total_sessions} sessions across "
                  f"{len(global_model.tracks_included)} track(s)")
            if global_model.anomalies:
                print(f"  Track anomalies detected: {len(global_model.anomalies)}")
    except Exception as e:
        if verbose:
            print(f"\nPhase 5: Global model skipped ({e})")

    # ── Summary ──────────────────────────────────────────────────────
    result = {
        "session_id": session_id,
        "observation_stored": True,
        "delta_computed": delta_result is not None,
        "models_updated": True,
        "total_sessions": len(all_obs),
        "new_learnings": [],
    }

    if delta_result and delta_result.key_finding:
        result["new_learnings"].append(delta_result.key_finding)

    # ── Auto-calibration: add this session to per-car calibration dataset ──
    cal_update = _update_auto_calibration(
        car_name=car_name,
        ibt_path=ibt_path,
        session_id=session_id,
        assessment=diag.assessment,
        lap_time_s=diag.lap_time_s,
        verbose=verbose,
    )
    result.update({k: v for k, v in cal_update.items() if k != "new_learning"})
    if cal_update.get("new_learning"):
        result["new_learnings"].append(cal_update["new_learning"])

    if verbose:
        print(f"\n{'='*60}")
        print(f"  INGEST COMPLETE — {len(all_obs)} sessions in knowledge base")
        print(f"{'='*60}\n")

    return result


def _apply_within_ibt_noise_floor(
    store: KnowledgeStore,
    lap_results: list[dict],
    *,
    verbose: bool = True,
) -> None:
    """Compute std-of-means across per-lap observations from one IBT and write
    the noise-floor fields back to each observation.

    The same setup ran each lap, so cross-lap variance in mean front_rh /
    rear_rh / lap_time is measurement noise, NOT setup effect. The fitter
    can use this as a per-IBT noise floor.
    """
    import statistics

    stored_ids = [r.get("session_id") for r in lap_results
                  if r.get("observation_stored") and r.get("session_id")]
    if len(stored_ids) < 2:
        return  # Single observation: noise floor is meaningless.

    obs_list: list[dict] = []
    for sid in stored_ids:
        data = store.load_observation(sid)
        if data:
            obs_list.append(data)
    if len(obs_list) < 2:
        return

    front_rh_means = [o.get("telemetry", {}).get("dynamic_front_rh_mm") for o in obs_list]
    rear_rh_means = [o.get("telemetry", {}).get("dynamic_rear_rh_mm") for o in obs_list]
    lap_times = [o.get("performance", {}).get("best_lap_time_s") for o in obs_list]

    def _std(xs):
        clean = [float(x) for x in xs if isinstance(x, (int, float)) and x > 0]
        if len(clean) < 2:
            return None
        return float(statistics.pstdev(clean))

    front_rh_std = _std(front_rh_means)
    rear_rh_std = _std(rear_rh_means)
    lap_time_std = _std(lap_times)
    n_laps = len(obs_list)

    for sid, data in zip(stored_ids, obs_list):
        data["setup_noise_floor_front_rh_mm"] = front_rh_std
        data["setup_noise_floor_rear_rh_mm"] = rear_rh_std
        data["setup_noise_floor_lap_time_s"] = lap_time_std
        data["setup_noise_floor_n_laps"] = n_laps
        store.save_observation(sid, data)

    if verbose:
        parts = []
        if front_rh_std is not None:
            parts.append(f"front_rh σ={front_rh_std:.3f}mm")
        if rear_rh_std is not None:
            parts.append(f"rear_rh σ={rear_rh_std:.3f}mm")
        if lap_time_std is not None:
            parts.append(f"lap_time σ={lap_time_std:.3f}s")
        if parts:
            print(f"  [noise-floor] across {n_laps} laps: " + ", ".join(parts))


def ingest_all_laps(
    car_name: str,
    ibt_path: str,
    wing: float | None = None,
    store: KnowledgeStore | None = None,
    verbose: bool = True,
) -> list[dict]:
    """Ingest every valid lap from a single IBT as separate observations.

    Each valid lap becomes its own observation with a unique session ID
    (base_session_id__lap_N). Delta detection runs between consecutive
    laps to capture the effect of any live cockpit adjustments (brake bias,
    ARB) made during the session.

    Returns a list of per-lap result dicts (same shape as ingest_ibt output).
    """
    store = store or KnowledgeStore()

    if verbose:
        print(f"\n{'='*60}")
        print(f"  LEARNER: Multi-lap ingest — {Path(ibt_path).name}")
        print(f"{'='*60}\n")

    # Resolve track name for session IDs and sanity checks.
    from track_model.build_profile import build_profile
    from track_model.ibt_parser import IBTFile

    ibt = IBTFile(ibt_path)
    track = build_profile(ibt_path)

    valid_laps = select_all_valid_laps(ibt, car=car_name, track=track.track_name)
    if not valid_laps:
        if verbose:
            print("  No valid laps found in this IBT.")
        return []

    if verbose:
        print(f"  Found {len(valid_laps)} valid lap(s): "
              f"{[ln for ln, _, _, _ in valid_laps]}")

    # If only one valid lap, delegate to the normal single-lap path.
    if len(valid_laps) == 1:
        ln, _, _, _ = valid_laps[0]
        result = ingest_ibt(car_name, ibt_path, wing=wing, lap=ln,
                            store=store, verbose=verbose)
        return [result]

    # Process each valid lap as a separate observation.
    base_session_id = store.session_id_from_ibt(ibt_path, car_name, track.track_name)
    results: list[dict] = []
    best_diag = None
    best_lap_time = float("inf")

    for lap_num, lap_time, _start, _end in valid_laps:
        if verbose:
            print(f"\n{'─'*40}")
            print(f"  Lap {lap_num} ({lap_time:.3f}s)")
            print(f"{'─'*40}")

        # Use a per-lap session ID so observations don't overwrite each other.
        lap_session_id = f"{base_session_id}__lap_{lap_num}"

        if store.has_observation(lap_session_id):
            if verbose:
                print(f"  Already ingested — skipping.")
            results.append({"session_id": lap_session_id, "observation_stored": False,
                            "delta_computed": False, "skipped": True})
            continue

        try:
            track_lap, measured, setup, driver, diag, corners, _ = _run_analyzer(
                car_name, ibt_path, wing, lap=lap_num
            )
        except (ValueError, Exception) as exc:
            if verbose:
                print(f"  Skipping lap {lap_num}: {exc}")
            continue

        obs = build_observation(
            session_id=lap_session_id,
            ibt_path=ibt_path,
            car_name=car_name,
            track_profile=track_lap,
            measured_state=measured,
            current_setup=setup,
            driver_profile_obj=driver,
            diagnosis_obj=diag,
            corners=corners,
        )

        store.save_observation(lap_session_id, obs.to_dict())
        if verbose:
            print(f"  Observation stored: {lap_session_id}")
            print(f"  Assessment: {diag.assessment}")
        if lap_time < best_lap_time:
            best_lap_time = lap_time
            best_diag = diag

        # Update index.
        idx = store.load_index()
        if lap_session_id not in idx["sessions"]:
            idx["sessions"].append(lap_session_id)
        idx["total_observations"] = len(idx["sessions"])
        if car_name not in idx["cars_seen"]:
            idx["cars_seen"].append(car_name)
        track_key = f"{track.track_name}_{track.track_config}"
        if track_key not in idx["tracks_seen"]:
            idx["tracks_seen"].append(track_key)
        store.save_index(idx)

        # Delta detection against the previous lap in this session.
        delta_result = None
        if results:
            prev_id = results[-1].get("session_id")
            if prev_id:
                prev_obs_data = store.load_observation(prev_id)
                if prev_obs_data:
                    obs_before = Observation.from_dict(prev_obs_data)
                    delta_result = detect_delta(obs_before, obs)
                    track_key_short = track.track_name.lower().split()[0]
                    delta_id = f"{car_name}_{track_key_short}_delta_lap_{lap_num:03d}"
                    store.save_delta(delta_id, delta_result.to_dict())
                    if verbose:
                        print(f"  Setup changes vs prev lap: {delta_result.num_setup_changes}")
                        if delta_result.lap_time_delta_s != 0:
                            faster = "FASTER" if delta_result.lap_time_delta_s < 0 else "SLOWER"
                            print(f"  Lap time: {abs(delta_result.lap_time_delta_s):.3f}s {faster}")

        results.append({
            "session_id": lap_session_id,
            "observation_stored": True,
            "delta_computed": delta_result is not None,
            "lap_number": lap_num,
            "lap_time_s": lap_time,
        })

    # ── Within-IBT measurement-noise floor ────────────────────────────
    # Same setup, multiple laps → std-of-means is driver/conditions/noise,
    # not setup effect. Write back to each per-lap Observation so the
    # auto_calibrate fitter can use it as a heteroscedastic weight.
    _apply_within_ibt_noise_floor(store, results, verbose=verbose)

    # Fit models with all accumulated observations for this car/track.
    all_obs = store.list_observations(car=car_name, track=track.track_name)
    all_deltas = store.list_deltas(car=car_name, track=track.track_name)
    track_key_short = track.track_name.lower().split()[0]

    models = fit_models(all_obs, all_deltas, car_name, track.track_name)
    model_id = f"{car_name}_{track_key_short}_empirical".lower()
    store.save_model(model_id, models.to_dict())

    insights = _generate_insights(all_obs, all_deltas, models, car_name, track.track_name)
    insight_id = f"{car_name}_{track_key_short}_insights".lower()
    store.save_insights(insight_id, insights)

    if best_diag is not None:
        cal_update = _update_auto_calibration(
            car_name=car_name,
            ibt_path=ibt_path,
            session_id=base_session_id,
            assessment=best_diag.assessment,
            lap_time_s=best_diag.lap_time_s,
            verbose=verbose,
        )
        if cal_update and results:
            results[-1].update(cal_update)

    stored_count = sum(1 for r in results if r.get("observation_stored"))
    if verbose:
        print(f"\n{'='*60}")
        print(f"  MULTI-LAP INGEST COMPLETE")
        print(f"  {stored_count} new observation(s) from {len(valid_laps)} valid lap(s)")
        print(f"  Total observations in store: {len(all_obs)}")
        print(f"{'='*60}\n")

    return results


def rebuild_track_learnings(
    car_name: str,
    track: str,
    *,
    store: KnowledgeStore | None = None,
    repair_invalid_only: bool = True,
    verbose: bool = True,
) -> dict:
    """Rebuild stored observations, deltas, models, and insights for one car/track."""
    store = store or KnowledgeStore()
    repo_root = Path(__file__).resolve().parent.parent

    observations = sorted(
        store.list_observations(car=car_name, track=track),
        key=lambda obs: obs.get("session_id", ""),
    )
    if not observations:
        return {
            "repaired_sessions": [],
            "removed_sessions": [],
            "skipped_sessions": [],
            "rebuilt_deltas": 0,
            "model_updated": False,
            "insights_updated": False,
        }

    repaired_sessions: list[str] = []
    removed_sessions: list[str] = []
    skipped_sessions: list[str] = []

    for existing in observations:
        lap_time = existing.get("performance", {}).get("best_lap_time_s")
        if repair_invalid_only and is_plausible_lap_time(lap_time, car_name, existing.get("track", track)):
            continue

        ibt_ref = existing.get("ibt_path", "")
        ibt_path = Path(ibt_ref)
        if not ibt_path.is_absolute():
            ibt_path = repo_root / ibt_path
        if not ibt_path.exists():
            skipped_sessions.append(existing.get("session_id", ""))
            if verbose:
                print(f"Skipping rebuild for missing IBT: {ibt_path}")
            continue

        try:
            track_profile, measured, setup, driver, diag, corners, _ibt = _run_analyzer(
                car_name,
                str(ibt_path),
            )
        except ValueError as exc:
            store.observation_path(existing["session_id"]).unlink(missing_ok=True)
            removed_sessions.append(existing["session_id"])
            if verbose:
                print(f"Removed observation with no valid lap: {existing['session_id']} ({exc})")
            continue
        rebuilt = build_observation(
            session_id=existing["session_id"],
            ibt_path=ibt_ref or str(ibt_path),
            car_name=car_name,
            track_profile=track_profile,
            measured_state=measured,
            current_setup=setup,
            driver_profile_obj=driver,
            diagnosis_obj=diag,
            corners=corners,
        )
        store.save_observation(existing["session_id"], rebuilt.to_dict())
        repaired_sessions.append(existing["session_id"])
        if verbose:
            print(f"Rebuilt observation: {existing['session_id']} ({diag.lap_time_s:.3f}s)")

    track_key_short = track.lower().split()[0]
    for delta_path in (store.base / "deltas").glob(f"{car_name}_{track_key_short}_delta_*.json"):
        delta_path.unlink()

    observations = sorted(
        store.list_observations(car=car_name, track=track),
        key=lambda obs: obs.get("session_id", ""),
    )
    rebuilt_deltas = 0
    for idx in range(1, len(observations)):
        delta_result = detect_delta(
            Observation.from_dict(observations[idx - 1]),
            Observation.from_dict(observations[idx]),
        )
        delta_id = f"{car_name}_{track_key_short}_delta_{idx:03d}"
        store.save_delta(delta_id, delta_result.to_dict())
        rebuilt_deltas += 1

    all_obs = store.list_observations(car=car_name, track=track)
    all_deltas = store.list_deltas(car=car_name, track=track)
    models = fit_models(all_obs, all_deltas, car_name, track)
    model_id = f"{car_name}_{track_key_short}_empirical".lower()
    store.save_model(model_id, models.to_dict())

    insights = _generate_insights(all_obs, all_deltas, models, car_name, track)
    insight_id = f"{car_name}_{track_key_short}_insights".lower()
    store.save_insights(insight_id, insights)

    try:
        from learner.cross_track import build_global_model
        build_global_model(car_name, store)
    except Exception as exc:
        if verbose:
            print(f"Global model rebuild skipped: {exc}")

    all_store_obs = store.list_observations()
    idx = store.load_index()
    idx["sessions"] = sorted(obs.get("session_id", "") for obs in all_store_obs if obs.get("session_id"))
    idx["total_observations"] = len(all_store_obs)
    idx["total_deltas"] = len(list((store.base / "deltas").glob("*.json")))
    idx["cars_seen"] = sorted({obs.get("car", "") for obs in all_store_obs if obs.get("car")})
    idx["tracks_seen"] = sorted(
        {
            f"{obs.get('track', '')}_{obs.get('track_config', '')}".rstrip("_")
            for obs in all_store_obs
            if obs.get("track")
        }
    )
    store.save_index(idx)

    return {
        "repaired_sessions": repaired_sessions,
        "removed_sessions": removed_sessions,
        "skipped_sessions": skipped_sessions,
        "rebuilt_deltas": rebuilt_deltas,
        "model_updated": True,
        "insights_updated": True,
    }


def _generate_insights(
    observations: list[dict],
    deltas: list[dict],
    models,
    car: str,
    track: str,
) -> dict:
    """Generate human-readable insights from accumulated data."""
    insights = {
        "car": car,
        "track": track,
        "session_count": len(observations),
        "key_insights": [],
        "setup_trends": [],
        "unresolved_questions": [],
    }

    if len(observations) < 2:
        insights["key_insights"].append(
            f"Only {len(observations)} session(s) — need more data for insights."
        )
        return insights

    # Lap time trend
    lap_times = []
    for obs in observations:
        lt = obs.get("performance", {}).get("best_lap_time_s", 0)
        if lt > 0:
            lap_times.append(lt)
    lap_times = filter_plausible_lap_times(lap_times, car=car, track=track)
    if lap_times:
        best = min(lap_times)
        worst = max(lap_times)
        improvement = worst - best
        insights["key_insights"].append(
            f"Lap time range: {best:.3f}s – {worst:.3f}s "
            f"({improvement:.3f}s window across {len(lap_times)} sessions)"
        )

    # High- and medium-confidence findings from deltas (skip trivial)
    for d in deltas:
        conf = d.get("confidence_level", "")
        if conf in ("high", "medium") and d.get("key_finding"):
            insights["key_insights"].append(d["key_finding"])

    # Setup parameter trends — architecture-aware (audit DEGRADED #12).
    # For GT3 cars, surface corner-spring + splitter trends; for GTP cars,
    # surface heave/third trends.
    try:
        from car_model.cars import SuspensionArchitecture, get_car
        car_obj = get_car(car, apply_calibration=False)
        is_gt3 = car_obj.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL
    except Exception:
        is_gt3 = False
    if is_gt3:
        params_to_track = [
            "front_corner_spring_nmm", "rear_corner_spring_nmm",
            "rear_arb_blade", "front_camber_deg", "rear_camber_deg",
            "splitter_height_mm",
        ]
    else:
        params_to_track = ["front_heave_nmm", "rear_third_nmm", "rear_arb_blade",
                            "front_camber_deg", "rear_camber_deg"]
    for param in params_to_track:
        values = []
        for obs in observations:
            v = obs.get("setup", {}).get(param)
            if v is not None:
                values.append(float(v))
        if len(values) >= 2:
            if values[-1] != values[0]:
                direction = "increasing" if values[-1] > values[0] else "decreasing"
                insights["setup_trends"].append(
                    f"{param}: trending {direction} "
                    f"({values[0]} -> {values[-1]} over {len(values)} sessions)"
                )

    # Recurring problems
    problem_counts: dict[str, int] = {}
    for obs in observations:
        for p in obs.get("diagnosis", {}).get("problems", []):
            key = f"{p['category']}:{p['symptom']}"
            problem_counts[key] = problem_counts.get(key, 0) + 1

    recurring = [(k, c) for k, c in problem_counts.items()
                 if c >= len(observations) * 0.5]  # appears in 50%+ of sessions
    for prob, count in sorted(recurring, key=lambda x: -x[1]):
        insights["unresolved_questions"].append(
            f"{prob} appears in {count}/{len(observations)} sessions — may need attention"
        )

    # Empirical corrections that disagree with physics
    if models and models.corrections:
        rg = models.corrections.get("roll_gradient_measured_mean", 0)
        if rg > 0:
            insights["key_insights"].append(
                f"Measured roll gradient: {rg:.3f} deg/g "
                f"(+/- {models.corrections.get('roll_gradient_measured_std', 0):.3f})"
            )

    return insights


# ── CLI ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="IOptimal Learner — ingest IBT sessions and build knowledge"
    )
    # GT3 Phase 2 W9.1 — F9 fix. Pre-W9.1 the help string was empty; with
    # GT3 canonical names landing in the registry the user needs to know
    # which keys are valid. The choices list is enforced at parse time so
    # mistyped GT3 names fail loudly.
    from car_model.cars import _CARS as _CAR_REGISTRY
    _car_choices = sorted(_CAR_REGISTRY.keys())
    parser.add_argument(
        "--car",
        type=str,
        choices=_car_choices,
        help="Car canonical name. Choices: " + ", ".join(_car_choices) +
             " (e.g., bmw, bmw_m4_gt3, porsche_992_gt3r).",
    )
    parser.add_argument("--ibt", type=str, help="Path to IBT file")
    parser.add_argument("--wing", type=float, help="Wing angle override")
    parser.add_argument("--lap", type=int,
                        help="Specific lap number to analyze (implies --single-lap)")
    # --all-laps is now the DEFAULT behaviour. The flag is kept as a
    # backward-compatible no-op so existing scripts don't break.
    parser.add_argument("--all-laps", action="store_true", default=False,
                        dest="all_laps_legacy",
                        help="(Default behaviour — kept for backward compatibility)")
    parser.add_argument("--single-lap", action="store_true", default=False,
                        dest="single_lap",
                        help="Legacy: ingest only the best lap as one observation")
    parser.add_argument("--status", action="store_true",
                        help="Show knowledge store status")
    parser.add_argument("--recall", action="store_true",
                        help="Dump all knowledge for --car/--track")
    parser.add_argument("--track", type=str, help="Track name (for --recall)")

    args = parser.parse_args()
    store = KnowledgeStore()

    if args.status:
        idx = store.load_index()
        print(f"IOptimal Knowledge Store")
        print(f"{'='*40}")
        print(f"Total sessions: {idx.get('total_observations', 0)}")
        print(f"Total deltas: {idx.get('total_deltas', 0)}")
        print(f"Cars: {', '.join(idx.get('cars_seen', []))}")
        print(f"Tracks: {', '.join(idx.get('tracks_seen', []))}")
        if idx.get("last_updated"):
            print(f"Last updated: {idx['last_updated']}")
        return

    if args.recall:
        if not args.car:
            print("ERROR: --recall requires --car")
            sys.exit(1)
        track = args.track or ""
        recall = KnowledgeRecall(store)
        print(recall.knowledge_summary(args.car, track))
        return

    if not args.car or not args.ibt:
        print("ERROR: --car and --ibt are required for ingestion")
        print("Usage: python -m learner.ingest --car bmw --ibt path/to/session.ibt")
        sys.exit(1)

    # Default: all-laps. Switch to single-lap when --single-lap is passed
    # OR when a specific --lap N is requested (single-lap by definition).
    use_single_lap = args.single_lap or (args.lap is not None)
    if use_single_lap:
        ingest_ibt(
            car_name=args.car,
            ibt_path=args.ibt,
            wing=args.wing,
            lap=args.lap,
            store=store,
        )
    else:
        ingest_all_laps(
            car_name=args.car,
            ibt_path=args.ibt,
            wing=args.wing,
            store=store,
        )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
