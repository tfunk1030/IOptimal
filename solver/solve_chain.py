from __future__ import annotations

import copy
import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

from car_model.setup_registry import (
    diff_ramp_option_index,
    diff_ramp_pair_for_option,
    diff_ramp_string_for_option,
    internal_solver_value,
    public_output_value,
    snap_supporting_field_value,
)
from solver.arb_solver import ARBSolver
from solver.corner_spring_solver import CornerSpringSolver
from solver.damper_solver import CornerDamperSettings, DamperSolver
from solver.decision_trace import build_parameter_decisions
from solver.full_setup_optimizer import optimize_if_supported
from solver.heave_solver import HeaveSolution, HeaveSolver
from solver.legality_engine import LegalValidation, validate_solution_legality
from solver.modifiers import SolverModifiers
from solver.params_util import solver_steps_to_params
from solver.predictor import PredictedTelemetry, PredictionConfidence, predict_candidate_telemetry
from solver.rake_solver import RakeSolver, reconcile_ride_heights
from solver.setup_fingerprint import CandidateVeto, fingerprint_from_solver_steps, match_failed_cluster
from solver.supporting_solver import SupportingSolver
from solver.wheel_geometry_solver import WheelGeometrySolver


@dataclass
class SolveChainInputs:
    car: Any
    surface: Any
    track: Any
    measured: Any
    driver: Any
    diagnosis: Any
    current_setup: Any
    target_balance: float
    fuel_load_l: float
    wing_angle: float
    modifiers: SolverModifiers = field(default_factory=SolverModifiers)
    prediction_corrections: dict[str, float] = field(default_factory=dict)
    balance_tolerance: float = 0.1
    pin_front_min: bool = True
    scenario_profile: str = "single_lap_safe"
    legacy_solver: bool = False
    camber_confidence: str = "estimated"
    failed_validation_clusters: list[Any] | None = None
    supporting_driver: Any | None = None
    supporting_measured: Any | None = None
    supporting_diagnosis: Any | None = None
    corners: list[Any] | None = None
    optimization_mode: str = "driver"  # "driver" or "physics"

    def resolved_supporting_driver(self) -> Any:
        return self.supporting_driver if self.supporting_driver is not None else self.driver

    def resolved_supporting_measured(self) -> Any:
        return self.supporting_measured if self.supporting_measured is not None else self.measured

    def resolved_supporting_diagnosis(self) -> Any:
        return self.supporting_diagnosis if self.supporting_diagnosis is not None else self.diagnosis


@dataclass
class SolveChainOverrides:
    step1: dict[str, Any] = field(default_factory=dict)
    step2: dict[str, Any] = field(default_factory=dict)
    step3: dict[str, Any] = field(default_factory=dict)
    step4: dict[str, Any] = field(default_factory=dict)
    step5: dict[str, Any] = field(default_factory=dict)
    step6: dict[str, dict[str, Any]] = field(default_factory=dict)
    supporting: dict[str, Any] = field(default_factory=dict)

    def earliest_step(self) -> int | None:
        for idx, mapping in enumerate(
            (self.step1, self.step2, self.step3, self.step4, self.step5, self.step6, self.supporting),
            start=1,
        ):
            if mapping:
                return idx if idx <= 6 else 7
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step1": dict(self.step1),
            "step2": dict(self.step2),
            "step3": dict(self.step3),
            "step4": dict(self.step4),
            "step5": dict(self.step5),
            "step6": {corner: dict(fields) for corner, fields in self.step6.items()},
            "supporting": dict(self.supporting),
        }


@dataclass
class SolveChainResult:
    step1: Any
    step2: Any
    step3: Any
    step4: Any
    step5: Any
    step6: Any
    supporting: Any
    legal_validation: LegalValidation
    decision_trace: list[Any]
    prediction: PredictedTelemetry | None
    prediction_confidence: PredictionConfidence
    notes: list[str] = field(default_factory=list)
    candidate_vetoes: list[CandidateVeto] = field(default_factory=list)
    optimizer_used: bool = False
    # Legacy compatibility flag. Ferrari runs should now solve indexed controls
    # directly on the legal manifold instead of passing them through.
    ferrari_passthrough: bool = False
    # Solver path taken: "optimizer" | "sequential" | "sequential_fallback"
    solver_path: str = "sequential"


def apply_damper_modifiers(
    step6: Any,
    modifiers: SolverModifiers,
    car: Any,
) -> None:
    """Apply click offsets from modifiers in-place."""
    if not any([
        modifiers.front_ls_rbd_offset,
        modifiers.rear_ls_rbd_offset,
        modifiers.front_hs_comp_offset,
        modifiers.rear_hs_comp_offset,
    ]):
        return

    d = car.damper
    lo_ls, hi_ls = d.ls_comp_range
    lo_hs, hi_hs = d.hs_comp_range

    def clamp_click(val: int, lo: int, hi: int) -> int:
        return max(lo, min(hi, val))

    for corner in [step6.lf, step6.rf]:
        corner.ls_rbd = clamp_click(corner.ls_rbd + modifiers.front_ls_rbd_offset, lo_ls, hi_ls)
        corner.hs_comp = clamp_click(corner.hs_comp + modifiers.front_hs_comp_offset, lo_hs, hi_hs)

    for corner in [step6.lr, step6.rr]:
        corner.ls_rbd = clamp_click(corner.ls_rbd + modifiers.rear_ls_rbd_offset, lo_ls, hi_ls)
        corner.hs_comp = clamp_click(corner.hs_comp + modifiers.rear_hs_comp_offset, lo_hs, hi_hs)


def _default_modifiers(modifiers: SolverModifiers | None) -> SolverModifiers:
    return modifiers if modifiers is not None else SolverModifiers()


def _front_camber(inputs: SolveChainInputs, step5: Any | None = None) -> float:
    if step5 is not None and hasattr(step5, "front_camber_deg"):
        return float(step5.front_camber_deg)
    setup_camber = getattr(inputs.current_setup, "front_camber_deg", 0.0)
    if setup_camber:
        return float(setup_camber)
    return float(inputs.car.geometry.front_camber_baseline_deg)


def _candidate_veto_for_solution(
    *,
    inputs: SolveChainInputs,
    step1: Any,
    step2: Any,
    step3: Any,
    step4: Any,
    step5: Any,
    step6: Any,
) -> CandidateVeto | None:
    if not inputs.failed_validation_clusters:
        return None
    fingerprint = fingerprint_from_solver_steps(
        wing=inputs.wing_angle,
        fuel_l=inputs.fuel_load_l,
        step1=step1,
        step2=step2,
        step3=step3,
        step4=step4,
        step5=step5,
        step6=step6,
    )
    matched = match_failed_cluster(fingerprint, inputs.failed_validation_clusters)
    if matched is None:
        return None
    penalty = 1e6 if matched.penalty_mode == "hard" else 5e4
    return CandidateVeto(
        fingerprint=fingerprint,
        matched_session_label=matched.latest_session_label,
        matched_session_idx=matched.latest_session_idx,
        reason=matched.reason,
        penalty=penalty,
        penalty_mode=matched.penalty_mode,
    )


def _build_supporting(inputs: SolveChainInputs) -> Any:
    solver = SupportingSolver(
        inputs.car,
        inputs.resolved_supporting_driver(),
        inputs.resolved_supporting_measured(),
        inputs.resolved_supporting_diagnosis(),
        track=inputs.track,
        current_setup=inputs.current_setup,
    )
    return solver.solve()


def _snap_supporting_value(field_name: str, value: Any, car: Any = None) -> Any:
    """Snap supporting parameter values to iRacing garage increments."""
    if field_name == "diff_ramp_coast":
        if not isinstance(value, (int, float)):
            return value
        v = float(value)
        valid_coast = [40, 45, 50]
        return min(valid_coast, key=lambda r: abs(r - v))
    if field_name == "diff_ramp_drive":
        if not isinstance(value, (int, float)):
            return value
        v = float(value)
        valid_drive = [65, 70, 75]
        return min(valid_drive, key=lambda r: abs(r - v))
    return snap_supporting_field_value(car, field_name, value)


def _enforce_ramp_pair(supporting: Any, car: Any = None) -> None:
    """Ensure diff_ramp_coast and diff_ramp_drive form a valid garage pair."""
    coast = getattr(supporting, "diff_ramp_coast", None)
    drive = getattr(supporting, "diff_ramp_drive", None)
    if coast is None or drive is None:
        return
    valid_pairs = car.garage_ranges.diff_coast_drive_ramp_options
    best = min(valid_pairs, key=lambda p: abs(p[0] - coast) + abs(p[1] - drive))
    supporting.diff_ramp_coast = best[0]
    supporting.diff_ramp_drive = best[1]
    # NOTE: must NOT use `or 1` — the legal options tuple has index 0 =
    # (40, 65) which is FALSY in Python. Use explicit None check to preserve
    # the legitimate idx=0 value. (Same falsy-int bug fixed in supporting_solver.)
    _idx = diff_ramp_option_index(
        car,
        coast=supporting.diff_ramp_coast,
        drive=supporting.diff_ramp_drive,
        default=1,
    )
    supporting.diff_ramp_option_idx = 1 if _idx is None else int(_idx)
    supporting.diff_ramp_angles = diff_ramp_string_for_option(
        car,
        getattr(supporting, "diff_ramp_option_idx", 1),
        ferrari_label=car.canonical_name == "ferrari",
    )


def _apply_supporting_overrides(supporting: Any, overrides: dict[str, Any], car: Any = None) -> None:
    for field_name, value in overrides.items():
        if field_name == "diff_ramp_option_idx":
            option_idx = _snap_supporting_value(field_name, value, car)
            coast, drive = diff_ramp_pair_for_option(car, option_idx, default_idx=1)
            if hasattr(supporting, "diff_ramp_option_idx"):
                supporting.diff_ramp_option_idx = option_idx
            if hasattr(supporting, "diff_ramp_coast"):
                supporting.diff_ramp_coast = coast
            if hasattr(supporting, "diff_ramp_drive"):
                supporting.diff_ramp_drive = drive
            if hasattr(supporting, "diff_ramp_angles"):
                supporting.diff_ramp_angles = diff_ramp_string_for_option(
                    car,
                    option_idx,
                    ferrari_label=car.canonical_name == "ferrari",
                )
            continue
        if hasattr(supporting, field_name):
            setattr(supporting, field_name, _snap_supporting_value(field_name, value, car))


def _finalize_result(
    inputs: SolveChainInputs,
    *,
    step1: Any,
    step2: Any,
    step3: Any,
    step4: Any,
    step5: Any,
    step6: Any,
    supporting: Any,
    notes: list[str] | None = None,
    candidate_vetoes: list[CandidateVeto] | None = None,
    optimizer_used: bool = False,
) -> SolveChainResult:
    legal_validation = validate_solution_legality(
        car=inputs.car,
        track_name=inputs.track.track_name,
        step1=step1,
        step2=step2,
        step3=step3,
        fuel_l=inputs.fuel_load_l,
        step5=step5,
    )
    decision_trace = build_parameter_decisions(
        car_name=inputs.car.canonical_name,
        current_setup=inputs.current_setup,
        measured=inputs.measured,
        step1=step1,
        step2=step2,
        step3=step3,
        step4=step4,
        step5=step5,
        step6=step6,
        supporting=supporting,
        legality=legal_validation,
        fallback_reasons=list(getattr(inputs.measured, "fallback_reasons", []) or []),
        car=inputs.car,
    )
    prediction, prediction_confidence = predict_candidate_telemetry(
        current_setup=inputs.current_setup,
        baseline_measured=inputs.measured,
        step1=step1,
        step2=step2,
        step3=step3,
        step4=step4,
        step5=step5,
        step6=step6,
        supporting=supporting,
        corrections=inputs.prediction_corrections,
    )
    return SolveChainResult(
        step1=step1,
        step2=step2,
        step3=step3,
        step4=step4,
        step5=step5,
        step6=step6,
        supporting=supporting,
        legal_validation=legal_validation,
        decision_trace=decision_trace,
        prediction=prediction,
        prediction_confidence=prediction_confidence,
        notes=list(notes or []),
        candidate_vetoes=list(candidate_vetoes or []),
        optimizer_used=optimizer_used,
        ferrari_passthrough=False,
        solver_path="optimizer" if optimizer_used else "sequential",
    )


def _decode_ferrari_indexed_setup(car: Any, setup: Any) -> None:
    """Decode Ferrari indexed controls on current_setup to physical values in-place.

    Ferrari garage exposes heave springs as indices (0-8, 0-9) and torsion bars
    as indices (0-18). The solver needs physical N/mm and mm values. This decoding
    MUST happen before any solver step reads current_setup values.
    """
    if setup is None or car.canonical_name != "ferrari":
        return
    _indexed_keys = [
        "front_heave_nmm",
        "rear_third_nmm",
        "front_torsion_od_mm",
        # CurrentSetup uses rear_spring_nmm for Ferrari rear torsion index.
        "rear_spring_nmm",
        # Keep legacy/alternate key for robustness across callers.
        "rear_spring_rate_nmm",
    ]
    for key in _indexed_keys:
        raw = getattr(setup, key, None)
        if raw is not None:
            decoded = internal_solver_value(car, key, raw)
            if decoded is not None and decoded != raw:
                setattr(setup, key, float(decoded))


def _run_sequential_solver(inputs: SolveChainInputs) -> tuple[Any, Any, Any, Any, Any, Any, float]:
    mods = _default_modifiers(inputs.modifiers)
    car = inputs.car
    track = inputs.track
    measured = inputs.measured
    fuel = inputs.fuel_load_l

    # Decode Ferrari indexed controls BEFORE any solver step reads them
    _decode_ferrari_indexed_setup(car, inputs.current_setup)

    # In physics mode, disable all driver anchors — find the physics-optimal
    # setup regardless of what the driver loaded.
    _physics_mode = inputs.optimization_mode == "physics"

    rake_solver = RakeSolver(car, inputs.surface, track)
    step1 = rake_solver.solve(
        target_balance=inputs.target_balance,
        balance_tolerance=inputs.balance_tolerance,
        fuel_load_l=fuel,
        pin_front_min=inputs.pin_front_min,
    )

    # GT3 dispatch (W2.1): cars without heave/third architecture skip the
    # HeaveSolver constructor (which raises on car.heave_spring=None) and
    # propagate Step 1's dynamic RH targets through HeaveSolution.null().
    _k_current = None if _physics_mode else (getattr(inputs.current_setup, "front_heave_nmm", None) if inputs.current_setup else None)
    _k_rear_current = None if _physics_mode else (getattr(inputs.current_setup, "rear_third_nmm", None) if inputs.current_setup else None)
    if car.suspension_arch.has_heave_third:
        heave_solver = HeaveSolver(car, track)
        step2 = heave_solver.solve(
            dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
            dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
            front_heave_floor_nmm=mods.front_heave_min_floor_nmm,
            rear_third_floor_nmm=mods.rear_third_min_floor_nmm,
            front_heave_perch_target_mm=mods.front_heave_perch_target_mm,
            front_pushrod_mm=step1.front_pushrod_offset_mm,
            rear_pushrod_mm=step1.rear_pushrod_offset_mm,
            fuel_load_l=fuel,
            front_camber_deg=_front_camber(inputs),
            measured=inputs.measured,
            front_heave_current_nmm=_k_current,
            rear_third_current_nmm=_k_rear_current,
            prediction_corrections=inputs.prediction_corrections or None,
        )
    else:
        heave_solver = None  # GT3: Step 2 is N/A
        step2 = HeaveSolution.null(
            front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
            rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
        )

    corner_solver = CornerSpringSolver(car, track)
    _curr_rear_coil = None if _physics_mode else (
        getattr(inputs.current_setup, "rear_spring_nmm", None)
        if inputs.current_setup else None
    )
    step3 = corner_solver.solve(
        front_heave_nmm=step2.front_heave_nmm,
        rear_third_nmm=step2.rear_third_nmm,
        fuel_load_l=fuel,
        current_rear_third_nmm=None if _physics_mode else _k_rear_current,
        current_rear_spring_nmm=_curr_rear_coil,
    )
    rear_wheel_rate_nmm = step3.rear_wheel_rate_nmm

    # Backward-compat: legacy SimpleNamespace step2 mocks lack `.present`,
    # treat as present=True (GTP behaviour).
    if heave_solver is not None and getattr(step2, "present", True):
        heave_solver.reconcile_solution(
            step1,
            step2,
            step3,
            fuel_load_l=fuel,
            front_camber_deg=_front_camber(inputs),
            verbose=False,
        )
    reconcile_ride_heights(
        car,
        step1,
        step2,
        step3,
        fuel_load_l=fuel,
        track_name=track.track_name,
        verbose=False,
        surface=inputs.surface,
        track=track,
        target_balance=inputs.target_balance,
    )

    # NOTE: Previously a provisional Step 6 (DamperSolver) ran here to refine
    # heave sizing with HS damper values. Removed because it introduces a Step 6
    # dependency before Steps 4/5, violating the fixed 6-step ordering.

    damper_solver = DamperSolver(car, track)

    arb_solver = ARBSolver(car, track)
    _current_rear_arb = None if _physics_mode else (getattr(inputs.current_setup, "rear_arb_size", None) if inputs.current_setup else None)
    _current_rear_arb_blade = None if _physics_mode else (getattr(inputs.current_setup, "rear_arb_blade", None) if inputs.current_setup else None)
    _current_front_arb = None if _physics_mode else (getattr(inputs.current_setup, "front_arb_size", None) if inputs.current_setup else None)
    _current_front_arb_blade = None if _physics_mode else (getattr(inputs.current_setup, "front_arb_blade", None) if inputs.current_setup else None)
    step4 = arb_solver.solve(
        front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
        rear_wheel_rate_nmm=rear_wheel_rate_nmm,
        lltd_offset=mods.lltd_offset,
        current_rear_arb_size=_current_rear_arb,
        current_rear_arb_blade=_current_rear_arb_blade,
        current_front_arb_size=_current_front_arb,
        current_front_arb_blade=_current_front_arb_blade,
    )

    geom_solver = WheelGeometrySolver(car, track)
    step5 = geom_solver.solve(
        k_roll_total_nm_deg=step4.k_roll_front_total + step4.k_roll_rear_total,
        front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
        rear_wheel_rate_nmm=rear_wheel_rate_nmm,
        fuel_load_l=fuel,
        camber_confidence=inputs.camber_confidence,
        measured=inputs.measured,
    )
    reconcile_ride_heights(
        car,
        step1,
        step2,
        step3,
        step5=step5,
        fuel_load_l=fuel,
        track_name=track.track_name,
        verbose=False,
        surface=inputs.surface,
        track=track,
        target_balance=inputs.target_balance,
    )

    step6 = None
    try:
        step6 = damper_solver.solve(
            front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
            front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
            rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
            fuel_load_l=fuel,
            damping_ratio_scale=mods.damping_ratio_scale,
            measured=measured,
            front_heave_nmm=step2.front_heave_nmm,
            rear_third_nmm=step2.rear_third_nmm,
        )
        apply_damper_modifiers(step6, mods, car)
    except ValueError:
        # Damper solver raises when zeta targets are uncalibrated for this car.
        # The calibration gate will null this out downstream; return None so
        # the pipeline can continue and produce output for calibrated steps.
        pass
    return step1, step2, step3, step4, step5, step6, rear_wheel_rate_nmm


def _run_branching_solver(
    inputs: SolveChainInputs,
    max_heave: int = 6,
    max_corner: int = 6,
    max_arb: int = 4,
) -> tuple[Any, Any, Any, Any, Any, Any, float]:
    """Multi-candidate branching solver.

    Instead of the sequential solver's single-answer-per-step approach, this
    generates candidate sets at Steps 2, 3, and 4, evaluates the cross-product
    of top candidates through Steps 5-6, and picks the best path based on a
    lightweight physics composite score.

    The branching is bounded: max_heave × max_corner × max_arb paths are
    evaluated (default 6×6×4 = 144 paths). Steps 5 and 6 are fast (pure
    calculation), so the total time is ~2-10s depending on car complexity.

    Falls back to ``_run_sequential_solver`` if any step fails.
    """
    mods = _default_modifiers(inputs.modifiers)
    car = inputs.car
    track = inputs.track
    measured = inputs.measured
    fuel = inputs.fuel_load_l

    _decode_ferrari_indexed_setup(car, inputs.current_setup)

    # Build objective function for scoring branching paths.
    # Uses evaluate_physics() + _estimate_lap_gain() instead of the old
    # lightweight heuristic (which rewarded softer springs -- wrong for GTP).
    from solver.objective import ObjectiveFunction
    from solver.constraints import constraints_from_diagnosis
    try:
        _branching_obj = ObjectiveFunction(
            car, track, explore=False,
            scenario_profile=inputs.scenario_profile,
        )
    except Exception as e:
        logger.debug("ObjectiveFunction init failed in branching solver: %s", e)
        _branching_obj = None

    # Build telemetry-derived constraints from diagnosis (if available).
    _telemetry_constraints = constraints_from_diagnosis(
        getattr(inputs, "diagnosis", None),
        getattr(inputs, "measured", None),
    )

    # ── Step 1: Rake (single answer — Brent root-find, no branching) ──
    rake_solver = RakeSolver(car, inputs.surface, track)
    step1 = rake_solver.solve(
        target_balance=inputs.target_balance,
        balance_tolerance=inputs.balance_tolerance,
        fuel_load_l=fuel,
        pin_front_min=inputs.pin_front_min,
    )

    # ── Step 2: Heave candidates ──
    _physics_mode = inputs.optimization_mode == "physics"
    _k_current = None if _physics_mode else (getattr(inputs.current_setup, "front_heave_nmm", None) if inputs.current_setup else None)
    _k_rear_current = None if _physics_mode else (getattr(inputs.current_setup, "rear_third_nmm", None) if inputs.current_setup else None)
    # GT3 dispatch (W2.1): no heave/third architecture → single null candidate
    # so the outer branching loop runs exactly once and only Step 3+ corner /
    # ARB axes fan out.
    if car.suspension_arch.has_heave_third:
        heave_solver = HeaveSolver(car, track)
        heave_candidates = heave_solver.solve_candidates(
            dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
            dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
            front_heave_floor_nmm=mods.front_heave_min_floor_nmm,
            rear_third_floor_nmm=mods.rear_third_min_floor_nmm,
            front_heave_perch_target_mm=mods.front_heave_perch_target_mm,
            front_pushrod_mm=step1.front_pushrod_offset_mm,
            rear_pushrod_mm=step1.rear_pushrod_offset_mm,
            fuel_load_l=fuel,
            front_camber_deg=_front_camber(inputs),
            measured=inputs.measured,
            front_heave_current_nmm=_k_current,
            rear_third_current_nmm=_k_rear_current,
            n_candidates=max_heave,
        )
    else:
        heave_solver = None  # GT3: Step 2 is N/A
        heave_candidates = [
            HeaveSolution.null(
                front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
                rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
            )
        ]

    corner_solver = CornerSpringSolver(car, track)
    _curr_rear_coil = None if _physics_mode else (
        getattr(inputs.current_setup, "rear_spring_nmm", None)
        if inputs.current_setup else None
    )
    geom_solver = WheelGeometrySolver(car, track)
    damper_solver = DamperSolver(car, track)
    arb_solver_inst = ARBSolver(car, track)

    _current_rear_arb = None if _physics_mode else (getattr(inputs.current_setup, "rear_arb_size", None) if inputs.current_setup else None)
    _current_rear_arb_blade = None if _physics_mode else (getattr(inputs.current_setup, "rear_arb_blade", None) if inputs.current_setup else None)
    _current_front_arb = None if _physics_mode else (getattr(inputs.current_setup, "front_arb_size", None) if inputs.current_setup else None)
    _current_front_arb_blade = None if _physics_mode else (getattr(inputs.current_setup, "front_arb_blade", None) if inputs.current_setup else None)

    # ── Evaluate paths ──
    best_score = float("-inf")
    best_path = None

    for s2 in heave_candidates:
        # Step 3: corner spring candidates for this heave setting
        corner_candidates = corner_solver.solve_candidates(
            front_heave_nmm=s2.front_heave_nmm,
            rear_third_nmm=s2.rear_third_nmm,
            fuel_load_l=fuel,
            current_rear_third_nmm=_k_rear_current,
            current_rear_spring_nmm=_curr_rear_coil,
            max_candidates=max_corner,
        )

        for s3 in corner_candidates:
            rwr = s3.rear_wheel_rate_nmm

            # Work on copies so reconciliation doesn't mutate originals
            # across iterations. step1/s2/s3 are mutable dataclasses.
            s1_copy = copy.copy(step1)
            s2_copy = copy.copy(s2)
            s3_copy = copy.copy(s3)

            # Reconcile ride heights with this spring combo
            if heave_solver is not None and getattr(s2_copy, "present", True):
                heave_solver.reconcile_solution(s1_copy, s2_copy, s3_copy, fuel_load_l=fuel,
                                               front_camber_deg=_front_camber(inputs),
                                               verbose=False)
            reconcile_ride_heights(car, s1_copy, s2_copy, s3_copy, fuel_load_l=fuel,
                                  track_name=track.track_name, verbose=False,
                                  surface=inputs.surface, track=track,
                                  target_balance=inputs.target_balance)

            # Step 4: ARB candidates for this spring combo
            arb_candidates = arb_solver_inst.solve_candidates(
                front_wheel_rate_nmm=s3_copy.front_wheel_rate_nmm,
                rear_wheel_rate_nmm=rwr,
                lltd_offset=mods.lltd_offset,
                current_rear_arb_size=_current_rear_arb,
                current_rear_arb_blade=_current_rear_arb_blade,
                current_front_arb_size=_current_front_arb,
                current_front_arb_blade=_current_front_arb_blade,
                max_candidates=max_arb,
            )

            for s4 in arb_candidates:
                # Step 5: geometry (fast, deterministic)
                s5 = geom_solver.solve(
                    k_roll_total_nm_deg=s4.k_roll_front_total + s4.k_roll_rear_total,
                    front_wheel_rate_nmm=s3_copy.front_wheel_rate_nmm,
                    rear_wheel_rate_nmm=rwr,
                    fuel_load_l=fuel,
                    camber_confidence=inputs.camber_confidence,
                    measured=inputs.measured,
                )

                # Second reconciliation (post-geometry, same as sequential solver)
                reconcile_ride_heights(
                    car, s1_copy, s2_copy, s3_copy, step5=s5,
                    fuel_load_l=fuel, track_name=track.track_name,
                    verbose=False, surface=inputs.surface, track=track,
                    target_balance=inputs.target_balance,
                )

                # Step 6: dampers (fast, deterministic)
                s6 = None
                try:
                    s6 = damper_solver.solve(
                        front_wheel_rate_nmm=s3_copy.front_wheel_rate_nmm,
                        rear_wheel_rate_nmm=rwr,
                        front_dynamic_rh_mm=s1_copy.dynamic_front_rh_mm,
                        rear_dynamic_rh_mm=s1_copy.dynamic_rear_rh_mm,
                        fuel_load_l=fuel,
                        damping_ratio_scale=mods.damping_ratio_scale,
                        measured=measured,
                        front_heave_nmm=s2_copy.front_heave_nmm,
                        rear_third_nmm=s2_copy.rear_third_nmm,
                    )
                    apply_damper_modifiers(s6, mods, car)
                except ValueError:
                    pass

                # ── Score this path ──
                # Use the full ObjectiveFunction.evaluate_physics() +
                # _estimate_lap_gain() for scoring.  This replaces the old
                # lightweight heuristic that rewarded softer springs (wrong
                # for ground-effect cars where platform stability dominates).
                if _branching_obj is not None:
                    try:
                        _params = solver_steps_to_params(
                            s1_copy, s2_copy, s3_copy, s4, s5, s6, car=car,
                        )
                        _physics = _branching_obj.evaluate_physics(_params)
                        # Hard veto: negative bottoming or vortex stall margin
                        if _physics.front_bottoming_margin_mm < 0 or _physics.rear_bottoming_margin_mm < 0:
                            score = -1e6
                        elif _physics.stall_margin_mm is not None and _physics.stall_margin_mm < 0:
                            score = -1e6
                        else:
                            # Primary score: lap gain from calibrated physics hierarchy
                            score = _branching_obj._estimate_lap_gain(_params, _physics)
                            # Platform risk bonus: more bottoming margin is good (diminishing)
                            score += math.log1p(max(_physics.front_bottoming_margin_mm, 0)) * 5
                            score += math.log1p(max(_physics.rear_bottoming_margin_mm, 0)) * 5
                            # Telemetry-derived constraint penalties
                            if _telemetry_constraints.constraints:
                                _c_penalty, _, _c_veto = _telemetry_constraints.evaluate(_physics)
                                if _c_veto:
                                    score = -1e6
                                else:
                                    score -= _c_penalty
                    except Exception as e:
                        logger.debug("Branching path scoring failed: %s", e)
                        score = float("-inf")
                else:
                    # Fallback: minimal safety-only scoring when objective
                    # cannot be instantiated (e.g. missing aero data).
                    front_margin = s2_copy.front_bottoming_margin_mm
                    rear_margin = s2_copy.rear_bottoming_margin_mm
                    if front_margin < 0 or rear_margin < 0:
                        score = -1e6
                    else:
                        score = (
                            math.log1p(max(front_margin, 0)) * 10
                            + math.log1p(max(rear_margin, 0)) * 10
                            - s4.lltd_error * 500
                        )
                if score > best_score:
                    best_score = score
                    best_path = (s1_copy, s2_copy, s3_copy, s4, s5, s6, rwr)

    if best_path is None:
        # Fallback to sequential solver
        return _run_sequential_solver(inputs)

    # Apply iterative coupling refinement to the best path
    s1, s2, s3, s4, s5, s6, rwr = best_path
    return _iterative_coupling_refinement(inputs, s1, s2, s3, s4, s5, s6, rwr)


def _iterative_coupling_refinement(
    inputs: SolveChainInputs,
    step1, step2, step3, step4, step5, step6,
    rear_wheel_rate_nmm: float,
    max_iterations: int = 3,
    df_tol: float = 0.001,
    lltd_tol: float = 0.002,
    sigma_tol_mm: float = 0.1,
) -> tuple[Any, Any, Any, Any, Any, Any, float]:
    """Objective-driven iterative coupling resolution.

    After the initial solver pass, re-optimizes each step against the full
    objective while resolving inter-step coupling residuals:

    - **DF balance drift**: Step 1 ↔ Steps 2-3 spring compliance coupling.
    - **LLTD drift**: Step 3 wheel rates → Step 4 roll stiffness.
    - **Step 4 re-optimization**: Enumerate all ARB blade options and pick
      the one that maximizes the objective (not just closest to LLTD target).

    Uses ``ObjectiveFunction.evaluate_physics()`` + ``_estimate_lap_gain()``
    to score each iteration.  Stops when score stops improving or residuals
    converge.  Max 3 iterations to bound runtime.
    """
    mods = _default_modifiers(inputs.modifiers)
    car = inputs.car
    track = inputs.track
    fuel = inputs.fuel_load_l

    # Build objective for scoring iterations.
    from solver.objective import ObjectiveFunction
    try:
        _refine_obj = ObjectiveFunction(
            car, track, explore=False,
            scenario_profile=inputs.scenario_profile,
        )
    except Exception as e:
        logger.debug("ObjectiveFunction init failed in refinement: %s", e)
        _refine_obj = None

    def _score_current() -> float:
        """Score the current step1-6 combination using the full objective."""
        if _refine_obj is None:
            return 0.0
        try:
            _p = solver_steps_to_params(step1, step2, step3, step4, step5, step6, car=car)
            _phys = _refine_obj.evaluate_physics(_p)
            return _refine_obj._estimate_lap_gain(_p, _phys)
        except Exception as e:
            logger.debug("Refinement scoring failed: %s", e)
            return 0.0

    prev_score = _score_current()

    for iteration in range(max_iterations):
        # ── Check DF balance residual ──
        aero_surface = inputs.surface
        if aero_surface is not None and hasattr(aero_surface, "df_balance"):
            try:
                actual_balance = aero_surface.df_balance(
                    step1.dynamic_front_rh_mm,
                    step1.dynamic_rear_rh_mm,
                    inputs.wing_angle,
                )
                df_residual = abs(actual_balance - inputs.target_balance)
            except Exception as e:
                logger.debug("DF balance check failed: %s", e)
                df_residual = 0.0
        else:
            df_residual = 0.0

        # ── Check LLTD residual ──
        if step4 is not None:
            lltd_residual = abs(step4.lltd_error)
        else:
            lltd_residual = 0.0

        # ── Check σ residual (front + rear platform stability) ──
        # σ residual = max(0, sigma_at_rate - sigma_target) for each axle.
        # If both axles meet target, residual is 0 and σ does not block
        # convergence; if either is over target the loop should keep iterating
        # (Step 2 may need stiffer springs, which couples back to Step 1
        # static RH and Step 4 roll stiffness).
        sigma_residual = 0.0
        if step2 is not None:
            f_target = getattr(step2, "front_sigma_target_mm", 0.0) or 0.0
            r_target = getattr(step2, "rear_sigma_target_mm", 0.0) or 0.0
            if f_target > 0:
                sigma_residual = max(sigma_residual,
                                     step2.front_sigma_at_rate_mm - f_target)
            if r_target > 0:
                sigma_residual = max(sigma_residual,
                                     step2.rear_sigma_at_rate_mm - r_target)
            sigma_residual = max(0.0, sigma_residual)

        # ── Check convergence ──
        converged = (
            df_residual <= df_tol
            and lltd_residual <= lltd_tol
            and sigma_residual <= sigma_tol_mm
        )
        if converged:
            break

        # ── Re-solve Step 1 if DF balance drifted ──
        if df_residual > df_tol and aero_surface is not None:
            rake_solver = RakeSolver(car, inputs.surface, track)
            try:
                actual_balance = aero_surface.df_balance(
                    step1.dynamic_front_rh_mm,
                    step1.dynamic_rear_rh_mm,
                    inputs.wing_angle,
                )
                correction = inputs.target_balance - actual_balance
                # Damping factor 0.6 prevents full-gain overshoot in nonlinear aero maps
                corrected_target = inputs.target_balance + 0.6 * correction
                new_step1 = rake_solver.solve(
                    target_balance=corrected_target,
                    balance_tolerance=inputs.balance_tolerance,
                    fuel_load_l=fuel,
                    pin_front_min=inputs.pin_front_min,
                )
                step1 = new_step1
            except Exception as e:
                logger.debug("Coupling re-solve Step 1 failed: %s", e)

        # ── Re-solve Step 4 (ARBs) with objective-driven blade selection ──
        # Instead of just re-solving for LLTD target, enumerate blade options
        # and pick the one that maximizes the objective score.
        if step3 is not None and step4 is not None:
            _physics_mode = inputs.optimization_mode == "physics"
            _current_rear_arb = None if _physics_mode else (
                getattr(inputs.current_setup, "rear_arb_size", None) if inputs.current_setup else None
            )
            _current_rear_arb_blade = None if _physics_mode else (
                getattr(inputs.current_setup, "rear_arb_blade", None) if inputs.current_setup else None
            )
            arb_solver = ARBSolver(car, track)
            try:
                # Try multi-candidate ARB solve if available, else single solve.
                if hasattr(arb_solver, "solve_candidates"):
                    arb_candidates = arb_solver.solve_candidates(
                        front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
                        rear_wheel_rate_nmm=rear_wheel_rate_nmm,
                        lltd_offset=mods.lltd_offset,
                        current_rear_arb_size=_current_rear_arb,
                        current_rear_arb_blade=_current_rear_arb_blade,
                        max_candidates=5,
                    )
                    # Score each ARB candidate via the objective.
                    best_arb_score = float("-inf")
                    best_arb = step4
                    for arb_cand in arb_candidates:
                        if _refine_obj is not None:
                            try:
                                _p = solver_steps_to_params(
                                    step1, step2, step3, arb_cand, step5, step6, car=car,
                                )
                                _phys = _refine_obj.evaluate_physics(_p)
                                _s = _refine_obj._estimate_lap_gain(_p, _phys)
                            except Exception as e:
                                logger.debug("ARB candidate scoring failed: %s", e)
                                _s = float("-inf")
                        else:
                            _s = -abs(arb_cand.lltd_error) * 500
                        if _s > best_arb_score:
                            best_arb_score = _s
                            best_arb = arb_cand
                    step4 = best_arb
                else:
                    new_step4 = arb_solver.solve(
                        front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
                        rear_wheel_rate_nmm=rear_wheel_rate_nmm,
                        lltd_offset=mods.lltd_offset,
                        current_rear_arb_size=_current_rear_arb,
                        current_rear_arb_blade=_current_rear_arb_blade,
                    )
                    step4 = new_step4
            except Exception as e:
                logger.debug("Coupling re-solve Step 4 (ARBs) failed: %s", e)

        # Re-run Steps 5-6 with updated inputs
        if step4 is not None:
            geom_solver = WheelGeometrySolver(car, track)
            try:
                step5 = geom_solver.solve(
                    k_roll_total_nm_deg=step4.k_roll_front_total + step4.k_roll_rear_total,
                    front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
                    rear_wheel_rate_nmm=rear_wheel_rate_nmm,
                    fuel_load_l=fuel,
                    camber_confidence=inputs.camber_confidence,
                    measured=inputs.measured,
                )
            except Exception as e:
                logger.debug("Coupling re-solve Step 5 (geometry) failed: %s", e)

        if step3 is not None:
            reconcile_ride_heights(
                car, step1, step2, step3, step5=step5,
                fuel_load_l=fuel, track_name=track.track_name,
                verbose=False, surface=inputs.surface, track=track,
                target_balance=inputs.target_balance,
            )

        if step6 is not None:
            damper_solver = DamperSolver(car, track)
            try:
                step6 = damper_solver.solve(
                    front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
                    rear_wheel_rate_nmm=rear_wheel_rate_nmm,
                    front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
                    rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
                    fuel_load_l=fuel,
                    damping_ratio_scale=mods.damping_ratio_scale,
                    measured=inputs.measured,
                    front_heave_nmm=step2.front_heave_nmm,
                    rear_third_nmm=step2.rear_third_nmm,
                )
                apply_damper_modifiers(step6, mods, car)
            except Exception as e:
                logger.debug("Coupling re-solve Step 6 (dampers) failed: %s", e)

        # ── Check score improvement ──
        # Stop iterating if the objective score hasn't improved.
        current_score = _score_current()
        if current_score <= prev_score + 0.01:  # need at least 0.01ms gain
            break
        prev_score = current_score

    return step1, step2, step3, step4, step5, step6, rear_wheel_rate_nmm


def run_base_solve(inputs: SolveChainInputs) -> SolveChainResult:
    # Decode Ferrari indexed controls early — before optimizer or sequential solver
    _decode_ferrari_indexed_setup(inputs.car, inputs.current_setup)
    mods = _default_modifiers(inputs.modifiers)
    notes: list[str] = []
    optimized = optimize_if_supported(
        car=inputs.car,
        surface=inputs.surface,
        track=inputs.track,
        target_balance=inputs.target_balance,
        balance_tolerance=inputs.balance_tolerance,
        fuel_load_l=inputs.fuel_load_l,
        pin_front_min=inputs.pin_front_min,
        wing_angle=inputs.wing_angle,
        legacy_solver=inputs.legacy_solver,
        damping_ratio_scale=mods.damping_ratio_scale,
        lltd_offset=mods.lltd_offset,
        measured=inputs.measured,
        camber_confidence=inputs.camber_confidence,
        failed_validation_clusters=inputs.failed_validation_clusters,
        front_heave_floor_nmm=mods.front_heave_min_floor_nmm,
        rear_third_floor_nmm=mods.rear_third_min_floor_nmm,
        front_heave_perch_target_mm=mods.front_heave_perch_target_mm,
    )
    candidate_vetoes = list(getattr(optimized, "candidate_vetoes", []) or []) if optimized is not None else []

    if optimized is not None and not getattr(optimized, "all_candidates_vetoed", False):
        step1 = optimized.step1
        step2 = optimized.step2
        step3 = optimized.step3
        step4 = optimized.step4
        step5 = optimized.step5
        step6 = optimized.step6
        apply_damper_modifiers(step6, mods, inputs.car)
        notes.append("Selected constrained optimizer candidate.")
    else:
        # Try branching solver first (evaluates multi-candidate paths),
        # fall back to sequential if branching raises or returns None.
        try:
            step1, step2, step3, step4, step5, step6, _rear_wheel_rate = _run_branching_solver(inputs)
            _used_branching = True
        except Exception as e:
            logger.debug("Branching solver failed, falling back to sequential: %s", e)
            step1, step2, step3, step4, step5, step6, _rear_wheel_rate = _run_sequential_solver(inputs)
            _used_branching = False
        sequential_veto = _candidate_veto_for_solution(
            inputs=inputs,
            step1=step1,
            step2=step2,
            step3=step3,
            step4=step4,
            step5=step5,
            step6=step6,
        )
        if sequential_veto is None:
            _solver_label = "branching" if _used_branching else "sequential"
            notes.append(
                f"Selected {_solver_label} fallback." if optimized is not None else f"Selected {_solver_label} solver path."
            )
        elif optimized is None:
            candidate_vetoes.append(sequential_veto)
            notes.append("Sequential solution matched a failed validation cluster; using best available fallback with warning.")
        else:
            candidate_vetoes.append(sequential_veto)
            step1 = optimized.step1
            step2 = optimized.step2
            step3 = optimized.step3
            step4 = optimized.step4
            step5 = optimized.step5
            step6 = optimized.step6
            apply_damper_modifiers(step6, mods, inputs.car)
            notes.append(
                "Sequential fallback also matched a rejected setup; returning the lowest-penalty optimizer candidate."
            )

    supporting = _build_supporting(inputs)
    supporting.fuel_l = round(float(inputs.fuel_load_l), 1)
    supporting.fuel_target_l = round(float(getattr(inputs.current_setup, "fuel_target_l", 0.0) or inputs.fuel_load_l), 1)
    _enforce_ramp_pair(supporting, inputs.car)
    from solver.bmw_rotation_search import search_rotation_controls

    preliminary_result = _finalize_result(
        inputs,
        step1=step1,
        step2=step2,
        step3=step3,
        step4=step4,
        step5=step5,
        step6=step6,
        supporting=supporting,
        notes=notes,
        candidate_vetoes=candidate_vetoes,
        optimizer_used=optimized is not None and not getattr(optimized, "all_candidates_vetoed", False),
    )
    rotation_search = search_rotation_controls(base_result=preliminary_result, inputs=inputs)
    if rotation_search is not None:
        result = rotation_search.result
        result.notes = list(dict.fromkeys(list(preliminary_result.notes) + list(result.notes) + list(rotation_search.notes)))
        return result
    return _finalize_result(
        inputs,
        step1=step1,
        step2=step2,
        step3=step3,
        step4=step4,
        step5=step5,
        step6=step6,
        supporting=supporting,
        notes=notes,
        candidate_vetoes=candidate_vetoes,
        optimizer_used=optimized is not None and not getattr(optimized, "all_candidates_vetoed", False),
    )


def _build_explicit_corner_settings(base_step6: Any, overrides: dict[str, dict[str, Any]]) -> dict[str, CornerDamperSettings]:
    result: dict[str, CornerDamperSettings] = {}
    for corner_name in ("lf", "rf", "lr", "rr"):
        base_corner = getattr(base_step6, corner_name)
        merged = {
            "ls_comp": base_corner.ls_comp,
            "ls_rbd": base_corner.ls_rbd,
            "hs_comp": base_corner.hs_comp,
            "hs_rbd": base_corner.hs_rbd,
            "hs_slope": base_corner.hs_slope,
            "hs_slope_rbd": base_corner.hs_slope_rbd,
        }
        merged.update(overrides.get(corner_name, {}))
        result[corner_name] = CornerDamperSettings(**merged)
    return result


def materialize_overrides(
    base_result: SolveChainResult,
    overrides: SolveChainOverrides,
    inputs: SolveChainInputs,
) -> SolveChainResult:
    earliest = overrides.earliest_step()
    if earliest is None:
        return copy.deepcopy(base_result)

    mods = _default_modifiers(inputs.modifiers)
    car = inputs.car
    track = inputs.track

    step1 = copy.deepcopy(base_result.step1)
    step2 = copy.deepcopy(base_result.step2)
    step3 = copy.deepcopy(base_result.step3)
    step4 = copy.deepcopy(base_result.step4)
    step5 = copy.deepcopy(base_result.step5)
    step6 = copy.deepcopy(base_result.step6)

    if earliest == 1:
        rake_solver = RakeSolver(car, inputs.surface, track)
        step1 = rake_solver.solution_from_explicit_offsets(
            target_balance=inputs.target_balance,
            fuel_load_l=inputs.fuel_load_l,
            front_pushrod_offset_mm=overrides.step1.get("front_pushrod_offset_mm", step1.front_pushrod_offset_mm),
            rear_pushrod_offset_mm=overrides.step1.get("rear_pushrod_offset_mm", step1.rear_pushrod_offset_mm),
            static_front_rh_mm=overrides.step1.get("static_front_rh_mm", step1.static_front_rh_mm),
            static_rear_rh_mm=overrides.step1.get("static_rear_rh_mm", step1.static_rear_rh_mm),
        )

    rebuild_step23 = earliest <= 3
    if rebuild_step23:
        # GT3 dispatch (W2.1): no HeaveSolver — Step 2 is null, but Step 3 is
        # still re-solved through the corner spring path below.
        heave_solver = (
            HeaveSolver(car, track)
            if car.suspension_arch.has_heave_third
            else None
        )
        corner_solver = CornerSpringSolver(car, track)
        step2_targets = {
            "front_heave_nmm": overrides.step2.get(
                "front_heave_nmm",
                public_output_value(car, "front_heave_nmm", step2.front_heave_nmm),
            ),
            "rear_third_nmm": overrides.step2.get(
                "rear_third_nmm",
                public_output_value(car, "rear_third_nmm", step2.rear_third_nmm),
            ),
            "perch_offset_front_mm": overrides.step2.get("perch_offset_front_mm", step2.perch_offset_front_mm),
            "perch_offset_rear_mm": overrides.step2.get("perch_offset_rear_mm", step2.perch_offset_rear_mm),
        }
        step3_targets = {
            "front_torsion_od_mm": overrides.step3.get(
                "front_torsion_od_mm",
                public_output_value(car, "front_torsion_od_mm", step3.front_torsion_od_mm),
            ),
            "front_roll_spring_nmm": overrides.step3.get(
                "front_roll_spring_nmm",
                step3.front_roll_spring_nmm,
            ),
            "rear_spring_rate_nmm": overrides.step3.get(
                "rear_spring_rate_nmm",
                public_output_value(car, "rear_spring_rate_nmm", step3.rear_spring_rate_nmm),
            ),
            "rear_spring_perch_mm": overrides.step3.get("rear_spring_perch_mm", step3.rear_spring_perch_mm),
            "rear_torsion_od_mm": overrides.step3.get("rear_torsion_od_mm", step3.rear_torsion_od_mm),
        }
        decoded_step2_targets = {
            "front_heave_nmm": internal_solver_value(car, "front_heave_nmm", step2_targets["front_heave_nmm"]),
            "rear_third_nmm": internal_solver_value(car, "rear_third_nmm", step2_targets["rear_third_nmm"]),
            "perch_offset_front_mm": step2_targets["perch_offset_front_mm"],
            "perch_offset_rear_mm": step2_targets["perch_offset_rear_mm"],
        }
        decoded_step3_targets = {
            "front_torsion_od_mm": internal_solver_value(car, "front_torsion_od_mm", step3_targets["front_torsion_od_mm"]),
            "front_roll_spring_nmm": step3_targets["front_roll_spring_nmm"],
            "rear_spring_rate_nmm": internal_solver_value(car, "rear_spring_rate_nmm", step3_targets["rear_spring_rate_nmm"]),
            "rear_spring_perch_mm": step3_targets["rear_spring_perch_mm"],
            "rear_torsion_od_mm": (
                internal_solver_value(car, "rear_torsion_od_mm", step3_targets["rear_torsion_od_mm"])
                if step3_targets["rear_torsion_od_mm"] is not None
                else None
            ),
        }

        explicit_step2 = bool(overrides.step2 or overrides.step3)
        if heave_solver is None:
            # GT3 dispatch (W2.1): null Step 2; downstream Step 3 corner spring
            # is rebuilt below from step1 dynamic RH + corner overrides.
            step2 = HeaveSolution.null(
                front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
                rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
            )
        elif explicit_step2:
            step2 = heave_solver.solution_from_explicit_settings(
                dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
                dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
                front_heave_nmm=decoded_step2_targets["front_heave_nmm"],
                rear_third_nmm=decoded_step2_targets["rear_third_nmm"],
                front_heave_perch_mm=decoded_step2_targets["perch_offset_front_mm"],
                rear_third_perch_mm=decoded_step2_targets["perch_offset_rear_mm"],
                front_pushrod_mm=step1.front_pushrod_offset_mm,
                rear_pushrod_mm=step1.rear_pushrod_offset_mm,
                front_torsion_od_mm=decoded_step3_targets["front_torsion_od_mm"],
                rear_spring_nmm=decoded_step3_targets["rear_spring_rate_nmm"],
                rear_spring_perch_mm=decoded_step3_targets["rear_spring_perch_mm"],
                fuel_load_l=inputs.fuel_load_l,
                front_camber_deg=_front_camber(inputs),
            )
        else:
            _k_current = (
                getattr(inputs.current_setup, "front_heave_nmm", None)
                if inputs.current_setup else None
            )
            _k_rear_current = (
                getattr(inputs.current_setup, "rear_third_nmm", None)
                if inputs.current_setup else None
            )
            step2 = heave_solver.solve(
                dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
                dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
                front_heave_floor_nmm=mods.front_heave_min_floor_nmm,
                rear_third_floor_nmm=mods.rear_third_min_floor_nmm,
                front_heave_perch_target_mm=mods.front_heave_perch_target_mm,
                front_pushrod_mm=step1.front_pushrod_offset_mm,
                rear_pushrod_mm=step1.rear_pushrod_offset_mm,
                fuel_load_l=inputs.fuel_load_l,
                front_camber_deg=_front_camber(inputs),
                measured=inputs.measured,
                front_heave_current_nmm=_k_current,
                rear_third_current_nmm=_k_rear_current,
            )

        if overrides.step3:
            step3 = corner_solver.solution_from_explicit_rates(
                front_heave_nmm=step2.front_heave_nmm,
                rear_third_nmm=step2.rear_third_nmm,
                front_torsion_od_mm=decoded_step3_targets["front_torsion_od_mm"],
                rear_spring_rate_nmm=decoded_step3_targets["rear_spring_rate_nmm"],
                fuel_load_l=inputs.fuel_load_l,
                rear_spring_perch_mm=decoded_step3_targets["rear_spring_perch_mm"],
                rear_torsion_od_mm=decoded_step3_targets["rear_torsion_od_mm"],
                front_roll_spring_nmm=decoded_step3_targets.get("front_roll_spring_nmm"),
            )
        else:
            _curr_rt = (
                getattr(inputs.current_setup, "rear_third_nmm", None)
                if inputs.current_setup else None
            )
            _curr_rc = (
                getattr(inputs.current_setup, "rear_spring_nmm", None)
                if inputs.current_setup else None
            )
            step3 = corner_solver.solve(
                front_heave_nmm=step2.front_heave_nmm,
                rear_third_nmm=step2.rear_third_nmm,
                fuel_load_l=inputs.fuel_load_l,
                current_rear_third_nmm=_curr_rt,
                current_rear_spring_nmm=_curr_rc,
            )
        rear_wheel_rate_nmm = step3.rear_wheel_rate_nmm
        if heave_solver is not None and getattr(step2, "present", True):
            heave_solver.reconcile_solution(
                step1,
                step2,
                step3,
                fuel_load_l=inputs.fuel_load_l,
                front_camber_deg=_front_camber(inputs),
                verbose=False,
            )
        reconcile_ride_heights(
            car,
            step1,
            step2,
            step3,
            fuel_load_l=inputs.fuel_load_l,
            track_name=track.track_name,
            verbose=False,
            surface=inputs.surface,
            track=track,
            target_balance=inputs.target_balance,
        )

        damper_solver = DamperSolver(car, track)
        # COUPLING APPROXIMATION: The heave excursion model has a weak dependency
        # on HS damping coefficient (HS dampers reduce effective excursion by ~5–10%
        # at typical GTP speeds). We use the base solve's step6 HS values as the
        # coupling estimate rather than re-solving step6 here (which would violate
        # the 1→2→3→4→5→6 workflow ordering — steps 4 and 5 have not been rebuilt
        # yet at this point in materialize_overrides).
        # If step6 from the base result exists, borrow its HS coefficients.
        # If not (first solve, uncalibrated zeta), use 0 which is physics-correct
        # for the undamped excursion bound.
        _prev_step6 = base_result.step6
        _prov_hs_front = (
            _prev_step6.c_hs_front if _prev_step6 is not None else 0.0
        )
        _prov_hs_rear = (
            _prev_step6.c_hs_rear if _prev_step6 is not None else 0.0
        )

        if heave_solver is None:
            # GT3 dispatch (W2.1): step2 already null from above; nothing to do.
            pass
        elif explicit_step2:
            step2 = heave_solver.solution_from_explicit_settings(
                dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
                dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
                front_heave_nmm=decoded_step2_targets["front_heave_nmm"],
                rear_third_nmm=decoded_step2_targets["rear_third_nmm"],
                front_heave_perch_mm=decoded_step2_targets["perch_offset_front_mm"],
                rear_third_perch_mm=decoded_step2_targets["perch_offset_rear_mm"],
                front_pushrod_mm=step1.front_pushrod_offset_mm,
                rear_pushrod_mm=step1.rear_pushrod_offset_mm,
                front_torsion_od_mm=step3.front_torsion_od_mm,
                rear_spring_nmm=step3.rear_spring_rate_nmm,
                rear_spring_perch_mm=step3.rear_spring_perch_mm,
                fuel_load_l=inputs.fuel_load_l,
                front_camber_deg=_front_camber(inputs),
                front_hs_damper_nsm=_prov_hs_front,
                rear_hs_damper_nsm=_prov_hs_rear,
            )
        else:
            step2 = heave_solver.solve(
                dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
                dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
                front_heave_floor_nmm=mods.front_heave_min_floor_nmm,
                rear_third_floor_nmm=mods.rear_third_min_floor_nmm,
                front_heave_perch_target_mm=mods.front_heave_perch_target_mm,
                front_pushrod_mm=step1.front_pushrod_offset_mm,
                rear_pushrod_mm=step1.rear_pushrod_offset_mm,
                front_torsion_od_mm=step3.front_torsion_od_mm,
                rear_spring_nmm=step3.rear_spring_rate_nmm,
                rear_spring_perch_mm=step3.rear_spring_perch_mm,
                rear_third_perch_mm=step2.perch_offset_rear_mm,
                fuel_load_l=inputs.fuel_load_l,
                front_camber_deg=_front_camber(inputs),
                front_hs_damper_nsm=_prov_hs_front,
                rear_hs_damper_nsm=_prov_hs_rear,
            )

        if overrides.step3:
            step3 = corner_solver.solution_from_explicit_rates(
                front_heave_nmm=step2.front_heave_nmm,
                rear_third_nmm=step2.rear_third_nmm,
                front_torsion_od_mm=decoded_step3_targets["front_torsion_od_mm"],
                rear_spring_rate_nmm=decoded_step3_targets["rear_spring_rate_nmm"],
                fuel_load_l=inputs.fuel_load_l,
                rear_spring_perch_mm=decoded_step3_targets["rear_spring_perch_mm"],
                rear_torsion_od_mm=decoded_step3_targets["rear_torsion_od_mm"],
                front_roll_spring_nmm=decoded_step3_targets.get("front_roll_spring_nmm"),
            )
        else:
            _curr_rt = (
                getattr(inputs.current_setup, "rear_third_nmm", None)
                if inputs.current_setup else None
            )
            _curr_rc = (
                getattr(inputs.current_setup, "rear_spring_nmm", None)
                if inputs.current_setup else None
            )
            step3 = corner_solver.solve(
                front_heave_nmm=step2.front_heave_nmm,
                rear_third_nmm=step2.rear_third_nmm,
                fuel_load_l=inputs.fuel_load_l,
                current_rear_third_nmm=_curr_rt,
                current_rear_spring_nmm=_curr_rc,
            )
        rear_wheel_rate_nmm = step3.rear_wheel_rate_nmm
        if heave_solver is not None and getattr(step2, "present", True):
            heave_solver.reconcile_solution(
                step1,
                step2,
                step3,
                fuel_load_l=inputs.fuel_load_l,
                front_camber_deg=_front_camber(inputs),
                front_hs_damper_nsm=_prov_hs_front,
                verbose=False,
            )
        reconcile_ride_heights(
            car,
            step1,
            step2,
            step3,
            fuel_load_l=inputs.fuel_load_l,
            track_name=track.track_name,
            verbose=False,
            surface=inputs.surface,
            track=track,
            target_balance=inputs.target_balance,
        )
    else:
        rear_wheel_rate_nmm = step3.rear_wheel_rate_nmm

    rebuild_step4 = earliest <= 4 or rebuild_step23
    if rebuild_step4:
        arb_solver = ARBSolver(car, track)
        if overrides.step4:
            # ARB size may come in as ordinal int (0=Soft,1=Medium,2=Stiff) from grid search
            def _arb_size_label(val, labels):
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    idx = int(round(val))
                    if labels and 0 <= idx < len(labels):
                        return labels[idx]
                return val  # already a string label
            _f_arb_raw = overrides.step4.get("front_arb_size", step4.front_arb_size)
            _r_arb_raw = overrides.step4.get("rear_arb_size", step4.rear_arb_size)
            _f_arb_size = _arb_size_label(_f_arb_raw, car.arb.front_size_labels)
            _r_arb_size = _arb_size_label(_r_arb_raw, car.arb.rear_size_labels)
            step4 = arb_solver.solution_from_explicit_settings(
                front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
                rear_wheel_rate_nmm=rear_wheel_rate_nmm,
                front_arb_size=_f_arb_size,
                front_arb_blade_start=overrides.step4.get("front_arb_blade_start", step4.front_arb_blade_start),
                rear_arb_size=_r_arb_size,
                rear_arb_blade_start=overrides.step4.get("rear_arb_blade_start", step4.rear_arb_blade_start),
                lltd_offset=mods.lltd_offset,
                rarb_blade_slow_corner=overrides.step4.get("rarb_blade_slow_corner", step4.rarb_blade_slow_corner),
                rarb_blade_fast_corner=overrides.step4.get("rarb_blade_fast_corner", step4.rarb_blade_fast_corner),
                farb_blade_locked=overrides.step4.get("farb_blade_locked", step4.farb_blade_locked),
            )
        else:
            _current_rear_arb = getattr(inputs.current_setup, "rear_arb_size", None) if inputs.current_setup else None
            _current_rear_arb_blade = getattr(inputs.current_setup, "rear_arb_blade", None) if inputs.current_setup else None
            _current_front_arb = getattr(inputs.current_setup, "front_arb_size", None) if inputs.current_setup else None
            _current_front_arb_blade = getattr(inputs.current_setup, "front_arb_blade", None) if inputs.current_setup else None
            step4 = arb_solver.solve(
                front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
                rear_wheel_rate_nmm=rear_wheel_rate_nmm,
                lltd_offset=mods.lltd_offset,
                current_rear_arb_size=_current_rear_arb,
                current_rear_arb_blade=_current_rear_arb_blade,
                current_front_arb_size=_current_front_arb,
                current_front_arb_blade=_current_front_arb_blade,
            )

    rebuild_step5 = earliest <= 5 or rebuild_step4
    if rebuild_step5:
        geom_solver = WheelGeometrySolver(car, track)
        if overrides.step5:
            step5 = geom_solver.solution_from_explicit_settings(
                k_roll_total_nm_deg=step4.k_roll_front_total + step4.k_roll_rear_total,
                front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
                rear_wheel_rate_nmm=rear_wheel_rate_nmm,
                front_camber_deg=overrides.step5.get("front_camber_deg", step5.front_camber_deg),
                rear_camber_deg=overrides.step5.get("rear_camber_deg", step5.rear_camber_deg),
                front_toe_mm=overrides.step5.get("front_toe_mm", step5.front_toe_mm),
                rear_toe_mm=overrides.step5.get("rear_toe_mm", step5.rear_toe_mm),
                fuel_load_l=inputs.fuel_load_l,
                camber_confidence=inputs.camber_confidence,
            )
        else:
            step5 = geom_solver.solve(
                k_roll_total_nm_deg=step4.k_roll_front_total + step4.k_roll_rear_total,
                front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
                rear_wheel_rate_nmm=rear_wheel_rate_nmm,
                fuel_load_l=inputs.fuel_load_l,
                camber_confidence=inputs.camber_confidence,
                measured=inputs.measured,
            )
        reconcile_ride_heights(
            car,
            step1,
            step2,
            step3,
            step5=step5,
            fuel_load_l=inputs.fuel_load_l,
            track_name=track.track_name,
            verbose=False,
            surface=inputs.surface,
            track=track,
            target_balance=inputs.target_balance,
        )

    rebuild_step6 = earliest <= 6 or rebuild_step5
    if rebuild_step6:
        damper_solver = DamperSolver(car, track)
        if overrides.step6:
            corner_settings = _build_explicit_corner_settings(base_result.step6, overrides.step6)
            step6 = damper_solver.solution_from_explicit_settings(
                front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
                rear_wheel_rate_nmm=rear_wheel_rate_nmm,
                front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
                rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
                fuel_load_l=inputs.fuel_load_l,
                damping_ratio_scale=mods.damping_ratio_scale,
                measured=inputs.measured,
                front_heave_nmm=step2.front_heave_nmm,
                rear_third_nmm=step2.rear_third_nmm,
                lf=corner_settings["lf"],
                rf=corner_settings["rf"],
                lr=corner_settings["lr"],
                rr=corner_settings["rr"],
            )
        else:
            step6 = damper_solver.solve(
                front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
                rear_wheel_rate_nmm=rear_wheel_rate_nmm,
                front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
                rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
                fuel_load_l=inputs.fuel_load_l,
                damping_ratio_scale=mods.damping_ratio_scale,
                measured=inputs.measured,
                front_heave_nmm=step2.front_heave_nmm,
                rear_third_nmm=step2.rear_third_nmm,
            )
            apply_damper_modifiers(step6, mods, car)

    supporting = _build_supporting(inputs)
    _apply_supporting_overrides(supporting, overrides.supporting, inputs.car)
    _enforce_ramp_pair(supporting, inputs.car)
    notes = ["Materialized candidate through shared solve chain."]
    if overrides.supporting:
        notes.append("Applied family-specific supporting overrides on top of fresh supporting solve.")
    return _finalize_result(
        inputs,
        step1=step1,
        step2=step2,
        step3=step3,
        step4=step4,
        step5=step5,
        step6=step6,
        supporting=supporting,
        notes=notes,
        candidate_vetoes=[],
        optimizer_used=False,
    )
