from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import math

from analyzer.telemetry_truth import get_signal


@dataclass
class PredictedTelemetry:
    front_heave_travel_used_pct: float | None = None
    front_excursion_mm: float | None = None
    rear_rh_std_mm: float | None = None
    braking_pitch_deg: float | None = None
    front_lock_p95: float | None = None
    rear_power_slip_ratio_p95: float | None = None
    body_slip_p95_deg: float | None = None
    understeer_low_deg: float | None = None
    understeer_high_deg: float | None = None
    front_pressure_hot_kpa: float | None = None
    rear_pressure_hot_kpa: float | None = None

    def to_dict(self) -> dict[str, float | None]:
        return asdict(self)

    @property
    def rear_power_slip_p95(self) -> float | None:
        """Backward-compatible alias for rear_power_slip_ratio_p95."""
        return self.rear_power_slip_ratio_p95


@dataclass
class PredictionConfidence:
    overall: float
    per_metric: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"overall": self.overall, "per_metric": dict(self.per_metric)}


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sqrt_ratio(numerator: float | None, denominator: float | None) -> float:
    if numerator is None or denominator is None or numerator <= 0 or denominator <= 0:
        return 1.0
    return math.sqrt(numerator / denominator)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def predict_candidate_telemetry(
    *,
    current_setup: Any,
    baseline_measured: Any,
    step1: Any | None = None,
    step2: Any | None = None,
    step3: Any | None = None,
    step4: Any | None = None,
    step5: Any | None = None,
    step6: Any | None = None,
    supporting: Any | None = None,
    corrections: dict[str, float] | None = None,
) -> tuple[PredictedTelemetry, PredictionConfidence]:
    """Predict telemetry directionally from candidate setup changes.

    This first-pass predictor is intentionally simple and hybrid:
    - use baseline telemetry directly as the anchor
    - scale a few metrics with spring/support changes
    - allow additive learned corrections
    """
    corrections = corrections or {}
    # Clamp learned corrections to sane magnitudes — an unclamped +7.5mm correction
    # on rear_rh_std_mm makes the predictor useless and distorts candidate scoring.
    _MAX_CORRECTIONS = {
        "front_rh_std_mm": 3.0,
        "rear_rh_std_mm": 3.0,
        "front_heave_travel_used_pct": 10.0,
        "front_excursion_mm": 5.0,
        "braking_pitch_deg": 0.5,
        "front_lock_p95": 0.03,
        "rear_power_slip_ratio_p95": 0.03,
        "body_slip_p95_deg": 2.0,
        "understeer_low_deg": 0.5,
        "understeer_high_deg": 0.5,
    }
    corrections = {
        k: max(-cap, min(cap, v)) if (cap := _MAX_CORRECTIONS.get(k)) is not None else v
        for k, v in corrections.items()
    }
    current_heave = _safe_float(getattr(current_setup, "front_heave_nmm", None))
    current_third = _safe_float(getattr(current_setup, "rear_third_nmm", None))
    current_bb = _safe_float(getattr(current_setup, "brake_bias_pct", None))
    current_pushrod_front = _safe_float(getattr(current_setup, "front_pushrod_mm", None))
    current_pushrod_rear = _safe_float(getattr(current_setup, "rear_pushrod_mm", None))
    current_front_torsion = _safe_float(getattr(current_setup, "front_torsion_od_mm", None))
    current_rear_spring = _safe_float(getattr(current_setup, "rear_spring_nmm", None))
    current_front_camber = _safe_float(getattr(current_setup, "front_camber_deg", None))
    current_rear_camber = _safe_float(getattr(current_setup, "rear_camber_deg", None))
    current_front_toe = _safe_float(getattr(current_setup, "front_toe_mm", None))
    current_rear_toe = _safe_float(getattr(current_setup, "rear_toe_mm", None))
    current_rear_arb = _safe_float(getattr(current_setup, "rear_arb_blade", None))
    current_diff_preload = _safe_float(getattr(current_setup, "diff_preload_nm", None))
    current_tc_gain = _safe_float(getattr(current_setup, "tc_gain", None))
    current_tc_slip = _safe_float(getattr(current_setup, "tc_slip", None))
    target_heave = _safe_float(getattr(step2, "front_heave_nmm", None)) if step2 is not None else current_heave
    target_third = _safe_float(getattr(step2, "rear_third_nmm", None)) if step2 is not None else current_third
    target_bb = _safe_float(getattr(supporting, "brake_bias_pct", None)) if supporting is not None else current_bb
    target_pushrod_front = _safe_float(getattr(step1, "front_pushrod_offset_mm", None)) if step1 is not None else current_pushrod_front
    target_pushrod_rear = _safe_float(getattr(step1, "rear_pushrod_offset_mm", None)) if step1 is not None else current_pushrod_rear
    target_front_torsion = _safe_float(getattr(step3, "front_torsion_od_mm", None)) if step3 is not None else current_front_torsion
    target_rear_spring = _safe_float(getattr(step3, "rear_spring_rate_nmm", None)) if step3 is not None else current_rear_spring
    target_front_camber = _safe_float(getattr(step5, "front_camber_deg", None)) if step5 is not None else current_front_camber
    target_rear_camber = _safe_float(getattr(step5, "rear_camber_deg", None)) if step5 is not None else current_rear_camber
    target_front_toe = _safe_float(getattr(step5, "front_toe_mm", None)) if step5 is not None else current_front_toe
    target_rear_toe = _safe_float(getattr(step5, "rear_toe_mm", None)) if step5 is not None else current_rear_toe
    target_rear_arb = _safe_float(getattr(step4, "rear_arb_blade_start", None)) if step4 is not None else current_rear_arb
    target_diff_preload = _safe_float(getattr(supporting, "diff_preload_nm", None)) if supporting is not None else current_diff_preload
    target_tc_gain = _safe_float(getattr(supporting, "tc_gain", None)) if supporting is not None else current_tc_gain
    target_tc_slip = _safe_float(getattr(supporting, "tc_slip", None)) if supporting is not None else current_tc_slip
    target_front_hs_comp = _safe_float(getattr(getattr(step6, "lf", None), "hs_comp", None)) if step6 is not None else None
    target_rear_hs_comp = _safe_float(getattr(getattr(step6, "lr", None), "hs_comp", None)) if step6 is not None else None
    current_front_hs_comp = _safe_float(getattr(current_setup, "front_hs_comp", None))
    current_rear_hs_comp = _safe_float(getattr(current_setup, "rear_hs_comp", None))

    baseline_front_travel = _safe_float(getattr(baseline_measured, "front_heave_travel_used_pct", None))
    baseline_front_excursion = _safe_float(getattr(baseline_measured, "front_rh_excursion_measured_mm", None))
    baseline_rear_sigma = _safe_float(getattr(baseline_measured, "rear_rh_std_mm", None))
    baseline_pitch = _safe_float(getattr(baseline_measured, "pitch_range_braking_deg", None))
    baseline_front_lock = _safe_float(getattr(baseline_measured, "front_braking_lock_ratio_p95", None))
    baseline_rear_slip = _safe_float(getattr(baseline_measured, "rear_power_slip_ratio_p95", None))
    baseline_body_slip = _safe_float(getattr(baseline_measured, "body_slip_p95_deg", None))
    baseline_us_low = _safe_float(getattr(baseline_measured, "understeer_low_speed_deg", None))
    baseline_us_high = _safe_float(getattr(baseline_measured, "understeer_high_speed_deg", None))
    baseline_front_pressure = _safe_float(getattr(baseline_measured, "front_pressure_mean_kpa", None))
    baseline_rear_pressure = _safe_float(getattr(baseline_measured, "rear_pressure_mean_kpa", None))

    heave_ratio = _sqrt_ratio(current_heave, target_heave)
    third_ratio = _sqrt_ratio(current_third, target_third)

    def _delta(current: float | None, target: float | None, scale: float) -> float:
        if current is None or target is None or scale <= 0:
            return 0.0
        return _clamp((target - current) / scale, -1.0, 1.0)

    pushrod_front_delta = _delta(current_pushrod_front, target_pushrod_front, 4.0)
    pushrod_rear_delta = _delta(current_pushrod_rear, target_pushrod_rear, 4.0)
    front_torsion_delta = _delta(current_front_torsion, target_front_torsion, 1.0)
    rear_spring_delta = _delta(current_rear_spring, target_rear_spring, 30.0)
    front_camber_delta = _delta(abs(current_front_camber) if current_front_camber is not None else None, abs(target_front_camber) if target_front_camber is not None else None, 0.6)
    rear_camber_delta = _delta(abs(current_rear_camber) if current_rear_camber is not None else None, abs(target_rear_camber) if target_rear_camber is not None else None, 0.4)
    front_toe_delta = _delta(abs(current_front_toe) if current_front_toe is not None else None, abs(target_front_toe) if target_front_toe is not None else None, 0.8)
    rear_toe_delta = _delta(current_rear_toe, target_rear_toe, 0.8)
    rear_arb_delta = _delta(current_rear_arb, target_rear_arb, 2.0)
    diff_preload_delta = _delta(current_diff_preload, target_diff_preload, 20.0)
    tc_gain_delta = _delta(current_tc_gain, target_tc_gain, 2.0)
    tc_slip_delta = _delta(current_tc_slip, target_tc_slip, 2.0)
    front_hs_comp_delta = _delta(current_front_hs_comp, target_front_hs_comp, 3.0)
    rear_hs_comp_delta = _delta(current_rear_hs_comp, target_rear_hs_comp, 3.0)

    # Front platform: stiffer heave (ratio < 1) reduces travel.
    # More negative pushrod (negative delta) lowers front RH → less travel.
    # Stiffer HS comp (positive delta) and torsion bar (positive delta) also reduce.
    front_platform_factor = _clamp(
        heave_ratio
        * (1.0 + 0.05 * pushrod_front_delta - 0.02 * front_hs_comp_delta - 0.015 * front_torsion_delta),
        0.72,
        1.25,
    )
    # Rear platform: stiffer springs reduce variance (third_ratio < 1 when stiffer),
    # more negative pushrod lowers RH (reduces variance — pushrod delta is negative
    # when target < current, so +0.04 makes negative delta shrink the factor).
    # Stiffer rear spring also reduces variance (positive delta = stiffer target).
    rear_platform_factor = _clamp(
        third_ratio
        * (1.0 + 0.04 * pushrod_rear_delta - 0.03 * rear_spring_delta - 0.02 * rear_hs_comp_delta),
        0.72,
        1.25,
    )
    # Traction: higher preload/TC reduces slip (factor < 1).
    # Stiffer rear ARB reduces rear mechanical grip → MORE slip (factor > 1).
    # This is correct: rear ARB stiffness trades rear grip for front response.
    traction_factor = _clamp(
        rear_platform_factor
        * (1.0 - 0.08 * diff_preload_delta - 0.05 * tc_gain_delta - 0.04 * tc_slip_delta + 0.03 * rear_arb_delta),
        0.65,
        1.3,
    )

    lltd_achieved = _safe_float(getattr(step4, "lltd_achieved", None)) if step4 is not None else None
    lltd_delta = 0.0
    if lltd_achieved is not None:
        # Relative to a neutral 0.5 proxy; small effect in first-pass predictor.
        lltd_delta = (lltd_achieved - 0.5) * 2.0

    brake_ratio = 1.0
    if baseline_front_lock is not None and current_bb not in (None, 0.0) and target_bb not in (None, 0.0):
        brake_ratio = target_bb / current_bb

    predicted = PredictedTelemetry(
        front_heave_travel_used_pct=(
            round(_clamp(baseline_front_travel * front_platform_factor + corrections.get("front_heave_travel_used_pct", 0.0), 0.0, 150.0), 3)
            if baseline_front_travel is not None
            else None
        ),
        front_excursion_mm=(
            round(max(0.0, baseline_front_excursion * front_platform_factor + corrections.get("front_excursion_mm", 0.0)), 3)
            if baseline_front_excursion is not None
            else None
        ),
        rear_rh_std_mm=(
            round(max(0.0, baseline_rear_sigma * rear_platform_factor + corrections.get("rear_rh_std_mm", 0.0)), 3)
            if baseline_rear_sigma is not None
            else None
        ),
        braking_pitch_deg=(
            round(max(0.0, baseline_pitch * _clamp(front_platform_factor * (1.0 - 0.02 * pushrod_front_delta), 0.72, 1.25) + corrections.get("braking_pitch_deg", 0.0)), 3)
            if baseline_pitch is not None
            else None
        ),
        front_lock_p95=(
            round(_clamp(baseline_front_lock * brake_ratio * front_platform_factor + corrections.get("front_lock_p95", 0.0), 0.0, 0.3), 4)
            if baseline_front_lock is not None
            else None
        ),
        rear_power_slip_ratio_p95=(
            round(_clamp(baseline_rear_slip * traction_factor + corrections.get("rear_power_slip_ratio_p95", 0.0), 0.0, 0.3), 4)
            if baseline_rear_slip is not None
            else None
        ),
        body_slip_p95_deg=(
            round(
                max(
                    0.0,
                    baseline_body_slip
                    * _clamp(
                        1.0
                        - 0.07 * diff_preload_delta
                        - 0.04 * rear_camber_delta
                        - 0.03 * rear_hs_comp_delta
                        + 0.04 * rear_arb_delta,
                        0.7,
                        1.3,
                    )
                    + corrections.get("body_slip_p95_deg", 0.0),
                ),
                3,
            )
            if baseline_body_slip is not None
            else None
        ),
        understeer_low_deg=(
            round(
                (baseline_us_low or 0.0)
                + lltd_delta * 0.55
                + 0.14 * front_torsion_delta
                - 0.22 * rear_arb_delta
                - 0.25 * front_camber_delta
                - 0.08 * front_toe_delta
                + 0.06 * diff_preload_delta
                + corrections.get("understeer_low_deg", 0.0),
                3,
            )
            if baseline_us_low is not None
            else None
        ),
        understeer_high_deg=(
            round(
                (baseline_us_high or 0.0)
                + lltd_delta * 0.5
                + 0.15 * front_torsion_delta
                - 0.16 * rear_arb_delta
                - 0.15 * front_camber_delta
                + 0.10 * pushrod_front_delta  # lower front RH (negative delta) = more front DF = less understeer
                - 0.08 * front_hs_comp_delta
                + corrections.get("understeer_high_deg", 0.0),
                3,
            )
            if baseline_us_high is not None
            else None
        ),
        front_pressure_hot_kpa=(
            round(
                max(
                    0.0,
                    baseline_front_pressure
                    + 1.2 * front_toe_delta
                    + 0.8 * front_camber_delta
                    + corrections.get("front_pressure_hot_kpa", 0.0),
                ),
                3,
            )
            if baseline_front_pressure is not None
            else None
        ),
        rear_pressure_hot_kpa=(
            round(
                max(
                    0.0,
                    baseline_rear_pressure
                    + 0.8 * rear_toe_delta
                    + 0.6 * rear_camber_delta
                    + corrections.get("rear_pressure_hot_kpa", 0.0),
                ),
                3,
            )
            if baseline_rear_pressure is not None
            else None
        ),
    )

    def _signal_conf(name: str) -> float:
        signal = get_signal(baseline_measured, name)
        if signal.value is None:
            return 0.2
        return signal.confidence or 0.2

    def _metric_conf(signal_names: list[str], *, input_terms: list[float | None], correction_key: str | None = None) -> float:
        signal_conf = sum(_signal_conf(name) for name in signal_names) / max(len(signal_names), 1)
        input_bonus = min(0.18, sum(1 for term in input_terms if term not in (None, 0.0)) * 0.04)
        correction_bonus = 0.05 if correction_key and correction_key in corrections else 0.0
        return round(_clamp(signal_conf * 0.7 + input_bonus + correction_bonus, 0.2, 0.95), 3)

    per_metric = {
        "front_heave_travel_used_pct": _metric_conf(
            ["front_heave_travel_used_pct"],
            input_terms=[current_heave, target_heave, target_pushrod_front, target_front_hs_comp],
            correction_key="front_heave_travel_used_pct",
        ),
        "front_excursion_mm": _metric_conf(
            ["front_heave_travel_used_pct"],
            input_terms=[target_heave, target_pushrod_front, target_front_hs_comp],
            correction_key="front_excursion_mm",
        ),
        "rear_rh_std_mm": _metric_conf(
            ["rear_rh_std_mm"],
            input_terms=[current_third, target_third, target_rear_spring, target_rear_hs_comp],
            correction_key="rear_rh_std_mm",
        ),
        "braking_pitch_deg": _metric_conf(
            ["pitch_range_braking_deg"],
            input_terms=[target_heave, target_pushrod_front, target_front_hs_comp],
            correction_key="braking_pitch_deg",
        ),
        "front_lock_p95": _metric_conf(
            ["front_braking_lock_ratio_p95"],
            input_terms=[target_bb, target_heave, target_pushrod_front],
            correction_key="front_lock_p95",
        ),
        "rear_power_slip_ratio_p95": _metric_conf(
            ["rear_power_slip_ratio_p95"],
            input_terms=[target_third, target_diff_preload, target_tc_gain, target_tc_slip, target_rear_arb],
            correction_key="rear_power_slip_ratio_p95",
        ),
        "body_slip_p95_deg": _metric_conf(
            ["body_slip_p95_deg"],
            input_terms=[target_diff_preload, target_rear_arb, target_rear_camber, target_rear_hs_comp],
            correction_key="body_slip_p95_deg",
        ),
        "understeer_low_deg": _metric_conf(
            ["understeer_low_speed_deg"],
            input_terms=[lltd_achieved, target_front_torsion, target_rear_arb, target_front_camber, target_front_toe],
            correction_key="understeer_low_deg",
        ),
        "understeer_high_deg": _metric_conf(
            ["understeer_high_speed_deg"],
            input_terms=[lltd_achieved, target_front_torsion, target_pushrod_front, target_front_hs_comp, target_front_camber],
            correction_key="understeer_high_deg",
        ),
        "front_pressure_hot_kpa": _metric_conf(
            ["front_pressure_mean_kpa"],
            input_terms=[target_front_camber, target_front_toe],
            correction_key="front_pressure_hot_kpa",
        ),
        "rear_pressure_hot_kpa": _metric_conf(
            ["rear_pressure_mean_kpa"],
            input_terms=[target_rear_camber, target_rear_toe],
            correction_key="rear_pressure_hot_kpa",
        ),
    }
    overall = round(sum(per_metric.values()) / len(per_metric), 3)
    return predicted, PredictionConfidence(overall=overall, per_metric=per_metric)
