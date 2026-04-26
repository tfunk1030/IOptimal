"""Knowledge routes — empirical models and team statistics."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth import verify_api_key
from server.database import get_db
from server.rate_limit import get_limit
from server.validation import require_known_car
from teamdb.models import (
    ActivityLog,
    CarDefinition,
    EmpiricalModel,
    Member,
    Observation,
)

router = APIRouter(tags=["knowledge"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class EmpiricalModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    car: str
    track: str
    model_json: dict[str, Any]
    observation_count: int
    support_tier: str
    updated_at: datetime


class KnowledgeResponse(BaseModel):
    empirical_models: list[EmpiricalModelOut]
    car_model: Optional[dict[str, Any]] = None


class CarStat(BaseModel):
    car_name: str
    car_class: Optional[str] = None
    support_tier: str
    tracks: list[str] = []


class StatsResponse(BaseModel):
    total_observations: int
    total_members: int
    cars: list[CarStat]
    tracks: list[str]
    recent_activity_count: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/knowledge/{car}/{track}", response_model=KnowledgeResponse)
@get_limit()
async def get_knowledge(
    request: Request,
    car: str,
    track: str,
    member: Member = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    require_known_car(car)

    # Empirical models for this car/track.
    result = await db.execute(
        select(EmpiricalModel).where(
            EmpiricalModel.team_id == member.team_id,
            EmpiricalModel.car == car,
            EmpiricalModel.track == track,
        ).order_by(EmpiricalModel.updated_at.desc())
    )
    models = [
        EmpiricalModelOut(
            id=str(m.id),
            car=m.car,
            track=m.track,
            model_json=m.model_json,
            observation_count=m.observation_count,
            support_tier=m.support_tier,
            updated_at=m.updated_at,
        )
        for m in result.scalars().all()
    ]

    # Global car model (if a car row exists with car_model_json populated).
    car_result = await db.execute(
        select(CarDefinition).where(CarDefinition.team_id == member.team_id, CarDefinition.car_name == car)
    )
    car_row = car_result.scalar_one_or_none()
    car_model = car_row.car_model_json if car_row and car_row.car_model_json else None

    return KnowledgeResponse(empirical_models=models, car_model=car_model)


@router.get("/stats", response_model=StatsResponse)
@get_limit()
async def get_stats(
    request: Request,
    member: Member = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    team_id = member.team_id

    obs_count = (await db.execute(
        select(func.count()).select_from(Observation).where(Observation.team_id == team_id)
    )).scalar_one()

    member_count = (await db.execute(
        select(func.count()).select_from(Member).where(Member.team_id == team_id)
    )).scalar_one()

    cars_result = await db.execute(
        select(CarDefinition).where(CarDefinition.team_id == team_id)
    )
    car_rows = cars_result.scalars().all()

    # Fetch distinct tracks per car from observations.
    car_tracks_result = await db.execute(
        select(Observation.car, Observation.track)
        .where(Observation.team_id == team_id)
        .distinct()
    )
    car_tracks_map: dict[str, list[str]] = {}
    for row in car_tracks_result.all():
        car_tracks_map.setdefault(row[0], []).append(row[1])

    cars = [
        CarStat(
            car_name=c.car_name,
            car_class=c.car_class,
            support_tier=c.support_tier,
            tracks=car_tracks_map.get(c.car_name, []),
        )
        for c in car_rows
    ]

    tracks_result = await db.execute(
        select(distinct(Observation.track)).where(Observation.team_id == team_id)
    )
    tracks = [row[0] for row in tracks_result.all()]

    # Recent activity = last 7 days.
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    recent_count = (await db.execute(
        select(func.count())
        .select_from(ActivityLog)
        .where(ActivityLog.team_id == team_id, ActivityLog.created_at >= week_ago)
    )).scalar_one()

    return StatsResponse(
        total_observations=obs_count,
        total_members=member_count,
        cars=cars,
        tracks=tracks,
        recent_activity_count=recent_count,
    )
