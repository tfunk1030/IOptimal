"""Tests for the streamlined optimize pipeline."""

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from car_model.registry import CarIdentity, TrackIdentity
from pipeline.optimize import (
    OptimizeError,
    _validate_ibt_consistency,
    _auto_calibrate_from_ibts,
    _build_produce_args,
)


# ─── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def mock_ibt_bmw():
    """Mock IBTFile that reports BMW M Hybrid V8 at Sebring."""
    ibt = MagicMock()
    ibt.car_info.return_value = {"car": "BMW M Hybrid V8", "driver": "Test Driver"}
    ibt.track_info.return_value = {
        "track_name": "Sebring International Raceway",
        "track_config": "International",
    }
    return ibt


@pytest.fixture
def mock_ibt_porsche():
    """Mock IBTFile that reports Porsche 963 at Algarve."""
    ibt = MagicMock()
    ibt.car_info.return_value = {"car": "Porsche 963", "driver": "Test Driver"}
    ibt.track_info.return_value = {
        "track_name": "Algarve International Circuit",
        "track_config": "Grand Prix",
    }
    return ibt


# ─── _validate_ibt_consistency ──────────────────────────────────────────────

class TestValidateIbtConsistency:
    def test_no_ibts_raises(self):
        with pytest.raises(OptimizeError, match="No IBT files"):
            _validate_ibt_consistency([])

    @patch("pipeline.optimize.IBTFile")
    def test_single_ibt_success(self, mock_ibt_cls, mock_ibt_bmw):
        mock_ibt_cls.return_value = mock_ibt_bmw
        car, track, ibt = _validate_ibt_consistency(["session1.ibt"])
        assert car.canonical == "bmw"
        assert track.display_name == "Sebring International Raceway"

    @patch("pipeline.optimize.IBTFile")
    def test_unknown_car_raises(self, mock_ibt_cls):
        ibt = MagicMock()
        ibt.car_info.return_value = {"car": "McLaren 720S GT3"}
        ibt.track_info.return_value = {"track_name": "Spa", "track_config": ""}
        mock_ibt_cls.return_value = ibt

        with pytest.raises(OptimizeError, match="Unknown car"):
            _validate_ibt_consistency(["session.ibt"])

    @patch("pipeline.optimize.IBTFile")
    def test_mixed_cars_raises(self, mock_ibt_cls, mock_ibt_bmw, mock_ibt_porsche):
        mock_ibt_cls.side_effect = [mock_ibt_bmw, mock_ibt_porsche]
        with pytest.raises(OptimizeError, match="same car"):
            _validate_ibt_consistency(["s1.ibt", "s2.ibt"])

    @patch("pipeline.optimize.IBTFile")
    def test_consistent_ibts_pass(self, mock_ibt_cls, mock_ibt_bmw):
        mock_ibt_cls.return_value = mock_ibt_bmw
        car, track, _ = _validate_ibt_consistency(["s1.ibt", "s2.ibt", "s3.ibt"])
        assert car.canonical == "bmw"


# ─── _auto_calibrate_from_ibts ──────────────────────────────────────────────

class TestAutoCalibrateFromIbts:
    @patch("car_model.auto_calibrate.save_calibrated_models")
    @patch("car_model.auto_calibrate.fit_models_from_points")
    @patch("car_model.auto_calibrate.save_calibration_points")
    @patch("car_model.auto_calibrate.load_calibration_points")
    @patch("car_model.auto_calibrate.extract_point_from_ibt")
    def test_merges_new_points(
        self, mock_extract, mock_load, mock_save, mock_fit, mock_save_models,
    ):
        # Existing: 4 points
        existing_pts = [MagicMock(session_id=f"existing_{i}") for i in range(4)]
        mock_load.return_value = existing_pts

        # New: 2 points (1 duplicate, 1 new)
        new_pt = MagicMock(session_id="new_1")
        dup_pt = MagicMock(session_id="existing_0")  # duplicate
        mock_extract.side_effect = [new_pt, dup_pt]

        mock_fit.return_value = MagicMock(n_unique_setups=5)

        n_total, n_unique = _auto_calibrate_from_ibts(
            "bmw", ["s1.ibt", "s2.ibt"]
        )

        assert n_total == 5  # 4 existing + 1 new (duplicate skipped)
        mock_save.assert_called_once()
        mock_fit.assert_called_once()

    @patch("car_model.auto_calibrate.save_calibration_points")
    @patch("car_model.auto_calibrate.load_calibration_points")
    @patch("car_model.auto_calibrate.extract_point_from_ibt")
    def test_skips_fit_with_few_points(
        self, mock_extract, mock_load, mock_save,
    ):
        mock_load.return_value = []
        mock_extract.side_effect = [
            MagicMock(session_id="pt1"),
            MagicMock(session_id="pt2"),
        ]

        n_total, n_unique = _auto_calibrate_from_ibts("bmw", ["s1.ibt", "s2.ibt"])
        assert n_total == 2
        assert n_unique == 0  # not enough to fit

    @patch("car_model.auto_calibrate.save_calibration_points")
    @patch("car_model.auto_calibrate.load_calibration_points")
    @patch("car_model.auto_calibrate.extract_point_from_ibt")
    def test_handles_failed_extraction(
        self, mock_extract, mock_load, mock_save,
    ):
        mock_load.return_value = []
        mock_extract.return_value = None  # extraction failed

        n_total, n_unique = _auto_calibrate_from_ibts("bmw", ["bad.ibt"])
        assert n_total == 0
        assert n_unique == 0


# ─── _build_produce_args ────────────────────────────────────────────────────

class TestBuildProduceArgs:
    @patch("analyzer.setup_reader.CurrentSetup.from_ibt")
    def test_builds_correct_namespace(self, mock_from_ibt, mock_ibt_bmw):
        mock_setup = MagicMock()
        mock_setup.wing_angle_deg = 17.0
        mock_setup.fuel_l = 89.0
        mock_from_ibt.return_value = mock_setup

        car = CarIdentity("bmw", "BMW M Hybrid V8", "BMW M Hybrid V8",
                          "bmwlmdh", "bmw")

        args = _build_produce_args(
            car, ["s1.ibt", "s2.ibt"], mock_ibt_bmw,
            sto_path="output.sto",
            scenario_profile="race",
        )

        assert args.car == "bmw"
        assert args.ibt == ["s1.ibt", "s2.ibt"]
        assert args.wing == 17.0
        assert args.fuel == 89.0
        assert args.sto == "output.sto"
        assert args.scenario_profile == "race"

    @patch("analyzer.setup_reader.CurrentSetup.from_ibt")
    def test_handles_missing_wing(self, mock_from_ibt, mock_ibt_bmw):
        mock_setup = MagicMock()
        mock_setup.wing_angle_deg = None
        mock_setup.fuel_l = None
        mock_from_ibt.return_value = mock_setup

        car = CarIdentity("bmw", "BMW M Hybrid V8", "BMW M Hybrid V8",
                          "bmwlmdh", "bmw")

        args = _build_produce_args(car, ["s1.ibt"], mock_ibt_bmw)
        assert args.wing is None
        assert args.fuel is None
