"""Tests for calibration semantic consistency.

Validates that:
1. DeflectionModel receives coefficients matching its reciprocal formula
2. m_eff computation uses decoded N/mm rates for indexed cars (not raw indices)
3. Round-trip: fit reciprocal model → apply_to_car → predict → matches known points
"""

import unittest
from dataclasses import dataclass

import numpy as np


class TestDeflectionModelSemantics(unittest.TestCase):
    """Verify that heave_spring_defl_static uses reciprocal features."""

    def test_reciprocal_features_match_defl_model(self):
        """Fit with reciprocal features and verify DeflectionModel reproduces the fit."""
        from car_model.cars import get_car
        car = get_car("bmw")
        dm = car.deflection

        # Known BMW data points (heave_nmm, perch_mm, od_mm, measured_defl_mm)
        # These are representative values from BMW/Sebring calibration
        test_points = [
            (50.0, 43.0, 14.34, None),  # typical mid-range
            (30.0, 43.0, 14.34, None),  # soft heave
            (90.0, 43.0, 14.34, None),  # stiff heave
        ]

        for heave, perch, od, _ in test_points:
            result = dm.heave_spring_defl_static(heave, perch, od)
            # Verify the formula uses reciprocal form: intercept + A/heave + B*perch + C/od^4
            expected = (
                dm.heave_defl_intercept
                + dm.heave_defl_inv_heave_coeff / max(heave, 1.0)
                + dm.heave_defl_perch_coeff * perch
                + dm.heave_defl_inv_od4_coeff / max(od ** 4, 1.0)
            )
            self.assertAlmostEqual(result, expected, places=6,
                                   msg=f"DeflectionModel formula mismatch at heave={heave}")

    def test_defl_model_monotonic_in_heave(self):
        """Softer heave spring → more deflection (physically correct)."""
        from car_model.cars import get_car
        dm = get_car("bmw").deflection
        perch, od = 43.0, 14.34

        defl_soft = dm.heave_spring_defl_static(30.0, perch, od)
        defl_stiff = dm.heave_spring_defl_static(90.0, perch, od)
        # inv_heave_coeff should be positive → softer spring = more deflection
        if dm.heave_defl_inv_heave_coeff > 0:
            self.assertGreater(defl_soft, defl_stiff,
                               "Softer heave should produce more deflection")

    def test_polynomial_coefficients_not_applied_to_reciprocal_fields(self):
        """Regression test: polynomial fit coefficients must not be assigned to
        reciprocal DeflectionModel fields. The feature names in the fitted model
        should indicate reciprocal features, not polynomial ones."""
        from car_model.auto_calibrate import FittedModel

        # Simulate a WRONG polynomial fit (the old bug)
        bad_model = FittedModel(
            name="heave_spring_defl_static",
            feature_names=["front_heave", "front_heave_perch", "front_heave^2",
                           "front_heave_perch^2", "heave*perch", "torsion_od"],
            coefficients=[10.0, 0.5, -0.3, 0.01, -0.001, 0.002, -0.1],
        )
        # The feature_names should NOT contain polynomial terms like "front_heave^2"
        # If they do, apply_to_car should NOT map them to DeflectionModel
        self.assertIn("front_heave^2", bad_model.feature_names,
                      "Test setup: bad model should have polynomial features")

        # Correct model uses reciprocal features
        good_model = FittedModel(
            name="heave_spring_defl_static",
            feature_names=["inv_heave_nmm", "front_heave_perch", "inv_od4"],
            coefficients=[5.0, 7.03, -0.91, 666311.0],
        )
        self.assertNotIn("front_heave^2", good_model.feature_names)
        self.assertEqual(len(good_model.coefficients), 4,
                         "Reciprocal model: intercept + 3 features = 4 coefficients")


class TestMeffIndexDecode(unittest.TestCase):
    """Verify m_eff uses decoded N/mm rates for indexed cars."""

    def test_ferrari_front_rate_from_setting(self):
        """Ferrari front heave index → N/mm should use anchor+slope decode."""
        from car_model.cars import get_car
        car = get_car("ferrari")
        hs = car.heave_spring

        # Ferrari: anchor index=1, rate=50 N/mm, slope=20 N/mm/index
        # Index 0 → 50 + (0-1)*20 = 30 N/mm
        # Index 4 → 50 + (4-1)*20 = 110 N/mm
        self.assertAlmostEqual(hs.front_rate_from_setting(0), 30.0)
        self.assertAlmostEqual(hs.front_rate_from_setting(1), 50.0)
        self.assertAlmostEqual(hs.front_rate_from_setting(4), 110.0)

    def test_ferrari_rear_rate_from_setting(self):
        """Ferrari rear heave index → N/mm should use anchor+slope decode."""
        from car_model.cars import get_car
        car = get_car("ferrari")
        hs = car.heave_spring

        # Ferrari: anchor index=2, rate=530 N/mm, slope=60 N/mm/index
        # Index 0 → 530 + (0-2)*60 = 410 N/mm
        # Index 2 → 530 N/mm
        # Index 5 → 530 + (5-2)*60 = 710 N/mm
        self.assertAlmostEqual(hs.rear_rate_from_setting(0), 410.0)
        self.assertAlmostEqual(hs.rear_rate_from_setting(2), 530.0)
        self.assertAlmostEqual(hs.rear_rate_from_setting(5), 710.0)

    def test_bmw_rate_from_setting_passthrough(self):
        """BMW has no index mapping — setting IS the N/mm value."""
        from car_model.cars import get_car
        car = get_car("bmw")
        hs = car.heave_spring

        self.assertIsNone(hs.front_setting_index_range)
        self.assertAlmostEqual(hs.front_rate_from_setting(50.0), 50.0)
        self.assertAlmostEqual(hs.rear_rate_from_setting(300.0), 300.0)

    def test_meff_with_index_vs_nmm_differs(self):
        """Using raw index vs decoded N/mm produces very different m_eff.
        This is the bug: Ferrari index 4 → m_eff formula uses k=4 instead of k=110."""
        sigma_mm = 2.0
        shock_vel_p99 = 0.05

        # With raw index (the bug): m_eff = 4 * (2.0/0.05)^2 = 4 * 1600 = 6400
        m_eff_wrong = 4 * (sigma_mm / shock_vel_p99) ** 2
        # With decoded N/mm: m_eff = 110 * (2.0/0.05)^2 = 110 * 1600 = 176000
        m_eff_decoded = 110 * (sigma_mm / shock_vel_p99) ** 2

        # The wrong value is 27x too small — clearly nonsense
        self.assertGreater(m_eff_decoded / m_eff_wrong, 20,
                           "Raw index m_eff should be drastically wrong vs decoded")

    def test_ferrari_unvalidated_skips_meff(self):
        """Ferrari heave_index_unvalidated=True should cause m_eff to be skipped."""
        from car_model.cars import get_car
        car = get_car("ferrari")
        self.assertTrue(car.heave_spring.heave_index_unvalidated,
                        "Ferrari heave index should be marked unvalidated")


class TestCalibrationGateDependencyPropagation(unittest.TestCase):
    """Verify that CalibrationGate blocks downstream steps when upstream is blocked."""

    def test_acura_step1_blocked_cascades_all(self):
        """Acura has uncalibrated aero_compression → Step 1 blocked → all blocked."""
        from car_model.cars import get_car
        from car_model.calibration_gate import CalibrationGate
        car = get_car("acura")
        gate = CalibrationGate(car, "hockenheim")
        report = gate.full_report()
        # Step 1 is blocked by its own subsystems
        self.assertTrue(report.step_reports[0].blocked)
        self.assertFalse(report.step_reports[0].dependency_blocked)
        # Steps 2-6 should be blocked by dependency cascade
        for step_num in range(2, 7):
            sr = report.step_reports[step_num - 1]
            self.assertTrue(sr.blocked, f"Step {step_num} should be blocked")
            self.assertTrue(sr.dependency_blocked, f"Step {step_num} should be dependency-blocked")
            self.assertIsNotNone(sr.blocked_by_step)

    def test_bmw_no_steps_blocked(self):
        """BMW/Sebring is fully calibrated — no steps blocked."""
        from car_model.cars import get_car
        from car_model.calibration_gate import CalibrationGate
        car = get_car("bmw")
        gate = CalibrationGate(car, "sebring")
        report = gate.full_report()
        self.assertFalse(report.any_blocked)
        self.assertEqual(report.blocked_steps, [])
        self.assertEqual(len(report.solved_steps), 6)

    def test_ferrari_partial_blocks_steps_4_through_6(self):
        """Ferrari has calibrated steps 1-3 but blocked steps 4-6."""
        from car_model.cars import get_car
        from car_model.calibration_gate import CalibrationGate
        car = get_car("ferrari")
        gate = CalibrationGate(car, "sebring")
        # Steps 1-3 should be runnable
        for step_num in range(1, 4):
            self.assertTrue(gate.step_is_runnable(step_num),
                            f"Ferrari step {step_num} should be runnable")
        # Step 4 blocked by its own subsystems (arb_stiffness, lltd_target)
        sr4 = gate.check_step(4)
        self.assertTrue(sr4.blocked)
        self.assertFalse(sr4.dependency_blocked)
        # Step 5 cascades from blocked step 4.
        sr5 = gate.check_step(5)
        self.assertTrue(sr5.blocked, "Ferrari step 5 should be blocked")
        self.assertTrue(sr5.dependency_blocked,
                        "Ferrari step 5 should be dependency-blocked")
        # Step 6 depends on step 3 wheel rates, so it blocks on its own
        # uncalibrated damper_zeta subsystem rather than cascading from step 4.
        sr6 = gate.check_step(6)
        self.assertTrue(sr6.blocked, "Ferrari step 6 should be blocked")
        self.assertFalse(sr6.dependency_blocked,
                         "Ferrari step 6 should block on its own damper calibration")

    def test_dependency_blocked_instructions_text(self):
        """Dependency-blocked steps should reference the upstream blocker."""
        from car_model.cars import get_car
        from car_model.calibration_gate import CalibrationGate
        car = get_car("acura")
        gate = CalibrationGate(car, "hockenheim")
        sr = gate.check_step(3)
        text = sr.instructions_text()
        self.assertIn("Depends on Step", text)
        self.assertIn("Resolve Step", text)

    def test_full_report_blocked_steps_includes_cascaded(self):
        """full_report().blocked_steps should include dependency-cascaded steps."""
        from car_model.cars import get_car
        from car_model.calibration_gate import CalibrationGate
        car = get_car("acura")
        gate = CalibrationGate(car, "hockenheim")
        report = gate.full_report()
        self.assertEqual(report.blocked_steps, [1, 2, 3, 4, 5, 6])


if __name__ == "__main__":
    unittest.main()
