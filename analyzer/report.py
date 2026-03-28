"""Setup analysis report -- ASCII terminal output and JSON export.

Conventions:
- ASCII only (no Unicode, cp1252 safe)
- 63-char width sections
- Structured JSON via dataclass serialization
"""

from __future__ import annotations

import json
import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analyzer.diagnose import Diagnosis, Problem
from analyzer.recommend import AnalysisResult, SetupChange
from analyzer.setup_reader import CurrentSetup
from analyzer.extract import MeasuredState
from analyzer.telemetry_truth import summarize_signal_quality


def _safe_dict(obj: Any) -> Any:
    """Recursively convert dataclasses/objects to JSON-serializable dicts."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        d = {}
        for f in dataclasses.fields(obj):
            val = getattr(obj, f.name)
            # Skip track profile (too large)
            if f.name == "measured_track_profile":
                d[f.name] = None
                continue
            d[f.name] = _safe_dict(val)
        return d
    if isinstance(obj, list):
        return [_safe_dict(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _safe_dict(v) for k, v in obj.items()}
    return obj


def format_report(
    result: AnalysisResult,
    car_name: str,
    track_name: str,
    ibt_name: str,
    measured: MeasuredState | None = None,
) -> str:
    """Generate the ASCII analysis report.

    Returns a multi-line string ready for print().
    """
    width = 63
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    diag = result.diagnosis

    def section(title: str) -> str:
        pad = (width - len(title) - 2) // 2
        return "=" * pad + f" {title} " + "=" * (width - pad - len(title) - 2)

    # Format lap time
    mins = int(diag.lap_time_s) // 60
    secs = diag.lap_time_s - mins * 60

    # Assessment styling
    assessment_map = {
        "fast": "FAST",
        "competitive": "COMPETITIVE",
        "compromised": "COMPROMISED",
        "dangerous": "DANGEROUS",
    }
    assessment_str = assessment_map.get(diag.assessment, diag.assessment.upper())

    lines = [
        "=" * width,
        "  SETUP ANALYSIS REPORT",
        f"  {now}",
        "=" * width,
        f"  Car:        {car_name}",
        f"  Track:      {track_name}",
        f"  Session:    {ibt_name}",
        f"  Lap:        {diag.lap_number} ({mins}:{secs:06.3f})",
        f"  Assessment: {assessment_str}",
        "=" * width,
        "",
    ]

    def _num(name: str, default: float = 0.0) -> float:
        if measured is None:
            return default
        try:
            value = getattr(measured, name, None)
            return default if value is None else float(value)
        except (TypeError, ValueError):
            return default

    # --- Current Setup Summary ---
    setup = result.current_setup
    lines.append(section("CURRENT SETUP (from IBT)"))
    lines.append("")
    lines.append(
        f"  Wing: {setup.wing_angle_deg:.0f} deg    "
        f"DF balance: {setup.df_balance_pct:.2f}%    "
        f"L/D: {setup.ld_ratio:.3f}"
    )
    lines.append(
        f"  Front RH: {setup.static_front_rh_mm:.1f}mm (static)  "
        f"{setup.front_rh_at_speed_mm:.1f}mm (dynamic)"
    )
    lines.append(
        f"  Rear RH:  {setup.static_rear_rh_mm:.1f}mm (static)  "
        f"{setup.rear_rh_at_speed_mm:.1f}mm (dynamic)"
    )
    lines.append(
        f"  Heave: {setup.front_heave_nmm:.0f} N/mm   "
        f"Third: {setup.rear_third_nmm:.0f} N/mm"
    )
    lines.append(
        f"  FARB: {setup.front_arb_size}/{setup.front_arb_blade}     "
        f"RARB: {setup.rear_arb_size}/{setup.rear_arb_blade}"
    )
    lines.append(
        f"  Camber: F {setup.front_camber_deg:.1f} / R {setup.rear_camber_deg:.1f}    "
        f"Toe: F {setup.front_toe_mm:.1f} / R {setup.rear_toe_mm:.1f}"
    )
    lines.append(
        f"  Dampers F: {setup.front_ls_comp}/{setup.front_ls_rbd}/"
        f"{setup.front_hs_comp}/{setup.front_hs_rbd}/{setup.front_hs_slope}  "
        f"R: {setup.rear_ls_comp}/{setup.rear_ls_rbd}/"
        f"{setup.rear_hs_comp}/{setup.rear_hs_rbd}/{setup.rear_hs_slope}"
    )
    lines.append(
        f"  Brake bias: {setup.brake_bias_pct:.1f}%    "
        f"Diff preload: {setup.diff_preload_nm:.0f} Nm    "
        f"Fuel: {setup.fuel_l:.0f} L"
    )
    lines.append(
        f"  TC gain: {setup.tc_gain}    TC slip: {setup.tc_slip}"
    )
    if (
        setup.brake_bias_target != 0.0
        or setup.brake_bias_migration != 0.0
        or setup.front_master_cyl_mm > 0.0
        or setup.rear_master_cyl_mm > 0.0
        or setup.pad_compound
    ):
        lines.append(
            f"  Brake target: {setup.brake_bias_target:+.1f}    "
            f"Migration: {setup.brake_bias_migration:+.1f}"
        )
        lines.append(
            f"  Master cyl: F {setup.front_master_cyl_mm:.1f} / "
            f"R {setup.rear_master_cyl_mm:.1f} mm    "
            f"Pad: {setup.pad_compound or 'unknown'}"
        )
    lines.append("")

    # --- Handling Diagnosis ---
    lines.append(section("HANDLING DIAGNOSIS"))
    lines.append("")

    # Show OK items first
    ok_items = _get_ok_items(diag, measured)
    for item in ok_items:
        lines.append(f"  [OK] {item}")

    # Show problems
    for problem in diag.problems:
        severity_tag = {
            "critical": "CRITICAL",
            "significant": "ISSUE",
            "minor": "NOTE",
        }.get(problem.severity, "???")

        lines.append(f"  [{severity_tag}] {problem.symptom}")
        # Wrap the cause
        wrapped = _wrap_text(problem.cause, width - 10)
        for line in wrapped:
            lines.append(f"          {line}")

    lines.append("")

    signal_lines = summarize_signal_quality(measured) if measured is not None else []
    if signal_lines:
        lines.append(section("SIGNAL CONFIDENCE"))
        lines.append("")
        for signal_line in signal_lines:
            wrapped = _wrap_text(signal_line, width - 4)
            for line in wrapped:
                lines.append(f"  {line}")
        if measured is not None and measured.metric_fallbacks:
            lines.append("  Fallbacks used:")
            for fallback in measured.metric_fallbacks[:8]:
                lines.append(f"    - {fallback}")
        lines.append("")

    if diag.state_issues or diag.overhaul_assessment is not None:
        lines.append(section("PRIMARY CAR STATES"))
        lines.append("")
        if diag.overhaul_assessment is not None:
            lines.append(
                f"  Overhaul: {diag.overhaul_assessment.classification}  "
                f"(conf {diag.overhaul_assessment.confidence:.0%}, score {diag.overhaul_assessment.score:.2f})"
            )
            for reason in diag.overhaul_assessment.reasons[:3]:
                wrapped = _wrap_text(reason, width - 6)
                for line in wrapped:
                    lines.append(f"    {line}")
        for issue in diag.state_issues[:5]:
            lines.append(
                f"  - {issue.state_id}  sev={issue.severity:.2f}  "
                f"conf={issue.confidence:.2f}  loss~{issue.estimated_loss_ms:.0f}ms"
            )
            if issue.recommended_direction:
                wrapped = _wrap_text(issue.recommended_direction, width - 6)
                for line in wrapped:
                    lines.append(f"    {line}")
        lines.append("")

    # --- Recommended Changes ---
    if result.changes:
        lines.append(section(f"RECOMMENDED CHANGES ({len(result.changes)} changes)"))
        lines.append("")

        for i, change in enumerate(result.changes, 1):
            cat_tag = change.parameter.upper()
            # Show step area
            step_names = {1: "AERO", 2: "PLATFORM", 4: "BALANCE", 5: "GEOMETRY", 6: "DAMPER"}
            area = step_names.get(change.step, f"STEP {change.step}")

            lines.append(f"  {i}. [{area}] {change.parameter}: "
                         f"{_fmt_change_value(change.current, change.units)} -> "
                         f"{_fmt_change_value(change.recommended, change.units)}")
            wrapped = _wrap_text(change.reasoning, width - 6)
            for line in wrapped:
                lines.append(f"    {line}")
            lines.append("")
    else:
        lines.append(section("NO CHANGES NEEDED"))
        lines.append("")
        lines.append("  Setup looks good. No issues detected above thresholds.")
        lines.append("")

    # --- Unchanged items ---
    if result.changes:
        unchanged = _get_unchanged(result)
        if unchanged:
            lines.append(section("UNCHANGED"))
            lines.append("")
            wrapped = _wrap_text(", ".join(unchanged), width - 4)
            for line in wrapped:
                lines.append(f"  {line}")
            lines.append("")

    # --- Tyre Data ---
    front_carcass = _num("front_carcass_mean_c")
    rear_carcass = _num("rear_carcass_mean_c")
    front_pressure = _num("front_pressure_mean_kpa")
    rear_pressure = _num("rear_pressure_mean_kpa")
    front_spread_lf = _num("front_temp_spread_lf_c")
    front_spread_rf = _num("front_temp_spread_rf_c")
    rear_spread_lr = _num("rear_temp_spread_lr_c")
    rear_spread_rr = _num("rear_temp_spread_rr_c")
    front_wear = _num("front_wear_mean_pct")
    rear_wear = _num("rear_wear_mean_pct")
    if measured is not None and (front_carcass > 0 or front_pressure > 0):
        lines.append(section("TYRE DATA"))
        lines.append("")

        if front_carcass > 0 or rear_carcass > 0:
            lines.append("  Carcass temp (operating):")
            if front_carcass > 0:
                lines.append(f"    Fronts:  {front_carcass:.0f} C")
            if rear_carcass > 0:
                lines.append(f"    Rears:   {rear_carcass:.0f} C")

        if front_pressure > 0 or rear_pressure > 0:
            lines.append("  Hot pressure:")
            if front_pressure > 0:
                lines.append(f"    Fronts:  {front_pressure:.0f} kPa")
            if rear_pressure > 0:
                lines.append(f"    Rears:   {rear_pressure:.0f} kPa")

        has_spread = any(value != 0 for value in (front_spread_lf, front_spread_rf, rear_spread_lr, rear_spread_rr))
        if has_spread:
            lines.append("  Temp spread (inner - outer):")
            if front_spread_lf != 0:
                lines.append(f"    LF:  {front_spread_lf:+.1f} C")
            if front_spread_rf != 0:
                lines.append(f"    RF:  {front_spread_rf:+.1f} C")
            if rear_spread_lr != 0:
                lines.append(f"    LR:  {rear_spread_lr:+.1f} C")
            if rear_spread_rr != 0:
                lines.append(f"    RR:  {rear_spread_rr:+.1f} C")

        if front_wear > 0 or rear_wear > 0:
            lines.append("  Tyre wear remaining:")
            if front_wear > 0:
                lines.append(f"    Fronts:  {front_wear:.0f}%")
            if rear_wear > 0:
                lines.append(f"    Rears:   {rear_wear:.0f}%")

        lines.append("")

    # --- Handling Dynamics ---
    understeer_mean = _num("understeer_mean_deg")
    understeer_low = _num("understeer_low_speed_deg")
    understeer_high = _num("understeer_high_speed_deg")
    body_slip = _num("body_slip_p95_deg")
    rear_power_slip_p95 = _num("rear_power_slip_ratio_p95")
    rear_slip_p95 = _num("rear_slip_ratio_p95")
    front_lock_p95 = _num("front_braking_lock_ratio_p95")
    front_slip_p95 = _num("front_slip_ratio_p95")
    yaw_corr = _num("yaw_rate_correlation")
    roll_proxy_direct = _num("roll_distribution_proxy")
    lltd_measured = _num("lltd_measured")
    roll_gradient = _num("roll_gradient_measured_deg_per_g")
    roll_rate = _num("roll_rate_p95_deg_per_s")
    pitch_rate = _num("pitch_rate_p95_deg_per_s")
    pitch_range_braking = _num("pitch_range_braking_deg")
    pitch_mean_braking = _num("pitch_mean_braking_deg")
    hydraulic_split = _num("hydraulic_brake_split_pct")
    braking_decel_mean = _num("braking_decel_mean_g")
    braking_decel_peak = _num("braking_decel_peak_g")
    brake_asym = _num("front_brake_wheel_decel_asymmetry_p95_ms2")
    front_settle = _num("front_rh_settle_time_ms")
    rear_settle = _num("rear_rh_settle_time_ms")
    if measured is not None and (understeer_mean != 0 or body_slip > 0):
        lines.append(section("HANDLING DYNAMICS"))
        lines.append("")

        if understeer_mean != 0:
            us_label = "UNDERSTEER" if understeer_mean > 0 else "OVERSTEER"
            lines.append(f"  Understeer angle (mean):    {understeer_mean:+.1f} deg  ({us_label})")
        if understeer_low != 0:
            lines.append(f"    Low speed (<120 kph):     {understeer_low:+.1f} deg")
        if understeer_high != 0:
            lines.append(f"    High speed (>180 kph):    {understeer_high:+.1f} deg")

        lines.append(f"  Body slip angle (p95):      {body_slip:.1f} deg")
        rear_power_slip = rear_power_slip_p95 if rear_power_slip_p95 > 0 else rear_slip_p95
        front_lock = front_lock_p95 if front_lock_p95 > 0 else front_slip_p95
        if rear_power_slip > 0:
            lines.append(f"  Rear power slip (p95):      {rear_power_slip:.3f}")
        if front_lock > 0:
            lines.append(f"  Front braking lock (p95):   {front_lock:.3f}")

        if yaw_corr > 0:
            qual = "excellent" if yaw_corr > 0.90 else \
                   "good" if yaw_corr > 0.75 else \
                   "marginal" if yaw_corr > 0.60 else "poor"
            lines.append(f"  Yaw correlation (R^2):      {yaw_corr:.3f}  ({qual})")

        roll_proxy = roll_proxy_direct if roll_proxy_direct > 0 else lltd_measured
        if roll_proxy > 0:
            lines.append(f"  Roll distribution proxy:    {roll_proxy*100:.1f}%")
        if roll_gradient > 0:
            lines.append(f"  Roll gradient:              {roll_gradient:.3f} deg/g")

        if roll_rate > 0:
            lines.append(f"  Roll rate (p95):            {roll_rate:.1f} deg/s")
        if pitch_rate > 0:
            lines.append(f"  Pitch rate (p95):           {pitch_rate:.1f} deg/s")
        if pitch_range_braking > 0:
            lines.append(
                f"  Braking pitch range:        {pitch_range_braking:.2f} deg"
            )
        if pitch_mean_braking != 0:
            lines.append(
                f"  Mean pitch under brake:     {pitch_mean_braking:+.2f} deg"
            )
        if hydraulic_split > 0:
            confidence = measured.hydraulic_brake_split_confidence or "low"
            lines.append(
                f"  Hydraulic brake split:      {hydraulic_split:.1f}% ({confidence})"
            )
        if braking_decel_mean > 0:
            lines.append(
                f"  Braking decel mean/peak:    {braking_decel_mean:.2f} / "
                f"{braking_decel_peak:.2f} g"
            )
        if brake_asym > 0:
            lines.append(
                f"  Front brake decel asym.:    "
                f"{brake_asym:.1f} m/s^2"
            )

        if front_settle > 0:
            lines.append(f"  Front settle time:          {front_settle:.0f} ms")
        if rear_settle > 0:
            lines.append(f"  Rear settle time:           {rear_settle:.0f} ms")
        if measured.metric_fallbacks:
            lines.append("  Metric fallbacks:")
            for fallback in measured.metric_fallbacks:
                lines.append(f"    - {fallback}")

        lines.append("")

    # --- Heave Spring Travel ---
    if measured is not None and ((measured.front_heave_defl_p99_mm or 0) > 0 or
                                  (measured.front_heave_travel_used_pct or 0) > 0):
        lines.append(section("HEAVE SPRING TRAVEL"))
        lines.append("")

        if (measured.front_heave_defl_mean_mm or 0) > 0:
            lines.append(f"  Front heave deflection:")
            lines.append(f"    Mean:  {measured.front_heave_defl_mean_mm:.1f} mm"
                         f"    p99:  {measured.front_heave_defl_p99_mm:.1f} mm"
                         f"    Max:  {measured.front_heave_defl_max_mm:.1f} mm")
        if (measured.front_heave_travel_used_pct or 0) > 0:
            pct = measured.front_heave_travel_used_pct
            flag = " *** WARNING ***" if pct > 85 else ""
            lines.append(f"  Travel used (at speed):     {pct:.0f}%{flag}")
        if (measured.front_heave_travel_used_braking_pct or 0) > 0:
            pct = measured.front_heave_travel_used_braking_pct
            flag = " *** WARNING ***" if pct > 85 else ""
            lines.append(f"  Travel used (braking):      {pct:.0f}%{flag}")
        if (measured.heave_bottoming_events_front or 0) > 0:
            lines.append(f"  Bottoming events (front):   {measured.heave_bottoming_events_front}")

        if (measured.rear_heave_defl_mean_mm or 0) > 0:
            lines.append(f"  Rear heave deflection:")
            lines.append(f"    Mean:  {measured.rear_heave_defl_mean_mm:.1f} mm"
                         f"    p99:  {measured.rear_heave_defl_p99_mm:.1f} mm"
                         f"    Max:  {measured.rear_heave_defl_max_mm:.1f} mm")
        if (measured.rear_heave_travel_used_pct or 0) > 0:
            lines.append(f"  Rear travel used:           {measured.rear_heave_travel_used_pct:.0f}%")
        if (measured.heave_bottoming_events_rear or 0) > 0:
            lines.append(f"  Bottoming events (rear):    {measured.heave_bottoming_events_rear}")

        lines.append("")

    lines.append("=" * width)

    return "\n".join(lines)


def save_analysis_json(
    result: AnalysisResult,
    car_name: str,
    track_name: str,
    measured: MeasuredState | None,
    output_path: str | Path,
) -> None:
    """Save analysis results as structured JSON."""
    diag = result.diagnosis

    summary = {
        "meta": {
            "car": car_name,
            "track": track_name,
            "lap": diag.lap_number,
            "lap_time_s": diag.lap_time_s,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        },
        "assessment": diag.assessment,
        "problem_count": len(diag.problems),
        "change_count": len(result.changes),
        "problems": [_safe_dict(p) for p in diag.problems],
        "changes": [_safe_dict(c) for c in result.changes],
        "current_setup": _safe_dict(result.current_setup),
        "improved_setup": _safe_dict(result.improved_setup),
    }

    if measured is not None:
        summary["measured"] = _safe_dict(measured)
        summary["signal_quality_summary"] = summarize_signal_quality(measured)
        summary["telemetry_signals"] = _safe_dict(getattr(measured, "telemetry_signals", {}))
        summary["telemetry_bundle"] = _safe_dict(getattr(measured, "telemetry_bundle", {}))

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(summary, indent=2))


def _fmt_change_value(value: float, units: str) -> str:
    """Format a value for the change display."""
    if units in ("clicks", "setting", "events"):
        return f"{int(value)}"
    elif units == "%":
        return f"{value:.1f}%"
    elif units == "deg":
        return f"{value:.1f} deg"
    elif units == "mm":
        return f"{value:.1f}mm"
    elif units == "N/mm":
        return f"{value:.0f} N/mm"
    elif units == "Nm":
        return f"{value:.0f} Nm"
    elif units == "ratio":
        return f"{value:.3f}"
    else:
        return f"{value:.1f} {units}"


def _get_ok_items(diag: Diagnosis, measured: MeasuredState | None) -> list[str]:
    """Generate list of items that are OK (not flagged as problems)."""
    ok = []
    problem_cats = {p.category for p in diag.problems}
    problem_symptoms = " ".join(p.symptom.lower() for p in diag.problems)

    def _positive(name: str) -> float | None:
        try:
            value = getattr(measured, name, None)
            if value is None:
                return None
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if value > 0.0 else None

    if "safety" not in problem_cats:
        ok.append("Safety: no bottoming, no vortex burst")

    if measured is not None:
        front_rh_std = _positive("front_rh_std_mm")
        rear_rh_std = _positive("rear_rh_std_mm")
        if "front rh variance" not in problem_symptoms and front_rh_std is not None:
            ok.append(f"Front variance: {front_rh_std:.1f}mm (threshold 8.0)")
        if "rear rh variance" not in problem_symptoms and rear_rh_std is not None:
            ok.append(f"Rear variance: {rear_rh_std:.1f}mm (threshold 10.0)")

    if "balance" not in problem_cats:
        ok.append("Balance: neutral handling, roll-distribution proxy within range")

    if "damper" not in problem_cats:
        ok.append("Dampers: settle time and yaw response OK")

    if "thermal" not in problem_cats and measured is not None:
        if _positive("front_carcass_mean_c") is not None:
            ok.append("Thermals: tyres in operating window")

    return ok


def _get_unchanged(result: AnalysisResult) -> list[str]:
    """List parameters that were NOT changed."""
    changed_params = {c.parameter for c in result.changes}
    all_params = [
        ("wing", "wing_angle_deg"),
        ("ride heights", "static_front_rh_mm"),
        ("heave spring", "front_heave_nmm"),
        ("third spring", "rear_third_nmm"),
        ("front ARB", "front_arb_blade"),
        ("rear ARB", "rear_arb_blade"),
        ("front camber", "front_camber_deg"),
        ("rear camber", "rear_camber_deg"),
        ("front toe", "front_toe_mm"),
        ("rear toe", "rear_toe_mm"),
        ("front dampers", "front_ls_rbd"),
        ("rear dampers", "rear_ls_rbd"),
        ("brake bias", "brake_bias_pct"),
        ("diff preload", "diff_preload_nm"),
        ("TC", "tc_slip"),
    ]

    unchanged = []
    for label, param in all_params:
        if param not in changed_params:
            unchanged.append(label)

    return unchanged


def _wrap_text(text: str, max_width: int) -> list[str]:
    """Simple word-wrap for report text."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > max_width:
            if current:
                lines.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        lines.append(current)
    return lines or [""]
