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
    python -m learner.ingest --car bmw --ibt path/to/session.ibt
    python -m learner.ingest --car bmw --ibt path/to/session.ibt --wing 17
    python -m learner.ingest --car bmw --ibt path/to/session.ibt --all-laps
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
    driver = analyze_driver(ibt, corners, car, tick_rate=ibt.tick_rate)
    refine_driver_with_measured(driver, measured)
    thresholds = compute_adaptive_thresholds(track, car, driver)
    diag = diagnose(measured, setup, car, thresholds)

    return track, measured, setup, driver, diag, corners, ibt


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
    track_key_short = track.track_name.lower().split()[0]  # "sebring"

    prior_obs = store.list_observations(car=car_name, track=track.track_name)
    # Filter to sessions BEFORE this one (by timestamp or position)
    prior_obs = [o for o in prior_obs if o["session_id"] != session_id]

    if prior_obs:
        from learner.observation import Observation
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
    # Runs silently (never blocks ingest). Once 5+ unique-setup sessions
    # accumulate, models are auto-fitted and saved to data/calibration/{car}/.
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
        if session_id not in existing_ids:
            pt = extract_point_from_ibt(ibt_path, car_name)
            if pt is not None:
                pt.session_id = session_id  # use same ID for consistency
                pt.assessment = diag.assessment
                pt.lap_time_s = diag.lap_time_s
                cal_points.append(pt)
                save_calibration_points(car_name, cal_points)
                # Count unique setups to check if we can fit models
                unique: set[tuple] = set()
                for p2 in cal_points:
                    key = (round(p2.front_heave_setting, 1), round(p2.rear_third_setting, 1),
                           round(p2.front_torsion_od_mm, 3), round(p2.front_pushrod_mm, 1),
                           round(p2.rear_pushrod_mm, 1))
                    unique.add(key)
                n_unique = len(unique)
                result["cal_point_added"] = True
                result["cal_unique_setups"] = n_unique
                if n_unique >= 5:
                    # Auto-fit models
                    cal_models = fit_models_from_points(car_name, cal_points)
                    # Preserve existing spring lookup tables
                    existing_saved = load_calibrated_models(car_name)
                    if existing_saved:
                        if existing_saved.front_torsion_lookup and not cal_models.front_torsion_lookup:
                            cal_models.front_torsion_lookup = existing_saved.front_torsion_lookup
                        if existing_saved.rear_torsion_lookup and not cal_models.rear_torsion_lookup:
                            cal_models.rear_torsion_lookup = existing_saved.rear_torsion_lookup
                    save_calibrated_models(car_name, cal_models)
                    if cal_models.calibration_complete:
                        result["new_learnings"].append(
                            f"Auto-calibration complete: {n_unique} unique setups, "
                            f"deflection model fitted. Run 'ioptimal calibrate --car {car_name} "
                            f"--status' for details."
                        )
                    if verbose:
                        print(f"  [calibrate] {n_unique} unique setups — "
                              f"models {'fitted' if cal_models.calibration_complete else 'pending'}")
    except Exception:
        # Auto-calibration is never allowed to break normal ingest
        pass

    if verbose:
        print(f"\n{'='*60}")
        print(f"  INGEST COMPLETE — {len(all_obs)} sessions in knowledge base")
        print(f"{'='*60}\n")

    return result


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

    # High-confidence findings from deltas
    for d in deltas:
        if d.get("confidence_level") == "high" and d.get("key_finding"):
            insights["key_insights"].append(d["key_finding"])

    # Setup parameter trends
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
    parser.add_argument("--car", type=str, help="Car name (e.g., bmw)")
    parser.add_argument("--ibt", type=str, help="Path to IBT file")
    parser.add_argument("--wing", type=float, help="Wing angle override")
    parser.add_argument("--lap", type=int, help="Specific lap number to analyze")
    parser.add_argument("--all-laps", action="store_true",
                        help="Ingest every valid lap as a separate observation")
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

    if args.all_laps:
        ingest_all_laps(
            car_name=args.car,
            ibt_path=args.ibt,
            wing=args.wing,
            store=store,
        )
    else:
        ingest_ibt(
            car_name=args.car,
            ibt_path=args.ibt,
            wing=args.wing,
            lap=args.lap,
            store=store,
        )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
