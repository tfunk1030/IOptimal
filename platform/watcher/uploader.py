"""HTTP client to upload raw IBT + local solver artifacts in one request."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from watcher.config import WatcherConfig


@dataclass
class UploadResult:
    session_id: str
    status: str
    response: dict[str, Any]


class IBTUploader:
    def __init__(self, config: WatcherConfig) -> None:
        self.config = config
        self.client = httpx.Client(timeout=300)

    def upload(
        self,
        *,
        ibt_path: Path,
        solver_json_path: Path,
        solver_sto_path: Path,
        car: str,
        wing: float | None,
        lap: int | None,
    ) -> UploadResult:
        with (
            ibt_path.open("rb") as ibt_file,
            solver_json_path.open("rb") as solver_json_file,
            solver_sto_path.open("rb") as solver_sto_file,
        ):
            files = {
                "file": (ibt_path.name, ibt_file, "application/octet-stream"),
                "solver_json": (solver_json_path.name, solver_json_file, "application/json"),
                "solver_sto": (solver_sto_path.name, solver_sto_file, "application/octet-stream"),
            }
            data = {
                "car": car,
                "wing": "" if wing is None else str(wing),
                "lap": "" if lap is None else str(lap),
                "driver_id": self.config.driver_id,
            }
            response = self.client.post(
                f"{self.config.server_url.rstrip('/')}/api/upload-ibt",
                files=files,
                data=data,
                headers={"Authorization": f"Bearer {self.config.access_token}"},
            )
            response.raise_for_status()
            payload = response.json()
            return UploadResult(
                session_id=payload["session_id"],
                status=payload.get("status", "processing"),
                response=payload,
            )

