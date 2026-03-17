"""Corner-by-corner lap segmentation with per-corner suspension/handling metrics.

Segments a best lap into discrete corner events. Each corner carries suspension,
handling, and time-loss metrics that drive downstream driver-style analysis and
solver modifier computation.

Reuses detection algorithms from track_model/build_profile.py and understeer
computation from analyzer/extract.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from car_model.cars import CarModel
    from track_model.ibt_parser import IBTFile


@dataclass
class CornerAnalysis:
    """Per-corner suspension, handling, and time-loss metrics."""

    corner_id: int
    lap_dist_start_m: float
    lap_dist_end_m: float
    direction: str  # "left" | "right"
    entry_speed_kph: float
    apex_speed_kph: float
    exit_speed_kph: float
    min_radius_m: float
    peak_lat_g: float
    duration_s: float
    speed_class: str  # "low" (<120kph) | "mid" (120-180) | "high" (>180)

    # Per-corner suspension
    front_shock_vel_p95_mps: float = 0.0
    rear_shock_vel_p95_mps: float = 0.0
    front_shock_vel_p99_mps: float = 0.0
    rear_shock_vel_p99_mps: float = 0.0
    front_rh_mean_mm: float = 0.0
    rear_rh_mean_mm: float = 0.0
    front_rh_min_mm: float = 0.0  # splitter proximity check

    # Per-corner handling
    understeer_mean_deg: float = 0.0
    body_slip_peak_deg: float = 0.0
    trail_brake_pct: float = 0.0  # fraction of corner duration with brake > 5%
    throttle_onset_dist_m: float = 0.0  # lap_dist (m) where throttle first exceeds 20%

    # Kerb overlap
    has_kerb_overlap: bool = False
    kerb_severity_max: float = 0.0

    # Raw phase timing and bounded opportunity proxies
    entry_phase_s: float = 0.0
    apex_phase_s: float = 0.0
    exit_phase_s: float = 0.0
    throttle_delay_s: float = 0.0
    entry_loss_s: float = 0.0
    apex_loss_s: float = 0.0
    exit_loss_s: float = 0.0
    platform_risk_flags: list[str] = field(default_factory=list)
    traction_risk_flags: list[str] = field(default_factory=list)
    delta_to_min_time_s: float = 0.0  # backward-compatible alias of bounded total


def _classify_speed(apex_speed_kph: float) -> str:
    """Classify corner by apex speed into aero-relevance bands."""
    if apex_speed_kph < 120:
        return "low"
    elif apex_speed_kph < 180:
        return "mid"
    return "high"


def _detect_corners(
    lat_g: np.ndarray,
    speed_kph: np.ndarray,
    steering: np.ndarray,
    lap_dist: np.ndarray,
) -> list[tuple[int, int, int, str]]:
    """Detect corner boundaries: (start_idx, apex_idx, end_idx, direction).

    Uses the same algorithm as track_model/build_profile.py:_find_corners():
    smooth |lat_g| with 15-sample kernel, threshold at 0.5g, min 10 samples.
    """
    kernel = np.ones(15) / 15  # 0.25s window at 60Hz
    lat_smooth = np.convolve(np.abs(lat_g), kernel, mode="same")

    in_corner = lat_smooth > 0.5
    edges = np.diff(in_corner.astype(int))
    starts = np.where(edges == 1)[0] + 1
    ends = np.where(edges == -1)[0] + 1

    if in_corner[0]:
        starts = np.insert(starts, 0, 0)
    if in_corner[-1]:
        ends = np.append(ends, len(in_corner))

    corners = []
    n = min(len(starts), len(ends))
    for i in range(n):
        s, e = starts[i], ends[i]
        if e - s < 10:
            continue

        seg_speed = speed_kph[s:e]
        seg_steer = steering[s:e]
        apex_local = int(np.argmin(seg_speed))
        apex_idx = s + apex_local

        avg_steer = float(np.mean(seg_steer))
        direction = "left" if avg_steer > 0 else "right"

        corners.append((s, apex_idx, e, direction))

    return corners


def _detect_braking_zones(
    brake: np.ndarray,
    lap_dist: np.ndarray,
) -> list[tuple[int, int]]:
    """Detect braking zone boundaries: (start_idx, end_idx).

    Same algorithm as track_model/build_profile.py:_find_braking_zones().
    """
    braking = brake > 0.10
    edges = np.diff(braking.astype(int))
    starts = np.where(edges == 1)[0] + 1
    ends = np.where(edges == -1)[0] + 1

    if braking[0]:
        starts = np.insert(starts, 0, 0)
    if braking[-1]:
        ends = np.append(ends, len(braking))

    zones = []
    n = min(len(starts), len(ends))
    for i in range(n):
        s, e = starts[i], ends[i]
        if e - s < 10:
            continue
        # Skip wrap-around
        if lap_dist[min(e - 1, len(lap_dist) - 1)] < lap_dist[s]:
            continue
        zones.append((s, e))

    return zones


def _match_braking_to_corner(
    braking_zones: list[tuple[int, int]],
    corner_start: int,
    lap_dist: np.ndarray,
) -> tuple[int, int] | None:
    """Find the braking zone that ends closest before the corner entry."""
    corner_dist = lap_dist[corner_start]
    best = None
    best_gap = float("inf")
    for bz_start, bz_end in braking_zones:
        bz_end_dist = lap_dist[min(bz_end - 1, len(lap_dist) - 1)]
        gap = corner_dist - bz_end_dist
        # Braking zone should end before or at corner start, small gap
        if 0 <= gap < best_gap:
            best_gap = gap
            best = (bz_start, bz_end)
        # Also allow slight overlap (braking extends into corner)
        elif -200 < gap < 0 and abs(gap) < best_gap:
            best_gap = abs(gap)
            best = (bz_start, bz_end)
    return best


def segment_lap(
    ibt: IBTFile,
    start: int,
    end: int,
    car: CarModel | None = None,
    tick_rate: int = 60,
    kerb_events: list | None = None,
) -> list[CornerAnalysis]:
    """Segment one lap (start..end sample indices) into corner events.

    Parameters
    ----------
    ibt : IBTFile
        Parsed IBT file with all channels available.
    start, end : int
        Sample indices for the lap (inclusive).
    car : CarModel | None
        If provided, computes understeer angle using steering ratio and wheelbase.
    tick_rate : int
        Sample rate in Hz (default 60).

    Returns
    -------
    list[CornerAnalysis]
        One entry per detected corner, sorted by lap distance.
    """
    dt = 1.0 / tick_rate
    n = end - start + 1

    # --- Load channels (sliced to lap) ---
    speed_kph = ibt.channel("Speed")[start:end + 1] * 3.6  # m/s → kph
    speed_ms = ibt.channel("Speed")[start:end + 1]
    lat_g = ibt.channel("LatAccel")[start:end + 1]
    long_g = ibt.channel("LongAccel")[start:end + 1]
    steering = ibt.channel("SteeringWheelAngle")[start:end + 1]
    brake = ibt.channel("Brake")[start:end + 1]
    throttle = ibt.channel("Throttle")[start:end + 1]
    lap_dist = ibt.channel("LapDist")[start:end + 1]

    # Shock velocities (front avg, rear avg)
    has_shocks = all(
        ibt.has_channel(c)
        for c in ["LFshockVel", "RFshockVel", "LRshockVel", "RRshockVel"]
    )
    if has_shocks:
        lf_sv = ibt.channel("LFshockVel")[start:end + 1]
        rf_sv = ibt.channel("RFshockVel")[start:end + 1]
        lr_sv = ibt.channel("LRshockVel")[start:end + 1]
        rr_sv = ibt.channel("RRshockVel")[start:end + 1]
        front_sv = (np.abs(lf_sv) + np.abs(rf_sv)) / 2.0
        rear_sv = (np.abs(lr_sv) + np.abs(rr_sv)) / 2.0
    else:
        front_sv = np.zeros(n)
        rear_sv = np.zeros(n)

    # Ride heights (front avg, rear avg)
    has_rh = all(
        ibt.has_channel(c)
        for c in ["LFrideHeight", "RFrideHeight", "LRrideHeight", "RRrideHeight"]
    )
    if has_rh:
        lf_rh = ibt.channel("LFrideHeight")[start:end + 1] * 1000  # m → mm
        rf_rh = ibt.channel("RFrideHeight")[start:end + 1] * 1000
        lr_rh = ibt.channel("LRrideHeight")[start:end + 1] * 1000
        rr_rh = ibt.channel("RRrideHeight")[start:end + 1] * 1000
        front_rh = (lf_rh + rf_rh) / 2.0
        rear_rh = (lr_rh + rr_rh) / 2.0
    else:
        front_rh = np.zeros(n)
        rear_rh = np.zeros(n)

    # Yaw rate (used for radius and understeer)
    yaw_rate: np.ndarray | None = None
    if ibt.has_channel("YawRate"):
        yaw_rate = ibt.channel("YawRate")[start:end + 1]

    # Understeer (optional, requires car model)
    understeer_deg: np.ndarray | None = None
    if car is not None and yaw_rate is not None:
        safe_speed = np.maximum(speed_ms, 5.0)
        road_wheel_angle = steering / car.steering_ratio
        understeer_rad = road_wheel_angle - car.wheelbase_m * yaw_rate / safe_speed
        understeer_deg = np.degrees(understeer_rad)

    # Body slip
    body_slip_deg: np.ndarray | None = None
    if ibt.has_channel("VelocityX") and ibt.has_channel("VelocityY"):
        vx = ibt.channel("VelocityX")[start:end + 1]
        vy = ibt.channel("VelocityY")[start:end + 1]
        body_slip_deg = np.degrees(np.arctan2(vy, np.maximum(np.abs(vx), 1.0)))

    # --- Detect corners and braking zones ---
    raw_corners = _detect_corners(lat_g, speed_kph, steering, lap_dist)
    braking_zones = _detect_braking_zones(brake, lap_dist)

    # --- Build CornerAnalysis for each detected corner ---
    results: list[CornerAnalysis] = []
    for cid, (cs, ca, ce, direction) in enumerate(raw_corners):
        seg_speed = speed_kph[cs:ce]
        seg_lat = lat_g[cs:ce]
        seg_brake = brake[cs:ce]
        seg_throttle = throttle[cs:ce]
        seg_dist = lap_dist[cs:ce]

        entry_speed = float(speed_kph[cs])
        apex_speed = float(np.min(seg_speed))
        exit_speed = float(speed_kph[min(ce - 1, n - 1)])
        peak_lat = float(np.max(np.abs(seg_lat)))
        duration = (ce - cs) * dt

        # Radius from speed / yaw_rate at peak lat_g sample (independent measurement)
        peak_lat_idx = cs + int(np.argmax(np.abs(seg_lat)))
        if yaw_rate is not None and abs(float(yaw_rate[peak_lat_idx])) > 0.01:
            radius = abs(float(speed_ms[peak_lat_idx]) / float(yaw_rate[peak_lat_idx]))
        else:
            # Fallback: use speed and lat_g at peak lat_g sample (cotimed)
            peak_speed_ms = float(speed_ms[peak_lat_idx])
            lat_ms2 = peak_lat * 9.81
            radius = (peak_speed_ms ** 2) / lat_ms2 if lat_ms2 > 0.5 else 999.0

        speed_class = _classify_speed(apex_speed)

        # Suspension metrics for this corner
        seg_front_sv = front_sv[cs:ce]
        seg_rear_sv = rear_sv[cs:ce]
        seg_front_rh = front_rh[cs:ce]
        seg_rear_rh = rear_rh[cs:ce]

        f_sv_p95 = float(np.percentile(seg_front_sv, 95)) if len(seg_front_sv) > 2 else 0.0
        r_sv_p95 = float(np.percentile(seg_rear_sv, 95)) if len(seg_rear_sv) > 2 else 0.0
        f_sv_p99 = float(np.percentile(seg_front_sv, 99)) if len(seg_front_sv) > 2 else 0.0
        r_sv_p99 = float(np.percentile(seg_rear_sv, 99)) if len(seg_rear_sv) > 2 else 0.0
        f_rh_mean = float(np.mean(seg_front_rh)) if len(seg_front_rh) > 0 else 0.0
        r_rh_mean = float(np.mean(seg_rear_rh)) if len(seg_rear_rh) > 0 else 0.0
        f_rh_min = float(np.min(seg_front_rh)) if len(seg_front_rh) > 0 else 0.0

        # Understeer for this corner
        us_mean = 0.0
        if understeer_deg is not None:
            seg_us = understeer_deg[cs:ce]
            # Filter to samples where cornering is meaningful
            corn_mask = np.abs(seg_lat) > 0.5
            if np.sum(corn_mask) > 5:
                us_mean = float(np.mean(seg_us[corn_mask]))

        # Body slip peak
        bs_peak = 0.0
        if body_slip_deg is not None:
            seg_bs = body_slip_deg[cs:ce]
            bs_peak = float(np.max(np.abs(seg_bs))) if len(seg_bs) > 0 else 0.0

        # Trail braking: fraction of pre-apex samples (turn-in to apex) with brake > 5%
        apex_local = ca - cs
        pre_apex_brake = seg_brake[:apex_local]
        if len(pre_apex_brake) > 0:
            trail_pct = float(np.sum(pre_apex_brake > 0.05) / len(pre_apex_brake))
        else:
            trail_pct = 0.0

        # Throttle onset: lap distance where throttle first exceeds 20% after apex
        throttle_onset = 0.0
        throttle_delay_s = 0.0
        post_apex_throttle = seg_throttle[apex_local:]
        post_apex_dist = seg_dist[apex_local:]
        if len(post_apex_throttle) > 0:
            onset_mask = post_apex_throttle > 0.20
            if np.any(onset_mask):
                onset_idx = np.argmax(onset_mask)
                throttle_onset = float(post_apex_dist[onset_idx])
                throttle_delay_s = onset_idx * dt
            else:
                throttle_delay_s = len(post_apex_throttle) * dt

        corner_length = float(seg_dist[-1] - seg_dist[0]) if len(seg_dist) > 1 else 0.0
        if corner_length < 0:
            corner_length = 0.0
        entry_phase_s = apex_local * dt
        apex_window = max(3, min(len(seg_speed) // 3, int(0.25 * tick_rate)))
        apex_start = max(0, apex_local - apex_window // 2)
        apex_end = min(len(seg_speed), apex_local + apex_window // 2)
        apex_phase_s = (apex_end - apex_start) * dt
        exit_phase_s = max(0.0, (len(seg_speed) - apex_end) * dt)

        # Check for kerb overlap: any kerb event within this corner's distance range
        corner_has_kerb = False
        corner_kerb_sev = 0.0
        if kerb_events:
            corner_start_m = float(lap_dist[cs])
            corner_end_m = float(lap_dist[min(ce - 1, n - 1)])
            buffer_m = 30.0
            for ke in kerb_events:
                if (corner_start_m - buffer_m) <= ke.lap_dist_m <= (corner_end_m + buffer_m):
                    corner_has_kerb = True
                    corner_kerb_sev = max(corner_kerb_sev, ke.severity)

        platform_flags: list[str] = []
        traction_flags: list[str] = []
        if f_rh_mean > 0 and (f_rh_mean - f_rh_min) > 6.0:
            platform_flags.append("front_rh_collapse")
        if f_sv_p99 > 0.45 or r_sv_p99 > 0.45:
            platform_flags.append("high_shock_velocity")
        if corner_has_kerb and corner_kerb_sev > 2.0:
            platform_flags.append("kerb_overlap")
        if throttle_delay_s > 0.25:
            traction_flags.append("late_throttle")
        if bs_peak > 3.0:
            traction_flags.append("body_slip_peak")
        if exit_speed < apex_speed + 12.0:
            traction_flags.append("weak_exit_speed_recovery")

        results.append(CornerAnalysis(
            corner_id=cid,
            lap_dist_start_m=round(float(lap_dist[cs]), 1),
            lap_dist_end_m=round(float(lap_dist[min(ce - 1, n - 1)]), 1),
            direction=direction,
            entry_speed_kph=round(entry_speed, 1),
            apex_speed_kph=round(apex_speed, 1),
            exit_speed_kph=round(exit_speed, 1),
            min_radius_m=round(radius, 1),
            peak_lat_g=round(peak_lat, 2),
            duration_s=round(duration, 3),
            speed_class=speed_class,
            front_shock_vel_p95_mps=round(f_sv_p95, 4),
            rear_shock_vel_p95_mps=round(r_sv_p95, 4),
            front_shock_vel_p99_mps=round(f_sv_p99, 4),
            rear_shock_vel_p99_mps=round(r_sv_p99, 4),
            front_rh_mean_mm=round(f_rh_mean, 1),
            rear_rh_mean_mm=round(r_rh_mean, 1),
            front_rh_min_mm=round(f_rh_min, 1),
            understeer_mean_deg=round(us_mean, 2),
            body_slip_peak_deg=round(bs_peak, 2),
            trail_brake_pct=round(trail_pct, 3),
            throttle_onset_dist_m=round(throttle_onset, 1),
            has_kerb_overlap=corner_has_kerb,
            kerb_severity_max=round(corner_kerb_sev, 2),
            entry_phase_s=round(entry_phase_s, 3),
            apex_phase_s=round(apex_phase_s, 3),
            exit_phase_s=round(exit_phase_s, 3),
            throttle_delay_s=round(throttle_delay_s, 3),
            entry_loss_s=0.0,
            apex_loss_s=0.0,
            exit_loss_s=0.0,
            platform_risk_flags=platform_flags,
            traction_risk_flags=traction_flags,
            delta_to_min_time_s=0.0,
        ))

    # Sort by lap distance
    results.sort(key=lambda c: c.lap_dist_start_m)
    return results
