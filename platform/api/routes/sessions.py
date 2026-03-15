"""Session listing routes."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from api.dependencies import RequestContext, get_db, require_team_context
from api.models.database import DatabaseGateway
from api.models.schemas import SessionSummary, SessionsResponse

router = APIRouter(prefix="/api", tags=["sessions"])


@router.get("/sessions", response_model=SessionsResponse)
def list_sessions(
    driver_id: str | None = Query(default=None),
    car: str | None = Query(default=None),
    track: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    ctx: RequestContext = Depends(require_team_context),
    db: DatabaseGateway = Depends(get_db),
) -> SessionsResponse:
    filters: dict[str, object] = {"team_id": ctx.team_id}
    if driver_id:
        filters["driver_id"] = driver_id
    if car:
        filters["car"] = car
    ilike = {"track": f"{track}%"} if track else None

    rows = db.select(
        "sessions",
        filters=filters,
        ilike=ilike,
        order_by="created_at",
        ascending=False,
    )

    if date_from:
        d_from = datetime.fromisoformat(date_from)
        rows = [r for r in rows if r.get("created_at") and datetime.fromisoformat(r["created_at"]) >= d_from]
    if date_to:
        d_to = datetime.fromisoformat(date_to)
        rows = [r for r in rows if r.get("created_at") and datetime.fromisoformat(r["created_at"]) <= d_to]

    total = len(rows)
    paged = rows[offset : offset + limit]
    items = [SessionSummary(**r) for r in paged]
    return SessionsResponse(total=total, items=items)
