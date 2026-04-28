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
    recommended_front_mc_mm: float = 0.0
    recommended_rear_mc_mm: float = 0.0
    mc_recommendation_reason: str = ""
    effective_bias_correction: float = 0.0
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
    """Brake bias solver with master-cylinder physics.

    Computes brake bias % accounting for:
    - Calibrated per-car baseline
    - Fuel load weight transfer
    - Track peak speed (aero load under braking)
    - Driver trail-braking style
    - Front lock / slip evidence
    - ABS activity
    - **Master cylinder bore sizes** (effective hydraulic force split)

    The effective front braking force fraction is NOT just the bias %:
        F_front = P_line * A_front_mc * bias_fraction
    where A = pi/4 * d^2.  If front MC is larger than rear, the actual
    front braking force exceeds what the bias % suggests.  This solver
    corrects the bias recommendation to account for the MC ratio the
    driver is actually running, and recommends optimal MC sizes.
    """

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

        # -- Track speed adaptation -----------------------------------------
        # At higher peak braking speeds, aero load shifts the weight
        # distribution forward under heavy braking, requiring more front
        # bias. At lower peak speeds, less aero = less forward shift.
        # Reference: 230 kph (typical GTP track). Scale: ~0.3% per 30 kph.
        max_speed_kph = getattr(measured, 'speed_max_kph', None) or 0.0
        if max_speed_kph > 150:
            _speed_ref_kph = 230.0
            _speed_delta = max_speed_kph - _speed_ref_kph
            _speed_adj = round(_speed_delta / 30.0 * 0.3, 1)
            _speed_adj = max(-0.5, min(0.8, _speed_adj))  # clamp
            if abs(_speed_adj) >= 0.1:
                bias += _speed_adj
                reasons.append(
                    f"{_speed_adj:+.1f}% for track peak speed "
                    f"{max_speed_kph:.0f} kph (ref 230 kph)"
                )

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
                    f"Note: low braking decel p95={measured.braking_decel_peak_g:.2f}g -- "
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
                f"Warning: High brake deceleration asymmetry ({asymmetry:.1f} m/s2) detected -- "
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
            reasons.append(f"+0.3% for high body slip p95={measured.body_slip_p95_deg:.1f} deg")

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

        # -- Master cylinder physics -----------------------------------------
        # The effective braking force split depends on BOTH the bias % knob
        # AND the front/rear MC bore diameters.  Hydraulic force = P * A,
        # and A is proportional to diameter^2.  If the driver's MC ratio
        # diverges from the physics-ideal ratio, the effective front brake
        # force fraction differs from the bias % setting.
        mc_ratio = 0.0
        mc_note = ""
        rec_front_mc = 0.0
        rec_rear_mc = 0.0
        mc_rec_reason = ""
        effective_bias_correction = 0.0
        pad = ""
        pad_note = ""

        # Compute physics-ideal MC sizes from car geometry
        nominal_ratio = self.car.nominal_mc_ratio()
        decel_for_mc = max(
            float(getattr(measured, "braking_decel_peak_g", 0.0) or 0.0),
            1.5,  # floor: even light brakers still need correct MC ratio
        )
        rec_front_mc, rec_rear_mc, mc_rec_reason = self.car.compute_ideal_mc_sizes(
            decel_g=decel_for_mc,
            fuel_load_l=self.fuel_load_l,
        )
        reasons.append(f"MC physics: {mc_rec_reason}")

        if self.current_setup is not None:
            if getattr(self.current_setup, "brake_bias_migration", 0.0) != 0.0:
                reasons.append(
                    f"Current migration {self.current_setup.brake_bias_migration:+.1f} retained as hardware context only"
                )
            if getattr(self.current_setup, "brake_bias_target", 0.0) != 0.0:
                reasons.append(
                    f"Current target {self.current_setup.brake_bias_target:+.1f} retained as hardware context only"
                )
            front_mc = float(getattr(self.current_setup, "front_master_cyl_mm", 0.0) or 0.0)
            rear_mc = float(getattr(self.current_setup, "rear_master_cyl_mm", 0.0) or 0.0)
            if front_mc > 0.0 and rear_mc > 0.0:
                mc_ratio = front_mc / rear_mc
                mc_note = f"F/R = {front_mc:.1f}/{rear_mc:.1f} mm (ratio {mc_ratio:.3f})"

                # Effective-bias correction: if the MC ratio differs from
                # nominal, the actual front brake force is higher/lower than
                # the bias % suggests.  We correct the bias recommendation
                # to compensate.
                #
                # The hydraulic force ratio = (d_front/d_rear)^2.
                # If actual ratio > ideal ratio, more front force than
                # intended, so reduce bias % to compensate (and vice versa).
                # Sensitivity: ~0.5% bias per 0.1 MC ratio deviation.
                ratio_delta = mc_ratio - nominal_ratio
                if abs(ratio_delta) > 0.02:
                    effective_bias_correction = round(-ratio_delta * 5.0, 1)
                    effective_bias_correction = _clamp(effective_bias_correction, -1.5, 1.5)
                    bias += effective_bias_correction
                    reasons.append(
                        f"MC effective-bias correction: {effective_bias_correction:+.1f}% "
                        f"(current ratio {mc_ratio:.3f} vs ideal {nominal_ratio:.3f}, "
                        f"delta {ratio_delta:+.3f})"
                    )

                # Flag suboptimal MC configuration
                if abs(ratio_delta) > 0.15:
                    mc_note += (
                        f"; !! SUBOPTIMAL: current ratio {mc_ratio:.3f} far from "
                        f"ideal {nominal_ratio:.3f} -- recommend "
                        f"F {rec_front_mc:.1f} / R {rec_rear_mc:.1f} mm"
                    )
                elif abs(ratio_delta) > 0.05:
                    direction = "increase" if mc_ratio > nominal_ratio else "decrease"
                    mc_note += (
                        f"; differs from ideal {nominal_ratio:.3f} -- "
                        f"effective bias may need {direction}; consider "
                        f"F {rec_front_mc:.1f} / R {rec_rear_mc:.1f} mm"
                    )
                reasons.append(f"MC: {mc_note}")
            else:
                mc_ratio = 0.0
                mc_note = ""
            pad = getattr(self.current_setup, "pad_compound", "") or ""
            pad_note = ""
            if pad:
                pad_note = pad
                front_temp = getattr(measured, "front_carcass_mean_c", 0.0) or 0.0
                if front_temp > 0:
                    if front_temp < 60:
                        pad_note += " -- low brake temps, softer compound may improve initial bite"
                    elif front_temp > 110:
                        pad_note += " -- high brake temps, compound fade risk"
                reasons.append(f"Pad: {pad_note}")

        return BrakeSolution(
            brake_bias_pct=round(bias, 1),
            reasoning="; ".join(reasons),
            mc_ratio=mc_ratio,
            mc_ratio_note=mc_note,
            recommended_front_mc_mm=rec_front_mc,
            recommended_rear_mc_mm=rec_rear_mc,
            mc_recommendation_reason=mc_rec_reason,
            effective_bias_correction=effective_bias_correction,
            pad_compound=pad,
            pad_compound_note=pad_note,
        )
