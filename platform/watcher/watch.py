"""IBT watcher implementation with debounce to wait for file completion."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

DEBOUNCE_SECONDS = 5


class IBTHandler(FileSystemEventHandler):
    def __init__(self, on_ready: Callable[[Path], None]) -> None:
        self.on_ready = on_ready
        self._pending: dict[str, float] = {}

    def on_created(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        if event.is_directory or not str(event.src_path).lower().endswith(".ibt"):
            return
        self._pending[event.src_path] = time.time()

    def on_modified(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        if event.src_path in self._pending:
            self._pending[event.src_path] = time.time()

    def check_ready(self) -> None:
        now = time.time()
        ready = [path for path, ts in self._pending.items() if now - ts > DEBOUNCE_SECONDS]
        for path in ready:
            self._pending.pop(path, None)
            self.on_ready(Path(path))


def start_watcher(folder: Path, on_ready: Callable[[Path], None]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    handler = IBTHandler(on_ready=on_ready)
    observer = Observer()
    observer.schedule(handler, str(folder), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1.0)
            handler.check_ready()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

