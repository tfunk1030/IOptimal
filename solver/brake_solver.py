"""Brake-specific solver logic.

Handles static bias calculation, target/migration modeling,
master-cylinder influence, and braking phase behavior. Extracts
brake-related recommendations that go beyond the simple bias
offset in supporting_solver.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analyzer.extract import MeasuredState
    from analyzer.setup_reader import CurrentSetup
    from car_model.cars import CarModel


@dataclass
class BrakeSolution:
    """Brake system solution with rationale."""

    bias_pct: float = 0.0
    bias_target: float = 0.0
    bias_migration: float = 0.0
    front_master_cyl_mm: float = 0.0
    rear_master_cyl_mm: float = 0.0
    pad_compound: str = ""
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)


def solve_brake_bias(
    car: "CarModel",
    measured: "MeasuredState",
    setup: "CurrentSetup",
    driver_trail_brake_depth: float = 0.5,
) -> BrakeSolution:
    """Calculate brake bias from physics + telemetry feedback.

    The brake bias calculation follows:
    1. Start from weight transfer baseline: front_weight_pct
    2. Adjust for trail braking style (more trail = more forward bias needed)
    3. Correct from measured front lock ratio
    4. Account for master cylinder ratio if available

    Args:
        car: Car model with mass and weight distribution.
        measured: Telemetry measurements.
        setup: Current garage setup.
        driver_trail_brake_depth: 0-1 trail braking aggressiveness.

    Returns:
        BrakeSolution with recommended bias and reasoning.
    """
    notes: list[str] = []

    # --- Baseline from weight distribution ---
    front_weight_pct = car.front_weight_pct if hasattr(car, "front_weight_pct") else 0.48
    baseline_bias = front_weight_pct * 100.0 + 2.0  # Slight forward offset
    notes.append(f"Baseline from weight dist: {baseline_bias:.1f}%")

    # --- Trail braking adjustment ---
    # Aggressive trail braking needs slightly more forward bias
    trail_offset = driver_trail_brake_depth * 1.5  # Up to +1.5% for heavy trail
    adjusted_bias = baseline_bias + trail_offset
    if trail_offset > 0.5:
        notes.append(f"Trail brake offset: +{trail_offset:.1f}%")

    # --- Measured front lock correction ---
    front_lock = measured.front_braking_lock_ratio_p95
    if front_lock > 0.08:
        lock_correction = -(front_lock - 0.05) * 20  # Pull bias rearward
        adjusted_bias += lock_correction
        notes.append(f"Front lock correction: {lock_correction:+.1f}% (lock={front_lock:.3f})")
    elif front_lock > 0 and front_lock < 0.02:
        # Front barely locking — could push slightly forward
        adjusted_bias += 0.3
        notes.append("Front lock very low — slight forward push")

    # --- Master cylinder ratio influence ---
    front_mc = setup.front_master_cyl_mm
    rear_mc = setup.rear_master_cyl_mm
    if front_mc > 0 and rear_mc > 0:
        mc_ratio = front_mc / rear_mc
        notes.append(f"Master cyl ratio F/R: {mc_ratio:.2f}")
        # A larger front MC produces more front pressure per pedal force
        # This is informational — bias target already accounts for it in iRacing

    # --- Clamp to safe range ---
    adjusted_bias = max(44.0, min(58.0, adjusted_bias))

    confidence = 0.7
    if front_lock > 0:
        confidence = 0.8  # Have measured data to validate
    if measured.braking_decel_mean_g > 0:
        confidence = min(0.9, confidence + 0.1)

    return BrakeSolution(
        bias_pct=round(adjusted_bias, 1),
        bias_target=setup.brake_bias_target,
        bias_migration=setup.brake_bias_migration,
        front_master_cyl_mm=front_mc,
        rear_master_cyl_mm=rear_mc,
        pad_compound=setup.pad_compound,
        confidence=round(confidence, 3),
        notes=notes,
    )
