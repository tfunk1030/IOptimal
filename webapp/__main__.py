"""Run the local IOptimal web app with Uvicorn."""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run("webapp.app:create_app", factory=True, host="0.0.0.0", port=8765, reload=False)


if __name__ == "__main__":
    main()
