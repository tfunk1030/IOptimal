"""Observation routes — upload and query telemetry observations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth import verify_api_key
from server.database import get_db
from teamdb.models import ActivityLog, CarDefinition, Member, Observation

router = APIRouter(prefix="/observations", tags=["observations"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ObservationCreateRequest(BaseModel):
    session_id: str
    car: str
    car_class: Optional[str] = None
    track: str
    best_lap_time_s: Optional[float] = None
    lap_count: Optional[int] = None
    observation_json: dict[str, Any]


class ObservationOut(BaseModel):
    id: str
    member_id: str
    session_id: str
    car: str
    car_class: Optional[str] = None
    track: str
    best_lap_time_s: Optional[float] = None
    lap_count: Optional[int] = None
    observation_json: dict[str, Any]
    created_at: datetime


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", response_model=ObservationOut, status_code=201)
async def create_observation(
    body: ObservationCreateRequest,
    member: Member = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    # Auto-register car if not already present for this team.
    result = await db.execute(
        select(CarDefinition).where(CarDefinition.team_id == member.team_id, CarDefinition.car_name == body.car)
    )
    if result.scalar_one_or_none() is None:
        car_def = CarDefinition(
            id=uuid.uuid4().hex,
            team_id=member.team_id,
            car_name=body.car,
            car_class=body.car_class or "unknown",
            display_name=body.car,
            support_tier="exploratory",
            created_at=datetime.now(timezone.utc),
        )
        db.add(car_def)

    obs = Observation(
        id=uuid.uuid4().hex,
        team_id=member.team_id,
        member_id=member.id,
        session_id=body.session_id,
        car=body.car,
        car_class=body.car_class,
        track=body.track,
        best_lap_time_s=body.best_lap_time_s,
        lap_count=body.lap_count,
        observation_json=body.observation_json,
        created_at=datetime.now(timezone.utc),
    )
    db.add(obs)

    # Log activity.
    activity = ActivityLog(
        id=uuid.uuid4().hex,
        team_id=member.team_id,
        member_id=member.id,
        event_type="observation_upload",
        car_class=body.car_class,
        summary=f"{body.car}/{body.track} — {body.lap_count or '?'} laps",
        created_at=datetime.now(timezone.utc),
    )
    db.add(activity)

    await db.commit()
    await db.refresh(obs)

    return ObservationOut(
        id=obs.id,
        member_id=obs.member_id,
        session_id=obs.session_id,
        car=obs.car,
        car_class=obs.car_class,
        track=obs.track,
        best_lap_time_s=obs.best_lap_time_s,
        lap_count=obs.lap_count,
        observation_json=obs.observation_json,
        created_at=obs.created_at,
    )


@router.get("/{car}/{track}", response_model=list[ObservationOut])
async def list_observations(
    car: str,
    track: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    member: Member = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Observation)
        .where(
            Observation.team_id == member.team_id,
            Observation.car == car,
            Observation.track == track,
        )
        .order_by(Observation.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [
        ObservationOut(
            id=o.id,
            member_id=o.member_id,
            session_id=o.session_id,
            car=o.car,
            car_class=o.car_class,
            track=o.track,
            best_lap_time_s=o.best_lap_time_s,
            lap_count=o.lap_count,
            observation_json=o.observation_json,
            created_at=o.created_at,
        )
        for o in result.scalars().all()
    ]
