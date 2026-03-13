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
    python -m learner.ingest --status                    # show what we know
    python -m learner.ingest --car bmw --track sebring --recall  # knowledge dump
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from learner.knowledge_store import KnowledgeStore
from learner.observation import build_observation
from learner.delta_detector import detect_delta, SessionDelta
from learner.empirical_models import fit_models
from learner.recall import KnowledgeRecall


def _run_analyzer(car_name: str, ibt_path: str, wing: float | None = None,
                   lap: int | None = None):
    """Run the analyzer pipeline and return all intermediate results.

    Follows the same flow as pipeline/produce.py but returns intermediate
    objects instead of writing output files.

    Returns: (track_profile, measured, current_setup, driver, diagnosis, corners, ibt)
    """
    from analyzer.adaptive_thresholds import compute_adaptive_thresholds
    from analyzer.diagnose import diagnose
    from analyzer.driver_style import analyze_driver
    from analyzer.extract import extract_measurements
    from analyzer.segment import segment_lap
    from analyzer.setup_reader import CurrentSetup
    from car_model.cars import get_car
    from track_model.build_profile import build_profile
    from track_model.ibt_parser import IBTFile

    car = get_car(car_name)
    ibt = IBTFile(ibt_path)
    track = build_profile(ibt_path)

    # Extract measurements (handles lap selection internally)
    measured = extract_measurements(ibt_path, car, lap=lap)

    # Find lap indices for segment/driver (needs explicit start/end)
    if lap:
        for ln, s, e in ibt.lap_boundaries():
            if ln == lap:
                start, end = s, e
                break
        else:
            raise ValueError(f"Lap {lap} not found")
    else:
        result = ibt.best_lap_indices()
        if result is None:
            raise ValueError("No valid laps found")
        start, end = result

    setup = CurrentSetup.from_ibt(ibt)
    corners = segment_lap(ibt, start, end, car=car, tick_rate=ibt.tick_rate)
    driver = analyze_driver(ibt, corners, car, tick_rate=ibt.tick_rate)
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
    """Full ingest cycle: analyze → observe → delta → models → insights.

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
                    print(f"\n  Causal hypotheses (≥50% confidence):")
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

    if verbose:
        print(f"\n{'='*60}")
        print(f"  INGEST COMPLETE — {len(all_obs)} sessions in knowledge base")
        print(f"{'='*60}\n")

    return result


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
                f"(± {models.corrections.get('roll_gradient_measured_std', 0):.3f})"
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

    ingest_ibt(
        car_name=args.car,
        ibt_path=args.ibt,
        wing=args.wing,
        lap=args.lap,
        store=store,
    )


if __name__ == "__main__":
    main()
