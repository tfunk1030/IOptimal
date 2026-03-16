"""Stint-level telemetry analysis — multi-lap extraction and evolution tracking.

Analyzes all representative laps in a stint (within a configurable threshold
of the fastest lap time), tracks how conditions evolve from beginning to end,
and computes actual degradation rates from telemetry.

Usage:
    from analyzer.stint_analysis import analyze_stint_evolution

    evolution = analyze_stint_evolution(
        ibt_path="session.ibt", car=car,
        threshold_pct=1.5, min_lap_time=108.0,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from car_model.cars import CarModel
    from track_model.ibt_parser import IBTFile


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class LapSnapshot:
    """Stint-relevant telemetry from one lap (subset of MeasuredState)."""

    lap_number: int = 0
    lap_time_s: float = 0.0
    fuel_level_l: float = 0.0

    # Per-corner tire pressures (hot, kPa)
    lf_pressure_kpa: float = 0.0
    rf_pressure_kpa: float = 0.0
    lr_pressure_kpa: float = 0.0
    rr_pressure_kpa: float = 0.0

    # Per-corner tire wear (%)
    lf_wear_pct: float = 0.0
    rf_wear_pct: float = 0.0
    lr_wear_pct: float = 0.0
    rr_wear_pct: float = 0.0

    # Axle-average tire data
    front_carcass_mean_c: float = 0.0
    rear_carcass_mean_c: float = 0.0
    front_pressure_mean_kpa: float = 0.0
    rear_pressure_mean_kpa: float = 0.0

    # Per-corner surface temps (inner/mid/outer)
    lf_temp_inner_c: float = 0.0
    lf_temp_middle_c: float = 0.0
    lf_temp_outer_c: float = 0.0
    rf_temp_inner_c: float = 0.0
    rf_temp_middle_c: float = 0.0
    rf_temp_outer_c: float = 0.0
    lr_temp_inner_c: float = 0.0
    lr_temp_middle_c: float = 0.0
    lr_temp_outer_c: float = 0.0
    rr_temp_inner_c: float = 0.0
    rr_temp_middle_c: float = 0.0
    rr_temp_outer_c: float = 0.0

    # Balance & grip
    understeer_mean_deg: float = 0.0
    body_slip_p95_deg: float = 0.0
    peak_lat_g_measured: float = 0.0
    rear_slip_ratio_p95: float = 0.0

    # Platform
    mean_front_rh_at_speed_mm: float = 0.0
    mean_rear_rh_at_speed_mm: float = 0.0
    front_rh_std_mm: float = 0.0
    rear_rh_std_mm: float = 0.0
    front_shock_vel_p95_mps: float = 0.0
    rear_shock_vel_p95_mps: float = 0.0
    bottoming_event_count_front: int = 0
    bottoming_event_count_rear: int = 0

    # Environment
    air_temp_c: float = 0.0
    track_temp_c: float = 0.0


@dataclass
class DegradationRates:
    """Telemetry-measured rates of change per lap (from linear regression)."""

    fuel_burn_l_per_lap: float = 0.0
    front_pressure_kpa_per_lap: float = 0.0
    rear_pressure_kpa_per_lap: float = 0.0
    front_wear_pct_per_lap: float = 0.0
    rear_wear_pct_per_lap: float = 0.0
    understeer_deg_per_lap: float = 0.0
    peak_lat_g_per_lap: float = 0.0
    front_carcass_c_per_lap: float = 0.0
    rear_carcass_c_per_lap: float = 0.0
    track_temp_c_per_lap: float = 0.0
    front_rh_mm_per_lap: float = 0.0
    rear_rh_mm_per_lap: float = 0.0
    lap_time_s_per_lap: float = 0.0

    # R² confidence per metric
    r_squared: dict[str, float] = field(default_factory=dict)


@dataclass
class StintEvolution:
    """Complete stint evolution from telemetry — beginning to end."""

    snapshots: list[LapSnapshot] = field(default_factory=list)
    rates: DegradationRates | None = None
    qualifying_lap_count: int = 0
    total_lap_count: int = 0
    threshold_pct: float = 1.5
    fastest_lap_time_s: float = 0.0

    # Three key operating points for multi-solve
    start_snapshot: LapSnapshot = field(default_factory=LapSnapshot)
    mid_snapshot: LapSnapshot = field(default_factory=LapSnapshot)
    end_snapshot: LapSnapshot = field(default_factory=LapSnapshot)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def filter_qualifying_laps(
    ibt: IBTFile,
    threshold_pct: float = 1.5,
    min_lap_time: float = 108.0,
) -> list[tuple[int, float, int, int]]:
    """Return laps within *threshold_pct* of the fastest valid lap.

    Args:
        ibt: Parsed IBT file.
        threshold_pct: Maximum % slower than fastest to include (default 1.5%).
        min_lap_time: Hard floor — laps shorter than this are excluded.

    Returns:
        List of (lap_num, lap_time_s, start_idx, end_idx) sorted by lap_number.
    """
    all_laps = ibt.lap_times(min_time=min_lap_time)
    if not all_laps:
        return []

    fastest = min(lt for _, lt, _, _ in all_laps)
    cutoff = fastest * (1.0 + threshold_pct / 100.0)

    qualifying = [
        (ln, lt, s, e)
        for ln, lt, s, e in all_laps
        if lt <= cutoff
    ]
    qualifying.sort(key=lambda t: t[0])  # chronological
    return qualifying


def _snapshot_from_measured(measured) -> LapSnapshot:
    """Map a MeasuredState to a LapSnapshot (stint-relevant fields only)."""
    return LapSnapshot(
        lap_number=getattr(measured, "lap_number", 0),
        lap_time_s=getattr(measured, "lap_time_s", 0.0),
        fuel_level_l=getattr(measured, "fuel_level_at_measurement_l", 0.0),
        # Per-corner pressures
        lf_pressure_kpa=getattr(measured, "lf_pressure_kpa", 0.0),
        rf_pressure_kpa=getattr(measured, "rf_pressure_kpa", 0.0),
        lr_pressure_kpa=getattr(measured, "lr_pressure_kpa", 0.0),
        rr_pressure_kpa=getattr(measured, "rr_pressure_kpa", 0.0),
        # Per-corner wear
        lf_wear_pct=getattr(measured, "lf_wear_pct", 0.0),
        rf_wear_pct=getattr(measured, "rf_wear_pct", 0.0),
        lr_wear_pct=getattr(measured, "lr_wear_pct", 0.0),
        rr_wear_pct=getattr(measured, "rr_wear_pct", 0.0),
        # Axle averages
        front_carcass_mean_c=getattr(measured, "front_carcass_mean_c", 0.0),
        rear_carcass_mean_c=getattr(measured, "rear_carcass_mean_c", 0.0),
        front_pressure_mean_kpa=getattr(measured, "front_pressure_mean_kpa", 0.0),
        rear_pressure_mean_kpa=getattr(measured, "rear_pressure_mean_kpa", 0.0),
        # Per-corner surface temps
        lf_temp_inner_c=getattr(measured, "lf_temp_inner_c", 0.0),
        lf_temp_middle_c=getattr(measured, "lf_temp_middle_c", 0.0),
        lf_temp_outer_c=getattr(measured, "lf_temp_outer_c", 0.0),
        rf_temp_inner_c=getattr(measured, "rf_temp_inner_c", 0.0),
        rf_temp_middle_c=getattr(measured, "rf_temp_middle_c", 0.0),
        rf_temp_outer_c=getattr(measured, "rf_temp_outer_c", 0.0),
        lr_temp_inner_c=getattr(measured, "lr_temp_inner_c", 0.0),
        lr_temp_middle_c=getattr(measured, "lr_temp_middle_c", 0.0),
        lr_temp_outer_c=getattr(measured, "lr_temp_outer_c", 0.0),
        rr_temp_inner_c=getattr(measured, "rr_temp_inner_c", 0.0),
        rr_temp_middle_c=getattr(measured, "rr_temp_middle_c", 0.0),
        rr_temp_outer_c=getattr(measured, "rr_temp_outer_c", 0.0),
        # Balance & grip
        understeer_mean_deg=getattr(measured, "understeer_mean_deg", 0.0),
        body_slip_p95_deg=getattr(measured, "body_slip_p95_deg", 0.0),
        peak_lat_g_measured=getattr(measured, "peak_lat_g_measured", 0.0),
        rear_slip_ratio_p95=getattr(measured, "rear_power_slip_ratio_p95", 0.0),
        # Platform
        mean_front_rh_at_speed_mm=getattr(measured, "mean_front_rh_at_speed_mm", 0.0),
        mean_rear_rh_at_speed_mm=getattr(measured, "mean_rear_rh_at_speed_mm", 0.0),
        front_rh_std_mm=getattr(measured, "front_rh_std_mm", 0.0),
        rear_rh_std_mm=getattr(measured, "rear_rh_std_mm", 0.0),
        front_shock_vel_p95_mps=getattr(measured, "front_shock_vel_p95_mps", 0.0),
        rear_shock_vel_p95_mps=getattr(measured, "rear_shock_vel_p95_mps", 0.0),
        bottoming_event_count_front=getattr(measured, "bottoming_event_count_front", 0),
        bottoming_event_count_rear=getattr(measured, "bottoming_event_count_rear", 0),
        # Environment
        air_temp_c=getattr(measured, "air_temp_c", 0.0),
        track_temp_c=getattr(measured, "track_temp_c", 0.0),
    )


def extract_stint_snapshots(
    ibt_path: str | Path,
    car: CarModel,
    qualifying_laps: list[tuple[int, float, int, int]],
    ibt: IBTFile | None = None,
) -> list[LapSnapshot]:
    """Extract a LapSnapshot for each qualifying lap.

    Calls extract_measurements() once per lap, then maps to LapSnapshot.

    Args:
        ibt_path: Path to IBT file.
        car: Car model.
        qualifying_laps: Output from filter_qualifying_laps().
        ibt: Optional pre-opened IBTFile to avoid re-parsing.

    Returns:
        List of LapSnapshot in chronological order.
    """
    from analyzer.extract import extract_measurements

    snapshots: list[LapSnapshot] = []
    for lap_num, _lap_time, _start, _end in qualifying_laps:
        measured = extract_measurements(
            ibt_path, car, lap=lap_num, ibt=ibt,
        )
        snapshots.append(_snapshot_from_measured(measured))
    return snapshots


def _linear_fit(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Return (slope, r_squared) from a linear fit of y vs x."""
    if len(x) < 2:
        return 0.0, 0.0
    coeffs = np.polyfit(x, y, 1)
    slope = float(coeffs[0])
    y_pred = np.polyval(coeffs, x)
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_sq = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope, max(0.0, r_sq)


def compute_degradation_rates(
    snapshots: list[LapSnapshot],
) -> DegradationRates | None:
    """Compute linear degradation rates from lap-over-lap evolution.

    Returns None if fewer than 3 snapshots (insufficient for reliable fits).
    """
    if len(snapshots) < 3:
        return None

    laps = np.array([s.lap_number for s in snapshots], dtype=float)
    rates = DegradationRates()
    r_sq: dict[str, float] = {}

    # Helper to fit and store
    def fit(attr_name: str, values: np.ndarray, rate_name: str) -> None:
        slope, r2 = _linear_fit(laps, values)
        setattr(rates, rate_name, round(slope, 6))
        r_sq[rate_name] = round(r2, 3)

    fit("fuel_level_l",
        np.array([s.fuel_level_l for s in snapshots]),
        "fuel_burn_l_per_lap")
    # Fuel burn is negative slope (fuel decreases); negate for intuitive meaning
    rates.fuel_burn_l_per_lap = -rates.fuel_burn_l_per_lap

    fit("front_pressure_mean_kpa",
        np.array([s.front_pressure_mean_kpa for s in snapshots]),
        "front_pressure_kpa_per_lap")

    fit("rear_pressure_mean_kpa",
        np.array([s.rear_pressure_mean_kpa for s in snapshots]),
        "rear_pressure_kpa_per_lap")

    fit("front_wear",
        np.array([(s.lf_wear_pct + s.rf_wear_pct) / 2 for s in snapshots]),
        "front_wear_pct_per_lap")

    fit("rear_wear",
        np.array([(s.lr_wear_pct + s.rr_wear_pct) / 2 for s in snapshots]),
        "rear_wear_pct_per_lap")

    fit("understeer",
        np.array([s.understeer_mean_deg for s in snapshots]),
        "understeer_deg_per_lap")

    fit("peak_lat_g",
        np.array([s.peak_lat_g_measured for s in snapshots]),
        "peak_lat_g_per_lap")

    fit("front_carcass",
        np.array([s.front_carcass_mean_c for s in snapshots]),
        "front_carcass_c_per_lap")

    fit("rear_carcass",
        np.array([s.rear_carcass_mean_c for s in snapshots]),
        "rear_carcass_c_per_lap")

    fit("track_temp",
        np.array([s.track_temp_c for s in snapshots]),
        "track_temp_c_per_lap")

    fit("front_rh",
        np.array([s.mean_front_rh_at_speed_mm for s in snapshots]),
        "front_rh_mm_per_lap")

    fit("rear_rh",
        np.array([s.mean_rear_rh_at_speed_mm for s in snapshots]),
        "rear_rh_mm_per_lap")

    fit("lap_time",
        np.array([s.lap_time_s for s in snapshots]),
        "lap_time_s_per_lap")

    rates.r_squared = r_sq
    return rates


def analyze_stint_evolution(
    ibt_path: str | Path,
    car: CarModel,
    threshold_pct: float = 1.5,
    min_lap_time: float = 108.0,
    ibt: IBTFile | None = None,
) -> StintEvolution:
    """Analyze full stint evolution from telemetry.

    Filters qualifying laps, extracts per-lap snapshots, computes
    degradation rates, and identifies start/mid/end operating points.

    Args:
        ibt_path: Path to IBT file.
        car: Car model.
        threshold_pct: Max % slower than fastest lap to include.
        min_lap_time: Hard floor for valid laps.
        ibt: Optional pre-opened IBTFile to avoid re-parsing.

    Returns:
        StintEvolution with full begin-to-end analysis.
    """
    from track_model.ibt_parser import IBTFile as _IBTFile

    if ibt is None:
        ibt = _IBTFile(ibt_path)

    # Count total valid laps
    all_laps = ibt.lap_times(min_time=min_lap_time)
    total_laps = len(all_laps)

    # Filter qualifying laps
    qualifying = filter_qualifying_laps(ibt, threshold_pct, min_lap_time)
    if not qualifying:
        return StintEvolution(total_lap_count=total_laps, threshold_pct=threshold_pct)

    fastest = min(lt for _, lt, _, _ in qualifying)

    # Extract per-lap snapshots
    snapshots = extract_stint_snapshots(ibt_path, car, qualifying, ibt=ibt)

    # Compute degradation rates (None if <3 laps)
    rates = compute_degradation_rates(snapshots)

    # Identify start/mid/end operating points
    start = snapshots[0]
    mid = snapshots[len(snapshots) // 2]
    end = snapshots[-1]

    return StintEvolution(
        snapshots=snapshots,
        rates=rates,
        qualifying_lap_count=len(snapshots),
        total_lap_count=total_laps,
        threshold_pct=threshold_pct,
        fastest_lap_time_s=fastest,
        start_snapshot=start,
        mid_snapshot=mid,
        end_snapshot=end,
    )
