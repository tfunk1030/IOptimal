"""Stint/session reasoning — multi-condition setup optimization.

The solver normally optimizes for a single moment. Real stints have:
- Fuel burn (mass decreases, CG shifts, weight distribution changes)
- Tyre degradation (grip drops, balance shifts)
- Track evolution (grip improves as rubber is laid)

This module computes solver solutions at multiple conditions and finds
the best compromise parameters across the stint.

Key outputs:
- Setup sensitivity to fuel load
- Predicted balance shift over stint
- Compromise parameter recommendations
- Pushrod compensation for fuel burn
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from car_model.cars import CarModel


@dataclass
class FuelState:
    """Car state at a specific fuel load."""
    fuel_load_l: float
    fuel_mass_kg: float
    total_mass_kg: float
    front_weight_pct: float
    cg_height_mm: float
    pushrod_correction_mm: float  # pushrod offset delta from full tank


@dataclass
class StintCondition:
    """Solver-relevant parameters at one point in the stint."""
    label: str                     # "full_fuel" | "half_fuel" | "empty_fuel"
    fuel_state: FuelState
    lap_number: int = 0

    # Predicted parameter changes from full-fuel baseline
    front_weight_shift_pct: float = 0.0
    damping_ratio_shift: float = 0.0
    understeer_shift_deg: float = 0.0

    # Spring rate recommendation at this condition
    heave_optimal_nmm: float = 0.0
    third_optimal_nmm: float = 0.0


@dataclass
class TyreDegradation:
    """Predicted tyre degradation over a stint."""
    grip_loss_per_10_laps_pct: float = 3.0    # typical 2-4%
    balance_shift_per_10_laps_deg: float = 0.5  # rears degrade faster → understeer
    pressure_rise_per_10_laps_kpa: float = 3.0  # thermal soak

    # Recommendations
    preemptive_rarb_offset: int = 0  # pre-set RARB softer to compensate
    pressure_cold_offset_kpa: float = 0.0


@dataclass
class StintStrategy:
    """Complete stint optimization result."""
    conditions: list[StintCondition] = field(default_factory=list)
    degradation: TyreDegradation = field(default_factory=TyreDegradation)
    compromise_parameters: dict[str, float] = field(default_factory=dict)
    compromise_reasoning: list[str] = field(default_factory=list)

    def summary(self, width: int = 63) -> str:
        lines = [
            "=" * width,
            "  STINT/SESSION ANALYSIS",
            "=" * width,
        ]

        # Fuel states
        lines.append("")
        lines.append("  FUEL LOAD SENSITIVITY")
        lines.append("  " + "-" * (width - 4))
        lines.append(
            f"  {'Condition':<15s} {'Fuel':>5s} {'Mass':>6s} "
            f"{'F wt%':>5s} {'ΔUS':>5s} {'Heave':>6s}"
        )

        for cond in self.conditions:
            lines.append(
                f"  {cond.label:<15s} "
                f"{cond.fuel_state.fuel_load_l:>5.0f}L "
                f"{cond.fuel_state.total_mass_kg:>5.0f}kg "
                f"{cond.fuel_state.front_weight_pct:>5.1f} "
                f"{cond.understeer_shift_deg:>+5.1f} "
                f"{cond.heave_optimal_nmm:>6.0f}"
            )

        # Tyre degradation
        lines.append("")
        lines.append("  TYRE DEGRADATION PREDICTION")
        lines.append("  " + "-" * (width - 4))
        deg = self.degradation
        lines.append(f"    Grip loss: ~{deg.grip_loss_per_10_laps_pct:.1f}% per 10 laps")
        lines.append(f"    Balance shift: +{deg.balance_shift_per_10_laps_deg:.1f}° understeer per 10 laps")
        lines.append(f"    Pressure rise: +{deg.pressure_rise_per_10_laps_kpa:.0f} kPa per 10 laps")

        if deg.preemptive_rarb_offset != 0:
            lines.append(
                f"    Recommendation: Start RARB {deg.preemptive_rarb_offset:+d} blade(s) "
                f"from optimal to compensate for degradation"
            )

        # Compromise parameters
        if self.compromise_parameters:
            lines.append("")
            lines.append("  COMPROMISE PARAMETERS")
            lines.append("  " + "-" * (width - 4))
            for param, value in self.compromise_parameters.items():
                lines.append(f"    {param}: {value:.1f}")

        if self.compromise_reasoning:
            lines.append("")
            for reason in self.compromise_reasoning:
                lines.append(f"    {reason}")

        lines.append("=" * width)
        return "\n".join(lines)


# ── Fuel load model ──────────────────────────────────────────────────

# Fuel density: ~0.73 kg/L for racing fuel
FUEL_DENSITY_KG_PER_L = 0.73

# Fuel tank position relative to CG (fraction of wheelbase behind front axle)
# GTP cars: fuel tank is slightly behind the CG
FUEL_TANK_POSITION_FRACTION = 0.55  # 55% of wheelbase from front

# Pushrod correction per fuel load change
# From per-car-quirks.md: BMW 89L → 12L needs ~0.5mm pushrod correction
PUSHROD_CORRECTION_MM_PER_KG = 0.5 / (77 * FUEL_DENSITY_KG_PER_L)


def compute_fuel_states(
    car: CarModel,
    fuel_levels_l: list[float] | None = None,
) -> list[FuelState]:
    """Compute car state at different fuel loads.

    Args:
        car: Car model
        fuel_levels_l: Fuel levels to compute (default: [89, 50, 12])

    Returns:
        List of FuelState objects
    """
    if fuel_levels_l is None:
        fuel_levels_l = [89.0, 50.0, 12.0]

    dry_mass_kg = car.mass_car_kg + car.mass_driver_kg  # car + driver mass without fuel
    wheelbase_m = car.wheelbase_m
    front_weight_base = car.weight_dist_front

    states = []
    for fuel_l in fuel_levels_l:
        fuel_mass = fuel_l * FUEL_DENSITY_KG_PER_L
        total_mass = dry_mass_kg + fuel_mass

        # Fuel CG effect on front weight distribution
        # Fuel behind CG → reduces front weight %
        fuel_moment = fuel_mass * FUEL_TANK_POSITION_FRACTION * wheelbase_m
        total_moment = (dry_mass_kg * front_weight_base * wheelbase_m + fuel_moment)
        front_weight = total_moment / (total_mass * wheelbase_m)

        # CG height shift (fuel tank is ~200mm above ground)
        cg_shift = fuel_mass * 0.200 / total_mass  # approximate contribution

        # Pushrod correction from full tank
        full_fuel_mass = fuel_levels_l[0] * FUEL_DENSITY_KG_PER_L
        delta_mass = full_fuel_mass - fuel_mass
        pushrod_correction = delta_mass * PUSHROD_CORRECTION_MM_PER_KG

        states.append(FuelState(
            fuel_load_l=fuel_l,
            fuel_mass_kg=round(fuel_mass, 1),
            total_mass_kg=round(total_mass, 1),
            front_weight_pct=round(front_weight * 100, 1),
            cg_height_mm=round(car.corner_spring.cg_height_mm + cg_shift * 1000, 1),
            pushrod_correction_mm=round(pushrod_correction, 1),
        ))

    return states


# ── Tyre degradation model ───────────────────────────────────────────

def predict_tyre_degradation(
    stint_laps: int = 30,
    car_name: str = "bmw",
) -> TyreDegradation:
    """Predict tyre degradation effects over a stint.

    Based on empirical data from per-car-quirks.md and telemetry analysis.
    """
    # Empirical degradation rates (from Vision tread model, S1 2026)
    grip_loss = 3.0  # % per 10 laps (conservative estimate)
    balance_shift = 0.5  # degrees understeer per 10 laps (rears degrade faster)
    pressure_rise = 3.0  # kPa per 10 laps

    # Preemptive RARB offset
    # If stint > 20 laps, expect +1° understeer → pre-set RARB 1 blade softer
    preemptive_rarb = 0
    if stint_laps > 20:
        preemptive_rarb = -1  # softer = more rear load transfer = less understeer
    if stint_laps > 40:
        preemptive_rarb = -2

    # Cold pressure offset: if hot pressure will rise 9+ kPa over stint,
    # start 3 kPa lower cold
    total_pressure_rise = pressure_rise * stint_laps / 10
    pressure_cold_offset = -min(total_pressure_rise / 3, 5.0)  # cap at -5 kPa

    return TyreDegradation(
        grip_loss_per_10_laps_pct=grip_loss,
        balance_shift_per_10_laps_deg=balance_shift,
        pressure_rise_per_10_laps_kpa=pressure_rise,
        preemptive_rarb_offset=preemptive_rarb,
        pressure_cold_offset_kpa=round(pressure_cold_offset, 1),
    )


# ── Compromise optimization ──────────────────────────────────────────

def find_compromise_parameters(
    conditions: list[StintCondition],
) -> tuple[dict[str, float], list[str]]:
    """Find parameter values that minimize worst-case violation across conditions.

    For each parameter, pick the value that satisfies the tightest constraint
    across all fuel states.
    """
    params: dict[str, float] = {}
    reasoning: list[str] = []

    if not conditions:
        return params, reasoning

    # Heave spring: take the maximum across conditions (safety-binding at worst case)
    heave_values = [c.heave_optimal_nmm for c in conditions if c.heave_optimal_nmm > 0]
    if heave_values:
        max_heave = max(heave_values)
        min_heave = min(heave_values)
        params["front_heave_nmm"] = max_heave

        if max_heave > min_heave:
            max_cond = next(c for c in conditions if c.heave_optimal_nmm == max_heave)
            reasoning.append(
                f"Front heave: {max_heave:.0f} N/mm (constrained at {max_cond.label}, "
                f"optimal {min_heave:.0f} at lightest fuel). "
                f"Safety-binding at full fuel."
            )

    # Third spring: same logic
    third_values = [c.third_optimal_nmm for c in conditions if c.third_optimal_nmm > 0]
    if third_values:
        max_third = max(third_values)
        params["rear_third_nmm"] = max_third

    return params, reasoning


# ── Full stint analysis ──────────────────────────────────────────────

def analyze_stint(
    car: CarModel,
    stint_laps: int = 30,
    fuel_levels_l: list[float] | None = None,
    base_heave_nmm: float = 50.0,
    base_third_nmm: float = 530.0,
    v_p99_front_mps: float = 0.260,
    v_p99_rear_mps: float = 0.324,
) -> StintStrategy:
    """Analyze setup sensitivity across a full stint.

    Args:
        car: Car model
        stint_laps: Expected stint length
        fuel_levels_l: Fuel states to analyze
        base_heave_nmm: Baseline front heave from solver
        base_third_nmm: Baseline rear third from solver
        v_p99_front_mps: Track front shock velocity p99
        v_p99_rear_mps: Track rear shock velocity p99

    Returns:
        StintStrategy with multi-condition analysis and compromise parameters
    """
    # Compute fuel states
    fuel_states = compute_fuel_states(car, fuel_levels_l)

    # Build stint conditions
    conditions = []
    for i, fs in enumerate(fuel_states):
        label = {0: "full_fuel", 1: "half_fuel", 2: "empty_fuel"}.get(i, f"fuel_{fs.fuel_load_l:.0f}L")

        # Estimate parameter changes from baseline
        mass_ratio = fs.total_mass_kg / fuel_states[0].total_mass_kg
        front_weight_shift = fs.front_weight_pct - fuel_states[0].front_weight_pct

        # Heave spring: k_min ∝ m_eff (lighter car needs less spring)
        heave_at_fuel = base_heave_nmm * mass_ratio
        heave_at_fuel = math.ceil(heave_at_fuel / 10) * 10  # round to garage step

        third_at_fuel = base_third_nmm * mass_ratio
        third_at_fuel = math.ceil(third_at_fuel / 10) * 10

        # Damping ratio shift: ζ ∝ 1/√(k*m), so lighter → higher ζ
        zeta_shift = 1.0 / math.sqrt(mass_ratio) - 1.0

        # Understeer shift from weight distribution change
        # More front weight → more understeer (rough linear model)
        us_shift = front_weight_shift * 0.5  # ~0.5° per 1% weight shift

        conditions.append(StintCondition(
            label=label,
            fuel_state=fs,
            lap_number=int(i * stint_laps / max(len(fuel_states) - 1, 1)),
            front_weight_shift_pct=round(front_weight_shift, 2),
            damping_ratio_shift=round(zeta_shift, 3),
            understeer_shift_deg=round(us_shift, 1),
            heave_optimal_nmm=heave_at_fuel,
            third_optimal_nmm=third_at_fuel,
        ))

    # Tyre degradation
    degradation = predict_tyre_degradation(stint_laps, car.canonical_name)

    # Compromise parameters
    compromise, reasoning = find_compromise_parameters(conditions)

    return StintStrategy(
        conditions=conditions,
        degradation=degradation,
        compromise_parameters=compromise,
        compromise_reasoning=reasoning,
    )
