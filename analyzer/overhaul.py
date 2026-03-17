from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analyzer.state_inference import CarStateIssue


@dataclass
class OverhaulAssessment:
    classification: str
    confidence: float
    score: float
    reasons: list[str] = field(default_factory=list)


def assess_overhaul(state_issues: list["CarStateIssue"]) -> OverhaulAssessment:
    if not state_issues:
        return OverhaulAssessment(
            classification="minor_tweak",
            confidence=0.6,
            score=0.0,
            reasons=["No root-state issues exceeded the overhaul threshold."],
        )

    weighted_score = sum(issue.severity * issue.confidence for issue in state_issues)
    major_states = [issue for issue in state_issues if issue.severity >= 0.6 and issue.confidence >= 0.55]
    implicated_steps = sorted({step for issue in state_issues for step in issue.implicated_steps})
    reasons: list[str] = []

    if major_states:
        reasons.append(
            "Major states: "
            + ", ".join(f"{issue.state_id} ({issue.severity:.2f}/{issue.confidence:.2f})" for issue in major_states[:4])
        )
    if implicated_steps:
        reasons.append(
            "Impacted solver steps: " + ", ".join(str(step) for step in implicated_steps)
        )

    if weighted_score >= 2.4 or (len(major_states) >= 3 and len(implicated_steps) >= 3):
        classification = "baseline_reset"
    elif weighted_score >= 1.1 or (len(major_states) >= 2 and len(implicated_steps) >= 2):
        classification = "moderate_rework"
    else:
        classification = "minor_tweak"

    if classification == "baseline_reset":
        confidence = min(0.95, 0.65 + len(major_states) * 0.08 + len(implicated_steps) * 0.04)
        reasons.append("State issues are broad and severe enough to justify resetting toward a healthier baseline.")
    elif classification == "moderate_rework":
        confidence = min(0.9, 0.55 + len(major_states) * 0.06 + len(implicated_steps) * 0.03)
        reasons.append("State issues span multiple subsystems; more than a single tweak is required.")
    else:
        confidence = min(0.85, 0.5 + weighted_score * 0.12)
        reasons.append("Primary issues look localized enough for targeted setup refinement.")

    return OverhaulAssessment(
        classification=classification,
        confidence=round(confidence, 3),
        score=round(weighted_score, 3),
        reasons=reasons,
    )
