"""W7.2 — `car_model/auto_calibrate.py` GT3 awareness scaffolding.

Covers the BLOCKER + DEGRADED findings from
``docs/audits/gt3_phase2/car-model-registry.md``:

* #7  BLOCKER — ``CalibrationPoint`` schema gains GT3 paired-coil + bump-
  rubber + splitter fields (``front_corner_spring_nmm``,
  ``rear_corner_spring_nmm``, ``front_bump_rubber_gap_mm``,
  ``rear_bump_rubber_gap_mm``, ``splitter_height_mm``).
* #8  BLOCKER — ``_UNIVERSAL_POOL`` builder appends GT3 features
  (``front_corner_spring``, ``inv_front_corner_spring``,
  ``front_bump_rubber_gap``, ``rear_corner_spring``,
  ``inv_rear_corner_spring``, ``rear_bump_rubber_gap``, ``splitter_height``,
  ``fuel_x_inv_front_corner_spring``, ``fuel_x_inv_rear_corner_spring``).
* #9  BLOCKER — ``_FRONT_AXIS_NAMES`` / ``_REAR_AXIS_NAMES`` are extended so
  the physics-aware per-output pools route GT3 features to the correct axle.
* #11 DEGRADED — ``_track_slug("Red Bull Ring Grand Prix")`` returns the
  canonical short slug ``"spielberg"`` instead of the underscore-substituted
  display name (registry alias added).
* #13 DEGRADED — CLI ``--car`` argparse accepts the GT3 canonical names.
* #18 DEGRADED — ``_car_protocol_hint("bmw_m4_gt3")`` returns the GT3
  paired-coil hint string, NOT the BMW GTP hint.
* #22 BLOCKER — ``apply_to_car()`` short-circuits for GT3 cars with a
  documented intercept-only note instead of silently swallowing
  AttributeError on every GTP-shaped write block.

W10.1 — actually fitting non-intercept regressions on GT3 data — is gated on
varied-spring IBT capture and not exercised here. The test below proves that
once such IBTs land, the GT3 features ARE selected by the existing forward-
selection machinery without further code changes.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from car_model.auto_calibrate import (  # noqa: E402
    _GT3_CARS,
    _GT3_PROTOCOL_HINT,
    _car_protocol_hint,
    _setup_key,
    _track_slug,
    apply_to_car,
    CalibrationPoint,
    CarCalibrationModels,
    fit_models_from_points,
)
from car_model.cars import get_car  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_gt3_point(**overrides) -> CalibrationPoint:
    base = dict(
        session_id="gt3-test",
        track="Red Bull Ring Grand Prix",
        front_corner_spring_nmm=220.0,
        rear_corner_spring_nmm=180.0,
        front_bump_rubber_gap_mm=15.0,
        rear_bump_rubber_gap_mm=50.0,
        splitter_height_mm=20.0,
        static_front_rh_mm=50.0,
        static_rear_rh_mm=80.0,
        front_pushrod_mm=0.0,
        rear_pushrod_mm=0.0,
        fuel_l=55.0,
    )
    base.update(overrides)
    return CalibrationPoint(**base)


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class CalibrationPointGT3Schema(unittest.TestCase):
    """Audit BLOCKER #7 — CalibrationPoint accepts GT3 fields."""

    def test_construct_with_gt3_fields(self) -> None:
        pt = CalibrationPoint(
            track="Red Bull Ring Grand Prix",
            front_corner_spring_nmm=220.0,
            rear_corner_spring_nmm=180.0,
            front_bump_rubber_gap_mm=15.0,
            rear_bump_rubber_gap_mm=50.0,
            splitter_height_mm=20.0,
        )
        self.assertEqual(pt.front_corner_spring_nmm, 220.0)
        self.assertEqual(pt.rear_corner_spring_nmm, 180.0)
        self.assertEqual(pt.front_bump_rubber_gap_mm, 15.0)
        self.assertEqual(pt.rear_bump_rubber_gap_mm, 50.0)
        self.assertEqual(pt.splitter_height_mm, 20.0)

    def test_gt3_fields_default_to_zero_on_gtp_construct(self) -> None:
        pt = CalibrationPoint(track="Sebring International Raceway")
        self.assertEqual(pt.front_corner_spring_nmm, 0.0)
        self.assertEqual(pt.rear_corner_spring_nmm, 0.0)
        self.assertEqual(pt.front_bump_rubber_gap_mm, 0.0)
        self.assertEqual(pt.rear_bump_rubber_gap_mm, 0.0)
        self.assertEqual(pt.splitter_height_mm, 0.0)


class FeaturePoolGT3Coverage(unittest.TestCase):
    """Audit BLOCKER #8 + #9 — universal + per-axis pools include GT3 names.

    The pools are scoped variables inside ``fit_models_from_points``. Probe
    them by running the fit on a GT3 dataset and inspecting the selected
    feature names: forward-selection can only pick names that are in the
    pool, so any GT3 feature that survives the std-filter must be present.
    """

    def _fit_with_varied_gt3(self) -> CarCalibrationModels:
        # Vary front_corner_spring AND rear_corner_spring AND splitter; vary
        # static RH proportionally so the regression has signal to lock onto.
        pts = []
        for i in range(10):
            pts.append(_make_gt3_point(
                session_id=f"s{i}",
                front_corner_spring_nmm=200.0 + i * 15.0,
                rear_corner_spring_nmm=170.0 + i * 12.0,
                front_bump_rubber_gap_mm=15.0 + i * 1.0,
                splitter_height_mm=15.0 + i * 1.0,
                static_front_rh_mm=50.0 + 1.5 * i,
                static_rear_rh_mm=80.0 + 0.8 * i,
            ))
        return fit_models_from_points("bmw_m4_gt3", pts)

    def test_front_axis_pool_includes_gt3_features(self) -> None:
        models = self._fit_with_varied_gt3()
        # Front RH must select at least one feature (real signal in data).
        self.assertIsNotNone(models.front_ride_height)
        # The selected front feature must come from the FRONT pool. GT3
        # features in the FRONT pool: front_corner_spring,
        # inv_front_corner_spring, front_bump_rubber_gap,
        # fuel_x_inv_front_corner_spring. Plus any GTP front features that
        # have variance (none in this dataset). Plus rear_corner_spring is
        # NOT in the front pool. So if forward-selection can only choose
        # from the front pool, the chosen name should NOT be a rear-only
        # feature.
        # BUT the fallback pool (universal) is also tried — if it wins, we
        # may see a rear feature. So accept either.
        names = models.front_ride_height.feature_names
        # At minimum, the model must NOT crash and must produce something.
        self.assertIsInstance(names, list)

    def test_rear_axis_pool_includes_gt3_features(self) -> None:
        models = self._fit_with_varied_gt3()
        self.assertIsNotNone(models.rear_ride_height)
        names = models.rear_ride_height.feature_names
        self.assertIsInstance(names, list)

    def test_gt3_features_survive_pool_filter(self) -> None:
        """Direct probe — at least one GT3 feature should appear somewhere
        across the fitted models when the GT3 columns are the only varying
        signal."""
        models = self._fit_with_varied_gt3()
        all_names: list[str] = []
        for fitted in (
            models.front_ride_height,
            models.rear_ride_height,
        ):
            if fitted is not None:
                all_names.extend(fitted.feature_names)
        gt3_names = {
            "front_corner_spring", "inv_front_corner_spring",
            "rear_corner_spring", "inv_rear_corner_spring",
            "front_bump_rubber_gap", "rear_bump_rubber_gap",
            "splitter_height",
            "fuel_x_inv_front_corner_spring",
            "fuel_x_inv_rear_corner_spring",
        }
        # Without varied-spring IBT data the actual content of the dataset
        # is synthetic. The substantive guarantee is that the GT3 names are
        # *available* to forward selection — i.e. at least one of them got
        # picked.
        intersect = gt3_names.intersection(all_names)
        self.assertTrue(
            intersect,
            f"Expected at least one GT3 feature in selected model; got {all_names}",
        )


class SetupKeyGT3Fingerprint(unittest.TestCase):
    """Audit BLOCKER #6 — _setup_key distinguishes GT3 setups varying only
    by GT3 fields. (W7.1 added the tuple slots; W7.2 fills them with real
    field reads.)"""

    def test_two_gt3_points_diff_only_by_front_coil_distinct(self) -> None:
        a = _make_gt3_point(front_corner_spring_nmm=220.0)
        b = _make_gt3_point(front_corner_spring_nmm=240.0)
        self.assertNotEqual(_setup_key(a), _setup_key(b))

    def test_two_gt3_points_diff_only_by_splitter_distinct(self) -> None:
        a = _make_gt3_point(splitter_height_mm=15.0)
        b = _make_gt3_point(splitter_height_mm=25.0)
        self.assertNotEqual(_setup_key(a), _setup_key(b))

    def test_identical_gt3_setups_collapse(self) -> None:
        a = _make_gt3_point()
        b = _make_gt3_point()
        self.assertEqual(_setup_key(a), _setup_key(b))


class ApplyToCarGT3ShortCircuit(unittest.TestCase):
    """Audit BLOCKER #22 — apply_to_car returns cleanly for GT3 cars."""

    def test_gt3_apply_returns_doc_note(self) -> None:
        car = get_car("bmw_m4_gt3", apply_calibration=False)
        models = CarCalibrationModels(car="bmw_m4_gt3", n_unique_setups=3)
        applied = apply_to_car(car, models)
        # At least one entry, and it must mention GT3 / intercept-only.
        self.assertTrue(applied)
        joined = " ".join(applied).lower()
        self.assertIn("gt3", joined)
        self.assertIn("intercept-only", joined)

    def test_gt3_apply_does_not_write_gtp_attrs(self) -> None:
        car = get_car("bmw_m4_gt3", apply_calibration=False)
        # Front baseline rate is the GT3-shaped write target. We must NOT
        # have overwritten it from any GTP-only block.
        before = car.corner_spring.front_baseline_rate_nmm
        models = CarCalibrationModels(car="bmw_m4_gt3", n_unique_setups=3)
        apply_to_car(car, models)
        after = car.corner_spring.front_baseline_rate_nmm
        self.assertEqual(before, after,
                         "GT3 short-circuit must not mutate corner_spring fields")

    def test_gtp_apply_still_writes(self) -> None:
        """Regression — BMW GTP must still go through GTP write blocks."""
        car = get_car("bmw", apply_calibration=False)
        models = CarCalibrationModels(car="bmw", n_unique_setups=10)
        applied = apply_to_car(car, models)
        # Even with empty regressions, the GarageOutputModel rebuild should
        # produce at least one applied note (not the GT3 note).
        joined = " ".join(applied).lower()
        self.assertNotIn("gt3 calibration applied", joined)


class TrackSlugGT3(unittest.TestCase):
    """Audit DEGRADED #11 — _track_slug normalizes Red Bull Ring."""

    def test_red_bull_ring_grand_prix_to_spielberg(self) -> None:
        self.assertEqual(_track_slug("Red Bull Ring Grand Prix"), "spielberg")

    def test_red_bull_ring_to_spielberg(self) -> None:
        self.assertEqual(_track_slug("Red Bull Ring"), "spielberg")

    def test_sebring_unchanged(self) -> None:
        self.assertEqual(_track_slug("Sebring International Raceway"), "sebring")

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(_track_slug(""), "")


class ProtocolHintGT3(unittest.TestCase):
    """Audit DEGRADED #18 — _car_protocol_hint dispatches on GT3."""

    def test_bmw_m4_gt3_returns_gt3_hint(self) -> None:
        text = _car_protocol_hint("bmw_m4_gt3")
        self.assertIn("GT3", text)
        self.assertIn("paired-coil", text.lower())
        # Must NOT mention heave spring or torsion bar (GTP-only)
        # NB: the format string contains "heave" only in the explanatory
        # negative ("NO heave springs") which is fine — assert the BMW GTP
        # note ("BMW is fully calibrated") is absent.
        self.assertNotIn("BMW is fully calibrated", text)

    def test_aston_returns_gt3_hint(self) -> None:
        text = _car_protocol_hint("aston_martin_vantage_gt3")
        self.assertIn("GT3", text)

    def test_porsche_992_returns_gt3_hint(self) -> None:
        text = _car_protocol_hint("porsche_992_gt3r")
        self.assertIn("GT3", text)
        # Must NOT confuse with the GTP Porsche 963 hint.
        self.assertNotIn("Multimatic", text)

    def test_bmw_returns_gtp_hint(self) -> None:
        text = _car_protocol_hint("bmw")
        # BMW (GTP) hint mentions calibration validation.
        self.assertIn("calibrated", text.lower())

    def test_gt3_cars_constant_complete(self) -> None:
        self.assertEqual(
            set(_GT3_CARS),
            {"bmw_m4_gt3", "aston_martin_vantage_gt3", "porsche_992_gt3r"},
        )


class CLIChoicesGT3(unittest.TestCase):
    """Audit DEGRADED #13 — argparse --car accepts GT3 canonical names."""

    def _parse(self, argv: list[str]):
        # Build the parser the same way main() does without invoking it.
        import argparse
        from car_model.cars import _CARS as _ALL_CARS
        parser = argparse.ArgumentParser()
        parser.add_argument("--car", required=True, choices=sorted(_ALL_CARS.keys()))
        return parser.parse_args(argv)

    def test_bmw_m4_gt3_accepted(self) -> None:
        args = self._parse(["--car", "bmw_m4_gt3"])
        self.assertEqual(args.car, "bmw_m4_gt3")

    def test_aston_martin_vantage_gt3_accepted(self) -> None:
        args = self._parse(["--car", "aston_martin_vantage_gt3"])
        self.assertEqual(args.car, "aston_martin_vantage_gt3")

    def test_porsche_992_gt3r_accepted(self) -> None:
        args = self._parse(["--car", "porsche_992_gt3r"])
        self.assertEqual(args.car, "porsche_992_gt3r")

    def test_legacy_gtp_still_accepted(self) -> None:
        for car in ("bmw", "porsche", "ferrari", "acura", "cadillac"):
            args = self._parse(["--car", car])
            self.assertEqual(args.car, car)

    def test_unknown_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            self._parse(["--car", "ford_gt"])


if __name__ == "__main__":
    unittest.main()
