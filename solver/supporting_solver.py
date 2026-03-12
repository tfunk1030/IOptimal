"""Physics-based solver for supporting parameters: brakes, diff, TC, tyre pressures.

These parameters are currently hardcoded in setup_writer.py. This solver derives
them from:
- Weight transfer physics (brake bias)
- Driver behavior (diff ramps, preload)
- Measured tyre data (pressures)
- Traction demand (TC settings)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analyzer.diagnose import Diagnosis
    from analyzer.driver_style import DriverProfile
    from analyzer.extract import MeasuredState
    from car_model.cars import CarModel


@dataclass
class SupportingSolution:
    """Computed values for brake, diff, TC, and tyre pressure parameters."""

    # Brakes
    brake_bias_pct: float = 46.5
    brake_bias_reasoning: str = ""

    # Differential
    diff_preload_nm: float = 10.0
    diff_ramp_coast: int = 40  # coast ramp angle (degrees)
    diff_ramp_drive: int = 65  # drive ramp angle (degrees)
    diff_clutch_plates: int = 6
    diff_reasoning: str = ""

    # Traction control
    tc_gain: int = 4
    tc_slip: int = 3
    tc_reasoning: str = ""

    # Tyre pressures (per corner, cold setting in kPa)
    tyre_cold_fl_kpa: float = 152.0
    tyre_cold_fr_kpa: float = 152.0
    tyre_cold_rl_kpa: float = 152.0
    tyre_cold_rr_kpa: float = 152.0
    pressure_reasoning: str = ""

    def summary(self) -> str:
        lines = [
            f"Brake bias: {self.brake_bias_pct:.1f}%",
            f"  {self.brake_bias_reasoning}",
            f"Diff: preload={self.diff_preload_nm:.0f} Nm, "
            f"coast={self.diff_ramp_coast}°, drive={self.diff_ramp_drive}°, "
            f"plates={self.diff_clutch_plates}",
            f"  {self.diff_reasoning}",
            f"TC: gain={self.tc_gain}, slip={self.tc_slip}",
            f"  {self.tc_reasoning}",
            f"Tyres (cold kPa): FL={self.tyre_cold_fl_kpa:.0f} FR={self.tyre_cold_fr_kpa:.0f} "
            f"RL={self.tyre_cold_rl_kpa:.0f} RR={self.tyre_cold_rr_kpa:.0f}",
            f"  {self.pressure_reasoning}",
        ]
        return "\n".join(lines)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class SupportingSolver:
    """Compute brake bias, diff, TC, and tyre pressures from physics + driver style."""

    def __init__(
        self,
        car: CarModel,
        driver: DriverProfile,
        measured: MeasuredState,
        diagnosis: Diagnosis,
    ) -> None:
        self.car = car
        self.driver = driver
        self.measured = measured
        self.diagnosis = diagnosis

    def solve(self) -> SupportingSolution:
        sol = SupportingSolution()
        self._solve_brake_bias(sol)
        self._solve_diff(sol)
        self._solve_tc(sol)
        self._solve_pressures(sol)
        return sol

    def _solve_brake_bias(self, sol: SupportingSolution) -> None:
        """Brake bias from weight transfer under braking + driver style.

        Baseline: front_weight_pct + 1.0% (front tyres carry more under decel)
        Adjustments from driver trail braking and measured slip ratios.
        """
        car = self.car
        driver = self.driver
        measured = self.measured

        # Baseline from static weight distribution
        bias = car.weight_dist_front * 100 + 1.0
        reasons = [f"Baseline: {car.weight_dist_front*100:.1f}% + 1.0% = {bias:.1f}%"]

        # Deep trail braker needs more front authority for later rotation
        if driver.trail_brake_classification == "deep":
            bias += 0.5
            reasons.append("+0.5% for deep trail braking")
        elif driver.trail_brake_classification == "light":
            bias -= 0.3
            reasons.append("-0.3% for light trail braking")

        # Front locking detected → shift rearward
        if measured.front_slip_ratio_p95 > 0.06:
            bias -= 0.5
            reasons.append(f"-0.5% for front slip ratio p95={measured.front_slip_ratio_p95:.3f}")

        # Rear instability under braking → shift forward
        if measured.body_slip_p95_deg > 5.0:
            bias += 0.3
            reasons.append(f"+0.3% for high body slip p95={measured.body_slip_p95_deg:.1f}°")

        sol.brake_bias_pct = round(_clamp(bias, 44.0, 50.0), 1)
        sol.brake_bias_reasoning = "; ".join(reasons)

    def _solve_diff(self, sol: SupportingSolution) -> None:
        """Differential from traction demand × driver style.

        Preload: stability vs rotation tradeoff
        Coast ramp: trail braking behavior
        Drive ramp: throttle application style
        """
        driver = self.driver
        measured = self.measured

        # ── Preload ──
        preload = 10.0  # neutral baseline
        reasons = ["Preload baseline: 10 Nm"]

        if driver.throttle_classification == "binary":
            preload += 10
            reasons.append("+10 Nm for binary throttle (stability)")
        elif driver.throttle_classification == "progressive":
            preload -= 3
            reasons.append("-3 Nm for progressive throttle (rotation)")

        if measured.body_slip_p95_deg > 4.0:
            preload += 5
            reasons.append(f"+5 Nm for body slip p95={measured.body_slip_p95_deg:.1f}° (lock more)")

        if driver.trail_brake_classification == "deep":
            preload -= 5
            reasons.append("-5 Nm for deep trail braking (rotation on coast)")

        if measured.rear_slip_ratio_p95 > 0.05:
            preload += 5
            reasons.append(f"+5 Nm for rear slip ratio p95={measured.rear_slip_ratio_p95:.3f}")

        sol.diff_preload_nm = round(_clamp(preload, 5.0, 40.0), 0)

        # ── Coast ramp ── (lower angle = more locking on coast/decel)
        coast = 45 - int(driver.trail_brake_depth_mean * 10)
        coast = int(_clamp(coast, 30, 55))
        reasons.append(f"Coast ramp: {coast}° (from trail brake depth {driver.trail_brake_depth_mean:.2f})")

        # ── Drive ramp ── (higher angle = less locking on accel)
        drive = 65 + int(driver.throttle_progressiveness * 10)
        drive = int(_clamp(drive, 55, 80))
        reasons.append(f"Drive ramp: {drive}° (from throttle R²={driver.throttle_progressiveness:.2f})")

        sol.diff_ramp_coast = coast
        sol.diff_ramp_drive = drive
        sol.diff_clutch_plates = 6  # BMW default, rarely changed
        sol.diff_reasoning = "; ".join(reasons)

    def _solve_tc(self, sol: SupportingSolution) -> None:
        """Traction control from rear slip and driver consistency."""
        driver = self.driver
        measured = self.measured

        # Baseline
        tc_gain = 4
        tc_slip = 3
        reasons = ["Baseline: gain=4, slip=3"]

        # Erratic driver benefits from more TC help
        if driver.consistency == "erratic":
            tc_gain += 1
            reasons.append("+1 gain for erratic consistency")
        elif driver.consistency == "consistent":
            tc_gain -= 1
            reasons.append("-1 gain for consistent driver")

        # High rear slip → more TC intervention
        if measured.rear_slip_ratio_p95 > 0.06:
            tc_slip += 1
            reasons.append(f"+1 slip for rear slip p95={measured.rear_slip_ratio_p95:.3f}")

        # Binary throttle → more TC to protect rears
        if driver.throttle_classification == "binary":
            tc_gain += 1
            reasons.append("+1 gain for binary throttle")

        sol.tc_gain = int(_clamp(tc_gain, 1, 10))
        sol.tc_slip = int(_clamp(tc_slip, 1, 10))
        sol.tc_reasoning = "; ".join(reasons)

    def _solve_pressures(self, sol: SupportingSolution) -> None:
        """Tyre pressures targeting 155-170 kPa hot window.

        Adjusts cold starting pressure based on measured hot pressures.
        If no measured data, uses minimum safe cold pressure.
        """
        measured = self.measured

        # Hot pressure target window
        hot_low = 155.0
        hot_high = 170.0
        min_cold = 152.0  # iRacing minimum
        default_cold = 152.0

        # We only have front/rear averages from MeasuredState
        front_hot = measured.front_pressure_mean_kpa
        rear_hot = measured.rear_pressure_mean_kpa
        reasons = []

        if front_hot > 0:
            # Compute cold adjustment for front
            if front_hot > hot_high:
                adj = -(front_hot - hot_high) / 3.0
                cold_f = default_cold + adj
                reasons.append(
                    f"Front hot {front_hot:.0f} kPa > {hot_high:.0f} → "
                    f"cold {max(cold_f, min_cold):.0f} kPa"
                )
            elif front_hot < hot_low:
                adj = (hot_low - front_hot) / 3.0
                cold_f = default_cold + adj
                reasons.append(
                    f"Front hot {front_hot:.0f} kPa < {hot_low:.0f} → "
                    f"cold {cold_f:.0f} kPa"
                )
            else:
                cold_f = default_cold
                reasons.append(f"Front hot {front_hot:.0f} kPa in target window")

            sol.tyre_cold_fl_kpa = round(max(cold_f, min_cold), 0)
            sol.tyre_cold_fr_kpa = round(max(cold_f, min_cold), 0)
        else:
            reasons.append("No front pressure data — using minimum 152 kPa")

        if rear_hot > 0:
            if rear_hot > hot_high:
                adj = -(rear_hot - hot_high) / 3.0
                cold_r = default_cold + adj
                reasons.append(
                    f"Rear hot {rear_hot:.0f} kPa > {hot_high:.0f} → "
                    f"cold {max(cold_r, min_cold):.0f} kPa"
                )
            elif rear_hot < hot_low:
                adj = (hot_low - rear_hot) / 3.0
                cold_r = default_cold + adj
                reasons.append(
                    f"Rear hot {rear_hot:.0f} kPa < {hot_low:.0f} → "
                    f"cold {cold_r:.0f} kPa"
                )
            else:
                cold_r = default_cold
                reasons.append(f"Rear hot {rear_hot:.0f} kPa in target window")

            sol.tyre_cold_rl_kpa = round(max(cold_r, min_cold), 0)
            sol.tyre_cold_rr_kpa = round(max(cold_r, min_cold), 0)
        else:
            reasons.append("No rear pressure data — using minimum 152 kPa")

        sol.pressure_reasoning = "; ".join(reasons)
