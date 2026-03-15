"""Setup artifact download routes."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from api.dependencies import RequestContext, get_db, require_team_context
from api.models.database import DatabaseGateway

router = APIRouter(prefix="/api", tags=["setups"])


@router.get("/setups/{session_id}")
def download_setup(
    session_id: str,
    ctx: RequestContext = Depends(require_team_context),
    db: DatabaseGateway = Depends(get_db),
) -> FileResponse:
    row = db.get_by_id("sessions", session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.get("team_id") != ctx.team_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    storage_path = row.get("sto_storage_path")
    if not storage_path:
        raise HTTPException(status_code=404, detail="No setup artifact available")
    if "/" not in storage_path:
        raise HTTPException(status_code=500, detail="Invalid storage path")
    bucket, file_path = storage_path.split("/", 1)
    content = db.storage_download(bucket, file_path)
    temp = Path(tempfile.gettempdir()) / f"{session_id}.sto"
    temp.write_bytes(content)
    return FileResponse(
        path=temp,
        media_type="application/octet-stream",
        filename=f"{session_id}.sto",
    )
