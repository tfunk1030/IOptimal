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

from analyzer.telemetry_truth import (
    TelemetrySignal,
    build_signal_map,
    build_telemetry_bundle,
)
from track_model.ibt_parser import IBTFile
from track_model.build_profile import build_profile
from track_model.profile import TrackProfile, build_kerb_spatial_mask
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
    mean_front_rh_at_speed_mm: float | None = None
    mean_rear_rh_at_speed_mm: float | None = None
    front_rh_std_mm: float | None = None
    rear_rh_std_mm: float | None = None
    aero_compression_front_mm: float | None = None
    aero_compression_rear_mm: float | None = None
    bottoming_event_count_front: int = 0
    bottoming_event_count_rear: int = 0
    bottoming_event_count_front_clean: int = 0
    bottoming_event_count_rear_clean: int = 0
    bottoming_event_count_front_kerb: int = 0
    bottoming_event_count_rear_kerb: int = 0
    vortex_burst_event_count: int = 0
    front_rh_p01_mm: float | None = None
    rear_rh_p01_mm: float | None = None
    static_front_rh_sensor_mm: float | None = None
    static_rear_rh_sensor_mm: float | None = None

    # --- Step 2: Platform stability ---
    front_shock_vel_p99_mps: float | None = None
    rear_shock_vel_p99_mps: float | None = None
    front_rh_excursion_measured_mm: float | None = None
    rear_rh_excursion_measured_mm: float | None = None

    # --- Heave/shock deflection (spring travel) ---
    front_heave_defl_mean_mm: float | None = None       # Mean HFshockDefl at speed
    front_heave_defl_p99_mm: float | None = None        # p99 peak compression
    front_heave_defl_max_mm: float | None = None        # Maximum observed compression
    front_heave_defl_std_mm: float | None = None        # Variance of heave deflection
    rear_heave_defl_mean_mm: float | None = None        # Mean HRshockDefl at speed
    rear_heave_defl_p99_mm: float | None = None
    rear_heave_defl_max_mm: float | None = None
    rear_heave_defl_std_mm: float | None = None
    front_heave_travel_used_pct: float | None = None    # p99 defl / DeflMax * 100
    rear_heave_travel_used_pct: float | None = None
    heave_bottoming_events_front: int = 0       # Direct spring travel exhaustion
    heave_bottoming_events_rear: int = 0
    # Braking-specific heave analysis (detects entry rotation → mid-corner push)
    front_heave_defl_braking_p99_mm: float | None = None
    front_heave_travel_used_braking_pct: float | None = None

    # --- Step 3: Spring response ---
    front_dominant_freq_hz: float | None = None
    rear_dominant_freq_hz: float | None = None

    # --- Step 4: Balance ---
    lltd_measured: float | None = None              # Backward-compatible alias of roll_distribution_proxy
    roll_distribution_proxy: float | None = None    # RH-based proxy, not true LLTD
    roll_gradient_measured_deg_per_g: float | None = None
    body_roll_at_peak_g_deg: float | None = None
    peak_lat_g_measured: float | None = None

    # --- Step 6: Dampers ---
    front_shock_vel_p95_mps: float | None = None
    rear_shock_vel_p95_mps: float | None = None
    front_rh_settle_time_ms: float = 0.0
    rear_rh_settle_time_ms: float = 0.0

    # --- Body roll p95 ---
    body_roll_p95_deg: float | None = None

    # --- Handling dynamics ---
    understeer_mean_deg: float | None = None
    understeer_low_speed_deg: float | None = None
    understeer_high_speed_deg: float | None = None
    body_slip_p95_deg: float | None = None
    body_slip_at_peak_g_deg: float | None = None
    rear_slip_ratio_p95: float = 0.0        # Backward-compatible alias of rear_power_slip_ratio_p95
    front_slip_ratio_p95: float = 0.0       # Backward-compatible alias of front_braking_lock_ratio_p95
    rear_power_slip_ratio_p95: float = 0.0
    front_braking_lock_ratio_p95: float = 0.0
    front_brake_wheel_decel_asymmetry_p95_ms2: float = 0.0
    yaw_rate_correlation: float | None = None
    roll_rate_p95_deg_per_s: float | None = None
    pitch_rate_p95_deg_per_s: float | None = None

    # --- Tyre thermal analysis ---
    front_temp_spread_lf_c: float | None = None
    front_temp_spread_rf_c: float | None = None
    rear_temp_spread_lr_c: float | None = None
    rear_temp_spread_rr_c: float | None = None
    front_carcass_mean_c: float | None = None
    rear_carcass_mean_c: float | None = None
    front_pressure_mean_kpa: float | None = None
    rear_pressure_mean_kpa: float | None = None
    front_wear_mean_pct: float = 0.0
    rear_wear_mean_pct: float = 0.0

    # --- Splitter ride height (CFSRrideHeight) ---
    splitter_rh_mean_at_speed_mm: float | None = None   # Mean center-front splitter RH at >150kph
    splitter_rh_min_mm: float | None = None              # Minimum observed (splitter scrape proximity)
    splitter_rh_p01_mm: float | None = None              # 1st percentile (near-worst case)
    splitter_rh_std_mm: float | None = None              # Variance at speed
    splitter_scrape_events: int = 0              # Samples where splitter RH < 2mm

    # --- Corner shock deflections (LF/RF/LR/RRshockDefl) ---
    front_corner_defl_p99_mm: float | None = None        # p99 corner shock deflection (avg LF+RF)
    rear_corner_defl_p99_mm: float | None = None         # p99 corner shock deflection (avg LR+RR)
    front_corner_defl_max_mm: float | None = None
    rear_corner_defl_max_mm: float | None = None

    # --- Heave shock velocities (HFshockVel, HRshockVel) ---
    front_heave_vel_p95_mps: float | None = None         # Front heave damper velocity p95
    front_heave_vel_p99_mps: float | None = None
    rear_heave_vel_p95_mps: float | None = None
    rear_heave_vel_p99_mps: float | None = None
    front_heave_vel_ls_pct: float = 0.0          # % of samples in LS regime (<25 mm/s)
    front_heave_vel_hs_pct: float = 0.0          # % of samples in HS regime (>100 mm/s)

    # --- Brake system ---
    measured_brake_bias_pct: float | None = None         # Backward-compatible alias of hydraulic_brake_split_pct
    hydraulic_brake_split_pct: float | None = None       # Front hydraulic pressure share, not brake torque split
    hydraulic_brake_split_confidence: float | None = None
    abs_active_pct: float = 0.0                  # % of braking time ABS is active
    abs_cut_mean_pct: float = 0.0                # Mean ABS force reduction during engagement
    front_brake_pressure_peak_bar: float | None = None
    rear_brake_pressure_peak_bar: float | None = None
    braking_decel_mean_g: float | None = None
    braking_decel_peak_g: float | None = None

    # --- In-car adjustment tracking ---
    brake_bias_adjustments: int = 0              # Number of bias changes during session
    tc_adjustments: int = 0                      # Number of TC changes during session
    brake_bias_range: tuple[float, float] = (0.0, 0.0)  # Min/max bias values used
    live_brake_bias_pct: float | None = None
    live_tc_gain: int | None = None
    live_tc_slip: int | None = None
    live_front_arb_blade: int | None = None
    live_rear_arb_blade: int | None = None

    # --- Fuel and weight ---
    fuel_level_at_measurement_l: float | None = None     # Fuel level during analyzed lap
    fuel_used_per_lap_l: float | None = None

    # --- Hybrid/ERS ---
    ers_battery_mean_pct: float = 0.0            # Mean battery charge during lap
    ers_battery_min_pct: float = 0.0             # Minimum (depleted = less rear torque)
    mguk_torque_peak_nm: float = 0.0             # Peak hybrid torque contribution

    # --- Environmental ---
    air_temp_c: float | None = None
    track_temp_c: float | None = None
    air_density_kg_m3: float | None = None

    # --- RPM ---
    rpm_at_braking_pct_at_limiter: float = 0.0   # % of braking events hitting rev limiter

    # --- Speed-dependent LLTD ---
    lltd_low_speed: float | None = None                  # Backward-compatible alias of roll_distribution_proxy_low_speed
    lltd_high_speed: float | None = None                 # Backward-compatible alias of roll_distribution_proxy_high_speed
    roll_distribution_proxy_low_speed: float | None = None
    roll_distribution_proxy_high_speed: float | None = None

    # --- Directional understeer (left/right split) ---
    understeer_left_turn_deg: float | None = None
    understeer_right_turn_deg: float | None = None

    # --- Per-corner shock velocities (loaded vs unloaded) ---
    lf_shock_vel_p95_mps: float | None = None
    rf_shock_vel_p95_mps: float | None = None
    lr_shock_vel_p95_mps: float | None = None
    rr_shock_vel_p95_mps: float | None = None

    # --- Carcass temperature gradient (inner-outer, for deep camber validation) ---
    front_carcass_gradient_lf_c: float | None = None     # LF carcass inner-outer spread
    front_carcass_gradient_rf_c: float | None = None
    rear_carcass_gradient_lr_c: float | None = None
    rear_carcass_gradient_rr_c: float | None = None

    # --- Per-corner tyre data (preserves left-right split) ---
    lf_pressure_kpa: float | None = None
    rf_pressure_kpa: float | None = None
    lr_pressure_kpa: float | None = None
    rr_pressure_kpa: float | None = None
    lf_cold_pressure_kpa: float | None = None
    rf_cold_pressure_kpa: float | None = None
    lr_cold_pressure_kpa: float | None = None
    rr_cold_pressure_kpa: float | None = None
    lf_wear_pct: float = 0.0
    rf_wear_pct: float = 0.0
    lr_wear_pct: float = 0.0
    rr_wear_pct: float = 0.0
    lf_temp_inner_c: float | None = None   # Inner surface temp at speed
    rf_temp_inner_c: float | None = None
    lr_temp_inner_c: float | None = None
    rr_temp_inner_c: float | None = None
    lf_temp_middle_c: float | None = None  # Middle surface temp at speed
    rf_temp_middle_c: float | None = None
    lr_temp_middle_c: float | None = None
    rr_temp_middle_c: float | None = None
    lf_temp_outer_c: float | None = None   # Outer surface temp at speed
    rf_temp_outer_c: float | None = None
    lr_temp_outer_c: float | None = None
    rr_temp_outer_c: float | None = None

    # --- Raw driver inputs (before TC/ABS intervention) ---
    throttle_raw_mean: float = 0.0          # Mean ThrottleRaw at speed
    tc_intervention_pct: float = 0.0        # % of time TC is cutting throttle
    brake_raw_peak: float = 0.0             # Peak BrakeRaw value

    # --- Gear data ---
    gear_at_apex_mode: int = 0              # Most common gear at corner apexes
    max_gear: int = 0                       # Highest gear used on track

    # --- Pitch dynamics ---
    pitch_mean_at_speed_deg: float | None = None    # Mean pitch angle at speed (rake indicator)
    pitch_range_deg: float | None = None            # p99-p01 pitch range (platform stability)
    pitch_mean_braking_deg: float | None = None
    pitch_range_braking_deg: float | None = None

    # --- In-car adjustment tracking (extended) ---
    arb_front_adjustments: int = 0          # dcAntiRollFront changes
    arb_rear_adjustments: int = 0           # dcAntiRollRear changes
    tc2_adjustments: int = 0                # dcTractionControl2 changes
    abs_adjustments: int = 0                # dcABS changes
    deploy_mode_adjustments: int = 0        # dcMGUKDeployMode changes

    # --- Rear shock oscillation analysis (P2: damper validation) ---
    rear_shock_oscillation_hz: float | None = None   # Zero-crossing frequency of rear shock vel
    front_shock_oscillation_hz: float | None = None  # Zero-crossing frequency of front shock vel

    # --- High-speed m_eff filtering (P3c) ---
    front_heave_vel_p95_hs_mps: float | None = None  # Front heave vel p95 at >200 kph only
    front_rh_std_hs_mm: float | None = None          # Front RH std at >200 kph only

    # --- Wind ---
    wind_speed_ms: float | None = None
    wind_dir_deg: float | None = None

    # --- Full rebuilt track profile ---
    measured_track_profile: TrackProfile | None = None

    # --- Session metadata ---
    lap_time_s: float | None = None
    lap_number: int = 0
    speed_mean_kph: float | None = None
    speed_max_kph: float | None = None
    mean_speed_at_speed_kph: float | None = None
    metric_fallbacks: list[str] = field(default_factory=list)
    fallback_reasons: list[str] = field(default_factory=list)

    # --- Telemetry truth metadata ---
    front_settle_total_events: int = 0
    rear_settle_total_events: int = 0
    front_settle_valid_clean_events: int = 0
    rear_settle_valid_clean_events: int = 0
    front_settle_invalid_reason: str = ""
    rear_settle_invalid_reason: str = ""
    extraction_attempts: list[dict[str, object]] = field(default_factory=list, repr=False)
    signal_conflicts: list[str] = field(default_factory=list)
    telemetry_signals: dict[str, TelemetrySignal[float]] = field(default_factory=dict, repr=False)
    telemetry_bundle: dict[str, object] = field(default_factory=dict, repr=False)


def extract_measurements(
    ibt_path: str | Path,
    car: CarModel,
    lap: int | None = None,
    min_lap_time: float = 108.0,
    outlier_pct: float = 0.115,
    ibt: IBTFile | None = None,
) -> MeasuredState:
    """Extract all analysis-relevant measurements from an IBT session.

    Args:
        ibt_path: Path to .ibt or .zip file
        car: Car model for thresholds (vortex burst, etc.)
        lap: Specific lap number to analyze (None = best lap)
        ibt: Optional pre-opened IBTFile to avoid re-parsing the file.

    Returns:
        MeasuredState with all measured quantities
    """
    if ibt is None:
        ibt = IBTFile(ibt_path)
    state = MeasuredState()
    state.extraction_attempts.append(
        {"phase": "primary_channels", "source": "ibt_channels", "status": "ok"}
    )

    try:
        state.measured_track_profile = build_profile(ibt_path)
        state.extraction_attempts.append(
            {"phase": "track_profile", "source": "build_profile", "status": "ok"}
        )
    except Exception as exc:
        state.measured_track_profile = None
        state.extraction_attempts.append(
            {"phase": "track_profile", "source": "build_profile", "status": f"failed: {exc}"}
        )
        state.fallback_reasons.append("track_profile_unavailable")

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
    lap_dist = ibt.channel("LapDist")[start:end + 1] if ibt.has_channel("LapDist") else np.zeros(n)
    kerb_spatial_mask = None
    if (
        state.measured_track_profile is not None
        and getattr(state.measured_track_profile, "kerb_events", None)
        and len(lap_dist) == n
    ):
        kerb_spatial_mask = build_kerb_spatial_mask(
            lap_dist,
            state.measured_track_profile.kerb_events,
        )

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

    # --- Shock oscillation frequency (P2: damper underdamping detection) ---
    # Zero-crossings of signed shock velocity → oscillation frequency proxy.
    # If zero_crossing_freq > 1.5× natural frequency → underdamped evidence.
    duration_s = n / ibt.tick_rate
    if duration_s > 1.0:
        # Use signed shock velocity (not abs) for zero-crossing detection
        lr_sv_signed = ibt.channel("LRshockVel")[start:end + 1]
        rr_sv_signed = ibt.channel("RRshockVel")[start:end + 1]
        rear_sv_signed = (lr_sv_signed + rr_sv_signed) / 2.0
        rear_zc = int(np.sum(np.diff(np.sign(rear_sv_signed)) != 0))
        state.rear_shock_oscillation_hz = round(rear_zc / 2.0 / duration_s, 2)

        lf_sv_signed = ibt.channel("LFshockVel")[start:end + 1]
        rf_sv_signed = ibt.channel("RFshockVel")[start:end + 1]
        front_sv_signed = (lf_sv_signed + rf_sv_signed) / 2.0
        front_zc = int(np.sum(np.diff(np.sign(front_sv_signed)) != 0))
        state.front_shock_oscillation_hz = round(front_zc / 2.0 / duration_s, 2)

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
        if ibt.has_channel("ThrottleRaw"):
            throttle_signal = ibt.channel("ThrottleRaw")[start:end + 1]
        elif ibt.has_channel("Throttle"):
            throttle_signal = ibt.channel("Throttle")[start:end + 1]
        else:
            throttle_signal = np.zeros(n)
        at_speed = (speed_kph > 150) & (brake < 0.05)

        # Kerb-filtered at-speed mask for ride height variance
        # Kerb strikes inflate RH variance but are driving choices, not setup problems.
        # Mean RH and p01 still use all samples (needed for aero operating point).
        if kerb_spatial_mask is not None and len(kerb_spatial_mask) == n:
            at_speed_clean = at_speed & ~kerb_spatial_mask
        else:
            at_speed_clean = at_speed

        if np.sum(at_speed) > 50:
            state.mean_front_rh_at_speed_mm = float(np.mean(front_rh[at_speed]))
            state.mean_rear_rh_at_speed_mm = float(np.mean(rear_rh[at_speed]))
            # Use kerb-filtered mask for variance (avoids over-stiffening from kerb spikes)
            clean_mask = at_speed_clean if np.sum(at_speed_clean) > 30 else at_speed
            state.front_rh_std_mm = float(np.std(front_rh[clean_mask]))
            state.rear_rh_std_mm = float(np.std(rear_rh[clean_mask]))
            state.front_rh_p01_mm = float(np.percentile(front_rh[at_speed], 1))
            state.rear_rh_p01_mm = float(np.percentile(rear_rh[at_speed], 1))
            state.mean_speed_at_speed_kph = float(np.mean(speed_kph[at_speed]))

        # High-speed aero regime: >200 kph (P3c: filtered m_eff)
        # These values exclude low-speed corners and pit exit, giving
        # more accurate m_eff calibration for the aero platform regime.
        hs_aero_mask = (speed_kph > 200) & (brake < 0.05)
        if np.sum(hs_aero_mask) > 30:
            state.front_rh_std_hs_mm = float(np.std(front_rh[hs_aero_mask]))
            if ibt.has_channel("HFshockVel"):
                hf_hs = np.abs(ibt.channel("HFshockVel")[start:end + 1])
                state.front_heave_vel_p95_hs_mps = round(
                    float(np.percentile(hf_hs[hs_aero_mask], 95)), 4
                )

        # Aero compression: static - dynamic (offset-independent)
        pit_mask = speed_kph < 5.0
        if np.sum(pit_mask) > 20:
            state.static_front_rh_sensor_mm = float(np.mean(front_rh[pit_mask]))
            state.static_rear_rh_sensor_mm = float(np.mean(rear_rh[pit_mask]))
        else:
            state.static_front_rh_sensor_mm = float(np.percentile(front_rh, 95))
            state.static_rear_rh_sensor_mm = float(np.percentile(rear_rh, 95))

        front_static_rh = state.static_front_rh_sensor_mm
        front_mean_rh = state.mean_front_rh_at_speed_mm
        rear_static_rh = state.static_rear_rh_sensor_mm
        rear_mean_rh = state.mean_rear_rh_at_speed_mm

        if (
            isinstance(front_static_rh, (int, float))
            and isinstance(front_mean_rh, (int, float))
            and front_static_rh > 0
            and front_mean_rh > 0
        ):
            state.aero_compression_front_mm = (
                front_static_rh - front_mean_rh
            )
        if (
            isinstance(rear_static_rh, (int, float))
            and isinstance(rear_mean_rh, (int, float))
            and rear_static_rh > 0
            and rear_mean_rh > 0
        ):
            state.aero_compression_rear_mm = (
                rear_static_rh - rear_mean_rh
            )

        # Bottoming events: samples where RH drops below 3-sigma from mean
        front_mean_all = float(np.mean(front_rh))
        front_std_all = float(np.std(front_rh))
        rear_mean_all = float(np.mean(rear_rh))
        rear_std_all = float(np.std(rear_rh))

        front_bottom_thresh = front_mean_all - 3.0 * front_std_all
        rear_bottom_thresh = rear_mean_all - 3.0 * rear_std_all
        front_bottoming = front_rh < front_bottom_thresh
        rear_bottoming = rear_rh < rear_bottom_thresh
        state.bottoming_event_count_front = int(np.sum(front_bottoming))
        state.bottoming_event_count_rear = int(np.sum(rear_bottoming))

        # Split bottoming into clean-track vs kerb using spatial mask
        # Build kerb mask from rumble channels or VertAccel fallback
        lap_dist_ch = ibt.channel("LapDist")[start:end + 1] if ibt.has_channel("LapDist") else None
        rumble_lf = ibt.channel("TireLF_RumblePitch")[start:end + 1] if ibt.has_channel("TireLF_RumblePitch") else None
        rumble_rf = ibt.channel("TireRF_RumblePitch")[start:end + 1] if ibt.has_channel("TireRF_RumblePitch") else None

        if lap_dist_ch is not None and (rumble_lf is not None or rumble_rf is not None):
            # Build kerb mask from rumble strips (same logic as build_profile)
            vert_accel = ibt.channel("VertAccel")[start:end + 1] / 9.81
            from track_model.build_profile import _find_kerb_events
            kerb_events_local, _ = _find_kerb_events(vert_accel, rumble_lf, rumble_rf, lap_dist_ch)
            kerb_spatial = build_kerb_spatial_mask(lap_dist_ch, kerb_events_local, buffer_m=30.0)

            state.bottoming_event_count_front_clean = int(np.sum(front_bottoming & ~kerb_spatial))
            state.bottoming_event_count_rear_clean = int(np.sum(rear_bottoming & ~kerb_spatial))
            state.bottoming_event_count_front_kerb = int(np.sum(front_bottoming & kerb_spatial))
            state.bottoming_event_count_rear_kerb = int(np.sum(rear_bottoming & kerb_spatial))
        else:
            # No kerb data available — treat all as clean-track
            state.bottoming_event_count_front_clean = state.bottoming_event_count_front
            state.bottoming_event_count_rear_clean = state.bottoming_event_count_rear

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

        # --- Roll stiffness distribution proxy from ride-height deflections ---
        # WARNING: This is NOT true LLTD (Lateral Load Transfer Distribution).
        # It measures ROLL STIFFNESS DISTRIBUTION: how much each axle contributes
        # to total roll resistance, approximated from ride height differential.
        # True LLTD = (K_roll_f/t_f) / (K_roll_f/t_f + K_roll_r/t_r) and also
        # includes geometric and direct components. This proxy correlates with
        # LLTD but is not identical — use the field name "roll_distribution_proxy"
        # and treat "lltd_measured" as a backward-compatible alias.
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
                state.roll_distribution_proxy = front_moment / total_moment
                state.lltd_measured = state.roll_distribution_proxy

        # --- Speed-dependent roll distribution proxy ---
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
                    state.roll_distribution_proxy_low_speed = f_mom_ls / total_ls
                    state.lltd_low_speed = state.roll_distribution_proxy_low_speed

            if np.sum(high_speed_corner) > 30:
                f_defl_hs = np.abs(lf_rh[high_speed_corner] - rf_rh[high_speed_corner])
                r_defl_hs = np.abs(lr_rh[high_speed_corner] - rr_rh[high_speed_corner])
                f_mom_hs = float(np.mean(f_defl_hs)) * tw_f_sq
                r_mom_hs = float(np.mean(r_defl_hs)) * tw_r_sq
                total_hs = f_mom_hs + r_mom_hs
                if total_hs > 0.1:
                    state.roll_distribution_proxy_high_speed = f_mom_hs / total_hs
                    state.lltd_high_speed = state.roll_distribution_proxy_high_speed

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
        front_settle = _settle_time_signal(
            front_rh,
            front_sv_avg,
            ibt.tick_rate,
            brake=brake,
            throttle=throttle_signal,
            kerb_mask=kerb_spatial_mask,
        )
        state.front_rh_settle_time_ms = (
            float(front_settle.value) if front_settle.value is not None else 0.0
        )
        state.front_settle_total_events = int(getattr(front_settle, "total_events", 0))
        state.front_settle_valid_clean_events = int(getattr(front_settle, "valid_clean_events", 0))
        state.front_settle_invalid_reason = str(front_settle.invalid_reason or "")
        rear_sv_avg = (lr_sv + rr_sv) / 2
        rear_settle = _settle_time_signal(
            rear_rh,
            rear_sv_avg,
            ibt.tick_rate,
            brake=brake,
            throttle=throttle_signal,
            kerb_mask=kerb_spatial_mask,
        )
        state.rear_rh_settle_time_ms = (
            float(rear_settle.value) if rear_settle.value is not None else 0.0
        )
        state.rear_settle_total_events = int(getattr(rear_settle, "total_events", 0))
        state.rear_settle_valid_clean_events = int(getattr(rear_settle, "valid_clean_events", 0))
        state.rear_settle_invalid_reason = str(rear_settle.invalid_reason or "")
        state.extraction_attempts.append(
            {
                "phase": "settle_time",
                "source": "event_based_clean_response",
                "front_valid_clean_events": state.front_settle_valid_clean_events,
                "rear_valid_clean_events": state.rear_settle_valid_clean_events,
                "front_total_events": state.front_settle_total_events,
                "rear_total_events": state.rear_settle_total_events,
            }
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

    # --- Telemetry truth map ---
    state.telemetry_signals = build_signal_map(state)
    state.telemetry_bundle = build_telemetry_bundle(state.telemetry_signals).to_dict()
    if state.front_settle_invalid_reason:
        state.fallback_reasons.append(f"front_settle:{state.front_settle_invalid_reason}")
    if state.rear_settle_invalid_reason:
        state.fallback_reasons.append(f"rear_settle:{state.rear_settle_invalid_reason}")

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
    brake = ibt.channel("Brake")[start:end + 1] if ibt.has_channel("Brake") else np.zeros(n)
    if ibt.has_channel("ThrottleRaw"):
        throttle = ibt.channel("ThrottleRaw")[start:end + 1]
    elif ibt.has_channel("Throttle"):
        throttle = ibt.channel("Throttle")[start:end + 1]
    else:
        throttle = np.zeros(n)

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

        vehicle_speed = np.maximum(np.hypot(vx, vy), 2.0)
        front_min_ws = np.minimum(lf_ws, rf_ws)
        rear_max_ws = np.maximum(lr_ws, rr_ws)

        front_lock = np.maximum(0.0, vehicle_speed - front_min_ws) / vehicle_speed
        rear_power_slip = np.maximum(0.0, rear_max_ws - vehicle_speed) / vehicle_speed

        braking_mask = (
            (brake > 0.25)
            & (throttle < 0.15)
            & (speed_kph > 60)
            & (np.abs(lat_g) < 1.4)
        )
        if np.sum(braking_mask) < 40:
            braking_mask = (brake > 0.25) & (speed_kph > 60)
            if np.sum(braking_mask) > 20:
                state.metric_fallbacks.append("front_braking_lock_ratio_p95=fallback_brake_mask")

        power_mask = (
            (throttle > 0.45)
            & (brake < 0.05)
            & (speed_kph > 60)
            & (np.abs(lat_g) < 1.1)
        )
        if np.sum(power_mask) < 40:
            power_mask = (throttle > 0.35) & (brake < 0.10) & (speed_kph > 60)
            if np.sum(power_mask) > 20:
                state.metric_fallbacks.append("rear_power_slip_ratio_p95=fallback_power_mask")

        if np.sum(braking_mask) > 20:
            state.front_braking_lock_ratio_p95 = float(np.percentile(front_lock[braking_mask], 95))
            state.front_slip_ratio_p95 = state.front_braking_lock_ratio_p95

            dt = 1.0 / max(float(getattr(ibt, "tick_rate", 60.0)), 1.0)
            lf_decel = -np.gradient(lf_ws, dt)
            rf_decel = -np.gradient(rf_ws, dt)
            asym = np.abs(lf_decel - rf_decel)
            state.front_brake_wheel_decel_asymmetry_p95_ms2 = float(
                np.percentile(asym[braking_mask], 95)
            )
        elif np.sum(speed_kph > 60) > 100:
            state.front_slip_ratio_p95 = float(np.percentile(front_lock[speed_kph > 60], 95))
            state.front_braking_lock_ratio_p95 = state.front_slip_ratio_p95
            state.metric_fallbacks.append("front_braking_lock_ratio_p95=legacy_speed_mask")

        if np.sum(power_mask) > 20:
            state.rear_power_slip_ratio_p95 = float(np.percentile(rear_power_slip[power_mask], 95))
            state.rear_slip_ratio_p95 = state.rear_power_slip_ratio_p95
        elif np.sum(speed_kph > 60) > 100:
            state.rear_slip_ratio_p95 = float(np.percentile(rear_power_slip[speed_kph > 60], 95))
            state.rear_power_slip_ratio_p95 = state.rear_slip_ratio_p95
            state.metric_fallbacks.append("rear_power_slip_ratio_p95=legacy_speed_mask")

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


def _settle_time_signal(
    rh_signal: np.ndarray,
    shock_vel: np.ndarray,
    tick_rate: int,
    *,
    brake: np.ndarray | None = None,
    throttle: np.ndarray | None = None,
    kerb_mask: np.ndarray | None = None,
    max_search_ms: float = 700.0,
) -> TelemetrySignal[float]:
    """Event-based settle-time extraction from ride-height response.

    The legacy implementation silently returned 0 ms when it could not form a
    valid response. That is not usable as a damping signal. This implementation
    only returns a trusted settle time when there are at least 3 clean-track
    disturbance events with a clear peak excursion and a sustained settled
    window.
    """
    signal = TelemetrySignal[float](
        value=None,
        quality="unknown",
        confidence=0.0,
        source="event_based_clean_disturbance_response",
        invalid_reason="insufficient_samples",
    )
    signal.total_events = 0
    signal.valid_clean_events = 0

    if len(rh_signal) < 100 or len(shock_vel) < 100:
        return signal

    n = min(len(rh_signal), len(shock_vel))
    rh_signal = np.asarray(rh_signal[:n], dtype=float)
    shock_vel = np.asarray(shock_vel[:n], dtype=float)
    abs_shock = np.abs(shock_vel)
    brake = np.asarray(brake[:n], dtype=float) if brake is not None and len(brake) >= n else np.zeros(n)
    throttle = (
        np.asarray(throttle[:n], dtype=float)
        if throttle is not None and len(throttle) >= n
        else np.zeros(n)
    )
    kerb_mask = (
        np.asarray(kerb_mask[:n], dtype=bool)
        if kerb_mask is not None and len(kerb_mask) >= n
        else np.zeros(n, dtype=bool)
    )

    shock_threshold = max(
        float(np.percentile(abs_shock, 97)),
        float(np.median(abs_shock) + 3.0 * np.median(np.abs(abs_shock - np.median(abs_shock)))),
    )
    if shock_threshold < 0.01:
        signal.invalid_reason = "no_disturbance_events"
        return signal

    noise_floor = max(float(np.std(rh_signal[: max(10, tick_rate // 3)])), 0.15)
    refractory = max(4, int(0.12 * tick_rate))
    candidate_indices = np.where(abs_shock >= shock_threshold)[0]
    event_starts: list[int] = []
    last_start = -refractory
    for idx in candidate_indices:
        if idx - last_start >= refractory:
            event_starts.append(int(idx))
            last_start = int(idx)

    settle_times_ms: list[float] = []
    signal.total_events = len(event_starts)
    if not event_starts:
        signal.invalid_reason = "no_disturbance_events"
        return signal

    settle_window = max(3, int(0.15 * tick_rate))
    max_search_samples = int(max_search_ms / 1000.0 * tick_rate)

    for start_idx in event_starts:
        pre_start = max(0, start_idx - max(3, int(0.12 * tick_rate)))
        peak_search_end = min(n, start_idx + max(3, int(0.12 * tick_rate)))
        baseline = float(np.median(rh_signal[pre_start:start_idx])) if start_idx > pre_start else float(rh_signal[start_idx])
        peak_local = rh_signal[start_idx:peak_search_end]
        if peak_local.size == 0:
            continue
        rel_peak_idx = int(np.argmax(np.abs(peak_local - baseline)))
        peak_idx = start_idx + rel_peak_idx
        amplitude = float(abs(rh_signal[peak_idx] - baseline))
        if amplitude < max(0.5, noise_floor * 1.5):
            continue

        window_slice = slice(max(0, start_idx - 2), min(n, peak_idx + 2))
        is_kerb = bool(np.any(kerb_mask[window_slice]))
        mean_brake = float(np.mean(brake[window_slice])) if window_slice.stop > window_slice.start else 0.0
        mean_throttle = float(np.mean(throttle[window_slice])) if window_slice.stop > window_slice.start else 0.0
        if is_kerb:
            event_class = "kerb"
        elif mean_brake > 0.15:
            event_class = "braking"
        elif mean_throttle > 0.25:
            event_class = "traction"
        else:
            event_class = "clean"

        if event_class != "clean":
            continue

        peak_shock = float(np.max(abs_shock[pre_start:peak_search_end]))
        band_mm = max(0.2, amplitude * 0.10)
        vel_band = max(0.02, peak_shock * 0.20)
        signal.valid_clean_events += 1

        search_end = min(n - settle_window, peak_idx + max_search_samples)
        settled = False
        for idx in range(peak_idx, search_end):
            rh_window = rh_signal[idx:idx + settle_window]
            vel_window = abs_shock[idx:idx + settle_window]
            if np.all(np.abs(rh_window - baseline) <= band_mm) and np.all(vel_window <= vel_band):
                settle_times_ms.append((idx - start_idx) / tick_rate * 1000.0)
                settled = True
                break

        if not settled and signal.invalid_reason in {"", "insufficient_samples", "insufficient_clean_events"}:
            signal.invalid_reason = "no_sustained_settle_window"

    if signal.valid_clean_events < 3:
        signal.invalid_reason = "insufficient_clean_events"
        return signal
    if not settle_times_ms:
        signal.invalid_reason = signal.invalid_reason or "no_sustained_settle_window"
        return signal

    signal.value = round(float(np.median(settle_times_ms)), 1)
    signal.quality = "trusted"
    signal.confidence = min(0.95, 0.55 + len(settle_times_ms) * 0.08)
    signal.invalid_reason = ""
    return signal


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
    """Extract brake line pressures, ABS data, and hydraulic split metrics.

    Per-corner brake line pressure captures the hydraulic pressure share, not the
    actual brake torque distribution. ABS, pad mu, disc radius, and hybrid regen
    can all change the real tyre force split, so this metric is kept explicitly
    hydraulic-only.
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
                split_samples = front_braking[valid] / total[valid] * 100
                hydraulic_split = round(float(np.mean(split_samples)), 1)
                state.hydraulic_brake_split_pct = hydraulic_split
                state.measured_brake_bias_pct = hydraulic_split
                state.hydraulic_brake_split_confidence = round(
                    float(np.sum(valid) / max(np.sum(braking), 1)),
                    3,
                )

                if ibt.has_channel("LongAccel"):
                    long_accel = ibt.channel("LongAccel")[start:end + 1]
                    braking_decel = np.maximum(0.0, -long_accel[braking] / 9.81)
                else:
                    speed = ibt.channel("Speed")[start:end + 1] if ibt.has_channel("Speed") else None
                    if speed is not None:
                        dt = 1.0 / max(float(getattr(ibt, "tick_rate", 60.0)), 1.0)
                        braking_decel = np.maximum(0.0, -np.gradient(speed, dt)[braking] / 9.81)
                    else:
                        braking_decel = np.array([])

                if braking_decel.size > 0:
                    state.braking_decel_mean_g = round(float(np.mean(braking_decel)), 3)
                    state.braking_decel_peak_g = round(float(np.percentile(braking_decel, 95)), 3)

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
        # Require meaningful ABS engagement: at least 50 samples AND abs_active > 5%
        # to avoid noise artifacts (previously 5 samples produced 100% cut on near-zero ABS)
        if np.sum(abs_engaged) > 50 and state.abs_active_pct > 5.0:
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
            if float(np.max(bias) - np.min(bias)) <= 0.25:
                state.live_brake_bias_pct = round(float(np.median(bias)), 1)

    if ibt.has_channel("dcTractionControl"):
        tc = ibt.channel("dcTractionControl")
        if tc is not None and len(tc) > 100:
            changes = np.sum(np.abs(np.diff(tc)) > 0.001)
            state.tc_adjustments = int(changes)
            if float(np.max(tc) - np.min(tc)) <= 0.1:
                state.live_tc_slip = int(round(float(np.median(tc))))

    if ibt.has_channel("dcTractionControl2"):
        tc2 = ibt.channel("dcTractionControl2")
        if tc2 is not None and len(tc2) > 100 and float(np.max(tc2) - np.min(tc2)) <= 0.1:
            state.live_tc_gain = int(round(float(np.median(tc2))))

    if ibt.has_channel("dcAntiRollFront"):
        arb_front = ibt.channel("dcAntiRollFront")
        if arb_front is not None and len(arb_front) > 100 and float(np.max(arb_front) - np.min(arb_front)) <= 0.1:
            state.live_front_arb_blade = int(round(float(np.median(arb_front))))

    if ibt.has_channel("dcAntiRollRear"):
        arb_rear = ibt.channel("dcAntiRollRear")
        if arb_rear is not None and len(arb_rear) > 100 and float(np.max(arb_rear) - np.min(arb_rear)) <= 0.1:
            state.live_rear_arb_blade = int(round(float(np.median(arb_rear))))


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
        braking_pitch = (brake > 0.20) & (speed_kph > 80)
        if np.sum(braking_pitch) > 30:
            state.pitch_mean_braking_deg = round(float(np.mean(pitch[braking_pitch])), 3)
            state.pitch_range_braking_deg = round(
                float(np.percentile(pitch[braking_pitch], 99) - np.percentile(pitch[braking_pitch], 1)),
                2,
            )


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
