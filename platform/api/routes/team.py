"""Team knowledge and comparison routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import RequestContext, get_db, require_team_context
from api.models.database import DatabaseGateway
from api.models.schemas import SyncKnowledgeResponse, TeamCompareResponse, TeamKnowledgeResponse
from api.services.team_service import TeamKnowledgeService, compare_sessions

router = APIRouter(prefix="/api/team", tags=["team"])


@router.get("/knowledge")
def get_team_knowledge(
    car: str = Query(...),
    track: str = Query(...),
    mode: str | None = Query(default=None),
    ctx: RequestContext = Depends(require_team_context),
    db: DatabaseGateway = Depends(get_db),
) -> dict:
    service = TeamKnowledgeService(db)
    if mode == "sync":
        return service.get_sync_payload(team_id=ctx.team_id or "", driver_id=ctx.user_id, car=car, track=track)
    payload = service.get_team_knowledge(team_id=ctx.team_id or "", car=car, track=track)
    sync = service.get_sync_payload(team_id=ctx.team_id or "", driver_id=ctx.user_id, car=car, track=track)
    return TeamKnowledgeResponse(
        session_count=payload["session_count"],
        driver_session_count=sync["driver_session_count"],
        fallback_mode=sync["fallback_mode"],
        drivers=payload["drivers"],
        recurring_issues=payload["recurring_issues"],
        individual_models=payload["individual_models"],
        team_model=payload["team_model"],
    )


@router.get("/sync-learnings", response_model=SyncKnowledgeResponse)
def get_sync_learnings(
    car: str = Query(...),
    track: str = Query(...),
    ctx: RequestContext = Depends(require_team_context),
    db: DatabaseGateway = Depends(get_db),
) -> SyncKnowledgeResponse:
    service = TeamKnowledgeService(db)
    payload = service.get_sync_payload(team_id=ctx.team_id or "", driver_id=ctx.user_id, car=car, track=track)
    return SyncKnowledgeResponse(**payload)


@router.get("/compare", response_model=TeamCompareResponse)
def compare_team_sessions(
    session_ids: list[str] = Query(..., min_length=2),
    ctx: RequestContext = Depends(require_team_context),
    db: DatabaseGateway = Depends(get_db),
) -> TeamCompareResponse:
    rows = db.select("sessions", in_filter=("id", session_ids))
    rows = [r for r in rows if r.get("team_id") == ctx.team_id]
    if len(rows) < 2:
        raise HTTPException(status_code=404, detail="Need at least two team sessions to compare")
    compared = compare_sessions(rows)
    return TeamCompareResponse(**compared)
