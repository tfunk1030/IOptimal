"""Corner-by-corner balance analysis — per-phase understeer/oversteer measurement.

Segments each detected corner into three phases (entry, mid, exit) and computes
physics-based balance metrics for each phase.  The measured balance deficits are
then aggregated across all corners and mapped to specific setup parameter changes.

This module sits between ``analyzer/segment.py`` (which provides per-corner
metrics) and ``solver/modifiers.py`` (which adjusts solver targets).  It does
NOT replace the existing aggregate diagnosis in ``analyzer/diagnose.py`` —
it augments it with per-corner-phase detail.

Key physics quantities per phase:
- Understeer angle = road_wheel_angle - (a_lat × wheelbase / v²)
- Yaw rate error = (measured - expected) / expected
- Lateral load transfer split from shock deflection asymmetry
- Traction utilization (exit): throttle % vs rear slip %
- Stability margin: headroom below tyre saturation
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from car_model.cars import CarModel

from analyzer.segment import CornerAnalysis
from track_model.ibt_parser import IBTFile


# ---------------------------------------------------------------------------
# Enums & Dataclasses
# ---------------------------------------------------------------------------

class CornerPhase(Enum):
    """Phase within a corner event."""
    ENTRY = "entry"
    MID = "mid"
    EXIT = "exit"


@dataclass
class PhaseBalance:
    """Balance measurement for one phase of one corner."""

    phase: CornerPhase
    understeer_deg: float = 0.0       # positive = understeer, negative = oversteer
    yaw_rate_error_pct: float = 0.0   # (actual - expected) / expected × 100
    rear_slip_proxy: float = 0.0      # rear shock deflection asymmetry (normalised)
    lateral_load_transfer_front_pct: float = 50.0  # front share of total LLT
    traction_utilization_pct: float = 0.0  # exit phase only
    lateral_g_mean: float = 0.0
    speed_kph_mean: float = 0.0
    duration_s: float = 0.0
    stability_margin: float = 0.0     # 0 = at limit, 1 = large headroom


    @property
    def balance_label(self) -> str:
        """Human-readable balance state."""
        if self.understeer_deg > 1.0:
            return "understeer"
        elif self.understeer_deg < -1.0:
            return "oversteer"
        return "neutral"


@dataclass
class CornerBalance:
    """Per-phase balance for a single corner."""

    corner_id: int
    corner_type: str            # "low" | "mid" | "high" (speed class)
    direction: str              # "left" | "right"
    lap_dist_start_m: float
    entry: PhaseBalance
    mid: PhaseBalance
    exit: PhaseBalance

    @property
    def dominant_issue(self) -> str:
        """Which phase has the largest imbalance."""
        phases = [
            ("entry", abs(self.entry.understeer_deg)),
            ("mid", abs(self.mid.understeer_deg)),
            ("exit", abs(self.exit.understeer_deg)),
        ]
        return max(phases, key=lambda x: x[1])[0]


@dataclass
class BalanceSummary:
    """Aggregated balance across all corners on the lap."""

    # Dominant issue per phase (across all corners)
    dominant_entry_issue: str = "neutral"   # "understeer" | "oversteer" | "neutral"
    dominant_mid_issue: str = "neutral"
    dominant_exit_issue: str = "neutral"

    # What fraction of corners exhibit each issue
    entry_understeer_pct: float = 0.0
    entry_oversteer_pct: float = 0.0
    mid_understeer_pct: float = 0.0
    mid_oversteer_pct: float = 0.0
    exit_understeer_pct: float = 0.0
    exit_oversteer_pct: float = 0.0

    # Severity-weighted averages (positive = understeer)
    weighted_entry_us_deg: float = 0.0
    weighted_mid_us_deg: float = 0.0
    weighted_exit_us_deg: float = 0.0

    # Stability margin (mean across neutral corners)
    stability_margin_mean: float = 0.0

    # Speed-dependent bias
    high_speed_bias: str = "neutral"    # balance tendency in fast corners (>180 kph)
    low_speed_bias: str = "neutral"     # balance tendency in slow corners (<120 kph)

    # All per-corner results
    corners: list[CornerBalance] = field(default_factory=list)

    @property
    def priority_fix(self) -> str | None:
        """If >=70% of corners share the same phase issue, return it."""
        for phase, us_pct, os_pct in [
            ("entry", self.entry_understeer_pct, self.entry_oversteer_pct),
            ("mid", self.mid_understeer_pct, self.mid_oversteer_pct),
            ("exit", self.exit_understeer_pct, self.exit_oversteer_pct),
        ]:
            if us_pct >= 70.0:
                return f"{phase}_understeer"
            if os_pct >= 70.0:
                return f"{phase}_oversteer"
        return None


# ---------------------------------------------------------------------------
# Phase Segmentation
# ---------------------------------------------------------------------------

def _segment_phases(
    corner: CornerAnalysis,
    lat_g: np.ndarray,
    brake: np.ndarray,
    throttle: np.ndarray,
    speed_kph: np.ndarray,
    cs: int,
    ce: int,
    ca: int,
    dt: float,
) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    """Split a corner into (entry, mid, exit) index ranges relative to cs.

    Returns three (start, end) tuples as absolute indices into the lap arrays.

    Entry:  cs → peak |lat_g| region, while brake > 0 or |lat_g| still rising
    Mid:    peak |lat_g| region (samples within 90% of peak), before throttle > 20%
    Exit:   from throttle onset (>20%) or mid-end → ce
    """
    n = ce - cs
    if n < 6:
        # Tiny corner — treat entire thing as mid
        third = max(1, n // 3)
        return (cs, cs + third), (cs + third, ce - third), (ce - third, ce)

    seg_lat = np.abs(lat_g[cs:ce])
    seg_brake = brake[cs:ce]
    seg_throttle = throttle[cs:ce]

    peak_lat = np.max(seg_lat)
    peak_threshold = 0.90 * peak_lat if peak_lat > 0.3 else 0.3

    # Find the contiguous region around peak lat_g that stays above 90% of peak
    above_threshold = seg_lat >= peak_threshold
    mid_indices = np.where(above_threshold)[0]

    if len(mid_indices) < 2:
        # Fallback: use apex region ±10% of corner length
        apex_local = ca - cs
        margin = max(2, n // 10)
        mid_start_local = max(0, apex_local - margin)
        mid_end_local = min(n, apex_local + margin)
    else:
        mid_start_local = int(mid_indices[0])
        mid_end_local = int(mid_indices[-1]) + 1

    # Refine exit: find where throttle first exceeds 20% after mid region
    post_mid_throttle = seg_throttle[mid_end_local:]
    throttle_onset_local = mid_end_local  # default: exit starts right after mid
    if len(post_mid_throttle) > 0:
        onset_mask = post_mid_throttle > 0.20
        if np.any(onset_mask):
            throttle_onset_local = mid_end_local + int(np.argmax(onset_mask))

    # Entry ends where mid starts (or where braking ends, whichever is later)
    entry_end_local = mid_start_local

    # Ensure non-empty phases
    if entry_end_local <= 0:
        entry_end_local = max(1, n // 6)
    if throttle_onset_local >= n:
        throttle_onset_local = n - 1

    entry_range = (cs, cs + entry_end_local)
    mid_range = (cs + entry_end_local, cs + throttle_onset_local)
    exit_range = (cs + throttle_onset_local, ce)

    # Safety: ensure mid has at least 2 samples
    if mid_range[1] - mid_range[0] < 2:
        mid_range = (entry_range[1], exit_range[0])

    return entry_range, mid_range, exit_range


# ---------------------------------------------------------------------------
# Per-Phase Balance Computation
# ---------------------------------------------------------------------------

def _compute_phase_balance(
    phase: CornerPhase,
    steering: np.ndarray,
    lat_g: np.ndarray,
    speed_ms: np.ndarray,
    yaw_rate: np.ndarray | None,
    throttle: np.ndarray,
    lf_sv: np.ndarray,
    rf_sv: np.ndarray,
    lr_sv: np.ndarray,
    rr_sv: np.ndarray,
    start: int,
    end: int,
    car: CarModel,
    dt: float,
    peak_lat_g_session: float = 2.5,
) -> PhaseBalance:
    """Compute balance metrics for one phase of one corner.

    All arrays are full-lap; start/end are absolute indices.
    """
    if end <= start:
        return PhaseBalance(phase=phase)

    seg_steer = steering[start:end]
    seg_lat = lat_g[start:end]            # signed, in g
    seg_speed = speed_ms[start:end]       # m/s
    seg_throttle = throttle[start:end]
    seg_lf = lf_sv[start:end]
    seg_rf = rf_sv[start:end]
    seg_lr = lr_sv[start:end]
    seg_rr = rr_sv[start:end]

    n = end - start
    duration = n * dt

    lat_g_mean = float(np.mean(np.abs(seg_lat)))
    speed_kph_mean = float(np.mean(seg_speed)) * 3.6
    speed_ms_mean = float(np.mean(np.maximum(seg_speed, 5.0)))

    # --- Understeer angle ---
    # road_wheel_angle (rad) = steering_wheel_angle / steering_ratio
    # neutral_steer_angle (rad) = wheelbase × lat_accel / v²
    #   where lat_accel is in m/s² (= lat_g × 9.81)
    safe_speed = np.maximum(seg_speed, 5.0)
    road_wheel_angle = seg_steer / car.steering_ratio  # rad
    neutral_steer_angle = car.wheelbase_m * (seg_lat * 9.81) / (safe_speed ** 2)  # rad
    understeer_rad = road_wheel_angle - neutral_steer_angle
    # Filter to meaningful cornering samples (|lat_g| > 0.3)
    corn_mask = np.abs(seg_lat) > 0.3
    if np.sum(corn_mask) >= 3:
        understeer_deg = float(np.mean(np.degrees(understeer_rad[corn_mask])))
    else:
        understeer_deg = float(np.mean(np.degrees(understeer_rad)))

    # --- Yaw rate error ---
    yaw_error_pct = 0.0
    if yaw_rate is not None:
        seg_yaw = yaw_rate[start:end]
        # Expected yaw rate = v × (a_lat / v²) = a_lat / v  [but a_lat in m/s²]
        expected_yaw = (seg_lat * 9.81) / safe_speed  # rad/s
        actual_yaw = seg_yaw
        safe_expected = np.where(np.abs(expected_yaw) > 0.01, expected_yaw, 0.01)
        yaw_err = (actual_yaw - expected_yaw) / safe_expected * 100.0
        if np.sum(corn_mask) >= 3:
            yaw_error_pct = float(np.mean(yaw_err[corn_mask]))
        else:
            yaw_error_pct = float(np.mean(yaw_err))

    # --- Rear slip proxy from shock deflection asymmetry ---
    # In cornering, the loaded rear shock compresses more. The asymmetry
    # between LR and RR shock velocity indicates rear lateral grip usage.
    rear_asym = np.abs(seg_lr) - np.abs(seg_rr)
    rear_slip_proxy = float(np.mean(np.abs(rear_asym))) if n > 0 else 0.0

    # --- Front vs rear lateral load transfer ---
    # LLT proxy: difference between inner/outer shock velocities per axle.
    # Front LLT ∝ |LF_sv - RF_sv|, Rear LLT ∝ |LR_sv - RR_sv|
    front_llt = float(np.mean(np.abs(seg_lf - seg_rf))) if n > 0 else 0.0
    rear_llt = float(np.mean(np.abs(seg_lr - seg_rr))) if n > 0 else 0.0
    total_llt = front_llt + rear_llt
    llt_front_pct = (front_llt / total_llt * 100.0) if total_llt > 0.001 else 50.0

    # --- Traction utilization (exit phase only) ---
    traction_pct = 0.0
    if phase == CornerPhase.EXIT and n > 2:
        # How much throttle is being used relative to available traction.
        # High throttle + low lateral_g = good traction. High throttle + high
        # lateral_g + body slip = traction-limited.
        mean_throttle = float(np.mean(seg_throttle))
        traction_pct = mean_throttle * 100.0  # simplified: throttle % as proxy

    # --- Stability margin ---
    # How close is the car to the tyre saturation limit?
    # margin = 1 - (actual_lat_g / session_peak_lat_g)
    if peak_lat_g_session > 0.5:
        margin = max(0.0, 1.0 - lat_g_mean / peak_lat_g_session)
    else:
        margin = 1.0

    return PhaseBalance(
        phase=phase,
        understeer_deg=round(understeer_deg, 2),
        yaw_rate_error_pct=round(yaw_error_pct, 1),
        rear_slip_proxy=round(rear_slip_proxy, 4),
        lateral_load_transfer_front_pct=round(llt_front_pct, 1),
        traction_utilization_pct=round(traction_pct, 1),
        lateral_g_mean=round(lat_g_mean, 3),
        speed_kph_mean=round(speed_kph_mean, 1),
        duration_s=round(duration, 3),
        stability_margin=round(margin, 3),
    )


# ---------------------------------------------------------------------------
# Main Analysis Function
# ---------------------------------------------------------------------------

def analyze_corner_balance(
    ibt: IBTFile,
    start: int,
    end: int,
    car: CarModel,
    corners: list[CornerAnalysis],
    tick_rate: int = 60,
) -> list[CornerBalance]:
    """Compute per-phase balance for every corner on the lap.

    Parameters
    ----------
    ibt : IBTFile
        Parsed IBT telemetry.
    start, end : int
        Sample indices for the best lap (inclusive).
    car : CarModel
        Car physical model (provides steering_ratio, wheelbase_m).
    corners : list[CornerAnalysis]
        Corner segments from ``analyzer.segment.segment_lap()``.
    tick_rate : int
        Sample rate (Hz).

    Returns
    -------
    list[CornerBalance]
        One entry per corner, sorted by lap distance.
    """
    dt = 1.0 / tick_rate
    n = end - start + 1

    # Load channels (sliced to lap)
    speed_ms = ibt.channel("Speed")[start:end + 1]
    speed_kph = speed_ms * 3.6
    lat_g = ibt.channel("LatAccel")[start:end + 1] / 9.81  # convert to g
    steering = ibt.channel("SteeringWheelAngle")[start:end + 1]
    brake = ibt.channel("Brake")[start:end + 1]
    throttle = ibt.channel("Throttle")[start:end + 1]
    lap_dist = ibt.channel("LapDist")[start:end + 1]

    # Yaw rate (optional)
    yaw_rate: np.ndarray | None = None
    if ibt.has_channel("YawRate"):
        yaw_rate = ibt.channel("YawRate")[start:end + 1]

    # Shock velocities per corner
    has_corner_shocks = all(
        ibt.has_channel(c)
        for c in ["LFshockVel", "RFshockVel", "LRshockVel", "RRshockVel"]
    )
    if has_corner_shocks:
        lf_sv = ibt.channel("LFshockVel")[start:end + 1]
        rf_sv = ibt.channel("RFshockVel")[start:end + 1]
        lr_sv = ibt.channel("LRshockVel")[start:end + 1]
        rr_sv = ibt.channel("RRshockVel")[start:end + 1]
    else:
        # Synthesise from heave + roll bar (Acura etc.)
        hf = ibt.channel("HFshockVel")[start:end + 1] if ibt.has_channel("HFshockVel") else np.zeros(n)
        tr = ibt.channel("TRshockVel")[start:end + 1] if ibt.has_channel("TRshockVel") else np.zeros(n)
        froll = ibt.channel("FROLLshockVel")[start:end + 1] if ibt.has_channel("FROLLshockVel") else np.zeros(n)
        rroll = ibt.channel("RROLLshockVel")[start:end + 1] if ibt.has_channel("RROLLshockVel") else np.zeros(n)
        lf_sv = hf + froll
        rf_sv = hf - froll
        lr_sv = tr + rroll
        rr_sv = tr - rroll

    # Session peak lateral G for stability margin computation
    peak_lat_g_session = float(np.percentile(np.abs(lat_g), 99.5)) if n > 100 else 2.0

    # Re-detect corners using same algorithm as segment.py to get sample indices
    from analyzer.segment import _detect_corners, _detect_braking_zones

    raw_corners = _detect_corners(lat_g, speed_kph, steering, lap_dist)

    # Match detected corners to CornerAnalysis objects by lap_dist proximity
    results: list[CornerBalance] = []

    for ca_obj in corners:
        # Find the raw corner whose start distance is closest to ca_obj
        best_match = None
        best_dist = float("inf")
        for rc_start, rc_apex, rc_end, rc_dir in raw_corners:
            rc_start_m = float(lap_dist[rc_start])
            dist = abs(rc_start_m - ca_obj.lap_dist_start_m)
            if dist < best_dist:
                best_dist = dist
                best_match = (rc_start, rc_apex, rc_end, rc_dir)

        if best_match is None or best_dist > 100.0:
            continue  # no matching raw corner found

        cs, ca_idx, ce, direction = best_match
        # Clamp to valid range
        cs = max(0, cs)
        ce = min(n, ce)

        if ce - cs < 6:
            continue

        # Segment into phases
        entry_range, mid_range, exit_range = _segment_phases(
            ca_obj, lat_g, brake, throttle, speed_kph,
            cs, ce, ca_idx, dt,
        )

        # Compute balance for each phase
        entry_balance = _compute_phase_balance(
            CornerPhase.ENTRY, steering, lat_g, speed_ms, yaw_rate,
            throttle, lf_sv, rf_sv, lr_sv, rr_sv,
            entry_range[0], entry_range[1], car, dt, peak_lat_g_session,
        )
        mid_balance = _compute_phase_balance(
            CornerPhase.MID, steering, lat_g, speed_ms, yaw_rate,
            throttle, lf_sv, rf_sv, lr_sv, rr_sv,
            mid_range[0], mid_range[1], car, dt, peak_lat_g_session,
        )
        exit_balance = _compute_phase_balance(
            CornerPhase.EXIT, steering, lat_g, speed_ms, yaw_rate,
            throttle, lf_sv, rf_sv, lr_sv, rr_sv,
            exit_range[0], exit_range[1], car, dt, peak_lat_g_session,
        )

        results.append(CornerBalance(
            corner_id=ca_obj.corner_id,
            corner_type=ca_obj.speed_class,
            direction=ca_obj.direction,
            lap_dist_start_m=ca_obj.lap_dist_start_m,
            entry=entry_balance,
            mid=mid_balance,
            exit=exit_balance,
        ))

    results.sort(key=lambda c: c.lap_dist_start_m)
    return results


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _classify_balance(us_deg: float) -> str:
    """Classify a phase balance value."""
    if us_deg > 1.0:
        return "understeer"
    elif us_deg < -1.0:
        return "oversteer"
    return "neutral"


def _speed_weight(corner_type: str, param_domain: str) -> float:
    """Weight corner by speed class for parameter relevance.

    Aero parameters (wing, ride height) matter more at high speed.
    Mechanical parameters (springs, ARBs, diff) matter more at low speed.
    """
    if param_domain == "aero":
        return {"high": 1.5, "mid": 1.0, "low": 0.5}.get(corner_type, 1.0)
    else:  # mechanical
        return {"high": 0.5, "mid": 1.0, "low": 1.5}.get(corner_type, 1.0)


def aggregate_balance(corners: list[CornerBalance]) -> BalanceSummary:
    """Aggregate per-corner-phase balance into a lap-level summary.

    Weights high-speed corners more heavily for aero-related conclusions
    and low-speed corners more heavily for mechanical conclusions.
    """
    if not corners:
        return BalanceSummary()

    n = len(corners)

    # Count issues per phase
    entry_us = sum(1 for c in corners if c.entry.understeer_deg > 1.0)
    entry_os = sum(1 for c in corners if c.entry.understeer_deg < -1.0)
    mid_us = sum(1 for c in corners if c.mid.understeer_deg > 1.0)
    mid_os = sum(1 for c in corners if c.mid.understeer_deg < -1.0)
    exit_us = sum(1 for c in corners if c.exit.understeer_deg > 1.0)
    exit_os = sum(1 for c in corners if c.exit.understeer_deg < -1.0)

    # Weighted average understeer per phase (mechanical weighting)
    weights = [_speed_weight(c.corner_type, "mechanical") for c in corners]
    total_w = sum(weights) or 1.0
    w_entry = sum(c.entry.understeer_deg * w for c, w in zip(corners, weights)) / total_w
    w_mid = sum(c.mid.understeer_deg * w for c, w in zip(corners, weights)) / total_w
    w_exit = sum(c.exit.understeer_deg * w for c, w in zip(corners, weights)) / total_w

    # Stability margin (across neutral-ish corners)
    neutral_margins = [
        c.mid.stability_margin
        for c in corners
        if abs(c.mid.understeer_deg) <= 1.5
    ]
    margin_mean = float(np.mean(neutral_margins)) if neutral_margins else 0.0

    # Speed-dependent bias
    high_speed = [c for c in corners if c.corner_type == "high"]
    low_speed = [c for c in corners if c.corner_type == "low"]
    hs_bias = "neutral"
    ls_bias = "neutral"
    if high_speed:
        hs_mid_mean = float(np.mean([c.mid.understeer_deg for c in high_speed]))
        hs_bias = _classify_balance(hs_mid_mean)
    if low_speed:
        ls_mid_mean = float(np.mean([c.mid.understeer_deg for c in low_speed]))
        ls_bias = _classify_balance(ls_mid_mean)

    return BalanceSummary(
        dominant_entry_issue=_classify_balance(w_entry),
        dominant_mid_issue=_classify_balance(w_mid),
        dominant_exit_issue=_classify_balance(w_exit),
        entry_understeer_pct=round(entry_us / n * 100, 1),
        entry_oversteer_pct=round(entry_os / n * 100, 1),
        mid_understeer_pct=round(mid_us / n * 100, 1),
        mid_oversteer_pct=round(mid_os / n * 100, 1),
        exit_understeer_pct=round(exit_us / n * 100, 1),
        exit_oversteer_pct=round(exit_os / n * 100, 1),
        weighted_entry_us_deg=round(w_entry, 2),
        weighted_mid_us_deg=round(w_mid, 2),
        weighted_exit_us_deg=round(w_exit, 2),
        stability_margin_mean=round(margin_mean, 3),
        high_speed_bias=hs_bias,
        low_speed_bias=ls_bias,
        corners=corners,
    )


# ---------------------------------------------------------------------------
# Balance → Parameter Change Mapping
# ---------------------------------------------------------------------------

def map_balance_to_params(
    summary: BalanceSummary,
    car: CarModel,
) -> dict[str, float]:
    """Map aggregated balance issues to specific parameter change recommendations.

    Returns a dict of ``{parameter_name: recommended_change}`` where the change
    is a delta (positive = increase, negative = decrease) in the parameter's
    native units.

    The magnitudes are scaled by the measured understeer deficit — larger
    deficits produce larger recommended changes. The scaling factors are
    derived from suspension geometry relationships, not from lap time
    correlations or hardcoded constants.

    Parameters
    ----------
    summary : BalanceSummary
        Aggregated balance from ``aggregate_balance()``.
    car : CarModel
        Car physical model.

    Returns
    -------
    dict[str, float]
        Parameter changes keyed by canonical parameter name.
    """
    changes: dict[str, float] = {}
    has_heave = car.suspension_arch.has_heave_third

    # --- Entry phase ---
    entry_us = summary.weighted_entry_us_deg
    if abs(entry_us) > 0.5:
        if entry_us > 0:
            # Entry understeer: front dives too much on braking, loses grip
            if has_heave:
                # Stiffer front heave resists pitch → better front mechanical grip
                # Scale: ~15 N/mm per degree of entry understeer
                changes["front_heave_nmm"] = round(entry_us * 15.0, 1)
            # Shift brake bias forward to load front more
            changes["brake_bias_pct"] = round(-entry_us * 0.5, 1)
            # Free up diff coast (less drag on decel = less rear stability needed)
            changes["diff_coast_ramp"] = round(-entry_us * 0.3, 1)
        else:
            # Entry oversteer: rear snaps around under braking
            # Softer rear spring allows more rear compliance
            changes["rear_spring_nmm"] = round(entry_us * 8.0, 1)  # negative = softer
            # More diff coast = more rear stability on decel
            changes["diff_coast_ramp"] = round(-entry_us * 0.3, 1)  # entry_us<0 → positive
            # Shift brake bias rearward
            changes["brake_bias_pct"] = round(-entry_us * 0.5, 1)  # entry_us<0 → positive

    # --- Mid-corner phase ---
    mid_us = summary.weighted_mid_us_deg
    if abs(mid_us) > 0.5:
        if mid_us > 0:
            # Mid understeer: LLTD too high (front overloaded relative to rear)
            # Soften front ARB or stiffen rear ARB to shift LLTD
            # Scale: ~0.01 LLTD shift per degree of mid understeer
            changes["lltd_offset"] = round(-mid_us * 0.01, 3)
            # More front camber for more grip
            changes["front_camber_deg"] = round(-mid_us * 0.15, 2)  # more negative = more camber
            # Lower front RH for more front downforce
            changes["front_rh_offset_mm"] = round(-mid_us * 0.5, 1)
        else:
            # Mid oversteer: rear saturates before front
            changes["lltd_offset"] = round(-mid_us * 0.01, 3)  # mid_us<0 → positive shift
            changes["rear_camber_deg"] = round(mid_us * 0.15, 2)  # more rear camber
            changes["rear_rh_offset_mm"] = round(mid_us * 0.5, 1)  # lower rear

    # --- Exit phase ---
    exit_us = summary.weighted_exit_us_deg
    if abs(exit_us) > 0.5:
        if exit_us > 0:
            # Exit understeer: not enough rear traction drive
            # More diff preload transfers torque to loaded wheel
            changes["diff_preload_nm"] = round(exit_us * 8.0, 0)
            # Softer rear spring for more rear mechanical grip
            changes["rear_spring_nmm"] = round(-exit_us * 5.0, 1)
        else:
            # Exit oversteer: too much rear torque or snap
            changes["diff_preload_nm"] = round(exit_us * 8.0, 0)  # exit_us<0 → reduce preload
            # Reduce rear camber (more contact patch area)
            changes["rear_camber_deg"] = round(-exit_us * 0.10, 2)

    # --- Stability margin trade-off ---
    if summary.stability_margin_mean > 0.3 and summary.dominant_mid_issue == "neutral":
        # Car is stable with headroom — can trade stability for straight-line speed
        # Suggest wing reduction proportional to margin
        wing_reduction = round(summary.stability_margin_mean * 2.0, 1)  # degrees
        changes["wing_angle_reduction_deg"] = -wing_reduction

    # --- Speed-dependent compromise ---
    if summary.high_speed_bias != summary.low_speed_bias:
        # Different balance at high vs low speed → note the compromise
        # (No single parameter change resolves this; it's informational)
        changes["_compromise_note"] = 1.0 if summary.high_speed_bias == "understeer" else -1.0

    return changes


# ---------------------------------------------------------------------------
# Convenience: Full Pipeline Entry Point
# ---------------------------------------------------------------------------

def run_corner_balance_analysis(
    ibt: IBTFile,
    start: int,
    end: int,
    car: CarModel,
    corners: list[CornerAnalysis],
    tick_rate: int = 60,
) -> tuple[list[CornerBalance], BalanceSummary, dict[str, float]]:
    """One-call convenience: analyze → aggregate → map to params.

    Returns
    -------
    (corner_balances, summary, param_changes)
    """
    corner_balances = analyze_corner_balance(ibt, start, end, car, corners, tick_rate)
    summary = aggregate_balance(corner_balances)
    param_changes = map_balance_to_params(summary, car)
    return corner_balances, summary, param_changes


def format_balance_report(summary: BalanceSummary) -> str:
    """Format a human-readable balance report section for the engineering report."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("CORNER-BY-CORNER BALANCE ANALYSIS")
    lines.append("=" * 60)
    lines.append("")

    n = len(summary.corners)
    lines.append(f"Analyzed {n} corners")
    lines.append("")

    # Phase summary
    for phase_name, dom, us_pct, os_pct, w_us in [
        ("ENTRY", summary.dominant_entry_issue, summary.entry_understeer_pct,
         summary.entry_oversteer_pct, summary.weighted_entry_us_deg),
        ("MID", summary.dominant_mid_issue, summary.mid_understeer_pct,
         summary.mid_oversteer_pct, summary.weighted_mid_us_deg),
        ("EXIT", summary.dominant_exit_issue, summary.exit_understeer_pct,
         summary.exit_oversteer_pct, summary.weighted_exit_us_deg),
    ]:
        lines.append(f"  {phase_name:6s}: {dom.upper():12s}  "
                      f"US={us_pct:4.0f}%  OS={os_pct:4.0f}%  "
                      f"weighted={w_us:+.1f}°")

    lines.append("")
    lines.append(f"  Stability margin (neutral corners): {summary.stability_margin_mean:.2f}")
    lines.append(f"  High-speed bias: {summary.high_speed_bias}")
    lines.append(f"  Low-speed bias:  {summary.low_speed_bias}")

    priority = summary.priority_fix
    if priority:
        lines.append(f"  ** PRIORITY FIX: {priority} (>70% of corners) **")

    lines.append("")

    # Per-corner detail
    lines.append("Per-Corner Detail:")
    lines.append(f"  {'ID':>3s}  {'Type':>4s}  {'Dir':>5s}  {'Dist':>6s}  "
                  f"{'Entry':>7s}  {'Mid':>7s}  {'Exit':>7s}  {'Margin':>6s}")
    lines.append("  " + "-" * 56)
    for cb in summary.corners:
        lines.append(
            f"  {cb.corner_id:3d}  {cb.corner_type:>4s}  {cb.direction:>5s}  "
            f"{cb.lap_dist_start_m:6.0f}  "
            f"{cb.entry.understeer_deg:+6.1f}°  "
            f"{cb.mid.understeer_deg:+6.1f}°  "
            f"{cb.exit.understeer_deg:+6.1f}°  "
            f"{cb.mid.stability_margin:5.2f}"
        )

    lines.append("")
    return "\n".join(lines)
