"""Shared request dependencies for auth and gateway access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status

from api.config import settings
from api.models.database import DatabaseGateway, db_gateway


@dataclass
class RequestContext:
    user_id: str
    team_id: str | None
    email: str | None = None
    token: str | None = None
    is_local_dev: bool = False


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _is_local_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in LOCAL_HOSTS


def get_db() -> DatabaseGateway:
    return db_gateway


def get_request_context(
    request: Request,
    authorization: str | None = Header(default=None),
    x_dev_driver_id: str | None = Header(default=None),
    x_dev_team_id: str | None = Header(default=None),
    db: DatabaseGateway = Depends(get_db),
) -> RequestContext:
    """Resolve caller identity.

    Remote callers must present a valid Supabase bearer token.
    Localhost callers can run with open auth in dev mode.
    """
    is_local = _is_local_request(request)

    if is_local and settings.dev_local_open_auth and not authorization:
        return RequestContext(
            user_id=(x_dev_driver_id or "local-dev-driver"),
            team_id=(x_dev_team_id or "local-dev-team"),
            is_local_dev=True,
        )

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    token = authorization.split(" ", 1)[1].strip()
    user = db.get_user_from_token(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user_id = str(user.get("id") or "")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    driver_rows = db.select("drivers", filters={"id": user_id}, limit=1)
    team_id = driver_rows[0].get("team_id") if driver_rows else None
    return RequestContext(
        user_id=user_id,
        team_id=team_id,
        email=user.get("email"),
        token=token,
        is_local_dev=False,
    )


def require_team_context(ctx: RequestContext = Depends(get_request_context)) -> RequestContext:
    if not ctx.team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not associated with a team",
        )
    return ctx
