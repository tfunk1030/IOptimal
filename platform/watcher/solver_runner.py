"""Local watcher solver execution (IBT -> JSON + STO artifacts)."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from dataclasses import dataclass
from pathlib import Path

from analyzer.setup_reader import CurrentSetup
from car_model.cars import get_car
from pipeline.produce import produce
from track_model.ibt_parser import IBTFile

from watcher.config import WatcherConfig
from watcher.sync import TeamKnowledgeSyncClient


CAR_NAME_MAP = {
    "bmw m hybrid v8": "bmw",
    "cadillac v-series.r": "cadillac",
    "ferrari 499p": "ferrari",
    "porsche 963": "porsche",
    "acura arx-06": "acura",
}


@dataclass
class LocalSolveResult:
    car: str
    track: str
    wing: float | None
    json_path: Path
    sto_path: Path
    report_text: str
    summary: str
    lap_number: int | None


def _normalize_car(name: str | None, default_car: str) -> str:
    if not name:
        return default_car
    key = name.strip().lower()
    mapped = CAR_NAME_MAP.get(key, key)
    try:
        get_car(mapped)
        return mapped
    except Exception:
        return default_car


class LocalSolverRunner:
    def __init__(self, config: WatcherConfig, solver_root: Path) -> None:
        self.config = config
        self.solver_root = solver_root
        self.sync_client = TeamKnowledgeSyncClient(config)

    def solve(self, ibt_path: Path) -> LocalSolveResult:
        ibt = IBTFile(ibt_path)
        track = ibt.track_info().get("track_name", "Unknown Track")
        car_info = ibt.car_info()
        setup = CurrentSetup.from_ibt(ibt)

        car = _normalize_car(car_info.get("car") if car_info else None, self.config.default_car)
        wing = setup.wing_angle_deg if setup.wing_angle_deg else None

        if self.config.access_token and self.config.team_id:
            payload = self.sync_client.fetch_sync_payload(car=car, track=track)
            self.sync_client.write_local_learnings(payload, self.solver_root)

        out_dir = self.config.runtime_dir / "runs"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = ibt_path.stem.replace(" ", "_")
        json_path = out_dir / f"{stamp}.json"
        sto_path = out_dir / f"{stamp}.sto"

        args = argparse.Namespace(
            car=car,
            ibt=str(ibt_path),
            wing=wing,
            lap=None,
            fuel=None,
            balance=50.14,
            tolerance=0.1,
            free=False,
            sto=str(sto_path),
            json=str(json_path),
            report_only=True,
            no_learn=False,
            legacy_solver=False,
            min_lap_time=108.0,
            outlier_pct=0.115,
            stint_laps=30,
            learn=True,
            auto_learn=True,
        )
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            produce(args)
        report_text = capture.getvalue().strip()

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        lap = payload.get("lap_number")
        summary = (
            f"{payload.get('car', car)} @ {payload.get('track', track)}: "
            f"lap {payload.get('lap_time_s', 'n/a')}s, wing {payload.get('wing', wing)}"
        )

        return LocalSolveResult(
            car=car,
            track=track,
            wing=wing,
            json_path=json_path,
            sto_path=sto_path,
            report_text=report_text,
            summary=summary,
            lap_number=lap,
        )

