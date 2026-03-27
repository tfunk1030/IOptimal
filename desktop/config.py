"""Desktop app configuration — persistent user settings.

Stores configuration in a JSON file at the platform-appropriate location:
- Windows: %APPDATA%/IOptimal/config.json
- Linux/Mac: ~/.config/ioptimal/config.json
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _default_config_dir() -> Path:
    """Return the platform-appropriate config directory."""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(base) / "IOptimal"
    elif system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "IOptimal"
    else:
        return Path.home() / ".config" / "ioptimal"


def _default_telemetry_dir() -> str:
    """Best-guess iRacing telemetry directory."""
    if platform.system() == "Windows":
        home = Path(os.environ.get("USERPROFILE", str(Path.home())))
        return str(home / "Documents" / "iRacing" / "Telemetry")
    return str(Path.home() / "Documents" / "iRacing" / "Telemetry")


@dataclass
class AppConfig:
    """User configuration for the IOptimal desktop app."""

    # Team server
    team_server_url: str = ""
    api_key: str = ""
    team_name: str = ""
    member_name: str = ""
    invite_code: str = ""
    iracing_name: str = ""

    # Telemetry
    telemetry_dir: str = field(default_factory=_default_telemetry_dir)
    auto_ingest: bool = True
    auto_sync: bool = True

    # Sync intervals (minutes)
    push_interval: int = 5
    pull_interval: int = 5

    # Filtering
    car_filter: list[str] = field(default_factory=list)  # empty = all cars

    # UI
    sound_enabled: bool = True
    browser_open_on_start: bool = True
    webapp_port: int = 8000

    # State
    first_run_complete: bool = False
    bulk_import_done: bool = False

    @property
    def team_connected(self) -> bool:
        """Whether we have a valid team connection (API key set)."""
        return bool(self.team_server_url and self.api_key)

    def save(self, config_dir: Path | None = None) -> None:
        """Persist config to disk."""
        d = config_dir or _default_config_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / "config.json"
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, config_dir: Path | None = None) -> AppConfig:
        """Load config from disk, or return defaults if not found."""
        d = config_dir or _default_config_dir()
        path = d / "config.json"
        if not path.exists():
            return cls()
        try:
            with open(path) as f:
                data = json.load(f)
            # Only use known fields
            known = {k for k in cls.__dataclass_fields__}
            filtered = {k: v for k, v in data.items() if k in known}
            return cls(**filtered)
        except (json.JSONDecodeError, TypeError):
            return cls()

    @property
    def is_team_configured(self) -> bool:
        return bool(self.team_server_url and self.api_key)
