"""Output module — setup report and JSON summary.

Aggregates all 6 solver steps into a single coherent setup sheet
suitable for entering into iRacing garage, with engineering rationale.
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
from solver.coupling import coupling_notes_for_report, COUPLING_CHAINS


def _asdict_safe(obj: Any) -> Any:
    """Recursively convert dataclasses to dicts for JSON serialization."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _asdict_safe(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_asdict_safe(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _asdict_safe(v) for k, v in obj.items()}
    return obj


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
) -> str:
    """Generate the complete setup sheet as a printable string.

    Optional extra results (all default to None for backward compatibility):
        stint_result: StintStrategy from solver.stint_model
        sector_result: SectorCompromiseResult from solver.sector_compromise
        sensitivity_result: LaptimeSensitivityReport from solver.laptime_sensitivity
        space_result: SetupSpaceReport from solver.setup_space
    """

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    width = 63

    def section(title: str) -> str:
        pad = (width - len(title) - 2) // 2
        return "=" * pad + f" {title} " + "=" * (width - pad - len(title) - 2)

    lines = [
        "=" * width,
        f"  GTP SETUP BUILDER — PHYSICS-BASED SETUP SHEET",
        f"  {now}",
        "=" * width,
        f"  Car:    {car_name}",
        f"  Track:  {track_name}",
        f"  Wing:   {wing}°",
        f"  Target DF balance:  {target_balance:.2f}%",
        "=" * width,
        "",
        "  !!  IMPORTANT: These are physics-derived STARTING POINTS.",
        "  Validate with a 5-lap IBT session before committing.",
        "  Follow the 6-step workflow — do not adjust dampers (Step 6)",
        "  until Steps 1-5 are validated on track.",
        "",
        section("GARAGE SETTINGS SUMMARY"),
        "",
        "  RIDE HEIGHTS (garage)",
        f"    Front static:   {step1.static_front_rh_mm:6.1f} mm",
        f"    Rear static:    {step1.static_rear_rh_mm:6.1f} mm",
        f"    Rake:           {step1.rake_static_mm:6.1f} mm",
        "",
        "  PUSHROD OFFSETS",
        f"    Front:  {step1.front_pushrod_offset_mm:6.1f} mm",
        f"    Rear:   {step1.rear_pushrod_offset_mm:6.1f} mm",
        "",
        "  HEAVE / THIRD SPRINGS",
        f"    Front heave:    {step2.front_heave_nmm:6.1f} N/mm",
        f"    Rear third:     {step2.rear_third_nmm:6.1f} N/mm",
        f"    Front perch:    {step2.perch_offset_front_mm:6.0f} mm",
        f"    Rear perch:     {step2.perch_offset_rear_mm:6.0f} mm",
        "",
        "  CORNER SPRINGS",
        f"    Front torsion bar OD:  {step3.front_torsion_od_mm:.2f} mm",
        f"    Rear coil spring:      {step3.rear_spring_rate_nmm:.0f} N/mm",
        f"    Rear spring perch:     {step3.rear_spring_perch_mm:.1f} mm",
        "",
        "  ANTI-ROLL BARS",
        f"    Front ARB size:   {step4.front_arb_size}",
        f"    Front ARB blade:  {step4.front_arb_blade_start}  (keep locked here)",
        f"    Rear ARB size:    {step4.rear_arb_size}",
        f"    Rear ARB blade:   {step4.rear_arb_blade_start}  (starting point — adjust live)",
        f"    Live RARB range:  blade {step4.rarb_blade_slow_corner} (slow) -> "
        f"blade {step4.rarb_blade_fast_corner} (fast)",
        "",
        "  WHEEL GEOMETRY",
        f"    Front camber:  {step5.front_camber_deg:+.1f}°",
        f"    Rear camber:   {step5.rear_camber_deg:+.1f}°",
        f"    Front toe:     {step5.front_toe_mm:+.1f} mm  "
        f"({'toe-out' if step5.front_toe_mm < 0 else 'toe-in' if step5.front_toe_mm > 0 else 'neutral'})",
        f"    Rear toe:      {step5.rear_toe_mm:+.1f} mm  "
        f"({'toe-out' if step5.rear_toe_mm < 0 else 'toe-in' if step5.rear_toe_mm > 0 else 'neutral'})",
        "",
        "  DAMPERS (all in clicks — BMW scale)",
        "",
        "              LF    RF    LR    RR",
        f"  LS Comp:  {step6.lf.ls_comp:4d}  {step6.rf.ls_comp:4d}  {step6.lr.ls_comp:4d}  {step6.rr.ls_comp:4d}",
        f"  LS Rbd:   {step6.lf.ls_rbd:4d}  {step6.rf.ls_rbd:4d}  {step6.lr.ls_rbd:4d}  {step6.rr.ls_rbd:4d}",
        f"  HS Comp:  {step6.lf.hs_comp:4d}  {step6.rf.hs_comp:4d}  {step6.lr.hs_comp:4d}  {step6.rr.hs_comp:4d}",
        f"  HS Rbd:   {step6.lf.hs_rbd:4d}  {step6.rf.hs_rbd:4d}  {step6.lr.hs_rbd:4d}  {step6.rr.hs_rbd:4d}",
        f"  HS Slope: {step6.lf.hs_slope:4d}  {step6.rf.hs_slope:4d}  {step6.lr.hs_slope:4d}  {step6.rr.hs_slope:4d}",
        "",
        section("AERO VALIDATION"),
        "",
        f"  Dynamic front RH:   {step1.dynamic_front_rh_mm:.1f} mm",
        f"  Dynamic rear RH:    {step1.dynamic_rear_rh_mm:.1f} mm",
        f"  DF balance:         {step1.df_balance_pct:.2f}%  (target: {target_balance:.2f}%)",
        f"  L/D ratio:          {step1.ld_ratio:.3f}",
        f"  Vortex burst margin:{step1.vortex_burst_margin_mm:.1f} mm  "
        f"({'OK' if step1.vortex_burst_margin_mm > 0 else 'VIOLATED'})",
        "",
        section("MECHANICAL BALANCE"),
        "",
        f"  LLTD (achieved):    {step4.lltd_achieved:.1%}",
        f"  LLTD (target):      {step4.lltd_target:.1%}",
        f"  Static front wt:    {step4.static_front_weight_dist:.1%}",
        f"  RARB sensitivity:   {step4.rarb_sensitivity_per_blade:+.1%} LLTD per blade",
        f"  LLTD range (RARB 1->{step4.rarb_blade_fast_corner}):  "
        f"{step4.lltd_at_rarb_min:.1%} -> {step4.lltd_at_rarb_max:.1%}",
        "",
        section("THERMAL PREDICTION"),
        "",
        f"  Body roll at peak {step5.peak_lat_g:.2f}g:  {step5.body_roll_at_peak_deg:.1f}°",
        f"  Front dynamic camber @ peak g:  {step5.front_dynamic_camber_at_peak_deg:+.1f}°",
        f"  Rear dynamic camber @ peak g:   {step5.rear_dynamic_camber_at_peak_deg:+.1f}°",
        f"  Fronts reach op temp:  ~{step5.expected_conditioning_laps_front:.0f} laps",
        f"  Rears reach op temp:   ~{step5.expected_conditioning_laps_rear:.0f} laps",
        "",
        section("COUPLING SENSITIVITY"),
        "",
    ]

    # Add coupling notes for the key solver outputs
    # Use delta-from-baseline (BMW reference values)
    _BMW_TORSION_BASELINE_OD = 13.9
    _torsion_delta = step3.front_torsion_od_mm - _BMW_TORSION_BASELINE_OD
    _arb_delta = step4.rear_arb_blade_start - 3  # BMW baseline blade = 3
    _coupling_notes = coupling_notes_for_report(
        torsion_od_delta=_torsion_delta,
        rear_arb_delta=_arb_delta,
    )
    if _coupling_notes:
        for _note in _coupling_notes:
            lines.append(f"  {_note}")
    else:
        lines.append("  No significant deviations from coupling baselines.")
    lines += [
        "",
        section("PLATFORM CHECKS"),
        "",
        f"  Front heave:  {step2.front_heave_nmm:.0f} N/mm  "
        f"(bottoming margin: {step2.front_bottoming_margin_mm:.1f} mm)",
        f"  Rear third:   {step2.rear_third_nmm:.0f} N/mm  "
        f"(sigma: {step2.rear_sigma_at_rate_mm:.1f} mm)",
        f"  Front torsion bar:  {step3.front_torsion_od_mm:.2f} mm  "
        f"(freq: {step3.front_natural_freq_hz:.2f} Hz, heave/corner: {step3.front_heave_corner_ratio:.1f}x)",
        f"  Rear coil spring:   {step3.rear_spring_rate_nmm:.0f} N/mm  "
        f"(freq: {step3.rear_natural_freq_hz:.2f} Hz, third/corner: {step3.rear_third_corner_ratio:.1f}x)",
        "",
        "",
        "  GARAGE LIMITS",
        "  Heave slider defl: check in garage (target <45mm static)",
        "  Heave/third perch offsets: integer increments only",
        "",
    ]

    # ── Optional: Stint Analysis ──
    if stint_result is not None:
        try:
            lines.append(stint_result.summary(width))
            lines.append("")
        except Exception:
            pass

    # ── Optional: Sector Compromise ──
    if sector_result is not None:
        try:
            lines.append(sector_result.summary(width))
            lines.append("")
        except Exception:
            pass

    # ── Optional: Lap Time Sensitivity ──
    if sensitivity_result is not None:
        try:
            lines.append(sensitivity_result.summary(width))
            lines.append("")
        except Exception:
            pass

    # ── Optional: Setup Space Exploration ──
    if space_result is not None:
        try:
            lines.append(space_result.summary(width))
            lines.append("")
        except Exception:
            pass

    lines += [
        section("VALIDATION CHECKLIST"),
        "",
        "  Before calling the setup 'done':",
        "  [ ] Run 5 laps minimum for tyres to condition",
        "  [ ] Check ride heights on IBT (compare dynamic to Step 1 targets)",
        "  [ ] Confirm front vortex burst margin not violated",
        "  [ ] Check shock velocity p99 — if >800mm/s, stiffen HS comp +1",
        "  [ ] Check tyre temp spread (inner/outer) for camber validation",
        "  [ ] RARB live range confirmed: blade 1 slow corners, 4-5 fast corners",
        "  [ ] Dampers: only adjust after ride heights + springs + ARBs validated",
        "",
        "=" * width,
    ]
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
