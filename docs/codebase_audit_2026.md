# iOptimal GTP Setup Solver — Deep Codebase Audit
**Branch:** `claw-research` | **Audit date:** 2026-03-31  
**Auditor:** Cursor Cloud Agent (claude-4.6-sonnet-high-thinking)

---

## 1. Executive Summary

The codebase is a well-structured, physics-first GTP/LMDh setup solver with a working end-to-end pipeline for the BMW M Hybrid V8 at Sebring. The fundamental architecture is sound and the engineering quality is above average for a research codebase. However, three systemic problems explain the bulk of user pain:

**Critical systemic issues:**

1. **Objective function has near-zero predictive power.** The BMW/Sebring non-vetoed Spearman between solver score and lap time is **−0.18** (−0.06 Pearson), with holdout worst-case of **+0.25** (wrong direction). The objective function is effectively uncalibrated for ranking purposes. "Best" as chosen by the system is statistically indistinguishable from random at this data volume. Weights were searched but not applied to runtime because validation is not stable enough — which is the correct engineering call but means the scoring engine is in an acknowledged broken state.

2. **Per-car calibration is asymmetric to the point of being non-functional for non-BMW cars.** Ferrari, Acura, and Porsche all have major `ESTIMATE` flags on the physics parameters that the solver depends on: `m_eff_kg` (heave excursion), `torsion_c` (spring rate), `ls_force_per_click_n`/`hs_force_per_click_n` (damper force), ARB stiffness, and aero compression. The solver computes physically plausible numbers but they are uncalibrated to the actual iRacing simulation, so the output drifts far from what actually works in-game.

3. **The ride height and spring deflection model is fragmented and inconsistently sourced.** The BMW front RH has a flat pushrod sensitivity (r=0, no measured sensitivity in the −22.5 to −28mm range), but the solver uses a `RideHeightModel` regression (R²=0.15, LOO-RMSE=0.16mm) that is essentially a constant. The Acura front RH is camber-dominated (2.39mm/deg) but the solver's `PushrodGeometry` cannot represent this. The Ferrari heave spring is an indexed integer in-game but the solver works in physical N/mm with estimated index-to-rate mappings that may be wrong by a step or two. These mismatches propagate into the legal search and scoring.

**Most important next fixes (in order):**
1. Calibrate Ferrari and Acura damper force-per-click constants from IBT shock velocity vs damper click sweeps.
2. Calibrate Ferrari `m_eff_kg` from IBT heave deflection vs spring rate.
3. Fix Acura front RH model to use camber as the primary control variable (not pushrod).
4. Grow BMW/Sebring dataset to ≥150 non-vetoed observations to achieve statistically significant objective correlation.
5. Suppress "optimal" language in non-BMW outputs; add explicit "ESTIMATE" banners.

---

## 2. Actual Production Path

### 2.1 Primary Production Path
```
CLI: python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --sto output.sto
Entry: pipeline/produce.py::produce()
  ├─ IBTFile(ibt_path)                           [track_model/ibt_parser.py]
  ├─ build_profile(ibt_path)                     [track_model/build_profile.py]
  ├─ extract_measurements(ibt_path, car, ...)    [analyzer/extract.py]
  ├─ segment_lap(ibt, start, end, car)           [analyzer/segment.py]
  ├─ analyze_driver(ibt, corners, car)           [analyzer/driver_style.py]
  ├─ compute_adaptive_thresholds(track, car, driver) [analyzer/adaptive_thresholds.py]
  ├─ diagnose(measured, current_setup, car)      [analyzer/diagnose.py]
  ├─ build_session_context(measured, setup, diag) [analyzer/context.py]
  ├─ compute_gradients(surface, car, ...)        [aero_model/gradient.py]
  ├─ compute_modifiers(diagnosis, driver, measured) [solver/modifiers.py]
  ├─ run_base_solve(SolveChainInputs)            [solver/solve_chain.py]
  │    ├─ optimize_if_supported(...)             [solver/full_setup_optimizer.py]
  │    │    └─ BMWSebringOptimizer (BMW+Sebring only; sequential fallback otherwise)
  │    └─ _run_sequential_solver(inputs)
  │         Steps 1–6 + RH reconciliation + damper refinement pass
  ├─ generate_candidate_families(...)            [solver/candidate_search.py]
  ├─ validate_solution_legality(...)             [solver/legality_engine.py]
  ├─ build_parameter_decisions(...)              [solver/decision_trace.py]
  ├─ write_sto(...)                              [output/setup_writer.py]
  └─ ingest_ibt(...)                             [learner/ingest.py] (auto-learn)
```

### 2.2 Supported Secondary Paths

| Path | Entry | Activation |
|------|-------|-----------|
| Track-only (no IBT) | `solver/solve.py::run_solver()` | `python -m solver.solve --car X --track Y --wing Z` |
| Multi-IBT reasoning | `pipeline/reason.py::reason_and_solve()` | `--ibt file1.ibt file2.ibt ...` |
| Grid search | `solver/grid_search.py::GridSearchEngine` | `--search-mode quick/standard/exhaustive/maximum` |
| Random legal search | `solver/legal_search.py::run_legal_search()` | `--explore-legal-space` or `--free` |
| Stint solve | `solver/stint_reasoner.py::solve_stint_compromise()` | `--stint` |
| Comparison (multi-IBT) | `comparison/compare.py` | `pipeline/preset_compare.py` |

### 2.3 Experimental / Analysis-Only Paths

| Module | Entry | Status |
|--------|-------|--------|
| `solver/bayesian_optimizer.py` | `--bayesian` | Explicitly EXPERIMENTAL; labeled in CLI help |
| `solver/multi_speed_solver.py` | `--multi-speed` | Explicitly EXPERIMENTAL; labeled in CLI help |
| `solver/explorer.py` | `--explore` (solve.py only) | Explicitly EXPERIMENTAL; labeled in CLI help |
| `solver/iterative_solver.py` | Not wired | **Dead code** — no caller outside itself |
| `solver/corner_strategy.py` | Not wired | **Dead code** — no caller outside itself |
| `solver/coupling.py` | Not wired | **Dead code** — no caller except itself |
| `solver/setup_space.py` | `--space` (solve.py only) | Advisory only; results discarded |
| `validation/objective_calibration.py` | Manual CLI | Calibration tooling; not runtime |

### 2.4 Legacy / Overlap-Heavy Paths

- `validator/` module: An older parallel validation pipeline (`validator/extract.py`, `validator/compare.py`, `validator/classify.py`, `validator/recommend.py`, `validator/report.py`). This duplicates logic now handled by `analyzer/`. The `validator/__main__.py` still works as a standalone CLI but is not invoked by any modern pipeline path. It reads a solver JSON alongside an IBT, which is a different workflow than the current pipeline.

- `run_full_pipeline.py`, `run_full_v2.py`, `run_full_justified.py`, `run_now.py`, `run_tuned_search.py`, `run_exhaustive.py`: All Windows-path hardcoded wrappers (`C:\Users\VYRAL\...`). **Dev-machine artifacts** committed to the repo. Not portable.

- `scripts/generate_repo_audit.py`: Generates a JSON inventory — not runtime.

- `research/ferrari_calibration_mar21.py`, `research/ferrari_calibration_write_docs.py`: Research scripts; not wired to runtime.

---

## 3. How the Solver Works

### 3.1 Orchestration Path (Single-IBT, Primary)

`pipeline/produce.py::produce()` constructs a `SolveChainInputs` object and calls `run_base_solve()`. The actual solver logic lives in `solver/solve_chain.py`.

### 3.2 BMW/Sebring Optimizer vs Sequential Fallback

`solver/solve_chain.py::run_base_solve()` begins with:

```python
optimized = optimize_if_supported(car, surface, track, ...)
```

`solver/full_setup_optimizer.py::optimize_if_supported()` checks `_is_bmw_sebring(car, track)`. If true, it runs `BMWSebringOptimizer`; otherwise it returns `None` and the sequential solver runs.

**`BMWSebringOptimizer`** (`solver/full_setup_optimizer.py`):
- Loads a fixed seed dataset from `data/calibration_dataset.json` (`@lru_cache(maxsize=1)`)
- Iterates over each seed; calls the full 6-step sequential solver with that seed's spring/perch values
- Scores each candidate via a SciPy-based internal objective (`minimize` is used for RH matching; scoring is penalty-sum)
- Selects `best_clean` (no veto) over `best_any`; falls back to sequential if all candidates vetoed
- **Important:** The "optimizer" is really a seeded grid search over ~72 observed setups, not a general-purpose continuous optimizer. It fits only because the BMW/Sebring calibration dataset exists.

**Sequential solver** (`solver/solve_chain.py::_run_sequential_solver()`):
- Step 1: `RakeSolver` → target DF balance at operating ride height
- Step 2: `HeaveSolver` → minimum heave spring for bottoming constraint
- Step 3: `CornerSpringSolver` → corner spring + torsion OD
- RH reconciliation pass (heave_solver.reconcile_solution + reconcile_ride_heights)
- Provisional Step 6: `DamperSolver` (provisional) → feeds back into Step 2 re-solve
- Step 2 re-solve with HS damper work term
- Step 3 re-solve
- Second RH reconciliation
- Step 4: `ARBSolver` → LLTD targeting
- Step 5: `WheelGeometrySolver` → camber + toe
- Third RH reconciliation (with camber)
- Step 6 (final): `DamperSolver`

### 3.3 Post-Solve Layers

After the base solve:
1. `SupportingSolver` computes brake bias, diff, TC, tyre pressures
2. `search_rotation_controls()` searches BMW-specific rotation control adjustments
3. `_finalize_result()` runs: legality validation, decision trace, telemetry prediction
4. `generate_candidate_families()` creates alternative candidates (conservative/aggressive/balanced variants)
5. Legal-manifold search (if `--free`/`--explore-legal-space`/`--search-mode`)
6. Best candidate selected and applied to final output

### 3.4 Fallback Behavior

- If `optimize_if_supported` fails → sequential solver
- If sequential solver matches a failed validation cluster → optimizer candidate used instead
- If legal search finds no accepted candidate → base solve result retained
- All exceptions in optional analysis phases (stint, sector, sensitivity) are silently caught and result is `None`

### 3.5 Legality / Veto Flow

`solver/legality_engine.py::validate_solution_legality()` calls `output/garage_validator.py::validate_and_fix_garage_correlation()` which checks:
- Heave spring deflection within `(0.6, 25.0)` mm
- Heave slider deflection within `(25.0, 45.0)` mm
- Shock deflection within per-car legal ranges
- Torsion bar OD within legal options
- Camber within allowed range

Vetoes are stored as `hard_veto_reasons`. In the BMW optimizer, a veto penalty of `1e6` or `5e4` is added to the candidate score (not excluded). This means a hard-vetoed candidate can still win if all others are worse — **the veto adds a penalty but does not guarantee exclusion**.

In the objective function (`solver/objective.py`), hard vetoes are set via `veto_reasons` accumulation. A candidate with `hard_vetoed=True` gets `score = -1e9` which effectively excludes it.

**Risk:** The legality logic in the optimizer and in the objective function use different mechanisms (penalty vs score floor). In the grid search path, candidates marked `hard_vetoed` are filtered by `if not _gs_best.hard_vetoed` before application, but the BMW optimizer path does not filter — it just adds a large penalty.

---

## 4. How "Best" Is Chosen

There are **three distinct scoring systems** active simultaneously. They can produce different rankings and do not share a common anchor.

### 4.1 `solver/objective.py::ObjectiveFunction.evaluate()` — Grid Search / Legal Search Scoring

**Canonical path for `--search-mode` and `--explore-legal-space`.**

Score formula:
```
total = w_lap_gain * lap_gain_ms
      - w_platform * platform_risk_ms
      - w_driver   * driver_mismatch_ms
      - w_uncertainty * telemetry_uncertainty_ms
      - w_envelope * envelope_penalty_ms
      - w_staleness * staleness_penalty_ms
      - w_empirical * empirical_penalty_ms
```

All terms are in milliseconds. Weights come from `solver/scenario_profiles.py::ScenarioProfile.objective`.

**Problem:** The objective score has Spearman r = −0.18 with lap time across 98 non-vetoed BMW/Sebring sessions (validation/objective_validation.json). This is statistically close to zero. The calibration tooling (`validation/objective_calibration.py`) exists but produces weights that are not auto-applied at runtime because holdout stability is insufficient. The weights in `scenario_profiles.py` are manually-set best-guesses, not the calibration-searched values.

**Key sub-components:**
- `_estimate_lap_gain()` — penalties for LLTD error, damping ratios, DF balance, camber, diff, ARB, TC. These are hand-tuned penalty rates (e.g., `LLTD_MS_PER_PCT = 2.5`, cap 10ms). Most terms have low individual correlation with lap time.
- `_compute_platform_risk()` — bottoming margin, vortex margin, RH variance, fuel-window LLTD drift. These use real physics via `vertical_dynamics.py::damped_excursion_mm()`.
- `k-NN empirical penalty` — `solver/session_database.py::SessionDatabase.predict()` — when ≥3 sessions exist; predicts telemetry outcomes and compares to target ranges. BMW/Sebring-specific.

**Interacts with vetoes:** Hard veto → score = −1e9.

### 4.2 `solver/candidate_ranker.py::score_from_prediction()` — Candidate Family Scoring

**Used by `generate_candidate_families()` in `solver/candidate_search.py`.**

Different scoring formula from ObjectiveFunction:
```
total = safety * 0.25 + performance * 0.25 + stability * 0.15 + confidence * 0.10 + (1 - disruption_cost) * 0.25
```

This is a 0–1 normalized score across 5 dimensions. It is computed from `PredictedTelemetry` (what the solver predicts the candidate will produce). This is a separate, narrower scoring layer than ObjectiveFunction.

**Can conflict with ObjectiveFunction:** A candidate ranked #1 by ObjectiveFunction may rank differently by `score_from_prediction`, and vice versa. The pipeline applies them in series (grid search wins if used; candidate families are used otherwise).

### 4.3 `solver/full_setup_optimizer.py::BMWSebringOptimizer` — Internal Seed Scoring

**BMW/Sebring optimizer internal only.** Each seed candidate's `score` is a sum of physics constraint penalties (not in milliseconds). This is a third distinct metric. The seed with minimum penalty is selected as `best_clean`. This score is never exposed in the output JSON as the primary candidate score.

### 4.4 `comparison/score.py` — Multi-Session Comparison Scoring

**Used by the `comparison/` module (multi-IBT compare mode).** Distinct scoring system with `CATEGORY_WEIGHTS` totalling 1.0 across 10 categories (lap_time, grip, balance, aero efficiency, corner performance, etc.). Produces a 0–1 `overall_score` per session. Not connected to the solver's objective function.

### 4.5 Summary: "Best" Selection Flowchart

```
produce() called
  → run_base_solve() → SolveChainResult (base)
  → generate_candidate_families() → score via score_from_prediction()
      → "selected" candidate applied if selectable
  → [if --search-mode] GridSearchEngine → ObjectiveFunction.evaluate_batch()
      → best_robust = top robust candidate
      → best_overall = top any candidate
      → applied if not hard_vetoed
  → [if --free / --explore-legal-space] run_legal_search()
      → ObjectiveFunction.evaluate() per candidate
      → accepted_best = best candidate passing sanity + legality
      → applied if accepted_best is not None
```

The base solve result is overwritten by each layer if that layer produces a valid result. **The final output can be the base solve, a candidate family variant, a grid search result, or a legal search result** — whichever was last to run and succeeded.

---

## 5. Accuracy / Reliability Risks

### 5.1 Scoring / Model-Calibration Risks

- **Objective correlation near zero.** Spearman −0.18 on BMW/Sebring means the objective is not reliably ranking candidates by lap time. Any setup the system labels "optimal" has only marginally better-than-random probability of being faster.
- **Zeta targets are BMW/Sebring-only.** `objective.py` hardcodes `zeta_ls_front=0.68`, `zeta_ls_rear=0.23`, etc. These are IBT-calibrated from BMW Sebring top-15 laps. For Ferrari/Acura these targets are wrong and the penalty direction may be inverted.
- **TORSION_ARB_COUPLING = 0.25** is single-point calibrated from one BMW LLTD observation. It is physically speculative and applied universally. For other cars, this term is uncalibrated and can distort LLTD scoring.
- **Damper force-per-click constants** are ESTIMATES for Ferrari (`ls_force_per_click_n=7.0`, `hs_force_per_click_n=30.0`), Acura (`ls=18.0`, `hs=80.0`), and Porsche (`ls=18.0`, `hs=80.0`). The damper solver computes click positions from these constants — wrong constants produce wrong clicks that are internally consistent but wrong for the actual car.

### 5.2 Path Fragmentation Risks

- **Three scoring paths can produce different "best."** The user gets whichever last ran. There is no single ranked list visible across all paths.
- **Produce.py runs both the sequential solver AND solve_chain in sequence** (lines 597–895 then 869–899). The steps are computed twice independently — first in the produce.py explicit loop (lines 628–866), then in `run_base_solve()` which re-runs everything from scratch. The second run's results overwrite the first. This is wasteful and may produce different results if modifiers have side effects.
  - **Confirmed bug:** `produce.py` runs Steps 1–6 explicitly (the `optimized`/`else` block), then calls `run_base_solve(solve_inputs)` which re-runs the entire solver again. The first pass results are discarded and the second pass (via `run_base_solve`) is what gets used. The first pass is pure overhead.
- **Legal-search sanity checks** (`solver/scenario_profiles.py::prediction_passes_sanity()`) use predicted telemetry from `solver/predictor.py::predict_candidate_telemetry()` which relies heavily on fallback defaults when telemetry signals are missing. Sanity boundaries can be passed by candidates that are actually problematic.

### 5.3 Telemetry Underuse Risks

- **Aero compression** from IBT (`LFrideHeight` mean at speed minus pit static) is computed and stored in `measured.aero_compression_front_mm` but is explicitly **not fed back into the solver** (`produce.py` line 315–321 comment: "applied here produces inflated static RH recommendations"). The solver uses fixed `car.aero_compression` constants instead. The measurement is effectively unused for aero calibration.
- **High-speed m_eff filtering** (`front_rh_std_hs_mm`, `front_heave_vel_p95_hs_mps`) is extracted for >200kph regime but the solver's m_eff correction still uses lap-wide stats. This was documented as a known limitation.
- **Tyre temperatures** are extracted (`LFtempM`, `RFtempM`, etc.) and scored in `ObjectiveFunction._compute_lap_gain_breakdown()` as `carcass_ms`, but the actual temperature channels are frequently unavailable in IBT and the scoring term silently returns 0.0.
- **Roll gradient** from telemetry (`roll_gradient_measured_deg_per_g`) feeds the LLTD measurement proxy but the LLTD proxy is acknowledged as "not true LLTD" — it correlates with roll stiffness distribution, not actual LLTD.

### 5.4 Validation Gaps

- BMW/Sebring has 98 non-vetoed samples but Spearman is still −0.18 — more data alone will not fix this if scoring signals are wrong.
- Cadillac (4 samples), Ferrari (12 samples), Porsche (2 samples), Acura (0 samples in validation) — none have enough data for any meaningful correlation check.
- Holdout worst-case Spearman for BMW = +0.25 (wrong direction). One 10-fold holdout split predicts faster setups are actually slower. This indicates the objective has systematic error in some regions of the parameter space.

### 5.5 Support Asymmetry

| Car | Heave m_eff | Damper F/click | Torsion C | ARB stiffness | m_eff source |
|-----|-------------|---------------|-----------|---------------|--------------|
| BMW | Calibrated (learner) | Semi-calibrated | Calibrated | Calibrated | IBT telemetry |
| Ferrari | ESTIMATE | ESTIMATE | Calibrated (4-pt) | ESTIMATE | None |
| Acura | Mid-range (2-pt) | ESTIMATE | ESTIMATE (BMW borrowed) | ESTIMATE | 2 garage points |
| Cadillac | ESTIMATE | Inherited BMW | ESTIMATE | ESTIMATE | Learner (compression only) |
| Porsche | ESTIMATE | ESTIMATE | ESTIMATE | ESTIMATE | None |

For Ferrari and Acura, the solver is running valid physics equations on estimated parameters. The output will be physically internally consistent but may be systematically wrong in absolute value.

---

## 6. Telemetry Channel Audit

| Channel / Derived Metric | Where Read | Where Analyzed | Solver Impact | Classification | Notes |
|--------------------------|-----------|----------------|--------------|----------------|-------|
| `LFrideHeight`, `RFrideHeight`, `LRrideHeight`, `RRrideHeight` | `analyzer/extract.py` | Ride height mean, std, p01, aero compression | Modifies heave floor constraints via modifiers; aero gradient uses measured RH | **solve-critical** | IBT RH ≠ aero map RH frame; compression is computed but NOT fed to solver |
| `LFshockVel`, `RFshockVel`, `LRshockVel`, `RRshockVel` | `analyzer/extract.py` | p95/p99, settle time, oscillation freq | Damper solver uses p95/p99 from `track.shock_vel_*` (built from IBT in build_profile); measured values used for modifier scaling | **solve-critical** | Corner shock channels used when present; heave+roll synthesis for Acura |
| `HFshockVel`, `TRshockVel`, `FROLLshockVel`, `RROLLshockVel` | `analyzer/extract.py` | Synthesized corner shocks | Acura-specific synthesis path | solve-critical (Acura) | Synthesis adds heave±roll to approximate corner shocks |
| `HFshockDefl`, `HRshockDefl` | `analyzer/extract.py` | Heave deflection p99, mean, std, travel% | Modifier: bottoming diagnosis → heave floor constraint | **solve-critical** | Travel % vs DeflMax drives heave floor modifier |
| `Lat Accel` / `LatAccel` | `analyzer/extract.py` | Understeer, body slip, LLTD proxy, peak lat g | LLTD offset modifier; understeer diagnosis → modifier adjustments | **solve-critical** | Used for corner segmentation and LLTD proxy |
| `Yaw Rate` | `analyzer/extract.py` | Body slip, understeer angle | Balance modifiers | diagnostic-only | Indirect via body_slip_p95_deg |
| `Brake` | `analyzer/extract.py` | At-speed mask, trail brake depth | Driver style → damper modifier | context-only | Used as filter, not direct solver input |
| `Throttle` / `ThrottleRaw` | `analyzer/extract.py` | Throttle progressiveness | Diff ramp modifier | context-only | |
| `LFtempM`, `RFtempM`, etc. | `analyzer/extract.py` | Tyre carcass mean temperature | Carcass penalty in objective | diagnostic-only | Frequently unavailable; silently falls back to 0 penalty |
| `LFpressure`, `RFpressure`, etc. | `analyzer/extract.py` | Hot tyre pressure | Supporting solver: tyre cold target from hot measurement | solve-critical | Used to back-calculate cold pressure target |
| `Speed` | `analyzer/extract.py` | At-speed filtering, speed bands | Track profile, corner speed classification | context-only | |
| `VelocityX`, `VelocityY` | `analyzer/extract.py` | Body slip angle | Balance diagnosis | diagnostic-only | |
| `FuelLevel` | `analyzer/extract.py` | Fuel level mean | Auto-detect fuel load | context-only | |
| `LFwearM`, `RFwearM`, etc. | `analyzer/extract.py` | Tyre wear estimate | Thermal diagnosis | diagnostic-only | Indirect proxy |
| `Gear` | `analyzer/extract.py` | Braking zone detection | Corner segmentation | context-only | |
| `AeroCalcFrontRhAtSpeed`, `AeroCalcRearRhAtSpeed` | Not read by default | N/A | N/A | **unused** | AeroCalc channels exist in IBT but are not read by `extract.py`. The solver computes its own aero operating point from sensor RH channels instead |
| `AeroCalcDFBalance` | Not read | N/A | N/A | **unused** | Same — would be the ground truth for DF balance but is not ingested |
| `LFshockDefl`, `RFshockDefl` | `analyzer/extract.py` | Corner shock deflection | Used in legality check indirectly | diagnostic-only | |
| `TireCompound` | `analyzer/setup_reader.py` | Setup extraction | Not used by solver | context-only | |
| `front_dominant_freq_hz` | `extract.py` (FFT) | Natural frequency | Heave calibration store | diagnostic-only | Used for heave calibration; not directly in solver |
| `rear_shock_oscillation_hz` | `extract.py` | Damper underdamping detection | Triggers damper zeta floor bump in learner | diagnostic-only | |
| `roll_distribution_proxy` (derived) | `extract.py` | LLTD proxy via RH delta | Modifier: LLTD offset | diagnostic-only | Documented as proxy, not true LLTD |

---

## 7. Unused, Unwired, or Overlapping Code

### 7.1 Dead Code (No External Callers)

| Module | Evidence | Notes |
|--------|----------|-------|
| `solver/iterative_solver.py` | `grep -r import.*iterative_solver` returns only itself | Implements a multi-pass cross-step optimizer. Was likely an intermediate approach superseded by `solve_chain.py`. 413 lines of unused code. |
| `solver/corner_strategy.py` | `grep -r import.*corner_strategy` returns only itself | Per-corner RARB blade optimization by speed class. Was intended for the webapp but never wired. |
| `solver/coupling.py` | Only imported by itself and `research/physics-notes.md` | Physics coupling explanations. Used in comments/notes but `explain_change()` is not called from any production path. |
| `analyzer/overhaul.py` | Used only by `analyzer/diagnose.py` | OverhaulAssessment — partially wired (output included in Diagnosis object but not displayed in produce reports or used for routing) |

### 7.2 Experimental (Labeled, Not Production)

| Module | Activation | Notes |
|--------|-----------|-------|
| `solver/bayesian_optimizer.py` | `--bayesian` in `solver/solve.py` only | Custom GP optimizer. Results not applied to final output in solve.py (only printed). |
| `solver/multi_speed_solver.py` | `--multi-speed` in `solver/solve.py` only | Speed-regime compromise. Advisory output only. |
| `solver/explorer.py` | `--explore` in `solver/solve.py` only | Unconstrained param space explorer. Not the same as `--explore` in `produce.py` which is a Sobol mode. |
| `solver/setup_space.py` | `--space` in `solver/solve.py` only | Feasible region analysis. Results discarded after print. |

### 7.3 Legacy / Overlap

| Module | Overlap / Issue | Recommendation |
|--------|----------------|----------------|
| `validator/` (full package) | Duplicates `analyzer/` functionality. `validator/extract.py` extracts a subset of what `analyzer/extract.py` does. `validator/classify.py` classifies discrepancies; `validator/recommend.py` recommends changes — both superseded by `analyzer/diagnose.py` and `solver/modifiers.py` | Deprecate or consolidate. The validator reads solver JSON + IBT while the pipeline reads IBT only — distinct workflows but functionally redundant. |
| `run_full_pipeline.py`, `run_full_v2.py`, `run_full_justified.py`, `run_now.py`, `run_tuned_search.py`, `run_exhaustive.py` | Hardcoded Windows absolute paths. Not portable. Committed as dev artifacts. | Delete from repo or move to `.gitignore`. |
| `comparison/` module | Multi-session comparison scoring system with independent category weights. Not used in the main `produce.py` path. | Assess whether this multi-session scoring is useful separately from the solver's objective. Currently operates in parallel without integration. |
| `analyzer/sto_reader.py`, `analyzer/sto_binary.py`, `analyzer/sto_adapters.py` | Three different STO file reading approaches | `sto_reader.py` and `sto_binary.py` appear to be separate implementations of the same problem. Audit for which is the primary reader. |
| `vertical_dynamics.py` (root level) | A standalone physics module at the root that is imported by `solver/objective.py`, `solver/heave_solver.py`, `solver/damper_solver.py` | Should be moved to `solver/` or a `physics/` package for cleaner import structure. |

### 7.4 Partially Wired

| Module | Wired State | Issue |
|--------|-------------|-------|
| `analyzer/causal_graph.py` | Imported by `analyzer/diagnose.py` but result (`causal_diagnosis`) is never used by `solver/modifiers.py` | Causal reasoning output exists in Diagnosis object but no solver path reads it |
| `analyzer/conflict_resolver.py` | Used by `analyzer/telemetry_truth.py` | Partially wired into signal quality tracking but conflict flags do not gate solver decisions |
| `analyzer/state_inference.py` | Produces `CarStateIssue` list used in `_compute_single_session_authority()` | Wired for authority scoring but not for routing or modifier decisions |
| `solver/predictor.py` | Output `PredictedTelemetry` used for sanity checks and learner storage | Prediction accuracy for non-BMW cars is uncalibrated |
| `learner/cross_track.py` | Imported in learner module but not called by ingest or recall | Cross-track transfer learning — incomplete |
| `learner/report_section.py` | Appears unused by pipeline | Optional enrichment; may be dead |

---

## 8. Repo / Runtime Hygiene Issues

### 8.1 Generated Artifacts Committed to Source

- `exhaustive_output.txt`, `full_justified_output.txt`, `full_output.txt`, `full_pipeline_output.txt`, `full_pipeline_output_v2.txt`, `run_output.txt`, `setup_output.txt`, `tuned_output.txt` — large text output files from dev runs
- `stdout.txt`, `stderr.txt` — captured process output  
- `best.sto`, `today.sto`, `optimal.sto`, `optimalnf.sto`, `optimalcaddy.sto`, `cadillac_silverstone.sto`, `reasoned.sto`, `idk.sto`, `test.sto`, `test_phase4.sto`, `test_stripped.sto`, `output.sto` — generated setup files  
- `tmp_bmw_prefix.bin`, `tmp_vrs_prefix.bin` — binary dev artifacts
- `commit_msg.txt`, `commit_msg2.txt`, `_syntax_result.txt`, `_syntax_result2.txt`, `_git_result.txt` — dev shell artifacts
- `git_push.bat`, `run_filter.sh`, `remove_large_file.sh`, `syntax_check.bat` — dev scripts

### 8.2 Windows-Path Artifacts in Production Code

- `run_full_pipeline.py`, `run_full_v2.py`, etc. contain hardcoded `C:\Users\VYRAL\IOptimal\...` paths. These are committed as production Python files but only run on a specific developer machine.

### 8.3 Misleading Module Names / Entry Points

- `solver/solve.py` and `pipeline/produce.py` are both entrypoints, but `solve.py` is the "simpler" path without IBT. The distinction is unclear from the root `__main__.py` which tries to detect IBT usage.
- `solver/explorer.py::SetupExplorer` (`--explore`) and `ObjectiveFunction`'s `explore=True` mode are different things with the same name.
- `solver/grid_search.py` calls itself `GridSearchEngine` but is a hierarchical Sobol+coordinate descent — not a traditional grid search.

### 8.4 Entrypoint Clarity

- Root `__main__.py` exists but its relationship to `pipeline/produce.py::main()` and `solver/solve.py::main()` is not immediately clear.
- `pipeline/__main__.py` and `webapp/__main__.py` provide additional entry points.
- The desktop app (`desktop/app.py`) wraps the watcher, which auto-invokes the pipeline. This path is not documented as a distinct pipeline entry in the main docs.

### 8.5 Data Directory Structure

- `data/learnings/observations/` contains per-session JSON files (BMW Sebring) — these are generated data but are runtime-critical for the learner/empirical path.
- `data/calibration_dataset.json` is a critical file for the BMW optimizer (loaded via `lru_cache`) and is committed as source data.
- `data/aeromaps/` xlsx files are large blobs that define the aero model — no `.gitignore` guidance on these.

---

## 9. Recommended Module Status Map

### Production-Critical
- `pipeline/produce.py` — primary pipeline entry
- `pipeline/reason.py` — multi-IBT reasoning
- `solver/solve_chain.py` — actual solver orchestration
- `solver/rake_solver.py`, `heave_solver.py`, `corner_spring_solver.py`, `arb_solver.py`, `wheel_geometry_solver.py`, `damper_solver.py` — 6-step solver stages
- `solver/supporting_solver.py` — brake/diff/TC/pressure
- `solver/modifiers.py` — telemetry → solver adjustments
- `solver/full_setup_optimizer.py` — BMW/Sebring calibrated optimizer
- `solver/legality_engine.py` — legal validation
- `solver/scenario_profiles.py` — scenario weights and sanity limits
- `solver/objective.py` — grid search scoring (needs calibration)
- `solver/legal_search.py` — random manifold search
- `solver/grid_search.py` — structured search
- `solver/candidate_search.py` — candidate family generation
- `solver/candidate_ranker.py` — candidate scoring (family path)
- `analyzer/extract.py`, `diagnose.py`, `driver_style.py`, `segment.py`, `modifiers.py` — telemetry pipeline
- `analyzer/telemetry_truth.py` — signal quality tracking
- `car_model/cars.py`, `setup_registry.py`, `garage.py` — car models
- `aero_model/` — aero surfaces
- `track_model/` — track profiles and IBT parser
- `output/setup_writer.py`, `output/report.py`, `output/garage_validator.py` — output generation
- `learner/` — empirical correction system (knowledge store + ingest)
- `solver/decision_trace.py`, `solver/predictor.py`, `output/run_trace.py` — transparency and provenance
- `vertical_dynamics.py` — core physics

### Supported Secondary
- `solver/solve.py` — track-only (no IBT) standalone entry
- `solver/legal_space.py` — legal parameter space definition
- `solver/session_database.py` — k-NN empirical scoring
- `solver/heave_calibration.py` — empirical heave → sigma model
- `solver/bmw_rotation_search.py` — BMW-specific rotation control search
- `solver/bmw_coverage.py` — parameter/telemetry coverage reporting
- `comparison/` — multi-session analysis
- `solver/stint_reasoner.py`, `solver/stint_model.py` — stint analysis
- `solver/sector_compromise.py` — sector tradeoffs
- `solver/laptime_sensitivity.py` — parameter sensitivity
- `pipeline/preset_compare.py` — preset comparison
- `validation/run_validation.py`, `validation/objective_calibration.py` — calibration tooling (manual use)
- `watcher/`, `desktop/`, `teamdb/`, `server/` — deployment infrastructure
- `solver/sensitivity.py`, `solver/uncertainty.py` — constraint and uncertainty reporting

### Experimental (label preserved)
- `solver/bayesian_optimizer.py` — GP Bayesian optimizer (research)
- `solver/multi_speed_solver.py` — speed-regime compromise (research)
- `solver/explorer.py` — unconstrained explorer (research)
- `solver/setup_space.py` — feasibility analysis (research)
- `learner/cross_track.py` — cross-track transfer (incomplete)

### Legacy / Merge / Deprecate Candidates
- `solver/iterative_solver.py` — **dead code; delete**
- `solver/corner_strategy.py` — **dead code; delete**
- `solver/coupling.py` — **dead code; merge documentation into comments or delete**
- `validator/` package — **legacy; superseded by analyzer/; deprecate or consolidate**
- `run_full_pipeline.py`, `run_full_v2.py`, `run_full_justified.py`, `run_now.py`, `run_tuned_search.py`, `run_exhaustive.py` — **dev artifacts; delete**
- `research/ferrari_calibration_*.py` — **research scripts; move to /scripts or delete**
- Large committed output files (`.sto`, `.txt`, `.bin`) — **remove from source control**

---

## 10. Fix Plan in Priority Order

### Priority 1: Calibrate Ferrari and Acura Damper Physics (High Impact, Low Risk)

**Problem:** `ls_force_per_click_n` and `hs_force_per_click_n` for Ferrari (`7.0` / `30.0`) and Acura (`18.0` / `80.0`) are estimates. These constants convert damper click positions to physical force, which determines damping ratios, which drives the damper solver outputs AND the objective function scoring.

**Fix:** Record IBT sessions with systematic damper sweeps (vary LS comp clicks from 1→max while holding all else constant; extract `LFshockVel` p95 at same track section). Fit force = a × clicks at reference velocity. Update `car_model/cars.py` for both cars.

**Files:** `car_model/cars.py` → `DamperModel.ls_force_per_click_n`, `hs_force_per_click_n`

---

### Priority 2: Calibrate Ferrari `m_eff_kg` from IBT (High Impact, Low Risk)

**Problem:** Ferrari `front_m_eff_kg=176.0` and `rear_m_eff_kg=2870.0` are estimates. BMW uses a calibrated value from the learner. Ferrari's value directly controls heave spring sizing (the bottoming constraint) and platform sigma scoring.

**Fix:** Run Ferrari sessions with varying heave spring index settings. Extract `HFshockDefl` p99 per session. Fit `excursion_p99 = v_p99 * sqrt(m_eff / k)` to back-solve `m_eff`. At minimum, 3 sessions with different heave indices.

**Files:** `car_model/cars.py` → `HeaveSpringModel.front_m_eff_kg`, `rear_m_eff_kg` for Ferrari

---

### Priority 3: Fix Acura Front RH Model (High Impact, Medium Complexity)

**Problem:** The Acura front RH is camber-dominated (`r²=0.988`, slope 2.39mm/deg) but the solver's `PushrodGeometry` model cannot represent this (it uses pushrod offset, not camber). The result: the rake solver cannot correctly predict or target front static RH for the Acura.

**Fix:** Add a `camber_to_rh` coefficient to `PushrodGeometry` (or create an Acura-specific subclass). Update `RakeSolver` to use camber when solving for front RH. The calibration data is already in `cars.py` comments but not wired into the model.

**Files:** `car_model/cars.py` → `PushrodGeometry`, `solver/rake_solver.py`

---

### Priority 4: Fix produce.py Double-Solve (Performance, Correctness)

**Problem:** `pipeline/produce.py::produce()` explicitly runs Steps 1–6 in its own loop (lines ~628–866), then immediately calls `run_base_solve(solve_inputs)` which runs the entire 6-step chain again from scratch. The first pass results are discarded. This doubles the solve time and suggests the two code paths diverged.

**Fix:** Remove the explicit step-by-step loop from `produce.py`. Use only `run_base_solve()`. Verify the modifier arguments are correctly passed through `SolveChainInputs`.

**Files:** `pipeline/produce.py` lines 597–895

---

### Priority 5: Remove Dead Code (Clarity, Maintainability)

Delete or archive:
- `solver/iterative_solver.py` (413 lines, no callers)
- `solver/corner_strategy.py` (no callers)
- `solver/coupling.py` (no production callers)
- `run_full_pipeline.py`, `run_full_v2.py`, `run_full_justified.py`, `run_now.py`, `run_tuned_search.py`, `run_exhaustive.py` (hardcoded Windows paths)
- Large output artifacts from root directory

---

### Priority 6: Grow BMW/Sebring Dataset and Apply Calibrated Weights

**Problem:** 98 non-vetoed observations yield Spearman −0.18. The calibration tooling exists (`validation/objective_calibration.py`) but results are not auto-applied because stability is insufficient.

**Fix:** Target 200+ observations. Run the calibration tool, check that holdout worst-case Spearman is reliably negative (< −0.15 in all 10-fold splits). Only then apply calibrated weights at runtime via `scenario_profiles.py`.

---

### Priority 7: Suppress "Optimal" Claims for Non-BMW Cars

**Problem:** The pipeline output and JSON report use "optimal" language for Ferrari/Acura outputs even though the physics constants are ESTIMATEs.

**Fix:** Add a calibration tier check in `pipeline/report.py` and `output/report.py`. If `validation_tier != "full"` or car is not BMW/Sebring, emit "Physics estimate — parameters not calibrated" banners instead of "optimal" claims.

**Files:** `pipeline/report.py`, `output/report.py`, `solver/decision_trace.py`

---

### Priority 8: Calibrate Acura and Ferrari Torsion Bar C Constants

**Problem:** Acura `front_torsion_c=0.0008036` is borrowed from BMW ("same as BMW until calibrated"). Ferrari C is calibrated (`0.001282`) but Acura is not. Wrong C constant → wrong spring rate → wrong corner spring solver output → wrong ARB LLTD calculation.

**Fix:** Take 4–6 Acura garage screenshots at different front torsion bar OD settings, measure corner weight (from setup screen), back-solve C. Match the Ferrari calibration protocol from `car_model/cars.py` lines 1743–1752.

**Files:** `car_model/cars.py` → `ACURA_ARX06.corner_spring.front_torsion_c`

---

### Priority 9: Wire `AeroCalcFrontRhAtSpeed` / `AeroCalcDFBalance` Channels

**Problem:** The IBT contains iRacing's internal aero calculator output (`AeroCalcFrontRhAtSpeed`, `AeroCalcRearRhAtSpeed`, `AeroCalcDFBalance`) which would be the direct ground truth for the aero model. These channels are not read by `analyzer/extract.py`. The solver currently estimates dynamic RH and DF balance indirectly.

**Fix:** Add these channel extractions to `extract.py`. Compare against solver predictions to calibrate aero compression and DF balance targeting. This is the most direct path to accurate aero model calibration for all cars.

**Files:** `analyzer/extract.py`, `analyzer/extract.py::MeasuredState`

---

### Priority 10: Consolidate or Deprecate `validator/` Package

The `validator/` package duplicates `analyzer/` functionality. If it serves a distinct workflow (comparing solver JSON predictions vs IBT), document it clearly. If it is superseded, deprecate it.

---

## 11. Appendices

### A. Key File / Function References

| Topic | File | Function / Class |
|-------|------|-----------------|
| Double-solve bug | `pipeline/produce.py` | Lines 597–895 (explicit) + lines 869–899 (`run_base_solve`) |
| BMW-only optimizer gate | `solver/full_setup_optimizer.py` | `_is_bmw_sebring()`, `optimize_if_supported()` |
| Objective scoring | `solver/objective.py` | `ObjectiveFunction.evaluate()`, `_estimate_lap_gain()` |
| Scenario weights | `solver/scenario_profiles.py` | `_SCENARIOS`, `ObjectiveWeightProfile` |
| Single-point coupling | `solver/objective.py` | `TORSION_ARB_COUPLING = 0.25` |
| RH model (BMW) | `car_model/cars.py` | `RideHeightModel` (R²=0.15 front) |
| Ferrari indexed heave | `car_model/cars.py` | `FERRARI_499P.heave_spring.front_setting_*` |
| Acura RH camber dominance | `car_model/cars.py` | `ACURA_ARX06.pushrod` comment |
| Aero channel not read | `analyzer/extract.py` | Missing `AeroCalcFrontRhAtSpeed` extraction |
| Aero compression not applied | `pipeline/produce.py` | Lines 315–321 (intentional omission comment) |
| Legal veto mechanism (objective) | `solver/objective.py` | `_compute_platform_risk()` → `veto_reasons` |
| Legal veto mechanism (optimizer) | `solver/full_setup_optimizer.py` | Lines 184–195, penalty `+= 1e6` without exclusion |
| Dead code (iterative) | `solver/iterative_solver.py` | Entire file |
| Dead code (corner strategy) | `solver/corner_strategy.py` | Entire file |
| k-NN empirical scoring | `solver/session_database.py` | `SessionDatabase.predict()`, `SessionDatabase.score()` |
| Calibration correlation | `validation/objective_validation.json` | `score_correlation.spearman_r_non_vetoed = -0.18` |

### B. Open Questions / Uncertainties

1. **Ferrari heave index ↔ rate mapping**: `front_rate_per_index_nmm=20.0` and anchor at index 1 = 50 N/mm are approximations from observation, not a systematic sweep. The actual rate mapping may be non-linear.

2. **Acura roll damper contribution to LLTD**: Roll dampers contribute to instantaneous roll resistance but the LLTD model only accounts for spring/ARB stiffness. The roll damper's effective roll stiffness contribution is not modeled.

3. **BMW calibration dataset (`data/calibration_dataset.json`)**: This drives the BMW optimizer. It is unclear how current this file is or whether it was generated from a subset of the 98 validation sessions or a separate dataset.

4. **`TORSION_ARB_COUPLING` for non-BMW cars**: The code comments say BMW is 0.25 but `getattr(self.car, "torsion_arb_coupling", self.TORSION_ARB_COUPLING)` would use the class constant for cars that don't set the attribute. None of the other car models set `torsion_arb_coupling`, meaning they all inherit 0.25. For Ferrari/Acura this is an unvalidated cross-car transfer of a BMW-specific coupling.

5. **Candidate family vs grid search interaction**: When both `generate_candidate_families()` and `--search-mode` run, the grid search result overwrites the family candidate. The user may not know which path was taken. The `run_trace.record_solver_path()` call helps but is not prominently displayed.

### C. Contradictions Between Docs and Code

| Doc Claim | Code Reality |
|-----------|-------------|
| CLAUDE.md: "BMW/Sebring non-vetoed Spearman was `-0.120522`" | `validation/objective_validation.json`: `spearman_r_non_vetoed = -0.1808` (current run; either the doc is stale or a different calibration state) |
| CLAUDE.md: "73 observations, 72 non-vetoed" | `objective_validation.json`: `samples=99`, `non_vetoed=98` (different numbers; doc likely stale) |
| CLAUDE.md: "Acura: 7 observations ingested" | `objective_validation.json`: acura samples=0, unsupported tier |
| CLAUDE.md: "damper targets updated to IBT-calibrated values (0.68/0.23/0.47/0.20)" | `solver/objective.py` line 1223-1253: zeta targets are in code as penalty targets; old comment on line 1221 still says "targets kept at original values (0.88/0.30/0.45/0.14)" — the comment is internally inconsistent with the code below it |
| CLAUDE.md: solve chain produces `.sto + JSON + engineering report` | `produce.py` double-solves: runs the 6-step chain twice; second run via `run_base_solve()` overwrites first |

### D. Important Code Snippets

**The BMW optimizer gate (why other cars use the sequential solver):**
```python
# solver/full_setup_optimizer.py::_is_bmw_sebring()
def _is_bmw_sebring(car: Any, track: Any) -> bool:
    return (
        getattr(car, "canonical_name", "").lower() == "bmw"
        and "sebring" in getattr(track, "track_name", "").lower()
    )
```

**The veto-as-penalty issue (optimizer doesn't hard-exclude vetoed candidates):**
```python
# solver/full_setup_optimizer.py line ~194
candidate.score += penalty  # penalty = 1e6 or 5e4; candidate is NOT excluded
if best_any is None or candidate.score < best_any.score:
    best_any = candidate
```

**The double-solve in produce.py (first solve's results are discarded):**
```python
# produce.py lines 597-895: Runs Steps 1-6 explicitly
optimized = optimize_if_supported(...)  # first solve pass
if optimized is not None:
    step1 = optimized.step1; ...
else:
    step1 = rake_solver.solve(...); ...  # explicit sequential pass
    ...step6 = damper_solver.solve(...)
# Lines 869-899: Second full solve — OVERWRITES everything above
base_solve_result = run_base_solve(solve_inputs)  # re-runs entire 6-step chain
step1 = base_solve_result.step1  # previous step1 discarded
...
```

**The aero compression not-applied comment:**
```python
# pipeline/produce.py lines 315-321
# NOTE: aero_compression_{front,rear}_mm are intentionally NOT applied here.
# The IBT LFrideHeight/LRrideHeight sensor channels are in a different
# coordinate frame than the aero maps (AeroCalc reference). Applying the
# sensor-measured compression to the aero map solver produces inflated
# static RH recommendations (+10-15mm error). The car-model values in
# cars.py are calibrated directly from AeroCalculator IBT fields and
# are the correct reference for the aero solver.
```
*Note: This rationale is sound, but means `AeroCalcFrontRhAtSpeed` (which IS in the correct reference frame) should be read instead of `LFrideHeight`. Currently neither the AeroCalc channels nor the sensor channels feed directly to the aero model.*

---

## Handoff Summary for Continuing Engineering Work

**To the next model continuing this audit or fix work:**

This codebase implements a serious physics-based solver that is correctly architected but has three layers of problems:

**Layer 1 — Correctness:** The BMW/Sebring path works and produces physically defensible outputs. The objective function's absolute calibration is weak (Spearman −0.18) but the physics steps (rake, heave, ARB) are grounded in real physics and real telemetry data. Trust the 6-step sequential solver for BMW.

**Layer 2 — Non-BMW cars:** Ferrari and Acura produce outputs that follow the correct algorithms but on estimated physics constants. The outputs look plausible but are uncalibrated. Do not present Ferrari/Acura outputs as "optimal." The fix is systematic: calibrate damper force constants, m_eff, torsion C constants in that priority order. Each calibration requires 3–6 targeted IBT sessions with controlled parameter sweeps.

**Layer 3 — Dead weight:** ~1200 lines of dead code in `solver/iterative_solver.py`, `solver/corner_strategy.py`, `solver/coupling.py` and ~10 root-level hardcoded dev scripts. Delete these.

**Biggest single architectural fix:** In `pipeline/produce.py`, remove the redundant explicit 6-step loop (lines ~597–866). Everything goes through `run_base_solve()` which already runs the full solver. The explicit loop is a historical artifact of the pipeline growing around the existing solve.py code path.

**Don't touch until calibrated:** The objective function weights in `solver/scenario_profiles.py` should not be changed until BMW/Sebring Spearman is reliably < −0.20 in holdout. The `validation/objective_calibration.py` tooling will tell you when that threshold is met.

**For ride height accuracy:** The AeroCalcFrontRhAtSpeed and AeroCalcDFBalance IBT channels are the missing link. Reading them would provide ground truth for the aero model calibration and would enable direct validation of the solver's aero predictions. This is the highest-leverage single telemetry addition.
