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


def _live_rarb_blade_targets(arb) -> tuple[int, int]:
    """Recommended slow-corner / fast-corner rear ARB blades for live tuning.

    Slow corners want softer-than-baseline (max rotation, no aero help).
    Fast corners want stiffer-than-baseline (front bite via load transfer).

    Legacy GTP behavior (5-blade cars: BMW/Cadillac/Acura/Ferrari) is preserved
    exactly with `slow=1, fast=4` so existing setups don't shift. Wide-range
    cars (Porsche 963: 1–16 blades, baseline 6) get baseline-relative spreads
    instead — the historical `min(4, count)` cap put Porsche fast-corner blade
    *softer* than baseline, exactly the opposite of intent.
    """
    count = arb.rear_blade_count
    baseline = arb.rear_baseline_blade
    if count <= 6:
        return 1, min(4, count)
    softer_range = baseline - 1
    stiffer_range = count - baseline
    slow = max(1, baseline - max(2, softer_range // 2))
    fast = min(count, baseline + max(2, stiffer_range // 3))
    return slow, fast


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

    # W2.4 / Audit A-3..A-6: live RARB SIZE-LABEL range — populated when the
    # car's ARB encoding collapses the blade dimension (rear_blade_count <= 1)
    # and the size_label IS the live tuning variable. None for legacy GTP cars
    # that tune via blade count.
    rarb_size_slow_corner: str | None = None
    rarb_size_fast_corner: str | None = None

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
        ]
        if self.rarb_size_slow_corner is not None and self.rarb_size_fast_corner is not None:
            # GT3 / paired-blade encoding: the size LABEL is the tuning unit; the
            # blade dimension is collapsed.
            lines += [
                f"    RARB sensitivity:       {self.rarb_sensitivity_per_blade:+.1%} LLTD per size step",
                f"    Slow corners (<80kph):  size {self.rarb_size_slow_corner}  "
                f"(LLTD {self.lltd_at_rarb_min:.1%}  — soft for rotation)",
                f"    Fast corners (>2.5g):   size {self.rarb_size_fast_corner}  "
                f"(LLTD {self.lltd_at_rarb_max:.1%}  — stiff for front bite)",
                f"    FARB size:              {self.front_arb_size}  (keep here — rear size is the variable)",
            ]
        else:
            lines += [
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

    # W2.4 / Audit A-7: extract the front-roll-stiffness branch into a single
    # helper so all three call sites (solve, solve_candidates,
    # solution_from_explicit_settings) dispatch on the same logic. This is the
    # natural extension point for a future fourth arm (e.g. asymmetric track-
    # width or a different paired-coil installation ratio).
    def _front_spring_roll_stiffness(self, front_wheel_rate_nmm: float) -> float:
        """Front-axle spring roll stiffness contribution (N·m/deg).

        Dispatches on the corner-spring architecture:
        - Porsche-GTP roll-spring: single spring with installation ratio.
            K = k * IR^2 * (t_half)^2 (no factor of 2)
        - Conventional paired (BMW/Ferrari/GT3 paired coils): two corner
          springs in roll, classic K_roll = 2 * k_wheel * (t_half)^2.
        """
        arb = self.car.arb
        csm = self.car.corner_spring
        if getattr(csm, "front_is_roll_spring", False):
            ir = getattr(csm, "front_roll_spring_installation_ratio", 1.0)
            k_wheel_nm = front_wheel_rate_nmm * 1000.0
            t_half_m = (arb.track_width_front_mm / 2) / 1000.0
            return k_wheel_nm * (ir ** 2) * (t_half_m ** 2) * (math.pi / 180)
        return self._corner_spring_roll_stiffness(
            front_wheel_rate_nmm, arb.track_width_front_mm,
        )

    # W2.4 / Audit A-3..A-6: when a car's ARB encoding collapses the blade
    # dimension (rear_blade_count == 1, e.g. all 3 GT3 stubs), the size_label
    # IS the live tuning variable. Iterate blades only when there's a real
    # blade range; otherwise yield a single fixed blade=1.
    @staticmethod
    def _iter_blade_options(blade_count: int):
        if blade_count <= 1:
            return [1]
        return list(range(1, blade_count + 1))

    @staticmethod
    def _neighbor_size(labels: list[str], chosen: str, delta: int,
                       arb_direction: str = "ascending") -> str:
        """Return the size label `delta` steps stiffer (positive) or softer
        (negative) than `chosen`. Direction is reversed for descending ARB
        encodings (e.g. Corvette where 0=stiff → 6=soft).

        Skips the "Disconnected" sentinel if it's at index 0.
        """
        if chosen not in labels:
            return chosen
        idx = labels.index(chosen)
        # Determine valid index range — skip a leading "Disconnected" entry.
        lo_idx = 0
        if labels and labels[0].lower() == "disconnected":
            lo_idx = 1
        hi_idx = len(labels) - 1
        if arb_direction == "descending":
            delta = -delta
        new_idx = max(lo_idx, min(hi_idx, idx + delta))
        return labels[new_idx]

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

        # W2.4 / Audit A-2: loud-fail safety net. Step 3 must produce a non-zero
        # front wheel rate. If a regression (e.g. corner_spring_solver dispatch
        # mis-routes GT3 paired coils through a torsion-bar branch and emits 0)
        # silently feeds in 0 here, LLTD becomes 0 / (0 + k_rear) = 0 and the
        # rear ARB search snaps to its softest config. Fail loudly instead.
        if not (front_wheel_rate_nmm > 0):
            raise ValueError(
                f"ARB Step 4 received zero/negative front wheel rate "
                f"({front_wheel_rate_nmm}) — Step 3 produced a null front coil. "
                f"Check corner_spring_solver dispatch (suspension_arch="
                f"{getattr(self.car.suspension_arch, 'name', '?')})."
            )

        # Roll stiffness from springs
        # Rear: always paired corner springs → K = 2 * k_wheel * (t/2)²
        k_springs_rear = self._corner_spring_roll_stiffness(
            rear_wheel_rate_nmm, arb.track_width_rear_mm,
        )
        # Front: dispatched via the helper (W2.4 / A-7 — single source of truth
        # for roll-spring vs paired-coil branching).
        k_springs_front = self._front_spring_roll_stiffness(front_wheel_rate_nmm)

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
        if self.car.measured_lltd_target is not None:
            target_lltd = self.car.measured_lltd_target + lltd_offset
        else:
            # Theoretical formula (physics-based from tyre load sensitivity + track speed).
            # Uses OptimumG/Milliken "Magic Number" baseline: LLTD ≈ weight_dist + 5%.
            logger.info("LLTD target: using physics formula (measured_lltd_target not set for %s)",
                        getattr(self.car, 'canonical_name', 'unknown'))
            tyre_sens = self.car.tyre_load_sensitivity
            pct_hs = self.track.pct_above_200kph
            hs_correction = 0.01 * pct_hs  # up to +1% at 100% high-speed track
            lltd_physics_offset = (tyre_sens / 0.20) * (0.05 + hs_correction)
            target_lltd = self.car.weight_dist_front + lltd_physics_offset + lltd_offset

        # Bounds-check: LLTD must be in a physically reasonable range
        if target_lltd < 0.30 or target_lltd > 0.75:
            logger.warning(
                "LLTD target %.3f is outside [0.30, 0.75] — clamping "
                "(lltd_offset=%.3f may be too extreme)",
                target_lltd, lltd_offset,
            )
            target_lltd = max(0.30, min(0.75, target_lltd))

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

        # W2.4 / Audit A-3..A-6: enumerate the size labels as the primary search
        # axis; iterate blades only when the car's ARB encoding has a real blade
        # range (rear_blade_count > 1, e.g. BMW GTP 5 blades). For GT3 cars the
        # blade dimension collapses (rear_blade_count == 1) and the size_label
        # IS the live tuning unit.
        blade_options = self._iter_blade_options(arb.rear_blade_count)

        # Prefer current setup's ARB size — only change size if no blade within
        # the current size achieves an acceptable LLTD (within 0.015 = 1.5%).
        # Changing ARB bar size has massive feel implications and shouldn't be
        # done casually; blade changes within a size are the expected tuning range.
        preferred_size = current_rear_arb_size or arb.rear_baseline_size
        preferred_best_blade = arb.rear_baseline_blade
        preferred_best_error = float("inf")
        if preferred_size in arb.rear_size_labels and preferred_size.lower() != "disconnected":
            for blade in blade_options:
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
                for blade in blade_options:
                    lltd, _, _, _, _ = self._compute_lltd(
                        farb_size, farb_blade, rear_size, blade,
                        k_springs_front, k_springs_rear
                    )
                    err = abs(lltd - target_lltd)
                    if err < best_lltd_error:
                        best_lltd_error = err
                        best_size = rear_size
                        best_blade = blade

            # ── Per Unit F2: NO driver-anchor escape hatch ──
            # The previous "if best_lltd_error > 0.03 → anchor to driver"
            # branch was a Type-B/F preserve-driver fallback. It silently
            # masked physics signals: when the OptimumG LLTD target
            # disagreed with the model's k_front/k_total reading by more
            # than 3 pp, the solver gave up and copied the driver's loaded
            # ARB. F2 retracts this — when no ARB combo achieves target
            # within 3 pp, we still emit the **closest physics solution**
            # and label it `physics_search_no_target_match` so the report
            # surfaces the gap honestly. The caller (Step 4 audit logic)
            # can flag this for the LLTD epistemic gap (see CLAUDE.md
            # "LLTD CALIBRATION GAP"). Preserve-driver is reserved for
            # the case where physics cannot run at all (no spring rates,
            # no roll stiffness inputs).

        # Compute full solution at chosen ARB setup
        lltd, k_farb, k_rarb, k_front, k_rear = self._compute_lltd(
            farb_size, farb_blade, best_size, best_blade,
            k_springs_front, k_springs_rear
        )

        # W2.4 / Audit A-3..A-6: live RARB tuning dispatches on encoding.
        # - GTP cars (rear_blade_count > 1): walk the BLADE within the chosen size.
        # - GT3 / collapsed-blade cars (rear_blade_count == 1): walk the SIZE LABEL
        #   index ±1 (sensitivity) and ±2 (slow/fast) from the chosen size.
        rarb_size_slow: str | None = None
        rarb_size_fast: str | None = None
        if arb.rear_blade_count > 1:
            # GTP path — blade-based live tuning (legacy behavior)
            k_rarb_step_plus = self.car.arb.rear_roll_stiffness(
                best_size, min(best_blade + 1, arb.rear_blade_count)
            )
            k_rarb_step_minus = self.car.arb.rear_roll_stiffness(
                best_size, max(best_blade - 1, 1)
            )
            lltd_plus = self._lltd_from_roll_stiffness(
                k_front, k_rear - k_rarb + k_rarb_step_plus
            )
            lltd_minus = self._lltd_from_roll_stiffness(
                k_front, k_rear - k_rarb + k_rarb_step_minus
            )
            sensitivity = (lltd_plus - lltd_minus) / 2

            rarb_slow_blade, rarb_fast_blade = _live_rarb_blade_targets(arb)

            lltd_min, _, _, _, _ = self._compute_lltd(
                farb_size, farb_blade, best_size, rarb_slow_blade,
                k_springs_front, k_springs_rear
            )
            lltd_max, _, _, _, _ = self._compute_lltd(
                farb_size, farb_blade, best_size, rarb_fast_blade,
                k_springs_front, k_springs_rear
            )
        else:
            # GT3 / collapsed-blade path — size-label live tuning. The blade
            # value stays at 1; slow/fast walk the size-label index instead.
            rarb_slow_blade = 1
            rarb_fast_blade = 1

            arb_dir = getattr(arb, "arb_direction", "ascending")
            # Sensitivity: one step stiffer minus one step softer, in label space.
            stiffer_one = self._neighbor_size(
                arb.rear_size_labels, best_size, +1, arb_dir
            )
            softer_one = self._neighbor_size(
                arb.rear_size_labels, best_size, -1, arb_dir
            )
            k_rarb_plus = self.car.arb.rear_roll_stiffness(stiffer_one, 1)
            k_rarb_minus = self.car.arb.rear_roll_stiffness(softer_one, 1)
            lltd_plus = self._lltd_from_roll_stiffness(
                k_front, k_rear - k_rarb + k_rarb_plus
            )
            lltd_minus = self._lltd_from_roll_stiffness(
                k_front, k_rear - k_rarb + k_rarb_minus
            )
            sensitivity = (lltd_plus - lltd_minus) / 2

            # Live slow/fast: walk ±2 size-label steps from the chosen size.
            rarb_size_slow = self._neighbor_size(
                arb.rear_size_labels, best_size, -2, arb_dir
            )
            rarb_size_fast = self._neighbor_size(
                arb.rear_size_labels, best_size, +2, arb_dir
            )
            lltd_min, _, _, _, _ = self._compute_lltd(
                farb_size, farb_blade, rarb_size_slow, 1,
                k_springs_front, k_springs_rear
            )
            lltd_max, _, _, _, _ = self._compute_lltd(
                farb_size, farb_blade, rarb_size_fast, 1,
                k_springs_front, k_springs_rear
            )

        # Constraint checks. The "covers slow-corner" check now passes when
        # either a blade range exists (legacy GTP) OR a slow size label exists
        # (GT3 size-label live tuning).
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
                name="RARB range covers slow-corner setting",
                passed=(rarb_slow_blade >= 1) or (rarb_size_slow is not None),
                value=float(rarb_slow_blade),
                target=1.0,
                units="blade" if rarb_size_slow is None else "size_label",
            ),
            ARBConstraintCheck(
                name="RARB sensitivity within useful range",
                # GT3 ARB stiffness tables are uncalibrated stubs (PENDING_IBT,
                # all zeros) so sensitivity will be 0 until ARB stiffness data
                # lands. Skip the lower bound when ARB is uncalibrated.
                passed=(
                    abs(sensitivity) < 0.05
                    if not getattr(arb, "is_calibrated", False)
                    else 0.005 < abs(sensitivity) < 0.05
                ),
                value=abs(sensitivity),
                target=0.02,
                units="LLTD/blade" if rarb_size_slow is None else "LLTD/size_step",
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
        elif rarb_size_slow is not None and rarb_size_fast is not None:
            # W2.4 / Audit A-8: generic GT3 / collapsed-blade ARB note. The
            # size_label is the live tuning unit; the blade dimension is a
            # no-op for these ARB encodings.
            notes = [
                f"{self.car.name}: Front ARB '{farb_size}', Rear ARB '{best_size}' "
                "(blade dimension collapsed — size label is the live tuning unit).",
                f"Live RARB walk: slow corners -> '{rarb_size_slow}' (softer for rotation), "
                f"fast corners -> '{rarb_size_fast}' (stiffer for front bite via LLTD shift).",
                "Cold tyre out-lap: softer rear ARB size to prevent snap oversteer.",
                "NOTE: GT3 ARB stiffness tables are PENDING_IBT — sensitivity readings "
                "will be 0 until per-size stiffness is calibrated.",
            ]
        else:
            notes = [
                f"{self.car.name}: Front ARB '{farb_size}'/blade {farb_blade}, "
                f"Rear ARB '{best_size}'/blade {best_blade}.",
                "Stiffer RARB -> more load transfer to rear -> front gains grip via LLTD. "
                "Use RARB as the primary live balance variable.",
                "Cold tyre out-lap: softer RARB to prevent snap oversteer.",
            ]

        # F2 parameter_search_status: physics-search result, with an
        # honest label when the model couldn't reach the LLTD target
        # within tolerance.
        if best_lltd_error <= 0.015:
            _rear_arb_status = "physics_search"
        elif best_lltd_error <= 0.03:
            _rear_arb_status = "physics_search_low_confidence"
        else:
            # Honest label: physics found the closest combo but couldn't
            # reach target. The LLTD calibration target may itself be a
            # geometric proxy (see CLAUDE.md "LLTD epistemic gap"); we
            # surface the gap rather than masking it with a driver anchor.
            _rear_arb_status = "physics_search_no_target_match"
        pss = {
            "front_arb_size": "physics_baseline",
            "front_arb_blade": "physics_baseline",
            "rear_arb_size": _rear_arb_status,
            "rear_arb_blade": _rear_arb_status,
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
            rarb_size_slow_corner=rarb_size_slow,
            rarb_size_fast_corner=rarb_size_fast,
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

        # Compute spring roll stiffness (same as solve()) — via the W2.4 helper
        # so the front-architecture branching has a single source of truth.
        k_springs_rear = self._corner_spring_roll_stiffness(
            rear_wheel_rate_nmm, arb.track_width_rear_mm,
        )
        k_springs_front = self._front_spring_roll_stiffness(front_wheel_rate_nmm)

        target_lltd = base.lltd_target
        farb_size = arb.front_baseline_size
        farb_blade = arb.front_baseline_blade

        # W2.4 / Audit A-3..A-6: enumerate the size labels as the primary axis;
        # iterate blades only when the encoding has a real blade range.
        blade_options = self._iter_blade_options(arb.rear_blade_count)

        # Enumerate all rear size/blade combos within tolerance
        scored: list[tuple[float, str, int]] = []
        for rear_size in arb.rear_size_labels:
            if rear_size.lower() == "disconnected":
                continue
            for blade in blade_options:
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

        # W2.4 / Audit A-7: front roll-stiffness via the helper (single source
        # of truth for roll-spring vs paired-coil branching).
        k_springs_front = self._front_spring_roll_stiffness(front_wheel_rate_nmm)
        k_springs_rear = self._corner_spring_roll_stiffness(
            rear_wheel_rate_nmm, arb.track_width_rear_mm,
        )
        # Use measured LLTD target if available (overrides theoretical formula)
        if self.car.measured_lltd_target is not None:
            target_lltd = self.car.measured_lltd_target + lltd_offset
        else:
            # Theoretical formula (physics-based from tyre load sensitivity + track speed)
            tyre_sens = self.car.tyre_load_sensitivity
            pct_hs = self.track.pct_above_200kph
            hs_correction = 0.01 * pct_hs
            lltd_physics_offset = (tyre_sens / 0.20) * (0.05 + hs_correction)
            target_lltd = self.car.weight_dist_front + lltd_physics_offset + lltd_offset

        farb_blade = int(farb_blade_locked if farb_blade_locked is not None else front_arb_blade_start)

        # W2.4 / Audit A-3..A-6: blade vs size live-tuning dispatch. Mirrors
        # solve(): if rear_blade_count > 1 the legacy GTP blade walk applies;
        # otherwise the size-label walk applies.
        rarb_size_slow_corner: str | None = None
        rarb_size_fast_corner: str | None = None
        if arb.rear_blade_count > 1:
            _slow_default, _fast_default = _live_rarb_blade_targets(arb)
            rarb_slow_blade = int(rarb_blade_slow_corner if rarb_blade_slow_corner is not None else _slow_default)
            rarb_fast_blade = int(rarb_blade_fast_corner if rarb_blade_fast_corner is not None else _fast_default)
        else:
            rarb_slow_blade = 1
            rarb_fast_blade = 1
            arb_dir = getattr(arb, "arb_direction", "ascending")
            rarb_size_slow_corner = self._neighbor_size(
                arb.rear_size_labels, rear_arb_size, -2, arb_dir
            )
            rarb_size_fast_corner = self._neighbor_size(
                arb.rear_size_labels, rear_arb_size, +2, arb_dir
            )

        lltd, k_farb, k_rarb, k_front, k_rear = self._compute_lltd(
            front_arb_size,
            int(front_arb_blade_start),
            rear_arb_size,
            int(rear_arb_blade_start),
            k_springs_front,
            k_springs_rear,
        )

        if arb.rear_blade_count > 1:
            k_rarb_step_plus = self.car.arb.rear_roll_stiffness(
                rear_arb_size, min(int(rear_arb_blade_start) + 1, arb.rear_blade_count)
            )
            k_rarb_step_minus = self.car.arb.rear_roll_stiffness(
                rear_arb_size, max(int(rear_arb_blade_start) - 1, 1)
            )
        else:
            arb_dir = getattr(arb, "arb_direction", "ascending")
            stiffer = self._neighbor_size(
                arb.rear_size_labels, rear_arb_size, +1, arb_dir
            )
            softer = self._neighbor_size(
                arb.rear_size_labels, rear_arb_size, -1, arb_dir
            )
            k_rarb_step_plus = self.car.arb.rear_roll_stiffness(stiffer, 1)
            k_rarb_step_minus = self.car.arb.rear_roll_stiffness(softer, 1)
        lltd_plus = self._lltd_from_roll_stiffness(k_front, k_rear - k_rarb + k_rarb_step_plus)
        lltd_minus = self._lltd_from_roll_stiffness(k_front, k_rear - k_rarb + k_rarb_step_minus)
        sensitivity = (lltd_plus - lltd_minus) / 2

        if rarb_size_slow_corner is not None and rarb_size_fast_corner is not None:
            lltd_min, _, _, _, _ = self._compute_lltd(
                front_arb_size, farb_blade, rarb_size_slow_corner, 1,
                k_springs_front, k_springs_rear
            )
            lltd_max, _, _, _, _ = self._compute_lltd(
                front_arb_size, farb_blade, rarb_size_fast_corner, 1,
                k_springs_front, k_springs_rear
            )
        else:
            lltd_min, _, _, _, _ = self._compute_lltd(
                front_arb_size, farb_blade, rear_arb_size, rarb_slow_blade,
                k_springs_front, k_springs_rear
            )
            lltd_max, _, _, _, _ = self._compute_lltd(
                front_arb_size, farb_blade, rear_arb_size, rarb_fast_blade,
                k_springs_front, k_springs_rear
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
                name="RARB range covers slow-corner setting",
                passed=(rarb_slow_blade >= 1) or (rarb_size_slow_corner is not None),
                value=float(rarb_slow_blade),
                target=1.0,
                units="blade" if rarb_size_slow_corner is None else "size_label",
            ),
            ARBConstraintCheck(
                name="RARB sensitivity within useful range",
                passed=(
                    abs(sensitivity) < 0.05
                    if not getattr(arb, "is_calibrated", False)
                    else 0.005 < abs(sensitivity) < 0.05
                ),
                value=abs(sensitivity),
                target=0.02,
                units="LLTD/blade" if rarb_size_slow_corner is None else "LLTD/size_step",
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
            rarb_size_slow_corner=rarb_size_slow_corner,
            rarb_size_fast_corner=rarb_size_fast_corner,
        )
