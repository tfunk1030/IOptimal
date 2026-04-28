"""GT3-aware watcher + desktop config tests (W8.2).

Covers the audit F5 / F13 fixes:

  * ``car_model.registry._BY_IRACING_PATH`` indexes every CarIdentity by its
    iRacing CarPath so the watcher can resolve GT3 IBTs whose CarScreenName
    has been localised or carries an EVO/year suffix.
  * ``resolve_car()`` accepts CarPath as a first-class lookup key.
  * ``IBTFile.car_info()`` exposes ``iracing_car_path`` (already added in
    a previous wave; pinned here as a regression check).
  * ``watcher.service._detect_car_and_track`` prefers CarPath over
    CarScreenName so a misleading screen name cannot misroute the IBT.
  * ``desktop.config.AppConfig`` carries an optional ``class_filter`` field
    that round-trips through ``save()`` / ``load()``.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from car_model.registry import (
    _BY_IRACING_PATH,
    CarIdentity,
    resolve_car,
    resolve_car_from_ibt,
)
from desktop.config import AppConfig


# ─── Registry: CarPath index ───────────────────────────────────────────────

class TestCarPathIndex:
    """`_BY_IRACING_PATH` should map every populated CarPath to its identity."""

    def test_bmw_m4_gt3_indexed(self):
        assert "bmwm4gt3" in _BY_IRACING_PATH
        identity = _BY_IRACING_PATH["bmwm4gt3"]
        assert identity.canonical == "bmw_m4_gt3"

    def test_aston_gt3_indexed(self):
        assert "amvantageevogt3" in _BY_IRACING_PATH
        assert _BY_IRACING_PATH["amvantageevogt3"].canonical == \
            "aston_martin_vantage_gt3"

    def test_porsche_gt3_indexed(self):
        assert "porsche992rgt3" in _BY_IRACING_PATH
        assert _BY_IRACING_PATH["porsche992rgt3"].canonical == "porsche_992_gt3r"

    def test_gtp_paths_indexed(self):
        # Regression: every GTP entry now also carries a CarPath that mirrors
        # its sto_id.  Once iRacing diverges them, the field is the place we
        # update — call sites continue working.
        assert _BY_IRACING_PATH["bmwlmdh"].canonical == "bmw"
        assert _BY_IRACING_PATH["porsche963"].canonical == "porsche"
        assert _BY_IRACING_PATH["ferrari499p"].canonical == "ferrari"

    def test_resolve_bmwm4gt3_returns_gt3(self):
        # W1.3 regression: substring fallback used to route "bmwm4gt3" to
        # the GTP BMW entry.  W1.3 added the GT3 registry rows; W8.2 now
        # makes the CarPath lookup explicit.
        result = resolve_car("bmwm4gt3")
        assert result is not None
        assert result.canonical == "bmw_m4_gt3"
        assert result.canonical != "bmw"


# ─── IBTFile.car_info() exposes iracing_car_path ──────────────────────────

class TestIBTFileCarInfoStructure:
    """`IBTFile.car_info()` must expose ``iracing_car_path`` for the watcher."""

    def test_car_info_keys_documented(self):
        # We can't easily build a real IBTFile in a unit test, but we can
        # at least verify the docstring + by mocking the session_info shape
        # the parser dispatches on.
        from track_model.ibt_parser import IBTFile

        ibt = IBTFile.__new__(IBTFile)  # bypass __init__ — just need car_info
        ibt._session_info_cache = None  # type: ignore[attr-defined]
        ibt.session_info = {
            "DriverInfo": {
                "DriverCarIdx": 0,
                "Drivers": [
                    {
                        "CarIdx": 0,
                        "CarIsPaceCar": 0,
                        "UserName": "Test Driver",
                        "CarScreenName": "BMW M4 GT3 EVO",
                        "CarPath": "bmwm4gt3",
                    }
                ],
            }
        }
        info = ibt.car_info()
        assert info["car"] == "BMW M4 GT3 EVO"
        assert info["car_path"] == "bmwm4gt3"
        assert info["iracing_car_path"] == "bmwm4gt3"
        assert info["driver"] == "Test Driver"

    def test_car_info_handles_missing_carpath(self):
        # Older IBTs / modded content may have an empty CarPath.  Both
        # alias keys must still be present (empty string).
        from track_model.ibt_parser import IBTFile

        ibt = IBTFile.__new__(IBTFile)
        ibt.session_info = {
            "DriverInfo": {
                "DriverCarIdx": 0,
                "Drivers": [
                    {
                        "CarIdx": 0,
                        "CarIsPaceCar": 0,
                        "UserName": "Driver",
                        "CarScreenName": "BMW M Hybrid V8",
                        # CarPath omitted
                    }
                ],
            }
        }
        info = ibt.car_info()
        assert info["car"] == "BMW M Hybrid V8"
        assert info["car_path"] == ""
        assert info["iracing_car_path"] == ""


# ─── resolve_car_from_ibt prefers CarPath ─────────────────────────────────

class _MockIBT:
    """Minimal IBTFile stand-in — only ``car_info()`` is used by the watcher."""

    def __init__(self, *, screen_name: str, car_path: str, driver: str = "Test"):
        self._info = {
            "driver": driver,
            "car": screen_name,
            "car_path": car_path,
            "iracing_car_path": car_path,
            "car_idx": 0,
        }

    def car_info(self) -> dict:
        return dict(self._info)

    def track_info(self) -> dict:
        return {"track_name": "Test Track", "track_config": "", "track_length": "",
                "surface_temp": ""}


class TestResolveCarFromIBT:
    """`resolve_car_from_ibt` must prefer CarPath over CarScreenName."""

    def test_gt3_resolves_via_carpath(self):
        ibt = _MockIBT(screen_name="BMW M4 GT3 EVO", car_path="bmwm4gt3")
        identity = resolve_car_from_ibt(ibt)  # type: ignore[arg-type]
        assert identity is not None
        assert identity.canonical == "bmw_m4_gt3"

    def test_carpath_overrides_misleading_screen_name(self):
        # The whole point of preferring CarPath: even if iRacing serves a
        # localised / wrong CarScreenName, CarPath disambiguates.
        ibt = _MockIBT(
            screen_name="BMW M Hybrid V8",   # GTP screen name
            car_path="bmwm4gt3",              # but CarPath says GT3
        )
        identity = resolve_car_from_ibt(ibt)  # type: ignore[arg-type]
        assert identity is not None
        assert identity.canonical == "bmw_m4_gt3"

    def test_falls_back_to_screen_name_when_carpath_empty(self):
        ibt = _MockIBT(screen_name="BMW M Hybrid V8", car_path="")
        identity = resolve_car_from_ibt(ibt)  # type: ignore[arg-type]
        assert identity is not None
        assert identity.canonical == "bmw"

    def test_unknown_carpath_falls_back_to_screen_name(self):
        # A forward-compat scenario: iRacing ships a new GT3 car whose
        # CarPath we don't know yet.  The screen name should still pull
        # the legacy substring resolution path (which may or may not
        # match — but it shouldn't crash).
        ibt = _MockIBT(
            screen_name="Porsche 963",
            car_path="brand_new_car_2030",
        )
        identity = resolve_car_from_ibt(ibt)  # type: ignore[arg-type]
        assert identity is not None
        assert identity.canonical == "porsche"

    def test_unknown_everything_returns_none(self):
        ibt = _MockIBT(screen_name="McLaren 720S GT3", car_path="mclaren720gt3")
        identity = resolve_car_from_ibt(ibt)  # type: ignore[arg-type]
        assert identity is None


# ─── watcher.service._detect_car_and_track ────────────────────────────────

class TestDetectCarAndTrack:
    """`_detect_car_and_track` returns the resolved CarIdentity using CarPath."""

    def _patch_ibt(self, monkeypatch, *, screen_name: str, car_path: str):
        from watcher import service

        mock_ibt = _MockIBT(screen_name=screen_name, car_path=car_path)
        monkeypatch.setattr(
            "track_model.ibt_parser.IBTFile",
            lambda _path: mock_ibt,
        )

    def test_gt3_identity_via_carpath(self, monkeypatch):
        from watcher.service import _detect_car_and_track

        self._patch_ibt(
            monkeypatch,
            screen_name="BMW M4 GT3 EVO",
            car_path="bmwm4gt3",
        )
        screen, path, track, driver, identity = _detect_car_and_track(Path("/x.ibt"))
        assert screen == "BMW M4 GT3 EVO"
        assert path == "bmwm4gt3"
        assert identity is not None
        assert identity.canonical == "bmw_m4_gt3"

    def test_carpath_wins_over_misleading_screen_name(self, monkeypatch):
        from watcher.service import _detect_car_and_track

        self._patch_ibt(
            monkeypatch,
            screen_name="BMW M Hybrid V8",   # GTP screen name
            car_path="bmwm4gt3",              # but stable CarPath says GT3
        )
        _, _, _, _, identity = _detect_car_and_track(Path("/x.ibt"))
        assert identity is not None
        assert identity.canonical == "bmw_m4_gt3"

    def test_gtp_regression(self, monkeypatch):
        from watcher.service import _detect_car_and_track

        self._patch_ibt(
            monkeypatch,
            screen_name="BMW M Hybrid V8",
            car_path="bmwlmdh",
        )
        _, _, _, _, identity = _detect_car_and_track(Path("/x.ibt"))
        assert identity is not None
        assert identity.canonical == "bmw"

    def test_unknown_returns_none_identity(self, monkeypatch):
        from watcher.service import _detect_car_and_track

        self._patch_ibt(
            monkeypatch,
            screen_name="McLaren 720S GT3",
            car_path="mclaren720gt3",
        )
        screen, path, track, driver, identity = _detect_car_and_track(Path("/x.ibt"))
        assert screen == "McLaren 720S GT3"
        assert path == "mclaren720gt3"
        assert identity is None


# ─── desktop.config.AppConfig.class_filter ────────────────────────────────

class TestAppConfigClassFilter:
    """`class_filter` is a new W8.2 field; must round-trip through save/load."""

    def test_default_is_empty_list(self):
        cfg = AppConfig()
        assert cfg.class_filter == []

    def test_set_and_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AppConfig(class_filter=["GT3"])
            cfg.save(Path(d))
            loaded = AppConfig.load(Path(d))
            assert loaded.class_filter == ["GT3"]

    def test_round_trip_multiple_classes(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AppConfig(class_filter=["GT3", "GTP"])
            cfg.save(Path(d))
            loaded = AppConfig.load(Path(d))
            assert sorted(loaded.class_filter) == ["GT3", "GTP"]

    def test_orthogonal_to_car_filter(self):
        # Both filter fields coexist; setting one doesn't disturb the other.
        with tempfile.TemporaryDirectory() as d:
            cfg = AppConfig(
                car_filter=["bmw_m4_gt3"],
                class_filter=["GT3"],
            )
            cfg.save(Path(d))
            loaded = AppConfig.load(Path(d))
            assert loaded.car_filter == ["bmw_m4_gt3"]
            assert loaded.class_filter == ["GT3"]

    def test_load_legacy_config_without_class_filter(self):
        # Pre-W8.2 config files do not contain the class_filter key;
        # AppConfig.load must default it to [] without crashing.
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({
                "team_server_url": "https://example.test",
                "api_key": "deadbeef",
                "car_filter": ["bmw"],
            }))
            loaded = AppConfig.load(Path(d))
            assert loaded.team_server_url == "https://example.test"
            assert loaded.car_filter == ["bmw"]
            assert loaded.class_filter == []


# ─── WatcherService.class_filter dispatch ─────────────────────────────────

class TestWatcherServiceClassFilter:
    """`class_filter` causes the watcher to skip out-of-class IBTs."""

    def test_constructor_accepts_class_filter(self):
        from watcher.service import WatcherService

        # Construct against a tmp dir (no monitor.start() — just ctor).
        with tempfile.TemporaryDirectory() as d:
            svc = WatcherService(
                telemetry_dir=Path(d),
                class_filter=["GT3"],
            )
            assert svc._class_filter == {"GT3"}

    def test_constructor_normalises_class_filter_case(self):
        from watcher.service import WatcherService

        with tempfile.TemporaryDirectory() as d:
            svc = WatcherService(
                telemetry_dir=Path(d),
                class_filter=["gt3", "GtP"],
            )
            assert svc._class_filter == {"GT3", "GTP"}

    def test_class_filter_none_means_all(self):
        from watcher.service import WatcherService

        with tempfile.TemporaryDirectory() as d:
            svc = WatcherService(telemetry_dir=Path(d))
            assert svc._class_filter is None
