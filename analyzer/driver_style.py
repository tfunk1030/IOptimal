"""Driver style analysis from IBT telemetry and corner segmentation.

Extracts quantitative driver behavior metrics that determine setup tuning:
- Trail braking depth → brake bias, diff coast ramp
- Throttle progressiveness → diff drive ramp, preload
- Steering smoothness → damper LS response
- Consistency → optimize for peak vs forgiveness
- Cornering aggression → tyre thermal targets

Depends on Phase 1: analyzer/segment.py for CornerAnalysis data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from car_model.cars import CarModel
    from track_model.ibt_parser import IBTFile

from analyzer.segment import CornerAnalysis


@dataclass
class DriverProfile:
    """Quantitative driver behavior profile extracted from telemetry."""

    # Trail braking (affects brake bias + diff coast ramp)
    trail_brake_depth_mean: float = 0.0  # 0-1: avg fraction of corner with brake
    trail_brake_depth_p95: float = 0.0  # 0-1: aggressive corners
    trail_brake_classification: str = "moderate"  # "light" | "moderate" | "deep"

    # Throttle application (affects diff drive ramp + preload)
    throttle_progressiveness: float = 0.5  # 0-1: R² of linear fit during exit
    throttle_onset_rate_pct_per_s: float = 0.0
    throttle_classification: str = "moderate"  # "progressive" | "moderate" | "binary"

    # Steering (affects damper LS + ARB sensitivity)
    steering_jerk_p95_rad_per_s2: float = 0.0
    steering_smoothness: str = "moderate"  # "smooth" | "moderate" | "aggressive"

    # Consistency (optimize for peak vs average)
    apex_speed_cv: float = 0.0  # coefficient of variation across laps
    entry_speed_cv: float = 0.0
    consistency: str = "consistent"  # "consistent" | "variable" | "erratic"

    # Cornering aggression (tyre thermal targets)
    avg_peak_lat_g_utilization: float = 0.0  # actual / theoretical limit
    cornering_aggression: str = "moderate"  # "conservative" | "moderate" | "limit"

    # Summary
    style: str = "moderate-consistent"

    def summary(self) -> str:
        """One-line summary of driver profile."""
        return (
            f"Style: {self.style} | "
            f"Trail brake: {self.trail_brake_classification} ({self.trail_brake_depth_mean:.0%}) | "
            f"Throttle: {self.throttle_classification} (R²={self.throttle_progressiveness:.2f}) | "
            f"Steering: {self.steering_smoothness} (jerk p95={self.steering_jerk_p95_rad_per_s2:.0f}) | "
            f"Consistency: {self.consistency} (CV={self.apex_speed_cv:.3f}) | "
            f"Aggression: {self.cornering_aggression} ({self.avg_peak_lat_g_utilization:.0%})"
        )


def _classify_trail_brake(depth: float) -> str:
    if depth < 0.15:
        return "light"
    elif depth > 0.40:
        return "deep"
    return "moderate"


def _classify_throttle(r_squared: float) -> str:
    if r_squared > 0.75:
        return "progressive"
    elif r_squared < 0.50:
        return "binary"
    return "moderate"


def _classify_steering(jerk_p95: float) -> str:
    if jerk_p95 < 50:
        return "smooth"
    elif jerk_p95 > 100:
        return "aggressive"
    return "moderate"


def _classify_consistency(cv: float) -> str:
    if cv < 0.03:
        return "consistent"
    elif cv > 0.08:
        return "erratic"
    return "variable"


def _classify_aggression(utilization: float) -> str:
    if utilization < 0.75:
        return "conservative"
    elif utilization > 0.90:
        return "limit"
    return "moderate"


def _compute_style(
    steering: str, consistency: str, aggression: str
) -> str:
    """Combine sub-classifications into overall style label."""
    if steering == "smooth" and consistency == "consistent":
        prefix = "smooth"
    elif steering == "aggressive" or aggression == "limit":
        prefix = "aggressive"
    else:
        prefix = "moderate"

    return f"{prefix}-{consistency}"


def analyze_driver(
    ibt: IBTFile,
    corners: list[CornerAnalysis],
    car: CarModel,
    tick_rate: int = 60,
) -> DriverProfile:
    """Extract a DriverProfile from IBT telemetry and corner segmentation.

    Parameters
    ----------
    ibt : IBTFile
        Full parsed IBT file.
    corners : list[CornerAnalysis]
        Corner analysis from segment_lap() (best lap).
    car : CarModel
        Car model for physics parameters.
    tick_rate : int
        Sample rate (Hz).

    Returns
    -------
    DriverProfile
    """
    dt = 1.0 / tick_rate
    profile = DriverProfile()

    # ── Trail Braking ──
    if corners:
        trail_depths = [c.trail_brake_pct for c in corners]
        profile.trail_brake_depth_mean = float(np.mean(trail_depths))
        profile.trail_brake_depth_p95 = float(np.percentile(trail_depths, 95))
        profile.trail_brake_classification = _classify_trail_brake(
            profile.trail_brake_depth_mean
        )

    # ── Throttle Progressiveness ──
    # Fit linear ramp to throttle in exit phase (apex → full power) per corner
    # Use full telemetry for precise per-sample analysis
    throttle_full = ibt.channel("Throttle")
    speed_full = ibt.channel("Speed")
    r_squared_list = []
    onset_rates = []

    if throttle_full is not None and corners:
        for c in corners:
            # Find approximate sample indices for this corner's exit phase
            # Use lap distance to find apex and exit
            lap_dist_full = ibt.channel("LapDist")
            if lap_dist_full is None:
                continue

            # Estimate sample range from best lap indices
            # The corner's exit phase: from apex to where throttle reaches ~80%
            # We work with the full array but look near the corner distance
            # This is approximate — find samples near the corner's distance range
            pass  # Will use corner-level data below

    # Use best lap for detailed throttle analysis
    best = ibt.best_lap_indices()
    if best is not None:
        bstart, bend = best
        throttle_lap = ibt.channel("Throttle")[bstart:bend + 1]
        speed_lap = ibt.channel("Speed")[bstart:bend + 1] * 3.6  # kph
        lat_g_lap = ibt.channel("LatAccel")[bstart:bend + 1]

        # Find throttle application phases: where throttle is ramping 10%→90%
        in_ramp = (throttle_lap > 0.10) & (throttle_lap < 0.90)
        # Additional filter: cornering at reasonable speed
        in_corner_exit = in_ramp & (np.abs(lat_g_lap) > 0.3) & (speed_lap > 50)

        if np.sum(in_corner_exit) > 30:
            # Fit linearity: R² of throttle vs time in exit phases
            # Find contiguous ramp segments
            edges = np.diff(in_corner_exit.astype(int))
            starts = np.where(edges == 1)[0] + 1
            ends = np.where(edges == -1)[0] + 1
            if in_corner_exit[0]:
                starts = np.insert(starts, 0, 0)
            if in_corner_exit[-1]:
                ends = np.append(ends, len(in_corner_exit))

            seg_r2 = []
            for i in range(min(len(starts), len(ends))):
                s, e = starts[i], ends[i]
                if e - s < 10:
                    continue
                seg_throttle = throttle_lap[s:e]
                t = np.arange(len(seg_throttle)) * dt
                if np.std(seg_throttle) < 0.01:
                    continue
                # Linear fit R²
                coeffs = np.polyfit(t, seg_throttle, 1)
                fitted = np.polyval(coeffs, t)
                ss_res = np.sum((seg_throttle - fitted) ** 2)
                ss_tot = np.sum((seg_throttle - np.mean(seg_throttle)) ** 2)
                if ss_tot > 0:
                    r2 = 1.0 - ss_res / ss_tot
                    seg_r2.append(max(r2, 0.0))
                    # Onset rate: slope in %/s
                    onset_rates.append(coeffs[0] * 100)

            if seg_r2:
                profile.throttle_progressiveness = float(np.mean(seg_r2))

    if onset_rates:
        profile.throttle_onset_rate_pct_per_s = float(np.mean(onset_rates))
    profile.throttle_classification = _classify_throttle(
        profile.throttle_progressiveness
    )

    # ── Steering Smoothness ──
    if best is not None:
        bstart, bend = best
        steer = ibt.channel("SteeringWheelAngle")[bstart:bend + 1]
        if steer is not None and len(steer) > 5:
            # Steering jerk = d²steer/dt²
            d_steer = np.diff(steer) * tick_rate  # rad/s
            d2_steer = np.diff(d_steer) * tick_rate  # rad/s²
            jerk = np.abs(d2_steer)
            # Filter to meaningful cornering (not straight-line micro-corrections)
            speed_lap = ibt.channel("Speed")[bstart:bend + 1] * 3.6
            lat_g_lap = ibt.channel("LatAccel")[bstart:bend + 1]
            cornering_mask = (np.abs(lat_g_lap[2:]) > 0.3) & (speed_lap[2:] > 40)
            if np.sum(cornering_mask) > 30:
                profile.steering_jerk_p95_rad_per_s2 = float(
                    np.percentile(jerk[cornering_mask], 95)
                )
            else:
                profile.steering_jerk_p95_rad_per_s2 = float(np.percentile(jerk, 95))

    profile.steering_smoothness = _classify_steering(
        profile.steering_jerk_p95_rad_per_s2
    )

    # ── Consistency ──
    lap_bounds = ibt.lap_boundaries()
    valid_laps = [(ln, s, e) for ln, s, e in lap_bounds if ln > 0 and (e - s) > 60 * tick_rate]

    if len(valid_laps) >= 3 and corners:
        # For each valid lap, detect corners and compare apex speeds
        # across laps at matching corners (by lap distance proximity)
        from analyzer.segment import _detect_corners

        ref_dists = [c.lap_dist_start_m for c in corners]
        apex_speeds_per_corner: list[list[float]] = [[] for _ in ref_dists]
        entry_speeds_per_corner: list[list[float]] = [[] for _ in ref_dists]

        for _ln, ls, le in valid_laps:
            lap_speed = ibt.channel("Speed")[ls:le + 1] * 3.6
            lap_lat = ibt.channel("LatAccel")[ls:le + 1]
            lap_steer = ibt.channel("SteeringWheelAngle")[ls:le + 1]
            lap_dist = ibt.channel("LapDist")[ls:le + 1]

            if len(lap_speed) < 60:
                continue

            lap_corners = _detect_corners(lap_lat, lap_speed, lap_steer, lap_dist)
            for lcs, lca, lce, _dir in lap_corners:
                apex_dist = float(lap_dist[min(lca, len(lap_dist) - 1)])
                apex_spd = float(np.min(lap_speed[lcs:lce]))
                entry_spd = float(lap_speed[lcs])

                # Match to nearest reference corner
                diffs = [abs(apex_dist - rd) for rd in ref_dists]
                best_match = int(np.argmin(diffs))
                if diffs[best_match] < 100:  # within 100m
                    apex_speeds_per_corner[best_match].append(apex_spd)
                    entry_speeds_per_corner[best_match].append(entry_spd)

        # Compute CV across laps per corner, then average
        apex_cvs = []
        entry_cvs = []
        for speeds in apex_speeds_per_corner:
            if len(speeds) >= 3:
                mean_s = np.mean(speeds)
                if mean_s > 10:
                    apex_cvs.append(float(np.std(speeds) / mean_s))
        for speeds in entry_speeds_per_corner:
            if len(speeds) >= 3:
                mean_s = np.mean(speeds)
                if mean_s > 10:
                    entry_cvs.append(float(np.std(speeds) / mean_s))

        if apex_cvs:
            profile.apex_speed_cv = float(np.mean(apex_cvs))
        if entry_cvs:
            profile.entry_speed_cv = float(np.mean(entry_cvs))

    profile.consistency = _classify_consistency(profile.apex_speed_cv)

    # ── Cornering Aggression ──
    if corners:
        utilizations = []
        for c in corners:
            if c.min_radius_m > 1 and c.min_radius_m < 500:
                # Theoretical max lat g from radius and speed
                v_apex = c.apex_speed_kph / 3.6
                theoretical_lat_g = v_apex ** 2 / (c.min_radius_m * 9.81)
                if theoretical_lat_g > 0.3:
                    utilizations.append(c.peak_lat_g / theoretical_lat_g)

        if utilizations:
            profile.avg_peak_lat_g_utilization = float(np.mean(utilizations))

    profile.cornering_aggression = _classify_aggression(
        profile.avg_peak_lat_g_utilization
    )

    # ── Overall Style ──
    profile.style = _compute_style(
        profile.steering_smoothness,
        profile.consistency,
        profile.cornering_aggression,
    )

    return profile
