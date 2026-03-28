"""Stint-level telemetry analysis and multi-lap stint selection."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from car_model.cars import CarModel
    from track_model.ibt_parser import IBTFile


@dataclass
class LapSnapshot:
    """Stint-relevant telemetry from one lap (subset of MeasuredState)."""

    lap_number: int = 0
    lap_time_s: float = 0.0
    fuel_level_l: float = 0.0

    lf_pressure_kpa: float = 0.0
    rf_pressure_kpa: float = 0.0
    lr_pressure_kpa: float = 0.0
    rr_pressure_kpa: float = 0.0

    lf_wear_pct: float = 0.0
    rf_wear_pct: float = 0.0
    lr_wear_pct: float = 0.0
    rr_wear_pct: float = 0.0

    front_carcass_mean_c: float = 0.0
    rear_carcass_mean_c: float = 0.0
    front_pressure_mean_kpa: float = 0.0
    rear_pressure_mean_kpa: float = 0.0

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

    understeer_mean_deg: float = 0.0
    body_slip_p95_deg: float = 0.0
    peak_lat_g_measured: float = 0.0
    rear_slip_ratio_p95: float = 0.0

    mean_front_rh_at_speed_mm: float = 0.0
    mean_rear_rh_at_speed_mm: float = 0.0
    front_rh_std_mm: float = 0.0
    rear_rh_std_mm: float = 0.0
    front_shock_vel_p95_mps: float = 0.0
    rear_shock_vel_p95_mps: float = 0.0
    bottoming_event_count_front: int = 0
    bottoming_event_count_rear: int = 0

    air_temp_c: float = 0.0
    track_temp_c: float = 0.0


@dataclass
class DegradationRates:
    """Telemetry-measured rates of change per lap from a linear regression."""

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
    r_squared: dict[str, float] = field(default_factory=dict)


@dataclass
class LapQuality:
    """Quality metadata for one lap."""

    status: str = "usable"
    hard_reject: bool = False
    direct_weight: float = 1.0
    trend_weight: float = 1.0
    flags: list[str] = field(default_factory=list)


@dataclass
class StintLapState:
    """One lap inside a selected stint."""

    lap_number: int
    lap_time_s: float
    start_idx: int
    end_idx: int
    measured: Any
    snapshot: LapSnapshot
    quality: LapQuality = field(default_factory=LapQuality)
    progress: float = 0.0
    phase: str = "mid"
    fuel_level_l: float = 0.0
    segment_id: int = 0
    source_label: str = ""
    source_path: str = ""
    selected_for_evaluation: bool = False


@dataclass
class StintSegment:
    """A contiguous green-run segment."""

    segment_id: int
    laps: list[StintLapState] = field(default_factory=list)
    source_label: str = ""
    source_path: str = ""
    break_reasons: list[str] = field(default_factory=list)

    @property
    def lap_count(self) -> int:
        return len(self.laps)

    @property
    def start_lap(self) -> int:
        return self.laps[0].lap_number if self.laps else 0

    @property
    def end_lap(self) -> int:
        return self.laps[-1].lap_number if self.laps else 0


@dataclass
class StintDataset:
    """Selected stint data ready for solving and reporting."""

    ibt_path: str
    source_label: str = ""
    source_path: str = ""
    segments: list[StintSegment] = field(default_factory=list)
    selected_segments: list[StintSegment] = field(default_factory=list)
    usable_laps: list[StintLapState] = field(default_factory=list)
    evaluation_laps: list[StintLapState] = field(default_factory=list)
    total_lap_count: int = 0
    selected_lap_count: int = 0
    stint_select: str = "longest"
    stint_max_laps: int = 40
    threshold_pct: float = 1.5
    fastest_lap_time_s: float = 0.0
    median_lap_time_s: float = 0.0
    confidence: float = 0.0
    fallback_mode: str | None = None
    selection_notes: list[str] = field(default_factory=list)
    phase_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class StintEvolution:
    """Complete stint evolution from telemetry."""

    snapshots: list[LapSnapshot] = field(default_factory=list)
    rates: DegradationRates | None = None
    qualifying_lap_count: int = 0
    total_lap_count: int = 0
    threshold_pct: float = 1.5
    fastest_lap_time_s: float = 0.0
    start_snapshot: LapSnapshot = field(default_factory=LapSnapshot)
    mid_snapshot: LapSnapshot = field(default_factory=LapSnapshot)
    end_snapshot: LapSnapshot = field(default_factory=LapSnapshot)


def phase_for_progress(progress: float) -> str:
    if progress <= 0.33:
        return "early"
    if progress <= 0.67:
        return "mid"
    return "late"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def filter_qualifying_laps(
    ibt: IBTFile,
    threshold_pct: float = 1.5,
    min_lap_time: float = 108.0,
) -> list[tuple[int, float, int, int]]:
    """Return laps within threshold_pct of the fastest valid lap."""

    all_laps = ibt.lap_times(min_time=min_lap_time)
    if not all_laps:
        return []

    fastest = min(lt for _, lt, _, _ in all_laps)
    cutoff = fastest * (1.0 + threshold_pct / 100.0)
    qualifying = [
        (lap_num, lap_time_s, start_idx, end_idx)
        for lap_num, lap_time_s, start_idx, end_idx in all_laps
        if lap_time_s <= cutoff
    ]
    qualifying.sort(key=lambda row: row[0])
    return qualifying


def _snapshot_from_measured(measured: Any) -> LapSnapshot:
    return LapSnapshot(
        lap_number=_safe_int(getattr(measured, "lap_number", 0)),
        lap_time_s=_safe_float(getattr(measured, "lap_time_s", 0.0)),
        fuel_level_l=_safe_float(getattr(measured, "fuel_level_at_measurement_l", 0.0)),
        lf_pressure_kpa=_safe_float(getattr(measured, "lf_pressure_kpa", 0.0)),
        rf_pressure_kpa=_safe_float(getattr(measured, "rf_pressure_kpa", 0.0)),
        lr_pressure_kpa=_safe_float(getattr(measured, "lr_pressure_kpa", 0.0)),
        rr_pressure_kpa=_safe_float(getattr(measured, "rr_pressure_kpa", 0.0)),
        lf_wear_pct=_safe_float(getattr(measured, "lf_wear_pct", 0.0)),
        rf_wear_pct=_safe_float(getattr(measured, "rf_wear_pct", 0.0)),
        lr_wear_pct=_safe_float(getattr(measured, "lr_wear_pct", 0.0)),
        rr_wear_pct=_safe_float(getattr(measured, "rr_wear_pct", 0.0)),
        front_carcass_mean_c=_safe_float(getattr(measured, "front_carcass_mean_c", 0.0)),
        rear_carcass_mean_c=_safe_float(getattr(measured, "rear_carcass_mean_c", 0.0)),
        front_pressure_mean_kpa=_safe_float(getattr(measured, "front_pressure_mean_kpa", 0.0)),
        rear_pressure_mean_kpa=_safe_float(getattr(measured, "rear_pressure_mean_kpa", 0.0)),
        lf_temp_inner_c=_safe_float(getattr(measured, "lf_temp_inner_c", 0.0)),
        lf_temp_middle_c=_safe_float(getattr(measured, "lf_temp_middle_c", 0.0)),
        lf_temp_outer_c=_safe_float(getattr(measured, "lf_temp_outer_c", 0.0)),
        rf_temp_inner_c=_safe_float(getattr(measured, "rf_temp_inner_c", 0.0)),
        rf_temp_middle_c=_safe_float(getattr(measured, "rf_temp_middle_c", 0.0)),
        rf_temp_outer_c=_safe_float(getattr(measured, "rf_temp_outer_c", 0.0)),
        lr_temp_inner_c=_safe_float(getattr(measured, "lr_temp_inner_c", 0.0)),
        lr_temp_middle_c=_safe_float(getattr(measured, "lr_temp_middle_c", 0.0)),
        lr_temp_outer_c=_safe_float(getattr(measured, "lr_temp_outer_c", 0.0)),
        rr_temp_inner_c=_safe_float(getattr(measured, "rr_temp_inner_c", 0.0)),
        rr_temp_middle_c=_safe_float(getattr(measured, "rr_temp_middle_c", 0.0)),
        rr_temp_outer_c=_safe_float(getattr(measured, "rr_temp_outer_c", 0.0)),
        understeer_mean_deg=_safe_float(getattr(measured, "understeer_mean_deg", 0.0)),
        body_slip_p95_deg=_safe_float(getattr(measured, "body_slip_p95_deg", 0.0)),
        peak_lat_g_measured=_safe_float(getattr(measured, "peak_lat_g_measured", 0.0)),
        rear_slip_ratio_p95=_safe_float(
            getattr(measured, "rear_power_slip_ratio_p95", getattr(measured, "rear_slip_ratio_p95", 0.0)),
        ),
        mean_front_rh_at_speed_mm=_safe_float(getattr(measured, "mean_front_rh_at_speed_mm", 0.0)),
        mean_rear_rh_at_speed_mm=_safe_float(getattr(measured, "mean_rear_rh_at_speed_mm", 0.0)),
        front_rh_std_mm=_safe_float(getattr(measured, "front_rh_std_mm", 0.0)),
        rear_rh_std_mm=_safe_float(getattr(measured, "rear_rh_std_mm", 0.0)),
        front_shock_vel_p95_mps=_safe_float(getattr(measured, "front_shock_vel_p95_mps", 0.0)),
        rear_shock_vel_p95_mps=_safe_float(getattr(measured, "rear_shock_vel_p95_mps", 0.0)),
        bottoming_event_count_front=_safe_int(getattr(measured, "bottoming_event_count_front_clean", getattr(measured, "bottoming_event_count_front", 0))),
        bottoming_event_count_rear=_safe_int(getattr(measured, "bottoming_event_count_rear_clean", getattr(measured, "bottoming_event_count_rear", 0))),
        air_temp_c=_safe_float(getattr(measured, "air_temp_c", 0.0)),
        track_temp_c=_safe_float(getattr(measured, "track_temp_c", 0.0)),
    )


def extract_stint_snapshots(
    ibt_path: str | Path,
    car: CarModel,
    qualifying_laps: list[tuple[int, float, int, int]],
    ibt: IBTFile | None = None,
) -> list[LapSnapshot]:
    from analyzer.extract import extract_measurements

    snapshots: list[LapSnapshot] = []
    for lap_num, _lap_time, _start, _end in qualifying_laps:
        measured = extract_measurements(ibt_path, car, lap=lap_num, ibt=ibt)
        snapshots.append(_snapshot_from_measured(measured))
    return snapshots


def _linear_fit(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    if len(x) < 2:
        return 0.0, 0.0
    coeffs = np.polyfit(x, y, 1)
    slope = float(coeffs[0])
    y_pred = np.polyval(coeffs, x)
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_sq = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope, max(0.0, r_sq)


def compute_degradation_rates(snapshots: list[LapSnapshot]) -> DegradationRates | None:
    if len(snapshots) < 3:
        return None

    laps = np.array([snapshot.lap_number for snapshot in snapshots], dtype=float)
    rates = DegradationRates()
    r_sq: dict[str, float] = {}

    def fit(values: np.ndarray, rate_name: str) -> None:
        slope, score = _linear_fit(laps, values)
        setattr(rates, rate_name, round(slope, 6))
        r_sq[rate_name] = round(score, 3)

    fit(np.array([snapshot.fuel_level_l for snapshot in snapshots]), "fuel_burn_l_per_lap")
    rates.fuel_burn_l_per_lap = -rates.fuel_burn_l_per_lap
    fit(np.array([snapshot.front_pressure_mean_kpa for snapshot in snapshots]), "front_pressure_kpa_per_lap")
    fit(np.array([snapshot.rear_pressure_mean_kpa for snapshot in snapshots]), "rear_pressure_kpa_per_lap")
    fit(np.array([(snapshot.lf_wear_pct + snapshot.rf_wear_pct) / 2.0 for snapshot in snapshots]), "front_wear_pct_per_lap")
    fit(np.array([(snapshot.lr_wear_pct + snapshot.rr_wear_pct) / 2.0 for snapshot in snapshots]), "rear_wear_pct_per_lap")
    fit(np.array([snapshot.understeer_mean_deg for snapshot in snapshots]), "understeer_deg_per_lap")
    fit(np.array([snapshot.peak_lat_g_measured for snapshot in snapshots]), "peak_lat_g_per_lap")
    fit(np.array([snapshot.front_carcass_mean_c for snapshot in snapshots]), "front_carcass_c_per_lap")
    fit(np.array([snapshot.rear_carcass_mean_c for snapshot in snapshots]), "rear_carcass_c_per_lap")
    fit(np.array([snapshot.track_temp_c for snapshot in snapshots]), "track_temp_c_per_lap")
    fit(np.array([snapshot.mean_front_rh_at_speed_mm for snapshot in snapshots]), "front_rh_mm_per_lap")
    fit(np.array([snapshot.mean_rear_rh_at_speed_mm for snapshot in snapshots]), "rear_rh_mm_per_lap")
    fit(np.array([snapshot.lap_time_s for snapshot in snapshots]), "lap_time_s_per_lap")
    rates.r_squared = r_sq
    return rates


def _channel_slice(ibt: IBTFile, name: str, start_idx: int, end_idx: int) -> np.ndarray | None:
    if not hasattr(ibt, "has_channel") or not ibt.has_channel(name):
        return None
    channel = ibt.channel(name)
    if channel is None:
        return None
    return channel[start_idx:end_idx + 1]


def _critical_signal_coverage(measured: Any) -> int:
    checks = (
        _safe_float(getattr(measured, "front_rh_std_mm", None)),
        _safe_float(getattr(measured, "rear_rh_std_mm", None)),
        _safe_float(getattr(measured, "front_pressure_mean_kpa", None)),
        _safe_float(getattr(measured, "rear_pressure_mean_kpa", None)),
        _safe_float(getattr(measured, "body_slip_p95_deg", None)),
        _safe_float(getattr(measured, "understeer_mean_deg", None)),
        _safe_float(getattr(measured, "pitch_range_braking_deg", None)),
    )
    return sum(1 for value in checks if abs(value) > 1e-6)


def _lap_quality(
    *,
    ibt: IBTFile,
    start_idx: int,
    end_idx: int,
    lap_time_s: float,
    fastest_lap_s: float,
    median_lap_s: float,
    threshold_pct: float,
    measured: Any,
) -> LapQuality:
    quality = LapQuality()

    if _critical_signal_coverage(measured) < 3:
        quality.status = "hard_reject"
        quality.hard_reject = True
        quality.direct_weight = 0.0
        quality.trend_weight = 0.0
        quality.flags.append("missing_critical_telemetry")
        return quality

    on_pit = _channel_slice(ibt, "OnPitRoad", start_idx, end_idx)
    if on_pit is not None and np.mean(on_pit.astype(float)) > 0.05:
        quality.status = "hard_reject"
        quality.hard_reject = True
        quality.direct_weight = 0.0
        quality.trend_weight = 0.0
        quality.flags.append("pit_contamination")
        return quality

    fuel = _channel_slice(ibt, "FuelLevel", start_idx, end_idx)
    if fuel is not None and len(fuel) > 1:
        fuel_delta = float(fuel[-1] - fuel[0])
        if fuel_delta > 0.75:
            quality.status = "hard_reject"
            quality.hard_reject = True
            quality.direct_weight = 0.0
            quality.trend_weight = 0.0
            quality.flags.append("fuel_reset")
            return quality

    invalid_ceiling = max(fastest_lap_s * (1.0 + max(threshold_pct, 6.0) / 100.0), median_lap_s * 1.22)
    if lap_time_s >= invalid_ceiling:
        quality.status = "hard_reject"
        quality.hard_reject = True
        quality.direct_weight = 0.0
        quality.trend_weight = 0.0
        quality.flags.append("full_lap_invalid_or_caution")
        return quality

    noisy_ceiling = max(fastest_lap_s * (1.0 + max(threshold_pct, 2.5) / 100.0), median_lap_s * 1.08)
    if lap_time_s >= noisy_ceiling:
        quality.status = "downgraded"
        quality.direct_weight *= 0.55
        quality.flags.append("traffic_or_noise")

    front_lock = _safe_float(getattr(measured, "front_braking_lock_ratio_p95", 0.0))
    rear_slip = _safe_float(getattr(measured, "rear_power_slip_ratio_p95", getattr(measured, "rear_slip_ratio_p95", 0.0)))
    if front_lock > 0.16 or rear_slip > 0.18:
        quality.status = "downgraded"
        quality.direct_weight *= 0.7
        quality.flags.append("trace_outlier")

    return quality


def _finalize_segment(segment: StintSegment) -> StintSegment:
    if not segment.laps:
        return segment
    lap_count = max(len(segment.laps) - 1, 1)
    finalized: list[StintLapState] = []
    for idx, lap in enumerate(segment.laps):
        progress = 0.0 if len(segment.laps) == 1 else idx / lap_count
        finalized.append(replace(lap, progress=round(progress, 4), phase=phase_for_progress(progress)))
    segment.laps = finalized
    return segment


def _select_segments(segments: list[StintSegment], stint_select: str) -> list[StintSegment]:
    if not segments:
        return []
    if stint_select == "all":
        return list(segments)
    if stint_select == "last":
        return [segments[-1]]
    longest = max(segments, key=lambda segment: (segment.lap_count, segment.end_lap))
    return [longest]


def _select_evaluation_laps(laps: list[StintLapState], stint_max_laps: int) -> list[StintLapState]:
    if len(laps) <= stint_max_laps:
        return [replace(lap, selected_for_evaluation=True) for lap in laps]
    keep: set[int] = set()
    for idx in range(min(3, len(laps))):
        keep.add(idx)
    for idx in range(max(0, len(laps) - 3), len(laps)):
        keep.add(idx)
    remaining = max(0, stint_max_laps - len(keep))
    if remaining > 0 and len(laps) > len(keep):
        interior_start = min(3, len(laps))
        interior_end = max(interior_start, len(laps) - 3)
        interior_indices = list(range(interior_start, interior_end))
        if interior_indices:
            step_positions = np.linspace(0, len(interior_indices) - 1, num=min(remaining, len(interior_indices)))
            for position in step_positions:
                keep.add(interior_indices[int(round(position))])
    return [
        replace(lap, selected_for_evaluation=(idx in keep))
        for idx, lap in enumerate(laps)
        if idx in keep
    ]


def _phase_issues(metrics: dict[str, float]) -> list[str]:
    issues: list[str] = []
    if metrics.get("front_support", 0.0) > 0.0:
        issues.append("increase_front_support")
    if metrics.get("rear_support", 0.0) > 0.0:
        issues.append("increase_rear_support")
    if metrics.get("entry_understeer", 0.0) > 0.0:
        issues.append("reduce_entry_understeer")
    if metrics.get("high_speed_understeer", 0.0) > 0.0:
        issues.append("reduce_high_speed_understeer")
    if metrics.get("traction_instability", 0.0) > 0.0:
        issues.append("improve_traction")
    if metrics.get("brake_instability", 0.0) > 0.0:
        issues.append("stabilize_braking")
    if metrics.get("thermal_load", 0.0) > 0.0:
        issues.append("protect_tyres")
    return issues


def _summarize_phase(laps: list[StintLapState]) -> dict[str, Any]:
    if not laps:
        return {"lap_count": 0, "issues": [], "metrics": {}}

    def avg(attr: str) -> float:
        return round(float(np.mean([_safe_float(getattr(lap.measured, attr, 0.0)) for lap in laps])), 4)

    front_support = max(
        0.0,
        avg("front_heave_travel_used_pct") - 80.0,
        avg("front_rh_std_mm") - 8.0,
        avg("pitch_range_braking_deg") - 1.4,
        avg("bottoming_event_count_front_clean") * 6.0,
    )
    rear_support = max(
        0.0,
        avg("rear_heave_travel_used_pct") - 80.0,
        avg("rear_rh_std_mm") - 9.0,
        avg("bottoming_event_count_rear_clean") * 6.0,
    )
    entry_understeer = max(0.0, avg("understeer_low_speed_deg") - 0.9)
    high_speed_understeer = max(0.0, avg("understeer_high_speed_deg") - max(avg("understeer_low_speed_deg"), 0.8))
    traction_instability = max(
        0.0,
        avg("rear_power_slip_ratio_p95") - 0.075,
        avg("body_slip_p95_deg") - 3.4,
    )
    brake_instability = max(
        0.0,
        avg("front_braking_lock_ratio_p95") - 0.07,
        avg("abs_active_pct") - 10.0,
    )
    thermal_load = max(
        0.0,
        avg("front_carcass_mean_c") - 95.0,
        avg("rear_carcass_mean_c") - 95.0,
        avg("front_pressure_mean_kpa") - 169.0,
        avg("rear_pressure_mean_kpa") - 169.0,
    )
    metrics = {
        "avg_lap_time_s": avg("lap_time_s"),
        "avg_fuel_l": avg("fuel_level_at_measurement_l"),
        "front_support": round(front_support, 3),
        "rear_support": round(rear_support, 3),
        "entry_understeer": round(entry_understeer, 3),
        "high_speed_understeer": round(high_speed_understeer, 3),
        "traction_instability": round(traction_instability, 3),
        "brake_instability": round(brake_instability, 3),
        "thermal_load": round(thermal_load, 3),
    }
    return {
        "lap_count": len(laps),
        "lap_numbers": [lap.lap_number for lap in laps],
        "metrics": metrics,
        "issues": _phase_issues(metrics),
    }


def _build_phase_summaries(laps: list[StintLapState]) -> dict[str, dict[str, Any]]:
    grouped = {"early": [], "mid": [], "late": []}
    for lap in laps:
        grouped.setdefault(lap.phase, []).append(lap)
    return {phase: _summarize_phase(grouped.get(phase, [])) for phase in ("early", "mid", "late")}


def build_stint_dataset(
    *,
    ibt_path: str | Path,
    car: CarModel,
    stint_select: str = "longest",
    stint_max_laps: int = 40,
    threshold_pct: float = 1.5,
    min_lap_time: float = 108.0,
    ibt: IBTFile | None = None,
    source_label: str = "",
) -> StintDataset:
    """Build a stint dataset from all completed laps in an IBT."""

    from analyzer.extract import extract_measurements
    from track_model.ibt_parser import IBTFile as ParsedIBTFile

    if ibt is None:
        ibt = ParsedIBTFile(ibt_path)

    all_laps = list(ibt.lap_times(min_time=min_lap_time))
    times = [lap_time_s for _, lap_time_s, _, _ in all_laps]
    fastest = min(times) if times else 0.0
    median = float(np.median(times)) if times else 0.0
    dataset = StintDataset(
        ibt_path=str(ibt_path),
        source_label=source_label,
        source_path=str(ibt_path),
        total_lap_count=len(all_laps),
        stint_select=stint_select,
        stint_max_laps=stint_max_laps,
        threshold_pct=threshold_pct,
        fastest_lap_time_s=round(fastest, 4),
        median_lap_time_s=round(median, 4),
    )
    if not all_laps:
        dataset.fallback_mode = "single_lap_insufficient_stint_data"
        dataset.selection_notes.append("No completed laps met the minimum lap-time filter.")
        return dataset

    segments: list[StintSegment] = []
    current_segment = StintSegment(segment_id=1, source_label=source_label, source_path=str(ibt_path))
    previous_usable_fuel: float | None = None

    for lap_num, lap_time_s, start_idx, end_idx in all_laps:
        measured = extract_measurements(
            str(ibt_path),
            car,
            lap=lap_num,
            ibt=ibt,
            min_lap_time=min_lap_time,
        )
        quality = _lap_quality(
            ibt=ibt,
            start_idx=start_idx,
            end_idx=end_idx,
            lap_time_s=lap_time_s,
            fastest_lap_s=fastest,
            median_lap_s=median,
            threshold_pct=threshold_pct,
            measured=measured,
        )
        if quality.hard_reject:
            if current_segment.laps:
                current_segment.break_reasons.extend(quality.flags)
                segments.append(_finalize_segment(current_segment))
                current_segment = StintSegment(
                    segment_id=current_segment.segment_id + 1,
                    source_label=source_label,
                    source_path=str(ibt_path),
                )
            continue

        fuel_level_l = _safe_float(getattr(measured, "fuel_level_at_measurement_l", 0.0))
        if current_segment.laps and previous_usable_fuel is not None and fuel_level_l > previous_usable_fuel + 1.0:
            current_segment.break_reasons.append("fuel_reset")
            segments.append(_finalize_segment(current_segment))
            current_segment = StintSegment(
                segment_id=current_segment.segment_id + 1,
                source_label=source_label,
                source_path=str(ibt_path),
            )

        lap_state = StintLapState(
            lap_number=lap_num,
            lap_time_s=round(lap_time_s, 4),
            start_idx=start_idx,
            end_idx=end_idx,
            measured=measured,
            snapshot=_snapshot_from_measured(measured),
            quality=quality,
            fuel_level_l=fuel_level_l,
            segment_id=current_segment.segment_id,
            source_label=source_label,
            source_path=str(ibt_path),
        )
        current_segment.laps.append(lap_state)
        previous_usable_fuel = fuel_level_l

    if current_segment.laps:
        segments.append(_finalize_segment(current_segment))

    dataset.segments = [segment for segment in segments if segment.laps]
    dataset.selected_segments = _select_segments(dataset.segments, stint_select)
    dataset.usable_laps = [lap for segment in dataset.selected_segments for lap in segment.laps]
    dataset.selected_lap_count = len(dataset.usable_laps)
    selected_eval = _select_evaluation_laps(dataset.usable_laps, stint_max_laps)
    selected_keys = {(lap.source_label, lap.lap_number) for lap in selected_eval}
    dataset.usable_laps = [
        replace(lap, selected_for_evaluation=((lap.source_label, lap.lap_number) in selected_keys))
        for lap in dataset.usable_laps
    ]
    dataset.evaluation_laps = [lap for lap in dataset.usable_laps if lap.selected_for_evaluation]
    dataset.phase_summaries = _build_phase_summaries(dataset.usable_laps)

    if dataset.usable_laps:
        mean_weight = float(np.mean([lap.quality.direct_weight for lap in dataset.usable_laps]))
        dataset.confidence = round(
            max(0.05, min(1.0, (len(dataset.usable_laps) / max(len(all_laps), 1)) * 0.55 + mean_weight * 0.45)),
            3,
        )
    else:
        dataset.confidence = 0.05

    if len(dataset.usable_laps) < 5:
        dataset.fallback_mode = "single_lap_insufficient_stint_data"
        dataset.selection_notes.append(
            f"Only {len(dataset.usable_laps)} usable stint laps remained after gating; use the single-lap fallback."
        )
    elif len(dataset.evaluation_laps) < len(dataset.usable_laps):
        dataset.selection_notes.append(
            f"Evaluation reduced {len(dataset.usable_laps)} usable laps to {len(dataset.evaluation_laps)} representative laps."
        )

    if dataset.selected_segments:
        selected_ranges = ", ".join(
            f"{segment.start_lap}-{segment.end_lap}" if segment.start_lap != segment.end_lap else f"{segment.start_lap}"
            for segment in dataset.selected_segments
        )
        dataset.selection_notes.append(
            f"Selected stint segment(s): {selected_ranges} via '{stint_select}' selection."
        )
    else:
        dataset.selection_notes.append("No selectable green-run stint segment was found.")

    return dataset


def merge_stint_datasets(
    datasets: list[StintDataset],
    *,
    stint_max_laps: int = 40,
    label: str = "merged",
) -> StintDataset:
    """Merge selected stint laps from multiple datasets into one combined dataset."""

    merged = StintDataset(
        ibt_path=label,
        source_label=label,
        source_path=label,
        stint_select="all",
        stint_max_laps=stint_max_laps,
        threshold_pct=max((dataset.threshold_pct for dataset in datasets), default=1.5),
    )
    merged.segments = [segment for dataset in datasets for segment in dataset.selected_segments]
    merged.selected_segments = list(merged.segments)
    merged.usable_laps = [replace(lap) for dataset in datasets for lap in dataset.usable_laps]
    merged.total_lap_count = sum(dataset.total_lap_count for dataset in datasets)
    merged.selected_lap_count = len(merged.usable_laps)
    merged.fastest_lap_time_s = min((dataset.fastest_lap_time_s for dataset in datasets if dataset.fastest_lap_time_s > 0), default=0.0)
    medians = [dataset.median_lap_time_s for dataset in datasets if dataset.median_lap_time_s > 0]
    merged.median_lap_time_s = round(float(np.mean(medians)), 4) if medians else 0.0
    selected_eval = _select_evaluation_laps(merged.usable_laps, stint_max_laps)
    selected_keys = {(lap.source_label, lap.lap_number) for lap in selected_eval}
    merged.usable_laps = [
        replace(lap, selected_for_evaluation=((lap.source_label, lap.lap_number) in selected_keys))
        for lap in merged.usable_laps
    ]
    merged.evaluation_laps = [lap for lap in merged.usable_laps if lap.selected_for_evaluation]
    merged.phase_summaries = _build_phase_summaries(merged.usable_laps)
    merged.selection_notes = [
        note
        for dataset in datasets
        for note in dataset.selection_notes
    ]
    if merged.usable_laps:
        merged.confidence = round(float(np.mean([dataset.confidence for dataset in datasets])), 3)
    else:
        merged.confidence = 0.05
        merged.fallback_mode = "single_lap_insufficient_stint_data"
    if sum(1 for dataset in datasets if dataset.fallback_mode is None) == 0:
        merged.fallback_mode = "single_lap_insufficient_stint_data"
    return merged


def dataset_to_evolution(dataset: StintDataset) -> StintEvolution:
    """Convert a selected stint dataset to the legacy evolution view."""

    snapshots = [lap.snapshot for lap in dataset.usable_laps]
    if not snapshots:
        return StintEvolution(
            total_lap_count=dataset.total_lap_count,
            threshold_pct=dataset.threshold_pct,
        )
    rates = compute_degradation_rates(snapshots)
    start = snapshots[0]
    mid = snapshots[len(snapshots) // 2]
    end = snapshots[-1]
    return StintEvolution(
        snapshots=snapshots,
        rates=rates,
        qualifying_lap_count=len(snapshots),
        total_lap_count=dataset.total_lap_count,
        threshold_pct=dataset.threshold_pct,
        fastest_lap_time_s=dataset.fastest_lap_time_s,
        start_snapshot=start,
        mid_snapshot=mid,
        end_snapshot=end,
    )


def analyze_stint_evolution(
    ibt_path: str | Path,
    car: CarModel,
    threshold_pct: float = 1.5,
    min_lap_time: float = 108.0,
    ibt: IBTFile | None = None,
) -> StintEvolution:
    """Backward-compatible stint evolution entrypoint."""

    dataset = build_stint_dataset(
        ibt_path=ibt_path,
        car=car,
        stint_select="longest",
        stint_max_laps=40,
        threshold_pct=threshold_pct,
        min_lap_time=min_lap_time,
        ibt=ibt,
    )
    return dataset_to_evolution(dataset)
