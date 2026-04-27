"""Observation routes — upload and query telemetry observations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
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
    # GT3 Phase 2 — F2 + F6 (audit infra-teamdb-watcher-desktop.md). The
    # architecture string is validated against the team's CarDefinition
    # before persistence. A default of `gtp_heave_third_torsion_front`
    # keeps legacy GTP clients (which never send the field) compatible
    # with the new schema.
    suspension_arch: str = "gtp_heave_third_torsion_front"
    bop_version: Optional[str] = None
    iracing_car_path: Optional[str] = None


class ObservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    member_id: str
    session_id: str
    car: str
    car_class: Optional[str] = None
    track: str
    best_lap_time_s: Optional[float] = None
    lap_count: Optional[int] = None
    observation_json: dict[str, Any]
    suspension_arch: Optional[str] = None
    bop_version: Optional[str] = None
    iracing_car_path: Optional[str] = None
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
    car_def = result.scalar_one_or_none()
    if car_def is None:
        car_def = CarDefinition(
            id=uuid.uuid4().hex,
            team_id=member.team_id,
            car_name=body.car,
            car_class=body.car_class or "unknown",
            display_name=body.car,
            support_tier="exploratory",
            # GT3 Phase 2 — F1. New rows record the architecture stamp
            # the client supplied so downstream validation has something
            # to compare against. Auto-registered CarDefinitions trust
            # the first uploader's arch string by definition.
            suspension_arch=body.suspension_arch,
            bop_version=body.bop_version,
            iracing_car_path=body.iracing_car_path,
            created_at=datetime.now(timezone.utc),
        )
        db.add(car_def)
    else:
        # GT3 Phase 2 — F6. If a CarDefinition already exists with a
        # populated `suspension_arch`, the upload must match. This is
        # the data-corruption guardrail: a misconfigured client
        # uploading a GT3 IBT under `car="bmw"` would otherwise silently
        # pollute the GTP empirical models.
        if car_def.suspension_arch and car_def.suspension_arch != body.suspension_arch:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"suspension_arch mismatch: car {body.car!r} is registered "
                    f"as {car_def.suspension_arch!r}, observation declares "
                    f"{body.suspension_arch!r}"
                ),
            )

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
        # F2: persist the discriminator + provenance on every row.
        suspension_arch=body.suspension_arch,
        bop_version=body.bop_version,
        iracing_car_path=body.iracing_car_path,
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
        suspension_arch=obs.suspension_arch,
        bop_version=obs.bop_version,
        iracing_car_path=obs.iracing_car_path,
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
            id=str(o.id),
            member_id=str(o.member_id),
            session_id=o.session_id,
            car=o.car,
            car_class=o.car_class,
            track=o.track,
            best_lap_time_s=o.best_lap_time_s,
            lap_count=o.lap_count,
            observation_json=o.observation_json,
            suspension_arch=o.suspension_arch,
            bop_version=o.bop_version,
            iracing_car_path=o.iracing_car_path,
            created_at=o.created_at,
        )
        for o in result.scalars().all()
    ]
