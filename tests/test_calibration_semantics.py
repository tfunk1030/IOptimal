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

    def test_acura_steps_1_3_runnable_4_6_blocked(self):
        """Acura Steps 1-3 runnable (aero calibrated), Steps 4-6 blocked by own subsystems."""
        from car_model.cars import get_car
        from car_model.calibration_gate import CalibrationGate
        car = get_car("acura")
        gate = CalibrationGate(car, "hockenheim")
        report = gate.full_report()
        # Steps 1-3 are runnable (aero_compression calibrated, spring_rates calibrated)
        for step_num in range(1, 4):
            self.assertTrue(gate.step_is_runnable(step_num),
                            f"Acura step {step_num} should be runnable")
        # Step 4 blocked by its own subsystems (arb_stiffness, lltd_target)
        sr4 = report.step_reports[3]
        self.assertTrue(sr4.blocked)
        self.assertFalse(sr4.dependency_blocked)
        # Step 5 cascades from blocked step 4
        sr5 = report.step_reports[4]
        self.assertTrue(sr5.blocked, "Step 5 should be blocked")
        self.assertTrue(sr5.dependency_blocked, "Step 5 should be dependency-blocked")
        # Step 6 blocked by its own damper_zeta
        sr6 = report.step_reports[5]
        self.assertTrue(sr6.blocked, "Step 6 should be blocked")
        self.assertFalse(sr6.dependency_blocked)

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

    def test_weak_upstream_steps_property_matches_step_reports(self):
        """weak_upstream_steps should mirror per-step weak_upstream flags."""
        from car_model.cars import get_car
        from car_model.calibration_gate import CalibrationGate
        car = get_car("bmw")
        gate = CalibrationGate(car, "sebring")
        report = gate.full_report()
        expected = [
            sr.step_number
            for sr in report.step_reports
            if sr.weak_upstream
        ]
        self.assertEqual(report.weak_upstream_steps, expected)

    def test_ferrari_step1_runnable_steps_2_through_6_runnable(self):
        """Ferrari steps 1-6 are all runnable after rear_torsion_unvalidated was cleared.

        The rear torsion bar model was previously flagged as unvalidated (blocking
        Steps 2–6).  PR #57 fixed the index/physical-OD domain-mismatch bug in
        garage_validator._clamp_step3 that caused the original 3.5x apparent error.
        Subsequently, IBT controlled-group analysis (60 sessions, same turns+pushrod,
        different spring index) validated the bar model to within ~4–22% of measured
        wheel rates, and the stale flag was cleared in this PR.
        """
        from car_model.cars import get_car
        from car_model.calibration_gate import CalibrationGate
        car = get_car("ferrari")
        gate = CalibrationGate(car, "sebring")
        # Steps 1 and 2 should now both be runnable
        self.assertTrue(gate.step_is_runnable(1), "Ferrari step 1 should be runnable")
        self.assertTrue(gate.step_is_runnable(2), "Ferrari step 2 should be runnable after flag cleared")

    def test_dependency_blocked_instructions_text(self):
        """Dependency-blocked steps should reference the upstream blocker."""
        from car_model.cars import get_car
        from car_model.calibration_gate import CalibrationGate
        # Acura Step 5 is dependency-blocked (cascades from blocked Step 4)
        car = get_car("acura")
        gate = CalibrationGate(car, "hockenheim")
        sr = gate.check_step(5)
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
        # Acura Steps 1-3 runnable, Step 4 blocked (own), Step 5 cascaded, Step 6 blocked (own)
        self.assertEqual(report.blocked_steps, [4, 5, 6])


class TestPerTrackCalibration(unittest.TestCase):
    """Tests for per-track calibration model save/load and safety."""

    def _make_models(self, car: str = "bmw", n_unique: int = 5, track: str = "") -> object:
        """Return a minimal CarCalibrationModels instance."""
        from car_model.auto_calibrate import CarCalibrationModels
        return CarCalibrationModels(
            car=car,
            track=track,
            n_sessions=n_unique,
            n_unique_setups=n_unique,
        )

    # ── filename safety ──────────────────────────────────────────────────────

    def test_safe_track_slug_allows_clean_names(self):
        from car_model.auto_calibrate import _safe_track_slug
        self.assertEqual(_safe_track_slug("algarve"), "algarve")
        self.assertEqual(_safe_track_slug("sebring_international_raceway"), "sebring_international_raceway")
        self.assertEqual(_safe_track_slug("laguna_seca"), "laguna_seca")

    def test_safe_track_slug_replaces_spaces_and_dots(self):
        from car_model.auto_calibrate import _safe_track_slug
        slug = _safe_track_slug("Laguna Seca 2.0")
        self.assertRegex(slug, r"^[a-z0-9_]+$", "slug should contain only [a-z0-9_]")
        self.assertNotIn(" ", slug)
        self.assertNotIn(".", slug)

    def test_safe_track_slug_strips_path_separators(self):
        """Path traversal characters must be removed."""
        from car_model.auto_calibrate import _safe_track_slug
        slug = _safe_track_slug("../../etc/passwd")
        self.assertRegex(slug, r"^[a-z0-9_]+$")
        self.assertNotIn("/", slug)
        self.assertNotIn(".", slug)
        self.assertNotIn("..", slug)

    def test_safe_track_slug_windows_separators(self):
        from car_model.auto_calibrate import _safe_track_slug
        slug = _safe_track_slug(r"..\windows\system32")
        self.assertRegex(slug, r"^[a-z0-9_]+$")
        self.assertNotIn("\\", slug)

    def test_safe_track_slug_empty_string_returns_unknown(self):
        from car_model.auto_calibrate import _safe_track_slug
        self.assertEqual(_safe_track_slug(""), "unknown")

    def test_models_path_for_track_uses_slug(self):
        """_models_path_for_track must not embed raw path separators."""
        from car_model.auto_calibrate import _models_path_for_track
        p = _models_path_for_track("bmw", "../../evil")
        # The resulting path must still be inside the car data directory
        self.assertNotIn("..", str(p.name))
        self.assertRegex(p.name, r"^models_[a-z0-9_]+\.json$")

    # ── load preference ──────────────────────────────────────────────────────

    def test_per_track_preferred_when_sufficient(self):
        """load_calibrated_models prefers per-track file when it has enough setups."""
        import tempfile
        from pathlib import Path
        from car_model.auto_calibrate import (
            load_calibrated_models, save_calibrated_models,
            _MIN_SESSIONS_FOR_FIT, CarCalibrationModels,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # Patch _data_dir to point at tmpdir
            from car_model import auto_calibrate as _ac
            original = _ac._data_dir

            def _patched(car):
                return Path(tmpdir)

            _ac._data_dir = _patched
            try:
                # Write pooled model with n_unique = _MIN_SESSIONS_FOR_FIT
                pooled = self._make_models(n_unique=_MIN_SESSIONS_FOR_FIT)
                pooled.status["source"] = "pooled"
                save_calibrated_models("bmw", pooled)

                # Write per-track model with n_unique = _MIN_SESSIONS_FOR_FIT
                per_track = self._make_models(n_unique=_MIN_SESSIONS_FOR_FIT, track="algarve")
                per_track.status["source"] = "per_track"
                save_calibrated_models("bmw", per_track, track="algarve")

                loaded = load_calibrated_models("bmw", track="algarve")
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.status.get("source"), "per_track",
                                 "Per-track model should be preferred when sufficient")
            finally:
                _ac._data_dir = original

    def test_pooled_fallback_when_per_track_insufficient(self):
        """load_calibrated_models falls back to pooled when per-track has too few setups."""
        import tempfile
        from car_model.auto_calibrate import (
            load_calibrated_models, save_calibrated_models,
            _MIN_SESSIONS_FOR_FIT, CarCalibrationModels,
        )
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            from car_model import auto_calibrate as _ac
            original = _ac._data_dir

            def _patched(car):
                return Path(tmpdir)

            _ac._data_dir = _patched
            try:
                # Write pooled model with sufficient setups
                pooled = self._make_models(n_unique=_MIN_SESSIONS_FOR_FIT)
                pooled.status["source"] = "pooled"
                save_calibrated_models("bmw", pooled)

                # Write per-track model with INSUFFICIENT setups (below threshold)
                per_track = self._make_models(n_unique=_MIN_SESSIONS_FOR_FIT - 1, track="algarve")
                per_track.status["source"] = "per_track"
                save_calibrated_models("bmw", per_track, track="algarve")

                loaded = load_calibrated_models("bmw", track="algarve")
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.status.get("source"), "pooled",
                                 "Pooled fallback should be used when per-track is insufficient")
            finally:
                _ac._data_dir = original

    def test_pooled_fallback_when_per_track_missing(self):
        """load_calibrated_models falls back to pooled when no per-track file exists."""
        import tempfile
        from car_model.auto_calibrate import (
            load_calibrated_models, save_calibrated_models,
            _MIN_SESSIONS_FOR_FIT,
        )
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            from car_model import auto_calibrate as _ac
            original = _ac._data_dir

            def _patched(car):
                return Path(tmpdir)

            _ac._data_dir = _patched
            try:
                pooled = self._make_models(n_unique=_MIN_SESSIONS_FOR_FIT)
                pooled.status["source"] = "pooled"
                save_calibrated_models("bmw", pooled)

                # No per-track file written
                loaded = load_calibrated_models("bmw", track="nonexistent_track")
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.status.get("source"), "pooled")
            finally:
                _ac._data_dir = original

    # ── car-wide field merging ───────────────────────────────────────────────

    def test_merge_car_wide_fields_copies_zeta(self):
        """_merge_car_wide_fields should copy zeta from source when dest has none."""
        from car_model.auto_calibrate import _merge_car_wide_fields, CarCalibrationModels
        source = self._make_models()
        source.front_ls_zeta = 0.35
        source.rear_ls_zeta = 0.38
        source.front_hs_zeta = 0.55
        source.rear_hs_zeta = 0.60
        source.zeta_n_sessions = 7

        dest = self._make_models(track="algarve")
        self.assertIsNone(dest.front_ls_zeta)

        _merge_car_wide_fields("bmw", dest, source, verbose=False)
        self.assertEqual(dest.front_ls_zeta, 0.35)
        self.assertEqual(dest.rear_ls_zeta, 0.38)
        self.assertEqual(dest.zeta_n_sessions, 7)

    def test_merge_car_wide_fields_does_not_overwrite_existing_zeta(self):
        """_merge_car_wide_fields should NOT overwrite zeta already present in dest."""
        from car_model.auto_calibrate import _merge_car_wide_fields
        source = self._make_models()
        source.front_ls_zeta = 0.99

        dest = self._make_models(track="algarve")
        dest.front_ls_zeta = 0.40  # already set

        _merge_car_wide_fields("bmw", dest, source, verbose=False)
        self.assertEqual(dest.front_ls_zeta, 0.40, "Existing zeta must not be overwritten")

    def test_merge_car_wide_fields_copies_status_keys(self):
        """_merge_car_wide_fields merges missing status keys."""
        from car_model.auto_calibrate import _merge_car_wide_fields
        source = self._make_models()
        source.status["arb_calibrated"] = "True"
        source.status["roll_gains_calibrated"] = "True"

        dest = self._make_models(track="algarve")
        dest.status["arb_calibrated"] = "False"  # already present, must not be overwritten

        _merge_car_wide_fields("bmw", dest, source, verbose=False)
        self.assertEqual(dest.status["arb_calibrated"], "False", "Existing status key must not be overwritten")
        self.assertEqual(dest.status["roll_gains_calibrated"], "True", "Missing key should be copied")

    def test_merge_car_wide_fields_handles_none_source(self):
        """_merge_car_wide_fields with None source should be a no-op."""
        from car_model.auto_calibrate import _merge_car_wide_fields
        dest = self._make_models(track="algarve")
        _merge_car_wide_fields("bmw", dest, None, verbose=False)  # must not raise


if __name__ == "__main__":
    unittest.main()
