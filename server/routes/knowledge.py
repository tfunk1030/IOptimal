"""Knowledge routes — empirical models and team statistics."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth import verify_api_key
from server.database import get_db
from teamdb.models import (
    ActivityLog,
    Car,
    EmpiricalModel,
    Member,
    Observation,
)

router = APIRouter(tags=["knowledge"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class EmpiricalModelOut(BaseModel):
    id: str
    model_id: str
    car: str
    track: str
    model_type: str
    model_json: dict[str, Any]
    created_at: datetime


class KnowledgeResponse(BaseModel):
    empirical_models: list[EmpiricalModelOut]
    car_model: Optional[dict[str, Any]] = None


class CarStat(BaseModel):
    name: str
    car_class: Optional[str] = None
    support_tier: str


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
async def get_knowledge(
    car: str,
    track: str,
    member: Member = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    # Empirical models for this car/track.
    result = await db.execute(
        select(EmpiricalModel).where(
            EmpiricalModel.team_id == member.team_id,
            EmpiricalModel.car == car,
            EmpiricalModel.track == track,
        ).order_by(EmpiricalModel.created_at.desc())
    )
    models = [
        EmpiricalModelOut(
            id=m.id,
            model_id=m.model_id,
            car=m.car,
            track=m.track,
            model_type=m.model_type,
            model_json=m.model_json,
            created_at=m.created_at,
        )
        for m in result.scalars().all()
    ]

    # Global car model (if a car row exists with car_model_json populated).
    car_result = await db.execute(
        select(Car).where(Car.team_id == member.team_id, Car.name == car)
    )
    car_row = car_result.scalar_one_or_none()
    car_model = car_row.car_model_json if car_row and car_row.car_model_json else None

    return KnowledgeResponse(empirical_models=models, car_model=car_model)


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
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
        select(Car).where(Car.team_id == team_id)
    )
    cars = [
        CarStat(name=c.name, car_class=c.car_class, support_tier=c.support_tier)
        for c in cars_result.scalars().all()
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
