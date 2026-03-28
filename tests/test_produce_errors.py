import contextlib
import io
import sys
import unittest
from unittest import mock

from pipeline.produce import PipelineInputError, _wrap_no_valid_laps_error, main


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


if __name__ == "__main__":
    unittest.main()
