"""W7.1 — `car_model/garage.py` + `auto_calibrate._setup_key()` GT3 awareness.

Covers six findings from `docs/audits/gt3_phase2/car-model-registry.md`:

* #4  BLOCKER — `GarageSetupState` is GTP-shaped (11/13 fields require
  heave/third/torsion). For GT3 these are 0 by definition. The dataclass
  must carry GT3 paired-coil + bump-rubber + splitter fields that
  default to 0.0 for GTP cars and are populated for GT3 setups.
* #5  BLOCKER — `DirectRegression._EXTRACTORS` must include GT3 features
  (`inv_front_corner_spring`, `inv_rear_corner_spring`, `front_bump_rubber_gap`,
  `rear_bump_rubber_gap`, `splitter_height`, plus fuel-coupled compliance terms)
  so a regression fitted on GT3 features doesn't collapse to its intercept.
* #6  BLOCKER — `auto_calibrate._setup_key()` fingerprint must include the
  GT3 fields so two GT3 IBTs varying only by `front_corner_spring_nmm` produce
  different tuples (today they would collapse).
* #16 DEGRADED — `from_current_setup()` must read GT3 fields from the
  CurrentSetup object via getattr-with-default so the resulting state
  carries the real spring rates / bump rubber gaps / splitter height.
* #23 DEGRADED — `GarageOutputModel.default_state(car=...)` must dispatch
  on `car.suspension_arch.has_heave_third` so a GT3 baseline state has
  GT3 fields populated and heave/third/torsion zeroed.
* #25 COSMETIC — track-key comment in `_setup_key()` carries a TODO for the
  next track-coverage pass; no behavioral test required.

W7.2 (auto-calibrate per-car for GT3) is the heavy lift gated on more IBT
capture; this unit lays the dataclass + extractor scaffolding so W7.2 can
land cleanly without rewriting the schema layer.
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from car_model.auto_calibrate import _setup_key  # noqa: E402
from car_model.cars import get_car  # noqa: E402
from car_model.garage import (  # noqa: E402
    DirectRegression,
    GarageOutputModel,
    GarageSetupState,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _make_setup_stub(**overrides):
    """Build a minimal CurrentSetup-like stub with explicit GT3 / GTP fields.

    Uses ``types.SimpleNamespace`` so missing attributes raise AttributeError
    (which ``from_current_setup`` translates to the getattr default).
    """
    ns = types.SimpleNamespace()
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


def _make_calibration_pt(**overrides):
    """Build a minimal CalibrationPoint-like stub for `_setup_key()` testing.

    Carries every GTP field at neutral defaults so two stubs differing only
    by one GT3 field can be compared cleanly.
    """
    base = dict(
        track="spielberg",
        front_heave_setting=0.0,
        rear_third_setting=0.0,
        front_heave_perch_mm=0.0,
        rear_third_perch_mm=0.0,
        front_torsion_od_mm=0.0,
        rear_spring_setting=0.0,
        rear_spring_perch_mm=0.0,
        front_pushrod_mm=0.0,
        rear_pushrod_mm=0.0,
        front_camber_deg=-3.5,
        rear_camber_deg=-2.0,
        fuel_l=50.0,
        front_arb_size="P10",
        front_arb_blade=0,
        rear_arb_size="P5",
        rear_arb_blade=0,
        # GT3-only fields default to 0.0; tests override individually.
        front_corner_spring_nmm=0.0,
        rear_corner_spring_nmm=0.0,
        front_bump_rubber_gap_mm=0.0,
        rear_bump_rubber_gap_mm=0.0,
        splitter_height_mm=0.0,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


# ──────────────────────────────────────────────────────────────────────────────
# Test 1 — GarageSetupState GT3 fields default to 0.0
# ──────────────────────────────────────────────────────────────────────────────


class GarageSetupStateGT3FieldsTests(unittest.TestCase):
    """Audit BLOCKER #4 — dataclass carries GT3 fields with 0.0 defaults."""

    def test_default_state_carries_gt3_fields_zeroed(self) -> None:
        state = GarageSetupState(
            front_pushrod_mm=0.0,
            rear_pushrod_mm=0.0,
            front_heave_nmm=0.0,
            front_heave_perch_mm=0.0,
            rear_third_nmm=0.0,
            rear_third_perch_mm=0.0,
            front_torsion_od_mm=0.0,
            rear_spring_nmm=0.0,
            rear_spring_perch_mm=0.0,
            front_camber_deg=0.0,
        )
        self.assertEqual(state.front_corner_spring_nmm, 0.0)
        self.assertEqual(state.rear_corner_spring_nmm, 0.0)
        self.assertEqual(state.front_bump_rubber_gap_mm, 0.0)
        self.assertEqual(state.rear_bump_rubber_gap_mm, 0.0)
        self.assertEqual(state.splitter_height_mm, 0.0)


# ──────────────────────────────────────────────────────────────────────────────
# Test 2 — from_current_setup populates GT3 fields for a GT3 setup
# ──────────────────────────────────────────────────────────────────────────────


class FromCurrentSetupGT3Tests(unittest.TestCase):
    """Audit DEGRADED #16 — GT3 setup → state populates GT3 fields."""

    def test_gt3_setup_populates_gt3_fields(self) -> None:
        car = get_car("bmw_m4_gt3")
        # analyzer/setup_reader.py:235 stores the per-axle avg of LR/RR
        # SpringRate into ``rear_spring_nmm`` on GT3 setups (not into a
        # separate ``rear_corner_spring_nmm`` attribute), so the from-state
        # extraction surfaces it under the canonical GT3 key.
        setup = _make_setup_stub(
            # GT3 chassis: per-axle paired coil rates + per-corner gaps.
            front_corner_spring_nmm=240.0,
            rear_spring_nmm=210.0,  # analyzer-canonical GT3 rear avg storage
            lf_bump_rubber_gap_mm=14.0,
            rf_bump_rubber_gap_mm=16.0,
            lr_bump_rubber_gap_mm=48.0,
            rr_bump_rubber_gap_mm=52.0,
            splitter_height_mm=22.0,
            front_pushrod_mm=-12.0,
            rear_pushrod_mm=-18.0,
            front_camber_deg=-3.6,
            rear_camber_deg=-2.1,
            wing_angle_deg=8.0,
            fuel_l=60.0,
            # GTP-only fields stay zero (GT3 has no heave/third/torsion).
            front_heave_nmm=0.0,
            rear_third_nmm=0.0,
            front_torsion_od_mm=0.0,
        )
        state = GarageSetupState.from_current_setup(setup, car=car)
        self.assertEqual(state.front_corner_spring_nmm, 240.0)
        self.assertEqual(state.rear_corner_spring_nmm, 210.0)
        # Per-corner gaps averaged into per-axle.
        self.assertAlmostEqual(state.front_bump_rubber_gap_mm, 15.0)
        self.assertAlmostEqual(state.rear_bump_rubber_gap_mm, 50.0)
        self.assertEqual(state.splitter_height_mm, 22.0)
        # Heave/third/torsion stay zero on a GT3 setup (no front torsion bar,
        # no heave/third spring architecture). ``state.rear_spring_nmm`` is the
        # analyzer-canonical storage of rear coil avg = 210.0; the W7.1 dataclass
        # surfaces it both there and under the canonical GT3 alias above.
        self.assertEqual(state.front_heave_nmm, 0.0)
        self.assertEqual(state.rear_third_nmm, 0.0)
        self.assertEqual(state.front_torsion_od_mm, 0.0)


# ──────────────────────────────────────────────────────────────────────────────
# Test 3 — GTP setup leaves GT3 fields at 0.0 (regression)
# ──────────────────────────────────────────────────────────────────────────────


class FromCurrentSetupGTPRegressionTests(unittest.TestCase):
    """A GTP setup must never spill non-zero into GT3 fields."""

    def test_gtp_setup_leaves_gt3_fields_zero(self) -> None:
        car = get_car("bmw")  # BMW M Hybrid V8 — GTP_HEAVE_THIRD_TORSION_FRONT
        setup = _make_setup_stub(
            front_heave_nmm=180.0,
            rear_third_nmm=420.0,
            front_torsion_od_mm=14.5,
            rear_spring_nmm=170.0,
            front_pushrod_mm=-25.0,
            rear_pushrod_mm=-29.0,
            front_camber_deg=-2.9,
            rear_camber_deg=-1.9,
            fuel_l=80.0,
            wing_angle_deg=17.0,
        )
        state = GarageSetupState.from_current_setup(setup, car=car)
        # GTP path populates heave/third/torsion.
        self.assertEqual(state.front_heave_nmm, 180.0)
        self.assertEqual(state.rear_third_nmm, 420.0)
        # GT3 fields stay zero.
        self.assertEqual(state.front_corner_spring_nmm, 0.0)
        self.assertEqual(state.rear_corner_spring_nmm, 0.0)
        self.assertEqual(state.front_bump_rubber_gap_mm, 0.0)
        self.assertEqual(state.rear_bump_rubber_gap_mm, 0.0)
        self.assertEqual(state.splitter_height_mm, 0.0)


# ──────────────────────────────────────────────────────────────────────────────
# Test 4 — DirectRegression._EXTRACTORS carries GT3 keys
# ──────────────────────────────────────────────────────────────────────────────


class DirectRegressionExtractorsGT3Tests(unittest.TestCase):
    """Audit BLOCKER #5 — extractor map must include GT3 features."""

    def _build_extractors(self) -> dict:
        # ``from_model`` constructs the extractor map; we don't care about the
        # coefficients — the dict is the unit under test.
        dr = DirectRegression.from_model([0.0], [])
        return dr._EXTRACTORS

    def test_required_gt3_features_registered(self) -> None:
        extractors = self._build_extractors()
        for key in (
            "front_corner_spring",
            "inv_front_corner_spring",
            "rear_corner_spring",
            "inv_rear_corner_spring",
            "front_bump_rubber_gap",
            "rear_bump_rubber_gap",
            "splitter_height",
            "fuel_x_inv_front_corner_spring",
            "fuel_x_inv_rear_corner_spring",
        ):
            self.assertIn(key, extractors, f"missing extractor: {key}")


# ──────────────────────────────────────────────────────────────────────────────
# Test 5 — GT3 extractors return real numbers on a populated state
# ──────────────────────────────────────────────────────────────────────────────


class DirectRegressionGT3ExtractorBehaviourTests(unittest.TestCase):
    """Audit BLOCKER #5 — extractor behaviour: real numbers, no errors."""

    def _state(self) -> GarageSetupState:
        return GarageSetupState(
            front_pushrod_mm=-12.0,
            rear_pushrod_mm=-18.0,
            front_heave_nmm=0.0,
            front_heave_perch_mm=0.0,
            rear_third_nmm=0.0,
            rear_third_perch_mm=0.0,
            front_torsion_od_mm=0.0,
            rear_spring_nmm=0.0,
            rear_spring_perch_mm=0.0,
            front_camber_deg=-3.6,
            rear_camber_deg=-2.1,
            fuel_l=60.0,
            front_corner_spring_nmm=240.0,
            rear_corner_spring_nmm=210.0,
            front_bump_rubber_gap_mm=15.0,
            rear_bump_rubber_gap_mm=50.0,
            splitter_height_mm=22.0,
        )

    def test_gt3_extractors_return_finite_floats(self) -> None:
        state = self._state()
        extractors = DirectRegression.from_model([0.0], [])._EXTRACTORS
        for key in (
            "front_corner_spring",
            "inv_front_corner_spring",
            "rear_corner_spring",
            "inv_rear_corner_spring",
            "front_bump_rubber_gap",
            "rear_bump_rubber_gap",
            "splitter_height",
            "fuel_x_inv_front_corner_spring",
            "fuel_x_inv_rear_corner_spring",
        ):
            val = extractors[key](state)
            self.assertIsInstance(val, float, f"{key}: not a float")
            # Compliance terms should be finite and positive on populated state.
            self.assertGreater(val, 0.0, f"{key}: expected > 0 on populated state")

    def test_inv_front_corner_spring_zero_safe(self) -> None:
        """Division-by-zero guard — GTP setups have spring=0, must return 0.0."""
        gtp_state = GarageSetupState(
            front_pushrod_mm=0.0,
            rear_pushrod_mm=0.0,
            front_heave_nmm=180.0,
            front_heave_perch_mm=0.0,
            rear_third_nmm=420.0,
            rear_third_perch_mm=0.0,
            front_torsion_od_mm=14.5,
            rear_spring_nmm=170.0,
            rear_spring_perch_mm=0.0,
            front_camber_deg=0.0,
            front_corner_spring_nmm=0.0,  # GTP — no paired front coil
            rear_corner_spring_nmm=0.0,
        )
        extractors = DirectRegression.from_model([0.0], [])._EXTRACTORS
        self.assertEqual(extractors["inv_front_corner_spring"](gtp_state), 0.0)
        self.assertEqual(extractors["inv_rear_corner_spring"](gtp_state), 0.0)
        self.assertEqual(extractors["fuel_x_inv_front_corner_spring"](gtp_state), 0.0)
        self.assertEqual(extractors["fuel_x_inv_rear_corner_spring"](gtp_state), 0.0)


# ──────────────────────────────────────────────────────────────────────────────
# Test 6 — _setup_key() distinguishes GT3 setups varying only by spring rate
# ──────────────────────────────────────────────────────────────────────────────


class SetupKeyGT3FingerprintTests(unittest.TestCase):
    """Audit BLOCKER #6 — GT3 fingerprint distinguishes coil-rate variations."""

    def test_two_gt3_setups_differing_only_by_front_spring_collapse_no_more(self) -> None:
        pt_a = _make_calibration_pt(front_corner_spring_nmm=220.0)
        pt_b = _make_calibration_pt(front_corner_spring_nmm=260.0)
        self.assertNotEqual(_setup_key(pt_a), _setup_key(pt_b))

    def test_rear_corner_spring_distinguishes(self) -> None:
        pt_a = _make_calibration_pt(rear_corner_spring_nmm=180.0)
        pt_b = _make_calibration_pt(rear_corner_spring_nmm=220.0)
        self.assertNotEqual(_setup_key(pt_a), _setup_key(pt_b))

    def test_bump_rubber_gap_distinguishes(self) -> None:
        pt_a = _make_calibration_pt(front_bump_rubber_gap_mm=12.0)
        pt_b = _make_calibration_pt(front_bump_rubber_gap_mm=18.0)
        self.assertNotEqual(_setup_key(pt_a), _setup_key(pt_b))

    def test_splitter_height_distinguishes(self) -> None:
        pt_a = _make_calibration_pt(splitter_height_mm=15.0)
        pt_b = _make_calibration_pt(splitter_height_mm=25.0)
        self.assertNotEqual(_setup_key(pt_a), _setup_key(pt_b))


# ──────────────────────────────────────────────────────────────────────────────
# Test 7 — _setup_key() GTP regression: heave/third still distinguish
# ──────────────────────────────────────────────────────────────────────────────


class SetupKeyGTPRegressionTests(unittest.TestCase):
    """Appending GT3 slots must not break legacy GTP fingerprint behaviour."""

    def test_two_gtp_setups_differing_only_by_heave_distinguish(self) -> None:
        pt_a = _make_calibration_pt(front_heave_setting=180.0)
        pt_b = _make_calibration_pt(front_heave_setting=220.0)
        self.assertNotEqual(_setup_key(pt_a), _setup_key(pt_b))

    def test_two_gtp_setups_differing_only_by_third_distinguish(self) -> None:
        pt_a = _make_calibration_pt(rear_third_setting=420.0)
        pt_b = _make_calibration_pt(rear_third_setting=480.0)
        self.assertNotEqual(_setup_key(pt_a), _setup_key(pt_b))

    def test_identical_setups_collapse(self) -> None:
        """Sanity check — same inputs → same fingerprint."""
        pt_a = _make_calibration_pt(front_heave_setting=180.0)
        pt_b = _make_calibration_pt(front_heave_setting=180.0)
        self.assertEqual(_setup_key(pt_a), _setup_key(pt_b))


# ──────────────────────────────────────────────────────────────────────────────
# Test 8 — GarageOutputModel.default_state(car=...) GT3 vs GTP
# ──────────────────────────────────────────────────────────────────────────────


class GarageOutputModelDefaultStateTests(unittest.TestCase):
    """Audit DEGRADED #23 — default_state dispatches on suspension architecture."""

    def test_gt3_default_state_populates_coil_zeroes_heave(self) -> None:
        car = get_car("bmw_m4_gt3")
        model = GarageOutputModel(name="gt3_test")
        state = model.default_state(car=car)
        # GT3 fields populated from defaults.
        self.assertGreater(state.front_corner_spring_nmm, 0.0)
        self.assertGreater(state.rear_corner_spring_nmm, 0.0)
        self.assertGreater(state.splitter_height_mm, 0.0)
        # GTP fields zeroed.
        self.assertEqual(state.front_heave_nmm, 0.0)
        self.assertEqual(state.rear_third_nmm, 0.0)
        self.assertEqual(state.front_torsion_od_mm, 0.0)
        self.assertEqual(state.rear_spring_nmm, 0.0)

    def test_gtp_default_state_populates_heave_zeroes_coil(self) -> None:
        car = get_car("bmw")
        model = GarageOutputModel(name="gtp_test")
        state = model.default_state(car=car)
        # GTP fields populated.
        self.assertGreater(state.front_heave_nmm, 0.0)
        self.assertGreater(state.rear_third_nmm, 0.0)
        self.assertGreater(state.front_torsion_od_mm, 0.0)
        self.assertGreater(state.rear_spring_nmm, 0.0)
        # GT3 fields zeroed.
        self.assertEqual(state.front_corner_spring_nmm, 0.0)
        self.assertEqual(state.rear_corner_spring_nmm, 0.0)
        self.assertEqual(state.front_bump_rubber_gap_mm, 0.0)
        self.assertEqual(state.rear_bump_rubber_gap_mm, 0.0)
        self.assertEqual(state.splitter_height_mm, 0.0)

    def test_no_car_kwarg_legacy_gtp_path(self) -> None:
        """Backward compat — calls without `car=` get the legacy GTP baseline."""
        model = GarageOutputModel(name="no_car_test")
        state = model.default_state()
        # Legacy GTP defaults.
        self.assertEqual(state.front_heave_nmm, model.default_front_heave_nmm)
        self.assertEqual(state.rear_third_nmm, model.default_rear_third_nmm)
        # GT3 fields zeroed (dataclass defaults).
        self.assertEqual(state.front_corner_spring_nmm, 0.0)
        self.assertEqual(state.splitter_height_mm, 0.0)


if __name__ == "__main__":
    unittest.main()
