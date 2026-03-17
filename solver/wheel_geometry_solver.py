"""Step 5: Wheel Geometry Solver.

Determines optimal camber and toe angles for the tyre contact patch
characteristics of the track and expected tyre temperatures.

Physics:

    Camber:
    When a car corners, the body rolls. Negative static camber partially
    compensates for this roll, keeping the outer tyre more upright (better
    contact patch) at peak lateral load.

        camber_dynamic = camber_static + roll * roll_gain

    At peak lateral g (worst case), we want camber_dynamic ≈ 0° for maximum
    contact patch. Therefore:

        camber_optimal = -(roll_at_peak_g * roll_gain)

    Body roll at peak lateral g:
        roll_deg ≈ m * ay * h_cg / (K_roll_total * (π/180))

    Where K_roll_total is the total roll stiffness (springs + ARBs) from Step 4.

    Practically: more negative camber → more inner tyre heat, better cornering
    grip but worse longitudinal grip (narrower contact patch under braking/accel).

    Vision tread model (S1 2026): Optimal inner/outer temp spread ≈ 5-8°C
    (inner slightly hotter = correct load and camber). If outer runs hotter,
    camber is too positive (or tyre overcrowning from high pressure).

    Toe:
    Front toe-out (negative) → improves turn-in by pointing the outside front
    tyre slightly into the corner. Increases front tyre heating and scrub.
    Keep small (0.3-0.5mm total toe-out on the BMW baseline).

    Rear toe-in (positive) → stabilizes the rear, adds a self-centering force.
    Rear toe-out is generally destabilizing. BMW baseline: 0mm rear toe (neutral).

    Thermal adjustment:
    If tyres condition too slowly (Vision tread needs 8-15 laps), increase
    front toe-out to accelerate heating. If overheating, reduce toe-out.
    BMW fronts condition at +2.2-2.6°C/lap — moderate toe-out (-0.4mm) is correct.

Validated against BMW Sebring:
    - Front camber: -2.9°, Rear camber: -1.9°
    - Front toe: -0.4mm (slight toe-out), Rear toe: 0mm
    - These match the theoretical optima at BMW's typical roll angle and
      lateral g envelope at Sebring.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from car_model.cars import CarModel
from track_model.profile import TrackProfile


@dataclass
class GeometryConstraintCheck:
    """Result of a single geometry constraint check."""
    name: str
    passed: bool
    value: float
    target: float
    units: str
    note: str = ""


@dataclass
class WheelGeometrySolution:
    """Output of the Step 5 wheel geometry solver."""

    # Recommended alignment
    front_camber_deg: float
    rear_camber_deg: float
    front_toe_mm: float
    rear_toe_mm: float

    # Camber physics
    peak_lat_g: float
    body_roll_at_peak_deg: float
    front_camber_change_at_peak_deg: float   # Dynamic camber at peak g
    rear_camber_change_at_peak_deg: float
    front_dynamic_camber_at_peak_deg: float  # static + dynamic change
    rear_dynamic_camber_at_peak_deg: float

    # Deviation from baselines
    front_camber_delta_from_baseline: float
    rear_camber_delta_from_baseline: float
    front_toe_delta_from_baseline: float
    rear_toe_delta_from_baseline: float

    # Thermal prediction
    expected_conditioning_laps_front: float
    expected_conditioning_laps_rear: float

    # Roll stiffness input (from Step 4)
    k_roll_total_nm_deg: float

    # Constraint checks
    constraints: list[GeometryConstraintCheck]

    # Camber confidence: "estimated" (physics model) or "calibrated" (thermal data)
    camber_confidence: str = "estimated"
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "===========================================================",
            "  STEP 5: WHEEL GEOMETRY SOLUTION",
            "===========================================================",
            "",
            "  ALIGNMENT SETTINGS",
            f"    Front camber:  {self.front_camber_deg:+.1f}°",
            f"    Rear camber:   {self.rear_camber_deg:+.1f}°",
            f"    Front toe:     {self.front_toe_mm:+.1f} mm  "
            f"({'toe-out' if self.front_toe_mm < 0 else 'toe-in' if self.front_toe_mm > 0 else 'neutral'})",
            f"    Rear toe:      {self.rear_toe_mm:+.1f} mm  "
            f"({'toe-out' if self.rear_toe_mm < 0 else 'toe-in' if self.rear_toe_mm > 0 else 'neutral'})",
            "",
            "  DELTA FROM CALIBRATED BASELINE",
            f"    Front camber:  {self.front_camber_delta_from_baseline:+.1f}°",
            f"    Rear camber:   {self.rear_camber_delta_from_baseline:+.1f}°",
            f"    Front toe:     {self.front_toe_delta_from_baseline:+.1f} mm",
            f"    Rear toe:      {self.rear_toe_delta_from_baseline:+.1f} mm",
            "",
            "  CAMBER PHYSICS",
            f"    Peak lateral g:               {self.peak_lat_g:.2f} g",
            f"    Body roll at peak:            {self.body_roll_at_peak_deg:.2f}°",
            f"    Front camber change (roll):   {self.front_camber_change_at_peak_deg:+.2f}°",
            f"    Front dynamic camber @ peak:  {self.front_dynamic_camber_at_peak_deg:+.2f}°  "
            f"(target: ~0°)",
            f"    Rear camber change (roll):    {self.rear_camber_change_at_peak_deg:+.2f}°",
            f"    Rear dynamic camber @ peak:   {self.rear_dynamic_camber_at_peak_deg:+.2f}°  "
            f"(target: ~0°)",
            "",
            "  THERMAL PREDICTION (Vision tread model)",
            f"    Front tyre to operating window:  ~{self.expected_conditioning_laps_front:.0f} laps",
            f"    Rear tyre to operating window:   ~{self.expected_conditioning_laps_rear:.0f} laps",
        ]
        if self.constraints:
            lines += ["", "  CONSTRAINT CHECKS"]
            for c in self.constraints:
                status = "OK" if c.passed else "WARN"
                lines.append(f"    [{status}] {c.name}: {c.value:.2f} {c.units} "
                              f"(target: {c.target:.2f})")
                if c.note:
                    lines.append(f"         {c.note}")
        if self.notes:
            lines += ["", "  NOTES"]
            for note in self.notes:
                lines.append(f"    - {note}")
        lines.append("===========================================================")
        return "\n".join(lines)


class WheelGeometrySolver:
    """Step 5 solver: find camber and toe for optimal tyre utilization.

    Uses peak lateral g from the track profile and total roll stiffness
    from Step 4 to compute the body roll angle and target camber.
    """

    def __init__(self, car: CarModel, track: TrackProfile):
        self.car = car
        self.track = track

    def _body_roll_at_g(self, lat_g: float, k_roll_total_nm_deg: float,
                        fuel_load_l: float = 89.0) -> float:
        """Estimate body roll angle at a given lateral g.

        roll = m * ay * h_cg / K_roll_total
        Returns degrees.
        """
        mass_kg = self.car.total_mass(fuel_load_l=fuel_load_l)
        ay_ms2 = lat_g * 9.81
        h_cg_m = self.car.corner_spring.cg_height_mm / 1000.0
        t_avg_m = (
            self.car.arb.track_width_front_mm + self.car.arb.track_width_rear_mm
        ) / 2.0 / 1000.0

        # Roll moment: m * ay * h_cg (N·m)
        roll_moment = mass_kg * ay_ms2 * h_cg_m
        # Roll stiffness in N·m/deg → roll_deg = moment / stiffness
        if k_roll_total_nm_deg < 1.0:
            return 2.0  # fallback if stiffness not computed
        return roll_moment / k_roll_total_nm_deg

    def _optimal_camber(
        self,
        representative_roll_deg: float,
        roll_gain: float,
        baseline_deg: float,
        is_front: bool = True,
        measured: Any = None,
    ) -> float:
        """Compute optimal static camber from roll kinematics and thermal feedback.

        We want dynamic camber ≈ 0° at the REPRESENTATIVE cornering load.
        Additionally, we use thermal telemetry (if available) to correct the camber.
        """
        geo = self.car.geometry

        optimal = -(representative_roll_deg * roll_gain)
        crown_correction = -0.2
        optimal += crown_correction

        # Thermal and Wear feedback loop (if telemetry is provided)
        if measured is not None:
            if is_front:
                lf_wear_spread = getattr(measured, "lf_wear_spread", 0.0)
                rf_wear_spread = getattr(measured, "rf_wear_spread", 0.0)
                wear_spread = max(lf_wear_spread, rf_wear_spread)
                
                lf_spread = getattr(measured, "front_temp_spread_lf_c", 0.0)
                rf_spread = getattr(measured, "front_temp_spread_rf_c", 0.0)
                spread = max(lf_spread, rf_spread)
            else:
                lr_wear_spread = getattr(measured, "lr_wear_spread", 0.0)
                rr_wear_spread = getattr(measured, "rr_wear_spread", 0.0)
                wear_spread = max(lr_wear_spread, rr_wear_spread)
                
                lr_spread = getattr(measured, "rear_temp_spread_lr_c", 0.0)
                rr_spread = getattr(measured, "rear_temp_spread_rr_c", 0.0)
                spread = max(lr_spread, rr_spread)

            # Wear spread is the ultimate ground truth. If the inner tire is wearing significantly faster than outer
            # (e.g. inner - outer > 1.5%), we override temperature heuristics.
            if wear_spread > 1.5:
                optimal += 0.2
            elif wear_spread < -1.5:
                optimal -= 0.2
            else:
                # Target inner-outer spread is 5-8°C.
                # If spread > 10°C, inner is overheating -> reduce negative camber
                if spread > 10.0:
                    optimal += 0.2
                # If spread < 3°C, outer is doing too much work -> increase negative camber
                elif spread < 3.0 and spread > -5.0: # ignore wild negative values
                    optimal -= 0.2

        if is_front:
            c_min, c_max = geo.front_camber_range_deg
            step = geo.front_camber_step_deg
        else:
            c_min, c_max = geo.rear_camber_range_deg
            step = geo.rear_camber_step_deg
            
        optimal = max(c_min, min(c_max, optimal))
        return round(optimal / step) * step

    def _toe_recommendation(
        self,
        conditioning_rate_deg_per_lap: float,
        target_laps_to_op_temp: float,
        baseline_mm: float,
        is_front: bool,
    ) -> float:
        """Recommend toe adjustment based on thermal conditioning need.

        BMW fronts condition at +2.2-2.6°C/lap. Operating window at 85°C,
        starting at ~25°C cold → need ~60°C rise → ~25 laps at 2.4°C/lap.
        For sprint sessions, slightly more toe-out accelerates heating.

        For rear: toe-in (0mm baseline) — don't increase toe-out, it
        destabilizes the car. Rear toe is a stability parameter, not thermal.
        """
        geo = self.car.geometry
        # For rear: keep at baseline (stability)
        if not is_front:
            return baseline_mm

        # For front: if conditioning is very slow (<1.5°C/lap), add 0.2mm toe-out
        if conditioning_rate_deg_per_lap < 1.5:
            candidate = baseline_mm - 0.2
        elif conditioning_rate_deg_per_lap > 3.5:
            # Too fast → reduce toe-out to avoid overheating
            candidate = baseline_mm + 0.2
        else:
            candidate = baseline_mm

        # Clamp to valid range
        t_min, t_max = geo.front_toe_range_mm
        return max(t_min, min(t_max, round(candidate / geo.front_toe_step_mm) * geo.front_toe_step_mm))

    def _laps_to_operating_temp(
        self,
        conditioning_rate_deg_per_lap: float,
        toe_mm: float,
        is_front: bool,
    ) -> float:
        """Estimate laps to reach operating temperature window (85°C).

        Starting temp ≈ ambient + 15°C (tyre warmup from garage), target 85°C.
        Baseline conditioning rate adjusted for toe.
        """
        geo = self.car.geometry
        baseline_toe = geo.front_toe_baseline_mm if is_front else geo.rear_toe_baseline_mm
        heating_coeff = geo.front_toe_heating_coeff if is_front else geo.rear_toe_heating_coeff

        # Toe-out is negative → more toe-out = faster heating
        toe_delta = toe_mm - baseline_toe
        adjusted_rate = conditioning_rate_deg_per_lap - toe_delta * heating_coeff

        # Sebring ambient ~25°C, tyre cold start ~40°C
        temp_rise_needed = 85.0 - 40.0
        if adjusted_rate <= 0:
            return 50.0  # Tyre never reaches temp — clamp
        return min(50.0, temp_rise_needed / adjusted_rate)

    def solve(
        self,
        k_roll_total_nm_deg: float,
        front_wheel_rate_nmm: float,
        rear_wheel_rate_nmm: float,
        fuel_load_l: float = 89.0,
        camber_confidence: str = "estimated",
        measured: Any = None,
    ) -> WheelGeometrySolution:
        """Compute optimal wheel geometry.

        Args:
            k_roll_total_nm_deg: Total roll stiffness from Step 4 (N·m/deg)
            front_wheel_rate_nmm: Front wheel rate from Step 3 (N/mm)
            rear_wheel_rate_nmm: Rear wheel rate from Step 3 (N/mm)

        Returns:
            WheelGeometrySolution with camber, toe, and thermal predictions
        """
        geo = self.car.geometry
        peak_lat_g = self.track.peak_lat_g

        # Representative cornering load for camber optimization.
        #
        # Pure p95 optimization gives maximum grip at typical cornering but
        # suboptimal contact patch at peak load (corner apices). Pure peak
        # optimization gives best grip at the limit but too much negative
        # camber during moderate cornering (heat, drag, wear).
        #
        # The correct target depends on track character:
        # - High kerb severity (Sebring): transient roll events exceed
        #   steady-state, need more camber margin → weight toward peak
        # - Smooth tracks: less transient roll → weight toward p95
        #
        # Blend: representative_g = p95 + kerb_weight * (peak - p95)
        # kerb_weight = 0.3 (smooth) to 0.7 (heavy kerbs)
        if self.track.lateral_g and self.track.lateral_g.get("p95"):
            p95_lat_g = self.track.lateral_g["p95"]
        else:
            p95_lat_g = peak_lat_g * 0.47

        # Kerb severity: use number and intensity of kerb events
        n_kerbs = len(self.track.kerb_events)
        if n_kerbs > 10:
            kerb_weight = 0.7   # heavy kerbing (Sebring, Monza chicanes)
        elif n_kerbs > 5:
            kerb_weight = 0.5   # moderate
        else:
            kerb_weight = 0.3   # smooth (Daytona, Le Mans)

        representative_lat_g = p95_lat_g + kerb_weight * (peak_lat_g - p95_lat_g)

        # Body roll at peak lateral g (for dynamic camber check at worst case)
        roll_deg = self._body_roll_at_g(peak_lat_g, k_roll_total_nm_deg, fuel_load_l)
        # Body roll at representative lateral g (for camber optimization)
        representative_roll_deg = self._body_roll_at_g(
            representative_lat_g, k_roll_total_nm_deg, fuel_load_l
        )

        # Optimal static camber — optimized for p95 cornering load and thermal telemetry
        front_camber = self._optimal_camber(
            representative_roll_deg, geo.front_roll_gain, geo.front_camber_baseline_deg,
            is_front=True, measured=measured
        )
        rear_camber = self._optimal_camber(
            representative_roll_deg, geo.rear_roll_gain, geo.rear_camber_baseline_deg,
            is_front=False, measured=measured
        )
        # Rear range already applied in _optimal_camber via is_front=False
        r_min, r_max = geo.rear_camber_range_deg
        rear_camber = max(r_min, min(r_max, rear_camber))

        # Dynamic camber at peak lateral g (what the tyre actually sees)
        front_camber_change = roll_deg * geo.front_roll_gain
        rear_camber_change = roll_deg * geo.rear_roll_gain
        front_dynamic = front_camber + front_camber_change
        rear_dynamic = rear_camber + rear_camber_change

        # BMW Vision tread conditioning rates (from per-car-quirks.md)
        front_conditioning_rate = 2.4   # °C/lap baseline (Sebring)
        rear_conditioning_rate = 3.2    # °C/lap baseline

        # Toe recommendation
        front_toe = self._toe_recommendation(
            front_conditioning_rate, 25.0, geo.front_toe_baseline_mm, is_front=True
        )
        rear_toe = self._toe_recommendation(
            rear_conditioning_rate, 20.0, geo.rear_toe_baseline_mm, is_front=False
        )

        # Thermal predictions
        laps_front = self._laps_to_operating_temp(front_conditioning_rate, front_toe, is_front=True)
        laps_rear = self._laps_to_operating_temp(rear_conditioning_rate, rear_toe, is_front=False)

        # Constraint checks
        constraints = [
            GeometryConstraintCheck(
                name="Front dynamic camber at peak g",
                passed=abs(front_dynamic) < 1.0,
                value=front_dynamic,
                target=0.0,
                units="deg",
                note=f"Dynamic = static {front_camber:+.1f}° + roll change {front_camber_change:+.1f}°",
            ),
            GeometryConstraintCheck(
                name="Rear dynamic camber at peak g",
                passed=abs(rear_dynamic) < 1.0,
                value=rear_dynamic,
                target=0.0,
                units="deg",
                note=f"Dynamic = static {rear_camber:+.1f}° + roll change {rear_camber_change:+.1f}°",
            ),
            GeometryConstraintCheck(
                name="Front toe within reasonable range",
                passed=-1.5 <= front_toe <= 0.5,
                value=front_toe,
                target=-0.4,
                units="mm",
                note="Excessive toe-out increases tyre scrub and lap time"
                     if front_toe < -1.0 else "",
            ),
            GeometryConstraintCheck(
                name="Rear toe non-destabilizing",
                passed=rear_toe >= 0.0,
                value=rear_toe,
                target=0.0,
                units="mm",
                note="Rear toe-out is destabilizing — keep at or above 0mm",
            ),
            GeometryConstraintCheck(
                name="Front conditioning to op temp",
                passed=laps_front < 20.0,
                value=laps_front,
                target=15.0,
                units="laps",
                note="Vision tread: >20 laps indicates tyres may not reach temp in a stint",
            ),
        ]

        notes = [
            f"Representative cornering: {representative_lat_g:.2f}g "
            f"(p95={p95_lat_g:.2f}g, peak={peak_lat_g:.2f}g, "
            f"kerb_weight={kerb_weight:.1f}). "
            f"Roll: {representative_roll_deg:.1f}° at representative, "
            f"{roll_deg:.1f}° at peak.",
            "Tyre temperature spread diagnosis: inner hotter = correct camber. "
            "If outer runs hotter -> reduce negative camber by 0.2-0.3 deg.",
            "BMW Vision tread conditioning (Sebring): fronts +2.4°C/lap, "
            "rears +3.2°C/lap. Full operating temp by lap 13-15 (fronts), 8-9 (rears).",
            "For sprint qualifying: add 0.3° more negative camber + 0.2mm extra "
            "toe-out to accelerate thermal buildup.",
        ]

        return WheelGeometrySolution(
            front_camber_deg=round(front_camber, 1),
            rear_camber_deg=round(rear_camber, 1),
            front_toe_mm=round(front_toe, 1),
            rear_toe_mm=round(rear_toe, 1),
            peak_lat_g=round(peak_lat_g, 2),
            body_roll_at_peak_deg=round(roll_deg, 2),
            front_camber_change_at_peak_deg=round(front_camber_change, 2),
            rear_camber_change_at_peak_deg=round(rear_camber_change, 2),
            front_dynamic_camber_at_peak_deg=round(front_dynamic, 2),
            rear_dynamic_camber_at_peak_deg=round(rear_dynamic, 2),
            front_camber_delta_from_baseline=round(front_camber - geo.front_camber_baseline_deg, 1),
            rear_camber_delta_from_baseline=round(rear_camber - geo.rear_camber_baseline_deg, 1),
            front_toe_delta_from_baseline=round(front_toe - geo.front_toe_baseline_mm, 1),
            rear_toe_delta_from_baseline=round(rear_toe - geo.rear_toe_baseline_mm, 1),
            expected_conditioning_laps_front=round(laps_front, 1),
            expected_conditioning_laps_rear=round(laps_rear, 1),
            k_roll_total_nm_deg=round(k_roll_total_nm_deg, 0),
            camber_confidence=camber_confidence,
            constraints=constraints,
            notes=notes,
        )
