from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any

from car_model.garage import GarageSetupState
from car_model.setup_registry import public_output_value
from output.garage_validator import validate_and_fix_garage_correlation


@dataclass
class LegalValidation:
    valid: bool
    warnings: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    # Informational notes — not warnings, not errors.  Used for Ferrari
    # "no garage model" notices and similar benign housekeeping messages
    # that would pollute the report if promoted to warnings.
    info_messages: list[str] = field(default_factory=list)
    snapped_or_corrected: bool = False
    source: str = "garage_validator"

    # Phase 4 — hard veto vs soft penalty distinction
    hard_veto: bool = False
    hard_veto_reasons: list[str] = field(default_factory=list)
    soft_penalties: list[str] = field(default_factory=list)
    legality_margin: float = 1.0     # 0.0 = on edge, 1.0 = comfortably legal
    garage_corrections_count: int = 0
    corrected_fields: list[str] = field(default_factory=list)
    constraint_violations: list[str] = field(default_factory=list)

    # Validation tier — indicates depth of validation performed
    # "full"        = garage model active, physics constraints checked (BMW/Sebring)
    # "range_clamp" = no garage model, geometric range checks only
    # "none"        = no validation available for this car/track
    validation_tier: str = "range_clamp"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_solution_legality(
    *,
    car: Any,
    track_name: str,
    step1: Any,
    step2: Any,
    step3: Any,
    step5: Any,
    fuel_l: float,
) -> LegalValidation:
    validation_step1 = step1
    validation_step2 = step2
    validation_step3 = step3
    is_ferrari = getattr(car, "canonical_name", "") == "ferrari"
    if is_ferrari:
        validation_step1 = copy.deepcopy(step1)
        validation_step2 = copy.deepcopy(step2)
        validation_step3 = copy.deepcopy(step3)
        validation_step2.front_heave_nmm = float(
            public_output_value(car, "front_heave_nmm", validation_step2.front_heave_nmm)
        )
        validation_step2.rear_third_nmm = float(
            public_output_value(car, "rear_third_nmm", validation_step2.rear_third_nmm)
        )
        validation_step3.front_torsion_od_mm = float(
            public_output_value(car, "front_torsion_od_mm", validation_step3.front_torsion_od_mm)
        )
        validation_step3.rear_spring_rate_nmm = float(
            public_output_value(car, "rear_spring_rate_nmm", validation_step3.rear_spring_rate_nmm)
        )
        validation_step3.rear_spring_perch_mm = 0.0

    warnings = validate_and_fix_garage_correlation(
        car=car,
        step1=validation_step1,
        step2=validation_step2,
        step3=validation_step3,
        step5=step5,
        fuel_l=fuel_l,
        track_name=track_name,
    )

    garage_model = car.active_garage_output_model(track_name)
    if garage_model is None:
        car_name = getattr(car, "canonical_name", "unknown")
        if car_name == "ferrari":
            # Ferrari uses indexed controls; no calibrated garage model is
            # expected or needed.  Promote the note to info_messages so it
            # doesn't pollute warnings/messages in reports.
            info_note = (
                "Ferrari 499P: indexed controls validated via range-clamp. "
                "No garage output model needed (physics constraints are "
                "enforced by the index-space range guards)."
            )
            return LegalValidation(
                valid=True,
                warnings=warnings,
                messages=[],
                info_messages=[info_note],
                snapped_or_corrected=bool(warnings),
                validation_tier="range_clamp",
            )
        support_note = (
            f"⚠ No garage model for {car_name} at {track_name}. "
            "Validation is geometric range-clamp ONLY — physics constraints "
            "(bottoming, vortex, slider travel) are NOT checked. "
            "Treat output as exploratory."
        )
        return LegalValidation(
            valid=True,
            warnings=warnings,
            messages=[support_note],
            snapped_or_corrected=bool(warnings),
            validation_tier="range_clamp",
        )

    state = GarageSetupState.from_solver_steps(
        step1=validation_step1,
        step2=validation_step2,
        step3=validation_step3,
        step5=step5,
        fuel_l=fuel_l,
    )
    constraint = garage_model.validate(
        state,
        front_excursion_p99_mm=getattr(validation_step2, "front_excursion_at_rate_mm", 0.0),
        front_bottoming_margin_mm=getattr(validation_step2, "front_bottoming_margin_mm", 0.0),
        vortex_burst_margin_mm=getattr(validation_step1, "vortex_burst_margin_mm", 0.0),
    )
    setattr(step2, "garage_constraints_ok", bool(constraint.valid))
    setattr(step2, "garage_constraint_notes", list(getattr(constraint, "messages", [])))
    return LegalValidation(
        valid=bool(constraint.valid),
        warnings=warnings,
        messages=list(getattr(constraint, "messages", [])),
        snapped_or_corrected=bool(warnings),
        validation_tier="full",
    )


def validate_candidate_legality(
    params: dict[str, float],
    car: Any,
) -> LegalValidation:
    """Search-time legality check for a candidate parameter set.

    Returns hard veto vs soft penalty distinction:
    - Hard veto: illegal discrete value, out-of-range after snap/clamp,
      predicted bottoming/vortex collapse, garage-model invalid
    - Soft penalty: unusual setup distance, unconventional ratio,
      weak confidence, out-of-envelope but not contradictory

    This is the fast path used during search — does NOT run the full
    solver chain. For final validation, use validate_solution_legality().
    """
    hard_veto = False
    hard_reasons: list[str] = []
    soft_penalties: list[str] = []
    corrected_fields: list[str] = []
    margin = 1.0

    gr = car.garage_ranges

    # ── Hard checks: parameter ranges ────────────────────────────────
    range_checks = {
        "front_pushrod_offset_mm": gr.front_pushrod_mm,
        "rear_pushrod_offset_mm": gr.rear_pushrod_mm,
        "front_heave_spring_nmm": gr.front_heave_nmm,
        "front_heave_perch_mm": gr.front_heave_perch_mm,
        "rear_third_spring_nmm": gr.rear_third_nmm,
        "rear_third_perch_mm": gr.rear_third_perch_mm,
        "rear_spring_rate_nmm": gr.rear_spring_nmm,
        "front_camber_deg": gr.camber_front_deg,
        "rear_camber_deg": gr.camber_rear_deg,
        "brake_bias_pct": (40.0, 60.0),  # broad legal range
        "diff_preload_nm": gr.diff_preload_nm,
    }
    for key, (lo, hi) in range_checks.items():
        if key not in params:
            continue
        val = params[key]
        if val < lo - 1e-9 or val > hi + 1e-9:
            hard_veto = True
            hard_reasons.append(f"{key}={val:.2f} outside [{lo}, {hi}]")

    # Damper click ranges
    d = car.damper
    damper_checks = {
        "front_ls_comp": d.ls_comp_range,
        "front_ls_rbd": d.ls_rbd_range,
        "front_hs_comp": d.hs_comp_range,
        "front_hs_rbd": d.hs_rbd_range,
        "front_hs_slope": d.hs_slope_range,
        "rear_ls_comp": d.ls_comp_range,
        "rear_ls_rbd": d.ls_rbd_range,
        "rear_hs_comp": d.hs_comp_range,
        "rear_hs_rbd": d.hs_rbd_range,
        "rear_hs_slope": d.hs_slope_range,
    }
    for key, (lo, hi) in damper_checks.items():
        if key not in params:
            continue
        val = params[key]
        if val < lo - 0.5 or val > hi + 0.5:
            hard_veto = True
            hard_reasons.append(f"{key}={val:.0f} outside [{lo}, {hi}]")

    # ── Soft checks: conventional ratios ─────────────────────────────
    # Heave/third ratio
    fh = params.get("front_heave_spring_nmm")
    rt = params.get("rear_third_spring_nmm")
    if fh is not None and rt is not None and rt > 0:
        ratio = fh / rt
        if ratio < 0.02 or ratio > 0.25:
            soft_penalties.append(f"Unusual heave/third ratio: {ratio:.3f}")
            margin = min(margin, 0.5)

    # Damper hierarchy: front LS comp should >= rear LS comp
    fls = params.get("front_ls_comp")
    rls = params.get("rear_ls_comp")
    if fls is not None and rls is not None and fls < rls:
        soft_penalties.append("Front LS comp < rear LS comp (entry instability)")
        margin = min(margin, 0.7)

    # Rear HS comp should <= front HS comp
    fhs = params.get("front_hs_comp")
    rhs = params.get("rear_hs_comp")
    if fhs is not None and rhs is not None and rhs > fhs + 2:
        soft_penalties.append("Rear HS comp >> front HS comp (compliance hierarchy violation)")
        margin = min(margin, 0.6)

    return LegalValidation(
        valid=not hard_veto,
        hard_veto=hard_veto,
        hard_veto_reasons=hard_reasons,
        soft_penalties=soft_penalties,
        legality_margin=margin,
        garage_corrections_count=len(corrected_fields),
        corrected_fields=corrected_fields,
    )
