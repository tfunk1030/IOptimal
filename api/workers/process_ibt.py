import json
import os
import tempfile
from supabase import Client
import sys
import traceback

# Import the existing solver pipeline
# Make sure the solver path is in sys.path
from api.config import settings
if settings.ioptimal_solver_path not in sys.path:
    sys.path.append(settings.ioptimal_solver_path)

from pipeline.produce import produce
from api.services.solver_service import make_produce_args
from learner.ingest import _run_analyzer
from learner.observation import build_observation
from learner.empirical_models import fit_models

async def process_ibt(session_id: str, ibt_path: str, car: str,
                      wing: float, driver_id: str, team_id: str, db: Client):
    """Background task: run solver pipeline on uploaded IBT."""
    try:
        # 1. Update session status
        db.table("sessions").update({"status": "processing"}).eq("id", session_id).execute()

        # Temporary files for output
        json_path = os.path.join(tempfile.gettempdir(), f"{session_id}.json")
        sto_path = os.path.join(tempfile.gettempdir(), f"{session_id}.sto")

        # 2. Run solver (synchronous - this runs in thread pool via FastAPI BackgroundTasks usually, 
        #    but we can run it synchronously here if invoked properly)
        args = make_produce_args(
            car=car, ibt_path=ibt_path, wing=wing,
            json_path=json_path,
            sto_path=sto_path,
            learn=True, auto_learn=True,
        )
        
        produce(args)

        # 3. Read solver output
        with open(json_path) as f:
            solver_output = json.load(f)

        # 4. Run learner ingestion to get observation
        # Extract necessary details from the output or analyzer
        track, measured, setup, driver_style, diagnosis, corners, ibt = _run_analyzer(ibt_path, car, wing, None, None)
        observation = build_observation(track, measured, setup, driver_style, diagnosis, corners)

        # Import the team service to save to local + supabase
        from api.services.team_service import TeamKnowledgeService
        team_service = TeamKnowledgeService(db)
        await team_service.ingest_session(observation, session_id, driver_id, car, track.name, driver_style, diagnosis)

        # 5. Build team-aggregate models
        # Trigger fit_models locally (which updates local KnowledgeStore)
        fit_models(car, track.name)

        # And we could push the new models to Supabase via team_service if needed.
        # (Assuming team_service handles syncing local models back to DB)
        await team_service.sync_models_to_db(car, track.name, driver_id, team_id)

        # 6. Upload .sto to Supabase Storage
        with open(sto_path, "rb") as f:
            sto_bytes = f.read()
        
        db.storage.from_("sto-files").upload(f"{session_id}.sto", sto_bytes)

        # 7. Store results in Supabase
        db.table("sessions").update({
            "status": "complete",
            "results": solver_output,
            "sto_storage_path": f"sto-files/{session_id}.sto",
        }).eq("id", session_id).execute()

    except Exception as e:
        error_trace = traceback.format_exc()
        db.table("sessions").update({
            "status": "error", "error": str(e) + "\\n" + error_trace
        }).eq("id", session_id).execute()
        print(f"Error processing session {session_id}: {e}")
