> See also: `docs/usage_and_calibration_guide.md` for a practical runbook covering
> day-to-day solver usage, support tiers, calibration collection, publication,
> and guidance for future coders extending the system.

# IOptimal Usage Guide

Physics-based setup calculator for iRacing GTP/Hypercar class. Produces optimized setups from first principles using telemetry data.

## Requirements

- Python 3.11+
- numpy
- scipy
- openpyxl

Install dependencies:

```bash
pip install numpy scipy openpyxl
```

For local development / cloud-agent test execution, install the dev test stack:

```bash
python3 -m pip install -r requirements-dev.txt
```

This installs `pytest` plus the Python solver dependencies used by the focused regression suite and CI workflow.

## Supported Cars

| Name | CLI Flag | Full Name |
|------|----------|-----------|
| BMW | `--car bmw` | BMW M Hybrid V8 LMDh |
| Cadillac | `--car cadillac` | Cadillac V-Series.R |
| Ferrari | `--car ferrari` | Ferrari 499P |
| Porsche | `--car porsche` | Porsche 963 GTP |
| Acura | `--car acura` | Acura ARX-06 |

Current support tiers in the codebase:

- BMW/Sebring: calibrated
- Ferrari/Sebring: partial
- Cadillac/Silverstone: exploratory
- Porsche/Acura: unsupported until more telemetry exists

## Quick Start

**Produce a setup from a telemetry file:**

```bash
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --scenario-profile single_lap_safe --sto my_setup.sto
```

This reads your IBT telemetry, profiles your driving style, diagnoses handling, runs the 6-step physics solver, and writes an iRacing `.sto` setup file you can load directly in the garage. For BMW/Sebring, the runtime objective currently reproduces `73` observed sessions with `72` non-vetoed rows and a non-vetoed Spearman of `-0.120522`, so treat outputs as calibrated-but-not-yet-authoritative rather than guaranteed optimal.

---

## Commands

### 1. Pipeline — Full IBT-to-Setup Production

The primary workflow. Takes a telemetry file and produces a complete, driver-adaptive setup.

```bash
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --scenario-profile single_lap_safe --sto output.sto
```

**Arguments:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--car` | Yes | — | Car name (`bmw`, `ferrari`, `porsche`, `cadillac`, `acura`) |
| `--ibt` | Yes | — | Path to IBT telemetry file |
| `--wing` | No | auto (from IBT) | Wing angle in degrees |
| `--lap` | No | best lap | Specific lap number to analyze |
| `--balance` | No | 50.14 | Target aero DF balance (%) |
| `--tolerance` | No | 0.1 | Balance tolerance (%) |
| `--fuel` | No | auto | Fuel load in liters |
| `--free` | No | off | Search the legal setup manifold from the pinned baseline and apply the best fully accepted candidate |
| `--explore-legal-space` | No | off | Run legal-manifold search after the base physics solve |
| `--search-mode` | No | off | Structured legal-manifold search mode: `quick`, `standard`, or `exhaustive` |
| `--scenario-profile` | No | `single_lap_safe` | Scenario objective profile: `single_lap_safe`, `quali`, `sprint`, or `race` |
| `--sto` | No | — | Export iRacing .sto setup file to this path |
| `--json` | No | — | Save full solver results as JSON |
| `--report-only` | No | off | Only print the final report (skip per-step details) |
| `--learn` | No | off | Apply empirical corrections from the knowledge store |
| `--auto-learn` | No | off | Ingest this session into the knowledge store after producing |

**Examples:**

```bash
# Basic setup production
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --scenario-profile single_lap_safe --sto output.sto

# Qualifying-biased single-lap solve
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --lap 25 --scenario-profile quali --json output.json

# Race-stint legal-manifold search from the pinned physics seed
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --free --scenario-profile race --sto output.sto

# Use learning system for refined output
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --scenario-profile sprint --sto output.sto --learn --auto-learn

# Concise report only
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --report-only
```

The pipeline prints a detailed engineering report covering: driver profile, handling diagnosis, aero analysis, each solver step, supporting parameters, selected scenario profile, decision trace, legal-manifold candidate status, and a comparison of your current setup vs the produced setup.

---

### 2. Analyzer — Diagnose an Existing Setup

Analyzes telemetry to identify handling problems and recommend specific setup changes. Does not produce a new setup — just tells you what's wrong and what to change.

```bash
python -m analyzer --car bmw --ibt session.ibt
```

**Arguments:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--car` | Yes | — | Car name |
| `--ibt` | Yes | — | Path to IBT telemetry file |
| `--lap` | No | best lap | Lap number to analyze |
| `--save` | No | — | Save JSON report to this path |

**Example:**

```bash
python -m analyzer --car bmw --ibt session.ibt --save diagnosis.json
```

The report includes: setup readout, ride height statistics, shock velocity histograms, handling diagnosis (understeer/oversteer/bottoming/instability), and prioritized recommendations.

---

### 3. Solver — Standalone Setup Solver

Runs the 6-step physics solver using a pre-built track profile (no IBT needed). Useful when you already have a track profile saved and just want to compute a setup.

```bash
python -m solver.solve --car bmw --track sebring --wing 17 --scenario-profile single_lap_safe --sto output.sto
```

**Arguments:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--car` | Yes | — | Car name |
| `--track` | Yes | — | Track name (must match a profile in `data/tracks/`) |
| `--wing` | Yes | — | Wing angle in degrees |
| `--balance` | No | 50.14 | Target DF balance (%) |
| `--tolerance` | No | 0.1 | Balance tolerance (%) |
| `--fuel` | No | 89.0 | Fuel load in liters |
| `--free` | No | off | Search the legal setup manifold from a pinned baseline seed |
| `--legal-search` | No | off | Run legal-manifold search after the base physics solve |
| `--scenario-profile` | No | `single_lap_safe` | Scenario objective profile: `single_lap_safe`, `quali`, `sprint`, or `race` |
| `--json` | No | off | Output as JSON to stdout |
| `--save` | No | — | Save full JSON summary to file |
| `--sto` | No | — | Export iRacing .sto setup file |
| `--report-only` | No | off | Only print the garage setup sheet |
| `--learn` | No | off | Apply empirical corrections from learnings |

**Examples:**

```bash
# Solve and export
python -m solver.solve --car bmw --track sebring --wing 17 --scenario-profile single_lap_safe --sto output.sto

# Save JSON for validation
python -m solver.solve --car bmw --track sebring --wing 17 --scenario-profile quali --save solver_output.json

# Run legal-manifold search for a race profile
python -m solver.solve --car bmw --track sebring --wing 17 --legal-search --scenario-profile race --sto output.sto

# Quick garage sheet
python -m solver.solve --car bmw --track sebring --wing 17 --report-only
```

**Note:** The solver requires a track profile JSON in `data/tracks/`. Build one first if it doesn't exist (see Track Profile Builder below).

---

### 4. Comparison — Multi-Session Analysis

Compares two or more telemetry sessions side by side. Ranks them, identifies what worked, and optionally synthesizes an optimal setup from the best elements of each session.

```bash
python -m comparison --car bmw --ibt session1.ibt session2.ibt session3.ibt --wing 17
```

**Arguments:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--car` | Yes | — | Car name |
| `--ibt` | Yes (2+) | — | Paths to two or more IBT files |
| `--wing` | No | auto | Wing angle for synthesis |
| `--lap` | No | best lap | Lap number(s) — one per IBT file, or a single value for all |
| `--balance` | No | 50.14 | Target DF balance for synthesis (%) |
| `--fuel` | No | auto | Fuel load override in liters |
| `--sto` | No | — | Export synthesized setup as .sto |
| `--json` | No | — | Save comparison results as JSON |
| `--no-synthesis` | No | off | Compare and rank only, don't synthesize |

**Examples:**

```bash
# Compare and synthesize best setup
python -m comparison --car bmw --ibt run1.ibt run2.ibt run3.ibt --wing 17 --sto best.sto

# Compare only, no synthesis
python -m comparison --car bmw --ibt run1.ibt run2.ibt --no-synthesis
```

---

### 5. Learner — Knowledge Store Management

Every telemetry session is treated as an experiment. The learner extracts observations, detects what changed between sessions, fits empirical corrections, and accumulates knowledge that improves future solver runs.

**Ingest a session:**

```bash
python -m learner --car bmw --ibt session.ibt
```

**Check what's stored:**

```bash
python -m learner --status
```

**Recall knowledge for a car/track:**

```bash
python -m learner --recall --car bmw --track sebring
```

**Arguments:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--car` | For ingest/recall | — | Car name |
| `--ibt` | For ingest | — | Path to IBT file |
| `--wing` | No | — | Wing angle override |
| `--lap` | No | — | Lap number to analyze |
| `--status` | No | off | Show knowledge store index |
| `--recall` | No | off | Dump stored knowledge for a car/track |
| `--track` | For recall | — | Track name |

Knowledge is stored in `data/learnings/` and persists across sessions. Use `--learn` on the pipeline or solver to apply these corrections automatically.

---

### 6. Validator — Check Solver Accuracy

Compares solver predictions against measured telemetry. Useful for calibrating the model and understanding where the solver is accurate vs where it needs refinement.

```bash
python -m validator --car bmw --track sebring --wing 17 --ibt session.ibt --setup solver_output.json
```

**Arguments:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--car` | Yes | — | Car name |
| `--track` | Yes | — | Track name |
| `--wing` | Yes | — | Wing angle in degrees |
| `--ibt` | Yes | — | Path to IBT or .zip telemetry file |
| `--setup` | Yes | — | Path to solver output JSON (from `--save`) |
| `--lap` | No | best lap | Lap to analyze |
| `--json` | No | off | Output as JSON |
| `--save` | No | — | Save validation report JSON |
| `--next-profile` | No | — | Save updated track profile for next iteration |

**Workflow:**

```bash
# Step 1: Run the solver and save its output
python -m solver.solve --car bmw --track sebring --wing 17 --save solver_output.json

# Step 2: Drive on that setup and collect telemetry

# Step 3: Validate predictions against reality
python -m validator --car bmw --track sebring --wing 17 --ibt new_session.ibt --setup solver_output.json
```

---

## Running Tests

The repository ships a focused Python regression suite under `tests/`.

Install the development dependencies first:

```bash
python3 -m pip install -r requirements-dev.txt
```

Then run the suite with either `pytest` or the Python module form:

```bash
python3 -m pytest -q tests
```

If you are in a minimal environment, some integration coverage may still be skipped when optional scientific dependencies are unavailable. The CI workflow installs the full dev stack, including `pytest`.

Exit codes: `0` = predictions match, `1` = needs tweaking, `2` = significant mismatch.

---

### 7. Track Profile Builder

Builds a `TrackProfile` JSON from an IBT file. The track profile captures surface characteristics, corner demands, speed distribution, braking zones, and kerb severity. Required by the standalone solver.

```bash
python track_model/build_profile.py session.ibt
```

**Arguments:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `ibt_file` | Yes (positional) | — | Path to .ibt or .zip file |
| `--output` / `-o` | No | auto | Output JSON path (auto: `data/tracks/<track>_<config>.json`) |

**Example:**

```bash
# Auto-named output based on track metadata in the IBT
python track_model/build_profile.py session.ibt

# Explicit output path
python track_model/build_profile.py session.ibt -o data/tracks/sebring.json
```

---

### 8. Web App

Local-first FastAPI UI for the existing solver stack. App metadata is stored in SQLite, while uploads and generated artifacts are kept in your local app-data directory.

```bash
python -m pip install -r requirements-dev.txt
python -m webapp
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

**Default local storage:**

- Windows: `%LOCALAPPDATA%\IOptimal`
- Override path: set `IOPTIMAL_APP_DATA_DIR`

**Main views:**

- `New Run` — single-session solve, multi-session compare, and track-only solve
- `Sessions` — run history backed by SQLite metadata
- `Compare` — recent comparison runs
- `Knowledge` — learner summaries without manually opening JSON files

---

## Typical Workflows

### First time at a new track

1. **Drive a session** in iRacing (practice, any setup)
2. **Build a track profile** from the IBT:
   ```bash
   python track_model/build_profile.py session.ibt
   ```
3. **Produce a setup** from telemetry:
   ```bash
   python -m pipeline --car bmw --ibt session.ibt --wing 17 --sto round1.sto
   ```
4. **Load** `round1.sto` in iRacing and drive another session
5. **Iterate** — feed the new IBT back into the pipeline

### Iterating on a setup

1. **Analyze** what's wrong with your current run:
   ```bash
   python -m analyzer --car bmw --ibt latest_session.ibt
   ```
2. **Produce** an updated setup:
   ```bash
   python -m pipeline --car bmw --ibt latest_session.ibt --wing 17 --sto improved.sto
   ```
3. **Compare** sessions to track progress:
   ```bash
   python -m comparison --car bmw --ibt session1.ibt session2.ibt session3.ibt --wing 17
   ```

### Building long-term knowledge

1. **Ingest every session** into the learner:
   ```bash
   python -m learner --car bmw --ibt session.ibt
   ```
2. Or use `--auto-learn` on the pipeline to do it automatically:
   ```bash
   python -m pipeline --car bmw --ibt session.ibt --wing 17 --sto output.sto --learn --auto-learn
   ```
3. **Review** accumulated knowledge:
   ```bash
   python -m learner --recall --car bmw --track sebring
   ```

### Validating the solver

1. **Run the solver** and save output:
   ```bash
   python -m solver.solve --car bmw --track sebring --wing 17 --save solver.json --sto test_setup.sto
   ```
2. **Drive on the solver's setup** and save the IBT
3. **Validate** predictions vs reality:
   ```bash
   python -m validator --car bmw --track sebring --wing 17 --ibt validation_run.ibt --setup solver.json
   ```

---

## Output Files

### .sto (iRacing Setup)

XML file loadable directly in the iRacing garage. Drop it into your iRacing setups folder or use "Import Setup" in-game.

Default iRacing setups directory:
```
%USERPROFILE%\Documents\iRacing\setups\<car>\
```

### JSON Reports

Machine-readable output containing all solver decisions, parameter values, and reasoning. Useful for scripting, validation, or further analysis.

### Terminal Reports

All commands print formatted ASCII reports (63-character width) to the terminal. These include driver profiles, handling diagnoses, solver step summaries, and setup comparisons.

---

## Data Directories

| Directory | Contents |
|-----------|----------|
| `data/aeromaps_parsed/` | Pre-parsed aero maps per car (required, included) |
| `data/tracks/` | Track profile JSONs (built from IBT files) |
| `data/learnings/` | Persistent knowledge store (observations, models, insights) |
| `data/calibration/` | Calibration reference data |
| `ibtfiles/` | Place your IBT telemetry files here |
| `output/` | Generated setup files and reports |

---

## Tips

- **Wing angle**: If your IBT was recorded with the wing you want to target, you can omit `--wing` and the pipeline will read it from the telemetry.
- **Best lap**: By default, all tools analyze the fastest clean lap. Use `--lap N` to override.
- **Free mode**: The `--free` flag lets the solver optimize front ride height freely instead of pinning it to the sim floor. Try it if the default feels too stiff.
- **Learning compounds**: The more sessions you ingest, the better the empirical corrections become. Ingest consistently for best results.
- **Ferrari caveat**: Ferrari rear suspension uses a torsion bar that isn't fully decoded yet. Corner spring and LLTD outputs for Ferrari should be treated as approximate.

---

## New Features (March 2026)

### `--space` Flag — Setup Space Exploration

Scans around the solver's optimal values to map the "flat bottom" of the parameter space.

```bash
python -m solver.solve --car bmw --track sebring --wing 17 --space
```

For each key parameter, the space explorer:
- Scans ±N steps from the optimal value
- Scores each point: constraint violations + estimated lap time delta
- Identifies the "flat bottom" range (lap time delta < 100ms)
- Classifies robustness: **tight** / **moderate** / **wide**

**How to read the output:**
```
SETUP SPACE EXPLORATION
Parameter        Optimal    Flat Bottom Range    Robustness
rear_rh_mm       40.0mm     38.0–43.0mm (±2.5)  moderate
front_heave_nmm  50 N/mm    40–70 N/mm   (±15)  wide
rear_arb_blade   3          2–4          (±1)    tight
```
- **Tight**: Must nail this parameter precisely in the garage
- **Moderate**: Small errors (±1 step) tolerable
- **Wide**: Broad latitude — de-prioritize this parameter in on-track tuning

---

### Stint Model Output Interpretation

The stint model appears in the report after the main setup section. It shows how the setup's behavior changes as fuel burns off during a stint.

**Key sections:**
- **Balance evolution**: Understeer shift over laps as fuel lightens the car
- **RARB compensation schedule**: Which laps to soften/stiffen the rear ARB to maintain balance
- **Pushrod schedule**: Pushrod adjustments (pitstop only) to compensate for fuel burn

**Example:**
```
STINT ANALYSIS (30 laps)
  Lap  Fuel  FrontWt  US_shift  RARB_rec  Verdict
  1    89L   47.0%    +0.0deg   blade 3   Baseline
  15   58L   47.8%    +0.4deg   blade 2   Soften slightly
  30   27L   48.6%    +0.8deg   blade 2   Monitor balance
```
- Positive US_shift = car tends more towards understeer as fuel burns
- RARB_rec = recommended live RARB blade for that fuel load

---

### Sector Compromise Table Interpretation

The sector compromise section appears below the stint analysis. It shows the trade-off cost of a single setup across different corner types.

```
SECTOR COMPROMISE
Parameter         Slow opt  Fast opt  Lap compromise  Slow cost  Fast cost
rear_arb_blade    2         4         3               +0.3s      +0.2s
front_heave_nmm   40        70        50              +0.1s      +0.15s
brake_bias_pct    57.5      55.0      56.0            +0.05s     +0.08s
```

- **Slow opt**: Optimal value for slow-speed corners (<120 kph)
- **Fast opt**: Optimal value for fast-speed corners (>180 kph)
- **Lap compromise**: Best single value across all sectors
- **Slow/Fast cost**: Estimated time loss per lap from the compromise in that sector

If slow cost >> fast cost: circuit is slow-corner dominated — bias the setup toward slow-corner optimum.

---

### Lap Time Sensitivity Ranking Interpretation

The sensitivity section ranks setup parameters by how many milliseconds of lap time you gain per unit of change. This helps prioritize where to spend setup time.

```
LAP TIME SENSITIVITY ANALYSIS
Parameter          Value    ±ms/unit   Conf
rear_rh_mm         40.0     -45.2      med    <- biggest lever
rear_arb_blade      3.0     +38.1      high
front_rh_mm        15.0     -22.4      med
brake_bias_pct     56.0     -18.3      med
torsion_bar_od_mm  13.9      -8.7      med
front_heave_nmm    50.0      -0.8      low
rear_camber_deg    -1.8     +28.5      low
```

**Reading the signs:**
- **Negative ms/unit** = faster if you *decrease* the parameter
- **Positive ms/unit** = faster if you *increase* the parameter

**Focus:**
- Parameters >50ms/unit: prioritize these in on-track fine-tuning
- Parameters 20-50ms/unit: worthwhile but less urgent  
- Parameters <20ms/unit: marginal gains — only tune after the above are locked in
- "low" confidence = physics model estimate only; treat as directional, not precise

**Typical ranking at Sebring:**
1. Rear RH (aero balance — most critical)
2. Rear ARB (LLTD and balance)
3. Front RH (vortex sensitivity)
4. Brake bias (entry stability)
5. Torsion bar OD (front wheel rate/LLTD coupling)

---

### 12. Ferrari Hockenheim Calibration Helper (Setup JSON + IBT/ZIP)

Builds a calibration JSON from a setupdelta-style setup export plus one or more telemetry files.
Useful for week-specific Ferrari tuning when you want channel coverage, aggregate signal stats,
and a deterministic starting setup recommendation.

```bash
python -m scripts.ferrari_hockenheim_calibration \
  --setup-json tests/fixtures/ferrari_hockenheim_setupdelta.json \
  --telemetry \
    "ferrari499p_hockenheim_1.zip" \
    "ferrari499p_hockenheim_2.zip" \
    "ferrari499p_hockenheim_3.zip" \
  --output out/ferrari_hockenheim_calibration.json
```

Notes:
- Accepts `.ibt` directly, or `.zip` containing one `.ibt`.
- If a telemetry file is missing, the report still builds and lists missing files.
- Output is currently exploratory-tier guidance and should be validated with fresh race-week telemetry.
- Includes setup integrity checks for all provided rows: row-id format/uniqueness, min/max range validation,
  and Ferrari-only guardrails (`carName` must be `ferrari499p`).
