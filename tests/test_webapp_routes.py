import tempfile
import unittest
from pathlib import Path
from io import BytesIO
from unittest.mock import patch

from fastapi.testclient import TestClient

from webapp.app import create_app
from webapp.settings import AppSettings
from webapp.types import KnowledgeSummaryView, RunCreateRequest


class WebAppRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.settings = AppSettings.from_env(self.tempdir.name)
        self.settings.ensure_directories()
        self.app = create_app(self.settings)

    def test_new_run_page_renders(self) -> None:
        with TestClient(self.app) as client:
            response = client.get("/runs/new")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Run the solver without touching the CLI", response.text)

    def test_create_track_solve_run_redirects_to_status_page(self) -> None:
        with TestClient(self.app) as client:
            with patch.object(client.app.state.jobs, "submit", return_value=None):
                response = client.post(
                    "/runs",
                    data={
                        "mode": "track_solve",
                        "car": "bmw",
                        "track": "sebring",
                        "wing": "17",
                        "balance": "50.14",
                        "tolerance": "0.1",
                        "use_learning": "on",
                    },
                    follow_redirects=False,
                )
                run_id = response.headers["location"].rsplit("/", 1)[-1]
                payload = client.app.state.repository.get_run(run_id)

        self.assertEqual(response.status_code, 303)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["run"]["mode"], "track_solve")

    def test_invalid_compare_request_redirects_back_with_error(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/runs",
                data={"mode": "comparison", "car": "bmw"},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertIn("/runs/new?mode=comparison", response.headers["location"])

    def test_invalid_compare_request_cleans_orphaned_uploads(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/runs",
                data={"mode": "comparison", "car": "bmw"},
                files={"ibt_files": ("session.ibt", BytesIO(b"ibt"), "application/octet-stream")},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(list(self.settings.uploads_dir.glob("*")), [])

    def test_completed_run_page_renders_summary(self) -> None:
        with TestClient(self.app) as client:
            repo = client.app.state.repository
            run_id = "done1234"
            repo.create_run(run_id, RunCreateRequest(mode="single_session", car="bmw", ibt_paths=[Path("session.ibt")]))
            repo.save_summary(
                run_id,
                "single_session",
                {
                    "result_kind": "single_session",
                    "title": "Single Session Analysis",
                    "subtitle": "BMW on Sebring",
                    "car_name": "BMW M Hybrid V8",
                    "track_name": "Sebring",
                    "lap_label": "Lap 10 · 100.100s",
                    "assessment": "Compromised",
                    "confidence_label": "Medium",
                    "overview_badges": ["Wing 17 deg"],
                    "problems": [{"severity": "Critical", "symptom": "Bottoming", "cause": "Too soft", "speed_context": "braking"}],
                    "top_changes": [{"label": "Rear ride height", "current": "48.0 mm", "recommended": "49.0 mm", "delta": "+1.0 mm"}],
                    "setup_groups": [{"name": "Platform", "help_text": "Platform help", "rows": [{"label": "Rear ride height", "current": "48.0 mm", "recommended": "49.0 mm", "delta": "+1.0 mm"}]}],
                    "telemetry": [{"label": "Lap time", "baseline": "100.100 s", "predicted": "-", "delta": "N/A"}],
                    "engineering_notes": ["Legality check passed."],
                    "report_text": "report",
                    "candidate_family": "compromise",
                    "candidate_score": 0.7,
                    "artifact_links": [],
                },
            )
            repo.update_run(run_id, state="completed", phase="Complete")
            response = client.get(f"/runs/{run_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Single Session Analysis", response.text)
        self.assertIn("Rear ride height", response.text)

    def test_artifact_download_works(self) -> None:
        artifact_path = Path(self.tempdir.name) / "artifact.txt"
        artifact_path.write_text("artifact-content")
        with TestClient(self.app) as client:
            repo = client.app.state.repository
            run_id = "artifactrun"
            repo.create_run(run_id, RunCreateRequest(mode="track_solve", car="bmw", track="sebring"))
            repo.save_artifact("artifact1", run_id, "report", "Report", artifact_path)
            response = client.get("/artifacts/artifact1/download")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"artifact-content")

    def test_knowledge_page_renders(self) -> None:
        fake_summary = KnowledgeSummaryView(total_observations=1, total_deltas=2, recent_learnings=["Rear third spring helped."])
        with TestClient(self.app) as client:
            with patch.object(client.app.state.service, "load_knowledge_summary", return_value=fake_summary):
                response = client.get("/knowledge")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Rear third spring helped.", response.text)


if __name__ == "__main__":
    unittest.main()
