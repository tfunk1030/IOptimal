"""Sync client — pushes observations to team server, pulls knowledge.

Runs as a background thread in the desktop app.  Handles offline queuing,
retry with exponential backoff, and periodic model pulls.
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_DEFAULT_PUSH_INTERVAL_S = 30
_DEFAULT_PULL_INTERVAL_S = 300  # 5 minutes
_MAX_RETRY_BACKOFF_S = 600  # 10 minutes
_QUEUE_CLEANUP_AGE_DAYS = 30
_QUEUE_CLEANUP_INTERVAL_S = 24 * 60 * 60  # once per day


@dataclass
class SyncStatus:
    """Current sync status."""

    connected: bool = False
    last_push: str | None = None
    last_pull: str | None = None
    queued_observations: int = 0
    total_pushed: int = 0
    total_pulled_models: int = 0
    last_error: str | None = None


class SyncClient:
    """Background sync client for team server communication.

    Pushes queued observations to the team server and periodically pulls
    updated empirical models for local use by the solver.

    Parameters
    ----------
    server_url:
        Base URL of the team server (e.g., "https://ioptimal-server-xxx.run.app").
    api_key:
        Member API key for authentication.
    local_db_path:
        Path to local SQLite database for offline queue persistence.
    push_interval:
        How often (seconds) to attempt pushing queued observations.
    pull_interval:
        How often (seconds) to pull updated models from the server.
    """

    def __init__(
        self,
        server_url: str,
        api_key: str,
        *,
        local_db_path: Path | str | None = None,
        push_interval: float = _DEFAULT_PUSH_INTERVAL_S,
        pull_interval: float = _DEFAULT_PULL_INTERVAL_S,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self._api_key = api_key
        self._push_interval = push_interval
        self._pull_interval = pull_interval
        self._queue: queue.Queue = queue.Queue()
        self._status = SyncStatus()
        self._last_push_failed = False
        self._stop_event = threading.Event()
        self._push_thread: threading.Thread | None = None
        self._pull_thread: threading.Thread | None = None

        # Local SQLite for offline queue persistence
        db_path = Path(local_db_path) if local_db_path else Path.home() / ".ioptimal_app" / "sync_queue.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._last_cleanup_ts = 0.0
        self._init_local_db()

    @property
    def status(self) -> SyncStatus:
        return self._status

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _request_error_type() -> type[Exception]:
        if httpx is None:
            return OSError
        return httpx.RequestError

    # ── Local offline queue (SQLite) ──────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        """Open the local SQLite DB in WAL journal mode.

        WAL is safer than the default rollback journal for concurrent reads /
        writes, especially on network-mounted Documents folders where multiple
        IOptimal processes (watcher, desktop, sync client) may touch the same
        file.
        """
        conn = sqlite3.connect(str(self._db_path))
        # WAL persists across connections once set, but cheap to re-issue.
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
        except sqlite3.DatabaseError as exc:
            logger.debug("PRAGMA setup skipped: %s", exc)
        return conn

    def _init_local_db(self) -> None:
        """Create the local sync queue table if it doesn't exist."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    synced INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    processed_at TEXT
                )
            """)
            # Backfill columns on pre-existing DBs.
            cols = {row[1] for row in conn.execute("PRAGMA table_info(sync_queue)").fetchall()}
            if "status" not in cols:
                conn.execute("ALTER TABLE sync_queue ADD COLUMN status TEXT DEFAULT 'pending'")
            if "processed_at" not in cols:
                conn.execute("ALTER TABLE sync_queue ADD COLUMN processed_at TEXT")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pulled_models (
                    car TEXT NOT NULL,
                    track TEXT NOT NULL,
                    model_json TEXT NOT NULL,
                    updated_at TEXT DEFAULT (datetime('now')),
                    PRIMARY KEY (car, track)
                )
            """)

    def queue_observation(self, observation_dict: dict) -> None:
        """Queue an observation for sync.  Persists to local SQLite."""
        payload = json.dumps(observation_dict, default=str)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sync_queue (payload_type, payload_json) VALUES (?, ?)",
                ("observation", payload),
            )
        self._status.queued_observations += 1
        logger.debug("Observation queued for sync (total queued: %d)", self._status.queued_observations)

    def queue_setup(self, setup_dict: dict) -> None:
        """Queue a shared setup for sync."""
        payload = json.dumps(setup_dict, default=str)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sync_queue (payload_type, payload_json) VALUES (?, ?)",
                ("setup", payload),
            )
        self._status.queued_observations += 1

    # ── Push logic ────────────────────────────────────────────────────

    def _push_pending(self) -> int:
        """Push all pending items from the local queue.  Returns count pushed."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, payload_type, payload_json FROM sync_queue WHERE synced = 0 ORDER BY id LIMIT 50"
            ).fetchall()

        if not rows:
            return 0

        if httpx is None:
            self._last_push_failed = True
            self._status.connected = False
            self._status.last_error = "httpx dependency is missing; install httpx to enable sync push."
            return 0

        pushed = 0
        failed = False
        with httpx.Client(timeout=30) as client:
            for row_id, payload_type, payload_json in rows:
                endpoint = self._endpoint_for_type(payload_type)
                if not endpoint:
                    failed = True
                    self._status.last_error = f"Unknown payload_type: {payload_type}"
                    continue

                try:
                    resp = client.post(
                        f"{self._server_url}{endpoint}",
                        headers=self._headers(),
                        content=payload_json,
                    )
                    if resp.status_code in (200, 201, 409):
                        # 409 = duplicate, still mark as synced
                        self._mark_row_processed(row_id, "success")
                        pushed += 1
                    else:
                        logger.warning("Push failed (HTTP %d): %s", resp.status_code, resp.text[:200])
                        self._status.last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                        # 4xx are non-retryable; 5xx leaves the row pending for retry.
                        if 400 <= resp.status_code < 500:
                            self._mark_row_processed(row_id, "failed")
                        failed = True
                        break  # Stop on first failure to preserve order
                except self._request_error_type() as e:
                    logger.warning("Push failed (network): %s", e)
                    self._status.connected = False
                    self._status.last_error = str(e)
                    failed = True
                    break

        self._last_push_failed = failed
        if pushed > 0:
            self._status.total_pushed += pushed
            self._status.queued_observations = self._count_pending_queue_items()
            self._status.connected = True
            self._status.last_error = None
            from datetime import datetime, timezone
            self._status.last_push = datetime.now(timezone.utc).isoformat()
            logger.info("Pushed %d/%d items to team server", pushed, len(rows))

        return pushed

    def _count_pending_queue_items(self) -> int:
        """Count unsynced rows in the local queue."""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM sync_queue WHERE synced = 0").fetchone()
        return int(row[0] if row else 0)

    def _mark_row_processed(self, row_id: int, status: str) -> None:
        """Mark a queue row as ``success`` / ``failed`` with a timestamp."""
        with self._connect() as conn:
            conn.execute(
                """UPDATE sync_queue
                   SET synced = 1, status = ?, processed_at = datetime('now')
                   WHERE id = ?""",
                (status, row_id),
            )

    def _cleanup_old_rows(self, max_age_days: int = _QUEUE_CLEANUP_AGE_DAYS) -> int:
        """Delete processed rows older than ``max_age_days``.  Returns rows deleted."""
        with self._connect() as conn:
            cur = conn.execute(
                f"""DELETE FROM sync_queue
                    WHERE status IN ('success', 'failed')
                      AND processed_at IS NOT NULL
                      AND processed_at < datetime('now', '-{int(max_age_days)} days')"""
            )
            deleted = cur.rowcount or 0
        if deleted:
            logger.info("Cleaned up %d processed sync_queue rows older than %dd", deleted, max_age_days)
        return deleted

    def _maybe_cleanup_queue(self) -> None:
        """Run ``_cleanup_old_rows`` at most once per ``_QUEUE_CLEANUP_INTERVAL_S``."""
        now = time.monotonic()
        if now - self._last_cleanup_ts < _QUEUE_CLEANUP_INTERVAL_S:
            return
        self._last_cleanup_ts = now
        try:
            self._cleanup_old_rows()
        except sqlite3.DatabaseError:
            logger.exception("sync_queue cleanup failed")

    def _endpoint_for_type(self, payload_type: str) -> str | None:
        return {
            "observation": "/api/observations",
            "setup": "/api/setups/share",
        }.get(payload_type)

    # ── Pull logic ────────────────────────────────────────────────────

    def _pull_models(self) -> int:
        """Pull updated empirical models from the team server.  Returns count pulled."""
        pulled = 0
        if httpx is None:
            self._status.connected = False
            self._status.last_error = "httpx dependency is missing; install httpx to enable sync pull."
            return 0
        try:
            with httpx.Client(timeout=30) as client:
                # First get team stats to know which car/track pairs exist
                resp = client.get(
                    f"{self._server_url}/api/stats",
                    headers=self._headers(),
                )
                if resp.status_code != 200:
                    return 0

                stats = resp.json()
                self._status.connected = True

                # Pull models for each car/track pair
                for car_info in stats.get("cars", []):
                    car = car_info.get("car_name", "")
                    for track in car_info.get("tracks", []):
                        try:
                            model_resp = client.get(
                                f"{self._server_url}/api/knowledge/{car}/{track}",
                                headers=self._headers(),
                            )
                            if model_resp.status_code == 200:
                                model_data = model_resp.json()
                                self._store_pulled_model(car, track, model_data)
                                pulled += 1
                        except self._request_error_type():
                            continue

        except self._request_error_type() as e:
            logger.warning("Pull failed (network): %s", e)
            self._status.connected = False
            self._status.last_error = str(e)

        if pulled > 0:
            self._status.total_pulled_models += pulled
            from datetime import datetime, timezone
            self._status.last_pull = datetime.now(timezone.utc).isoformat()
            logger.info("Pulled %d models from team server", pulled)

        return pulled

    def _store_pulled_model(self, car: str, track: str, model_data: dict) -> None:
        """Store a pulled model in local SQLite for the solver to use."""
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO pulled_models (car, track, model_json, updated_at)
                   VALUES (?, ?, ?, datetime('now'))""",
                (car, track, json.dumps(model_data, default=str)),
            )

    def get_team_model(self, car: str, track: str) -> dict | None:
        """Retrieve a pulled team model for local solver use."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT model_json FROM pulled_models WHERE car = ? AND track = ?",
                (car, track),
            ).fetchone()
        if row:
            return json.loads(row[0])
        return None

    # ── Background threads ────────────────────────────────────────────

    def start(self) -> None:
        """Start background push and pull threads."""
        self._stop_event.clear()
        self._push_thread = threading.Thread(target=self._push_loop, daemon=True, name="sync-push")
        self._pull_thread = threading.Thread(target=self._pull_loop, daemon=True, name="sync-pull")
        self._push_thread.start()
        self._pull_thread.start()
        logger.info("Sync client started (push=%ds, pull=%ds)", self._push_interval, self._pull_interval)

    def stop(self) -> None:
        """Stop background threads."""
        self._stop_event.set()
        if self._push_thread:
            self._push_thread.join(timeout=5)
        if self._pull_thread:
            self._pull_thread.join(timeout=5)
        logger.info("Sync client stopped.")

    def _push_loop(self) -> None:
        """Background push loop with exponential backoff on failure."""
        backoff = self._push_interval
        while not self._stop_event.is_set():
            try:
                pushed = self._push_pending()
                if pushed > 0:
                    backoff = self._push_interval  # Reset on success
                elif self._last_push_failed:
                    backoff = min(backoff * 2, _MAX_RETRY_BACKOFF_S)
                else:
                    backoff = self._push_interval
                self._maybe_cleanup_queue()
            except Exception:
                logger.exception("Push loop error")
                backoff = min(backoff * 2, _MAX_RETRY_BACKOFF_S)

            self._stop_event.wait(timeout=backoff)

    def _pull_loop(self) -> None:
        """Background pull loop."""
        # Initial pull on startup
        self._pull_models()

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._pull_interval)
            if not self._stop_event.is_set():
                try:
                    self._pull_models()
                except Exception:
                    logger.exception("Pull loop error")

    # ── Convenience ───────────────────────────────────────────────────

    def force_sync(self) -> tuple[int, int]:
        """Force an immediate push + pull cycle.  Returns (pushed, pulled)."""
        pushed = self._push_pending()
        pulled = self._pull_models()
        return pushed, pulled
