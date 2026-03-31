# Solver Directory Deep Audit Report

**Date:** 2026-03-31  
**Scope:** All ~35 Python files in `/workspace/solver/`  
**Auditor:** Automated deep read of every file, function, and branch

---

## Table of Contents

1. [Orchestration Flow](#1-orchestration-flow)
2. [Physics Sub-Solvers (Steps 1–6)](#2-physics-sub-solvers-steps-16)
3. [Scoring & Objective System](#3-scoring--objective-system)
4. [Legal Search System](#4-legal-search-system)
5. [Scenario Profiles](#5-scenario-profiles)
6. [Optimizers (Bayesian, Grid, Full Setup)](#6-optimizers)
7. [Supporting Solver](#7-supporting-solver)
8. [Modifier System](#8-modifier-system)
9. [Decision Trace](#9-decision-trace)
10. [Experimental / Research Modules](#10-experimental--research-modules)
11. [Utility & Analysis Modules](#11-utility--analysis-modules)
12. [Car-Specific Logic Branching](#12-car-specific-logic-branching)
13. [TODO / FIXME / HACK Inventory](#13-todo--fixme--hack-inventory)
14. [Fallback & Default Value Risks](#14-fallback--default-value-risks)
15. [Production Path Map](#15-production-path-map)

---

## 1. Orchestration Flow

### `solver/__init__.py`
- **Production path:** Yes — package entry point
- **Purpose:** Lazy-import dispatcher. Exposes `RakeSolver`, `HeaveSolver`, etc. via `__getattr__` to avoid eagerly loading heavy deps (numpy, scipy).
- **BMW-specific:** No
- **Car-specific params:** None
- **TODOs:** None
- **Fallbacks:** `importlib.import_module` with dynamic `__getattr__`

### `solver/solve.py`
- **Production path:** Yes — main CLI entry point (`python -m solver.solve`)
- **Purpose:** Parses CLI args, loads `CarModel` + `AeroSurface` + `TrackProfile`, resolves scenario profile, runs the 6-step solver chain (or BMW optimizer), applies learned corrections, runs optional post-analyses (sensitivity, stint, sector, legal search, bayesian, explorer, multi-speed).
- **Key functions:**
  - `solve.py::main()` — CLI argument parsing
  - `solve.py::run_solver(args)` — full orchestration
- **BMW-specific:** Calls `optimize_if_supported()` which is BMW/Sebring-only. All other cars fall through to sequential solver.
- **Car-specific params:** `car.default_df_balance_pct`, `load_car_surfaces(car.canonical_name)`
- **TODOs:** Line 272 comment: "aero_compression overrides intentionally omitted"
- **Fallbacks:** Generic track profile if file not found. Default fuel=89L, default balance from car model. Broad `try-except Exception` blocks around optional analyses.

### `solver/solve_chain.py`
- **Production path:** Yes — core internal API called by `solve.py`, `pipeline/produce.py`, and legal search
- **Purpose:** Defines `SolveChainInputs`, `SolveChainOverrides`, `SolveChainResult`. Provides `run_base_solve()` (orchestrates the 6-step chain or BMW optimizer), `materialize_overrides()` (applies parameter overrides to create new candidates), and `_run_sequential_solver()` (the actual step-by-step execution).
- **Key functions:**
  - `solve_chain.py::run_base_solve(inputs)` — dispatches to optimizer or sequential chain
  - `solve_chain.py::_run_sequential_solver(inputs, mods)` — runs Steps 1→6 in order
  - `solve_chain.py::materialize_overrides(base, overrides, inputs)` — creates candidate variants
  - `solve_chain.py::_finalize_result(...)` — validates legality, builds decisions, predicts telemetry
- **BMW-specific:** Calls `bmw_rotation_search.search_rotation_controls()` for BMW/Sebring
- **Car-specific params:**
  - `car.corner_spring.rear_motion_ratio` — rear spring → wheel rate conversion
  - `car.canonical_name == "ferrari"` — for `diff_ramp_string_for_option` formatting
  - `car.geometry.front_camber_baseline_deg` — fallback camber
  - `car.garage_ranges.diff_coast_drive_ramp_options` — diff ramp enforcement
- **TODOs:** None
- **Fallbacks:** `_default_modifiers()` provides zero-offset modifiers if none computed

---

## 2. Physics Sub-Solvers (Steps 1–6)

### Step 1: `solver/rake_solver.py` — Rake / Ride Heights
- **Production path:** Yes
- **Computes:** Dynamic front/rear ride heights, rake, pushrod offsets, DF balance, L/D, vortex burst margin
- **Inputs:** `CarModel`, `AeroSurface`, `TrackProfile`, `target_balance`, `fuel_load_l`
- **Outputs:** `RakeSolution` — dynamic/static RH, pushrod offsets, DF balance %, L/D ratio, vortex margin
- **Key functions:**
  - `rake_solver.py::RakeSolver.solve()` — main entry, calls `_solve_pinned_front()` or `_solve_free()`
  - `rake_solver.py::RakeSolver._solve_pinned_front()` — pins front static RH at 30mm (default GTP strategy)
  - `rake_solver.py::RakeSolver._solve_free()` — unconstrained optimization
  - `rake_solver.py::reconcile_ride_heights()` — post-hoc refinement after step2/step3 spring rates known
- **BMW-specific:** `pin_front_min=True` defaults to 30.0mm static floor — "real-world GTP setup methodology"
- **Car-specific params:** `car.to_aero_coords()`, `car.from_aero_coords()`, `car.min_front_rh_static`, `car.vortex_burst_threshold_mm`, `car.aero_compression`, `car.active_garage_output_model(track_name)`
- **TODOs:** None
- **Fallbacks:** Grid search fallback if `brentq` root finder fails. Default compression ref speed if `track.median_speed_kph == 0`. Closest-balance fallback if target unachievable.

### Step 2: `solver/heave_solver.py` — Heave / Third Springs
- **Production path:** Yes
- **Computes:** Minimum front heave and rear third spring rates for bottoming prevention and platform stability
- **Inputs:** `CarModel`, `TrackProfile`, dynamic RH targets, fuel load, front camber, measured telemetry
- **Outputs:** `HeaveSolution` — spring rates, excursion data, bottoming margins, perch offsets
- **Key functions:**
  - `heave_solver.py::HeaveSolver.solve()` — main solver
  - `heave_solver.py::HeaveSolver.excursion()` — damped ride height excursion model
  - `heave_solver.py::HeaveSolver._best_front_perch_with_garage_model()` — BMW perch optimization
  - `heave_solver.py::HeaveSolver.reconcile_solution()` — post-step3 travel budget recalculation
- **BMW-specific:** `_best_front_perch_with_garage_model()` and `_garage_constrained_front_solution()` use BMW/Sebring garage output models. `_heave_hard_bounds()` applies track-specific hard limits using `hsm.front_heave_hard_range_nmm`.
- **Car-specific params:** `car.heave_spring.front_m_eff_kg/rear_m_eff_kg`, `car.tyre_vertical_rate_front_nmm`, `car.damper.front_hs_coefficient_nsm`, `car.active_garage_output_model()`
- **TODOs:** None
- **Fallbacks:** Default `v_p99` to `shock_vel_p99_mps` if `_clean_mps` is zero. Default perch target if not provided.

### Step 3: `solver/corner_spring_solver.py` — Corner Springs
- **Production path:** Yes
- **Computes:** Front torsion bar OD and rear coil spring rate via natural frequency targeting
- **Inputs:** `CarModel`, `TrackProfile`, front heave rate, rear third rate, fuel load
- **Outputs:** `CornerSpringSolution` — torsion OD, spring rates, natural frequencies, heave/corner ratios
- **Key functions:**
  - `corner_spring_solver.py::CornerSpringSolver.solve()` — frequency/ratio targeting
  - `corner_spring_solver.py::CornerSpringSolver._surface_severity_to_freq_ratio()` — dynamic severity scaling
- **BMW-specific:** None (car-agnostic physics)
- **Car-specific params:** `car.corner_spring.front_torsion_c`, `car.corner_spring.rear_motion_ratio`, `car.corner_spring.rear_is_torsion_bar`, `car.rh_variance.dominant_bump_freq_hz`, `csm.snap_torsion_od`, `csm.snap_rear_rate`
- **TODOs:** None
- **Fallbacks:** Default values if `track.shock_vel_p99_..._clean_mps` unavailable

### Step 4: `solver/arb_solver.py` — Anti-Roll Bars
- **Production path:** Yes
- **Computes:** Front/rear ARB size and blade position for target LLTD
- **Inputs:** `CarModel`, `TrackProfile`, front/rear wheel rates, LLTD offset, current rear ARB size
- **Outputs:** `ARBSolution` — ARB setup, LLTD analysis, RARB sensitivity, live blade range
- **Key functions:**
  - `arb_solver.py::ARBSolver.solve()` — LLTD targeting + ARB search
  - `arb_solver.py::ARBSolver._compute_lltd()` — roll stiffness → LLTD calculation
- **BMW-specific:** Strategy comment: "keep FARB soft (blade 1), use RARB as primary live balance"
- **Car-specific params:** `car.arb.*` (track widths, roll stiffness coefficients, size labels, baseline values), `car.weight_dist_front`, `car.tyre_load_sensitivity`, `track.pct_above_200kph`
- **TODOs:** None
- **Fallbacks:** Default LLTD to 0.5 if total roll stiffness is zero

### Step 5: `solver/wheel_geometry_solver.py` — Wheel Geometry
- **Production path:** Yes
- **Computes:** Optimal camber and toe angles for contact patch optimization and thermal conditioning
- **Inputs:** `CarModel`, `TrackProfile`, roll stiffness, wheel rates, fuel load, measured state
- **Outputs:** `WheelGeometrySolution` — camber/toe recommendations, thermal predictions, body roll data
- **Key functions:**
  - `wheel_geometry_solver.py::WheelGeometrySolver.solve()` — camber/toe optimization
  - `wheel_geometry_solver.py::WheelGeometrySolver._laps_to_operating_temp()` — thermal conditioning model
- **BMW-specific:** Hardcoded "BMW Vision tread conditioning rates": `2.4 °C/lap front, 3.2 °C/lap rear` in `solve()`. Sprint qualifying strategy notes reference BMW specifically.
- **Car-specific params:** `car.geometry.*` (roll gains, camber/toe baselines/ranges/steps)
- **TODOs:** None
- **Fallbacks:** Default `p95_lat_g` if not in track profile. Defaults for `understeer_*` and `body_slip_p95_deg` if `measured` is `None`.

### Step 6: `solver/damper_solver.py` — Dampers
- **Production path:** Yes
- **Computes:** All damper settings (LS/HS Comp/Rbd, Slope) from first principles
- **Inputs:** `CarModel`, `TrackProfile`, wheel rates, dynamic RH, fuel load, measured state, heave/third rates
- **Outputs:** `DamperSolution` — per-corner click settings, damping coefficients, ζ ratios, Rbd/Comp ratios
- **Key functions:**
  - `damper_solver.py::DamperSolver.solve()` — physics-first damper derivation
  - `damper_solver.py::DamperSolver._damping_ratio_ls/hs()` — target ζ values
  - `damper_solver.py::DamperSolver._hs_slope_from_surface()` — track-adaptive slope
- **BMW-specific:** Hardcoded optimal ζ targets: `front LS=0.88, rear LS=0.30, front HS=0.45, rear HS=0.14` — extensive physics justifications in comments specific to "GTP cars (spring asymmetry, traction requirements)". Hardcoded Rbd/Comp ratios: `front LS=0.86, rear HS=3.00`.
- **Car-specific params:** `car.damper.*` (ranges, force-per-click, digressive exponent, knee velocity), `car.damper.has_roll_dampers` (ORECA support)
- **TODOs:** None
- **Fallbacks:** Default damper coefficients if not provided. Default `v_hs_ref` if track profile unavailable.

---

## 3. Scoring & Objective System

### `solver/objective.py` — Multi-Objective Scoring Function
- **Production path:** Yes — used by legal search, grid search, candidate evaluation
- **Purpose:** Defines `ObjectiveFunction` that evaluates any candidate setup into a single score with transparent breakdown. Score = `w_lap_gain × lap_gain_ms - w_platform × platform_risk_ms - w_driver × driver_mismatch_ms - ...`
- **Key functions:**
  - `objective.py::ObjectiveFunction.evaluate(params, family)` — scores a single candidate
  - `objective.py::ObjectiveFunction.evaluate_physics(params)` — forward physics calculations (excursion, LLTD, DF balance, damping ratios)
  - `objective.py::ObjectiveFunction._estimate_lap_gain()` — per-parameter lap time estimation
  - `objective.py::ObjectiveFunction._compute_platform_risk()` — bottoming/vortex/slider penalties
  - `objective.py::ObjectiveFunction._compute_vortex_threshold_mm()` — dynamic wing-specific vortex threshold from aero gradient
- **BMW-specific:**
  - `_is_bmw_sebring_track_aware_single_lap_safe()` — special weighting path for BMW/Sebring `single_lap_safe`
  - `TORSION_ARB_COUPLING = 0.25` — BMW/Sebring empirical constant for LLTD prediction correction
  - `car.measured_lltd_target` — BMW calibration notes: `theory=0.528, measured=0.38-0.43 → use 0.41`
  - `arb_extreme_ms` zeroed out based on BMW calibration evidence
  - IBT-calibrated damper ζ targets: `LS front=0.68, LS rear=0.23, HS front=0.47, HS rear=0.20`
- **Car-specific params:** `car.canonical_name`, `car.default_df_balance_pct`, `car.measured_lltd_target`, `car.garage_ranges.diff_coast_drive_ramp_options`
- **Car-specific branching:**
  - `diff_target = 10.0 if self._car_slug == "ferrari" else 65.0` (line 1335, 1501)
- **TODOs:**
  - Line 287: "TODO: consider using measured heave/third spring rates for physics eval"
  - Line 1236: "Future: replace zeta model with non-monotonic empirical click scoring"
  - Line 1367: "arb_extreme_ms ZEROED OUT — do NOT restore without corroborating IBT evidence"
  - Line 1398: "diff_ramp penalty reduced ... calibration evidence"
- **Fallbacks:** Default `VORTEX_BURST_THRESHOLD_MM`, default wheel rates, default diff preload if not found in params. Cached default `AeroSurface` if `wing_deg` not passed.

### `solver/predictor.py` — Telemetry Prediction
- **Production path:** Yes — called from `solve_chain.py::_finalize_result()`
- **Purpose:** Predicts telemetry changes directionally from setup modifications. Anchors to baseline measured telemetry and applies scaling factors.
- **Key functions:**
  - `predictor.py::predict_candidate_telemetry()` — hybrid model producing `PredictedTelemetry`
- **BMW-specific:** No
- **Car-specific params:** None (general prediction logic based on parameter deltas)
- **TODOs:** None
- **Fallbacks:** `_safe_float` handles `None`/invalid inputs. `_MAX_CORRECTIONS` clamps learned corrections. Default `lltd_delta = 0.0`.

### `solver/candidate_ranker.py` — Candidate Scoring
- **Production path:** Yes — called from `candidate_search.py`
- **Purpose:** Combines predicted performance, safety, stability, confidence, and disruption cost into a single `CandidateScore`.
- **Key functions:**
  - `candidate_ranker.py::combine_candidate_score()` — weighted aggregation
  - `candidate_ranker.py::score_from_prediction()` — builds score from predicted telemetry
- **BMW-specific:** No
- **Car-specific params:** None
- **TODOs:** None
- **Fallbacks:** Returns neutral score if no predicted telemetry available

---

## 4. Legal Search System

### `solver/legal_space.py` — Search Space Definition
- **Production path:** Yes — used by legal search, grid search
- **Purpose:** Defines `LegalSpace` (the full manifold of searchable parameters), `SearchDimension`, `LegalCandidate`. Provides sampling (Sobol, seeded, uniform), snapping, neighborhood generation, and exhaustive grid enumeration.
- **Key functions:**
  - `legal_space.py::LegalSpace.from_car(car)` — builds search space from car model
  - `legal_space.py::compute_perch_offsets(params, car)` — derives dependent perch offsets from spring rates
  - `legal_space.py::LegalSpace.sobol_sample()` — quasi-random Sobol sampling with scipy fallback
- **BMW-specific:** `compute_perch_offsets()` — hardcoded reference values (`FRONT_HEAVE_SPRING_REF=50.0`, `REAR_SPRING_REF=160.0`) are BMW-calibrated. Front heave perch empirical model: `front_static_rh = 30.1458 + 0.001614*heave_nmm + 0.074486*camber_deg`
- **Car-specific branching:**
  - `if getattr(car, "canonical_name", "") == "ferrari":` — preserves Ferrari perch offsets instead of computing them (line 98)
- **Car-specific params:** `car.garage_ranges.*`, `car.corner_spring.*`, `car.geometry.*`, `car.damper.*`, `car.arb.*`
- **TODOs:** None
- **Fallbacks:** Falls back to uniform random if scipy unavailable for Sobol. `_TORSION_OD_OPTIONS` hardcoded BMW values as fallback.

### `solver/legality_engine.py` — Legality Validation
- **Production path:** Yes
- **Purpose:** Validates setup legality via two paths: `validate_solution_legality()` (full, uses garage model) and `validate_candidate_legality()` (fast, search-time range checks).
- **Key functions:**
  - `legality_engine.py::validate_solution_legality()` — full validation with garage model
  - `legality_engine.py::validate_candidate_legality()` — fast range + ratio checks
- **BMW-specific:** No (uses car model abstractions)
- **Car-specific branching:**
  - `is_ferrari = getattr(car, "canonical_name", "") == "ferrari"` — converts spring rates to `public_output_value` before passing to garage validator (line 52–53)
- **Car-specific params:** `car.active_garage_output_model()`, `car.garage_ranges`, `car.damper`
- **TODOs:** None
- **Fallbacks:** If no `garage_model` found, returns `range_clamp` tier validation with warning

### `solver/legal_search.py` — Legal Manifold Search Engine
- **Production path:** Yes — called from `solve.py` and `pipeline/produce.py`
- **Purpose:** Two-stage search: (1) generate candidates via Sobol sampling + edge-family seeds, (2) evaluate via `ObjectiveFunction` + legality checks. Selects best robust, aggressive, and scenario-picked candidates.
- **Key functions:**
  - `legal_search.py::run_legal_search()` — main entry
  - `legal_search.py::_generate_family_seeds()` — physics baseline + edge families (min_drag, max_platform, max_rotation, etc.)
  - `legal_search.py::_evaluate_candidates()` — score + legality check
- **BMW-specific:** `_is_bmw_sebring(car, track)` check in `_run_grid_search` path
- **Car-specific params:** `LegalSpace.from_car(car)`, `compute_perch_offsets()`
- **TODOs:** None
- **Fallbacks:** Default budget, seed, mode. Handles `None` for best candidates.

### `solver/candidate_search.py` — Candidate Family Generation
- **Production path:** Yes — called from `pipeline/produce.py`
- **Purpose:** Generates 3 candidate families (incremental, compromise, baseline_reset). Each starts from the solver's physics output and applies telemetry-driven adjustments of varying aggressiveness. Materializes via `materialize_overrides()`, then scores via `score_from_prediction()`.
- **Key functions:**
  - `candidate_search.py::generate_candidate_families()` — creates 3 candidates
  - `candidate_search.py::_apply_family_state_adjustments()` — telemetry-driven parameter tweaks
  - `candidate_search.py::canonical_params_to_overrides()` — converts search params to chain overrides
  - `candidate_search.py::_snap_targets_to_garage()` — garage legality snapping
- **BMW-specific:** `_TORSION_OD_OPTIONS` hardcoded BMW torsion bar OD discrete values. Ferrari torsion snapping differs (uses step instead of discrete options).
- **Car-specific branching:**
  - `is_ferrari = getattr(car, "canonical_name", "") == "ferrari"` — at lines 110, 290, 598, 603, 622, 782, 796–799 — affects spring rate adjustments (index-based vs N/mm), perch handling, disruption scaling
- **TODOs:** None
- **Fallbacks:** `_safe_float` handles None values. Default master cylinder options, pad compound options.

### `solver/setup_space.py` — Setup Space Exploration
- **Production path:** Yes (optional, called from `solve.py`)
- **Purpose:** Explores parameter space around solver's optimal values. Scans ±N steps for each key parameter, scoring constraint violations + estimated lap time delta. Reports "flat bottom" ranges and robustness classification.
- **Key functions:**
  - `setup_space.py::explore_setup_space()` — main entry
  - `setup_space.py::_scan_rear_arb()`, `_scan_front_heave()`, etc. — per-parameter scans
- **BMW-specific:** Default sensitivity values are plausible for BMW (e.g., `rarb_ms_per_blade = 180`)
- **Car-specific params:** None (uses solver outputs)
- **TODOs:** None
- **Fallbacks:** Default lap-time sensitivities if no sensitivity report provided

---

## 5. Scenario Profiles

### `solver/scenario_profiles.py`
- **Production path:** Yes — drives weight selection in objective function
- **Purpose:** Defines 4 scenario profiles (`single_lap_safe`, `quali`, `sprint`, `race`) each with specific objective weight profiles and prediction sanity limits.
- **Key functions:**
  - `scenario_profiles.py::get_scenario_profile(name)` — returns profile
  - `scenario_profiles.py::prediction_passes_sanity(predicted, profile)` — validates predictions against limits
  - `scenario_profiles.py::should_run_legal_manifold_search()` — determines if legal search is warranted
- **BMW-specific:** No (profiles are car-agnostic; weights may have been tuned for BMW implicitly)
- **Car-specific params:** None
- **TODOs:** None
- **Fallbacks:** `resolve_scenario_name()` defaults to `single_lap_safe`

---

## 6. Optimizers

### `solver/full_setup_optimizer.py` — BMW/Sebring Constrained Optimizer
- **Production path:** Yes — called from `solve_chain.py::run_base_solve()` for BMW/Sebring only
- **Purpose:** Loads pre-calibrated `BMWSebringSeed` setups from `data/calibration_dataset.json`, optimizes continuous parameters (pushrod, perch, camber) via `scipy.optimize.minimize` (SLSQP), runs full 6-step solver for each seed, selects best.
- **Key functions:**
  - `full_setup_optimizer.py::optimize_if_supported()` — entry point; returns `None` for non-BMW/non-Sebring
  - `full_setup_optimizer.py::_is_bmw_sebring(car, track)` — gate check (line 94–96)
  - `full_setup_optimizer.py::BMWSebringOptimizer._evaluate_seed()` — evaluates each seed
- **BMW-specific:** **100% BMW/Sebring specific.** `_is_bmw_sebring()` checks `car.canonical_name.lower() == "bmw"` and `"sebring" in track_name.lower()`. Seeds loaded from BMW-specific calibration dataset.
- **Car-specific params:** `car.active_garage_output_model(track.track_name)`
- **TODOs:** None
- **Fallbacks:** If no feasible seed found, raises `RuntimeError`. If optimization fails for a seed, it's skipped.

### `solver/bayesian_optimizer.py` — Bayesian Optimization (Research)
- **Production path:** No — only called if `--bayesian` flag is passed (marked "research only")
- **Purpose:** Gaussian Process surrogate model + Expected Improvement acquisition for setup optimization. Uses a simplified physics-based scoring proxy (not the full ObjectiveFunction).
- **Key functions:**
  - `bayesian_optimizer.py::BayesianOptimizer.optimize()` — LHS + BO loop
  - `bayesian_optimizer.py::BayesianOptimizer._score()` — simplified physics proxy
- **BMW-specific:** `_score()` has hardcoded "peak near -3.5F / -2.5R" camber targets. PARAM_SPEC uses 7 dimensions only.
- **Car-specific params:** `car.garage_ranges`, `car.heave_spring.front_m_eff_kg`, `car.weight_dist_front`, `car.tyre_load_sensitivity`
- **TODOs:** None
- **Fallbacks:** Latin Hypercube initial design. Jitter escalation in GP fitting if kernel matrix is singular.

### `solver/grid_search.py` — 4-Layer Hierarchical Grid Search
- **Production path:** Yes — called from `legal_search.py::_run_grid_search()` for exhaustive/maximum modes
- **Purpose:** Structured 4-layer search: (1) Sobol platform skeletons, (2) exhaustive balance grid (torsion×ARB_F×ARB_R), (3) damper coordinate descent, (4) neighborhood polish.
- **Key functions:**
  - `grid_search.py::GridSearchEngine.run(budget)` — orchestrates 4 layers
  - `grid_search.py::GridSearchEngine.layer1_platform_skeletons()` — Sobol + family biasing
  - `grid_search.py::GridSearchEngine.layer2_balance_grid()` — exhaustive torsion×ARB grid
  - `grid_search.py::GridSearchEngine.layer3_damper_coordinate_descent()` — per-axis damper sweep
  - `grid_search.py::GridSearchEngine.layer4_neighborhood_polish()` — ±1 step hill climbing
- **Budget tiers:** `quick` (~3s), `standard` (~4min), `exhaustive` (~80min), `maximum` (~5h)
- **BMW-specific:** `FAMILY_BIASES` defines "robust", "aggressive", "balanced" Sobol biases. Not explicitly BMW-specific but parameter ranges are car-derived.
- **Car-specific params:** `LegalSpace`, `ObjectiveFunction`, `car`, `track`
- **TODOs:** None
- **Fallbacks:** Falls back to L1 results if L2 produces nothing. Falls back to L2 if L3 produces nothing.

### `solver/iterative_solver.py` — Multi-Pass Iterative Solver (Experimental)
- **Production path:** No — only called if `--iterative` flag passed (marked experimental)
- **Purpose:** Wraps the 6-step solver in an outer loop with cross-step constraint checking and relaxation damping. Max 3 passes.
- **Key functions:**
  - `iterative_solver.py::run_iterative_solver()` — orchestrates passes
  - `iterative_solver.py::compute_residuals()` — constraint violation vector
  - `iterative_solver.py::compute_cross_step_adjustments()` — derives corrections
- **BMW-specific:** Default `target_balance=50.14` is BMW's default
- **Car-specific params:** Uses car/track/surface from input args
- **TODOs:** None
- **Fallbacks:** Convergence after max passes. Relaxation factors `{1: 1.0, 2: 0.7, 3: 0.5}`.

---

## 7. Supporting Solver

### `solver/supporting_solver.py` — Brake, Diff, TC, Pressures
- **Production path:** Yes
- **Purpose:** Computes brake bias, differential settings, traction control, and tyre pressures. Delegates to `BrakeSolver` and `DiffSolver`.
- **Key functions:**
  - `supporting_solver.py::SupportingSolver.solve()` — orchestrates all supporting params
  - `supporting_solver.py::SupportingSolver._solve_pressures()` — targets 155–170 kPa hot window
  - `supporting_solver.py::SupportingSolver._solve_tc()` — TC based on driver consistency
  - `supporting_solver.py::SupportingSolver._solve_context()` — fuel, gears, roof color
- **BMW-specific:** No explicit BMW hardcoding, but references car model defaults
- **Car-specific branching:**
  - `if getattr(self.car, "canonical_name", "") == "ferrari":` — 4 instances (lines 253, 304, 377, 450) — passthrough live brake bias and TC settings from telemetry/setup instead of computing them
- **Car-specific params:** `car.garage_ranges` (MC options, pad options, diff ramp options, clutch plate options)
- **TODOs:** None
- **Fallbacks:** `_solve_diff_fallback()` if `DiffSolver` unavailable. Default values for TC, diff, pressures.

### `solver/brake_solver.py` — Brake Bias
- **Production path:** Yes — called from `SupportingSolver`
- **Purpose:** Physics-informed brake bias from fuel-corrected weight distribution + driver style adjustments.
- **Key functions:**
  - `brake_solver.py::BrakeSolver.solve()` — full brake solution with trail-brake, decel, lock corrections
  - `brake_solver.py::compute_brake_bias()` — baseline from car model + fuel correction
- **BMW-specific:** No
- **Car-specific params:** `car.brake_bias_pct`, `car.mass_car_kg`, `car.mass_driver_kg`, `car.wheelbase_m`, `car.weight_dist_front`
- **TODOs:** None
- **Fallbacks:** None significant

### `solver/diff_solver.py` — Differential Model
- **Production path:** Yes — called from `SupportingSolver` if available
- **Purpose:** BMW-first empirical differential model. Computes preload, ramp angles, clutch plates from driver style and traction data.
- **Key functions:**
  - `diff_solver.py::DiffSolver.solve()` — full diff recommendation
  - `diff_solver.py::DiffSolver.solve_defaults()` — conservative baseline for standalone solver
  - `diff_solver.py::DiffSolver._lock_pct()` — lock percentage formula
- **BMW-specific:** Constants: `CLUTCH_TORQUE_PER_PLATE = 45 Nm`, `BMW_DEFAULT_CLUTCH_PLATES = 6`, `DEFAULT_MAX_TORQUE_NM = 700`. Ramp options: coast `[40, 45, 50]`, drive `[65, 70, 75]`.
- **Car-specific params:** `car.max_torque_nm` (overrides default), `car.total_mass()`, `car.corner_spring.track_width_mm`, `car.wheelbase_m`
- **TODOs:** None
- **Fallbacks:** Default peak_lat_g = 2.0 if track/measured don't provide it

---

## 8. Modifier System

### `solver/modifiers.py`
- **Production path:** Yes — called from `solve_chain.py`
- **Purpose:** Computes `SolverModifiers` from `Diagnosis`, `DriverProfile`, and `MeasuredState`. Adjusts solver targets: DF balance offset, heave spring floors, LLTD offset, damper click offsets, damping ratio scale.
- **Key functions:**
  - `modifiers.py::compute_modifiers()` — main entry, iterates through diagnosis.problems
- **BMW-specific:** BMW heave range noted: "BMW heave range is 30-50 N/mm" in comments
- **Car-specific params:** Implicitly via `Diagnosis`, `DriverProfile`, `MeasuredState`
- **TODOs:** None
- **Fallbacks:** `_num()` helper defaults to 0.0 for None/invalid values. State-confidence weighting with safety floor re-application.

---

## 9. Decision Trace

### `solver/decision_trace.py`
- **Production path:** Yes — called from `solve_chain.py::_finalize_result()`
- **Purpose:** Generates human-readable trace of every parameter decision with current vs. proposed values, confidence, legality, and physics rationale.
- **Key functions:**
  - `decision_trace.py::build_parameter_decisions()` — main entry
  - `decision_trace.py::_legacy_build_parameter_decisions()` — fallback for non-BMW cars
- **BMW-specific branching:**
  - `if car_name.lower() == "bmw":` — uses `bmw_coverage.build_parameter_coverage()` for dynamic parameter list
  - Non-BMW cars use `_legacy_parameter_spec()` with hardcoded parameter list
- **Car-specific branching:**
  - `is_ferrari = car_name.lower() == "ferrari"` — changes parameter labels (e.g., `"front_heave_nmm"` vs `"front_heave_index"`, units `"N/mm"` vs `"idx"`)
  - Ferrari-specific warning for unsupported engineering unit decode
- **TODOs:** None
- **Fallbacks:** `_avg_confidence` handles missing signals. `_estimate_gain_ms` / `_estimate_cost_ms` provide default estimations.

### `solver/bmw_coverage.py` — BMW Parameter Coverage
- **Production path:** Yes — called from `decision_trace.py` for BMW cars only
- **Purpose:** Maps all BMW settable parameters to their solver outputs, current setup values, and required telemetry signals. Provides `build_parameter_coverage()` for comprehensive change reporting.
- **Key functions:**
  - `bmw_coverage.py::build_parameter_coverage()` — maps all ~50 BMW fields to current/proposed values
  - `bmw_coverage.py::build_telemetry_coverage()` — maps required signals per parameter
  - `bmw_coverage.py::bmw_coverage_fields()` — list of all BMW settable fields
  - `bmw_coverage.py::solved_value()` — extracts solver output for any field name
- **BMW-specific:** **100% BMW-specific.** All constants (`BMW_LOCAL_REFINE_FIELDS`, `BMW_SIGNAL_REQUIREMENTS`, etc.) are BMW-only. However, the architecture is extensible — similar coverage modules could be created for other cars.
- **Car-specific params:** Uses `car_model.setup_registry` which has per-car field specs
- **TODOs:** None
- **Fallbacks:** Falls through to `current_setup_value()` if solved value unavailable

---

## 10. Experimental / Research Modules

### `solver/bmw_rotation_search.py` — BMW Rotation Control Optimizer
- **Production path:** Yes — called from `solve_chain.py` for BMW/Sebring
- **Purpose:** Fine-tunes "rotation controls" (diff, torsion, geometry, rear ARB) using a bespoke scoring function based on telemetry-derived rotation characteristics (entry push, exit push, instability, traction risk).
- **Key functions:**
  - `bmw_rotation_search.py::search_rotation_controls()` — main entry
  - `bmw_rotation_search.py::_build_rotation_state()` — derives rotation metrics from telemetry
  - `bmw_rotation_search.py::_score_controls()` — bespoke scoring with BMW-tuned coefficients
  - `bmw_rotation_search.py::preserve_candidate_rotation_controls()` — applies rotation results to existing SolveChainResult
- **BMW-specific:** **100% BMW/Sebring specific.** `_is_bmw_sebring()` gate. All scoring coefficients (e.g., `preload_exit_gain`, `preload_instability_cost`) are BMW-tuned.
- **TODOs:** None
- **Fallbacks:** Defaults for `_safe_float`, `_safe_int`. Defaults if `corners` is `None`.

### `solver/explorer.py` — Unconstrained Parameter Space Explorer
- **Production path:** No — only called if `--explore` flag passed
- **Purpose:** Explores the full legal iRacing garage space WITHOUT applying engineering soft constraints. Uses Latin Hypercube Sampling + simplified scoring to find potentially unconventional but fast setups.
- **Key functions:**
  - `explorer.py::SetupExplorer.explore()` — LHS with 5000 samples default
- **BMW-specific:** Hardcoded "peak near -3.5F / -2.5R" camber targets. Uses `car.corner_spring.front_torsion_c` for wheel rate calculation.
- **Car-specific params:** `car.garage_ranges`, `car.corner_spring.*`, `car.arb.*`
- **TODOs:** None
- **Fallbacks:** Default 5000 samples, default baseline score

### `solver/multi_speed_solver.py` — Multi-Speed Compromise Analysis
- **Production path:** No — only called if `--multi-speed` flag passed (marked "research only")
- **Purpose:** Evaluates setup performance at 3 speed regimes (low/mid/high) with time-weighted scoring. Answers "Am I losing more in slow or fast corners?"
- **BMW-specific:** No
- **Car-specific params:** `car.aero_compression`, `car.heave_spring.front_m_eff_kg`
- **TODOs:** None
- **Fallbacks:** Default `pct_below_120kph`, `pct_above_200kph` if not in track profile

---

## 11. Utility & Analysis Modules

### `solver/sensitivity.py` — Constraint Proximity & Sensitivity
- **Production path:** Yes (optional, called from `solve.py`)
- **Purpose:** Reports constraint proximity (binding/slack), parameter sensitivities (∂output/∂input), and confidence bands with uncertainty propagation.
- **BMW-specific:** Hardcoded BMW default masses: `m_eff_front=228.0 kg`, `m_eff_rear=2395.3 kg` in `build_sensitivity_report()` (line 502–503, 529–530)
- **Car-specific params:** Uses step1/step2 outputs
- **TODOs:** None
- **Fallbacks:** None significant

### `solver/coupling.py` — Parameter Coupling Map
- **Production path:** Yes (optional, called from reporting)
- **Purpose:** Static coupling map: downstream effects of parameter changes. E.g., `rear_perch_offset_mm +2mm → rear_static_rh_mm +0.19mm, df_balance_pct -0.06%`.
- **BMW-specific:** All coupling gains are "calibrated from BMW Sebring data" per comments (line 44). E.g., `OD^4 linearized at BMW baseline 13.9mm`, `RARB: -3.02% LLTD per blade`.
- **Car-specific params:** None (static constants)
- **TODOs:** None
- **Fallbacks:** Returns fallback message for unknown parameters

### `solver/laptime_sensitivity.py` — Lap Time Sensitivity Model
- **Production path:** Yes (optional)
- **Purpose:** Estimates ms/unit for each key setup parameter via physics chains (aero→DF→cornering speed→lap time, etc.)
- **BMW-specific:** Default constants calibrated for GTP: `GTP_MASS_KG=1050`, `FRONT_RH_DIRECT_MS_PER_MM=55.0`, `REAR_RH_DIRECT_MS_PER_MM=25.0`
- **TODOs:** None
- **Fallbacks:** Default sensitivity values

### `solver/stint_model.py` — Stint / Fuel Burn Modeling
- **Production path:** Yes (optional, called from `solve.py`)
- **Purpose:** Models fuel burn's effect on mass, weight distribution, CG height, and setup parameters across a stint. Predicts parameter drift and compromise recommendations.
- **BMW-specific:** No
- **Car-specific params:** Uses `CarModel` mass/fuel properties
- **TODOs:** None
- **Fallbacks:** Default degradation rates

### `solver/stint_reasoner.py` — Stint-Aware Solve
- **Production path:** Yes (called from `pipeline/produce.py` for stint-aware scenarios)
- **Purpose:** Re-solves the setup at multiple fuel states across a stint, blending results for best compromise. Uses per-lap penalty scoring.
- **BMW-specific:** No
- **Car-specific params:** Via solve chain inputs
- **TODOs:** None
- **Fallbacks:** Fallback mode if stint data insufficient

### `solver/session_database.py` — k-NN Empirical Session Database
- **Production path:** Yes — used by `ObjectiveFunction` for empirical cross-checks
- **Purpose:** Multi-dimensional k-NN predictor over setup × telemetry space. Given a proposed setup, finds nearest historical sessions and predicts telemetry outcomes.
- **BMW-specific:** No (data-driven, car/track specific via stored observations)
- **Car-specific params:** None (loaded from data files)
- **TODOs:** None
- **Fallbacks:** Returns empty predictions if no data available

### `solver/heave_calibration.py` — Empirical Heave Calibration
- **Production path:** Yes — used by `ObjectiveFunction`
- **Purpose:** Learns heave_spring → platform_sigma relationship from real IBT data. Weighted combination of 1/√k physics model + RBF interpolant from observations.
- **BMW-specific:** No (data-driven)
- **Car-specific params:** None (loaded from `data/learnings/`)
- **TODOs:** None
- **Fallbacks:** Physics model if no observations available

### `solver/learned_corrections.py` — Learner → Solver Bridge
- **Production path:** Yes — called from `solve.py` if `--learn` flag
- **Purpose:** Loads empirical corrections from the knowledge store and packages them as `LearnedCorrections` for the solver to apply.
- **BMW-specific:** No
- **Car-specific params:** Car/track-specific via knowledge store query
- **TODOs:** None
- **Fallbacks:** Returns empty corrections if insufficient sessions

### `solver/setup_fingerprint.py` — Setup Fingerprinting
- **Production path:** Yes — used for deduplication and comparison
- **Purpose:** Creates a frozen hashable `SetupFingerprint` from a setup for comparison, deduplication, and tracking.
- **BMW-specific:** No
- **Car-specific params:** None
- **TODOs:** None
- **Fallbacks:** Snap to resolution for hashing

### `solver/uncertainty.py` — Uncertainty Quantification
- **Production path:** Yes (optional, called from reporting)
- **Purpose:** Propagates input measurement uncertainty through the solver. Classifies HIGH/MEDIUM/LOW confidence per parameter.
- **BMW-specific:** No
- **Car-specific params:** None
- **TODOs:** None
- **Fallbacks:** Default uncertainty assumptions

### `solver/validation.py` — Prediction → Measurement Validation
- **Production path:** Yes (called from learning loop)
- **Purpose:** Stores solver predictions, compares against actual telemetry, computes Bayesian updates for model parameters.
- **BMW-specific:** No
- **Car-specific params:** None
- **TODOs:** None
- **Fallbacks:** None significant

### `solver/corner_strategy.py` — Per-Corner Live Parameter Strategy
- **Production path:** Yes (optional)
- **Purpose:** Groups corners into speed clusters, computes per-cluster live parameter recommendations (RARB blade, brake bias, TC, diff).
- **BMW-specific:** No
- **Car-specific params:** Uses `CarModel` for baseline values
- **TODOs:** None
- **Fallbacks:** Default corner clusters

### `solver/sector_compromise.py` — Sector-Level Compromise Analysis
- **Production path:** Yes (optional, called from `solve.py`)
- **Purpose:** Divides lap into slow/medium/fast sectors and computes per-parameter compromise costs.
- **BMW-specific:** Constants calibrated for GTP: `RARB_LLTD_PER_BLADE = -0.030`, `RARB_LAPTIME_MS_PER_BLADE = 45.0`
- **Car-specific params:** None
- **TODOs:** None
- **Fallbacks:** Default sensitivity values

---

## 12. Car-Specific Logic Branching

### BMW-Specific Paths

| Module | Check | Effect |
|--------|-------|--------|
| `solve_chain.py` | `optimize_if_supported()` | Routes to `BMWSebringOptimizer` instead of sequential solver |
| `solve_chain.py` | `bmw_rotation_search.search_rotation_controls()` | Runs rotation control optimization |
| `full_setup_optimizer.py` | `_is_bmw_sebring(car, track)` | Entire optimizer is BMW/Sebring only |
| `bmw_rotation_search.py` | `_is_bmw_sebring()` | Entire module is BMW/Sebring only |
| `bmw_coverage.py` | All functions | 100% BMW-specific parameter coverage |
| `decision_trace.py` | `car_name.lower() == "bmw"` | Uses BMW coverage instead of legacy spec |
| `objective.py` | `_is_bmw_sebring_track_aware_single_lap_safe()` | Special weight profile for BMW/Sebring |
| `objective.py` | `TORSION_ARB_COUPLING = 0.25` | BMW/Sebring LLTD empirical correction |
| `sensitivity.py` | Hardcoded `m_eff_front=228.0` | BMW mass defaults |
| `coupling.py` | All gains | "Calibrated from BMW Sebring data" |
| `legal_space.py` | `compute_perch_offsets()` reference values | BMW-calibrated perch model |

### Ferrari-Specific Paths

| Module | Check | Effect |
|--------|-------|--------|
| `legal_space.py` | `canonical_name == "ferrari"` | Preserves perch offsets (no computation) |
| `legality_engine.py` | `canonical_name == "ferrari"` | Converts spring rates to `public_output_value` |
| `candidate_search.py` | `canonical_name == "ferrari"` | Index-based spring adjustments, different disruption scales |
| `supporting_solver.py` | `canonical_name == "ferrari"` | Passthrough live brake bias and TC from telemetry |
| `objective.py` | `_car_slug == "ferrari"` | `diff_target = 10.0` (vs 65.0 for others) |
| `solve_chain.py` | `canonical_name == "ferrari"` | `diff_ramp_string_for_option` with `ferrari_label=True` |
| `decision_trace.py` | `car_name.lower() == "ferrari"` | Different parameter labels and units |

### Acura-Specific Paths

| Module | Check | Effect |
|--------|-------|--------|
| `damper_solver.py` | `car.damper.has_roll_dampers` | Uses baseline roll damper values (ORECA architecture) |
| `corner_spring_solver.py` | `car.corner_spring.rear_is_torsion_bar` | Rear torsion bar handling |

### All Cars (Via CarModel Abstraction)

The vast majority of car-specific behavior is encoded in the `CarModel` dataclass rather than conditional branches. Key abstractions:
- `car.aero_compression` — static↔dynamic RH conversion
- `car.active_garage_output_model(track)` — garage physics model
- `car.heave_spring.*` — effective masses, spring ranges
- `car.corner_spring.*` — torsion constants, motion ratios, OD options
- `car.arb.*` — roll stiffness coefficients, size labels
- `car.geometry.*` — roll gains, camber/toe ranges/steps
- `car.damper.*` — click ranges, force-per-click, digressive model
- `car.garage_ranges.*` — all legal parameter ranges

---

## 13. TODO / FIXME / HACK Inventory

| File | Line | Content |
|------|------|---------|
| `objective.py` | ~287 | `TODO: consider using measured heave/third spring rates for physics eval` |
| `objective.py` | ~1236 | `Future: replace zeta model with non-monotonic empirical click scoring` |
| `objective.py` | ~1367 | `arb_extreme_ms ZEROED OUT — do NOT restore without corroborating IBT evidence` |
| `objective.py` | ~1398 | `diff_ramp penalty reduced... calibration evidence` |
| `solve.py` | ~272 | `aero_compression overrides intentionally omitted — see pipeline/produce.py note` |

No `FIXME`, `HACK`, `XXX`, `WORKAROUND`, `TEMP`, or `KLUDGE` comments were found in any solver file.

---

## 14. Fallback & Default Value Risks

### High-Risk Defaults (Could Mask Problems)

| Module | Default | Risk |
|--------|---------|------|
| `solve.py` | Generic track profile generation | Missing track-specific data produces plausible but potentially wrong solver inputs |
| `solve.py` | Broad `try-except Exception` around optional analyses | Silently swallows errors in stint, sector, sensitivity, etc. |
| `heave_solver.py` | `v_p99` fallback to non-clean shock velocity | Over-estimates excursion (includes kerb events) |
| `objective.py` | Cached default `AeroSurface` | Wrong wing angle data if wing not specified |
| `sensitivity.py` | `m_eff_front=228.0, m_eff_rear=2395.3` | Hardcoded BMW masses used regardless of car |
| `coupling.py` | All coupling gains | BMW Sebring calibrated, applied universally |
| `bayesian_optimizer.py` | `_score()` camber targets `-3.5F/-2.5R` | BMW-specific targets in nominally car-agnostic optimizer |
| `diff_solver.py` | `BMW_DEFAULT_CLUTCH_PLATES = 6` | May not match other cars' defaults |
| `damper_solver.py` | Hardcoded ζ targets | Derived for GTP characteristics; may not apply to non-GTP |

### Low-Risk Defaults

| Module | Default | Notes |
|--------|---------|-------|
| `scenario_profiles.py` | `single_lap_safe` | Safe default scenario |
| `modifiers.py` | Zero-offset modifiers | Conservative no-change default |
| `candidate_ranker.py` | Neutral score if no predictions | Doesn't penalize absence of data |
| `legal_space.py` | scipy Sobol fallback to random | Still covers the space, just less efficiently |

---

## 15. Production Path Map

```
CLI / Pipeline Entry
    ↓
solve.py::run_solver() OR pipeline/produce.py
    ↓
solve_chain.py::run_base_solve(inputs)
    ↓
    ├─── [BMW/Sebring] → full_setup_optimizer.py::optimize_if_supported()
    │         ↓
    │    Load BMWSebringSeed → scipy SLSQP optimize → 6-step eval per seed
    │         ↓
    │    Best seed → SolveChainResult
    │
    └─── [All other cars] → solve_chain.py::_run_sequential_solver()
              ↓
         Step 1: rake_solver.py     → RakeSolution
         Step 2: heave_solver.py    → HeaveSolution
              ↓ reconcile_ride_heights()
         Step 3: corner_spring_solver.py → CornerSpringSolution
              ↓ reconcile_ride_heights() (again)
         Step 4: arb_solver.py      → ARBSolution
         Step 5: wheel_geometry_solver.py → WheelGeometrySolution
              ↓ reconcile_ride_heights() (final)
         Step 6: damper_solver.py   → DamperSolution
              ↓ reconcile heave↔damper if heave solver is telemetry-aware
    ↓
solve_chain.py::_finalize_result()
    ├── legality_engine.py::validate_solution_legality()
    ├── decision_trace.py::build_parameter_decisions()
    └── predictor.py::predict_candidate_telemetry()
    ↓
[BMW/Sebring only] bmw_rotation_search.py::search_rotation_controls()
    ↓
supporting_solver.py::SupportingSolver.solve()
    ├── brake_solver.py::BrakeSolver.solve()
    ├── diff_solver.py::DiffSolver.solve()
    ├── _solve_tc()
    ├── _solve_pressures()
    └── _solve_context()
    ↓
[Optional: --free / --legal-search]
legal_search.py::run_legal_search()
    ├── legal_space.py::LegalSpace.from_car()
    ├── _generate_family_seeds() [Sobol + edge families]
    ├── _evaluate_candidates()
    │     ├── objective.py::ObjectiveFunction.evaluate()
    │     └── legality_engine.py::validate_candidate_legality()
    └── OR grid_search.py::GridSearchEngine.run() [exhaustive/maximum]
    ↓
[Pipeline only] candidate_search.py::generate_candidate_families()
    ├── "incremental" family
    ├── "compromise" family
    └── "baseline_reset" family
    ↓
Output: .sto file, JSON report, engineering report
```

### Module Production Status Summary

| Module | Production | Notes |
|--------|-----------|-------|
| `__init__.py` | Yes | Package init |
| `solve.py` | Yes | CLI entry |
| `solve_chain.py` | Yes | Core chain |
| `rake_solver.py` | Yes | Step 1 |
| `heave_solver.py` | Yes | Step 2 |
| `corner_spring_solver.py` | Yes | Step 3 |
| `arb_solver.py` | Yes | Step 4 |
| `wheel_geometry_solver.py` | Yes | Step 5 |
| `damper_solver.py` | Yes | Step 6 |
| `objective.py` | Yes | Scoring |
| `predictor.py` | Yes | Telemetry prediction |
| `candidate_ranker.py` | Yes | Candidate scoring |
| `legal_space.py` | Yes | Search manifold |
| `legality_engine.py` | Yes | Legality checks |
| `legal_search.py` | Yes | Manifold search |
| `candidate_search.py` | Yes | Family generation |
| `grid_search.py` | Yes | Exhaustive search |
| `scenario_profiles.py` | Yes | Scenario weights |
| `modifiers.py` | Yes | Feedback loop |
| `decision_trace.py` | Yes | Change tracing |
| `bmw_coverage.py` | Yes (BMW) | Parameter coverage |
| `bmw_rotation_search.py` | Yes (BMW) | Rotation optimizer |
| `full_setup_optimizer.py` | Yes (BMW) | Constrained optimizer |
| `supporting_solver.py` | Yes | Brake/diff/TC/pressure |
| `brake_solver.py` | Yes | Brake bias |
| `diff_solver.py` | Yes | Differential |
| `learned_corrections.py` | Yes | Learner bridge |
| `session_database.py` | Yes | k-NN database |
| `heave_calibration.py` | Yes | Empirical heave model |
| `setup_fingerprint.py` | Yes | Setup hashing |
| `sensitivity.py` | Optional | Constraint analysis |
| `coupling.py` | Optional | Coupling reporting |
| `laptime_sensitivity.py` | Optional | Sensitivity model |
| `stint_model.py` | Optional | Fuel burn model |
| `stint_reasoner.py` | Yes | Stint-aware solve |
| `setup_space.py` | Optional | Space exploration |
| `corner_strategy.py` | Optional | Per-corner strategy |
| `sector_compromise.py` | Optional | Sector analysis |
| `uncertainty.py` | Optional | Uncertainty bands |
| `validation.py` | Optional | Predict-validate loop |
| `bayesian_optimizer.py` | **No** | Research only |
| `iterative_solver.py` | **No** | Research only |
| `explorer.py` | **No** | Research only |
| `multi_speed_solver.py` | **No** | Research only |
