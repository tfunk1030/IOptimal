# IOptimal — Complete Guide
**Physics-based GTP/Hypercar setup solver for iRacing**

This guide covers everything from first-time setup to advanced calibration, and includes a full developer reference for extending the system.

---

## Table of Contents

### For Drivers and Setup Users
1. [What This Program Does](#what-this-does)
2. [Installation](#installation)
3. [Calibration Support by Car](#calibration-support)
4. [Your First Setup in 5 Minutes](#quickstart)
5. [Core Workflows](#workflows)
6. [All Commands Reference](#commands)
7. [Reading the Output](#reading-output)
8. [Feeding the Garage Schema JSON](#garage-schema)
9. [Common Problems and Fixes](#troubleshooting)

### For Future Coders and Contributors
10. [Architecture Overview](#architecture)
11. [How the Solver Actually Works](#solver-internals)
12. [How Physics Constants Are Derived](#physics-constants)
13. [Adding a New Car](#adding-car)
14. [Adding a New Track](#adding-track)
15. [Improving the Objective Function](#objective)
16. [Data Sources and Calibration Pipeline](#calibration-pipeline)
17. [Test Suite](#tests)
18. [Known Limitations and Active Fix Plan](#known-limits)
19. [Key Files Reference](#key-files)

---

## 1. What This Program Does {#what-this-does}

IOptimal reads your iRacing telemetry file (`.ibt`) and produces a physics-justified setup file (`.sto`) you can load directly in the garage. It does not guess or pattern-match from a template library — it works through a 6-step physics chain (rake → heave springs → corner springs → ARBs → geometry → dampers) and explains every number it writes.

**What it reads from your IBT:**
- Your entire garage setup (every parameter, exact values, iRacing-computed display values)
- Your driving style (trail braking depth, throttle progressiveness, smoothness, consistency)
- What the car actually did (ride heights, shock velocities, lateral g, understeer, body slip, tyre data)
- Track surface character (bump severity by sector, corner speed distribution, kerb locations)

**What it writes:**
- A complete `.sto` setup file ready to load in iRacing
- An engineering report explaining why each parameter was chosen
- A JSON of all solver decisions (useful for debugging or scripting)

**What it does not do:**
- It does not guarantee lap time improvement — it is a physics model, not a tested racing setup
- It does not search driver-specific preferences unless you drive it multiple sessions and the learner accumulates data
- It is not a black box — every output parameter has a traceable physics reason

---

## 2. Installation {#installation}

**Requirements:** Python 3.11+

```bash
# Core dependencies
pip install numpy scipy openpyxl pyyaml

# For the web UI
pip install fastapi uvicorn httpx

# For running tests
pip install -r requirements-dev.txt
```

**Verify the install:**

```bash
python3 -m solver.solve --car bmw --track sebring --wing 17 --report-only
```

If this prints a garage setup sheet, everything is working.

**File layout you care about:**

```
IOptimal/
├── data/
│   ├── aeromaps_parsed/    ← required, pre-built aero maps
│   ├── tracks/             ← track profiles built from your IBT files
│   ├── learnings/          ← persistent knowledge store (grows over time)
│   └── auto_calibration/   ← physics constants derived from IBT sessions
├── docs/
│   └── GUIDE.md            ← this file
├── pipeline/produce.py     ← main entry point
└── solver/solve.py         ← track-only entry point (no IBT)
```

Place your `.ibt` files anywhere accessible; you pass the path as an argument.

---

## 3. Calibration Support by Car {#calibration-support}

The solver runs for all cars, but confidence in the output depends on how much real telemetry has been used to calibrate the physics constants.

| Car | Track(s) | Tier | What "tier" means |
|-----|----------|------|-------------------|
| BMW M Hybrid V8 | Sebring | **Calibrated** | Physics constants measured from IBT. Output is a defensible engineering recommendation. |
| Ferrari 499P | Sebring | **Partial** | Torsion bar C calibrated (4-pt sweep). Heave m_eff estimated. Damper force/click estimated. Use outputs as a starting direction, not a final answer. |
| Cadillac V-Series.R | Silverstone | **Exploratory** | Some learner corrections applied. Only a few sessions ingested. Treat as approximate. |
| Porsche 963 | Any | **Unsupported** | All physics constants are estimates transferred from BMW. Will produce a plausible-looking setup but the values may be systematically wrong. |
| Acura ARX-06 | Hockenheim | **Exploratory** | ORECA heave+roll architecture wired. Rear RH targets unreliable. Front heave bottoms at high OD. Roll dampers use baselines. |

**Rule of thumb:** BMW/Sebring outputs are trustworthy enough to load and drive. All other cars/tracks produce outputs that are directionally correct but may need one tuning session to dial in the actual spring stiffnesses.

**How to improve your car's tier:** Feed more IBT sessions. Every session teaches the system the actual physics constants (m_eff, torsion C, aero compression). See [Feeding the Garage Schema JSON](#garage-schema) for an even faster calibration path.

---

## 4. Your First Setup in 5 Minutes {#quickstart}

### Step 1: Drive any session in iRacing

Use the default setup or your current setup. At least one clean lap at reasonable pace. Save the IBT.

### Step 2: Produce a setup

```bash
python3 -m pipeline.produce \
  --car bmw \
  --ibt "path/to/your_session.ibt" \
  --wing 17 \
  --scenario-profile single_lap_safe \
  --sto my_new_setup.sto
```

Replace `bmw` with your car and `17` with your target wing angle.

### Step 3: Load the setup in iRacing

Copy `my_new_setup.sto` to:
```
%USERPROFILE%\Documents\iRacing\setups\<CarName>\<TrackName>\
```

Or use "Import Setup" in the iRacing garage screen.

### Step 4: Read the report

The terminal prints an engineering report. Scroll up to see:
- **Driver profile**: how the solver read your driving style
- **Handling diagnosis**: what problems it detected in your current setup
- **Solver decisions**: why each parameter was chosen
- **Setup comparison**: your current setup vs the produced setup (parameter by parameter)

### Step 5: Drive the new setup and iterate

The system improves with each session. Run `--auto-learn` (it is enabled by default) to accumulate knowledge automatically.

---

## 5. Core Workflows {#workflows}

### Workflow A: Race weekend practice → qualifying setup

```bash
# Session 1: P1 IBT (any setup)
python3 -m pipeline.produce --car ferrari --ibt p1_session.ibt \
  --wing 16 --scenario-profile single_lap_safe --sto p1_setup.sto

# Drive the P1 setup, save IBT

# Session 2: P2 IBT — refine toward qualifying
python3 -m pipeline.produce --car ferrari --ibt p2_session.ibt \
  --wing 16 --scenario-profile quali --sto quali_setup.sto

# Drive quali setup, save IBT — load into qualifier
```

The `single_lap_safe` profile is conservative (prioritizes platform stability). The `quali` profile accepts slightly more aggressive heave travel for raw pace.

### Workflow B: Race stint setup

```bash
python3 -m pipeline.produce --car bmw --ibt practice_session.ibt \
  --wing 17 --scenario-profile race --stint \
  --stint-select longest --sto race_setup.sto
```

`--stint` triggers full stint analysis: the solver models how fuel burn shifts the car's balance over 30+ laps and recommends a setup that stays balanced throughout. `--stint-select longest` uses the longest green-run segment from your practice session.

### Workflow C: Compare multiple sessions, synthesize best setup

```bash
python3 -m comparison --car bmw \
  --ibt session1.ibt session2.ibt session3.ibt \
  --wing 17 --sto synthesized_best.sto
```

Each session is scored across grip, balance, aero efficiency, high/low speed corners, damper platform stability, and thermal management. The synthesized setup picks the best-performing configuration from each area.

### Workflow D: Search beyond the physics baseline

The base solver pins front ride height to the iRacing minimum (the safest aero starting point). If you want the solver to search the full legal parameter space from that baseline:

```bash
# Quick search (~3 seconds)
python3 -m pipeline.produce --car bmw --ibt session.ibt \
  --wing 17 --search-mode quick --sto search_result.sto

# Standard search (~4 minutes, recommended)
python3 -m pipeline.produce --car bmw --ibt session.ibt \
  --wing 17 --search-mode standard --sto search_result.sto

# Show top 5 candidates instead of just rank 1
python3 -m pipeline.produce --car bmw --ibt session.ibt \
  --wing 17 --search-mode standard --top-n 5
```

### Workflow E: Building the knowledge base (recommended for all users)

Run this after every session. It takes about 10 seconds and makes every future session more accurate.

```bash
python3 -m learner.ingest --car bmw --ibt your_session.ibt
```

The pipeline runs this automatically when you use `--auto-learn` (enabled by default since March 2026). Check what's accumulated:

```bash
python3 -m learner.ingest --status
python3 -m learner.ingest --car bmw --track sebring --recall
```

### Workflow F: Physics calibration from garage schema JSON

If your webapp produces a structured garage JSON (the format shown in the user query), feed it directly:

```python
from car_model.garage_schema_ingester import (
    parse_garage_schema_json,
    derive_physics_from_schema,
    save_schema_calibration,
    apply_schema_to_car_model,
)
from car_model.cars import get_car
import json

with open("my_ferrari_setup.json") as f:
    schema = json.load(f)

car = get_car("ferrari")
physics = derive_physics_from_schema(schema, car_name="ferrari")
print(physics.summary())

# Apply calibrated constants to car model before solving
applied = apply_schema_to_car_model(car, physics)
print(f"Applied: {applied}")

# Save for accumulation
save_schema_calibration(parse_garage_schema_json(schema), physics, "ferrari")
```

Every unique torsion OD index + spring rate you feed in adds another calibration point to the index→physical-rate curve. After 5-6 different setups, the Ferrari heave spring mapping will be fully calibrated.

---

## 6. All Commands Reference {#commands}

### `pipeline.produce` — Primary workflow (IBT → setup)

```bash
python3 -m pipeline.produce [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--car NAME` | required | `bmw`, `ferrari`, `cadillac`, `porsche`, `acura` |
| `--ibt FILE [FILE...]` | required | IBT file(s). Two or more triggers multi-session reasoning. |
| `--wing DEG` | auto from IBT | Wing angle in degrees |
| `--lap N` | best lap | Analyze specific lap number |
| `--balance PCT` | car default | Target DF balance % |
| `--tolerance PCT` | 0.1 | DF balance tolerance |
| `--fuel L` | auto from IBT | Fuel load in liters |
| `--scenario-profile NAME` | `single_lap_safe` | `single_lap_safe` / `quali` / `sprint` / `race` |
| `--sto PATH` | none | Write iRacing `.sto` setup file |
| `--json PATH` | none | Write full solver results as JSON |
| `--setup-json PATH` | none | Write setup schema JSON and exit |
| `--search-mode MODE` | none | Grid search: `quick`, `standard`, `exhaustive`, `maximum` |
| `--top-n N` | 1 | Show N ranked candidates from search |
| `--free` | off | Random legal-manifold search from physics seed |
| `--explore-legal-space` | off | Same as `--free` (alias) |
| `--stint` | off | Enable full-stint analysis |
| `--stint-select MODE` | `longest` | Which stint: `longest`, `last`, `all` |
| `--stint-max-laps N` | 40 | Maximum laps to score directly |
| `--no-learn` | off | Disable auto-calibration and knowledge store ingestion |
| `--legacy-solver` | off | Force sequential solver (bypass BMW optimizer) |
| `--report-only` | off | Print compact report only, skip per-step details |
| `--verbose` | off | Full reasoning dump (multi-IBT mode) |
| `--min-lap-time S` | auto | Minimum valid lap time in seconds |
| `--outlier-pct F` | 0.115 | Max fractional deviation to accept a lap |

### `solver.solve` — Track-only (no IBT needed)

```bash
python3 -m solver.solve --car bmw --track sebring --wing 17 [options]
```

Uses a pre-built track profile from `data/tracks/`. All the same flags as `pipeline.produce` except `--ibt`.

### `analyzer` — Diagnose handling from IBT

```bash
python3 -m analyzer --car bmw --ibt session.ibt [--lap N] [--save report.json]
```

Prints: ride height statistics, shock velocity histograms, handling diagnosis (understeer/oversteer/bottoming/platform instability), prioritized recommendations.

### `learner.ingest` — Accumulate knowledge

```bash
python3 -m learner.ingest --car bmw --ibt session.ibt [--wing 17] [--lap N]
python3 -m learner.ingest --status
python3 -m learner.ingest --car bmw --track sebring --recall
```

### `comparison` — Multi-session ranking and synthesis

```bash
python3 -m comparison --car bmw --ibt s1.ibt s2.ibt s3.ibt [--wing 17] [--sto best.sto]
```

### `validator` — Check solver predictions vs telemetry

```bash
python3 -m validator --car bmw --track sebring --wing 17 \
  --ibt validation_run.ibt --setup solver_output.json
```

### Track profile builder

```bash
python3 track_model/build_profile.py session.ibt [-o data/tracks/sebring.json]
```

Run this once per track before using `solver.solve`. The pipeline runs it automatically.

---

## 7. Reading the Output {#reading-output}

### Engineering report sections

```
════════════════ DRIVER PROFILE ════════════════
Style: smooth-consistent | Trail brake: moderate (p95=0.32)
Throttle: progressive (R²=0.91) | Smoothness: 0.73

════════════════ HANDLING DIAGNOSIS ════════════════
Assessment: competitive
  [SIGNIFICANT] Platform variance: front_rh_std=3.2mm > 2.8mm threshold
    → Platform oscillating: heave spring too soft for this track surface
  [MINOR] High-speed understeer: 1.4° at peak lat-g corners
    → RARB softening or camber adjustment recommended

════════════════ STEP 1: RAKE ═══════════════════
Target DF balance: 50.14%
  Front static RH: 30.0mm (pinned — iRacing GTP floor)
  Rear static RH: 42.3mm → pushrod -29.0mm
  Dynamic front: 14.8mm | Dynamic rear: 36.8mm at 230kph
  L/D: 3.412 at operating point

════════════════ STEP 2: HEAVE ══════════════════
Bottoming constraint: binding at front (14.8mm dynamic RH, p99 excursion 14.1mm)
  Front heave: 50 N/mm (binding: bottoming at 14.8mm with v_p99=0.118 m/s)
  Rear third: 450 N/mm (constraint: variance σ=2.8mm target)
```

**Assessment levels:**
- `fast` — No significant problems detected
- `competitive` — Minor issues, manageable
- `compromised` — Multiple problems affecting lap time
- `dangerous` — Safety-critical issues (bottoming, vortex burst risk)

### Sensitivity table

```
LAP TIME SENSITIVITY
Parameter        Value      ±ms/unit   Direction
rear_rh_mm       40.0mm    -45ms/mm   lower = faster (more rear DF)
rear_arb_blade    3.0      +38ms/step  higher = faster (more LLTD)
front_rh_mm      15.0mm    -22ms/mm   lower is risky (vortex threshold at 8mm)
```

Negative ms/unit = decreasing the value gains time. Positive = increasing gains time.

### Decision trace

The report includes a "decision trace" for every parameter: what the current value is, what the solver recommends, why, and how confident it is. Look for:
- `[PHYSICS]` — derived from the 6-step constraint chain
- `[TELEMETRY]` — modified by your actual measured data
- `[LEARNER]` — empirical correction from past sessions
- `[ESTIMATE]` — car constant not yet calibrated; treat this parameter as a starting point

### Legal validation status

```
Legal validation: PASS (full tier)
  Heave spring defl: 11.2mm ✓ [legal: 0.6–25.0mm]
  Heave slider: 38.4mm ✓ [legal: 25.0–45.0mm]
  Front shock defl: 18.1mm ✓ [legal: max 19.9mm]
```

If any constraint fails, the setup will show in the garage with a red warning and may not be raceable. The solver tries to avoid this, but always verify in the garage before a session.

---

## 8. Feeding the Garage Schema JSON {#garage-schema}

The webapp produces a structured JSON describing every garage parameter. This is the fastest calibration path for non-BMW cars.

**What the JSON unlocks:**
- `fSideSpringRateNpm` → exact physical corner spring rate in N/m (divide by 1000 for N/mm) — no estimation needed
- `rSideSpringRateNpm` → rear spring rate
- `lrPerchOffsetm` → exact rear perch offset in SI units
- `hfLowSpeedCompDampSetting` → internal heave damper setting (separate from corner dampers — important for Ferrari)
- `brakeMasterCylDiaFm` → exact master cylinder diameter

```python
from car_model.garage_schema_ingester import (
    parse_garage_schema_json, derive_physics_from_schema,
    save_schema_calibration, build_garage_ranges_from_schema,
)
import json

# Load the JSON from the webapp
with open("ferrari_setup_schema.json") as f:
    data = json.load(f)

# Parse all parameters
params = parse_garage_schema_json(data)
print(f"Front heave index: {params.front_heave_index}")
print(f"Front spring rate (exact): {params.front_spring_rate_nmm / 1000:.2f} N/mm")
print(f"Heave damper LS (internal): {params.hf_ls_comp_setting}")
print(f"Corner damper LS (per-corner): {params.lf_ls_comp}")

# Extract legal ranges
ranges = build_garage_ranges_from_schema(data)
print(f"Brake bias legal range: {ranges.brake_bias_pct}")

# Derive physics constants
physics = derive_physics_from_schema(data, car_name="ferrari")
print(physics.summary())

# Save for accumulation (builds index→rate curve over multiple setups)
save_schema_calibration(params, physics, "ferrari")
```

**Accumulation strategy:** Feed schemas from setups with different torsion OD indices and heave spring indices. After 5-6 different setups covering the OD range, the full index→physical-rate curve is calibrated and the solver's spring rate computations become exact rather than estimated.

**Critical Ferrari discovery from the JSON format:**
Ferrari has two separate damper systems that look identical in the label but operate on different scales:
- `hfLowSpeedCompDampSetting` (internal, 0-10) — controls the **heave spring damper**
- `LS comp damping / Left Front Damper` (garage-visible, 0-40) — controls the **corner shock damper**

The solver needs both. The internal heave damper (0-10) is what primarily controls aero platform stability. The corner dampers (0-40) handle transient load transfer.

---

## 9. Common Problems and Fixes {#troubleshooting}

### "No valid laps found in IBT file"
The pipeline needs at least one complete timed lap. Out-lap, in-lap, and pit-exiting laps don't count. Drive at least one timed lap from the pit exit flag. If your session was testing-only (no timing), use `solver.solve` with a track name instead.

### Setup produces strange ride heights for Ferrari
Ferrari's front heave spring uses an integer index (0–8), not N/mm. The solver works in physical N/mm internally and encodes back to index for output. If the index mapping is wrong for your current setup, the output will be off by one or two index positions. Fix: feed a garage schema JSON with `fSideSpringRateNpm` so the solver learns the exact mapping at your setting.

### "Heave slider defl too high: 46.2mm > 45.0mm legal max"
The solver chose a perch offset that pushed the slider too far. Add `--legacy-solver` to disable the BMW constrained optimizer and fall back to the sequential solver, which has a slightly different perch targeting approach. Or adjust `--fuel` — heave slider position is fuel-sensitive (higher fuel = more compression).

### Outputs seem too stiff compared to what you've been running
The solver targets a bottoming constraint: it picks the softest spring that still prevents bottoming at p99 shock velocity. If your IBT showed unusually smooth conditions, the solver will pick soft springs. If the track was actually bumpier in other sectors, the produced setup may bottom there. Use `--search-mode standard` to explore stiffer options and see their platform risk scores.

### Knowledge store is empty or not applying
After ingesting with `--auto-learn` (or `learner.ingest`), corrections apply only once 3+ sessions exist for a car/track combination. Check: `python3 -m learner.ingest --status`.

### Ferrari camber values look wrong (positive values)
The per-corner camber in the garage schema JSON reflects the actual asymmetric state including cross-weight. LF shows +0.6° and RF shows -0.6° in the example above — this is the cross-weighted state, not the absolute camber setting. The solver averages left and right for symmetric parameter output. The camber setting you actually change in the garage is the symmetric pair.

---

## 10. Architecture Overview {#architecture}

```
IBT file
  │
  ├─ session_info YAML ────────────────────────────────────────────────────┐
  │   Every garage parameter (settable + computed display values)           │
  │   → analyzer/setup_reader.py::CurrentSetup                             │
  │   → car_model/garage_schema_ingester.py (if webapp JSON provided)     │
  │                                                                         │
  └─ binary telemetry channels ────────────────────────────────────────────┤
      AeroCalcFrontRhAtSpeed (aero-map frame) → aerocalc_front_rh          │
      HFshockVel / HFshockDefl → m_eff calibration                         │
      LatAccel / YawRate → handling diagnosis                               │
      LFshockVel / LFrideHeight → track surface profile                    │
      → analyzer/extract.py::MeasuredState                                  │
                                                                            │
                 ┌──────────────────────────────────────────────────────────┘
                 │
    car_model/auto_calibrate.py  ← derives physics constants from IBT+setup
      - aero_compression (from AeroCalcFrontRhAtSpeed)
      - m_eff (from HFshockVel + HFshockDefl + spring rate)
      - torsion_c (from CornerWeight + TorsionBarDefl + OD)
      - weight_dist_front (from corner weights)
      Saves to data/auto_calibration/
                 │
                 ▼
    car_model/cars.py::CarModel ← car physics model, updated with calibrated values

    analyzer/diagnose.py ← 6-priority handling problem classification
    analyzer/driver_style.py ← trail brake, throttle progression, smoothness
    solver/modifiers.py ← translate diagnosis + driver → solver adjustments

                 ▼
    solver/solve_chain.py::run_base_solve()
      ┌─ solver/full_setup_optimizer.py (BMW/Sebring only)
      │    Constrained SciPy optimizer over calibration dataset
      └─ solver/solve_chain.py::_run_sequential_solver() (all other cars)
           Step 1: RakeSolver       → ride heights, pushrods
           Step 2: HeaveSolver      → heave/third spring rates + perch
           Step 3: CornerSpringSolver → torsion OD / rear spring
           Step 4: ARBSolver        → LLTD targeting
           Step 5: WheelGeometrySolver → camber + toe
           Step 6: DamperSolver     → all 10 damper axes (LS/HS comp/rbd + slope)
           Supporting: SupportingSolver → brake bias, diff, TC, tyre pressures

                 ▼
    solver/legality_engine.py ← validate every output against garage legal limits
    solver/candidate_search.py ← generate conservative / aggressive / balanced variants
    solver/objective.py ← score candidates (platform risk, lap gain, driver match)
    solver/scenario_profiles.py ← weights per scenario (quali / race / etc.)

                 ▼
    output/setup_writer.py ← write .sto XML (car-specific CarSetup_* IDs)
    output/report.py ← terminal engineering report
    learner/ingest.py ← store observation for future sessions
```

---

## 11. How the Solver Actually Works {#solver-internals}

### Step 1: Rake / Ride Heights (`solver/rake_solver.py`)

**Goal:** Find pushrods and rear ride height that hit the target DF balance at the track's operating speed.

**Constraint:** Front static RH ≥ 30mm (iRacing GTP floor). Not negotiable.

**Physics:** The aero map (`data/aeromaps_parsed/`) gives DF_balance(front_RH, rear_RH) at each wing angle. The solver searches rear RH (via rear pushrod) to hit the target while keeping front at the floor.

**Key input from telemetry:** `AeroCalcFrontRhAtSpeed` — the actual dynamic RH iRacing measures at speed. The solver uses this to calibrate the static→dynamic compression model. If this channel exists in the IBT, it replaces the estimated compression constant in `cars.py`.

### Step 2: Heave / Third Springs (`solver/heave_solver.py`)

**Goal:** Find the softest heave spring that prevents bottoming at p99 shock velocity.

**Physics:** `excursion_p99 = v_p99 * sqrt(m_eff / k)`. The solver solves for k given v_p99 from the track profile and m_eff from the car model (which auto_calibrate can refine).

**Two constraints, solver picks the binding one:**
- Bottoming: `excursion < dynamic_RH` — usually binding at the front
- Variance: `σ = excursion / 2.33 < σ_target` — usually binding at the rear

**Refinement pass:** A provisional damper solve runs first, then heave re-solves accounting for the damper's energy absorption. This is why the solver runs Step 6 twice (once provisional, once final).

### Step 3: Corner Springs (`solver/corner_spring_solver.py`)

**For BMW/Cadillac/Porsche:** Torsion bar OD and rear coil spring rate. `k = C * OD^4` for torsion bars. C is calibrated per car.

**For Ferrari:** Front and rear torsion bar indices (0–18). The index→physical-rate mapping is calibrated from garage screenshots (9 data points for Ferrari as of March 2026). The `garage_schema_ingester.py` allows any new setup to add another calibration point instantly.

**For Acura:** Front torsion bars (same hardware as BMW) + rear torsion bars (not per-corner coils). The C constant is borrowed from BMW until enough Acura data accumulates.

### Step 4: ARBs (`solver/arb_solver.py`)

**Goal:** Hit LLTD target = front weight fraction + 5% (OptimumG formula), then offer a live RARB range.

**Physics:** `LLTD = K_front / (K_front + K_rear)` where K includes both corner spring roll stiffness and ARB roll stiffness. Front torsion OD has an empirical coupling effect on ARB effectiveness (γ = 0.25, calibrated from BMW IBT, may be wrong for other cars).

### Step 5: Wheel Geometry (`solver/wheel_geometry_solver.py`)

**Goal:** Camber that compensates for body roll at peak lateral g. Toe that balances turn-in vs drag.

**Physics:** At peak lat-g, the body rolls by `roll_angle = lateral_g / roll_gradient`. Optimal camber keeps the outside tyre flat at the roll angle. The roll gradient is calibrated from measured roll vs lat-g in telemetry.

### Step 6: Dampers (`solver/damper_solver.py`)

**Goal:** Clicks that produce target damping ratios ζ at p95 shock velocity.

**Physics:** `ζ = c / (2 * sqrt(k * m))`. The solver back-calculates clicks from the target ζ and the `force_per_click` constant. **This constant is the biggest source of error for non-BMW cars** — it is estimated for Ferrari, Acura, Cadillac, and Porsche.

**Calibration method:** IBT shock velocity at a known click setting, combined with the natural frequency from zero-crossing rate, lets you back-solve `force_per_click`. Requires systematic damper click sweeps with IBT capture.

### Where "best" is chosen

There are three independent scoring systems. This is an architectural issue documented in the audit:

1. **`solver/objective.py::ObjectiveFunction`** — used by `--search-mode` grid search. Scores candidates in milliseconds using platform risk, lap gain estimate, driver mismatch, telemetry uncertainty, envelope penalty. BMW/Sebring non-vetoed Spearman = -0.18 with lap time (weak, ~near-random). Not reliable for ranking yet.

2. **`solver/candidate_ranker.py::score_from_prediction`** — used by candidate family generation. 0–1 scale across safety, performance, stability, confidence, disruption cost.

3. **BMW/Sebring optimizer** — internal seed-based SciPy minimizer. Scores candidates by physics constraint violation penalty. Only runs for BMW + Sebring combination.

When `--search-mode` is active, its result overwrites the base solve. When no search is active, the base solve stands (optionally modified by the best candidate family).

---

## 12. How Physics Constants Are Derived {#physics-constants}

### From a single IBT (auto-calibration)

`car_model/auto_calibrate.py::calibrate_from_ibt()` runs on every IBT before the solver. It extracts:

| Constant | Source in IBT | Method |
|----------|--------------|--------|
| `aero_compression_front_mm` | `AeroCalcFrontRhAtSpeed` channel (at speed vs pit speed) | Direct measurement in aero-map frame |
| `aero_compression_rear_mm` | `AeroCalcRearRhAtSpeed` channel | Direct measurement |
| `weight_dist_front` | `CornerWeight` values in session YAML | `(LF+RF) / total` |
| `front_torsion_c` | `CornerWeight` + `TorsionBarDefl` + OD from YAML | `C = k / OD^4 = (weight/defl) / OD^4` |
| `m_eff_front_kg` | `HFshockDefl` p99 + `HFshockVel` p99 + spring rate | `m_eff = k * (excursion/v_p99)^2` |
| `m_eff_rear_kg` | `HRshockDefl` + `HRshockVel` + spring rate | Same formula |
| `df_balance_pct` | `AeroCalcDownforceBalance` channel | Direct read |

Results save to `data/auto_calibration/{car}_{track}_{setup_hash}.json` and are averaged across sessions for stability.

### From garage schema JSON (instant, no IBT needed)

`car_model/garage_schema_ingester.py::derive_physics_from_schema()` extracts:

| Constant | Source in JSON | Notes |
|----------|---------------|-------|
| `front_corner_spring_rate_nmm` | `fSideSpringRateNpm / 1000` | Exact — iRacing's own simulation value |
| `rear_corner_spring_rate_nmm` | `rSideSpringRateNpm / 1000` | Exact |
| `front_torsion_c` (per index) | spring rate + OD index → `C = k / OD^4` | One calibration point per schema |
| Heave index → rate mapping | spring rate at known heave index | Builds index→rate curve over multiple setups |
| Legal ranges | `range_metric` for every row | Exact iRacing enforcement limits |
| Per-corner camber | `Camber` per section | Shows cross-weight / alignment asymmetry |
| Separate damper systems | `hfLowSpeedCompDampSetting` (internal) vs corner damper (mapped) | Distinguishes heave damper from corner damper |

### From manual calibration sweeps

For damper force per click (still estimated for non-BMW cars):

1. Drive two sessions: one with LS comp at minimum, one at maximum, all else identical
2. Extract `HFshockVel` p50 (LS regime, 10-30mm/s) from each
3. The natural frequency from `HFshockVel` zero-crossings + spring rate gives m_eff
4. `force_per_click = (target_zeta * 2 * sqrt(k * m)) / (clicks * p50_vel)`

This requires controlled experiments — change only damper clicks between sessions.

---

## 13. Adding a New Car {#adding-car}

### Step 1: Define the car model in `car_model/cars.py`

Copy the closest existing car (Cadillac for Dallara LMDh chassis, Ferrari for LMH chassis, Acura for ORECA). Change:
- `name`, `canonical_name` (lowercase, no spaces)
- `mass_car_kg`, `mass_driver_kg`
- `weight_dist_front` (from corner weights in garage if available)
- `default_df_balance_pct` (from the aero calculator in a real session)
- `wing_angles` (from the garage dropdown range)
- `garage_ranges` (legal min/max for every parameter — read from garage or schema JSON)
- Mark all physics constants as `# ESTIMATE` until calibrated

```python
MY_NEW_CAR = CarModel(
    name="My Car",
    canonical_name="mycar",
    mass_car_kg=1030.0,
    # ... etc
)

_CARS["mycar"] = MY_NEW_CAR
```

### Step 2: Add aero map data

Place the aero map xlsx files in `data/aeromaps/` following the naming convention and run the parser:

```bash
python3 -m aero_model.parse_all --car mycar
```

### Step 3: Add STO parameter ID mappings in `output/setup_writer.py`

Find the `_BMW_PARAM_IDS` dict and create a `_MYCAR_PARAM_IDS` dict with the correct `CarSetup_*` XML IDs. Add it to `_CAR_PARAM_IDS`.

You can extract the correct IDs by parsing a real `.sto` file for the car — the STO format is XML and the IDs are visible. Or use iRacing's LDX file (same format) from a session with that car.

### Step 4: Add setup reader mappings in `analyzer/setup_reader.py::CurrentSetup.from_ibt()`

The session YAML structure differs between cars (LMDh vs LMH vs ORECA). Add detection logic and field mappings following the Ferrari/Acura patterns already there.

### Step 5: Calibrate physics constants

Minimum viable calibration:
1. Drive one session → run `auto_calibrate_and_apply()` → get aero compression, corner weights, torsion C if available
2. Take 5+ garage screenshots at different torsion OD settings → back-solve C from `CornerWeight / (TorsionBarDefl * OD^4)`
3. Drive 3 sessions with different heave spring settings → calibrate m_eff

### Step 6: Update `validation/objective_validation.json` support matrix

Add an entry for the new car/track with `confidence_tier: "exploratory"`. Promote to `partial` after 10+ observations, `calibrated` after 50+ with stable Spearman correlation.

---

## 14. Adding a New Track {#adding-track}

### Automatic (recommended)

Drive any session at the track. The pipeline auto-builds and saves the profile:

```bash
python3 -m pipeline.produce --car bmw --ibt new_track_session.ibt --wing 17 --report-only
```

The track profile is saved to `data/tracks/` automatically.

### Manual (if you only have the track name)

```bash
python3 track_model/build_profile.py session_at_new_track.ibt -o data/tracks/new_track.json
```

### What a track profile contains

`track_model/profile.py::TrackProfile` stores:
- `shock_vel_p95_front_mps` / `shock_vel_p99_front_mps` — surface roughness (drives heave spring sizing)
- `median_speed_kph` — aero operating point
- `corner_speed_distribution` — % of laps in slow/mid/high speed bands
- `best_lap_time_s` — reference
- Kerb spatial mask — which GPS positions are kerb strikes (excluded from RH variance calculation)
- Per-sector shock velocity spectra

The heave solver uses `shock_vel_p99_front_mps` directly. The aero solver uses `median_speed_kph` to scale compression. A generic profile (`track_model/generic_profiles.py`) is used as fallback but gives much less accurate results.

---

## 15. Improving the Objective Function {#objective}

The current objective (`solver/objective.py::ObjectiveFunction`) has Spearman r = -0.18 with lap time on BMW/Sebring (98 sessions). This is the single biggest limitation. Here is how to improve it.

### What's currently wrong

The scoring formula:
```
total = w_lap_gain * lap_gain_ms - w_platform * platform_risk_ms - ...
```

The `lap_gain_ms` term is a hand-tuned penalty sum (LLTD error, damping ratios, DF balance error, camber error, etc.). The penalty magnitudes and target values were set by engineering judgment, not measured from data. Some terms have wrong direction correlations when checked against real sessions.

### How to improve it

**Approach 1 (quickest win): Grow the dataset**

The calibration tool (`validation/objective_calibration.py`) runs ablation and weight search on the BMW/Sebring observation dataset. Run it with more observations:

```bash
# After ingesting more sessions:
python3 -m validation.objective_calibration --car bmw --track sebring
# → outputs validation/calibration_report.md with recommended weights
```

Apply the weights to `solver/scenario_profiles.py::ObjectiveWeightProfile` only when the holdout worst-case Spearman is reliably < -0.15 in all 10-fold splits.

**Approach 2: Replace penalty scoring with empirical k-NN**

The `solver/session_database.py::SessionDatabase` already does k-NN scoring when ≥3 sessions exist. Increase the empirical weight (`w_empirical`) as the dataset grows and the k-NN predictions become reliable.

**Approach 3: Separate platform scoring from pace scoring**

The current formula conflates "this setup is safe" with "this setup is fast". Better approach: gate on platform safety first (hard veto if not safe), then rank surviving candidates purely by empirical pace signal (lap time correlation).

**Where to look in the code:**
- `solver/objective.py::_estimate_lap_gain()` — the lap gain penalty sum (most overfit to BMW)
- `solver/objective.py::_compute_platform_risk()` — the physics-based safety scoring (more reliable)
- `solver/scenario_profiles.py` — scenario weights (change here, not in objective.py)
- `validation/objective_calibration.py` — the calibration tooling

---

## 16. Data Sources and Calibration Pipeline {#calibration-pipeline}

### Data flow: how physics constants become calibrated

```
Garage screenshot / webapp JSON
  → car_model/garage_schema_ingester.py
  → data/garage_schemas/{car}_{hash}.json
  → PhysicsFromSchema.front_torsion_c (exact, from fSideSpringRateNpm)
  → PhysicsFromSchema.front_heave_rate_at_index (index→rate map)

IBT file
  → car_model/auto_calibrate.py::calibrate_from_ibt()
  → data/auto_calibration/{car}_{track}_{hash}.json
  → aero_compression (from AeroCalcFrontRhAtSpeed)
  → m_eff (from HFshockVel + HFshockDefl)
  → torsion_c (from CornerWeight + TorsionBarDefl)

Both accumulate via weighted average across sessions
  → applied to car model before solver runs
```

### Priority order for improving calibration per car

1. **Feed garage schema JSONs** from 6+ different setups (different torsion OD indices, different heave spring indices). Gets you exact spring rate curves.

2. **Ingest IBT files** from sessions with those different setups. Gets you aero compression, m_eff, weight distribution.

3. **Damper sweep** (most effort): Drive two sessions identical except LS comp clicks at min and max. Back-solve `force_per_click` from the frequency shift in `HFshockVel`.

4. **ARB stiffness sweep**: Take garage screenshots at each ARB size with different OD settings, measure LLTD from IBT (`LatAccel` vs roll). Gives you the actual stiffness values.

---

## 17. Test Suite {#tests}

```bash
# Full test suite (excludes webapp and sync which need server running)
python3 -m pytest tests/ -q \
  --ignore=tests/test_webapp_routes.py \
  --ignore=tests/test_webapp_services.py \
  --ignore=tests/test_webapp_regression.py \
  --ignore=tests/test_sync_client.py \
  --ignore=tests/test_acura_hockenheim.py

# Expected: 162 passed, 7 skipped
```

### Key test files

| Test file | What it covers |
|-----------|---------------|
| `test_candidate_search.py` | Candidate family generation and scoring |
| `test_objective_calibration.py` | Objective function scoring against BMW/Sebring fixtures |
| `test_garage_validator.py` | Legal constraint checking for all parameters |
| `test_legal_search_scenarios.py` | Legal-manifold search with different scenario profiles |
| `test_registry_consistency.py` | CarSetup_* XML ID consistency across all cars |
| `test_reasoning_veto.py` | Veto logic when a candidate matches a failed cluster |
| `test_physics_corrections.py` | m_eff and aero compression application |
| `test_produce_errors.py` | Pipeline error handling (missing laps, bad files) |
| `test_sto_binary.py` | STO file reading and writing round-trip |
| `test_ferrari_setup_writer.py` | Ferrari-specific STO output (indexed springs, letter ARBs) |

### Adding tests for a new feature

```python
# tests/test_my_feature.py
import pytest
from car_model.cars import get_car

def test_my_calibration():
    car = get_car("bmw")
    # your test here
```

Tests that need real IBT files are decorated with `@pytest.mark.skipif(not FIXTURES_PRESENT, reason="...")` to keep CI fast.

---

## 18. Known Limitations and Active Fix Plan {#known-limits}

### Confirmed issues (as of 2026-03-31)

**1. Double-solve in `pipeline/produce.py`**
Steps 1-6 are run twice in `produce()`. The first pass (explicit loop, lines ~597–866) is discarded when `run_base_solve()` is called at line ~889. This doubles solve time for no gain.
- **File:** `pipeline/produce.py`
- **Fix:** Remove lines ~597–866; use only `run_base_solve()`

**2. Ferrari C constant is 2× too high**
From a garage schema JSON with `fSideSpringRateNpm=115170.265625` at torsion OD index 2:
- Computed: C = 115.17 / (20.44mm)^4 = **0.000660**
- Currently used: **0.001282** (from 9-point calibration sweep in March 2026)

The discrepancy suggests the calibration sweep misidentified which spring was contributing load (heave + torsion series vs pure torsion). This will cause the solver to overestimate corner spring rate by ~2×, producing undersized torsion OD recommendations.
- **File:** `car_model/cars.py::FERRARI_499P.corner_spring.front_torsion_c`
- **Fix:** Feed more garage schema JSONs from Ferrari sessions to accumulate the index→rate curve and resolve the discrepancy. Run `derive_physics_from_schema()` on them and compare.

**3. Objective function correlation is near-zero**
BMW/Sebring Spearman = -0.18 (98 sessions). Not reliable for ranking.
- **Fix plan:** See [Improving the Objective Function](#objective)

**4. Ferrari/Acura damper force per click is estimated**
Ferrari: `ls_force_per_click_n=7.0, hs_force_per_click_n=30.0` — estimated, never measured.
Acura: `ls_force_per_click_n=18.0, hs_force_per_click_n=80.0` — estimated (same as BMW).
- **Fix:** Systematic damper click sweeps with IBT capture (see [Physics Constants](#physics-constants))

**5. Dead code in solver/**
`solver/iterative_solver.py` (413 lines), `solver/corner_strategy.py`, `solver/coupling.py` — all have zero callers.
- **Fix:** Delete them

**6. AeroCalc channels only extracted since 2026-03-31**
Sessions before this date do not have `aerocalc_front_rh_at_speed_mm` in their stored observations, so they will use the estimated compression constant from `cars.py`.

### Feature gaps

| Gap | Affected Cars | Priority |
|-----|--------------|----------|
| Acura front RH model uses pushrod not camber as primary control | Acura | High |
| Ferrari heave motion ratio not modeled (heave deflects ~1.9mm/mm) | Ferrari | Medium |
| ARB stiffness values estimated for all cars except BMW | All non-BMW | Medium |
| Roll dampers (Acura) use static baselines, no physics tuning | Acura | Medium |
| Torsion bar turns not used in RH model (only perch offset is modeled) | Ferrari | Low |
| Multi-car sessions (iRacing mixed-class) not supported | All | Low |

---

## 19. Key Files Reference {#key-files}

### Entry points

| File | Purpose |
|------|---------|
| `pipeline/produce.py::main()` | Primary pipeline: IBT → diagnosis → solve → .sto |
| `solver/solve.py::main()` | Track-only: no IBT, uses saved track profile |
| `analyzer/__main__.py` | Diagnosis only: no solve |
| `learner/ingest.py` | Ingest IBT into knowledge store |
| `comparison/__main__.py` | Multi-session comparison |
| `webapp/__main__.py` | Web UI |

### Car model

| File | Purpose |
|------|---------|
| `car_model/cars.py` | All car definitions (`BMW_M_HYBRID_V8`, `FERRARI_499P`, etc.) |
| `car_model/setup_registry.py` | Canonical field definitions + per-car YAML/STO mappings |
| `car_model/garage.py` | `GarageSetupState` + `GarageOutputs` regression models |
| `car_model/auto_calibrate.py` | IBT-based physics constant derivation (NEW) |
| `car_model/garage_schema_ingester.py` | Webapp JSON ingestion (NEW) |
| `car_model/calibrate_deflections.py` | One-time calibration script (run once per car to fit models) |

### Solver

| File | Purpose |
|------|---------|
| `solver/solve_chain.py` | Orchestrates the 6-step solver chain |
| `solver/full_setup_optimizer.py` | BMW/Sebring constrained optimizer (SciPy) |
| `solver/rake_solver.py` | Step 1: ride heights |
| `solver/heave_solver.py` | Step 2: heave/third springs |
| `solver/corner_spring_solver.py` | Step 3: torsion OD + rear spring |
| `solver/arb_solver.py` | Step 4: ARBs |
| `solver/wheel_geometry_solver.py` | Step 5: camber + toe |
| `solver/damper_solver.py` | Step 6: damper clicks |
| `solver/supporting_solver.py` | Brake bias, diff, TC, tyre pressures |
| `solver/modifiers.py` | Translate diagnosis + driver → solver adjustments |
| `solver/objective.py` | Candidate scoring function |
| `solver/scenario_profiles.py` | Scenario weight profiles |
| `solver/legal_search.py` | Random legal-manifold search |
| `solver/grid_search.py` | Structured Sobol + coordinate-descent search |
| `solver/legality_engine.py` | Legal constraint validation |

### Analyzer

| File | Purpose |
|------|---------|
| `analyzer/extract.py` | All telemetry channel extraction (including AeroCalc channels) |
| `analyzer/diagnose.py` | 6-priority handling problem classification |
| `analyzer/driver_style.py` | Trail brake, throttle, smoothness, consistency |
| `analyzer/segment.py` | Per-corner segmentation |
| `analyzer/modifiers.py` | Diagnosis → solver input adjustments |
| `analyzer/setup_reader.py` | Parse garage setup from IBT session YAML |
| `analyzer/telemetry_truth.py` | Signal quality tracking |

### Learner

| File | Purpose |
|------|---------|
| `learner/knowledge_store.py` | Persistent JSON store |
| `learner/ingest.py` | Run analyzer → store observation |
| `learner/empirical_models.py` | Fit corrections from accumulated data |
| `learner/recall.py` | Query stored knowledge for solver |
| `learner/delta_detector.py` | Detect setup changes between sessions |

### Validation

| File | Purpose |
|------|---------|
| `validation/run_validation.py` | Full BMW/Sebring validation run |
| `validation/objective_calibration.py` | Weight search + ablation tooling |
| `validation/observation_mapping.py` | Canonical setup→parameter mappings |
| `validation/objective_validation.json` | Current validation state (authoritative) |

### Output

| File | Purpose |
|------|---------|
| `output/setup_writer.py` | Write `.sto` XML (car-specific CarSetup_* IDs) |
| `output/report.py` | Terminal engineering report formatting |
| `output/garage_validator.py` | Final legal check + correction before .sto write |
| `output/run_trace.py` | Data provenance tracking |
| `output/decision_trace.py` (via `solver/decision_trace.py`) | Per-parameter reasoning |

---

## Appendix: Understanding Calibration Tiers

**Calibrated** means:
- Physics constants (m_eff, torsion C, aero compression) measured from real IBT data
- Legal ranges confirmed from garage truth
- ≥50 observations in knowledge store
- Score-vs-lap-time Spearman correlation tested on holdout (not just in-sample)
- Outputs described as "physics-justified recommendation" not "optimal"

**Partial** means:
- Some physics constants calibrated (e.g., Ferrari torsion C from 9-pt sweep)
- Some constants still estimated (m_eff, damper force/click)
- <20 observations in knowledge store
- Outputs described as "approximate starting point"

**Exploratory** means:
- Architecture wired (can produce a complete .sto)
- Most physics constants estimated from similar cars
- <10 observations
- Outputs described as "directional guidance — expect one tuning session"

**Unsupported** means:
- Architecture wired but untested
- All physics constants estimated or borrowed
- No IBT observations ingested
- Outputs should be hand-checked against garage before use

---

*Guide last updated: 2026-03-31. Reflects codebase state on branch `cursor/iracing-setup-solver-audit-49a4`.*
