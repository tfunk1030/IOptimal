from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import math


@dataclass
class PredictedTelemetry:
    front_heave_travel_used_pct: float | None = None
    front_excursion_mm: float | None = None
    rear_rh_std_mm: float | None = None
    braking_pitch_deg: float | None = None
    front_lock_p95: float | None = None
    rear_power_slip_p95: float | None = None
    body_slip_p95_deg: float | None = None
    understeer_low_deg: float | None = None
    understeer_high_deg: float | None = None
    front_pressure_hot_kpa: float | None = None
    rear_pressure_hot_kpa: float | None = None

    def to_dict(self) -> dict[str, float | None]:
        return asdict(self)


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
    step2: Any | None = None,
    step4: Any | None = None,
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
    current_heave = _safe_float(getattr(current_setup, "front_heave_nmm", None))
    current_third = _safe_float(getattr(current_setup, "rear_third_nmm", None))
    current_bb = _safe_float(getattr(current_setup, "brake_bias_pct", None))
    target_heave = _safe_float(getattr(step2, "front_heave_nmm", None)) if step2 is not None else current_heave
    target_third = _safe_float(getattr(step2, "rear_third_nmm", None)) if step2 is not None else current_third
    target_bb = _safe_float(getattr(supporting, "brake_bias_pct", None)) if supporting is not None else current_bb

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
            round(_clamp(baseline_front_travel * heave_ratio + corrections.get("front_heave_travel_used_pct", 0.0), 0.0, 150.0), 3)
            if baseline_front_travel is not None
            else None
        ),
        front_excursion_mm=(
            round(max(0.0, baseline_front_excursion * heave_ratio + corrections.get("front_excursion_mm", 0.0)), 3)
            if baseline_front_excursion is not None
            else None
        ),
        rear_rh_std_mm=(
            round(max(0.0, baseline_rear_sigma * third_ratio + corrections.get("rear_rh_std_mm", 0.0)), 3)
            if baseline_rear_sigma is not None
            else None
        ),
        braking_pitch_deg=(
            round(max(0.0, baseline_pitch * heave_ratio + corrections.get("braking_pitch_deg", 0.0)), 3)
            if baseline_pitch is not None
            else None
        ),
        front_lock_p95=(
            round(_clamp(baseline_front_lock * brake_ratio * heave_ratio + corrections.get("front_lock_p95", 0.0), 0.0, 0.3), 4)
            if baseline_front_lock is not None
            else None
        ),
        rear_power_slip_p95=(
            round(_clamp(baseline_rear_slip * third_ratio + corrections.get("rear_power_slip_p95", 0.0), 0.0, 0.3), 4)
            if baseline_rear_slip is not None
            else None
        ),
        body_slip_p95_deg=(
            round(max(0.0, baseline_body_slip * third_ratio + corrections.get("body_slip_p95_deg", 0.0)), 3)
            if baseline_body_slip is not None
            else None
        ),
        understeer_low_deg=(
            round((baseline_us_low or 0.0) + lltd_delta * 0.35 + corrections.get("understeer_low_deg", 0.0), 3)
            if baseline_us_low is not None
            else None
        ),
        understeer_high_deg=(
            round((baseline_us_high or 0.0) + lltd_delta * 0.25 + heave_ratio * 0.1 - 0.1 + corrections.get("understeer_high_deg", 0.0), 3)
            if baseline_us_high is not None
            else None
        ),
        front_pressure_hot_kpa=(
            round(max(0.0, baseline_front_pressure + corrections.get("front_pressure_hot_kpa", 0.0)), 3)
            if baseline_front_pressure is not None
            else None
        ),
        rear_pressure_hot_kpa=(
            round(max(0.0, baseline_rear_pressure + corrections.get("rear_pressure_hot_kpa", 0.0)), 3)
            if baseline_rear_pressure is not None
            else None
        ),
    )

    per_metric = {
        "front_heave_travel_used_pct": 0.82 if baseline_front_travel is not None and current_heave not in (None, 0.0) and target_heave not in (None, 0.0) else 0.45,
        "front_excursion_mm": 0.78 if baseline_front_excursion is not None else 0.4,
        "rear_rh_std_mm": 0.8 if baseline_rear_sigma is not None and current_third not in (None, 0.0) and target_third not in (None, 0.0) else 0.45,
        "braking_pitch_deg": 0.72 if baseline_pitch is not None else 0.35,
        "front_lock_p95": 0.68 if baseline_front_lock is not None and target_bb is not None else 0.35,
        "rear_power_slip_p95": 0.68 if baseline_rear_slip is not None else 0.35,
        "body_slip_p95_deg": 0.62 if baseline_body_slip is not None else 0.35,
        "understeer_low_deg": 0.55 if baseline_us_low is not None and lltd_achieved is not None else 0.3,
        "understeer_high_deg": 0.55 if baseline_us_high is not None and lltd_achieved is not None else 0.3,
        "front_pressure_hot_kpa": 0.4 if baseline_front_pressure is not None else 0.2,
        "rear_pressure_hot_kpa": 0.4 if baseline_rear_pressure is not None else 0.2,
    }
    overall = round(sum(per_metric.values()) / len(per_metric), 3)
    return predicted, PredictionConfidence(overall=overall, per_metric=per_metric)
