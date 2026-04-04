# IOptimal System Architecture

> Physics-Based Setup Calculator for iRacing GTP/Hypercar  
> Document generated: 2026-04-03  
> Covers: Full codebase (~180 Python files, ~460K lines)

---

## Table of Contents

1. [High-Level Overview](#1-high-level-overview)
2. [Component Interactions](#2-component-interactions)
3. [Data Flow Diagrams](#3-data-flow-diagrams)
4. [Design Decisions and Rationale](#4-design-decisions-and-rationale)
5. [System Constraints and Limitations](#5-system-constraints-and-limitations)

---

## 1. High-Level Overview

### 1.1 Purpose

IOptimal is a **physics-first setup solver** for iRacing's GTP/Hypercar class. It reads in-game telemetry (IBT files), diagnoses handling problems from physics thresholds, profiles driver behavior, and produces optimized setup files (`.sto`) loadable directly into iRacing. Every parameter recommendation is justified by a physical constraint -- not pattern matching, not copying fast setups.

### 1.2 Supported Cars and Calibration Tiers (updated 2026-04-04)

| Car | Chassis | Primary Track | Tier | Observations | Calibrated Steps |
|-----|---------|--------------|------|-------------|-----------------|
| BMW M Hybrid V8 | Dallara LMDh | Sebring | **Calibrated** | 99 | 1-6 (all) |
| Ferrari 499P | Custom LMH | Sebring/Hockenheim | Partial | ~25 | 1-3 |
| Cadillac V-Series.R | Dallara LMDh | Silverstone | Exploratory | 4 | 2-3 |
| Acura ARX-06 | ORECA LMDh | Hockenheim/Daytona | Exploratory | 7 | — (all blocked) |
| Porsche 963 | Multimatic LMDh | Sebring | Unsupported | 2 | 1-3 |

### 1.2.1 Calibration Gate

The solver enforces a **calibration gate** (`car_model/calibration_gate.py`) at each of the 6 solver steps. Before running a step, the gate checks whether all required subsystems are calibrated from real measured data for that specific car. If any required subsystem is uncalibrated, the step is **blocked** and the system outputs step-by-step calibration instructions instead of a setup value.

This ensures the system **never outputs a setup value from an uncalibrated model**. The output for a blocked step tells the user:
- Which subsystem is missing calibration
- Exactly what data to collect in the sim (IBT sessions, garage screenshots, etc.)
- The exact CLI command to run after collecting the data
- How many data points are needed for reliable calibration

Per-step calibration requirements:
- **Step 1 (Rake/RH):** aero_compression, ride_height_model, pushrod_geometry
- **Step 2 (Heave/Third):** spring_rates
- **Step 3 (Corner Springs):** spring_rates
- **Step 4 (ARBs):** arb_stiffness, lltd_target
- **Step 5 (Geometry):** roll_gains
- **Step 6 (Dampers):** damper_zeta

### 1.3 System Boundary Diagram

```
+------------------------------------------------------------------+
|                        USER ENVIRONMENT                          |
|                                                                  |
|  iRacing Sim  -->  IBT files  -->  IOptimal Desktop App          |
|                                    +------------------------+    |
|                                    | Watcher (auto-detect)  |    |
|                                    | Webapp  (browser UI)   |    |
|                                    | Sync    (team push)    |    |
|                                    | Tray    (system icon)  |    |
|                                    +----------+-------------+    |
|                                               |                  |
+-----------------------------------------------|------------------+
                                                |
                          +---------------------v------------------+
                          |           TEAM SERVER (Cloud Run)       |
                          |  FastAPI + PostgreSQL (asyncpg)         |
                          |  /api/team, /observations, /knowledge  |
                          |  /setups, /leaderboard                 |
                          +----------------------------------------+
```

### 1.4 Module Map (14 Modules)

```
                    +-----------+
                    |  desktop/ |  Orchestration: watcher + sync + webapp + tray
                    +-----+-----+
                          |
              +-----------+-----------+
              |           |           |
        +-----v---+ +----v----+ +----v----+
        | watcher/ | | webapp/ | | teamdb/ |
        | IBT auto | | Web UI  | | ORM +   |
        | detect   | | FastAPI | | sync    |
        +-----+----+ +----+----+ +----+----+
              |            |           |
              +------+-----+           |
                     |                 |
               +-----v-----+    +-----v-----+
               |  pipeline/ |    |  server/  |
               |  IBT->.sto |    |  REST API |
               |  orchestr. |    |  Cloud Run|
               +--+--+--+--+    +-----------+
                  |  |  |
      +-----------+  |  +----------+
      |              |             |
+-----v-----+ +-----v------+ +---v--------+
| analyzer/  | |  solver/   | |  output/   |
| Telemetry  | |  6-step    | |  .sto gen  |
| extract,   | |  physics   | |  reports   |
| diagnose,  | |  chain +   | |  bundles   |
| driver     | |  search    | |  garage    |
| profiling  | |  manifold  | |  validator |
+-----+------+ +--+--+------+ +------------+
      |            |  |
+-----v-----+ +---v--v-----+
| track_model| | car_model/ |
| IBT parser | | 5 cars,    |
| TrackProfile| | legality, |
|            | | registry,  |
|            | | calib_gate |
+-----+------+ +------+-----+
      |                |
+-----v-----+   +-----v------+
| aero_model/|   | learner/   |
| Aero maps  |   | Knowledge  |
| gradients  |   | store,     |
| surfaces   |   | empirical  |
|            |   | models     |
+------------+   +------------+

Cross-cutting: validation/, comparison/
```

---

## 2. Component Interactions

### 2.1 Module Dependency Graph

```
pipeline/produce.py (master orchestrator)
  |
  +-- track_model/build_profile.py      Build TrackProfile from IBT
  |     +-- track_model/ibt_parser.py   Zero-copy IBT binary parser
  |
  +-- analyzer/extract.py               Extract 150+ telemetry metrics
  |     +-- analyzer/segment.py         Corner-by-corner segmentation
  |     +-- analyzer/driver_style.py    Driver behavior profiling
  |     +-- analyzer/setup_reader.py    Parse current garage from IBT YAML
  |     +-- analyzer/diagnose.py        6-priority handling diagnosis
  |     |     +-- analyzer/adaptive_thresholds.py   Track/car/driver-scaled
  |     |     +-- analyzer/causal_graph.py          Root-cause DAG
  |     +-- analyzer/state_inference.py  Car state detection (11 states)
  |     +-- analyzer/stint_analysis.py   Multi-lap degradation analysis
  |     +-- analyzer/telemetry_truth.py  Signal quality framework
  |
  +-- aero_model/gradient.py            Aero sensitivity at operating point
  |     +-- aero_model/interpolator.py  Cubic-interpolated aero surfaces
  |
  +-- solver/modifiers.py               Diagnosis -> solver target adjustments
  +-- solver/solve_chain.py             6-step sequential chain
  |     +-- solver/rake_solver.py       Step 1: Ride heights / DF balance
  |     +-- solver/heave_solver.py      Step 2: Heave / third springs
  |     +-- solver/corner_spring_solver.py  Step 3: Corner springs
  |     +-- solver/arb_solver.py        Step 4: Anti-roll bars / LLTD
  |     +-- solver/wheel_geometry_solver.py  Step 5: Camber / toe
  |     +-- solver/damper_solver.py     Step 6: All damper clicks
  |     +-- solver/supporting_solver.py Supporting: brakes/diff/TC/pressures
  |           +-- solver/brake_solver.py   Physics-based brake bias
  |           +-- solver/diff_solver.py    Empirical diff model
  |
  +-- solver/legal_search.py            Legal manifold search
  |     +-- solver/legal_space.py       23-dimension search space
  |     +-- solver/legality_engine.py   Garage + physics validation
  |     +-- solver/objective.py         Multi-term scoring function
  |     +-- solver/grid_search.py       4-layer hierarchical search
  |     +-- solver/candidate_ranker.py  Score candidates vs telemetry
  |
  +-- car_model/cars.py                 5 vehicle physical models
  |     +-- car_model/legality.py       Legal parameter ranges
  |     +-- car_model/setup_registry.py Canonical field mappings
  |     +-- car_model/garage.py         Garage output prediction model
  |     +-- car_model/auto_calibrate.py IBT-driven model calibration
  |
  +-- output/setup_writer.py            Generate iRacing .sto XML
  |     +-- output/garage_validator.py  Validate garage correlation
  |     +-- output/bundle.py            Multi-artifact output bundle
  |     +-- output/run_trace.py         Data provenance logging
  |     +-- output/delta_card.py        Change-only summary card
  |
  +-- learner/ingest.py                 Ingest session into knowledge store
  |     +-- learner/observation.py      Structured session snapshot
  |     +-- learner/delta_detector.py   Session-to-session causality
  |     +-- learner/empirical_models.py Regression fitting + corrections
  |     +-- learner/knowledge_store.py  JSON-backed persistent store
  |     +-- learner/recall.py           Query interface for solver
  |     +-- learner/cross_track.py      Cross-track knowledge transfer
  |     +-- learner/envelope.py         Telemetry envelope builder
  |     +-- learner/setup_clusters.py   Setup fingerprint clustering
  |
  +-- pipeline/reason.py                Multi-IBT reasoning engine (9-phase)
  +-- pipeline/report.py                Engineering report generator
  +-- pipeline/preset_compare.py        Race/sprint/quali preset comparison
```

### 2.2 Key Inter-Module Contracts

#### Analyzer -> Solver (via `solver/modifiers.py`)

The analyzer produces three outputs consumed by the solver:

| Analyzer Output | Solver Consumer | Mechanism |
|----------------|----------------|-----------|
| `Diagnosis.problems[]` | `SolverModifiers` | Balance problems -> LLTD offset; speed gradient -> DF balance offset; bottoming -> heave floor; settle time -> damper click offsets |
| `DriverProfile` | `SolverModifiers` | Smoothness -> zeta scaling (0.92x); aggressive steering -> HS clicks +1; limit cornering -> HS +1 both axles |
| `MeasuredState` | Direct use in solvers | Shock vel p99 drives excursion model; measured LLTD drives ARB target; settle time drives damper zeta; heave travel drives floor constraints |

All modifier values are **confidence-scaled** (0.25-1.0 range) and clamped: LLTD [-0.05, 0.05], DF balance [-1.5, 1.5], click offsets [-2, 2], zeta scale [0.80, 1.20].

#### Car Model -> All Modules

`CarModel` is the central physics truth. It composes 12 sub-models:

```
CarModel
  +-- AeroCompression        V^2-scaled aero compression (ref: 230 kph)
  +-- PushrodGeometry        Pushrod offset <-> ride height mappings
  +-- RideHeightModel        Multi-variable static RH regression
  +-- HeaveSpringModel       Effective mass, spring ranges, deflection models
  +-- DeflectionModel        16 regression models for garage display values
  +-- CornerSpringModel      Torsion C constant, OD options, motion ratios
  +-- ARBModel               Stiffness lookup tables, blade factor formula
  +-- WheelGeometryModel     Camber/toe ranges, roll gain coefficients
  +-- DamperModel            Click ranges, force/click, zeta targets, baselines
  +-- GarageRanges           Legal parameter limits with resolution steps
  +-- GarageOutputModel      Track-specific garage display predictions
  +-- FerrariIndexedControlModel  (Ferrari only) Index-to-physical lookup tables
```

#### Learner -> Solver (via `learner/recall.py`)

The learner provides 4 types of corrections to the solver:

1. **Prediction corrections** (min 3 sessions): `error = measured - predicted`, exponentially-weighted mean applied as offset to solver predictions
2. **Physics corrections** (min 5 sessions): roll gradient, LLTD, effective mass time-weighted means override physics defaults
3. **Lap time sensitivity** (min 2 controlled experiments): ranked parameter impact list guides search priority
4. **Recurring problem detection** (>50% sessions): flags persistent issues for modifier amplification

#### Aero Model -> Rake Solver

```
AeroSurface.find_rh_for_balance(target, rear_rh)
  --> front_rh satisfying DF balance target (bisection, 50 iterations)

AeroSurface.stall_proximity(front_rh)
  --> vortex burst constraint (8mm threshold, linear ramp 2-8mm)

compute_gradients(surface, car, front_rh, rear_rh, sigma)
  --> dBalance/dRH, dLD/dRH, aero window, L/D variance cost
```

### 2.3 Data Structure Flow Between Solver Steps

```
Step 1 (Rake) --> RakeSolution
  | dynamic_front_rh_mm, dynamic_rear_rh_mm
  | static_front_rh_mm, static_rear_rh_mm
  | pushrod offsets, DF balance, L/D, aero state
  v
Step 2 (Heave) --> HeaveSolution
  | front_heave_nmm, rear_third_nmm
  | perch offsets, bottoming margin, sigma
  | heave travel budget (slider, defl_max, available)
  v
Step 3 (Corner Springs) --> CornerSpringSolution
  | front_torsion_od_mm, front_wheel_rate_nmm
  | rear_spring_rate_nmm (raw spring, NOT wheel rate)
  | natural frequencies, heave/corner ratio
  |
  |--- RECONCILE ride heights (refine pushrods with actual spring values) ---|
  |--- PROVISIONAL Step 6 dampers -> re-solve Steps 2,3 with HS coefficients ---|
  v
Step 4 (ARBs) --> ARBSolution
  | front/rear ARB size + blade
  | LLTD achieved, roll stiffness breakdown
  | RARB sensitivity per blade for live strategy
  v
Step 5 (Geometry) --> WheelGeometrySolution
  | front/rear camber_deg, toe_mm
  | dynamic camber at peak lateral load
  | thermal prediction (laps to operating temp)
  v
Step 6 (Dampers) --> DamperSolution
  | 20 clicks: {LF,RF,LR,RR} x {LS comp, LS rbd, HS comp, HS rbd, HS slope}
  | zeta ratios per axle per regime
  | rebound/compression ratios
  v
Supporting --> SupportingSolution
  | brake_bias_pct, brake hardware (MC, pads, migration)
  | diff_preload_nm, diff_ramps, diff_clutch_plates
  | tc_gain, tc_slip
  | 4x tyre_pressure_cold_kpa
```

**Critical convention:** Rear coil spring `rear_spring_rate_nmm` is a **raw spring rate**. Must multiply by `car.corner_spring.rear_motion_ratio ** 2` to get wheel rate before passing to ARB/geometry/damper solvers.

---

## 3. Data Flow Diagrams

### 3.1 Primary Pipeline: IBT -> .sto

```
                         IBT File (binary telemetry)
                                |
                    +-----------v-----------+
                    |  Phase A: Track Model |
                    |  build_profile(ibt)   |
                    |  -> TrackProfile JSON  |
                    +-----------+-----------+
                                |
                    +-----------v-----------+
                    |  Phase B: Extract     |
                    |  extract_measurements |
                    |  -> MeasuredState     |
                    |    (150+ fields)      |
                    +-----------+-----------+
                                |
              +-----------------+------------------+
              |                 |                   |
    +---------v--------+ +-----v------+ +----------v---------+
    | Phase C: Segment | | Phase D:   | | Phase E: Diagnose  |
    | segment_lap()    | | Driver     | | adaptive thresholds|
    | -> CornerAnalysis| | analyze()  | | -> Diagnosis       |
    | per corner       | | -> Driver  | |   (6 priorities)   |
    |                  | |   Profile  | | + causal graph     |
    +--------+---------+ +-----+------+ | + state inference  |
             |                 |        | + overhaul assess  |
             +-----------------+--------+----------+---------+
                                                   |
                    +-----------v-----------+       |
                    |  Phase F: Aero Grads  |       |
                    |  compute_gradients()  |       |
                    |  -> AeroGradients     |       |
                    +-----------+-----------+       |
                                |                   |
                    +-----------v-----------+       |
                    |  Phase G: Modifiers   |<------+
                    |  compute_modifiers()  |
                    |  -> SolverModifiers   |
                    +-----------+-----------+
                                |
                    +-----------v-----------+
                    |  Phase H: 6-Step      |
                    |  Solver Chain          |
                    |                       |
                    |  1. Rake/RH           |
                    |  2. Heave Springs     |
                    |  3. Corner Springs    |
                    |    [reconcile RH]     |
                    |    [provisional damp] |
                    |    [re-solve 2,3]     |
                    |  4. ARBs / LLTD       |
                    |  5. Wheel Geometry    |
                    |  6. Dampers (final)   |
                    |  7. Supporting Params |
                    +-----------+-----------+
                                |
                    +-----------v-----------+
                    |  Phase J: Output      |
                    |                       |
                    |  - Legality validate  |
                    |  - Decision trace     |
                    |  - Candidate families |
                    |  - Legal manifold     |
                    |    search (optional)  |
                    |  - Garage correlation |
                    |    validate & fix     |
                    |  - write_sto()        |
                    |  - write JSON/report  |
                    +-----------+-----------+
                                |
                    +-----------v-----------+
                    |  Phase L: Auto-Learn  |
                    |  ingest_ibt()         |
                    |  -> Observation       |
                    |  -> Delta detection   |
                    |  -> Model re-fitting  |
                    +----------+------------+
                               |
                          .sto file
                          JSON summary
                          Engineering report
```

### 3.2 Legal Manifold Search Pipeline

```
Physics Baseline (from 6-step solve)
        |
        v
+-------+--------+
| Seed Generation |
| - Physics neighborhood (12% perturbation)
| - 6 edge-anchor families:
|   min_drag, max_platform, max_rotation,
|   max_stability, extreme_soft, extreme_stiff
| - Sobol quasi-random scatter (2x budget)
| - Uniform random scatter
+-------+--------+
        |
        v (thousands of candidates)
+-------+--------+
| Perch Offsets   |
| Compute dependent variables:
| - front_heave_perch (k=0.001614 mm/N/mm)
| - rear_third_perch  (k=0.8 mm/N/mm)
| - rear_spring_perch
+-------+--------+
        |
        v
+-------+--------+
| Fast Legality   |
| - Range checks (hard veto)
| - Ratio checks (soft penalty)
| - ~90% eliminated here
+-------+--------+
        |
        v (hundreds surviving)
+-------+--------+
| Objective Score |
| Layer 1: platform + lap_gain (Sobol filter)
| Layer 2: + LLTD + DF balance + envelope
| Layer 3: + damping ratios
| Layer 4: full objective (driver/telemetry/empirical)
+-------+--------+
        |
        v
+-------+--------+
| Materialize     |
| Top candidate -> SolveChainOverrides
| -> Full 6-step re-solve through solve_chain
| -> Garage validation + legality check
+-------+--------+
        |
        v
+-------+--------+
| Result Classes  |
| best_robust    (no soft penalties)
| best_aggressive (highest raw score)
| best_weird     (non-baseline family)
+----------------+
```

**Budget tiers:** quick (~3s, <=50K evals), standard (~4min, <=500K), exhaustive (~80min, <=10M), maximum (~5h, >10M).

### 3.3 Hierarchical Grid Search (4-Layer)

```
Layer 1: Platform Skeleton (Sobol over 6 dims)
  | wing, pushrods, heave, third, rear spring
  | Family biases shift sampling centers
  | Physics-filter to top N
  v
Layer 2: Balance Grid (exhaustive)
  | For each L1 skeleton:
  | torsion_OD x ARB_F x ARB_R (14x5x5 = 350)
  | x coarsened camber/bias/diff (up to 81)
  v
Layer 3: Damper Coordinate Descent
  | Independent sweep of 10 damper axes
  | Up to 3 passes until convergence
  v
Layer 4: Neighborhood Polish
  | Steepest descent in all 23 Tier A dims
  | +/-1 step, guarantees local optimum
```

### 3.4 Learner Knowledge Accumulation Flow

```
IBT Session
    |
    v
+---+---+
| Ingest |
| Phase 1: Analyze IBT (extract, segment, diagnose)
| Phase 2: Build Observation (150+ fields, setup + telemetry)
| Phase 3: Update index (idempotent, session_id dedup)
| Phase 4: Delta detection (vs most recent same car/track)
|   - Identify changed setup steps
|   - Map effects to known causality table (~40 pairs)
|   - Confidence: high (1 step changed) / medium (<=3) / low
| Phase 5: Fit empirical models
|   - 10 regression stages (roll gradient, LLTD vs ARB,
|     heave->variance, settle time vs damper, lap time sensitivity,
|     prediction feedback corrections, roll gain from thermals)
|   - Time decay: weight = 0.95^days
|   - Min 5 sessions for physics corrections
|   - Min 3 sessions for prediction corrections
| Phase 6: Generate insights
|   - Recurring problems (>50% sessions)
|   - Parameter trends
|   - High-confidence findings
| Phase 7: Update cross-track global model
+---+---+
    |
    v
Knowledge Store (JSON on disk)
    |
    +-- observations/  (1 per session)
    +-- deltas/        (between consecutive sessions)
    +-- models/        (per car/track empirical regressions)
    +-- insights/      (distilled human-readable findings)
    +-- index.json     (master session registry)
```

### 3.5 Team Sync Architecture

```
LOCAL MACHINE                              CLOUD SERVER
+-------------+                           +------------------+
| IBT watcher |                           | FastAPI (8080)   |
|  detects    |                           | PostgreSQL       |
|  new file   |                           |                  |
+------+------+                           |  /api/team       |
       |                                  |  /api/observations|
       v                                  |  /api/knowledge  |
+------+------+                           |  /api/setups     |
| learner/    |                           |  /api/leaderboard|
| ingest.py   |                           +--------+---------+
+------+------+                                    ^
       |                                           |
       v                                           |
+------+------+     push (30s interval)    +-------+--------+
| SyncClient  |  ========================>  | server/routes/ |
|             |                             | observations   |
| SQLite      |  <========================  |                |
| queue       |     pull (300s interval)    | aggregator.py  |
| ~/.ioptimal |                             | (fit team      |
| _app/       |     Exponential backoff     |  models)       |
| sync_queue  |     on failure (max 600s)   +----------------+
+-------------+

Offline resilience:
- Observations queued in local SQLite when server unreachable
- Up to 50 items per push batch
- Ordering preserved (stops on first failure)
- Pulled models cached in local SQLite for offline solver use
```

### 3.6 Multi-IBT Reasoning Pipeline (9 Phases)

```
N IBT Files
    |
    v
Phase 1: Extract (per-IBT: track, setup, telemetry, corners, driver, diagnosis)
    |          -> N SessionSnapshot objects
    v
Phase 2: All-Pairs Delta (N*(N-1)/2 comparisons)
    |          -> ParameterLearning per parameter (directional evidence, sensitivity)
    v
Phase 3: Corner Profiling (match corners across sessions by lap distance)
    |          -> Per-corner entry/apex/exit loss vs reference
    |          -> Consistent weaknesses (>50% slow by >0.05s)
    v
Phase 4: Speed-Regime Analysis (separate high-speed aero vs low-speed mechanical)
    |          -> Understeer gradient, dominant problem regime
    v
Phase 5: Target Profile (cherry-pick best metrics across sessions)
    |          -> Ranked gaps by estimated ms/lap impact
    v
Phase 6: Historical Integration (query knowledge store)
    |          -> Corrections, recurring problems, per-parameter evidence
    v
Phase 7: Physics Reasoning (cross-validation chains)
    |          -> Category scoring, quantified tradeoffs with ms/lap
    v
Phase 8: Confidence-Gated Modifiers
    |          -> Scaled solver modifiers, gated by sensitivity
    v
Phase 9: Solve + Report
    |          -> 6-step solver, candidate selection, engineering report
    v
Synthesized Setup (best-of across N sessions)
```

### 3.7 Webapp Request Flow

```
Browser (localhost:8765)
    |
    v
GET /runs/new  (form: upload IBT, select car/wing/scenario)
    |
    v
POST /runs  (multipart: IBT file + parameters)
    |
    v
RunJobManager.submit(run_id, request)
    |  ThreadPoolExecutor(max_workers=1)
    v
IOptimalWebService.execute_run()
    |
    +-- _run_single_session()  -> pipeline.produce.produce_result()
    +-- _run_comparison()      -> comparison.compare + score + synthesize
    +-- _run_track_solve()     -> solver.solve.run_solver()
    |
    v
RunRepository (SQLite WAL)
    |  - Save run state transitions
    |  - Save artifacts (sto, json, report)
    |  - Save result summaries
    v
GET /runs/{id}/status  (HTMX polling, 2s interval)
    |
    v (on completion, redirect)
GET /runs/{id}  (full result page with setup groups, changes, telemetry)
```

---

## 4. Design Decisions and Rationale

### 4.1 Physics-First, Not Pattern Matching

**Decision:** Every parameter value must be justified by a physical constraint. The solver follows a strict 6-step sequential workflow where each step's output feeds the next.

**Rationale:** Pattern matching (copying fast setups) fails because:
- The same setup produces different results with different drivers
- Setup interactions are non-linear (changing springs affects damper tuning)
- Copying provides no understanding of *why*, making iteration impossible
- Track conditions, fuel loads, and tyre state shift optimal values

**Implementation:** Each solver step has explicit constraints and objectives. For example, the heave solver finds the **softest spring that prevents bottoming** -- not a lookup table, but a binary search over the excursion equation `damped_excursion_mm(rate, m_eff, v_eff, f_aero, c_damper, k_tyre)`.

### 4.2 Strict 6-Step Ordering with Refinement

**Decision:** Always solve Rake -> Heave -> Corner Springs -> ARBs -> Geometry -> Dampers in order, with one refinement pass (provisional dampers fed back into heave/spring re-solve).

**Rationale:** Suspension parameters cascade physically:
- Ride heights determine aero loads, which determine spring requirements
- Springs determine roll stiffness, which determines ARB sizing
- ARBs determine LLTD, which determines geometry targets
- Everything above determines damper operating regime

Solving out of order produces internally inconsistent setups. The refinement pass handles the circular dependency between damper HS coefficients and heave spring sizing.

**Implementation:** `solve_chain.py:_run_sequential_solver()` executes: Steps 1-3 -> reconcile -> provisional Step 6 -> re-solve Steps 2-3 with damper coefficients -> Step 4-5 -> reconcile -> final Step 6.

### 4.3 Scenario Profiles Over Universal Weights

**Decision:** Four scenario profiles (`single_lap_safe`, `quali`, `sprint`, `race`) with distinct objective weight sets and prediction sanity limits.

**Rationale:** Calibration evidence showed that `single_lap_safe` with `lap_gain_only` (w_lap_gain=1.0, all penalties=0.0) outperforms the full multi-term objective by 3.4x in Spearman correlation against actual lap times. Penalty terms added noise rather than signal at the current observation count.

**Implementation:** `scenario_profiles.py` defines `ObjectiveWeightProfile` and `PredictionSanityProfile` per scenario. The pipeline resolves a profile from CLI flags and passes it through every scoring layer.

### 4.4 Legal Manifold Search (Not Free Optimization)

**Decision:** The search explores only legal garage states rather than continuous optimization. Accepted candidates must pass setup-registry legality, garage-output validation, and telemetry sanity checks.

**Rationale:** iRacing's garage has discrete parameter steps and hard display-value constraints. A "theoretically optimal" setup that iRacing rounds to different values or flags as illegal is useless. The search must work within the actual garage grid.

**Implementation:** `LegalSpace.from_car()` builds 23 search dimensions from `FIELD_REGISTRY` + car garage ranges. Perch offsets are computed as dependent variables (not searched), reducing the space by ~600,000x. Candidates undergo 3-tier validation: range checks (hard veto), ratio checks (soft penalty), and full garage-model correlation.

### 4.5 Driver-Adaptive Setup Production

**Decision:** Different drivers on the same car and track should produce different setups.

**Rationale:** A smooth, consistent driver benefits from stiffer damping and tighter tolerances. An aggressive, inconsistent driver needs more forgiving setups with wider operating windows. Trail braking depth affects ideal brake bias and diff coast ramps. Throttle progressiveness affects diff drive ramps and TC settings.

**Implementation:** `analyzer/driver_style.py` profiles 5 behavioral dimensions:
- Trail braking depth and classification (light/moderate/deep)
- Throttle progressiveness (R^2 of linear ramp)
- Steering smoothness (jerk p95)
- Lap-to-lap consistency (apex speed CV)
- Cornering aggression (g-utilization)

These feed into `solver/modifiers.py`: smooth drivers get zeta * 0.92; aggressive steering -> front HS +1; limit cornering -> HS +1 both axles. The brake solver adjusts bias for trail brake depth. The diff solver selects coast ramps from trail brake classification.

### 4.6 Per-Car Abstraction via Composed Sub-Models

**Decision:** Each car is defined as a composition of ~12 typed sub-model dataclasses rather than a flat parameter dictionary.

**Rationale:** The 5 GTP cars have fundamentally different architectures:
- BMW: Per-corner torsion bars (front) + coil springs (rear), Ohlins 11-click dampers
- Ferrari: Indexed heave springs (0-8/0-9), indexed torsion bars (0-18), rear torsion bars, 40-click dampers, heave dampers
- Acura: ORECA chassis with heave+roll damper architecture, torsion bars all 4 corners, synthesized corner shocks
- Porsche: Multimatic DSSV 20-click dampers, roll springs, Disconnected ARB toggle
- Cadillac: Open differential (no diff tuning)

A flat dict cannot express these differences. Typed sub-models ensure each solver step receives the correct physics for the active car.

**Implementation:** `CarModel` composes `HeaveSpringModel`, `CornerSpringModel`, `ARBModel`, `DamperModel`, etc. Each sub-model has car-specific methods (e.g. `HeaveSpringModel.front_rate_from_setting()` handles both physical N/mm and indexed controls). `FerrariIndexedControlModel` provides bidirectional lookup tables.

### 4.7 Cumulative Learning with Time Decay

**Decision:** Every IBT session is treated as an experiment. The system accumulates knowledge over time with exponential decay (0.95^days).

**Rationale:** Recent sessions are more relevant than old ones (track conditions evolve, driver improves, sim updates change physics). But all sessions contribute information. A 30-day-old session still carries ~21% weight vs 95% for yesterday's.

**Implementation:** `learner/empirical_models.py` applies `weight = 0.95^days` to all observations when computing corrections. Controlled experiments (1 setup change) get full confidence; multi-change sessions get proportionally less. Minimum data gates prevent premature corrections (5 sessions for physics, 3 for prediction feedback).

### 4.8 Garage Output Model for BMW/Sebring

**Decision:** Build a regression model that predicts what iRacing's garage will display for a given setup state, and validate solver outputs against it before writing .sto files.

**Rationale:** iRacing's internal physics computes display values (heave slider position, torsion bar deflection, static ride height, available travel) from setup inputs in ways that aren't publicly documented. If the solver's output would produce illegal display values, the setup gets rejected. The garage output model reverse-engineers these relationships from calibration data.

**Implementation:** `car_model/garage.py:GarageOutputModel` with ~60 regression coefficients predicts 18 display values. `output/garage_validator.py:validate_and_fix_garage_correlation()` runs 3 phases: range-clamp, garage-model correlation check with auto-correction (perch adjustments, spring stiffening, pushrod inversion), and RH reconciliation.

### 4.9 Signal Quality Framework

**Decision:** Every telemetry measurement is tagged with quality (trusted/proxy/broken/unknown) and confidence (0-1).

**Rationale:** Not all telemetry signals are equally reliable. Some channels may be missing for certain cars (Acura has no per-corner shock velocity channels -- they're synthesized from heave +/- roll). Fallback signals need different interpretation. The solver and report should know how much to trust each measurement.

**Implementation:** `analyzer/telemetry_truth.py:TelemetryBundle` groups signals into functional domains. `validation/observation_mapping.py:resolve_validation_signals()` applies calibrated scale factors when using fallback signals. The pipeline report shows signal quality summaries with fallback notes.

### 4.10 Offline-First Team Architecture

**Decision:** The desktop app works fully offline. Team sync is background-only with local SQLite queue and exponential backoff.

**Rationale:** Racing sessions happen in real-time. The tool must never block on network calls. If the server is down, the user shouldn't notice. Observations accumulate locally and sync when connectivity returns.

**Implementation:** `teamdb/sync_client.py:SyncClient` uses a local SQLite queue (`~/.ioptimal_app/sync_queue.db`). Push runs every 30s, pull every 300s. On failure, backoff doubles (up to 600s max). Up to 50 items per push batch. Ordering preserved by stopping on first failure.

---

## 5. System Constraints and Limitations

### 5.1 Calibration Coverage

| Constraint | Details |
|-----------|---------|
| **Only BMW/Sebring is calibrated** | 73 observations, garage output model, deflection regressions, heave calibration, damper zeta targets all from BMW/Sebring data. Other car/track combinations use physics defaults and may produce less accurate results. |
| **Weak objective correlation** | Non-vetoed Spearman correlation is -0.120522 (BMW/Sebring). This means the scoring function is only weakly predictive of actual lap time ordering. Weight auto-application is disabled; manual review required. |
| **Holdout stability insufficient** | K-fold holdout validation shows unstable Spearman across folds. The scoring function is not yet reliable enough for autonomous weight optimization. |
| **Ferrari indexed controls partially calibrated** | Front/rear heave lookup tables and rear torsion C constant are calibrated. Corner spring and LLTD outputs need more observations (currently ~25) to validate. |
| **Acura aero maps uncalibrated** | RH targets are unreliable because aero compression is unknown for Acura. Front heave damper bottoms at torsion OD >= 14.76mm. Torsion bar C constant borrowed from BMW -- needs ORECA-specific calibration from 5+ varied garage screenshots. |
| **Porsche essentially unsupported** | Only 2 observations. DSSV damper model is estimated. Multimatic chassis parameters are best-guess. |

### 5.2 Physics Model Limitations

| Constraint | Details |
|-----------|---------|
| **m_eff uses lap-wide statistics** | Effective mass correction uses lap-wide shock velocity and RH variance, not high-speed-only filtered data. Overestimates bump severity due to kerb contamination. High-speed filtered fields exist (`front_heave_vel_p95_hs_mps`, `front_rh_std_hs_mm`) but aren't used yet. |
| **LLTD is actually roll stiffness distribution proxy** | The `lltd_measured` field is computed from ride height deflection ratios, not true lateral load transfer. It correlates with LLTD but is not identical. Name kept for backward compatibility. |
| **Quarter-car damper model** | Damper solver uses quarter-car eigenvalue analysis, not full 7-DOF vehicle model. Cross-coupling between heave/pitch/roll modes is not captured. |
| **Aero compression at single reference speed** | AeroCompression stores values at 230 kph reference and V^2-scales. Real aero compression varies non-linearly near stall and at extreme ride heights. |
| **No tyre model** | Tyre load sensitivity is a static lookup, not a dynamic thermal-mechanical tyre model. Tyre degradation is approximated as 3%/10-laps grip loss. |
| **Speed-dependent damper effects partially captured** | The solver uses separate LS/HS zeta targets and track-specific p95/p99 velocities, but the multi-speed solver only evaluates 3 discrete speed regimes, not continuous speed-dependent optimization. |

### 5.3 Data and Concurrency Constraints

| Constraint | Details |
|-----------|---------|
| **No file locking on knowledge store** | JSON-based knowledge store uses simple read/write. Safe for single-user CLI but will corrupt if multiple processes ingest simultaneously. |
| **Single-worker job executor** | The webapp runs a `ThreadPoolExecutor(max_workers=1)`. Multiple concurrent run requests are queued but only one executes at a time. |
| **IBT file stability check** | Watcher waits 3 seconds of no file growth before processing. If iRacing writes slowly or the disk is under load, the watcher might process an incomplete file. Timeout is 5 minutes. |
| **Session ID deterministic** | Session IDs are derived from `{car}_{track}_{ibt_filename}`. Re-ingesting the same IBT file overwrites the previous observation (idempotent). |

### 5.4 Per-Car Architecture Differences

| Car | Unique Constraint |
|-----|------------------|
| **BMW** | Only car with full garage output model. Constrained optimizer (`BMWSebringOptimizer`) only works for BMW/Sebring. Heave perch search integrated into physics solve. |
| **Ferrari** | Indexed controls require bidirectional conversion (physical N/mm <-> garage index 0-18). Solver must deep-copy and convert step2/step3 outputs before writing .sto. 40-click dampers (vs BMW 11-click) create 4x larger damper search space. |
| **Acura (ORECA)** | No per-corner shock velocity channels in IBT. Heave+roll telemetry must be synthesized into corner shocks: `corner = heave +/- roll`. Roll dampers are separate from corner dampers. Rear uses torsion bars (not coil springs). |
| **Porsche** | DSSV dampers have different force characteristics than Ohlins. Roll springs instead of torsion bars on some axes. "Disconnected" ARB option is binary toggle, not a stiffness value. |
| **Cadillac** | Open differential -- no diff preload, ramp, or clutch plate tuning. Front pushrod-to-RH ratio is 1.28 (not pinned like BMW). |

### 5.5 Search Space Complexity

| Dimension | Range | Steps | Values |
|-----------|-------|-------|--------|
| Wing | 12-17 (BMW) | 1 | 6 |
| Front pushrod | -30 to 30mm | 0.5mm | 121 |
| Rear pushrod | -30 to 30mm | 0.5mm | 121 |
| Front heave | 0-900 N/mm | 10 | 91 |
| Rear third | 100-900 N/mm | 10 | 81 |
| Torsion bar OD | 14 discrete options | - | 14 |
| Rear spring | 50-200 N/mm | 5 | 31 |
| Front/rear ARB | 3 sizes x 5 blades | - | 15 each |
| Front/rear camber | -5.0 to 0.0 deg | 0.1 | 51 each |
| Front/rear toe | -3.0 to 3.0mm | 0.1 | 61 each |
| 10 damper clicks | 1-11 each | 1 | 11 each |
| Brake bias | 40-70% | 0.1 | 301 |
| Diff preload | 5-40 Nm | 5 | 8 |

**Full Cartesian product:** ~10^25 combinations. This is why the search uses hierarchical grid search (4 layers) and family-seeded Sobol sampling rather than exhaustive enumeration. Perch offsets are computed (not searched), reducing the effective space by ~600,000x.

### 5.6 Validation and Testing

| Aspect | Status |
|--------|--------|
| **Test suite** | 47+ test files in `tests/`, run via `pytest` in GitHub Actions CI (Python 3.12) |
| **Canonical validation** | `validation/run_validation.py` computes score correlation, parameter correlations, signal usage, claim audit |
| **Calibration pipeline** | `validation/objective_calibration.py` runs ablation, weight search, holdout k-fold -- all produce evidence for manual review only |
| **Support tiers enforced** | Reports and documentation must use explicit support tier labels. "Optimal" claims not allowed until Spearman < -0.3 |
| **Garage correlation validated** | BMW/Sebring only. Other cars pass range-clamp but skip garage-model validation |

### 5.7 Deployment Constraints

| Constraint | Details |
|-----------|---------|
| **Desktop app is Windows-focused** | Packaged as `dist/IOptimal/IOptimal.exe` via PyInstaller (177 MB). macOS/Linux paths exist in config but are not packaged. |
| **Server requires PostgreSQL for production** | Falls back to SQLite for development. CORS is fully permissive (`*`) -- not suitable for public internet without a gateway. |
| **No authentication rotation** | API keys are static SHA-256 hashed strings. No expiration, rotation, or OAuth. |
| **iRacing IBT format dependency** | The binary parser is reverse-engineered from the IBT format. iRacing sim updates could change the format without notice. |
| **Aero map data is static** | The 33 aero map spreadsheets are provided data, not dynamically computed. Sim physics updates would require new aero maps. |
