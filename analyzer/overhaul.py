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


def assess_overhaul(
    state_issues: list["CarStateIssue"],
    *,
    telemetry_envelope_distance: float | None = None,
    setup_cluster_distance: float | None = None,
) -> OverhaulAssessment:
    if not state_issues:
        return OverhaulAssessment(
            classification="minor_tweak",
            confidence=0.6,
            score=0.0,
            reasons=["No root-state issues exceeded the overhaul threshold."],
        )

    weighted_score = sum(issue.severity * issue.confidence for issue in state_issues)
    major_states = [issue for issue in state_issues if issue.severity >= 0.6 and issue.confidence >= 0.55]
    advisory_states = {"thermal_window_invalid"}
    core_states = [issue for issue in state_issues if issue.state_id not in advisory_states]
    core_weighted_score = sum(issue.severity * issue.confidence for issue in core_states)
    major_core_states = [issue for issue in major_states if issue.state_id not in advisory_states]
    platform_safety_states = {
        "front_platform_collapse_braking",
        "front_platform_near_limit_high_speed",
        "rear_platform_under_supported",
        "rear_platform_over_supported",
    }
    major_platform_safety = [issue for issue in major_core_states if issue.state_id in platform_safety_states]
    reset_confirming_states = {
        "rear_platform_under_supported",
        "rear_platform_over_supported",
        "front_contact_patch_undercambered",
        "entry_front_limited",
        "exit_traction_limited",
        "balance_asymmetric",
    }
    confirming_reset = [issue for issue in major_core_states if issue.state_id in reset_confirming_states]
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
    if telemetry_envelope_distance is not None:
        reasons.append(f"Telemetry envelope distance: {telemetry_envelope_distance:.2f}")
    if setup_cluster_distance is not None:
        reasons.append(f"Setup cluster distance: {setup_cluster_distance:.2f}")

    if (
        core_weighted_score >= 2.3
        and len(major_core_states) >= 3
        and len(major_platform_safety) >= 2
        and (len(confirming_reset) >= 1 or len(major_core_states) >= 4)
    ):
        classification = "baseline_reset"
    elif core_weighted_score >= 1.0 or (len(major_core_states) >= 2 and len(implicated_steps) >= 2):
        classification = "moderate_rework"
    else:
        classification = "minor_tweak"

    if classification != "baseline_reset":
        if (telemetry_envelope_distance or 0.0) >= 2.4 or (setup_cluster_distance or 0.0) >= 2.4:
            classification = "baseline_reset" if core_weighted_score >= 1.7 and len(major_platform_safety) >= 1 else "moderate_rework"
        elif classification == "minor_tweak" and (
            (telemetry_envelope_distance or 0.0) >= 1.5 or (setup_cluster_distance or 0.0) >= 1.5
        ):
            classification = "moderate_rework"

    if classification == "baseline_reset":
        confidence = min(0.95, 0.65 + len(major_core_states) * 0.08 + len(implicated_steps) * 0.04)
        reasons.append("State issues are broad and severe enough to justify resetting toward a healthier baseline.")
    elif classification == "moderate_rework":
        confidence = min(0.9, 0.55 + len(major_core_states) * 0.06 + len(implicated_steps) * 0.03)
        reasons.append("State issues span multiple subsystems; more than a single tweak is required.")
    else:
        confidence = min(0.85, 0.5 + core_weighted_score * 0.12)
        reasons.append("Primary issues look localized enough for targeted setup refinement.")

    return OverhaulAssessment(
        classification=classification,
        confidence=round(confidence, 3),
        score=round(core_weighted_score, 3),
        reasons=reasons,
    )
