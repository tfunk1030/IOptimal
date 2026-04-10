import contextlib
import io
import sys
import unittest
from unittest import mock

# Allow importing pipeline.produce in environments without scipy.
if "scipy" not in sys.modules:
    _scipy = mock.MagicMock()
    _interpolate = mock.MagicMock()
    _interpolate.RegularGridInterpolator = object
    _optimize = mock.MagicMock()
    _optimize.minimize = lambda *args, **kwargs: None
    _optimize.brentq = lambda *args, **kwargs: 0.0
    _stats = mock.MagicMock()
    _qmc = mock.MagicMock()
    _qmc.LatinHypercube = object
    _qmc.Sobol = object
    _stats.qmc = _qmc
    _spatial = mock.MagicMock()
    _distance = mock.MagicMock()
    _distance.cdist = lambda *args, **kwargs: []
    _spatial.distance = _distance
    _scipy.interpolate = _interpolate
    _scipy.optimize = _optimize
    _scipy.stats = _stats
    _scipy.spatial = _spatial
    sys.modules["scipy"] = _scipy
    sys.modules["scipy.interpolate"] = _interpolate
    sys.modules["scipy.optimize"] = _optimize
    sys.modules["scipy.stats"] = _stats
    sys.modules["scipy.stats.qmc"] = _qmc
    sys.modules["scipy.spatial"] = _spatial
    sys.modules["scipy.spatial.distance"] = _distance

from pipeline.produce import (
    PipelineInputError,
    _apply_calibration_step_blocks,
    _normalize_grid_search_params_for_overrides,
    _wrap_no_valid_laps_error,
    main,
)


class ProduceErrorTests(unittest.TestCase):
    def test_wrap_no_valid_laps_error_returns_pipeline_input_error(self) -> None:
        wrapped = _wrap_no_valid_laps_error(
            ValueError("No valid laps found in IBT file"),
            ibt_path=r"ibtfiles\badbmwquali.ibt",
            car_name="bmw",
            track_hint="sebring",
        )

        self.assertIsInstance(wrapped, PipelineInputError)
        self.assertIn("No usable complete timed lap was detected", str(wrapped))
        self.assertIn("badbmwquali.ibt", str(wrapped))
        self.assertIn("python -m solver.solve --car bmw --track sebring", str(wrapped))

    def test_main_prints_clean_pipeline_input_error(self) -> None:
        stderr = io.StringIO()
        argv = [
            "pipeline.produce",
            "--car",
            "bmw",
            "--ibt",
            "ibtfiles\\badbmwquali.ibt",
        ]
        with mock.patch.object(sys, "argv", argv), mock.patch(
            "pipeline.produce.produce",
            side_effect=PipelineInputError("friendly message"),
        ), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exc:
                main()

        self.assertEqual(1, exc.exception.code)
        self.assertEqual("ERROR: friendly message", stderr.getvalue().strip())

    def test_normalize_grid_search_params_maps_aliases(self) -> None:
        params = {
            "front_heave_nmm": 480.0,
            "rear_third_nmm": 420.0,
            "front_arb_blade_start": 3,
            "rear_arb_blade_start": 4,
            "front_toe_deg": -0.2,
            "rear_toe_deg": 0.1,
            "rear_spring_nmm": 165.0,
        }
        normalized = _normalize_grid_search_params_for_overrides(params)

        self.assertEqual(normalized["front_heave_spring_nmm"], 480.0)
        self.assertEqual(normalized["rear_third_spring_nmm"], 420.0)
        self.assertEqual(normalized["front_arb_blade"], 3)
        self.assertEqual(normalized["rear_arb_blade"], 4)
        self.assertEqual(normalized["front_toe_mm"], -0.2)
        self.assertEqual(normalized["rear_toe_mm"], 0.1)
        self.assertEqual(normalized["rear_spring_rate_nmm"], 165.0)

    def test_normalize_grid_search_params_preserves_existing_canonical_values(self) -> None:
        params = {
            "front_toe_deg": -0.5,
            "front_toe_mm": -0.3,
            "rear_spring_nmm": 150.0,
            "rear_spring_rate_nmm": 160.0,
        }
        normalized = _normalize_grid_search_params_for_overrides(params)

        self.assertEqual(normalized["front_toe_mm"], -0.3)
        self.assertEqual(normalized["rear_spring_rate_nmm"], 160.0)

    def test_apply_calibration_step_blocks_nuls_only_blocked_steps(self) -> None:
        s1, s2, s3, s4, s5, s6 = ("a", "b", "c", "d", "e", "f")
        out = _apply_calibration_step_blocks(
            step1=s1,
            step2=s2,
            step3=s3,
            step4=s4,
            step5=s5,
            step6=s6,
            blocked_steps={2, 4, 6},
        )
        self.assertEqual(out, ("a", None, "c", None, "e", None))


if __name__ == "__main__":
    unittest.main()
