"""Watcher entrypoint: monitor telemetry folder, solve locally, upload session."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from watcher.auth import WatcherAuthClient
from watcher.config import WatcherConfig
from watcher.solver_runner import LocalSolverRunner
from watcher.tray import WatcherTray
from watcher.uploader import IBTUploader
from watcher.watch import start_watcher


def _ensure_auth(config: WatcherConfig, auth: WatcherAuthClient) -> None:
    if config.access_token:
        auth.ensure_me()
        return

    password = os.getenv("IOPTIMAL_WATCHER_PASSWORD", "").strip()
    if config.email and password:
        auth.login(config.email, password)
        auth.ensure_me()
        return

    raise RuntimeError(
        "Watcher is not authenticated. Set email/password in config or "
        "run login first (IOPTIMAL_WATCHER_PASSWORD env supported)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="iOptimal local telemetry watcher")
    parser.add_argument("--email", default=None, help="Supabase account email for watcher login")
    parser.add_argument("--password", default=None, help="Supabase account password for watcher login")
    parser.add_argument("--server-url", default=None, help="Backend API base URL")
    parser.add_argument("--dashboard-url", default=None, help="Dashboard URL for tray quick-open")
    parser.add_argument("--car", default=None, help="Default car canonical name")
    parser.add_argument("--telemetry-folder", default=None, help="Telemetry folder path")
    args = parser.parse_args()

    config = WatcherConfig.load()
    if args.server_url:
        config.server_url = args.server_url
    if args.dashboard_url:
        config.dashboard_url = args.dashboard_url
    if args.car:
        config.default_car = args.car
    if args.telemetry_folder:
        config.telemetry_folder = args.telemetry_folder
    if args.email:
        config.email = args.email
    config.save()

    auth = WatcherAuthClient(config)
    if args.email and args.password:
        auth.login(args.email, args.password)
    _ensure_auth(config, auth)

    uploader = IBTUploader(config)
    solver_root = Path(__file__).resolve().parents[2]
    runner = LocalSolverRunner(config, solver_root=solver_root)

    def on_toggle_pause() -> None:
        config.paused = not config.paused
        config.save()
        status = "paused" if config.paused else "watching"
        tray.set_watching(f"iOptimal watcher {status}")

    tray = WatcherTray(config=config, on_toggle_pause=on_toggle_pause)
    tray.start()

    def process_file(path: Path) -> None:
        if config.paused:
            return
        try:
            tray.set_processing(f"Processing {path.name}")
            solve_result = runner.solve(path)
            upload_result = uploader.upload(
                ibt_path=path,
                solver_json_path=solve_result.json_path,
                solver_sto_path=solve_result.sto_path,
                car=solve_result.car,
                wing=solve_result.wing,
                lap=solve_result.lap_number,
            )
            tray.set_watching(f"Uploaded {path.name} -> {upload_result.session_id}")
            print(f"[iOptimal] {solve_result.summary}")
        except Exception as exc:
            tray.set_error(f"Error on {path.name}: {exc}")
            print(f"[iOptimal] ERROR: {exc}")

    start_watcher(Path(config.telemetry_folder), process_file)


if __name__ == "__main__":
    main()

