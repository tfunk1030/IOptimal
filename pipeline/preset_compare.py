"""Generate race/sprint/quali presets from one IBT and compare them."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.produce import produce_result


PRESET_CONFIGS: dict[str, dict[str, Any]] = {
    "race": {
        "stint": True,
        "stint_select": "all",
        "fuel_attr": "race_fuel",
    },
    "sprint": {
        "stint": True,
        "stint_select": "last",
        "fuel_attr": "sprint_fuel",
    },
    "quali": {
        "stint": False,
        "stint_select": "longest",
        "fuel_attr": "quali_fuel",
    },
}


COMPARISON_FIELDS: list[tuple[str, str, str]] = [
    ("Fuel Load", "fuel_l", "L"),
    ("Wing", "wing", "deg"),
    ("Front Pushrod", "step1.front_pushrod_offset_mm", "mm"),
    ("Rear Pushrod", "step1.rear_pushrod_offset_mm", "mm"),
    ("Front Heave", "step2.front_heave_nmm", "N/mm"),
    ("Front Heave Perch", "step2.perch_offset_front_mm", "mm"),
    ("Rear Third", "step2.rear_third_nmm", "N/mm"),
    ("Rear Third Perch", "step2.perch_offset_rear_mm", "mm"),
    ("Front Torsion OD", "step3.front_torsion_od_mm", "mm"),
    ("Rear Spring", "step3.rear_spring_rate_nmm", "N/mm"),
    ("Rear Spring Perch", "step3.rear_spring_perch_mm", "mm"),
    ("Front ARB Size", "step4.front_arb_size", ""),
    ("Front ARB Blade", "step4.front_arb_blade_start", ""),
    ("Rear ARB Size", "step4.rear_arb_size", ""),
    ("Rear ARB Blade", "step4.rear_arb_blade_start", ""),
    ("Rear ARB Slow", "step4.rarb_blade_slow_corner", ""),
    ("Rear ARB Fast", "step4.rarb_blade_fast_corner", ""),
    ("Front Camber", "step5.front_camber_deg", "deg"),
    ("Rear Camber", "step5.rear_camber_deg", "deg"),
    ("Front Toe", "step5.front_toe_mm", "mm"),
    ("Rear Toe", "step5.rear_toe_mm", "mm"),
    ("Brake Bias", "supporting.brake_bias_pct", "%"),
    ("Brake Target", "supporting.brake_bias_target", ""),
    ("Brake Migration", "supporting.brake_bias_migration", ""),
    ("Front Master Cyl", "supporting.front_master_cyl_mm", "mm"),
    ("Rear Master Cyl", "supporting.rear_master_cyl_mm", "mm"),
    ("Pad Compound", "supporting.pad_compound", ""),
    ("Diff Preload", "supporting.diff_preload_nm", "Nm"),
    ("Diff Coast", "supporting.diff_ramp_coast", "deg"),
    ("Diff Drive", "supporting.diff_ramp_drive", "deg"),
    ("Diff Plates", "supporting.diff_clutch_plates", ""),
    ("TC Gain", "supporting.tc_gain", ""),
    ("TC Slip", "supporting.tc_slip", ""),
    ("LF LS Comp", "step6.lf.ls_comp", ""),
    ("LF LS Rbd", "step6.lf.ls_rbd", ""),
    ("LF HS Comp", "step6.lf.hs_comp", ""),
    ("LF HS Rbd", "step6.lf.hs_rbd", ""),
    ("LF HS Slope", "step6.lf.hs_slope", ""),
    ("RF LS Comp", "step6.rf.ls_comp", ""),
    ("RF LS Rbd", "step6.rf.ls_rbd", ""),
    ("RF HS Comp", "step6.rf.hs_comp", ""),
    ("RF HS Rbd", "step6.rf.hs_rbd", ""),
    ("RF HS Slope", "step6.rf.hs_slope", ""),
    ("LR LS Comp", "step6.lr.ls_comp", ""),
    ("LR LS Rbd", "step6.lr.ls_rbd", ""),
    ("LR HS Comp", "step6.lr.hs_comp", ""),
    ("LR HS Rbd", "step6.lr.hs_rbd", ""),
    ("LR HS Slope", "step6.lr.hs_slope", ""),
    ("RR LS Comp", "step6.rr.ls_comp", ""),
    ("RR LS Rbd", "step6.rr.ls_rbd", ""),
    ("RR HS Comp", "step6.rr.hs_comp", ""),
    ("RR HS Rbd", "step6.rr.hs_rbd", ""),
    ("RR HS Slope", "step6.rr.hs_slope", ""),
]


def _resolve_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
    return current


def _format_value(value: Any, units: str = "") -> str:
    if value is None:
        rendered = "N/A"
    elif isinstance(value, float):
        rendered = f"{value:.3f}".rstrip("0").rstrip(".")
    else:
        rendered = str(value)
    return f"{rendered} {units}".rstrip()


def _normalized(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 4)
    return value


def _selected_segments_text(result: dict[str, Any]) -> str:
    dataset = result.get("stint_dataset")
    if dataset is None:
        return "best lap path"
    segments = getattr(dataset, "selected_segments", []) or []
    if not segments:
        return "none"
    ranges = []
    for segment in segments:
        if segment.start_lap == segment.end_lap:
            ranges.append(f"{segment.start_lap}")
        else:
            ranges.append(f"{segment.start_lap}-{segment.end_lap}")
    return ", ".join(ranges)


def _stint_objective_text(result: dict[str, Any]) -> str:
    stint_solve = result.get("stint_solve")
    if stint_solve is None or getattr(stint_solve, "objective", None) is None:
        dataset = result.get("stint_dataset")
        fallback = getattr(dataset, "fallback_mode", None) if dataset is not None else None
        return fallback or "single_lap"
    objective = stint_solve.objective
    return f"{objective.get('total', 0.0):.4f}"


def _build_args(
    *,
    car: str,
    ibt: str,
    fuel: float,
    stint: bool,
    stint_select: str,
    stint_max_laps: int,
    json_path: str,
    sto_path: str,
    wing: float | None = None,
    balance: float | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        car=car,
        track=None,
        ibt=[ibt],
        wing=wing,
        lap=None,
        balance=balance,
        tolerance=0.1,
        fuel=fuel,
        free=False,
        sto=sto_path,
        json=json_path,
        setup_json=None,
        report_only=True,
        no_learn=True,
        legacy_solver=False,
        min_lap_time=None,
        outlier_pct=0.115,
        stint=stint,
        stint_threshold=1.5,
        stint_select=stint_select,
        stint_max_laps=stint_max_laps,
        verbose=False,
        explore_legal_space=False,
        search_budget=1000,
        keep_weird=False,
        search_mode=None,
        top_n=1,
        search_family=None,
        explore=False,
        objective_profile="balanced",
        learn=False,
        auto_learn=False,
    )


def _print_divider(title: str, width: int = 100) -> None:
    print()
    print("=" * width)
    print(title)
    print("=" * width)


def _print_comparison(results: dict[str, dict[str, Any]]) -> None:
    presets = ["race", "sprint", "quali"]
    name_w = 20
    col_w = 24

    _print_divider("PRESET SUMMARY")
    header = f"{'Preset':<{name_w}}{'Race':>{col_w}}{'Sprint':>{col_w}}{'Quali':>{col_w}}"
    print(header)
    print("-" * len(header))

    summary_rows = [
        ("Selected Segments", [ _selected_segments_text(results[name]) for name in presets ]),
        ("Candidate Family", [ str(results[name].get("selected_candidate_family") or "none") for name in presets ]),
        ("Candidate Score", [
            _format_value(results[name].get("selected_candidate_score"), "")
            for name in presets
        ]),
        ("Stint Obj Total", [_stint_objective_text(results[name]) for name in presets]),
        ("Fallback Mode", [
            str(getattr(results[name].get("stint_solve"), "fallback_mode", None)
                or getattr(results[name].get("stint_dataset"), "fallback_mode", None)
                or "none")
            for name in presets
        ]),
        ("JSON", [results[name]["json_path"].name for name in presets]),
        ("STO", [results[name]["sto_path"].name for name in presets]),
    ]
    for label, values in summary_rows:
        print(f"{label:<{name_w}}{values[0]:>{col_w}}{values[1]:>{col_w}}{values[2]:>{col_w}}")

    _print_divider("CHANGED SETUP FIELDS")
    field_header = f"{'Field':<{name_w}}{'Race':>{col_w}}{'Sprint':>{col_w}}{'Quali':>{col_w}}"
    print(field_header)
    print("-" * len(field_header))
    changed_count = 0
    for label, path, units in COMPARISON_FIELDS:
        values = [_resolve_path(results[name], path) for name in presets]
        if len({_normalized(value) for value in values}) <= 1:
            continue
        changed_count += 1
        rendered = [_format_value(value, units) for value in values]
        print(f"{label:<{name_w}}{rendered[0]:>{col_w}}{rendered[1]:>{col_w}}{rendered[2]:>{col_w}}")
    if changed_count == 0:
        print("No setup deltas across presets.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate race/sprint/quali presets from one IBT and compare them side-by-side.",
    )
    parser.add_argument("--car", required=True, help="Car name (e.g. bmw)")
    parser.add_argument("--ibt", required=True, help="Path to one IBT file")
    parser.add_argument("--out-dir", default="tmp/preset_compare", help="Output directory for .json/.sto artifacts")
    parser.add_argument("--wing", type=float, default=None, help="Wing override")
    parser.add_argument("--balance", type=float, default=None, help="Target DF balance override")
    parser.add_argument("--race-fuel", type=float, default=58.0, help="Fuel load for the race preset")
    parser.add_argument("--sprint-fuel", type=float, default=35.0, help="Fuel load for the sprint preset")
    parser.add_argument("--quali-fuel", type=float, default=8.0, help="Fuel load for the quali preset")
    parser.add_argument("--stint-max-laps", type=int, default=40, help="Maximum stint laps to score directly")
    args = parser.parse_args()

    ibt_path = Path(args.ibt)
    if not ibt_path.exists():
        raise FileNotFoundError(f"IBT file not found: {ibt_path}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = ibt_path.stem

    results: dict[str, dict[str, Any]] = {}
    for preset_name in ("race", "sprint", "quali"):
        config = PRESET_CONFIGS[preset_name]
        fuel = float(getattr(args, config["fuel_attr"]))
        json_path = out_dir / f"{stem}_{preset_name}.json"
        sto_path = out_dir / f"{stem}_{preset_name}.sto"
        produce_args = _build_args(
            car=args.car,
            ibt=str(ibt_path),
            fuel=fuel,
            stint=bool(config["stint"]),
            stint_select=str(config["stint_select"]),
            stint_max_laps=int(args.stint_max_laps),
            json_path=str(json_path),
            sto_path=str(sto_path),
            wing=args.wing,
            balance=args.balance,
        )
        result = produce_result(produce_args, emit_report=False, compact_report=True)
        result["json_path"] = json_path
        result["sto_path"] = sto_path
        results[preset_name] = result

    for preset_name in ("race", "sprint", "quali"):
        _print_divider(f"{preset_name.upper()} SETUP")
        print(results[preset_name]["report"])

    _print_comparison(results)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    main()
