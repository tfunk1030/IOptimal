"""One-time calibration script: extract iRacing ground truth from all IBT files
and fit deflection/ride height models.

Usage:
    python -m car_model.calibrate_deflections

Reads all BMW IBT files, extracts setup inputs + iRacing-computed garage values,
fits regression models, and prints new coefficients with R² and RMSE.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from track_model.ibt_parser import IBTFile
from analyzer.setup_reader import CurrentSetup


def extract_all_sessions() -> list[dict]:
    """Extract setup inputs + computed values from all BMW IBT files."""
    ibt_dir = Path("ibtfiles")
    rows = []

    for ibt_path in sorted(ibt_dir.glob("bmw*.ibt")):
        try:
            ibt = IBTFile(str(ibt_path))
            setup = CurrentSetup.from_ibt(ibt)
        except Exception as e:
            print(f"  [skip] {ibt_path.name}: {e}", file=sys.stderr)
            continue

        # Skip sessions with no valid data
        if setup.front_heave_nmm <= 0 or setup.lf_corner_weight_n <= 0:
            print(f"  [skip] {ibt_path.name}: missing heave or corner weight", file=sys.stderr)
            continue

        row = {
            "file": ibt_path.name,
            # Inputs
            "heave_nmm": setup.front_heave_nmm,
            "heave_perch_mm": setup.front_heave_perch_mm,
            "torsion_od_mm": setup.front_torsion_od_mm,
            "rear_spring_nmm": setup.rear_spring_nmm,
            "rear_spring_perch_mm": setup.rear_spring_perch_mm,
            "rear_third_nmm": setup.rear_third_nmm,
            "rear_third_perch_mm": setup.rear_third_perch_mm,
            "front_pushrod_mm": setup.front_pushrod_mm,
            "rear_pushrod_mm": setup.rear_pushrod_mm,
            "fuel_l": setup.fuel_l,
            "front_camber_deg": setup.front_camber_deg,
            "rear_camber_deg": setup.rear_camber_deg,
            # iRacing computed (ground truth)
            "front_rh_mm": setup.static_front_rh_mm,
            "rear_rh_mm": setup.static_rear_rh_mm,
            "tb_turns": setup.torsion_bar_turns,
            "tb_defl_mm": setup.torsion_bar_defl_mm,
            "front_shock_defl_mm": setup.front_shock_defl_static_mm,
            "front_shock_defl_max_mm": setup.front_shock_defl_max_mm,
            "rear_shock_defl_mm": setup.rear_shock_defl_static_mm,
            "rear_shock_defl_max_mm": setup.rear_shock_defl_max_mm,
            "heave_defl_mm": setup.heave_spring_defl_static_mm,
            "heave_defl_max_mm": setup.heave_spring_defl_max_mm,
            "heave_slider_mm": setup.heave_slider_defl_static_mm,
            "heave_slider_max_mm": setup.heave_slider_defl_max_mm,
            "rear_spring_defl_mm": setup.rear_spring_defl_static_mm,
            "rear_spring_defl_max_mm": setup.rear_spring_defl_max_mm,
            "third_defl_mm": setup.third_spring_defl_static_mm,
            "third_defl_max_mm": setup.third_spring_defl_max_mm,
            "third_slider_mm": setup.third_slider_defl_static_mm,
            "third_slider_max_mm": setup.third_slider_defl_max_mm,
            "lf_corner_weight_n": setup.lf_corner_weight_n,
            "lr_corner_weight_n": setup.lr_corner_weight_n,
        }
        rows.append(row)

    return rows


def fit_linear(X: np.ndarray, y: np.ndarray, feature_names: list[str], model_name: str) -> np.ndarray:
    """Fit y = X @ beta via least squares, print results."""
    # Add intercept column
    ones = np.ones((X.shape[0], 1))
    X_aug = np.hstack([ones, X])
    beta, residuals, rank, sv = np.linalg.lstsq(X_aug, y, rcond=None)

    y_pred = X_aug @ beta
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1.0 - ss_res / max(ss_tot, 1e-12)
    rmse = np.sqrt(ss_res / len(y))
    max_err = np.max(np.abs(y - y_pred))

    print(f"\n{'=' * 60}")
    print(f"  {model_name}")
    print(f"  N={len(y)}  R²={r2:.4f}  RMSE={rmse:.3f}  MaxErr={max_err:.3f}")
    print(f"  intercept = {beta[0]:.6f}")
    for i, name in enumerate(feature_names):
        print(f"  {name} = {beta[i + 1]:.6f}")
    print(f"{'=' * 60}")

    # Print worst predictions
    errors = y - y_pred
    worst_idx = np.argsort(np.abs(errors))[-3:]
    for idx in worst_idx:
        print(f"  worst: pred={y_pred[idx]:.2f} actual={y[idx]:.2f} err={errors[idx]:.2f}")

    return beta


def main():
    print("Extracting ground truth from all BMW IBT files...")
    rows = extract_all_sessions()
    print(f"\nExtracted {len(rows)} sessions with valid data\n")

    if len(rows) < 5:
        print("Not enough data points for calibration. Need at least 5 sessions.")
        return

    # Save raw dataset
    out_path = Path("data/calibration_dataset.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(rows, f, indent=2)
    print(f"Saved calibration dataset to {out_path}")

    # Deduplicate by unique setup configuration
    seen = set()
    unique_rows = []
    for r in rows:
        key = (r["heave_nmm"], r["heave_perch_mm"], r["torsion_od_mm"],
               r["rear_spring_nmm"], r["rear_spring_perch_mm"],
               r["rear_third_nmm"], r["rear_third_perch_mm"],
               r["front_pushrod_mm"], r["rear_pushrod_mm"], r["fuel_l"])
        if key not in seen:
            seen.add(key)
            unique_rows.append(r)
    print(f"Unique setup configurations: {len(unique_rows)}")

    # Convert to numpy for fitting
    def col(name: str, source=unique_rows) -> np.ndarray:
        return np.array([r[name] for r in source])

    # ─── 1. Front Ride Height ───
    X = np.column_stack([
        col("heave_nmm"), col("heave_perch_mm"), col("front_camber_deg"),
        col("fuel_l"), col("front_pushrod_mm"), col("torsion_od_mm"),
    ])
    fit_linear(X, col("front_rh_mm"),
               ["heave_nmm", "heave_perch_mm", "front_camber_deg",
                "fuel_l", "front_pushrod_mm", "torsion_od_mm"],
               "Front Ride Height (mm)")

    # ─── 2. Rear Ride Height ───
    X = np.column_stack([
        col("rear_pushrod_mm"), col("rear_third_nmm"),
        col("rear_spring_nmm"), col("heave_perch_mm"),
        col("fuel_l"), col("rear_spring_perch_mm"),
    ])
    fit_linear(X, col("rear_rh_mm"),
               ["rear_pushrod_mm", "rear_third_nmm", "rear_spring_nmm",
                "heave_perch_mm", "fuel_l", "rear_spring_perch_mm"],
               "Rear Ride Height (mm)")

    # ─── 3. Torsion Bar Turns ───
    heave = col("heave_nmm")
    X = np.column_stack([
        1.0 / np.maximum(heave, 1.0),
        col("heave_perch_mm"),
        col("torsion_od_mm"),
    ])
    fit_linear(X, col("tb_turns"),
               ["1/heave_nmm", "heave_perch_mm", "torsion_od_mm"],
               "Torsion Bar Turns")

    # ─── 4. Torsion Bar Deflection ───
    # Physics form: defl = load / k_torsion
    # k_torsion = C * OD^4
    # Try: defl * OD^4 = a + b*heave + c*perch  (i.e., fit the load)
    od4 = col("torsion_od_mm") ** 4
    y_load = col("tb_defl_mm") * od4  # effective load * C
    X = np.column_stack([col("heave_nmm"), col("heave_perch_mm")])
    fit_linear(X, y_load,
               ["heave_nmm", "heave_perch_mm"],
               "TB Defl Load (defl * OD^4)")

    # ─── 5. Heave Spring Defl Static ───
    X = np.column_stack([
        1.0 / np.maximum(heave, 1.0),
        col("heave_perch_mm"),
        1.0 / np.maximum(od4, 1.0),
    ])
    fit_linear(X, col("heave_defl_mm"),
               ["1/heave_nmm", "heave_perch_mm", "1/OD^4"],
               "Heave Spring Defl Static (mm)")

    # ─── 6. Heave Spring Defl Max ───
    X = np.column_stack([heave])
    fit_linear(X, col("heave_defl_max_mm"),
               ["heave_nmm"],
               "Heave Spring Defl Max (mm)")

    # ─── 7. Heave Slider Defl Static ───
    X = np.column_stack([
        col("heave_nmm"), col("heave_perch_mm"), col("torsion_od_mm"),
    ])
    fit_linear(X, col("heave_slider_mm"),
               ["heave_nmm", "heave_perch_mm", "torsion_od_mm"],
               "Heave Slider Defl Static (mm)")

    # ─── 8. Front Shock Defl Static ───
    X = np.column_stack([col("front_pushrod_mm")])
    fit_linear(X, col("front_shock_defl_mm"),
               ["front_pushrod_mm"],
               "Front Shock Defl Static (mm)")

    # ─── 9. Rear Shock Defl Static ───
    X = np.column_stack([col("rear_pushrod_mm")])
    fit_linear(X, col("rear_shock_defl_mm"),
               ["rear_pushrod_mm"],
               "Rear Shock Defl Static (mm)")

    # ─── 10. Rear Spring Defl Static (force-balance) ───
    # defl = (eff_load - perch_coeff * perch) / spring_rate
    # → defl * spring_rate = eff_load - perch_coeff * perch
    y_load = col("rear_spring_defl_mm") * col("rear_spring_nmm")
    X = np.column_stack([col("rear_spring_perch_mm")])
    fit_linear(X, y_load,
               ["rear_spring_perch_mm"],
               "Rear Spring Load (defl * rate)")

    # ─── 11. Rear Spring Defl Max ───
    y = col("rear_spring_defl_max_mm")
    if np.std(y) > 0.1:
        X = np.column_stack([col("rear_spring_nmm"), col("rear_spring_perch_mm")])
        fit_linear(X, y,
                   ["rear_spring_nmm", "rear_spring_perch_mm"],
                   "Rear Spring Defl Max (mm)")
    else:
        print(f"\nRear Spring Defl Max: constant = {np.mean(y):.1f}")

    # ─── 12. Third Spring Defl Static (force-balance) ───
    y_load = col("third_defl_mm") * col("rear_third_nmm")
    X = np.column_stack([col("rear_third_perch_mm")])
    fit_linear(X, y_load,
               ["rear_third_perch_mm"],
               "Third Spring Load (defl * rate)")

    # ─── 13. Third Spring Defl Max ───
    y = col("third_defl_max_mm")
    if np.std(y) > 0.1:
        X = np.column_stack([col("rear_third_nmm"), col("rear_third_perch_mm")])
        fit_linear(X, y,
                   ["rear_third_nmm", "rear_third_perch_mm"],
                   "Third Spring Defl Max (mm)")
    else:
        print(f"\nThird Spring Defl Max: constant = {np.mean(y):.1f}")

    # ─── 14. Third Slider Defl Static ───
    X = np.column_stack([col("third_defl_mm")])
    fit_linear(X, col("third_slider_mm"),
               ["third_defl_mm"],
               "Third Slider Defl Static (mm)")

    # ─── 15. Corner Weights ───
    total_weight = col("lf_corner_weight_n") * 2 + col("lr_corner_weight_n") * 2
    total_mass = total_weight / 9.81
    fuel_mass = col("fuel_l") * 0.742
    car_driver_mass = total_mass - fuel_mass
    front_dist = (col("lf_corner_weight_n") * 2) / total_weight

    print(f"\n{'=' * 60}")
    print(f"  Corner Weight Analysis")
    print(f"  Total mass range: {np.min(total_mass):.1f} - {np.max(total_mass):.1f} kg")
    print(f"  Car+driver mass range: {np.min(car_driver_mass):.1f} - {np.max(car_driver_mass):.1f} kg")
    print(f"  Car+driver mean: {np.mean(car_driver_mass):.1f} ± {np.std(car_driver_mass):.1f} kg")
    print(f"  Front weight dist: {np.mean(front_dist):.4f} ± {np.std(front_dist):.4f}")
    print(f"  Fuel density check: {np.mean(car_driver_mass):.1f} - 75 (driver) = {np.mean(car_driver_mass) - 75:.1f} kg (car dry mass)")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
