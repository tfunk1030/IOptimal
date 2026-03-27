"""SQLAlchemy 2.0 ORM models for the IOptimal team database.

All tables use UUID primary keys (server-side gen_random_uuid default)
and PostgreSQL JSONB for semi-structured telemetry / model payloads.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    """Declarative base for all team-database models."""

    pass


# ---------------------------------------------------------------------------
# 1. teams
# ---------------------------------------------------------------------------

class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    invite_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    # relationships
    members: Mapped[list[Member]] = relationship(back_populates="team", cascade="all, delete-orphan")
    divisions: Mapped[list[Division]] = relationship(back_populates="team", cascade="all, delete-orphan")
    car_definitions: Mapped[list[CarDefinition]] = relationship(back_populates="team", cascade="all, delete-orphan")
    observations: Mapped[list[Observation]] = relationship(back_populates="team", cascade="all, delete-orphan")
    deltas: Mapped[list[Delta]] = relationship(back_populates="team", cascade="all, delete-orphan")
    empirical_models: Mapped[list[EmpiricalModel]] = relationship(back_populates="team", cascade="all, delete-orphan")
    global_car_models: Mapped[list[GlobalCarModel]] = relationship(back_populates="team", cascade="all, delete-orphan")
    shared_setups: Mapped[list[SharedSetup]] = relationship(back_populates="team", cascade="all, delete-orphan")
    activity_log: Mapped[list[ActivityLog]] = relationship(back_populates="team", cascade="all, delete-orphan")
    leaderboard_entries: Mapped[list[Leaderboard]] = relationship(back_populates="team", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# 2. members
# ---------------------------------------------------------------------------

class Member(Base):
    __tablename__ = "members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    iracing_name: Mapped[str] = mapped_column(String(255), nullable=False)
    iracing_member_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="member"
    )  # 'admin' | 'engineer' | 'member'
    primary_class: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    # relationships
    team: Mapped[Team] = relationship(back_populates="members")
    divisions: Mapped[list[Division]] = relationship(
        secondary="division_members", back_populates="members"
    )
    observations: Mapped[list[Observation]] = relationship(back_populates="member")
    deltas: Mapped[list[Delta]] = relationship(back_populates="member")
    shared_setups: Mapped[list[SharedSetup]] = relationship(back_populates="member")
    activity_log: Mapped[list[ActivityLog]] = relationship(back_populates="member")
    leaderboard_entries: Mapped[list[Leaderboard]] = relationship(back_populates="member")


# ---------------------------------------------------------------------------
# 3. divisions
# ---------------------------------------------------------------------------

class Division(Base):
    __tablename__ = "divisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    car_class: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    # relationships
    team: Mapped[Team] = relationship(back_populates="divisions")
    members: Mapped[list[Member]] = relationship(
        secondary="division_members", back_populates="divisions"
    )


# ---------------------------------------------------------------------------
# 4. division_members (association table with composite PK)
# ---------------------------------------------------------------------------

from sqlalchemy import Column, Table

division_members = Table(
    "division_members",
    Base.metadata,
    Column(
        "division_id",
        UUID(as_uuid=True),
        ForeignKey("divisions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "member_id",
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


# ---------------------------------------------------------------------------
# 5. car_definitions
# ---------------------------------------------------------------------------

class CarDefinition(Base):
    __tablename__ = "car_definitions"
    __table_args__ = (
        UniqueConstraint("team_id", "car_name", name="uq_car_definitions_team_car"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    car_name: Mapped[str] = mapped_column(String(128), nullable=False)
    car_class: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    has_aero_maps: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_car_model: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_setup_writer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    support_tier: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unsupported"
    )  # 'unsupported' | 'exploratory' | 'partial' | 'calibrated'
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    car_model_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # relationships
    team: Mapped[Team] = relationship(back_populates="car_definitions")


# ---------------------------------------------------------------------------
# 6. observations
# ---------------------------------------------------------------------------

class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint("team_id", "session_id", name="uq_observations_team_session"),
        Index("ix_observations_team_car_track", "team_id", "car", "track"),
        Index("ix_observations_team_car_class", "team_id", "car_class"),
        Index("ix_observations_team_member", "team_id", "member_id"),
        Index(
            "ix_observations_team_created",
            "team_id",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    car: Mapped[str] = mapped_column(String(128), nullable=False)
    car_class: Mapped[str] = mapped_column(String(64), nullable=False)
    track: Mapped[str] = mapped_column(String(255), nullable=False)
    best_lap_time_s: Mapped[float] = mapped_column(Float, nullable=False)
    lap_count: Mapped[int] = mapped_column(Integer, nullable=False)
    observation_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    # relationships
    team: Mapped[Team] = relationship(back_populates="observations")
    member: Mapped[Member] = relationship(back_populates="observations")


# ---------------------------------------------------------------------------
# 7. deltas
# ---------------------------------------------------------------------------

class Delta(Base):
    __tablename__ = "deltas"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL"), nullable=False
    )
    car: Mapped[str] = mapped_column(String(128), nullable=False)
    track: Mapped[str] = mapped_column(String(255), nullable=False)
    setup_changes_count: Mapped[int] = mapped_column(Integer, nullable=False)
    delta_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    # relationships
    team: Mapped[Team] = relationship(back_populates="deltas")
    member: Mapped[Member] = relationship(back_populates="deltas")


# ---------------------------------------------------------------------------
# 8. empirical_models
# ---------------------------------------------------------------------------

class EmpiricalModel(Base):
    __tablename__ = "empirical_models"
    __table_args__ = (
        UniqueConstraint("team_id", "car", "track", name="uq_empirical_models_team_car_track"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    car: Mapped[str] = mapped_column(String(128), nullable=False)
    track: Mapped[str] = mapped_column(String(255), nullable=False)
    model_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    support_tier: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unsupported"
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # relationships
    team: Mapped[Team] = relationship(back_populates="empirical_models")


# ---------------------------------------------------------------------------
# 9. global_car_models
# ---------------------------------------------------------------------------

class GlobalCarModel(Base):
    __tablename__ = "global_car_models"
    __table_args__ = (
        UniqueConstraint("team_id", "car", name="uq_global_car_models_team_car"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    car: Mapped[str] = mapped_column(String(128), nullable=False)
    model_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    tracks_included: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    total_sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # relationships
    team: Mapped[Team] = relationship(back_populates="global_car_models")


# ---------------------------------------------------------------------------
# 10. shared_setups
# ---------------------------------------------------------------------------

class SharedSetup(Base):
    __tablename__ = "shared_setups"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL"), nullable=False
    )
    car: Mapped[str] = mapped_column(String(128), nullable=False)
    car_class: Mapped[str] = mapped_column(String(64), nullable=False)
    track: Mapped[str] = mapped_column(String(255), nullable=False)
    scenario: Mapped[str] = mapped_column(String(64), nullable=False)
    sto_content: Mapped[str] = mapped_column(Text, nullable=False)
    setup_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    lap_time_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rating_sum: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rating_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    # relationships
    team: Mapped[Team] = relationship(back_populates="shared_setups")
    member: Mapped[Member] = relationship(back_populates="shared_setups")


# ---------------------------------------------------------------------------
# 11. activity_log
# ---------------------------------------------------------------------------

class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    car: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    car_class: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    track: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    # relationships
    team: Mapped[Team] = relationship(back_populates="activity_log")
    member: Mapped[Member] = relationship(back_populates="activity_log")


# ---------------------------------------------------------------------------
# 12. leaderboard
# ---------------------------------------------------------------------------

class Leaderboard(Base):
    __tablename__ = "leaderboard"
    __table_args__ = (
        UniqueConstraint(
            "team_id", "car", "track", "member_id",
            name="uq_leaderboard_team_car_track_member",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    car: Mapped[str] = mapped_column(String(128), nullable=False)
    track: Mapped[str] = mapped_column(String(255), nullable=False)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )
    best_lap_time_s: Mapped[float] = mapped_column(Float, nullable=False)
    session_date: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # relationships
    team: Mapped[Team] = relationship(back_populates="leaderboard_entries")
    member: Mapped[Member] = relationship(back_populates="leaderboard_entries")
