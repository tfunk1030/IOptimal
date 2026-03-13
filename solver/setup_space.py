"""Setup space exploration — feasible region analysis.

Explores the setup parameter space around the solver's optimal values.
For each key parameter:
- Scans ±N steps from the solver's optimal value
- Scores each point: constraint violations + estimated lap time delta
- Finds the "flat bottom" — range where lap time delta < 100ms
- Classifies robustness: tight / moderate / wide

This tells you: "How sensitive is this parameter to small errors?
Can I fine-tune it on track, or must I nail it in the garage?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from solver.arb_solver import ARBSolution
    from solver.heave_solver import HeaveSolution
    from solver.rake_solver import RakeSolution
    from solver.corner_spring_solver import CornerSpringSolution
    from solver.laptime_sensitivity import LaptimeSensitivityReport
    from track_model.profile import TrackProfile

# Flat-bottom threshold: lap time within this many ms of optimal is "flat"
FLAT_BOTTOM_MS = 100.0

# Robustness thresholds (% of optimal value as width of flat bottom)
ROBUSTNESS_TIGHT = 0.20     # <20% range → "tight" (very sensitive)
ROBUSTNESS_MODERATE = 0.40  # 20-40% → "moderate"
# >40% → "wide" (robust)


@dataclass
class SetupPoint:
    """A single point in parameter space."""
    parameter: str
    value: float
    delta_from_optimal: float
    constraint_violations: list[str] = field(default_factory=list)
    estimated_laptime_delta_ms: float = 0.0  # vs optimal
    feasible: bool = True  # all hard constraints satisfied


@dataclass
class ParameterRange:
    """Feasible and flat-bottom range for one parameter."""
    parameter: str
    optimal: float
    units: str
    feasible_min: float
    feasible_max: float
    flat_bottom_min: float
    flat_bottom_max: float
    sensitivity_ms_per_unit: float
    robustness: str             # "tight" | "moderate" | "wide"
    scan_points: list[SetupPoint] = field(default_factory=list)

    @property
    def flat_bottom_width(self) -> float:
        return self.flat_bottom_max - self.flat_bottom_min

    @property
    def feasible_width(self) -> float:
        return self.feasible_max - self.feasible_min


@dataclass
class SetupSpaceReport:
    """Setup space exploration for all key parameters."""
    parameter_ranges: list[ParameterRange] = field(default_factory=list)
    tightest_constraint: str = ""      # parameter with smallest feasible range
    most_robust_parameter: str = ""    # parameter with widest flat bottom

    def summary(self, width: int = 63) -> str:
        lines = [
            "=" * width,
            "  SETUP SPACE EXPLORATION",
            "=" * width,
            "",
            "  Feasible range: all hard constraints satisfied.",
            "  Flat bottom: within 100ms of optimal lap time.",
            "  Robustness: tight (<20%), moderate (20-40%), wide (>40%).",
            "",
        ]

        if not self.parameter_ranges:
            lines.append("  (no setup space data)")
            lines.append("=" * width)
            return "\n".join(lines)

        header = (
            f"  {'Parameter':<22s} {'Opt':>6s} {'Feas min':>9s} "
            f"{'Feas max':>9s} {'FlatBot W':>10s} {'Robust':>8s}"
        )
        lines.append(header)
        lines.append("  " + "-" * (width - 4))

        for pr in self.parameter_ranges:
            lines.append(
                f"  {pr.parameter:<22s} {pr.optimal:>6.2f} "
                f"{pr.feasible_min:>9.2f} {pr.feasible_max:>9.2f} "
                f"{pr.flat_bottom_width:>10.2f} {pr.robustness:>8s}"
            )

        if self.tightest_constraint:
            lines.append("")
            lines.append(
                f"  Tightest constraint: {self.tightest_constraint} "
                f"(smallest feasible range — nail this one)"
            )
        if self.most_robust_parameter:
            lines.append(
                f"  Most robust:         {self.most_robust_parameter} "
                f"(wide flat bottom — can fine-tune on track)"
            )

        # Scan charts for top 3 most sensitive parameters
        tightest_params = sorted(
            self.parameter_ranges,
            key=lambda p: p.feasible_width / max(abs(p.optimal), 0.01),
        )[:3]

        if any(p.scan_points for p in tightest_params):
            lines.append("")
            lines.append("  PARAMETER SCANS (tightest 3)")
            lines.append("  " + "-" * (width - 4))

            for pr in tightest_params:
                if not pr.scan_points:
                    continue
                lines.append(f"  {pr.parameter} [{pr.units}]")
                lines.append(
                    f"    {'Value':>7s}  {'DeltaLap':>9s}  {'Feasible':>8s}"
                )
                for pt in pr.scan_points:
                    feas_str = "OK" if pt.feasible else "VIOLATED"
                    lines.append(
                        f"    {pt.value:>7.2f}  {pt.estimated_laptime_delta_ms:>+8.1f}ms"
                        f"  {feas_str:>8s}"
                    )
                    if pt.constraint_violations and not pt.feasible:
                        for v in pt.constraint_violations[:2]:
                            lines.append(f"        ! {v[:width - 12]}")

        lines.append("")
        lines.append("=" * width)
        return "\n".join(lines)


# ── Helper functions ─────────────────────────────────────────────────────────

def _robustness_label(flat_width: float, optimal: float) -> str:
    """Classify robustness based on flat bottom width relative to optimal."""
    if abs(optimal) < 0.01:
        pct = 1.0  # avoid division by zero for zero-optimal values
    else:
        pct = flat_width / abs(optimal)

    if pct < ROBUSTNESS_TIGHT:
        return "tight"
    elif pct < ROBUSTNESS_MODERATE:
        return "moderate"
    return "wide"


def _build_range(
    parameter: str,
    units: str,
    optimal: float,
    scan_points: list[SetupPoint],
) -> ParameterRange:
    """Build ParameterRange from a list of scanned points."""
    if not scan_points:
        return ParameterRange(
            parameter=parameter, optimal=optimal, units=units,
            feasible_min=optimal, feasible_max=optimal,
            flat_bottom_min=optimal, flat_bottom_max=optimal,
            sensitivity_ms_per_unit=0.0, robustness="wide",
        )

    feasible = [p for p in scan_points if p.feasible]
    feasible_min = min(p.value for p in feasible) if feasible else optimal
    feasible_max = max(p.value for p in feasible) if feasible else optimal

    flat = [p for p in feasible if abs(p.estimated_laptime_delta_ms) <= FLAT_BOTTOM_MS]
    flat_min = min(p.value for p in flat) if flat else optimal
    flat_max = max(p.value for p in flat) if flat else optimal

    # Sensitivity: estimate ms per unit from nearby feasible points
    nearby = sorted(feasible, key=lambda p: abs(p.delta_from_optimal))[:3]
    if len(nearby) >= 2:
        deltas = [p.delta_from_optimal for p in nearby if abs(p.delta_from_optimal) > 1e-6]
        costs = [p.estimated_laptime_delta_ms for p in nearby if abs(p.delta_from_optimal) > 1e-6]
        if deltas and costs:
            sensitivity = sum(c / d for c, d in zip(costs, deltas) if abs(d) > 0) / len(costs)
        else:
            sensitivity = 0.0
    else:
        sensitivity = 0.0

    flat_width = flat_max - flat_min
    robustness = _robustness_label(flat_width, optimal)

    return ParameterRange(
        parameter=parameter,
        optimal=optimal,
        units=units,
        feasible_min=feasible_min,
        feasible_max=feasible_max,
        flat_bottom_min=flat_min,
        flat_bottom_max=flat_max,
        sensitivity_ms_per_unit=round(sensitivity, 2),
        robustness=robustness,
        scan_points=scan_points,
    )


# ── Individual parameter space solvers ───────────────────────────────────────

def _scan_rear_arb(
    step4: "ARBSolution",
    sensitivity_ms_per_blade: float,
) -> ParameterRange:
    """Scan RARB blade space (1-5) around the solver's optimal."""
    optimal = float(step4.rear_arb_blade_start)
    target_lltd = step4.lltd_target
    lltd_per_blade = getattr(step4, "rarb_sensitivity_per_blade", -0.030)

    points = []
    for blade in range(1, 6):
        delta = float(blade) - optimal
        laptime_delta = sensitivity_ms_per_blade * delta

        # Check LLTD constraint: deviation > 5% from target is a violation
        lltd_at_blade = step4.lltd_achieved + delta * lltd_per_blade
        lltd_violation = abs(lltd_at_blade - target_lltd) > 0.05

        violations = []
        if lltd_violation:
            violations.append(
                f"LLTD {lltd_at_blade:.1%} vs target {target_lltd:.1%}"
            )

        # Blade 1 and 5 may have additional constraint violations (extreme settings)
        if blade == 1 and step4.lltd_target > 0.55:
            violations.append("LLTD critically low at blade 1")
        if blade == 5 and step4.lltd_target < 0.45:
            violations.append("LLTD critically high at blade 5")

        points.append(SetupPoint(
            parameter="rear_arb_blade",
            value=float(blade),
            delta_from_optimal=delta,
            constraint_violations=violations,
            estimated_laptime_delta_ms=round(laptime_delta, 1),
            feasible=not violations,
        ))

    return _build_range("rear_arb_blade", "blade", optimal, points)


def _scan_rear_rh(
    step1: "RakeSolution",
    sensitivity_ms_per_mm: float,
    vortex_burst_threshold: float = 2.0,
) -> ParameterRange:
    """Scan rear ride height space ±10mm around optimal."""
    optimal = step1.dynamic_rear_rh_mm

    points = []
    for delta_mm in [-10, -8, -6, -4, -2, 0, 2, 4, 6, 8, 10]:
        value = optimal + delta_mm
        laptime_delta = sensitivity_ms_per_mm * delta_mm

        violations = []
        # Constraint: rear RH must stay within aero map bounds
        if value < 25.0:
            violations.append(f"Rear RH {value:.0f}mm below aero map lower bound (25mm)")
        if value > 75.0:
            violations.append(f"Rear RH {value:.0f}mm above aero map upper bound (75mm)")

        points.append(SetupPoint(
            parameter="rear_rh_mm",
            value=round(value, 1),
            delta_from_optimal=float(delta_mm),
            constraint_violations=violations,
            estimated_laptime_delta_ms=round(laptime_delta, 1),
            feasible=not violations,
        ))

    return _build_range("rear_rh_mm", "mm", optimal, points)


def _scan_front_rh(
    step1: "RakeSolution",
    sensitivity_ms_per_mm: float,
) -> ParameterRange:
    """Scan front ride height space ±6mm around optimal."""
    optimal = step1.dynamic_front_rh_mm
    vortex_margin = step1.vortex_burst_margin_mm

    points = []
    for delta_mm in [-6, -4, -2, 0, 2, 4, 6]:
        value = optimal + delta_mm
        laptime_delta = sensitivity_ms_per_mm * delta_mm

        violations = []
        # Vortex burst constraint: front dynamic RH must stay above threshold
        new_margin = vortex_margin + delta_mm  # lower RH = closer to vortex burst
        if new_margin < 0:
            violations.append(
                f"Front RH {value:.0f}mm violates vortex burst threshold "
                f"(margin={new_margin:.1f}mm)"
            )
        if value < 5.0:
            violations.append(f"Front RH {value:.0f}mm below aero map lower bound")
        if value > 50.0:
            violations.append(f"Front RH {value:.0f}mm above aero map upper bound")

        points.append(SetupPoint(
            parameter="front_rh_mm",
            value=round(value, 1),
            delta_from_optimal=float(delta_mm),
            constraint_violations=violations,
            estimated_laptime_delta_ms=round(laptime_delta, 1),
            feasible=not violations,
        ))

    return _build_range("front_rh_mm", "mm", optimal, points)


def _scan_front_heave(
    step2: "HeaveSolution",
    sensitivity_ms_per_10nmm: float,
) -> ParameterRange:
    """Scan front heave spring ±40 N/mm around optimal."""
    optimal = step2.front_heave_nmm
    bottoming_margin = step2.front_bottoming_margin_mm

    points = []
    for delta_nmm in [-40, -30, -20, -10, 0, 10, 20, 30, 40]:
        value = optimal + delta_nmm
        if value < 10:
            continue
        laptime_delta = sensitivity_ms_per_10nmm * delta_nmm / 10.0

        violations = []
        # Bottoming constraint
        new_margin = bottoming_margin - delta_nmm * 0.2  # softer spring = less margin
        if new_margin < 0:
            violations.append(
                f"Heave {value:.0f} N/mm: bottoming margin {new_margin:.1f}mm < 0"
            )
        if value < 20:
            violations.append(f"Heave {value:.0f} N/mm below minimum (20 N/mm)")
        if value > 200:
            violations.append(f"Heave {value:.0f} N/mm above maximum (200 N/mm)")

        points.append(SetupPoint(
            parameter="front_heave_nmm",
            value=float(value),
            delta_from_optimal=float(delta_nmm),
            constraint_violations=violations,
            estimated_laptime_delta_ms=round(laptime_delta, 1),
            feasible=not violations,
        ))

    return _build_range("front_heave_nmm", "N/mm", optimal, points)


def _scan_torsion_bar(
    step3: "CornerSpringSolution",
    sensitivity_ms_per_01mm: float,
) -> ParameterRange:
    """Scan torsion bar OD ±0.5mm around optimal."""
    optimal = step3.front_torsion_od_mm

    points = []
    for delta_01 in [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5]:
        delta_mm = delta_01 * 0.1
        value = optimal + delta_mm
        laptime_delta = sensitivity_ms_per_01mm * delta_01

        violations = []
        if value < 11.0:
            violations.append(f"OD {value:.2f}mm below range minimum (11mm)")
        if value > 18.2:
            violations.append(f"OD {value:.2f}mm above range maximum (18.2mm)")

        points.append(SetupPoint(
            parameter="torsion_bar_od_mm",
            value=round(value, 2),
            delta_from_optimal=round(delta_mm, 2),
            constraint_violations=violations,
            estimated_laptime_delta_ms=round(laptime_delta, 1),
            feasible=not violations,
        ))

    return _build_range("torsion_bar_od_mm", "mm", optimal, points)


# ── Main entry point ─────────────────────────────────────────────────────────

def explore_setup_space(
    track: "TrackProfile",
    step1: "RakeSolution",
    step2: "HeaveSolution",
    step3: "CornerSpringSolution",
    step4: "ARBSolution",
    sensitivity: "LaptimeSensitivityReport | None" = None,
) -> SetupSpaceReport:
    """Explore the feasible parameter space around the solver's optimal setup.

    For each key parameter, scans values around the optimal and reports:
    - Feasible range (all hard constraints satisfied)
    - Flat bottom (within 100ms of optimal lap time)
    - Robustness classification

    Args:
        track: Track profile
        step1: Rake solution
        step2: Heave solution
        step3: Corner spring solution
        step4: ARB solution
        sensitivity: Pre-computed laptime sensitivity (for ms-per-unit values)

    Returns:
        SetupSpaceReport with parameter ranges and robustness classification
    """
    # Extract sensitivities (or use defaults if not provided)
    sens_map: dict[str, float] = {}
    if sensitivity is not None:
        for s in sensitivity.sensitivities:
            sens_map[s.parameter] = s.delta_per_unit_ms

    # Default sensitivities (ms per unit) if not provided
    rarb_ms_per_blade = abs(sens_map.get("rear_arb_blade", -180.0))
    rear_rh_ms_per_mm = abs(sens_map.get("rear_rh_mm", 10.0))
    front_rh_ms_per_mm = abs(sens_map.get("front_rh_mm", 25.0))
    heave_ms_per_10nmm = abs(sens_map.get("front_heave_nmm", 0.7)) * 10
    torsion_ms_per_01mm = abs(sens_map.get("torsion_bar_od_mm", 5.0)) * 0.1

    # Scan each parameter
    ranges = [
        _scan_rear_arb(step4, rarb_ms_per_blade),
        _scan_rear_rh(step1, rear_rh_ms_per_mm),
        _scan_front_rh(step1, front_rh_ms_per_mm),
        _scan_front_heave(step2, heave_ms_per_10nmm),
        _scan_torsion_bar(step3, torsion_ms_per_01mm),
    ]

    # Find tightest constraint and most robust parameter
    tightest = ""
    min_feasible_pct = float("inf")
    most_robust = ""
    max_flat_pct = -1.0

    for pr in ranges:
        feasible_pct = pr.feasible_width / max(abs(pr.optimal), 0.01)
        flat_pct = pr.flat_bottom_width / max(abs(pr.optimal), 0.01)

        if feasible_pct < min_feasible_pct:
            min_feasible_pct = feasible_pct
            tightest = pr.parameter

        if flat_pct > max_flat_pct:
            max_flat_pct = flat_pct
            most_robust = pr.parameter

    return SetupSpaceReport(
        parameter_ranges=ranges,
        tightest_constraint=tightest,
        most_robust_parameter=most_robust,
    )
