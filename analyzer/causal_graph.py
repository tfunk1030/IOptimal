"""Causal reasoning engine for setup diagnosis.

Instead of mapping symptoms to fixes linearly (symptom → fix), this module
builds a directed acyclic graph (DAG) of cause → effect relationships.
Multiple symptoms may share a root cause, and the same symptom can have
multiple possible causes.

Key benefits:
1. Root cause consolidation: one fix addresses multiple symptoms
2. Disambiguation: supporting evidence distinguishes between possible causes
3. Causal chain reporting: explains the full reasoning path
4. Avoids redundant recommendations

The causal graph is defined from domain physics (not learned from data).

W5.3 (analyzer.md:A19): some root-cause nodes only apply to specific
suspension architectures (heave-bearing GTP cars vs coil-4-corner GT3).
We tag those nodes with `gtp_only` / `gt3_only` flags so downstream
consumers (e.g. `applicable_nodes(car)` and `analyze_causes(...)`) can
filter the graph by architecture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from analyzer.diagnose import Problem
    from car_model.cars import CarModel


# ── Causal Graph Definition ────────────────────────────────────────────

@dataclass
class CausalNode:
    """A node in the causal graph — either a root cause or an observable symptom."""
    id: str
    label: str
    node_type: str  # "root_cause" | "intermediate" | "symptom"
    category: str   # matches Problem.category for symptoms
    # Which setup parameter this root cause maps to (for root causes only)
    parameter: str = ""
    # Direction of the fix ("increase" | "decrease" | "")
    fix_direction: str = ""
    # W5.3 (A19): architecture restrictions.  When `gtp_only=True` the node
    # only applies to cars with a front heave element (GTP).  When
    # `gt3_only=True` the node only applies to coil-4-corner GT3 cars.
    # Both default to False (architecture-agnostic).
    gtp_only: bool = False
    gt3_only: bool = False


@dataclass
class CausalEdge:
    """A directed edge from cause to effect."""
    cause_id: str
    effect_id: str
    mechanism: str   # physics explanation of how cause produces effect
    strength: float = 1.0  # 0-1, how strong the causal link is


@dataclass
class RootCauseAnalysis:
    """Result of analyzing observed symptoms to find root causes."""
    root_cause: CausalNode
    explained_symptoms: list[str]       # symptom IDs explained by this root cause
    causal_chains: list[CausalChain]    # full chains from root to each symptom
    confidence: float                    # 0-1
    fix_summary: str                     # one-line fix description


@dataclass
class CausalChain:
    """A single cause → intermediate → ... → symptom chain."""
    nodes: list[str]      # ordered node IDs from root cause to symptom
    mechanisms: list[str]  # mechanism for each edge in the chain
    symptom_id: str


@dataclass
class CausalDiagnosis:
    """Complete causal analysis of all observed symptoms."""
    root_causes: list[RootCauseAnalysis]  # sorted by number of symptoms explained
    unexplained_symptoms: list[str]        # symptoms with no matching root cause
    disambiguation_notes: list[str]        # notes about ambiguous diagnoses

    def summary(self, width: int = 63) -> str:
        """ASCII summary of causal analysis."""
        lines = [
            "=" * width,
            "  CAUSAL ANALYSIS",
            "=" * width,
        ]

        for i, rca in enumerate(self.root_causes, 1):
            lines.append("")
            lines.append(f"  ROOT CAUSE {i}: {rca.root_cause.label}")
            lines.append(f"  Confidence: {rca.confidence:.0%}")
            lines.append(f"  Fix: {rca.fix_summary}")
            lines.append(f"  Explains {len(rca.explained_symptoms)} symptom(s):")

            for chain in rca.causal_chains:
                chain_str = " → ".join(chain.nodes)
                lines.append(f"    {chain_str}")
                for mech in chain.mechanisms:
                    lines.append(f"      ({mech})")

        if self.unexplained_symptoms:
            lines.append("")
            lines.append("  UNEXPLAINED SYMPTOMS:")
            for s in self.unexplained_symptoms:
                lines.append(f"    - {s}")

        if self.disambiguation_notes:
            lines.append("")
            lines.append("  DISAMBIGUATION NOTES:")
            for n in self.disambiguation_notes:
                lines.append(f"    - {n}")

        lines.append("=" * width)
        return "\n".join(lines)


# ── Static Causal Graph ───────────────────────────────────────────────
# Defined from domain physics knowledge (SKILL.md, per-car-quirks.md)

NODES: dict[str, CausalNode] = {
    # ── Root causes (setup parameters that are wrong) ──
    "heave_too_soft": CausalNode(
        "heave_too_soft", "Front heave spring too soft",
        "root_cause", "platform",
        parameter="front_heave_nmm", fix_direction="increase",
        gtp_only=True,  # W5.3 (A19): GT3 has no heave element
    ),
    "heave_too_stiff": CausalNode(
        "heave_too_stiff", "Front heave spring too stiff",
        "root_cause", "platform",
        parameter="front_heave_nmm", fix_direction="decrease",
        gtp_only=True,  # W5.3 (A19): GT3 has no heave element
    ),
    "third_too_soft": CausalNode(
        "third_too_soft", "Rear third spring too soft",
        "root_cause", "platform",
        parameter="rear_third_nmm", fix_direction="increase",
        gtp_only=True,  # W5.3 (A19): GT3 has no rear third element
    ),
    # W5.3 (A19): GT3-only equivalents — paired front/rear coil-over springs.
    # These map to `front_corner_spring_nmm` / `rear_spring_nmm` on
    # `CurrentSetup` (already populated by `setup_reader.from_ibt` for GT3 IBTs).
    "front_corner_spring_too_soft": CausalNode(
        "front_corner_spring_too_soft", "Front corner springs too soft",
        "root_cause", "platform",
        parameter="front_corner_spring_nmm", fix_direction="increase",
        gt3_only=True,
    ),
    "front_corner_spring_too_stiff": CausalNode(
        "front_corner_spring_too_stiff", "Front corner springs too stiff",
        "root_cause", "platform",
        parameter="front_corner_spring_nmm", fix_direction="decrease",
        gt3_only=True,
    ),
    "rear_corner_spring_too_soft": CausalNode(
        "rear_corner_spring_too_soft", "Rear corner springs too soft",
        "root_cause", "platform",
        parameter="rear_spring_nmm", fix_direction="increase",
        gt3_only=True,
    ),
    "rarb_too_stiff": CausalNode(
        "rarb_too_stiff", "Rear ARB too stiff",
        "root_cause", "balance",
        parameter="rear_arb_blade", fix_direction="decrease",
    ),
    "rarb_too_soft": CausalNode(
        "rarb_too_soft", "Rear ARB too soft",
        "root_cause", "balance",
        parameter="rear_arb_blade", fix_direction="increase",
    ),
    "rear_df_excess": CausalNode(
        "rear_df_excess", "Excessive rear downforce (aero balance)",
        "root_cause", "balance",
        parameter="rear_rh_at_speed_mm", fix_direction="increase",
    ),
    "front_df_excess": CausalNode(
        "front_df_excess", "Excessive front downforce (aero balance)",
        "root_cause", "balance",
        parameter="rear_rh_at_speed_mm", fix_direction="decrease",
    ),
    "camber_excess_front": CausalNode(
        "camber_excess_front", "Too much front negative camber",
        "root_cause", "thermal",
        parameter="front_camber_deg", fix_direction="increase",
    ),
    "camber_deficit_front": CausalNode(
        "camber_deficit_front", "Not enough front negative camber",
        "root_cause", "thermal",
        parameter="front_camber_deg", fix_direction="decrease",
    ),
    "pressure_high_front": CausalNode(
        "pressure_high_front", "Front tyre pressure too high",
        "root_cause", "thermal",
        parameter="front_cold_pressure_kpa", fix_direction="decrease",
    ),
    "ls_rebound_low": CausalNode(
        "ls_rebound_low", "LS rebound damping too low",
        "root_cause", "damper",
        parameter="front_ls_rbd", fix_direction="increase",
    ),
    "ls_rebound_high": CausalNode(
        "ls_rebound_high", "LS rebound damping too high",
        "root_cause", "damper",
        parameter="front_ls_rbd", fix_direction="decrease",
    ),
    "diff_preload_low": CausalNode(
        "diff_preload_low", "Differential preload too low",
        "root_cause", "balance",
        parameter="diff_preload_nm", fix_direction="increase",
    ),
    "brake_bias_forward": CausalNode(
        "brake_bias_forward", "Brake bias too far forward",
        "root_cause", "grip",
        parameter="brake_bias_pct", fix_direction="decrease",
    ),

    # ── Intermediate effects ──
    "excessive_rh_variance": CausalNode(
        "excessive_rh_variance", "Excessive ride height variance",
        "intermediate", "platform",
    ),
    "df_balance_oscillation": CausalNode(
        "df_balance_oscillation", "DF balance oscillates with ride height",
        "intermediate", "balance",
    ),
    "high_lltd": CausalNode(
        "high_lltd", "LLTD above target",
        "intermediate", "balance",
    ),
    "low_lltd": CausalNode(
        "low_lltd", "LLTD below target",
        "intermediate", "balance",
    ),
    "slow_platform_recovery": CausalNode(
        "slow_platform_recovery", "Slow platform recovery after bumps",
        "intermediate", "damper",
    ),
    "fast_platform_recovery": CausalNode(
        "fast_platform_recovery", "Platform overdamped (no compliance)",
        "intermediate", "damper",
    ),

    # ── Observable symptoms (match Problem symptom patterns) ──
    "symptom_front_rh_variance": CausalNode(
        "symptom_front_rh_variance", "Front RH variance high",
        "symptom", "platform",
    ),
    "symptom_rear_rh_variance": CausalNode(
        "symptom_rear_rh_variance", "Rear RH variance high",
        "symptom", "platform",
    ),
    "symptom_front_bottoming": CausalNode(
        "symptom_front_bottoming", "Front bottoming events",
        "symptom", "safety",
    ),
    "symptom_rear_bottoming": CausalNode(
        "symptom_rear_bottoming", "Rear bottoming events",
        "symptom", "safety",
    ),
    "symptom_vortex_burst": CausalNode(
        "symptom_vortex_burst", "Vortex burst events",
        "symptom", "safety",
    ),
    "symptom_understeer_all": CausalNode(
        "symptom_understeer_all", "Understeer (all speeds)",
        "symptom", "balance",
    ),
    "symptom_oversteer_all": CausalNode(
        "symptom_oversteer_all", "Oversteer (all speeds)",
        "symptom", "balance",
    ),
    "symptom_speed_gradient_us": CausalNode(
        "symptom_speed_gradient_us", "More understeer at high speed",
        "symptom", "balance",
    ),
    "symptom_speed_gradient_os": CausalNode(
        "symptom_speed_gradient_os", "More oversteer at high speed",
        "symptom", "balance",
    ),
    "symptom_body_slip_high": CausalNode(
        "symptom_body_slip_high", "High body slip angle",
        "symptom", "balance",
    ),
    "symptom_inconsistent_understeer": CausalNode(
        "symptom_inconsistent_understeer", "Lap-to-lap understeer variation",
        "symptom", "balance",
    ),
    "symptom_front_settle_slow": CausalNode(
        "symptom_front_settle_slow", "Front settle time too long",
        "symptom", "damper",
    ),
    "symptom_front_settle_fast": CausalNode(
        "symptom_front_settle_fast", "Front settle time too short",
        "symptom", "damper",
    ),
    "symptom_yaw_poor": CausalNode(
        "symptom_yaw_poor", "Poor yaw rate correlation",
        "symptom", "damper",
    ),
    "symptom_roll_rate_high": CausalNode(
        "symptom_roll_rate_high", "Excessive roll rate",
        "symptom", "damper",
    ),
    "symptom_inner_hot_front": CausalNode(
        "symptom_inner_hot_front", "Front inner tyre hot",
        "symptom", "thermal",
    ),
    "symptom_outer_hot_front": CausalNode(
        "symptom_outer_hot_front", "Front outer tyre hot",
        "symptom", "thermal",
    ),
    "symptom_front_lock": CausalNode(
        "symptom_front_lock", "Front braking slip",
        "symptom", "grip",
    ),
    "symptom_rear_traction_slip": CausalNode(
        "symptom_rear_traction_slip", "Rear traction slip",
        "symptom", "grip",
    ),
    "symptom_lltd_high": CausalNode(
        "symptom_lltd_high", "LLTD measured too high",
        "symptom", "balance",
    ),
    "symptom_lltd_low": CausalNode(
        "symptom_lltd_low", "LLTD measured too low",
        "symptom", "balance",
    ),
    "symptom_excursion_high": CausalNode(
        "symptom_excursion_high", "Front excursion near bottoming",
        "symptom", "platform",
    ),
}

EDGES: list[CausalEdge] = [
    # ── Heave spring too soft → cascade ──
    CausalEdge("heave_too_soft", "excessive_rh_variance",
               "Softer spring → larger ride height oscillation (σ ~ 1/√k)"),
    CausalEdge("heave_too_soft", "symptom_front_bottoming",
               "Softer spring → larger excursion → hits bump stops"),
    CausalEdge("heave_too_soft", "symptom_excursion_high",
               "Excursion p99 approaches dynamic ride height limit"),
    CausalEdge("heave_too_soft", "symptom_vortex_burst",
               "Excessive excursion drops front RH below aero stall threshold"),
    CausalEdge("excessive_rh_variance", "df_balance_oscillation",
               "RH oscillation → DF balance shifts with each cycle"),
    CausalEdge("excessive_rh_variance", "symptom_front_rh_variance",
               "Direct measurement of ride height standard deviation"),
    CausalEdge("df_balance_oscillation", "symptom_inconsistent_understeer",
               "Oscillating DF balance → lap-to-lap handling variation"),

    # ── Third spring too soft ──
    CausalEdge("third_too_soft", "symptom_rear_rh_variance",
               "Softer third → larger rear ride height oscillation"),
    CausalEdge("third_too_soft", "symptom_rear_bottoming",
               "Softer third → rear excursion exceeds dynamic RH"),

    # ── W5.3 (A19): GT3 corner spring root causes ──
    # Mirror the heave/third edges but skip the aero-floor symptoms
    # (`symptom_excursion_high`, `symptom_vortex_burst`) — those are
    # GTP-specific concepts tied to the front splitter+heave architecture
    # and have no direct GT3 equivalent today.
    CausalEdge("front_corner_spring_too_soft", "excessive_rh_variance",
               "Softer front coil → larger ride height oscillation (σ ~ 1/√k)"),
    CausalEdge("front_corner_spring_too_soft", "symptom_front_bottoming",
               "Softer front coil → larger excursion → hits bump rubbers"),
    CausalEdge("rear_corner_spring_too_soft", "symptom_rear_rh_variance",
               "Softer rear coil → larger rear ride height oscillation"),
    CausalEdge("rear_corner_spring_too_soft", "symptom_rear_bottoming",
               "Softer rear coil → rear excursion exceeds dynamic RH"),

    # ── ARB imbalance ──
    CausalEdge("rarb_too_stiff", "high_lltd",
               "Stiff rear ARB → more rear roll stiffness → higher LLTD"),
    CausalEdge("high_lltd", "symptom_understeer_all",
               "High LLTD → front axle overloaded in corners → understeer"),
    CausalEdge("high_lltd", "symptom_lltd_high",
               "Direct measurement of LLTD above target"),

    CausalEdge("rarb_too_soft", "low_lltd",
               "Soft rear ARB → less rear roll stiffness → lower LLTD"),
    CausalEdge("low_lltd", "symptom_oversteer_all",
               "Low LLTD → rear axle overloaded → oversteer at limit"),
    CausalEdge("low_lltd", "symptom_lltd_low",
               "Direct measurement of LLTD below target"),

    # ── Aero balance ──
    CausalEdge("rear_df_excess", "symptom_speed_gradient_us",
               "Too much rear DF → front light at speed → high-speed understeer"),
    CausalEdge("front_df_excess", "symptom_speed_gradient_os",
               "Too much front DF → rear light at speed → high-speed oversteer"),

    # ── Damper LS rebound ──
    CausalEdge("ls_rebound_low", "slow_platform_recovery",
               "Low LS rebound → insufficient damping of body motions"),
    CausalEdge("slow_platform_recovery", "symptom_front_settle_slow",
               "Underdamped platform oscillates instead of settling"),
    CausalEdge("slow_platform_recovery", "symptom_yaw_poor",
               "Platform oscillation decouples yaw from steering input"),
    CausalEdge("ls_rebound_low", "symptom_roll_rate_high",
               "Low LS rebound → insufficient control of weight transfer rate"),

    CausalEdge("ls_rebound_high", "fast_platform_recovery",
               "High LS rebound → overdamped, loses compliance"),
    CausalEdge("fast_platform_recovery", "symptom_front_settle_fast",
               "Overdamped: fast settle but tyre bounces off surface"),

    # ── Camber / thermal ──
    CausalEdge("camber_excess_front", "symptom_inner_hot_front",
               "Excessive camber loads inner edge → inner overheats"),
    CausalEdge("camber_deficit_front", "symptom_outer_hot_front",
               "Insufficient camber → outer edge works harder → outer overheats"),
    CausalEdge("pressure_high_front", "symptom_outer_hot_front",
               "High pressure → contact patch crowns → outer edge overloads",
               strength=0.7),

    # ── Diff / grip ──
    CausalEdge("diff_preload_low", "symptom_body_slip_high",
               "Low diff preload → rear axle splits easily → body slides"),
    CausalEdge("diff_preload_low", "symptom_rear_traction_slip",
               "Low preload → one rear wheel spins → slip ratio increases"),
    CausalEdge("brake_bias_forward", "symptom_front_lock",
               "Brake bias too far forward → front tyres lock first"),
]


# ── Symptom matching ──────────────────────────────────────────────────

def _match_problem_to_symptom(problem: Problem) -> str | None:
    """Map a diagnosed Problem to a symptom node ID in the causal graph."""
    symptom = problem.symptom.lower()
    category = problem.category

    if category == "safety":
        if "vortex burst" in symptom:
            return "symptom_vortex_burst"
        if "front bottoming" in symptom:
            return "symptom_front_bottoming"
        if "rear bottoming" in symptom:
            return "symptom_rear_bottoming"

    elif category == "platform":
        if "front rh variance" in symptom:
            return "symptom_front_rh_variance"
        if "rear rh variance" in symptom:
            return "symptom_rear_rh_variance"
        if "excursion" in symptom:
            return "symptom_excursion_high"

    elif category == "balance":
        if "speed gradient" in symptom:
            if problem.measured > 0:
                return "symptom_speed_gradient_us"
            return "symptom_speed_gradient_os"
        if "understeer" in symptom:
            if problem.measured > 0:
                return "symptom_understeer_all"
            return "symptom_oversteer_all"
        if "lltd" in symptom:
            if "too high" in problem.cause.lower():
                return "symptom_lltd_high"
            return "symptom_lltd_low"
        if "body slip" in symptom:
            return "symptom_body_slip_high"

    elif category == "damper":
        if "front settle" in symptom and problem.measured > 200:
            return "symptom_front_settle_slow"
        if "front settle" in symptom and problem.measured < 50:
            return "symptom_front_settle_fast"
        if "yaw" in symptom:
            return "symptom_yaw_poor"
        if "roll rate" in symptom:
            return "symptom_roll_rate_high"

    elif category == "thermal":
        if "inner hot" in symptom:
            if "F" in symptom[:3]:
                return "symptom_inner_hot_front"
        if "outer hot" in symptom:
            if "F" in symptom[:3]:
                return "symptom_outer_hot_front"

    elif category == "grip":
        if "rear traction" in symptom:
            return "symptom_rear_traction_slip"
        if "front braking" in symptom:
            return "symptom_front_lock"

    return None


# ── Graph traversal ───────────────────────────────────────────────────

def applicable_nodes(car: "CarModel") -> Iterator[CausalNode]:
    """Yield the subset of NODES that apply to the given car's architecture.

    W5.3 (A19): GT3 cars (no heave element) skip `gtp_only` nodes;
    GTP cars skip `gt3_only` nodes.  Architecture-agnostic nodes
    (the default) yield for every car.
    """
    is_gt3 = not car.suspension_arch.has_heave_third
    for node in NODES.values():
        if is_gt3 and node.gtp_only:
            continue
        if not is_gt3 and node.gt3_only:
            continue
        yield node


def _is_node_applicable(node: "CausalNode | None", car: "CarModel | None") -> bool:
    """Return True iff the node applies to `car`'s architecture (or `car is None`)."""
    if node is None:
        return False
    if car is None:
        return True
    is_gt3 = not car.suspension_arch.has_heave_third
    if is_gt3 and node.gtp_only:
        return False
    if not is_gt3 and node.gt3_only:
        return False
    return True


def _build_adjacency() -> dict[str, list[CausalEdge]]:
    """Build forward adjacency list: cause_id → [edges]."""
    adj: dict[str, list[CausalEdge]] = {}
    for edge in EDGES:
        adj.setdefault(edge.cause_id, []).append(edge)
    return adj


def _build_reverse_adjacency() -> dict[str, list[CausalEdge]]:
    """Build reverse adjacency list: effect_id → [edges from causes]."""
    rev: dict[str, list[CausalEdge]] = {}
    for edge in EDGES:
        rev.setdefault(edge.effect_id, []).append(edge)
    return rev


def _find_root_causes_for_symptom(
    symptom_id: str,
    reverse_adj: dict[str, list[CausalEdge]],
    visited: set[str] | None = None,
) -> list[tuple[str, list[CausalEdge]]]:
    """Trace backwards from a symptom to all root causes.

    Returns list of (root_cause_id, [edges in chain from root to symptom]).
    """
    if visited is None:
        visited = set()

    if symptom_id in visited:
        return []
    visited.add(symptom_id)

    results = []
    incoming = reverse_adj.get(symptom_id, [])

    for edge in incoming:
        cause_node = NODES.get(edge.cause_id)
        if cause_node is None:
            continue

        if cause_node.node_type == "root_cause":
            results.append((edge.cause_id, [edge]))
        else:
            # Recurse upward
            upstream = _find_root_causes_for_symptom(
                edge.cause_id, reverse_adj, visited
            )
            for root_id, chain_edges in upstream:
                results.append((root_id, chain_edges + [edge]))

    return results


def _build_causal_chain(root_id: str, edges: list[CausalEdge]) -> CausalChain:
    """Build a CausalChain from a root cause ID and edge list."""
    nodes = [root_id]
    mechanisms = []
    for edge in edges:
        nodes.append(edge.effect_id)
        mechanisms.append(edge.mechanism)

    return CausalChain(
        nodes=[NODES[n].label for n in nodes],
        mechanisms=mechanisms,
        symptom_id=nodes[-1],
    )


# ── Main analysis ─────────────────────────────────────────────────────

def analyze_causes(
    problems: list[Problem],
    car: "CarModel | None" = None,
) -> CausalDiagnosis:
    """Perform causal analysis on a list of diagnosed problems.

    Maps each problem to symptom nodes in the causal graph, traces
    backwards to root causes, and consolidates shared root causes.

    Args:
        problems: List of Problem objects from diagnose()
        car: Optional CarModel — when supplied, root causes whose
             `gtp_only`/`gt3_only` flags don't match the car's architecture
             are filtered out (W5.3:A19).  Backwards-compatible default
             (`None`) preserves the old behaviour and yields every node.

    Returns:
        CausalDiagnosis with root causes, causal chains, and unexplained symptoms
    """
    reverse_adj = _build_reverse_adjacency()

    # Map problems to symptom IDs
    symptom_map: dict[str, Problem] = {}  # symptom_id → Problem
    unmatched: list[str] = []

    for problem in problems:
        symptom_id = _match_problem_to_symptom(problem)
        if symptom_id:
            symptom_map[symptom_id] = problem
        else:
            unmatched.append(problem.symptom)

    # Find root causes for each matched symptom
    # root_cause_id → {symptom_ids, chains}
    root_cause_groups: dict[str, dict] = {}

    for symptom_id in symptom_map:
        traces = _find_root_causes_for_symptom(symptom_id, reverse_adj)

        for root_id, edges in traces:
            # W5.3 (A19): drop architecture-mismatched root causes (e.g. a
            # GT3 session must not surface `heave_too_soft`).
            root_node = NODES.get(root_id)
            if root_node is None or not _is_node_applicable(root_node, car):
                continue
            if root_id not in root_cause_groups:
                root_cause_groups[root_id] = {
                    "symptom_ids": set(),
                    "chains": [],
                }
            root_cause_groups[root_id]["symptom_ids"].add(symptom_id)
            root_cause_groups[root_id]["chains"].append(
                _build_causal_chain(root_id, edges)
            )

    # Build RootCauseAnalysis objects, sorted by symptoms explained (descending)
    analyses: list[RootCauseAnalysis] = []

    for root_id, data in root_cause_groups.items():
        root_node = NODES[root_id]
        n_symptoms = len(data["symptom_ids"])
        total_symptoms = len(symptom_map)
        confidence = min(1.0, n_symptoms / max(total_symptoms, 1) * 1.5)

        fix = f"{root_node.fix_direction.capitalize()} {root_node.parameter}"
        if root_node.fix_direction and root_node.parameter:
            fix = f"{root_node.fix_direction.capitalize()} {root_node.parameter}"
        else:
            fix = root_node.label

        analyses.append(RootCauseAnalysis(
            root_cause=root_node,
            explained_symptoms=sorted(data["symptom_ids"]),
            causal_chains=data["chains"],
            confidence=confidence,
            fix_summary=fix,
        ))

    # Sort: most symptoms explained first, then by confidence
    analyses.sort(key=lambda a: (-len(a.explained_symptoms), -a.confidence))

    # Disambiguation notes
    notes: list[str] = []

    # Check for symptoms with multiple root causes
    for symptom_id in symptom_map:
        traces = _find_root_causes_for_symptom(symptom_id, reverse_adj)
        # W5.3 (A19): filter out architecture-mismatched roots before
        # counting "multiple possible causes" for the disambiguation note.
        traces = [
            (root_id, edges) for root_id, edges in traces
            if _is_node_applicable(NODES.get(root_id), car)
        ]
        if len(traces) > 1:
            root_labels = [NODES[t[0]].label for t in traces]
            symptom_label = NODES[symptom_id].label
            strengths = [
                min(e.strength for e in edges) if edges else 1.0
                for _, edges in traces
            ]
            # Report the ambiguity with relative likelihood
            total_str = sum(strengths)
            parts = []
            for label, s in zip(root_labels, strengths):
                parts.append(f"{s/total_str:.0%} {label}")
            notes.append(
                f"'{symptom_label}' has multiple possible causes: "
                + ", ".join(parts)
            )

    return CausalDiagnosis(
        root_causes=analyses,
        unexplained_symptoms=unmatched,
        disambiguation_notes=notes,
    )
