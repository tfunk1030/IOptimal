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
    #        front_heave_defl_p99_mm, front_heave_travel_used_pct,
    #        front_heave_travel_used_braking_pct,
    #        rear_heave_defl_p99_mm, rear_heave_travel_used_pct,
    #        heave_bottoming_events_front, heave_bottoming_events_rear,
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

    # ── Solver Predictions (what the solver predicted for this session) ──
    solver_predictions: dict = field(default_factory=dict)
    # Keys: front_rh_std_mm, rear_rh_std_mm, lltd_predicted,
    #        body_roll_predicted_deg_per_g, front_bottoming_predicted, etc.
    # Populated by pipeline.produce when --learn is active.

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
        "front_heave_defl_p99_mm": getattr(m, "front_heave_defl_p99_mm", 0.0),
        "front_heave_travel_used_pct": getattr(m, "front_heave_travel_used_pct", 0.0),
        "front_heave_travel_used_braking_pct": getattr(m, "front_heave_travel_used_braking_pct", 0.0),
        "rear_heave_defl_p99_mm": getattr(m, "rear_heave_defl_p99_mm", 0.0),
        "rear_heave_travel_used_pct": getattr(m, "rear_heave_travel_used_pct", 0.0),
        "heave_bottoming_events_front": getattr(m, "heave_bottoming_events_front", 0),
        "heave_bottoming_events_rear": getattr(m, "heave_bottoming_events_rear", 0),
        "front_rh_settle_time_ms": m.front_rh_settle_time_ms,
        "rear_rh_settle_time_ms": m.rear_rh_settle_time_ms,
        "front_dominant_freq_hz": m.front_dominant_freq_hz,
        "rear_dominant_freq_hz": m.rear_dominant_freq_hz,
        # New Phase 1: Splitter & shock deflection
        "splitter_rh_mean_at_speed_mm": getattr(m, "splitter_rh_mean_at_speed_mm", 0.0),
        "splitter_rh_min_mm": getattr(m, "splitter_rh_min_mm", 0.0),
        "splitter_scrape_events": getattr(m, "splitter_scrape_events", 0),
        "front_corner_defl_p99_mm": getattr(m, "front_corner_defl_p99_mm", 0.0),
        "rear_corner_defl_p99_mm": getattr(m, "rear_corner_defl_p99_mm", 0.0),
        "front_heave_vel_p95_mps": getattr(m, "front_heave_vel_p95_mps", 0.0),
        "rear_heave_vel_p95_mps": getattr(m, "rear_heave_vel_p95_mps", 0.0),
        "front_heave_vel_ls_pct": getattr(m, "front_heave_vel_ls_pct", 0.0),
        "front_heave_vel_hs_pct": getattr(m, "front_heave_vel_hs_pct", 0.0),
        # New Phase 1: Speed-dependent LLTD
        "lltd_low_speed": getattr(m, "lltd_low_speed", 0.0),
        "lltd_high_speed": getattr(m, "lltd_high_speed", 0.0),
        # New Phase 2: Brake system
        "measured_brake_bias_pct": getattr(m, "measured_brake_bias_pct", 0.0),
        "abs_active_pct": getattr(m, "abs_active_pct", 0.0),
        "abs_cut_mean_pct": getattr(m, "abs_cut_mean_pct", 0.0),
        "brake_bias_adjustments": getattr(m, "brake_bias_adjustments", 0),
        "tc_adjustments": getattr(m, "tc_adjustments", 0),
        # Environmental, hybrid, & fuel
        "fuel_level_at_measurement_l": getattr(m, "fuel_level_at_measurement_l", 0.0),
        "fuel_used_per_lap_l": getattr(m, "fuel_used_per_lap_l", 0.0),
        "ers_battery_mean_pct": getattr(m, "ers_battery_mean_pct", 0.0),
        "ers_battery_min_pct": getattr(m, "ers_battery_min_pct", 0.0),
        "mguk_torque_peak_nm": getattr(m, "mguk_torque_peak_nm", 0.0),
        "air_temp_c": getattr(m, "air_temp_c", 0.0),
        "track_temp_c": getattr(m, "track_temp_c", 0.0),
        "air_density_kg_m3": getattr(m, "air_density_kg_m3", 0.0),
        "wind_speed_ms": getattr(m, "wind_speed_ms", 0.0),
        "wind_dir_deg": getattr(m, "wind_dir_deg", 0.0),
        "rpm_at_braking_pct_at_limiter": getattr(m, "rpm_at_braking_pct_at_limiter", 0.0),
        # In-car adjustment counts (for delta confidence scoring)
        "arb_front_adjustments": getattr(m, "arb_front_adjustments", 0),
        "arb_rear_adjustments": getattr(m, "arb_rear_adjustments", 0),
        # Brake system telemetry
        "front_brake_pressure_peak_bar": getattr(m, "front_brake_pressure_peak_bar", 0.0),
        "rear_brake_pressure_peak_bar": getattr(m, "rear_brake_pressure_peak_bar", 0.0),
        "braking_decel_peak_g": getattr(m, "braking_decel_peak_g", 0.0),
        "tc_intervention_pct": getattr(m, "tc_intervention_pct", 0.0),
        # Pitch dynamics
        "pitch_mean_at_speed_deg": getattr(m, "pitch_mean_at_speed_deg", 0.0),
        "pitch_range_deg": getattr(m, "pitch_range_deg", 0.0),
        # New Phase 4: Directional understeer & per-corner shock vel
        "understeer_left_turn_deg": getattr(m, "understeer_left_turn_deg", 0.0),
        "understeer_right_turn_deg": getattr(m, "understeer_right_turn_deg", 0.0),
        "lf_shock_vel_p95_mps": getattr(m, "lf_shock_vel_p95_mps", 0.0),
        "rf_shock_vel_p95_mps": getattr(m, "rf_shock_vel_p95_mps", 0.0),
        "lr_shock_vel_p95_mps": getattr(m, "lr_shock_vel_p95_mps", 0.0),
        "rr_shock_vel_p95_mps": getattr(m, "rr_shock_vel_p95_mps", 0.0),
        # Per-corner tyre data
        "lf_pressure_kpa": getattr(m, "lf_pressure_kpa", 0.0),
        "rf_pressure_kpa": getattr(m, "rf_pressure_kpa", 0.0),
        "lr_pressure_kpa": getattr(m, "lr_pressure_kpa", 0.0),
        "rr_pressure_kpa": getattr(m, "rr_pressure_kpa", 0.0),
        "lf_cold_pressure_kpa": getattr(m, "lf_cold_pressure_kpa", 0.0),
        "rf_cold_pressure_kpa": getattr(m, "rf_cold_pressure_kpa", 0.0),
        "lr_cold_pressure_kpa": getattr(m, "lr_cold_pressure_kpa", 0.0),
        "rr_cold_pressure_kpa": getattr(m, "rr_cold_pressure_kpa", 0.0),
        "lf_wear_pct": getattr(m, "lf_wear_pct", 0.0),
        "rf_wear_pct": getattr(m, "rf_wear_pct", 0.0),
        "lr_wear_pct": getattr(m, "lr_wear_pct", 0.0),
        "rr_wear_pct": getattr(m, "rr_wear_pct", 0.0),
        "lf_temp_middle_c": getattr(m, "lf_temp_middle_c", 0.0),
        "rf_temp_middle_c": getattr(m, "rf_temp_middle_c", 0.0),
        "lr_temp_middle_c": getattr(m, "lr_temp_middle_c", 0.0),
        "rr_temp_middle_c": getattr(m, "rr_temp_middle_c", 0.0),
        # Shock oscillation analysis (P2: damper validation)
        "rear_shock_oscillation_hz": getattr(m, "rear_shock_oscillation_hz", 0.0),
        "front_shock_oscillation_hz": getattr(m, "front_shock_oscillation_hz", 0.0),
        # High-speed m_eff filtering (P3c)
        "front_heave_vel_p95_hs_mps": getattr(m, "front_heave_vel_p95_hs_mps", 0.0),
        "front_rh_std_hs_mm": getattr(m, "front_rh_std_hs_mm", 0.0),
        # Raw driver inputs
        "throttle_raw_mean": getattr(m, "throttle_raw_mean", 0.0),
        "brake_raw_peak": getattr(m, "brake_raw_peak", 0.0),
        # Gear
        "gear_at_apex_mode": getattr(m, "gear_at_apex_mode", 0),
        "max_gear": getattr(m, "max_gear", 0),
        # Extended adjustments
        "tc2_adjustments": getattr(m, "tc2_adjustments", 0),
        "abs_adjustments": getattr(m, "abs_adjustments", 0),
        "deploy_mode_adjustments": getattr(m, "deploy_mode_adjustments", 0),
    }

    # ── Driver ──
    driver = {
        "style": d.style,
        "trail_braking_depth": d.trail_brake_depth_mean,
        "trail_braking_p95": d.trail_brake_depth_p95,
        "trail_braking_class": d.trail_brake_classification,
        "brake_release_quality": getattr(d, "brake_release_quality", 0.0),
        "throttle_progressiveness": d.throttle_progressiveness,
        "throttle_onset_rate_pct_per_s": d.throttle_onset_rate_pct_per_s,
        "throttle_onset_aggression": getattr(d, "throttle_onset_aggression", 0.0),
        "throttle_classification": d.throttle_classification,
        "steering_smoothness": d.steering_smoothness,
        "steering_jerk_p95_rad_per_s2": d.steering_jerk_p95_rad_per_s2,
        "apex_speed_cv": d.apex_speed_cv,
        "driver_noise_index": getattr(d, "driver_noise_index", 0.0),
        "classification_confidence": getattr(d, "classification_confidence", 0.0),
        "consistency": d.consistency,
        "cornering_aggression": d.cornering_aggression,
        "avg_peak_lat_g_utilization": d.avg_peak_lat_g_utilization,
    }

    # ── Diagnosis ──
    diagnosis_dict = {
        "assessment": diag.assessment,
        "problem_count": len(diag.problems),
        "evidence_strength": getattr(diag, "evidence_strength", 0.0),
        "overhaul_assessment": (
            {
                "classification": diag.overhaul_assessment.classification,
                "confidence": diag.overhaul_assessment.confidence,
                "score": diag.overhaul_assessment.score,
                "reasons": list(diag.overhaul_assessment.reasons),
            }
            if getattr(diag, "overhaul_assessment", None) is not None
            else None
        ),
        "state_issues": [
            {
                "state_id": issue.state_id,
                "severity": issue.severity,
                "confidence": issue.confidence,
                "estimated_loss_ms": issue.estimated_loss_ms,
                "implicated_steps": list(issue.implicated_steps),
                "likely_causes": list(issue.likely_causes),
                "recommended_direction": issue.recommended_direction,
            }
            for issue in getattr(diag, "state_issues", [])
        ],
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
                "lap_dist_m": getattr(c, "lap_dist_start_m", 0.0),
                "direction": getattr(c, "direction", ""),
                "speed_class": getattr(c, "speed_class", ""),
                "speed_kph": getattr(c, "apex_speed_kph", 0.0),
                "understeer_deg": getattr(c, "understeer_mean_deg", 0.0),
                "body_slip_deg": getattr(c, "body_slip_peak_deg", 0.0),
                "braking_phase_s": getattr(c, "braking_phase_s", 0.0),
                "release_phase_s": getattr(c, "release_phase_s", 0.0),
                "turn_in_phase_s": getattr(c, "turn_in_phase_s", 0.0),
                "apex_phase_s": getattr(c, "apex_phase_s", 0.0),
                "throttle_pickup_phase_s": getattr(c, "throttle_pickup_phase_s", 0.0),
                "exit_phase_s": getattr(c, "exit_phase_s", 0.0),
                "corner_confidence": getattr(c, "corner_confidence", 0.0),
                "entry_pitch_severity": getattr(c, "entry_pitch_severity", 0.0),
                "aero_collapse_severity": getattr(c, "aero_collapse_severity", 0.0),
                "exit_slip_severity": getattr(c, "exit_slip_severity", 0.0),
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
