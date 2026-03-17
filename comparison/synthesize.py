"""Synthesize an optimal setup from multiple session analyses.

Takes the comparison and scoring results, merges the best aspects
of each session into solver modifiers, then runs the full 6-step
physics solver to produce a new setup.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from aero_model import load_car_surfaces
from aero_model.gradient import compute_gradients
from car_model.cars import CarModel
from comparison.compare import ComparisonResult, SessionAnalysis
from comparison.score import CATEGORY_WEIGHTS, ScoringResult, SessionScore
from output.setup_writer import write_sto
from pipeline.produce import _apply_damper_modifiers
from solver.arb_solver import ARBSolver
from solver.corner_spring_solver import CornerSpringSolver
from solver.damper_solver import DamperSolver
from solver.heave_solver import HeaveSolver
from solver.modifiers import SolverModifiers, compute_modifiers
from solver.rake_solver import RakeSolver
from solver.supporting_solver import SupportingSolver
from solver.wheel_geometry_solver import WheelGeometrySolver


# ── Data structures ─────────────────────────────────────────────


@dataclass
class SetupExplanation:
    """Why a specific parameter value was chosen in the synthesized setup."""

    parameter: str
    value: str
    reasoning: str
    influenced_by: list[str]  # session labels


@dataclass
class SynthesisResult:
    """Full synthesis output including solver results and reasoning."""

    step1: object  # RakeSolution
    step2: object  # HeaveSolution
    step3: object  # CornerSpringSolution
    step4: object  # ARBSolution
    step5: object  # WheelGeometrySolution
    step6: object  # DamperSolution
    supporting: object  # SupportingSolution
    modifiers: SolverModifiers
    explanations: list[SetupExplanation] = field(default_factory=list)
    source_sessions: dict[str, list[str]] = field(default_factory=dict)
    confidence: dict[str, str] = field(default_factory=dict)
    wing_angle: float = 0.0
    fuel_l: float = 89.0
    best_session_label: str = ""


# ── Modifier merging ────────────────────────────────────────────


def _merge_modifiers(
    comparison: ComparisonResult,
    scoring: ScoringResult,
) -> tuple[SolverModifiers, list[SetupExplanation]]:
    """Merge solver modifiers from all sessions, weighted by category scores.

    For each modifier field, take a weighted average across sessions where
    the weight comes from the session's score in the most relevant category.
    """
    sessions = comparison.sessions
    scores = scoring.scores
    n = len(sessions)

    # Build score lookup: session label → SessionScore
    score_by_label: dict[str, SessionScore] = {
        ss.session.label: ss for ss in scores
    }

    # Compute per-session modifiers from their telemetry
    per_session_mods: list[SolverModifiers] = []
    for sess in sessions:
        mods = compute_modifiers(sess.diagnosis, sess.driver, sess.measured)
        per_session_mods.append(mods)

    # Weighted merge for each modifier field
    explanations: list[SetupExplanation] = []

    def _weighted_avg(
        values: list[float],
        weights: list[float],
        param_name: str,
        category: str,
    ) -> tuple[float, list[str]]:
        """Compute weighted average and track which sessions influenced it."""
        total_w = sum(weights)
        if total_w == 0:
            return 0.0, []
        result = sum(v * w for v, w in zip(values, weights)) / total_w
        # Sessions that contributed meaningfully (weight > avg)
        avg_w = total_w / len(weights) if weights else 0
        influencers = [
            sessions[i].label
            for i, w in enumerate(weights)
            if w > avg_w * 0.5
        ]
        return result, influencers

    # Category-relevant weights for each modifier
    # df_balance_offset → balance scores
    balance_weights = [score_by_label[s.label].category_scores.get("balance", 0.5) for s in sessions]
    df_offset, df_sources = _weighted_avg(
        [m.df_balance_offset_pct for m in per_session_mods],
        balance_weights, "df_balance_offset", "balance",
    )

    # heave floors → damper/platform scores
    platform_weights = [score_by_label[s.label].category_scores.get("damper_platform", 0.5) for s in sessions]
    heave_floors = [m.front_heave_min_floor_nmm for m in per_session_mods]
    # For floors, take the max (most conservative) rather than average
    front_heave_floor = max(heave_floors) if any(h > 0 for h in heave_floors) else 0.0
    rear_third_floors = [m.rear_third_min_floor_nmm for m in per_session_mods]
    rear_third_floor = max(rear_third_floors) if any(h > 0 for h in rear_third_floors) else 0.0

    heave_sources = [sessions[i].label for i, h in enumerate(heave_floors) if h > 0]

    # lltd_offset → balance + grip
    grip_weights = [score_by_label[s.label].category_scores.get("grip", 0.5) for s in sessions]
    combined_balance_grip = [(b + g) / 2.0 for b, g in zip(balance_weights, grip_weights)]
    lltd_offset, lltd_sources = _weighted_avg(
        [m.lltd_offset for m in per_session_mods],
        combined_balance_grip, "lltd_offset", "balance+grip",
    )

    # damper offsets → damper/platform scores
    front_ls_rbd, rbd_sources = _weighted_avg(
        [float(m.front_ls_rbd_offset) for m in per_session_mods],
        platform_weights, "front_ls_rbd_offset", "damper_platform",
    )
    rear_ls_rbd, _ = _weighted_avg(
        [float(m.rear_ls_rbd_offset) for m in per_session_mods],
        platform_weights, "rear_ls_rbd_offset", "damper_platform",
    )
    front_hs_comp, _ = _weighted_avg(
        [float(m.front_hs_comp_offset) for m in per_session_mods],
        platform_weights, "front_hs_comp_offset", "damper_platform",
    )
    rear_hs_comp, _ = _weighted_avg(
        [float(m.rear_hs_comp_offset) for m in per_session_mods],
        platform_weights, "rear_hs_comp_offset", "damper_platform",
    )

    # damping ratio scale → weighted by lap time + platform
    lap_weights = [score_by_label[s.label].category_scores.get("lap_time", 0.5) for s in sessions]
    combined_lap_platform = [(l + p) / 2.0 for l, p in zip(lap_weights, platform_weights)]
    damp_scale, damp_sources = _weighted_avg(
        [m.damping_ratio_scale for m in per_session_mods],
        combined_lap_platform, "damping_ratio_scale", "lap_time+platform",
    )

    # Build merged modifier reasons
    reasons: list[str] = []
    if abs(df_offset) > 0.01:
        reasons.append(f"Merged DF balance offset: {df_offset:+.2f}% (from {', '.join(df_sources)})")
    if front_heave_floor > 0:
        reasons.append(f"Heave floor: {front_heave_floor:.0f} N/mm (from {', '.join(heave_sources)})")
    if abs(lltd_offset) > 0.005:
        reasons.append(f"Merged LLTD offset: {lltd_offset:+.3f} (from {', '.join(lltd_sources)})")
    if abs(damp_scale - 1.0) > 0.01:
        reasons.append(f"Damping ratio scale: {damp_scale:.3f} (from {', '.join(damp_sources)})")

    merged = SolverModifiers(
        df_balance_offset_pct=df_offset,
        front_heave_min_floor_nmm=front_heave_floor,
        rear_third_min_floor_nmm=rear_third_floor,
        lltd_offset=lltd_offset,
        front_ls_rbd_offset=round(front_ls_rbd),
        rear_ls_rbd_offset=round(rear_ls_rbd),
        front_hs_comp_offset=round(front_hs_comp),
        rear_hs_comp_offset=round(rear_hs_comp),
        damping_ratio_scale=damp_scale,
        reasons=reasons,
    )

    # Build explanations
    if abs(df_offset) > 0.01:
        explanations.append(SetupExplanation(
            parameter="DF Balance Target",
            value=f"{df_offset:+.2f}% offset",
            reasoning=f"Weighted merge of balance adjustments across sessions. "
                      f"Best-balance session contributed most.",
            influenced_by=df_sources,
        ))
    if front_heave_floor > 0:
        explanations.append(SetupExplanation(
            parameter="Front Heave Floor",
            value=f"{front_heave_floor:.0f} N/mm minimum",
            reasoning=f"Conservative floor from sessions with bottoming events. "
                      f"Ensures platform stability across conditions.",
            influenced_by=heave_sources,
        ))
    if abs(lltd_offset) > 0.005:
        explanations.append(SetupExplanation(
            parameter="LLTD Offset",
            value=f"{lltd_offset:+.3f}",
            reasoning=f"Weighted merge of balance + grip adjustments. "
                      f"Targets the LLTD that produced best combined grip and balance.",
            influenced_by=lltd_sources,
        ))

    return merged, explanations


# ── Full synthesis ──────────────────────────────────────────────


def synthesize_setup(
    comparison: ComparisonResult,
    scoring: ScoringResult,
    car: CarModel,
    wing: float | None = None,
    fuel: float = 89.0,
    balance_target: float = 50.14,
) -> SynthesisResult:
    """Create an optimal setup by combining insights from all sessions.

    Algorithm:
    1. Merge solver modifiers from all sessions (weighted by scores)
    2. Select best wing angle and track profile
    3. Run full 6-step solver with merged modifiers
    4. Run supporting parameter solver with best driver profile
    5. Generate explanations for each decision

    Args:
        comparison: Multi-session comparison data
        scoring: Session scores and rankings
        car: Car model
        wing: Wing angle override (auto-selects best if None)
        fuel: Fuel load in liters
        balance_target: Base DF balance target %
    """
    sessions = comparison.sessions
    best_score = scoring.scores[0]  # rank 1 = best overall
    best_session = best_score.session

    # Step 1: Select wing angle
    if wing is None:
        # Use the wing from the best-performing session
        wing = best_session.wing_angle
    wing_source = best_session.label

    # Step 2: Select track profile from fastest session
    fastest_idx = min(range(len(sessions)), key=lambda i: sessions[i].lap_time_s)
    track = sessions[fastest_idx].track

    # Step 3: Merge modifiers
    merged_mods, explanations = _merge_modifiers(comparison, scoring)

    # Step 4: Load aero surfaces
    surfaces = load_car_surfaces(car.canonical_name)
    if wing not in surfaces:
        available = sorted(surfaces.keys())
        closest = min(available, key=lambda w: abs(w - wing))
        print(f"  Wing {wing}° not available, using closest: {closest}°")
        wing = closest
    surface = surfaces[wing]

    # Step 5: Run 6-step solver with merged modifiers
    target_balance = balance_target + merged_mods.df_balance_offset_pct

    # Step 1: Rake
    rake_solver = RakeSolver(car, surface, track)
    step1 = rake_solver.solve(
        target_balance=target_balance,
        fuel_load_l=fuel,
        pin_front_min=True,
    )

    # Step 2: Heave
    heave_solver = HeaveSolver(car, track)
    step2 = heave_solver.solve(
        dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
        dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
    )
    if merged_mods.front_heave_min_floor_nmm > 0 and step2.front_heave_nmm < merged_mods.front_heave_min_floor_nmm:
        step2 = heave_solver.solve(
            dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
            dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
            front_heave_floor_nmm=merged_mods.front_heave_min_floor_nmm,
            rear_third_floor_nmm=merged_mods.rear_third_min_floor_nmm,
        )
    elif merged_mods.rear_third_min_floor_nmm > 0 and step2.rear_third_nmm < merged_mods.rear_third_min_floor_nmm:
        step2 = heave_solver.solve(
            dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
            dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
            front_heave_floor_nmm=merged_mods.front_heave_min_floor_nmm,
            rear_third_floor_nmm=merged_mods.rear_third_min_floor_nmm,
        )

    # Step 3: Corner Springs
    corner_solver = CornerSpringSolver(car, track)
    step3 = corner_solver.solve(
        front_heave_nmm=step2.front_heave_nmm,
        rear_third_nmm=step2.rear_third_nmm,
        fuel_load_l=fuel,
    )

    # Step 4: ARBs
    arb_solver = ARBSolver(car, track)
    step4 = arb_solver.solve(
        front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
        rear_wheel_rate_nmm=step3.rear_spring_rate_nmm,
        lltd_offset=merged_mods.lltd_offset,
    )

    # Step 5: Geometry
    geom_solver = WheelGeometrySolver(car, track)
    step5 = geom_solver.solve(
        k_roll_total_nm_deg=step4.k_roll_front_total + step4.k_roll_rear_total,
        front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
        rear_wheel_rate_nmm=step3.rear_spring_rate_nmm,
    )

    # Step 6: Dampers
    damper_solver = DamperSolver(car, track)
    step6 = damper_solver.solve(
        front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
        rear_wheel_rate_nmm=step3.rear_spring_rate_nmm,
        front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
        rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
        fuel_load_l=fuel,
        damping_ratio_scale=merged_mods.damping_ratio_scale,
        measured=best_session.measured,
    )
    _apply_damper_modifiers(step6, merged_mods, car)

    # Supporting parameters: use the best overall session's driver + telemetry
    supporting_solver = SupportingSolver(
        car,
        best_session.driver,
        best_session.measured,
        best_session.diagnosis,
        current_setup=best_session.setup,
    )
    supporting = supporting_solver.solve()

    # Build source tracking
    source_sessions: dict[str, list[str]] = {
        "wing_angle": [wing_source],
        "track_profile": [sessions[fastest_idx].label],
        "driver_profile": [best_session.label],
    }

    # Confidence assessment
    n = len(sessions)
    confidence: dict[str, str] = {
        "ride_heights": "high" if n >= 3 else "medium",
        "springs": "high" if n >= 3 else "medium",
        "arbs": "medium",
        "geometry": "medium",
        "dampers": "medium" if n >= 3 else "low",
        "supporting": "medium",
    }

    # Add step-level explanations
    explanations.append(SetupExplanation(
        parameter="Wing Angle",
        value=f"{wing}°",
        reasoning=f"Selected from best-performing session ({wing_source}).",
        influenced_by=[wing_source],
    ))
    explanations.append(SetupExplanation(
        parameter="Track Profile",
        value=f"From fastest lap ({sessions[fastest_idx].lap_time_s:.3f}s)",
        reasoning=f"Fastest lap session provides most representative driving data.",
        influenced_by=[sessions[fastest_idx].label],
    ))

    return SynthesisResult(
        step1=step1,
        step2=step2,
        step3=step3,
        step4=step4,
        step5=step5,
        step6=step6,
        supporting=supporting,
        modifiers=merged_mods,
        explanations=explanations,
        source_sessions=source_sessions,
        confidence=confidence,
        wing_angle=wing,
        fuel_l=fuel,
        best_session_label=best_session.label,
    )
