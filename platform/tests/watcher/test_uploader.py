from pathlib import Path

from watcher.config import WatcherConfig
from watcher.uploader import IBTUploader


class DummyResponse:
    def __init__(self):
        self._json = {"session_id": "abc123", "status": "processing"}

    def raise_for_status(self):
        return None

    def json(self):
        return self._json


def test_uploader_posts_expected_fields(monkeypatch, tmp_path: Path):
    cfg = WatcherConfig()
    cfg.server_url = "http://localhost:8000"
    cfg.access_token = "token"
    cfg.driver_id = "driver-x"
    uploader = IBTUploader(cfg)

    called = {}

    def fake_post(url, files, data, headers):
        called["url"] = url
        called["files"] = files
        called["data"] = data
        called["headers"] = headers
        return DummyResponse()

    monkeypatch.setattr(uploader.client, "post", fake_post)

    ibt = tmp_path / "run.ibt"
    jsn = tmp_path / "run.json"
    sto = tmp_path / "run.sto"
    ibt.write_bytes(b"ibt")
    jsn.write_text("{}")
    sto.write_bytes(b"sto")

    result = uploader.upload(
        ibt_path=ibt,
        solver_json_path=jsn,
        solver_sto_path=sto,
        car="bmw",
        wing=17.0,
        lap=4,
    )

    assert result.session_id == "abc123"
    assert called["url"].endswith("/api/upload-ibt")
    assert called["data"]["car"] == "bmw"
    assert called["headers"]["Authorization"] == "Bearer token"

