"""Extract a structured observation from one IBT analysis.

An Observation is the atomic unit of learning. It captures:
- WHAT the setup was (every garage parameter)
- WHAT the car did (telemetry metrics)
- WHAT the driver did (style profile)
- WHAT problems existed (diagnosis)
- WHERE and WHEN (track, conditions, timestamp)

This is raw data — no interpretation. The delta_detector and empirical_models
modules interpret by comparing observations against each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Observation:
    """Complete snapshot of one session for the learning system."""

    # ── Identity ──
    session_id: str
    ibt_path: str
    car: str
    track: str
    track_config: str = ""
    timestamp: str = ""  # ISO format when ingested
    ibt_date: str = ""   # date extracted from IBT filename if possible

    # ── Setup Parameters (what was configured) ──
    setup: dict = field(default_factory=dict)
    # Keys: wing, fuel_l, front_rh_static, rear_rh_static,
    #        front_pushrod, rear_pushrod, front_heave_nmm, rear_third_nmm,
    #        torsion_bar_od_mm, rear_spring_nmm, front_arb_size, front_arb_blade,
    #        rear_arb_size, rear_arb_blade, front_camber_deg, rear_camber_deg,
    #        front_toe_mm, rear_toe_mm, brake_bias_pct,
    #        dampers: {lf/rf/lr/rr: {ls_comp, ls_rbd, hs_comp, hs_rbd, hs_slope}}

    # ── Performance Metrics (what happened) ──
    performance: dict = field(default_factory=dict)
    # Keys: best_lap_time_s, lap_number, consistency_cv,
    #        median_speed_kph, max_speed_kph

    # ── Telemetry Measurements (what the car did) ──
    telemetry: dict = field(default_factory=dict)
    # Keys: dynamic_front_rh_mm, dynamic_rear_rh_mm,
    #        front_rh_std_mm, rear_rh_std_mm,
    #        front_shock_vel_p95_mps, rear_shock_vel_p95_mps,
    #        front_shock_vel_p99_mps, rear_shock_vel_p99_mps,
    #        peak_lat_g, mean_abs_lat_g,
    #        body_roll_p95_deg, body_roll_max_deg,
    #        roll_gradient_deg_per_g, lltd_measured,
    #        understeer_mean_deg, understeer_high_speed_deg, understeer_low_speed_deg,
    #        body_slip_p95_deg,
    #        front_bottoming_events, rear_bottoming_events,
    #        front_rh_settle_time_ms, rear_rh_settle_time_ms,
    #        front_dominant_freq_hz, rear_dominant_freq_hz

    # ── Driver Profile (how they drove) ──
    driver_profile: dict = field(default_factory=dict)
    # Keys: style, trail_braking_depth, trail_braking_class,
    #        throttle_progressiveness, steering_smoothness,
    #        consistency, cornering_aggression

    # ── Diagnosis (what was wrong) ──
    diagnosis: dict = field(default_factory=dict)
    # Keys: assessment, problem_count,
    #        problems: [{category, severity, symptom, measured, threshold}]

    # ── Track Conditions ──
    conditions: dict = field(default_factory=dict)
    # Keys: surface_temp_c, air_temp_c, track_state

    # ── Solver Comparison (if solver was also run) ──
    solver_comparison: dict = field(default_factory=dict)
    # Keys: parameters_exact_match, parameters_within_2, parameters_off,
    #        biggest_disagreement, solver_version

    # ── Corner-by-Corner Performance ──
    corner_performance: list[dict] = field(default_factory=list)
    # Each: {corner_id, lap_dist_m, direction, speed_class, speed_kph,
    #         understeer_deg, body_slip_deg, shock_vel_p95, time_delta_s}

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Observation":
        return Observation(**{k: v for k, v in d.items()
                             if k in Observation.__dataclass_fields__})


def build_observation(
    session_id: str,
    ibt_path: str,
    car_name: str,
    track_profile,       # TrackProfile
    measured_state,       # MeasuredState
    current_setup,        # CurrentSetup
    driver_profile_obj,   # DriverProfile
    diagnosis_obj,        # Diagnosis
    corners: list = None, # list[CornerAnalysis]
) -> Observation:
    """Build an Observation from analyzer outputs.

    This is the bridge between the existing analyzer pipeline and the
    learning system. It extracts the subset of data we want to persist
    and learn from.
    """
    from analyzer.extract import MeasuredState
    from analyzer.setup_reader import CurrentSetup
    from analyzer.driver_style import DriverProfile
    from analyzer.diagnose import Diagnosis
    from track_model.profile import TrackProfile

    m: MeasuredState = measured_state
    s: CurrentSetup = current_setup
    d: DriverProfile = driver_profile_obj
    diag: Diagnosis = diagnosis_obj
    tp: TrackProfile = track_profile

    # ── Setup dict ──
    setup = {
        "wing": s.wing_angle_deg,
        "fuel_l": s.fuel_l,
        "front_rh_static": s.static_front_rh_mm,
        "rear_rh_static": s.static_rear_rh_mm,
        "front_pushrod": s.front_pushrod_mm,
        "rear_pushrod": s.rear_pushrod_mm,
        "front_heave_nmm": s.front_heave_nmm,
        "rear_third_nmm": s.rear_third_nmm,
        "torsion_bar_od_mm": s.front_torsion_od_mm,
        "rear_spring_nmm": s.rear_spring_nmm,
        "front_arb_size": s.front_arb_size,
        "front_arb_blade": s.front_arb_blade,
        "rear_arb_size": s.rear_arb_size,
        "rear_arb_blade": s.rear_arb_blade,
        "front_camber_deg": s.front_camber_deg,
        "rear_camber_deg": s.rear_camber_deg,
        "front_toe_mm": s.front_toe_mm,
        "rear_toe_mm": s.rear_toe_mm,
        "brake_bias_pct": s.brake_bias_pct,
    }
    # Dampers
    setup["dampers"] = {
        "lf": {"ls_comp": s.front_ls_comp, "ls_rbd": s.front_ls_rbd,
                "hs_comp": s.front_hs_comp, "hs_rbd": s.front_hs_rbd,
                "hs_slope": s.front_hs_slope},
        "rf": {"ls_comp": s.front_ls_comp, "ls_rbd": s.front_ls_rbd,
                "hs_comp": s.front_hs_comp, "hs_rbd": s.front_hs_rbd,
                "hs_slope": s.front_hs_slope},
        "lr": {"ls_comp": s.rear_ls_comp, "ls_rbd": s.rear_ls_rbd,
                "hs_comp": s.rear_hs_comp, "hs_rbd": s.rear_hs_rbd,
                "hs_slope": s.rear_hs_slope},
        "rr": {"ls_comp": s.rear_ls_comp, "ls_rbd": s.rear_ls_rbd,
                "hs_comp": s.rear_hs_comp, "hs_rbd": s.rear_hs_rbd,
                "hs_slope": s.rear_hs_slope},
    }

    # ── Performance ──
    performance = {
        "best_lap_time_s": diag.lap_time_s,
        "lap_number": diag.lap_number,
        "median_speed_kph": tp.median_speed_kph,
        "max_speed_kph": tp.max_speed_kph,
    }
    if hasattr(d, "apex_speed_cv"):
        performance["consistency_cv"] = d.apex_speed_cv

    # ── Telemetry ──
    telemetry = {
        "dynamic_front_rh_mm": m.mean_front_rh_at_speed_mm,
        "dynamic_rear_rh_mm": m.mean_rear_rh_at_speed_mm,
        "front_rh_std_mm": m.front_rh_std_mm,
        "rear_rh_std_mm": m.rear_rh_std_mm,
        "front_shock_vel_p95_mps": m.front_shock_vel_p95_mps,
        "rear_shock_vel_p95_mps": m.rear_shock_vel_p95_mps,
        "front_shock_vel_p99_mps": m.front_shock_vel_p99_mps,
        "rear_shock_vel_p99_mps": m.rear_shock_vel_p99_mps,
        "peak_lat_g": m.peak_lat_g_measured,
        "body_roll_p95_deg": getattr(m, "body_roll_p95_deg", 0.0),
        "body_roll_max_deg": getattr(m, "body_roll_at_peak_g_deg", 0.0),
        "roll_gradient_deg_per_g": m.roll_gradient_measured_deg_per_g,
        "lltd_measured": m.lltd_measured,
        "understeer_mean_deg": getattr(m, "understeer_mean_deg", 0.0),
        "understeer_high_speed_deg": getattr(m, "understeer_high_speed_deg", 0.0),
        "understeer_low_speed_deg": getattr(m, "understeer_low_speed_deg", 0.0),
        "body_slip_p95_deg": getattr(m, "body_slip_p95_deg", 0.0),
        "front_bottoming_events": m.bottoming_event_count_front,
        "rear_bottoming_events": m.bottoming_event_count_rear,
        "front_rh_settle_time_ms": m.front_rh_settle_time_ms,
        "rear_rh_settle_time_ms": m.rear_rh_settle_time_ms,
        "front_dominant_freq_hz": m.front_dominant_freq_hz,
        "rear_dominant_freq_hz": m.rear_dominant_freq_hz,
    }

    # ── Driver ──
    driver = {
        "style": d.style,
        "trail_braking_depth": d.trail_brake_depth_mean,
        "trail_braking_class": d.trail_brake_classification,
        "throttle_progressiveness": d.throttle_progressiveness,
        "steering_smoothness": d.steering_smoothness,
        "consistency": d.consistency,
        "cornering_aggression": d.cornering_aggression,
    }

    # ── Diagnosis ──
    diagnosis_dict = {
        "assessment": diag.assessment,
        "problem_count": len(diag.problems),
        "problems": [
            {
                "category": p.category,
                "severity": p.severity,
                "symptom": p.symptom,
                "measured": p.measured,
                "threshold": p.threshold,
                "speed_context": p.speed_context,
            }
            for p in diag.problems
        ],
    }

    # ── Conditions ──
    conditions = {}
    if tp.telemetry_source:
        conditions["telemetry_source"] = tp.telemetry_source

    # ── Corner performance ──
    corner_perf = []
    if corners:
        for c in corners:
            corner_perf.append({
                "corner_id": getattr(c, "corner_id", 0),
                "lap_dist_m": getattr(c, "lap_dist_m", 0.0),
                "direction": getattr(c, "direction", ""),
                "speed_class": getattr(c, "speed_class", ""),
                "speed_kph": getattr(c, "speed_kph", 0.0),
                "understeer_deg": getattr(c, "understeer_deg", 0.0),
                "body_slip_deg": getattr(c, "body_slip_deg", 0.0),
            })

    return Observation(
        session_id=session_id,
        ibt_path=str(ibt_path),
        car=car_name,
        track=tp.track_name,
        track_config=tp.track_config,
        timestamp=datetime.now(timezone.utc).isoformat(),
        setup=setup,
        performance=performance,
        telemetry=telemetry,
        driver_profile=driver,
        diagnosis=diagnosis_dict,
        conditions=conditions,
        corner_performance=corner_perf,
    )
