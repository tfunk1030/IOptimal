"""Predict telemetry outcomes from a candidate setup.

Uses a hybrid approach: physics-based predictions where possible,
with learned residual corrections on top. Starts simple — directional
predictions based on known setup-to-telemetry relationships.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PredictedTelemetry:
    """Predicted telemetry values for a candidate setup."""

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


@dataclass
class PredictionConfidence:
    """Confidence levels for each predicted metric."""

    overall: float = 0.0
    per_metric: dict[str, float] = field(default_factory=dict)


def predict_telemetry_directional(
    current_measured: Any,
    setup_deltas: dict[str, float],
) -> tuple[PredictedTelemetry, PredictionConfidence]:
    """Predict telemetry changes from setup parameter deltas.

    This is the initial directional predictor — it estimates which way
    key telemetry metrics will move based on known physics relationships.

    Args:
        current_measured: Current MeasuredState as baseline.
        setup_deltas: Dict of parameter_name -> delta (proposed - current).

    Returns:
        (PredictedTelemetry, PredictionConfidence) tuple.
    """
    pred = PredictedTelemetry()
    conf = PredictionConfidence()

    # --- Front heave travel prediction ---
    # Stiffer front heave -> less travel used
    heave_delta = setup_deltas.get("front_heave_nmm", 0)
    if heave_delta != 0:
        current_travel = getattr(current_measured, "front_heave_travel_used_pct", None)
        if current_travel is not None and current_travel > 0:
            # Approximate: travel inversely proportional to stiffness
            ratio = heave_delta / max(1.0, getattr(current_measured, "_front_heave_nmm", 200))
            pred.front_heave_travel_used_pct = current_travel * (1.0 - ratio * 0.5)
            conf.per_metric["front_heave_travel_used_pct"] = 0.5

    # --- Rear RH variance prediction ---
    # Stiffer rear third -> less variance
    third_delta = setup_deltas.get("rear_third_nmm", 0)
    if third_delta != 0:
        current_std = getattr(current_measured, "rear_rh_std_mm", None)
        if current_std is not None and current_std > 0:
            ratio = third_delta / max(1.0, getattr(current_measured, "_rear_third_nmm", 200))
            pred.rear_rh_std_mm = current_std * (1.0 - ratio * 0.3)
            conf.per_metric["rear_rh_std_mm"] = 0.4

    # --- Braking pitch prediction ---
    # Stiffer front heave -> less braking pitch
    if heave_delta != 0:
        current_pitch = getattr(current_measured, "pitch_range_braking_deg", None)
        if current_pitch is not None and current_pitch > 0:
            ratio = heave_delta / max(1.0, getattr(current_measured, "_front_heave_nmm", 200))
            pred.braking_pitch_deg = current_pitch * (1.0 - ratio * 0.4)
            conf.per_metric["braking_pitch_deg"] = 0.4

    # Compute overall confidence
    if conf.per_metric:
        conf.overall = sum(conf.per_metric.values()) / len(conf.per_metric)

    return pred, conf
