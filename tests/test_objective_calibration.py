import unittest

from car_model.cars import get_car
from solver.objective import ObjectiveFunction, PhysicsResult
from validation.objective_calibration import (
    ScoredObservation,
    build_calibration_report,
    search_weight_profiles,
)


class ObjectiveCalibrationTests(unittest.TestCase):
    def test_bmw_sebring_track_aware_single_lap_safe_uses_runtime_guard(self) -> None:
        track_aware = ObjectiveFunction(
            get_car("bmw"),
            {"track_name": "Sebring International Raceway"},
            scenario_profile="single_lap_safe",
        )
        trackless = ObjectiveFunction(
            get_car("bmw"),
            None,
            scenario_profile="single_lap_safe",
        )

        guarded = track_aware._new_breakdown()
        defaulted = trackless._new_breakdown()

        self.assertEqual(guarded.w_lap_gain, 0.25)
        self.assertEqual(guarded.w_envelope, 0.40)
        self.assertEqual(defaulted.w_lap_gain, 1.0)
        self.assertEqual(defaulted.w_envelope, 0.70)

    def test_heave_realism_moves_to_envelope_not_raw_lap_gain(self) -> None:
        objective = ObjectiveFunction(
            get_car("bmw"),
            {"track_name": "Sebring International Raceway"},
            scenario_profile="single_lap_safe",
        )
        physics = PhysicsResult()
        nominal_params = {"front_heave_spring_nmm": 50.0, "rear_third_spring_nmm": 440.0}
        extreme_params = {"front_heave_spring_nmm": 300.0, "rear_third_spring_nmm": 440.0}

        nominal_gain = objective._estimate_lap_gain(nominal_params, physics)
        extreme_gain = objective._estimate_lap_gain(extreme_params, physics)
        nominal_penalty = objective._compute_envelope_penalty(nominal_params, physics, [])
        extreme_penalty = objective._compute_envelope_penalty(extreme_params, physics, [])

        self.assertEqual(nominal_gain, extreme_gain)
        self.assertGreater(extreme_penalty.total_ms, nominal_penalty.total_ms)

    def test_bmw_sebring_track_aware_single_lap_safe_halves_camber_penalty(self) -> None:
        track_aware = ObjectiveFunction(
            get_car("bmw"),
            {"track_name": "Sebring International Raceway"},
            scenario_profile="single_lap_safe",
        )
        trackless = ObjectiveFunction(
            get_car("bmw"),
            None,
            scenario_profile="single_lap_safe",
        )

        guarded = track_aware._camber_lap_penalty_ms(-2.0, -1.0)
        defaulted = trackless._camber_lap_penalty_ms(-2.0, -1.0)

        self.assertEqual(guarded, defaulted * 0.5)

    def test_build_calibration_report_includes_track_modes(self) -> None:
        report = build_calibration_report(include_search=False)
        track_aware = report["modes"]["track_aware"]

        self.assertGreaterEqual(report["bmw_sebring_samples"], 70)
        self.assertEqual(report["bmw_sebring_samples"], track_aware["samples"])
        self.assertEqual(track_aware["samples"], track_aware["non_vetoed_samples"] + track_aware["vetoed_samples"])
        self.assertIn("track_aware", report["modes"])
        self.assertIn("trackless", report["modes"])
        self.assertIn("ablations", track_aware)
        self.assertIn("term_correlations", report["modes"]["trackless"])
        self.assertIn("lap_gain_component_correlations", track_aware)
        self.assertIn("lap_gain_component_ablations", track_aware)
        self.assertIn("holdout_validation", track_aware)
        self.assertLess(track_aware["score_correlation"]["spearman_r"], 0.0)
        self.assertFalse(report["recommended_runtime_profile"]["auto_apply"])

    def test_search_weight_profiles_ignores_vetoed_rows(self) -> None:
        scored = [
            ScoredObservation(
                filename="a.json",
                lap_time_s=100.0,
                vetoed=False,
                total_score_ms=-10.0,
                lap_gain_ms=-10.0,
                platform_risk_ms=1.0,
                driver_mismatch_ms=0.0,
                telemetry_uncertainty_ms=0.0,
                envelope_penalty_ms=0.0,
                staleness_penalty_ms=0.0,
                empirical_penalty_ms=0.0,
                weighted_lap_gain_ms=-10.0,
                weighted_platform_ms=-1.0,
                weighted_driver_ms=0.0,
                weighted_uncertainty_ms=0.0,
                weighted_envelope_ms=0.0,
                weighted_staleness_ms=0.0,
                weighted_empirical_ms=0.0,
            ),
            ScoredObservation(
                filename="b.json",
                lap_time_s=101.0,
                vetoed=False,
                total_score_ms=-20.0,
                lap_gain_ms=-20.0,
                platform_risk_ms=2.0,
                driver_mismatch_ms=0.0,
                telemetry_uncertainty_ms=0.0,
                envelope_penalty_ms=0.0,
                staleness_penalty_ms=0.0,
                empirical_penalty_ms=0.0,
                weighted_lap_gain_ms=-20.0,
                weighted_platform_ms=-2.0,
                weighted_driver_ms=0.0,
                weighted_uncertainty_ms=0.0,
                weighted_envelope_ms=0.0,
                weighted_staleness_ms=0.0,
                weighted_empirical_ms=0.0,
            ),
            ScoredObservation(
                filename="c.json",
                lap_time_s=105.0,
                vetoed=True,
                total_score_ms=-1e9,
                lap_gain_ms=999.0,
                platform_risk_ms=999.0,
                driver_mismatch_ms=0.0,
                telemetry_uncertainty_ms=0.0,
                envelope_penalty_ms=0.0,
                staleness_penalty_ms=0.0,
                empirical_penalty_ms=0.0,
                weighted_lap_gain_ms=999.0,
                weighted_platform_ms=-999.0,
                weighted_driver_ms=0.0,
                weighted_uncertainty_ms=0.0,
                weighted_envelope_ms=0.0,
                weighted_staleness_ms=0.0,
                weighted_empirical_ms=0.0,
            ),
        ]

        result = search_weight_profiles(scored)

        self.assertIn("best_weights", result)
        self.assertLessEqual(result["best_spearman_r"], result["current_spearman_r"])


if __name__ == "__main__":
    unittest.main()
