# IOptimal Calibration Guide
**How to calibrate per-car garage prediction models from IBT files**

---

## What Is Calibration?

iOptimal predicts iRacing's garage display values (ride heights, spring deflections, torsion bar turns, slider positions) using regression models fitted from YOUR telemetry. Each car needs its own calibration because iRacing's internal formulas differ per chassis.

**After calibration with sufficient data (25+ unique setups), the pipeline predicts most garage values to within 0.5mm of iRacing's display.** The accuracy depends on sample size and the car's physics complexity. This means:
- Ride height predictions match iRacing exactly
- Deflection values in saved .sto files load correctly in-game
- The solver can trust its RH targeting for pushrod reconciliation
- Aero compression uses your measured data (static RH - dynamic RH)

The system reads the `CarSetup` block embedded in every IBT file — the same data iRacing uses to display setup values in the garage.

---

## Current Calibration Status (2026-04-10, post-overfitting-fix)

| Car | Unique Setups | Features/Model | Training RMSE | LOO/Train Ratio | Status |
|-----|:---:|:---:|:---:|:---:|------|
| **BMW** | 9 | 0-3 | < 0.09mm | 1.0-3.5x | Calibrated, 6/6 steps (Sebring) |
| **Porsche** | 36 | 7-12 | < 0.5mm | 1.3-3.2x | Calibrated, 5/6 steps (Algarve) |
| **Ferrari** | 23 | 6-7 | 0.09-0.82mm | 1.1-1.7x | Partial, 1/6 steps (Hockenheim) |
| **Acura** | 8 | 0-3 | RH < 0.11mm | 1.0-2.7x | Partial, 3/6 steps (Hockenheim) |
| **Cadillac** | 0 | — | — | — | No data |

**Note on accuracy claims:** Previous claims of "< 0.06mm" for Ferrari and "< 0.07mm" for Porsche were based on overfit models (18 features on 23-36 samples, LOO/train ratios of 272-579x). The models memorized training data but did not generalize to new setups. After fixing the feature selection threshold to enforce a 3:1 sample-to-feature ratio, the models are honest about their accuracy. BMW's accuracy (< 0.09mm) was always reliable because its models used 0-3 features.

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
3. Forward feature selection (LOO RMSE) picks the best subset from the physics pool — **capped at `n_samples // 3` features** (3:1 ratio prevents overfitting). Selection is skipped only when `n_samples >= 3 * n_features`.
4. DirectRegression models are built — they evaluate directly from setup state, bypassing the rigid DeflectionModel interface for maximum accuracy
5. Defense-in-depth: models with LOO/train RMSE ratio > 10x are marked uncalibrated despite high training R²
6. Models are stored in `data/calibration/{car}/models.json`

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

**Target accuracy:** < 0.5mm RMSE on holdout IBTs for calibrated cars. BMW achieves < 0.09mm with 9 setups. Porsche achieves < 0.5mm on most outputs with 36 setups. More data improves accuracy — the feature selection enforces a 3:1 sample-to-feature ratio, so more setups unlock more features.

---

## How Many IBTs Do I Need?

| Unique Setups | Max Features (3:1 rule) | Expected Accuracy | Notes |
|:---:|:---:|:---:|-------|
| 5-8 | 1-2 | 1-3mm | Minimum for basic models. Constant or 1-feature fits only. |
| 9-15 | 3-5 | 0.5-1mm | Good accuracy. Enough to capture main physics terms. |
| 16-25 | 5-8 | 0.2-0.8mm | Good. Most compliance/nonlinear features accessible. |
| 25-36 | 8-12 | 0.1-0.5mm | Excellent. Most of the 20-feature physics pool available. |
| 54+ | 18 (all) | < 0.1mm | Full pool — no feature selection needed. |

"Unique setups" means setups with different spring rates, pushrods, or perches. Running the same setup 10 times counts as 1 unique setup. The 3:1 rule (`max_features = n_samples // 3`) prevents overfitting by limiting model complexity to what the data can support.

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
