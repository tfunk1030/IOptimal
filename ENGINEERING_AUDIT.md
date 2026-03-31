# IOptimal Engineering Audit — Merged Master Reference
**Branch:** `claw-research` | **Date:** 2026-03-31  
**Sources merged:** `deep_audit_report_claw_research.md`, `AUDIT_REPORT.md`, `docs/codebase_audit_2026-03-31.md`  
**Audience:** Engineering model for follow-up fix work  
**Implementation tracker:** See Section 10 — 21/22 items from the 43-file implementation sprint are complete as of 2026-03-31.

---

## Implementation Status (Post-Sprint Summary)

The following fixes from Section 10 were implemented in a 43-file code sprint (856 insertions, 3,031 deletions). Items marked ✅ are live in the codebase; items marked ⏳ require external data collection; items marked ❌ are deferred.

| Fix | Status | Notes |
|-----|--------|-------|
| P0.1: `is_calibrated` flag on DeflectionModel | ✅ Done | BMW isolation working; all non-BMW cars use `.uncalibrated()` |
| P0.2: Ferrari front diff range (−50 to +50 Nm) | ✅ Done | `car_model/cars.py` |
| P0.3: Ferrari torsion bar turns in `.sto` output | ✅ Done | `output/setup_writer.py` |
| P0.4: Bypass garage validator BMW regressions for non-BMW | ✅ **Most important fix** | `output/garage_validator.py` — non-BMW no longer poisoned |
| P0.5: Ferrari heave spring index→N/mm validation | ⏳ Blocked | Needs 9-point garage screenshot sweep |
| P0.6: (see P0.2) | ✅ Done | |
| P1.1: Fix telemetry naming drift | ✅ Done | `rear_power_slip_ratio_p95` and `peak_lat_g_measured` corrected |
| P1.2: Pass `measured_lltd_target` to ARBSolver | ✅ Done | `solver/arb_solver.py` |
| P1.3: Per-car damper zeta targets | ✅ Done | `DamperModel` per-car in `car_model/cars.py` |
| P1.4: ESTIMATE warnings in pipeline report | ✅ Done | `pipeline/report.py`, `solver/predictor.py`, `solver/candidate_ranker.py` |
| P1.5: Wire veto mechanism in `produce.py` | ✅ Done | `learner/observation.py` + `pipeline/produce.py` — now reads `validation_failed` + `setup_fingerprint` fields |
| Phase 2.1: Flag Ferrari rear torsion UNVALIDATED | ✅ Done | `solver/corner_spring_solver.py` warns on 3.5× error |
| Phase 2.2: Ferrari heave spring index table | ⏳ Blocked | Needs garage data |
| Phase 2.3: Ferrari HS rebound slope | ✅ Done | `solver/damper_solver.py` — `hs_slope_rbd` field added |
| Phase 2.4: Ferrari heave damper baselines | ✅ Done | `has_heave_dampers=True` with 5-param baselines per axle |
| Phase 2.5: Handle derived camber | ✅ Done | `camber_is_derived=True` skips Step 5 optimization for Ferrari |
| Phase 2.6: Brake migration output | ✅ Done | `solver/supporting_solver.py` — type + gain fields |
| Phase 3.3: m_eff high-speed filter | ✅ Done | `learner/empirical_models.py` prefers >200kph data |
| Phase 3.4: Selection authority research | ✅ Documented | 5 incompatible scales identified; refactor deferred |
| Phase 4.2: Brake decel asymmetry wiring | ✅ Done | `solver/brake_solver.py` — warns at >1.5 m/s² |
| Phase 4.3: In-car adjustment count authority penalty | ✅ Done | `pipeline/produce.py` — >5 adjustments → up to 50% authority penalty |
| Phase 4.4: Splitter scrape → heave floor | ✅ Done | `solver/modifiers.py` — 42/50 N/mm floors |
| Phase 5.1: Unified CLI + `pyproject.toml` | ✅ Done | `__main__.py` subcommands + installable package |
| Phase 5.2: Delete dead code | ✅ Done | 5 dead solver modules + 6 legacy run scripts deleted |
| Phase 5.3: Fix `.gitignore` | ✅ Done | `.sto`, `tmp_*.bin`, `aeromaps_parsed/`, etc. added |
| Phase 6: Acura/Cadillac calibration | ❌ Deferred | Needs more telemetry sessions |
| Selection authority unification | ❌ Deferred | Large cross-cutting refactor |
| Objective recalibration | ❌ Deferred | Needs IBT session data |

---

## Document Comparison Summary

Before the full audit, here is the ranked quality assessment of the four source documents:

| Rank | Document | Score | Best Contribution |
|------|----------|-------|-------------------|
| 1 | `AUDIT_REPORT.md` | 9.2/10 | Deepest ferrari.json analysis; only source to find rear spring 3.5× contradiction, two damper subsystems, HS rebound slope, negative front diff |
| 2 | `docs/codebase_audit_2026-03-31.md` | 8.7/10 | Best root-cause explanation; unique: Spearman −0.12 correlation, runtime `w_lap_gain` guard, observation counts (BMW=73, Ferrari=9, Acura=7, Porsche=0), scenario weight table |
| 3 | `deep_audit_report_claw_research.md` | 7.5/10 | Best path-fragmentation map; unique: telemetry naming drift, candidate-family as 3rd scoring path, pseudocode summary |
| 4 | `CLI_GUIDE.md` | 7.0/10 | User-facing guide; serves a different purpose entirely |

**Unique critical findings per source:**
- **`AUDIT_REPORT.md` only:** rear spring C constant 3.5× wrong; Ferrari DamperSolver writes to wrong subsystem; HS rebound slope unmapped; fingerprint veto dead in production; negative front diff truncated.
- **`docs/` only:** Spearman −0.12 scoring correlation (noise-level); `w_lap_gain` clamped to ≤0.25 by runtime guard; scenario-profile weight table; garage validator makes non-BMW outputs *worse*.
- **`deep_audit` only:** telemetry naming drift (`rear_power_slip_p95` vs `rear_power_slip_ratio_p95`); candidate-family score as an independent third "best" selector.

---

## 1. Executive Summary

IOptimal is a physics-based GTP/Hypercar setup solver: IBT telemetry → 6-step constraint-satisfaction solver → `.sto` garage file. The architecture is sound and the physics reasoning is genuine — this is not a pattern-matching or ML system.

**The single biggest problem:** The entire calibration chain — garage output models, deflection models, ride height regressions, heave calibration data, damper zeta targets, session database, and objective scoring weights — is **BMW/Sebring-specific**. Ferrari and Acura silently inherit BMW regression coefficients as defaults. The garage validator then "corrects" their solutions using BMW-specific formulas, making the outputs **worse**, not better. This is a structural calibration gap, not a tuning problem.

**The three most important architectural risks:**

1. **No generalized constrained optimizer for non-BMW cars.** `BMWSebringOptimizer` guards on `car.canonical_name == "bmw"`. Ferrari and Acura always fall into the sequential solver using `ESTIMATE`-tagged parameters.

2. **Ferrari and Acura use indexed garage controls, not physical N/mm values.** The decode round-trip uses estimated linear slopes. The rear spring C constant has been confirmed 3.5× wrong from `ferrari.json` ground truth.

3. **Multiple independent "best" selectors.** ObjectiveFunction, BMW optimizer score, candidate-family score, legal-manifold search, and grid search can all disagree on which candidate is best. No canonical arbitration layer exists.

**Objective scoring is unreliable:** Spearman correlation of −0.12 for the best-calibrated path (BMW/Sebring). The scoring function cannot reliably distinguish good from bad setups. A runtime guard clamps `w_lap_gain` to ≤0.25 because the system doesn't trust its own scoring.

**What works well:** The entire BMW Sebring pipeline is production-quality. The 6-step solver chain is complete and well-reasoned. Telemetry extraction is thorough (60+ channels). The learning loop is well-designed. BMW/Sebring has 73 observations, calibrated regressions, and genuine depth.

---

## 2. Actual Production Path

### 2.1 Primary Production Path

**Entry point:** `python -m ioptimal` → `__main__.py::main()`

```
__main__.py::main()
  └─ if single --ibt:
       pipeline/produce.py::produce_result(args)            ← PRIMARY PATH
         Phase A: load car model      (car_model/cars.py::get_car)
         Phase B: build track profile (track_model/build_profile.py::build_profile)
         Phase C: extract telemetry   (analyzer/extract.py::extract_measurements)  [60+ channels]
         Phase D: segment corners     (analyzer/segment.py::segment_lap)
         Phase E: analyze driver      (analyzer/driver_style.py::analyze_driver)
         Phase F: adaptive thresholds (analyzer/adaptive_thresholds.py)
         Phase G: diagnose handling   (analyzer/diagnose.py::diagnose)
         Phase H: aero gradients      (aero_model/gradient.py::compute_gradients)
         Phase I: solver modifiers    (solver/modifiers.py::compute_modifiers)
         Phase J: run solver chain    (solver/solve_chain.py::run_base_solve)
                    └─ full_setup_optimizer.py::optimize_if_supported  [BMW/Sebring ONLY]
                    └─ solve_chain.py::_run_sequential_solver           [ALL cars]
                    └─ supporting_solver.py::SupportingSolver.solve()
                    └─ bmw_rotation_search.py::search_rotation_controls [BMW ONLY]
                    └─ legality_engine.py::validate_solution_legality
                    └─ predictor.py::predict_candidate_telemetry
                    └─ decision_trace.py::build_parameter_decisions
         Phase K: legal/grid search   (solver/legal_search.py, if --free/--search-mode)
         Phase L: garage validation   (output/garage_validator.py::validate_and_fix)
         Phase M: generate report     (pipeline/report.py::generate_report)
         Phase N: export .sto         (output/setup_writer.py::write_sto)
```

### 2.2 Supported Secondary Paths

**Multi-session reasoning:**
```
__main__.py::main()
  └─ if multiple --ibt files:
       pipeline/reason.py::reason_and_solve()         ← MULTI-SESSION PATH
         Phase 1: Extract all sessions
         Phase 2: All-pairs delta computation
         Phase 3: Corner profiling
         Phase 4: Speed-regime analysis
         Phase 5: Target profile synthesis
         Phase 6: Historical integration (learner knowledge store)
         Phase 7: Physics reasoning
         Phase 8: Confidence-gated modifiers
         Phase 9: Solve + Report (delegates to produce.py)
```

| Entry Point | Command | Notes |
|---|---|---|
| Standalone solver (no IBT) | `python -m ioptimal --car bmw --track sebring --wing 16` | Uses pre-built TrackProfile JSON |
| Learner ingestion | `python -m learner.ingest --car bmw --ibt session.ibt` | Stores observations, fits empirical corrections |
| Analyzer only | `python -m analyzer --car bmw --ibt session.ibt` | Diagnosis report without solving |
| Preset comparison | `python -m pipeline --compare base.sto test.sto --car bmw --track sebring` | Side-by-side setup diff |
| Setup validator | `python -m validator --ibt session.ibt --setup setup.sto --car bmw` | Validate setup vs telemetry |
| Comparison module | `python -m comparison --ibt a.ibt b.ibt --car bmw` | Separate scoring/ranking path — **may conflict with pipeline scoring** |
| Webapp | `python -m webapp` | Uvicorn localhost:8000 |

### 2.3 Experimental / Analysis-Only Paths

| Module/Flag | What It Does | Status |
|---|---|---|
| `solver/solve.py::main()` | Older standalone solver CLI — bypasses IBT analysis AND garage validation step | Legacy |
| `--search-mode quick/standard/exhaustive/maximum` | Grid search over legal parameter space | Functional but compute-intensive |
| `--free` flag | Unconstrained legal-manifold search | Experimental |
| `solver/bayesian_optimizer.py` | Gaussian Process optimizer — **never wired in**, not imported in production | Dead |
| `solver/legal_space.py`, `solver/legal_search.py` | Legal manifold search — only called from `solver/solve.py`, NOT from pipeline | Unwired from production |
| `solver/explorer.py` | Parameter space explorer | Not wired |
| `solver/stint_model.py`, `solver/stint_reasoner.py` | Stint-aware compile — only via `solver/solve.py --stint` | Not in `produce.py` |
| `solver/sector_compromise.py` | Sector compromise | Not in `produce.py` |
| `solver/multi_speed_solver.py` | Multi-speed compromise | Partially wired via `reason.py` |
| `solver/laptime_sensitivity.py` | Sensitivity analysis — only via `solver/solve.py --sensitivity` | Not in `produce.py` |
| `track_model/build_profile.py` | Track profile builder | Mostly analysis-only |
| `validation/run_validation.py` | Offline objective calibration | Analysis-only |
| `validation/objective_calibration.py` | Weight search | Analysis-only |

### 2.4 Legacy / Overlap-Heavy Paths

- `run_full_pipeline.py`, `run_full_v2.py`, `run_full_justified.py`, `run_now.py`, `run_tuned_search.py` — 5 ad-hoc runners, all duplicating `python -m ioptimal`. **Should be deleted.**
- `analyzer/__main__.py` — Calls `pipeline.produce.produce_result` directly; another entry point that duplicates `python -m ioptimal`.
- `comparison/__main__.py` vs `validator/__main__.py` — Both are session-comparison tools with overlapping scope.
- Root `__main__.py::run_multi_ibt` overlaps with `pipeline/reason.py::reason_and_solve`.
- Root `__main__.py::run_grid_search` overlaps with pipeline-integrated search mode.

---

## 3. How the Solver Works

### 3.1 Orchestration

**Entry:** `solver/solve_chain.py::run_base_solve(inputs: SolveChainInputs)`

```
run_base_solve(inputs)
  1. Try constrained optimizer:
     full_setup_optimizer.py::optimize_if_supported(...)
       → ONLY runs for BMW + Sebring (guards on car.canonical_name == "bmw")
       → If supported: BMWSebringOptimizer._run() → SLSQP over 6 continuous params
       → Returns OptimizedResult or None

  2. If optimizer returned None OR all_candidates_vetoed:
     _run_sequential_solver(inputs) → (step1..step6, rear_wheel_rate)

  3. Build supporting parameters:
     _build_supporting(inputs) → SupportingSolver.solve()

  4. Optionally refine with BMW rotation search:
     bmw_rotation_search.py::search_rotation_controls(base_result, inputs)
       → BMW only (guards on car.canonical_name == "bmw")
       → CAN OVERRIDE diff, ARB, and spring settings from main solve

  5. Finalize:
     _finalize_result(inputs, step1..step6, supporting)
       → validate_solution_legality(...)  → LegalValidation
       → build_parameter_decisions(...)   → decision trace
       → predict_candidate_telemetry(...) → PredictedTelemetry

  6. Optional: candidate-family rematerialization
     solver/candidate_search.py::generate_candidate_families

  7. Optional: legal/grid search application
     solver/legal_search.py::run_legal_search
     solver/grid_search.py::GridSearchEngine.run
```

### 3.2 Sequential Solver Stages (6-Step)

Each step feeds into the next. Order is mandatory.

| Step | Solver | Output | Key Inputs |
|------|--------|--------|------------|
| 1 | `RakeSolver.solve()` | Static/dynamic RH, pushrod offsets, L/D ratio | target DF balance, car aero map, fuel, wing |
| 2 | `HeaveSolver.solve()` | front_heave_nmm, rear_third_nmm, perch offsets | dynamic RH from step1, m_eff, track bump freq |
| 3 | `CornerSpringSolver.solve()` | torsion bar OD, rear spring rate, wheel rates | heave from step2, fuel, car.corner_spring |
| 4 | `ARBSolver.solve()` | front/rear ARB size and blade, achieved LLTD | wheel rates from step3, target LLTD, lltd_offset |
| 5 | `WheelGeometrySolver.solve()` | camber, toe | roll stiffness from step4, measured camber, fuel |
| 6 | `DamperSolver.solve()` | per-corner LS/HS comp/rbd clicks | wheel rates, dynamic RH, heave/third rates, shock vel |

**⚠️ Two-pass heave/spring solve:** Steps 2 and 3 run **twice**:
- Pass 1: initial heave/spring solve without damper coupling
- Provisional step 6: preliminary damper solve for HS damper coefficients
- Pass 2: re-solve heave/springs with HS damper coupling (`front_hs_damper_nsm`, `rear_hs_damper_nsm`)
- Final step 6: re-solve dampers with corrected spring rates
Then `reconcile_ride_heights()` is called after steps 2, 3, and 5 to enforce ride height constraints.

**⚠️ CRITICAL CONVENTION (Step 3):** Front torsion output is already wheel rate (MR baked in via `C*OD^4`). Rear coil spring is RAW spring rate — must multiply by `rear_motion_ratio^2` for wheel rate.

### 3.3 Fallback Behavior

1. If optimizer succeeds → use optimizer result
2. If optimizer is None (non-BMW car or non-Sebring) → use sequential solver
3. If optimizer returns `all_candidates_vetoed=True` → fall back to sequential solver
4. If sequential result matches a failed cluster fingerprint → try to restore lowest-penalty optimizer candidate
5. If both are vetoed → use sequential anyway with warning

**✅ Fingerprint veto now wired (P1.5 fixed):** `pipeline/produce.py` now loads observations with `validation_failed=True` from `KnowledgeStore`, extracts their `setup_fingerprint` strings, and builds `ValidationCluster` objects into `failed_validation_clusters`. The `Observation` dataclass now has `validation_failed: bool` and `setup_fingerprint: str` fields. The veto mechanism is live; it just won't fire until users or automated tests mark observations as failed.

### 3.4 Constrained Optimizer (BMW/Sebring Only)

`solver/full_setup_optimizer.py::BMWSebringOptimizer._run()`:
1. Generate seed candidates from calibration dataset of real BMW Sebring setups
2. For each seed, run `scipy.optimize.minimize(method="SLSQP")` over 6 continuous variables: `[front_pushrod, rear_pushrod, front_heave_perch, rear_third_perch, front_camber, rear_camber]`
3. SLSQP objective calls `_evaluate_candidate()` → full 6-step solver + `ObjectiveFunction` scoring
4. Validate garage constraints (RH floors, deflection limits, vortex burst, slider position)
5. Fingerprint-veto any candidate matching a failed cluster
6. Return best non-vetoed candidate

**Note:** Optimizer only optimizes 6 continuous parameters. Discrete parameters (torsion bar OD, rear spring rate, ARB size/blade) are determined by the sequential solver called inside the objective.

### 3.5 Legality / Veto Flow

1. **Setup fingerprinting** (`solver/setup_fingerprint.py`): Candidates hashed to fingerprints (wing, RH bucket, spring bucket, etc.)
2. **Failed cluster matching**: Match → hard veto (+1,000,000 ms penalty) or soft veto (+50,000 ms)
3. **Legal validation** (`solver/legality_engine.py::validate_solution_legality()`): Checks GTP class rules — ride height min (30mm), wing angle, fuel. Returns `LegalValidation`. **Called AFTER solve — cannot prevent illegal setups, only flags them.**
4. **Garage validation** (`output/garage_validator.py::validate_and_fix()`): Predicts iRacing display values, auto-corrects if out of range. **✅ P0.4 fixed: Now bypasses BMW regressions when `DeflectionModel.is_calibrated=False` or `GarageOutputModel=None`. Non-BMW cars are no longer poisoned by BMW coefficients.**
5. **Prediction sanity** (`solver/scenario_profiles.py::prediction_passes_sanity()`): Scenario-specific limits

`solver/legal_space.py` and `solver/legal_search.py` define a constraint manifold. Called from `solver/solve.py --legal-search` but **NOT from `pipeline/produce.py`** — unwired from production.

### 3.6 Where Final Selection Happens

**Multiple independent "best" selectors exist — no canonical arbitration:**

| Selector | Location | When Active | Conflict Risk |
|----------|----------|-------------|---------------|
| Sequential solver winner | `_finalize_result()` | All non-BMW cars, BMW non-Sebring | Single candidate, no ranking |
| BMW optimizer winner | `BMWSebringOptimizer` | BMW+Sebring only | ObjectiveFunction minimum |
| BMW rotation search override | `bmw_rotation_search.py` | BMW only, post-solve | **Can override optimizer's ARB/diff/spring** |
| Candidate-family winner | `solver/candidate_search.py::generate_candidate_families` | Single-IBT production flow | Independent of ObjectiveFunction |
| Legal-manifold accepted pick | `solver/legal_search.py::_run_sampling_search` | Only with `--free` flag, NOT in production | Scenario preference can diverge |
| Grid search best | `solver/grid_search.py::GridSearchEngine.run` | Only with `--search-mode` | Layered approximations |

---

## 4. How "Best" Is Chosen

### 4.1 ObjectiveFunction (`solver/objective.py::ObjectiveFunction.evaluate()`)

**Full formula:**
```
total_score_ms = w_lap_gain     * lap_gain_ms
               - w_platform     * platform_risk_ms
               - w_driver       * driver_mismatch_ms
               - w_uncertainty  * telemetry_uncertainty_ms
               - w_envelope     * envelope_penalty_ms
               - w_staleness    * staleness_ms
               - w_empirical    * empirical_penalty_ms
```
All terms in milliseconds. Higher score = better candidate.

**⚠️ Objective correlation is Spearman −0.12 for BMW/Sebring (the only calibrated path).** This is effectively noise — the system cannot reliably distinguish good from bad setups. A runtime calibration guard clamps `w_lap_gain` to ≤0.25 because correlation is too weak. This means the system doesn't trust its own scoring.

**Scenario-specific weights:**

| Profile | w_platform | w_driver | w_uncertainty | w_envelope | w_staleness | w_empirical | Preferred |
|---------|-----------|---------|--------------|-----------|------------|------------|---------|
| single_lap_safe (default) | 0.75 | 0.30 | 0.40 | 0.55 | 0.15 | 0.20 | best_robust |
| quali | 0.90 | 0.35 | 0.45 | 0.50 | 0.20 | 0.25 | best_aggressive |
| sprint | 1.00 | 0.45 | 0.55 | 0.70 | 0.30 | 0.35 | best_robust |
| race | 1.20 | 0.55 | 0.70 | 0.85 | 0.35 | 0.45 | best_robust |

`w_lap_gain` is always 1.0. Runtime guard clamps to ≤0.25 for BMW/Sebring.

**Lap gain components:** LLTD balance deviation, damping deviation from target zeta, rebound/compression ratio deviation, DF balance error × 20 ms/%, camber deviation, diff/ARB extremes, TC deviation, carcass temperature outside optimal window.

**Platform risk components:** Bottoming risk, vortex risk (wing-specific threshold), slider exhaustion, RH collapse risk.

**Canonical vs path-specific:** ONLY invoked by `BMWSebringOptimizer`. The sequential solver does NOT call ObjectiveFunction — it produces one solution and that is the output.

### 4.2 CandidateRanker (`solver/candidate_ranker.py::score_from_prediction()`)

**What it scores:** Safety / performance / stability / confidence / disruption cost as weighted combination.

**Path-specific:** Only invoked from `pipeline/reason.py` multi-session path. NOT called in `pipeline/produce.py`.

**Conflict risk:** Completely separate scoring mechanism from ObjectiveFunction. Multi-session reasoning scores candidates differently.

### 4.3 BMW Rotation Search (`solver/bmw_rotation_search.py`)

**What it scores:** Diff preload, diff ramp angles, rear ARB, rear spring rate, geometry — scored against telemetry-derived rotation target (yaw rate, lateral G, brake stability).

**BMW-only, post-solve override:** If the rotation search picks Soft ARB but the optimizer chose Medium ARB, the rotation search wins. The final output is NOT the ObjectiveFunction minimum.

### 4.4 k-NN Empirical Scoring (`solver/session_database.py`)

- Loads 76+ BMW/Sebring sessions, finds k-nearest neighbors in setup space
- Weight: `w_empirical = 0.20–0.45` depending on scenario
- **BMW/Sebring only** — no session database for other cars (returns empty)

### 4.5 Conflicting LLTD Targets

```python
# solver/arb_solver.py — targets theoretical LLTD
lltd_target = car.weight_dist_front + (car.tyre_load_sensitivity / 0.20) * 0.05
# BMW: 0.4727 + (0.22/0.20)*0.05 = 0.528

# solver/objective.py — scores against measured LLTD
lltd_target = car.measured_lltd_target or (car.weight_dist_front + ...)
# BMW: car.measured_lltd_target = 0.41  ← Different!
```

For all non-optimizer paths (non-BMW, non-Sebring), the ARB solver targets 0.528 but measured behavior is 0.41 → ARB will be systematically stiffer than optimal. Non-BMW cars have no measured target at all.

**Note:** `lltd_measured` from telemetry is actually a roll stiffness distribution proxy, not true LLTD. The solver uses this proxy for LLTD targeting.

### 4.6 Summary: All "Best" Mechanisms

| Mechanism | File | Used in Production? | LLTD Target | Selection Criterion |
|-----------|------|---------------------|-------------|---------------------|
| ObjectiveFunction | `objective.py` | BMW optimizer only | 0.41 (calibrated) | Min total score |
| Sequential solver ARB | `arb_solver.py` | All cars | 0.528 (theoretical) | Force-balance physics |
| CandidateRanker | `candidate_ranker.py` | Multi-session path only | N/A | Weighted composite |
| BMW rotation search | `bmw_rotation_search.py` | BMW only, post-solve | N/A | Telemetry rotation score |
| Legal manifold search | `legal_search.py` | NOT in production | N/A | Legal constraint |

---

## 5. Accuracy / Reliability Risks

### 5.1 Scoring / Model-Calibration Risks

**ROOT CAUSE: BMW-only calibration chain**

| Component | BMW/Sebring | Ferrari | Acura | Cadillac | Porsche |
|-----------|------------|---------|-------|----------|---------|
| `GarageOutputModel` | ✅ Calibrated (31 setups) | ❌ None | ❌ None | ❌ None | ❌ None |
| `DeflectionModel` | ✅ R-sq=0.90+ | ❌ BMW defaults | ❌ All zeros | ❌ BMW defaults | ❌ BMW defaults |
| `RideHeightModel` | ✅ R-sq=0.52/0.15 | ❌ All zeros → 30mm const | ❌ All zeros | ❌ None | ❌ None |
| Heave calibration | ✅ JSON exists | ❌ None | ❌ None | ❌ None | ❌ None |
| Session DB (k-NN) | ✅ 76+ sessions | ❌ Empty | ❌ Empty | ❌ Empty | ❌ Empty |
| Damper zeta targets | ✅ IBT-calibrated | ✅ Per-car defaults (P1.3) | ✅ Per-car defaults (P1.3) | ✅ Per-car defaults (P1.3) | ✅ Per-car defaults (P1.3) |
| Objective weights | ✅ Searched | ❌ BMW weights | ❌ BMW weights | ❌ BMW weights | ❌ BMW weights |
| Constrained optimizer | ✅ Sebring only | ❌ | ❌ | ❌ | ❌ |
| m_eff calibrated | ✅ | ❌ | partial | partial | ❌ |
| Control index decode | N/A | partial | BROKEN | N/A | N/A |
| Rotation search | ✅ | ❌ | ❌ | ❌ | ❌ |
| Observations collected | **73** | **9** | **7** | few | **0** |

**This is why Ferrari and Acura produce bad setups.** Every regression coefficient was fit to BMW/Sebring data. Non-BMW cars silently inherit these values, and the garage validator "corrects" their solutions using BMW-specific models — making them worse.

**[CRITICAL] Non-BMW cars use BMW regression coefficients for deflection prediction:**
```python
# car_model/cars.py — default DeflectionModel:
@dataclass
class DeflectionModel:
    shock_front_intercept: float = 21.228    # BMW Sebring regression!
    shock_front_pushrod_coeff: float = 0.226  # BMW Sebring regression!
    heave_defl_intercept: float = -20.756    # BMW Sebring regression!
    # All 20+ coefficients are BMW/Sebring regressions

# Ferrari:
FERRARI_499P = CarModel(
    ...
    deflection=DeflectionModel(),  # ← BMW defaults silently applied!
)

# Acura:
ACURA_ARX06 = CarModel(
    ...
    heave_spring=HeaveSpringModel(
        heave_spring_defl_max_intercept_mm=0.0,  # No travel model!
        defl_static_intercept=0.0,               # No deflection model!
    ),
)
```

**[CRITICAL] Ferrari/Acura heave spring index ↔ N/mm mapping is estimated:**
```python
# car_model/cars.py::HeaveSpringModel.front_rate_from_setting()
def front_rate_from_setting(self, setting_value: float) -> float:
    if (self.front_setting_index_range is None ...):
        return float(setting_value)  # Acura: returns index AS N/mm!
    return float(
        self.front_rate_at_anchor_nmm
        + (float(setting_value) - self.front_setting_anchor_index)
        * self.front_rate_per_index_nmm
    )
    # Ferrari: anchor_index=1, rate_at_anchor=50, per_index=20.0 (ESTIMATE)
    # Linear: UNVALIDATED
```

**[CRITICAL — new finding from `ferrari.json`] Ferrari rear spring rate C constant is 3.5× wrong:**
- `rSideSpringRateNpm=105000 N/m = 105.0 N/mm` at OD index 1 from ferrari.json
- Code model predicts: `C=0.001282 × 23.1^4 = 361 N/mm` at OD=23.1mm (minimum of rear range)
- Discrepancy: 3.5× overestimate
- **The rear spring range in `car_model/cars.py` (`rear_spring_range_nmm=(364.0, 590.0)`) is approximately 3.5× too high**
- Either the rear C constant is different from front, or the rear spring physics is not torsion-bar

**[CRITICAL — new finding] Ferrari DamperSolver writes to wrong subsystem:**
Ferrari has **two completely separate damper subsystems** (confirmed from `ferrari.json`):
- **Per-corner dampers (visible in garage):** LF/RF/LR/RR with 0–40 click range for LS/HS comp/rbd
- **Heave dampers (internal, NOT visible in garage):** `hfLowSpeedCompDampSetting=10`, `hfHighSpeedCompDampSetting=40`, etc. (range 1–20+)
The `DamperSolver` computes one set of values and writes to per-corner position. It does not distinguish between subsystems. The heave damper values (`hf*`, `hr*`) need separate computation tied to heave spring rate and pitch dynamics.

**[CRITICAL] Ferrari HS Rebound slope unmapped:**
`ferrari.json` shows `lfHSSlopeRbdDampSetting`, `rfHSSlopeRbdDampSetting`, etc. exist for rebound. Code models `hs_slope` only for compression side.

**[HIGH] BMW `measured_lltd_target=0.41` overrides ARB solver's theoretical LLTD=0.528:**
For BMW non-Sebring and all other cars, the solver targets 0.528 but measured behavior is 0.41 → ARB systematically stiffer than optimal.

**[HIGH] Aero compression model is a single constant per car:**
- `AeroCompression.front_at_speed(v)` = `compression_ref × (v/v_ref)^2` — correct V² scaling
- Reference compression varies with setup (BMW rear: 7.8mm at heave 60 vs 9.5mm at heave 50)
- Cadillac rear was initially 8mm (ESTIMATE) → calibrated to 18.5mm → 2.3× underestimate

**[HIGH] Acura m_eff is rate-dependent but solver uses single constant:**
- Code: `front_m_eff_kg=450.0` (constant)
- Reality: "Front: 641kg at 90 N/mm, 319kg at 190 N/mm" — nonlinear, 2× range
- Acura has `has_roll_dampers=True` (ORECA heave+roll architecture) — fundamentally different effective mass coupling

**[HIGH] Ferrari torsion bar turns completely missing from solver:**
- `ferrari.json` shows LF/RF torsion bar turns: `0.100 Turns`, range `−0.250 to +0.250`
- These directly affect static ride height at all 4 corners
- **Solver never outputs torsion bar turns for Ferrari** — Ferrari ride heights in generated `.sto` files are always wrong because preload contribution is ignored

**[HIGH] Ferrari front diff allows negative preload (code truncates at zero):**
- `ferrari.json`: Front Diff Preload range `−50 to +50 Nm`
- Code: `diff_preload_nm=(0.0, 150.0)` — **WRONG, clamps at zero**. Negative preload is legal for Ferrari front diff.

**[HIGH] Ferrari camber is derived, not settable:**
- `ferrari.json` shows camber values marked `is_derived: true`
- Solver Step 5 outputs camber as if independently adjustable
- For Ferrari, camber is a consequence of ride height + spring settings, not a direct setter

**[MEDIUM — FIXED P1.1] Naming drift was suppressing telemetry influence:**
- ✅ `solver/objective.py` now reads `rear_power_slip_ratio_p95` (canonical name)
- ✅ `solver/diff_solver.py` now reads `peak_lat_g_measured` (canonical name)
- Both fields are now correctly coupled to their extractor sources

### 5.2 Path Fragmentation Risks

**[CRITICAL] Two completely different scoring systems for the same car:**
- BMW at Sebring: ObjectiveFunction → k-NN → rotation search → final output
- BMW at any other track: sequential solver only → no objective scoring → no rotation search
- Ferrari: sequential solver only → no objective scoring

**[HIGH] Multi-session reasoning uses CandidateRanker, not ObjectiveFunction:**
When multiple IBT files are passed, `reason_and_solve()` builds a `TargetProfile` and passes it to the solver via `confidence_gated_modifiers`. Scoring is NOT run through ObjectiveFunction.

**[HIGH] `comparison/` module has its own scoring:**
`comparison/score.py::score_sessions()` defines "best" completely differently from `solver/objective.py`. A user can get conflicting recommendations from the same data.

### 5.3 Telemetry Underuse Risks

**[HIGH] 150+ telemetry fields extracted but most never reach the solver:**
Solver uses primarily: `shock_vel_p99_f_mps`, `shock_vel_p99_r_mps`, `sigma_f_mm`, `sigma_r_mm`, `dynamic_front_rh_mm`, `dynamic_rear_rh_mm`, `lltd_front_pct`.

**[MEDIUM] High-speed filtered metrics available but unused:**
- `front_heave_vel_p95_hs_mps` (>200 kph filtered) — available for filtered m_eff but solver uses lap-wide stats
- `front_rh_std_hs_mm` (>200 kph filtered) — same
- `front_brake_wheel_decel_asymmetry_p95_ms2` — extracted but not wired to any solver step

### 5.4 Validation Gaps

**[HIGH] Legality check runs after solve — solver can produce illegal setups:**
`validate_solution_legality()` is called in `_finalize_result()` after all steps. Only reports violations, does NOT cause re-solve.

**[HIGH — FIXED P1.5] Failed cluster veto now wired in `pipeline/produce.py`:**
`produce.py` now loads observations with `validation_failed=True` from `KnowledgeStore` and builds `ValidationCluster` objects. The `Observation` model now has `validation_failed: bool` and `setup_fingerprint: str` fields. The mechanism fires when observations are marked as failed.

**[HIGH] GarageOutputModel validates only BMW Sebring:**
All other cars have `garage_output_model=None`. Ferrari/Acura `.sto` files may have deflection values outside iRacing's valid range → silent in-game load failures.

**[MEDIUM] Torsion-ARB coupling gamma=0.25 from single BMW data point:**
Physical mechanism is plausible (rocker mount compliance) but unvalidated across OD range.

---

## 6. Telemetry Channel Audit

| Channel / Derived Metric | Where Read | Where Analyzed | Affects Solver | Classification | Notes |
|---|---|---|---|---|---|
| `LF/RF/LR/RR rideHeight` → RH stats/excursion | `extract.py` | mean, σ, p5/p95/p99 | RakeSolver step1, HeaveSolver step2 (bottoming) | **SOLVE-CRITICAL** | Primary ride height target |
| `CFSRrideHeight` (splitter) | `extract.py` | scrape events, splitter RH | Step 2 (floor clearance) | **SOLVE-CRITICAL** | Most important aero channel |
| `LF/RF/LR/RR shockVel` p95/p99 | `extract.py` | `shock_vel_p99_f_mps`, `_r_mps` | HeaveSolver step2, DamperSolver step6 | **SOLVE-CRITICAL** | Most important telemetry for heave/damper |
| `HF/HR shockDefl` / `sigma_f/r_mm` | `extract.py` | Standard deviation of RH | HeaveSolver target constraint | **SOLVE-CRITICAL** | Platform stability constraint |
| `SteeringWheelAngle` + `YawRate` | `extract.py` | understeer angle | ARBSolver step4 (LLTD/balance) | **SOLVE-CRITICAL** | Combined with Speed for Ackermann |
| `Speed` / `LatAccel` / `LongAccel` | `extract.py` | cornering masks, speed regimes | All steps (speed-dependent constraints) | **SOLVE-CRITICAL** | Fundamental filtering channels |
| `Roll` | `extract.py` | roll gradient, roll distribution proxy | ARBSolver step4 (LLTD proxy) | **SOLVE-CRITICAL** | `lltd_measured` is actually roll stiffness dist |
| `Pitch` | `extract.py` | braking pitch range | HeaveSolver step2 (platform stability) | **SOLVE-CRITICAL** | Large braking pitch → heave support inadequate |
| `LF/RF/LR/RR tempL/M/R` | `extract.py` | surface temp spread | WheelGeometrySolver step5 (camber) | **SOLVE-CRITICAL** | Positive spread → inner hotter |
| `CamberRL/RR deg` | `extract.py` | `rear_camber_deg` | WheelGeometrySolver step5 (baseline) | **SOLVE-CRITICAL** | Camber confidence baseline |
| `Throttle / Brake` inputs | `extract.py` | trail-brake ratio, power-on fraction | `driver_style.py` → `lltd_offset` → ARBSolver | **SOLVE-CRITICAL (indirect)** | Driver style → LLTD |
| `DF balance %` (computed) | `aero_model/gradient.py` | DF balance at dynamic RH | RakeSolver target balance | **SOLVE-CRITICAL** | Balance constraint for Step 1 |
| `dominant_bump_freq_hz` | `extract.py` | spectral analysis of RH signal | `RideHeightVariance.dominant_bump_freq_hz` replaces car default | **SOLVE-CRITICAL** | Feeds heave solver frequency target |
| `lltd_front_pct` | computed from lateral G + corner weights | MeasuredState | ObjectiveFunction BMW penalty | **SOLVE-CRITICAL (BMW)** | Only used in BMW ObjectiveFunction |
| `understeer_low/high_speed_deg` | `extract.py` | diagnose balance | modifiers + candidate scoring | **SOLVE-CRITICAL** | Balance correction |
| `HF/HR shockVel` (heave) | `extract.py` | oscillation freq, heave regime | Oscillation >1.5× natural → bump `zeta_hs_rear` | **DIAGNOSTIC** | Feeds heave regime classification |
| `LF/RF/LR/RR brakeLinePress` | `extract.py` | hydraulic brake split | Supporting (brake bias baseline) | **DIAGNOSTIC** | NOT brake torque split |
| `dcBrakeBias` | `extract.py` | live brake bias | Supporting (bias targeting) | **CONTEXT-ONLY** | Driver compensation detection |
| `Tyre temps` (per corner, inner/middle/outer) | `extract.py` | distribution, min/max, range | `diagnose.py` → recommendations only | **DIAGNOSTIC-ONLY** | Not passed to solver |
| `Tyre pressure` (warm psi) | `extract.py` | `tyre_pressure_*` fields | `diagnose.py` only | **DIAGNOSTIC-ONLY** | Not used by solver |
| `BrakeABSactive` / `BrakeABScutPct` | `extract.py` | ABS activity | `diagnose.py` only | **DIAGNOSTIC-ONLY** | Not wired to solver |
| `SteeringWheelAngle deg` | `extract.py` | `steering_angle_p95_deg` | `driver_style.py` → profile only | **DIAGNOSTIC-ONLY** | Contributes to driver style |
| `Pitch angle deg` | `extract.py` | `pitch_angle_deg` | `diagnose.py` handling balance | **DIAGNOSTIC-ONLY** | Not used by solver |
| `front_rh_settle_time_ms` | `extract.py` | event-based settle | DamperSolver (if quality="trusted") | **DIAGNOSTIC** | Damper targeting fallback |
| `rear_power_slip_ratio_p95` | `extract.py` | diagnose/supporting | `solver/objective.py` (P1.1 fixed) | **SOLVE-CRITICAL** | ✅ Naming drift fixed — objective now reads correct canonical name |
| `peak_lat_g_measured` | `extract.py` | diagnose | `solver/diff_solver.py` (P1.1 fixed) | **SOLVE-CRITICAL** | ✅ Naming drift fixed — diff solver now reads correct canonical name |
| `front_brake_wheel_decel_asymmetry_p95_ms2` | `extract.py` | extracted | **Nothing** | **UNUSED** | Not wired to any solver step |
| `front_heave_vel_p95_hs_mps` (>200kph) | `extract.py` | extracted | **Nothing** | **UNUSED** | Available for filtered m_eff, not used |
| `front_rh_std_hs_mm` (>200kph) | `extract.py` | extracted | **Nothing** | **UNUSED** | Same — available but unused |
| `EnergyERSBatteryPct` / `TorqueMGU_K` | `extract.py` | battery/hybrid context | Nothing | **CONTEXT-ONLY** | Not solver-wired |
| `wind_speed_mps` / direction | `extract.py` | reporting | Nothing | **CONTEXT-ONLY** | Not in modifier equations |
| `gear-at-apex` | `extract.py` | report context | Nothing | **UNUSED** | No active consumer in solve path |
| `WaterTemp / OilTemp` | `extract.py` | thermal flags | `diagnose.py` context only | **CONTEXT-ONLY** | Not used by solver |
| `RPM`, `Gear` | `extract.py` | segment context | `driver_style.py` | **CONTEXT-ONLY** | Not used directly by solver |

---

## 7. Unused, Unwired, or Overlapping Code

### 7.1 Dead Code (High Confidence from Call Graph)

| Module | Evidence | Recommendation |
|--------|----------|----------------|
| `solver/bayesian_optimizer.py::BayesianOptimizer` | Never imported in `solve_chain.py`, `produce.py`, or `full_setup_optimizer.py` | Archive |
| `solver/laptime_sensitivity.py` | Only called from `solver/solve.py --sensitivity`; not in `produce.py` | Archive |
| `solver/legal_search.py` + `solver/legal_space.py` | Only called from `solver/solve.py --legal-search`; NOT from `pipeline/produce.py` | Archive or wire in |
| `solver/explorer.py::ParameterExplorer` | Not imported in any production path | Archive |
| `solver/validation.py` | **✅ DELETED (P5.2)** | |
| `solver/uncertainty.py` | **✅ DELETED (P5.2)** | |
| `solver/coupling.py` | **✅ DELETED (P5.2)** | |
| `solver/corner_strategy.py` | **✅ DELETED (P5.2)** | |
| `solver/iterative_solver.py` | **✅ DELETED (P5.2)** | |
| `validation/objective_calibration.py`, `run_validation.py`, `observation_mapping.py` | Offline calibration scripts — not runtime pipeline | Label clearly as analysis-only |

### 7.2 Partially Wired Code

| Module | What's Missing |
|--------|----------------|
| `solver/setup_fingerprint.py` | **✅ P1.5 fixed**: `pipeline/produce.py` now populates `failed_validation_clusters` from `KnowledgeStore` observations with `validation_failed=True` + `setup_fingerprint`. Mechanism fires when observations are marked failed. |
| `solver/multi_speed_solver.py` | Called from `pipeline/reason.py::_analyze_speed_regimes()` but connection to final solve is unclear |
| `learner/` module | Only active for BMW via ObjectiveFunction; k-NN path; no effect on other cars |
| `solver/stint_model.py`, `solver/stint_reasoner.py` | Only via `solver/solve.py --stint`; not in `produce.py` |
| `solver/sector_compromise.py` | Only via `solver/solve.py --sector-compromise`; not in `produce.py` |
| `solver/predictor.py` | Prediction used for sanity checks but not feedback-corrected per car |

### 7.3 Redundant / Overlapping Modules

| Conflict | Files | Risk |
|----------|-------|------|
| Multi-IBT overlap | `__main__.py::run_multi_ibt` vs `pipeline/produce.py` → `pipeline/reason.py` | Different behavior from same data |
| Grid search overlap | Root `__main__.py::run_grid_search` vs pipeline-integrated search | Reproducibility |
| Comparison overlap | Root comparison table vs `comparison/*` stack | User confusion |
| Scoring conflict | `comparison/score.py` vs `solver/objective.py` | Conflicting "best" definitions |
| Synthesis conflict | `comparison/synthesize.py` (weighted blend) vs `pipeline/produce.py` (physics solve) | Different outputs for same data |
| Entry point confusion | `pipeline/__main__.py` vs `__main__.py` | Duplicate routing |
| CLI overlap | `solver/solve.py::main()` vs `__main__.py` | Bypasses garage validation and learning steps |

---

## 8. Repo / Runtime Hygiene Issues

### 8.1 Root-Level Clutter

**7 ad-hoc runner scripts** at root (all superseded by `python -m ioptimal`):
- `run_exhaustive.py`, `run_filter.sh`, `run_full_justified.py`, `run_full_pipeline.py`, `run_full_v2.py`, `run_now.py`, `run_tuned_search.py`

**Test artifacts committed to source:**
- `*.sto` files at root: `best.sto`, `cadillac_silverstone.sto`, `idk.sto`, `optimal.sto`, `optimalcaddy.sto`, `optimalnf.sto`, `output.sto`, `reasoned.sto`, `test_phase4.sto`, `output_ferrari_hockenheim.sto`, `test.sto`, `today.sto`, etc.
- `*.txt` debug outputs: `exhaustive_output.txt`, `full_justified_output.txt`, `full_output.txt`, `full_pipeline_output.txt`, `full_pipeline_output_v2.txt`, `stdout.txt`, `stderr.txt`, `run_output.txt`, `tuned_output.txt`, `setup_output.txt`, `_git_result.txt`, `_syntax_result.txt`
- `commit_msg.txt`, `commit_msg2.txt`, `currentjob.md` — git workflow artifacts
- `tmp_bmw_prefix.bin`, `tmp_vrs_prefix.bin` — temporary binary files
- `vertical_dynamics.py` at root — belongs in `physics/` or `utils/`

### 8.2 Multiple Package Managers / Missing pyproject.toml

- `requirements-dev.txt` — Core runtime + test dependencies
- `requirements-desktop.txt` — Superset adding SQLAlchemy, aiosqlite, watchdog, pystray, Pillow
- No `requirements.txt` at root — authoritative requirements file is ambiguous
- No `pyproject.toml` or `setup.py` — cannot `pip install -e .`
- `Dockerfile` copies only `server/` and `teamdb/` — solver pipeline not in Docker image

### 8.3 Unclear Authoritative Runtime

**At minimum 5 ways to run the same IBT→.sto pipeline:**
1. `python -m ioptimal --car bmw --ibt session.ibt` **(correct, authoritative)**
2. `python __main__.py --car bmw --ibt session.ibt` (works because `__main__.py` is at root)
3. `python run_full_pipeline.py` (hardcoded paths — legacy)
4. `python -m pipeline.produce --car bmw --ibt session.ibt` (older entry point)
5. `python -m analyzer --car bmw --ibt session.ibt` (even older)
6. `python -m solver.solve --car bmw --track sebring` (solver-only, no IBT)

**No README makes clear which is canonical.**

### 8.4 .gitignore Gaps

`.gitignore` correctly excludes: `__pycache__/`, `.ibt files`, `ibtfiles/`, `data/Ferraridata/`, `*.ld`

**Should also exclude:**
```gitignore
*.txt              # exclude debug output files
outputs/           # exclude solver output directories
output/
*.sto              # exclude .sto test artifacts (except tests/fixtures/)
tmp_*.bin          # exclude temp binaries
data/aeromaps_parsed/  # generated cache
_git_result.txt
_syntax_result*.txt
commit_msg*.txt
```

### 8.5 CLAUDE.md vs Code Discrepancies

| Claimed in CLAUDE.md | Observed in Code | Severity |
|---------------------|-----------------|----------|
| Multi-car optimizer support as a goal | `optimize_if_supported()` guards on BMW+Sebring only | HIGH |
| Legal manifold search as a solver option | Never activated in `pipeline/produce.py` | MEDIUM |
| Stint analysis is production | Only invoked via `solver/solve.py --stint` | MEDIUM |
| "Front model: R-sq=0.97, LOO RMSE=0.845mm" | `cars.py` default `rear_r_squared: float = 0.0` — only BMW has non-zero value | MEDIUM |

---

## 9. Recommended Module Status Map

### 9.1 Production-Critical (Do Not Break)

- `__main__.py` — Unified CLI router
- `pipeline/produce.py` — Primary pipeline orchestrator
- `pipeline/reason.py` — Multi-session reasoning
- `solver/solve_chain.py` — Core solver orchestration
- `solver/rake_solver.py` — Step 1: ride height
- `solver/heave_solver.py` — Step 2: heave/third spring
- `solver/corner_spring_solver.py` — Step 3: corner springs
- `solver/arb_solver.py` — Step 4: anti-roll bars
- `solver/wheel_geometry_solver.py` — Step 5: camber/toe
- `solver/damper_solver.py` — Step 6: dampers
- `solver/supporting_solver.py` — Diff, brakes, fuel, wings
- `solver/legality_engine.py` — Post-solve legal validation
- `solver/objective.py` — ObjectiveFunction (BMW optimizer path)
- `solver/full_setup_optimizer.py` — BMW Sebring constrained optimizer
- `solver/modifiers.py` — SolverModifiers dataclass
- `solver/predictor.py`, `solver/decision_trace.py` — Output metadata
- `car_model/cars.py` — All car definitions (CRITICAL but BMW-biased)
- `car_model/garage.py` — GarageOutputModel (BMW Sebring only)
- `car_model/setup_registry.py` — Canonical field registry
- `analyzer/extract.py` — Telemetry extraction from IBT
- `analyzer/diagnose.py` — Handling diagnosis
- `analyzer/driver_style.py` — Driver behavior profiling
- `analyzer/segment.py` — Corner segmentation
- `analyzer/sto_binary.py`, `analyzer/sto_reader.py` — .sto file I/O
- `output/setup_writer.py` — .sto XML generation
- `output/garage_validator.py` — Pre-write validation (currently BMW-only)
- `aero_model/interpolator.py`, `aero_model/gradient.py` — Aero map query
- `track_model/ibt_parser.py` — IBT binary parser
- `vertical_dynamics.py` — Shared physics helpers
- `pipeline/report.py` — Output report generation

### 9.2 Supported Secondary (Maintain, Document)

- `comparison/` — Setup comparison (parallel scoring path, document conflict risk)
- `validator/` — Session-vs-setup validation
- `learner/` — Knowledge accumulation (BMW only)
- `solver/learned_corrections.py`, `solver/heave_calibration.py` — BMW calibration store
- `solver/candidate_ranker.py` — Multi-session path scoring
- `solver/setup_fingerprint.py` — Anti-regression mechanism (needs production wiring)
- `solver/bmw_rotation_search.py` — BMW-specific post-solve refinement
- `solver/bmw_coverage.py` — BMW telemetry coverage map
- `solver/scenario_profiles.py` — Scenario weight profiles (needs CLI exposure)
- `pipeline/preset_compare.py` — Preset comparison utility
- `car_model/calibrate_deflections.py` — One-time calibration tool
- `server/` — Team REST API

### 9.3 Experimental (May Contain Useful Ideas, Not Production-Ready)

- `solver/bayesian_optimizer.py` — Gaussian Process optimizer (unwired)
- `solver/legal_search.py`, `solver/legal_space.py` — Manifold search (concept valid, unwired)
- `solver/multi_speed_solver.py` — Multi-speed compromise
- `solver/sector_compromise.py` — Sector compromise
- `solver/stint_model.py`, `solver/stint_reasoner.py` — Stint-aware solver
- `solver/laptime_sensitivity.py` — Sensitivity analysis
- `solver/explorer.py` — Parameter space explorer
- `track_model/` — Track profile builder (mostly analysis-only)
- `webapp/`, `desktop/`, `watcher/` — UI paths (separate concern)

### 9.4 Legacy / Merge / Deprecate Candidates

- `run_full_pipeline.py`, `run_full_v2.py`, `run_full_justified.py`, `run_now.py`, `run_tuned_search.py`, `run_exhaustive.py` — **DELETE.** All superseded by `python -m ioptimal`.
- `analyzer/__main__.py` — **DELETE or redirect** to `__main__.py`.
- `pipeline/__main__.py` — **Merge** into `__main__.py`.
- `solver/solve.py` CLI — **Deprecate** CLI; keep as module for solver-only mode.
- `solver/validation.py`, `solver/uncertainty.py`, `solver/coupling.py`, `solver/corner_strategy.py`, `solver/iterative_solver.py` — **Archive** (confirm intent with maintainer).
- All root-level `*.sto` test files — **Move** to `tests/fixtures/`.
- All root-level `*.txt` debug outputs — **Delete** and add to `.gitignore`.

---

## 10. Fix Plan in Priority Order

### Priority 0 — BLOCKING (Non-BMW cars produce structurally wrong outputs)

**Fix 0.1: Bypass BMW regressions in garage validator for non-BMW cars**
- **Files:** `output/garage_validator.py::validate_and_fix_garage_correlation()`
- **Problem:** When `GarageOutputModel is None` (Ferrari, Acura, etc.), the validator still calls `DeflectionModel()` defaults — which ARE BMW-specific regressions. It then "corrects" solutions using BMW physics, making non-BMW outputs worse.
- **Action:** Add conditional: when `car.garage_output_model is None`, skip correlation corrections entirely. Log a warning in the output so users know deflection validation is bypassed.
- **Effort:** 2–4 hours

**Fix 0.2: Disable BMW DeflectionModel for non-BMW cars**
- **Files:** `car_model/cars.py` — all non-BMW car definitions
- **Action:** Replace `deflection=DeflectionModel()` with explicit per-car instances using zeroed/disabled coefficients and a `is_calibrated=False` flag. Add warning in `_finalize_result()` when outputting .sto for uncalibrated cars.
- **Effort:** 2–4 hours

**Fix 0.3: Ferrari rear spring C constant — correct 3.5× overestimate**
- **File:** `car_model/cars.py::FERRARI_499P::corner_spring`
- **Problem:** `ferrari.json` confirms `rSideSpringRateNpm=105 N/mm` at OD index 1. Code predicts 361 N/mm using `C=0.001282`. `rear_spring_range_nmm=(364.0, 590.0)` is 3.5× too high.
- **Action:** Collect 3+ rear torsion bar OD index points with internal spring rates from `ferrari.json`-style dumps. Either derive the correct C constant or build an index→N/mm lookup table. Interim: disable rear torsion model and use direct index passthrough with documented uncertainty.
- **Effort:** 1 day data collection + 1 day calibration

**Fix 0.4: Ferrari heave spring index → N/mm validation**
- **File:** `car_model/cars.py::FERRARI_499P::heave_spring`
- **Problem:** `front_rate_per_index_nmm=20.0` is unvalidated ESTIMATE. Rear anchor=2, rate=530, per_index=60 → index 8 → 890 N/mm (implausibly stiff).
- **Action:** Run a 9-point garage screenshot sweep (index 0–8 front, 0–9 rear) with iRacing internal JSON to capture actual N/mm values. Build non-linear lookup table if relationship is not linear.
- **Effort:** 1 IBT session + 1 day calibration

**Fix 0.5: Add Ferrari torsion bar turns solver output**
- **Files:** `car_model/cars.py`, `solver/heave_solver.py` or `solver/supporting_solver.py`, `car_model/garage.py`
- **Problem:** Ferrari has `LF/RF/LR/RR Torsion bar turns` (range −0.250 to +0.250) that directly control ride height via preload. Solver never outputs this field → Ferrari ride heights always wrong.
- **Action:** Add `torsion_bar_turns_range = (−0.250, 0.250)` to Ferrari `GarageRanges`. Model turns as a function of heave spring + pushrod combination. Update `RakeSolver.solve()` to output torsion turns for Ferrari alongside pushrod offsets.
- **Effort:** Medium (1–2 days + validation data)

**Fix 0.6: Fix Ferrari front diff negative preload range**
- **File:** `car_model/cars.py::FERRARI_499P` `GarageRanges.diff_preload_nm`
- **Problem:** Code `diff_preload_nm=(0.0, 150.0)` clamps at zero. Ferrari front diff allows −50 to +50 Nm.
- **Action:** Change to `front_diff_preload_nm=(−50.0, 50.0)` and `rear_diff_preload_nm=(0.0, 150.0)`. Update `SupportingSolver` to handle separate front/rear diff models for Ferrari.
- **Effort:** 30 minutes

### Priority 1 — HIGH (Calibration accuracy)

**Fix 1.1: Calibrate m_eff per car from IBT telemetry**
- **Files:** `car_model/cars.py`, `solver/heave_calibration.py`
- **Action:** For each non-BMW car, vary heave spring rate across 3–5 settings in actual iRacing sessions. Extract `shock_vel_p99` and `sigma_mm`. Solve: `m_eff = k_nmm × (sigma_mm / shock_vel_p99)^2`. Update per-car constants. For Acura: account for nonlinear m_eff with rate-dependent lookup table.
- **Effort:** 1–2 sessions per car

**Fix 1.2: Wire failed_validation_clusters in produce.py**
- **Files:** `pipeline/produce.py`, `solver/solve_chain.py`
- **Action:** Load known-bad setup fingerprints from learner knowledge store and pass into `SolveChainInputs.failed_validation_clusters`. Activates the fingerprint veto mechanism that is fully implemented but dead.
- **Effort:** 1–2 hours

**Fix 1.3: Fix telemetry naming drift**
- **Files:** `solver/objective.py`, `solver/diff_solver.py`, `analyzer/extract.py`
- **Action:** Normalize `rear_power_slip_p95` → `rear_power_slip_ratio_p95` and `peak_lat_g_p99` → `peak_lat_g_measured`. Add adapter validation in solve entry to detect missing/renamed fields at startup.
- **Effort:** 1–2 hours

**Fix 1.4: Pass measured_lltd_target to ARBSolver**
- **Files:** `solver/arb_solver.py`, `car_model/cars.py`
- **Action:** When `car.measured_lltd_target` is not None, use it as the primary LLTD target in `ARBSolver.solve()` instead of the theoretical formula. For cars without measured target, keep theoretical formula.
- **Effort:** 1–2 hours

**Fix 1.5: Calibrate Ferrari/Acura damper click sensitivity**
- **Files:** `solver/damper_solver.py`
- **Problem:** Ferrari uses 0–40 click range vs BMW's 0–11. A 1-click change on Ferrari = ~¼ the effect of BMW. Solver must rescale.
- **Action:** Add per-car `damper_click_range` to `CarModel`. Use it in `DamperSolver` click calculations. Also add separate heave damper solve path for Ferrari (`hf*/hr*` internal settings, range 1–20).
- **Effort:** Medium (1–2 days)

**Fix 1.6: Re-run objective calibration**
- **Files:** `validation/objective_calibration.py`
- **Action:** Re-run with updated IBT-calibrated damper zeta targets (updated 2026-03-27, pending validation). Audit per-component correlation to find terms correlating in wrong direction. Test physics-only vs physics+k-NN to quantify k-NN contribution.
- **Effort:** 1–2 days

### Priority 2 — MEDIUM (Generalize solver beyond BMW)

**Fix 2.1: Build per-car DeflectionModel from calibration data**
- **Files:** `car_model/cars.py`, `car_model/calibrate_deflections.py`
- **Action:** Collect 5+ garage screenshots per car with varied setup parameters. Run `car_model/calibrate_deflections.py --car ferrari` to generate Ferrari-specific regression coefficients. Repeat for Acura, Cadillac, Porsche.
- **Effort:** Per-car: 1 day data collection + 1 day calibration

**Fix 2.2: Add per-car GarageOutputModel**
- **Files:** `car_model/cars.py`, `car_model/garage.py`
- **Action:** After building `DeflectionModel` per car (Fix 2.1), create `GarageOutputModel` instances for at minimum Ferrari and Acura. Even without full regression, simple range validators would prevent invalid `.sto` files.
- **Effort:** Medium

**Fix 2.3: Extract GTPConstrainedOptimizer from BMW-only code**
- **File:** `solver/full_setup_optimizer.py`
- **Problem:** `optimize_if_supported()` guards on `car.canonical_name == "bmw"`.
- **Action:** Rename to `GTPConstrainedOptimizer`. Replace BMW-specific seed generation with a per-car calibration dataset loader. The 6-step solver chain inside is already car-agnostic — only seed generation is BMW-specific.
- **Effort:** Large (1–2 weeks including data collection for other cars)

**Fix 2.4: Generalize BMW rotation search to all cars**
- **File:** `solver/bmw_rotation_search.py`
- **Action:** The rotation scoring logic (diff, rear ARB, rear spring vs yaw/lateral telemetry) is physically general. Apply to all cars once rotation characteristics are understood. At minimum, activate for Cadillac (same Dallara platform).

**Fix 2.5: Add per-car LLTD measured targets**
- **Files:** `car_model/cars.py`
- **Action:** Run sessions for Ferrari/Acura/Cadillac with verified setups. Extract LLTD from IBT using same methodology as BMW. Update `measured_lltd_target` per car.
- **Effort:** 1 session per car with good data quality

**Fix 2.6: Unify final selection authority**
- **Files:** `solver/solve_chain.py`, `solver/candidate_search.py`, `solver/legal_search.py`, `solver/grid_search.py`
- **Action:** Choose one canonical arbitration layer (base solve + objective acceptance) and make all optional search paths report/apply through it.
- **Effort:** 1–2 weeks

### Priority 3 — LOW (Housekeeping / CLI)

**Fix 3.1: Delete root-level legacy files**
- `run_full_pipeline.py`, `run_full_v2.py`, `run_full_justified.py`, `run_now.py`, `run_tuned_search.py`, `run_exhaustive.py`
- All `*.txt` debug output files  
- All `*.sto` test artifact files at root (move to `tests/fixtures/`)
- `tmp_*.bin` temporary binary files

**Fix 3.2: Add pyproject.toml and make package installable**
```toml
[project]
name = "ioptimal"
[project.scripts]
ioptimal = "ioptimal.__main__:main"
```
Run `pip install -e .` once; then `ioptimal solve --car bmw --ibt session.ibt --wing 16` works.

**Fix 3.3: Unified CLI with subcommands**
Proposed clean interface:
```bash
ioptimal solve    --car bmw --ibt session.ibt --wing 17          # Single IBT
ioptimal solve    --car bmw --ibt s1.ibt s2.ibt --wing 17        # Multi IBT
ioptimal solve    --car bmw --track sebring --wing 17             # No IBT
ioptimal solve    --car bmw --ibt session.ibt --wing 17 --free    # Legal search
ioptimal analyze  --car bmw --ibt session.ibt                    # Diagnosis only
ioptimal learn    --car bmw --ibt session.ibt                    # Ingest only
ioptimal validate --car bmw --track sebring                      # Run validation
ioptimal serve                                                    # Web UI
```

**Fix 3.4: Update .gitignore** (see Section 8.4)

**Fix 3.5: Move `vertical_dynamics.py` to `physics/vertical_dynamics.py`**
- Update all imports in `solver/`, `car_model/`, `analyzer/`

**Fix 3.6: Standardize CLI flags**
- Normalize `--json` behavior across all CLIs
- Remove/rename ambiguous `--objective-profile`
- Expose `--scenario` flag for `single_lap_safe/quali/sprint/race`
- Expose `--search-mode` and `--free` in main `python -m ioptimal` CLI

---

## 11. Appendices

### A. Key File / Function Reference

| Topic | File | Function |
|-------|------|----------|
| Production entry point | `__main__.py` | `main()` |
| Full pipeline orchestrator | `pipeline/produce.py` | `produce_result(args)` |
| Multi-session reasoning | `pipeline/reason.py` | `reason_and_solve()` |
| Solver chain orchestration | `solver/solve_chain.py` | `run_base_solve(inputs)` |
| Sequential solver | `solver/solve_chain.py` | `_run_sequential_solver(inputs)` |
| Constrained optimizer (BMW only) | `solver/full_setup_optimizer.py` | `BMWSebringOptimizer._run()` |
| Optimizer gate | `solver/full_setup_optimizer.py` | `optimize_if_supported()` |
| Objective/scoring function | `solver/objective.py` | `ObjectiveFunction.evaluate(candidate)` |
| Scenario profiles + weights | `solver/scenario_profiles.py` | `ScenarioProfile` |
| Step 1: Rake/RH | `solver/rake_solver.py` | `RakeSolver.solve()` |
| Step 2: Heave spring | `solver/heave_solver.py` | `HeaveSolver.solve()` |
| Step 3: Corner spring | `solver/corner_spring_solver.py` | `CornerSpringSolver.solve()` |
| Step 4: ARB | `solver/arb_solver.py` | `ARBSolver.solve()` |
| Step 5: Wheel geometry | `solver/wheel_geometry_solver.py` | `WheelGeometrySolver.solve()` |
| Step 6: Dampers | `solver/damper_solver.py` | `DamperSolver.solve()` |
| Supporting params (diff, brakes) | `solver/supporting_solver.py` | `SupportingSolver.solve()` |
| BMW post-solve rotation refinement | `solver/bmw_rotation_search.py` | `search_rotation_controls()` |
| Legality check | `solver/legality_engine.py` | `validate_solution_legality()` |
| Garage validator | `output/garage_validator.py` | `validate_and_fix_garage_correlation()` |
| .sto binary write | `output/setup_writer.py` | `write_sto()` |
| Predicted telemetry output | `solver/predictor.py` | `predict_candidate_telemetry()` |
| Decision trace / explanation | `solver/decision_trace.py` | `build_parameter_decisions()` |
| Fingerprint veto | `solver/setup_fingerprint.py` | `hash_setup_fingerprint()` |
| Telemetry extraction | `analyzer/extract.py` | `extract_measurements(ibt_path)` |
| Diagnosis engine | `analyzer/diagnose.py` | `diagnose(measured, car, track)` |
| Driver profiling | `analyzer/driver_style.py` | `analyze_driver(measured, corners)` |
| All car definitions | `car_model/cars.py` | `get_car(name)` |
| Ferrari definition | `car_model/cars.py:1686` | `FERRARI_499P` |
| Acura definition | `car_model/cars.py:1971` | `ACURA_ARX06` |
| BMW definition | `car_model/cars.py:~1400` | `BMW_M_HYBRID_V8` |
| DeflectionModel | `car_model/cars.py:345` | `DeflectionModel` — BMW defaults |
| RideHeightModel | `car_model/cars.py` | `RideHeightModel` — BMW calibrated |
| GarageOutputModel | `car_model/garage.py:~88` | `GarageOutputModel` — BMW only |
| Session database (k-NN) | `solver/session_database.py` | BMW/Sebring only |
| Calibration script | `car_model/calibrate_deflections.py` | `(run as script)` |
| Aero map query | `aero_model/interpolator.py` | `AeroSurface.query()` |
| Physics helpers | `vertical_dynamics.py` | `damped_excursion_mm()` |
| Alternative scoring | `comparison/score.py` | `score_sessions()` — parallel system |
| Ferrari garage ground truth | `ferrari.json` | (full file) |

### B. Production Pseudocode Summary

```python
# Effective production solve (single IBT session)
measured = extract_measurements(ibt)
profile = build_profile(track)
diagnosis, driver = diagnose_and_profile(measured)
mods = compute_modifiers(diagnosis, driver, measured)

base = run_base_solve(inputs_with(mods))
# inside run_base_solve:
#   if BMW+Sebring:
#       try SLSQP optimizer over 6 continuous params
#       then BMW rotation search post-solve (can override ARB/diff/spring)
#   else:
#       sequential 6-step solver

result = finalize_legality_prediction_trace(base)
# NOTE: legality check is post-facto — solver can produce illegal setups

# Optional:
if candidate_families_enabled:
    result = select_candidate_family(result)  # 3rd independent scorer

if legal_or_grid_search_enabled:  # Only via --free or --search-mode flags
    result = rematerialize_best_search_candidate(result)

validate_garage(result)  # ⚠️ Uses BMW regressions on ALL cars
export_report_json_sto(result)
```

### C. Ferrari 499P Garage Schema — Verified Ground Truth from `ferrari.json`

This appendix documents what `ferrari.json` (complete iRacing setup parameter dump) proves about the Ferrari 499P that the solver must handle:

**Spring Controls (INDEXED, not continuous):**

| Control | ferrari.json Value | Code Assumption | Mismatch |
|---------|-------------------|-----------------|---------|
| Front Heave spring | Index `5` (internal: 115.17 N/mm at index 5) | `front_rate_per_index_nmm=20.0` ESTIMATE → index 5 = 130 N/mm | **Critical** |
| Rear Heave spring | Index `8` (physics unknown) | `rear_rate_per_index_nmm=60.0` ESTIMATE → index 8 = 890 N/mm (implausibly stiff) | **Critical** |
| Front Torsion bar OD | Index `2` (internal: `fSideSpringRateNpm=115170 N/m = 115.17 N/mm`) | `C=0.001282, OD range 20-24mm` → index 2 ≈ 20.22mm ✅ | Consistent |
| Rear Torsion bar OD | Index `1` (internal: `rSideSpringRateNpm=105000 N/m = 105.0 N/mm`) | `C=0.001282, OD range 23.1-26mm` → 361 N/mm at OD=23.1mm | **CRITICAL: 3.5× wrong** |

**Torsion Bar Turns — Entirely Missing from Solver:**

| Control | Range | Impact |
|---------|-------|--------|
| LF/RF Torsion bar turns | −0.250 to +0.250 Turns | Front ride height depends on this |
| LR/RR Torsion bar turns | −0.250 to +0.250 Turns | Rear ride height depends on this |
| **Solver output** | Not generated | Generated Ferrari .sto ride heights are wrong |

**Damper Architecture — Two Separate Subsystems:**

| Subsystem | Visibility | Controls | Range | Code Handling |
|-----------|-----------|---------|-------|---------------|
| Per-corner dampers | Visible in garage | LF/RF/LR/RR LS comp, HS comp, LS rbd, HS rbd, HS comp slope | 0–40 clicks (slope: 0–11) | `DamperSolver` writes here only |
| Heave dampers | **Hidden from standard garage UI** | `hf/hr LS/HS comp/rbd` | 1–20 range | **Not handled at all** |

**HS Rebound slope** (`lfHSSlopeRbdDampSetting`, etc.) also exists — solver models `hs_slope` for compression only.

**Other Ferrari-Unique Controls Not Modeled:**

| Control | ferrari.json Value | Code Status |
|---------|-------------------|-------------|
| Front Diff Preload | −50 to +50 Nm | Code clamps at 0 — **WRONG** |
| Brake bias migration type | 1–6 selector | Not modeled |
| Brake bias migration gain | −4% to +4% | Not modeled |
| Front/Rear master cylinder | 16.8–20.6mm options | Not modeled |
| Gear stack | Short/Tall selector | Not modeled |
| Camber | `is_derived: true` | Solver outputs as settable — **WRONG** |
| Diff ramp angles | String labels ("More Locking") | Code expects numeric pairs |
| Packer thicknesses | All zeros (adjustable) | Not modeled |

### D. Specific Broken Things by Car

**Ferrari 499P (Priority Order):**
1. `DeflectionModel()` — BMW coefficients used → deflection fields are wrong
2. `rear_spring_range_nmm=(364.0, 590.0)` — 3.5× too high (actual: ~105 N/mm at min)
3. Torsion bar turns not output → ride heights always wrong
4. `DamperSolver` writes to corner dampers only; heave dampers (`hf*/hr*`) not addressed
5. Front diff preload range is `(0.0, 150.0)` — negative values valid per garage schema
6. HS rebound slope unmapped
7. Camber modeled as independently settable — it is derived
8. ARB stiffnesses all ESTIMATE — affect LLTD accuracy
9. `heave_spring.front/rear_rate_per_index_nmm` — unvalidated linear estimates

**Acura ARX-06 (Priority Order):**
1. `DeflectionModel` — all-zero coefficients → no deflection model at all
2. `has_roll_dampers=True` — `DamperSolver` may not handle split heave+roll damper architecture
3. Front heave damper "always bottomed" per code — solver doesn't know this; may try to optimize a fixed parameter
4. `m_eff` is rate-dependent (319–641 kg range) but solver uses `front_m_eff_kg=450.0` constant
5. `front_torsion_c=0.0008036` — ESTIMATE marked "same as BMW until calibrated" — Acura is ORECA, not Dallara
6. Aero compression front/rear both ESTIMATE — no aero map calibration
7. Rear uses torsion bars (`rear_is_torsion_bar=True`) — code paths must use `rear_torsion_bar_rate()` not `snap_rear_rate()`

**Porsche 963:**
1. Zero IBT sessions collected — everything is estimate or BMW placeholder
2. No `GarageOutputModel`, no `DeflectionModel` calibration
3. All `ESTIMATE` tags throughout car definition in `car_model/cars.py`

### E. Critical Code Snippets

**The optimizer gate — single point that determines if optimization happens:**
```python
# solver/full_setup_optimizer.py::optimize_if_supported()
def optimize_if_supported(car, surface, track, ...) -> OptimizedResult | None:
    if car.canonical_name == "bmw":          # ← ONLY BMW
        optimizer = BMWSebringOptimizer(...)
        return optimizer.run()
    return None                              # ← All other cars: no optimization
```

**Why non-BMW deflection models fail:**
```python
# Ferrari silently inherits BMW defaults:
FERRARI_499P = CarModel(..., deflection=DeflectionModel())  # BMW coefficients!

# When garage_validator calls:
defl = car.deflection.heave_spring_defl_static(heave_nmm)  
# Returns: -20.756 + 7.030/heave_nmm + ...  ← BMW regression on Ferrari physics
# Result: garbage; validator then "corrects" the setup based on this garbage
```

**The LLTD conflict:**
```python
# ARB solver — targets theoretical LLTD for ALL cars
lltd_target = car.weight_dist_front + (car.tyre_load_sensitivity / 0.20) * 0.05
# BMW: 0.4727 + (0.22/0.20)*0.05 = 0.528

# ObjectiveFunction — scores against MEASURED LLTD (BMW only)
lltd_target = car.measured_lltd_target or (car.weight_dist_front + ...)
# BMW: 0.41 (calibrated from 46 sessions)  ← 0.528 vs 0.41 = systematic tension
```

**✅ Fingerprint veto now wired (P1.5 fixed):**
```python
# learner/observation.py — Observation now has veto fields:
validation_failed: bool = False  # Mark True to add hard-veto fingerprint
setup_fingerprint: str = ""      # Hash populated at ingest time

# pipeline/produce.py — now populated from knowledge store:
failed_clusters = []
for obs_data in ks.list_observations(car=..., track=...):
    if obs_data.get("validation_failed", False):
        fp = obs_data.get("setup_fingerprint", "")
        if fp:
            failed_clusters.append(ValidationCluster(fingerprint=fp, veto_type="hard"))

inputs = SolveChainInputs(
    ...
    failed_validation_clusters=failed_clusters,  # ← Now populated!
)
```
**To activate:** set `observation.validation_failed = True` and `observation.setup_fingerprint = hash_setup_fingerprint(...)` on any session you want to blacklist.

### F. Open Questions / Uncertainties

1. **Whether orphan solver modules are intentionally reserved** for upcoming work — cannot confirm from static wiring alone.
2. **Whether `ferrari.json` data represents the actual current-version iRacing Ferrari** — if the game updated the setup parameters, the mapping tables would need refresh.
3. **BMW `measured_lltd_target=0.41` source validation** — cites "46 BMW Sebring sessions, 2026" and "objective_validation.md Section 6" but `validation/objective_validation.md` is not present. Round-trip validation (solver → simulation → telemetry) not confirmed.
4. **Is the 73-observation BMW dataset diverse enough?** Most sessions may use similar setups → k-NN and empirical corrections biased toward local optimum.
5. **Whether torsion-ARB coupling (gamma=0.25) is real** — single BMW data point. Physical mechanism plausible but could be noise.
6. **Cadillac rear compression 18.5mm vs BMW 9.5mm** — nearly 2× difference, "calibrated from 2 sessions" — is this stable with more data?
7. **Ferrari front diff negative preload physics** — what effect does negative preload have on GTP handling? The solver would need a physical model for this range.

---

*Merged from: `deep_audit_report_claw_research.md`, `AUDIT_REPORT.md`, `docs/codebase_audit_2026-03-31.md`*  
*For user-facing CLI reference, see `CLI_GUIDE.md`*  
*Total source files analyzed across all audits: ~120 Python files, 15 modules, ferrari.json garage schema*
