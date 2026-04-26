"""Comparison engine — compare solver predictions against measured telemetry.

Each comparison maps a solver output field to a measured telemetry quantity,
computes the delta, and stores the tolerance thresholds for classification.
"""

from __future__ import annotations

from dataclasses import dataclass

from validator.extract import MeasuredState


@dataclass
class Comparison:
    """A single prediction-vs-measurement comparison."""
    step: int
    parameter: str
    predicted: float
    measured: float
    delta: float             # measured - predicted
    delta_pct: float         # |delta| / |predicted| * 100 (0 if predicted=0)
    tolerance_abs: float     # Below this = confirmed
    rethink_abs: float       # Above this = rethink (between = tweak)
    units: str
    physics_note: str


def _pct(predicted: float, measured: float) -> float:
    """Compute percentage difference, safe for zero predicted."""
    if abs(predicted) < 1e-9:
        return 0.0 if abs(measured) < 1e-9 else 100.0
    return abs(measured - predicted) / abs(predicted) * 100.0


def compare_all(solver_json: dict, measured: MeasuredState) -> list[Comparison]:
    """Build the full comparison matrix.

    Args:
        solver_json: Loaded solver output JSON (from --save)
        measured: MeasuredState from extract_measurements()

    Returns:
        List of Comparison objects, one per validated quantity
    """
    comparisons = []
    step1 = solver_json.get("step1_rake", {})
    step2 = solver_json.get("step2_heave", {})
    step3 = solver_json.get("step3_corner", {})
    step4 = solver_json.get("step4_arb", {})
    step5 = solver_json.get("step5_geometry", {})
    step6 = solver_json.get("step6_dampers", {})

    # ================================================================
    # Step 1: Aero Compression (offset-independent RH validation)
    # ================================================================
    # NOTE: IBT ride height sensors and aero model (AeroCalc) use
    # different reference frames with non-constant offsets.
    # The solver's compression values are AeroCalc-frame (correct for
    # aero map queries). IBT sensor compression is in sensor-frame.
    # Both are offset-independent (static - dynamic), BUT the static
    # reference point differs between frames, so a systematic offset
    # remains.  We V^2-scale the measured compression to the solver's
    # reference speed before comparing, and use wider tolerances to
    # account for the frame difference.

    pred_front_comp = step1.get("aero_compression_front_mm", 0)
    pred_rear_comp = step1.get("aero_compression_rear_mm", 0)
    ref_speed = step1.get("compression_ref_speed_kph", 230.0)

    # V^2-scale measured compression to reference speed
    mean_speed = measured.mean_speed_at_speed_kph if measured.mean_speed_at_speed_kph > 0 else 0
    if mean_speed > 100 and ref_speed > 100:
        v2_scale = (ref_speed / mean_speed) ** 2
    else:
        v2_scale = 1.0

    scaled_front_comp = measured.aero_compression_front_mm * v2_scale
    scaled_rear_comp = measured.aero_compression_rear_mm * v2_scale

    if pred_front_comp > 0 and measured.aero_compression_front_mm > 0:
        comparisons.append(Comparison(
            step=1,
            parameter="aero_compression_front_mm",
            predicted=round(pred_front_comp, 1),
            measured=round(scaled_front_comp, 1),
            delta=round(scaled_front_comp - pred_front_comp, 1),
            delta_pct=round(_pct(pred_front_comp, scaled_front_comp), 1),
            tolerance_abs=4.0,   # Wider: sensor vs AeroCalc frame offset
            rethink_abs=10.0,    # Only rethink if V^2 scaling itself fails
            units="mm",
            physics_note=(f"V^2-scaled to {ref_speed:.0f} kph "
                          f"(measured at {mean_speed:.0f} kph). "
                          "Sensor/AeroCalc frame offset expected."),
        ))

    if pred_rear_comp > 0 and measured.aero_compression_rear_mm > 0:
        comparisons.append(Comparison(
            step=1,
            parameter="aero_compression_rear_mm",
            predicted=round(pred_rear_comp, 1),
            measured=round(scaled_rear_comp, 1),
            delta=round(scaled_rear_comp - pred_rear_comp, 1),
            delta_pct=round(_pct(pred_rear_comp, scaled_rear_comp), 1),
            tolerance_abs=5.0,   # Wider: rear more sensitive to spring interaction
            rethink_abs=12.0,
            units="mm",
            physics_note=(f"V^2-scaled to {ref_speed:.0f} kph. "
                          "Rear more variable (spring-rate + aero interaction)."),
        ))

    # Ride height variance at speed (offset-independent)
    pred_front_sigma = step2.get("front_sigma_at_rate_mm", 0)
    if pred_front_sigma > 0 and measured.front_rh_std_mm > 0:
        comparisons.append(Comparison(
            step=1,
            parameter="front_rh_sigma_mm",
            predicted=pred_front_sigma,
            measured=round(measured.front_rh_std_mm, 1),
            delta=round(measured.front_rh_std_mm - pred_front_sigma, 1),
            delta_pct=round(_pct(pred_front_sigma, measured.front_rh_std_mm), 1),
            tolerance_abs=1.5,
            rethink_abs=4.0,
            units="mm",
            physics_note="RH variance at speed. Platform stability metric.",
        ))

    comparisons.append(Comparison(
        step=1,
        parameter="bottoming_events_front",
        predicted=0,
        measured=measured.bottoming_event_count_front,
        delta=measured.bottoming_event_count_front,
        delta_pct=0,
        tolerance_abs=0,
        rethink_abs=5,
        units="events",
        physics_note="Heave spring too soft or RH prediction wrong.",
    ))

    comparisons.append(Comparison(
        step=1,
        parameter="vortex_burst_events",
        predicted=0,
        measured=measured.vortex_burst_event_count,
        delta=measured.vortex_burst_event_count,
        delta_pct=0,
        tolerance_abs=0,
        rethink_abs=3,
        units="events",
        physics_note="Front RH too low. Safety-critical aero instability.",
    ))

    # ================================================================
    # Step 2: Platform Stability
    # ================================================================

    pred_front_exc = step2.get("front_excursion_at_rate_mm", 0)
    if pred_front_exc > 0 and measured.front_rh_excursion_measured_mm > 0:
        comparisons.append(Comparison(
            step=2,
            parameter="front_excursion_p99_mm",
            predicted=pred_front_exc,
            measured=round(measured.front_rh_excursion_measured_mm, 1),
            delta=round(measured.front_rh_excursion_measured_mm - pred_front_exc, 1),
            delta_pct=round(_pct(pred_front_exc, measured.front_rh_excursion_measured_mm), 1),
            tolerance_abs=2.0,
            rethink_abs=5.0,
            units="mm",
            physics_note="m_eff calibration drift or excursion model error.",
        ))

    pred_sv_front = step2.get("front_shock_vel_p99_mps", 0)
    if pred_sv_front > 0 and measured.front_shock_vel_p99_mps > 0:
        comparisons.append(Comparison(
            step=2,
            parameter="shock_vel_p99_front_mps",
            predicted=round(pred_sv_front, 4),
            measured=round(measured.front_shock_vel_p99_mps, 4),
            delta=round(measured.front_shock_vel_p99_mps - pred_sv_front, 4),
            delta_pct=round(_pct(pred_sv_front, measured.front_shock_vel_p99_mps), 1),
            tolerance_abs=pred_sv_front * 0.10,  # 10%
            rethink_abs=pred_sv_front * 0.25,    # 25%
            units="m/s",
            physics_note="Track surface changed or tire pressure effect.",
        ))

    pred_sv_rear = step2.get("rear_shock_vel_p99_mps", 0)
    if pred_sv_rear > 0 and measured.rear_shock_vel_p99_mps > 0:
        comparisons.append(Comparison(
            step=2,
            parameter="shock_vel_p99_rear_mps",
            predicted=round(pred_sv_rear, 4),
            measured=round(measured.rear_shock_vel_p99_mps, 4),
            delta=round(measured.rear_shock_vel_p99_mps - pred_sv_rear, 4),
            delta_pct=round(_pct(pred_sv_rear, measured.rear_shock_vel_p99_mps), 1),
            tolerance_abs=pred_sv_rear * 0.10,
            rethink_abs=pred_sv_rear * 0.25,
            units="m/s",
            physics_note="Track surface changed or tire pressure effect.",
        ))

    # ================================================================
    # Step 3: Natural Frequency (heave-mode, not per-corner)
    # ================================================================
    # The FFT on straight-line ride height measures the heave-mode
    # frequency: both wheels moving together under heave_spring +
    # 2*corner springs at full axle sprung mass.  The solver now
    # outputs front_heave_mode_freq_hz / rear_heave_mode_freq_hz
    # for this purpose.  Fall back to per-corner freq for old JSONs.

    pred_front_freq = step3.get("front_heave_mode_freq_hz",
                                step3.get("front_natural_freq_hz", 0))
    if pred_front_freq > 0 and measured.front_dominant_freq_hz > 0:
        comparisons.append(Comparison(
            step=3,
            parameter="front_heave_mode_freq_hz",
            predicted=round(pred_front_freq, 2),
            measured=measured.front_dominant_freq_hz,
            delta=round(measured.front_dominant_freq_hz - pred_front_freq, 2),
            delta_pct=round(_pct(pred_front_freq, measured.front_dominant_freq_hz), 1),
            tolerance_abs=0.4,
            rethink_abs=1.0,
            units="Hz",
            physics_note="Heave-mode: k_total = heave + 2*corner. "
                         "FFT on straight-line RH. Mass or stiffness wrong.",
        ))

    pred_rear_freq = step3.get("rear_heave_mode_freq_hz",
                               step3.get("rear_natural_freq_hz", 0))
    if pred_rear_freq > 0 and measured.rear_dominant_freq_hz > 0:
        comparisons.append(Comparison(
            step=3,
            parameter="rear_heave_mode_freq_hz",
            predicted=round(pred_rear_freq, 2),
            measured=measured.rear_dominant_freq_hz,
            delta=round(measured.rear_dominant_freq_hz - pred_rear_freq, 2),
            delta_pct=round(_pct(pred_rear_freq, measured.rear_dominant_freq_hz), 1),
            tolerance_abs=0.4,
            rethink_abs=1.0,
            units="Hz",
            physics_note="Heave-mode: k_total = third + 2*corner*MR^2. "
                         "FFT on straight-line RH. Mass or stiffness wrong.",
        ))

    # ================================================================
    # Step 4: Balance (roll gradient, body roll)
    # ================================================================
    #
    # Deliberately do NOT compare step4.lltd_achieved against
    # measured.lltd_measured here.  The IBT-derived "lltd_measured" field is a
    # backward-compatible alias for a ride-height roll-distribution proxy, not
    # true wheel-load LLTD.  Using it as a validation target caused downstream
    # ARB back-solves to chase a geometric constant rather than a measured
    # load-transfer distribution.

    # Roll gradient: derive predicted from step5 data
    pred_roll_grad = 0.0
    peak_g = step5.get("peak_lat_g", 0)
    roll_at_peak = step5.get("body_roll_at_peak_deg", 0)
    if peak_g > 0.5 and roll_at_peak > 0:
        pred_roll_grad = roll_at_peak / peak_g

    if pred_roll_grad > 0 and measured.roll_gradient_measured_deg_per_g > 0:
        comparisons.append(Comparison(
            step=4,
            parameter="roll_gradient_deg_per_g",
            predicted=round(pred_roll_grad, 3),
            measured=round(measured.roll_gradient_measured_deg_per_g, 3),
            delta=round(measured.roll_gradient_measured_deg_per_g - pred_roll_grad, 3),
            delta_pct=round(_pct(pred_roll_grad, measured.roll_gradient_measured_deg_per_g), 1),
            tolerance_abs=0.15,
            rethink_abs=0.40,
            units="deg/g",
            physics_note="Total roll stiffness mismatch.",
        ))

    if roll_at_peak > 0 and measured.body_roll_at_peak_g_deg > 0:
        comparisons.append(Comparison(
            step=4,
            parameter="body_roll_at_peak_g",
            predicted=round(roll_at_peak, 1),
            measured=round(measured.body_roll_at_peak_g_deg, 1),
            delta=round(measured.body_roll_at_peak_g_deg - roll_at_peak, 1),
            delta_pct=round(_pct(roll_at_peak, measured.body_roll_at_peak_g_deg), 1),
            tolerance_abs=0.3,
            rethink_abs=0.8,
            units="deg",
            physics_note="CG height or roll stiffness model failure.",
        ))

    # ================================================================
    # Step 6: Dampers (shock velocity spectrum)
    # ================================================================

    pred_sv_p95_front = step6.get("track_shock_vel_p95_front_mps", 0)
    if pred_sv_p95_front > 0 and measured.front_shock_vel_p95_mps > 0:
        comparisons.append(Comparison(
            step=6,
            parameter="shock_vel_p95_front_mps",
            predicted=round(pred_sv_p95_front, 4),
            measured=round(measured.front_shock_vel_p95_mps, 4),
            delta=round(measured.front_shock_vel_p95_mps - pred_sv_p95_front, 4),
            delta_pct=round(_pct(pred_sv_p95_front, measured.front_shock_vel_p95_mps), 1),
            tolerance_abs=pred_sv_p95_front * 0.10,
            rethink_abs=pred_sv_p95_front * 0.30,
            units="m/s",
            physics_note="Surface profile changed. Damper HS reference may need update.",
        ))

    pred_sv_p95_rear = step6.get("track_shock_vel_p95_rear_mps", 0)
    if pred_sv_p95_rear > 0 and measured.rear_shock_vel_p95_mps > 0:
        comparisons.append(Comparison(
            step=6,
            parameter="shock_vel_p95_rear_mps",
            predicted=round(pred_sv_p95_rear, 4),
            measured=round(measured.rear_shock_vel_p95_mps, 4),
            delta=round(measured.rear_shock_vel_p95_mps - pred_sv_p95_rear, 4),
            delta_pct=round(_pct(pred_sv_p95_rear, measured.rear_shock_vel_p95_mps), 1),
            tolerance_abs=pred_sv_p95_rear * 0.10,
            rethink_abs=pred_sv_p95_rear * 0.30,
            units="m/s",
            physics_note="Surface profile changed. Damper HS reference may need update.",
        ))

    # ================================================================
    # Handling Dynamics (derived from yaw, steering, slip)
    # ================================================================
    # These are NOT compared against solver predictions (solver doesn't
    # predict understeer/slip yet). Instead they are observational metrics
    # that inform whether the setup is producing the desired behavior.
    # Compare against target ranges rather than solver output.

    # Understeer angle: should be slightly positive (mild understeer)
    # at all speeds. Negative = oversteer (dangerous at high speed).
    if measured.understeer_mean_deg != 0:
        # Target: 0.5-2.0 deg understeer on average during cornering
        target_us = 1.0  # degrees
        comparisons.append(Comparison(
            step=4,
            parameter="understeer_mean_deg",
            predicted=target_us,
            measured=round(measured.understeer_mean_deg, 2),
            delta=round(measured.understeer_mean_deg - target_us, 2),
            delta_pct=round(_pct(target_us, measured.understeer_mean_deg), 1),
            tolerance_abs=1.5,   # within 1.5 deg of target = OK
            rethink_abs=3.0,     # 3+ deg off = fundamental balance issue
            units="deg",
            physics_note="Understeer angle from steering/yaw. + = understeer, - = oversteer.",
        ))

    # Low-speed vs high-speed understeer balance
    # If low-speed understeer differs significantly from high-speed,
    # it reveals speed-dependent balance issues (aero vs mechanical)
    if (measured.understeer_low_speed_deg != 0 and
            measured.understeer_high_speed_deg != 0):
        delta_us = measured.understeer_high_speed_deg - measured.understeer_low_speed_deg
        comparisons.append(Comparison(
            step=4,
            parameter="understeer_speed_gradient",
            predicted=0.0,  # Target: same at all speeds
            measured=round(delta_us, 2),
            delta=round(delta_us, 2),
            delta_pct=0,
            tolerance_abs=1.0,   # up to 1 deg difference OK
            rethink_abs=2.5,     # >2.5 deg = aero/mech balance issue
            units="deg",
            physics_note="High-speed minus low-speed understeer. "
                         "+ = more understeer at speed (too much front DF). "
                         "- = oversteer at speed (not enough front DF).",
        ))

    # Body slip: excessive slip angle = rear instability
    if measured.body_slip_p95_deg > 0:
        comparisons.append(Comparison(
            step=4,
            parameter="body_slip_p95_deg",
            predicted=2.0,  # Target: ~2 deg for GTP cars
            measured=round(measured.body_slip_p95_deg, 1),
            delta=round(measured.body_slip_p95_deg - 2.0, 1),
            delta_pct=round(_pct(2.0, measured.body_slip_p95_deg), 1),
            tolerance_abs=1.5,
            rethink_abs=3.0,
            units="deg",
            physics_note="P95 body slip angle. >4 deg = rear instability.",
        ))

    # Yaw correlation: R^2 of actual vs expected yaw rate
    # R^2 > 0.75 is normal for a racing car at the limit — tyre
    # nonlinearity, trail-braking, and aero shift all decorrelate
    # yaw from neutral-steer expectation.  Only flag if very low.
    if measured.yaw_rate_correlation > 0:
        comparisons.append(Comparison(
            step=6,
            parameter="yaw_rate_correlation",
            predicted=0.85,  # Realistic target for GTP at the limit
            measured=round(measured.yaw_rate_correlation, 3),
            delta=round(measured.yaw_rate_correlation - 0.85, 3),
            delta_pct=0,
            tolerance_abs=0.15,  # 0.70-1.0 = confirmed
            rethink_abs=0.30,    # <0.55 = fundamentally unpredictable
            units="R^2",
            physics_note="Yaw vs neutral-steer expectation. R^2>0.75 normal "
                         "at the limit. Low R^2 = tyre saturation or instability.",
        ))

    # ================================================================
    # Tyre Thermal Validation (Step 5 — camber/toe)
    # ================================================================
    # Temperature spread validates camber: inner-outer delta should be
    # close to 0 under peak cornering for optimal contact patch.

    # Average front temp spread across both sides
    if (measured.front_temp_spread_lf_c != 0 or
            measured.front_temp_spread_rf_c != 0):
        front_spread = (measured.front_temp_spread_lf_c +
                        measured.front_temp_spread_rf_c) / 2.0
        comparisons.append(Comparison(
            step=5,
            parameter="front_tyre_temp_spread",
            predicted=0.0,  # Target: even wear = 0 spread
            measured=round(front_spread, 1),
            delta=round(front_spread, 1),
            delta_pct=0,
            tolerance_abs=5.0,   # 5C spread OK
            rethink_abs=12.0,    # >12C = serious camber issue
            units="C",
            physics_note="Front inner-outer temp spread. "
                         "+ = too much camber (inner hot). "
                         "- = not enough camber (outer hot).",
        ))

    if (measured.rear_temp_spread_lr_c != 0 or
            measured.rear_temp_spread_rr_c != 0):
        rear_spread = (measured.rear_temp_spread_lr_c +
                       measured.rear_temp_spread_rr_c) / 2.0
        comparisons.append(Comparison(
            step=5,
            parameter="rear_tyre_temp_spread",
            predicted=0.0,
            measured=round(rear_spread, 1),
            delta=round(rear_spread, 1),
            delta_pct=0,
            tolerance_abs=5.0,
            rethink_abs=12.0,
            units="C",
            physics_note="Rear inner-outer temp spread. "
                         "Validates rear camber setting.",
        ))

    return comparisons
