"""FastAPI application for the local IOptimal web interface."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from webapp.jobs import RunJobManager
from webapp.services import IOptimalWebService, list_supported_cars
from webapp.settings import AppSettings
from webapp.storage import RunRepository
from webapp.types import RunCreateRequest


def _build_supported_cars_grouped() -> dict[str, list[tuple[str, str]]]:
    """Group ``list_supported_cars()`` output by class for the runs_new
    template. GT3 Phase 2 W9.1 — F1 fix.
    """
    grouped: dict[str, list[tuple[str, str]]] = {}
    for canonical, display, klass in list_supported_cars():
        grouped.setdefault(klass, []).append((canonical, display))
    return grouped


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
        # Start auto-ingest background task if configured
        ingest_task = None
        try:
            from desktop.config import AppConfig
            config = AppConfig.load()
            if config.auto_ingest and config.telemetry_dir:
                ingest_task = asyncio.create_task(_auto_ingest_loop(config.telemetry_dir))
                logging.getLogger("webapp").info(
                    "Auto-ingest started — monitoring %s every 30s", config.telemetry_dir
                )
        except Exception:
            pass
        yield
        if ingest_task:
            ingest_task.cancel()
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
            _page_context(
                request,
                "new_run",
                selected_mode=selected_mode,
                error=error,
                supported_cars_grouped=_build_supported_cars_grouped(),
            ),
        )

    @app.post("/runs")
    async def create_run(
        request: Request,
        mode: str = Form("single_session"),
        # GT3 Phase 2 W9.1 — F3 fix. Car is now required (no GTP default).
        # Pre-W9.1 a missing car silently routed to BMW M Hybrid V8 which is
        # both a GTP car and the wrong physics for any GT3 IBT.
        car: str = Form(...),
        track: str | None = Form(None),
        wing: float | None = Form(None),
        lap: int | None = Form(None),
        fuel: float | None = Form(None),
        balance: float = Form(50.14),
        tolerance: float = Form(0.1),
        scenario_profile: str = Form("single_lap_safe"),
        free_opt: str | None = Form(None),
        use_learning: str | None = Form(None),
        synthesize: str | None = Form(None),
        ibt_files: list[UploadFile] | None = File(None),
    ) -> RedirectResponse:
        settings_local: AppSettings = request.app.state.settings
        run_id = uuid4().hex
        run_request = RunCreateRequest(
            mode=mode if mode in {"single_session", "comparison", "track_solve"} else "single_session",
            car=car.strip().lower(),
            ibt_paths=[],
            track=(track or "").strip() or None,
            wing=wing,
            lap=lap,
            fuel=fuel,
            balance=balance,
            tolerance=tolerance,
            scenario_profile=(scenario_profile or "single_lap_safe").strip() or "single_lap_safe",
            free_opt=free_opt is not None,
            use_learning=use_learning is not None,
            synthesize=synthesize is not None or mode != "comparison",
        )
        requires_uploads = run_request.mode in {"single_session", "comparison"}
        if requires_uploads:
            run_request.ibt_paths = await _persist_uploads(run_id, settings_local, ibt_files or [])
        error = _validate_run_request(run_request)
        if error is not None:
            _cleanup_uploads(run_id, settings_local)
            return RedirectResponse(
                url=f"/runs/new?mode={quote_plus(run_request.mode)}&error={quote_plus(error)}",
                status_code=303,
            )

        request.app.state.repository.create_run(run_id, run_request)
        request.app.state.jobs.submit(run_id, run_request)
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    @app.post("/runs/from-sessions")
    async def create_run_from_sessions(request: Request):
        """Create a run from already-ingested session IDs (no file upload needed)."""
        from learner.knowledge_store import KnowledgeStore
        body = await request.json()
        session_ids = body.get("session_ids", [])
        mode = body.get("mode", "single_session")
        scenario_profile = body.get("scenario_profile", "single_lap_safe")
        free_opt = body.get("free_opt", False)
        use_learning = body.get("use_learning", True)
        synthesize = body.get("synthesize", True)

        if not session_ids:
            return JSONResponse({"ok": False, "message": "No sessions selected"})
        if mode == "single_session" and len(session_ids) != 1:
            return JSONResponse({"ok": False, "message": "Single session mode requires exactly 1 session"})
        if mode == "comparison" and len(session_ids) < 2:
            return JSONResponse({"ok": False, "message": "Comparison mode requires at least 2 sessions"})

        store = KnowledgeStore()
        ibt_paths: list[Path] = []
        car = None
        errors = []
        for sid in session_ids:
            obs = store.load_observation(sid)
            if obs is None:
                errors.append(f"Session not found: {sid[:30]}...")
                continue
            ibt_p = Path(obs.get("ibt_path", ""))
            if not ibt_p.exists():
                errors.append(f"IBT file missing for {obs.get('car', '?')} {obs.get('track', '?')}: {ibt_p.name}")
                continue
            ibt_paths.append(ibt_p)
            if car is None:
                car = obs.get("car")

        if errors:
            return JSONResponse({"ok": False, "message": "; ".join(errors)})
        if not ibt_paths:
            return JSONResponse({"ok": False, "message": "No valid IBT files found"})
        if not car:
            # GT3 Phase 2 W9.1 — F3 fix. Pre-W9.1 we silently defaulted to
            # ``bmw`` here, which routed every observation without a car
            # field through the GTP BMW solver path. Surface the missing
            # field instead so the user / caller sees the failure.
            return JSONResponse(
                {"ok": False, "message": "No car identified on selected sessions; ingest with --car set."}
            )

        run_id = uuid4().hex
        run_request = RunCreateRequest(
            mode=mode if mode in {"single_session", "comparison"} else "single_session",
            car=car,
            ibt_paths=ibt_paths,
            track=None,
            wing=None,
            lap=None,
            fuel=None,
            balance=50.14,
            tolerance=0.1,
            scenario_profile=scenario_profile,
            free_opt=free_opt,
            use_learning=use_learning,
            synthesize=synthesize,
        )

        request.app.state.repository.create_run(run_id, run_request)
        request.app.state.jobs.submit(run_id, run_request)
        return JSONResponse({"ok": True, "run_id": run_id})

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

    # ── Telemetry browser ────────────────────────────────────────

    @app.get("/telemetry", response_class=HTMLResponse)
    async def telemetry_list(request: Request, car: str = "") -> HTMLResponse:
        observations, cars = _load_observation_list(car_filter=car)
        return templates.TemplateResponse(
            request,
            "telemetry_list.html",
            _page_context(request, "telemetry", observations=observations, cars=cars, car_filter=car),
        )

    @app.post("/telemetry/upload")
    async def telemetry_upload(request: Request, ibt_files: list[UploadFile] = File(...)):
        """Upload IBT files and ingest them into the knowledge base."""
        settings_local: AppSettings = request.app.state.settings
        upload_dir = settings_local.base_dir / "uploads" / "telemetry"
        upload_dir.mkdir(parents=True, exist_ok=True)
        ingested = 0
        errors = []
        for upload in ibt_files:
            if not upload.filename or not upload.filename.lower().endswith(".ibt"):
                errors.append(f"{upload.filename or 'unknown'}: not an .ibt file")
                continue
            dest = upload_dir / upload.filename
            content = await upload.read()
            dest.write_bytes(content)
            try:
                from watcher.service import WatcherService
                svc = WatcherService(telemetry_dir=str(upload_dir), auto_ingest=True)
                svc._handle_new_ibt(dest)
                ingested += 1
            except Exception as exc:
                errors.append(f"{upload.filename}: {exc}")
        msg = f"Ingested {ingested} file(s)"
        if errors:
            msg += f", {len(errors)} error(s): {'; '.join(errors[:3])}"
        return JSONResponse({"ok": ingested > 0 or not errors, "message": msg, "ingested": ingested})

    @app.get("/telemetry/browse-dir")
    async def telemetry_browse_dir(request: Request):
        """List .ibt files in the telemetry directory that haven't been ingested yet."""
        from desktop.config import AppConfig
        from learner.knowledge_store import KnowledgeStore
        config = AppConfig.load()
        if not config.telemetry_dir:
            return JSONResponse({"ok": False, "message": "No telemetry directory configured", "files": []})
        tdir = Path(config.telemetry_dir)
        if not tdir.exists():
            return JSONResponse({"ok": False, "message": f"Directory not found: {config.telemetry_dir}", "files": []})
        store = KnowledgeStore()
        index = store.load_index()
        known_sessions = set(index.get("sessions", []))
        ibt_files = sorted(tdir.glob("*.ibt"), key=lambda f: f.stat().st_mtime, reverse=True)
        file_list = []
        for f in ibt_files[:200]:
            # Check if any session ID contains this filename stem
            stem = f.stem.lower()
            already = any(stem in sid for sid in known_sessions)
            file_list.append({
                "name": f.name,
                "path": str(f),
                "size_mb": round(f.stat().st_size / 1048576, 1),
                "modified": f.stat().st_mtime,
                "ingested": already,
            })
        return JSONResponse({
            "ok": True,
            "message": f"{len(file_list)} files found, {sum(1 for f in file_list if f['ingested'])} already ingested",
            "files": file_list,
        })

    @app.post("/telemetry/ingest-file")
    async def telemetry_ingest_file(request: Request):
        """Ingest a specific .ibt file from the telemetry directory."""
        body = await request.json()
        file_path = body.get("path", "").strip()
        if not file_path:
            return JSONResponse({"ok": False, "message": "No file path provided"})
        p = Path(file_path)
        if not p.exists() or not p.suffix.lower() == ".ibt":
            return JSONResponse({"ok": False, "message": "File not found or not an .ibt file"})
        try:
            from watcher.service import WatcherService
            svc = WatcherService(telemetry_dir=str(p.parent), auto_ingest=True)
            svc._handle_new_ibt(p)
            return JSONResponse({"ok": True, "message": f"Ingested {p.name}"})
        except Exception as exc:
            return JSONResponse({"ok": False, "message": f"Error: {exc}"})

    @app.get("/telemetry/{session_id}/traces")
    async def telemetry_traces(request: Request, session_id: str):
        """Serve decimated IBT time-series for chart visualization."""
        from learner.knowledge_store import KnowledgeStore
        store = KnowledgeStore()
        obs = store.load_observation(session_id)
        if obs is None:
            return JSONResponse({"ok": False, "message": "Session not found"})
        ibt_path = Path(obs.get("ibt_path", ""))
        if not ibt_path.exists():
            return JSONResponse({"ok": False, "message": "IBT file not found on disk"})
        try:
            import numpy as np
            from track_model.ibt_parser import IBTFile
            ibt = IBTFile(str(ibt_path))
            indices = ibt.best_lap_indices()
            if indices is None:
                return JSONResponse({"ok": False, "message": "No valid lap found in IBT"})
            start, end = indices
            step = 10  # decimate 60Hz → ~6Hz (~660 points)

            def _ch(name: str, scale: float = 1.0) -> list:
                arr = ibt.channel(name)
                if arr is None:
                    return []
                return (arr[start:end:step] * scale).tolist()

            channels = {
                "speed": _ch("Speed", 3.6),
                "throttle": _ch("Throttle", 100),
                "brake": _ch("Brake", 100),
                "steering": _ch("SteeringWheelAngle"),
                "lat_g": _ch("LatAccel"),
                "long_g": _ch("LongAccel"),
                "front_rh": _ch("LFrideHeight", 1000),
                "rear_rh": _ch("RRrideHeight", 1000),
                "lap_dist_pct": _ch("LapDistPct", 100),
            }
            return JSONResponse({"ok": True, "channels": channels, "sample_count": len(channels["speed"])})
        except Exception as exc:
            return JSONResponse({"ok": False, "message": f"Error reading IBT: {exc}"})

    # NOTE: {session_id} wildcard MUST come after all /telemetry/specific-path routes
    @app.get("/telemetry/{session_id}", response_class=HTMLResponse)
    async def telemetry_detail(request: Request, session_id: str) -> HTMLResponse:
        detail = _load_observation_detail(session_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return templates.TemplateResponse(
            request,
            "telemetry_detail.html",
            _page_context(request, "telemetry", obs=detail),
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

    # ── Team pages ─────────────────────────────────────────────────

    @app.get("/team", response_class=HTMLResponse)
    async def team_dashboard(request: Request) -> HTMLResponse:
        from desktop.config import AppConfig
        config = AppConfig.load()
        team_data = _load_team_data(config)
        return templates.TemplateResponse(
            request,
            "team_dashboard.html",
            _page_context(request, "team", **team_data),
        )

    @app.get("/team/setups", response_class=HTMLResponse)
    async def team_setups(request: Request) -> HTMLResponse:
        from desktop.config import AppConfig
        config = AppConfig.load()
        setups = _fetch_team_setups(config)
        return templates.TemplateResponse(
            request,
            "team_setups.html",
            _page_context(request, "team", setups=setups, cars=[], tracks=[]),
        )

    @app.get("/team/leaderboard", response_class=HTMLResponse)
    async def team_leaderboard(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "team_leaderboard.html",
            _page_context(request, "team", entries=[], cars=[], tracks=[]),
        )

    @app.get("/team/cars", response_class=HTMLResponse)
    async def team_cars(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "team_cars.html",
            _page_context(request, "team", cars=[]),
        )

    @app.get("/team/knowledge", response_class=HTMLResponse)
    async def team_knowledge(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "team_knowledge.html",
            _page_context(request, "team", knowledge=[]),
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> HTMLResponse:
        from desktop.config import AppConfig
        from learner.knowledge_store import KnowledgeStore
        config = AppConfig.load()
        obs_count = KnowledgeStore().session_count()
        return templates.TemplateResponse(
            request,
            "settings.html",
            _page_context(request, "settings", config=config, obs_count=obs_count),
        )

    @app.post("/settings", response_class=HTMLResponse)
    async def save_settings(
        request: Request,
        team_server_url: str = Form(""),
        api_key: str = Form(""),
        invite_code: str = Form(""),
        iracing_name: str = Form(""),
        telemetry_dir: str = Form(""),
        auto_ingest: bool = Form(False),
        auto_sync: bool = Form(False),
        push_interval: int = Form(5),
        pull_interval: int = Form(5),
        sound_enabled: bool = Form(False),
        browser_open_on_start: bool = Form(False),
    ) -> RedirectResponse:
        from desktop.config import AppConfig
        config = AppConfig.load()
        config.team_server_url = team_server_url
        if api_key:
            config.api_key = api_key
        config.invite_code = invite_code
        config.iracing_name = iracing_name
        if telemetry_dir:
            config.telemetry_dir = telemetry_dir
        config.auto_ingest = auto_ingest
        config.auto_sync = auto_sync
        config.push_interval = push_interval
        config.pull_interval = pull_interval
        config.sound_enabled = sound_enabled
        config.browser_open_on_start = browser_open_on_start
        config.save()
        return RedirectResponse(url="/settings", status_code=302)

    # ── Settings action endpoints ─────────────────────────────────

    @app.post("/settings/test-connection")
    async def test_connection(request: Request):
        import httpx
        from desktop.config import AppConfig
        config = AppConfig.load()
        if not config.team_server_url:
            return JSONResponse({"ok": False, "message": "No server URL configured"})
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                headers = {}
                if config.api_key:
                    headers["Authorization"] = f"Bearer {config.api_key}"
                resp = await client.get(f"{config.team_server_url.rstrip('/')}/api/health", headers=headers)
                if resp.status_code == 200:
                    # Also test auth if we have a key
                    if config.api_key:
                        stats_resp = await client.get(
                            f"{config.team_server_url.rstrip('/')}/api/stats",
                            headers=headers,
                        )
                        if stats_resp.status_code == 200:
                            data = stats_resp.json()
                            return JSONResponse({
                                "ok": True,
                                "message": f"Connected! {data.get('total_members', 0)} members, {data.get('total_observations', 0)} observations",
                            })
                        elif stats_resp.status_code == 401:
                            return JSONResponse({"ok": False, "message": "Server reachable but API key is invalid"})
                    return JSONResponse({"ok": True, "message": "Server is reachable"})
                return JSONResponse({"ok": False, "message": f"Server returned {resp.status_code}"})
        except httpx.ConnectError:
            return JSONResponse({"ok": False, "message": "Cannot reach server — check URL"})
        except Exception as e:
            return JSONResponse({"ok": False, "message": str(e)})

    @app.post("/settings/join-team")
    async def join_team_action(request: Request):
        import httpx
        from desktop.config import AppConfig
        body = await request.json()
        invite_code = body.get("invite_code", "").strip()
        iracing_name = body.get("iracing_name", "").strip()
        config = AppConfig.load()
        if not config.team_server_url:
            return JSONResponse({"ok": False, "message": "Set the server URL first"})
        if not invite_code or not iracing_name:
            return JSONResponse({"ok": False, "message": "Invite code and iRacing name are required"})
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{config.team_server_url.rstrip('/')}/api/team/join",
                    json={"invite_code": invite_code, "iracing_name": iracing_name},
                )
                if resp.status_code == 201:
                    data = resp.json()
                    config.api_key = data["api_key"]
                    config.invite_code = invite_code
                    config.iracing_name = iracing_name
                    config.save()
                    return JSONResponse({"ok": True, "message": f"Joined! Your member ID: {data['member_id'][:8]}…"})
                elif resp.status_code == 404:
                    return JSONResponse({"ok": False, "message": "Invalid invite code"})
                return JSONResponse({"ok": False, "message": f"Server returned {resp.status_code}: {resp.text}"})
        except Exception as e:
            return JSONResponse({"ok": False, "message": str(e)})

    @app.post("/settings/bulk-import")
    async def bulk_import_action(request: Request):
        from desktop.config import AppConfig
        config = AppConfig.load()
        telemetry_dir = config.telemetry_dir
        if not telemetry_dir:
            return JSONResponse({"ok": False, "message": "No telemetry directory configured"})

        from pathlib import Path
        tdir = Path(telemetry_dir)
        if not tdir.exists():
            return JSONResponse({"ok": False, "message": f"Directory not found: {telemetry_dir}"})

        ibt_files = list(tdir.glob("*.ibt"))
        if not ibt_files:
            return JSONResponse({"ok": False, "message": "No .ibt files found in telemetry directory"})

        # Run bulk import in background via watcher service
        try:
            from watcher.service import WatcherService
            svc = WatcherService(telemetry_dir=str(tdir), auto_ingest=True)
            results = svc.bulk_import(limit=500)
            success = sum(1 for r in results if r.fully_ingested)
            return JSONResponse({
                "ok": True,
                "message": f"Imported {success}/{len(results)} sessions ({len(ibt_files)} .ibt files found)",
            })
        except Exception as e:
            return JSONResponse({"ok": False, "message": f"Import error: {e}"})

    return app


async def _auto_ingest_loop(telemetry_dir: str, interval: int = 30) -> None:
    """Periodically scan for new IBT files and ingest them."""
    logger = logging.getLogger("webapp.autoingest")
    from learner.knowledge_store import KnowledgeStore
    known_files: set[str] = set()
    while True:
        try:
            await asyncio.sleep(interval)
            tdir = Path(telemetry_dir)
            if not tdir.exists():
                continue
            ibt_files = list(tdir.glob("*.ibt"))
            new_files = [f for f in ibt_files if str(f) not in known_files]
            if not new_files:
                continue
            # Mark all as known to avoid reprocessing
            for f in new_files:
                known_files.add(str(f))
            try:
                from watcher.service import WatcherService
                svc = WatcherService(telemetry_dir=telemetry_dir, auto_ingest=True)
                results = svc.bulk_import(limit=10)
                ingested = sum(1 for r in results if r.fully_ingested)
                if ingested:
                    logger.info("Auto-ingested %d new session(s)", ingested)
            except Exception as exc:
                logger.warning("Auto-ingest error: %s", exc)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Auto-ingest loop error: %s", exc)


def _page_context(request: Request, active_nav: str, **extra: Any) -> dict[str, Any]:
    context = {
        "request": request,
        "active_nav": active_nav,
        "app_title": "IOptimal",
    }
    context.update(extra)
    return context


def _load_team_data(config) -> dict:
    """Load team data from sync client or return empty defaults."""
    defaults = {
        "team_name": config.team_name or "(Not connected)",
        "total_members": 0,
        "total_sessions": 0,
        "cars_tracked": 0,
        "tracks_covered": 0,
        "activity": [],
        "recent_sessions": [],
    }
    if not config.is_team_configured:
        return defaults
    try:
        import httpx
        resp = httpx.get(
            f"{config.team_server_url.rstrip('/')}/api/stats",
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "team_name": data.get("team_name", config.team_name or "My Team"),
                "total_members": data.get("total_members", 0),
                "total_sessions": data.get("total_observations", 0),
                "cars_tracked": len(data.get("cars", [])),
                "tracks_covered": len(data.get("tracks", [])),
                "activity": data.get("recent_activity", []),
                "recent_sessions": data.get("recent_sessions", []),
            }
    except Exception:
        pass
    return defaults


def _fetch_team_setups(config) -> list:
    """Fetch shared setups from team server."""
    if not config.is_team_configured:
        return []
    try:
        import httpx
        resp = httpx.get(
            f"{config.team_server_url.rstrip('/')}/api/setups",
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json().get("setups", [])
    except Exception:
        pass
    return []


def _load_observation_list(car_filter: str = "") -> tuple[list[dict], list[str]]:
    """Load lightweight observation summaries from the knowledge store."""
    from learner.knowledge_store import KnowledgeStore
    store = KnowledgeStore()
    all_obs = store.list_observations(car=car_filter)
    cars: set[str] = set()
    items: list[dict] = []
    for obs in all_obs:
        cars.add(obs.get("car", "unknown"))
        perf = obs.get("performance", {})
        items.append({
            "session_id": obs.get("session_id", ""),
            "car": obs.get("car", "unknown"),
            "track": obs.get("track", "Unknown"),
            "best_lap": perf.get("best_lap_time_s"),
            "consistency": perf.get("consistency_cv"),
            "max_speed": perf.get("max_speed_kph"),
            "timestamp": obs.get("timestamp", obs.get("ibt_date", "")),
        })
    # Sort newest first
    items.sort(key=lambda x: x["timestamp"] or "", reverse=True)
    return items, sorted(cars)


def _load_observation_detail(session_id: str) -> dict | None:
    """Load full observation detail for the telemetry detail page."""
    from learner.knowledge_store import KnowledgeStore
    store = KnowledgeStore()
    obs = store.load_observation(session_id)
    if obs is None:
        return None

    perf = obs.get("performance", {})
    tel = obs.get("telemetry", {})
    dp = obs.get("driver_profile", {})
    diag = obs.get("diagnosis", {})
    setup = obs.get("setup", {})

    # Setup groups for display
    setup_groups = _build_obs_setup_groups(setup)

    # Tyre pressure data (per corner)
    corners = [("LF", "lf"), ("RF", "rf"), ("LR", "lr"), ("RR", "rr")]
    tyre_pressures = []
    for label, prefix in corners:
        hot = tel.get(f"{prefix}_pressure_kpa")
        cold = tel.get(f"{prefix}_cold_pressure_kpa")
        buildup = ""
        if hot is not None and cold is not None:
            try:
                buildup = f"+{hot - cold:.1f}"
            except (TypeError, ValueError):
                pass
        tyre_pressures.append({
            "corner": label,
            "hot": f"{hot:.1f}" if hot is not None else "-",
            "cold": f"{cold:.1f}" if cold is not None else "-",
            "buildup": buildup or "-",
        })

    tyre_wear = []
    for label, prefix in corners:
        temp = tel.get(f"{prefix}_temp_middle_c")
        wear = tel.get(f"{prefix}_wear_pct")
        tyre_wear.append({
            "corner": label,
            "temp": f"{temp:.1f}" if temp is not None else "-",
            "wear": f"{wear:.1f}" if wear is not None else "-",
        })

    # Key telemetry metrics
    metric_specs = [
        ("Front heave travel used", "front_heave_travel_used_pct", "%", 1),
        ("Front ride height std", "front_rh_std_mm", "mm", 2),
        ("Rear ride height std", "rear_rh_std_mm", "mm", 2),
        ("Front excursion", "front_excursion_mm", "mm", 1),
        ("Braking pitch", "braking_pitch_deg", "deg", 2),
        ("Body slip p95", "body_slip_p95_deg", "deg", 2),
        ("Understeer mean", "understeer_mean_deg", "deg", 2),
        ("Peak lateral g", "peak_lat_g", "g", 2),
        ("Front bottoming events", "bottoming_event_count_front", "", 0),
        ("Rear bottoming events", "bottoming_event_count_rear", "", 0),
        ("Front brake pressure peak", "front_brake_pressure_peak_bar", "bar", 1),
        ("Front pressure mean", "front_pressure_mean_kpa", "kPa", 1),
        ("Rear pressure mean", "rear_pressure_mean_kpa", "kPa", 1),
    ]
    telemetry_metrics = []
    for label, key, units, digits in metric_specs:
        val = tel.get(key)
        if val is not None:
            try:
                telemetry_metrics.append({"label": label, "value": f"{val:.{digits}f} {units}".strip()})
            except (TypeError, ValueError):
                telemetry_metrics.append({"label": label, "value": str(val)})

    # Driver profile attributes
    driver_attr_specs = [
        ("Trail braking depth", "trail_braking_depth"),
        ("Trail braking class", "trail_braking_class"),
        ("Throttle progressiveness", "throttle_progressiveness"),
        ("Throttle classification", "throttle_classification"),
        ("Steering smoothness", "steering_smoothness"),
        ("Apex speed consistency", "apex_speed_cv"),
        ("Cornering aggression", "cornering_aggression"),
        ("Consistency", "consistency"),
        ("Driver noise index", "driver_noise_index"),
    ]
    driver_attrs = []
    for label, key in driver_attr_specs:
        val = dp.get(key)
        if val is not None:
            if isinstance(val, float):
                driver_attrs.append({"label": label, "value": f"{val:.3f}"})
            else:
                driver_attrs.append({"label": label, "value": str(val)})

    # Problems
    problems = []
    for p in (diag.get("problems") or [])[:8]:
        problems.append({
            "severity": str(p.get("severity", "note")).title(),
            "symptom": str(p.get("symptom", "")),
            "cause": str(p.get("cause", "")),
            "speed_context": str(p.get("speed_context", "all")),
        })

    return {
        "session_id": session_id,
        "car": obs.get("car", "unknown"),
        "track": obs.get("track", "Unknown"),
        "timestamp": obs.get("timestamp", obs.get("ibt_date", "")),
        "lap_number": perf.get("lap_number"),
        "best_lap": perf.get("best_lap_time_s"),
        "median_speed": perf.get("median_speed_kph"),
        "max_speed": perf.get("max_speed_kph"),
        "consistency": perf.get("consistency_cv"),
        "driver_class": dp.get("style") or dp.get("classification", ""),
        "diagnosis": diag.get("assessment", ""),
        "problems": problems,
        "setup_groups": setup_groups,
        "tyre_pressures": tyre_pressures,
        "tyre_wear": tyre_wear,
        "air_temp": tel.get("air_temp_c"),
        "track_temp": tel.get("track_temp_c"),
        "telemetry_metrics": telemetry_metrics,
        "driver_attrs": driver_attrs,
        # Raw data for chart visualizations
        "corner_performance": obs.get("corner_performance", []),
        "driver_profile_raw": dp,
        "ibt_path": obs.get("ibt_path", ""),
    }


def _build_obs_setup_groups(setup: dict) -> list[dict]:
    """Build setup display groups from a raw observation setup dict."""
    groups = [
        {
            "name": "Platform",
            "params": [
                ("Wing angle", setup.get("wing"), "deg"),
                ("Front pushrod", setup.get("front_pushrod"), "mm"),
                ("Rear pushrod", setup.get("rear_pushrod"), "mm"),
                ("Front ride height", setup.get("front_rh_static"), "mm"),
                ("Rear ride height", setup.get("rear_rh_static"), "mm"),
                ("Front heave", setup.get("front_heave_nmm"), "N/mm"),
                ("Rear third", setup.get("rear_third_nmm"), "N/mm"),
                ("Front torsion OD", setup.get("torsion_bar_od_mm"), "mm"),
                ("Rear spring", setup.get("rear_spring_nmm"), "N/mm"),
            ],
        },
        {
            "name": "Balance",
            "params": [
                ("Front ARB blade", setup.get("front_arb_blade"), ""),
                ("Rear ARB blade", setup.get("rear_arb_blade"), ""),
                ("Brake bias", setup.get("brake_bias_pct"), "%"),
                ("Diff preload", setup.get("diff_preload_nm"), "Nm"),
            ],
        },
        {
            "name": "Geometry",
            "params": [
                ("Front camber", setup.get("front_camber_deg"), "deg"),
                ("Rear camber", setup.get("rear_camber_deg"), "deg"),
                ("Front toe", setup.get("front_toe_mm"), "mm"),
                ("Rear toe", setup.get("rear_toe_mm"), "mm"),
            ],
        },
        {
            "name": "Driver Aids",
            "params": [
                ("TC gain", setup.get("tc_gain"), ""),
                ("TC slip", setup.get("tc_slip"), ""),
            ],
        },
    ]
    # Format values
    for group in groups:
        formatted = []
        for item in group["params"]:
            label, val, units = item
            if val is not None:
                if isinstance(val, float):
                    formatted.append({"label": label, "value": f"{val:.2f} {units}".strip()})
                else:
                    formatted.append({"label": label, "value": f"{val} {units}".strip()})
            else:
                formatted.append({"label": label, "value": "-"})
        group["params"] = formatted
    return groups


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


def _cleanup_uploads(run_id: str, settings: AppSettings) -> None:
    upload_dir = settings.upload_dir_for(run_id)
    if upload_dir.exists():
        shutil.rmtree(upload_dir, ignore_errors=True)


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
