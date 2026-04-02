# IOptimal Codebase Audit Report
**Branch:** claw-research  
**Date:** 2026-03-31  
**Auditor:** Deep static analysis of all source files  
**Audience:** Engineering model for follow-up fix work

---

## 1. Executive Summary

IOptimal is a physics-based GTP/Hypercar setup solver that takes iRacing IBT telemetry as input and produces `.sto` garage setup files. The architecture is well-structured with a clear 6-step sequential solver pipeline orchestrated through `solver/solve_chain.py::run_base_solve()`.

**However, the system is deeply BMW+Sebring-centric.** The constrained optimizer, the rotation search, the heave calibration store, the GarageOutputModel, the deflection regression coefficients, the effective heave mass (`m_eff`), and the ride-height prediction model are all calibrated exclusively from BMW Sebring IBT telemetry. Every other car (Ferrari 499P, Acura ARX-06, Cadillac V-Series.R, Porsche 963) runs through the sequential solver using parameters marked `ESTIMATE` in the source code, many of which are copied directly from BMW or set to placeholder values.

**The three most important architectural risks are:**

1. **No generalized constrained optimizer for non-BMW cars.** `solver/full_setup_optimizer.py::BMWSebringOptimizer` guards its execution with `if car.canonical_name == "bmw"`. Ferrari and Acura always fall into the sequential solver, which outputs physically reasonable but uncalibrated values.

2. **Ferrari and Acura use indexed garage controls, not physical N/mm values.** The decode/encode round-trip uses estimated linear slopes (`front_rate_per_index_nmm=20.0` for Ferrari heave, `front_rate_per_index_nmm=None` for Acura which bypasses decoding entirely). The solver never confirmed that index 1 on the Ferrari = 50 N/mm or index 9 on the Acura = any specific physical rate. The physical-to-garage mapping is the primary source of Ferrari/Acura output inaccuracy.

3. **Calibration data silos.** The `DeflectionModel`, `RideHeightModel`, `HeaveSpringModel` regression coefficients in `car_model/cars.py` are BMW Sebring regression fits. Ferrari and Acura have `DeflectionModel()` with **default zeros** — meaning deflection values in generated `.sto` files use BMW formulas on non-BMW cars.

**Top three fixes in order:**
1. Build a per-car garage-control decode table from actual garage screenshots (Ferrari index → N/mm sweep) and treat it as truth.
2. Calibrate `m_eff_front`, `m_eff_rear`, `aero_compression`, and `DeflectionModel` per car from their own IBT sessions.
3. Extract and generalize the optimizer beyond BMW/Sebring — the current sequential solver order is correct; the optimizer just needs per-car seed ranges.

---

## 2. Actual Production Path

### 2.1 Primary Production Path

**Entry point:** `python -m ioptimal` → `__main__.py::main()`

The unified CLI router in `__main__.py` selects a mode based on arguments:

```
__main__.py::main()
  └─ if single --ibt:
       pipeline/produce.py::produce(args)            ← PRIMARY PATH
         Phase A: load car model (car_model/cars.py::get_car)
         Phase B: load surface (aero_model)
         Phase C: extract telemetry (analyzer/extract.py::extract_measurements)
         Phase D: segment corners (analyzer/segment.py)
         Phase E: analyze driver style (analyzer/driver_style.py::analyze_driver)
         Phase F: compute adaptive thresholds (analyzer/adaptive_thresholds.py)
         Phase G: diagnose handling (analyzer/diagnose.py::diagnose)
         Phase H: compute aero gradients (aero_model/gradient.py::compute_gradients)
         Phase I: compute solver modifiers (analyzer/recommend.py derived)
         Phase J: run solver chain (solver/solve_chain.py::run_base_solve)
                    └─ full_setup_optimizer.py::optimize_if_supported (BMW/Sebring only)
                    └─ solve_chain.py::_run_sequential_solver (all cars)
                    └─ supporting_solver.py::SupportingSolver.solve()
                    └─ bmw_rotation_search.py::search_rotation_controls (BMW only)
                    └─ legality_engine.py::validate_solution_legality
                    └─ predictor.py::predict_candidate_telemetry
                    └─ decision_trace.py::build_parameter_decisions
         Phase K: generate report (pipeline/report.py::generate_report)
         Phase L: export .sto (analyzer/sto_binary.py / analyzer/sto_reader.py)
```

### 2.2 Supported Secondary Paths

**Multi-session reasoning:**
```
__main__.py::main()
  └─ if multiple --ibt files (--ibt a.ibt b.ibt c.ibt):
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

**Preset comparison:**
```
python -m pipeline --compare base.sto test.sto --car bmw --track sebring
  └─ pipeline/preset_compare.py
```

**Setup comparison (validator):**
```
python -m validator --ibt session.ibt --setup setup.sto --car bmw
  └─ validator/__main__.py → validator/compare.py, validator/classify.py
```

**Comparison module:**
```
python -m comparison --ibt a.ibt b.ibt --car bmw
  └─ comparison/__main__.py → comparison/compare.py, comparison/score.py
```

### 2.3 Experimental / Analysis-Only Paths

- `run_exhaustive.py` — Grid search over parameter space, dumps CSV. Not wired to main pipeline.
- `run_tuned_search.py` — Manual tuned grid search. Standalone script, no CLI hook.
- `run_full_justified.py`, `run_full_v2.py`, `run_full_pipeline.py` — Ad-hoc pipeline runners, pre-date `__main__.py` refactor. Redundant with `python -m ioptimal`.
- `run_now.py` — Quick dev-test entrypoint. Hardcoded BMW/Sebring. Legacy.
- `vertical_dynamics.py` (root level) — Shared physics helpers used by solver. Not a standalone path.
- `solver/bayesian_optimizer.py` — Gaussian Process optimizer. NOT wired into production path. `optimize_if_supported()` never invokes it; only `BMWSebringOptimizer` is called.
- `solver/grid_search.py` — Grid search utility used by `full_setup_optimizer.py` internally. Not a standalone path.
- `solver/explorer.py` — Parameter space explorer. Not wired to production.
- `solver/multi_speed_solver.py` — Multi-speed compromise analysis. Called from `pipeline/reason.py` but path is unclear; not in `produce.py`.
- `solver/sector_compromise.py` — Sector-level compromise. Not wired to production `produce.py`.
- `solver/laptime_sensitivity.py` — Sensitivity analysis. Only invoked via `solver/solve.py --sensitivity` flag (standalone solver CLI), not `pipeline/produce.py`.
- `track_model/` — Track profile builder and generic profiles. Mostly analysis-only.

### 2.4 Likely Legacy / Overlap-Heavy Paths

- `solver/solve.py` (`python -m solver.solve`) — Older standalone solver CLI. Overlaps completely with `python -m ioptimal --solve-only`. Still functional but predates the unified `__main__.py`.
- `run_full_pipeline.py`, `run_full_v2.py`, `run_full_justified.py` — All call `pipeline/produce.py` with hardcoded paths. Should be removed.
- `analyzer/__main__.py` — Calls `pipeline.produce.produce_result` directly. Another entry point that duplicates `python -m ioptimal`.
- `comparison/__main__.py` vs `validator/__main__.py` — Both are session-comparison tools with overlapping scope. Should be merged or clearly differentiated.

---

## 3. How the Solver Works

### 3.1 Orchestration Path

**Entry:** `solver/solve_chain.py::run_base_solve(inputs: SolveChainInputs)`

```
run_base_solve(inputs)
  1. Try constrained optimizer:
     full_setup_optimizer.py::optimize_if_supported(...)
       → Only runs for BMW + Sebring (guards on car.canonical_name == "bmw")
       → If supported: BMWSebringOptimizer._run() runs SLSQP over seed space
       → Returns OptimizedResult or None
  
  2. If optimizer returned None OR all_candidates_vetoed:
     _run_sequential_solver(inputs) → (step1..step6, rear_wheel_rate)
  
  3. Build supporting parameters:
     _build_supporting(inputs) → SupportingSolver.solve()
  
  4. Optionally refine with BMW rotation search:
     bmw_rotation_search.py::search_rotation_controls(base_result, inputs)
       → BMW only (guards on car.canonical_name == "bmw")
  
  5. Finalize:
     _finalize_result(inputs, step1..step6, supporting)
       → validate_solution_legality(...)  → LegalValidation
       → build_parameter_decisions(...)   → decision trace
       → predict_candidate_telemetry(...) → PredictedTelemetry
```

### 3.2 Sequential Solver Stages

The 6-step sequential solver runs in `_run_sequential_solver()`. Each step feeds into the next:

| Step | Solver | Output | Key inputs |
|------|--------|--------|------------|
| Step 1 | `RakeSolver.solve()` | `RakeSolution` (static/dynamic RH, pushrod offsets) | target_balance, fuel, surface, aero map |
| Step 2 | `HeaveSolver.solve()` | `HeaveSolution` (front_heave_nmm, rear_third_nmm, perch offsets) | dynamic RH from step1, m_eff, track bump freq |
| Step 3 | `CornerSpringSolver.solve()` | `CornerSpringSolution` (torsion bar OD, rear spring rate, wheel rates) | heave from step2, fuel, car.corner_spring |
| Step 4 | `ARBSolver.solve()` | `ARBSolution` (front/rear ARB size and blade) | wheel rates from step3, target LLTD, lltd_offset |
| Step 5 | `WheelGeometrySolver.solve()` | `WheelGeometrySolution` (camber, toe) | roll stiffness from step4, measured camber, fuel |
| Step 6 | `DamperSolver.solve()` | `DamperSolution` (per-corner LS/HS comp/rbd clicks) | wheel rates, dynamic RH, heave/third rates, measured shock vel |

**Important:** Steps 2 and 3 are run **twice** in the sequential solver:
- Pass 1: initial heave/spring solve without damper coupling
- Provisional step6: preliminary damper solve for HS damper coefficients
- Pass 2: re-solve heave/springs with HS damper coupling (`front_hs_damper_nsm`, `rear_hs_damper_nsm`)
- Final step6: re-solve dampers with corrected spring rates

Then `reconcile_ride_heights()` is called after steps 2, 3, and 5 to enforce ride height constraints.

### 3.3 Fallback Behavior

In `run_base_solve()`:
1. If optimizer succeeds → use optimizer result (notes: "Selected constrained optimizer candidate.")
2. If optimizer is None (non-BMW car or non-Sebring track) → use sequential solver (notes: "Selected sequential solver path.")
3. If optimizer returns `all_candidates_vetoed=True` → fall back to sequential solver (notes: "Selected sequential fallback.")
4. If sequential result matches a failed cluster fingerprint → try to restore lowest-penalty optimizer candidate
5. If both are vetoed → use sequential anyway with warning

**Fingerprint veto** (`solver/setup_fingerprint.py`) hashes the solver step outputs into a cluster fingerprint and checks against `failed_validation_clusters`. This prevents the solver from recommending setups that were previously judged BAD by the validator. However, **this only works if failed clusters are passed in** — `pipeline/produce.py` does NOT populate `failed_validation_clusters` in `SolveChainInputs`. The veto mechanism is wired but effectively dead in the production path.

### 3.4 Constrained Optimizer (BMW/Sebring Only)

`solver/full_setup_optimizer.py::BMWSebringOptimizer._run()`:

1. Generate seed candidates from `_build_seed_candidates(car, track, measured, wing)` — iterates over a calibration dataset of real BMW Sebring setups.
2. For each seed, run `scipy.optimize.minimize(method="SLSQP")` over continuous variables: `[front_pushrod, rear_pushrod, front_heave_perch, rear_third_perch, front_camber, rear_camber]`
3. The SLSQP objective calls `_evaluate_candidate()`, which runs the full 6-step solver chain and scores the result with `ObjectiveFunction`.
4. Validate garage constraints (ride height floors, deflection limits, vortex burst, slider position).
5. Fingerprint-veto any candidate that matches a failed cluster.
6. Return the best non-vetoed candidate.

**Critical observation:** The optimizer only optimizes **6 continuous parameters** (pushrods, perches, cambers). The discrete parameters (torsion bar OD, rear spring rate, ARB size/blade) are determined by the sequential 6-step solver called inside the objective. There is no outer loop over discrete combinations.

### 3.5 Legality / Veto Flow

`solver/legality_engine.py::validate_solution_legality()`:
- Checks GTP class rules: ride height min (30mm front/rear for all cars), wing angle legality
- Checks fuel load vs max capacity
- Returns `LegalValidation` with `is_legal: bool` and `violations: list[str]`
- **This is checked AFTER the solver runs.** Illegal results are flagged but not prevented — the solver does not enforce legality as a hard constraint during optimization. Ride height floors are enforced in `reconcile_ride_heights()` but other legality checks are post-facto.
- Legality status flows into `build_parameter_decisions()` and `decision_trace` but does NOT cause a re-solve.

`solver/legal_space.py` and `solver/legal_search.py` — These define a constraint manifold for legal parameter search. Called from `solver/solve.py --legal-search` flag but NOT from `pipeline/produce.py`. Effectively unused in the production path.

### 3.6 Where Final Selection Happens

**Sequential path:** `_finalize_result()` is called once on the single sequential solution. There is no ranking between candidates.

**Optimizer path:** `BMWSebringOptimizer` ranks candidates using `ObjectiveFunction.evaluate()` and returns the one with the **lowest total score** (minimization). The score includes: lap gain, platform risk, driver mismatch, telemetry uncertainty, envelope penalty, staleness penalty, and an empirical k-NN correction.

**BMW rotation search:** After the main solve, `bmw_rotation_search.py::search_rotation_controls()` re-evaluates diff/spring/geometry/rear ARB candidates scored with telemetry-state scoring (rear rotation target from measured yaw rate, lateral G, etc.). This can **override the main solve's diff, ARB, and spring settings** for BMW.

---

## 4. How "Best" Is Chosen

### 4.1 ObjectiveFunction (solver/objective.py)

**Location:** `solver/objective.py::ObjectiveFunction.evaluate(candidate) → CandidateEvaluation`

**What it scores:**
- `lap_gain`: Estimated lap time improvement from DF balance optimization (physics-based)
- `platform_risk`: Penalty for ride height excursion approaching vortex burst threshold
- `driver_mismatch`: Penalty for setup that conflicts with driver style (e.g., aggressive driver + stiff front)
- `telemetry_uncertainty`: Penalty when telemetry quality flags are low
- `envelope_penalty`: Penalty for parameters outside validated envelope
- `staleness_penalty`: Penalty for setups that deviate far from calibrated BMW Sebring baseline
- `knn_empirical`: k-NN correction from historical results (if learner data available)

**Canonical vs path-specific:** This scoring is ONLY invoked by `BMWSebringOptimizer`. The sequential solver does not call `ObjectiveFunction` — it produces one solution and that is the output.

**Interaction with vetoes:** After scoring, fingerprint veto runs independently and can override the ObjectiveFunction ranking by rejecting the highest-scored candidate.

**Can conflict with other scoring paths:** YES.
- `ObjectiveFunction` uses `measured_lltd_target=0.41` for BMW (overriding theoretical ~0.528)
- `ARBSolver.solve()` targets `weight_dist_front + (tyre_load_sensitivity/0.20)*0.05` (theoretical formula)
- These two LLTD targets are different. If the optimizer calls the ARB solver internally, the ARB solver uses the theoretical target but ObjectiveFunction penalizes based on 0.41. This creates a systematic tension.

### 4.2 CandidateRanker (solver/candidate_ranker.py)

**Location:** `solver/candidate_ranker.py::score_from_prediction()`

**What it scores:** Safety / performance / stability / confidence / disruption_cost as a weighted combination.

**Canonical or path-specific:** Path-specific. Only invoked from `pipeline/reason.py` multi-session path. NOT called in `pipeline/produce.py`. 

**Can conflict:** Yes — this is a completely separate scoring mechanism from ObjectiveFunction. Multi-session reasoning scores candidates differently from the single-session path.

### 4.3 BMW Rotation Search Scoring (solver/bmw_rotation_search.py)

**Location:** `bmw_rotation_search.py::score_rotation_candidate(candidate, telemetry_state)`

**What it scores:** Diff preload, diff ramp angles, rear ARB, rear spring rate, geometry (front toe, rear camber) — scored against telemetry-derived rotation target (yaw rate, lateral G, brake stability metrics).

**Canonical or path-specific:** BMW-only, runs after main solve as a second scoring pass.

**Can conflict:** YES. This can select different rear ARB, rear spring rate, and diff settings than what the ObjectiveFunction chose in the optimizer. If the optimizer selected Medium ARB and the rotation search picks Soft ARB, the rotation search wins (`result = rotation_search.result`). The final answer is NOT the ObjectiveFunction minimum.

### 4.4 Comparison Scoring (comparison/score.py, validator/compare.py)

**Location:** `comparison/score.py::score_comparison()`

**What it scores:** Lap time delta, handling classification, parameter-level confidence comparison.

**Canonical or path-specific:** Comparison-only path. Not invoked by main production pipeline.

### 4.5 Summary: Conflicting "Best" Definitions

| Mechanism | File | Used in production? | LLTD target | Selection criterion |
|-----------|------|---------------------|-------------|---------------------|
| ObjectiveFunction | objective.py | BMW optimizer only | 0.41 (calibrated) | Min total score |
| Sequential solver ARB | arb_solver.py | All cars | theoretical formula | Force-balance physics |
| CandidateRanker | candidate_ranker.py | Multi-session path only | N/A | Weighted composite |
| BMW rotation search | bmw_rotation_search.py | BMW only, post-solve | N/A (rotation focus) | Telemetry rotation score |
| Legal manifold search | legal_search.py | NOT in production | N/A | Legal constraint |

---

## 5. Accuracy / Reliability Risks

### 5.1 Scoring / Model Calibration Risks

**[CRITICAL] Non-BMW cars use BMW regression coefficients for deflection prediction.**
- `car_model/cars.py` → Ferrari, Acura, Cadillac, Porsche all instantiate `DeflectionModel()` with no arguments — meaning they use the default BMW Sebring calibrated values (`heave_defl_intercept=71.07...`, `rear_spring_eff_load=6091.76`, etc.).
- These are regression fits from BMW Sebring data. On Ferrari (LMH, completely different suspension architecture, torsion bars at both ends, indexed controls), these values are physically meaningless.
- **Impact:** All deflection fields in generated Ferrari .sto files are wrong.

**[CRITICAL] Ferrari/Acura heave spring index ↔ N/mm mapping is estimated, not validated.**
- `car_model/cars.py::FERRARI_499P::heave_spring.front_rate_per_index_nmm = 20.0` — the comment says "approximate until a full Ferrari sweep is run."
- At index 1 → 50 N/mm. At index 8 → 190 N/mm. This produces a 7-step linear range that was never validated against actual garage screenshots.
- `car_model/cars.py::ACURA_ARX06::heave_spring` — Acura sets `front_setting_index_range=None` (no indexed decode at all for front heave). The `front_rate_from_setting()` returns the raw value unchanged, treating index numbers as N/mm. At Acura, an index of "180" would be treated as 180 N/mm — but the garage only goes to ~380 N/mm with very different physical behavior.
- **Impact:** Every Acura/Ferrari heave spring solve produces values in the wrong physical space.

**[HIGH] Acura rear heave mass (`m_eff`) is uncalibrated and architecturally different.**
- BMW `rear_m_eff_kg=2395.3` (calibrated from 2 IBT sessions). Acura `rear_m_eff_kg=220.0` — 10.8x smaller.
- On the Acura, rear suspension uses the ORECA heave+roll damper architecture (`has_roll_dampers=True`). The "HeaveSpring" controls heave only; roll is handled by separate roll dampers. This fundamentally changes the effective mass coupling.
- The comment admits: "m_eff varies with spring rate (nonlinear sim model): Front: 641kg at 90 N/mm, 319kg at 190 N/mm" — but the solver uses a single constant `front_m_eff_kg=450.0`.
- **Impact:** Heave spring sizing for Acura is non-monotonic and the solver can't represent it correctly with a constant m_eff.

**[HIGH] Ferrari torsion bar OD calibration is good but the decode is applied inconsistently.**
- `car_model/cars.py::FERRARI_499P::corner_spring` — calibrated 6-point sweep gives `C=0.001282`, front OD range 20-24mm, rear OD range 23.1-26mm. This calibration is genuinely good.
- BUT: `corner_spring.front_setting_index_range = (0.0, 18.0)` and `front_torsion_od_range_mm=(20.0, 24.0)` means **linear interpolation** from index 0→OD 20mm, index 18→OD 24mm.
- The actual physical relationship between garage index and bar OD is NOT confirmed as linear. The 6-point sweep shows `k^(1/4) = 3.7829 + 0.04201×idx` — meaning the rate relationship is linear, but the OD relationship is the 4th root of that.
- **Impact:** Index ↔ OD decode for intermediate Ferrari torsion bar values has up to 5.2% error (acknowledged in comments).

**[HIGH] Aero compression model is a single constant per car (not speed/RH dependent).**
- `AeroCompression.front_at_speed(v)` = `compression_ref * (v/v_ref)^2` — correct V² scaling.
- BUT the reference compression itself is a single number (BMW: 15.0mm front, 9.5mm rear) assumed constant across all ride heights and wing angles.
- The comment admits: "Rear compression varies with setup (7.8mm with heave 60/third 450, 9.5mm with heave 50/third 540). This is a known limitation."
- **Impact:** Especially for Cadillac, where rear compression was initially 8mm (ESTIMATE) and later calibrated to 18.5mm — a 2.3x underestimate. The solver was targeting completely wrong dynamic ride heights.

**[MEDIUM] BMW `measured_lltd_target=0.41` overrides ARB solver's theoretical LLTD = 0.528.**
- The ARB solver (`arb_solver.py`) uses: `lltd_target = weight_dist_front + (sensitivity/0.20)*0.05`
- For BMW: `0.4727 + (0.22/0.20)*0.05 = 0.528`
- The ObjectiveFunction uses `measured_lltd_target=0.41` as the penalty reference
- The sequential solver path (used for all non-BMW cars and BMW without optimizer) targets 0.528, but the optimizer-path ObjectiveFunction scores against 0.41
- **Impact:** For BMW on the sequential solver path (non-Sebring tracks), the ARB solve targets 0.528 but expected behavior is 0.41 → ARB will be systematically stiffer than optimal.

### 5.2 Path Fragmentation Risks

**[CRITICAL] Two completely different scoring systems for the same car.**
- BMW at Sebring uses: ObjectiveFunction → k-NN empirical → rotation search → final output
- BMW at any other track uses: sequential solver only → no objective scoring → no rotation search
- Ferrari always uses: sequential solver only → no objective scoring
- **Impact:** BMW Sebring setups are genuinely optimized. BMW Silverstone setups are just physics-derived, not optimized.

**[HIGH] Multi-session reasoning (pipeline/reason.py) uses CandidateRanker, not ObjectiveFunction.**
- When multiple IBT files are passed, `reason_and_solve()` builds a `TargetProfile` and passes it to the solver via `confidence_gated_modifiers`.
- The modifiers adjust `SolveChainInputs` but the scoring is not re-run through ObjectiveFunction.
- **Impact:** Multi-session result quality depends entirely on the quality of the delta extraction and target profile synthesis, which is more heuristic than physics-based.

### 5.3 Telemetry Underuse Risks

**[HIGH] 150+ telemetry fields are extracted by `extract.py` but most never reach the solver.**
- `extract_measurements()` populates ~150 fields on `MeasuredState`.
- Of these, the solver uses primarily: `shock_vel_p99_f_mps`, `shock_vel_p99_r_mps`, `sigma_f_mm`, `sigma_r_mm`, `dynamic_front_rh_mm`, `dynamic_rear_rh_mm`, `lltd_front_pct`.
- Tyre temperature, tyre pressure, tyre wear, pitch angle, yaw rate, lateral G — these reach the BMW rotation search but not the sequential solver or objective function.
- **Impact:** Setup recommendations based on purely physics-derived variables without using available tyre thermal or handling dynamics data.

**[MEDIUM] Heave spring calibration (`heave_calibration.py`) is BMW-only.**
- `solver/heave_calibration.py::HeaveCalibration` stores `(heave_nmm, sigma_front_mm)` pairs from real BMW Sebring IBT runs. Used to interpolate sigma for novel spring rates.
- For non-BMW cars, physics fallback using m_eff is used — but m_eff is uncalibrated for most cars.

### 5.4 Validation Gaps

**[HIGH] Legality check runs after solve — solver can produce illegal setups.**
- `legality_engine.py::validate_solution_legality()` is called in `_finalize_result()`, after all solver steps complete.
- The solver does not reject illegal parameters mid-solve; it only reports them in `LegalValidation.violations`.
- `reconcile_ride_heights()` does enforce the 30mm floor, but other constraints (wing angle, fuel) are post-facto.

**[HIGH] Failed cluster veto is unwired in `pipeline/produce.py`.**
- `SolveChainInputs.failed_validation_clusters` is `None` by default.
- `pipeline/produce.py` never populates this field.
- **Impact:** The fingerprint-based anti-regression mechanism (preventing known-bad setups from being reproduced) is completely inactive in production.

**[MEDIUM] `GarageOutputModel` validates deflections/RH only for BMW Sebring.**
- `car_model/cars.py::BMW_M_HYBRID_V8.garage_output_model` is fully populated.
- All other cars have `garage_output_model=None`.
- `solve_chain.py::_finalize_result()` does not call `garage_output_model.validate()` — it uses `legality_engine` which only checks GTP class rules, not garage deflection limits.
- **Impact:** Ferrari/Acura .sto files may have deflection values outside iRacing's valid range, causing silent failures when loading in-game.

### 5.5 Support Asymmetry Across Cars

| Feature | BMW | Cadillac | Ferrari | Acura | Porsche |
|---------|-----|----------|---------|-------|---------|
| Constrained optimizer | ✅ Sebring only | ❌ | ❌ | ❌ | ❌ |
| m_eff calibrated | ✅ | partial | ❌ | partial | ❌ |
| RideHeightModel calibrated | ✅ | ❌ | ❌ | partial | ❌ |
| GarageOutputModel | ✅ Sebring | ❌ | ❌ | ❌ | ❌ |
| DeflectionModel calibrated | ✅ | ❌ | ❌ | ❌ | ❌ |
| Aero compression calibrated | ✅ | ✅ | ✅ | ❌ | ❌ |
| Control index decode | N/A | N/A | partial | BROKEN | N/A |
| Rotation search | ✅ | ❌ | ❌ | ❌ | ❌ |
| LLTD target measured | ✅ (0.41) | ❌ | ❌ | ❌ | ❌ |
| Rear torsion bar architecture | N/A | N/A | partial | partial | N/A |

---

## 6. Telemetry Channel Audit

The primary telemetry extraction is in `analyzer/extract.py::extract_measurements()`. Below is the audit of key channels:

| Channel / Derived Metric | Where Read | Where Analyzed | Where Affects Solver | Classification | Notes |
|--------------------------|------------|----------------|----------------------|----------------|-------|
| `RideHeight` (FL/FR/RL/RR mm) | `extract.py::_extract_heave_deflection` | Mean, σ, p5/p95/p99 in `MeasuredState` | `dynamic_front_rh_mm`, `dynamic_rear_rh_mm` → RakeSolver step1 | **SOLVE-CRITICAL** | Primary ride height target for Step 1 |
| `ShockVel` (front/rear p50/p95/p99 m/s) | `extract.py::_extract_heave_shock_vel` | `shock_vel_p99_f_mps`, `shock_vel_p99_r_mps` | HeaveSolver step2 (excursion calc), DamperSolver step6 (critical damping) | **SOLVE-CRITICAL** | Most important telemetry input to heave/damper |
| `HeaveDeflection` (`sigma_f_mm`, `sigma_r_mm`) | `extract.py::_extract_heave_deflection` | Standard deviation of ride height | HeaveSolver target constraint | **SOLVE-CRITICAL** | Platform stability constraint |
| `Yaw rate`, `Lat G` (p95) | `extract.py::_extract_handling` | `lateral_g_p95`, `yaw_rate_bias_deg_s` | BMW rotation search only | **diagnostic-only** outside BMW | Used by `bmw_rotation_search.py` for diff/ARB scoring |
| `BrakePressureBias` | `extract.py::_extract_brake_system` | Raw value | `MeasuredState.brake_pressure_bias_pct` → diagnose.py | **CONTEXT-ONLY** | Not used by sequential solver. Used by diagnosis only. |
| `Tyre temps` (FL/FR/RL/RR inner/middle/outer °C) | `extract.py::_extract_tyre_data` | Distribution, min/max, range | diagnose.py → recommendations | **DIAGNOSTIC-ONLY** | Not passed to solver. Only to diagnosis/recommend. |
| `Tyre pressure` (warm psi) | `extract.py::_extract_tyre_data` | `tyre_pressure_*` fields | diagnose.py | **DIAGNOSTIC-ONLY** | Not used by solver at all. |
| `CamberRL/RR deg` | `extract.py` via IBT `.Camber` channel | `rear_camber_deg` in MeasuredState | WheelGeometrySolver step5 (baseline) | **SOLVE-CRITICAL** | Used as camber confidence baseline |
| `SteeringWheelAngle deg` | `extract.py::_extract_raw_inputs` | `steering_angle_p95_deg` | driver_style.py → SolverModifiers | **DIAGNOSTIC-ONLY** | Contributes to driver style profile |
| `ThrottleInput / BrakeInput` | `extract.py::_extract_raw_inputs` | Trail-brake ratio, power-on fraction | driver_style.py → SolverModifiers lltd_offset | **SOLVE-CRITICAL** (indirect) | Driver style → lltd_offset → ARBSolver |
| `DF balance % (computed)` | aero_model/gradient.py | DF balance at dynamic RH | RakeSolver target balance | **SOLVE-CRITICAL** | Balance constraint for Step 1 |
| `FrontAeroDownforce / RearAeroDownforce` | extract.py via IBT | `df_balance_pct` | RakeSolver | **SOLVE-CRITICAL** | If available in IBT |
| `HeaveSpringDeflStatic` (garage value) | `extract.py::_extract_heave_deflection` | Validates static deflection | heave_calibration.py (BMW only) | **diagnostic-only** | Used to calibrate BMW heave model. Not used in Ferrari/Acura solve. |
| `TorsionBarDefl` | `extract.py` via garage schema | `torsion_bar_defl_mm` | DeflectionModel prediction validation | **DIAGNOSTIC-ONLY** | Not used as solver input. Only validates output. |
| `FuelLevel` | `extract.py::_extract_fuel` | `fuel_level_l` session average | `fuel_load_l` in SolveChainInputs | **CONTEXT-ONLY** | Only if --fuel not explicitly passed |
| `WaterTemp / OilTemp` | `extract.py::_extract_environmental` | Context only | diagnose.py thermal flags | **CONTEXT-ONLY** | Not used by solver |
| `RPM`, `gear` | `extract.py::_extract_rpm`, `_extract_gear` | Segment context | driver_style.py | **CONTEXT-ONLY** | Not used directly by solver |
| `Pitch angle deg` | `extract.py::_extract_pitch` | `pitch_angle_deg` | diagnose.py handling balance | **DIAGNOSTIC-ONLY** | Not used by solver |
| `Wind speed/angle` | `extract.py::_extract_wind` | `wind_speed_mps` | context only | **UNUSED** | Extracted but not used anywhere in solver |
| `HybridERS level` | `extract.py::_extract_hybrid` | `ers_deployed_kj` | context only | **UNUSED** | Extracted, not used in setup solver |
| `TrafficFlag` | `analyzer/context.py` | `context_score.traffic_confidence` | Uncertainty scaling in ObjectiveFunction | **CONTEXT-ONLY** | Scales telemetry confidence |
| `LapTime`, `LapDelta` | via inferred from IBT | `diagnose.py` pace validity | context score | **CONTEXT-ONLY** | Not used in solver |
| `shock_settle_time_ms` | `extract.py::_settle_time_signal` | Damper settle time | diagnose.py | **DIAGNOSTIC-ONLY** | Not used by solver directly |
| `dominant_bump_freq_hz` | `extract.py::_dominant_frequency` | Spectral analysis of RH signal | `RideHeightVariance.dominant_bump_freq_hz` replaces car default | **SOLVE-CRITICAL** | If extracted — feeds heave solver frequency target |
| `lltd_front_pct` | computed from lateral G + corner weights | `MeasuredState.lltd_front_pct` | ObjectiveFunction BMW penalty | **SOLVE-CRITICAL** (BMW) | Only used in BMW ObjectiveFunction, not sequential |

---

## 7. Unused, Unwired, or Overlapping Code

### 7.1 Dead Code

**`solver/bayesian_optimizer.py::BayesianOptimizer`**
- **Why dead:** `optimize_if_supported()` in `full_setup_optimizer.py` never calls it. The function is not imported anywhere in the production path. The Bayesian optimizer was written (presumably to replace the SLSQP+grid approach) but was never wired in.
- **Evidence:** No import of `bayesian_optimizer` in `solve_chain.py`, `produce.py`, or `full_setup_optimizer.py`.

**`solver/laptime_sensitivity.py::LaptimeSensitivity`**
- **Why dead:** Only called from `solver/solve.py --sensitivity` flag, which is an old standalone CLI. `pipeline/produce.py` never calls it.
- **Evidence:** Not imported in `produce.py` or `reason.py`.

**`solver/legal_search.py` and `solver/legal_space.py`**
- **Why dead:** Only imported/called from `solver/solve.py` (`--legal-search` flag). The production pipeline `pipeline/produce.py` does not call them. The legal manifold search was designed to constrain the solver to legal parameter space, but it was never integrated.

**`solver/explorer.py::ParameterExplorer`**
- **Why dead:** Not imported in any production path. Designed for parameter-space exploration/visualization. Standalone analysis utility only.

**`solver/grid_search.py`** (partially alive)
- **Why partially dead:** Used internally by `full_setup_optimizer.py` for seed generation — but ONLY for BMW Sebring. For all other cars, it never runs.

**`solver/scenario_profiles.py::ScenarioProfile`**
- **Why underused:** `SolveChainInputs.scenario_profile` defaults to `"single_lap_safe"`. The scenario profiles modify solver behavior but the production path doesn't expose this to the user in any documented CLI flag.

**`validation/objective_calibration.py`, `validation/observation_mapping.py`, `validation/run_validation.py`**
- **Why analysis-only:** These are calibration scripts used to validate the ObjectiveFunction against real telemetry ground truth. Not part of the runtime pipeline. Good to have for development but should be clearly labeled.

### 7.2 Analysis-Only Modules

**`car_model/calibrate_deflections.py`**
- Standalone calibration script. Reads all IBT files, runs regression, prints new CSV coefficients. It is NOT auto-run. The coefficients must be manually copied into `cars.py`. This creates a workflow where calibration results can be stale without warning.

**`track_model/build_profile.py`**, **`track_model/generic_profiles.py`**
- Track surface profile builders. Appear to feed `track_model/profile.py` but are not called in the production pipeline. `pipeline/produce.py` uses a fallback surface type, not a built track profile.

**`aero_model/parse_xlsx.py`**, **`aero_model/parse_all.py`**
- Parse aero map Excel files into binary cache format. One-time preprocessing tools. Not runtime.

**`research/` directory**
- Research notes, physics markdown files. Not code. Correctly excluded from production.

### 7.3 Partially Wired Code

**`solver/setup_fingerprint.py` + `SolveChainInputs.failed_validation_clusters`**
- **Why partially wired:** The fingerprinting and veto logic is fully implemented in `solve_chain.py`. But `pipeline/produce.py` never populates `failed_validation_clusters`. The anti-regression mechanism is inert.

**`solver/multi_speed_solver.py`**
- **Why partially wired:** Called from `pipeline/reason.py::_analyze_speed_regimes()` for multi-session analysis. But the speed regime analysis results don't clearly feed into the final solve in a controlled way — they are added to `SolverModifiers` but the path is complex and may not always activate.

**`learner/` module**
- **Why partially wired:** `learner/knowledge_store.py` and `learner/recall.py` are used by `solver/learned_corrections.py`, which is called during `objective.py`'s k-NN empirical correction. This path only runs when the learner has accumulated data AND the ObjectiveFunction is active (BMW optimizer only). For all other paths, learning has no effect.

**`solver/stint_model.py`, `solver/stint_reasoner.py`**
- **Why partially wired:** `solver/solve.py --stint` invokes these. `pipeline/produce.py` does NOT call stint analysis. The stint-aware solver is a separate path not integrated into the primary pipeline.

**`solver/sector_compromise.py`**
- **Why partially wired:** Called from `solver/solve.py --sector-compromise`. Not in `pipeline/produce.py`.

**`solver/coupling.py`**
- **Why underused:** Defines coupling between spring parameters. Imported in `corner_spring_solver.py` but the coupling correction may not be applied in all code paths.

### 7.4 Redundant / Overlapping Modules

**`run_full_pipeline.py`, `run_full_v2.py`, `run_full_justified.py`**
- All three call `pipeline/produce.py` with hardcoded paths. Should be deleted; replaced with `python -m ioptimal --car bmw --ibt <path>`.

**`analyzer/__main__.py` vs `__main__.py`**
- Both route to `pipeline/produce.py`. The `analyzer/__main__.py` is an older entry point that should be removed.

**`comparison/` vs `validator/`**
- Both compare IBT sessions and setups. `comparison/` focuses on side-by-side score comparison. `validator/` focuses on setup validation against telemetry. Significant scope overlap. Their reports cover similar ground.

**`solver/solve.py` vs `pipeline/produce.py`**
- `solver/solve.py` is a direct solver CLI that bypasses the IBT analysis pipeline. `pipeline/produce.py` is the full pipeline. They share >90% of the solver call logic. Should be consolidated.

---

## 8. Repo / Runtime Hygiene Issues

### 8.1 Root-Level Clutter

The repository root contains numerous non-source artifacts that make locating the real entrypoint difficult:
- `run_exhaustive.py`, `run_filter.sh`, `run_full_justified.py`, `run_full_pipeline.py`, `run_full_v2.py`, `run_now.py`, `run_tuned_search.py` — **7 ad-hoc runner scripts**, all partially duplicating the main CLI
- `*.sto` files at root: `best.sto`, `cadillac_silverstone.sto`, `idk.sto`, `optimal.sto`, `optimalcaddy.sto`, `optimalnf.sto`, `output.sto`, `reasoned.sto`, `test_phase4.sto`, etc. — **test artifacts committed to source**
- `*.txt` debugging outputs: `exhaustive_output.txt`, `full_justified_output.txt`, `full_output.txt`, `full_pipeline_output.txt`, `full_pipeline_output_v2.txt`, `stdout.txt`, `stderr.txt`, `run_output.txt`, `tuned_output.txt`, `setup_output.txt` — **debug output files committed to source**
- `commit_msg.txt`, `commit_msg2.txt`, `currentjob.md` — **git workflow artifacts committed to source**
- `vertical_dynamics.py` at root — This is a shared physics utility that logically belongs in a shared `utils/` or `physics/` module, not the project root.

### 8.2 Multiple Package Managers / Requirements Files

- `requirements-dev.txt` — Core runtime + test dependencies
- `requirements-desktop.txt` — Superset adding SQLAlchemy, aiosqlite, watchdog, pystray, Pillow
- No `requirements.txt` at root — The authoritative requirements file is ambiguous
- No `pyproject.toml` or `setup.py` — No proper Python package metadata. Cannot `pip install -e .`
- The `Dockerfile` copies only `server/` and `teamdb/` — the Docker image does NOT include the solver pipeline. The Dockerfile is for the team REST API only, but it sits at the root alongside the solver code, creating confusion about the deployable unit.

### 8.3 Generated Artifacts in Source

- `data/aeromaps_parsed/` — Pre-parsed binary aero map cache. Generated by `aero_model/parse_all.py`. Should be in `.gitignore` or `data/` subdir only.
- `tmp_bmw_prefix.bin`, `tmp_vrs_prefix.bin` — Temporary binary files committed to root.
- `_git_result.txt`, `_syntax_result.txt`, `_syntax_result2.txt` — Script output files at root.
- PDFs: `Acura-ARX-06-GTP.pdf`, `Shock-Tuning-User-Guide.pdf` — Large binary reference docs at root.

### 8.4 Unclear Authoritative Runtime

There are **at minimum 4 ways to run the same IBT→.sto pipeline:**
1. `python -m ioptimal --car bmw --ibt session.ibt`  (correct, authoritative)
2. `python __main__.py --car bmw --ibt session.ibt`  (works because `__main__.py` is at root)
3. `python run_full_pipeline.py`  (hardcoded paths — legacy)
4. `python -m pipeline.produce --car bmw --ibt session.ibt`  (older entry point)
5. `python -m analyzer --car bmw --ibt session.ibt`  (even older)
6. `python -m solver.solve --car bmw --track sebring`  (solver-only, no IBT)

**No README makes clear which is canonical.**

### 8.5 .gitignore Inconsistency

`.gitignore` correctly excludes: `__pycache__/`, `.ibt files`, `ibtfiles/`, `data/Ferraridata/`, `*.ld`

**But does NOT exclude:**
- `*.txt` output files (all the debug .txt files at root are tracked)
- `*.sto` files at root (test .sto files are tracked)
- `data/acura_sto/` (appears to be IBT-derived data, should be excluded like other data/)
- `outputs/` and `output/` directories (contain solver outputs, should be excluded)

### 8.6 CLAUDE.md vs Code Discrepancy

**Documented but not implemented:**
- CLAUDE.md references multi-car optimizer support as a goal. In code, only BMW Sebring is implemented.
- CLAUDE.md mentions the `legal manifold search` as a solver option. In `pipeline/produce.py`, this is never activated.
- CLAUDE.md describes the `stint_reasoner` as production. In reality it is only invoked via `solver/solve.py` flag, not `pipeline/produce.py`.

---

## 9. Recommended Module Status Map

### 9.1 Production-Critical (do not break)
- `__main__.py` — Unified CLI router
- `pipeline/produce.py` — Primary pipeline orchestrator
- `pipeline/reason.py` — Multi-session reasoning (active when multiple IBTs provided)
- `solver/solve_chain.py` — Core solver orchestration
- `solver/rake_solver.py` — Step 1: ride height
- `solver/heave_solver.py` — Step 2: heave/third spring
- `solver/corner_spring_solver.py` — Step 3: corner springs
- `solver/arb_solver.py` — Step 4: anti-roll bars
- `solver/wheel_geometry_solver.py` — Step 5: camber/toe
- `solver/damper_solver.py` — Step 6: dampers
- `solver/supporting_solver.py` — Diff, brakes, fuel, wings
- `solver/legality_engine.py` — Post-solve legal validation
- `solver/objective.py` — ObjectiveFunction (BMW optimizer)
- `solver/full_setup_optimizer.py` — BMW Sebring constrained optimizer
- `solver/predictor.py` — Predicted telemetry output
- `solver/decision_trace.py` — Engineering explanation trace
- `solver/modifiers.py` — SolverModifiers dataclass
- `car_model/cars.py` — All car definitions (CRITICAL but BMW-biased)
- `car_model/setup_registry.py` — Canonical field registry and garage snap
- `car_model/garage.py` — GarageOutputModel (BMW Sebring only, but critical for BMW)
- `analyzer/extract.py` — Telemetry extraction from IBT
- `analyzer/diagnose.py` — Handling diagnosis
- `analyzer/driver_style.py` — Driver behavior profiling
- `analyzer/recommend.py` — Physics-based recommendations
- `analyzer/sto_binary.py`, `analyzer/sto_reader.py` — .sto file I/O
- `analyzer/segment.py` — Corner segmentation
- `aero_model/interpolator.py`, `aero_model/gradient.py` — Aero map query
- `vertical_dynamics.py` — Shared physics helpers
- `pipeline/report.py` — Output report generation

### 9.2 Supported Secondary (maintain, document clearly)
- `comparison/` — Setup comparison; useful for tuning validation
- `validator/` — Session-vs-setup validation; partially overlaps comparison/
- `learner/` — Knowledge accumulation; only active for BMW via ObjectiveFunction
- `solver/learned_corrections.py` — Interface to learner store
- `solver/heave_calibration.py` — BMW heave calibration store
- `solver/candidate_ranker.py` — Used in multi-session path
- `solver/setup_fingerprint.py` — Anti-regression mechanism (needs production wiring)
- `solver/bmw_rotation_search.py` — BMW-specific post-solve refinement
- `solver/bmw_coverage.py` — BMW telemetry coverage map
- `car_model/calibrate_deflections.py` — One-time calibration tool (run it, don't ship it)
- `pipeline/preset_compare.py` — Preset comparison utility
- `server/` — Team REST API

### 9.3 Experimental (may contain useful ideas, not production-ready)
- `solver/bayesian_optimizer.py` — Gaussian Process optimizer (unwired)
- `solver/uncertainty.py` — Uncertainty quantification (partially used)
- `solver/sensitivity.py`, `solver/laptime_sensitivity.py` — Sensitivity analysis
- `solver/multi_speed_solver.py` — Multi-speed compromise (partially wired)
- `solver/sector_compromise.py` — Sector compromise (unwired from production)
- `solver/stint_model.py`, `solver/stint_reasoner.py` — Stint-aware solve (unwired from production)
- `track_model/` — Track profile builder (not used in production)
- `solver/corner_strategy.py` — Corner strategy analysis

### 9.4 Legacy / Merge / Deprecate Candidates
- `run_full_pipeline.py`, `run_full_v2.py`, `run_full_justified.py`, `run_now.py`, `run_tuned_search.py` — **DELETE.** All superseded by `python -m ioptimal`.
- `run_exhaustive.py` — **DELETE** or move to `scripts/`.
- `analyzer/__main__.py` — **DELETE** or redirect to `__main__.py`.
- `solver/solve.py` — **DEPRECATE.** Keep for solver-only mode but document clearly.
- `solver/legal_space.py`, `solver/legal_search.py` — **ARCHIVE.** The manifold search concept is valid but not integrated.
- `solver/explorer.py` — **ARCHIVE.** Analysis-only.
- `solver/grid_search.py` — **KEEP** as internal utility for optimizer seed generation.
- All root-level `*.sto` test files — **MOVE** to `tests/fixtures/` and add to .gitignore.
- All root-level `*.txt` debug outputs — **DELETE** and add to .gitignore.

---

## 10. Fix Plan in Priority Order

### Priority 1 (BLOCKING — Ferrari/Acura produce wrong outputs)

**Fix 1.1: Ferrari heave spring index ↔ N/mm calibration**
- **File:** `car_model/cars.py::FERRARI_499P::heave_spring`
- **Action:** Run a 9-point garage screenshot sweep (index 0–8 front, 0–9 rear) with telemetry-validated spring deflections. The current `front_rate_per_index_nmm=20.0` is unvalidated. Replace with a non-linear lookup table (index → N/mm) once real data is available. Interim: treat the rate schedule as physically approximate and add a warning in CLI output.
- **Effort:** 1 IBT session + 1 day calibration

**Fix 1.2: Acura heave spring decode is bypassed**
- **File:** `car_model/cars.py::ACURA_ARX06::heave_spring`
- **Action:** Acura `front_setting_index_range=None` means `front_rate_from_setting()` returns the raw index as N/mm. The solver receives index=180 and treats it as 180 N/mm — but 180 is the actual N/mm, so this accidentally works for some setups. However the encode path `front_setting_from_rate()` also does no conversion — it outputs a physical rate as a garage index. Run a systematic check: is Acura's garage value literally N/mm, or is it an opaque index? The PDF says "90-380 N/mm" for front heave — if the garage control IS in N/mm, set `index_range=None` intentionally and document it.
- **Effort:** 2 hours verification + documentation

**Fix 1.3: Ferrari/Acura/Cadillac/Porsche DeflectionModel coefficients**
- **File:** `car_model/cars.py` — Ferrari, Acura, Cadillac, Porsche all use `DeflectionModel()` default (BMW Sebring regression)
- **Action:** Either (a) add explicit `DeflectionModel(...)` with zeroed coefficients and warnings that deflection validation is disabled for these cars, OR (b) run garage screenshot sweeps for each car to calibrate their own coefficients. Minimum: add a `is_calibrated` property check in `_finalize_result()` and warn when outputting .sto for uncalibrated cars.
- **Effort:** Per-car: 1 day data collection + 1 day calibration

**Fix 1.4: Ferrari/Acura GarageOutputModel validation**
- **File:** `car_model/cars.py`, `solver/solve_chain.py::_finalize_result()`
- **Action:** The `.sto` output for Ferrari/Acura skips all deflection limit checks. After building DeflectionModel for these cars, add `garage_output_model` instances (even without full regression — just range validators). At minimum, add a `WARN: DeflectionModel not calibrated for {car}` in the CLI output so the user knows the .sto may not load in-game.
- **Effort:** Medium

### Priority 2 (HIGH — Calibration accuracy)

**Fix 2.1: Calibrate m_eff per car from IBT telemetry**
- **Files:** `car_model/cars.py`, `solver/heave_calibration.py`
- **Action:** For each non-BMW car, run the same calibration procedure used for BMW:
  - Vary heave spring rate across 3-5 settings in actual iRacing sessions
  - Extract `shock_vel_p99` and `sigma_mm` from IBT
  - Solve: `m_eff = k_nmm * (sigma_mm / shock_vel_p99)^2`
  - Update `heave_spring.front_m_eff_kg` and `rear_m_eff_kg` per car
- For Acura: account for nonlinear m_eff (document rate-dependent lookup)
- **Effort:** 1-2 practice sessions per car

**Fix 2.2: ARB solver LLTD target vs ObjectiveFunction target mismatch (BMW)**
- **Files:** `solver/arb_solver.py`, `solver/objective.py`, `car_model/cars.py`
- **Problem:** ARB solver targets ~0.528 (theoretical). ObjectiveFunction scores against 0.41 (measured). For non-optimizer paths (BMW non-Sebring, all other cars), the ARB solve uses the wrong LLTD target.
- **Action:** Pass `car.measured_lltd_target` into `ARBSolver.solve()` when it is not None. Use it as the primary LLTD target instead of the theoretical formula. For cars where `measured_lltd_target` is None, use the theoretical formula.
- **Effort:** 1-2 hours

**Fix 2.3: Aero compression model → per-setup calibration**
- **Files:** `car_model/cars.py`, `aero_model/`
- **Problem:** Single compression constant per car ignores ride height dependency.
- **Action:** Fit a 2D compression model: `front_compression = f(front_rh_target, rear_rh_target)` from aero map query outputs at multiple ride height combinations. The aero map already exists — just need to query it at multiple points and build a bilinear interpolation for compression.
- **Effort:** Medium (no new sessions needed, pure computation)

**Fix 2.4: Wire failed_validation_clusters into produce.py**
- **Files:** `pipeline/produce.py`, `solver/solve_chain.py`
- **Action:** Add logic in `produce.py` to load known-bad setup fingerprints from the learner knowledge store and pass them into `SolveChainInputs.failed_validation_clusters`. This activates the fingerprint veto anti-regression mechanism that is already fully implemented but never activated.
- **Effort:** 1-2 hours

### Priority 3 (MEDIUM — Generalize solver beyond BMW)

**Fix 3.1: Extract optimizer into a generalized constrained optimizer**
- **Files:** `solver/full_setup_optimizer.py`
- **Problem:** `BMWSebringOptimizer` guards on `car.canonical_name == "bmw"`.
- **Action:** Rename to `GTPConstrainedOptimizer`. Replace the BMW-specific seed generation with a per-car calibration dataset loader (each car has its own .json calibration seeds). The 6-step solver chain inside is already car-agnostic — only the seed generation is BMW-specific.
- **Effort:** Large (1-2 weeks including data collection for other cars)

**Fix 3.2: BMW rotation search → generalized rotation tuning**
- **Files:** `solver/bmw_rotation_search.py`
- **Problem:** Guards on `car.canonical_name == "bmw"`.
- **Action:** The rotation scoring logic (diff preload, rear ARB, rear spring vs yaw/lateral telemetry) is general. Apply to all cars once their rotation characteristics are understood. At minimum, document and activate for Cadillac (same Dallara platform).

**Fix 3.3: Add per-car LLTD measured targets from IBT**
- **Files:** `car_model/cars.py`
- **Action:** Run sessions for Ferrari/Acura/Cadillac with verified setups, extract LLTD from IBT using the same methodology as BMW (rear tyre load transfer from lateral G + corner weights). Update `measured_lltd_target` per car.
- **Effort:** 1 IBT session per car with good data quality

### Priority 4 (LOW — Housekeeping / CLI simplification)

**Fix 4.1: Delete root-level legacy files**
- `run_full_pipeline.py`, `run_full_v2.py`, `run_full_justified.py`, `run_now.py`, `run_tuned_search.py`, `run_exhaustive.py`
- All `*.txt` debug output files
- All `*.sto` test artifact files at root (move to `tests/fixtures/`)

**Fix 4.2: Move `vertical_dynamics.py` to `physics/vertical_dynamics.py`**
- Update all imports in `solver/`, `car_model/`, `analyzer/`

**Fix 4.3: Add `pyproject.toml`**
- Define the package properly so `pip install -e .` works
- Make `python -m ioptimal` the official entrypoint
- Removes confusion about which runner script is correct

**Fix 4.4: Update `.gitignore`**
```
*.txt              # exclude debug output files
outputs/           # exclude solver output directories
output/
*.sto              # exclude .sto test artifacts (except tests/fixtures/)
tmp_*.bin          # exclude temp binaries
data/aeromaps_parsed/  # generated cache
```

**Fix 4.5: Add CLI_GUIDE.md (see companion file)**
- Single-page command reference for all valid entry points

---

## 11. Appendices

### 11.A Key File / Function Reference

| Topic | File | Function |
|-------|------|----------|
| Production entry point | `__main__.py` | `main()` |
| Full pipeline orchestrator | `pipeline/produce.py` | `produce(args)` |
| Multi-session reasoning | `pipeline/reason.py` | `reason_and_solve()` |
| Solver chain orchestration | `solver/solve_chain.py` | `run_base_solve(inputs)` |
| Constrained optimizer (BMW only) | `solver/full_setup_optimizer.py` | `BMWSebringOptimizer._run()` |
| Optimizer gate | `solver/full_setup_optimizer.py` | `optimize_if_supported()` |
| Objective/scoring function | `solver/objective.py` | `ObjectiveFunction.evaluate(candidate)` |
| Sequential solver | `solver/solve_chain.py` | `_run_sequential_solver(inputs)` |
| Step 1: Rake/RH | `solver/rake_solver.py` | `RakeSolver.solve()` |
| Step 2: Heave spring | `solver/heave_solver.py` | `HeaveSolver.solve()` |
| Step 3: Corner spring | `solver/corner_spring_solver.py` | `CornerSpringSolver.solve()` |
| Step 4: ARB | `solver/arb_solver.py` | `ARBSolver.solve()` |
| Step 5: Wheel geometry | `solver/wheel_geometry_solver.py` | `WheelGeometrySolver.solve()` |
| Step 6: Dampers | `solver/damper_solver.py` | `DamperSolver.solve()` |
| Supporting params (diff, brakes) | `solver/supporting_solver.py` | `SupportingSolver.solve()` |
| BMW post-solve rotation refinement | `solver/bmw_rotation_search.py` | `search_rotation_controls()` |
| Legality check | `solver/legality_engine.py` | `validate_solution_legality()` |
| Predicted telemetry output | `solver/predictor.py` | `predict_candidate_telemetry()` |
| Decision trace / explanation | `solver/decision_trace.py` | `build_parameter_decisions()` |
| Telemetry extraction | `analyzer/extract.py` | `extract_measurements(ibt_path)` |
| Diagnosis engine | `analyzer/diagnose.py` | `diagnose(measured, car, track)` |
| Driver profiling | `analyzer/driver_style.py` | `analyze_driver(measured, corners)` |
| All car definitions | `car_model/cars.py` | `get_car(name)` |
| Deflection model | `car_model/cars.py` | `DeflectionModel` (BMW calibrated) |
| Ride height model | `car_model/cars.py` | `RideHeightModel` (BMW calibrated) |
| Effective heave mass | `car_model/cars.py` | `HeaveSpringModel.front_m_eff_kg` |
| Ferrari indexed decode | `car_model/cars.py` | `HeaveSpringModel.front_rate_from_setting()` |
| Garage output validation (BMW) | `car_model/garage.py` | `GarageOutputModel` |
| Calibration script | `car_model/calibrate_deflections.py` | `(run as script)` |
| .sto binary write | `analyzer/sto_binary.py` | `write_sto()` |
| Aero map query | `aero_model/interpolator.py` | `AeroSurface.query()` |
| Physics helpers | `vertical_dynamics.py` | `damped_excursion_mm()` |

### 11.B Questions / Uncertainties — Updated with ferrari.json ground truth

**✅ RESOLVED:** Ferrari torsion bar turns ARE a real garage control (confirmed from `ferrari.json`).
- LF/RF Torsion bar turns: `0.100 Turns`, range `−0.250 to +0.250 Turns`
- LR/RR Torsion bar turns: `0.048 Turns`, range `−0.250 to +0.250 Turns`
- The solver does NOT model these. They affect preload and ride height at all 4 corners. BMW's `GarageOutputModel` has `torsion_turns_intercept` etc. — Ferrari needs the same and the solver needs to output torsion bar turns values. **Currently the solver outputs no torsion bar turns field for Ferrari — this means ride heights in generated Ferrari .sto files will be wrong because preload contribution is ignored.**

**✅ RESOLVED:** Ferrari heave spring indices confirmed.
- Front: index `5`, Rear: index `8` — no physical units shown in garage, confirms indexed control
- From `ferrari.json` internal fields: `fSideSpringRateNpm = 115170.265625 N/m = 115.17 N/mm` for front corner spring at Torsion bar OD index 2. `rSideSpringRateNpm = 105000 N/m = 105.0 N/mm` for rear at OD index 1. These are corner spring physics values (not heave spring).

**✅ RESOLVED:** Ferrari has **separate heave dampers AND corner dampers** — two distinct damper subsystems.
- Garage "Dampers" tab shows per-corner dampers (LF/RF/LR/RR): 0-40 clicks for LS/HS comp and rbd
- Internal fields `hfLowSpeedCompDampSetting=10`, `hfHighSpeedCompDampSetting=40`, `hrLowSpeedCompDampSetting=10`, `hrHighSpeedCompDampSetting=40` — these are HEAVE FRONT/REAR internal damper settings, separate from the corner dampers.
- **The DamperSolver likely writes to the wrong damper subsystem for Ferrari.** It computes corner damper clicks but Ferrari exposes both heave dampers (hf/hr) AND per-corner dampers (lf/rf/lr/rr).

1. **Acura roll damper solve path:** `DamperSolver.solve()` appears to handle regular 4-corner dampers. But Acura has `has_roll_dampers=True` and separate `FrontRoll`/`RearRoll` LS/HS settings. Is `DamperSolver` producing correct output for Acura's heave + roll architecture, or is it outputting 4-corner settings that don't map to the Acura garage structure?

2. **Aero map coordinate swap:** The docstring in `car_model/cars.py` states "front_rh axis = REAR ride height, rear_rh axis = FRONT ride height" — for ALL cars. But if some car's aero map is formatted in the non-swapped convention, `aero_axes_swapped=True` on that car would silently corrupt aero predictions. Needs verification per car.

3. **Ferrari rear heave perch = −103.5mm (code) vs −104.0mm (ferrari.json):** Near-exact match confirms the negative perch is correct physics. `lrPerchOffsetm = −0.032112m = −32.1mm` is the internal raw offset; the `−104.0mm` garage value is the user-visible control, and the code's `−103.5mm` baseline is accurate within 0.5mm.

4. **BMW `measured_lltd_target=0.41` source:** The code cites "46 BMW Sebring sessions, 2026" and "objective_validation.md Section 6". The file `validation/objective_calibration.py` exists but `validation/objective_validation.md` is not present. Was this calibration validated against round-trip (solver → simulation → telemetry), or is it just a statistical mean of observed setups?

5. **Cadillac rear compression 18.5mm vs BMW 9.5mm:** Nearly 2x difference. This suggests the Cadillac's rear suspension geometry (and hence spring setup) is fundamentally different from BMW's, even on the "same Dallara platform." The note says it's "calibrated from 2 sessions" — is this stable with more data?

6. **Ferrari brake bias:** Code calibrates `brake_bias_pct=54.0%` but this `ferrari.json` setup shows `49.00%` with range `42–65%`. The 54% is plausible for a different track/conditions. The code's range `GarageRanges.brake_bias_target=(-5.0, 5.0)` models only the offset from base bias, not the absolute bias control visible here. The garage field `Brake pressure bias` (42–65%) is a separate, absolute control.

7. **Ferrari front diff can go negative:** ferrari.json shows `Front Diff Preload` range `−50 to +50 Nm`. The code's `diff_preload_nm=(0.0, 150.0)` range in `GarageRanges` is wrong for Ferrari front diff (it clamps at 0 when negatives are valid).

### 11.C Contradictions Between Docs and Code

| Claimed in Docs | Observed in Code | Severity |
|-----------------|-----------------|----------|
| CLAUDE.md: multi-car optimizer | `optimize_if_supported()` guards on BMW+Sebring only | HIGH |
| USAGE.md: "python -m ioptimal" is the entry point | `analyzer/__main__.py`, `run_full_pipeline.py`, etc. all claim to be entry points | MEDIUM |
| CLAUDE.md: stint analysis is production | `stint_model.py/stint_reasoner.py` only called via `solver/solve.py --stint` | MEDIUM |
| `car_model/cars.py` BMW docstring: "m_eff calibrated from telemetry" | Comments acknowledge Session 1 vs Session 2 give different values (166.8 vs 228.0 kg) — 37% range | LOW |
| `car_model/cars.py` BMW: "MR_rear=0.60 is consistent with highly leveraged pushrod" | Ferrari MR_rear=0.612 is also described as "CALIBRATED from LLTD back-solve" — the values are suspiciously similar despite different chassis platforms | LOW |
| `solver/full_setup_optimizer.py` comment: "BMW Sebring optimizer" | `optimize_if_supported` docstring says "returns None for unsupported car/track combos" — implying future support for other combos — but the implementation only adds BMW/Sebring as supported | LOW |

### 11.D Critical Code Snippets

**The optimizer gate (the single point that determines if optimization happens):**
```python
# solver/full_setup_optimizer.py::optimize_if_supported()
def optimize_if_supported(car, surface, track, ...) -> OptimizedResult | None:
    if car.canonical_name == "bmw":          # ← ONLY BMW
        optimizer = BMWSebringOptimizer(...)
        return optimizer.run()
    return None                              # ← All other cars: no optimization
```

**Why Ferrari heave spring decode may produce garbage:**
```python
# car_model/cars.py::HeaveSpringModel.front_rate_from_setting()
def front_rate_from_setting(self, setting_value: float) -> float:
    if (
        self.front_setting_index_range is None        # ← Acura: returns setting_value unchanged
        or self.front_setting_anchor_index is None
        or self.front_rate_at_anchor_nmm is None
        or self.front_rate_per_index_nmm is None
    ):
        return float(setting_value)                  # ← For Acura: returns index as N/mm!
    return float(
        self.front_rate_at_anchor_nmm
        + (float(setting_value) - self.front_setting_anchor_index) * self.front_rate_per_index_nmm
    )
    # Ferrari: anchor_index=1, rate_at_anchor=50, per_index=20.0 (ESTIMATE)
    # index 0 → 50 + (0-1)*20 = 30 N/mm
    # index 8 → 50 + (8-1)*20 = 190 N/mm
    # Linear: UNVALIDATED
```

**Why DeflectionModel is broken for non-BMW cars:**
```python
# car_model/cars.py::FERRARI_499P (line ~850 in the file)
# Ferrari does NOT override DeflectionModel → uses default BMW coefficients:
deflection=DeflectionModel()  # ← Default! All values are BMW Sebring regression fits.

# The DeflectionModel defaults:
heave_defl_intercept: float = -20.756     # BMW calibrated
heave_defl_inv_heave_coeff: float = 7.030 # BMW calibrated
rear_spring_eff_load: float = 6091.76     # BMW calibrated
# These will give nonsensical values on Ferrari's indexed suspension
```

**The LLTD conflict:**
```python
# solver/arb_solver.py::ARBSolver.solve() — targets theoretical LLTD
lltd_target = car.weight_dist_front + (car.tyre_load_sensitivity / 0.20) * 0.05
# BMW: 0.4727 + (0.22/0.20)*0.05 = 0.528

# solver/objective.py::ObjectiveFunction — scores against measured LLTD
lltd_target = car.measured_lltd_target or (car.weight_dist_front + ...)
# BMW: car.measured_lltd_target = 0.41  ← Different!

# For ALL non-optimizer paths (non-BMW, non-Sebring), solver targets 0.528
# but the measured reality for BMW is 0.41. Non-BMW cars have no measured target.
```

---

## Handoff Summary for Follow-Up Engineering Model

**What this codebase is:** A physics-based iRacing GTP setup solver. The architecture is clean: IBT → extract → diagnose → 6-step sequential solver → .sto output. The physics models are carefully documented with real formulas and good calibration practices.

**What works well:** The entire BMW Sebring pipeline is production-quality. The constrained optimizer (SLSQP), objective function (multi-component scoring), heave calibration store, GarageOutputModel regression, and rotation search all work together and are well-calibrated.

**The core problem:** The codebase treats BMW Sebring as the reference implementation, and all other cars/tracks are approximations using BMW parameters as defaults. This is not a design flaw — it's an expected state of incomplete calibration. The code is structured to allow per-car calibration (all the model classes exist, all the coefficient fields exist, all the `ESTIMATE` flags are honest). The problem is execution: no one has run the calibration sessions for Ferrari, Acura, Porsche.

**The specific broken things for Ferrari:**
1. `DeflectionModel()` — BMW coefficients, needs Ferrari sweep data
2. `heave_spring.front_rate_per_index_nmm=20.0` — unvalidated linear estimate
3. `heave_spring.perch_offset_rear_baseline_mm=-103.5` — negative perch unique to Ferrari; may cause sign errors in code paths that assume positive perch
4. ARB labels (Disconnected/A/B/C/D/E) — confirmed in garage_ranges but ARBModel stiffness values are ESTIMATES
5. Damper click range (0-40) vs BMW (0-11) — `DamperSolver` generates clicks via formula; does it clamp to the correct range for Ferrari?

**The specific broken things for Acura:**
1. `has_roll_dampers=True` but `DamperSolver.solve()` may not handle this split architecture
2. Front torsion bar always "bottomed" per code comments — the solver should expect/accept this
3. `m_eff` is rate-dependent (319-641 kg range) but solver uses single constant
4. Rear uses torsion bars (not coil springs) — `rear_is_torsion_bar=True` is set but codepaths must be verified to use `rear_torsion_bar_rate()` instead of `snap_rear_rate()`

**Where to start:**
1. Run `python -m ioptimal --car ferrari --ibt <ferrari_session.ibt> --wing 14` and compare output .sto with a known-good human setup from `Acura Setups/` directory.
2. Diff the deflection values — if they are wildly wrong (they will be), the DeflectionModel issue is confirmed.
3. Run `car_model/calibrate_deflections.py --car ferrari` with 5-10 garage screenshot data points to generate Ferrari-specific coefficients.
4. Fix the index decode issue for heave springs (Ferrari validation sweep).
5. Verify Acura roll damper path in `DamperSolver`.

**Tools/commands for development:**
- `python -m ioptimal --car bmw --ibt data/telemetry/session.ibt --wing 16` (working reference)
- `python -m ioptimal --car ferrari --ibt data/telemetry/ferrari_session.ibt --wing 14`
- `python -m validator --car ferrari --ibt session.ibt --setup setup.sto`  (compare against known good)
- `python car_model/calibrate_deflections.py` (calibration workflow)

---

### 11.E Ferrari 499P Garage Schema Analysis (from ferrari.json)

This appendix documents verified Ferrari garage fields extracted from `ferrari.json` — the actual iRacing setup schema. These are ground truth values for the Ferrari 499P and several of them reveal critical bugs or missing models in the solver.

#### Spring Controls (Confirmed Indexed)

| Garage Field | Label | Value | Section | Range | Code Notes |
|---|---|---|---|---|---|
| Front Heave spring | `Heave spring` | Index `5` | Front | null (no range exposed) | Code: `front_setting_anchor=1, rate_at_anchor=50, per_index=20.0 N/mm` (ESTIMATE). Index 5 → estimated 130 N/mm. **Unvalidated.** |
| Rear Heave spring | `Heave spring` | Index `8` | Rear | null | Code: `rear_setting_anchor=2, rate_at_anchor=530, per_index=60.0 N/mm` (ESTIMATE). Index 8 → estimated 890 N/mm. **Very stiff — plausibility check needed.** |
| LF Torsion bar O.D. | `Torsion bar O.D.` | Index `2` | Left Front | null | Code: linear decode, index 2 → OD ~20.22mm → wheel rate ~115 N/mm. Internal field `fSideSpringRateNpm=115170 N/m = 115.17 N/mm` ✅ confirms corner spring physics. |
| LR Torsion bar O.D. | `Torsion bar O.D.` | Index `1` | Left Rear | null | Code: linear decode, index 1 → OD ~23.1mm. Internal `rSideSpringRateNpm=105000 N/m = 105.0 N/mm` ✅ confirms rear corner spring physics. |

#### Torsion Bar Turns — **Ferrari-Unique Control, Completely Missing from Solver**

| Garage Field | Value | Range | Notes |
|---|---|---|---|
| LF Torsion bar turns | `0.100 Turns` | `−0.250 to +0.250` | **CRITICAL: solver never outputs this field** |
| RF Torsion bar turns | `0.100 Turns` | `−0.250 to +0.250` | Front turns directly affect front static RH |
| LR Torsion bar turns | `0.048 Turns` | `−0.250 to +0.250` | Rear turns affect rear static RH |
| RR Torsion bar turns | `0.048 Turns` | `−0.250 to +0.250` | Without this field, ride heights in generated .sto are wrong |

**Impact:** The Ferrari torsion bar corner springs use both OD (stiffness) AND turns (preload/ride height). When the solver writes a Ferrari setup without specifying torsion bar turns, iRacing will load the .sto with default turns values = the car sits at whatever height the default turns produce, not the solver's intended ride height. This is why Ferrari ride heights are always wrong in generated setups.

**Fix required in `car_model/cars.py`:** Add `torsion_bar_turns_range = (-0.250, 0.250)` to Ferrari `GarageRanges`. Add torsion bar turns prediction to Ferrari's `GarageOutputModel` (equivalent to BMW's `torsion_turns_intercept` model). Update `solver/supporting_solver.py` or `solver/heave_solver.py` to solve for and output torsion bar turns for Ferrari.

#### Pushrod Values (Confirmed from ferrari.json)

| Field | Value | Range | Code vs JSON |
|---|---|---|---|
| Front Pushrod length delta | `+2.0 mm` | `−40.0 to +40.0 mm` | Code `front_pushrod_default_mm = −3.0` (CALIBRATED from IBT showing −3.0mm). This JSON shows +2.0mm → different setup, confirms positive pushrod values are legal for Ferrari. |
| Rear Pushrod length delta | `+18.0 mm` | `−40.0 to +40.0 mm` | Code uses positive rear pushrods for Ferrari. Rear perch calibration: 18mm pushrod → ~50.6mm RH with code model. |

#### Heave Perch (Confirmed Negative Values)

| Field | Value | Range | Code Notes |
|---|---|---|---|
| Front Heave perch offset | `−6.5 mm` | `−150.0 to +100.0 mm` | Code `front_heave_perch_range` not explicitly set for Ferrari (uses default). −6.5mm is within valid range. |
| Rear Heave perch offset | `−104.0 mm` | `−150.0 to +100.0 mm` | ✅ Matches code `perch_offset_rear_baseline_mm=−103.5` within 0.5mm. Negative perch confirmed correct. |
| `lrPerchOffsetm` (internal) | `−0.032112 m = −32.1 mm` | — | This is the iRacing physics raw offset (in meters), different coordinate system from the garage control. The −104mm garage value ≠ −32mm physics value — they are different reference frames. The solver must use garage values, not physics raw offsets. |

#### Damper Architecture — **Two Separate Subsystems**

The ferrari.json reveals that Ferrari has TWO completely separate damper subsystems:

**Subsystem 1: Per-corner dampers (Dampers tab, visible in garage)**
- LF/RF `LS comp`: `0 clicks`, range `0–40`
- LF/RF `HS comp`: `0 clicks`, range `0–40`
- LF/RF `HS comp slope`: `7 clicks`, range `0–11`
- LF/RF `LS rbd`: `0 clicks`, range `0–40`
- LF/RF `HS rbd`: `0 clicks`, range `0–40`
- LR/RR `LS comp`: `40 clicks`, range `0–40`
- LR/RR `HS comp`: `40 clicks`, range `0–40`
- LR/RR `HS comp slope`: `10 clicks`, range `0–11`
- LR/RR `LS rbd`: `35 clicks`, range `0–40`
- LR/RR `HS rbd`: `0 clicks`, range `0–40`

**Subsystem 2: Heave dampers (internal fields, NOT visible in garage)**
- `hfLowSpeedCompDampSetting = 10` (Heave Front LS comp — internal 0–40 range)
- `hfHighSpeedCompDampSetting = 40` (Heave Front HS comp)
- `hfHSSlopeCompDampSetting = 5` (Heave Front HS slope — 0–11 range)
- `hfLowSpeedRbdDampSetting = 10` (Heave Front LS rbd)
- `hfHighSpeedRbdDampSetting = 40` (Heave Front HS rbd)
- `hrLowSpeedCompDampSetting = 10` (Heave Rear equivalents)
- `hrHighSpeedCompDampSetting = 40`
- `hfHSSlopeRbdDampSetting = 5`, `hrHSSlopeRbdDampSetting = 5` ← **HS Rebound slope also exists!**

**Critical observations:**
1. Front corner dampers show `0 clicks` for LS/HS comp and LS/HS rbd — but heave front shows 10/40/10/40 internal values. The two subsystems are decoupled.
2. Rear corner dampers show near-max values (40/40/35) while heave rear is 10/40/10/40. These serve different physics roles (corner: transient handling; heave: pitch/platform control).
3. **The `DamperSolver` in the code computes one set of damper values and writes it to per-corner position. It does not distinguish between corner and heave damper subsystems.** The heave damper values (`hf*`, `hr*`) appear to need separate computation — likely tied to heave spring rate and pitch dynamics, NOT to per-corner wheel rate.
4. **HS Rebound slope** (`lfHSSlopeRbdDampSetting`, etc.) exists at all corners. The code models `hs_slope` only for compression — the rebound slope is an unmapped field on Ferrari.

#### ARB (Confirmed Labels)

| Field | Value | Notes |
|---|---|---|
| Front ARB size | `B` | Confirms A-E label scale. Code has `["Disconnected", "A", "B", "C", "D", "E"]` ✅ |
| Front ARB blades | `1` | Range 1–5 ✅ |
| Rear ARB size | `C` | ✅ Consistent with code |
| Rear ARB blades | `1` | Range 1–5 ✅ |

#### Brake System (Confirmed)

| Field | Value | Range | Code vs JSON |
|---|---|---|---|
| Brake pressure bias | `49.00%` | `42.00%–65.00%` | Code calibrates `brake_bias_pct=54.0%`. This JSON shows `49%` → different setup/track. The wide range (42–65%) is notable — much wider than BMW. |
| Front master cyl. | `17.8 mm` | `16.8–20.6 mm` | Code `brake_master_cyl_options_mm = [15.9, 16.8, 17.8, 19.1, 20.6, 22.2, 23.8]` — this JSON confirms 17.8mm is a valid option. |
| Rear master cyl. | `19.1 mm` | `16.8–20.6 mm` | ✅ Range confirmed |
| Bias migration | `1%` | `1%–6%` | **Code models `brake_bias_migration = (-6.0, 6.0)` (gain range). But this JSON shows migration TYPE is 1–6 integer selector, PLUS a separate `Bias migration gain` = `0.00%` range `−4.00% to +4.00%`. These are two distinct controls. Code only models the gain, not the type selector.** |

#### Differential (Confirmed)

| Field | Value | Range | Code vs JSON |
|---|---|---|---|
| Rear Diff Preload | `20 Nm` | `0–150 Nm` | Code `diff_preload_nm=(0.0, 150.0)` ✅ correct for rear diff |
| **Front Diff Preload** | `5 Nm` | **`−50 to +50 Nm`** | **CRITICAL: Code `diff_preload_nm=(0.0, 150.0)` is WRONG for Ferrari front diff. Negative preload is legal (range allows −50 Nm). Front diff is Ferrari-unique (not on BMW/Cadillac).** |
| Coast/drive ramp | `More Locking` | text options | Code models coast/drive ramp as numeric pairs (40/65, 45/70, 50/75). Ferrari uses text labels (`More Locking`, `Less Locking`, etc.) — different schema. |

#### Geometry (Observed from ferrari.json)

- Camber shows `LF=+0.7°, RF=−0.7°` in the JSON. By iRacing's sign convention, both fronts are 0.7° inward lean (negative camber physically). This is much less than BMW's typical −2.9°. The Ferrari setup in this JSON uses mild camber — fine, it's within range.
- Toe: `LF/RF Toe-in = 0.0004`, `LR/RR Toe-in = 0.0005` (very small, near zero). These are `is_derived=true` fields, meaning they are computed from other inputs, not set directly.

#### Gear Stack (Unmapped in Solver)

- `Gear stack: Short` (options: Short/Tall) — Ferrari-specific feature. Not modeled by `SupportingSolver`. The solver should output a gear stack recommendation based on typical track speed. Currently neither `supporting_solver.py` nor `solver/solve.py` handles this field.

#### Physics Internal Fields Summary

| Internal Field | Value | What It Tells Us |
|---|---|---|
| `fSideSpringRateNpm` | `115170.27 N/m = 115.17 N/mm` | **Actual iRacing physics rate for front corner spring at OD index 2.** Use this to validate the decode formula: `C * OD^4 = 115 N/mm`. With C=0.001282 → OD = (115/0.001282)^0.25 = 20.22mm. ✅ Linear decode index 2 → ~20.22mm is consistent. |
| `rSideSpringRateNpm` | `105000 N/m = 105.0 N/mm` | **Actual iRacing physics rate for rear corner spring at OD index 1.** OD = (105/0.001282)^0.25 = 19.92mm ≈ 20.0mm (OD range lo = 23.1mm for rear → discrepancy! At OD 23.1mm: 0.001282*23.1^4 = 364 N/mm, but JSON says 105 N/mm). **This suggests the decode formula for rear torsion is not `C*OD^4` with C=0.001282 and OD range 23.1–26mm. Likely either C is different for rear, or the rear index→OD mapping is wrong.** |
| `hfHighSpeedCompDampSetting` | `40` | Heave front HS comp internal value (0–40 range). Max setting suggests heave front HS comp is fully locked — not a normal setup choice unless the dampers have separate physical scales. |
| `hfHighSpeedRbdDampSetting` | `40` | Heave front HS rbd also at max. The code's `DamperModel.front_hs_comp_baseline=15` for Ferrari is inconsistent with this. |

**Critical flag on rear spring rate:** `rSideSpringRateNpm = 105 N/mm` at OD index 1 is NOT consistent with `C=0.001282` and OD range 23.1–26mm. At OD=23.1mm: k = 0.001282 × 23.1^4 = 361 N/mm. But JSON shows 105 N/mm. This means either:
1. The rear C constant is much smaller than 0.001282, OR
2. The rear spring physics is NOT torsion bar — it may be a coil spring expressed as index, OR
3. The front and rear use different C constants (the code comment saying "SAME as front!" may be wrong for the rear decode formula)

This is a new high-severity finding: **`rSideSpringRateNpm=105 N/mm` at OD index 1 directly contradicts the code's rear torsion bar model (`C=0.001282`, OD range 23.1–26mm).** The actual rear spring rate per index appears to be 60–70 N/mm range (not 364–590 N/mm as the code model predicts). The rear spring in `car_model/cars.py` (`rear_spring_range_nmm=(364.0, 590.0)`) is approximately **3.5× too high**.

---

*End of audit report. Total source files analyzed: ~120 Python files across 15 modules. ferrari.json garage schema analyzed for ground truth verification.*
