# GTP Setup Builder — Physics-Based Setup Calculator for iRacing GTP/Hypercar

## Project Goal
Build a physics-first setup solver for iRacing's GTP/Hypercar class that searches only legal garage states and explains why a setup should work. The current authoritative implementation target is BMW M Hybrid V8 at Sebring International Raceway; Porsche 963 at Algarve is calibrated for Steps 1-5 (Step 6 needs `zeta_is_calibrated=True`). Ferrari, Cadillac, and Acura paths remain partial or exploratory until more telemetry and garage-truth coverage exists.

## Current Codebase Status (2026-04-11)

- **Full codebase audit — 25 failing tests fixed, 6 production bugs corrected (2026-04-11):** Codex review identified broken contracts (Ferrari setup writer, track path resolution, Acura registry gaps). Fixed in PR #57: (1) `output/setup_writer.py` — `validate_and_fix_garage_correlation` now runs **before** Ferrari index conversion; fixes Ferrari HeaveSpring writing `"8"` instead of `"3"`. (2) `output/garage_validator.py` — `_clamp_step3` now guards against snapping index-space values to physical discrete OD values (the two domains are incompatible for Ferrari — range 0-18 indices vs discrete 19.99-23.99 mm); also uses `min()` not `[0]` for robustness. (3) `car_model/registry.py` — `track_slug()` no longer uses `_TRACK_ALIASES`; only `track_key()` does. All garage model files on disk are named `sebring_international_raceway.json` not `sebring.json`; the alias-based slug was causing `GarageModelBuilder` to write/read from the wrong path. (4) `car_model/cars.py` — Ferrari `torsion_arb_coupling` `0.15 → 0.0`; measured LLTD is empirically constant (range 0.508–0.514, σ=0.0016) regardless of bar changes — coupling is negligible. (5) `solver/predictor.py` — `rear_power_slip_p95` backward-compat alias added on `PredictedTelemetry`. (6) `car_model/setup_registry.py` — 9 missing Acura settable fields added (`front_roll_hs_slope`, `rear_3rd_{ls,hs}_{comp,rbd}`, `front_roll_spring_nmm`, `front_roll_perch_mm`, `front_arb_setting`, `rear_spring_nmm`). Test fixes: support tier expectations updated to actual session counts; Porsche rear RH mean tolerance 0.50→0.75mm (honest post-overfitting model R²=0.605); data-dependent tests now skip gracefully when observation files absent from checkout. Result: **295 passed, 17 skipped, 0 failures** (was 25 failures).

## Previous Status (2026-04-10)

- **Physics-aware feature pools with universal-pool fallback (2026-04-11):** Forward selection in `_select_features()` is physics-blind — it picks whatever feature minimizes LOO RMSE. With small datasets (8–36 setups), this causes **cross-axis pollution**: Ferrari `front_ride_height` was picking `inv_rear_spring` (coefficient **-21934**) and `rear_spring` for FRONT RH; BMW `front_shock_defl_static` was picking `fuel_x_inv_third` and `rear_camber`; Porsche `front_ride_height` was picking 6/12 features from the rear axis. Fix: split `_UNIVERSAL_POOL` into `_FRONT_POOL` (front-axis + global features only) and `_REAR_POOL` (rear-axis + global), then route each `_fit_from_pool()` call to the physics-aware pool. **Critical addition:** because per-output pools alone CAUSED regressions on Porsche (LOO 3x→68x) and Acura (R² 0.75→0.09) where cross-axis features were serving as effective regularization or genuine chassis-flex coupling, `_fit_from_pool()` now also accepts `fallback_pool=_UNIVERSAL_POOL` and keeps whichever fit has the lower LOO RMSE. Result: Ferrari `front_ride_height` R²=0.50→**0.72**, Ferrari `front_shock_defl_static` R²=0.93→**0.97**, BMW `front_shock_defl_static` LOO ratio 3.3x→**2.4x**, Porsche/Acura unchanged (universal fell back). Universal calibration sweep: Ferrari `Front Static RH 2.15mm→1.06mm` (was FAIL), Ferrari `Heave Slider 4.16mm→2.02mm` (was FAIL), Acura `Third Defl Static 7.20mm→1.88mm` (was FAIL) — 3 pre-existing FAILs fixed, 0 new regressions, 0 new test failures.
- **🚨 Systemic overfitting fix + pipeline crash guards (2026-04-10):** `_select_features()` threshold was too loose (`n_samples >= n_features + 5`), allowing 18-feature models on as few as 23 samples. LOO/train RMSE ratios were catastrophic: Ferrari 559x, Porsche 272x, BMW 48Mx, Acura 559Mx. Fixed by aligning with the project's own `_min_sessions_for_features()` 3:1 ratio: `max_features = n_samples // 3`, threshold = `3 * n_features`. Defense-in-depth: `_fit()` now marks models uncalibrated when LOO/train > 10x despite R² ≥ 0.85. All 4 cars refit — Ferrari worst LOO/train 579x→1.7x, Porsche 272x→3.2x, BMW 48Mx→30x (1 model caught by guard), Acura 559Mx→2.7x. Additional fixes: (1) `produce.py` uses `track_key()` instead of `.split()[0]` for track name resolution ("autodromo"→"algarve"). (2) `track_support` removed from Step 1 requirements — calibration is car-dependent, not track-dependent. (3) `garage_validator.py` null check moved before Ferrari index conversion. (4) `report.py` null guards for step1-step6 in CURRENT vs RECOMMENDED and HEAVE TRAVEL BUDGET sections.
- **Full codebase audit and enhancement round 2 (2026-04-10):** Deep audit continuation — 7 additional fixes on top of round 1. Key changes: (15) `candidate_search.py`: ARB blade range now uses `car.arb.rear_blade_count` instead of hardcoded BMW (1,5); added garage_ranges warning when missing for non-BMW cars. (16) `auto_calibrate.py:_fit()`: underdetermined system guard — rejects fits where n_samples ≤ n_parameters (returns `is_calibrated=False`). (17) `bmw_coverage.py:_car_name()` default changed "bmw"→"unknown" to prevent silent BMW assumption. (18) `produce.py`: 3 remaining silent exception handlers now log (calibration load, track profile comparison, veto clusters). (19) `auto_calibrate.py`: 4 remaining silent exception handlers now log. (20) `objective.py`: parallel_wheel_rate ×0.5 documented (per-corner = axle_rate/2). (21) `candidate_search.py`: logger added for BMW-fallback warnings.
- **Full codebase audit and enhancement round 1 (2026-04-10):** 3-agent audit (solver workflow, calibration system, code quality) — 20 findings, 0 critical bugs. Solver physics and 6-step workflow are correct. Key fixes: (1) Aero balance over-correction in coupling refinement removed (`solve_chain.py:832`). (2) `zeta_is_calibrated` default fixed from True→False in `damper_solver.py:476`. (3) Tyre vertical rate warning added to `objective.py` when excursion degrades to suspension-only. (4) LLTD offset bounds-checked to [0.30, 0.75] in `arb_solver.py`. (5) Phantom Porsche roll damper backward-compat fixed — only applies when `has_roll_dampers=True`. (6) Ferrari rear torsion 3.5x error now gated as `uncalibrated` (blocks Step 3). (7) Speed-dependent LLTD gap eliminated (120-180 kph → unified 150 kph boundary). (8) 11 `except Exception: pass` handlers in `solve_chain.py` replaced with `logger.debug()`. (9) Auto-calibrate overfit warning: LOO vs training RMSE check + sample-to-feature ratio warning. (10) Confidence weight property added to `StepCalibrationReport` (1.0/0.7/0.5/0.0) and surfaced in JSON output. (11) Hardcoded Windows paths removed from tests. (12) Cadillac calibration stubs added. (13) `decision_trace.py` None handling fixed. (14) Ferrari setup_writer fallback now warns.
- **Codebase audit and enhancement (2026-04-09):** Full 3-agent audit (solver workflow, calibration system, code quality). Key fixes: (1) `objective.py` tyre_vertical_rate_nmm was referencing a non-existent CarModel field — always None, meaning tyre compliance was never included in excursion calculations. Now uses per-axle `tyre_vertical_rate_front/rear_nmm`. (2) Calibration gate cascade fixed: Step 5 now depends on Step 4 (was Step 3), matching actual data flow where Step 5 consumes `step4.k_roll_total`. (3) `CornerSpringSolution.rear_wheel_rate_nmm` property added — eliminates 8 manual MR^2 conversion sites. (4) Weak-upstream propagation: downstream steps now know when input data has weak calibration. (5) Ferrari rear torsion 3.5x error now gated as `weak` in calibration gate. (6) Dead LLTD proxy code removed. (7) 19MB repomix-output.xml + 486 generated JSON files removed from git. (8) BMW-default fallbacks replaced with direct attribute access in 12 solver files.
- Workflow map: `IBT -> track/analyzer -> diagnosis/driver/style -> calibration_gate -> solve_chain/legality -> driver-anchor pass -> report/.sto -> webapp`
- **🚨 LLTD phantom proxy bug found and fixed (2026-04-08):** The field `lltd_measured` stored in `data/calibration/<car>/models.json` and consumed as `measured_lltd_target` was actually `analyzer/extract.py:roll_distribution_proxy` — a **geometric constant** (`= front_RH_diff × tw_f² / total_moment`) that collapses to `t_f³/(t_f³+t_r³)` for a rigid chassis and is **insensitive to spring stiffness**. Verified across 5 Porsche/Algarve IBTs with rear stiffness varying 100–300%: proxy varied 0.5047→0.5056 (spread **0.09 pp**). A real LLTD measurement would shift 5–15 pp. The "11 pp model gap" the ARB solver was chasing was apples-to-oranges. Fix: `auto_calibrate.py:1360` LLTD-from-proxy block disabled, `data/calibration/porsche/models.json` cleared, Porsche `cars.py:measured_lltd_target=0.521` set explicitly from the OptimumG/Milliken physics formula. **The model's k_front/k_total computation may now be correct in physics; we have NO direct LLTD measurement from IBT and cannot disambiguate without true wheel-force telemetry.** See "LLTD epistemic gap" in Known Limitations.
- **Driver-anchor pattern (2026-04-08):** When the driver loads a setup into iRacing, the IBT session_info captures it. Several solvers now read `current_setup` and prefer driver-loaded values as soft anchors when the model's recommendation is within tolerance OR when the model is admittedly broken/unverifiable. This is **explicit, provenance-tracked, and never lap-time-driven** — see Key Principle 11. Anchors live in: `solver/heave_solver.py` σ-cal sticky (front_heave + rear_third), `solver/corner_spring_solver.py` direct R_coil, `solver/arb_solver.py` LLTD-fallback ARB blade, `solver/diff_solver.py` coast/drive/preload, `solver/supporting_solver.py` TC gain/slip, `solver/candidate_search.py` skip-scale-when-anchored guard.
- **σ-calibration architecture (2026-04-08):** `solver/heave_solver.py:min_rate_for_sigma()` now accepts `current_rate_nmm` + `current_meas_sigma_mm` (driver-loaded rate + IBT-measured rear/front_rh_std). Computes `cal_ratio = meas_σ / model_σ_at_current_rate` (clamped [0.5, 2.0]) and translates the user σ-target to model space. A **sticky pre-check** returns the current rate when its model σ ≤ effective target + 0.05 mm — this prevents 1-step gradient drift. The σ MODEL is still physics; the TARGET is driver-anchored. Validated against Porsche/Algarve newest IBT (driver rate=160, σ_meas=7.6, model_σ=7.34, cal_ratio=1.036, sticky returns 160 exactly).
- **Per-axle roll damper architecture (2026-04-08):** `DamperModel` now carries `has_front_roll_damper` and `has_rear_roll_damper` flags (in addition to `has_roll_dampers`). Porsche 963 (Multimatic) has FRONT roll damper but NO rear roll damper — rear roll motion is implicit in the per-corner LR/RR shocks. Acura ARX-06 (ORECA) has BOTH. Setup writer (`output/setup_writer.py:1069`) and damper solver (`solver/damper_solver.py:790`) gate on these flags so Porsche stops emitting phantom `CarSetup_Dampers_RearRoll_*` XML IDs that don't exist in the iRacing schema. Backward-compat: cars with `has_roll_dampers=True` and neither per-axle flag set assume both (legacy Acura).
- **Strict calibration gate (2026-04-07):** `car_model/calibration_gate.py` classifies every subsystem as `calibrated`, `weak`, or `uncalibrated` and surfaces R² for every regression model. R² thresholds: `R2_THRESHOLD_BLOCK = 0.85`, `R2_THRESHOLD_WARN = 0.95`. The gate distinguishes:
  - **`calibrated`**: real measurement, R² ≥ 0.85 OR auto-cal validated. Step runs cleanly.
  - **`weak`**: R² < 0.85 OR manual override that auto-cal *contradicts*. Step still runs (legacy call sites assume steps exist) but is flagged `[~~]` and a `WEAK CALIBRATION DETECTED` banner is printed prominently. JSON output carries `calibration_provenance` and `calibration_weak_steps`.
  - **`uncalibrated`**: no measurement at all. Step blocks and outputs CLI calibration instructions.
  - **Cascade rule:** only TRUE blocks (uncalibrated, dependency-blocked) propagate to downstream steps. Weak blocks do NOT cascade. Dependency chain: `{2→1, 3→2, 4→3, 5→4, 6→3}` — Step 5 (Geometry) cascades from Step 4 (ARBs, because geometry uses `step4.k_roll_total`), Step 6 (Dampers) cascades from Step 3 (wheel rates).
- **Compliance physics (2026-04-07):** Static ride heights and deflections under aero load follow spring **compliance (1/k)**, not stiffness (k). The RH model and deflection model now use `1/heave`, `1/rear_third`, `1/rear_spring` features. For Porsche this took rear RH R² from **0.61 → 0.94**, deflection R² from **0.67 → 0.97**, with sub-half-mm prediction error across the operating range. BMW uses linear terms (its data is fit by a different functional form). Both forms coexist in the same `RideHeightModel`/`DeflectionModel` classes.
- **Provenance tracking (2026-04-07):** `CalibrationGate.provenance()` returns a JSON-friendly dict mapping every subsystem to `{status, source, confidence, r_squared, data_points, warnings}`. The pipeline embeds this in JSON output as `calibration_provenance` so the user can audit exactly where each value came from. The pipeline prints a `CALIBRATION CONFIDENCE — provenance per subsystem` block on every run.
- **Silent fallbacks partially removed (2026-04-07, extended 2026-04-09):** Dangerous `getattr(car, "field", bmw_default)` patterns in core solver steps have been replaced with direct attribute access. Files cleaned in Phase 1 (2026-04-07): `solver/objective.py`, `solver/sensitivity.py`, `solver/damper_solver.py`, `solver/stint_model.py`, `solver/rake_solver.py`, `solver/arb_solver.py`. Phase 2 (2026-04-09): `solver/legal_space.py` (BMW spring refs), `solver/diff_solver.py` (preload), `solver/modifiers.py` (heave minimum), `solver/heave_solver.py` (track fields), `solver/corner_spring_solver.py` (canonical_name), `solver/objective.py` (tyre_vertical_rate per-axle fix, vortex_excursion_pctile, torsion_arb_coupling), `car_model/calibration_gate.py` (weak_block direct access). **Remaining:** ~700 `getattr` calls in `solver/candidate_search.py` (188), `solver/bmw_rotation_search.py` (113), `solver/bmw_coverage.py` (78), `pipeline/reason.py` (69), and others. Most are legitimate optional-feature checks (car-type branching, sub-model access, telemetry field defaults), not physics-value fallbacks. The BMW-specific rotation/coverage files only run for BMW and are not cross-car risks.
- **Regression test safety net (2026-04-06):** `tests/test_setup_regression.py` runs the full pipeline against `tests/fixtures/baselines/bmw_sebring_baseline.sto` and `tests/fixtures/baselines/porsche_algarve_baseline.sto`. Every code change is verified to either preserve or intentionally update these fixtures. To regenerate after an intentional change, see the docstring in the test file.
- Scenario engine: `solver/scenario_profiles.py` defines `single_lap_safe`, `quali`, `sprint`, and `race`, and those profiles now drive `pipeline/produce.py`, `pipeline/reason.py`, `solver/solve.py`, preset comparison, and the webapp.
- Legal-manifold search: `--free`, `--explore-legal-space`, and `--legal-search` now mean "start from the pinned physics solve and search the full legal setup manifold". Accepted candidates must pass setup-registry legality, garage-output validation, and telemetry sanity checks. Legal search is gated on all 6 steps being present (not blocked by calibration).
- Current BMW/Sebring evidence: `99` observations, `~97` non-vetoed. Post-fix Pearson `~0.226`, Spearman `~-0.298`. Objective is improving but not yet authoritative.
- **Current calibration status (2026-04-10, post-overfitting-fix):**
  - **BMW/Sebring**: `calibrated` (6/6 steps run cleanly, 9 unique setups, 3 features/model, garage RMSE < 0.09mm). ARB has medium-confidence hand-calibration.
  - **Porsche/Algarve**: `calibrated` (5/6 steps — Step 6 blocked: `damper_zeta` uncalibrated in car model, needs `zeta_is_calibrated=True`). 36 unique setups, 7-12 features/model. Front RH: R²=0.999 LOO=0.078mm. Rear RH: R²=0.605 (weak, 7 features — honest after overfitting fix, was 0.983 with 18 overfit features). Aero compression from 24 sessions, LLTD target = **0.521 from OptimumG physics formula**.
  - **Ferrari/Hockenheim**: `partial` (Step 1 runs with weak RH model, Steps 2-6 blocked by `spring_rates` uncalibrated). 23 unique setups, 6-7 features/model. Front RH: R²=0.501 (honest after overfitting fix, was 0.999 with 18 overfit features — model needs more data). Garage RMSE 0.09-0.82mm across outputs. 6 contaminated BMW data points removed (2026-04-10).
  - **Acura/Hockenheim**: `partial` (Steps 1-3 runnable, Steps 4-6 blocked). 8 unique setups, 2 features/model. RH < 0.11mm, some deflections limited by rear torsion bar architecture.
  - **Cadillac/Silverstone**: `no data` (0 calibration points).
  - **Garage prediction architecture (2026-04-10):** `DirectRegression` class in `car_model/garage.py` evaluates fitted regressions directly from `GarageSetupState`, bypassing `DeflectionModel`'s rigid coefficient interface. Physics feature pool: 20 features (linear + compliance 1/k + pushrod² + fuel×compliance). `GarageSetupState.from_current_setup(setup, car=car)` handles indexed-car decoding (Ferrari/Acura indices → N/mm). See `CALIBRATION_GUIDE.md` for how to calibrate new cars.
- Current source-of-truth reports: `docs/repo_audit.md`, `docs/overhaul_plan_2026_04_06.md`, `validation/objective_validation.md`, `validation/calibration_report.md`.
- **Team tool deployed (2026-03-27):** Server live at `https://ioptimal-server-27191526338.us-central1.run.app`, team "SOELPEC Precision Racing" created (invite code `5a1c520b`), desktop app packaged at `dist/IOptimal/IOptimal.exe`. All 18 bugs fixed (12 original + 6 deployment). See `docs/team_tool_next_steps.md` for full deployment reference.
- **Acura ARX-06 onboarded (2026-03-30):** ORECA LMDh chassis with heave+roll damper architecture (not per-corner). Rear torsion bars, diff ramp angles, synthesized corner shocks from heave±roll telemetry. Pipeline functional end-to-end. Steps 1-3 runnable (aero compression calibrated, spring_rates calibrated), Steps 4-6 blocked by calibration gate (ARB/LLTD/geometry/damper uncalibrated). See `skill/per-car-quirks.md` Acura section for full calibration status.

## Architecture

### Core Modules

#### 1. `aero_model/` — Aerodynamic Response Surface
- Parse all 33 aero map spreadsheets (5 cars × 6-9 wing angles)
- Build interpolated surfaces: DF_balance(front_RH, rear_RH, wing_angle) and L_D(front_RH, rear_RH, wing_angle)
- For any ride height + wing combination, return: front DF, rear DF, total DF, drag, L/D, DF balance
- Support querying: "what ride height gives target DF balance X at wing Y?"
- Data format: rows = front RH (25-50mm), columns = rear RH (5-50mm), values = DF balance % and L/D

#### 2. `track_model/` — Track Demand Profile
- Parse IBT files to extract track characteristics:
  - Surface frequency spectrum (shock velocity histogram per sector)
  - Braking zone locations, entry speeds, deceleration demands
  - Corner speeds, lateral g demands, radius estimates
  - Speed profile (% of lap in speed bands)
  - Kerb locations and severity (ride height spike detection)
  - Elevation changes (from vertical g)
- Output a TrackProfile object that any solver can query

#### 3. `car_model/` — Vehicle Physical Model
- Per-car parameter definitions with valid ranges, units, and constraint relationships
- Mass, weight distribution, CG height, wheelbase, track width
- Suspension motion ratios (spring-to-wheel rate conversions)
- Tyre load sensitivity curves (derived from telemetry: grip vs vertical load)
- Parameter name mappings (BMW uses "TorsionBarOD", Ferrari uses indexed values, Porsche has roll springs)
- Hybrid system characteristics (deployment speed, power, front/rear)
- **Calibration gate** (`calibration_gate.py`): per-car, per-subsystem calibration status tracking. Checks whether each solver step's required subsystems are calibrated from real measured data. Blocked steps output calibration instructions instead of setup values. This enforces the rule: **never output a setup value from an uncalibrated model**.

#### 4. `solver/` — Constraint Satisfaction Engine
Follows the 6-step workflow. Each step has constraints and an objective:

**Step 1: Rake/Ride Heights**
- Input: target DF balance, car aero map, track speed profile
- Constraint: DF balance must match target at the track's median high-speed cornering RH
- Constraint: front RH must stay above vortex burst threshold for 99% of clean-track samples
- Objective: maximize L/D while meeting balance target
- Output: front RH, rear RH, pushrod offsets

**Step 2: Heave/Third Springs**
- Input: target ride heights from Step 1, track surface spectrum, car mass + aero loads
- Constraint: clean-track bottoming events < threshold (e.g., 5 per lap)
- Constraint: ride height variance (σ) below target at speed
- Objective: softest spring that meets bottoming constraint (maximize mechanical grip)
- Output: front heave rate, rear third rate, perch offsets

**Step 3: Corner Springs**
- Input: car mass, target roll stiffness distribution, track bump severity
- Constraint: combined roll + heave stiffness must control ride height under lateral load
- Constraint: must not bottom under combined lateral + longitudinal + vertical loading
- Objective: balance mechanical grip vs platform control
- Output: corner spring rates

**Step 4: ARBs**
- Input: target LLTD, car weight distribution, tyre load sensitivity
- Constraint: LLTD should be ~5% above static front weight distribution (OptimumG baseline)
- Objective: neutral steady-state cornering balance at the track's characteristic speed
- Output: front ARB, rear ARB baseline, recommended live RARB range

**Step 5: Wheel Geometry**
- Input: tyre model, corner speeds, lateral loads
- Constraint: camber must optimize contact patch across the roll range
- Constraint: toe must balance turn-in response vs straight-line drag/heat
- Output: camber F/R, toe F/R

**Step 6: Dampers**
- Input: track surface spectrum, spring rates, target transient response
- Constraint: p99 shock velocity should be controlled (not causing platform instability)
- Constraint: rebound/compression ratio ~2:1 at equivalent velocities
- Objective: fastest weight transfer rate that doesn't cause oscillation
- Output: all damper clicks (LS/HS comp/rbd, slope)
- NOTE: damper effects are speed-dependent. Low-speed corners and high-speed corners may need different reasoning.

**Supporting Parameters** (`solver/supporting_solver.py`):
- Brake bias: weight transfer baseline + driver trail braking adjustment + measured slip correction
- Diff preload: traction demand × driver throttle style + body slip correction (5–40 Nm)
- Diff ramps: coast from trail braking depth, drive from throttle progressiveness
- TC: gain/slip from rear slip ratio + driver consistency
- Tyre pressures: targeting 155–170 kPa hot window from measured hot data

**Solver Modifiers** (`solver/modifiers.py`):
- Feedback loop: diagnosis + driver style → adjust solver targets before physics runs
- DF balance offset (from speed gradient diagnosis)
- LLTD offset (from understeer/oversteer diagnosis)
- Heave floor constraints (from bottoming diagnosis)
- Damper click offsets + ζ scaling (from settle time diagnosis + driver smoothness)

**Aero Gradients** (`aero_model/gradient.py`):
- Central-difference ∂(DF balance)/∂(RH) and ∂(L/D)/∂(RH) at operating point
- Aero window: ± mm before 0.5% balance shift
- L/D cost of ride height variance (second-order curvature analysis)

#### 5. `analyzer/` — Telemetry Analysis & Diagnosis
- `extract.py` — Extract 60+ measured quantities from IBT (ride heights, shock vel, understeer, body slip, tyre thermals)
- `diagnose.py` — Identify handling problems from physics thresholds (6 priority categories: safety → grip)
- `recommend.py` — Generate physics-based setup change recommendations
- `setup_reader.py` — Parse current garage setup from IBT session info YAML
- `segment.py` — **Corner-by-corner lap segmentation**: detects corners (|lat_g| > 0.5g), computes per-corner suspension metrics (shock vel p95/p99, RH mean/min), handling metrics (understeer, body slip, trail brake %), speed classification (low/mid/high), and time-loss delta
- `driver_style.py` — **Driver behavior profiling**: trail braking depth/classification, throttle progressiveness (R² of linear ramp), steering jerk (smoothness), lap-to-lap consistency (apex speed CV), cornering aggression (g utilization). Produces a `DriverProfile` with style classification (e.g., "smooth-consistent", "aggressive-erratic")
- `report.py` — ASCII terminal report formatting (63-char width)

#### 6. `pipeline/` — Unified IBT→.sto Setup Producer
End-to-end pipeline that connects telemetry analysis to the 6-step solver:
```
IBT → extract → segment corners → driver style → diagnose
    → aero gradients → solver modifiers → 6-step solver
    → supporting params → .sto + JSON + engineering report
```
- `produce.py` — CLI orchestrator: `python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --sto output.sto`
- `produce.py` / `reason.py` now resolve a scenario profile, keep the base physics solve as the seed, optionally run legal-manifold search, and persist the selected candidate family plus decision trace.
- `report.py` — Engineering report: driver profile, handling diagnosis, aero analysis, 6-step solution summary, supporting parameters, setup comparison (current vs produced), confidence assessment
- `__main__.py` — Entry point for `python -m pipeline`

#### 7. `output/` — Setup File Generator
- Generate iRacing .sto setup files directly (BMW-specific CarSetup_* XML IDs)
- Generate human-readable setup reports with reasoning for each parameter
- Generate comparison reports (current setup vs solver recommendation)
- `write_sto()` accepts optional supporting parameter overrides (brake bias, diff, TC, pressures) via kwargs

#### 8. `learner/` — Cumulative Knowledge System
Treats every IBT session as an experiment. Extracts structured observations,
detects deltas between sessions, fits empirical models, and accumulates
knowledge that compounds over time.

```
IBT → analyzer pipeline → Observation (structured snapshot)
    → Delta detection (vs prior session: what changed, what resulted)
    → Empirical model fitting (corrections to physics from data)
    → Insight generation (recurring patterns, trends, sensitivities)
    → Knowledge store (persistent JSON in data/learnings/)
```

- `knowledge_store.py` — JSON-based persistent storage (observations, deltas, models, insights)
- `observation.py` — Extracts structured observation from one IBT analysis
- `delta_detector.py` — Compares consecutive sessions, finds setup→effect causality
- `empirical_models.py` — Fits lightweight regressions from accumulated data
- `recall.py` — Query interface: "what do we know about X?", corrections for solver
- `ingest.py` — CLI entry point: `python -m learner.ingest --car bmw --ibt session.ibt`
  - `--all-laps`: ingest every valid lap as a separate observation (1 IBT → N observations)

Key features:
- **Controlled experiment detection**: if only one solver step changed between sessions,
  causal confidence is high. Multi-change sessions get lower confidence.
- **Expanded causal knowledge**: `KNOWN_CAUSALITY` covers ~40 setup→effect pairs across
  all 6 solver steps plus supporting parameters. Unknown relationships are dropped (not
  stored at low confidence). Reverse-direction entries auto-generated.
- **Prediction-vs-measurement feedback loop**: pipeline stores solver predictions in each
  observation; `fit_prediction_errors()` computes exponentially-weighted corrections from
  the gap between predicted and measured values. Solver can query via `get_prediction_corrections()`.
- **Time decay**: recent observations carry more weight (0.95^days). 30-day-old sessions
  contribute ~22% vs 95% for yesterday's. Prevents stale data from dominating corrections.
- **Experiment gating for sensitivity**: lap time sensitivity only uses deltas with ≤2 setup
  changes (single-change weighted 1.0, two-change 0.5, multi-change excluded).
- **Empirical corrections**: measured roll gradient, LLTD, m_eff, aero compression
  accumulate and the solver can query them to refine its physics predictions.
  Minimum 5 sessions required for non-prediction corrections.
- **Lap time sensitivity**: tracks which parameters had the biggest lap time effect.
- **Recurring problem detection**: flags issues that appear in >50% of sessions.
- **Damper oscillation validation**: rear shock oscillation frequency extracted from
  telemetry; if >1.5× natural frequency, damper solver bumps ζ_hs_rear (0.14→0.21).

#### 9. `watcher/` — IBT Auto-Detection
- `monitor.py` — Filesystem event handler using watchdog; file stability check (3s no-growth)
- `service.py` — WatcherService orchestrates detection → ingestion → sync queue; car auto-detection from IBT headers

#### 10. `teamdb/` — Team Database & Sync
- `models.py` — SQLAlchemy 2.0 ORM (13 tables: Team, Member, Division, CarDefinition, Observation, Delta, EmpiricalModel, GlobalCarModel, SharedSetup, SetupRating, ActivityLog, Leaderboard, division_members)
- `sync_client.py` — Background push/pull with offline SQLite queue (~/.ioptimal_app/sync_queue.db), exponential backoff, 30s push / 300s pull intervals
- `aggregator.py` — Server-side empirical model fitting from team observations

#### 11. `server/` — Team REST API
- FastAPI app on Cloud Run (`server/app.py`), async SQLAlchemy with PostgreSQL (asyncpg)
- Auth: Bearer API key (SHA-256 hashed in Member.api_key_hash)
- Routes: `/api/team`, `/api/observations`, `/api/knowledge`, `/api/setups`, `/api/leaderboard`
- Deployed: `https://ioptimal-server-27191526338.us-central1.run.app`
- Dockerfile at project root (builds `server/` + `teamdb/`)

#### 12. `desktop/` — Desktop App
- `app.py` — Orchestrates watcher + sync + webapp; CLI entry point with `--no-tray`, `--bulk-import`
- `config.py` — AppConfig dataclass persisted to JSON (%APPDATA%/IOptimal/config.json)
- `tray.py` — System tray icon via pystray (pause watcher, sync now, status, quit)
- Packaged via PyInstaller: `dist/IOptimal/IOptimal.exe` (177 MB)

### Data Files
- `data/aeromaps/` — Raw xlsx files (provided)
- `data/aeromaps_parsed/` — Parsed JSON/numpy arrays
- `data/tracks/` — TrackProfile JSONs (built from IBT analysis)
- `data/cars/` — Car model definitions
- `data/telemetry/` — Reference IBT sessions for validation

### Validation Strategy
- Canonical validation lives in `validation/run_validation.py` and `validation/objective_calibration.py`.
- All evidence uses canonical registry-backed setup mappings (`validation/observation_mapping.py`) instead of stale aliases.
- Current authority is BMW/Sebring only: `73` observations, `72` non-vetoed, with objective correlation still weak enough that "optimal" claims are not yet allowed.
- Validation reports now track score correlation, top parameter correlations, signal usage, claim audit status, and scenario-aware recalibration metrics including holdout performance.
- Support tiers are explicit and enforced in documentation: BMW/Sebring `calibrated` (6/6), Porsche/Algarve `calibrated` (5/6, Step 6 blocked), Ferrari/Hockenheim `partial` (1/6), Acura/Hockenheim `partial` (3/6), Cadillac/Silverstone `no data`.

### Tech Stack
- Python 3.11+
- numpy/scipy for interpolation and optimization
- openpyxl for xlsx parsing
- Possibly React frontend for visualization (later phase)

### Key Principles
1. Physics first, not pattern matching. Every parameter value must be justified by a physical constraint.
2. The solver follows the 6-step workflow ALWAYS. No jumping to dampers before rake is set.
3. Speed-dependent reasoning. The same symptom at different speeds may require different solutions.
4. Uncertainty is OK. If the solver can't determine a value from physics, it says so and gives a range.
5. Validate against telemetry. Every prediction should be testable with an IBT file.
6. Driver-adaptive: different drivers on the same track should produce different setups.
7. **Calibrated or instruct, never guess.** If a model is not calibrated from real measured data for a specific car, the output must be calibration instructions — not a value derived from another car's coefficients, not a physics estimate, not a default presented as a recommendation. The calibration gate (`car_model/calibration_gate.py`) enforces this at every solver step.
8. **No silent fallbacks.** Every value the solver uses must come from one of: (a) measured data with R² ≥ 0.85, (b) first-principles physics computation, (c) car-specific hand calibration with explicit warning. The user explicitly asked for "no fallbacks to baselines or hardcoded values" — this is enforced via direct attribute access (no `getattr` with hardcoded defaults), strict gate classification, and the `WEAK CALIBRATION DETECTED` banner.
9. **Provenance over output.** Every solver run prints a `CALIBRATION CONFIDENCE` block that lists every subsystem with its source, R² (where applicable), and confidence label. JSON output carries the full provenance dict so the user can audit any value.
10. **Compliance physics for static loads.** Static ride heights and deflections under aero load follow `defl ∝ F/k` (compliance), not stiffness. Use `1/k` features in regressions for these models. This was the single biggest accuracy improvement of 2026-04-07.
11. **Driver-anchor as physics fallback, never lap-time.** When an internal model is admittedly broken (e.g., LLTD k_front/k_total can't be ground-truthed without wheel-force telemetry) OR when the model agrees with the driver-loaded value within tolerance, prefer the driver-loaded value as the recommendation **with explicit provenance** (`anchored to driver-loaded X`). This is NOT lap-time-driven — anchors trigger on σ-measurement, model self-test, or close-tolerance agreement, never on `if lap_time < X:`. The driver loading their best setup before each session creates an IMPLICIT lap-time signal, but the anchor logic does not consume lap_time. See `feedback_no_laptime_setup_selection.md` and the Phase 6/7 implementation in `solver/{heave,corner_spring,arb,diff,supporting}_solver.py`. **Honest naming**: when the anchor fires, the output line in step4/step6/etc. says "anchored to driver-loaded" so a reader can audit which values are model-derived vs driver-derived.

### Important Implementation Details

**Spring rate conventions (critical):**
- Front torsion bar: `CornerSpringSolution.front_wheel_rate_nmm` is already a wheel rate (MR baked into C*OD^4 formula, `front_motion_ratio=1.0` for all cars)
- Rear coil spring: `CornerSpringSolution.rear_spring_rate_nmm` is a RAW SPRING RATE. Must multiply by `car.corner_spring.rear_motion_ratio ** 2` to get wheel rate before passing to ARB/geometry/damper solvers.
- The ARB solver's `_corner_spring_roll_stiffness()` now expects wheel rates for both axles (no internal MR conversion).

**Aero compression is speed-dependent:**
- `AeroCompression` stores reference values at `ref_speed_kph` (230 kph)
- Use `comp.front_at_speed(speed)` / `comp.rear_at_speed(speed)` for V² scaling
- The rake solver and `solver/objective.py` use `track.aero_reference_speed_kph` (V²-RMS over speed bands ≥100 kph), NOT `median_speed_kph`. Median under-predicts compression by ~3 mm because compression is dominated by high-speed sections. Validated 2026-04-07 against 4 Porsche/Algarve IBTs: V²-RMS=200 kph for Algarve gives compression matching IBT-measured to within 1 mm both axles.

**solution_from_explicit_offsets must honor caller-provided static (2026-04-07):**
- `solver/rake_solver.py:solution_from_explicit_offsets()` previously recomputed static_front from `garage_model.predict()` with **baseline springs** (heave=180 default, etc.) regardless of what the caller had already chosen. When `materialize_overrides` (in solve_chain.py, called by the candidate generator) passed both `front_pushrod_offset_mm` AND `static_front_rh_mm` from a base solve that pinned static_front=30, the function was overwriting static_front with the baseline-spring prediction (~32.78 for Porsche), and `reconcile_ride_heights` then used that drifted value as a NEW target. Fix: when `static_front_rh_mm`/`static_rear_rh_mm` are explicitly provided, USE THEM directly. This was the single largest fix for the front pushrod / static drift in Phase 2.

**σ-calibration architecture (heave_solver.min_rate_for_sigma):**
- The synthetic σ model (`damped_excursion_mm` energy method) does NOT match IBT-measured rear/front_rh_std exactly. For Porsche/Algarve newest IBT at driver rate=160: model σ = 7.34 mm, IBT-measured = 7.6 mm. Gradient is also slightly off.
- `min_rate_for_sigma()` accepts optional `current_rate_nmm` and `current_meas_sigma_mm` (driver-loaded rate + IBT std). It computes:
  ```
  cal_ratio = current_meas_σ / model_σ_at_current_rate    # clamped [0.5, 2.0]
  effective_meas_target = min(user_target, current_meas_σ × target_margin)   # default margin 1.05
  effective_model_target = effective_meas_target / cal_ratio    # floored at 3 mm
  ```
- Then it searches for the minimum rate where model_σ ≤ effective_model_target.
- **Sticky pre-check**: if the current rate's model_σ is within 0.05 mm of the target, return the current rate directly (snapped to 10 N/mm). This prevents the gradient mismatch from drifting the recommendation 1 step softer than driver.
- Wired through both `_run_sequential_solver` and `materialize_overrides` paths in `solver/solve_chain.py` via `front_heave_current_nmm` + `rear_third_current_nmm` parameters.
- The σ MODEL is still physics-driven; the σ TARGET is driver-anchored when available. The driver's measured σ becomes "the σ the new setup must achieve or exceed".

**LLTD calibration target — physics formula, NOT IBT (2026-04-08):**
- `analyzer/extract.py:574-599` computes `roll_distribution_proxy` (aliased as `lltd_measured`) from `(front_RH_diff × tw_f²) / (front_RH_diff × tw_f² + rear_RH_diff × tw_r²)`. **This is NOT LLTD.** It is a geometric ratio that collapses to `t_f³/(t_f³+t_r³)` for a rigid chassis and is essentially insensitive to spring stiffness.
- Verified across 5 Porsche/Algarve IBTs varying R_third 160→320 N/mm and R_coil 150→180: proxy varied 0.5047→0.5056 (spread 0.09 pp).
- `auto_calibrate.py:1360` previously stored `mean(proxy)` as `models.measured_lltd_target` and the ARB solver used it as the calibration target. The "11 pp model gap" between true model LLTD (k_front/k_total) and the proxy was apples-to-oranges.
- **Fix**: `auto_calibrate.py:1360-1395` block disabled (`if False:`), `models.measured_lltd_target = None` for cars where this was the source. `cars.py` Porsche definition sets `measured_lltd_target = 0.521` explicitly from the OptimumG/Milliken physics formula `weight_dist_front + (tyre_sens/0.20)×0.05 + speed_correction`. The arb_solver's existing physics-fallback path computes the same formula when `measured_lltd_target` is None.
- **Open epistemic gap**: we still have NO direct LLTD measurement from IBT. iRacing doesn't expose individual wheel-load channels. Without wheel-force telemetry OR a controlled per-axle ARB lap-time correlation (10+ varied sessions), we cannot disambiguate three hypotheses: (A) OptimumG rule doesn't apply to GTP/Porsche tyres, (B) driver setup is suboptimal but lap time is still good, (C) one of the model's k_roll terms has a residual physics error. The ARB solver's driver-anchor fallback (Phase 6.6) currently fires for Porsche because model LLTD (0.391) is 13 pp below the OptimumG target (0.521). The anchor preserves driver Stiff/10 with HONEST justification ("physics target unverifiable, defer to driver-loaded value"), not the previous fake "model is broken" justification.

**Per-axle roll damper architecture:**
- `DamperModel` carries `has_front_roll_damper` and `has_rear_roll_damper` flags (in addition to the legacy `has_roll_dampers` boolean).
- **Porsche 963 (Multimatic)**: Front Heave (4 channels) + Front Roll (3 channels) + Left Rear corner (5 channels) + Right Rear corner (5 channels) + Rear 3rd (4 channels) = 21 channels. **No rear roll damper** — rear roll motion is implicit in the per-corner LR/RR shocks. `has_front_roll_damper=True`, `has_rear_roll_damper=False`.
- **Acura ARX-06 (ORECA)**: Front Heave + Front Roll + Rear Heave + Rear Roll. `has_front_roll_damper=True`, `has_rear_roll_damper=True`.
- The setup writer (`output/setup_writer.py:1069`) and damper solver (`solver/damper_solver.py:790`) gate roll damper writes/computation on the per-axle flag. Backward-compat: cars with `has_roll_dampers=True` and neither per-axle flag set assume both axles (legacy Acura behavior). Before this fix, Porsche was emitting phantom `CarSetup_Dampers_RearRoll_LsDamping/HsDamping` XML IDs that don't exist in iRacing's Porsche garage schema.

**Static ride height models (RideHeightModel):**
- Front static RH is NOT sim-pinned — it varies with heave spring rate (compliance), front camber, pushrod, and perch.
- **Two functional forms coexist** in the same `RideHeightModel` class:
  - **BMW** (constant model): `front_static_rh ≈ 30.2` (LOO RMSE ≈ 0.031mm, 0 features — front RH barely varies across 9 setups)
  - **Porsche** (compliance, 12 features after overfitting fix): R²=0.9993, LOO RMSE = 0.078mm. Previously had 18 features with fake LOO=0.03mm from overfit (LOO/train was actually 271x).
- Rear model uses compliance for both spring and third spring on Porsche:
  - **BMW**: `rear = 48.96 + 0.226*pushrod + 0.139*heave_perch + 0.069*spring_perch`
  - **Porsche**: rear model now has 7 features (after overfitting fix), R²=0.605, LOO RMSE = 0.99mm. Previously 18 features with R²=0.98 but LOO/train was 85x (overfit). The honest model needs more data or different features to improve rear RH accuracy.
- The model carries both linear and compliance coefficient fields (`front_coeff_heave_nmm` AND `front_coeff_inv_heave`, `rear_coeff_third_nmm` AND `rear_coeff_inv_third`, `rear_coeff_rear_spring` AND `rear_coeff_inv_spring`). Each car uses whichever set its calibration data fits best.
- `auto_calibrate.py` feature selection now includes both `1/heave` and `1/spring` candidates and lets the regression pick whichever is non-zero. Feature selection uses a 3:1 sample-to-feature ratio (`max_features = n_samples // 3`, skip threshold = `3 * n_features`). Defense-in-depth: `_fit()` marks models uncalibrated when LOO/train > 10x despite R² ≥ 0.85.
- `apply_to_car()` zeroes ALL coefficients in `_FRONT_RH_COEFF_MAP` / `_REAR_RH_COEFF_MAP` before applying new values, so stale BMW defaults can never persist alongside fresh non-BMW calibration.
- `GarageOutputModel` was extended with `front_coeff_inv_heave_nmm`, `rear_coeff_inv_third_nmm`, `rear_coeff_inv_rear_spring_nmm` fields and uses them in both `predict_*_static_rh_raw()` and the inverse `*_pushrod_for_static_rh()` methods.
- Both models are reconciled after step2+step3 in `solver/rake_solver.py:reconcile_ride_heights()` (called from solve.py and produce.py).

**Deflection models (DeflectionModel):**
- Same compliance physics applies to spring deflection under aero load: `defl ∝ F/k`.
- `rear_spring_defl_static`, `third_spring_defl_static`, and `rear_shock_defl_static` now use `1/spring` + `1/third` + perches + pushrod features.
- For Porsche these models achieve R²=0.93-0.98 with 7-10 features (after overfitting fix). Previously showed R²=0.99+ with 18 features but LOO/train ratios of 78-95x indicated severe overfitting.
- `DeflectionModel` carries a `*_defl_direct` flag per submodel. When True, it uses the new compliance form; when False, it uses the legacy load-balance form. BMW continues to use legacy because its single-feature fits don't have compliance terms.
- `apply_to_car()` only sets `*_defl_direct=True` when the fitted model includes inverse features — avoids accidentally flipping BMW into the new path.

**Learner model ID convention:**
- Model IDs use first word of track name only: `{car}_{track_first_word}_empirical` (e.g., `bmw_sebring_empirical`)
- Both `ingest.py` and `recall.py` use `track_name.lower().split()[0]` for consistency

**Known limitations:**
- BMW/Sebring is the fully-calibrated car/track pair (6/6 steps). Porsche/Algarve has 5/6 steps (Step 6 blocked: `damper_zeta` needs `zeta_is_calibrated=True` set in car model).
- Other cars have partial calibration: Ferrari/Hockenheim 1/6 (Step 1 weak, Step 2 blocked by `spring_rates`), Acura/Hockenheim 3/6 (Steps 1-3 runnable, 4-6 blocked), Cadillac/Silverstone 0/6.
- **Garage prediction accuracy after overfitting fix (2026-04-10):** Previous claims of "<0.06mm" for Ferrari and "<0.07mm" for Porsche were on overfit models (18 features, LOO/train ratios 272-579x — models were memorizing training data). After fixing `_select_features()` threshold to 3:1 ratio and refitting: BMW <0.09mm (unchanged), Porsche front RH 0.078mm LOO but rear RH R²=0.60 (needs more data), Ferrari 0.08-0.82mm RMSE depending on output (front RH R²=0.50, needs more data). The honest models generalize to new setups; the old overfit models did not.
- The objective is improving but still not authoritative: current BMW/Sebring non-vetoed Spearman is `~-0.298` (improved from -0.06 after 2026-04-04 fixes). Holdout stability is not yet strong enough for automatic runtime weight application.
- Several BMW validation signals still lean on fallbacks for some rows (`front_excursion_mm`, `braking_pitch_deg`, `rear_power_slip_p95`, hot pressures, lock proxies), so some supporting heuristics remain lower confidence.
- Ferrari rear torsion bar is calibrated (C=0.001282, MR=0.612, 4-point fit, max 3.2% error). Corner spring and LLTD outputs are functional but need more observations (currently 9) to validate against lap time.
- `m_eff` empirical correction uses lap-wide statistics (not filtered to high-speed straights), causing overestimation. Treat as rough indicator.
- `min_sessions=5` gate for non-prediction learned corrections. Prediction-based corrections (from solver feedback loop) need only 3 sessions since they measure specific prediction errors.
- Knowledge store has no file locking — safe for single-user CLI but not concurrent access.
- **🚨 LLTD CALIBRATION GAP (2026-04-08):** `analyzer/extract.py:lltd_measured` is a misnamed alias for `roll_distribution_proxy` — a GEOMETRIC ratio (`(front_RH_diff×tw_f²)/(...+rear_RH_diff×tw_r²)`) that is **insensitive to spring stiffness**. We have **no real LLTD measurement** from iRacing IBT (no individual wheel-load channels). The ARB solver now uses the OptimumG/Milliken physics formula as the LLTD target, with a driver-anchor fallback when the model can't reach target. To upgrade to true LLTD calibration we need EITHER (a) wheel-force telemetry from iRacing's `LF/RF/LR/RR_LoadN` channels if/when exposed, OR (b) a controlled per-axle ARB lap-time correlation across 10+ varied-blade sessions on the same track. Current Porsche LLTD target = 0.521 (physics-derived), model says 0.391 with driver setup, 13 pp gap is REAL but un-attributable.
- High-speed m_eff filtering available via `front_heave_vel_p95_hs_mps` and `front_rh_std_hs_mm` (>200 kph only) but not yet used by the solver's m_eff correction — uses lap-wide stats.
- **ARB back-solve (auto_calibrate.py):** measures total roll stiffness per ARB config from roll gradient, but cannot split front/rear individually. The `models.status['arb_calibrated']` returns `True`/`False`/`None` based on a noise-floor check (False if predicted-vs-measured deltas disagree by >20%, None if signal is below the K_total noise floor). Porsche currently sits at `None` (signal-below-noise) which the gate maps to `MEDIUM` hand-cal — not weak.
- **Driver-anchor pattern caveats:** the anchors in `solver/{heave,corner_spring,arb,diff,supporting}_solver.py` reduce the solver's ability to RECOMMEND a setup substantially different from what the driver loaded. This is by design for the rear chain (where physics is unverifiable) but means that loading a fresh/unfamiliar setup will cause the solver to re-anchor on whatever is loaded. The anchors do NOT consume lap_time directly, but the driver's selection of "best so far" creates an implicit lap-time-anchored loop. Acceptable for current use; revisit if false-positive driver anchors block real solver improvements.
- **Porsche 963 (Multimatic chassis):** Real garage ranges (heave 150–600, third 0–800, rear spring 105–280, front ARB adj 1–13, rear ARB adj 1–16, roll spring 100–320). Damper architecture: Front Heave (4 channels) + Front Roll (3 channels) + Left Rear corner (5 channels) + Right Rear corner (5 channels) + Rear 3rd (4 channels) = 21 channels total. **No rear roll damper** — `has_rear_roll_damper=False` (per-axle flag set 2026-04-08). Roll perch offset (14–16) not modeled. Individual L/R rear spring perch offsets (-150 to +150) not modeled.
- **Trailing rear pushrod gap on newest IBT (2026-04-08):** Pipeline R_pushrod = 23.5 vs driver = 18 (5 mm gap). Cascades from R_static = 50.0 vs driver 48.7 (1.3 mm gap), which in turn cascades from the rake solver's `_find_rear_for_balance` not being anchored to the IBT-measured rear dynamic RH. Same pattern fix as the other anchors would close it: add `current_rear_rh_dynamic_mm = measured.mean_rear_rh_at_speed_mm` anchor to the rake solver's rear-balance search. Estimated 30-min next-session task.
- **Acura ARX-06 (ORECA chassis):** Heave+roll damper architecture, rear torsion bars, synthesized corner
  shocks. Pipeline functional but RH targets unreliable (aero maps not calibrated for Acura). Front heave
  damper bottoms at torsion OD ≥ 14.76 mm. Roll dampers use baseline values only (no physics tuning yet).
  Torsion bar C constant borrowed from BMW — needs ORECA-specific calibration from 5+ varied garage screenshots.

## Usage

### Standalone solver (pre-built track profile):
```bash
python -m solver.solve --car bmw --track sebring --wing 17 --scenario-profile single_lap_safe --sto output.sto
```

### Full pipeline (IBT → .sto, driver-adaptive):
```bash
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --scenario-profile single_lap_safe --sto output.sto
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --lap 25 --scenario-profile quali --json output.json
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --free --scenario-profile race --sto output.sto
```

### Full pipeline with learning:
```bash
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --sto output.sto --learn --auto-learn
```

### Analyzer (diagnose existing setup):
```bash
python -m analyzer --car bmw --ibt session.ibt
```

### Learner (ingest session into knowledge base):
```bash
python -m learner.ingest --car bmw --ibt session.ibt
```

## Reference Files
- `skill/SKILL.md` — Engineering knowledge base (damper theory, ARB physics, etc.)
- `skill/per-car-quirks.md` — Car-specific verified findings
- `skill/ibt-parsing-guide.md` — IBT binary format parser
- `skill/telemetry-channels.md` — Channel reference
