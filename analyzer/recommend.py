"""Recommendation engine -- compute specific parameter changes from physics.

For each diagnosed Problem, compute the exact garage parameter change
using car model physics. Every recommendation has:
- The specific parameter and its current/recommended values
- Physics reasoning (WHY this change fixes the problem)
- Expected effect on the symptom
- Confidence level based on how well the physics model is calibrated

All values are clamped to the car's valid range and snapped to garage
step sizes.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

from analyzer.diagnose import Diagnosis, Problem
from analyzer.setup_reader import CurrentSetup
from car_model.cars import CarModel


@dataclass
class SetupChange:
    """A single recommended parameter change."""
    parameter: str           # garage parameter name
    current: float           # current value
    recommended: float       # recommended value
    units: str
    step: int               # solver step area (1-6)
    reasoning: str          # physics WHY
    effect: str             # expected improvement
    priority: int           # 0=safety ... 5=optimization
    confidence: str         # "high" | "medium" | "low"


@dataclass
class AnalysisResult:
    """Complete analysis output."""
    diagnosis: Diagnosis
    changes: list[SetupChange] = field(default_factory=list)
    current_setup: CurrentSetup = field(default_factory=lambda: CurrentSetup(source="none"))
    improved_setup: CurrentSetup = field(default_factory=lambda: CurrentSetup(source="none"))


def recommend(
    diagnosis: Diagnosis,
    setup: CurrentSetup,
    car: CarModel,
) -> AnalysisResult:
    """Compute specific parameter changes for each diagnosed problem.

    Args:
        diagnosis: Handling diagnosis from diagnose.py
        setup: Current garage setup
        car: Car physical model

    Returns:
        AnalysisResult with ordered changes and improved setup
    """
    changes: list[SetupChange] = []
    improved = copy.deepcopy(setup)
    improved.source = "analyzer"

    for problem in diagnosis.problems:
        new_changes = _recommend_for_problem(problem, setup, improved, car)
        changes.extend(new_changes)

    # Deduplicate: if the same parameter appears multiple times, keep
    # the highest-priority (lowest number) change
    seen: dict[str, SetupChange] = {}
    for ch in changes:
        if ch.parameter not in seen or ch.priority < seen[ch.parameter].priority:
            seen[ch.parameter] = ch
    changes = sorted(seen.values(), key=lambda c: (c.priority, c.parameter))

    # Apply all changes to the improved setup
    for ch in changes:
        _apply_change(improved, ch)

    return AnalysisResult(
        diagnosis=diagnosis,
        changes=changes,
        current_setup=setup,
        improved_setup=improved,
    )


def _recommend_for_problem(
    problem: Problem,
    current: CurrentSetup,
    improved: CurrentSetup,
    car: CarModel,
) -> list[SetupChange]:
    """Generate recommendations for a single problem."""

    if problem.category == "safety":
        return _recommend_safety(problem, current, car)
    elif problem.category == "platform":
        return _recommend_platform(problem, current, car)
    elif problem.category == "balance":
        return _recommend_balance(problem, current, car)
    elif problem.category == "damper":
        return _recommend_damper(problem, current, car)
    elif problem.category == "thermal":
        return _recommend_thermal(problem, current, car)
    elif problem.category == "grip":
        return _recommend_grip(problem, current, car)
    return []


# ── Safety recommendations ──────────────────────────────────────────────

def _recommend_safety(
    problem: Problem, setup: CurrentSetup, car: CarModel,
) -> list[SetupChange]:
    """Safety problems -> heave/third spring or ride height changes."""
    changes = []

    if "vortex burst" in problem.symptom.lower():
        # Stiffen front heave to reduce excursion
        hs = car.heave_spring
        # Target: reduce excursion by 30% -> need k_new = k_old * 1.3^2 = 1.7 * k_old
        k_current = setup.front_heave_nmm
        k_target = k_current * 1.7
        k_target = max(hs.front_spring_range_nmm[0],
                       min(hs.front_spring_range_nmm[1], round(k_target)))

        if k_target != k_current:
            changes.append(SetupChange(
                parameter="front_heave_nmm",
                current=k_current,
                recommended=k_target,
                units="N/mm",
                step=2,
                reasoning=(
                    f"Vortex burst events require significantly stiffer front heave. "
                    f"Excursion scales as 1/sqrt(k). Increasing from {k_current:.0f} to "
                    f"{k_target:.0f} N/mm reduces p99 excursion by ~30%."
                ),
                effect="Eliminate vortex burst events by keeping front RH above critical threshold.",
                priority=0,
                confidence="high",
            ))

    elif "front bottoming" in problem.symptom.lower():
        hs = car.heave_spring
        k_current = setup.front_heave_nmm
        # Stiffen by 40% for bottoming
        k_target = k_current * 1.4
        k_target = max(hs.front_spring_range_nmm[0],
                       min(hs.front_spring_range_nmm[1], round(k_target)))

        if k_target != k_current:
            changes.append(SetupChange(
                parameter="front_heave_nmm",
                current=k_current,
                recommended=k_target,
                units="N/mm",
                step=2,
                reasoning=(
                    f"Front bottoming events need stiffer heave spring. "
                    f"excursion ~ 1/sqrt(k). {k_current:.0f} -> {k_target:.0f} N/mm "
                    f"reduces peak excursion by ~16%."
                ),
                effect="Reduce front bottoming events below safety threshold.",
                priority=0,
                confidence="high",
            ))

    elif "rear bottoming" in problem.symptom.lower():
        hs = car.heave_spring
        k_current = setup.rear_third_nmm
        k_target = k_current * 1.4
        k_target = max(hs.rear_spring_range_nmm[0],
                       min(hs.rear_spring_range_nmm[1], round(k_target)))

        if k_target != k_current:
            changes.append(SetupChange(
                parameter="rear_third_nmm",
                current=k_current,
                recommended=k_target,
                units="N/mm",
                step=2,
                reasoning=(
                    f"Rear bottoming events need stiffer third spring. "
                    f"{k_current:.0f} -> {k_target:.0f} N/mm "
                    f"reduces peak excursion by ~16%."
                ),
                effect="Reduce rear bottoming events below safety threshold.",
                priority=0,
                confidence="high",
            ))

    return changes


# ── Platform recommendations ────────────────────────────────────────────

def _recommend_platform(
    problem: Problem, setup: CurrentSetup, car: CarModel,
) -> list[SetupChange]:
    """Platform problems -> heave/third spring stiffness."""
    changes = []
    hs = car.heave_spring

    if "front rh variance" in problem.symptom.lower():
        k_current = setup.front_heave_nmm
        # Target sigma = 8.0mm. Current sigma = problem.measured.
        # sigma ~ 1/sqrt(k), so k_target = k_current * (sigma_current / sigma_target)^2
        sigma_target = 8.0
        sigma_current = problem.measured
        if sigma_current > 0 and k_current > 0:
            k_target = k_current * (sigma_current / sigma_target) ** 2
            k_target = max(hs.front_spring_range_nmm[0],
                           min(hs.front_spring_range_nmm[1], round(k_target)))

            if k_target > k_current:
                changes.append(SetupChange(
                    parameter="front_heave_nmm",
                    current=k_current,
                    recommended=k_target,
                    units="N/mm",
                    step=2,
                    reasoning=(
                        f"Front RH sigma = {sigma_current:.1f}mm, target <{sigma_target:.0f}mm. "
                        f"sigma ~ 1/sqrt(k). Need k = {k_current:.0f} * "
                        f"({sigma_current:.1f}/{sigma_target:.0f})^2 = {k_target:.0f} N/mm."
                    ),
                    effect=f"Reduce front RH variance from {sigma_current:.1f}mm to ~{sigma_target:.0f}mm.",
                    priority=1,
                    confidence="high",
                ))

    elif "rear rh variance" in problem.symptom.lower():
        k_current = setup.rear_third_nmm
        sigma_target = 10.0
        sigma_current = problem.measured
        if sigma_current > 0 and k_current > 0:
            k_target = k_current * (sigma_current / sigma_target) ** 2
            k_target = max(hs.rear_spring_range_nmm[0],
                           min(hs.rear_spring_range_nmm[1], round(k_target)))

            if k_target > k_current:
                changes.append(SetupChange(
                    parameter="rear_third_nmm",
                    current=k_current,
                    recommended=k_target,
                    units="N/mm",
                    step=2,
                    reasoning=(
                        f"Rear RH sigma = {sigma_current:.1f}mm, target <{sigma_target:.0f}mm. "
                        f"sigma ~ 1/sqrt(k). Need k = {k_current:.0f} * "
                        f"({sigma_current:.1f}/{sigma_target:.0f})^2 = {k_target:.0f} N/mm."
                    ),
                    effect=f"Reduce rear RH variance from {sigma_current:.1f}mm to ~{sigma_target:.0f}mm.",
                    priority=1,
                    confidence="high",
                ))

    elif "excursion" in problem.symptom.lower():
        # Excursion near bottoming -> stiffen heave
        k_current = setup.front_heave_nmm
        k_target = k_current * 1.3  # 30% stiffer
        k_target = max(hs.front_spring_range_nmm[0],
                       min(hs.front_spring_range_nmm[1], round(k_target)))

        if k_target > k_current:
            changes.append(SetupChange(
                parameter="front_heave_nmm",
                current=k_current,
                recommended=k_target,
                units="N/mm",
                step=2,
                reasoning=(
                    "Front excursion p99 is dangerously close to bottoming. "
                    "30% stiffer heave spring reduces excursion by ~14%."
                ),
                effect="Increase margin to bottoming for safety over kerbs and dirty air.",
                priority=1,
                confidence="medium",
            ))

    return changes


# ── Balance recommendations ─────────────────────────────────────────────

def _recommend_balance(
    problem: Problem, setup: CurrentSetup, car: CarModel,
) -> list[SetupChange]:
    """Balance problems -> ARB, aero balance, diff."""
    changes = []
    arb = car.arb

    if "understeer" in problem.symptom.lower() and problem.measured > 0:
        # Car pushing -> multiple approaches depending on speed context
        if problem.speed_context == "high":
            # High-speed understeer -> aero: increase front DF balance
            # Lower front RH gives more front DF (if not already at minimum)
            pass  # Aero changes need aero map queries; flag for manual review
        else:
            # All-speed or low-speed understeer -> soften rear ARB
            current_blade = setup.rear_arb_blade
            if current_blade > 1:
                new_blade = current_blade - 1
                changes.append(SetupChange(
                    parameter="rear_arb_blade",
                    current=float(current_blade),
                    recommended=float(new_blade),
                    units="clicks",
                    step=4,
                    reasoning=(
                        f"Understeer {problem.measured:+.1f} deg. Softening rear ARB "
                        f"from blade {current_blade} to {new_blade} reduces rear roll "
                        f"stiffness, shifting LLTD toward rear (less front load transfer)."
                    ),
                    effect="Reduce understeer by shifting lateral load transfer rearward.",
                    priority=2,
                    confidence="high",
                ))

    elif "loose" in problem.symptom.lower() or (
        "understeer" in problem.symptom.lower() and problem.measured < 0
    ):
        # Car oversteering -> stiffen rear ARB
        current_blade = setup.rear_arb_blade
        max_blade = arb.rear_blade_count
        if current_blade < max_blade:
            new_blade = current_blade + 1
            changes.append(SetupChange(
                parameter="rear_arb_blade",
                current=float(current_blade),
                recommended=float(new_blade),
                units="clicks",
                step=4,
                reasoning=(
                    f"Oversteer {problem.measured:+.1f} deg. Stiffening rear ARB "
                    f"from blade {current_blade} to {new_blade} increases rear roll "
                    f"stiffness, shifting LLTD toward front (more front load transfer)."
                ),
                effect="Reduce oversteer by shifting lateral load transfer forward.",
                priority=2,
                confidence="high",
            ))

    elif "speed gradient" in problem.symptom.lower():
        gradient = problem.measured
        if gradient > 0:
            # More understeer at speed -> front has too much load at speed
            # Need more front DF balance (aero tweak, not mechanical)
            changes.append(SetupChange(
                parameter="rear_rh_at_speed_mm",
                current=setup.rear_rh_at_speed_mm,
                recommended=setup.rear_rh_at_speed_mm + 2.0,
                units="mm",
                step=1,
                reasoning=(
                    f"Speed gradient +{gradient:.1f} deg means more understeer at "
                    f"high speed. Aero balance is too rear-biased. Raising rear "
                    f"dynamic RH by 2mm shifts DF balance forward ~1%."
                ),
                effect="Reduce high-speed understeer while preserving low-speed balance.",
                priority=2,
                confidence="medium",
            ))
        else:
            # More oversteer at speed -> need less front DF balance
            changes.append(SetupChange(
                parameter="rear_rh_at_speed_mm",
                current=setup.rear_rh_at_speed_mm,
                recommended=max(25.0, setup.rear_rh_at_speed_mm - 2.0),
                units="mm",
                step=1,
                reasoning=(
                    f"Speed gradient {gradient:+.1f} deg means more oversteer at "
                    f"high speed. Need more rear DF. Lowering rear dynamic RH "
                    f"by 2mm increases rear downforce."
                ),
                effect="Reduce high-speed oversteer by increasing rear downforce.",
                priority=2,
                confidence="medium",
            ))

    elif "lltd" in problem.symptom.lower():
        if "too high" in problem.cause.lower():
            # LLTD too high -> soften front ARB or stiffen rear
            current_blade = setup.front_arb_blade
            if current_blade > 1:
                new_blade = current_blade - 1
                changes.append(SetupChange(
                    parameter="front_arb_blade",
                    current=float(current_blade),
                    recommended=float(new_blade),
                    units="clicks",
                    step=4,
                    reasoning=(
                        f"LLTD too high ({problem.measured:.1f}%). Softening front ARB "
                        f"from blade {current_blade} to {new_blade} reduces front roll "
                        f"stiffness contribution."
                    ),
                    effect="Lower LLTD toward target for better balance.",
                    priority=2,
                    confidence="high",
                ))
            else:
                # Front ARB already at minimum blade -> stiffen rear
                current_rear_blade = setup.rear_arb_blade
                max_blade = arb.rear_blade_count
                if current_rear_blade < max_blade:
                    new_blade = current_rear_blade + 1
                    changes.append(SetupChange(
                        parameter="rear_arb_blade",
                        current=float(current_rear_blade),
                        recommended=float(new_blade),
                        units="clicks",
                        step=4,
                        reasoning=(
                            f"LLTD too high ({problem.measured:.1f}%) and front ARB "
                            f"already at minimum. Stiffening rear ARB from blade "
                            f"{current_rear_blade} to {new_blade} increases rear "
                            f"roll stiffness, lowering LLTD."
                        ),
                        effect="Lower LLTD toward target by increasing rear roll stiffness.",
                        priority=2,
                        confidence="high",
                    ))

        elif "too low" in problem.cause.lower():
            # LLTD too low -> stiffen front ARB or soften rear
            current_blade = setup.front_arb_blade
            max_blade = arb.front_blade_count
            if current_blade < max_blade:
                new_blade = current_blade + 1
                changes.append(SetupChange(
                    parameter="front_arb_blade",
                    current=float(current_blade),
                    recommended=float(new_blade),
                    units="clicks",
                    step=4,
                    reasoning=(
                        f"LLTD too low ({problem.measured:.1f}%). Stiffening front ARB "
                        f"from blade {current_blade} to {new_blade} increases front "
                        f"roll stiffness contribution."
                    ),
                    effect="Raise LLTD toward target to avoid snap oversteer.",
                    priority=2,
                    confidence="high",
                ))

    elif "body slip" in problem.symptom.lower():
        # High body slip = rear instability. Increase diff preload.
        current_preload = setup.diff_preload_nm
        new_preload = min(current_preload + 5.0, 40.0)  # +5 Nm, cap at 40
        if new_preload > current_preload:
            changes.append(SetupChange(
                parameter="diff_preload_nm",
                current=current_preload,
                recommended=new_preload,
                units="Nm",
                step=4,
                reasoning=(
                    f"Body slip angle {problem.measured:.1f} deg is high. "
                    f"Increasing diff preload from {current_preload:.0f} to "
                    f"{new_preload:.0f} Nm locks the diff more, stabilizing "
                    f"the rear under power."
                ),
                effect="Reduce rear-axle slip and body yaw angle.",
                priority=2,
                confidence="medium",
            ))

    return changes


# ── Damper recommendations ──────────────────────────────────────────────

def _recommend_damper(
    problem: Problem, setup: CurrentSetup, car: CarModel,
) -> list[SetupChange]:
    """Damper problems -> click changes."""
    changes = []
    dm = car.damper

    if "front settle time" in problem.symptom.lower():
        if problem.measured > 200:
            # Underdamped -> increase LS rebound
            current = setup.front_ls_rbd
            new_val = dm.snap_click(current + 1, "ls_rbd")
            if new_val != current:
                changes.append(SetupChange(
                    parameter="front_ls_rbd",
                    current=float(current),
                    recommended=float(new_val),
                    units="clicks",
                    step=6,
                    reasoning=(
                        f"Front settle time {problem.measured:.0f}ms (target <200ms). "
                        f"Increase LS rebound from {current} to {new_val} clicks "
                        f"to damp oscillation faster."
                    ),
                    effect="Faster platform recovery after bumps.",
                    priority=3,
                    confidence="medium",
                ))
        elif problem.measured < 50:
            # Overdamped -> decrease LS rebound
            current = setup.front_ls_rbd
            new_val = dm.snap_click(current - 1, "ls_rbd")
            if new_val != current:
                changes.append(SetupChange(
                    parameter="front_ls_rbd",
                    current=float(current),
                    recommended=float(new_val),
                    units="clicks",
                    step=6,
                    reasoning=(
                        f"Front settle time {problem.measured:.0f}ms (too fast, <50ms). "
                        f"Decrease LS rebound from {current} to {new_val} clicks "
                        f"for more compliance."
                    ),
                    effect="Better tyre contact over bumps at cost of slightly slower settle.",
                    priority=3,
                    confidence="low",
                ))

    elif "rear settle time" in problem.symptom.lower():
        if problem.measured > 200:
            current = setup.rear_ls_rbd
            new_val = dm.snap_click(current + 1, "ls_rbd")
            if new_val != current:
                changes.append(SetupChange(
                    parameter="rear_ls_rbd",
                    current=float(current),
                    recommended=float(new_val),
                    units="clicks",
                    step=6,
                    reasoning=(
                        f"Rear settle time {problem.measured:.0f}ms (target <200ms). "
                        f"Increase LS rebound from {current} to {new_val} clicks."
                    ),
                    effect="Faster rear platform recovery.",
                    priority=3,
                    confidence="medium",
                ))
        elif problem.measured < 50:
            current = setup.rear_ls_rbd
            new_val = dm.snap_click(current - 1, "ls_rbd")
            if new_val != current:
                changes.append(SetupChange(
                    parameter="rear_ls_rbd",
                    current=float(current),
                    recommended=float(new_val),
                    units="clicks",
                    step=6,
                    reasoning=(
                        f"Rear settle time {problem.measured:.0f}ms (too fast, <50ms). "
                        f"Decrease LS rebound from {current} to {new_val} clicks."
                    ),
                    effect="Better rear tyre contact over bumps.",
                    priority=3,
                    confidence="low",
                ))

    elif "yaw rate" in problem.symptom.lower():
        # Poor yaw correlation -> increase LS rebound (both axles)
        for axle, attr in [("front", "front_ls_rbd"), ("rear", "rear_ls_rbd")]:
            current = getattr(setup, attr)
            new_val = dm.snap_click(current + 1, "ls_rbd")
            if new_val != current:
                changes.append(SetupChange(
                    parameter=attr,
                    current=float(current),
                    recommended=float(new_val),
                    units="clicks",
                    step=6,
                    reasoning=(
                        f"Yaw correlation R^2={problem.measured:.3f} (target >0.65). "
                        f"Increase {axle} LS rebound from {current} to {new_val} "
                        f"to tighten transient response."
                    ),
                    effect="More predictable yaw response to steering input.",
                    priority=3,
                    confidence="low",
                ))

    elif "roll rate" in problem.symptom.lower():
        # Excessive roll rate -> increase LS rebound both axles
        for axle, attr in [("front", "front_ls_rbd"), ("rear", "rear_ls_rbd")]:
            current = getattr(setup, attr)
            new_val = dm.snap_click(current + 2, "ls_rbd")
            if new_val != current:
                changes.append(SetupChange(
                    parameter=attr,
                    current=float(current),
                    recommended=float(new_val),
                    units="clicks",
                    step=6,
                    reasoning=(
                        f"Roll rate p95={problem.measured:.1f} deg/s (target <25). "
                        f"Increase {axle} LS rebound from {current} to {new_val} clicks "
                        f"to control weight transfer rate."
                    ),
                    effect="Slower, more controlled weight transfer in transitions.",
                    priority=3,
                    confidence="medium",
                ))

    return changes


# ── Thermal recommendations ─────────────────────────────────────────────

def _recommend_thermal(
    problem: Problem, setup: CurrentSetup, car: CarModel,
) -> list[SetupChange]:
    """Thermal problems -> camber, toe, pressure adjustments."""
    changes = []
    geo = car.geometry

    if "inner hot" in problem.symptom.lower():
        # Too much negative camber -> reduce magnitude
        corner = problem.symptom[:2]  # "LF", "RF", "LR", "RR"
        axle = "front" if corner[1] == "F" else "rear"
        spread = abs(problem.measured)
        camber_correction = spread / 20.0  # ~0.1 deg per 2C spread

        if axle == "front":
            current = setup.front_camber_deg
            # Reduce magnitude = move toward 0
            new_val = current + camber_correction  # less negative
            new_val = round(new_val / geo.front_camber_step_deg) * geo.front_camber_step_deg
            new_val = max(geo.front_camber_range_deg[0],
                         min(geo.front_camber_range_deg[1], new_val))
            new_val = round(new_val, 1)
            if new_val != current:
                changes.append(SetupChange(
                    parameter="front_camber_deg",
                    current=current,
                    recommended=new_val,
                    units="deg",
                    step=5,
                    reasoning=(
                        f"{corner} inner-outer spread {problem.measured:+.1f}C. "
                        f"Inner overheating -> too much negative camber. "
                        f"Reduce from {current:.1f} to {new_val:.1f} deg."
                    ),
                    effect=f"Even out {corner} temperature spread, extend tyre life.",
                    priority=4,
                    confidence="high",
                ))
        else:
            current = setup.rear_camber_deg
            new_val = current + camber_correction
            new_val = round(new_val / geo.rear_camber_step_deg) * geo.rear_camber_step_deg
            new_val = max(geo.rear_camber_range_deg[0],
                         min(geo.rear_camber_range_deg[1], new_val))
            new_val = round(new_val, 1)
            if new_val != current:
                changes.append(SetupChange(
                    parameter="rear_camber_deg",
                    current=current,
                    recommended=new_val,
                    units="deg",
                    step=5,
                    reasoning=(
                        f"{corner} inner-outer spread {problem.measured:+.1f}C. "
                        f"Inner overheating -> too much negative camber. "
                        f"Reduce from {current:.1f} to {new_val:.1f} deg."
                    ),
                    effect=f"Even out {corner} temperature spread, extend tyre life.",
                    priority=4,
                    confidence="high",
                ))

    elif "outer hot" in problem.symptom.lower():
        # Not enough negative camber -> increase magnitude
        corner = problem.symptom[:2]
        axle = "front" if corner[1] == "F" else "rear"
        spread = abs(problem.measured)
        camber_correction = spread / 20.0

        if axle == "front":
            current = setup.front_camber_deg
            new_val = current - camber_correction  # more negative
            new_val = round(new_val / geo.front_camber_step_deg) * geo.front_camber_step_deg
            new_val = max(geo.front_camber_range_deg[0],
                         min(geo.front_camber_range_deg[1], new_val))
            new_val = round(new_val, 1)
            if new_val != current:
                changes.append(SetupChange(
                    parameter="front_camber_deg",
                    current=current,
                    recommended=new_val,
                    units="deg",
                    step=5,
                    reasoning=(
                        f"{corner} inner-outer spread {problem.measured:+.1f}C. "
                        f"Outer overheating -> not enough negative camber. "
                        f"Increase from {current:.1f} to {new_val:.1f} deg."
                    ),
                    effect=f"Even out {corner} temperature spread, improve peak grip.",
                    priority=4,
                    confidence="high",
                ))
        else:
            current = setup.rear_camber_deg
            new_val = current - camber_correction
            new_val = round(new_val / geo.rear_camber_step_deg) * geo.rear_camber_step_deg
            new_val = max(geo.rear_camber_range_deg[0],
                         min(geo.rear_camber_range_deg[1], new_val))
            new_val = round(new_val, 1)
            if new_val != current:
                changes.append(SetupChange(
                    parameter="rear_camber_deg",
                    current=current,
                    recommended=new_val,
                    units="deg",
                    step=5,
                    reasoning=(
                        f"{corner} inner-outer spread {problem.measured:+.1f}C. "
                        f"Outer overheating -> not enough negative camber. "
                        f"Increase from {current:.1f} to {new_val:.1f} deg."
                    ),
                    effect=f"Even out {corner} temperature spread.",
                    priority=4,
                    confidence="high",
                ))

    elif "carcass" in problem.symptom.lower() and problem.measured < 80:
        # Cold tyres -> increase toe for scrub heat
        axle = "front" if "front" in problem.symptom.lower() else "rear"
        if axle == "front":
            current = setup.front_toe_mm
            # More toe-out (more negative) for more scrub heat
            new_val = current - 0.2
            new_val = max(geo.front_toe_range_mm[0],
                         min(geo.front_toe_range_mm[1], round(new_val, 1)))
            if new_val != current:
                changes.append(SetupChange(
                    parameter="front_toe_mm",
                    current=current,
                    recommended=new_val,
                    units="mm",
                    step=5,
                    reasoning=(
                        f"Front carcass {problem.measured:.0f}C (target >80C). "
                        f"More toe-out ({current:.1f} -> {new_val:.1f}mm) generates "
                        f"scrub heating to bring tyres into window faster."
                    ),
                    effect="Faster tyre warm-up, higher grip sooner.",
                    priority=4,
                    confidence="medium",
                ))
        else:
            current = setup.rear_toe_mm
            new_val = current + 0.2  # More toe-in at rear = more scrub heat
            new_val = max(geo.rear_toe_range_mm[0],
                         min(geo.rear_toe_range_mm[1], round(new_val, 1)))
            if new_val != current:
                changes.append(SetupChange(
                    parameter="rear_toe_mm",
                    current=current,
                    recommended=new_val,
                    units="mm",
                    step=5,
                    reasoning=(
                        f"Rear carcass {problem.measured:.0f}C (target >80C). "
                        f"More toe-in ({current:.1f} -> {new_val:.1f}mm) generates "
                        f"scrub heating to bring rears into window faster."
                    ),
                    effect="Faster rear tyre warm-up.",
                    priority=4,
                    confidence="medium",
                ))

    return changes


# ── Grip recommendations ────────────────────────────────────────────────

def _recommend_grip(
    problem: Problem, setup: CurrentSetup, car: CarModel,
) -> list[SetupChange]:
    """Grip problems -> TC, diff, brake bias."""
    changes = []

    if "rear traction slip" in problem.symptom.lower():
        # High rear slip -> lower TC slip
        current_tc = setup.tc_slip
        if current_tc > 1:
            new_val = current_tc - 1
            changes.append(SetupChange(
                parameter="tc_slip",
                current=float(current_tc),
                recommended=float(new_val),
                units="setting",
                step=4,
                reasoning=(
                    f"Rear traction slip p95={problem.measured:.3f} (target <0.08). "
                    f"Lower TC slip from {current_tc} to {new_val} to reduce "
                    f"wheelspin under power."
                ),
                effect="Less rear wheelspin, better traction out of corners.",
                priority=5,
                confidence="high",
            ))

    elif "front braking slip" in problem.symptom.lower():
        # Front locking -> shift bias rearward
        current_bias = setup.brake_bias_pct
        new_bias = current_bias - 0.5  # -0.5% rearward
        new_bias = max(40.0, min(60.0, round(new_bias * 2) / 2))  # snap to 0.5%
        if new_bias != current_bias:
            changes.append(SetupChange(
                parameter="brake_bias_pct",
                current=current_bias,
                recommended=new_bias,
                units="%",
                step=4,
                reasoning=(
                    f"Front braking slip p95={problem.measured:.3f} (target <0.06). "
                    f"Shift brake bias from {current_bias:.1f}% to {new_bias:.1f}% "
                    f"(rearward) to reduce front lock-up tendency."
                ),
                effect="Better braking stability, less front lock-up.",
                priority=5,
                confidence="high",
            ))

    return changes


# ── Apply change to setup ───────────────────────────────────────────────

def _apply_change(setup: CurrentSetup, change: SetupChange) -> None:
    """Apply a SetupChange to a CurrentSetup object."""
    attr_map = {
        "front_heave_nmm": "front_heave_nmm",
        "rear_third_nmm": "rear_third_nmm",
        "front_arb_blade": "front_arb_blade",
        "rear_arb_blade": "rear_arb_blade",
        "front_camber_deg": "front_camber_deg",
        "rear_camber_deg": "rear_camber_deg",
        "front_toe_mm": "front_toe_mm",
        "rear_toe_mm": "rear_toe_mm",
        "front_ls_rbd": "front_ls_rbd",
        "rear_ls_rbd": "rear_ls_rbd",
        "brake_bias_pct": "brake_bias_pct",
        "diff_preload_nm": "diff_preload_nm",
        "tc_slip": "tc_slip",
        "rear_rh_at_speed_mm": "rear_rh_at_speed_mm",
    }

    attr = attr_map.get(change.parameter)
    if attr and hasattr(setup, attr):
        val = change.recommended
        # Convert to int for integer fields
        if isinstance(getattr(setup, attr), int):
            val = int(round(val))
        setattr(setup, attr, val)
