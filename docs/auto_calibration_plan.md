# Auto-Calibration Pipeline: Perfect Per-Car Setup Calibration from IBT + STO Files

**Branch:** `claw-research` | **Date:** 2026-03-31

---

## Context

iOptimal currently only produces reliable setups for BMW at Sebring (72 observations, fully calibrated physics models). Ferrari has 9 observations, Cadillac 2, Acura 7, Porsche 0. Each car requires 7 physics models to be calibrated before the solver can produce correct output. Today, calibration is a painful manual process: taking garage screenshots, hand-measuring constants, hardcoding values into `cars.py`.

**The insight:** iRacing already knows all the physics values — they're embedded in the data files the user already has. Every IBT file contains the complete garage setup with computed display values (ride heights, corner weights, deflections). Every .sto file contains internal sim physics values (`fSideSpringRateNpm`, `rSideSpringRateNpm`). By collecting a handful of sessions with varied setups, we can reverse-engineer every physics model automatically.

**Goal:** Build a `python -m ioptimal calibrate` command that scans IBT + STO files and auto-calibrates all 7 physics models for any car, then plugs those models directly into the solver.

---

## What Data Lives Where

### IBT File (every session produces one)
```
Session Info YAML -> CarSetup block:
  |-- User-settable params: pushrod offsets, spring rates/indices, ARBs, dampers, wing, etc.
  |-- iRacing-computed displays: static RH, corner weights, deflections, torsion bar turns
  +-- AeroCalculator: FrontRhAtSpeed, RearRhAtSpeed, DF balance, L/D
Telemetry channels (60Hz):
  |-- Ride heights (LF/RF/LR/RR + splitter)
  |-- Shock velocities & deflections (heave + corner)
  |-- Dynamics: Speed, LatAccel, LongAccel, Yaw, Roll, Pitch
  +-- Tyres: temps, pressures, wear
```
**Already extracted by:** `analyzer/setup_reader.py` -> `CurrentSetup` (100+ params)
**Already extracted by:** `analyzer/extract.py` -> `MeasuredState` (150+ metrics)

### STO Binary File (every saved setup has one)
```
Full setup rows including UNMAPPED internal values:
  |-- fSideSpringRateNpm = 115170.265625  (front wheel rate in N/m -- GROUND TRUTH)
  |-- rSideSpringRateNpm = 105000          (rear wheel rate in N/m -- GROUND TRUTH)
  +-- All user-settable params + display values (same as IBT)
```
**Already decoded by:** `analyzer/sto_binary.py` -> `DecodedSto`
**NOT currently extracted:** `fSideSpringRateNpm` and `rSideSpringRateNpm` are in the rows but the adapter doesn't map them

### What This Means
- **IBT alone** can calibrate 5 of 7 models (RH, deflections, aero compression, m_eff, ARB stiffness) using computed display values as ground truth
- **IBT + STO** can calibrate ALL 7 models because STO provides the actual spring rates, making torsion bar C and heave spring mapping trivial
- **The user doesn't need garage screenshots** — the data is already in files they have

---

## The 7 Models and How to Calibrate Each

### Model 1: Torsion Bar C Constant (`k_wheel = C * OD^4`)
**Used by:** Step 3 corner spring solver
**Current state:** BMW C=0.0008036 (calibrated), Ferrari C=0.001282 (calibrated from 6 screenshots), Acura/Cadillac/Porsche use BMW default

| Data Source | Method | Accuracy |
|-------------|--------|----------|
| **STO (best)** | Read `fSideSpringRateNpm` at different OD values, fit `C = rate / OD^4` | Exact |
| **IBT (good)** | Read `torsion_bar_defl_mm` + `corner_weight_n`, compute `k = corner_weight / defl`, fit C | ~5% error (heave spring contaminates) |
| **IBT (better)** | Use 2+ sessions, vary ONLY torsion bar OD, difference eliminates heave contribution | ~2% error |

**Minimum data:** 3 distinct OD values (or torsion bar indices for Ferrari)
**Per-car notes:**
- BMW: continuous OD (13.9-18.2mm) -- already calibrated
- Ferrari: indexed (0-18) -- both front AND rear are torsion bars
- Cadillac: same Dallara platform as BMW, can share C constant initially
- Acura (ORECA): both front AND rear torsion bars, C currently borrowed from BMW
- Porsche (Multimatic): unknown platform, needs from-scratch calibration

### Model 2: Heave Spring Index Mapping
**Used by:** Step 2 heave solver
**Current state:** BMW/Cadillac use direct N/mm (no mapping needed). Ferrari uses indexed (0-8 front, 0-9 rear) with estimated 20 N/mm per index.

| Data Source | Method | Accuracy |
|-------------|--------|----------|
| **STO (best)** | Extract spring rates at each index from `fSideSpringRateNpm` (after separating heave from corner contribution) | Exact |
| **IBT (good)** | Read `heave_spring_defl_static_mm` + loads at different indices, compute `rate = load / defl` | ~10% error |

**Minimum data:** 3 distinct heave spring indices
**Only needed for:** Ferrari (and potentially Porsche if it uses indexed heave springs)

### Model 3: Aero Compression
**Used by:** Step 1 rake solver
**Current state:** BMW calibrated, Ferrari/Cadillac partial, Porsche/Acura estimated

| Data Source | Method | Accuracy |
|-------------|--------|----------|
| **IBT (direct)** | `compression = static_rh - rh_at_speed` from AeroCalculator in CarSetup | Exact (this IS iRacing's value) |

**Minimum data:** 1 IBT file (already in every session). 2+ with different wing angles improves model.
**This is the easiest model to calibrate -- data already extracted but not stored as calibration.**

### Model 4: Ride Height Regression
**Used by:** Step 1 rake solver, garage validator
**Current state:** BMW fully calibrated (31 configs, R^2=0.52 rear). Others use simple PushrodGeometry model.

| Data Source | Method | Accuracy |
|-------------|--------|----------|
| **IBT (direct)** | Multi-variable OLS: setup params -> iRacing static RH display values | R^2>0.5 with 6+ configs |

**Minimum data:** 6 unique setup configurations
**Pattern to follow:** `car_model/calibrate_deflections.py` already does exactly this for BMW

### Model 5: Deflection Model (Garage Display Prediction)
**Used by:** Garage validator, constraint checking
**Current state:** BMW calibrated (R^2=0.83-0.95 across 16 outputs). Others completely uncalibrated.

| Data Source | Method | Accuracy |
|-------------|--------|----------|
| **IBT (direct)** | Multi-variable OLS: setup params -> each deflection output | R^2>0.80 with 8+ configs |

**Minimum data:** 8 unique setup configurations
**Pattern to follow:** `car_model/calibrate_deflections.py` lines 23-120

### Model 6: Effective Mass (m_eff)
**Used by:** Step 2 heave solver
**Current state:** BMW front=450kg/rear=550kg, Acura varies 319-641kg with spring rate

| Data Source | Method | Accuracy |
|-------------|--------|----------|
| **IBT telemetry** | FFT of ride height -> dominant frequency -> `m_eff = k / (2*pi*f)^2` | ~15% (track-dependent) |
| **IBT multi-session** | Vary heave spring rate, measure frequency change, fit m_eff | ~5% with 3+ rates |

**Minimum data:** 3 sessions with different heave spring rates
**Key insight:** m_eff may be rate-dependent (Acura shows 2x variation). Fit `m_eff(k) = a/k + b` instead of scalar.

### Model 7: ARB Roll Stiffness
**Used by:** Step 4 ARB solver
**Current state:** All cars use estimated stiffness per size label

| Data Source | Method | Accuracy |
|-------------|--------|----------|
| **IBT telemetry** | Roll gradient (deg/g) at different ARB configs -> back-solve ARB stiffness | ~10% (requires controlled experiments) |

**Minimum data:** 4 sessions with at least 2 different ARB size/blade combos
**This is the weakest calibration -- many confounding variables. Best with single-variable sweeps.**

---

## Implementation Plan

### New Module: `calibration/`

```
calibration/
    __init__.py
    __main__.py                # CLI: python -m ioptimal calibrate --car ferrari --ibt-dir ./
    extract.py                 # CalibrationData from IBT + optional STO
    fit_torsion_bar.py         # Model 1
    fit_heave_spring.py        # Model 2
    fit_aero_compression.py    # Model 3
    fit_ride_height.py         # Model 4 (port from car_model/calibrate_deflections.py)
    fit_deflection.py          # Model 5 (port from car_model/calibrate_deflections.py)
    fit_m_eff.py               # Model 6
    fit_arb.py                 # Model 7
    apply.py                   # Write calibrated params -> cars.py JSON / dataclass
    protocol.py                # Generate per-car calibration sweep instructions
    report.py                  # Human-readable calibration report
```

### Step 1: `calibration/extract.py` -- CalibrationData Extractor

```python
@dataclass
class CalibrationData:
    car_name: str
    track_name: str
    source_path: str
    setup: CurrentSetup                    # From IBT or STO
    measured: MeasuredState | None         # From IBT telemetry (None if STO-only)
    # Internal sim values (STO-only, None from IBT)
    f_side_spring_rate_npm: float | None   # Front wheel rate N/m
    r_side_spring_rate_npm: float | None   # Rear wheel rate N/m
    setup_hash: str                        # For deduplication
```

**Implementation:**
1. Scan directory for `*.ibt` and `*.sto` files
2. For each IBT: call `CurrentSetup.from_ibt()` + `extract_measured_state()` -> CalibrationData
3. For each STO: call `CurrentSetup.from_sto()` + extract `fSideSpringRateNpm`/`rSideSpringRateNpm` from unmapped rows -> CalibrationData
4. Deduplicate by setup_hash (same setup -> keep the one with more data, prefer IBT+STO over IBT-only)
5. Return `list[CalibrationData]` sorted by unique configs

**Key change to existing code:** Add `f_side_spring_rate_npm` extraction to `sto_adapters.py`:
```python
# In build_current_setup_fields() or a new function:
for row in decoded.rows:
    if row.label == "fSideSpringRateNpm":
        extra["f_side_spring_rate_npm"] = float(row.metric_value)
    if row.label == "rSideSpringRateNpm":
        extra["r_side_spring_rate_npm"] = float(row.metric_value)
```

### Step 2: Individual Fitters (Models 1-7)

Each fitter follows the same interface:
```python
@dataclass
class FitResult:
    model_name: str
    is_calibrated: bool
    sample_count: int
    r_squared: float | None  # For regression models
    parameters: dict          # Model-specific coefficients
    warnings: list[str]

def fit(data: list[CalibrationData], car: CarModel) -> FitResult:
    ...
```

**Model 1 (`fit_torsion_bar.py`):**
- Group CalibrationData by unique torsion bar OD/index
- If STO data available: `C = mean(rate_npm / (1000 * OD_mm^4))` for each group -> average
- If IBT only: `k_effective = corner_weight_n / torsion_defl_mm`, then `C = k_effective / OD^4`
- Fit separately for front and rear (Ferrari has both)
- Output: `CornerSpringModel(front_torsion_c=..., rear_torsion_c=...)`

**Model 3 (`fit_aero_compression.py`):**
- For each CalibrationData: `front_comp = static_front_rh - front_rh_at_speed`
- Group by wing angle
- Fit linear: `compression(wing) = base + slope * wing`
- Output: `AeroCompression(ref_speed_kph=230, front_compression_mm=..., rear_compression_mm=...)`

**Models 4 & 5 (`fit_ride_height.py`, `fit_deflection.py`):**
- Port logic directly from `car_model/calibrate_deflections.py`
- Build feature matrix from setup params -> OLS fit against display values
- Output: `RideHeightModel(...)` and `DeflectionModel(...)`

### Step 3: `calibration/__main__.py` -- CLI Orchestrator

```bash
# Full auto-calibration
python -m ioptimal calibrate --car ferrari --ibt-dir ./sessions/ --sto-dir ./setups/

# Dry run (show what would be calibrated, report gaps)
python -m ioptimal calibrate --car ferrari --ibt-dir ./sessions/ --dry-run

# Only fit specific models
python -m ioptimal calibrate --car ferrari --ibt-dir ./sessions/ --models torsion_bar,aero

# Generate calibration sweep instructions
python -m ioptimal calibrate --car ferrari --protocol

# Apply calibrated params
python -m ioptimal calibrate --car ferrari --apply data/calibration/ferrari.json
```

**Flow:**
1. Extract all CalibrationData from IBT + STO files
2. Check minimum data requirements per model (report gaps)
3. Run each fitter, collect FitResults
4. Generate calibration report (R^2, sample counts, confidence)
5. Write calibrated params to `data/calibration/{car}_calibrated.json`
6. Optionally auto-apply to `car_model/cars.py` runtime

### Step 4: `calibration/apply.py` -- Apply to Solver (Both JSON + Code)

Output format: **Both** JSON for runtime loading and generated Python code for review.

```python
def save_calibration(car_name: str, results: dict[str, FitResult]):
    """Write calibration to JSON + generate Python code snippet."""
    # 1. JSON for runtime loading
    json_path = f"data/calibration/{car_name}_calibrated.json"
    write_json(json_path, {name: r.to_dict() for name, r in results.items()})

    # 2. Python code snippet for cars.py (review + git versioning)
    code_path = f"data/calibration/{car_name}_cars_py_snippet.py"
    write_code_snippet(code_path, car_name, results)
    # Prints: "# Paste into cars.py Ferrari 499P definition:"
    # Prints: "corner_spring=CornerSpringModel(front_torsion_c=0.001282, ...)"

def apply_calibration(car: CarModel, car_name: str) -> CarModel:
    """Load calibration JSON and update car model at runtime."""
    json_path = f"data/calibration/{car_name}_calibrated.json"
    calibration = load_json(json_path)
    if "torsion_bar" in calibration:
        car.corner_spring.front_torsion_c = calibration["torsion_bar"]["front_c"]
    if "aero_compression" in calibration:
        car.aero_compression = AeroCompression(**calibration["aero_compression"])
    if "deflection" in calibration:
        car.deflection_model = DeflectionModel(**calibration["deflection"])
        car.garage_output_model = auto_generate_garage_output_model(car)  # Enables validation!
    ...
    return car
```

**Critical integration:** Once Models 4+5 are calibrated, auto-generate `GarageOutputModel` for the car -- this enables the full garage validation path that currently only works for BMW.

### Step 5: `calibration/protocol.py` -- Sweep Instructions

Generates per-car calibration instructions based on what's missing:

```
=====================================================
Ferrari 499P Calibration Protocol
=====================================================

Current status:
  [OK] Aero compression: CALIBRATED (9 sessions)
  [!!] Torsion bar C (front): needs 2 more OD values
  [!!] Torsion bar C (rear): needs 3 more OD values
  [!!] Heave spring mapping: needs 3 index values
  [!!] Ride height model: needs 4 more configs
  [!!] Deflection model: needs 6 more configs
  [~~] m_eff: ESTIMATED (need 2 more varied spring rates)
  [~~] ARB stiffness: ESTIMATED (need 3 ARB configs)

Quick sweep (~30 min in-sim):
  Step 1: Load practice session at any track
  Step 2: Apply your current setup
  Step 3: Do 3 out-laps, save IBT
  Step 4: ONLY change front torsion bar to index 3, do 3 laps, save IBT
  Step 5: ONLY change front torsion bar to index 12, do 3 laps, save IBT
  Step 6: ONLY change front torsion bar to index 18, do 3 laps, save IBT
  Step 7: Reset to baseline. ONLY change front heave spring to index 0, 3 laps
  Step 8: ONLY change front heave spring to index 8, 3 laps

  After: run `python -m ioptimal calibrate --car ferrari --ibt-dir ./`
  Expected result: torsion C + heave mapping + RH model calibrated

Full sweep (adds deflection model, ~1 hour):
  Steps 1-8 above, plus:
  Step 9: Change heave perch to -20mm, 3 laps
  Step 10: Change rear pushrod offset +5mm, 3 laps
  Step 11: Change rear spring rate to max, 3 laps

  After: run `python -m ioptimal calibrate --car ferrari --ibt-dir ./`
  Expected: ALL 7 models calibrated
```

**Key UX point:** Users can also skip the sweep entirely and just point the calibrator at ALL their existing IBT files from normal racing. If they've naturally varied their setup across sessions, the calibrator will find enough diversity to calibrate most models.

---

## Files Modified

| File | Change | Why |
|------|--------|-----|
| `calibration/__init__.py` | New | Module init |
| `calibration/__main__.py` | New | CLI entry point |
| `calibration/extract.py` | New | CalibrationData extractor |
| `calibration/fit_torsion_bar.py` | New | Model 1 fitter |
| `calibration/fit_heave_spring.py` | New | Model 2 fitter |
| `calibration/fit_aero_compression.py` | New | Model 3 fitter |
| `calibration/fit_ride_height.py` | New | Model 4 fitter (port from calibrate_deflections.py) |
| `calibration/fit_deflection.py` | New | Model 5 fitter (port from calibrate_deflections.py) |
| `calibration/fit_m_eff.py` | New | Model 6 fitter |
| `calibration/fit_arb.py` | New | Model 7 fitter |
| `calibration/apply.py` | New | Apply results to CarModel |
| `calibration/protocol.py` | New | Generate sweep instructions |
| `calibration/report.py` | New | Human-readable report |
| `analyzer/sto_adapters.py` | Modify | Extract fSideSpringRateNpm from STO rows |
| `__main__.py` | Modify | Add `calibrate` subcommand |
| `car_model/cars.py` | Modify | Add `CalibrationStatus` dataclass, load from JSON |
| `pipeline/produce.py` | Modify | Load calibration at runtime if available |

---

## User Workflow Summary

### Fast Path (5 min, data you already have)
```bash
# Point at your existing IBT files (primary data source)
python -m ioptimal calibrate --car ferrari --ibt-dir ~/Documents/iRacing/telemetry/

# See what's calibrated and what's missing
python -m ioptimal calibrate --car ferrari --ibt-dir ~/Documents/iRacing/telemetry/ --dry-run
```

### If you also have .sto files (optional, enhances accuracy)
```bash
# STO files give ground truth spring rates (fSideSpringRateNpm)
# Most users won't have these -- IBT-only path works fine
python -m ioptimal calibrate --car ferrari \
  --ibt-dir ~/Documents/iRacing/telemetry/ \
  --sto-dir ~/Documents/iRacing/setups/ferrari499p/
```

> **Note:** You currently have IBT files only for non-BMW cars. The IBT-only calibration
> path uses deflection + corner weight data to derive spring rates. This is accurate to
> ~2-5% which is more than sufficient. The ferrari.json at project root provides STO-level
> data for one Ferrari config as a validation anchor.

### If gaps remain, generate a sweep protocol
```bash
# Get instructions for what to do in iRacing
python -m ioptimal calibrate --car ferrari --protocol
```

### Then in iRacing
1. Load a practice session (any track works)
2. Follow the protocol: change one parameter at a time, do 3 laps each
3. Save IBTs after each run
4. ~30 minutes for basic calibration, ~1 hour for full

### Apply and use
```bash
# Apply calibration
python -m ioptimal calibrate --car ferrari --ibt-dir ./ --auto-apply

# Now the solver uses calibrated models
python -m ioptimal produce --car ferrari --ibt session.ibt --wing 17
```

---

## Per-Car Calibration Priority

| Car | Platform | IBT Files Available | STO Files? | Est. Time to Calibrate | Priority |
|-----|----------|--------------------|-----------|-----------------------|----------|
| **Ferrari** | Bespoke LMH | 9 sessions + Hockenheim | ferrari.json has STO data | 30 min sweep | **HIGH** |
| **Cadillac** | Dallara (shared w/ BMW) | 2 sessions | Unknown | 30 min sweep (share BMW C) | **HIGH** |
| **Acura** | ORECA | 7 sessions | Unknown | 45 min sweep | **MEDIUM** |
| **Porsche** | Multimatic | 0 sessions | Unknown | 1 hour sweep (everything from scratch) | **LOW** |
| **BMW** | Dallara | 72 sessions | Yes | Already calibrated (validate only) | Validation |

---

## Verification Plan

### Self-Validation (BMW)
1. Remove BMW calibration from cars.py
2. Run `python -m ioptimal calibrate --car bmw --ibt-dir ./ibtfiles/`
3. Compare auto-calibrated C constant against known 0.0008036 (should be within 1%)
4. Compare auto-calibrated RH model against known coefficients
5. Compare auto-calibrated deflection model R^2 against known 0.83-0.95

### Cross-Validation (Ferrari)
1. Run calibration with existing 9 IBTs + ferrari.json STO data
2. Compare auto-calibrated C against manually derived 0.001282 (should match within 2%)
3. Run solver with calibrated params: `python -m ioptimal produce --car ferrari --ibt session.ibt --wing 17`
4. Check output .sto values are in legal garage ranges
5. Check deflection predictions match IBT display values

### Integration Test
```bash
python -m pytest tests/test_auto_calibration.py -v
```
- Test extraction from fixture IBTs
- Test each fitter with known-good data
- Test apply pipeline produces valid CarModel
- Test round-trip: calibrate -> apply -> solve -> verify constraints
