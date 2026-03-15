from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLATFORM_DIR = ROOT / "platform"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

