# Deep Codebase Audit Report (claw-research)

## 1. Executive summary

- **Observed from code:** The real production-capable workflow is centered on `pipeline/produce.py::produce` -> `solver/solve_chain.py::run_base_solve` -> `solver/legality_engine.py::validate_solution_legality` -> report/export paths in `output/*`.
- **Observed from code:** “Best setup” is not singular. There are multiple active selectors:
  - constrained optimizer score (`solver/full_setup_optimizer.py`)
  - objective score (`solver/objective.py::ObjectiveFunction.evaluate`)
  - candidate-family score (`solver/candidate_search.py::generate_candidate_families` + `solver/candidate_ranker.py::score_from_prediction`)
  - legal-manifold/grid search picks (`solver/legal_search.py`, `solver/grid_search.py`)
- **Observed from code:** BMW/Sebring has privileged support (optimizer gate + active garage output model). Ferrari/Acura do not get the same depth of legality model coverage.
- **Observed from code + `ferrari.json`:** Ferrari setup controls are structurally heterogeneous (indices, labels, turns, per-corner torsion preload, 0–40 click dampers, labeled diff ramp), increasing mapping/calibration risk vs BMW-style assumptions.
- **Inference/judgment:** Primary architecture risk is **path fragmentation + support asymmetry** more than one isolated algorithm bug.
- **Highest-leverage next fixes:** unify final selection policy, fix telemetry naming drift, harden per-car canonical mapping (Ferrari first), and simplify CLI entrypoints/flags.

## 2. Actual production path

### Primary production path
- Entry:
  - `pipeline/produce.py::main`
  - `pipeline/produce.py::produce`
- Core flow (single-IBT):
  1. IBT parse: `track_model/ibt_parser.py::IBTFile`
  2. extraction: `analyzer/extract.py::extract_measurements`
  3. diagnosis/style/context in `produce`
  4. modifier generation: `solver/modifiers.py::compute_modifiers`
  5. base solve: `solver/solve_chain.py::run_base_solve`
  6. legality + trace + prediction: `solver/solve_chain.py::_finalize_result`
  7. optional candidate-family rematerialization: `solver/candidate_search.py::generate_candidate_families`
  8. optional legal-space / grid search application:
     - `solver/legal_search.py::run_legal_search`
     - `solver/grid_search.py::GridSearchEngine.run`
  9. output: report/json/sto via `output/*` and `pipeline/produce.py`

### Supported secondary paths
- Multi-IBT reasoning: `pipeline/reason.py::reason_and_solve` (called by `pipeline/produce.py::produce` for multiple `--ibt` values).
- Standalone physics (no IBT): `solver/solve.py::run_solver`.
- Comparison pipeline: `comparison/__main__.py::main` + `comparison/*`.
- Analyzer wrapper: `analyzer/__main__.py::main` (delegates to `produce_result`).

### Experimental / analysis-only paths
- Experimental flags in `solver/solve.py`: `--explore`, `--bayesian`, `--multi-speed`.
- Offline validation/calibration analytics:
  - `validation/run_validation.py::build_validation_report`
  - `validation/objective_calibration.py::*`

### Likely legacy or overlap-heavy paths
- Root router `__main__.py::run_multi_ibt` overlaps with multi-IBT reasoning flow in `pipeline/produce.py`.
- Root `__main__.py::run_grid_search` overlaps with pipeline-integrated search mode.
- Comparison overlap between root multi-IBT reporting and `comparison/*`.

## 3. How the solver works

### Orchestration path
- `pipeline/produce.py::produce` builds `SolveChainInputs` and calls `solver/solve_chain.py::run_base_solve`.

### Solver stages (sequential path)
- Implemented in `solver/solve_chain.py::_run_sequential_solver`:
  1. `RakeSolver.solve`
  2. `HeaveSolver.solve`
  3. `CornerSpringSolver.solve`
  4. reconcile (`HeaveSolver.reconcile_solution`, `reconcile_ride_heights`)
  5. provisional dampers, then heave/corner refinement
  6. `ARBSolver.solve`
  7. `WheelGeometrySolver.solve`
  8. final `DamperSolver.solve`
  9. apply damper click offsets (`apply_damper_modifiers`)

### Fallback behavior
- `run_base_solve` first attempts `solver/full_setup_optimizer.py::optimize_if_supported`.
- If unsupported or all optimizer candidates vetoed, uses sequential path.
- Failed-cluster matching (`_candidate_veto_for_solution`) can force fallback to lowest-penalty optimizer candidate.

### Optimizer-specific behavior
- `optimize_if_supported` hard-gates to BMW+Sebring (`_is_bmw_sebring`) unless `legacy_solver` requested.
- Uses seed dataset + SLSQP continuous optimization + garage model validity checks.

### Legality/veto flow
- Search-time legality: `solver/legality_engine.py::validate_candidate_legality` (hard veto + soft penalties).
- Final legality: `validate_solution_legality`; returns `validation_tier`:
  - `full` when `car.active_garage_output_model(track_name)` exists
  - `range_clamp` otherwise.

### Where final selection happens
- Base path winner: `run_base_solve`.
- Candidate-family winner: `solver/candidate_search.py::generate_candidate_families` (`max` by `CandidateScore.total` among selectable).
- Legal search accepted winner: `solver/legal_search.py::_run_sampling_search`.
- Grid winner: `solver/grid_search.py::GridSearchEngine.run` then rematerialized in `pipeline/produce.py`.

## 4. How “best” is chosen

### A) Objective function
- **File/function:** `solver/objective.py::ObjectiveFunction.evaluate`
- **What it scores:** weighted penalties/benefits (lap gain, platform, driver mismatch, uncertainty, envelope, staleness, empirical).
- **Canonical/path-specific:** canonical in objective-based search/ranking paths.
- **Veto/penalty interactions:** merges hard-veto flags and soft penalties.
- **Conflict risk:** can disagree with candidate-family ranking and optimizer custom score.

### B) BMW/Sebring constrained optimizer score
- **File/function:** `solver/full_setup_optimizer.py::BMWSebringOptimizer._evaluate_seed`
- **What it scores:** bespoke scalar penalties (balance, LLTD, margins, slider/deflection, etc.).
- **Canonical/path-specific:** path-specific (BMW/Sebring only).
- **Veto interactions:** applies cluster penalties / all-candidates-vetoed behavior.
- **Conflict risk:** objective basis differs from `ObjectiveFunction`.

### C) Candidate-family ranking
- **File/function:** `solver/candidate_search.py::generate_candidate_families`, `solver/candidate_ranker.py::score_from_prediction`
- **What it scores:** safety/performance/stability/confidence/disruption -> `CandidateScore.total`.
- **Canonical/path-specific:** active in single-IBT production flow.
- **Veto interactions:** non-legal candidates become non-selectable.
- **Conflict risk:** this scorer is independent of `ObjectiveFunction`.

### D) Legal-manifold accepted pick
- **File/function:** `solver/legal_search.py::_run_sampling_search`
- **What it scores:** objective-ranked candidates + full rematerialization + legality + prediction sanity by scenario.
- **Canonical/path-specific:** canonical when legal search enabled.
- **Conflict risk:** scenario preference (`best_robust` vs `best_aggressive`) can diverge from base solve and candidate-family winners.

### E) Grid search best
- **File/function:** `solver/grid_search.py::GridSearchEngine.run`
- **What it scores:** layered search with `best_overall` and `best_robust`.
- **Canonical/path-specific:** canonical only under `--search-mode`.
- **Conflict risk:** layered approximations + rematerialization can shift final applied candidate.

## 5. Accuracy/reliability risks

### Scoring/model-calibration risks
- **Observed:** objective correlation remains weak/unstable in `validation/objective_validation.json`.
- **Observed:** naming drift likely suppresses telemetry influence:
  - `solver/objective.py` reads `rear_power_slip_p95`
  - extractor defines `rear_power_slip_ratio_p95` in `analyzer/extract.py`.
- **Inference:** objective may default/fallback where measured slip should contribute, reducing ranking fidelity.

### Path fragmentation risks
- **Observed:** multiple active scoring/selection systems define “best” differently.
- **Observed:** different entrypoints run different multi-IBT behaviors (`__main__.py::run_multi_ibt` vs `pipeline/reason.py::reason_and_solve`).
- **Inference:** reproducibility and auditability degrade across command choices.

### Telemetry underuse risks
- **Observed:** extracted signals like splitter scrape, dominant frequency, wind, gear-at-apex are mostly diagnostic/context, not direct solve controls.
- **Observed:** `solver/diff_solver.py` checks `peak_lat_g_p99` while extractor exposes `peak_lat_g_measured`.
- **Inference:** useful runtime information is partially unwired into control outputs.

### Validation gaps
- **Observed:** only BMW has configured `garage_output_model` in `car_model/cars.py`; others fall to `range_clamp`.
- **Inference:** non-BMW legality confidence is materially lower because full physics/garage constraints are not equally enforced.

### Support asymmetry across car/track
- **Observed:** BMW/Sebring:
  - dedicated optimizer gate
  - active garage model
  - richer calibration constants
- **Observed:** Ferrari/Acura include many `ESTIMATE`-tagged model elements in `car_model/cars.py`.
- **Observed from `ferrari.json`:** Ferrari control schema is more complex (index/label/turns/per-corner behaviors), requiring stronger dedicated mapping/calibration.
- **Inference:** this asymmetry is a direct reason Ferrari/Acura outputs lag BMW quality even when telemetry exists.

## 6. Telemetry channel audit

| Channel or derived metric | Where it is read | Where it is analyzed | Where it affects solver output | Classification | Notes |
|---|---|---|---|---|---|
| `LF/LR/RF/RRrideHeight` -> RH stats/excursion | `analyzer/extract.py::extract_measurements` | `analyzer/diagnose.py` | heave/damper/objective via `measured` | solve-critical | platform/bottoming envelope inputs |
| `LF/LR/RF/RRshockVel` p95/p99 | `extract_measurements` | diagnose + modifiers | `solver/modifiers.py`, `DamperSolver.solve` | solve-critical | spring floor + HS damper offsets |
| `HF/HR shock deflection` | `analyzer/extract.py::_extract_heave_deflection` | diagnose | `HeaveSolver` + modifiers | solve-critical | heave travel/floor logic |
| corner shock deflection (`LF...RR`) | `analyzer/extract.py::_extract_corner_shock_defl` | diagnose | `solver/modifiers.py` (`front_corner_defl_p99_mm`) | solve-critical | travel proximity |
| `understeer_low_speed_deg` | `_extract_handling` | diagnose balance | modifiers + candidate scoring | solve-critical | low-speed balance correction |
| `understeer_high_speed_deg` | `_extract_handling` | diagnose balance | modifiers speed-gradient DF offset | solve-critical | high-speed aero-balance correction |
| directional understeer L/R | `_extract_handling` | diagnose | `solver/modifiers.py` directional LLTD tweak | solve-critical | asymmetry compensation |
| `front_heave_travel_used_pct` | `extract_measurements` | diagnose safety | modifiers floors + candidate safety | solve-critical | repeatedly enforced floor logic |
| `pitch_range_deg` | `analyzer/extract.py::_extract_pitch` | diagnose/platform | `solver/modifiers.py` heave floor | solve-critical | braking platform guard |
| `front_heave_vel_hs_pct` | `extract_measurements` | diagnose/platform | modifiers floor + HS comp | solve-critical | rough-surface control |
| `body_slip_p95_deg` | `_extract_handling` | diagnose/supporting | candidate stability + objective | solve-critical | transient stability signal |
| `rear_power_slip_ratio_p95` | `_extract_handling` | diagnose/supporting | diff/TC/supporting/candidate ranker | solve-critical | objective uses mismatched name |
| `front_braking_lock_ratio_p95` | `_extract_handling` | brake diagnosis | candidate safety/supporting | solve-critical | brake stability |
| tire temps/pressures | `_extract_tyre_data` | thermal diagnosis | supporting pressure targets + objective thermal penalties | diagnostic-only | mostly not direct step target |
| in-car adjustments (`dcBrakeBias`, TC, ARB) | `_extract_in_car_adjustments` | diagnose | supporting recommendations | context-only | driver/current-setup context |
| splitter RH / scrape | `_extract_splitter_rh` | `diagnose.py::_check_safety/_check_platform` | no direct modifier mapping | diagnostic-only | safety reporting, limited solve actuation |
| vortex burst events | extract-derived | diagnose safety | objective/platform/legality context | diagnostic-only | not directly mapped in modifiers |
| roll/pitch rates | `_extract_handling` | damper/platform checks | little/no direct modifier usage | diagnostic-only | analyzed but lightly wired |
| dominant frequencies | `extract_measurements` | reporting/learning | not directly in 6-step equations | context-only | solver uses model priors instead |
| gear-at-apex | `_extract_gear` | report context | no solver use found | unused/effectively-unused | no active consumer in solve path |
| wind speed/direction | `_extract_wind` | reasoning/reporting | no direct core-solver use | context-only | not part of modifier equations |

## 7. Unused, unwired, or overlapping code

### Dead or likely orphaned (high confidence from call graph)
- `solver/validation.py`
- `solver/uncertainty.py`
- `solver/coupling.py`
- `solver/corner_strategy.py`
- `solver/iterative_solver.py`
- **Why:** no active import/call in production paths discovered during audit.

### Analysis-only
- `validation/run_validation.py`
- `validation/objective_calibration.py`
- `validation/observation_mapping.py`
- **Why:** offline evidence/calibration tooling, not runtime solve pipeline.

### Experimental
- `solver/solve.py` experimental mode branches (`--explore`, `--bayesian`, `--multi-speed`).
- **Why:** explicitly flagged experimental in CLI.

### Redundant/overlapping
- Multi-IBT overlap:
  - `__main__.py::run_multi_ibt`
  - `pipeline/produce.py::produce` multi-IBT branch to `pipeline/reason.py::reason_and_solve`
- Grid-search overlap:
  - root `__main__.py::run_grid_search`
  - pipeline-integrated search path in `pipeline/produce.py`
- Comparison overlap:
  - custom root comparison table
  - `comparison/*` stack

### Partially wired
- Extracted telemetry fields that do not materially influence solve controls (splitter/frequency/gear/wind classes).
- Naming mismatches (`rear_power_slip_*`, `peak_lat_g_*`) causing incomplete intended downstream coupling.

## 8. Repo/runtime hygiene issues

- **Entrypoint clarity is weak:** several valid CLIs with overlapping but non-identical behavior (`__main__.py`, `pipeline`, `solver.solve`, `comparison`, `analyzer`, `desktop`).
- **Flag semantic inconsistency:**
  - `--json` path vs boolean differs across CLIs.
  - `--objective-profile` semantics diverge.
- **Legacy compatibility complexity:** hidden/legacy flags still accepted in producer.
- **Potentially misleading root runtime:** root router behavior differs from module-specific CLIs.
- **Structural audit friction:** duplicate ways to run multi-IBT/search paths complicate reproducibility and postmortem analysis.

### Terminal command simplification (recommended canonical set)
- **Primary IBT solve:**  
  `python -m pipeline.produce --car <car> --ibt <session.ibt> --wing <deg> --sto <out.sto>`
- **Multi-IBT reasoning path:**  
  `python -m pipeline.produce --car <car> --ibt <s1.ibt> <s2.ibt> --wing <deg>`
- **Track-only no-IBT baseline:**  
  `python -m solver.solve --car <car> --track <track> --wing <deg>`
- **Dedicated compare/synthesize:**  
  `python -m comparison --car <car> --ibt <s1.ibt> <s2.ibt> --wing <deg>`

## 9. Recommended module status map

### Production-critical
- `pipeline/produce.py`
- `solver/solve_chain.py`
- `analyzer/extract.py`
- `analyzer/diagnose.py`
- `solver/modifiers.py`
- `solver/legality_engine.py`
- `solver/objective.py`
- `car_model/cars.py`
- `output/*`

### Supported secondary
- `pipeline/reason.py`
- `solver/legal_search.py`
- `solver/grid_search.py`
- `comparison/*`
- `solver/solve.py` (standalone mode)

### Experimental
- Experimental branches in `solver/solve.py`
- Explore-family search toggles in pipeline search paths

### Legacy / merge / deprecate candidates
- root `__main__.py` multi-IBT/grid-search wrappers
- orphan solver modules listed above (pending maintainer confirmation)
- duplicated comparison/search interfaces that are behaviorally overlapping

## 10. Fix plan in priority order

1. **Unify final selection authority**
- Choose one canonical arbitration layer (base solve + objective acceptance) and make all optional search paths report/apply through it.

2. **Fix telemetry schema drift immediately**
- Normalize measured-field names across extractor/objective/diff/supporting.
- Add adapter validation in solve entry to detect missing/renamed fields early.

3. **Ferrari mapping hardening (high leverage)**
- Build strict Ferrari canonical mapper for:
  - heave index conversions
  - torsion OD index + turns
  - diff label-to-option mapping
  - damper 0–40 semantics
- Validate against artifacts like `ferrari.json` and ensure round-trip invariance.

4. **Expand full legality model coverage**
- Add `garage_output_model` for Ferrari and Acura tracks with known data.
- Keep `range_clamp` explicitly exploratory in reports/JSON.

5. **Collapse overlapping runtime paths**
- Make one multi-IBT path authoritative (`pipeline/reason.py` route).
- Deprecate root overlapping commands or convert them to thin aliases.

6. **CLI hygiene**
- Standardize `--json` behavior.
- Remove/rename ambiguous `--objective-profile`.
- Publish minimal command matrix as canonical runtime docs.

7. **Clean dead/orphaned modules**
- Confirm ownership/intent, then remove or archive unused solver modules.

## 11. Appendices

### Exact file/function references
- `pipeline/produce.py::produce`
- `pipeline/reason.py::reason_and_solve`
- `solver/solve_chain.py::run_base_solve`
- `solver/solve_chain.py::_run_sequential_solver`
- `solver/full_setup_optimizer.py::optimize_if_supported`
- `solver/legality_engine.py::validate_solution_legality`
- `solver/legality_engine.py::validate_candidate_legality`
- `solver/objective.py::ObjectiveFunction.evaluate`
- `solver/candidate_search.py::generate_candidate_families`
- `solver/candidate_ranker.py::score_from_prediction`
- `solver/legal_search.py::run_legal_search`
- `solver/grid_search.py::GridSearchEngine.run`
- `analyzer/extract.py::extract_measurements`
- `analyzer/diagnose.py::diagnose`
- `solver/modifiers.py::compute_modifiers`
- `car_model/cars.py`
- `validation/run_validation.py::build_validation_report`
- `validation/objective_validation.json`
- `__main__.py::run_multi_ibt`
- `solver/solve.py::run_solver`
- `comparison/__main__.py::main`
- `analyzer/__main__.py::main`
- `ferrari.json`

### Open questions / uncertainties
- Whether orphan modules are intentionally reserved for upcoming work (not provable from static wiring alone).
- Whether latest local data artifacts supersede `validation/objective_validation.json` snapshot values.
- Exact runtime impact of each mismatch depends on specific flag/entrypoint usage patterns per session.

### Contradictions between docs and code
- Claims of broad support can conflict with code-level gating of optimizer + full legality depth to BMW/Sebring.
- Some documented telemetry significance exceeds actual solver wiring (diagnostic/context-only metrics).

### Important pseudocode summary

```python
# Effective production solve (single-IBT)
measured = extract_measurements(ibt)
diagnosis, driver = diagnose_and_profile(measured)
mods = compute_modifiers(diagnosis, driver, measured)

base = run_base_solve(inputs_with(mods))
# inside run_base_solve:
#   try BMW/Sebring optimizer else sequential 6-step

result = finalize_legality_prediction_trace(base)

if candidate_families_enabled:
    result = select_candidate_family(result)

if legal_or_grid_search_enabled:
    result = rematerialize_best_search_candidate(result)

export_report_json_sto(result)
```

### Ferrari-specific addendum (from `ferrari.json`)
- Confirms non-BMW schema complexity:
  - indexed heave, per-corner torsion turns, letter ARB sizes, labeled diff ramps, 0–40 damper click ranges.
- Supports audit conclusion that Ferrari quality depends on robust car-specific mapping + calibration, not telemetry presence alone.

## Handoff summary (for next model)

Focus next on three concrete workstreams:

1. **Selection unification:** enforce one canonical “applied winner” policy across base solve, candidate families, legal search, and grid search.
2. **Telemetry/schema correctness:** fix `MeasuredState` naming mismatches and add explicit schema checks at solver boundaries.
3. **Ferrari-first mapping + legality depth:** implement rigorous Ferrari canonical mapping (validated against `ferrari.json`) and add/extend full garage-output legality models beyond BMW.

These three will likely produce the largest immediate gain in setup reliability and cross-car consistency.
