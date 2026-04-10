# Universal Calibration Sweep — Claude Code Mission Brief

## What This Is

A self-contained prompt for Claude Code to systematically validate and fix the solver/pipeline's ride height and deflection predictions against EVERY known calibration data point for EVERY car.

---

## The Prompt

Copy everything below the line into a new Claude Code session with this repo open.

---

```
You are working on the IOptimal GTP setup solver. Your mission is to make the solver's ride height and deflection predictions match iRacing's ground-truth values for EVERY calibration data point across ALL cars — not just the handful of setups that happen to work today.

Read CLAUDE.md first for full context. Then execute the following systematic process.

## PHASE 1: Build the Universal Validation Harness

### Step 1.1: Understand the data

Read these files to understand what you're working with:
- data/calibration/bmw/calibration_points.json (12 points, 9 unique setups)
- data/calibration/porsche/calibration_points.json (60 points, 36 unique setups)  
- data/calibration/ferrari/calibration_points.json (65 points, 29 unique setups)
- data/calibration/acura/calibration_points.json (15 points, 8 unique setups)
- data/calibration/cadillac/calibration_points.json (stub — 0 points)

Each calibration point contains:
- INPUTS: front_heave_setting, rear_third_setting, front_torsion_od_mm, rear_spring_setting, front_pushrod_mm, rear_pushrod_mm, front_heave_perch_mm, rear_third_perch_mm, rear_spring_perch_mm, front_camber_deg, rear_camber_deg, fuel_l, wing_deg
- MEASURED OUTPUTS (ground truth from iRacing): static_front_rh_mm, static_rear_rh_mm, heave_spring_defl_static_mm, heave_spring_defl_max_mm, rear_spring_defl_static_mm, third_spring_defl_static_mm, third_spring_defl_max_mm, front_shock_defl_static_mm, rear_shock_defl_static_mm, torsion_bar_turns, torsion_bar_defl_mm, heave_slider_defl_static_mm, third_slider_defl_static_mm

### Step 1.2: Build the test script

Create `validation/universal_calibration_sweep.py` that does this for EACH car that has calibration data (bmw, porsche, ferrari, acura):

```python
"""Universal calibration sweep: predict vs measure for every known setup."""

import json
import sys
from pathlib import Path
from dataclasses import dataclass

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from car_model.cars import get_car
from car_model.auto_calibrate import (
    load_calibration_points,
    load_calibrated_models,
    CalibrationPoint,
)

# For each car with calibration data:
CARS_TO_VALIDATE = ["bmw", "porsche", "ferrari", "acura"]

# The fields we predict and their ground-truth counterparts
PREDICTION_TARGETS = [
    # (prediction_method, measured_field, label, tolerance_mm)
    ("predict_front_static_rh", "static_front_rh_mm", "Front Static RH", 0.5),
    ("predict_rear_static_rh", "static_rear_rh_mm", "Rear Static RH", 1.0),
    ("predict_heave_defl_static", "heave_spring_defl_static_mm", "Heave Defl Static", 1.0),
    ("predict_rear_spring_defl_static", "rear_spring_defl_static_mm", "Rear Spring Defl", 1.0),
    ("predict_third_spring_defl_static", "third_spring_defl_static_mm", "Third Defl Static", 1.0),
    ("predict_front_shock_defl", "front_shock_defl_static_mm", "Front Shock Defl", 1.0),
    ("predict_rear_shock_defl", "rear_shock_defl_static_mm", "Rear Shock Defl", 1.0),
    ("predict_torsion_turns", "torsion_bar_turns", "Torsion Turns", 0.05),
    ("predict_torsion_defl", "torsion_bar_defl_mm", "Torsion Defl", 0.5),
    ("predict_heave_slider_defl", "heave_slider_defl_static_mm", "Heave Slider", 1.0),
]
```

For each calibration point:
1. Load the car model via `get_car(car_name)` 
2. Apply calibration data via `load_calibrated_models()` + `apply_to_car()`
3. Construct a `GarageSetupState` from the calibration point's input fields
4. Call every prediction method on the car's `GarageOutputModel` (or `RideHeightModel` / `DeflectionModel`)
5. Compare predicted value vs the measured ground-truth value from the calibration point
6. Record: car, session_id, field, predicted, measured, error_mm, error_pct

IMPORTANT: For indexed cars (Ferrari, Acura), the `front_heave_setting` and `rear_third_setting` fields are RAW INDICES, not N/mm. You must use the car's spring lookup tables to convert index → N/mm before calling prediction methods. Check `car_model/auto_calibrate.py:_decode_spring_rate()` and the `FerrariIndexedControlModel` / spring lookup tables in `cars.py`.

### Step 1.3: Run the sweep and analyze

Run the script. For each car, produce a table like:

```
=== BMW (12 points) ===
Field                  | Mean Error | Max Error | R²     | Points w/ >1mm error
-----------------------|-----------|-----------|--------|--------------------
Front Static RH        |  0.12 mm  |  0.34 mm  | 0.998  | 0/12
Rear Static RH         |  0.89 mm  |  2.31 mm  | 0.91   | 3/12
Heave Defl Static      |  1.23 mm  |  3.45 mm  | 0.87   | 5/12   ← PROBLEM
...
```

Also produce a per-point detail dump showing the WORST mismatches:
```
Point bmw_session_abc123:
  Rear Static RH: predicted=49.2, measured=51.5, error=+2.3mm
  Setup: heave=50, third=530, pushrod_r=-29, perch_r=41, spring=180
  → Diagnosis: pushrod coefficient too weak? Third compliance missing?
```

## PHASE 2: Diagnose Systematic Errors

After Phase 1, you will have a complete error map. Now diagnose:

### Step 2.1: Per-car error patterns

For each car, answer:
- Which prediction targets have mean error > 0.5mm (RH) or > 1mm (deflections)?
- Is the error correlated with a specific input variable? (e.g., "rear RH error grows with 1/third_rate" → missing compliance term)
- Is the error a constant bias (intercept wrong) or a slope error (coefficient wrong)?
- Are there outlier points that are dramatically wrong while others are fine?

### Step 2.2: Cross-car comparison

- Do BMW and Porsche (both calibrated) show the same error patterns?
- Does Ferrari's indexed control system introduce systematic prediction errors?
- Are there features that the model uses for one car but ignores for another?

### Step 2.3: Root cause identification

For each significant error, determine the root cause:
1. **Missing feature**: The regression model doesn't include a feature that affects the output (e.g., rear_spring_perch not in the rear RH model)
2. **Wrong functional form**: Using linear k when compliance 1/k is correct (or vice versa)
3. **Coefficient drift**: The fitted coefficient is wrong because the training data was biased
4. **Index decoding error**: Ferrari/Acura index → N/mm conversion is wrong
5. **Cross-talk**: A variable affects an output that the model doesn't account for (e.g., fuel affects torsion deflection through weight)
6. **Motion ratio error**: The MR used to convert spring rate → wheel rate is wrong
7. **Prediction pipeline bug**: The code that calls the model passes wrong inputs

## PHASE 3: Fix the Models and Code

For each root cause identified in Phase 2, fix it. The fixes fall into categories:

### 3A: Regression model improvements (auto_calibrate.py)

If a feature is missing from the regression:
1. Add the feature to the candidate list in `fit_models_from_points()` 
2. Re-fit the model by running: `python -m car_model.auto_calibrate --car {car} --refit`
3. Check if R² and LOO_RMSE improved
4. Update `apply_to_car()` to map the new coefficient to the correct `RideHeightModel` / `DeflectionModel` field

If the functional form is wrong:
1. Switch between linear and compliance terms (e.g., replace `heave_nmm` with `1/heave_nmm`)
2. Re-fit and verify improvement

### 3B: GarageOutputModel prediction code (garage_model.py)

If the prediction code doesn't use all calibrated coefficients:
1. Add the missing terms to `predict_front_static_rh_raw()`, `predict_rear_static_rh_raw()`, or the deflection methods
2. Ensure the `GarageSetupState` dataclass carries all needed inputs
3. Update `build_garage_output_model()` in auto_calibrate.py to populate the new fields

### 3C: Car definition coefficients (cars.py)

If a car's RideHeightModel or DeflectionModel has wrong coefficients:
1. Update the coefficients in the car definition
2. Ensure `apply_to_car()` correctly maps from `models.json` to the car's model fields
3. If the car uses indexed controls, verify the lookup table is correct

### 3D: Index decoding (auto_calibrate.py, cars.py)

For Ferrari and Acura:
1. Verify every index → N/mm conversion against known garage data
2. Check that `_decode_spring_rate()` handles all edge cases
3. Ensure the lookup tables in `cars.py` match the actual iRacing garage values

### 3E: Solver pipeline fixes (rake_solver.py, heave_solver.py, etc.)

If the solver passes wrong values to the prediction models:
1. Trace the data flow from solver step → GarageSetupState → prediction
2. Fix any unit conversions, motion ratio applications, or field name mismatches
3. Verify that `reconcile_ride_heights()` uses the correct model

## PHASE 4: Validate the Fixes

After each fix:
1. Re-run the universal sweep script
2. Verify the fix improved the target metric WITHOUT regressing others
3. Update the regression baselines if the fix intentionally changes outputs:
   - Run: `python -m pytest tests/test_setup_regression.py` 
   - If baselines need updating, regenerate them per the test file's docstring

After ALL fixes:
1. Run the full test suite: `python -m pytest tests/ -q`
2. Verify no new test failures beyond pre-existing ones
3. Produce a final sweep report showing before/after for every car × every prediction target

## PHASE 5: Commit and Document

Commit with a message summarizing:
- How many prediction targets were fixed
- Per-car improvement in mean error and R²
- Any regression model refits that were done
- Any new features added to the regression

## CRITICAL RULES

1. **Never fake a fix.** If a prediction doesn't match, find and fix the root cause. Don't add fudge factors or hard-code corrections for specific data points.

2. **Physics over curve-fitting.** If a feature has a physical justification (e.g., compliance = 1/k under load), prefer the physics-correct form even if the empirical R² is slightly worse.

3. **All points, not just some.** The goal is for EVERY calibration point to predict within tolerance, not just the mean. One outlier with 5mm error means the model is wrong for that region of the setup space.

4. **Preserve cross-car consistency.** A fix for Porsche should not break BMW. The GarageOutputModel, RideHeightModel, and DeflectionModel classes serve all cars — changes must be backward-compatible.

5. **Refit, don't hand-tune.** If a regression model needs new coefficients, refit it from the calibration data using `auto_calibrate.py`. Don't manually edit models.json.

6. **Index → N/mm is sacred.** For Ferrari and Acura, the index-to-rate conversion must be verified against garage screenshots. If in doubt, document the uncertainty rather than guessing.

7. **Track the error budget.** After Phase 4, produce a final error budget:
   - Front static RH: target ≤0.5mm mean, ≤1.0mm max for calibrated cars
   - Rear static RH: target ≤1.0mm mean, ≤2.0mm max
   - Deflections: target ≤1.0mm mean, ≤2.0mm max
   - Torsion turns: target ≤0.05 turns mean
   
   If a car can't meet these targets, document WHY (e.g., "Ferrari front RH R²=0.59 with 29 points — need more varied pushrod data to improve").

## KEY FILES

### Data:
- data/calibration/{bmw,porsche,ferrari,acura}/calibration_points.json — Ground truth
- data/calibration/{bmw,porsche,ferrari,acura}/models.json — Fitted regression models

### Prediction models:
- car_model/cars.py — RideHeightModel (lines 252-390), DeflectionModel (lines 604-810), per-car definitions
- car_model/garage_model.py — GarageOutputModel: predict_front_static_rh_raw(), predict_rear_static_rh_raw(), deflection methods
- car_model/auto_calibrate.py — fit_models_from_points() (regression fitting), apply_to_car() (coefficient mapping), extract_point_from_ibt() (data extraction), _decode_spring_rate() (index conversion)

### Solver (consumers of predictions):
- solver/rake_solver.py — solution_from_explicit_offsets(), reconcile_ride_heights()
- solver/heave_solver.py — min_rate_for_sigma(), solve()
- solver/corner_spring_solver.py — solve()
- solver/solve_chain.py — _iterative_coupling_refinement(), materialize_overrides()

### Existing tests:
- tests/test_setup_regression.py — BMW/Porsche baseline regression tests
- tests/test_bmw_sebring_garage_truth.py — BMW garage output validation
- validation/run_validation.py — Objective function validation

## EXPECTED WORKFLOW

1. Create validation/universal_calibration_sweep.py
2. Run it → get the error map
3. Pick the worst car/field combination
4. Diagnose → fix → re-run sweep → verify improvement
5. Repeat step 3-4 until all cars meet targets or you've documented why they can't
6. Run full test suite
7. Commit

Start with Phase 1. Take your time reading the calibration data and understanding the prediction pipeline before writing code. The quality of your diagnosis in Phase 2 determines whether the fixes in Phase 3 actually work.
```
