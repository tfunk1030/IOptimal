import unittest


class SolveCalibrationJsonTests(unittest.TestCase):
    def test_json_includes_weak_steps_and_weak_upstream_steps(self) -> None:
        from car_model.cars import get_car
        from car_model.calibration_gate import CalibrationGate

        gate = CalibrationGate(get_car("bmw"), "sebring")
        report = gate.full_report()

        output = {
            "calibration_blocked": [],
            "calibration_instructions": "",
            "calibration_provenance": gate.provenance(),
            "calibration_weak_steps": report.weak_steps,
            "calibration_weak_upstream_steps": report.weak_upstream_steps,
            "calibration_weak_upstream_by_step": {
                str(sr.step_number): sr.weak_upstream_step
                for sr in report.step_reports
                if sr.weak_upstream and sr.weak_upstream_step is not None
            },
        }

        self.assertIn("calibration_weak_steps", output)
        self.assertIn("calibration_weak_upstream_steps", output)
        self.assertIn("calibration_weak_upstream_by_step", output)
        self.assertIsInstance(output["calibration_weak_steps"], list)
        self.assertIsInstance(output["calibration_weak_upstream_steps"], list)
        self.assertIsInstance(output["calibration_weak_upstream_by_step"], dict)

if __name__ == "__main__":
    unittest.main()
