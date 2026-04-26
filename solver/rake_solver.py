"""Step 1: Rake/Ride Height Solver.

Finds the optimal front and rear ride heights that:
  1. Achieve a target DF balance (constraint)
  2. Maximize L/D ratio (objective)
  3. Keep front dynamic RH above vortex burst threshold for 99% of
     clean-track samples (constraint, using shock velocity spectrum)
  4. Respect the sim-enforced minimum static ride height (30.0mm for GTP)

Then converts the dynamic targets to static ride heights and pushrod
offsets using the car's calibrated aero compression model.

Physics:
    At speed, aero loads compress the suspension from static (garage) ride
    heights to lower dynamic values. The aero map is parameterized by these
    dynamic ride heights. The solver searches over dynamic RH space, then
    works backwards to the static settings the driver should enter in the
    garage.

    GTP cars universally run at the minimum possible front static ride height
    (30.0mm sim floor) to maximize absolute downforce. Lower front RH means
    more aggressive ground effect, higher absolute DF for cornering, at the
    cost of slightly worse L/D (more drag). This tradeoff favors maximum DF
    on most circuits because lap time is more sensitive to cornering speed
    than straight-line speed.

    The solver has two modes:
    - Default (pin_front_min=True): Front static RH is pinned at the sim
      minimum. The solver finds the rear RH that achieves the target balance.
      This matches real-world GTP setup methodology.
    - Free optimization (pin_front_min=False): Both front and rear dynamic
      RH are optimized freely for maximum L/D at the target balance. Useful
      for exploring the aero map but may not match real setups.

    Ride height variance at speed comes from track surface bumps, characterized
    by the shock velocity spectrum in the track profile. The p99 shock velocity
    is converted to an estimated ride height excursion via:
        excursion = shock_vel_p99 / (2 * pi * dominant_bump_freq)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize, brentq

logger = logging.getLogger(__name__)

from aero_model.interpolator import AeroSurface
from car_model.garage import GarageSetupState
from car_model.cars import CarModel
from track_model.profile import TrackProfile


@dataclass
class RakeSolution:
    """Output of the Step 1 rake/ride height solver."""

    # Target dynamic ride heights (what the car runs at speed)
    dynamic_front_rh_mm: float
    dynamic_rear_rh_mm: float
    rake_dynamic_mm: float           # rear - front

    # Aero performance at target dynamic RH
    df_balance_pct: float
    ld_ratio: float

    # Vortex burst margin
    front_rh_excursion_p99_mm: float  # p99 ride height oscillation
    front_rh_min_p99_mm: float        # dynamic front - excursion (must be > threshold)
    vortex_burst_threshold_mm: float
    vortex_burst_margin_mm: float     # min_p99 - threshold

    # Static ride heights (garage settings)
    static_front_rh_mm: float
    static_rear_rh_mm: float
    rake_static_mm: float

    # Pushrod offsets
    front_pushrod_offset_mm: float
    rear_pushrod_offset_mm: float

    # Aero compression at reference speed
    aero_compression_front_mm: float
    aero_compression_rear_mm: float
    compression_ref_speed_kph: float

    # Solver diagnostics
    balance_error_pct: float         # |achieved - target| balance
    converged: bool
    iterations: int
    mode: str                        # "pinned_front" or "free_optimization"

    # L/D comparison (only populated when pin_front_min=True)
    free_opt_ld: float = 0.0         # L/D if front were not pinned
    ld_cost_of_pinning: float = 0.0  # L/D penalty from running at floor

    # Aero stall proximity
    aero_state: str = "nominal"      # "nominal" | "stall_warning" | "stall_risk"
    stall_factor: float = 0.0        # 0.0 (clear) to 1.0 (full stall)
    parameter_search_status: dict = None
    parameter_search_evidence: dict = None

    def __post_init__(self):
        if self.parameter_search_status is None:
            self.parameter_search_status = {
                "front_pushrod_offset_mm": "solver_computed",
                "rear_pushrod_offset_mm": "solver_computed",
                "static_front_rh_mm": "solver_computed",
                "static_rear_rh_mm": "solver_computed",
            }
        if self.parameter_search_evidence is None:
            self.parameter_search_evidence = {}


    def summary(self) -> str:
        """Human-readable summary of the solution."""
        lines = [
            "===========================================================",
            "  STEP 1: RAKE / RIDE HEIGHT SOLUTION",
            f"  Mode: {self.mode}",
            "===========================================================",
            "",
            "  DYNAMIC RIDE HEIGHTS (at speed)",
            f"    Front:  {self.dynamic_front_rh_mm:6.1f} mm",
            f"    Rear:   {self.dynamic_rear_rh_mm:6.1f} mm",
            f"    Rake:   {self.rake_dynamic_mm:6.1f} mm",
            "",
            "  AERO PERFORMANCE",
            f"    DF balance:  {self.df_balance_pct:6.2f} %",
            f"    L/D ratio:   {self.ld_ratio:6.3f}",
        ]
        if self.free_opt_ld > 0:
            lines += [
                f"    L/D (free):  {self.free_opt_ld:6.3f}  (if front not pinned)",
                f"    L/D cost:    {self.ld_cost_of_pinning:+6.3f}  (tradeoff for more DF)",
            ]
        if self.aero_state != "nominal":
            lines += [
                "",
                f"  ⚠  AERO STALL WARNING: {self.aero_state.upper()}",
                f"     stall_factor={self.stall_factor:.3f}  "
                f"(front RH too low — vortex collapse risk)",
                f"     Raise front static RH or reduce front aero compression.",
            ]
        lines += [
            "",
            "  VORTEX BURST CONSTRAINT",
            f"    Front p99 excursion:  {self.front_rh_excursion_p99_mm:5.1f} mm",
            f"    Front minimum (p99):  {self.front_rh_min_p99_mm:5.1f} mm",
            f"    Threshold:            {self.vortex_burst_threshold_mm:5.1f} mm",
            f"    Margin:               {self.vortex_burst_margin_mm:5.1f} mm  "
            + ("OK" if self.vortex_burst_margin_mm > 0 else "VIOLATED"),
            "",
            f"  AERO COMPRESSION (at {self.compression_ref_speed_kph:.0f} kph)",
            f"    Front:  {self.aero_compression_front_mm:5.1f} mm",
            f"    Rear:   {self.aero_compression_rear_mm:5.1f} mm",
            "",
            "  STATIC RIDE HEIGHTS (garage settings)",
            f"    Front:  {self.static_front_rh_mm:6.1f} mm",
            f"    Rear:   {self.static_rear_rh_mm:6.1f} mm",
            f"    Rake:   {self.rake_static_mm:6.1f} mm",
            "",
            "  PUSHROD OFFSETS",
            f"    Front:  {self.front_pushrod_offset_mm:6.1f} mm",
            f"    Rear:   {self.rear_pushrod_offset_mm:6.1f} mm",
            "",
            "  SOLVER STATUS",
            f"    Converged:      {'Yes' if self.converged else 'No'}",
            f"    Balance error:  {self.balance_error_pct:.3f} %",
            f"    Iterations:     {self.iterations}",
            "===========================================================",
        ]
        return "\n".join(lines)


class RakeSolver:
    """Step 1 solver: find optimal rake/ride heights for target DF balance.

    The solver searches over dynamic (at-speed) ride heights using the aero
    response surface, then converts back to static garage settings.
    """

    def __init__(self, car: CarModel, surface: AeroSurface, track: TrackProfile):
        self.car = car
        self.surface = surface
        self.track = track

    def _query_aero(self, actual_front: float, actual_rear: float) -> tuple[float, float]:
        """Query aero surface at actual front/rear RH. Returns (balance, L/D)."""
        aero_frh, aero_rrh = self.car.to_aero_coords(actual_front, actual_rear)
        bal = self.surface.df_balance(aero_frh, aero_rrh)
        ld = self.surface.lift_drag(aero_frh, aero_rrh)
        return bal, ld

    def _resolve_aero_speed(self) -> float:
        """Resolve the aero compression reference speed for this track.

        Logs a warning if the track's V²-RMS reference is unset and we fall
        back to the AeroCompression's intrinsic ref_speed (typically 230 kph),
        since that fallback misrepresents tracks whose actual operating speed
        differs significantly.
        """
        comp = self.car.aero_compression
        aero_ref = self.track.aero_reference_speed_kph
        if aero_ref > 0:
            return aero_ref
        logger.warning(
            "track.aero_reference_speed_kph=%s — falling back to "
            "comp.ref_speed_kph=%.1f kph; compression may be miscalibrated "
            "for this track",
            aero_ref, comp.ref_speed_kph,
        )
        return comp.ref_speed_kph

    def _find_rear_for_balance(
        self, actual_front: float, target_balance: float,
        current_rear_rh_dynamic_mm: float | None = None,
        anchor_tolerance_pct: float = 0.10,
    ) -> float | None:
        """Find the actual rear RH that achieves target balance at a given front RH.

        Primary method: Brent's method (root finding) on the balance error.
        Fallback: surface.find_rh_for_balance() bisection (interpolator utility).

        Driver anchor (NEVER lap-time-driven): if current_rear_rh_dynamic_mm
        (IBT-measured dynamic rear RH) is provided AND its balance is within
        anchor_tolerance_pct of the target, return the measured value. The
        model-derived rear vs measured rear can differ by a few mm because
        the rake_solver's balance search is independent of suspension
        compliance, while the IBT measurement reflects the chassis the
        driver actually drove. Honest-naming: callers should annotate the
        returned value as "anchored to driver-loaded" when this branch fires.

        Aero-gradient curvature warning: if |dBalance/dRH| is < 0.01 %/mm
        near the operating point, the binary balance search becomes unstable
        to pushrod quantization (small RH steps move balance by < 0.005 pp,
        smaller than aero-map noise). We log a one-line info notice.
        """
        rear_lo = self.car.min_rear_rh_dynamic
        rear_hi = self.car.max_rear_rh_dynamic

        def balance_error(actual_rear):
            bal, _ = self._query_aero(actual_front, actual_rear)
            return bal - target_balance

        # Check if target is bracketed
        err_lo = balance_error(rear_lo)
        err_hi = balance_error(rear_hi)

        if err_lo * err_hi > 0:
            # Target not bracketed - no solution in range
            return None

        try:
            result = brentq(balance_error, rear_lo, rear_hi, xtol=0.01, maxiter=50)
        except ValueError:
            # Fallback: use AeroSurface.find_rh_for_balance() bisection method
            # Note: this operates in aero-map coordinates (may have axes swapped)
            aero_front, _ = self.car.to_aero_coords(actual_front, rear_lo)
            result = self.surface.find_rh_for_balance(
                target_balance,
                rear_rh=aero_front,  # in aero coords, "rear_rh" col = actual front
                front_rh_range=(rear_lo, rear_hi),
            )

        if result is None:
            return None

        # Aero-gradient curvature check at the operating point.
        # ∂(balance)/∂(rear_RH) by central difference; warn if extremely flat.
        try:
            h = 0.5  # mm
            r_lo = max(rear_lo, result - h)
            r_hi = min(rear_hi, result + h)
            if r_hi > r_lo:
                bal_hi, _ = self._query_aero(actual_front, r_hi)
                bal_lo, _ = self._query_aero(actual_front, r_lo)
                d_bal_d_rh = (bal_hi - bal_lo) / (r_hi - r_lo)
                if abs(d_bal_d_rh) < 0.01:
                    logger.info(
                        "Low-gradient aero regime at front=%.1fmm rear=%.1fmm: "
                        "|dBalance/dRH|=%.4f %%/mm (<0.01) — solution sensitive to "
                        "pushrod quantization",
                        actual_front, result, abs(d_bal_d_rh),
                    )
        except Exception as exc:  # pragma: no cover — diagnostic only
            logger.debug("Aero-gradient curvature check failed: %s", exc)

        # Driver anchor: if measured dynamic rear RH is within tolerance of
        # the model's solution AND the measured value also achieves balance
        # within tolerance, prefer the measured value. Provenance is the
        # caller's job (log a "anchored to driver-loaded" message).
        if current_rear_rh_dynamic_mm is not None and current_rear_rh_dynamic_mm > 0:
            anchor = float(current_rear_rh_dynamic_mm)
            if rear_lo <= anchor <= rear_hi:
                anchor_err = abs(balance_error(anchor))
                if anchor_err <= anchor_tolerance_pct:
                    logger.info(
                        "Rake rear RH anchored to driver-loaded: "
                        "model=%.2fmm, measured=%.2fmm, balance_err=%.3f%% "
                        "(tolerance %.2f%%)",
                        result, anchor, anchor_err, anchor_tolerance_pct,
                    )
                    return anchor

        return result

    def _build_solution(
        self,
        actual_front_dyn: float,
        actual_rear_dyn: float,
        front_excursion_p99: float,
        target_balance: float,
        fuel_load_l: float,
        converged: bool,
        iterations: int,
        mode: str,
        free_opt_ld: float = 0.0,
    ) -> RakeSolution:
        """Build a RakeSolution from dynamic ride heights."""
        bal, ld = self._query_aero(actual_front_dyn, actual_rear_dyn)

        comp = self.car.aero_compression
        # Aero-relevant reference speed: V²-RMS over speed bands, not median.
        # Validated against IBT-measured rear compression on Porsche/Algarve.
        track_speed = self._resolve_aero_speed()
        front_comp = comp.front_at_speed(track_speed)
        rear_comp = comp.rear_at_speed(track_speed)

        static_front = actual_front_dyn + front_comp
        static_rear = actual_rear_dyn + rear_comp

        # Clamp to sim-enforced minimums
        static_front = max(static_front, self.car.min_front_rh_static)
        static_rear = max(static_rear, self.car.min_rear_rh_static)

        garage_model = self.car.active_garage_output_model(self.track.track_name)
        rh_model = self.car.ride_height_model
        if garage_model is not None:
            baseline = garage_model.default_state(fuel_l=fuel_load_l)
            front_pushrod = garage_model.front_pushrod_for_static_rh(
                static_front,
                front_heave_nmm=baseline.front_heave_nmm,
                front_heave_perch_mm=baseline.front_heave_perch_mm,
                front_torsion_od_mm=baseline.front_torsion_od_mm,
                front_camber_deg=baseline.front_camber_deg,
                fuel_l=fuel_load_l,
                # Provide rear-axis context for DirectRegression bisection
                rear_pushrod_mm=baseline.rear_pushrod_mm,
                rear_third_nmm=baseline.rear_third_nmm,
                rear_third_perch_mm=baseline.rear_third_perch_mm,
                rear_spring_nmm=baseline.rear_spring_nmm,
                rear_spring_perch_mm=baseline.rear_spring_perch_mm,
                rear_camber_deg=baseline.rear_camber_deg,
                wing_deg=baseline.wing_deg,
            )
            rear_pushrod = garage_model.rear_pushrod_for_static_rh(
                static_rear,
                rear_third_nmm=baseline.rear_third_nmm,
                rear_third_perch_mm=baseline.rear_third_perch_mm,
                rear_spring_nmm=baseline.rear_spring_nmm,
                rear_spring_perch_mm=baseline.rear_spring_perch_mm,
                front_heave_perch_mm=baseline.front_heave_perch_mm,
                fuel_l=fuel_load_l,
                # Provide front-axis context for DirectRegression bisection
                front_pushrod_mm=front_pushrod,
                front_heave_nmm=baseline.front_heave_nmm,
                front_torsion_od_mm=baseline.front_torsion_od_mm,
                front_camber_deg=baseline.front_camber_deg,
                rear_camber_deg=baseline.rear_camber_deg,
                wing_deg=baseline.wing_deg,
            )
            # Snap pushrods to 0.5mm garage step; surface any RH drift > 0.5mm
            # so high-sensitivity cars (e.g. Cadillac front_pushrod_to_rh ≈
            # 1.388) don't accumulate quiet drift across solver iterations.
            unsnapped = {"front": float(front_pushrod), "rear": float(rear_pushrod)}
            front_pushrod = round(front_pushrod * 2) / 2
            rear_pushrod = round(rear_pushrod * 2) / 2
            outputs = garage_model.predict(GarageSetupState(
                front_pushrod_mm=front_pushrod,
                rear_pushrod_mm=rear_pushrod,
                front_heave_nmm=baseline.front_heave_nmm,
                front_heave_perch_mm=baseline.front_heave_perch_mm,
                rear_third_nmm=baseline.rear_third_nmm,
                rear_third_perch_mm=baseline.rear_third_perch_mm,
                front_torsion_od_mm=baseline.front_torsion_od_mm,
                rear_spring_nmm=baseline.rear_spring_nmm,
                rear_spring_perch_mm=baseline.rear_spring_perch_mm,
                front_camber_deg=baseline.front_camber_deg,
                rear_camber_deg=baseline.rear_camber_deg,
                fuel_l=fuel_load_l,
            ))
            for axle, target_rh, snapped_rh, snapped_pushrod in (
                ("Front", static_front, outputs.front_static_rh_mm, front_pushrod),
                ("Rear", static_rear, outputs.rear_static_rh_mm, rear_pushrod),
            ):
                drift = abs(snapped_rh - target_rh)
                if drift > 0.5:
                    logger.warning(
                        "%s pushrod snap drift: target_RH=%.2fmm, snapped "
                        "pushrod %.2f→%.1fmm gives RH=%.2fmm (drift %.2fmm > "
                        "0.5mm) — high sensitivity car",
                        axle, target_rh, unsnapped[axle.lower()], snapped_pushrod,
                        snapped_rh, drift,
                    )
            static_front = max(outputs.front_static_rh_mm, self.car.min_front_rh_static)
            static_rear = max(outputs.rear_static_rh_mm, self.car.min_rear_rh_static)
        else:
            # Snap pushrods to 0.5mm increments (iRacing garage constraint)
            front_pushrod = round(self.car.pushrod.front_offset_for_rh(static_front) * 2) / 2

            # Rear pushrod: use multi-variable RH model if calibrated
            if rh_model.is_calibrated:
                # Use baseline heave/spring values (step2/step3 haven't run yet).
                # These will be reconciled after step2 in solve.py.
                baseline_third_nmm = self.car.rear_third_spring_nmm  # car default
                baseline_heave_perch = self.car.heave_spring.perch_offset_front_baseline_mm
                baseline_rear_spring = self.car.corner_spring.rear_spring_range_nmm[0]
                # Ferrari RH model was calibrated with INDEX inputs (0-9 heave, 0-18 torsion),
                # not physical N/mm. Convert physical baselines to index space.
                # Also: Ferrari RH model feature 4 = rear_third_perch (not front_heave_perch).
                if self.car.canonical_name == 'ferrari':
                    from car_model.setup_registry import public_output_value
                    baseline_third_nmm = float(public_output_value('ferrari', 'rear_third_nmm', baseline_third_nmm))
                    baseline_rear_spring = float(public_output_value('ferrari', 'rear_spring_rate_nmm', baseline_rear_spring))
                    baseline_heave_perch = self.car.heave_spring.perch_offset_rear_baseline_mm
                baseline_fuel = self.car.fuel_capacity_l
                baseline_spring_perch = self.car.corner_spring.rear_spring_perch_baseline_mm
                rear_pushrod = rh_model.pushrod_for_target_rh(
                    static_rear, baseline_third_nmm,
                    baseline_rear_spring, baseline_heave_perch,
                    fuel_l=baseline_fuel,
                    spring_perch_mm=baseline_spring_perch,
                )
            else:
                rear_pushrod = self.car.pushrod.rear_offset_for_rh(static_rear)
            rear_pushrod = round(rear_pushrod * 2) / 2

            # Recompute actual static RH from snapped pushrod
            if rh_model.front_is_calibrated:
                static_front = rh_model.predict_front_static_rh(
                    heave_nmm=self.car.front_heave_spring_nmm,
                    camber_deg=self.car.geometry.front_camber_baseline_deg,
                    pushrod_mm=front_pushrod,
                    perch_mm=self.car.heave_spring.perch_offset_front_baseline_mm,
                )
            else:
                static_front = self.car.pushrod.front_rh_for_offset(front_pushrod)
            if rh_model.is_calibrated:
                static_rear = rh_model.predict_rear_static_rh(
                    rear_pushrod, baseline_third_nmm,
                    baseline_rear_spring, baseline_heave_perch,
                    fuel_l=baseline_fuel,
                    spring_perch_mm=baseline_spring_perch,
                )
            else:
                static_rear = self.car.pushrod.rear_rh_for_offset(rear_pushrod)

        # Final safety clamp: pushrod snap (0.5mm rounding) can shift static RH
        # slightly below the sim-enforced minimum.  This is the definitive floor
        # that applies regardless of which code path was taken above.
        static_front = max(static_front, self.car.min_front_rh_static)
        static_rear = max(static_rear, self.car.min_rear_rh_static)

        front_min_p99 = actual_front_dyn - front_excursion_p99
        vb_margin = front_min_p99 - self.car.vortex_burst_threshold_mm

        ld_cost = ld - free_opt_ld if free_opt_ld > 0 else 0.0

        # Compute aero stall proximity at the dynamic front ride height
        stall = self.surface.stall_proximity(actual_front_dyn)

        return RakeSolution(
            dynamic_front_rh_mm=round(actual_front_dyn, 1),
            dynamic_rear_rh_mm=round(actual_rear_dyn, 1),
            rake_dynamic_mm=round(actual_rear_dyn - actual_front_dyn, 1),
            df_balance_pct=round(bal, 2),
            ld_ratio=round(ld, 3),
            front_rh_excursion_p99_mm=round(front_excursion_p99, 1),
            front_rh_min_p99_mm=round(front_min_p99, 1),
            vortex_burst_threshold_mm=self.car.vortex_burst_threshold_mm,
            vortex_burst_margin_mm=round(vb_margin, 1),
            static_front_rh_mm=round(static_front, 1),
            static_rear_rh_mm=round(static_rear, 1),
            rake_static_mm=round(static_rear - static_front, 1),
            front_pushrod_offset_mm=round(front_pushrod, 1),
            rear_pushrod_offset_mm=round(rear_pushrod, 1),
            aero_compression_front_mm=round(front_comp, 1),
            aero_compression_rear_mm=round(rear_comp, 1),
            compression_ref_speed_kph=track_speed,
            balance_error_pct=round(abs(bal - target_balance), 3),
            converged=converged,
            iterations=iterations,
            mode=mode,
            free_opt_ld=round(free_opt_ld, 3) if free_opt_ld > 0 else 0.0,
            ld_cost_of_pinning=round(ld_cost, 3) if free_opt_ld > 0 else 0.0,
            aero_state=stall["aero_state"],
            stall_factor=stall["stall_factor"],
        )

    def solve(
        self,
        target_balance: float | None = None,
        balance_tolerance: float = 0.1,
        fuel_load_l: float | None = None,
        pin_front_min: bool = True,
        current_rear_rh_dynamic_mm: float | None = None,
    ) -> RakeSolution:
        """Find optimal ride heights for target DF balance.

        Args:
            target_balance: Target DF balance (% front). If None, uses the
                car model's default_df_balance_pct.
            balance_tolerance: Maximum acceptable deviation from target (%).
            fuel_load_l: Fuel load in liters (affects mass for compression).
                If None, uses car.fuel_capacity_l (all LMDh GTP = 88.96L).
            pin_front_min: If True (default), pin front static RH at the sim
                minimum (30.0mm) and solve only for rear. This matches real
                GTP methodology where drivers always run minimum front RH for
                maximum absolute downforce.
            current_rear_rh_dynamic_mm: IBT-measured dynamic rear RH (driver
                anchor). If provided AND the measured rear achieves the
                target balance within tolerance, prefer it over the model's
                Brent root. NEVER lap-time-driven — strictly an honest
                fallback when the model and the IBT agree within tolerance.

        Returns:
            RakeSolution with dynamic targets, static settings, and pushrod offsets.
        """
        if fuel_load_l is None:
            fuel_load_l = getattr(self.car, 'fuel_capacity_l', 89.0)
        if target_balance is None:
            target_balance = self.car.default_df_balance_pct
        # Ride height excursion from track surface (use clean-track p99,
        # kerb strikes are not representative of sustained platform behavior)
        front_sv_p99 = (self.track.shock_vel_p99_front_clean_mps
                        if self.track.shock_vel_p99_front_clean_mps > 0
                        else self.track.shock_vel_p99_front_mps)
        front_excursion_p99 = self.car.rh_excursion_p99(front_sv_p99)

        if pin_front_min:
            return self._solve_pinned_front(
                target_balance, front_excursion_p99, fuel_load_l,
                current_rear_rh_dynamic_mm=current_rear_rh_dynamic_mm,
            )
        else:
            return self._solve_free(
                target_balance, balance_tolerance, front_excursion_p99, fuel_load_l
            )

    def solution_from_explicit_offsets(
        self,
        *,
        target_balance: float,
        fuel_load_l: float,
        front_pushrod_offset_mm: float | None = None,
        rear_pushrod_offset_mm: float | None = None,
        static_front_rh_mm: float | None = None,
        static_rear_rh_mm: float | None = None,
    ) -> RakeSolution:
        """Build a Step 1 solution from explicit garage ride-height controls."""
        comp = self.car.aero_compression
        track_speed = self._resolve_aero_speed()
        front_comp = comp.front_at_speed(track_speed)
        rear_comp = comp.rear_at_speed(track_speed)
        garage_model = self.car.active_garage_output_model(self.track.track_name)

        if garage_model is not None:
            baseline = garage_model.default_state(fuel_l=fuel_load_l)
            baseline_outputs = garage_model.predict(baseline)
            front_pushrod = (
                round(float(front_pushrod_offset_mm) * 2.0) / 2.0
                if front_pushrod_offset_mm is not None
                else round(
                    garage_model.front_pushrod_for_static_rh(
                        float(static_front_rh_mm if static_front_rh_mm is not None else baseline_outputs.front_static_rh_mm),
                        front_heave_nmm=baseline.front_heave_nmm,
                        front_heave_perch_mm=baseline.front_heave_perch_mm,
                        front_torsion_od_mm=baseline.front_torsion_od_mm,
                        front_camber_deg=baseline.front_camber_deg,
                        fuel_l=fuel_load_l,
                        rear_pushrod_mm=baseline.rear_pushrod_mm,
                        rear_third_nmm=baseline.rear_third_nmm,
                        rear_third_perch_mm=baseline.rear_third_perch_mm,
                        rear_spring_nmm=baseline.rear_spring_nmm,
                        rear_spring_perch_mm=baseline.rear_spring_perch_mm,
                        rear_camber_deg=baseline.rear_camber_deg,
                        wing_deg=baseline.wing_deg,
                    ) * 2.0
                ) / 2.0
            )
            rear_pushrod = (
                round(float(rear_pushrod_offset_mm) * 2.0) / 2.0
                if rear_pushrod_offset_mm is not None
                else round(
                    garage_model.rear_pushrod_for_static_rh(
                        float(static_rear_rh_mm if static_rear_rh_mm is not None else baseline_outputs.rear_static_rh_mm),
                        rear_third_nmm=baseline.rear_third_nmm,
                        rear_third_perch_mm=baseline.rear_third_perch_mm,
                        rear_spring_nmm=baseline.rear_spring_nmm,
                        rear_spring_perch_mm=baseline.rear_spring_perch_mm,
                        front_heave_perch_mm=baseline.front_heave_perch_mm,
                        fuel_l=fuel_load_l,
                        front_pushrod_mm=front_pushrod,
                        front_heave_nmm=baseline.front_heave_nmm,
                        front_torsion_od_mm=baseline.front_torsion_od_mm,
                        front_camber_deg=baseline.front_camber_deg,
                        rear_camber_deg=baseline.rear_camber_deg,
                        wing_deg=baseline.wing_deg,
                    ) * 2.0
                ) / 2.0
            )
            # When the caller explicitly provides static_front_rh_mm /
            # static_rear_rh_mm (e.g. materialize_overrides passing the
            # rake-pinned base_result), TRUST those values rather than
            # recomputing from the BASELINE springs in the garage_model.
            # The base_result was produced by the rake solver + reconcile
            # with the actual chosen springs, so its static is correct.
            # Recomputing here with default heave/perch erases that work
            # and shifts pinned-front output upward by 2-3 mm. Calibrated
            # 2026-04-07 against Porsche/Algarve where the base solve
            # returned static_F=30 (pinned) but recomputation drifted to
            # 32.78 mm with heave_baseline=180.  reconcile_ride_heights
            # then used 32.78 as its target, propagating the drift.
            if static_front_rh_mm is not None:
                static_front = max(float(static_front_rh_mm), self.car.min_front_rh_static)
            else:
                outputs_f = garage_model.predict(GarageSetupState(
                    front_pushrod_mm=front_pushrod,
                    rear_pushrod_mm=rear_pushrod,
                    front_heave_nmm=baseline.front_heave_nmm,
                    front_heave_perch_mm=baseline.front_heave_perch_mm,
                    rear_third_nmm=baseline.rear_third_nmm,
                    rear_third_perch_mm=baseline.rear_third_perch_mm,
                    front_torsion_od_mm=baseline.front_torsion_od_mm,
                    rear_spring_nmm=baseline.rear_spring_nmm,
                    rear_spring_perch_mm=baseline.rear_spring_perch_mm,
                    front_camber_deg=baseline.front_camber_deg,
                    rear_camber_deg=baseline.rear_camber_deg,
                    fuel_l=fuel_load_l,
                ))
                static_front = max(float(outputs_f.front_static_rh_mm), self.car.min_front_rh_static)
            if static_rear_rh_mm is not None:
                static_rear = max(float(static_rear_rh_mm), self.car.min_rear_rh_static)
            else:
                outputs_r = garage_model.predict(GarageSetupState(
                    front_pushrod_mm=front_pushrod,
                    rear_pushrod_mm=rear_pushrod,
                    front_heave_nmm=baseline.front_heave_nmm,
                    front_heave_perch_mm=baseline.front_heave_perch_mm,
                    rear_third_nmm=baseline.rear_third_nmm,
                    rear_third_perch_mm=baseline.rear_third_perch_mm,
                    front_torsion_od_mm=baseline.front_torsion_od_mm,
                    rear_spring_nmm=baseline.rear_spring_nmm,
                    rear_spring_perch_mm=baseline.rear_spring_perch_mm,
                    front_camber_deg=baseline.front_camber_deg,
                    rear_camber_deg=baseline.rear_camber_deg,
                    fuel_l=fuel_load_l,
                ))
                static_rear = max(float(outputs_r.rear_static_rh_mm), self.car.min_rear_rh_static)
        else:
            front_pushrod = (
                round(float(front_pushrod_offset_mm) * 2.0) / 2.0
                if front_pushrod_offset_mm is not None
                else round(self.car.pushrod.front_offset_for_rh(float(static_front_rh_mm or self.car.min_front_rh_static)) * 2.0) / 2.0
            )
            rear_pushrod = (
                round(float(rear_pushrod_offset_mm) * 2.0) / 2.0
                if rear_pushrod_offset_mm is not None
                else round(self.car.pushrod.rear_offset_for_rh(float(static_rear_rh_mm or self.car.min_rear_rh_static)) * 2.0) / 2.0
            )
            static_front = max(
                float(
                    static_front_rh_mm
                    if static_front_rh_mm is not None
                    else self.car.pushrod.front_rh_for_offset(front_pushrod)
                ),
                self.car.min_front_rh_static,
            )
            static_rear = max(
                float(
                    static_rear_rh_mm
                    if static_rear_rh_mm is not None
                    else self.car.pushrod.rear_rh_for_offset(rear_pushrod)
                ),
                self.car.min_rear_rh_static,
            )

        actual_front_dyn = max(self.car.min_front_rh_dynamic, static_front - front_comp)
        actual_rear_dyn = max(self.car.min_rear_rh_dynamic, static_rear - rear_comp)
        bal, ld = self._query_aero(actual_front_dyn, actual_rear_dyn)
        front_sv_p99 = (self.track.shock_vel_p99_front_clean_mps
                        if self.track.shock_vel_p99_front_clean_mps > 0
                        else self.track.shock_vel_p99_front_mps)
        front_excursion_p99 = self.car.rh_excursion_p99(front_sv_p99)
        front_min_p99 = actual_front_dyn - front_excursion_p99
        vb_margin = front_min_p99 - self.car.vortex_burst_threshold_mm
        stall = self.surface.stall_proximity(actual_front_dyn)
        return RakeSolution(
            dynamic_front_rh_mm=round(actual_front_dyn, 1),
            dynamic_rear_rh_mm=round(actual_rear_dyn, 1),
            rake_dynamic_mm=round(actual_rear_dyn - actual_front_dyn, 1),
            df_balance_pct=round(bal, 2),
            ld_ratio=round(ld, 3),
            front_rh_excursion_p99_mm=round(front_excursion_p99, 1),
            front_rh_min_p99_mm=round(front_min_p99, 1),
            vortex_burst_threshold_mm=self.car.vortex_burst_threshold_mm,
            vortex_burst_margin_mm=round(vb_margin, 1),
            static_front_rh_mm=round(static_front, 1),
            static_rear_rh_mm=round(static_rear, 1),
            rake_static_mm=round(static_rear - static_front, 1),
            front_pushrod_offset_mm=round(front_pushrod, 1),
            rear_pushrod_offset_mm=round(rear_pushrod, 1),
            aero_compression_front_mm=round(front_comp, 1),
            aero_compression_rear_mm=round(rear_comp, 1),
            compression_ref_speed_kph=track_speed,
            balance_error_pct=round(abs(bal - target_balance), 3),
            converged=True,
            iterations=0,
            mode="explicit_overrides",
            free_opt_ld=0.0,
            ld_cost_of_pinning=0.0,
            aero_state=stall["aero_state"],
            stall_factor=stall["stall_factor"],
        )

    def _solve_pinned_front(
        self,
        target_balance: float,
        front_excursion_p99: float,
        fuel_load_l: float,
        current_rear_rh_dynamic_mm: float | None = None,
    ) -> RakeSolution:
        """Solve with front static RH pinned at sim minimum.

        This is the standard GTP approach: minimum front RH for maximum DF,
        then find rear RH to achieve target balance.
        """
        comp = self.car.aero_compression
        track_speed = self._resolve_aero_speed()

        # Front static = sim minimum → front dynamic = minimum - compression
        static_front = self.car.min_front_rh_static
        dyn_front = static_front - comp.front_at_speed(track_speed)

        # Check vortex burst constraint
        min_front_for_vortex = (
            self.car.vortex_burst_threshold_mm + front_excursion_p99
        )
        if dyn_front < min_front_for_vortex:
            # Front too low — would vortex burst. Raise to safe minimum.
            dyn_front = min_front_for_vortex
            static_front = dyn_front + comp.front_at_speed(track_speed)

        # Find rear RH for target balance (with optional driver anchor)
        dyn_rear = self._find_rear_for_balance(
            dyn_front, target_balance,
            current_rear_rh_dynamic_mm=current_rear_rh_dynamic_mm,
        )
        if dyn_rear is None:
            # Balance target not achievable at this front RH — target lies outside
            # the aero map's achievable range. Two causes:
            #   (a) Dynamic front RH is too low, pushing balance above target (Ferrari at
            #       low front RH runs >51% front regardless of rear height adjustment).
            #   (b) Aero map coverage doesn't extend to the requested ride heights.
            #
            # Fallback strategy: find the closest-achievable balance by scanning
            # the rear RH range and picking the rear height that minimises |balance - target|.
            # This allows the solver to produce a physically valid setup with a warning
            # rather than crashing.  The balance_error_pct field on the solution will flag
            # the deviation for the caller to handle.
            import warnings
            rear_lo = self.car.min_rear_rh_dynamic
            rear_hi = self.car.max_rear_rh_dynamic
            best_rear = rear_lo
            best_err = float("inf")
            for rear_candidate in [rear_lo + i * (rear_hi - rear_lo) / 50 for i in range(51)]:
                try:
                    bal, _ = self._query_aero(dyn_front, rear_candidate)
                    err = abs(bal - target_balance)
                    if err < best_err:
                        best_err = err
                        best_rear = rear_candidate
                except Exception as e:
                    logger.debug("Rear RH search iteration failed: %s", e)
                    continue
            achieved_bal, _ = self._query_aero(dyn_front, best_rear)
            warnings.warn(
                f"[rake_solver] Cannot achieve {target_balance:.2f}% DF balance at "
                f"dynamic front RH {dyn_front:.1f}mm for {self.car.canonical_name}. "
                f"Closest achievable: {achieved_bal:.2f}% at rear_dyn={best_rear:.1f}mm "
                f"(error {best_err:.2f}pp). Check default_df_balance_pct for this car.",
                stacklevel=3,
            )
            dyn_rear = best_rear

        # ── Garage feasibility check ──────────────────────────────────
        # The rear dynamic RH implies a static RH (via aero compression).
        # If that static RH requires a pushrod beyond the garage range, cap
        # the dynamic rear to the maximum achievable and accept the balance
        # error rather than producing an impossible setup.
        rear_comp = comp.rear_at_speed(track_speed)
        implied_static_rear = dyn_rear + rear_comp
        pushrod_range = self.car.garage_ranges.rear_pushrod_mm
        max_pushrod = pushrod_range[1]  # upper garage limit

        rh_model = self.car.ride_height_model
        if rh_model.is_calibrated and abs(rh_model.rear_coeff_pushrod) > 1e-6:
            # Compute the max static rear RH achievable at max pushrod
            # using baseline spring values (step 2/3 haven't run yet)
            baseline_third = self.car.rear_third_spring_nmm
            baseline_spring = self.car.corner_spring.rear_spring_range_nmm[0]
            baseline_perch = self.car.heave_spring.perch_offset_rear_baseline_mm
            baseline_fuel = self.car.fuel_capacity_l
            baseline_spring_perch = self.car.corner_spring.rear_spring_perch_baseline_mm
            max_static_rear = rh_model.predict_rear_static_rh(
                max_pushrod, baseline_third, baseline_spring,
                baseline_perch, fuel_l=baseline_fuel,
                spring_perch_mm=baseline_spring_perch,
            )
            if implied_static_rear > max_static_rear:
                # Cap to maximum achievable
                capped_dyn_rear = max_static_rear - rear_comp
                capped_bal, _ = self._query_aero(dyn_front, capped_dyn_rear)
                import warnings
                warnings.warn(
                    f"[rake_solver] Target rear static RH {implied_static_rear:.1f}mm "
                    f"exceeds garage max ({max_static_rear:.1f}mm at pushrod={max_pushrod}mm). "
                    f"Capping rear dynamic to {capped_dyn_rear:.1f}mm "
                    f"(DF balance {capped_bal:.2f}% vs target {target_balance:.2f}%).",
                    stacklevel=3,
                )
                dyn_rear = capped_dyn_rear

        # Also find the free-optimization L/D for comparison
        free_opt_ld = self._find_free_max_ld(target_balance, front_excursion_p99)

        return self._build_solution(
            actual_front_dyn=dyn_front,
            actual_rear_dyn=dyn_rear,
            front_excursion_p99=front_excursion_p99,
            target_balance=target_balance,
            fuel_load_l=fuel_load_l,
            converged=True,
            iterations=1,  # Root finding, not iterative optimization
            mode="pinned_front",
            free_opt_ld=free_opt_ld,
        )

    def _solve_free(
        self,
        target_balance: float,
        balance_tolerance: float,
        front_excursion_p99: float,
        fuel_load_l: float,
    ) -> RakeSolution:
        """Solve with both front and rear freely optimized for max L/D."""
        min_front_for_vortex = (
            self.car.vortex_burst_threshold_mm + front_excursion_p99
        )
        # Also enforce static minimum constraint
        comp = self.car.aero_compression
        min_front_for_static = self.car.min_front_rh_static - comp.front_compression_mm

        front_lo = max(
            self.car.min_front_rh_dynamic,
            min_front_for_vortex,
            min_front_for_static,
        )
        front_hi = self.car.max_front_rh_dynamic
        rear_lo = self.car.min_rear_rh_dynamic
        rear_hi = self.car.max_rear_rh_dynamic

        def objective(x):
            actual_front, actual_rear = x
            _, ld = self._query_aero(actual_front, actual_rear)
            return -ld

        def balance_constraint(x):
            actual_front, actual_rear = x
            bal, _ = self._query_aero(actual_front, actual_rear)
            return balance_tolerance - abs(bal - target_balance)

        def vortex_constraint(x):
            return x[0] - front_excursion_p99 - self.car.vortex_burst_threshold_mm

        best_result = None
        best_ld = -np.inf

        front_starts = np.linspace(front_lo, front_hi, 5)
        rear_starts = np.linspace(rear_lo, rear_hi, 5)

        for f0 in front_starts:
            for r0 in rear_starts:
                try:
                    result = minimize(
                        objective,
                        x0=np.array([f0, r0]),
                        method="SLSQP",
                        bounds=[(front_lo, front_hi), (rear_lo, rear_hi)],
                        constraints=[
                            {"type": "ineq", "fun": balance_constraint},
                            {"type": "ineq", "fun": vortex_constraint},
                        ],
                        options={"maxiter": 200, "ftol": 1e-9},
                    )
                    if result.success and -result.fun > best_ld:
                        af, ar = result.x
                        bal, _ = self._query_aero(af, ar)
                        if abs(bal - target_balance) <= balance_tolerance + 0.01:
                            best_ld = -result.fun
                            best_result = result
                except Exception as e:
                    logger.debug("Rear RH search iteration failed: %s", e)
                    continue

        if best_result is None:
            raise RuntimeError("Solver failed to find a valid solution")

        return self._build_solution(
            actual_front_dyn=float(best_result.x[0]),
            actual_rear_dyn=float(best_result.x[1]),
            front_excursion_p99=front_excursion_p99,
            target_balance=target_balance,
            fuel_load_l=fuel_load_l,
            converged=best_result.success,
            iterations=best_result.nit,
            mode="free_optimization",
        )

    def _find_free_max_ld(
        self, target_balance: float, front_excursion_p99: float
    ) -> float:
        """Find the maximum L/D achievable at target balance (for comparison).

        Quick grid search — used to compute the L/D cost of pinning front RH.
        """
        best_ld = -np.inf
        for frh in self.surface.front_rh:
            for rrh in self.surface.rear_rh:
                # These are aero coords — convert to actual
                actual_front, actual_rear = self.car.from_aero_coords(
                    float(frh), float(rrh)
                )
                # Vortex check
                if actual_front < self.car.vortex_burst_threshold_mm + front_excursion_p99:
                    continue
                bal = self.surface.df_balance(float(frh), float(rrh))
                if abs(bal - target_balance) <= 0.5:
                    ld = self.surface.lift_drag(float(frh), float(rrh))
                    if ld > best_ld:
                        best_ld = ld
        return best_ld if best_ld > 0 else 0.0


def reconcile_ride_heights(
    car,
    step1: RakeSolution,
    step2,
    step3,
    step5=None,
    fuel_load_l: float = 0.0,
    track_name: str | None = None,
    verbose: bool = True,
    surface=None,
    track=None,
    target_balance: float | None = None,
    current_rear_rh_dynamic_mm: float | None = None,
) -> None:
    """Reconcile static ride heights after step2+step3 provide actual spring values.

    Modifies step1 in-place with refined static RH, pushrod, and rake values.
    Called from both solve.py and produce.py after steps 2 and 3.

    If surface and target_balance are provided, re-derives the correct dynamic
    rear RH from the aero balance target instead of relying on the potentially
    stale value in step1.dynamic_rear_rh_mm (which may have been computed with
    baseline springs in solution_from_explicit_offsets).

    If current_rear_rh_dynamic_mm is provided (IBT-measured dynamic rear RH),
    it is used as a driver anchor for the rear-balance search. NEVER lap-time-
    driven — only fires when the model and IBT agree on balance within
    tolerance.
    """
    garage_model = car.active_garage_output_model(track_name)
    if garage_model is not None:
        front_camber = (
            float(step5.front_camber_deg)
            if step5 is not None and hasattr(step5, "front_camber_deg")
            else float(car.geometry.front_camber_baseline_deg)
        )
        rear_camber = (
            float(step5.rear_camber_deg)
            if step5 is not None and hasattr(step5, "rear_camber_deg")
            else float(car.geometry.rear_camber_baseline_deg)
        )
        target_front_rh = max(
            car.min_front_rh_static,
            round(step1.dynamic_front_rh_mm + step1.aero_compression_front_mm, 3),
        )

        # If aero surface and balance target are available, re-derive the correct
        # dynamic rear RH.  solution_from_explicit_offsets may have computed
        # dynamic_rear_rh_mm with baseline springs, making it stale when the
        # candidate changed step2/step3 springs.
        corrected_dynamic_rear = step1.dynamic_rear_rh_mm
        if surface is not None and target_balance is not None and track is not None:
            try:
                rake_solver = RakeSolver(car, surface, track)
                corrected = rake_solver._find_rear_for_balance(
                    step1.dynamic_front_rh_mm, target_balance,
                    current_rear_rh_dynamic_mm=current_rear_rh_dynamic_mm,
                )
                if corrected is not None:
                    corrected_dynamic_rear = corrected
            except Exception as e:
                logger.debug("Rear RH balance correction failed: %s", e)

        target_rear_rh = max(
            car.min_rear_rh_static,
            round(corrected_dynamic_rear + step1.aero_compression_rear_mm, 3),
        )

        # ── Garage feasibility: cap rear static RH to what pushrod can achieve ──
        _pushrod_lo, _pushrod_hi = car.garage_ranges.rear_pushrod_mm
        _test_rear_pushrod = garage_model.rear_pushrod_for_static_rh(
            target_rear_rh,
            rear_third_nmm=step2.rear_third_nmm,
            rear_third_perch_mm=step2.perch_offset_rear_mm,
            rear_spring_nmm=step3.rear_spring_rate_nmm,
            rear_spring_perch_mm=step3.rear_spring_perch_mm,
            front_heave_perch_mm=step2.perch_offset_front_mm,
            fuel_l=fuel_load_l,
            # Provide full context for DirectRegression bisection
            front_pushrod_mm=step1.front_pushrod_offset_mm,
            front_heave_nmm=step2.front_heave_nmm,
            front_torsion_od_mm=step3.front_torsion_od_mm,
            front_camber_deg=float(car.geometry.front_camber_baseline_deg),
            rear_camber_deg=rear_camber,
        )
        if _test_rear_pushrod > _pushrod_hi or _test_rear_pushrod < _pushrod_lo:
            _clamped_pushrod = max(_pushrod_lo, min(_pushrod_hi, _test_rear_pushrod))
            _test_state = GarageSetupState(
                front_pushrod_mm=step1.front_pushrod_offset_mm,
                rear_pushrod_mm=_clamped_pushrod,
                front_heave_nmm=float(step2.front_heave_nmm),
                front_heave_perch_mm=float(step2.perch_offset_front_mm),
                rear_third_nmm=float(step2.rear_third_nmm),
                rear_third_perch_mm=float(step2.perch_offset_rear_mm),
                front_torsion_od_mm=float(step3.front_torsion_od_mm),
                rear_spring_nmm=float(step3.rear_spring_rate_nmm),
                rear_spring_perch_mm=float(step3.rear_spring_perch_mm),
                front_camber_deg=float(car.geometry.front_camber_baseline_deg),
                rear_camber_deg=rear_camber,
                fuel_l=float(fuel_load_l),
            )
            _max_rear_rh = garage_model.predict(_test_state).rear_static_rh_mm
            if verbose:
                _capped_dyn = _max_rear_rh - step1.aero_compression_rear_mm
                _capped_bal, _ = (
                    RakeSolver(car, surface, track)._query_aero(step1.dynamic_front_rh_mm, _capped_dyn)
                    if surface is not None and track is not None
                    else (0.0, 0.0)
                )
                print(
                    f"  Rear RH capped: target {target_rear_rh:.1f}mm exceeds garage "
                    f"(max {_max_rear_rh:.1f}mm at pushrod={_clamped_pushrod:.1f}mm). "
                    f"DF balance achievable: {_capped_bal:.2f}%"
                )
            target_rear_rh = _max_rear_rh
            corrected_dynamic_rear = target_rear_rh - step1.aero_compression_rear_mm

        # Solve for pushrod + perch to achieve target front RH.
        # When the heave spring changes (e.g., 180→600 N/mm), the pushrod alone
        # may not have enough range to compensate. In that case, adjust the perch.
        new_front_pushrod = step1.front_pushrod_offset_mm
        # When DirectRegression is available, always use bisection (the linear
        # front_coeff_pushrod may be zero because the feature is named differently
        # or the full model uses pushrod_sq instead of linear pushrod).
        _has_direct_front = garage_model._direct_front_rh is not None
        if _has_direct_front or abs(garage_model.front_coeff_pushrod) >= 0.05:
            new_front_pushrod = garage_model.front_pushrod_for_static_rh(
                target_front_rh,
                front_heave_nmm=step2.front_heave_nmm,
                front_heave_perch_mm=step2.perch_offset_front_mm,
                front_torsion_od_mm=step3.front_torsion_od_mm,
                front_camber_deg=front_camber,
                fuel_l=fuel_load_l,
                # Provide full context for DirectRegression bisection
                rear_pushrod_mm=step1.rear_pushrod_offset_mm,
                rear_third_nmm=step2.rear_third_nmm,
                rear_third_perch_mm=step2.perch_offset_rear_mm,
                rear_spring_nmm=step3.rear_spring_rate_nmm,
                rear_spring_perch_mm=step3.rear_spring_perch_mm,
                rear_camber_deg=rear_camber,
            )
            # If pushrod is out of range, adjust perch to compensate
            pushrod_lo, pushrod_hi = car.garage_ranges.front_pushrod_mm
            if new_front_pushrod < pushrod_lo or new_front_pushrod > pushrod_hi:
                # Clamp pushrod to range and solve for perch
                clamped_pushrod = max(pushrod_lo, min(pushrod_hi, new_front_pushrod))
                # How much RH delta does clamping cause?
                rh_at_clamped = garage_model.predict_front_static_rh_raw(
                    GarageSetupState(
                        front_pushrod_mm=clamped_pushrod,
                        rear_pushrod_mm=step1.rear_pushrod_offset_mm,
                        front_heave_nmm=step2.front_heave_nmm,
                        front_heave_perch_mm=step2.perch_offset_front_mm,
                        rear_third_nmm=step2.rear_third_nmm,
                        rear_third_perch_mm=step2.perch_offset_rear_mm,
                        front_torsion_od_mm=step3.front_torsion_od_mm,
                        rear_spring_nmm=step3.rear_spring_rate_nmm,
                        rear_spring_perch_mm=step3.rear_spring_perch_mm,
                        front_camber_deg=front_camber,
                        rear_camber_deg=rear_camber,
                        fuel_l=fuel_load_l,
                    )
                )
                rh_deficit = target_front_rh - rh_at_clamped
                # Perch can absorb the deficit
                perch_coeff = garage_model.front_coeff_heave_perch_mm
                if abs(perch_coeff) > 1e-6:
                    perch_delta = rh_deficit / perch_coeff
                    new_perch = step2.perch_offset_front_mm + perch_delta
                    new_perch = round(new_perch * 2) / 2
                    perch_lo, perch_hi = car.garage_ranges.front_heave_perch_mm
                    new_perch = max(perch_lo, min(perch_hi, new_perch))
                    step2.perch_offset_front_mm = new_perch
                    if verbose:
                        print(f"  Front perch adjusted to {new_perch:.1f}mm "
                              f"(compensates for heave {step2.front_heave_nmm:.0f} N/mm)")
                new_front_pushrod = clamped_pushrod
        new_rear_pushrod = garage_model.rear_pushrod_for_static_rh(
            target_rear_rh,
            rear_third_nmm=step2.rear_third_nmm,
            rear_third_perch_mm=step2.perch_offset_rear_mm,
            rear_spring_nmm=step3.rear_spring_rate_nmm,
            rear_spring_perch_mm=step3.rear_spring_perch_mm,
            front_heave_perch_mm=step2.perch_offset_front_mm,
            fuel_l=fuel_load_l,
            # Provide full context for DirectRegression bisection
            front_pushrod_mm=new_front_pushrod,
            front_heave_nmm=step2.front_heave_nmm,
            front_torsion_od_mm=step3.front_torsion_od_mm,
            front_camber_deg=front_camber,
            rear_camber_deg=rear_camber,
        )
        new_front_pushrod = round(new_front_pushrod * 2) / 2
        new_rear_pushrod = round(new_rear_pushrod * 2) / 2

        outputs = garage_model.predict(
            GarageSetupState(
                front_pushrod_mm=new_front_pushrod,
                rear_pushrod_mm=new_rear_pushrod,
                front_heave_nmm=float(step2.front_heave_nmm),
                front_heave_perch_mm=float(step2.perch_offset_front_mm),
                rear_third_nmm=float(step2.rear_third_nmm),
                rear_third_perch_mm=float(step2.perch_offset_rear_mm),
                front_torsion_od_mm=float(step3.front_torsion_od_mm),
                rear_spring_nmm=float(step3.rear_spring_rate_nmm),
                rear_spring_perch_mm=float(step3.rear_spring_perch_mm),
                front_camber_deg=float(front_camber),
                rear_camber_deg=float(rear_camber),
                fuel_l=float(fuel_load_l),
            ),
            front_excursion_p99_mm=step2.front_excursion_at_rate_mm,
        )

        if verbose:
            if abs(outputs.front_static_rh_mm - step1.static_front_rh_mm) > 0.05:
                print(
                    f"  Front RH round-trip: {step1.static_front_rh_mm:.1f} -> "
                    f"{outputs.front_static_rh_mm:.1f} mm "
                    f"(pushrod {step1.front_pushrod_offset_mm:.1f} -> {new_front_pushrod:.1f})"
                )
            if abs(outputs.rear_static_rh_mm - step1.static_rear_rh_mm) > 0.05:
                print(
                    f"  Rear RH round-trip: {step1.static_rear_rh_mm:.1f} -> "
                    f"{outputs.rear_static_rh_mm:.1f} mm "
                    f"(pushrod {step1.rear_pushrod_offset_mm:.1f} -> {new_rear_pushrod:.1f})"
                )

        step1.front_pushrod_offset_mm = round(new_front_pushrod, 1)
        step1.rear_pushrod_offset_mm = round(new_rear_pushrod, 1)
        # Apply min-RH clamps: garage model can predict below-minimum values when
        # pushrods are at low offsets or spring rates are too soft. Enforce the
        # sim-mandated floor here so reconcile never writes a negative/illegal RH.
        step1.static_front_rh_mm = round(max(outputs.front_static_rh_mm, car.min_front_rh_static), 1)
        step1.static_rear_rh_mm = round(max(outputs.rear_static_rh_mm, car.min_rear_rh_static), 1)
        step1.rake_static_mm = round(step1.static_rear_rh_mm - step1.static_front_rh_mm, 1)

        # Update dynamic fields if we corrected the rear target from aero balance
        if corrected_dynamic_rear != step1.dynamic_rear_rh_mm:
            step1.dynamic_rear_rh_mm = round(corrected_dynamic_rear, 1)
            step1.rake_dynamic_mm = round(
                step1.dynamic_rear_rh_mm - step1.dynamic_front_rh_mm, 1
            )
            # Re-query aero balance and L/D at corrected operating point
            if surface is not None:
                try:
                    af, ar = car.to_aero_coords(
                        step1.dynamic_front_rh_mm, step1.dynamic_rear_rh_mm
                    )
                    step1.df_balance_pct = round(surface.df_balance(af, ar), 2)
                    step1.ld_ratio = round(surface.lift_drag(af, ar), 3)
                except Exception as e:
                    logger.debug("Aero balance update failed: %s", e)
        return

    rh_model = car.ride_height_model

    # Front RH: when heave spring changes from step2, adjust perch to maintain
    # the original target static RH (dynamic + compression). Without this, a
    # stiffer heave produces a higher static RH than intended.
    if rh_model.front_is_calibrated:
        front_camber = car.geometry.front_camber_baseline_deg
        _heave_for_rh = step2.front_heave_nmm
        if car.canonical_name == 'ferrari':
            from car_model.setup_registry import public_output_value
            _heave_for_rh = float(public_output_value('ferrari', 'front_heave_nmm', step2.front_heave_nmm))

        # Target static RH: the value step1 computed (dynamic + compression)
        target_static_front = step1.static_front_rh_mm
        _perch_coeff = rh_model.front_coeff_perch

        if abs(_perch_coeff) > 1e-6:
            # Solve for perch: target = intercept + coeff_heave*heave + coeff_camber*camber
            #                           + coeff_pushrod*pushrod + coeff_perch*perch
            # perch = (target - rest) / coeff_perch
            rest = (rh_model.front_intercept
                    + rh_model.front_coeff_heave_nmm * _heave_for_rh
                    + rh_model.front_coeff_camber_deg * front_camber
                    + rh_model.front_coeff_pushrod * step1.front_pushrod_offset_mm)
            new_perch = (target_static_front - rest) / _perch_coeff
            new_perch = round(new_perch * 2) / 2  # snap to 0.5mm garage step
            # Clamp to valid range
            perch_lo, perch_hi = car.garage_ranges.front_heave_perch_mm
            new_perch = max(perch_lo, min(perch_hi, new_perch))
            # Update step2 perch and recompute actual static RH
            step2.perch_offset_front_mm = new_perch
            new_front_rh = rh_model.predict_front_static_rh(
                _heave_for_rh, front_camber,
                pushrod_mm=step1.front_pushrod_offset_mm,
                perch_mm=new_perch,
            )
            if verbose and abs(new_perch - car.heave_spring.perch_offset_front_baseline_mm) > 0.1:
                print(f"  Front perch adjusted: {car.heave_spring.perch_offset_front_baseline_mm:.1f} -> "
                      f"{new_perch:.1f} mm (heave {step2.front_heave_nmm:.0f} -> maintains {new_front_rh:.1f}mm static RH)")
        else:
            new_front_rh = rh_model.predict_front_static_rh(
                _heave_for_rh, front_camber,
                pushrod_mm=step1.front_pushrod_offset_mm,
                perch_mm=car.heave_spring.perch_offset_front_baseline_mm,
            )

        if abs(new_front_rh - step1.static_front_rh_mm) > 0.05:
            if verbose:
                print(f"  Front RH refined: {step1.static_front_rh_mm:.1f} -> "
                      f"{new_front_rh:.1f} mm (heave {step2.front_heave_nmm:.0f} N/mm)")
            step1.static_front_rh_mm = round(new_front_rh, 1)

    # Rear RH: refine with actual spring values from step2+step3
    if rh_model.is_calibrated:
        actual_third_nmm = step2.rear_third_nmm
        actual_heave_perch = step2.perch_offset_front_mm
        actual_rear_spring = step3.rear_spring_rate_nmm
        actual_spring_perch = step3.rear_spring_perch_mm

        # Ferrari RH model was calibrated with INDEX inputs, not physical N/mm.
        # Convert solver's physical values to index space for the regression.
        # Also: Ferrari feature 4 = rear_third_perch (not front_heave_perch).
        _third_for_rh = actual_third_nmm
        _rear_spring_for_rh = actual_rear_spring
        _heave_perch_for_rh = actual_heave_perch
        if car.canonical_name == 'ferrari':
            from car_model.setup_registry import public_output_value
            _third_for_rh = float(public_output_value('ferrari', 'rear_third_nmm', actual_third_nmm))
            _rear_spring_for_rh = float(public_output_value('ferrari', 'rear_spring_rate_nmm', actual_rear_spring))
            _heave_perch_for_rh = step2.perch_offset_rear_mm

        _fuel_for_rh = car.fuel_capacity_l
        predicted_rh = rh_model.predict_rear_static_rh(
            step1.rear_pushrod_offset_mm, _third_for_rh,
            _rear_spring_for_rh, _heave_perch_for_rh,
            fuel_l=_fuel_for_rh,
            spring_perch_mm=actual_spring_perch,
        )
        rh_error = predicted_rh - step1.static_rear_rh_mm

        if abs(rh_error) > 0.5:
            new_pushrod = rh_model.pushrod_for_target_rh(
                step1.static_rear_rh_mm, _third_for_rh,
                _rear_spring_for_rh, _heave_perch_for_rh,
                fuel_l=_fuel_for_rh,
                spring_perch_mm=actual_spring_perch,
            )
            new_pushrod = round(new_pushrod * 2) / 2  # snap to 0.5mm
            new_rh = rh_model.predict_rear_static_rh(
                new_pushrod, _third_for_rh,
                _rear_spring_for_rh, _heave_perch_for_rh,
                fuel_l=_fuel_for_rh,
                spring_perch_mm=actual_spring_perch,
            )
            if verbose:
                print(f"  RH reconciliation: pushrod {step1.rear_pushrod_offset_mm:.1f} "
                      f"-> {new_pushrod:.1f} mm "
                      f"(predicted RH {predicted_rh:.1f} -> {new_rh:.1f} mm)")
            step1.rear_pushrod_offset_mm = round(new_pushrod, 1)
            step1.static_rear_rh_mm = round(new_rh, 1)
        elif verbose:
            print(f"  RH model check: predicted {predicted_rh:.1f} mm "
                  f"vs target {step1.static_rear_rh_mm:.1f} mm "
                  f"(error {rh_error:+.2f} mm — OK)")

    # Final floor clamp: regression models can extrapolate below the sim minimum.
    # This is the authoritative floor for the non-garage-model path.
    if step1.static_rear_rh_mm < car.min_rear_rh_static:
        if verbose:
            print(f"  Rear RH clamped: {step1.static_rear_rh_mm:.1f} -> "
                  f"{car.min_rear_rh_static:.1f} mm (regression below sim floor)")
        step1.static_rear_rh_mm = round(car.min_rear_rh_static, 1)
    if step1.static_front_rh_mm < car.min_front_rh_static:
        if verbose:
            print(f"  Front RH clamped: {step1.static_front_rh_mm:.1f} -> "
                  f"{car.min_front_rh_static:.1f} mm (regression below sim floor)")
        step1.static_front_rh_mm = round(car.min_front_rh_static, 1)
    step1.rake_static_mm = round(step1.static_rear_rh_mm - step1.static_front_rh_mm, 1)

    # Front pushrod reconcile — for cars with a heave-perch-dependent front RH model
    # (e.g. Cadillac). After step2 we know the actual heave perch, so re-solve pushrod.
    # Pushrod reconciliation for perch changes: only use PushrodGeometry when
    # the RideHeightModel is NOT calibrated. When the RH model IS calibrated,
    # the perch adjustment above (lines 895-927) already handled the compensation
    # correctly using the full 4-variable model. PushrodGeometry doesn't account
    # for heave rate and would compute a wrong pushrod at different heave rates.
    if (abs(car.pushrod.front_heave_perch_to_rh) > 1e-6
            and hasattr(step2, "perch_offset_front_mm")
            and not rh_model.front_is_calibrated):
        target_front = max(car.min_front_rh_static, step1.static_front_rh_mm)
        actual_perch = float(step2.perch_offset_front_mm)
        new_front_pushrod = round(
            car.pushrod.front_offset_for_rh(target_front, heave_perch_mm=actual_perch) * 2
        ) / 2
        new_front_rh = car.pushrod.front_rh_for_offset(new_front_pushrod, heave_perch_mm=actual_perch)
        if abs(new_front_pushrod - step1.front_pushrod_offset_mm) > 0.4:
            if verbose:
                print(f"  Front pushrod reconciled for perch {actual_perch:.1f}mm: "
                      f"{step1.front_pushrod_offset_mm:.1f} -> {new_front_pushrod:.1f} mm "
                      f"(RH {step1.static_front_rh_mm:.1f} -> {new_front_rh:.1f} mm)")
            step1.front_pushrod_offset_mm = new_front_pushrod
            step1.static_front_rh_mm = round(new_front_rh, 1)
            step1.rake_static_mm = round(step1.static_rear_rh_mm - step1.static_front_rh_mm, 1)

    # Update static rake from (possibly refined) front + rear
    step1.rake_static_mm = round(step1.static_rear_rh_mm - step1.static_front_rh_mm, 1)
