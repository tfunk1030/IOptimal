"""Healthy telemetry envelope by car/track combination.

Maintains statistical bounds (mean, std, p10, p90) for key telemetry
metrics from sessions judged as "good". New sessions can be scored
against the envelope to detect anomalies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class TelemetryEnvelope:
    """Statistical bounds for key telemetry metrics from healthy sessions."""

    metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    # Each metric maps to: {"mean", "std", "median", "mad", "p10", "p90"}
    sample_count: int = 0
    source_sessions: list[str] = field(default_factory=list)

    def add_observation(self, metric: str, value: float) -> None:
        """Record a single metric observation (accumulate for later fit)."""
        if metric not in self.metrics:
            self.metrics[metric] = {"_values": []}  # type: ignore[dict-item]
        values_list = self.metrics[metric].get("_values")
        if isinstance(values_list, list):
            values_list.append(value)

    def fit(self) -> None:
        """Compute envelope statistics from accumulated observations."""
        for metric, data in list(self.metrics.items()):
            values_list = data.get("_values")
            if not isinstance(values_list, list) or len(values_list) < 2:
                continue
            arr = np.array(values_list, dtype=float)
            self.metrics[metric] = {
                "mean": float(np.mean(arr)),
                "std": float(np.std(arr)),
                "median": float(np.median(arr)),
                "mad": float(np.median(np.abs(arr - np.median(arr)))),
                "p10": float(np.percentile(arr, 10)),
                "p90": float(np.percentile(arr, 90)),
                "count": float(len(arr)),
            }

    def z_score(self, metric: str, value: float) -> float | None:
        """Return robust z-score for a value against the envelope."""
        stats = self.metrics.get(metric)
        if stats is None or "mad" not in stats:
            return None
        mad = stats["mad"]
        if mad < 1e-9:
            std = stats.get("std", 1e-9)
            if std < 1e-9:
                return 0.0
            return (value - stats["mean"]) / std
        return (value - stats["median"]) / (mad * 1.4826)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": {k: {kk: vv for kk, vv in v.items() if kk != "_values"} for k, v in self.metrics.items()},
            "sample_count": self.sample_count,
            "source_sessions": self.source_sessions,
        }

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: str | Path) -> TelemetryEnvelope:
        data = json.loads(Path(path).read_text())
        return cls(
            metrics=data.get("metrics", {}),
            sample_count=data.get("sample_count", 0),
            source_sessions=data.get("source_sessions", []),
        )


@dataclass
class EnvelopeDistance:
    """Distance of a session from the healthy envelope."""

    total_score: float = 0.0
    per_metric: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def compute_envelope_distance(
    envelope: TelemetryEnvelope,
    measured_values: dict[str, float],
) -> EnvelopeDistance:
    """Score how far a session is from the healthy envelope.

    Uses robust z-scores. Higher score = further from healthy.

    Args:
        envelope: The healthy baseline envelope.
        measured_values: Dict of metric_name -> measured_value.

    Returns:
        EnvelopeDistance with per-metric and total scores.
    """
    per_metric: dict[str, float] = {}
    notes: list[str] = []

    for metric, value in measured_values.items():
        z = envelope.z_score(metric, value)
        if z is not None:
            per_metric[metric] = abs(z)
            if abs(z) > 2.0:
                notes.append(f"{metric}: z={z:+.2f} (outside healthy range)")

    if per_metric:
        total = float(np.mean(list(per_metric.values())))
    else:
        total = 0.0

    return EnvelopeDistance(
        total_score=round(total, 3),
        per_metric={k: round(v, 3) for k, v in per_metric.items()},
        notes=notes,
    )
