"""Ride Height Calibration — extract setup params + measured RH from all IBTs.

Processes every IBT file, extracts:
  - Garage setup parameters (from session info YAML)
  - Measured static RH (from telemetry, speed < 5 kph)
  - Measured dynamic RH (from telemetry, speed > 150 kph)
  - Computed aero compression (static - dynamic sensor readings)

Then computes correlations and fits regression models for:
  - Rear static RH = f(rear_third_perch, rear_pushrod, fuel, rear_spring_perch, ...)
  - Aero compression = f(speed², fuel, setup_params, ...)

Usage:
    python -m scripts.rh_calibration
    python -m scripts.rh_calibration --csv output.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from track_model.ibt_parser import IBTFile
from analyzer.setup_reader import CurrentSetup
from car_model.cars import get_car


@dataclass
class SessionRHData:
    """All ride-height-relevant data from one IBT session."""
    filename: str
    # Garage setup params (from session info YAML)
    static_front_rh_mm: float = 0.0       # avg LF/RF RideHeight
    static_rear_rh_mm: float = 0.0        # avg LR/RR RideHeight
    front_rh_at_speed_mm: float = 0.0     # iRacing AeroCalculator
    rear_rh_at_speed_mm: float = 0.0      # iRacing AeroCalculator
    df_balance_pct: float = 0.0
    front_pushrod_mm: float = 0.0
    rear_pushrod_mm: float = 0.0
    front_heave_nmm: float = 0.0
    front_heave_perch_mm: float = 0.0
    rear_third_nmm: float = 0.0
    rear_third_perch_mm: float = 0.0
    front_torsion_od_mm: float = 0.0
    rear_spring_nmm: float = 0.0
    rear_spring_perch_mm: float = 0.0
    front_camber_deg: float = 0.0
    rear_camber_deg: float = 0.0
    fuel_l: float = 0.0
    wing_angle_deg: float = 0.0
    # Measured from telemetry sensors
    sensor_static_front_mm: float = 0.0   # mean front RH at speed < 5 kph
    sensor_static_rear_mm: float = 0.0    # mean rear RH at speed < 5 kph
    sensor_dynamic_front_mm: float = 0.0  # mean front RH at speed > 150 kph
    sensor_dynamic_rear_mm: float = 0.0   # mean rear RH at speed > 150 kph
    sensor_front_std_mm: float = 0.0      # front RH std at speed
    sensor_rear_std_mm: float = 0.0       # rear RH std at speed
    aero_comp_front_mm: float = 0.0       # static_sensor - dynamic_sensor
    aero_comp_rear_mm: float = 0.0
    mean_speed_at_speed_kph: float = 0.0  # mean speed when > 150 kph
    best_lap_time_s: float = 0.0
    n_pit_samples: int = 0
    n_speed_samples: int = 0


def find_all_ibts() -> list[Path]:
    """Find all IBT files in project root and data/telemetry/."""
    ibts = []
    # Project root
    ibts.extend(sorted(PROJECT_ROOT.glob("*.ibt")))
    # data/telemetry/
    tel_dir = PROJECT_ROOT / "data" / "telemetry"
    if tel_dir.exists():
        ibts.extend(sorted(tel_dir.glob("*.ibt")))
    return ibts


def extract_session_data(ibt_path: Path) -> SessionRHData:
    """Extract all RH-relevant data from one IBT file."""
    ibt = IBTFile(ibt_path)
    data = SessionRHData(filename=ibt_path.name)

    # --- Setup params from session info ---
    try:
        setup = CurrentSetup.from_ibt(ibt)
        data.static_front_rh_mm = setup.static_front_rh_mm
        data.static_rear_rh_mm = setup.static_rear_rh_mm
        data.front_rh_at_speed_mm = setup.front_rh_at_speed_mm
        data.rear_rh_at_speed_mm = setup.rear_rh_at_speed_mm
        data.df_balance_pct = setup.df_balance_pct
        data.front_pushrod_mm = setup.front_pushrod_mm
        data.rear_pushrod_mm = setup.rear_pushrod_mm
        data.front_heave_nmm = setup.front_heave_nmm
        data.front_heave_perch_mm = setup.front_heave_perch_mm
        data.rear_third_nmm = setup.rear_third_nmm
        data.rear_third_perch_mm = setup.rear_third_perch_mm
        data.front_torsion_od_mm = setup.front_torsion_od_mm
        data.rear_spring_nmm = setup.rear_spring_nmm
        data.rear_spring_perch_mm = setup.rear_spring_perch_mm
        data.front_camber_deg = setup.front_camber_deg
        data.rear_camber_deg = setup.rear_camber_deg
        data.fuel_l = setup.fuel_l
        data.wing_angle_deg = setup.wing_angle_deg
    except Exception as e:
        print(f"  WARNING: Could not parse setup from {ibt_path.name}: {e}")

    # --- Telemetry sensor data ---
    try:
        speed_ms = ibt.channel("Speed")
        if speed_ms is None:
            print(f"  WARNING: No Speed channel in {ibt_path.name}")
            return data
        speed_kph = speed_ms * 3.6

        has_rh = all(ibt.has_channel(c) for c in
                     ["LFrideHeight", "RFrideHeight", "LRrideHeight", "RRrideHeight"])
        if not has_rh:
            print(f"  WARNING: Missing ride height channels in {ibt_path.name}")
            return data

        lf_rh = ibt.channel("LFrideHeight") * 1000  # m -> mm
        rf_rh = ibt.channel("RFrideHeight") * 1000
        lr_rh = ibt.channel("LRrideHeight") * 1000
        rr_rh = ibt.channel("RRrideHeight") * 1000

        front_rh = (lf_rh + rf_rh) / 2.0
        rear_rh = (lr_rh + rr_rh) / 2.0

        brake = ibt.channel("Brake") if ibt.has_channel("Brake") else np.zeros(len(speed_kph))

        # Static: pit samples (speed < 5 kph)
        pit_mask = speed_kph < 5.0
        data.n_pit_samples = int(np.sum(pit_mask))
        if data.n_pit_samples > 20:
            data.sensor_static_front_mm = float(np.mean(front_rh[pit_mask]))
            data.sensor_static_rear_mm = float(np.mean(rear_rh[pit_mask]))
        else:
            # Fallback: p95 of all samples
            data.sensor_static_front_mm = float(np.percentile(front_rh, 95))
            data.sensor_static_rear_mm = float(np.percentile(rear_rh, 95))

        # Dynamic: at speed (> 150 kph, no braking)
        at_speed = (speed_kph > 150) & (brake < 0.05)
        data.n_speed_samples = int(np.sum(at_speed))
        if data.n_speed_samples > 50:
            data.sensor_dynamic_front_mm = float(np.mean(front_rh[at_speed]))
            data.sensor_dynamic_rear_mm = float(np.mean(rear_rh[at_speed]))
            data.sensor_front_std_mm = float(np.std(front_rh[at_speed]))
            data.sensor_rear_std_mm = float(np.std(rear_rh[at_speed]))
            data.mean_speed_at_speed_kph = float(np.mean(speed_kph[at_speed]))

        # Aero compression
        if data.sensor_static_front_mm > 0 and data.sensor_dynamic_front_mm > 0:
            data.aero_comp_front_mm = data.sensor_static_front_mm - data.sensor_dynamic_front_mm
        if data.sensor_static_rear_mm > 0 and data.sensor_dynamic_rear_mm > 0:
            data.aero_comp_rear_mm = data.sensor_static_rear_mm - data.sensor_dynamic_rear_mm

        # Best lap time
        best = ibt.best_lap_indices(min_time=60.0)
        if best is not None:
            lt_ch = ibt.channel("LapCurrentLapTime")
            if lt_ch is not None:
                data.best_lap_time_s = float(lt_ch[best[1]])

    except Exception as e:
        print(f"  WARNING: Telemetry extraction error for {ibt_path.name}: {e}")

    return data


def print_data_table(sessions: list[SessionRHData]) -> None:
    """Print a formatted table of all sessions."""
    print("\n" + "=" * 120)
    print("  SESSION DATA SUMMARY")
    print("=" * 120)

    # Header
    print(f"{'File':<45} {'FrntRH':>6} {'RearRH':>6} {'RrPush':>6} "
          f"{'3rdPrch':>7} {'3rdSpg':>6} {'RrSpg':>5} {'RrSPrch':>7} "
          f"{'Fuel':>5} {'FCam':>5} {'RCam':>5} {'TbarOD':>6} {'FHvPrch':>7}")
    print(f"{'(garage)':<45} {'mm':>6} {'mm':>6} {'mm':>6} "
          f"{'mm':>7} {'N/mm':>6} {'N/mm':>5} {'mm':>7} "
          f"{'L':>5} {'deg':>5} {'deg':>5} {'mm':>6} {'mm':>7}")
    print("-" * 120)

    for s in sessions:
        fname = s.filename[:44]
        print(f"{fname:<45} {s.static_front_rh_mm:6.1f} {s.static_rear_rh_mm:6.1f} "
              f"{s.rear_pushrod_mm:6.1f} {s.rear_third_perch_mm:7.1f} "
              f"{s.rear_third_nmm:6.0f} {s.rear_spring_nmm:5.0f} "
              f"{s.rear_spring_perch_mm:7.1f} {s.fuel_l:5.0f} "
              f"{s.front_camber_deg:5.1f} {s.rear_camber_deg:5.1f} "
              f"{s.front_torsion_od_mm:6.2f} {s.front_heave_perch_mm:7.1f}")

    # Sensor measurements
    print()
    print(f"{'File':<45} {'SensFrnt':>8} {'SensRear':>8} {'DynFrnt':>8} "
          f"{'DynRear':>8} {'CompF':>6} {'CompR':>6} {'MnSpd':>6} "
          f"{'LapT':>7} {'#Pit':>5} {'#Spd':>5}")
    print(f"{'(sensor mm)':<45} {'static':>8} {'static':>8} {'@speed':>8} "
          f"{'@speed':>8} {'mm':>6} {'mm':>6} {'kph':>6} "
          f"{'s':>7} {'samp':>5} {'samp':>5}")
    print("-" * 120)

    for s in sessions:
        fname = s.filename[:44]
        print(f"{fname:<45} {s.sensor_static_front_mm:8.2f} {s.sensor_static_rear_mm:8.2f} "
              f"{s.sensor_dynamic_front_mm:8.2f} {s.sensor_dynamic_rear_mm:8.2f} "
              f"{s.aero_comp_front_mm:6.2f} {s.aero_comp_rear_mm:6.2f} "
              f"{s.mean_speed_at_speed_kph:6.1f} {s.best_lap_time_s:7.3f} "
              f"{s.n_pit_samples:5d} {s.n_speed_samples:5d}")


def compute_correlations(sessions: list[SessionRHData]) -> None:
    """Compute and print correlation matrix for rear static RH."""
    print("\n" + "=" * 80)
    print("  CORRELATION ANALYSIS — Rear Static RH vs Setup Parameters")
    print("=" * 80)

    rear_rh = np.array([s.static_rear_rh_mm for s in sessions])

    if np.std(rear_rh) < 0.01:
        print("  Rear static RH has no variance across sessions — no correlations possible.")
        return

    # Parameters to check
    params = {
        "rear_third_perch_mm": [s.rear_third_perch_mm for s in sessions],
        "rear_pushrod_mm": [s.rear_pushrod_mm for s in sessions],
        "rear_third_nmm": [s.rear_third_nmm for s in sessions],
        "rear_spring_nmm": [s.rear_spring_nmm for s in sessions],
        "rear_spring_perch_mm": [s.rear_spring_perch_mm for s in sessions],
        "fuel_l": [s.fuel_l for s in sessions],
        "front_camber_deg": [s.front_camber_deg for s in sessions],
        "rear_camber_deg": [s.rear_camber_deg for s in sessions],
        "front_torsion_od_mm": [s.front_torsion_od_mm for s in sessions],
        "front_heave_nmm": [s.front_heave_nmm for s in sessions],
        "front_heave_perch_mm": [s.front_heave_perch_mm for s in sessions],
        "wing_angle_deg": [s.wing_angle_deg for s in sessions],
        "front_pushrod_mm": [s.front_pushrod_mm for s in sessions],
    }

    print(f"\n  Rear static RH range: {min(rear_rh):.1f} — {max(rear_rh):.1f} mm "
          f"(std: {np.std(rear_rh):.2f} mm)\n")

    correlations = []
    for name, values in params.items():
        arr = np.array(values, dtype=float)
        if np.std(arr) < 1e-6:
            correlations.append((name, 0.0, np.std(arr), "no variance"))
            continue
        r = float(np.corrcoef(rear_rh, arr)[0, 1])
        correlations.append((name, r, float(np.std(arr)), ""))

    # Sort by |r|
    correlations.sort(key=lambda x: abs(x[1]), reverse=True)

    print(f"  {'Parameter':<25} {'r':>8} {'|r|':>8} {'param_std':>10} {'note':>12}")
    print(f"  {'-' * 25} {'-' * 8} {'-' * 8} {'-' * 10} {'-' * 12}")
    for name, r, std, note in correlations:
        strength = ""
        if abs(r) > 0.7:
            strength = "STRONG"
        elif abs(r) > 0.3:
            strength = "moderate"
        elif note:
            strength = note
        else:
            strength = "weak"
        print(f"  {name:<25} {r:8.4f} {abs(r):8.4f} {std:10.3f} {strength:>12}")

    # Also check front static RH variance
    front_rh = np.array([s.static_front_rh_mm for s in sessions])
    print(f"\n  Front static RH range: {min(front_rh):.1f} — {max(front_rh):.1f} mm "
          f"(std: {np.std(front_rh):.2f} mm)")
    if np.std(front_rh) < 0.05:
        print("  => Front RH is effectively constant. No regression needed.")
    else:
        print(f"  => Front RH varies — driven by setup parameters (not sim-pinned).")

    # Sensor static RH correlations
    print("\n" + "=" * 80)
    print("  CORRELATION ANALYSIS — Sensor Static RH vs Setup Parameters")
    print("=" * 80)

    sensor_rear_rh = np.array([s.sensor_static_rear_mm for s in sessions])
    if np.std(sensor_rear_rh) > 0.01:
        print(f"\n  Sensor rear static RH range: {min(sensor_rear_rh):.2f} — "
              f"{max(sensor_rear_rh):.2f} mm (std: {np.std(sensor_rear_rh):.2f} mm)\n")

        correlations_sensor = []
        for name, values in params.items():
            arr = np.array(values, dtype=float)
            if np.std(arr) < 1e-6:
                continue
            r = float(np.corrcoef(sensor_rear_rh, arr)[0, 1])
            correlations_sensor.append((name, r))

        correlations_sensor.sort(key=lambda x: abs(x[1]), reverse=True)
        print(f"  {'Parameter':<25} {'r':>8} {'|r|':>8}")
        print(f"  {'-' * 25} {'-' * 8} {'-' * 8}")
        for name, r in correlations_sensor:
            print(f"  {name:<25} {r:8.4f} {abs(r):8.4f}")

    # Aero compression correlations
    print("\n" + "=" * 80)
    print("  AERO COMPRESSION ANALYSIS")
    print("=" * 80)

    comp_front = np.array([s.aero_comp_front_mm for s in sessions])
    comp_rear = np.array([s.aero_comp_rear_mm for s in sessions])
    speeds = np.array([s.mean_speed_at_speed_kph for s in sessions])

    valid = comp_front > 0
    if np.sum(valid) > 2:
        print(f"\n  Front aero compression: {np.mean(comp_front[valid]):.2f} ± "
              f"{np.std(comp_front[valid]):.2f} mm (n={np.sum(valid)})")
        print(f"  Rear aero compression:  {np.mean(comp_rear[valid]):.2f} ± "
              f"{np.std(comp_rear[valid]):.2f} mm")
        print(f"  Mean speed (>150 kph mask): {np.mean(speeds[valid]):.1f} ± "
              f"{np.std(speeds[valid]):.1f} kph")

        # Check if compression varies with fuel or speed
        fuel = np.array([s.fuel_l for s in sessions])[valid]
        if np.std(fuel) > 0.5:
            r_fuel_front = float(np.corrcoef(comp_front[valid], fuel)[0, 1])
            r_fuel_rear = float(np.corrcoef(comp_rear[valid], fuel)[0, 1])
            print(f"\n  Compression vs fuel: front r={r_fuel_front:.3f}, "
                  f"rear r={r_fuel_rear:.3f}")

        if np.std(speeds[valid]) > 1.0:
            r_speed_front = float(np.corrcoef(comp_front[valid], speeds[valid])[0, 1])
            r_speed_rear = float(np.corrcoef(comp_rear[valid], speeds[valid])[0, 1])
            print(f"  Compression vs speed: front r={r_speed_front:.3f}, "
                  f"rear r={r_speed_rear:.3f}")


def fit_regression(sessions: list[SessionRHData]) -> None:
    """Fit multi-variable regression for rear static RH."""
    print("\n" + "=" * 80)
    print("  REGRESSION ANALYSIS — Rear Static RH")
    print("=" * 80)

    rear_rh = np.array([s.static_rear_rh_mm for s in sessions])

    if np.std(rear_rh) < 0.01:
        print("  No variance in rear static RH — regression not possible.")
        return

    # Build feature matrix with all potentially relevant params
    feature_names = [
        "rear_third_perch_mm",
        "rear_pushrod_mm",
        "rear_third_nmm",
        "rear_spring_nmm",
        "rear_spring_perch_mm",
        "fuel_l",
        "rear_camber_deg",
        "front_torsion_od_mm",
        "front_heave_nmm",
        "front_heave_perch_mm",
    ]

    X_all = np.column_stack([
        [getattr(s, name) for s in sessions]
        for name in feature_names
    ])

    # Remove features with no variance
    var_mask = np.std(X_all, axis=0) > 1e-6
    active_names = [n for n, m in zip(feature_names, var_mask) if m]
    X = X_all[:, var_mask]

    if X.shape[1] == 0:
        print("  No features with variance — regression not possible.")
        return

    n_samples, n_features = X.shape
    print(f"\n  Samples: {n_samples}, Features with variance: {n_features}")
    print(f"  Active features: {active_names}\n")

    # --- OLS regression ---
    # Add intercept
    X_with_intercept = np.column_stack([np.ones(n_samples), X])

    try:
        # Solve normal equations: β = (X'X)^-1 X'y
        beta, residuals, rank, sv = np.linalg.lstsq(X_with_intercept, rear_rh, rcond=None)
    except np.linalg.LinAlgError:
        print("  Linear algebra error — regression failed.")
        return

    predictions = X_with_intercept @ beta
    residuals_vec = rear_rh - predictions
    ss_res = np.sum(residuals_vec ** 2)
    ss_tot = np.sum((rear_rh - np.mean(rear_rh)) ** 2)
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # Adjusted R²
    if n_samples > n_features + 1:
        r_squared_adj = 1.0 - (1.0 - r_squared) * (n_samples - 1) / (n_samples - n_features - 1)
    else:
        r_squared_adj = r_squared

    print(f"  FULL MODEL (all features with variance):")
    print(f"  R² = {r_squared:.4f}, Adjusted R² = {r_squared_adj:.4f}")
    print(f"  RMSE = {np.sqrt(ss_res / n_samples):.3f} mm")
    print(f"  Max residual = {np.max(np.abs(residuals_vec)):.3f} mm\n")

    print(f"  {'Feature':<25} {'Coefficient':>12} {'Unit effect':>12}")
    print(f"  {'-' * 25} {'-' * 12} {'-' * 12}")
    print(f"  {'intercept':<25} {beta[0]:12.4f}")
    for i, name in enumerate(active_names):
        param_range = np.max(X[:, i]) - np.min(X[:, i])
        effect = beta[i + 1] * param_range if param_range > 0 else 0
        print(f"  {name:<25} {beta[i + 1]:12.6f} {effect:12.3f} mm")

    # --- Predictions table ---
    print(f"\n  {'Session':<45} {'Actual':>8} {'Predicted':>10} {'Residual':>10}")
    print(f"  {'-' * 45} {'-' * 8} {'-' * 10} {'-' * 10}")
    for j, s in enumerate(sessions):
        fname = s.filename[:44]
        print(f"  {fname:<45} {rear_rh[j]:8.1f} {predictions[j]:10.2f} "
              f"{residuals_vec[j]:10.3f}")

    # --- Stepwise: try each feature alone ---
    print(f"\n  SINGLE-FEATURE REGRESSIONS:")
    print(f"  {'Feature':<25} {'R²':>8} {'Slope':>12} {'Intercept':>12}")
    print(f"  {'-' * 25} {'-' * 8} {'-' * 12} {'-' * 12}")

    for i, name in enumerate(active_names):
        x = X[:, i]
        X_single = np.column_stack([np.ones(n_samples), x])
        beta_s, _, _, _ = np.linalg.lstsq(X_single, rear_rh, rcond=None)
        pred_s = X_single @ beta_s
        ss_res_s = np.sum((rear_rh - pred_s) ** 2)
        r2_s = 1.0 - ss_res_s / ss_tot if ss_tot > 0 else 0.0
        print(f"  {name:<25} {r2_s:8.4f} {beta_s[1]:12.6f} {beta_s[0]:12.4f}")

    # --- Parsimonious models (2-4 features) ---
    from itertools import combinations

    print(f"\n  BEST PARSIMONIOUS MODELS (2-4 features, leave-one-out CV):")
    print(f"  {'Features':<60} {'R2':>6} {'R2adj':>6} {'RMSE':>6} {'MaxRes':>6} {'LOO':>6}")
    print(f"  {'-' * 60} {'-' * 6} {'-' * 6} {'-' * 6} {'-' * 6} {'-' * 6}")

    best_models = []
    for n_feat in range(2, min(5, n_features + 1)):
        for combo in combinations(range(n_features), n_feat):
            names_c = [active_names[i] for i in combo]
            X_c = X[:, list(combo)]
            X_ci = np.column_stack([np.ones(n_samples), X_c])
            beta_c, _, _, _ = np.linalg.lstsq(X_ci, rear_rh, rcond=None)
            pred_c = X_ci @ beta_c
            res_c = rear_rh - pred_c
            ss_res_c = np.sum(res_c ** 2)
            r2_c = 1.0 - ss_res_c / ss_tot if ss_tot > 0 else 0.0
            r2_adj_c = 1.0 - (1.0 - r2_c) * (n_samples - 1) / (n_samples - n_feat - 1) \
                if n_samples > n_feat + 1 else r2_c
            rmse_c = np.sqrt(ss_res_c / n_samples)
            max_res_c = np.max(np.abs(res_c))

            # Leave-one-out cross-validation
            loo_errors = []
            for j in range(n_samples):
                X_train = np.delete(X_ci, j, axis=0)
                y_train = np.delete(rear_rh, j)
                try:
                    b, _, _, _ = np.linalg.lstsq(X_train, y_train, rcond=None)
                    pred_j = X_ci[j] @ b
                    loo_errors.append((rear_rh[j] - pred_j) ** 2)
                except Exception:
                    loo_errors.append(999.0)
            loo_rmse = np.sqrt(np.mean(loo_errors))

            best_models.append((r2_adj_c, names_c, beta_c, r2_c, rmse_c, max_res_c, loo_rmse))

    # Sort by adjusted R² descending
    best_models.sort(key=lambda x: x[0], reverse=True)
    for _, names_c, beta_c, r2_c, rmse_c, max_res_c, loo_rmse in best_models[:15]:
        names_str = " + ".join(names_c)[:59]
        print(f"  {names_str:<60} {r2_c:6.4f} {_:6.4f} {rmse_c:6.3f} {max_res_c:6.3f} {loo_rmse:6.3f}")

    # Print the best model in detail
    print(f"\n  BEST PARSIMONIOUS MODEL (by adjusted R²):")
    best = best_models[0]
    _, best_names, best_beta, best_r2, best_rmse, best_max_res, best_loo = best
    print(f"  Features: {best_names}")
    print(f"  R² = {best_r2:.4f}, RMSE = {best_rmse:.3f} mm, Max residual = {best_max_res:.3f} mm")
    print(f"  LOO-CV RMSE = {best_loo:.3f} mm\n")
    print(f"  Equation:")
    terms = [f"{best_beta[0]:.4f}"]
    for i, name in enumerate(best_names):
        sign = "+" if best_beta[i + 1] >= 0 else ""
        terms.append(f"{sign}{best_beta[i + 1]:.6f} * {name}")
    print(f"    rear_static_rh = {' '.join(terms)}\n")

    # Predictions for best model
    best_idx = [active_names.index(n) for n in best_names]
    X_best = np.column_stack([np.ones(n_samples), X[:, best_idx]])
    pred_best = X_best @ best_beta
    print(f"  {'Session':<45} {'Actual':>8} {'Predicted':>10} {'Residual':>10}")
    print(f"  {'-' * 45} {'-' * 8} {'-' * 10} {'-' * 10}")
    for j, s in enumerate(sessions):
        fname = s.filename[:44]
        res = rear_rh[j] - pred_best[j]
        print(f"  {fname:<45} {rear_rh[j]:8.1f} {pred_best[j]:10.2f} {res:10.3f}")

    # --- Sensor-based regression (dynamic RH) ---
    print("\n" + "=" * 80)
    print("  REGRESSION — Sensor Dynamic Rear RH (at speed > 150 kph)")
    print("=" * 80)

    sensor_dyn_rear = np.array([s.sensor_dynamic_rear_mm for s in sessions])
    valid = sensor_dyn_rear > 0
    if np.sum(valid) < 3:
        print("  Too few sessions with valid dynamic RH data.")
        return

    X_valid = X[valid]
    y_valid = sensor_dyn_rear[valid]
    sessions_valid = [s for s, v in zip(sessions, valid) if v]

    X_v = np.column_stack([np.ones(np.sum(valid)), X_valid])
    try:
        beta_d, _, _, _ = np.linalg.lstsq(X_v, y_valid, rcond=None)
    except np.linalg.LinAlgError:
        print("  Regression failed.")
        return

    pred_d = X_v @ beta_d
    res_d = y_valid - pred_d
    ss_res_d = np.sum(res_d ** 2)
    ss_tot_d = np.sum((y_valid - np.mean(y_valid)) ** 2)
    r2_d = 1.0 - ss_res_d / ss_tot_d if ss_tot_d > 0 else 0.0

    print(f"\n  R² = {r2_d:.4f}, RMSE = {np.sqrt(ss_res_d / np.sum(valid)):.3f} mm\n")
    print(f"  {'Feature':<25} {'Coefficient':>12}")
    print(f"  {'-' * 25} {'-' * 12}")
    print(f"  {'intercept':<25} {beta_d[0]:12.4f}")
    for i, name in enumerate(active_names):
        print(f"  {name:<25} {beta_d[i + 1]:12.6f}")


def save_csv(sessions: list[SessionRHData], path: str) -> None:
    """Save all session data to CSV."""
    field_list = [f.name for f in fields(SessionRHData)]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=field_list)
        writer.writeheader()
        for s in sessions:
            writer.writerow({f.name: getattr(s, f.name) for f in fields(SessionRHData)})
    print(f"\nCSV saved to: {path}")


def main():
    parser = argparse.ArgumentParser(
        description="Ride Height Calibration — extract and correlate RH data from IBTs"
    )
    parser.add_argument("--csv", type=str, default=None,
                        help="Save data to CSV file")
    parser.add_argument("--car", type=str, default="bmw",
                        help="Car name (default: bmw)")
    args = parser.parse_args()

    ibts = find_all_ibts()
    print(f"Found {len(ibts)} IBT files")

    sessions = []
    for i, ibt_path in enumerate(ibts):
        print(f"\n[{i + 1}/{len(ibts)}] Processing: {ibt_path.name}")
        try:
            data = extract_session_data(ibt_path)
            sessions.append(data)
            print(f"  Garage: front={data.static_front_rh_mm:.1f}mm, "
                  f"rear={data.static_rear_rh_mm:.1f}mm | "
                  f"Sensor: front={data.sensor_static_front_mm:.2f}mm, "
                  f"rear={data.sensor_static_rear_mm:.2f}mm | "
                  f"Comp: F={data.aero_comp_front_mm:.2f}, R={data.aero_comp_rear_mm:.2f}")
        except Exception as e:
            print(f"  ERROR: {e}")

    if not sessions:
        print("No sessions processed successfully.")
        sys.exit(1)

    # Print summary table
    print_data_table(sessions)

    # Correlations
    compute_correlations(sessions)

    # Regression
    fit_regression(sessions)

    # Save CSV if requested
    if args.csv:
        save_csv(sessions, args.csv)


if __name__ == "__main__":
    main()
