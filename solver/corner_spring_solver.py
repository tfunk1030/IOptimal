"""Step 3: Corner Spring Solver.

Finds front torsion bar OD and rear coil spring rate that provide adequate
mechanical grip for the track surface while maintaining the aero platform
validated in Steps 1-2.

Physics:
    Corner springs (torsion bars front, coil springs rear) contribute to BOTH
    heave stiffness AND roll stiffness. Heave springs only affect heave
    (geometric decoupling from roll). ARBs only affect roll.

    This creates a clear separation of concerns:
    - Heave springs: set for aero platform (Step 2)
    - Corner springs: set for track surface compliance + heave contribution
    - ARBs: set for mechanical balance (Step 4)

    The corner spring natural frequency determines ride quality over bumps:
        f_corner = (1/2pi) * sqrt(k_wheel / m_corner)

    For good bump isolation, corner frequency should be well below the
    track's dominant bump frequency:
        f_corner < f_bump / isolation_ratio

    The solver finds the corner spring rate that:
    1. Provides adequate frequency isolation for the track surface
    2. Maintains heave-to-corner ratio within the 1.5-3.5x guideline
    3. Keeps total heave stiffness adequate for the aero platform
    4. For rear: addresses traction needs under longitudinal load transfer

    Front output: torsion bar OD in mm (iRacing garage parameter)
    Rear output: coil spring rate in N/mm (direct garage parameter)

    The front torsion bar stiffness scales as OD^4:
        k_wheel = C_torsion * OD^4
    where C_torsion is calibrated from the verified setup.

    The rear coil spring rate is driven by the third-to-corner ratio.
    For bumpy tracks (high shock velocity), a higher ratio (softer corner)
    gives better mechanical grip. For smooth tracks, a lower ratio (stiffer)
    gives better platform control.

Validated against BMW Sebring:
    - Front torsion bar OD: 13.90mm (wheel rate ~30 N/mm, freq 1.66 Hz)
    - Rear coil spring: 170 N/mm (raised from 160 for throttle oversteer)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

from car_model.cars import CarModel
from track_model.profile import TrackProfile


def _solve_torsion_bar_turns(
    car: CarModel,
    current_setup,
    *,
    front_torsion_od_mm: float,
    rear_spring_rate_nmm: float,
    front_heave_nmm: float,
    rear_third_nmm: float,
    front_heave_perch_mm: float = -16.5,
    rear_third_perch_mm: float = -104.0,
) -> tuple[float, float]:
    """Dispatcher for front/rear torsion bar preload turns by car.

    Ferrari has a calibrated regression (see _solve_ferrari_torsion_bar_turns).
    BMW/Cadillac/Acura have no calibrated solver — preserve the driver's
    loaded value from the IBT session_info instead of recomputing from a
    formula that drifts from driver-loaded ground truth. Porsche has no
    front torsion bar (uses a roll spring), so turns are 0.0.

    Args:
        car: CarModel.
        current_setup: CurrentSetup-like object from analyzer.setup_reader,
            or None when running in pure-physics mode.

    Returns:
        (front_turns, rear_turns). For BMW/Cadillac/Acura when current_setup
        is None, returns (0.0, 0.0) — there's no driver value to anchor to,
        and we don't fabricate a value (Key Principle 7: calibrated or instruct,
        never guess).
    """
    canonical = (car.canonical_name or "").lower()

    if canonical == "ferrari":
        return _solve_ferrari_torsion_bar_turns(
            car,
            front_torsion_od_mm=front_torsion_od_mm,
            rear_spring_rate_nmm=rear_spring_rate_nmm,
            front_heave_nmm=front_heave_nmm,
            rear_third_nmm=rear_third_nmm,
            front_heave_perch_mm=front_heave_perch_mm,
            rear_third_perch_mm=rear_third_perch_mm,
        )

    if canonical == "porsche":
        # No front torsion bar; no rear torsion bar. Roll spring car.
        return 0.0, 0.0

    if canonical in ("bmw", "cadillac", "acura"):
        # Preserve driver-loaded value when present. CurrentSetup field name
        # is `torsion_bar_turns` (front) and `rear_torsion_bar_turns` (rear).
        if current_setup is None:
            return 0.0, 0.0
        front = float(getattr(current_setup, "torsion_bar_turns", 0.0) or 0.0)
        rear = float(getattr(current_setup, "rear_torsion_bar_turns", 0.0) or 0.0)
        return front, rear

    # Unknown car: don't fabricate.
    return 0.0, 0.0


def _solve_ferrari_torsion_bar_turns(
    car: CarModel,
    *,
    front_torsion_od_mm: float,
    rear_spring_rate_nmm: float,
    front_heave_nmm: float,
    rear_third_nmm: float,
    front_heave_perch_mm: float = -16.5,
    rear_third_perch_mm: float = -104.0,
) -> tuple[float, float]:
    """Compute Ferrari front/rear torsion bar preload turns.

    Calibrated from 59 indexed Ferrari sessions.  Turns represent static
    torsion-bar twist under the car's weight — a function of corner weight
    (driven by heave spring preload/perch) and bar stiffness (OD^4).

    Front model (R²=0.51, RMSE=0.003):
        turns = 0.1364 + 0.3292/heave + 0.000484*perch − 8.4804/torsion_rate

    Rear model (R²=0.55, RMSE=0.004):
        turns = 0.1239 + 4.5102/third + 0.000964*perch + 7.1109/torsion_rate

    Returns:
        (front_turns, rear_turns) — calibrated preload turns.
    """
    csm = car.corner_spring

    # Front torsion bar rate from physical OD
    front_torsion_rate = (
        csm.torsion_bar_rate(front_torsion_od_mm)
        if csm.front_torsion_c > 0 and front_torsion_od_mm > 1.0
        else 250.0
    )
    front_turns = (
        0.1364
        + 0.3292 / max(front_heave_nmm, 1.0)
        + 0.000484 * front_heave_perch_mm
        - 8.4804 / max(front_torsion_rate, 1.0)
    )

    # Rear torsion bar rate (already a physical rate in the solver)
    rear_torsion_rate = max(rear_spring_rate_nmm, 1.0)
    rear_turns = (
        0.1239
        + 4.5102 / max(rear_third_nmm, 1.0)
        + 0.000964 * rear_third_perch_mm
        + 7.1109 / rear_torsion_rate
    )

    return round(front_turns, 3), round(rear_turns, 3)


@dataclass
class CornerSpringSolution:
    """Output of the Step 3 corner spring solver."""

    # Front torsion bar (or roll spring for Porsche)
    front_torsion_od_mm: float  # 0.0 for Porsche (uses roll spring instead)
    front_wheel_rate_nmm: float
    front_natural_freq_hz: float
    front_heave_corner_ratio: float   # heave_spring / corner_wheel_rate
    front_mass_per_corner_kg: float

    # Rear corner spring — RAW spring rate (N/mm), NOT wheel rate.
    # Use rear_wheel_rate_nmm property for the MR^2-corrected wheel rate.
    rear_spring_rate_nmm: float
    rear_natural_freq_hz: float
    rear_third_corner_ratio: float    # third_spring / corner_rate
    rear_mass_per_corner_kg: float

    # Total heave stiffness (heave/third + 2 * corner)
    total_front_heave_nmm: float
    total_rear_heave_nmm: float

    # Heave-mode natural frequencies (heave + 2*corner, full axle mass)
    # THIS is what the FFT measures on straights — both wheels moving together
    front_heave_mode_freq_hz: float
    rear_heave_mode_freq_hz: float

    # Track surface matching
    track_bump_freq_hz: float
    front_freq_isolation_ratio: float  # bump_freq / corner_freq
    rear_freq_isolation_ratio: float

    # Perch offset
    rear_spring_perch_mm: float

    # Constraint checks
    constraints: list[CornerSpringCheck]

    # ORECA: rear torsion bar OD (None = coil spring car)
    rear_torsion_od_mm: float | None = None
    # Porsche: optimized front roll spring rate (0.0 for torsion bar cars)
    front_roll_spring_nmm: float = 0.0
    # Rear motion ratio — stored at solve time so consumers don't need the
    # car model to compute wheel rate. See rear_wheel_rate_nmm property.
    rear_motion_ratio: float = 1.0

    # GT3 paired front coil rate (N/mm).  Zero for GTP/torsion-bar and Porsche-
    # roll-spring cars.  When >0 the GT3 architecture branch fired and this is
    # the authoritative front-axle rate (front_torsion_od_mm will be 0.0).
    front_coil_rate_nmm: float = 0.0
    # GT3 paired front coil perch offset (mm).  Mirrors rear_spring_perch_mm.
    front_coil_perch_mm: float = 0.0

    # Torsion bar preload turns (Ferrari: -0.250 to +0.250 at all 4 corners).
    # For Ferrari these are authoritative solver outputs computed by
    # _solve_ferrari_torsion_bar_turns(); for all other cars they remain 0.0.
    front_torsion_bar_turns: float = 0.0
    rear_torsion_bar_turns: float = 0.0
    parameter_search_status: dict = None
    parameter_search_evidence: dict = None

    def __post_init__(self):
        if self.parameter_search_status is None:
            self.parameter_search_status = {
                "front_torsion_od_mm": "user_set",
                "rear_torsion_od_mm": "user_set",
                "rear_spring_rate_nmm": "user_set",
                "rear_spring_perch_mm": "user_set",
                "front_torsion_bar_turns": "user_set",
                "rear_torsion_bar_turns": "user_set",
            }
        if self.parameter_search_evidence is None:
            self.parameter_search_evidence = {}

    @property
    def rear_wheel_rate_nmm(self) -> float:
        """Rear wheel rate (N/mm) = raw spring rate * motion_ratio^2."""
        return self.rear_spring_rate_nmm * self.rear_motion_ratio ** 2

    def summary(self) -> str:
        """Human-readable summary of the solution."""
        # Detect architecture from the solution:
        #   - GT3 paired front coil: front_coil_rate_nmm > 0
        #   - Porsche-GTP roll spring: OD=0 AND front_roll_spring_nmm > 0
        #   - GTP torsion bar: OD > 0
        _is_gt3_coil = self.front_coil_rate_nmm > 0
        _is_roll_spring = (
            not _is_gt3_coil
            and self.front_torsion_od_mm == 0.0
            and self.front_roll_spring_nmm > 0
        )
        # GT3: no heave spring exists, so heave/corner ratio is meaningless.
        _has_heave = self.total_front_heave_nmm > 2 * self.front_wheel_rate_nmm + 1e-3
        lines = [
            "===========================================================",
            "  STEP 3: CORNER SPRING SOLUTION",
            "===========================================================",
            "",
        ]
        if _is_gt3_coil:
            lines += [
                "  FRONT COIL SPRING (paired)",
                f"    Coil spring rate:    {self.front_coil_rate_nmm:6.0f} N/mm",
                f"    Wheel rate:          {self.front_wheel_rate_nmm:6.1f} N/mm",
                f"    Natural frequency:   {self.front_natural_freq_hz:6.2f} Hz",
                f"    Freq isolation:      {self.front_freq_isolation_ratio:6.1f}x "
                f"(target: >2.5x)",
            ]
        elif _is_roll_spring:
            lines += [
                "  FRONT ROLL SPRING",
                f"    Roll spring rate:    {self.front_roll_spring_nmm:6.0f} N/mm",
                f"    Wheel rate:          {self.front_wheel_rate_nmm:6.1f} N/mm",
                f"    Natural frequency:   {self.front_natural_freq_hz:6.2f} Hz",
                f"    Heave/corner ratio:  {self.front_heave_corner_ratio:6.1f}x "
                f"(guideline: 1.5-3.5x)",
                f"    Freq isolation:      {self.front_freq_isolation_ratio:6.1f}x "
                f"(target: >2.5x)",
            ]
        else:
            lines += [
                "  FRONT TORSION BAR",
                f"    Torsion bar OD:      {self.front_torsion_od_mm:6.2f} mm",
                f"    Wheel rate:          {self.front_wheel_rate_nmm:6.1f} N/mm",
                f"    Natural frequency:   {self.front_natural_freq_hz:6.2f} Hz",
                f"    Heave/corner ratio:  {self.front_heave_corner_ratio:6.1f}x "
                f"(guideline: 1.5-3.5x)",
                f"    Freq isolation:      {self.front_freq_isolation_ratio:6.1f}x "
                f"(target: >2.5x)",
            ]
        rear_block = [
            "",
            f"  REAR {'TORSION BAR' if self.rear_torsion_od_mm else 'COIL SPRING'}",
            *(
                [f"    Torsion bar OD:      {self.rear_torsion_od_mm:6.2f} mm"]
                if self.rear_torsion_od_mm else []
            ),
            f"    Spring rate:         {self.rear_spring_rate_nmm:6.0f} N/mm",
            f"    Wheel rate:          {self.rear_wheel_rate_nmm:6.1f} N/mm"
            f"  (MR={self.rear_motion_ratio:.3f})",
            f"    Natural frequency:   {self.rear_natural_freq_hz:6.2f} Hz",
        ]
        if _has_heave:
            rear_block += [
                f"    Third/corner ratio:  {self.rear_third_corner_ratio:6.1f}x "
                f"(guideline: 1.5-3.5x)",
            ]
        rear_block += [
            f"    Freq isolation:      {self.rear_freq_isolation_ratio:6.1f}x",
            f"    Perch offset:        {self.rear_spring_perch_mm:6.1f} mm (baseline)",
            "",
        ]
        if _has_heave:
            rear_block += [
                "  TOTAL HEAVE STIFFNESS (heave/third + 2 * corner wheel rate)",
                f"    Front:  {self.total_front_heave_nmm:6.0f} N/mm "
                f"(heave alone: {self.total_front_heave_nmm - 2*self.front_wheel_rate_nmm:.0f})",
                f"    Rear:   {self.total_rear_heave_nmm:6.0f} N/mm "
                f"(third alone: {self.total_rear_heave_nmm - 2*self.rear_wheel_rate_nmm:.0f})",
            ]
        else:
            # GT3 architecture: no heave spring; "total" is just 2 × corner.
            rear_block += [
                "  TOTAL AXLE WHEEL RATE (2 x corner — no heave spring)",
                f"    Front:  {self.total_front_heave_nmm:6.0f} N/mm",
                f"    Rear:   {self.total_rear_heave_nmm:6.0f} N/mm",
            ]
        lines += rear_block + [
            "",
            "  TRACK SURFACE MATCHING",
            f"    Track bump frequency:  {self.track_bump_freq_hz:.1f} Hz",
            f"    Front corner freq:     {self.front_natural_freq_hz:.2f} Hz "
            f"({self.front_freq_isolation_ratio:.1f}x isolation)",
            f"    Rear corner freq:      {self.rear_natural_freq_hz:.2f} Hz "
            f"({self.rear_freq_isolation_ratio:.1f}x isolation)",
        ]

        if self.constraints:
            lines += ["", "  CONSTRAINT CHECKS"]
            for c in self.constraints:
                status = "OK" if c.satisfied else "WARNING"
                lines.append(f"    {c.name}: {status}")
                if not c.satisfied:
                    lines.append(f"      {c.detail}")

        lines.append("===========================================================")
        return "\n".join(lines)


@dataclass
class CornerSpringCheck:
    """Result of checking a constraint."""
    name: str
    satisfied: bool
    detail: str


class CornerSpringSolver:
    """Step 3 solver: find corner spring rates for track surface compliance.

    Uses natural frequency targeting based on the track's dominant bump
    frequency, constrained by the heave-to-corner ratio guideline and
    the total heave stiffness requirement from Step 2.
    """

    def __init__(self, car: CarModel, track: TrackProfile):
        self.car = car
        self.track = track

    def natural_freq(self, k_wheel_nmm: float, m_corner_kg: float) -> float:
        """Corner natural frequency (Hz) for a given wheel rate and mass."""
        return (1 / (2 * math.pi)) * math.sqrt(k_wheel_nmm * 1000 / m_corner_kg)

    def rate_for_freq(self, freq_hz: float, m_corner_kg: float) -> float:
        """Wheel rate (N/mm) for a target natural frequency."""
        return (2 * math.pi * freq_hz) ** 2 * m_corner_kg / 1000

    def solve(
        self,
        front_heave_nmm: float,
        rear_third_nmm: float,
        fuel_load_l: float | None = None,
        current_rear_third_nmm: float | None = None,
        current_rear_spring_nmm: float | None = None,
        current_setup=None,
    ) -> CornerSpringSolution:
        """Find optimal corner spring rates.

        Args:
            front_heave_nmm: Front heave spring rate from Step 2
            rear_third_nmm: Rear third spring rate from Step 2
            fuel_load_l: Fuel load (affects corner mass). If None, uses
                car.fuel_capacity_l (all LMDh GTP = 88.96L).
            current_rear_third_nmm: Driver's currently-loaded rear third
                (anchor for the third/coil ratio calibration)
            current_rear_spring_nmm: Driver's currently-loaded rear coil
                (anchor for the third/coil ratio calibration)
            current_setup: Driver's loaded CurrentSetup (analyzer.setup_reader).
                Passed through to torsion-bar-turns dispatcher so non-Ferrari
                cars preserve the driver's loaded TorsionBarTurns value.

        Returns:
            CornerSpringSolution with torsion bar OD and rear rate
        """
        if fuel_load_l is None:
            fuel_load_l = getattr(self.car, 'fuel_capacity_l', 89.0)
        csm = self.car.corner_spring
        total_mass = self.car.total_mass(fuel_load_l)
        m_f_corner = total_mass * self.car.weight_dist_front / 2
        m_r_corner = total_mass * (1 - self.car.weight_dist_front) / 2

        bump_freq = self.car.rh_variance.dominant_bump_freq_hz

        # === FRONT: Natural frequency targeting ===
        # Target: corner freq = bump_freq / freq_ratio
        # Use a ratio of 3.0 for bumpy tracks (high shock vel), 2.5 for smooth
        # Scale based on track surface severity (p99 shock velocity)
        # Sebring p99_front = 0.2511 m/s is moderately bumpy -> ratio ~3.0
        # Use clean-track p99 for surface severity — curb spikes are not
        # representative of the sustained surface the corner springs must handle.
        front_sv_p99 = (self.track.shock_vel_p99_front_clean_mps
                        if self.track.shock_vel_p99_front_clean_mps > 0
                        else self.track.shock_vel_p99_front_mps)
        front_freq_ratio = self._surface_severity_to_freq_ratio(front_sv_p99)
        front_target_freq = bump_freq / front_freq_ratio
        front_target_rate = self.rate_for_freq(front_target_freq, m_f_corner)

        # Check heave-to-corner ratio constraint.
        # GTP path: clamp front_rate so heave/corner stays within the
        # 1.5-3.5x guideline. GT3 path: no heave spring exists (Step 2 was
        # skipped, front_heave_nmm == 0), so the ratio clamp is meaningless
        # and would collapse front_rate to zero. Instead, clamp directly to
        # the GT3 paired-coil garage range. (See audit C-1.)
        ratio_lo, ratio_hi = csm.heave_corner_ratio_range
        front_coil_rate_nmm = 0.0  # Populated only on GT3 paired-coil path
        if front_heave_nmm > 0:
            # GTP heave-spring path
            front_max_for_ratio = front_heave_nmm / ratio_lo  # Upper bound from ratio
            front_min_for_ratio = front_heave_nmm / ratio_hi  # Lower bound from ratio
            front_rate = max(front_target_rate, front_min_for_ratio)
            front_rate = min(front_rate, front_max_for_ratio)
        elif csm.front_spring_range_nmm[1] > 0:
            # GT3 paired-coil path — no heave spring; clamp to coil range.
            lo_coil, hi_coil = csm.front_spring_range_nmm
            front_rate = max(front_target_rate, lo_coil)
            front_rate = min(front_rate, hi_coil)
        else:
            # No heave spring AND no GT3 coil range — leave the physics
            # frequency target untouched (GTP roll-spring car will clamp
            # later in its own branch). This preserves the prior behaviour
            # for the Porsche-GTP roll-spring path.
            front_rate = front_target_rate

        # Convert to torsion bar OD (skip for cars with no front torsion bar, e.g. Porsche)
        if csm.front_torsion_c > 0 and csm.front_torsion_od_options:
            front_od = csm.torsion_bar_od_for_rate(front_rate)
            front_od = csm.snap_torsion_od(front_od)

            # Clamp to valid OD range
            front_od = max(front_od, csm.front_torsion_od_range_mm[0])
            front_od = min(front_od, csm.front_torsion_od_range_mm[1])

            # Recalculate actual rate from snapped OD
            front_rate = csm.torsion_bar_rate(front_od)
        elif csm.front_spring_range_nmm[1] > 0:
            # GT3 paired front coils: front spring rate set directly in N/mm
            # via the garage. Snap to the resolution grid; no torsion bar.
            # (See audit C-2.)
            front_rate = csm.snap_front_rate(front_rate)
            front_coil_rate_nmm = front_rate
            front_od = 0.0
        elif csm.front_roll_spring_range_nmm[1] > 0:
            # Porsche: front corner stiffness comes from adjustable roll spring (100-320 N/mm)
            # Snap computed rate to the roll spring garage range and step
            front_rate = csm.snap_front_roll_spring(front_rate)

            # ── LLTD-aware floor for roll-spring cars ──
            # Step 3 picks the softest legal roll spring for ride quality, but
            # Step 4 (ARBs) may not have enough authority to reach the LLTD
            # target if the front roll stiffness is too low.  Compute the
            # approximate achievable LLTD at maximum ARB stiffness and bump
            # the roll spring rate if the gap exceeds 5 pp.
            front_rate = self._apply_lltd_floor(
                front_rate, rear_third_nmm, front_heave_nmm, fuel_load_l,
            )

            # Update the car model so downstream solvers (ARB, geometry) use the optimized value
            csm.front_roll_spring_rate_nmm = front_rate
            front_od = 0.0
        else:
            # No front torsion bar and no roll spring range defined
            front_od = 0.0
        front_freq = self.natural_freq(front_rate, m_f_corner)

        # === REAR: Third-to-corner ratio targeting ===
        # For the rear, the binding constraint is the third/corner ratio.
        # Bumpy tracks need higher ratio (softer corner for grip).
        # The target ratio is scaled by surface severity.
        rear_sv_p99 = (self.track.shock_vel_p99_rear_clean_mps
                       if self.track.shock_vel_p99_rear_clean_mps > 0
                       else self.track.shock_vel_p99_rear_mps)
        rear_freq_ratio = self._surface_severity_to_freq_ratio(rear_sv_p99)

        # Rear target rate.
        #
        # When the driver's CURRENT rear coil is known (loaded from IBT
        # session info), prefer it DIRECTLY. The rear coil is part of a
        # COUPLED system with the rear ARB: together they determine LLTD.
        # Picking a different coil value forces the ARB solver to
        # compensate, which can saturate (the driver's selected ARB blade
        # was tuned for the driver's selected coil). Anchoring the coil to
        # the driver's value preserves the LLTD-balance the driver
        # validated. Validated 2026-04-07 against Porsche/Algarve where
        # driver runs coil=180/ARB Stiff blade 10 — synthetic ratio
        # heuristic gave coil=105 which forced rear ARB to collapse to
        # blade 1 (LLTD missed target by 4.3pp).
        _physics_rear_rate: float | None = None
        if rear_third_nmm > 0 and current_rear_spring_nmm is not None and current_rear_spring_nmm > 0:
            # GTP path with driver anchor: compute what physics alone would give.
            _physics_ratio = self._surface_severity_to_heave_ratio(rear_sv_p99)
            _physics_rear_rate = rear_third_nmm / _physics_ratio
            # Guard the relative-gap divide against a degenerate _physics_rear_rate.
            denom = max(_physics_rear_rate, 1.0)
            # Soft preference: use driver's value only if within 40% of physics
            # FIXED 2026-04-28: was 20% which was too tight — when the third
            # spring changes (e.g. 680→370), the physics ratio shifts and the
            # driver's coil (validated at the old third) falls outside 20%.
            # 40% accommodates the natural third/coil coupling.
            if abs(float(current_rear_spring_nmm) - _physics_rear_rate) / denom <= 0.40:
                rear_target_rate = float(current_rear_spring_nmm)
                logger.debug(
                    "Rear spring anchored to driver-loaded %.0f N/mm "
                    "(physics target: %.0f N/mm, within 40%%)",
                    rear_target_rate, _physics_rear_rate,
                )
            else:
                rear_target_rate = _physics_rear_rate
                logger.debug(
                    "Rear spring using physics target %.0f N/mm "
                    "(driver-loaded: %.0f N/mm, differs by >40%%)",
                    rear_target_rate, float(current_rear_spring_nmm),
                )
            # Use the driver's empirical ratio for the FREQUENCY check that
            # follows (kept symmetric with the synthetic path).
            if current_rear_third_nmm and current_rear_third_nmm > 0:
                rear_target_ratio = float(current_rear_third_nmm) / float(current_rear_spring_nmm)
            else:
                rear_target_ratio = self._surface_severity_to_heave_ratio(rear_sv_p99)
        elif rear_third_nmm > 0:
            # GTP path without driver anchor: third/corner ratio.
            rear_target_ratio = self._surface_severity_to_heave_ratio(rear_sv_p99)
            rear_target_rate = rear_third_nmm / rear_target_ratio
        else:
            # GT3 path: no third spring → no third/corner ratio. Pick the
            # rear corner rate by frequency-isolation directly. (See audit
            # C-3.) The driver-anchor `/0` at C-4 is unreachable here
            # because the GTP branches above already require rear_third_nmm > 0.
            rear_target_freq = bump_freq / rear_freq_ratio
            rear_target_rate = self.rate_for_freq(rear_target_freq, m_r_corner)
            # Optional driver anchor: GT3 has no third/corner ratio, but if
            # the driver loaded a coil within the legal range we still
            # respect it as a soft anchor (mirrors the GTP path policy).
            if current_rear_spring_nmm is not None and current_rear_spring_nmm > 0:
                _phys = max(rear_target_rate, 1.0)
                if abs(float(current_rear_spring_nmm) - rear_target_rate) / _phys <= 0.20:
                    _physics_rear_rate = rear_target_rate
                    rear_target_rate = float(current_rear_spring_nmm)
                    logger.debug(
                        "Rear spring anchored to driver-loaded %.0f N/mm "
                        "(GT3 frequency target: %.0f N/mm, within 20%%)",
                        rear_target_rate, _physics_rear_rate,
                    )
            # Synthetic ratio for the frequency-check downstream — without a
            # third spring this is effectively zero, but we keep the shape.
            rear_target_ratio = 0.0

        # ── Zero-coefficient pushrod guard ──────────────────────────────────
        # When the car's rear_pushrod_to_rh is ~0 (e.g. Porsche 963), pushrod
        # adjustments have NO effect on rear ride height.  RH is controlled
        # by rear_spring + third via the RideHeightModel.  If we change the
        # rear coil spring rate too aggressively, the resulting RH shift is
        # uncompensated because the rake solver / reconcile cannot offset it
        # via pushrod.  Constrain the rate change to ±30% of the car's
        # baseline rear coil rate so RH stays within a recoverable window.
        _rear_pushrod_coeff = getattr(self.car.pushrod, "rear_pushrod_to_rh", -1.0)
        if abs(_rear_pushrod_coeff) < 1e-6:
            _baseline_rear_spring = self.car.corner_spring.rear_spring_rate_baseline_nmm \
                if hasattr(self.car.corner_spring, "rear_spring_rate_baseline_nmm") \
                else (current_rear_spring_nmm if current_rear_spring_nmm and current_rear_spring_nmm > 0
                      else self.car.rear_third_spring_nmm)
            if _baseline_rear_spring and _baseline_rear_spring > 0:
                _lo_guard = _baseline_rear_spring * 0.70
                _hi_guard = _baseline_rear_spring * 1.30
                _unconstrained = rear_target_rate
                rear_target_rate = max(rear_target_rate, _lo_guard)
                rear_target_rate = min(rear_target_rate, _hi_guard)
                if abs(rear_target_rate - _unconstrained) > 0.5:
                    logger.warning(
                        "Zero rear_pushrod_to_rh: constraining rear spring "
                        "%.0f→%.0f N/mm (baseline %.0f, ±30%% guard) to "
                        "prevent uncompensated RH shift.",
                        _unconstrained, rear_target_rate, _baseline_rear_spring,
                    )

        # Clamp to valid range and snap
        if csm.rear_is_torsion_bar:
            # Rear torsion bar: convert target rate to OD, snap, reconvert
            rear_od = csm.rear_torsion_bar_od_for_rate(rear_target_rate)
            rear_od = csm.snap_rear_torsion_od(rear_od)
            rear_od = max(rear_od, csm.rear_torsion_od_range_mm[0])
            rear_od = min(rear_od, csm.rear_torsion_od_range_mm[1])
            rear_rate = csm.rear_torsion_bar_rate(rear_od)
            # Validation warning for unvalidated rear torsion bar models
            if getattr(csm, 'rear_torsion_unvalidated', False):
                print("\n⚠  UNVALIDATED: Ferrari rear torsion bar model may have 3.5x rate error — verify rear spring rates manually\n")
        else:
            rear_od = None
            rear_rate = max(rear_target_rate, csm.rear_spring_range_nmm[0])
            rear_rate = min(rear_rate, csm.rear_spring_range_nmm[1])
            rear_rate = csm.snap_rear_rate(rear_rate)

        rear_freq = self.natural_freq(rear_rate, m_r_corner)

        # Choose which front-rate kwarg to forward into the explicit builder.
        # GT3 paired-coil cars set front_coil_rate_nmm > 0 above; Porsche-GTP
        # roll-spring cars satisfy front_is_roll_spring; torsion-bar cars use
        # the snapped front_od. Pass front_roll_spring_nmm only on the GTP
        # roll-spring path so GT3's coil rate isn't accidentally re-snapped to
        # the (zero) roll-spring range.
        is_gt3_coil = csm.front_spring_range_nmm[1] > 0
        sol = self.solution_from_explicit_rates(
            front_heave_nmm=front_heave_nmm,
            rear_third_nmm=rear_third_nmm,
            front_torsion_od_mm=front_od,
            rear_spring_rate_nmm=rear_rate,
            fuel_load_l=fuel_load_l,
            rear_spring_perch_mm=csm.rear_spring_perch_baseline_mm,
            rear_torsion_od_mm=rear_od,
            front_roll_spring_nmm=(
                front_rate
                if (csm.front_is_roll_spring and not is_gt3_coil)
                else None
            ),
            front_coil_rate_nmm=front_rate if is_gt3_coil else None,
            current_setup=current_setup,
        )
        # Annotate solution with anchor provenance so trace consumers see final values
        if _physics_rear_rate is not None:
            sol.parameter_search_status["rear_spring_rate_nmm"] = "anchored_to_driver"
            if sol.parameter_search_evidence is None:
                sol.parameter_search_evidence = {}
            sol.parameter_search_evidence["rear_spring_rate_nmm"] = {
                "driver_value": current_rear_spring_nmm,
                "physics_value": round(_physics_rear_rate, 1),
            }
        return sol

    def solution_from_explicit_rates(
        self,
        front_heave_nmm: float,
        rear_third_nmm: float,
        front_torsion_od_mm: float,
        rear_spring_rate_nmm: float,
        fuel_load_l: float | None = None,
        rear_spring_perch_mm: float | None = None,
        rear_torsion_od_mm: float | None = None,
        front_heave_perch_mm: float | None = None,
        rear_third_perch_mm: float | None = None,
        front_roll_spring_nmm: float | None = None,
        front_coil_rate_nmm: float | None = None,
        front_coil_perch_mm: float | None = None,
        current_setup=None,
    ) -> CornerSpringSolution:
        """Build a corner-spring solution from explicit garage selections.

        Args:
            front_roll_spring_nmm: For roll-spring cars (Porsche GTP), the
                explicit front roll spring rate (N/mm). When provided,
                overrides the torsion bar path entirely. Ignored for torsion
                bar and GT3 paired-coil cars.
            front_coil_rate_nmm: For GT3 paired front coil cars, the explicit
                front spring rate (N/mm). When provided, overrides any other
                front-rate path. Ignored for GTP architecture.
            front_coil_perch_mm: GT3 front spring perch offset (mm). Mirrors
                ``rear_spring_perch_mm``. Defaults to 0.0 when not provided.
            current_setup: Driver's loaded CurrentSetup; used by the
                torsion-bar-turns dispatcher (Unit 3) for non-Ferrari cars
                to preserve the driver's loaded TorsionBarTurns value.
        """
        if fuel_load_l is None:
            fuel_load_l = getattr(self.car, 'fuel_capacity_l', 89.0)
        csm = self.car.corner_spring
        # Architecture dispatch — three mutually-exclusive front arms:
        #   1. GT3 paired front coils (front_spring_range_nmm[1] > 0)
        #   2. Porsche-GTP roll spring (front_is_roll_spring True)
        #   3. GTP torsion bar (front_torsion_c > 0, OD options present)
        # Audit C-5: previously the legacy `front_torsion_c == 0.0` predicate
        # also matched GT3 (front_torsion_c=0.0) and routed it into the
        # roll-spring branch, which then fell back to
        # csm.front_roll_spring_rate_nmm (0.0 for GT3) — emitting zero
        # front rate. Add the explicit GT3 arm BEFORE the roll-spring arm.
        front_coil_rate_out = 0.0
        if csm.front_spring_range_nmm[1] > 0:
            # GT3 paired coil path
            if front_coil_rate_nmm is not None and front_coil_rate_nmm > 0:
                front_rate = csm.snap_front_rate(front_coil_rate_nmm)
            elif csm.front_baseline_rate_nmm > 0:
                front_rate = csm.snap_front_rate(csm.front_baseline_rate_nmm)
            else:
                # Last-ditch: use the lower bound. Should not normally fire
                # since stubs all set front_baseline_rate_nmm.
                front_rate = csm.front_spring_range_nmm[0]
            front_torsion_od_mm = 0.0
            front_coil_rate_out = front_rate
        elif csm.front_is_roll_spring or csm.front_torsion_c == 0.0:
            # Porsche-GTP roll spring path (or genuinely unmodeled front)
            if front_roll_spring_nmm is not None and front_roll_spring_nmm > 0:
                front_rate = csm.snap_front_roll_spring(front_roll_spring_nmm)
            else:
                front_rate = csm.front_roll_spring_rate_nmm
            front_torsion_od_mm = 0.0
        else:
            # GTP torsion bar path: snap OD to discrete garage option
            front_torsion_od_mm = csm.snap_torsion_od(front_torsion_od_mm)
            front_rate = csm.torsion_bar_rate(front_torsion_od_mm)
        # Snap rear spring/torsion to garage step
        if csm.rear_is_torsion_bar and rear_torsion_od_mm is not None:
            rear_torsion_od_mm = csm.snap_rear_torsion_od(rear_torsion_od_mm)
            rear_spring_rate_nmm = csm.rear_torsion_bar_rate(rear_torsion_od_mm)
        else:
            rear_spring_rate_nmm = csm.snap_rear_rate(rear_spring_rate_nmm)
        total_mass = self.car.total_mass(fuel_load_l)
        m_f_corner = total_mass * self.car.weight_dist_front / 2
        m_r_corner = total_mass * (1 - self.car.weight_dist_front) / 2

        bump_freq = self.car.rh_variance.dominant_bump_freq_hz
        front_freq = self.natural_freq(front_rate, m_f_corner)
        rear_rate = rear_spring_rate_nmm
        rear_freq = self.natural_freq(rear_rate, m_r_corner)

        # === Compute derived values ===
        total_front_heave = front_heave_nmm + 2 * front_rate  # front MR=1.0
        total_rear_heave = rear_third_nmm + 2 * rear_rate * csm.rear_motion_ratio ** 2
        front_heave_ratio = front_heave_nmm / front_rate if front_rate > 0 else 0
        rear_third_ratio = rear_third_nmm / rear_rate if rear_rate > 0 else 0
        front_isolation = bump_freq / front_freq if front_freq > 0 else 0
        rear_isolation = bump_freq / rear_freq if rear_freq > 0 else 0

        # Heave-mode natural frequencies (what FFT measures on straights)
        # Heave mode: both wheels move together, full axle sprung mass
        # k_total = heave_spring + 2 * corner_wheel_rate (all in N/mm)
        # Rear corner wheel rate = spring_rate * MR^2
        rear_wheel_rate = rear_rate * csm.rear_motion_ratio ** 2
        k_heave_front = front_heave_nmm + 2 * front_rate  # front MR=1.0
        k_heave_rear = rear_third_nmm + 2 * rear_wheel_rate
        # Sprung mass per axle (subtract ~50 kg/corner unsprung)
        m_sprung_front = max(m_f_corner * 2 - 100, 200)  # kg
        m_sprung_rear = max(m_r_corner * 2 - 100, 200)
        front_heave_freq = self.natural_freq(k_heave_front / 2, m_sprung_front / 2)
        rear_heave_freq = self.natural_freq(k_heave_rear / 2, m_sprung_rear / 2)

        # === Constraint checks ===
        constraints = self._check_constraints(
            front_rate=front_rate,
            rear_rate=rear_rate,
            front_heave_nmm=front_heave_nmm,
            rear_third_nmm=rear_third_nmm,
            front_freq=front_freq,
            rear_freq=rear_freq,
            bump_freq=bump_freq,
            m_f_corner=m_f_corner,
            m_r_corner=m_r_corner,
        )

        # Torsion bar preload turns:
        #   - Ferrari: calibrated regression (R²=0.51/0.55, see _solve_ferrari_torsion_bar_turns).
        #   - BMW / Cadillac / Acura: passthrough of driver-loaded value from
        #     CurrentSetup (no calibrated solver — old formula drifted from
        #     driver-loaded ground truth).
        #   - Porsche: 0.0 (roll spring car, no front torsion bar).
        _f_perch = front_heave_perch_mm if front_heave_perch_mm is not None else -16.5
        _r_perch = rear_third_perch_mm if rear_third_perch_mm is not None else -104.0
        front_tb_turns, rear_tb_turns = _solve_torsion_bar_turns(
            self.car,
            current_setup,
            front_torsion_od_mm=front_torsion_od_mm,
            rear_spring_rate_nmm=rear_spring_rate_nmm,
            front_heave_nmm=front_heave_nmm,
            rear_third_nmm=rear_third_nmm,
            front_heave_perch_mm=_f_perch,
            rear_third_perch_mm=_r_perch,
        )

        return CornerSpringSolution(
            front_torsion_od_mm=front_torsion_od_mm,
            front_roll_spring_nmm=round(front_rate, 0) if csm.front_roll_spring_range_nmm[1] > 0 else 0.0,
            front_wheel_rate_nmm=round(front_rate, 1),
            front_natural_freq_hz=round(front_freq, 2),
            front_heave_corner_ratio=round(front_heave_ratio, 1),
            front_mass_per_corner_kg=round(m_f_corner, 0),
            rear_spring_rate_nmm=rear_rate,
            rear_motion_ratio=csm.rear_motion_ratio,
            rear_natural_freq_hz=round(rear_freq, 2),
            rear_third_corner_ratio=round(rear_third_ratio, 1),
            rear_mass_per_corner_kg=round(m_r_corner, 0),
            front_heave_mode_freq_hz=round(front_heave_freq, 2),
            rear_heave_mode_freq_hz=round(rear_heave_freq, 2),
            total_front_heave_nmm=round(total_front_heave, 0),
            total_rear_heave_nmm=round(total_rear_heave, 0),
            track_bump_freq_hz=bump_freq,
            front_freq_isolation_ratio=round(front_isolation, 1),
            rear_freq_isolation_ratio=round(rear_isolation, 1),
            rear_spring_perch_mm=(
                csm.rear_spring_perch_baseline_mm
                if rear_spring_perch_mm is None
                else rear_spring_perch_mm
            ),
            rear_torsion_od_mm=rear_torsion_od_mm,
            constraints=constraints,
            front_torsion_bar_turns=front_tb_turns,
            rear_torsion_bar_turns=rear_tb_turns,
            front_coil_rate_nmm=round(front_coil_rate_out, 1),
            front_coil_perch_mm=(
                front_coil_perch_mm if front_coil_perch_mm is not None else 0.0
            ),
        )

    def solve_candidates(
        self,
        front_heave_nmm: float,
        rear_third_nmm: float,
        fuel_load_l: float | None = None,
        current_rear_third_nmm: float | None = None,
        current_rear_spring_nmm: float | None = None,
        max_candidates: int = 20,
        current_setup=None,
    ) -> list[CornerSpringSolution]:
        """Evaluate all legal (front_OD, rear_spring) combos and return top-N.

        Exhaustively enumerates the discrete front torsion bar OD options and
        the rear spring rate range, builds a :class:`CornerSpringSolution` for
        each via :meth:`solution_from_explicit_rates`, and returns the top
        *max_candidates* ranked by a quick composite score that balances:

        - Frequency isolation (higher = better bump absorption = more grip)
        - Heave-to-corner ratio within the 1.5-3.5× guideline
        - Constraint satisfaction count

        The first element is always the ``solve()`` result (the physics-targeted
        single answer) so existing callers can use ``solve_candidates()[0]`` as
        a drop-in replacement.

        For Porsche (no front torsion bars), front rate candidates are drawn from
        the roll spring range in 10 N/mm steps.

        Returns:
            List of up to *max_candidates* CornerSpringSolution objects, scored
            best-first.
        """
        if fuel_load_l is None:
            fuel_load_l = getattr(self.car, 'fuel_capacity_l', 89.0)
        csm = self.car.corner_spring

        # Always include the physics-targeted solve as the first candidate
        base = self.solve(
            front_heave_nmm=front_heave_nmm,
            rear_third_nmm=rear_third_nmm,
            fuel_load_l=fuel_load_l,
            current_rear_third_nmm=current_rear_third_nmm,
            current_rear_spring_nmm=current_rear_spring_nmm,
            current_setup=current_setup,
        )

        # Build list of front options. Three architecture branches:
        #   1. GT3 paired front coils (front_spring_range_nmm[1] > 0)
        #   2. Porsche-GTP roll spring (front_is_roll_spring True)
        #   3. GTP torsion bar (front_torsion_od_options non-empty)
        # Audit C-9: previously a GT3 car had `front_torsion_c == 0.0` which
        # set `use_front_roll_spring=True`, but `front_roll_spring_range_nmm`
        # is (0, 0) for GT3 — falling through to a single-element list of 0.0
        # and emitting only one (degenerate) candidate.
        use_gt3_coil = csm.front_spring_range_nmm[1] > 0
        use_front_roll_spring = (
            (csm.front_is_roll_spring or csm.front_torsion_c == 0.0)
            and not use_gt3_coil
        )
        if use_gt3_coil:
            # GT3 paired coil — sample on the resolution grid.
            lo_c, hi_c = csm.front_spring_range_nmm
            step_c = csm.front_spring_resolution_nmm or 10.0
            front_ods = []
            v = lo_c
            while v <= hi_c + 1e-9:
                front_ods.append(round(v, 3))
                v += step_c
        elif not use_front_roll_spring and csm.front_torsion_od_options:
            front_ods = list(csm.front_torsion_od_options)
        elif use_front_roll_spring and csm.front_roll_spring_range_nmm[1] > 0:
            # Porsche roll spring — sample in 10 N/mm steps
            lo, hi = csm.front_roll_spring_range_nmm
            step = 10.0
            front_ods = []
            v = lo
            while v <= hi:
                front_ods.append(v)
                v += step
        else:
            front_ods = [base.front_torsion_od_mm]

        # Build list of rear options
        if csm.rear_is_torsion_bar and hasattr(csm, 'rear_torsion_od_options') and csm.rear_torsion_od_options:
            rear_opts = list(csm.rear_torsion_od_options)
            use_rear_torsion = True
        else:
            r_lo, r_hi = csm.rear_spring_range_nmm
            r_step = getattr(csm, 'rear_spring_resolution_nmm', 5.0) or 5.0
            rear_opts = []
            v = r_lo
            while v <= r_hi:
                rear_opts.append(v)
                v += r_step
            use_rear_torsion = False

        # Pre-compute reference values for scoring
        ratio_lo, ratio_hi = csm.heave_corner_ratio_range
        ratio_mid = (ratio_lo + ratio_hi) / 2
        bump_freq = self.car.rh_variance.dominant_bump_freq_hz

        # Enumerate all combos, build quick score
        scored: list[tuple[float, CornerSpringSolution]] = []
        seen: set[tuple[float, float]] = set()

        for f_od in front_ods:
            for r_opt in rear_opts:
                rear_torsion_od = r_opt if use_rear_torsion else None
                rear_rate = r_opt if not use_rear_torsion else 0.0  # will be recomputed
                sol = self.solution_from_explicit_rates(
                    front_heave_nmm=front_heave_nmm,
                    rear_third_nmm=rear_third_nmm,
                    front_torsion_od_mm=0.0 if (use_front_roll_spring or use_gt3_coil) else f_od,
                    rear_spring_rate_nmm=rear_rate,
                    fuel_load_l=fuel_load_l,
                    rear_torsion_od_mm=rear_torsion_od,
                    front_roll_spring_nmm=f_od if use_front_roll_spring else None,
                    front_coil_rate_nmm=f_od if use_gt3_coil else None,
                    current_setup=current_setup,
                )
                # On GT3 the front_torsion_od_mm is always 0, so use front_coil_rate_nmm
                # in the dedupe key to keep distinct GT3 candidates separate.
                if use_gt3_coil:
                    key = (sol.front_coil_rate_nmm, sol.rear_spring_rate_nmm)
                else:
                    key = (sol.front_torsion_od_mm, sol.rear_spring_rate_nmm)
                if key in seen:
                    continue
                seen.add(key)

                # Quick composite score (higher = better)
                # Frequency isolation: more is better for grip
                iso_score = min(sol.front_freq_isolation_ratio, 5.0) + min(sol.rear_freq_isolation_ratio, 5.0)
                # Heave-corner ratio: penalize distance from guideline midpoint
                f_ratio_penalty = abs(sol.front_heave_corner_ratio - ratio_mid)
                r_ratio_penalty = abs(sol.rear_third_corner_ratio - ratio_mid)
                ratio_penalty = f_ratio_penalty + r_ratio_penalty
                # Constraint satisfaction bonus
                ok_count = sum(1 for c in sol.constraints if c.satisfied)
                # Score
                score = iso_score * 10 - ratio_penalty * 5 + ok_count * 2
                scored.append((score, sol))

        # Sort descending by score
        scored.sort(key=lambda x: x[0], reverse=True)

        # Ensure base solution is included and first. On GT3 the front_torsion
        # axis is always 0; key on front_coil_rate_nmm for dedupe.
        if use_gt3_coil:
            base_key = (base.front_coil_rate_nmm, base.rear_spring_rate_nmm)
        else:
            base_key = (base.front_torsion_od_mm, base.rear_spring_rate_nmm)
        results = [base]
        seen_keys: set[tuple[float, float]] = {base_key}
        for _, sol in scored:
            if len(results) >= max_candidates:
                break
            if use_gt3_coil:
                key = (sol.front_coil_rate_nmm, sol.rear_spring_rate_nmm)
            else:
                key = (sol.front_torsion_od_mm, sol.rear_spring_rate_nmm)
            if key not in seen_keys:
                seen_keys.add(key)
                results.append(sol)

        return results

    def _apply_lltd_floor(
        self,
        front_rate: float,
        rear_third_nmm: float,
        front_heave_nmm: float,
        fuel_load_l: float,
    ) -> float:
        """Bump front roll spring rate if needed to make LLTD target achievable.

        Only applies to roll-spring cars (Porsche).  Computes the approximate
        LLTD that Step 4 could achieve with the proposed front rate at maximum
        ARB stiffness.  If that LLTD is more than 5 pp below the car's target,
        the front rate is raised until the gap closes (or the garage ceiling is
        hit).

        Returns the (possibly increased) front roll spring rate, snapped to
        garage step.
        """
        csm = self.car.corner_spring
        # Audit C-7: this helper assumes a Porsche-GTP roll-spring kinematic
        # (single roll spring with installation_ratio ≈ 0.882). For GT3 paired
        # front coils (or any car without a roll spring), the formula
        # K_roll = k * IR^2 * (t/2)^2 is wrong (paired-corner roll stiffness
        # is `2 * k_wheel * (t/2)^2`). Early-return so GT3 cars never enter.
        if not csm.front_is_roll_spring:
            return front_rate
        arb = self.car.arb

        # Determine the LLTD target (same logic as arb_solver.py)
        if self.car.measured_lltd_target is not None:
            lltd_target = self.car.measured_lltd_target
        else:
            tyre_sens = self.car.tyre_load_sensitivity
            pct_hs = self.track.pct_above_200kph
            hs_correction = 0.01 * pct_hs
            lltd_physics_offset = (tyre_sens / 0.20) * (0.05 + hs_correction)
            lltd_target = self.car.weight_dist_front + lltd_physics_offset

        # Rear corner wheel rate (raw spring → wheel rate via MR^2)
        # Use the rear spring from the third/corner ratio targeting
        # (at this point in solve(), rear hasn't been computed yet — use
        #  an approximate rear rate from the ratio heuristic).
        rear_sv_p99 = (self.track.shock_vel_p99_rear_clean_mps
                       if self.track.shock_vel_p99_rear_clean_mps > 0
                       else self.track.shock_vel_p99_rear_mps)
        rear_target_ratio = self._surface_severity_to_heave_ratio(rear_sv_p99)
        approx_rear_rate = rear_third_nmm / max(rear_target_ratio, 0.5)
        approx_rear_rate = max(approx_rear_rate, csm.rear_spring_range_nmm[0])
        approx_rear_rate = min(approx_rear_rate, csm.rear_spring_range_nmm[1])
        rear_wheel_rate = approx_rear_rate * csm.rear_motion_ratio ** 2

        # Rear spring roll stiffness: K = 2 * k_wheel(N/m) * (t/2)^2 * (pi/180)
        t_half_rear_m = (arb.track_width_rear_mm / 2) / 1000.0
        k_roll_rear_springs = 2.0 * (rear_wheel_rate * 1000.0) * (t_half_rear_m ** 2) * (math.pi / 180)

        # Maximum rear ARB stiffness (stiffest size, highest blade)
        max_rear_arb_k = 0.0
        for i, label in enumerate(arb.rear_size_labels):
            if label.lower() == "disconnected":
                continue
            k = arb.rear_stiffness_nmm_deg[i] * arb.blade_factor(arb.rear_blade_count, arb.rear_blade_count)
            max_rear_arb_k = max(max_rear_arb_k, k)

        k_roll_rear_total = k_roll_rear_springs + max_rear_arb_k

        # Front ARB stiffness at baseline (locked — not the live variable)
        k_farb = arb.front_roll_stiffness(arb.front_baseline_size, arb.front_baseline_blade)

        # Roll spring roll stiffness: K = k(N/m) * IR^2 * (t/2)^2 * (pi/180)
        ir = csm.front_roll_spring_installation_ratio
        t_half_front_m = (arb.track_width_front_mm / 2) / 1000.0

        def _front_roll_k(rate_nmm: float) -> float:
            return (rate_nmm * 1000.0) * (ir ** 2) * (t_half_front_m ** 2) * (math.pi / 180)

        k_roll_front = _front_roll_k(front_rate) + k_farb
        k_total = k_roll_front + k_roll_rear_total
        if k_total > 0:
            achievable_lltd = k_roll_front / k_total
        else:
            achievable_lltd = 0.5

        lltd_gap = lltd_target - achievable_lltd
        if lltd_gap <= 0.05:
            # Within 5 pp — no floor needed
            return front_rate

        # Need to bump front roll spring to close the LLTD gap.
        # From LLTD = K_f / (K_f + K_r), solving for K_f:
        #   K_f = LLTD_target * K_r / (1 - LLTD_target)
        # Then k_roll_spring = (K_f_needed - K_farb) and rate = k / (IR^2 * (t/2)^2 * pi/180)
        k_front_needed = lltd_target * k_roll_rear_total / max(1.0 - lltd_target, 0.01)
        k_spring_needed = k_front_needed - k_farb
        divisor = (ir ** 2) * (t_half_front_m ** 2) * (math.pi / 180)
        if divisor > 0 and k_spring_needed > 0:
            rate_needed_nmm = k_spring_needed / divisor / 1000.0
        else:
            return front_rate

        new_rate = csm.snap_front_roll_spring(rate_needed_nmm)
        if new_rate > front_rate:
            logger.debug(
                "LLTD floor: bumping front roll spring %.0f -> %.0f N/mm "
                "(achievable LLTD %.1f%% -> target %.1f%%)",
                front_rate, new_rate, achievable_lltd * 100, lltd_target * 100,
            )
            return new_rate
        return front_rate

    def _surface_severity_to_freq_ratio(self, shock_vel_p99_mps: float) -> float:
        """Map track surface severity to frequency isolation ratio.

        Higher shock velocity = bumpier surface = need more isolation = higher ratio.
        The ratio determines how far below the bump frequency the corner spring
        natural frequency should be.

        Returns a ratio in the range [2.5, 3.5].
        """
        # Linear interpolation:
        # p99 = 0.15 m/s (smooth) -> ratio 2.5
        # p99 = 0.35 m/s (very bumpy) -> ratio 3.5
        v_lo, v_hi = 0.15, 0.35
        r_lo, r_hi = 2.5, 3.5
        t = max(0, min(1, (shock_vel_p99_mps - v_lo) / (v_hi - v_lo)))
        return r_lo + t * (r_hi - r_lo)

    def _surface_severity_to_heave_ratio(self, shock_vel_p99_mps: float) -> float:
        """Map track surface severity to heave-to-corner spring ratio.

        Higher shock velocity = bumpier = want softer corner springs = higher ratio.
        This ratio determines the rear corner spring rate relative to the third spring.

        Returns a ratio in the range [3.0, 5.0].

        FIXED 2026-04-28: range was [2.0, 3.5] which under-predicted the ratio
        for cars with stiff third springs. Cadillac at Silverstone runs
        third=680 / corner=140 → ratio 4.86. The old range capped at 3.5,
        producing corner=194-340 (vs driver's 140). Widened to [3.0, 5.0]
        to cover the observed range across all GTP cars.
        """
        # Linear interpolation:
        # p99 = 0.15 m/s (smooth) -> ratio 3.0 (stiffer corner for platform)
        # p99 = 0.40 m/s (very bumpy) -> ratio 5.0 (softer corner for grip)
        v_lo, v_hi = 0.15, 0.40
        r_lo, r_hi = 3.0, 5.0
        t = max(0, min(1, (shock_vel_p99_mps - v_lo) / (v_hi - v_lo)))
        return r_lo + t * (r_hi - r_lo)

    def _check_constraints(
        self,
        front_rate: float,
        rear_rate: float,
        front_heave_nmm: float,
        rear_third_nmm: float,
        front_freq: float,
        rear_freq: float,
        bump_freq: float,
        m_f_corner: float,
        m_r_corner: float,
    ) -> list[CornerSpringCheck]:
        """Check all constraints on the proposed corner spring rates."""
        csm = self.car.corner_spring
        checks = []

        # 1. Front heave-to-corner ratio
        ratio_lo, ratio_hi = csm.heave_corner_ratio_range
        front_ratio = front_heave_nmm / front_rate if front_rate > 0 else 0
        checks.append(CornerSpringCheck(
            name=f"Front heave/corner ratio ({front_ratio:.1f}x)",
            satisfied=ratio_lo <= front_ratio <= ratio_hi,
            detail=f"Ratio {front_ratio:.1f}x outside guideline {ratio_lo}-{ratio_hi}x",
        ))

        # 2. Rear third-to-corner ratio
        rear_ratio = rear_third_nmm / rear_rate if rear_rate > 0 else 0
        checks.append(CornerSpringCheck(
            name=f"Rear third/corner ratio ({rear_ratio:.1f}x)",
            satisfied=ratio_lo <= rear_ratio <= ratio_hi,
            detail=f"Ratio {rear_ratio:.1f}x outside guideline {ratio_lo}-{ratio_hi}x",
        ))

        # 3. Front frequency isolation
        front_isolation = bump_freq / front_freq if front_freq > 0 else 0
        min_isolation = csm.min_freq_isolation_ratio
        checks.append(CornerSpringCheck(
            name=f"Front freq isolation ({front_isolation:.1f}x)",
            satisfied=front_isolation >= min_isolation,
            detail=f"Isolation {front_isolation:.1f}x < minimum {min_isolation}x",
        ))

        # 4. Rear frequency isolation (less strict — rear can be stiffer)
        rear_isolation = bump_freq / rear_freq if rear_freq > 0 else 0
        checks.append(CornerSpringCheck(
            name=f"Rear freq isolation ({rear_isolation:.1f}x)",
            satisfied=rear_isolation >= 1.2,  # Less strict for rear
            detail=f"Isolation {rear_isolation:.1f}x < minimum 1.2x",
        ))

        # 5. Total front heave stiffness adequate
        total_front = front_heave_nmm + 2 * front_rate
        # Must be at least as stiff as heave spring alone (Step 2 validation)
        checks.append(CornerSpringCheck(
            name=f"Total front heave ({total_front:.0f} N/mm)",
            satisfied=total_front >= front_heave_nmm,
            detail=f"Total heave {total_front:.0f} < heave spring {front_heave_nmm:.0f}",
        ))

        # 6. Front torsion bar OD / roll spring in valid range
        if csm.front_is_roll_spring or csm.front_torsion_c == 0.0:
            # Roll spring car — check roll spring rate is in garage range
            rs_lo, rs_hi = csm.front_roll_spring_range_nmm
            checks.append(CornerSpringCheck(
                name=f"Roll spring rate in range ({front_rate:.0f} N/mm)",
                satisfied=rs_lo <= front_rate <= rs_hi,
                detail=f"Rate {front_rate:.0f} outside range {rs_lo}-{rs_hi} N/mm",
            ))
        else:
            od = csm.torsion_bar_od_for_rate(front_rate)
            od_lo, od_hi = csm.front_torsion_od_range_mm
            if csm.front_torsion_od_options:
                snapped = csm.snap_torsion_od(od)
                checks.append(CornerSpringCheck(
                    name=f"Torsion bar OD valid ({snapped:.2f}mm)",
                    satisfied=snapped in csm.front_torsion_od_options,
                    detail=f"OD {od:.2f}mm not in discrete garage options",
                ))
            else:
                checks.append(CornerSpringCheck(
                    name=f"Torsion bar OD in range ({od:.1f}mm)",
                    satisfied=od_lo <= od <= od_hi,
                    detail=f"OD {od:.1f}mm outside range {od_lo}-{od_hi}mm",
                ))

        # 7. Rear spring rate in valid range
        r_lo, r_hi = csm.rear_spring_range_nmm
        checks.append(CornerSpringCheck(
            name=f"Rear rate in range ({rear_rate:.0f} N/mm)",
            satisfied=r_lo <= rear_rate <= r_hi,
            detail=f"Rate {rear_rate:.0f} outside range {r_lo}-{r_hi} N/mm",
        ))

        return checks
