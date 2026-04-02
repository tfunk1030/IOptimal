from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analyzer.diagnose import Diagnosis
    from analyzer.driver_style import DriverProfile
    from analyzer.extract import MeasuredState
    from analyzer.setup_reader import CurrentSetup
    from car_model.cars import CarModel


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class BrakeSolution:
    brake_bias_pct: float
    reasoning: str
    mc_ratio: float = 0.0
    mc_ratio_note: str = ""
    pad_compound: str = ""
    pad_compound_note: str = ""


def compute_brake_bias(
    car: "CarModel",
    decel_g: float | None = None,
    fuel_load_l: float | None = None,
) -> tuple[float, str]:
    """Compute iRacing-calibrated hydraulic brake bias baseline."""
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
        front_weight_with_fuel = (car.wheelbase_m - combined_cg_from_front_m) / max(car.wheelbase_m, 1e-9)
        front_weight_shift_pct = (front_weight_with_fuel - car.weight_dist_front) * 100.0
        fuel_correction = front_weight_shift_pct * 0.4
        bias = bias + fuel_correction
    else:
        fuel_correction = 0.0

    reasoning = (
        f"Calibrated base: {car.brake_bias_pct:.1f}% | "
        f"Fuel correction: {fuel_correction:+.2f}% at {fuel_load_l or 0:.0f}L | "
        f"Result: {bias:.1f}%"
    )
    return round(bias, 1), reasoning


class BrakeSolver:
    """Brake bias solver extracted from SupportingSolver."""

    def __init__(
        self,
        car: "CarModel",
        driver: "DriverProfile",
        measured: "MeasuredState",
        diagnosis: "Diagnosis | None" = None,
        current_setup: "CurrentSetup | None" = None,
        fuel_load_l: float | None = None,
    ) -> None:
        self.car = car
        self.driver = driver
        self.measured = measured
        self.diagnosis = diagnosis
        self.current_setup = current_setup
        self.fuel_load_l = fuel_load_l

    def solve(self) -> BrakeSolution:
        driver = self.driver
        measured = self.measured
        bias, base_reason = compute_brake_bias(self.car, fuel_load_l=self.fuel_load_l)
        reasons = [base_reason]

        if driver.trail_brake_depth_p95 > 0:
            trail_adj = (driver.trail_brake_depth_p95 - 0.3) * 1.5
            trail_adj = round(_clamp(trail_adj, -0.5, 0.75), 1)
            if abs(trail_adj) >= 0.1:
                bias += trail_adj
                reasons.append(f"{trail_adj:+.1f}% from trail brake depth p95={driver.trail_brake_depth_p95:.2f}")
        elif driver.trail_brake_classification == "deep":
            bias += 0.5
            reasons.append("+0.5% for deep trail braking")
        elif driver.trail_brake_classification == "light":
            bias -= 0.3
            reasons.append("-0.3% for light trail braking")

        if measured.braking_decel_peak_g > 0:
            if measured.braking_decel_peak_g > 2.0:
                bias += 0.2
                reasons.append(f"+0.2% for high braking decel p95={measured.braking_decel_peak_g:.2f}g")
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

        # Front brake asymmetry: hardware warning at lower threshold, bias adjustment at higher
        asymmetry = getattr(measured, 'front_brake_wheel_decel_asymmetry_p95_ms2', 0.0)
        if asymmetry > 1.5:
            reasons.append(
                f"⚠ High brake deceleration asymmetry ({asymmetry:.1f} m/s²) detected — "
                f"check front caliper/pad condition before adjusting brake bias"
            )
        if asymmetry > 3.0:
            bias -= 0.2
            reasons.append(
                f"-0.2% for front brake wheel decel asymmetry "
                f"p95={asymmetry:.1f} m/s^2"
            )

        if measured.body_slip_p95_deg > 5.0:
            bias += 0.3
            reasons.append(f"+0.3% for high body slip p95={measured.body_slip_p95_deg:.1f}°")

        measured_split = getattr(measured, "hydraulic_brake_split_pct", 0.0)
        if measured_split > 0 and abs(measured_split - bias) > 2.0:
            reasons.append(
                f"Note: measured hydraulic split {measured_split:.1f}% vs "
                f"recommended {bias:.1f}% (delta {measured_split - bias:+.1f}%)"
            )

        if measured.abs_active_pct > 10.0 and measured.abs_cut_mean_pct > 20.0:
            bias -= 0.3
            reasons.append(
                f"-0.3% for ABS active {measured.abs_active_pct:.0f}% "
                f"with {measured.abs_cut_mean_pct:.0f}% force cut (front locking)"
            )

        mc_ratio = 0.0
        mc_note = ""
        pad = ""
        pad_note = ""

        if self.current_setup is not None:
            if getattr(self.current_setup, "brake_bias_migration", 0.0) != 0.0:
                reasons.append(
                    f"Current migration {self.current_setup.brake_bias_migration:+.1f} retained as hardware context only"
                )
            if getattr(self.current_setup, "brake_bias_target", 0.0) != 0.0:
                reasons.append(
                    f"Current target {self.current_setup.brake_bias_target:+.1f} retained as hardware context only"
                )
            front_mc = getattr(self.current_setup, "front_master_cyl_mm", 0.0)
            rear_mc = getattr(self.current_setup, "rear_master_cyl_mm", 0.0)
            if front_mc > 0.0 and rear_mc > 0.0:
                mc_ratio = front_mc / rear_mc
                nominal_ratio = getattr(self.car, "nominal_mc_ratio", 1.0)
                mc_note = f"F/R = {front_mc:.1f}/{rear_mc:.1f} mm (ratio {mc_ratio:.2f})"
                if abs(mc_ratio - nominal_ratio) > 0.05:
                    direction = "increase" if mc_ratio > nominal_ratio else "decrease"
                    mc_note += (
                        f"; differs from nominal {nominal_ratio:.2f} — "
                        f"effective bias may need {direction}"
                    )
                reasons.append(f"MC: {mc_note}")
            else:
                mc_ratio = 0.0
                mc_note = ""
            pad = getattr(self.current_setup, "pad_compound", "")
            pad_note = ""
            if pad:
                pad_note = pad
                front_temp = getattr(measured, "front_carcass_mean_c", 0.0) or 0.0
                if front_temp > 0:
                    if front_temp < 60:
                        pad_note += " — low brake temps, softer compound may improve initial bite"
                    elif front_temp > 110:
                        pad_note += " — high brake temps, compound fade risk"
                reasons.append(f"Pad: {pad_note}")

        return BrakeSolution(
            brake_bias_pct=round(bias, 1),
            reasoning="; ".join(reasons),
            mc_ratio=mc_ratio,
            mc_ratio_note=mc_note,
            pad_compound=pad,
            pad_compound_note=pad_note,
        )
