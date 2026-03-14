"""IOptimal — unified GTP setup solver.

Routes to the full pipeline (when IBT is provided) or the standalone
physics solver (when no IBT is available yet).  Pass multiple --ibt files
to get individual reports for each plus a side-by-side comparison table.

Usage:
    # Single IBT — full pipeline (ingest, calibrate, solve, compare, report)
    python3 -m ioptimal --car bmw --ibt session.ibt --wing 17

    # Multiple IBT files — individual reports + cross-setup comparison table
    python3 -m ioptimal --car bmw --ibt s1.ibt s2.ibt s3.ibt --wing 17

    # Export .sto for the best-performing setup (multi-IBT mode)
    python3 -m ioptimal --car bmw --ibt s1.ibt s2.ibt --wing 17 --sto output.sto

    # Standalone physics (no IBT — new track, no data yet)
    python3 -m ioptimal --car bmw --track sebring --wing 17

    # Setup space exploration
    python3 -m ioptimal --car bmw --ibt session.ibt --wing 17 --space

    # Skip learning (read-only, don't update calibration)
    python3 -m ioptimal --car bmw --ibt session.ibt --wing 17 --no-learn
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


W = 70  # comparison table width


def _box(title: str) -> str:
    pad = (W - len(title) - 2) // 2
    return "═" * pad + f" {title} " + "═" * (W - pad - len(title) - 2)


def _hdr(title: str) -> str:
    pad = (W - len(title) - 2) // 2
    return "─" * pad + f" {title} " + "─" * (W - pad - len(title) - 2)


def run_multi_ibt(args: argparse.Namespace) -> None:
    """Run pipeline for each IBT file, then print a cross-setup comparison."""
    from pipeline.produce import produce_result
    from analyzer.setup_reader import CurrentSetup
    from track_model.ibt_parser import IBTFile

    results: list[dict] = []

    for idx, ibt_path in enumerate(args.ibt, 1):
        ibt_path = str(ibt_path)
        label = Path(ibt_path).stem[-20:]  # last 20 chars for table width
        print(f"\n{'═'*W}")
        print(f"  FILE {idx}/{len(args.ibt)}: ...{label}")
        print(f"{'═'*W}")

        # Per-file args copy
        import copy
        file_args = copy.copy(args)
        file_args.ibt = ibt_path

        try:
            result = produce_result(file_args)
            results.append({
                "label": label,
                "ibt": ibt_path,
                "lap_time": result["lap_time_s"],
                "lap_num": result["lap_number"],
                "current": result["current_setup"],
                "step1": result["step1"],
                "step2": result["step2"],
                "step3": result["step3"],
                "step4": result["step4"],
                "step5": result["step5"],
                "step6": result["step6"],
                "supporting": result["supporting"],
                "report": result["report"],
            })
            print(result["report"])
        except Exception as e:
            print(f"  [ERROR] {ibt_path}: {e}")
            import traceback
            traceback.print_exc()

    if len(results) < 2:
        return

    # ── Cross-setup comparison table ─────────────────────────────────
    print(f"\n{'═'*W}")
    print(_box("CROSS-SETUP COMPARISON"))
    print(f"{'═'*W}")

    # Header row: file labels
    col = 12
    label_row = f"  {'Parameter':<20}"
    for r in results:
        lbl = r["label"][-col:]
        label_row += f"  {lbl:>{col}}"
    print(label_row)
    print("  " + "─" * (W - 2))

    # Lap time row (current setup)
    def _cur_row(label: str, getter, fmt=".1f"):
        row = f"  {label:<20}"
        vals = []
        for r in results:
            try:
                v = getter(r)
                vals.append(v)
                row += f"  {v:>{col}{fmt}}"
            except Exception:
                row += f"  {'—':>{col}}"
                vals.append(None)
        # Flag best (min) in brackets
        nums = [v for v in vals if v is not None]
        if nums:
            best = min(nums)
            row += f"   ← best: {best:{fmt}}"
        return row

    def _rec_row(label: str, getter, fmt=".1f", best_is_min=True):
        row = f"  {label:<20}"
        vals = []
        for r in results:
            try:
                v = getter(r)
                vals.append(v)
                row += f"  {v:>{col}{fmt}}"
            except Exception:
                row += f"  {'—':>{col}}"
                vals.append(None)
        nums = [v for v in vals if v is not None]
        if nums and len(set(f"{v:{fmt}}" for v in nums)) > 1:
            row += "  ←varies"
        return row

    def _delta_row(label: str, cur_getter, rec_getter, fmt=".1f"):
        """Show current → recommended delta for each file."""
        row = f"  {label:<20}"
        for r in results:
            try:
                cur = cur_getter(r)
                rec = rec_getter(r)
                d = rec - cur
                sign = "+" if d >= 0 else ""
                row += f"  {sign}{d:>{col-1}{fmt}}"
            except Exception:
                row += f"  {'—':>{col}}"
        return row

    # ── SECTION: Current setups across files ─────────────────────────
    print(f"\n  {'CURRENT SETUPS (from IBT)':}")
    print("  " + "─" * (W - 2))
    print(_cur_row("Fastest lap (s)",  lambda r: r["lap_time"], ".3f"))
    print(_cur_row("Lap #",            lambda r: float(r["lap_num"]), ".0f"))
    print(_cur_row("Rear RH (mm)",     lambda r: r["current"].static_rear_rh_mm))
    print(_cur_row("Front heave N/mm", lambda r: r["current"].front_heave_nmm, ".0f"))
    print(_cur_row("Rear third N/mm",  lambda r: r["current"].rear_third_nmm, ".0f"))
    print(_cur_row("Rear spring N/mm", lambda r: r["current"].rear_spring_nmm, ".0f"))
    print(_cur_row("Torsion OD (mm)",  lambda r: r["current"].front_torsion_od_mm))
    print(_cur_row("RARB blade",       lambda r: float(r["current"].rear_arb_blade), ".0f"))
    print(_cur_row("Front camber (°)", lambda r: r["current"].front_camber_deg))
    print(_cur_row("Rear camber (°)",  lambda r: r["current"].rear_camber_deg))
    print(_cur_row("Brake bias (%)",   lambda r: r["current"].brake_bias_pct))
    print(_cur_row("Diff preload Nm",  lambda r: r["current"].diff_preload_nm, ".0f"))
    print(_cur_row("TC gain",          lambda r: float(r["current"].tc_gain), ".0f"))
    print(_cur_row("F LS Comp",        lambda r: float(r["current"].front_ls_comp), ".0f"))
    print(_cur_row("R LS Comp",        lambda r: float(r["current"].rear_ls_comp), ".0f"))

    # ── SECTION: Recommended setups ──────────────────────────────────
    print(f"\n  {'RECOMMENDED SETUP (physics solver)':}")
    print("  " + "─" * (W - 2))
    print(_rec_row("Rear RH (mm)",     lambda r: r["step1"].static_rear_rh_mm))
    print(_rec_row("Front heave N/mm", lambda r: r["step2"].front_heave_nmm, ".0f"))
    print(_rec_row("Rear third N/mm",  lambda r: r["step2"].rear_third_nmm, ".0f"))
    print(_rec_row("Rear spring N/mm", lambda r: r["step3"].rear_spring_rate_nmm, ".0f"))
    print(_rec_row("Torsion OD (mm)",  lambda r: r["step3"].front_torsion_od_mm))
    print(_rec_row("RARB blade",       lambda r: float(r["step4"].rear_arb_blade_start), ".0f"))
    print(_rec_row("Front camber (°)", lambda r: r["step5"].front_camber_deg))
    print(_rec_row("Rear camber (°)",  lambda r: r["step5"].rear_camber_deg))
    print(_rec_row("Brake bias (%)",   lambda r: r["supporting"].brake_bias_pct))
    print(_rec_row("Diff preload Nm",  lambda r: r["supporting"].diff_preload_nm, ".0f"))
    print(_rec_row("TC gain",          lambda r: float(r["supporting"].tc_gain), ".0f"))

    # ── SECTION: Current → Recommended deltas ────────────────────────
    print(f"\n  {'DELTA  (recommended − current)':}")
    print("  " + "─" * (W - 2))
    print(_delta_row("Δ Rear RH (mm)",
                     lambda r: r["current"].static_rear_rh_mm,
                     lambda r: r["step1"].static_rear_rh_mm))
    print(_delta_row("Δ Front heave",
                     lambda r: r["current"].front_heave_nmm,
                     lambda r: r["step2"].front_heave_nmm, ".0f"))
    print(_delta_row("Δ Rear third",
                     lambda r: r["current"].rear_third_nmm,
                     lambda r: r["step2"].rear_third_nmm, ".0f"))
    print(_delta_row("Δ RARB blade",
                     lambda r: float(r["current"].rear_arb_blade),
                     lambda r: float(r["step4"].rear_arb_blade_start), ".0f"))
    print(_delta_row("Δ Front camber",
                     lambda r: r["current"].front_camber_deg,
                     lambda r: r["step5"].front_camber_deg))
    print(_delta_row("Δ Brake bias",
                     lambda r: r["current"].brake_bias_pct,
                     lambda r: r["supporting"].brake_bias_pct))
    print(_delta_row("Δ Diff preload",
                     lambda r: r["current"].diff_preload_nm,
                     lambda r: r["supporting"].diff_preload_nm, ".0f"))
    print(f"\n{'═'*W}")


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
    parser.add_argument("--ibt", nargs="+", default=None, metavar="IBT",
                        help="IBT telemetry file(s). One file = single report. "
                             "Multiple files = individual reports + cross-setup comparison.")
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
                        help="Export iRacing .sto setup file")
    parser.add_argument("--json", type=str, default=None,
                        help="Save full JSON summary to file")
    parser.add_argument("--report-only", action="store_true",
                        help="Print only the final report (suppress per-step progress)")
    parser.add_argument("--space", action="store_true",
                        help="Run setup space exploration (feasible ranges + flat bottom)")

    # ── Lap filtering ──
    parser.add_argument("--min-lap-time", type=float, default=108.0, dest="min_lap_time",
                        help="Absolute floor for valid laps in seconds (default: 108.0). "
                             "Partial laps and pit exits below this are ignored.")
    parser.add_argument("--outlier-pct", type=float, default=0.115, dest="outlier_pct",
                        help="Max %% above lap-time median to accept (default: 0.115 = 11.5%%). "
                             "Drops safety-car / off-track laps. Pass 0 to disable.")

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
        if len(args.ibt) > 1:
            # ── Multi-IBT: individual reports + comparison table ──────
            run_multi_ibt(args)
        else:
            # ── Single IBT: full pipeline ─────────────────────────────
            import copy
            single_args = copy.copy(args)
            single_args.ibt = args.ibt[0]
            from pipeline.produce import produce
            produce(single_args)
    else:
        # ── Standalone physics solver (no telemetry) ──────────────────
        from solver.solve import run_solver
        run_solver(args)


if __name__ == "__main__":
    main()
