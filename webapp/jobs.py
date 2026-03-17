"""Single-worker background job execution for the local web app."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from webapp.services import IOptimalWebService
from webapp.storage import RunRepository, utc_now_iso
from webapp.types import ArtifactLinkView, RunCreateRequest


class RunJobManager:
    """Executes one run at a time and persists state changes in SQLite."""

    def __init__(self, repository: RunRepository, service: IOptimalWebService):
        self.repository = repository
        self.service = service
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ioptimal-web")
        self._futures: dict[str, Future[Any]] = {}
        self._lock = threading.Lock()

    def submit(self, run_id: str, request: RunCreateRequest) -> None:
        with self._lock:
            self._futures[run_id] = self._executor.submit(self._run_job, run_id, request)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _run_job(self, run_id: str, request: RunCreateRequest) -> None:
        self.repository.update_run(
            run_id,
            state="running",
            phase="Preparing",
            started_at=utc_now_iso(),
        )
        try:
            kind, summary_payload, artifacts = self.service.execute_run(
                run_id,
                request,
                lambda phase: self.repository.update_run(run_id, state="running", phase=phase),
            )
            artifact_links = []
            for artifact in artifacts:
                artifact_id = uuid4().hex
                self.repository.save_artifact(artifact_id, run_id, artifact.kind, artifact.label, artifact.path)
                artifact_links.append(asdict(ArtifactLinkView(id=artifact_id, label=artifact.label, kind=artifact.kind)))
            summary_payload["artifact_links"] = artifact_links
            self.repository.save_summary(run_id, kind, summary_payload)
            self.repository.update_run(
                run_id,
                state="completed",
                phase="Complete",
                finished_at=utc_now_iso(),
            )
        except Exception as exc:  # pragma: no cover - covered via route behaviour
            self.repository.update_run(
                run_id,
                state="failed",
                phase="Failed",
                finished_at=utc_now_iso(),
                error=str(exc),
            )
            raise
