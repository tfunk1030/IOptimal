#!/usr/bin/env python3
"""Cadillac V-Series.R comprehensive calibration script.

Extracts CalibrationPoints from ALL available Cadillac IBT files,
combines with existing calibration data, generates virtual anchors
from physics priors, and fits calibration models.
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# Fix encoding for Windows console
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

def main():
    from car_model.auto_calibrate import (
        CalibrationPoint,
        extract_point_from_ibt,
        fit_models_from_points,
        save_calibrated_models,
        load_calibrated_models,
        _setup_key,
    )
    from dataclasses import asdict

    CAR = "cadillac"
    CALIB_DIR = PROJECT_ROOT / "data" / "calibration" / CAR
    CALIB_DIR.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------------
    # Step 1: Find ALL Cadillac IBT files
    # ----------------------------------------------------------------
    ibt_dir = PROJECT_ROOT / "ibtfiles"
    cadillac_ibts = []
    for p in ibt_dir.rglob("*.ibt"):
        name_lower = p.name.lower()
        if "cadillac" in name_lower or "caddy" in name_lower:
            cadillac_ibts.append(p)

    print(f"\n{'='*70}")
    print(f"  Cadillac V-Series.R Calibration")
    print(f"  {datetime.now(timezone.utc).isoformat()}")
    print(f"{'='*70}")
    print(f"\n  Found {len(cadillac_ibts)} Cadillac IBT files:")
    for p in sorted(cadillac_ibts):
        sz_mb = p.stat().st_size / 1e6
        print(f"    {p.name}  ({sz_mb:.1f} MB)")

    # ----------------------------------------------------------------
    # Step 2: Extract CalibrationPoints from IBTs
    # ----------------------------------------------------------------
    print(f"\n  Extracting calibration points from IBTs...")
    extracted_points = []
    failed_ibts = []
    for ibt_path in sorted(cadillac_ibts):
        try:
            pt = extract_point_from_ibt(ibt_path, car_name=CAR)
            if pt is not None:
                extracted_points.append(pt)
                print(f"    OK: {ibt_path.name}")
            else:
                failed_ibts.append((ibt_path.name, "returned None"))
                print(f"    SKIP: {ibt_path.name} (returned None)")
        except Exception as e:
            failed_ibts.append((ibt_path.name, str(e)))
            print(f"    FAIL: {ibt_path.name}: {e}")

    print(f"\n  Extracted {len(extracted_points)} points from IBTs")
    if failed_ibts:
        print(f"  Failed/Skipped: {len(failed_ibts)}")
        for name, reason in failed_ibts:
            print(f"    {name}: {reason}")

    # ----------------------------------------------------------------
    # Step 3: Merge with existing calibration_points.json
    # ----------------------------------------------------------------
    existing_points_path = CALIB_DIR / "calibration_points.json"
    existing_points = []
    if existing_points_path.exists():
        with open(existing_points_path, encoding="utf-8") as f:
            raw = json.load(f)
        for entry in raw:
            pt = CalibrationPoint(**{
                k: v for k, v in entry.items()
                if k in CalibrationPoint.__dataclass_fields__
            })
            existing_points.append(pt)
        print(f"\n  Loaded {len(existing_points)} existing calibration points")

    # Merge: use session_id for dedup
    all_points_map = {}
    for pt in existing_points:
        all_points_map[pt.session_id] = pt
    # New IBT extractions take priority
    for pt in extracted_points:
        all_points_map[pt.session_id] = pt

    all_points = list(all_points_map.values())
    print(f"  Total unique points after merge: {len(all_points)}")

    # Deduplicate by setup fingerprint
    seen_setups = set()
    unique_points = []
    for pt in all_points:
        key = _setup_key(pt)
        if key not in seen_setups:
            seen_setups.add(key)
            unique_points.append(pt)
    print(f"  Unique setup configurations: {len(unique_points)}")

    # ----------------------------------------------------------------
    # Step 4: Save updated calibration_points.json
    # ----------------------------------------------------------------
    points_data = [asdict(pt) for pt in all_points]
    with open(existing_points_path, "w", encoding="utf-8") as f:
        json.dump(points_data, f, indent=2)
    print(f"\n  Saved {len(all_points)} points to {existing_points_path}")

    # ----------------------------------------------------------------
    # Step 5: Fit models (virtual anchors are auto-generated inside)
    # ----------------------------------------------------------------
    print(f"\n  Fitting calibration models...")
    print(f"  (virtual anchors from Unit 9 will be auto-generated)")
    models = fit_models_from_points(CAR, all_points)

    # ----------------------------------------------------------------
    # Step 6: Save models
    # ----------------------------------------------------------------
    save_calibrated_models(CAR, models)
    models_path = CALIB_DIR / "models.json"
    print(f"\n  Saved models to {models_path}")

    # ----------------------------------------------------------------
    # Step 7: Report results
    # ----------------------------------------------------------------
    print(f"\n{'='*70}")
    print(f"  Calibration Results")
    print(f"{'='*70}")
    print(f"  Sessions:       {models.n_sessions}")
    print(f"  Unique setups:  {models.n_unique_setups}")
    print(f"  Calibrated:     {models.calibration_complete}")

    regression_names = [
        "front_ride_height", "rear_ride_height",
        "torsion_bar_turns", "torsion_bar_defl",
        "front_shock_defl_static", "front_shock_defl_max",
        "rear_shock_defl_static", "rear_shock_defl_max",
        "heave_spring_defl_static", "heave_spring_defl_max",
        "heave_slider_defl_static",
        "rear_spring_defl_static", "rear_spring_defl_max",
        "third_spring_defl_static", "third_spring_defl_max",
        "third_slider_defl_static",
        "torsion_bar_defl_direct",
        "third_slider_defl_direct",
    ]

    print(f"\n  {'Model':<35} {'R2':>8} {'Q2':>8} {'RMSE':>8} {'n':>4} {'Cal':>5}")
    print(f"  {'-'*35} {'-'*8} {'-'*8} {'-'*8} {'-'*4} {'-'*5}")
    for name in regression_names:
        m = getattr(models, name, None)
        if m is None:
            print(f"  {name:<35}    null")
            continue
        r2 = f"{m.r_squared:.3f}" if m.r_squared else "---"
        q2 = f"{m.q_squared:.3f}" if hasattr(m, 'q_squared') and m.q_squared is not None else "---"
        rmse = f"{m.rmse:.2f}" if hasattr(m, 'rmse') and m.rmse else "---"
        n = getattr(m, 'n_samples', '?')
        cal = "YES" if getattr(m, 'is_calibrated', False) else "no"
        print(f"  {name:<35} {r2:>8} {q2:>8} {rmse:>8} {n:>4} {cal:>5}")

    # Physics parameters
    print(f"\n  m_eff_front: {models.m_eff_front_kg:.1f} kg")
    print(f"  m_eff_rear:  {models.m_eff_rear_kg:.1f} kg")
    print(f"  Aero front:  {models.aero_front_compression_mm} mm")
    print(f"  Aero rear:   {models.aero_rear_compression_mm} mm")

    # Status dict
    print(f"\n  Status keys:")
    for k, v in models.status.items():
        v_str = str(v)[:80]
        print(f"    {k}: {v_str}")

    print(f"\n{'='*70}")
    print(f"  Calibration complete!")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
