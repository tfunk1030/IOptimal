from supabase import Client
import sys
from api.config import settings
import json
import os

if settings.ioptimal_solver_path not in sys.path:
    sys.path.append(settings.ioptimal_solver_path)

from learner.knowledge_store import KnowledgeStore

class TeamKnowledgeService:
    """Bridges local KnowledgeStore with team Supabase database."""

    def __init__(self, db: Client):
        self.db = db
        self.local_store = KnowledgeStore()

    async def ingest_session(self, observation: dict, session_id: str, driver_id: str, car: str, track: str, driver_style: dict = None, diagnosis: dict = None):
        """Store observation both locally (for solver) and in Supabase (for team)."""
        # Local store (solver needs this)
        self.local_store.store_observation(observation)

        # Supabase (team needs this)
        # Handle dict serialization for pydantic models if they aren't already dicts
        def to_dict(obj):
            if hasattr(obj, "dict"):
                return obj.dict()
            return obj
            
        obs_dict = to_dict(observation)
        style_dict = to_dict(driver_style) if driver_style else None
        diag_dict = to_dict(diagnosis) if diagnosis else None

        self.db.table("observations").insert({
            "session_id": session_id,
            "driver_id": driver_id,
            "car": car,
            "track": track,
            "data": obs_dict,
            "driver_style": style_dict,
            "diagnosis": diag_dict,
        }).execute()

    async def sync_models_to_db(self, car: str, track: str, driver_id: str, team_id: str):
        """Sync locally fitted models back to Supabase."""
        # This reads the local store and pushes to DB. 
        # Here we just fetch the model file for this car/track.
        model_path = os.path.join(self.local_store.models_dir, f"{car}_{track}_model.json")
        if os.path.exists(model_path):
            with open(model_path, "r") as f:
                model_data = json.load(f)
                
            # Upsert into Supabase for this driver
            self.db.table("models").upsert({
                "driver_id": driver_id,
                "team_id": team_id,
                "car": car,
                "track": track,
                "model_type": "empirical",
                "data": model_data,
                "session_count": model_data.get("n_observations", 1)
            }, on_conflict="driver_id, car, track, model_type").execute()
            
    async def get_team_knowledge(self, car: str, track: str) -> dict:
        """Query team-wide knowledge for a car/track combo."""
        # All observations for this car/track across all team drivers
        obs = self.db.table("observations") \
            .select("*") \
            .eq("car", car) \
            .ilike("track", f"{track}%") \
            .execute()

        # Individual driver models
        individual = self.db.table("models") \
            .select("*") \
            .eq("car", car) \
            .ilike("track", f"{track}%") \
            .is_("driver_id", "not.null") \
            .execute()

        # Team aggregate model
        aggregate = self.db.table("models") \
            .select("*") \
            .eq("car", car) \
            .ilike("track", f"{track}%") \
            .is_("driver_id", "null") \
            .execute()

        return {
            "session_count": len(obs.data) if obs.data else 0,
            "drivers": self._group_by_driver(obs.data),
            "individual_models": individual.data,
            "team_model": aggregate.data[0] if aggregate.data else None,
            "recurring_issues": self._find_recurring_issues(obs.data),
        }
        
    def _group_by_driver(self, observations):
        if not observations:
            return []
        drivers = set(o.get("driver_id") for o in observations)
        return list(drivers)
        
    def _find_recurring_issues(self, observations):
        if not observations:
            return []
        issues = {}
        for o in observations:
            diag = o.get("diagnosis", {})
            if not diag: continue
            for corner, severity in diag.get("issues", {}).items():
                issues[corner] = issues.get(corner, 0) + 1
        
        # Sort by frequency
        return sorted([{"corner": k, "count": v} for k, v in issues.items()], key=lambda x: x["count"], reverse=True)
