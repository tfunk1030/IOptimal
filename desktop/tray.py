"""System tray icon for the IOptimal desktop app.

Provides a tray icon with menu for controlling the background services
(watcher, sync) and opening the web dashboard.
"""

from __future__ import annotations

import logging
import threading
import webbrowser
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from desktop.app import DesktopApp


def _create_default_icon():
    """Create a simple default icon (green circle with 'iO' text).

    Returns a PIL Image.  If Pillow is not available, returns None.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont

        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Green circle background
        draw.ellipse([2, 2, size - 2, size - 2], fill=(28, 90, 79, 255))
        # Text
        try:
            font = ImageFont.truetype("arial.ttf", 24)
        except (OSError, IOError):
            font = ImageFont.load_default()
        draw.text((size // 2, size // 2), "iO", fill="white", anchor="mm", font=font)
        return img
    except ImportError:
        return None


class TrayIcon:
    """System tray icon with menu for the IOptimal desktop app."""

    def __init__(self, app: DesktopApp) -> None:
        self._app = app
        self._icon = None

    def start(self) -> None:
        """Start the tray icon (blocking — run in a thread or as main loop)."""
        try:
            import pystray
            from pystray import MenuItem, Menu
        except ImportError:
            logger.warning("pystray not installed — running without tray icon")
            return

        icon_image = _create_default_icon()
        if icon_image is None:
            logger.warning("Pillow not installed — cannot create tray icon")
            return

        menu = Menu(
            MenuItem("Open Dashboard", self._open_dashboard, default=True),
            Menu.SEPARATOR,
            MenuItem(
                "Watcher",
                Menu(
                    MenuItem("Pause", self._toggle_watcher),
                    MenuItem("Bulk Import", self._bulk_import),
                ),
            ),
            MenuItem("Sync Now", self._force_sync),
            MenuItem(
                "Status",
                Menu(
                    MenuItem(lambda _: self._status_text(), None, enabled=False),
                ),
            ),
            Menu.SEPARATOR,
            MenuItem("Settings", self._open_settings),
            MenuItem("Quit", self._quit),
        )

        self._icon = pystray.Icon("IOptimal", icon_image, "IOptimal", menu)
        logger.info("Starting system tray icon")
        self._icon.run()

    def stop(self) -> None:
        """Stop the tray icon."""
        if self._icon:
            self._icon.stop()

    def notify(self, title: str, message: str) -> None:
        """Show a system notification."""
        if self._icon:
            try:
                self._icon.notify(message, title)
            except Exception:
                logger.debug("Notification failed: %s — %s", title, message)

    # ── Menu callbacks ────────────────────────────────────────────────

    def _open_dashboard(self, icon=None, item=None) -> None:
        port = self._app.config.webapp_port
        webbrowser.open(f"http://localhost:{port}")

    def _open_settings(self, icon=None, item=None) -> None:
        port = self._app.config.webapp_port
        webbrowser.open(f"http://localhost:{port}/settings")

    def _toggle_watcher(self, icon=None, item=None) -> None:
        if self._app.watcher_running:
            self._app.stop_watcher()
            self.notify("IOptimal", "Watcher paused")
        else:
            self._app.start_watcher()
            self.notify("IOptimal", "Watcher resumed")

    def _force_sync(self, icon=None, item=None) -> None:
        threading.Thread(target=self._do_sync, daemon=True).start()

    def _do_sync(self) -> None:
        pushed, pulled = self._app.force_sync()
        self.notify("IOptimal", f"Sync complete: {pushed} pushed, {pulled} models pulled")

    def _bulk_import(self, icon=None, item=None) -> None:
        threading.Thread(target=self._do_bulk_import, daemon=True).start()

    def _do_bulk_import(self) -> None:
        self.notify("IOptimal", "Bulk import started …")
        results = self._app.bulk_import()
        self.notify("IOptimal", f"Bulk import complete: {len(results)} sessions processed")

    def _quit(self, icon=None, item=None) -> None:
        self._app.shutdown()
        if self._icon:
            self._icon.stop()

    def _status_text(self) -> str:
        parts = []
        if self._app.watcher_running:
            parts.append("Watcher: ON")
        else:
            parts.append("Watcher: OFF")
        if self._app.sync_client:
            s = self._app.sync_client.status
            parts.append(f"Queued: {s.queued_observations}")
            parts.append(f"Pushed: {s.total_pushed}")
            parts.append("Connected" if s.connected else "Offline")
        return " | ".join(parts)
