"""FastAPI entrypoint for iOptimal platform backend."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import settings
from api.models.database import db_gateway
from api.routes.auth import router as auth_router
from api.routes.results import router as results_router
from api.routes.sessions import router as sessions_router
from api.routes.setups import router as setups_router
from api.routes.team import router as team_router
from api.routes.upload import router as upload_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Ensure runtime directories exist at startup.
    _ = settings.upload_dir
    _ = settings.artifact_dir
    yield


app = FastAPI(title="iOptimal Platform API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload_router)
app.include_router(results_router)
app.include_router(setups_router)
app.include_router(sessions_router)
app.include_router(team_router)
app.include_router(auth_router)


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "supabase_enabled": db_gateway.enabled,
        "runtime_root": str(settings.runtime_root),
    }
