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

import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

from car_model.cars import CarModel
from solver._lltd import LLTD_MAX, LLTD_MIN, optimal_lltd
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
    parameter_search_status: dict[str, str] = field(default_factory=dict)
    parameter_search_evidence: dict[str, list[str]] = field(default_factory=dict)

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
            f"    Target LLTD:          {self.lltd_target:.1%}  (tyre-sensitivity optimized)",
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
        current_rear_arb_size: str | None = None,
        current_rear_arb_blade: int | None = None,
        current_front_arb_size: str | None = None,
        current_front_arb_blade: int | None = None,
    ) -> ARBSolution:
        """Find ARB sizes and blades for target LLTD.

        Args:
            front_wheel_rate_nmm: Front corner wheel rate from Step 3 (N/mm)
            rear_wheel_rate_nmm: Rear corner wheel rate from Step 3 (N/mm)
            lltd_offset: Offset added to LLTD target (from SolverModifiers)
            current_rear_arb_size: Driver-loaded rear ARB size (for driver anchor fallback)
            current_rear_arb_blade: Driver-loaded rear ARB blade (for driver anchor fallback)
            current_front_arb_size: Driver-loaded front ARB size.
                ACCEPTED BUT NOT CURRENTLY USED IN SEARCH — the front ARB is fixed
                at the start of the rear search loop (``farb_size``, ``farb_blade``
                selected by LLTD proximity). A symmetrical front anchor (like the
                rear anchor) would require an additional search pass. Retained in
                the API for future implementation.
            current_front_arb_blade: Driver-loaded front ARB blade.
                ACCEPTED BUT NOT CURRENTLY USED — same reason as current_front_arb_size.

        Returns:
            ARBSolution with recommended ARB setup and live blade strategy
        """
        arb = self.car.arb

        # Roll stiffness from springs
        # Rear: always paired corner springs → K = 2 * k_wheel * (t/2)²
        k_springs_rear = self._corner_spring_roll_stiffness(
            rear_wheel_rate_nmm, arb.track_width_rear_mm,
        )
        # Front: depends on architecture
        csm = self.car.corner_spring
        if csm.front_is_roll_spring:
            # Multimatic (Porsche): single roll spring, not a pair.
            # K_roll = k * IR² * (t/2)²  (no factor of 2)
            ir = csm.front_roll_spring_installation_ratio
            k_wheel_nm = front_wheel_rate_nmm * 1000.0
            t_half_m = (arb.track_width_front_mm / 2) / 1000.0
            k_springs_front = k_wheel_nm * (ir ** 2) * (t_half_m ** 2) * (math.pi / 180)
        else:
            # Conventional (BMW/Ferrari/etc): paired corner springs
            k_springs_front = self._corner_spring_roll_stiffness(
                front_wheel_rate_nmm, arb.track_width_front_mm,
            )

        # Target LLTD — use measured value if available, otherwise physics-based formula.
        #
        # Measured LLTD: When IBT telemetry provides a calibrated target (stored in
        # car.measured_lltd_target), use it directly. This overrides the theoretical
        # formula below. BMW/Sebring: measured_lltd_target=0.41 from 46 sessions.
        #
        # Theoretical LLTD (when measured_lltd_target is None):
        # Base: OptimumG/Milliken RCVD baseline at λ=0.20 → +5% over static front weight.
        # Formula: offset = (λ / 0.20) * 0.05, so λ=0.20 → +5%, λ=0.10 → +2.5%, λ=0.30 → +7.5%.
        #
        # Speed correction (validated by Milliken RCVD Ch.18 + Ron Sutton empirical rule):
        # For road courses with fast corners (>160 kph / 100 mph), optimal LLTD increases
        # by 0.5–1.0% above the baseline +5% rule. The physics reason: at high speed, aero
        # downforce shifts effective weight distribution rearward (rear DF > front DF in most
        # GTP setups), requiring more front LLTD bias to maintain neutral balance. Also, high-
        # speed cornering demands faster, more decisive weight transfer — stiffer front roll
        # resistance helps the front tyres load up instantly without lagging behind the rear.
        #
        # Implementation: use track.pct_above_200kph as a proxy for "high speed track".
        #   pct_above_200kph=0   (slow track, e.g. Long Beach): +5.0%
        #   pct_above_200kph=0.3 (mixed, e.g. Sebring):         +5.3%
        #   pct_above_200kph=0.5 (fast, e.g. Daytona Road):     +5.5%
        #   pct_above_200kph=0.8 (Monza / Le Mans):             +5.8%
        # This matches the empirical +5.5–6.0% recommendation for fast tracks.

        # Use measured LLTD target if available (overrides theoretical formula)
        car_name = getattr(self.car, "canonical_name", None)
        if self.car.measured_lltd_target is not None:
            target_lltd = self.car.measured_lltd_target + lltd_offset
        else:
            logger.info(
                "LLTD target: using physics formula (measured_lltd_target not set for %s)",
                car_name or "unknown",
            )
            target_lltd = optimal_lltd(
                front_weight_dist=self.car.weight_dist_front,
                tyre_sens=self.car.tyre_load_sensitivity,
                pct_above_200kph=self.track.pct_above_200kph,
                car_name=car_name,
            ) + lltd_offset

        # Bounds-check: post-offset LLTD must still be in the physically plausible range.
        if target_lltd < LLTD_MIN or target_lltd > LLTD_MAX:
            logger.warning(
                "LLTD target %.3f is outside [%.2f, %.2f] — clamping "
                "(lltd_offset=%.3f may be too extreme)",
                target_lltd, LLTD_MIN, LLTD_MAX, lltd_offset,
            )
            target_lltd = max(LLTD_MIN, min(LLTD_MAX, target_lltd))

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

        # Prefer current setup's ARB size — only change size if no blade within
        # the current size achieves an acceptable LLTD (within 0.015 = 1.5%).
        # Changing ARB bar size has massive feel implications and shouldn't be
        # done casually; blade changes within a size are the expected tuning range.
        preferred_size = current_rear_arb_size or arb.rear_baseline_size
        preferred_best_blade = arb.rear_baseline_blade
        preferred_best_error = float("inf")
        if preferred_size in arb.rear_size_labels and preferred_size.lower() != "disconnected":
            for blade in range(1, arb.rear_blade_count + 1):
                lltd, _, _, _, _ = self._compute_lltd(
                    farb_size, farb_blade, preferred_size, blade,
                    k_springs_front, k_springs_rear
                )
                err = abs(lltd - target_lltd)
                if err < preferred_best_error:
                    preferred_best_error = err
                    preferred_best_blade = blade

        # Use the preferred size if it can get within 1.5% LLTD error
        if preferred_best_error < 0.015:
            best_size = preferred_size
            best_blade = preferred_best_blade
            best_lltd_error = preferred_best_error
        else:
            # Preferred size can't achieve target — search all sizes
            for rear_size in arb.rear_size_labels:
                if rear_size.lower() == "disconnected":
                    continue
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

            # ── Driver anchor fallback ──
            # If even the best searched setup is far from target (>3 pp),
            # the LLTD physics model is mis-calibrated for this car/track
            # combo and the IBT-validated current setup is more reliable
            # than any model-derived guess. Anchor to the driver's loaded
            # ARB and accept the model's LLTD reading is wrong.
            #
            # Validated 2026-04-07 against Porsche/Algarve where:
            #   measured LLTD target = 0.503 (from 14 IBT sessions)
            #   model says driver setup (Stiff/10) gives LLTD = 0.391
            #   → 11.2 pp gap means the rear-roll-stiffness contribution
            #     is over-stated. Solver picks Soft/1 (LLTD=0.43) as
            #     "closest" but driver's Stiff/10 actually achieves the
            #     target in real telemetry. Anchor to driver.
            anchor_eligible = (
                current_rear_arb_size is not None
                and current_rear_arb_blade is not None
                and current_rear_arb_size in arb.rear_size_labels
                and current_rear_arb_size.lower() != "disconnected"
                and 1 <= int(current_rear_arb_blade or 0) <= arb.rear_blade_count
            )
            if best_lltd_error > 0.03 and anchor_eligible:
                logger.warning(
                    "ARB driver-anchor fallback fired: best searched LLTD "
                    "error %.3f > 0.03 (target=%.3f). Anchoring to driver-loaded "
                    "rear ARB %s/%d (physics target unverifiable per LLTD epistemic gap).",
                    best_lltd_error, target_lltd,
                    current_rear_arb_size, int(current_rear_arb_blade),
                )
                best_size = current_rear_arb_size
                best_blade = int(current_rear_arb_blade)
                lltd_anchor, _, _, _, _ = self._compute_lltd(
                    farb_size, farb_blade, best_size, best_blade,
                    k_springs_front, k_springs_rear
                )
                best_lltd_error = abs(lltd_anchor - target_lltd)
            elif best_lltd_error > 0.03:
                logger.warning(
                    "ARB target unreachable: best searched LLTD error %.3f > 0.03 "
                    "(target=%.3f, picked %s/%d). No driver-loaded ARB available "
                    "for anchoring — solver returning best-effort match.",
                    best_lltd_error, target_lltd, best_size, best_blade,
                )

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

        # Car-specific notes
        car_name = self.car.canonical_name
        if car_name == "bmw":
            notes: list[str] = [
                "BMW: keep FARB at blade 1 (maximum front mechanical grip). "
                "Use RARB as the only live balance variable.",
                f"Rear ARB '{best_size}' provides the correct blade range. "
                "If blade runs out of range, change ARB diameter — not FARB.",
                "Stiffer RARB -> shifts load transfer rear -> front GAINS grip via LLTD -> "
                "sharpens front-end bite. Softer RARB -> stable/planted rear.",
                "Cold tyre out-lap: RARB at blade 1 to prevent snap oversteer before tyres are up to temperature.",
            ]
        elif car_name == "ferrari":
            notes = [
                f"Ferrari 499P: Front ARB '{farb_size}'/blade {farb_blade} sets baseline front roll stiffness. "
                "Both FARB and RARB are adjustable live (no lockout).",
                f"Rear ARB '{best_size}' provides the correct blade range. "
                "If blade runs out of range, change ARB letter (A→B→C etc.) — not FARB.",
                "Stiffer RARB -> more rear load transfer -> front gains grip -> sharpens turn-in. "
                "Softer RARB -> easier rotation, less front bite.",
                "Cold tyre out-lap: RARB at blade 1 to prevent snap oversteer before tyres are up to temperature.",
                "Ferrari ARBs use letter sizing (A=softest, E=stiffest). "
                "Both front and rear blades can be adjusted in-car.",
            ]
        elif car_name == "acura":
            notes = [
                "Acura ARX-06 (ORECA): FARB at minimum for front grip. "
                "Use RARB as the primary live balance lever.",
                f"Rear ARB '{best_size}' selected. Adjust blades to tune balance through corners.",
                "Stiffer RARB -> shifts load transfer rear -> front gains grip -> better turn-in. "
                "Softer RARB -> more rear stability.",
                "Cold tyre out-lap: RARB at blade 1 to prevent snap oversteer.",
            ]
        else:
            notes = [
                f"{self.car.name}: Front ARB '{farb_size}'/blade {farb_blade}, "
                f"Rear ARB '{best_size}'/blade {best_blade}.",
                "Stiffer RARB -> more load transfer to rear -> front gains grip via LLTD. "
                "Use RARB as the primary live balance variable.",
                "Cold tyre out-lap: softer RARB to prevent snap oversteer.",
            ]

        # parameter_search_status: classify ARB settings as user-set
        pss = {
            "front_arb_size": "user_set",
            "front_arb_blade": "user_set",
            "rear_arb_size": "user_set",
            "rear_arb_blade": "user_set",
        }

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
            parameter_search_status=pss,
        )

    def solve_candidates(
        self,
        front_wheel_rate_nmm: float,
        rear_wheel_rate_nmm: float,
        lltd_offset: float = 0.0,
        current_rear_arb_size: str | None = None,
        current_rear_arb_blade: int | None = None,
        current_front_arb_size: str | None = None,
        current_front_arb_blade: int | None = None,
        lltd_tolerance: float = 0.005,
        max_candidates: int = 10,
    ) -> list[ARBSolution]:
        """Return all ARB combos achieving LLTD within *lltd_tolerance*.

        Unlike ``solve()`` which picks the single closest combo, this method
        enumerates every legal (rear_size, rear_blade) combination (front ARB
        is locked at baseline) and returns all that land within ±lltd_tolerance
        of the physics target. The combos are sorted by |LLTD_error| ascending.

        The first element is always the ``solve()`` result.

        Returns:
            List of :class:`ARBSolution` objects, closest-LLTD first.
        """
        # Always include the standard single-answer solve as first candidate
        base = self.solve(
            front_wheel_rate_nmm=front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
            lltd_offset=lltd_offset,
            current_rear_arb_size=current_rear_arb_size,
            current_rear_arb_blade=current_rear_arb_blade,
            current_front_arb_size=current_front_arb_size,
            current_front_arb_blade=current_front_arb_blade,
        )

        arb = self.car.arb
        csm = self.car.corner_spring

        # Compute spring roll stiffness (same as solve())
        k_springs_rear = self._corner_spring_roll_stiffness(
            rear_wheel_rate_nmm, arb.track_width_rear_mm,
        )
        if getattr(csm, "front_is_roll_spring", False):
            ir = csm.front_roll_spring_installation_ratio
            k_wheel_nm = front_wheel_rate_nmm * 1000.0
            t_half_m = (arb.track_width_front_mm / 2) / 1000.0
            k_springs_front = k_wheel_nm * (ir ** 2) * (t_half_m ** 2) * (math.pi / 180)
        else:
            k_springs_front = self._corner_spring_roll_stiffness(
                front_wheel_rate_nmm, arb.track_width_front_mm,
            )

        target_lltd = base.lltd_target
        farb_size = arb.front_baseline_size
        farb_blade = arb.front_baseline_blade

        # Enumerate all rear size/blade combos within tolerance
        scored: list[tuple[float, str, int]] = []
        for rear_size in arb.rear_size_labels:
            if rear_size.lower() == "disconnected":
                continue
            for blade in range(1, arb.rear_blade_count + 1):
                lltd, _, _, _, _ = self._compute_lltd(
                    farb_size, farb_blade, rear_size, blade,
                    k_springs_front, k_springs_rear,
                )
                err = abs(lltd - target_lltd)
                if err <= lltd_tolerance:
                    scored.append((err, rear_size, blade))

        scored.sort(key=lambda x: x[0])

        # Build solutions
        base_key = (base.rear_arb_size, base.rear_arb_blade_start)
        results = [base]
        seen: set[tuple[str, int]] = {base_key}

        for err, r_size, r_blade in scored:
            if len(results) >= max_candidates:
                break
            key = (r_size, r_blade)
            if key in seen:
                continue
            seen.add(key)
            sol = self.solution_from_explicit_settings(
                front_wheel_rate_nmm=front_wheel_rate_nmm,
                rear_wheel_rate_nmm=rear_wheel_rate_nmm,
                front_arb_size=farb_size,
                front_arb_blade_start=farb_blade,
                rear_arb_size=r_size,
                rear_arb_blade_start=r_blade,
                lltd_offset=lltd_offset,
            )
            results.append(sol)

        return results

    def solution_from_explicit_settings(
        self,
        *,
        front_wheel_rate_nmm: float,
        rear_wheel_rate_nmm: float,
        front_arb_size: str,
        front_arb_blade_start: int,
        rear_arb_size: str,
        rear_arb_blade_start: int,
        lltd_offset: float = 0.0,
        rarb_blade_slow_corner: int | None = None,
        rarb_blade_fast_corner: int | None = None,
        farb_blade_locked: int | None = None,
    ) -> ARBSolution:
        """Build a Step 4 solution from explicit ARB settings."""
        arb = self.car.arb
        csm = self.car.corner_spring
        if getattr(csm, "front_is_roll_spring", False):
            ir = getattr(csm, "front_roll_spring_installation_ratio", 1.0)
            k_wheel_nm = front_wheel_rate_nmm * 1000.0
            t_half_m = (arb.track_width_front_mm / 2) / 1000.0
            k_springs_front = k_wheel_nm * (ir ** 2) * (t_half_m ** 2) * (math.pi / 180)
        else:
            k_springs_front = self._corner_spring_roll_stiffness(
                front_wheel_rate_nmm, arb.track_width_front_mm,
            )
        k_springs_rear = self._corner_spring_roll_stiffness(
            rear_wheel_rate_nmm, arb.track_width_rear_mm,
        )
        # Use measured LLTD target if available (overrides theoretical formula)
        if self.car.measured_lltd_target is not None:
            target_lltd = self.car.measured_lltd_target + lltd_offset
        else:
            target_lltd = optimal_lltd(
                front_weight_dist=self.car.weight_dist_front,
                tyre_sens=self.car.tyre_load_sensitivity,
                pct_above_200kph=self.track.pct_above_200kph,
                car_name=getattr(self.car, "canonical_name", None),
            ) + lltd_offset
        farb_blade = int(farb_blade_locked if farb_blade_locked is not None else front_arb_blade_start)
        rarb_slow_blade = int(rarb_blade_slow_corner if rarb_blade_slow_corner is not None else 1)
        rarb_fast_blade = int(rarb_blade_fast_corner if rarb_blade_fast_corner is not None else min(4, arb.rear_blade_count))
        lltd, k_farb, k_rarb, k_front, k_rear = self._compute_lltd(
            front_arb_size,
            int(front_arb_blade_start),
            rear_arb_size,
            int(rear_arb_blade_start),
            k_springs_front,
            k_springs_rear,
        )
        k_rarb_step_plus = self.car.arb.rear_roll_stiffness(rear_arb_size, min(int(rear_arb_blade_start) + 1, arb.rear_blade_count))
        k_rarb_step_minus = self.car.arb.rear_roll_stiffness(rear_arb_size, max(int(rear_arb_blade_start) - 1, 1))
        lltd_plus = self._lltd_from_roll_stiffness(k_front, k_rear - k_rarb + k_rarb_step_plus)
        lltd_minus = self._lltd_from_roll_stiffness(k_front, k_rear - k_rarb + k_rarb_step_minus)
        sensitivity = (lltd_plus - lltd_minus) / 2
        lltd_min, _, _, _, _ = self._compute_lltd(
            front_arb_size, farb_blade, rear_arb_size, rarb_slow_blade, k_springs_front, k_springs_rear
        )
        lltd_max, _, _, _, _ = self._compute_lltd(
            front_arb_size, farb_blade, rear_arb_size, rarb_fast_blade, k_springs_front, k_springs_rear
        )
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
        notes = [
            "Explicit ARB materialization preserves the selected bar/blade family and recomputes LLTD.",
            f"Front ARB {front_arb_size}/{int(front_arb_blade_start)}, rear ARB {rear_arb_size}/{int(rear_arb_blade_start)}.",
        ]
        pss = {
            "front_arb_size": "user_set",
            "front_arb_blade": "user_set",
            "rear_arb_size": "user_set",
            "rear_arb_blade": "user_set",
        }
        return ARBSolution(
            front_arb_size=front_arb_size,
            front_arb_blade_start=int(front_arb_blade_start),
            rear_arb_size=rear_arb_size,
            rear_arb_blade_start=int(rear_arb_blade_start),
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
            parameter_search_status=pss,
        )
