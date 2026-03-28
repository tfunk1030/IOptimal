"""Two-stage legal-manifold search engine.

Stage 1 — Family generation:
  Physics baseline + edge-anchor families seeded into the legal space.
  Each family samples candidates around its theme.

Stage 2 — Local expansion + scoring:
  Evaluate all candidates with ObjectiveFunction + legality checks.
  Retain top-K, including unconventional-but-legal setups.

Usage:
    from solver.legal_search import run_legal_search, LegalSearchResult
    result = run_legal_search(car, track, baseline_params, budget=1000)
    print(result.summary())
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from car_model.cars import CarModel
from solver.candidate_search import canonical_params_to_overrides
from solver.legal_space import LegalSpace, LegalCandidate, compute_perch_offsets
from solver.legality_engine import validate_candidate_legality
from solver.objective import ObjectiveFunction, CandidateEvaluation
from solver.scenario_profiles import get_scenario_profile, prediction_passes_sanity, resolve_scenario_name
from solver.solve_chain import SolveChainInputs, SolveChainResult, materialize_overrides
from track_model.profile import TrackProfile


# ── Edge-anchor family definitions ─────────────────────────────────────────
# Each family pushes parameters toward a specific extreme to explore
# unconventional but legal parts of the manifold.

EDGE_FAMILIES: dict[str, dict[str, str]] = {
    "min_drag": {
        "description": "Minimize drag — low wing, stiff heave, moderate rake",
        "front_heave_spring_nmm": "high",
        "rear_third_spring_nmm": "high",
        "front_camber_deg": "mid",
        "rear_camber_deg": "mid",
    },
    "max_platform": {
        "description": "Maximum aero platform stability",
        "front_heave_spring_nmm": "high",
        "rear_third_spring_nmm": "high",
        "front_ls_comp": "high",
        "rear_ls_comp": "high",
        "front_hs_comp": "high",
        "rear_hs_comp": "high",
    },
    "max_rotation": {
        "description": "Maximum yaw rotation — loose rear",
        "rear_arb_blade": "low",
        "front_arb_blade": "high",
        "diff_preload_nm": "low",
        "rear_camber_deg": "high",
    },
    "max_stability": {
        "description": "Maximum straight-line and braking stability",
        "rear_arb_blade": "high",
        "front_arb_blade": "low",
        "diff_preload_nm": "high",
        "front_ls_comp": "high",
    },
    "extreme_soft_mech": {
        "description": "Ultra-soft mechanical setup for grip",
        "front_heave_spring_nmm": "low",
        "rear_third_spring_nmm": "low",
        "rear_spring_rate_nmm": "low",
        "front_ls_comp": "low",
        "rear_ls_comp": "low",
    },
    "extreme_stiff_aero": {
        "description": "Ultra-stiff aero platform, sacrifice mech grip",
        "front_heave_spring_nmm": "high",
        "rear_third_spring_nmm": "high",
        "rear_spring_rate_nmm": "high",
        "front_hs_comp": "high",
        "rear_hs_comp": "high",
    },
}


def _resolve_edge_value(
    dim_name: str,
    direction: str,
    space: LegalSpace,
) -> float:
    """Resolve 'low'/'mid'/'high' to actual values from the legal space."""
    dim = space[dim_name]
    if direction == "low":
        return dim.lo
    elif direction == "high":
        return dim.hi
    else:  # mid
        return dim.snap((dim.lo + dim.hi) / 2)


def _generate_family_seeds(
    space: LegalSpace,
    baseline_params: dict[str, float],
    budget_per_family: int,
    rng: random.Random,
    car: CarModel | None = None,
) -> list[LegalCandidate]:
    """Stage 1: Generate seeded candidates from baseline + edge families.

    Uses Sobol quasi-random sampling for uniform scatter (much better
    space coverage than pseudorandom). Auto-computes dependent perch
    offsets for all candidates when a car model is available.
    """
    all_candidates: list[LegalCandidate] = []

    # Family 0: Physics baseline neighborhood
    baseline_cands = space.sample_seeded(
        baseline_params,
        n=budget_per_family,
        perturbation=0.12,
        seed=rng.randint(0, 2**31),
    )
    for c in baseline_cands:
        c.family = "physics_baseline"
    all_candidates.extend(baseline_cands)

    # Family 1-N: Edge anchor families
    for family_name, overrides in EDGE_FAMILIES.items():
        # Build seed params: start from baseline, push specified dims to edge
        seed = dict(baseline_params)
        for dim_name, direction in overrides.items():
            if dim_name == "description":
                continue
            if dim_name in space._dim_map:
                seed[dim_name] = _resolve_edge_value(dim_name, direction, space)

        # Sample around the edge seed with wider perturbation
        cands = space.sample_seeded(
            seed,
            n=budget_per_family,
            perturbation=0.20,
            seed=rng.randint(0, 2**31),
        )
        for c in cands:
            c.family = family_name
            c.is_extreme = True
        all_candidates.extend(cands)

    # Sobol scatter: quasi-random for much better coverage than uniform
    # Sobol fills the space more evenly, avoiding clumps and gaps
    tier_a_keys = [d.name for d in space.tier_a()]
    sobol_budget = budget_per_family * 2  # give Sobol extra budget
    sobol_samples = space.sobol_sample(
        tier_a_keys, sobol_budget, seed=rng.randint(0, 2**31)
    )
    for s in sobol_samples:
        all_candidates.append(LegalCandidate(
            params=s, family="sobol_scatter"
        ))

    # Also keep some uniform random for diversity
    uniform = space.sample_uniform(
        n=budget_per_family // 2,
        seed=rng.randint(0, 2**31),
    )
    for c in uniform:
        c.family = "uniform_scatter"
    all_candidates.extend(uniform)

    # Auto-compute dependent perch offsets for all candidates
    if car is not None:
        for cand in all_candidates:
            perches = compute_perch_offsets(cand.params, car)
            cand.params.update(perches)

    return all_candidates


def _evaluate_candidates(
    candidates: list[LegalCandidate],
    car: CarModel,
    track_name: str,
    objective: ObjectiveFunction,
    solver_result: dict | None = None,
    measured=None,
    driver_profile=None,
    session_count: int = 0,
) -> list[CandidateEvaluation]:
    """Stage 2: Score and filter all candidates."""
    evaluations: list[CandidateEvaluation] = []

    for cand in candidates:
        # Fast legality check
        legality = validate_candidate_legality(cand.params, car)

        # Score via objective function
        ev = objective.evaluate(
            params=cand.params,
            family=cand.family,
            solver_result=solver_result,
            measured=measured,
            driver_profile=driver_profile,
            session_count=session_count,
        )

        # Merge legality info
        if legality.hard_veto:
            ev.hard_vetoed = True
            ev.veto_reasons.extend(legality.hard_veto_reasons)
        ev.soft_penalties.extend(legality.soft_penalties)

        evaluations.append(ev)

    return evaluations


@dataclass
class LegalSearchResult:
    """Complete result of a legal-manifold search."""
    all_evaluations: list[CandidateEvaluation]
    best_robust: CandidateEvaluation | None = None
    best_aggressive: CandidateEvaluation | None = None
    best_weird: CandidateEvaluation | None = None
    accepted_evaluations: list[CandidateEvaluation] = field(default_factory=list)
    accepted_best: CandidateEvaluation | None = None
    accepted_best_result: SolveChainResult | None = None
    accepted_candidates_count: int = 0
    scenario_profile: str | None = None
    acceptance_notes: list[str] = field(default_factory=list)
    vetoed_count: int = 0
    total_evaluated: int = 0
    families_searched: list[str] = field(default_factory=list)
    # Grid search metadata (populated when using exhaustive/maximum mode)
    layer_times: dict[str, float] = field(default_factory=dict)
    layer_best_scores: dict[str, float] = field(default_factory=dict)
    locally_optimal: bool = False  # True if Layer 4 polish was applied

    def summary(self) -> str:
        lines = [
            "=" * 63,
            "  LEGAL-MANIFOLD SEARCH RESULTS",
            "=" * 63,
            f"  Total evaluated:  {self.total_evaluated}",
            f"  Vetoed (hard):    {self.vetoed_count}",
            f"  Families:         {', '.join(self.families_searched)}",
        ]
        if self.scenario_profile is not None:
            lines.append(f"  Scenario:         {self.scenario_profile}")
        lines.append(f"  Fully accepted:   {self.accepted_candidates_count}")
        if self.locally_optimal:
            lines.append("  Local optimality: GUARANTEED (Layer 4 polish applied)")
        if self.layer_times:
            lines.append("  Time per layer:")
            for layer_name, secs in sorted(self.layer_times.items()):
                lines.append(f"    {layer_name}: {secs:.1f}s")
        if self.layer_best_scores:
            lines.append("  Best score per layer:")
            for layer_name, score in sorted(self.layer_best_scores.items()):
                lines.append(f"    {layer_name}: {score:+.1f}ms")
        lines.append("")

        def _fmt(label: str, ev: CandidateEvaluation | None) -> list[str]:
            if ev is None:
                return [f"  {label}: (none)"]
            out = [
                f"  {label} — family={ev.family}, score={ev.score:+.1f}ms",
                ev.breakdown.summary(),
            ]
            # Show physics
            if ev.physics is not None:
                p = ev.physics
                out.append(
                    f"    Physics: excursion F={p.front_excursion_mm:.1f}mm "
                    f"R={p.rear_excursion_mm:.1f}mm | "
                    f"bottom margin={p.front_bottoming_margin_mm:+.1f}mm | "
                    f"stall={p.stall_margin_mm:+.1f}mm"
                )
                out.append(
                    f"    LLTD={p.lltd:.1%} (err={p.lltd_error:.3f}) | "
                    f"ζ_LS F={p.zeta_ls_front:.2f}/R={p.zeta_ls_rear:.2f} | "
                    f"ζ_HS F={p.zeta_hs_front:.2f}/R={p.zeta_hs_rear:.2f}"
                )
            # Show key params
            pk = ev.params
            param_keys = [
                ("heave", "front_heave_spring_nmm", "N/mm"),
                ("third", "rear_third_spring_nmm", "N/mm"),
                ("rear_spr", "rear_spring_rate_nmm", "N/mm"),
                ("camber_F", "front_camber_deg", "°"),
                ("camber_R", "rear_camber_deg", "°"),
                ("ARB_F", "front_arb_blade", ""),
                ("ARB_R", "rear_arb_blade", ""),
                ("LS_F", "front_ls_comp", ""),
                ("LS_R", "rear_ls_comp", ""),
                ("HS_F", "front_hs_comp", ""),
                ("HS_R", "rear_hs_comp", ""),
                ("bias", "brake_bias_pct", "%"),
                ("diff", "diff_preload_nm", "Nm"),
            ]
            parts = []
            for short, key, unit in param_keys:
                if key in pk:
                    v = pk[key]
                    if isinstance(v, float) and v == int(v):
                        parts.append(f"{short}={int(v)}{unit}")
                    elif isinstance(v, float):
                        parts.append(f"{short}={v:.1f}{unit}")
                    else:
                        parts.append(f"{short}={v}{unit}")
            # Split across two lines
            mid = len(parts) // 2
            out.append(f"    Params: {', '.join(parts[:mid])}")
            out.append(f"            {', '.join(parts[mid:])}")
            if ev.soft_penalties:
                out.append(f"    Penalties: {'; '.join(ev.soft_penalties[:3])}")
            return out

        lines.extend(_fmt("Best Robust", self.best_robust))
        lines.append("")
        lines.extend(_fmt("Best Aggressive", self.best_aggressive))
        lines.append("")
        lines.extend(_fmt("Best Weird-but-Legal", self.best_weird))
        lines.append("")
        lines.extend(_fmt("Scenario Pick", self.accepted_best))
        lines.append("")

        if self.acceptance_notes:
            lines.append("  --- Acceptance notes ---")
            for note in self.acceptance_notes[:8]:
                lines.append(f"    {note}")
            lines.append("")

        # Top 10
        selectable = [e for e in self.all_evaluations if not e.hard_vetoed]
        selectable.sort(key=lambda e: e.score, reverse=True)
        if selectable:
            lines.append("  --- Top 10 candidates ---")
            for i, ev in enumerate(selectable[:10], 1):
                sp = f" | penalties: {len(ev.soft_penalties)}" if ev.soft_penalties else ""
                phys = ""
                if ev.physics:
                    phys = (f" | LLTD={ev.physics.lltd:.1%}"
                            f" exc={ev.physics.front_excursion_mm:.1f}mm"
                            f" ζLS={ev.physics.zeta_ls_front:.2f}")
                lines.append(
                    f"  {i:2d}. [{ev.family:<20s}] {ev.score:+7.1f}ms{phys}{sp}"
                )
            lines.append("")

        # Vetoed examples
        vetoed = [e for e in self.all_evaluations if e.hard_vetoed]
        if vetoed:
            lines.append(f"  --- Vetoed candidates ({len(vetoed)}) ---")
            for ev in vetoed[:5]:
                lines.append(f"    [{ev.family}] {', '.join(ev.veto_reasons[:2])}")
            lines.append("")

        lines.append("=" * 63)
        return "\n".join(lines)


def _budget_to_mode(budget: int) -> str:
    """Map numeric budget to a search mode string.

    The --search-budget CLI flag passes an integer. We use threshold
    ranges to pick the appropriate search mode:
        ≤50,000   → "quick"    (Sobol sampling, no grid)
        ≤500,000  → "standard" (Sobol sampling, no grid)
        ≤10M      → "exhaustive" (Grid engine Layer 1-2)
        >10M      → "maximum"   (Grid engine Layer 1-2, finer coarse levels)
    """
    if budget <= 50_000:
        return "quick"
    elif budget <= 500_000:
        return "standard"
    elif budget <= 10_000_000:
        return "exhaustive"
    else:
        return "maximum"


def run_legal_search(
    car: CarModel,
    track: TrackProfile | str,
    baseline_params: dict[str, float],
    budget: int = 1000,
    solver_result: dict | None = None,
    measured=None,
    driver_profile=None,
    session_count: int = 0,
    keep_weird: bool = True,
    seed: int = 42,
    mode: str | None = None,
    base_result: SolveChainResult | None = None,
    solve_inputs: SolveChainInputs | None = None,
    scenario_profile: str | None = None,
    accept_top_n: int = 12,
) -> LegalSearchResult:
    """Run the legal-manifold search.

    Dispatches to the appropriate search engine based on mode:
    - "quick" / "standard": Two-stage Sobol + edge-family sampling
      (original fast path).
    - "exhaustive" / "maximum": Hierarchical grid search via
      GridSearchEngine (Layer 1-2 exhaustive enumeration).

    If mode is not specified, it is inferred from budget.

    Args:
        car: Car model
        track: TrackProfile or track name string
        baseline_params: Physics solver baseline (seed point)
        budget: Total candidate budget
        solver_result: Solver step outputs (optional, improves scoring)
        measured: MeasuredState from telemetry (optional)
        driver_profile: DriverProfile (optional)
        session_count: Number of telemetry sessions available
        keep_weird: If True, preserve unconventional candidates in results
        seed: Random seed
        mode: Search mode override — "quick", "standard", "exhaustive",
              or "maximum". If None, inferred from budget.
    """
    # Determine search mode
    search_mode = mode if mode is not None else _budget_to_mode(budget)

    # ── Grid engine path (exhaustive / maximum) ────────────────────
    if search_mode in ("exhaustive", "maximum"):
        return _run_grid_search(
            car=car,
            track=track,
            baseline_params=baseline_params,
            mode=search_mode,
            measured=measured,
            driver_profile=driver_profile,
            session_count=session_count,
            scenario_profile=scenario_profile,
        )

    # ── Sobol sampling path (quick / standard) ─────────────────────
    return _run_sampling_search(
        car=car,
        track=track,
        baseline_params=baseline_params,
        budget=budget,
        solver_result=solver_result,
        measured=measured,
        driver_profile=driver_profile,
        session_count=session_count,
        keep_weird=keep_weird,
        seed=seed,
        base_result=base_result,
        solve_inputs=solve_inputs,
        scenario_profile=scenario_profile,
        accept_top_n=accept_top_n,
    )


def _run_grid_search(
    car: CarModel,
    track: TrackProfile | str,
    baseline_params: dict[str, float],
    mode: str,
    measured=None,
    driver_profile=None,
    session_count: int = 0,
    scenario_profile: str | None = None,
) -> LegalSearchResult:
    """Dispatch to the GridSearchEngine for exhaustive/maximum modes."""
    from solver.grid_search import GridSearchEngine, GridSearchResult

    track_name = track if isinstance(track, str) else getattr(track, "name", "")
    track_obj = track if isinstance(track, TrackProfile) else None

    space = LegalSpace.from_car(car, track_name=track_name)
    objective = ObjectiveFunction(
        car,
        track_obj if track_obj is not None else track_name,
        scenario_profile=scenario_profile,
    )

    engine = GridSearchEngine(
        space=space,
        objective=objective,
        car=car,
        track=track_obj if track_obj is not None else track_name,
        baseline_params=baseline_params,
    )

    grid_result = engine.run(budget=mode)

    # Convert GridSearchResult → LegalSearchResult for compatibility
    families_seen = sorted(set(e.family for e in grid_result.all_evaluations))

    return LegalSearchResult(
        all_evaluations=grid_result.all_evaluations,
        best_robust=grid_result.best_robust,
        best_aggressive=grid_result.best_aggressive,
        best_weird=grid_result.best_weird,
        scenario_profile=resolve_scenario_name(scenario_profile),
        vetoed_count=grid_result.vetoed_count,
        total_evaluated=grid_result.total_evaluated,
        families_searched=families_seen,
        layer_times=grid_result.layer_times,
        layer_best_scores=grid_result.layer_best_scores,
        locally_optimal=grid_result.layer4_candidates > 0,
    )


def _run_sampling_search(
    car: CarModel,
    track: TrackProfile | str,
    baseline_params: dict[str, float],
    budget: int = 1000,
    solver_result: dict | None = None,
    measured=None,
    driver_profile=None,
    session_count: int = 0,
    keep_weird: bool = True,
    seed: int = 42,
    base_result: SolveChainResult | None = None,
    solve_inputs: SolveChainInputs | None = None,
    scenario_profile: str | None = None,
    accept_top_n: int = 12,
) -> LegalSearchResult:
    """Original two-stage Sobol + edge-family sampling search."""
    track_name = track if isinstance(track, str) else getattr(track, "name", "")
    track_obj = track if isinstance(track, TrackProfile) else None
    rng = random.Random(seed)
    resolved_scenario = resolve_scenario_name(scenario_profile)
    profile = get_scenario_profile(resolved_scenario)

    # Build legal space
    space = LegalSpace.from_car(car, track_name=track_name)

    # Build objective — pass actual TrackProfile for physics evaluation
    objective = ObjectiveFunction(
        car,
        track_obj if track_obj is not None else track_name,
        scenario_profile=resolved_scenario,
    )

    # Budget allocation: baseline gets 30%, each edge family ~10%, uniform scatter 10%
    n_families = len(EDGE_FAMILIES) + 2  # +1 baseline, +1 uniform
    budget_per_family = max(10, budget // n_families)

    # Stage 1: Generate candidates (with auto-computed perch offsets)
    candidates = _generate_family_seeds(space, baseline_params, budget_per_family, rng, car=car)

    # Stage 2: Evaluate all
    evaluations = _evaluate_candidates(
        candidates, car, track_name, objective,
        solver_result=solver_result,
        measured=measured,
        driver_profile=driver_profile,
        session_count=session_count,
    )

    # Classify results
    vetoed = [e for e in evaluations if e.hard_vetoed]
    selectable = [e for e in evaluations if not e.hard_vetoed]
    selectable.sort(key=lambda e: e.score, reverse=True)

    # Best robust: highest score with no soft penalties
    clean = [e for e in selectable if len(e.soft_penalties) == 0]
    best_robust = clean[0] if clean else (selectable[0] if selectable else None)

    # Best aggressive: highest raw score regardless of penalties
    best_aggressive = selectable[0] if selectable else None

    # Best weird: highest score among candidates from edge families
    weird = [e for e in selectable if e.family not in ("physics_baseline", "seeded")]
    best_weird = weird[0] if weird else None

    families_seen = sorted(set(e.family for e in evaluations))
    accepted_pairs: list[tuple[CandidateEvaluation, SolveChainResult]] = []
    acceptance_notes: list[str] = []
    accepted_best: CandidateEvaluation | None = None
    accepted_best_result: SolveChainResult | None = None

    if selectable and base_result is not None and solve_inputs is not None:
        for ev in selectable[: max(1, int(accept_top_n))]:
            try:
                overrides = canonical_params_to_overrides(base_result, ev.params, car=car)
                rematerialized = materialize_overrides(base_result, overrides, solve_inputs)
            except Exception as exc:
                acceptance_notes.append(f"{ev.family}: materialization failed ({exc})")
                continue
            if not rematerialized.legal_validation.valid:
                reason = "; ".join(rematerialized.legal_validation.messages[:2]) or "full legality failed"
                acceptance_notes.append(f"{ev.family}: rejected by full legality ({reason})")
                continue
            sane, sanity_issues = prediction_passes_sanity(
                rematerialized.prediction,
                rematerialized.prediction_confidence,
                resolved_scenario,
            )
            if not sane:
                acceptance_notes.append(
                    f"{ev.family}: rejected by {resolved_scenario} sanity ({'; '.join(sanity_issues[:2])})"
                )
                continue
            accepted_pairs.append((ev, rematerialized))
    elif selectable:
        acceptance_notes.append("full acceptance skipped: missing base_result/solve_inputs")

    if accepted_pairs:
        accepted_lookup = {id(ev): result for ev, result in accepted_pairs}
        preferred = {
            "best_robust": best_robust,
            "best_aggressive": best_aggressive,
            "best_weird": best_weird,
        }.get(profile.preferred_result_key)
        if preferred is not None and id(preferred) in accepted_lookup:
            accepted_best = preferred
            accepted_best_result = accepted_lookup[id(preferred)]
        else:
            accepted_best, accepted_best_result = max(accepted_pairs, key=lambda item: item[0].score)
    else:
        acceptance_notes.append(f"no candidate survived full {resolved_scenario} acceptance")

    return LegalSearchResult(
        all_evaluations=evaluations,
        best_robust=best_robust,
        best_aggressive=best_aggressive,
        best_weird=best_weird,
        accepted_evaluations=[ev for ev, _ in accepted_pairs],
        accepted_best=accepted_best,
        accepted_best_result=accepted_best_result,
        accepted_candidates_count=len(accepted_pairs),
        scenario_profile=resolved_scenario,
        acceptance_notes=acceptance_notes,
        vetoed_count=len(vetoed),
        total_evaluated=len(evaluations),
        families_searched=families_seen,
    )
