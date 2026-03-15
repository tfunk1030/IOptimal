"""Supabase-backed auth helper routes for dashboard and watcher."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import RequestContext, get_db, get_request_context
from api.models.database import DatabaseGateway
from api.models.schemas import AuthLoginRequest, AuthRegisterRequest, AuthResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register")
def register(
    payload: AuthRegisterRequest,
    db: DatabaseGateway = Depends(get_db),
) -> dict:
    auth_result = db.auth_sign_up(payload.email, payload.password)
    user = auth_result.get("user") or {}
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Failed to create user")
    db.insert(
        "drivers",
        {
            "id": user_id,
            "display_name": payload.display_name,
            "team_id": None,
            "default_car": "bmw",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"user": user, "session": auth_result.get("session")}


@router.post("/login", response_model=AuthResponse)
def login(
    payload: AuthLoginRequest,
    db: DatabaseGateway = Depends(get_db),
) -> AuthResponse:
    auth_result = db.auth_sign_in(payload.email, payload.password)
    user = auth_result.get("user") or {}
    session = auth_result.get("session") or {}
    access_token = session.get("access_token")
    if not access_token:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return AuthResponse(
        access_token=access_token,
        refresh_token=session.get("refresh_token"),
        user=user,
    )


@router.get("/me")
def me(
    ctx: RequestContext = Depends(get_request_context),
    db: DatabaseGateway = Depends(get_db),
) -> dict:
    rows = db.select("drivers", filters={"id": ctx.user_id}, limit=1)
    return {
        "id": ctx.user_id,
        "email": ctx.email,
        "team_id": ctx.team_id,
        "driver_profile": rows[0] if rows else None,
        "is_local_dev": ctx.is_local_dev,
    }
