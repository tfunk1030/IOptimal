"""Output module — setup report and JSON summary.

Aggregates all 6 solver steps into a single coherent setup sheet
suitable for entering into iRacing garage, with engineering rationale.

Layout:
  1. Header
  2. GARAGE CARD  — every value you need, two-column layout
  3. TOP ACTIONS  — prioritized list with lap time impact
  4. STINT CARD   — condensed balance curve + pushrod schedule
  5. LAP TIME SENSITIVITY — ranked table
  6. SECTOR COMPROMISE — conflict table
  7. SETUP SPACE  — feasible range table (if --space)
  8. AERO / BALANCE checks
  9. VALIDATION CHECKLIST
"""

from __future__ import annotations

import json
import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from solver.rake_solver import RakeSolution
from solver.heave_solver import HeaveSolution
from solver.corner_spring_solver import CornerSpringSolution
from solver.arb_solver import ARBSolution
from solver.wheel_geometry_solver import WheelGeometrySolution
from solver.damper_solver import DamperSolution

W = 70  # report width


def _asdict_safe(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _asdict_safe(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_asdict_safe(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _asdict_safe(v) for k, v in obj.items()}
    return obj


def _hdr(title: str) -> str:
    pad = (W - len(title) - 2) // 2
    return "─" * pad + f" {title} " + "─" * (W - pad - len(title) - 2)


def _box_top(title: str) -> str:
    inner = W - 2
    t = f" {title} "
    pad = (inner - len(t)) // 2
    return "┌" + "─" * pad + t + "─" * (inner - pad - len(t)) + "┐"


def _box_bot() -> str:
    return "└" + "─" * (W - 2) + "┘"


def _row(left: str, right: str) -> str:
    """Two-column row inside a box, total inner width W-2."""
    col = (W - 2) // 2
    return "│" + left.ljust(col) + right.ljust(W - 2 - col) + "│"


def _full(text: str) -> str:
    """Full-width row inside a box."""
    return "│" + text.ljust(W - 2) + "│"


def _blank() -> str:
    return "│" + " " * (W - 2) + "│"


def _ok(val: bool) -> str:
    return "✓" if val else "✗"


def print_full_setup_report(
    car_name: str,
    track_name: str,
    wing: float,
    target_balance: float,
    step1: RakeSolution,
    step2: HeaveSolution,
    step3: CornerSpringSolution,
    step4: ARBSolution,
    step5: WheelGeometrySolution,
    step6: DamperSolution,
    stint_result: Any = None,
    sector_result: Any = None,
    sensitivity_result: Any = None,
    space_result: Any = None,
    supporting: Any = None,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    a = lines.append

    # ── Header ────────────────────────────────────────────────────────
    a("═" * W)
    title = f"  {car_name}  ·  {track_name}  ·  Wing {wing}°"
    a(title)
    a(f"  Physics-based setup  ·  {now}")
    a("═" * W)
    a("")

    # ── GARAGE CARD ───────────────────────────────────────────────────
    a(_box_top("GARAGE CARD"))
    a(_blank())
    a(_row("  RIDE HEIGHTS & PUSHRODS", "  SPRINGS"))
    _tb_turns = round(
        0.1089 - 0.1642 / max(step2.front_heave_nmm, 1) + 0.000368 * step2.perch_offset_front_mm, 3
    )
    a(_row(f"  Front static:  {step1.static_front_rh_mm:5.1f} mm",
           f"  Heave F:    {step2.front_heave_nmm:5.0f} N/mm  perch {step2.perch_offset_front_mm:+.0f}mm"))
    a(_row(f"  Rear static:   {step1.static_rear_rh_mm:5.1f} mm",
           f"  Third R:    {step2.rear_third_nmm:5.0f} N/mm  perch {step2.perch_offset_rear_mm:+.0f}mm"))
    a(_row(f"  Rake:          {step1.rake_static_mm:5.1f} mm",
           f"  Torsion:   {step3.front_torsion_od_mm:6.2f} mm OD  {_tb_turns:.3f} Turns"))
    a(_row(f"  Front pushrod: {step1.front_pushrod_offset_mm:5.1f} mm",
           f"  Rear coil:  {step3.rear_spring_rate_nmm:5.0f} N/mm"))
    a(_row(f"  Rear pushrod:  {step1.rear_pushrod_offset_mm:5.1f} mm",
           f"  Rear perch:  {step3.rear_spring_perch_mm:5.1f} mm"))
    a(_blank())
    a(_row("  ANTI-ROLL BARS", "  WHEEL GEOMETRY"))
    a(_row(f"  FARB: {step4.front_arb_size:<6s} Blade {step4.front_arb_blade_start}  (locked)",
           f"  Front camber: {step5.front_camber_deg:+.1f}°"))
    live = f"[{step4.rarb_blade_slow_corner}→{step4.rarb_blade_fast_corner}]"
    a(_row(f"  RARB: {step4.rear_arb_size:<6s} Blade {step4.rear_arb_blade_start}  {live}",
           f"  Rear camber:  {step5.rear_camber_deg:+.1f}°"))
    a(_row("",
           f"  Front toe:  {step5.front_toe_mm:+.1f} mm"))
    a(_row("",
           f"  Rear toe:   {step5.rear_toe_mm:+.1f} mm"))
    a(_blank())
    # Diff & brakes row
    diff_str = ""
    bias_str = ""
    if supporting is not None:
        bias_str = f"  Brake bias: {supporting.brake_bias_pct:.1f}%"
        diff_str = (f"  Diff: {supporting.diff_preload_nm:.0f} Nm  "
                    f"{supporting.diff_ramp_coast}°/{supporting.diff_ramp_drive}°  "
                    f"{supporting.diff_clutch_plates}pl")
    else:
        bias_str = f"  Brake bias: (see pipeline)"
        diff_str = ""

    a(_row("  BRAKES & DIFF", "  TYRES"))
    a(_row(bias_str,
           f"  Cold pres: 152 kPa"))
    if diff_str:
        a(_row(diff_str, ""))
    a(_blank())

    # Dampers
    a(_full("  DAMPERS (clicks)                     AERO STATUS"))
    a(_row(f"            LF   RF   LR   RR",
           f"  DF bal: {step1.df_balance_pct:.2f}%  {_ok(abs(step1.df_balance_pct - target_balance) < 0.2)}"))
    a(_row(f"  LS Comp: {step6.lf.ls_comp:3d}  {step6.rf.ls_comp:3d}  {step6.lr.ls_comp:3d}  {step6.rr.ls_comp:3d}",
           f"  L/D:    {step1.ld_ratio:.3f}"))
    margin_ok = step1.vortex_burst_margin_mm > 0
    a(_row(f"  LS Rbd:  {step6.lf.ls_rbd:3d}  {step6.rf.ls_rbd:3d}  {step6.lr.ls_rbd:3d}  {step6.rr.ls_rbd:3d}",
           f"  Stall:  {step1.vortex_burst_margin_mm:+.1f}mm  {_ok(margin_ok)}"))
    a(_row(f"  HS Comp: {step6.lf.hs_comp:3d}  {step6.rf.hs_comp:3d}  {step6.lr.hs_comp:3d}  {step6.rr.hs_comp:3d}",
           f"  LLTD:   {step4.lltd_achieved:.1%}  (target {step4.lltd_target:.1%})"))
    a(_row(f"  HS Rbd:  {step6.lf.hs_rbd:3d}  {step6.rf.hs_rbd:3d}  {step6.lr.hs_rbd:3d}  {step6.rr.hs_rbd:3d}",
           f"  Dyn RH: F {step1.dynamic_front_rh_mm:.1f}  R {step1.dynamic_rear_rh_mm:.1f} mm"))
    a(_row(f"  HS Slope:{step6.lf.hs_slope:3d}  {step6.rf.hs_slope:3d}  {step6.lr.hs_slope:3d}  {step6.rr.hs_slope:3d}",
           f"  Camber: F{step5.front_camber_deg:+.1f}°  R{step5.rear_camber_deg:+.1f}°  [{step5.camber_confidence}]"))
    a(_blank())
    a(_box_bot())
    a("")

    # ── TOP ACTIONS ───────────────────────────────────────────────────
    top_actions: list[tuple[str, str]] = []

    # Gather from sensitivity if available
    if sensitivity_result is not None:
        try:
            _PARAM_LABELS = {
                "rear_arb_blade":    "RARB blade",
                "torsion_bar_od_mm": "Torsion bar OD",
                "brake_bias_pct":    "Brake bias",
                "rear_camber_deg":   "Rear camber",
                "front_rh_mm":       "Front ride height",
                "rear_rh_mm":        "Rear ride height",
                "front_heave_nmm":   "Front heave spring",
            }
            for i, s in enumerate(sensitivity_result.top_n(3)):
                label = _PARAM_LABELS.get(s.parameter, s.parameter)
                impact = f"{abs(s.delta_per_unit_ms):.0f}ms/unit"
                desc = f"{label}: {s.current_value:.3g} {s.units}  ({s.mechanism[:40]})"
                top_actions.append((desc, impact))
        except Exception:
            pass

    # Always add RARB live strategy
    live_action = (
        f"RARB live: blade {step4.rear_arb_blade_start} start  →  "
        f"{step4.rarb_blade_slow_corner} slow / {step4.rarb_blade_fast_corner} fast",
        "live adjustment"
    )
    if not any("RARB" in a[0] or "arb" in a[0].lower() for a in top_actions):
        top_actions.insert(0, live_action)

    # Add stint recommendation
    if stint_result is not None:
        try:
            bias = stint_result.setup_bias
            if bias and bias != "balanced":
                us_end = stint_result.balance_curve.understeer_deg[-1]
                stint_action = (
                    f"Stint ({bias.upper()}): start RARB {stint_result.degradation.preemptive_rarb_offset:+d} blade  "
                    f"→ +{abs(us_end):.1f}° US drift by lap {stint_result.balance_curve.lap_numbers[-1]}",
                    "endurance"
                )
                top_actions.append(stint_action)
        except Exception:
            pass

    # Add sector recommendation
    if sector_result is not None:
        try:
            sc = sector_result.slow_sector_time_pct
            fc = sector_result.fast_sector_time_pct
            dominant = "fast" if fc > sc else "slow"
            pct = max(sc, fc)
            sector_action = (
                f"Sector: {pct:.0f}% {dominant}-corner → "
                + (f"aero platform priority (heave, RH stability)"
                   if dominant == "fast"
                   else f"mechanical grip priority (RARB, diff)"),
                "track-specific"
            )
            top_actions.append(sector_action)
        except Exception:
            pass

    if top_actions:
        a(_box_top("TOP ACTIONS"))
        for i, (action, impact) in enumerate(top_actions[:6], 1):
            impact_str = f"[{impact}]"
            # Wrap long action text
            max_action = W - 4 - len(impact_str) - 2
            if len(action) > max_action:
                action = action[:max_action - 1] + "…"
            row_str = f"  {i}. {action}"
            a("│" + row_str.ljust(W - 4 - len(impact_str)) + f"  {impact_str}" + "│")
        a(_box_bot())
        a("")

    # ── CURRENT vs RECOMMENDED COMPARISON ────────────────────────────
    # (populated by pipeline when current setup is available)
    # This section is conditionally added by the pipeline path.

    # ── STINT CARD ───────────────────────────────────────────────────
    if stint_result is not None:
        try:
            bc = stint_result.balance_curve
            deg = stint_result.degradation
            a(_box_top("STINT CARD"))
            a(_full("  Balance evolution:"))
            # Compact 5-point table
            step = max(1, len(bc.lap_numbers) // 5)
            sample_idxs = list(range(0, len(bc.lap_numbers), step))
            if (len(bc.lap_numbers) - 1) not in sample_idxs:
                sample_idxs.append(len(bc.lap_numbers) - 1)
            a(_full(f"  {'Lap':>5}  {'US drift':>10}  {'RARB rec':>10}  Note"))
            a(_full("  " + "─" * (W - 4)))
            for idx in sample_idxs[:6]:
                lap = bc.lap_numbers[idx]
                us = bc.understeer_deg[idx]
                rarb = bc.rarb_recommendation[idx] if bc.rarb_recommendation else "—"
                note = "← go softer" if us > 0.8 and idx > 0 else ""
                a(_full(f"  {lap:>5}   {us:>+7.1f}°   blade {rarb:<3}   {note}"))
            a(_blank())
            # Pushrod schedule
            a(_full(f"  Pushrod correction: +{abs(stint_result.conditions[-1].fuel_state.pushrod_correction_mm):.1f}mm over stint"))
            if len(stint_result.conditions) > 1:
                mid = stint_result.conditions[len(stint_result.conditions)//2]
                last = stint_result.conditions[-1]
                a(_full(f"  Lap ~{mid.lap_number}: +{abs(mid.fuel_state.pushrod_correction_mm):.1f}mm   "
                        f"Lap ~{last.lap_number}: +{abs(last.fuel_state.pushrod_correction_mm):.1f}mm"))
            a(_full(f"  Tyre pressure rise: +{deg.pressure_rise_per_10_laps_kpa:.0f} kPa/10 laps"))
            a(_box_bot())
            a("")
        except Exception:
            pass

    # ── LAP TIME SENSITIVITY ──────────────────────────────────────────
    if sensitivity_result is not None:
        try:
            a(_box_top("LAP TIME SENSITIVITY"))
            a(_full(f"  {'#':<3}  {'Parameter':<24}  {'Current':>8}  {'Impact':>12}  {'Conf'}"))
            a(_full("  " + "─" * (W - 4)))
            for i, s in enumerate(sensitivity_result.sensitivities[:7], 1):
                sign = "+" if s.delta_per_unit_ms > 0 else ""
                impact = f"{sign}{s.delta_per_unit_ms:.0f}ms/unit"
                a(_full(f"  {i:<3}  {s.parameter:<24}  {s.current_value:>8.2g}  {impact:>12}  {s.confidence}"))
            a(_blank())
            try:
                top = sensitivity_result.top_n(1)[0]
                a(_full(f"  Biggest lever: {top.parameter} ({abs(top.delta_per_unit_ms):.0f}ms/unit)"))
            except Exception:
                pass
            a(_box_bot())
            a("")
        except Exception:
            pass

    # ── SECTOR COMPROMISE ─────────────────────────────────────────────
    if sector_result is not None:
        try:
            a(_box_top("SECTOR COMPROMISE"))
            sc_pct = sector_result.slow_sector_time_pct
            mc_pct = sector_result.medium_sector_time_pct
            fc_pct = sector_result.fast_sector_time_pct
            a(_full(f"  Track split:  slow {sc_pct:.0f}%  ·  medium {mc_pct:.0f}%  ·  fast {fc_pct:.0f}%"))
            a(_blank())
            a(_full(f"  {'Parameter':<18}  {'Slow best':>10}  {'Fast best':>10}  {'Compromise':>12}  Cost"))
            a(_full("  " + "─" * (W - 4)))
            total_cost = 0.0
            for pc in sector_result.parameter_conflicts:
                cost_ms = getattr(pc, "time_cost_ms", 0.0)
                total_cost += cost_ms
                a(_full(f"  {pc.parameter:<18}  {pc.slow_optimal:>10}  {pc.fast_optimal:>10}  {pc.compromise:>12}  {cost_ms:.0f}ms"))
                note = getattr(pc, "note", "")
                if note:
                    # Wrap note across multiple lines at word boundary
                    max_note = W - 8
                    while note:
                        chunk = note[:max_note]
                        if len(note) > max_note:
                            # break at last space
                            bp = chunk.rfind(" ")
                            if bp > 0:
                                chunk = chunk[:bp]
                        a(_full(f"    ↳ {chunk}"))
                        note = note[len(chunk):].lstrip()
            a(_blank())
            a(_full(f"  Total compromise cost: ~{total_cost:.0f}ms  ·  use live RARB to recover"))
            # Recommendations
            recs = getattr(sector_result, "compromise_recommendations", [])
            for rec in recs[:3]:
                a(_full(f"  → {rec[:W-6]}"))
            a(_box_bot())
            a("")
        except Exception:
            pass

    # ── SETUP SPACE ───────────────────────────────────────────────────
    if space_result is not None:
        try:
            a(_box_top("SETUP SPACE  (feasible range · flat bottom = <100ms of optimal)"))
            a(_full(f"  {'Parameter':<22}  {'Optimal':>8}  {'Min':>8}  {'Max':>8}  {'FlatW':>6}  Robust"))
            a(_full("  " + "─" * (W - 4)))
            for pr in space_result.parameter_ranges:
                fw = pr.flat_bottom_max - pr.flat_bottom_min
                a(_full(f"  {pr.parameter:<22}  {pr.optimal:>8.2g}  {pr.feasible_min:>8.2g}  "
                        f"{pr.feasible_max:>8.2g}  {fw:>6.2g}  {pr.robustness}"))
            a(_blank())
            try:
                a(_full(f"  Nail this: {space_result.tightest_constraint}  (smallest feasible range)"))
                a(_full(f"  Latitude:  {space_result.most_robust_parameter}  (wide flat bottom — adjust on track)"))
            except Exception:
                pass
            a(_box_bot())
            a("")
        except Exception:
            pass

    # ── BALANCE & PLATFORM CHECKS ─────────────────────────────────────
    a(_hdr("BALANCE & PLATFORM"))
    a(f"  LLTD achieved: {step4.lltd_achieved:.1%}  target: {step4.lltd_target:.1%}  "
      f"RARB sensitivity: {step4.rarb_sensitivity_per_blade:+.1%}/blade")
    a(f"  RARB 1→{step4.rarb_blade_fast_corner} range: {step4.lltd_at_rarb_min:.1%}→{step4.lltd_at_rarb_max:.1%}")
    a(f"  Heave: {step2.front_heave_nmm:.0f} N/mm  (bottom margin: {step2.front_bottoming_margin_mm:.1f}mm)")
    a(f"  Torsion: {step3.front_torsion_od_mm:.2f}mm OD  {step3.front_natural_freq_hz:.2f}Hz  "
      f"heave/corner: {step3.front_heave_corner_ratio:.1f}x")
    a(f"  Rear coil: {step3.rear_spring_rate_nmm:.0f} N/mm  {step3.rear_natural_freq_hz:.2f}Hz  "
      f"third/corner: {step3.rear_third_corner_ratio:.1f}x")
    a(f"  Roll at peak {step5.peak_lat_g:.2f}g: {step5.body_roll_at_peak_deg:.1f}°  "
      f"Fcamber dynamic: {step5.front_dynamic_camber_at_peak_deg:+.2f}°  [{step5.camber_confidence}]")
    a(f"  Tyres to op temp: fronts ~{step5.expected_conditioning_laps_front:.0f} laps  "
      f"rears ~{step5.expected_conditioning_laps_rear:.0f} laps")
    a("")

    # ── VALIDATION CHECKLIST ──────────────────────────────────────────
    a(_hdr("VALIDATION CHECKLIST"))
    a("  [ ] 5 laps minimum before judging — tyres need conditioning")
    a("  [ ] Check IBT ride heights vs Step 1 targets (dyn vs static)")
    a(f"  [ ] Stall margin {step1.vortex_burst_margin_mm:.1f}mm — watch if RH drops on bumps")
    a("  [ ] Shock vel p99 >800mm/s → stiffen HS comp +1 click")
    a("  [ ] Tyre temp spread (inner-outer) → flag for camber calibration")
    a(f"  [ ] RARB live: blade {step4.rarb_blade_slow_corner} (slow) ↔ blade {step4.rarb_blade_fast_corner} (fast)")
    a("  [ ] Do NOT touch dampers until Steps 1-5 validated")
    a("")
    a("  Starting points only. Validate every step on track.")
    a("═" * W)

    return "\n".join(lines)


def print_comparison_table(
    current_setup: Any,
    recommended: dict[str, tuple[float, str]],
) -> str:
    """Print a side-by-side current vs recommended comparison table.

    Args:
        current_setup: CurrentSetup object with current garage values
        recommended: {param: (recommended_value, units)} dict
    """
    lines: list[str] = []
    a = lines.append

    a(_box_top("CURRENT vs RECOMMENDED"))
    a(_full(f"  {'Parameter':<24}  {'Current':>10}  {'Recommended':>12}  {'Change':>10}"))
    a(_full("  " + "─" * (W - 4)))

    param_map = {
        "front_rh_mm":          ("Front static RH",    current_setup.front_rh_static_mm,    "mm"),
        "rear_rh_mm":           ("Rear static RH",     current_setup.rear_rh_static_mm,     "mm"),
        "front_heave_nmm":      ("Front heave spring", current_setup.front_heave_nmm,       "N/mm"),
        "rear_third_nmm":       ("Rear third spring",  current_setup.rear_third_nmm,        "N/mm"),
        "torsion_bar_od_mm":    ("Torsion bar OD",     current_setup.front_torsion_od_mm,   "mm"),
        "rear_spring_nmm":      ("Rear coil spring",   current_setup.rear_spring_rate_nmm,  "N/mm"),
        "rear_arb_blade":       ("RARB blade",         current_setup.rear_arb_blade_start,  "blade"),
        "front_camber_deg":     ("Front camber",       current_setup.front_camber_deg,      "°"),
        "rear_camber_deg":      ("Rear camber",        current_setup.rear_camber_deg,       "°"),
        "brake_bias_pct":       ("Brake bias",         current_setup.brake_bias_pct,        "%"),
    }

    any_change = False
    for param, (label, cur_val, units) in param_map.items():
        if param not in recommended or cur_val is None or cur_val == 0:
            continue
        rec_val, _ = recommended[param]
        delta = rec_val - cur_val
        if abs(delta) < 0.05:
            continue
        any_change = True
        arrow = "↑" if delta > 0 else "↓"
        change_str = f"{delta:+.1f} {units} {arrow}"
        a(_full(f"  {label:<24}  {cur_val:>10.2g}  {rec_val:>12.2g}  {change_str:>10}"))

    if not any_change:
        a(_full("  No significant changes from current setup."))

    a(_box_bot())
    return "\n".join(lines)


def save_json_summary(
    car_name: str,
    track_name: str,
    wing: float,
    step1: RakeSolution,
    step2: HeaveSolution,
    step3: CornerSpringSolution,
    step4: ARBSolution,
    step5: WheelGeometrySolution,
    step6: DamperSolution,
    output_path: str | Path,
) -> None:
    """Save all solver outputs as structured JSON."""
    summary = {
        "meta": {
            "car": car_name,
            "track": track_name,
            "wing": wing,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "step1_rake": _asdict_safe(step1),
        "step2_heave": _asdict_safe(step2),
        "step3_corner": _asdict_safe(step3),
        "step4_arb": _asdict_safe(step4),
        "step5_geometry": _asdict_safe(step5),
        "step6_dampers": _asdict_safe(step6),
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(summary, indent=2))
