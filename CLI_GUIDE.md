# IOptimal CLI Guide
**The one-page reference for running the program from the terminal.**  
**Branch:** `claw-research` | **Updated:** 2026-03-31

---

## Quick Start

```powershell
# Install dependencies (one-time)
pip install -r requirements-dev.txt

# ── Windows: use the run.ps1 wrapper (fixes garbled Unicode in PowerShell) ──
.\run.ps1 produce --car bmw --ibt path/to/session.ibt --wing 16
.\run.ps1 produce --car bmw --ibt session.ibt --wing 16 --sto output.sto

# ── Alternative: set encoding manually then call Python directly ──
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = "1"
python __main__.py produce --car bmw --ibt session.ibt --wing 16 --sto output.sto
```

> **Windows encoding note:** PowerShell's pipe operator (`|`, `2>&1`) decodes Python's
> output using the legacy OEM code page (CP850) by default, turning box-drawing
> characters into mojibake (e.g. `Γ╠É` instead of `═`).  
> **Always use `run.ps1`** or prepend `[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); $env:PYTHONUTF8 = "1"` to your command.

---

## The One True Command

```
python -m ioptimal [OPTIONS]
```

All other runner scripts (`run_now.py`, `run_full_pipeline.py`, etc.) are legacy. Use `python -m ioptimal` exclusively.

---

## Required Arguments

| Argument | Values | Description |
|----------|--------|-------------|
| `--car` | `bmw`, `cadillac`, `ferrari`, `acura`, `porsche` | Which car to solve for |
| `--ibt` | `path/to/file.ibt` | IBT telemetry file from your iRacing session |
| `--wing` | `12`–`17` (BMW/Cadillac/Ferrari/Porsche), `6`–`10` (Acura) | Front wing angle |

---

## Common Options

| Argument | Default | Description |
|----------|---------|-------------|
| `--sto` | (none) | Output path for the .sto garage file |
| `--track` | auto-detected | Track name (e.g., `sebring`, `daytona`, `silverstone`) |
| `--fuel` | from IBT | Fuel load in litres |
| `--balance` | car default | Target DF balance % (e.g., `50.5`) |
| `--lap` | last clean lap | Which lap to use from the IBT file |
| `--json` | (none) | Also export full result as JSON |
| `--report` | (none) | Write engineering report to this path |

---

## Advanced / Scenario Options

| Argument | Default | Description |
|----------|---------|-------------|
| `--scenario` | `single_lap_safe` | Scoring scenario: `single_lap_safe`, `quali`, `sprint`, `race` |
| `--search-mode` | (none) | Enable grid search: `quick`, `standard`, `exhaustive`, `maximum` |
| `--free` | off | Enable unconstrained legal-manifold search (slower, explores wider space) |
| `--solve-only` | off | Skip IBT extraction; run solver with car defaults only |

**Scenario profiles affect objective function weights:**

| Scenario | Platform Risk Weight | When To Use |
|----------|---------------------|-------------|
| `single_lap_safe` | 0.75 (default) | Practice / hot lap / general use |
| `quali` | 0.90 | Qualifying — more aggressive |
| `sprint` | 1.00 | Sprint race — balance performance/safety |
| `race` | 1.20 | Endurance — maximize platform stability |

---

## Common Usage Examples

### Single Session → Setup File
```bash
python -m ioptimal \
  --car bmw \
  --ibt "C:/Users/you/Documents/iRacing/telemetry/bmw_sebring.ibt" \
  --wing 16 \
  --sto best_setup.sto
```

### Specify Fuel Load and Track
```bash
python -m ioptimal \
  --car cadillac \
  --ibt cadillac_session.ibt \
  --wing 14 \
  --fuel 60 \
  --track silverstone \
  --sto cadillac_silverstone.sto
```

### Generate Setup + Engineering Report
```bash
python -m ioptimal \
  --car ferrari \
  --ibt ferrari_session.ibt \
  --wing 13 \
  --sto ferrari_output.sto \
  --report ferrari_report.txt
```

### Qualifying Setup (More Aggressive Scoring)
```bash
python -m ioptimal \
  --car bmw \
  --ibt session.ibt \
  --wing 16 \
  --scenario quali \
  --sto quali_setup.sto
```

### Race Setup (Maximum Platform Stability)
```bash
python -m ioptimal \
  --car bmw \
  --ibt session.ibt \
  --wing 17 \
  --scenario race \
  --sto race_setup.sto
```

### Legal-Manifold Search (Wider Exploration)
```bash
# --free enables unconstrained search over the legal parameter space
# Slower but explores more candidates
python -m ioptimal \
  --car bmw \
  --ibt session.ibt \
  --wing 16 \
  --free \
  --sto searched_setup.sto
```

### Grid Search (Exhaustive Sweep)
```bash
# --search-mode exhaustive: full grid over legal space
# WARNING: can take 10–30 minutes
python -m ioptimal \
  --car bmw \
  --ibt session.ibt \
  --wing 16 \
  --search-mode exhaustive \
  --sto grid_best.sto
```

### Multi-Session Reasoning (Uses all sessions together)
```bash
# Pass multiple IBT files — triggers the multi-session reasoning engine
# Performs cross-session delta analysis and target profile synthesis
python -m ioptimal \
  --car bmw \
  --ibt session1.ibt session2.ibt session3.ibt \
  --wing 16 \
  --sto best_multi_session.sto
```

### Acura ARX-06 (different wing range)
```bash
python -m ioptimal \
  --car acura \
  --ibt acura_session.ibt \
  --wing 8 \
  --sto acura_output.sto
```

---

## Comparing Setups

### Validate a Setup Against Telemetry
```bash
# Check how well a .sto setup matches what the telemetry predicts
python -m validator \
  --car bmw \
  --ibt session.ibt \
  --setup my_setup.sto
```

### Compare Two Sessions Side-by-Side
```bash
python -m comparison \
  --car bmw \
  --ibt session_a.ibt session_b.ibt
```

> ⚠️ **Note:** `comparison/` uses its own separate scoring system that may rank setups
> differently from the main pipeline. Treat its output as a second opinion, not the
> same ranking as `python -m ioptimal`.

### Compare Two Setup Files
```bash
python -m pipeline --compare base.sto modified.sto --car bmw --track sebring
```

---

## Learning / Ingestion

```bash
# Store a session as an observation in the knowledge base
# Improves k-NN empirical corrections for BMW/Sebring (no effect on other cars yet)
python -m learner.ingest --car bmw --ibt session.ibt
```

---

## Solver-Only Mode (No IBT Required)
```bash
# Run the solver directly without IBT telemetry
# Uses car default physics (no telemetry personalization)
python -m solver.solve \
  --car bmw \
  --track sebring \
  --wing 16 \
  --balance 50.14 \
  --fuel 60 \
  --sto solver_only_output.sto
```

> ⚠️ `solver.solve` bypasses the garage validation step and the learning pipeline.
> Use `python -m ioptimal` whenever you have an IBT file.

---

## Calibration Tools (Development Only)

```bash
# Re-calibrate deflection models from IBT data
# (Results must be manually pasted into car_model/cars.py)
python car_model/calibrate_deflections.py

# Validate objective function against real telemetry ground truth
python validation/run_validation.py
```

---

## Environment Setup

### Windows (PowerShell)

```powershell
# Navigate to project directory
cd C:\Users\tfunk\IOptimal

# Install dependencies (one-time — no venv needed if using system Python 3.14)
pip install -r requirements-dev.txt

# ── RECOMMENDED: use run.ps1 wrapper (handles encoding automatically) ──
.\run.ps1 produce --car bmw --ibt session.ibt --wing 16 --sto output.sto
.\run.ps1 calibrate --car ferrari --status

# ── OR: set encoding manually each session ──
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = "1"
$python = "C:\Users\tfunk\AppData\Local\Python\bin\python.exe"
& $python __main__.py produce --car bmw --ibt session.ibt --wing 16 --sto output.sto
```

> **Why run.ps1?** PowerShell decodes external-process output with the OEM code page (CP850)
> by default. Without the encoding fix, all box-drawing characters and emoji print as
> garbled Latin characters. `run.ps1` sets `[Console]::OutputEncoding` + `PYTHONUTF8=1`
> before invoking Python, fixing this permanently for the session.

### Quick Check: Is Everything Installed?
```bash
python -c "import numpy, scipy, yaml; print('OK')"
```

---

## Output Files

| File | Description |
|------|-------------|
| `output.sto` | iRacing garage setup file — load directly in-game |
| `output.json` | Full solver result with all intermediate steps (if `--json` passed) |
| `report.txt` | Engineering explanation of every parameter decision (if `--report` passed) |

### Loading the .sto in iRacing
1. Copy the `.sto` file to: `Documents\iRacing\setups\<CarName>\<TrackName>\`
2. In iRacing Garage → Load Setup → select the file
3. If the file doesn't load, check the engineering report for validation warnings

---

## Troubleshooting

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| `KeyError: Unknown car 'xxx'` | Wrong car name | Use: `bmw`, `cadillac`, `ferrari`, `acura`, `porsche` |
| `FileNotFoundError: session.ibt` | Path is wrong | Use absolute path or check spelling |
| `.sto file doesn't load in iRacing` | Deflection validation warnings | Check `--report` output for constraint violations |
| `WARN: DeflectionModel not calibrated` | Non-BMW car with BMW defaults | See ENGINEERING_AUDIT.md Fix 0.2 — outputs may have wrong deflection values |
| `No optimizer available for this car/track` | Optimizer is BMW+Sebring only | Expected — sequential solver runs instead |
| Acura produces very stiff setups | m_eff calibration issue / roll damper architecture | Known issue. See ENGINEERING_AUDIT.md Section 5.1 |
| Ferrari damper values seem wrong | Click range 0–40 vs BMW 0–11; two separate damper subsystems | Known issue. Verify output before use. See ENGINEERING_AUDIT.md Fix 1.5 |
| Ferrari ride heights don't match garage | Torsion bar turns field not output by solver | Known issue. See ENGINEERING_AUDIT.md Fix 0.5 |
| Ferrari rear spring values look extreme | Rear torsion C constant is 3.5× wrong | Known issue. See ENGINEERING_AUDIT.md Fix 0.3 |
| Scoring seems inconsistent between runs | Multiple independent "best" selectors | By design — see ENGINEERING_AUDIT.md Section 3.6 for explanation |
| `comparison/` output conflicts with main pipeline | Comparison uses separate scoring | Expected — comparison is a second opinion, not the same system |

---

## Car-Specific Notes

### BMW M Hybrid V8 ✅ Best supported
- Full constrained optimizer active at Sebring
- Fully calibrated: deflection model, ride height model, LLTD target (0.41), m_eff, 76+ sessions in k-NN database
- Wing range: 12–17°
- Scenario profiles, rotation search, and learning loop all active
- **Most reliable outputs of all cars — the only path where objective scoring is meaningful**

### Cadillac V-Series.R 🟡 Partially calibrated
- Sequential solver only (no optimizer)
- Aero compression calibrated (18.5mm rear — very different from BMW's 9.5mm)
- Ride height model NOT calibrated (BMW coefficients used)
- m_eff partially calibrated
- Wing range: 12–17°
- **Outputs are directionally correct but not precision-calibrated**

### Ferrari 499P ⚠️ Use with significant caution

**Known structural issues (as of 2026-03-31):**
- **Ride heights will be wrong** — torsion bar turns control not yet output by solver
- **Rear spring values are 3.5× too high** — `rear_spring_range_nmm=(364.0, 590.0)` should be ~105–300 N/mm range
- **Damper values may map to wrong subsystem** — Ferrari has separate heave dampers (hidden) and corner dampers (visible); solver writes corner damper values only
- **DeflectionModel uses BMW coefficients** — deflection fields in generated .sto use wrong physics
- **Front diff clamps at 0 Nm** — range is −50 to +50 Nm per garage; solver doesn't allow negative preload
- Indexed heave spring decode is estimated, not validated
- ARB stiffnesses all ESTIMATE
- Wing range: 12–17°
- **Always compare output against a known-good Ferrari setup before race use**

### Acura ARX-06 ⚠️ Use with caution

**Known structural issues (as of 2026-03-31):**
- **Roll damper architecture not handled** — Acura has `FrontRoll`/`RearRoll` damper controls that the solver treats as regular per-corner dampers
- **m_eff is nonlinear** (319–641 kg across spring rate range) but solver uses fixed `front_m_eff_kg=450.0`
- **DeflectionModel is all zeros** — no deflection prediction at all for Acura
- Front heave damper is "always bottomed" (Acura characteristic) but solver may try to optimize it
- Aero compression uncalibrated
- Wing range: 6–10°
- **Check roll damper settings manually after generating setup**

### Porsche 963 ⚠️ Minimal calibration
- Sequential solver only
- Zero IBT sessions collected — everything is BMW placeholder
- No `GarageOutputModel`, no `DeflectionModel`, no heave calibration
- Wing range: 12–17°
- **Porsche outputs are physics-derived estimates only — treat as starting guess, not final setup**

---

## Legacy Commands to Avoid

These scripts still work but are deprecated — they duplicate `python -m ioptimal`:

```bash
# ❌ Don't use these:
python run_now.py                   # hardcoded BMW/Sebring, legacy
python run_full_pipeline.py         # hardcoded path, legacy
python run_full_v2.py               # hardcoded path, legacy
python run_full_justified.py        # hardcoded path, legacy
python run_tuned_search.py          # dev test script
python run_exhaustive.py            # grid search dump, not a setup generator
python -m analyzer --car bmw ...    # old entry point, use python -m ioptimal
```

---

## Understanding Output Quality by Car

```
BMW  @ Sebring    ████████████████████  Optimizer + objective + k-NN + calibration
BMW  @ Other      ████████████░░░░░░░░  Sequential solver, well-calibrated physics
Cadillac          ████████░░░░░░░░░░░░  Sequential, partial calibration
Ferrari           ████░░░░░░░░░░░░░░░░  Sequential, known structural issues
Acura             ████░░░░░░░░░░░░░░░░  Sequential, roll damper architecture mismatch
Porsche           ██░░░░░░░░░░░░░░░░░░  Placeholder estimates only
```

---

*See `ENGINEERING_AUDIT.md` for full architecture documentation, known issues, and fix plans.*  
*Source documents compared: `deep_audit_report_claw_research.md`, `AUDIT_REPORT.md`, `docs/codebase_audit_2026-03-31.md`*
