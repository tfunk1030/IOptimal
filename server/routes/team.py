"""Team management routes — create, join, list members, activity log."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth import hash_api_key, verify_api_key
from server.database import get_db
from teamdb.models import ActivityLog, Member, Team

router = APIRouter(prefix="/team", tags=["team"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class TeamCreateRequest(BaseModel):
    name: str


class TeamCreateResponse(BaseModel):
    team_id: str
    invite_code: str
    admin_api_key: str


class TeamJoinRequest(BaseModel):
    invite_code: str
    iracing_name: str
    iracing_member_id: Optional[int] = None
    primary_class: Optional[str] = None


class TeamJoinResponse(BaseModel):
    member_id: str
    api_key: str


class MemberOut(BaseModel):
    id: str
    iracing_name: str
    iracing_member_id: Optional[int] = None
    primary_class: Optional[str] = None
    role: str
    created_at: datetime


class ActivityOut(BaseModel):
    id: str
    member_id: str
    event_type: str
    summary: Optional[str] = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/create", response_model=TeamCreateResponse, status_code=201)
async def create_team(body: TeamCreateRequest, db: AsyncSession = Depends(get_db)):
    team_id = uuid.uuid4().hex
    invite_code = uuid.uuid4().hex[:8]

    team = Team(
        id=team_id,
        name=body.name,
        invite_code=invite_code,
        created_at=datetime.now(timezone.utc),
    )
    db.add(team)

    # Create an admin member for the team creator.
    raw_api_key = uuid.uuid4().hex
    admin_member = Member(
        id=uuid.uuid4().hex,
        team_id=team_id,
        iracing_name="admin",
        role="admin",
        api_key_hash=hash_api_key(raw_api_key),
        created_at=datetime.now(timezone.utc),
    )
    db.add(admin_member)
    await db.commit()

    return TeamCreateResponse(
        team_id=team_id,
        invite_code=invite_code,
        admin_api_key=raw_api_key,
    )


@router.post("/join", response_model=TeamJoinResponse, status_code=201)
async def join_team(body: TeamJoinRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Team).where(Team.invite_code == body.invite_code))
    team = result.scalar_one_or_none()
    if team is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Invalid invite code.")

    raw_api_key = uuid.uuid4().hex
    member = Member(
        id=uuid.uuid4().hex,
        team_id=team.id,
        iracing_name=body.iracing_name,
        iracing_member_id=body.iracing_member_id,
        primary_class=body.primary_class,
        role="member",
        api_key_hash=hash_api_key(raw_api_key),
        created_at=datetime.now(timezone.utc),
    )
    db.add(member)
    await db.commit()

    return TeamJoinResponse(member_id=member.id, api_key=raw_api_key)


@router.get("/members", response_model=list[MemberOut])
async def list_members(
    member: Member = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Member).where(Member.team_id == member.team_id).order_by(Member.created_at)
    )
    return [
        MemberOut(
            id=m.id,
            iracing_name=m.iracing_name,
            iracing_member_id=m.iracing_member_id,
            primary_class=m.primary_class,
            role=m.role,
            created_at=m.created_at,
        )
        for m in result.scalars().all()
    ]


@router.get("/activity", response_model=list[ActivityOut])
async def team_activity(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    car_class: Optional[str] = Query(None),
    member: Member = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(ActivityLog)
        .join(Member, ActivityLog.member_id == Member.id)
        .where(Member.team_id == member.team_id)
    )
    if car_class is not None:
        stmt = stmt.where(ActivityLog.car_class == car_class)
    stmt = stmt.order_by(ActivityLog.created_at.desc()).offset(offset).limit(limit)

    result = await db.execute(stmt)
    return [
        ActivityOut(
            id=a.id,
            member_id=a.member_id,
            event_type=a.event_type,
            summary=a.summary,
            created_at=a.created_at,
        )
        for a in result.scalars().all()
    ]
