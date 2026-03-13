"""CLI entry point for multi-session comparison mode.

Usage:
    python -m comparison --car bmw --ibt s1.ibt s2.ibt s3.ibt --wing 17
    python -m comparison --car bmw --ibt s1.ibt s2.ibt --wing 17 --sto optimal.sto
    python -m comparison --car bmw --ibt s1.ibt s2.ibt --wing 17 --json comparison.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from car_model.cars import get_car
from comparison.compare import analyze_session, compare_sessions
from comparison.report import format_comparison_report, save_comparison_json
from comparison.score import score_sessions
from comparison.synthesize import synthesize_setup
from output.setup_writer import write_sto


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="comparison",
        description=(
            "Multi-session comparison: analyze multiple IBT files, "
            "compare setups side-by-side, rank performance, and "
            "synthesize an optimal setup from all sessions."
        ),
    )
    parser.add_argument(
        "--car", required=True,
        help="Car name (bmw, ferrari, porsche, cadillac, acura)",
    )
    parser.add_argument(
        "--ibt", required=True, nargs="+",
        help="Paths to 2+ IBT telemetry files",
    )
    parser.add_argument(
        "--wing", type=float, default=None,
        help="Wing angle override for synthesis (auto-detected if omitted)",
    )
    parser.add_argument(
        "--lap", type=int, nargs="+", default=None,
        help="Lap number(s) to analyze. One per IBT file, or a single value applied to all (default: best lap)",
    )
    parser.add_argument(
        "--balance", type=float, default=50.14,
        help="Target DF balance %% for synthesis (default: 50.14)",
    )
    parser.add_argument(
        "--fuel", type=float, default=None,
        help="Fuel load override in liters (auto-detected if omitted)",
    )
    parser.add_argument(
        "--sto", type=str, default=None,
        help="Export synthesized optimal setup as iRacing .sto file",
    )
    parser.add_argument(
        "--json", type=str, default=None,
        help="Save full comparison results as JSON",
    )
    parser.add_argument(
        "--no-synthesis", action="store_true",
        help="Skip synthesis (compare and rank only)",
    )

    args = parser.parse_args()

    # Validate inputs
    if len(args.ibt) < 2:
        print("ERROR: Need at least 2 IBT files to compare.")
        sys.exit(1)

    for ibt_path in args.ibt:
        if not Path(ibt_path).exists():
            print(f"ERROR: IBT file not found: {ibt_path}")
            sys.exit(1)

    # Load car model
    try:
        car = get_car(args.car)
    except KeyError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"Car: {car.name}")
    print(f"Sessions to compare: {len(args.ibt)}")
    print()

    # Resolve per-file lap numbers
    if args.lap is None:
        lap_list = [None] * len(args.ibt)
    elif len(args.lap) == 1:
        lap_list = args.lap * len(args.ibt)
    elif len(args.lap) == len(args.ibt):
        lap_list = args.lap
    else:
        print(f"ERROR: --lap got {len(args.lap)} values but --ibt got {len(args.ibt)} files. "
              f"Provide one lap per file, or a single lap for all.")
        sys.exit(1)

    # ── Phase 1: Analyze each session ──
    sessions = []
    for i, ibt_path in enumerate(args.ibt, start=1):
        label = f"S{i} ({Path(ibt_path).stem})"
        print(f"{'=' * 50}")
        print(f"Analyzing session {i}/{len(args.ibt)}: {Path(ibt_path).name}")
        print(f"{'=' * 50}")

        session = analyze_session(
            ibt_path=ibt_path,
            car=car,
            wing=args.wing,
            fuel=args.fuel,
            lap=lap_list[i - 1],
            label=label,
        )
        sessions.append(session)

        print(f"  Lap {session.lap_number}: {session.lap_time_s:.3f}s")
        print(f"  Track: {session.track_name}")
        print(f"  Wing: {session.wing_angle}°")
        print(f"  Assessment: {session.diagnosis.assessment}")
        print(f"  Problems: {len(session.diagnosis.problems)}")
        print(f"  Driver: {session.driver.style}")
        print(f"  Corners: {len(session.corners)}")
        print()

    # ── Phase 2: Compare ──
    print("Comparing sessions...")
    try:
        comparison = compare_sessions(sessions)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    print(f"  Setup parameters compared: {len(comparison.setup_deltas)}")
    print(f"  Telemetry metrics compared: {len(comparison.telemetry_deltas)}")
    print(f"  Corners matched: {len(comparison.corner_comparisons)}")
    print()

    # ── Phase 3: Score & rank ──
    print("Scoring and ranking sessions...")
    scoring = score_sessions(comparison)
    for ss in scoring.scores:
        print(f"  #{ss.rank} {ss.session.label}: {ss.overall_score:.1%}")
    print()

    # ── Phase 4: Synthesize ──
    synthesis = None
    if not args.no_synthesis:
        print("Synthesizing optimal setup from all sessions...")
        synthesis = synthesize_setup(
            comparison=comparison,
            scoring=scoring,
            car=car,
            wing=args.wing,
            fuel=args.fuel or 89.0,
            balance_target=args.balance,
        )
        print(f"  Synthesis complete. Wing: {synthesis.wing_angle}°")
        print(f"  Based on best session: {synthesis.best_session_label}")
        print()

        # Write .sto if requested
        if args.sto:
            track_name = sessions[0].track_name
            sto_path = write_sto(
                car_name=car.name,
                track_name=track_name,
                wing=synthesis.wing_angle,
                fuel_l=synthesis.fuel_l,
                step1=synthesis.step1,
                step2=synthesis.step2,
                step3=synthesis.step3,
                step4=synthesis.step4,
                step5=synthesis.step5,
                step6=synthesis.step6,
                output_path=args.sto,
                tyre_pressure_kpa=synthesis.supporting.tyre_cold_fl_kpa,
                brake_bias_pct=synthesis.supporting.brake_bias_pct,
                diff_coast_drive_ramp=(
                    f"{synthesis.supporting.diff_ramp_coast}"
                    f"/{synthesis.supporting.diff_ramp_drive}"
                ),
                diff_clutch_plates=synthesis.supporting.diff_clutch_plates,
                diff_preload_nm=synthesis.supporting.diff_preload_nm,
                tc_gain=synthesis.supporting.tc_gain,
                tc_slip=synthesis.supporting.tc_slip,
            )
            print(f"Synthesized .sto setup saved to: {sto_path}")

    # ── Phase 5: Print report ──
    print()
    report = format_comparison_report(comparison, scoring, synthesis)
    print(report)

    # ── Phase 6: Save JSON if requested ──
    if args.json:
        save_comparison_json(comparison, scoring, synthesis, args.json)
        print(f"\nJSON comparison saved to: {args.json}")


if __name__ == "__main__":
    main()
