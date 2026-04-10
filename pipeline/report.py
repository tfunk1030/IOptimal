"""Pipeline report — wraps the shared garage card format with IBT-specific context.

Adds driver profile, handling diagnosis, corner analysis, current vs recommended
comparison, and learning summary around the shared output/report.py garage card.

Usage:
    report_str = generate_report(car, track, measured, driver, diagnosis, ...)
    print(report_str)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from car_model.garage import GarageSetupState
from car_model.setup_registry import public_output_value
from analyzer.telemetry_truth import get_signal, summarize_signal_quality
from solver.predictor import predict_candidate_telemetry

if TYPE_CHECKING:
    from aero_model.gradient import AeroGradients
    from analyzer.diagnose import Diagnosis
    from analyzer.driver_style import DriverProfile
    from analyzer.extract import MeasuredState
    from analyzer.segment import CornerAnalysis
    from analyzer.setup_reader import CurrentSetup
    from car_model.cars import CarModel
    from solver.arb_solver import ARBSolution
    from solver.corner_spring_solver import CornerSpringSolution
    from solver.damper_solver import DamperSolution
    from solver.heave_solver import HeaveSolution
    from solver.laptime_sensitivity import LaptimeSensitivityReport
    from solver.modifiers import SolverModifiers
    from solver.rake_solver import RakeSolution
    from solver.sector_compromise import SectorCompromiseResult
    from solver.stint_model import StintStrategy
    from solver.supporting_solver import SupportingSolution
    from solver.wheel_geometry_solver import WheelGeometrySolution
    from track_model.profile import TrackProfile

from output.report import print_full_setup_report, _load_support_tier

W = 70


def _hdr(title: str) -> str:
    pad = (W - len(title) - 2) // 2
    return "─" * pad + f" {title} " + "─" * (W - pad - len(title) - 2)


def _row(label: str, value: str) -> str:
    pad = W - len(label) - len(value) - 4
    return f"  {label}{'.' * max(pad, 1)} {value}"


def _cmp(label: str, curr: float | None, prod: float, unit: str = "", fmt: str = ".1f") -> str:
    if curr is None or curr == 0:
        return f"  {label:22s}  {'—':>8}  {prod:>8{fmt}}  {'—':>8} {unit}"
    delta = prod - curr
    arrow = "↑" if delta > 0.05 else ("↓" if delta < -0.05 else "·")
    return f"  {label:22s}  {curr:>8{fmt}}  {prod:>8{fmt}}  {delta:>+8{fmt}} {unit} {arrow}"


def _as_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _prediction_unavailable_reason(
    measured: Any,
    *,
    attr_name: str,
    signal_name: str | None = None,
) -> str:
    if signal_name is not None:
        signal = get_signal(measured, signal_name)
        if signal.conflict_state != "clear":
            return "signal is conflicted"
        if signal.value is not None:
            return "predictor did not return an estimate"
        if signal.invalid_reason:
            return signal.invalid_reason
        return f"{signal.quality} signal"
    value = getattr(measured, attr_name, None)
    if value in (None, "", 0, 0.0):
        return "baseline metric unavailable"
    return "predictor did not return an estimate"


def _build_prediction_lines(
    *,
    current_setup: Any,
    measured: Any,
    step1: Any,
    step2: Any,
    step3: Any,
    step4: Any,
    step5: Any,
    step6: Any,
    supporting: Any,
    prediction_corrections: dict[str, float] | None = None,
) -> tuple[list[str], Any, Any]:
    predicted_telemetry, prediction_confidence = predict_candidate_telemetry(
        current_setup=current_setup,
        baseline_measured=measured,
        step1=step1,
        step2=step2,
        step3=step3,
        step4=step4,
        step5=step5,
        step6=step6,
        supporting=supporting,
        corrections=prediction_corrections,
    )

    lines: list[str] = []
    if prediction_confidence.overall < 0.45:
        lines.append(
            f"Prediction confidence is low ({prediction_confidence.overall:.2f}); "
            "treat the deltas below as advisory."
        )
    else:
        lines.append(f"Prediction confidence: {prediction_confidence.overall:.2f}")

    metric_specs = [
        ("Front travel used", "front_heave_travel_used_pct", "front_heave_travel_used_pct", "%", "lower", "front_heave_travel_used_pct"),
        ("Front excursion", "front_rh_excursion_measured_mm", "front_excursion_mm", "mm", "lower", None),
        ("Rear RH variance", "rear_rh_std_mm", "rear_rh_std_mm", "mm", "lower", "rear_rh_std_mm"),
        ("Braking pitch", "pitch_range_braking_deg", "braking_pitch_deg", "deg", "lower", "pitch_range_braking_deg"),
        ("Front lock p95", "front_braking_lock_ratio_p95", "front_lock_p95", "", "lower", "front_braking_lock_ratio_p95"),
        ("Rear power slip p95", "rear_power_slip_ratio_p95", "rear_power_slip_p95", "", "lower", "rear_power_slip_ratio_p95"),
        ("Body slip p95", "body_slip_p95_deg", "body_slip_p95_deg", "deg", "lower", "body_slip_p95_deg"),
        ("Understeer low", "understeer_low_speed_deg", "understeer_low_deg", "deg", "lower", "understeer_low_speed_deg"),
        ("Understeer high", "understeer_high_speed_deg", "understeer_high_deg", "deg", "lower", "understeer_high_speed_deg"),
        ("Front hot pressure", "front_pressure_mean_kpa", "front_pressure_hot_kpa", "kPa", "target", "front_pressure_mean_kpa"),
        ("Rear hot pressure", "rear_pressure_mean_kpa", "rear_pressure_hot_kpa", "kPa", "target", "rear_pressure_mean_kpa"),
    ]
    rendered = 0
    for label, before_attr, after_attr, unit, better, signal_name in metric_specs:
        before = _as_float(getattr(measured, before_attr, None))
        after = _as_float(getattr(predicted_telemetry, after_attr, None))
        if before is not None and after is not None:
            delta = after - before
            if better == "target":
                direction = "tracks target"
            else:
                direction = (
                    "improves"
                    if ((delta < 0 and better == "lower") or (delta > 0 and better == "higher"))
                    else "worsens"
                )
            unit_suffix = f" {unit}" if unit else ""
            lines.append(
                f"{label}: {before:.3f} -> {after:.3f}{unit_suffix} "
                f"({delta:+.3f}, {direction})"
            )
            rendered += 1
            continue
        reason = _prediction_unavailable_reason(
            measured,
            attr_name=before_attr,
            signal_name=signal_name,
        )
        lines.append(f"{label}: unavailable ({reason})")

    if rendered == 0:
        lines.append("No prediction deltas could be rendered from the available baseline telemetry.")

    return lines, predicted_telemetry, prediction_confidence


def generate_report(
    car: CarModel,
    track: TrackProfile,
    measured: MeasuredState,
    driver: DriverProfile,
    diagnosis: Diagnosis,
    corners: list[CornerAnalysis],
    aero_grad: AeroGradients,
    modifiers: SolverModifiers,
    step1: RakeSolution,
    step2: HeaveSolution,
    step3: CornerSpringSolution,
    step4: ARBSolution,
    step5: WheelGeometrySolution,
    step6: DamperSolution,
    supporting: SupportingSolution,
    current_setup: CurrentSetup,
    wing: float,
    target_balance: float,
    fuel_l: float | None = None,
    stint_result: StintStrategy | None = None,
    sector_result: SectorCompromiseResult | None = None,
    sensitivity_result: LaptimeSensitivityReport | None = None,
    space_result: object = None,
    stint_evolution: object = None,
    stint_compromise_info: list[str] | None = None,
    solve_context_lines: list[str] | None = None,
    prediction_corrections: dict[str, float] | None = None,
    selected_candidate_family: str | None = None,
    selected_candidate_score: float | None = None,
    compact: bool = False,
) -> str:
    """Generate the full pipeline report: telemetry context + garage card + comparison."""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    a = lines.append
    report_fuel_l = fuel_l if fuel_l is not None else getattr(current_setup, "fuel_l", 0.0)
    garage_outputs = None
    garage_model = getattr(car, "active_garage_output_model", lambda _track: None)(track.track_name)
    if garage_model is not None:
        garage_outputs = garage_model.predict(
            GarageSetupState.from_solver_steps(
                step1=step1,
                step2=step2,
                step3=step3,
                step5=step5,
                fuel_l=report_fuel_l,
            ),
            front_excursion_p99_mm=step2.front_excursion_at_rate_mm,
        )

    _ferrari_tb = (
        current_setup.torsion_bar_turns
        if current_setup is not None and getattr(car, "canonical_name", "") == "ferrari"
        else None
    )
    _ferrari_rear_tb = (
        current_setup.rear_torsion_bar_turns
        if current_setup is not None and getattr(car, "canonical_name", "") == "ferrari"
        else None
    )
    _is_ferrari_pipeline = current_setup is not None and getattr(car, "canonical_name", "") == "ferrari"
    _hybrid_enabled = getattr(current_setup, "hybrid_rear_drive_enabled", None) if _is_ferrari_pipeline else None
    _hybrid_corner_pct = getattr(current_setup, "hybrid_rear_drive_corner_pct", None) if _is_ferrari_pipeline else None
    _front_diff_preload_nm = getattr(current_setup, "front_diff_preload_nm", None) if _is_ferrari_pipeline else None
    _bias_migration_gain = getattr(current_setup, "brake_bias_migration_gain", None) if _is_ferrari_pipeline else None
    prediction_lines, predicted_telemetry, prediction_confidence = _build_prediction_lines(
        current_setup=current_setup,
        measured=measured,
        step1=step1,
        step2=step2,
        step3=step3,
        step4=step4,
        step5=step5,
        step6=step6,
        supporting=supporting,
        prediction_corrections=prediction_corrections,
    )

    if compact:
        report = print_full_setup_report(
            car_name=car.name,
            track_name=f"{track.track_name} — {track.track_config}",
            wing=wing,
            target_balance=target_balance,
            step1=step1,
            step2=step2,
            step3=step3,
            step4=step4,
            step5=step5,
            step6=step6,
            stint_result=stint_result,
            sector_result=sector_result,
            sensitivity_result=sensitivity_result,
            space_result=space_result,
            supporting=supporting,
            car=car,
            fuel_l=report_fuel_l,
            garage_outputs=garage_outputs,
            compact=True,
            front_tb_turns_override=_ferrari_tb,
            rear_tb_turns_override=_ferrari_rear_tb,
            hybrid_enabled=_hybrid_enabled,
            hybrid_corner_pct=_hybrid_corner_pct,
            front_diff_preload_nm=_front_diff_preload_nm,
            bias_migration_gain=_bias_migration_gain,
        )
        if selected_candidate_family is not None:
            report += "\n" + _hdr("CANDIDATE SELECTION") + "\n"
            report += f"  Selected family: {selected_candidate_family}\n"
            if selected_candidate_score is not None:
                report += f"  Candidate score: {selected_candidate_score:.3f}\n"
        report += "\n" + _hdr("PREDICTED IMPROVEMENTS") + "\n"
        report += "\n".join(f"  {line}" for line in prediction_lines)
        if solve_context_lines:
            report += "\n" + _hdr("SOLVE CONTEXT") + "\n"
            report += "\n".join(f"  {line}" for line in solve_context_lines)
        return report

    # ── PRE-CARD: Driver & Diagnosis ──────────────────────────────────
    a("═" * W)
    lap_str = f"  Lap #{measured.lap_number}  ({measured.lap_time_s:.3f}s)" if measured else ""
    a(f"  {car.name}  ·  {track.track_name} — {track.track_config}  ·  Wing {wing}°")
    a(f"  Telemetry-calibrated{lap_str}  ·  {now}")
    a("═" * W)
    a("")

    # ── CONFIDENCE & EVIDENCE ────────────────────────────────────────
    _car_slug = getattr(car, "canonical_name", "bmw")
    _tier_info = _load_support_tier(_car_slug, track.track_name)
    _sig_quality = summarize_signal_quality(measured)
    _direct_count = _sig_quality.get("direct", 0) if isinstance(_sig_quality, dict) else 0
    _total_count = sum(_sig_quality.values()) if isinstance(_sig_quality, dict) else 0
    if _tier_info is not None:
        _tier = _tier_info.get("confidence_tier", "unknown")
        _samples = _tier_info.get("samples", 0)
        a(f"  Support: {_tier}  ·  {_samples} observations  ·  Signals: {_direct_count}/{_total_count} direct")
    else:
        a(f"  Support: unknown  ·  Signals: {_direct_count}/{_total_count} direct")
    if prediction_confidence is not None:
        _conf = getattr(prediction_confidence, "overall", None)
        if _conf is not None:
            a(f"  Prediction confidence: {_conf:.2f}")

    # In-car adjustment warning
    _total_adjustments = (
        getattr(measured, "brake_bias_adjustments", 0) +
        getattr(measured, "tc_adjustments", 0)
    )
    if _total_adjustments > 5:
        a(f"  ⚠ Driver made {_total_adjustments} in-car adjustments — telemetry reflects "
          "mixed setup configurations (reduced authority)")

    a("")

    # Driver profile (one line each)
    a(_hdr("DRIVER PROFILE"))
    a(f"  Style: {driver.style}  ·  Trail brake: {driver.trail_brake_classification} "
      f"({driver.trail_brake_depth_mean:.0%})  ·  "
      f"Throttle: {driver.throttle_classification}  ·  "
      f"Consistency: {driver.consistency}")
    if getattr(driver, "setup_noise_index", 0.0) > 0 or getattr(driver, "noise_reasoning", ""):
        dni = getattr(driver, "driver_noise_index", 0.0)
        sni = getattr(driver, "setup_noise_index", 0.0)
        reason = getattr(driver, "noise_reasoning", "")
        a(f"  Noise: driver={dni:.2f}, setup={sni:.2f}"
          + (f"  ({reason})" if reason else ""))
    a("")

    # Handling diagnosis (top 3 problems)
    a(_hdr("HANDLING DIAGNOSIS"))
    severity_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
    if diagnosis.problems:
        for p in diagnosis.problems[:4]:
            icon = severity_icon.get(p.severity.lower(), "⚪")
            a(f"  {icon} {p.symptom}")
            cause = p.cause[:W - 8]
            a(f"    ↳ {cause}")
    else:
        a("  ✓ No significant handling problems detected.")
    if getattr(diagnosis, "causal_diagnosis", None):
        a(f"  Causal chain: {str(diagnosis.causal_diagnosis)[:W - 16]}")
    a("")

    signal_lines = summarize_signal_quality(measured)
    if signal_lines:
        a(_hdr("SIGNAL CONFIDENCE"))
        for line in signal_lines:
            a(f"  {line[:W - 2]}")
        if getattr(measured, "metric_fallbacks", None):
            for fallback in measured.metric_fallbacks[:5]:
                a(f"  Fallback: {fallback[:W - 14]}")
        a("")

    if (
        current_setup.brake_bias_target != 0.0
        or current_setup.brake_bias_migration != 0.0
        or current_setup.front_master_cyl_mm > 0.0
        or current_setup.rear_master_cyl_mm > 0.0
        or current_setup.pad_compound
    ):
        a(_hdr("BRAKE HARDWARE (FROM IBT)"))
        a(_row("Brake bias target", f"{current_setup.brake_bias_target:+.1f}"))
        a(_row("Brake migration", f"{current_setup.brake_bias_migration:+.1f}"))
        a(_row("Front master cyl", f"{current_setup.front_master_cyl_mm:.1f} mm"))
        a(_row("Rear master cyl", f"{current_setup.rear_master_cyl_mm:.1f} mm"))
        a(_row("Pad compound", current_setup.pad_compound or "unknown"))
        a("")

    if getattr(diagnosis, "state_issues", None) or getattr(diagnosis, "overhaul_assessment", None) is not None:
        a(_hdr("PRIMARY CAR STATES"))
        if getattr(diagnosis, "overhaul_assessment", None) is not None:
            oa = diagnosis.overhaul_assessment
            a(
                f"  Overhaul: {oa.classification}  "
                f"(conf {oa.confidence:.0%}, score {oa.score:.2f})"
            )
            for reason in oa.reasons[:3]:
                a(f"  {reason[:W - 2]}")
        for issue in diagnosis.state_issues[:4]:
            a(
                f"  - {issue.state_id}  sev={issue.severity:.2f}  "
                f"conf={issue.confidence:.2f}  loss~{issue.estimated_loss_ms:.0f}ms"
            )
            if issue.recommended_direction:
                a(f"    {issue.recommended_direction[:W - 6]}")
        a("")

    if selected_candidate_family is not None:
        a(_hdr("CANDIDATE SELECTION"))
        a(f"  Selected family: {selected_candidate_family}")
        if selected_candidate_score is not None:
            a(f"  Candidate score: {selected_candidate_score:.3f}")
        a("")

    a(_hdr("PREDICTED IMPROVEMENTS"))
    for line in prediction_lines:
        a(f"  {line}")
    a("")

    # ── CORE GARAGE CARD + ANALYSIS SECTIONS ─────────────────────────
    try:
        _setup_report = print_full_setup_report(
        car_name=car.name,
        track_name=f"{track.track_name} — {track.track_config}",
        wing=wing,
        target_balance=target_balance,
        step1=step1,
        step2=step2,
        step3=step3,
        step4=step4,
        step5=step5,
        step6=step6,
        stint_result=stint_result,
        sector_result=sector_result,
        sensitivity_result=sensitivity_result,
        space_result=space_result,
        supporting=supporting,
        car=car,
        fuel_l=report_fuel_l,
        garage_outputs=garage_outputs,
        front_tb_turns_override=_ferrari_tb,
        rear_tb_turns_override=_ferrari_rear_tb,
        hybrid_enabled=_hybrid_enabled,
        hybrid_corner_pct=_hybrid_corner_pct,
        front_diff_preload_nm=_front_diff_preload_nm,
        bias_migration_gain=_bias_migration_gain,
    )
        a(_setup_report)
    except (AttributeError, TypeError) as exc:
        # Some steps are None (blocked by calibration gate). Print partial report.
        a(f"  [Report truncated — blocked steps: {exc}]")
        a(f"  Steps 1-3 produced output; steps 4-6 blocked by calibration gate.")
        a(f"  .sto will use garage defaults for those steps.")

    # ── CURRENT vs RECOMMENDED ────────────────────────────────────────
    if current_setup is not None and step4 is not None and step5 is not None:
        a("")
        a(_hdr("CURRENT vs RECOMMENDED"))
        a(f"  {'Parameter':<22}  {'Current':>8}  {'Recomm':>8}  {'Change':>9}")
        a("  " + "─" * (W - 4))
        a(_cmp("Wing",               current_setup.wing_angle_deg,       wing,                           "°",   ".0f"))
        a(_cmp("Front RH (static)",  current_setup.static_front_rh_mm,   step1.static_front_rh_mm,       "mm"))
        a(_cmp("Rear RH (static)",   current_setup.static_rear_rh_mm,    step1.static_rear_rh_mm,        "mm"))
        _is_ferrari = getattr(car, "canonical_name", "") == "ferrari"
        _hu = "idx" if _is_ferrari else "N/mm"
        _tu = "idx" if _is_ferrari else "mm"
        # For Ferrari, convert raw N/mm values to garage indices via public_output_value so
        # both Current and Recomm columns are in the same display units (idx).
        _cur_fh  = float(public_output_value(car, "front_heave_nmm",     current_setup.front_heave_nmm))
        _rec_fh  = float(public_output_value(car, "front_heave_nmm",     step2.front_heave_nmm))
        _cur_rh  = float(public_output_value(car, "rear_third_nmm",      current_setup.rear_third_nmm))
        _rec_rh  = float(public_output_value(car, "rear_third_nmm",      step2.rear_third_nmm))
        _cur_rs  = float(public_output_value(car, "rear_spring_rate_nmm", current_setup.rear_spring_nmm))
        _rec_rs  = float(public_output_value(car, "rear_spring_rate_nmm", step3.rear_spring_rate_nmm))
        _cur_tb  = float(public_output_value(car, "front_torsion_od_mm", current_setup.front_torsion_od_mm))
        _rec_tb  = float(public_output_value(car, "front_torsion_od_mm", step3.front_torsion_od_mm))
        a(_cmp("Front heave",        _cur_fh,   _rec_fh,  _hu, ".0f"))
        _rear_heave_lbl = "Rear heave" if _is_ferrari else "Rear third"
        a(_cmp(_rear_heave_lbl,      _cur_rh,   _rec_rh,  _hu, ".0f"))
        _rear_spr_lbl = "Rear TB OD" if _is_ferrari else "Rear spring"
        a(_cmp(_rear_spr_lbl,        _cur_rs,   _rec_rs,  _hu, ".0f"))
        _tb_lbl = "F torsion bar OD" if _is_ferrari else "Torsion bar OD"
        a(_cmp(_tb_lbl,              _cur_tb,   _rec_tb,  _tu))
        a(_cmp("Front camber",       current_setup.front_camber_deg,     step5.front_camber_deg,         "°"))
        a(_cmp("Rear camber",        current_setup.rear_camber_deg,      step5.rear_camber_deg,          "°"))
        a(_cmp("Brake bias",         current_setup.brake_bias_pct,       supporting.brake_bias_pct,      "%"))
        if current_setup.brake_bias_target != 0.0 or supporting.brake_bias_target != 0.0:
            a(_cmp("Brake bias target", current_setup.brake_bias_target, supporting.brake_bias_target, ""))
        if current_setup.brake_bias_migration != 0.0 or supporting.brake_bias_migration != 0.0:
            a(_cmp("Brake migration", current_setup.brake_bias_migration, supporting.brake_bias_migration, ""))
        _brake_sol = getattr(supporting, "_brake_solution", None)
        if _brake_sol is not None:
            if _brake_sol.mc_ratio_note:
                a(f"  MC: {_brake_sol.mc_ratio_note}")
            if _brake_sol.pad_compound_note:
                a(f"  Pad: {_brake_sol.pad_compound_note}")
        a(_cmp("Diff preload",       current_setup.diff_preload_nm,      supporting.diff_preload_nm,     "Nm",  ".0f"))
        a(_cmp("TC gain",            current_setup.tc_gain,              supporting.tc_gain,             "",    ".0f"))
        a(_cmp("F LS Comp",          current_setup.front_ls_comp,        step6.lf.ls_comp,               "cl",  ".0f"))
        a(_cmp("F HS Comp",          current_setup.front_hs_comp,        step6.lf.hs_comp,               "cl",  ".0f"))
        a(_cmp("R LS Comp",          current_setup.rear_ls_comp,         step6.lr.ls_comp,               "cl",  ".0f"))
        a(_cmp("R HS Comp",          current_setup.rear_hs_comp,         step6.lr.hs_comp,               "cl",  ".0f"))
        a("")

    # ── HEAVE TRAVEL BUDGET ────────────────────────────────────────────
    if step2.defl_max_front_mm > 0:
        budget_slider = (
            garage_outputs.heave_slider_defl_static_mm
            if garage_outputs is not None else
            step2.slider_static_front_mm
        )
        budget_defl_max = (
            garage_outputs.heave_spring_defl_max_mm
            if garage_outputs is not None else
            step2.defl_max_front_mm
        )
        budget_static_defl = (
            garage_outputs.heave_spring_defl_static_mm
            if garage_outputs is not None else
            step2.static_defl_front_mm
        )
        budget_available = (
            garage_outputs.available_travel_front_mm
            if garage_outputs is not None else
            step2.available_travel_front_mm
        )
        budget_margin = (
            garage_outputs.travel_margin_front_mm
            if garage_outputs is not None else
            step2.travel_margin_front_mm
        )
        a(_hdr("FRONT HEAVE TRAVEL BUDGET"))
        _hu2 = "idx" if getattr(car, "canonical_name", "") == "ferrari" else "N/mm"
        a(f"  Heave spring:       {step2.front_heave_nmm:.0f} {_hu2}")
        a(f"  Perch offset:       {step2.perch_offset_front_mm:.1f} mm")
        a(f"  Slider position:    {budget_slider:.1f} mm")
        a(f"  DeflMax:            {budget_defl_max:.1f} mm")
        a(f"  Static deflection:  {budget_static_defl:.1f} mm")
        a(f"  Available travel:   {budget_available:.1f} mm")
        a(f"  Excursion p99:      {step2.front_excursion_at_rate_mm:.1f} mm")
        margin_status = "OK" if budget_margin >= 5 else "LOW"
        a(f"  Travel margin:      {budget_margin:.1f} mm  [{margin_status}]")
        if step2.total_force_at_limit_n > 0:
            a(f"  Force at limit:")
            a(f"    Spring:  {step2.spring_force_at_limit_n:.0f} N  (k × travel)")
            a(f"    Damper:  {step2.damper_force_braking_n:.0f} N  (c_ls × v_braking)")
            a(f"    Total:   {step2.total_force_at_limit_n:.0f} N")
        # Travel usage from telemetry (if measured)
        if (measured.front_heave_travel_used_pct or 0) > 0:
            a(f"  Measured travel use: {measured.front_heave_travel_used_pct:.0f}%")
        if (measured.front_heave_travel_used_braking_pct or 0) > 0:
            pct = measured.front_heave_travel_used_braking_pct
            flag = " *** WARNING ***" if pct > 85 else ""
            a(f"  Under braking:      {pct:.0f}%{flag}")
        a("")

    # ── STINT EVOLUTION (telemetry-based) ─────────────────────────────
    if stint_evolution is not None and getattr(stint_evolution, "qualifying_lap_count", 0) >= 3:
        a(_hdr("STINT EVOLUTION (TELEMETRY)"))
        _se = stint_evolution
        _ss = _se.start_snapshot
        _ms = _se.mid_snapshot
        _es = _se.end_snapshot
        a(f"  Qualifying laps: {_se.qualifying_lap_count}/{_se.total_lap_count}  "
          f"(within {_se.threshold_pct}% of {_se.fastest_lap_time_s:.3f}s)")
        a(f"  Stint range: lap {_ss.lap_number} -> lap {_es.lap_number}  "
          f"({_se.qualifying_lap_count} laps analyzed)")
        a("")
        a(f"  {'CONDITION EVOLUTION':^{W - 4}}")
        a(f"  {'':22s}  {'Start':>8}  {'Mid':>8}  {'End':>8}")
        a("  " + "-" * (W - 4))
        a(f"  {'Fuel level':<22}  {_ss.fuel_level_l:>7.1f}L  {_ms.fuel_level_l:>7.1f}L  {_es.fuel_level_l:>7.1f}L")
        a(f"  {'Lap time':<22}  {_ss.lap_time_s:>7.3f}s  {_ms.lap_time_s:>7.3f}s  {_es.lap_time_s:>7.3f}s")
        a(f"  {'Front pressure':<22}  {_ss.front_pressure_mean_kpa:>6.1f}kPa  {_ms.front_pressure_mean_kpa:>6.1f}kPa  {_es.front_pressure_mean_kpa:>6.1f}kPa")
        a(f"  {'Rear pressure':<22}  {_ss.rear_pressure_mean_kpa:>6.1f}kPa  {_ms.rear_pressure_mean_kpa:>6.1f}kPa  {_es.rear_pressure_mean_kpa:>6.1f}kPa")
        f_wear_s = (_ss.lf_wear_pct + _ss.rf_wear_pct) / 2
        f_wear_m = (_ms.lf_wear_pct + _ms.rf_wear_pct) / 2
        f_wear_e = (_es.lf_wear_pct + _es.rf_wear_pct) / 2
        r_wear_s = (_ss.lr_wear_pct + _ss.rr_wear_pct) / 2
        r_wear_m = (_ms.lr_wear_pct + _ms.rr_wear_pct) / 2
        r_wear_e = (_es.lr_wear_pct + _es.rr_wear_pct) / 2
        a(f"  {'Front wear':<22}  {f_wear_s:>7.1f}%  {f_wear_m:>7.1f}%  {f_wear_e:>7.1f}%")
        a(f"  {'Rear wear':<22}  {r_wear_s:>7.1f}%  {r_wear_m:>7.1f}%  {r_wear_e:>7.1f}%")
        a(f"  {'Front carcass temp':<22}  {_ss.front_carcass_mean_c:>6.1f} C  {_ms.front_carcass_mean_c:>6.1f} C  {_es.front_carcass_mean_c:>6.1f} C")
        a(f"  {'Rear carcass temp':<22}  {_ss.rear_carcass_mean_c:>6.1f} C  {_ms.rear_carcass_mean_c:>6.1f} C  {_es.rear_carcass_mean_c:>6.1f} C")
        a(f"  {'Track temp':<22}  {_ss.track_temp_c:>6.1f} C  {_ms.track_temp_c:>6.1f} C  {_es.track_temp_c:>6.1f} C")
        a(f"  {'Understeer':<22}  {_ss.understeer_mean_deg:>+7.2f}d  {_ms.understeer_mean_deg:>+7.2f}d  {_es.understeer_mean_deg:>+7.2f}d")
        a(f"  {'Peak lateral g':<22}  {_ss.peak_lat_g_measured:>7.2f}g  {_ms.peak_lat_g_measured:>7.2f}g  {_es.peak_lat_g_measured:>7.2f}g")
        a(f"  {'Front RH (dynamic)':<22}  {_ss.mean_front_rh_at_speed_mm:>6.1f}mm  {_ms.mean_front_rh_at_speed_mm:>6.1f}mm  {_es.mean_front_rh_at_speed_mm:>6.1f}mm")
        a(f"  {'Bottoming events F':<22}  {_ss.bottoming_event_count_front:>8d}  {_ms.bottoming_event_count_front:>8d}  {_es.bottoming_event_count_front:>8d}")
        a("")

        # Degradation rates
        _rates = _se.rates
        if _rates is not None:
            _rsq = _rates.r_squared or {}
            a(f"  {'DEGRADATION RATES (per lap, linear fit)':^{W - 4}}")
            a("  " + "-" * (W - 4))

            def _rate_row(label: str, val: float, unit: str, key: str) -> str:
                r2 = _rsq.get(key, 0.0)
                conf = "low" if r2 < 0.3 else ""
                return f"  {label:<26} {val:>+8.4f} {unit:<8} (R2={r2:.2f}){' ' + conf if conf else ''}"

            a(_rate_row("Fuel burn",        -_rates.fuel_burn_l_per_lap,       "L/lap",    "fuel_burn_l_per_lap"))
            a(_rate_row("Lap time",         _rates.lap_time_s_per_lap,         "s/lap",    "lap_time_s_per_lap"))
            a(_rate_row("Front pressure",   _rates.front_pressure_kpa_per_lap, "kPa/lap",  "front_pressure_kpa_per_lap"))
            a(_rate_row("Rear pressure",    _rates.rear_pressure_kpa_per_lap,  "kPa/lap",  "rear_pressure_kpa_per_lap"))
            a(_rate_row("Front wear",       _rates.front_wear_pct_per_lap,     "%/lap",    "front_wear_pct_per_lap"))
            a(_rate_row("Rear wear",        _rates.rear_wear_pct_per_lap,      "%/lap",    "rear_wear_pct_per_lap"))
            a(_rate_row("Understeer drift", _rates.understeer_deg_per_lap,     "deg/lap",  "understeer_deg_per_lap"))
            a(_rate_row("Grip trend",       _rates.peak_lat_g_per_lap,         "g/lap",    "peak_lat_g_per_lap"))
            a(_rate_row("Track temp",       _rates.track_temp_c_per_lap,       "C/lap",    "track_temp_c_per_lap"))
            a(_rate_row("Front RH drift",   _rates.front_rh_mm_per_lap,        "mm/lap",   "front_rh_mm_per_lap"))
            a("")

        # Multi-solve compromise info
        if stint_compromise_info:
            a(f"  {'MULTI-SOLVE COMPROMISE':^{W - 4}}")
            a("  " + "-" * (W - 4))
            for info in stint_compromise_info:
                a(f"  {info}")
            a("")

    # ── PARAMETER JUSTIFICATION (comprehensive engineering brief) ──────────
    if sensitivity_result is not None and hasattr(sensitivity_result, "justification_report"):
        a("")
        a(sensitivity_result.justification_report(width=W))
        a("")

    # ── LEARNING SUMMARY ──────────────────────────────────────────────
    try:
        from learner.report_section import generate_learning_section
        ls = generate_learning_section(
            car=car.canonical_name,
            track=track.track_name,
            width=W,
        )
        if ls:
            a(ls)
            a("")
    except Exception:
        pass

    # ── ESTIMATE WARNINGS ─────────────────────────────────────────────
    estimate_warnings = []

    # Check deflection model calibration
    if hasattr(car, 'deflection') and not getattr(car.deflection, 'is_calibrated', True):
        estimate_warnings.append(
            "ESTIMATE: Deflection predictions use uncalibrated model — "
            "verify garage display values manually"
        )

    # Check ride height model calibration
    if hasattr(car, 'ride_height_model') and not getattr(car.ride_height_model, 'is_calibrated', True):
        estimate_warnings.append(
            "ESTIMATE: Ride height predictions use uncalibrated model"
        )

    # Check damper zeta calibration
    if hasattr(car, 'damper') and not getattr(car.damper, 'zeta_is_calibrated', True):
        estimate_warnings.append(
            "ESTIMATE: Damper zeta targets are conservative defaults — "
            "verify damper feel on track"
        )

    # Check garage output model
    garage_model = getattr(car, "active_garage_output_model", lambda _track: None)(track.track_name)
    if garage_model is None:
        estimate_warnings.append(
            "ESTIMATE: No garage output model — .sto display values are physics estimates only"
        )

    if estimate_warnings:
        a(_hdr("ESTIMATE WARNINGS"))
        for warning in estimate_warnings:
            # Wrap long warnings at word boundaries to fit width
            words = warning.split()
            current_line = "  • "
            for word in words:
                if len(current_line) + len(word) + 1 <= W:
                    current_line += word + " "
                else:
                    a(current_line.rstrip())
                    current_line = "    " + word + " "
            if current_line.strip():
                a(current_line.rstrip())
        a("")

    a("═" * W)
    return "\n".join(lines)
