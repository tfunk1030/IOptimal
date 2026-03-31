# GTP Setup Solver — Deep Codebase Audit Report

**Date:** 2026-03-31  
**Branch snapshot:** `claw-research`  
**Scope:** 206 Python files, ~80,385 lines  
**Method:** Exhaustive source read of every module, verified against runtime paths  

---

## 1. Executive Summary

This codebase is a physics-first iRacing GTP/Hypercar setup solver. It ingests telemetry (IBT files), diagnoses handling problems, runs a 6-step constrained physics solver, validates legality, and exports `.sto` setup files. The architecture is ambitious and technically deep — the problem decomposition (rake → heave → corner spring → ARB → geometry → dampers) is sound vehicle dynamics.

**However, the system has a critical structural asymmetry: it is calibrated for one car at one track (BMW M Hybrid V8 at Sebring), and the extension to other cars is fundamentally incomplete.** The Ferrari, Cadillac, Porsche, and Acura paths share the same solver code but depend on car model constants that are borrowed, estimated, or wrong. The result is that non-BMW outputs look plausible but are not physically grounded.

### Top-Level Risks

1. **Scoring function does not predict lap time.** The objective function's Spearman correlation with lap time is -0.18 (BMW/Sebring, 99 observations). Penalty terms actively *worsen* correlation. The damping component correlates in the *wrong direction* (+0.246). The system's definition of "best" is not validated.

2. **Non-BMW cars use borrowed physics constants.** Acura and Porsche use BMW's torsion bar C constant (0.0008036) despite having entirely different chassis (ORECA, Multimatic). Ferrari's heave spring decode is approximate. No non-BMW car has a garage output model for validation.

3. **The BMW/Sebring optimizer is a separate code path** (`full_setup_optimizer.py`). Non-BMW cars fall through to the sequential solver, which lacks the constrained optimization, garage-truth validation, and rotation search that BMW benefits from.

4. **Significant dead code and developer artifacts** remain in the repo: 8 root-level scripts with hardcoded Windows paths, 4 experimental solver modules behind CLI flags, naming collisions between modules.

### Most Important Next Fixes (in order)

1. Fix the scoring function — zero out all penalty weights except `lap_gain`, then recalibrate damper targets from IBT data (the current targets penalize faster setups)
2. Build garage output models for Ferrari and Acura from 5+ varied garage screenshots each
3. Calibrate Acura torsion bar C constant from ORECA chassis data
4. Remove dead root-level scripts and resolve naming collisions
5. Generalize the BMW-only optimizer path into a car-agnostic constrained search

---

## 2. Actual Production Path

### Primary Production Path

**Entry:** `python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --sto output.sto`

**File:** `pipeline/produce.py::produce()` → full orchestration:

```
1.  Parse args, resolve scenario profile (scenario_profiles.py)
2.  Load CarModel (car_model/cars.py::get_car)
3.  [Multi-IBT] → delegate to reason.py::reason_and_solve()
4.  Open IBTFile (track_model/ibt_parser.py)
5.  Apply learned corrections (solver/learned_corrections.py) if --learn
6.  Load aero surfaces (aero_model/interpolator.py)
7.  Build/load TrackProfile (track_model/build_profile.py)
8.  Extract measurements (analyzer/extract.py::extract_measurements)
9.  Apply live_control_overrides (setup_schema.py)
10. Build setup schema (analyzer/setup_schema.py)
11. Corner segmentation (analyzer/segment.py)
12. Driver style (analyzer/driver_style.py)
13. Adaptive thresholds (analyzer/adaptive_thresholds.py)
14. Diagnose (analyzer/diagnose.py)
15. Session context (analyzer/context.py)
16. Aero gradients (aero_model/gradient.py)
17. Solver modifiers (solver/modifiers.py)
18. 6-step solver:
    a. RakeSolver (solver/rake_solver.py) → ride heights
    b. HeaveSolver (solver/heave_solver.py) → heave/third springs
    c. CornerSpringSolver (solver/corner_spring_solver.py) → torsion OD, rear spring
    d. ARBSolver (solver/arb_solver.py) → LLTD targeting
    e. WheelGeometrySolver (solver/wheel_geometry_solver.py) → camber, toe
    f. DamperSolver (solver/damper_solver.py) → all damper clicks
    [Includes ride height reconciliation passes between steps]
19. SupportingSolver (solver/supporting_solver.py) → brake, diff, TC, pressures
20. [Optional] Legal manifold search (solver/legal_search.py, solver/grid_search.py)
21. Garage validation (output/garage_validator.py)
22. Write .sto (output/setup_writer.py)
23. Generate report (pipeline/report.py)
24. [Optional] Auto-learn (learner/ingest.py)
```

**Critical fork at step 18:** `solve_chain.py::run_base_solve()` calls `full_setup_optimizer.py::optimize_if_supported()`. For BMW/Sebring, this returns a constrained-optimization result from pre-calibrated seeds. For ALL other car/track combinations, it returns `None` and the sequential 6-step solver runs.

### Supported Secondary Paths

| Path | Entry | Purpose |
|------|-------|---------|
| Multi-IBT reasoning | `pipeline/reason.py::reason_and_solve()` | 9-phase pipeline for N sessions |
| Standalone solver | `python -m solver.solve --car bmw --track sebring` | No IBT required |
| Analyzer only | `python -m analyzer --car bmw --ibt session.ibt` | Diagnosis without solve |
| Comparison | `python -m comparison --car bmw --ibt s1.ibt s2.ibt` | N-session comparison + synthesis |
| Learner ingest | `python -m learner.ingest --car bmw --ibt session.ibt` | Knowledge accumulation |
| Webapp | `python -m webapp` | FastAPI local server, 3 run modes |
| Preset compare | `python -m pipeline.preset_compare` | Race/sprint/quali preset generation |
| Validator | `python -m validator --car bmw --ibt ... --setup output.json` | Solver output verification |

### Experimental / Research-Only Paths (gated by CLI flags)

| Module | Flag | Status |
|--------|------|--------|
| `solver/bayesian_optimizer.py` | `--bayesian` | Research. Simplified scoring proxy with BMW-hardcoded camber targets |
| `solver/iterative_solver.py` | `--iterative` | Experimental. Multi-pass solver with convergence damping |
| `solver/explorer.py` | `--explore` | Research. Unconstrained LHS exploration |
| `solver/multi_speed_solver.py` | `--multi-speed` | Research. 3-speed-regime analysis |

### Dead / Legacy Paths

| File | Evidence |
|------|----------|
| `run_now.py` | Hardcoded `C:\Users\VYRAL\IOptimal\` Windows path |
| `run_full_v2.py` | Hardcoded Windows path, specific IBT file |
| `run_full_pipeline.py` | Hardcoded Windows path |
| `run_full_justified.py` | Hardcoded Windows path |
| `run_exhaustive.py` | Hardcoded Windows path |
| `run_tuned_search.py` | Hardcoded Windows path |
| `run_tests.py` | Hardcoded Windows path, subset test runner |
| `test_camber.py` | One-off test, not in `tests/` dir |
| `research/ferrari_calibration_mar21.py` | One-time calibration script |
| `research/ferrari_calibration_write_docs.py` | Documentation generator |

---

## 3. How the Solver Works

### Orchestration

The solver is orchestrated by `solver/solve_chain.py::run_base_solve(inputs)`:

1. It checks `full_setup_optimizer.py::optimize_if_supported(car, surface, track)` 
   - If BMW at Sebring: runs `BMWSebringOptimizer` (constrained scipy SLSQP over pre-calibrated seeds from `data/calibration_dataset.json`)
   - Otherwise: returns `None`, falls through to sequential solver

2. Sequential solver (`solve_chain.py::_run_sequential_solver`):

**Step 1 — Rake (`rake_solver.py::RakeSolver.solve`):**
- Targets a DF balance percentage at the track's median cornering ride height
- Default strategy: pin front static RH at 30mm (GTP convention), solve rear RH for target balance
- Uses aero surface interpolation to find the rear RH that achieves target DF balance
- Outputs: front/rear dynamic RH, pushrod offsets, DF balance, L/D, vortex margin
- Fallback: grid search if `brentq` root finder fails; closest-achievable balance if target is unreachable

**Step 2 — Heave/Third Springs (`heave_solver.py::HeaveSolver.solve`):**
- Minimizes platform variance (σ of ride height at speed) while preventing bottoming
- Uses damped excursion model: `excursion = v_p99 / (2π × f_n × ζ)` where `f_n = √(k/m_eff)`
- Constraint: excursion must stay within dynamic RH margin (not touch ground)
- BMW path: uses `GarageOutputModel` for perch optimization and slider correlation
- Non-BMW path: simpler computation without garage-truth feedback
- Outputs: front heave rate, rear third rate, perch offsets, excursion data

**Step 3 — Corner Springs (`corner_spring_solver.py::CornerSpringSolver.solve`):**
- Targets a corner/heave spring frequency ratio for optimal platform response
- Uses natural frequency matching: `f_corner = √(k_corner/m_corner) / (2π)`
- Adjusts target ratio based on track surface severity
- Outputs: front torsion bar OD, rear coil spring rate, natural frequencies

**Step 4 — ARBs (`arb_solver.py::ARBSolver.solve`):**
- Targets LLTD (lateral load transfer distribution) for neutral balance
- LLTD = front_roll_stiffness / total_roll_stiffness
- Target LLTD: car model default + modifiers (from understeer diagnosis)
- Strategy: keep front ARB soft (blade 1), use rear ARB as primary live balance tool
- Outputs: front/rear ARB size + blade, LLTD analysis, RARB sensitivity

**Step 5 — Wheel Geometry (`wheel_geometry_solver.py::WheelGeometrySolver.solve`):**
- Camber: optimizes contact patch width under roll using body roll prediction
- Toe: balances turn-in response vs straight-line drag and heat
- BMW-hardcoded thermal conditioning rates: 2.4°C/lap front, 3.2°C/lap rear
- Outputs: camber F/R, toe F/R, thermal predictions

**Step 6 — Dampers (`damper_solver.py::DamperSolver.solve`):**
- Derives damping from first principles using target ζ (damping ratio)
- ζ targets are hardcoded: front LS=0.88, rear LS=0.30, front HS=0.45, rear HS=0.14
- These are the *solver* targets; the *objective function* uses IBT-calibrated values (0.68/0.23/0.47/0.20)
- **This is a discrepancy**: the solver targets and objective function targets are different numbers
- Outputs: per-corner click settings, damping coefficients, ζ ratios

**Post-solve:**
- `_finalize_result()`: validates legality, builds decision trace, predicts telemetry
- [BMW only] `bmw_rotation_search.py::search_rotation_controls()`: fine-tunes diff/torsion/geometry/RARB
- `supporting_solver.py`: brake bias, diff preload/ramps, TC, tyre pressures

### Fallback Behavior

| Solver Step | Fallback |
|-------------|----------|
| Rake (Step 1) | Grid search if brentq fails; closest balance if target unreachable |
| Heave (Step 2) | Non-clean shock velocity if clean not available; default perch |
| Corner Spring (Step 3) | Default severity ratio if track shock data missing |
| ARB (Step 4) | LLTD defaults to 0.5 if total roll stiffness is zero |
| Geometry (Step 5) | Default p95 lateral g; defaults for understeer/body slip if no measured data |
| Damper (Step 6) | Default damper coefficients; default v_hs_ref if track profile unavailable |
| Supporting | Diff fallback if DiffSolver unavailable; Ferrari passthrough for brake/TC |

### Legality / Veto Flow

**Two validation paths:**

1. **Full validation** (`legality_engine.py::validate_solution_legality`):
   - Used after 6-step solve for the base solution
   - Checks garage output model predictions (BMW/Sebring only)
   - Validates heave slider, torsion bar deflection, ride height floor
   - Returns tier: `garage_correlated` (BMW), `range_clamp` (all others), or `rejected`

2. **Fast candidate validation** (`legality_engine.py::validate_candidate_legality`):
   - Used during legal-manifold search for each candidate
   - Range checks only (no garage model evaluation)
   - Checks: spring rates within car.garage_ranges, damper clicks within bounds, rebound/comp ratios

**Veto logic in ObjectiveFunction:**
- Hard veto: bottoming margin < 0, stall margin < 0, DF balance error > 3%
- Hard-vetoed candidates get score = -1e9
- Soft penalties: slider exhaustion, RH collapse risk, LLTD deviation

**Key observation:** Legality validation is thorough for BMW (garage model checks slider position, deflections, RH correlation) but trivially permissive for non-BMW cars (range clamping only, no garage-truth validation). This means non-BMW setups can pass legality while being physically impossible in iRacing's garage.

---

## 4. How "Best" Is Chosen

### Scoring Mechanism 1: ObjectiveFunction (`solver/objective.py`)

**Canonical scorer** used by legal search, grid search, and candidate evaluation.

**Formula:**
```
total_score = w_lap_gain × lap_gain_ms
            - w_platform × platform_risk_ms
            - w_driver × driver_mismatch_ms
            - w_uncertainty × telemetry_uncertainty_ms
            - w_envelope × envelope_penalty_ms
            - w_staleness × staleness_penalty_ms
            - w_empirical × empirical_penalty_ms
```

**`lap_gain_ms` components:**
- `damping_ms`: penalty for ζ deviation from IBT-calibrated targets (0.68/0.23/0.47/0.20)
- `lltd_balance_ms`: penalty for LLTD deviation from target (uses TORSION_ARB_COUPLING = 0.25)
- `df_balance_ms`: penalty for DF balance deviation from target
- `camber_ms`: penalty for camber deviation from optimal
- `rebound_ratio_ms`: penalty for rebound/compression ratio deviation from target
- `diff_preload_ms`, `diff_ramp_ms`, `diff_clutch_ms`: differential penalties
- `arb_extreme_ms`: ARB extreme penalty (ZEROED OUT — calibration showed it hurt)
- `tc_ms`, `carcass_ms`: TC and thermal penalties

**Weight profiles** defined in `scenario_profiles.py`:
- `single_lap_safe`: w_lap_gain=1.00, w_platform=0.75, w_envelope=0.55
- `quali`: w_lap_gain=1.00, w_platform=0.90, w_envelope=0.50
- `sprint` / `race`: increasing platform and staleness weights

**BMW-specific path:** `_is_bmw_sebring_track_aware_single_lap_safe()` applies a special weight profile with calibration-searched values (w_lap_gain=1.25, w_envelope=0.20).

**Car-specific branching:** Ferrari diff target = 10.0 Nm (vs 65.0 for BMW/others).

### Scoring Mechanism 2: CandidateRanker (`solver/candidate_ranker.py`)

Used by `candidate_search.py` for the 3-family candidate generation (incremental/compromise/baseline_reset).

Combines: predicted performance, safety, stability, confidence, and disruption cost into `CandidateScore`. Uses `predictor.py` predictions as input.

### Scoring Mechanism 3: Comparison Scorer (`comparison/score.py`)

Used by multi-session comparison. 10 categories: lap_time (12%), grip (14%), balance (14%), aero_efficiency (10%), etc. Uses learner envelope/cluster distances for context_health scoring.

### Scoring Mechanism 4: BMWSebringOptimizer (`solver/full_setup_optimizer.py`)

BMW/Sebring only. Evaluates each pre-calibrated seed through the full 6-step solver, uses garage output model for constraint satisfaction, selects the seed with best constraint-feasible score.

### Scoring Mechanism 5: Rotation Search (`solver/bmw_rotation_search.py`)

BMW/Sebring only. Bespoke scoring function based on telemetry-derived rotation characteristics (entry push, exit push, instability, traction risk). All coefficients BMW-tuned.

### Interaction Between Scorers

The production path can invoke multiple scorers sequentially:
1. Base solve (sequential or BMW optimizer) — no explicit score, physics-first
2. Legal search (if `--free`/`--legal-search`) — `ObjectiveFunction.evaluate()`
3. Candidate search (pipeline) — `CandidateRanker`
4. Rotation search (BMW only) — bespoke rotation scorer

**Conflict risk:** The `ObjectiveFunction` and `CandidateRanker` use different scoring philosophies. The objective function scores physics-forward (excursion, LLTD, ζ). The candidate ranker scores predicted-telemetry-forward (what would the car do). If these disagree, the final selection depends on which path the user invoked.

---

## 5. Accuracy/Reliability Risks

### 5.1 Scoring/Model-Calibration Risks

**CRITICAL: The scoring function correlates weakly and partially *wrongly* with lap time.**

Evidence from `validation/objective_calibration.py` and `validation/calibration_weights.json`:

| Metric | BMW/Sebring (99 obs) |
|--------|---------------------|
| Spearman (score vs lap time, non-vetoed) | **-0.18** |
| Best achievable Spearman (lap_gain only) | **-0.21** |
| Damping_ms component Spearman | **+0.246** (WRONG direction) |
| Camber_ms component Spearman | **+0.125** (WRONG direction) |
| LLTD_balance_ms Spearman | **+0.091** (wrong direction) |
| Platform_risk Spearman | **+0.108** (wrong direction) |

Raw parameter correlations are 2–3× stronger than the objective score:
- `front_ls_comp` vs lap time: Spearman = **-0.429**
- `rear_toe_mm` vs lap time: Spearman = **-0.457**

**Root cause:** The physics model's targets (damper ζ, camber peaks, LLTD target) don't match what actually makes the car fast at this track. The damper penalty literally penalizes faster setups.

**Discrepancy between solver and objective ζ targets:**
- Solver `damper_solver.py` uses: front LS=0.88, rear LS=0.30, front HS=0.45, rear HS=0.14
- Objective `objective.py` uses: front LS=0.68, rear LS=0.23, front HS=0.47, rear HS=0.20
- These are different numbers. The solver produces setups targeting one set of ζ values, then the objective scores them against different targets.

### 5.2 Path Fragmentation Risks

- BMW/Sebring gets: constrained optimizer, garage-truth validation, rotation search, calibrated seeds, special scoring weights
- All other car/track combinations get: sequential solver only, range-clamp validation, no rotation search, no calibrated seeds, generic scoring weights
- There is no way for a non-BMW path to achieve the same output quality

### 5.3 Telemetry Underuse Risks

- 70+ IBT channels are extracted but only ~15 derived metrics drive the solver via modifiers
- Corner-by-corner data (`segment.py`) is computed but only used by the rotation search (BMW-only)
- Tyre wear data is extracted but not used by any solver step
- Hybrid deployment data is extracted but not used
- Stint degradation analysis exists but is rarely activated (requires `--stint` flag)

### 5.4 Validation Gaps

- 5-fold holdout cross-validation shows instability: worst fold Spearman = +0.121 (wrong direction)
- 24% of BMW/Sebring observations (24/99) are missing critical signals (`front_excursion_mm`, `braking_pitch_deg`)
- No validation exists for non-BMW cars
- The `validator/` module (solver output vs IBT comparison) is functional but not integrated into the pipeline

### 5.5 Support Asymmetry

| Car | Track | Observations | Garage Model | Torsion C | Aero Calibrated | Tier |
|-----|-------|-------------|-------------|-----------|----------------|------|
| BMW | Sebring | 99 | Yes (R²=0.90+) | Calibrated | Yes | calibrated |
| Ferrari | Sebring | 12 | **No** | Calibrated | No | partial |
| Cadillac | Silverstone | 4 | **No** | Borrowed (BMW) | No | exploratory |
| Porsche | Sebring | 2 | **No** | **Estimated (wrong chassis)** | No | unsupported |
| Acura | Hockenheim | 7 | **No** | **Estimated (wrong chassis)** | No | exploratory |

### 5.6 Why Ferrari and Acura Produce Bad Outputs

**Ferrari:**
1. Heave spring index decode is approximate — "front idx 1 ≈ 50 N/mm, 20 N/mm per step" with no garage screenshot anchor
2. Rear heave perch baseline is -103.5mm vs BMW's +42mm (145mm gap). If any code path uses BMW defaults, results are catastrophically wrong
3. No garage output model — no validation that the solver's output would produce legal values in iRacing's garage
4. The setup registry inherits from BMW by copying the entire BMW spec dict, then selectively overriding. Any BMW field not explicitly overridden silently applies to Ferrari
5. Front/rear m_eff values are ESTIMATES (176/2870 kg) — not calibrated from Ferrari telemetry
6. All ARB stiffness values are ESTIMATES

**Acura:**
1. Torsion bar C constant borrowed from BMW — acknowledged as wrong in code comments. ORECA chassis ≠ Dallara chassis
2. Aero maps not calibrated — rear RH targets are unreliable because aero compression uses estimated 15/8mm values
3. Front heave damper always bottomed (-1.7 to -2.5mm deflection) across all tested ODs. Deflection model coefficients are all zeroed out
4. No garage output model configured
5. Roll dampers use baseline values only — no physics tuning
6. Front RH is camber-dominated (2.4mm/deg camber) vs BMW's pinned-at-30mm behavior — completely different suspension geometry

### 5.7 Why Heave Damper and Spring Deflection Calibration Is Inaccurate

**BMW (calibrated but with discrepancy):**
- `DeflectionModel` in `cars.py`: `heave_spring_defl_max = 106.43 - 0.310 × rate`
- `GarageOutputModel` in `garage.py`: `heave_spring_defl_max = 96.02 - 0.083 × rate`
- These two models predict different max deflections for the same heave rate — inconsistency between garage validation and solver calculations
- Both were fitted from the same dataset but with different feature sets

**Ferrari (wrong model):**
- Uses BMW deflection coefficients, which are physically meaningless for Ferrari's different suspension
- Heave springs are indexed (not N/mm), so the BMW regression formula can't apply

**Acura (no model):**
- All deflection model coefficients zeroed — no predictions possible
- Front heave damper always bottomed — the excursion model can't account for this
- Torsion bar deflection is NOT purely weight/(C×OD⁴) — includes preload from torsion bar turns, which the current model doesn't capture

**Ride Height calibration:**
- BMW front RH is sim-pinned at ~30mm (nearly constant regardless of setup). This is a strong constraint that simplifies the BMW solver but doesn't apply to other cars
- Acura front RH varies 30.9–34.3mm depending on camber (2.4mm/deg sensitivity)
- Cadillac front RH depends on both pushrod AND heave perch (BMW doesn't)
- No non-BMW car has a ride height model, so ride height predictions for non-BMW are approximations

### 5.8 The Rear m_eff Problem

BMW `rear_m_eff_kg = 2395.3`, other cars default to 2870.0. The entire car weighs ~1100 kg. These values are physically implausible as sprung mass — they capture some combination of aero-induced apparent mass, track surface coupling, and frequency-domain effects. The extreme values (5-10× actual sprung mass) are unlikely to transfer between cars/tracks.

---

## 6. Telemetry Channel Audit

### Solve-Critical Channels (directly affect solver output)

| Channel/Metric | Read In | Analyzed In | Affects Solver Via | Notes |
|---|---|---|---|---|
| `LFrideHeight`, `RFrideHeight`, `LRrideHeight`, `RRrideHeight` | `extract.py` | `diagnose.py` (platform, bottoming) | `modifiers.py` → rake_solver target, heave floor | Core platform state |
| `HFshockVel`, `HRshockVel`, `TRshockVel` | `extract.py` | `extract.py` (p95/p99 derivation) | `heave_solver.py` (excursion model) | Heave platform sizing |
| `LFshockVel`..`RRshockVel` | `extract.py` | `diagnose.py` (damper category) | `modifiers.py` → damper click offsets | Damper tuning |
| `SteeringWheelAngle` + `YawRate` | `extract.py` | `extract.py` (understeer derivation) | `modifiers.py` → LLTD offset, DF balance offset | Balance targeting |
| `VelocityX`, `VelocityY` | `extract.py` | `extract.py` (body slip derivation) | `modifiers.py` → diff/ARB adjustments | Stability |
| `Roll` | `extract.py` | `extract.py` (LLTD proxy, roll gradient) | `modifiers.py` → LLTD offset | Roll stiffness distribution |
| `HFshockDefl`, `HRshockDefl` | `extract.py` | `diagnose.py` (safety: travel exhaustion) | `modifiers.py` → heave floor adjustment | Platform safety |
| `Speed` | `extract.py` | `extract.py` (speed masks, regime classification) | `heave_solver.py`, `rake_solver.py` (speed-dependent compression) | Regime separation |
| `LatAccel` | `extract.py` | `segment.py` (corner detection) | `arb_solver.py` (target LLTD at characteristic speed) | Balance |

### Diagnostic-Only Channels (affect diagnosis but not directly solver)

| Channel/Metric | Read In | Analyzed In | Classification | Notes |
|---|---|---|---|---|
| `CFSRrideHeight` | `extract.py` | `diagnose.py` (splitter scrape detection) | Diagnostic | Safety warning only |
| `RollRate` | `extract.py` | `diagnose.py` (roll rate p95) | Diagnostic | Damper category; no modifier path |
| `PitchRate` | `extract.py` | `diagnose.py` (pitch rate) | Diagnostic | Report only |
| `Pitch` | `extract.py` | `diagnose.py` (braking pitch range) | Diagnostic | Envelope penalty input |
| `BrakeABSactive`, `BrakeABScutPct` | `extract.py` | `diagnose.py` (ABS activity) | Diagnostic | Brake system warning |
| `LFbrakeLinePress`..`RRbrakeLinePress` | `extract.py` | `extract.py` (hydraulic split) | Diagnostic | Brake diagnosis only |
| `LFtempL`..`RRtempR` (surface temps) | `extract.py` | `diagnose.py` (thermal category) | Diagnostic | Camber/toe recommendations |
| `LFtempCM`..`RRtempCM` (carcass) | `extract.py` | `diagnose.py` (thermal window) | Diagnostic + envelope | Confidence weighting |
| `LFpressure`..`RRpressure` | `extract.py` | `diagnose.py` (pressure window) | Diagnostic | Supporting solver input |

### Context-Only Channels (extracted but not used in solving)

| Channel/Metric | Read In | Where Used | Classification | Notes |
|---|---|---|---|---|
| `FuelLevel` | `extract.py` | `context.py` (confidence), solver (fuel_load_l) | Context | Fuel mass used for solver but level is context |
| `AirTemp`, `TrackTempCrew`, `AirDensity` | `extract.py` | `context.py` (weather confidence) | Context | Not used by any solver step |
| `WindVel`, `WindDir` | `extract.py` | `context.py` | Context | Not used by any solver step |
| `RPM` | `extract.py` | Report (rev limiter %) | Context | Not used by solver |
| `Gear` | `extract.py` | Report (apex gear, max gear) | Context | Not used by solver |
| `dcBrakeBias`, `dcTractionControl`, etc. | `extract.py` | `setup_schema.py` (live overrides) | Context | Used for setup schema, not solver |
| `EnergyERSBatteryPct`/`EnergyERSBattery` | `extract.py` | Report only | Context | Not used by solver |
| `TorqueMGU_K` | `extract.py` | Report only | Context | Not used by solver |

### Effectively Unused Channels (extracted but never referenced downstream)

| Channel/Metric | Read In | Classification | Notes |
|---|---|---|---|
| `LFwearL`..`RRwearR` (tyre wear) | `extract.py` | **Unused** | Extracted but not referenced by any diagnosis, modifier, or solver |
| `LFcoldPressure`..`RRcoldPressure` | `extract.py` | **Unused** | Extracted, stored in MeasuredState, never referenced |
| `dcAntiRollFront`, `dcAntiRollRear` live values | `extract.py` | **Partially used** | Live override values captured, but adjustment *count* is the only diagnostic signal |
| `dcABS`, `dcMGUKDeployMode` adjustment counts | `extract.py` | **Partially used** | Count contributes to "in-car adjustments" diagnostic |
| `TireLF_RumblePitch`, `TireRF_RumblePitch` | `extract.py` | **Partially used** | Used for kerb spatial masking, not for solver inputs |
| `LongAccel` | `extract.py` | **Partially used** | Fallback for braking deceleration; typically Speed gradient is preferred |

---

## 7. Unused, Unwired, or Overlapping Code

### Dead Code (safe to delete)

| File | Evidence | Lines |
|------|----------|-------|
| `run_now.py` | Hardcoded Windows path `C:\Users\VYRAL\IOptimal\` | ~30 |
| `run_full_v2.py` | Hardcoded Windows path, specific IBT file | ~50 |
| `run_full_pipeline.py` | Hardcoded Windows path | ~30 |
| `run_full_justified.py` | Hardcoded Windows path, specific IBT file | ~40 |
| `run_exhaustive.py` | Hardcoded Windows path | ~30 |
| `run_tuned_search.py` | Hardcoded Windows path, tuned weights | ~40 |
| `run_tests.py` | Hardcoded Windows path, subset test runner | ~20 |
| `test_camber.py` | One-off test, not in `tests/` dir, not run by pytest | ~50 |
| `research/ferrari_calibration_mar21.py` | One-time calibration script | ~200 |
| `research/ferrari_calibration_write_docs.py` | Documentation generator | ~100 |

### Analysis-Only / Experimental

| Module | Status | Evidence |
|--------|--------|---------|
| `solver/bayesian_optimizer.py` | Research only | Behind `--bayesian` flag, simplified BMW-specific scoring proxy |
| `solver/iterative_solver.py` | Experimental | Behind `--iterative` flag, max 3 passes |
| `solver/explorer.py` | Research only | Behind `--explore` flag, LHS with BMW camber targets |
| `solver/multi_speed_solver.py` | Research only | Behind `--multi-speed` flag |
| `scripts/rh_calibration.py` | Utility script | Ride height calibration tool |
| `scripts/generate_repo_audit.md` | Utility script | Report generator |
| `scripts/ferrari_hockenheim_calibration.py` | One-off script | Ferrari calibration |

### Overlapping / Name-Colliding Code

| Collision | Files | Issue |
|-----------|-------|-------|
| `MeasuredState` (2 definitions) | `analyzer/extract.py`, `validator/extract.py` | Same class name, completely different fields. Validator's should be renamed `ValidationMeasuredState` |
| `SetupCluster` (2 definitions) | `output/search_report.py`, `learner/setup_clusters.py` | Same class name, different purposes (candidate clustering vs session clustering) |
| `validator/` vs `learner/delta_detector.py` | Both compare predicted vs measured | Partial functional overlap. Validator is per-run; learner is cumulative |

### Partially Wired

| Module | Status | Evidence |
|--------|--------|---------|
| `validator/` package | Functional but not auto-integrated | Self-contained CLI. Not called by pipeline. Manual feedback loop only |
| `learner/cross_track.py` | Built but output not consumed | Global car model saved during ingest but solver corrections come from per-track models only |
| `analyzer/stint_analysis.py` | Conditional | Only activated with `--stint` flag; not part of default production path |
| `solver/corner_strategy.py` | Optional | Per-corner live parameter recommendations; generates report section but doesn't feed back into solver |
| `solver/sector_compromise.py` | Optional | Sector analysis generates report section, no solver feedback |

---

## 8. Repo/Runtime Hygiene Issues

### Misleading Root Files
- 8 `run_*.py` scripts at repo root with hardcoded Windows paths. These look like entry points but are dead developer convenience wrappers. `test_camber.py` looks like it should be in `tests/` but isn't.

### Module at Wrong Location
- `vertical_dynamics.py` is a utility module at repo root imported by 5 production modules (`car_model/cars.py`, `solver/heave_solver.py`, `solver/damper_solver.py`, `solver/objective.py`, tests). Should be in `solver/` or `car_model/`.

### Naming Confusion
- `validator/` vs `validation/` — completely different purposes (per-run feedback vs aggregate statistics). The names are easily confused.
- Two `MeasuredState` classes, two `SetupCluster` classes in different modules.

### Hardcoded Support Tiers Drifting
- `output/run_trace.py::_SUPPORT_TIERS` is hardcoded and stale (lists Acura as "unsupported" with "<1 session" but Acura now has 7 observations). Should be loaded dynamically from `validation/objective_validation.json`.

### Generated Artifacts in Source
- `data/learnings/` contains observation JSON files that are generated at runtime. These are knowledge store artifacts, not source code, but they live in the repo tree.
- `data/aeromaps_parsed/` contains parsed aero map JSONs generated from xlsx files.
- `docs/solver_audit.md`, `docs/analyzer_pipeline_audit.md`, `docs/car_model_validation_audit.md`, `docs/module_audit_output_learner_misc.md` — these were generated by this audit and should be considered transient.

### No Unified Entry Point Documentation
- There are multiple valid entry points (`python -m pipeline.produce`, `python -m solver.solve`, `python -m analyzer`, `python -m comparison`, `python -m learner.ingest`, `python -m webapp`, `python -m validator`) but no clear "start here" guide for which to use when.

### Broad Exception Handling
- `solver/solve.py::run_solver()` wraps optional analyses (stint, sector, sensitivity) in `try-except Exception` blocks that silently swallow errors.

---

## 9. Recommended Module Status Map

### Production-Critical

| Module | Role |
|--------|------|
| `pipeline/produce.py` | Primary orchestrator |
| `pipeline/reason.py` | Multi-session reasoning |
| `pipeline/report.py` | Report generation |
| `solver/solve.py` | CLI solver entry |
| `solver/solve_chain.py` | Core solver chain |
| `solver/rake_solver.py` | Step 1 |
| `solver/heave_solver.py` | Step 2 |
| `solver/corner_spring_solver.py` | Step 3 |
| `solver/arb_solver.py` | Step 4 |
| `solver/wheel_geometry_solver.py` | Step 5 |
| `solver/damper_solver.py` | Step 6 |
| `solver/objective.py` | Scoring function |
| `solver/legal_space.py` | Search manifold |
| `solver/legality_engine.py` | Legality validation |
| `solver/legal_search.py` | Manifold search |
| `solver/grid_search.py` | Exhaustive search |
| `solver/candidate_search.py` | Family generation |
| `solver/candidate_ranker.py` | Candidate scoring |
| `solver/predictor.py` | Telemetry prediction |
| `solver/scenario_profiles.py` | Scenario weights |
| `solver/modifiers.py` | Feedback loop |
| `solver/supporting_solver.py` | Brake/diff/TC/pressure |
| `solver/brake_solver.py` | Brake bias |
| `solver/diff_solver.py` | Differential |
| `solver/decision_trace.py` | Change tracing |
| `analyzer/extract.py` | Telemetry extraction |
| `analyzer/diagnose.py` | Handling diagnosis |
| `analyzer/segment.py` | Corner segmentation |
| `analyzer/driver_style.py` | Driver profiling |
| `analyzer/recommend.py` | Recommendations |
| `analyzer/setup_reader.py` | Setup parsing |
| `analyzer/setup_schema.py` | Schema |
| `analyzer/telemetry_truth.py` | Signal quality |
| `analyzer/adaptive_thresholds.py` | Threshold adaptation |
| `analyzer/state_inference.py` | State inference |
| `analyzer/conflict_resolver.py` | Conflict resolution |
| `analyzer/causal_graph.py` | Root cause analysis |
| `analyzer/context.py` | Session context |
| `analyzer/report.py` | Report formatting |
| `car_model/cars.py` | Car definitions |
| `car_model/setup_registry.py` | Parameter registry |
| `car_model/garage.py` | Garage output model |
| `output/setup_writer.py` | .sto generation |
| `output/garage_validator.py` | Pre-write validation |
| `output/report.py` | Engineering report |
| `output/run_trace.py` | Decision trace |
| `output/search_report.py` | Search analysis |
| `aero_model/interpolator.py` | Aero surface |
| `aero_model/gradient.py` | Aero gradients |
| `track_model/profile.py` | Track profile |
| `track_model/build_profile.py` | Profile builder |
| `track_model/ibt_parser.py` | IBT parser |
| `vertical_dynamics.py` | Vertical dynamics helpers |

### Supported Secondary

| Module | Role |
|--------|------|
| `solver/full_setup_optimizer.py` | BMW/Sebring optimizer (should become generic) |
| `solver/bmw_rotation_search.py` | BMW rotation optimizer |
| `solver/bmw_coverage.py` | BMW parameter coverage |
| `solver/learned_corrections.py` | Learner bridge |
| `solver/session_database.py` | k-NN database |
| `solver/heave_calibration.py` | Empirical heave model |
| `solver/stint_model.py` | Fuel burn model |
| `solver/stint_reasoner.py` | Stint-aware solve |
| `solver/setup_fingerprint.py` | Setup hashing |
| `solver/sensitivity.py` | Constraint analysis |
| `solver/coupling.py` | Coupling reporting |
| `solver/laptime_sensitivity.py` | Sensitivity model |
| `solver/setup_space.py` | Space exploration |
| `solver/corner_strategy.py` | Per-corner strategy |
| `solver/sector_compromise.py` | Sector analysis |
| `solver/uncertainty.py` | Uncertainty bands |
| `solver/validation.py` | Predict-validate loop |
| `comparison/` | Multi-session comparison |
| `learner/` | Knowledge system |
| `webapp/` | Web application |
| `analyzer/sto_binary.py` | STO decoder |
| `analyzer/sto_adapters.py` | STO adapters |
| `analyzer/sto_reader.py` | STO inspector (CLI debug tool) |
| `analyzer/stint_analysis.py` | Stint analysis |
| `analyzer/overhaul.py` | Overhaul assessment |
| `validation/` | Aggregate validation |
| `car_model/calibrate_deflections.py` | Calibration utility |
| `aero_model/parse_xlsx.py`, `parse_all.py` | Aero map parsing |
| `track_model/generic_profiles.py` | Generic track profiles |

### Experimental

| Module | Status |
|--------|--------|
| `solver/bayesian_optimizer.py` | Research only, behind flag |
| `solver/iterative_solver.py` | Experimental, behind flag |
| `solver/explorer.py` | Research only, behind flag |
| `solver/multi_speed_solver.py` | Research only, behind flag |
| `validator/` | Functional but not pipeline-integrated |
| `learner/cross_track.py` | Output not consumed by solver |

### Legacy / Merge / Deprecate Candidates

| Module | Recommendation |
|--------|---------------|
| `run_now.py`, `run_full_v2.py`, `run_full_pipeline.py`, `run_full_justified.py`, `run_exhaustive.py`, `run_tuned_search.py`, `run_tests.py` | **Delete** |
| `test_camber.py` | **Delete** or move to `tests/` |
| `research/ferrari_calibration_mar21.py`, `research/ferrari_calibration_write_docs.py` | **Delete** (one-time calibration, data preserved in `cars.py`) |
| `scripts/rh_calibration.py`, `scripts/ferrari_hockenheim_calibration.py` | Keep as utilities but don't call from production |

---

## 10. Fix Plan in Priority Order

### Priority 1: Fix the Scoring Function (Highest Leverage)

**Problem:** The objective function's damping, camber, and LLTD components correlate in the wrong direction with lap time.

**Fix:**
1. In `solver/objective.py`, zero out `damping_ms`, `camber_ms`, and `lltd_balance_ms` penalty components (or at minimum set their contribution to 0.0 in `LapGainBreakdown`)
2. In `solver/scenario_profiles.py`, set all penalty weights to near-zero except `w_lap_gain` (calibration search already recommends `lap_gain=0.25, everything_else=0.0`)
3. Reconcile damper ζ targets: the solver (`damper_solver.py`) uses 0.88/0.30/0.45/0.14 while the objective (`objective.py`) uses 0.68/0.23/0.47/0.20. Align to the IBT-calibrated values
4. Re-run `validation/run_validation.py` to confirm Spearman improvement
5. Consider replacing the penalty-based scoring with direct parameter-correlation-based scoring (e.g., use the raw `front_ls_comp`, `rear_toe_mm` correlations)

**Files:** `solver/objective.py`, `solver/scenario_profiles.py`, `solver/damper_solver.py`

### Priority 2: Build Garage Output Models for Ferrari and Acura

**Problem:** No non-BMW car has a `GarageOutputModel`, so solver outputs bypass deflection and ride height verification.

**Fix:**
1. Collect 5+ garage screenshots per car with varied setups (different heave rates, torsion ODs, perch offsets)
2. Run `car_model/calibrate_deflections.py --car ferrari` and `--car acura` to fit regression models
3. Implement `GarageOutputModel` for Ferrari and Acura in `car_model/garage.py`
4. Add the models to each car's definition in `car_model/cars.py`

**Files:** `car_model/cars.py`, `car_model/garage.py`, `car_model/calibrate_deflections.py`

### Priority 3: Calibrate Acura Torsion Bar C Constant

**Problem:** Acura uses BMW's C=0.0008036 for an ORECA chassis. This is explicitly acknowledged as wrong.

**Fix:**
1. Collect 5+ Acura garage screenshots with different torsion bar ODs and record the displayed wheel rate or spring deflection
2. Fit `k_wheel = C × OD⁴` to the data
3. Update `car_model/cars.py` Acura `front_torsion_c` and `rear_torsion_c`
4. Re-run pipeline to validate

**Files:** `car_model/cars.py`

### Priority 4: Fix Ferrari Heave Spring Index Decode

**Problem:** Ferrari heave springs are decoded with estimated formula (`rate = 50 + (idx-1) × 20`) that has no garage anchor.

**Fix:**
1. From Ferrari garage screenshots, record the actual N/mm displayed for each index position
2. Build a proper index→rate lookup table (not a linear approximation)
3. Update `car_model/setup_registry.py` Ferrari heave spring specs

**Files:** `car_model/setup_registry.py`, `car_model/cars.py`

### Priority 5: Resolve Deflection Model Discrepancy

**Problem:** `DeflectionModel` and `GarageOutputModel` predict different heave spring deflection max for BMW.

**Fix:**
1. Determine which model is correct by comparing both against fresh garage screenshots
2. Align coefficients so both models produce the same prediction
3. Document which model is authoritative

**Files:** `car_model/cars.py`, `car_model/garage.py`

### Priority 6: Align Solver and Objective Damper ζ Targets

**Problem:** The solver produces setups targeting ζ = 0.88/0.30/0.45/0.14 but the objective evaluates against ζ = 0.68/0.23/0.47/0.20.

**Fix:**
1. Use the IBT-calibrated values (0.68/0.23/0.47/0.20) in both the solver and objective
2. Or use the calibrated values only in the solver and remove damping from the objective penalty entirely (per Priority 1)

**Files:** `solver/damper_solver.py`, `solver/objective.py`

### Priority 7: Clean Up Dead Code and Naming Collisions

**Fix:**
1. Delete 8 root-level `run_*.py` and `test_camber.py`
2. Delete `research/` directory (one-time scripts, data preserved)
3. Move `vertical_dynamics.py` into `solver/` or `car_model/`
4. Rename `validator/extract.py::MeasuredState` to `ValidationMeasuredState`
5. Rename `output/search_report.py::SetupCluster` to `CandidateCluster`

**Files:** Multiple

### Priority 8: Generalize BMW-Only Optimizer

**Problem:** The constrained optimizer (`full_setup_optimizer.py`) and rotation search (`bmw_rotation_search.py`) only work for BMW/Sebring.

**Fix:**
1. Factor out the `_is_bmw_sebring()` gates
2. Create a generic `ConstrainedOptimizer` that loads car-specific calibration seeds from per-car JSON files
3. Generalize the rotation search scoring to be parameterized by car model constants

**Files:** `solver/full_setup_optimizer.py`, `solver/bmw_rotation_search.py`

### Priority 9: Integrate `validator/` Into the Pipeline

**Problem:** The validator provides useful solver-output-vs-IBT feedback but requires manual invocation.

**Fix:**
1. Add a `--validate` flag to `pipeline/produce.py` that runs the validator after the solve
2. Feed validator recommendations back into the learner knowledge store
3. Use validator discrepancies to automatically flag suspect predictions

**Files:** `pipeline/produce.py`, `validator/`

### Priority 10: Reduce Hardcoded BMW Constants in Shared Modules

**Problem:** `solver/sensitivity.py` (m_eff=228.0), `solver/coupling.py` (all gains), `solver/bmw_coverage.py` (BMW-only parameter list) use BMW constants that affect non-BMW paths.

**Fix:**
1. Move BMW-specific constants into the car model
2. Make sensitivity analysis use `car.heave_spring.front_m_eff_kg` instead of hardcoded 228.0
3. Make coupling gains parameterized by car model

**Files:** `solver/sensitivity.py`, `solver/coupling.py`

---

## 11. Appendices

### A. File/Function Reference Index

**Primary orchestration chain:**
- `pipeline/produce.py::produce()` → main entry
- `solver/solve_chain.py::run_base_solve()` → solver dispatch
- `solver/solve_chain.py::_run_sequential_solver()` → 6-step execution
- `solver/full_setup_optimizer.py::optimize_if_supported()` → BMW/Sebring optimizer gate
- `solver/solve_chain.py::_finalize_result()` → legality + trace + prediction

**Solver steps:**
- `solver/rake_solver.py::RakeSolver.solve()` → Step 1
- `solver/heave_solver.py::HeaveSolver.solve()` → Step 2
- `solver/corner_spring_solver.py::CornerSpringSolver.solve()` → Step 3
- `solver/arb_solver.py::ARBSolver.solve()` → Step 4
- `solver/wheel_geometry_solver.py::WheelGeometrySolver.solve()` → Step 5
- `solver/damper_solver.py::DamperSolver.solve()` → Step 6

**Scoring:**
- `solver/objective.py::ObjectiveFunction.evaluate()` → canonical scorer
- `solver/candidate_ranker.py::combine_candidate_score()` → candidate scorer
- `comparison/score.py::score_sessions()` → comparison scorer
- `solver/bmw_rotation_search.py::_score_controls()` → BMW rotation scorer

**Legality:**
- `solver/legality_engine.py::validate_solution_legality()` → full validation
- `solver/legality_engine.py::validate_candidate_legality()` → fast validation
- `output/garage_validator.py::validate_and_fix_garage_correlation()` → pre-write validation

**Car models:**
- `car_model/cars.py::BMW_M_HYBRID_V8` → line ~1050
- `car_model/cars.py::CADILLAC_V_SERIES_R` → line ~1600
- `car_model/cars.py::FERRARI_499P` → line ~1750
- `car_model/cars.py::PORSCHE_963` → line ~1900
- `car_model/cars.py::ACURA_ARX_06` → line ~2000

### B. Open Questions / Uncertainties

1. **Why is rear m_eff so large?** 2395 kg (BMW) and 2870 kg (others) for cars weighing ~1100 kg total. Is this intentional lumped-parameter modeling or a bug in the calibration?

2. **Does the BMW optimizer produce better setups than the sequential solver?** The optimizer uses calibration seeds and SLSQP, but there's no A/B comparison showing it outperforms the 6-step chain for BMW.

3. **Is the TORSION_ARB_COUPLING = 0.25 physically real?** Back-calibrated from a single observation. Could be compensating for other model errors (roll center, tyre compliance, chassis flex).

4. **Are the aero maps accurate for non-BMW cars?** All 5 cars have maps, but only BMW has been validated against IBT ride heights. The Acura aero map produces rear RH targets that the solver can't achieve.

5. **Is the Ferrari rear torsion bar C=0.001282 correct?** Calibrated from only 4 rear data points. The front was calibrated from 6 points. More data needed to confirm.

6. **What is the correct heave spring deflection max model?** Two different regression models exist in `cars.py` (`DeflectionModel`) and `garage.py` (`GarageOutputModel`) with different coefficients. Which is authoritative?

### C. Contradictions Between Docs and Code

| Doc Claim | Code Reality |
|-----------|-------------|
| CLAUDE.md says 73 observations, 72 non-vetoed | `validation/objective_validation.json` shows 99 observations, 98 non-vetoed |
| CLAUDE.md says Spearman = -0.120522 | Validation data shows Spearman = -0.1808 (99 obs) |
| CLAUDE.md says Acura is "exploratory" with 7 observations | `run_trace.py::_SUPPORT_TIERS` lists Acura as "unsupported — <1 session" |
| CLAUDE.md says damper targets are "IBT-calibrated (0.68/0.23/0.47/0.20)" | `damper_solver.py` still uses 0.88/0.30/0.45/0.14 as solver targets; only `objective.py` uses the IBT-calibrated values |
| CLAUDE.md says "single_lap_safe weights set to calibration-searched values (lap_gain=1.25, envelope=0.20)" | `scenario_profiles.py` shows single_lap_safe has w_lap_gain=1.00, w_envelope=0.55 — the calibration-searched values may only apply to the BMW-specific code path |
| CLAUDE.md says rear_motion_ratio=0.60 for BMW is "calibrated" | The value is back-solved from LLTD; it's a model fit, not a direct measurement of the physical motion ratio |

### D. Critical Code Snippets

**The damper ζ target discrepancy:**

`solver/damper_solver.py` (solver targets):
```python
front LS=0.88, rear LS=0.30, front HS=0.45, rear HS=0.14
```

`solver/objective.py` line 116-119 (scoring targets):
```python
zeta_ls_front: float = 0.68   # IBT-calibrated: top-15 fastest mean
zeta_ls_rear: float = 0.23    # IBT-calibrated
zeta_hs_front: float = 0.47   # IBT-calibrated
zeta_hs_rear: float = 0.20    # IBT-calibrated (was 0.14)
```

**The BMW-only optimizer gate:**

`solver/full_setup_optimizer.py` line 94-98:
```python
def _is_bmw_sebring(car, track):
    return (
        getattr(car, "canonical_name", "").lower() == "bmw"
        and "sebring" in getattr(track, "track_name", "").lower()
    )
```

**Ferrari spec inheritance (silent propagation risk):**

`car_model/setup_registry.py`:
```python
_FERRARI_SPECS = {**{k: v for k, v in _BMW_SPECS.items()}, ...overrides...}
```

---

## Handoff Summary for Follow-Up Engineering

**To the next model continuing this work:**

This codebase is architecturally sound but suffers from a fundamental calibration asymmetry. The BMW/Sebring path has 99 observations, a garage output model, a constrained optimizer, and rotation search. Every other car/track combination lacks these and uses borrowed constants that are physically incorrect.

**The three highest-impact fixes are:**

1. **Zero out penalty weights in the objective function.** The validation data proves that all penalty terms (platform, envelope, driver, etc.) worsen correlation with lap time. Only `lap_gain_ms` has signal, and even that is contaminated by wrong-direction damping and camber components. Zero the penalties, then fix damping targets.

2. **Build garage output models for Ferrari and Acura.** Without these, the solver can produce outputs that look physically reasonable but would display illegal values in iRacing's garage. This is the single biggest reason non-BMW setups are bad — there's no ground-truth validation loop.

3. **Fix the torsion bar C constants for Acura.** The ORECA chassis uses different geometry than Dallara. The borrowed C=0.0008036 produces wrong wheel rates from torsion bar OD, which cascades through corner spring sizing, LLTD calculation, and damper sizing.

**Key files to start with:** `solver/objective.py`, `solver/damper_solver.py`, `car_model/cars.py`, `car_model/garage.py`, `validation/run_validation.py`.

**Key invariant to preserve:** The 6-step solver ordering (rake → heave → corner → ARB → geometry → damper) is physically correct and should not be changed. The problem is not the solver architecture — it's the constants, calibration, and scoring function that feed it.
