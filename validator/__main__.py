"""CLI entry point for the setup validator.

Usage:
    python -m validator --car bmw --track sebring --wing 17 \
        --ibt path/to/session.ibt \
        --setup path/to/solver_output.json \
        [--lap 4] \
        [--json] \
        [--save validation_report.json] \
        [--next-profile updated_profile.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from car_model import get_car
from validator.extract import extract_measurements
from validator.compare import compare_all
from validator.classify import classify_discrepancies
from validator.recommend import generate_recommendations
from validator.report import format_report, save_validation_json


def main():
    parser = argparse.ArgumentParser(
        description="GTP Setup Validator -- Compare solver predictions vs IBT telemetry"
    )
    parser.add_argument("--car", required=True, help="Car name (e.g., bmw)")
    parser.add_argument("--track", required=True, help="Track name (for report)")
    parser.add_argument("--wing", required=True, type=float, help="Wing angle (degrees)")
    parser.add_argument("--ibt", required=True, help="Path to .ibt or .zip telemetry file")
    parser.add_argument("--setup", required=True,
                        help="Path to solver output JSON (from --save)")
    parser.add_argument("--lap", type=int, default=None,
                        help="Specific lap to analyze (default: best lap)")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON instead of human-readable")
    parser.add_argument("--save", type=str, default=None,
                        help="Save validation report JSON to file")
    parser.add_argument("--next-profile", type=str, default=None,
                        help="Save updated track profile for next solver iteration")

    args = parser.parse_args()

    # --- Load inputs ---
    print("Loading car model...")
    car = get_car(args.car)
    print(f"  Car: {car.name}")

    print("Loading solver output...")
    solver_json = json.loads(Path(args.setup).read_text())
    print(f"  Solver output loaded: {Path(args.setup).name}")

    print(f"Parsing IBT: {Path(args.ibt).name}...")
    if args.lap:
        print(f"  Analyzing lap {args.lap}")
    else:
        print("  Analyzing best lap")

    # --- Extract measurements ---
    print()
    print("Extracting telemetry measurements...")
    measured = extract_measurements(
        ibt_path=args.ibt,
        car=car,
        solver_json=solver_json,
        lap=args.lap,
    )
    print(f"  Lap {measured.lap_number}: {measured.lap_time_s:.3f}s")
    print(f"  Mean speed: {measured.speed_mean_kph:.1f} kph")
    print(f"  Peak lat g: {measured.peak_lat_g_measured:.2f} g")

    # --- Compare ---
    print()
    print("Comparing solver predictions vs measurements...")
    comparisons = compare_all(solver_json, measured)
    print(f"  {len(comparisons)} comparisons generated")

    # --- Classify ---
    result = classify_discrepancies(comparisons, measured, solver_json)
    print(f"  Verdict: {result.overall_verdict}")
    print(f"  Confidence: {result.confidence_score}/100")
    print(f"  Confirmed: {len(result.confirmed)}, "
          f"Tweaks: {len(result.tweaks)}, "
          f"Rethinks: {len(result.rethinks)}")

    # --- Recommend ---
    feedback = generate_recommendations(result, measured, solver_json, car)

    # --- Output ---
    if args.json:
        # JSON output to stdout
        output = {
            "verdict": result.overall_verdict,
            "confidence": result.confidence_score,
            "comparisons": [
                {
                    "step": c.step,
                    "parameter": c.parameter,
                    "predicted": c.predicted,
                    "measured": c.measured,
                    "delta": c.delta,
                    "status": (
                        "confirmed" if c in result.confirmed
                        else "tweak" if c in result.tweaks
                        else "rethink"
                    ),
                }
                for c in comparisons
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        # Human-readable report
        ibt_name = Path(args.ibt).name
        track_display = args.track

        # Try to get track name from solver JSON
        meta = solver_json.get("meta", {})
        if meta.get("track"):
            track_display = meta["track"]

        report = format_report(
            result=result,
            feedback=feedback,
            car_name=car.name,
            track_name=track_display,
            lap_number=measured.lap_number,
            lap_time_s=measured.lap_time_s,
            ibt_name=ibt_name,
            measured=measured,
        )
        print()
        print(report)

    # --- Save outputs ---
    if args.save:
        meta = solver_json.get("meta", {})
        save_validation_json(
            result=result,
            feedback=feedback,
            car_name=car.name,
            track_name=meta.get("track", args.track),
            lap_number=measured.lap_number,
            lap_time_s=measured.lap_time_s,
            output_path=args.save,
        )
        print(f"\nValidation JSON saved to: {args.save}")

    if args.next_profile and measured.measured_track_profile is not None:
        measured.measured_track_profile.save(args.next_profile)
        print(f"\nUpdated track profile saved to: {args.next_profile}")
    elif args.next_profile:
        print("\nWARNING: Could not build updated track profile from IBT.")

    # Exit code: 0=good, 1=tweaks, 2=rethink
    if result.overall_verdict == "rethink":
        sys.exit(2)
    elif result.overall_verdict == "needs_tweaking":
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
