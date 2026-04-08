# iOptimal Architectural Overhaul Plan

**Created:** 2026-04-06
**Last updated:** 2026-04-08 (LLTD phantom proxy disabled, σ-cal driver-anchor architecture, per-axle damper flags, rear roll damper bug fixed, driver-anchor pattern across 5 solvers)
**Status:** ~70% delivered. Phase 2 (RH model unification) and Phase 4 (solver path unification) still pending. **NEW: Phase 6 (driver-anchor pattern + LLTD honesty) shipped 2026-04-08.**
**Estimated scope:** 6 phases, ~15-20 sessions

## Progress as of 2026-04-08

✅ **Shipped 2026-04-08 (this branch):**

10. **🚨 LLTD phantom proxy disabled** — `analyzer/extract.py:lltd_measured` is a misnamed alias for `roll_distribution_proxy`, a geometric ratio insensitive to spring stiffness. Verified across 5 Porsche/Algarve IBTs with R_third varying 100%: spread 0.09 pp (a real LLTD would shift 5–15 pp). The "11 pp model gap" the ARB solver was chasing was apples-to-oranges. `auto_calibrate.py:1360` block populating `models.measured_lltd_target = mean(proxy)` is now gated behind `if False:`. `data/calibration/porsche/models.json:measured_lltd_target` cleared. Porsche `cars.py` sets `measured_lltd_target = 0.521` explicitly from the OptimumG/Milliken physics formula. Open epistemic gap documented: no true LLTD measurement from iRacing IBT.
11. **σ-calibration architecture** — `solver/heave_solver.py:min_rate_for_sigma` now accepts `current_rate_nmm` + `current_meas_sigma_mm`, computes `cal_ratio = meas / model_at_current` (clamped [0.5, 2.0]), translates user σ-target to model space. Sticky pre-check returns driver rate when its model_σ ≤ effective target + 0.05 mm. Wired through both `_run_sequential_solver` and `materialize_overrides` paths.
12. **Driver-anchor pattern** rolled out across 5 solvers (`heave`, `corner_spring`, `arb`, `diff`, `supporting`). Anchors are explicit, provenance-tracked, and never lap-time-driven. See "Driver-anchor pattern caveats" in CLAUDE.md known limitations.
13. **Per-axle roll damper flags** — `DamperModel.has_front_roll_damper` and `has_rear_roll_damper`. Porsche set to `front=True / rear=False` (Multimatic). Acura set to `both=True`. Setup writer + damper solver gate output on these flags. Fixes phantom `CarSetup_Dampers_RearRoll_*` XML IDs in Porsche `.sto`.
14. **`solution_from_explicit_offsets` static-honoring fix** — when caller provides explicit `static_front_rh_mm`/`static_rear_rh_mm`, use them directly instead of recomputing from `garage_model.predict()` with baseline springs. Was the single largest fix for Porsche front static drift.
15. **`solver/objective.py` aero ref + compliance front static** — replaced `track.median_speed_kph` with `track.aero_reference_speed_kph` (V²-RMS over speed bands ≥100 kph). Compliance-based front static now honors candidate's `front_pushrod_offset_mm` (was hardcoded to `pushrod.front_pinned_rh_mm`).
16. **Falsy-int bug fix in 3 sites** (`supporting_solver.py:303-313`, `:406`, `solve_chain.py:240`) — `diff_ramp_option_index(...) or 1` was silently collapsing legal index 0 (= 40/65) to index 1 (= 45/70). Replaced with explicit None checks.
17. **Porsche `default_df_balance_pct` 50.5 → 46.8** — calibrated from 4 Algarve IBTs (driver achieves 46.6–47.2% balance). Old 50.5% was unreachable at sim-min front (aero map caps at 52.5% only at static_R ≈ 66 mm, beyond garage cap).
18. **Porsche `default_diff_preload_nm`** field added (default 12 N·m for cars without override, 85 N·m for Porsche). `solver/diff_solver.py` reads from car instead of hardcoded 12.
19. **ARB blade clamp** uses `car.arb.rear_blade_count` instead of hardcoded `hi=6` BMW assumption. Driver-validated Stiff/10 was unreachable for Porsche before this fix (Porsche range 1–16).

## Progress as of 2026-04-07

✅ **Shipped (in current branch):**

1. **Regression test safety net** — `tests/test_setup_regression.py` runs the full pipeline against committed `tests/fixtures/baselines/{bmw_sebring,porsche_algarve}_baseline.sto` fixtures. Every code change verified against these.
2. **Honest calibration gate** — Three-status classification (`calibrated` / `weak` / `uncalibrated`), R² thresholds (0.85 block / 0.95 warn), per-subsystem provenance, JSON `calibration_provenance` output, prominent `WEAK CALIBRATION DETECTED` banner. Cascade fixed: only TRUE blocks (uncalibrated) propagate; weak blocks do not. Steps 5/6 cascade from Step 3 (wheel rates), not from Step 4 (ARBs).
3. **Compliance physics for RH and deflection models** — Static RH and deflection under aero load follow `defl ∝ F/k`. Added `front_coeff_inv_heave`, `rear_coeff_inv_third`, `rear_coeff_inv_spring` fields. For Porsche: front R² 0.96→0.9997, rear R² 0.61→0.94, deflection R² 0.67→0.97. BMW continues to use linear (its data fits that better). Both forms coexist in `RideHeightModel`/`DeflectionModel`.
4. **`apply_to_car` zeroing fix** — Stale BMW coefficients no longer persist alongside fresh non-BMW calibration. Every coefficient in the maps is zeroed before applying the new model.
5. **18 silent BMW fallback patterns removed** from `solver/objective.py`, `solver/sensitivity.py`, `solver/candidate_search.py`, `solver/sector_compromise.py`, `solver/legal_space.py`, `solver/damper_solver.py`, `solver/stint_model.py`, `solver/rake_solver.py`, `solver/arb_solver.py`, `solver/bayesian_optimizer.py`, `solver/explorer.py`. Direct attribute access — fail loudly if a car is missing a field.
6. **`damper_solver.py` strict mode** — 50-line baseline-fallback path removed. Now raises `ValueError` with click-sweep instructions if zeta is uncalibrated. Gate blocks Step 6 BEFORE this is reached.
7. **`pushrod_for_target_rh` strict mode** — `-29.0` BMW fallback removed. Now raises `ValueError` if `rear_coeff_pushrod` is zero (uncalibrated).
8. **Garage feasibility cap** in `rake_solver` — caps target rear static RH to what the garage pushrod range can produce. Prevents impossible targets that previously caused +74.5mm pushrod garbage.
9. **Per-corner tyre pressures**, **Front Roll HS slope propagation**, **Rear 3rd damper propagation**, **Porsche diff coast/drive ramp XML IDs** — all `.sto` mapping fixes shipped.

⏳ **Pending (need future sessions):**

- **Phase 2: Unify the 3 RH models** (`PushrodGeometry`, `RideHeightModel`, `GarageOutputModel`). They are now CONSISTENT (compliance physics applied to all three) but still SEPARATE classes. The 12 `reconcile_ride_heights()` call sites still exist. This is the highest-value remaining structural cleanup.
- **Phase 4: Unify the 4 solver entry points** (`solve.py`, `solve_chain.py`, `full_setup_optimizer.py`, `pipeline/reason.py`). 14 known divergences. Current state: bug fixes have to be applied to each path independently.
- **Phase 5: Decompose `pipeline/produce.py`** (still ~1500 lines despite this session's additions). Lower priority.
- **Strict gate hard-blocking on `weak`** — currently weak status produces output + loud warning. To make it actually `blocked = True`, ~170 references to `step4`/`step5`/`step6` across the codebase need None-handling. Multi-session refactor.
- **m_eff rate-table lookup enable** — infrastructure shipped, gated off (`m_eff_rate_lookup_enabled=False`) because the table is too noisy (rear range 5.9x with non-monotonic averages). Need 10+ samples per spring rate to enable safely.
- **Porsche ARB resolution** — RESOLVED 2026-04-07: noise-floor gate added to auto_calibrate. The ARB back-solve now returns `arb_calibrated=None` (inconclusive) when the predicted ARB stiffness delta is below the K_total measurement noise floor. Gate maps None → MEDIUM hand-cal, not weak. Porsche ARB no longer triggers WEAK CALIBRATION banner.
- **🚨 LLTD true measurement** (NEW open item) — `analyzer/extract.py:lltd_measured` is a geometric proxy, NOT real LLTD (see Phase 6 shipped items). To get a TRUE LLTD calibration we need EITHER (a) iRacing wheel-load telemetry channels (`LF/RF/LR/RR_LoadN` if exposed), OR (b) controlled per-axle ARB lap-time correlation across 10+ varied-blade sessions on the same track. Without one of these the 13 pp model-vs-physics gap (model 0.391 vs OptimumG 0.521 for Porsche driver setup) is un-attributable. Currently the ARB solver uses driver-anchor fallback when `lltd_error > 3 pp`.
- **Trailing rear pushrod gap** — Porsche newest IBT shows pipeline R_pushrod = 23.5 vs driver = 18 (5 mm gap), cascading from rear static 1.3 mm above driver. Same anchor pattern fix as Phase 6 anchors would close it: add `current_rear_rh_dynamic_mm = measured.mean_rear_rh_at_speed_mm` anchor to `rake_solver._find_rear_for_balance`. Estimated 30-min next-session task.

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
