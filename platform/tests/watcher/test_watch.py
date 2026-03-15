import time
from pathlib import Path

from watcher.watch import IBTHandler


def test_debounce_ready_event():
    seen: list[Path] = []
    handler = IBTHandler(lambda p: seen.append(p))
    fake_path = "C:/tmp/test.ibt"
    handler._pending[fake_path] = time.time() - 10
    handler.check_ready()
    assert seen == [Path(fake_path)]

