# IOptimal Agent Guide

> Master reference for all AI agents working on this codebase.  
> Last updated: 2026-04-03

---

## What This Project Is

IOptimal is a **physics-based car setup calculator for iRacing GTP/Hypercar**. It reads binary telemetry (IBT) files, diagnoses handling problems, profiles driver behavior, and produces optimized `.sto` setup files loadable directly into iRacing. Every recommendation is justified by physics constraints -- not pattern matching.

## Current State (2026-04-03)

### Calibration Tiers

| Car | Tier | Observations | What's Calibrated | What's Missing |
|-----|------|-------------|-------------------|----------------|
| BMW M Hybrid V8 | **Calibrated** | 73 (Sebring) | Garage output model (60 regressions), RH model, deflection model, heave calibration, damper zeta targets (0.68/0.23/0.47/0.20), m_eff, LLTD target (0.41), aero compression, spring lookups | Holdout Spearman still weak (-0.12), m_eff uses lap-wide not HS-filtered stats |
| Ferrari 499P | **Partial** | ~25 (Hockenheim + Sebring) | Rear torsion C=0.001282 (MR=0.612), indexed control lookups (heave 0-8/0-9, torsion 0-18), 3 calibration points | Garage output model, deflection model, damper zeta, LLTD target |
| Cadillac V-Series.R | **Exploratory** | 4 (Silverstone) | Aero compression calibrated (front=12mm, rear=18.5mm) | Everything else estimated |
| Porsche 963 | **Unsupported** | 2 (Sebring) | Roll gradient (0.84 deg/g), aero compression rough (front=12mm, rear=23mm) | Everything: garage model, RH model, deflection, damper force/click, m_eff, spring C, ARB stiffness, LLTD -- all ESTIMATE |
| Acura ARX-06 | **Exploratory** | 7 (Hockenheim) | m_eff (front=450, rear=220), pushrod-to-RH regression (R^2=0.91), ORECA chassis architecture | Torsion C (borrowing BMW), aero maps uncalibrated, roll dampers baseline only |

### Active Goals

1. **Porsche 963 at Algarve Grand Prix** -- This week's race. Need rapid calibration from unsupported to functional.
2. **BMW/Sebring score model improvement** -- Spearman -0.12 is weak; enhancementplan.md tracks roadmap.
3. **Multi-car onboarding** -- Each car needs 5+ varied garage screenshots and 10+ IBT sessions for basic calibration.

## Architecture Quick Reference

### Pipeline Flow
```
IBT -> track_model/build_profile -> analyzer/extract -> analyzer/diagnose
    -> analyzer/driver_style -> aero_model/gradient -> solver/modifiers
    -> solver/solve_chain (6 steps) -> output/setup_writer (.sto)
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
- Check `car_model/setup_registry.py` for correct YAML paths and STO param IDs before modifying setup readers/writers.
- Use `snap_to_resolution()` when writing garage values.
- Apply `rear_motion_ratio ** 2` when converting rear spring rate to wheel rate.
- Test with `python -m pytest tests/` before committing.

### DON'T
- Don't pattern-match from other cars' setups.
- Don't use `lltd_measured` as true LLTD -- it's a roll stiffness distribution proxy.
- Don't auto-apply calibration weights -- Spearman is too weak for autonomous weight application.
- Don't modify the solver step order.
- Don't assume Porsche/Cadillac/Acura garage models work like BMW's -- they have uncalibrated deflection/RH models.
- Don't write `include_computed=True` in .sto files for non-BMW cars (may cause iRacing rejection).

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
