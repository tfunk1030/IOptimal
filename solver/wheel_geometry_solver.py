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
    parameter_search_status: dict[str, str] = field(default_factory=dict)
    parameter_search_evidence: dict[str, list[str]] = field(default_factory=dict)

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
        # t_avg_m computed here for potential future split-axle roll model
        # but the current single-DOF formula uses only h_cg and k_roll_total.

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
    ) -> float:
        """Compute optimal static camber from roll kinematics.

        We want dynamic camber ≈ 0° at the REPRESENTATIVE cornering load,
        which is the track's measured p95 lateral G. This is the load level
        the driver is at or below for 95% of cornering time — mid-corner,
        transitions, trail-braking.

        At peak g (>p95), dynamic camber goes slightly positive — acceptable
        because the tyre inner shoulder is still loaded and the wider contact
        patch at maximum load is beneficial.

        Args:
            representative_roll_deg: Body roll at representative (p95) lateral G
            roll_gain: Camber change per degree of roll (deg/deg)
            baseline_deg: Calibrated baseline camber for comparison
            is_front: True for front axle, False for rear axle
        """
        geo = self.car.geometry

        # At representative cornering (p95 lat_g):
        #   camber_change = representative_roll * roll_gain
        #   Want dynamic camber ≈ -0.5° at this load (not 0°).
        #   Michelin GTP compound is optimised for slight negative dynamic camber
        #   at representative load. 0° dynamic camber leaves grip on the table.
        #   optimal = -(representative_roll * roll_gain) - 0.5
        TARGET_DYNAMIC_DEG = -0.5  # target dynamic camber at representative load
        optimal = TARGET_DYNAMIC_DEG - (representative_roll_deg * roll_gain)

        # Clamp to valid range and snap to garage step (axle-specific)
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
        understeer_low: float = 0.0,
        understeer_high: float = 0.0,
        body_slip_p95: float = 0.0,
    ) -> float:
        """Recommend toe based on thermal conditioning (front) and balance (rear+front).

        Front toe:
          - Thermal baseline: -0.4mm (toe-out) for BMW/Sebring
          - More toe-out (-0.6mm) if conditioning slow (<1.5°C/lap)
          - Less toe-out (-0.2mm) if overheating (>3.5°C/lap)
          - Additional -0.2mm if high understeer at low speed (turn-in deficit)
          - +0.2mm if snap/oversteer tendencies (straight-line stability)

        Rear toe:
          - Stability baseline: 0mm (neutral)
          - +0.5mm toe-in if oversteer at high speed (body_slip_p95 > 3.0°)
          - +0.3mm toe-in if rear instability / lift-off oversteer signal
          - Never go negative (rear toe-out = destabilizing)
        """
        geo = self.car.geometry

        if not is_front:
            # Rear toe: stability-driven
            candidate = baseline_mm  # 0mm neutral
            if body_slip_p95 > 3.5:
                candidate += 0.5   # significant rear instability → toe-in
            elif body_slip_p95 > 2.0:
                candidate += 0.3   # moderate rear instability
            # Understeer at high speed = front grip deficit, not rear toe issue
            # Oversteer at high speed (US < 0) = add rear toe-in
            if understeer_high < -0.2:
                candidate += 0.3   # high-speed oversteer → rear toe-in
            r_min, r_max = geo.rear_toe_range_mm
            # Floor at baseline — baseline was set per car from calibrated/user data.
            # Previous hard clamp at 0.0 prevented Porsche (-1.6mm baseline) from outputting
            # any toe-out. The baseline represents the driver's validated operating point.
            candidate = max(baseline_mm, candidate)
            return max(r_min, min(r_max,
                round(candidate / geo.rear_toe_step_mm) * geo.rear_toe_step_mm))

        # Front toe: thermal + balance
        if conditioning_rate_deg_per_lap < 1.5:
            candidate = baseline_mm - 0.2   # slow heating → more toe-out
        elif conditioning_rate_deg_per_lap > 3.5:
            candidate = baseline_mm + 0.2   # overheating → less toe-out
        else:
            candidate = baseline_mm

        # Balance correction on top of thermal
        if understeer_low > 0.3:
            candidate -= 0.2   # understeer at low speed → more toe-out (sharpens turn-in)
        elif understeer_low < -0.2 or body_slip_p95 > 3.5:
            candidate += 0.2   # entry oversteer / instability → less toe-out

        t_min, t_max = geo.front_toe_range_mm
        return max(t_min, min(t_max,
            round(candidate / geo.front_toe_step_mm) * geo.front_toe_step_mm))

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
        measured: object | None = None,
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

        # Check if camber is derived from geometry (not independently settable)
        camber_is_derived = getattr(geo, 'camber_is_derived', False)

        if camber_is_derived:
            # Camber is derived from suspension geometry — use baseline values
            front_camber = geo.front_camber_baseline_deg
            rear_camber = geo.rear_camber_baseline_deg
        else:
            # Optimal static camber — optimized for p95 cornering load
            front_camber = self._optimal_camber(
                representative_roll_deg, geo.front_roll_gain, geo.front_camber_baseline_deg
            )
            rear_camber = self._optimal_camber(
                representative_roll_deg, geo.rear_roll_gain, geo.rear_camber_baseline_deg,
                is_front=False,
            )
            # Clamp both axles to iRacing legal garage limits
            f_min, f_max = geo.front_camber_range_deg
            front_camber = max(f_min, min(f_max, front_camber))
            r_min, r_max = geo.rear_camber_range_deg
            rear_camber = max(r_min, min(r_max, rear_camber))

        # Dynamic camber at peak lateral g (what the tyre actually sees)
        front_camber_change = roll_deg * geo.front_roll_gain
        rear_camber_change = roll_deg * geo.rear_roll_gain
        front_dynamic = front_camber + front_camber_change
        rear_dynamic = rear_camber + rear_camber_change

        # Tread conditioning rates per car (°C/lap from per-car-quirks.md and telemetry)
        car_name = self.car.canonical_name
        if car_name == "bmw":
            front_conditioning_rate = 2.4   # BMW Sebring: fronts 13-15 laps to op temp
            rear_conditioning_rate = 3.2    # BMW Sebring: rears 8-9 laps
        elif car_name == "porsche":
            front_conditioning_rate = 1.8   # DSSV better compliance → slower front heating
            rear_conditioning_rate = 2.5    # DSSV → slightly slower rear heating
        elif car_name == "ferrari":
            front_conditioning_rate = 2.2   # LMH compound, heavier car
            rear_conditioning_rate = 3.0
        elif car_name == "acura":
            front_conditioning_rate = 2.4   # ORECA similar to BMW
            rear_conditioning_rate = 3.0
        else:
            front_conditioning_rate = 2.4   # generic fallback
            rear_conditioning_rate = 3.0

        # Toe recommendation — driven by thermal conditioning + balance signals
        # Signals come from the TelemetryMeasurements object, not the track profile
        us_low = getattr(measured, "understeer_low_speed_deg", None) or 0.0
        us_high = getattr(measured, "understeer_high_speed_deg", None) or 0.0
        slip_p95 = getattr(measured, "body_slip_p95_deg", None) or 0.0

        front_toe = self._toe_recommendation(
            front_conditioning_rate, 25.0, geo.front_toe_baseline_mm, is_front=True,
            understeer_low=us_low, understeer_high=us_high, body_slip_p95=slip_p95,
        )
        rear_toe = self._toe_recommendation(
            rear_conditioning_rate, 20.0, geo.rear_toe_baseline_mm, is_front=False,
            understeer_low=us_low, understeer_high=us_high, body_slip_p95=slip_p95,
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

        notes = []

        if camber_is_derived:
            notes.append(
                f"Camber is derived from suspension geometry — not independently settable on {self.car.name}. "
                f"Using baseline values: front {front_camber:+.1f}°, rear {rear_camber:+.1f}°."
            )
        else:
            notes.append(
                f"Representative cornering: {representative_lat_g:.2f}g "
                f"(p95={p95_lat_g:.2f}g, peak={peak_lat_g:.2f}g, "
                f"kerb_weight={kerb_weight:.1f}). "
                f"Roll: {representative_roll_deg:.1f}° at representative, "
                f"{roll_deg:.1f}° at peak."
            )
            notes.append(
                "Tyre temperature spread diagnosis: inner hotter = correct camber. "
                "If outer runs hotter -> reduce negative camber by 0.2-0.3 deg."
            )

        # Car-specific thermal conditioning notes (car_name set above in conditioning rates)
        if car_name == "bmw":
            notes.extend([
                "BMW Vision tread conditioning (Sebring): fronts +2.4°C/lap, "
                "rears +3.2°C/lap. Full operating temp by lap 13-15 (fronts), 8-9 (rears).",
                "For sprint qualifying: add 0.3° more negative camber + 0.2mm extra "
                "toe-out to accelerate thermal buildup.",
            ])
        elif car_name == "ferrari":
            notes.extend([
                "Ferrari 499P (Michelin LMH compound): target tyre temp 85-105°C. "
                "Conditioning typically 10-14 laps from cold start.",
                "For sprint qualifying: add 0.2° more negative camber + 0.2mm extra "
                "toe-out to accelerate thermal buildup.",
                "Camber range: front -2.9° to 0.0°, rear -1.9° to 0.0°. "
                "User-settable per corner — verify values in garage before session.",
            ])
        elif car_name == "acura":
            notes.extend([
                "Acura ARX-06 (ORECA chassis): tyre conditioning similar to BMW (~10-14 laps). "
                "Front camber strongly affects front ride height (~2.9mm RH per degree).",
                "For sprint qualifying: add 0.2° more negative camber + 0.2mm extra "
                "toe-out to accelerate thermal buildup.",
            ])
        elif car_name == "cadillac":
            notes.extend([
                "Cadillac V-Series.R (Dallara LMDh): tyre conditioning similar to BMW. "
                "Target tyre temp 85-105°C.",
                "For sprint qualifying: add 0.3° more negative camber + 0.2mm extra "
                "toe-out to accelerate thermal buildup.",
            ])
        else:
            notes.extend([
                f"{self.car.name}: target tyre temp 85-105°C (Michelin GTP compound). "
                f"Conditioning typically 10-15 laps from cold start.",
                "For sprint qualifying: add 0.2-0.3° more negative camber + 0.2mm extra "
                "toe-out to accelerate thermal buildup.",
            ])

        # Classify parameters
        if camber_is_derived:
            camber_status = "geometry_derived"
        else:
            camber_status = "solver_computed"
        pss = {
            "front_camber_deg": camber_status,
            "rear_camber_deg": camber_status,
            "front_toe_mm": "solver_computed",
            "rear_toe_mm": "solver_computed",
        }

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
            parameter_search_status=pss,
        )

    def solution_from_explicit_settings(
        self,
        *,
        k_roll_total_nm_deg: float,
        front_wheel_rate_nmm: float,
        rear_wheel_rate_nmm: float,
        front_camber_deg: float,
        rear_camber_deg: float,
        front_toe_mm: float,
        rear_toe_mm: float,
        fuel_load_l: float = 89.0,
        camber_confidence: str = "estimated",
    ) -> WheelGeometrySolution:
        """Build a Step 5 solution from explicit geometry settings."""
        geo = self.car.geometry
        peak_lat_g = self.track.peak_lat_g
        if self.track.lateral_g and self.track.lateral_g.get("p95"):
            p95_lat_g = self.track.lateral_g["p95"]
        else:
            p95_lat_g = peak_lat_g * 0.47
        n_kerbs = len(self.track.kerb_events)
        if n_kerbs > 10:
            kerb_weight = 0.7
        elif n_kerbs > 5:
            kerb_weight = 0.5
        else:
            kerb_weight = 0.3
        representative_lat_g = p95_lat_g + kerb_weight * (peak_lat_g - p95_lat_g)
        roll_deg = self._body_roll_at_g(peak_lat_g, k_roll_total_nm_deg, fuel_load_l)
        representative_roll_deg = self._body_roll_at_g(representative_lat_g, k_roll_total_nm_deg, fuel_load_l)
        # Clamp to iRacing legal garage limits
        f_min, f_max = geo.front_camber_range_deg
        r_min, r_max = geo.rear_camber_range_deg
        front_camber = max(f_min, min(f_max, float(front_camber_deg)))
        rear_camber = max(r_min, min(r_max, float(rear_camber_deg)))
        front_toe = float(front_toe_mm)
        rear_toe = float(rear_toe_mm)
        front_camber_change = roll_deg * geo.front_roll_gain
        rear_camber_change = roll_deg * geo.rear_roll_gain
        front_dynamic = front_camber + front_camber_change
        rear_dynamic = rear_camber + rear_camber_change
        # Per-car tyre conditioning rates (matches solve() logic)
        car_name = self.car.canonical_name
        if car_name == "bmw":
            front_conditioning_rate = 2.4
            rear_conditioning_rate = 3.2
        elif car_name == "porsche":
            front_conditioning_rate = 1.8
            rear_conditioning_rate = 2.5
        elif car_name == "ferrari":
            front_conditioning_rate = 2.2
            rear_conditioning_rate = 3.0
        elif car_name == "acura":
            front_conditioning_rate = 2.4
            rear_conditioning_rate = 3.0
        else:
            front_conditioning_rate = 2.4
            rear_conditioning_rate = 3.0
        laps_front = self._laps_to_operating_temp(front_conditioning_rate, front_toe, is_front=True)
        laps_rear = self._laps_to_operating_temp(rear_conditioning_rate, rear_toe, is_front=False)
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
                note="Excessive toe-out increases tyre scrub and lap time" if front_toe < -1.0 else "",
            ),
            GeometryConstraintCheck(
                name="Rear toe non-destabilizing",
                passed=rear_toe >= 0.0,
                value=rear_toe,
                target=0.0,
                units="mm",
                note="Rear toe-out is destabilizing — keep at or above 0mm",
            ),
        ]
        notes = [
            f"Explicit geometry materialization at representative roll {representative_roll_deg:.1f}°.",
            f"Front/rear wheel-rate context preserved: {front_wheel_rate_nmm:.1f} / {rear_wheel_rate_nmm:.1f} N/mm.",
        ]
        pss = {
            "front_camber_deg": "user_set",
            "rear_camber_deg": "user_set",
            "front_toe_mm": "user_set",
            "rear_toe_mm": "user_set",
        }
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
            constraints=constraints,
            camber_confidence=camber_confidence,
            notes=notes,
            parameter_search_status=pss,
        )
