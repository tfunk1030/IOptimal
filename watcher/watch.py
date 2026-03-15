import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

IRACING_TELEMETRY_DIR = Path.home() / "Documents" / "iRacing" / "telemetry"
DEBOUNCE_SECONDS = 5  # wait for iRacing to finish writing

class IBTHandler(FileSystemEventHandler):
    def __init__(self, uploader, car: str):
        self.uploader = uploader
        self.car = car
        self._pending = {}

    def on_created(self, event):
        if event.is_directory or not event.src_path.endswith(".ibt"):
            return
        self._pending[event.src_path] = time.time()

    def on_modified(self, event):
        if event.src_path in self._pending:
            self._pending[event.src_path] = time.time()

    def check_ready(self):
        """Called periodically — upload files that haven't been modified for DEBOUNCE_SECONDS."""
        now = time.time()
        ready = [p for p, t in self._pending.items() if now - t > DEBOUNCE_SECONDS]
        for path in ready:
            del self._pending[path]
            self.uploader.upload(path, self.car)

def start_watcher(uploader, car: str, folder: Path = None):
    folder = folder or IRACING_TELEMETRY_DIR
    handler = IBTHandler(uploader, car)
    observer = Observer()
    observer.schedule(handler, str(folder), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
            handler.check_ready()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
