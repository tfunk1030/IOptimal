"""Multi-objective scoring function for setup candidates.

Turns the optimization objective into a single canonical score with
a transparent breakdown. Every candidate shows exactly why it ranked
where it did.

Score formula:
    total_score = (
        + w_lap_gain * lap_gain_ms
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

import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

from solver.scenario_profiles import get_scenario_profile
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
    # Damping (sentinel defaults; actual targets read from car.damper.zeta_target_* at eval time)
    # WARNING: 0.0 = not yet populated. Scoring code must check and use car model values.
    zeta_ls_front: float = 0.0    # Populated per car during evaluation (was 0.68 BMW default)
    zeta_ls_rear: float = 0.0     # Populated per car during evaluation (was 0.23 BMW default)
    zeta_hs_front: float = 0.0    # Populated per car during evaluation (was 0.47 BMW default)
    zeta_hs_rear: float = 0.0     # Populated per car during evaluation (was 0.20 BMW default)
    # Wheel rates
    front_wheel_rate_nmm: float = 30.0
    rear_wheel_rate_nmm: float = 60.0
    # Roll stiffness
    k_roll_front: float = 0.0
    k_roll_rear: float = 0.0


@dataclass
class LapGainBreakdown:
    """Penalty components that make up raw lap-gain scoring."""

    lltd_balance_ms: float = 0.0
    damping_ms: float = 0.0
    rebound_ratio_ms: float = 0.0
    df_balance_ms: float = 0.0
    camber_ms: float = 0.0
    diff_preload_ms: float = 0.0
    arb_extreme_ms: float = 0.0
    diff_ramp_ms: float = 0.0
    diff_clutch_ms: float = 0.0
    tc_ms: float = 0.0
    carcass_ms: float = 0.0   # penalty for tyre carcass temp outside optimal window

    @property
    def total_penalty_ms(self) -> float:
        return (
            self.lltd_balance_ms
            + self.damping_ms
            + self.rebound_ratio_ms
            + self.df_balance_ms
            + self.camber_ms
            + self.diff_preload_ms
            + self.arb_extreme_ms
            + self.diff_ramp_ms
            + self.diff_clutch_ms
            + self.tc_ms
            + self.carcass_ms
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "lltd_balance_ms": self.lltd_balance_ms,
            "damping_ms": self.damping_ms,
            "rebound_ratio_ms": self.rebound_ratio_ms,
            "df_balance_ms": self.df_balance_ms,
            "camber_ms": self.camber_ms,
            "diff_preload_ms": self.diff_preload_ms,
            "arb_extreme_ms": self.arb_extreme_ms,
            "diff_ramp_ms": self.diff_ramp_ms,
            "diff_clutch_ms": self.diff_clutch_ms,
            "tc_ms": self.tc_ms,
            "carcass_ms": self.carcass_ms,
        }


@dataclass
class ObjectiveBreakdown:
    """Full scoring breakdown — never rank on a black box."""
    lap_gain_ms: float = 0.0
    lap_gain_detail: LapGainBreakdown = field(default_factory=LapGainBreakdown)
    platform_risk: PlatformRisk = field(default_factory=PlatformRisk)
    driver_mismatch: DriverMismatch = field(default_factory=DriverMismatch)
    telemetry_uncertainty: TelemetryUncertainty = field(default_factory=TelemetryUncertainty)
    envelope_penalty: EnvelopePenalty = field(default_factory=EnvelopePenalty)
    staleness_penalty_ms: float = 0.0

    # Weights (explicit and tunable)
    w_lap_gain: float = 1.0
    # Platform risk weight raised to 1.0 — platform collapse = catastrophic.
    # For ground-effect GTP cars, an unstable platform is the DOMINANT risk.
    # Source: Taylor Funk (2026 calibration) — "rake/ride height dwarfs ARBs"
    # NOTE (2026-03-27): Damper zeta targets updated from hardcoded (0.88/0.30/
    # 0.45/0.14) to IBT-calibrated values (0.68/0.23/0.47/0.20) and penalty
    # scaling halved. Previous targets caused damping_ms and rebound_ratio_ms
    # to correlate positively with lap time (wrong direction: Spearman +0.19
    # and +0.33 respectively). Weight search recommends lap_gain=1.25 with
    # penalties near zero — applied in scenario_profiles.py single_lap_safe.
    # See: validation/calibration_report.md
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
            self.w_lap_gain * self.lap_gain_ms
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
            f"    Lap gain:            {self.w_lap_gain * self.lap_gain_ms:+.1f} ms "
            f"(raw={self.lap_gain_ms:+.1f}, w={self.w_lap_gain:.2f})",
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

    # ── Tyre thermal constants (for future IBT-driven temperature scoring) ───
    # GTP Michelin Pilot Sport Endurance operating window: 82–104 °C.
    # Source: Ken Payne (Michelin NA technical director), IMSA Michelin Insider 2018.
    # Confirmed by iRacing community: optimal 85–105 °C (simracingsetup.com 2025).
    # Lateral stiffness (Ky) penalty model — Pacejka MF thermal scaling:
    #   cold: Ky_eff = Ky_nom × (1 - TYRE_TEMP_SENS_COLD × max(0, T_min - T))
    #   hot:  Ky_eff = Ky_nom × (1 - TYRE_TEMP_SENS_HOT  × max(0, T - T_max))
    # where T_min/T_max come from car.tyre_opt_temp_min_c / tyre_opt_temp_max_c.
    # These constants mirror the car model defaults (research/physics-notes.md 2026-03-26).
    # When IBT tyre temp channels (LFtempM, RFtempM, etc.) become available in
    # track profiles, plug them into this model to score temperature management.
    TYRE_TEMP_SENS_COLD = 0.010  # Ky loss per °C below T_min (~20% at 20°C cold)
    TYRE_TEMP_SENS_HOT  = 0.015  # Ky loss per °C above T_max (~15% at 10°C hot)

    def __init__(self, car, track, explore: bool = False, scenario_profile: str | None = None):
        self.car = car
        self.track = track
        self.explore = explore  # when True: zero k-NN weight, no empirical anchoring
        self._scenario_profile = get_scenario_profile(scenario_profile)
        self._surface = None  # lazy-loaded aero surface
        self._vortex_threshold_cache: dict[float, float] = {}  # wing_deg → threshold_mm
        # Empirical heave spring calibration — loads from real IBT telemetry data.
        # Falls back to physics model if no calibration file exists yet.
        from solver.heave_calibration import HeaveCalibration

        # Resolve car slug: "bmw m hybrid v8" → "bmw", "cadillac v-series.r" → "cadillac"
        _car_raw = car.name if hasattr(car, "name") else str(car)
        _car_slug = _car_raw.lower().split()[0].replace("-", "").replace(".", "")
        self._car_slug = _car_slug

        # Resolve track slug: handles dict (track json), object with .name, or string
        if isinstance(track, dict):
            _track_raw = track.get("track_name") or track.get("name") or str(track)
        else:
            _track_raw = getattr(track, "name", None) or getattr(track, "track_name", None) or str(track)
        _track_slug = str(_track_raw).lower().split()[0].replace("-", "").replace("_", "")
        self._track_slug = _track_slug

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
                self._car_slug, self._track_slug
            )
        except Exception as e:
            logger.debug("Session DB init failed: %s", e)
            self._session_db = None

    def _new_breakdown(self) -> ObjectiveBreakdown:
        weights = self._scenario_profile.objective
        # Gate empirical weight on session count: only enable when the
        # SessionDatabase has >= 10 sessions for this car/track pair.
        # Sparse data (< 10) produces noisy k-NN predictions.
        _w_emp = weights.w_empirical
        if _w_emp > 0.0 and self._session_db is not None:
            _n_sessions = len(getattr(self._session_db, "sessions", []))
            if _n_sessions < 10:
                _w_emp = 0.0  # not enough data for reliable k-NN
        breakdown = ObjectiveBreakdown(
            w_lap_gain=weights.w_lap_gain,
            w_platform=weights.w_platform,
            w_driver=weights.w_driver,
            w_uncertainty=weights.w_uncertainty,
            w_envelope=weights.w_envelope,
            w_staleness=weights.w_staleness,
            w_empirical=_w_emp,
        )
        if self.explore:
            breakdown.w_empirical = 0.0
        return breakdown

    def _heave_calibration_uncertainty_penalty_ms(self, front_heave: float) -> float:
        cal_uncertainty = self._heave_cal.uncertainty(front_heave)
        if cal_uncertainty <= 0.2:
            return 0.0
        return (cal_uncertainty - 0.2) ** 1.5 * 8.0

    def _heave_realism_penalty_ms(self, front_heave: float) -> float:
        # Keep solver search inside a realistic GTP heave window even when
        # the forward physics looks artificially "safe" at very stiff rates.
        # This remains an envelope/realism concern, not raw lap-gain.
        # Read per-car realistic operating range instead of BMW-hardcoded (30, 100).
        # Falls back to garage range if no realistic range is defined.
        heave_opt_lo, heave_opt_hi = 30.0, 100.0  # ultimate fallback
        _hs = self.car.heave_spring
        _realistic = _hs.front_realistic_range_nmm
        if _realistic is not None and len(_realistic) >= 2:
            heave_opt_lo, heave_opt_hi = _realistic[0], _realistic[1]
        else:
            _range = _hs.front_spring_range_nmm
            if _range is not None and len(_range) >= 2:
                    heave_opt_lo, heave_opt_hi = _range[0], _range[1]
        if front_heave > heave_opt_hi:
            excess = front_heave - heave_opt_hi
            return min(300.0, excess * 0.5 + (max(0.0, excess - 100.0) ** 1.2) * 0.3)
        if 0.0 < front_heave < heave_opt_lo:
            return min(45.0, (heave_opt_lo - front_heave) * 1.5)
        return 0.0

    def _df_balance_lap_penalty_ms(self, physics: PhysicsResult) -> float:
        ms_per_pct = 20.0
        return physics.df_balance_error_pct * ms_per_pct

    def _camber_lap_penalty_ms(self, front_camber: float, rear_camber: float) -> float:
        front_penalty = min(8.0, abs(front_camber - (-3.0)) * 5.0)
        rear_penalty = min(6.0, abs(rear_camber - (-2.0)) * 4.0)
        return front_penalty + rear_penalty

    @staticmethod
    def _arb_size_label(raw: object, labels: list[str] | tuple[str, ...], baseline: str) -> str:
        if isinstance(raw, str) and raw:
            return raw
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            idx = int(round(float(raw)))
            if labels and 0 <= idx < len(labels):
                return str(labels[idx])
        return str(baseline)

    @classmethod
    def _arb_size_index(
        cls,
        raw: object,
        labels: list[str] | tuple[str, ...],
        baseline: str,
        *,
        default: int = 0,
    ) -> int:
        if labels:
            label = cls._arb_size_label(raw, labels, baseline)
            if label in labels:
                return labels.index(label)
        try:
            return int(round(float(raw)))
        except (TypeError, ValueError):
            return int(default)

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
        except Exception as e:
            logger.debug("Aero surface lookup failed: %s", e)
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
        # Use car-specific coupling — defaults to 0.0 for uncalibrated cars.
        # Only BMW/Sebring (γ=0.25) has IBT validation for this term.
        coupling = self.car.torsion_arb_coupling
        if coupling == 0.0:
            return 1.0  # no coupling for this car — standard parallel-element model
        # Relative stiffness ratio: (OD/OD_ref)^4 (same OD^4 law as wheel rate)
        stiffness_ratio = (front_torsion_od / od_ref) ** 4
        return 1.0 + coupling * (stiffness_ratio - 1.0)

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

        except Exception as e:
            logger.debug("Vortex threshold computation failed: %s", e)

        self._vortex_threshold_cache[wing_deg] = threshold
        return threshold

    def _compute_lltd_fuel_window(
        self,
        params: dict[str, float],
        fuel_start_l: float | None = None,
        fuel_end_l: float | None = None,
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

        # Read per-car fuel loads from the car model (no BMW fallbacks).
        if fuel_start_l is None:
            fuel_start_l = car.fuel_capacity_l
        if fuel_end_l is None:
            fuel_end_l = car.fuel_stint_end_l

        # Compute weight distributions at start and end fuel
        mass_start = car.total_mass(fuel_start_l)
        mass_end = car.total_mass(fuel_end_l)

        # Front weight fraction at each fuel load
        # If car has fuel_cg_x data, use it; otherwise assume fuel is at mid-car CG
        # BMW fuel tank is slightly rear-biased from center
        front_pct_start = car.weight_dist_front
        front_pct_end = car.weight_dist_front

        if hasattr(car, 'fuel_cg_frac') and car.fuel_cg_frac is not None:
            # Compute actual CG shift from fuel burn using car's fuel CG position.
            # fuel_cg_frac is distance from front axle as fraction of wheelbase
            # (0 = front axle, 1 = rear axle), so front load fraction = 1 - fuel_cg_frac.
            fuel_burned = fuel_start_l - fuel_end_l
            fuel_mass_burned = fuel_burned * car.fuel_density_kg_per_l
            fuel_front_frac = 1.0 - car.fuel_cg_frac
            # Wf_end = (Wf_start * m_start - fuel_front_frac * fuel_mass_burned) / m_end
            front_mass_start = front_pct_start * mass_start
            front_mass_end = front_mass_start - fuel_front_frac * fuel_mass_burned
            front_pct_end = front_mass_end / mass_end
        else:
            # Fallback: no fuel CG data — assume fuel is at mid-car CG
            front_pct_end = front_pct_start + 0.003

        # LLTD from roll stiffness (same spring/ARB values → fixed during stint)
        front_heave_nmm = params.get("front_heave_spring_nmm", 50.0)
        rear_third_nmm = params.get("rear_third_spring_nmm", 450.0)
        rear_spring_nmm = params.get("rear_spring_rate_nmm", 160.0)
        front_torsion_od = params.get("front_torsion_od_mm",
                                       car.corner_spring.front_torsion_od_options[0]
                                       if car.corner_spring.front_torsion_od_options else 0.0)
        front_arb_blade = int(params.get("front_arb_blade", 1))
        rear_arb_blade = int(params.get("rear_arb_blade", 3))

        # ── Front and rear corner wheel rates ───────────────────────────
        # Ferrari uses indexed torsion bars at BOTH ends.
        # Use FerrariIndexedControlModel for both axles — calibrated from garage screenshots.
        # All other cars (BMW/Cadillac/Porsche/Acura): BMW path — c_torsion*OD^4 front,
        # rear_spring_nmm*MR^2 rear (Dallara coil spring architecture).
        _ferrari_controls = car.ferrari_indexed_controls
        if _ferrari_controls is not None:
            _ftb_idx = float(params.get("front_torsion_bar_index", 2.0))
            _rtb_idx = float(params.get("rear_torsion_bar_index", 2.0))
            front_wheel_rate = _ferrari_controls.front_torsion_rate_from_index(_ftb_idx)
            rear_wheel_rate = _ferrari_controls.rear_torsion_rate_from_index(_rtb_idx)
        else:
            c_torsion = car.corner_spring.front_torsion_c
            if c_torsion > 0:
                front_wheel_rate = c_torsion * (front_torsion_od ** 4)
            else:
                front_wheel_rate = car.corner_spring.front_roll_spring_rate_nmm
            mr_rear = car.corner_spring.rear_motion_ratio
            rear_wheel_rate = rear_spring_nmm * (mr_rear ** 2)

        arb = car.arb
        t_f = arb.track_width_front_mm / 2000.0
        t_r = arb.track_width_rear_mm / 2000.0
        k_roll_springs_front = 2.0 * (front_wheel_rate * 1000.0) * t_f**2 * (math.pi / 180.0)
        k_roll_springs_rear = 2.0 * (rear_wheel_rate * 1000.0) * t_r**2 * (math.pi / 180.0)
        k_arb_front_base = arb.front_roll_stiffness(arb.front_baseline_size, front_arb_blade)
        k_arb_rear = arb.rear_roll_stiffness(arb.rear_baseline_size, rear_arb_blade)
        # torsion_arb_coupling=0.0 for Ferrari (set in cars.py) — standard parallel model.
        # γ=0.25 coupling only applies to BMW where it was back-calibrated from IBT LLTD.
        k_arb_front = k_arb_front_base * self._torsion_arb_coupling_factor(front_torsion_od)

        k_front_total = k_roll_springs_front + k_arb_front
        k_rear_total = k_roll_springs_rear + k_arb_rear
        lltd_actual = k_front_total / (k_front_total + k_rear_total) if (k_front_total + k_rear_total) > 0 else 0.5

        # LLTD targets at each fuel level.
        # Use car.measured_lltd_target when available (IBT-calibrated override).
        # For fuel window analysis, we still model the shift with fuel load,
        # but anchor to the measured target instead of the theoretical formula.
        tyre_sens = car.tyre_load_sensitivity
        _measured_lltd_target = car.measured_lltd_target
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

        # ── Extract parameters from per-car attributes (no BMW fallbacks) ──
        front_heave_nmm = params.get("front_heave_spring_nmm",
                                      car.front_heave_spring_nmm)
        rear_third_nmm = params.get("rear_third_spring_nmm",
                                     car.rear_third_spring_nmm)
        rear_spring_nmm = params.get("rear_spring_rate_nmm",
                                      car.corner_spring.rear_spring_range_nmm[0])
        # Torsion OD: use car's options if defined, else 0.0 for cars without
        # torsion bars (Porsche / Acura).
        _od_options = car.corner_spring.front_torsion_od_options
        _od_default = _od_options[0] if _od_options else 0.0
        front_torsion_od = params.get("front_torsion_od_mm", _od_default)
        front_camber = params.get("front_camber_deg",
                                   car.geometry.front_camber_baseline_deg)
        rear_camber = params.get("rear_camber_deg",
                                  car.geometry.rear_camber_baseline_deg)
        front_arb_blade = int(params.get("front_arb_blade",
                                          car.arb.front_baseline_blade))
        rear_arb_blade = int(params.get("rear_arb_blade",
                                         car.arb.rear_baseline_blade))

        # Damper clicks — defaults from per-car baselines (not BMW hardcodes)
        _dm = car.damper
        f_ls_comp = int(params.get("front_ls_comp", _dm.front_ls_comp_baseline))
        f_ls_rbd = int(params.get("front_ls_rbd", _dm.front_ls_rbd_baseline))
        f_hs_comp = int(params.get("front_hs_comp", _dm.front_hs_comp_baseline))
        f_hs_rbd = int(params.get("front_hs_rbd", _dm.front_hs_rbd_baseline))
        r_ls_comp = int(params.get("rear_ls_comp", _dm.rear_ls_comp_baseline))
        r_ls_rbd = int(params.get("rear_ls_rbd", _dm.rear_ls_rbd_baseline))
        r_hs_comp = int(params.get("rear_hs_comp", _dm.rear_hs_comp_baseline))
        r_hs_rbd = int(params.get("rear_hs_rbd", _dm.rear_hs_rbd_baseline))

        # ── Wheel rates ─────────────────────────────────────────────────
        # Ferrari: indexed torsion bars at both ends — use FerrariIndexedControlModel.
        # Other cars: c_torsion*OD^4 front + rear_spring_nmm*MR^2 rear (Dallara coil).
        _ferrari_controls = car.ferrari_indexed_controls
        if _ferrari_controls is not None:
            _ftb_idx = float(params.get("front_torsion_bar_index", 2.0))
            _rtb_idx = float(params.get("rear_torsion_bar_index", 2.0))
            front_wheel_rate = _ferrari_controls.front_torsion_rate_from_index(_ftb_idx)
            rear_wheel_rate = _ferrari_controls.rear_torsion_rate_from_index(_rtb_idx)
        else:
            c_torsion = car.corner_spring.front_torsion_c
            if c_torsion > 0:
                front_wheel_rate = c_torsion * (front_torsion_od ** 4)
            else:
                front_wheel_rate = car.corner_spring.front_roll_spring_rate_nmm
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
            _vortex_pctile = car.vortex_excursion_pctile
            if _vortex_pctile == "p95":
                v_vortex_front = (track.shock_vel_p95_front_clean_mps
                                  if getattr(track, "shock_vel_p95_front_clean_mps", 0) > 0
                                  else track.shock_vel_p95_front_mps)
            else:
                v_vortex_front = v_p99_front  # legacy behaviour

            m_eff_front = car.heave_spring.front_m_eff_kg
            m_eff_rear = car.heave_spring.rear_m_eff_kg
            tyre_vr_front = car.tyre_vertical_rate_front_nmm
            tyre_vr_rear = car.tyre_vertical_rate_rear_nmm
            if tyre_vr_front is None or tyre_vr_front <= 0:
                import logging
                logging.getLogger(__name__).warning(
                    "tyre_vertical_rate_front_nmm is %s — excursion uses "
                    "suspension-only model (no tyre compliance in series)",
                    tyre_vr_front,
                )
            if tyre_vr_rear is None or tyre_vr_rear <= 0:
                import logging
                logging.getLogger(__name__).warning(
                    "tyre_vertical_rate_rear_nmm is %s — excursion uses "
                    "suspension-only model (no tyre compliance in series)",
                    tyre_vr_rear,
                )

            # Front excursion at p99 — for bottoming margin (worst-case bump)
            # Guard against k=0 which returns 0 (wrong: should be ∞)
            # GTP heave spring ≥ 20 N/mm in practice. k=0 is physically degenerate.
            front_heave_clamped = max(5.0, front_heave_nmm)  # prevent div/zero physics
            # parallel_wheel_rate is halved because front_wheel_rate is the
            # per-axle total (2 corners), but the excursion model computes
            # per-corner dynamics. Each corner sees half the axle wheel rate
            # in parallel with the heave spring.
            result.front_excursion_mm = damped_excursion_mm(
                v_p99_front, m_eff_front, front_heave_clamped,
                tyre_vertical_rate_nmm=tyre_vr_front,
                parallel_wheel_rate_nmm=front_wheel_rate * 0.5,
            )
            # Override: if heave spring < 20 N/mm, cap excursion at full travel (30mm)
            # so sigma reflects the true aero instability risk
            if front_heave_nmm < 20.0:
                result.front_excursion_mm = max(result.front_excursion_mm, 30.0)

            # Front excursion at vortex percentile (p95) — for stall margin
            _front_vortex_excursion_mm = damped_excursion_mm(
                v_vortex_front, m_eff_front, front_heave_clamped,
                tyre_vertical_rate_nmm=tyre_vr_front,
                parallel_wheel_rate_nmm=front_wheel_rate * 0.5,
            )

            # Rear excursion at p99 — for bottoming margin
            rear_third_clamped = max(5.0, rear_third_nmm)
            result.rear_excursion_mm = damped_excursion_mm(
                v_p99_rear, m_eff_rear, rear_third_clamped,
                tyre_vertical_rate_nmm=tyre_vr_rear,
                parallel_wheel_rate_nmm=rear_wheel_rate * 0.5,
            )

            # Dynamic ride heights (use car compression model when available).
            # static_front_rh - aero_compression → mean floor height at speed.
            # Aero compression scales with V²; the relevant operating-point speed
            # is the V²-RMS of the lap (track.aero_reference_speed_kph) — NOT the
            # median, because compression is dominated by high-speed sections and
            # underpredicted at the lap median. Calibrated 2026-04-07 against 4
            # Porsche/Algarve IBTs (24 speed-binned data points): median 174 kph
            # under-predicts front comp by ~3 mm; V²-RMS 200 kph matches IBT
            # measured to within 1 mm.
            _op_speed = (
                getattr(track, "aero_reference_speed_kph", 0.0)
                or getattr(track, "median_speed_kph", 0.0)
                or car.aero_compression.ref_speed_kph
            )
            # Front static: prefer the calibrated GarageOutputModel compliance
            # prediction at the candidate's pushrod offset, so the objective can
            # SEE the front_pushrod_offset_mm dimension. Falls back to the legacy
            # pinned static for cars without a garage_output_model (BMW path).
            _gom = car.active_garage_output_model(
                getattr(track, "track_name", None)
            ) if hasattr(car, "active_garage_output_model") else None
            _front_pushrod_param = params.get("front_pushrod_offset_mm", None)
            if _gom is not None and _front_pushrod_param is not None:
                try:
                    from car_model.garage import GarageSetupState
                    _baseline = _gom.default_state(fuel_l=0.0)
                    _state = GarageSetupState(
                        front_pushrod_mm=float(_front_pushrod_param),
                        rear_pushrod_mm=float(params.get("rear_pushrod_offset_mm", _baseline.rear_pushrod_mm)),
                        front_heave_nmm=float(params.get("front_heave_spring_nmm", _baseline.front_heave_nmm)),
                        front_heave_perch_mm=_baseline.front_heave_perch_mm,
                        rear_third_nmm=float(params.get("rear_third_spring_nmm", _baseline.rear_third_nmm)),
                        rear_third_perch_mm=_baseline.rear_third_perch_mm,
                        front_torsion_od_mm=float(params.get("front_torsion_od_mm", _baseline.front_torsion_od_mm)),
                        rear_spring_nmm=float(params.get("rear_spring_rate_nmm", _baseline.rear_spring_nmm)),
                        rear_spring_perch_mm=_baseline.rear_spring_perch_mm,
                        front_camber_deg=_baseline.front_camber_deg,
                        rear_camber_deg=_baseline.rear_camber_deg,
                        fuel_l=0.0,
                    )
                    _static_f = float(_gom.predict_front_static_rh(_state))
                    _rear_static = float(_gom.predict_rear_static_rh(_state))
                except Exception as e:
                    logger.debug("Garage model RH prediction failed: %s", e)
                    _static_f = car.pushrod.front_pinned_rh_mm
                    _rear_static = car.pushrod.rear_rh_for_offset(
                        float(params.get("rear_pushrod_offset_mm", 0.0))
                    )
            else:
                _static_f = car.pushrod.front_pinned_rh_mm
                _rear_static = car.pushrod.rear_rh_for_offset(
                    float(params.get("rear_pushrod_offset_mm", 0.0))
                )
            _comp_f = car.aero_compression.front_at_speed(_op_speed)
            _rear_comp = car.aero_compression.rear_at_speed(_op_speed)
            # Clamp to sim minimums for safety
            _static_f = max(_static_f, car.min_front_rh_static)
            _rear_static = max(_rear_static, car.min_rear_rh_static)
            dyn_front_rh = max(5.0, _static_f - _comp_f)
            dyn_rear_rh = max(5.0, _rear_static - _rear_comp)
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
        front_arb_size = self._arb_size_label(_f_arb_size_raw, arb.front_size_labels, arb.front_baseline_size)
        rear_arb_size = self._arb_size_label(_r_arb_size_raw, arb.rear_size_labels, arb.rear_baseline_size)
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

        # ── LLTD override for cars with constant measured LLTD ──────────
        # Ferrari 499P: LLTD is effectively constant at 0.510±0.002 across 19 sessions
        # despite torsion bars ranging idx 2-8 and ARBs from A/1 to E/5.
        # The component calculation (torsion bars + ARBs) gives 0.35-0.43 — WRONG —
        # because the individual stiffness values can't be resolved from available data.
        # Use the measured constant directly when the car's LLTD doesn't vary with setup.
        _measured_lltd_target = car.measured_lltd_target
        _ferrari_controls = car.ferrari_indexed_controls
        if _ferrari_controls is not None and _measured_lltd_target is not None:
            # Ferrari: LLTD is a car constant — use measured value directly
            result.lltd = _measured_lltd_target
            result.lltd_error = 0.0  # zero error — LLTD is not tunable
        else:
            # All other cars: LLTD is computed from components and scored vs target
            tyre_sens = car.tyre_load_sensitivity
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
            except Exception as e:
                logger.debug("Aero balance/L:D scoring failed: %s", e)

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

        breakdown = self._new_breakdown()
        veto_reasons: list[str] = []
        soft_penalties: list[str] = []

        # ── 1. Lap gain from physics ────────────────────────────────────
        breakdown.lap_gain_detail = self._compute_lap_gain_breakdown(params, physics)
        breakdown.lap_gain_ms = self._estimate_lap_gain(params, physics)

        # ── 2. Platform risk from physics ───────────────────────────────
        breakdown.platform_risk = self._compute_platform_risk(
            params, physics, veto_reasons, soft_penalties
        )

        # ── 3. Driver mismatch ──────────────────────────────────────────
        # Zero the weight when no driver profile is available — scoring an
        # always-zero term wastes weight budget and dilutes real signals.
        if driver_profile is None:
            breakdown.w_driver = 0.0
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
            except Exception as e:
                logger.debug("Empirical scoring failed: %s", e)

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
        # ── DAMPER SCORING ─────────────────────────────────────────────
        # 2026-03-26: IBT regression (73 BMW Sebring sessions) found
        # front_ls_comp is the #1 lap-time correlate (r=-0.447).
        #
        # Zeta targets kept at original values (0.88/0.30/0.45/0.14).
        # Although 0.88 is unreachable at legal click values, the DIRECTION
        # is correct: higher ls_comp → higher zeta → lower penalty, and
        # this correlates with faster laps up to clicks 8-9. The original
        # targets accidentally act as "always want more damping" which
        # matches the partial-r direction from IBT data.
        #
        # IBT-calibrated optimal zeta (top-15 fastest sessions):
        #   LS front=0.68, LS rear=0.23, HS front=0.47, HS rear=0.20
        # These are stored in PhysicsResult defaults for reference but
        # NOT used as penalty targets — the monotonic penalty direction
        # of the original targets is more useful than being centered at
        # the correct value but pulling the wrong direction near the peak.
        #
        # Future: replace zeta model with non-monotonic empirical click
        # scoring as a separate ObjectiveBreakdown component (not inside
        # lap_gain_ms) to properly capture the peak at clicks 8-9.
        # ═══════════════════════════════════════════════════════════════

        # Damping ratio targets — only apply when car damper is IBT-calibrated.
        # For uncalibrated cars (zeta_is_calibrated=False), force/click is an estimate
        # and the zeta targets were back-derived from BMW IBT. Applying BMW targets to
        # a Ferrari 40-click damper produces random noise, not a useful gradient.
        # Calibrated: BMW (73 IBT sessions). Uncalibrated: Ferrari, Porsche, Acura, Cadillac.
        if getattr(self.car.damper, "zeta_is_calibrated", False):
            zeta_ls_front_err = abs(physics.zeta_ls_front - self.car.damper.zeta_target_ls_front)
            gain -= min(4.0, zeta_ls_front_err * 5.0)

            # Rear LS: traction compliance over kerbs
            zeta_ls_rear_err = abs(physics.zeta_ls_rear - self.car.damper.zeta_target_ls_rear)
            gain -= min(3.0, zeta_ls_rear_err * 4.0)

            # Front HS
            zeta_hs_front_err = abs(physics.zeta_hs_front - self.car.damper.zeta_target_hs_front)
            gain -= min(2.5, zeta_hs_front_err * 3.5)

            # Rear HS
            zeta_hs_rear_err = abs(physics.zeta_hs_rear - self.car.damper.zeta_target_hs_rear)
            gain -= min(2.5, zeta_hs_rear_err * 3.5)
        # else: damper clicks are not scored — output will flag [ESTIMATE: damper clicks unscored]

        # ── Extract damper click params (used by compression bonus + ratio sections) ──
        f_ls_comp = params.get("front_ls_comp", 7)
        f_ls_rbd  = params.get("front_ls_rbd", 6)
        f_hs_comp = params.get("front_hs_comp", 5)
        f_hs_rbd  = params.get("front_hs_rbd", 5)
        r_ls_comp = params.get("rear_ls_comp", 5)
        r_ls_rbd  = params.get("rear_ls_rbd", 5)
        r_hs_comp = params.get("rear_hs_comp", 3)
        r_hs_rbd  = params.get("rear_hs_rbd", 3)

        # ── DAMPER COMPRESSION LEVEL (empirical, BMW-calibrated) ──────────────
        # Cross-session correlation (73 BMW/Sebring sessions): front_ls_comp has
        # r=-0.447 with lap time — the strongest single predictor under race
        # conditions. Higher compression clicks = faster laps (within legal range).
        # This is gated behind zeta_is_calibrated to only apply for cars with
        # proven correlation data. Weight: ~0.5ms per click above mid-range.
        if getattr(self.car.damper, "zeta_is_calibrated", False):
            _lo, _hi = self.car.damper.ls_comp_range
            _mid = (_lo + _hi) / 2.0
            # Reward clicks above mid-range (empirical direction: higher = faster)
            _f_comp_bonus = (f_ls_comp - _mid) * 0.5  # ms per click above mid
            _r_comp_bonus = (r_ls_comp - _mid) * 0.3  # rear is weaker predictor
            gain += max(-3.0, min(3.0, _f_comp_bonus))
            gain += max(-2.0, min(2.0, _r_comp_bonus))

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

        # Ratio target: rbd = target_ratio * comp
        # Penalty is deliberately WEAK (max 5ms) so ζ (comp) stays dominant.
        # The gradient here only moves RBD — it must never pull COMP down.
        # Implementation: rbd_target = comp * ratio_target (comp-anchored, not rbd-anchored)
        # So if comp=11 → rbd_target_LS = 11*0.95 = 10.5 → solver pushes rbd toward 10-11.
        # If rbd=5 and comp=11 → ratio=0.45 → error=0.50 → penalty=0.50*8=4ms (small).
        # This gives the coord descent a gradient on rbd without destabilizing comp.
        LS_RBD_COMP_TARGET = 0.95   # [ratio] LS rebound / LS comp target
        HS_RBD_COMP_TARGET = 0.60   # [ratio] HS rebound / HS comp target (GTP kerb rule)
        RBD_PENALTY_MS_PER_UNIT = 4.0   # halved from 8.0 — was wrong-direction correlated (+0.33 Spearman)

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
        gain -= self._df_balance_lap_penalty_ms(physics)

        # ═══════════════════════════════════════════════════════════════
        # TIER 5: CAMBER (contact patch optimization, tertiary)
        # ═══════════════════════════════════════════════════════════════

        # Front camber target: -3.0° (compensates for ~0.5° body roll at limit)
        # Rear camber target: -2.0° (less roll compensation needed)
        # Max contribution: ~8ms (small vs platform + balance terms)
        front_camber = params.get("front_camber_deg", -3.5)
        rear_camber = params.get("rear_camber_deg", -2.0)
        gain -= self._camber_lap_penalty_ms(front_camber, rear_camber)

        # ═══════════════════════════════════════════════════════════════
        # TIER 5: DIFF PRELOAD (exit traction, small effect)
        # ═══════════════════════════════════════════════════════════════

        # Diff preload: penalty for distance from moderate baseline.
        # All cars use 30 Nm as the neutral target — individual cars will converge
        # to their optimal via the empirical learner, not hardcoded overrides.
        diff = params.get("diff_preload_nm", 20.0)
        diff_target = 30.0  # Nm — moderate baseline for all cars
        gain -= min(8.0, abs(diff - diff_target) * 0.12)

        # ═══════════════════════════════════════════════════════════════
        # TIER 5: ARB SIZE (LLTD range — full steps, ~36-96ms each)
        # ═══════════════════════════════════════════════════════════════
        # ARB size controls available LLTD range. Wrong size = can't hit
        # target LLTD even with blade adjustments. Penalty on size mismatch
        # vs what's needed to achieve target LLTD with mid-range blade.
        # Soft=0, Medium=1, Stiff=2 for ordinal encoding.
        f_arb_size_idx = self._arb_size_index(
            params.get("front_arb_size", 0),
            self.car.arb.front_size_labels,
            self.car.arb.front_baseline_size,
            default=0,
        )
        r_arb_size_idx = self._arb_size_index(
            params.get("rear_arb_size", 1),
            self.car.arb.rear_size_labels,
            self.car.arb.rear_baseline_size,
            default=1,
        )
        f_arb_blade = int(round(params.get("front_arb_blade", 1)))
        r_arb_blade = int(round(params.get("rear_arb_blade", 2)))
        # ARB extreme-combo penalty ZEROED OUT (2026-03-28, calibration evidence):
        # Removing this term improved BMW/Sebring in-sample Spearman by +0.048 and
        # holdout mean by +0.062.  The heuristic (max blade + Soft = wrong size) does
        # not hold in the 75-session dataset — fast BMW setups use a range of ARB
        # size/blade combos including those this term would penalise.  The physics
        # reasoning (blade maxed → size up) is sound in principle but the lap-time
        # signal is absent, so applying it adds noise that hurts ranking quality.
        # See validation/calibration_report.md — ablation: arb_extreme_ms removed.
        # gain -= ...  (placeholder — do NOT restore without corroborating IBT evidence)

        # ═══════════════════════════════════════════════════════════════
        # TIER 5: DIFF RAMP ANGLES (corner-entry rotation + exit traction)
        # ═══════════════════════════════════════════════════════════════
        # Coast ramp controls corner-entry rotation (lower = more locking = more US)
        # Drive ramp controls exit traction (higher = less locking = more wheelspin)
        # Optimal: match to driver trail-brake depth and throttle progressiveness
        ramp_options = self.car.garage_ranges.diff_coast_drive_ramp_options
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
        # diff_ramp penalty reduced from min(12.0, 6ms/step) to min(4.0, 2ms/step)
        # (2026-03-28, calibration evidence): removing this term improved trackless
        # Spearman by +0.069 in-sample and +0.049 holdout mean.  The trail-brake→ramp
        # mapping is directionally correct but the 6ms-per-step magnitude was too
        # aggressive, causing correlated noise with driver-profile fallbacks.  Reduced
        # to 2ms/step max 4ms to keep directional signal while cutting noise floor.
        # See validation/calibration_report.md — ablation: diff_ramp_ms removed.
        gain -= min(4.0, coast_mismatch * 2.0)  # reduced from 6ms → 2ms per step mismatch

        # ═══════════════════════════════════════════════════════════════
        # TIER 5: DIFF CLUTCH PLATES (lock authority, exit traction)
        # ═══════════════════════════════════════════════════════════════
        # More plates = more lock authority at same preload.
        # Fewer plates = less mechanical traction effect, smoother.
        # GTP baseline: 4 plates for traction-limited tracks, 6 for rotation-limited.
        clutch_plates = int(round(params.get("diff_clutch_plates", 4)))
        # Rear power slip p95 from measured
        rear_slip_p95 = getattr(self._measured, "rear_power_slip_ratio_p95", None) if self._measured else None
        if rear_slip_p95 is None:
            # Per-car baseline: higher tyre_load_sensitivity = more slip tendency
            # BMW (0.22) -> 0.083, Porsche (0.18) -> 0.077, Ferrari (0.25) -> 0.088, Acura (0.20) -> 0.080
            _tls = self.car.tyre_load_sensitivity
            rear_slip_p95 = 0.05 + _tls * 0.15
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

    def _compute_lap_gain_breakdown(
        self, params: dict[str, float], physics: PhysicsResult,
    ) -> LapGainBreakdown:
        """Mirror _estimate_lap_gain() as explicit penalty components."""
        detail = LapGainBreakdown()

        lltd_ms_per_pct = 2.5
        lltd_penalty = physics.lltd_error * 100.0 * lltd_ms_per_pct
        detail.lltd_balance_ms += min(10.0, lltd_penalty)

        # Damping ratio targets — calibrated cars only (see _estimate_lap_gain guard)
        if getattr(self.car.damper, "zeta_is_calibrated", False):
            zeta_ls_front_err = abs(physics.zeta_ls_front - self.car.damper.zeta_target_ls_front)
            zeta_ls_rear_err = abs(physics.zeta_ls_rear - self.car.damper.zeta_target_ls_rear)
            zeta_hs_front_err = abs(physics.zeta_hs_front - self.car.damper.zeta_target_hs_front)
            zeta_hs_rear_err = abs(physics.zeta_hs_rear - self.car.damper.zeta_target_hs_rear)
            detail.damping_ms += min(4.0, zeta_ls_front_err * 5.0)
            detail.damping_ms += min(3.0, zeta_ls_rear_err * 4.0)
            detail.damping_ms += min(2.5, zeta_hs_front_err * 3.5)
            detail.damping_ms += min(2.5, zeta_hs_rear_err * 3.5)

        f_ls_comp = params.get("front_ls_comp", 7)
        f_ls_rbd = params.get("front_ls_rbd", 6)
        f_hs_comp = params.get("front_hs_comp", 5)
        f_hs_rbd = params.get("front_hs_rbd", 5)
        r_ls_comp = params.get("rear_ls_comp", 5)
        r_ls_rbd = params.get("rear_ls_rbd", 5)
        r_hs_comp = params.get("rear_hs_comp", 3)
        r_hs_rbd = params.get("rear_hs_rbd", 3)
        ls_rbd_comp_target = 0.95
        hs_rbd_comp_target = 0.60
        rbd_penalty_ms_per_unit = 4.0  # halved from 8.0 — was wrong-direction correlated

        def _rbd_penalty(rbd: float, comp: float, target: float) -> float:
            if comp < 1.0:
                return 0.0
            rbd_target = comp * target
            err = abs(rbd - rbd_target)
            return min(5.0, err * rbd_penalty_ms_per_unit / max(1.0, comp))

        detail.rebound_ratio_ms += _rbd_penalty(f_ls_rbd, f_ls_comp, ls_rbd_comp_target)
        detail.rebound_ratio_ms += _rbd_penalty(f_hs_rbd, f_hs_comp, hs_rbd_comp_target)
        detail.rebound_ratio_ms += _rbd_penalty(r_ls_rbd, r_ls_comp, ls_rbd_comp_target)
        detail.rebound_ratio_ms += _rbd_penalty(r_hs_rbd, r_hs_comp, hs_rbd_comp_target)

        detail.df_balance_ms += self._df_balance_lap_penalty_ms(physics)

        front_camber = params.get("front_camber_deg", -3.5)
        rear_camber = params.get("rear_camber_deg", -2.0)
        detail.camber_ms += self._camber_lap_penalty_ms(front_camber, rear_camber)

        diff = params.get("diff_preload_nm", 20.0)
        diff_target = 30.0  # Nm — moderate baseline for all cars
        detail.diff_preload_ms += min(8.0, abs(diff - diff_target) * 0.12)

        f_arb_size_idx = self._arb_size_index(
            params.get("front_arb_size", 0),
            self.car.arb.front_size_labels,
            self.car.arb.front_baseline_size,
            default=0,
        )
        r_arb_size_idx = self._arb_size_index(
            params.get("rear_arb_size", 1),
            self.car.arb.rear_size_labels,
            self.car.arb.rear_baseline_size,
            default=1,
        )
        f_arb_blade = int(round(params.get("front_arb_blade", 1)))
        r_arb_blade = int(round(params.get("rear_arb_blade", 2)))
        max_blade = 5
        # arb_extreme_ms zeroed out — see _estimate_lap_gain() comment (2026-03-28)
        # detail.arb_extreme_ms += ...  (kept at 0.0 — calibration shows it adds noise)

        ramp_options = self.car.garage_ranges.diff_coast_drive_ramp_options
        ramp_idx = int(round(params.get("diff_ramp_option_idx", 1)))
        ramp_idx = max(0, min(len(ramp_options) - 1, ramp_idx))
        trail_brake = getattr(self._driver, "trail_brake_depth_p95", 0.3) if self._driver else 0.3
        if trail_brake > 0.4:
            coast_target_idx = 0
        elif trail_brake < 0.2:
            coast_target_idx = 2
        else:
            coast_target_idx = 1
        coast_mismatch = abs(ramp_idx - coast_target_idx)
        detail.diff_ramp_ms += min(4.0, coast_mismatch * 2.0)  # reduced — see _estimate_lap_gain() comment

        clutch_plates = int(round(params.get("diff_clutch_plates", 4)))
        rear_slip_p95 = getattr(self._measured, "rear_power_slip_ratio_p95", None) if self._measured else None
        if rear_slip_p95 is None:
            rear_slip_p95 = 0.07
        if rear_slip_p95 > 0.10:
            plates_target = 6
        elif rear_slip_p95 < 0.05:
            plates_target = 2
        else:
            plates_target = 4
        detail.diff_clutch_ms += min(10.0, abs(clutch_plates - plates_target) * 3.0)

        tc_gain = int(round(params.get("tc_gain", 4)))
        tc_slip = int(round(params.get("tc_slip", 3)))
        tc_gain_target = getattr(self._measured, "_tc_gain_recommendation", None) if self._measured else None
        tc_slip_target = getattr(self._measured, "_tc_slip_recommendation", None) if self._measured else None
        if tc_gain_target is not None:
            detail.tc_ms += min(8.0, abs(tc_gain - tc_gain_target) * 2.0)
        if tc_slip_target is not None:
            detail.tc_ms += min(6.0, abs(tc_slip - tc_slip_target) * 2.0)
        if rear_slip_p95 > 0.10 and tc_gain < 5:
            detail.tc_ms += min(8.0, (5 - tc_gain) * 3.0)
        elif rear_slip_p95 < 0.04 and tc_gain > 6:
            detail.tc_ms += min(5.0, (tc_gain - 6) * 2.0)

        # ── Tyre carcass temperature — thermal window penalty ────────────────
        # GTP/LMDh Michelin compound optimal window: ~82-104°C (180-220°F)
        # Source: Ken Payne (Michelin NA), Sportscar365; iRacing GTP data
        CARCASS_OPTIMAL_MIN_C = 82.0
        CARCASS_OPTIMAL_MAX_C = 104.0
        CARCASS_MS_PER_DEG_COLD = 1.2   # ms penalty per °C below min (cold = low grip)
        CARCASS_MS_PER_DEG_HOT = 1.8    # ms penalty per °C above max (hot = graining)
        if self._measured is not None:
            for attr in ("front_carcass_mean_c", "rear_carcass_mean_c"):
                carcass_temp = getattr(self._measured, attr, None)
                if carcass_temp is not None and float(carcass_temp) > 20.0:
                    temp = float(carcass_temp)
                    cold_pen = max(0.0, CARCASS_OPTIMAL_MIN_C - temp) * CARCASS_MS_PER_DEG_COLD
                    hot_pen = max(0.0, temp - CARCASS_OPTIMAL_MAX_C) * CARCASS_MS_PER_DEG_HOT
                    detail.carcass_ms += min(15.0, cold_pen + hot_pen)

        return detail

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
        # Default OD: use car's first torsion option, or 0.0 for cars without torsion bars (Porsche)
        _od_default = (self.car.corner_spring.front_torsion_od_options[0]
                       if self.car.corner_spring.front_torsion_od_options else 0.0)
        _od_mm = params.get("front_torsion_od_mm", _od_default)

        # Ferrari's auto-calibrated deflection model was trained on INDEX inputs
        # (front_heave=0-8, torsion_od=0-18), not physical N/mm rates.
        # Convert N/mm → index before calling the deflection model.
        _ferrari_controls = self.car.ferrari_indexed_controls
        if _ferrari_controls is not None:
            # Convert front heave N/mm → index for deflection model
            _anchor = _hsm.front_setting_anchor_index or 1.0
            _rate_at_anchor = _hsm.front_rate_at_anchor_nmm or 50.0
            _rate_per_idx = _hsm.front_rate_per_index_nmm or 20.0
            _k_front = _anchor + (_k_front - _rate_at_anchor) / _rate_per_idx  # → heave index
            # Convert torsion OD mm → torsion bar index for deflection model
            _od_mm = float(params.get("front_torsion_bar_index", 2.0))

        _perch_front = _hsm.perch_offset_front_baseline_mm
        _dm = self.car.deflection
        _spring_defl = _dm.heave_spring_defl_static(_k_front, _perch_front, _od_mm)
        _slider_static = _dm.heave_slider_defl_static(_k_front, _perch_front, _od_mm)

        _defl_min, _defl_max = _gr.heave_spring_defl_mm   # (0.6, 25.0)
        _slider_min, _slider_max = _gr.heave_slider_defl_mm  # (25.0, 45.0)

        # Deflection vetoes only applied when the car's DeflectionModel is calibrated
        # from real measured data. BMW: calibrated from 31 sessions (R²=0.953).
        # Ferrari: auto-calibrated but indexed inputs have insufficient boundary precision.
        # Porsche/Acura/Cadillac: uncalibrated — BMW coefficients produce garbage values.
        # NEVER apply BMW-calibrated deflection model to uncalibrated cars.
        _deflection_veto_enabled = _dm.is_calibrated and _ferrari_controls is None
        if _deflection_veto_enabled:
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
        elif _ferrari_controls is not None and _dm.is_calibrated:
            # Ferrari: log as soft warning, not veto (indexed inputs have boundary precision issues)
            if _spring_defl < _defl_min or _spring_defl > _defl_max:
                soft_penalties.append(
                    f"[Ferrari] Heave defl model uncertainty: pred={_spring_defl:.2f}mm "
                    f"(legal: {_defl_min}-{_defl_max}mm) — model uses indexed inputs, verify in garage"
                )
        # else: uncalibrated deflection model — skip entirely, don't score with wrong coefficients

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
                params
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
        except Exception as e:
            logger.debug("Fuel window LLTD scoring failed: %s", e)

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

        # Heave extrapolation and realism belong in the validated operating
        # envelope, not in raw pace. This avoids double-counting platform
        # instability against _compute_platform_risk().
        uncertainty_penalty = self._heave_calibration_uncertainty_penalty_ms(front_heave)
        if uncertainty_penalty > 0.0:
            penalty.setup_distance_ms += uncertainty_penalty
            soft_penalties.append(
                f"Heave calibration extrapolation: k={front_heave:.0f} N/mm "
                f"(penalty {uncertainty_penalty:.1f}ms)"
            )

        realism_penalty = self._heave_realism_penalty_ms(front_heave)
        if realism_penalty > 0.0:
            penalty.setup_distance_ms += realism_penalty
            soft_penalties.append(
                f"Heave spring outside realistic window: k={front_heave:.0f} N/mm "
                f"(penalty {realism_penalty:.0f}ms)"
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
                breakdown = self._new_breakdown()
                veto_reasons: list[str] = []
                soft_penalties: list[str] = []
                breakdown.lap_gain_detail = self._compute_lap_gain_breakdown(params, physics)
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
                breakdown = self._new_breakdown()
                veto_reasons = []
                soft_penalties = []
                breakdown.lap_gain_detail = self._compute_lap_gain_breakdown(params, physics)
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
