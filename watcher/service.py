"""Watcher service — coordinates IBT detection, ingestion, and sync queueing.

This is the main service that the desktop app or CLI starts.  It:
1. Watches the iRacing telemetry directory for new IBT files
2. Auto-detects the car from the IBT header
3. Ingests via the learner pipeline (for known cars) or stores raw observation
4. Queues the observation for team server sync
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from car_model.registry import resolve_car
from watcher.monitor import IBTWatcher, default_telemetry_dir

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    """Result of processing one IBT file."""

    ibt_path: str
    car_screen_name: str
    car_canonical: str | None  # None if unknown car
    track_name: str
    driver_name: str
    best_lap_time_s: float | None
    session_id: str | None
    fully_ingested: bool  # True if learner pipeline ran; False if raw-only
    error: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _detect_car_and_track(ibt_path: Path) -> tuple[str, str, str]:
    """Parse IBT header to extract car screen name, track name, and driver name.

    Returns (car_screen_name, track_name, driver_name).
    """
    from track_model.ibt_parser import IBTFile

    ibt = IBTFile(str(ibt_path))
    car = ibt.car_info()
    track = ibt.track_info()
    return (
        car.get("car", "Unknown"),
        track.get("track_name", "Unknown"),
        car.get("driver", "Unknown"),
    )


class WatcherService:
    """Orchestrates watching, ingestion, and sync queueing.

    Parameters
    ----------
    telemetry_dir:
        Directory to watch.  Defaults to iRacing standard path.
    on_ingest:
        Optional callback fired after each successful ingest with the
        ``IngestResult``.  Useful for desktop notifications.
    sync_queue:
        If provided, ``IngestResult`` objects are put onto this queue
        for the sync client to push to the team server.
    auto_ingest:
        If True (default), automatically run the learner pipeline on
        new IBT files.  If False, only detect and queue.
    car_filter:
        If provided, only process IBTs for these car canonical names.
        Empty list or None means accept all.
    """

    def __init__(
        self,
        *,
        telemetry_dir: Path | str | None = None,
        on_ingest: Callable[[IngestResult], None] | None = None,
        sync_queue: queue.Queue | None = None,
        auto_ingest: bool = True,
        car_filter: list[str] | None = None,
    ) -> None:
        self._telemetry_dir = Path(telemetry_dir) if telemetry_dir else default_telemetry_dir()
        self._on_ingest = on_ingest
        self._sync_queue = sync_queue
        self._auto_ingest = auto_ingest
        self._car_filter = set(car_filter) if car_filter else None
        self._watcher = IBTWatcher(
            on_new_ibt=self._handle_new_ibt,
            telemetry_dir=self._telemetry_dir,
        )
        self._lock = threading.Lock()
        self._results: list[IngestResult] = []

    @property
    def results(self) -> list[IngestResult]:
        with self._lock:
            return list(self._results)

    def start(self) -> None:
        """Start the watcher (non-blocking)."""
        self._watcher.start()
        logger.info("WatcherService started — monitoring %s", self._telemetry_dir)

    def stop(self) -> None:
        """Stop the watcher."""
        self._watcher.stop()
        logger.info("WatcherService stopped.")

    def bulk_import(self, limit: int | None = None) -> list[IngestResult]:
        """Scan for existing IBT files and ingest them all.

        Useful on first install to import historical sessions.
        Returns list of IngestResults.
        """
        existing = self._watcher.scan_existing()
        if limit:
            existing = existing[:limit]

        results = []
        for ibt_path in existing:
            result = self._handle_new_ibt(ibt_path)
            if result:
                results.append(result)
        return results

    def _handle_new_ibt(self, ibt_path: Path) -> IngestResult | None:
        """Process a single IBT file."""
        logger.info("Processing IBT: %s", ibt_path.name)

        # Step 1: Detect car and track from IBT header
        try:
            car_screen, track_name, driver_name = _detect_car_and_track(ibt_path)
        except Exception as e:
            logger.error("Failed to parse IBT header: %s — %s", ibt_path.name, e)
            result = IngestResult(
                ibt_path=str(ibt_path),
                car_screen_name="Unknown",
                car_canonical=None,
                track_name="Unknown",
                driver_name="Unknown",
                best_lap_time_s=None,
                session_id=None,
                fully_ingested=False,
                error=str(e),
            )
            self._store_result(result)
            return result

        _car_identity = resolve_car(car_screen)
        car_canonical = _car_identity.canonical if _car_identity else None

        # Step 2: Filter by car if configured
        if self._car_filter and car_canonical and car_canonical not in self._car_filter:
            logger.debug("Skipping %s (car %s not in filter)", ibt_path.name, car_canonical)
            return None

        # Step 3: Ingest via learner pipeline (for known cars)
        session_id = None
        best_lap = None
        fully_ingested = False
        error = None

        if self._auto_ingest and car_canonical:
            try:
                from learner.ingest import ingest_ibt

                ingest_result = ingest_ibt(
                    car_name=car_canonical,
                    ibt_path=str(ibt_path),
                    verbose=False,
                )
                session_id = ingest_result.get("session_id")
                fully_ingested = ingest_result.get("observation_stored", False)
                logger.info(
                    "Ingested %s: %s @ %s (session %s, %d total)",
                    car_screen,
                    track_name,
                    driver_name,
                    session_id,
                    ingest_result.get("total_sessions", 0),
                )
            except Exception as e:
                error = str(e)
                logger.error("Ingestion failed for %s: %s", ibt_path.name, e)
        elif not car_canonical:
            logger.info(
                "Unknown car '%s' — storing metadata only (no physics model).",
                car_screen,
            )
            # For unknown cars, we still record that we saw the session.
            # The team server can use this for car auto-registration.

        result = IngestResult(
            ibt_path=str(ibt_path),
            car_screen_name=car_screen,
            car_canonical=car_canonical,
            track_name=track_name,
            driver_name=driver_name,
            best_lap_time_s=best_lap,
            session_id=session_id,
            fully_ingested=fully_ingested,
            error=error,
        )
        self._store_result(result)
        return result

    def _store_result(self, result: IngestResult) -> None:
        """Store result and notify listeners."""
        with self._lock:
            self._results.append(result)

        # Queue for sync to team server
        if self._sync_queue is not None:
            self._sync_queue.put(result)

        # Fire notification callback
        if self._on_ingest is not None:
            try:
                self._on_ingest(result)
            except Exception:
                logger.exception("on_ingest callback failed")
