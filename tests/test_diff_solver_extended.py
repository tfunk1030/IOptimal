import unittest
from types import SimpleNamespace

from car_model import get_car
from solver.diff_solver import DiffSolver


class DiffSolverExtendedTests(unittest.TestCase):
    def test_current_clutch_plate_count_changes_lock_and_is_reported(self) -> None:
        car = get_car("bmw")
        solver = DiffSolver(car)
        driver = SimpleNamespace(
            throttle_classification="moderate",
            throttle_progressiveness=0.6,
            throttle_onset_rate_pct_per_s=220.0,
            trail_brake_classification="moderate",
            trail_brake_depth_mean=0.3,
        )
        measured = SimpleNamespace(
            body_slip_p95_deg=2.0,
            rear_power_slip_ratio_p95=0.05,
            rear_slip_ratio_p95=0.05,
            peak_lat_g_p99=2.0,
        )

        four_plate = solver.solve(driver=driver, measured=measured, current_clutch_plates=4)
        six_plate = solver.solve(driver=driver, measured=measured, current_clutch_plates=6)

        self.assertLess(four_plate.lock_pct_drive, six_plate.lock_pct_drive)
        self.assertIn("clutch plates=4", four_plate.preload_reasoning)
        self.assertIn("clutch plates=6", six_plate.preload_reasoning)


if __name__ == "__main__":
    unittest.main()
