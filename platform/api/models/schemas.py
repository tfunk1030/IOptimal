"""Request/response schemas for platform API routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class UploadIBTResponse(BaseModel):
    session_id: str
    status: str = "processing"


class SessionSummary(BaseModel):
    id: str
    driver_id: str
    team_id: str | None = None
    car: str
    track: str | None = None
    track_config: str | None = None
    best_lap_time: float | None = None
    lap_number: int | None = None
    wing_angle: float | None = None
    status: str
    created_at: datetime | None = None


class SessionsResponse(BaseModel):
    total: int
    items: list[SessionSummary]


class SessionResultResponse(BaseModel):
    id: str
    status: str
    error: str | None = None
    results: dict[str, Any] | None = None
    report_text: str | None = None
    sto_storage_path: str | None = None
    created_at: datetime | None = None


class TeamKnowledgeResponse(BaseModel):
    session_count: int
    driver_session_count: int | None = None
    fallback_mode: str = Field(
        default="driver", description="driver when driver-specific model is usable, else team"
    )
    drivers: list[dict[str, Any]]
    recurring_issues: list[dict[str, Any]]
    individual_models: list[dict[str, Any]]
    team_model: dict[str, Any] | None = None


class TeamCompareResponse(BaseModel):
    sessions: list[dict[str, Any]]
    setup_diff: list[dict[str, Any]]
    style_diff: list[dict[str, Any]]
    performance_diff: list[dict[str, Any]]


class SyncKnowledgeResponse(BaseModel):
    car: str
    track: str
    fallback_mode: str
    driver_session_count: int
    learnings_snapshot: dict[str, Any]


class AuthRegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str


class AuthLoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    user: dict[str, Any]

