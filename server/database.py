"""Database setup — async SQLAlchemy engine, session, and init."""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from teamdb.models import Base

DATABASE_URL: str = os.environ.get(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./ioptimal_team.db",
)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    """FastAPI dependency — yield an async DB session."""
    async with async_session() as session:
        yield session


async def init_db() -> None:
    """Create all tables defined in teamdb.models."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
