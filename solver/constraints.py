"""Telemetry-derived constraint functions for the optimization solver.

Instead of converting telemetry measurements to scalar modifier floors
(lossy), this module defines inequality constraints that the optimizer
evaluates directly via ``ObjectiveFunction.evaluate_physics()``.

Each constraint maps a measured problem to a specific physics output
from ``PhysicsResult`` with a bound and penalty.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TelemetryConstraint:
    """A single inequality constraint derived from telemetry.

    The constraint is: ``physics_metric`` {>=, <=} ``bound``.
    If violated, penalty = ``penalty_per_unit`` × |violation|, capped at
    ``max_penalty_ms``.  If ``hard_veto`` is True, violation immediately
    vetoes the candidate.
    """
    name: str                     # human-readable label
    physics_metric: str           # field name on PhysicsResult
    bound_type: str               # "ge" (>=) or "le" (<=)
    bound: float                  # threshold value
    penalty_per_unit: float       # ms per unit of violation
    max_penalty_ms: float = 200.0
    hard_veto: bool = False
    source_signal: str = ""       # MeasuredState field that triggered this
    source_value: float = 0.0     # actual measured value


@dataclass
class TelemetryConstraintSet:
    """Collection of constraints derived from a telemetry session."""
    constraints: list[TelemetryConstraint] = field(default_factory=list)

    def evaluate(self, physics: Any) -> tuple[float, list[str], bool]:
        """Evaluate all constraints against a PhysicsResult.

        Returns
        -------
        total_penalty_ms : float
            Sum of soft constraint violations.
        violations : list[str]
            Human-readable violation descriptions.
        vetoed : bool
            True if any hard-veto constraint is violated.
        """
        total_penalty = 0.0
        violations: list[str] = []
        vetoed = False

        for c in self.constraints:
            value = getattr(physics, c.physics_metric, None)
            if value is None:
                continue

            if c.bound_type == "ge":
                violation = c.bound - value  # positive when value < bound
            else:  # "le"
                violation = value - c.bound  # positive when value > bound

            if violation > 0:
                if c.hard_veto:
                    vetoed = True
                    violations.append(
                        f"VETO {c.name}: {c.physics_metric}={value:.2f} "
                        f"{'<' if c.bound_type == 'ge' else '>'} {c.bound:.2f}"
                    )
                penalty = min(c.penalty_per_unit * violation, c.max_penalty_ms)
                total_penalty += penalty
                violations.append(
                    f"{c.name}: {c.physics_metric}={value:.2f} "
                    f"{'<' if c.bound_type == 'ge' else '>'} {c.bound:.2f} "
                    f"(-{penalty:.1f}ms)"
                )

        return total_penalty, violations, vetoed


def constraints_from_diagnosis(
    diagnosis: Any,
    measured: Any | None = None,
) -> TelemetryConstraintSet:
    """Convert a Diagnosis + MeasuredState into a TelemetryConstraintSet.

    Translates diagnosed problems into specific physics constraints that
    the optimizer can evaluate directly against ``PhysicsResult``.
    """
    constraints: list[TelemetryConstraint] = []

    if diagnosis is None:
        return TelemetryConstraintSet(constraints=constraints)

    for problem in getattr(diagnosis, "problems", []):
        cat = problem.category
        sev = problem.severity

        # ── Safety: bottoming → bottoming margin constraint ──
        if cat == "safety" and "bottoming" in problem.symptom.lower():
            # More events → tighter constraint
            target_margin = 3.0 if sev == "critical" else 2.0 if sev == "significant" else 1.0
            if "front" in problem.symptom.lower():
                constraints.append(TelemetryConstraint(
                    name="front_bottoming_safety",
                    physics_metric="front_bottoming_margin_mm",
                    bound_type="ge",
                    bound=target_margin,
                    penalty_per_unit=30.0,
                    hard_veto=(sev == "critical"),
                    source_signal="bottoming_event_count_front_clean",
                    source_value=problem.measured,
                ))
            if "rear" in problem.symptom.lower():
                constraints.append(TelemetryConstraint(
                    name="rear_bottoming_safety",
                    physics_metric="rear_bottoming_margin_mm",
                    bound_type="ge",
                    bound=target_margin,
                    penalty_per_unit=30.0,
                    hard_veto=(sev == "critical"),
                    source_signal="bottoming_event_count_rear_clean",
                    source_value=problem.measured,
                ))

        # ── Safety: vortex burst → stall margin constraint ──
        if cat == "safety" and "vortex" in problem.symptom.lower():
            constraints.append(TelemetryConstraint(
                name="vortex_stall_safety",
                physics_metric="stall_margin_mm",
                bound_type="ge",
                bound=3.0,
                penalty_per_unit=100.0,
                hard_veto=True,
                source_signal="vortex_burst_event_count",
                source_value=problem.measured,
            ))

        # ── Platform: RH variance → sigma constraint ──
        if cat == "platform" and "variance" in problem.symptom.lower():
            # Target: reduce current sigma by 15%
            target_sigma = problem.threshold  # the threshold that was exceeded
            if "front" in problem.symptom.lower():
                constraints.append(TelemetryConstraint(
                    name="front_platform_sigma",
                    physics_metric="front_sigma_mm",
                    bound_type="le",
                    bound=target_sigma,
                    penalty_per_unit=50.0,
                    source_signal="front_rh_std_mm",
                    source_value=problem.measured,
                ))
            if "rear" in problem.symptom.lower():
                constraints.append(TelemetryConstraint(
                    name="rear_platform_sigma",
                    physics_metric="rear_sigma_mm",
                    bound_type="le",
                    bound=target_sigma,
                    penalty_per_unit=50.0,
                    source_signal="rear_rh_std_mm",
                    source_value=problem.measured,
                ))

    # ── Always-on constraints from measured data ──
    if measured is not None:
        # If we have heave travel data, constrain it
        travel_pct = getattr(measured, "front_heave_travel_used_pct", None)
        if travel_pct is not None and travel_pct > 85:
            constraints.append(TelemetryConstraint(
                name="front_heave_travel_budget",
                physics_metric="front_bottoming_margin_mm",
                bound_type="ge",
                bound=1.0,
                penalty_per_unit=20.0,
                source_signal="front_heave_travel_used_pct",
                source_value=travel_pct,
            ))

    return TelemetryConstraintSet(constraints=constraints)
