# GTP Setup Builder — Physics-Based Setup Calculator for iRacing GTP/Hypercar

## Project Goal
Build a physics-first setup solver for iRacing's GTP/Hypercar class that searches only legal garage states and explains why a setup should work. The current authoritative implementation target is BMW M Hybrid V8 at Sebring International Raceway; Ferrari, Cadillac, Porsche, and Acura paths remain partial or exploratory until more telemetry and garage-truth coverage exists.

## Current Codebase Status (2026-03-30)

- Workflow map: `IBT -> track/analyzer -> diagnosis/driver/style -> solve_chain/legality -> report/.sto -> webapp`
- Scenario engine: `solver/scenario_profiles.py` defines `single_lap_safe`, `quali`, `sprint`, and `race`, and those profiles now drive `pipeline/produce.py`, `pipeline/reason.py`, `solver/solve.py`, preset comparison, and the webapp.
- Legal-manifold search: `--free`, `--explore-legal-space`, and `--legal-search` now mean "start from the pinned physics solve and search the full legal setup manifold". Accepted candidates must pass setup-registry legality, garage-output validation, and telemetry sanity checks.
- Current BMW/Sebring evidence: `73` observations, `72` non-vetoed. Previous non-vetoed Spearman was `-0.120522` with hardcoded damper zeta targets and equal penalty weights. Damper targets updated to IBT-calibrated values (0.68/0.23/0.47/0.20), penalty scaling halved, and `single_lap_safe` weights set to calibration-searched values (lap_gain=1.25, envelope=0.20, rest=0). Pending re-validation to confirm improvement.
- Current support tiers from `validation/objective_validation.json`: BMW/Sebring = `calibrated`, Ferrari/Sebring = `partial`, Cadillac/Silverstone = `exploratory`, Porsche/Sebring = `unsupported`, Acura/Hockenheim = `exploratory` (ORECA chassis onboarded, heave+roll dampers, rear torsion bars, 7 observations ingested, RH calibration incomplete).
- Current source-of-truth reports: `docs/repo_audit.md`, `validation/objective_validation.md`, and `validation/calibration_report.md`.
- Current roadmap for improving the score model and onboarding the rest of the GTP field: `enhancementplan.md`.
- **Team tool deployed (2026-03-27):** Server live at `https://ioptimal-server-27191526338.us-central1.run.app`, team "SOELPEC Precision Racing" created (invite code `5a1c520b`), desktop app packaged at `dist/IOptimal/IOptimal.exe`. All 18 bugs fixed (12 original + 6 deployment). See `docs/team_tool_next_steps.md` for full deployment reference.
- **Acura ARX-06 onboarded (2026-03-30):** ORECA LMDh chassis with heave+roll damper architecture (not per-corner). Rear torsion bars, diff ramp angles, synthesized corner shocks from heave±roll telemetry. Pipeline functional end-to-end. Known limitations: front heave damper bottoming at OD≥14.76, rear RH misses aero targets (aero maps uncalibrated for Acura), roll dampers use baselines only. See `skill/per-car-quirks.md` Acura section for full calibration status.

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
- Support tiers are explicit and enforced in documentation: BMW/Sebring `calibrated`, Ferrari/Sebring `partial`, Cadillac/Silverstone `exploratory`, Porsche/Sebring `unsupported`, Acura/Hockenheim `exploratory`.

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

### Important Implementation Details

**Spring rate conventions (critical):**
- Front torsion bar: `CornerSpringSolution.front_wheel_rate_nmm` is already a wheel rate (MR baked into C*OD^4 formula, `front_motion_ratio=1.0` for all cars)
- Rear coil spring: `CornerSpringSolution.rear_spring_rate_nmm` is a RAW SPRING RATE. Must multiply by `car.corner_spring.rear_motion_ratio ** 2` to get wheel rate before passing to ARB/geometry/damper solvers.
- The ARB solver's `_corner_spring_roll_stiffness()` now expects wheel rates for both axles (no internal MR conversion).

**Aero compression is speed-dependent:**
- `AeroCompression` stores reference values at `ref_speed_kph` (230 kph)
- Use `comp.front_at_speed(speed)` / `comp.rear_at_speed(speed)` for V² scaling
- The rake solver uses `track.median_speed_kph` for compression at the operating point

**Static ride height models (RideHeightModel):**
- Front static RH is NOT sim-pinned — it varies with front_heave_nmm (r=0.50) and front_camber_deg (r=0.64)
- Front model: `front_static_rh = 30.1458 + 0.001614*heave_nmm + 0.074486*camber_deg` (LOO RMSE = 0.066mm)
- Rear model: 4-feature regression (pushrod, third_nmm, rear_spring, heave_perch), R²=0.97, LOO RMSE = 0.845mm
- Both models are reconciled after step2+step3 in solve.py and produce.py

**Learner model ID convention:**
- Model IDs use first word of track name only: `{car}_{track_first_word}_empirical` (e.g., `bmw_sebring_empirical`)
- Both `ingest.py` and `recall.py` use `track_name.lower().split()[0]` for consistency

**Known limitations:**
- BMW/Sebring is the only calibrated path. Other cars/tracks should not be described as equally validated.
- The objective is improving but still not authoritative: current BMW/Sebring non-vetoed Spearman is `-0.120522` and holdout stability is not yet strong enough for automatic runtime weight application.
- Several BMW validation signals still lean on fallbacks for some rows (`front_excursion_mm`, `braking_pitch_deg`, `rear_power_slip_p95`, hot pressures, lock proxies), so some supporting heuristics remain lower confidence.
- Ferrari rear torsion bar is calibrated (C=0.001282, MR=0.612, 4-point fit, max 3.2% error). Corner spring and LLTD outputs are functional but need more observations (currently 9) to validate against lap time.
- `m_eff` empirical correction uses lap-wide statistics (not filtered to high-speed straights), causing overestimation. Treat as rough indicator.
- `min_sessions=5` gate for non-prediction learned corrections. Prediction-based corrections
  (from solver feedback loop) need only 3 sessions since they measure specific prediction errors.
- Knowledge store has no file locking — safe for single-user CLI but not concurrent access.
- LLTD measurement from telemetry is actually a roll stiffness distribution proxy (not true LLTD).
  The `lltd_measured` field name is a backward-compatible alias for `roll_distribution_proxy`.
- High-speed m_eff filtering available via `front_heave_vel_p95_hs_mps` and `front_rh_std_hs_mm`
  (>200 kph only) but not yet used by the solver's m_eff correction — uses lap-wide stats.
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
