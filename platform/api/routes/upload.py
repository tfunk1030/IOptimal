"""Upload route: receives IBT and optional watcher-local solver artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile

from api.config import settings
from api.dependencies import RequestContext, get_db, require_team_context
from api.models.database import DatabaseGateway
from api.models.schemas import UploadIBTResponse
from api.services.upload_service import detect_metadata, stream_to_disk, validate_ibt
from api.workers.process_ibt import ProcessJob, process_ibt

router = APIRouter(prefix="/api", tags=["upload"])


@router.post("/upload-ibt", response_model=UploadIBTResponse)
async def upload_ibt(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    solver_json: UploadFile | None = File(default=None),
    solver_sto: UploadFile | None = File(default=None),
    car: str | None = Form(default=None),
    wing: float | None = Form(default=None),
    lap: int | None = Form(default=None),
    driver_id: str | None = Form(default=None),
    ctx: RequestContext = Depends(require_team_context),
    db: DatabaseGateway = Depends(get_db),
) -> UploadIBTResponse:
    if not file.filename or not file.filename.lower().endswith(".ibt"):
        raise HTTPException(status_code=400, detail="Expected .ibt upload")

    session_id = str(uuid4())
    upload_path = settings.upload_dir / f"{session_id}.ibt"
    await stream_to_disk(file, upload_path, settings.max_upload_mb)
    ibt = validate_ibt(upload_path)
    meta = detect_metadata(ibt, provided_car=car, provided_wing=wing, provided_lap=lap)

    effective_driver = driver_id if (ctx.is_local_dev and driver_id) else ctx.user_id
    if not effective_driver:
        raise HTTPException(status_code=400, detail="driver_id is required")

    ibt_storage_path = db.storage_upload(
        "ibt-files",
        f"{session_id}.ibt",
        upload_path.read_bytes(),
        content_type="application/octet-stream",
    )

    solver_json_path: Path | None = None
    solver_sto_path: Path | None = None
    if solver_json:
        solver_json_path = settings.artifact_dir / f"{session_id}.watcher.json"
        await stream_to_disk(solver_json, solver_json_path, settings.max_upload_mb)
    if solver_sto:
        solver_sto_path = settings.artifact_dir / f"{session_id}.watcher.sto"
        await stream_to_disk(solver_sto, solver_sto_path, settings.max_upload_mb)

    db.insert(
        "sessions",
        {
            "id": session_id,
            "driver_id": effective_driver,
            "team_id": ctx.team_id,
            "car": meta.car,
            "track": meta.track,
            "track_config": meta.track_config,
            "wing_angle": meta.wing,
            "lap_number": meta.lap,
            "status": "processing",
            "ibt_storage_path": ibt_storage_path,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    background_tasks.add_task(
        process_ibt,
        ProcessJob(
            session_id=session_id,
            team_id=ctx.team_id or "local-dev-team",
            driver_id=effective_driver,
            car=meta.car,
            wing=meta.wing,
            lap=meta.lap,
            ibt_path=upload_path,
            ibt_storage_path=ibt_storage_path,
            solver_json_path=solver_json_path,
            solver_sto_path=solver_sto_path,
        ),
        db,
    )
    return UploadIBTResponse(session_id=session_id, status="processing")
