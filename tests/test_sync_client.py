import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from teamdb.sync_client import SyncClient


class _DummyResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _SuccessClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, *args, **kwargs):
        return _DummyResponse(201, "created")


class _FailingClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, *args, **kwargs):
        return _DummyResponse(500, "server boom")


class _NetworkErrorClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, *args, **kwargs):
        raise OSError("offline")


class SyncClientTests(unittest.TestCase):
    @staticmethod
    def _httpx_stub(client_cls):
        return SimpleNamespace(Client=client_cls, RequestError=OSError)

    def _make_client(self, db_path: Path) -> SyncClient:
        return SyncClient(
            "https://example.invalid",
            "test-key",
            local_db_path=db_path,
            push_interval=0.01,
            pull_interval=1.0,
        )

    def test_queue_setup_increments_pending_counter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = self._make_client(Path(tmp) / "sync.db")
            client.queue_setup({"foo": "bar"})
            self.assertEqual(client.status.queued_observations, 1)

    def test_push_failure_marks_last_error_and_failure_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = self._make_client(Path(tmp) / "sync.db")
            client.queue_observation({"session": "abc"})

            with patch("teamdb.sync_client.httpx", self._httpx_stub(_FailingClient)):
                pushed = client._push_pending()

            self.assertEqual(pushed, 0)
            self.assertTrue(client._last_push_failed)
            self.assertIn("HTTP 500", client.status.last_error or "")

    def test_network_failure_marks_last_error_and_failure_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = self._make_client(Path(tmp) / "sync.db")
            client.queue_observation({"session": "abc"})

            with patch("teamdb.sync_client.httpx", self._httpx_stub(_NetworkErrorClient)):
                pushed = client._push_pending()

            self.assertEqual(pushed, 0)
            self.assertTrue(client._last_push_failed)
            self.assertIn("offline", client.status.last_error or "")

    def test_successful_push_clears_last_error_and_recounts_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = self._make_client(Path(tmp) / "sync.db")
            client.queue_observation({"session": "abc"})
            client.queue_setup({"setup": "xyz"})
            client.status.last_error = "old error"

            with patch("teamdb.sync_client.httpx", self._httpx_stub(_SuccessClient)):
                pushed = client._push_pending()

            self.assertEqual(pushed, 2)
            self.assertFalse(client._last_push_failed)
            self.assertIsNone(client.status.last_error)
            self.assertEqual(client.status.queued_observations, 0)


if __name__ == "__main__":
    unittest.main()
