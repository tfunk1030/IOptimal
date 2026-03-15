"""System tray status icon for watcher runtime."""

from __future__ import annotations

import threading
import webbrowser
from dataclasses import dataclass
from typing import Callable

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover - optional in headless test envs
    pystray = None
    Image = None
    ImageDraw = None

from watcher.config import WatcherConfig


@dataclass
class TrayStatus:
    state: str = "watching"
    tooltip: str = "iOptimal watcher running"


def _build_icon(color: str):
    image = Image.new("RGB", (64, 64), "black")
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 56, 56), fill=color)
    draw.text((20, 24), "IO", fill="white")
    return image


class WatcherTray:
    def __init__(self, config: WatcherConfig, on_toggle_pause: Callable[[], None]) -> None:
        self.config = config
        self.on_toggle_pause = on_toggle_pause
        self.status = TrayStatus()
        self._icon = None

    def start(self) -> None:
        if pystray is None:
            return
        icon = pystray.Icon("iOptimal")
        self._icon = icon
        icon.icon = _build_icon("green")
        icon.title = self.status.tooltip
        icon.menu = pystray.Menu(
            pystray.MenuItem("Open Dashboard", self._open_dashboard),
            pystray.MenuItem("Pause / Resume", self._toggle_pause),
            pystray.MenuItem("Quit", self._quit),
        )
        thread = threading.Thread(target=icon.run, daemon=True)
        thread.start()

    def set_processing(self, text: str) -> None:
        self._set("processing", text, "yellow")

    def set_watching(self, text: str) -> None:
        self._set("watching", text, "green")

    def set_error(self, text: str) -> None:
        self._set("error", text, "red")

    def _set(self, state: str, tooltip: str, color: str) -> None:
        self.status = TrayStatus(state=state, tooltip=tooltip)
        if self._icon is None:
            return
        self._icon.title = tooltip
        self._icon.icon = _build_icon(color)

    def _open_dashboard(self) -> None:
        webbrowser.open(self.config.dashboard_url)

    def _toggle_pause(self) -> None:
        self.on_toggle_pause()

    def _quit(self) -> None:
        if self._icon is not None:
            self._icon.stop()

