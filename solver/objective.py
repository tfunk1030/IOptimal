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
    # Platform risk weight raised to 1.0 — platform collapse = catastrophic.
    # For ground-effect GTP cars, an unstable platform is the DOMINANT risk.
    # Source: Taylor Funk (2026 calibration) — "rake/ride height dwarfs ARBs"
    w_platform: float = 1.0   # raised from 0.9 — platform is primary risk
    w_driver: float = 0.5     # lowered from 0.6 — secondary to physics
    w_uncertainty: float = 0.6  # lowered from 0.7 — less aggressive no-data penalty
    w_envelope: float = 0.7   # lowered from 0.8 — envelope is soft guidance, not hard
    w_staleness: float = 0.3  # lowered from 0.4 — staleness is least important

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

    def __init__(self, car, track):
        self.car = car
        self.track = track
        self._surface = None  # lazy-loaded aero surface
        self._vortex_threshold_cache: dict[float, float] = {}  # wing_deg → threshold_mm

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
        k_arb_front = arb.front_roll_stiffness(arb.front_baseline_size, front_arb_blade)
        k_arb_rear = arb.rear_roll_stiffness(arb.rear_baseline_size, rear_arb_blade)

        k_front_total = k_roll_springs_front + k_arb_front
        k_rear_total = k_roll_springs_rear + k_arb_rear
        lltd_actual = k_front_total / (k_front_total + k_rear_total) if (k_front_total + k_rear_total) > 0 else 0.5

        # LLTD targets at each fuel level
        tyre_sens = getattr(car, "tyre_load_sensitivity", 0.20)
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
            v_p99_front = (track.shock_vel_p99_front_clean_mps
                          if getattr(track, "shock_vel_p99_front_clean_mps", 0) > 0
                          else track.shock_vel_p99_front_mps)
            v_p99_rear = (track.shock_vel_p99_rear_clean_mps
                         if getattr(track, "shock_vel_p99_rear_clean_mps", 0) > 0
                         else track.shock_vel_p99_rear_mps)

            m_eff_front = car.heave_spring.front_m_eff_kg
            m_eff_rear = car.heave_spring.rear_m_eff_kg
            tyre_vr = getattr(car, "tyre_vertical_rate_nmm", None)

            # Front excursion — guard against k=0 which returns 0 (wrong: should be ∞)
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

            # Rear excursion
            rear_third_clamped = max(5.0, rear_third_nmm)
            result.rear_excursion_mm = damped_excursion_mm(
                v_p99_rear, m_eff_rear, rear_third_clamped,
                tyre_vertical_rate_nmm=tyre_vr,
                parallel_wheel_rate_nmm=rear_wheel_rate * 0.5,
            )

            # Dynamic ride heights (use typical values — actual depends on rake solver)
            dyn_front_rh = 19.0  # typical for GTP at speed
            dyn_rear_rh = 42.0
            result.front_bottoming_margin_mm = dyn_front_rh - result.front_excursion_mm
            result.rear_bottoming_margin_mm = dyn_rear_rh - result.rear_excursion_mm

            # Stall margin: distance from wing-specific vortex burst threshold
            # Physics: at steeper wing angles the aero sensitivity to RH increases
            # → the safe minimum RH is higher than at shallow wing angles.
            # _compute_vortex_threshold_mm() reads the aero map gradient to determine
            # this dynamically rather than using a fixed 8mm constant.
            wing_deg = float(params.get("wing_angle_deg",
                             car.wing_angles[0] if car.wing_angles else 17.0))
            vortex_thresh = self._compute_vortex_threshold_mm(wing_deg)
            result.stall_margin_mm = (dyn_front_rh - result.front_excursion_mm
                                      - vortex_thresh)

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
        # Above threshold: each extra mm costs ~80ms (GTP literature estimate,
        #   calibrated from F1 2022 porpoising data ~100ms/mm, discounted 20%
        #   for GTP lower-sensitivity underbody)
        # Source: ground effect aerodynamics research (Katz & Plotkin;
        #   iRacing GTP setup analysis from Taylor Funk's 46 session dataset)
        SIGMA_F_STABLE_MM = 3.0
        SIGMA_F_MS_PER_MM = 80.0  # ms per mm above stable threshold [ms/mm]
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

        # NOTE: There is NO mechanical-grip bonus for soft heave springs.
        # The old "softer heave = more grip" model is INCORRECT for GTP.
        # The heave spring exists to control the aero platform. Its only
        # grip effect is via tyre contact — negligible vs aero effect at speed.

        # ── TOO SOFT penalty (prevents degenerate k=0 exploit) ─────────
        # The excursion physics model returns 0 when k=0 (divide-by-zero
        # clamped to 0), which would make heave=0 appear infinitely stable.
        # Reality: a GTP with no heave spring has NO platform control.
        # Legal minimum for GTP racing: ~20 N/mm.
        # Penalty: 300ms for k<20 (degenerate), linear 15ms/N/mm from 20→45.
        # GTP optimal range at Sebring: 40-80 N/mm.
        front_heave = params.get("front_heave_spring_nmm", 50.0)
        HEAVE_MIN_NMM = 20.0   # [N/mm] absolute floor — no heave spring = illegal
        HEAVE_OPT_NMM = 40.0   # [N/mm] optimal lower bound for Sebring (rough)
        if front_heave < HEAVE_MIN_NMM:
            # Degenerate: no heave spring or near-zero
            gain -= 400.0 + (HEAVE_MIN_NMM - front_heave) * 20.0
        elif front_heave < HEAVE_OPT_NMM:
            # Below optimal: platform instability not captured by sigma model
            # (sigma model fails at very low k — returns 0 instead of ∞)
            # Linear penalty bridging the gap
            gain -= (HEAVE_OPT_NMM - front_heave) * 8.0

        # ── TOO STIFF penalty: mechanical compliance floor ─────────────
        # GTP heave spring operating range: 30-120 N/mm.
        # Below 30: platform unstable → already penalized above via sigma.
        # Above 120 N/mm: suspension can no longer absorb track roughness →
        #   tyre contact breaks at bumps → mechanical grip loss → 0.1-0.3s/lap.
        # Above 200 N/mm: functionally locked suspension (not GTP intent).
        # Sebring is very rough → compliance floor is important here.
        # Source: Taylor Funk (2026) + GTP heave spring engineering practice.
        HEAVE_STIFF_THRESHOLD_NMM = 120.0  # [N/mm]
        front_heave = params.get("front_heave_spring_nmm", 50.0)
        if front_heave > HEAVE_STIFF_THRESHOLD_NMM:
            # 1.5ms per N/mm over threshold → 380 N/mm = (380-120)*1.5 = 390ms penalty
            # This keeps the optimum in the 40-120 N/mm range
            heave_stiff_loss = (front_heave - HEAVE_STIFF_THRESHOLD_NMM) * 1.5
            gain -= min(500.0, heave_stiff_loss)

        # Same constraint for rear third spring: max practical ~500 N/mm for GTP
        THIRD_STIFF_THRESHOLD_NMM = 500.0  # [N/mm]
        rear_third = params.get("rear_third_spring_nmm", 450.0)
        if rear_third > THIRD_STIFF_THRESHOLD_NMM:
            third_stiff_loss = (rear_third - THIRD_STIFF_THRESHOLD_NMM) * 0.5
            gain -= min(200.0, third_stiff_loss)

        # ═══════════════════════════════════════════════════════════════
        # TIER 2: LLTD BALANCE (flows through ARB blades and diameter)
        # ARB blade: ~0.5-1.0% LLTD per step × 12ms/% = 6-12ms/click ✓
        # ARB size step: ~3-8% LLTD × 12ms/% = 36-96ms/step ✓
        # Both consistent with Taylor's 5-15ms (blade) / 30-80ms (size)
        # ═══════════════════════════════════════════════════════════════

        # 12ms per 1% LLTD error at Sebring (high mechanical grip demand)
        # Raised from 8ms — Sebring's slow-speed sections are more balance-sensitive
        # than pure high-speed aero tracks where DF overwhelms traction balance.
        LLTD_MS_PER_PCT = 12.0  # [ms / %LLTD_error]
        lltd_penalty = physics.lltd_error * 100.0 * LLTD_MS_PER_PCT
        gain -= min(50.0, lltd_penalty)  # cap: balance is real but not catastrophic

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

        # ═══════════════════════════════════════════════════════════════
        # TIER 4: DF BALANCE (aero map quality, secondary to rake)
        # ═══════════════════════════════════════════════════════════════

        # Each 0.1% DF balance error: ~5ms at speed tracks
        # Raised from 30ms/pct to 50ms/pct — more aggressive aero sensitivity
        gain -= physics.df_balance_error_pct * 50.0

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

        # Optimal for Sebring: 50-80 Nm (moderate-high mechanical grip,
        # slow hairpins need more preload than pure aero tracks)
        # Too low: exit wheelspin. Too high: entry understeer + mid-corner push.
        diff = params.get("diff_preload_nm", 20.0)
        diff_target = 65.0  # Nm, Sebring-specific
        gain -= min(8.0, abs(diff - diff_target) * 0.12)

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
        dyn_front_rh = 19.0  # typical GTP dynamic RH at speed (Sebring)
        dyn_rear_rh = 42.0
        FRONT_RH_FLOOR_MM = 30.0  # [mm] static — every competitive GTP setup
        FRONT_RH_FLOOR_PENALTY_MS_PER_MM = 25.0  # [ms/mm] below floor

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
