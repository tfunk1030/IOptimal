import unittest
from types import SimpleNamespace

from car_model import get_car
from solver.decision_trace import build_parameter_decisions
from solver.brake_solver import BrakeSolver, compute_brake_bias
from solver.supporting_solver import SupportingSolver


class BrakeSolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.car = get_car("bmw")
        self.driver = SimpleNamespace(
            trail_brake_depth_p95=0.45,
            trail_brake_classification="deep",
        )
        self.measured = SimpleNamespace(
            braking_decel_peak_g=2.2,
            front_braking_lock_ratio_p95=0.08,
            front_slip_ratio_p95=0.08,
            front_brake_wheel_decel_asymmetry_p95_ms2=4.2,
            body_slip_p95_deg=3.0,
            hydraulic_brake_split_pct=46.5,
            abs_active_pct=18.0,
            abs_cut_mean_pct=22.0,
        )

    def test_compute_brake_bias_returns_fuel_adjusted_baseline(self) -> None:
        bias_empty, reason_empty = compute_brake_bias(self.car, fuel_load_l=12.0)
        bias_full, reason_full = compute_brake_bias(self.car, fuel_load_l=89.0)
        self.assertIn("12L", reason_empty)
        self.assertIn("89L", reason_full)
        self.assertLessEqual(abs(bias_empty - self.car.brake_bias_pct), 1.0)
        self.assertLessEqual(abs(bias_full - self.car.brake_bias_pct), 1.0)

    def test_brake_solver_includes_hardware_context_in_reasoning(self) -> None:
        current_setup = SimpleNamespace(
            brake_bias_target=1.5,
            brake_bias_migration=-0.5,
            front_master_cyl_mm=19.1,
            rear_master_cyl_mm=20.6,
            pad_compound="Medium",
        )
        solution = BrakeSolver(
            car=self.car,
            driver=self.driver,
            measured=self.measured,
            diagnosis=None,
            current_setup=current_setup,
            fuel_load_l=89.0,
        ).solve()

        self.assertGreater(solution.brake_bias_pct, 0.0)
        self.assertIn("Current migration -0.5", solution.reasoning)
        self.assertIn("Current target +1.5", solution.reasoning)
        self.assertIn("MC: F/R = 19.1/20.6 mm", solution.reasoning)
        self.assertIn("Pad: Medium", solution.reasoning)

    def test_supporting_solver_propagates_brake_hardware_outputs(self) -> None:
        current_setup = SimpleNamespace(
            brake_bias_target=1.5,
            brake_bias_migration=-0.5,
            front_master_cyl_mm=19.1,
            rear_master_cyl_mm=20.6,
            pad_compound="Medium",
            diff_clutch_plates=4,
        )
        driver = SimpleNamespace(
            trail_brake_depth_p95=0.45,
            trail_brake_classification="deep",
            throttle_classification="moderate",
            throttle_progressiveness=0.6,
            throttle_onset_rate_pct_per_s=220.0,
            consistency="consistent",
        )
        measured = SimpleNamespace(
            fuel_level_at_measurement_l=89.0,
            braking_decel_peak_g=2.2,
            front_braking_lock_ratio_p95=0.08,
            front_slip_ratio_p95=0.08,
            front_brake_wheel_decel_asymmetry_p95_ms2=4.2,
            body_slip_p95_deg=3.0,
            hydraulic_brake_split_pct=46.5,
            abs_active_pct=18.0,
            abs_cut_mean_pct=22.0,
            rear_power_slip_ratio_p95=0.05,
            rear_slip_ratio_p95=0.05,
            tc_intervention_pct=0.0,
            mguk_torque_peak_nm=0.0,
            ers_battery_min_pct=0.0,
            front_pressure_mean_kpa=165.0,
            rear_pressure_mean_kpa=166.0,
            front_carcass_mean_c=92.0,
            rear_carcass_mean_c=93.0,
            track_temp_c=30.0,
            lf_pressure_kpa=165.0,
            rf_pressure_kpa=165.0,
            lr_pressure_kpa=166.0,
            rr_pressure_kpa=166.0,
        )
        diagnosis = SimpleNamespace()

        solution = SupportingSolver(
            self.car,
            driver,
            measured,
            diagnosis,
            current_setup=current_setup,
        ).solve()

        self.assertLess(solution.brake_bias_target, 1.5)
        self.assertLess(solution.brake_bias_migration, -0.5)
        self.assertLess(solution.front_master_cyl_mm, 19.1)
        self.assertGreater(solution.rear_master_cyl_mm, 20.6)
        self.assertNotEqual(solution.pad_compound, "Medium")
        self.assertEqual(solution.brake_bias_status, "solved")
        self.assertEqual(solution.brake_bias_target_status, "seeded_from_telemetry")
        self.assertEqual(solution.brake_bias_migration_status, "seeded_from_telemetry")
        self.assertEqual(solution.master_cylinder_status, "seeded_from_telemetry")
        self.assertEqual(solution.pad_compound_status, "seeded_from_telemetry")

    def test_decision_trace_surfaces_seeded_brake_hardware_changes(self) -> None:
        current_setup = SimpleNamespace(
            front_pushrod_mm=-26.5,
            rear_pushrod_mm=-24.0,
            front_heave_nmm=50.0,
            front_heave_perch_mm=-11.0,
            rear_third_nmm=530.0,
            rear_third_perch_mm=42.5,
            front_torsion_od_mm=13.9,
            rear_spring_nmm=160.0,
            front_camber_deg=-2.9,
            rear_camber_deg=-1.9,
            front_toe_mm=-0.4,
            rear_toe_mm=0.0,
            brake_bias_pct=46.0,
            brake_bias_target=1.5,
            brake_bias_migration=-0.5,
            front_master_cyl_mm=19.1,
            rear_master_cyl_mm=20.6,
            pad_compound="Medium",
            diff_preload_nm=20.0,
            tc_gain=4,
            tc_slip=3,
        )
        measured = SimpleNamespace(
            front_braking_lock_ratio_p95=0.08,
            pitch_range_braking_deg=1.1,
            rear_power_slip_ratio_p95=0.06,
            front_heave_travel_used_pct=85.0,
            bottoming_event_count_front_clean=0,
            rear_rh_std_mm=6.0,
            front_rh_std_mm=4.0,
            understeer_mean_deg=0.4,
            front_carcass_mean_c=92.0,
            front_pressure_mean_kpa=166.0,
            rear_carcass_mean_c=93.0,
            rear_pressure_mean_kpa=167.0,
            telemetry_signals={},
        )
        supporting = SimpleNamespace(
            brake_bias_pct=45.8,
            brake_bias_target=1.0,
            brake_bias_migration=-1.0,
            brake_bias_target_status="seeded_from_telemetry",
            brake_bias_migration_status="seeded_from_telemetry",
            front_master_cyl_mm=17.8,
            rear_master_cyl_mm=22.2,
            master_cylinder_status="seeded_from_telemetry",
            pad_compound="Low",
            pad_compound_status="seeded_from_telemetry",
            diff_preload_nm=20.0,
            tc_gain=4,
            tc_slip=3,
        )

        decisions = build_parameter_decisions(
            car_name="bmw",
            current_setup=current_setup,
            measured=measured,
            step1=SimpleNamespace(front_pushrod_offset_mm=-26.0, rear_pushrod_offset_mm=-24.0),
            step2=SimpleNamespace(front_heave_nmm=52.0, perch_offset_front_mm=-11.0, rear_third_nmm=540.0),
            step3=SimpleNamespace(front_torsion_od_mm=13.9, rear_spring_rate_nmm=160.0),
            step4=SimpleNamespace(),
            step5=SimpleNamespace(front_camber_deg=-3.0, rear_camber_deg=-1.9, front_toe_mm=-0.5, rear_toe_mm=0.0),
            step6=SimpleNamespace(),
            supporting=supporting,
            legality=SimpleNamespace(valid=True),
            fallback_reasons=[],
        )

        by_parameter = {decision.parameter: decision for decision in decisions}
        for key in (
            "brake_bias_target",
            "brake_bias_migration",
            "front_master_cyl_mm",
            "rear_master_cyl_mm",
            "pad_compound",
        ):
            self.assertIn(key, by_parameter)
            self.assertEqual(by_parameter[key].legality_status, "validated")
            self.assertEqual(by_parameter[key].evidence.source_tier, "seeded_from_telemetry")

    def test_ferrari_supporting_solver_prefers_live_brake_bias_over_hydraulic_split(self) -> None:
        ferrari = get_car("ferrari")
        current_setup = SimpleNamespace(
            adapter_name="ferrari",
            brake_bias_pct=54.0,
            brake_bias_target=0.0,
            brake_bias_migration=1.0,
            front_master_cyl_mm=19.1,
            rear_master_cyl_mm=20.6,
            pad_compound="Medium",
            diff_clutch_plates=4,
            diff_preload_nm=20.0,
            tc_gain=4,
            tc_slip=3,
        )
        driver = SimpleNamespace(
            trail_brake_depth_p95=0.45,
            trail_brake_classification="deep",
            throttle_classification="moderate",
            throttle_progressiveness=0.6,
            throttle_onset_rate_pct_per_s=220.0,
            consistency="consistent",
        )
        measured = SimpleNamespace(
            fuel_level_at_measurement_l=89.0,
            braking_decel_peak_g=2.2,
            front_braking_lock_ratio_p95=0.08,
            front_slip_ratio_p95=0.08,
            front_brake_wheel_decel_asymmetry_p95_ms2=4.2,
            body_slip_p95_deg=3.0,
            hydraulic_brake_split_pct=12.0,
            live_brake_bias_pct=53.7,
            abs_active_pct=18.0,
            abs_cut_mean_pct=22.0,
            rear_power_slip_ratio_p95=0.05,
            rear_slip_ratio_p95=0.05,
            tc_intervention_pct=0.0,
            mguk_torque_peak_nm=0.0,
            ers_battery_min_pct=0.0,
            front_pressure_mean_kpa=165.0,
            rear_pressure_mean_kpa=166.0,
            front_carcass_mean_c=92.0,
            rear_carcass_mean_c=93.0,
            track_temp_c=30.0,
            lf_pressure_kpa=165.0,
            rf_pressure_kpa=165.0,
            lr_pressure_kpa=166.0,
            rr_pressure_kpa=166.0,
            live_tc_gain=None,
            live_tc_slip=None,
        )
        diagnosis = SimpleNamespace()

        solution = SupportingSolver(
            ferrari,
            driver,
            measured,
            diagnosis,
            current_setup=current_setup,
        ).solve()

        self.assertEqual(solution.brake_bias_pct, 53.7)
        self.assertEqual(solution.brake_bias_status, "telemetry_passthrough")
        self.assertIn("dcBrakeBias telemetry", solution.brake_bias_reasoning)
        self.assertIn("Hydraulic split remains diagnostic only", solution.brake_bias_reasoning)


if __name__ == "__main__":
    unittest.main()
