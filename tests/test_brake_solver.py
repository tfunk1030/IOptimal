import unittest
from types import SimpleNamespace

from car_model import get_car
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
        self.assertIn("Master cylinders F/R = 19.1/20.6 mm", solution.reasoning)
        self.assertIn("Pad compound: Medium", solution.reasoning)

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

        self.assertEqual(solution.brake_bias_target, 1.5)
        self.assertEqual(solution.brake_bias_migration, -0.5)
        self.assertEqual(solution.front_master_cyl_mm, 19.1)
        self.assertEqual(solution.rear_master_cyl_mm, 20.6)
        self.assertEqual(solution.pad_compound, "Medium")


if __name__ == "__main__":
    unittest.main()
