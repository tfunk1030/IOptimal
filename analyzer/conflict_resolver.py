"""Conflict resolution engine for competing setup recommendations.

When multiple diagnoses recommend contradictory changes to the same parameter,
this module resolves conflicts using:
1. Priority-based resolution (safety > platform > balance > grip)
2. Physics-compatible compromise (middle ground for equal priorities)
3. Compensating actions (mitigate side effects of chosen resolution)
4. Pareto analysis (report tradeoffs when no clear winner)

Each conflict produces a ConflictResolution with full reasoning trace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analyzer.recommend import SetupChange
    from analyzer.diagnose import Problem


@dataclass
class ConflictResolution:
    """Resolution of a conflict between competing recommendations."""
    parameter: str
    conflicting_changes: list[SetupChange]
    resolution_strategy: str   # "priority" | "compromise" | "compensating" | "pareto"
    chosen_value: float
    tradeoff_explanation: str
    compensating_actions: list[SetupChange] = field(default_factory=list)
    pareto_alternatives: list[dict] = field(default_factory=list)  # [{value, scores}]


@dataclass
class ConflictReport:
    """Complete conflict analysis for a set of recommendations."""
    resolutions: list[ConflictResolution] = field(default_factory=list)
    resolved_changes: list[SetupChange] = field(default_factory=list)
    conflict_count: int = 0

    def summary(self, width: int = 63) -> str:
        lines = [
            "=" * width,
            "  CONFLICT RESOLUTION",
            "=" * width,
        ]

        if not self.resolutions:
            lines.append("  No conflicts detected.")
            lines.append("=" * width)
            return "\n".join(lines)

        lines.append(f"  {self.conflict_count} conflict(s) resolved:")
        lines.append("")

        for i, res in enumerate(self.resolutions, 1):
            lines.append(f"  [{i}] {res.parameter}")
            lines.append(f"      Strategy: {res.resolution_strategy}")

            for ch in res.conflicting_changes:
                lines.append(
                    f"      - Priority {ch.priority}: "
                    f"{ch.current:.1f} → {ch.recommended:.1f} {ch.units} "
                    f"({ch.reasoning[:50]}...)"
                )

            lines.append(f"      Resolved: {res.chosen_value:.1f}")
            lines.append(f"      Reason: {res.tradeoff_explanation}")

            if res.compensating_actions:
                for ca in res.compensating_actions:
                    lines.append(
                        f"      Compensate: {ca.parameter} "
                        f"{ca.current:.1f} → {ca.recommended:.1f} {ca.units}"
                    )

        lines.append("=" * width)
        return "\n".join(lines)


# ── Conflict detection ────────────────────────────────────────────────

def _detect_conflicts(changes: list[SetupChange]) -> dict[str, list[SetupChange]]:
    """Group changes by parameter and identify those with opposing directions."""
    groups: dict[str, list[SetupChange]] = {}
    for ch in changes:
        groups.setdefault(ch.parameter, []).append(ch)

    conflicts = {}
    for param, group in groups.items():
        if len(group) < 2:
            continue

        # Check if directions conflict
        directions = set()
        for ch in group:
            if ch.recommended > ch.current:
                directions.add("increase")
            elif ch.recommended < ch.current:
                directions.add("decrease")

        if len(directions) > 1:
            conflicts[param] = group

    return conflicts


# ── Physics-aware parameter coupling ──────────────────────────────────

# Indirect conflicts: changing one parameter worsens another objective
# Maps (parameter, direction) → list of (affected_parameter, effect)
INDIRECT_CONFLICTS = {
    ("front_heave_nmm", "increase"): [
        ("mechanical_grip", "decreases — stiffer spring reduces compliance"),
    ],
    ("front_heave_nmm", "decrease"): [
        ("platform_stability", "decreases — softer spring increases RH variance"),
    ],
    ("rear_arb_blade", "decrease"): [
        ("rear_stability", "decreases — softer ARB reduces rear roll stiffness"),
    ],
    ("front_ls_rbd", "increase"): [
        ("compliance", "decreases — stiffer rebound reduces bump absorption"),
    ],
}


# ── Compensating actions ──────────────────────────────────────────────

# When a resolution choice worsens another objective, compensate with a
# secondary parameter change
COMPENSATION_RULES: dict[tuple[str, str], dict] = {
    # Stiffen heave (for platform) → compensate with softer LS comp (for grip)
    ("front_heave_nmm", "increase"): {
        "parameter": "front_ls_comp",
        "direction": "decrease",
        "amount": 1,
        "units": "clicks",
        "reasoning": "Stiffer heave spring → compensate with softer LS compression for grip recovery",
        "step": 6,
    },
    # Stiffen rear ARB (for oversteer) → compensate with softer diff preload
    ("rear_arb_blade", "increase"): {
        "parameter": "diff_preload_nm",
        "direction": "decrease",
        "amount": 5,
        "units": "Nm",
        "reasoning": "Stiffer rear ARB → compensate with less diff preload to maintain rotation",
        "step": 4,
    },
}


# ── Resolution strategies ─────────────────────────────────────────────

def _resolve_by_priority(
    param: str, group: list[SetupChange]
) -> ConflictResolution:
    """Resolve by picking the highest-priority (lowest number) change."""
    group_sorted = sorted(group, key=lambda c: c.priority)
    winner = group_sorted[0]

    losers_desc = []
    for ch in group_sorted[1:]:
        losers_desc.append(
            f"priority {ch.priority} ({ch.reasoning[:40]}...) "
            f"overridden by priority {winner.priority}"
        )

    # Check for compensating action
    direction = "increase" if winner.recommended > winner.current else "decrease"
    comp_rule = COMPENSATION_RULES.get((param, direction))
    compensating = []
    if comp_rule:
        from analyzer.recommend import SetupChange as SC
        compensating.append(SC(
            parameter=comp_rule["parameter"],
            current=0,  # unknown, applied later
            recommended=comp_rule["amount"],
            units=comp_rule["units"],
            step=comp_rule["step"],
            reasoning=comp_rule["reasoning"],
            effect="Mitigate side effect of conflict resolution",
            priority=winner.priority,
            confidence="medium",
        ))

    return ConflictResolution(
        parameter=param,
        conflicting_changes=group,
        resolution_strategy="priority",
        chosen_value=winner.recommended,
        tradeoff_explanation=(
            f"Safety/priority wins: {winner.reasoning[:80]}. "
            f"Overrides: {'; '.join(losers_desc)}"
        ),
        compensating_actions=compensating,
    )


def _resolve_by_compromise(
    param: str, group: list[SetupChange]
) -> ConflictResolution:
    """Resolve by finding physics-compatible middle ground."""
    values = [ch.recommended for ch in group]
    weights = [1.0 / max(ch.priority, 1) for ch in group]  # higher priority = more weight
    total_weight = sum(weights)

    compromise = sum(v * w for v, w in zip(values, weights)) / total_weight

    return ConflictResolution(
        parameter=param,
        conflicting_changes=group,
        resolution_strategy="compromise",
        chosen_value=round(compromise, 1),
        tradeoff_explanation=(
            f"Weighted compromise between {len(group)} recommendations: "
            f"values {[f'{v:.1f}' for v in values]} → {compromise:.1f} "
            f"(weighted by priority)"
        ),
    )


def _resolve_with_pareto(
    param: str, group: list[SetupChange]
) -> ConflictResolution:
    """Report tradeoff without picking a clear winner (Pareto front)."""
    alternatives = []
    for ch in group:
        alternatives.append({
            "value": ch.recommended,
            "priority": ch.priority,
            "reasoning": ch.reasoning[:60],
            "confidence": ch.confidence,
        })

    # Default to highest-confidence value
    best = max(group, key=lambda c: {"high": 3, "medium": 2, "low": 1}[c.confidence])

    return ConflictResolution(
        parameter=param,
        conflicting_changes=group,
        resolution_strategy="pareto",
        chosen_value=best.recommended,
        tradeoff_explanation=(
            f"No clear priority winner. {len(group)} alternatives available. "
            f"Selected highest-confidence option ({best.confidence}). "
            f"Engineer should review tradeoffs."
        ),
        pareto_alternatives=alternatives,
    )


# ── Main resolution ───────────────────────────────────────────────────

def resolve_conflicts(changes: list[SetupChange]) -> ConflictReport:
    """Detect and resolve all conflicts in a set of recommendations.

    Args:
        changes: List of SetupChange objects from recommend()

    Returns:
        ConflictReport with resolutions and final resolved change list
    """
    conflicts = _detect_conflicts(changes)

    if not conflicts:
        return ConflictReport(
            resolved_changes=changes,
            conflict_count=0,
        )

    resolutions: list[ConflictResolution] = []
    resolved_params: dict[str, float] = {}

    for param, group in conflicts.items():
        priorities = [ch.priority for ch in group]
        min_priority = min(priorities)
        max_priority = max(priorities)

        if min_priority < max_priority:
            # Clear priority difference → priority wins
            res = _resolve_by_priority(param, group)
        elif all(ch.confidence == group[0].confidence for ch in group):
            # Same priority, same confidence → compromise
            res = _resolve_by_compromise(param, group)
        else:
            # Same priority, different confidence → Pareto analysis
            res = _resolve_with_pareto(param, group)

        resolutions.append(res)
        resolved_params[param] = res.chosen_value

    # Build final change list: non-conflicting + resolved
    final_changes: list[SetupChange] = []
    conflict_params = set(conflicts.keys())

    for ch in changes:
        if ch.parameter not in conflict_params:
            final_changes.append(ch)

    # Add resolved values (take the original change closest to resolved value)
    for param, resolved_value in resolved_params.items():
        group = conflicts[param]
        # Find the change closest to the resolved value
        closest = min(group, key=lambda c: abs(c.recommended - resolved_value))
        # Create a new change with the resolved value
        from copy import copy
        resolved_ch = copy(closest)
        resolved_ch.recommended = resolved_value
        resolved_ch.reasoning = f"[RESOLVED] {resolved_ch.reasoning}"
        final_changes.append(resolved_ch)

    # Add compensating actions from resolutions
    for res in resolutions:
        final_changes.extend(res.compensating_actions)

    # Sort by priority
    final_changes.sort(key=lambda c: (c.priority, c.parameter))

    return ConflictReport(
        resolutions=resolutions,
        resolved_changes=final_changes,
        conflict_count=len(conflicts),
    )
