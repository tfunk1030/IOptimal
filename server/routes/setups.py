"""Setup sharing routes — share, list, and rate setups."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth import verify_api_key
from server.database import get_db
from server.rate_limit import get_limit, post_limit
from server.validation import require_known_car
from teamdb.models import ActivityLog, Member, SetupRating, SharedSetup

router = APIRouter(prefix="/setups", tags=["setups"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SetupShareRequest(BaseModel):
    car: str
    car_class: Optional[str] = None
    track: str
    scenario: Optional[str] = None
    sto_content: Optional[str] = None
    setup_json: Optional[dict[str, Any]] = None
    notes: Optional[str] = None
    lap_time_s: Optional[float] = None


class SetupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    member_id: str
    car: str
    car_class: Optional[str] = None
    track: str
    scenario: Optional[str] = None
    sto_content: Optional[str] = None
    setup_json: Optional[dict[str, Any]] = None
    notes: Optional[str] = None
    lap_time_s: Optional[float] = None
    rating_sum: int
    rating_count: int
    created_at: datetime


class RateRequest(BaseModel):
    rating: int = Field(..., ge=-1, le=1)


class RateResponse(BaseModel):
    setup_id: str
    new_rating: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/share", response_model=SetupOut, status_code=201)
@post_limit()
async def share_setup(
    request: Request,
    body: SetupShareRequest,
    member: Member = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    require_known_car(body.car)

    setup = SharedSetup(
        id=uuid.uuid4().hex,
        team_id=member.team_id,
        member_id=member.id,
        car=body.car,
        car_class=body.car_class,
        track=body.track,
        scenario=body.scenario,
        sto_content=body.sto_content,
        setup_json=body.setup_json,
        notes=body.notes,
        lap_time_s=body.lap_time_s,
        created_at=datetime.now(timezone.utc),
    )
    db.add(setup)

    activity = ActivityLog(
        id=uuid.uuid4().hex,
        team_id=member.team_id,
        member_id=member.id,
        event_type="setup_shared",
        car_class=body.car_class,
        summary=f"{body.car}/{body.track} ({body.scenario or 'default'})",
        created_at=datetime.now(timezone.utc),
    )
    db.add(activity)

    await db.commit()
    await db.refresh(setup)

    return SetupOut(
        id=setup.id,
        member_id=setup.member_id,
        car=setup.car,
        car_class=setup.car_class,
        track=setup.track,
        scenario=setup.scenario,
        sto_content=setup.sto_content,
        setup_json=setup.setup_json,
        notes=setup.notes,
        lap_time_s=setup.lap_time_s,
        rating_sum=setup.rating_sum,
        rating_count=setup.rating_count,
        created_at=setup.created_at,
    )


@router.get("/{car}/{track}", response_model=list[SetupOut])
@get_limit()
async def list_setups(
    request: Request,
    car: str,
    track: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    member: Member = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    require_known_car(car)

    stmt = (
        select(SharedSetup)
        .where(
            SharedSetup.team_id == member.team_id,
            SharedSetup.car == car,
            SharedSetup.track == track,
        )
        .order_by(SharedSetup.rating_sum.desc(), SharedSetup.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [
        SetupOut(
            id=str(s.id),
            member_id=str(s.member_id),
            car=s.car,
            car_class=s.car_class,
            track=s.track,
            scenario=s.scenario,
            sto_content=s.sto_content,
            setup_json=s.setup_json,
            notes=s.notes,
            lap_time_s=s.lap_time_s,
            rating_sum=s.rating_sum,
            rating_count=s.rating_count,
            created_at=s.created_at,
        )
        for s in result.scalars().all()
    ]


@router.post("/{setup_id}/rate", response_model=RateResponse)
@post_limit()
async def rate_setup(
    request: Request,
    setup_id: str,
    body: RateRequest,
    member: Member = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SharedSetup).where(
            SharedSetup.id == setup_id,
            SharedSetup.team_id == member.team_id,
        )
    )
    setup = result.scalar_one_or_none()
    if setup is None:
        raise HTTPException(status_code=404, detail="Setup not found.")

    # Check for existing rating by this member.
    existing = await db.execute(
        select(SetupRating).where(
            SetupRating.setup_id == setup_id,
            SetupRating.member_id == member.id,
        )
    )
    existing_rating = existing.scalar_one_or_none()

    if existing_rating is not None:
        # Undo old rating, apply new.
        setup.rating_sum = setup.rating_sum - existing_rating.rating + body.rating
        existing_rating.rating = body.rating
    else:
        setup.rating_sum += body.rating
        setup.rating_count += 1
        new_rating = SetupRating(
            id=uuid.uuid4().hex,
            setup_id=setup_id,
            member_id=member.id,
            rating=body.rating,
            created_at=datetime.now(timezone.utc),
        )
        db.add(new_rating)

    await db.commit()

    return RateResponse(setup_id=setup_id, new_rating=setup.rating_sum)
