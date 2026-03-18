"""Hierarchical exhaustive search engine for the legal manifold.

Replaces random sampling with structured grid enumeration across
layered subspaces. Each layer fixes "above" parameters and exhausts
the ones below.

Layer 1 — Platform Skeletons:
    Sobol/LHS grid over wing × pushrod_F × pushrod_R × heave × third × rear_spring.
    ~50,000 skeletons → physics filter → top ~2,000.

Layer 2 — Balance Tuning:
    For each surviving skeleton: exhaustive grid over
    torsion_OD × ARB_F × ARB_R, crossed with coarse
    camber/bias/diff levels (3 each).
    14 × 5 × 5 × 3 × 3 × 3 × 3 = 28,350 per skeleton.

Layer 3 — Damper Coordinate Descent:
    For top ~500 from Layer 2: sweep each of the 10 damper axes
    independently (coordinate descent). ~120 evals per candidate.
    Repeat until convergence or max iterations.

Layer 4 — Neighborhood Polish:
    For top ~50 from Layer 3: full ±1 step neighborhood search
    across ALL dimensions. Iterate until no neighbor improves.
    Guarantees local optimality.

Usage:
    from solver.grid_search import GridSearchEngine
    engine = GridSearchEngine(space, objective, car, track)
    result = engine.run(budget="exhaustive")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from car_model.cars import CarModel
from solver.legal_space import LegalSpace, LegalCandidate, compute_perch_offsets
from solver.legality_engine import validate_candidate_legality
from solver.objective import ObjectiveFunction, CandidateEvaluation
from track_model.profile import TrackProfile

logger = logging.getLogger(__name__)


# ── Layer 1 platform dimension keys ────────────────────────────────
PLATFORM_KEYS: list[str] = [
    "wing_angle_deg",
    "front_pushrod_offset_mm",
    "rear_pushrod_offset_mm",
    "front_heave_spring_nmm",
    "rear_third_spring_nmm",
    "rear_spring_rate_nmm",
]

# ── Layer 2 fully-enumerated balance keys ──────────────────────────
# These discrete dimensions are small enough to enumerate exhaustively
BALANCE_FULL_KEYS: list[str] = [
    "front_torsion_od_mm",
    "front_arb_blade",
    "rear_arb_blade",
]

# ── Layer 2 coarse-grid keys (3 levels each) ──────────────────────
BALANCE_COARSE_KEYS: dict[str, int] = {
    "front_camber_deg": 3,
    "rear_camber_deg": 3,
    "brake_bias_pct": 3,
    "diff_preload_nm": 3,
}

# ── Layer 3 damper dimension keys (10 axes) ────────────────────────
DAMPER_KEYS: list[str] = [
    "front_ls_comp",
    "front_ls_rbd",
    "front_hs_comp",
    "front_hs_rbd",
    "front_hs_slope",
    "rear_ls_comp",
    "rear_ls_rbd",
    "rear_hs_comp",
    "rear_hs_rbd",
    "rear_hs_slope",
]


@dataclass
class GridSearchResult:
    """Complete result of a grid-based hierarchical search."""
    all_evaluations: list[CandidateEvaluation]
    best_robust: CandidateEvaluation | None = None
    best_aggressive: CandidateEvaluation | None = None
    best_weird: CandidateEvaluation | None = None
    vetoed_count: int = 0
    total_evaluated: int = 0
    layer1_survivors: int = 0
    layer2_candidates: int = 0
    layer3_candidates: int = 0
    layer4_candidates: int = 0
    elapsed_seconds: float = 0.0
    layer_times: dict[str, float] = field(default_factory=dict)
    layer_best_scores: dict[str, float] = field(default_factory=dict)
    budget_mode: str = "exhaustive"

    def summary(self) -> str:
        lines = [
            "=" * 63,
            "  GRID SEARCH RESULTS (Hierarchical Exhaustive)",
            "=" * 63,
            f"  Budget mode:       {self.budget_mode}",
            f"  Layer 1 survivors: {self.layer1_survivors}",
            f"  Layer 2 evaluated: {self.layer2_candidates}",
            f"  Layer 3 evaluated: {self.layer3_candidates}",
            f"  Layer 4 evaluated: {self.layer4_candidates}",
            f"  Total evaluated:   {self.total_evaluated}",
            f"  Vetoed (hard):     {self.vetoed_count}",
            f"  Elapsed:           {self.elapsed_seconds:.1f}s",
        ]
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
            pk = ev.params
            param_keys = [
                ("wing", "wing_angle_deg", "°"),
                ("heave", "front_heave_spring_nmm", "N/mm"),
                ("third", "rear_third_spring_nmm", "N/mm"),
                ("rear_spr", "rear_spring_rate_nmm", "N/mm"),
                ("torsion", "front_torsion_od_mm", "mm"),
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

        lines.append("=" * 63)
        return "\n".join(lines)


# ── Budget tier configuration ──────────────────────────────────────
# Maps budget mode → (n_sobol, top_k_skeletons, camber_levels, bias_levels, diff_levels)
BUDGET_TIERS: dict[str, dict] = {
    "quick": {
        "n_sobol": 5_000,
        "top_k": 200,
        "camber_levels": 3,
        "bias_levels": 3,
        "diff_levels": 3,
        "damper_top_n": 0,       # no Layer 3
        "polish_top_n": 0,       # no Layer 4
    },
    "standard": {
        "n_sobol": 20_000,
        "top_k": 500,
        "camber_levels": 3,
        "bias_levels": 3,
        "diff_levels": 3,
        "damper_top_n": 0,       # no Layer 3
        "polish_top_n": 0,       # no Layer 4
    },
    "exhaustive": {
        "n_sobol": 50_000,
        "top_k": 2_000,
        "camber_levels": 3,
        "bias_levels": 3,
        "diff_levels": 3,
        "damper_top_n": 500,     # Layer 3: coordinate descent on top 500
        "polish_top_n": 0,       # no Layer 4 (exhaustive stops at L3)
    },
    "maximum": {
        "n_sobol": 50_000,
        "top_k": 2_000,
        "camber_levels": 5,
        "bias_levels": 5,
        "diff_levels": 5,
        "damper_top_n": 500,     # Layer 3: coordinate descent on top 500
        "polish_top_n": 50,      # Layer 4: neighborhood polish on top 50
    },
}


class GridSearchEngine:
    """Hierarchical exhaustive search across the legal manifold.

    Uses a 2-layer decomposition to systematically evaluate the
    setup space:

    Layer 1 — Platform Skeletons: Sobol quasi-random sampling over
    the 6 structural platform dimensions, physics-filtered to a
    manageable set of survivors.

    Layer 2 — Balance Tuning: For each survivor, exhaustive
    enumeration of torsion_OD × ARB_F × ARB_R crossed with coarse
    camber/bias/diff levels. Every combination is scored.

    Perch offsets are computed (not searched) — see compute_perch_offsets().
    """

    def __init__(
        self,
        space: LegalSpace,
        objective: ObjectiveFunction,
        car: CarModel,
        track: TrackProfile | str,
        baseline_params: dict[str, float] | None = None,
    ):
        self.space = space
        self.objective = objective
        self.car = car
        self.track = track
        self.baseline_params = baseline_params or {}

    # ── Layer 1: Platform Skeletons ────────────────────────────────

    def layer1_platform_skeletons(
        self,
        n_sobol: int = 50_000,
        top_k: int = 2_000,
    ) -> list[dict[str, float]]:
        """Generate Sobol samples over platform dimensions, physics-filter.

        Generates n_sobol quasi-random skeletons over:
            wing × pushrod_F × pushrod_R × heave × third × rear_spring

        Then evaluates each with the Layer 1 objective profile
        (platform_risk + lap_gain only — fast, ~0.1ms per candidate)
        and keeps the top_k survivors.

        Perch offsets are auto-computed for each skeleton.

        Args:
            n_sobol: Number of Sobol samples to generate.
            top_k: Number of survivors to keep after physics filtering.

        Returns:
            List of top_k parameter dicts (platform skeletons).
        """
        # Only sample keys that actually exist in the space
        active_keys = [k for k in PLATFORM_KEYS if k in self.space._dim_map]
        if not active_keys:
            logger.warning("No platform keys found in search space")
            return []

        logger.info(
            f"Layer 1: generating {n_sobol:,} Sobol skeletons over "
            f"{len(active_keys)} platform dimensions..."
        )
        t0 = time.perf_counter()

        # Generate Sobol samples
        sobol_samples = self.space.sobol_sample(active_keys, n_sobol, seed=42)

        # Fill in baseline values for non-platform dimensions so
        # evaluate_batch has complete parameter sets
        filled = []
        for sample in sobol_samples:
            params = dict(self.baseline_params)
            params.update(sample)
            # Compute dependent perch offsets
            perches = compute_perch_offsets(params, self.car)
            params.update(perches)
            filled.append(params)

        # Batch-evaluate at Layer 1 (platform_risk + lap_gain only)
        evaluations = self.objective.evaluate_batch(
            filled, layer=1, family="grid_L1"
        )

        # Filter: remove hard-vetoed, sort by score, keep top_k
        scored = [
            (ev, params)
            for ev, params in zip(evaluations, filled)
            if not ev.hard_vetoed
        ]
        scored.sort(key=lambda x: x[0].score, reverse=True)

        survivors = [params for _, params in scored[:top_k]]

        dt = time.perf_counter() - t0
        logger.info(
            f"Layer 1 complete: {len(sobol_samples):,} sampled → "
            f"{len(scored):,} viable → {len(survivors):,} survivors "
            f"({dt:.1f}s)"
        )

        return survivors

    # ── Layer 2: Balance Grid ──────────────────────────────────────

    def layer2_balance_grid(
        self,
        skeletons: list[dict[str, float]],
        camber_levels: int = 3,
        bias_levels: int = 3,
        diff_levels: int = 3,
    ) -> list[CandidateEvaluation]:
        """Exhaustive balance grid for each platform skeleton.

        For each skeleton, generates the full Cartesian product of:
            torsion_OD × ARB_F × ARB_R  (all legal values)
        crossed with:
            front_camber × rear_camber × brake_bias × diff_preload  (coarse levels)

        Each combination is scored with the Layer 2 objective profile
        (platform_risk + lap_gain + LLTD + balance).

        Args:
            skeletons: Platform skeletons from Layer 1.
            camber_levels: Number of coarse levels for camber dims.
            bias_levels: Number of coarse levels for brake bias.
            diff_levels: Number of coarse levels for diff preload.

        Returns:
            List of CandidateEvaluation for ALL scored combinations.
        """
        if not skeletons:
            return []

        # Build the balance grid template (same for every skeleton)
        coarse = {
            "front_camber_deg": camber_levels,
            "rear_camber_deg": camber_levels,
            "brake_bias_pct": bias_levels,
            "diff_preload_nm": diff_levels,
        }

        # Only use balance keys that exist in the space
        active_full_keys = [
            k for k in BALANCE_FULL_KEYS if k in self.space._dim_map
        ]
        active_coarse = {
            k: v for k, v in coarse.items() if k in self.space._dim_map
        }

        # Generate the grid template once
        grid_template = self.space.exhaustive_grid(
            active_full_keys, coarse_keys=active_coarse
        )
        grid_size = len(grid_template)

        if grid_size == 0:
            logger.warning("Layer 2: exhaustive_grid returned empty — "
                           "cardinality may exceed limit")
            # Fallback: use coarse grid for everything
            all_coarse = {k: 3 for k in active_full_keys}
            all_coarse.update(active_coarse)
            grid_template = self.space.exhaustive_grid([], coarse_keys=all_coarse)
            grid_size = len(grid_template)
            if grid_size == 0:
                logger.error("Layer 2: even coarse grid is empty, aborting")
                return []

        total_candidates = len(skeletons) * grid_size
        logger.info(
            f"Layer 2: {len(skeletons):,} skeletons × {grid_size:,} "
            f"balance combos = {total_candidates:,} total candidates"
        )
        t0 = time.perf_counter()

        all_evaluations: list[CandidateEvaluation] = []

        # Process skeletons in chunks for memory efficiency
        BATCH_SIZE = 10_000  # evaluate_batch chunk size
        batch_buffer: list[dict[str, float]] = []

        for skel_idx, skeleton in enumerate(skeletons):
            for grid_point in grid_template:
                # Merge: skeleton platform + grid balance params
                candidate = dict(skeleton)
                candidate.update(grid_point)

                # Recompute perch offsets (spring rates may differ from
                # the skeleton if torsion_od changed wheel rate)
                perches = compute_perch_offsets(candidate, self.car)
                candidate.update(perches)

                batch_buffer.append(candidate)

                # Flush when buffer is full
                if len(batch_buffer) >= BATCH_SIZE:
                    evals = self._evaluate_and_validate(
                        batch_buffer, layer=2, family="grid_L2"
                    )
                    all_evaluations.extend(evals)
                    batch_buffer = []

            # Progress logging every 100 skeletons
            if (skel_idx + 1) % 100 == 0:
                elapsed = time.perf_counter() - t0
                pct = (skel_idx + 1) / len(skeletons) * 100
                logger.info(
                    f"  Layer 2 progress: {skel_idx + 1}/{len(skeletons)} "
                    f"skeletons ({pct:.0f}%), {len(all_evaluations):,} "
                    f"evaluated, {elapsed:.1f}s elapsed"
                )

        # Flush remaining
        if batch_buffer:
            evals = self._evaluate_and_validate(
                batch_buffer, layer=2, family="grid_L2"
            )
            all_evaluations.extend(evals)

        dt = time.perf_counter() - t0
        n_viable = sum(1 for e in all_evaluations if not e.hard_vetoed)
        logger.info(
            f"Layer 2 complete: {len(all_evaluations):,} evaluated, "
            f"{n_viable:,} viable ({dt:.1f}s)"
        )

        return all_evaluations

    # ── Evaluate + validate helper ─────────────────────────────────

    def _evaluate_and_validate(
        self,
        param_batch: list[dict[str, float]],
        layer: int,
        family: str,
    ) -> list[CandidateEvaluation]:
        """Evaluate a batch and apply legality validation."""
        evals = self.objective.evaluate_batch(
            param_batch, layer=layer, family=family
        )

        for ev in evals:
            legality = validate_candidate_legality(ev.params, self.car)
            if legality.hard_veto:
                ev.hard_vetoed = True
                ev.veto_reasons.extend(legality.hard_veto_reasons)
            ev.soft_penalties.extend(legality.soft_penalties)

        return evals

    # ── Layer 3: Damper Coordinate Descent ────────────────────────

    def layer3_damper_coordinate_descent(
        self,
        candidates: list[CandidateEvaluation],
        top_n: int = 500,
        max_iterations: int = 3,
    ) -> list[CandidateEvaluation]:
        """Coordinate descent over the 10 damper axes for top candidates.

        For each of the top_n candidates from Layer 2, sweeps each
        damper dimension independently to find the best click position.
        Repeats the full coordinate descent cycle until convergence
        (no improvement) or max_iterations reached.

        Correlated axis cross-refinement: after the initial sweep,
        for the top 50 candidates, enumerates all combinations of
        correlated axis pairs (ls_comp + ls_rbd, hs_comp + hs_rbd)
        for front and rear.

        Uses Layer 3 objective profile (platform_risk + lap_gain +
        LLTD + balance + damping ratio scoring).

        Args:
            candidates: CandidateEvaluations from Layer 2, sorted by
                        score descending.
            top_n: Number of top candidates to optimize.
            max_iterations: Maximum coordinate descent cycles.

        Returns:
            List of CandidateEvaluation with optimized damper settings,
            sorted by score descending.
        """
        if not candidates:
            return []

        # Select top-N non-vetoed candidates
        viable = [c for c in candidates if not c.hard_vetoed]
        viable.sort(key=lambda e: e.score, reverse=True)
        selected = viable[:top_n]

        if not selected:
            return candidates

        # Identify active damper keys that exist in the search space
        active_damper_keys = [
            k for k in DAMPER_KEYS if k in self.space._dim_map
        ]
        if not active_damper_keys:
            logger.warning("Layer 3: no damper keys found in search space")
            return candidates

        logger.info(
            f"Layer 3: coordinate descent over {len(active_damper_keys)} "
            f"damper axes for {len(selected)} candidates "
            f"(max {max_iterations} iterations)..."
        )
        t0 = time.perf_counter()
        total_evals = 0
        best_score_so_far = selected[0].score if selected else 0.0

        optimized: list[CandidateEvaluation] = []

        for cand_idx, cand in enumerate(selected):
            current_params = dict(cand.params)
            current_score = cand.score

            for iteration in range(max_iterations):
                improved_this_cycle = False

                for damper_key in active_damper_keys:
                    dim = self.space._dim_map[damper_key]
                    legal_vals = dim.legal_values()
                    if not legal_vals or len(legal_vals) <= 1:
                        continue

                    # Build batch: one candidate per legal value of this axis
                    batch: list[dict[str, float]] = []
                    for val in legal_vals:
                        trial = dict(current_params)
                        trial[damper_key] = val
                        batch.append(trial)

                    # Evaluate at Layer 3
                    evals = self._evaluate_and_validate(
                        batch, layer=3, family="grid_L3_cd"
                    )
                    total_evals += len(evals)

                    # Find best
                    best_eval = max(
                        (e for e in evals if not e.hard_vetoed),
                        key=lambda e: e.score,
                        default=None,
                    )

                    if best_eval is not None and best_eval.score > current_score + 1e-6:
                        current_params = dict(best_eval.params)
                        current_score = best_eval.score
                        improved_this_cycle = True

                if not improved_this_cycle:
                    break  # converged

            # Re-evaluate the final optimized params at Layer 3
            final_evals = self._evaluate_and_validate(
                [current_params], layer=3, family="grid_L3"
            )
            if final_evals:
                optimized.append(final_evals[0])
                total_evals += 1
                if final_evals[0].score > best_score_so_far:
                    best_score_so_far = final_evals[0].score

            # Progress logging
            if (cand_idx + 1) % 50 == 0 or cand_idx + 1 == len(selected):
                elapsed = time.perf_counter() - t0
                logger.info(
                    f"  Layer 3 progress: {cand_idx + 1}/{len(selected)} "
                    f"candidates, {total_evals:,} evals, "
                    f"best={best_score_so_far:+.1f}ms, {elapsed:.1f}s"
                )

        # Cross-refinement on top 50: for correlated damper pairs,
        # try all combinations
        optimized.sort(key=lambda e: e.score, reverse=True)
        cross_top = optimized[:min(50, len(optimized))]

        CORRELATED_PAIRS = [
            ("front_ls_comp", "front_ls_rbd"),
            ("front_hs_comp", "front_hs_rbd"),
            ("rear_ls_comp", "rear_ls_rbd"),
            ("rear_hs_comp", "rear_hs_rbd"),
        ]
        active_pairs = [
            (a, b) for a, b in CORRELATED_PAIRS
            if a in self.space._dim_map and b in self.space._dim_map
        ]

        if active_pairs and cross_top:
            logger.info(
                f"  Layer 3 cross-refinement: {len(active_pairs)} "
                f"correlated pairs for top {len(cross_top)} candidates"
            )
            for i, cand in enumerate(cross_top):
                current_params = dict(cand.params)
                current_score = cand.score

                for key_a, key_b in active_pairs:
                    dim_a = self.space._dim_map[key_a]
                    dim_b = self.space._dim_map[key_b]
                    vals_a = dim_a.legal_values()
                    vals_b = dim_b.legal_values()

                    if not vals_a or not vals_b:
                        continue

                    # Enumerate all (a, b) combos
                    batch: list[dict[str, float]] = []
                    for va in vals_a:
                        for vb in vals_b:
                            trial = dict(current_params)
                            trial[key_a] = va
                            trial[key_b] = vb
                            batch.append(trial)

                    evals = self._evaluate_and_validate(
                        batch, layer=3, family="grid_L3_cross"
                    )
                    total_evals += len(evals)

                    best_eval = max(
                        (e for e in evals if not e.hard_vetoed),
                        key=lambda e: e.score,
                        default=None,
                    )

                    if best_eval is not None and best_eval.score > current_score + 1e-6:
                        current_params = dict(best_eval.params)
                        current_score = best_eval.score

                # Update the candidate with cross-refined params
                final_evals = self._evaluate_and_validate(
                    [current_params], layer=3, family="grid_L3"
                )
                if final_evals:
                    cross_top[i] = final_evals[0]
                    total_evals += 1

        dt = time.perf_counter() - t0
        n_viable = sum(1 for e in optimized if not e.hard_vetoed)

        # Merge cross-refined top with the rest of the optimized list
        cross_set = {id(c) for c in cross_top}
        merged = list(cross_top) + [
            c for c in optimized if id(c) not in cross_set
        ]
        merged.sort(key=lambda e: e.score, reverse=True)

        logger.info(
            f"Layer 3 complete: {total_evals:,} evals, "
            f"{n_viable} viable, best={best_score_so_far:+.1f}ms ({dt:.1f}s)"
        )

        return merged

    # ── Layer 4: Neighborhood Polish ───────────────────────────────

    def layer4_neighborhood_polish(
        self,
        candidates: list[CandidateEvaluation],
        top_n: int = 50,
        max_iterations: int = 10,
    ) -> list[CandidateEvaluation]:
        """Full ±1 step neighborhood search for guaranteed local optimality.

        For each of the top_n candidates from Layer 3, generates all
        ±1 step neighbors across ALL dimensions using
        LegalSpace.neighborhood(). Accepts any improvement and iterates
        until no neighbor improves (local optimum) or max_iterations.

        Uses the full Layer 4 objective (all terms including driver
        mismatch, telemetry uncertainty, envelope penalty).

        Args:
            candidates: CandidateEvaluations from Layer 3, sorted by
                        score descending.
            top_n: Number of top candidates to polish.
            max_iterations: Maximum hill-climbing iterations.

        Returns:
            List of CandidateEvaluation at local optima, sorted by
            score descending.
        """
        if not candidates:
            return []

        viable = [c for c in candidates if not c.hard_vetoed]
        viable.sort(key=lambda e: e.score, reverse=True)
        selected = viable[:top_n]

        if not selected:
            return candidates

        logger.info(
            f"Layer 4: neighborhood polish for {len(selected)} candidates "
            f"(max {max_iterations} iterations, "
            f"~{len(self.space.dimensions) * 2} neighbors per step)..."
        )
        t0 = time.perf_counter()
        total_evals = 0
        best_score_so_far = selected[0].score if selected else 0.0

        polished: list[CandidateEvaluation] = []

        for cand_idx, cand in enumerate(selected):
            current_params = dict(cand.params)
            current_score = cand.score
            iterations_used = 0

            for iteration in range(max_iterations):
                # Generate all ±1 step neighbors across ALL dimensions
                neighbors = self.space.neighborhood(current_params, steps=1)

                if not neighbors:
                    break

                # Recompute perch offsets for each neighbor since
                # spring/pushrod changes affect dependent variables
                for i, nb in enumerate(neighbors):
                    perches = compute_perch_offsets(nb, self.car)
                    nb.update(perches)

                # Evaluate all neighbors at Layer 4 (full objective)
                evals = self._evaluate_and_validate(
                    neighbors, layer=4, family="grid_L4"
                )
                total_evals += len(evals)

                # Find the best neighbor
                best_neighbor = max(
                    (e for e in evals if not e.hard_vetoed),
                    key=lambda e: e.score,
                    default=None,
                )

                if best_neighbor is not None and best_neighbor.score > current_score + 1e-6:
                    current_params = dict(best_neighbor.params)
                    current_score = best_neighbor.score
                    iterations_used = iteration + 1
                else:
                    # No improvement → local optimum reached
                    break

            # Re-evaluate final polished params at Layer 4
            final_evals = self._evaluate_and_validate(
                [current_params], layer=4, family="grid_L4_polished"
            )
            if final_evals:
                polished.append(final_evals[0])
                total_evals += 1
                if final_evals[0].score > best_score_so_far:
                    best_score_so_far = final_evals[0].score

            # Progress logging
            if (cand_idx + 1) % 10 == 0 or cand_idx + 1 == len(selected):
                elapsed = time.perf_counter() - t0
                logger.info(
                    f"  Layer 4 progress: {cand_idx + 1}/{len(selected)} "
                    f"candidates, {total_evals:,} evals, "
                    f"best={best_score_so_far:+.1f}ms, "
                    f"last used {iterations_used} iterations, {elapsed:.1f}s"
                )

        dt = time.perf_counter() - t0
        polished.sort(key=lambda e: e.score, reverse=True)

        logger.info(
            f"Layer 4 complete: {total_evals:,} evals, "
            f"{len(polished)} polished, best={best_score_so_far:+.1f}ms "
            f"({dt:.1f}s) — local optimality guaranteed"
        )

        return polished

    # ── Main dispatch ──────────────────────────────────────────────

    def run(self, budget: str = "exhaustive") -> GridSearchResult:
        """Execute the hierarchical grid search.

        Budget tiers control which layers run:
        - quick / standard: Layer 1-2 only (structured grid)
        - exhaustive: Layer 1-3 (+ damper coordinate descent)
        - maximum: Layer 1-4 (+ neighborhood polish for local optimality)

        Args:
            budget: Budget tier — one of "quick", "standard",
                    "exhaustive", or "maximum".

        Returns:
            GridSearchResult with best_robust, best_aggressive,
            best_weird candidates and full evaluation lists.
        """
        t0 = time.perf_counter()

        tier = BUDGET_TIERS.get(budget, BUDGET_TIERS["exhaustive"])
        logger.info(
            f"Grid search starting — budget={budget}, "
            f"n_sobol={tier['n_sobol']:,}, top_k={tier['top_k']:,}, "
            f"damper_top_n={tier['damper_top_n']}, "
            f"polish_top_n={tier['polish_top_n']}"
        )

        layer_times: dict[str, float] = {}
        layer_best_scores: dict[str, float] = {}
        total_evals = 0

        # ── Layer 1: Platform skeletons ───────────────────────────
        t_l1 = time.perf_counter()
        skeletons = self.layer1_platform_skeletons(
            n_sobol=tier["n_sobol"],
            top_k=tier["top_k"],
        )
        layer_times["Layer 1 (platform)"] = time.perf_counter() - t_l1
        total_evals += tier["n_sobol"]

        if not skeletons:
            logger.warning("No viable skeletons survived Layer 1")
            return GridSearchResult(
                all_evaluations=[],
                budget_mode=budget,
                elapsed_seconds=time.perf_counter() - t0,
                layer_times=layer_times,
            )

        # ── Layer 2: Balance grid ─────────────────────────────────
        t_l2 = time.perf_counter()
        all_evals = self.layer2_balance_grid(
            skeletons,
            camber_levels=tier["camber_levels"],
            bias_levels=tier["bias_levels"],
            diff_levels=tier["diff_levels"],
        )
        layer_times["Layer 2 (balance)"] = time.perf_counter() - t_l2
        total_evals += len(all_evals)

        # Track Layer 2 best score
        viable_l2 = [e for e in all_evals if not e.hard_vetoed]
        if viable_l2:
            viable_l2.sort(key=lambda e: e.score, reverse=True)
            layer_best_scores["Layer 2"] = viable_l2[0].score

        # ── Layer 3: Damper coordinate descent (exhaustive+) ──────
        layer3_evals: list[CandidateEvaluation] = []
        if tier["damper_top_n"] > 0 and all_evals:
            t_l3 = time.perf_counter()
            layer3_evals = self.layer3_damper_coordinate_descent(
                all_evals,
                top_n=tier["damper_top_n"],
            )
            layer_times["Layer 3 (dampers)"] = time.perf_counter() - t_l3

            if layer3_evals:
                viable_l3 = [e for e in layer3_evals if not e.hard_vetoed]
                if viable_l3:
                    layer_best_scores["Layer 3"] = max(
                        e.score for e in viable_l3
                    )

        # ── Layer 4: Neighborhood polish (maximum only) ───────────
        layer4_evals: list[CandidateEvaluation] = []
        source_for_l4 = layer3_evals if layer3_evals else all_evals
        if tier["polish_top_n"] > 0 and source_for_l4:
            t_l4 = time.perf_counter()
            layer4_evals = self.layer4_neighborhood_polish(
                source_for_l4,
                top_n=tier["polish_top_n"],
            )
            layer_times["Layer 4 (polish)"] = time.perf_counter() - t_l4

            if layer4_evals:
                viable_l4 = [e for e in layer4_evals if not e.hard_vetoed]
                if viable_l4:
                    layer_best_scores["Layer 4"] = max(
                        e.score for e in viable_l4
                    )

        # ── Merge all evaluations ─────────────────────────────────
        # The final result set includes Layer 2 base + any Layer 3/4
        # refined candidates (which replace their Layer 2 originals
        # by being strictly better).
        final_evals = list(all_evals)
        if layer3_evals:
            final_evals.extend(layer3_evals)
        if layer4_evals:
            final_evals.extend(layer4_evals)

        # Classify results
        vetoed = [e for e in final_evals if e.hard_vetoed]
        selectable = [e for e in final_evals if not e.hard_vetoed]
        selectable.sort(key=lambda e: e.score, reverse=True)

        # Best robust: highest score with no soft penalties
        clean = [e for e in selectable if len(e.soft_penalties) == 0]
        best_robust = clean[0] if clean else (
            selectable[0] if selectable else None
        )

        # Best aggressive: highest raw score regardless of penalties
        best_aggressive = selectable[0] if selectable else None

        # Best weird: highest score among candidates whose balance
        # params are far from the baseline (if we have one)
        best_weird = self._find_weird_candidate(selectable)

        elapsed = time.perf_counter() - t0

        # Summary logging
        logger.info(
            f"Grid search complete: {len(final_evals):,} total evals, "
            f"{len(selectable):,} viable, {elapsed:.1f}s"
        )
        for layer_name, secs in sorted(layer_times.items()):
            score_str = ""
            # Extract just the layer number for the score lookup
            for score_key, score_val in layer_best_scores.items():
                if score_key.split()[1] in layer_name:
                    score_str = f", best={score_val:+.1f}ms"
                    break
            logger.info(f"  {layer_name}: {secs:.1f}s{score_str}")

        return GridSearchResult(
            all_evaluations=final_evals,
            best_robust=best_robust,
            best_aggressive=best_aggressive,
            best_weird=best_weird,
            vetoed_count=len(vetoed),
            total_evaluated=len(final_evals),
            layer1_survivors=len(skeletons),
            layer2_candidates=len(all_evals),
            layer3_candidates=len(layer3_evals),
            layer4_candidates=len(layer4_evals),
            elapsed_seconds=elapsed,
            layer_times=layer_times,
            layer_best_scores=layer_best_scores,
            budget_mode=budget,
        )

    def _find_weird_candidate(
        self,
        selectable: list[CandidateEvaluation],
    ) -> CandidateEvaluation | None:
        """Find the best "weird but legal" candidate.

        Weird = substantially different from baseline in balance
        dimensions (LLTD, ARB config, diff). We look for candidates
        whose LLTD differs from the top candidate by >2%, or whose
        ARB configuration is unusual (both extremes).
        """
        if not selectable or len(selectable) < 2:
            return selectable[0] if selectable else None

        top = selectable[0]
        top_lltd = top.physics.lltd if top.physics else 0.5

        for ev in selectable[1:]:
            if ev.physics is None:
                continue
            lltd_diff = abs(ev.physics.lltd - top_lltd)
            # Check for unusual ARB or diff config
            arb_f = ev.params.get("front_arb_blade", 0)
            arb_r = ev.params.get("rear_arb_blade", 0)
            diff = ev.params.get("diff_preload_nm", 0)

            top_arb_f = top.params.get("front_arb_blade", 0)
            top_arb_r = top.params.get("rear_arb_blade", 0)
            top_diff = top.params.get("diff_preload_nm", 0)

            arb_diff = abs(arb_f - top_arb_f) + abs(arb_r - top_arb_r)
            diff_diff = abs(diff - top_diff)

            is_weird = (
                lltd_diff > 0.02
                or arb_diff >= 3
                or diff_diff > 15
            )
            if is_weird:
                return ev

        # Fallback: just return something different from top
        return selectable[min(1, len(selectable) - 1)]
