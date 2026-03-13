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

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize, brentq

from aero_model.interpolator import AeroSurface
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

    def _find_rear_for_balance(
        self, actual_front: float, target_balance: float
    ) -> float | None:
        """Find the actual rear RH that achieves target balance at a given front RH.

        Uses Brent's method (root finding) on the balance error.
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
            return result
        except ValueError:
            return None

    def _build_solution(
        self,
        actual_front_dyn: float,
        actual_rear_dyn: float,
        front_excursion_p99: float,
        target_balance: float,
        converged: bool,
        iterations: int,
        mode: str,
        free_opt_ld: float = 0.0,
    ) -> RakeSolution:
        """Build a RakeSolution from dynamic ride heights."""
        bal, ld = self._query_aero(actual_front_dyn, actual_rear_dyn)

        comp = self.car.aero_compression
        # Use track median speed for compression instead of fixed reference speed
        track_speed = self.track.median_speed_kph if self.track.median_speed_kph > 0 else comp.ref_speed_kph
        front_comp = comp.front_at_speed(track_speed)
        rear_comp = comp.rear_at_speed(track_speed)

        static_front = actual_front_dyn + front_comp
        static_rear = actual_rear_dyn + rear_comp

        # Clamp to sim-enforced minimums
        static_front = max(static_front, self.car.min_front_rh_static)
        static_rear = max(static_rear, self.car.min_rear_rh_static)

        # Snap pushrods to 0.5mm increments (iRacing garage constraint)
        front_pushrod = round(self.car.pushrod.front_offset_for_rh(static_front) * 2) / 2

        # Rear pushrod: use multi-variable RH model if calibrated
        rh_model = self.car.ride_height_model
        if rh_model.is_calibrated:
            # Use baseline heave/spring values (step2/step3 haven't run yet).
            # These will be reconciled after step2 in solve.py.
            baseline_third_nmm = self.car.rear_third_spring_nmm  # car default
            baseline_heave_perch = self.car.heave_spring.perch_offset_front_baseline_mm
            baseline_rear_spring = self.car.corner_spring.rear_spring_range_nmm[0]
            rear_pushrod = rh_model.pushrod_for_target_rh(
                static_rear, baseline_third_nmm,
                baseline_rear_spring, baseline_heave_perch,
            )
        else:
            rear_pushrod = self.car.pushrod.rear_offset_for_rh(static_rear)
        rear_pushrod = round(rear_pushrod * 2) / 2

        # Recompute actual static RH from snapped pushrod
        if rh_model.front_is_calibrated:
            static_front = rh_model.predict_front_static_rh(
                heave_nmm=self.car.front_heave_spring_nmm,
                camber_deg=self.car.geometry.front_camber_baseline_deg,
            )
        else:
            static_front = self.car.pushrod.front_rh_for_offset(front_pushrod)
        if rh_model.is_calibrated:
            static_rear = rh_model.predict_rear_static_rh(
                rear_pushrod, baseline_third_nmm,
                baseline_rear_spring, baseline_heave_perch,
            )
        else:
            static_rear = self.car.pushrod.rear_rh_for_offset(rear_pushrod)

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
        target_balance: float = 50.14,
        balance_tolerance: float = 0.1,
        fuel_load_l: float = 89.0,
        pin_front_min: bool = True,
    ) -> RakeSolution:
        """Find optimal ride heights for target DF balance.

        Args:
            target_balance: Target DF balance (% front). Default 50.14%.
            balance_tolerance: Maximum acceptable deviation from target (%).
            fuel_load_l: Fuel load in liters (affects mass for compression).
            pin_front_min: If True (default), pin front static RH at the sim
                minimum (30.0mm) and solve only for rear. This matches real
                GTP methodology where drivers always run minimum front RH for
                maximum absolute downforce.

        Returns:
            RakeSolution with dynamic targets, static settings, and pushrod offsets.
        """
        # Ride height excursion from track surface
        front_excursion_p99 = self.car.rh_excursion_p99(
            self.track.shock_vel_p99_front_mps
        )

        if pin_front_min:
            return self._solve_pinned_front(
                target_balance, front_excursion_p99, fuel_load_l
            )
        else:
            return self._solve_free(
                target_balance, balance_tolerance, front_excursion_p99, fuel_load_l
            )

    def _solve_pinned_front(
        self,
        target_balance: float,
        front_excursion_p99: float,
        fuel_load_l: float,
    ) -> RakeSolution:
        """Solve with front static RH pinned at sim minimum.

        This is the standard GTP approach: minimum front RH for maximum DF,
        then find rear RH to achieve target balance.
        """
        comp = self.car.aero_compression

        # Use track median speed for compression (consistent with _build_solution)
        track_speed = self.track.median_speed_kph if self.track.median_speed_kph > 0 else comp.ref_speed_kph

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

        # Find rear RH for target balance
        dyn_rear = self._find_rear_for_balance(dyn_front, target_balance)
        if dyn_rear is None:
            raise RuntimeError(
                f"Cannot achieve {target_balance:.2f}% balance at "
                f"dynamic front RH {dyn_front:.1f}mm. "
                f"Target may be outside the aero map range."
            )

        # Also find the free-optimization L/D for comparison
        free_opt_ld = self._find_free_max_ld(target_balance, front_excursion_p99)

        return self._build_solution(
            actual_front_dyn=dyn_front,
            actual_rear_dyn=dyn_rear,
            front_excursion_p99=front_excursion_p99,
            target_balance=target_balance,
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
                except Exception:
                    continue

        if best_result is None:
            raise RuntimeError("Solver failed to find a valid solution")

        return self._build_solution(
            actual_front_dyn=float(best_result.x[0]),
            actual_rear_dyn=float(best_result.x[1]),
            front_excursion_p99=front_excursion_p99,
            target_balance=target_balance,
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
    verbose: bool = True,
) -> None:
    """Reconcile static ride heights after step2+step3 provide actual spring values.

    Modifies step1 in-place with refined static RH, pushrod, and rake values.
    Called from both solve.py and produce.py after steps 2 and 3.
    """
    rh_model = car.ride_height_model

    # Front RH: refine with actual heave spring from step2
    if rh_model.front_is_calibrated:
        front_camber = car.geometry.front_camber_baseline_deg
        new_front_rh = rh_model.predict_front_static_rh(
            step2.front_heave_nmm, front_camber,
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

        predicted_rh = rh_model.predict_rear_static_rh(
            step1.rear_pushrod_offset_mm, actual_third_nmm,
            actual_rear_spring, actual_heave_perch,
        )
        rh_error = predicted_rh - step1.static_rear_rh_mm

        if abs(rh_error) > 0.5:
            new_pushrod = rh_model.pushrod_for_target_rh(
                step1.static_rear_rh_mm, actual_third_nmm,
                actual_rear_spring, actual_heave_perch,
            )
            new_pushrod = round(new_pushrod * 2) / 2  # snap to 0.5mm
            new_rh = rh_model.predict_rear_static_rh(
                new_pushrod, actual_third_nmm,
                actual_rear_spring, actual_heave_perch,
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

    # Update static rake from (possibly refined) front + rear
    step1.rake_static_mm = round(step1.static_rear_rh_mm - step1.static_front_rh_mm, 1)
