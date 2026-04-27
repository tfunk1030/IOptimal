"""IOptimal — unified GTP setup solver.

Canonical CLI entry point with subcommands:
    python -m ioptimal produce --car bmw --ibt session.ibt --wing 17
    python -m ioptimal analyze --car bmw --ibt session.ibt
    python -m ioptimal solve --car bmw --track sebring --wing 17
    python -m ioptimal ingest --car bmw --ibt session.ibt
    python -m ioptimal calibrate --car ferrari --ibt s1.ibt s2.ibt s3.ibt
    python -m ioptimal calibrate --car ferrari --status

Legacy usage (routes to 'produce' subcommand):
    python -m ioptimal --car bmw --ibt session.ibt --wing 17
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ── Fix Windows console encoding before ANY output ──────────────────────────
# Must happen before argparse, imports, or pipeline modules print anything.
# Without this, Unicode box-drawing characters and emoji render as mojibake
# on Windows terminals using the legacy OEM code page (CP850/CP437).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


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


def run_grid_search(args: argparse.Namespace) -> None:
    """Run hierarchical grid search over the full legal setup space and print ranked setup cards."""
    import time
    import pathlib
    from car_model.cars import get_car
    from solver.legal_space import LegalSpace
    from solver.objective import ObjectiveFunction

    car = get_car(args.car)

    # Load track — prefer IBT-derived track, fall back to --track arg
    track = None
    if args.track:
        from track_model.profile import TrackProfile
        track_path = pathlib.Path(args.track)
        if not track_path.exists():
            # Try data/tracks/ prefix
            track_path = pathlib.Path("data/tracks") / args.track
            if not track_path.exists():
                track_path = pathlib.Path("data/tracks") / f"{args.track}.json"
        if track_path.exists():
            track = TrackProfile.load(track_path)

    space = LegalSpace.from_car(car)
    obj = ObjectiveFunction(car, track)

    try:
        from solver.grid_search import GridSearchEngine
    except ImportError as e:
        print(f"[ERROR] GridSearchEngine not available: {e}")
        print("Make sure you're on the claw-research branch with solver/grid_search.py present.")
        sys.exit(1)

    wing = args.wing or 17.0
    budget = args.search_mode
    top_n = args.top_n

    track_label = pathlib.Path(args.track).stem if args.track else "no-track"
    car_label = args.car.upper()

    print("═" * 72)
    print(f"  {car_label}  ·  {track_label}  ·  Wing {wing}°  ·  Grid Search [{budget.upper()}]")
    print("═" * 72)
    print(f"  Legal space: {len(space.dimensions)} dims  |  {space.total_cardinality:.2e} total combos")
    print(f"  Running {budget} search...\n")
    sys.stdout.flush()

    t0 = time.time()
    engine = GridSearchEngine(space, obj, car, track)
    result = engine.run(budget=budget)
    elapsed = time.time() - t0

    print(result.summary())
    print(f"\n  ⏱ {elapsed:.1f}s  |  {result.total_evaluated:,} candidates evaluated")

    candidates = result.top_candidates[:top_n]
    if not candidates:
        print("\n[No valid candidates found — all vetoed]")
        return

    print(f"\n{'═' * 72}")
    print(f"  TOP {len(candidates)} SETUPS")
    print(f"{'═' * 72}")

    for rank, cand in enumerate(candidates, 1):
        p = cand.params
        ev = obj.evaluate(p, family=cand.family)

        # Score breakdown
        bd = ev.breakdown if hasattr(ev, "breakdown") and ev.breakdown else None
        lap_gain = f"{bd.lap_gain_ms:+.1f}ms" if bd else "n/a"
        plat_risk = f"{bd.platform_risk.total_ms:.1f}ms" if bd else "n/a"
        lltd = f"{ev.lltd_pct:.1f}%" if hasattr(ev, "lltd_pct") else "n/a"
        stall = f"{ev.stall_margin_mm:+.1f}mm" if hasattr(ev, "stall_margin_mm") else "n/a"
        zeta = f"{ev.zeta_ls_front:.2f}" if hasattr(ev, "zeta_ls_front") else "n/a"

        print(f"\n┌─── #{rank}  score={cand.score:.1f}ms  [{cand.family}] {'─' * (40 - len(cand.family))}┐")
        print(f"│  Score breakdown:  lap_gain={lap_gain}  platform_risk={plat_risk}")
        print(f"│  LLTD={lltd}  stall_margin={stall}  ζLS_front={zeta}")
        print(f"├{'─' * 70}┤")
        print(f"│  PLATFORM / SPRINGS")
        print(f"│  Wing                    {wing:.1f} deg")
        print(f"│  Front pushrod           {p.get('front_pushrod_offset_mm', '—'):>8} mm")
        print(f"│  Rear pushrod            {p.get('rear_pushrod_offset_mm', '—'):>8} mm")
        print(f"│  Front heave spring      {p.get('front_heave_spring_nmm', '—'):>8} N/mm")
        print(f"│  Rear third spring       {p.get('rear_third_spring_nmm', '—'):>8} N/mm")
        print(f"│  Rear coil spring        {p.get('rear_spring_rate_nmm', '—'):>8} N/mm")
        print(f"│  Front torsion OD        {p.get('front_torsion_od_mm', '—'):>8} mm")
        print(f"├{'─' * 70}┤")
        print(f"│  ARBs / GEOMETRY")
        print(f"│  Front ARB blade         {p.get('front_arb_blade', '—'):>8}")
        print(f"│  Rear ARB blade          {p.get('rear_arb_blade', '—'):>8}")
        print(f"│  Front camber            {p.get('front_camber_deg', '—'):>8} deg")
        print(f"│  Rear camber             {p.get('rear_camber_deg', '—'):>8} deg")
        print(f"├{'─' * 70}┤")
        print(f"│  BALANCE")
        print(f"│  Brake bias              {p.get('brake_bias_pct', '—'):>8} %")
        print(f"│  Diff preload            {p.get('diff_preload_nm', '—'):>8} Nm")
        print(f"├{'─' * 70}┤")
        print(f"│  DAMPERS")
        print(f"│  Front LS comp/rbd       {p.get('front_ls_comp','—'):.0f} / {p.get('front_ls_rbd','—'):.0f} clicks")
        print(f"│  Front HS comp/rbd/slope {p.get('front_hs_comp','—'):.0f} / {p.get('front_hs_rbd','—'):.0f} / {p.get('front_hs_slope','—'):.0f}")
        print(f"│  Rear LS comp/rbd        {p.get('rear_ls_comp','—'):.0f} / {p.get('rear_ls_rbd','—'):.0f} clicks")
        print(f"│  Rear HS comp/rbd/slope  {p.get('rear_hs_comp','—'):.0f} / {p.get('rear_hs_rbd','—'):.0f} / {p.get('rear_hs_slope','—'):.0f}")
        print(f"└{'─' * 70}┘")

    # Optionally export best to JSON
    if args.json and candidates:
        import json
        best = candidates[0]
        out = {
            "search_mode": budget,
            "car": args.car,
            "wing": wing,
            "total_evaluated": result.total_evaluated,
            "elapsed_s": round(elapsed, 2),
            "top_candidates": [
                {"rank": i + 1, "score": c.score, "family": c.family, "params": c.params}
                for i, c in enumerate(candidates)
            ],
        }
        pathlib.Path(args.json).write_text(json.dumps(out, indent=2))
        print(f"\n  Saved to {args.json}")

    # Export best .sto — grid search only has flat params, not solver step
    # objects required by write_sto(); disabled until step materialisation exists.
    if args.sto and candidates:
        print(f"  [sto export not yet supported for grid search candidates]")

    print(f"\n{'═' * 72}")


def cmd_produce(args: argparse.Namespace) -> None:
    """Route to pipeline.produce."""
    from pipeline.produce import produce, produce_result

    if args.ibt and len(args.ibt) > 1:
        run_multi_ibt(args)
    elif args.ibt:
        import copy
        single_args = copy.copy(args)
        single_args.ibt = args.ibt[0]

        if getattr(args, 'bundle_dir', None):
            result = produce_result(single_args)
            if result is not None:
                from output.bundle import bundle_from_pipeline_result
                manifest = bundle_from_pipeline_result(
                    args.bundle_dir,
                    result,
                    report_text=result.get("report"),
                )
                print(f"\nBundle written to: {manifest.bundle_dir}")
                for p in manifest.artifacts:
                    print(f"  {p}")
                if manifest.errors:
                    for e in manifest.errors:
                        print(f"  [bundle error] {e}")
        else:
            produce(single_args)
    else:
        # Standalone solver mode
        from solver.solve import run_solver
        run_solver(args)


def cmd_analyze(args: argparse.Namespace) -> None:
    """Route to analyzer."""
    # Import and call analyzer main with converted args
    from analyzer.__main__ import main as analyzer_main

    # Build args namespace expected by analyzer
    analyzer_args = argparse.Namespace(
        car=args.car,
        ibt=args.ibt,
        lap=getattr(args, 'lap', None),
        save=getattr(args, 'save', None),
    )

    # Temporarily replace sys.argv for analyzer's main
    old_argv = sys.argv
    sys.argv = ['analyzer', '--car', args.car, '--ibt', args.ibt]
    if hasattr(args, 'lap') and args.lap:
        sys.argv.extend(['--lap', str(args.lap)])
    if hasattr(args, 'save') and args.save:
        sys.argv.extend(['--save', args.save])

    try:
        analyzer_main()
    finally:
        sys.argv = old_argv


def cmd_solve(args: argparse.Namespace) -> None:
    """Route to solver.solve."""
    from solver.solve import run_solver
    run_solver(args)


def cmd_calibrate(args: argparse.Namespace) -> None:
    """Route to car_model.auto_calibrate."""
    from car_model.auto_calibrate import main as calibrate_main

    # Build sys.argv expected by auto_calibrate
    old_argv = sys.argv
    new_argv = ['car_model.auto_calibrate', '--car', args.car]
    if getattr(args, 'status', False):
        new_argv.append('--status')
    elif getattr(args, 'protocol', False):
        new_argv.append('--protocol')
    else:
        if getattr(args, 'ibt', None):
            ibts = args.ibt if isinstance(args.ibt, list) else [args.ibt]
            new_argv.append('--ibt')
            new_argv.extend(str(p) for p in ibts)
        if getattr(args, 'ibt_dir', None):
            new_argv.extend(['--ibt-dir', str(args.ibt_dir)])
        if getattr(args, 'sto_json', None):
            new_argv.extend(['--sto-json', str(args.sto_json)])
        if getattr(args, 'refit', False):
            new_argv.append('--refit')
        if getattr(args, 'clear', False):
            new_argv.append('--clear')

    sys.argv = new_argv
    try:
        calibrate_main()
    finally:
        sys.argv = old_argv


def cmd_ingest(args: argparse.Namespace) -> None:
    """Route to learner.ingest."""
    from learner.ingest import main as ingest_main

    # Build sys.argv for ingest
    old_argv = sys.argv
    sys.argv = ['learner.ingest', '--car', args.car, '--ibt', args.ibt]
    if hasattr(args, 'wing') and args.wing:
        sys.argv.extend(['--wing', str(args.wing)])
    if hasattr(args, 'lap') and args.lap:
        sys.argv.extend(['--lap', str(args.lap)])
    if hasattr(args, 'all_laps') and args.all_laps:
        sys.argv.append('--all-laps')
    if hasattr(args, 'single_lap') and args.single_lap:
        sys.argv.append('--single-lap')

    try:
        ingest_main()
    finally:
        sys.argv = old_argv


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ioptimal",
        description="IOptimal — GTP setup solver (pipeline + physics)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Check if called with legacy args (no subcommand)
    if len(sys.argv) > 1 and not sys.argv[1].startswith('-') and sys.argv[1] not in ['produce', 'analyze', 'solve', 'ingest', 'calibrate', 'run']:
        # First arg is a subcommand
        pass
    elif len(sys.argv) > 1 and sys.argv[1].startswith('--'):
        # Legacy usage: --car ... (no subcommand)
        # Route to produce_legacy
        return main_legacy()

    subparsers = parser.add_subparsers(dest='command', help='Subcommands')

    # ── produce subcommand ──
    produce_parser = subparsers.add_parser(
        'produce',
        help='Full pipeline: IBT → analysis → physics solver → .sto',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    produce_parser.add_argument("--car", required=True,
                        help="Car canonical name (bmw | ferrari | porsche | cadillac | acura)")
    produce_parser.add_argument("--ibt", action="append", default=None, metavar="IBT",
                        help="IBT telemetry file. Repeat for multiple files: "
                             "--ibt f1.ibt --ibt f2.ibt → per-file reports + comparison table.")
    produce_parser.add_argument("--track", default=None,
                        help="Track name for standalone solver (used when no --ibt)")
    produce_parser.add_argument("--wing", type=float, default=None,
                        help="Wing angle in degrees (auto-detected from IBT if not set)")

    produce_parser.add_argument("--lap", type=int, default=None,
                        help="Lap number to analyze (default: best lap)")
    produce_parser.add_argument("--fuel", type=float, default=None,
                        help="Fuel load in liters (auto-detected from IBT if not set)")
    produce_parser.add_argument("--balance", type=float, default=None,
                        help="Target DF balance %% (default: car-specific)")
    produce_parser.add_argument("--tolerance", type=float, default=0.1,
                        help="Balance tolerance %% (default: 0.1)")
    produce_parser.add_argument("--free", action="store_true",
                        help="Free optimization (don't pin front RH at sim floor)")
    produce_parser.add_argument("--legacy-solver", action="store_true",
                        help="Force the legacy sequential solver path for BMW/Sebring validation")
    produce_parser.add_argument("--sto", type=str, default=None,
                        help="Export iRacing .sto setup file")
    produce_parser.add_argument("--json", type=str, default=None,
                        help="Save full JSON summary to file")
    produce_parser.add_argument("--report-only", action="store_true",
                        help="Print only the final report (suppress per-step progress)")
    produce_parser.add_argument("--verbose", action="store_true",
                        help="Show full step-by-step solver output (default: report only)")
    produce_parser.add_argument("--space", action="store_true",
                        help="Run setup space exploration (feasible ranges + flat bottom)")
    produce_parser.add_argument("--search-mode",
        choices=["quick", "standard", "exhaustive", "maximum"],
        default=None, dest="search_mode",
        help="Run hierarchical legal-space grid search")
    produce_parser.add_argument("--top-n", type=int, default=5, dest="top_n",
                        help="Number of top candidates to display when using --search-mode (default: 5)")
    produce_parser.add_argument("--min-lap-time", type=float, default=108.0, dest="min_lap_time",
                        help="Absolute floor for valid laps in seconds (default: 108.0)")
    produce_parser.add_argument("--outlier-pct", type=float, default=0.115, dest="outlier_pct",
                        help="Max %% above lap-time median to accept (default: 0.115)")
    produce_parser.add_argument("--no-learn", action="store_true",
                        help="Skip IBT ingestion / empirical corrections (read-only run)")
    produce_parser.add_argument("--bundle-dir", type=str, default=None, dest="bundle_dir",
                        help="Write all artifacts (.sto, .json, report, manifest) to this directory")

    # ── analyze subcommand ──
    analyze_parser = subparsers.add_parser(
        'analyze',
        help='Analyze one IBT session (diagnose setup, driver style, handling)',
    )
    analyze_parser.add_argument("--car", required=True,
                        help="Car canonical name (bmw | ferrari | porsche | cadillac | acura)")
    analyze_parser.add_argument("--ibt", required=True,
                        help="Path to IBT telemetry file")
    analyze_parser.add_argument("--lap", type=int, default=None,
                        help="Specific lap number to analyze (default: best lap)")
    analyze_parser.add_argument("--save", default=None,
                        help="Save JSON report to this path")

    # ── solve subcommand ──
    solve_parser = subparsers.add_parser(
        'solve',
        help='Standalone physics solver (no IBT required)',
    )
    solve_parser.add_argument("--car", required=True,
                        help="Car canonical name (bmw | ferrari | porsche | cadillac | acura)")
    solve_parser.add_argument("--track", required=True,
                        help="Track name (e.g., sebring)")
    solve_parser.add_argument("--wing", type=float, required=True,
                        help="Wing angle in degrees")
    solve_parser.add_argument("--balance", type=float, default=None,
                        help="Target DF balance %% (default: car-specific)")
    solve_parser.add_argument("--tolerance", type=float, default=0.1,
                        help="Balance tolerance %% (default: 0.1)")
    solve_parser.add_argument("--fuel", type=float, default=None,
                        help="Fuel load in liters")
    solve_parser.add_argument("--free", action="store_true",
                        help="Free optimization (don't pin front RH at sim floor)")
    solve_parser.add_argument("--sto", type=str, default=None,
                        help="Export iRacing .sto setup file")
    solve_parser.add_argument("--json", type=str, default=None,
                        help="Save full JSON summary to file")
    solve_parser.add_argument("--verbose", action="store_true",
                        help="Show full step-by-step solver output")

    # ── calibrate subcommand ──
    calibrate_parser = subparsers.add_parser(
        'calibrate',
        help='Auto-calibrate car model from IBT sessions (builds per-car physics models)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    calibrate_parser.add_argument("--car", required=True,
                        choices=["bmw", "cadillac", "ferrari", "acura", "porsche"],
                        help="Car to calibrate")
    calibrate_parser.add_argument("--ibt", nargs="+", default=None,
                        help="One or more IBT files to add to the calibration dataset")
    calibrate_parser.add_argument("--ibt-dir", default=None, dest="ibt_dir",
                        help="Directory to scan for IBT files")
    calibrate_parser.add_argument("--sto-json", default=None, dest="sto_json",
                        help="Path to a setupdelta.com decrypted .sto JSON (for spring lookup table)")
    calibrate_parser.add_argument("--status", action="store_true",
                        help="Print calibration status for the car and exit")
    calibrate_parser.add_argument("--refit", action="store_true",
                        help="Re-fit models from all accumulated data points")
    calibrate_parser.add_argument("--clear", action="store_true",
                        help="Clear all calibration data for the car")
    calibrate_parser.add_argument("--protocol", action="store_true",
                        help="Generate step-by-step iRacing calibration sweep instructions")

    # ── ingest subcommand ──
    ingest_parser = subparsers.add_parser(
        'ingest',
        help='Ingest IBT into knowledge base (learning system)',
    )
    ingest_parser.add_argument("--car", required=True,
                        help="Car canonical name (bmw | ferrari | porsche | cadillac | acura)")
    ingest_parser.add_argument("--ibt", required=True,
                        help="Path to IBT telemetry file")
    ingest_parser.add_argument("--wing", type=float, default=None,
                        help="Wing angle override")
    ingest_parser.add_argument("--lap", type=int, default=None,
                        help="Specific lap number to analyze")
    ingest_parser.add_argument("--all-laps", action="store_true",
                        help="(Default behaviour — kept for backward compatibility)")
    ingest_parser.add_argument("--single-lap", action="store_true", dest="single_lap",
                        help="Legacy: ingest only the best lap as one observation")

    # ── run subcommand (unified: does everything, outputs to folder) ──
    run_parser = subparsers.add_parser(
        'run',
        help='Run everything: analyze + ingest + calibrate + produce. Outputs organized folder.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    run_parser.add_argument("--car", required=True,
                        help="Car canonical name (bmw | ferrari | porsche | cadillac | acura)")
    run_parser.add_argument("--ibt", action="append", required=True, metavar="IBT",
                        help="IBT telemetry file(s)")
    run_parser.add_argument("--wing", type=float, default=None,
                        help="Wing angle in degrees (auto-detected from IBT if not set)")
    run_parser.add_argument("--output-dir", type=str, default=None, dest="output_dir",
                        help="Output directory (default: output/<car>_<track>_<timestamp>/)")
    run_parser.add_argument("--scenario", type=str, default="single_lap_safe",
                        choices=["single_lap_safe", "quali", "sprint", "race"],
                        help="Scenario profile (default: single_lap_safe)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # Route to subcommand
    if args.command == 'produce':
        # Apply defaults
        if not hasattr(args, 'verbose') or not args.verbose:
            args.report_only = True
        if args.search_mode:
            run_grid_search(args)
        else:
            cmd_produce(args)
    elif args.command == 'analyze':
        cmd_analyze(args)
    elif args.command == 'solve':
        cmd_solve(args)
    elif args.command == 'ingest':
        cmd_ingest(args)
    elif args.command == 'calibrate':
        cmd_calibrate(args)
    elif args.command == 'run':
        cmd_run(args)


def cmd_run(args: argparse.Namespace) -> None:
    """Unified run: analyze + ingest + calibrate + produce, all outputs to one folder."""
    import json
    from datetime import datetime
    from pathlib import Path

    car_name = args.car
    ibt_paths = args.ibt
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Determine output directory
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        # Auto-name from first IBT track info
        track_label = "unknown"
        try:
            from track_model.ibt_parser import IBTFile
            ibt = IBTFile(ibt_paths[0])
            ti = ibt.track_info()
            track_label = ti.get("track_name", "unknown").lower().replace(" ", "_")[:30]
        except Exception:
            pass
        out_dir = Path("output") / f"{car_name}_{track_label}_{timestamp}"

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {out_dir}")
    print("=" * 60)

    manifest = {"car": car_name, "timestamp": timestamp, "files": {}}

    # Step 1: Calibrate (refit from accumulated data)
    print("\n[1/4] Calibrating car model...")
    try:
        from car_model.auto_calibrate import print_status
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print_status(car_name)
        cal_text = buf.getvalue()
        cal_path = out_dir / "calibration_status.txt"
        cal_path.write_text(cal_text, encoding="utf-8")
        manifest["files"]["calibration_status"] = str(cal_path)
        print(f"  Saved: {cal_path}")
    except Exception as e:
        print(f"  Calibration status: {e}")

    # Step 2: Analyze
    print("\n[2/4] Analyzing telemetry...")
    analysis_path = out_dir / "analysis.json"
    for ibt_path in ibt_paths:
        try:
            analyze_ns = argparse.Namespace(car=car_name, ibt=ibt_path, lap=None, save=str(analysis_path))
            cmd_analyze(analyze_ns)
            manifest["files"]["analysis"] = str(analysis_path)
            print(f"  Saved: {analysis_path}")
        except Exception as e:
            print(f"  Analysis error: {e}")

    # Step 3: Ingest (learn from session)
    print("\n[3/4] Ingesting session for learning...")
    for ibt_path in ibt_paths:
        try:
            ingest_ns = argparse.Namespace(
                car=car_name, ibt=ibt_path, wing=args.wing, lap=None,
                all_laps=False, single_lap=True,
            )
            cmd_ingest(ingest_ns)
            print(f"  Ingested: {ibt_path}")
        except Exception as e:
            print(f"  Ingest error: {e}")

    # Step 4: Produce (full pipeline → setup)
    print("\n[4/4] Running physics solver...")
    sto_path = out_dir / "setup.sto"
    json_path = out_dir / "solver_result.json"
    try:
        produce_ns = argparse.Namespace(
            car=car_name,
            ibt=ibt_paths,
            wing=args.wing,
            track=None,
            lap=None,
            fuel=None,
            balance=None,
            tolerance=0.1,
            free=False,
            legacy_solver=False,
            sto=str(sto_path),
            json=str(json_path),
            report_only=True,
            verbose=False,
            space=False,
            search_mode=None,
            top_n=5,
            min_lap_time=60.0,
            outlier_pct=0.115,
            no_learn=False,
            bundle_dir=None,
        )
        cmd_produce(produce_ns)
        manifest["files"]["setup_sto"] = str(sto_path)
        manifest["files"]["solver_result"] = str(json_path)
        print(f"  Saved: {sto_path}")
        print(f"  Saved: {json_path}")
    except Exception as e:
        print(f"  Produce error: {e}")

    # Write manifest
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\n{'=' * 60}")
    print(f"All outputs saved to: {out_dir}")
    print(f"Files:")
    for label, path in manifest["files"].items():
        print(f"  {label}: {path}")


def main_legacy() -> None:
    """Legacy entry point for backward compatibility.

    Handles: python -m ioptimal --car bmw --ibt session.ibt --wing 17
    """
    parser = argparse.ArgumentParser(
        prog="ioptimal",
        description="IOptimal — GTP setup solver (pipeline + physics)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── Car / session ──
    parser.add_argument("--car", required=True,
                        help="Car canonical name (bmw | ferrari | porsche | cadillac | acura)")
    parser.add_argument("--ibt", action="append", default=None, metavar="IBT",
                        help="IBT telemetry file. Repeat for multiple files")
    parser.add_argument("--track", default=None,
                        help="Track name for standalone solver (used when no --ibt)")
    parser.add_argument("--wing", type=float, default=None,
                        help="Wing angle in degrees (auto-detected from IBT if not set)")
    parser.add_argument("--lap", type=int, default=None,
                        help="Lap number to analyze (default: best lap)")
    parser.add_argument("--fuel", type=float, default=None,
                        help="Fuel load in liters (auto-detected from IBT if not set)")
    parser.add_argument("--balance", type=float, default=None,
                        help="Target DF balance %% (default: car-specific)")
    parser.add_argument("--tolerance", type=float, default=0.1,
                        help="Balance tolerance %% (default: 0.1)")
    parser.add_argument("--free", action="store_true",
                        help="Free optimization (don't pin front RH at sim floor)")
    parser.add_argument("--legacy-solver", action="store_true",
                        help="Force the legacy sequential solver path for BMW/Sebring validation")
    parser.add_argument("--sto", type=str, default=None,
                        help="Export iRacing .sto setup file")
    parser.add_argument("--json", type=str, default=None,
                        help="Save full JSON summary to file")
    parser.add_argument("--report-only", action="store_true",
                        help="Print only the final report (suppress per-step progress)")
    parser.add_argument("--verbose", action="store_true",
                        help="Show full step-by-step solver output (default: report only)")
    parser.add_argument("--space", action="store_true",
                        help="Run setup space exploration (feasible ranges + flat bottom)")
    parser.add_argument("--search-mode",
        choices=["quick", "standard", "exhaustive", "maximum"],
        default=None, dest="search_mode",
        help="Run hierarchical legal-space grid search")
    parser.add_argument("--top-n", type=int, default=5, dest="top_n",
                        help="Number of top candidates to display when using --search-mode (default: 5)")
    parser.add_argument("--min-lap-time", type=float, default=108.0, dest="min_lap_time",
                        help="Absolute floor for valid laps in seconds (default: 108.0)")
    parser.add_argument("--outlier-pct", type=float, default=0.115, dest="outlier_pct",
                        help="Max %% above lap-time median to accept (default: 0.115)")
    parser.add_argument("--no-learn", action="store_true",
                        help="Skip IBT ingestion / empirical corrections (read-only run)")
    parser.add_argument("--bundle-dir", type=str, default=None, dest="bundle_dir",
                        help="Write all artifacts (.sto, .json, report, manifest) to this directory")

    args = parser.parse_args()

    # Default: quiet (report-only). Use --verbose to see step-by-step solver output.
    if not args.verbose:
        args.report_only = True

    # ── Validate ──
    if args.ibt is None and args.track is None:
        parser.error("Provide --ibt (full pipeline) or --track (standalone solver)")

    if args.wing is None and args.ibt is None:
        parser.error("--wing is required when running without --ibt")

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # ── Grid search mode (overrides all other routing) ───────────────
    if args.search_mode:
        run_grid_search(args)
        return

    if args.ibt:
        if len(args.ibt) > 1:
            # ── Multi-IBT: individual reports + comparison table ──────
            run_multi_ibt(args)
        else:
            # ── Single IBT: full pipeline ─────────────────────────────
            import copy
            single_args = copy.copy(args)
            single_args.ibt = args.ibt[0]

            if getattr(args, 'bundle_dir', None):
                from pipeline.produce import produce_result
                result = produce_result(single_args)
                if result is not None:
                    from output.bundle import bundle_from_pipeline_result
                    manifest = bundle_from_pipeline_result(
                        args.bundle_dir,
                        result,
                        report_text=result.get("report"),
                    )
                    print(f"\nBundle written to: {manifest.bundle_dir}")
                    for p in manifest.artifacts:
                        print(f"  {p}")
                    if manifest.errors:
                        for e in manifest.errors:
                            print(f"  [bundle error] {e}")
            else:
                from pipeline.produce import produce
                produce(single_args)
    else:
        # ── Standalone physics solver (no telemetry) ──────────────────
        from solver.solve import run_solver
        run_solver(args)


if __name__ == "__main__":
    main()
