# Codebase Audit — Why setup quality regressed and why non-BMW cars are underperforming

Date: 2026-04-01

## Scope

This audit focused on the code paths that determine setup quality, car/track correlation, and calibration application:

- `car_model/*` (car definitions + auto-calibration)
- `solver/objective.py` + `solver/session_database.py`
- `output/setup_writer.py`
- `validation/*`
- `data/learnings/observations/*` and `validation/objective_validation.json`

## Executive diagnosis

The program is not failing for one single reason. It is failing due to **a chain of compounding system-level issues**:

1. **Calibrations exist but are only partially applied at runtime** (intercepts get applied; most learned coefficients are ignored).
2. **BMW-only validation and garage-truth infrastructure still dominate quality gates**.
3. **Non-BMW cars still contain many estimated physics constants** that materially affect solver outputs.
4. **Output writer still has fallback behavior and partial parameter mappings**, so even good solver outputs can be degraded at export.
5. **Objective scoring is weakly/negatively correlated to real pace** in the one track where enough data exists.

This explains why BMW/Sebring can appear “understood” while Ferrari/Acura/Porsche/Cadillac often feel unreliable.

## Findings (factual)

### 1) Auto-calibration application is incomplete (major root cause)

`apply_to_car()` sets only a few intercept-style terms and leaves several learned coefficients unapplied (explicitly commented out). This means your learned data does **not fully reach the solver**.

- Deflection application leaves heave terms unused (`heave_defl_inv_heave_coeff`, `heave_defl_perch_coeff` commented out).
- Ride-height application updates intercepts but not the coefficient vectors that drive sensitivity.

Impact: even with Ferrari calibration files present, the runtime model can still behave close to legacy defaults for key sensitivities.

### 2) Calibration load failures are silently swallowed

`get_car()` wraps calibration loading in a broad `except Exception: pass`.

Impact: if calibration loading/parsing/application breaks, solver quietly falls back to uncalibrated behavior, and you get no visible failure signal.

### 3) Garage-output truth model is only defined for BMW/Sebring

`active_garage_output_model()` is generic, but only BMW has a `garage_output_model=` assignment in car definitions.

Impact: post-solve garage correlation correction path is effectively BMW-specific; other cars miss one of the strongest “realism” correction stages.

### 4) Output writer still supports partial mapping with TODO stubs/fallbacks

`write_sto()` explicitly states partially mapped cars can emit TODO comments for unmapped fields, and some logic still uses BMW-specific fallback formulas.

Impact: solver may produce sensible internals but exported setup fields can be incomplete, approximated, or coupled with BMW-derived fallback behavior.

### 5) Objective function correlation to actual lap time is poor

`validation/objective_validation.json` reports weak-to-negative correlation even on BMW/Sebring:

- Spearman (non-vetoed): `-0.1808`
- Pearson (non-vetoed): `-0.0604`

Impact: candidate ranking can select setups that “score” well but are not actually faster.

### 6) Validation pipeline itself is still BMW-centric

`validation/run_validation.py` hardcodes BMW objective evaluation and BMW model freshness checks (`bmw_sebring` files/models).

Impact: your validation dashboard can say “good” while Ferrari/Acura regressions are invisible.

### 7) Non-BMW car models still include critical ESTIMATE parameters

Ferrari and Acura definitions still contain multiple `ESTIMATE` values in areas that strongly affect behavior (rear effective mass, ARB stiffness maps, geometry gains, damper click force conversion, torsion constants, etc.).

Impact: low-confidence parameters propagate through heave, roll, damping, and balance calculations; this directly degrades setup transferability.

### 8) Empirical session DB uses shared fixed targets/weights

`TelemetryTargets` and `METRIC_SCORE_WEIGHTS` are global constants, not explicitly car/track-conditioned.

Impact: Ferrari Hockenheim or Acura ORECA dynamics are judged with thresholds likely tuned to BMW-like assumptions.

## Why BMW@Sebring felt better than Ferrari/other GTPs

- BMW has the deepest end-to-end support: garage truth model, densest observation volume, and most mature specific calibrations.
- Ferrari has useful data and calibration artifacts, but application and validation plumbing still has BMW-era bottlenecks.
- Acura/Cadillac/Porsche remain lower-confidence in either model completeness, data volume, or both.

## Why “more Ferrari data” still didn’t fix it

Because data alone is not enough. The pipeline has at least three choke points:

1. Learned coefficients are not fully applied to runtime car objects.
2. Downstream objective/validation still evaluates through partially BMW-shaped assumptions.
3. Export layer can still lose fidelity via partial mapping/fallback behavior.

## Concrete, viable fix plan (ordered by ROI)

### P0 (must do first)

1. **Make calibration application complete and explicit**
   - Apply full coefficient vectors for ride height and deflection models (not intercept-only).
   - Add an assertion/report object: “which coefficients applied” per run.

2. **Fail loudly (or warn loudly) on calibration load/apply errors**
   - Replace silent catch with structured warning in report + manifest.
   - Add a strict mode to stop generation when expected calibration is missing.

3. **Add parity tests per car for calibration usage**
   - Unit test: applying calibration must change predictions for known sensitivity probes.
   - Regression test: Ferrari/Acura calibration alters objective evaluation in expected direction.

### P1 (quality unlock)

4. **Create garage-output models per supported car/track family**
   - Start Ferrari Hockenheim + Ferrari Sebring, Acura Hockenheim.
   - Gate correction use by confidence tier, but don’t leave path BMW-only.

5. **Refactor validation into per-car/per-track matrix**
   - Remove BMW hardcoding from objective + freshness report.
   - Emit objective correlation, signal fallback rates, and model staleness for each supported tuple.

6. **Separate scoring policy by car/track**
   - Car-conditioned `TelemetryTargets` and `METRIC_SCORE_WEIGHTS` (or learned priors).
   - Track-profile-aware temperature/ride-height bounds.

### P2 (stability + trust)

7. **Finish `.sto` writer mapping coverage and remove TODO fallback dependence**
   - Enforce mapping completeness by car before marking support tier “production”.

8. **Add confidence ledger to every output setup**
   - Include % of parameters from calibrated vs estimated sources.
   - Include “unsupported assumptions touched” list.

9. **Promote support tiers to runtime behavior**
   - For exploratory/unsupported tuples, switch solver to conservative bounded mode.
   - Block aggressive optimization when confidence is too low.

## What “viable resource” should mean (definition)

A car/track tuple should only be labeled viable when all are true:

- objective correlation (non-vetoed Spearman) is positive and stable over rolling window,
- no silent calibration fallbacks occurred,
- writer mapping coverage is complete for required setup controls,
- fallback signal usage is below agreed threshold,
- garage correlation model exists or explicit validated alternative path is active.

Without those gates, the tool can still be useful for exploration, but not trustworthy for competitive setup decisions.
