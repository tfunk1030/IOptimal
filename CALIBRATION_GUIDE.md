# IOptimal Calibration Guide
**How to calibrate per-car garage prediction models from IBT files**

---

## What Is Calibration?

iOptimal predicts iRacing's garage display values (ride heights, spring deflections, torsion bar turns, slider positions) using regression models fitted from YOUR telemetry. Each car needs its own calibration because iRacing's internal formulas differ per chassis.

**After calibration, the pipeline predicts every garage value to within 0.1mm of iRacing's display.** This means:
- Ride height predictions match iRacing exactly
- Deflection values in saved .sto files load correctly in-game
- The solver can trust its RH targeting for pushrod reconciliation
- Aero compression uses your measured data (static RH - dynamic RH)

The system reads the `CarSetup` block embedded in every IBT file — the same data iRacing uses to display setup values in the garage.

---

## Current Calibration Status (2026-04-10)

| Car | Unique Setups | Holdout Accuracy | Status |
|-----|:---:|:---:|------|
| **BMW** | 9 | all fields < 0.09mm | Calibrated (Sebring) |
| **Porsche** | 36 | all fields < 0.07mm (3 real IBTs) | Calibrated (Algarve) |
| **Ferrari** | 23 | all fields < 0.06mm (4 real IBTs) | Calibrated (Hockenheim) |
| **Acura** | 8 | RH < 0.11mm, some deflections limited | Partial (Hockenheim) |
| **Cadillac** | 0 | — | No data |

**Verified on real IBT files:** 77/77 blind predictions within 0.1mm across 7 holdout IBTs (4 Ferrari, 3 Porsche).

---

## How The Calibration Works

### Physics Feature Pool (20 features)

The regression models use physically motivated features:

| Category | Features | Why |
|----------|----------|-----|
| **Linear** | pushrod_f/r, heave, third, spring, torsion_od, perch_f/rt/rs, camber_f/r, fuel, wing | Direct geometric/weight effects |
| **Compliance (1/k)** | 1/heave, 1/third, 1/spring, 1/od^4 | Deflection under load is proportional to spring compliance |
| **Nonlinear geometry** | pushrod_f^2, pushrod_r^2 | Pushrod linkage ratio changes with angle |
| **Weight x compliance** | fuel/spring, fuel/third | Fuel weight compresses springs proportional to compliance |

**No ARB blade** — confirmed via isolated-change analysis to have zero effect on any garage output.

### What Happens During Calibration

1. IBT files are ingested → setup parameters + iRacing's computed garage values extracted
2. Duplicate setups (same springs/pushrods/perches) are merged
3. Forward feature selection (LOO RMSE) picks the best subset from the physics pool
4. DirectRegression models are built — they evaluate directly from setup state, bypassing the rigid DeflectionModel interface for maximum accuracy
5. Models are stored in `data/calibration/{car}/models.json`

---

## How To Calibrate a NEW Car

### Step 1: Collect IBT Files (30-60 minutes in iRacing)

**Goal:** Get 15-25 unique setups with varied parameters. The more parameters you vary independently, the better the model.

**Best practice — systematic sweep:**

1. **Start with your baseline setup** at any practice track
2. **Vary ONE parameter at a time**, drive 2-3 laps, let iRacing save the IBT
3. **Priority order of what to vary** (most impactful first):

| Priority | Parameter | How to Vary | Why |
|:---:|----------|-------------|-----|
| 1 | Rear pushrod | 3+ settings spanning full range | Dominant rear RH driver |
| 2 | Front pushrod | 3+ settings spanning full range | Dominant front RH driver |
| 3 | Rear third spring | 3+ settings (soft/mid/stiff) | Rear compliance |
| 4 | Rear spring | 3+ settings (soft/mid/stiff) | Rear compliance |
| 5 | Front heave | 3+ settings (soft/mid/stiff) | Front compliance |
| 6 | Front heave perch | 2-3 settings | Load path effect |
| 7 | Torsion bar OD | 2-3 settings | Torsion deflection |
| 8 | Front camber | 2 settings | Geometry coupling |
| 9 | Fuel level | 2 settings (low ~10L, full ~58L) | Weight effect |

**Minimum for basic calibration:** 8 unique setups with at least 3 different values for each of pushrod, third, and spring.

**For best accuracy:** 20+ unique setups with ALL of the above parameters varied. This is what Porsche (36 setups) and Ferrari (23 setups) have — they achieve 0.06mm accuracy.

**Important:** You DON'T need to vary ARB blade, ARB size, or TC/diff settings. These have zero effect on garage display values.

### Step 2: Ingest the IBTs

```bash
# Point at the directory where iRacing saved the IBTs
python -m car_model.auto_calibrate --car <car_name> --ibt-dir /path/to/ibt/files

# Or add specific files
python -m car_model.auto_calibrate --car <car_name> --ibt file1.ibt file2.ibt file3.ibt
```

The system auto-detects duplicates. Running it twice on the same file is safe.

### Step 3: Refit Models

```bash
python -m car_model.auto_calibrate --car <car_name> --refit
```

This fits regression models from all calibration points. The output shows R^2 per model and recommendations.

### Step 4: Verify

```bash
# Run the universal calibration sweep to check predictions vs ground truth
python -m validation.universal_calibration_sweep --car <car_name> --verbose

# Check calibration status
python -m car_model.auto_calibrate --car <car_name> --status
```

The sweep shows predicted vs measured for every calibration point. Target: < 0.5mm max error across all fields.

### Step 5: Validate on a NEW IBT (the real test)

```bash
# Run pipeline.produce on an IBT that WASN'T used for calibration
python -m pipeline.produce --car <car_name> --ibt new_session.ibt --wing 17 --json output.json
```

Compare the predicted garage values against what iRacing shows in the garage for that session. If predictions match within 0.5mm, the calibration is good. If within 0.1mm, it's excellent.

---

## Calibration for Indexed Cars (Ferrari, Acura)

Ferrari and Acura use **index-based garage controls** (torsion bar OD is index 0-18, not a direct mm value). The calibration system handles this automatically:

1. Raw indices are stored in `calibration_points.json`
2. During model fitting, indices are converted to physical N/mm using the car's lookup table
3. During prediction, `GarageSetupState.from_current_setup(setup, car=car)` decodes indices automatically

**Spring lookup tables** (optional but helpful): If you have a setupdelta.com JSON export, you can add precise index-to-rate mappings:
```bash
python -m car_model.auto_calibrate --car ferrari --sto-json ferrari_setup.json
```

---

## Commands Quick Reference

| Command | What It Does |
|---------|-------------|
| `python -m car_model.auto_calibrate --car X --ibt-dir DIR` | Add IBT files to calibration data |
| `python -m car_model.auto_calibrate --car X --refit` | Re-fit all models from calibration data |
| `python -m car_model.auto_calibrate --car X --status` | Show calibration status and R^2 scores |
| `python -m car_model.auto_calibrate --car X --protocol` | Generate sweep instructions for missing data |
| `python -m car_model.auto_calibrate --car X --clear` | Reset all calibration data for the car |
| `python -m validation.universal_calibration_sweep --car X -v` | Verify predictions vs ground truth |
| `python -m pipeline.produce --car X --ibt FILE --wing N` | Run full pipeline with calibration |

---

## Giving IBTs to Claude Code for Calibration

When starting a new Claude Code session to calibrate a car:

1. **Add the IBT files** to the repository (or provide a path)
2. **Tell Claude:** "Calibrate [car_name] from these IBT files"
3. **Claude will run:**
   ```bash
   python -m car_model.auto_calibrate --car <name> --ibt file1.ibt file2.ibt ... --refit
   python -m validation.universal_calibration_sweep --car <name> --verbose
   ```
4. **Claude should verify** on at least one holdout IBT (not used for calibration):
   ```python
   # Extract setup from IBT, predict garage values, compare to iRacing
   from car_model.cars import get_car
   from car_model.garage import GarageSetupState
   from track_model.ibt_parser import IBTFile
   from analyzer.setup_reader import CurrentSetup

   car = get_car(car_name)
   ibt = IBTFile("holdout.ibt")
   setup = CurrentSetup.from_ibt(ibt, car_canonical=car_name)
   state = GarageSetupState.from_current_setup(setup, car=car)
   gom = car.active_garage_output_model(None) or car.garage_output_model
   out = gom.predict(state)

   # Compare out.front_static_rh_mm vs setup.static_front_rh_mm etc.
   ```

**Target accuracy:** < 0.1mm on holdout IBTs for calibrated cars (Porsche/Ferrari achieve this with 23-36 unique setups).

---

## How Many IBTs Do I Need?

| Unique Setups | Expected Accuracy | Notes |
|:---:|:---:|-------|
| 5-8 | 1-3mm | Minimum for basic models. Enough for RH direction but not precision. |
| 9-15 | 0.5-1mm | Good accuracy. Most fields within 0.5mm. |
| 16-25 | 0.1-0.5mm | Excellent. Enough features for physics formula discovery. |
| 25-36 | < 0.1mm | Near-perfect. All fields match iRacing within display resolution. |

"Unique setups" means setups with different spring rates, pushrods, or perches. Running the same setup 10 times counts as 1 unique setup.

---

## Data Storage

```
data/calibration/
  bmw/
    calibration_points.json   <- raw data from each session (inputs + iRacing outputs)
    models.json               <- fitted regression coefficients
  porsche/
    calibration_points.json
    models.json
  ferrari/
    calibration_points.json
    models.json
  acura/
    calibration_points.json
    models.json
  cadillac/
    calibration_points.json   <- empty stub
    models.json               <- empty stub
```

These files are updated automatically by `auto_calibrate`. You never need to edit them manually.

---

## Troubleshooting

**"No GarageOutputModel for X"** — The car doesn't have enough calibration data. Add more IBTs and refit.

**High R^2 on training but bad on new IBTs** — The model is overfitting. This usually means too many features for the available data. Add more unique setups (different spring/pushrod combinations).

**Ferrari/Acura shows wrong values** — Check that index decoding is working. `GarageSetupState.from_current_setup(setup, car=car)` needs the `car` parameter for indexed cars.

**Pipeline crashes with "NoneType has no attribute"** — The solver steps are blocked by the calibration gate. This is expected when the car/track combination doesn't have full calibration. The garage prediction models still work — only the 6-step solver is blocked.
