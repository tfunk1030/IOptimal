"""Main FastAPI application — lifespan, CORS, routes, health check."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.database import init_db
from server.rate_limit import attach_rate_limiter
from server.routes.knowledge import router as knowledge_router
from server.routes.leaderboard import router as leaderboard_router
from server.routes.observations import router as observations_router
from server.routes.setups import router as setups_router
from server.routes.team import router as team_router

logger = logging.getLogger(__name__)

_DEFAULT_DEV_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000,http://127.0.0.1:8000"


def _allowed_origins() -> list[str]:
    """Read comma-separated origins from IOPTIMAL_ALLOWED_ORIGINS env var.

    Defaults to common localhost dev ports so the desktop/web dev workflow
    keeps working without manual config.  Setting the var to ``*`` is honored
    explicitly for legacy deployments but logged as a warning.
    """
    raw = os.environ.get("IOPTIMAL_ALLOWED_ORIGINS", _DEFAULT_DEV_ORIGINS)
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if "*" in origins:
        logger.warning("CORS allow_origins is '*' — this is unsafe for production deployments")
    return origins


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup: create DB tables.  Shutdown: nothing special."""
    await init_db()
    yield


app = FastAPI(
    title="IOptimal Team Server",
    version="0.1.0",
    lifespan=lifespan,
)

# ---- CORS ----------------------------------------------------------------
_origins = _allowed_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    # `allow_credentials=True` is incompatible with a wildcard origin per the
    # CORS spec; downgrade to False if a wildcard slipped through.
    allow_credentials="*" not in _origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Rate limiting -------------------------------------------------------
attach_rate_limiter(app)

# ---- Routers (all under /api) --------------------------------------------
app.include_router(team_router, prefix="/api")
app.include_router(observations_router, prefix="/api")
app.include_router(knowledge_router, prefix="/api")
app.include_router(setups_router, prefix="/api")
app.include_router(leaderboard_router, prefix="/api")


# ---- Health check ---------------------------------------------------------
@app.get("/api/health")
async def health():
    return {"status": "ok"}
