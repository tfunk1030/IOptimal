"""FastAPI application for the local IOptimal web interface."""

from __future__ import annotations

import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from webapp.jobs import RunJobManager
from webapp.services import IOptimalWebService
from webapp.settings import AppSettings
from webapp.storage import RunRepository
from webapp.types import RunCreateRequest


def create_app(settings: AppSettings | None = None) -> FastAPI:
    resolved_settings = settings or AppSettings.from_env()
    resolved_settings.ensure_directories()
    repository = RunRepository(resolved_settings.database_path)
    repository.initialize()
    service = IOptimalWebService(resolved_settings)
    job_manager = RunJobManager(repository, service)
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = resolved_settings
        app.state.repository = repository
        app.state.service = service
        app.state.jobs = job_manager
        yield
        job_manager.shutdown()

    app = FastAPI(title=resolved_settings.title, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/runs/new", status_code=302)

    @app.get("/runs/new", response_class=HTMLResponse)
    async def new_run(request: Request, mode: str = "single_session", error: str | None = None) -> HTMLResponse:
        selected_mode = mode if mode in {"single_session", "comparison", "track_solve"} else "single_session"
        return templates.TemplateResponse(
            request,
            "runs_new.html",
            _page_context(request, "new_run", selected_mode=selected_mode, error=error),
        )

    @app.post("/runs")
    async def create_run(
        request: Request,
        mode: str = Form("single_session"),
        car: str = Form("bmw"),
        track: str | None = Form(None),
        wing: float | None = Form(None),
        lap: int | None = Form(None),
        fuel: float | None = Form(None),
        balance: float = Form(50.14),
        tolerance: float = Form(0.1),
        free_opt: str | None = Form(None),
        use_learning: str | None = Form(None),
        synthesize: str | None = Form(None),
        ibt_files: list[UploadFile] | None = File(None),
    ) -> RedirectResponse:
        settings_local: AppSettings = request.app.state.settings
        run_id = uuid4().hex
        saved_files = await _persist_uploads(run_id, settings_local, ibt_files or [])
        run_request = RunCreateRequest(
            mode=mode if mode in {"single_session", "comparison", "track_solve"} else "single_session",
            car=car.strip().lower(),
            ibt_paths=saved_files,
            track=(track or "").strip() or None,
            wing=wing,
            lap=lap,
            fuel=fuel,
            balance=balance,
            tolerance=tolerance,
            free_opt=free_opt is not None,
            use_learning=use_learning is not None,
            synthesize=synthesize is not None or mode != "comparison",
        )
        error = _validate_run_request(run_request)
        if error is not None:
            return RedirectResponse(
                url=f"/runs/new?mode={quote_plus(run_request.mode)}&error={quote_plus(error)}",
                status_code=303,
            )

        request.app.state.repository.create_run(run_id, run_request)
        request.app.state.jobs.submit(run_id, run_request)
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def run_detail(request: Request, run_id: str) -> HTMLResponse:
        payload = request.app.state.repository.get_run(run_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Run not found")
        run = payload["run"]
        if run["state"] in {"queued", "running"} or payload["summary"] is None:
            return templates.TemplateResponse(
                request,
                "run_status.html",
                _page_context(request, "sessions", run=run, run_id=run_id),
            )
        if run["state"] == "failed":
            return templates.TemplateResponse(
                request,
                "run_status.html",
                _page_context(request, "sessions", run=run, run_id=run_id),
            )
        summary = payload["summary"]["payload"]
        template_name = "comparison_detail.html" if summary.get("result_kind") == "comparison" else "run_detail.html"
        return templates.TemplateResponse(
            request,
            template_name,
            _page_context(request, "sessions", run=run, run_id=run_id, summary=summary),
        )

    @app.get("/runs/{run_id}/status", response_class=HTMLResponse)
    async def run_status_fragment(request: Request, run_id: str) -> HTMLResponse:
        payload = request.app.state.repository.get_run(run_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Run not found")
        run = payload["run"]
        if request.headers.get("HX-Request") == "true" and run["state"] in {"completed", "failed"}:
            return HTMLResponse("", headers={"HX-Redirect": f"/runs/{run_id}"})
        return templates.TemplateResponse(
            request,
            "partials/status_card.html",
            {"request": request, "run": run, "run_id": run_id},
        )

    @app.get("/sessions", response_class=HTMLResponse)
    async def sessions(request: Request) -> HTMLResponse:
        rows = request.app.state.repository.list_runs(limit=100)
        return templates.TemplateResponse(
            request,
            "sessions.html",
            _page_context(request, "sessions", runs=rows),
        )

    @app.get("/compare", response_class=HTMLResponse)
    async def compare(request: Request) -> HTMLResponse:
        rows = request.app.state.repository.list_runs(mode="comparison", limit=50)
        return templates.TemplateResponse(
            request,
            "compare.html",
            _page_context(request, "compare", runs=rows),
        )

    @app.get("/knowledge", response_class=HTMLResponse)
    async def knowledge(request: Request) -> HTMLResponse:
        summary = request.app.state.service.load_knowledge_summary()
        return templates.TemplateResponse(
            request,
            "knowledge.html",
            _page_context(request, "knowledge", summary=summary),
        )

    @app.get("/artifacts/{artifact_id}/download")
    async def download_artifact(request: Request, artifact_id: str) -> FileResponse:
        artifact = request.app.state.repository.get_artifact(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found")
        path = Path(artifact["path"])
        if not path.exists():
            raise HTTPException(status_code=404, detail="Artifact file no longer exists")
        return FileResponse(path=path, filename=path.name, media_type="application/octet-stream")

    return app


def _page_context(request: Request, active_nav: str, **extra: Any) -> dict[str, Any]:
    context = {
        "request": request,
        "active_nav": active_nav,
        "app_title": "IOptimal",
    }
    context.update(extra)
    return context


async def _persist_uploads(run_id: str, settings: AppSettings, uploads: list[UploadFile]) -> list[Path]:
    upload_dir = settings.upload_dir_for(run_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, upload in enumerate(uploads, start=1):
        if not upload.filename:
            continue
        safe_name = _safe_filename(upload.filename)
        suffix = Path(safe_name).suffix or ".ibt"
        stem = Path(safe_name).stem or f"session_{index}"
        destination = upload_dir / f"{index:02d}_{stem}{suffix}"
        with destination.open("wb") as file_obj:
            shutil.copyfileobj(upload.file, file_obj)
        await upload.close()
        paths.append(destination)
    return paths


def _safe_filename(filename: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._")
    return sanitized or "upload.ibt"


def _validate_run_request(run_request: RunCreateRequest) -> str | None:
    if run_request.mode == "single_session" and len(run_request.ibt_paths) != 1:
        return "Single Session requires exactly one IBT file."
    if run_request.mode == "comparison" and len(run_request.ibt_paths) < 2:
        return "Multi-Session Compare requires at least two IBT files."
    if run_request.mode == "track_solve":
        if not run_request.track:
            return "Track-Only Solve requires a track name."
        if run_request.wing is None:
            return "Track-Only Solve requires a wing angle."
    return None
