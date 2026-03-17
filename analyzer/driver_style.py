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
    throttle_onset_aggression: float = 0.0
    throttle_classification: str = "moderate"  # "progressive" | "moderate" | "binary"

    # Steering (affects damper LS + ARB sensitivity)
    steering_jerk_p95_rad_per_s2: float = 0.0
    steering_smoothness: str = "moderate"  # "smooth" | "moderate" | "aggressive"

    # Consistency (optimize for peak vs average)
    apex_speed_cv: float = 0.0  # coefficient of variation across laps
    entry_speed_cv: float = 0.0
    driver_noise_index: float = 0.0
    consistency: str = "consistent"  # "consistent" | "variable" | "erratic"

    # Confidence / noise separation
    classification_confidence: float = 0.0
    brake_release_quality: float = 0.0
    setup_noise_index: float = 0.0       # How much noise is setup-induced (0-1)
    noise_reasoning: str = ""            # Human-readable decomposition

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
            f"Aggression: {self.cornering_aggression} ({self.avg_peak_lat_g_utilization:.0%}) | "
            f"Confidence: {self.classification_confidence:.2f}"
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
        release_phases = [c.release_phase_s for c in corners if c.release_phase_s > 0]
        if release_phases:
            mean_release = float(np.mean(release_phases))
            std_release = float(np.std(release_phases))
            profile.brake_release_quality = max(0.0, min(1.0, 1.0 - std_release / max(mean_release, 0.1)))

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
                    # Onset rate: slope in %/s (positive = throttle application only)
                    if coeffs[0] > 0:
                        onset_rates.append(coeffs[0] * 100)

            if seg_r2:
                profile.throttle_progressiveness = float(np.mean(seg_r2))

    if onset_rates:
        profile.throttle_onset_rate_pct_per_s = float(np.mean(onset_rates))
        profile.throttle_onset_aggression = max(0.0, min(1.0, profile.throttle_onset_rate_pct_per_s / 400.0))
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
    profile.driver_noise_index = max(
        0.0,
        min(
            1.0,
            profile.apex_speed_cv * 8.0
            + max(0.0, profile.steering_jerk_p95_rad_per_s2 - 50.0) / 120.0,
        ),
    )

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
    trail_margin = abs(profile.trail_brake_depth_mean - 0.275)
    throttle_margin = abs(profile.throttle_progressiveness - 0.625)
    consistency_margin = abs(profile.apex_speed_cv - 0.055)
    confidence = 1.0 - min(
        0.65,
        trail_margin * 0.8 + throttle_margin * 0.8 + consistency_margin * 4.0 + profile.driver_noise_index * 0.3,
    )
    profile.classification_confidence = round(max(0.25, min(0.95, confidence)), 3)

    return profile


def refine_driver_with_measured(
    profile: DriverProfile,
    measured: object,
) -> None:
    """Refine driver profile using MeasuredState in-car adjustment data.

    High in-session adjustment counts indicate the driver was still searching
    for the right setup, which reduces confidence in consistency classification.
    Called after both analyze_driver() and extract() have completed.

    Modifies profile in-place.
    """
    bb_adj = getattr(measured, "brake_bias_adjustments", 0)
    tc_adj = getattr(measured, "tc_adjustments", 0)
    arb_f_adj = getattr(measured, "arb_front_adjustments", 0)
    arb_r_adj = getattr(measured, "arb_rear_adjustments", 0)
    total_adj = bb_adj + tc_adj + arb_f_adj + arb_r_adj

    if total_adj > 15 and profile.consistency == "consistent":
        # Driver was hunting for setup — consistency metric is unreliable
        profile.consistency = "variable"
        profile.style = _compute_style(
            profile.steering_smoothness,
            profile.consistency,
            profile.cornering_aggression,
        )
        profile.driver_noise_index = min(1.0, profile.driver_noise_index + 0.15)
        profile.classification_confidence = max(0.25, profile.classification_confidence - 0.1)


def separate_driver_noise(
    profile: DriverProfile,
    measured: object,
) -> tuple[float, float, str]:
    """Decompose observed noise into driver-caused vs setup-caused components.

    Returns:
        (driver_component, setup_component, reasoning)

    driver_component: 0-1, how much of the observed variance is driver behavior.
    setup_component: 0-1, how much is setup-induced instability.
    """
    # Driver-controlled factors (should be forgiven by the solver)
    driver_factors = (
        profile.apex_speed_cv * 6.0
        + profile.entry_speed_cv * 3.0
        + max(0.0, (profile.steering_jerk_p95_rad_per_s2 - 50.0) / 150.0)
        + max(0.0, (1.0 - profile.brake_release_quality) * 0.3)
    )
    driver_component = min(1.0, max(0.0, driver_factors))

    # Setup-induced instability symptoms (demand precise tuning)
    front_rh_std = getattr(measured, "front_rh_std_mm", 0.0) or 0.0
    rear_rh_std = getattr(measured, "rear_rh_std_mm", 0.0) or 0.0
    body_slip = getattr(measured, "body_slip_p95_deg", 0.0) or 0.0
    rear_slip = getattr(measured, "rear_power_slip_ratio_p95", 0.0) or 0.0
    front_lock = getattr(measured, "front_braking_lock_ratio_p95", 0.0) or 0.0
    front_travel = getattr(measured, "front_heave_travel_used_pct", 0.0) or 0.0

    setup_factors = (
        min(1.0, front_rh_std / 2.0) * 0.25
        + min(1.0, rear_rh_std / 2.5) * 0.15
        + min(1.0, body_slip / 3.0) * 0.20
        + min(1.0, rear_slip / 0.08) * 0.15
        + min(1.0, front_lock / 0.5) * 0.10
        + min(1.0, max(0.0, front_travel - 85) / 15) * 0.15
    )
    setup_component = min(1.0, max(0.0, setup_factors))

    parts = []
    if driver_component > 0.3:
        parts.append(f"driver variance={driver_component:.2f}")
    if setup_component > 0.3:
        parts.append(f"setup instability={setup_component:.2f}")
    reasoning = "; ".join(parts) if parts else "low noise overall"

    return (round(driver_component, 3), round(setup_component, 3), reasoning)
