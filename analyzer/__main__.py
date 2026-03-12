"""CLI entry point for the setup analyzer.

Usage:
    python -m analyzer --car bmw --ibt path/to/session.ibt
    python -m analyzer --car bmw --ibt path/to/session.ibt --lap 25
    python -m analyzer --car bmw --ibt path/to/session.ibt --save report.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from car_model.cars import get_car
from track_model.ibt_parser import IBTFile
from analyzer.setup_reader import CurrentSetup
from analyzer.extract import extract_measurements
from analyzer.diagnose import diagnose
from analyzer.recommend import recommend
from analyzer.report import format_report, save_analysis_json


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="analyzer",
        description="Analyze iRacing IBT telemetry and recommend setup improvements.",
    )
    parser.add_argument(
        "--car", required=True,
        help="Car name (bmw, ferrari, porsche, cadillac, acura)",
    )
    parser.add_argument(
        "--ibt", required=True,
        help="Path to IBT telemetry file",
    )
    parser.add_argument(
        "--lap", type=int, default=None,
        help="Specific lap number to analyze (default: best lap)",
    )
    parser.add_argument(
        "--save", default=None,
        help="Save JSON report to this path",
    )

    args = parser.parse_args()

    # Validate IBT file exists
    ibt_path = Path(args.ibt)
    if not ibt_path.exists():
        print(f"ERROR: IBT file not found: {ibt_path}")
        sys.exit(1)

    # Load car model
    try:
        car = get_car(args.car)
    except KeyError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"Loading {ibt_path.name} ...")

    # Parse IBT for session metadata
    ibt = IBTFile(str(ibt_path))
    si = ibt.session_info
    track_name = "Unknown Track"
    if isinstance(si, dict):
        wi = si.get("WeekendInfo", {})
        track_name = wi.get("TrackDisplayName", wi.get("TrackName", "Unknown Track"))

    # Step 1: Read current setup from IBT
    print("Reading setup from IBT session info ...")
    setup = CurrentSetup.from_ibt(ibt)
    print(f"  {setup.summary()}")

    # Step 2: Extract telemetry measurements
    print("Extracting telemetry measurements ...")
    measured = extract_measurements(str(ibt_path), car, lap=args.lap)
    print(f"  Lap {measured.lap_number} ({measured.lap_time_s:.3f}s)")
    print(f"  Speed: mean {measured.speed_mean_kph:.0f} kph, max {measured.speed_max_kph:.0f} kph")

    # Step 3: Diagnose handling problems
    print("Diagnosing handling ...")
    diag = diagnose(measured, setup, car)
    print(f"  Assessment: {diag.assessment.upper()}")
    print(f"  {len(diag.problems)} problems found")

    # Step 4: Generate recommendations
    print("Computing recommendations ...")
    result = recommend(diag, setup, car)
    print(f"  {len(result.changes)} changes recommended")

    # Step 5: Print report
    print("")
    report = format_report(
        result,
        car_name=car.name,
        track_name=track_name,
        ibt_name=ibt_path.name,
        measured=measured,
    )
    print(report)

    # Step 6: Save JSON if requested
    if args.save:
        save_analysis_json(
            result,
            car_name=car.name,
            track_name=track_name,
            measured=measured,
            output_path=args.save,
        )
        print(f"\nJSON report saved to: {args.save}")


if __name__ == "__main__":
    main()
