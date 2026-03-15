"""Watcher auth helper using backend Supabase-backed auth endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from watcher.config import WatcherConfig


@dataclass
class AuthSession:
    access_token: str
    refresh_token: str
    user: dict[str, Any]


class WatcherAuthClient:
    def __init__(self, config: WatcherConfig) -> None:
        self.config = config
        self.client = httpx.Client(timeout=30)

    def login(self, email: str, password: str) -> AuthSession:
        response = self.client.post(
            f"{self.config.server_url.rstrip('/')}/api/auth/login",
            json={"email": email, "password": password},
        )
        response.raise_for_status()
        payload = response.json()
        session = AuthSession(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token") or "",
            user=payload.get("user") or {},
        )
        self.config.access_token = session.access_token
        self.config.refresh_token = session.refresh_token
        self.config.driver_id = session.user.get("id", self.config.driver_id)
        self.config.email = email
        self.config.save()
        return session

    def ensure_me(self) -> dict[str, Any]:
        if not self.config.access_token:
            raise RuntimeError("No auth token configured. Run login first.")
        response = self.client.get(
            f"{self.config.server_url.rstrip('/')}/api/auth/me",
            headers={"Authorization": f"Bearer {self.config.access_token}"},
        )
        response.raise_for_status()
        payload = response.json()
        self.config.driver_id = payload.get("id", self.config.driver_id)
        self.config.team_id = payload.get("team_id", self.config.team_id)
        self.config.save()
        return payload

