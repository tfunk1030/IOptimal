"""iRacing-calibrated solver for supporting parameters: brakes, diff, TC, tyre pressures.

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
    # Small fuel-load adjustment: the BMW tank sits slightly behind the dry
    # car CG, so full fuel nudges the static balance rearward and burning fuel
    # moves front weight back in. Hydraulic split follows that directionally,
    # but with much smaller magnitude than the axle-load change itself.
    bias = car.brake_bias_pct

    if fuel_load_l is not None:
        dry_mass_kg = car.mass_car_kg + car.mass_driver_kg
        dry_cg_from_front_m = car.wheelbase_m * (1.0 - car.weight_dist_front)
        fuel_mass_kg = fuel_load_l * 0.73
        fuel_cg_from_front_m = car.wheelbase_m * 0.55
        total_mass_kg = dry_mass_kg + fuel_mass_kg
        combined_cg_from_front_m = (
            dry_mass_kg * dry_cg_from_front_m + fuel_mass_kg * fuel_cg_from_front_m
        ) / max(total_mass_kg, 1e-9)
        front_weight_with_fuel = (
            car.wheelbase_m - combined_cg_from_front_m
        ) / max(car.wheelbase_m, 1e-9)
        front_weight_shift_pct = (front_weight_with_fuel - car.weight_dist_front) * 100.0

        # Keep the hydraulic correction small: brake pressure split should move
        # with front axle load directionally, but far less than 1:1.
        fuel_correction = front_weight_shift_pct * 0.4
        bias = bias + fuel_correction
    else:
        fuel_correction = 0.0

    reasoning = (
        f"Calibrated base: {car.brake_bias_pct:.1f}% | "
        f"Fuel correction: {fuel_correction:+.2f}% at {fuel_load_l or 0:.0f}L | "
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
        self._fuel_load_l = (
            measured.fuel_level_at_measurement_l
            if getattr(measured, "fuel_level_at_measurement_l", 0.0) > 0
            else None
        )

    def solve(self) -> SupportingSolution:
        sol = SupportingSolution()
        self._solve_brake_bias(sol)
        self._solve_diff(sol)
        self._solve_tc(sol)
        self._solve_pressures(sol)
        return sol

    def _solve_brake_bias(self, sol: SupportingSolution) -> None:
        """Brake bias from hydraulic split calibration + braking-phase telemetry.

        Seeds from compute_brake_bias(), then adjusts from braking lock evidence
        and entry stability. This uses hydraulic-front-split logic, not true
        brake-torque balance.
        """
        driver = self.driver
        measured = self.measured

        # Physics seed (fuel-adjusted weight transfer)
        fuel_l = getattr(self, "_fuel_load_l", None)
        bias, base_reason = compute_brake_bias(self.car, fuel_load_l=fuel_l)
        reasons = [base_reason]

        # Driver style adjustments — use quantitative trail brake depth when available,
        # fall back to classification string
        if driver.trail_brake_depth_p95 > 0:
            # Continuous scaling: 0.3 = neutral, deeper = more forward bias
            trail_adj = (driver.trail_brake_depth_p95 - 0.3) * 1.5
            trail_adj = round(_clamp(trail_adj, -0.5, 0.75), 1)
            if abs(trail_adj) >= 0.1:
                bias += trail_adj
                reasons.append(
                    f"{trail_adj:+.1f}% from trail brake depth p95={driver.trail_brake_depth_p95:.2f}"
                )
        elif driver.trail_brake_classification == "deep":
            bias += 0.5
            reasons.append("+0.5% for deep trail braking")
        elif driver.trail_brake_classification == "light":
            bias -= 0.3
            reasons.append("-0.3% for light trail braking")

        # Braking deceleration validation
        if measured.braking_decel_peak_g > 0:
            # Forward weight transfer under braking: ΔW_f = m*a*h/L
            # Higher decel → more forward weight → front needs more capacity
            if measured.braking_decel_peak_g > 2.0:
                bias += 0.2
                reasons.append(
                    f"+0.2% for high braking decel p95={measured.braking_decel_peak_g:.2f}g"
                )
            elif measured.braking_decel_peak_g < 1.2:
                reasons.append(
                    f"Note: low braking decel p95={measured.braking_decel_peak_g:.2f}g — "
                    f"driver may not be braking hard enough to reveal bias issues"
                )

        front_lock = (
            measured.front_braking_lock_ratio_p95
            if measured.front_braking_lock_ratio_p95 > 0
            else measured.front_slip_ratio_p95
        )
        if front_lock > 0.06:
            bias -= 0.5
            reasons.append(f"-0.5% for front braking lock proxy p95={front_lock:.3f}")

        if measured.front_brake_wheel_decel_asymmetry_p95_ms2 > 3.0:
            bias -= 0.2
            reasons.append(
                f"-0.2% for front brake wheel decel asymmetry "
                f"p95={measured.front_brake_wheel_decel_asymmetry_p95_ms2:.1f} m/s^2"
            )

        # Rear instability under braking → shift forward
        if measured.body_slip_p95_deg > 5.0:
            bias += 0.3
            reasons.append(f"+0.3% for high body slip p95={measured.body_slip_p95_deg:.1f}°")

        # Measured brake pressure split validation (synchronous per-sample split)
        measured_split = getattr(measured, "hydraulic_brake_split_pct", 0.0)
        if measured_split > 0:
            if abs(measured_split - bias) > 2.0:
                reasons.append(
                    f"Note: measured hydraulic split {measured_split:.1f}% vs "
                    f"recommended {bias:.1f}% (delta {measured_split - bias:+.1f}%)"
                )

        # ABS engagement feedback: Use BrakeABScutPct for exact bias dialing
        if measured.abs_active_pct > 5.0:
            if measured.abs_cut_mean_pct > 15.0:
                # The ABS is cutting >15% of brake force on the front axle.
                # We need to shift bias rearwards aggressively.
                bias -= 0.5
                reasons.append(
                    f"-0.5% for ABS active {measured.abs_active_pct:.0f}% "
                    f"with aggressive {measured.abs_cut_mean_pct:.0f}% force cut (front locking)"
                )
            elif measured.abs_cut_mean_pct > 5.0:
                # Minor intervention, shift back slightly
                bias -= 0.2
                reasons.append(
                    f"-0.2% for ABS active {measured.abs_active_pct:.0f}% "
                    f"with {measured.abs_cut_mean_pct:.0f}% force cut"
                )

        sol.brake_bias_pct = round(bias, 1)
        sol.brake_bias_reasoning = "; ".join(reasons)

    def _solve_diff(self, sol: SupportingSolution) -> None:
        """Differential from traction demand × driver style.

        Uses DiffSolver for the empirical BMW-first model (preload, coast ramp, drive ramp,
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

        rear_power_slip = (
            measured.rear_power_slip_ratio_p95
            if measured.rear_power_slip_ratio_p95 > 0
            else measured.rear_slip_ratio_p95
        )
        if rear_power_slip > 0.05:
            preload += 5
            reasons.append(f"+5 Nm for rear power slip p95={rear_power_slip:.3f}")

        sol.diff_preload_nm = round(_clamp(preload, 0.0, 150.0) / 5) * 5  # 5 Nm increments

        # ── Coast ramp ── (lower angle = more locking on coast/decel)
        coast = 45 - int(driver.trail_brake_depth_mean * 10)
        coast = round(coast / 5) * 5
        coast = int(_clamp(coast, 40, 50))
        reasons.append(f"Coast ramp: {coast} deg (from trail brake depth {driver.trail_brake_depth_mean:.2f})")

        # ── Drive ramp ── (higher angle = less locking on accel)
        drive = 65 + int(driver.throttle_progressiveness * 10)
        # Throttle onset rate: faster onset → more abrupt power → open diff more (higher ramp)
        onset_rate = driver.throttle_onset_rate_pct_per_s
        if onset_rate > 300:
            drive += 5
            reasons.append(f"Drive ramp +5° for fast throttle onset {onset_rate:.0f}%/s")
        drive = round(drive / 5) * 5
        drive = int(_clamp(drive, 65, 75))
        reasons.append(f"Drive ramp: {drive} deg (from throttle R2={driver.throttle_progressiveness:.2f})")

        sol.diff_ramp_coast = coast
        sol.diff_ramp_drive = drive
        sol.diff_clutch_plates = self.car.garage_ranges.diff_clutch_plates_options[-1]  # highest available
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
        rear_power_slip = (
            measured.rear_power_slip_ratio_p95
            if measured.rear_power_slip_ratio_p95 > 0
            else measured.rear_slip_ratio_p95
        )
        if rear_power_slip > 0.06:
            tc_slip += 1
            reasons.append(f"+1 slip for rear power slip p95={rear_power_slip:.3f}")

        # Binary throttle → more TC to protect rears
        if driver.throttle_classification == "binary":
            tc_gain += 1
            reasons.append("+1 gain for binary throttle")

        # TC intervention feedback from telemetry
        if measured.tc_intervention_pct > 30:
            reasons.append(
                f"Warning: TC intervening {measured.tc_intervention_pct:.0f}% of time "
                f"— consider lower gain if driver finds it intrusive"
            )
        elif measured.tc_intervention_pct < 5 and rear_power_slip > 0.04:
            tc_gain += 1
            reasons.append(
                f"+1 gain: TC barely active ({measured.tc_intervention_pct:.0f}%) "
                f"but rear slip p95={rear_power_slip:.3f}"
            )

        # ERS/hybrid torque feedback
        if measured.mguk_torque_peak_nm > 200:
            tc_slip += 1
            reasons.append(
                f"+1 slip for high MGU-K torque {measured.mguk_torque_peak_nm:.0f} Nm"
            )
        if 0 < measured.ers_battery_min_pct < 20:
            reasons.append(
                f"Note: ERS depleted to {measured.ers_battery_min_pct:.0f}% — "
                f"late-stint TC may be too aggressive"
            )

        # ABS engagement may indicate TC should catch wheelspin earlier
        if measured.abs_active_pct > 15 and measured.abs_cut_mean_pct > 15:
            reasons.append(
                f"Note: ABS active {measured.abs_active_pct:.0f}% — "
                f"check if rear-end instability triggers front lock under braking"
            )

        sol.tc_gain = int(_clamp(tc_gain, 1, 10))
        sol.tc_slip = int(_clamp(tc_slip, 1, 10))
        sol.tc_reasoning = "; ".join(reasons)

    def _solve_pressures(self, sol: SupportingSolution) -> None:
        """Tyre pressures targeting 155-170 kPa hot window.

        Uses per-corner hot pressures when available (preserves left-right split).
        Applies track temperature correction: ~0.3 kPa cold adjustment per °C
        difference from 30°C reference (hotter track → lower cold start).
        """
        measured = self.measured

        # Hot pressure target window
        hot_low = 155.0
        hot_high = 170.0
        hot_target = (hot_low + hot_high) / 2.0
        min_cold = 152.0  # iRacing minimum
        default_cold = 152.0
        reasons = []

        # Track temperature correction: hotter track → tyres heat more → start lower
        # Reference: 30°C track temp. Correction: ~0.3 kPa per °C difference.
        track_temp_correction = 0.0
        if measured.track_temp_c > 0:
            track_temp_correction = -(measured.track_temp_c - 30.0) * 0.3
            if abs(track_temp_correction) > 0.5:
                reasons.append(
                    f"Track temp {measured.track_temp_c:.0f}°C → "
                    f"cold adj {track_temp_correction:+.1f} kPa"
                )

        def _cold_from_hot(hot_kpa: float) -> float:
            """Compute cold target from measured hot pressure."""
            if hot_kpa > hot_high:
                adj = -(hot_kpa - hot_high) / 3.0
            elif hot_kpa < hot_low:
                adj = (hot_low - hot_kpa) / 3.0
            else:
                adj = 0.0
            return max(default_cold + adj + track_temp_correction, min_cold)

        # Per-corner pressures (if available)
        lf_hot = measured.lf_pressure_kpa
        rf_hot = measured.rf_pressure_kpa
        lr_hot = measured.lr_pressure_kpa
        rr_hot = measured.rr_pressure_kpa

        if lf_hot > 0 and rf_hot > 0:
            sol.tyre_cold_fl_kpa = round(_cold_from_hot(lf_hot), 0)
            sol.tyre_cold_fr_kpa = round(_cold_from_hot(rf_hot), 0)
            if abs(lf_hot - rf_hot) > 3:
                reasons.append(
                    f"LF/RF hot split: {lf_hot:.0f}/{rf_hot:.0f} kPa → "
                    f"cold {sol.tyre_cold_fl_kpa:.0f}/{sol.tyre_cold_fr_kpa:.0f}"
                )
            else:
                reasons.append(f"Front hot {(lf_hot+rf_hot)/2:.0f} kPa → cold {sol.tyre_cold_fl_kpa:.0f} kPa")
        elif measured.front_pressure_mean_kpa > 0:
            cold_f = _cold_from_hot(measured.front_pressure_mean_kpa)
            sol.tyre_cold_fl_kpa = round(cold_f, 0)
            sol.tyre_cold_fr_kpa = round(cold_f, 0)
            reasons.append(f"Front hot {measured.front_pressure_mean_kpa:.0f} kPa → cold {cold_f:.0f} kPa")
        else:
            reasons.append("No front pressure data — using minimum 152 kPa")

        if lr_hot > 0 and rr_hot > 0:
            sol.tyre_cold_rl_kpa = round(_cold_from_hot(lr_hot), 0)
            sol.tyre_cold_rr_kpa = round(_cold_from_hot(rr_hot), 0)
            if abs(lr_hot - rr_hot) > 3:
                reasons.append(
                    f"LR/RR hot split: {lr_hot:.0f}/{rr_hot:.0f} kPa → "
                    f"cold {sol.tyre_cold_rl_kpa:.0f}/{sol.tyre_cold_rr_kpa:.0f}"
                )
            else:
                reasons.append(f"Rear hot {(lr_hot+rr_hot)/2:.0f} kPa → cold {sol.tyre_cold_rl_kpa:.0f} kPa")
        elif measured.rear_pressure_mean_kpa > 0:
            cold_r = _cold_from_hot(measured.rear_pressure_mean_kpa)
            sol.tyre_cold_rl_kpa = round(cold_r, 0)
            sol.tyre_cold_rr_kpa = round(cold_r, 0)
            reasons.append(f"Rear hot {measured.rear_pressure_mean_kpa:.0f} kPa → cold {cold_r:.0f} kPa")
        else:
            reasons.append("No rear pressure data — using minimum 152 kPa")

        sol.pressure_reasoning = "; ".join(reasons)
