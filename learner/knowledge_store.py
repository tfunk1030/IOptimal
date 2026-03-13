"""Persistent knowledge store for accumulated learnings.

Stores observations, empirical models, and derived insights as JSON files
in a structured directory. Each car/track combination gets its own namespace.

Directory structure:
    data/learnings/
        index.json                          — Master index of all sessions ingested
        observations/
            bmw_sebring_2026-03-06_17-38.json   — One per session
            bmw_sebring_2026-03-11_19-21.json
        deltas/
            bmw_sebring_delta_001.json          — Session-to-session changes
        models/
            bmw_sebring_empirical.json          — Fitted empirical models
            bmw_global_empirical.json           — Cross-track model for BMW
        insights/
            bmw_sebring_insights.json           — Human-readable distilled insights
        calibration_updates/
            bmw_calibration_history.json        — Proposed model corrections over time
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LEARNINGS_DIR = Path(__file__).parent.parent / "data" / "learnings"


class KnowledgeStore:
    """Read/write interface to the learnings directory."""

    def __init__(self, base_dir: Path | None = None):
        self.base = base_dir or LEARNINGS_DIR
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for sub in ["observations", "deltas", "models", "insights",
                     "calibration_updates"]:
            (self.base / sub).mkdir(parents=True, exist_ok=True)

    # ── Index ─────────────────────────────────────────────────────

    def _index_path(self) -> Path:
        return self.base / "index.json"

    def load_index(self) -> dict:
        p = self._index_path()
        if p.exists():
            return json.loads(p.read_text())
        return {
            "version": 1,
            "created": datetime.now(timezone.utc).isoformat(),
            "sessions": [],
            "total_observations": 0,
            "total_deltas": 0,
            "cars_seen": [],
            "tracks_seen": [],
        }

    def save_index(self, idx: dict) -> None:
        idx["last_updated"] = datetime.now(timezone.utc).isoformat()
        self._index_path().write_text(json.dumps(idx, indent=2))

    # ── Observations ──────────────────────────────────────────────

    def observation_path(self, session_id: str) -> Path:
        return self.base / "observations" / f"{session_id}.json"

    def save_observation(self, session_id: str, obs: dict) -> Path:
        p = self.observation_path(session_id)
        p.write_text(json.dumps(obs, indent=2))
        return p

    def load_observation(self, session_id: str) -> dict | None:
        p = self.observation_path(session_id)
        if p.exists():
            return json.loads(p.read_text())
        return None

    def has_observation(self, session_id: str) -> bool:
        return self.observation_path(session_id).exists()

    def list_observations(self, car: str = "", track: str = "") -> list[dict]:
        """List all observations, optionally filtered by car/track."""
        results = []
        for f in sorted((self.base / "observations").glob("*.json")):
            obs = json.loads(f.read_text())
            if car and obs.get("car", "").lower() != car.lower():
                continue
            if track and track.lower() not in obs.get("track", "").lower():
                continue
            results.append(obs)
        return results

    # ── Deltas ────────────────────────────────────────────────────

    def save_delta(self, delta_id: str, delta: dict) -> Path:
        p = self.base / "deltas" / f"{delta_id}.json"
        p.write_text(json.dumps(delta, indent=2))
        return p

    def list_deltas(self, car: str = "", track: str = "") -> list[dict]:
        results = []
        for f in sorted((self.base / "deltas").glob("*.json")):
            d = json.loads(f.read_text())
            if car and d.get("car", "").lower() != car.lower():
                continue
            if track and track.lower() not in d.get("track", "").lower():
                continue
            results.append(d)
        return results

    # ── Empirical Models ──────────────────────────────────────────

    def model_path(self, model_id: str) -> Path:
        return self.base / "models" / f"{model_id}.json"

    def save_model(self, model_id: str, model: dict) -> Path:
        p = self.model_path(model_id)
        p.write_text(json.dumps(model, indent=2))
        return p

    def load_model(self, model_id: str) -> dict | None:
        p = self.model_path(model_id)
        if p.exists():
            return json.loads(p.read_text())
        return None

    # ── Insights ──────────────────────────────────────────────────

    def save_insights(self, insight_id: str, insights: dict) -> Path:
        p = self.base / "insights" / f"{insight_id}.json"
        p.write_text(json.dumps(insights, indent=2))
        return p

    def load_insights(self, insight_id: str) -> dict | None:
        p = self.base / "insights" / f"{insight_id}.json"
        if p.exists():
            return json.loads(p.read_text())
        return None

    # ── Calibration Updates ───────────────────────────────────────

    def save_calibration_update(self, car: str, update: dict) -> Path:
        p = self.base / "calibration_updates" / f"{car}_calibration_history.json"
        history = []
        if p.exists():
            history = json.loads(p.read_text())
        history.append(update)
        p.write_text(json.dumps(history, indent=2))
        return p

    def load_calibration_history(self, car: str) -> list[dict]:
        p = self.base / "calibration_updates" / f"{car}_calibration_history.json"
        if p.exists():
            return json.loads(p.read_text())
        return []

    # ── Utility ───────────────────────────────────────────────────

    @staticmethod
    def session_id_from_ibt(ibt_path: str, car: str, track: str) -> str:
        """Generate a deterministic session ID from the IBT path."""
        name = Path(ibt_path).stem
        # e.g. "bmw_sebring_2026-03-06_17-38-43" or just the filename stem
        return f"{car}_{track}_{name}".lower().replace(" ", "_")

    def session_count(self, car: str = "", track: str = "") -> int:
        return len(self.list_observations(car, track))
