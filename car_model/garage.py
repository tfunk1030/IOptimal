"""Unified garage-output model for garage-visible iRacing setup values.

This module centralizes the BMW/Sebring regressions for:
  - static ride heights
  - torsion bar turns
  - front heave spring static deflection
  - front heave slider static position

It also wraps the existing display-only deflection helpers so solver,
reporting, and writer paths consume a single source of garage truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from car_model.cars import DeflectionModel

import logging as _logging

_log = _logging.getLogger(__name__)


def _extract_or_warn(setup: Any, attr: str, default: float) -> float:
    """Extract a float attribute, warning if it falls back to default."""
    val = getattr(setup, attr, None)
    if val is None:
        _log.debug(
            "GarageSetupState: '%s' missing from setup object — using default %.1f",
            attr, default,
        )
        return default
    return float(val)


@dataclass(frozen=True)
class GarageSetupState:
    """Garage-settable inputs required to predict displayed garage outputs."""

    front_pushrod_mm: float
    rear_pushrod_mm: float
    front_heave_nmm: float
    front_heave_perch_mm: float
    rear_third_nmm: float
    rear_third_perch_mm: float
    front_torsion_od_mm: float
    rear_spring_nmm: float
    rear_spring_perch_mm: float
    front_camber_deg: float
    rear_camber_deg: float = 0.0
    fuel_l: float = 0.0
    wing_deg: float = 0.0
    front_arb_blade: float = 0.0
    rear_arb_blade: float = 0.0
    # Torsion bar preload turns (Ferrari/Acura only; 0.0 for BMW/Porsche).
    # Used as regression features in DirectRegression models fitted with
    # `torsion_turns` / `rear_torsion_turns` pool features.  Populated from
    # IBT observation data when available; defaults to 0.0 in solver-path.
    torsion_bar_turns: float = 0.0
    rear_torsion_bar_turns: float = 0.0
    # ── GT3 paired-coil + bump-rubber + splitter fields (W7.1, audit BLOCKER #4) ──
    # All 0.0 for GTP cars; populated for GT3 (SuspensionArchitecture.GT3_COIL_4WHEEL).
    # Field names match the W6.3 ``learner/observation.py`` keys so observations
    # and garage-state share schema. Per-axle (LF/RF averaged into front_*; LR/RR
    # averaged into rear_*) — analyzer/setup_reader.py averages on read.
    front_corner_spring_nmm: float = 0.0       # paired front coil rate (avg LF/RF)
    rear_corner_spring_nmm: float = 0.0        # paired rear coil rate (avg LR/RR)
    front_bump_rubber_gap_mm: float = 0.0      # avg per-axle bump rubber gap, front
    rear_bump_rubber_gap_mm: float = 0.0       # avg per-axle bump rubber gap, rear
    splitter_height_mm: float = 0.0            # CenterFrontSplitterHeight

    # ── D1 per-lap covariates (lap-condition state) ──
    # Lap-condition state, NOT setup parameters.  When a regression includes
    # tyre/fuel/aggression interaction features (Unit D1), predictions need
    # representative race values for these.  At solve time, populate from
    # the IBT's measured per-lap mean (mid-stint conditions).  When unset,
    # defaults below produce conservative predictions that exclude the
    # lap-condition contribution (≈ training-mean-equivalent for tyre_temp).
    tyre_temp_avg_c: float = 60.0       # representative steady-state tyre temp
    driver_aggression_idx: float = 0.0  # front_shock_vel_p99 baseline
    fuel_remaining_l: float = 0.0       # mid-stint fuel; defaults to 0 = full stint

    @classmethod
    def from_current_setup(cls, setup: Any, car: Any = None) -> "GarageSetupState":
        """Build from analyzer.setup_reader.CurrentSetup-like objects.

        If *car* is provided (a CarModel), indexed spring cars (Ferrari, Acura)
        get their raw garage indices decoded to physical N/mm rates.
        """
        front_heave_nmm = float(getattr(setup, "front_heave_nmm", 0.0))
        rear_third_nmm = float(getattr(setup, "rear_third_nmm", 0.0))
        rear_spring_nmm = float(getattr(setup, "rear_spring_nmm", 0.0))
        front_torsion_od_mm = float(getattr(setup, "front_torsion_od_mm", 0.0))

        # Index decoding for indexed cars (Ferrari, Acura).
        # GT3 cars have ``heave_spring=None`` (no heave/third architecture) — we
        # guard on ``hsm is not None`` so the index-decode block doesn't blow up
        # when the W7.1 GT3 path calls this function.
        if car is not None:
            hsm = car.heave_spring
            csm = car.corner_spring
            if hsm is not None:
                if (hsm.front_setting_index_range is not None
                        and front_heave_nmm <= hsm.front_setting_index_range[1] + 0.5):
                    front_heave_nmm = hsm.front_rate_from_setting(front_heave_nmm)
                if (hsm.rear_setting_index_range is not None
                        and rear_third_nmm <= hsm.rear_setting_index_range[1] + 0.5):
                    rear_third_nmm = hsm.rear_rate_from_setting(rear_third_nmm)
            if csm is not None:
                if (hasattr(csm, 'rear_setting_index_range')
                        and csm.rear_setting_index_range is not None
                        and rear_spring_nmm <= csm.rear_setting_index_range[1] + 0.5):
                    rear_spring_nmm = csm.rear_bar_rate_from_setting(rear_spring_nmm)
                if (hasattr(csm, 'front_setting_index_range')
                        and csm.front_setting_index_range is not None
                        and front_torsion_od_mm <= csm.front_setting_index_range[1] + 0.5):
                    front_torsion_od_mm = csm.front_torsion_od_from_setting(front_torsion_od_mm)

        # ── GT3 paired-coil + bump-rubber + splitter (W7.1, audit BLOCKER #4/#16) ──
        # Always populate via getattr-with-defaults so GTP observations carry
        # zeros and GT3 observations carry the real values. analyzer/setup_reader
        # stores the avg of LF/RF SpringRate into ``front_corner_spring_nmm`` and
        # the per-corner bump rubber gaps into ``{lf,rf,lr,rr}_bump_rubber_gap_mm``.
        # We average per axle here to mirror observation.py's contract.
        front_corner_spring_nmm = float(getattr(setup, "front_corner_spring_nmm", 0.0))
        # ``rear_corner_spring_nmm`` is the GT3-canonical alias of rear_spring_nmm
        # — analyzer stores the avg of LR/RR SpringRate into rear_spring_nmm for
        # GT3 (analyzer/setup_reader.py:235). Surface it under the canonical key
        # when the architecture is GT3 so DirectRegression models with a
        # ``inv_rear_corner_spring`` feature can fire.
        is_gt3 = (
            front_corner_spring_nmm > 0.0
            and not float(getattr(setup, "front_heave_nmm", 0.0))
            and not float(getattr(setup, "front_torsion_od_mm", 0.0))
        )
        rear_corner_spring_nmm = (
            rear_spring_nmm if is_gt3
            else float(getattr(setup, "rear_corner_spring_nmm", 0.0))
        )
        lf_gap = float(getattr(setup, "lf_bump_rubber_gap_mm", 0.0))
        rf_gap = float(getattr(setup, "rf_bump_rubber_gap_mm", 0.0))
        lr_gap = float(getattr(setup, "lr_bump_rubber_gap_mm", 0.0))
        rr_gap = float(getattr(setup, "rr_bump_rubber_gap_mm", 0.0))
        front_bump_rubber_gap_mm = (
            (lf_gap + rf_gap) / 2.0 if (lf_gap or rf_gap)
            else float(getattr(setup, "front_bump_rubber_gap_mm", 0.0))
        )
        rear_bump_rubber_gap_mm = (
            (lr_gap + rr_gap) / 2.0 if (lr_gap or rr_gap)
            else float(getattr(setup, "rear_bump_rubber_gap_mm", 0.0))
        )

        return cls(
            front_pushrod_mm=float(getattr(setup, "front_pushrod_mm", 0.0)),
            rear_pushrod_mm=float(getattr(setup, "rear_pushrod_mm", 0.0)),
            front_heave_nmm=front_heave_nmm,
            front_heave_perch_mm=float(getattr(setup, "front_heave_perch_mm", 0.0)),
            rear_third_nmm=rear_third_nmm,
            rear_third_perch_mm=float(getattr(setup, "rear_third_perch_mm", 0.0)),
            front_torsion_od_mm=front_torsion_od_mm,
            rear_spring_nmm=rear_spring_nmm,
            rear_spring_perch_mm=float(getattr(setup, "rear_spring_perch_mm", 0.0)),
            front_camber_deg=float(getattr(setup, "front_camber_deg", 0.0)),
            rear_camber_deg=_extract_or_warn(setup, "rear_camber_deg", 0.0),
            fuel_l=float(getattr(setup, "fuel_l", 0.0)),
            wing_deg=_extract_or_warn(setup, "wing_angle_deg", 0.0),
            front_arb_blade=float(getattr(setup, "front_arb_blade", 0) or 0),
            rear_arb_blade=float(getattr(setup, "rear_arb_blade", 0) or 0),
            torsion_bar_turns=float(getattr(setup, "torsion_bar_turns", 0.0)),
            rear_torsion_bar_turns=float(getattr(setup, "rear_torsion_bar_turns", 0.0)),
            front_corner_spring_nmm=front_corner_spring_nmm,
            rear_corner_spring_nmm=rear_corner_spring_nmm,
            front_bump_rubber_gap_mm=front_bump_rubber_gap_mm,
            rear_bump_rubber_gap_mm=rear_bump_rubber_gap_mm,
            splitter_height_mm=float(getattr(setup, "splitter_height_mm", 0.0)),
            # D1 lap-condition state — pull from IBT measurements when
            # available; field defaults (60°C / 0 / 0) handle missing values.
            tyre_temp_avg_c=float(getattr(setup, "tyre_temp_avg_c", 60.0) or 60.0),
            driver_aggression_idx=float(getattr(setup, "driver_aggression_idx", 0.0) or 0.0),
            fuel_remaining_l=float(getattr(setup, "fuel_remaining_l", 0.0) or 0.0),
        )

    @classmethod
    def from_solver_steps(
        cls,
        step1: Any,
        step2: Any,
        step3: Any,
        step5: Any | None = None,
        fuel_l: float = 0.0,
        front_camber_deg: float | None = None,
        rear_camber_deg: float | None = None,
        wing_deg: float = 0.0,
    ) -> "GarageSetupState | None":
        """Build from solver outputs.  Returns None if any required step is None."""
        if step1 is None or step2 is None or step3 is None:
            return None
        if front_camber_deg is None:
            front_camber_deg = (
                float(step5.front_camber_deg)
                if step5 is not None and hasattr(step5, "front_camber_deg")
                else 0.0
            )
        if rear_camber_deg is None:
            rear_camber_deg = (
                float(step5.rear_camber_deg)
                if step5 is not None and hasattr(step5, "rear_camber_deg")
                else 0.0
            )
        return cls(
            front_pushrod_mm=float(step1.front_pushrod_offset_mm),
            rear_pushrod_mm=float(step1.rear_pushrod_offset_mm),
            front_heave_nmm=float(step2.front_heave_nmm),
            front_heave_perch_mm=float(step2.perch_offset_front_mm),
            rear_third_nmm=float(step2.rear_third_nmm),
            rear_third_perch_mm=float(step2.perch_offset_rear_mm),
            front_torsion_od_mm=float(step3.front_torsion_od_mm),
            rear_spring_nmm=float(step3.rear_spring_rate_nmm),
            rear_spring_perch_mm=float(step3.rear_spring_perch_mm),
            front_camber_deg=float(front_camber_deg),
            rear_camber_deg=float(rear_camber_deg),
            fuel_l=float(fuel_l),
            wing_deg=float(wing_deg),
        )


@dataclass
class DirectRegression:
    """Stores a fitted regression that evaluates directly from GarageSetupState.

    Bypasses DeflectionModel's rigid coefficient interface to achieve
    sub-0.1mm accuracy when the fitted model uses features that don't
    map cleanly to DeflectionModel fields.
    """
    intercept: float = 0.0
    feature_names: tuple[str, ...] = ()
    coefficients: tuple[float, ...] = ()
    # F3 confidence tier propagated from the source FittedModel.  Used by
    # callers that want to gate behavior on calibration confidence (e.g.,
    # garage_validator.py only overrides physics-derived values when the
    # regression is high/medium tier — low-tier predictions defer to the
    # physics solve).
    confidence_tier: str = "low"

    # Map from feature name to GarageSetupState extraction
    _EXTRACTORS: dict[str, Callable] = field(default_factory=dict, repr=False)

    def predict(self, setup: "GarageSetupState") -> float:
        val = self.intercept
        for name, coeff in zip(self.feature_names, self.coefficients):
            extractor = self._EXTRACTORS.get(name)
            if extractor is not None:
                val += coeff * extractor(setup)
            else:
                import logging
                logging.getLogger(__name__).warning(
                    "DirectRegression: unknown feature '%s' (coeff=%.4f) — "
                    "dropped from prediction. Add an extractor to from_model().",
                    name, coeff,
                )
        return max(0.0, val)

    @classmethod
    def from_model(cls, model_coefficients: list[float],
                   model_feature_names: list[str],
                   confidence_tier: str = "low") -> "DirectRegression":
        """Build from a FittedModel's coefficients and feature names."""
        extractors: dict[str, Callable] = {
            "front_pushrod": lambda s: s.front_pushrod_mm,
            "rear_pushrod": lambda s: s.rear_pushrod_mm,
            "front_heave": lambda s: s.front_heave_nmm,
            "rear_third": lambda s: s.rear_third_nmm,
            "rear_spring": lambda s: s.rear_spring_nmm,
            "torsion_od": lambda s: s.front_torsion_od_mm,
            "front_heave_perch": lambda s: s.front_heave_perch_mm,
            "rear_third_perch": lambda s: s.rear_third_perch_mm,
            "rear_spring_perch": lambda s: s.rear_spring_perch_mm,
            "front_camber": lambda s: s.front_camber_deg,
            "fuel": lambda s: s.fuel_l,
            "inv_front_heave": lambda s: 1.0 / max(s.front_heave_nmm, 1.0),
            "inv_heave": lambda s: 1.0 / max(s.front_heave_nmm, 1.0),
            "inv_heave_nmm": lambda s: 1.0 / max(s.front_heave_nmm, 1.0),
            "inv_rear_third": lambda s: 1.0 / max(s.rear_third_nmm, 1.0),
            "inv_rear_spring": lambda s: 1.0 / max(s.rear_spring_nmm, 1.0),
            "inv_od4": lambda s: 1.0 / max(s.front_torsion_od_mm ** 4, 1.0),
            "od4": lambda s: s.front_torsion_od_mm ** 4,
            "rear_camber": lambda s: s.rear_camber_deg,
            "wing": lambda s: s.wing_deg,
            "front_pushrod_sq": lambda s: s.front_pushrod_mm ** 2,
            "rear_pushrod_sq": lambda s: s.rear_pushrod_mm ** 2,
            "fuel_x_inv_spring": lambda s: s.fuel_l / max(s.rear_spring_nmm, 1.0),
            "fuel_x_inv_third": lambda s: s.fuel_l / max(s.rear_third_nmm, 1.0),
            # Torsion bar preload turns (Ferrari/Acura; zero for BMW/Porsche, so the
            # feature is auto-excluded when fitting those cars — see _pool_to_matrix).
            "torsion_turns": lambda s: s.torsion_bar_turns,
            "rear_torsion_turns": lambda s: s.rear_torsion_bar_turns,
            # ── GT3 paired-coil + bump-rubber + splitter features (W7.1, audit BLOCKER #5) ──
            # Compliance form (1/k) is the primary feature for ride-height-vs-spring
            # relationships under aero load (project's "compliance physics" principle,
            # CLAUDE.md). Linear forms are also exposed for cases where the regression
            # selects k directly (e.g. dynamic excursion ∝ k for a constant force band).
            # All extractors guard against zero (GTP setups) by returning 0.0.
            "front_corner_spring": lambda s: s.front_corner_spring_nmm,
            "inv_front_corner_spring": (
                lambda s: 1.0 / s.front_corner_spring_nmm
                if s.front_corner_spring_nmm > 0 else 0.0
            ),
            "rear_corner_spring": lambda s: s.rear_corner_spring_nmm,
            "inv_rear_corner_spring": (
                lambda s: 1.0 / s.rear_corner_spring_nmm
                if s.rear_corner_spring_nmm > 0 else 0.0
            ),
            "front_bump_rubber_gap": lambda s: s.front_bump_rubber_gap_mm,
            "rear_bump_rubber_gap": lambda s: s.rear_bump_rubber_gap_mm,
            "splitter_height": lambda s: s.splitter_height_mm,
            # Fuel-coupled GT3 compliance features — analogue of the GTP
            # ``fuel_x_inv_spring`` / ``fuel_x_inv_third`` features that capture
            # the rear-axle-mass × spring-compliance interaction term.
            "fuel_x_inv_front_corner_spring": (
                lambda s: s.fuel_l / s.front_corner_spring_nmm
                if s.front_corner_spring_nmm > 0 else 0.0
            ),
            "fuel_x_inv_rear_corner_spring": (
                lambda s: s.fuel_l / s.rear_corner_spring_nmm
                if s.rear_corner_spring_nmm > 0 else 0.0
            ),
            # ── D1 per-lap covariates (lap-condition features) ──
            # Read from GarageSetupState.tyre_temp_avg_c / driver_aggression_idx
            # / fuel_remaining_l.  These default to representative race values
            # (60°C tyre, zero aggression baseline, zero remaining-fuel) so
            # predictions match training conditions when callers don't pass
            # IBT-measured values explicitly.
            "tyre_temp": lambda s: s.tyre_temp_avg_c,
            "driver_aggression": lambda s: s.driver_aggression_idx,
            "fuel_remaining": lambda s: s.fuel_remaining_l,
            "fuel_remaining_sq": lambda s: s.fuel_remaining_l ** 2,
            "tyre_temp_x_inv_spring": (
                lambda s: s.tyre_temp_avg_c / max(s.rear_spring_nmm, 1.0)
            ),
            "tyre_temp_x_inv_third": (
                lambda s: s.tyre_temp_avg_c / max(s.rear_third_nmm, 1.0)
            ),
            "tyre_temp_x_inv_front_corner_spring": (
                lambda s: s.tyre_temp_avg_c / s.front_corner_spring_nmm
                if s.front_corner_spring_nmm > 0 else 0.0
            ),
            "aggression_x_inv_spring": (
                lambda s: s.driver_aggression_idx / max(s.rear_spring_nmm, 1.0)
            ),
            "aggression_x_inv_third": (
                lambda s: s.driver_aggression_idx / max(s.rear_third_nmm, 1.0)
            ),
        }
        return cls(
            intercept=model_coefficients[0] if model_coefficients else 0.0,
            feature_names=tuple(model_feature_names),
            coefficients=tuple(model_coefficients[1:1 + len(model_feature_names)]),
            confidence_tier=confidence_tier,
            _EXTRACTORS=extractors,
        )


@dataclass(frozen=True)
class GarageOutputs:
    """Predicted garage-visible outputs from a setup state."""

    front_static_rh_mm: float
    rear_static_rh_mm: float
    torsion_bar_turns: float
    torsion_bar_defl_mm: float
    front_shock_defl_static_mm: float
    front_shock_defl_max_mm: float
    rear_shock_defl_static_mm: float
    rear_shock_defl_max_mm: float
    heave_spring_defl_static_mm: float
    heave_spring_defl_max_mm: float
    heave_slider_defl_static_mm: float
    heave_slider_defl_max_mm: float
    rear_spring_defl_static_mm: float
    rear_spring_defl_max_mm: float
    third_spring_defl_static_mm: float
    third_spring_defl_max_mm: float
    third_slider_defl_static_mm: float
    available_travel_front_mm: float
    travel_margin_front_mm: float


@dataclass(frozen=True)
class GarageConstraintResult:
    """Hard-constraint evaluation for a garage candidate."""

    valid: bool
    front_static_rh_ok: bool
    heave_slider_ok: bool
    torsion_defl_ok: bool
    travel_margin_ok: bool
    bottoming_ok: bool
    vortex_ok: bool
    available_travel_front_mm: float
    travel_margin_front_mm: float
    messages: list[str] = field(default_factory=list)


@dataclass
class GarageOutputModel:
    """Single-source garage-output model for BMW/Sebring."""

    name: str
    track_keywords: tuple[str, ...] = field(default_factory=tuple)

    # Defaults used when earlier solver stages have not produced all values yet.
    default_front_pushrod_mm: float = -25.5
    default_rear_pushrod_mm: float = -29.0
    default_front_heave_nmm: float = 50.0
    default_front_heave_perch_mm: float = -13.0
    default_rear_third_nmm: float = 530.0
    default_rear_third_perch_mm: float = 42.0
    default_front_torsion_od_mm: float = 13.9
    default_rear_spring_nmm: float = 170.0
    default_rear_spring_perch_mm: float = 30.0
    default_front_camber_deg: float = -2.9
    default_rear_camber_deg: float = -1.9
    default_front_shock_defl_max_mm: float = 100.0
    default_rear_shock_defl_max_mm: float = 150.0

    # ── GT3 paired-coil + bump-rubber + splitter defaults (W7.1, audit DEGRADED #23) ──
    # Mid-range BMW M4 GT3 EVO baseline (audit ``output.md:540-555`` driver-bracketed
    # ranges; we pick the middle of each range so a freshly-constructed GarageOutputModel
    # without per-car overrides has a meaningful baseline state for any GT3 car). Per-car
    # GarageOutputModel instances populated from real fits will override these.
    # Used only by ``default_state(car=...)`` when ``car.suspension_arch.has_heave_third``
    # is False; GTP cars never read these.
    default_front_corner_spring_nmm: float = 220.0     # BMW M4 GT3 mid (range 190-340 N/mm)
    default_rear_corner_spring_nmm: float = 180.0      # BMW M4 GT3 mid
    default_front_bump_rubber_gap_mm: float = 15.0     # BMW M4 GT3 driver-anchor
    default_rear_bump_rubber_gap_mm: float = 50.0      # BMW M4 GT3 driver-anchor
    default_splitter_height_mm: float = 20.0           # mid-range across all 3 GT3 stubs
    front_rh_floor_mm: float = 30.0
    max_slider_mm: float = 45.0
    min_static_defl_mm: float = 3.0
    max_torsion_bar_defl_mm: float | None = None
    torsion_bar_defl_safety_margin_mm: float = 0.0
    torsion_bar_rate_c: float = 0.0008036
    heave_spring_defl_max_intercept_mm: float = 106.43
    heave_spring_defl_max_slope: float = -0.310

    # Front static RH fit (linear terms)
    front_intercept: float = 0.0
    front_coeff_pushrod: float = 0.0
    front_coeff_heave_nmm: float = 0.0
    front_coeff_heave_perch_mm: float = 0.0
    front_coeff_torsion_od_mm: float = 0.0
    front_coeff_camber_deg: float = 0.0
    front_coeff_fuel_l: float = 0.0
    # Front compliance terms (physics-correct: ∝ 1/k under aero load)
    front_coeff_inv_heave_nmm: float = 0.0

    # Rear static RH fit (linear terms)
    rear_intercept: float = 0.0
    rear_coeff_pushrod: float = 0.0
    rear_coeff_third_nmm: float = 0.0
    rear_coeff_third_perch_mm: float = 0.0
    rear_coeff_rear_spring_nmm: float = 0.0
    rear_coeff_rear_spring_perch_mm: float = 0.0
    rear_coeff_front_heave_perch_mm: float = 0.0
    rear_coeff_fuel_l: float = 0.0
    # Rear compliance terms (physics-correct: ∝ 1/k under aero load)
    rear_coeff_inv_third_nmm: float = 0.0
    rear_coeff_inv_rear_spring_nmm: float = 0.0

    # Torsion turns fit
    torsion_turns_intercept: float = 0.0
    torsion_turns_coeff_heave_nmm: float = 0.0
    torsion_turns_coeff_heave_perch_mm: float = 0.0
    torsion_turns_coeff_torsion_od_mm: float = 0.0
    torsion_turns_coeff_front_rh_mm: float = 0.0

    # Front heave spring/slider display fit
    heave_defl_intercept: float = 0.0
    heave_defl_coeff_heave_nmm: float = 0.0
    heave_defl_coeff_heave_perch_mm: float = 0.0
    heave_defl_coeff_torsion_od_mm: float = 0.0
    heave_defl_coeff_front_pushrod_mm: float = 0.0
    heave_defl_coeff_front_rh_mm: float = 0.0
    heave_defl_coeff_torsion_turns: float = 0.0
    # Inverse-feature coefficients (Porsche: defl ~ 1/heave_nmm, not ~ heave_nmm)
    heave_defl_coeff_inv_heave_nmm: float = 0.0
    heave_defl_coeff_inv_od4: float = 0.0

    slider_intercept: float = 0.0
    slider_coeff_heave_nmm: float = 0.0
    slider_coeff_heave_perch_mm: float = 0.0
    slider_coeff_torsion_od_mm: float = 0.0
    slider_coeff_front_pushrod_mm: float = 0.0
    slider_coeff_front_rh_mm: float = 0.0
    slider_coeff_torsion_turns: float = 0.0

    deflection: "DeflectionModel | None" = None

    # Direct regressions bypass DeflectionModel for higher accuracy.
    # When set, these override the corresponding DeflectionModel method calls.
    _direct_front_rh: DirectRegression | None = None
    _direct_rear_rh: DirectRegression | None = None
    _direct_front_shock: DirectRegression | None = None
    _direct_heave_defl_static: DirectRegression | None = None
    _direct_heave_slider: DirectRegression | None = None
    _direct_heave_defl_max: DirectRegression | None = None
    _direct_rear_shock: DirectRegression | None = None
    _direct_torsion_defl: DirectRegression | None = None
    _direct_rear_spring_defl: DirectRegression | None = None
    _direct_rear_spring_defl_max: DirectRegression | None = None
    _direct_third_defl: DirectRegression | None = None
    _direct_third_defl_max: DirectRegression | None = None
    _direct_third_slider: DirectRegression | None = None

    def applies_to_track(self, track_name: str | None) -> bool:
        """Whether this model is the authoritative garage path for the track."""
        if not self.track_keywords:
            return True
        if not track_name:
            return False
        haystack = track_name.lower().replace("_", " ")
        return any(keyword.lower() in haystack for keyword in self.track_keywords)

    def default_state(self, fuel_l: float = 0.0, *, car: Any = None) -> GarageSetupState:
        """Baseline state used before later solver stages fill in all inputs.

        Architecture-aware (W7.1, audit DEGRADED #23): when *car* has a non-GTP
        ``suspension_arch`` (i.e. ``has_heave_third`` is False), returns a state
        with GT3 paired-coil + bump-rubber + splitter fields populated and
        heave/third/torsion fields zeroed. When *car* is None or a GTP car,
        returns the legacy GTP baseline so existing callers see no change.
        """
        if car is not None and not car.suspension_arch.has_heave_third:
            # GT3 baseline: coil rates + bump-rubber + splitter populated; the
            # heave/third/torsion fields stay 0.0 via dataclass default.
            return GarageSetupState(
                front_pushrod_mm=self.default_front_pushrod_mm,
                rear_pushrod_mm=self.default_rear_pushrod_mm,
                front_heave_nmm=0.0,
                front_heave_perch_mm=0.0,
                rear_third_nmm=0.0,
                rear_third_perch_mm=0.0,
                front_torsion_od_mm=0.0,
                rear_spring_nmm=0.0,
                rear_spring_perch_mm=0.0,
                front_camber_deg=self.default_front_camber_deg,
                rear_camber_deg=self.default_rear_camber_deg,
                fuel_l=fuel_l,
                front_corner_spring_nmm=self.default_front_corner_spring_nmm,
                rear_corner_spring_nmm=self.default_rear_corner_spring_nmm,
                front_bump_rubber_gap_mm=self.default_front_bump_rubber_gap_mm,
                rear_bump_rubber_gap_mm=self.default_rear_bump_rubber_gap_mm,
                splitter_height_mm=self.default_splitter_height_mm,
            )
        return GarageSetupState(
            front_pushrod_mm=self.default_front_pushrod_mm,
            rear_pushrod_mm=self.default_rear_pushrod_mm,
            front_heave_nmm=self.default_front_heave_nmm,
            front_heave_perch_mm=self.default_front_heave_perch_mm,
            rear_third_nmm=self.default_rear_third_nmm,
            rear_third_perch_mm=self.default_rear_third_perch_mm,
            front_torsion_od_mm=self.default_front_torsion_od_mm,
            rear_spring_nmm=self.default_rear_spring_nmm,
            rear_spring_perch_mm=self.default_rear_spring_perch_mm,
            front_camber_deg=self.default_front_camber_deg,
            rear_camber_deg=self.default_rear_camber_deg,
            fuel_l=fuel_l,
        )

    @staticmethod
    def _bisect_pushrod(
        model: DirectRegression,
        template: GarageSetupState,
        target: float,
        pushrod_field: str,
        *,
        lo: float = -60.0,
        hi: float = 60.0,
        tol: float = 0.01,
        max_iter: int = 60,
        fallback: float = 0.0,
    ) -> float:
        """Numerical search for the pushrod value that achieves *target* RH.

        Uses a sample-then-bisect strategy that handles:
        - Monotone models (linear pushrod): standard bisection
        - Non-monotone models (pushrod_sq only): samples 5 points, picks the
          interval containing the target, then bisects within it
        - Constant models (zero features): returns fallback immediately
        """
        def _predict_at(pushrod_val: float) -> float:
            state = replace(template, **{pushrod_field: pushrod_val})
            return model.predict(state)

        # Sample the function at several points to handle non-monotone models
        # (e.g., quadratic pushrod_sq where f(-60) == f(60) by symmetry).
        n_samples = 7
        sample_pts = [lo + (hi - lo) * i / (n_samples - 1) for i in range(n_samples)]
        sample_vals = [_predict_at(p) for p in sample_pts]

        # Constant model guard — if all samples give the same value, pushrod
        # has no effect (e.g. BMW zero-feature constant model).
        val_range = max(sample_vals) - min(sample_vals)
        if val_range < tol:
            _log.debug(
                "_bisect_pushrod: model insensitive to %s "
                "(range=%.4f < tol=%.3f) — returning fallback=%.1f",
                pushrod_field, val_range, tol, fallback,
            )
            return fallback

        # Find adjacent sample pair that brackets the target.
        # For quadratic models, multiple intervals may bracket it — pick the
        # one closest to the fallback (expected operating region).
        best_pair = None
        best_dist = float("inf")
        for i in range(len(sample_pts) - 1):
            a, b = sample_vals[i], sample_vals[i + 1]
            if (a - target) * (b - target) <= 0:
                mid_pt = (sample_pts[i] + sample_pts[i + 1]) / 2.0
                dist = abs(mid_pt - fallback)
                if dist < best_dist:
                    best_dist = dist
                    best_pair = (i, i + 1)

        if best_pair is None:
            # Target unreachable — return sample point closest to target
            closest_idx = min(range(len(sample_vals)),
                              key=lambda i: abs(sample_vals[i] - target))
            _log.debug(
                "_bisect_pushrod: target=%.2f unreachable in [%.1f, %.1f] "
                "(range [%.2f, %.2f]) — returning closest=%.1f",
                target, lo, hi, min(sample_vals), max(sample_vals),
                sample_pts[closest_idx],
            )
            return sample_pts[closest_idx]

        # Bisect within the bracketing interval
        lo_b = sample_pts[best_pair[0]]
        hi_b = sample_pts[best_pair[1]]
        y_lo_b = sample_vals[best_pair[0]]

        for _ in range(max_iter):
            mid = (lo_b + hi_b) / 2.0
            y_mid = _predict_at(mid)
            if abs(y_mid - target) < tol:
                return mid
            if (y_lo_b - target) * (y_mid - target) <= 0:
                hi_b = mid
            else:
                lo_b = mid
                y_lo_b = y_mid
        return (lo_b + hi_b) / 2.0

    def predict_front_static_rh_raw(self, setup: GarageSetupState) -> float:
        """Predict unclamped front static ride height from the garage state.

        Uses DirectRegression when available (same path as predict_front_static_rh)
        so that solver, validator, and inverse all share the same model.
        """
        if self._direct_front_rh is not None:
            return self._direct_front_rh.predict(setup)
        inv_heave = 0.0
        if abs(self.front_coeff_inv_heave_nmm) > 1e-9 and setup.front_heave_nmm > 0:
            inv_heave = 1.0 / setup.front_heave_nmm
        return (
            self.front_intercept
            + self.front_coeff_pushrod * setup.front_pushrod_mm
            + self.front_coeff_heave_nmm * setup.front_heave_nmm
            + self.front_coeff_inv_heave_nmm * inv_heave
            + self.front_coeff_heave_perch_mm * setup.front_heave_perch_mm
            + self.front_coeff_torsion_od_mm * setup.front_torsion_od_mm
            + self.front_coeff_camber_deg * setup.front_camber_deg
            + self.front_coeff_fuel_l * setup.fuel_l
        )

    def predict_front_static_rh(self, setup: GarageSetupState) -> float:
        """Predict front static ride height.

        Delegates to the DirectRegression when available, otherwise falls back
        to the legacy linear coefficient model.  Returns the raw prediction
        without floor clamping so that downstream code can detect when a setup
        combination would produce a sub-legal ride height.  Callers that need
        the floor-clamped value should use
        ``max(model.front_rh_floor_mm, prediction)`` explicitly.
        """
        if self._direct_front_rh is not None:
            return self._direct_front_rh.predict(setup)
        return self.predict_front_static_rh_raw(setup)

    def predict_rear_static_rh(self, setup: GarageSetupState) -> float:
        """Predict rear static ride height.

        Delegates to the DirectRegression when available (Porsche/Algarve path),
        otherwise falls back to the legacy linear coefficient model.
        """
        if self._direct_rear_rh is not None:
            return self._direct_rear_rh.predict(setup)
        inv_third = 0.0
        if abs(self.rear_coeff_inv_third_nmm) > 1e-9 and setup.rear_third_nmm > 0:
            inv_third = 1.0 / setup.rear_third_nmm
        inv_rspring = 0.0
        if abs(self.rear_coeff_inv_rear_spring_nmm) > 1e-9 and setup.rear_spring_nmm > 0:
            inv_rspring = 1.0 / setup.rear_spring_nmm
        return (
            self.rear_intercept
            + self.rear_coeff_pushrod * setup.rear_pushrod_mm
            + self.rear_coeff_third_nmm * setup.rear_third_nmm
            + self.rear_coeff_inv_third_nmm * inv_third
            + self.rear_coeff_third_perch_mm * setup.rear_third_perch_mm
            + self.rear_coeff_rear_spring_nmm * setup.rear_spring_nmm
            + self.rear_coeff_inv_rear_spring_nmm * inv_rspring
            + self.rear_coeff_rear_spring_perch_mm * setup.rear_spring_perch_mm
            + self.rear_coeff_front_heave_perch_mm * setup.front_heave_perch_mm
            + self.rear_coeff_fuel_l * setup.fuel_l
        )

    def predict_torsion_turns(
        self,
        setup: GarageSetupState,
        front_static_rh_mm: float | None = None,
    ) -> float:
        """Predict front torsion bar turns."""
        if front_static_rh_mm is None:
            front_static_rh_mm = self.predict_front_static_rh(setup)
        turns = (
            self.torsion_turns_intercept
            + self.torsion_turns_coeff_heave_nmm * setup.front_heave_nmm
            + self.torsion_turns_coeff_heave_perch_mm * setup.front_heave_perch_mm
            + self.torsion_turns_coeff_torsion_od_mm * setup.front_torsion_od_mm
            + self.torsion_turns_coeff_front_rh_mm * front_static_rh_mm
        )
        return max(0.0, turns)

    def predict_heave_spring_defl_static(
        self,
        setup: GarageSetupState,
        torsion_turns: float | None = None,
        front_static_rh_mm: float | None = None,
    ) -> float:
        """Predict front heave spring static compression."""
        if torsion_turns is None:
            torsion_turns = self.predict_torsion_turns(setup)
        if front_static_rh_mm is None:
            front_static_rh_mm = self.predict_front_static_rh(setup)
        return max(
            0.0,
            self.heave_defl_intercept
            + self.heave_defl_coeff_heave_nmm * setup.front_heave_nmm
            + self.heave_defl_coeff_heave_perch_mm * setup.front_heave_perch_mm
            + self.heave_defl_coeff_torsion_od_mm * setup.front_torsion_od_mm
            + self.heave_defl_coeff_front_pushrod_mm * setup.front_pushrod_mm
            + self.heave_defl_coeff_front_rh_mm * front_static_rh_mm
            + self.heave_defl_coeff_torsion_turns * torsion_turns
            + self.heave_defl_coeff_inv_heave_nmm / max(setup.front_heave_nmm, 1.0)
            + self.heave_defl_coeff_inv_od4 / max(setup.front_torsion_od_mm ** 4, 1.0),
        )

    def predict_heave_slider_defl_static(
        self,
        setup: GarageSetupState,
        torsion_turns: float | None = None,
        front_static_rh_mm: float | None = None,
    ) -> float:
        """Predict front heave slider static position."""
        if torsion_turns is None:
            torsion_turns = self.predict_torsion_turns(setup)
        if front_static_rh_mm is None:
            front_static_rh_mm = self.predict_front_static_rh(setup)
        return max(
            0.0,
            self.slider_intercept
            + self.slider_coeff_heave_nmm * setup.front_heave_nmm
            + self.slider_coeff_heave_perch_mm * setup.front_heave_perch_mm
            + self.slider_coeff_torsion_od_mm * setup.front_torsion_od_mm
            + self.slider_coeff_front_pushrod_mm * setup.front_pushrod_mm
            + self.slider_coeff_front_rh_mm * front_static_rh_mm
            + self.slider_coeff_torsion_turns * torsion_turns,
        )

    def front_pushrod_for_static_rh(
        self,
        target_rh_mm: float,
        *,
        front_heave_nmm: float,
        front_heave_perch_mm: float,
        front_torsion_od_mm: float,
        front_camber_deg: float,
        fuel_l: float = 0.0,
        rear_pushrod_mm: float | None = None,
        rear_third_nmm: float | None = None,
        rear_third_perch_mm: float | None = None,
        rear_spring_nmm: float | None = None,
        rear_spring_perch_mm: float | None = None,
        rear_camber_deg: float | None = None,
        wing_deg: float | None = None,
    ) -> float:
        """Invert the front RH regression to a pushrod offset.

        When a DirectRegression is available (e.g. Porsche with 12 features
        including rear-axis terms), uses bisection search so that ALL features
        participate in the inversion — not just the 6 that map to linear
        coefficient slots.  Falls back to analytic linear inversion otherwise.
        """
        target = max(target_rh_mm, self.front_rh_floor_mm)

        # --- DirectRegression path: bisection search ---
        # Only use bisection when the DirectRegression actually depends on pushrod.
        # BMW's constant model (zero features, R²=1.0) would produce garbage.
        _dr_has_pushrod = (
            self._direct_front_rh is not None
            and any("front_pushrod" in f for f in self._direct_front_rh.feature_names)
        )
        if _dr_has_pushrod:
            template = GarageSetupState(
                front_pushrod_mm=0.0,  # will be varied
                rear_pushrod_mm=rear_pushrod_mm if rear_pushrod_mm is not None else self.default_rear_pushrod_mm,
                front_heave_nmm=front_heave_nmm,
                front_heave_perch_mm=front_heave_perch_mm,
                rear_third_nmm=rear_third_nmm if rear_third_nmm is not None else self.default_rear_third_nmm,
                rear_third_perch_mm=rear_third_perch_mm if rear_third_perch_mm is not None else self.default_rear_third_perch_mm,
                front_torsion_od_mm=front_torsion_od_mm,
                rear_spring_nmm=rear_spring_nmm if rear_spring_nmm is not None else self.default_rear_spring_nmm,
                rear_spring_perch_mm=rear_spring_perch_mm if rear_spring_perch_mm is not None else self.default_rear_spring_perch_mm,
                front_camber_deg=front_camber_deg,
                rear_camber_deg=rear_camber_deg if rear_camber_deg is not None else self.default_rear_camber_deg,
                fuel_l=fuel_l,
                wing_deg=wing_deg if wing_deg is not None else 0.0,
            )
            # Pre-populate torsion turns so the DirectRegression receives
            # correct feature values (Ferrari: coeff=159.47 on torsion_turns).
            template = self._ensure_torsion_turns_populated(template)
            return self._bisect_pushrod(
                self._direct_front_rh, template, target, "front_pushrod_mm",
                fallback=self.default_front_pushrod_mm,
            )

        # --- Legacy linear coefficient path ---
        if abs(self.front_coeff_pushrod) < 1e-9:
            return self.default_front_pushrod_mm
        inv_heave = 1.0 / front_heave_nmm if (abs(self.front_coeff_inv_heave_nmm) > 1e-9 and front_heave_nmm > 0) else 0.0
        other = (
            self.front_intercept
            + self.front_coeff_heave_nmm * front_heave_nmm
            + self.front_coeff_inv_heave_nmm * inv_heave
            + self.front_coeff_heave_perch_mm * front_heave_perch_mm
            + self.front_coeff_torsion_od_mm * front_torsion_od_mm
            + self.front_coeff_camber_deg * front_camber_deg
            + self.front_coeff_fuel_l * fuel_l
        )
        return (target - other) / self.front_coeff_pushrod

    def rear_pushrod_for_static_rh(
        self,
        target_rh_mm: float,
        *,
        rear_third_nmm: float,
        rear_third_perch_mm: float,
        rear_spring_nmm: float,
        rear_spring_perch_mm: float,
        front_heave_perch_mm: float,
        fuel_l: float = 0.0,
        front_pushrod_mm: float | None = None,
        front_heave_nmm: float | None = None,
        front_torsion_od_mm: float | None = None,
        front_camber_deg: float | None = None,
        rear_camber_deg: float | None = None,
        wing_deg: float | None = None,
    ) -> float:
        """Invert the rear RH regression to a pushrod offset.

        When a DirectRegression is available, uses bisection search so that ALL
        features participate — not just the subset that maps to linear coeff slots.
        """
        # --- DirectRegression path: bisection search ---
        # Only use bisection when the DirectRegression actually depends on rear pushrod.
        _dr_has_pushrod = (
            self._direct_rear_rh is not None
            and any("rear_pushrod" in f for f in self._direct_rear_rh.feature_names)
        )
        # FIXED 2026-04-28: Even when the model includes rear_pushrod, check if
        # the coefficient is strong enough to use pushrod as an RH control lever.
        # Cadillac has rear_pushrod coeff=0.089 — changing pushrod by 40mm only
        # moves RH by 3.6mm. The solver then requests absurd pushrod values
        # (-40 to -56mm) for small RH targets. If the pushrod effect is < 0.2
        # mm RH per mm pushrod, fall back to the car's default pushrod value.
        if _dr_has_pushrod and self._direct_rear_rh is not None:
            _pushrod_coeff = 0.0
            for i, fname in enumerate(self._direct_rear_rh.feature_names):
                if fname == "rear_pushrod":
                    _pushrod_coeff = abs(self._direct_rear_rh.coefficients[i])
                    break
            if _pushrod_coeff < 0.2:
                _log.info(
                    "Rear pushrod coefficient %.3f too weak for RH control "
                    "(threshold 0.2). Returning default pushrod %.1f",
                    _pushrod_coeff, self.default_rear_pushrod_mm,
                )
                return self.default_rear_pushrod_mm
        if _dr_has_pushrod:
            template = GarageSetupState(
                front_pushrod_mm=front_pushrod_mm if front_pushrod_mm is not None else self.default_front_pushrod_mm,
                rear_pushrod_mm=0.0,  # will be varied
                front_heave_nmm=front_heave_nmm if front_heave_nmm is not None else self.default_front_heave_nmm,
                front_heave_perch_mm=front_heave_perch_mm,
                rear_third_nmm=rear_third_nmm,
                rear_third_perch_mm=rear_third_perch_mm,
                front_torsion_od_mm=front_torsion_od_mm if front_torsion_od_mm is not None else self.default_front_torsion_od_mm,
                rear_spring_nmm=rear_spring_nmm,
                rear_spring_perch_mm=rear_spring_perch_mm,
                front_camber_deg=front_camber_deg if front_camber_deg is not None else self.default_front_camber_deg,
                rear_camber_deg=rear_camber_deg if rear_camber_deg is not None else self.default_rear_camber_deg,
                fuel_l=fuel_l,
                wing_deg=wing_deg if wing_deg is not None else 0.0,
            )
            # Pre-populate torsion turns so the DirectRegression receives
            # correct feature values (Ferrari: coeff=217.20 on rear_torsion_turns).
            template = self._ensure_torsion_turns_populated(template)
            return self._bisect_pushrod(
                self._direct_rear_rh, template, target_rh_mm, "rear_pushrod_mm",
                fallback=self.default_rear_pushrod_mm,
            )

        # --- Legacy linear coefficient path ---
        if abs(self.rear_coeff_pushrod) < 1e-9:
            return self.default_rear_pushrod_mm
        inv_third = 1.0 / rear_third_nmm if (abs(self.rear_coeff_inv_third_nmm) > 1e-9 and rear_third_nmm > 0) else 0.0
        inv_rspring = 1.0 / rear_spring_nmm if (abs(self.rear_coeff_inv_rear_spring_nmm) > 1e-9 and rear_spring_nmm > 0) else 0.0
        other = (
            self.rear_intercept
            + self.rear_coeff_third_nmm * rear_third_nmm
            + self.rear_coeff_inv_third_nmm * inv_third
            + self.rear_coeff_third_perch_mm * rear_third_perch_mm
            + self.rear_coeff_rear_spring_nmm * rear_spring_nmm
            + self.rear_coeff_inv_rear_spring_nmm * inv_rspring
            + self.rear_coeff_rear_spring_perch_mm * rear_spring_perch_mm
            + self.rear_coeff_front_heave_perch_mm * front_heave_perch_mm
            + self.rear_coeff_fuel_l * fuel_l
        )
        return (target_rh_mm - other) / self.rear_coeff_pushrod

    def _ensure_torsion_turns_populated(
        self, setup: GarageSetupState,
    ) -> GarageSetupState:
        """Pre-populate torsion_bar_turns and rear_torsion_bar_turns if zero.

        DirectRegression models (especially Ferrari) can have massive coefficients
        for ``torsion_turns`` (159.47) and ``rear_torsion_turns`` (217.20).
        When the solver path leaves these at 0.0, the models under-predict RH
        by 15–25mm, causing catastrophic pushrod extrapolation (+40mm) and
        front/rear RH swaps in the .sto output.

        Front turns: use ``predict_torsion_turns()`` (constant model for Ferrari: 0.096).
        Rear turns: estimate from front turns × 0.5 (empirical ratio from Ferrari
        IBT data: front ≈ 0.096, rear ≈ 0.048).
        """
        changed = {}
        if setup.torsion_bar_turns == 0.0:
            # Use a nominal front_rh (30mm) — for Ferrari the torsion turns model
            # is constant (no front_rh dependency), so the value doesn't matter.
            _front_turns = self.predict_torsion_turns(setup, front_static_rh_mm=30.0)
            if _front_turns != 0.0:
                changed["torsion_bar_turns"] = _front_turns
        if setup.rear_torsion_bar_turns == 0.0:
            _front_turns = changed.get(
                "torsion_bar_turns", setup.torsion_bar_turns
            )
            if _front_turns > 0.0:
                # Rear turns are typically ~50% of front turns (Ferrari IBT data:
                # front ≈ 0.096, rear ≈ 0.048 from setup_writer rear formula).
                changed["rear_torsion_bar_turns"] = _front_turns * 0.5
        if changed:
            setup = replace(setup, **changed)
        return setup

    def predict(
        self,
        setup: GarageSetupState,
        front_excursion_p99_mm: float = 0.0,
    ) -> GarageOutputs:
        """Predict the unified set of garage outputs for a setup."""
        # Pre-populate torsion turns BEFORE front/rear RH predictions.
        # DirectRegression models can include torsion_turns / rear_torsion_turns
        # as features with large coefficients (Ferrari: 159.47 / 217.20).
        # Without this, the solver path (torsion_bar_turns=0.0) under-predicts
        # RH by 15–25mm → pushrod extrapolation → front/rear RH swap in .sto.
        setup = self._ensure_torsion_turns_populated(setup)
        if self._direct_front_rh:
            front_static_rh = self._direct_front_rh.predict(setup)
        else:
            front_static_rh = self.predict_front_static_rh(setup)
        if self._direct_rear_rh:
            rear_static_rh = self._direct_rear_rh.predict(setup)
        else:
            rear_static_rh = self.predict_rear_static_rh(setup)
        torsion_turns = self.predict_torsion_turns(setup, front_static_rh)
        # Augment the setup state with the refined torsion bar turns so
        # that downstream DirectRegression models (heave_defl, slider, etc.)
        # receive the correct value.
        if setup.torsion_bar_turns == 0.0 and torsion_turns != 0.0:
            setup = replace(setup, torsion_bar_turns=torsion_turns)
        # Use direct regressions when available (higher accuracy), fall back
        # to coefficient-based formulas otherwise.
        if self._direct_heave_defl_static:
            heave_defl_static = self._direct_heave_defl_static.predict(setup)
        else:
            heave_defl_static = self.predict_heave_spring_defl_static(
                setup, torsion_turns, front_static_rh)
        if self._direct_heave_slider:
            heave_slider_static = self._direct_heave_slider.predict(setup)
        else:
            heave_slider_static = self.predict_heave_slider_defl_static(
                setup, torsion_turns, front_static_rh)
        if self._direct_heave_defl_max:
            heave_defl_max = self._direct_heave_defl_max.predict(setup)
        else:
            heave_defl_max = (
                self.heave_spring_defl_max_intercept_mm
                + self.heave_spring_defl_max_slope * setup.front_heave_nmm
            )
        available_travel = max(0.0, heave_defl_max - heave_defl_static)
        travel_margin = available_travel - front_excursion_p99_mm

        front_shock_defl_static = 0.0
        rear_shock_defl_static = 0.0
        torsion_bar_defl = 0.0
        rear_spring_defl_static = 0.0
        rear_spring_defl_max = 0.0
        third_spring_defl_static = 0.0
        third_spring_defl_max = 0.0
        third_slider_defl_static = 0.0
        if self.deflection is not None or any([
            self._direct_rear_shock, self._direct_torsion_defl,
            self._direct_rear_spring_defl, self._direct_third_defl,
        ]):
            if self._direct_rear_shock:
                rear_shock_defl_static = self._direct_rear_shock.predict(setup)
            elif self.deflection:
                rear_shock_defl_static = self.deflection.shock_defl_rear(
                    setup.rear_pushrod_mm,
                    third_rate_nmm=setup.rear_third_nmm,
                    spring_rate_nmm=setup.rear_spring_nmm,
                    third_perch_mm=setup.rear_third_perch_mm,
                    spring_perch_mm=setup.rear_spring_perch_mm,
                )
            if self._direct_front_shock:
                front_shock_defl_static = self._direct_front_shock.predict(setup)
            elif self.deflection:
                front_shock_defl_static = self.deflection.shock_defl_front(
                    setup.front_pushrod_mm,
                    heave_perch_mm=setup.front_heave_perch_mm,
                    torsion_od_mm=setup.front_torsion_od_mm,
                    heave_nmm=setup.front_heave_nmm,
                )
            if self._direct_torsion_defl:
                torsion_bar_defl = self._direct_torsion_defl.predict(setup)
            elif self.deflection:
                torsion_bar_rate = self.torsion_bar_rate_c * setup.front_torsion_od_mm ** 4
                torsion_bar_defl = self.deflection.torsion_bar_defl(
                    setup.front_heave_nmm, setup.front_heave_perch_mm, torsion_bar_rate)
            if self._direct_rear_spring_defl:
                rear_spring_defl_static = self._direct_rear_spring_defl.predict(setup)
            elif self.deflection:
                rear_spring_defl_static = self.deflection.rear_spring_defl_static(
                    setup.rear_spring_nmm, setup.rear_spring_perch_mm,
                    third_rate_nmm=setup.rear_third_nmm,
                    third_perch_mm=setup.rear_third_perch_mm,
                    pushrod_mm=setup.rear_pushrod_mm,
                )
            if self._direct_rear_spring_defl_max:
                rear_spring_defl_max = self._direct_rear_spring_defl_max.predict(setup)
            elif self.deflection:
                rear_spring_defl_max = self.deflection.rear_spring_defl_max(
                    setup.rear_spring_nmm, setup.rear_spring_perch_mm)
            if self._direct_third_defl:
                third_spring_defl_static = self._direct_third_defl.predict(setup)
            elif self.deflection:
                third_spring_defl_static = self.deflection.third_spring_defl_static(
                    setup.rear_third_nmm, setup.rear_third_perch_mm,
                    spring_rate_nmm=setup.rear_spring_nmm,
                    spring_perch_mm=setup.rear_spring_perch_mm,
                    pushrod_mm=setup.rear_pushrod_mm,
                )
            if self._direct_third_defl_max:
                third_spring_defl_max = self._direct_third_defl_max.predict(setup)
            elif self.deflection:
                third_spring_defl_max = self.deflection.third_spring_defl_max(
                    setup.rear_third_nmm, setup.rear_third_perch_mm)
            if self._direct_third_slider:
                third_slider_defl_static = self._direct_third_slider.predict(setup)
            elif self.deflection:
                third_slider_defl_static = self.deflection.third_slider_defl_static(
                    third_spring_defl_static)

        return GarageOutputs(
            front_static_rh_mm=round(front_static_rh, 3),
            rear_static_rh_mm=round(rear_static_rh, 3),
            torsion_bar_turns=round(torsion_turns, 4),
            torsion_bar_defl_mm=round(torsion_bar_defl, 3),
            front_shock_defl_static_mm=round(front_shock_defl_static, 3),
            front_shock_defl_max_mm=float(self.default_front_shock_defl_max_mm),
            rear_shock_defl_static_mm=round(rear_shock_defl_static, 3),
            rear_shock_defl_max_mm=float(self.default_rear_shock_defl_max_mm),
            heave_spring_defl_static_mm=round(heave_defl_static, 3),
            heave_spring_defl_max_mm=round(heave_defl_max, 3),
            heave_slider_defl_static_mm=round(heave_slider_static, 3),
            heave_slider_defl_max_mm=float(self.max_slider_mm),
            rear_spring_defl_static_mm=round(rear_spring_defl_static, 3),
            rear_spring_defl_max_mm=round(rear_spring_defl_max, 3),
            third_spring_defl_static_mm=round(third_spring_defl_static, 3),
            third_spring_defl_max_mm=round(third_spring_defl_max, 3),
            third_slider_defl_static_mm=round(third_slider_defl_static, 3),
            available_travel_front_mm=round(available_travel, 3),
            travel_margin_front_mm=round(travel_margin, 3),
        )

    def effective_torsion_bar_defl_limit_mm(self) -> float | None:
        """Operational torsion-bar limit after applying a safety buffer."""
        if self.max_torsion_bar_defl_mm is None:
            return None
        return max(
            0.0,
            float(self.max_torsion_bar_defl_mm) - max(0.0, float(self.torsion_bar_defl_safety_margin_mm)),
        )

    def validate(
        self,
        setup: GarageSetupState,
        *,
        front_excursion_p99_mm: float = 0.0,
        min_travel_margin_mm: float = 0.0,
        front_bottoming_margin_mm: float | None = None,
        vortex_burst_margin_mm: float | None = None,
    ) -> GarageConstraintResult:
        """Validate hard garage and platform constraints for a candidate."""
        outputs = self.predict(setup, front_excursion_p99_mm=front_excursion_p99_mm)
        # Use the SAME front RH source as predict() for the floor check.
        # When _direct_front_rh is present, predict() uses it, so validate()
        # must too — otherwise legality can disagree with reported outputs.
        if self._direct_front_rh:
            front_static_raw = self._direct_front_rh.predict(setup)
        else:
            front_static_raw = self.predict_front_static_rh_raw(setup)
        front_static_ok = front_static_raw >= self.front_rh_floor_mm - 1e-6
        slider_ok = outputs.heave_slider_defl_static_mm <= self.max_slider_mm + 1e-6
        torsion_limit = self.effective_torsion_bar_defl_limit_mm()
        torsion_defl_ok = (
            True if torsion_limit is None
            else outputs.torsion_bar_defl_mm <= torsion_limit + 1e-6
        )
        travel_ok = outputs.travel_margin_front_mm >= min_travel_margin_mm - 1e-6
        bottoming_ok = (
            True if front_bottoming_margin_mm is None
            else front_bottoming_margin_mm >= -1e-6
        )
        vortex_ok = (
            True if vortex_burst_margin_mm is None
            else vortex_burst_margin_mm >= -1e-6
        )
        messages: list[str] = []
        if not front_static_ok:
            messages.append(
                f"front static RH {front_static_raw:.2f}mm < floor {self.front_rh_floor_mm:.1f}mm"
            )
        if not slider_ok:
            messages.append(
                f"heave slider {outputs.heave_slider_defl_static_mm:.2f}mm > limit {self.max_slider_mm:.1f}mm"
            )
        if not torsion_defl_ok:
            if (
                self.max_torsion_bar_defl_mm is not None
                and torsion_limit is not None
                and torsion_limit < self.max_torsion_bar_defl_mm - 1e-6
            ):
                messages.append(
                    f"torsion bar defl {outputs.torsion_bar_defl_mm:.2f}mm > operating limit "
                    f"{torsion_limit:.1f}mm (hard limit {self.max_torsion_bar_defl_mm:.1f}mm)"
                )
            else:
                messages.append(
                    f"torsion bar defl {outputs.torsion_bar_defl_mm:.2f}mm > limit {torsion_limit:.1f}mm"
                )
        if not travel_ok:
            messages.append(
                f"front travel margin {outputs.travel_margin_front_mm:.2f}mm < {min_travel_margin_mm:.1f}mm"
            )
        if not bottoming_ok and front_bottoming_margin_mm is not None:
            messages.append(f"front bottoming margin {front_bottoming_margin_mm:.2f}mm < 0")
        if not vortex_ok and vortex_burst_margin_mm is not None:
            messages.append(f"front vortex margin {vortex_burst_margin_mm:.2f}mm < 0")
        valid = front_static_ok and slider_ok and torsion_defl_ok and travel_ok and bottoming_ok and vortex_ok
        return GarageConstraintResult(
            valid=valid,
            front_static_rh_ok=front_static_ok,
            heave_slider_ok=slider_ok,
            torsion_defl_ok=torsion_defl_ok,
            travel_margin_ok=travel_ok,
            bottoming_ok=bottoming_ok,
            vortex_ok=vortex_ok,
            available_travel_front_mm=outputs.available_travel_front_mm,
            travel_margin_front_mm=outputs.travel_margin_front_mm,
            messages=messages,
        )
