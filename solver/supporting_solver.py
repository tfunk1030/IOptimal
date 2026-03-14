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
    from track_model.profile import TrackProfile


@dataclass
class SupportingSolution:
    """Computed values for brake, diff, TC, and tyre pressure parameters."""

    # Brakes
    brake_bias_pct: float = 56.0
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


def compute_brake_bias(
    car: "CarModel",
    decel_g: float | None = None,
    fuel_load_l: float | None = None,
) -> tuple[float, str]:
    """Compute iRacing-calibrated brake bias (BrakePressureBias parameter).

    iRacing's BrakePressureBias is the hydraulic FRONT pressure split (%).
    It is NOT the dynamic weight transfer ratio. The rear master cylinder
    (20.6mm BMW) is already physically larger than the front (19.1mm),
    which provides the braking system's dynamic weight transfer compensation.

    Calibrated from 3 real BMW Sebring sessions:
        IBT session:   46.0%   (BrakePressureBias from telemetry)
        S1 (compliant): 46.5%  (from bmw_sebring_s1.ldx)
        S2 (locked):    46.0%  (from bmw_sebring_s2.ldx)

    Formula: bias ≈ static_front_weight_pct + forward_correction
    Where forward_correction is a small positive offset that keeps the
    front axle from locking under heavy braking. The mc size ratio
    (rear/front = 20.6/19.1 = 1.079) handles the dynamic compensation;
    this parameter stays close to static weight distribution.

    Args:
        car: Car physical model
        decel_g: Unused (kept for API compatibility)
        fuel_load_l: Fuel load for weight distribution shift (optional)

    Returns:
        (brake_bias_pct, reasoning_str)
    """
    # Use calibrated per-car value from car model.
    # iRacing BrakePressureBias = hydraulic front pressure split (%).
    # Calibrated from real IBT/LDX data — BMW: 46.0-46.5% at Sebring.
    # Small fuel-load adjustment (full tank → slightly higher front weight
    # → bias can come up ~0.3-0.5%; empty tank → bias can drop slightly).
    bias = car.brake_bias_pct

    if fuel_load_l is not None:
        # Full tank adds ~56kg (89L × 0.63 kg/L net rear-biased effect)
        # shifts front weight up slightly → allow small forward bias correction
        # Range: 89L = +0.5%, 12L = 0%
        fuel_correction = (fuel_load_l / 89.0) * 0.5
        bias = bias + fuel_correction

    reasoning = (
        f"Calibrated base: {car.brake_bias_pct:.1f}% | "
        f"Fuel correction: +{(fuel_load_l or 0) / 89.0 * 0.5:.2f}% at {fuel_load_l or 0:.0f}L | "
        f"Result: {bias:.1f}% | "
        f"Source: car_model per-car calibration (BMW: IBT=46.0%, S1=46.5%, S2=46.0%)"
    )

    bias = round(bias, 1)
    return bias, reasoning


class SupportingSolver:
    """Compute brake bias, diff, TC, and tyre pressures from physics + driver style."""

    def __init__(
        self,
        car: CarModel,
        driver: DriverProfile,
        measured: MeasuredState,
        diagnosis: Diagnosis,
        track: "TrackProfile | None" = None,
    ) -> None:
        self.car = car
        self.driver = driver
        self.measured = measured
        self.diagnosis = diagnosis
        self.track = track

    def solve(self) -> SupportingSolution:
        sol = SupportingSolution()
        self._solve_brake_bias(sol)
        self._solve_diff(sol)
        self._solve_tc(sol)
        self._solve_pressures(sol)
        return sol

    def _solve_brake_bias(self, sol: SupportingSolution) -> None:
        """Brake bias from dynamic weight transfer under braking + driver style.

        Seeds from compute_brake_bias() (physics-only), then applies
        driver-style and measured-state adjustments.
        """
        driver = self.driver
        measured = self.measured

        # Physics seed (fuel-adjusted weight transfer)
        fuel_l = getattr(self, "_fuel_load_l", None)
        bias, base_reason = compute_brake_bias(self.car, fuel_load_l=fuel_l)
        reasons = [base_reason]

        # Driver style adjustments
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

        sol.brake_bias_pct = round(bias, 1)
        sol.brake_bias_reasoning = "; ".join(reasons)

    def _solve_diff(self, sol: SupportingSolution) -> None:
        """Differential from traction demand × driver style.

        Uses DiffSolver for full physics model (preload, coast ramp, drive ramp,
        lock percentage, and handling indices). Falls back to simplified calculation
        if DiffSolver import fails.
        """
        try:
            from solver.diff_solver import DiffSolver
            diff_solver = DiffSolver(self.car)
            diff_sol = diff_solver.solve(
                driver=self.driver,
                measured=self.measured,
                track=self.track,
            )
            sol.diff_preload_nm = diff_sol.preload_nm
            sol.diff_ramp_coast = diff_sol.coast_ramp_deg
            sol.diff_ramp_drive = diff_sol.drive_ramp_deg
            sol.diff_clutch_plates = diff_sol.clutch_plates
            # Store diff solution for reporting (optional attribute)
            sol._diff_solution = diff_sol
            sol.diff_reasoning = (
                f"{diff_sol.preload_reasoning} | {diff_sol.ramp_reasoning} | "
                f"Lock: coast={diff_sol.lock_pct_coast:.1f}% "
                f"drive={diff_sol.lock_pct_drive:.1f}%"
            )
        except Exception:
            # Fallback: simplified calculation (original implementation)
            self._solve_diff_fallback(sol)

    def _solve_diff_fallback(self, sol: SupportingSolution) -> None:
        """Fallback differential solver (simplified physics, no DiffSolver dependency)."""
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
            reasons.append(f"+5 Nm for body slip p95={measured.body_slip_p95_deg:.1f} deg (lock more)")

        if driver.trail_brake_classification == "deep":
            preload -= 5
            reasons.append("-5 Nm for deep trail braking (rotation on coast)")

        if measured.rear_slip_ratio_p95 > 0.05:
            preload += 5
            reasons.append(f"+5 Nm for rear slip ratio p95={measured.rear_slip_ratio_p95:.3f}")

        sol.diff_preload_nm = round(_clamp(preload, 5.0, 40.0) / 5) * 5  # 5 Nm increments

        # ── Coast ramp ── (lower angle = more locking on coast/decel)
        coast = 45 - int(driver.trail_brake_depth_mean * 10)
        coast = round(coast / 5) * 5
        coast = int(_clamp(coast, 40, 50))
        reasons.append(f"Coast ramp: {coast} deg (from trail brake depth {driver.trail_brake_depth_mean:.2f})")

        # ── Drive ramp ── (higher angle = less locking on accel)
        drive = 65 + int(driver.throttle_progressiveness * 10)
        drive = round(drive / 5) * 5
        drive = int(_clamp(drive, 65, 75))
        reasons.append(f"Drive ramp: {drive} deg (from throttle R2={driver.throttle_progressiveness:.2f})")

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
