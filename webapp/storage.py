"""SQLite-backed metadata store for the web UI."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from webapp.types import RunCreateRequest


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class RunRepository:
    """Persists run metadata, cached summaries, and artifact paths."""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    state TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    car TEXT,
                    track TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_inputs (
                    run_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs (id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs (id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS result_summaries (
                    run_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs (id) ON DELETE CASCADE
                )
                """
            )

    def create_run(self, run_id: str, request: RunCreateRequest) -> None:
        payload_json = json.dumps(asdict(request), default=_json_default, indent=2)
        created_at = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (id, mode, state, phase, car, track, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    request.mode,
                    "queued",
                    "Queued",
                    request.car,
                    request.track,
                    created_at,
                ),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO run_inputs (run_id, payload_json)
                VALUES (?, ?)
                """,
                (run_id, payload_json),
            )

    def update_run(
        self,
        run_id: str,
        *,
        state: str | None = None,
        phase: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        error: str | None = None,
    ) -> None:
        fields: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("state", state),
            ("phase", phase),
            ("started_at", started_at),
            ("finished_at", finished_at),
            ("error", error),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(value)
        if not fields:
            return
        values.append(run_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE runs SET {', '.join(fields)} WHERE id = ?",
                values,
            )

    def save_summary(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        updated_at = utc_now_iso()
        payload_json = json.dumps(payload, default=_json_default, indent=2)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO result_summaries (run_id, kind, payload_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    kind = excluded.kind,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (run_id, kind, payload_json, updated_at),
            )

    def save_artifact(self, artifact_id: str, run_id: str, kind: str, label: str, path: Path) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO artifacts (id, run_id, kind, label, path, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, run_id, kind, label, str(path), utc_now_iso()),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            run_row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if run_row is None:
                return None
            input_row = conn.execute(
                "SELECT payload_json FROM run_inputs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            summary_row = conn.execute(
                "SELECT kind, payload_json, updated_at FROM result_summaries WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            artifact_rows = conn.execute(
                "SELECT id, kind, label, path, created_at FROM artifacts WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        payload = json.loads(input_row["payload_json"]) if input_row else None
        summary = None
        if summary_row:
            summary = {
                "kind": summary_row["kind"],
                "payload": json.loads(summary_row["payload_json"]),
                "updated_at": summary_row["updated_at"],
            }
        return {
            "run": dict(run_row),
            "input": payload,
            "summary": summary,
            "artifacts": [dict(row) for row in artifact_rows],
        }

    def list_runs(self, *, mode: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM runs"
        params: list[Any] = []
        if mode is not None:
            query += " WHERE mode = ?"
            params.append(mode)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, run_id, kind, label, path, created_at FROM artifacts WHERE id = ?",
                (artifact_id,),
            ).fetchone()
        return None if row is None else dict(row)

    @contextmanager
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        finally:
            conn.close()
