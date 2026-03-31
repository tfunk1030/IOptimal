# IOptimal Deep Codebase Audit Report

**Branch:** `claw-research` | **Date:** 2026-03-31 | **Auditor:** Claude Opus 4.6

---

## 1. Executive Summary

IOptimal is a physics-based setup solver for iRacing GTP/Hypercar cars. It takes telemetry (IBT files) through a 6-step constraint-satisfaction solver to produce `.sto` setup files. The system is architecturally sound and the physics reasoning is genuine — this is not a pattern-matching or ML system.

**The single biggest problem:** The entire calibration chain — garage output models, deflection models, ride height regressions, heave calibration data, damper zeta targets, session database, and objective scoring weights — is **BMW/Sebring-specific**. Ferrari and Acura inherit BMW's regression coefficients as defaults, producing physically wrong deflection predictions, ride heights, and garage-validator corrections. This is not a tuning problem; it is a structural calibration gap.

**Key findings:**
- **Objective correlation is weak** (Spearman -0.12 for BMW/Sebring, the only calibrated path). The scoring function cannot reliably distinguish good from bad setups yet.
- **Ferrari and Acura produce bad setups** because they lack car-specific `GarageOutputModel`, `DeflectionModel`, `RideHeightModel`, and heave calibration data. They silently inherit BMW defaults.
- **Many ESTIMATE markers** in car definitions — ARB stiffnesses, track widths, tyre load sensitivity, aero compression for non-BMW cars are guesses.
- **The `comparison/` module** is a parallel scoring/ranking path that can define "best" differently from the pipeline.
- **Dead and experimental code** exists in several modules (grid search, space exploration, stint reasoner).
- **Commands are complex** — 6+ entry points with overlapping CLI flags make the system confusing to run.

**What works well:**
- Physics solver chain (Steps 1-6) is complete and well-reasoned
- Telemetry extraction is thorough (60+ channels, signal quality tracking)
- Learning loop (observations → deltas → empirical corrections) is well-designed
- Legal manifold search correctly constrains to garage-legal parameter space
- Setup fingerprinting and veto logic prevent repeating known-bad configurations
- BMW/Sebring path has genuine depth (73 observations, garage-validated regressions)

---

## 2. Actual Production Path

### Primary Production Path (single IBT → .sto)
```
python -m ioptimal --car bmw --ibt session.ibt --wing 17

__main__.py → produce_result(args)
  → pipeline/produce.py::produce_result()
    → track_model/build_profile.py::build_profile()     [build TrackProfile]
    → analyzer/extract.py::extract_measurements()        [60+ telemetry metrics]
    → analyzer/segment.py::segment_lap()                 [corner-by-corner]
    → analyzer/driver_style.py::analyze_driver()         [DriverProfile]
    → analyzer/diagnose.py::diagnose()                   [Diagnosis + problems]
    → solver/modifiers.py::compute_modifiers()           [diagnosis → targets]
    → solver/solve_chain.py::run_base_solve()            [6-step physics solve]
      → rake_solver.py → heave_solver.py → corner_spring_solver.py
      → arb_solver.py → wheel_geometry_solver.py → damper_solver.py
      → supporting_solver.py
    → solver/legal_search.py (if --free/--search-mode)   [manifold search]
    → output/garage_validator.py::validate_and_fix()     [garage correlation fix]
    → output/setup_writer.py::write_sto()                [.sto XML export]
    → pipeline/report.py::generate_report()              [ASCII report]
```

### Supported Secondary Paths
| Entry Point | Command | Notes |
|---|---|---|
| Multi-IBT reasoning | `python -m ioptimal --car bmw --ibt s1.ibt s2.ibt --wing 17` | Cross-session delta analysis via `pipeline/reason.py` |
| Standalone solver (no IBT) | `python -m ioptimal --car bmw --track sebring --wing 17` | Uses pre-built TrackProfile; falls back when no telemetry available |
| Learner ingestion | `python -m learner.ingest --car bmw --ibt session.ibt` | Stores observations, fits empirical corrections |
| Analyzer only | `python -m analyzer --car bmw --ibt session.ibt` | Diagnosis report without solving |
| Webapp | `python -m webapp` | Uvicorn localhost:8000 |

### Experimental / Analysis-Only Paths
| Entry Point | What It Does | Status |
|---|---|---|
| `python -m comparison --car bmw --ibt s1.ibt s2.ibt` | Separate scoring/ranking via `comparison/score.py` | **Parallel ranking path** — may conflict with pipeline scoring |
| `--search-mode quick/standard/exhaustive/maximum` | Grid search over legal space | Functional but compute-intensive |
| `--space` flag | Setup space exploration | Experimental visualization |
| `python -m ioptimal --car bmw --track sebring --grid` | Grid search entry | Experimental |
| `python -m desktop` | Desktop app with watcher + sync + tray | Deployed but depends on team server |
| `python -m server` | Team REST API on Cloud Run | Production-deployed but separate concern |

### Legacy / Overlap-Heavy Paths
- `pipeline/__main__.py` — Separate entry point that duplicates `__main__.py` routing
- `solver/solve.py::main()` — Has its own CLI parser that overlaps with `__main__.py`
- `comparison/` module — Completely separate scoring system from `solver/objective.py`

---

## 3. How the Solver Works

### Orchestration: `solver/solve_chain.py::run_base_solve(SolveChainInputs)`

The solver is a **6-step sequential constraint-satisfaction engine**. Each step feeds forward into the next. The order is mandatory — you cannot solve dampers before springs.

**Step 1: Rake / Ride Heights** (`solver/rake_solver.py`)
- **Input:** target DF balance, car aero map, fuel load, wing angle
- **Constraint:** DF balance at median cornering speed must match target (+-tolerance)
- **Constraint:** front dynamic RH > vortex burst threshold (2.0mm)
- **Objective:** maximize L/D at target balance
- **Output:** front/rear pushrod offsets, static/dynamic RH, DF balance achieved, L/D ratio
- **Car-specific:** uses `AeroCompression.front_at_speed(V)` for V-squared scaling

**Step 2: Heave / Third Springs** (`solver/heave_solver.py`)
- **Input:** dynamic RH from Step 1, track surface spectrum, car mass + aero loads
- **Constraint:** clean-track bottoming events < 5/lap
- **Constraint:** RH variance (sigma) below target at speed
- **Objective:** softest spring that meets constraints (maximize mechanical grip)
- **Refinement:** solves -> provisional dampers -> re-solves with damper-included excursion
- **Output:** front heave rate (N/mm), rear third rate (N/mm), perch offsets

**Step 3: Corner Springs** (`solver/corner_spring_solver.py`)
- **Input:** heave/third from Step 2, car mass distribution
- **Constraint:** combined roll + heave stiffness controls platform under lateral load
- **Objective:** balance mechanical grip vs platform control
- **Output:** front torsion OD (mm), rear spring rate (N/mm), rear spring perch
- **CRITICAL CONVENTION:** front torsion output is already wheel rate (MR baked in via C*OD^4). Rear coil spring is RAW spring rate — must multiply by `rear_motion_ratio^2` for wheel rate.

**Step 4: ARBs** (`solver/arb_solver.py`)
- **Input:** total wheel rates from Steps 2-3
- **Constraint:** LLTD ~5% above front weight distribution
- **Objective:** neutral steady-state cornering at track's characteristic speed
- **Output:** front/rear ARB size (string), blade position (int), achieved LLTD, roll stiffness
- **Torsion-ARB coupling:** BMW-only gamma=0.25 (single-point calibrated, needs validation)

**Step 5: Wheel Geometry** (`solver/wheel_geometry_solver.py`)
- **Input:** total roll stiffness, wheel rates, tyre temp data (if available)
- **Constraint:** camber optimizes contact patch across roll range
- **Output:** front/rear camber, front/rear toe

**Step 6: Dampers** (`solver/damper_solver.py`)
- **Input:** spring rates, dynamic RH, track surface spectrum, settle time (if measured)
- **Target zeta values:** 0.68/0.23/0.47/0.20 (front_LS/rear_LS/front_HS/rear_HS) — **IBT-calibrated from top-15 fastest BMW/Sebring sessions only**
- **Constraint:** p99 shock velocity controlled, rebound/compression ~2:1
- **Output:** 20 damper parameters (LS/HS comp/rbd/slope per corner)

**Supporting Parameters** (`solver/supporting_solver.py`)
- Brake bias, diff preload (5-40 Nm), diff ramps, TC gain/slip, tyre pressures

### Fallback Behavior
- If no IBT available -> standalone solver uses pre-built `TrackProfile` JSON from `data/tracks/`
- If no telemetry signal -> uses `TelemetrySignal` quality system: trusted -> proxy -> fallback -> unknown
- If settle time not measurable -> damper solver uses heuristic targets instead of measured response
- If aero map missing for car/wing combo -> falls back to nearest available wing angle

### Ride Height Reconciliation (post-Step 3)
After Steps 2 and 3 are solved, `reconcile_ride_heights()` in `solver/rake_solver.py` re-predicts static RH using the `RideHeightModel` regression (which depends on heave rate, camber, perch, spring rate). **This model is BMW/Sebring-calibrated only** (R-squared=0.52 rear, R-squared=0.15 front). For Ferrari/Acura, it falls back to `front_intercept=30.0` with zero coefficients — essentially a constant.

### Legality / Veto Flow
1. **Setup fingerprinting** (`solver/setup_fingerprint.py`): Candidates are hashed to fingerprints (wing, RH bucket, spring bucket, etc.)
2. **Failed cluster matching**: If a fingerprint matches a previously failed setup cluster, it gets:
   - Hard veto: +1,000,000 ms penalty (effectively blocked)
   - Soft veto: +50,000 ms penalty (deprioritized)
3. **Legal validation** (`solver/legality_engine.py`): Checks garage ranges, coupling constraints
4. **Garage validation** (`output/garage_validator.py`): Predicts iRacing display values, auto-corrects if out of range
5. **Prediction sanity** (`solver/scenario_profiles.py::prediction_passes_sanity()`): Scenario-specific limits on predicted telemetry values

### Where Final Selection Happens
For the base solve: `solve_chain.py::run_base_solve()` returns the sequential solver result.
For legal-manifold search: `solver/legal_search.py` generates candidate families -> scores each -> classifies as `best_robust` / `best_aggressive` / `best_weird` -> scenario profile's `preferred_result_key` selects the winner.

---

## 4. How "Best" Is Chosen

### 4.1 ObjectiveFunction (`solver/objective.py`)
**Canonical scoring function.** Used by both `solve_chain.py` and `legal_search.py`.

```
total_score_ms = w_lap_gain * lap_gain_ms
               - w_platform * platform_risk_ms
               - w_driver * driver_mismatch_ms
               - w_uncertainty * telemetry_uncertainty_ms
               - w_envelope * envelope_penalty_ms
               - w_staleness * staleness_ms
               - w_empirical * empirical_penalty_ms
```

All terms in milliseconds. Higher score = better candidate.

**Scenario-specific weights** (`solver/scenario_profiles.py`):

| Profile | w_platform | w_driver | w_uncertainty | w_envelope | w_staleness | w_empirical | Preferred |
|---|---|---|---|---|---|---|---|
| single_lap_safe (default) | 0.75 | 0.30 | 0.40 | 0.55 | 0.15 | 0.20 | best_robust |
| quali | 0.90 | 0.35 | 0.45 | 0.50 | 0.20 | 0.25 | best_aggressive |
| sprint | 1.00 | 0.45 | 0.55 | 0.70 | 0.30 | 0.35 | best_robust |
| race | 1.20 | 0.55 | 0.70 | 0.85 | 0.35 | 0.45 | best_robust |

`w_lap_gain` is always 1.0. Runtime calibration guard clamps it to <=0.25 for BMW/Sebring because correlation is too weak.

### 4.2 Lap Gain Components
- LLTD balance deviation from OptimumG baseline
- Damping deviation from target zeta (0.68/0.23/0.47/0.20)
- Rebound/compression ratio deviation from 2:1
- DF balance error * 20 ms/%
- Camber deviation
- Diff/ARB extremes
- TC deviation
- Carcass temperature outside optimal window

### 4.3 Platform Risk Components
- Bottoming risk (front RH below safe threshold)
- Vortex risk (front RH near vortex burst — wing-angle-specific threshold)
- Slider exhaustion (heave slider near travel limit)
- RH collapse risk (ride height variance too high)

### 4.4 k-NN Empirical Scoring (`solver/session_database.py`)
- Loads 76+ BMW/Sebring sessions
- Finds k-nearest neighbors in setup parameter space
- Predicts telemetry outcomes from neighbors
- Weight: `w_empirical = 0.20-0.45` depending on scenario
- **Disabled in explore mode** (`w_empirical = 0.0`)
- **BMW/Sebring only** — no session database for other cars

### 4.5 Comparison Module (`comparison/score.py`) — PARALLEL SCORING PATH
- Completely separate scoring system from `solver/objective.py`
- Scores sessions based on lap time, stability metrics, handling quality
- Can synthesize "optimal" setup from multiple sessions
- **Potential conflict:** defines "best" differently from the pipeline's objective function

### 4.6 Interaction with Vetoes
- Hard vetoes add +1M ms (blocks candidate)
- Soft vetoes add +50K ms (deprioritizes)
- Vetoes are applied **after** scoring — a vetoed candidate keeps its physics score but gets buried
- This means a vetoed candidate's absolute score can look good in isolation but will never be selected

---

## 5. Accuracy / Reliability Risks

### 5.1 Scoring / Model-Calibration Risks

**ROOT CAUSE: BMW-only calibration chain**

| Component | BMW/Sebring | Ferrari | Acura | Impact |
|---|---|---|---|---|
| `GarageOutputModel` | Calibrated (31 setups) | None (defaults to None) | None | Garage validator cannot predict display values -> auto-corrections may be wrong |
| `DeflectionModel` | Calibrated (31 setups, R-sq=0.90+) | Uses BMW defaults | Uses BMW defaults | Shock/spring/slider deflections predicted using wrong car's physics |
| `RideHeightModel` | R-sq=0.52 rear, R-sq=0.15 front | All coefficients=0 -> constant 30mm | All coefficients=0 | Static RH reconciliation fails for non-BMW |
| Heave calibration data | JSON exists | None | None | Sigma prediction falls back to physics model (less accurate) |
| Session database (k-NN) | 76+ sessions | Empty | Empty | k-NN scoring disabled (no neighbors) |
| Damper zeta targets | IBT-calibrated (top-15 fastest) | Uses BMW targets | Uses BMW targets | Damper solver targets wrong response for non-BMW cars |
| Objective calibration weights | Searched (Spearman -0.12) | Uses BMW weights | Uses BMW weights | Scoring function not validated for non-BMW |

**This is why Ferrari and Acura produce bad setups.** Every regression coefficient, calibration constant, and validation threshold was fit to BMW/Sebring data. Non-BMW cars silently inherit these values, and the garage validator "corrects" their solutions using BMW-specific models — making them worse, not better.

**Specific Ferrari problems (VERIFIED from code):**
- `front_heave_spring_nmm=50.0` marked ESTIMATE — Ferrari uses indexed heave settings
- `rear_m_eff_kg=2870.0` marked ESTIMATE — wildly different from BMW's calibrated value
- ARB stiffnesses all marked ESTIMATE
- `tyre_load_sensitivity=0.25` marked ESTIMATE (BMW is 0.20)
- `track_width_mm=1600.0` marked ESTIMATE
- `cg_height_mm=340.0` marked ESTIMATE
- `front_torsion_c=0.001282` is calibrated from garage screenshots — good
- `rear_motion_ratio=0.612` is calibrated from LLTD back-solve — good
- But these calibrated values are undermined by uncalibrated surrounding models

**Ferrari garage ground truth (VERIFIED from `ferrari.json` — full garage parameter dump):**

The `ferrari.json` file reveals the Ferrari 499P's actual garage structure, exposing critical mismatches between how the solver models this car and how iRacing implements it:

| Parameter Area | ferrari.json Reality | Code Assumption | Mismatch Severity |
|---|---|---|---|
| **Heave springs** | INDEX values (" 5", " 8"), not N/mm. Range: integer indices mapping to internal rates | `front_heave_spring_nmm=50.0` ESTIMATE — solver treats as continuous N/mm | **Critical** — solver optimizes a continuous value that maps to a discrete indexed setting |
| **Torsion bar OD** | INDEX values (" 2", " 1"), not mm. Combined with torsion bar turns (-0.250 to 0.250) | Solver uses continuous OD in mm with C*OD^4 formula | **Critical** — solver outputs continuous OD, must snap to discrete index |
| **Corner dampers** | 0-40 click range (LS comp, LS rbd, HS comp, HS rbd, plus HS comp slope 0-11) | BMW uses 0-11 range. Code may assume BMW damper click resolution | **High** — 4x finer resolution means different sensitivity per click |
| **Heave dampers** | SEPARATE heave damper settings exist: `hfLowSpeedCompDampSetting=10`, `hfLowSpeedRbdDampSetting=10`, `hrLowSpeedCompDampSetting=10`, `hrLowSpeedRbdDampSetting=10` (range 1-20) | Solver has no separate heave damper tuning path — only corner dampers | **High** — an entire category of tunable parameters is ignored |
| **Internal spring rates** | `fSideSpringRateNpm=115170.265625` (N/m), `rSideSpringRateNpm=105000` (N/m) — computed from indexed settings | Not accessible to solver; could be used for calibration | **Medium** — valuable calibration data available but unused |
| **Camber** | DERIVED values (is_derived: true): LF=0.7, RF=-0.7, LR=-0.3, RR=0.3 degrees — NOT directly settable | Solver outputs camber as a settable parameter | **Medium** — camber is a consequence of other settings, not independent |
| **Front diff** | Has FRONT diff preload (-50 to 50 Nm) AND rear preload (0-150 Nm) | BMW has rear diff only. Supporting solver may not handle front diff | **Medium** — additional physics dimension unmapped |
| **Brake system** | Migration maps (1-6), migration gain (-4% to +4%), front/rear master cyl (16.8-20.6mm), pad compound selection | Solver only outputs brake bias % | **Medium** — brake physics is more complex than single bias value |
| **Diff ramps** | String values ("More Locking", etc.), not numeric angles | Code may expect numeric coast/drive ramp values | **Low** — need string-to-physics mapping |
| **Packer thicknesses** | All zeros for all corners + heave elements | Not modeled in solver | **Low** — currently inactive but could be a tuning variable |
| **Tyre pressures** | 152-207 kPa range, 0.5 kPa step | Solver targets 155-170 kPa hot window | **Low** — range is compatible |
| **Wing angle** | 12-17 deg range (integer steps) | Aero maps exist for Ferrari wing angles | **Low** — compatible |

**Key ferrari.json insights for calibration:**
1. **Indexed heave springs reveal internal rates** — `fSideSpringRateNpm=115170.265625` at index " 5" = 115.17 N/mm wheel rate. This is the ground truth the solver needs. By collecting several index-to-N/mm pairs, we can build the Ferrari heave spring mapping table.
2. **Heave dampers are hidden but settable** — These don't appear in the standard garage UI tabs but ARE parameters in the .sto file. The solver should either tune them or hold them at validated baselines.
3. **Camber is derived, not set** — The solver's Step 5 (wheel geometry) outputs camber as if it's independently adjustable. For Ferrari, camber is a function of ride height, spring rates, and geometry. The solver should either skip camber output or compute it as a derived consequence.
4. **Corner damper click range is 4x BMW** — The damper solver's click sensitivity calculations (ms per click) must be rescaled. A 1-click change on Ferrari (0-40 range) is ~1/4 the effect of a 1-click change on BMW (0-11 range).

**Specific Acura problems (VERIFIED from code):**
- `front_torsion_c=0.0008036` — marked "ESTIMATE — same as BMW until calibrated" but Acura is ORECA chassis, not Dallara
- `aero_compression` front=15.0mm, rear=8.0mm — both ESTIMATE, no aero map calibration
- Heave+roll damper architecture -> solver treats as per-corner (mismatched abstraction)
- `heave_spring_defl_max_intercept_mm=0.0`, `heave_spring_defl_max_slope=0.0` — no heave travel model at all
- `defl_static_intercept=0.0`, `defl_static_heave_coeff=0.0` — no deflection model
- Front heave damper "ALWAYS bottomed" per code comments — normal Acura characteristic but solver doesn't know this
- Rear RH misses aero targets because aero maps are uncalibrated

### 5.2 Path Fragmentation Risks
- `comparison/` module has its own scoring that doesn't use `solver/objective.py`
- `pipeline/reason.py` (multi-IBT) computes its own modifiers via a different code path than single-IBT `produce.py`
- `solver/solve.py` has its own CLI that can bypass the pipeline's garage-validation step
- `solver/full_setup_optimizer.py::optimize_if_supported()` is an additional optimization layer that only runs for BMW/Sebring

### 5.3 Telemetry Underuse Risks
- High-speed m_eff filtering available (`front_heave_vel_p95_hs_mps` at >200 kph) but **not used** by solver — uses lap-wide stats instead
- `lltd_measured` is actually a roll stiffness distribution proxy, not true LLTD — the solver uses this for LLTD targeting
- Settle time extraction requires >=3 clean events; many laps fail this -> damper solver falls back to heuristics
- Brake wheel asymmetry (`front_brake_wheel_decel_asymmetry_p95_ms2`) is extracted but not wired to any solver step

### 5.4 Validation Gaps
- Objective correlation Spearman -0.12 is effectively noise (not statistically significant)
- Weight search found `lap_gain=1.25` as best but runtime guard clamps to 0.25 — the system doesn't trust its own scoring
- Holdout validation only tests track-aware vs trackless, not scenario-specific
- No cross-validation of scoring across different tracks
- Torsion-ARB coupling gamma=0.25 from single BMW data point — unvalidated

### 5.5 Support Asymmetry

| Car/Track | Observations | GarageOutputModel | DeflectionModel | Heave Calibration | Session DB | Objective Weights | Tier |
|---|---|---|---|---|---|---|---|
| BMW/Sebring | 73 (72 non-vetoed) | Yes | Yes | Yes | Yes (76+) | Yes (searched) | calibrated |
| Ferrari/Sebring | 9 | No | No (BMW defaults) | No | No | No (BMW) | partial |
| Cadillac/Silverstone | few | No | No (BMW defaults) | No | No | No (BMW) | exploratory |
| Acura/Hockenheim | 7 | No | No (all zeros) | No | No | No (BMW) | exploratory |
| Porsche/Sebring | 0 | No | No | No | No | No | unsupported |

---

## 6. Telemetry Channel Audit

### Solve-Critical Channels (directly affect solver output)

| Channel / Derived Metric | Read In | Analyzed In | Affects Solver Step | Notes |
|---|---|---|---|---|
| `LF/RF/LR/RRrideHeight` | `extract.py` | `extract.py` (aero compression, variance, excursion) | Step 1 (rake), Step 2 (bottoming) | Core platform channels |
| `CFSRrideHeight` (splitter) | `extract.py` | `extract.py` (scrape events) | Step 2 (floor clearance) | Most important aero channel |
| `HFshockDefl` / `HRshockDefl` | `extract.py` | `extract.py` (travel used %) | Step 2 (heave travel constraint) | BMW-specific DeflMax formula |
| `LF/RF/LR/RRshockVel` | `extract.py` | `extract.py` (p95/p99, oscillation freq) | Step 6 (damper targeting) | Acura: synthesized from heave+/-roll |
| `SteeringWheelAngle` + `YawRate` | `extract.py` | `extract.py` (understeer angle) | Step 4 (LLTD/balance) | Combined with Speed for Ackermann |
| `VelocityX` / `VelocityY` | `extract.py` | `extract.py` (body slip angle) | Step 4 (rear grip validation) | Used for slip angle computation |
| `Speed` / `LatAccel` / `LongAccel` | `extract.py` | `extract.py` (cornering masks, speed regimes) | All steps (speed-dependent constraints) | Fundamental filtering channels |
| `Roll` | `extract.py` | `extract.py` (roll gradient, roll distribution proxy) | Step 4 (LLTD proxy) | "lltd_measured" is actually roll stiffness dist |
| `Pitch` | `extract.py` | `extract.py` (pitch at speed, braking pitch range) | Step 2 (platform stability) | Large braking pitch -> heave support inadequate |
| `LF/RF/LR/RRtempL/M/R` | `extract.py` | `extract.py` (surface temp spread) | Step 5 (camber validation) | Positive spread -> inner hotter -> camber issue |
| `LF/RF/LR/RRbrakeLinePress` | `extract.py` | `extract.py` (hydraulic brake split) | Supporting (brake bias baseline) | NOT brake torque split |
| `dcBrakeBias` | `extract.py` | `extract.py` (live brake bias) | Supporting (bias targeting) | Driver compensation detection |
| `Brake` / `Throttle` | `extract.py` | `driver_style.py` (trail brake depth, throttle progressiveness) | Modifiers (driver style -> solver adjustments) | Core driver profiling channels |

### Diagnostic-Only Channels (used for diagnosis, may adjust modifiers)

| Channel / Derived Metric | Where Read | Where Analyzed | Effect |
|---|---|---|---|
| `LF/RFspeed`, `LR/RRspeed` | `extract.py` | `extract.py` (lock ratio, power slip) | Diagnosis priority 5 (grip) -> may trigger modifier |
| `BrakeABSactive` / `BrakeABScutPct` | `extract.py` | `extract.py` (ABS activity) | Diagnosis only — not wired to solver |
| `HFshockVel` / `HRshockVel` (heave) | `extract.py` | `extract.py` (oscillation freq, heave regime classification) | Oscillation >1.5x natural -> bump zeta_hs_rear |
| `LF/RF/LR/RRtempCL/CR` (carcass) | `extract.py` | `extract.py` (carcass gradient) | Deep camber validation (confirms surface temp) |
| `dcTractionControl` | `extract.py` | `extract.py` (TC adjustments count) | Driver compensation detection |
| Derived: `front_rh_settle_time_ms` | `extract.py` | `extract.py` (event-based settle) | Damper targeting (if quality="trusted") |

### Context-Only Channels (extracted but not solver-affecting)

| Channel / Derived Metric | Where Read | Purpose |
|---|---|---|
| `EnergyERSBatteryPct` | `extract.py` | Battery state context (low battery -> less MGU-K torque) |
| `TorqueMGU_K` | `extract.py` | Peak hybrid torque (not solver-wired) |
| `TireLF_RumblePitch` / `TireRF_RumblePitch` | `build_profile.py` | Kerb detection for spatial masking |
| `LapDist` | `extract.py` | Spatial coordinates for kerb mask |
| `SessionTime` / `Lap` | IBT parser | Lap identification |
| `FuelLevel` | `extract.py` | Fuel load context |
| `Gear` | `extract.py` | Apex gear detection |

### Effectively Unused (extracted but not reaching solver)

| Channel / Derived Metric | Where Read | Why Unused |
|---|---|---|
| `front_brake_wheel_decel_asymmetry_p95_ms2` | `extract.py` | Extracted but not wired to any solver step or modifier |
| `front_heave_vel_p95_hs_mps` (>200kph filtered) | `extract.py` | Available for filtered m_eff but solver uses lap-wide stats instead |
| `front_rh_std_hs_mm` (>200kph filtered) | `extract.py` | Same — available but unused |
| `front_heave_vel_ls_pct` / `front_heave_vel_hs_pct` | `extract.py` | Heave regime classification — informational only |
| In-car adjustment counts (ARB, TC adjustments) | `extract.py` | Counts driver compensation but doesn't feed back to solver |

---

## 7. Unused, Unwired, or Overlapping Code

### Dead / Effectively Dead
| Module/Function | Evidence | Recommendation |
|---|---|---|
| `solver/stint_reasoner.py` | Imported in `produce.py` (`solve_stint_compromise`) but only called when scenario="race" with stint data — rarely exercised | Audit; if untested, mark experimental |
| `solver/bmw_rotation_search.py::preserve_candidate_rotation_controls()` | BMW-specific search optimization; imported in `produce.py` | BMW-only; document as such |
| `solver/full_setup_optimizer.py::optimize_if_supported()` | Only runs for BMW/Sebring; other cars skip silently | Document limitation; extend or remove |
| `solver/bmw_coverage.py` | `build_parameter_coverage`, `build_search_baseline`, `build_telemetry_coverage` — BMW-specific utilities | BMW-only coverage analysis tools |
| `--space` flag in `__main__.py` | Setup space exploration entry point | Experimental; document or remove |

### Overlapping / Parallel Paths
| Module | Overlaps With | Risk |
|---|---|---|
| `comparison/score.py::score_sessions()` | `solver/objective.py::ObjectiveFunction.evaluate()` | Different scoring criteria for "best" — user could get conflicting recommendations |
| `comparison/synthesize.py::synthesize_setup()` | `pipeline/produce.py` (single-IBT solve) | Synthesis uses weighted blending; pipeline uses physics solve -> different outputs for same data |
| `pipeline/__main__.py` | `__main__.py` | Duplicate routing; `__main__.py` should be canonical |
| `solver/solve.py::main()` | `__main__.py` | Separate CLI that bypasses pipeline's garage validation and learning steps |

### Partially Wired
| Module/Function | What's Missing |
|---|---|
| `solver/predictor.py::predict_candidate_telemetry()` | Prediction used for sanity checks but predicted telemetry values are not feedback-corrected per car |
| `learner/empirical_models.py::fit_prediction_errors()` | Corrections exist but require >=3 sessions — Ferrari (9) qualifies but corrections may be wrong due to BMW-centric physics |
| `solver/session_database.py` (k-NN) | Only has BMW/Sebring data; returns empty for other cars |
| `solver/decision_trace.py::build_parameter_decisions()` | Generates provenance but not used for validation feedback |

---

## 8. Repo / Runtime Hygiene Issues

### Command Confusion
**6+ overlapping entry points:**
```bash
python -m ioptimal           # Unified entry (recommended)
python -m pipeline           # Duplicate of above
python -m pipeline.produce   # Same as above
python -m solver.solve       # Bypasses pipeline
python -m analyzer           # Analysis only
python -m learner.ingest     # Learning only
python -m comparison         # Separate scoring system
python -m webapp             # Web UI
python -m desktop            # Desktop app
```
**Users should only need 1 command.** The current state is confusing.

### No `pyproject.toml` or `setup.py`
- Dependencies in `requirements-dev.txt` and `requirements-desktop.txt`
- No installable package — must run from project root
- No `python -m pip install -e .` support
- Module imports assume project root is on `PYTHONPATH`

### Data Files in Source Tree
- `data/learnings/` contains runtime-generated observation JSON files mixed with static data
- `data/aeromaps_parsed/` contains pre-processed numpy arrays
- No `.gitignore` audit for generated files vs tracked files

### Unclear Which Module Owns What
- `car_model/garage.py` vs `output/garage_validator.py` — both deal with garage physics
- `car_model/cars.py` at 2100+ lines — single file with all 5 car definitions
- `solver/objective.py` described as "31k LOC" — likely needs decomposition

---

## 9. Recommended Module Status Map

### Production-Critical
- `solver/solve_chain.py` — orchestration
- `solver/rake_solver.py` — Step 1
- `solver/heave_solver.py` — Step 2
- `solver/corner_spring_solver.py` — Step 3
- `solver/arb_solver.py` — Step 4
- `solver/wheel_geometry_solver.py` — Step 5
- `solver/damper_solver.py` — Step 6
- `solver/supporting_solver.py` — brake/diff/TC/tyres
- `solver/objective.py` — canonical scoring
- `solver/scenario_profiles.py` — weight profiles
- `solver/legality_engine.py` — legal validation
- `solver/legal_search.py` — manifold search
- `solver/modifiers.py` — diagnosis -> targets
- `pipeline/produce.py` — single-IBT orchestration
- `analyzer/extract.py` — telemetry extraction
- `analyzer/diagnose.py` — handling diagnosis
- `analyzer/segment.py` — corner-by-corner
- `analyzer/driver_style.py` — driver profiling
- `analyzer/setup_reader.py` — current setup parsing
- `car_model/cars.py` — car definitions
- `car_model/garage.py` — garage output model
- `output/setup_writer.py` — .sto export
- `output/garage_validator.py` — pre-write validation
- `track_model/build_profile.py` — track profile generation
- `track_model/ibt_parser.py` — IBT binary parser
- `__main__.py` — unified CLI entry

### Supported Secondary
- `pipeline/reason.py` — multi-IBT reasoning
- `pipeline/report.py` — engineering report generation
- `learner/knowledge_store.py` — persistent observations
- `learner/delta_detector.py` — causality detection
- `learner/empirical_models.py` — correction fitting
- `learner/ingest.py` — CLI ingestion
- `learner/recall.py` — query interface
- `validation/run_validation.py` — evidence builder
- `validation/objective_calibration.py` — weight search
- `validation/observation_mapping.py` — setup registry

### Experimental
- `solver/full_setup_optimizer.py` — BMW-only optimizer
- `solver/bmw_coverage.py` — BMW-specific coverage tools
- `solver/bmw_rotation_search.py` — BMW-specific search
- `solver/stint_reasoner.py` — stint compromise (race scenario)
- `solver/session_database.py` — k-NN empirical scoring (BMW-only data)
- `solver/decision_trace.py` — provenance tracking
- `comparison/` module (all files) — parallel scoring system
- `webapp/` — local web UI
- `desktop/` — desktop app with watcher
- `watcher/` — IBT auto-detection
- `teamdb/` — team database
- `server/` — team REST API

### Legacy / Merge / Deprecate Candidates
- `pipeline/__main__.py` — duplicate of `__main__.py`
- `solver/solve.py::main()` CLI — should be unified under `__main__.py`
- `comparison/score.py` scoring logic — should either use `solver/objective.py` or be clearly documented as alternative

---

## 10. Fix Plan in Priority Order

### P0: Why Non-BMW Cars Produce Bad Setups (Structural Fix)

**Problem:** Ferrari and Acura inherit BMW's `DeflectionModel` (all regression coefficients from BMW/Sebring data), have no `GarageOutputModel`, and use default-zero `RideHeightModel` coefficients. The garage validator "corrects" their solutions using BMW physics — making outputs worse.

**Fix sequence:**
1. **Add `garage_output_model=None` bypass in garage validator** — when `GarageOutputModel` is None, skip correlation corrections that assume BMW regressions. Currently the validator uses `DeflectionModel()` defaults which ARE BMW-specific even when `GarageOutputModel` is None.
2. **Create per-car `DeflectionModel` instances** — Ferrari and Acura need their own calibrated shock/spring/slider deflection models from their garage screenshots. This requires collecting 5+ garage screenshots per car with varied setup parameters.
3. **Build Ferrari indexed-setting mapping tables** — `ferrari.json` proves heave springs and torsion bars are INDEXED (not continuous). The solver must map its continuous physics output to the nearest legal index. Use internal spring rates from `ferrari.json` (e.g., index " 5" -> `fSideSpringRateNpm=115170.265625` = 115.17 N/mm) to build the index-to-physical-rate lookup table. Collect 3-5 more index/rate pairs to complete the mapping.
4. **Create per-car `RideHeightModel` instances** — calibrate front/rear static RH regressions from each car's IBT data. Until calibrated, use a simple "pinned" model (front=30mm constant, rear = pushrod-based with measured slope).
5. **Handle Ferrari's derived camber** — `ferrari.json` shows camber values are `is_derived: true`, meaning the solver's Step 5 camber output cannot be directly applied. Either skip camber for Ferrari or compute it as a consequence of ride height + spring settings.
6. **Rescale damper solver for Ferrari's 0-40 click range** — `ferrari.json` confirms corner dampers use 0-40 clicks (not BMW's 0-11). The damper click sensitivity must be recalculated: 1 Ferrari click ~ 1/4 the effect of 1 BMW click. Also add support for the SEPARATE heave damper settings (LS/HS comp/rbd, range 1-20) that `ferrari.json` reveals.
7. **Make damper zeta targets car-specific** — the current 0.68/0.23/0.47/0.20 targets are from BMW fastest sessions. Ferrari/Acura dampers have different architectures and different optimal zeta values.
8. **Add Ferrari front diff + brake migration to supporting solver** — `ferrari.json` reveals front diff preload (-50 to 50 Nm) and brake bias migration maps (1-6) + gain (-4% to +4%). These are unmapped parameters that affect handling.
9. **Flag ESTIMATE values in solver output** — when the solver uses an estimated (not calibrated) parameter, the report should warn the user: "ARB stiffness is estimated — this setup may need manual tuning."

### P1: Improve Objective Correlation (Scoring Accuracy)

**Problem:** Spearman -0.12 is noise. The scoring function cannot distinguish good from bad setups.

**Fix sequence:**
1. **Re-run objective calibration** with the new IBT-calibrated damper zeta targets (updated 2026-03-27, pending validation)
2. **Audit lap_gain components individually** — run per-component correlation to find which terms correlate in the wrong direction and remove/invert them
3. **Add k-NN ablation** — test physics-only vs physics+k-NN to quantify the k-NN contribution
4. **Fix m_eff high-speed filtering** — use `front_heave_vel_p95_hs_mps` (>200kph only) instead of lap-wide stats
5. **Expand torsion-ARB coupling validation** — current gamma=0.25 from single data point; either validate across OD range or remove

### P2: Simplify Commands and Entry Points

**Problem:** 6+ entry points with overlapping flags. Users don't know which to use.

**Proposed simplified interface:**
```bash
# The ONE command (all modes auto-detected)
ioptimal solve --car bmw --ibt session.ibt --wing 17          # Single IBT
ioptimal solve --car bmw --ibt s1.ibt s2.ibt --wing 17        # Multi IBT
ioptimal solve --car bmw --track sebring --wing 17             # No IBT (standalone)
ioptimal solve --car bmw --ibt session.ibt --wing 17 --free    # With legal search

ioptimal analyze --car bmw --ibt session.ibt                   # Diagnosis only
ioptimal learn --car bmw --ibt session.ibt                     # Ingest only
ioptimal validate --car bmw --track sebring                    # Run validation
ioptimal serve                                                  # Web UI
```

**Implementation:**
1. Make `__main__.py` the single entry point with subcommands
2. Deprecate `pipeline/__main__.py`, `solver/solve.py::main()` CLI
3. Add `pyproject.toml` with `[project.scripts]` so `pip install -e .` creates an `ioptimal` command

### P3: Unify Scoring Paths

**Problem:** `comparison/score.py` defines "best" differently from `solver/objective.py`. Users can get conflicting recommendations.

**Fix:** Either:
- (a) Make `comparison/` use `solver/objective.py` for scoring (unified scoring)
- (b) Clearly document that `comparison/` is a separate analysis tool with different criteria, and label its output accordingly

### P4: Wire Unused Telemetry

**Problem:** Several extracted metrics never reach the solver.

1. **Wire `front_heave_vel_p95_hs_mps`** to m_eff correction (filtered to >200kph)
2. **Wire `front_brake_wheel_decel_asymmetry_p95_ms2`** to brake bias modifier (asymmetry suggests caliper/pad issue)
3. **Wire in-car adjustment counts** to confidence scoring (many adjustments = driver compensating for bad base setup -> lower authority score)

### P5: Clean Up Overlapping Code

1. Unify `pipeline/__main__.py` into `__main__.py`
2. Remove `solver/solve.py::main()` CLI (keep `solve.py` as a module, remove `if __name__` block or redirect to `__main__.py`)
3. Document `solver/bmw_coverage.py` and `solver/bmw_rotation_search.py` as BMW-only tools
4. Add `__all__` exports to modules to clarify public APIs

---

## 11. Appendices

### A. Exact File/Function References

| Reference | File | Line (approx) | Purpose |
|---|---|---|---|
| `produce_result()` | `pipeline/produce.py` | ~100 | Single-IBT pipeline orchestrator |
| `reason_and_solve()` | `pipeline/reason.py` | ~50 | Multi-IBT reasoning engine |
| `run_base_solve()` | `solver/solve_chain.py` | ~50 | 6-step solver orchestration |
| `ObjectiveFunction.evaluate()` | `solver/objective.py` | ~200 | Canonical scoring function |
| `validate_and_fix_garage_correlation()` | `output/garage_validator.py` | ~76 | Pre-write garage validation |
| `write_sto()` | `output/setup_writer.py` | ~529 | .sto XML generation |
| `extract_measurements()` | `analyzer/extract.py` | ~100 | Telemetry extraction (60+ channels) |
| `diagnose()` | `analyzer/diagnose.py` | ~50 | Handling problem identification |
| `FERRARI_499P` | `car_model/cars.py` | 1686 | Ferrari car definition |
| `ACURA_ARX06` | `car_model/cars.py` | 1971 | Acura car definition |
| `BMW_M_HYBRID_V8` | `car_model/cars.py` | ~1400 | BMW car definition |
| `GarageOutputModel` | `car_model/garage.py` | ~88 | Garage display prediction model |
| `DeflectionModel` | `car_model/cars.py` | 345 | Static deflection regression model |
| `_CAR_PARAM_IDS` | `output/setup_writer.py` | 428 | Per-car .sto XML ID dispatch |
| `score_sessions()` | `comparison/score.py` | ~1 | Alternative scoring system |
| `prediction_passes_sanity()` | `solver/scenario_profiles.py` | 207 | Prediction sanity gating |
| Ferrari garage dump | `ferrari.json` | (full file) | Ground truth: indexed settings, internal spring rates, heave dampers, derived camber, front diff, brake migration |

### B. Open Questions / Uncertainties

1. **Is torsion-ARB coupling (gamma=0.25) real?** Only one BMW/Sebring data point. Physical mechanism plausible (rocker mount compliance) but unproven. Could be noise.
2. **Why is m_eff so different between cars?** BMW front_m_eff_kg varies widely (440-1000kg range mentioned); Acura ranges 319-641kg. This suggests the m_eff model is capturing more than just sprung mass.
3. **Should the objective function use absolute lap time or relative improvement?** Current scoring uses absolute penalty ms — but different tracks have different lap times. A 50ms penalty on Sebring (1:59) vs Silverstone (1:45) has different significance.
4. **What happens when the garage validator overcorrects?** It auto-adjusts perch offset and heave rate to meet constraints — but for non-BMW cars, the constraints themselves are wrong.
5. **Is the 73-observation BMW dataset diverse enough?** If most sessions used similar setups, the k-NN and empirical corrections may be biased toward a local optimum.

### C. Contradictions Between Docs and Code

| Doc Claim | Code Reality | Impact |
|---|---|---|
| CLAUDE.md: "Front model: R-sq=0.97, LOO RMSE=0.845mm" for rear RH | `cars.py` line 137: `rear_r_squared: float = 0.0` (default) — only BMW has non-zero R-squared | Misleading — only true for BMW |
| CLAUDE.md: "Ferrari rear torsion bar calibrated" | VERIFIED in code: `front_torsion_c=0.001282`, `rear_motion_ratio=0.612` | Accurate — these specific values are calibrated |
| CLAUDE.md: "Acura pipeline functional end-to-end" | VERIFIED: Pipeline runs but with all-zero deflection model and estimated aero compression | Technically true but outputs unreliable |
| CLAUDE.md: "6-step solver follows workflow ALWAYS" | VERIFIED: `solve_chain.py` always runs steps 1->6 in order | Accurate |
| CLAUDE.md: "73 observations, 72 non-vetoed" | Consistent with validation code references | Accurate |

### D. Critical Code Snippet: Why Non-BMW Deflection Models Fail

```python
# car_model/cars.py line 345
@dataclass
class DeflectionModel:
    """Calibrated static deflection models for .sto garage display values.
    Calibrated from BMW Sebring LDX ground truth..."""

    shock_front_intercept: float = 21.228    # BMW-specific!
    shock_front_pushrod_coeff: float = 0.226  # BMW-specific!
    # ... all 20+ coefficients are BMW/Sebring regressions

# Ferrari at line 1686:
FERRARI_499P = CarModel(
    ...
    # NOTE: no DeflectionModel specified -> inherits DeflectionModel()
    # which has BMW coefficients as defaults!
    deflection=DeflectionModel(),  # <-- BMW defaults silently applied
    ...
)

# Acura at line 1971:
ACURA_ARX06 = CarModel(
    ...
    # Heave spring model: ALL ZEROS
    heave_spring=HeaveSpringModel(
        heave_spring_defl_max_intercept_mm=0.0,  # No travel model!
        heave_spring_defl_max_slope=0.0,           # No travel model!
        defl_static_intercept=0.0,                 # No deflection model!
        defl_static_heave_coeff=0.0,               # No deflection model!
    ),
    ...
)
```

When the garage validator runs `validate_and_fix_garage_correlation()` for Ferrari, it calls `DeflectionModel.heave_spring_defl_static()` which computes `(-20.756 + 7.030/heave_nmm + ...)` — a BMW regression. For Ferrari with indexed heave settings (different physics), this returns garbage. The validator then "corrects" the solution based on this garbage prediction, making the output worse.

### E. Ferrari Garage Ground Truth (`ferrari.json`)

`ferrari.json` is a complete dump of the Ferrari 499P's garage parameter structure from iRacing. Key evidence:

```
Heave springs:  INDEXED — front=" 5", rear=" 8" (not continuous N/mm)
                Internal rates available: fSideSpringRateNpm=115170.265625 (115.17 N/mm)

Torsion bars:   INDEXED — front=" 2", rear=" 1" (not continuous OD mm)
                Combined with torsion bar turns: -0.250 to 0.250 range

Corner dampers: 0-40 click range (BMW is 0-11) for LS comp, LS rbd, HS comp, HS rbd
                PLUS HS comp damp slope: 0-11 range (5 damper params per corner, not 4)

Heave dampers:  SEPARATE settings exist (hidden from standard garage UI):
                hfLowSpeedCompDampSetting=10 (range 1-20)
                hfLowSpeedRbdDampSetting=10 (range 1-20)
                hrLowSpeedCompDampSetting=10 (range 1-20)
                hrLowSpeedRbdDampSetting=10 (range 1-20)

Camber:         DERIVED (is_derived: true) — LF=0.7, RF=-0.7, LR=-0.3, RR=0.3 degrees
                Not independently settable; consequence of geometry + springs

Front diff:     Preload -50 to 50 Nm (BMW has no front diff)

Brake system:   Bias 42-65%, migration maps 1-6, migration gain -4% to +4%,
                Front master cyl 16.8-20.6mm, rear master cyl 16.8-20.6mm,
                Pad compound selection

Diff ramps:     String values — "More Locking" etc. (not numeric angles)

Packers:        All zeros (inactive but tunable: LF/RF/LR/RR + front/rear heave)

Tyre pressures: 152-207 kPa range, 0.5 kPa step

Wing:           12-17 deg range (integer steps)
```

This data proves:
1. The solver's continuous-value optimization for heave springs and torsion bars **cannot map directly** to Ferrari's indexed garage settings without a lookup table
2. Ferrari has **5 damper parameters per corner** (not 4) plus **4 separate heave damper parameters** = 24 total damper values (vs BMW's ~20)
3. Camber is NOT a solver output for Ferrari — it's a readback from the sim
4. The front diff and brake migration system add physics dimensions the solver doesn't model

---

## Handoff Summary (for next model)

**You are picking up a physics-based iRacing setup solver.** The core solver (6-step constraint satisfaction) is sound and well-engineered. The problem is calibration asymmetry.

**The #1 fix needed:** Break the BMW-only calibration dependency. Ferrari and Acura silently inherit BMW regression coefficients for deflection models, ride height models, heave calibration, damper targets, and garage validation. This is why their outputs are bad — the garage validator "fixes" their solutions using BMW physics.

**Immediate actions:**
1. Make garage validator bypass correlation corrections when `GarageOutputModel is None` (Ferrari, Acura)
2. Set all non-calibrated `DeflectionModel` coefficients to zero for non-BMW (disable BMW regressions rather than apply them wrongly)
3. Build Ferrari indexed-setting mapping tables from `ferrari.json` ground truth (heave spring indices -> N/mm, torsion bar indices -> OD mm)
4. Rescale Ferrari damper solver for 0-40 click range and add heave damper support (LS/HS comp/rbd, range 1-20)
5. Re-run objective calibration with new damper zeta targets for BMW (pending since 2026-03-27)
6. Collect 5+ garage screenshots for Ferrari to build Ferrari-specific `DeflectionModel`

**Key files to read first:**
- `solver/solve_chain.py` (orchestration)
- `car_model/cars.py:1686` (Ferrari definition — note all ESTIMATE markers)
- `car_model/cars.py:1971` (Acura definition — note all-zero heave model)
- `output/garage_validator.py:76` (where BMW regressions are applied to all cars)
- `solver/objective.py` (scoring function — understand what "best" means)
- `ferrari.json` (Ferrari 499P full garage dump — ground truth for indexed settings, internal spring rates, derived camber, heave damper params, front diff, brake migration)

**Key constraint:** BMW/Sebring is the only calibrated path. Do not claim "optimal" for any car until objective correlation reaches Spearman <= -0.40 with stable holdout validation.
