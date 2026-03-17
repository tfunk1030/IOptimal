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
    if measured is not None and (measured.front_carcass_mean_c > 0 or
                                  measured.front_pressure_mean_kpa > 0):
        lines.append(section("TYRE DATA"))
        lines.append("")

        if measured.front_carcass_mean_c > 0 or measured.rear_carcass_mean_c > 0:
            lines.append("  Carcass temp (operating):")
            if measured.front_carcass_mean_c > 0:
                lines.append(f"    Fronts:  {measured.front_carcass_mean_c:.0f} C")
            if measured.rear_carcass_mean_c > 0:
                lines.append(f"    Rears:   {measured.rear_carcass_mean_c:.0f} C")

        if measured.front_pressure_mean_kpa > 0 or measured.rear_pressure_mean_kpa > 0:
            lines.append("  Hot pressure:")
            if measured.front_pressure_mean_kpa > 0:
                lines.append(f"    Fronts:  {measured.front_pressure_mean_kpa:.0f} kPa")
            if measured.rear_pressure_mean_kpa > 0:
                lines.append(f"    Rears:   {measured.rear_pressure_mean_kpa:.0f} kPa")

        has_spread = (measured.front_temp_spread_lf_c != 0 or
                      measured.front_temp_spread_rf_c != 0 or
                      measured.rear_temp_spread_lr_c != 0 or
                      measured.rear_temp_spread_rr_c != 0)
        if has_spread:
            lines.append("  Temp spread (inner - outer):")
            if measured.front_temp_spread_lf_c != 0:
                lines.append(f"    LF:  {measured.front_temp_spread_lf_c:+.1f} C")
            if measured.front_temp_spread_rf_c != 0:
                lines.append(f"    RF:  {measured.front_temp_spread_rf_c:+.1f} C")
            if measured.rear_temp_spread_lr_c != 0:
                lines.append(f"    LR:  {measured.rear_temp_spread_lr_c:+.1f} C")
            if measured.rear_temp_spread_rr_c != 0:
                lines.append(f"    RR:  {measured.rear_temp_spread_rr_c:+.1f} C")

        if measured.front_wear_mean_pct > 0 or measured.rear_wear_mean_pct > 0:
            lines.append("  Tyre wear remaining:")
            if measured.front_wear_mean_pct > 0:
                lines.append(f"    Fronts:  {measured.front_wear_mean_pct:.0f}%")
            if measured.rear_wear_mean_pct > 0:
                lines.append(f"    Rears:   {measured.rear_wear_mean_pct:.0f}%")

        lines.append("")

    # --- Handling Dynamics ---
    if measured is not None and (measured.understeer_mean_deg != 0 or
                                  measured.body_slip_p95_deg > 0):
        lines.append(section("HANDLING DYNAMICS"))
        lines.append("")

        if measured.understeer_mean_deg != 0:
            us_label = "UNDERSTEER" if measured.understeer_mean_deg > 0 else "OVERSTEER"
            lines.append(f"  Understeer angle (mean):    {measured.understeer_mean_deg:+.1f} deg  ({us_label})")
        if measured.understeer_low_speed_deg != 0:
            lines.append(f"    Low speed (<120 kph):     {measured.understeer_low_speed_deg:+.1f} deg")
        if measured.understeer_high_speed_deg != 0:
            lines.append(f"    High speed (>180 kph):    {measured.understeer_high_speed_deg:+.1f} deg")

        lines.append(f"  Body slip angle (p95):      {measured.body_slip_p95_deg:.1f} deg")
        rear_power_slip = (
            measured.rear_power_slip_ratio_p95
            if measured.rear_power_slip_ratio_p95 > 0
            else measured.rear_slip_ratio_p95
        )
        front_lock = (
            measured.front_braking_lock_ratio_p95
            if measured.front_braking_lock_ratio_p95 > 0
            else measured.front_slip_ratio_p95
        )
        if rear_power_slip > 0:
            lines.append(f"  Rear power slip (p95):      {rear_power_slip:.3f}")
        if front_lock > 0:
            lines.append(f"  Front braking lock (p95):   {front_lock:.3f}")

        if measured.yaw_rate_correlation > 0:
            qual = "excellent" if measured.yaw_rate_correlation > 0.90 else \
                   "good" if measured.yaw_rate_correlation > 0.75 else \
                   "marginal" if measured.yaw_rate_correlation > 0.60 else "poor"
            lines.append(f"  Yaw correlation (R^2):      {measured.yaw_rate_correlation:.3f}  ({qual})")

        roll_proxy = (
            measured.roll_distribution_proxy
            if measured.roll_distribution_proxy > 0
            else measured.lltd_measured
        )
        if roll_proxy > 0:
            lines.append(f"  Roll distribution proxy:    {roll_proxy*100:.1f}%")
        if measured.roll_gradient_measured_deg_per_g > 0:
            lines.append(f"  Roll gradient:              {measured.roll_gradient_measured_deg_per_g:.3f} deg/g")

        if measured.roll_rate_p95_deg_per_s > 0:
            lines.append(f"  Roll rate (p95):            {measured.roll_rate_p95_deg_per_s:.1f} deg/s")
        if measured.pitch_rate_p95_deg_per_s > 0:
            lines.append(f"  Pitch rate (p95):           {measured.pitch_rate_p95_deg_per_s:.1f} deg/s")
        if measured.pitch_range_braking_deg > 0:
            lines.append(
                f"  Braking pitch range:        {measured.pitch_range_braking_deg:.2f} deg"
            )
        if measured.pitch_mean_braking_deg != 0:
            lines.append(
                f"  Mean pitch under brake:     {measured.pitch_mean_braking_deg:+.2f} deg"
            )
        if measured.hydraulic_brake_split_pct > 0:
            confidence = measured.hydraulic_brake_split_confidence or "low"
            lines.append(
                f"  Hydraulic brake split:      {measured.hydraulic_brake_split_pct:.1f}% ({confidence})"
            )
        if measured.braking_decel_mean_g > 0:
            lines.append(
                f"  Braking decel mean/peak:    {measured.braking_decel_mean_g:.2f} / "
                f"{measured.braking_decel_peak_g:.2f} g"
            )
        if measured.front_brake_wheel_decel_asymmetry_p95_ms2 > 0:
            lines.append(
                f"  Front brake decel asym.:    "
                f"{measured.front_brake_wheel_decel_asymmetry_p95_ms2:.1f} m/s^2"
            )

        if measured.front_rh_settle_time_ms > 0:
            lines.append(f"  Front settle time:          {measured.front_rh_settle_time_ms:.0f} ms")
        if measured.rear_rh_settle_time_ms > 0:
            lines.append(f"  Rear settle time:           {measured.rear_rh_settle_time_ms:.0f} ms")
        if measured.metric_fallbacks:
            lines.append("  Metric fallbacks:")
            for fallback in measured.metric_fallbacks:
                lines.append(f"    - {fallback}")

        lines.append("")

    # --- Heave Spring Travel ---
    if measured is not None and (measured.front_heave_defl_p99_mm > 0 or
                                  measured.front_heave_travel_used_pct > 0):
        lines.append(section("HEAVE SPRING TRAVEL"))
        lines.append("")

        if measured.front_heave_defl_mean_mm > 0:
            lines.append(f"  Front heave deflection:")
            lines.append(f"    Mean:  {measured.front_heave_defl_mean_mm:.1f} mm"
                         f"    p99:  {measured.front_heave_defl_p99_mm:.1f} mm"
                         f"    Max:  {measured.front_heave_defl_max_mm:.1f} mm")
        if measured.front_heave_travel_used_pct > 0:
            pct = measured.front_heave_travel_used_pct
            flag = " *** WARNING ***" if pct > 85 else ""
            lines.append(f"  Travel used (at speed):     {pct:.0f}%{flag}")
        if measured.front_heave_travel_used_braking_pct > 0:
            pct = measured.front_heave_travel_used_braking_pct
            flag = " *** WARNING ***" if pct > 85 else ""
            lines.append(f"  Travel used (braking):      {pct:.0f}%{flag}")
        if measured.heave_bottoming_events_front > 0:
            lines.append(f"  Bottoming events (front):   {measured.heave_bottoming_events_front}")

        if measured.rear_heave_defl_mean_mm > 0:
            lines.append(f"  Rear heave deflection:")
            lines.append(f"    Mean:  {measured.rear_heave_defl_mean_mm:.1f} mm"
                         f"    p99:  {measured.rear_heave_defl_p99_mm:.1f} mm"
                         f"    Max:  {measured.rear_heave_defl_max_mm:.1f} mm")
        if measured.rear_heave_travel_used_pct > 0:
            lines.append(f"  Rear travel used:           {measured.rear_heave_travel_used_pct:.0f}%")
        if measured.heave_bottoming_events_rear > 0:
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

    if "safety" not in problem_cats:
        ok.append("Safety: no bottoming, no vortex burst")

    if measured is not None:
        if "front rh variance" not in problem_symptoms and measured.front_rh_std_mm > 0:
            ok.append(f"Front variance: {measured.front_rh_std_mm:.1f}mm (threshold 8.0)")
        if "rear rh variance" not in problem_symptoms and measured.rear_rh_std_mm > 0:
            ok.append(f"Rear variance: {measured.rear_rh_std_mm:.1f}mm (threshold 10.0)")

    if "balance" not in problem_cats:
        ok.append("Balance: neutral handling, roll-distribution proxy within range")

    if "damper" not in problem_cats:
        ok.append("Dampers: settle time and yaw response OK")

    if "thermal" not in problem_cats and measured is not None:
        if measured.front_carcass_mean_c > 0:
            ok.append(f"Thermals: tyres in operating window")

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
