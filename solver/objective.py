"""Multi-objective scoring function for setup candidates.

Turns the optimization objective into a single canonical score with
a transparent breakdown. Every candidate shows exactly why it ranked
where it did.

Score formula:
    total_score = (
        + lap_gain_ms
        - 1.0 * platform_risk_ms        [primary — platform collapse is catastrophic]
        - 0.5 * driver_mismatch_ms
        - 0.6 * telemetry_uncertainty_ms
        - 0.7 * envelope_penalty_ms
        - 0.3 * staleness_penalty_ms
    )

All terms are in milliseconds for human-interpretable comparison.

Platform sigma is computed from empirical IBT calibration when available
(data/learnings/heave_calibration_<car>_<track>.json), falling back to
a physics model. When you drop in an IBT from a 380 N/mm run, the system
automatically learns the actual sigma at that heave rate and updates the
scoring model.

Usage:
    from solver.objective import ObjectiveFunction
    obj = ObjectiveFunction(car, track)
    evaluation = obj.evaluate(candidate_params, solver_result, measured, driver)
    print(evaluation.breakdown)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from track_model.profile import TrackProfile
from vertical_dynamics import damped_excursion_mm


@dataclass
class PlatformRisk:
    """Platform risk breakdown — things that endanger car safety/stability."""
    bottoming_risk_ms: float = 0.0      # front RH below safe threshold
    vortex_risk_ms: float = 0.0         # front RH near vortex burst
    slider_exhaustion_ms: float = 0.0   # heave slider near travel limit
    rh_collapse_risk_ms: float = 0.0    # ride height variance too high

    @property
    def total_ms(self) -> float:
        return (
            self.bottoming_risk_ms
            + self.vortex_risk_ms
            + self.slider_exhaustion_ms
            + self.rh_collapse_risk_ms
        )


@dataclass
class DriverMismatch:
    """Driver mismatch breakdown — setup doesn't match driving style."""
    trail_brake_ms: float = 0.0         # dampers/diff don't suit trail braking depth
    throttle_style_ms: float = 0.0      # diff ramps don't match throttle progressiveness
    smoothness_ms: float = 0.0          # damper ratios vs steering jerk

    @property
    def total_ms(self) -> float:
        return self.trail_brake_ms + self.throttle_style_ms + self.smoothness_ms


@dataclass
class TelemetryUncertainty:
    """Uncertainty from signal quality/availability."""
    missing_signal_ms: float = 0.0      # key channels not available
    proxy_signal_ms: float = 0.0        # using derived rather than direct measurement
    conflict_signal_ms: float = 0.0     # conflicting evidence in data

    @property
    def total_ms(self) -> float:
        return self.missing_signal_ms + self.proxy_signal_ms + self.conflict_signal_ms


@dataclass
class EnvelopePenalty:
    """How far the candidate is from validated operating envelope."""
    setup_distance_ms: float = 0.0      # distance from known-good cluster
    telemetry_envelope_ms: float = 0.0  # measured values outside expected range

    @property
    def total_ms(self) -> float:
        return self.setup_distance_ms + self.telemetry_envelope_ms


@dataclass
class PhysicsResult:
    """Forward-evaluated physics for a candidate setup."""
    # Excursion / bottoming
    front_excursion_mm: float = 0.0
    rear_excursion_mm: float = 0.0
    front_bottoming_margin_mm: float = 20.0
    rear_bottoming_margin_mm: float = 40.0
    # Stall / vortex
    stall_margin_mm: float = 5.0
    # Platform variance
    front_sigma_mm: float = 2.0
    rear_sigma_mm: float = 3.0
    # DF balance & L/D
    df_balance_pct: float = 50.0
    df_balance_error_pct: float = 0.0
    ld_ratio: float = 3.0
    # LLTD
    lltd: float = 0.52
    lltd_error: float = 0.0
    # Damping
    zeta_ls_front: float = 0.88
    zeta_ls_rear: float = 0.30
    zeta_hs_front: float = 0.45
    zeta_hs_rear: float = 0.14
    # Wheel rates
    front_wheel_rate_nmm: float = 30.0
    rear_wheel_rate_nmm: float = 60.0
    # Roll stiffness
    k_roll_front: float = 0.0
    k_roll_rear: float = 0.0


@dataclass
class ObjectiveBreakdown:
    """Full scoring breakdown — never rank on a black box."""
    lap_gain_ms: float = 0.0
    platform_risk: PlatformRisk = field(default_factory=PlatformRisk)
    driver_mismatch: DriverMismatch = field(default_factory=DriverMismatch)
    telemetry_uncertainty: TelemetryUncertainty = field(default_factory=TelemetryUncertainty)
    envelope_penalty: EnvelopePenalty = field(default_factory=EnvelopePenalty)
    staleness_penalty_ms: float = 0.0

    # Weights (explicit and tunable)
    # Platform risk weight raised to 1.0 — platform collapse = catastrophic.
    # For ground-effect GTP cars, an unstable platform is the DOMINANT risk.
    # Source: Taylor Funk (2026 calibration) — "rake/ride height dwarfs ARBs"
    w_platform: float = 1.0   # raised from 0.9 — platform is primary risk
    w_driver: float = 0.5     # lowered from 0.6 — secondary to physics
    w_uncertainty: float = 0.6  # lowered from 0.7 — less aggressive no-data penalty
    w_envelope: float = 0.7   # lowered from 0.8 — envelope is soft guidance, not hard
    w_staleness: float = 0.3  # lowered from 0.4 — staleness is least important
    empirical_penalty_ms: float = 0.0  # k-NN empirical score from SessionDatabase (76+ sessions)
    w_empirical: float = 0.40  # blend weight — empirical augments physics, never overrides it

    @property
    def total_score_ms(self) -> float:
        return (
            self.lap_gain_ms
            - self.w_platform * self.platform_risk.total_ms
            - self.w_driver * self.driver_mismatch.total_ms
            - self.w_uncertainty * self.telemetry_uncertainty.total_ms
            - self.w_envelope * self.envelope_penalty.total_ms
            - self.w_staleness * self.staleness_penalty_ms
            - self.w_empirical * self.empirical_penalty_ms
        )

    def summary(self) -> str:
        lines = [
            f"  Total score:           {self.total_score_ms:+.1f} ms",
            f"    Lap gain:            {self.lap_gain_ms:+.1f} ms",
            f"    Platform risk:       {-self.w_platform * self.platform_risk.total_ms:+.1f} ms "
            f"(bottom={self.platform_risk.bottoming_risk_ms:.0f}, "
            f"vortex={self.platform_risk.vortex_risk_ms:.0f}, "
            f"slider={self.platform_risk.slider_exhaustion_ms:.0f}, "
            f"rh_col={self.platform_risk.rh_collapse_risk_ms:.0f})",
            f"    Driver mismatch:     {-self.w_driver * self.driver_mismatch.total_ms:+.1f} ms",
            f"    Telemetry uncert:    {-self.w_uncertainty * self.telemetry_uncertainty.total_ms:+.1f} ms",
            f"    Envelope penalty:    {-self.w_envelope * self.envelope_penalty.total_ms:+.1f} ms",
            f"    Staleness:           {-self.w_staleness * self.staleness_penalty_ms:+.1f} ms",
            f"    Empirical (k-NN):    {-self.w_empirical * self.empirical_penalty_ms:+.1f} ms",
            f"  [hierarchy: rake/RH > heave_platform > LLTD(ARB) > dampers > camber]",
        ]
        return "\n".join(lines)


@dataclass
class CandidateEvaluation:
    """Complete evaluation of one candidate."""
    params: dict[str, float]
    family: str
    breakdown: ObjectiveBreakdown
    physics: PhysicsResult | None = None
    hard_vetoed: bool = False
    veto_reasons: list[str] = field(default_factory=list)
    soft_penalties: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        if self.hard_vetoed:
            return -1e9
        return self.breakdown.total_score_ms


class ObjectiveFunction:
    """Canonical multi-objective evaluator for setup candidates.

    Takes a candidate parameter set and returns a CandidateEvaluation
    with transparent scoring breakdown.

    When a TrackProfile is provided, runs full forward physics evaluation
    (excursion, LLTD, damping ratios, DF balance) for each candidate.
    """

    # Vortex burst threshold: dynamic front RH below this = aero stall.
    # This is now COMPUTED per wing angle from the aero map gradient
    # (see _compute_vortex_threshold_mm). The constant is the fallback.
    # Physics: vortex burst occurs when the ground effect vortex detaches from
    # the underfloor leading edge diffuser. At steeper wing angles, front DF
    # sensitivity to RH increases, so the safe minimum RH is higher.
    VORTEX_BURST_THRESHOLD_MM = 8.0  # fallback when aero map unavailable

    # Torsion bar → ARB coupling coefficient.
    #
    # ── Standard parallel model (Milliken RCVD / OptimumG "Bar Talk") ────────
    # In rigid-kinematic suspension theory, corner springs and ARB are PARALLEL
    # roll-stiffness elements:
    #   K_roll_total_front = K_roll_corners + K_roll_arb
    #   K_roll_corners = 2 * k_wheel[N/m] * (t_f/2)^2 * π/180   [N·m/deg]
    #   K_roll_arb     = K_arb_base * MR_arb^2                   [N·m/deg]
    # Wheel→rocker motion ratio φ = δ/r_arm is set by geometry alone, independent
    # of torsion-bar stiffness. So in a rigid kinematic model, the theoretically
    # correct coupling = 0.0.
    #
    # ── Why this constant is non-zero (empirical) ────────────────────────────
    # 0.25 was BACK-CALIBRATED from a single BMW Sebring IBT measurement:
    #   LLTD_ibr = 50.99% at (OD=13.9mm, FARB Soft/1, RARB Medium/3)
    # Without coupling (γ=0): predicted LLTD ≈ 50.0% — slightly too low.
    # γ=0.25 closes the ~1% gap, matching the observation.
    # This single-point calibration is insufficient to confirm a physical coupling;
    # the term may be compensating for other model offsets (roll-centre height,
    # tyre compliance, chassis torsional flex in the rocker mount).
    #
    # ── Physical mechanism (if real) ─────────────────────────────────────────
    # The most plausible second-order effect: the torsion bar mount undergoes small
    # elastic flex under roll load. A stiffer bar (larger OD) resists this flex,
    # preserving the ARB blade attachment geometry and marginally increasing the
    # ARB's effective motion ratio. Sign is POSITIVE (stiffer bar → coupling > 1.0),
    # consistent with the formula below.
    #
    # ── Coupling model ───────────────────────────────────────────────────────
    #   k_arb_effective = k_arb_base * coupling_factor
    #   coupling_factor = 1 + TORSION_ARB_COUPLING * ((OD/OD_ref)^4 - 1)
    #
    #   OD=OD_ref  → factor=1.0  (calibration baseline; no change)
    #   OD=16mm    → factor≈1.19 (+19% ARB stiffness vs OD=13.9mm)
    #   OD=11mm    → factor≈0.69 (-31% ARB stiffness)
    #
    # ── Validation protocol ──────────────────────────────────────────────────
    # To validate or update γ:
    # 1. Find BMW Sebring IBT sessions with DIFFERENT torsion OD (not 13.9mm)
    #    but the SAME ARB size/blade settings.
    # 2. Compute predicted LLTD with γ=0.0 vs γ=0.25 vs actual IBT LLTD.
    # 3. Best-fit γ = minimises |predicted - IBT| across the OD range.
    # 4. If best-fit γ ≈ 0 → remove coupling; use pure parallel model.
    # If that data becomes available, update this constant and research/physics-notes.md.
    # Expected physical range: γ ∈ [0.0, 0.30]. Current 0.25 is plausible but
    # requires multi-OD IBT confirmation. (research/physics-notes.md 2026-03-24)
    TORSION_ARB_COUPLING = 0.25  # empirical; see comment above

    def __init__(self, car, track, explore: bool = False):
        self.car = car
        self.track = track
        self.explore = explore  # when True: zero k-NN weight, no empirical anchoring
        self._surface = None  # lazy-loaded aero surface
        self._vortex_threshold_cache: dict[float, float] = {}  # wing_deg → threshold_mm
        # Empirical heave spring calibration — loads from real IBT telemetry data.
        # Falls back to physics model if no calibration file exists yet.
        from solver.heave_calibration import HeaveCalibration

        # Resolve car slug: "bmw m hybrid v8" → "bmw", "cadillac v-series.r" → "cadillac"
        _car_raw = getattr(car, "name", "") or getattr(car, "adapter_name", "") or str(car)
        _car_slug = _car_raw.lower().split()[0].replace("-", "").replace(".", "")
        self._car_slug = _car_slug

        # Resolve track slug: handles dict (track json), object with .name, or string
        if isinstance(track, dict):
            _track_raw = track.get("track_name") or track.get("name") or str(track)
        else:
            _track_raw = getattr(track, "name", None) or getattr(track, "track_name", None) or str(track)
        _track_slug = str(_track_raw).lower().split()[0].replace("-", "").replace("_", "")

        self._heave_cal = HeaveCalibration.load(_car_slug, _track_slug)
        self._measured = None   # set per-evaluation in evaluate()
        self._driver = None     # set per-evaluation in evaluate()
        self._session_db = None  # populated by set_session_context(); safe default for __main__ path

    def set_session_context(self, measured=None, driver=None) -> None:
        """Pre-stash measured telemetry and driver profile for all subsequent evaluations.

        Call this once before evaluate_batch() when running a grid search on a
        specific session, so k-NN scoring and signal-driven objective terms use
        the correct session data throughout.
        """
        self._measured = measured
        self._driver = driver

        # Load SessionDatabase for empirical k-NN cross-check.
        # When ≥3 sessions are available, the empirical prediction of telemetry
        # outcomes (front_rh_std_mm, understeer, LLTD from real sessions) augments
        # the physics model, catching systematic gaps that pure physics can't see.
        try:
            from solver.session_database import SessionDatabase
            self._session_db: "SessionDatabase | None" = SessionDatabase.load(
                _car_slug, _track_slug
            )
        except Exception:
            self._session_db = None

    def _get_surface(self, wing_deg: float | None = None):
        """Lazy-load aero surface for DF balance queries.

        Args:
            wing_deg: Wing angle to look up. If None, uses first available surface.
                      Pass params.get('wing_angle_deg') per candidate for accuracy.
        """
        try:
            from aero_model import load_car_surfaces
            surfaces = load_car_surfaces(self.car.canonical_name)
            if not surfaces:
                return None
            if wing_deg is not None:
                # Find closest available wing map to requested angle
                best = min(surfaces.keys(), key=lambda w: abs(w - wing_deg))
                return surfaces[best]
            # Fallback: cache a default surface for callers that don't pass wing_deg
            if self._surface is None:
                default_wing = self.car.wing_angles[0] if self.car.wing_angles else 17.0
                best = min(surfaces.keys(), key=lambda w: abs(w - default_wing))
                self._surface = surfaces[best]
            return self._surface
        except Exception:
            return None

    def _torsion_arb_coupling_factor(self, front_torsion_od: float) -> float:
        """Compute the coupling multiplier applied to k_arb_front.

        Standard suspension theory (Milliken RCVD, OptimumG "Bar Talk") treats
        corner springs and ARB as parallel roll-stiffness elements — changing torsion
        bar OD has NO direct effect on ARB stiffness in a rigid kinematic model.

        However, iOptimal applies an empirical coupling factor (TORSION_ARB_COUPLING =
        0.25) that was back-calibrated from a single BMW Sebring IBT data point
        (LLTD = 50.99% at OD=13.9mm, FARB Soft/1, RARB Medium/3). This term corrects
        a ~1% LLTD prediction gap that likely arises from rocker mount compliance,
        chassis torsional flex, or other non-modelled second-order effects.

        Physics notes: research/physics-notes.md §2026-03-24 Topic G documents the
        derivation in detail and specifies the IBT validation protocol needed to
        confirm whether γ=0.25 is correct or should be reduced to 0.0.

        Args:
            front_torsion_od: Current torsion bar OD in mm.

        Returns:
            Dimensionless coupling factor (1.0 at reference OD = no correction).
        """
        od_ref = self.car.corner_spring.front_torsion_od_ref_mm
        if od_ref <= 0:
            return 1.0
        # Relative stiffness ratio: (OD/OD_ref)^4 (same OD^4 law as wheel rate)
        stiffness_ratio = (front_torsion_od / od_ref) ** 4
        return 1.0 + self.TORSION_ARB_COUPLING * (stiffness_ratio - 1.0)

    def _compute_vortex_threshold_mm(self, wing_deg: float) -> float:
        """Compute wing-specific minimum safe front RH from aero map gradient.

        Physics basis:
          The vortex burst threshold is NOT a fixed value — it depends on the
          gradient of DF balance with respect to front RH at the operating point.
          At steeper wing angles (higher downforce), the aero system is more
          sensitive to front RH changes, so the "cliff edge" of vortex separation
          is higher.

          Approach:
          1. Load the aero map for this wing angle
          2. Compute ∂(balance)/∂(front_RH) at the nominal rear RH (say 42mm)
          3. The threshold rises with this gradient: steeper = higher minimum RH

          Empirical formula (derived from BMW aero maps at multiple wing angles):
            threshold_mm = base_threshold + gradient_factor * |∂balance/∂rh|
            where base_threshold = 6.0mm (physical floor from tunnel test data)
            and gradient_factor = 2.0 mm / (pct/mm gradient)

          If no aero map is available, returns the class-level fallback constant.

        Args:
            wing_deg: Wing angle in degrees

        Returns:
            Minimum safe front RH in mm (below this = vortex burst risk)
        """
        if wing_deg in self._vortex_threshold_cache:
            return self._vortex_threshold_cache[wing_deg]

        threshold = self.VORTEX_BURST_THRESHOLD_MM  # fallback

        try:
            import pathlib
            import json

            # Try to load aero map for this car+wing
            car_name = self.car.canonical_name
            aero_path = pathlib.Path("data/aero-maps") / f"{car_name}_wing_{wing_deg:.1f}.json"
            if not aero_path.exists():
                # Try nearest available wing
                available = sorted(pathlib.Path("data/aero-maps").glob(f"{car_name}_wing_*.json"))
                if available:
                    # Pick closest wing angle
                    def _wing(p: pathlib.Path) -> float:
                        return float(p.stem.split("_wing_")[1])
                    aero_path = min(available, key=lambda p: abs(_wing(p) - wing_deg))
                else:
                    self._vortex_threshold_cache[wing_deg] = threshold
                    return threshold

            data = json.loads(aero_path.read_text())
            front_rh_axis = data.get("front_rh_mm", [])
            rear_rh_axis = data.get("rear_rh_mm", [])
            balance_table = data.get("balance_pct", [])

            if not front_rh_axis or not balance_table:
                self._vortex_threshold_cache[wing_deg] = threshold
                return threshold

            # Pick a nominal rear RH column (use index closest to 42mm rear RH)
            # NOTE: per CLAUDE.md, axis labels are swapped in xlsx — but the JSON
            # is stored with physical convention: front_rh_mm = rows, rear_rh_mm = cols
            target_rear_rh = 42.0
            rear_col = min(range(len(rear_rh_axis)),
                          key=lambda i: abs(rear_rh_axis[i] - target_rear_rh))

            # Compute ∂balance/∂front_rh across the lower portion of the RH range.
            # The aero map starts at 25mm. We use the lowest third of the RH range
            # where the sensitivity is highest (nonlinear near ground effect cliff).
            n_pts = len(front_rh_axis)
            # Use bottom 25% of RH range for gradient (most sensitive region)
            low_n = max(4, n_pts // 4)
            low_rh_idx = list(range(low_n))

            # Compute gradient ∂balance/∂front_rh in the low-RH danger zone
            b_vals = [balance_table[i][rear_col] for i in low_rh_idx
                      if i < len(balance_table) and rear_col < len(balance_table[i])]
            rh_vals = [front_rh_axis[i] for i in low_rh_idx]

            if len(b_vals) >= 2:
                # Gradient: Δbalance / Δrh (pct per mm)
                # Decreasing balance at lower RH = vortex risk approaching
                gradients = []
                for j in range(len(b_vals) - 1):
                    drh = rh_vals[j+1] - rh_vals[j]
                    db = b_vals[j+1] - b_vals[j]
                    if abs(drh) > 1e-6:
                        gradients.append(abs(db / drh))
                if gradients:
                    max_gradient = max(gradients)
                    # Steeper gradient → higher safe minimum RH
                    # base=6mm, scale=2.0mm per unit gradient (pct/mm)
                    threshold = max(6.0, 6.0 + 2.0 * max_gradient)
                    threshold = min(threshold, 12.0)  # cap at 12mm (physical limit)

        except Exception:
            pass  # any error → return fallback

        self._vortex_threshold_cache[wing_deg] = threshold
        return threshold

    def _compute_lltd_fuel_window(
        self,
        params: dict[str, float],
        fuel_start_l: float = 89.0,
        fuel_end_l: float = 20.0,
    ) -> tuple[float, float, float]:
        """Compute LLTD at race start and end of stint fuel loads.

        Physics basis:
          Front-rear weight balance shifts as fuel burns off from the tank.
          If the tank is behind the rear axle (or at rear-biased CG), burning
          fuel moves weight distribution forward, increasing front weight fraction.
          This shifts the optimal LLTD target — if setup is optimized only for
          full fuel, it will be wrong at the end of a stint.

          LLTD_target = W_front + λ * 0.05
          where W_front = front weight fraction (changes with fuel)
          and λ = tyre load sensitivity (constant per car)

          The key insight: LLTD from springs/ARBs is FIXED during a stint, but the
          OPTIMAL target shifts as fuel burns. At low fuel, the target moves, and if
          the setup is wrong direction, the car gets worse over the stint.

          We score the WORST case LLTD error (max of start vs. end) to penalize
          setups that are tuned only for one fuel condition.

        Returns:
            (lltd_start_error, lltd_end_error, worst_lltd_error)
        """
        car = self.car

        # Compute weight distributions at start and end fuel
        mass_start = car.total_mass(fuel_start_l)
        mass_end = car.total_mass(fuel_end_l)

        # Front weight fraction at each fuel load
        # If car has fuel_cg_x data, use it; otherwise assume fuel is at mid-car CG
        # BMW fuel tank is slightly rear-biased from center
        front_pct_start = car.weight_dist_front
        front_pct_end = car.weight_dist_front

        if hasattr(car, 'fuel_cg_fraction_front') and car.fuel_cg_fraction_front is not None:
            # Compute actual CG shift from fuel burn
            fuel_burned = fuel_start_l - fuel_end_l
            fuel_mass_burned = fuel_burned * car.fuel_density_kg_per_l
            fuel_front_frac = car.fuel_cg_fraction_front
            # Wf_end = (Wf_start * m_start - fuel_front_frac * fuel_mass_burned) / m_end
            front_mass_start = front_pct_start * mass_start
            front_mass_end = front_mass_start - fuel_front_frac * fuel_mass_burned
            front_pct_end = front_mass_end / mass_end
        else:
            # Simplified: use BMW empirical data (fuel tank slightly rear of midship)
            # From IBT observations: RH slightly increases as fuel burns at Sebring
            # Approximate: front_pct changes by ~0.3% over 89→20L stint
            front_pct_end = front_pct_start + 0.003  # fuel behind CG → front gets lighter

        # LLTD from roll stiffness (same spring/ARB values → fixed during stint)
        front_heave_nmm = params.get("front_heave_spring_nmm", 50.0)
        rear_third_nmm = params.get("rear_third_spring_nmm", 450.0)
        rear_spring_nmm = params.get("rear_spring_rate_nmm", 160.0)
        front_torsion_od = params.get("front_torsion_od_mm",
                                       car.corner_spring.front_torsion_od_options[0]
                                       if car.corner_spring.front_torsion_od_options else 14.34)
        front_arb_blade = int(params.get("front_arb_blade", 1))
        rear_arb_blade = int(params.get("rear_arb_blade", 3))

        c_torsion = car.corner_spring.front_torsion_c
        front_wheel_rate = c_torsion * (front_torsion_od ** 4)
        mr_rear = car.corner_spring.rear_motion_ratio
        rear_wheel_rate = rear_spring_nmm * (mr_rear ** 2)

        arb = car.arb
        t_f = arb.track_width_front_mm / 2000.0
        t_r = arb.track_width_rear_mm / 2000.0
        k_roll_springs_front = 2.0 * (front_wheel_rate * 1000.0) * t_f**2 * (math.pi / 180.0)
        k_roll_springs_rear = 2.0 * (rear_wheel_rate * 1000.0) * t_r**2 * (math.pi / 180.0)
        k_arb_front_base = arb.front_roll_stiffness(arb.front_baseline_size, front_arb_blade)
        k_arb_rear = arb.rear_roll_stiffness(arb.rear_baseline_size, rear_arb_blade)
        # Empirical coupling: γ=0.25 back-calibrated from BMW Sebring IBT LLTD.
        # Theoretically 0.0 (parallel elements, rigid kinematics). See TORSION_ARB_COUPLING
        # class constant and research/physics-notes.md §2026-03-24 for derivation.
        k_arb_front = k_arb_front_base * self._torsion_arb_coupling_factor(front_torsion_od)

        k_front_total = k_roll_springs_front + k_arb_front
        k_rear_total = k_roll_springs_rear + k_arb_rear
        lltd_actual = k_front_total / (k_front_total + k_rear_total) if (k_front_total + k_rear_total) > 0 else 0.5

        # LLTD targets at each fuel level.
        # Use car.measured_lltd_target when available (IBT-calibrated override).
        # For fuel window analysis, we still model the shift with fuel load,
        # but anchor to the measured target instead of the theoretical formula.
        tyre_sens = getattr(car, "tyre_load_sensitivity", 0.20)
        _measured_lltd_target = getattr(car, "measured_lltd_target", None)
        if _measured_lltd_target is not None:
            # Anchor to measured target; apply fuel-load shift on top
            _shift = front_pct_end - front_pct_start  # how much W_front changes
            target_start = _measured_lltd_target
            target_end = _measured_lltd_target + _shift
        else:
            target_start = front_pct_start + (tyre_sens / 0.20) * 0.05
            target_end = front_pct_end + (tyre_sens / 0.20) * 0.05

        err_start = abs(lltd_actual - target_start)
        err_end = abs(lltd_actual - target_end)

        return err_start, err_end, max(err_start, err_end)

    def evaluate_physics(self, params: dict[str, float]) -> PhysicsResult:
        """Forward-evaluate physics for a candidate parameter set.

        Computes:
        - Front/rear excursion and bottoming margin from heave/third rates
        - LLTD from spring rates + ARB stiffness
        - Damping ratios from click positions
        - DF balance from aero maps (if available)
        """
        car = self.car
        track = self.track
        result = PhysicsResult()

        # ── Extract parameters ──────────────────────────────────────────
        front_heave_nmm = params.get("front_heave_spring_nmm", 50.0)
        rear_third_nmm = params.get("rear_third_spring_nmm", 450.0)
        rear_spring_nmm = params.get("rear_spring_rate_nmm", 160.0)
        front_torsion_od = params.get("front_torsion_od_mm",
                                       car.corner_spring.front_torsion_od_options[0]
                                       if car.corner_spring.front_torsion_od_options else 14.34)
        front_camber = params.get("front_camber_deg", -3.5)
        rear_camber = params.get("rear_camber_deg", -2.5)
        front_arb_blade = int(params.get("front_arb_blade", 1))
        rear_arb_blade = int(params.get("rear_arb_blade", 3))

        # Damper clicks
        f_ls_comp = int(params.get("front_ls_comp", 7))
        f_ls_rbd = int(params.get("front_ls_rbd", 7))
        f_hs_comp = int(params.get("front_hs_comp", 5))
        f_hs_rbd = int(params.get("front_hs_rbd", 5))
        r_ls_comp = int(params.get("rear_ls_comp", 6))
        r_ls_rbd = int(params.get("rear_ls_rbd", 7))
        r_hs_comp = int(params.get("rear_hs_comp", 3))
        r_hs_rbd = int(params.get("rear_hs_rbd", 3))

        # ── Wheel rates ─────────────────────────────────────────────────
        c_torsion = car.corner_spring.front_torsion_c
        front_wheel_rate = c_torsion * (front_torsion_od ** 4)
        mr_rear = car.corner_spring.rear_motion_ratio
        rear_wheel_rate = rear_spring_nmm * (mr_rear ** 2)
        result.front_wheel_rate_nmm = front_wheel_rate
        result.rear_wheel_rate_nmm = rear_wheel_rate

        # ── Excursion & bottoming (real physics) ────────────────────────
        if isinstance(track, TrackProfile):
            # ── Shock velocity percentile selection ──────────────────────
            # P99 is used for bottoming risk (survive worst-case isolated bumps).
            # P95 is used for vortex stall margin (sustained floor dynamics;
            # p99 caused 43% false veto rate on real BMW Sebring setups because
            # an isolated p99 spike does not cause sustained vortex burst).
            # The percentile for vortex is configurable per car via
            # car.vortex_excursion_pctile ("p95" default, "p99" for legacy behaviour).
            v_p99_front = (track.shock_vel_p99_front_clean_mps
                          if getattr(track, "shock_vel_p99_front_clean_mps", 0) > 0
                          else track.shock_vel_p99_front_mps)
            v_p99_rear = (track.shock_vel_p99_rear_clean_mps
                         if getattr(track, "shock_vel_p99_rear_clean_mps", 0) > 0
                         else track.shock_vel_p99_rear_mps)

            # Vortex excursion uses car-specific percentile (p95 by default)
            _vortex_pctile = getattr(car, "vortex_excursion_pctile", "p95")
            if _vortex_pctile == "p95":
                v_vortex_front = (track.shock_vel_p95_front_clean_mps
                                  if getattr(track, "shock_vel_p95_front_clean_mps", 0) > 0
                                  else track.shock_vel_p95_front_mps)
            else:
                v_vortex_front = v_p99_front  # legacy behaviour

            m_eff_front = car.heave_spring.front_m_eff_kg
            m_eff_rear = car.heave_spring.rear_m_eff_kg
            tyre_vr = getattr(car, "tyre_vertical_rate_nmm", None)

            # Front excursion at p99 — for bottoming margin (worst-case bump)
            # Guard against k=0 which returns 0 (wrong: should be ∞)
            # GTP heave spring ≥ 20 N/mm in practice. k=0 is physically degenerate.
            front_heave_clamped = max(5.0, front_heave_nmm)  # prevent div/zero physics
            result.front_excursion_mm = damped_excursion_mm(
                v_p99_front, m_eff_front, front_heave_clamped,
                tyre_vertical_rate_nmm=tyre_vr,
                parallel_wheel_rate_nmm=front_wheel_rate * 0.5,
            )
            # Override: if heave spring < 20 N/mm, cap excursion at full travel (30mm)
            # so sigma reflects the true aero instability risk
            if front_heave_nmm < 20.0:
                result.front_excursion_mm = max(result.front_excursion_mm, 30.0)

            # Front excursion at vortex percentile (p95) — for stall margin
            _front_vortex_excursion_mm = damped_excursion_mm(
                v_vortex_front, m_eff_front, front_heave_clamped,
                tyre_vertical_rate_nmm=tyre_vr,
                parallel_wheel_rate_nmm=front_wheel_rate * 0.5,
            )

            # Rear excursion at p99 — for bottoming margin
            rear_third_clamped = max(5.0, rear_third_nmm)
            result.rear_excursion_mm = damped_excursion_mm(
                v_p99_rear, m_eff_rear, rear_third_clamped,
                tyre_vertical_rate_nmm=tyre_vr,
                parallel_wheel_rate_nmm=rear_wheel_rate * 0.5,
            )

            # Dynamic ride heights (use car compression model when available)
            # static_front_rh - aero_compression → mean floor height at speed.
            # Fallback to 19.0mm if model not available.
            _static_f = car.pushrod.front_pinned_rh_mm
            _comp_f = car.aero_compression.front_compression_mm
            dyn_front_rh = max(5.0, _static_f - _comp_f)
            dyn_rear_rh = 42.0  # rear not used for vortex; fallback is fine
            result.front_bottoming_margin_mm = dyn_front_rh - result.front_excursion_mm
            result.rear_bottoming_margin_mm = dyn_rear_rh - result.rear_excursion_mm

            # Stall margin: distance from wing-specific vortex burst threshold.
            # Uses p95 excursion (car.vortex_excursion_pctile) NOT p99.
            # Physics: vortex burst is a sustained floor proximity effect — sustained
            # mean floor position matters more than a single extreme bump event.
            # Using p99 over-penalises setups that survive isolated bumps fine.
            # Physics: at steeper wing angles the aero sensitivity to RH increases
            # → the safe minimum RH is higher than at shallow wing angles.
            # _compute_vortex_threshold_mm() reads the aero map gradient to determine
            # this dynamically rather than using a fixed 8mm constant.
            wing_deg = float(params.get("wing_angle_deg",
                             car.wing_angles[0] if car.wing_angles else 17.0))
            vortex_thresh = self._compute_vortex_threshold_mm(wing_deg)
            result.stall_margin_mm = (dyn_front_rh - _front_vortex_excursion_mm
                                      - vortex_thresh)

            # Platform variance (sigma = p99 / 2.33 for Gaussian)
            # Override with empirical calibration when available — the real IBT-measured
            # sigma is more accurate than the synthetic excursion model.
            # The U-shape (30→90 improving, 900 worsening) is from 46 real sessions.
            synthetic_sigma_f = result.front_excursion_mm / 2.33
            synthetic_sigma_r = result.rear_excursion_mm / 2.33
            cal_sigma_f = self._heave_cal.predict_sigma(
                params.get("front_heave_spring_nmm", 50.0)
            )
            # Use empirical if calibration has data, else fall back to synthetic
            if self._heave_cal.summary:
                result.front_sigma_mm = cal_sigma_f
            else:
                result.front_sigma_mm = synthetic_sigma_f
            result.rear_sigma_mm = synthetic_sigma_r

        # ── LLTD (real roll stiffness calculation) ──────────────────────
        arb = car.arb
        t_f = arb.track_width_front_mm / 2000.0  # half track width in meters
        t_r = arb.track_width_rear_mm / 2000.0

        # Corner spring roll stiffness: K = 2 * k_wheel(N/m) * t_half² * π/180
        k_roll_springs_front = 2.0 * (front_wheel_rate * 1000.0) * t_f**2 * (math.pi / 180.0)
        k_roll_springs_rear = 2.0 * (rear_wheel_rate * 1000.0) * t_r**2 * (math.pi / 180.0)

        # ARB contribution — with empirical torsion bar coupling (γ=0.25).
        # Standard theory (RCVD): parallel model, coupling = 0.0. The 0.25 is an
        # empirical correction back-calibrated from BMW Sebring IBT LLTD=50.99%.
        # May compensate for rocker mount flex or other non-modelled compliance.
        # See TORSION_ARB_COUPLING class constant for full derivation and validation
        # protocol. (research/physics-notes.md §2026-03-24 Topic G)
        # ARB size: may come as ordinal int (0=Soft,1=Medium,2=Stiff) or string label
        _f_arb_size_raw = params.get("front_arb_size", arb.front_baseline_size)
        _r_arb_size_raw = params.get("rear_arb_size", arb.rear_baseline_size)
        def _resolve_arb_size(val, labels, baseline):
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                idx = int(round(float(val)))
                if labels and 0 <= idx < len(labels):
                    return labels[idx]
            return val if val else baseline
        front_arb_size = _resolve_arb_size(_f_arb_size_raw, arb.front_size_labels, arb.front_baseline_size)
        rear_arb_size = _resolve_arb_size(_r_arb_size_raw, arb.rear_size_labels, arb.rear_baseline_size)
        k_arb_front_base = arb.front_roll_stiffness(front_arb_size, front_arb_blade)
        k_arb_rear = arb.rear_roll_stiffness(rear_arb_size, rear_arb_blade)
        k_arb_front = k_arb_front_base * self._torsion_arb_coupling_factor(front_torsion_od)

        k_front_total = k_roll_springs_front + k_arb_front
        k_rear_total = k_roll_springs_rear + k_arb_rear
        result.k_roll_front = k_front_total
        result.k_roll_rear = k_rear_total

        if k_front_total + k_rear_total > 0:
            result.lltd = k_front_total / (k_front_total + k_rear_total)
        else:
            result.lltd = 0.5

        # LLTD target — use IBT-calibrated measured target when available.
        # Theory: LLTD_target = W_front + (λ/λ_ref)*0.05  (OptimumG formula)
        # In practice, some cars run intentionally rear-biased LLTD for rotation.
        # car.measured_lltd_target overrides the theoretical formula when set.
        # BMW calibration: theory=0.528, measured=0.38-0.43 → use 0.41.
        # Source: validation/objective_validation.md Section 6.
        tyre_sens = getattr(car, "tyre_load_sensitivity", 0.20)
        _measured_lltd_target = getattr(car, "measured_lltd_target", None)
        if _measured_lltd_target is not None:
            target_lltd = _measured_lltd_target
        else:
            target_lltd = car.weight_dist_front + (tyre_sens / 0.20) * 0.05
        result.lltd_error = abs(result.lltd - target_lltd)

        # ── Damping ratios (real physics) ───────────────────────────────
        damper = car.damper
        # Modal spring rates (corner + heave in parallel, then series with tyre)
        k_modal_front = front_wheel_rate + front_heave_nmm * 0.5  # simplified modal
        k_modal_rear = rear_wheel_rate + rear_third_nmm * 0.5

        # Quarter-car masses (use 89L fuel as default)
        total_mass = car.total_mass(89.0)
        front_mass = total_mass * car.weight_dist_front / 2.0
        rear_mass = total_mass * (1.0 - car.weight_dist_front) / 2.0

        # Critical damping: c_crit = 2 * sqrt(k * m)
        c_crit_front = 2.0 * math.sqrt(k_modal_front * 1000.0 * front_mass)
        c_crit_rear = 2.0 * math.sqrt(k_modal_rear * 1000.0 * rear_mass)

        # LS/HS damping: harmonic mean of comp + rebound per-cycle energy dissipation.
        # Rationale: one full oscillation cycle has both a compression and rebound stroke.
        # Effective damping coefficient per cycle = harmonic mean of c_comp and c_rbd:
        #   c_eff = 2 * c_comp * c_rbd / (c_comp + c_rbd)
        # Previous model used c_comp only → ζ overestimated by ~30-40% when rbd < comp.
        # Harmonic mean naturally captures asymmetric damping (GTP always comp > rbd).
        # Source: Dixon "The Shock Absorber Handbook" §7.4 (per-cycle energy method).

        def _c_eff_harmonic(comp_clicks: float, rbd_clicks: float,
                             force_per_click: float, v_ref: float) -> float:
            """Effective damping coefficient via harmonic mean (symmetric energy dissipation)."""
            c_c = (max(0.5, comp_clicks) * force_per_click) / v_ref
            c_r = (max(0.5, rbd_clicks) * force_per_click) / v_ref
            return 2.0 * c_c * c_r / (c_c + c_r)

        v_ls_ref = 0.025  # 25 mm/s — LS reference velocity

        c_ls_front = _c_eff_harmonic(
            params.get("front_ls_comp", f_ls_comp),
            params.get("front_ls_rbd", f_ls_rbd),
            damper.ls_force_per_click_n, v_ls_ref,
        )
        c_ls_rear = _c_eff_harmonic(
            params.get("rear_ls_comp", r_ls_comp),
            params.get("rear_ls_rbd", r_ls_rbd),
            damper.ls_force_per_click_n, v_ls_ref,
        )

        result.zeta_ls_front = c_ls_front / c_crit_front if c_crit_front > 0 else 0
        result.zeta_ls_rear = c_ls_rear / c_crit_rear if c_crit_rear > 0 else 0

        # HS damping from clicks
        if isinstance(track, TrackProfile):
            v_hs_front = max(track.shock_vel_p95_front_mps, 0.050)
            v_hs_rear = max(track.shock_vel_p95_rear_mps, 0.050)
        else:
            v_hs_front = 0.120
            v_hs_rear = 0.150

        c_hs_front = _c_eff_harmonic(
            params.get("front_hs_comp", f_hs_comp),
            params.get("front_hs_rbd", f_hs_rbd),
            damper.hs_force_per_click_n, v_hs_front,
        )
        c_hs_rear = _c_eff_harmonic(
            params.get("rear_hs_comp", r_hs_comp),
            params.get("rear_hs_rbd", r_hs_rbd),
            damper.hs_force_per_click_n, v_hs_rear,
        )

        result.zeta_hs_front = c_hs_front / c_crit_front if c_crit_front > 0 else 0
        result.zeta_hs_rear = c_hs_rear / c_crit_rear if c_crit_rear > 0 else 0

        # ── DF balance (aero map lookup if available) ───────────────────
        # Use per-candidate wing angle for correct surface selection.
        # Clamp dynamic RH to the aero map's available range to avoid
        # extrapolation errors (map floor is 25mm front; our operating
        # range 19-30mm often sits below this → clamp to map minimum).
        wing_deg_candidate = float(params.get(
            "wing_angle_deg", car.wing_angles[0] if car.wing_angles else 17.0
        ))
        surface = self._get_surface(wing_deg=wing_deg_candidate)
        if surface is not None:
            try:
                # Derive dynamic RH from static pushrod offsets + car geometry.
                # Fallback to typical values if pushrod params not present.
                _fp = float(params.get("front_pushrod_offset_mm", -26.0))
                _rp = float(params.get("rear_pushrod_offset_mm", -18.0))
                # Approximate static RH from pushrod offset (0.096 mm/mm sensitivity)
                _rh_model = car.rh_model if hasattr(car, "rh_model") else None
                if _rh_model is not None:
                    _static_f = _rh_model.front_base_rh_mm + _rh_model.front_pushrod_to_rh * _fp
                    _static_r = _rh_model.rear_base_rh_mm + _rh_model.rear_pushrod_to_rh * _rp
                    # Dynamic is typically 3-4mm lower than static (aero compression at speed)
                    dyn_f = max(_static_f - 4.0, float(surface.front_rh[0]))
                    dyn_r = max(_static_r - 4.0, float(surface.rear_rh[0]))
                else:
                    # No rh_model — try direct static RH params if caller provided them.
                    # These come from observed/IBT data and are more accurate than defaults.
                    # Dynamic ≈ static − 4mm (aero compression at speed).
                    _srh_f = float(params.get("front_rh_static_mm", 0.0))
                    _srh_r = float(params.get("rear_rh_static_mm", 0.0))
                    if _srh_f > 0.0 and _srh_r > 0.0:
                        dyn_f = max(_srh_f - 4.0, float(surface.front_rh[0]))
                        dyn_r = max(_srh_r - 4.0, float(surface.rear_rh[0]))
                    else:
                        # True fallback: clamp to map floor
                        dyn_f = max(23.0, float(surface.front_rh[0]))
                        dyn_r = max(42.0, float(surface.rear_rh[0]))
                # BMW has aero_axes_swapped=True — convert to aero map coordinates
                # before querying (map x-axis = actual rear RH, y-axis = actual front RH).
                af, ar = car.to_aero_coords(dyn_f, dyn_r)
                result.df_balance_pct = surface.df_balance(af, ar)
                result.ld_ratio = surface.lift_drag(af, ar)
                result.df_balance_error_pct = abs(
                    result.df_balance_pct - car.default_df_balance_pct
                )
            except Exception:
                pass

        return result

    def evaluate(
        self,
        params: dict[str, float],
        family: str = "unknown",
        solver_result: dict | None = None,
        measured=None,
        driver_profile=None,
        session_count: int = 0,
    ) -> CandidateEvaluation:
        """Evaluate a candidate setup with full physics.

        Args:
            params: Candidate parameter values (canonical keys)
            family: Candidate family name
            solver_result: Solver step outputs (if available, augments physics)
            measured: MeasuredState from telemetry (if available)
            driver_profile: DriverProfile from analyzer (if available)
            session_count: Number of sessions used for calibration
        """
        # Stash measured/driver for use in sub-methods (avoids signature churn)
        self._measured = measured
        self._driver = driver_profile

        # In explore mode: zero empirical weight so k-NN doesn't anchor to past setups
        _w_empirical = 0.0 if self.explore else 0.40

        # Run forward physics evaluation
        physics = self.evaluate_physics(params)

        breakdown = ObjectiveBreakdown()
        if self.explore:
            breakdown.w_empirical = 0.0  # explore mode: pure physics, no k-NN anchoring
        veto_reasons: list[str] = []
        soft_penalties: list[str] = []

        # ── 1. Lap gain from physics ────────────────────────────────────
        breakdown.lap_gain_ms = self._estimate_lap_gain(params, physics)

        # ── 2. Platform risk from physics ───────────────────────────────
        breakdown.platform_risk = self._compute_platform_risk(
            params, physics, veto_reasons, soft_penalties
        )

        # ── 3. Driver mismatch ──────────────────────────────────────────
        breakdown.driver_mismatch = self._compute_driver_mismatch(
            params, physics, driver_profile, soft_penalties
        )

        # ── 4. Telemetry uncertainty ────────────────────────────────────
        breakdown.telemetry_uncertainty = self._compute_telemetry_uncertainty(
            measured, session_count, soft_penalties
        )

        # ── 5. Envelope penalty ─────────────────────────────────────────
        breakdown.envelope_penalty = self._compute_envelope_penalty(
            params, physics, soft_penalties
        )

        # ── 6. Staleness ────────────────────────────────────────────────
        breakdown.staleness_penalty_ms = 0.0

        # ── 7. Empirical cross-check from SessionDatabase ────────────────
        # k-NN prediction from real BMW Sebring sessions (76+) — scores each
        # candidate by how well its predicted telemetry matches target ranges.
        # Weight 0.40: empirical augments physics scoring without overriding it.
        # When physics and empirical agree → lower uncertainty; when they
        # disagree → the soft_penalties list captures the discrepancy for review.
        if not self.explore and self._session_db is not None and len(self._session_db) >= 3:
            try:
                k_nn = min(7, len(self._session_db))
                emp_pred = self._session_db.predict(params, k=k_nn)
                emp_result = self._session_db.score(emp_pred)
                breakdown.empirical_penalty_ms = emp_result.total_penalty_ms
                if emp_result.total_penalty_ms > 25.0:
                    top_bad = [m for m in emp_result.metrics if m.status != "ok"][:3]
                    issues = ", ".join(
                        f"{m.metric.split('_')[0]}={m.predicted:.2f}" for m in top_bad
                    )
                    soft_penalties.append(
                        f"Empirical k-NN ({emp_pred.k_used} sessions): "
                        f"penalty={emp_result.total_penalty_ms:.0f}ms — {issues}"
                    )
            except Exception:
                pass  # empirical scoring is non-critical — never break the pipeline

        return CandidateEvaluation(
            params=params,
            family=family,
            breakdown=breakdown,
            physics=physics,
            hard_vetoed=len(veto_reasons) > 0,
            veto_reasons=veto_reasons,
            soft_penalties=soft_penalties,
        )

    def _estimate_lap_gain(
        self, params: dict[str, float], physics: PhysicsResult,
    ) -> float:
        """Estimate lap time gain from real physics evaluation.

        PERFORMANCE HIERARCHY for ground-effect GTP/LMDh cars
        (validated by Taylor Funk, professional GTP driver/engineer, 2026):

        1. RAKE / RIDE HEIGHTS  ← dominant, dwarfs everything else
           Front RH controls underfloor suction. Each mm below 30mm minimum
           floor is hard-vetoed. Each mm deviation from optimal rake costs
           15-40ms. This term alone can swing 1-2+ seconds.

        2. WING ANGLE  ← seconds/lap on some tracks (fixed in current run)

        3. HEAVE / THIRD SPRINGS  ← aero platform stability, 300-800ms range
           Controls ride height variance (σ_front) at speed. An unstable
           platform (σ > 3mm) loses underfloor suction consistency → huge
           aero loss. This DWARFS mechanical grip — GTP cars deliberately
           run stiff heave springs (40-120 N/mm) for platform, NOT for grip.
           ⚠️  OLD MODEL was WRONG: "softer = more grip" → that's road cars.
               For GTP: soft heave = platform collapse = 300-800ms loss.

        4. CORNER SPRINGS / DIFF / TYRES  ← foundational mechanical, ~10-30ms

        5. ARB DIAMETER (full size steps)  ← 30-80ms per size step
           Full Soft→Medium→Hard transitions shift LLTD by 3-8%, costing
           36-96ms per step — meaningful, but well below springs/rake.

        6. ARB BLADES  ← fine trim, realistic 5-15ms per click
           NOT 33ms/click. Each blade step shifts LLTD ≈ 0.5-1.0%,
           at ~12ms/1% = 6-12ms/click. Source: OptimumG + Taylor Funk.

        Reference: Milliken & Milliken RCVD Ch.18 (LLTD theory);
        OptimumG ground effect platform analysis; Taylor Funk (2026)
        """
        gain = 0.0

        # ═══════════════════════════════════════════════════════════════
        # TIER 1: HEAVE / THIRD SPRING PLATFORM STABILITY
        # Dominant lap time driver for ground-effect cars.
        # σ_front is computed in evaluate_physics() from heave spring rate
        # + track p99 shock velocity via damped_excursion_mm().
        # ═══════════════════════════════════════════════════════════════

        sigma_f = physics.front_sigma_mm  # [mm]
        # Threshold: stable platform = σ < 3mm at speed
        # Below threshold: no aero platform penalty
        # Above threshold: each extra mm costs ~100ms (recalibrated 2026-03-22).
        #   IBT correlation: dyn_frh r=+0.235 with lap_time across 63 sessions.
        #   Observed 4mm dyn_frh spread (19→23mm) ≈ 0.5s lap time spread
        #   → empirical ≈ 125ms/mm. Using 100ms/mm (conservative — some variance
        #   in dyn_frh is driver/conditions, not pure setup).
        #   Raised from 80ms/mm (original GTP literature estimate, pre-IBT data).
        # Source: Taylor Funk 63-session Sebring IBT dataset (2026-03-22).
        SIGMA_F_STABLE_MM = 3.0
        SIGMA_F_MS_PER_MM = 100.0  # ms per mm above stable threshold [ms/mm] — was 80
        if sigma_f > SIGMA_F_STABLE_MM:
            platform_loss = (sigma_f - SIGMA_F_STABLE_MM) * SIGMA_F_MS_PER_MM
            gain -= min(800.0, platform_loss)

        # Rear platform (third spring): less sensitive than front — rear
        # diffuser is less ground-coupled than front underfloor in GTP
        sigma_r = physics.rear_sigma_mm  # [mm]
        SIGMA_R_STABLE_MM = 5.0
        SIGMA_R_MS_PER_MM = 40.0  # half the front sensitivity [ms/mm]
        if sigma_r > SIGMA_R_STABLE_MM:
            gain -= min(300.0, (sigma_r - SIGMA_R_STABLE_MM) * SIGMA_R_MS_PER_MM)

        # ── HEAVE CALIBRATION UNCERTAINTY PENALTY ───────────────────────
        # The empirical sigma is now wired into physics.front_sigma_mm and
        # flows into _compute_platform_risk (rh_collapse_risk_ms).
        # Here we add an ADDITIONAL uncertainty penalty for exploring beyond
        # the calibrated range — the solver is free to go there, but pays an
        # increasing cost reflecting reduced confidence in the prediction.
        # When you actually run 380 N/mm and drop the IBT, uncertainty drops
        # to ~0.15mm and the true sigma (whatever it is) is used instead.
        front_heave = params.get("front_heave_spring_nmm", 50.0)
        cal_uncertainty = self._heave_cal.uncertainty(front_heave)
        # Uncertainty penalty: 0 for well-calibrated, grows for extrapolated
        # At uncertainty=0.15mm (near data) → 0ms; at 3.4mm (380 N/mm) → ~27ms
        if cal_uncertainty > 0.2:
            gain -= (cal_uncertainty - 0.2) ** 1.5 * 8.0

        # ── SPRING RATE REALISM WINDOW ───────────────────────────────────
        # GTP physics model assumes heave spring behaves linearly and that
        # the sigma_f model is valid. Both assumptions break down outside the
        # realistic operating window (30–100 N/mm for Sebring).
        #
        # Very stiff springs (>150 N/mm) appear "safe" to the physics model
        # (σ is very small → nearly zero platform penalty) but are slower in
        # practice because:
        #   1. The car loses compliance over bumps → tyre load variation spikes
        #   2. iRacing tyre model loses performance under high unsprung load variance
        #   3. The heave slider saturates → any bump pushes the car down hard
        #
        # Evidence: 63-session IBT dataset. Heave=900 N/mm sessions scored
        # better than heave=50 by objective but ran 0.5–0.8s slower in practice.
        # The sigma model gives heave=900 nearly zero penalty (σ→0) — but
        # actual dyn_frh data shows these cars bounced more in the data, not less.
        #
        # Penalty structure (validated against Sebring IBT fastest setups):
        #   Optimal: 30–100 N/mm → no penalty
        #   Stiff: 100–200 N/mm → 0–50ms (moderate compliance loss)
        #   Very stiff: 200–500 N/mm → 50–200ms (significant compliance loss)
        #   Extreme: >500 N/mm → 200ms+ (car is essentially rigid, implausible)
        # Source: Taylor Funk 63-session Sebring IBT analysis, 2026-03-22.
        HEAVE_OPT_LO = 30.0   # N/mm — below this is too soft
        HEAVE_OPT_HI = 100.0  # N/mm — above this starts compliance loss
        if front_heave > HEAVE_OPT_HI:
            # Progressive penalty: 0 at 100, 50ms at 200, 200ms at 500, capped 300ms
            excess = front_heave - HEAVE_OPT_HI
            compliance_penalty = min(300.0, excess * 0.5 + (max(0, excess - 100) ** 1.2) * 0.3)
            gain -= compliance_penalty
        elif front_heave < HEAVE_OPT_LO and front_heave > 0:
            # Too soft: risk of bottoming / slider exhaustion already scored elsewhere,
            # add small additional penalty for being below realistic GTP window
            soft_penalty = (HEAVE_OPT_LO - front_heave) * 1.5
            gain -= min(45.0, soft_penalty)

        # ═══════════════════════════════════════════════════════════════
        # TIER 2: LLTD BALANCE (flows through ARB blades and diameter)
        # ARB blade: ~0.5-1.0% LLTD per step × 12ms/% = 6-12ms/click ✓
        # ARB size step: ~3-8% LLTD × 12ms/% = 36-96ms/step ✓
        # Both consistent with Taylor's 5-15ms (blade) / 30-80ms (size)
        # ═══════════════════════════════════════════════════════════════

        # LLTD balance penalty — recalibrated 2026-03-22.
        # IBT data: lltd_measured has r=-0.282 with lap_time (63 sessions).
        # However, this correlation is confounded by torsion bar OD (which
        # shifts LLTD mechanically). The LLTD *error* from target has near-zero
        # correlation with lap time independently.
        # Cap reduced from 25ms → 10ms: LLTD targeting is a soft guideline,
        # not a primary lap time driver. The ARB/torsion terms already capture
        # the physical stiffness effects; the LLTD error cap prevents over-penalizing
        # setups that differ from the theoretical target but are fast in practice.
        LLTD_MS_PER_PCT = 2.5  # [ms / %LLTD_error]
        lltd_penalty = physics.lltd_error * 100.0 * LLTD_MS_PER_PCT
        gain -= min(10.0, lltd_penalty)  # cap reduced from 25ms → 10ms (2026-03-22 IBT calibration)

        # ═══════════════════════════════════════════════════════════════
        # TIER 3: DAMPING RATIOS (secondary, 3-8ms per axis max)
        # Dampers matter, but their total contribution across all 10 axes
        # is ~20-40ms — not 5ms per axis × 10 = 50ms.
        # Source: Taylor Funk IBT validation (46 sessions), objective
        # calibration from validation/objective_validation.md
        # ═══════════════════════════════════════════════════════════════

        # Front LS near ζ=0.88 (near-critical): entry stability, braking control
        # Rear LS near ζ=0.30 (compliant): traction compliance over kerbs
        # Each axis: max ~8ms penalty, down from old 5ms (recalibrated upward)
        zeta_ls_front_err = abs(physics.zeta_ls_front - 0.88)
        gain -= min(8.0, zeta_ls_front_err * 10.0)

        zeta_ls_rear_err = abs(physics.zeta_ls_rear - 0.30)
        gain -= min(6.0, zeta_ls_rear_err * 8.0)

        zeta_hs_front_err = abs(physics.zeta_hs_front - 0.45)
        gain -= min(5.0, zeta_hs_front_err * 7.0)

        zeta_hs_rear_err = abs(physics.zeta_hs_rear - 0.14)
        gain -= min(5.0, zeta_hs_rear_err * 7.0)

        # ── REBOUND : COMPRESSION RATIO (previously invisible — pinning bug) ──
        # Rebound clicks had ZERO gradient in the objective because ζ was computed
        # from compression only. This caused the coord descent to leave rbd pinned
        # at whatever Sobol init selected (typically 5).
        #
        # Physics basis for ratio targets:
        #   LS rbd/comp ≈ 0.9–1.1 (near 1:1) — symmetric low-speed response
        #     avoids roll jacking on chicanes; slightly < 1 prevents bump-stop hunting
        #   HS rbd/comp ≈ 0.5–0.7 (more comp than rbd) — GTP aero platform rule:
        #     comp absorbs the kerb hit, rbd controls the return; if rbd > comp
        #     at high speed, car jacks up off kerbs and loses underfloor seal
        #   Rear HS rbd target is softer than front (rear must "follow through"
        #     on kerb exit without unloading rear axle)
        #
        # Source: rbd_comp_ratio_target=1.6 in DamperModel (clicks ratio, not force)
        #   That value means rbd_clicks = 1.6 × comp_clicks (typical for passenger car)
        #   For GTP/prototype: tighter control needed; ~0.9 LS, ~0.6 HS
        #   Penalty: 3ms per 0.1 ratio error (mild — 4ms total range per axis)
        #   This gives the coord descent a gradient without overpowering compression ζ
        f_ls_comp = params.get("front_ls_comp", 7)
        f_ls_rbd  = params.get("front_ls_rbd", 6)
        f_hs_comp = params.get("front_hs_comp", 5)
        f_hs_rbd  = params.get("front_hs_rbd", 5)
        r_ls_comp = params.get("rear_ls_comp", 5)
        r_ls_rbd  = params.get("rear_ls_rbd", 5)
        r_hs_comp = params.get("rear_hs_comp", 3)
        r_hs_rbd  = params.get("rear_hs_rbd", 3)

        # Ratio target: rbd = target_ratio * comp
        # Penalty is deliberately WEAK (max 5ms) so ζ (comp) stays dominant.
        # The gradient here only moves RBD — it must never pull COMP down.
        # Implementation: rbd_target = comp * ratio_target (comp-anchored, not rbd-anchored)
        # So if comp=11 → rbd_target_LS = 11*0.95 = 10.5 → solver pushes rbd toward 10-11.
        # If rbd=5 and comp=11 → ratio=0.45 → error=0.50 → penalty=0.50*8=4ms (small).
        # This gives the coord descent a gradient on rbd without destabilizing comp.
        LS_RBD_COMP_TARGET = 0.95   # [ratio] LS rebound / LS comp target
        HS_RBD_COMP_TARGET = 0.60   # [ratio] HS rebound / HS comp target (GTP kerb rule)
        RBD_PENALTY_MS_PER_UNIT = 8.0   # weak — max 5ms, never overpowers ζ

        def _rbd_penalty(rbd: float, comp: float, target: float) -> float:
            if comp < 1.0:
                # Zero comp is penalized by ζ already; don't double-penalize here
                return 0.0
            rbd_target = comp * target
            err = abs(rbd - rbd_target)  # in click units (not ratio — click-anchored)
            return min(5.0, err * RBD_PENALTY_MS_PER_UNIT / max(1.0, comp))

        gain -= _rbd_penalty(f_ls_rbd, f_ls_comp, LS_RBD_COMP_TARGET)
        gain -= _rbd_penalty(f_hs_rbd, f_hs_comp, HS_RBD_COMP_TARGET)
        gain -= _rbd_penalty(r_ls_rbd, r_ls_comp, LS_RBD_COMP_TARGET)
        gain -= _rbd_penalty(r_hs_rbd, r_hs_comp, HS_RBD_COMP_TARGET)

        # ═══════════════════════════════════════════════════════════════
        # TIER 4: DF BALANCE (aero map quality, secondary to rake)
        # ═══════════════════════════════════════════════════════════════

        # Each 0.1% DF balance error: ~5ms at speed tracks
        # Raised from 30ms/pct to 50ms/pct — more aggressive aero sensitivity
        gain -= physics.df_balance_error_pct * 20.0  # tuned from 45-50 → 20

        # ═══════════════════════════════════════════════════════════════
        # TIER 5: CAMBER (contact patch optimization, tertiary)
        # ═══════════════════════════════════════════════════════════════

        # Front camber target: -3.0° (compensates for ~0.5° body roll at limit)
        # Rear camber target: -2.0° (less roll compensation needed)
        # Max contribution: ~8ms (small vs platform + balance terms)
        front_camber = params.get("front_camber_deg", -3.5)
        gain -= min(8.0, abs(front_camber - (-3.0)) * 5.0)

        rear_camber = params.get("rear_camber_deg", -2.0)
        gain -= min(6.0, abs(rear_camber - (-2.0)) * 4.0)

        # ═══════════════════════════════════════════════════════════════
        # TIER 5: DIFF PRELOAD (exit traction, small effect)
        # ═══════════════════════════════════════════════════════════════

        # Ferrari 499P at Sebring: fastest session (108.1s) used 0 Nm + Less Locking.
        # E-diff + hybrid manage traction electronically; mechanical preload adds
        # corner-entry understeer at slow hairpins (T1, T17) without traction benefit.
        # BMW/Porsche/Cadillac may still prefer higher preload — keep 65 Nm default.
        diff = params.get("diff_preload_nm", 20.0)
        diff_target = 10.0 if self._car_slug == "ferrari" else 65.0  # Nm, calibrated Mar21 (fastest=0Nm+LessLocking)
        gain -= min(8.0, abs(diff - diff_target) * 0.12)

        # ═══════════════════════════════════════════════════════════════
        # TIER 5: ARB SIZE (LLTD range — full steps, ~36-96ms each)
        # ═══════════════════════════════════════════════════════════════
        # ARB size controls available LLTD range. Wrong size = can't hit
        # target LLTD even with blade adjustments. Penalty on size mismatch
        # vs what's needed to achieve target LLTD with mid-range blade.
        # Soft=0, Medium=1, Stiff=2 for ordinal encoding.
        f_arb_size_idx = int(round(params.get("front_arb_size", 0)))
        r_arb_size_idx = int(round(params.get("rear_arb_size", 1)))
        f_arb_blade = int(round(params.get("front_arb_blade", 1)))
        r_arb_blade = int(round(params.get("rear_arb_blade", 2)))
        # Penalty for extreme blade + wrong size (should have sized up/down)
        # e.g., blade 5 + Soft = should be Medium; blade 1 + Stiff = should be Soft
        max_blade = 5
        if f_arb_blade >= max_blade and f_arb_size_idx == 0:
            gain -= 15.0  # at max blade on Soft → too small, needs Medium
        if r_arb_blade >= max_blade and r_arb_size_idx == 0:
            gain -= 20.0
        if f_arb_blade <= 1 and f_arb_size_idx >= 2:
            gain -= 10.0  # at min blade on Stiff → too large, needs Medium
        if r_arb_blade <= 1 and r_arb_size_idx >= 2:
            gain -= 15.0

        # ═══════════════════════════════════════════════════════════════
        # TIER 5: DIFF RAMP ANGLES (corner-entry rotation + exit traction)
        # ═══════════════════════════════════════════════════════════════
        # Coast ramp controls corner-entry rotation (lower = more locking = more US)
        # Drive ramp controls exit traction (higher = less locking = more wheelspin)
        # Optimal: match to driver trail-brake depth and throttle progressiveness
        ramp_options = getattr(
            getattr(self.car, "garage_ranges", None), "diff_coast_drive_ramp_options",
            [(40, 65), (45, 70), (50, 75)]
        )
        ramp_idx = int(round(params.get("diff_ramp_option_idx", 1)))
        ramp_idx = max(0, min(len(ramp_options) - 1, ramp_idx))
        coast_deg, drive_deg = ramp_options[ramp_idx]

        # Trail brake depth → coast ramp preference
        # Deep trail braking (>0.4): prefer lower coast (more locking = rotation)
        # Light trail braking (<0.2): prefer higher coast (less locking)
        trail_brake = getattr(self._driver, "trail_brake_depth_p95", 0.3) if self._driver else 0.3
        if trail_brake > 0.4:
            # Deep trail brake: 40° coast optimal (index 0)
            coast_target_idx = 0
        elif trail_brake < 0.2:
            # Light trail brake: 50° coast optimal (index 2)
            coast_target_idx = 2
        else:
            coast_target_idx = 1  # 45° middle

        coast_mismatch = abs(ramp_idx - coast_target_idx)
        gain -= min(12.0, coast_mismatch * 6.0)  # ~6ms per step mismatch

        # ═══════════════════════════════════════════════════════════════
        # TIER 5: DIFF CLUTCH PLATES (lock authority, exit traction)
        # ═══════════════════════════════════════════════════════════════
        # More plates = more lock authority at same preload.
        # Fewer plates = less mechanical traction effect, smoother.
        # GTP baseline: 4 plates for traction-limited tracks, 6 for rotation-limited.
        clutch_plates = int(round(params.get("diff_clutch_plates", 4)))
        # Rear power slip p95 from measured
        rear_slip_p95 = getattr(self._measured, "rear_power_slip_p95", None) if self._measured else None
        if rear_slip_p95 is None:
            rear_slip_p95 = 0.07  # Sebring BMW baseline
        if rear_slip_p95 > 0.10:
            # High rear slip → more plates needed for traction
            plates_target = 6
        elif rear_slip_p95 < 0.05:
            # Low rear slip → fewer plates, smoother exit
            plates_target = 2
        else:
            plates_target = 4
        gain -= min(10.0, abs(clutch_plates - plates_target) * 3.0)

        # ═══════════════════════════════════════════════════════════════
        # TIER 5: TC GAIN / SLIP (traction control authority, 5-15ms)
        # ═══════════════════════════════════════════════════════════════
        # TC too aggressive: driver losing corner exit (intervention clips power)
        # TC too passive: wheelspin on exit, tyre wear, rear instability
        tc_gain = int(round(params.get("tc_gain", 4)))
        tc_slip = int(round(params.get("tc_slip", 3)))

        # Target: gain/slip from supporting_solver's recommendation
        tc_gain_target = getattr(self._measured, "_tc_gain_recommendation", None) if self._measured else None
        tc_slip_target = getattr(self._measured, "_tc_slip_recommendation", None) if self._measured else None
        if tc_gain_target is not None:
            gain -= min(8.0, abs(tc_gain - tc_gain_target) * 2.0)
        if tc_slip_target is not None:
            gain -= min(6.0, abs(tc_slip - tc_slip_target) * 2.0)
        # Direct rear slip pressure: if rear_slip_p95 > 0.10, want higher TC
        if rear_slip_p95 > 0.10 and tc_gain < 5:
            gain -= min(8.0, (5 - tc_gain) * 3.0)
        elif rear_slip_p95 < 0.04 and tc_gain > 6:
            gain -= min(5.0, (tc_gain - 6) * 2.0)  # TC too aggressive for stable car

        return gain

    def _compute_platform_risk(
        self,
        params: dict[str, float],
        physics: PhysicsResult,
        veto_reasons: list[str],
        soft_penalties: list[str],
    ) -> PlatformRisk:
        """Compute platform risk from forward-evaluated physics."""
        risk = PlatformRisk()

        # ── Front RH floor: hard constraint for ground-effect GTP cars ──
        # Every competitive GTP setup pins front RH at ≥ 30mm.
        # Below 30mm: risk of vortex stall + underfloor contact on bumps.
        # Below 25mm: hard veto (cannot race, unsafe aero stall).
        # Source: Taylor Funk (professional GTP driver, 2026 calibration);
        #   all 46 BMW Sebring observations show front static RH 28-35mm.
        # Dynamic ride heights — use aero compression model when available.
        # dyn_front_rh = static_front_rh - aero_compression_front
        # For BMW Sebring: 30mm static - 15mm compression = 15mm dynamic mean.
        # This is the mean floor height at speed; the car oscillates around it.
        # Bottoming occurs when excursion (from p99 bump) exceeds dyn_front_rh.
        # Vortex burst uses p95 excursion (see evaluate_physics notes).
        _car = self.car
        _static_f = _car.pushrod.front_pinned_rh_mm
        _comp_f = _car.aero_compression.front_compression_mm
        dyn_front_rh = max(5.0, _static_f - _comp_f)  # floor at 5mm (physical limit)
        dyn_rear_rh = 42.0  # rear: not used for vortex; kept as reference
        FRONT_RH_FLOOR_MM = 30.0  # [mm] static — every competitive GTP setup
        FRONT_RH_FLOOR_PENALTY_MS_PER_MM = 25.0  # [ms/mm] below floor

        # ── iRacing garage deflection legality (hard veto) ─────────────
        # heave_spring_defl_static = defl_static_intercept + defl_static_heave_coeff × k
        # Legal: 0.6 mm ≤ defl_static ≤ 25.0 mm  (GarageRanges.heave_spring_defl_mm)
        # Legal: 25.0 mm ≤ slider_static ≤ 45.0 mm (GarageRanges.heave_slider_defl_mm)
        # Front shock legal max: 19.9 mm (GarageRanges.front_shock_defl_max_mm)
        # Rear shock legal min: 15.0 mm (GarageRanges.rear_shock_defl_min_mm)
        # These are enforced by iRacing's garage and cannot be raced if violated.
        _hsm = self.car.heave_spring
        _gr = self.car.garage_ranges
        _k_front = params.get("front_heave_spring_nmm", 50.0)
        _od_mm = params.get("front_torsion_od_mm", 14.34)
        # Use baseline perch for veto check — compute_perch_offsets uses a zero-referenced
        # offset (0 at heave=50), but the slider formula is calibrated from the absolute
        # perch position. Use the car's absolute baseline perch when evaluating legality.
        _perch_front = _hsm.perch_offset_front_baseline_mm
        # Use DeflectionModel (multi-variable: heave + perch + OD^4, R²=0.953, 31 sessions)
        # instead of the HeaveSpringModel simplified single-variable formula.
        # Bug: old formula gives 24.0mm at k=30 N/mm but actual is 6.4mm (3.7× error).
        # The DeflectionModel gives ~7.1mm at k=30 N/mm — 10× more accurate.
        # Impact: old formula caused WRONG near-vetoes for soft heave springs (k≤35 N/mm)
        # by predicting deflection near the 25.0mm legal max when actual is far below it.
        # Source: car_model/calibrate_deflections.py, BMW Sebring 31 setups, March 2026.
        _dm = self.car.deflection
        _spring_defl = _dm.heave_spring_defl_static(_k_front, _perch_front, _od_mm)
        _slider_static = _dm.heave_slider_defl_static(_k_front, _perch_front, _od_mm)

        _defl_min, _defl_max = _gr.heave_spring_defl_mm   # (0.6, 25.0)
        _slider_min, _slider_max = _gr.heave_slider_defl_mm  # (25.0, 45.0)

        if _spring_defl < _defl_min:
            veto_reasons.append(
                f"Heave spring defl too low: {_spring_defl:.2f}mm < {_defl_min}mm legal min "
                f"(heave={_k_front:.0f} N/mm — increase heave spring rate)"
            )
        if _spring_defl > _defl_max:
            veto_reasons.append(
                f"Heave spring defl too high: {_spring_defl:.2f}mm > {_defl_max}mm legal max "
                f"(heave={_k_front:.0f} N/mm — decrease heave spring rate)"
            )
        if _slider_static < _slider_min:
            veto_reasons.append(
                f"Heave slider defl too low: {_slider_static:.2f}mm < {_slider_min}mm legal min"
            )
        if _slider_static > _slider_max:
            veto_reasons.append(
                f"Heave slider defl too high: {_slider_static:.2f}mm > {_slider_max}mm legal max"
            )

        # ── Bottoming risk (from real excursion calculation) ────────────
        margin = physics.front_bottoming_margin_mm
        if margin < 0:
            # Bottoming — hard veto if severe
            if margin < -5.0:
                veto_reasons.append(
                    f"Front bottoming: excursion {physics.front_excursion_mm:.1f}mm "
                    f"exceeds RH by {-margin:.1f}mm"
                )
            risk.bottoming_risk_ms = min(500.0, 100.0 * abs(margin))
            soft_penalties.append(f"Front bottoming margin negative: {margin:.1f}mm")
        elif margin < 2.0:
            risk.bottoming_risk_ms = 200.0 * (2.0 - margin)
            soft_penalties.append(f"Front bottoming margin critically low: {margin:.1f}mm")
        elif margin < 5.0:
            risk.bottoming_risk_ms = 30.0 * (5.0 - margin)

        # ── Vortex burst risk (from stall margin) ──────────────────────
        stall = physics.stall_margin_mm
        if stall < 0:
            if stall < -3.0:
                veto_reasons.append(
                    f"Vortex burst: stall margin {stall:.1f}mm"
                )
            risk.vortex_risk_ms = min(500.0, 150.0 * abs(stall))
            soft_penalties.append(f"Stall margin negative: {stall:.1f}mm")
        elif stall < 2.0:
            risk.vortex_risk_ms = 100.0 * (2.0 - stall)

        # ── Ride height variance (platform collapse risk) ──────────────
        sigma_target = 3.0  # mm — typical GTP target
        if physics.front_sigma_mm > sigma_target * 1.5:
            risk.rh_collapse_risk_ms = 50.0 * (physics.front_sigma_mm - sigma_target)
            soft_penalties.append(
                f"Front RH variance high: σ={physics.front_sigma_mm:.1f}mm "
                f"(target <{sigma_target:.0f}mm)"
            )
        if physics.rear_sigma_mm > sigma_target * 2.0:
            risk.rh_collapse_risk_ms += 30.0 * (physics.rear_sigma_mm - sigma_target * 2.0)

        # ── Fuel window LLTD risk (worst of race start vs. end of stint) ─
        # Physics: as fuel burns off, front-rear weight balance shifts.
        # If the LLTD is tuned only for full fuel, the car gets imbalanced
        # at low fuel. We score the WORST case error across the stint window.
        # Race start = 89L, end of stint = 20L (typical GTP stint window).
        # Reference: OptimumG — LLTD_target ≈ W_front + λ * 0.05
        # where W_front changes with fuel load and λ = tyre load sensitivity.
        try:
            err_start, err_end, worst_err = self._compute_lltd_fuel_window(
                params, fuel_start_l=89.0, fuel_end_l=20.0
            )
            # Use physics.lltd_error for full-fuel case (already computed)
            # Add incremental penalty for the END-of-stint case getting worse
            if worst_err > physics.lltd_error + 0.005:
                # Stint-end LLTD drift is significant
                drift_penalty = (worst_err - physics.lltd_error) * 100.0 * 5.0
                if drift_penalty > 2.0:
                    soft_penalties.append(
                        f"LLTD fuel drift: start_err={err_start:.1%} "
                        f"end_err={err_end:.1%} "
                        f"(worst penalized {drift_penalty:.0f}ms)"
                    )
                # Cap the fuel LLTD drift penalty at 20ms — it's secondary to
                # the static LLTD error which is already in lap_gain
                risk.rh_collapse_risk_ms += min(20.0, drift_penalty)
        except Exception:
            pass  # fuel window LLTD is non-critical — never let it break scoring

        return risk

    def _compute_driver_mismatch(
        self,
        params: dict[str, float],
        physics: PhysicsResult,
        driver_profile,
        soft_penalties: list[str],
    ) -> DriverMismatch:
        """Compute driver style mismatch penalty using real damping ratios."""
        mismatch = DriverMismatch()

        if driver_profile is None:
            return mismatch

        # Trail braking: aggressive trail brakers need stiffer front LS (higher ζ)
        trail_depth = getattr(driver_profile, "trail_brake_depth", 0.5)
        if trail_depth > 0.7 and physics.zeta_ls_front < 0.6:
            mismatch.trail_brake_ms = (0.6 - physics.zeta_ls_front) * 80.0
            soft_penalties.append(
                f"Front LS damping ζ={physics.zeta_ls_front:.2f} too soft "
                f"for deep trail braker (depth={trail_depth:.2f})"
            )
        elif trail_depth < 0.3 and physics.zeta_ls_front > 1.2:
            mismatch.trail_brake_ms = (physics.zeta_ls_front - 1.2) * 50.0
            soft_penalties.append(
                f"Front LS damping ζ={physics.zeta_ls_front:.2f} too stiff "
                f"for light trail braker"
            )

        # Smoothness: erratic drivers need more HS damping
        smoothness = getattr(driver_profile, "smoothness", 0.5)
        if smoothness < 0.3 and physics.zeta_hs_rear < 0.10:
            mismatch.smoothness_ms = (0.10 - physics.zeta_hs_rear) * 200.0
            soft_penalties.append(
                f"Rear HS damping ζ={physics.zeta_hs_rear:.2f} too soft "
                f"for erratic driver (smoothness={smoothness:.2f})"
            )

        # Throttle style: progressive throttle + low diff preload = entry rotation
        throttle_prog = getattr(driver_profile, "throttle_progressiveness", 0.5)
        diff_preload = params.get("diff_preload_nm", 20.0)
        if throttle_prog > 0.7 and diff_preload > 35:
            mismatch.throttle_style_ms = (diff_preload - 35) * 0.5
            soft_penalties.append(
                f"Diff preload {diff_preload:.0f}Nm high for progressive throttle driver"
            )

        return mismatch

    def _compute_telemetry_uncertainty(
        self,
        measured,
        session_count: int,
        soft_penalties: list[str],
    ) -> TelemetryUncertainty:
        """Compute scoring uncertainty from telemetry quality."""
        uncert = TelemetryUncertainty()

        if measured is None:
            uncert.missing_signal_ms = 15.0  # moderate penalty
            soft_penalties.append("No telemetry — physics-only prediction")
            return uncert

        if session_count < 3:
            uncert.proxy_signal_ms = 10.0 * (3 - session_count)
            soft_penalties.append(f"Only {session_count} sessions — corrections uncertain")

        return uncert

    def _compute_envelope_penalty(
        self,
        params: dict[str, float],
        physics: PhysicsResult,
        soft_penalties: list[str],
    ) -> EnvelopePenalty:
        """Penalize candidates far from validated operating envelope.

        Uses physics-derived metrics instead of parameter-space distance.
        """
        penalty = EnvelopePenalty()

        # Extreme spring ratios
        front_heave = params.get("front_heave_spring_nmm", 50.0)
        rear_third = params.get("rear_third_spring_nmm", 450.0)
        if rear_third > 0:
            ratio = front_heave / rear_third
            if ratio < 0.03 or ratio > 0.20:
                penalty.setup_distance_ms = 10.0
                soft_penalties.append(
                    f"Unusual heave/third ratio: {ratio:.3f} (normal 0.04-0.15)"
                )

        # Extreme damping — underdamped or overdamped
        if physics.zeta_ls_front > 1.5:
            penalty.setup_distance_ms += 15.0
            soft_penalties.append(
                f"Front LS overdamped: ζ={physics.zeta_ls_front:.2f} (>1.5)"
            )
        if physics.zeta_ls_front < 0.2:
            penalty.setup_distance_ms += 15.0
            soft_penalties.append(
                f"Front LS severely underdamped: ζ={physics.zeta_ls_front:.2f}"
            )

        # Extreme LLTD
        if physics.lltd < 0.45 or physics.lltd > 0.60:
            penalty.setup_distance_ms += 15.0
            soft_penalties.append(
                f"LLTD outside normal range: {physics.lltd:.1%}"
            )

        # Damper asymmetry: front HS should >= rear HS for compliance hierarchy
        f_hs = params.get("front_hs_comp", 5)
        r_hs = params.get("rear_hs_comp", 3)
        if r_hs > f_hs + 3:
            penalty.setup_distance_ms += 10.0
            soft_penalties.append("Rear HS comp much stiffer than front — unconventional")

        return penalty

    def evaluate_batch(
        self,
        param_batch: list[dict[str, float]],
        family: str = "batch",
        measured=None,
        driver_profile=None,
        session_count: int = 0,
        layer: int = 4,
    ) -> list[CandidateEvaluation]:
        """Batch evaluation with shared precomputation.

        Pre-loads the aero surface ONCE and reuses it across all candidates.
        For Layer 1 and 2 (coarse scoring), skips driver/telemetry terms to
        keep per-candidate cost low.

        Per-layer objective profiles (controls which scoring terms are active):
          Layer 1: platform_risk + lap_gain only (fastest — for coarse Sobol filter)
          Layer 2: + LLTD + DF balance (balance grid scoring)
          Layer 3: + damping ratios (damper coordinate descent)
          Layer 4: full objective (neighborhood polish + final ranking)

        Physics cost is dominated by damped_excursion_mm (~0.1ms/candidate).
        At 57M candidates (exhaustive), Layer 1+2 fast path ≈ 5-8 hours.
        At 1M candidates (standard), same path ≈ 5-8 min.

        Args:
            param_batch:    List of candidate parameter dicts
            family:         Family label for all candidates in batch
            measured:       MeasuredState (shared across batch)
            driver_profile: DriverProfile (shared across batch)
            session_count:  Session count for uncertainty scoring
            layer:          1-4, controls which scoring terms are active

        Returns:
            List of CandidateEvaluation in same order as param_batch
        """
        # Pre-load aero surface once (cached after first call)
        _ = self._get_surface()

        results: list[CandidateEvaluation] = []
        for params in param_batch:
            # Layer 1 fast path: skip driver/uncertainty/envelope
            if layer == 1:
                physics = self.evaluate_physics(params)
                breakdown = ObjectiveBreakdown()
                veto_reasons: list[str] = []
                soft_penalties: list[str] = []
                breakdown.lap_gain_ms = self._estimate_lap_gain(params, physics)
                breakdown.platform_risk = self._compute_platform_risk(
                    params, physics, veto_reasons, soft_penalties
                )
                # Zero out the slower terms
                breakdown.driver_mismatch = DriverMismatch()
                breakdown.telemetry_uncertainty = TelemetryUncertainty()
                breakdown.envelope_penalty = EnvelopePenalty()
                results.append(CandidateEvaluation(
                    params=params,
                    family=family,
                    breakdown=breakdown,
                    physics=physics,
                    hard_vetoed=len(veto_reasons) > 0,
                    veto_reasons=veto_reasons,
                    soft_penalties=soft_penalties,
                ))
            elif layer == 2:
                # Layer 2: add LLTD + DF balance via full physics, skip driver/uncertainty
                physics = self.evaluate_physics(params)
                breakdown = ObjectiveBreakdown()
                veto_reasons = []
                soft_penalties = []
                breakdown.lap_gain_ms = self._estimate_lap_gain(params, physics)
                breakdown.platform_risk = self._compute_platform_risk(
                    params, physics, veto_reasons, soft_penalties
                )
                breakdown.envelope_penalty = self._compute_envelope_penalty(
                    params, physics, soft_penalties
                )
                breakdown.driver_mismatch = DriverMismatch()
                breakdown.telemetry_uncertainty = TelemetryUncertainty()
                results.append(CandidateEvaluation(
                    params=params,
                    family=family,
                    breakdown=breakdown,
                    physics=physics,
                    hard_vetoed=len(veto_reasons) > 0,
                    veto_reasons=veto_reasons,
                    soft_penalties=soft_penalties,
                ))
            else:
                # Layers 3-4: full evaluation
                results.append(self.evaluate(
                    params=params,
                    family=family,
                    measured=measured,
                    driver_profile=driver_profile,
                    session_count=session_count,
                ))
        return results
