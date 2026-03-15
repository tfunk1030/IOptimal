"""Team-level read models and watcher sync payloads."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api.models.database import DatabaseGateway


class TeamKnowledgeService:
    def __init__(self, db: DatabaseGateway) -> None:
        self.db = db

    def get_team_knowledge(self, *, team_id: str, car: str, track: str) -> dict[str, Any]:
        obs = self.db.select(
            "observations",
            filters={"team_id": team_id, "car": car},
            ilike={"track": f"{track}%"},
        )
        models = self.db.select(
            "models",
            filters={"team_id": team_id, "car": car, "model_type": "empirical"},
            ilike={"track": f"{track}%"},
            order_by="updated_at",
            ascending=False,
        )
        team_model = next((m for m in models if not m.get("driver_id")), None)
        individual = [m for m in models if m.get("driver_id")]
        driver_groups = defaultdict(list)
        for row in obs:
            driver_groups[row["driver_id"]].append(row["data"])

        return {
            "session_count": len(obs),
            "drivers": [
                {
                    "driver_id": driver_id,
                    "session_count": len(items),
                    "latest_lap_time": items[-1].get("performance", {}).get("best_lap_time_s"),
                    "style": items[-1].get("driver_profile", {}).get("style"),
                }
                for driver_id, items in driver_groups.items()
            ],
            "individual_models": individual,
            "team_model": team_model,
            "recurring_issues": self._find_recurring_issues(obs),
        }

    def get_sync_payload(self, *, team_id: str, driver_id: str, car: str, track: str) -> dict[str, Any]:
        driver_obs = self.db.select(
            "observations",
            filters={"team_id": team_id, "driver_id": driver_id, "car": car},
            ilike={"track": f"{track}%"},
        )
        driver_session_count = len(driver_obs)
        models = self.db.select(
            "models",
            filters={"team_id": team_id, "car": car, "model_type": "empirical"},
            ilike={"track": f"{track}%"},
            order_by="updated_at",
            ascending=False,
        )
        driver_model = next((m for m in models if m.get("driver_id") == driver_id), None)
        team_model = next((m for m in models if not m.get("driver_id")), None)
        use_team_fallback = driver_session_count < 3
        chosen = team_model if use_team_fallback else (driver_model or team_model)
        fallback_mode = "team" if use_team_fallback else "driver"

        snapshot = {
            "car": car,
            "track": track,
            "fallback_mode": fallback_mode,
            "driver_session_count": driver_session_count,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model_data": deepcopy((chosen or {}).get("data") or {}),
        }
        return {
            "car": car,
            "track": track,
            "fallback_mode": fallback_mode,
            "driver_session_count": driver_session_count,
            "learnings_snapshot": snapshot,
        }

    @staticmethod
    def write_snapshot_to_learnings(snapshot: dict[str, Any], base_dir: Path) -> Path:
        """Write watcher sync payload into knowledge-store model file format."""
        car = snapshot["car"]
        track = snapshot["track"]
        track_key = track.lower().split()[0]
        model_file = base_dir / "models" / f"{car}_{track_key}_empirical.json"
        model_file.parent.mkdir(parents=True, exist_ok=True)
        payload = snapshot.get("model_data") or {}
        model_file.write_text(__import__("json").dumps(payload, indent=2), encoding="utf-8")
        return model_file

    @staticmethod
    def _find_recurring_issues(obs_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for row in obs_rows:
            problems = (row.get("diagnosis") or {}).get("problems", [])
            for item in problems:
                symptom = item.get("symptom", "unknown")
                counts[symptom] = counts.get(symptom, 0) + 1
        threshold = max(2, int(len(obs_rows) * 0.35))
        recurring = [{"symptom": key, "count": value} for key, value in counts.items() if value >= threshold]
        recurring.sort(key=lambda x: x["count"], reverse=True)
        return recurring


def compare_sessions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute basic side-by-side diffs for compare endpoint."""
    setup_diff: list[dict[str, Any]] = []
    style_diff: list[dict[str, Any]] = []
    performance_diff: list[dict[str, Any]] = []

    if len(rows) < 2:
        return {"sessions": rows, "setup_diff": setup_diff, "style_diff": style_diff, "performance_diff": performance_diff}

    baseline = rows[0]
    base_results = baseline.get("results") or {}
    base_setup = (base_results.get("step1_rake") or {}) | (base_results.get("step2_heave") or {})

    for row in rows[1:]:
        result = row.get("results") or {}
        setup = (result.get("step1_rake") or {}) | (result.get("step2_heave") or {})
        for key, base_value in base_setup.items():
            value = setup.get(key)
            if value is None or base_value is None or value == base_value:
                continue
            setup_diff.append(
                {
                    "session_id": row["id"],
                    "parameter": key,
                    "baseline": base_value,
                    "value": value,
                    "delta": (value - base_value) if isinstance(value, (int, float)) and isinstance(base_value, (int, float)) else None,
                }
            )
        style_diff.append(
            {
                "session_id": row["id"],
                "baseline_style": base_results.get("driver_style"),
                "style": result.get("driver_style"),
            }
        )
        performance_diff.append(
            {
                "session_id": row["id"],
                "baseline_lap_time_s": base_results.get("lap_time_s"),
                "lap_time_s": result.get("lap_time_s"),
            }
        )
    return {"sessions": rows, "setup_diff": setup_diff, "style_diff": style_diff, "performance_diff": performance_diff}
