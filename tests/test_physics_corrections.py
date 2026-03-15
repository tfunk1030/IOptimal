import unittest

from analyzer.adaptive_thresholds import AdaptiveThresholds
from analyzer.diagnose import Diagnosis, Problem, diagnose
from analyzer.driver_style import DriverProfile
from analyzer.extract import MeasuredState
from analyzer.setup_reader import CurrentSetup
from car_model import get_car
from solver.damper_solver import DamperSolver
from solver.heave_solver import HeaveSolver
from solver.modifiers import compute_modifiers
from solver.stint_model import compute_fuel_states
from track_model.profile import TrackProfile
from vertical_dynamics import axle_modal_rate_nmm


class PhysicsCorrectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.car = get_car("bmw")
        self.setup = CurrentSetup(source="unit")
        self.thresholds = AdaptiveThresholds()

    def test_compute_fuel_states_moves_front_weight_forward_as_fuel_burns(self) -> None:
        states = compute_fuel_states(self.car, [89.0, 50.0, 12.0])

        self.assertEqual([s.fuel_load_l for s in states], [89.0, 50.0, 12.0])
        self.assertLess(states[0].front_weight_pct, states[1].front_weight_pct)
        self.assertLess(states[1].front_weight_pct, states[2].front_weight_pct)

    def test_travel_exhaustion_modifier_targets_less_negative_perch(self) -> None:
        diagnosis = Diagnosis(
            problems=[
                Problem(
                    category="safety",
                    severity="significant",
                    symptom="Front heave travel exhausted under braking",
                    cause="travel exhausted",
                    speed_context="braking",
                    measured=92.0,
                    threshold=85.0,
                    units="%",
                    priority=0,
                )
            ]
        )
        driver = DriverProfile()
        measured = MeasuredState()

        mods = compute_modifiers(diagnosis, driver, measured)

        self.assertEqual(mods.front_heave_perch_target_mm, -11.0)
        self.assertTrue(any("less negative perch" in reason for reason in mods.reasons))

    def test_thermal_targets_accept_nominal_inner_hot_window(self) -> None:
        measured = MeasuredState(
            lap_number=6,
            front_temp_spread_lf_c=10.0,
            front_temp_spread_rf_c=11.0,
            rear_temp_spread_lr_c=8.0,
            rear_temp_spread_rr_c=8.0,
            front_carcass_mean_c=92.0,
            rear_carcass_mean_c=94.0,
        )

        diagnosis = diagnose(measured, self.setup, self.car, self.thresholds)

        thermal_problems = [p for p in diagnosis.problems if p.category == "thermal"]
        self.assertEqual(thermal_problems, [])

    def test_thermal_targets_flag_flat_spread_as_insufficient_camber(self) -> None:
        measured = MeasuredState(
            lap_number=6,
            front_temp_spread_lf_c=2.0,
            front_temp_spread_rf_c=3.0,
            rear_temp_spread_lr_c=8.0,
            rear_temp_spread_rr_c=8.0,
            front_carcass_mean_c=92.0,
            rear_carcass_mean_c=94.0,
        )

        diagnosis = diagnose(measured, self.setup, self.car, self.thresholds)

        thermal_problems = [p for p in diagnosis.problems if p.category == "thermal"]
        self.assertTrue(thermal_problems)
        self.assertTrue(
            any("insufficient negative camber" in problem.cause.lower() for problem in thermal_problems)
        )

    def test_damper_solver_uses_modal_heave_rate_for_critical_damping(self) -> None:
        track = TrackProfile(
            track_name="Unit Test",
            track_config="baseline",
            track_length_m=5000.0,
            car="bmw",
            best_lap_time_s=90.0,
            shock_vel_p95_front_mps=0.120,
            shock_vel_p95_rear_mps=0.160,
            shock_vel_p99_front_mps=0.260,
            shock_vel_p99_rear_mps=0.320,
        )
        solver = DamperSolver(self.car, track)
        front_wheel_rate_nmm = 30.0
        rear_wheel_rate_nmm = 70.0
        front_heave_nmm = 50.0
        rear_third_nmm = 540.0

        solution = solver.solve(
            front_wheel_rate_nmm=front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
            front_dynamic_rh_mm=15.0,
            rear_dynamic_rh_mm=42.0,
            fuel_load_l=89.0,
            front_heave_nmm=front_heave_nmm,
            rear_third_nmm=rear_third_nmm,
        )

        modal_front_rate = axle_modal_rate_nmm(
            front_wheel_rate_nmm,
            front_heave_nmm,
            self.car.tyre_vertical_rate_front_nmm,
        )
        expected_front_c_crit = round(
            solver._critical_damping(
                modal_front_rate,
                solver._mass_per_corner_kg(is_front=True, fuel_load_l=89.0),
            ),
            0,
        )

        self.assertEqual(solution.c_crit_front, expected_front_c_crit)

    def test_bmw_rear_third_solver_stays_inside_real_garage_range(self) -> None:
        track = TrackProfile(
            track_name="Sebring International",
            track_config="International",
            track_length_m=6000.0,
            car="bmw",
            best_lap_time_s=110.0,
            shock_vel_p95_front_mps=0.120,
            shock_vel_p95_rear_mps=0.160,
            shock_vel_p99_front_mps=0.260,
            shock_vel_p99_rear_mps=0.320,
            shock_vel_p99_front_clean_mps=0.260,
            shock_vel_p99_rear_clean_mps=0.320,
        )
        solver = HeaveSolver(self.car, track)

        solution = solver.solve(
            dynamic_front_rh_mm=15.0,
            dynamic_rear_rh_mm=42.0,
            rear_spring_nmm=170.0,
            fuel_load_l=89.0,
            front_camber_deg=-2.9,
        )

        self.assertEqual(self.car.heave_spring.rear_spring_range_nmm[1], 900.0)
        self.assertLess(solution.rear_third_nmm, 900.0)
        self.assertLessEqual(solution.rear_sigma_at_rate_mm, self.car.heave_spring.sigma_target_mm)


if __name__ == "__main__":
    unittest.main()
