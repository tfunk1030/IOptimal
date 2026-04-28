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

from car_model.registry import CarIdentity, resolve_car
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
    # Stable iRacing CarPath (DriverInfo.Drivers[me].CarPath) — locale-
    # independent identifier captured by W8.2.  Empty string when the IBT
    # parser couldn't read it.
    iracing_car_path: str = ""


def _detect_car_and_track(
    ibt_path: Path,
) -> tuple[str, str, str, str, CarIdentity | None]:
    """Parse IBT header to extract car / track / driver and resolve identity.

    Returns ``(car_screen_name, car_path, track_name, driver_name, identity)``.

    Resolution dispatch order (W8.2, audit F5):

    1. iRacing ``CarPath`` (locale-independent, stable across EVO/year drift)
    2. iRacing ``CarScreenName`` (legacy fallback, drifts with locale + suffixes)
    3. ``None`` — unknown car, observation stored raw without a physics model.

    GT3 IBTs whose screen name was localised used to silently drop here
    (substring fallback returned ``None``); the CarPath path resolves them
    against ``car_model.registry._BY_IRACING_PATH``.
    """
    from track_model.ibt_parser import IBTFile

    ibt = IBTFile(str(ibt_path))
    car = ibt.car_info()
    track = ibt.track_info()
    car_screen = car.get("car", "Unknown")
    car_path = car.get("iracing_car_path", "") or car.get("car_path", "") or ""
    track_name = track.get("track_name", "Unknown")
    driver_name = car.get("driver", "Unknown")

    identity: CarIdentity | None = None
    if car_path:
        identity = resolve_car(car_path)
    if identity is None and car_screen and car_screen != "Unknown":
        identity = resolve_car(car_screen)

    return (car_screen, car_path, track_name, driver_name, identity)


def _class_for_canonical(canonical: str | None) -> str | None:
    """Return the race class label ("GTP" / "GT3") for a canonical car name.

    Uses ``car_model.cars.get_car()`` and reads ``suspension_arch`` so the
    class label tracks the underlying physics architecture.  Returns
    ``None`` if the canonical is unknown or the car spec hasn't been
    onboarded yet (defensive — keeps the watcher running on unfamiliar
    cars rather than blocking ingestion).
    """
    if not canonical:
        return None
    try:
        from car_model.cars import get_car
        from car_model.cars import SuspensionArchitecture
        car = get_car(canonical)
    except Exception:
        return None
    arch = getattr(car, "suspension_arch", None)
    if arch is None:
        return None
    if arch == SuspensionArchitecture.GT3_COIL_4WHEEL:
        return "GT3"
    if arch in (
        SuspensionArchitecture.GTP_HEAVE_THIRD_TORSION_FRONT,
        SuspensionArchitecture.GTP_HEAVE_THIRD_ROLL_FRONT,
    ):
        return "GTP"
    return None


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
    class_filter:
        If provided, only process IBTs whose suspension architecture maps
        to one of the listed race classes ("GTP" / "GT3").  Coarser than
        ``car_filter`` and orthogonal to it: both filters must pass.  None
        or empty list means accept all classes.  Useful for a multi-class
        league member who only races GT3 and does not want their GTP test
        sessions ingested.
    """

    def __init__(
        self,
        *,
        telemetry_dir: Path | str | None = None,
        on_ingest: Callable[[IngestResult], None] | None = None,
        sync_queue: queue.Queue | None = None,
        auto_ingest: bool = True,
        car_filter: list[str] | None = None,
        class_filter: list[str] | None = None,
    ) -> None:
        self._telemetry_dir = Path(telemetry_dir) if telemetry_dir else default_telemetry_dir()
        self._on_ingest = on_ingest
        self._sync_queue = sync_queue
        self._auto_ingest = auto_ingest
        self._car_filter = set(car_filter) if car_filter else None
        self._class_filter = (
            {c.upper() for c in class_filter} if class_filter else None
        )
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

        # Step 1: Detect car and track from IBT header.  The resolver prefers
        # the iRacing CarPath (stable, locale-independent) and falls back to
        # CarScreenName.  See _detect_car_and_track docstring for full
        # dispatch order (W8.2, audit F5).
        try:
            (
                car_screen,
                car_path,
                track_name,
                driver_name,
                _car_identity,
            ) = _detect_car_and_track(ibt_path)
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

        car_canonical = _car_identity.canonical if _car_identity else None
        if _car_identity is None:
            logger.info(
                "Could not resolve car for %s: CarPath=%r CarScreenName=%r",
                ibt_path.name, car_path, car_screen,
            )

        # Step 2: Filter by car if configured
        if self._car_filter and car_canonical and car_canonical not in self._car_filter:
            logger.debug("Skipping %s (car %s not in filter)", ibt_path.name, car_canonical)
            return None

        # Step 2b: Filter by class (GTP / GT3) if configured.  Requires the
        # car to resolve so we can read suspension_arch.  Unknown cars
        # bypass the class filter (they are stored raw with no model).
        if self._class_filter and _car_identity is not None:
            ibt_class = _class_for_canonical(car_canonical)
            if ibt_class is not None and ibt_class not in self._class_filter:
                logger.debug(
                    "Skipping %s (class %s not in filter %s)",
                    ibt_path.name, ibt_class, sorted(self._class_filter),
                )
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
            iracing_car_path=car_path,
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
