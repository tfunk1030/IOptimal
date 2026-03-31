from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

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
from solver.heave_solver import HeaveSolver
from solver.legality_engine import LegalValidation, validate_solution_legality
from solver.modifiers import SolverModifiers
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
    valid_pairs = getattr(
        getattr(car, "garage_ranges", None), "diff_coast_drive_ramp_options",
        [(40, 65), (45, 70), (50, 75)],
    )
    best = min(valid_pairs, key=lambda p: abs(p[0] - coast) + abs(p[1] - drive))
    supporting.diff_ramp_coast = best[0]
    supporting.diff_ramp_drive = best[1]
    supporting.diff_ramp_option_idx = diff_ramp_option_index(
        car,
        coast=supporting.diff_ramp_coast,
        drive=supporting.diff_ramp_drive,
        default=1,
    ) or 1
    supporting.diff_ramp_angles = diff_ramp_string_for_option(
        car,
        getattr(supporting, "diff_ramp_option_idx", 1),
        ferrari_label=getattr(car, "canonical_name", "") == "ferrari",
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
                    ferrari_label=getattr(car, "canonical_name", "") == "ferrari",
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


def _run_sequential_solver(inputs: SolveChainInputs) -> tuple[Any, Any, Any, Any, Any, Any, float]:
    mods = _default_modifiers(inputs.modifiers)
    car = inputs.car
    track = inputs.track
    measured = inputs.measured
    fuel = inputs.fuel_load_l

    rake_solver = RakeSolver(car, inputs.surface, track)
    step1 = rake_solver.solve(
        target_balance=inputs.target_balance,
        balance_tolerance=inputs.balance_tolerance,
        fuel_load_l=fuel,
        pin_front_min=inputs.pin_front_min,
    )

    heave_solver = HeaveSolver(car, track)
    _k_current = getattr(inputs.current_setup, "front_heave_nmm", None) if inputs.current_setup else None
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
    )

    corner_solver = CornerSpringSolver(car, track)
    step3 = corner_solver.solve(
        front_heave_nmm=step2.front_heave_nmm,
        rear_third_nmm=step2.rear_third_nmm,
        fuel_load_l=fuel,
    )
    rear_wheel_rate_nmm = step3.rear_spring_rate_nmm * car.corner_spring.rear_motion_ratio ** 2

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

    damper_solver = DamperSolver(car, track)
    provisional_step6 = damper_solver.solve(
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
        fuel_load_l=fuel,
        front_camber_deg=_front_camber(inputs),
        front_hs_damper_nsm=provisional_step6.c_hs_front,
        rear_hs_damper_nsm=provisional_step6.c_hs_rear,
        measured=inputs.measured,
        front_heave_current_nmm=_k_current,
    )
    step3 = corner_solver.solve(
        front_heave_nmm=step2.front_heave_nmm,
        rear_third_nmm=step2.rear_third_nmm,
        fuel_load_l=fuel,
    )
    rear_wheel_rate_nmm = step3.rear_spring_rate_nmm * car.corner_spring.rear_motion_ratio ** 2
    heave_solver.reconcile_solution(
        step1,
        step2,
        step3,
        fuel_load_l=fuel,
        front_camber_deg=_front_camber(inputs),
        front_hs_damper_nsm=provisional_step6.c_hs_front,
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

    arb_solver = ARBSolver(car, track)
    _current_rear_arb = getattr(inputs.current_setup, "rear_arb_size", None) if inputs.current_setup else None
    step4 = arb_solver.solve(
        front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
        rear_wheel_rate_nmm=rear_wheel_rate_nmm,
        lltd_offset=mods.lltd_offset,
        current_rear_arb_size=_current_rear_arb,
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
    return step1, step2, step3, step4, step5, step6, rear_wheel_rate_nmm


def run_base_solve(inputs: SolveChainInputs) -> SolveChainResult:
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
        step1, step2, step3, step4, step5, step6, _rear_wheel_rate = _run_sequential_solver(inputs)
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
            notes.append(
                "Selected sequential fallback." if optimized is not None else "Selected sequential solver path."
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
        heave_solver = HeaveSolver(car, track)
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
            "rear_spring_rate_nmm": internal_solver_value(car, "rear_spring_rate_nmm", step3_targets["rear_spring_rate_nmm"]),
            "rear_spring_perch_mm": step3_targets["rear_spring_perch_mm"],
            "rear_torsion_od_mm": (
                internal_solver_value(car, "rear_spring_rate_nmm", step3_targets["rear_torsion_od_mm"])
                if step3_targets["rear_torsion_od_mm"] is not None
                else None
            ),
        }

        explicit_step2 = bool(overrides.step2 or overrides.step3)
        if explicit_step2:
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
            )
        else:
            step3 = corner_solver.solve(
                front_heave_nmm=step2.front_heave_nmm,
                rear_third_nmm=step2.rear_third_nmm,
                fuel_load_l=inputs.fuel_load_l,
            )
        rear_wheel_rate_nmm = step3.rear_spring_rate_nmm * car.corner_spring.rear_motion_ratio ** 2
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
        provisional_step6 = damper_solver.solve(
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

        if explicit_step2:
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
                front_hs_damper_nsm=provisional_step6.c_hs_front,
                rear_hs_damper_nsm=provisional_step6.c_hs_rear,
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
                front_hs_damper_nsm=provisional_step6.c_hs_front,
                rear_hs_damper_nsm=provisional_step6.c_hs_rear,
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
            )
        else:
            step3 = corner_solver.solve(
                front_heave_nmm=step2.front_heave_nmm,
                rear_third_nmm=step2.rear_third_nmm,
                fuel_load_l=inputs.fuel_load_l,
            )
        rear_wheel_rate_nmm = step3.rear_spring_rate_nmm * car.corner_spring.rear_motion_ratio ** 2
        heave_solver.reconcile_solution(
            step1,
            step2,
            step3,
            fuel_load_l=inputs.fuel_load_l,
            front_camber_deg=_front_camber(inputs),
            front_hs_damper_nsm=provisional_step6.c_hs_front,
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
        rear_wheel_rate_nmm = step3.rear_spring_rate_nmm * car.corner_spring.rear_motion_ratio ** 2

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
            step4 = arb_solver.solve(
                front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
                rear_wheel_rate_nmm=rear_wheel_rate_nmm,
                lltd_offset=mods.lltd_offset,
                current_rear_arb_size=_current_rear_arb,
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
