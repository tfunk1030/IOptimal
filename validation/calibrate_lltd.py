"""Calibrate LLTD target from IBT sessions with varied ARB/spring settings.

Correlates measured lateral load transfer distribution (LLTD) with lap time
to identify the LLTD range that produces the fastest laps.

Requires 10+ sessions ingested via learner.ingest with varied ARB/spring
settings to provide a meaningful LLTD sweep.

Usage:
    python -m validation.calibrate_lltd --car ferrari --track sebring
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def calibrate_lltd(car_name: str, track_name: str) -> dict | None:
    """Find optimal LLTD target from lap time correlation.

    Returns a dict with lltd_target, lltd_range, correlation, n_sessions
    if successful, else None.
    """
    from learner.knowledge_store import KnowledgeStore

    store = KnowledgeStore()
    track_key = track_name.lower().split()[0]
    obs_ids = store.list_observations(car=car_name, track=track_key)

    if len(obs_ids) < 10:
        print(f"Need 10+ sessions for LLTD calibration, have {len(obs_ids)}")
        return None

    # Collect LLTD + lap time pairs
    pairs: list[tuple[float, float]] = []
    for oid in obs_ids:
        # list_observations returns dicts with 'session_id'; load_observation expects a string
        obs_key = oid.get("session_id", oid) if isinstance(oid, dict) else oid
        obs = store.load_observation(obs_key)
        if obs is None:
            continue

        telem = obs.get("telemetry", {})
        perf = obs.get("performance", {})

        lltd = telem.get("lltd_measured", 0)
        lap_time = perf.get("lap_time_s", 0) or perf.get("best_lap_time_s", 0)

        # Plausibility: LLTD between 0.30 and 0.70, lap time > 30s
        if 0.30 < lltd < 0.70 and lap_time > 30.0:
            pairs.append((lltd, lap_time))

    if len(pairs) < 10:
        print(f"Only {len(pairs)} valid LLTD + lap time pairs, need 10+")
        return None

    lltd_arr = np.array([p[0] for p in pairs])
    time_arr = np.array([p[1] for p in pairs])

    # Correlation: negative means higher LLTD → faster (lower time)
    corr = float(np.corrcoef(lltd_arr, time_arr)[0, 1])

    # Find optimal LLTD: fit quadratic to find minimum lap time
    # lap_time = a*lltd^2 + b*lltd + c
    coeffs = np.polyfit(lltd_arr, time_arr, 2)
    a, b, c = coeffs

    if a > 0:
        # Parabola opens upward — minimum exists
        lltd_optimal = -b / (2 * a)
        # Clamp to observed range
        lltd_optimal = max(float(lltd_arr.min()), min(float(lltd_arr.max()), lltd_optimal))
    else:
        # Monotonic relationship — use the LLTD from fastest sessions
        n_top = max(3, len(pairs) // 5)
        fastest_idx = np.argsort(time_arr)[:n_top]
        lltd_optimal = float(np.mean(lltd_arr[fastest_idx]))

    # Define confidence range (±1 std of fastest sessions' LLTD)
    n_top = max(3, len(pairs) // 5)
    fastest_idx = np.argsort(time_arr)[:n_top]
    lltd_std = float(np.std(lltd_arr[fastest_idx]))
    lltd_range = (
        round(max(0.30, lltd_optimal - lltd_std), 4),
        round(min(0.70, lltd_optimal + lltd_std), 4),
    )

    result = {
        "lltd_target": round(lltd_optimal, 4),
        "lltd_range": lltd_range,
        "correlation": round(corr, 4),
        "n_sessions": len(pairs),
        "quadratic_coeffs": [round(float(x), 6) for x in coeffs],
    }

    # Save to calibration models
    from car_model.auto_calibrate import load_calibrated_models, save_calibrated_models, CarCalibrationModels

    models = load_calibrated_models(car_name) or CarCalibrationModels(car=car_name)
    models.measured_lltd_target = result["lltd_target"]
    save_calibrated_models(car_name, models)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Calibrate LLTD target from lap time correlation"
    )
    parser.add_argument("--car", required=True, help="Car name (e.g., ferrari)")
    parser.add_argument("--track", required=True, help="Track name (e.g., sebring)")
    args = parser.parse_args()

    print(f"Calibrating LLTD target for {args.car} at {args.track}...")
    result = calibrate_lltd(args.car, args.track)

    if result is None:
        print("Calibration failed — insufficient data.")
        sys.exit(1)

    print(f"\nLLTD calibration results ({result['n_sessions']} sessions):")
    print(f"  Optimal LLTD:  {result['lltd_target']:.4f}")
    print(f"  Optimal range: {result['lltd_range'][0]:.4f} – {result['lltd_range'][1]:.4f}")
    print(f"  Correlation:   {result['correlation']:.4f} (LLTD vs lap time)")
    print("\nSaved to calibration models.")


if __name__ == "__main__":
    main()
