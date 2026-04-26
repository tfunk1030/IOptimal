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
from track_model.profile import TrackProfile


# ─── Tunable constants (single source of truth) ─────────────────────────────
# OptimumG/Milliken "Magic Number" baseline LLTD = static_front + 0.05 at λ=0.20.
LLTD_BASELINE_OFFSET = 0.05
LLTD_TYRE_SENS_REFERENCE = 0.20
# Speed-correction span: up to +1 pp at 100% high-speed track.
LLTD_HS_CORRECTION_MAX = 0.01
# Physically reasonable LLTD bounds (front share of lateral load transfer).
LLTD_MIN = 0.30
LLTD_MAX = 0.75
# Search/anchor tolerances.
LLTD_PREFER_SAME_SIZE_GATE = 0.015   # If preferred bar can hit target within 1.5pp, keep it.
LLTD_DRIVER_ANCHOR_GATE = 0.03       # Best searched setup >3pp off → fall back to driver setup.
LLTD_CONSTRAINT_PASS_GATE = 0.05     # Constraint reports PASS within 5pp.
# RARB sensitivity (LLTD change per blade step) — useful operating range.
RARB_SENS_MIN = 0.005
RARB_SENS_MAX = 0.05


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

    def _spring_roll_stiffness_pair(
        self,
        front_wheel_rate_nmm: float,
        rear_wheel_rate_nmm: float,
    ) -> tuple[float, float]:
        """Roll stiffness contribution from springs for both axles (N·m/deg).

        Picks the correct front-axle formula based on whether the car has a
        single front roll spring (Multimatic/Porsche) or paired corner springs
        (BMW/Ferrari/Acura/etc).

        Both inputs are WHEEL rates (per Spring rate conventions in CLAUDE.md).
        """
        arb = self.car.arb
        csm = self.car.corner_spring
        k_springs_rear = self._corner_spring_roll_stiffness(
            rear_wheel_rate_nmm, arb.track_width_rear_mm,
        )
        if csm.front_is_roll_spring:
            # Multimatic (Porsche): single roll spring, not a pair.
            # K_roll = k * IR² * (t/2)²  (no factor of 2)
            ir = csm.front_roll_spring_installation_ratio
            k_wheel_nm = front_wheel_rate_nmm * 1000.0
            t_half_m = (arb.track_width_front_mm / 2) / 1000.0
            k_springs_front = k_wheel_nm * (ir ** 2) * (t_half_m ** 2) * (math.pi / 180)
        else:
            k_springs_front = self._corner_spring_roll_stiffness(
                front_wheel_rate_nmm, arb.track_width_front_mm,
            )
        return k_springs_front, k_springs_rear

    def _resolve_target_lltd(self, lltd_offset: float) -> float:
        """Resolve LLTD target with bounds-check.

        Prefers the car's hand-calibrated `measured_lltd_target` when set;
        otherwise falls back to the OptimumG/Milliken physics formula:
            LLTD ≈ weight_dist_front + (tyre_sens / 0.20) * (0.05 + hs_correction)

        Result is clamped to [LLTD_MIN, LLTD_MAX] (a warning is logged if the
        raw value lands outside; usually means lltd_offset is too extreme).
        """
        if self.car.measured_lltd_target is not None:
            target = self.car.measured_lltd_target + lltd_offset
        else:
            logger.info(
                "LLTD target: using physics formula (measured_lltd_target not set for %s)",
                getattr(self.car, "canonical_name", "unknown"),
            )
            tyre_sens = self.car.tyre_load_sensitivity
            hs_correction = LLTD_HS_CORRECTION_MAX * self.track.pct_above_200kph
            physics_offset = (tyre_sens / LLTD_TYRE_SENS_REFERENCE) * (
                LLTD_BASELINE_OFFSET + hs_correction
            )
            target = self.car.weight_dist_front + physics_offset + lltd_offset

        if target < LLTD_MIN or target > LLTD_MAX:
            logger.warning(
                "LLTD target %.3f is outside [%.2f, %.2f] — clamping "
                "(lltd_offset=%.3f may be too extreme)",
                target, LLTD_MIN, LLTD_MAX, lltd_offset,
            )
            target = max(LLTD_MIN, min(LLTD_MAX, target))
        return target

    def _build_constraints(
        self,
        lltd: float,
        target_lltd: float,
        rarb_slow_blade: int,
        sensitivity: float,
    ) -> list[ARBConstraintCheck]:
        """Standard ARB constraint set (LLTD target, slow-blade, RARB sensitivity)."""
        return [
            ARBConstraintCheck(
                name="LLTD target",
                passed=abs(lltd - target_lltd) < LLTD_CONSTRAINT_PASS_GATE,
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
                passed=RARB_SENS_MIN < abs(sensitivity) < RARB_SENS_MAX,
                value=abs(sensitivity),
                target=0.02,
                units="LLTD/blade",
                note="<0.005 = insensitive (wrong bar size), >0.05 = too sensitive (step down)",
            ),
        ]

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

        # Front ARB anchor params accepted for API symmetry but not used —
        # FARB is pinned soft (per BMW/Multimatic strategy). Warn if caller
        # supplies them so they aren't silently dropped.
        if current_front_arb_size is not None or current_front_arb_blade is not None:
            logger.debug(
                "Front-ARB driver anchor (size=%s, blade=%s) accepted but not honored — "
                "FARB is pinned at baseline by design.",
                current_front_arb_size, current_front_arb_blade,
            )

        # Roll stiffness from springs (architecture-aware).
        k_springs_front, k_springs_rear = self._spring_roll_stiffness_pair(
            front_wheel_rate_nmm, rear_wheel_rate_nmm,
        )

        # Target LLTD — measured value if available, otherwise OptimumG/Milliken physics:
        #   LLTD ≈ weight_dist_front + (tyre_sens/0.20) * (0.05 + hs_correction)
        # where hs_correction (up to +1pp at 100% high-speed track) accounts for the
        # downforce-induced rearward weight shift (Milliken RCVD Ch.18 + Ron Sutton).
        # See LLTD epistemic gap in CLAUDE.md — without wheel-force telemetry we can't
        # ground-truth this; the driver-anchor fallback below handles known mis-cal.
        target_lltd = self._resolve_target_lltd(lltd_offset)

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
        # the current size achieves an acceptable LLTD (within LLTD_PREFER_SAME_SIZE_GATE).
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

        anchored_to_driver = False
        if preferred_best_error < LLTD_PREFER_SAME_SIZE_GATE:
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

            # ── Driver anchor fallback (per CLAUDE.md Principle 11) ──
            # If even the best searched setup is far from target (>LLTD_DRIVER_ANCHOR_GATE),
            # the LLTD physics model is mis-calibrated for this car/track and the
            # IBT-validated driver setup is more reliable than any model-derived guess.
            # See "LLTD epistemic gap" in CLAUDE.md Known Limitations: without wheel-force
            # telemetry we cannot ground-truth k_front/k_total. Validated 2026-04-07
            # against Porsche/Algarve (model says driver Stiff/10 gives LLTD=0.391 vs
            # OptimumG target 0.521 — 13pp gap is real but un-attributable).
            if (best_lltd_error > LLTD_DRIVER_ANCHOR_GATE
                    and current_rear_arb_size is not None
                    and current_rear_arb_blade is not None
                    and current_rear_arb_size in arb.rear_size_labels
                    and current_rear_arb_size.lower() != "disconnected"
                    and 1 <= int(current_rear_arb_blade) <= arb.rear_blade_count):
                best_size = current_rear_arb_size
                best_blade = int(current_rear_arb_blade)
                anchored_to_driver = True
                # Recompute the model's LLTD at the anchored setup so the gap is
                # visible in step4 output (honest provenance, not hidden).
                lltd_anchor, _, _, _, _ = self._compute_lltd(
                    farb_size, farb_blade, best_size, best_blade,
                    k_springs_front, k_springs_rear
                )
                best_lltd_error = abs(lltd_anchor - target_lltd)

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
        constraints = self._build_constraints(lltd, target_lltd, rarb_slow_blade, sensitivity)

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
                f"{self.car.canonical_name}: Front ARB '{farb_size}'/blade {farb_blade}, "
                f"Rear ARB '{best_size}'/blade {best_blade}.",
                "Stiffer RARB -> more load transfer to rear -> front gains grip via LLTD. "
                "Use RARB as the primary live balance variable.",
                "Cold tyre out-lap: softer RARB to prevent snap oversteer.",
            ]

        # Honest provenance: surface driver-anchor when it fired (Principle 11).
        if anchored_to_driver:
            notes.insert(
                0,
                f"Rear ARB anchored to driver-loaded {best_size}/blade {best_blade} — "
                f"model LLTD={lltd:.1%} vs target {target_lltd:.1%} "
                f"({best_lltd_error:.1%} gap). Physics target unverifiable without "
                "wheel-force telemetry; deferring to IBT-validated driver setup.",
            )

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

        # Compute spring roll stiffness (architecture-aware, same as solve()).
        k_springs_front, k_springs_rear = self._spring_roll_stiffness_pair(
            front_wheel_rate_nmm, rear_wheel_rate_nmm,
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
        k_springs_front, k_springs_rear = self._spring_roll_stiffness_pair(
            front_wheel_rate_nmm, rear_wheel_rate_nmm,
        )
        target_lltd = self._resolve_target_lltd(lltd_offset)
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
        constraints = self._build_constraints(lltd, target_lltd, rarb_slow_blade, sensitivity)
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
