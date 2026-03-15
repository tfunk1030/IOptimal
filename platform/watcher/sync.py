"""Team knowledge sync for watcher pre-solve fallback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from watcher.config import WatcherConfig


def _track_key(track: str) -> str:
    return track.lower().split()[0]


class TeamKnowledgeSyncClient:
    def __init__(self, config: WatcherConfig) -> None:
        self.config = config
        self.client = httpx.Client(timeout=30)

    def fetch_sync_payload(self, *, car: str, track: str) -> dict[str, Any]:
        response = self.client.get(
            f"{self.config.server_url.rstrip('/')}/api/team/sync-learnings",
            params={"car": car, "track": track},
            headers={"Authorization": f"Bearer {self.config.access_token}"},
        )
        response.raise_for_status()
        return response.json()

    def write_local_learnings(self, payload: dict[str, Any], solver_root: Path) -> Path:
        snapshot = payload.get("learnings_snapshot") or {}
        track = payload["track"]
        car = payload["car"]
        model = snapshot.get("model_data") or {}

        learnings_dir = solver_root / "data" / "learnings"
        models_dir = learnings_dir / "models"
        models_dir.mkdir(parents=True, exist_ok=True)

        model_path = models_dir / f"{car}_{_track_key(track)}_empirical.json"
        model_path.write_text(json.dumps(model, indent=2), encoding="utf-8")

        index = {
            "version": 1,
            "sessions": [],
            "total_observations": payload.get("driver_session_count", 0),
            "total_deltas": 0,
            "cars_seen": [car],
            "tracks_seen": [track],
            "last_sync_mode": payload.get("fallback_mode"),
        }
        (learnings_dir / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
        return model_path

