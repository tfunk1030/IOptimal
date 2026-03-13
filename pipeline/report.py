"""Engineering report for the setup producer pipeline.

ASCII terminal output (63-char width, cp1252-safe) with optional JSON export.
Reports every decision with physics justification.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

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
    from solver.modifiers import SolverModifiers
    from solver.rake_solver import RakeSolution
    from solver.supporting_solver import SupportingSolution
    from solver.wheel_geometry_solver import WheelGeometrySolution
    from track_model.profile import TrackProfile


WIDTH = 63


def _section(title: str) -> str:
    pad = (WIDTH - len(title) - 2) // 2
    return "=" * pad + f" {title} " + "=" * (WIDTH - pad - len(title) - 2)


def _subsection(title: str) -> str:
    pad = (WIDTH - len(title) - 2) // 2
    return "-" * pad + f" {title} " + "-" * (WIDTH - pad - len(title) - 2)


def _row(label: str, value: str, width: int = WIDTH) -> str:
    """Left-align label, right-align value."""
    padding = width - len(label) - len(value) - 4
    return f"  {label}{'.' * max(padding, 1)} {value}"


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
) -> str:
    """Generate the full engineering report.

    Returns a multi-line string ready for print().
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    def add(s: str = "") -> None:
        lines.append(s)

    # ── 1. Header ──
    add(_section("GTP SETUP PRODUCER"))
    add(f"  Generated: {now}")
    add(f"  Car: {car.name}")
    add(f"  Track: {track.track_name} -- {track.track_config}")
    fuel_str = f"{current_setup.fuel_l:.0f} L" if current_setup and current_setup.fuel_l else "N/A"
    add(f"  Wing: {wing} deg   Fuel: {fuel_str}")
    add(f"  Lap: #{measured.lap_number} ({measured.lap_time_s:.3f}s)")
    add(f"  Assessment: {diagnosis.assessment.upper()}")
    add()

    # ── 2. Driver Profile ──
    add(_section("DRIVER PROFILE"))
    add(_row("Overall style", driver.style))
    add(_row("Trail braking", f"{driver.trail_brake_classification} ({driver.trail_brake_depth_mean:.0%})"))
    add(_row("Throttle", f"{driver.throttle_classification} (R2={driver.throttle_progressiveness:.2f})"))
    add(_row("Steering", f"{driver.steering_smoothness} (jerk p95={driver.steering_jerk_p95_rad_per_s2:.0f})"))
    add(_row("Consistency", f"{driver.consistency} (CV={driver.apex_speed_cv:.3f})"))
    add(_row("Cornering aggression", f"{driver.cornering_aggression} ({driver.avg_peak_lat_g_utilization:.0%})"))
    add()

    # ── 3. Handling Diagnosis ──
    add(_section("HANDLING DIAGNOSIS"))
    if diagnosis.problems:
        for p in diagnosis.problems[:8]:  # top 8 problems
            sev_tag = f"[{p.severity.upper():11s}]"
            add(f"  {sev_tag} {p.symptom}")
            add(f"    {p.cause[:WIDTH - 6]}")
    else:
        add("  No significant handling problems detected.")
    add()

    # ── 4. Corner Analysis ──
    if corners:
        add(_section("CORNER ANALYSIS"))
        add(f"  {len(corners)} corners detected")
        n_low = sum(1 for c in corners if c.speed_class == "low")
        n_mid = sum(1 for c in corners if c.speed_class == "mid")
        n_high = sum(1 for c in corners if c.speed_class == "high")
        add(f"  Speed classes: low={n_low} mid={n_mid} high={n_high}")
        add()

        # Top 5 worst corners by time loss
        worst = sorted(corners, key=lambda c: c.delta_to_min_time_s, reverse=True)[:5]
        if worst and worst[0].delta_to_min_time_s > 0:
            add(_subsection("Worst Corners (time loss)"))
            for c in worst:
                add(f"  T{c.corner_id + 1:02d} @{c.lap_dist_start_m:.0f}m "
                    f"{c.direction:5s} {c.apex_speed_kph:5.0f}kph "
                    f"[{c.speed_class:4s}] "
                    f"dt={c.delta_to_min_time_s:+.3f}s "
                    f"US={c.understeer_mean_deg:+.1f}deg")
            add()

    # ── 5. Aero Analysis ──
    add(_section("AERO ANALYSIS"))
    add(_row("DF balance", f"{aero_grad.df_balance_pct:.2f}%"))
    add(_row("L/D ratio", f"{aero_grad.ld_ratio:.3f}"))
    add(_row("dBal/dFrontRH", f"{aero_grad.dBalance_dFrontRH:+.4f} %/mm"))
    add(_row("dBal/dRearRH", f"{aero_grad.dBalance_dRearRH:+.4f} %/mm"))
    add(_row("Aero window (front)", f"+/-{aero_grad.front_rh_window_mm:.1f} mm"))
    add(_row("Aero window (rear)", f"+/-{aero_grad.rear_rh_window_mm:.1f} mm"))
    if aero_grad.balance_variance_from_rh_pct > 0:
        add(_row("DF balance sigma (from RH)", f"{aero_grad.balance_variance_from_rh_pct:.3f}%"))
        add(_row("L/D cost of variance", f"{aero_grad.ld_cost_of_variance:.4f}"))
    add()

    # ── 6. Solver Modifiers ──
    if modifiers.reasons:
        add(_section("SOLVER MODIFIERS"))
        for r in modifiers.reasons:
            add(f"  - {r}")
        add()

    # ── 7. 6-Step Solver Summary ──
    add(_section("SETUP SOLUTION"))
    add(_subsection("Step 1: Rake / Ride Heights"))
    add(_row("Dynamic front RH", f"{step1.dynamic_front_rh_mm:.1f} mm"))
    add(_row("Dynamic rear RH", f"{step1.dynamic_rear_rh_mm:.1f} mm"))
    add(_row("Rake", f"{step1.rake_dynamic_mm:.1f} mm"))
    add(_row("DF balance", f"{step1.df_balance_pct:.2f}%"))
    add(_row("L/D", f"{step1.ld_ratio:.3f}"))
    add(_row("Static front RH", f"{step1.static_front_rh_mm:.0f} mm"))
    add(_row("Static rear RH", f"{step1.static_rear_rh_mm:.0f} mm"))
    add()

    add(_subsection("Step 2: Heave / Third Springs"))
    add(_row("Front heave", f"{step2.front_heave_nmm:.0f} N/mm"))
    add(_row("Rear third", f"{step2.rear_third_nmm:.0f} N/mm"))
    add(_row("Front bottoming margin", f"{step2.front_bottoming_margin_mm:.1f} mm"))
    add(_row("Rear bottoming margin", f"{step2.rear_bottoming_margin_mm:.1f} mm"))
    add()

    add(_subsection("Step 3: Corner Springs"))
    add(_row("Front torsion bar OD", f"{step3.front_torsion_od_mm:.1f} mm"))
    add(_row("Front wheel rate", f"{step3.front_wheel_rate_nmm:.1f} N/mm"))
    add(_row("Rear spring rate", f"{step3.rear_spring_rate_nmm:.1f} N/mm"))
    add(_row("Front natural freq", f"{step3.front_natural_freq_hz:.2f} Hz"))
    add(_row("Rear natural freq", f"{step3.rear_natural_freq_hz:.2f} Hz"))
    add()

    add(_subsection("Step 4: Anti-Roll Bars"))
    add(_row("Front ARB", f"{step4.front_arb_size} blade {step4.front_arb_blade_start}"))
    add(_row("Rear ARB", f"{step4.rear_arb_size} blade {step4.rear_arb_blade_start}"))
    add(_row("LLTD target", f"{step4.lltd_target:.1%}"))
    add(_row("LLTD achieved", f"{step4.lltd_achieved:.1%}"))
    add(_row("RARB range", f"blade {step4.rarb_blade_slow_corner}-{step4.rarb_blade_fast_corner}"))
    add()

    add(_subsection("Step 5: Wheel Geometry"))
    add(_row("Front camber", f"{step5.front_camber_deg:.1f} deg"))
    add(_row("Rear camber", f"{step5.rear_camber_deg:.1f} deg"))
    add(_row("Front toe", f"{step5.front_toe_mm:.2f} mm"))
    add(_row("Rear toe", f"{step5.rear_toe_mm:.2f} mm"))
    add()

    add(_subsection("Step 6: Dampers"))
    add("              LF    RF    LR    RR")
    add(f"  LS Comp:  {step6.lf.ls_comp:4d}  {step6.rf.ls_comp:4d}  {step6.lr.ls_comp:4d}  {step6.rr.ls_comp:4d}")
    add(f"  LS Rbd:   {step6.lf.ls_rbd:4d}  {step6.rf.ls_rbd:4d}  {step6.lr.ls_rbd:4d}  {step6.rr.ls_rbd:4d}")
    add(f"  HS Comp:  {step6.lf.hs_comp:4d}  {step6.rf.hs_comp:4d}  {step6.lr.hs_comp:4d}  {step6.rr.hs_comp:4d}")
    add(f"  HS Rbd:   {step6.lf.hs_rbd:4d}  {step6.rf.hs_rbd:4d}  {step6.lr.hs_rbd:4d}  {step6.rr.hs_rbd:4d}")
    add(f"  HS Slope: {step6.lf.hs_slope:4d}  {step6.rf.hs_slope:4d}  {step6.lr.hs_slope:4d}  {step6.rr.hs_slope:4d}")
    add()

    # ── 8. Supporting Parameters ──
    add(_section("SUPPORTING PARAMETERS"))
    add(_row("Brake bias", f"{supporting.brake_bias_pct:.1f}%"))
    add(f"    {supporting.brake_bias_reasoning}")
    add(_row("Diff preload", f"{supporting.diff_preload_nm:.0f} Nm"))
    add(_row("Diff ramps", f"coast {supporting.diff_ramp_coast} / drive {supporting.diff_ramp_drive}"))
    add(_row("Diff plates", f"{supporting.diff_clutch_plates}"))
    add(f"    {supporting.diff_reasoning[:WIDTH - 6]}")
    add(_row("TC gain", f"{supporting.tc_gain}"))
    add(_row("TC slip", f"{supporting.tc_slip}"))
    add(f"    {supporting.tc_reasoning}")
    add(_row("Tyre pressure FL", f"{supporting.tyre_cold_fl_kpa:.0f} kPa"))
    add(_row("Tyre pressure FR", f"{supporting.tyre_cold_fr_kpa:.0f} kPa"))
    add(_row("Tyre pressure RL", f"{supporting.tyre_cold_rl_kpa:.0f} kPa"))
    add(_row("Tyre pressure RR", f"{supporting.tyre_cold_rr_kpa:.0f} kPa"))
    add()

    # ── 9. Setup Comparison ──
    add(_section("SETUP COMPARISON"))
    add("  Parameter              Current   Produced   Delta")
    add("  " + "-" * (WIDTH - 4))

    def _cmp(label: str, curr: float, prod: float, unit: str = "", fmt: str = ".1f") -> None:
        delta = prod - curr
        delta_str = f"{delta:+{fmt}}"
        add(f"  {label:22s} {curr:>8{fmt}}  {prod:>8{fmt}}  {delta_str:>8} {unit}")

    _cmp("Wing", current_setup.wing_angle_deg, wing, "deg", ".0f")
    _cmp("Front RH (static)", current_setup.static_front_rh_mm, step1.static_front_rh_mm, "mm")
    _cmp("Rear RH (static)", current_setup.static_rear_rh_mm, step1.static_rear_rh_mm, "mm")
    _cmp("Front heave", current_setup.front_heave_nmm, step2.front_heave_nmm, "N/mm", ".0f")
    _cmp("Rear third", current_setup.rear_third_nmm, step2.rear_third_nmm, "N/mm", ".0f")
    _cmp("Rear spring", current_setup.rear_spring_nmm, step3.rear_spring_rate_nmm, "N/mm", ".0f")
    _cmp("Front camber", current_setup.front_camber_deg, step5.front_camber_deg, "deg")
    _cmp("Rear camber", current_setup.rear_camber_deg, step5.rear_camber_deg, "deg")
    _cmp("Front toe", current_setup.front_toe_mm, step5.front_toe_mm, "mm", ".2f")
    _cmp("Rear toe", current_setup.rear_toe_mm, step5.rear_toe_mm, "mm", ".2f")
    _cmp("Brake bias", current_setup.brake_bias_pct, supporting.brake_bias_pct, "%")
    _cmp("Diff preload", current_setup.diff_preload_nm, supporting.diff_preload_nm, "Nm", ".0f")
    _cmp("TC gain", current_setup.tc_gain, supporting.tc_gain, "", ".0f")
    _cmp("TC slip", current_setup.tc_slip, supporting.tc_slip, "", ".0f")
    # Dampers (front LF as representative)
    _cmp("F LS Comp", current_setup.front_ls_comp, step6.lf.ls_comp, "clicks", ".0f")
    _cmp("F LS Rbd", current_setup.front_ls_rbd, step6.lf.ls_rbd, "clicks", ".0f")
    _cmp("F HS Comp", current_setup.front_hs_comp, step6.lf.hs_comp, "clicks", ".0f")
    _cmp("F HS Rbd", current_setup.front_hs_rbd, step6.lf.hs_rbd, "clicks", ".0f")
    _cmp("R LS Comp", current_setup.rear_ls_comp, step6.lr.ls_comp, "clicks", ".0f")
    _cmp("R LS Rbd", current_setup.rear_ls_rbd, step6.lr.ls_rbd, "clicks", ".0f")
    _cmp("R HS Comp", current_setup.rear_hs_comp, step6.lr.hs_comp, "clicks", ".0f")
    _cmp("R HS Rbd", current_setup.rear_hs_rbd, step6.lr.hs_rbd, "clicks", ".0f")
    add()

    # ── 10. Confidence Assessment ──
    add(_section("CONFIDENCE ASSESSMENT"))
    add("  HIGH confidence (well-calibrated):")
    add("    - Ride heights, heave/third springs (energy model)")
    add("    - DF balance (aero map interpolation)")
    add("    - Corner springs (frequency model)")
    add("    - ARBs (roll stiffness model)")
    add("  MEDIUM confidence:")
    add("    - Dampers (calibrated ratios, track-dependent)")
    add("    - Wheel geometry (camber/roll model)")
    add("    - Brake bias (weight transfer baseline)")
    add("  LOWER confidence (driver-style dependent):")
    add("    - Diff preload/ramps (driver behavior)")
    add("    - TC settings (driver consistency)")
    add("    - Tyre pressures (requires measured hot data)")
    add()

    # ── 11. Accumulated Knowledge (from learner) ──
    try:
        from learner.report_section import generate_learning_section
        learning_section = generate_learning_section(
            car=car.canonical_name,
            track=track.track_name,
            width=WIDTH,
        )
        if learning_section:
            add(learning_section)
    except Exception:
        pass  # learner not available or no data — skip silently

    add("=" * WIDTH)
    add(f"  Generated by GTP Setup Producer v1.0")
    add(f"  {now}")
    add("=" * WIDTH)

    return "\n".join(lines)
