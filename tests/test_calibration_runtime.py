import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from calibration.runtime import (
    load_runtime_garage_model,
    load_runtime_ride_height_model,
    telemetry_model_corrections,
)
from car_model.cars import get_car
from solver.predictor import predict_candidate_telemetry


class CalibrationRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model_root = (
            REPO_ROOT
            / "data"
            / "calibration"
            / "models"
            / "ferrari"
            / "sebring_international_raceway"
        )
        self.model_root.mkdir(parents=True, exist_ok=True)
        self._created = []

    def tearDown(self) -> None:
        for path in self._created:
            if path.exists():
                path.unlink()

    def _write(self, name: str, payload: dict) -> Path:
        path = self.model_root / name
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._created.append(path)
        return path

    def test_load_runtime_ride_height_model(self) -> None:
        self._write(
            "ride_height_model.json",
            {
                "car": "ferrari",
                "track": "sebring",
                "model_type": "ride_height_model",
                "parameters": {
                    "front": {
                        "intercept": 31.0,
                        "coefficients": {
                            "front_heave_spring_nmm": 0.01,
                            "front_camber_deg": 0.2,
                        },
                    },
                    "rear": {
                        "intercept": 45.0,
                        "coefficients": {
                            "rear_pushrod_offset_mm": 0.1,
                            "rear_third_spring_nmm": 0.02,
                            "rear_spring_rate_nmm": 0.03,
                            "front_heave_perch_mm": 0.04,
                            "fuel_l": -0.01,
                            "rear_spring_perch_mm": 0.05,
                        },
                    },
                },
            },
        )
        model = load_runtime_ride_height_model("ferrari", "Sebring International Raceway")
        self.assertIsNotNone(model)
        self.assertAlmostEqual(model.front_intercept, 31.0)
        self.assertAlmostEqual(model.rear_intercept, 45.0)

    def test_load_runtime_garage_model(self) -> None:
        self._write(
            "garage_model.json",
            {
                "car": "ferrari",
                "track": "sebring",
                "model_type": "garage_model",
                "parameters": {
                    "models": {
                        "static_front_rh_mm": {
                            "intercept": 30.0,
                            "coefficients": {
                                "front_pushrod_offset_mm": 0.1,
                                "front_heave_spring_nmm": 0.01,
                                "front_heave_perch_mm": -0.02,
                                "front_torsion_od_mm": 0.03,
                                "front_camber_deg": 0.04,
                                "fuel_l": -0.01,
                            },
                        },
                        "static_rear_rh_mm": {
                            "intercept": 44.0,
                            "coefficients": {
                                "rear_pushrod_offset_mm": 0.1,
                                "rear_third_spring_nmm": 0.02,
                                "rear_third_perch_mm": -0.03,
                                "rear_spring_rate_nmm": 0.04,
                                "rear_spring_perch_mm": 0.05,
                                "front_heave_perch_mm": 0.06,
                                "fuel_l": -0.01,
                            },
                        },
                    }
                },
            },
        )
        model = load_runtime_garage_model("ferrari", "Sebring International Raceway")
        self.assertIsNotNone(model)
        self.assertAlmostEqual(model.front_intercept, 30.0)
        self.assertAlmostEqual(model.rear_intercept, 44.0)

    def test_car_model_prefers_runtime_models_when_present(self) -> None:
        self._write(
            "ride_height_model.json",
            {
                "car": "ferrari",
                "track": "sebring",
                "model_type": "ride_height_model",
                "parameters": {
                    "front": {"intercept": 31.0, "coefficients": {}},
                    "rear": {"intercept": 45.0, "coefficients": {}},
                },
            },
        )
        car = get_car("ferrari")
        model = car.active_ride_height_model("Sebring International Raceway")
        self.assertAlmostEqual(model.front_intercept, 31.0)
        self.assertAlmostEqual(model.rear_intercept, 45.0)

    def test_predictor_uses_runtime_telemetry_model_biases(self) -> None:
        self._write(
            "telemetry_model.json",
            {
                "car": "ferrari",
                "track": "sebring",
                "model_type": "telemetry_model",
                "parameters": {
                    "models": {
                        "front_heave_travel_used_pct": {
                            "intercept": 1.5,
                            "coefficients": {},
                        },
                        "rear_rh_std_mm": {
                            "intercept": -0.4,
                            "coefficients": {},
                        },
                    }
                },
                "metrics": {"samples": 4},
            },
        )

        current_setup = type(
            "Setup",
            (),
            {
                "front_heave_nmm": 100.0,
                "rear_third_nmm": 500.0,
                "brake_bias_pct": 53.0,
                "front_pushrod_mm": 1.0,
                "rear_pushrod_mm": 5.0,
                "front_torsion_od_mm": 2.0,
                "rear_spring_nmm": 2.0,
                "front_camber_deg": -2.0,
                "rear_camber_deg": -1.8,
                "front_toe_mm": -0.7,
                "rear_toe_mm": 0.3,
                "rear_arb_blade": 3,
                "diff_preload_nm": 25.0,
                "tc_gain": 3,
                "tc_slip": 4,
                "front_hs_comp": 20,
                "rear_hs_comp": 32,
            },
        )()
        baseline_measured = type(
            "Measured",
            (),
            {
                "front_heave_travel_used_pct": 82.4,
                "front_rh_excursion_measured_mm": 12.1,
                "rear_rh_std_mm": 6.8,
                "pitch_range_braking_deg": 1.2,
                "front_braking_lock_ratio_p95": 0.07,
                "rear_power_slip_ratio_p95": 0.11,
                "body_slip_p95_deg": 3.5,
                "understeer_low_speed_deg": 1.4,
                "understeer_high_speed_deg": 0.7,
                "front_pressure_mean_kpa": 165.0,
                "rear_pressure_mean_kpa": 166.0,
                "telemetry_signals": {},
            },
        )()
        prediction, _confidence = predict_candidate_telemetry(
            current_setup=current_setup,
            baseline_measured=baseline_measured,
            corrections=telemetry_model_corrections("ferrari", "Sebring International Raceway"),
        )
        self.assertIsNotNone(prediction.front_heave_travel_used_pct)
        self.assertIsNotNone(prediction.rear_rh_std_mm)
        self.assertGreater(prediction.front_heave_travel_used_pct, baseline_measured.front_heave_travel_used_pct)
        self.assertLess(prediction.rear_rh_std_mm, baseline_measured.rear_rh_std_mm)


if __name__ == "__main__":
    unittest.main()
