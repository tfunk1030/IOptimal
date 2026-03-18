"""Multi-objective scoring function for setup candidates.

Turns the optimization objective into a single canonical score with
a transparent breakdown. Every candidate shows exactly why it ranked
where it did.

Score formula:
    total_score = (
        + lap_gain_ms
        - 0.9 * platform_risk_ms
        - 0.6 * driver_mismatch_ms
        - 0.7 * telemetry_uncertainty_ms
        - 0.8 * envelope_penalty_ms
        - 0.4 * staleness_penalty_ms
    )

All terms are in milliseconds for human-interpretable comparison.

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
    w_platform: float = 0.9
    w_driver: float = 0.6
    w_uncertainty: float = 0.7
    w_envelope: float = 0.8
    w_staleness: float = 0.4

    @property
    def total_score_ms(self) -> float:
        return (
            self.lap_gain_ms
            - self.w_platform * self.platform_risk.total_ms
            - self.w_driver * self.driver_mismatch.total_ms
            - self.w_uncertainty * self.telemetry_uncertainty.total_ms
            - self.w_envelope * self.envelope_penalty.total_ms
            - self.w_staleness * self.staleness_penalty_ms
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

    # Vortex burst threshold: dynamic front RH below this = aero stall
    VORTEX_BURST_THRESHOLD_MM = 8.0

    def __init__(self, car, track):
        self.car = car
        self.track = track
        self._surface = None  # lazy-loaded aero surface

    def _get_surface(self):
        """Lazy-load aero surface for DF balance queries."""
        if self._surface is None:
            try:
                from aero_model import load_car_surfaces
                surfaces = load_car_surfaces(self.car.canonical_name)
                # Use default wing angle
                wing = self.car.wing_angles[0] if self.car.wing_angles else 17.0
                if isinstance(self.track, TrackProfile):
                    # Try to get wing from context
                    pass
                if wing in surfaces:
                    self._surface = surfaces[wing]
                elif surfaces:
                    self._surface = next(iter(surfaces.values()))
            except Exception:
                pass
        return self._surface

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
            v_p99_front = (track.shock_vel_p99_front_clean_mps
                          if getattr(track, "shock_vel_p99_front_clean_mps", 0) > 0
                          else track.shock_vel_p99_front_mps)
            v_p99_rear = (track.shock_vel_p99_rear_clean_mps
                         if getattr(track, "shock_vel_p99_rear_clean_mps", 0) > 0
                         else track.shock_vel_p99_rear_mps)

            m_eff_front = car.heave_spring.front_m_eff_kg
            m_eff_rear = car.heave_spring.rear_m_eff_kg
            tyre_vr = getattr(car, "tyre_vertical_rate_nmm", None)

            # Front excursion
            result.front_excursion_mm = damped_excursion_mm(
                v_p99_front, m_eff_front, front_heave_nmm,
                tyre_vertical_rate_nmm=tyre_vr,
                parallel_wheel_rate_nmm=front_wheel_rate * 0.5,
            )
            # Rear excursion
            result.rear_excursion_mm = damped_excursion_mm(
                v_p99_rear, m_eff_rear, rear_third_nmm,
                tyre_vertical_rate_nmm=tyre_vr,
                parallel_wheel_rate_nmm=rear_wheel_rate * 0.5,
            )

            # Dynamic ride heights (use typical values — actual depends on rake solver)
            dyn_front_rh = 19.0  # typical for GTP at speed
            dyn_rear_rh = 42.0
            result.front_bottoming_margin_mm = dyn_front_rh - result.front_excursion_mm
            result.rear_bottoming_margin_mm = dyn_rear_rh - result.rear_excursion_mm

            # Stall margin: distance from vortex burst threshold
            result.stall_margin_mm = (dyn_front_rh - result.front_excursion_mm
                                      - self.VORTEX_BURST_THRESHOLD_MM)

            # Platform variance (sigma = p99 / 2.33 for Gaussian)
            result.front_sigma_mm = result.front_excursion_mm / 2.33
            result.rear_sigma_mm = result.rear_excursion_mm / 2.33

        # ── LLTD (real roll stiffness calculation) ──────────────────────
        arb = car.arb
        t_f = arb.track_width_front_mm / 2000.0  # half track width in meters
        t_r = arb.track_width_rear_mm / 2000.0

        # Corner spring roll stiffness: K = 2 * k_wheel(N/m) * t_half² * π/180
        k_roll_springs_front = 2.0 * (front_wheel_rate * 1000.0) * t_f**2 * (math.pi / 180.0)
        k_roll_springs_rear = 2.0 * (rear_wheel_rate * 1000.0) * t_r**2 * (math.pi / 180.0)

        # ARB contribution
        front_arb_size = arb.front_baseline_size
        rear_arb_size = arb.rear_baseline_size
        k_arb_front = arb.front_roll_stiffness(front_arb_size, front_arb_blade)
        k_arb_rear = arb.rear_roll_stiffness(rear_arb_size, rear_arb_blade)

        k_front_total = k_roll_springs_front + k_arb_front
        k_rear_total = k_roll_springs_rear + k_arb_rear
        result.k_roll_front = k_front_total
        result.k_roll_rear = k_rear_total

        if k_front_total + k_rear_total > 0:
            result.lltd = k_front_total / (k_front_total + k_rear_total)
        else:
            result.lltd = 0.5

        # LLTD target
        tyre_sens = getattr(car, "tyre_load_sensitivity", 0.20)
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

        # LS damping from clicks: F = clicks * force_per_click, c = F / v_ref
        v_ls_ref = 0.025  # 25 mm/s
        c_ls_front = (f_ls_comp * damper.ls_force_per_click_n) / v_ls_ref
        c_ls_rear = (r_ls_comp * damper.ls_force_per_click_n) / v_ls_ref

        result.zeta_ls_front = c_ls_front / c_crit_front if c_crit_front > 0 else 0
        result.zeta_ls_rear = c_ls_rear / c_crit_rear if c_crit_rear > 0 else 0

        # HS damping from clicks
        if isinstance(track, TrackProfile):
            v_hs_front = max(track.shock_vel_p95_front_mps, 0.050)
            v_hs_rear = max(track.shock_vel_p95_rear_mps, 0.050)
        else:
            v_hs_front = 0.120
            v_hs_rear = 0.150

        c_hs_front = (f_hs_comp * damper.hs_force_per_click_n) / v_hs_front
        c_hs_rear = (r_hs_comp * damper.hs_force_per_click_n) / v_hs_rear

        result.zeta_hs_front = c_hs_front / c_crit_front if c_crit_front > 0 else 0
        result.zeta_hs_rear = c_hs_rear / c_crit_rear if c_crit_rear > 0 else 0

        # ── DF balance (aero map lookup if available) ───────────────────
        surface = self._get_surface()
        if surface is not None:
            try:
                dyn_f = 19.0  # typical operating point
                dyn_r = 42.0
                result.df_balance_pct = surface.df_balance(dyn_f, dyn_r)
                result.ld_ratio = surface.lift_drag(dyn_f, dyn_r)
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
        # Run forward physics evaluation
        physics = self.evaluate_physics(params)

        breakdown = ObjectiveBreakdown()
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

        return CandidateEvaluation(
            params=params,
            family=family,
            breakdown=breakdown,
            physics=physics,
            hard_vetoed=len(veto_reasons) > 0,
            veto_reasons=veto_reasons,
            soft_penalties=soft_penalties,
        )

    # ── Per-layer objective profiles ─────────────────────────────
    # Each layer evaluates only the terms that matter at that stage,
    # avoiding unnecessary computation in early filtering layers.
    #
    # Layer 1 (platform skeletons): platform_risk + lap_gain only
    # Layer 2 (balance tuning):     + LLTD + balance scoring
    # Layer 3 (damper optimization): + damping ratio scoring
    # Layer 4 (fine tuning):        full objective (all terms)

    LAYER_PROFILES: dict[int, set[str]] = {
        1: {"lap_gain", "platform_risk"},
        2: {"lap_gain", "platform_risk", "lltd", "balance"},
        3: {"lap_gain", "platform_risk", "lltd", "balance", "damping"},
        4: {"lap_gain", "platform_risk", "lltd", "balance", "damping",
            "driver_mismatch", "telemetry_uncertainty", "envelope_penalty"},
    }

    def evaluate_batch(
        self,
        param_batch: list[dict[str, float]],
        layer: int = 4,
        family: str = "batch",
        solver_result: dict | None = None,
        measured=None,
        driver_profile=None,
        session_count: int = 0,
    ) -> list[CandidateEvaluation]:
        """Batch evaluation with shared precomputation.

        Pre-computes aero surface lookups and track profile constants
        once, then reuses them across the batch. Supports per-layer
        objective profiles for hierarchical search:

        - Layer 1: platform_risk + lap_gain only (fastest)
        - Layer 2: add LLTD + balance scoring
        - Layer 3: add damping ratio scoring
        - Layer 4: full objective (default)

        Args:
            param_batch: List of candidate parameter dicts.
            layer: Objective layer (1-4). Lower = faster but coarser.
            family: Family label for all candidates.
            solver_result: Solver step outputs (optional).
            measured: MeasuredState (optional, only used in layer 4).
            driver_profile: DriverProfile (optional, only used in layer 3+).
            session_count: Number of telemetry sessions.

        Returns:
            List of CandidateEvaluation, one per candidate.
        """
        active = self.LAYER_PROFILES.get(layer, self.LAYER_PROFILES[4])

        # ── Shared precomputation ─────────────────────────────────
        # Cache the aero surface once (expensive to load)
        surface = self._get_surface() if "lap_gain" in active else None

        # Pre-extract track constants used by physics
        track = self.track
        is_track_profile = isinstance(track, TrackProfile)

        if is_track_profile:
            v_p99_front = (track.shock_vel_p99_front_clean_mps
                          if getattr(track, "shock_vel_p99_front_clean_mps", 0) > 0
                          else track.shock_vel_p99_front_mps)
            v_p99_rear = (track.shock_vel_p99_rear_clean_mps
                         if getattr(track, "shock_vel_p99_rear_clean_mps", 0) > 0
                         else track.shock_vel_p99_rear_mps)
            v_hs_front = max(getattr(track, "shock_vel_p95_front_mps", 0.120), 0.050)
            v_hs_rear = max(getattr(track, "shock_vel_p95_rear_mps", 0.150), 0.050)
        else:
            v_p99_front = v_p99_rear = 0.0
            v_hs_front = 0.120
            v_hs_rear = 0.150

        # Pre-extract car constants
        car = self.car
        c_torsion = car.corner_spring.front_torsion_c
        mr_rear = car.corner_spring.rear_motion_ratio
        m_eff_front = car.heave_spring.front_m_eff_kg
        m_eff_rear = car.heave_spring.rear_m_eff_kg
        tyre_vr = getattr(car, "tyre_vertical_rate_nmm", None)
        arb = car.arb
        t_f = arb.track_width_front_mm / 2000.0
        t_r = arb.track_width_rear_mm / 2000.0
        damper = car.damper
        tyre_sens = getattr(car, "tyre_load_sensitivity", 0.20)
        target_lltd = car.weight_dist_front + (tyre_sens / 0.20) * 0.05
        total_mass = car.total_mass(89.0)
        front_mass = total_mass * car.weight_dist_front / 2.0
        rear_mass = total_mass * (1.0 - car.weight_dist_front) / 2.0
        v_ls_ref = 0.025

        # ── Evaluate each candidate ──────────────────────────────
        results: list[CandidateEvaluation] = []

        for params in param_batch:
            physics = PhysicsResult()
            breakdown = ObjectiveBreakdown()
            veto_reasons: list[str] = []
            soft_penalties: list[str] = []

            # Extract common params
            front_heave_nmm = params.get("front_heave_spring_nmm", 50.0)
            rear_third_nmm = params.get("rear_third_spring_nmm", 450.0)
            rear_spring_nmm = params.get("rear_spring_rate_nmm", 160.0)
            front_torsion_od = params.get("front_torsion_od_mm",
                car.corner_spring.front_torsion_od_options[0]
                if car.corner_spring.front_torsion_od_options else 14.34)

            # Wheel rates
            front_wheel_rate = c_torsion * (front_torsion_od ** 4)
            rear_wheel_rate = rear_spring_nmm * (mr_rear ** 2)
            physics.front_wheel_rate_nmm = front_wheel_rate
            physics.rear_wheel_rate_nmm = rear_wheel_rate

            # ── Layer 1: Platform + lap gain ──────────────────────
            if is_track_profile and "platform_risk" in active:
                physics.front_excursion_mm = damped_excursion_mm(
                    v_p99_front, m_eff_front, front_heave_nmm,
                    tyre_vertical_rate_nmm=tyre_vr,
                    parallel_wheel_rate_nmm=front_wheel_rate * 0.5,
                )
                physics.rear_excursion_mm = damped_excursion_mm(
                    v_p99_rear, m_eff_rear, rear_third_nmm,
                    tyre_vertical_rate_nmm=tyre_vr,
                    parallel_wheel_rate_nmm=rear_wheel_rate * 0.5,
                )
                dyn_front_rh = 19.0
                dyn_rear_rh = 42.0
                physics.front_bottoming_margin_mm = dyn_front_rh - physics.front_excursion_mm
                physics.rear_bottoming_margin_mm = dyn_rear_rh - physics.rear_excursion_mm
                physics.stall_margin_mm = (
                    physics.front_bottoming_margin_mm - self.VORTEX_BURST_THRESHOLD_MM
                )
                physics.front_sigma_mm = physics.front_excursion_mm / 2.33
                physics.rear_sigma_mm = physics.rear_excursion_mm / 2.33

            if "platform_risk" in active:
                breakdown.platform_risk = self._compute_platform_risk(
                    params, physics, veto_reasons, soft_penalties)

            if "lap_gain" in active:
                breakdown.lap_gain_ms = self._estimate_lap_gain(params, physics)

            # Early exit for layer 1 — skip expensive LLTD/damping
            if layer <= 1:
                results.append(CandidateEvaluation(
                    params=params, family=family, breakdown=breakdown,
                    physics=physics, hard_vetoed=len(veto_reasons) > 0,
                    veto_reasons=veto_reasons, soft_penalties=soft_penalties))
                continue

            # ── Layer 2: LLTD + balance ───────────────────────────
            if "lltd" in active or "balance" in active:
                front_arb_blade = int(params.get("front_arb_blade", 1))
                rear_arb_blade = int(params.get("rear_arb_blade", 3))

                k_roll_springs_front = 2.0 * (front_wheel_rate * 1000.0) * t_f**2 * (math.pi / 180.0)
                k_roll_springs_rear = 2.0 * (rear_wheel_rate * 1000.0) * t_r**2 * (math.pi / 180.0)

                k_arb_front = arb.front_roll_stiffness(arb.front_baseline_size, front_arb_blade)
                k_arb_rear = arb.rear_roll_stiffness(arb.rear_baseline_size, rear_arb_blade)

                k_front_total = k_roll_springs_front + k_arb_front
                k_rear_total = k_roll_springs_rear + k_arb_rear
                physics.k_roll_front = k_front_total
                physics.k_roll_rear = k_rear_total

                if k_front_total + k_rear_total > 0:
                    physics.lltd = k_front_total / (k_front_total + k_rear_total)
                else:
                    physics.lltd = 0.5
                physics.lltd_error = abs(physics.lltd - target_lltd)

                # Re-score lap gain with LLTD now computed
                breakdown.lap_gain_ms = self._estimate_lap_gain(params, physics)

            if layer <= 2:
                results.append(CandidateEvaluation(
                    params=params, family=family, breakdown=breakdown,
                    physics=physics, hard_vetoed=len(veto_reasons) > 0,
                    veto_reasons=veto_reasons, soft_penalties=soft_penalties))
                continue

            # ── Layer 3: Damping ratios ───────────────────────────
            if "damping" in active:
                f_ls_comp = int(params.get("front_ls_comp", 7))
                r_ls_comp = int(params.get("rear_ls_comp", 6))
                f_hs_comp = int(params.get("front_hs_comp", 5))
                r_hs_comp = int(params.get("rear_hs_comp", 3))

                k_modal_front = front_wheel_rate + front_heave_nmm * 0.5
                k_modal_rear = rear_wheel_rate + rear_third_nmm * 0.5

                c_crit_front = 2.0 * math.sqrt(k_modal_front * 1000.0 * front_mass)
                c_crit_rear = 2.0 * math.sqrt(k_modal_rear * 1000.0 * rear_mass)

                c_ls_front = (f_ls_comp * damper.ls_force_per_click_n) / v_ls_ref
                c_ls_rear = (r_ls_comp * damper.ls_force_per_click_n) / v_ls_ref

                physics.zeta_ls_front = c_ls_front / c_crit_front if c_crit_front > 0 else 0
                physics.zeta_ls_rear = c_ls_rear / c_crit_rear if c_crit_rear > 0 else 0

                c_hs_front = (f_hs_comp * damper.hs_force_per_click_n) / v_hs_front
                c_hs_rear = (r_hs_comp * damper.hs_force_per_click_n) / v_hs_rear

                physics.zeta_hs_front = c_hs_front / c_crit_front if c_crit_front > 0 else 0
                physics.zeta_hs_rear = c_hs_rear / c_crit_rear if c_crit_rear > 0 else 0

                # Re-score with damping
                breakdown.lap_gain_ms = self._estimate_lap_gain(params, physics)

            if layer <= 3:
                # Add driver mismatch if we have a profile
                if driver_profile is not None and "damping" in active:
                    breakdown.driver_mismatch = self._compute_driver_mismatch(
                        params, physics, driver_profile, soft_penalties)
                results.append(CandidateEvaluation(
                    params=params, family=family, breakdown=breakdown,
                    physics=physics, hard_vetoed=len(veto_reasons) > 0,
                    veto_reasons=veto_reasons, soft_penalties=soft_penalties))
                continue

            # ── Layer 4: Full objective ───────────────────────────
            breakdown.driver_mismatch = self._compute_driver_mismatch(
                params, physics, driver_profile, soft_penalties)
            breakdown.telemetry_uncertainty = self._compute_telemetry_uncertainty(
                measured, session_count, soft_penalties)
            breakdown.envelope_penalty = self._compute_envelope_penalty(
                params, physics, soft_penalties)

            results.append(CandidateEvaluation(
                params=params, family=family, breakdown=breakdown,
                physics=physics, hard_vetoed=len(veto_reasons) > 0,
                veto_reasons=veto_reasons, soft_penalties=soft_penalties))

        return results

    def _estimate_lap_gain(
        self, params: dict[str, float], physics: PhysicsResult,
    ) -> float:
        """Estimate lap time gain from real physics evaluation.

        Components:
        - Mechanical grip: softer springs = more grip (diminishing returns)
        - LLTD proximity: closer to target = better balance = faster
        - Damper quality: LS front near target ζ helps entry; HS rear near target helps traction
        - DF balance proximity: each 0.1% error costs ~5ms
        - Camber optimization: proximity to optimal contact patch angle
        """
        gain = 0.0

        # ── Mechanical grip (softer = more grip, up to a point) ─────────
        # Front heave: softer gains grip. Each N/mm below 80 gains ~2.5ms.
        # Research: 20-60 ms per 10 N/mm. Platform stability gatekeeper.
        front_heave = params.get("front_heave_spring_nmm", 50.0)
        heave_grip = max(0.0, min(75.0, (80.0 - front_heave) * 2.5))
        gain += heave_grip

        # Rear wheel rate: softer rear = more rear mechanical grip for traction.
        rear_wr = physics.rear_wheel_rate_nmm
        rear_grip = max(0.0, min(45.0, (120.0 - rear_wr) * 1.5))
        gain += rear_grip

        # ── LLTD balance proximity ──────────────────────────────────────
        # Each 1% LLTD error costs ~2.5ms (tuned: old 8.0 amplified ARB blades via LLTD)
        lltd_penalty = physics.lltd_error * 100.0 * 2.5
        gain -= min(25.0, lltd_penalty)

        # ── Damper quality ──────────────────────────────────────────────
        # Damper ζ errors are secondary compared to springs and balance.
        # Each axis contributes up to ~3ms penalty (2-10 ms/click total).

        # Front LS near 0.88 = optimal entry stability
        zeta_ls_front_error = abs(physics.zeta_ls_front - 0.88)
        gain -= min(3.0, zeta_ls_front_error * 5.0)

        # Rear LS near 0.30 = optimal traction
        zeta_ls_rear_error = abs(physics.zeta_ls_rear - 0.30)
        gain -= min(3.0, zeta_ls_rear_error * 5.0)

        # HS front near 0.45 = platform control
        zeta_hs_front_error = abs(physics.zeta_hs_front - 0.45)
        gain -= min(2.5, zeta_hs_front_error * 4.0)

        # HS rear near 0.14 = maximum compliance for traction
        zeta_hs_rear_error = abs(physics.zeta_hs_rear - 0.14)
        gain -= min(2.5, zeta_hs_rear_error * 4.0)

        # ── DF balance ──────────────────────────────────────────────────
        # Each 0.1% DF balance error costs ~2ms (tuned from 45 to 20)
        # (currently constant across candidates since we don't vary ride heights)
        gain -= physics.df_balance_error_pct * 20.0

        # ── Camber optimization ─────────────────────────────────────────
        # Front camber: ~-3.0 to -3.5 compensates for body roll → -0.5 dynamic
        front_camber = params.get("front_camber_deg", -3.5)
        front_camber_target = -3.0
        camber_error = abs(front_camber - front_camber_target)
        gain -= min(25.0, camber_error * 20.0)

        rear_camber = params.get("rear_camber_deg", -2.5)
        rear_camber_target = -2.0
        rear_camber_error = abs(rear_camber - rear_camber_target)
        gain -= min(20.0, rear_camber_error * 15.0)

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
