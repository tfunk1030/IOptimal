"""Filesystem watcher for iRacing telemetry (.ibt) files.

Uses the ``watchdog`` library to monitor the iRacing telemetry directory
for new IBT files.  When a file is detected and fully written, it is
handed off to a callback for ingestion.
"""

from __future__ import annotations

import logging
import os
import platform
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

# How long to wait (seconds) for the file to stop growing before declaring
# it "fully written".  iRacing writes IBT continuously during a session;
# the file stops growing once the driver exits the car / ends the session.
_STABLE_WAIT_S = 3.0
_STABLE_POLL_S = 0.5
_MAX_RETRIES = 3
# Cap the number of times we defer for "size changed during gap" before giving
# up — guards against pathological cases where the file grows on every check.
_MAX_DEFERRALS = 5


def default_telemetry_dir() -> Path:
    """Return the default iRacing telemetry directory for the current OS."""
    if platform.system() == "Windows":
        home = Path(os.environ.get("USERPROFILE", Path.home()))
        return home / "Documents" / "iRacing" / "Telemetry"
    # Linux/Mac — unlikely for iRacing, but allow override via config.
    return Path.home() / "Documents" / "iRacing" / "Telemetry"


class IBTHandler(FileSystemEventHandler):
    """Handles filesystem events for .ibt files.

    When a new ``.ibt`` file appears (creation or modification), the handler
    waits until the file size stabilises (no growth for ``_STABLE_WAIT_S``
    seconds), then calls the user-provided *on_new_ibt* callback with the
    resolved ``Path``.
    """

    def __init__(
        self,
        on_new_ibt: Callable[[Path], None],
        *,
        seen: set[str] | None = None,
    ) -> None:
        super().__init__()
        self._on_new_ibt = on_new_ibt
        # Track files we have already dispatched so duplicates from
        # create + modify events are suppressed.
        self._seen: set[str] = seen or set()
        self._retry_counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._maybe_dispatch(event.src_path)

    def on_modified(self, event: FileModifiedEvent) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._maybe_dispatch(event.src_path)

    # ------------------------------------------------------------------
    def _maybe_dispatch(self, src_path: str) -> None:
        p = Path(src_path)
        if p.suffix.lower() != ".ibt":
            return
        key = str(p.resolve())
        if key in self._seen:
            return
        self._seen.add(key)

        logger.info("New IBT detected: %s — waiting for write to finish …", p.name)
        if not self._wait_until_stable(p):
            logger.warning("IBT file disappeared before stabilising: %s", p.name)
            self._seen.discard(key)
            return

        logger.info("IBT file stable (%s bytes): %s", p.stat().st_size, p.name)
        try:
            self._on_new_ibt(p)
            self._retry_counts.pop(key, None)  # clear on success
        except Exception:
            retries = self._retry_counts.get(key, 0) + 1
            self._retry_counts[key] = retries
            if retries >= _MAX_RETRIES:
                logger.error("IBT %s failed %d times — giving up", p.name, retries)
                self._retry_counts.pop(key, None)  # clear when giving up
            else:
                delay = 2.0 ** (retries - 1)  # exponential backoff: 1s, 2s, …
                logger.exception(
                    "Error processing IBT %s (attempt %d/%d — retrying in %.0fs)",
                    p.name, retries, _MAX_RETRIES, delay,
                )
                self._seen.discard(key)
                t = threading.Timer(delay, self._maybe_dispatch, args=(src_path,))
                t.daemon = True
                t.start()

    @staticmethod
    def _wait_until_stable(path: Path, timeout: float = 300.0) -> bool:
        """Block until *path* stops growing or disappears.

        After the stability window passes, re-check the size: if the file grew
        during the small gap between the stability decision and this final
        check, defer for another window. Capped at ``_MAX_DEFERRALS`` to avoid
        looping forever on a continuously-written file.
        """
        deadline = time.monotonic() + timeout
        deferrals = 0

        while time.monotonic() < deadline:
            stable_size = IBTHandler._wait_for_stable_size(path, deadline)
            if stable_size is None:
                return False
            # Final re-check: if size changed during the dispatch gap, defer.
            time.sleep(_STABLE_POLL_S)
            if not path.exists():
                return False
            if path.stat().st_size == stable_size:
                return True
            deferrals += 1
            if deferrals >= _MAX_DEFERRALS:
                logger.warning(
                    "IBT %s kept growing across %d stability windows — giving up",
                    path.name, deferrals,
                )
                return False
            logger.debug("IBT %s size changed during gap — deferring (%d/%d)",
                         path.name, deferrals, _MAX_DEFERRALS)
        return False

    @staticmethod
    def _wait_for_stable_size(path: Path, deadline: float) -> int | None:
        """Wait for *path* to stop growing for ``_STABLE_WAIT_S``.

        Returns the stable size on success, or ``None`` if the file disappeared
        or the deadline was reached.
        """
        prev_size = -1
        stable_since = 0.0
        while time.monotonic() < deadline:
            if not path.exists():
                return None
            size = path.stat().st_size
            if size == prev_size and size > 0:
                if stable_since == 0.0:
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= _STABLE_WAIT_S:
                    return size
            else:
                stable_since = 0.0
                prev_size = size
            time.sleep(_STABLE_POLL_S)
        return None


class IBTWatcher:
    """High-level watcher that monitors a directory for new IBT files.

    Parameters
    ----------
    telemetry_dir:
        Directory to watch.  Defaults to the standard iRacing telemetry path.
    on_new_ibt:
        Callback invoked with the ``Path`` of each newly-written IBT file.
    recursive:
        Whether to watch sub-directories (iRacing stores files in dated
        sub-folders on some versions).
    """

    def __init__(
        self,
        on_new_ibt: Callable[[Path], None],
        *,
        telemetry_dir: Path | str | None = None,
        recursive: bool = True,
    ) -> None:
        self._dir = Path(telemetry_dir) if telemetry_dir else default_telemetry_dir()
        self._recursive = recursive
        self._seen: set[str] = set()
        self._handler = IBTHandler(on_new_ibt, seen=self._seen)
        self._observer = Observer()

    @property
    def telemetry_dir(self) -> Path:
        return self._dir

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start watching (non-blocking — runs in a background thread)."""
        if not self._dir.exists():
            logger.warning("Telemetry directory does not exist: %s", self._dir)
            self._dir.mkdir(parents=True, exist_ok=True)
            logger.info("Created directory: %s", self._dir)

        self._observer.schedule(self._handler, str(self._dir), recursive=self._recursive)
        self._observer.start()
        logger.info("IBT watcher started — monitoring %s", self._dir)

    def stop(self) -> None:
        """Stop watching and clean up."""
        self._observer.stop()
        self._observer.join(timeout=5)
        logger.info("IBT watcher stopped.")

    # ------------------------------------------------------------------
    def scan_existing(self) -> list[Path]:
        """Return all un-seen .ibt files already present in the directory.

        This is useful for a bulk-import on first install.  Files are returned
        sorted by modification time (oldest first).
        """
        if not self._dir.exists():
            return []
        files = sorted(self._dir.rglob("*.ibt"), key=lambda p: p.stat().st_mtime)
        unseen = []
        for f in files:
            key = str(f.resolve())
            if key not in self._seen:
                unseen.append(f)
        return unseen
