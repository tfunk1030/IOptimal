"""Desktop app configuration — persistent user settings.

Stores configuration in a JSON file at the platform-appropriate location:
- Windows: %APPDATA%/IOptimal/config.json
- Linux/Mac: ~/.config/ioptimal/config.json
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Hex / base64-url alphabet, length-only validation. The server generates
# 32+ char hex API keys; reject anything visibly malformed but don't reach
# out to the server to validate.
_API_KEY_RE = re.compile(r"^[A-Za-z0-9_\-+/=]{32,}$")


try:
    import fcntl as _fcntl  # type: ignore[import-not-found]
except ImportError:
    _fcntl = None  # type: ignore[assignment]

try:
    import msvcrt as _msvcrt  # type: ignore[import-not-found]
except ImportError:
    _msvcrt = None  # type: ignore[assignment]


@contextmanager
def _file_lock(fh):
    """Best-effort cross-platform exclusive lock on an open file handle.

    Falls back to a no-op if neither fcntl nor msvcrt is available (e.g.
    exotic test environments) so concurrent-save tests still pass without
    crashing the app.
    """
    acquired = None
    try:
        if _fcntl is not None:
            try:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)
                acquired = "fcntl"
            except OSError as exc:
                logger.warning("Config file lock acquisition failed: %s", exc)
        elif _msvcrt is not None:
            try:
                _msvcrt.locking(fh.fileno(), _msvcrt.LK_LOCK, 1)
                acquired = "msvcrt"
            except OSError as exc:
                logger.warning("Config file lock acquisition failed: %s", exc)
        yield
    finally:
        if acquired == "fcntl" and _fcntl is not None:
            try:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
            except OSError:
                pass
        elif acquired == "msvcrt" and _msvcrt is not None:
            try:
                _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, 1)
            except OSError:
                pass


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

    def save(self, config_dir: Path | None = None) -> None:
        """Persist config to disk atomically.

        Writes to a temp file in the same directory, fsyncs, then atomically
        replaces the target. An OS-level file lock guards against concurrent
        saves from a second process.
        """
        d = config_dir or _default_config_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / "config.json"
        lock_path = d / "config.lock"
        payload = json.dumps(asdict(self), indent=2)

        with open(lock_path, "a+") as lock_fh:
            with _file_lock(lock_fh):
                tmp_path = path.with_suffix(path.suffix + ".tmp")
                with open(tmp_path, "w") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, path)

    @classmethod
    def load(cls, config_dir: Path | None = None) -> AppConfig:
        """Load config from disk, returning defaults if missing or invalid.

        Validates fields against safe defaults and logs a warning on any
        rejected value rather than crashing.
        """
        d = config_dir or _default_config_dir()
        path = d / "config.json"
        if not path.exists():
            return cls()
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Config file unreadable (%s); using defaults", exc)
            return cls()

        known = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        try:
            instance = cls(**filtered)
        except TypeError as exc:
            logger.warning("Config schema mismatch (%s); using defaults", exc)
            return cls()

        instance._validate_and_repair()
        return instance

    def _validate_and_repair(self) -> None:
        """Reset invalid fields to safe defaults; warn on each repair."""
        if self.telemetry_dir and not Path(self.telemetry_dir).is_dir():
            logger.warning(
                "Configured telemetry_dir does not exist: %s", self.telemetry_dir
            )

        if self.team_server_url:
            parsed = urlparse(self.team_server_url)
            if not (parsed.scheme and parsed.netloc):
                logger.warning(
                    "Invalid team_server_url %r; clearing", self.team_server_url
                )
                self.team_server_url = ""

        if self.api_key and not _API_KEY_RE.match(self.api_key):
            logger.warning("Invalid api_key format; clearing")
            self.api_key = ""

    @property
    def is_team_configured(self) -> bool:
        return bool(self.team_server_url and self.api_key)
