# iOptimal Architectural Overhaul Plan

**Created:** 2026-04-06
**Status:** Planning
**Estimated scope:** 6 phases, ~15-20 sessions

## Why This Overhaul

The system was built by accretion around BMW. Each new car (Ferrari, Cadillac, Porsche, Acura) was added by overriding BMW assumptions rather than removing them. The result is a fragile codebase where:

- 3 ride height models (PushrodGeometry, RideHeightModel, GarageOutputModel) compete and disagree
- `reconcile_ride_heights()` is called 12 times across 5 files to paper over disagreements
- 322 dataclass fields default to BMW values that silently leak to other cars
- 4 different solver entry points (`solve.py`, `solve_chain.py`, `full_setup_optimizer.py`, `pipeline/reason.py`) duplicate the 6-step chain
- `produce()` is 1,432 lines with 160 branches and 15+ responsibilities
- `apply_to_car()` overwrites a R²=0.97 calibrated model with a R²=0.61 auto-calibrated model with no quality check
- The calibration gate says "PASS" for Porsche even though `arb_calibrated=False` in models.json (cars.py hardcodes `is_calibrated=True`)
- 785 `getattr(obj, "field", bmw_fallback)` calls hide type mismatches
- 123 `if car == "bmw"` string checks instead of polymorphism

The physics in individual solvers is sound. The wiring is the problem.

## Goals

1. **One source of truth** for ride height prediction (not 3)
2. **No silent BMW leaks** — calibrated values default to None, gate blocks on None
3. **One solver path** — both CLI and pipeline call the same function
4. **Quality-gated calibration** — auto-calibration can't replace a better model with a worse one
5. **Decomposed pipeline** — `produce()` becomes a 50-line orchestrator over typed stages
6. **No regressions** — BMW/Sebring output stays identical, Porsche/Algarve stays at least as good

## Non-Goals

- Rewriting solver physics (it's correct)
- Adding new cars
- Changing the 6-step workflow
- Rewriting analyzer/extract.py (works fine)
- Touching aero_model/, track_model/ (work fine)

## Phases

### Phase 0: Snapshot & Regression Tests (1 session)

**Goal:** Capture current behavior so subsequent phases can detect regressions.

**Tasks:**
- Generate canonical .sto outputs for BMW/Sebring and Porsche/Algarve at fixed wing/fuel/IBT
- Save them as fixtures in `tests/fixtures/`
- Write a regression test: "given this IBT and these args, the .sto must match this fixture"
- Same for the JSON output (parameter values, not the cosmetic fields)
- Document the current calibration gate status for all 5 cars

**Validation:** Tests pass on `main` before any changes.

**Deliverable:** `tests/test_setup_regression.py`, `docs/baseline_snapshots/` directory.

---

### Phase 1: Quality-Gated Calibration (1-2 sessions)

**Goal:** Stop bad data from silently overwriting good data.

**Tasks:**
1. Add R² thresholds to calibration gate (`car_model/calibration_gate.py`):
   - Front RH model: R² > 0.85
   - Rear RH model: R² > 0.85
   - Deflection models: R² > 0.80
   - Below threshold → status "weak", solver runs but flags low confidence
   - Below 0.5 → status "unreliable", step blocks
2. Fix `apply_to_car()` (`car_model/auto_calibrate.py`) to check R² before overwriting:
   - If existing model has higher R², keep it
   - If new model is better, replace and log
   - Never silently overwrite
3. Fix the `arb_calibrated` lie:
   - `apply_to_car()` reads `models.status.arb_calibrated`
   - Sets `car.arb.is_calibrated = models.status.arb_calibrated` unconditionally
   - Calibration gate checks the actual flag, not a hardcoded `True`
4. Use the m_eff rate table:
   - `HeaveSolver` reads the rate table when available
   - Looks up m_eff at the recommended spring rate (interpolated)
   - Falls back to scalar mean only if rate table is empty
5. Add R² to solver output reporting so the user sees model confidence

**Validation:**
- BMW/Sebring regression test passes (no behavior change for the calibrated car)
- Porsche/Algarve calibration gate now reports ARB as uncalibrated
- m_eff lookup matches the calibration table for known spring rates

**Deliverable:** `car_model/calibration_gate.py`, `car_model/auto_calibrate.py`, `solver/heave_solver.py` updates.

---

### Phase 2: Unify Ride Height Models (3-4 sessions)

**Goal:** One ride height predictor per car. Delete two of the three current models.

**Tasks:**
1. Design `RideHeightPredictor` interface:
   ```
   class RideHeightPredictor:
       def predict_static(self, setup: SetupState) -> tuple[float, float]
       def pushrod_for_target(self, target_front, target_rear, setup) -> tuple[float, float]
       def confidence(self) -> float  # R² or RMSE-based
   ```
2. Implement `BMWRideHeightPredictor` from existing BMW coefficients
3. Implement `PorscheRideHeightPredictor` from Porsche coefficients
4. Implement for Ferrari, Cadillac, Acura (using current best available data)
5. Replace `PushrodGeometry`, `RideHeightModel`, `GarageOutputModel` ride height methods with calls to the predictor
6. Delete the 12 `reconcile_ride_heights()` call sites — reconciliation happens inside `predict_static()` once
7. Validate each car against current output

**Validation:**
- BMW regression test passes
- Porsche regression test passes (same ride heights ±0.1mm)
- Each car's `predict_static(baseline_setup)` matches the garage screenshots
- Single call to `predict_static()` produces the same answer no matter how many times called

**Risk:** Highest-risk phase. Touching 12 call sites and 3 model classes.

**Mitigation:**
- Keep old classes in place during migration
- New predictor coexists with old code
- Migrate one solver step at a time
- Run regression tests after each step
- Don't delete old code until all 5 cars validated

**Deliverable:** `car_model/ride_height.py` (new), updates to all solver files, deletion of legacy methods.

---

### Phase 3: Eliminate BMW Default Contamination (2-3 sessions)

**Goal:** No silent BMW leaks. Calibrated coefficients default to None or are required.

**Tasks:**
1. Audit all 322 fields in `car_model/cars.py` dataclasses. For each field, classify:
   - **Universal physics constant** (e.g., gravity, fuel density) → keep default
   - **Class-wide constant** (LMDh fuel capacity 88.96L) → keep default
   - **Calibrated per-car** → change default to `None`
   - **Estimated per-car** → change default to `None`, add explicit value in each car definition
2. Remove BMW values from base dataclass definitions
3. Make every car definition explicitly specify every non-universal field
4. Update calibration gate: any `None` calibrated field blocks the relevant step with instructions
5. Fix the 18 known BMW leak sites identified in the audit:
   - `solver/objective.py:802-809` (damper click defaults)
   - `solver/sensitivity.py:500-546` (m_eff)
   - `solver/candidate_search.py:24,813` (torsion OD options, "bmw" string)
   - `solver/damper_solver.py:304-322,841-842` (zeta defaults, track widths)
   - `solver/sector_compromise.py:240,263,294-295` (brake bias, camber)
   - `solver/legal_space.py:69-71` (spring rate refs)
   - `solver/stint_model.py:663-664` (heave/third defaults)
   - `car_model/garage.py:137` (default rear pushrod)
   - `solver/objective.py:1687` (rear dynamic RH ref)

**Validation:**
- Every car explicitly defines every calibrated field
- No `getattr(obj, "field", bmw_value)` patterns remain in solver/
- BMW regression test passes
- Porsche regression test passes
- Removing a single Porsche field causes the calibration gate to block (not silent BMW fallback)

**Deliverable:** Updated `car_model/cars.py`, solver/* fixes, audit report.

---

### Phase 4: Unify Solver Paths (3-4 sessions)

**Goal:** One `solve()` function. Both CLI and pipeline call it.

**Tasks:**
1. Audit the 14 divergence points between `solve.py` and `solve_chain.py`:
   - Modifiers, Step 2 inputs, camber source, Step 4 inputs, Step 5 inputs, Step 6 inputs, supporting params, learned corrections, reconcile timing, legal search, optimizer args, failed validation clusters, rotation search, .sto output
2. Identify which divergences are intentional design (e.g., standalone has no telemetry) and which are bugs/drift
3. Define the canonical signature: `run_solve(SolveInputs) → SolveResult`
4. `SolveInputs` is a typed dataclass with optional fields for telemetry/measured/diagnosis
5. The single `run_solve()` handles both:
   - Track-only mode (no IBT) → uses physics defaults
   - Full pipeline mode (with IBT) → uses telemetry-driven modifiers
6. `solve.py` CLI becomes a thin wrapper: parse args → build SolveInputs → call run_solve → write output
7. `pipeline/produce.py` becomes a thin wrapper: extract IBT → build SolveInputs → call run_solve → write outputs
8. Delete the duplicated inline solver in `solve.py` lines 380-560
9. Delete the inline solver in `pipeline/reason.py`
10. Move `reconcile_ride_heights()` inside the rake solver — callers don't need to know it exists

**Validation:**
- BMW standalone (`solve.py`) output matches BMW pipeline (`produce.py`) output for the same inputs
- Porsche regression test passes
- All 14 divergence points are either resolved or explicitly documented as intentional
- `grep -r "_run_sequential_solver" solver/ pipeline/` returns one definition, not 4

**Deliverable:** `solver/solve_unified.py`, deletion of duplicated paths.

---

### Phase 5: Decompose `produce()` (2-3 sessions)

**Goal:** The 1,432-line god function becomes a 50-line pipeline of typed stages.

**Tasks:**
1. Identify the natural stages in `produce()`:
   - `parse_ibt(args) → IBTSession`
   - `select_lap(session, args) → SelectedLap`
   - `extract_telemetry(lap, car) → MeasuredState`
   - `segment_corners(measured, track) → CornerSegmentation`
   - `profile_driver(measured, segmentation) → DriverProfile`
   - `diagnose_handling(measured, driver, car) → Diagnosis`
   - `compute_modifiers(diagnosis, driver, measured, car) → SolverModifiers`
   - `solve(car, track, measured, modifiers, args) → SolveResult` (calls unified Phase 4 entry)
   - `validate_garage(result, car) → ValidationReport`
   - `write_outputs(result, validation, args)`
2. Each stage is a pure function with typed input/output (no global state)
3. New `produce()` is ~50 lines: orchestrate stages, handle errors, log progress
4. Each stage gets its own test file
5. Document the data flow in a single diagram

**Validation:**
- `produce.py` is < 200 lines total
- Each stage has unit tests
- Pipeline regression test passes
- Stages can be called independently in tests

**Deliverable:** `pipeline/stages/` directory with one file per stage, slim `pipeline/produce.py`.

---

### Phase 6: Polish and Documentation (1-2 sessions)

**Goal:** Make the new architecture maintainable.

**Tasks:**
1. Replace remaining `getattr(obj, "field", fallback)` patterns with typed access
2. Replace remaining `if car == "bmw"` branches with polymorphism or explicit per-car methods
3. Add architecture diagram showing the new data flow
4. Update CLAUDE.md with the new structure
5. Update calibration_guide.md with new gate semantics
6. Add a "porting a new car" guide showing what fields must be defined
7. Delete dead code from the old paths

**Deliverable:** Documentation, cleanup.

## Validation Strategy

**Every phase must:**
1. Start by running the regression tests from Phase 0
2. Make changes in a feature branch
3. Run regression tests after each significant change
4. End with all regression tests passing
5. Be merged only when BMW and Porsche outputs are unchanged or strictly improved

**No phase changes the physics.** The math in the individual solver steps is correct. We're only fixing wiring.

## Risk Assessment

| Phase | Risk | Mitigation |
|-------|------|------------|
| 0 | Low — only adds tests | None needed |
| 1 | Low — additive checks | BMW tests detect any regression |
| 2 | **High** — touches core models | Keep old code in place during migration; one car at a time |
| 3 | Medium — wide blast radius | Audit-driven, BMW test catches regressions |
| 4 | High — touches all entry points | Run BMW + Porsche tests after each merge |
| 5 | Medium — refactoring only | Stages are pure functions, easy to test |
| 6 | Low — cleanup | None needed |

## What This Will NOT Fix

These problems are real but out of scope for this overhaul:

1. **Calibration data quality** — More garage screenshots and IBT sessions are needed. The system can only be as good as its input data.
2. **Aero map coverage** — Some cars have sparse aero maps. The system extrapolates outside calibrated regions.
3. **R²=0.61 rear RH model** — Even with the unified predictor, the underlying regression is weak because there isn't enough setup variation in the calibration data.
4. **DSSV vs shim-stack damper physics** — Porsche dampers physically differ from BMW. Need a click-sweep session to calibrate force-per-click.
5. **The current Porsche race setup** — Phase 0 will snapshot the current best output and use it as a regression target. Improvements come from better calibration data, not code changes.

## Next Step

Start Phase 0: snapshot current behavior and write regression tests. This is the foundation that lets every subsequent phase detect breakage immediately.

Confirm before I start, or modify the plan.
