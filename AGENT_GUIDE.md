# IOptimal Agent Guide

> Master reference for all AI agents working on this codebase.  
> Last updated: 2026-04-11

---

## What This Project Is

IOptimal is a **physics-based car setup calculator for iRacing GTP/Hypercar**. It reads binary telemetry (IBT) files, diagnoses handling problems, profiles driver behavior, and produces optimized `.sto` setup files loadable directly into iRacing. Every recommendation is justified by physics constraints -- not pattern matching.

## Core Philosophy: Calibrated or Instruct, Never Guess

**The system NEVER outputs a setup value from an uncalibrated model.** If a subsystem isn't calibrated from real measured data for that specific car, the output is calibration instructions -- not a guess.

This is enforced by the **CalibrationGate** (`car_model/calibration_gate.py`), which sits between input loading and the solver. Each solver step is checked against per-car, per-subsystem calibration status. Blocked steps output calibration instructions telling the user exactly what data to collect and what CLI commands to run. Runnable steps produce validated output.

## Current State (2026-04-11)

### Calibration Gate Status

The CalibrationGate enforces per-car, per-step blocking. Each solver step requires specific calibrated subsystems. If ANY required subsystem is uncalibrated, the step is **blocked** and outputs calibration instructions.

| Car | Tier | Unique Setups | Calibrated Steps | Blocked Steps | What User Sees |
|-----|------|:---:|:---:|:---:|----------------|
| BMW M Hybrid V8 | **Exploratory** | 9 (Sebring) | 1-6 (all) | none | Full setup output (sequential solver; optimizer gated on BMW only) |
| Ferrari 499P | **Calibrated** | 60 sessions, 23 unique setups (Hockenheim) | 1-6 (all) | none | Full 6-step output; rear torsion bar validated from IBT controlled-group analysis |
| Cadillac V-Series.R | **Unsupported** | <5 (Silverstone) | — | 1-6 | Calibration instructions only |
| Porsche 963 | **Calibrated** | 62 sessions, 36 unique setups (Algarve) | 1-5 | 6 (damper_zeta not set) | 5-step setup; Step 6 blocked until `zeta_is_calibrated=True` |
| Acura ARX-06 | **Partial** | 15 sessions, 8 unique setups (Hockenheim) | 1-3 | 4-6 (ARB/LLTD/geometry/damper uncalibrated) | Steps 1-3 output + instructions for steps 4-6 |

### Per-Subsystem Calibration Matrix

| Subsystem | BMW | Ferrari | Cadillac | Porsche | Acura |
|-----------|-----|---------|----------|---------|-------|
| Aero maps | ✅ | ✅ | ✅ | ✅ | ✅ |
| Aero compression | ✅ | ✅ | ⚠️ stub | ✅ | ✅ calibrated |
| Ride height model | ✅ 0-3 features | ✅ R²=0.72 (front) | ⚠️ no data | ✅ R²=0.999 (front) | ✅ R²>0.9 |
| Deflection model | ✅ <0.09mm | ✅ 0.09-0.82mm | ⚠️ no data | ✅ R²=0.93-0.98 | ✅ R²>0.85 |
| spring_rates | ✅ | ❌ uncalibrated | ❌ uncalibrated | ✅ | ✅ |
| Damper zeta | ✅ (0.68/0.23/0.47/0.20) | ❌ estimate | ❌ estimate | ❌ not set | ❌ uncalibrated |
| ARB stiffness | ✅ hand-cal | ❌ estimate | ❌ estimate | ⚠️ hand-cal (medium) | ❌ estimate |
| LLTD target | ✅ (0.41) | ⚠️ torsion_arb_coupling=0.0 | ❌ not set | ✅ (0.521 physics formula) | ❌ not set |
| Roll gains | ✅ | ✅ | ❌ estimate | ❌ estimate | ❌ estimate |
| Pushrod geometry | ✅ | ✅ | ✅ | ✅ | ✅ |
| Garage model | ✅ full | ✅ full | ❌ none | ✅ full | ✅ partial |

### Per-Step Calibration Requirements

| Step | Name | Required Subsystems |
|------|------|-------------------|
| 1 | Rake/RH | aero_maps, aero_compression, ride_height_model, pushrod_geometry |
| 2 | Heave/Third | Step 1 output, m_eff, track_profile, **spring_rates** |
| 3 | Corner Springs | Step 2 output, torsion_constants, **spring_rates** |
| 4 | ARBs | Step 3 output, arb_stiffness, lltd_target |
| 5 | Geometry | Step 4 output, roll_gains, camber/toe ranges |
| 6 | Dampers | Step 5 output, damper_zeta, force_per_click |

### Objective Function Status (2026-04-11)

BMW/Sebring correlation:
- **Spearman**: **-0.298** (in-sample); **-0.080** (5-fold holdout mean)
- **Pearson**: **~0.226**
- Holdout worst fold: +0.121 (known weakness — one fold flips positive)
- Status: improving but not yet authoritative for automatic runtime weight application

### Active Goals

1. **Ferrari steps 2-6**: Unblocked — rear torsion bar validated from IBT controlled-group analysis; `spring_rates` now calibrated from 60 IBT sessions. Collect more sessions with varied rear corner settings to further reduce the ~20% rate uncertainty at extreme indices.
2. **Porsche Step 6**: Run damper click-sweep procedure → set `zeta_is_calibrated=True` in `car_model/cars.py`.
3. **Cadillac**: Collect everything from scratch (0 calibration points, 0 unique setups).
4. **BMW/Sebring objective hardening**: Spearman -0.298 is approaching actionable. Continue improving holdout stability (worst fold currently +0.121).
5. **Acura steps 4-6**: Collect ARB stiffness, LLTD data, and geometry measurements (need 10+ sessions with varied ARB settings).

## Architecture Quick Reference

### Pipeline Flow
```
IBT -> track_model/build_profile -> analyzer/extract -> analyzer/diagnose
    -> analyzer/driver_style -> aero_model/gradient -> solver/modifiers
    -> CalibrationGate.check_step() (per-step blocking)
        ├── BLOCKED -> calibration instructions (not setup values)
        └── RUNNABLE -> solver/solve_chain (6 steps) -> output/setup_writer (.sto)
    -> learner/ingest (knowledge accumulation)
```

### The 6-Step Solver Chain (ALWAYS in this order)
1. **Rake** -- ride heights for target DF balance (aero maps)
2. **Heave** -- softest spring preventing bottoming (excursion model)
3. **Corner Springs** -- natural frequency targeting, heave/corner ratio
4. **ARBs** -- LLTD targeting via roll stiffness distribution
5. **Geometry** -- camber/toe for contact patch across roll range
6. **Dampers** -- zeta ratios at LS/HS reference velocities

### Key Files by Task
| Task | Primary Files |
|------|--------------|
| Add/modify car model | `car_model/cars.py`, `car_model/garage_params.py`, `car_model/legality.py`, `car_model/setup_registry.py` |
| Calibration gate | `car_model/calibration_gate.py`, `solver/solve.py` |
| Fix telemetry extraction | `analyzer/extract.py`, `analyzer/setup_reader.py` |
| Modify solver physics | `solver/{step}_solver.py`, `solver/solve_chain.py` |
| Change scoring | `solver/objective.py`, `solver/scenario_profiles.py` |
| Fix .sto output | `output/setup_writer.py`, `output/garage_validator.py` |
| Calibration | `car_model/auto_calibrate.py`, `validation/objective_calibration.py` |
| Knowledge system | `learner/ingest.py`, `learner/empirical_models.py`, `learner/recall.py` |

## Rules for AI Agents

### DO
- Follow the 6-step ordering. Never jump to dampers before rake is set.
- Use physics justification for every parameter value.
- Respect calibration tiers -- don't claim BMW-level accuracy for Porsche.
- **Always check CalibrationGate before outputting setup values.** If a subsystem is uncalibrated, output calibration instructions instead.
- Check `car_model/setup_registry.py` for correct YAML paths and STO param IDs before modifying setup readers/writers.
- Use `snap_to_resolution()` when writing garage values.
- Apply `rear_motion_ratio ** 2` when converting rear spring rate to wheel rate.
- Test with `python -m pytest tests/` before committing.
- **Gate new scoring signals on calibration status.** Any new signal added to the objective function must be gated behind verified calibration data (like damper compression is gated on `zeta_is_calibrated`).

### DON'T
- **Don't output setup values from uncalibrated models.** This is the #1 rule. Output calibration instructions instead.
- Don't pattern-match from other cars' setups.
- Don't use `lltd_measured` as true LLTD -- it's a roll stiffness distribution proxy.
- Don't auto-apply calibration weights -- Spearman is improving but not yet authoritative.
- Don't modify the solver step order.
- Don't assume Porsche/Cadillac/Acura garage models work like BMW's -- they have uncalibrated deflection/RH models.
- Don't write `include_computed=True` in .sto files for non-BMW cars (may cause iRacing rejection).
- **Don't apply BMW coefficients to other cars.** The DeflectionModel gate ensures uncalibrated cars skip deflection veto entirely. Similar gates exist for damper zeta, LLTD, and ARB stiffness.

### Critical Conventions
- **Front torsion bar `front_wheel_rate_nmm`** = already a wheel rate (MR baked into C*OD^4)
- **Rear coil spring `rear_spring_rate_nmm`** = raw spring rate. Multiply by `rear_motion_ratio^2` for wheel rate.
- **Aero compression** is V^2-scaled: use `comp.front_at_speed(speed)`, not raw values.
- **Session IDs**: `{car}_{track}_{ibt_stem}`, lowercased, spaces to underscores.
- **Model IDs**: `{car}_{track_first_word}_empirical`.
- **Time decay**: `weight = 0.95^days` for all empirical corrections.
- **Min sessions**: 5 for physics corrections, 3 for prediction feedback, 4 for safe_linear_fit.

## Per-Car Architecture Notes

### Porsche 963 (Multimatic) -- CRITICAL DIFFERENCES FROM DALLARA
- **DSSV spool-valve dampers** (20 clicks, more progressive than shim-stack)
- **NO front torsion bar OD selection** (`exists=False` in garage_params) -- fundamentally different from BMW/Cadillac
- **Front roll spring system** (unique): roll spring + roll perch + roll damper (LS/HS/slope)
- **Front ARB**: Connected/Disconnected toggle (NOT Soft/Medium/Stiff labels), blades 1-5
- **Rear ARB**: Needs schema fix -- screenshots show "Stiff" but schema says ["Disconnected", "Soft"]
- **Diff**: Schema says `exists=False` but screenshots show ramp 50/75, clutch plates 6, preload 0Nm -- SCHEMA IS WRONG
- **Damper layout**: front heave (4-param, no slope), front roll (3-param), rear corners (5-param per corner), rear 3rd (4-param, no slope)
- **m_eff model is wrong**: car model says 176kg front, empirical shows ~498kg
- **Rear aero compression model is wrong**: car model says 8mm, empirical shows ~23mm
- Natural entry understeer -- don't just add front wing (destabilizes rear at high speed)
- Gentle on tyres -- can run aggressive geometry
- Existing observations have dampers reading all zeros (setup_reader parsing issue)
- Brake migration available (like Cadillac/Ferrari)

### Ferrari 499P (Bespoke LMH)
- Indexed controls: heave springs (0-8 front, 0-9 rear), torsion bars (0-18)
- Must convert physical N/mm <-> index before reading/writing
- Has separate heave dampers
- ARB uses letter indices (A-E + Disconnected)
- Pushrod param = PushrodLengthDelta (not Offset)
- Front hybrid only >190 kph

### Acura ARX-06 (ORECA)
- Heave + roll damper architecture (NOT per-corner)
- No per-corner shock velocity channels -- synthesized from heave +/- roll
- Torsion bars all 4 corners (front AND rear)
- Diff preload is THE most sensitive parameter
- Front heave damper always bottomed (normal characteristic)

### BMW M Hybrid V8 (Dallara)
- Most calibrated car. Reference implementation.
- Garage output model with 60 regression coefficients (Sebring only)
- Has constrained optimizer (BMWSebringOptimizer)
- Per-corner torsion bars (front) + coil springs (rear)

### Cadillac V-Series.R (Dallara)
- Open differential (no diff tuning)
- Front pushrod_to_rh = 1.28 (not pinned like BMW)
- Same Dallara chassis as BMW but different aero/weight

## How to Unblock Calibration Steps for Non-BMW Cars

Each car needs specific real-world data to unlock blocked steps. The CalibrationGate outputs these instructions automatically, but here's the summary:

| Data Needed | CLI Command | Unlocks |
|-------------|------------|---------|
| ARB stiffness (3+ IBT sessions, varied ARB sizes, springs constant) | `python -m car_model.auto_calibrate --car <car> --ibt-dir <telemetry_dir>` | Step 4 |
| LLTD target (10+ IBT sessions, varied settings) | `python -m validation.calibrate_lltd --car <car> --track <track>` | Step 4 |
| Roll gains (3+ IBT sessions with lateral-g data) | `python -m learner.ingest --car <car> --ibt <session.ibt>` | Step 5 |
| Damper zeta (5+ stints, varied LS comp clicks) | `python -m validation.calibrate_dampers --car <car> --track <track>` | Step 6 |
| Aero compression (3+ IBT sessions, different speeds) | `python -m learner.ingest --car <car> --ibt <each_file>` | Step 1 |
| Ride height model (10+ IBT sessions, varied spring/pushrod) | `python -m car_model.auto_calibrate --car <car> --ibt-dir <telemetry_dir>` | Step 1 |

## Empirical Data Systems

Two empirical systems exist, both functional and serving different purposes:

1. **SessionDatabase** (`solver/session_database.py`) -- k-NN predictions from observations. BMW: 99 sessions, Ferrari: 17 sessions. Gated on ≥10 sessions per car/track.
2. **EmpiricalModels** (`learner/empirical_models.py`) -- Learned corrections from `*_empirical.json`. BMW: 86 observations, 41 corrections. Prediction feedback loop.

No consolidation needed -- they serve complementary roles.

## Recent Changes Log (2026-04-04)

- **CalibrationGate framework** added (`car_model/calibration_gate.py`). Per-car, per-subsystem, per-step blocking.
- **DeflectionModel gate** -- uncalibrated cars skip deflection veto entirely (was applying BMW coefficients to all cars, producing impossible -55.9mm for Porsche).
- **Zero-variance physics fix** -- damper click variables were extracted AFTER being used in `_estimate_lap_gain()`, causing UnboundLocalError caught by try/except → all sessions got defaults.
- **Damper compression signal** -- front LS comp (r=-0.447) now scored in objective, gated on `zeta_is_calibrated`.
- **driver_mismatch weight** -- `w_driver=0.0` when no driver profile present.
- **k-NN data quality gate** -- `w_empirical` zeroed when < 10 sessions available.
- **BMW/Sebring Spearman** improved from -0.06 to -0.298 (5× improvement).
