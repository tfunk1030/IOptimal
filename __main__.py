"""IOptimal — unified GTP setup solver.

Routes to the full pipeline (when IBT is provided) or the standalone
physics solver (when no IBT is available yet).

Usage:
    # Full pipeline — ingest IBT, calibrate car model, solve, compare, report
    python3 -m ioptimal --car bmw --ibt session.ibt --wing 17

    # Same but export .sto
    python3 -m ioptimal --car bmw --ibt session.ibt --wing 17 --sto output.sto

    # Standalone physics (no IBT — e.g., new track, no data yet)
    python3 -m ioptimal --car bmw --track sebring --wing 17

    # With setup space exploration
    python3 -m ioptimal --car bmw --ibt session.ibt --wing 17 --space

    # Skip learning (read-only, don't update calibration)
    python3 -m ioptimal --car bmw --ibt session.ibt --wing 17 --no-learn
"""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ioptimal",
        description="IOptimal — GTP setup solver (pipeline + physics)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # ── Car / session ──
    parser.add_argument("--car", required=True,
                        help="Car canonical name (bmw | ferrari | porsche | cadillac | acura)")
    parser.add_argument("--ibt", default=None,
                        help="IBT telemetry file path (enables full pipeline + learner)")
    parser.add_argument("--track", default=None,
                        help="Track name for standalone solver (used when no --ibt)")
    parser.add_argument("--wing", type=float, default=None,
                        help="Wing angle in degrees (auto-detected from IBT if not set)")

    # ── Solver options ──
    parser.add_argument("--lap", type=int, default=None,
                        help="Lap number to analyze (default: best lap)")
    parser.add_argument("--fuel", type=float, default=None,
                        help="Fuel load in liters (auto-detected from IBT if not set)")
    parser.add_argument("--balance", type=float, default=50.14,
                        help="Target DF balance %% (default: 50.14)")
    parser.add_argument("--tolerance", type=float, default=0.1,
                        help="Balance tolerance %% (default: 0.1)")
    parser.add_argument("--free", action="store_true",
                        help="Free optimization (don't pin front RH at sim floor)")

    # ── Output ──
    parser.add_argument("--sto", type=str, default=None,
                        help="Export iRacing .sto setup file to this path")
    parser.add_argument("--json", type=str, default=None,
                        help="Save full JSON summary to file")
    parser.add_argument("--report-only", action="store_true",
                        help="Print only the final report (suppress per-step progress)")
    parser.add_argument("--space", action="store_true",
                        help="Run setup space exploration (feasible ranges + flat bottom)")

    # ── Learning ──
    parser.add_argument("--no-learn", action="store_true",
                        help="Skip IBT ingestion / empirical corrections (read-only run)")

    args = parser.parse_args()

    # ── Validate ──
    if args.ibt is None and args.track is None:
        parser.error("Provide --ibt (full pipeline) or --track (standalone solver)")

    if args.wing is None and args.ibt is None:
        parser.error("--wing is required when running without --ibt")

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    if args.ibt:
        # ── Full pipeline: learner → calibrated solver → report ──────
        from pipeline.produce import produce
        produce(args)
    else:
        # ── Standalone physics solver (no telemetry) ──────────────────
        from solver.solve import run_solver
        run_solver(args)


if __name__ == "__main__":
    main()
