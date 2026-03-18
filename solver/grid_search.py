"""Hierarchical exhaustive search across the legal setup manifold.

Implements a 4-layer structured search that systematically evaluates
the legal parameter space without brute-forcing the full Cartesian product.

Architecture:
    Layer 1: Platform skeleton — Sobol sample wing/pushrods/heave/third/rear springs
             → physics-filter to top N by platform_risk + lap_gain
    Layer 2: Balance grid — for each skeleton, exhaustive grid over
             torsion_OD × front_arb_blade × rear_arb_blade × coarse camber/bias/diff
    Layer 3: Damper coordinate descent — for top N from Layer 2, sweep each
             damper axis independently to find best per axis
    Layer 4: Neighborhood polish — full ±1 step neighborhood for top N,
             guarantees locally optimal result

Budget tiers (--search-mode flag in pipeline/produce.py):
    quick:      L1=1k,  keep=200, L3 top=50,   L4 top=10  (~5s)
    standard:   L1=10k, keep=500, L3 top=200,  L4 top=25  (~4 min)
    exhaustive: L1=50k, keep=2k,  L3 top=500,  L4 top=50  (~80 min)

Key insight from enhancementplan.md:
    Perch offsets are NOT searched — they're computed via compute_perch_offsets().
    This alone reduces search space by ~600,000× vs. the original Tier A set.

Usage:
    from solver.grid_search import GridSearchEngine
    engine = GridSearchEngine(space, objective, car, track)
    result = engine.run(budget='quick')
    print(result.summary())
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from itertools import product as cart_product
from typing import Callable

from car_model.cars import CarModel
from solver.legal_space import LegalSpace, LegalCandidate, compute_perch_offsets
from solver.legality_engine import validate_candidate_legality
from solver.objective import ObjectiveFunction, CandidateEvaluation
from track_model.profile import TrackProfile


# ─── Layer 1: Platform skeleton dimensions ──────────────────────────────────
# These define the aerodynamic + vertical dynamics "skeleton" of the setup.
# Wing sets DF level; pushrods set rake; heave/third/rear springs set platform
# compliance. Searching these with Sobol covers the main trade-off space.
LAYER1_KEYS: list[str] = [
    "wing_angle_deg",
    "front_pushrod_offset_mm",
    "rear_pushrod_offset_mm",
    "front_heave_spring_nmm",
    "rear_third_spring_nmm",
    "rear_spring_rate_nmm",
]

# ─── Layer 2: Balance tuning dimensions ─────────────────────────────────────
# Given a fixed skeleton, these control lateral balance (LLTD + DF distribution).
# torsion_OD × ARB_F × ARB_R: exhaustive (14 × 5 × 5 = 350 combos per skeleton)
# camber/bias/diff: coarsened to 3 levels (lo/mid/hi) — refined in Layer 4
LAYER2_EXHAUSTIVE_KEYS: list[str] = [
    "front_torsion_od_mm",  # 14 discrete options
    "front_arb_blade",       # 1-5 (5 values)
    "rear_arb_blade",        # 1-5 (5 values)
]
LAYER2_COARSE_KEYS: list[str] = [
    "front_camber_deg",
    "rear_camber_deg",
    "brake_bias_pct",
    "diff_preload_nm",
]

# ─── Layer 3: Damper axes ────────────────────────────────────────────────────
# Independent coordinate descent per axis — optimize one damper at a time.
# 10 axes × ~12 clicks each = 120 evals per candidate.
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

# ─── Budget tiers ────────────────────────────────────────────────────────────
# Runtime estimates at ~0.8ms/eval in Python (single-threaded):
#   quick:     ~15-30s   (L1=0.4s, L2=3s, L3=2s, L4=0.5s)
#   standard:  ~4-6 min  (L1=4s, L2=3min, L3=30s, L4=5s)
#   exhaustive: ~25-40 min (L1=16s, L2=25min, L3=5min, L4=2min)
#
# l2_from_l1: how many L1 skeletons feed into the L2 balance grid.
#   This is the main lever controlling L2 cost:
#   cost = l2_from_l1 × (|torsion_od| × |arb_f| × |arb_r|) × coarse_levels^4
#   at 14×5×5=350 exhaustive combos and 3^4=81 coarse combos:
#   standard: 50 × 350 × 4 = 70,000 evals ≈ 56s
#   exhaustive: 200 × 350 × 9 = 630,000 evals ≈ 504s

BUDGET_TIERS: dict[str, dict] = {
    "quick": {
        "l1_n_sobol": 500,
        "l1_keep": 50,           # top 50 retained from L1 output
        "l2_from_l1": 5,         # only top 5 skeletons go into L2 balance grid
        "l2_coarse_levels": 1,   # 1 level = midpoint only (skip coarse grid crossings)
        "l3_top_n": 10,
        "l4_top_n": 5,
        # Expected total: 500 + 5×350 + 10×(10×12) + 5×(46×3) ≈ 3,620 evals ≈ 3s
    },
    "standard": {
        "l1_n_sobol": 5_000,
        "l1_keep": 200,
        "l2_from_l1": 50,        # 50 skeletons → 50×350×4 = 70,000 evals
        "l2_coarse_levels": 2,   # lo/hi only → 2^4=16 coarse combos
        "l3_top_n": 50,
        "l4_top_n": 10,
        # Expected total: 5k + 50×350×16 + 50×(10×12) + 10×(46×5) ≈ 289,000 ≈ 4 min
    },
    "exhaustive": {
        "l1_n_sobol": 20_000,
        "l1_keep": 500,
        "l2_from_l1": 200,       # 200 skeletons → 200×350×81 = 5.7M evals
        "l2_coarse_levels": 3,   # lo/mid/hi → 3^4=81 coarse combos
        "l3_top_n": 200,
        "l4_top_n": 25,
        # Expected total: 20k + 200×350×81 + 200×(10×12) + 25×(46×10) ≈ 5.9M ≈ 80 min
    },
}


@dataclass
class LayerStats:
    """Statistics for a single search layer."""
    name: str
    evaluated: int = 0
    kept: int = 0
    vetoed: int = 0
    elapsed_s: float = 0.0
    best_score_ms: float = -1e9

    def summary(self) -> str:
        rate = self.evaluated / max(self.elapsed_s, 0.001)
        return (
            f"  {self.name}: evaluated={self.evaluated:,} kept={self.kept:,} "
            f"vetoed={self.vetoed:,} best={self.best_score_ms:+.1f}ms "
            f"time={self.elapsed_s:.1f}s ({rate:,.0f}/s)"
        )


@dataclass
class GridSearchResult:
    """Complete result of a hierarchical grid search."""
    best_overall: CandidateEvaluation | None = None
    best_robust: CandidateEvaluation | None = None
    best_aggressive: CandidateEvaluation | None = None
    top_candidates: list[CandidateEvaluation] = field(default_factory=list)
    layer_stats: list[LayerStats] = field(default_factory=list)
    budget: str = "standard"
    total_evaluated: int = 0
    total_elapsed_s: float = 0.0

    def summary(self) -> str:
        lines = [
            "=" * 63,
            "  GRID SEARCH RESULTS",
            "=" * 63,
            f"  Budget: {self.budget} | Total: {self.total_evaluated:,} evals "
            f"in {self.total_elapsed_s:.1f}s",
            "",
        ]
        for stat in self.layer_stats:
            lines.append(stat.summary())
        lines.append("")

        def _fmt(label: str, ev: CandidateEvaluation | None) -> list[str]:
            if ev is None:
                return [f"  {label}: (none)"]
            out = [f"  {label}: score={ev.score:+.1f}ms family={ev.family}"]
            out.append(ev.breakdown.summary())
            if ev.physics:
                p = ev.physics
                out.append(
                    f"    Excursion F={p.front_excursion_mm:.1f}mm "
                    f"R={p.rear_excursion_mm:.1f}mm "
                    f"stall={p.stall_margin_mm:+.1f}mm "
                    f"LLTD={p.lltd:.1%}"
                )
            # Key params
            pk = ev.params
            param_pairs = [
                ("wing", "wing_angle_deg"),
                ("heave", "front_heave_spring_nmm"),
                ("third", "rear_third_spring_nmm"),
                ("tor", "front_torsion_od_mm"),
                ("ARB_F", "front_arb_blade"),
                ("ARB_R", "rear_arb_blade"),
                ("cam_F", "front_camber_deg"),
                ("cam_R", "rear_camber_deg"),
            ]
            parts = [f"{k}={pk.get(v, '?')}" for k, v in param_pairs if v in pk]
            out.append(f"    {' | '.join(parts)}")
            return out

        lines.extend(_fmt("Best Overall", self.best_overall))
        lines.append("")
        lines.extend(_fmt("Best Robust", self.best_robust))
        lines.append("")

        # Top 10
        if self.top_candidates:
            lines.append("  --- Top 10 candidates ---")
            for i, ev in enumerate(self.top_candidates[:10], 1):
                phys = ""
                if ev.physics:
                    phys = (f" | LLTD={ev.physics.lltd:.1%}"
                            f" exc={ev.physics.front_excursion_mm:.1f}mm"
                            f" ζLS={ev.physics.zeta_ls_front:.2f}")
                lines.append(
                    f"  {i:2d}. [{ev.family:<20s}] {ev.score:+7.1f}ms{phys}"
                )

        lines.append("=" * 63)
        return "\n".join(lines)


class GridSearchEngine:
    """Hierarchical exhaustive search across the legal setup manifold.

    Decomposes the ~10³⁰ legal space into structured layers that are
    individually tractable, combining systematic coverage with physics-
    informed filtering at each stage.
    """

    def __init__(
        self,
        space: LegalSpace,
        objective: ObjectiveFunction,
        car: CarModel,
        track: TrackProfile | None,
        progress_cb: Callable[[str], None] | None = None,
    ):
        """
        Args:
            space:       LegalSpace for the car (provides snapping, dimension info)
            objective:   ObjectiveFunction for scoring
            car:         CarModel
            track:       TrackProfile (or None — physics scores are reduced)
            progress_cb: Optional callback for progress messages (e.g., print)
        """
        self.space = space
        self.objective = objective
        self.car = car
        self.track = track
        self._log = progress_cb or (lambda s: None)

    def _score_candidates(
        self,
        param_list: list[dict[str, float]],
        family: str,
        layer: int,
    ) -> list[CandidateEvaluation]:
        """Score a list of candidate param dicts, filtering hard vetoes."""
        evals = self.objective.evaluate_batch(param_list, family=family, layer=layer)
        # Apply legality hard veto check
        for ev in evals:
            legality = validate_candidate_legality(ev.params, self.car)
            if legality.hard_veto:
                ev.hard_vetoed = True
                ev.veto_reasons.extend(legality.hard_veto_reasons)
        return evals

    def _top_n(
        self,
        evals: list[CandidateEvaluation],
        n: int,
    ) -> list[CandidateEvaluation]:
        """Return top N non-vetoed candidates sorted by score descending."""
        selectable = [e for e in evals if not e.hard_vetoed]
        selectable.sort(key=lambda e: e.score, reverse=True)
        return selectable[:n]

    def _fill_defaults(self, params: dict[str, float]) -> dict[str, float]:
        """Fill any missing Tier A keys with mid-range values."""
        result = dict(params)
        for dim in self.space.tier_a():
            if dim.name not in result:
                result[dim.name] = dim.snap((dim.lo + dim.hi) / 2)
        # Add computed perch offsets
        result.update(compute_perch_offsets(result, self.car))
        return result

    def layer1_platform_skeletons(
        self,
        n_sobol: int = 10_000,
        keep: int = 500,
    ) -> tuple[list[CandidateEvaluation], LayerStats]:
        """Layer 1: Sobol sample platform skeleton params, physics-filter to top N.

        Platform skeleton = wing angle, pushrods, heave spring, third spring, rear spring.
        These define the aerodynamic + vertical dynamics envelope. Scoring uses
        platform_risk + lap_gain only (Layer 1 fast path).

        Physics basis:
          - Wing sets DF level → determines minimum safe front RH (vortex threshold)
          - Heave/third springs → dynamic excursion → bottoming margin
          - Pushrods → static RH offset → feeds into aero compression model
          Sobol sequence gives uniform coverage of this 6D skeleton space.

        Args:
            n_sobol: Number of Sobol samples to generate
            keep:    How many to keep for Layer 2

        Returns:
            (top_evals, stats)
        """
        stat = LayerStats(name="Layer 1 (platform skeleton)")
        t0 = time.time()
        self._log(f"Layer 1: generating {n_sobol:,} Sobol samples over platform dims...")

        # Sobol sample over layer 1 keys that exist in this space
        l1_keys = [k for k in LAYER1_KEYS if k in self.space._dim_map]
        samples = self.space.sobol_sample(l1_keys, n=n_sobol)

        # Fill in mid-range defaults for non-sampled dims, add perch offsets
        param_list = [self._fill_defaults(s) for s in samples]

        # Score at Layer 1 speed (platform_risk + lap_gain only)
        evals = self._score_candidates(param_list, family="l1_skeleton", layer=1)

        top = self._top_n(evals, keep)
        stat.evaluated = len(evals)
        stat.kept = len(top)
        stat.vetoed = sum(1 for e in evals if e.hard_vetoed)
        stat.elapsed_s = time.time() - t0
        stat.best_score_ms = top[0].score if top else -1e9

        self._log(f"  → kept {stat.kept} / {stat.evaluated:,} | "
                  f"best={stat.best_score_ms:+.1f}ms | {stat.elapsed_s:.1f}s")
        return top, stat

    def layer2_balance_grid(
        self,
        skeletons: list[CandidateEvaluation],
        keep: int = 500,
        coarse_levels: int = 3,
    ) -> tuple[list[CandidateEvaluation], LayerStats]:
        """Layer 2: For each skeleton, exhaustive grid over torsion_OD × ARB_F × ARB_R.

        Physics basis:
          - torsion_OD controls front wheel rate → dominant factor in LLTD
            (k_roll_front ∝ C * OD^4 where C = torsion bar spring constant)
          - ARB blades modulate roll stiffness distribution independently of
            heave rate → LLTD fine-tuning without changing platform compliance
          - Camber at 3 levels: captures gross contact patch optimization
            (lo = conservative, mid = nominal, hi = aggressive)
          - Brake bias + diff preload at 3 levels: yaw balance coarse tuning

        For each skeleton: 14 × 5 × 5 × 3^4 = 28,350 candidates (at coarse_levels=3).
        This is the "every combo between extremes" layer from the enhancement plan.

        Args:
            skeletons:     Top candidates from Layer 1 (provide the skeleton params)
            keep:          How many to keep total for Layer 3
            coarse_levels: Number of levels (2=lo/hi, 3=lo/mid/hi) for coarse dims

        Returns:
            (top_evals, stats)
        """
        stat = LayerStats(name="Layer 2 (balance grid)")
        t0 = time.time()

        # Pre-compute the exhaustive grid structure once
        # exhaustive: torsion_OD × ARB_F × ARB_R
        l2_ex_keys = [k for k in LAYER2_EXHAUSTIVE_KEYS if k in self.space._dim_map]
        ex_grids = self.space.exhaustive_grid(l2_ex_keys)

        # coarse dims at N levels
        l2_coarse_keys = [k for k in LAYER2_COARSE_KEYS if k in self.space._dim_map]
        coarse_grid = self.space.exhaustive_grid(
            l2_coarse_keys,
            coarse_keys={k: coarse_levels for k in l2_coarse_keys},
        )

        # Cross exhaustive × coarse
        if not ex_grids and not coarse_grid:
            return [], LayerStats(name="Layer 2 (skipped — no dims)")

        # Build combined grid combos per skeleton
        all_evals: list[CandidateEvaluation] = []
        self._log(
            f"Layer 2: {len(skeletons)} skeletons × "
            f"{len(ex_grids):,} ex × {len(coarse_grid):,} coarse "
            f"= {len(skeletons) * len(ex_grids) * max(len(coarse_grid), 1):,} candidates"
        )

        for skel_ev in skeletons:
            skel_params = skel_ev.params

            if ex_grids and coarse_grid:
                # Cross exhaustive × coarse
                combos: list[dict[str, float]] = []
                for ex in ex_grids:
                    for coarse in coarse_grid:
                        p = dict(skel_params)
                        p.update(ex)
                        p.update(coarse)
                        p.update(compute_perch_offsets(p, self.car))
                        combos.append(p)
            elif ex_grids:
                combos = []
                for ex in ex_grids:
                    p = dict(skel_params)
                    p.update(ex)
                    p.update(compute_perch_offsets(p, self.car))
                    combos.append(p)
            else:
                combos = []
                for coarse in coarse_grid:
                    p = dict(skel_params)
                    p.update(coarse)
                    p.update(compute_perch_offsets(p, self.car))
                    combos.append(p)

            # Score all combos for this skeleton at Layer 2 speed
            evals = self._score_candidates(combos, family="l2_balance", layer=2)
            all_evals.extend(evals)

        top = self._top_n(all_evals, keep)
        stat.evaluated = len(all_evals)
        stat.kept = len(top)
        stat.vetoed = sum(1 for e in all_evals if e.hard_vetoed)
        stat.elapsed_s = time.time() - t0
        stat.best_score_ms = top[0].score if top else -1e9

        self._log(f"  → kept {stat.kept:,} / {stat.evaluated:,} | "
                  f"best={stat.best_score_ms:+.1f}ms | {stat.elapsed_s:.1f}s")
        return top, stat

    def layer3_damper_coordinate_descent(
        self,
        candidates: list[CandidateEvaluation],
        top_n: int = 200,
    ) -> tuple[list[CandidateEvaluation], LayerStats]:
        """Layer 3: Coordinate descent over damper axes.

        For each candidate, sweeps each damper dimension independently (holding
        all others at current best) and keeps the best per axis. Iterates until
        no improvement is found or max_rounds exceeded.

        Physics basis:
          - Low-speed dampers (LS_comp, LS_rbd) control slow body motion:
            entry oversteer (LS_rbd front), mid-corner understeer (LS_comp rear)
          - High-speed dampers (HS_comp, HS_rbd) control platform over kerbs:
            too stiff HS = platform bouncing; too soft = aero instability
          - Damper slope (HS_slope) sets the LS→HS transition speed
          - Rebound:Compression ratio target ≈ 2:1 for neutral transient response
            (rebound must control the return from bump without jack)
          - Front:Rear HS ratio: front stiffer → stable platform; rear stiffer → grip

        Coordinate descent is tractable because damper axes are mostly independent
        in their effect on lap time. Cross-axis interactions are weak compared to
        spring rates or ARBs.

        Args:
            candidates: Input candidates from Layer 2
            top_n:      How many candidates to run descent on

        Returns:
            (refined_evals, stats)
        """
        stat = LayerStats(name="Layer 3 (damper coord descent)")
        t0 = time.time()

        if not candidates:
            return [], stat

        working = list(candidates[:top_n])
        damper_dims = [k for k in DAMPER_KEYS if k in self.space._dim_map]
        self._log(
            f"Layer 3: coordinate descent on {len(working)} candidates "
            f"× {len(damper_dims)} damper axes"
        )

        total_evals = 0
        results: list[CandidateEvaluation] = []

        for cand_ev in working:
            best_params = dict(cand_ev.params)
            best_score = cand_ev.score

            for _round in range(3):  # max 3 passes over all axes
                improved = False
                for axis in damper_dims:
                    if axis not in self.space._dim_map:
                        continue
                    dim = self.space._dim_map[axis]
                    axis_vals = dim.legal_values()
                    if not axis_vals:
                        continue

                    # Sweep this axis, hold others fixed
                    sweep_params = [{**best_params, axis: v} for v in axis_vals]
                    sweep_evals = self._score_candidates(
                        sweep_params, family=f"l3_sweep_{axis}", layer=3
                    )
                    total_evals += len(sweep_evals)

                    best_in_sweep = max(
                        (e for e in sweep_evals if not e.hard_vetoed),
                        key=lambda e: e.score,
                        default=None,
                    )
                    if best_in_sweep and best_in_sweep.score > best_score + 0.01:
                        best_params = dict(best_in_sweep.params)
                        best_score = best_in_sweep.score
                        improved = True

                if not improved:
                    break  # converged

            # Final evaluation at full Layer 3 quality
            final_eval = self.objective.evaluate(best_params, family="l3_refined")
            results.append(final_eval)

        top = self._top_n(results, len(results))
        stat.evaluated = total_evals
        stat.kept = len(top)
        stat.vetoed = sum(1 for e in results if e.hard_vetoed)
        stat.elapsed_s = time.time() - t0
        stat.best_score_ms = top[0].score if top else -1e9

        self._log(f"  → best={stat.best_score_ms:+.1f}ms | {stat.elapsed_s:.1f}s")
        return top, stat

    def layer4_neighborhood_polish(
        self,
        candidates: list[CandidateEvaluation],
        top_n: int = 25,
    ) -> tuple[list[CandidateEvaluation], LayerStats]:
        """Layer 4: Full ±1 step neighborhood in all Tier A dims.

        For each candidate, evaluates all ±1 step neighbors across all 23 Tier A
        dimensions. If any neighbor is better, moves to it and repeats until no
        improvement is found (guaranteed local optimum).

        This is a steepest-descent hill climber in the discrete legal manifold.
        For 23 dims at ±1: 46 neighbors per candidate per iteration.

        Physics basis:
          Each ±1 step is the minimum legal increment in that dimension.
          For continuous dims (camber, springs): one resolution step.
          For discrete dims (ARB blades, dampers): one integer click.
          Local optimality in this discrete sense is meaningful — no adjacent
          setup is strictly better in the full objective.

        Args:
            candidates: Input candidates from Layer 3
            top_n:      How many to polish

        Returns:
            (polished_evals, stats)
        """
        stat = LayerStats(name="Layer 4 (neighborhood polish)")
        t0 = time.time()

        if not candidates:
            return [], stat

        working = list(candidates[:top_n])
        all_keys = [d.name for d in self.space.tier_a()]
        self._log(
            f"Layer 4: neighborhood polish on {len(working)} candidates "
            f"({len(all_keys)} dims × ±1 = {len(all_keys)*2} neighbors each)"
        )

        total_evals = 0
        results: list[CandidateEvaluation] = []

        for cand_ev in working:
            best_params = dict(cand_ev.params)
            best_score = cand_ev.score

            for _iter in range(20):  # max 20 iterations (prevents infinite loops)
                neighbors = self.space.neighborhood(best_params, steps=1, keys=all_keys)
                # Also add perch offsets for each neighbor
                neighbors = [
                    {**nb, **compute_perch_offsets(nb, self.car)} for nb in neighbors
                ]
                if not neighbors:
                    break

                nb_evals = self._score_candidates(neighbors, family="l4_neighbor", layer=4)
                total_evals += len(nb_evals)

                best_nb = max(
                    (e for e in nb_evals if not e.hard_vetoed),
                    key=lambda e: e.score,
                    default=None,
                )
                if best_nb and best_nb.score > best_score + 0.01:
                    best_params = dict(best_nb.params)
                    best_score = best_nb.score
                else:
                    break  # local optimum reached

            # Final score at full quality
            final = self.objective.evaluate(best_params, family="l4_polished")
            results.append(final)

        top = self._top_n(results, len(results))
        stat.evaluated = total_evals
        stat.kept = len(top)
        stat.vetoed = sum(1 for e in results if e.hard_vetoed)
        stat.elapsed_s = time.time() - t0
        stat.best_score_ms = top[0].score if top else -1e9

        self._log(f"  → best={stat.best_score_ms:+.1f}ms | {stat.elapsed_s:.1f}s")
        return top, stat

    def run(
        self,
        budget: str = "standard",
        progress: bool = True,
    ) -> GridSearchResult:
        """Execute the full 4-layer hierarchical search.

        Args:
            budget:   One of 'quick', 'standard', 'exhaustive'
            progress: Whether to print progress messages

        Returns:
            GridSearchResult with all layers populated
        """
        if budget not in BUDGET_TIERS:
            raise ValueError(f"budget must be one of {list(BUDGET_TIERS.keys())}, got {budget!r}")

        cfg = BUDGET_TIERS[budget]
        if progress:
            self._log = print

        t_total = time.time()
        all_stats: list[LayerStats] = []
        total_evals = 0

        l2_from_l1 = cfg.get("l2_from_l1", cfg["l1_keep"])

        self._log(f"\n{'='*63}")
        self._log(f"  GRID SEARCH — budget={budget.upper()}")
        self._log(f"  L1={cfg['l1_n_sobol']:,} Sobol | keep={cfg['l1_keep']} "
                  f"| L2 from={l2_from_l1} skel "
                  f"| L3 top={cfg['l3_top_n']} | L4 top={cfg['l4_top_n']}")
        self._log(f"{'='*63}")

        # ── Layer 1: Platform skeletons ──────────────────────────────
        l1_top, l1_stat = self.layer1_platform_skeletons(
            n_sobol=cfg["l1_n_sobol"],
            keep=cfg["l1_keep"],
        )
        all_stats.append(l1_stat)
        total_evals += l1_stat.evaluated

        if not l1_top:
            self._log("  Layer 1 produced no valid candidates. Aborting.")
            return GridSearchResult(budget=budget, layer_stats=all_stats)

        # ── Layer 2: Balance grid ─────────────────────────────────────
        # Only feed top l2_from_l1 skeletons into the exhaustive balance grid
        l2_skeletons = l1_top[:l2_from_l1]
        l2_top, l2_stat = self.layer2_balance_grid(
            skeletons=l2_skeletons,
            keep=cfg["l1_keep"],       # keep best cfg["l1_keep"] for L3
            coarse_levels=cfg["l2_coarse_levels"],
        )
        all_stats.append(l2_stat)
        total_evals += l2_stat.evaluated

        if not l2_top:
            self._log("  Layer 2 produced no valid candidates. Trying Layer 3 with L1 results.")
            l2_top = l1_top

        # ── Layer 3: Damper coordinate descent ───────────────────────
        l3_top, l3_stat = self.layer3_damper_coordinate_descent(
            candidates=l2_top,
            top_n=cfg["l3_top_n"],
        )
        all_stats.append(l3_stat)
        total_evals += l3_stat.evaluated

        if not l3_top:
            self._log("  Layer 3 produced no results. Using Layer 2 for Layer 4.")
            l3_top = l2_top

        # ── Layer 4: Neighborhood polish ─────────────────────────────
        l4_top, l4_stat = self.layer4_neighborhood_polish(
            candidates=l3_top,
            top_n=cfg["l4_top_n"],
        )
        all_stats.append(l4_stat)
        total_evals += l4_stat.evaluated

        # ── Collect results ───────────────────────────────────────────
        final_pool = l4_top if l4_top else l3_top
        final_pool.sort(key=lambda e: e.score, reverse=True)

        # Best overall: highest score
        best_overall = final_pool[0] if final_pool else None

        # Best robust: highest score with no soft penalties and positive stall margin
        robust_pool = [
            e for e in final_pool
            if not e.soft_penalties
            and (e.physics is None or e.physics.stall_margin_mm >= 0)
        ]
        best_robust = robust_pool[0] if robust_pool else best_overall

        total_elapsed = time.time() - t_total
        self._log(f"\n  TOTAL: {total_evals:,} evals in {total_elapsed:.1f}s | "
                  f"best={best_overall.score:+.1f}ms" if best_overall else "no results")

        return GridSearchResult(
            best_overall=best_overall,
            best_robust=best_robust,
            best_aggressive=final_pool[0] if final_pool else None,
            top_candidates=final_pool[:50],
            layer_stats=all_stats,
            budget=budget,
            total_evaluated=total_evals,
            total_elapsed_s=total_elapsed,
        )
