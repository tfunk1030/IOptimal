"""Deprecated LLTD calibration entry point.

This module previously attempted to infer an LLTD target from the telemetry
field ``lltd_measured``. That field is now known to be a backward-compatible
alias of ``roll_distribution_proxy`` in ``analyzer.extract`` — a ride-height-
derived geometric proxy, not true wheel-load LLTD.

Running the old calibration path would persist proxy-derived targets back into
``data/calibration/<car>/models.json`` and contaminate live solver behavior.
The script now fails fast with guidance instead of writing invalid targets.
"""

from __future__ import annotations

import argparse
import sys


def calibrate_lltd(car_name: str, track_name: str) -> dict | None:
    """Always fail: proxy-derived LLTD calibration is no longer supported."""
    raise RuntimeError(
        "validation.calibrate_lltd is disabled. "
        f"{car_name}/{track_name} cannot derive a true LLTD target from "
        "IBT lltd_measured because that field is a ride-height proxy, not "
        "wheel-load LLTD. Use car-specific hand calibration or physics-"
        "derived targets until true wheel-force telemetry exists."
    )


def main():
    parser = argparse.ArgumentParser(
        description="Disabled: proxy-based LLTD calibration is no longer supported"
    )
    parser.add_argument("--car", required=True, help="Car name (e.g., ferrari)")
    parser.add_argument("--track", required=True, help="Track name (e.g., sebring)")
    args = parser.parse_args()

    try:
        calibrate_lltd(args.car, args.track)
    except RuntimeError as exc:
        print(str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
