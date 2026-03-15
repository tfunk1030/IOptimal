"""Watcher configuration persisted under %APPDATA%/iOptimal/config.json."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def default_telemetry_dir() -> str:
    return str(Path.home() / "Documents" / "iRacing" / "telemetry")


def config_path() -> Path:
    appdata = Path(os.getenv("APPDATA", str(Path.home() / ".config")))
    base = appdata / "iOptimal"
    base.mkdir(parents=True, exist_ok=True)
    return base / "config.json"


@dataclass
class WatcherConfig:
    server_url: str = "http://localhost:8000"
    dashboard_url: str = "http://localhost:5173"
    email: str = ""
    default_car: str = "bmw"
    telemetry_folder: str = default_telemetry_dir()
    paused: bool = False
    access_token: str = ""
    refresh_token: str = ""
    driver_id: str = ""
    team_id: str = ""

    @classmethod
    def load(cls) -> "WatcherConfig":
        path = config_path()
        if not path.exists():
            cfg = cls()
            cfg.save()
            return cfg
        data = json.loads(path.read_text(encoding="utf-8"))
        defaults = asdict(cls())
        defaults.update(data)
        return cls(**defaults)

    def save(self) -> None:
        path = config_path()
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @property
    def runtime_dir(self) -> Path:
        path = config_path().parent / "runtime"
        path.mkdir(parents=True, exist_ok=True)
        return path
