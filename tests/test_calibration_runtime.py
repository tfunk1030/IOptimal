import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from calibration.runtime import load_runtime_garage_model, load_runtime_ride_height_model
from car_model.cars import get_car


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


if __name__ == "__main__":
    unittest.main()
