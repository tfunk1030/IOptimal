"""Session result retrieval routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import RequestContext, get_db, require_team_context
from api.models.database import DatabaseGateway
from api.models.schemas import SessionResultResponse

router = APIRouter(prefix="/api", tags=["results"])


@router.get("/results/{session_id}", response_model=SessionResultResponse)
def get_results(
    session_id: str,
    ctx: RequestContext = Depends(require_team_context),
    db: DatabaseGateway = Depends(get_db),
) -> SessionResultResponse:
    row = db.get_by_id("sessions", session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.get("team_id") != ctx.team_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    return SessionResultResponse(
        id=row["id"],
        status=row.get("status", "processing"),
        error=row.get("error"),
        results=row.get("results"),
        report_text=row.get("report_text"),
        sto_storage_path=row.get("sto_storage_path"),
        created_at=row.get("created_at"),
    )
