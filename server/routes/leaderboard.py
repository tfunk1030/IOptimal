"""Leaderboard routes — per car/track sorted leaderboard."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth import verify_api_key
from server.database import get_db
from teamdb.models import Leaderboard, Member

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class LeaderboardEntry(BaseModel):
    id: str
    member_id: str
    car: str
    track: str
    best_lap_time_s: float
    session_id: Optional[str] = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/{car}/{track}", response_model=list[LeaderboardEntry])
async def get_leaderboard(
    car: str,
    track: str,
    member: Member = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Leaderboard)
        .where(
            Leaderboard.team_id == member.team_id,
            Leaderboard.car == car,
            Leaderboard.track == track,
        )
        .order_by(Leaderboard.best_lap_time_s.asc())
    )
    result = await db.execute(stmt)
    return [
        LeaderboardEntry(
            id=e.id,
            member_id=e.member_id,
            car=e.car,
            track=e.track,
            best_lap_time_s=e.best_lap_time_s,
            session_id=e.session_id,
            created_at=e.created_at,
        )
        for e in result.scalars().all()
    ]
