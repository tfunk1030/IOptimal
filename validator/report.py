"""Validation report — ASCII terminal output and JSON export.

Follows the same conventions as output/report.py:
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

from validator.compare import Comparison
from validator.classify import ValidationResult
from validator.recommend import FeedbackOutput, ParameterAdjustment, ModelCorrection
from validator.extract import MeasuredState


def _safe_dict(obj: Any) -> Any:
    """Recursively convert dataclasses/objects to JSON-serializable dicts."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _safe_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_safe_dict(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _safe_dict(v) for k, v in obj.items()}
    return obj


def format_report(
    result: ValidationResult,
    feedback: FeedbackOutput,
    car_name: str,
    track_name: str,
    lap_number: int,
    lap_time_s: float,
    ibt_name: str,
    measured: MeasuredState | None = None,
) -> str:
    """Generate the ASCII validation report.

    Returns a multi-line string ready for print().
    """
    width = 63
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def section(title: str) -> str:
        pad = (width - len(title) - 2) // 2
        return "=" * pad + f" {title} " + "=" * (width - pad - len(title) - 2)

    # Format lap time
    mins = int(lap_time_s) // 60
    secs = lap_time_s - mins * 60

    # Verdict styling
    verdict_map = {
        "good_setup": "GOOD SETUP",
        "needs_tweaking": "NEEDS TWEAKING",
        "rethink": "RETHINK REQUIRED",
    }
    verdict_str = verdict_map.get(result.overall_verdict, result.overall_verdict.upper())

    lines = [
        "=" * width,
        "  SETUP VALIDATION REPORT",
        f"  {now}",
        "=" * width,
        f"  Car:        {car_name}",
        f"  Track:      {track_name}",
        f"  Session:    {ibt_name}",
        f"  Lap:        {lap_number} ({mins}:{secs:06.3f})",
        f"  Confidence: {result.confidence_score}/100",
        f"  Verdict:    {verdict_str}",
        "=" * width,
        "",
    ]

    # --- Comparison Matrix ---
    lines.append(section("COMPARISON MATRIX"))
    lines.append("")
    lines.append("  Step  Parameter                  Predicted  Measured  Delta   Status")
    lines.append("  ----  -------------------------  ---------  --------  ------  ---------")

    all_comps = result.confirmed + result.tweaks + result.rethinks
    all_comps.sort(key=lambda c: (c.step, c.parameter))

    for comp in all_comps:
        if comp in result.confirmed:
            status = "CONFIRMED"
        elif comp in result.tweaks:
            status = "TWEAK"
        else:
            status = "RETHINK"

        # Format values based on magnitude and units
        pred_str = _fmt_value(comp.predicted, comp.units)
        meas_str = _fmt_value(comp.measured, comp.units)
        delta_str = _fmt_delta(comp.delta, comp.units)

        # Truncate parameter name to fit
        param = comp.parameter[:25].ljust(25)

        lines.append(
            f"  {comp.step:4d}  {param}  {pred_str:>9s}  {meas_str:>8s}  {delta_str:>6s}  {status}"
        )

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
        if measured.understeer_low_speed_deg != 0 and measured.understeer_high_speed_deg != 0:
            gradient = measured.understeer_high_speed_deg - measured.understeer_low_speed_deg
            lines.append(f"    Speed gradient:           {gradient:+.1f} deg  "
                         f"({'aero pushes front' if gradient > 0.5 else 'aero pushes rear' if gradient < -0.5 else 'balanced'})")

        lines.append(f"  Body slip angle (p95):      {measured.body_slip_p95_deg:.1f} deg")
        if measured.body_slip_at_peak_g_deg > 0:
            lines.append(f"  Body slip at peak g:        {measured.body_slip_at_peak_g_deg:.1f} deg")

        if measured.rear_slip_ratio_p95 > 0:
            lines.append(f"  Rear traction slip (p95):   {measured.rear_slip_ratio_p95:.3f}")
        if measured.front_slip_ratio_p95 > 0:
            lines.append(f"  Front braking slip (p95):   {measured.front_slip_ratio_p95:.3f}")

        if measured.yaw_rate_correlation > 0:
            # R^2 > 0.75 is normal for a racing car at the limit.
            # Tyre nonlinearity, trail-braking, and aero shift all
            # decorrelate yaw from neutral-steer expectation.
            qual = "excellent" if measured.yaw_rate_correlation > 0.90 else \
                   "good" if measured.yaw_rate_correlation > 0.75 else \
                   "marginal" if measured.yaw_rate_correlation > 0.60 else "poor"
            lines.append(f"  Yaw correlation (R^2):      {measured.yaw_rate_correlation:.3f}  ({qual})")

        if measured.roll_rate_p95_deg_per_s > 0:
            lines.append(f"  Roll rate (p95):            {measured.roll_rate_p95_deg_per_s:.1f} deg/s")
        if measured.pitch_rate_p95_deg_per_s > 0:
            lines.append(f"  Pitch rate (p95):           {measured.pitch_rate_p95_deg_per_s:.1f} deg/s")

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

        # Temp spread (camber validation)
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

    # --- Cascade Notes ---
    if result.cascade_chain:
        lines.append(section("CASCADE ANALYSIS"))
        lines.append("")
        for note in result.cascade_chain:
            # Wrap long notes
            wrapped = _wrap_text(note, width - 4)
            for line in wrapped:
                lines.append(f"  {line}")
        lines.append("")

    # --- Recommendations ---
    if feedback.parameter_adjustments or feedback.model_corrections:
        lines.append(section("RECOMMENDATIONS"))
        lines.append("")

        for adj in feedback.parameter_adjustments:
            label = "TWEAK" if abs(adj.current_value - adj.recommended_value) < 100 else "CHANGE"
            lines.append(f"  [{label}] Step {adj.step}: {adj.parameter}")
            wrapped = _wrap_text(adj.reasoning, width - 6)
            for line in wrapped:
                lines.append(f"    {line}")
            lines.append("")

        for mc in feedback.model_corrections:
            lines.append(f"  [MODEL] {mc.model_component} (confidence: {mc.confidence})")
            lines.append(f"    Current: {mc.current_value}  ->  Corrected: {mc.corrected_value} {mc.units}")
            wrapped = _wrap_text(mc.reasoning, width - 6)
            for line in wrapped:
                lines.append(f"    {line}")
            lines.append("")

    # --- Next Iteration ---
    lines.append(section("NEXT ITERATION"))
    lines.append("")

    if result.overall_verdict == "good_setup":
        lines.append("  Setup matches predictions. No changes needed.")
        lines.append("  Monitor marginal items near tolerance boundaries.")
    elif result.overall_verdict == "needs_tweaking":
        if feedback.rerun_solver:
            lines.append(f"  Re-run solver from Step {feedback.rerun_from_step} after applying corrections.")
            lines.append("  Apply model corrections to car_model/cars.py first.")
        else:
            lines.append("  Apply parameter adjustments directly in iRacing garage.")
        if feedback.updated_track_profile is not None:
            lines.append("  Updated track profile available (use --next-profile to save).")
    else:
        lines.append("  FUNDAMENTAL MODEL PROBLEMS DETECTED.")
        lines.append("  Do NOT tweak individual parameters.")
        lines.append("  Fix model calibration first, then re-run full solver.")
        if feedback.model_corrections:
            lines.append(f"  {len(feedback.model_corrections)} model corrections identified above.")

    lines.append("")
    lines.append("=" * width)

    return "\n".join(lines)


def save_validation_json(
    result: ValidationResult,
    feedback: FeedbackOutput,
    car_name: str,
    track_name: str,
    lap_number: int,
    lap_time_s: float,
    output_path: str | Path,
) -> None:
    """Save validation results as structured JSON."""
    # Strip the track profile from feedback (too large for summary JSON)
    feedback_dict = {
        "parameter_adjustments": [_safe_dict(a) for a in feedback.parameter_adjustments],
        "model_corrections": [_safe_dict(m) for m in feedback.model_corrections],
        "rerun_solver": feedback.rerun_solver,
        "rerun_from_step": feedback.rerun_from_step,
        "has_updated_track_profile": feedback.updated_track_profile is not None,
    }

    summary = {
        "meta": {
            "car": car_name,
            "track": track_name,
            "lap": lap_number,
            "lap_time_s": lap_time_s,
            "validated_at": datetime.now(timezone.utc).isoformat(),
        },
        "verdict": result.overall_verdict,
        "confidence_score": result.confidence_score,
        "confirmed_count": len(result.confirmed),
        "tweak_count": len(result.tweaks),
        "rethink_count": len(result.rethinks),
        "comparisons": [_safe_dict(c) for c in
                        result.confirmed + result.tweaks + result.rethinks],
        "cascade_chain": result.cascade_chain,
        "feedback": feedback_dict,
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(summary, indent=2))


def _fmt_value(value: float, units: str) -> str:
    """Format a value for the comparison table."""
    if units == "events":
        return f"{int(value)}"
    elif units == "m/s":
        return f"{value:.4f}"
    elif units in ("ratio", "R^2"):
        return f"{value:.3f}"
    elif units == "Hz":
        return f"{value:.2f}"
    elif units == "deg/g":
        return f"{value:.3f}"
    elif units in ("deg", "C"):
        return f"{value:.1f}"
    else:
        return f"{value:.1f}"


def _fmt_delta(delta: float, units: str) -> str:
    """Format a delta value for the comparison table."""
    if units == "events":
        return f"{int(delta):+d}"
    elif units == "m/s":
        return f"{delta:+.3f}"
    elif units in ("ratio", "R^2"):
        return f"{delta:+.3f}"
    elif units == "Hz":
        return f"{delta:+.2f}"
    elif units == "deg/g":
        return f"{delta:+.3f}"
    elif units in ("deg", "C"):
        return f"{delta:+.1f}"
    else:
        return f"{delta:+.1f}"


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
