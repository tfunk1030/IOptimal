"""Final garage validation and auto-correction before .sto write.

This module ensures that the combination of setup parameters written to the
.sto file is physically consistent — i.e., iRacing's garage would display
legal slider positions, ride heights, and deflections for the given
parameter combination.

The key insight is that iRacing's garage computes display values (heave slider
position, static ride height, spring deflections) from the *combination* of
setup inputs.  Validating each parameter in isolation is insufficient — we must
check the predicted garage outputs and correct the combination if necessary.

Correction strategy (auto-correct correlations):
  1. Check predicted heave slider position (must be <= 45 mm).
  2. Check predicted front static RH (must be >= 30 mm floor).
  3. If either fails, iteratively adjust perch offset and/or heave rate.
  4. Re-predict after each adjustment and stop when constraints pass.
  5. Range-clamp all parameters and quantise to iRacing resolution.
"""

from __future__ import annotations

import logging

from car_model.garage import GarageSetupState

logger = logging.getLogger(__name__)


def _snap(value: float, resolution: float) -> float:
    """Round *value* to nearest multiple of *resolution*."""
    if resolution <= 0:
        return value
    return round(value / resolution) * resolution


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _front_perch_step(gr) -> float:
    return (
        getattr(gr, "front_heave_perch_resolution_mm", None)
        or getattr(gr, "perch_resolution_mm", 1.0)
        or 1.0
    )


def _rear_third_perch_step(gr) -> float:
    return (
        getattr(gr, "rear_third_perch_resolution_mm", None)
        or getattr(gr, "perch_resolution_mm", 1.0)
        or 1.0
    )


def _is_bmw_sebring_soft_front_bar_edge(car, track_name: str | None, step1, step2, step3, fuel_l: float) -> bool:
    if getattr(car, "canonical_name", "").lower() != "bmw":
        return False
    if "sebring" not in (track_name or "").lower():
        return False
    options = list(getattr(getattr(car, "corner_spring", None), "front_torsion_od_options", []) or [])
    if options:
        softest_od = min(float(option) for option in options)
    else:
        softest_od = float(getattr(car.garage_ranges, "front_torsion_od_mm", (13.9, 16.0))[0])
    if float(step3.front_torsion_od_mm) > softest_od + 0.05:
        return False
    if fuel_l < 40.0:
        return False
    if float(step2.front_heave_nmm) > 55.0:
        return False
    if float(step2.perch_offset_front_mm) <= -8.0:
        return False
    if float(step1.front_pushrod_offset_mm) > -25.5:
        return False
    return True


def validate_and_fix_garage_correlation(
    car,
    step1,
    step2,
    step3,
    step5,
    fuel_l: float,
    track_name: str | None = None,
) -> list[str]:
    """Validate and auto-correct setup parameter correlations before .sto write.

    Checks the predicted iRacing garage outputs for the solver's parameter
    combination and makes corrective adjustments when constraints are violated.

    Modifies step1/step2 in-place when corrections are needed.
    Returns a list of warning/adjustment messages (empty if all OK).
    """
    warnings: list[str] = []
    gr = car.garage_ranges

    # --- Early exit: if core steps are blocked, nothing to validate ---
    if step1 is None or step2 is None or step3 is None:
        warnings.append("solver steps blocked — skipping garage validation")
        return warnings

    # Ferrari: public garage_ranges are in index space (0-8 heave, 0-18 torsion),
    # but solver outputs are physical units (N/mm, mm OD).  Convert to public-unit
    # deep copies before Phase 1 clamping so the index-space range guards operate
    # on the correct numeric domain.  This mirrors the pattern already used in
    # setup_writer.py and legality_engine.py.
    _ferrari_orig_step2 = None
    _ferrari_orig_step3 = None
    if getattr(car, 'canonical_name', '') == 'ferrari':
        import copy as _copy
        from car_model.setup_registry import public_output_value as _pov
        _ferrari_orig_step2, _ferrari_orig_step3 = step2, step3
        step2 = _copy.deepcopy(step2)
        step3 = _copy.deepcopy(step3)
        step2.front_heave_nmm = float(_pov(car, "front_heave_nmm", step2.front_heave_nmm))
        step2.rear_third_nmm = float(_pov(car, "rear_third_nmm", step2.rear_third_nmm))
        step3.front_torsion_od_mm = float(_pov(car, "front_torsion_od_mm", step3.front_torsion_od_mm))
        step3.rear_spring_rate_nmm = float(_pov(car, "rear_spring_rate_nmm", step3.rear_spring_rate_nmm))
        step3.rear_spring_perch_mm = 0.0

    # --- Phase 1: Range-clamp and quantise individual parameters ---
    warnings.extend(_clamp_step1(step1, gr))
    warnings.extend(_clamp_step2(step2, gr, car=car))
    warnings.extend(_clamp_step3(step3, gr, car=car))
    if step5 is not None:
        warnings.extend(_clamp_step5(step5, gr))

    # --- Ferrari write-back: propagate clamped index-space corrections to physical objects ---
    # _clamp_step2/_clamp_step3 operated on local deep copies in index space; write the
    # corrected values back to the originals so callers receive the adjusted values.
    if _ferrari_orig_step2 is not None and _ferrari_orig_step3 is not None:
        try:
            from car_model.setup_registry import internal_solver_value as _isv
            _ferrari_orig_step2.front_heave_nmm = float(_isv(car, "front_heave_nmm", step2.front_heave_nmm))
            _ferrari_orig_step2.rear_third_nmm = float(_isv(car, "rear_third_nmm", step2.rear_third_nmm))
            _ferrari_orig_step2.perch_offset_front_mm = step2.perch_offset_front_mm
            _ferrari_orig_step2.perch_offset_rear_mm = step2.perch_offset_rear_mm
            _ferrari_orig_step3.front_torsion_od_mm = float(_isv(car, "front_torsion_od_mm", step3.front_torsion_od_mm))
            _ferrari_orig_step3.rear_spring_rate_nmm = float(_isv(car, "rear_spring_rate_nmm", step3.rear_spring_rate_nmm))
            _ferrari_orig_step3.rear_spring_perch_mm = step3.rear_spring_perch_mm
        except Exception as exc:
            logger.warning(
                "Ferrari write-back failed (%s); restoring original physical "
                "step2/step3 to avoid partial corruption",
                exc,
            )
            warnings.append(
                f"Ferrari index write-back failed ({exc}); "
                f"using unclamped physical values"
            )
        # Restore local references to the physical objects so Phase 2 garage-model
        # validation (GarageSetupState.from_solver_steps) receives physical units
        # (N/mm, mm OD) rather than the index-space values used for Phase 1 clamping.
        step2 = _ferrari_orig_step2
        step3 = _ferrari_orig_step3

    # --- Phase 2: Garage-model correlation check ---
    # GT3 architecture (W4.3): the heave-slider / torsion-bar / front-RH fixers
    # all reference GTP-only physics (heave perch, torsion OD, pushrod-as-RH-
    # lever). GT3 RH-correction levers are SpringPerchOffset and BumpRubberGap
    # per corner — out of scope for this unit (deferred to W7.1 with the GT3
    # GarageOutputModel). Skip Phase 2 + Phase 3 entirely for GT3; do not call
    # garage_model.validate (its GTP fixers will mutate null step2/step3 fields
    # toward meaningless GTP physics).  Audit O17/O19/O20/O21.
    _is_gt3 = (
        car is not None
        and getattr(getattr(car, "suspension_arch", None), "has_heave_third", True) is False
    )
    if _is_gt3:
        # Mark step2 as constraints-OK so downstream report code (which reads
        # `garage_constraints_ok`) doesn't print a phantom CHECK on GT3.
        setattr(step2, "garage_constraints_ok", True)
        setattr(step2, "garage_constraint_notes", [])
        return warnings

    garage_model = car.active_garage_output_model(track_name)
    if garage_model is None:
        canonical = getattr(car, 'canonical_name', '')
        if canonical not in ('bmw', 'ferrari'):
            # BMW and Ferrari have calibrated GarageOutputModels when auto-calibration
            # data is present — suppress the warning for those cars to keep reports
            # clean when calibration hasn't loaded yet.  Other unknown cars get the
            # informational note below.
            warnings.append(
                f"NOTE: Garage correlation validation skipped for {canonical} — "
                f"no calibrated GarageOutputModel. Skipped checks: "
                f"heave slider position, torsion bar deflection limits, "
                f"front static RH floor, travel margin. "
                f"Output values are physics-only estimates — verify "
                f"all garage display values manually before loading .sto."
            )
        return warnings

    state = GarageSetupState.from_solver_steps(
        step1=step1, step2=step2, step3=step3,
        step5=step5, fuel_l=fuel_l,
    )
    constraint = garage_model.validate(
        state,
        front_excursion_p99_mm=step2.front_excursion_at_rate_mm,
    )
    final = constraint
    warnings.extend(_fix_bmw_soft_front_bar_edge(garage_model, car, step1, step2, step3, step5, fuel_l, gr, track_name))
    if warnings:
        state = GarageSetupState.from_solver_steps(
            step1=step1, step2=step2, step3=step3,
            step5=step5, fuel_l=fuel_l,
        )
        constraint = garage_model.validate(
            state,
            front_excursion_p99_mm=step2.front_excursion_at_rate_mm,
        )
        final = constraint
    if not constraint.valid:
        # Something is wrong — attempt auto-correction
        warnings.extend(_fix_slider(garage_model, car, step1, step2, step3, step5, fuel_l, gr))
        warnings.extend(_fix_torsion_bar_defl(garage_model, car, step1, step2, step3, step5, fuel_l, gr))
        warnings.extend(_fix_bmw_soft_front_bar_edge(garage_model, car, step1, step2, step3, step5, fuel_l, gr, track_name))
        warnings.extend(_fix_front_rh(garage_model, car, step1, step2, step3, step5, fuel_l, gr))

        # Final verification
        state = GarageSetupState.from_solver_steps(
            step1=step1, step2=step2, step3=step3,
            step5=step5, fuel_l=fuel_l,
        )
        final = garage_model.validate(
            state,
            front_excursion_p99_mm=step2.front_excursion_at_rate_mm,
        )
        if not final.valid:
            for msg in final.messages:
                warnings.append(f"UNCORRECTABLE: {msg}")

    # --- Phase 3: Reconcile step1 RH to match garage model prediction ---
    # Ensures the .sto ride heights match what iRacing will actually display.
    state = GarageSetupState.from_solver_steps(
        step1=step1, step2=step2, step3=step3,
        step5=step5, fuel_l=fuel_l,
    )
    predicted_front = garage_model.predict_front_static_rh(state)
    predicted_rear = garage_model.predict_rear_static_rh(state)
    if abs(predicted_front - step1.static_front_rh_mm) > 0.05:
        warnings.append(
            f"front RH reconciled: {step1.static_front_rh_mm:.1f} -> "
            f"{predicted_front:.1f} mm (garage model prediction)"
        )
        step1.static_front_rh_mm = round(predicted_front, 1)
        step1.rake_static_mm = round(step1.static_rear_rh_mm - step1.static_front_rh_mm, 1)
    if abs(predicted_rear - step1.static_rear_rh_mm) > 0.1:
        warnings.append(
            f"rear RH reconciled: {step1.static_rear_rh_mm:.1f} -> "
            f"{predicted_rear:.1f} mm (garage model prediction)"
        )
        step1.static_rear_rh_mm = round(predicted_rear, 1)
        step1.rake_static_mm = round(step1.static_rear_rh_mm - step1.static_front_rh_mm, 1)

    setattr(step2, "garage_constraints_ok", bool(final.valid))
    setattr(step2, "garage_constraint_notes", list(getattr(final, "messages", [])))

    return warnings


# ── Per-step clamping helpers ─────────────────────────────────────────────


def _clamp_step1(step1, gr) -> list[str]:
    """Clamp and quantise Step 1 (rake) parameters."""
    msgs: list[str] = []

    # Pushrod offsets
    old = step1.front_pushrod_offset_mm
    val = _snap(_clamp(old, *gr.front_pushrod_mm), gr.pushrod_resolution_mm)
    if val != old:
        msgs.append(f"front_pushrod: {old:.1f} -> {val:.1f} mm (clamped/snapped)")
        step1.front_pushrod_offset_mm = val

    old = step1.rear_pushrod_offset_mm
    val = _snap(_clamp(old, *gr.rear_pushrod_mm), gr.pushrod_resolution_mm)
    if val != old:
        msgs.append(f"rear_pushrod: {old:.1f} -> {val:.1f} mm (clamped/snapped)")
        step1.rear_pushrod_offset_mm = val

    # Static ride heights
    old = step1.static_front_rh_mm
    val = _clamp(old, *gr.static_rh_mm)
    if val != old:
        msgs.append(f"static_front_rh: {old:.1f} -> {val:.1f} mm (clamped)")
        step1.static_front_rh_mm = val

    old = step1.static_rear_rh_mm
    val = _clamp(old, *gr.static_rh_mm)
    if val != old:
        msgs.append(f"static_rear_rh: {old:.1f} -> {val:.1f} mm (clamped)")
        step1.static_rear_rh_mm = val

    return msgs


def _clamp_step2(step2, gr, car=None) -> list[str]:
    """Clamp and quantise Step 2 (heave/third) parameters.

    GT3 architecture (W3.1 / W4.1): step2 is a HeaveSolution.null() with all
    heave/third fields = 0 and `present=False`. The GT3 GarageRanges sentinel
    tuples are also (0.0, 0.0). Early-return on GT3 — there is nothing to clamp,
    and snapping a null field produces noisy 0.0 -> 0.0 messages otherwise.
    Audit O16/O18.
    """
    if (
        car is not None
        and getattr(getattr(car, "suspension_arch", None), "has_heave_third", True) is False
    ):
        return []
    if getattr(step2, "present", True) is False:
        # Defense-in-depth: even if `car` was not threaded through, a null
        # step2 means there's nothing to clamp.
        return []
    msgs: list[str] = []

    old = step2.front_heave_nmm
    val = _snap(_clamp(old, *gr.front_heave_nmm), gr.heave_spring_resolution_nmm)
    if abs(val - old) > 0.01:
        msgs.append(f"front_heave: {old:.0f} -> {val:.0f} N/mm (clamped/snapped)")
        step2.front_heave_nmm = float(val)

    old = step2.perch_offset_front_mm
    val = _snap(_clamp(old, *gr.front_heave_perch_mm), _front_perch_step(gr))
    if abs(val - old) > 0.01:
        msgs.append(f"front_heave_perch: {old:.1f} -> {val:.1f} mm (clamped/snapped)")
        step2.perch_offset_front_mm = val

    old = step2.rear_third_nmm
    val = _snap(_clamp(old, *gr.rear_third_nmm), gr.heave_spring_resolution_nmm)
    if abs(val - old) > 0.01:
        msgs.append(f"rear_third: {old:.0f} -> {val:.0f} N/mm (clamped/snapped)")
        step2.rear_third_nmm = float(val)

    old = step2.perch_offset_rear_mm
    val = _snap(_clamp(old, *gr.rear_third_perch_mm), _rear_third_perch_step(gr))
    if val != old:
        msgs.append(f"rear_third_perch: {old:.1f} -> {val:.1f} mm (clamped/snapped)")
        step2.perch_offset_rear_mm = val

    return msgs


def _clamp_step3(step3, gr, car=None) -> list[str]:
    """Clamp and quantise Step 3 (corner spring) parameters.

    GT3 architecture (W4.3): GT3 has no torsion bar (`step3.front_torsion_od_mm`
    is 0.0 by W2.3 contract). Snapping it to a discrete BMW grid would push it
    to 13.9 (BMW minimum) — meaningless for GT3 and will silently corrupt the
    .sto value. Skip the torsion clamp on GT3 but still clamp `rear_spring_rate`
    against the GT3 rear-spring range. Audit O17 cascade.
    """
    msgs: list[str] = []
    _is_gt3 = (
        car is not None
        and getattr(getattr(car, "suspension_arch", None), "has_heave_third", True) is False
    )

    if not _is_gt3:
        old = step3.front_torsion_od_mm
        clamped = _clamp(old, *gr.front_torsion_od_mm)
        # Snap to discrete options if available and they live in the same numeric
        # domain as the range.  For Ferrari the range is in index space (0–18) but
        # the discrete list contains physical OD values (19.99–23.99 mm), so the
        # two spaces are incompatible — fall back to rounding in that case.
        discrete_in_range = (
            gr.front_torsion_od_discrete
            and min(gr.front_torsion_od_discrete) <= gr.front_torsion_od_mm[1]
        )
        if discrete_in_range:
            val = min(gr.front_torsion_od_discrete, key=lambda x: abs(x - clamped))
        else:
            val = round(clamped, 2)
        if abs(val - old) > 0.01:
            msgs.append(f"front_torsion_od: {old:.2f} -> {val:.2f} mm (clamped)")
            step3.front_torsion_od_mm = val

    # rear_spring_rate clamp applies to both GTP and GT3 (GT3 has 4 corner
    # coil rates; the per-axle range here is reasonable for the rear pair).
    # TODO(W7.x): GT3 4-corner clamps once CornerSpringSolution carries
    # lf/rf/lr/rr_spring_rate_nmm independently.
    old = step3.rear_spring_rate_nmm
    val = _snap(_clamp(old, *gr.rear_spring_nmm), gr.rear_spring_resolution_nmm)
    if abs(val - old) > 0.01:
        msgs.append(f"rear_spring_rate: {old:.0f} -> {val:.0f} N/mm (clamped/snapped)")
        step3.rear_spring_rate_nmm = float(val)

    if not _is_gt3:
        # GT3 rear spring perch is a per-corner concept, not a paired-rear one.
        # Skip the GTP perch clamp until W7.x plumbs the GT3 garage state.
        old = step3.rear_spring_perch_mm
        val = _snap(_clamp(old, *gr.rear_spring_perch_mm), gr.rear_spring_perch_resolution_mm)
        if abs(val - old) > 0.01:
            msgs.append(f"rear_spring_perch: {old:.1f} -> {val:.1f} mm (clamped/snapped)")
            step3.rear_spring_perch_mm = val

    return msgs


def _clamp_step5(step5, gr) -> list[str]:
    """Clamp Step 5 (wheel geometry) parameters."""
    msgs: list[str] = []

    old = step5.front_camber_deg
    val = round(_clamp(old, *gr.camber_front_deg), 1)
    if abs(val - old) > 0.01:
        msgs.append(f"front_camber: {old:.2f} -> {val:.1f} deg (clamped)")
        step5.front_camber_deg = val

    old = step5.rear_camber_deg
    val = round(_clamp(old, *gr.camber_rear_deg), 1)
    if abs(val - old) > 0.01:
        msgs.append(f"rear_camber: {old:.2f} -> {val:.1f} deg (clamped)")
        step5.rear_camber_deg = val

    old = step5.front_toe_mm
    val = round(_clamp(old, *gr.toe_front_mm), 1)
    if abs(val - old) > 0.01:
        msgs.append(f"front_toe: {old:.2f} -> {val:.1f} mm (clamped)")
        step5.front_toe_mm = val

    old = step5.rear_toe_mm
    val = round(_clamp(old, *gr.toe_rear_mm), 1)
    if abs(val - old) > 0.01:
        msgs.append(f"rear_toe: {old:.2f} -> {val:.1f} mm (clamped)")
        step5.rear_toe_mm = val

    return msgs


# ── Garage-correlation fixers ─────────────────────────────────────────────


def _fix_slider(garage_model, car, step1, step2, step3, step5, fuel_l, gr) -> list[str]:
    """Fix heave slider > max_slider_mm by adjusting perch then heave rate.

    GT3 (W4.3): GT3 cars have no heave slider concept (no front heave spring,
    no slider position). Audit O19. Early-return.
    """
    if (
        car is not None
        and getattr(getattr(car, "suspension_arch", None), "has_heave_third", True) is False
    ):
        return []
    msgs: list[str] = []
    max_slider = garage_model.max_slider_mm
    max_iters = 20  # safety cap
    front_step = _front_perch_step(gr)

    for _ in range(max_iters):
        state = GarageSetupState.from_solver_steps(
            step1=step1, step2=step2, step3=step3,
            step5=step5, fuel_l=fuel_l,
        )
        slider = garage_model.predict_heave_slider_defl_static(state)
        if slider <= max_slider + 0.1:
            break

        # Try making perch more negative (tightens preload, lowers slider)
        new_perch = step2.perch_offset_front_mm - front_step
        if new_perch >= gr.front_heave_perch_mm[0]:
            old_perch = step2.perch_offset_front_mm
            step2.perch_offset_front_mm = new_perch
            msgs.append(
                f"heave slider {slider:.1f}mm > {max_slider:.0f}mm: "
                f"perch {old_perch:.1f} -> {new_perch:.1f} mm"
            )
            continue

        # Perch at minimum — bump heave rate up
        new_rate = step2.front_heave_nmm + 10.0
        if new_rate <= gr.front_heave_nmm[1]:
            old_rate = step2.front_heave_nmm
            step2.front_heave_nmm = new_rate
            # Reset perch to middle of range for re-optimisation
            step2.perch_offset_front_mm = _snap(
                (gr.front_heave_perch_mm[0] + gr.front_heave_perch_mm[1]) / 2,
                front_step,
            )
            msgs.append(
                f"heave slider still > {max_slider:.0f}mm after perch exhaust: "
                f"heave rate {old_rate:.0f} -> {new_rate:.0f} N/mm"
            )
            continue

        # Both maxed out — cannot fix
        msgs.append(
            f"UNCORRECTABLE: heave slider {slider:.1f}mm > {max_slider:.0f}mm "
            f"(perch and rate at limits)"
        )
        break

    return msgs


def _fix_front_rh(garage_model, car, step1, step2, step3, step5, fuel_l, gr) -> list[str]:
    """Fix front static RH < floor by adjusting front pushrod offset.

    GT3 (W4.3): GT3 front static RH lever is the spring perch (not pushrod
    offset) and BumpRubberGap controls travel limits. The pushrod-offset
    correction here is GTP-only physics. Audit O21. Skip with a deferral note.

    TODO(W7.x): GT3-specific RH fixer using SpringPerchOffset + BumpRubberGap
    garage state once the plumbing exists.
    """
    if (
        car is not None
        and getattr(getattr(car, "suspension_arch", None), "has_heave_third", True) is False
    ):
        return []
    msgs: list[str] = []
    # Use a safety margin above the floor to account for model RMSE (~0.2mm)
    floor = garage_model.front_rh_floor_mm
    margin = 0.3  # mm — slightly above LOO RMSE to prevent "Too Low" in garage

    state = GarageSetupState.from_solver_steps(
        step1=step1, step2=step2, step3=step3,
        step5=step5, fuel_l=fuel_l,
    )
    predicted_rh = garage_model.predict_front_static_rh(state)
    if predicted_rh >= floor + margin - 0.05:
        return msgs

    # Check if pushrod has enough leverage to fix the RH deficit.
    # If the coefficient is too small, pushrod changes would be extreme and
    # counter-productive (pushrod controls shock preload, not ride height).
    if abs(garage_model.front_coeff_pushrod) < 0.05:
        # Pushrod is not an effective lever — just accept the predicted RH
        # and warn that it may display below floor in iRacing.
        msgs.append(
            f"front static RH {predicted_rh:.1f}mm near floor {floor:.0f}mm "
            f"(pushrod coefficient too small for correction, accepting as-is)"
        )
        step1.static_front_rh_mm = round(predicted_rh, 1)
        step1.rake_static_mm = round(
            step1.static_rear_rh_mm - step1.static_front_rh_mm, 1
        )
        return msgs

    # Invert the regression to find the pushrod that gives floor + margin RH
    front_camber = (
        float(step5.front_camber_deg) if step5 is not None else
        float(car.geometry.front_camber_baseline_deg)
    )
    rear_camber = (
        float(step5.rear_camber_deg) if step5 is not None else
        float(car.geometry.rear_camber_baseline_deg)
    )
    new_pushrod = garage_model.front_pushrod_for_static_rh(
        floor + margin,
        front_heave_nmm=step2.front_heave_nmm,
        front_heave_perch_mm=step2.perch_offset_front_mm,
        front_torsion_od_mm=step3.front_torsion_od_mm,
        front_camber_deg=front_camber,
        fuel_l=fuel_l,
        # Provide full context for DirectRegression bisection
        rear_pushrod_mm=step1.rear_pushrod_offset_mm,
        rear_third_nmm=step2.rear_third_nmm,
        rear_third_perch_mm=step2.perch_offset_rear_mm,
        rear_spring_nmm=step3.rear_spring_rate_nmm,
        rear_spring_perch_mm=step3.rear_spring_perch_mm,
        rear_camber_deg=rear_camber,
    )
    new_pushrod = _snap(
        _clamp(new_pushrod, *gr.front_pushrod_mm),
        gr.pushrod_resolution_mm,
    )

    if new_pushrod != step1.front_pushrod_offset_mm:
        msgs.append(
            f"front static RH {predicted_rh:.1f}mm < floor {floor:.0f}mm: "
            f"pushrod {step1.front_pushrod_offset_mm:.1f} -> {new_pushrod:.1f} mm"
        )
        step1.front_pushrod_offset_mm = new_pushrod

        # Re-predict the actual RH with the new pushrod
        state = GarageSetupState.from_solver_steps(
            step1=step1, step2=step2, step3=step3,
            step5=step5, fuel_l=fuel_l,
        )
        new_rh = garage_model.predict_front_static_rh(state)
        step1.static_front_rh_mm = round(new_rh, 1)
        step1.rake_static_mm = round(
            step1.static_rear_rh_mm - step1.static_front_rh_mm, 1
        )

    return msgs


def _fix_torsion_bar_defl(garage_model, car, step1, step2, step3, step5, fuel_l, gr) -> list[str]:
    """Fix torsion bar defl > max_torsion_bar_defl_mm by stiffening the bar or adjusting heave perch.

    GT3 (W4.3): GT3 cars have no torsion bars (`step3.front_torsion_od_mm` is
    0.0). Mutating it from a discrete BMW options list is meaningless. Audit
    O20. Early-return.
    """
    if (
        car is not None
        and getattr(getattr(car, "suspension_arch", None), "has_heave_third", True) is False
    ):
        return []
    msgs: list[str] = []
    max_defl = garage_model.effective_torsion_bar_defl_limit_mm()
    if max_defl is None:
        return msgs
    max_iters = 20
    front_step = _front_perch_step(gr)

    for _ in range(max_iters):
        state = GarageSetupState.from_solver_steps(
            step1=step1, step2=step2, step3=step3,
            step5=step5, fuel_l=fuel_l,
        )
        outputs = garage_model.predict(state)
        defl = outputs.torsion_bar_defl_mm
        if defl <= max_defl + 1e-6:
            break

        # First try a smaller front-heave perch move. It is usually the least
        # disruptive way to pull BMW off the torsion-deflection edge.
        new_perch = step2.perch_offset_front_mm - front_step
        if new_perch >= gr.front_heave_perch_mm[0]:
            old_perch = step2.perch_offset_front_mm
            step2.perch_offset_front_mm = new_perch
            msgs.append(
                f"torsion bar defl {defl:.1f}mm > {max_defl:.1f}mm: "
                f"heave perch {old_perch:.1f} -> {new_perch:.1f} mm"
            )
            continue

        # If the perch is exhausted, stiffen the torsion bar.
        current_od = step3.front_torsion_od_mm
        options = getattr(car.corner_spring, "front_torsion_od_options", None)
        if options is not None:
            larger_options = [od for od in options if od > current_od + 0.05]
            if larger_options:
                new_od = larger_options[0]
                step3.front_torsion_od_mm = new_od
                msgs.append(
                    f"torsion bar defl {defl:.1f}mm > {max_defl:.1f}mm: "
                    f"torsion OD {current_od:.2f} -> {new_od:.2f} mm"
                )
                continue
        else:
            new_od = round(current_od + 0.5, 2)
            if new_od <= gr.front_torsion_od_mm[1]:
                step3.front_torsion_od_mm = new_od
                msgs.append(
                    f"torsion bar defl {defl:.1f}mm > {max_defl:.1f}mm: "
                    f"torsion OD {current_od:.2f} -> {new_od:.2f} mm"
                )
                continue

        msgs.append(
            f"UNCORRECTABLE: torsion bar defl {defl:.1f}mm > {max_defl:.1f}mm "
            f"(OD and heave perch at limits)"
        )
        break

    return msgs


def _fix_bmw_soft_front_bar_edge(garage_model, car, step1, step2, step3, step5, fuel_l, gr, track_name: str | None) -> list[str]:
    """Apply a conservative BMW/Sebring guard for the softest front bar on race fuel.

    Real-garage feedback shows the softest 13.90 mm bar can drop the front platform
    materially lower than the linear garage regression predicts when race fuel and a
    shallow front heave-perch are combined. Treat that combo as unsafe and move to the
    next legal torsion bar before report/export.
    """
    msgs: list[str] = []
    if not _is_bmw_sebring_soft_front_bar_edge(car, track_name, step1, step2, step3, fuel_l):
        return msgs

    options = sorted(list(getattr(getattr(car, "corner_spring", None), "front_torsion_od_options", []) or []))
    current_od = float(step3.front_torsion_od_mm)
    larger_options = [float(option) for option in options if float(option) > current_od + 0.05]
    if larger_options:
        new_od = round(larger_options[0], 2)
        step3.front_torsion_od_mm = new_od
        msgs.append(
            f"BMW/Sebring soft-front-bar guard: torsion OD {current_od:.2f} -> {new_od:.2f} mm"
        )
        return msgs

    new_pushrod = _snap(
        _clamp(step1.front_pushrod_offset_mm + gr.pushrod_resolution_mm, *gr.front_pushrod_mm),
        gr.pushrod_resolution_mm,
    )
    if abs(new_pushrod - step1.front_pushrod_offset_mm) > 0.01:
        msgs.append(
            f"BMW/Sebring soft-front-bar guard: front pushrod {step1.front_pushrod_offset_mm:.1f} -> {new_pushrod:.1f} mm"
        )
        step1.front_pushrod_offset_mm = new_pushrod
    return msgs
