# IOptimal GTP Setup Solver — Usage Guide

A physics-first setup solver for iRacing GTP/Hypercar cars. This guide covers how to run the system as a driver, and how to understand and extend it as a developer.

---

## Part 1: Running the Solver (User Guide)

### Prerequisites

```bash
# Python 3.11+
pip install numpy scipy openpyxl pyyaml

# For the web interface, also install:
pip install fastapi jinja2 python-multipart uvicorn

# For development/testing:
pip install pytest httpx
```

### Supported Cars

| Car | CLI Name | Calibration Status | Notes |
|-----|----------|--------------------|-------|
| BMW M Hybrid V8 | `bmw` | **Calibrated** (99 observations at Sebring) | Best-supported path, constrained optimizer |
| Ferrari 499P | `ferrari` | Partial (12 observations) | Indexed springs, separate heave dampers |
| Cadillac V-Series.R | `cadillac` | Exploratory (4 observations) | Shares Dallara platform with BMW |
| Acura ARX-06 | `acura` | Exploratory (7 observations) | ORECA chassis, heave+roll damper architecture |
| Porsche 963 | `porsche` | Unsupported (2 observations) | Multimatic chassis, DSSV dampers |

### Supported Tracks (pre-built profiles)

| Track | Profile |
|-------|---------|
| Sebring International Raceway | `sebring` |
| Hockenheim Grand Prix | `hockenheim` |

Any track can be used if you provide an IBT file — the system builds a track profile automatically.

---

### Quick Start: Generate a Setup from a Telemetry File

The main command takes an IBT telemetry file and produces a complete setup:

```bash
python -m pipeline.produce \
  --car bmw \
  --ibt path/to/session.ibt \
  --wing 17 \
  --sto output_setup.sto
```

This runs the full pipeline:
1. Parses the IBT file and builds a track profile
2. Extracts 70+ telemetry channels from your fastest lap
3. Segments the lap into corners and classifies your driving style
4. Diagnoses handling problems (understeer, bottoming, damper issues, etc.)
5. Runs the 6-step physics solver (rake → heave → corner springs → ARBs → geometry → dampers)
6. Computes supporting parameters (brake bias, differential, TC, tyre pressures)
7. Validates the setup against iRacing garage constraints
8. Writes the `.sto` file you can load directly in iRacing

**Output:** A terminal report showing the full analysis, plus the `.sto` file.

### Command Reference

#### `pipeline.produce` — Full Pipeline (IBT to Setup)

This is the primary command. It reads your telemetry, analyzes it, and produces an optimized setup.

```bash
python -m pipeline.produce --car <car> --ibt <file.ibt> [options]
```

**Required arguments:**
| Argument | Description |
|----------|-------------|
| `--car` | Car name: `bmw`, `ferrari`, `cadillac`, `acura`, `porsche` |
| `--ibt` | Path to one or more IBT files. Multiple files triggers multi-session reasoning |

**Common options:**
| Option | Default | Description |
|--------|---------|-------------|
| `--wing <deg>` | auto-detect | Wing angle in degrees (e.g., `17`). Auto-detected from IBT if omitted |
| `--sto <path>` | none | Write iRacing `.sto` setup file |
| `--json <path>` | none | Write machine-readable JSON summary |
| `--lap <n>` | best lap | Analyze a specific lap number instead of the fastest |
| `--fuel <liters>` | from IBT | Override fuel load (liters) |
| `--scenario-profile <name>` | `single_lap_safe` | Solver scenario (see below) |
| `--balance <pct>` | car default | Target DF balance percentage (e.g., `50.14`) |

**Search options (advanced):**
| Option | Description |
|--------|-------------|
| `--free` | Run legal-manifold search after the physics solve. Explores the full garage space |
| `--explore-legal-space` | Same as `--free` |
| `--search-mode <mode>` | Search aggressiveness: `quick`, `standard`, `exhaustive`, `maximum` |
| `--search-budget <n>` | Number of candidates to evaluate (default: 1000) |

**Stint options:**
| Option | Description |
|--------|-------------|
| `--stint` | Enable stint-aware analysis (fuel burn, degradation, multi-lap) |
| `--stint-select <mode>` | Which stint: `longest`, `last`, `all` |
| `--stint-max-laps <n>` | Max laps to include (default: 40) |

**Learning options:**
| Option | Description |
|--------|-------------|
| `--no-learn` | Disable learned corrections from previous sessions |
| `--learn` | Ingest this session into the knowledge store after solving |
| `--auto-learn` | Automatically learn + apply |

**Other:**
| Option | Description |
|--------|-------------|
| `--verbose` | Print detailed solver output |
| `--report-only` | Print report without writing files |
| `--legacy-solver` | Force sequential solver even for BMW/Sebring |

#### Examples

```bash
# Basic: analyze and generate setup
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --sto race_setup.sto

# Qualifying setup with low fuel
python -m pipeline.produce --car bmw --ibt quali.ibt --wing 17 \
  --scenario-profile quali --fuel 12 --sto quali_setup.sto

# Race setup with stint analysis
python -m pipeline.produce --car bmw --ibt race.ibt --wing 17 \
  --scenario-profile race --stint --sto race_setup.sto

# Full legal-space search (thorough, slower)
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 \
  --free --search-mode exhaustive --sto optimized.sto

# Multi-session reasoning (provide 2+ IBTs)
python -m pipeline.produce --car bmw \
  --ibt session1.ibt session2.ibt session3.ibt \
  --wing 17 --sto best_of_3.sto

# Ferrari at any track
python -m pipeline.produce --car ferrari --ibt ferrari_session.ibt --sto ferrari.sto

# Acura with specific lap
python -m pipeline.produce --car acura --ibt acura_hockenheim.ibt --lap 12 --sto acura.sto
```

### Scenario Profiles

Scenarios control how aggressively the solver prioritizes lap time vs. stability.

| Profile | Use Case | Behavior |
|---------|----------|----------|
| `single_lap_safe` | Default. Hotlap practice, testing | Balanced. Penalizes instability moderately |
| `quali` | Qualifying | Aggressive. Accepts more platform risk for pace |
| `sprint` | Short stints (10-15 laps) | Moderate. Weighs thermal/tyre more than quali |
| `race` | Full race stints (30+ laps) | Conservative. Heavy penalties for platform risk, traction, consistency |

```bash
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 \
  --scenario-profile race --sto race_setup.sto
```

---

#### `solver.solve` — Standalone Solver (No IBT Required)

Runs the 6-step solver from a pre-built track profile. Useful when you don't have telemetry yet.

```bash
python -m solver.solve --car bmw --track sebring --wing 17
```

**Required:**
| Argument | Description |
|----------|-------------|
| `--car` | Car name |
| `--track` | Track name (must match a saved profile in `data/tracks/`) |
| `--wing` | Wing angle in degrees |

**Options:**
| Option | Default | Description |
|--------|---------|-------------|
| `--sto <path>` | none | Write `.sto` setup file |
| `--json` | off | Print JSON to stdout |
| `--save <path>` | none | Save JSON to file |
| `--fuel <liters>` | 89.0 | Fuel load |
| `--balance <pct>` | car default | Target DF balance |
| `--scenario-profile` | `single_lap_safe` | Scenario profile |
| `--free` | off | Run legal-manifold search |
| `--legal-search` | off | Same as `--free` |
| `--learn` | off | Apply learned corrections |

---

#### `analyzer` — Diagnose Without Solving

Analyzes telemetry and reports handling problems without generating a new setup.

```bash
python -m analyzer --car bmw --ibt session.ibt
```

Output: Terminal report with diagnosis (understeer, bottoming, damper issues, thermal, grip).

---

#### `comparison` — Compare Multiple Sessions

Analyzes 2+ IBT files side by side, ranks them, and optionally synthesizes an optimal setup.

```bash
python -m comparison --car bmw --ibt session1.ibt session2.ibt session3.ibt --wing 17
```

**Options:**
| Option | Description |
|--------|-------------|
| `--sto <path>` | Export synthesized optimal setup |
| `--json <path>` | Save comparison data as JSON |
| `--no-synthesis` | Compare and rank only, don't synthesize |

---

#### `learner.ingest` — Build Knowledge from Sessions

Ingests telemetry sessions into the knowledge store for empirical corrections.

```bash
# Ingest a single session (best lap)
python -m learner.ingest --car bmw --ibt session.ibt

# Ingest every valid lap as a separate observation
python -m learner.ingest --car bmw --ibt session.ibt --all-laps

# View knowledge status
python -m learner.ingest --status

# Query what's been learned
python -m learner.ingest --recall --car bmw --track sebring
```

After ingesting 3+ sessions, the solver automatically applies empirical corrections (m_eff, roll gradient, aero compression, LLTD baseline). After 5+ sessions, full empirical models are available.

---

#### `pipeline.preset_compare` — Generate Race/Sprint/Quali Presets

Generates setups for all three scenarios from the same IBT(s) and compares them.

```bash
python -m pipeline.preset_compare --car bmw --ibt session.ibt --wing 17
```

---

#### Web Interface

```bash
python -m webapp
```

Opens a local web app (default: `http://localhost:8000`) with three run modes:
1. **Single Session** — upload one IBT, get analysis + setup
2. **Comparison** — upload 2+ IBTs, compare and synthesize
3. **Track Solve** — solver without IBT (select car + track + wing)

---

### Understanding the Output

#### Terminal Report Sections

1. **Driver Profile** — Your driving style classification (e.g., "smooth-consistent", "aggressive-erratic") with trail braking depth, throttle progressiveness, steering jerk, consistency metrics.

2. **Handling Diagnosis** — Problems found in your telemetry, ranked by priority:
   - **Safety** (P0): Bottoming, vortex burst, heave travel exhaustion
   - **Platform** (P1): Ride height variance, excursion, braking pitch
   - **Balance** (P2): Understeer/oversteer, speed gradient, LLTD
   - **Damper** (P3): Settle time, yaw correlation
   - **Thermal** (P4): Tyre temps, carcass window, pressures
   - **Grip** (P5): Traction slip, braking lock, ABS activity

3. **Solver Solution** — The 6-step physics solution with values and reasoning for each step.

4. **Supporting Parameters** — Brake bias, differential, TC, tyre pressures.

5. **Setup Comparison** — Side-by-side current vs. recommended values.

6. **Confidence Assessment** — How confident the solver is in each recommendation.

#### .sto File

The `.sto` file is an iRacing setup file. To use it:
1. Copy the `.sto` file to your iRacing setups directory:
   - Windows: `C:\Users\<you>\Documents\iRacing\setups\<car>\`
2. In iRacing, go to the garage and load the setup from the "My Setups" tab.

---

### Tips for Best Results

1. **Drive clean laps.** The solver needs at least one complete timed lap without pit stops or incidents. Smooth, representative laps produce the best diagnosis.

2. **Fuel matters.** The solver accounts for fuel mass. If you're building a qualifying setup, run with qualifying fuel loads.

3. **Use `--stint` for race setups.** This analyzes fuel-burn effects and optimizes across the full stint.

4. **Ingest sessions for learning.** After each session, run `python -m learner.ingest --car bmw --ibt session.ibt` to accumulate knowledge. After 3+ sessions, the solver applies empirical corrections automatically.

5. **Multi-IBT is powerful.** Providing 2-3 IBTs from the same track (different setups/conditions) gives the 9-phase reasoning engine much more to work with.

6. **Non-BMW cars work but have known limitations.** Ferrari, Acura, Cadillac, and Porsche all run through the solver, but their calibration is less mature. BMW at Sebring is the reference path. See the calibration status table above.

---

## Part 2: Developer Guide

### Architecture Overview

```
IBT file
  │
  ▼
track_model/ibt_parser.py          Parse binary IBT format
  │
  ├──► track_model/build_profile.py   Build TrackProfile (speed bands, shock spectra, corners)
  │
  ▼
analyzer/extract.py                Extract 70+ telemetry channels → MeasuredState
  │
  ├──► analyzer/segment.py           Corner-by-corner lap segmentation
  ├──► analyzer/driver_style.py      Driver behavior profiling
  ├──► analyzer/adaptive_thresholds.py  Track/car/driver threshold scaling
  │
  ▼
analyzer/diagnose.py               Handling diagnosis → Problem list
  │
  ├──► analyzer/state_inference.py   High-level car state classification
  ├──► analyzer/causal_graph.py      Root cause analysis
  ├──► analyzer/recommend.py         Parameter change recommendations
  │
  ▼
solver/modifiers.py                Diagnosis → solver target adjustments
  │
  ▼
solver/solve_chain.py              6-step solver orchestration
  │
  ├──► solver/rake_solver.py         Step 1: Ride heights, rake, pushrod offsets
  ├──► solver/heave_solver.py        Step 2: Heave/third spring rates
  ├──► solver/corner_spring_solver.py Step 3: Torsion bar OD, rear spring
  ├──► solver/arb_solver.py          Step 4: ARBs, LLTD targeting
  ├──► solver/wheel_geometry_solver.py Step 5: Camber, toe
  ├──► solver/damper_solver.py       Step 6: All damper clicks
  │
  ├──► solver/full_setup_optimizer.py BMW/Sebring constrained optimizer (bypasses sequential)
  ├──► solver/bmw_rotation_search.py  BMW rotation control fine-tuning
  │
  ▼
solver/supporting_solver.py        Brake bias, diff, TC, pressures
  │
  ├──► solver/brake_solver.py        Physics-informed brake bias
  ├──► solver/diff_solver.py         Differential preload/ramps
  │
  ▼
solver/legality_engine.py          Validate against garage constraints
  │
  ▼
output/garage_validator.py         Pre-write garage correlation check
  │
  ▼
output/setup_writer.py             Generate .sto XML file
```

### Directory Map

```
/workspace
├── analyzer/          # Telemetry extraction, diagnosis, driver style
├── aero_model/        # Aero response surfaces (DF balance, L/D)
├── car_model/         # Per-car physics models and garage constraints
│   ├── cars.py        # All 5 car definitions (~2100 lines)
│   ├── setup_registry.py  # Canonical field registry, per-car mappings
│   ├── garage.py      # Garage output model (BMW/Sebring only)
│   ├── auto_discover.py   # NEW: Auto-discover from setup JSON
│   └── calibrate_deflections.py  # Calibration utility
├── comparison/        # Multi-session comparison and synthesis
├── data/
│   ├── aeromaps/      # Raw xlsx aero map spreadsheets
│   ├── aeromaps_parsed/  # Parsed JSON + numpy (5 cars)
│   ├── cars/          # Garage constraint data
│   ├── learnings/     # Knowledge store (observations, models)
│   ├── tracks/        # Pre-built track profiles
│   └── calibration_dataset.json  # BMW/Sebring calibration seeds
├── desktop/           # Desktop app (tray icon, watcher, sync)
├── docs/              # Documentation and audit reports
├── learner/           # Cumulative knowledge system
├── output/            # .sto writer, reports, garage validation
├── pipeline/          # End-to-end orchestration
│   ├── produce.py     # PRIMARY ENTRY POINT
│   ├── reason.py      # Multi-session 9-phase reasoning
│   ├── report.py      # Report generation
│   └── preset_compare.py  # Race/sprint/quali preset comparison
├── server/            # Team REST API (Cloud Run)
├── solver/            # Constraint satisfaction engine (~25 files)
├── teamdb/            # Team database and sync
├── tests/             # Test suite
├── track_model/       # Track profiles and IBT parser
├── validation/        # Aggregate validation (Spearman correlation)
├── validator/         # Per-run prediction feedback loop
├── vertical_dynamics.py  # Shared vertical dynamics helpers
└── webapp/            # FastAPI local web interface
```

### How the Solver Works (Developer Detail)

The solver follows a strict 6-step ordering. Each step depends on outputs from previous steps. This ordering reflects physical reality: you can't size dampers until you know the spring rates, and you can't size springs until you know the ride heights.

#### Step 1: Rake (Ride Heights)

**File:** `solver/rake_solver.py`

**Physics:** The aero balance of a ground-effect car depends on the ratio of front-to-rear ride heights (rake angle). The solver finds the front/rear ride height combination that achieves the target DF balance while maximizing L/D.

**Inputs:** Car aero map, target DF balance %, track speed profile, fuel load
**Outputs:** Front/rear dynamic RH (mm), static RH (mm), pushrod offsets (mm), DF balance %, L/D ratio

**Key method:** `RakeSolver.solve()` — pins front static RH at 30mm (GTP convention), then uses `brentq` root-finding on the aero surface to find the rear RH that gives the target balance.

#### Step 2: Heave / Third Springs

**File:** `solver/heave_solver.py`

**Physics:** The heave (front) and third (rear) springs control the platform's response to aero loads. The solver finds the softest springs that prevent bottoming (ride height touching the ground).

**Inputs:** Target ride heights from Step 1, track surface spectrum, car effective mass
**Outputs:** Front heave rate (N/mm), rear third rate (N/mm), perch offsets (mm)

**Key formula:** `excursion = v_p99 / (2π × f_n × ζ)` where `f_n = √(k/m_eff) / (2π)`. The excursion must fit within the available dynamic ride height.

#### Step 3: Corner Springs

**File:** `solver/corner_spring_solver.py`

**Physics:** Corner springs work in parallel with heave springs. The solver targets a natural frequency ratio between corner and heave springs for optimal platform response.

**Inputs:** Heave spring rates from Step 2, track surface severity, car mass
**Outputs:** Front torsion bar OD (mm), rear coil spring rate (N/mm)

**Critical convention:** Front torsion bar rate = `C × OD⁴` where C is car-specific (BMW: 0.0008036, Ferrari: 0.001282). The front `front_wheel_rate_nmm` already includes the motion ratio (MR baked into C).

#### Step 4: ARBs (Anti-Roll Bars)

**File:** `solver/arb_solver.py`

**Physics:** ARBs distribute lateral load transfer between front and rear axles. The LLTD (lateral load transfer distribution) determines steady-state balance: higher front LLTD = more understeer.

**Inputs:** Front/rear wheel rates from Steps 2-3, target LLTD, car weight distribution
**Outputs:** Front/rear ARB size + blade position, LLTD analysis

**Key formula:** `LLTD = K_roll_front / (K_roll_front + K_roll_rear)` where `K_roll = K_roll_springs + K_roll_arb`.

#### Step 5: Wheel Geometry

**File:** `solver/wheel_geometry_solver.py`

**Physics:** Camber angle optimizes the tyre contact patch under body roll. Toe angle balances turn-in response against straight-line drag and heat.

**Inputs:** Body roll prediction, tyre model, lateral g profile
**Outputs:** Front/rear camber (deg), front/rear toe (mm)

#### Step 6: Dampers

**File:** `solver/damper_solver.py`

**Physics:** Dampers control the rate of weight transfer. Low-speed damping governs transient handling (corner entry/exit). High-speed damping governs bump response and aero platform stability.

**Inputs:** Wheel rates from Steps 2-5, track surface spectrum, target damping ratios
**Outputs:** Per-corner click settings (LS comp, LS rbd, HS comp, HS rbd, HS slope)

**Key parameter:** ζ (zeta, damping ratio). The solver targets specific ζ values then converts to clicks using the car's force-per-click model.

### Adding a New Car

To add support for a new car:

1. **Define the car model** in `car_model/cars.py`:

```python
NEW_CAR = CarModel(
    name="New Car Name",
    canonical_name="newcar",     # CLI name
    mass_car_kg=1050.0,
    mass_driver_kg=75.0,
    weight_dist_front=0.47,
    default_df_balance_pct=50.0,
    tyre_load_sensitivity=0.22,
    # ... all sub-models: aero_compression, pushrod, heave_spring,
    # corner_spring, arb, geometry, damper, garage_ranges
)
```

Look at the existing BMW definition (line ~1050 in `cars.py`) as a template. The critical parameters to get right:

| Parameter | How to get it |
|-----------|--------------|
| `mass_car_kg`, `weight_dist_front` | From IBT corner weights |
| `front_torsion_c` | From setup JSON `fSideSpringRateNpm` (see below) |
| `rear_motion_ratio` | Back-solve from measured LLTD |
| `front_m_eff_kg` | From IBT heave shock velocity + ride height response |
| `aero_compression` | From static vs dynamic ride height in IBT |
| Damper ranges | From setup JSON damper rows (click ranges) |

2. **Add to the registry** in `cars.py`:
```python
_CARS = {
    "bmw": BMW_M_HYBRID_V8,
    # ...
    "newcar": NEW_CAR,
}
```

3. **Add setup registry specs** in `car_model/setup_registry.py`. This maps solver output field names to iRacing's internal XML IDs for .sto file generation.

4. **Add .sto parameter IDs** in `output/setup_writer.py`. Maps each setup parameter to the XML tag used in iRacing's binary setup format.

5. **Parse aero maps** — place xlsx files in `data/aeromaps/newcar/` and run:
```bash
python -m aero_model.parse_all
```

### Calibrating a Car from Setup JSON

The new `car_model/auto_discover.py` module can extract hidden physics values from iRacing's setup JSON format. This is the fastest calibration path:

```python
from car_model.auto_discover import discover_car_parameters
import json

with open("setup_export.json") as f:
    data = json.load(f)

params = discover_car_parameters(data)
print(params.summary())

# The hidden fSideSpringRateNpm gives you the actual spring rate
# at whatever torsion bar index is set in the setup:
print(f"Front spring rate: {params.front_corner_spring_rate_nmm} N/mm")
print(f"At torsion index: {params.front_torsion_bar_index}")

# From multiple setups with different torsion bar indices,
# you can fit the C constant:
from car_model.auto_discover import build_calibration_dataset
dataset = build_calibration_dataset([params1, params2, params3])
print(dataset["torsion_bar_calibration"])
```

**Key hidden fields to look for:**

| Field | What it gives you |
|-------|-------------------|
| `fSideSpringRateNpm` | Actual front corner spring wheel rate (N/m) |
| `rSideSpringRateNpm` | Actual rear corner spring wheel rate (N/m) |
| `lrPerchOffsetm` / `rrPerchOffsetm` | Actual rear corner perch offset (meters) |
| `hfLowSpeedCompDampSetting` etc. | Separate heave damper settings |
| `dCxBoP` / `dCzTBoP` | BoP aero coefficient deltas |

### Adding a New Track

Tracks are auto-built from IBT files. To pre-build a profile:

```python
from track_model.build_profile import build_profile
from track_model.ibt_parser import IBTFile
import json

ibt = IBTFile("path/to/session_at_new_track.ibt")
profile = build_profile(ibt)
with open("data/tracks/new_track_name.json", "w") as f:
    json.dump(profile.to_dict(), f, indent=2)
```

Or use the standalone solver, which auto-generates profiles from the track name:
```bash
python -m solver.solve --car bmw --track "new_track" --wing 17
```

### Key Concepts for Developers

#### Motion Ratio Convention

- **Front torsion bar:** `k_wheel = C × OD⁴`. The C constant already includes the motion ratio, so `front_motion_ratio = 1.0` for all cars. The solver's `front_wheel_rate_nmm` IS a wheel rate.

- **Rear coil spring:** The solver's `rear_spring_rate_nmm` is a RAW spring rate. Multiply by `car.corner_spring.rear_motion_ratio²` to get the wheel rate. BMW MR = 0.60, Ferrari MR = 0.612.

#### Aero Compression

Ride heights change at speed due to aerodynamic downforce:
```
dynamic_RH = static_RH - aero_compression × (speed / ref_speed)²
```
`AeroCompression` stores values at `ref_speed_kph = 230`. Use `comp.front_at_speed(speed)` for V²-scaled values.

#### Indexed vs. Physical Values (Ferrari)

Ferrari exposes springs and torsion bars as integer indices (0-8, 0-18), not physical values. The decode mappings are:
- Front heave: `rate = 50 + (idx-1) × 20` N/mm (APPROXIMATE)
- Front torsion: `OD = 20.0 + idx × (24.0-20.0)/18.0` mm then `k = C × OD⁴`
- Better: use `fSideSpringRateNpm` from the setup JSON for the true value

#### The Scoring Function

`solver/objective.py::ObjectiveFunction` evaluates setup candidates. Key formula:
```
score = w_lap_gain × lap_gain_ms
      - w_platform × platform_risk_ms
      - w_driver × driver_mismatch_ms
      - w_uncertainty × uncertainty_ms
      - w_envelope × envelope_penalty_ms
      - w_staleness × staleness_ms
      - w_empirical × empirical_penalty_ms
```

**Known issue:** The scoring function's correlation with actual lap time is weak (Spearman = -0.18). The damping component correlates in the wrong direction. See `docs/codebase_audit_report.md` Section 5 for details.

#### Learned Corrections

The knowledge system (`learner/`) accumulates empirical corrections from session data:
1. Ingest sessions via `learner/ingest.py`
2. After 3+ sessions: prediction-error corrections are available
3. After 5+ sessions: full empirical models (m_eff, roll gradient, LLTD, aero compression)
4. The solver queries these via `solver/learned_corrections.py`

Gate: corrections are disabled if `--no-learn` is set, or if fewer than 3 sessions exist.

### Modifying the Solver

#### Changing solver targets

Each solver step has targets that can be adjusted. The main lever is `solver/modifiers.py`, which translates diagnosis results into target offsets:

- `df_balance_offset_pct` — shifts the Step 1 DF balance target
- `heave_spring_floor_front/rear_nmm` — sets minimum spring rates for Step 2
- `lltd_offset` — shifts the Step 4 LLTD target
- `damper_click_offsets` — shifts Step 6 damper values
- `damping_ratio_scale` — scales the ζ targets

#### Adding a new diagnosis category

1. Add the check in `analyzer/diagnose.py` (follow the existing pattern)
2. Add handling in `solver/modifiers.py::compute_modifiers()`
3. Add threshold defaults in `analyzer/adaptive_thresholds.py`

#### Modifying scoring weights

Edit `solver/scenario_profiles.py` to change the `ObjectiveWeightProfile` for each scenario. Or create a new scenario profile.

### Running Tests

```bash
# All tests
python -m pytest tests/ -v

# Specific test file
python -m pytest tests/test_auto_discover.py -v

# Only fast tests (no IBT files needed)
python -m pytest tests/ -v -k "not ibt and not hockenheim"
```

### Known Limitations and Warnings

1. **BMW/Sebring is the only fully calibrated path.** Other car/track combinations produce setups but with lower confidence. Do not treat non-BMW outputs as equally validated.

2. **The scoring function needs recalibration.** See the audit report for details. The penalty terms (platform, envelope, driver, etc.) worsen correlation with lap time. Only the `lap_gain` term has meaningful signal.

3. **Damper ζ target discrepancy.** The solver (`damper_solver.py`) targets ζ = 0.88/0.30/0.45/0.14 while the objective function (`objective.py`) scores against 0.68/0.23/0.47/0.20. These need alignment.

4. **No garage output model for non-BMW cars.** The solver can produce outputs that pass range checks but might display impossible values in iRacing's garage. BMW has a regression model that catches this; other cars don't.

5. **Ferrari/Acura spring rates may be wrong.** The torsion bar C constants for these cars are either approximate or borrowed from BMW. Use the `auto_discover` module with setup JSON data to get the true values.

6. **Heave damper settings on Ferrari are invisible.** The Ferrari has separate heave damper settings (hidden in unmapped setup rows) that the solver currently doesn't model. See `car_model/auto_discover.py` for extraction.

### File Reference for Common Tasks

| Task | Primary File(s) |
|------|-----------------|
| Change solver physics | `solver/rake_solver.py` through `solver/damper_solver.py` |
| Modify car parameters | `car_model/cars.py` |
| Add/change garage mappings | `car_model/setup_registry.py` |
| Fix .sto output | `output/setup_writer.py` |
| Change diagnosis thresholds | `analyzer/adaptive_thresholds.py`, `analyzer/diagnose.py` |
| Modify scoring weights | `solver/scenario_profiles.py`, `solver/objective.py` |
| Add telemetry channels | `analyzer/extract.py` |
| Change report format | `pipeline/report.py`, `output/report.py` |
| Web interface | `webapp/app.py`, `webapp/services.py` |
| Calibrate from setup JSON | `car_model/auto_discover.py` |
| Validate scoring function | `validation/run_validation.py`, `validation/objective_calibration.py` |
