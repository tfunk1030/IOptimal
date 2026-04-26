"""Tests for desktop.config: atomic save, validation, and locking."""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import platform
from pathlib import Path
from unittest import mock

import pytest

from desktop.config import AppConfig


# --- Atomic save -----------------------------------------------------------


def test_save_creates_config_file(tmp_path: Path) -> None:
    cfg = AppConfig(team_name="Test Team", webapp_port=9001)
    cfg.save(tmp_path)
    assert (tmp_path / "config.json").exists()
    data = json.loads((tmp_path / "config.json").read_text())
    assert data["team_name"] == "Test Team"
    assert data["webapp_port"] == 9001


def test_save_does_not_leave_tmp_file(tmp_path: Path) -> None:
    cfg = AppConfig(team_name="X")
    cfg.save(tmp_path)
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_save_is_atomic_against_replace_failure(tmp_path: Path) -> None:
    """If os.replace fails after the temp file is written, the existing
    config must remain intact (not be partially written)."""
    cfg = AppConfig(team_name="Original")
    cfg.save(tmp_path)
    original = (tmp_path / "config.json").read_text()

    cfg2 = AppConfig(team_name="Replacement")
    with mock.patch("desktop.config.os.replace", side_effect=OSError("boom")):
        with pytest.raises(OSError):
            cfg2.save(tmp_path)

    # Original config still intact.
    assert (tmp_path / "config.json").read_text() == original


def test_save_uses_fsync_before_replace(tmp_path: Path) -> None:
    """Verify fsync is called on the temp file before os.replace."""
    call_order: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def tracked_fsync(*args, **kwargs):
        call_order.append("fsync")
        return real_fsync(*args, **kwargs)

    def tracked_replace(*args, **kwargs):
        call_order.append("replace")
        return real_replace(*args, **kwargs)

    with mock.patch("desktop.config.os.fsync", side_effect=tracked_fsync), mock.patch(
        "desktop.config.os.replace", side_effect=tracked_replace
    ):
        AppConfig(team_name="X").save(tmp_path)

    assert call_order == ["fsync", "replace"]


# --- Load + validation -----------------------------------------------------


def test_load_missing_returns_defaults(tmp_path: Path) -> None:
    cfg = AppConfig.load(tmp_path)
    assert cfg.team_name == ""
    assert cfg.webapp_port == 8000


def test_load_corrupted_json_returns_defaults(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "config.json").write_text("{not valid json")
    with caplog.at_level(logging.WARNING, logger="desktop.config"):
        cfg = AppConfig.load(tmp_path)
    assert cfg.team_name == ""
    assert any("unreadable" in r.message for r in caplog.records)


def test_load_unknown_fields_are_filtered(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"team_name": "T", "removed_field": "x"})
    )
    cfg = AppConfig.load(tmp_path)
    assert cfg.team_name == "T"


def test_load_invalid_url_is_cleared(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"team_server_url": "not-a-url", "api_key": "x" * 32})
    )
    with caplog.at_level(logging.WARNING, logger="desktop.config"):
        cfg = AppConfig.load(tmp_path)
    assert cfg.team_server_url == ""
    assert any("team_server_url" in r.message for r in caplog.records)


def test_load_valid_url_preserved(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "team_server_url": "https://example.com/api",
                "api_key": "a" * 32,
            }
        )
    )
    cfg = AppConfig.load(tmp_path)
    assert cfg.team_server_url == "https://example.com/api"
    assert cfg.api_key == "a" * 32


def test_load_short_api_key_is_cleared(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"api_key": "short"}))
    with caplog.at_level(logging.WARNING, logger="desktop.config"):
        cfg = AppConfig.load(tmp_path)
    assert cfg.api_key == ""
    assert any("api_key" in r.message for r in caplog.records)


def test_load_api_key_with_invalid_chars_is_cleared(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"api_key": "!" * 40}))
    cfg = AppConfig.load(tmp_path)
    assert cfg.api_key == ""


def test_load_missing_telemetry_dir_warns_but_keeps_value(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bogus = str(tmp_path / "does_not_exist")
    (tmp_path / "config.json").write_text(json.dumps({"telemetry_dir": bogus}))
    with caplog.at_level(logging.WARNING, logger="desktop.config"):
        cfg = AppConfig.load(tmp_path)
    # Per spec: warn, don't crash, don't reset the value.
    assert cfg.telemetry_dir == bogus
    assert any("telemetry_dir" in r.message for r in caplog.records)


# --- Round-trip ------------------------------------------------------------


def test_round_trip_preserves_values(tmp_path: Path) -> None:
    src = AppConfig(
        team_server_url="https://example.com",
        api_key="b" * 40,
        team_name="Team",
        webapp_port=8123,
        car_filter=["bmw", "porsche"],
    )
    src.save(tmp_path)
    loaded = AppConfig.load(tmp_path)
    assert loaded.team_server_url == src.team_server_url
    assert loaded.api_key == src.api_key
    assert loaded.team_name == src.team_name
    assert loaded.webapp_port == src.webapp_port
    assert loaded.car_filter == src.car_filter


# --- Concurrent save -------------------------------------------------------


def _save_in_process(args):
    config_dir, team_name = args
    cfg = AppConfig(team_name=team_name, api_key="z" * 40)
    cfg.save(Path(config_dir))


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="multiprocessing fork semantics differ on Windows",
)
def test_concurrent_save_produces_valid_file(tmp_path: Path) -> None:
    """Multiple processes saving simultaneously must always leave a valid
    JSON file (the lock serializes writes; the atomic replace guarantees
    no partial content)."""
    args = [(str(tmp_path), f"team-{i}") for i in range(8)]
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(4) as pool:
        pool.map(_save_in_process, args)

    path = tmp_path / "config.json"
    assert path.exists()
    data = json.loads(path.read_text())  # must be parseable
    assert data["team_name"].startswith("team-")
    assert data["api_key"] == "z" * 40
