"""Step 4: Anti-Roll Bar (ARB) Solver.

Determines front and rear ARB diameter and starting blade position to achieve
a target Lateral Load Transfer Distribution (LLTD) that provides neutral
mechanical balance.

Physics:
    Lateral load transfer is the redistribution of vertical tyre forces when
    the car corners. Total lateral load transfer is fixed by physics:

        ΔFz_total = m * ay * h_cg / t_avg

    ARBs control the DISTRIBUTION of this load transfer between front and
    rear axles. LLTD = front share of total lateral load transfer.

    Due to tyre load sensitivity (grip coefficient decreases as load
    increases), the axle with more load transfer has less total grip.
    This is the primary mechanical balance lever in GTP cars because
    the heave/third springs have ZERO roll stiffness contribution
    (geometric decoupling).

    LLTD formula (from roll stiffness):
        LLTD ≈ K_roll_front / (K_roll_front + K_roll_rear)

    Roll stiffness per axle:
        K_roll = K_arb + 2 * k_wheel * (t_half)^2

    where:
        K_arb     = ARB roll stiffness (N·m/deg)
        k_wheel   = corner wheel rate (N/m) from Step 3
        t_half    = half track width (m)

    OptimumG baseline (Claude Rouelle): LLTD target = static front weight
    distribution + 0.05. This gives a neutral steady-state balance — the
    front axle is slightly more loaded in roll, which counteracts the natural
    understeer tendency of a rear-heavy GTP car.

    BMW ARB strategy:
    - Keep FARB soft (blade 1) to maximize front tyre grip and turn-in bite
    - RARB is the primary live balance variable (blades 1-5)
    - Blade 1 = soft (slow corners): prevents snap oversteer without aero
    - Blade 4-5 = stiff (fast corners): shifts load transfer rear, front
      gains grip, sharpens turn-in. Both effects compound through LLTD.
    - This is a verified professional technique: single-variable balance
      via RARB only, keeping FARB near minimum.

    Per SKILL.md: "If you need to fix a slow-corner problem, use ARBs. If
    you need to fix a fast-corner problem, use aero."

Validated against BMW Sebring:
    - Soft front ARB + blade 1 (minimum), Medium rear + blade 3 baseline
    - Driver uses full RARB range (1→5) as live corner-by-corner adjustment
    - Best lap (1:49.98) used blade 1 in slow corners, blade 4-5 at speed
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from car_model.cars import CarModel
from track_model.profile import TrackProfile


@dataclass
class ARBConstraintCheck:
    """Result of a single ARB constraint check."""
    name: str
    passed: bool
    value: float
    target: float
    units: str
    note: str = ""


@dataclass
class ARBSolution:
    """Output of the Step 4 ARB solver."""

    # Recommended ARB setup
    front_arb_size: str                  # e.g. "Soft"
    front_arb_blade_start: int           # Recommended starting blade
    rear_arb_size: str                   # e.g. "Medium"
    rear_arb_blade_start: int            # Recommended starting blade

    # LLTD analysis
    lltd_achieved: float                 # Achieved LLTD at baseline blades
    lltd_target: float                   # Target LLTD (static_front + 0.05)
    lltd_error: float                    # |achieved - target|
    static_front_weight_dist: float      # Car static front weight fraction

    # Roll stiffness breakdown (N·m/deg)
    k_roll_front_springs: float          # Corner spring contribution, front
    k_roll_rear_springs: float           # Corner spring contribution, rear
    k_roll_front_arb: float              # ARB contribution, front
    k_roll_rear_arb: float               # ARB contribution, rear
    k_roll_front_total: float
    k_roll_rear_total: float

    # RARB sensitivity (LLTD change per blade step)
    rarb_sensitivity_per_blade: float    # ΔLLTD per rear blade step

    # Live RARB blade range (from telemetry coaching)
    rarb_blade_slow_corner: int          # Recommended blade for slow corners
    rarb_blade_fast_corner: int          # Recommended blade for fast corners
    farb_blade_locked: int               # Keep FARB at this value

    # LLTD at extreme blade positions
    lltd_at_rarb_min: float
    lltd_at_rarb_max: float

    # Constraint checks
    constraints: list[ARBConstraintCheck]

    # Notes
    car_specific_notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "===========================================================",
            "  STEP 4: ANTI-ROLL BAR (ARB) SOLUTION",
            "===========================================================",
            "",
            "  ARB SETUP",
            f"    Front ARB size:   {self.front_arb_size}",
            f"    Front ARB blade:  {self.front_arb_blade_start}  (locked — FARB is not the live variable)",
            f"    Rear ARB size:    {self.rear_arb_size}",
            f"    Rear ARB blade:   {self.rear_arb_blade_start}  (baseline, adjust live)",
            "",
            "  LLTD ANALYSIS",
            f"    Static front weight:  {self.static_front_weight_dist:.1%}",
            f"    Target LLTD:          {self.lltd_target:.1%}  (static + 5%)",
            f"    Achieved LLTD:        {self.lltd_achieved:.1%}",
            f"    LLTD error:           {self.lltd_error:.1%}",
            "",
            "  ROLL STIFFNESS BREAKDOWN (N·m/deg)",
            f"    Front springs:  {self.k_roll_front_springs:8.0f}",
            f"    Front ARB:      {self.k_roll_front_arb:8.0f}",
            f"    Front TOTAL:    {self.k_roll_front_total:8.0f}",
            f"",
            f"    Rear springs:   {self.k_roll_rear_springs:8.0f}",
            f"    Rear ARB:       {self.k_roll_rear_arb:8.0f}",
            f"    Rear TOTAL:     {self.k_roll_rear_total:8.0f}",
            "",
            "  LIVE RARB STRATEGY (corner-by-corner adjustment)",
            f"    RARB sensitivity:       {self.rarb_sensitivity_per_blade:+.1%} LLTD per blade step",
            f"    Slow corners (<80kph):  blade {self.rarb_blade_slow_corner}  "
            f"(LLTD {self.lltd_at_rarb_min:.1%}  — soft for rotation)",
            f"    Fast corners (>2.5g):   blade {self.rarb_blade_fast_corner}  "
            f"(LLTD {self.lltd_at_rarb_max:.1%}  — stiff for front bite)",
            f"    FARB blade:             {self.farb_blade_locked}  (keep here — RARB is the variable)",
        ]
        if self.constraints:
            lines += ["", "  CONSTRAINT CHECKS"]
            for c in self.constraints:
                status = "OK" if c.passed else "FAIL"
                lines.append(f"    [{status}] {c.name}: {c.value:.3f} {c.units} "
                              f"(target: {c.target:.3f})")
                if c.note:
                    lines.append(f"         {c.note}")
        if self.car_specific_notes:
            lines += ["", "  CAR-SPECIFIC NOTES"]
            for note in self.car_specific_notes:
                lines.append(f"    - {note}")
        lines.append("===========================================================")
        return "\n".join(lines)


class ARBSolver:
    """Step 4 solver: find ARB sizes and blades for target LLTD.

    Strategy:
    1. Compute roll stiffness contribution from corner springs (Step 3 output)
    2. Compute target LLTD (static front + 5%)
    3. Find ARB sizes + blades that achieve target LLTD at baseline
    4. Compute RARB live range for slow/fast corner strategy
    5. Apply car-specific overrides (BMW: lock FARB at 1, use RARB live)
    """

    def __init__(self, car: CarModel, track: TrackProfile):
        self.car = car
        self.track = track

    def _corner_spring_roll_stiffness(
        self, k_spring_nmm: float, track_width_mm: float,
        motion_ratio: float = 1.0,
    ) -> float:
        """Roll stiffness contribution from corner springs (N·m/deg).

        The input k_spring_nmm may be SPRING rate (as reported in iRacing
        garage) rather than WHEEL rate. The motion ratio converts:
            k_wheel = k_spring * MR^2

        Then roll stiffness:
            K_roll = 2 * k_wheel (N/m) * (t_half)^2 [N·m/rad]
                   = K_roll_rad * (pi/180) [N·m/deg]
        """
        k_wheel_nmm = k_spring_nmm * (motion_ratio ** 2)
        k_wheel_nm = k_wheel_nmm * 1000  # N/mm → N/m
        t_half_m = (track_width_mm / 2) / 1000  # mm → m
        k_roll_rad = 2.0 * k_wheel_nm * (t_half_m ** 2)  # N·m/rad
        # Convert N·m/rad → N·m/deg: multiply by (π/180)
        return k_roll_rad * (math.pi / 180)

    def _lltd_from_roll_stiffness(
        self, k_front: float, k_rear: float
    ) -> float:
        """LLTD from front/rear roll stiffness (N·m/deg)."""
        total = k_front + k_rear
        if total < 1e-6:
            return 0.5
        return k_front / total

    def _compute_lltd(
        self,
        front_size: str,
        front_blade: int,
        rear_size: str,
        rear_blade: int,
        k_springs_front: float,
        k_springs_rear: float,
    ) -> tuple[float, float, float, float, float]:
        """Compute LLTD for given ARB setup.

        Returns (lltd, k_farb, k_rarb, k_front_total, k_rear_total).
        """
        k_farb = self.car.arb.front_roll_stiffness(front_size, front_blade)
        k_rarb = self.car.arb.rear_roll_stiffness(rear_size, rear_blade)
        k_front = k_springs_front + k_farb
        k_rear = k_springs_rear + k_rarb
        lltd = self._lltd_from_roll_stiffness(k_front, k_rear)
        return lltd, k_farb, k_rarb, k_front, k_rear

    def solve(
        self,
        front_wheel_rate_nmm: float,
        rear_wheel_rate_nmm: float,
        lltd_offset: float = 0.0,
    ) -> ARBSolution:
        """Find ARB sizes and blades for target LLTD.

        Args:
            front_wheel_rate_nmm: Front corner wheel rate from Step 3 (N/mm)
            rear_wheel_rate_nmm: Rear corner wheel rate from Step 3 (N/mm)

        Returns:
            ARBSolution with recommended ARB setup and live blade strategy
        """
        arb = self.car.arb

        # Roll stiffness from corner springs
        # Front torsion bar: MR already baked into wheel rate from C*OD^4
        # Rear coil spring: apply MR^2 to convert spring rate to wheel rate
        cs = self.car.corner_spring
        k_springs_front = self._corner_spring_roll_stiffness(
            front_wheel_rate_nmm, arb.track_width_front_mm,
            motion_ratio=cs.front_motion_ratio,
        )
        k_springs_rear = self._corner_spring_roll_stiffness(
            rear_wheel_rate_nmm, arb.track_width_rear_mm,
            motion_ratio=cs.rear_motion_ratio,
        )

        # Target LLTD (OptimumG: static front weight + 5%) + modifier offset
        target_lltd = self.car.weight_dist_front + 0.05 + lltd_offset

        # ─── BMW ARB strategy ────────────────────────────────────────────────
        # Per SKILL.md and per-car-quirks.md:
        # - Keep FARB at blade 1 (minimum). Front ARB blades at/near 1 for
        #   maximum front mechanical grip.
        # - Use RARB as the primary live balance variable (blades 1→5)
        # - Blade 1 for slow corners (rotation without snap)
        # - Blade 4-5 for fast corners (front bite via LLTD shift)
        #
        # Find the rear ARB size such that, at its baseline blade (3),
        # LLTD is close to target with FARB at baseline.

        farb_size = arb.front_baseline_size
        farb_blade = arb.front_baseline_blade  # blade 1

        best_size = arb.rear_baseline_size
        best_blade = arb.rear_baseline_blade
        best_lltd_error = float("inf")

        # Search over all rear ARB sizes and blades
        for rear_size in arb.rear_size_labels:
            for blade in range(1, arb.rear_blade_count + 1):
                lltd, _, _, _, _ = self._compute_lltd(
                    farb_size, farb_blade, rear_size, blade,
                    k_springs_front, k_springs_rear
                )
                err = abs(lltd - target_lltd)
                if err < best_lltd_error:
                    best_lltd_error = err
                    best_size = rear_size
                    best_blade = blade

        # Compute full solution at chosen ARB setup
        lltd, k_farb, k_rarb, k_front, k_rear = self._compute_lltd(
            farb_size, farb_blade, best_size, best_blade,
            k_springs_front, k_springs_rear
        )

        # RARB sensitivity: ΔLLTD per blade step at the chosen rear size
        k_rarb_step_plus = self.car.arb.rear_roll_stiffness(best_size, min(best_blade + 1, arb.rear_blade_count))
        k_rarb_step_minus = self.car.arb.rear_roll_stiffness(best_size, max(best_blade - 1, 1))
        lltd_plus = self._lltd_from_roll_stiffness(k_front, k_rear - k_rarb + k_rarb_step_plus)
        lltd_minus = self._lltd_from_roll_stiffness(k_front, k_rear - k_rarb + k_rarb_step_minus)
        sensitivity = (lltd_plus - lltd_minus) / 2

        # Live blade range for slow vs fast corners
        rarb_slow_blade = 1   # softest: maximum rotation, needed without aero
        rarb_fast_blade = min(4, arb.rear_blade_count)  # stiff for front bite

        # LLTD at extreme blade positions
        lltd_min, _, _, _, _ = self._compute_lltd(
            farb_size, farb_blade, best_size, rarb_slow_blade,
            k_springs_front, k_springs_rear
        )
        lltd_max, _, _, _, _ = self._compute_lltd(
            farb_size, farb_blade, best_size, rarb_fast_blade,
            k_springs_front, k_springs_rear
        )

        # Constraint checks
        constraints = [
            ARBConstraintCheck(
                name="LLTD target",
                passed=abs(lltd - target_lltd) < 0.05,
                value=lltd,
                target=target_lltd,
                units="fraction",
                note=f"Error: {abs(lltd - target_lltd):.1%}",
            ),
            ARBConstraintCheck(
                name="RARB range covers slow-corner blade",
                passed=rarb_slow_blade >= 1,
                value=float(rarb_slow_blade),
                target=1.0,
                units="blade",
            ),
            ARBConstraintCheck(
                name="RARB sensitivity within useful range",
                passed=0.005 < abs(sensitivity) < 0.05,
                value=abs(sensitivity),
                target=0.02,
                units="LLTD/blade",
                note="<0.005 = insensitive (wrong bar size), >0.05 = too sensitive (step down)",
            ),
        ]

        # Car-specific notes (BMW)
        notes: list[str] = [
            "BMW: keep FARB at blade 1 (maximum front mechanical grip). "
            "Use RARB as the only live balance variable.",
            f"Rear ARB '{best_size}' provides the correct blade range. "
            "If blade runs out of range, change ARB diameter — not FARB.",
            "Stiffer RARB -> shifts load transfer rear -> front GAINS grip via LLTD -> "
            "sharpens front-end bite. Softer RARB -> stable/planted rear.",
            "Cold tyre out-lap: RARB at blade 1 to prevent snap oversteer before tyres are up to temperature.",
        ]

        return ARBSolution(
            front_arb_size=farb_size,
            front_arb_blade_start=farb_blade,
            rear_arb_size=best_size,
            rear_arb_blade_start=best_blade,
            lltd_achieved=round(lltd, 4),
            lltd_target=round(target_lltd, 4),
            lltd_error=round(abs(lltd - target_lltd), 4),
            static_front_weight_dist=self.car.weight_dist_front,
            k_roll_front_springs=round(k_springs_front, 0),
            k_roll_rear_springs=round(k_springs_rear, 0),
            k_roll_front_arb=round(k_farb, 0),
            k_roll_rear_arb=round(k_rarb, 0),
            k_roll_front_total=round(k_front, 0),
            k_roll_rear_total=round(k_rear, 0),
            rarb_sensitivity_per_blade=round(sensitivity, 4),
            rarb_blade_slow_corner=rarb_slow_blade,
            rarb_blade_fast_corner=rarb_fast_blade,
            farb_blade_locked=farb_blade,
            lltd_at_rarb_min=round(lltd_min, 4),
            lltd_at_rarb_max=round(lltd_max, 4),
            constraints=constraints,
            car_specific_notes=notes,
        )
