"""CLI entry point for single-session setup analysis.

Uses the production pipeline so analyzer output stays aligned with the
candidate-family and prediction-driven solve architecture.

DEPRECATED: Use 'python -m ioptimal analyze' instead.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

from car_model.cars import get_car
from pipeline.produce import produce_result


def main() -> None:
    warnings.warn(
        "DeprecationWarning: Use 'python -m ioptimal analyze' instead of 'python -m analyzer'",
        DeprecationWarning,
        stacklevel=2
    )
    print("⚠️  DEPRECATED: Use 'python -m ioptimal analyze' instead of 'python -m analyzer'", file=sys.stderr)
    print("", file=sys.stderr)
    parser = argparse.ArgumentParser(
        prog="analyzer",
        description="Analyze one iRacing IBT with the production reasoning pipeline.",
    )
    # GT3 Phase 2 W9.1 — F8 fix. Pull dynamically from the canonical car
    # registry so GT3 canonical names (``bmw_m4_gt3`` etc.) are accepted.
    from car_model.cars import _CARS as _CAR_REGISTRY
    parser.add_argument(
        "--car",
        required=True,
        choices=sorted(_CAR_REGISTRY.keys()),
        help="Car canonical name. Choices: " + ", ".join(sorted(_CAR_REGISTRY.keys())),
    )
    parser.add_argument(
        "--ibt",
        required=True,
        help="Path to IBT telemetry file",
    )
    parser.add_argument(
        "--lap",
        type=int,
        default=None,
        help="Specific lap number to analyze (default: best lap)",
    )
    parser.add_argument(
        "--save",
        default=None,
        help="Save JSON report to this path",
    )

    args = parser.parse_args()

    ibt_path = Path(args.ibt)
    if not ibt_path.exists():
        print(f"ERROR: IBT file not found: {ibt_path}")
        sys.exit(1)

    try:
        get_car(args.car)
    except KeyError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    produce_args = argparse.Namespace(
        car=args.car,
        ibt=str(ibt_path),
        wing=None,
        lap=args.lap,
        balance=50.14,
        tolerance=0.1,
        fuel=None,
        free=False,
        sto=None,
        json=args.save,
        report_only=True,
        no_learn=True,
        legacy_solver=False,
        min_lap_time=108.0,
        outlier_pct=0.115,
        stint=False,
        stint_threshold=1.5,
    )

    result = produce_result(
        produce_args,
        emit_report=False,
        compact_report=False,
    )
    print(result["report"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
