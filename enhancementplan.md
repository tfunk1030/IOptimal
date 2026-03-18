# IOptimal Enhancement Plan — Exhaustive Legal Manifold Search

## Current State Assessment (codextwo branch)

### What’s Built and Working

|Module                                 |Status                    |Notes                                                                                                                                                                                               |
|---------------------------------------|--------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
|`solver/legal_space.py` (514 lines)    |**Complete**              |26 Tier A dimensions, `SearchDimension`, `LegalCandidate`, `SearchBounds`, `sample_seeded()`, `sample_uniform()`, `enumerate_discrete_subspace()`, `mutate_candidate()`, `from_car()` factory       |
|`solver/objective.py` (688 lines)      |**Complete**              |Full `ObjectiveFunction` with forward physics eval (excursion, LLTD, damping ratios, DF balance), `PlatformRisk`, `DriverMismatch`, `TelemetryUncertainty`, `EnvelopePenalty`, transparent breakdown|
|`solver/legal_search.py` (382 lines)   |**Functional but limited**|Two-stage search with 6 edge families + uniform scatter. Produces `best_robust`, `best_aggressive`, `best_weird`. Budget-capped random sampling only                                                |
|`solver/legality_engine.py` (180 lines)|**Complete**              |Hard veto vs soft penalty distinction, `validate_candidate_legality()` fast path                                                                                                                    |
|`solver/explorer.py` (318 lines)       |**Legacy**                |Old LHS-based explorer with simple heuristic scores. Not connected to `objective.py`. Superseded by `legal_search.py`                                                                               |
|`pipeline/produce.py` integration      |**Wired**                 |`--explore-legal-space`, `--search-budget`, `--keep-weird` flags working                                                                                                                            |

### The Core Problem

**BMW Sebring Tier A = 26 dimensions × ~1.8×10³⁶ total combinations.**

The current search does random sampling around edge anchors with a budget of ~1,000 candidates. That’s sampling 0.000…% of the space. You’re finding the *neighborhood* of extremes but missing everything in between.

What you want: **systematically evaluate every viable combination between the legal bounds** — not just the edges and their random neighbors.

-----

## The Plan: Hierarchical Exhaustive Search

The key insight: you can’t enumerate 10³⁶ combos, but you CAN enumerate if you decompose the problem into **layered subspaces** where each layer fixes the parameters above it and exhausts the ones below.

### Architecture: 3-Layer Search

```
Layer 1: Platform Skeleton    (wing × pushrod_F × pushrod_R × heave × third × rear_spring)
                               ~6 × 161 × 161 × 91 × 179 × 21 = TOO BIG
                               → Sobol/LHS grid: 50,000 skeletons
                               → Physics filter: keep top 2,000

Layer 2: Balance Tuning        For each surviving skeleton:
                               (torsion_OD × ARB_F × ARB_R × camber_F × camber_R × bias × diff)
                               14 × 5 × 5 × 51 × 41 × 41 × 31 = ~1.5 billion → still too big
                               → Smart grid: fix camber/bias/diff at 3 levels each
                               14 × 5 × 5 × 3 × 3 × 3 × 3 = 28,350 per skeleton
                               → 2,000 skeletons × 28,350 = 56.7M candidates
                               → Physics score on GPU-like batch = feasible in ~10 min

Layer 3: Damper Optimization   For each top-500 from Layer 2:
                               10 damper knobs × 12 clicks each = 12¹⁰ = 61 billion → nope
                               → Coordinate descent: optimize one axis at a time
                               → 10 axes × 12 values = 120 evals per candidate
                               → 500 × 120 = 60,000 evals
                               → Then cross-axis refinement on top 50

Layer 4: Fine Tuning           For top 50 from Layer 3:
                               Full neighborhood enumeration of all ±1 step neighbors
                               26 dims × 2 directions = 52 neighbors per candidate
                               50 × 52 = 2,600 evals → trivial
```

**Total evaluations: ~57M** — aggressive but computable if the per-candidate scorer is fast (current `ObjectiveFunction.evaluate()` does physics in ~0.5ms = ~8 hours at 57M).

### Practical Budget Tiers

|Mode                        |Budget     |What it covers                    |Runtime |
|----------------------------|-----------|----------------------------------|--------|
|`--search-budget quick`     |10,000     |Current behavior + denser sampling|~5 sec  |
|`--search-budget standard`  |500,000    |Layer 1 + Layer 2 at coarse grid  |~4 min  |
|`--search-budget exhaustive`|10,000,000 |Full Layer 1-3 pipeline           |~80 min |
|`--search-budget maximum`   |50,000,000+|Full Layer 1-4 with fine tuning   |~7 hours|

-----

## Implementation: 4 New/Modified Files

### 1. NEW: `solver/grid_search.py` — The Exhaustive Engine

This is the big new module. It replaces the random sampling in `legal_search.py` with structured enumeration.

```python
class GridSearchEngine:
    """Hierarchical exhaustive search across the legal manifold."""
    
    def __init__(self, space: LegalSpace, objective: ObjectiveFunction, car, track):
        ...
    
    def layer1_platform_skeletons(self, n_sobol=50000) -> list[LegalCandidate]:
        """Sobol sequence over platform params (wing, pushrods, heave, third, rear_spring).
        Physics-filter to top N by platform stability + lap gain."""
    
    def layer2_balance_grid(self, skeletons: list[LegalCandidate], 
                            camber_levels=3, bias_levels=3, diff_levels=3
                            ) -> list[LegalCandidate]:
        """For each skeleton: exhaustive grid over torsion_OD × ARB_F × ARB_R,
        crossed with coarse camber/bias/diff levels.
        Score every single combination."""
    
    def layer3_damper_coordinate_descent(self, candidates: list[LegalCandidate],
                                          top_n=500) -> list[LegalCandidate]:
        """For each top candidate: sweep each damper axis independently,
        then cross-refine the top survivors."""
    
    def layer4_neighborhood_polish(self, candidates: list[LegalCandidate],
                                    top_n=50) -> list[LegalCandidate]:
        """Full ±1 step neighborhood for every dimension.
        Guaranteed local optimality."""
    
    def run(self, budget: str = "standard") -> GridSearchResult:
        """Execute the full hierarchical search."""
```

**Key design decisions:**

- Layer 2 is where “every combo between extremes” lives — torsion OD and ARB blades are fully enumerated (14 × 5 × 5 = 350 combos per skeleton)
- Camber/bias/diff get 3 levels (low/mid/high) in Layer 2, then refined in Layer 4
- Perch offsets (heave_perch, third_perch, spring_perch) are computed from physics in each layer, not searched independently — they’re dependent variables

### 2. MODIFY: `solver/legal_space.py` — Add Grid Generation

Add to `LegalSpace`:

```python
def sobol_sample(self, keys: list[str], n: int) -> list[dict[str, float]]:
    """Quasi-random Sobol sequence over specified dimensions.
    Much better space coverage than random sampling."""

def exhaustive_grid(self, keys: list[str], 
                    coarse_keys: dict[str, int] | None = None
                    ) -> list[dict[str, float]]:
    """Full Cartesian product of specified dimensions.
    coarse_keys: {dim_name: n_levels} for dimensions to coarsen."""

def neighborhood(self, params: dict[str, float], 
                 steps: int = 1) -> list[dict[str, float]]:
    """All ±steps neighbors in every dimension. 
    For 26 dims at ±1: 52 neighbors."""
```

### 3. MODIFY: `solver/objective.py` — Batch Evaluation

The current `evaluate()` does one candidate at a time. For 57M candidates, we need vectorized scoring.

```python
def evaluate_batch(self, param_batch: list[dict[str, float]], 
                   **kwargs) -> list[CandidateEvaluation]:
    """Batch evaluation with shared precomputation.
    Pre-computes aero surface lookups, track profile constants,
    and reuses them across the batch."""
```

Also add **per-layer objective profiles**:

- Layer 1: platform_risk + lap_gain only (fast — skip driver/telemetry terms)
- Layer 2: add LLTD + balance scoring
- Layer 3: add damping ratio scoring
- Layer 4: full objective

### 4. MODIFY: `solver/legal_search.py` — Dispatch to Grid Engine

Replace the current `_generate_family_seeds()` random approach with a dispatch:

```python
def run_legal_search(..., mode="standard"):
    if mode in ("exhaustive", "maximum"):
        from solver.grid_search import GridSearchEngine
        engine = GridSearchEngine(space, objective, car, track)
        return engine.run(budget=mode)
    else:
        # Current random sampling path (quick/standard)
        ...
```

-----

## What NOT to Change

- `objective.py` scoring formula and weights — those are already well-calibrated
- `legality_engine.py` hard/soft distinction — correct as-is
- `pipeline/produce.py` integration — just add a `--search-mode` flag
- `explorer.py` — leave as legacy, it’s superseded

-----

## Perch Offset Strategy

**Critical insight:** `front_heave_perch_mm`, `rear_third_perch_mm`, and `rear_spring_perch_mm` are NOT independent search dimensions. They’re **dependent variables** computed from the target ride height + spring rate + pushrod offset.

Currently they’re in Tier A (401 × 36 × 41 = 591,876 combos just for perches), massively inflating the search space. They should be **computed** in each layer, not searched.

```python
# Instead of searching perch offsets:
perch = compute_perch_for_target_rh(
    spring_rate=candidate["front_heave_spring_nmm"],
    pushrod_offset=candidate["front_pushrod_offset_mm"],
    target_front_rh_mm=car.front_rh_mm,  # from rake solver
)
```

This alone reduces the search space by ~600,000× and makes exhaustive Layer 2 grid search practical.

-----

## New CLI Interface

```bash
# Quick exploration (current behavior, improved)
python -m pipeline.produce --car bmw --track sebring --explore-legal-space

# Standard: structured grid search (~4 min)  
python -m pipeline.produce --car bmw --track sebring --explore-legal-space --search-mode standard

# Exhaustive: every combo between extremes (~80 min)
python -m pipeline.produce --car bmw --track sebring --explore-legal-space --search-mode exhaustive

# Maximum: full pipeline with fine-tuning (~7 hours, run overnight)
python -m pipeline.produce --car bmw --track sebring --explore-legal-space --search-mode maximum
```

-----

## Implementation Order

### Sprint 1: Foundation (the perch fix + Sobol)

1. Remove perch offsets from search dimensions → compute them
1. Add `sobol_sample()` to `LegalSpace` (use `scipy.stats.qmc.Sobol`)
1. Add `evaluate_batch()` to `ObjectiveFunction`
1. **Deliverable:** Same pipeline, 100× better space coverage at same budget

### Sprint 2: Layer 1-2 Grid Engine

1. Create `solver/grid_search.py` with `GridSearchEngine`
1. Implement `layer1_platform_skeletons()` with Sobol + physics filter
1. Implement `layer2_balance_grid()` with full enumeration of torsion × ARB × (coarse others)
1. Wire into `legal_search.py` dispatch
1. **Deliverable:** Can find setups that random sampling misses

### Sprint 3: Layer 3-4 Refinement

1. Implement `layer3_damper_coordinate_descent()`
1. Implement `layer4_neighborhood_polish()`
1. Add progress reporting (% complete, ETA, current best)
1. **Deliverable:** Guaranteed locally-optimal results

### Sprint 4: Output + Analysis

1. Add heatmap/sensitivity output: “how does score change as param X varies?”
1. Add Pareto frontier visualization: lap_gain vs platform_risk
1. Add “setup landscape” report: clusters of high-scoring regions
1. **Deliverable:** You can see WHY certain regions of the space are fast

-----

## Dispatch Prompts (for Cowork/Claude Code)

### Sprint 1

> “In IOptimal codextwo, refactor `solver/legal_space.py`: remove `front_heave_perch_mm`, `rear_third_perch_mm`, and `rear_spring_perch_mm` from `TIER_A_KEYS`. These are dependent variables computed from spring rate + pushrod offset + target ride height — they should not be searched independently. Add a `compute_perch_offsets(params, car)` helper that calculates them from the other parameters. Also add `sobol_sample(keys, n)` using `scipy.stats.qmc.Sobol` for quasi-random sampling with much better space coverage than random. Run the existing tests to make sure nothing breaks.”

### Sprint 2

> “In IOptimal codextwo, create `solver/grid_search.py` with a `GridSearchEngine` class. It takes a `LegalSpace`, `ObjectiveFunction`, car, and track. Implement `layer1_platform_skeletons(n_sobol=50000)` that generates Sobol samples over wing, front_pushrod, rear_pushrod, front_heave_spring, rear_third_spring, rear_spring_rate — then physics-filters to top 2,000 by `objective.evaluate()` using platform_risk + lap_gain only. Then implement `layer2_balance_grid(skeletons, top_n=2000)` that for EACH skeleton does a full Cartesian product of front_torsion_od × front_arb_blade × rear_arb_blade (all legal values) crossed with front_camber × rear_camber × brake_bias × diff_preload at 3 levels (lo/mid/hi). Score every single combination with the full objective. Return all candidates ranked by score.”

### Sprint 3

> “In IOptimal codextwo, add `layer3_damper_coordinate_descent(candidates, top_n=500)` to `GridSearchEngine`. For each of the top 500 candidates from Layer 2, sweep each damper dimension independently (front_ls_comp, front_ls_rbd, front_hs_comp, front_hs_rbd, front_hs_slope, and same for rear — 10 axes × 12 clicks = 120 evals per candidate). Keep the best value for each axis. Then for the top 50, do a cross-refinement pass: for each pair of correlated axes (e.g., ls_comp + ls_rbd), enumerate all combinations. Also add `layer4_neighborhood_polish(candidates, top_n=50)` that generates all ±1 step neighbors across all 26 dimensions and evaluates them. Wire the full `run(budget)` method with progress logging.”

### Sprint 4

> “In IOptimal codextwo, enhance the `GridSearchResult.summary()` output to include: (1) parameter sensitivity analysis — for each Tier A dimension, show how the score changes across its range while holding others at the best values, (2) Pareto frontier — show the tradeoff between lap_gain_ms and platform_risk_ms for the top 100 candidates, (3) setup landscape clusters — group the top 200 candidates by similarity and identify distinct ‘fast regions’ in the manifold, (4) for each of the top 5 candidates, show a full diff from the physics baseline. Add `--search-mode quick|standard|exhaustive|maximum` CLI flag to `pipeline/produce.py`.”