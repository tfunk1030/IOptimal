import tempfile
import unittest
from pathlib import Path

from pipeline.produce import produce_result
from webapp.services import IOptimalWebService
from webapp.settings import AppSettings
from webapp.types import RunCreateRequest


DATA_FILE = Path("data/telemetry/bmw_sebring_2026-03-06.ibt")


def _find_recommended(summary_payload: dict, label: str) -> float:
    for group in summary_payload["setup_groups"]:
        for row in group["rows"]:
            if row["label"] == label:
                return float(str(row["recommended"]).split()[0])
    raise AssertionError(f"Could not find {label!r} in setup groups")


@unittest.skipUnless(DATA_FILE.exists(), "Telemetry fixture not available")
class WebAppRegressionTests(unittest.TestCase):
    def test_single_session_adapter_matches_pipeline_rear_ride_height(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            settings = AppSettings.from_env(tempdir)
            settings.ensure_directories()
            service = IOptimalWebService(settings)
            request = RunCreateRequest(
                mode="single_session",
                car="bmw",
                ibt_paths=[DATA_FILE],
                wing=17.0,
                use_learning=False,
            )

            kind, summary_payload, _artifacts = service.execute_run("regression", request, lambda phase: None)

            direct_args = type("Args", (), {
                "car": "bmw",
                "ibt": str(DATA_FILE),
                "wing": 17.0,
                "lap": None,
                "balance": 50.14,
                "tolerance": 0.1,
                "fuel": None,
                "free": False,
                "sto": None,
                "json": None,
                "report_only": True,
                "no_learn": True,
                "legacy_solver": False,
                "min_lap_time": 108.0,
                "outlier_pct": 0.115,
                "stint": False,
                "stint_threshold": 1.5,
                "verbose": False,
                "space": False,
            })()
            direct_result = produce_result(direct_args, emit_report=False, compact_report=False)

        self.assertEqual(kind, "single_session")
        self.assertAlmostEqual(
            _find_recommended(summary_payload, "Rear ride height"),
            round(direct_result["step1"].static_rear_rh_mm, 1),
            places=1,
        )


if __name__ == "__main__":
    unittest.main()
