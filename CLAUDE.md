# GTP Setup Builder — Physics-Based Setup Calculator for iRacing GTP/Hypercar

## Project Goal
Build a physics engine that calculates optimal setup parameters from first principles for any car/track combination in iRacing's GTP class. Not a database of "what worked" — a constraint solver that reasons about WHY parameters should be specific values.

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
- `report.py` — Engineering report: driver profile, handling diagnosis, aero analysis, 6-step solution summary, supporting parameters, setup comparison (current vs produced), confidence assessment
- `__main__.py` — Entry point for `python -m pipeline`

#### 7. `output/` — Setup File Generator
- Generate iRacing .sto setup files directly (BMW-specific CarSetup_* XML IDs)
- Generate human-readable setup reports with reasoning for each parameter
- Generate comparison reports (current setup vs solver recommendation)
- `write_sto()` accepts optional supporting parameter overrides (brake bias, diff, TC, pressures) via kwargs

### Data Files
- `data/aeromaps/` — Raw xlsx files (provided)
- `data/aeromaps_parsed/` — Parsed JSON/numpy arrays
- `data/tracks/` — TrackProfile JSONs (built from IBT analysis)
- `data/cars/` — Car model definitions
- `data/telemetry/` — Reference IBT sessions for validation

### Validation Strategy
- Parse existing IBT sessions (BMW 7 sessions, Ferrari 3, Porsche 2)
- For each session: run solver with same inputs, compare output to actual setup
- Track where solver agrees/disagrees with human-tuned setup
- Use disagreements to calibrate model parameters

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

## Usage

### Standalone solver (pre-built track profile):
```bash
python -m solver.solve --car bmw --track sebring --wing 17 --sto output.sto
```

### Full pipeline (IBT → .sto, driver-adaptive):
```bash
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --sto output.sto
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --lap 25 --json output.json
```

### Analyzer (diagnose existing setup):
```bash
python -m analyzer --car bmw --ibt session.ibt
```

## Reference Files
- `skill/SKILL.md` — Engineering knowledge base (damper theory, ARB physics, etc.)
- `skill/per-car-quirks.md` — Car-specific verified findings
- `skill/ibt-parsing-guide.md` — IBT binary format parser
- `skill/telemetry-channels.md` — Channel reference
```