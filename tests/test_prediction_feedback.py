import unittest
from datetime import datetime, timezone

from learner.empirical_models import EmpiricalModelSet, fit_prediction_errors


class PredictionFeedbackTests(unittest.TestCase):
    def test_fit_prediction_errors_tracks_new_predictor_metrics(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        observations = [
            {
                "timestamp": now,
                "solver_predictions": {
                    "front_excursion_mm": 10.0,
                    "front_lock_p95": 0.060,
                    "rear_power_slip_p95": 0.080,
                },
                "telemetry": {
                    "front_rh_excursion_measured_mm": 11.0,
                    "front_braking_lock_ratio_p95": 0.070,
                    "rear_power_slip_ratio_p95": 0.090,
                },
            },
            {
                "timestamp": now,
                "solver_predictions": {
                    "front_excursion_mm": 9.5,
                    "front_lock_p95": 0.055,
                    "rear_power_slip_p95": 0.075,
                },
                "telemetry": {
                    "front_rh_excursion_measured_mm": 10.0,
                    "front_braking_lock_ratio_p95": 0.060,
                    "rear_power_slip_ratio_p95": 0.080,
                },
            },
            {
                "timestamp": now,
                "solver_predictions": {
                    "front_excursion_mm": 10.5,
                    "front_lock_p95": 0.065,
                    "rear_power_slip_p95": 0.082,
                },
                "telemetry": {
                    "front_rh_excursion_measured_mm": 10.9,
                    "front_braking_lock_ratio_p95": 0.071,
                    "rear_power_slip_ratio_p95": 0.087,
                },
            },
        ]
        models = EmpiricalModelSet(car="bmw", track="sebring")

        fit_prediction_errors(observations, models)

        self.assertIn("prediction_correction_front_excursion_mm", models.corrections)
        self.assertIn("prediction_correction_front_lock_p95", models.corrections)
        self.assertIn("prediction_correction_rear_power_slip_p95", models.corrections)


if __name__ == "__main__":
    unittest.main()
