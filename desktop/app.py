"""Main desktop application — orchestrates all background services.

Entry point for the packaged desktop app.  Starts:
1. IBT file watcher (auto-detect and ingest telemetry)
2. Sync client (push observations, pull team knowledge)
3. Local web UI (FastAPI on localhost)
4. System tray icon (notifications and quick actions)

Usage:
    python -m desktop          # Start the desktop app
    python -m desktop --no-tray   # Start without tray icon (headless)
    python -m desktop --bulk-import  # Import all existing IBTs then start
"""

from __future__ import annotations

import argparse
import logging
import queue
import signal
import sys
import threading
import webbrowser
from pathlib import Path

from desktop.config import AppConfig

logger = logging.getLogger(__name__)


class DesktopApp:
    """Main application that ties together watcher, sync, webapp, and tray."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or AppConfig.load()
        self._watcher_service = None
        self._sync_client_obj = None
        self._webapp_thread = None
        self._shutdown_event = threading.Event()

    @property
    def watcher_running(self) -> bool:
        return self._watcher_service is not None

    @property
    def sync_client(self):
        return self._sync_client_obj

    # ── Watcher ───────────────────────────────────────────────────────

    def start_watcher(self) -> None:
        """Start the IBT file watcher."""
        if self._watcher_service:
            return

        from watcher.service import WatcherService

        sync_queue = queue.Queue() if self.config.auto_sync else None

        self._watcher_service = WatcherService(
            telemetry_dir=self.config.telemetry_dir,
            on_ingest=self._on_ingest_callback,
            sync_queue=sync_queue,
            auto_ingest=self.config.auto_ingest,
            car_filter=self.config.car_filter or None,
        )
        self._watcher_service.start()
        logger.info("Watcher started: %s", self.config.telemetry_dir)

    def stop_watcher(self) -> None:
        """Stop the IBT file watcher."""
        if self._watcher_service:
            self._watcher_service.stop()
            self._watcher_service = None
            logger.info("Watcher stopped")

    # ── Sync ──────────────────────────────────────────────────────────

    def start_sync(self) -> None:
        """Start the background sync client."""
        if not self.config.is_team_configured:
            logger.info("Team not configured — sync disabled")
            return
        if self._sync_client_obj:
            return

        from teamdb.sync_client import SyncClient

        self._sync_client_obj = SyncClient(
            server_url=self.config.team_server_url,
            api_key=self.config.api_key,
        )
        self._sync_client_obj.start()
        logger.info("Sync client started: %s", self.config.team_server_url)

    def stop_sync(self) -> None:
        """Stop the sync client."""
        if self._sync_client_obj:
            self._sync_client_obj.stop()
            self._sync_client_obj = None
            logger.info("Sync client stopped")

    def force_sync(self) -> tuple[int, int]:
        """Force immediate push + pull."""
        if self._sync_client_obj:
            return self._sync_client_obj.force_sync()
        return (0, 0)

    # ── Webapp ────────────────────────────────────────────────────────

    def start_webapp(self) -> None:
        """Start the local web UI in a background thread."""
        import uvicorn

        def _run():
            try:
                uvicorn.run(
                    "webapp.app:create_app",
                    factory=True,
                    host="127.0.0.1",
                    port=self.config.webapp_port,
                    log_level="warning",
                )
            except Exception:
                logger.exception("Webapp failed to start")

        self._webapp_thread = threading.Thread(target=_run, daemon=True, name="webapp")
        self._webapp_thread.start()
        logger.info("Webapp started on http://localhost:%d", self.config.webapp_port)

    # ── Bulk import ───────────────────────────────────────────────────

    def bulk_import(self, limit: int | None = None) -> list:
        """Import all existing IBT files."""
        if not self._watcher_service:
            self.start_watcher()
        results = self._watcher_service.bulk_import(limit=limit)
        self.config.bulk_import_done = True
        self.config.save()
        return results

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start_all(self) -> None:
        """Start all services."""
        self.start_watcher()
        self.start_sync()
        self.start_webapp()

    def shutdown(self) -> None:
        """Gracefully stop all services."""
        logger.info("Shutting down …")
        self.stop_watcher()
        self.stop_sync()
        self._shutdown_event.set()

    def wait(self) -> None:
        """Block until shutdown is requested."""
        self._shutdown_event.wait()

    # ── Callbacks ─────────────────────────────────────────────────────

    def _on_ingest_callback(self, result) -> None:
        """Called after each IBT is ingested."""
        # Queue observation for sync
        if self._sync_client_obj and result.fully_ingested and result.session_id:
            from learner.knowledge_store import KnowledgeStore

            store = KnowledgeStore()
            obs_dict = store.load_observation(result.session_id)
            if obs_dict:
                self._sync_client_obj.queue_observation(obs_dict)

        logger.info(
            "Ingested: %s @ %s (%s) — %s",
            result.car_screen_name,
            result.track_name,
            result.driver_name,
            "full" if result.fully_ingested else "metadata only",
        )


def main():
    """CLI entry point for the desktop app."""
    parser = argparse.ArgumentParser(description="IOptimal Desktop App")
    parser.add_argument("--no-tray", action="store_true", help="Run without system tray icon")
    parser.add_argument("--bulk-import", action="store_true", help="Import all existing IBTs on startup")
    parser.add_argument("--config-dir", type=str, help="Override config directory")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = AppConfig.load(Path(args.config_dir) if args.config_dir else None)
    app = DesktopApp(config)

    # Handle Ctrl+C
    signal.signal(signal.SIGINT, lambda *_: app.shutdown())

    # Start all services
    app.start_all()

    # Bulk import if requested or first run
    if args.bulk_import or (not config.bulk_import_done and config.first_run_complete):
        logger.info("Starting bulk import …")
        results = app.bulk_import()
        logger.info("Bulk import complete: %d sessions", len(results))

    # Open browser
    if config.browser_open_on_start:
        webbrowser.open(f"http://localhost:{config.webapp_port}")

    if args.no_tray:
        # Headless mode — just wait
        logger.info("Running in headless mode (Ctrl+C to stop)")
        app.wait()
    else:
        # Start tray icon (blocking — this becomes the main loop)
        from desktop.tray import TrayIcon

        tray = TrayIcon(app)
        tray.start()  # Blocks until quit

    app.shutdown()


if __name__ == "__main__":
    main()
