# codextwo Current Status and Fix Plan (2026-03-28)

## Executive summary
`codextwo` is a substantial Python-first solver stack that ingests IBT telemetry, derives diagnostics and driver context, produces setup candidates, validates legality, predicts behavior, and exports reports and `.sto` output. The repo is already beyond prototype quality in breadth, but it still has one central architectural weakness: multiple solver/search pathways can define “best setup” differently.

## Current status

### What is working well
- Production pipeline exists end to end: telemetry -> analysis -> diagnosis -> solve -> legality -> report/export.
- Modular physical solve stages are present (rake, heave, springs, ARBs, geometry, dampers).
- A stronger constrained optimizer exists for BMW/Sebring.
- Failed-validation memory and legality/veto logic reduce obviously bad outputs.
- Decision trace, prediction, and reporting are already integrated.

### Current limitations
1. **Fragmented ranking**
   - Different pathways use different score/penalty stacks.
   - Candidate search, legal search, constrained optimizer, and synthesis/rematerialization are not all governed by one canonical ranking policy.
2. **Support asymmetry**
   - The strongest optimizer is still BMW/Sebring-specific.
   - Other combinations rely more heavily on the sequential fallback chain.
3. **Calibration gap**
   - The score model is sophisticated but still not convincingly validated as a real-performance selector across combinations.
4. **Repo/runtime ambiguity**
   - Python is the real execution path, but root-level mixed toolchain artifacts still obscure that truth.
5. **Telemetry underuse**
   - Several read channels are not solve-critical, or are only used for context/reporting.

## Module status recommendation

### Production-critical
- `pipeline/produce.py`
- `solver/solve_chain.py`
- `solver/objective.py`
- `solver/full_setup_optimizer.py`
- `solver/supporting_solver.py`
- `solver/legality_engine.py`
- `solver/predictor.py`
- `analyzer/extract.py`
- `analyzer/diagnose.py`
- `analyzer/driver_style.py`

### Supported secondary
- `solver/legal_search.py`
- `solver/grid_search.py`
- `solver/candidate_search.py`
- `solver/bmw_rotation_search.py`

### Experimental / analysis-only until promoted
- `solver/explorer.py`
- `solver/bayesian_optimizer.py`
- `solver/setup_space.py`
- `solver/multi_speed_solver.py`
- `solver/iterative_solver.py`
- `solver/sensitivity.py`
- `solver/uncertainty.py`
- `solver/validation.py`

## Fix plan

### Phase 1 — unify selection truth
1. Create one canonical `score_candidate(...)` / `rank_candidates(...)` API.
2. Make every candidate-producing path call it:
   - constrained optimizer
   - legal search
   - candidate search
   - Bayesian/explorer post-ranking
   - synthesis/rematerialization
3. Separate hard legality/veto from ranking:
   - stage 1: legal/envelope/cluster veto
   - stage 2: rank surviving candidates

### Phase 2 — make calibration enforceable
1. Add held-out validation reports per car/track.
2. Track objective correlation, top-k hit rate, regret, and drift over time.
3. Require calibration reports for every meaningful score-model change.

### Phase 3 — clarify module ownership
1. Add a per-module status tag: `production`, `supported-secondary`, `experimental`, or `legacy`.
2. Deprecate or merge overlapping search/ranking layers where practical.
3. Publish one file that maps all entrypoints to the official production route.

### Phase 4 — clean runtime contract
1. Make Python the explicit authoritative runtime in the repo root.
2. Remove or isolate misleading `package.json` / `node_modules` residue unless a real webapp build requires them.
3. Add a single supported bootstrap path for local development and CI.

### Phase 5 — expand solver-grade telemetry use
1. Promote weather/wetness channels into explicit scenario branching.
2. Add tyre wear and fuel-use-rate into stint/race solve logic.
3. Promote hybrid power/deploy detail into traction/energy-aware reasoning where supported.
4. Use wind/density more directly in aero-sensitive solve contexts.

## Suggested immediate work order
1. Canonical score API and two-stage selection.
2. Telemetry channel classification registry.
3. Module status labels and entrypoint map.
4. Runtime cleanup.
5. Broader calibration harness and non-BMW optimizer generalization.
