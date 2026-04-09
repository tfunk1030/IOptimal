"""Step 2: Heave/Third Spring Solver.

Finds the minimum front heave spring rate and rear third spring rate that
keep clean-track bottoming events at zero at the 99th percentile of ride
height excursion at speed.

Physics:
    Track surface bumps excite the suspension, causing ride height to oscillate
    around its mean dynamic value. Stiffer heave/third springs reduce this
    oscillation but also reduce mechanical grip (harsher ride). The solver
    finds the minimum stiffness that prevents bottoming.

    The excursion model is BMW-calibrated but now includes tyre compliance,
    a rear corner-spring parallel path, and damper work during compression.
    The calibrated effective masses are remapped so the shared vertical model
    preserves the original telemetry fit instead of double-counting tyre spring.

    Two constraints per axle:
    - Bottoming: excursion_p99 < dynamic_RH
      Front is bottoming-constrained (dynamic RH ~15mm is the limiting factor)
    - Variance: sigma = excursion_p99 / 2.33 < sigma_target
      Rear is variance-constrained (platform stability at high speed)
      The 2.33 factor: for a Gaussian, p99 = mean + 2.33*sigma

    The solver picks the binding constraint (whichever requires stiffer spring).

Validated against BMW Sebring telemetry:
    - Front and rear use the same compliant vertical model in Step 1/2/6.
    - Rear third sizing now respects the real 900 N/mm garage limit.
    - Rear variance sizing includes rear corner spring contribution.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from car_model.garage import GarageSetupState
from car_model.cars import CarModel
from track_model.profile import TrackProfile
from vertical_dynamics import damped_excursion_mm, legacy_mass_to_shared_model_kg

# Guard so the heave-index UNVALIDATED warning is only printed once per process,
# regardless of how many times solve() / reconcile_solution() / solution_from_explicit_settings()
# are called during a single pipeline run.
_heave_index_warning_shown: bool = False


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

    # Perch offsets (optimized for travel budget)
    perch_offset_front_mm: float
    perch_offset_rear_mm: float

    # Travel budget analysis (front heave)
    slider_static_front_mm: float = 0.0       # Predicted slider position
    defl_max_front_mm: float = 0.0            # Maximum spring travel
    static_defl_front_mm: float = 0.0         # Static compression from preload
    available_travel_front_mm: float = 0.0    # DeflMax - StaticDefl
    travel_margin_front_mm: float = 0.0       # AvailableTravel - excursion_p99

    # Combined spring + shock force analysis
    spring_force_at_limit_n: float = 0.0      # k * available_travel (spring at max)
    damper_force_braking_n: float = 0.0       # c_ls * v_braking (damper at typical braking vel)
    total_force_at_limit_n: float = 0.0       # Spring + damper force before bottoming

    # Safety check results
    safety_checks: list[SpringSafetyCheck] = field(default_factory=list)
    garage_constraints_ok: bool = True
    garage_constraint_notes: list[str] = field(default_factory=list)
    parameter_search_status: dict = None
    parameter_search_evidence: dict = None

    def __post_init__(self):
        if self.parameter_search_status is None:
            self.parameter_search_status = {
                "front_heave_nmm": "user_set",
                "rear_third_nmm": "user_set",
                "perch_offset_front_mm": "user_set",
                "perch_offset_rear_mm": "user_set",
            }
        if self.parameter_search_evidence is None:
            self.parameter_search_evidence = {}

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
            "  PERCH OFFSETS (optimized for travel budget)",
            f"    Front:  {self.perch_offset_front_mm:6.1f} mm",
            f"    Rear:   {self.perch_offset_rear_mm:6.0f} mm",
        ]

        # Travel budget section
        if self.defl_max_front_mm > 0:
            lines += [
                "",
                "  FRONT TRAVEL BUDGET",
                f"    Slider position:     {self.slider_static_front_mm:5.1f} mm",
                f"    DeflMax:             {self.defl_max_front_mm:5.1f} mm",
                f"    Static deflection:   {self.static_defl_front_mm:5.1f} mm",
                f"    Available travel:    {self.available_travel_front_mm:5.1f} mm",
                f"    Excursion p99:       {self.front_excursion_at_rate_mm:5.1f} mm",
                f"    Travel margin:       {self.travel_margin_front_mm:5.1f} mm  "
                + ("OK" if self.travel_margin_front_mm >= 5 else "LOW"),
            ]

        # Combined force analysis
        if self.total_force_at_limit_n > 0:
            lines += [
                "",
                "  COMBINED SPRING + SHOCK FORCE AT TRAVEL LIMIT",
                f"    Spring force:   {self.spring_force_at_limit_n:7.0f} N  (k × travel)",
                f"    Damper force:   {self.damper_force_braking_n:7.0f} N  (c_ls × v_braking)",
                f"    Total:          {self.total_force_at_limit_n:7.0f} N",
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

    Uses the calibrated shared vertical model to find the minimum spring rate that satisfies:
        1. No bottoming: excursion < dynamic_RH
        2. Platform stability: sigma = excursion/2.33 < sigma_target
    """

    def __init__(self, car: CarModel, track: TrackProfile):
        self.car = car
        self.track = track

    def _rear_corner_wheel_rate_nmm(self, rear_spring_nmm: float | None = None) -> float:
        """Estimate rear wheel rate contribution from the corner springs."""
        rear_spring_rate_nmm = rear_spring_nmm
        if rear_spring_rate_nmm is None:
            garage_model = self.car.active_garage_output_model(self.track.track_name)
            if garage_model is not None:
                rear_spring_rate_nmm = garage_model.default_state().rear_spring_nmm
            else:
                lo, hi = self.car.corner_spring.rear_spring_range_nmm
                rear_spring_rate_nmm = 0.5 * (lo + hi)
        return max(rear_spring_rate_nmm, 0.0) * self.car.corner_spring.rear_motion_ratio ** 2

    def _shared_vertical_mass_kg(
        self,
        axle: str,
        legacy_m_eff_kg: float,
        *,
        parallel_wheel_rate_nmm: float = 0.0,
    ) -> float:
        """Preserve the legacy BMW heave calibration inside the shared compliant model."""
        is_front = axle == "front"
        tyre_rate_nmm = (
            self.car.tyre_vertical_rate_front_nmm
            if is_front
            else self.car.tyre_vertical_rate_rear_nmm
        )
        reference_rate_nmm = (
            self.car.front_heave_spring_nmm
            if is_front
            else self.car.rear_third_spring_nmm
        )
        # NOTE: m_eff is the modal sprung mass at the heave mode — a constant
        # for the car, NOT a function of stiffness. Earlier code passed
        # parallel_wheel_rate_nmm into the legacy rescaling, which inflates
        # m_eff by the compliance ratio when a parallel corner spring exists
        # (Porsche). That double-counts the parallel contribution: m grows
        # ~1.6x while k only grows ~1.1x in excursion, predicting ~20% higher
        # excursion than physics. The parallel rate must only enter through
        # k_eff inside damped_excursion_mm; m_eff scaling is reference-only.
        return legacy_mass_to_shared_model_kg(
            legacy_m_eff_kg,
            reference_rate_nmm,
            tyre_vertical_rate_nmm=tyre_rate_nmm,
            parallel_wheel_rate_nmm=0.0,
        )

    def excursion(
        self,
        v_p99_mps: float,
        m_eff_kg: float,
        k_nmm: float,
        *,
        axle: str = "front",
        damper_coeff_nsm: float = 0.0,
        parallel_wheel_rate_nmm: float = 0.0,
    ) -> float:
        """Calculate p99 ride height excursion (mm).

        Args:
            v_p99_mps: p99 shock velocity in m/s
            m_eff_kg: effective heave mass in kg
            k_nmm: spring rate in N/mm

        Returns:
            Excursion in mm
        """
        tyre_rate = (
            self.car.tyre_vertical_rate_front_nmm
            if axle == "front"
            else self.car.tyre_vertical_rate_rear_nmm
        )
        model_mass_kg = self._shared_vertical_mass_kg(
            axle,
            m_eff_kg,
            parallel_wheel_rate_nmm=parallel_wheel_rate_nmm,
        )
        return damped_excursion_mm(
            v_p99_mps,
            model_mass_kg,
            k_nmm,
            tyre_vertical_rate_nmm=tyre_rate,
            parallel_wheel_rate_nmm=parallel_wheel_rate_nmm,
            damper_coeff_nsm=damper_coeff_nsm,
        )

    def sigma_from_excursion(self, excursion_mm: float) -> float:
        """Convert p99 excursion to standard deviation (sigma).

        For Gaussian: p99 = mean + 2.33*sigma, so sigma = excursion/2.33.
        """
        return excursion_mm / 2.33

    def min_rate_for_no_bottoming(
        self,
        v_p99_mps: float,
        m_eff_kg: float,
        dynamic_rh_mm: float,
        *,
        axle: str = "front",
        damper_coeff_nsm: float = 0.0,
        parallel_wheel_rate_nmm: float = 0.0,
    ) -> float:
        """Minimum spring rate (N/mm) to prevent bottoming.
        """
        if dynamic_rh_mm <= 0:
            return float("inf")
        lo, hi = (
            self._heave_hard_bounds()
            if axle == "front"
            else self.car.heave_spring.rear_spring_range_nmm
        )
        best = float("inf")
        # Start search at 10 N/mm minimum — k=0 returns excursion=0.0 (degenerate,
        # no restoring force) which would falsely satisfy the constraint.
        lo_search = max(10.0, math.ceil(lo / 10.0) * 10)
        hsm = self.car.heave_spring
        for rate in range(int(lo_search), int(hi) + 10, 10):
            # Rate-dependent m_eff when calibration data is available, else fallback
            m_eff_at_this_rate = hsm.m_eff_at_rate(axle, float(rate))
            if m_eff_at_this_rate <= 0:
                m_eff_at_this_rate = m_eff_kg  # fallback to passed scalar
            excursion_mm = self.excursion(
                v_p99_mps,
                m_eff_at_this_rate,
                float(rate),
                axle=axle,
                damper_coeff_nsm=damper_coeff_nsm,
                parallel_wheel_rate_nmm=parallel_wheel_rate_nmm,
            )
            if excursion_mm <= dynamic_rh_mm + 1e-6:
                best = float(rate)
                break
        # If no spring rate in the legal range prevents bottoming, return the
        # maximum legal rate (stiffer is always better than softer when bottoming).
        if best == float("inf"):
            best = hi
        return best

    def min_rate_for_sigma(
        self,
        v_p99_mps: float,
        m_eff_kg: float,
        sigma_target_mm: float,
        *,
        axle: str = "front",
        damper_coeff_nsm: float = 0.0,
        parallel_wheel_rate_nmm: float = 0.0,
        current_rate_nmm: float | None = None,
        current_meas_sigma_mm: float | None = None,
        target_margin: float = 1.05,
    ) -> float:
        """Minimum spring rate (N/mm) to keep sigma below target.

        When ``current_rate_nmm`` and ``current_meas_sigma_mm`` are provided
        (the driver's currently-loaded setup + the IBT-measured rear/front
        RH std at that setup), the σ model is calibrated against the
        measured value at the current rate, and the effective σ-target is
        anchored on what the driver actually achieves rather than the
        loose user-set default.

        Calibration math:
            cal_ratio = current_meas_sigma_mm / model_sigma(current_rate)
            effective_meas_target = min(sigma_target_mm,
                                        current_meas_sigma_mm * target_margin)
            effective_model_target = effective_meas_target / cal_ratio

        Then the search returns the minimum rate where model σ ≤
        effective_model_target. With target_margin = 1.05, the algorithm
        produces a rate that gives a measured σ ~5 % looser than the
        driver's current operating point — preserving the driver-validated
        choice while allowing slight softening when other constraints
        permit it.

        cal_ratio is sanity-clamped to [0.5, 2.0] to reject outliers
        (e.g., a single bad lap with crazy RH transients).

        Validated against Porsche/Algarve IBT 14-23-44 (driver rate 160,
        measured σ=6.26, model σ at 160 = 7.34): cal_ratio = 0.853,
        effective_meas_target = 6.57, effective_model_target = 7.71,
        algorithm returns 140-160 N/mm — within 1 step of driver-validated.
        """
        excursion_limit = sigma_target_mm * 2.33
        if excursion_limit <= 0:
            return float("inf")
        lo, hi = (
            self._heave_hard_bounds()
            if axle == "front"
            else self.car.heave_spring.rear_spring_range_nmm
        )
        hsm = self.car.heave_spring

        # ── σ calibration anchor (per-session, current-setup-driven) ──
        cal_ratio = 1.0
        effective_target = float(sigma_target_mm)
        if (current_rate_nmm is not None and current_rate_nmm > 0
                and current_meas_sigma_mm is not None and current_meas_sigma_mm > 0):
            anchor_m_eff = hsm.m_eff_at_rate(axle, float(current_rate_nmm))
            if anchor_m_eff <= 0:
                anchor_m_eff = m_eff_kg
            anchor_exc = self.excursion(
                v_p99_mps,
                anchor_m_eff,
                float(current_rate_nmm),
                axle=axle,
                damper_coeff_nsm=damper_coeff_nsm,
                parallel_wheel_rate_nmm=parallel_wheel_rate_nmm,
            )
            anchor_model_sigma = self.sigma_from_excursion(anchor_exc)
            if anchor_model_sigma > 0:
                raw_ratio = float(current_meas_sigma_mm) / anchor_model_sigma
                # Clamp to reject outliers (one bad lap shouldn't dictate setup)
                cal_ratio = max(0.5, min(2.0, raw_ratio))
                # Effective σ-target in MEASURED units: tighter of (a) the
                # driver's current operating σ * margin, (b) the user's loose
                # default. Driver-anchored σ wins when it's tighter than the
                # default — which is the normal case for well-driven cars.
                effective_meas_target = min(
                    float(sigma_target_mm),
                    float(current_meas_sigma_mm) * target_margin,
                )
                # Translate target to MODEL units via the calibration ratio
                effective_target = effective_meas_target / cal_ratio
                # Floor at 3 mm so we never search for absurdly stiff rates
                effective_target = max(effective_target, 3.0)

        # ── Sticky-anchor pre-check ──
        # When the driver-loaded current rate satisfies the σ-target (within
        # epsilon), return it. This prevents one-step drift caused by the
        # synthetic σ model's gradient slightly mis-matching reality at the
        # calibration anchor — without it the algorithm picks the next rate
        # softer than the driver because the model says "good enough" at the
        # softer rate by a tiny margin.
        STICKY_EPSILON_MM = 0.05  # ~5 µm of measured σ — within model precision
        if (current_rate_nmm is not None and current_rate_nmm > 0
                and lo - 1e-6 <= current_rate_nmm <= hi + 1e-6):
            anchor_m_eff = hsm.m_eff_at_rate(axle, float(current_rate_nmm))
            if anchor_m_eff <= 0:
                anchor_m_eff = m_eff_kg
            anchor_exc = self.excursion(
                v_p99_mps,
                anchor_m_eff,
                float(current_rate_nmm),
                axle=axle,
                damper_coeff_nsm=damper_coeff_nsm,
                parallel_wheel_rate_nmm=parallel_wheel_rate_nmm,
            )
            anchor_sigma_now = self.sigma_from_excursion(anchor_exc)
            if anchor_sigma_now <= effective_target + STICKY_EPSILON_MM:
                # Snap current rate to the 10 N/mm garage step (round nearest)
                snapped = round(current_rate_nmm / 10.0) * 10
                snapped = max(int(math.ceil(lo / 10.0) * 10), min(int(hi), snapped))
                return float(snapped)

        best = float("inf")
        for rate in range(int(math.ceil(lo / 10.0) * 10), int(hi) + 10, 10):
            m_eff_at_this_rate = hsm.m_eff_at_rate(axle, float(rate))
            if m_eff_at_this_rate <= 0:
                m_eff_at_this_rate = m_eff_kg
            excursion_mm = self.excursion(
                v_p99_mps,
                m_eff_at_this_rate,
                float(rate),
                axle=axle,
                damper_coeff_nsm=damper_coeff_nsm,
                parallel_wheel_rate_nmm=parallel_wheel_rate_nmm,
            )
            sigma_mm = self.sigma_from_excursion(excursion_mm)
            if sigma_mm <= effective_target + 1e-6:
                best = float(rate)
                break
        return best

    def check_spring_rate(
        self,
        rate_nmm: float,
        axle: str,
        dynamic_rh_mm: float,
        label: str = "",
        parallel_wheel_rate_nmm: float = 0.0,
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
            # Use clean-track p99 (kerb strikes excluded) for spring sizing
            v_p99 = (self.track.shock_vel_p99_front_clean_mps
                     if self.track.shock_vel_p99_front_clean_mps > 0
                     else self.track.shock_vel_p99_front_mps)
            # Rate-dependent m_eff lookup when calibration data is available;
            # falls back to scalar front_m_eff_kg if no rate table.
            m_eff = hsm.m_eff_at_rate("front", rate_nmm)
        else:
            v_p99 = (self.track.shock_vel_p99_rear_clean_mps
                     if self.track.shock_vel_p99_rear_clean_mps > 0
                     else self.track.shock_vel_p99_rear_mps)
            m_eff = hsm.m_eff_at_rate("rear", rate_nmm)

        damper_coeff = (
            self.car.damper.front_hs_coefficient_nsm
            if axle == "front"
            else self.car.damper.rear_hs_coefficient_nsm
        )
        exc = self.excursion(
            v_p99,
            m_eff,
            rate_nmm,
            axle=axle,
            damper_coeff_nsm=damper_coeff,
            parallel_wheel_rate_nmm=parallel_wheel_rate_nmm,
        )
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

    def combined_force_at_travel(
        self,
        spring_rate_nmm: float,
        travel_mm: float,
        velocity_mps: float,
        axle: str = "front",
    ) -> tuple[float, float, float]:
        """Compute combined spring + shock force at a given travel and velocity.

        Springs are linear: F = k * x (position-dependent).
        Shocks are nonlinear: F = c(v) * v (velocity-dependent).
        Under braking (slow weight transfer, LS regime ~20 mm/s), spring dominates.
        Under bump impacts (fast transient, HS regime >50 mm/s), shock dominates.

        Args:
            spring_rate_nmm: Spring rate (N/mm)
            travel_mm: Spring compression position (mm from static)
            velocity_mps: Compression velocity (m/s)
            axle: "front" or "rear"

        Returns:
            (spring_force_n, damper_force_n, total_force_n)
        """
        spring_force = spring_rate_nmm * travel_mm  # N

        # Select damping coefficient based on velocity regime
        damper = self.car.damper
        knee = damper.knee_velocity_mps

        if axle == "front":
            c_ls = damper.front_ls_coefficient_nsm
            c_hs = damper.front_hs_coefficient_nsm
        else:
            c_ls = damper.rear_ls_coefficient_nsm
            c_hs = damper.rear_hs_coefficient_nsm

        if velocity_mps <= knee:
            damper_force = c_ls * velocity_mps
        else:
            # Digressive: LS force at knee + HS force for excess velocity
            damper_force = c_ls * knee + c_hs * (velocity_mps - knee)

        total = spring_force + damper_force
        return (spring_force, damper_force, total)

    def _heave_hard_bounds(self) -> tuple[float, float]:
        """Track-aware hard bounds for front heave spring."""
        hsm = self.car.heave_spring
        lo, hi = hsm.front_spring_range_nmm
        hard = hsm.front_heave_hard_range_nmm
        if hard is None:
            return lo, hi
        track_name_lower = self.track.track_name.lower()
        exempt = any(t in track_name_lower for t in hsm.front_heave_hard_range_exempt_tracks)
        if exempt:
            return lo, hi
        return max(lo, hard[0]), min(hi, hard[1])

    def _garage_state(
        self,
        *,
        front_pushrod_mm: float,
        rear_pushrod_mm: float,
        front_heave_nmm: float,
        front_heave_perch_mm: float,
        rear_third_nmm: float,
        rear_third_perch_mm: float,
        front_torsion_od_mm: float,
        rear_spring_nmm: float,
        rear_spring_perch_mm: float,
        fuel_load_l: float,
        front_camber_deg: float,
    ) -> GarageSetupState:
        return GarageSetupState(
            front_pushrod_mm=float(front_pushrod_mm),
            rear_pushrod_mm=float(rear_pushrod_mm),
            front_heave_nmm=float(front_heave_nmm),
            front_heave_perch_mm=float(front_heave_perch_mm),
            rear_third_nmm=float(rear_third_nmm),
            rear_third_perch_mm=float(rear_third_perch_mm),
            front_torsion_od_mm=float(front_torsion_od_mm),
            rear_spring_nmm=float(rear_spring_nmm),
            rear_spring_perch_mm=float(rear_spring_perch_mm),
            front_camber_deg=float(front_camber_deg),
            fuel_l=float(fuel_load_l),
        )

    def _best_front_perch_with_garage_model(
        self,
        *,
        front_heave_nmm: float,
        front_excursion_mm: float,
        dynamic_front_rh_mm: float,
        front_pushrod_mm: float,
        rear_pushrod_mm: float,
        rear_third_nmm: float,
        rear_third_perch_mm: float,
        front_torsion_od_mm: float,
        rear_spring_nmm: float,
        rear_spring_perch_mm: float,
        fuel_load_l: float,
        front_camber_deg: float,
        front_heave_perch_target_mm: float | None,
    ) -> tuple[float, object, object]:
        """Pick the perch that maximizes travel while keeping garage constraints valid."""
        garage_model = self.car.active_garage_output_model(self.track.track_name)
        if garage_model is None:
            raise RuntimeError("garage model required for BMW/Sebring perch optimization")

        if front_heave_perch_target_mm is not None:
            perch_candidates = [round(front_heave_perch_target_mm * 2) / 2]
        else:
            perch_candidates = [x / 2.0 for x in range(-60, 11)]

        best_valid: tuple[float, object, object] | None = None
        best_valid_score = -float("inf")
        best_any: tuple[float, object, object] | None = None
        best_any_penalty = float("inf")

        for perch in perch_candidates:
            state = self._garage_state(
                front_pushrod_mm=front_pushrod_mm,
                rear_pushrod_mm=rear_pushrod_mm,
                front_heave_nmm=front_heave_nmm,
                front_heave_perch_mm=perch,
                rear_third_nmm=rear_third_nmm,
                rear_third_perch_mm=rear_third_perch_mm,
                front_torsion_od_mm=front_torsion_od_mm,
                rear_spring_nmm=rear_spring_nmm,
                rear_spring_perch_mm=rear_spring_perch_mm,
                fuel_load_l=fuel_load_l,
                front_camber_deg=front_camber_deg,
            )
            outputs = garage_model.predict(state, front_excursion_p99_mm=front_excursion_mm)
            constraint = garage_model.validate(
                state,
                front_excursion_p99_mm=front_excursion_mm,
                min_travel_margin_mm=0.0,
                front_bottoming_margin_mm=dynamic_front_rh_mm - front_excursion_mm,
            )
            static_defl_ok = outputs.heave_spring_defl_static_mm >= garage_model.min_static_defl_mm - 1e-6
            penalty = 0.0
            if not static_defl_ok:
                penalty += garage_model.min_static_defl_mm - outputs.heave_spring_defl_static_mm
            if not constraint.heave_slider_ok:
                penalty += outputs.heave_slider_defl_static_mm - garage_model.max_slider_mm
            if not constraint.travel_margin_ok:
                penalty += abs(outputs.travel_margin_front_mm)

            if constraint.valid and static_defl_ok:
                score = outputs.travel_margin_front_mm - abs(perch) * 0.001
                if score > best_valid_score:
                    best_valid = (perch, outputs, constraint)
                    best_valid_score = score
            if penalty < best_any_penalty:
                best_any = (perch, outputs, constraint)
                best_any_penalty = penalty

        if best_valid is not None:
            return best_valid
        if best_any is None:
            raise RuntimeError("failed to evaluate any front perch candidates")
        return best_any

    def _garage_constrained_front_solution(
        self,
        *,
        base_front_heave_nmm: float,
        dynamic_front_rh_mm: float,
        rear_third_nmm: float,
        front_pushrod_mm: float,
        rear_pushrod_mm: float,
        front_torsion_od_mm: float,
        rear_spring_nmm: float,
        rear_spring_perch_mm: float,
        rear_third_perch_mm: float,
        fuel_load_l: float,
        front_camber_deg: float,
        front_heave_perch_target_mm: float | None,
        front_hs_damper_nsm: float,
    ) -> tuple[float, float, float, float, object, object]:
        """Search the minimum front-heave rate that satisfies hard garage constraints."""
        garage_model = self.car.active_garage_output_model(self.track.track_name)
        if garage_model is None:
            raise RuntimeError("garage model required for BMW/Sebring front-heave search")

        hsm = self.car.heave_spring
        v_front = (self.track.shock_vel_p99_front_clean_mps
                   if self.track.shock_vel_p99_front_clean_mps > 0
                   else self.track.shock_vel_p99_front_mps)
        m_front = hsm.front_m_eff_kg
        lo, hi = self._heave_hard_bounds()
        start_rate = max(lo, math.ceil(base_front_heave_nmm / 10.0) * 10.0)

        best_fallback: tuple[float, float, float, float, object, object] | None = None
        best_fallback_penalty = float("inf")

        for rate in range(int(start_rate), int(hi) + 10, 10):
            front_exc = self.excursion(
                v_front,
                m_front,
                rate,
                axle="front",
                damper_coeff_nsm=front_hs_damper_nsm,
            )
            front_sigma = self.sigma_from_excursion(front_exc)
            perch, outputs, constraint = self._best_front_perch_with_garage_model(
                front_heave_nmm=rate,
                front_excursion_mm=front_exc,
                dynamic_front_rh_mm=dynamic_front_rh_mm,
                front_pushrod_mm=front_pushrod_mm,
                rear_pushrod_mm=rear_pushrod_mm,
                rear_third_nmm=rear_third_nmm,
                rear_third_perch_mm=rear_third_perch_mm,
                front_torsion_od_mm=front_torsion_od_mm,
                rear_spring_nmm=rear_spring_nmm,
                rear_spring_perch_mm=rear_spring_perch_mm,
                fuel_load_l=fuel_load_l,
                front_camber_deg=front_camber_deg,
                front_heave_perch_target_mm=front_heave_perch_target_mm,
            )
            penalty = 0.0
            if not constraint.valid:
                penalty += abs(outputs.travel_margin_front_mm)
                penalty += max(0.0, outputs.heave_slider_defl_static_mm - garage_model.max_slider_mm)
            if front_sigma > hsm.sigma_target_mm:
                penalty += front_sigma - hsm.sigma_target_mm
            if dynamic_front_rh_mm - front_exc < 0:
                penalty += front_exc - dynamic_front_rh_mm

            result = (float(rate), float(perch), float(front_exc), float(front_sigma), outputs, constraint)
            if constraint.valid and front_sigma <= hsm.sigma_target_mm and dynamic_front_rh_mm - front_exc >= -1e-6:
                return result
            if penalty < best_fallback_penalty:
                best_fallback = result
                best_fallback_penalty = penalty

        if best_fallback is None:
            raise RuntimeError("no BMW/Sebring front-heave candidates evaluated")
        return best_fallback

    def solve(
        self,
        dynamic_front_rh_mm: float,
        dynamic_rear_rh_mm: float,
        front_heave_floor_nmm: float = 0.0,
        rear_third_floor_nmm: float = 0.0,
        front_heave_perch_target_mm: float | None = None,
        front_pushrod_mm: float | None = None,
        rear_pushrod_mm: float | None = None,
        front_torsion_od_mm: float | None = None,
        rear_spring_nmm: float | None = None,
        rear_spring_perch_mm: float | None = None,
        rear_third_perch_mm: float | None = None,
        fuel_load_l: float = 0.0,
        front_camber_deg: float | None = None,
        front_hs_damper_nsm: float | None = None,
        rear_hs_damper_nsm: float | None = None,
        measured: object | None = None,
        front_heave_current_nmm: float | None = None,
        rear_third_current_nmm: float | None = None,
    ) -> HeaveSolution:
        """Find minimum safe heave/third spring rates.

        Args:
            dynamic_front_rh_mm: Front dynamic ride height from Step 1
            dynamic_rear_rh_mm: Rear dynamic ride height from Step 1
            front_heave_floor_nmm: Minimum front heave rate from modifier
                (e.g., bottoming diagnosis demands stiffer spring)
            rear_third_floor_nmm: Minimum rear third rate from modifier
            front_heave_perch_target_mm: Override perch offset from modifier
                (e.g., travel exhaustion diagnosis demands more negative perch)

        Returns:
            HeaveSolution with recommended rates and constraint analysis
        """
        hsm = self.car.heave_spring
        front_damper_coeff = (
            self.car.damper.front_hs_coefficient_nsm
            if front_hs_damper_nsm is None
            else float(front_hs_damper_nsm)
        )
        rear_damper_coeff = (
            self.car.damper.rear_hs_coefficient_nsm
            if rear_hs_damper_nsm is None
            else float(rear_hs_damper_nsm)
        )

        # --- Front axle ---
        # Use clean-track p99 (kerb strikes excluded) for spring sizing.
        # Curb strikes are driving choices, not setup failures — sizing springs
        # for curb absorption loses mechanical grip everywhere else.
        v_front = (self.track.shock_vel_p99_front_clean_mps
                   if self.track.shock_vel_p99_front_clean_mps > 0
                   else self.track.shock_vel_p99_front_mps)
        # Platform sizing uses high-speed-only p99 when available.
        # At >200 kph, aero compression dominates; lap-wide p99 over-sizes springs
        # because it includes low-speed bumps that don't affect aero stability.
        # Bottoming constraint keeps the lap-wide value (safety — must not bottom anywhere).
        v_front_platform = v_front
        if self.track.shock_vel_p99_front_hs_mps > 0:
            v_front_platform = self.track.shock_vel_p99_front_hs_mps
        m_front = hsm.front_m_eff_kg

        # ── Shock velocity for heave sizing ──────────────────────────────────
        # Use the shock velocity p99 from the track profile / IBT directly.
        # Do NOT back-calculate effective velocity from deflection p99 — the
        # deflection includes kerb strikes, braking pitch, and direction changes
        # that are transient events handled by dampers, not by heave spring rate.
        # Back-calculating from deflection inflates the velocity by 50-70% on
        # tracks with aggressive kerbs (Algarve, Sebring) and drives the heave
        # spring to max range unnecessarily.
        #
        # The shock velocity channel represents the actual bump input the spring
        # needs to control for platform stability.

        # Bottoming constraint uses lap-wide p99 (safety — must not bottom anywhere)
        k_front_bottoming = self.min_rate_for_no_bottoming(
            v_front,
            m_front,
            dynamic_front_rh_mm,
            axle="front",
            damper_coeff_nsm=front_damper_coeff,
        )
        # Platform stability uses high-speed-only p99 (accuracy — only high-speed matters for aero)
        # Pass driver's CURRENT front_heave + IBT-measured front_rh_std as the
        # σ calibration anchor. min_rate_for_sigma uses this to translate the
        # synthetic σ model to MEASURED σ space and target the driver's current
        # operating point (typically tighter than the loose default 10 mm).
        _front_meas_sigma = (
            float(getattr(measured, "front_rh_std_mm", 0.0) or 0.0)
            if measured is not None else 0.0
        )
        k_front_sigma = self.min_rate_for_sigma(
            v_front_platform,
            m_front,
            hsm.sigma_target_mm,
            axle="front",
            damper_coeff_nsm=front_damper_coeff,
            current_rate_nmm=front_heave_current_nmm,
            current_meas_sigma_mm=_front_meas_sigma,
        )

        if k_front_bottoming >= k_front_sigma:
            k_front = k_front_bottoming
            front_binding = "bottoming"
        else:
            k_front = k_front_sigma
            front_binding = "variance"

        # Apply modifier floor constraint (diagnosis-driven minimum)
        if front_heave_floor_nmm > 0 and k_front < front_heave_floor_nmm:
            k_front = front_heave_floor_nmm
            front_binding = "modifier_floor"

        # Clamp to valid range
        lo_front, hi_front = self._heave_hard_bounds()
        k_front = max(k_front, lo_front)
        k_front = min(k_front, hi_front)

        # Round up to nearest 10 N/mm (iRacing garage step)
        k_front = math.ceil(k_front / 10) * 10

        front_exc = self.excursion(
            v_front,
            m_front,
            k_front,
            axle="front",
            damper_coeff_nsm=front_damper_coeff,
        )
        front_sigma = self.sigma_from_excursion(front_exc)

        # --- Rear axle ---
        v_rear = (self.track.shock_vel_p99_rear_clean_mps
                  if self.track.shock_vel_p99_rear_clean_mps > 0
                  else self.track.shock_vel_p99_rear_mps)
        # High-speed filtered for platform sizing (same logic as front)
        v_rear_platform = v_rear
        if self.track.shock_vel_p99_rear_hs_mps > 0:
            v_rear_platform = self.track.shock_vel_p99_rear_hs_mps
        m_rear = hsm.rear_m_eff_kg
        rear_corner_wheel_rate_nmm = self._rear_corner_wheel_rate_nmm(rear_spring_nmm)

        # Bottoming: lap-wide (safety)
        k_rear_bottoming = self.min_rate_for_no_bottoming(
            v_rear,
            m_rear,
            dynamic_rear_rh_mm,
            axle="rear",
            damper_coeff_nsm=rear_damper_coeff,
            parallel_wheel_rate_nmm=rear_corner_wheel_rate_nmm,
        )
        # Platform stability: high-speed-only (accuracy)
        # σ calibration anchor: driver's current rear_third + IBT-measured rear_rh_std.
        # See min_rate_for_sigma docstring for the calibration math.
        _rear_meas_sigma = (
            float(getattr(measured, "rear_rh_std_mm", 0.0) or 0.0)
            if measured is not None else 0.0
        )
        k_rear_sigma = self.min_rate_for_sigma(
            v_rear_platform,
            m_rear,
            hsm.sigma_target_mm,
            axle="rear",
            damper_coeff_nsm=rear_damper_coeff,
            parallel_wheel_rate_nmm=rear_corner_wheel_rate_nmm,
            current_rate_nmm=rear_third_current_nmm,
            current_meas_sigma_mm=_rear_meas_sigma,
        )

        if k_rear_bottoming >= k_rear_sigma:
            k_rear = k_rear_bottoming
            rear_binding = "bottoming"
        else:
            k_rear = k_rear_sigma
            rear_binding = "variance"

        # Apply modifier floor constraint (diagnosis-driven minimum)
        if rear_third_floor_nmm > 0 and k_rear < rear_third_floor_nmm:
            k_rear = rear_third_floor_nmm
            rear_binding = "modifier_floor"

        # Clamp and round up to nearest 10 N/mm (iRacing garage step)
        k_rear = max(k_rear, hsm.rear_spring_range_nmm[0])
        k_rear = min(k_rear, hsm.rear_spring_range_nmm[1])
        k_rear = math.ceil(k_rear / 10) * 10

        rear_exc = self.excursion(
            v_rear,
            m_rear,
            k_rear,
            axle="rear",
            damper_coeff_nsm=rear_damper_coeff,
            parallel_wheel_rate_nmm=rear_corner_wheel_rate_nmm,
        )
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
                f"Recommended rear third {k_rear} N/mm",
                parallel_wheel_rate_nmm=rear_corner_wheel_rate_nmm,
            )
        )

        # Check known unsafe rate (front 30 N/mm) as validation
        safety_checks.append(
            self.check_spring_rate(
                30.0, "front", dynamic_front_rh_mm,
                "Validation: front heave 30 N/mm (known unsafe)"
            )
        )

        # --- Perch offset optimization (travel budget) ---
        # Compute DeflMax, then find optimal perch that maximizes available travel
        # while maintaining minimum preload.
        defl_max = 0.0
        slider_static = 0.0
        static_defl = 0.0
        available_travel = 0.0
        travel_margin = 0.0
        perch_front = hsm.perch_offset_front_baseline_mm
        garage_constraint_notes: list[str] = []
        garage_constraints_ok = True
        garage_model = self.car.active_garage_output_model(self.track.track_name)
        # Auto-built garage models (from auto_calibrate) don't have validated travel
        # budget constraints — use physics-only path for spring rate selection.
        _garage_has_travel_constraints = (
            garage_model is not None
            and not getattr(garage_model, "_auto_built", False)
        )

        if _garage_has_travel_constraints:
            initial_front_rate = k_front
            baseline = garage_model.default_state(fuel_l=fuel_load_l)
            front_pushrod_val = (
                front_pushrod_mm
                if front_pushrod_mm is not None
                else baseline.front_pushrod_mm
            )
            rear_pushrod_val = (
                rear_pushrod_mm
                if rear_pushrod_mm is not None
                else baseline.rear_pushrod_mm
            )
            front_torsion_od_val = (
                front_torsion_od_mm
                if front_torsion_od_mm is not None
                else baseline.front_torsion_od_mm
            )
            rear_spring_val = (
                rear_spring_nmm
                if rear_spring_nmm is not None
                else baseline.rear_spring_nmm
            )
            rear_spring_perch_val = (
                rear_spring_perch_mm
                if rear_spring_perch_mm is not None
                else baseline.rear_spring_perch_mm
            )
            rear_third_perch_val = (
                rear_third_perch_mm
                if rear_third_perch_mm is not None
                else baseline.rear_third_perch_mm
            )
            front_camber_val = (
                front_camber_deg
                if front_camber_deg is not None
                else baseline.front_camber_deg
            )
            selected = self._garage_constrained_front_solution(
                base_front_heave_nmm=k_front,
                dynamic_front_rh_mm=dynamic_front_rh_mm,
                rear_third_nmm=k_rear,
                front_pushrod_mm=front_pushrod_val,
                rear_pushrod_mm=rear_pushrod_val,
                front_torsion_od_mm=front_torsion_od_val,
                rear_spring_nmm=rear_spring_val,
                rear_spring_perch_mm=rear_spring_perch_val,
                rear_third_perch_mm=rear_third_perch_val,
                fuel_load_l=fuel_load_l,
                front_camber_deg=front_camber_val,
                front_heave_perch_target_mm=front_heave_perch_target_mm,
                front_hs_damper_nsm=front_damper_coeff,
            )
            k_front, perch_front, front_exc, front_sigma, outputs, constraint = selected
            if k_front > initial_front_rate:
                front_binding = "garage_constraint"
            defl_max = outputs.heave_spring_defl_max_mm
            slider_static = outputs.heave_slider_defl_static_mm
            static_defl = outputs.heave_spring_defl_static_mm
            available_travel = outputs.available_travel_front_mm
            travel_margin = outputs.travel_margin_front_mm
            garage_constraints_ok = constraint.valid
            garage_constraint_notes = list(constraint.messages)
            safety_checks.append(SpringSafetyCheck(
                label=f"Garage travel budget at {k_front} N/mm (perch {perch_front:.1f}mm)",
                rate_nmm=k_front,
                axle="front",
                excursion_mm=round(front_exc, 1),
                dynamic_rh_mm=round(defl_max, 1),
                bottoming_mm=round(max(0, front_exc - available_travel), 1),
                sigma_mm=round(front_sigma, 1),
                sigma_target_mm=hsm.sigma_target_mm,
                safe=constraint.valid,
                reason=("; ".join(constraint.messages) if constraint.messages else
                        f"Slider={slider_static:.1f}mm, StaticDefl={static_defl:.1f}mm, "
                        f"available={available_travel:.1f}mm, margin={travel_margin:.1f}mm"),
            ))
        elif hsm.heave_spring_defl_max_intercept_mm > 0:
            defl_max = (hsm.heave_spring_defl_max_intercept_mm
                        + hsm.heave_spring_defl_max_slope * k_front)
            perch_front = hsm.perch_offset_front_baseline_mm
            if hsm.slider_perch_coeff > 0:
                target_slider = hsm.max_slider_mm - 3.0
                perch_front = (
                    (target_slider - hsm.slider_intercept - hsm.slider_heave_coeff * k_front)
                    / hsm.slider_perch_coeff
                )
                perch_front = round(perch_front * 2) / 2
                slider_static = (hsm.slider_intercept
                                 + hsm.slider_heave_coeff * k_front
                                 + hsm.slider_perch_coeff * perch_front)
            static_defl = max(0, hsm.defl_static_intercept + hsm.defl_static_heave_coeff * k_front)
            available_travel = max(0, defl_max - static_defl)
            travel_margin = available_travel - front_exc
            if front_heave_perch_target_mm is not None:
                perch_front = round(front_heave_perch_target_mm * 2) / 2
                if hsm.slider_perch_coeff > 0:
                    slider_static = (hsm.slider_intercept
                                     + hsm.slider_heave_coeff * k_front
                                     + hsm.slider_perch_coeff * perch_front)
            budget_safe = travel_margin >= 0.0
            safety_checks.append(SpringSafetyCheck(
                label=f"Travel budget at {k_front} N/mm (perch {perch_front:.1f}mm)",
                rate_nmm=k_front,
                axle="front",
                excursion_mm=round(front_exc, 1),
                dynamic_rh_mm=round(defl_max, 1),
                bottoming_mm=round(max(0, front_exc - available_travel), 1),
                sigma_mm=round(front_sigma, 1),
                sigma_target_mm=hsm.sigma_target_mm,
                safe=budget_safe,
                reason=(f"DeflMax={defl_max:.1f}mm, StaticDefl={static_defl:.1f}mm, "
                        f"available={available_travel:.1f}mm, excursion={front_exc:.1f}mm, "
                        f"margin={travel_margin:.1f}mm"),
            ))

        # --- Combined spring + shock force at travel limit ---
        spring_force_limit = k_front * available_travel if available_travel > 0 else 0.0
        # Under braking, compression velocity is in LS regime (~20 mm/s for weight transfer)
        v_braking_mps = 0.020  # Typical braking compression velocity (LS regime)
        damper = self.car.damper
        damper_force_braking = damper.front_ls_coefficient_nsm * v_braking_mps
        total_force_limit = spring_force_limit + damper_force_braking

        # Validation warning for unvalidated heave spring index mappings.
        # Use a module-level flag so this prints only once per pipeline run,
        # regardless of how many times solve() is called internally.
        global _heave_index_warning_shown
        if getattr(hsm, 'heave_index_unvalidated', False):
            garage_constraint_notes.append(
                "UNVALIDATED: Heave spring index-to-N/mm mapping is approximate — verify heave spring feel on track"
            )
            if not _heave_index_warning_shown:
                _heave_index_warning_shown = True
                print("\n⚠  UNVALIDATED: Heave spring index-to-N/mm mapping is approximate — verify heave spring feel on track\n")

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
            perch_offset_front_mm=round(perch_front, 1),
            perch_offset_rear_mm=round(
                rear_third_perch_mm
                if rear_third_perch_mm is not None
                else hsm.perch_offset_rear_baseline_mm
            ),
            slider_static_front_mm=round(slider_static, 1),
            defl_max_front_mm=round(defl_max, 1),
            static_defl_front_mm=round(static_defl, 1),
            available_travel_front_mm=round(available_travel, 1),
            travel_margin_front_mm=round(travel_margin, 1),
            spring_force_at_limit_n=round(spring_force_limit, 0),
            damper_force_braking_n=round(damper_force_braking, 0),
            total_force_at_limit_n=round(total_force_limit, 0),
            safety_checks=safety_checks,
            garage_constraints_ok=garage_constraints_ok,
            garage_constraint_notes=garage_constraint_notes,
        )

    def solution_from_explicit_settings(
        self,
        *,
        dynamic_front_rh_mm: float,
        dynamic_rear_rh_mm: float,
        front_heave_nmm: float,
        rear_third_nmm: float,
        front_heave_perch_mm: float | None = None,
        rear_third_perch_mm: float | None = None,
        front_pushrod_mm: float | None = None,
        rear_pushrod_mm: float | None = None,
        front_torsion_od_mm: float | None = None,
        rear_spring_nmm: float | None = None,
        rear_spring_perch_mm: float | None = None,
        fuel_load_l: float = 0.0,
        front_camber_deg: float | None = None,
        front_hs_damper_nsm: float | None = None,
        rear_hs_damper_nsm: float | None = None,
    ) -> HeaveSolution:
        """Materialize Step 2 from explicit rates/perches while recomputing margins."""
        return self.solve(
            dynamic_front_rh_mm=dynamic_front_rh_mm,
            dynamic_rear_rh_mm=dynamic_rear_rh_mm,
            front_heave_floor_nmm=float(front_heave_nmm),
            rear_third_floor_nmm=float(rear_third_nmm),
            front_heave_perch_target_mm=front_heave_perch_mm,
            front_pushrod_mm=front_pushrod_mm,
            rear_pushrod_mm=rear_pushrod_mm,
            front_torsion_od_mm=front_torsion_od_mm,
            rear_spring_nmm=rear_spring_nmm,
            rear_spring_perch_mm=rear_spring_perch_mm,
            rear_third_perch_mm=rear_third_perch_mm,
            fuel_load_l=fuel_load_l,
            front_camber_deg=front_camber_deg,
            front_hs_damper_nsm=front_hs_damper_nsm,
            rear_hs_damper_nsm=rear_hs_damper_nsm,
        )

    def reconcile_solution(
        self,
        step1,
        step2: HeaveSolution,
        step3,
        *,
        fuel_load_l: float = 0.0,
        front_camber_deg: float | None = None,
        front_hs_damper_nsm: float | None = None,
        verbose: bool = True,
    ) -> None:
        """Round-trip the front heave travel budget after torsion/spring choices are known."""
        garage_model = self.car.active_garage_output_model(self.track.track_name)
        # Auto-built garage models don't have validated travel budget constraints
        if garage_model is None or getattr(garage_model, "_auto_built", False):
            return

        front_damper_coeff = (
            self.car.damper.front_hs_coefficient_nsm
            if front_hs_damper_nsm is None
            else float(front_hs_damper_nsm)
        )

        selected = self._garage_constrained_front_solution(
            base_front_heave_nmm=step2.front_heave_nmm,
            dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
            rear_third_nmm=step2.rear_third_nmm,
            front_pushrod_mm=step1.front_pushrod_offset_mm,
            rear_pushrod_mm=step1.rear_pushrod_offset_mm,
            front_torsion_od_mm=step3.front_torsion_od_mm,
            rear_spring_nmm=step3.rear_spring_rate_nmm,
            rear_spring_perch_mm=step3.rear_spring_perch_mm,
            rear_third_perch_mm=step2.perch_offset_rear_mm,
            fuel_load_l=fuel_load_l,
            front_camber_deg=(
                front_camber_deg
                if front_camber_deg is not None
                else garage_model.default_front_camber_deg
            ),
            front_heave_perch_target_mm=None,
            front_hs_damper_nsm=front_damper_coeff,
        )
        new_rate, new_perch, front_exc, front_sigma, outputs, constraint = selected

        if verbose and (
            abs(new_rate - step2.front_heave_nmm) > 0.05
            or abs(new_perch - step2.perch_offset_front_mm) > 0.05
        ):
            print(
                f"  Heave round-trip: {step2.front_heave_nmm:.0f} N/mm / "
                f"{step2.perch_offset_front_mm:.1f} mm -> "
                f"{new_rate:.0f} N/mm / {new_perch:.1f} mm "
                f"(slider {outputs.heave_slider_defl_static_mm:.1f} mm)"
            )

        step2.front_heave_nmm = round(new_rate, 0)
        step2.perch_offset_front_mm = round(new_perch, 1)
        step2.front_excursion_at_rate_mm = round(front_exc, 1)
        step2.front_bottoming_margin_mm = round(step1.dynamic_front_rh_mm - front_exc, 1)
        step2.front_sigma_at_rate_mm = round(front_sigma, 1)
        step2.slider_static_front_mm = round(outputs.heave_slider_defl_static_mm, 1)
        step2.defl_max_front_mm = round(outputs.heave_spring_defl_max_mm, 1)
        step2.static_defl_front_mm = round(outputs.heave_spring_defl_static_mm, 1)
        step2.available_travel_front_mm = round(outputs.available_travel_front_mm, 1)
        step2.travel_margin_front_mm = round(outputs.travel_margin_front_mm, 1)
        step2.spring_force_at_limit_n = round(step2.front_heave_nmm * outputs.available_travel_front_mm, 0)
        v_braking_mps = 0.020
        step2.damper_force_braking_n = round(self.car.damper.front_ls_coefficient_nsm * v_braking_mps, 0)
        step2.total_force_at_limit_n = round(
            step2.spring_force_at_limit_n + step2.damper_force_braking_n,
            0,
        )
        step2.garage_constraints_ok = constraint.valid
        step2.garage_constraint_notes = list(constraint.messages)
        step2.safety_checks = [
            check for check in step2.safety_checks
            if "travel budget" not in check.label.lower()
        ]
        step2.safety_checks.append(SpringSafetyCheck(
            label=f"Garage travel budget at {step2.front_heave_nmm:.0f} N/mm (perch {step2.perch_offset_front_mm:.1f}mm)",
            rate_nmm=step2.front_heave_nmm,
            axle="front",
            excursion_mm=round(front_exc, 1),
            dynamic_rh_mm=round(outputs.heave_spring_defl_max_mm, 1),
            bottoming_mm=round(max(0, front_exc - outputs.available_travel_front_mm), 1),
            sigma_mm=round(front_sigma, 1),
            sigma_target_mm=self.car.heave_spring.sigma_target_mm,
            safe=constraint.valid,
            reason=("; ".join(constraint.messages) if constraint.messages else
                    f"Slider={outputs.heave_slider_defl_static_mm:.1f}mm, "
                    f"travel margin={outputs.travel_margin_front_mm:.1f}mm"),
        ))
