"""Supabase client setup plus a small local fallback store for dev/testing."""

from __future__ import annotations

import io
import threading
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from api.config import settings

try:
    from supabase import Client, create_client
except Exception:  # pragma: no cover - import guard for offline test envs
    Client = Any  # type: ignore[misc,assignment]
    create_client = None  # type: ignore[assignment]


class DatabaseGateway:
    """Light wrapper over Supabase calls with local fallback behavior."""

    def __init__(self) -> None:
        self._client: Client | None = None
        self._lock = threading.Lock()
        self._local_tables: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._local_storage_root = settings.runtime_root / "local-storage"
        self._local_storage_root.mkdir(parents=True, exist_ok=True)

        if create_client and settings.supabase_url and settings.supabase_service_role_key:
            self._client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @property
    def client(self) -> Client | None:
        return self._client

    # ---------- Table operations ----------

    def insert(self, table: str, data: dict[str, Any]) -> dict[str, Any]:
        if self._client:
            result = self._client.table(table).insert(data).execute()
            return (result.data or [deepcopy(data)])[0]
        with self._lock:
            row = deepcopy(data)
            row.setdefault("id", str(uuid4()))
            self._local_tables[table].append(row)
            return deepcopy(row)

    def upsert(self, table: str, data: dict[str, Any], on_conflict: str | None = None) -> dict[str, Any]:
        if self._client:
            if on_conflict:
                result = self._client.table(table).upsert(data, on_conflict=on_conflict).execute()
            else:
                result = self._client.table(table).upsert(data).execute()
            return (result.data or [deepcopy(data)])[0]
        with self._lock:
            target = deepcopy(data)
            row_id = target.get("id")
            if row_id:
                for idx, row in enumerate(self._local_tables[table]):
                    if row.get("id") == row_id:
                        self._local_tables[table][idx] = target
                        return deepcopy(target)
            self._local_tables[table].append(target)
            return deepcopy(target)

    def update(self, table: str, filters: dict[str, Any], data: dict[str, Any]) -> int:
        if self._client:
            query = self._client.table(table).update(data)
            for key, value in filters.items():
                query = query.eq(key, value)
            result = query.execute()
            return len(result.data or [])
        with self._lock:
            count = 0
            for row in self._local_tables[table]:
                if all(row.get(k) == v for k, v in filters.items()):
                    row.update(deepcopy(data))
                    count += 1
            return count

    def get_by_id(self, table: str, row_id: str) -> dict[str, Any] | None:
        if self._client:
            result = self._client.table(table).select("*").eq("id", row_id).limit(1).execute()
            data = result.data or []
            return data[0] if data else None
        with self._lock:
            for row in self._local_tables[table]:
                if row.get("id") == row_id:
                    return deepcopy(row)
        return None

    def select(
        self,
        table: str,
        filters: dict[str, Any] | None = None,
        ilike: dict[str, str] | None = None,
        in_filter: tuple[str, list[Any]] | None = None,
        limit: int | None = None,
        offset: int = 0,
        order_by: str | None = None,
        ascending: bool = False,
    ) -> list[dict[str, Any]]:
        if self._client:
            query = self._client.table(table).select("*")
            for key, value in (filters or {}).items():
                query = query.eq(key, value)
            for key, value in (ilike or {}).items():
                query = query.ilike(key, value)
            if in_filter:
                column, values = in_filter
                query = query.in_(column, values)
            if order_by:
                query = query.order(order_by, desc=not ascending)
            if limit is not None:
                query = query.range(offset, offset + max(limit - 1, 0))
            result = query.execute()
            return result.data or []

        with self._lock:
            rows = deepcopy(self._local_tables[table])
        if filters:
            rows = [r for r in rows if all(r.get(k) == v for k, v in filters.items())]
        if ilike:
            for key, pattern in ilike.items():
                needle = pattern.strip("%").lower()
                rows = [r for r in rows if needle in str(r.get(key, "")).lower()]
        if in_filter:
            column, values = in_filter
            rows = [r for r in rows if r.get(column) in values]
        if order_by:
            rows = sorted(rows, key=lambda r: r.get(order_by), reverse=not ascending)
        if offset:
            rows = rows[offset:]
        if limit is not None:
            rows = rows[:limit]
        return rows

    # ---------- Storage operations ----------

    def storage_upload(
        self,
        bucket: str,
        path: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        if self._client:
            self._client.storage.from_(bucket).upload(
                path,
                io.BytesIO(content),
                {"content-type": content_type, "upsert": "true"},
            )
            return f"{bucket}/{path}"
        target = self._local_storage_root / bucket / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return f"{bucket}/{path}"

    def storage_download(self, bucket: str, path: str) -> bytes:
        if self._client:
            return self._client.storage.from_(bucket).download(path)
        target = self._local_storage_root / bucket / path
        return target.read_bytes()

    # ---------- Auth helpers ----------

    def get_user_from_token(self, token: str) -> dict[str, Any] | None:
        if not self._client:
            return None
        try:
            response = self._client.auth.get_user(token)
            user = getattr(response, "user", None)
            if user is None:
                return None
            if hasattr(user, "model_dump"):
                return user.model_dump()
            if isinstance(user, dict):
                return user
            return {"id": getattr(user, "id", None), "email": getattr(user, "email", None)}
        except Exception:
            return None

    def auth_sign_up(self, email: str, password: str) -> dict[str, Any]:
        if not self._client:
            return {"user": {"id": str(uuid4()), "email": email}, "session": None}
        response = self._client.auth.sign_up({"email": email, "password": password})
        user = getattr(response, "user", None)
        session = getattr(response, "session", None)
        return {
            "user": user.model_dump() if hasattr(user, "model_dump") else user,
            "session": session.model_dump() if hasattr(session, "model_dump") else session,
        }

    def auth_sign_in(self, email: str, password: str) -> dict[str, Any]:
        if not self._client:
            fake_id = str(uuid4())
            return {
                "user": {"id": fake_id, "email": email},
                "session": {"access_token": f"dev-{fake_id}", "refresh_token": f"dev-refresh-{fake_id}"},
            }
        response = self._client.auth.sign_in_with_password({"email": email, "password": password})
        user = getattr(response, "user", None)
        session = getattr(response, "session", None)
        return {
            "user": user.model_dump() if hasattr(user, "model_dump") else user,
            "session": session.model_dump() if hasattr(session, "model_dump") else session,
        }


db_gateway = DatabaseGateway()
