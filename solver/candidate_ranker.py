from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CandidateScore:
    total: float
    safety: float
    performance: float
    stability: float
    confidence: float
    disruption_cost: float
    notes: list[str] = field(default_factory=list)


def combine_candidate_score(
    *,
    safety: float,
    performance: float,
    stability: float,
    confidence: float,
    disruption_cost: float,
    notes: list[str] | None = None,
) -> CandidateScore:
    safety = max(0.0, min(1.0, safety))
    performance = max(0.0, min(1.0, performance))
    stability = max(0.0, min(1.0, stability))
    confidence = max(0.0, min(1.0, confidence))
    disruption_cost = max(0.0, min(1.0, disruption_cost))
    total = (
        safety * 0.30
        + performance * 0.30
        + stability * 0.20
        + confidence * 0.10
        + (1.0 - disruption_cost) * 0.10
    )
    return CandidateScore(
        total=round(total, 3),
        safety=round(safety, 3),
        performance=round(performance, 3),
        stability=round(stability, 3),
        confidence=round(confidence, 3),
        disruption_cost=round(disruption_cost, 3),
        notes=list(notes or []),
    )
