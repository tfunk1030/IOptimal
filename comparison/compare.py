"""Per-IBT analysis and multi-session comparison data structures.

Runs the full analyzer pipeline on each IBT file, then builds comparison
tables across sessions for setup parameters, telemetry metrics, and
per-corner performance.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from analyzer.adaptive_thresholds import compute_adaptive_thresholds
from analyzer.diagnose import Diagnosis, diagnose
from analyzer.driver_style import DriverProfile, analyze_driver, refine_driver_with_measured
from analyzer.extract import MeasuredState, extract_measurements
from analyzer.segment import CornerAnalysis, segment_lap
from analyzer.setup_reader import CurrentSetup
from car_model.cars import CarModel, get_car
from track_model.build_profile import build_profile
from track_model.ibt_parser import IBTFile
from track_model.profile import TrackProfile


# ── Data structures ─────────────────────────────────────────────


@dataclass
class SessionAnalysis:
    """Complete analysis results for a single IBT session."""

    label: str  # e.g. "S1 (session_v3.ibt)"
    ibt_path: str
    setup: CurrentSetup
    measured: MeasuredState
    corners: list[CornerAnalysis]
    driver: DriverProfile
    diagnosis: Diagnosis
    track: TrackProfile
    lap_time_s: float
    lap_number: int
    track_name: str = ""
    wing_angle: float = 0.0


@dataclass
class CornerComparison:
    """One track corner matched across sessions."""

    corner_id: int
    lap_dist_m: float  # midpoint for matching
    direction: str
    speed_class: str
    per_session: list[CornerAnalysis | None]  # one per session, None if unmatched


@dataclass
class ComparisonResult:
    """Full comparison output across N sessions."""

    sessions: list[SessionAnalysis]
    setup_deltas: dict[str, list]  # param_name → [value_per_session]
    telemetry_deltas: dict[str, list]  # metric_name → [value_per_session]
    corner_comparisons: list[CornerComparison]
    problem_matrix: dict[str, list[bool]]  # problem_desc → [present_per_session]


# ── Per-IBT analysis ────────────────────────────────────────────


def analyze_session(
    ibt_path: str,
    car: CarModel,
    wing: float | None = None,
    fuel: float | None = None,
    lap: int | None = None,
    label: str | None = None,
) -> SessionAnalysis:
    """Run full telemetry analysis on a single IBT file.

    Reuses the existing analyzer pipeline:
      IBT → CurrentSetup + TrackProfile + MeasuredState
          → CornerAnalysis + DriverProfile → Diagnosis
    """
    ibt_path_str = str(ibt_path)
    ibt = IBTFile(ibt_path_str)

    # Session metadata
    si = ibt.session_info
    track_name = "Unknown Track"
    if isinstance(si, dict):
        wi = si.get("WeekendInfo", {})
        track_name = wi.get("TrackDisplayName", wi.get("TrackName", "Unknown Track"))

    # Current setup from IBT
    setup = CurrentSetup.from_ibt(ibt)
    detected_wing = wing or setup.wing_angle_deg
    detected_fuel = fuel or setup.fuel_l or 89.0

    # Track profile
    track = build_profile(ibt_path_str)

    # Telemetry extraction
    measured = extract_measurements(ibt_path_str, car, lap=lap)

    # Corner segmentation
    if lap:
        lap_indices = None
        for ln, s, e in ibt.lap_boundaries():
            if ln == lap:
                lap_indices = (s, e)
                break
    else:
        lap_indices = ibt.best_lap_indices()

    corners: list[CornerAnalysis] = []
    if lap_indices is not None:
        start, end = lap_indices
        corners = segment_lap(ibt, start, end, car=car, tick_rate=ibt.tick_rate)

    # Driver style
    driver = analyze_driver(ibt, corners, car, tick_rate=ibt.tick_rate)
    refine_driver_with_measured(driver, measured)

    # Adaptive thresholds + diagnosis
    adaptive = compute_adaptive_thresholds(track, car, driver)
    diagnosis = diagnose(
        measured,
        setup,
        car,
        thresholds=adaptive,
        driver=driver,
        corners=corners,
    )

    # Label
    if label is None:
        label = Path(ibt_path_str).stem

    return SessionAnalysis(
        label=label,
        ibt_path=ibt_path_str,
        setup=setup,
        measured=measured,
        corners=corners,
        driver=driver,
        diagnosis=diagnosis,
        track=track,
        lap_time_s=measured.lap_time_s,
        lap_number=measured.lap_number,
        track_name=track_name,
        wing_angle=detected_wing,
    )


# ── Setup parameter extraction ──────────────────────────────────

# Parameters to compare, grouped by solver step
SETUP_PARAMS: list[tuple[str, str, str]] = [
    # (display_name, CurrentSetup field, units)
    ("Wing Angle", "wing_angle_deg", "°"),
    ("Static Front RH", "static_front_rh_mm", "mm"),
    ("Static Rear RH", "static_rear_rh_mm", "mm"),
    ("Front Pushrod", "front_pushrod_mm", "mm"),
    ("Rear Pushrod", "rear_pushrod_mm", "mm"),
    ("Front Heave", "front_heave_nmm", "N/mm"),
    ("Front Heave Perch", "front_heave_perch_mm", "mm"),
    ("Rear Third", "rear_third_nmm", "N/mm"),
    ("Rear Third Perch", "rear_third_perch_mm", "mm"),
    ("Front Torsion OD", "front_torsion_od_mm", "mm"),
    ("Rear Spring", "rear_spring_nmm", "N/mm"),
    ("Front ARB Blade", "front_arb_blade", ""),
    ("Rear ARB Blade", "rear_arb_blade", ""),
    ("Front Camber", "front_camber_deg", "°"),
    ("Rear Camber", "rear_camber_deg", "°"),
    ("Front Toe", "front_toe_mm", "mm"),
    ("Rear Toe", "rear_toe_mm", "mm"),
    ("Front LS Comp", "front_ls_comp", "click"),
    ("Front LS Rbd", "front_ls_rbd", "click"),
    ("Front HS Comp", "front_hs_comp", "click"),
    ("Front HS Rbd", "front_hs_rbd", "click"),
    ("Rear LS Comp", "rear_ls_comp", "click"),
    ("Rear LS Rbd", "rear_ls_rbd", "click"),
    ("Rear HS Comp", "rear_hs_comp", "click"),
    ("Rear HS Rbd", "rear_hs_rbd", "click"),
    ("Brake Bias", "brake_bias_pct", "%"),
    ("Diff Preload", "diff_preload_nm", "Nm"),
    ("TC Gain", "tc_gain", ""),
    ("TC Slip", "tc_slip", ""),
]

# Telemetry metrics to compare
TELEMETRY_METRICS: list[tuple[str, str, str, bool]] = [
    # (display_name, MeasuredState field, units, lower_is_better)
    ("Lap Time", "lap_time_s", "s", True),
    ("Top Speed", "speed_max_kph", "kph", False),
    ("Mean Speed", "speed_mean_kph", "kph", False),
    ("Front RH Variance", "front_rh_std_mm", "mm", True),
    ("Rear RH Variance", "rear_rh_std_mm", "mm", True),
    ("Front RH Excursion", "front_rh_excursion_measured_mm", "mm", True),
    ("Rear RH Excursion", "rear_rh_excursion_measured_mm", "mm", True),
    ("Aero Compress F", "aero_compression_front_mm", "mm", False),
    ("Aero Compress R", "aero_compression_rear_mm", "mm", False),
    ("Bottoming F", "bottoming_event_count_front", "", True),
    ("Bottoming R", "bottoming_event_count_rear", "", True),
    ("Vortex Burst", "vortex_burst_event_count", "", True),
    ("Front Shock p99", "front_shock_vel_p99_mps", "m/s", True),
    ("Rear Shock p99", "rear_shock_vel_p99_mps", "m/s", True),
    ("Front Settle", "front_rh_settle_time_ms", "ms", True),
    ("Rear Settle", "rear_rh_settle_time_ms", "ms", True),
    ("Peak Lat G", "peak_lat_g_measured", "g", False),
    ("LLTD", "lltd_measured", "", None),  # target-dependent, not simple better/worse
    ("Understeer Mean", "understeer_mean_deg", "°", True),
    ("Understeer Low Spd", "understeer_low_speed_deg", "°", True),
    ("Understeer High Spd", "understeer_high_speed_deg", "°", True),
    ("Body Slip p95", "body_slip_p95_deg", "°", True),
    ("Rear Slip Ratio p95", "rear_slip_ratio_p95", "", True),
    ("Yaw Correlation", "yaw_rate_correlation", "", False),
    ("Roll Rate p95", "roll_rate_p95_deg_per_s", "°/s", True),
    ("F Temp Spread LF", "front_temp_spread_lf_c", "°C", True),
    ("F Temp Spread RF", "front_temp_spread_rf_c", "°C", True),
    ("R Temp Spread LR", "rear_temp_spread_lr_c", "°C", True),
    ("R Temp Spread RR", "rear_temp_spread_rr_c", "°C", True),
    ("F Carcass Temp", "front_carcass_mean_c", "°C", None),  # 80-105 window
    ("R Carcass Temp", "rear_carcass_mean_c", "°C", None),
    ("F Pressure", "front_pressure_mean_kpa", "kPa", None),  # 155-175 window
    ("R Pressure", "rear_pressure_mean_kpa", "kPa", None),
]


# ── Multi-session comparison ────────────────────────────────────


def _build_setup_deltas(sessions: list[SessionAnalysis]) -> dict[str, list]:
    """Extract setup parameter values across sessions."""
    deltas: dict[str, list] = {}
    for name, attr, _units in SETUP_PARAMS:
        vals = []
        for s in sessions:
            vals.append(getattr(s.setup, attr, None))
        deltas[name] = vals
    return deltas


def _build_telemetry_deltas(sessions: list[SessionAnalysis]) -> dict[str, list]:
    """Extract telemetry metric values across sessions."""
    deltas: dict[str, list] = {}
    for name, attr, _units, _lib in TELEMETRY_METRICS:
        vals = []
        for s in sessions:
            vals.append(getattr(s.measured, attr, None))
        deltas[name] = vals
    return deltas


def _match_corners(sessions: list[SessionAnalysis]) -> list[CornerComparison]:
    """Match corners across sessions by lap distance proximity.

    Uses the first session as the reference and matches corners from
    other sessions within 50m of the reference corner midpoint.
    """
    if not sessions or not sessions[0].corners:
        return []

    ref_corners = sessions[0].corners
    comparisons: list[CornerComparison] = []
    match_tolerance_m = 50.0

    for ref_c in ref_corners:
        mid = (ref_c.lap_dist_start_m + ref_c.lap_dist_end_m) / 2.0
        per_session: list[CornerAnalysis | None] = [ref_c]

        for sess in sessions[1:]:
            best_match: CornerAnalysis | None = None
            best_dist = match_tolerance_m
            for c in sess.corners:
                c_mid = (c.lap_dist_start_m + c.lap_dist_end_m) / 2.0
                dist = abs(c_mid - mid)
                if dist < best_dist:
                    best_dist = dist
                    best_match = c
            per_session.append(best_match)

        comparisons.append(CornerComparison(
            corner_id=ref_c.corner_id,
            lap_dist_m=mid,
            direction=ref_c.direction,
            speed_class=ref_c.speed_class,
            per_session=per_session,
        ))

    return comparisons


def _build_problem_matrix(sessions: list[SessionAnalysis]) -> dict[str, list[bool]]:
    """Build a matrix of which problems appear in which sessions."""
    # Collect all unique problem symptoms
    all_symptoms: list[str] = []
    seen: set[str] = set()
    for s in sessions:
        for p in s.diagnosis.problems:
            key = f"[{p.category}] {p.symptom}"
            if key not in seen:
                seen.add(key)
                all_symptoms.append(key)

    matrix: dict[str, list[bool]] = {}
    for symptom in all_symptoms:
        row = []
        for s in sessions:
            found = any(
                f"[{p.category}] {p.symptom}" == symptom
                for p in s.diagnosis.problems
            )
            row.append(found)
        matrix[symptom] = row

    return matrix


def compare_sessions(sessions: list[SessionAnalysis]) -> ComparisonResult:
    """Build a full comparison across analyzed sessions.

    Validates that all sessions are the same car and track.
    Different wing angles are allowed (flagged as aero test).
    """
    if len(sessions) < 2:
        raise ValueError("Need at least 2 sessions to compare")

    # Validate same track
    track_names = set()
    for s in sessions:
        track_names.add(s.track_name.strip().lower())
    # Allow minor naming differences (e.g. "Sebring" vs "Sebring International")
    # by checking if any name is a substring of another
    unique_tracks = list(track_names)
    if len(unique_tracks) > 1:
        # Check if they're just naming variants
        compatible = False
        for i, t1 in enumerate(unique_tracks):
            for t2 in unique_tracks[i + 1:]:
                if t1 in t2 or t2 in t1:
                    compatible = True
                    break
            if compatible:
                break
        if not compatible:
            raise ValueError(
                f"All IBTs must be from the same track. Found: "
                f"{', '.join(s.track_name for s in sessions)}"
            )

    return ComparisonResult(
        sessions=sessions,
        setup_deltas=_build_setup_deltas(sessions),
        telemetry_deltas=_build_telemetry_deltas(sessions),
        corner_comparisons=_match_corners(sessions),
        problem_matrix=_build_problem_matrix(sessions),
    )
