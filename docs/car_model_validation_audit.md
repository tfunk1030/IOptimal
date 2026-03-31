# Deep Audit: `car_model/` and `validation/`

Generated: 2026-03-31

---

## Part 1 — `car_model/` Directory

### 1.1 File Map

| File | Purpose | Lines |
|------|---------|-------|
| `__init__.py` | Re-exports `CarModel`, `GarageRanges`, `get_car` from `cars.py` | 7 |
| `cars.py` | All 5 car definitions + 8 physics sub-models + registry | ~2131 |
| `setup_registry.py` | Canonical field registry (~90 fields), per-car YAML/STO mappings, Ferrari index decode, snapping helpers | ~882 |
| `garage.py` | `GarageOutputModel` — BMW/Sebring regression model for predicting garage-displayed values (static RH, torsion turns, deflections), constraint validation | ~538 |
| `calibrate_deflections.py` | One-time calibration script: reads all IBT files, fits 15 regression models for deflection/RH using least-squares + LOO cross-validation | ~306 |

---

### 1.2 Car-by-Car Parameter Audit

#### 1.2.1 BMW M Hybrid V8 — `CALIBRATED`

**Source:** 41+ sessions, 31 unique setups at Sebring. Most parameters calibrated from IBT telemetry.

| Parameter | Value | Source |
|-----------|-------|--------|
| `mass_car_kg` | 1050.3 kg | **Calibrated** from 41 sessions (corner weights) |
| `mass_driver_kg` | 75.0 kg | Standard |
| `weight_dist_front` | 0.4727 | **Calibrated** from corner weights |
| `brake_bias_pct` | 46.0% | **Calibrated** from IBT |
| `default_df_balance_pct` | 50.14% | **Calibrated** from telemetry |
| `tyre_load_sensitivity` | 0.22 | **Calibrated** (Michelin GTP compound) |
| `torsion_arb_coupling` | 0.25 | **Back-calibrated** from 73 IBT sessions (LLTD=50.99%) |
| `measured_lltd_target` | 0.41 | **Calibrated**: midpoint of 38-43% IBT-observed range |
| `vortex_excursion_pctile` | "p95" | **Calibrated**: p99 caused 43% false veto rate |

**Aero Compression:**

| Parameter | Value | Source |
|-----------|-------|--------|
| `ref_speed_kph` | 230.0 | iRacing internal reference |
| `front_compression_mm` | 15.0 | **Calibrated**: AeroCalc static 30.0 → dynamic 15mm |
| `rear_compression_mm` | 9.5 | **Calibrated**: varies with setup (7.8-9.5mm known limitation) |

**Torsion Bar Constants (CRITICAL):**

| Parameter | Value | Source |
|-----------|-------|--------|
| `front_torsion_c` | **0.0008036** | **Calibrated**: k_wheel = C × OD⁴, verified at OD=13.9mm → 30.0 N/mm |
| `front_torsion_od_ref_mm` | 13.9 mm | **Calibrated** reference point |
| `front_torsion_od_range_mm` | (13.90, 18.20) | 14 discrete options verified from garage |
| `front_motion_ratio` | **1.0** | MR already baked into C constant |
| `rear_torsion_c` | **None** (coil spring) | BMW uses coil springs at rear |
| `rear_motion_ratio` | **0.60** | **Calibrated** from measured LLTD and body roll (MR²=0.36) |
| `rear_spring_range_nmm` | (100, 300) | Verified, 10 N/mm steps |

**Heave Spring Model:**

| Parameter | Value | Source |
|-----------|-------|--------|
| `front_m_eff_kg` | 228.0 | **Calibrated** from Session 2 telemetry |
| `rear_m_eff_kg` | 2395.3 | **Calibrated** from Session 2 telemetry (suspiciously high — see Issues) |
| `heave_spring_defl_max_intercept_mm` | 106.43 | **Calibrated** from 31 unique setups, R²=0.985 |
| `heave_spring_defl_max_slope` | -0.310 | **Calibrated** |

**Ride Height Model:**

| Parameter | Value | Source |
|-----------|-------|--------|
| Front RH intercept | 30.5834 | **Calibrated** R²=0.15, RMSE=0.16mm (front nearly pinned at 30mm) |
| Front coeff_heave_nmm | -0.002137 | **Calibrated** (minimal sensitivity) |
| Front coeff_camber_deg | 0.236605 | **Calibrated** (r=0.64, major contributor) |
| Rear RH intercept | 48.9601 | **Calibrated** R²=0.52, RMSE=0.68mm, MaxErr=2.1mm |
| Rear coeff_pushrod | 0.226407 | **Calibrated** (positive — less negative pushrod = higher RH) |

**Garage Output Model** (`GarageOutputModel`):

| Model | R² | RMSE | Source |
|-------|------|-------|--------|
| Front static RH | 0.896 | 0.174mm | N=38, 7 features |
| Rear static RH | 0.914 | 0.295mm | N=51, 7 features |
| Heave slider | 0.802 | 1.128mm | N=38 |
| DeflectionModel (shock front) | exact on S1/S2 | — | pushrod coeff=0.226 |
| DeflectionModel (shock rear) | exact on S1/S2 | — | pushrod coeff=0.266 |
| Torsion bar defl | 0.905 | — | 31 unique setups |
| Heave spring defl static | 0.953 | 0.97mm | 31 unique setups |

**Damper Architecture:** Per-corner (4 corners × 5 adjustments: LS comp, LS rbd, HS comp, HS rbd, HS slope). Range 0-11 all. Force-per-click estimated (18 N/click LS, 80 N/click HS).

**ARB Model:**
- Front: Disconnected/Soft/Medium/Stiff → 0/5500/11000/16500 N·mm/deg, 5 blades
- Rear: Soft/Medium/Stiff → 1500/3000/4500 N·mm/deg, 5 blades

---

#### 1.2.2 Cadillac V-Series.R — `EXPLORATORY`

**Source:** 4 observations at Silverstone. Some parameters calibrated from IBT, most borrowed from BMW (shared Dallara LMDh platform).

| Parameter | Value | Source |
|-----------|-------|--------|
| `mass_car_kg` | 1030.0 | Confirmed (GTP minimum) |
| `weight_dist_front` | 0.485 | **Calibrated** from IBT corner weights |
| `brake_bias_pct` | 47.5% | **Calibrated** from IBT |
| `default_df_balance_pct` | 52.0% | **Calibrated** from aero map sweep |
| `tyre_load_sensitivity` | 0.20 | **ESTIMATE** — borrowed from general Michelin GTP |
| `torsion_arb_coupling` | 0.0 (default) | **NOT calibrated** — no IBT LLTD data |
| `measured_lltd_target` | None | **NOT calibrated** |

**Torsion Bar Constants:**

| Parameter | Value | Source |
|-----------|-------|--------|
| `front_torsion_c` | **0.0008036** | **Borrowed from BMW** — same Dallara platform, assumed identical |
| `front_torsion_od_range_mm` | (11.0, 16.0) | ESTIMATE (BMW range is 13.9-18.2 — DIFFERENT) |
| `front_motion_ratio` | 1.0 | Same as BMW |
| `rear_motion_ratio` | 0.60 | Same as BMW (Dallara geometry) |

**Aero Compression:**

| Parameter | Value | Source |
|-----------|-------|--------|
| `front_compression_mm` | 12.0 | **Calibrated**: learner mean 11.98mm across 2 sessions |
| `rear_compression_mm` | 18.5 | **Calibrated**: learner mean 18.53mm — was 8mm ESTIMATE (2.3× underestimate!) |

**CRITICAL ISSUE:** The Cadillac's rear aero compression is **18.5mm** vs BMW's **9.5mm** — nearly 2× higher. This means the Cadillac compresses far more at the rear at speed. Initial BMW-borrowed value (8mm) was a 2.3× underestimate that would have produced completely wrong rear dynamic ride heights.

**Pushrod Geometry:**

| Parameter | Value | Source |
|-----------|-------|--------|
| `front_pushrod_to_rh` | 1.28 mm/mm | **Calibrated** from 4 garage data points (BMW is 0.0!) |
| `front_base_rh_mm` | 41.34 | **Calibrated** |
| `front_heave_perch_to_rh` | -1.955 mm/mm | **Calibrated** (BMW is 0.0!) |
| `rear_pushrod_to_rh` | 0.042 | **Calibrated** (positive, very weak — BMW is -0.096) |

**KEY DIFFERENCE FROM BMW:** On the Cadillac, front RH depends strongly on pushrod AND heave perch. On the BMW, front RH is sim-pinned at 30mm regardless. The Cadillac needs a full multi-variable RH model (6-feature like BMW's garage output model), but it only has a 2-variable approximation with ±1.5mm accuracy.

**Missing calibrations:**
- Ride height model (`RideHeightModel`): NOT calibrated — no coefficients set
- Garage output model: NOT configured — no `GarageOutputModel` defined
- Deflection model: Default BMW values applied (wrong for Cadillac)
- ARB stiffness: Propagated from BMW without Cadillac-specific validation

---

#### 1.2.3 Ferrari 499P — `PARTIAL`

**Source:** 12 observations at Sebring, 9 garage screenshots for torsion bar calibration.

| Parameter | Value | Source |
|-----------|-------|--------|
| `mass_car_kg` | 1030.0 | GTP minimum confirmed |
| `weight_dist_front` | 0.476 | **Calibrated** from IBT corner weights (2725F/2997R) |
| `brake_bias_pct` | 54.0% | **Calibrated** from IBT (notably higher than BMW 46%) |
| `default_df_balance_pct` | 51.5% | **Calibrated** from aero map |
| `tyre_load_sensitivity` | 0.25 | **ESTIMATE** — higher than BMW (bespoke LMH compound) |

**Torsion Bar Constants (CRITICAL — DIFFERENT FROM BMW):**

| Parameter | Value | Source |
|-----------|-------|--------|
| `front_torsion_c` | **0.001282** | **CALIBRATED** from 6-point front + 4-point rear sweep |
| `rear_torsion_c` | **None** — modeled as indexed bar rate | Rear uses same C=0.001282 |
| `front_torsion_od_range_mm` | (20.0, 24.0) | **CALIBRATED**: much larger bars than BMW (20-24mm vs 13.9-18.2mm) |
| `rear_torsion_od_range_mm` | (23.1, 26.0) | **CALIBRATED** |
| `front_motion_ratio` | 1.0 | C already gives wheel rate |
| `rear_motion_ratio` | **0.612** | **CALIBRATED**: back-solved from LLTD=50.99%, exact match |

**Why Ferrari C constant is different from BMW:** The Ferrari 499P is a bespoke LMH chassis (not Dallara). It uses physically larger torsion bars (20-24mm OD front vs BMW 13.9-18.2mm) but different bar length / rocker geometry, producing C=0.001282 vs BMW C=0.0008036. Both cars produce similar wheel rate ranges through different geometry.

**Indexed Controls (CRITICAL FERRARI QUIRK):**
- Front heave spring: exposed as **index 0-8**, not N/mm. Decoded: `rate = 50 + (idx-1) × 20` → 30-190 N/mm
- Rear heave spring: exposed as **index 0-9**, not N/mm. Decoded: `rate = 530 + (idx-2) × 60` → 410-950 N/mm
- Front torsion bar: exposed as **index 0-18**, not mm OD. Decoded via linear interpolation on 20.0-24.0mm range
- Rear torsion bar: exposed as **index 0-18**, not mm OD. Decoded via bar rate range 364-590 N/mm

**Why Ferrari produces bad outputs:** Several factors:
1. **Heave spring index decode is approximate** — "front idx 1 ≈ 50 N/mm" and "20 N/mm per index" are estimates with no anchor from a garage screenshot or formula reverse-engineering
2. **Rear heave perch is massively different:** Ferrari baseline = -103.5mm, BMW baseline = +42mm (a 145mm difference). If any code path uses BMW defaults, results will be catastrophically wrong
3. **Front m_eff_kg = 176.0** — marked ESTIMATE, needs telemetry calibration
4. **Rear m_eff_kg = 2870.0** — ESTIMATE
5. **No garage output model** — no `GarageOutputModel` configured for Ferrari, so no garage-truth validation
6. **No deflection model** — uses BMW defaults, which are physically meaningless for Ferrari's different suspension architecture
7. **ARB stiffness values are all ESTIMATES** — front [0, 3000, 6000, 9000, 12000, 15000], rear [0, 1500, 3000, 4500, 6000, 9000] N·mm/deg

**Setup Registry Quirk:** Ferrari specs are initialized by copying ALL BMW specs (`**{k: v for k, v in _BMW_SPECS.items()}`), then selectively overriding ~60% of entries. This means any BMW-specific field that's not overridden silently applies to Ferrari — potential source of bugs.

---

#### 1.2.4 Porsche 963 — `UNSUPPORTED`

**Source:** 2 observations at Sebring. Nearly everything is ESTIMATE.

| Parameter | Value | Source |
|-----------|-------|--------|
| `mass_car_kg` | 1030.0 | GTP minimum |
| `weight_dist_front` | 0.475 | **ESTIMATE** |
| `brake_bias_pct` | (not set, uses default 46.0) | **ESTIMATE** |
| `tyre_load_sensitivity` | 0.18 | **ESTIMATE** |

**Torsion Bar Constants:**

| Parameter | Value | Source |
|-----------|-------|--------|
| `front_torsion_c` | **0.0008036** | **ESTIMATE — borrowed from BMW/Dallara** |
| `front_torsion_od_range_mm` | (11.0, 16.0) | **ESTIMATE** |
| `front_motion_ratio` | 1.0 | ESTIMATE |
| `rear_motion_ratio` | 0.60 | **ESTIMATE** — Multimatic geometry, not Dallara |

**CRITICAL PROBLEM:** Porsche uses a Multimatic chassis (NOT Dallara). The torsion bar C constant, motion ratios, track widths, CG height, and ARB stiffnesses are all borrowed from BMW/Dallara with no evidence they apply to Multimatic. The Porsche also uses DSSV spool-valve dampers (force-velocity curve is fundamentally different from shim-stack dampers), but the damper model uses BMW's shim-stack coefficients.

**Missing entirely:**
- Aero compression (uses ESTIMATE 15/8mm)
- Pushrod geometry (uses BMW ESTIMATE)
- Heave spring m_eff (uses ESTIMATE 176/2870 kg)
- Ride height model (none)
- Garage output model (none)
- Deflection model (default)
- All ARB stiffnesses (ESTIMATE)

**Setup Registry:** Only 17 fields mapped (vs BMW's 80+). Missing: heave/third springs, spring deflections, corner springs, ARBs, dampers, diff, most supporting params. Essentially a stub.

---

#### 1.2.5 Acura ARX-06 — `EXPLORATORY`

**Source:** 7+ observations at Hockenheim, 15+ garage screenshots. Most parameters calibrated from garage data, but aero maps uncalibrated.

| Parameter | Value | Source |
|-----------|-------|--------|
| `mass_car_kg` | 1030.0 | From PDF |
| `weight_dist_front` | 0.470 | **Calibrated** from IBT corner weights |
| `brake_bias_pct` | (not set, uses default 46.0) | **NOT CALIBRATED** |
| `front_heave_spring_nmm` | 180.0 | **Calibrated** from IBT (Hockenheim baseline) |
| `rear_third_spring_nmm` | 120.0 | **Calibrated** from IBT |

**Torsion Bar Constants (CRITICAL — BORROWED FROM BMW):**

| Parameter | Value | Source |
|-----------|-------|--------|
| `front_torsion_c` | **0.0008036** | **ESTIMATE — borrowed from BMW** |
| `rear_torsion_c` | **0.0008036** | **ESTIMATE — borrowed from BMW** |
| `front_torsion_od_range_mm` | (13.9, 15.86) | Confirmed from garage dropdown |
| `rear_torsion_od_range_mm` | (13.9, 18.20) | Same hardware as front |
| `front_motion_ratio` | 1.0 | Baked into C |
| `rear_motion_ratio` | 1.0 | Baked into C (ORECA — rear also torsion) |

**CRITICAL PROBLEM:** Acura uses ORECA LMDh chassis, NOT Dallara. The comment says "NOTE: garage torsion bar deflection is NOT purely weight/(C*OD^4); it includes preload from torsion bar turns. C constant calibration deferred until turns-corrected model is built." This means the borrowed BMW C=0.0008036 is **known to be wrong** for the Acura — it's being used as a placeholder.

**Damper Architecture (UNIQUE — heave+roll):**
- Heave dampers: FrontHeave/RearHeave (LS/HS comp+rbd+slope, range 1-10)
- Roll dampers: FrontRoll/RearRoll (LS+HS only, no comp/rbd split, range 1-10)
- `has_roll_dampers = True`
- Unlike BMW/Ferrari/Cadillac/Porsche which have per-corner dampers

**Why Acura produces bad outputs:**
1. **Rear RH misses aero targets** — aero maps not calibrated for Acura. `aero_compression` uses ESTIMATE 15/8mm
2. **Front heave damper bottoming** — always bottomed (-1.7 to -2.5mm) at all tested ODs. This is documented as "normal Acura characteristic" but the solver may not handle it correctly
3. **C constant is wrong** — needs ORECA-specific calibration from 5+ varied garage screenshots
4. **No garage output model** configured
5. **Roll dampers use baselines only** — no physics tuning, just IBT-observed click values as defaults
6. **Front RH is camber-dominated** — `front_rh = 37.55 + 2.388*camber` (R²=0.988). This is very different from BMW (pinned at 30mm). At camber=-2.8: RH≈30.9mm; at camber=-1.4: RH≈34.3mm

---

### 1.3 Setup Registry Completeness

| Car | Registry Fields | Complete? | Notes |
|-----|----------------|-----------|-------|
| BMW | ~80 fields | **Yes** | Full YAML + STO mapping, all 6 solver steps + supporting |
| Ferrari | ~80 fields (inherited + overrides) | **Mostly** | Inherits from BMW then overrides ~60%. Some BMW-specific fields may leak through |
| Cadillac | ~80 fields (inherited + overrides) | **Mostly** | BMW base + indexed ARBs + different brake bias resolution |
| Porsche | **17 fields only** | **No** | Stub — missing springs, dampers, ARBs, diff, gear ratios, most supporting params |
| Acura | ~80 fields (inherited + overrides) | **Mostly** | BMW base + heave/roll dampers + rear torsion + Acura-specific brakes/diff |

**Design pattern:** Ferrari, Cadillac, and Acura all start by shallow-copying the entire BMW spec dict, then selectively overriding entries. This means if a new BMW field is added, it silently propagates to all other cars — which may or may not be correct.

---

### 1.4 Garage Output Validation (`garage.py`)

The `GarageOutputModel` class predicts iRacing-displayed values from setup inputs using regression models:
- Front/rear static ride height
- Torsion bar turns
- Heave spring deflection (static + max)
- Heave slider position
- Shock deflections
- Third spring deflections

**Only BMW/Sebring has a `GarageOutputModel` configured.** All other cars have `garage_output_model = None`, meaning:
- No garage-truth validation for Ferrari, Cadillac, Porsche, Acura
- Legal-manifold search can only validate garage constraints for BMW/Sebring
- Any solver output for non-BMW cars bypasses deflection and ride height verification

The BMW model has regression accuracy:
- Front RH: R²=0.896, RMSE=0.174mm
- Rear RH: R²=0.914, RMSE=0.295mm

---

### 1.5 Deflection Calibration (`calibrate_deflections.py`)

This script:
1. Reads all IBT files for a car from the `ibtfiles/` directory
2. Extracts setup inputs + iRacing-computed garage display values (ground truth)
3. Deduplicates by unique setup configuration
4. Fits 15 regression models using OLS with LOO cross-validation
5. Prints coefficients with R², RMSE, and worst-case errors

Models fitted:
1. Front ride height (6 features)
2. Rear ride height (6 features)
3. Torsion bar turns (3 features: 1/heave, perch, OD)
4. Torsion bar deflection load (2 features)
5. Heave spring defl static (3 features: 1/heave, perch, 1/OD⁴)
6. Heave spring defl max (1 feature: heave rate)
7. Heave slider defl static (3 features)
8. Front shock defl static (1 feature: pushrod)
9. Rear shock defl static (1 feature: pushrod)
10. Rear spring load (1 feature: perch)
11. Rear spring defl max (2 features)
12. Third spring load (1 feature: perch)
13. Third spring defl max (2 features)
14. Third slider defl static (1 feature: third defl)
15. Corner weight analysis

**IMPORTANT:** This script supports `--car bmw|cadillac|porsche|acura` but NOT Ferrari. The fitted coefficients are manually transferred into `cars.py` and `garage.py`. Only BMW coefficients have been fitted and transferred.

---

### 1.6 Critical Issues Summary for `car_model/`

| Issue | Severity | Cars Affected |
|-------|----------|---------------|
| Torsion bar C constant borrowed from BMW without validation | **HIGH** | Acura, Porsche |
| No garage output model (no garage-truth checking) | **HIGH** | Ferrari, Cadillac, Porsche, Acura |
| Heave spring index decode approximate (no garage anchor) | **HIGH** | Ferrari |
| Rear heave perch baseline 145mm different from BMW | **HIGH** | Ferrari |
| DSSV damper force curve treated as shim-stack | **MEDIUM** | Porsche |
| Rear aero compression initially 2.3× underestimated | **FIXED** | Cadillac |
| Front m_eff and rear m_eff not calibrated | **MEDIUM** | Ferrari, Porsche |
| Rear m_eff = 2395 kg (BMW) and 2870 kg (others) seem physically implausible | **MEDIUM** | All |
| BMW registry specs silently inherited by other cars | **LOW** | Ferrari, Cadillac, Acura |
| Setup registry is a stub for Porsche (17 of 80+ fields) | **HIGH** | Porsche |
| Front heave damper always bottomed on Acura — solver may not handle correctly | **MEDIUM** | Acura |
| Acura RH camber-dominated (2.4mm/deg) vs BMW pinned at 30mm | **MEDIUM** | Acura |

---

## Part 2 — `validation/` Directory

### 2.1 File Map

| File | Purpose |
|------|---------|
| `run_validation.py` | Loads all observations, scores BMW/Sebring subset, computes correlations, writes `objective_validation.{md,json}` |
| `objective_calibration.py` | Recalibration tooling: ablation studies, weight search, holdout cross-validation, component ablations |
| `observation_mapping.py` | Normalizes raw observation setup payloads to canonical field names; signal fallback hierarchy for telemetry |
| `objective_validation.json` | Last validation run output (BMW/Sebring 99 samples, scores, correlations, support matrix) |
| `objective_validation.md` | Human-readable summary of validation results |
| `calibration_weights.json` | Calibration search results: track-aware/trackless modes, holdout folds, weight suggestions |
| `calibration_report.md` | Human-readable calibration report |
| `cross_session_correlations.md` | Manual analysis of 73 BMW/Sebring sessions: raw parameter → lap time correlations |

---

### 2.2 What `run_validation.py` Actually Does

1. **Loads** all `*.json` observation files from `data/learnings/observations/`
2. **Filters** to BMW/Sebring sessions with valid lap times > 60s
3. **Normalizes** each setup to canonical parameter names via `observation_mapping.py`
4. **Creates** an `ObjectiveFunction` with the BMW car model, Sebring track profile, and `single_lap_safe` scenario
5. **Evaluates** each observation's setup parameters through the objective function
6. **Computes** Pearson and Spearman correlations between:
   - Objective score vs lap time (for all valid rows and non-vetoed rows)
   - Each numeric setup parameter vs lap time
7. **Reports** signal usage (direct vs fallback vs missing)
8. **Writes** `objective_validation.md` and `objective_validation.json`

---

### 2.3 Observation Counts

| Car | Track | Samples | Confidence Tier |
|-----|-------|---------|-----------------|
| BMW | Sebring International | **99** | calibrated |
| Ferrari | Sebring International | **12** | partial |
| Cadillac | Silverstone | **4** | exploratory |
| Porsche | Sebring International | **2** | unsupported |
| Acura | Hockenheim | **0** | unsupported |

**Note:** The JSON says 99 BMW samples, but the calibration_weights.json (from an earlier run) says 75. This indicates observations have been accumulating over time.

---

### 2.4 Current Correlation Numbers

**BMW/Sebring (99 samples, 98 non-vetoed):**

| Metric | Value |
|--------|-------|
| **Spearman (non-vetoed, score vs lap time)** | **-0.1808** |
| Pearson (non-vetoed) | -0.0604 |
| Spearman (all valid) | -0.1714 |
| Veto rate | 1.0% (1 out of 99) |

**From calibration_weights.json (75 samples, older run):**

| Mode | Spearman |
|------|----------|
| Track-aware (74 non-vetoed) | -0.0598 |
| Trackless (75 non-vetoed) | -0.1348 |
| Lap-gain only (track-aware) | -0.2054 |

---

### 2.5 Why Calibration Is Weak (Spearman ≈ -0.12 to -0.18)

This is the central question. Based on the data:

**1. The lap_gain_ms term is the only term with meaningful signal:**
- `lap_gain_ms` alone: Spearman = -0.2054 (track-aware, 74 non-vetoed)
- `lap_gain_ms` alone: Spearman = -0.1659 (trackless, 75 non-vetoed)
- Adding penalty terms (platform, envelope, etc.) **worsens** correlation
- Best configuration found by grid search: `lap_gain=0.25, everything_else=0.0`

**2. Penalty terms actively hurt correlation:**
- `platform_risk_ms`: Spearman = **+0.108** (positive = WRONG direction)
- `envelope_penalty_ms`: Spearman = **+0.051** (wrong direction track-aware), -0.055 (trackless)
- All other penalty terms (driver, uncertainty, staleness, empirical) have NaN Pearson (zero variance)

**3. Individual lap_gain components are poorly calibrated:**
- `damping_ms`: Spearman = **+0.246** — **WRONG DIRECTION** (higher damping penalty = faster laps in the data)
- `camber_ms`: Spearman = **+0.125** — wrong direction
- `rebound_ratio_ms`: Spearman = **-0.046** (noisy, near zero)
- `diff_ramp_ms`: Spearman = **-0.123** — correct direction, modest signal
- `lltd_balance_ms`: Spearman = **+0.091** — wrong direction
- `df_balance_ms`: Spearman = **+0.065** — wrong direction

**4. Damping penalty is the biggest problem:**
The damping component has the strongest individual correlation but in the **WRONG** direction (+0.246). This means the objective function penalizes setups that are actually faster. Component ablation confirms: removing damping_ms barely changes the overall score (-0.058 → -0.060), but the component itself is actively misleading.

**5. Holdout validation shows instability:**
- 5-fold cross-validation: mean Spearman = -0.080 (track-aware), -0.131 (trackless)
- Worst fold: +0.121 (positive = wrong direction entirely)
- Train-searched weights produce mean holdout Spearman of +0.010 (track-aware) — near zero, no generalization

**6. The real predictive signal is in raw parameters, not the objective:**
- `front_ls_comp` raw correlation: Spearman = **-0.429** (much stronger than objective score)
- `front_toe_mm`: Spearman = +0.458
- `rear_toe_mm`: Spearman = -0.457
- These raw parameter correlations dwarf the objective function's correlation

**Root Causes:**
1. **Physics model mismatch:** The objective function's physics predictions (damper targets, balance targets) don't match what actually makes the car fast at Sebring. The damper penalty rewards setups that are actually slower.
2. **Penalty dilution:** Adding any penalty term on top of lap_gain degrades correlation because the penalty models are uncalibrated or wrong-direction.
3. **Too many near-zero-variance terms:** Most penalty terms (driver, uncertainty, staleness, empirical) have zero variance across the BMW dataset, contributing nothing but noise.
4. **Small effective dataset:** 99 observations of ONE car at ONE track. Many observations come from the same driver running similar setups — limited variance in the parameters that matter most.
5. **Confounding:** Fuel load correlates +0.403 with lap time (race vs practice), and fuel variation is not properly isolated by the objective function.

---

### 2.6 Observation Mapping (`observation_mapping.py`)

**Normalization:** Maps observation JSON fields to canonical registry names:
- `front_heave_spring_nmm` / `front_heave_nmm` → `front_heave_spring_nmm`
- `front_torsion_od_mm` / `torsion_bar_od_mm` → `front_torsion_od_mm`
- Damper values: averaged from L/R corners via `_avg_damper()`
- Diff ramp: parsed from "coast/drive" string to nearest legal option index

**Signal Fallback Hierarchy:** For validation metrics that may be missing from older observations:

| Metric | Direct Source | Fallback(s) |
|--------|-------------|-------------|
| `front_excursion_mm` | `front_rh_excursion_measured_mm` | `front_rh_std_mm`, `front_heave_defl_p99_mm` |
| `braking_pitch_deg` | `pitch_range_braking_deg` | `pitch_range_deg` |
| `front_lock_p95` | `front_braking_lock_ratio_p95` | `front_brake_pressure_peak_bar` |
| `rear_power_slip_p95` | `rear_power_slip_ratio_p95` | `tc_intervention_pct` |
| `front_pressure_hot_kpa` | `front_pressure_mean_kpa` | `lf_pressure_kpa`, `rf_pressure_kpa` |

**Signal Coverage (BMW/Sebring, 99 observations):**

| Metric | Direct | Fallback | Missing |
|--------|--------|----------|---------|
| `body_slip_p95_deg` | 99 | 0 | 0 |
| `braking_pitch_deg` | 33 | 42 | 24 |
| `front_excursion_mm` | 33 | 42 | 24 |
| `front_heave_travel_used_pct` | 75 | 0 | 24 |
| `understeer_high/low_deg` | 99 | 0 | 0 |

24 observations (24%) are missing critical signals like `front_excursion_mm`, `braking_pitch_deg`, and `front_lock_p95`. These are older observations ingested before the telemetry extraction was improved.

---

### 2.7 Objective Calibration (`objective_calibration.py`)

This module:
1. Loads BMW/Sebring observations
2. Scores them in two modes: `track_aware` (with Sebring TrackProfile) and `trackless` (no track profile)
3. Computes per-term and per-component correlations with lap time
4. Runs ablation studies (drop each weight term, measure impact on Spearman)
5. Runs component ablations (drop each lap_gain sub-component)
6. Performs 5-fold holdout cross-validation
7. Runs coarse weight grid search over 8^5 = 32,768 combinations

**Key finding from calibration:** The best weight configuration is always `{lap_gain: 0.25, everything_else: 0.0}` — i.e., only the lap_gain term matters, and all penalty terms should be zeroed out for maximum correlation with lap time.

**Auto-apply is disabled:** The report explicitly says `auto_apply: false` with reason: "Calibration tooling is implemented, but runtime auto-application stays disabled until track-aware correlation is materially negative and stable under stronger validation."

---

### 2.8 Ride Height Modeling Issues

**BMW front RH is "sim-pinned":**
- The BMW front static RH is nearly constant at 30.0mm regardless of setup parameters
- `PushrodGeometry.front_pushrod_to_rh = 0.0` (no sensitivity measured in tested range)
- The `RideHeightModel.front_intercept = 30.5834` with tiny coefficients confirms this
- The `GarageOutputModel` front RH model (R²=0.896) shows more sensitivity because it uses 7 features including heave_nmm, camber, and pushrod, but the actual variation is only ±0.5mm

**BMW rear RH is more variable but hard to predict:**
- `RideHeightModel` rear R² = 0.52, RMSE = 0.68mm — mediocre
- `GarageOutputModel` rear R² = 0.914, RMSE = 0.295mm — much better with more features
- Primary drivers: pushrod (0.362 mm/mm), rear spring perch (-0.621 mm/mm), third perch (-0.821 mm/mm)

**Non-BMW cars:** No ride height models exist. The solver uses the `PushrodGeometry` linear approximation (often 2-variable only), which can be off by ±1.5mm or more. For Ferrari and Acura, where RH depends on camber, torsion bar turns, and heave perch in non-linear ways, this produces significant errors.

---

### 2.9 Heave Damper and Spring Deflection Calibration Issues

**BMW heave spring deflection model:**
- Static: `defl = -20.756 + 7.030/heave_nmm - 0.9146*perch + 666311/OD^4` (R²=0.953)
- Max: `defl_max = 106.43 - 0.310*heave_nmm` (R²=0.985)
- Travel budget: `available = defl_max - defl_static`

The garage output model uses slightly different coefficients:
- `heave_spring_defl_max_intercept_mm = 96.019667` (vs DeflectionModel: 106.43)
- `heave_spring_defl_max_slope = -0.082843` (vs DeflectionModel: -0.310)

This **discrepancy** between the `GarageOutputModel` and the `DeflectionModel` values for heave spring defl max could cause inconsistency between garage validation and solver calculations.

**Acura heave damper:** Always bottomed (-1.7 to -2.5mm deflection) across all tested configurations. The `HeaveSpringModel` for Acura has all deflection model coefficients zeroed out:
```
slider_perch_coeff=0.0, slider_intercept=0.0, slider_heave_coeff=0.0,
heave_spring_defl_max_intercept_mm=0.0, heave_spring_defl_max_slope=0.0,
defl_static_intercept=0.0, defl_static_heave_coeff=0.0,
```
This means the Acura has no spring deflection predictions at all — all deflection-based checks will produce zeros.

**Ferrari heave spring deflection:** Uses BMW defaults (no `DeflectionModel` overrides). Since Ferrari's heave springs are indexed (not N/mm) and the suspension geometry is completely different, the BMW deflection coefficients are meaningless for Ferrari.

---

## Part 3 — Cross-Cutting Issues

### 3.1 The "Borrowed Constant" Problem

The following constants are borrowed across cars without validation:

| Constant | BMW Value | Cars Borrowing It | Risk |
|----------|-----------|-------------------|------|
| `front_torsion_c` | 0.0008036 | Cadillac, Porsche, Acura | HIGH for Porsche (Multimatic), Acura (ORECA) |
| `rear_motion_ratio` | 0.60 | Cadillac, Porsche | MEDIUM — Cadillac shares Dallara, Porsche does not |
| `front_m_eff_kg` | 228.0 → varied | All non-BMW | Some calibrated (Acura: 450, Cadillac: 266), others ESTIMATE |
| ARB stiffness values | BMW-calibrated | Cadillac, Acura | MEDIUM — Dallara-shared may be OK |
| Damper force-per-click | 18/80 N/click | All non-BMW | HIGH for Porsche (DSSV), MEDIUM for Ferrari (different click range) |
| `DeflectionModel` coefficients | BMW-calibrated | All non-BMW | HIGH — wrong for every non-BMW car |

### 3.2 The Rear m_eff Problem

BMW's `rear_m_eff_kg = 2395.3` and the default for other cars is 2870.0. These values are physically implausible as effective heave masses — the entire car weighs ~1100 kg. The documentation acknowledges this: "m_eff is NOT the physical sprung mass — it's a lumped parameter that captures the frequency-domain coupling." However, the extreme values (5-10× the actual sprung mass on that axle) suggest the model may be capturing aero-induced apparent mass, track surface coupling, or some other phenomenon that would not transfer between cars/tracks.

### 3.3 Score-to-Lap-Time Correlation Path

```
Observation → normalize_setup_to_canonical_params() → ObjectiveFunction.evaluate()
  → lap_gain_ms (damping, camber, LLTD, DF balance, rebound ratio, diff, TC, ARB)
  → platform_risk_ms
  → envelope_penalty_ms
  → weighted sum → score_ms
                    ↕ compare
                 actual_lap_time_s
                    → Spearman correlation
```

The correlation is weak (-0.18) because:
1. The physics model's idea of "good" (low damping penalty, correct balance) doesn't match what's actually fast
2. Driver skill variation across the 99 observations is not controlled for
3. Penalty terms add noise rather than signal
