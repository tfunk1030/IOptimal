"""Pipeline report — wraps the shared garage card format with IBT-specific context.

Adds driver profile, handling diagnosis, corner analysis, current vs recommended
comparison, and learning summary around the shared output/report.py garage card.

Usage:
    report_str = generate_report(car, track, measured, driver, diagnosis, ...)
    print(report_str)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from car_model.garage import GarageSetupState

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

from output.report import print_full_setup_report

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
    stint_result: StintStrategy | None = None,
    sector_result: SectorCompromiseResult | None = None,
    sensitivity_result: LaptimeSensitivityReport | None = None,
    space_result: object = None,
    stint_evolution: object = None,
    stint_compromise_info: list[str] | None = None,
    compact: bool = False,
) -> str:
    """Generate the full pipeline report: telemetry context + garage card + comparison."""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    a = lines.append
    garage_outputs = None
    garage_model = getattr(car, "active_garage_output_model", lambda _track: None)(track.track_name)
    if garage_model is not None:
        garage_outputs = garage_model.predict(
            GarageSetupState.from_solver_steps(
                step1=step1,
                step2=step2,
                step3=step3,
                step5=step5,
                fuel_l=getattr(current_setup, "fuel_l", 0.0),
            ),
            front_excursion_p99_mm=step2.front_excursion_at_rate_mm,
        )

    if compact:
        return print_full_setup_report(
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
            fuel_l=getattr(current_setup, "fuel_l", 0.0),
            garage_outputs=garage_outputs,
            compact=True,
        )

    # ── PRE-CARD: Driver & Diagnosis ──────────────────────────────────
    a("═" * W)
    lap_str = f"  Lap #{measured.lap_number}  ({measured.lap_time_s:.3f}s)" if measured else ""
    a(f"  {car.name}  ·  {track.track_name} — {track.track_config}  ·  Wing {wing}°")
    a(f"  Telemetry-calibrated{lap_str}  ·  {now}")
    a("═" * W)
    a("")

    # Driver profile (one line each)
    a(_hdr("DRIVER PROFILE"))
    a(f"  Style: {driver.style}  ·  Trail brake: {driver.trail_brake_classification} "
      f"({driver.trail_brake_depth_mean:.0%})  ·  "
      f"Throttle: {driver.throttle_classification}  ·  "
      f"Consistency: {driver.consistency}")
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

    # ── CORE GARAGE CARD + ANALYSIS SECTIONS ─────────────────────────
    a(print_full_setup_report(
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
        fuel_l=getattr(current_setup, "fuel_l", 0.0),
        garage_outputs=garage_outputs,
    ))

    # ── CURRENT vs RECOMMENDED ────────────────────────────────────────
    if current_setup is not None:
        a("")
        a(_hdr("CURRENT vs RECOMMENDED"))
        a(f"  {'Parameter':<22}  {'Current':>8}  {'Recomm':>8}  {'Change':>9}")
        a("  " + "─" * (W - 4))
        a(_cmp("Wing",               current_setup.wing_angle_deg,       wing,                           "°",   ".0f"))
        a(_cmp("Front RH (static)",  current_setup.static_front_rh_mm,   step1.static_front_rh_mm,       "mm"))
        a(_cmp("Rear RH (static)",   current_setup.static_rear_rh_mm,    step1.static_rear_rh_mm,        "mm"))
        a(_cmp("Front heave",        current_setup.front_heave_nmm,      step2.front_heave_nmm,          "N/mm", ".0f"))
        a(_cmp("Rear third",         current_setup.rear_third_nmm,       step2.rear_third_nmm,           "N/mm", ".0f"))
        a(_cmp("Rear spring",        current_setup.rear_spring_nmm,      step3.rear_spring_rate_nmm,     "N/mm", ".0f"))
        a(_cmp("Torsion bar OD",     current_setup.front_torsion_od_mm,  step3.front_torsion_od_mm,      "mm"))
        a(_cmp("Front camber",       current_setup.front_camber_deg,     step5.front_camber_deg,         "°"))
        a(_cmp("Rear camber",        current_setup.rear_camber_deg,      step5.rear_camber_deg,          "°"))
        a(_cmp("Brake bias",         current_setup.brake_bias_pct,       supporting.brake_bias_pct,      "%"))
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
        a(f"  Heave spring:       {step2.front_heave_nmm:.0f} N/mm")
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
        if measured.front_heave_travel_used_pct > 0:
            a(f"  Measured travel use: {measured.front_heave_travel_used_pct:.0f}%")
        if measured.front_heave_travel_used_braking_pct > 0:
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

    a("═" * W)
    return "\n".join(lines)
