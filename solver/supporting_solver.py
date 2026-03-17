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
    brake_bias_target: float = 0.0
    brake_bias_migration: float = 0.0
    front_master_cyl_mm: float = 0.0
    rear_master_cyl_mm: float = 0.0
    pad_compound: str = ""
    brake_hardware_status: str = "pass-through only"
    brake_bias_status: str = "solved"
    brake_bias_target_status: str = "pass-through"
    brake_bias_migration_status: str = "pass-through"
    master_cylinder_status: str = "pass-through"
    pad_compound_status: str = "pass-through"

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
            f"Brake bias: {self.brake_bias_pct:.1f}% [{self.brake_bias_status}]",
            f"  {self.brake_bias_reasoning}",
            f"Brake target/migration: {self.brake_bias_target:+.1f} / {self.brake_bias_migration:+.1f} "
            f"[{self.brake_bias_target_status}/{self.brake_bias_migration_status}]",
            f"  Master cylinders: F {self.front_master_cyl_mm:.1f} mm / R {self.rear_master_cyl_mm:.1f} mm "
            f"[{self.master_cylinder_status}] | Pad: {self.pad_compound or 'unknown'} [{self.pad_compound_status}]",
            f"  Brake hardware status: {self.brake_hardware_status}",
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
from solver.brake_solver import BrakeSolver, compute_brake_bias


class SupportingSolver:
    """Compute brake bias, diff, TC, and tyre pressures from physics + driver style."""

    def __init__(
        self,
        car: CarModel,
        driver: DriverProfile,
        measured: MeasuredState,
        diagnosis: Diagnosis,
        track: "TrackProfile | None" = None,
        current_setup: object | None = None,
    ) -> None:
        self.car = car
        self.driver = driver
        self.measured = measured
        self.diagnosis = diagnosis
        self.track = track
        self.current_setup = current_setup
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
        brake_solution = BrakeSolver(
            car=self.car,
            driver=self.driver,
            measured=self.measured,
            diagnosis=self.diagnosis,
            current_setup=self.current_setup,
            fuel_load_l=getattr(self, "_fuel_load_l", None),
        ).solve()
        sol.brake_bias_pct = brake_solution.brake_bias_pct
        sol.brake_bias_reasoning = brake_solution.reasoning
        sol._brake_solution = brake_solution
        sol.brake_bias_target = getattr(self.current_setup, "brake_bias_target", 0.0) or 0.0
        sol.brake_bias_migration = getattr(self.current_setup, "brake_bias_migration", 0.0) or 0.0
        sol.front_master_cyl_mm = getattr(self.current_setup, "front_master_cyl_mm", 0.0) or 0.0
        sol.rear_master_cyl_mm = getattr(self.current_setup, "rear_master_cyl_mm", 0.0) or 0.0
        sol.pad_compound = getattr(self.current_setup, "pad_compound", "") or ""
        sol.brake_bias_status = "solved"
        sol.brake_bias_target_status = "pass-through"
        sol.brake_bias_migration_status = "pass-through"
        sol.master_cylinder_status = "pass-through"
        sol.pad_compound_status = "pass-through"
        sol.brake_hardware_status = (
            "Static brake bias is solved from telemetry; brake target, migration, "
            "master cylinders, and pad compound are pass-through hardware context."
        )
        if getattr(self.car, "canonical_name", "") == "ferrari":
            live_bias = getattr(self.measured, "live_brake_bias_pct", None)
            setup_bias = getattr(self.current_setup, "brake_bias_pct", 0.0) or 0.0
            if live_bias is not None:
                sol.brake_bias_pct = float(live_bias)
                sol.brake_bias_status = "telemetry_passthrough"
                sol.brake_bias_reasoning = (
                    f"Ferrari brake bias taken from stable dcBrakeBias telemetry ({live_bias:.1f}%). "
                    "Hydraulic split remains diagnostic only."
                )
            elif setup_bias > 0.0:
                sol.brake_bias_pct = float(setup_bias)
                sol.brake_bias_status = "setup_passthrough"
                sol.brake_bias_reasoning = (
                    f"Ferrari brake bias taken from setup session value ({setup_bias:.1f}%). "
                    "Hydraulic split remains diagnostic only."
                )
            sol.brake_hardware_status = (
                "Ferrari brake bias is sourced from stable live control or setup context; "
                "hardware fields remain pass-through."
            )

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
                current_clutch_plates=getattr(self.current_setup, "diff_clutch_plates", 0) or None,
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
                f"drive={diff_sol.lock_pct_drive:.1f}% "
                f"(preload {diff_sol.preload_contribution_pct:.1f}% + plates {diff_sol.plate_contribution_pct:.1f}%)"
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

        # ── Ramp angles ── must be a valid garage pair: (40,65), (45,70), (50,75)
        valid_pairs = getattr(self.car.garage_ranges, "diff_coast_drive_ramp_options",
                              [(40, 65), (45, 70), (50, 75)])

        # Score each pair based on driver style
        raw_coast = 45 - int(driver.trail_brake_depth_mean * 10)
        raw_drive = 65 + int(driver.throttle_progressiveness * 10)
        onset_rate = driver.throttle_onset_rate_pct_per_s
        if onset_rate > 300:
            raw_drive += 5
            reasons.append(f"Drive ramp +5° for fast throttle onset {onset_rate:.0f}%/s")

        # Pick the valid pair closest to the raw targets
        best_pair = min(valid_pairs, key=lambda p: abs(p[0] - raw_coast) + abs(p[1] - raw_drive))
        coast, drive = best_pair
        reasons.append(f"Ramp pair: {coast}/{drive} deg (from trail brake depth "
                       f"{driver.trail_brake_depth_mean:.2f}, throttle R2={driver.throttle_progressiveness:.2f})")

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
        if getattr(self.car, "canonical_name", "") == "ferrari":
            live_gain = getattr(measured, "live_tc_gain", None)
            live_slip = getattr(measured, "live_tc_slip", None)
            ferrari_reasons: list[str] = []
            if live_gain is not None:
                sol.tc_gain = int(live_gain)
                ferrari_reasons.append(f"TC gain taken from stable dcTractionControl2={int(live_gain)}")
            elif getattr(self.current_setup, "tc_gain", 0):
                sol.tc_gain = int(getattr(self.current_setup, "tc_gain"))
                ferrari_reasons.append(f"TC gain taken from Ferrari setup value {sol.tc_gain}")
            if live_slip is not None:
                sol.tc_slip = int(live_slip)
                ferrari_reasons.append(f"TC slip taken from stable dcTractionControl={int(live_slip)}")
            elif getattr(self.current_setup, "tc_slip", 0):
                sol.tc_slip = int(getattr(self.current_setup, "tc_slip"))
                ferrari_reasons.append(f"TC slip taken from Ferrari setup value {sol.tc_slip}")
            if ferrari_reasons:
                sol.tc_reasoning = "; ".join(ferrari_reasons)

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
