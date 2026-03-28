"""API-key authentication middleware for FastAPI."""

from __future__ import annotations

import hashlib

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.database import get_db
from teamdb.models import Member


def hash_api_key(key: str) -> str:
    """Return the SHA-256 hex digest of *key*."""
    return hashlib.sha256(key.encode()).hexdigest()


async def verify_api_key(
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db),
) -> Member:
    """Dependency that validates a ``Bearer <token>`` header.

    Extracts the token, SHA-256 hashes it, and looks it up in the
    ``members`` table.  Returns the :class:`Member` row on success or
    raises a 401 ``HTTPException``.
    """
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization header. Expected 'Bearer <api_key>'.")

    key_hash = hash_api_key(token)
    result = await db.execute(select(Member).where(Member.api_key_hash == key_hash))
    member = result.scalar_one_or_none()

    if member is None:
        raise HTTPException(status_code=401, detail="Invalid API key.")

    return member
