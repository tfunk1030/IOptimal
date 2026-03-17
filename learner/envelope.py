from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class TelemetryEnvelope:
    metrics: dict[str, dict[str, float]]
    sample_count: int
    source_sessions: list[str] = field(default_factory=list)


@dataclass
class EnvelopeDistance:
    total_score: float
    per_metric: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


DEFAULT_ENVELOPE_METRICS = [
    "front_rh_std_mm",
    "rear_rh_std_mm",
    "understeer_mean_deg",
    "body_slip_p95_deg",
    "front_heave_travel_used_pct",
    "rear_power_slip_ratio_p95",
    "front_braking_lock_ratio_p95",
]


def _extract_value(sample: Any, metric: str) -> float | None:
    if isinstance(sample, dict):
        value = sample.get(metric)
    else:
        value = getattr(sample, metric, None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_telemetry_envelope(
    samples: list[Any],
    *,
    metrics: list[str] | None = None,
    source_sessions: list[str] | None = None,
) -> TelemetryEnvelope:
    metrics = metrics or DEFAULT_ENVELOPE_METRICS
    envelope_metrics: dict[str, dict[str, float]] = {}
    for metric in metrics:
        values = [_extract_value(sample, metric) for sample in samples]
        filtered = np.array([value for value in values if value is not None], dtype=float)
        if filtered.size == 0:
            continue
        median = float(np.median(filtered))
        mad = float(np.median(np.abs(filtered - median)))
        scale = mad * 1.4826 if mad > 0 else max(0.001, float(np.std(filtered)) or 0.001)
        envelope_metrics[metric] = {
            "median": round(median, 4),
            "mad": round(mad, 4),
            "scale": round(scale, 4),
            "p10": round(float(np.percentile(filtered, 10)), 4),
            "p90": round(float(np.percentile(filtered, 90)), 4),
            "mean": round(float(np.mean(filtered)), 4),
            "std": round(float(np.std(filtered)), 4),
        }
    return TelemetryEnvelope(
        metrics=envelope_metrics,
        sample_count=len(samples),
        source_sessions=list(source_sessions or []),
    )


def compute_envelope_distance(
    sample: Any,
    envelope: TelemetryEnvelope,
) -> EnvelopeDistance:
    per_metric: dict[str, float] = {}
    notes: list[str] = []
    if envelope.sample_count <= 0 or not envelope.metrics:
        return EnvelopeDistance(total_score=0.0, per_metric={}, notes=["no envelope available"])

    for metric, stats in envelope.metrics.items():
        value = _extract_value(sample, metric)
        if value is None:
            continue
        scale = max(stats.get("scale", 0.001), 0.001)
        z = abs(value - stats["median"]) / scale
        per_metric[metric] = round(float(z), 3)
        if z > 2.5:
            notes.append(f"{metric} is outside the healthy envelope (z={z:.2f})")

    total = round(float(np.mean(list(per_metric.values()))) if per_metric else 0.0, 3)
    return EnvelopeDistance(total_score=total, per_metric=per_metric, notes=notes)
