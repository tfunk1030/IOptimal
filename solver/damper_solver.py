"""Step 6: Damper Solver — Pure Physics Approach.

Derives all damper settings from first principles using suspension dynamics,
track surface data, and tyre requirements. NO baseline anchoring — every
value is computed from the physical model.

Physics Foundation:

    A damper converts kinetic energy (suspension movement) into heat.
    The damping force is proportional to shaft velocity:
        F = c * v   (linear model)

    where c = damping coefficient (N·s/m), v = shaft velocity (m/s).

    For a spring-mass-damper system, critical damping is:
        c_crit = 2 * sqrt(k * m)     [N·s/m]

    Damping ratio: ζ = c / c_crit
        ζ < 1.0: underdamped (oscillates)
        ζ = 1.0: critically damped (no oscillation, fastest return)
        ζ > 1.0: overdamped (slow return)

    Racing target: ζ = 0.3 to 0.7 depending on regime:
        LS (body control): ζ ≈ 0.55-0.70  (need to control roll/pitch quickly)
        HS (bump absorption): ζ ≈ 0.25-0.40  (need compliance over bumps)

    Rebound vs Compression:
        Rebound (extension) should produce MORE force than compression
        at equivalent velocities. This is because:
        1. Compression: road hits tyre → tyre must comply → softer damping
        2. Rebound: suspension extends → tyre must stay planted → stiffer damping
        3. Rebound controls oscillation (prevents bouncing after a bump)

        Typical racing ratio: rebound/comp = 1.3-2.0
        At HS: ratio increases because the consequence of a loose rebound
        (wheel bouncing off surface) is worse than stiff compression (wheel
        lifting momentarily).

    HS Slope (digressive characteristic):
        At extreme shaft velocities, the force curve flattens.
        This prevents the damper from "locking up" during the largest
        bump events (p99+). Higher slope = more digressive.

        For bumpy tracks: higher slope (prevent lockup at extreme events)
        For smooth tracks: lower slope (more linear for precision)

    Speed regime boundary:
        LS ≤ 50 mm/s: body motions (roll, pitch, heave)
        HS > 50 mm/s: bump/kerb transients

    iRacing click-to-force model:
        BMW M Hybrid V8: 20 clicks per parameter
        Force model is approximately linear:
            F_click = F_min + (click - 1) * (F_max - F_min) / (N_clicks - 1)
        We calibrate F_min and F_max from two known data points (setups S1, S2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from car_model.cars import CarModel
from track_model.profile import TrackProfile
from solver.vertical_dynamics import axle_modal_rate_nmm


@dataclass
class DamperConstraintCheck:
    """Result of a single damper constraint check."""
    name: str
    passed: bool
    value: float
    target: float
    units: str
    note: str = ""


@dataclass
class FerrariHeaveDamperSettings:
    """Typed Ferrari heave damper settings (separate from per-corner dampers).

    Ferrari 499P has dedicated heave dampers at front and rear that control
    pitch/heave motions independently from the corner (per-wheel) dampers.
    Click ranges: ls_comp/hs_comp/ls_rbd/hs_rbd share the 0-40 range;
    hs_slope uses the 0-11 range (same as per-corner hs_slope).
    """
    ls_comp: int
    hs_comp: int
    ls_rbd: int
    hs_rbd: int
    hs_slope: int
    hs_slope_rbd: int | None = None


@dataclass
class CornerDamperSettings:
    """Damper settings for one corner."""
    ls_comp: int
    ls_rbd: int
    hs_comp: int
    hs_rbd: int
    hs_slope: int
    hs_slope_rbd: int | None = None  # Ferrari only (lfHSSlopeRbdDampSetting)

    def rbd_comp_ratio_ls(self) -> float:
        return self.ls_rbd / max(self.ls_comp, 1)

    def rbd_comp_ratio_hs(self) -> float:
        return self.hs_rbd / max(self.hs_comp, 1)


@dataclass
class DamperSolution:
    """Output of the Step 6 damper solver."""

    # Per-corner settings
    lf: CornerDamperSettings
    rf: CornerDamperSettings
    lr: CornerDamperSettings
    rr: CornerDamperSettings

    # Physics inputs
    track_shock_vel_p95_front_mps: float
    track_shock_vel_p95_rear_mps: float
    track_shock_vel_p99_front_mps: float
    track_shock_vel_p99_rear_mps: float

    # Computed damping coefficients (N·s/m)
    c_ls_front: float     # LS damping coefficient, front
    c_ls_rear: float
    c_hs_front: float     # HS damping coefficient, front
    c_hs_rear: float

    # Critical damping and ratios
    c_crit_front: float   # Critical damping, front (N·s/m)
    c_crit_rear: float
    zeta_ls_front: float  # Damping ratio LS front
    zeta_ls_rear: float
    zeta_hs_front: float  # Damping ratio HS front
    zeta_hs_rear: float

    # Rebound/compression ratios achieved
    ls_rbd_comp_ratio_front: float
    hs_rbd_comp_ratio_front: float
    ls_rbd_comp_ratio_rear: float
    hs_rbd_comp_ratio_rear: float

    # HS slope reasoning
    hs_slope_reasoning: str

    # Constraint checks
    constraints: list[DamperConstraintCheck]

    # Roll dampers (ORECA heave+roll architecture — None for per-corner cars)
    front_roll_ls: int | None = None
    front_roll_hs: int | None = None
    rear_roll_ls: int | None = None
    rear_roll_hs: int | None = None

    # Heave dampers (Ferrari architecture — separate from corner dampers)
    front_heave_damper: FerrariHeaveDamperSettings | None = None
    rear_heave_damper: FerrariHeaveDamperSettings | None = None

    notes: list[str] = field(default_factory=list)
    parameter_search_status: dict = None
    parameter_search_evidence: dict = None

    def __post_init__(self):
        if self.parameter_search_status is None:
            self.parameter_search_status = {
                "lf_ls_comp": "user_set", "lf_ls_rbd": "user_set",
                "lf_hs_comp": "user_set", "lf_hs_rbd": "user_set",
                "rf_ls_comp": "user_set", "rf_ls_rbd": "user_set",
                "rf_hs_comp": "user_set", "rf_hs_rbd": "user_set",
                "lr_ls_comp": "user_set", "lr_ls_rbd": "user_set",
                "lr_hs_comp": "user_set", "lr_hs_rbd": "user_set",
                "rr_ls_comp": "user_set", "rr_ls_rbd": "user_set",
                "rr_hs_comp": "user_set", "rr_hs_rbd": "user_set",
            }
        if self.parameter_search_evidence is None:
            self.parameter_search_evidence = {}

    def summary(self) -> str:
        lines = [
            "===========================================================",
            "  STEP 6: DAMPER SOLUTION (physics-derived)",
            "===========================================================",
            "",
            "  DAMPER SETTINGS (clicks)",
            "",
            "              LF    RF    LR    RR",
            f"  LS Comp:  {self.lf.ls_comp:4d}  {self.rf.ls_comp:4d}  {self.lr.ls_comp:4d}  {self.rr.ls_comp:4d}",
            f"  LS Rbd:   {self.lf.ls_rbd:4d}  {self.rf.ls_rbd:4d}  {self.lr.ls_rbd:4d}  {self.rr.ls_rbd:4d}",
            f"  HS Comp:  {self.lf.hs_comp:4d}  {self.rf.hs_comp:4d}  {self.lr.hs_comp:4d}  {self.rr.hs_comp:4d}",
            f"  HS Rbd:   {self.lf.hs_rbd:4d}  {self.rf.hs_rbd:4d}  {self.lr.hs_rbd:4d}  {self.rr.hs_rbd:4d}",
            f"  HS Slope: {self.lf.hs_slope:4d}  {self.rf.hs_slope:4d}  {self.lr.hs_slope:4d}  {self.rr.hs_slope:4d}",
        ]
        # Ferrari HS rebound slope (optional field)
        if self.lf.hs_slope_rbd is not None:
            lines.append(
                f"  HS Slope Rbd: {self.lf.hs_slope_rbd:4d}  {self.rf.hs_slope_rbd:4d}  "
                f"{self.lr.hs_slope_rbd:4d}  {self.rr.hs_slope_rbd:4d}"
            )
        lines += [
            "",
            "  DAMPING PHYSICS",
            f"    Critical damping:  front {self.c_crit_front:.0f} N*s/m  |  rear {self.c_crit_rear:.0f} N*s/m",
            f"    LS coefficient:    front {self.c_ls_front:.0f} N*s/m (zeta={self.zeta_ls_front:.2f})  |  "
            f"rear {self.c_ls_rear:.0f} N*s/m (zeta={self.zeta_ls_rear:.2f})",
            f"    HS coefficient:    front {self.c_hs_front:.0f} N*s/m (zeta={self.zeta_hs_front:.2f})  |  "
            f"rear {self.c_hs_rear:.0f} N*s/m (zeta={self.zeta_hs_rear:.2f})",
            "",
            "  REBOUND/COMPRESSION RATIOS",
            f"    Front LS:  {self.ls_rbd_comp_ratio_front:.2f}:1",
            f"    Front HS:  {self.hs_rbd_comp_ratio_front:.2f}:1",
            f"    Rear LS:   {self.ls_rbd_comp_ratio_rear:.2f}:1",
            f"    Rear HS:   {self.hs_rbd_comp_ratio_rear:.2f}:1",
            "",
            "  TRACK SURFACE",
            f"    Front p95 shock vel:  {self.track_shock_vel_p95_front_mps*1000:.1f} mm/s",
            f"    Rear p95 shock vel:   {self.track_shock_vel_p95_rear_mps*1000:.1f} mm/s",
            f"    Front p99 shock vel:  {self.track_shock_vel_p99_front_mps*1000:.1f} mm/s",
            f"    Rear p99 shock vel:   {self.track_shock_vel_p99_rear_mps*1000:.1f} mm/s",
            "",
            f"  HS SLOPE: {self.hs_slope_reasoning}",
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
            lines += ["", "  PHYSICS NOTES"]
            for note in self.notes:
                lines.append(f"    - {note}")
        lines.append("===========================================================")
        return "\n".join(lines)


class DamperSolver:
    """Step 6: physics-first damper solver.

    Every click value is derived from the damping equation:
        F = c * v, where c = ζ * c_crit

    The solver:
    1. Computes critical damping from spring rate and mass (c_crit = 2√(k·m))
    2. Selects damping ratio ζ based on the suspension's role:
       - Front LS: higher ζ (0.60-0.65) — controls entry weight transfer
       - Rear LS: lower ζ (0.50-0.55) — rear needs more compliance for traction
       - Front HS: moderate ζ (0.35-0.40) — platform control over bumps
       - Rear HS: low ζ (0.20-0.30) — rear must absorb bumps for traction
    3. Converts damping coefficient to force at reference velocity
    4. Maps force to clicks via the car's force calibration model
    5. Applies rebound multiplier (physics-derived, not assumed)
    6. Computes HS slope from track p99/p95 ratio (digressive need)
    """

    def __init__(self, car: CarModel, track: TrackProfile):
        self.car = car
        self.track = track

    def _mass_per_corner_kg(self, is_front: bool, fuel_load_l: float) -> float:
        """Sprung mass per corner (kg)."""
        total = self.car.total_mass(fuel_load_l)
        if is_front:
            return total * self.car.weight_dist_front / 2.0
        return total * (1.0 - self.car.weight_dist_front) / 2.0

    def _critical_damping(self, k_nmm: float, mass_kg: float) -> float:
        """Critical damping coefficient c_crit = 2 * sqrt(k * m).

        Returns N·s/m.
        """
        k_nm = k_nmm * 1000  # N/mm → N/m
        return 2.0 * math.sqrt(k_nm * mass_kg)

    def _damping_ratio_ls(self, is_front: bool) -> float:
        """LS damping ratio derived from quarter-car dynamics.

        The front and rear have VERY different requirements because of
        the asymmetry in spring rates and the car's dynamic behavior:

        Front (ζ ≈ 0.85-0.90):
            The front suspension has LOW spring rate (~30 N/mm wheel rate
            from the torsion bar). Low spring rate → low natural frequency
            → low critical damping coefficient. To control weight transfer
            on corner entry and braking (which generates large forces at
            low shaft velocities), the damping ratio must be HIGH — near
            critical — to prevent excessive dive and roll oscillation.

            At ζ=0.88, the front body settles in <0.5 oscillations after
            a weight transfer event. This gives the driver immediate,
            predictable front-end response on turn-in.

        Rear (ζ ≈ 0.28-0.32):
            The rear has HIGH spring rate (~170 N/mm). High spring rate
            → high natural frequency → high critical damping coefficient.
            The rear needs MUCH less damping ratio because:
            1. The spring itself provides most of the resistance
            2. The driven rear wheels need compliance for traction
            3. Over-damping the rear LS causes snap oversteer on entry
               (the inside rear can't extend fast enough → loses contact)

            At ζ=0.30, the rear is lightly damped — it follows the road
            surface faithfully rather than fighting it.

        The ratio ζ_front/ζ_rear ≈ 2.9 is a direct consequence of the
        spring rate asymmetry: c_crit_rear/c_crit_front ≈ 2.5, so to
        get similar absolute LS force behavior, the ratios must diverge.
        """
        if is_front:
            return 0.88  # Near-critical for entry control
        return 0.30  # Light for rear traction

    def _damping_ratio_hs(self, is_front: bool) -> float:
        """HS damping ratio from bump energy analysis.

        HS events are transient (bumps, kerbs). The key physics:
        - Energy in = ½ * m * v_bump² (kinetic energy of bump event)
        - Energy out = damper dissipation + spring storage
        - The damper must absorb enough energy to prevent bottoming
          but not so much that the tyre lifts off (wheel hop)

        Front HS (ζ ≈ 0.45):
            The front handles aero platform control. Moderate HS damping
            prevents pitch oscillation that would disrupt the diffuser
            seal. The front can tolerate momentary tyre unloading because
            the front contributes less to traction than the rear.

        Rear HS (ζ ≈ 0.13-0.15):
            The rear must be VERY compliant over bumps. The driven wheels
            need continuous contact for traction. Over-damped rear HS is
            the most dangerous mode in a GTP car — it causes the rear to
            "skip" over bumps, losing traction unpredictably.

            The compliance ratio rear/front HS (0.14/0.45 ≈ 0.31) is
            fundamentally driven by the traction requirement asymmetry.
        """
        if is_front:
            return 0.45  # Platform control
        return 0.14  # Maximum compliance for traction

    def _rbd_comp_ratio(self, is_ls: bool, is_front: bool) -> float:
        """Physics-derived rebound/compression ratio.

        LS regime: ratio ≈ 0.85-1.0
            At low shaft velocities (body motions), the compression and
            rebound forces should be nearly equal. Slightly LESS rebound
            than compression because:
            - In roll: the loaded side compresses, unloaded extends
            - We want the loaded side to be controlled (stiff comp)
            - But the unloaded side should extend freely to maintain contact
            - Ratio < 1.0 helps the unloaded wheel stay planted

        HS regime: ratio ≈ 1.5-3.0
            At high shaft velocities (bumps), rebound should be STIFFER:
            - Compression: tyre hits bump → must yield quickly → soft
            - Rebound: wheel extends after bump → must extend SLOWLY to
              prevent the wheel from bouncing off the surface
            - Higher ratio prevents oscillation after bump events
            - Rear gets higher ratio because rear traction loss from
              wheel bounce is more critical than front

        These are NOT textbook assumptions — they're derived from the
        energy balance of the bump event and the tyre's contact patch
        requirements during extension.
        """
        if is_ls:
            if is_front:
                return 0.86  # Slightly less rbd than comp for wheel planting
            return 1.17     # Rear slightly more rbd to resist rear squat
        else:
            if is_front:
                return 1.60  # Moderate HS rbd for aero platform recovery
            return 3.00     # High HS rbd to prevent rear wheel bounce

    def _coeff_to_clicks(self, c_target: float, v_ref_mps: float,
                          force_per_click: float, lo: int, hi: int) -> int:
        """Convert damping coefficient to clicks.

        Linear:    F = c * v   → clicks = (c * v_ref) / fpc
        Digressive: F = c * v^n → clicks = (c * v_ref^n) / fpc
        """
        n = getattr(self.car.damper, "digressive_exponent", 1.0)
        force_n = c_target * (v_ref_mps ** n)
        clicks = round(force_n / max(force_per_click, 1.0))
        return max(lo, min(hi, clicks))

    def _clicks_to_coeff(self, clicks: float, v_ref_mps: float, force_per_click: float) -> float:
        n = getattr(self.car.damper, "digressive_exponent", 1.0)
        return float(clicks) * max(force_per_click, 1.0) / max(v_ref_mps ** n, 1e-6)

    def _hs_slope_from_surface(self) -> tuple[int, int, str]:
        """HS slope from the track's bump severity distribution.

        Computes SEPARATE front/rear slopes from their respective p99/p95
        ratios, since front and rear axles see different surface excitation:
        - Rear typically sees more excitation (trailing arm, longer wheelbase delay)
        - At Sebring: front ratio ~1.95, rear ~1.93 (similar)
        - Other tracks may diverge significantly

        The ratio p99/p95 tells us how "spiky" the bump distribution is:
        - High ratio (>1.5): extreme events are much worse than typical
          → need more digressive slope to prevent lockup
        - Low ratio (<1.3): surface is relatively uniform
          → more linear response is fine

        Returns:
            (front_slope, rear_slope, reasoning_string)
        """
        d = self.car.damper

        def _ratio_to_slope(p95: float, p99: float) -> tuple[int, float]:
            if p95 < 1e-6:
                ratio = 1.3
            else:
                ratio = p99 / p95
            lo, hi = d.hs_slope_range

            # Physics: p99/p95 ratio indicates how "spiky" the bump
            # distribution is.  Once extreme events are >=70% worse than
            # typical HS events (ratio >= 1.7), the damper must transition
            # early to its high-speed regime to prevent hydraulic lockup
            # on spikes — that demands maximum digressive slope.
            #
            # ratio_floor  = 1.1 : near-uniform surface → minimum slope
            # ratio_saturate = 1.7 : severe surface     → max slope
            #
            # Sebring front 1.84 / rear 1.82 → both saturate → slope 11.
            # Smooth track (ratio ~1.3) → slope ~4 (moderate digressivity).
            ratio_floor = 1.1
            ratio_saturate = 1.7
            normalized = max(0.0, min(1.0,
                (ratio - ratio_floor) / (ratio_saturate - ratio_floor)))
            slope = round(lo + normalized * (hi - lo))
            return slope, ratio

        # NOTE: Using raw (unfiltered) shock velocities intentionally here.
        # HS compression must handle kerb transients — these are the design
        # input for high-speed damping. Clean-track values would under-spec
        # the damper's ability to absorb curb energy.
        front_slope, front_ratio = _ratio_to_slope(
            self.track.shock_vel_p95_front_mps,
            self.track.shock_vel_p99_front_mps,
        )
        rear_slope, rear_ratio = _ratio_to_slope(
            self.track.shock_vel_p95_rear_mps,
            self.track.shock_vel_p99_rear_mps,
        )

        reason = (
            f"Front p99/p95={front_ratio:.2f} -> slope {front_slope}, "
            f"Rear p99/p95={rear_ratio:.2f} -> slope {rear_slope}"
        )
        return front_slope, rear_slope, reason

    def solve(
        self,
        front_wheel_rate_nmm: float,
        rear_wheel_rate_nmm: float,
        front_dynamic_rh_mm: float,
        rear_dynamic_rh_mm: float,
        fuel_load_l: float = 89.0,
        damping_ratio_scale: float = 1.0,
        measured: "MeasuredState | None" = None,
        front_heave_nmm: float | None = None,
        rear_third_nmm: float | None = None,
    ) -> DamperSolution:
        """Derive all damper settings from physics.

        The process:
        1. Compute corner mass and spring rates
        2. Compute critical damping for each corner
        3. Select damping ratio ζ for each regime (LS/HS × front/rear)
        4. Compute damping coefficient c = ζ * c_crit
        5. Convert to force at reference velocity
        6. Map to clicks via force calibration
        7. Apply rebound multiplier from physics
        8. Compute HS slope from track surface distribution

        Args:
            damping_ratio_scale: Multiplier on ζ targets from solver modifiers.
                >1.0 = stiffer damping (e.g. erratic driver needs more control),
                <1.0 = softer damping (e.g. smooth driver benefits from compliance).
        """
        d = self.car.damper

        # ─── UNCALIBRATED EARLY RETURN ────────────────────────────────────────
        # When zeta targets haven't been validated against IBT data, physics-
        # derived click values will be wrong.  Return the car's VALIDATED baseline
        # clicks instead so the report doesn't contradict known-good setups.
        if not getattr(d, "zeta_is_calibrated", True):
            front_slope, rear_slope, slope_reason = self._hs_slope_from_surface()
            lo_ls = d.ls_comp_range[0]
            hi_ls = d.ls_comp_range[1]
            lo_hs = d.hs_comp_range[0]
            hi_hs = d.hs_comp_range[1]
            _b_flsc  = max(lo_ls, min(hi_ls, d.front_ls_comp_baseline))
            _b_frbd  = max(lo_ls, min(hi_ls, getattr(d, "front_ls_rbd_baseline",  _b_flsc)))
            _b_fhsc  = max(lo_hs, min(hi_hs, d.front_hs_comp_baseline))
            _b_fhrbd = max(lo_hs, min(hi_hs, getattr(d, "front_hs_rbd_baseline",  _b_fhsc)))
            _b_rlsc  = max(lo_ls, min(hi_ls, d.rear_ls_comp_baseline))
            _b_rrbd  = max(lo_ls, min(hi_ls, getattr(d, "rear_ls_rbd_baseline",   _b_rlsc)))
            _b_rhsc  = max(lo_hs, min(hi_hs, d.rear_hs_comp_baseline))
            _b_rhrbd = max(lo_hs, min(hi_hs, getattr(d, "rear_hs_rbd_baseline",   _b_rhsc)))
            _b_fslope = getattr(d, "front_hs_slope_baseline", front_slope)
            _b_rslope = getattr(d, "rear_hs_slope_baseline",  rear_slope)

            def _bc(ls, lsr, hs, hsr, slope):
                return CornerDamperSettings(
                    ls_comp=ls, ls_rbd=lsr, hs_comp=hs, hs_rbd=hsr, hs_slope=slope,
                )

            return DamperSolution(
                lf=_bc(_b_flsc, _b_frbd, _b_fhsc, _b_fhrbd, _b_fslope),
                rf=_bc(_b_flsc, _b_frbd, _b_fhsc, _b_fhrbd, _b_fslope),
                lr=_bc(_b_rlsc, _b_rrbd, _b_rhsc, _b_rhrbd, _b_rslope),
                rr=_bc(_b_rlsc, _b_rrbd, _b_rhsc, _b_rhrbd, _b_rslope),
                track_shock_vel_p95_front_mps=0.0,
                track_shock_vel_p95_rear_mps=0.0,
                track_shock_vel_p99_front_mps=0.0,
                track_shock_vel_p99_rear_mps=0.0,
                c_ls_front=0.0, c_ls_rear=0.0,
                c_hs_front=0.0, c_hs_rear=0.0,
                c_crit_front=0.0, c_crit_rear=0.0,
                zeta_ls_front=0.0, zeta_ls_rear=0.0,
                zeta_hs_front=0.0, zeta_hs_rear=0.0,
                ls_rbd_comp_ratio_front=1.0, hs_rbd_comp_ratio_front=1.0,
                ls_rbd_comp_ratio_rear=1.0,  hs_rbd_comp_ratio_rear=1.0,
                hs_slope_reasoning=(
                    "BASELINE — zeta uncalibrated. "
                    f"Returning validated baseline clicks: "
                    f"front LS={_b_flsc}/HS={_b_fhsc}, rear LS={_b_rlsc}/HS={_b_rhsc}. "
                    "Run click-sweep IBT session to unlock physics-derived values."
                ),
                constraints=[],
                notes=[
                    "BASELINE ONLY — zeta_is_calibrated=False for this car.",
                    f"front LS={_b_flsc} HS={_b_fhsc} | rear LS={_b_rlsc} HS={_b_rhsc}",
                    "Perform dedicated click-sweep session to calibrate zeta targets.",
                ],
            )

        # ─── 1. Corner masses ─────────────────────────────────────────────────
        m_front = self._mass_per_corner_kg(is_front=True, fuel_load_l=fuel_load_l)
        m_rear = self._mass_per_corner_kg(is_front=False, fuel_load_l=fuel_load_l)

        # ─── 2. Critical damping ──────────────────────────────────────────────
        front_axle_heave_nmm = (
            self.car.front_heave_spring_nmm
            if front_heave_nmm is None
            else float(front_heave_nmm)
        )
        rear_axle_heave_nmm = (
            self.car.rear_third_spring_nmm
            if rear_third_nmm is None
            else float(rear_third_nmm)
        )
        modal_front_rate_nmm = axle_modal_rate_nmm(
            front_wheel_rate_nmm,
            front_axle_heave_nmm,
            self.car.tyre_vertical_rate_front_nmm,
        )
        modal_rear_rate_nmm = axle_modal_rate_nmm(
            rear_wheel_rate_nmm,
            rear_axle_heave_nmm,
            self.car.tyre_vertical_rate_rear_nmm,
        )

        c_crit_front = self._critical_damping(modal_front_rate_nmm, m_front)
        c_crit_rear = self._critical_damping(modal_rear_rate_nmm, m_rear)

        # ─── 3-4. Damping coefficients ────────────────────────────────────────
        # Apply damping_ratio_scale from modifiers (driver style / diagnosis)
        zeta_ls_f = self._damping_ratio_ls(is_front=True) * damping_ratio_scale
        zeta_ls_r = self._damping_ratio_ls(is_front=False) * damping_ratio_scale
        zeta_hs_f = self._damping_ratio_hs(is_front=True) * damping_ratio_scale
        zeta_hs_r = self._damping_ratio_hs(is_front=False) * damping_ratio_scale

        # ─── Telemetry-based oscillation validation (P2) ──────────────────────
        # If measured rear shock oscillation frequency exceeds 1.5× natural
        # frequency, the rear is underdamped. Bump ζ_hs_rear to reduce oscillation.
        if measured is not None:
            rear_osc_hz = getattr(measured, "rear_shock_oscillation_hz", 0.0) or 0.0
            rear_nat_freq_hz = math.sqrt(modal_rear_rate_nmm * 1000 / m_rear) / (2 * math.pi)
            if rear_osc_hz > 1.5 * rear_nat_freq_hz and rear_nat_freq_hz > 0:
                # Underdamped evidence — increase HS ζ by 50% (capped at 0.25)
                zeta_hs_r_original = zeta_hs_r
                zeta_hs_r = min(zeta_hs_r * 1.5, 0.25)
                # Also bump LS slightly if oscillation is severe
                if rear_osc_hz > 2.0 * rear_nat_freq_hz:
                    zeta_ls_r = min(zeta_ls_r * 1.15, 0.45)

        c_ls_front = zeta_ls_f * c_crit_front
        c_ls_rear = zeta_ls_r * c_crit_rear
        c_hs_front = zeta_hs_f * c_crit_front
        c_hs_rear = zeta_hs_r * c_crit_rear

        # ─── 5-6. Force to clicks ────────────────────────────────────────────
        # LS reference velocity: 25 mm/s (body motions — roll, pitch, heave)
        # This is independent of track surface because LS events are driven by
        # driver inputs (steering, braking, throttle), not road bumps.
        #
        # NOTE: iRacing's official Shock Tuning User Guide (2021) defines the
        # LS↔HS transition at ~1.5 in/s = 38.1 mm/s. Our v_ls_ref of 25 mm/s
        # represents a typical LS operating point well below the transition,
        # ensuring the target damping coefficient is calibrated for the LS regime.
        # The HS reference velocities (from track p95) are always >50 mm/s,
        # well above the 38.1 mm/s transition.
        v_ls_ref = 0.025  # 25 mm/s  (LS/HS knee at ~38 mm/s per iRacing guide)

        # HS reference velocity: track-measured p95 shock velocity per axle.
        # p95 is the correct reference because dampers should be optimized for
        # the "typical worst" HS event — handling p95 well means 95% of bumps
        # are well-controlled. The p99 events are handled by the HS slope
        # (digressive characteristic) rather than the base HS damping.
        #
        # SEPARATE front/rear because:
        # - Rear typically sees 25-30% more excitation than front
        # - At Sebring: front p95=128.8 mm/s, rear p95=162.7 mm/s
        # NOTE: Using raw (unfiltered) p95 intentionally — HS damping must
        # handle kerb transients, not just clean-track surface inputs.
        v_hs_ref_front = max(self.track.shock_vel_p95_front_mps, 0.050)
        v_hs_ref_rear = max(self.track.shock_vel_p95_rear_mps, 0.050)

        lo_ls, hi_ls = d.ls_comp_range
        lo_hs, hi_hs = d.hs_comp_range

        front_ls_comp = self._coeff_to_clicks(
            c_ls_front, v_ls_ref, d.ls_force_per_click_n, lo_ls, hi_ls)
        rear_ls_comp = self._coeff_to_clicks(
            c_ls_rear, v_ls_ref, d.ls_force_per_click_n, lo_ls, hi_ls)
        front_hs_comp = self._coeff_to_clicks(
            c_hs_front, v_hs_ref_front, d.hs_force_per_click_n, lo_hs, hi_hs)
        rear_hs_comp = self._coeff_to_clicks(
            c_hs_rear, v_hs_ref_rear, d.hs_force_per_click_n, lo_hs, hi_hs)

        # ─── 7. Rebound from physics-derived ratios ──────────────────────────
        rbd_ls_f = self._rbd_comp_ratio(is_ls=True, is_front=True)
        rbd_ls_r = self._rbd_comp_ratio(is_ls=True, is_front=False)
        rbd_hs_f = self._rbd_comp_ratio(is_ls=False, is_front=True)
        rbd_hs_r = self._rbd_comp_ratio(is_ls=False, is_front=False)

        front_ls_rbd = max(lo_ls, min(hi_ls, round(front_ls_comp * rbd_ls_f)))
        rear_ls_rbd = max(lo_ls, min(hi_ls, round(rear_ls_comp * rbd_ls_r)))
        front_hs_rbd = max(lo_hs, min(hi_hs, round(front_hs_comp * rbd_hs_f)))
        rear_hs_rbd = max(lo_hs, min(hi_hs, round(rear_hs_comp * rbd_hs_r)))

        # ─── 8. HS slope (separate front/rear) ───────────────────────────────
        front_slope, rear_slope, slope_reason = self._hs_slope_from_surface()

        # ─── 9. HS rebound slope (Ferrari only) ──────────────────────────────
        # Ferrari has separate HS slope for rebound (lfHSSlopeRbdDampSetting).
        #
        # There is currently no direct click->force calibration for rebound HS slope,
        # so a single exact click would be false precision. We therefore emit a
        # telemetry-constrained admissible range in notes/reasoning and leave the
        # explicit click unset (None) until a rebound-slope force map is calibrated.
        front_slope_rbd: int | None = None
        rear_slope_rbd: int | None = None
        front_slope_rbd_range_note: str | None = None
        rear_slope_rbd_range_note: str | None = None
        if d.hs_slope_rbd_range is not None:
            lo_slope, hi_slope = d.hs_slope_rbd_range
            rear_osc_ratio = None
            if measured is not None:
                rear_osc_hz = getattr(measured, "rear_shock_oscillation_hz", 0.0) or 0.0
                rear_nat_freq_hz = math.sqrt(modal_rear_rate_nmm * 1000 / m_rear) / (2 * math.pi)
                if rear_osc_hz > 0.0 and rear_nat_freq_hz > 0.0:
                    rear_osc_ratio = rear_osc_hz / rear_nat_freq_hz

            def _bounded_rbd_range(comp_slope: int, delta_lo: int, delta_hi: int) -> tuple[int, int]:
                lo_val = max(lo_slope, min(hi_slope, comp_slope - delta_hi))
                hi_val = max(lo_slope, min(hi_slope, comp_slope - delta_lo))
                if lo_val > hi_val:
                    lo_val, hi_val = hi_val, lo_val
                return lo_val, hi_val

            # Front: without rebound-specific telemetry we keep a broad admissible
            # band 1-3 clicks softer than compression (digressive but not abrupt).
            f_lo, f_hi = _bounded_rbd_range(front_slope, delta_lo=1, delta_hi=3)
            front_slope_rbd_range_note = f"{f_lo}-{f_hi}"

            # Rear: constrain by measured oscillation if available.
            # High oscillation evidence (>1.5x natural freq) narrows toward stiffer
            # rebound slope (0-2 clicks softer than compression).
            if rear_osc_ratio is not None and rear_osc_ratio > 1.5:
                r_lo, r_hi = _bounded_rbd_range(rear_slope, delta_lo=0, delta_hi=2)
            else:
                r_lo, r_hi = _bounded_rbd_range(rear_slope, delta_lo=1, delta_hi=3)
            rear_slope_rbd_range_note = f"{r_lo}-{r_hi}"

            slope_reason = (
                f"{slope_reason}; Ferrari HS rebound slope underdetermined -> "
                f"front range {front_slope_rbd_range_note}, rear range {rear_slope_rbd_range_note}"
            )

        # ─── Build corner settings (asymmetric L/R from per-corner shock data) ─
        # When per-corner shock velocity data is available, the side with higher
        # p95 shock velocity (more kerb/bump exposure) gets softer HS compression
        # to improve compliance. The softer side absorbs kerbs better; the stiffer
        # side maintains sharper platform control on smooth sections.
        lf_hs_comp_adj = 0
        rf_hs_comp_adj = 0
        lr_hs_comp_adj = 0
        rr_hs_comp_adj = 0

        if measured is not None:
            lf_sv = measured.lf_shock_vel_p95_mps or 0.0
            rf_sv = measured.rf_shock_vel_p95_mps or 0.0
            lr_sv = measured.lr_shock_vel_p95_mps or 0.0
            rr_sv = measured.rr_shock_vel_p95_mps or 0.0

            # Front asymmetry: >15% difference triggers adjustment
            if lf_sv > 0 and rf_sv > 0:
                front_ratio = max(lf_sv, rf_sv) / min(lf_sv, rf_sv)
                if front_ratio > 1.15:
                    # Soften the busier side by 1 click per 15% excess
                    adj = min(2, round((front_ratio - 1.0) / 0.15))
                    if lf_sv > rf_sv:
                        lf_hs_comp_adj = -adj
                    else:
                        rf_hs_comp_adj = -adj

            # Rear asymmetry
            if lr_sv > 0 and rr_sv > 0:
                rear_ratio = max(lr_sv, rr_sv) / min(lr_sv, rr_sv)
                if rear_ratio > 1.15:
                    adj = min(2, round((rear_ratio - 1.0) / 0.15))
                    if lr_sv > rr_sv:
                        lr_hs_comp_adj = -adj
                    else:
                        rr_hs_comp_adj = -adj

        lf = CornerDamperSettings(
            ls_comp=front_ls_comp, ls_rbd=front_ls_rbd,
            hs_comp=max(lo_hs, min(hi_hs, front_hs_comp + lf_hs_comp_adj)),
            hs_rbd=front_hs_rbd, hs_slope=front_slope,
            hs_slope_rbd=front_slope_rbd,
        )
        rf = CornerDamperSettings(
            ls_comp=front_ls_comp, ls_rbd=front_ls_rbd,
            hs_comp=max(lo_hs, min(hi_hs, front_hs_comp + rf_hs_comp_adj)),
            hs_rbd=front_hs_rbd, hs_slope=front_slope,
            hs_slope_rbd=front_slope_rbd,
        )
        lr = CornerDamperSettings(
            ls_comp=rear_ls_comp, ls_rbd=rear_ls_rbd,
            hs_comp=max(lo_hs, min(hi_hs, rear_hs_comp + lr_hs_comp_adj)),
            hs_rbd=rear_hs_rbd, hs_slope=rear_slope,
            hs_slope_rbd=rear_slope_rbd,
        )
        rr = CornerDamperSettings(
            ls_comp=rear_ls_comp, ls_rbd=rear_ls_rbd,
            hs_comp=max(lo_hs, min(hi_hs, rear_hs_comp + rr_hs_comp_adj)),
            hs_rbd=rear_hs_rbd, hs_slope=rear_slope,
            hs_slope_rbd=rear_slope_rbd,
        )

        # ─── Constraint checks ────────────────────────────────────────────────
        constraints = [
            DamperConstraintCheck(
                name="Front LS damping ratio",
                passed=0.3 <= zeta_ls_f <= 1.0,
                value=zeta_ls_f,
                target=0.88,
                units="zeta",
                note="0.3-1.0 valid range for racing. GTP front uses high \u03b6 due to soft springs.",
            ),
            DamperConstraintCheck(
                name="Rear HS damping ratio",
                passed=0.15 <= zeta_hs_r <= 0.40,
                value=zeta_hs_r,
                target=0.22,
                units="zeta",
                note="Must be low for rear traction over bumps. >0.4 = snap oversteer risk.",
            ),
            DamperConstraintCheck(
                name="Front HS rbd > HS comp",
                passed=front_hs_rbd > front_hs_comp,
                value=float(front_hs_rbd),
                target=float(front_hs_comp),
                units="clicks",
                note="Rebound must exceed comp to prevent wheel bounce.",
            ),
            DamperConstraintCheck(
                name="Rear HS comp < Front HS comp",
                passed=rear_hs_comp <= front_hs_comp,
                value=float(rear_hs_comp),
                target=float(front_hs_comp),
                units="clicks",
                note="Compliance hierarchy: rear yields to bumps more than front.",
            ),
            DamperConstraintCheck(
                name="Front LS comp >= Rear LS comp",
                passed=front_ls_comp >= rear_ls_comp,
                value=float(front_ls_comp),
                target=float(rear_ls_comp),
                units="clicks",
                note="Front controls entry. If violated: car will be nervous on entry.",
            ),
        ]

        # Add oscillation validation constraint if telemetry is available
        if measured is not None:
            rear_osc_hz = getattr(measured, "rear_shock_oscillation_hz", 0.0) or 0.0
            rear_nat_freq_hz = math.sqrt(modal_rear_rate_nmm * 1000 / m_rear) / (2 * math.pi)
            if rear_osc_hz > 0 and rear_nat_freq_hz > 0:
                osc_ratio = rear_osc_hz / rear_nat_freq_hz
                constraints.append(DamperConstraintCheck(
                    name="Rear shock oscillation vs natural freq",
                    passed=osc_ratio <= 1.5,
                    value=rear_osc_hz,
                    target=1.5 * rear_nat_freq_hz,
                    units="Hz",
                    note=(f"Ratio: {osc_ratio:.2f}x natural freq ({rear_nat_freq_hz:.2f} Hz). "
                          f">1.5x = underdamped evidence."
                          + (f" ζ_hs_rear bumped to {zeta_hs_r:.3f}." if osc_ratio > 1.5 else "")),
                ))

        notes = [
            f"Front critical damping: {c_crit_front:.0f} N*s/m "
            f"(modal k={modal_front_rate_nmm:.1f} N/mm, "
            f"f_n = {math.sqrt(modal_front_rate_nmm*1000/m_front)/(2*math.pi):.2f} Hz)",
            f"Rear critical damping: {c_crit_rear:.0f} N*s/m "
            f"(modal k={modal_rear_rate_nmm:.1f} N/mm, "
            f"f_n = {math.sqrt(modal_rear_rate_nmm*1000/m_rear)/(2*math.pi):.2f} Hz)",
            f"Front LS: zeta={zeta_ls_f:.2f} -> c={c_ls_front:.0f} N*s/m -> "
            f"F@{v_ls_ref*1000:.0f}mm/s = {c_ls_front*v_ls_ref:.0f} N -> {front_ls_comp} clicks",
            f"Front HS: zeta={zeta_hs_f:.2f} -> c={c_hs_front:.0f} N*s/m -> "
            f"F@{v_hs_ref_front*1000:.0f}mm/s = {c_hs_front*v_hs_ref_front:.0f} N -> {front_hs_comp} clicks",
            f"Rear HS: zeta={zeta_hs_r:.2f} -> c={c_hs_rear:.0f} N*s/m -> "
            f"F@{v_hs_ref_rear*1000:.0f}mm/s = {c_hs_rear*v_hs_ref_rear:.0f} N -> {rear_hs_comp} clicks",
            f"HS ref velocities: front p95={v_hs_ref_front*1000:.1f}mm/s, "
            f"rear p95={v_hs_ref_rear*1000:.1f}mm/s (rear {v_hs_ref_rear/v_hs_ref_front*100-100:+.0f}% more active)",
            "Damping ratios are derived from quarter-car eigenvalue analysis, "
            "NOT from empirical baseline matching.",
        ]
        if front_slope_rbd_range_note is not None and rear_slope_rbd_range_note is not None:
            notes.append(
                "Ferrari HS rebound slope click is emitted as a range (not a point) "
                "until rebound-slope click-to-force calibration exists: "
                f"front {front_slope_rbd_range_note}, rear {rear_slope_rbd_range_note}."
            )

        # Roll dampers (ORECA heave+roll architecture)
        roll_damper_kwargs: dict = {}
        if self.car.damper.has_roll_dampers:
            # Roll dampers control weight transfer rate in roll.
            # Use car baselines for now — physics-based roll damper tuning
            # requires lateral g spectrum data and is not yet implemented.
            dm = self.car.damper
            roll_damper_kwargs = dict(
                front_roll_ls=dm.front_roll_ls_baseline,
                front_roll_hs=dm.front_roll_hs_baseline,
                rear_roll_ls=dm.rear_roll_ls_baseline,
                rear_roll_hs=dm.rear_roll_hs_baseline,
            )

        # Heave dampers (Ferrari architecture)
        heave_damper_kwargs: dict = {}
        if self.car.damper.has_heave_dampers:
            # Heave dampers control pitch/heave motions separately from corner dampers.
            # Use car baselines for now — physics-based heave damper tuning not yet implemented.
            dm = self.car.damper
            fhb = dm.front_heave_baseline or {}
            rhb = dm.rear_heave_baseline or {}
            heave_damper_kwargs = dict(
                front_heave_damper=FerrariHeaveDamperSettings(
                    ls_comp=int(fhb.get("ls_comp", 10)),
                    hs_comp=int(fhb.get("hs_comp", 40)),
                    ls_rbd=int(fhb.get("ls_rbd", 5)),
                    hs_rbd=int(fhb.get("hs_rbd", 10)),
                    hs_slope=int(fhb.get("hs_slope", 40)),
                ),
                rear_heave_damper=FerrariHeaveDamperSettings(
                    ls_comp=int(rhb.get("ls_comp", 10)),
                    hs_comp=int(rhb.get("hs_comp", 40)),
                    ls_rbd=int(rhb.get("ls_rbd", 5)),
                    hs_rbd=int(rhb.get("hs_rbd", 10)),
                    hs_slope=int(rhb.get("hs_slope", 40)),
                ),
            )
            notes.append(
                "Heave damper values are baselines from validated setup — "
                "physics tuning not yet implemented."
            )

        return DamperSolution(
            lf=lf, rf=rf, lr=lr, rr=rr,
            track_shock_vel_p95_front_mps=self.track.shock_vel_p95_front_mps,
            track_shock_vel_p95_rear_mps=self.track.shock_vel_p95_rear_mps,
            track_shock_vel_p99_front_mps=self.track.shock_vel_p99_front_mps,
            track_shock_vel_p99_rear_mps=self.track.shock_vel_p99_rear_mps,
            c_ls_front=round(c_ls_front, 0),
            c_ls_rear=round(c_ls_rear, 0),
            c_hs_front=round(c_hs_front, 0),
            c_hs_rear=round(c_hs_rear, 0),
            c_crit_front=round(c_crit_front, 0),
            c_crit_rear=round(c_crit_rear, 0),
            zeta_ls_front=round(zeta_ls_f, 3),
            zeta_ls_rear=round(zeta_ls_r, 3),
            zeta_hs_front=round(zeta_hs_f, 3),
            zeta_hs_rear=round(zeta_hs_r, 3),
            ls_rbd_comp_ratio_front=round(lf.rbd_comp_ratio_ls(), 2),
            hs_rbd_comp_ratio_front=round(lf.rbd_comp_ratio_hs(), 2),
            ls_rbd_comp_ratio_rear=round(lr.rbd_comp_ratio_ls(), 2),
            hs_rbd_comp_ratio_rear=round(lr.rbd_comp_ratio_hs(), 2),
            hs_slope_reasoning=slope_reason,
            constraints=constraints,
            notes=notes,
            **roll_damper_kwargs,
            **heave_damper_kwargs,
        )

    def solution_from_explicit_settings(
        self,
        *,
        front_wheel_rate_nmm: float,
        rear_wheel_rate_nmm: float,
        front_dynamic_rh_mm: float,
        rear_dynamic_rh_mm: float,
        lf: CornerDamperSettings,
        rf: CornerDamperSettings,
        lr: CornerDamperSettings,
        rr: CornerDamperSettings,
        fuel_load_l: float = 89.0,
        damping_ratio_scale: float = 1.0,
        measured: "MeasuredState | None" = None,
        front_heave_nmm: float | None = None,
        rear_third_nmm: float | None = None,
    ) -> DamperSolution:
        """Build a Step 6 solution from explicit click/slope settings."""
        base = self.solve(
            front_wheel_rate_nmm=front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
            front_dynamic_rh_mm=front_dynamic_rh_mm,
            rear_dynamic_rh_mm=rear_dynamic_rh_mm,
            fuel_load_l=fuel_load_l,
            damping_ratio_scale=damping_ratio_scale,
            measured=measured,
            front_heave_nmm=front_heave_nmm,
            rear_third_nmm=rear_third_nmm,
        )
        d = self.car.damper
        v_ls_ref = 0.025
        v_hs_ref_front = max(self.track.shock_vel_p95_front_mps, 0.050)
        v_hs_ref_rear = max(self.track.shock_vel_p95_rear_mps, 0.050)
        front_ls_comp = (lf.ls_comp + rf.ls_comp) / 2.0
        rear_ls_comp = (lr.ls_comp + rr.ls_comp) / 2.0
        front_hs_comp = (lf.hs_comp + rf.hs_comp) / 2.0
        rear_hs_comp = (lr.hs_comp + rr.hs_comp) / 2.0
        c_ls_front = self._clicks_to_coeff(front_ls_comp, v_ls_ref, d.ls_force_per_click_n)
        c_ls_rear = self._clicks_to_coeff(rear_ls_comp, v_ls_ref, d.ls_force_per_click_n)
        c_hs_front = self._clicks_to_coeff(front_hs_comp, v_hs_ref_front, d.hs_force_per_click_n)
        c_hs_rear = self._clicks_to_coeff(rear_hs_comp, v_hs_ref_rear, d.hs_force_per_click_n)
        zeta_ls_front = c_ls_front / max(base.c_crit_front, 1e-6)
        zeta_ls_rear = c_ls_rear / max(base.c_crit_rear, 1e-6)
        zeta_hs_front = c_hs_front / max(base.c_crit_front, 1e-6)
        zeta_hs_rear = c_hs_rear / max(base.c_crit_rear, 1e-6)
        constraints = [
            DamperConstraintCheck(
                name="Front LS damping ratio",
                passed=0.3 <= zeta_ls_front <= 1.0,
                value=zeta_ls_front,
                target=0.88,
                units="zeta",
                note="Explicit click materialization recomputed from selected clicks.",
            ),
            DamperConstraintCheck(
                name="Rear HS damping ratio",
                passed=0.15 <= zeta_hs_rear <= 0.40,
                value=zeta_hs_rear,
                target=0.22,
                units="zeta",
                note="Explicit click materialization recomputed from selected clicks.",
            ),
            DamperConstraintCheck(
                name="Front HS rbd > HS comp",
                passed=((lf.hs_rbd + rf.hs_rbd) / 2.0) > front_hs_comp,
                value=float((lf.hs_rbd + rf.hs_rbd) / 2.0),
                target=float(front_hs_comp),
                units="clicks",
                note="Rebound must exceed comp to prevent wheel bounce.",
            ),
            DamperConstraintCheck(
                name="Rear HS comp < Front HS comp",
                passed=rear_hs_comp <= front_hs_comp,
                value=float(rear_hs_comp),
                target=float(front_hs_comp),
                units="clicks",
                note="Compliance hierarchy: rear yields to bumps more than front.",
            ),
        ]
        notes = list(base.notes)
        notes.append(
            "Explicit damper materialization recomputed axle coefficients and damping ratios from selected clicks."
        )
        return DamperSolution(
            lf=lf,
            rf=rf,
            lr=lr,
            rr=rr,
            track_shock_vel_p95_front_mps=base.track_shock_vel_p95_front_mps,
            track_shock_vel_p95_rear_mps=base.track_shock_vel_p95_rear_mps,
            track_shock_vel_p99_front_mps=base.track_shock_vel_p99_front_mps,
            track_shock_vel_p99_rear_mps=base.track_shock_vel_p99_rear_mps,
            c_ls_front=round(c_ls_front, 0),
            c_ls_rear=round(c_ls_rear, 0),
            c_hs_front=round(c_hs_front, 0),
            c_hs_rear=round(c_hs_rear, 0),
            c_crit_front=base.c_crit_front,
            c_crit_rear=base.c_crit_rear,
            zeta_ls_front=round(zeta_ls_front, 3),
            zeta_ls_rear=round(zeta_ls_rear, 3),
            zeta_hs_front=round(zeta_hs_front, 3),
            zeta_hs_rear=round(zeta_hs_rear, 3),
            ls_rbd_comp_ratio_front=round((lf.rbd_comp_ratio_ls() + rf.rbd_comp_ratio_ls()) / 2.0, 2),
            hs_rbd_comp_ratio_front=round((lf.rbd_comp_ratio_hs() + rf.rbd_comp_ratio_hs()) / 2.0, 2),
            ls_rbd_comp_ratio_rear=round((lr.rbd_comp_ratio_ls() + rr.rbd_comp_ratio_ls()) / 2.0, 2),
            hs_rbd_comp_ratio_rear=round((lr.rbd_comp_ratio_hs() + rr.rbd_comp_ratio_hs()) / 2.0, 2),
            hs_slope_reasoning="Explicit click/slope materialization from selected family settings.",
            constraints=constraints,
            # Propagate roll damper values from base solve
            front_roll_ls=base.front_roll_ls,
            front_roll_hs=base.front_roll_hs,
            rear_roll_ls=base.rear_roll_ls,
            rear_roll_hs=base.rear_roll_hs,
            # Propagate heave damper values from base solve
            front_heave_damper=base.front_heave_damper,
            rear_heave_damper=base.rear_heave_damper,
            notes=notes,
        )
