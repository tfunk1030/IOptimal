from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any

import numpy as np

from analyzer.stint_analysis import StintDataset, StintLapState
from car_model.setup_registry import get_numeric_resolution
from solver.candidate_search import _extract_target_maps, _snap_targets_to_garage, _target_overrides
from solver.solve_chain import SolveChainInputs, SolveChainResult, materialize_overrides, run_base_solve


@dataclass
class StintObjectiveConfig:
    late_lap_progress_gain: float = 0.25
    max_weight: float = 0.50
    p90_weight: float = 0.30
    mean_weight: float = 0.20


@dataclass
class LapPenalty:
    lap_number: int
    phase: str
    progress: float
    base_weight: float
    penalty: float
    weighted_penalty: float
    source_label: str = ""


@dataclass
class StintSolveResult:
    dataset: StintDataset
    result: SolveChainResult
    objective: dict[str, float]
    lap_penalties: list[LapPenalty] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    fallback_mode: str | None = None
    confidence: float = 0.0
    phase_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)


_FIELD_SCALE: dict[str, float] = {
    "front_pushrod_offset_mm": 0.5,
    "rear_pushrod_offset_mm": 0.5,
    "front_heave_nmm": 20.0,
    "rear_third_nmm": 20.0,
    "perch_offset_front_mm": 0.5,
    "perch_offset_rear_mm": 1.0,
    "front_torsion_od_mm": 0.25,
    "rear_spring_rate_nmm": 5.0,
    "rear_spring_perch_mm": 0.5,
    "front_arb_size": 1.0,
    "rear_arb_size": 1.0,
    "front_arb_blade_start": 1.0,
    "rear_arb_blade_start": 1.0,
    "rarb_blade_slow_corner": 1.0,
    "rarb_blade_fast_corner": 1.0,
    "front_camber_deg": 0.1,
    "rear_camber_deg": 0.1,
    "front_toe_mm": 0.1,
    "rear_toe_mm": 0.1,
    "front_ls_comp": 1.0,
    "front_ls_rbd": 1.0,
    "front_hs_comp": 1.0,
    "front_hs_rbd": 1.0,
    "front_hs_slope": 1.0,
    "rear_ls_comp": 1.0,
    "rear_ls_rbd": 1.0,
    "rear_hs_comp": 1.0,
    "rear_hs_rbd": 1.0,
    "rear_hs_slope": 1.0,
    "ls_comp": 1.0,
    "ls_rbd": 1.0,
    "hs_comp": 1.0,
    "hs_rbd": 1.0,
    "hs_slope": 1.0,
    "brake_bias_pct": 0.3,
    "brake_bias_target": 0.5,
    "brake_bias_migration": 0.5,
    "front_master_cyl_mm": 0.9,
    "rear_master_cyl_mm": 0.9,
    "pad_compound": 1.0,
    "diff_preload_nm": 5.0,
    "diff_ramp_option_idx": 1.0,
    "diff_clutch_plates": 2.0,
    "tc_gain": 1.0,
    "tc_slip": 1.0,
}

_SUPPORT_HIGHER_IS_SAFER = {
    "front_pushrod_offset_mm",
    "rear_pushrod_offset_mm",
    "front_heave_nmm",
    "rear_third_nmm",
    "front_hs_comp",
    "rear_hs_comp",
    "front_hs_slope",
    "rear_hs_slope",
    "hs_comp",
    "hs_slope",
}

_IGNORE_SUPPORTING_FIELDS = {
    "fuel_l",
    "fuel_low_warning_l",
    "fuel_target_l",
    "gear_stack",
    "roof_light_color",
    "diff_ramp_angles",
    "hybrid_rear_drive_enabled",
    "hybrid_rear_drive_corner_pct",
    "brake_bias_migration_gain",
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _numeric(values: list[Any]) -> bool:
    return all(isinstance(value, (int, float)) for value in values if value is not None)


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    if not values:
        return 0.0
    if not weights or sum(weights) <= 0:
        return float(np.mean(values))
    return float(np.average(values, weights=weights))


def _weighted_quantile(values: list[float], weights: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    if not weights or sum(weights) <= 0:
        return float(np.quantile(values, quantile))
    order = np.argsort(values)
    sorted_values = np.asarray(values)[order]
    sorted_weights = np.asarray(weights)[order]
    cumulative = np.cumsum(sorted_weights)
    cutoff = quantile * cumulative[-1]
    idx = int(np.searchsorted(cumulative, cutoff, side="left"))
    idx = max(0, min(len(sorted_values) - 1, idx))
    return float(sorted_values[idx])


def _weighted_mode(values: list[Any], weights: list[float]) -> Any:
    if not values:
        return None
    totals: dict[Any, float] = {}
    for value, weight in zip(values, weights or [1.0] * len(values)):
        totals[value] = totals.get(value, 0.0) + float(weight)
    return max(totals.items(), key=lambda item: item[1])[0]


def _field_multiplier(field_name: str, lap: StintLapState) -> float:
    measured = lap.measured
    front_support = max(
        0.0,
        (_safe_float(getattr(measured, "front_heave_travel_used_pct", 0.0)) - 78.0) / 12.0,
        (_safe_float(getattr(measured, "pitch_range_braking_deg", 0.0)) - 1.2) / 0.8,
        _safe_float(getattr(measured, "bottoming_event_count_front_clean", 0.0)) / 4.0,
    )
    rear_support = max(
        0.0,
        (_safe_float(getattr(measured, "rear_heave_travel_used_pct", 0.0)) - 78.0) / 12.0,
        (_safe_float(getattr(measured, "rear_rh_std_mm", 0.0)) - 8.5) / 3.0,
        _safe_float(getattr(measured, "bottoming_event_count_rear_clean", 0.0)) / 4.0,
    )
    entry_understeer = max(0.0, (_safe_float(getattr(measured, "understeer_low_speed_deg", 0.0)) - 0.9) / 0.8)
    high_speed_understeer = max(
        0.0,
        (_safe_float(getattr(measured, "understeer_high_speed_deg", 0.0)) - max(_safe_float(getattr(measured, "understeer_low_speed_deg", 0.0)), 0.8)) / 0.8,
    )
    traction = max(
        0.0,
        (_safe_float(getattr(measured, "rear_power_slip_ratio_p95", getattr(measured, "rear_slip_ratio_p95", 0.0))) - 0.07) / 0.05,
        (_safe_float(getattr(measured, "body_slip_p95_deg", 0.0)) - 3.1) / 2.2,
    )
    braking = max(
        0.0,
        (_safe_float(getattr(measured, "front_braking_lock_ratio_p95", 0.0)) - 0.065) / 0.05,
        (_safe_float(getattr(measured, "abs_active_pct", 0.0)) - 10.0) / 12.0,
    )
    thermal = max(
        0.0,
        (_safe_float(getattr(measured, "front_carcass_mean_c", 0.0)) - 95.0) / 6.0,
        (_safe_float(getattr(measured, "rear_carcass_mean_c", 0.0)) - 95.0) / 6.0,
        (_safe_float(getattr(measured, "front_pressure_mean_kpa", 0.0)) - 169.0) / 6.0,
        (_safe_float(getattr(measured, "rear_pressure_mean_kpa", 0.0)) - 169.0) / 6.0,
    )
    if field_name in {"front_pushrod_offset_mm", "front_heave_nmm", "perch_offset_front_mm"}:
        return 1.0 + front_support
    if field_name in {"rear_pushrod_offset_mm", "rear_third_nmm", "perch_offset_rear_mm", "rear_spring_rate_nmm", "rear_spring_perch_mm"}:
        return 1.0 + rear_support + traction * 0.4
    if field_name in {"front_arb_size", "front_arb_blade_start", "front_camber_deg", "front_toe_mm"}:
        return 1.0 + max(entry_understeer, high_speed_understeer) + thermal * 0.25
    if field_name in {"rear_arb_size", "rear_arb_blade_start", "rarb_blade_slow_corner", "rarb_blade_fast_corner", "rear_camber_deg", "rear_toe_mm"}:
        return 1.0 + max(traction, entry_understeer * 0.5)
    if field_name in {"brake_bias_pct", "brake_bias_target", "brake_bias_migration", "front_master_cyl_mm", "rear_master_cyl_mm", "pad_compound"}:
        return 1.0 + braking
    if field_name in {"diff_preload_nm", "diff_ramp_option_idx", "diff_clutch_plates", "tc_gain", "tc_slip"}:
        return 1.0 + traction
    if field_name in {
        "front_ls_comp",
        "front_ls_rbd",
        "front_hs_comp",
        "front_hs_rbd",
        "front_hs_slope",
        "rear_ls_comp",
        "rear_ls_rbd",
        "rear_hs_comp",
        "rear_hs_rbd",
        "rear_hs_slope",
        "ls_comp",
        "ls_rbd",
        "hs_comp",
        "hs_rbd",
        "hs_slope",
    }:
        return 1.0 + max(front_support, rear_support)
    return 1.0


def _field_scale(field_name: str, car: Any | None = None) -> float:
    if field_name in {"brake_bias_target", "brake_bias_migration"}:
        return float(get_numeric_resolution(car, field_name, default=_FIELD_SCALE.get(field_name, 1.0)) or 1.0)
    return _FIELD_SCALE.get(field_name, 1.0)


def _distance(field_name: str, candidate: Any, ideal: Any, *, car: Any | None = None) -> float:
    if candidate == ideal:
        return 0.0
    if isinstance(candidate, str) or isinstance(ideal, str):
        return 1.0 if candidate != ideal else 0.0
    try:
        candidate_val = float(candidate)
        ideal_val = float(ideal)
    except (TypeError, ValueError):
        return 1.0
    scale = _field_scale(field_name, car)
    distance = abs(candidate_val - ideal_val) / max(scale, 1e-6)
    if field_name in _SUPPORT_HIGHER_IS_SAFER:
        if candidate_val < ideal_val:
            distance *= 1.35
        else:
            distance *= 0.7
    return distance


def _flatten_targets(targets: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for step_name, mapping in targets.items():
        if step_name == "step6":
            for corner_name, corner_fields in mapping.items():
                axle = "front" if corner_name in {"lf", "rf"} else "rear"
                for field_name, value in corner_fields.items():
                    flat[f"{axle}_{field_name}"] = value
        elif step_name == "step4_arb_size":
            for field_name, value in mapping.items():
                flat[field_name] = value
        else:
            for field_name, value in mapping.items():
                flat[field_name] = value
    return flat


def _aggregate_step_mapping(values: list[tuple[Any, float]], *, use_p90: bool = False, safety_field: bool = False) -> Any:
    filtered = [(value, weight) for value, weight in values if value is not None]
    if not filtered:
        return None
    raw_values = [value for value, _ in filtered]
    raw_weights = [weight for _, weight in filtered]
    if _numeric(raw_values):
        numeric_values = [float(value) for value in raw_values]
        if safety_field:
            return _weighted_quantile(numeric_values, raw_weights, 0.90 if use_p90 else 0.75)
        return _weighted_quantile(numeric_values, raw_weights, 0.65 if use_p90 else 0.50) if use_p90 else _weighted_mean(numeric_values, raw_weights)
    return _weighted_mode(raw_values, raw_weights)


def _lap_weight(lap: StintLapState, config: StintObjectiveConfig) -> float:
    progress_weight = 1.0 + config.late_lap_progress_gain * float(lap.progress)
    return progress_weight * max(0.0, float(lap.quality.direct_weight))


def _solve_inputs_for_lap(base_inputs: SolveChainInputs, lap: StintLapState) -> SolveChainInputs:
    fuel_level = _safe_float(getattr(lap.measured, "fuel_level_at_measurement_l", 0.0), 0.0)
    if fuel_level <= 0:
        fuel_level = base_inputs.fuel_load_l
    return replace(
        base_inputs,
        measured=lap.measured,
        fuel_load_l=fuel_level,
        supporting_measured=lap.measured,
    )


def _candidate_targets(
    base_result: SolveChainResult,
    lap_payloads: list[dict[str, Any]],
    *,
    profile: str,
    car: Any | None = None,
) -> dict[str, Any]:
    targets = _extract_target_maps(base_result)
    if profile == "base":
        return targets

    use_p90 = profile in {"late_safe", "phase_late"}
    late_only = profile == "phase_late"
    for step_name, mapping in list(targets.items()):
        if step_name == "step6":
            for corner_name, corner_fields in mapping.items():
                for field_name in list(corner_fields.keys()):
                    values: list[tuple[Any, float]] = []
                    for payload in lap_payloads:
                        if late_only and payload["lap"].phase != "late":
                            continue
                        target_value = payload["targets"]["step6"][corner_name][field_name]
                        values.append((target_value, payload["weight"]))
                    aggregated = _aggregate_step_mapping(
                        values,
                        use_p90=use_p90,
                        safety_field=field_name in _SUPPORT_HIGHER_IS_SAFER,
                    )
                    if aggregated is not None:
                        corner_fields[field_name] = aggregated
            continue
        if step_name == "step4_arb_size":
            for field_name in list(mapping.keys()):
                values = []
                for payload in lap_payloads:
                    if late_only and payload["lap"].phase != "late":
                        continue
                    values.append((payload["targets"][step_name][field_name], payload["weight"]))
                aggregated = _aggregate_step_mapping(values, use_p90=use_p90)
                if aggregated is not None:
                    mapping[field_name] = aggregated
            continue
        for field_name in list(mapping.keys()):
            if step_name == "supporting" and field_name in _IGNORE_SUPPORTING_FIELDS:
                continue
            values = []
            for payload in lap_payloads:
                if late_only and payload["lap"].phase != "late":
                    continue
                values.append((payload["targets"][step_name][field_name], payload["weight"]))
            safety_field = field_name in _SUPPORT_HIGHER_IS_SAFER
            aggregated = _aggregate_step_mapping(values, use_p90=use_p90, safety_field=safety_field)
            if aggregated is not None:
                mapping[field_name] = aggregated
    _snap_targets_to_garage(targets, car)
    return targets


def _score_candidate(
    candidate_result: SolveChainResult,
    lap_payloads: list[dict[str, Any]],
    config: StintObjectiveConfig,
    *,
    car: Any | None = None,
) -> tuple[dict[str, float], list[LapPenalty]]:
    candidate_flat = _flatten_targets(_extract_target_maps(candidate_result))
    lap_penalties: list[LapPenalty] = []
    for payload in lap_payloads:
        lap = payload["lap"]
        ideal_flat = payload["flat"]
        weight = payload["weight"]
        terms: list[float] = []
        multipliers: list[float] = []
        for field_name, ideal_value in ideal_flat.items():
            if field_name in _IGNORE_SUPPORTING_FIELDS or ideal_value is None:
                continue
            candidate_value = candidate_flat.get(field_name)
            if candidate_value is None:
                continue
            base_name = field_name
            terms.append(_distance(base_name, candidate_value, ideal_value, car=car))
            multipliers.append(_field_multiplier(base_name, lap))
        penalty = 0.0
        if terms:
            penalty = float(np.average(np.asarray(terms), weights=np.asarray(multipliers)))
        weighted = penalty * weight
        lap_penalties.append(
            LapPenalty(
                lap_number=lap.lap_number,
                phase=lap.phase,
                progress=lap.progress,
                base_weight=round(weight, 4),
                penalty=round(penalty, 5),
                weighted_penalty=round(weighted, 5),
                source_label=lap.source_label,
            )
        )
    weighted_values = [penalty.weighted_penalty for penalty in lap_penalties]
    if not weighted_values:
        return {"max": 1.0, "p90": 1.0, "mean": 1.0, "total": 1.0}, lap_penalties
    maximum = float(max(weighted_values))
    p90 = float(np.quantile(weighted_values, 0.90))
    mean = float(np.mean(weighted_values))
    total = config.max_weight * maximum + config.p90_weight * p90 + config.mean_weight * mean
    return {
        "max": round(maximum, 5),
        "p90": round(p90, 5),
        "mean": round(mean, 5),
        "total": round(total, 5),
    }, lap_penalties


def _local_refine(
    *,
    selected_result: SolveChainResult,
    selected_objective: dict[str, float],
    selected_penalties: list[LapPenalty],
    base_result: SolveChainResult,
    base_inputs: SolveChainInputs,
    lap_payloads: list[dict[str, Any]],
    config: StintObjectiveConfig,
) -> tuple[SolveChainResult, dict[str, float], list[LapPenalty], list[str]]:
    notes: list[str] = []
    targets = _extract_target_maps(selected_result)
    refinements = [
        ("step2", "perch_offset_front_mm", [targets["step2"].get("perch_offset_front_mm")]),
        ("step2", "perch_offset_rear_mm", [targets["step2"].get("perch_offset_rear_mm")]),
        ("step3", "rear_spring_perch_mm", [targets["step3"].get("rear_spring_perch_mm")]),
    ]
    best_result = selected_result
    best_objective = dict(selected_objective)
    best_penalties = list(selected_penalties)
    for step_name, field_name, defaults in refinements:
        current_value = defaults[0]
        if current_value is None:
            continue
        trial_values = {current_value}
        step = 0.5 if field_name == "rear_spring_perch_mm" else 1.0
        for delta in (-step, step):
            trial_values.add(round(float(current_value) + delta, 3))
        for payload in lap_payloads:
            trial_target = payload["targets"].get(step_name, {}).get(field_name)
            if trial_target is not None:
                trial_values.add(round(float(trial_target), 3))
        for trial_value in sorted(trial_values):
            candidate_targets = _extract_target_maps(best_result)
            candidate_targets[step_name][field_name] = trial_value
            overrides = _target_overrides(base_result, candidate_targets)
            candidate_result = materialize_overrides(base_result, overrides, base_inputs)
            objective, penalties = _score_candidate(candidate_result, lap_payloads, config, car=base_inputs.car)
            if objective["total"] + 1e-6 < best_objective["total"]:
                best_result = candidate_result
                best_objective = objective
                best_penalties = penalties
                notes.append(
                    f"Local refine improved {field_name} -> {trial_value} (objective {objective['total']:.4f})."
                )
    return best_result, best_objective, best_penalties, notes


def solve_stint_compromise(
    *,
    dataset: StintDataset,
    base_inputs: SolveChainInputs,
    base_result: SolveChainResult | None = None,
    objective_config: StintObjectiveConfig | None = None,
) -> StintSolveResult:
    """Solve one setup compromise against the retained stint laps."""

    config = objective_config or StintObjectiveConfig()
    if base_result is None:
        base_result = run_base_solve(base_inputs)

    if len(dataset.usable_laps) < 5:
        objective, penalties = _score_candidate(base_result, [], config, car=base_inputs.car)
        return StintSolveResult(
            dataset=dataset,
            result=base_result,
            objective=objective,
            lap_penalties=penalties,
            notes=["Stint solve skipped: insufficient usable stint laps, returning single-lap result."],
            fallback_mode="single_lap_insufficient_stint_data",
            confidence=round(dataset.confidence * 0.5, 3),
            phase_summaries=dataset.phase_summaries,
        )

    lap_payloads: list[dict[str, Any]] = []
    for lap in dataset.evaluation_laps:
        try:
            lap_inputs = _solve_inputs_for_lap(base_inputs, lap)
            lap_result = run_base_solve(lap_inputs)
        except Exception:
            continue
        weight = _lap_weight(lap, config)
        targets = _extract_target_maps(lap_result)
        lap_payloads.append(
            {
                "lap": lap,
                "weight": weight,
                "result": lap_result,
                "targets": targets,
                "flat": _flatten_targets(targets),
            }
        )

    if len(lap_payloads) < 2:
        objective, penalties = _score_candidate(base_result, [], config, car=base_inputs.car)
        return StintSolveResult(
            dataset=dataset,
            result=base_result,
            objective=objective,
            lap_penalties=penalties,
            notes=["Stint solve fell back: lap-target solve generation was incomplete."],
            fallback_mode="single_lap_insufficient_stint_data",
            confidence=round(dataset.confidence * 0.55, 3),
            phase_summaries=dataset.phase_summaries,
        )

    profiles = ("base", "balanced_mean", "late_safe", "phase_late")
    scored_candidates: list[tuple[str, SolveChainResult, dict[str, float], list[LapPenalty]]] = []
    for profile in profiles:
        candidate_targets = _candidate_targets(base_result, lap_payloads, profile=profile, car=base_inputs.car)
        overrides = _target_overrides(base_result, candidate_targets)
        candidate_result = materialize_overrides(base_result, overrides, base_inputs)
        objective, penalties = _score_candidate(candidate_result, lap_payloads, config, car=base_inputs.car)
        scored_candidates.append((profile, candidate_result, objective, penalties))

    profile, selected_result, selected_objective, selected_penalties = min(
        scored_candidates,
        key=lambda row: row[2]["total"],
    )
    base_objective = next((objective for candidate_profile, _result, objective, _penalties in scored_candidates if candidate_profile == "base"), None)

    selected_result, selected_objective, selected_penalties, refine_notes = _local_refine(
        selected_result=selected_result,
        selected_objective=selected_objective,
        selected_penalties=selected_penalties,
        base_result=base_result,
        base_inputs=base_inputs,
        lap_payloads=lap_payloads,
        config=config,
    )
    notes = [
        f"Scored {len(scored_candidates)} stint compromise candidates across {len(lap_payloads)} retained laps.",
        f"Selected '{profile}' compromise profile with objective {selected_objective['total']:.4f}.",
        f"Late laps weighted by 1 + {config.late_lap_progress_gain:.2f} * progress.",
    ]
    if base_objective is not None and selected_objective["total"] < base_objective["total"] - 1e-6:
        notes.append(
            f"Worst-case-safe compromise reduced total objective from {base_objective['total']:.4f} to {selected_objective['total']:.4f}."
        )
    notes.extend(refine_notes)
    return StintSolveResult(
        dataset=dataset,
        result=selected_result,
        objective=selected_objective,
        lap_penalties=selected_penalties,
        notes=notes,
        fallback_mode=None,
        confidence=round(min(1.0, dataset.confidence * 0.8 + 0.2), 3),
        phase_summaries=dataset.phase_summaries,
    )


def aggregate_stint_recommendations(datasets: list[StintDataset]) -> list[dict[str, Any]]:
    """Aggregate phase issues across selected stint datasets."""

    stint_count = sum(1 for dataset in datasets if dataset.usable_laps)
    if stint_count == 0:
        return []
    counts: dict[tuple[str, str], int] = {}
    for dataset in datasets:
        seen: set[tuple[str, str]] = set()
        for phase, summary in (dataset.phase_summaries or {}).items():
            for issue in summary.get("issues", []):
                key = (phase, issue)
                if key in seen:
                    continue
                counts[key] = counts.get(key, 0) + 1
                seen.add(key)
    recommendations: list[dict[str, Any]] = []
    for (phase, issue), count in sorted(counts.items(), key=lambda item: (-item[1], item[0][0], item[0][1])):
        ratio = count / max(stint_count, 1)
        if ratio >= 0.60:
            recommendations.append(
                {
                    "phase": phase,
                    "issue": issue,
                    "count": count,
                    "stint_count": stint_count,
                    "ratio": round(ratio, 3),
                }
            )
    return recommendations
