"""Build a comparison synthesis result from the reasoning engine."""

from __future__ import annotations

from dataclasses import dataclass, field

from car_model.cars import CarModel
from comparison.compare import ComparisonResult
from comparison.score import ScoringResult
from solver.modifiers import SolverModifiers


@dataclass
class SetupExplanation:
    """Why a specific parameter value was chosen in the synthesized setup."""

    parameter: str
    value: str
    reasoning: str
    influenced_by: list[str]


@dataclass
class SynthesisResult:
    """Full synthesis output including solver results and reasoning."""

    step1: object
    step2: object
    step3: object
    step4: object
    step5: object
    step6: object
    supporting: object
    modifiers: SolverModifiers
    explanations: list[SetupExplanation] = field(default_factory=list)
    source_sessions: dict[str, list[str]] = field(default_factory=dict)
    confidence: dict[str, str] = field(default_factory=dict)
    wing_angle: float = 0.0
    fuel_l: float = 89.0
    best_session_label: str = ""
    authority_session_label: str = ""
    selected_candidate_family: str | None = None
    selected_candidate_score: float | None = None
    solve_basis: str = ""
    solver_notes: list[str] = field(default_factory=list)


def synthesize_setup(
    comparison: ComparisonResult,
    scoring: ScoringResult,
    car: CarModel,
    wing: float | None = None,
    fuel: float = 89.0,
    balance_target: float = 50.14,
) -> SynthesisResult:
    """Create the comparison synthesis result from the reasoning engine."""
    from pipeline.reason import reason_and_solve

    state = reason_and_solve(
        car_name=car.canonical_name,
        ibt_paths=[session.ibt_path for session in comparison.sessions],
        wing=wing,
        fuel=fuel,
        balance_target=balance_target,
        verbose=False,
        emit_report=False,
    )

    final_values = (
        state.final_step1,
        state.final_step2,
        state.final_step3,
        state.final_step4,
        state.final_step5,
        state.final_step6,
        state.final_supporting,
    )
    if any(value is None for value in final_values):
        raise RuntimeError("reasoning synthesis did not produce a complete final solve")

    best_session = state.sessions[state.best_session_idx]
    authority_session = state.sessions[state.authority_session_idx]
    modifiers = state.final_modifiers if state.final_modifiers is not None else SolverModifiers()

    authority_score = next(
        (row for row in state.authority_scores if row["session"] == authority_session.label),
        None,
    )

    def _tag(value: float | None, *, high: float = 0.75, medium: float = 0.45) -> str:
        if value is None:
            return "medium"
        if value >= high:
            return "high"
        if value >= medium:
            return "medium"
        return "low"

    explanations = [
        SetupExplanation(
            parameter="Solve Basis",
            value=state.solve_basis,
            reasoning=(
                f"Authority session {authority_session.label} was used for the final solve; "
                f"benchmark best remains {best_session.label}."
            ),
            influenced_by=[authority_session.label, best_session.label],
        )
    ]
    if state.final_selected_candidate_family is not None:
        explanations.append(
            SetupExplanation(
                parameter="Candidate Family",
                value=state.final_selected_candidate_family,
                reasoning=(
                    "The final setup comes from the rematerialized candidate family "
                    "selected by prediction, legality, and context-aware ranking."
                ),
                influenced_by=[authority_session.label, best_session.label],
            )
        )
    if authority_session.label in state.envelope_distances and authority_session.label in state.setup_distances:
        envelope = state.envelope_distances[authority_session.label].total_score
        setup = state.setup_distances[authority_session.label].distance_score
        explanations.append(
            SetupExplanation(
                parameter="Healthy Family Fit",
                value=f"env={envelope:.2f}, setup={setup:.2f}",
                reasoning=(
                    "Authority selection and candidate ranking were biased toward sessions "
                    "closer to the healthy telemetry envelope and setup family."
                ),
                influenced_by=[authority_session.label],
            )
        )

    source_sessions: dict[str, list[str]] = {
        "authority_session": [authority_session.label],
        "best_session": [best_session.label],
    }
    if state.setup_cluster is not None:
        source_sessions["healthy_cluster"] = list(state.setup_cluster.member_sessions)

    confidence: dict[str, str] = {
        "authority": _tag(authority_score["score"] if authority_score is not None else None),
        "telemetry": _tag(
            authority_session.session_context.overall_score
            if authority_session.session_context is not None
            else None
        ),
        "candidate_family": (
            "high"
            if state.final_selected_candidate_applied and (state.final_selected_candidate_score or 0.0) >= 0.6
            else "medium"
            if state.final_selected_candidate_applied
            else "low"
        ),
        "validation": "high" if getattr(state.legal_validation, "valid", False) else "low",
    }

    return SynthesisResult(
        step1=state.final_step1,
        step2=state.final_step2,
        step3=state.final_step3,
        step4=state.final_step4,
        step5=state.final_step5,
        step6=state.final_step6,
        supporting=state.final_supporting,
        modifiers=modifiers,
        explanations=explanations,
        source_sessions=source_sessions,
        confidence=confidence,
        wing_angle=state.final_wing_angle or (wing or authority_session.setup.wing_angle_deg),
        fuel_l=state.final_fuel_l or fuel,
        best_session_label=best_session.label,
        authority_session_label=authority_session.label,
        selected_candidate_family=state.final_selected_candidate_family,
        selected_candidate_score=state.final_selected_candidate_score,
        solve_basis=state.solve_basis,
        solver_notes=list(state.solver_notes),
    )
