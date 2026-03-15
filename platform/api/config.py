"""Runtime configuration for the iOptimal platform API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    supabase_url: str = os.getenv("SUPABASE_URL", "").strip()
    supabase_anon_key: str = os.getenv("SUPABASE_ANON_KEY", "").strip()
    supabase_service_role_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    api_port: int = int(os.getenv("IOPTIMAL_API_PORT", "8000"))
    solver_path: str = os.getenv("IOPTIMAL_SOLVER_PATH", ".").strip()
    cors_origins: str = os.getenv("IOPTIMAL_CORS_ORIGINS", "*").strip()
    max_upload_mb: int = int(os.getenv("IOPTIMAL_MAX_UPLOAD_MB", "512"))
    data_root: str = os.getenv("IOPTIMAL_DATA_ROOT", "platform/.runtime").strip()
    dev_local_open_auth: bool = _env_bool("IOPTIMAL_DEV_LOCAL_OPEN_AUTH", True)

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def runtime_root(self) -> Path:
        root = Path(self.data_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    @property
    def upload_dir(self) -> Path:
        p = self.runtime_root / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def artifact_dir(self) -> Path:
        p = self.runtime_root / "artifacts"
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()

