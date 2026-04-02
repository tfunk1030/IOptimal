"""Run the local IOptimal web app with Uvicorn."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("WEBAPP_HOST", "127.0.0.1")
    port = int(os.environ.get("WEBAPP_PORT", "8000"))
    uvicorn.run("webapp.app:create_app", factory=True, host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
