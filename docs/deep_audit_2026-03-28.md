# IOptimal Deep Audit (2026-03-28)

## Scope
This audit focused on three priorities:
1. Solver logic and every pathway that selects an “optimal” setup.
2. Desktop `.exe` UX/workflow (tray + local web app).
3. Telemetry automation and correlation capacity (including parameter increments and interaction effects).

---

## 1) Solver and Optimal-Setup Determination

### How the program currently determines “optimal”

#### A. Base solve path (primary)
- `run_base_solve()` is the central orchestrator.
- It first attempts `optimize_if_supported()` (BMW/Sebring constrained optimizer).
- If optimizer is unavailable or fully vetoed, it falls back to the sequential 6-step chain:
  1. Rake / ride heights
  2. Heave/third springs
  3. Corner springs
  4. ARBs
  5. Wheel geometry
  6. Dampers
- Final output is validated for legality, decision trace, and predicted telemetry.

#### B. BMW/Sebring constrained optimizer path
- Optimizer is hard-scoped to BMW at Sebring and seeded from `data/calibration_dataset.json`.
- It evaluates discrete seed families + continuous state optimization (SciPy `minimize`) with weighted penalties:
  - static RH error,
  - slider limit overflow,
  - front travel margin collapse,
  - camber drift from seed.
- Failed validation clusters can hard/soft veto candidates via large score penalties.
- Best clean candidate preferred; best penalized candidate used as fallback.

#### C. Legal manifold search path
- Additional search can be enabled via `--free`/`--legal-search` and scenario profiles.
- Search engine supports families and structured candidate generation, with garage increment snapping and legality filtering.
- There is also a layered exhaustive/grid strategy (Sobol + layered refinement + local polish) for broader manifold coverage.

#### D. Auxiliary optimization paths
- Bayesian optimizer (`--bayesian`) with a lightweight GP and expected improvement exists.
- Unconstrained explorer (`--explore`) and multi-speed compromise analyzer (`--multi-speed`) are available.
- These are useful for discovery but are not yet fully fused into a single robust production selector with confidence-gated handoff.

#### E. Comparison/synthesis path
- Multi-session comparison can synthesize a setup by selecting an authority/best session and rematerializing ranked candidate families.
- This is another distinct “optimal” path separate from single-session solve.

### Strengths
- Clear modular solver decomposition.
- Multiple fallback pathways and legality gates reduce catastrophic outputs.
- Candidate veto memory from failed validation clusters is a strong practical safety mechanism.
- Prediction + decision trace are already integrated into final result packaging.

### Main limitations / risks
1. **Fragmented objective landscape**: different pathways use different scoring stacks (objective function ms scoring, candidate ranker normalized score, ad-hoc penalty rules), risking inconsistent behavior.
2. **Scope asymmetry**: the strongest constrained optimizer is BMW/Sebring-specific.
3. **No unified uncertainty-aware arbiter across all solver modes** (sequential vs constrained vs legal-search vs Bayesian/explorer).
4. **Interaction learning is partial**: pairwise/causal hypotheses exist, but a single explicit global interaction model over all major setup parameters is not yet central to solve-time decisions.

### Recommended improvements (solver)
1. **Unify all candidate scoring under one calibrated meta-objective**
   - Create one canonical score API consumed by:
     - constrained optimizer,
     - legal search,
     - synthesis rematerialization,
     - Bayesian/explorer post-ranking.
   - Include uncertainty penalty and support-tier weighting in one place.

2. **Introduce two-stage selection pipeline**
   - Stage 1: hard legality + envelope/cluster veto.
   - Stage 2: Pareto ranking (lap-gain vs stability/safety vs uncertainty), then scalar tie-break.

3. **Promote optimizer architecture beyond BMW/Sebring**
   - Keep seed-based constrained search pattern, but move to car/track plugin calibration datasets.
   - Graceful fallback to generic priors when no track-specific seed dataset exists.

4. **Add robust cross-validation harness for objective calibration**
   - Automate recalibration reports per car/track with held-out validation and drift alarms.

5. **Unify parameter-resolution snapping into one registry-backed policy module**
   - Eliminate duplicated increment logic across solve-chain and candidate search modules.

---

## 2) `.exe` UI and Workflow Audit

### Current workflow architecture
- Desktop app boots watcher, sync client, and local FastAPI web UI.
- Tray icon exposes dashboard, watcher toggle, sync now, bulk import, settings, quit.
- Web app offers 3 run modes:
  - single session,
  - multi-session comparison,
  - track-only solve.
- Background jobs are single-worker serialized (`ThreadPoolExecutor(max_workers=1)`), keeping execution simple and deterministic.

### Strengths
- “Run the solver without touching CLI” UX is clearly communicated.
- Workflow selection is explicit and sensible.
- Settings page includes team/sync and telemetry ingest controls.
- Local-first architecture is practical and resilient for sim-racing workflows.

### UX gaps and improvement opportunities
1. **No guided first-run wizard inside app shell**
   - Existing first-run state is in config, but guided onboarding is limited.
   - Add a 4-step wizard: telemetry folder, car support warning, sync optional, first import.

2. **Long-running run visibility is minimal**
   - Add richer progress phases (extract/diagnose/solve/search/export) and ETA estimates.
   - Show cancellable jobs and queue status.

3. **Mode-specific validation can be stricter pre-submit**
   - e.g., comparison requires >=2 IBTs; track-only requires track text; surface missing warnings.

4. **Decision confidence and support-tier need stronger, always-visible placement**
   - Keep confidence badge pinned near top change list, not only in detail surfaces.

5. **Single worker may bottleneck active team use**
   - Keep default at 1 worker for safety, but permit advanced setting for small bounded parallelism with CPU/load guardrails.

---

## 3) Telemetry Auto-Ingest + Correlation + Parameter Increment Intelligence

### Current state
- Auto-ingest watches local iRacing telemetry directory and processes new IBTs.
- Known cars map to canonical names; unknown cars are metadata-only.
- Knowledge store persists observations/deltas/models/insights in JSON namespaces.
- Delta detector includes explicit causal expectation maps and significance thresholds.
- Team sync client can push observations and pull team models with offline SQLite queueing.

### What exists for increments/parameter effects
- The system already snaps many values to garage increments during candidate materialization and supporting overrides.
- Delta analysis tracks before/after changes, effect metrics, and causal hypotheses.

### Gaps
1. **No single authoritative “increment map + legal domain + coupling constraints” artifact exposed as product data**.
2. **No fully automated global interaction matrix published to users** (e.g., “front heave × rear ARB effect on understeer by speed band”).
3. **Auto ingestion is local-folder-centric**; no native connector for external telemetry services/APIs.
4. **No always-on model retraining pipeline with versioned feature store and drift governance.**

### Recommended roadmap (telemetry + correlation)

#### Phase 1: Data contract hardening
- Build a canonical `parameter_registry` artifact:
  - parameter name,
  - legal bounds,
  - increment,
  - categorical options,
  - coupling rules,
  - solver step ownership.
- Make all modules import this single source.

#### Phase 2: Correlation/interaction engine
- Add nightly or on-demand pipeline that computes:
  - pairwise interactions,
  - partial dependence,
  - sensitivity by speed regime and corner type,
  - confidence intervals by sample count.
- Publish to UI as “what changed performance historically” cards.

#### Phase 3: Online learning loop
- Version model snapshots (timestamped + semantic tag).
- Add drift checks:
  - telemetry distribution drift,
  - objective calibration drift,
  - per-parameter effect drift.
- Auto-demote confidence when drift exceeds thresholds.

#### Phase 4: External telemetry connectors
- Add optional connector framework for remote telemetry source ingestion.
- Keep local file watcher as baseline fallback.

---

## Prioritized Action Plan (suggested order)

1. **Unify scoring and increment policy** (highest leverage).
2. **Ship onboarding + run-progress UX improvements** (highest user-visible gain).
3. **Publish parameter interaction matrix and drift-aware learning updates**.
4. **Generalize constrained optimizer architecture to non-BMW/Sebring targets**.
5. **Add external telemetry connector framework**.

---

## Executive Summary
- The project already has a strong technical skeleton: modular physics solve chain, legality enforcement, veto memory, telemetry-derived learning, and local-first desktop/web UX.
- The biggest next win is **unification**: one scoring policy, one parameter-increment registry, one confidence model, and one cross-mode candidate arbiter.
- For end users, the key improvements are **workflow guidance + progress transparency + explicit confidence communication**.
- For long-term performance gains, invest in **interaction modeling, drift governance, and optimizer generalization across cars/tracks**.
