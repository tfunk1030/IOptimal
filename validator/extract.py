"""Extract measured telemetry quantities for comparison against solver predictions.

Reuses track_model.ibt_parser for channel access and track_model.build_profile
for the full profile rebuild. Adds targeted extractions for validation:
- Ride heights at speed (aero compression validation)
- Ride height excursion p99 (heave model validation)
- Natural frequency via FFT (corner spring validation)
- Settle time after bump events (damper validation)
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
    """All telemetry-derived quantities for solver validation.

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
    # NOTE: These are RAW sensor values, NOT aero-model coordinates.
    # For comparison, use excursion and compression (offset-independent).
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
    # Static ride heights from IBT session setup (sensor coords)
    static_front_rh_sensor_mm: float = 0.0
    static_rear_rh_sensor_mm: float = 0.0

    # --- Step 2: Platform stability ---
    front_shock_vel_p99_mps: float = 0.0
    rear_shock_vel_p99_mps: float = 0.0
    front_rh_excursion_measured_mm: float = 0.0
    rear_rh_excursion_measured_mm: float = 0.0

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

    # --- Handling dynamics ---
    # Understeer angle: positive = understeer, negative = oversteer
    # Computed from: steer_wheel/ratio - wheelbase * yaw_rate / speed
    understeer_mean_deg: float = 0.0          # Mean during cornering >1.0g
    understeer_low_speed_deg: float = 0.0     # Mean at <120 kph corners
    understeer_high_speed_deg: float = 0.0    # Mean at >180 kph corners
    # Body slip angle (rear axle attitude)
    body_slip_p95_deg: float = 0.0            # P95 |atan2(Vy, Vx)|
    body_slip_at_peak_g_deg: float = 0.0      # At peak lateral g
    # Wheel slip ratios (driven - ground) / ground
    rear_slip_ratio_p95: float = 0.0          # P95 rear traction slip
    front_slip_ratio_p95: float = 0.0         # P95 front braking slip
    # Yaw rate correlation: how well yaw rate tracks expected from steering
    yaw_rate_correlation: float = 0.0         # R^2 of actual vs expected yaw
    # Weight transfer rate (roll rate and pitch rate)
    roll_rate_p95_deg_per_s: float = 0.0
    pitch_rate_p95_deg_per_s: float = 0.0

    # --- Tyre thermal analysis ---
    # Temperature spread: inner - outer (positive = more inner heat = too much camber)
    front_temp_spread_lf_c: float = 0.0       # LF inner - outer surface temp
    front_temp_spread_rf_c: float = 0.0       # RF inner - outer
    rear_temp_spread_lr_c: float = 0.0        # LR inner - outer
    rear_temp_spread_rr_c: float = 0.0        # RR inner - outer
    # Carcass temperature (thermal window indicator)
    front_carcass_mean_c: float = 0.0         # Mean front carcass temp
    rear_carcass_mean_c: float = 0.0          # Mean rear carcass temp
    # Tyre pressure at operating temp
    front_pressure_mean_kpa: float = 0.0
    rear_pressure_mean_kpa: float = 0.0
    # Wear (remaining tread % — averaged across surface)
    front_wear_mean_pct: float = 0.0
    rear_wear_mean_pct: float = 0.0

    # --- Full rebuilt track profile ---
    measured_track_profile: TrackProfile | None = None

    # --- Session metadata ---
    lap_time_s: float = 0.0
    lap_number: int = 0
    speed_mean_kph: float = 0.0
    speed_max_kph: float = 0.0
    # Mean speed during the at_speed window (>150 kph, no braking)
    # Used to V^2-scale aero compression to the solver's reference speed
    mean_speed_at_speed_kph: float = 0.0

    # --- Roll shock deflection (FROLLshockDefl / RROLLshockDefl) ---
    front_roll_shock_defl_mean_mm: float = 0.0
    front_roll_shock_defl_p99_mm: float = 0.0
    rear_roll_shock_defl_mean_mm: float = 0.0
    rear_roll_shock_defl_p99_mm: float = 0.0

    # --- Direct downforce (DownforceFront / DownforceRear) ---
    downforce_front_n: float = 0.0
    downforce_rear_n: float = 0.0
    downforce_total_n: float = 0.0
    downforce_balance_pct: float = 0.0

    # --- BrakeABSactive direct channel flag ---
    brake_abs_channel_present: bool = False

    def __getattr__(self, name: str):
        """Provide ``_clean`` suffixed aliases for bottoming event fields.

        Several solver modules (decision_trace, candidate_search, constraints)
        reference ``bottoming_event_count_front_clean`` / ``..._rear_clean``
        which are identical to the base fields for now (kerb filtering is
        not yet implemented).  This fallback prevents silent ``None`` returns
        that caused rear bottoming events to be ignored entirely.
        """
        if name.endswith("_clean"):
            base = name[: -len("_clean")]
            # Only proxy known bottoming fields to avoid masking real errors
            if base in ("bottoming_event_count_front", "bottoming_event_count_rear"):
                return getattr(self, base)
        raise AttributeError(f"'MeasuredState' has no attribute {name!r}")


def extract_measurements(
    ibt_path: str | Path,
    car: CarModel,
    solver_json: dict,
    lap: int | None = None,
) -> MeasuredState:
    """Extract all validation-relevant measurements from an IBT session.

    Args:
        ibt_path: Path to .ibt or .zip file
        car: Car model for thresholds (vortex burst, etc.)
        solver_json: Loaded solver output JSON (from --save)
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
        best = ibt.best_lap_indices(min_time=60.0)
        if best is None:
            raise ValueError("No valid laps found in IBT file")
        start, end = best
        lap_time_ch = ibt.channel("LapCurrentLapTime")
        state.lap_time_s = float(lap_time_ch[end]) if lap_time_ch is not None else 0.0
        # Find which lap number this is
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
    state.peak_lat_g_measured = float(np.percentile(np.abs(lat_g), 99.9))

    # Shock velocities — synthesise from heave+roll if corner channels missing
    n_samples = end - start + 1
    has_corner_shocks = all(
        ibt.has_channel(c) for c in ["LFshockVel", "RFshockVel", "LRshockVel", "RRshockVel"]
    )
    if has_corner_shocks:
        lf_sv = np.abs(ibt.channel("LFshockVel")[start:end + 1])
        rf_sv = np.abs(ibt.channel("RFshockVel")[start:end + 1])
        lr_sv = np.abs(ibt.channel("LRshockVel")[start:end + 1])
        rr_sv = np.abs(ibt.channel("RRshockVel")[start:end + 1])
    else:
        hf = ibt.channel("HFshockVel")[start:end + 1] if ibt.has_channel("HFshockVel") else np.zeros(n_samples)
        tr = ibt.channel("TRshockVel")[start:end + 1] if ibt.has_channel("TRshockVel") else np.zeros(n_samples)
        froll = ibt.channel("FROLLshockVel")[start:end + 1] if ibt.has_channel("FROLLshockVel") else np.zeros(n_samples)
        rroll = ibt.channel("RROLLshockVel")[start:end + 1] if ibt.has_channel("RROLLshockVel") else np.zeros(n_samples)
        lf_sv = np.abs(hf + froll)
        rf_sv = np.abs(hf - froll)
        lr_sv = np.abs(tr + rroll)
        rr_sv = np.abs(tr - rroll)

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

        # Aero compression: use IBT static RH from pit/low-speed samples
        # This is offset-independent because both static and dynamic are in
        # the same sensor reference frame: compression = static - dynamic.
        #
        # Extract static RH from the first few seconds (pit lane, ~0 speed)
        pit_mask = speed_kph < 5.0
        if np.sum(pit_mask) > 20:
            state.static_front_rh_sensor_mm = float(np.mean(front_rh[pit_mask]))
            state.static_rear_rh_sensor_mm = float(np.mean(rear_rh[pit_mask]))
        else:
            # Fallback: use the max RH values (proxy for static at low speed)
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
        # (offset-independent — uses the distribution, not absolute values)
        front_mean_all = float(np.mean(front_rh))
        front_std_all = float(np.std(front_rh))
        rear_mean_all = float(np.mean(rear_rh))
        rear_std_all = float(np.std(rear_rh))

        # Bottoming = extreme low outliers (below mean - 3*sigma)
        front_bottom_thresh = front_mean_all - 3.0 * front_std_all
        rear_bottom_thresh = rear_mean_all - 3.0 * rear_std_all
        state.bottoming_event_count_front = int(np.sum(front_rh < front_bottom_thresh))
        state.bottoming_event_count_rear = int(np.sum(rear_rh < rear_bottom_thresh))

        # Vortex burst: front RH dropping below p01 (extreme low events)
        # In sensor coordinates, we detect this from the excursion magnitude
        vb_excursion_threshold = 3.5 * front_std_all  # >3.5 sigma events
        if np.sum(at_speed) > 50:
            front_at_speed = front_rh[at_speed]
            front_mean_speed = float(np.mean(front_at_speed))
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
        abs_lat_g = np.abs(lat_g)
        corner_mask = abs_lat_g > 1.0
        if np.sum(corner_mask) > 100:
            front_deflection = np.abs(lf_rh[corner_mask] - rf_rh[corner_mask])
            rear_deflection = np.abs(lr_rh[corner_mask] - rr_rh[corner_mask])
            mean_front_defl = float(np.mean(front_deflection))
            mean_rear_defl = float(np.mean(rear_deflection))
            total_defl = mean_front_defl + mean_rear_defl
            if total_defl > 0.1:
                state.lltd_measured = mean_front_defl / total_defl

        # --- Body roll ---
        if ibt.has_channel("Roll"):
            all_roll_deg = np.degrees(ibt.channel("Roll")[start:end + 1])
            abs_roll = np.abs(all_roll_deg)

            # Roll gradient: p95_roll / p95_lat_g
            abs_lat_full = np.abs(lat_g)
            p95_lat = float(np.percentile(abs_lat_full, 95))
            p95_roll = float(np.percentile(abs_roll, 95))
            if p95_lat > 0.5 and p95_roll > 0.1:
                state.roll_gradient_measured_deg_per_g = p95_roll / p95_lat

            # Body roll at peak lateral g
            if state.peak_lat_g_measured > 1.0:
                state.body_roll_at_peak_g_deg = float(
                    state.roll_gradient_measured_deg_per_g * state.peak_lat_g_measured
                )
        else:
            # Derive from ride height differential
            if has_rh:
                track_w_m = 1.6  # approximate front track width
                roll_from_rh = np.degrees(np.arctan((lf_rh - rf_rh) / (track_w_m * 1000)))
                abs_roll_rh = np.abs(roll_from_rh)
                abs_lat_full = np.abs(lat_g)
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
        state.front_rh_settle_time_ms = _settle_time(
            front_rh, front_sv[:n], ibt.tick_rate,
        )
        state.rear_rh_settle_time_ms = _settle_time(
            rear_rh, rear_sv[:n] if len(rear_sv) >= n else np.concatenate([lr_sv, rr_sv])[:n],
            ibt.tick_rate,
        )

    # --- Handling dynamics ---
    _extract_handling(ibt, start, end, speed_ms, speed_kph, lat_g, car, state)

    # --- Tyre thermal / wear / pressure ---
    _extract_tyre_data(ibt, start, end, speed_kph, state)

    # --- Roll shock deflection (FROLLshockDefl / RROLLshockDefl) ---
    at_speed_150 = speed_kph > 150
    if np.sum(at_speed_150) > 30:
        if ibt.has_channel("FROLLshockDefl"):
            froll = ibt.channel("FROLLshockDefl")[start:end + 1]
            state.front_roll_shock_defl_mean_mm = round(float(np.mean(froll[at_speed_150])) * 1000, 2)
            state.front_roll_shock_defl_p99_mm = round(float(np.percentile(np.abs(froll[at_speed_150]), 99)) * 1000, 2)
        if ibt.has_channel("RROLLshockDefl"):
            rroll = ibt.channel("RROLLshockDefl")[start:end + 1]
            state.rear_roll_shock_defl_mean_mm = round(float(np.mean(rroll[at_speed_150])) * 1000, 2)
            state.rear_roll_shock_defl_p99_mm = round(float(np.percentile(np.abs(rroll[at_speed_150]), 99)) * 1000, 2)

    # --- Direct downforce (DownforceFront / DownforceRear) ---
    if np.sum(at_speed_150) > 30:
        if ibt.has_channel("DownforceFront"):
            df_f = ibt.channel("DownforceFront")[start:end + 1]
            state.downforce_front_n = round(float(np.mean(df_f[at_speed_150])), 1)
        if ibt.has_channel("DownforceRear"):
            df_r = ibt.channel("DownforceRear")[start:end + 1]
            state.downforce_rear_n = round(float(np.mean(df_r[at_speed_150])), 1)
        if state.downforce_front_n > 0 and state.downforce_rear_n > 0:
            state.downforce_total_n = round(state.downforce_front_n + state.downforce_rear_n, 1)
            total = state.downforce_front_n + state.downforce_rear_n
            if total > 10.0:
                state.downforce_balance_pct = round(state.downforce_front_n / total * 100, 1)

    # --- BrakeABSactive direct channel flag ---
    state.brake_abs_channel_present = ibt.has_channel("BrakeABSactive")

    # --- Rebuild full track profile (for next-iteration use) ---
    # Use build_profile for the full extraction; it uses best lap by default
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
    # UA = (steer_wheel / ratio) - (wheelbase * yaw_rate / speed)
    # Positive UA = understeer, Negative = oversteer
    # Only valid when speed > some minimum (avoid divide-by-zero noise)
    ratio = car.steering_ratio
    wb = car.wheelbase_m
    safe_speed = np.maximum(speed_ms, 5.0)  # clamp to avoid noise at low speed

    road_wheel_angle = steer / ratio  # rad
    neutral_yaw_rate = road_wheel_angle * safe_speed / wb  # what yaw_rate would be with no slip
    # Alternatively: understeer_angle = road_wheel_angle - wb * yaw_rate / speed
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

    # --- Body slip angle ---
    # atan2(Vy, Vx) gives the car's attitude angle
    body_slip_deg = np.degrees(np.arctan2(vy, np.maximum(np.abs(vx), 1.0)))
    abs_body_slip = np.abs(body_slip_deg)

    at_speed_mask = speed_kph > 60
    if np.sum(at_speed_mask) > 100:
        state.body_slip_p95_deg = float(np.percentile(abs_body_slip[at_speed_mask], 95))

    # Body slip at peak lateral g (highest grip moment)
    if state.peak_lat_g_measured > 1.0:
        peak_mask = np.abs(lat_g) > (state.peak_lat_g_measured * 0.9)
        if np.sum(peak_mask) > 10:
            state.body_slip_at_peak_g_deg = float(np.mean(abs_body_slip[peak_mask]))

    # --- Wheel slip ratios ---
    # Slip ratio = (wheel_speed - ground_speed) / ground_speed
    # Positive = wheel spinning faster (traction limited)
    # Negative = wheel spinning slower (braking/locked)
    if all(ibt.has_channel(c) for c in ["LFspeed", "RFspeed", "LRspeed", "RRspeed"]):
        lf_ws = ibt.channel("LFspeed")[start:end + 1]
        rf_ws = ibt.channel("RFspeed")[start:end + 1]
        lr_ws = ibt.channel("LRspeed")[start:end + 1]
        rr_ws = ibt.channel("RRspeed")[start:end + 1]

        safe_car_speed = np.maximum(speed_ms, 2.0)

        # Rear slip (traction): average of both rear wheels
        rear_avg_ws = (lr_ws + rr_ws) / 2.0
        rear_slip = (rear_avg_ws - safe_car_speed) / safe_car_speed

        # Front slip (braking): average of both front wheels
        front_avg_ws = (lf_ws + rf_ws) / 2.0
        front_slip = (front_avg_ws - safe_car_speed) / safe_car_speed

        driving_mask = speed_kph > 60
        if np.sum(driving_mask) > 100:
            state.rear_slip_ratio_p95 = float(np.percentile(np.abs(rear_slip[driving_mask]), 95))
            state.front_slip_ratio_p95 = float(np.percentile(np.abs(front_slip[driving_mask]), 95))

    # --- Yaw rate correlation ---
    # How well does the actual yaw rate follow the expected (neutral steer) yaw?
    # R^2 of actual vs expected. Low R^2 = inconsistent response (possible instability).
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

    # At-speed mask for stable tyre readings (tyres conditioned, not in pits)
    at_speed = speed_kph > 60

    if np.sum(at_speed) < 100:
        return

    # --- Temperature spread (inner - outer surface temp) ---
    # Positive spread = inner hotter = too much negative camber
    # Negative spread = outer hotter = not enough camber
    # For LEFT side: tempL = outer, tempR = inner (from driver perspective)
    # For RIGHT side: tempL = inner, tempR = outer
    temp_channels = {
        "LF": ("LFtempL", "LFtempR"),  # LF: tempL=outer, tempR=inner
        "RF": ("RFtempL", "RFtempR"),  # RF: tempL=inner, tempR=outer
        "LR": ("LRtempL", "LRtempR"),
        "RR": ("RRtempL", "RRtempR"),
    }

    for corner, (ch_l, ch_r) in temp_channels.items():
        if ibt.has_channel(ch_l) and ibt.has_channel(ch_r):
            temp_l = ibt.channel(ch_l)[start:end + 1]
            temp_r = ibt.channel(ch_r)[start:end + 1]

            # Determine inner vs outer based on side
            # Left side: L=outer, R=inner
            # Right side: L=inner, R=outer
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

    # --- Carcass temperature (core heat indicator) ---
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

    # --- Tyre pressure ---
    for prefix, attr in [("LF", "front"), ("RF", "front"), ("LR", "rear"), ("RR", "rear")]:
        ch = f"{prefix}pressure"
        if ibt.has_channel(ch):
            pressure = ibt.channel(ch)[start:end + 1]
            mean_p = float(np.mean(pressure[at_speed]))
            if attr == "front":
                if state.front_pressure_mean_kpa == 0:
                    state.front_pressure_mean_kpa = round(mean_p, 1)
                else:
                    state.front_pressure_mean_kpa = round(
                        (state.front_pressure_mean_kpa + mean_p) / 2.0, 1)
            else:
                if state.rear_pressure_mean_kpa == 0:
                    state.rear_pressure_mean_kpa = round(mean_p, 1)
                else:
                    state.rear_pressure_mean_kpa = round(
                        (state.rear_pressure_mean_kpa + mean_p) / 2.0, 1)

    # --- Tyre wear (end-of-lap snapshot) ---
    # Use the last sample of the lap for wear
    for prefix, attr in [("LF", "front"), ("RF", "front"), ("LR", "rear"), ("RR", "rear")]:
        wear_channels = [f"{prefix}wearL", f"{prefix}wearM", f"{prefix}wearR"]
        wear_vals = []
        for ch in wear_channels:
            if ibt.has_channel(ch):
                wear_vals.append(float(ibt.channel(ch)[end]))
        if wear_vals:
            avg_wear = np.mean(wear_vals) * 100  # Convert 0-1 to percentage
            if attr == "front":
                if state.front_wear_mean_pct == 0:
                    state.front_wear_mean_pct = round(avg_wear, 1)
                else:
                    state.front_wear_mean_pct = round(
                        (state.front_wear_mean_pct + avg_wear) / 2.0, 1)
            else:
                if state.rear_wear_mean_pct == 0:
                    state.rear_wear_mean_pct = round(avg_wear, 1)
                else:
                    state.rear_wear_mean_pct = round(
                        (state.rear_wear_mean_pct + avg_wear) / 2.0, 1)


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
    min_segment_samples: int = 120,  # 2 seconds at 60Hz
) -> float:
    """Find dominant ride height oscillation frequency via FFT.

    Analyzes clean straight segments (high speed, no braking) where the
    ride height oscillation reflects the natural frequency of the suspension.

    Returns dominant frequency in Hz, or 0.0 if insufficient data.
    """
    # Find clean straight segments
    clean = (speed_kph > min_speed_kph) & (brake < 0.05)

    # Find contiguous segments
    edges = np.diff(clean.astype(int))
    starts = np.where(edges == 1)[0] + 1
    ends = np.where(edges == -1)[0] + 1

    if clean[0]:
        starts = np.insert(starts, 0, 0)
    if clean[-1]:
        ends = np.append(ends, len(clean))

    # Collect power spectra from all valid segments
    all_power = None
    freq_axis = None

    n_segs = min(len(starts), len(ends))
    for i in range(n_segs):
        s, e = starts[i], ends[i]
        seg_len = e - s
        if seg_len < min_segment_samples:
            continue

        segment = rh_signal[s:e]
        # Remove DC component (mean)
        segment = segment - np.mean(segment)
        # Apply Hanning window
        window = np.hanning(seg_len)
        windowed = segment * window

        # FFT
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

    # Search in 0.5-10 Hz band (suspension natural frequencies)
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

    # Use the shorter of the two arrays
    n = min(len(rh_signal), len(shock_vel))
    rh_signal = rh_signal[:n]
    shock_vel = shock_vel[:n]

    p95 = float(np.percentile(shock_vel, 95))
    if p95 < 0.01:
        return 0.0

    sigma = float(np.std(rh_signal))
    if sigma < 0.1:
        return 0.0

    # Running mean with 0.5s window
    window = min(int(tick_rate * 0.5), n // 4)
    if window < 5:
        return 0.0
    kernel = np.ones(window) / window
    running_mean = np.convolve(rh_signal, kernel, mode="same")

    # Find bump events (shock vel exceeding p95)
    bump_mask = shock_vel > p95
    edges = np.diff(bump_mask.astype(int))
    bump_starts = np.where(edges == 1)[0] + 1

    max_search_samples = int(max_search_ms / 1000.0 * tick_rate)
    settle_times = []

    for bs in bump_starts:
        # Search forward for when RH returns within 1-sigma of running mean
        search_end = min(bs + max_search_samples, n)
        for j in range(bs, search_end):
            if abs(rh_signal[j] - running_mean[j]) < sigma:
                settle_times.append((j - bs) / tick_rate * 1000.0)  # ms
                break

    if not settle_times:
        return 0.0

    return round(float(np.median(settle_times)), 1)
