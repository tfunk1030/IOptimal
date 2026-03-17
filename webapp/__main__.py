"""Run the local IOptimal web app with Uvicorn."""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run("webapp.app:create_app", factory=True, host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
