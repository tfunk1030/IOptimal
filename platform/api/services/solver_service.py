"""Thin adapter around the existing solver pipeline entrypoint."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline.produce import produce


@dataclass
class SolverArtifacts:
    solver_output: dict[str, Any]
    report_text: str
    sto_path: Path
    json_path: Path


def make_produce_args(
    car: str,
    ibt_path: str,
    wing: float | None = None,
    fuel: float | None = None,
    lap: int | None = None,
    sto_path: str | None = None,
    json_path: str | None = None,
    learn: bool = True,
    auto_learn: bool = True,
) -> argparse.Namespace:
    """Adapter that constructs the argparse namespace expected by produce()."""
    return argparse.Namespace(
        car=car,
        ibt=ibt_path,
        wing=wing,
        fuel=fuel,
        lap=lap,
        sto=sto_path,
        json=json_path,
        learn=learn,
        auto_learn=auto_learn,
        no_learn=not learn,
        min_lap_time=108.0,
        outlier_pct=0.115,
        balance=50.14,
        tolerance=0.1,
        free=False,
        report_only=True,
        legacy_solver=False,
        stint_laps=30,
    )


def run_solver(
    *,
    car: str,
    ibt_path: Path,
    session_id: str,
    artifact_dir: Path,
    wing: float | None = None,
    fuel: float | None = None,
    lap: int | None = None,
    learn: bool = True,
) -> SolverArtifacts:
    """Execute the synchronous solver and return generated artifacts."""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    json_path = artifact_dir / f"{session_id}.json"
    sto_path = artifact_dir / f"{session_id}.sto"

    args = make_produce_args(
        car=car,
        ibt_path=str(ibt_path),
        wing=wing,
        fuel=fuel,
        lap=lap,
        sto_path=str(sto_path),
        json_path=str(json_path),
        learn=learn,
        auto_learn=learn,
    )

    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        produce(args)
    report_text = capture.getvalue().strip()

    solver_output = json.loads(json_path.read_text(encoding="utf-8"))
    return SolverArtifacts(
        solver_output=solver_output,
        report_text=report_text,
        sto_path=sto_path,
        json_path=json_path,
    )

