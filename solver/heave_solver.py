"""Step 2: Heave/Third Spring Solver.

Finds the minimum front heave spring rate and rear third spring rate that
keep clean-track bottoming events at zero at the 99th percentile of ride
height excursion at speed.

Physics:
    Track surface bumps excite the suspension, causing ride height to oscillate
    around its mean dynamic value. Stiffer heave/third springs reduce this
    oscillation but also reduce mechanical grip (harsher ride). The solver
    finds the minimum stiffness that prevents bottoming.

    The excursion model:
        excursion(k) = v_p99 * sqrt(m_eff / k)

    Where:
        v_p99 = 99th percentile shock velocity from track profile (m/s)
        m_eff = calibrated effective heave mass (kg)
        k     = spring rate (N/m)

    This comes from energy conservation: the kinetic energy of the effective
    mass at the shock velocity equals the potential energy stored in the spring
    at maximum compression: 0.5 * m_eff * v^2 = 0.5 * k * x^2.

    Two constraints per axle:
    - Bottoming: excursion_p99 < dynamic_RH
      Front is bottoming-constrained (dynamic RH ~15mm is the limiting factor)
    - Variance: sigma = excursion_p99 / 2.33 < sigma_target
      Rear is variance-constrained (platform stability at high speed)
      The 2.33 factor: for a Gaussian, p99 = mean + 2.33*sigma

    The solver picks the binding constraint (whichever requires stiffer spring).

Validated against BMW Sebring telemetry:
    - Front heave 50 N/mm: excursion = 14.9mm = dynamic RH (boundary) -> OK
    - Front heave 30 N/mm: excursion = 19.2mm > 14.9mm -> bottoming by 4.3mm
    - Rear third 530 N/mm: sigma = 9.9mm <= 10mm target -> OK
    - Rear third minimum for no bottoming: 177 N/mm (variance is binding)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from car_model.cars import CarModel
from track_model.profile import TrackProfile


@dataclass
class HeaveSolution:
    """Output of the Step 2 heave/third spring solver."""

    # Recommended spring rates
    front_heave_nmm: float
    rear_third_nmm: float

    # Front constraint analysis
    front_dynamic_rh_mm: float       # From Step 1
    front_shock_vel_p99_mps: float
    front_excursion_at_rate_mm: float  # Excursion at recommended rate
    front_bottoming_margin_mm: float   # dynamic_rh - excursion (must be >= 0)
    front_sigma_at_rate_mm: float
    front_binding_constraint: str      # "bottoming" or "variance"

    # Rear constraint analysis
    rear_dynamic_rh_mm: float
    rear_shock_vel_p99_mps: float
    rear_excursion_at_rate_mm: float
    rear_bottoming_margin_mm: float
    rear_sigma_at_rate_mm: float
    rear_binding_constraint: str

    # Perch offsets (baseline reference)
    perch_offset_front_mm: float
    perch_offset_rear_mm: float

    # Safety check results
    safety_checks: list[SpringSafetyCheck]

    def summary(self) -> str:
        """Human-readable summary of the solution."""
        lines = [
            "===========================================================",
            "  STEP 2: HEAVE / THIRD SPRING SOLUTION",
            "===========================================================",
            "",
            "  RECOMMENDED SPRING RATES",
            f"    Front heave:   {self.front_heave_nmm:6.0f} N/mm",
            f"    Rear third:    {self.rear_third_nmm:6.0f} N/mm",
            "",
            "  FRONT AXLE ANALYSIS",
            f"    Dynamic RH:          {self.front_dynamic_rh_mm:5.1f} mm",
            f"    Shock vel p99:       {self.front_shock_vel_p99_mps:.4f} m/s",
            f"    Excursion at rate:   {self.front_excursion_at_rate_mm:5.1f} mm",
            f"    Bottoming margin:    {self.front_bottoming_margin_mm:5.1f} mm  "
            + ("OK" if self.front_bottoming_margin_mm >= 0 else "BOTTOMING"),
            f"    Sigma at rate:       {self.front_sigma_at_rate_mm:5.1f} mm",
            f"    Binding constraint:  {self.front_binding_constraint}",
            "",
            "  REAR AXLE ANALYSIS",
            f"    Dynamic RH:          {self.rear_dynamic_rh_mm:5.1f} mm",
            f"    Shock vel p99:       {self.rear_shock_vel_p99_mps:.4f} m/s",
            f"    Excursion at rate:   {self.rear_excursion_at_rate_mm:5.1f} mm",
            f"    Bottoming margin:    {self.rear_bottoming_margin_mm:5.1f} mm  "
            + ("OK" if self.rear_bottoming_margin_mm >= 0 else "BOTTOMING"),
            f"    Sigma at rate:       {self.rear_sigma_at_rate_mm:5.1f} mm",
            f"    Binding constraint:  {self.rear_binding_constraint}",
            "",
            "  PERCH OFFSETS (baseline reference)",
            f"    Front:  {self.perch_offset_front_mm:6.0f} mm",
            f"    Rear:   {self.perch_offset_rear_mm:6.0f} mm",
        ]

        if self.safety_checks:
            lines += [
                "",
                "  SAFETY CHECKS",
            ]
            for check in self.safety_checks:
                status = "OK" if check.safe else "REJECTED"
                lines.append(f"    {check.label}: {status}")
                if not check.safe:
                    lines.append(f"      {check.reason}")

        lines += [
            "===========================================================",
        ]
        return "\n".join(lines)


@dataclass
class SpringSafetyCheck:
    """Result of checking an arbitrary spring rate against constraints."""
    label: str
    rate_nmm: float
    axle: str                  # "front" or "rear"
    excursion_mm: float
    dynamic_rh_mm: float
    bottoming_mm: float        # How far past RH=0 (positive = bottoming)
    sigma_mm: float
    sigma_target_mm: float
    safe: bool
    reason: str


class HeaveSolver:
    """Step 2 solver: find minimum heave/third spring rates.

    Uses the calibrated excursion model:
        excursion(k) = v_p99 * sqrt(m_eff / k)

    to find the minimum spring rate that satisfies:
        1. No bottoming: excursion < dynamic_RH
        2. Platform stability: sigma = excursion/2.33 < sigma_target
    """

    def __init__(self, car: CarModel, track: TrackProfile):
        self.car = car
        self.track = track

    def excursion(self, v_p99_mps: float, m_eff_kg: float, k_nmm: float) -> float:
        """Calculate p99 ride height excursion (mm).

        Args:
            v_p99_mps: p99 shock velocity in m/s
            m_eff_kg: effective heave mass in kg
            k_nmm: spring rate in N/mm

        Returns:
            Excursion in mm
        """
        k_nm = k_nmm * 1000.0  # N/mm -> N/m
        # excursion = v * sqrt(m/k), result in meters, convert to mm
        return v_p99_mps * math.sqrt(m_eff_kg / k_nm) * 1000.0

    def sigma_from_excursion(self, excursion_mm: float) -> float:
        """Convert p99 excursion to standard deviation (sigma).

        For Gaussian: p99 = mean + 2.33*sigma, so sigma = excursion/2.33.
        """
        return excursion_mm / 2.33

    def min_rate_for_no_bottoming(
        self, v_p99_mps: float, m_eff_kg: float, dynamic_rh_mm: float
    ) -> float:
        """Minimum spring rate (N/mm) to prevent bottoming.

        Solve: v_p99 * sqrt(m_eff / k) * 1000 = dynamic_rh_mm
        -> k = m_eff * (v_p99 * 1000 / dynamic_rh_mm)^2
        -> k_nmm = k / 1000
        """
        if dynamic_rh_mm <= 0:
            return float("inf")
        v_mm = v_p99_mps * 1000.0  # m/s -> mm/s
        k_nm = m_eff_kg * (v_mm / dynamic_rh_mm) ** 2
        return k_nm / 1000.0  # N/m -> N/mm

    def min_rate_for_sigma(
        self, v_p99_mps: float, m_eff_kg: float, sigma_target_mm: float
    ) -> float:
        """Minimum spring rate (N/mm) to keep sigma below target.

        sigma = excursion / 2.33, so excursion_limit = sigma_target * 2.33
        Then solve: v_p99 * sqrt(m_eff / k) * 1000 = excursion_limit
        """
        excursion_limit = sigma_target_mm * 2.33
        if excursion_limit <= 0:
            return float("inf")
        v_mm = v_p99_mps * 1000.0
        k_nm = m_eff_kg * (v_mm / excursion_limit) ** 2
        return k_nm / 1000.0

    def check_spring_rate(
        self,
        rate_nmm: float,
        axle: str,
        dynamic_rh_mm: float,
        label: str = "",
    ) -> SpringSafetyCheck:
        """Check if a given spring rate is safe for the specified axle.

        Args:
            rate_nmm: Spring rate to check (N/mm)
            axle: "front" or "rear"
            dynamic_rh_mm: Dynamic ride height at speed (mm)
            label: Human-readable label for this check
        """
        hsm = self.car.heave_spring
        if axle == "front":
            v_p99 = self.track.shock_vel_p99_front_mps
            m_eff = hsm.front_m_eff_kg
        else:
            v_p99 = self.track.shock_vel_p99_rear_mps
            m_eff = hsm.rear_m_eff_kg

        exc = self.excursion(v_p99, m_eff, rate_nmm)
        sigma = self.sigma_from_excursion(exc)
        bottoming = exc - dynamic_rh_mm  # positive = bottoming

        safe = True
        reasons = []
        if bottoming > 0:
            safe = False
            reasons.append(
                f"Bottoming by {bottoming:.1f}mm "
                f"(excursion {exc:.1f}mm > RH {dynamic_rh_mm:.1f}mm)"
            )
        if sigma > hsm.sigma_target_mm:
            safe = False
            reasons.append(
                f"Sigma {sigma:.1f}mm > target {hsm.sigma_target_mm:.1f}mm"
            )

        return SpringSafetyCheck(
            label=label or f"{axle} {rate_nmm:.0f} N/mm",
            rate_nmm=rate_nmm,
            axle=axle,
            excursion_mm=round(exc, 1),
            dynamic_rh_mm=round(dynamic_rh_mm, 1),
            bottoming_mm=round(max(0, bottoming), 1),
            sigma_mm=round(sigma, 1),
            sigma_target_mm=hsm.sigma_target_mm,
            safe=safe,
            reason="; ".join(reasons) if reasons else "Within all constraints",
        )

    def solve(
        self,
        dynamic_front_rh_mm: float,
        dynamic_rear_rh_mm: float,
    ) -> HeaveSolution:
        """Find minimum safe heave/third spring rates.

        Args:
            dynamic_front_rh_mm: Front dynamic ride height from Step 1
            dynamic_rear_rh_mm: Rear dynamic ride height from Step 1

        Returns:
            HeaveSolution with recommended rates and constraint analysis
        """
        hsm = self.car.heave_spring

        # --- Front axle ---
        v_front = self.track.shock_vel_p99_front_mps
        m_front = hsm.front_m_eff_kg

        k_front_bottoming = self.min_rate_for_no_bottoming(
            v_front, m_front, dynamic_front_rh_mm
        )
        k_front_sigma = self.min_rate_for_sigma(
            v_front, m_front, hsm.sigma_target_mm
        )

        if k_front_bottoming >= k_front_sigma:
            k_front = k_front_bottoming
            front_binding = "bottoming"
        else:
            k_front = k_front_sigma
            front_binding = "variance"

        # Clamp to valid range and round up to nearest 10 N/mm (iRacing garage step)
        k_front = max(k_front, hsm.front_spring_range_nmm[0])
        k_front = min(k_front, hsm.front_spring_range_nmm[1])
        k_front = math.ceil(k_front / 10) * 10

        front_exc = self.excursion(v_front, m_front, k_front)
        front_sigma = self.sigma_from_excursion(front_exc)

        # --- Rear axle ---
        v_rear = self.track.shock_vel_p99_rear_mps
        m_rear = hsm.rear_m_eff_kg

        k_rear_bottoming = self.min_rate_for_no_bottoming(
            v_rear, m_rear, dynamic_rear_rh_mm
        )
        k_rear_sigma = self.min_rate_for_sigma(
            v_rear, m_rear, hsm.sigma_target_mm
        )

        if k_rear_bottoming >= k_rear_sigma:
            k_rear = k_rear_bottoming
            rear_binding = "bottoming"
        else:
            k_rear = k_rear_sigma
            rear_binding = "variance"

        # Clamp and round up to nearest 10 N/mm (iRacing garage step)
        k_rear = max(k_rear, hsm.rear_spring_range_nmm[0])
        k_rear = min(k_rear, hsm.rear_spring_range_nmm[1])
        k_rear = math.ceil(k_rear / 10) * 10

        rear_exc = self.excursion(v_rear, m_rear, k_rear)
        rear_sigma = self.sigma_from_excursion(rear_exc)

        # --- Safety checks ---
        safety_checks = []

        # Check the recommended rates
        safety_checks.append(
            self.check_spring_rate(
                k_front, "front", dynamic_front_rh_mm,
                f"Recommended front heave {k_front} N/mm"
            )
        )
        safety_checks.append(
            self.check_spring_rate(
                k_rear, "rear", dynamic_rear_rh_mm,
                f"Recommended rear third {k_rear} N/mm"
            )
        )

        # Check known unsafe rate (front 30 N/mm) as validation
        safety_checks.append(
            self.check_spring_rate(
                30.0, "front", dynamic_front_rh_mm,
                "Validation: front heave 30 N/mm (known unsafe)"
            )
        )

        return HeaveSolution(
            front_heave_nmm=k_front,
            rear_third_nmm=k_rear,
            front_dynamic_rh_mm=round(dynamic_front_rh_mm, 1),
            front_shock_vel_p99_mps=v_front,
            front_excursion_at_rate_mm=round(front_exc, 1),
            front_bottoming_margin_mm=round(dynamic_front_rh_mm - front_exc, 1),
            front_sigma_at_rate_mm=round(front_sigma, 1),
            front_binding_constraint=front_binding,
            rear_dynamic_rh_mm=round(dynamic_rear_rh_mm, 1),
            rear_shock_vel_p99_mps=v_rear,
            rear_excursion_at_rate_mm=round(rear_exc, 1),
            rear_bottoming_margin_mm=round(dynamic_rear_rh_mm - rear_exc, 1),
            rear_sigma_at_rate_mm=round(rear_sigma, 1),
            rear_binding_constraint=rear_binding,
            perch_offset_front_mm=round(hsm.perch_offset_front_baseline_mm),
            perch_offset_rear_mm=round(hsm.perch_offset_rear_baseline_mm),
            safety_checks=safety_checks,
        )
