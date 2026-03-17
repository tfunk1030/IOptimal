"""Decide whether the setup needs a tweak, moderate rework, or full reset.

Uses the severity and count of inferred car states, the number of
solver steps implicated, and signal confidence to classify the
overall setup health.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analyzer.state_inference import CarStateIssue


@dataclass
class OverhaulAssessment:
    """Classification of how much the setup needs to change."""

    classification: str  # minor_tweak | moderate_rework | baseline_reset
    confidence: float  # 0-1
    score: float  # composite severity score
    reasons: list[str] = field(default_factory=list)


def assess_overhaul(
    issues: list["CarStateIssue"],
    signal_confidence_mean: float = 0.8,
) -> OverhaulAssessment:
    """Classify setup health from inferred car states.

    Args:
        issues: List of CarStateIssue from state_inference.
        signal_confidence_mean: Average signal confidence across all
            key metrics (lowers overhaul confidence if data is weak).

    Returns:
        OverhaulAssessment with classification and reasons.
    """
    if not issues:
        return OverhaulAssessment(
            classification="minor_tweak",
            confidence=signal_confidence_mean,
            score=0.0,
            reasons=["No significant car state issues detected"],
        )

    # Composite score from severity and confidence
    total_severity = sum(i.severity * i.confidence for i in issues)
    max_severity = max(i.severity for i in issues)
    n_major = sum(1 for i in issues if i.severity > 0.5)
    n_issues = len(issues)

    # Count unique implicated solver steps
    all_steps: set[int] = set()
    for issue in issues:
        all_steps.update(issue.implicated_steps)
    n_steps = len(all_steps)

    reasons: list[str] = []

    # Classification logic
    if total_severity > 1.5 or n_major >= 3 or n_steps >= 4:
        classification = "baseline_reset"
        reasons.append(f"High total severity ({total_severity:.2f})")
        if n_major >= 3:
            reasons.append(f"{n_major} major issues (severity > 0.5)")
        if n_steps >= 4:
            reasons.append(f"{n_steps} solver steps implicated")
    elif total_severity > 0.7 or n_major >= 2 or n_steps >= 3:
        classification = "moderate_rework"
        reasons.append(f"Moderate total severity ({total_severity:.2f})")
        if n_major >= 2:
            reasons.append(f"{n_major} major issues")
        if n_steps >= 3:
            reasons.append(f"{n_steps} solver steps implicated")
    else:
        classification = "minor_tweak"
        reasons.append(f"Low total severity ({total_severity:.2f})")
        if n_issues > 0:
            reasons.append(f"{n_issues} minor issue(s) detected")

    # Add top issues to reasons
    for issue in issues[:3]:
        reasons.append(f"{issue.state_id}: severity={issue.severity:.2f}, conf={issue.confidence:.2f}")

    # Confidence is lower if signals are weak
    assessment_confidence = min(
        signal_confidence_mean,
        sum(i.confidence for i in issues) / len(issues) if issues else 0.5,
    )

    return OverhaulAssessment(
        classification=classification,
        confidence=round(assessment_confidence, 3),
        score=round(total_severity, 3),
        reasons=reasons,
    )
