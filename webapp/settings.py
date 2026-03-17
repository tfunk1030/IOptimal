"""Application settings and local storage paths for the web UI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


APP_DATA_ENV = "IOPTIMAL_APP_DATA_DIR"


def _default_app_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "IOptimal"
    return Path.cwd() / ".ioptimal_app"


@dataclass(frozen=True)
class AppSettings:
    """Resolved filesystem paths and user-configurable settings."""

    title: str
    app_data_dir: Path
    uploads_dir: Path
    artifacts_dir: Path
    reports_dir: Path
    cache_dir: Path
    database_path: Path

    @classmethod
    def from_env(cls, app_data_dir: str | Path | None = None) -> "AppSettings":
        root_value = app_data_dir or os.environ.get(APP_DATA_ENV) or _default_app_data_dir()
        root = Path(root_value).expanduser().resolve()
        return cls(
            title="IOptimal",
            app_data_dir=root,
            uploads_dir=root / "uploads",
            artifacts_dir=root / "artifacts",
            reports_dir=root / "reports",
            cache_dir=root / "cache",
            database_path=root / "ioptimal_web.sqlite3",
        )

    def ensure_directories(self) -> None:
        for path in (
            self.app_data_dir,
            self.uploads_dir,
            self.artifacts_dir,
            self.reports_dir,
            self.cache_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def upload_dir_for(self, run_id: str) -> Path:
        return self.uploads_dir / run_id

    def artifact_dir_for(self, run_id: str) -> Path:
        return self.artifacts_dir / run_id

    def report_path_for(self, run_id: str, stem: str = "report") -> Path:
        return self.reports_dir / f"{run_id}_{stem}.txt"
