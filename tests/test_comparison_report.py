import unittest
from types import SimpleNamespace

from comparison.compare import SessionAnalysis, compare_sessions
from comparison.report import format_comparison_report
from comparison.score import score_sessions
from comparison.synthesize import SynthesisResult
from solver.modifiers import SolverModifiers


def _measured(**overrides):
    values = {
        "lap_time_s": 100.0,
        "speed_max_kph": 300.0,
        "speed_mean_kph": 190.0,
        "front_rh_std_mm": 4.0,
        "rear_rh_std_mm": 4.5,
        "front_rh_excursion_measured_mm": 12.0,
        "rear_rh_excursion_measured_mm": 14.0,
        "aero_compression_front_mm": 12.0,
        "aero_compression_rear_mm": 18.0,
        "bottoming_event_count_front": 0,
        "bottoming_event_count_rear": 0,
        "vortex_burst_event_count": 0,
        "front_shock_vel_p99_mps": 0.22,
        "rear_shock_vel_p99_mps": 0.24,
        "front_rh_settle_time_ms": 125.0,
        "rear_rh_settle_time_ms": 125.0,
        "peak_lat_g_measured": 2.0,
        "lltd_measured": 0.52,
        "understeer_mean_deg": 0.1,
        "understeer_low_speed_deg": 0.1,
        "understeer_high_speed_deg": 0.2,
        "body_slip_p95_deg": 2.0,
        "front_slip_ratio_p95": 0.04,
        "rear_slip_ratio_p95": 0.05,
        "yaw_rate_correlation": 0.9,
        "roll_rate_p95_deg_per_s": 6.0,
        "front_temp_spread_lf_c": 8.0,
        "front_temp_spread_rf_c": 8.0,
        "rear_temp_spread_lr_c": 7.0,
        "rear_temp_spread_rr_c": 7.0,
        "front_carcass_mean_c": 92.0,
        "rear_carcass_mean_c": 93.0,
        "front_pressure_mean_kpa": 165.0,
        "rear_pressure_mean_kpa": 166.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _setup(**overrides):
    values = {
        "wing_angle_deg": 17.0,
        "static_front_rh_mm": 30.0,
        "static_rear_rh_mm": 48.0,
        "front_pushrod_mm": -25.5,
        "rear_pushrod_mm": -20.0,
        "front_heave_nmm": 30.0,
        "front_heave_perch_mm": -20.0,
        "rear_third_nmm": 380.0,
        "rear_third_perch_mm": 36.0,
        "front_torsion_od_mm": 14.3,
        "rear_spring_nmm": 180.0,
        "front_arb_blade": 1,
        "rear_arb_blade": 1,
        "front_camber_deg": -2.8,
        "rear_camber_deg": -1.8,
        "front_toe_mm": -0.5,
        "rear_toe_mm": 0.0,
        "front_ls_comp": 8,
        "front_ls_rbd": 7,
        "front_hs_comp": 5,
        "front_hs_rbd": 8,
        "rear_ls_comp": 6,
        "rear_ls_rbd": 7,
        "rear_hs_comp": 6,
        "rear_hs_rbd": 9,
        "brake_bias_pct": 46.0,
        "brake_bias_target": 0.0,
        "brake_bias_migration": 0.0,
        "front_master_cyl_mm": 19.1,
        "rear_master_cyl_mm": 20.6,
        "pad_compound": "Medium",
        "diff_preload_nm": 20.0,
        "diff_clutch_plates": 6,
        "tc_gain": 4,
        "tc_slip": 4,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _session(label, lap_time_s, setup=None, measured=None):
    return SessionAnalysis(
        label=label,
        ibt_path=f"{label}.ibt",
        setup=setup or _setup(),
        measured=measured or _measured(lap_time_s=lap_time_s),
        corners=[],
        driver=SimpleNamespace(style="aggressive-variable", avg_peak_lat_g_utilization=0.9),
        diagnosis=SimpleNamespace(assessment="compromised", problems=[]),
        track=SimpleNamespace(),
        lap_time_s=lap_time_s,
        lap_number=10,
        session_context=None,
        track_name="Sebring International Raceway",
        wing_angle=17.0,
        car_name="BMW M Hybrid V8",
    )


class ComparisonReportTests(unittest.TestCase):
    def test_report_uses_car_name_and_reasoning_synthesis_metadata(self) -> None:
        sessions = [
            _session("S1", 100.0),
            _session("S2", 100.2, setup=_setup(rear_arb_blade=2)),
        ]
        comparison = compare_sessions(sessions)
        scoring = score_sessions(comparison)
        synthesis = SynthesisResult(
            step1=SimpleNamespace(dynamic_front_rh_mm=19.2, dynamic_rear_rh_mm=43.1, df_balance_pct=50.3),
            step2=SimpleNamespace(front_heave_nmm=40.0, rear_third_nmm=420.0),
            step3=SimpleNamespace(front_torsion_od_mm=14.3, rear_spring_rate_nmm=165.0),
            step4=SimpleNamespace(lltd_achieved=0.52, farb_blade_locked=1, rarb_blade_slow_corner=2),
            step5=SimpleNamespace(front_camber_deg=-2.5, rear_camber_deg=-1.8, front_toe_mm=-0.5, rear_toe_mm=0.0),
            step6=SimpleNamespace(
                lf=SimpleNamespace(ls_comp=9, ls_rbd=7, hs_comp=8, hs_rbd=6),
                rf=SimpleNamespace(ls_comp=9, ls_rbd=7, hs_comp=8, hs_rbd=6),
                lr=SimpleNamespace(ls_comp=5, ls_rbd=6, hs_comp=8, hs_rbd=8),
                rr=SimpleNamespace(ls_comp=5, ls_rbd=6, hs_comp=8, hs_rbd=8),
            ),
            supporting=SimpleNamespace(brake_bias_pct=46.2, diff_preload_nm=21.0, tc_gain=4, tc_slip=4),
            modifiers=SolverModifiers(reasons=["Rear clean-track bottoming across sessions"]),
            explanations=[],
            confidence={"authority": "high"},
            wing_angle=17.0,
            fuel_l=89.0,
            best_session_label="S1",
            authority_session_label="S2",
            selected_candidate_family="compromise",
            selected_candidate_score=0.622,
            solve_basis="latest_validation_veto",
            solver_notes=["Applied rematerialized compromise candidate result to final report/JSON/export payloads."],
        )

        report = format_comparison_report(comparison, scoring, synthesis)

        self.assertIn("Car:      BMW M Hybrid V8", report)
        self.assertIn("Candidate family: compromise", report)
        self.assertIn("Authority: S2", report)
        self.assertIn("Solve Context:", report)


if __name__ == "__main__":
    unittest.main()
