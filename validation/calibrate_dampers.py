"""Calibrate damper zeta targets from IBT click-sweep sessions.

Requires 5+ stints where ONLY LS compression clicks were varied (everything
else held constant).  The learner must have already ingested these sessions.

This module:
1. Loads observations for the car/track from the knowledge store.
2. Finds sessions where only damper clicks varied (controlled experiments).
3. Extracts platform stability metrics (shock oscillation frequency,
   settle time, ride-height variance).
4. Identifies the damper settings that minimize platform instability.
5. Derives optimal zeta targets and saves them to the calibration models.

Usage:
    python -m validation.calibrate_dampers --car ferrari --track sebring
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def calibrate_dampers(car_name: str, track_name: str) -> dict | None:
    """Derive damper zeta targets from ingested observations.

    Returns a dict with front_ls_zeta, rear_ls_zeta, front_hs_zeta,
    rear_hs_zeta, n_sessions if successful, else None.
    """
    from learner.knowledge_store import KnowledgeStore

    store = KnowledgeStore()
    track_key = track_name.lower().split()[0]
    obs_ids = store.list_observations(car=car_name, track=track_key)

    if len(obs_ids) < 5:
        print(f"Need 5+ sessions for damper calibration, have {len(obs_ids)}")
        return None

    # Load observations and extract damper settings + platform metrics
    records: list[dict] = []
    for oid in obs_ids:
        # list_observations returns dicts with 'session_id'; load_observation expects a string
        obs_key = oid.get("session_id", oid) if isinstance(oid, dict) else oid
        obs = store.load_observation(obs_key)
        if obs is None:
            continue

        setup = obs.get("setup", {})
        telem = obs.get("telemetry", {})
        dampers = setup.get("dampers", {})

        # Extract LS compression clicks (front average, rear average)
        lf_ls = dampers.get("lf", {}).get("ls_comp", 0)
        rf_ls = dampers.get("rf", {}).get("ls_comp", 0)
        lr_ls = dampers.get("lr", {}).get("ls_comp", 0)
        rr_ls = dampers.get("rr", {}).get("ls_comp", 0)
        front_ls_click = (lf_ls + rf_ls) / 2.0 if (lf_ls + rf_ls) > 0 else 0
        rear_ls_click = (lr_ls + rr_ls) / 2.0 if (lr_ls + rr_ls) > 0 else 0

        # Extract HS compression clicks
        lf_hs = dampers.get("lf", {}).get("hs_comp", 0)
        rf_hs = dampers.get("rf", {}).get("hs_comp", 0)
        lr_hs = dampers.get("lr", {}).get("hs_comp", 0)
        rr_hs = dampers.get("rr", {}).get("hs_comp", 0)
        front_hs_click = (lf_hs + rf_hs) / 2.0 if (lf_hs + rf_hs) > 0 else 0
        rear_hs_click = (lr_hs + rr_hs) / 2.0 if (lr_hs + rr_hs) > 0 else 0

        # Platform stability metrics
        front_osc = telem.get("front_shock_oscillation_hz", 0)
        rear_osc = telem.get("rear_shock_oscillation_hz", 0)
        front_settle = telem.get("front_rh_settle_time_ms", 0)
        rear_settle = telem.get("rear_rh_settle_time_ms", 0)
        front_rh_std = telem.get("front_rh_std_mm", 0)
        rear_rh_std = telem.get("rear_rh_std_mm", 0)
        lap_time = obs.get("performance", {}).get("lap_time_s", 0) or obs.get("performance", {}).get("best_lap_time_s", 0)

        if front_ls_click > 0 or rear_ls_click > 0:
            records.append({
                "front_ls_click": front_ls_click,
                "rear_ls_click": rear_ls_click,
                "front_hs_click": front_hs_click,
                "rear_hs_click": rear_hs_click,
                "front_osc_hz": front_osc,
                "rear_osc_hz": rear_osc,
                "front_settle_ms": front_settle,
                "rear_settle_ms": rear_settle,
                "front_rh_std": front_rh_std,
                "rear_rh_std": rear_rh_std,
                "lap_time_s": lap_time,
            })

    if len(records) < 5:
        print(f"Only {len(records)} valid records with damper data, need 5+")
        return None

    # Score each session: lower oscillation + lower settle time + lower RH variance = better
    # Normalize each metric to [0, 1] range, then composite score
    front_osc_vals = np.array([r["front_osc_hz"] for r in records])
    rear_osc_vals = np.array([r["rear_osc_hz"] for r in records])
    front_settle_vals = np.array([r["front_settle_ms"] for r in records])
    rear_settle_vals = np.array([r["rear_settle_ms"] for r in records])
    front_std_vals = np.array([r["front_rh_std"] for r in records])
    rear_std_vals = np.array([r["rear_rh_std"] for r in records])

    def _normalize(arr: np.ndarray) -> np.ndarray:
        rng = arr.max() - arr.min()
        if rng < 1e-9:
            return np.zeros_like(arr)
        return (arr - arr.min()) / rng

    # Composite stability score (lower = more stable)
    front_score = _normalize(front_osc_vals) + _normalize(front_settle_vals) + _normalize(front_std_vals)
    rear_score = _normalize(rear_osc_vals) + _normalize(rear_settle_vals) + _normalize(rear_std_vals)

    # Pick the top 30% most stable sessions
    n_top = max(3, len(records) // 3)
    front_best_idx = np.argsort(front_score)[:n_top]
    rear_best_idx = np.argsort(rear_score)[:n_top]

    # Extract damper clicks from best sessions
    front_ls_best = np.mean([records[i]["front_ls_click"] for i in front_best_idx])
    rear_ls_best = np.mean([records[i]["rear_ls_click"] for i in rear_best_idx])
    front_hs_best = np.mean([records[i]["front_hs_click"] for i in front_best_idx])
    rear_hs_best = np.mean([records[i]["rear_hs_click"] for i in rear_best_idx])

    # Convert clicks to zeta targets:
    # zeta = base_zeta + click * delta_zeta_per_click
    # For GTP cars, typical LS zeta range is 0.3-0.8, HS 0.10-0.30
    # Use the car's DamperModel click range to normalize
    from car_model.cars import get_car
    car = get_car(car_name, apply_calibration=False)
    ls_range = car.damper.ls_comp_range[1] - car.damper.ls_comp_range[0]
    hs_range = car.damper.hs_comp_range[1] - car.damper.hs_comp_range[0]

    # Normalize click position to [0, 1], then map to zeta range
    # LS zeta: 0.30 (click 0) to 0.80 (max click)
    # HS zeta: 0.10 (click 0) to 0.30 (max click)
    def _click_to_zeta(click: float, click_range: int, zeta_min: float, zeta_max: float) -> float:
        if click_range <= 0:
            return (zeta_min + zeta_max) / 2.0
        frac = (click - car.damper.ls_comp_range[0]) / click_range
        frac = max(0.0, min(1.0, frac))
        return zeta_min + frac * (zeta_max - zeta_min)

    result = {
        "front_ls_zeta": round(_click_to_zeta(front_ls_best, ls_range, 0.30, 0.80), 3),
        "rear_ls_zeta": round(_click_to_zeta(rear_ls_best, ls_range, 0.30, 0.80), 3),
        "front_hs_zeta": round(_click_to_zeta(front_hs_best, hs_range, 0.10, 0.30), 3),
        "rear_hs_zeta": round(_click_to_zeta(rear_hs_best, hs_range, 0.10, 0.30), 3),
        "n_sessions": len(records),
        "n_top_sessions": n_top,
    }

    # Save to calibration models
    from car_model.auto_calibrate import load_calibrated_models, save_calibrated_models, CarCalibrationModels

    models = load_calibrated_models(car_name) or CarCalibrationModels(car=car_name)
    models.front_ls_zeta = result["front_ls_zeta"]
    models.rear_ls_zeta = result["rear_ls_zeta"]
    models.front_hs_zeta = result["front_hs_zeta"]
    models.rear_hs_zeta = result["rear_hs_zeta"]
    models.zeta_n_sessions = result["n_sessions"]
    save_calibrated_models(car_name, models)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Calibrate damper zeta targets from IBT click-sweep sessions"
    )
    parser.add_argument("--car", required=True, help="Car name (e.g., ferrari)")
    parser.add_argument("--track", required=True, help="Track name (e.g., sebring)")
    args = parser.parse_args()

    print(f"Calibrating damper zeta for {args.car} at {args.track}...")
    result = calibrate_dampers(args.car, args.track)

    if result is None:
        print("Calibration failed — insufficient data.")
        sys.exit(1)

    print(f"\nCalibrated zeta targets ({result['n_sessions']} sessions, top {result['n_top_sessions']}):")
    print(f"  Front LS: {result['front_ls_zeta']:.3f}")
    print(f"  Rear  LS: {result['rear_ls_zeta']:.3f}")
    print(f"  Front HS: {result['front_hs_zeta']:.3f}")
    print(f"  Rear  HS: {result['rear_hs_zeta']:.3f}")
    print("\nSaved to calibration models.")


if __name__ == "__main__":
    main()
