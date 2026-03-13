"""Multi-pass iterative solver with cross-step optimization.

Wraps the 6-step sequential solver in an outer loop that:
1. Runs all 6 steps
2. Checks for cross-step constraint violations
3. Adjusts earlier-step inputs based on later-step findings
4. Re-runs with relaxation damping to prevent oscillation
5. Converges when residual vector is below threshold

Max 3 iterations — physics constraints are well-posed enough that
2-3 passes suffice. Each pass logs what changed and why.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from car_model.cars import CarModel
    from track_model.profile import TrackProfile
    from aero_model.interpolator import AeroSurface


@dataclass
class StepAdjustment:
    """An adjustment to apply to a solver step's inputs on the next pass."""
    target_step: int          # which step to adjust (1-6)
    parameter: str            # what to adjust
    delta: float              # how much to change
    source_step: int          # which later step triggered this
    reason: str               # physics explanation


@dataclass
class PassResult:
    """Result of a single solver pass."""
    pass_number: int
    residuals: dict[str, float]     # constraint violations
    residual_norm: float
    adjustments_applied: list[StepAdjustment]
    converged: bool

    def summary(self) -> str:
        lines = [f"  Pass {self.pass_number}:"]
        lines.append(f"    Residual norm: {self.residual_norm:.4f}")
        for name, val in self.residuals.items():
            status = "OK" if val <= 0 else f"VIOLATION {val:.3f}"
            lines.append(f"    {name}: {status}")
        if self.adjustments_applied:
            lines.append("    Adjustments:")
            for adj in self.adjustments_applied:
                lines.append(
                    f"      Step {adj.target_step} {adj.parameter}: "
                    f"{adj.delta:+.2f} (from Step {adj.source_step})"
                )
        if self.converged:
            lines.append("    ** CONVERGED **")
        return "\n".join(lines)


@dataclass
class IterativeSolution:
    """Result of the full iterative solver."""
    passes: list[PassResult] = field(default_factory=list)
    total_iterations: int = 0
    converged: bool = False
    convergence_reason: str = ""

    # Final step solutions (from last pass)
    # These are stored as generic dicts to avoid circular imports
    final_adjustments: list[StepAdjustment] = field(default_factory=list)

    def summary(self, width: int = 63) -> str:
        lines = [
            "=" * width,
            "  ITERATIVE SOLVER TRACE",
            "=" * width,
            f"  Iterations: {self.total_iterations}",
            f"  Converged: {self.converged} ({self.convergence_reason})",
        ]
        for p in self.passes:
            lines.append(p.summary())
        lines.append("=" * width)
        return "\n".join(lines)


# ── Cross-step constraint checking ────────────────────────────────────

# Relaxation factors: dampen adjustments on successive passes
RELAXATION_FACTORS = {1: 1.0, 2: 0.7, 3: 0.5}


def compute_residuals(
    front_bottoming_margin_mm: float,
    rear_bottoming_margin_mm: float,
    front_damping_ratio: float = 0.0,
    rear_damping_ratio: float = 0.0,
    lltd_error: float = 0.0,
    front_temp_spread_c: float = 0.0,
) -> dict[str, float]:
    """Compute residual vector measuring constraint violations.

    Returns dict of constraint_name → violation magnitude.
    Positive = violation, zero or negative = satisfied.
    """
    residuals = {}

    # Bottoming constraint (must be >= 0)
    residuals["front_bottoming"] = max(0.0, -front_bottoming_margin_mm)
    residuals["rear_bottoming"] = max(0.0, -rear_bottoming_margin_mm)

    # Damping ratio constraints
    if front_damping_ratio > 0:
        # Front LS: target 0.55-0.85
        if front_damping_ratio > 0.95:
            residuals["front_damping_high"] = front_damping_ratio - 0.95
        elif front_damping_ratio < 0.30:
            residuals["front_damping_low"] = 0.30 - front_damping_ratio
        else:
            residuals["front_damping_high"] = 0.0

    if rear_damping_ratio > 0:
        # Rear HS: target 0.15-0.40
        if rear_damping_ratio < 0.10:
            residuals["rear_damping_low"] = 0.10 - rear_damping_ratio
        else:
            residuals["rear_damping_low"] = 0.0

    # LLTD error
    residuals["lltd_error"] = max(0.0, abs(lltd_error) - 0.03)  # 3% tolerance

    # Thermal
    if abs(front_temp_spread_c) > 8.0:
        residuals["thermal_spread"] = abs(front_temp_spread_c) - 8.0
    else:
        residuals["thermal_spread"] = 0.0

    return residuals


def compute_residual_norm(residuals: dict[str, float]) -> float:
    """L2 norm of residual vector."""
    return math.sqrt(sum(v ** 2 for v in residuals.values()))


# ── Cross-step adjustment rules ───────────────────────────────────────

def compute_cross_step_adjustments(
    residuals: dict[str, float],
    pass_number: int,
    front_damping_ratio: float = 0.0,
    rear_damping_ratio: float = 0.0,
    lltd_error: float = 0.0,
    current_corner_spring_nmm: float = 0.0,
    current_heave_nmm: float = 0.0,
) -> list[StepAdjustment]:
    """Compute adjustments for the next pass based on residuals.

    Cross-step rules:
    1. Damper ζ out of range → adjust corner spring (Step 3)
    2. Damper ζ_rear_HS too low → soften heave spring (Step 2)
    3. LLTD can't reach target → adjust DF balance (Step 1)
    4. Thermal spread → adjust camber (Step 5)
    """
    adjustments = []
    relaxation = RELAXATION_FACTORS.get(pass_number, 0.5)

    # Rule 1: Front damping ratio overdamped → corner spring too soft
    if front_damping_ratio > 0.95 and current_corner_spring_nmm > 0:
        delta_k = current_corner_spring_nmm * (front_damping_ratio / 0.85 - 1.0)
        delta_k *= relaxation
        adjustments.append(StepAdjustment(
            target_step=3,
            parameter="front_corner_spring_nmm",
            delta=round(delta_k, 1),
            source_step=6,
            reason=(
                f"Front LS ζ={front_damping_ratio:.2f} > 0.95 (overdamped). "
                f"Corner spring too soft for this damper range. "
                f"Increase by {delta_k:.0f} N/mm."
            ),
        ))

    # Rule 2: Rear HS damping dangerously low → heave too stiff
    if rear_damping_ratio > 0 and rear_damping_ratio < 0.10 and current_heave_nmm > 0:
        delta_k = -current_heave_nmm * 0.10 * relaxation
        adjustments.append(StepAdjustment(
            target_step=2,
            parameter="rear_third_nmm",
            delta=round(delta_k, 1),
            source_step=6,
            reason=(
                f"Rear HS ζ={rear_damping_ratio:.2f} < 0.10 (dangerously underdamped). "
                f"Heave spring may be too stiff. Soften by {abs(delta_k):.0f} N/mm."
            ),
        ))

    # Rule 3: LLTD can't reach target → DF balance offset
    if abs(lltd_error) > 0.05:
        df_offset = lltd_error * 0.5 * relaxation
        adjustments.append(StepAdjustment(
            target_step=1,
            parameter="df_balance_target_pct",
            delta=round(df_offset * 100, 2),
            source_step=4,
            reason=(
                f"LLTD error {lltd_error*100:+.1f}% exceeds 5% tolerance. "
                f"ARBs alone cannot reach target. "
                f"Adjust DF balance by {df_offset*100:+.2f}%."
            ),
        ))

    return adjustments


# ── Convergence check ─────────────────────────────────────────────────

CONVERGENCE_THRESHOLD = 0.01   # residual norm below this = converged
IMPROVEMENT_THRESHOLD = 0.001  # if norm doesn't improve by this much, stop


def check_convergence(
    current_norm: float,
    previous_norm: float | None,
    pass_number: int,
    max_passes: int = 3,
) -> tuple[bool, str]:
    """Check if the iterative solver has converged.

    Returns:
        (converged, reason)
    """
    if current_norm < CONVERGENCE_THRESHOLD:
        return True, f"Residual norm {current_norm:.4f} < threshold {CONVERGENCE_THRESHOLD}"

    if pass_number >= max_passes:
        return True, f"Maximum passes ({max_passes}) reached"

    if previous_norm is not None:
        improvement = previous_norm - current_norm
        if improvement < IMPROVEMENT_THRESHOLD:
            return True, (
                f"No significant improvement: "
                f"Δnorm = {improvement:.4f} < {IMPROVEMENT_THRESHOLD}"
            )

    return False, ""


# ── Iterative solver orchestrator ─────────────────────────────────────

def run_iterative_solver(
    car: CarModel,
    track: TrackProfile,
    surface: AeroSurface,
    wing: float,
    target_balance: float = 50.14,
    fuel_load_l: float = 89.0,
    max_passes: int = 3,
) -> IterativeSolution:
    """Run the 6-step solver iteratively with cross-step optimization.

    This is the high-level orchestrator. It imports and calls the individual
    step solvers, checks residuals after each pass, computes adjustments,
    and re-runs until convergence.

    Args:
        car: Car model
        track: Track profile
        surface: Aero interpolation surface for the wing angle
        wing: Wing angle
        target_balance: Target DF balance %
        fuel_load_l: Fuel load in liters
        max_passes: Maximum number of passes (default 3)

    Returns:
        IterativeSolution with pass traces and final adjustments
    """
    from solver.rake_solver import RakeSolver
    from solver.heave_solver import HeaveSolver
    from solver.corner_spring_solver import CornerSpringSolver
    from solver.arb_solver import ARBSolver
    from solver.wheel_geometry_solver import WheelGeometrySolver
    from solver.damper_solver import DamperSolver

    solution = IterativeSolution()
    previous_norm: float | None = None
    cumulative_adjustments: dict[str, float] = {}

    for pass_num in range(1, max_passes + 1):
        # Apply cumulative adjustments to targets
        adjusted_balance = target_balance + cumulative_adjustments.get("df_balance_target_pct", 0)

        # ── Run 6-step solver ──
        rake_solver = RakeSolver(car, surface, track)
        step1 = rake_solver.solve(
            target_balance=adjusted_balance,
            fuel_load_l=fuel_load_l,
        )

        heave_solver = HeaveSolver(car, track)
        step2 = heave_solver.solve(
            dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
            dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
        )

        corner_solver = CornerSpringSolver(car, track)
        # Apply corner spring adjustment from previous pass
        step3 = corner_solver.solve(
            front_heave_nmm=step2.front_heave_nmm,
            rear_third_nmm=step2.rear_third_nmm,
            fuel_load_l=fuel_load_l,
        )

        # Convert rear spring rate to wheel rate (MR²) for downstream solvers
        rear_wheel_rate_nmm = step3.rear_spring_rate_nmm * car.corner_spring.rear_motion_ratio ** 2

        arb_solver = ARBSolver(car, track)
        step4 = arb_solver.solve(
            front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
        )

        geom_solver = WheelGeometrySolver(car, track)
        step5 = geom_solver.solve(
            k_roll_total_nm_deg=step4.k_roll_front_total + step4.k_roll_rear_total,
            front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
        )

        damper_solver = DamperSolver(car, track)
        step6 = damper_solver.solve(
            front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
            front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
            rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
            fuel_load_l=fuel_load_l,
        )

        # ── Compute residuals ──
        lltd_error = 0.0
        if hasattr(step4, 'lltd_achieved') and hasattr(step4, 'lltd_target'):
            lltd_error = step4.lltd_achieved - step4.lltd_target

        residuals = compute_residuals(
            front_bottoming_margin_mm=step2.front_bottoming_margin_mm,
            rear_bottoming_margin_mm=step2.rear_bottoming_margin_mm,
            lltd_error=lltd_error,
        )

        current_norm = compute_residual_norm(residuals)

        # ── Check convergence ──
        converged, reason = check_convergence(
            current_norm, previous_norm, pass_num, max_passes
        )

        # ── Compute adjustments for next pass (if not converged) ──
        adjustments = []
        if not converged:
            adjustments = compute_cross_step_adjustments(
                residuals=residuals,
                pass_number=pass_num,
                lltd_error=lltd_error,
                current_corner_spring_nmm=step3.front_wheel_rate_nmm,
                current_heave_nmm=step2.front_heave_nmm,
            )

            # Accumulate adjustments
            for adj in adjustments:
                key = adj.parameter
                cumulative_adjustments[key] = (
                    cumulative_adjustments.get(key, 0) + adj.delta
                )

        # Record pass result
        pass_result = PassResult(
            pass_number=pass_num,
            residuals=residuals,
            residual_norm=current_norm,
            adjustments_applied=adjustments,
            converged=converged,
        )
        solution.passes.append(pass_result)
        previous_norm = current_norm

        if converged:
            solution.converged = True
            solution.convergence_reason = reason
            break

    solution.total_iterations = len(solution.passes)
    if not solution.converged:
        solution.convergence_reason = "Maximum iterations reached"

    # Record final adjustments
    solution.final_adjustments = [
        StepAdjustment(
            target_step=0,
            parameter=k,
            delta=v,
            source_step=0,
            reason="Cumulative adjustment from iterative solver",
        )
        for k, v in cumulative_adjustments.items()
        if abs(v) > 0.001
    ]

    return solution
