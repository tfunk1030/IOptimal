"""Background job orchestration for uploaded session processing."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from api.config import settings
from api.models.database import DatabaseGateway
from api.services.learner_service import LearnerBridgeService
from api.services.solver_service import run_solver


@dataclass
class ProcessJob:
    session_id: str
    team_id: str
    driver_id: str
    car: str
    wing: float | None
    lap: int | None
    ibt_path: Path
    ibt_storage_path: str
    solver_json_path: Path | None = None
    solver_sto_path: Path | None = None


async def process_ibt(job: ProcessJob, db: DatabaseGateway) -> None:
    """Process an uploaded session to completion."""
    db.update("sessions", {"id": job.session_id}, {"status": "processing"})
    learner = LearnerBridgeService(db)

    try:
        if job.solver_json_path and job.solver_sto_path and job.solver_json_path.exists() and job.solver_sto_path.exists():
            solver_output = json.loads(job.solver_json_path.read_text(encoding="utf-8"))
            report_text = "Watcher-local solve: report rendered on client."
            sto_path = job.solver_sto_path
        else:
            artifacts = await asyncio.to_thread(
                run_solver,
                car=job.car,
                ibt_path=job.ibt_path,
                session_id=job.session_id,
                artifact_dir=settings.artifact_dir,
                wing=job.wing,
                lap=job.lap,
                learn=True,
            )
            solver_output = artifacts.solver_output
            report_text = artifacts.report_text
            sto_path = artifacts.sto_path

        sto_storage_path = db.storage_upload(
            "sto-files",
            f"{job.session_id}.sto",
            sto_path.read_bytes(),
            content_type="application/octet-stream",
        )

        learner_payload = await asyncio.to_thread(
            learner.ingest_session,
            session_id=job.session_id,
            ibt_path=str(job.ibt_path),
            car=job.car,
            team_id=job.team_id,
            driver_id=job.driver_id,
            wing=job.wing,
            lap=job.lap,
        )

        session_update = {
            "status": "complete",
            "results": solver_output,
            "report_text": report_text,
            "sto_storage_path": sto_storage_path,
            "track": solver_output.get("track"),
            "wing_angle": solver_output.get("wing"),
            "best_lap_time": solver_output.get("lap_time_s"),
            "lap_number": solver_output.get("lap_number"),
            "driver_style": solver_output.get("driver_style"),
            "learner_summary": learner_payload,
        }
        db.update("sessions", {"id": job.session_id}, session_update)

    except Exception as exc:
        db.update(
            "sessions",
            {"id": job.session_id},
            {"status": "error", "error": str(exc)},
        )
