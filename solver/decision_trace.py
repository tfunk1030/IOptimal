from __future__ import annotations

from typing import Any

from analyzer.telemetry_truth import ParameterDecision, ParameterEvidence, get_signal
from solver.bmw_coverage import (
    build_parameter_coverage,
    parameter_classification,
    required_signals_for_field,
)


def _avg_confidence(measured: Any, signal_names: list[str], *, allow_proxy: bool = True) -> float:
    confidences: list[float] = []
    for name in signal_names:
        signal = get_signal(measured, name)
        if signal.usable(allow_proxy=allow_proxy):
            confidences.append(signal.confidence)
    if not confidences:
        return 0.0
    return round(sum(confidences) / len(confidences), 3)


def _telemetry_lines(measured: Any, signal_names: list[str], *, allow_proxy: bool = True) -> list[str]:
    lines: list[str] = []
    for name in signal_names:
        signal = get_signal(measured, name)
        if signal.usable(allow_proxy=allow_proxy):
            lines.append(f"{name}={signal.value}")
        elif signal.invalid_reason:
            lines.append(f"{name}=unavailable ({signal.invalid_reason})")
    return lines


def _estimate_gain_ms(parameter: str, measured: Any) -> float:
    if parameter in {"front_heave_spring_nmm", "front_heave_perch_mm"}:
        heave = getattr(measured, "front_heave_travel_used_pct", 0.0) or 0.0
        bottoming = getattr(measured, "bottoming_event_count_front_clean", 0) or 0
        return round(max(0.0, (heave - 85.0) * 1.5) + max(0.0, bottoming) * 8.0, 1)
    if parameter in {"rear_third_spring_nmm", "rear_third_perch_mm"}:
        bottoming = getattr(measured, "bottoming_event_count_rear_clean", 0) or 0
        slip = getattr(measured, "rear_power_slip_ratio_p95", 0.0) or 0.0
        return round(max(0.0, bottoming) * 6.0 + max(0.0, (slip - 0.08) * 4000.0), 1)
    if parameter in {"front_camber_deg", "rear_camber_deg", "front_toe_mm", "rear_toe_mm"}:
        return round(abs(getattr(measured, "understeer_mean_deg", 0.0) or 0.0) * 35.0, 1)
    if parameter in {
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
    }:
        settle = getattr(measured, "front_rh_settle_time_ms", 125.0) or 125.0
        return round(abs(settle - 125.0) * 0.4, 1)
    if parameter in {"brake_bias_pct", "brake_bias_target", "brake_bias_migration"}:
        front_lock = getattr(measured, "front_braking_lock_ratio_p95", 0.0) or 0.0
        return round(max(0.0, front_lock - 0.06) * 2000.0, 1)
    if parameter in {
        "diff_preload_nm",
        "diff_ramp_option_idx",
        "diff_clutch_plates",
        "tc_gain",
        "tc_slip",
        "front_master_cyl_mm",
        "rear_master_cyl_mm",
        "pad_compound",
    }:
        slip = getattr(measured, "rear_power_slip_ratio_p95", 0.0) or 0.0
        return round(max(0.0, slip - 0.08) * 1800.0, 1)
    return 0.0


def _estimate_cost_ms(parameter: str, proposed_value: Any, current_value: Any) -> float:
    try:
        delta = abs(float(proposed_value) - float(current_value))
    except (TypeError, ValueError):
        delta = 0.0 if proposed_value == current_value else 1.0
    if parameter in {"front_heave_spring_nmm", "rear_third_spring_nmm"}:
        return round(delta * 0.3, 1)
    if parameter in {
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
    }:
        return round(delta * 2.0, 1)
    if parameter in {"front_camber_deg", "rear_camber_deg"}:
        return round(delta * 8.0, 1)
    return round(delta * 0.5, 1)


def _legacy_parameter_spec(car_name: str) -> list[dict[str, Any]]:
    is_ferrari = car_name.lower() == "ferrari"
    rear_heave_label = "rear_third_nmm" if not is_ferrari else "rear_heave_index"
    rear_spring_label = "rear_spring_nmm" if not is_ferrari else "rear_torsion_bar_index"
    return [
        {
            "parameter": "front_pushrod_mm",
            "current": lambda cs, *_: cs.front_pushrod_mm,
            "proposed": lambda _cs, step1, *_: step1.front_pushrod_offset_mm,
            "unit": "mm",
            "signals": ["front_rh_std_mm", "splitter_rh_p01_mm"],
            "rationale": "Pushrod move repositions the front static platform while preserving legal garage correlation.",
        },
        {
            "parameter": "rear_pushrod_mm",
            "current": lambda cs, *_: cs.rear_pushrod_mm,
            "proposed": lambda _cs, step1, *_: step1.rear_pushrod_offset_mm,
            "unit": "mm",
            "signals": ["rear_rh_std_mm", "rear_power_slip_ratio_p95"],
            "rationale": "Rear pushrod move changes static rake support and rear platform stability.",
        },
        {
            "parameter": "front_heave_nmm" if not is_ferrari else "front_heave_index",
            "current": lambda cs, *_: cs.front_heave_nmm,
            "proposed": lambda _cs, _s1, step2, *_: step2.front_heave_nmm,
            "unit": "N/mm" if not is_ferrari else "idx",
            "signals": ["front_heave_travel_used_pct", "bottoming_event_count_front_clean", "front_rh_std_mm"],
            "rationale": "Front heave rate is driven by travel margin, clean bottoming, and aero-platform control.",
        },
        {
            "parameter": "front_heave_perch_mm",
            "current": lambda cs, *_: cs.front_heave_perch_mm,
            "proposed": lambda _cs, _s1, step2, *_: step2.perch_offset_front_mm,
            "unit": "mm",
            "signals": ["front_heave_travel_used_pct", "bottoming_event_count_front_clean"],
            "rationale": "Front heave perch sets the static slider position and available heave travel.",
        },
        {
            "parameter": rear_heave_label,
            "current": lambda cs, *_: cs.rear_third_nmm,
            "proposed": lambda _cs, _s1, step2, *_: step2.rear_third_nmm,
            "unit": "N/mm" if not is_ferrari else "idx",
            "signals": ["bottoming_event_count_rear_clean", "rear_power_slip_ratio_p95", "rear_rh_std_mm"],
            "rationale": "Rear heave support is a tradeoff between rear platform control and exit traction compliance.",
        },
        {
            "parameter": "front_torsion_od_mm" if not is_ferrari else "front_torsion_bar_index",
            "current": lambda cs, *_: cs.front_torsion_od_mm,
            "proposed": lambda _cs, _s1, _s2, step3, *_: step3.front_torsion_od_mm,
            "unit": "mm" if not is_ferrari else "idx",
            "signals": ["understeer_low_speed_deg", "body_slip_p95_deg"],
            "rationale": "Front wheel rate changes are justified by low-speed balance and body-slip stability.",
        },
        {
            "parameter": rear_spring_label,
            "current": lambda cs, *_: cs.rear_spring_nmm,
            "proposed": lambda _cs, _s1, _s2, step3, *_: step3.rear_spring_rate_nmm,
            "unit": "N/mm" if not is_ferrari else "idx",
            "signals": ["rear_power_slip_ratio_p95", "understeer_low_speed_deg"],
            "rationale": "Rear wheel support balances traction retention against low-speed rotation support.",
        },
        {
            "parameter": "front_camber_deg",
            "current": lambda cs, *_: cs.front_camber_deg,
            "proposed": lambda _cs, _s1, _s2, _s3, _s4, step5, *_: step5.front_camber_deg,
            "unit": "deg",
            "signals": ["understeer_mean_deg", "front_carcass_mean_c", "front_pressure_mean_kpa"],
            "rationale": "Front camber is tied to contact patch shape, carcass temperature, and understeer support.",
        },
        {
            "parameter": "rear_camber_deg",
            "current": lambda cs, *_: cs.rear_camber_deg,
            "proposed": lambda _cs, _s1, _s2, _s3, _s4, step5, *_: step5.rear_camber_deg,
            "unit": "deg",
            "signals": ["rear_power_slip_ratio_p95", "rear_carcass_mean_c", "rear_pressure_mean_kpa"],
            "rationale": "Rear camber balances traction stability against tyre support and heat distribution.",
        },
        {
            "parameter": "brake_bias_pct",
            "current": lambda cs, *_: cs.brake_bias_pct,
            "proposed": lambda _cs, *_tail: _tail[-1].brake_bias_pct,
            "unit": "%",
            "signals": ["front_braking_lock_ratio_p95", "pitch_range_braking_deg"],
            "rationale": "Brake bias is adjusted from braking lock evidence and braking-platform stability.",
        },
        {
            "parameter": "diff_preload_nm",
            "current": lambda cs, *_: cs.diff_preload_nm,
            "proposed": lambda _cs, *_tail: _tail[-1].diff_preload_nm,
            "unit": "Nm",
            "signals": ["rear_power_slip_ratio_p95", "understeer_low_speed_deg"],
            "rationale": "Diff preload is set from exit traction demand and rotation stability.",
        },
        {
            "parameter": "tc_gain",
            "current": lambda cs, *_: cs.tc_gain,
            "proposed": lambda _cs, *_tail: _tail[-1].tc_gain,
            "unit": "",
            "signals": ["rear_power_slip_ratio_p95"],
            "rationale": "TC gain follows measured rear power-slip demand.",
        },
        {
            "parameter": "tc_slip",
            "current": lambda cs, *_: cs.tc_slip,
            "proposed": lambda _cs, *_tail: _tail[-1].tc_slip,
            "unit": "",
            "signals": ["rear_power_slip_ratio_p95"],
            "rationale": "TC slip target tracks the measured rear power-slip envelope.",
        },
    ]


def _append_pass_through_decision(
    decisions: list[ParameterDecision],
    *,
    parameter: str,
    current_value: Any,
    proposed_value: Any,
    unit: str,
    rationale: str,
    legality_text: str,
    fallback_text: str,
) -> None:
    decisions.append(
        ParameterDecision(
            parameter=parameter,
            current_value=current_value,
            proposed_value=proposed_value,
            unit=unit,
            confidence=0.0,
            legality_status="pass_through",
            fallback_reason=fallback_text,
            evidence=ParameterEvidence(
                telemetry=[],
                physics_rationale=rationale,
                legality=legality_text,
                expected_gain_ms=0.0,
                expected_cost_ms=0.0,
                confidence=0.0,
                source_tier="pass_through",
            ),
        )
    )


def _legacy_build_parameter_decisions(
    *,
    car_name: str,
    current_setup: Any,
    measured: Any,
    step1: Any,
    step2: Any,
    step3: Any,
    step4: Any,
    step5: Any,
    step6: Any,
    supporting: Any,
    legality: Any | None = None,
    fallback_reasons: list[str] | None = None,
) -> list[ParameterDecision]:
    decisions: list[ParameterDecision] = []
    fallback_text = "; ".join(fallback_reasons or [])
    legality_text = "garage validated"
    if legality is not None:
        legality_text = "garage validated" if legality.valid else "garage validation warning"

    for spec in _legacy_parameter_spec(car_name):
        current_value = spec["current"](current_setup, step1, step2, step3, step4, step5, supporting)
        proposed_value = spec["proposed"](current_setup, step1, step2, step3, step4, step5, supporting)
        if current_value == proposed_value:
            continue
        signal_names = list(spec["signals"])
        confidence = _avg_confidence(measured, signal_names)
        decisions.append(
            ParameterDecision(
                parameter=spec["parameter"],
                current_value=current_value,
                proposed_value=proposed_value,
                unit=spec["unit"],
                confidence=confidence,
                legality_status="validated" if legality is None or legality.valid else "warning",
                fallback_reason=fallback_text,
                evidence=ParameterEvidence(
                    telemetry=_telemetry_lines(measured, signal_names),
                    physics_rationale=spec["rationale"],
                    legality=legality_text,
                    expected_gain_ms=_estimate_gain_ms(spec["parameter"], measured),
                    expected_cost_ms=_estimate_cost_ms(spec["parameter"], proposed_value, current_value),
                    confidence=confidence,
                    source_tier="telemetry",
                ),
            )
        )

    _append_pass_through_decision(
        decisions,
        parameter="brake_bias_target",
        current_value=getattr(current_setup, "brake_bias_target", None),
        proposed_value=getattr(supporting, "brake_bias_target", None),
        unit="",
        rationale="Brake bias target is passed through from hardware/session context because the solver only solves static brake bias.",
        legality_text=legality_text,
        fallback_text=fallback_text,
    )
    _append_pass_through_decision(
        decisions,
        parameter="brake_bias_migration",
        current_value=getattr(current_setup, "brake_bias_migration", None),
        proposed_value=getattr(supporting, "brake_bias_migration", None),
        unit="",
        rationale="Brake migration is carried forward as hardware context; no migration model is currently solved by the pipeline.",
        legality_text=legality_text,
        fallback_text=fallback_text,
    )
    _append_pass_through_decision(
        decisions,
        parameter="front_master_cyl_mm",
        current_value=getattr(current_setup, "front_master_cyl_mm", None),
        proposed_value=getattr(supporting, "front_master_cyl_mm", None),
        unit="mm",
        rationale="Front master cylinder sizing is treated as pass-through hardware context rather than a solved setup output.",
        legality_text=legality_text,
        fallback_text=fallback_text,
    )
    _append_pass_through_decision(
        decisions,
        parameter="rear_master_cyl_mm",
        current_value=getattr(current_setup, "rear_master_cyl_mm", None),
        proposed_value=getattr(supporting, "rear_master_cyl_mm", None),
        unit="mm",
        rationale="Rear master cylinder sizing is treated as pass-through hardware context rather than a solved setup output.",
        legality_text=legality_text,
        fallback_text=fallback_text,
    )
    _append_pass_through_decision(
        decisions,
        parameter="pad_compound",
        current_value=getattr(current_setup, "pad_compound", None),
        proposed_value=getattr(supporting, "pad_compound", None),
        unit="",
        rationale="Pad compound is reported honestly as pass-through brake hardware context, not as a solved telemetry output.",
        legality_text=legality_text,
        fallback_text=fallback_text,
    )

    if car_name.lower() == "ferrari":
        warnings = list(getattr(current_setup, "decode_warnings", []) or [])
        for warning in warnings[:3]:
            decisions.append(
                ParameterDecision(
                    parameter="ferrari_adapter_warning",
                    current_value=None,
                    proposed_value=None,
                    confidence=0.0,
                    legality_status="blocked",
                    blocked_reason=warning,
                    fallback_reason="Ferrari indexed engineering decode remains partial; raw legal values preserved.",
                    evidence=ParameterEvidence(
                        telemetry=[],
                        physics_rationale="Unsupported Ferrari engineering-unit decode is blocked instead of backfilled with BMW defaults.",
                        legality=legality_text,
                        expected_gain_ms=0.0,
                        expected_cost_ms=0.0,
                        confidence=0.0,
                        source_tier="adapter",
                    ),
                )
            )
    return decisions


def _supporting_status(parameter: str, supporting: Any) -> str:
    mapping = {
        "brake_bias_pct": "brake_bias_status",
        "brake_bias_target": "brake_bias_target_status",
        "brake_bias_migration": "brake_bias_migration_status",
        "front_master_cyl_mm": "master_cylinder_status",
        "rear_master_cyl_mm": "master_cylinder_status",
        "pad_compound": "pad_compound_status",
    }
    attr = mapping.get(parameter, "")
    return str(getattr(supporting, attr, "") or "")


def _parameter_rationale(parameter: str, classification: str, status: str) -> str:
    if classification == "local_refine":
        return "Local refinement keeps the perch/slider position legal and physically correlated after the coarse spring search."
    if classification == "deterministic_context":
        return "This field is preserved or derived deterministically from session context instead of lap-time search."
    if classification == "computed_display":
        return "This display/export field is derived from the canonical searched control to keep JSON and .sto output aligned."
    if parameter in {"front_arb_size", "rear_arb_size"}:
        return "ARB size is surfaced because blade range and available LLTD span both matter on the legal BMW manifold."
    if parameter in {"front_arb_blade", "rear_arb_blade"}:
        return "ARB blade is driven by balance and LLTD evidence from low-speed and high-speed handling."
    if parameter in {"diff_ramp_option_idx", "diff_ramp_angles"}:
        return "Diff ramp is modelled as one coupled legal BMW ramp pair, so the option index is the canonical searched control."
    if parameter in {"diff_clutch_plates", "diff_preload_nm", "tc_gain", "tc_slip"}:
        return "Supporting traction controls are adjusted from exit-slip and rotation evidence."
    if parameter in {"brake_bias_target", "brake_bias_migration", "front_master_cyl_mm", "rear_master_cyl_mm", "pad_compound"}:
        if status.startswith("seeded_from_telemetry"):
            return "Brake hardware is conservatively seeded from braking telemetry instead of being left as silent pass-through context."
        if status.startswith("seeded_from_setup"):
            return "Brake hardware remains legal seeded context when telemetry does not justify a change."
        return "Brake hardware is surfaced explicitly so JSON/export output matches the final BMW setup state."
    if parameter.startswith("front_") or parameter.startswith("rear_"):
        return "The solver uses telemetry-backed state evidence to move this control on the legal garage manifold."
    return "The solver uses telemetry-backed state evidence to move this control on the legal garage manifold."


def _decision_confidence(classification: str, measured: Any, signal_names: list[str], status: str) -> float:
    if classification == "deterministic_context":
        return 1.0
    if classification == "computed_display":
        return 1.0
    confidence = _avg_confidence(measured, signal_names)
    if status.startswith("seeded_from_telemetry"):
        return max(confidence, 0.45)
    if status.startswith("seeded_from_setup"):
        return max(confidence, 0.25)
    return confidence


def _legality_status(classification: str, legality: Any | None) -> str:
    if classification == "deterministic_context":
        return "context"
    if classification == "computed_display":
        return "derived"
    return "validated" if legality is None or getattr(legality, "valid", True) else "warning"


def _source_tier(classification: str, status: str) -> str:
    if classification == "local_refine":
        return "local_refine"
    if classification == "deterministic_context":
        return "deterministic_context"
    if classification == "computed_display":
        return "computed_display"
    if status:
        return status
    return "telemetry"


def build_parameter_decisions(
    *,
    car_name: str,
    current_setup: Any,
    measured: Any,
    step1: Any,
    step2: Any,
    step3: Any,
    step4: Any,
    step5: Any,
    step6: Any,
    supporting: Any,
    legality: Any | None = None,
    fallback_reasons: list[str] | None = None,
) -> list[ParameterDecision]:
    if car_name.lower() != "bmw":
        return _legacy_build_parameter_decisions(
            car_name=car_name,
            current_setup=current_setup,
            measured=measured,
            step1=step1,
            step2=step2,
            step3=step3,
            step4=step4,
            step5=step5,
            step6=step6,
            supporting=supporting,
            legality=legality,
            fallback_reasons=fallback_reasons,
        )

    decisions: list[ParameterDecision] = []
    fallback_text = "; ".join(fallback_reasons or [])
    legality_text = "garage validated"
    if legality is not None:
        legality_text = "garage validated" if legality.valid else "garage validation warning"

    coverage = build_parameter_coverage(
        car=car_name,
        wing=None,
        current_setup=current_setup,
        step1=step1,
        step2=step2,
        step3=step3,
        step4=step4,
        step5=step5,
        step6=step6,
        supporting=supporting,
    )
    for parameter, entry in coverage.items():
        if not entry["changed"]:
            continue
        classification = parameter_classification(parameter)
        signal_names = required_signals_for_field(parameter)
        status = _supporting_status(parameter, supporting)
        confidence = _decision_confidence(classification, measured, signal_names, status)
        decisions.append(
            ParameterDecision(
                parameter=parameter,
                current_value=entry["current_value"],
                proposed_value=entry["proposed_value"],
                unit=entry["unit"],
                confidence=confidence,
                legality_status=_legality_status(classification, legality),
                search_status=str(entry.get("search_status", "") or ""),
                fallback_reason=fallback_text,
                evidence=ParameterEvidence(
                    telemetry=_telemetry_lines(measured, signal_names),
                    physics_rationale=_parameter_rationale(parameter, classification, status),
                    legality=legality_text,
                    expected_gain_ms=_estimate_gain_ms(parameter, measured),
                    expected_cost_ms=_estimate_cost_ms(parameter, entry["proposed_value"], entry["current_value"]),
                    confidence=confidence,
                    source_tier=_source_tier(classification, status),
                ),
            )
        )
    return decisions
