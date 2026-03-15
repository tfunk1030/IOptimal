"""Learner bridge: local knowledge store + team database synchronization."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from learner.delta_detector import detect_delta
from learner.empirical_models import fit_models
from learner.ingest import _run_analyzer
from learner.knowledge_store import KnowledgeStore
from learner.observation import Observation, build_observation

from api.models.database import DatabaseGateway


class LearnerBridgeService:
    """Mirror local observation/delta/model artifacts into Supabase tables."""

    def __init__(self, db: DatabaseGateway) -> None:
        self.db = db
        self.local_store = KnowledgeStore()

    def ingest_session(
        self,
        *,
        session_id: str,
        ibt_path: str,
        car: str,
        team_id: str,
        driver_id: str,
        wing: float | None = None,
        lap: int | None = None,
    ) -> dict[str, Any]:
        """Run analyzer/observation and synchronize observations, deltas, and models."""
        track, measured, setup, driver, diag, corners, _ibt = _run_analyzer(car, ibt_path, wing=wing, lap=lap)
        obs_obj = build_observation(
            session_id=session_id,
            ibt_path=ibt_path,
            car_name=car,
            track_profile=track,
            measured_state=measured,
            current_setup=setup,
            driver_profile_obj=driver,
            diagnosis_obj=diag,
            corners=corners,
        )
        obs_dict = obs_obj.to_dict()
        self.local_store.save_observation(session_id, obs_dict)

        observation_row = {
            "session_id": session_id,
            "driver_id": driver_id,
            "team_id": team_id,
            "car": car,
            "track": track.track_name,
            "data": obs_dict,
            "driver_style": obs_dict.get("driver_profile"),
            "diagnosis": obs_dict.get("diagnosis"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.db.insert("observations", observation_row)

        delta_row = self._sync_driver_delta(
            driver_id=driver_id,
            team_id=team_id,
            car=car,
            track=track.track_name,
        )
        models = self._sync_models(team_id=team_id, car=car, track=track.track_name)
        return {"observation": observation_row, "delta": delta_row, "models": models}

    def _sync_driver_delta(
        self,
        *,
        driver_id: str,
        team_id: str,
        car: str,
        track: str,
    ) -> dict[str, Any] | None:
        rows = self.db.select(
            "observations",
            filters={"driver_id": driver_id, "team_id": team_id, "car": car},
            ilike={"track": f"{track}%"},
            order_by="created_at",
            ascending=True,
        )
        if len(rows) < 2:
            return None

        before = Observation.from_dict(rows[-2]["data"])
        after = Observation.from_dict(rows[-1]["data"])
        delta = detect_delta(before, after)
        delta_dict = delta.to_dict()
        row = {
            "driver_id": driver_id,
            "team_id": team_id,
            "car": car,
            "track": track,
            "from_session": before.session_id,
            "to_session": after.session_id,
            "data": delta_dict,
            "causal_confidence": self._confidence_to_score(delta_dict.get("confidence_level")),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.db.insert("deltas", row)
        self.local_store.save_delta(f"{car}_{track.lower().split()[0]}_{before.session_id}_{after.session_id}", delta_dict)
        return row

    def _sync_models(self, *, team_id: str, car: str, track: str) -> list[dict[str, Any]]:
        observations = self.db.select(
            "observations",
            filters={"team_id": team_id, "car": car},
            ilike={"track": f"{track}%"},
        )
        deltas = self.db.select(
            "deltas",
            filters={"team_id": team_id, "car": car},
            ilike={"track": f"{track}%"},
        )
        by_driver: dict[str, list[dict[str, Any]]] = {}
        for row in observations:
            by_driver.setdefault(row["driver_id"], []).append(row["data"])
        delta_by_driver: dict[str, list[dict[str, Any]]] = {}
        for row in deltas:
            delta_by_driver.setdefault(row["driver_id"], []).append(row["data"])

        upserted: list[dict[str, Any]] = []
        existing_models = self.db.select(
            "models",
            filters={"team_id": team_id, "car": car, "track": track, "model_type": "empirical"},
        )
        for driver_id, obs in by_driver.items():
            model = fit_models(obs, delta_by_driver.get(driver_id, []), car, track).to_dict()
            row = {
                "driver_id": driver_id,
                "team_id": team_id,
                "car": car,
                "track": track,
                "model_type": "empirical",
                "data": model,
                "session_count": len(obs),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            existing = next((m for m in existing_models if m.get("driver_id") == driver_id), None)
            if existing:
                self.db.update("models", {"id": existing["id"]}, row)
                existing.update(row)
                upserted.append(existing)
            else:
                upserted.append(self.db.insert("models", row))

        team_model = fit_models([r["data"] for r in observations], [r["data"] for r in deltas], car, track).to_dict()
        team_row = {
            "driver_id": None,
            "team_id": team_id,
            "car": car,
            "track": track,
            "model_type": "empirical",
            "data": team_model,
            "session_count": len(observations),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        existing_team = next((m for m in existing_models if not m.get("driver_id")), None)
        if existing_team:
            self.db.update("models", {"id": existing_team["id"]}, team_row)
            existing_team.update(team_row)
            upserted.append(existing_team)
        else:
            upserted.append(self.db.insert("models", team_row))

        track_key = track.lower().split()[0]
        self.local_store.save_model(f"{car}_{track_key}_empirical", team_model)
        return upserted

    @staticmethod
    def _confidence_to_score(level: str | None) -> float:
        if level == "high":
            return 0.9
        if level == "medium":
            return 0.6
        if level == "low":
            return 0.35
        return 0.2
