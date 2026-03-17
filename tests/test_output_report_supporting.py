import unittest
from types import SimpleNamespace

from output.report import print_full_setup_report
from pipeline.report import generate_report


def _damper_corner() -> SimpleNamespace:
    return SimpleNamespace(ls_comp=6, ls_rbd=7, hs_comp=5, hs_rbd=8, hs_slope=10)


class OutputReportSupportingTests(unittest.TestCase):
    def test_compact_report_surfaces_brake_hardware_and_diff_lock(self) -> None:
        report = print_full_setup_report(
            car_name="BMW M Hybrid V8",
            track_name="Sebring International",
            wing=17.0,
            target_balance=50.14,
            step1=SimpleNamespace(
                df_balance_pct=50.14,
                static_front_rh_mm=30.1,
                static_rear_rh_mm=49.5,
                dynamic_front_rh_mm=15.0,
                dynamic_rear_rh_mm=40.0,
                ld_ratio=3.8,
                rake_static_mm=19.4,
                front_pushrod_offset_mm=-26.5,
                rear_pushrod_offset_mm=-24.0,
                vortex_burst_margin_mm=2.5,
            ),
            step2=SimpleNamespace(
                front_heave_nmm=50.0,
                rear_third_nmm=530.0,
                perch_offset_front_mm=-11.0,
                perch_offset_rear_mm=42.5,
                slider_static_front_mm=43.0,
                travel_margin_front_mm=6.0,
                front_excursion_at_rate_mm=9.0,
                front_bottoming_margin_mm=4.0,
            ),
            step3=SimpleNamespace(
                front_torsion_od_mm=13.9,
                rear_spring_rate_nmm=160.0,
                rear_spring_perch_mm=30.0,
            ),
            step4=SimpleNamespace(
                front_arb_size="Soft",
                front_arb_blade_start=1,
                rear_arb_size="Medium",
                rear_arb_blade_start=3,
                rarb_blade_slow_corner=1,
                rarb_blade_fast_corner=5,
                lltd_achieved=0.55,
                lltd_target=0.54,
            ),
            step5=SimpleNamespace(
                front_camber_deg=-2.9,
                rear_camber_deg=-1.9,
                front_toe_mm=-0.4,
                rear_toe_mm=0.0,
                camber_confidence="calibrated",
            ),
            step6=SimpleNamespace(
                lf=_damper_corner(),
                rf=_damper_corner(),
                lr=_damper_corner(),
                rr=_damper_corner(),
            ),
            supporting=SimpleNamespace(
                tyre_cold_fl_kpa=152.0,
                tyre_cold_fr_kpa=152.0,
                tyre_cold_rl_kpa=152.0,
                tyre_cold_rr_kpa=152.0,
                brake_bias_pct=46.0,
                brake_bias_target=1.5,
                brake_bias_migration=-0.5,
                front_master_cyl_mm=19.1,
                rear_master_cyl_mm=20.6,
                pad_compound="Medium",
                diff_preload_nm=20.0,
                diff_ramp_coast=40,
                diff_ramp_drive=65,
                diff_clutch_plates=4,
                tc_gain=4,
                tc_slip=3,
                _diff_solution=SimpleNamespace(
                    lock_pct_coast=47.0,
                    lock_pct_drive=31.0,
                    preload_contribution_pct=5.0,
                    plate_contribution_pct=42.0,
                ),
            ),
            compact=True,
        )

        self.assertIn("Brake target/mig: +1.5 / -0.5", report)
        self.assertIn("Master cyl: 19.1/20.6 mm", report)
        self.assertIn("Diff lock coast/drive: 47.0% / 31.0%", report)
        self.assertIn("Diff preload / plate %   5.0% / 42.0%", report)

    def test_compact_pipeline_report_renders_predicted_improvements_without_full_solver_imports(self) -> None:
        report = generate_report(
            car=SimpleNamespace(
                name="BMW M Hybrid V8",
                canonical_name="bmw",
                active_garage_output_model=lambda _track_name: None,
            ),
            track=SimpleNamespace(track_name="Sebring", track_config="International"),
            measured=SimpleNamespace(
                lap_number=4,
                lap_time_s=109.8,
                front_heave_travel_used_pct=88.0,
                front_rh_excursion_measured_mm=10.5,
                rear_rh_std_mm=7.1,
                pitch_range_braking_deg=1.1,
                front_braking_lock_ratio_p95=0.08,
                rear_power_slip_ratio_p95=0.09,
                body_slip_p95_deg=4.0,
                understeer_low_speed_deg=1.2,
                understeer_high_speed_deg=1.5,
                front_pressure_mean_kpa=171.0,
                rear_pressure_mean_kpa=172.0,
                telemetry_signals={},
                metric_fallbacks=[],
            ),
            driver=SimpleNamespace(
                style="smooth-consistent",
                trail_brake_classification="moderate",
                trail_brake_depth_mean=0.3,
                throttle_classification="moderate",
                consistency="consistent",
            ),
            diagnosis=SimpleNamespace(problems=[], state_issues=[], overhaul_assessment=None, causal_diagnosis=None),
            corners=[],
            aero_grad=None,
            modifiers=SimpleNamespace(),
            step1=SimpleNamespace(
                df_balance_pct=50.14,
                static_front_rh_mm=30.1,
                static_rear_rh_mm=49.5,
                dynamic_front_rh_mm=15.0,
                dynamic_rear_rh_mm=40.0,
                ld_ratio=3.8,
                rake_static_mm=19.4,
                front_pushrod_offset_mm=-26.5,
                rear_pushrod_offset_mm=-24.0,
                vortex_burst_margin_mm=2.5,
            ),
            step2=SimpleNamespace(
                front_heave_nmm=52.0,
                rear_third_nmm=560.0,
                perch_offset_front_mm=-11.0,
                perch_offset_rear_mm=42.5,
                slider_static_front_mm=43.0,
                travel_margin_front_mm=6.0,
                front_excursion_at_rate_mm=9.0,
                front_bottoming_margin_mm=4.0,
            ),
            step3=SimpleNamespace(
                front_torsion_od_mm=13.9,
                rear_spring_rate_nmm=160.0,
                rear_spring_perch_mm=30.0,
            ),
            step4=SimpleNamespace(
                front_arb_size="Soft",
                front_arb_blade_start=1,
                rear_arb_size="Medium",
                rear_arb_blade_start=3,
                rarb_blade_slow_corner=1,
                rarb_blade_fast_corner=5,
                lltd_achieved=0.55,
                lltd_target=0.54,
            ),
            step5=SimpleNamespace(
                front_camber_deg=-2.9,
                rear_camber_deg=-1.9,
                front_toe_mm=-0.4,
                rear_toe_mm=0.0,
                camber_confidence="calibrated",
            ),
            step6=SimpleNamespace(
                lf=_damper_corner(),
                rf=_damper_corner(),
                lr=_damper_corner(),
                rr=_damper_corner(),
            ),
            supporting=SimpleNamespace(
                tyre_cold_fl_kpa=152.0,
                tyre_cold_fr_kpa=152.0,
                tyre_cold_rl_kpa=152.0,
                tyre_cold_rr_kpa=152.0,
                brake_bias_pct=45.5,
                brake_bias_target=1.5,
                brake_bias_migration=-0.5,
                front_master_cyl_mm=19.1,
                rear_master_cyl_mm=20.6,
                pad_compound="Medium",
                brake_hardware_status="static bias solved; target/migration/master cylinders/pad are pass-through only",
                diff_preload_nm=20.0,
                diff_ramp_coast=40,
                diff_ramp_drive=65,
                diff_clutch_plates=4,
                tc_gain=4,
                tc_slip=3,
                _diff_solution=SimpleNamespace(
                    lock_pct_coast=47.0,
                    lock_pct_drive=31.0,
                    preload_contribution_pct=5.0,
                    plate_contribution_pct=42.0,
                ),
            ),
            current_setup=SimpleNamespace(
                fuel_l=89.0,
                torsion_bar_turns=0.1,
                brake_bias_target=1.5,
                brake_bias_migration=-0.5,
                front_master_cyl_mm=19.1,
                rear_master_cyl_mm=20.6,
                pad_compound="Medium",
            ),
            wing=17.0,
            target_balance=50.14,
            prediction_corrections={},
            selected_candidate_family="compromise",
            selected_candidate_score=0.713,
            solve_context_lines=["Candidate family selected: compromise (score 0.713)"],
            compact=True,
        )

        self.assertIn("CANDIDATE SELECTION", report)
        self.assertIn("Selected family: compromise", report)
        self.assertIn("PREDICTED IMPROVEMENTS", report)
        self.assertIn("Front travel used", report)


if __name__ == "__main__":
    unittest.main()
