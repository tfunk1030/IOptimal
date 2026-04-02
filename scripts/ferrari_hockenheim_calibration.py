"""Ferrari 499P Hockenheim correlation + setup calibration helper.

Purpose
-------
Build a practical calibration pack for Ferrari GTP at Hockenheim from:
1) a setupdelta-style setup JSON (rows[] schema), and
2) one or more telemetry files (.ibt or .zip containing .ibt).

The script produces:
- channel coverage + signal statistics (all discovered channels are reported),
- derived braking/corner/ride-height demand metrics,
- recommended Hockenheim-oriented setup adjustments starting from the provided baseline.

This is intentionally heuristic-first (exploratory support tier), but deterministic
and transparent so the result can be versioned and iterated week-to-week.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
import hashlib
from pathlib import Path
from statistics import mean
from typing import Any



@dataclass
class SetupValue:
    label: str
    section: str | None
    metric_value: str | None


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.search(r"[-+]?\d*\.?\d+", str(value))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _index_setup_rows(rows: list[dict[str, Any]]) -> dict[tuple[str, str | None], SetupValue]:
    indexed: dict[tuple[str, str | None], SetupValue] = {}
    for row in rows:
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        section = row.get("section")
        indexed[(label, section)] = SetupValue(
            label=label,
            section=section,
            metric_value=row.get("metric_value"),
        )
    return indexed


def _extract_baseline(indexed: dict[tuple[str, str | None], SetupValue]) -> dict[str, Any]:
    def g(label: str, section: str | None = None) -> str | None:
        return indexed.get((label, section), SetupValue(label, section, None)).metric_value

    return {
        "rear_wing_deg": _parse_float(g("Rear wing angle", "Aero Settings")),
        "brake_bias_pct": _parse_float(g("Brake pressure bias", "Brake Spec")),
        "front_arb_blade": _parse_float(g("ARB blades", "Front")),
        "rear_arb_blade": _parse_float(g("ARB blades", "Rear")),
        "front_toe": _parse_float(g("Toe-in", "Front")),
        "rear_toe_l": _parse_float(g("Toe-in", "Left Rear")),
        "rear_toe_r": _parse_float(g("Toe-in", "Right Rear")),
        "tc_slip": _parse_float(g("Traction control slip", "Traction Control")),
        "tc_gain": _parse_float(g("Traction control gain", "Traction Control")),
        "fuel_target_lap": _parse_float(g("Fuel target", "Fuel")),
        "fuel_l": _parse_float(g("Fuel level", "Rear")),
        "front_rh_speed_mm": _parse_float(g("Front RH at speed", "Aero Calculator")),
        "rear_rh_speed_mm": _parse_float(g("Rear RH at speed", "Aero Calculator")),
    }


def _parse_range_bound(text: str | None) -> float | None:
    if text is None:
        return None
    return _parse_float(text)


def _validate_setup_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    validated: list[dict[str, Any]] = []
    row_ids: list[str] = []
    out_of_range = 0
    in_range = 0

    for row in rows:
        row_id = str(row.get("row_id") or "")
        row_ids.append(row_id)
        label = str(row.get("label") or "")
        section = row.get("section")
        value = row.get("metric_value")
        numeric_value = _parse_float(value if isinstance(value, str) else None)

        range_metric = row.get("range_metric") if isinstance(row.get("range_metric"), dict) else None
        min_v = _parse_range_bound(range_metric.get("min")) if range_metric else None
        max_v = _parse_range_bound(range_metric.get("max")) if range_metric else None
        within = None
        if numeric_value is not None and min_v is not None and max_v is not None:
            within = min_v <= numeric_value <= max_v
            if within:
                in_range += 1
            else:
                out_of_range += 1

        expected_row_id = hashlib.md5(f"{label}|{section}".encode("utf-8")).hexdigest()[:8]
        validated.append(
            {
                "row_id": row_id,
                "label": label,
                "section": section,
                "metric_value": value,
                "numeric_value": numeric_value,
                "range_min": min_v,
                "range_max": max_v,
                "within_range": within,
                "row_id_format_ok": bool(re.fullmatch(r"[0-9a-f]{8}", row_id)),
                "derived_row_id_hint": expected_row_id,
            }
        )

    unique_ids = len(set(row_ids))
    ferrari_only_labels = [
        "Hybrid rear drive enabled",
        "Hybrid rear drive corner pct",
        "Front Diff Spec",
        "Rear Diff Spec",
    ]
    label_text = " ".join(str(r.get("label") or "") + " " + str(r.get("section") or "") for r in rows).lower()
    ferrari_signature_hits = sum(1 for token in ferrari_only_labels if token.lower().split()[0] in label_text)

    return {
        "summary": {
            "row_count": len(rows),
            "unique_row_ids": unique_ids,
            "duplicate_row_ids": len(rows) - unique_ids,
            "range_checked_rows": in_range + out_of_range,
            "in_range_rows": in_range,
            "out_of_range_rows": out_of_range,
            "ferrari_signature_hits": ferrari_signature_hits,
        },
        "rows": validated,
    }


def _setting_correlations(baseline: dict[str, Any]) -> dict[str, Any]:
    front = baseline.get("front_rh_speed_mm")
    rear = baseline.get("rear_rh_speed_mm")
    rear_toe_l = baseline.get("rear_toe_l")
    rear_toe_r = baseline.get("rear_toe_r")
    return {
        "dynamic_rake_mm": None if front is None or rear is None else rear - front,
        "rear_toe_split": None if rear_toe_l is None or rear_toe_r is None else rear_toe_l - rear_toe_r,
        "rear_toe_avg": None if rear_toe_l is None or rear_toe_r is None else (rear_toe_l + rear_toe_r) / 2.0,
        "arb_balance_index": None
        if baseline.get("front_arb_blade") is None or baseline.get("rear_arb_blade") is None
        else baseline["front_arb_blade"] - baseline["rear_arb_blade"],
    }


def _session_stats(ibt_path: Path) -> dict[str, Any]:
    # Lazy import because IBT parsing requires numpy; this script can still
    # produce baseline recommendations without telemetry parsing.
    from track_model.ibt_parser import IBTFile

    ibt = IBTFile(ibt_path)
    names = sorted(ibt.var_lookup.keys())
    out: dict[str, Any] = {
        "file": ibt_path.name,
        "track_info": ibt.track_info(),
        "car_info": ibt.car_info(),
        "sample_rate_hz": ibt.tick_rate,
        "samples": ibt.record_count,
        "duration_s": round(ibt.duration_s, 3),
        "channels_available": names,
        "channel_count": len(names),
    }

    def ch(name: str):
        return ibt.channel(name)

    speed = ch("Speed")
    brake = ch("Brake")
    throttle = ch("Throttle")
    lat = ch("LatAccel")
    lon = ch("LongAccel")

    metrics: dict[str, Any] = {}
    if speed is not None:
        speed_kph = speed * 3.6
        speed_kph_list = [float(v) for v in speed_kph]
        metrics["speed_kph_mean"] = float(mean(speed_kph_list))
        metrics["speed_kph_p95"] = _percentile(speed_kph_list, 95)
        metrics["speed_kph_max"] = max(speed_kph_list)

    if lat is not None:
        lat_abs = [abs(float(v)) for v in lat]
        lat_g = [v / 9.80665 for v in lat_abs] if max(lat_abs) > 7.0 else lat_abs
        metrics["lat_g_mean_abs"] = float(mean(lat_g))
        metrics["lat_g_p95_abs"] = _percentile(lat_g, 95)
        metrics["lat_g_max_abs"] = max(lat_g)

    if lon is not None:
        lon_list = [float(v) for v in lon]
        lon_g = [v / 9.80665 for v in lon_list] if max(abs(v) for v in lon_list) > 7.0 else lon_list
        metrics["long_g_min"] = min(lon_g)

    if brake is not None and speed is not None:
        speed_kph = speed * 3.6
        heavy = (brake > 0.8) & (speed_kph > 80)
        heavy_count = int(sum(bool(v) for v in heavy))
        metrics["heavy_brake_samples"] = heavy_count
        metrics["heavy_brake_ratio"] = heavy_count / len(heavy) if len(heavy) else 0.0

    if throttle is not None and speed is not None:
        speed_kph = speed * 3.6
        exits = (speed_kph > 70) & (speed_kph < 170)
        if any(bool(v) for v in exits):
            exit_vals = [float(v) for v, mask in zip(throttle, exits) if bool(mask)]
            metrics["mid_speed_throttle_mean"] = float(mean(exit_vals))

    # ride heights if present
    rh_names = ["LFrideHeight", "RFrideHeight", "LRrideHeight", "RRrideHeight"]
    if all(name in ibt.var_lookup for name in rh_names):
        lf = ch("LFrideHeight") * 1000.0
        rf = ch("RFrideHeight") * 1000.0
        lr = ch("LRrideHeight") * 1000.0
        rr = ch("RRrideHeight") * 1000.0
        fr = (lf + rf) / 2.0
        rrh = (lr + rr) / 2.0
        fr_list = [float(v) for v in fr]
        rrh_list = [float(v) for v in rrh]
        rake = [r - f for r, f in zip(rrh_list, fr_list)]
        metrics["front_rh_mm_p05"] = _percentile(fr_list, 5)
        metrics["rear_rh_mm_p05"] = _percentile(rrh_list, 5)
        metrics["rake_mm_p50"] = _percentile(rake, 50)

    out["derived_metrics"] = metrics
    return out


def _aggregate_metrics(sessions: list[dict[str, Any]]) -> dict[str, float]:
    bucket: dict[str, list[float]] = {}
    for s in sessions:
        for k, v in s.get("derived_metrics", {}).items():
            if isinstance(v, (int, float)) and math.isfinite(float(v)):
                bucket.setdefault(k, []).append(float(v))
    return {k: float(mean(vs)) for k, vs in bucket.items() if vs}


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = (len(ordered) - 1) * (pct / 100.0)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return ordered[lo]
    frac = idx - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def _recommend_hockenheim(baseline: dict[str, Any], aggregate: dict[str, float]) -> dict[str, Any]:
    # Hockenheim: long straights + heavy braking. Trim drag from max wing baseline,
    # preserve confidence in braking stability with slight front bias increase.
    wing = baseline.get("rear_wing_deg")
    target_wing = None if wing is None else max(12.0, min(17.0, wing - 2.0))

    bb = baseline.get("brake_bias_pct")
    heavy_brake_ratio = aggregate.get("heavy_brake_ratio", 0.0)
    target_bb = bb
    if bb is not None:
        delta = 1.5 if heavy_brake_ratio > 0.045 else 1.0
        target_bb = max(42.0, min(65.0, bb + delta))

    tc_slip = baseline.get("tc_slip")
    tc_gain = baseline.get("tc_gain")

    recs = {
        "rear_wing_angle_deg": {
            "baseline": wing,
            "recommended": target_wing,
            "reason": "Trim 2 deg from max-wing baseline to improve top speed for Parabolika while staying in legal Ferrari 499P wing window.",
        },
        "brake_pressure_bias_pct": {
            "baseline": bb,
            "recommended": target_bb,
            "reason": "Hockenheim has repeated heavy brake zones; a +1.0–1.5% front bias shift improves entry stability.",
        },
        "tc1_slip": {
            "baseline": tc_slip,
            "recommended": tc_slip if tc_slip is None else max(1.0, tc_slip - 1.0),
            "reason": "Lower slip threshold one step for traction support out of slow exits (T2 hairpin, stadium).",
        },
        "tc2_gain": {
            "baseline": tc_gain,
            "recommended": tc_gain if tc_gain is None else max(1.0, tc_gain - 1.0),
            "reason": "Slightly lower torque-cut gain to avoid over-intervention after rotation-heavy entries.",
        },
        "aero_rake_target_mm": {
            "baseline": None
            if baseline.get("rear_rh_speed_mm") is None or baseline.get("front_rh_speed_mm") is None
            else baseline["rear_rh_speed_mm"] - baseline["front_rh_speed_mm"],
            "recommended": 23.0,
            "reason": "Aim near 22–24 mm dynamic rake for Ferrari baseline balance in medium/high-speed load at trimmed wing.",
        },
        "fuel_target_l_per_lap": {
            "baseline": baseline.get("fuel_target_lap"),
            "recommended": 3.0,
            "reason": "Start around 3.0 L/lap and refine with race-length telemetry; lower drag setup should slightly reduce consumption.",
        },
    }
    return recs


def build_calibration_report(setup_json: Path, telemetry: list[Path]) -> dict[str, Any]:
    payload = json.loads(setup_json.read_text(encoding="utf-8"))
    car_name = str(payload.get("carName", "")).lower()
    if car_name != "ferrari499p":
        raise ValueError(
            f"Ferrari calibration helper requires carName='ferrari499p', got '{payload.get('carName')}'."
        )
    rows = payload.get("rows") or []
    indexed = _index_setup_rows(rows)
    baseline = _extract_baseline(indexed)
    validation = _validate_setup_rows(rows)
    correlations = _setting_correlations(baseline)

    sessions: list[dict[str, Any]] = []
    missing_files: list[str] = []
    for file_path in telemetry:
        if not file_path.exists():
            missing_files.append(str(file_path))
            continue
        sessions.append(_session_stats(file_path))

    aggregate = _aggregate_metrics(sessions)
    recommendations = _recommend_hockenheim(baseline, aggregate)

    return {
        "car": payload.get("carName", "ferrari499p"),
        "track": "hockenheim_grand_prix",
        "baseline_settings": baseline,
        "setting_correlations": correlations,
        "setup_validation": validation,
        "telemetry_files_requested": [str(p) for p in telemetry],
        "telemetry_files_missing": missing_files,
        "telemetry_sessions": sessions,
        "aggregate_channel_metrics": aggregate,
        "recommended_settings": recommendations,
        "confidence": "exploratory",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Ferrari 499P Hockenheim calibration helper")
    parser.add_argument("--setup-json", required=True, help="Path to setupdelta JSON")
    parser.add_argument("--telemetry", nargs="*", default=[], help="IBT or zip telemetry files")
    parser.add_argument("--output", required=True, help="Output report JSON path")
    args = parser.parse_args()

    report = build_calibration_report(
        setup_json=Path(args.setup_json),
        telemetry=[Path(p) for p in args.telemetry],
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote calibration report: {out_path}")
    print(f"Telemetry sessions parsed: {len(report['telemetry_sessions'])}")
    if report["telemetry_files_missing"]:
        print(f"Telemetry files missing: {len(report['telemetry_files_missing'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
