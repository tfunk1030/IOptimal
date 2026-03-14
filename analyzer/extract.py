"""Extract measured telemetry quantities for setup analysis.

Reuses track_model.ibt_parser for channel access and track_model.build_profile
for the full profile rebuild. Extracts:
- Ride heights at speed (aero compression)
- Ride height excursion p99 (platform stability)
- Natural frequency via FFT (spring response)
- Settle time after bump events (damper response)
- Handling dynamics (understeer, body slip, yaw correlation)
- Tyre thermal / pressure / wear data

Unlike the validator version, this does NOT require a solver JSON.
Everything is derived from the IBT telemetry alone.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from track_model.ibt_parser import IBTFile
from track_model.build_profile import build_profile
from track_model.profile import TrackProfile
from car_model.cars import CarModel


@dataclass
class MeasuredState:
    """All telemetry-derived quantities for setup analysis.

    IMPORTANT: IBT ride height sensors and the aero model operate in
    different reference frames. The solver's dynamic_front_rh_mm = 15mm
    is the aero operating point; IBT sensors read ~20mm at the same
    condition due to sensor placement offsets. Therefore:
    - Absolute RH comparison requires offset calibration (unreliable)
    - Excursion (p99 deviation from mean) is offset-independent (reliable)
    - Variance / sigma is offset-independent (reliable)
    - Aero compression (static - dynamic) is offset-independent (reliable)
    """

    # --- Step 1: Ride heights (IBT sensor coordinates) ---
    mean_front_rh_at_speed_mm: float = 0.0
    mean_rear_rh_at_speed_mm: float = 0.0
    front_rh_std_mm: float = 0.0
    rear_rh_std_mm: float = 0.0
    aero_compression_front_mm: float = 0.0
    aero_compression_rear_mm: float = 0.0
    bottoming_event_count_front: int = 0
    bottoming_event_count_rear: int = 0
    vortex_burst_event_count: int = 0
    front_rh_p01_mm: float = 0.0
    rear_rh_p01_mm: float = 0.0
    static_front_rh_sensor_mm: float = 0.0
    static_rear_rh_sensor_mm: float = 0.0

    # --- Step 2: Platform stability ---
    front_shock_vel_p99_mps: float = 0.0
    rear_shock_vel_p99_mps: float = 0.0
    front_rh_excursion_measured_mm: float = 0.0
    rear_rh_excursion_measured_mm: float = 0.0

    # --- Heave/shock deflection (spring travel) ---
    front_heave_defl_mean_mm: float = 0.0       # Mean HFshockDefl at speed
    front_heave_defl_p99_mm: float = 0.0        # p99 peak compression
    front_heave_defl_max_mm: float = 0.0        # Maximum observed compression
    front_heave_defl_std_mm: float = 0.0        # Variance of heave deflection
    rear_heave_defl_mean_mm: float = 0.0        # Mean HRshockDefl at speed
    rear_heave_defl_p99_mm: float = 0.0
    rear_heave_defl_max_mm: float = 0.0
    rear_heave_defl_std_mm: float = 0.0
    front_heave_travel_used_pct: float = 0.0    # p99 defl / DeflMax * 100
    rear_heave_travel_used_pct: float = 0.0
    heave_bottoming_events_front: int = 0       # Direct spring travel exhaustion
    heave_bottoming_events_rear: int = 0
    # Braking-specific heave analysis (detects entry rotation → mid-corner push)
    front_heave_defl_braking_p99_mm: float = 0.0
    front_heave_travel_used_braking_pct: float = 0.0

    # --- Step 3: Spring response ---
    front_dominant_freq_hz: float = 0.0
    rear_dominant_freq_hz: float = 0.0

    # --- Step 4: Balance ---
    lltd_measured: float = 0.0
    roll_gradient_measured_deg_per_g: float = 0.0
    body_roll_at_peak_g_deg: float = 0.0
    peak_lat_g_measured: float = 0.0

    # --- Step 6: Dampers ---
    front_shock_vel_p95_mps: float = 0.0
    rear_shock_vel_p95_mps: float = 0.0
    front_rh_settle_time_ms: float = 0.0
    rear_rh_settle_time_ms: float = 0.0

    # --- Body roll p95 ---
    body_roll_p95_deg: float = 0.0

    # --- Handling dynamics ---
    understeer_mean_deg: float = 0.0
    understeer_low_speed_deg: float = 0.0
    understeer_high_speed_deg: float = 0.0
    body_slip_p95_deg: float = 0.0
    body_slip_at_peak_g_deg: float = 0.0
    rear_slip_ratio_p95: float = 0.0
    front_slip_ratio_p95: float = 0.0
    yaw_rate_correlation: float = 0.0
    roll_rate_p95_deg_per_s: float = 0.0
    pitch_rate_p95_deg_per_s: float = 0.0

    # --- Tyre thermal analysis ---
    front_temp_spread_lf_c: float = 0.0
    front_temp_spread_rf_c: float = 0.0
    rear_temp_spread_lr_c: float = 0.0
    rear_temp_spread_rr_c: float = 0.0
    front_carcass_mean_c: float = 0.0
    rear_carcass_mean_c: float = 0.0
    front_pressure_mean_kpa: float = 0.0
    rear_pressure_mean_kpa: float = 0.0
    front_wear_mean_pct: float = 0.0
    rear_wear_mean_pct: float = 0.0

    # --- Splitter ride height (CFSRrideHeight) ---
    splitter_rh_mean_at_speed_mm: float = 0.0   # Mean center-front splitter RH at >150kph
    splitter_rh_min_mm: float = 0.0              # Minimum observed (splitter scrape proximity)
    splitter_rh_p01_mm: float = 0.0              # 1st percentile (near-worst case)
    splitter_rh_std_mm: float = 0.0              # Variance at speed
    splitter_scrape_events: int = 0              # Samples where splitter RH < 2mm

    # --- Corner shock deflections (LF/RF/LR/RRshockDefl) ---
    front_corner_defl_p99_mm: float = 0.0        # p99 corner shock deflection (avg LF+RF)
    rear_corner_defl_p99_mm: float = 0.0         # p99 corner shock deflection (avg LR+RR)
    front_corner_defl_max_mm: float = 0.0
    rear_corner_defl_max_mm: float = 0.0

    # --- Heave shock velocities (HFshockVel, HRshockVel) ---
    front_heave_vel_p95_mps: float = 0.0         # Front heave damper velocity p95
    front_heave_vel_p99_mps: float = 0.0
    rear_heave_vel_p95_mps: float = 0.0
    rear_heave_vel_p99_mps: float = 0.0
    front_heave_vel_ls_pct: float = 0.0          # % of samples in LS regime (<25 mm/s)
    front_heave_vel_hs_pct: float = 0.0          # % of samples in HS regime (>100 mm/s)

    # --- Brake system ---
    measured_brake_bias_pct: float = 0.0         # From brake line pressures: front/(front+rear)
    abs_active_pct: float = 0.0                  # % of braking time ABS is active
    abs_cut_mean_pct: float = 0.0                # Mean ABS force reduction during engagement
    front_brake_pressure_peak_bar: float = 0.0
    rear_brake_pressure_peak_bar: float = 0.0

    # --- In-car adjustment tracking ---
    brake_bias_adjustments: int = 0              # Number of bias changes during session
    tc_adjustments: int = 0                      # Number of TC changes during session
    brake_bias_range: tuple[float, float] = (0.0, 0.0)  # Min/max bias values used

    # --- Fuel and weight ---
    fuel_level_at_measurement_l: float = 0.0     # Fuel level during analyzed lap
    fuel_used_per_lap_l: float = 0.0

    # --- Hybrid/ERS ---
    ers_battery_mean_pct: float = 0.0            # Mean battery charge during lap
    ers_battery_min_pct: float = 0.0             # Minimum (depleted = less rear torque)
    mguk_torque_peak_nm: float = 0.0             # Peak hybrid torque contribution

    # --- Environmental ---
    air_temp_c: float = 0.0
    track_temp_c: float = 0.0
    air_density_kg_m3: float = 0.0

    # --- RPM ---
    rpm_at_braking_pct_at_limiter: float = 0.0   # % of braking events hitting rev limiter

    # --- Speed-dependent LLTD ---
    lltd_low_speed: float = 0.0                  # LLTD at <120 kph (mechanical-dominated)
    lltd_high_speed: float = 0.0                 # LLTD at >180 kph (aero-influenced)

    # --- Directional understeer (left/right split) ---
    understeer_left_turn_deg: float = 0.0
    understeer_right_turn_deg: float = 0.0

    # --- Per-corner shock velocities (loaded vs unloaded) ---
    lf_shock_vel_p95_mps: float = 0.0
    rf_shock_vel_p95_mps: float = 0.0
    lr_shock_vel_p95_mps: float = 0.0
    rr_shock_vel_p95_mps: float = 0.0

    # --- Carcass temperature gradient (inner-outer, for deep camber validation) ---
    front_carcass_gradient_lf_c: float = 0.0     # LF carcass inner-outer spread
    front_carcass_gradient_rf_c: float = 0.0
    rear_carcass_gradient_lr_c: float = 0.0
    rear_carcass_gradient_rr_c: float = 0.0

    # --- Per-corner tyre data (preserves left-right split) ---
    lf_pressure_kpa: float = 0.0
    rf_pressure_kpa: float = 0.0
    lr_pressure_kpa: float = 0.0
    rr_pressure_kpa: float = 0.0
    lf_cold_pressure_kpa: float = 0.0
    rf_cold_pressure_kpa: float = 0.0
    lr_cold_pressure_kpa: float = 0.0
    rr_cold_pressure_kpa: float = 0.0
    lf_wear_pct: float = 0.0
    rf_wear_pct: float = 0.0
    lr_wear_pct: float = 0.0
    rr_wear_pct: float = 0.0
    lf_temp_inner_c: float = 0.0   # Inner surface temp at speed
    rf_temp_inner_c: float = 0.0
    lr_temp_inner_c: float = 0.0
    rr_temp_inner_c: float = 0.0
    lf_temp_middle_c: float = 0.0  # Middle surface temp at speed
    rf_temp_middle_c: float = 0.0
    lr_temp_middle_c: float = 0.0
    rr_temp_middle_c: float = 0.0
    lf_temp_outer_c: float = 0.0   # Outer surface temp at speed
    rf_temp_outer_c: float = 0.0
    lr_temp_outer_c: float = 0.0
    rr_temp_outer_c: float = 0.0

    # --- Raw driver inputs (before TC/ABS intervention) ---
    throttle_raw_mean: float = 0.0          # Mean ThrottleRaw at speed
    tc_intervention_pct: float = 0.0        # % of time TC is cutting throttle
    brake_raw_peak: float = 0.0             # Peak BrakeRaw value

    # --- Gear data ---
    gear_at_apex_mode: int = 0              # Most common gear at corner apexes
    max_gear: int = 0                       # Highest gear used on track

    # --- Pitch dynamics ---
    pitch_mean_at_speed_deg: float = 0.0    # Mean pitch angle at speed (rake indicator)
    pitch_range_deg: float = 0.0            # p99-p01 pitch range (platform stability)

    # --- In-car adjustment tracking (extended) ---
    arb_front_adjustments: int = 0          # dcAntiRollFront changes
    arb_rear_adjustments: int = 0           # dcAntiRollRear changes
    tc2_adjustments: int = 0                # dcTractionControl2 changes
    abs_adjustments: int = 0                # dcABS changes
    deploy_mode_adjustments: int = 0        # dcMGUKDeployMode changes

    # --- Wind ---
    wind_speed_ms: float = 0.0
    wind_dir_deg: float = 0.0

    # --- Full rebuilt track profile ---
    measured_track_profile: TrackProfile | None = None

    # --- Session metadata ---
    lap_time_s: float = 0.0
    lap_number: int = 0
    speed_mean_kph: float = 0.0
    speed_max_kph: float = 0.0
    mean_speed_at_speed_kph: float = 0.0


def extract_measurements(
    ibt_path: str | Path,
    car: CarModel,
    lap: int | None = None,
    min_lap_time: float = 108.0,
    outlier_pct: float = 0.115,
) -> MeasuredState:
    """Extract all analysis-relevant measurements from an IBT session.

    Args:
        ibt_path: Path to .ibt or .zip file
        car: Car model for thresholds (vortex burst, etc.)
        lap: Specific lap number to analyze (None = best lap)

    Returns:
        MeasuredState with all measured quantities
    """
    ibt = IBTFile(ibt_path)
    state = MeasuredState()

    # --- Find lap boundaries ---
    if lap is not None:
        start, end = _find_lap(ibt, lap)
        lap_time_ch = ibt.channel("LapCurrentLapTime")
        state.lap_time_s = float(lap_time_ch[end]) if lap_time_ch is not None else 0.0
        state.lap_number = lap
    else:
        best = ibt.best_lap_indices(min_time=min_lap_time, outlier_pct=outlier_pct)
        if best is None:
            raise ValueError("No valid laps found in IBT file")
        start, end = best
        lap_time_ch = ibt.channel("LapCurrentLapTime")
        state.lap_time_s = float(lap_time_ch[end]) if lap_time_ch is not None else 0.0
        lap_ch = ibt.channel("Lap")
        state.lap_number = int(lap_ch[start]) if lap_ch is not None else 0

    n = end - start + 1

    # --- Load channels for this lap ---
    speed_ms = ibt.channel("Speed")[start:end + 1]
    speed_kph = speed_ms * 3.6
    lat_accel = ibt.channel("LatAccel")[start:end + 1]
    lat_g = lat_accel / 9.81

    state.speed_mean_kph = float(np.mean(speed_kph))
    state.speed_max_kph = float(np.max(speed_kph))
    state.peak_lat_g_measured = float(np.max(np.abs(lat_g)))

    # Shock velocities
    lf_sv = np.abs(ibt.channel("LFshockVel")[start:end + 1])
    rf_sv = np.abs(ibt.channel("RFshockVel")[start:end + 1])
    lr_sv = np.abs(ibt.channel("LRshockVel")[start:end + 1])
    rr_sv = np.abs(ibt.channel("RRshockVel")[start:end + 1])

    front_sv = np.concatenate([lf_sv, rf_sv])
    rear_sv = np.concatenate([lr_sv, rr_sv])

    state.front_shock_vel_p95_mps = float(np.percentile(front_sv, 95))
    state.front_shock_vel_p99_mps = float(np.percentile(front_sv, 99))
    state.rear_shock_vel_p95_mps = float(np.percentile(rear_sv, 95))
    state.rear_shock_vel_p99_mps = float(np.percentile(rear_sv, 99))

    # --- Ride heights ---
    has_rh = all(ibt.has_channel(c) for c in
                 ["LFrideHeight", "RFrideHeight", "LRrideHeight", "RRrideHeight"])

    if has_rh:
        lf_rh = ibt.channel("LFrideHeight")[start:end + 1] * 1000  # m -> mm
        rf_rh = ibt.channel("RFrideHeight")[start:end + 1] * 1000
        lr_rh = ibt.channel("LRrideHeight")[start:end + 1] * 1000
        rr_rh = ibt.channel("RRrideHeight")[start:end + 1] * 1000

        front_rh = (lf_rh + rf_rh) / 2.0
        rear_rh = (lr_rh + rr_rh) / 2.0

        # At-speed mask: >150 kph, no braking, reasonably straight
        brake = ibt.channel("Brake")[start:end + 1] if ibt.has_channel("Brake") else np.zeros(n)
        at_speed = (speed_kph > 150) & (brake < 0.05)

        if np.sum(at_speed) > 50:
            state.mean_front_rh_at_speed_mm = float(np.mean(front_rh[at_speed]))
            state.mean_rear_rh_at_speed_mm = float(np.mean(rear_rh[at_speed]))
            state.front_rh_std_mm = float(np.std(front_rh[at_speed]))
            state.rear_rh_std_mm = float(np.std(rear_rh[at_speed]))
            state.front_rh_p01_mm = float(np.percentile(front_rh[at_speed], 1))
            state.rear_rh_p01_mm = float(np.percentile(rear_rh[at_speed], 1))
            state.mean_speed_at_speed_kph = float(np.mean(speed_kph[at_speed]))

        # Aero compression: static - dynamic (offset-independent)
        pit_mask = speed_kph < 5.0
        if np.sum(pit_mask) > 20:
            state.static_front_rh_sensor_mm = float(np.mean(front_rh[pit_mask]))
            state.static_rear_rh_sensor_mm = float(np.mean(rear_rh[pit_mask]))
        else:
            state.static_front_rh_sensor_mm = float(np.percentile(front_rh, 95))
            state.static_rear_rh_sensor_mm = float(np.percentile(rear_rh, 95))

        if state.static_front_rh_sensor_mm > 0 and state.mean_front_rh_at_speed_mm > 0:
            state.aero_compression_front_mm = (
                state.static_front_rh_sensor_mm - state.mean_front_rh_at_speed_mm
            )
        if state.static_rear_rh_sensor_mm > 0 and state.mean_rear_rh_at_speed_mm > 0:
            state.aero_compression_rear_mm = (
                state.static_rear_rh_sensor_mm - state.mean_rear_rh_at_speed_mm
            )

        # Bottoming events: samples where RH drops below 3-sigma from mean
        front_mean_all = float(np.mean(front_rh))
        front_std_all = float(np.std(front_rh))
        rear_mean_all = float(np.mean(rear_rh))
        rear_std_all = float(np.std(rear_rh))

        front_bottom_thresh = front_mean_all - 3.0 * front_std_all
        rear_bottom_thresh = rear_mean_all - 3.0 * rear_std_all
        state.bottoming_event_count_front = int(np.sum(front_rh < front_bottom_thresh))
        state.bottoming_event_count_rear = int(np.sum(rear_rh < rear_bottom_thresh))

        # Vortex burst: front RH dropping below 3.5-sigma at speed
        # Use at-speed std (not full-lap) to avoid inflation from pit/low-speed samples
        if np.sum(at_speed) > 50:
            front_at_speed = front_rh[at_speed]
            front_mean_speed = float(np.mean(front_at_speed))
            front_std_speed = float(np.std(front_at_speed))
            vb_excursion_threshold = 3.5 * front_std_speed
            state.vortex_burst_event_count = int(
                np.sum(front_at_speed < (front_mean_speed - vb_excursion_threshold))
            )

        # Ride height excursion (p99 deviation from mean at speed)
        if np.sum(at_speed) > 50:
            front_mean = np.mean(front_rh[at_speed])
            rear_mean = np.mean(rear_rh[at_speed])
            front_deviation = np.abs(front_rh[at_speed] - front_mean)
            rear_deviation = np.abs(rear_rh[at_speed] - rear_mean)
            state.front_rh_excursion_measured_mm = float(np.percentile(front_deviation, 99))
            state.rear_rh_excursion_measured_mm = float(np.percentile(rear_deviation, 99))

        # --- LLTD from ride height deflections ---
        # Weight by track_width² to convert deflection ratio to load transfer ratio.
        # RH deflection (mm) × track_width² ∝ roll moment ∝ lateral load transfer.
        tw_f = getattr(car.arb, "track_width_front_mm", 1730.0)
        tw_r = getattr(car.arb, "track_width_rear_mm", 1650.0)
        tw_f_sq = tw_f ** 2
        tw_r_sq = tw_r ** 2

        abs_lat_g = np.abs(lat_g)
        corner_mask = abs_lat_g > 1.0
        if np.sum(corner_mask) > 100:
            front_deflection = np.abs(lf_rh[corner_mask] - rf_rh[corner_mask])
            rear_deflection = np.abs(lr_rh[corner_mask] - rr_rh[corner_mask])
            mean_front_defl = float(np.mean(front_deflection))
            mean_rear_defl = float(np.mean(rear_deflection))
            front_moment = mean_front_defl * tw_f_sq
            rear_moment = mean_rear_defl * tw_r_sq
            total_moment = front_moment + rear_moment
            if total_moment > 0.1:
                state.lltd_measured = front_moment / total_moment

        # --- Speed-dependent LLTD ---
        if np.sum(corner_mask) > 100:
            low_speed_corner = corner_mask & (speed_kph < 120)
            high_speed_corner = corner_mask & (speed_kph > 180)

            if np.sum(low_speed_corner) > 30:
                f_defl_ls = np.abs(lf_rh[low_speed_corner] - rf_rh[low_speed_corner])
                r_defl_ls = np.abs(lr_rh[low_speed_corner] - rr_rh[low_speed_corner])
                f_mom_ls = float(np.mean(f_defl_ls)) * tw_f_sq
                r_mom_ls = float(np.mean(r_defl_ls)) * tw_r_sq
                total_ls = f_mom_ls + r_mom_ls
                if total_ls > 0.1:
                    state.lltd_low_speed = f_mom_ls / total_ls

            if np.sum(high_speed_corner) > 30:
                f_defl_hs = np.abs(lf_rh[high_speed_corner] - rf_rh[high_speed_corner])
                r_defl_hs = np.abs(lr_rh[high_speed_corner] - rr_rh[high_speed_corner])
                f_mom_hs = float(np.mean(f_defl_hs)) * tw_f_sq
                r_mom_hs = float(np.mean(r_defl_hs)) * tw_r_sq
                total_hs = f_mom_hs + r_mom_hs
                if total_hs > 0.1:
                    state.lltd_high_speed = f_mom_hs / total_hs

        # --- Body roll ---
        if ibt.has_channel("Roll"):
            all_roll_deg = np.degrees(ibt.channel("Roll")[start:end + 1])
            abs_roll = np.abs(all_roll_deg)
            abs_lat_full = np.abs(lat_g)

            # p95 body roll (used by learner/empirical_models for roll gradient fitting)
            state.body_roll_p95_deg = round(float(np.percentile(abs_roll, 95)), 2)

            # Linear regression of |Roll| vs |LatAccel| in the 1-2g range
            # This is more accurate than the p95/p95 ratio which mixes
            # independent percentiles from different moments in time
            regression_mask = (abs_lat_full > 1.0) & (abs_lat_full < 2.5)
            if np.sum(regression_mask) > 50:
                x = abs_lat_full[regression_mask]
                y = abs_roll[regression_mask]
                coeffs = np.polyfit(x, y, 1)
                state.roll_gradient_measured_deg_per_g = round(float(coeffs[0]), 3)
            else:
                # Fallback to p95 ratio if insufficient cornering data
                p95_lat = float(np.percentile(abs_lat_full, 95))
                p95_roll = float(np.percentile(abs_roll, 95))
                if p95_lat > 0.5 and p95_roll > 0.1:
                    state.roll_gradient_measured_deg_per_g = p95_roll / p95_lat

            if state.peak_lat_g_measured > 1.0:
                state.body_roll_at_peak_g_deg = float(
                    state.roll_gradient_measured_deg_per_g * state.peak_lat_g_measured
                )
        else:
            # Derive from ride height differential
            if has_rh:
                track_w_m = car.arb.track_width_front_mm / 1000  # car-specific front track width
                roll_from_rh = np.degrees(np.arctan((lf_rh - rf_rh) / (track_w_m * 1000)))
                abs_roll_rh = np.abs(roll_from_rh)
                abs_lat_full = np.abs(lat_g)

                state.body_roll_p95_deg = round(float(np.percentile(abs_roll_rh, 95)), 2)

                regression_mask = (abs_lat_full > 1.0) & (abs_lat_full < 2.5)
                if np.sum(regression_mask) > 50:
                    x = abs_lat_full[regression_mask]
                    y = abs_roll_rh[regression_mask]
                    coeffs = np.polyfit(x, y, 1)
                    state.roll_gradient_measured_deg_per_g = round(float(coeffs[0]), 3)
                else:
                    p95_lat = float(np.percentile(abs_lat_full, 95))
                    p95_roll = float(np.percentile(abs_roll_rh, 95))
                    if p95_lat > 0.5 and p95_roll > 0.01:
                        state.roll_gradient_measured_deg_per_g = p95_roll / p95_lat

                if state.peak_lat_g_measured > 1.0:
                    state.body_roll_at_peak_g_deg = float(
                        state.roll_gradient_measured_deg_per_g * state.peak_lat_g_measured
                    )

        # --- FFT for natural frequency ---
        state.front_dominant_freq_hz = _dominant_frequency(
            front_rh, speed_kph, brake, ibt.tick_rate,
        )
        state.rear_dominant_freq_hz = _dominant_frequency(
            rear_rh, speed_kph, brake, ibt.tick_rate,
        )

        # --- Settle time after bump events ---
        # Average LF+RF shock velocities for front settle time (not just LF)
        front_sv_avg = (lf_sv + rf_sv) / 2
        state.front_rh_settle_time_ms = _settle_time(
            front_rh, front_sv_avg, ibt.tick_rate,
        )
        rear_sv_avg = (lr_sv + rr_sv) / 2
        state.rear_rh_settle_time_ms = _settle_time(
            rear_rh, rear_sv_avg, ibt.tick_rate,
        )

    # --- Heave shock deflection (direct spring travel measurement) ---
    # Independent of ride height channels — uses HFshockDefl/HRshockDefl directly
    brake_for_heave = ibt.channel("Brake")[start:end + 1] if ibt.has_channel("Brake") else np.zeros(n)
    _extract_heave_deflection(ibt, start, end, speed_kph, brake_for_heave, car, state)

    # --- Handling dynamics ---
    _extract_handling(ibt, start, end, speed_ms, speed_kph, lat_g, car, state)

    # --- Tyre thermal / wear / pressure ---
    _extract_tyre_data(ibt, start, end, speed_kph, state)

    # --- Splitter ride height (CFSRrideHeight) ---
    _extract_splitter_rh(ibt, start, end, speed_kph, brake_for_heave, state)

    # --- Corner shock deflections ---
    _extract_corner_shock_defl(ibt, start, end, state)

    # --- Heave shock velocities ---
    _extract_heave_shock_vel(ibt, start, end, speed_kph, state)

    # --- Brake system (line pressures, ABS) ---
    _extract_brake_system(ibt, start, end, state)

    # --- In-car adjustments ---
    _extract_in_car_adjustments(ibt, state)

    # --- Fuel level ---
    _extract_fuel(ibt, start, end, state)

    # --- Hybrid/ERS ---
    _extract_hybrid(ibt, start, end, state)

    # --- Environmental ---
    _extract_environmental(ibt, state)

    # --- RPM analysis ---
    _extract_rpm(ibt, start, end, speed_kph, state)

    # --- Raw driver inputs (ThrottleRaw, BrakeRaw) ---
    _extract_raw_inputs(ibt, start, end, speed_kph, state)

    # --- Gear ---
    _extract_gear(ibt, start, end, state)

    # --- Pitch dynamics ---
    brake_for_pitch = ibt.channel("Brake")[start:end + 1] if ibt.has_channel("Brake") else np.zeros(n)
    _extract_pitch(ibt, start, end, speed_kph, brake_for_pitch, state)

    # --- Extended in-car adjustments ---
    _extract_extended_adjustments(ibt, start, end, state)

    # --- Wind ---
    _extract_wind(ibt, state)

    # --- Per-corner shock velocities ---
    state.lf_shock_vel_p95_mps = float(np.percentile(lf_sv, 95))
    state.rf_shock_vel_p95_mps = float(np.percentile(rf_sv, 95))
    state.lr_shock_vel_p95_mps = float(np.percentile(lr_sv, 95))
    state.rr_shock_vel_p95_mps = float(np.percentile(rr_sv, 95))

    # --- Rebuild full track profile ---
    try:
        state.measured_track_profile = build_profile(ibt_path)
    except Exception:
        state.measured_track_profile = None

    return state


def _extract_handling(
    ibt: IBTFile,
    start: int,
    end: int,
    speed_ms: np.ndarray,
    speed_kph: np.ndarray,
    lat_g: np.ndarray,
    car: CarModel,
    state: MeasuredState,
) -> None:
    """Extract handling dynamics: understeer, body slip, wheel slip, yaw correlation."""

    n = end - start + 1

    # Load channels
    steer = ibt.channel("SteeringWheelAngle")[start:end + 1]  # rad
    yaw_rate = ibt.channel("YawRate")[start:end + 1]  # rad/s

    # Body-frame velocities
    vx = ibt.channel("VelocityX")[start:end + 1]  # m/s (forward)
    vy = ibt.channel("VelocityY")[start:end + 1]  # m/s (lateral)

    # --- Understeer angle ---
    ratio = car.steering_ratio
    wb = car.wheelbase_m
    safe_speed = np.maximum(speed_ms, 5.0)

    road_wheel_angle = steer / ratio  # rad
    neutral_yaw_rate = road_wheel_angle * safe_speed / wb
    understeer_rad = road_wheel_angle - wb * yaw_rate / safe_speed
    understeer_deg = np.degrees(understeer_rad)

    # Filter to cornering regions (|lat_g| > 0.5, speed > 40 kph)
    cornering = (np.abs(lat_g) > 0.5) & (speed_kph > 40)

    if np.sum(cornering) > 100:
        state.understeer_mean_deg = float(np.mean(understeer_deg[cornering]))

        # Low-speed corners: <120 kph, |lat_g| > 0.8
        low_speed = cornering & (speed_kph < 120) & (np.abs(lat_g) > 0.8)
        if np.sum(low_speed) > 30:
            state.understeer_low_speed_deg = float(np.mean(understeer_deg[low_speed]))

        # High-speed corners: >180 kph, |lat_g| > 0.5
        high_speed = cornering & (speed_kph > 180) & (np.abs(lat_g) > 0.5)
        if np.sum(high_speed) > 30:
            state.understeer_high_speed_deg = float(np.mean(understeer_deg[high_speed]))

        # Left/right understeer split (detect asymmetric handling)
        left_turn = cornering & (lat_g > 0.5)   # positive lat_g = left turn
        right_turn = cornering & (lat_g < -0.5)
        if np.sum(left_turn) > 30:
            state.understeer_left_turn_deg = float(np.mean(understeer_deg[left_turn]))
        if np.sum(right_turn) > 30:
            state.understeer_right_turn_deg = float(np.mean(understeer_deg[right_turn]))

    # --- Body slip angle ---
    body_slip_deg = np.degrees(np.arctan2(vy, np.maximum(np.abs(vx), 1.0)))
    abs_body_slip = np.abs(body_slip_deg)

    at_speed_mask = speed_kph > 60
    if np.sum(at_speed_mask) > 100:
        state.body_slip_p95_deg = float(np.percentile(abs_body_slip[at_speed_mask], 95))

    # Body slip at peak lateral g
    if state.peak_lat_g_measured > 1.0:
        peak_mask = np.abs(lat_g) > (state.peak_lat_g_measured * 0.9)
        if np.sum(peak_mask) > 10:
            state.body_slip_at_peak_g_deg = float(np.mean(abs_body_slip[peak_mask]))

    # --- Wheel slip ratios ---
    if all(ibt.has_channel(c) for c in ["LFspeed", "RFspeed", "LRspeed", "RRspeed"]):
        lf_ws = ibt.channel("LFspeed")[start:end + 1]
        rf_ws = ibt.channel("RFspeed")[start:end + 1]
        lr_ws = ibt.channel("LRspeed")[start:end + 1]
        rr_ws = ibt.channel("RRspeed")[start:end + 1]

        safe_car_speed = np.maximum(speed_ms, 2.0)

        rear_avg_ws = (lr_ws + rr_ws) / 2.0
        rear_slip = (rear_avg_ws - safe_car_speed) / safe_car_speed

        front_avg_ws = (lf_ws + rf_ws) / 2.0
        front_slip = (front_avg_ws - safe_car_speed) / safe_car_speed

        driving_mask = speed_kph > 60
        if np.sum(driving_mask) > 100:
            state.rear_slip_ratio_p95 = float(np.percentile(np.abs(rear_slip[driving_mask]), 95))
            state.front_slip_ratio_p95 = float(np.percentile(np.abs(front_slip[driving_mask]), 95))

    # --- Yaw rate correlation ---
    if np.sum(cornering) > 100:
        actual = yaw_rate[cornering]
        expected = neutral_yaw_rate[cornering]
        if np.std(actual) > 0.01 and np.std(expected) > 0.01:
            corr = np.corrcoef(actual, expected)[0, 1]
            state.yaw_rate_correlation = round(float(corr ** 2), 3)

    # --- Roll rate and pitch rate ---
    if ibt.has_channel("RollRate"):
        roll_rate = np.degrees(ibt.channel("RollRate")[start:end + 1])
        state.roll_rate_p95_deg_per_s = float(np.percentile(np.abs(roll_rate), 95))

    if ibt.has_channel("PitchRate"):
        pitch_rate = np.degrees(ibt.channel("PitchRate")[start:end + 1])
        state.pitch_rate_p95_deg_per_s = float(np.percentile(np.abs(pitch_rate), 95))


def _extract_tyre_data(
    ibt: IBTFile,
    start: int,
    end: int,
    speed_kph: np.ndarray,
    state: MeasuredState,
) -> None:
    """Extract tyre temperature, pressure, and wear data."""

    n = end - start + 1

    at_speed = speed_kph > 60

    if np.sum(at_speed) < 100:
        return

    # --- Temperature spread (inner - outer surface temp) ---
    temp_channels = {
        "LF": ("LFtempL", "LFtempR"),
        "RF": ("RFtempL", "RFtempR"),
        "LR": ("LRtempL", "LRtempR"),
        "RR": ("RRtempL", "RRtempR"),
    }

    for corner, (ch_l, ch_r) in temp_channels.items():
        if ibt.has_channel(ch_l) and ibt.has_channel(ch_r):
            temp_l = ibt.channel(ch_l)[start:end + 1]
            temp_r = ibt.channel(ch_r)[start:end + 1]

            if corner.startswith("L"):
                inner = temp_r[at_speed]
                outer = temp_l[at_speed]
            else:
                inner = temp_l[at_speed]
                outer = temp_r[at_speed]

            spread = float(np.mean(inner - outer))

            if corner == "LF":
                state.front_temp_spread_lf_c = round(spread, 1)
            elif corner == "RF":
                state.front_temp_spread_rf_c = round(spread, 1)
            elif corner == "LR":
                state.rear_temp_spread_lr_c = round(spread, 1)
            elif corner == "RR":
                state.rear_temp_spread_rr_c = round(spread, 1)

    # --- Carcass temperature ---
    carcass_channels_front = ["LFtempCM", "RFtempCM"]
    carcass_channels_rear = ["LRtempCM", "RRtempCM"]

    front_carcass = []
    for ch in carcass_channels_front:
        if ibt.has_channel(ch):
            front_carcass.append(np.mean(ibt.channel(ch)[start:end + 1][at_speed]))
    if front_carcass:
        state.front_carcass_mean_c = round(float(np.mean(front_carcass)), 1)

    rear_carcass = []
    for ch in carcass_channels_rear:
        if ibt.has_channel(ch):
            rear_carcass.append(np.mean(ibt.channel(ch)[start:end + 1][at_speed]))
    if rear_carcass:
        state.rear_carcass_mean_c = round(float(np.mean(rear_carcass)), 1)

    # --- Carcass temperature gradient (CL vs CR) for deep camber validation ---
    # When carcass temps are reliable (deviate from ambient), inner-outer
    # carcass spread confirms whether surface temp spread is a real camber issue
    carcass_gradient_channels = {
        "LF": ("LFtempCL", "LFtempCR"),
        "RF": ("RFtempCL", "RFtempCR"),
        "LR": ("LRtempCL", "LRtempCR"),
        "RR": ("RRtempCL", "RRtempCR"),
    }
    for corner, (ch_l, ch_r) in carcass_gradient_channels.items():
        if ibt.has_channel(ch_l) and ibt.has_channel(ch_r):
            cl = ibt.channel(ch_l)[start:end + 1][at_speed]
            cr = ibt.channel(ch_r)[start:end + 1][at_speed]
            # Only compute if carcass temps show meaningful deviation from ambient
            # (ambient ~25-35C, working temps >60C)
            mean_cl = float(np.mean(cl))
            mean_cr = float(np.mean(cr))
            if mean_cl > 50 or mean_cr > 50:  # Carcass is at working temperature
                if corner.startswith("L"):
                    gradient = float(np.mean(cr - cl))  # inner(R) - outer(L)
                else:
                    gradient = float(np.mean(cl - cr))  # inner(L) - outer(R)

                if corner == "LF":
                    state.front_carcass_gradient_lf_c = round(gradient, 1)
                elif corner == "RF":
                    state.front_carcass_gradient_rf_c = round(gradient, 1)
                elif corner == "LR":
                    state.rear_carcass_gradient_lr_c = round(gradient, 1)
                elif corner == "RR":
                    state.rear_carcass_gradient_rr_c = round(gradient, 1)

    # --- Tyre pressure (per-corner + axle average) ---
    per_corner_pressures: dict[str, float] = {}
    for prefix in ["LF", "RF", "LR", "RR"]:
        ch = f"{prefix}pressure"
        if ibt.has_channel(ch):
            pressure = ibt.channel(ch)[start:end + 1]
            mean_p = float(np.mean(pressure[at_speed]))
            per_corner_pressures[prefix] = mean_p
            setattr(state, f"{prefix.lower()}_pressure_kpa", round(mean_p, 1))

    # Axle averages (backward-compatible)
    if "LF" in per_corner_pressures and "RF" in per_corner_pressures:
        state.front_pressure_mean_kpa = round(
            (per_corner_pressures["LF"] + per_corner_pressures["RF"]) / 2.0, 1)
    if "LR" in per_corner_pressures and "RR" in per_corner_pressures:
        state.rear_pressure_mean_kpa = round(
            (per_corner_pressures["LR"] + per_corner_pressures["RR"]) / 2.0, 1)

    # --- Cold tyre pressure ---
    for prefix in ["LF", "RF", "LR", "RR"]:
        ch = f"{prefix}coldPressure"
        if ibt.has_channel(ch):
            cold_p = float(ibt.channel(ch)[start])  # First sample = cold start
            setattr(state, f"{prefix.lower()}_cold_pressure_kpa", round(cold_p, 1))

    # --- Per-corner surface temps (inner, middle, outer) ---
    for prefix in ["LF", "RF", "LR", "RR"]:
        is_left = prefix.startswith("L")
        ch_l = f"{prefix}tempL"
        ch_m = f"{prefix}tempM"
        ch_r = f"{prefix}tempR"
        p = prefix.lower()

        if ibt.has_channel(ch_l) and ibt.has_channel(ch_r):
            temp_l = ibt.channel(ch_l)[start:end + 1][at_speed]
            temp_r = ibt.channel(ch_r)[start:end + 1][at_speed]
            inner = float(np.mean(temp_r if is_left else temp_l))
            outer = float(np.mean(temp_l if is_left else temp_r))
            setattr(state, f"{p}_temp_inner_c", round(inner, 1))
            setattr(state, f"{p}_temp_outer_c", round(outer, 1))

        if ibt.has_channel(ch_m):
            temp_m = ibt.channel(ch_m)[start:end + 1][at_speed]
            setattr(state, f"{p}_temp_middle_c", round(float(np.mean(temp_m)), 1))

    # --- Tyre wear (end-of-lap snapshot, per-corner + axle average) ---
    per_corner_wear: dict[str, float] = {}
    for prefix in ["LF", "RF", "LR", "RR"]:
        wear_channels = [f"{prefix}wearL", f"{prefix}wearM", f"{prefix}wearR"]
        wear_vals = []
        for ch in wear_channels:
            if ibt.has_channel(ch):
                wear_vals.append(float(ibt.channel(ch)[end]))
        if wear_vals:
            avg_wear = float(np.mean(wear_vals)) * 100
            per_corner_wear[prefix] = avg_wear
            setattr(state, f"{prefix.lower()}_wear_pct", round(avg_wear, 1))

    if "LF" in per_corner_wear and "RF" in per_corner_wear:
        state.front_wear_mean_pct = round(
            (per_corner_wear["LF"] + per_corner_wear["RF"]) / 2.0, 1)
    if "LR" in per_corner_wear and "RR" in per_corner_wear:
        state.rear_wear_mean_pct = round(
            (per_corner_wear["LR"] + per_corner_wear["RR"]) / 2.0, 1)


def _find_lap(ibt: IBTFile, lap_num: int) -> tuple[int, int]:
    """Find start/end sample indices for a specific lap number."""
    boundaries = ibt.lap_boundaries()
    for num, start, end in boundaries:
        if num == lap_num:
            return (start, end)
    available = [b[0] for b in boundaries]
    raise ValueError(f"Lap {lap_num} not found. Available: {available}")


def _dominant_frequency(
    rh_signal: np.ndarray,
    speed_kph: np.ndarray,
    brake: np.ndarray,
    tick_rate: int,
    min_speed_kph: float = 200.0,
    min_segment_samples: int = 120,
) -> float:
    """Find dominant ride height oscillation frequency via FFT.

    Analyzes clean straight segments (high speed, no braking) where the
    ride height oscillation reflects the natural frequency of the suspension.

    Returns dominant frequency in Hz, or 0.0 if insufficient data.
    """
    clean = (speed_kph > min_speed_kph) & (brake < 0.05)

    edges = np.diff(clean.astype(int))
    starts = np.where(edges == 1)[0] + 1
    ends = np.where(edges == -1)[0] + 1

    if clean[0]:
        starts = np.insert(starts, 0, 0)
    if clean[-1]:
        ends = np.append(ends, len(clean))

    all_power = None
    freq_axis = None

    n_segs = min(len(starts), len(ends))
    for i in range(n_segs):
        s, e = starts[i], ends[i]
        seg_len = e - s
        if seg_len < min_segment_samples:
            continue

        segment = rh_signal[s:e]
        segment = segment - np.mean(segment)
        window = np.hanning(seg_len)
        windowed = segment * window

        fft_vals = np.fft.rfft(windowed)
        power = np.abs(fft_vals) ** 2
        freqs = np.fft.rfftfreq(seg_len, d=1.0 / tick_rate)

        if all_power is None:
            all_power = power
            freq_axis = freqs
        elif len(power) == len(all_power):
            all_power += power

    if all_power is None or freq_axis is None:
        return 0.0

    valid = (freq_axis >= 0.5) & (freq_axis <= 10.0)
    if not np.any(valid):
        return 0.0

    valid_freqs = freq_axis[valid]
    valid_power = all_power[valid]

    peak_idx = np.argmax(valid_power)
    return round(float(valid_freqs[peak_idx]), 2)


def _settle_time(
    rh_signal: np.ndarray,
    shock_vel: np.ndarray,
    tick_rate: int,
    max_search_ms: float = 500.0,
) -> float:
    """Measure average time for ride height to settle after a bump event.

    A bump event is defined as a shock velocity exceeding the p95 threshold.
    Settle time is when the ride height returns within 1-sigma of the
    running mean.

    Returns average settle time in ms, or 0.0 if insufficient events.
    """
    if len(rh_signal) < 100 or len(shock_vel) < 100:
        return 0.0

    n = min(len(rh_signal), len(shock_vel))
    rh_signal = rh_signal[:n]
    shock_vel = shock_vel[:n]

    p95 = float(np.percentile(shock_vel, 95))
    if p95 < 0.01:
        return 0.0

    sigma = float(np.std(rh_signal))
    if sigma < 0.1:
        return 0.0

    window = min(int(tick_rate * 0.5), n // 4)
    if window < 5:
        return 0.0
    kernel = np.ones(window) / window
    running_mean = np.convolve(rh_signal, kernel, mode="same")

    bump_mask = shock_vel > p95
    edges = np.diff(bump_mask.astype(int))
    bump_starts = np.where(edges == 1)[0] + 1

    max_search_samples = int(max_search_ms / 1000.0 * tick_rate)
    settle_times = []

    for bs in bump_starts:
        search_end = min(bs + max_search_samples, n)
        for j in range(bs, search_end):
            if abs(rh_signal[j] - running_mean[j]) < sigma:
                settle_times.append((j - bs) / tick_rate * 1000.0)
                break

    if not settle_times:
        return 0.0

    return round(float(np.median(settle_times)), 1)


def _extract_heave_deflection(
    ibt: IBTFile,
    start: int,
    end: int,
    speed_kph: np.ndarray,
    brake: np.ndarray,
    car: CarModel,
    state: MeasuredState,
) -> None:
    """Extract heave shock deflection data for spring travel analysis.

    Heave deflection measures actual spring travel (compression from static).
    When deflection approaches DeflMax, the spring is bottoming out — the car
    becomes rigid and loses mechanical grip.

    Physics:
        - Spring force is linear: F = k * x (position-dependent)
        - Shock force is nonlinear: F = c(v) * v (velocity-dependent)
        - Under braking (slow weight transfer, LS regime), the spring dominates
        - If spring travel is exhausted, the car hits a rigid bump stop
        - Symptom: entry rotation (spring compressing) → mid-corner push (bottomed)

    Channels:
        HFshockDefl — front heave element deflection (meters)
        HRshockDefl — rear heave/third element deflection (may be missing)
    """
    n = end - start + 1
    hsm = car.heave_spring

    # --- Front heave deflection ---
    if ibt.has_channel("HFshockDefl"):
        hf_defl = np.abs(ibt.channel("HFshockDefl")[start:end + 1]) * 1000  # m → mm

        # At-speed analysis (>150 kph, no heavy braking)
        at_speed = (speed_kph > 150) & (brake < 0.05)
        if np.sum(at_speed) > 50:
            state.front_heave_defl_mean_mm = round(float(np.mean(hf_defl[at_speed])), 2)
            state.front_heave_defl_std_mm = round(float(np.std(hf_defl[at_speed])), 2)
            state.front_heave_defl_p99_mm = round(float(np.percentile(hf_defl[at_speed], 99)), 2)

        # Full-lap max
        state.front_heave_defl_max_mm = round(float(np.max(hf_defl)), 2)

        # Compute travel usage (p99 deflection as % of DeflMax)
        # Read actual heave spring rate from session YAML if available
        heave_rate_nmm = 50.0  # fallback
        try:
            si = ibt.session_info
            if isinstance(si, dict):
                cs = si.get("CarSetup", {})
                front_setup = cs.get("Chassis", cs.get("Front", {})).get("Front", {})
                hs_val = front_setup.get("HeaveSpring")
                if hs_val is not None:
                    parsed = float(str(hs_val).replace(" N/mm", "").strip())
                    if parsed > 0:
                        heave_rate_nmm = parsed
        except (ValueError, TypeError, AttributeError):
            pass
        defl_max_ref = 0.0
        if hsm.heave_spring_defl_max_intercept_mm > 0:
            defl_max_ref = hsm.heave_spring_defl_max_intercept_mm + hsm.heave_spring_defl_max_slope * heave_rate_nmm
            full_p99 = round(float(np.percentile(hf_defl, 99)), 2)
            if defl_max_ref > 0:
                state.front_heave_travel_used_pct = round(full_p99 / defl_max_ref * 100, 1)

                # Direct spring bottoming: deflection within 2mm of DeflMax
                bottom_thresh = defl_max_ref - 2.0
                if bottom_thresh > 0:
                    state.heave_bottoming_events_front = int(np.sum(hf_defl > bottom_thresh))

        # --- Braking-specific analysis ---
        # Under braking (Brake > 0.3), weight transfer compresses front heave spring.
        # If it exhausts travel here, driver feels entry rotation → mid-corner push.
        braking_mask = brake > 0.3
        if np.sum(braking_mask) > 20:
            hf_braking = hf_defl[braking_mask]
            state.front_heave_defl_braking_p99_mm = round(float(np.percentile(hf_braking, 99)), 2)
            if defl_max_ref > 0:
                state.front_heave_travel_used_braking_pct = round(
                    state.front_heave_defl_braking_p99_mm / defl_max_ref * 100, 1
                )

    # --- Rear heave/third deflection ---
    # HRshockDefl may be missing in some IBT files (per ibt-parsing-guide.md)
    if ibt.has_channel("HRshockDefl"):
        hr_defl = np.abs(ibt.channel("HRshockDefl")[start:end + 1]) * 1000  # m → mm

        at_speed = (speed_kph > 150) & (brake < 0.05)
        if np.sum(at_speed) > 50:
            state.rear_heave_defl_mean_mm = round(float(np.mean(hr_defl[at_speed])), 2)
            state.rear_heave_defl_std_mm = round(float(np.std(hr_defl[at_speed])), 2)
            state.rear_heave_defl_p99_mm = round(float(np.percentile(hr_defl[at_speed], 99)), 2)

        state.rear_heave_defl_max_mm = round(float(np.max(hr_defl)), 2)

        # Rear travel usage — use per-car DeflMax from car model
        rear_defl_max = hsm.rear_third_defl_max_mm
        full_p99_rear = round(float(np.percentile(hr_defl, 99)), 2)
        if rear_defl_max > 0:
            state.rear_heave_travel_used_pct = round(full_p99_rear / rear_defl_max * 100, 1)
            bottom_thresh_rear = rear_defl_max - 2.0
            state.heave_bottoming_events_rear = int(np.sum(hr_defl > bottom_thresh_rear))


def _extract_splitter_rh(
    ibt: IBTFile, start: int, end: int,
    speed_kph: np.ndarray, brake: np.ndarray,
    state: MeasuredState,
) -> None:
    """Extract center front splitter ride height (CFSRrideHeight).

    The most important single aero channel — directly measures splitter-to-ground
    clearance. When near zero, the splitter is scraping and aero stall is imminent.
    """
    if not ibt.has_channel("CFSRrideHeight"):
        return

    cfsr = ibt.channel("CFSRrideHeight")[start:end + 1] * 1000  # m → mm
    at_speed = (speed_kph > 150) & (brake < 0.05)

    if np.sum(at_speed) > 50:
        state.splitter_rh_mean_at_speed_mm = round(float(np.mean(cfsr[at_speed])), 2)
        state.splitter_rh_std_mm = round(float(np.std(cfsr[at_speed])), 2)
        state.splitter_rh_p01_mm = round(float(np.percentile(cfsr[at_speed], 1)), 2)

    state.splitter_rh_min_mm = round(float(np.min(cfsr)), 2)
    state.splitter_scrape_events = int(np.sum(cfsr < 2.0))


def _extract_corner_shock_defl(
    ibt: IBTFile, start: int, end: int,
    state: MeasuredState,
) -> None:
    """Extract corner shock deflection (LF/RF/LR/RRshockDefl).

    Corner shock deflection measures actual spring compression from static.
    More precise than ride height for bottoming detection since it directly
    measures spring travel remaining.
    """
    front_channels = ["LFshockDefl", "RFshockDefl"]
    rear_channels = ["LRshockDefl", "RRshockDefl"]

    if all(ibt.has_channel(c) for c in front_channels):
        lf_defl = np.abs(ibt.channel("LFshockDefl")[start:end + 1]) * 1000  # m → mm
        rf_defl = np.abs(ibt.channel("RFshockDefl")[start:end + 1]) * 1000
        front_defl = (lf_defl + rf_defl) / 2.0
        state.front_corner_defl_p99_mm = round(float(np.percentile(front_defl, 99)), 2)
        state.front_corner_defl_max_mm = round(float(np.max(front_defl)), 2)

    if all(ibt.has_channel(c) for c in rear_channels):
        lr_defl = np.abs(ibt.channel("LRshockDefl")[start:end + 1]) * 1000
        rr_defl = np.abs(ibt.channel("RRshockDefl")[start:end + 1]) * 1000
        rear_defl = (lr_defl + rr_defl) / 2.0
        state.rear_corner_defl_p99_mm = round(float(np.percentile(rear_defl, 99)), 2)
        state.rear_corner_defl_max_mm = round(float(np.max(rear_defl)), 2)


def _extract_heave_shock_vel(
    ibt: IBTFile, start: int, end: int,
    speed_kph: np.ndarray,
    state: MeasuredState,
) -> None:
    """Extract heave shock velocities (HFshockVel, HRshockVel).

    Classifies heave damper regime:
    - <25 mm/s = LS regime (controlled by LS damper settings)
    - >100 mm/s = HS regime (controlled by HS damper settings)

    High HFshockVel variance at speed indicates platform instability
    from the heave damper, not the spring.
    """
    at_speed = speed_kph > 150

    if ibt.has_channel("HFshockVel"):
        hf_vel = np.abs(ibt.channel("HFshockVel")[start:end + 1])
        hf_vel_mmps = hf_vel * 1000  # m/s → mm/s

        if np.sum(at_speed) > 50:
            hf_at_speed = hf_vel[at_speed]
            state.front_heave_vel_p95_mps = round(float(np.percentile(hf_at_speed, 95)), 4)
            state.front_heave_vel_p99_mps = round(float(np.percentile(hf_at_speed, 99)), 4)

        # Regime classification (full lap)
        total = len(hf_vel_mmps)
        if total > 0:
            state.front_heave_vel_ls_pct = round(float(np.sum(hf_vel_mmps < 25) / total * 100), 1)
            state.front_heave_vel_hs_pct = round(float(np.sum(hf_vel_mmps > 100) / total * 100), 1)

    if ibt.has_channel("HRshockVel"):
        hr_vel = np.abs(ibt.channel("HRshockVel")[start:end + 1])
        if np.sum(at_speed) > 50:
            hr_at_speed = hr_vel[at_speed]
            state.rear_heave_vel_p95_mps = round(float(np.percentile(hr_at_speed, 95)), 4)
            state.rear_heave_vel_p99_mps = round(float(np.percentile(hr_at_speed, 99)), 4)


def _extract_brake_system(
    ibt: IBTFile, start: int, end: int,
    state: MeasuredState,
) -> None:
    """Extract brake line pressures, ABS data, and compute measured brake bias.

    Per-corner brake line pressure shows the ACTUAL force distribution including
    the bias setting and ABS intervention. Comparing front vs rear pressure gives
    the true hydraulic bias.
    """
    brake = ibt.channel("Brake")[start:end + 1] if ibt.has_channel("Brake") else None
    braking = (brake > 0.3) if brake is not None else None

    # Brake line pressures → measured brake bias
    front_press_chs = ["LFbrakeLinePress", "RFbrakeLinePress"]
    rear_press_chs = ["LRbrakeLinePress", "RRbrakeLinePress"]

    if all(ibt.has_channel(c) for c in front_press_chs + rear_press_chs):
        lf_press = ibt.channel("LFbrakeLinePress")[start:end + 1]
        rf_press = ibt.channel("RFbrakeLinePress")[start:end + 1]
        lr_press = ibt.channel("LRbrakeLinePress")[start:end + 1]
        rr_press = ibt.channel("RRbrakeLinePress")[start:end + 1]

        front_avg = (lf_press + rf_press) / 2.0
        rear_avg = (lr_press + rr_press) / 2.0

        state.front_brake_pressure_peak_bar = round(float(np.max(front_avg)), 1)
        state.rear_brake_pressure_peak_bar = round(float(np.max(rear_avg)), 1)

        if braking is not None and np.sum(braking) > 20:
            front_braking = front_avg[braking]
            rear_braking = rear_avg[braking]
            total = front_braking + rear_braking
            valid = total > 1.0  # bar
            if np.sum(valid) > 10:
                bias_samples = front_braking[valid] / total[valid] * 100
                state.measured_brake_bias_pct = round(float(np.mean(bias_samples)), 1)

    # ABS activity
    if ibt.has_channel("BrakeABSactive") and braking is not None:
        abs_active = ibt.channel("BrakeABSactive")[start:end + 1]
        braking_count = int(np.sum(braking))
        if braking_count > 10:
            state.abs_active_pct = round(
                float(np.sum(abs_active[braking] > 0.5) / braking_count * 100), 1
            )

    if ibt.has_channel("BrakeABScutPct") and braking is not None:
        abs_cut = ibt.channel("BrakeABScutPct")[start:end + 1]
        abs_engaged = braking & (abs_cut > 0.01)
        if np.sum(abs_engaged) > 5:
            state.abs_cut_mean_pct = round(float(np.mean(abs_cut[abs_engaged]) * 100), 1)


def _extract_in_car_adjustments(
    ibt: IBTFile,
    state: MeasuredState,
) -> None:
    """Track in-car adjustment changes across the full session.

    If the driver is frequently adjusting brake bias or TC, the base setup
    values are wrong and should be changed in the garage.
    """
    if ibt.has_channel("dcBrakeBias"):
        bias = ibt.channel("dcBrakeBias")
        if bias is not None and len(bias) > 100:
            changes = np.sum(np.abs(np.diff(bias)) > 0.001)
            state.brake_bias_adjustments = int(changes)
            state.brake_bias_range = (round(float(np.min(bias)), 2), round(float(np.max(bias)), 2))

    if ibt.has_channel("dcTractionControl"):
        tc = ibt.channel("dcTractionControl")
        if tc is not None and len(tc) > 100:
            changes = np.sum(np.abs(np.diff(tc)) > 0.001)
            state.tc_adjustments = int(changes)


def _extract_fuel(
    ibt: IBTFile, start: int, end: int,
    state: MeasuredState,
) -> None:
    """Extract fuel level for weight/balance calculations.

    89L full tank → 0L = ~65kg mass change = ~3% weight distribution shift.
    """
    if ibt.has_channel("FuelLevel"):
        fuel = ibt.channel("FuelLevel")[start:end + 1]
        state.fuel_level_at_measurement_l = round(float(np.mean(fuel)), 1)
        fuel_start = float(fuel[0])
        fuel_end = float(fuel[-1])
        if fuel_start > fuel_end:
            state.fuel_used_per_lap_l = round(fuel_start - fuel_end, 2)


def _extract_hybrid(
    ibt: IBTFile, start: int, end: int,
    state: MeasuredState,
) -> None:
    """Extract hybrid/ERS data for traction context.

    When battery is depleted, less rear torque is available from MGU-K.
    MGU-K torque spikes can cause traction events unrelated to diff/TC.
    """
    if ibt.has_channel("EnergyERSBatteryPct"):
        bat_pct = ibt.channel("EnergyERSBatteryPct")[start:end + 1]
        state.ers_battery_mean_pct = round(float(np.mean(bat_pct) * 100), 1)
        state.ers_battery_min_pct = round(float(np.min(bat_pct) * 100), 1)
    elif ibt.has_channel("EnergyERSBattery"):
        # Fallback to absolute energy (normalize to approximate percentage)
        bat_j = ibt.channel("EnergyERSBattery")[start:end + 1]
        max_j = float(np.max(bat_j)) if np.max(bat_j) > 0 else 1.0
        state.ers_battery_mean_pct = round(float(np.mean(bat_j) / max_j * 100), 1)
        state.ers_battery_min_pct = round(float(np.min(bat_j) / max_j * 100), 1)

    if ibt.has_channel("TorqueMGU_K"):
        mguk = ibt.channel("TorqueMGU_K")[start:end + 1]
        state.mguk_torque_peak_nm = round(float(np.max(np.abs(mguk))), 1)


def _extract_environmental(
    ibt: IBTFile,
    state: MeasuredState,
) -> None:
    """Extract environmental conditions from session info or telemetry channels.

    Air/track temp affect tyre pressures and grip.
    Air density affects aero forces (DF and drag scale linearly with density).
    """
    if ibt.has_channel("AirTemp"):
        air_temp = ibt.channel("AirTemp")
        if air_temp is not None and len(air_temp) > 0:
            state.air_temp_c = round(float(np.mean(air_temp)), 1)

    if ibt.has_channel("TrackTempCrew"):
        track_temp = ibt.channel("TrackTempCrew")
        if track_temp is not None and len(track_temp) > 0:
            state.track_temp_c = round(float(np.mean(track_temp)), 1)

    if ibt.has_channel("AirDensity"):
        density = ibt.channel("AirDensity")
        if density is not None and len(density) > 0:
            state.air_density_kg_m3 = round(float(np.mean(density)), 4)


def _extract_rpm(
    ibt: IBTFile, start: int, end: int,
    speed_kph: np.ndarray,
    state: MeasuredState,
) -> None:
    """Extract RPM data for rev limiter and gear analysis.

    Hitting the rev limiter before braking zones = lost time from not
    upshifting or from gear ratio mismatch.
    """
    if not ibt.has_channel("RPM") or not ibt.has_channel("Brake"):
        return

    rpm = ibt.channel("RPM")[start:end + 1]
    brake = ibt.channel("Brake")[start:end + 1]

    # Detect rev limiter hits: RPM within 50 of max observed RPM
    rpm_max = float(np.max(rpm))
    if rpm_max < 1000:
        return

    limiter_threshold = rpm_max - 50
    at_limiter = rpm > limiter_threshold

    # Check how often we're at limiter just before braking (within 0.5s = 30 samples)
    braking_starts = np.where(np.diff(brake > 0.1) == 1)[0]
    if len(braking_starts) == 0:
        return

    limiter_before_braking = 0
    for bs in braking_starts:
        lookback = max(0, bs - 30)
        if np.any(at_limiter[lookback:bs]):
            limiter_before_braking += 1

    state.rpm_at_braking_pct_at_limiter = round(
        limiter_before_braking / len(braking_starts) * 100, 1
    )


def _extract_raw_inputs(
    ibt: IBTFile,
    start: int,
    end: int,
    speed_kph: np.ndarray,
    state: MeasuredState,
) -> None:
    """Extract raw driver inputs (before TC/ABS intervention).

    ThrottleRaw vs Throttle difference reveals TC intervention.
    BrakeRaw vs Brake difference reveals ABS intervention.
    """
    at_speed = speed_kph > 100

    if ibt.has_channel("ThrottleRaw"):
        throttle_raw = ibt.channel("ThrottleRaw")[start:end + 1]
        if np.sum(at_speed) > 50:
            state.throttle_raw_mean = round(float(np.mean(throttle_raw[at_speed])), 3)

        # TC intervention: ThrottleRaw > Throttle means TC is cutting
        if ibt.has_channel("Throttle"):
            throttle = ibt.channel("Throttle")[start:end + 1]
            tc_cutting = (throttle_raw > throttle + 0.02) & (throttle_raw > 0.1)
            throttle_applied = throttle_raw > 0.1
            if np.sum(throttle_applied) > 50:
                state.tc_intervention_pct = round(
                    float(np.sum(tc_cutting) / np.sum(throttle_applied) * 100), 1)

    if ibt.has_channel("BrakeRaw"):
        brake_raw = ibt.channel("BrakeRaw")[start:end + 1]
        state.brake_raw_peak = round(float(np.max(brake_raw)), 3)


def _extract_gear(
    ibt: IBTFile,
    start: int,
    end: int,
    state: MeasuredState,
    corners: list | None = None,
) -> None:
    """Extract gear data for corner classification and rev analysis."""
    if not ibt.has_channel("Gear"):
        return

    gear = ibt.channel("Gear")[start:end + 1].astype(int)
    state.max_gear = int(np.max(gear))

    # Mode gear at corner apexes (if corner info available)
    if corners:
        apex_gears = []
        for c in corners:
            # Find approximate sample for this corner's apex
            if hasattr(c, "apex_speed_kph") and c.apex_speed_kph > 0:
                # Use the min-speed sample within the corner
                apex_gears.append(int(gear[min(c.corner_id, len(gear) - 1)]))
        if apex_gears:
            from collections import Counter
            gear_counts = Counter(apex_gears)
            state.gear_at_apex_mode = gear_counts.most_common(1)[0][0]


def _extract_pitch(
    ibt: IBTFile,
    start: int,
    end: int,
    speed_kph: np.ndarray,
    brake: np.ndarray,
    state: MeasuredState,
) -> None:
    """Extract pitch dynamics for aero platform analysis.

    Pitch angle at speed = actual rake.
    Pitch range = platform stability under braking/acceleration.
    """
    at_speed = (speed_kph > 150) & (brake < 0.05)

    if ibt.has_channel("Pitch"):
        pitch = np.degrees(ibt.channel("Pitch")[start:end + 1])
        if np.sum(at_speed) > 50:
            state.pitch_mean_at_speed_deg = round(float(np.mean(pitch[at_speed])), 3)
        state.pitch_range_deg = round(
            float(np.percentile(pitch, 99) - np.percentile(pitch, 1)), 2)


def _extract_extended_adjustments(
    ibt: IBTFile,
    start: int,
    end: int,
    state: MeasuredState,
) -> None:
    """Track in-car adjustment changes beyond brake bias and TC1."""
    adj_channels = {
        "dcAntiRollFront": "arb_front_adjustments",
        "dcAntiRollRear": "arb_rear_adjustments",
        "dcTractionControl2": "tc2_adjustments",
        "dcABS": "abs_adjustments",
        "dcMGUKDeployMode": "deploy_mode_adjustments",
    }
    for ch_name, attr_name in adj_channels.items():
        if ibt.has_channel(ch_name):
            ch_data = ibt.channel(ch_name)[start:end + 1]
            changes = int(np.sum(np.abs(np.diff(ch_data)) > 0.001))
            setattr(state, attr_name, changes)


def _extract_wind(
    ibt: IBTFile,
    state: MeasuredState,
) -> None:
    """Extract wind conditions (affects aero balance at high speed)."""
    if ibt.has_channel("WindVel"):
        wind_vel = ibt.channel("WindVel")
        if wind_vel is not None and len(wind_vel) > 0:
            state.wind_speed_ms = round(float(np.mean(wind_vel)), 2)

    if ibt.has_channel("WindDir"):
        wind_dir = ibt.channel("WindDir")
        if wind_dir is not None and len(wind_dir) > 0:
            state.wind_dir_deg = round(float(np.mean(np.degrees(wind_dir))), 1)
