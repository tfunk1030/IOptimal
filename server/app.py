"""Main FastAPI application — lifespan, CORS, routes, health check."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.database import init_db
from server.routes.knowledge import router as knowledge_router
from server.routes.leaderboard import router as leaderboard_router
from server.routes.observations import router as observations_router
from server.routes.setups import router as setups_router
from server.routes.team import router as team_router


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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
