# iOptimal GTP / GT3 Calibration Guide
*Generated 2026-04-02 | Last revised: 2026-04-27 (GT3 onboarding section added — Wave 9 Unit 2)*
*Previous revision: 2026-04-08 (LLTD phantom proxy disabled, σ-cal driver anchor architecture, per-axle roll damper flags)*

---

## 🚨 What Changed in 2026-04-08

### LLTD calibration was reading a geometric proxy, not a real measurement

The field stored as `lltd_measured` in `data/calibration/<car>/models.json` is the analyzer's `roll_distribution_proxy`:

```python
# analyzer/extract.py:574–599  (the comment in the file already warns this is NOT LLTD)
front_moment = mean_front_RH_diff × tw_f²
rear_moment  = mean_rear_RH_diff  × tw_r²
roll_distribution_proxy = front_moment / total_moment
state.lltd_measured = state.roll_distribution_proxy   # backward-compat alias
```

For a rigid chassis this collapses to `t_f³ / (t_f³ + t_r³)`. It is **insensitive to spring stiffness**.

**Verified empirically across 5 Porsche/Algarve IBTs** with rear stiffness varying 100–300%:

| Session | R_third | R_coil | R_ARB | "lltd_measured" proxy |
|---|---|---|---|---|
| 14-23-44 B HOT | 160 | 180 | Stiff/10 | 0.5049 |
| 13-59-01 B HOT | 160 | 180 | Stiff/10 | 0.5050 |
| 13-26-10 B HOT | 160 | 180 | Stiff/10 | 0.5047 |
| 13-14-00 A heavy | **320** | **150** | **Stiff/5** | 0.5056 |
| 15-58-25 hybrid | **320** | **180** | Stiff/10 | 0.5056 |

**Spread = 0.09 pp** across rear-third varying 100% and rear ARB shifting 5 blades. A real LLTD measurement would shift 5–15 pp. The proxy is geometric noise.

**Fix shipped 2026-04-08:**
- `auto_calibrate.py:1360–1395` block populating `models.measured_lltd_target = mean(proxy)` is now gated behind `if False:` with provenance comment
- `data/calibration/porsche/models.json:measured_lltd_target` cleared to `null`
- `car_model/cars.py` Porsche definition sets `measured_lltd_target = 0.521` explicitly from the **OptimumG/Milliken physics formula**: `weight_dist_front + (tyre_sens/0.20) × 0.05 + speed_correction = 0.471 + 0.045 + ~0.005`
- Other cars: gate falls back to `arb_solver.py:303` physics computation when `measured_lltd_target` is None
- The "11 pp model gap" (model k_front/k_total = 0.391 vs proxy 0.503) was apples-to-oranges. With the physics target (0.521), the gap is 13 pp — still REAL but un-attributable without true wheel-force telemetry

**Open epistemic gap**: we have **no direct LLTD measurement** from iRacing IBT (no individual wheel-load channels). To upgrade we need EITHER (a) wheel-force telemetry if iRacing exposes it, OR (b) a controlled per-axle ARB lap-time correlation across 10+ varied-blade sessions on the same track. Three hypotheses for the 13 pp model-vs-physics gap remain unverifiable: (A) OptimumG rule doesn't apply to GTP/Porsche tyres, (B) driver setup is rear-stiff suboptimal but lap time still good, (C) one of the model's k_roll terms has a residual physics error. Until disambiguated, the ARB solver uses a driver-anchor fallback when `lltd_error > 3 pp`.

### Driver-anchor pattern (now used by 5 solvers)

Several solvers now read `current_setup` and prefer driver-loaded values as soft anchors when the model is admittedly broken or within tolerance:

| Solver | Anchor | Trigger |
|---|---|---|
| `heave_solver.min_rate_for_sigma` | Driver-loaded F_heave / R_third | σ-cal sticky: model_σ at current rate ≤ effective target + 0.05 mm |
| `corner_spring_solver.solve` | Driver-loaded R_coil (direct) | `current_rear_spring_nmm` provided → use it as `rear_target_rate` |
| `arb_solver.solve` | Driver-loaded ARB size + blade | LLTD model error > 3 pp (model can't reach target) |
| `diff_solver.solve` | Driver-loaded coast/drive/preload | Within ±5° / ±15 Nm of computed |
| `supporting_solver._solve_tc` | Driver-loaded TC gain/slip | Within ±2 clicks of computed |
| `candidate_search._apply_family` | Skip family scaling on F_heave/R_third | Base value within 10 N/mm of driver |

**These anchors are explicit and provenance-tracked** (each fires a `"; anchored to driver-loaded X"` reason in the solver's reasoning string). They are **NOT lap-time-driven** — none of them call `if lap_time < X:`. The driver loading their best-so-far setup creates an implicit lap-time signal but the anchor logic does not consume `lap_time`. See `feedback_no_laptime_setup_selection.md`.

### σ-calibration architecture

`solver/heave_solver.py:min_rate_for_sigma()` now accepts `current_rate_nmm` + `current_meas_sigma_mm` and computes a calibration ratio between the synthetic σ model and the IBT-measured rear/front_rh_std at the driver's current rate. The model σ is scaled to MEASURED units via `cal_ratio = meas_σ / model_σ_at_current`, and the σ target is set to `min(user_target, current_meas_σ × 1.05)`. A sticky pre-check returns the driver's current rate when its model_σ is within 0.05 mm of the target — this prevents 1-step gradient drift.

Validated against Porsche/Algarve newest IBT (driver rate=160, σ_meas=7.6, model σ=7.34, cal_ratio=1.036, sticky returns 160 exactly). The σ MODEL is still physics-driven; the σ TARGET is driver-anchored when telemetry provides the anchor data.

### Per-axle roll damper architecture

`DamperModel` now has `has_front_roll_damper` and `has_rear_roll_damper` flags. **Porsche 963 has FRONT roll damper but NO rear roll damper** — rear roll motion is implicit in the per-corner LR/RR shocks. Acura has BOTH. The setup writer (`output/setup_writer.py:1069`) and damper solver (`solver/damper_solver.py:790`) now gate roll damper output on the per-axle flag. Before this fix Porsche was emitting phantom `CarSetup_Dampers_RearRoll_LsDamping/HsDamping` XML IDs that don't exist in the iRacing Porsche schema.

---

## 🆕 What Changed in 2026-04-07

If you last read this guide before 2026-04-07, three big things changed:

### 1. Strict calibration gate with three statuses

Every subsystem is now classified as one of:

| Status | Meaning | Behavior |
|---|---|---|
| `calibrated` | Real measurement, R² ≥ 0.85 OR auto-cal validated | Step runs cleanly, output trusted |
| `weak` | R² < 0.85 OR manual override that auto-cal *contradicts* | Step still runs (legacy code expects values) but is flagged `[~~]` and a `WEAK CALIBRATION DETECTED` banner is printed. Treat the output as a starting point, not gospel. |
| `uncalibrated` | No measurement at all | Step blocks, outputs CLI calibration instructions |

R² thresholds: `R2_THRESHOLD_BLOCK = 0.85`, `R2_THRESHOLD_WARN = 0.95`. Set in `car_model/calibration_gate.py`.

**Cascade rule:** only TRUE blocks (`uncalibrated`, `dependency_blocked`) propagate to downstream steps. Weak blocks do NOT cascade. Step 5 (Geometry) and Step 6 (Dampers) cascade from Step 3 (wheel rates), NOT from Step 4 (ARBs).

### 2. Compliance physics for static RH and deflection

Static ride heights and spring deflections under aero load follow `defl ∝ F/k` (compliance), not stiffness. The RH and deflection models now include `1/heave`, `1/rear_third`, `1/rear_spring` features in addition to (or instead of) the linear ones.

**Result for Porsche/Algarve:**
- Front RH model: R² 0.96 → **0.9997** (LOO RMSE 0.03mm)
- Rear RH model: R² 0.61 → **0.94** (LOO RMSE 0.44mm)
- Rear shock deflection: R² 0.94 → 0.97
- Rear spring deflection: R² 0.67 → 0.97
- Third spring deflection: R² 0.93 → 0.97

BMW continues to use linear terms (its data is fit better that way); both functional forms coexist in the same `RideHeightModel`/`DeflectionModel` classes. Each car uses whichever fits its data best. The `apply_to_car()` function zeroes ALL coefficient slots before applying new values, so cross-car contamination is impossible.

### 3. Provenance tracking

Every solver run now prints a `CALIBRATION CONFIDENCE — provenance per subsystem` block listing every subsystem with its source, R² (where applicable), and confidence label. JSON output carries the full `calibration_provenance` dict so you can audit any value.

Example Porsche/Algarve output:

```
CALIBRATION CONFIDENCE — provenance per subsystem:
  OK aero_compression       [HIGH]  IBT-derived (17 sessions)
  OK ride_height_model      [HIGH R²=0.94]  regression (front R²=1.00, rear R²=0.94)
  OK deflection_model       [HIGH R²=0.97]  regression (weakest R²=0.97)
  OK spring_rates             car-specific model
  OK pushrod_geometry         garage screenshots
  OK damper_zeta            [HIGH]  IBT click-sweep (79 sessions)
  ~~ arb_stiffness          [MANUAL_OVERRIDE]  manual override (auto-cal CONTRADICTS car definition)
  OK lltd_target              IBT data (target=0.5034)
  OK roll_gains               IBT-calibrated
```

The `~~` marker on `arb_stiffness` indicates a weak step. The user gets explicit instructions in the warning section.

### 4. No silent fallbacks

Every `getattr(car, "field", bmw_default)` pattern in the solver was removed. Direct attribute access — fail loudly if a car is missing a field. Specific cleanups:
- `solver/objective.py`: 11 fallbacks removed
- `solver/sensitivity.py`: 3 m_eff hardcodes removed
- `solver/candidate_search.py`: hardcoded `"bmw"` and torsion OD fallback removed
- `solver/sector_compromise.py`: BMW brake bias and camber defaults removed
- `solver/legal_space.py`: BMW spring rate refs replaced with per-car
- `solver/damper_solver.py`: 50-line baseline-fallback path removed (now raises ValueError)
- `solver/stint_model.py`: heave/third defaults removed
- `solver/rake_solver.py`: 3 fuel_capacity_l fallbacks removed
- `solver/arb_solver.py`, `bayesian_optimizer.py`, `explorer.py`: tyre_load_sensitivity fallbacks removed
- `car_model/cars.py:pushrod_for_target_rh`: -29.0 BMW fallback removed (now raises ValueError)

---

## ⚠️ The One Rule That Changes Everything

**You cannot change one parameter in isolation.**

Every spring, bar, and ride height target is coupled. Before touching anything:
1. Understand the full dependency chain below
2. Identify every downstream parameter that must move with it
3. Run a fresh IBT session with the change to re-anchor the calibration

Missing this causes the solver to recommend setups that look valid on paper but are physically impossible — e.g., a static RH target that requires the car to sit on the floor, or a LLTD that no ARB combination can achieve.

---

## Dependency Map

```
TORSION BAR OD
  ↓ front corner spring rate changes
  ↓ → LLTD range shifts (same ARB blade now produces different roll stiffness ratio)
  ↓ → heave deflection under aero load changes (front stiffness changed)
  ↓   → STATIC RH TARGET must move to achieve same DYNAMIC RH
  ↓   → VORTEX THRESHOLD (min safe front RH) may change
  ↓ → damping targets may need revisit (natural freq changed)
  ↓ → if chasing same LLTD: ARB blade must compensate

HEAVE SPRING (front or rear)
  ↓ front/rear heave stiffness changes
  ↓ → heave deflection under aero downforce changes
  ↓   → to maintain same dynamic RH: static RH must change
  ↓   → static RH target stored in solver must be updated
  ↓ → platform natural frequency changes
  ↓   → damper zeta targets change (critical damping force changes)
  ↓   → damper click recommendations invalid until re-calibrated
  ↓ → floor clearance/bottoming risk profile changes
  ↓   → vortex threshold may need recalculation

REAR THIRD SPRING
  ↓ rear platform compliance changes
  ↓ → rear dynamic RH variance changes (car rides higher or lower under load)
  ↓   → static RH target for rear must change
  ↓   → rake angle changes → DF balance shifts
  ↓   → wing angle recommendation may shift to compensate
  ↓ → braking platform behavior changes
  ↓   → diff preload interaction (exit traction affected by pitch change)

ARB BLADE or SIZE
  ↓ roll stiffness changes (front or rear)
  ↓ → LLTD ratio shifts
  ↓   → if LLTD was calibrated, it's now wrong
  ↓   → camber under roll changes → tyre contact patch changes
  ↓ → weight transfer rate changes
  ↓   → diff preload tuning may need revisit
  ↓ NOTE: changing blade does NOT affect static RH or heave deflection
         (ARB only loads in roll, not heave)

STATIC RH TARGET (garage setting)
  ↓ sets the baseline before any aero/mechanical load
  ↓ → dynamic RH = static RH - deflection under load
  ↓   deflection ≈ (aero_downforce_per_corner) / (heave_spring_rate)
  ↓ → if dynamic RH changes: DF balance changes → wing recommendation may shift
  ↓ → floor clearance: dynamic RH must stay above vortex threshold
  ↓ LEGAL MINIMUM: 30mm static (all cars, iRacing enforced)

DAMPER CLICKS
  ↓ changes transient behavior only — does NOT change static or mean dynamic RH
  ↓ → affects platform stability metric (how quickly car settles)
  ↓ → affects how IBT records dynamic RH variance (σ)
  ↓ NOTE: changing dampers without changing springs = safe isolated change
         (only parameter group that doesn't cascade into RH or LLTD)
```

---

## Calibration Gate (2026-04-04)

The solver now enforces a **calibration gate** at each of the 6 solver steps. If any subsystem required by a step is uncalibrated for the current car, that step is **blocked** and the system outputs calibration instructions instead of a setup value.

**The system never outputs a setup value from an uncalibrated model.** An incomplete setup with calibration instructions is more valuable than a complete setup built on unproven data.

### Per-Step Requirements

| Step | Name | Required Subsystems |
|------|------|-------------------|
| 1 | Rake / Ride Heights | aero_compression, ride_height_model, pushrod_geometry |
| 2 | Heave / Third Springs | spring_rates |
| 3 | Corner Springs | spring_rates |
| 4 | Anti-Roll Bars | arb_stiffness, lltd_target (physics formula or driver-anchor — NOT IBT proxy) |
| 5 | Wheel Geometry | roll_gains |
| 6 | Dampers | damper_zeta |

### Current Calibration Status

| Car | Steps 1-3 | Step 4 (ARBs) | Step 5 (Geometry) | Step 6 (Dampers) |
|-----|----------|--------------|-------------------|-----------------|
| BMW | all calibrated | calibrated | calibrated | calibrated |
| Ferrari | calibrated | **BLOCKED** (ARB stiffness + LLTD) | **BLOCKED** (roll gains) | **BLOCKED** (damper zeta) |
| Cadillac | **step 1 BLOCKED** (RH model) | BLOCKED | BLOCKED | BLOCKED |
| Porsche | all calibrated | calibrated (LLTD from telemetry, ARB from response analysis) | calibrated (roll gains validated) | calibrated (zeta from 35 sessions) |
| Acura | **step 1 BLOCKED** (aero + RH) | BLOCKED | BLOCKED | BLOCKED |

### How to Unblock Steps

When the solver blocks a step, it prints exactly what data to collect. The general pattern:

1. **ARB stiffness:** Record 3+ IBT sessions with different front/rear ARB sizes (keep springs constant). Run `python -m car_model.auto_calibrate --car <car> --ibt-dir <telemetry_dir>`
2. **LLTD target:** ⚠ The OLD path (`python -m validation.calibrate_lltd`) reads `lltd_measured` which is a GEOMETRIC PROXY, not real LLTD — see "What Changed in 2026-04-08" above. Use the OptimumG/Milliken physics formula directly (set `measured_lltd_target` in `cars.py`). To upgrade to a TRUE LLTD calibration, you need either iRacing wheel-load telemetry or a controlled per-axle ARB lap-time correlation (10+ varied-blade sessions).
3. **Roll gains:** Run 5+ laps capturing full lateral-g sweep. Run `python -m learner.ingest --car <car> --ibt <session.ibt> --all-laps`
4. **Damper zeta:** Run 5+ stints with LS comp at varied clicks (keep everything else identical). Run `python -m validation.calibrate_dampers --car <car> --track <track>`
5. **Ride height model:** Set 10+ different spring/pushrod/perch combinations in the garage. Record an IBT session at each (3+ clean laps). Run `python -m car_model.auto_calibrate --car <car> --ibt-dir <telemetry_dir>`
6. **Aero compression:** Record 3+ IBT sessions at different speed profiles. Run `python -m learner.ingest --car <car> --ibt <each_file>`

---

## Per-Car Status + Parameter Reference

---

### 🔵 BMW M Hybrid V8 — Calibration Status: **FULLY CALIBRATED (6/6 steps)**

**Springs (continuous, not indexed):**
| Parameter | iRacing Label | Solver Field | Range | Legal | Status |
|-----------|--------------|-------------|-------|-------|--------|
| Front torsion bar OD | TorsionBarDiameter | `torsion_bar_od_mm` | 13.9–18.2mm (0.1mm steps) | ✅ | ✅ CALIBRATED — 14 options, validated |
| Front heave spring | HeaveSpringRate (front) | `front_heave_nmm` | 0–900 N/mm | ✅ | ✅ CALIBRATED |
| Rear third spring | ThirdSpringRate (rear) | `rear_third_nmm` | 100–900 N/mm | ✅ | ✅ CALIBRATED |
| Rear torsion bar | rear coil spring | continuous | 100–300 N/mm | ✅ | ✅ |

**Dampers (0–11 clicks per channel):**
| Channel | iRacing Label | Solver Field | Baseline | Status |
|---------|--------------|-------------|----------|--------|
| LS Compression | LsCompDamp | `*_ls_comp_clicks` | F:7 R:6 | ✅ zeta=0.68/0.47 CALIBRATED |
| HS Compression | HsCompDamp | `*_hs_comp_clicks` | F:5 R:3 | ✅ zeta=0.23/0.20 CALIBRATED |
| LS Rebound | LsRbdDamp | `*_ls_rbd_clicks` | F:6 R:7 | ✅ |
| HS Rebound | HsRbdDamp | `*_hs_rbd_clicks` | F:8 R:9 | ✅ |

**ARB:**
| Parameter | Sizes | Blades | Stiffness per size |
|-----------|-------|--------|-------------------|
| Front | Disc / Soft / Medium / Stiff | 1–5 | 0 / 5500 / 11000 / 16500 N/mm·deg |
| Rear | Soft / Medium / Stiff | 1–5 | 1500 / 3000 / 4500 N/mm·deg |

**Other:**
- Wing: 12–17 deg (1° steps) — 6 aero maps ✅
- Diff preload: calibrated
- Weight dist front: 47.3% (measured)
- DF balance target: 50.14% (validated from Sebring telemetry)
- m_eff front: 228 kg ✅ calibrated; rear: 2395 kg ✅

**What's not perfect:**
- k-NN limited to Sebring (102 sessions). At a new track, solver has no empirical data — first 3 sessions will be physics-only.
- Torsion arb coupling = 0.25 (estimated, not validated from click sweep)

---

### 🔴 Ferrari 499P — Calibration Status: **PARTIAL (3/6 steps calibrated, steps 4-6 BLOCKED)**

**Springs (INDEXED — not continuous):**

| Parameter | iRacing Label | Index | Physical Value | Status |
|-----------|--------------|-------|----------------|--------|
| Front Heave | HeaveSpringRate | 0 | 30 N/mm | ⚠️ extrapolated |
| | | 1 | 50 N/mm | ✅ validated (IBT Mar19/20) |
| | | 2 | 70 N/mm | ⚠️ linear estimate |
| | | 3 | 90 N/mm | ⚠️ linear estimate |
| | | 4 | 110 N/mm | ⚠️ linear estimate |
| | | 5 | 130 N/mm | ⚠️ linear estimate |
| | | 6 | 150 N/mm | ⚠️ linear estimate |
| | | 7 | 170 N/mm | ⚠️ linear estimate |
| | | 8 | 190 N/mm | ⚠️ linear estimate |
| Rear Third | ThirdSpringRate | 0 | 410 N/mm | ⚠️ extrapolated |
| | | 1 | 470 N/mm | ⚠️ estimate |
| | | 2 | 530 N/mm | ✅ validated (IBT Mar19/20) |
| | | 3 | 590 N/mm | ⚠️ estimate |
| | | 4 | 650 N/mm | ⚠️ estimate |

> ⚠️ **Index→N/mm is assumed linear at 20 N/mm/step (heave) and 60 N/mm/step (third).**
> Only index 1 (heave) and index 2 (third) are validated from actual IBT data.
> **Changing heave or third index requires validating the N/mm at that index** — take a garage screenshot of ShockDeflStatic and TorsionBarDefl to verify.

**Torsion Bar (front — validated from garage screenshots):**
| Index | Stiffness | Source |
|-------|-----------|--------|
| 0 | 204.7 N/mm | estimated |
| 2 | 220.6 N/mm | ✅ garage: defl=12.1mm, cw=2669N |
| 5 | 266.9 N/mm | ✅ garage: defl=10.0mm |
| 9 | 317.7 N/mm | ✅ garage: defl=8.4mm |
| 11 | 317.7 N/mm | ✅ garage: defl=8.4mm |
| 15 | 360.7 N/mm | ✅ garage: defl=7.4mm |
| 18 | 444.8 N/mm | ✅ garage: defl=6.0mm |

> Rear torsion also validated at 4 index points. Between validated points: interpolated.

**Dampers (0–40 clicks per channel) — ⚠️ UNCALIBRATED:**
| Channel | iRacing Label | Solver Field | Current zeta | Status |
|---------|--------------|-------------|-------------|--------|
| LS Comp | LsCompDamp | `*_ls_comp_clicks` | 0.55 (BMW copy) | ❌ ESTIMATE |
| HS Comp | HsCompDamp | `*_hs_comp_clicks` | 0.20 (BMW copy) | ❌ ESTIMATE |
| LS Rebound | LsRbdDamp | `*_ls_rbd_clicks` | 0.40 (BMW copy) | ❌ ESTIMATE |
| HS Rebound | HsRbdDamp | `*_hs_rbd_clicks` | 0.18 (BMW copy) | ❌ ESTIMATE |

> BMW uses 0–11 clicks; Ferrari uses 0–40 clicks. Force per click is also different.
> **All damper click recommendations are directional at best until a click sweep is run.**
> See calibration procedure below.

**ARB:**
| Parameter | Sizes | Blades | Notes |
|-----------|-------|--------|-------|
| Front | Disc / A / B / C / D / E | 1–5 | Different size labels from BMW |
| Rear | Disc / A / B / C / D / E | 1–5 | |

> Stiffness per size NOT validated for Ferrari. BMW values used as starting point.
> **Changing ARB size on Ferrari requires a LLTD correlation session to verify.**

**Other:**
- Wing: 12–17 deg (1° steps) — 6 aero maps ✅ (axis convention validated)
- DF balance target: **48.3%** (calibrated 2026-04-02 from 17 Hockenheim sessions)
- Weight dist front: 47.6% (measured from IBT corner weights)
- Brake bias: 54.0% (measured from IBT BrakePressureBias)
- LLTD: ~0.510 (car constant, σ=0.0016 across 19 sessions) ✅
- m_eff front: 1439 kg ✅ (7 sessions); rear: 1500 kg ⚠️ (high variance)
- k-NN: 17 Hockenheim + 11 Sebring sessions

**What needs calibration:**
1. Damper zeta (click sweep — highest priority)
2. Heave index → N/mm above index 1 (run index 3, 5, 7 and screenshot ShockDeflStatic)
3. Rear m_eff (more sessions needed, high variance)
4. ARB stiffness per size label (LLTD sweep)

---

### 🟡 Cadillac V-Series.R — Calibration Status: **EXPLORATORY (2/6 steps, step 1 BLOCKED)**

**Springs (continuous):**
| Parameter | Range | Legal | Status |
|-----------|-------|-------|--------|
| Front heave | 20–200 N/mm | ✅ | ⚠️ m_eff=266kg (unverified) |
| Rear third | 100–1000 N/mm | ✅ | ⚠️ m_eff=2870kg (BMW copy) |
| Rear spring (coil) | 105–300 N/mm (5 N/mm steps) | ✅ | ❌ ESTIMATE |

> **m_eff rear = 2870 kg is a direct BMW copy. Cadillac has a different mass distribution.**
> **This will produce wrong heave frequency calculations and wrong spring rate recommendations.**

**Torsion Bar:**
| Parameter | Options | Status |
|-----------|---------|--------|
| Front torsion OD | **NOT SET** (empty array) | ❌ MISSING |
| Rear torsion | Not applicable (coil rear) | — |

> `torsion_od_options = []` means the solver falls back to continuous torsion model.
> Cadillac discrete torsion bar OD values NOT populated in `cars.py`.
> From the Cadillac manual: ODs are [13.90, 14.34, 14.76] mm — **these need to be added.**

**Dampers (1–11 clicks):**
| Channel | Status |
|---------|--------|
| All 4 channels | ❌ zeta = BMW copies (0.55/0.20/0.40/0.18), `zeta_is_calibrated=False` |

**ARB:** Same size labels/stiffness as BMW (unvalidated for Cadillac)

**Other:**
- Wing: 12–17 deg — aero maps present ✅
- DF balance target: 52.0% (estimated from aero map sweep only, not validated)
- Weight dist front: 48.5% (from Cadillac manual — unverified in IBT)
- Brake bias: 47.5% (from manual — unverified)
- Cadillac adapter bug: ✅ FIXED (c63c725) — was using BMW adapter

**What needs calibration (priority order):**
1. m_eff front and rear (heave sweep — 3 sessions)
2. Torsion bar OD options (add [13.90, 14.34, 14.76] to cars.py)
3. DF balance target (run sessions at different RH, check aero map)
4. Damper zeta (click sweep)
5. Weight dist front (garage screenshot of corner weights)

---

### ⚪ Porsche 963 — Calibration Status: **FULLY CALIBRATED (6/6 steps unblocked)**

*Last verified: 2026-04-04. All calibration data flows through solver and pipeline at runtime.*

**Fixes applied (2026-04-04):**
- Static RH model: full 4-variable regression (pushrod+heave+perch+camber). Previously missing terms caused 60mm error.
- Heave sigma target: relaxed from 8mm to 10mm. At 10mm, pipeline gives 300 N/mm — physically correct.
- Modifier scaling: fixed to use 10% of spring range as base increment, not range minimum.
- BMW deflection coefficients (defl_max_intercept, slider_perch_coeff, slider_intercept, slider_heave_coeff) zeroed — Multimatic chassis has different internal geometry.
- Rear pushrod default: corrected to +24.0mm (was inheriting BMW's -29.0mm via class default).
- Rear third spring minimum: set to 80 N/mm (was 0, causing solver to recommend 10 N/mm). Real fix: calibrate rear_m_eff_kg.
- Damper coefficients: kept as BMW shim-stack values (conservative bias safer than unvalidated DSSV estimates). Damper solver bypasses them via calibrated zeta. Heave solver uses HS coefficients for excursion only.

**Current evidence:** 35 Algarve observations (10 unique setups), 13 deltas, 2 Sebring sessions.
RH model calibrated from 13 garage screenshots (R²=0.996 front, R²=0.972 rear).
Deflection model calibrated (R²=1.00). Roll gradient: 0.184 deg/g. LLTD: 0.502 (7 sessions).
Damper zeta calibrated: LS=0.618, HS front=0.300, HS rear=0.282 (35 sessions).

**Step status (verified end-to-end 2026-04-04 — solver reads all values at runtime):**
| Step | Status | What's calibrated | Source |
|------|--------|-------------------|--------|
| 1 Rake/RH | ✅ UNBLOCKED | aero_compression (front=16.0mm rear=23.2mm), RH model (R²=0.990), pushrod (R²=1.0) | models.json (dynamic) |
| 2 Heave | ✅ UNBLOCKED | spring_rates, deflection model (R²=1.00, 10 setups), m_eff front=498kg | models.json (dynamic) |
| 3 Corner Springs | ✅ UNBLOCKED | spring_rates, front roll spring=100 N/mm (no torsion bar) | cars.py (static) |
| 4 ARBs | ✅ UNBLOCKED | LLTD target=0.502 (7 sessions), ARB stiffness [0,600]/[0,150,300,450] | models.json (LLTD), cars.py (ARB stiffness) |
| 5 Geometry | ✅ UNBLOCKED | roll gains front=0.60, rear=0.48 | cars.py (static — RG too noisy for auto-calibrate) |
| 6 Dampers | ✅ UNBLOCKED | zeta LS=0.618, HS front=0.300, HS rear=0.282 (35 sessions) | models.json (dynamic) |

**Data flow verified:** Solver and pipeline call `apply_to_car()` after `get_car()`, loading
calibration models from `data/calibration/porsche/models.json`. Damper solver reads calibrated
zeta targets (not hardcoded physics defaults). Running `learner.ingest` or `auto_calibrate`
preserves existing zeta and LLTD values in models.json. Values auto-update as you add IBTs.

**Calibration models fitted (from auto_calibrate):**
| Model | R² | RMSE | Sessions |
|-------|----|------|----------|
| rear_ride_height | 0.990 | 0.05 mm | 6 |
| rear_shock_defl_static | 0.998 | 0.06 mm | 6 |
| heave_spring_defl_static | 1.000 | 0.00 mm | 6 |
| heave_spring_defl_max | 0.996 | 0.21 mm | 6 |

**Springs (continuous, corrected 2026-04-04 from real garage data):**
| Parameter | Range | Status |
|-----------|-------|--------|
| Front heave | 150–600 N/mm | ✅ m_eff=498kg (calibrated from 2 Sebring sessions) |
| Front roll spring | 100–320 N/mm | ⚠️ model stores single baseline (100), not range |
| Rear L/R spring | 105–280 N/mm | ⚠️ per-side with individual perch offsets (-150 to +150) |
| Rear third | 80–800 N/mm | ⚠️ m_eff=800kg (ESTIMATE — needs rear third sweep per Procedure 1). Min 80 prevents pathological recommendation. |

**Front Corner Stiffness:**
- Porsche has **NO front torsion bar OD adjustment**. Front corner stiffness comes from the **roll spring** (100–320 N/mm).
- `front_torsion_c=0.0`, `front_torsion_od_options=[]` — this is CORRECT (not missing).
- The solver falls back to `front_roll_spring_rate_nmm` when torsion_c is zero.

**Dampers (4 separate systems — DSSV spool valve):**
| System | Channels | Range | Modeled? |
|--------|----------|-------|----------|
| Front heave | LS comp, HS comp, LS rbd, HS rbd | 0–11 | ✅ modeled as main damper |
| Front roll | LS damping, HS damping, HS damp slope | 0–11 | ⚠️ LS/HS modeled, HS slope NOT modeled |
| L/R rear | LS comp, HS comp, HS comp slope, LS rbd, HS rbd | 0–11 | ⚠️ LS/HS modeled, HS comp slope NOT modeled |
| Rear 3rd | LS comp, HS comp, LS rbd, HS rbd | 0–5 | ❌ NOT modeled — separate damper system |

> **DSSV spool-valve dampers** have more progressive force curves than BMW's shim stacks.
> The linear force-per-click model is a rougher approximation for Porsche than for BMW/Cadillac.
> Zeta values calibrated from 35 sessions (LS=0.618, HS front=0.300, HS rear=0.282).
> A proper click sweep (Procedure 6) with varied click settings would refine force-per-click.

**ARB (corrected 2026-04-04):**
- Front: Disconnected / Connected, adj 1–13 (was incorrectly 1–5)
- Rear: Disconnected / Soft / Medium / Stiff, adj 1–16 (was incorrectly 1–5)
- Stiffness derived from LLTD response: front [0, 600], rear [0, 150, 300, 450] — ARBs are very weak on Porsche (LLTD changes <0.5% across full range)

**Other:**
| Parameter | Value | Status |
|-----------|-------|--------|
| Wing | 12–17 deg | ✅ 6 aero maps present |
| Weight dist front | 47.1% | ✅ calibrated from corner weights |
| DF balance target | 50.5% | ⚠️ estimate — run Procedure 4 to refine |
| LLTD target | 0.502 | ✅ calibrated from 7 sessions (dynamic, auto-updates) |
| CG height | 345mm | ⚠️ estimate |
| Brake bias | 46.0% | ⚠️ from manual, unverified |
| Roll perch offset | 14–16 | Not modeled (does not block solver) |
| Pushrod geometry | front_pushrod_to_rh=0.549 | ✅ calibrated from 3-point sweep |

**Improvements that would refine accuracy (none are blockers):**
1. **DF balance** (Procedure 4): 1 session, 8+ laps. Current 50.5% is an estimate.
2. **Rear m_eff sweep** (Procedure 1 Phase B): 3 sessions varying rear third (50/400/800).
   Current rear m_eff=800kg is an estimate. Would improve heave spring sizing accuracy.
3. **Damper click sweep** (Procedure 6): 6 sessions (3 LS + 3 HS). Current zeta values are
   derived from 35 sessions at the same click settings — a proper sweep with varied clicks
   would give the force-per-click relationship and refine the DSSV damper model.
4. **Blade curve verification**: 3 sessions at Stiff adj 1/8/16 to verify whether blade
   scaling is linear or cosine (`I(phi) = (Ix+Iy)/2 + (Ix-Iy)/2 * cos(2*phi)`).

**All Porsche-specific parameters now computed by solver (verified 2026-04-04):**

| Parameter | Range | Value | Physics |
|-----------|-------|-------|---------|
| Front roll spring | 100–320 N/mm | 100 (frequency-targeted, at range minimum) | Same as corner spring — targets isolation from bump frequency. Snapped to 10 N/mm garage steps |
| Front roll LS | 0–11 | 5 | 30% of heave damper LS force as supplementary roll damping |
| Front roll HS | 0–11 | 2 | 30% of heave damper HS force |
| Front roll HS slope | 0–11 | 11 | Matched to track surface HS slope from p99/p95 ratio |
| Rear roll LS | 0–11 | 3 | 50% of front roll (softer for rear traction) |
| Rear roll HS | 0–11 | 1 | 50% of front roll HS |
| Rear 3rd LS comp | 0–5 | 4 | Calibrated rear zeta (0.618) applied to third spring natural freq |
| Rear 3rd HS comp | 0–5 | 2 | Calibrated rear zeta (0.282) applied to third spring natural freq |
| Rear 3rd LS rbd | 0–5 | 5 | Rebound ratio from rear corner physics (1.17x comp) |
| Rear 3rd HS rbd | 0–5 | 5 | Rebound ratio from rear corner physics (3.0x comp, clamped to 5) |
| L/R rear HS comp slope | 0–11 | 11 | From _hs_slope_from_surface() per-axle |
| .sto output | 23 channels | All mapped | Front heave (4), front roll (3), L/R rear (5+5), rear roll (2), rear 3rd (4) |

**Physics approach for roll dampers:** Roll dampers supplement the heave dampers' roll control.
The heave dampers already resist roll through opposite-phase motion. The roll damper provides
additional roll-specific damping at 30% of the heave force (front) and 15% (rear). This avoids
over-damping roll while still controlling weight transfer rate. The 30% factor is derived from
the industry practice of supplementary roll dampers providing 20-40% additional roll resistance.

**Known tuning items (physics-derived, may refine with data):**
- Roll damper 30% supplement factor — can be refined from lateral g transient response
- Rear 3rd force-per-click at 2x main damper — estimated from 6-click vs 12-click range scaling
- Front roll spring at range minimum (100) — solver frequency targeting computes rate <= 100, so no optimization benefit within current range. A higher target frequency would push this up

---

### 🟢 Acura ARX-06 — Calibration Status: **EXPLORATORY (0/6 steps — ALL BLOCKED)**

**Springs — DIFFERENT ARCHITECTURE:**
| Parameter | Range | Status |
|-----------|-------|--------|
| Front heave | 90–400 N/mm | ⚠️ m_eff=450kg (unverified) |
| Rear third | 60–300 N/mm | ⚠️ m_eff=220kg (much lighter than BMW — this looks right for Acura's rear layout) |

> Unlike BMW/Ferrari (heave is a secondary spring above the corner), Acura's front has
> an **active heave damper** — the "spring rate" acts as a stiffness setting on a
> hydraulic system, not a passive slider. The deflection model is not the same.
> Current heave deflection calculations use the BMW passive slider model — incorrect.

**Torsion Bar:**
| Axle | Options | Status |
|------|---------|--------|
| Front | [13.9, 14.34, 14.76, 15.14, 15.51, 15.86] mm | ⚠️ populated, unvalidated |
| Rear | [13.9–18.2mm] full range | ⚠️ populated, unvalidated |

> Acura has **torsion bars on BOTH axles** (BMW only has front torsion bars). Rear torsion
> stiffness affects rear roll resistance AND couples with the heave damper system.

**Dampers (1–10 clicks per channel):**
- `has_roll_dampers=True` — Acura has dedicated roll damper channels
- Roll damper range: 1–10 clicks (front and rear)
- All zeta values: BMW copies — ❌ invalid

> The roll dampers are a 5th degree of freedom for damping that BMW doesn't have.
> The solver currently does NOT route roll damper recommendations to the correct parameter.

**Wing: 6.0–10.0 deg (0.5° steps)**
> **This is different from all other GTP cars (12–17 deg).** Acura runs significantly lower
> wing angles. The aero map coverage matches (9 maps from 6.0–10.0 deg ✅).

**Other:**
- DF balance target: 49.0% (estimate)
- Weight dist front: 47.0% (from manual, unverified)
- Rear motion ratio: 1.0 (vs BMW's 0.6) — Acura rear geometry is fundamentally different

---

## Calibration Procedures

Each procedure tells you exactly what to change in the iRacing garage, what to keep
constant, how many laps to run, and which CLI command to run afterward. Follow the
order — later steps depend on earlier ones being correct.

### Physics Background

These procedures are based on classical vehicle dynamics methodology:
- **m_eff measurement:** Vary spring stiffness, measure deflection change at speed.
  `deflection = F_aero / K_spring`. Use softest available springs to maximize
  deflection signal. (Ref: OptimumG Tech Tip Part 1, HPA Academy downforce calculation)
- **ARB stiffness:** `roll_gradient = m*g*h_cg / K_total_roll`. Vary ONE end at a time
  to isolate front/rear contribution. (Ref: OptimumG "Bar Talk", Suspension Secrets)
- **Damper zeta:** Target ~0.65 critical damping at low speed, ~0.3 at high speed.
  `Cc = 2 * sqrt(K_wheel * m_sprung_corner)`, `zeta = C_actual / Cc`.
  (Ref: OptimumG Tech Tip Part 3, Racecomp Engineering, Far North Racing)
- **Session count:** iRacing is deterministic — no measurement noise. 3 data points
  (min/mid/max of range) is sufficient for linear regressions. We use 3 per sweep,
  not 5, to minimize total sessions without losing accuracy.

---

### Procedure 1: Effective Mass (m_eff) — Unlocks Steps 1-3

**Purpose:** Measures how much the car compresses under aero downforce at a given
spring rate. The physics: at speed, `aero_compression_mm = F_aero / K_heave`. By
varying K_heave and measuring the resulting dynamic ride height, we back-calculate
F_aero, then `m_eff = F_aero / g`.

**Why softest springs first:** Softer springs produce larger deflections, making the
measurement more accurate. A 50 N/mm spring deflects 4x more than a 200 N/mm spring
under the same aero load — this magnifies the signal.

**What you need:** 3 sessions varying front heave + 3 sessions varying rear third = 6 total.
Keep EVERYTHING else identical within each set. Use min, mid, and max of the spring range.

**Phase A: Front heave sweep (3 sessions)**

**BMW / Cadillac (Dallara, range 0–900 N/mm):**
| Session | Front Heave (N/mm) | Keep constant |
|---------|-------------------|---------------|
| 1 | 50 (soft — maximizes deflection) | Rear third: 500, Torsion OD: 15.14, ARBs: Soft blade 3, Dampers: baseline |
| 2 | 300 (mid) | Same |
| 3 | 700 (stiff) | Same |

**Porsche (Multimatic, range 150–600 N/mm):**
| Session | Front Heave (N/mm) | Keep constant |
|---------|-------------------|---------------|
| 1 | 150 (min — maximizes deflection) | Rear third: 400, Roll spring: 200, Rear spring: 190, ARBs: Connected adj 7, Dampers: baseline |
| 2 | 350 (mid) | Same |
| 3 | 600 (max) | Same |

**Ferrari (indexed, range 0–8):**
| Session | Front Heave Index | Keep constant |
|---------|------------------|---------------|
| 1 | 0 (softest) | Rear third: index 2, Torsion: index 9, ARBs: C blade 3, Dampers: all 20 |
| 2 | 4 (mid) | Same |
| 3 | 8 (stiffest) | Same |

**Acura (ORECA, range 90–400 N/mm):**
| Session | Front Heave (N/mm) | Keep constant |
|---------|-------------------|---------------|
| 1 | 90 (min) | Rear third: 150, Front torsion OD: 14.34, ARBs: Soft blade 3, Dampers: all 5 |
| 2 | 220 (mid) | Same |
| 3 | 400 (max) | Same |

**Phase B: Rear third sweep (3 sessions)**

**BMW / Cadillac:**
| Session | Rear Third (N/mm) | Keep constant |
|---------|-------------------|---------------|
| 4 | 100 (soft) | Front heave: 300, Torsion OD: 15.14, ARBs: Soft blade 3, Dampers: baseline |
| 5 | 400 (mid) | Same |
| 6 | 800 (stiff) | Same |

**Porsche:**
| Session | Rear Third (N/mm) | Keep constant |
|---------|-------------------|---------------|
| 4 | 50 (soft) | Front heave: 350, Roll spring: 200, Rear spring: 190, ARBs: Connected adj 7, Dampers: baseline |
| 5 | 400 (mid) | Same |
| 6 | 800 (max) | Same |

**Ferrari:**
| Session | Rear Third Index | Keep constant |
|---------|-----------------|---------------|
| 4 | 0 (softest) | Front heave: index 4, Torsion: index 9, ARBs: C blade 3, Dampers: all 20 |
| 5 | 2 (mid) | Same |
| 6 | 4 (stiffest) | Same |

**Acura:**
| Session | Rear Third (N/mm) | Keep constant |
|---------|-------------------|---------------|
| 4 | 60 (soft) | Front heave: 220, Front torsion OD: 14.34, ARBs: Soft blade 3, Dampers: all 5 |
| 5 | 180 (mid) | Same |
| 6 | 300 (max) | Same |

**Per session:**
1. Set the garage exactly as listed. Do NOT change anything else.
2. Go on track. Run **5+ clean laps** at race pace (no offs, no contact).
   You need consistent high-speed running so the aero load is representative.
3. Come back to pits. The IBT is saved automatically.

**Why race pace matters:** Aero compression scales with V². At 100 kph you get 1/5 the
compression of 230 kph. Slow laps give tiny deflection deltas that are hard to fit.

**After all 6 sessions, run:**
```bash
python -m car_model.auto_calibrate --car <car> --ibt-dir <folder_with_all_6_ibts>
```

**What it does:** For each session, extracts mean dynamic ride height at high speed
from the telemetry and the static ride height from the garage. The difference is the
aero compression: `compression = static_rh - dynamic_rh_at_speed`. Plots compression
vs spring rate. Fits `F_aero = compression * K_spring` (should be constant across sessions
if only the spring changed). Then `m_eff = F_aero / g`.

**Check the result:**
```bash
python -m car_model.auto_calibrate --car <car> --status
```
Expected m_eff ranges (from physics — these are NOT arbitrary):
- **Front m_eff:** 150–600 kg. This represents aero downforce per front corner / g.
  A GTP at 230 kph generates ~3000–6000 N total front DF → 750–1500 N per corner → 150–600 kg equivalent.
- **Rear m_eff:** 500–3000 kg. Rear tends to be higher because the diffuser generates
  more force than the front splitter, and it acts through the third spring (not per-corner).

If a value is negative or >5000 kg, one of the sessions likely had an incident,
the spring wasn't actually changed, or the ride height telemetry was corrupted.

---

### Procedure 2: Spring Index Validation (Ferrari only) — Refines Steps 2-3

**Purpose:** Ferrari uses indexed spring controls (0, 1, 2...) not N/mm values.
The solver needs to know the actual N/mm at each index. Only 2 of 9 heave
indices and 1 of 5 third indices are validated — the rest are linear estimates.

**Physics:** In the iRacing garage, `ShockDeflStatic = CornerWeight / SpringRate`.
By reading both from the IBT header at different indices, we solve for the spring
rate: `K = CornerWeight / ShockDeflStatic`. Three index points (min, mid, max)
are enough to fit the index→N/mm curve and check if it's linear or non-linear.

**Heave index sweep (3 sessions, keep everything else constant):**
| Session | Heave Index | Keep constant |
|---------|-------------|---------------|
| 1 | 0 (softest) | Third: index 2, Torsion: index 9, ARBs: C blade 3, Dampers: all 20 |
| 2 | 4 (mid) | Same |
| 3 | 8 (stiffest) | Same |

**Third index sweep (3 sessions):**
| Session | Third Index | Keep constant |
|---------|-------------|---------------|
| 4 | 0 | Heave: index 4, Torsion: index 9, ARBs: C blade 3, Dampers: all 20 |
| 5 | 2 | Same |
| 6 | 4 | Same |

**Per session:** 3+ clean laps, race pace, save IBT.

**After all sessions:**
```bash
python -m car_model.auto_calibrate --car ferrari --ibt-dir <folder>
```

**What it does:** Reads ShockDeflStatic and CornerWeight from each IBT header,
computes `k = weight / deflection` at each index, fits the index→N/mm lookup table.
With 3 points you can detect whether the spacing is linear (20 N/mm per step as
currently assumed) or non-linear.

---

### Procedure 3: LLTD / ARB Stiffness — Unlocks Step 4

**Purpose:** Determines how much roll stiffness each ARB size/blade combination adds.
Without this, the solver can't target a specific lateral load transfer distribution.

**Key DOE principle: vary ONE end at a time.** If you change both front and rear ARBs
simultaneously, you can only measure TOTAL roll stiffness change — you cannot separate
front from rear contribution. By sweeping front ARB alone (rear constant), then rear
ARB alone (front constant), you get independent front and rear stiffness measurements.

**Physics:**
```
roll_gradient (deg/g) = m * g * h_cg / K_total_roll
K_total_roll = K_spring_front + K_spring_rear + K_arb_front + K_arb_rear
```
With springs constant:
```
ΔK_total = -m * g * h_cg * Δ(1/roll_gradient)
```
If only front ARB changed: `ΔK_total = ΔK_arb_front` (directly measurable!)

**Phase A: Front ARB sweep (3 sessions, rear ARB constant)**

**BMW / Cadillac:**
| Session | Front ARB | Front Blade | Rear ARB (CONSTANT) | Keep constant |
|---------|-----------|-------------|---------------------|---------------|
| 1 | Disconnected | 1 | Soft blade 3 | Heave: 300, Third: 500, Torsion: 15.14, Dampers: baseline |
| 2 | Soft | 3 | Soft blade 3 | Same |
| 3 | Stiff | 5 | Soft blade 3 | Same |

**Porsche:**
| Session | Front ARB | Front Adj | Rear ARB (CONSTANT) | Keep constant |
|---------|-----------|-----------|---------------------|---------------|
| 1 | Disconnected | 1 | Soft adj 8 | Heave: 350, Third: 400, Roll spring: 200, Rear spring: 190, Dampers: baseline |
| 2 | Connected | 5 | Soft adj 8 | Same |
| 3 | Connected | 13 | Soft adj 8 | Same |

**Ferrari:**
| Session | Front ARB | Front Blade | Rear ARB (CONSTANT) | Keep constant |
|---------|-----------|-------------|---------------------|---------------|
| 1 | Disconnected | 1 | C blade 3 | Heave: idx 4, Third: idx 2, Torsion: idx 9, Dampers: all 20 |
| 2 | C | 3 | C blade 3 | Same |
| 3 | E | 5 | C blade 3 | Same |

**Acura:**
| Session | Front ARB | Front Blade | Rear ARB (CONSTANT) | Keep constant |
|---------|-----------|-------------|---------------------|---------------|
| 1 | Disconnected | 1 | Soft blade 3 | Heave: 220, Third: 150, Torsion: 14.34, Dampers: all 5 |
| 2 | Soft | 3 | Soft blade 3 | Same |
| 3 | Stiff | 5 | Soft blade 3 | Same |

**Phase B: Rear ARB sweep (3 sessions, front ARB constant)**

**BMW / Cadillac:**
| Session | Front ARB (CONSTANT) | Rear ARB | Rear Blade | Keep constant |
|---------|---------------------|----------|------------|---------------|
| 4 | Soft blade 3 | Soft | 1 | Heave: 300, Third: 500, Torsion: 15.14, Dampers: baseline |
| 5 | Soft blade 3 | Medium | 3 | Same |
| 6 | Soft blade 3 | Stiff | 5 | Same |

**Porsche:**
| Session | Front ARB (CONSTANT) | Rear ARB | Rear Adj | Keep constant |
|---------|---------------------|----------|----------|---------------|
| 4 | Connected adj 5 | Disconnected | 1 | Heave: 350, Third: 400, Roll spring: 200, Rear spring: 190, Dampers: baseline |
| 5 | Connected adj 5 | Medium | 8 | Same |
| 6 | Connected adj 5 | Stiff | 16 | Same |

**Ferrari:**
| Session | Front ARB (CONSTANT) | Rear ARB | Rear Blade | Keep constant |
|---------|---------------------|----------|------------|---------------|
| 4 | C blade 3 | Disconnected | 1 | Heave: idx 4, Third: idx 2, Torsion: idx 9, Dampers: all 20 |
| 5 | C blade 3 | C | 3 | Same |
| 6 | C blade 3 | E | 5 | Same |

**Acura:**
| Session | Front ARB (CONSTANT) | Rear ARB | Rear Blade | Keep constant |
|---------|---------------------|----------|------------|---------------|
| 4 | Soft blade 3 | Soft | 1 | Heave: 220, Third: 150, Torsion: 14.34, Dampers: all 5 |
| 5 | Soft blade 3 | Medium | 3 | Same |
| 6 | Soft blade 3 | Stiff | 5 | Same |

**Per session:** 5+ clean laps with hard cornering. The car needs sustained lateral g
(>1.5g) to generate meaningful roll gradient. Tracks with long, fast sweepers work best.
Short chicanes don't generate enough steady-state roll data.

**After all 6 sessions:**
```bash
python -m car_model.auto_calibrate --car <car> --ibt-dir <folder_with_all_arb_ibts>
```

Also ingest each session for the learner (needed for LLTD calibration later):
```bash
for f in <folder>/*.ibt; do python -m learner.ingest --car <car> --ibt "$f" --all-laps; done
```

**What it does:** auto_calibrate extracts roll gradient from each session, groups by
spring config (which is constant across all 6), and computes total roll stiffness
(K_total) per ARB configuration. Because front and rear are swept independently,
the deltas from Phase A give front ARB stiffness and Phase B gives rear ARB stiffness.

It then compares measured K_total deltas against the model's predicted ARB stiffness.

**If the model matches measured within 20%:** `arb_calibrated=True`, Step 4 unblocked.

**If mismatch (>20% error):** The gate stays closed. This means the ARB stiffness
values in `car_model/cars.py` are wrong for this car. You'll need to manually update
`front_stiffness_nmm_deg` and `rear_stiffness_nmm_deg` in the car's `ARBModel` using
the measured deltas as a guide.

**After 10+ total sessions are ingested** (from this + earlier procedures), also run:
```bash
python -m validation.calibrate_lltd --car <car> --track <track>
```
This fits the optimal LLTD target from lap time correlation (quadratic fit of LLTD vs lap time).
The LLTD that minimizes lap time becomes the solver's target for Step 4.

---

### Procedure 4: DF Balance Target — 1 session

**Purpose:** Find the actual aero balance the car runs at competitive ride heights.
Wrong balance target = solver recommends wrong wing angle.

**What you need:** 1 session with a middle-of-the-road setup. No extreme settings.

**Suggested baseline setup:**

| Parameter | BMW/Cadillac | Porsche | Ferrari | Acura |
|-----------|-------------|---------|---------|-------|
| Wing | 15 deg | 15 deg | 15 deg | 8 deg |
| Front heave | 300 N/mm | 350 N/mm | Index 4 | 220 N/mm |
| Rear third | 500 N/mm | 400 N/mm | Index 2 | 150 N/mm |
| Front ARB | Soft blade 3 | Connected adj 7 | C blade 3 | Soft blade 3 |
| Rear ARB | Medium blade 3 | Soft adj 8 | C blade 3 | Medium blade 3 |

**Run 8+ clean laps** at race pace. Save the IBT.

**After the session:**
```bash
python -m learner.ingest --car <car> --ibt <session.ibt> --all-laps
python -m car_model.auto_calibrate --car <car> --ibt <session.ibt>
```

**What it does:** Extracts mean dynamic front/rear ride height at high speed from IBT,
looks up the DF balance in the car's aero map at those ride heights
(`AeroSurface.df_balance(front_rh, rear_rh)`), averages across laps.

**Expected values:** 47–53% for GTP cars. If outside this range, either the ride heights
were extreme or the aero map axes might be swapped (check `aero_axes_swapped` in cars.py).

---

### Procedure 5: Roll Gains — Unlocks Step 5

**Purpose:** Measures how much camber changes per degree of body roll. Needed for the
wheel geometry solver to predict contact patch behavior through corners.

**Physics:** Roll gain = Δcamber / Δroll_angle. Typical values: 0.4–0.7 deg/deg for
double wishbone race car suspension. This is a geometry property of the suspension
linkage, not a setup parameter — it's the same regardless of springs/ARBs.

**What you need:** The sessions from Procedures 1 and 3 are usually sufficient.
The system needs 3+ sessions with measurable lateral g and consistent roll gradient.

**No extra sessions required** if you've already done Procedures 1 and 3.
Just run:
```bash
python -m car_model.auto_calibrate --car <car> --ibt-dir <folder_with_all_previous_ibts>
```

auto_calibrate checks roll gradient consistency across sessions (CV < 30%).
If consistent, it sets `roll_gains_calibrated=True` and Step 5 unblocks.

If the roll gradient data is too noisy (CV > 30%), you need more sessions with
harder cornering — the car needs to generate enough sustained lateral g (>1.5g)
for meaningful roll gradient extraction. Run laps on a track with long, fast sweepers.

---

### Procedure 6: Damper Zeta — Unlocks Step 6

**Purpose:** Determines the actual damping ratio at each click setting. Without this,
the solver returns baseline damper clicks instead of physics-derived recommendations.

**Physics background:**
```
Critical damping: Cc = 2 * sqrt(K_wheel * m_sprung_corner)
Damping ratio:    zeta = C_actual / Cc
```
Industry targets (OptimumG, Racecomp Engineering):
- **Low-speed (LS) ride:** zeta = 0.55–0.70 (controls weight transfer, body motion)
- **High-speed (HS) bump:** zeta = 0.25–0.35 (controls bump absorption, curb compliance)

In iRacing, click 0 = maximum damping (fully closed), higher clicks = softer.

**What you need:** Two phases — LS sweep and HS sweep. Keep everything else identical
across all sessions: same springs, ARBs, ride heights, fuel load.

**Phase A: LS Compression sweep (3 sessions)**

**BMW / Cadillac (0–11 click range):**
| Session | Front LS Comp | Rear LS Comp | Keep constant |
|---------|--------------|--------------|---------------|
| 1 | 0 (max damping) | 0 | Heave: 300, Third: 500, Torsion: 15.14, ARBs: Soft/3, HS comp: 5, HS slope: 10, All rbd: baseline |
| 2 | 5 (mid) | 5 | Same |
| 3 | 11 (min damping) | 11 | Same |

**Porsche (0–11 DSSV):**
| Session | Front Heave LS Comp | Rear LS Comp | Keep constant |
|---------|--------------------|--------------| --------------|
| 1 | 0 | 0 | Heave: 350, Third: 400, Roll spring: 200, ARBs: Connected/7, HS/slope/rbd: baseline |
| 2 | 5 | 5 | Same |
| 3 | 11 | 11 | Same |

**Ferrari (0–40):**
| Session | Front LS Comp | Rear LS Comp | Keep constant |
|---------|--------------|--------------|---------------|
| 1 | 0 | 0 | Heave: idx 4, Third: idx 2, Torsion: idx 9, ARBs: C/3, HS comp: 20, HS slope: 5, All rbd: 20 |
| 2 | 20 | 20 | Same |
| 3 | 40 | 40 | Same |

**Acura (1–10):**
| Session | Front LS Comp | Rear LS Comp | Keep constant |
|---------|--------------|--------------|---------------|
| 1 | 1 (max) | 1 | Heave: 220, Third: 150, Torsion: 14.34, ARBs: Soft/3, HS: 5, All rbd: 5 |
| 2 | 5 (mid) | 5 | Same |
| 3 | 10 (min) | 10 | Same |

**Phase B: HS Compression sweep (3 sessions)**

**BMW / Cadillac:**
| Session | Front HS Comp | Rear HS Comp | Keep constant |
|---------|--------------|--------------|---------------|
| 4 | 0 | 0 | Heave: 300, Third: 500, Torsion: 15.14, ARBs: Soft/3, LS comp: 5, HS slope: 10, All rbd: baseline |
| 5 | 5 | 5 | Same |
| 6 | 11 | 11 | Same |

**Porsche:**
| Session | Front Heave HS Comp | Rear HS Comp | Keep constant |
|---------|--------------------|--------------| --------------|
| 4 | 0 | 0 | Heave: 350, Third: 400, Roll spring: 200, ARBs: Connected/7, LS/slope/rbd: baseline |
| 5 | 5 | 5 | Same |
| 6 | 11 | 11 | Same |

**Ferrari:**
| Session | Front HS Comp | Rear HS Comp | Keep constant |
|---------|--------------|--------------|---------------|
| 4 | 0 | 0 | Heave: idx 4, Third: idx 2, Torsion: idx 9, ARBs: C/3, LS comp: 20, HS slope: 5, All rbd: 20 |
| 5 | 20 | 20 | Same |
| 6 | 40 | 40 | Same |

**Acura:**
| Session | Front HS Comp | Rear HS Comp | Keep constant |
|---------|--------------|--------------|---------------|
| 4 | 1 | 1 | Heave: 220, Third: 150, Torsion: 14.34, ARBs: Soft/3, LS: 5, All rbd: 5 |
| 5 | 5 | 5 | Same |
| 6 | 10 | 10 | Same |

**Per session:** 5+ clean laps at race pace including curbs/bumps. The car will feel
very different at each extreme:
- Click 0 (max damping): locked, harsh, poor bump absorption but stable platform
- Max click (min damping): floaty, wallowy, good bump compliance but unstable
Drive consistently regardless of feel. That's the experiment.

**After all 6 sessions, ingest each one:**
```bash
for f in <folder>/*.ibt; do python -m learner.ingest --car <car> --ibt "$f" --all-laps; done
```

**Then run the damper calibration:**
```bash
python -m validation.calibrate_dampers --car <car> --track <track>
```

**What it does:** Loads all ingested observations, identifies sessions where only damper
clicks changed. For each session, extracts shock velocity histograms, platform stability
metrics (RH variance, settle time after transients), and shock oscillation frequency.
Maps the click settings to damping force using the force-per-click model, computes zeta
at each click setting, and identifies the clicks that produce the target zeta values.

**Check the result:**
```bash
python -m car_model.auto_calibrate --car <car> --status
```
Look for `damper_zeta: calibrated` with values:
- **LS front zeta:** expect 0.55–0.70 (ride damping, weight transfer control)
- **LS rear zeta:** expect 0.35–0.55 (slightly lighter than front for traction)
- **HS front zeta:** expect 0.25–0.35 (bump absorption)
- **HS rear zeta:** expect 0.15–0.25 (maximum compliance over bumps)

If front LS zeta > 0.85 or < 0.3, the force-per-click estimate is probably wrong.

> **Dependency:** Do Procedures 1 and 2 (if Ferrari) BEFORE this one.
> `Cc = 2 * sqrt(K * m)`. Wrong m_eff or wrong spring rate means wrong critical damping
> force, which means every zeta value derived from the click sweep will be off.

---

### Procedure 7: Full Calibration Run — Generates Setup

After completing the procedures above, produce a setup:

```bash
# Check what's calibrated:
python -m car_model.auto_calibrate --car <car> --status

# Build track profile (if not already done for this track):
python -m track_model.build <session.ibt>

# Produce setup from your latest IBT:
python -m pipeline.produce --car <car> --ibt <session.ibt> --wing <angle> --sto output.sto
```

The pipeline will run all calibrated solver steps and skip blocked ones (printing
calibration instructions for anything still missing). Steps 1-3 typically unblock
after Procedure 1. Steps 4-6 require Procedures 3, 5, and 6 respectively.

---

### Total Session Budget

| Procedure | Sessions | What changes | Unblocks |
|-----------|----------|-------------|----------|
| 1. m_eff (front + rear) | 6 | Front heave (3), then rear third (3) | Steps 1-3 |
| 2. Spring index (Ferrari only) | 6 | Heave index (3) + third index (3) | Steps 2-3 accuracy |
| 3. ARB stiffness | 6 | Front ARB only (3), then rear ARB only (3) | Step 4 |
| 4. DF balance | 1 | Nothing (measurement only) | Wing accuracy |
| 5. Roll gains | 0 | (reuses sessions from 1 + 3) | Step 5 |
| 6. Damper zeta | 6 | LS comp (3), then HS comp (3) | Step 6 |
| **Total (non-Ferrari)** | **19 sessions** | | **All 6 steps** |
| **Total (Ferrari)** | **25 sessions** | | **All 6 steps** |

> Each "session" = go on track, run 5-8 clean laps, come back. At ~2 min/lap for GTP,
> that's ~10-15 min per session plus garage changes. Budget 5-6 hours total for a
> complete calibration sweep of a new car.

---

## What Happens When You Change A Torsion Bar

This is the most common misuse. The full cascade:

```
1. You change: Torsion bar OD (e.g., 13.9 → 14.34mm)
2. Front corner spring rate increases: ~204 → ~221 N/mm (+8%)
3. Front roll stiffness increases by same proportion
4. To maintain same LLTD: FARB blade must decrease OR RARB must increase
5. Front heave deflection under load DECREASES (stiffer front)
   → Car rides higher at front at same speed
   → Dynamic front RH increases by Δdefl = F_aero * (1/k_old - 1/k_new)
   → Typical: ~1-2mm RH change per torsion bar step
6. Static front RH TARGET must DECREASE by same amount to keep same dynamic RH
7. If static RH doesn't change: DF balance shifts (more front downforce)
8. If DF balance shifts: wing angle recommendation may change
9. Damper natural frequency has changed: zeta at current click settings is now different
   → You haven't changed damper clicks, but effective damping ratio has changed
```

**Minimum required actions after changing torsion bar OD:**
- [ ] Update static RH target: `static_rh_new = static_rh_old - Δdefl`
- [ ] Re-check ARB blade to maintain LLTD
- [ ] Re-run one IBT session to verify new dynamic RH
- [ ] Mark damper recommendations as EST until re-calibrated

---

## Quick Reference: Parameter Names (iRacing garage ↔ solver)

| iRacing Garage Label | Solver Field | Car |
|---------------------|-------------|-----|
| TorsionBarDiameter | `torsion_bar_od_mm` | BMW |
| HeaveSpringRate (front) | `front_heave_nmm` | All |
| ThirdSpringRate (rear) | `rear_third_nmm` | All |
| RearSpringRate | `rear_spring_nmm` | BMW/Porsche |
| ARBSize (front) | `front_arb_size` | All |
| ARBBlade (front) | `front_arb_blade` | All |
| ARBSize (rear) | `rear_arb_size` | All |
| ARBBlade (rear) | `rear_arb_blade` | All |
| RideHeight (front, static) | `front_rh_static` | All |
| RideHeight (rear, static) | `rear_rh_static` | All |
| AeroWingAngle | `wing_angle_deg` | All |
| LsCompDamp (per corner) | `lf/rf/lr/rr_ls_comp_clicks` | All |
| HsCompDamp (per corner) | `lf/rf/lr/rr_hs_comp_clicks` | All |
| LsRbdDamp (per corner) | `lf/rf/lr/rr_ls_rbd_clicks` | All |
| HsRbdDamp (per corner) | `lf/rf/lr/rr_hs_rbd_clicks` | All |
| DiffPreload | `diff_preload_nm` | All |
| DiffClutchPlates | `diff_clutch_plates` | BMW |
| FrontCamber | `front_camber_deg` | All |
| RearCamber | `rear_camber_deg` | All |
| FrontToe | `front_toe_mm` | All |
| RearToe | `rear_toe_mm` | All |
| BrakeBias | `brake_bias_pct` | All |
| TCGain | `tc_gain` | All |
| TCSlip | `tc_slip` | All |
| ShockDeflStatic | internal (deflection calibration) | All |
| TorsionBarDefl | internal (torsion stiffness calibration) | Ferrari |
| CarLeftRight | internal (LLTD measurement) | All |

---

## Calibration Session Budget Per Car

| Car | Steps 1-3 (m_eff) | Steps 4-5 (ARB + roll gains) | Step 6 (dampers) | Total to full calibration |
|-----|-------------------|------------------------------|------------------|--------------------------|
| BMW | Done ✅ | Done ✅ | Done ✅ | Done ✅ |
| Ferrari | Done ✅ | 6 sessions (ARB sweep) + LLTD | 6 sessions (LS + HS sweep) + 6 index validation | ~18 more |
| Cadillac | 6 sessions (heave + third sweep) | 6 sessions (ARB sweep) | 6 sessions (LS + HS sweep) | ~19 |
| Porsche | Done ✅ (10 setups) | Done ✅ (LLTD + ARB from telemetry) | Done ✅ (zeta from 35 sessions) | Done ✅ |
| Acura | 6 sessions (heave + third sweep) | 6 sessions (ARB sweep) | 6 sessions (LS + HS sweep) | ~19 |

To get a forecast for a specific (car, track) instead of the table-wide
estimates above, run:

```bash
python -m validation.calibration_confidence --car cadillac --track silverstone
python -m validation.calibration_confidence --car porsche --track algarve --gate-r2 0.95
```

The reporter re-fits the regression on bootstrapped subsets of the on-disk
calibration corpus, plots the LOO R² vs n_samples curve, and forecasts how
many additional sessions are needed to reach a given R² gate. Use it before
collecting more IBTs — sometimes the asymptote is already below the gate
(model is feature-limited, not data-limited).

---

## What "Calibrated" Actually Means Here

A parameter is **calibrated** when:
1. Its value was derived from IBT telemetry (not a manual estimate or BMW copy)
2. It has been validated against at least one independent session
3. The dependency chain below it has been checked — changing it doesn't silently break upstream assumptions

A parameter is **estimated** when:
- It was calculated from a physics formula with assumed constants
- It was copied from another car
- It was derived from a single session or single anchor point

The delta card confidence tiers (HIGH/MED/EST) reflect this directly.
If you see ⚠️ EST on a recommendation — the physics behind it hasn't been validated for that car.

---

*Last updated: 2026-04-27 by W9.2 (GT3 onboarding section appended)*
*Next review: after any spring, damper, or torsion bar calibration session, or when varied-spring GT3 IBT data lands*

---

## GT3 Onboarding (added 2026-04-27)

The GT3 Phase 2 work shipped across Waves 1–8 makes **BMW M4 GT3 EVO**, **Aston Martin Vantage GT3 EVO**, and **Porsche 911 GT3 R (992)** ingestible through the standard pipeline. Calibration accuracy is currently **"intercept-only"** — the auto-calibrate scaffolding (W7.2) accepts GT3 IBTs without crashing and applies a documented baseline, but real regression fits require **varied-spring IBT sweeps at a single track**. Until those land, GT3 setup recommendations carry an `ESTIMATE WARNINGS` block in the report and a `--force` flag is required to bypass the calibration gate.

### What's wired up (Wave 1–8)

| Subsystem | GT3 status |
|---|---|
| Calibration gate (`car_model/calibration_gate.py`) | Step 2 (Heave/Third) is `not_applicable`; cascade `{3:1, 4:3, 5:4, 6:3}`. |
| Solver chain (Steps 1, 3, 4, 5, 6) | Runs end-to-end on all 3 GT3 cars without crashing. Step 1 in **balance-only** mode, Step 3 in **GT3 paired-coil** arm. |
| Setup writer (`output/setup_writer.py`) | Per-car `_<CAR>_GT3_PARAM_IDS` dicts (BMW/Aston/Porsche). `.sto` round-trips through iRacing on all 3 (pending in-game QA). |
| Analyzer (extract / diagnose / causal_graph) | GT3 architecture detected; phantom heave-bottoming alarms suppressed. |
| Learner (observation / delta_detector / empirical_models) | GT3 setup keys recognised; corner-spring → RH variance fitter wired. |
| Watcher + desktop | CarPath dispatch (locale-independent); `class_filter=["GT3"]` available. |
| Team server (DB + aggregator) | `Observation.suspension_arch` column; per-arch empirical-fit partitioning. |
| Auto-calibrate (`car_model/auto_calibrate.py`) | **Intercept-only**. Real regression fits gated on varied-spring IBT capture (W10.1). |
| Garage prediction (`DirectRegression`) | Front/rear corner-spring + bump-rubber + splitter features wired in `_UNIVERSAL_POOL`. Coefficients all 0 until calibration data lands. |

### Required IBT capture per GT3 car

For each GT3 car (or to upgrade an existing GT3 car's calibration from intercept-only to fitted):

1. **Capture 5+ IBTs at the same track** with **varied front coil rates** covering the car's spring range. Suggested sweep:
   - **BMW M4 GT3 EVO:** `front_corner_spring_nmm` ∈ {190, 220, 260, 300, 340} N/mm (range = (190, 340), step 10).
   - **Aston Vantage GT3 EVO:** {180, 220, 260, 300, 320} N/mm.
   - **Porsche 992 GT3R:** {170, 220, 260, 300, 320} N/mm.
   Hold all other parameters constant across the sweep — driver style, tyres, fuel, ARB blades. The fitter accepts any single track but variance must come from front coil only.
2. **Run the auto-calibrator:**
   ```bash
   python -m car_model.auto_calibrate --car bmw_m4_gt3 --ibt-dir data/gt3_ibts/
   ```
   The fitter produces compliance regressions (`inv_front_corner_spring → front_rh_std_mm`, etc.).
3. **Apply the fits with `--apply`:**
   ```bash
   python -m car_model.auto_calibrate --car bmw_m4_gt3 --ibt-dir data/gt3_ibts/ --apply
   ```
   `apply_to_car` short-circuits today with a "intercept-only" applied note plus a TODO(W10.1) marker; once the real fits land they'll write to `car.corner_spring.front_baseline_rate_nmm` and the per-axis compliance coefficients in `RideHeightModel` / `DeflectionModel`.
4. **Verify** with the regression test suite:
   ```bash
   pytest tests/test_setup_regression.py -v
   ```
   The 3 GT3 baseline `.sto` fixtures committed under `tests/fixtures/baselines/` lock the current intercept-only output. After calibration the fixtures must be regenerated (see test docstring).

### Currently shipped (intercept-only, BoP 2026 S2 P3)

| Car | IBT(s) | Track(s) sampled |
|---|---|---|
| BMW M4 GT3 EVO | 2 | Spielberg (`bmwm4gt3_spielberg gp 2026-04-26 21-34-43.ibt`) + Nürburgring (byte-identical setup; cannot back-solve aero compression with same setup at 2 tracks) |
| Aston Vantage GT3 EVO | 1 | Spielberg (`amvantageevogt3_spielberg gp 2026-04-26 21-25-55.ibt`) |
| Porsche 911 GT3 R (992) | 1 | Spielberg (`porsche992rgt3_spielberg gp 2026-04-26 21-42-39.ibt`) |

The `data/gt3_ibts/` directory is gitignored — driver-side capture is required to populate it.

### Adding a 4th+ GT3 car (Mercedes AMG GT3, Acura NSX GT3, Lambo Huracán, McLaren 720S, Mustang, Corvette Z06, Audi R8 LMS)

W10.1 covers the remaining 7 GT3 cars. Per-car onboarding workflow:

1. **Add a stub `CarModel` entry to `car_model/cars.py`** with the car's `iracing_car_path` (verified from a real IBT's `DriverInfo.CarPath`), `front_spring_range_nmm`, `damper.click_polarity`, `damper.{ls,hs}_{comp,rbd}_range`, and `arb.measured_lltd_target`.
2. **Add the canonical name to `_CAR_REGISTRY` in `car_model/registry.py`** — both the `CarIdentity` row and the `CAR_FIELD_SPECS` dict (empty stub if PARAM_IDS not yet known). Without this, the substring fallback silently routes the IBT through the GTP BMW spec and corrupts learner observations.
3. **Add `_<CAR>_GT3_PARAM_IDS` dict to `output/setup_writer.py`** (mirror the BMW M4 GT3 / Aston / Porsche dicts; verbatim from the iRacing garage YAML for that car). Some cars have unique YAML hierarchy quirks (e.g. Porsche fuel under `FrontBrakesLights`, Aston EpasSetting) — sample a real session-info YAML first.
4. **Run the pipeline against an IBT** with `--force` to verify end-to-end output:
   ```bash
   python -m pipeline.produce --car <canonical> --ibt session.ibt --force --sto out.sto
   ```
5. **Commit a regression baseline `.sto` fixture** under `tests/fixtures/baselines/<car>_<track>_baseline.sto` and add the entry to `tests/test_setup_regression.py:REGRESSION_CASES`.
6. **Run varied-spring IBT capture** per the protocol above to upgrade to "calibrated" tier.

### Known gotchas

- **`--force` is required** for any GT3 pipeline run until the calibration gate is satisfied. Without it the gate blocks Step 1 (no aero compression) and Step 3 (no spring-rate calibration) for all GT3 cars.
- **Aero maps**: GT3 uses `balance_only` aero metadata (no L/D grid) — the rake solver dispatches to `_solve_balance_only` and `AeroSurface.has_ld == False`. Don't try to feed a GTP-format aero map to a GT3 car; it will compute NaN L/D and propagate.
- **Damper writes per-axle**: iRacing's GT3 garage schema is per-axle (8 channels) not per-corner (16). The damper solver averages L/F and R/F clicks before writing. This means asymmetric damper recommendations on GT3 are silently lost on .sto write — a known limitation.
- **Track aliases**: `_TRACK_ALIASES` in `car_model/registry.py` covers Spielberg ↔ Red Bull Ring. Add aliases for any new track that has a vendor-specific name vs the iRacing display name.
- **`measured_lltd_target` per car** (BMW 0.51, Aston 0.53, Porsche 0.45): these bypass the OptimumG physics formula because the empirical evidence is more authoritative than the formula. Don't override unless you have wheel-force telemetry.

### Per-car quirks reference

See [`skill/per-car-quirks.md`](../skill/per-car-quirks.md) GT3 sections for ARB encoding, damper polarity, fuel capacity, TC label suffix, rear toe shape (paired vs per-wheel), per-car YAML hierarchy quirks (Porsche fuel under `FrontBrakesLights`, Aston EPAS / ThrottleResponse, Porsche `ThrottleShapeSetting` under `InCarAdjustments`, etc.), and driver-loaded baseline values for the 3 sampled cars.

### Audit corpus

The full Phase 2 audit lives under [`docs/audits/gt3_phase2/`](audits/gt3_phase2/):
- `SYNTHESIS.md` — Top-level overview of the 12 audit PRs and ~329 findings.
- `IMPLEMENTATION_STATUS.md` — Wave 1–8 implementation tracker (updated after every batch).
- `output.md` — Per-car PARAM_IDS dicts + driver-side YAML divergences.
- `calibration-gate.md` — `not_applicable` step dispatch rationale.
- `solver-rake-corner-arb.md` — Step 1/3/4 GT3 paths.
- `solver-damper-legality.md` — Damper polarity + range per car.
- `learner.md` — GT3 KNOWN_CAUSALITY entries + corner-spring fits.
- (and 5 more)

