"""Score and rank candidate setups.

Each candidate is scored on safety, performance, stability, confidence,
and disruption cost. Weights can be adjusted for qualifying vs race
vs endurance contexts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from solver.candidate_search import SetupCandidate


@dataclass
class CandidateScore:
    """Multi-dimensional score for a candidate setup."""

    total: float = 0.0
    safety: float = 0.0
    performance: float = 0.0
    stability: float = 0.0
    confidence: float = 0.0
    disruption_cost: float = 0.0
    notes: list[str] = field(default_factory=list)


# Weight presets for different contexts
WEIGHT_PRESETS = {
    "qualifying": {
        "safety": 0.20,
        "performance": 0.40,
        "stability": 0.15,
        "confidence": 0.15,
        "low_disruption": 0.10,
    },
    "sprint_race": {
        "safety": 0.30,
        "performance": 0.30,
        "stability": 0.20,
        "confidence": 0.10,
        "low_disruption": 0.10,
    },
    "endurance": {
        "safety": 0.35,
        "performance": 0.20,
        "stability": 0.25,
        "confidence": 0.10,
        "low_disruption": 0.10,
    },
}


def score_candidate(
    candidate: "SetupCandidate",
    safety_score: float = 1.0,
    performance_score: float = 0.5,
    stability_score: float = 0.5,
    disruption_score: float = 0.0,
    context: str = "sprint_race",
) -> CandidateScore:
    """Score a candidate setup against multi-dimensional criteria.

    Args:
        candidate: The setup candidate to score.
        safety_score: 0-1, how safe is the setup (bottoming, vortex burst, etc.)
        performance_score: 0-1, expected pace improvement.
        stability_score: 0-1, platform stability and consistency.
        disruption_score: 0-1, how much change from current setup (0=no change).
        context: "qualifying", "sprint_race", or "endurance".

    Returns:
        CandidateScore with weighted total.
    """
    weights = WEIGHT_PRESETS.get(context, WEIGHT_PRESETS["sprint_race"])

    low_disruption = 1.0 - disruption_score
    confidence = candidate.confidence

    total = (
        weights["safety"] * safety_score
        + weights["performance"] * performance_score
        + weights["stability"] * stability_score
        + weights["confidence"] * confidence
        + weights["low_disruption"] * low_disruption
    )

    notes: list[str] = [
        f"Family: {candidate.family}",
        f"Context: {context}",
    ]

    return CandidateScore(
        total=round(total, 4),
        safety=round(safety_score, 4),
        performance=round(performance_score, 4),
        stability=round(stability_score, 4),
        confidence=round(confidence, 4),
        disruption_cost=round(disruption_score, 4),
        notes=notes,
    )


def rank_candidates(
    candidates: list["SetupCandidate"],
    scores: list[CandidateScore],
) -> list[tuple["SetupCandidate", CandidateScore]]:
    """Rank candidates by total score (highest first).

    Returns list of (candidate, score) tuples sorted by score.total descending.
    """
    paired = list(zip(candidates, scores))
    paired.sort(key=lambda x: x[1].total, reverse=True)
    return paired
