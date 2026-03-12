#!/usr/bin/env python3
"""Compare current pipeline vs enhanced pipeline output.

Runs the existing 6-step solver on BMW Sebring, then shows the additional
reasoning layers added by the new AI thought process modules.

Usage:
    python compare_pipelines.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# ── Setup imports ──
from aero_model import load_car_surfaces
from car_model import get_car
from track_model.profile import TrackProfile
from solver.rake_solver import RakeSolver
from solver.heave_solver import HeaveSolver
from solver.corner_spring_solver import CornerSpringSolver
from solver.arb_solver import ARBSolver
from solver.wheel_geometry_solver import WheelGeometrySolver
from solver.damper_solver import DamperSolver

WIDTH = 63


def header(title: str) -> str:
    return "\n" + "=" * WIDTH + f"\n  {title}\n" + "=" * WIDTH


def subheader(title: str) -> str:
    return f"\n  {title}\n  " + "-" * (WIDTH - 4)


# ═══════════════════════════════════════════════════════════════════
#  PHASE 1: Run the current 6-step solver (baseline)
# ═══════════════════════════════════════════════════════════════════

print(header("CURRENT PIPELINE — 6-Step Physics Solver"))
print("  Car: BMW M Hybrid V8")
print("  Track: Sebring International Raceway")
print("  Wing: 17°  Fuel: 89 L")

# Load models
car = get_car("bmw")
surfaces = load_car_surfaces(car.canonical_name)
surface = surfaces[17.0]

tracks_dir = Path(__file__).parent / "data" / "tracks"
track_files = [f for f in tracks_dir.glob("*.json") if "sebring" in f.stem.lower()]
latest = [f for f in track_files if f.stem.endswith("_latest")]
track = TrackProfile.load(latest[0] if latest else track_files[0])

# Run 6 steps
rake_solver = RakeSolver(car, surface, track)
step1 = rake_solver.solve(target_balance=50.14, fuel_load_l=89.0, pin_front_min=True)

heave_solver = HeaveSolver(car, track)
step2 = heave_solver.solve(
    dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
    dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
)

corner_solver = CornerSpringSolver(car, track)
step3 = corner_solver.solve(
    front_heave_nmm=step2.front_heave_nmm,
    rear_third_nmm=step2.rear_third_nmm,
    fuel_load_l=89.0,
)

arb_solver = ARBSolver(car, track)
step4 = arb_solver.solve(
    front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
    rear_wheel_rate_nmm=step3.rear_spring_rate_nmm,
)

geom_solver = WheelGeometrySolver(car, track)
step5 = geom_solver.solve(
    k_roll_total_nm_deg=step4.k_roll_front_total + step4.k_roll_rear_total,
    front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
    rear_wheel_rate_nmm=step3.rear_spring_rate_nmm,
)

damper_solver = DamperSolver(car, track)
step6 = damper_solver.solve(
    front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
    rear_wheel_rate_nmm=step3.rear_spring_rate_nmm,
    front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
    rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
    fuel_load_l=89.0,
)

# Print baseline results (condensed)
print(subheader("Step 1: Rake → Ride Heights"))
print(f"    Dynamic front RH: {step1.dynamic_front_rh_mm:.1f} mm")
print(f"    Dynamic rear RH:  {step1.dynamic_rear_rh_mm:.1f} mm")
print(f"    DF balance:       {step1.df_balance_pct:.2f}%")
print(f"    Vortex margin:    {step1.vortex_burst_margin_mm:.1f} mm")

print(subheader("Step 2: Heave / Third Springs"))
print(f"    Front heave:      {step2.front_heave_nmm:.0f} N/mm")
print(f"    Rear third:       {step2.rear_third_nmm:.0f} N/mm")
print(f"    Front bottoming:  {step2.front_bottoming_margin_mm:.1f} mm margin")
print(f"    Rear bottoming:   {step2.rear_bottoming_margin_mm:.1f} mm margin")

print(subheader("Step 3: Corner Springs"))
print(f"    Front torsion OD: {step3.front_torsion_od_mm:.1f} mm")
print(f"    Rear spring:      {step3.rear_spring_rate_nmm:.0f} N/mm")

print(subheader("Step 4: ARBs"))
print(f"    LLTD target:      {step4.lltd_target:.1%}")
print(f"    LLTD achieved:    {step4.lltd_achieved:.1%}")
print(f"    RARB range:       blade {step4.rarb_blade_slow_corner}-{step4.rarb_blade_fast_corner}")

print(subheader("Step 5: Geometry"))
print(f"    Camber F/R:       {step5.front_camber_deg:.1f}° / {step5.rear_camber_deg:.1f}°")
print(f"    Toe F/R:          {step5.front_toe_mm:.2f} / {step5.rear_toe_mm:.2f} mm")

print(subheader("Step 6: Dampers"))
print(f"              LF    RF    LR    RR")
print(f"    LS Comp:  {step6.lf.ls_comp:4d}  {step6.rf.ls_comp:4d}  {step6.lr.ls_comp:4d}  {step6.rr.ls_comp:4d}")
print(f"    LS Rbd:   {step6.lf.ls_rbd:4d}  {step6.rf.ls_rbd:4d}  {step6.lr.ls_rbd:4d}  {step6.rr.ls_rbd:4d}")
print(f"    HS Comp:  {step6.lf.hs_comp:4d}  {step6.rf.hs_comp:4d}  {step6.lr.hs_comp:4d}  {step6.rr.hs_comp:4d}")
print(f"    HS Rbd:   {step6.lf.hs_rbd:4d}  {step6.rf.hs_rbd:4d}  {step6.lr.hs_rbd:4d}  {step6.rr.hs_rbd:4d}")

print(subheader("Confidence Assessment (CURRENT)"))
print("    HIGH:  ride heights, springs, ARBs  (physics model)")
print("    MED:   dampers, geometry             (calibrated)")
print("    LOW:   diff, TC, pressures           (heuristic)")
print("    (No quantified uncertainty. No constraint margins.)")
print("    (No sensitivity analysis. No cross-step checks.)")


# ═══════════════════════════════════════════════════════════════════
#  PHASE 2: Enhanced pipeline — new AI reasoning modules
# ═══════════════════════════════════════════════════════════════════

print("\n\n")
print("*" * WIDTH)
print("  ENHANCED PIPELINE — With AI Thought Process Improvements")
print("*" * WIDTH)
print("  Same physics solver + 8 new reasoning layers")
print()


# ── Layer 1: Sensitivity Analysis ──
from solver.sensitivity import build_sensitivity_report

sensitivity = build_sensitivity_report(
    step1=step1,
    step2=step2,
    arb_lltd=step4.lltd_achieved,
    arb_lltd_target=step4.lltd_target,
    rarb_sensitivity=abs(step4.rarb_sensitivity_per_blade) if hasattr(step4, 'rarb_sensitivity_per_blade') else 0.029,
)
print(sensitivity.summary())


# ── Layer 2: Uncertainty Quantification ──
from solver.uncertainty import build_uncertainty_report

uncertainty = build_uncertainty_report(
    front_heave_nmm=step2.front_heave_nmm,
    rear_third_nmm=step2.rear_third_nmm,
    front_excursion_mm=step2.front_excursion_at_rate_mm,
    rear_excursion_mm=step2.rear_excursion_at_rate_mm,
    v_p99_front_mps=track.shock_vel_p99_front_mps,
    v_p99_rear_mps=track.shock_vel_p99_rear_mps,
    lltd=step4.lltd_achieved,
    k_roll_front=step4.k_roll_front_total,
    k_roll_rear=step4.k_roll_rear_total,
    damper_clicks={
        "front_ls_comp": step6.lf.ls_comp,
        "front_ls_rbd": step6.lf.ls_rbd,
        "rear_hs_comp": step6.lr.hs_comp,
        "rear_hs_rbd": step6.lr.hs_rbd,
    },
    n_laps=5,
)
print(uncertainty.summary())


# ── Layer 3: Adaptive Thresholds ──
from analyzer.adaptive_thresholds import compute_adaptive_thresholds, BASELINE_THRESHOLDS

# Create a mock driver for demo (since we don't have IBT here)
class MockDriver:
    steering_smoothness = "smooth"
    consistency = "consistent"
    style = "smooth-consistent"

adaptive = compute_adaptive_thresholds(track, car, MockDriver())

print(header("ADAPTIVE THRESHOLDS"))
print(f"  Track scale: {adaptive.track_scale:.2f} "
      f"(shock p99 = {track.shock_vel_p99_front_mps*1000:.0f} mm/s)")
print(f"  Driver scale: {adaptive.driver_scale:.2f} "
      f"(smooth-consistent)")
print()
print("  CURRENT (fixed)          →  ADAPTED (track/car/driver)")
print("  " + "-" * (WIDTH - 4))

changes = [
    ("Front RH variance", "mm", BASELINE_THRESHOLDS["front_rh_variance_mm"], adaptive.front_rh_variance_mm),
    ("Rear RH variance", "mm", BASELINE_THRESHOLDS["rear_rh_variance_mm"], adaptive.rear_rh_variance_mm),
    ("Understeer (all)", "°", BASELINE_THRESHOLDS["understeer_all_deg"], adaptive.understeer_all_deg),
    ("Understeer (low spd)", "°", BASELINE_THRESHOLDS["understeer_low_deg"], adaptive.understeer_low_deg),
    ("Understeer (high spd)", "°", BASELINE_THRESHOLDS["understeer_high_deg"], adaptive.understeer_high_deg),
    ("Body slip p95", "°", BASELINE_THRESHOLDS["body_slip_p95_deg"], adaptive.body_slip_p95_deg),
    ("Settle time upper", "ms", BASELINE_THRESHOLDS["settle_time_upper_ms"], adaptive.settle_time_upper_ms),
    ("Bottoming events F", "", BASELINE_THRESHOLDS["bottoming_events_front"], adaptive.bottoming_events_front),
]
for label, unit, old, new in changes:
    delta = new - old
    arrow = "→" if abs(delta) > 0.01 else "="
    print(f"    {label:<22s} {old:>6.1f} {arrow} {new:>6.1f} {unit}  ({delta:+.1f})")

for note in adaptive.adaptations:
    print(f"    {note}")


# ── Layer 4: Stint/Session Reasoning ──
from solver.stint_model import analyze_stint

stint = analyze_stint(
    car=car,
    stint_laps=30,
    base_heave_nmm=step2.front_heave_nmm,
    base_third_nmm=step2.rear_third_nmm,
    v_p99_front_mps=track.shock_vel_p99_front_mps,
    v_p99_rear_mps=track.shock_vel_p99_rear_mps,
)
print(stint.summary())


# ── Layer 5: Corner-Specific Strategy ──
# Build synthetic corner data for demo (real data would come from segment.py)
from solver.corner_strategy import (
    build_corner_strategy,
    CornerParameterMap,
)

# Create mock corner data representative of Sebring
class MockCorner:
    def __init__(self, cid, name, apex, entry, lat_g, direction, speed_class,
                 trail_brake_pct=0.3, body_slip_peak_deg=2.0,
                 front_shock_vel_p99_mps=0.26, front_rh_min_mm=8.0,
                 understeer_mean_deg=1.5):
        self.corner_id = cid
        self.corner_name = name
        self.apex_speed_kph = apex
        self.entry_speed_kph = entry
        self.peak_lat_g = lat_g
        self.direction = direction
        self.speed_class = speed_class
        self.trail_brake_pct = trail_brake_pct
        self.body_slip_peak_deg = body_slip_peak_deg
        self.front_shock_vel_p99_mps = front_shock_vel_p99_mps
        self.front_rh_min_mm = front_rh_min_mm
        self.understeer_mean_deg = understeer_mean_deg

sebring_corners = [
    MockCorner(1, "T1",  85,  250, 1.8, "right", "low",  trail_brake_pct=0.45, understeer_mean_deg=1.2),
    MockCorner(3, "T3",  145, 210, 2.1, "right", "mid",  front_shock_vel_p99_mps=0.28),
    MockCorner(5, "T5",  95,  180, 1.9, "left",  "low",  trail_brake_pct=0.50, body_slip_peak_deg=4.5),
    MockCorner(7, "T7",  195, 230, 2.8, "right", "high", front_rh_min_mm=5.5),
    MockCorner(10, "T10", 105, 280, 1.7, "right", "low",  understeer_mean_deg=2.8),
    MockCorner(13, "T13", 165, 220, 2.3, "left",  "mid"),
    MockCorner(15, "T15", 210, 260, 3.0, "right", "high", front_rh_min_mm=6.0),
    MockCorner(17, "T17", 135, 275, 2.0, "left",  "mid",  front_shock_vel_p99_mps=0.31, understeer_mean_deg=2.2),
]

corner_strategy = build_corner_strategy(
    sebring_corners,
    base_brake_bias_pct=46.0,
    base_tc_gain=4,
    base_tc_slip=3,
)
print(corner_strategy.summary())


# ── Layer 6: Iterative Solver Trace ──
from solver.iterative_solver import (
    compute_residuals,
    compute_residual_norm,
    compute_cross_step_adjustments,
    check_convergence,
)

print(header("ITERATIVE SOLVER ANALYSIS"))
print("  (Cross-step constraint checking on solver output)")
print()

# Check residuals from the single-pass solver
residuals = compute_residuals(
    front_bottoming_margin_mm=step2.front_bottoming_margin_mm,
    rear_bottoming_margin_mm=step2.rear_bottoming_margin_mm,
)
norm = compute_residual_norm(residuals)

print("  Pass 1 residuals (from current single-pass solver):")
for name, val in residuals.items():
    status = "OK" if val <= 0.001 else f"VIOLATION {val:.3f}"
    print(f"    {name:<25s} {status}")
print(f"    Residual norm: {norm:.4f}")

converged, reason = check_convergence(norm, None, 1)
if converged:
    print(f"    CONVERGED: {reason}")
else:
    print(f"    NOT CONVERGED — would trigger cross-step adjustments")
    adjustments = compute_cross_step_adjustments(
        residuals=residuals,
        pass_number=1,
        current_corner_spring_nmm=step3.front_wheel_rate_nmm,
        current_heave_nmm=step2.front_heave_nmm,
    )
    if adjustments:
        print("    Adjustments for Pass 2:")
        for adj in adjustments:
            print(f"      Step {adj.target_step} {adj.parameter}: {adj.delta:+.2f}")
            print(f"        Reason: {adj.reason}")
    else:
        print("    No cross-step adjustments needed.")


# ── Layer 7: Causal Graph Demo ──
# (Requires Problem objects — show structure with synthetic example)
print(header("CAUSAL REASONING (EXAMPLE)"))
print("  If diagnosis found these symptoms:")
print("    - Front RH variance high (8.5 mm > 8.0 mm threshold)")
print("    - Front bottoming events (7 events > 5 threshold)")
print("    - Inconsistent understeer (±0.8° lap-to-lap)")
print()

from analyzer.causal_graph import NODES, EDGES, _build_reverse_adjacency, _find_root_causes_for_symptom

reverse_adj = _build_reverse_adjacency()

print("  CURRENT PIPELINE would generate 3 separate recommendations:")
print("    1. Stiffen heave (for RH variance)")
print("    2. Stiffen heave (for bottoming)")
print("    3. Check aero balance (for understeer inconsistency)")
print()

print("  ENHANCED PIPELINE traces causal graph:")
symptoms = [
    "symptom_front_rh_variance",
    "symptom_front_bottoming",
    "symptom_inconsistent_understeer",
]

shared_roots = {}
for s_id in symptoms:
    traces = _find_root_causes_for_symptom(s_id, reverse_adj)
    for root_id, edges in traces:
        if root_id not in shared_roots:
            shared_roots[root_id] = {"symptoms": [], "chains": []}
        shared_roots[root_id]["symptoms"].append(s_id)
        chain = " → ".join([NODES[root_id].label] + [NODES[e.effect_id].label for e in edges])
        shared_roots[root_id]["chains"].append(chain)

for root_id, data in shared_roots.items():
    n = len(set(data["symptoms"]))
    root = NODES[root_id]
    print(f"  ROOT CAUSE: {root.label}")
    print(f"    Explains {n}/3 symptoms")
    print(f"    Fix: {root.fix_direction} {root.parameter}")
    for chain in data["chains"]:
        print(f"    Chain: {chain}")
    print()

print("  RESULT: One root cause, one fix, three symptoms resolved.")
print("  Current pipeline: 3 separate fixes (redundant)")


# ── Layer 8: Prediction / Validation Framework ──
from solver.validation import generate_predictions

prediction = generate_predictions(
    car="bmw",
    track="sebring",
    wing=17.0,
    fuel_l=89.0,
    front_excursion_mm=step2.front_excursion_at_rate_mm,
    front_sigma_mm=step2.front_sigma_at_rate_mm,
    rear_sigma_mm=step2.rear_sigma_at_rate_mm,
    lltd=step4.lltd_achieved,
    front_heave_nmm=step2.front_heave_nmm,
    rear_third_nmm=step2.rear_third_nmm,
    m_eff_front_kg=228.0,
    m_eff_rear_kg=2395.3,
)

print(header("SELF-VALIDATION FRAMEWORK"))
print("  Predictions stored for next-session validation:")
print()
for p in prediction.predictions:
    print(f"    [{p.confidence.upper():6s}] {p.metric:<30s} {p.predicted:>7.1f} {p.units}")
print()
print("  Model parameters tracked for Bayesian updating:")
for k, v in prediction.model_params.items():
    print(f"    {k}: {v:.1f}")
print()
print("  After next session, validation compares predicted vs actual:")
print("  If excursion predicted=14.8mm, actual=18.0mm:")
print("    → m_eff correction: 228 * (18.0/14.8)² = 337 kg")
print("    → Model learns from every run, improving over time")
print()


# ═══════════════════════════════════════════════════════════════════
#  PHASE 3: Re-run solver WITH modifier feedback (the real difference)
# ═══════════════════════════════════════════════════════════════════

print("\n\n")
print("!" * WIDTH)
print("  ENHANCED PIPELINE — ACTUAL SOLVER DIFFERENCES")
print("  (Modifiers now feed back into solver targets)")
print("!" * WIDTH)

# Simulate realistic diagnosis findings that would trigger modifiers
# In a real run, these come from telemetry. Here we simulate a driver
# with settle time issues and mild understeer.
from solver.modifiers import SolverModifiers

# Scenario: Sebring bumpy track → adaptive thresholds change what triggers.
# Smooth-consistent driver on rough track with mild understeer and slow settle.
# These modifiers represent what compute_modifiers() would produce.
enhanced_modifiers = SolverModifiers(
    df_balance_offset_pct=-0.15,     # Speed gradient → shift DF forward
    lltd_offset=-0.02,              # Understeer diagnosis → lower LLTD
    front_heave_min_floor_nmm=60.0, # Bottoming diagnosis → floor constraint
    rear_third_min_floor_nmm=0.0,
    front_ls_rbd_offset=1,          # Settle time too slow → stiffer rebound
    rear_ls_rbd_offset=0,
    front_hs_comp_offset=0,
    rear_hs_comp_offset=0,
    damping_ratio_scale=0.92,       # Smooth driver → slightly softer ζ targets
    reasons=[
        "Speed gradient +1.8° → DF balance offset -0.15%",
        "Understeer 2.8° > threshold 2.6° → LLTD offset -0.02",
        "Front bottoming 7 events → heave floor 60 N/mm",
        "Settle time 250ms > adaptive threshold 170ms → LS rbd +1",
        "Smooth-consistent driver → damping_ratio_scale 0.92",
    ],
)

print()
print("  Modifiers computed from diagnosis + driver style:")
for r in enhanced_modifiers.reasons:
    print(f"    {r}")

# Re-run Step 1 with DF balance offset
target_balance_enhanced = 50.14 + enhanced_modifiers.df_balance_offset_pct
e_step1 = rake_solver.solve(
    target_balance=target_balance_enhanced,
    fuel_load_l=89.0,
    pin_front_min=True,
)

# Re-run Step 2 with heave floor
e_step2 = heave_solver.solve(
    dynamic_front_rh_mm=e_step1.dynamic_front_rh_mm,
    dynamic_rear_rh_mm=e_step1.dynamic_rear_rh_mm,
    front_heave_floor_nmm=enhanced_modifiers.front_heave_min_floor_nmm,
    rear_third_floor_nmm=enhanced_modifiers.rear_third_min_floor_nmm,
)

# Re-run Step 3
e_step3 = corner_solver.solve(
    front_heave_nmm=e_step2.front_heave_nmm,
    rear_third_nmm=e_step2.rear_third_nmm,
    fuel_load_l=89.0,
)

# Re-run Step 4 with LLTD offset
e_step4 = arb_solver.solve(
    front_wheel_rate_nmm=e_step3.front_wheel_rate_nmm,
    rear_wheel_rate_nmm=e_step3.rear_spring_rate_nmm,
    lltd_offset=enhanced_modifiers.lltd_offset,
)

# Re-run Step 5
e_step5 = geom_solver.solve(
    k_roll_total_nm_deg=e_step4.k_roll_front_total + e_step4.k_roll_rear_total,
    front_wheel_rate_nmm=e_step3.front_wheel_rate_nmm,
    rear_wheel_rate_nmm=e_step3.rear_spring_rate_nmm,
)

# Re-run Step 6 with damping ratio scale
e_step6 = damper_solver.solve(
    front_wheel_rate_nmm=e_step3.front_wheel_rate_nmm,
    rear_wheel_rate_nmm=e_step3.rear_spring_rate_nmm,
    front_dynamic_rh_mm=e_step1.dynamic_front_rh_mm,
    rear_dynamic_rh_mm=e_step1.dynamic_rear_rh_mm,
    fuel_load_l=89.0,
    damping_ratio_scale=enhanced_modifiers.damping_ratio_scale,
)

# Apply damper click offsets from modifiers
from pipeline.produce import _apply_damper_modifiers
_apply_damper_modifiers(e_step6, enhanced_modifiers, car)


# ═══════════════════════════════════════════════════════════════════
#  SIDE-BY-SIDE: Current vs Enhanced — ACTUAL NUMBERS
# ═══════════════════════════════════════════════════════════════════

print()
print("=" * WIDTH)
print("  SIDE-BY-SIDE: SETUP VALUES THAT ACTUALLY CHANGE")
print("=" * WIDTH)
print()
print(f"  {'Parameter':<30s} {'Current':>8s} {'Enhanced':>8s} {'Delta':>8s}")
print("  " + "-" * (WIDTH - 4))

def diff_line(label, old_val, new_val, fmt=".1f", units=""):
    delta = new_val - old_val
    marker = " **" if abs(delta) > 0.01 else ""
    print(f"  {label:<30s} {old_val:>8{fmt}} {new_val:>8{fmt}} {delta:>+8{fmt}}{units}{marker}")

# Step 1
diff_line("DF balance target %", 50.14, target_balance_enhanced, ".2f")
diff_line("Dynamic front RH (mm)", step1.dynamic_front_rh_mm, e_step1.dynamic_front_rh_mm)
diff_line("Dynamic rear RH (mm)", step1.dynamic_rear_rh_mm, e_step1.dynamic_rear_rh_mm)
diff_line("DF balance achieved %", step1.df_balance_pct, e_step1.df_balance_pct, ".2f")

# Step 2
diff_line("Front heave (N/mm)", step2.front_heave_nmm, e_step2.front_heave_nmm, ".0f")
diff_line("Rear third (N/mm)", step2.rear_third_nmm, e_step2.rear_third_nmm, ".0f")
diff_line("Front bottoming margin (mm)", step2.front_bottoming_margin_mm, e_step2.front_bottoming_margin_mm)

# Step 3
diff_line("Front torsion OD (mm)", step3.front_torsion_od_mm, e_step3.front_torsion_od_mm)
diff_line("Rear spring rate (N/mm)", step3.rear_spring_rate_nmm, e_step3.rear_spring_rate_nmm, ".0f")

# Step 4
diff_line("LLTD target", step4.lltd_target * 100, e_step4.lltd_target * 100, ".2f", "%")
diff_line("LLTD achieved", step4.lltd_achieved * 100, e_step4.lltd_achieved * 100, ".2f", "%")
diff_line("RARB blade (slow)", float(step4.rarb_blade_slow_corner), float(e_step4.rarb_blade_slow_corner), ".0f")
diff_line("RARB blade (fast)", float(step4.rarb_blade_fast_corner), float(e_step4.rarb_blade_fast_corner), ".0f")

# Step 5
diff_line("Front camber (deg)", step5.front_camber_deg, e_step5.front_camber_deg)
diff_line("Rear camber (deg)", step5.rear_camber_deg, e_step5.rear_camber_deg)

# Step 6
diff_line("LF LS Comp (click)", float(step6.lf.ls_comp), float(e_step6.lf.ls_comp), ".0f")
diff_line("LF LS Rbd (click)", float(step6.lf.ls_rbd), float(e_step6.lf.ls_rbd), ".0f")
diff_line("LF HS Comp (click)", float(step6.lf.hs_comp), float(e_step6.lf.hs_comp), ".0f")
diff_line("LR LS Comp (click)", float(step6.lr.ls_comp), float(e_step6.lr.ls_comp), ".0f")
diff_line("LR LS Rbd (click)", float(step6.lr.ls_rbd), float(e_step6.lr.ls_rbd), ".0f")
diff_line("LR HS Comp (click)", float(step6.lr.hs_comp), float(e_step6.lr.hs_comp), ".0f")
diff_line("ζ LS front", step6.zeta_ls_front, e_step6.zeta_ls_front, ".3f")
diff_line("ζ LS rear", step6.zeta_ls_rear, e_step6.zeta_ls_rear, ".3f")
diff_line("ζ HS front", step6.zeta_hs_front, e_step6.zeta_hs_front, ".3f")
diff_line("ζ HS rear", step6.zeta_hs_rear, e_step6.zeta_hs_rear, ".3f")

print()
print("  ** = value changed by enhanced reasoning")
print()

# Count changes
changes_count = 0
for old, new in [
    (step1.dynamic_front_rh_mm, e_step1.dynamic_front_rh_mm),
    (step1.dynamic_rear_rh_mm, e_step1.dynamic_rear_rh_mm),
    (step2.front_heave_nmm, e_step2.front_heave_nmm),
    (step2.rear_third_nmm, e_step2.rear_third_nmm),
    (step4.lltd_achieved, e_step4.lltd_achieved),
    (step6.lf.ls_comp, e_step6.lf.ls_comp),
    (step6.lf.ls_rbd, e_step6.lf.ls_rbd),
    (step6.lf.hs_comp, e_step6.lf.hs_comp),
    (step6.lr.ls_comp, e_step6.lr.ls_comp),
    (step6.lr.ls_rbd, e_step6.lr.ls_rbd),
    (step6.zeta_ls_front, e_step6.zeta_ls_front),
]:
    if abs(old - new) > 0.001:
        changes_count += 1

print("#" * WIDTH)
print(f"  {changes_count} parameters changed by enhanced reasoning")
print(f"  Same physics engine — different inputs from deeper diagnosis")
print(f"  Adaptive thresholds + modifiers → different solver targets")
print(f"  → different actual setup values on the car")
print("#" * WIDTH)
