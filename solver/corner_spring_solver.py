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

import math
from dataclasses import dataclass, field

from car_model.cars import CarModel
from track_model.profile import TrackProfile


@dataclass
class CornerSpringSolution:
    """Output of the Step 3 corner spring solver."""

    # Front torsion bar
    front_torsion_od_mm: float
    front_wheel_rate_nmm: float
    front_natural_freq_hz: float
    front_heave_corner_ratio: float   # heave_spring / corner_wheel_rate
    front_mass_per_corner_kg: float

    # Rear coil spring
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

    def summary(self) -> str:
        """Human-readable summary of the solution."""
        lines = [
            "===========================================================",
            "  STEP 3: CORNER SPRING SOLUTION",
            "===========================================================",
            "",
            "  FRONT TORSION BAR",
            f"    Torsion bar OD:      {self.front_torsion_od_mm:6.2f} mm",
            f"    Wheel rate:          {self.front_wheel_rate_nmm:6.1f} N/mm",
            f"    Natural frequency:   {self.front_natural_freq_hz:6.2f} Hz",
            f"    Heave/corner ratio:  {self.front_heave_corner_ratio:6.1f}x "
            f"(guideline: 1.5-3.5x)",
            f"    Freq isolation:      {self.front_freq_isolation_ratio:6.1f}x "
            f"(target: >2.5x)",
            "",
            "  REAR COIL SPRING",
            f"    Spring rate:         {self.rear_spring_rate_nmm:6.0f} N/mm",
            f"    Natural frequency:   {self.rear_natural_freq_hz:6.2f} Hz",
            f"    Third/corner ratio:  {self.rear_third_corner_ratio:6.1f}x "
            f"(guideline: 1.5-3.5x)",
            f"    Freq isolation:      {self.rear_freq_isolation_ratio:6.1f}x",
            f"    Perch offset:        {self.rear_spring_perch_mm:6.1f} mm (baseline)",
            "",
            "  TOTAL HEAVE STIFFNESS (heave/third + 2 * corner)",
            f"    Front:  {self.total_front_heave_nmm:6.0f} N/mm "
            f"(heave alone: {self.total_front_heave_nmm - 2*self.front_wheel_rate_nmm:.0f})",
            f"    Rear:   {self.total_rear_heave_nmm:6.0f} N/mm "
            f"(third alone: {self.total_rear_heave_nmm - 2*self.rear_spring_rate_nmm:.0f})",
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
        fuel_load_l: float = 89.0,
    ) -> CornerSpringSolution:
        """Find optimal corner spring rates.

        Args:
            front_heave_nmm: Front heave spring rate from Step 2
            rear_third_nmm: Rear third spring rate from Step 2
            fuel_load_l: Fuel load (affects corner mass)

        Returns:
            CornerSpringSolution with torsion bar OD and rear rate
        """
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
        front_freq_ratio = self._surface_severity_to_freq_ratio(
            self.track.shock_vel_p99_front_mps
        )
        front_target_freq = bump_freq / front_freq_ratio
        front_target_rate = self.rate_for_freq(front_target_freq, m_f_corner)

        # Check heave-to-corner ratio constraint
        ratio_lo, ratio_hi = csm.heave_corner_ratio_range
        front_max_for_ratio = front_heave_nmm / ratio_lo  # Upper bound from ratio
        front_min_for_ratio = front_heave_nmm / ratio_hi  # Lower bound from ratio

        # Clamp to ratio bounds
        front_rate = max(front_target_rate, front_min_for_ratio)
        front_rate = min(front_rate, front_max_for_ratio)

        # Convert to torsion bar OD
        front_od = csm.torsion_bar_od_for_rate(front_rate)
        front_od = csm.snap_torsion_od(front_od)

        # Clamp to valid OD range
        front_od = max(front_od, csm.front_torsion_od_range_mm[0])
        front_od = min(front_od, csm.front_torsion_od_range_mm[1])

        # Recalculate actual rate from snapped OD
        front_rate = csm.torsion_bar_rate(front_od)
        front_freq = self.natural_freq(front_rate, m_f_corner)

        # === REAR: Third-to-corner ratio targeting ===
        # For the rear, the binding constraint is the third/corner ratio.
        # Bumpy tracks need higher ratio (softer corner for grip).
        # The target ratio is scaled by surface severity.
        rear_freq_ratio = self._surface_severity_to_freq_ratio(
            self.track.shock_vel_p99_rear_mps
        )

        # Rear target from third/corner ratio
        # For bumpy track: ratio ~3.0 (softer corner)
        # For smooth track: ratio ~2.0 (stiffer corner)
        rear_target_ratio = self._surface_severity_to_heave_ratio(
            self.track.shock_vel_p99_rear_mps
        )
        rear_target_rate = rear_third_nmm / rear_target_ratio

        # Clamp to valid range and snap
        rear_rate = max(rear_target_rate, csm.rear_spring_range_nmm[0])
        rear_rate = min(rear_rate, csm.rear_spring_range_nmm[1])
        rear_rate = csm.snap_rear_rate(rear_rate)

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

        return CornerSpringSolution(
            front_torsion_od_mm=front_od,
            front_wheel_rate_nmm=round(front_rate, 1),
            front_natural_freq_hz=round(front_freq, 2),
            front_heave_corner_ratio=round(front_heave_ratio, 1),
            front_mass_per_corner_kg=round(m_f_corner, 0),
            rear_spring_rate_nmm=rear_rate,
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
            rear_spring_perch_mm=csm.rear_spring_perch_baseline_mm,
            constraints=constraints,
        )

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

        Returns a ratio in the range [2.0, 3.5].
        """
        # Linear interpolation:
        # p99 = 0.15 m/s (smooth) -> ratio 2.0 (stiffer corner for platform)
        # p99 = 0.40 m/s (very bumpy) -> ratio 3.5 (softer corner for grip)
        v_lo, v_hi = 0.15, 0.40
        r_lo, r_hi = 2.0, 3.5
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

        # 6. Front torsion bar OD in valid range
        od = csm.torsion_bar_od_for_rate(front_rate)
        od_lo, od_hi = csm.front_torsion_od_range_mm
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
