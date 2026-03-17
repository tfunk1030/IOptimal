import unittest
from types import SimpleNamespace

from car_model import get_car
from solver.brake_solver import BrakeSolver, compute_brake_bias


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


if __name__ == "__main__":
    unittest.main()
