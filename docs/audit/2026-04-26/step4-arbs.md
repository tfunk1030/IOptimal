# Step 4 ARBs — Audit (2026-04-26)

Owner: `solver/arb_solver.py` (~774 LOC pre-refactor; ~720 LOC post-refactor)

## Summary

The ARB solver is physically sound. The OptimumG/Milliken LLTD formula, the
roll-stiffness decomposition (`K_arb + 2·k_wheel·t_half²` for paired-corner
cars and `k_wheel·IR²·t_half²` for Multimatic single-front-roll-spring cars),
the LLTD bounds clamp ([0.30, 0.75]), the use of `car.arb.rear_blade_count`,
the FARB-pinned strategy and the rear driver-anchor fallback are all in place
and correct. The 200 kph speed-correction usage is intentional — the
"unified 150 kph boundary" line in CLAUDE.md refers to a different
speed-bucketing decision in `solve_chain.py` and not to this file.

The audit found three quality issues and one HONEST-NAMING gap (per Principle
11). All four are fixed in this slice.

## Findings

### High — Honest naming for driver-anchor was missing
- **File:line:** `solver/arb_solver.py:410-425` (pre-refactor)
- **Behavior:** The anchor block correctly preserved the driver-loaded rear
  ARB and recomputed `lltd_error` to expose the model gap, but the
  `car_specific_notes` only contained generic, car-specific marketing copy.
  CLAUDE.md Principle 11 explicitly requires: *"Honest naming: when the
  anchor fires, the output line in step4/step6/etc. says 'anchored to
  driver-loaded' so a reader can audit which values are model-derived vs
  driver-derived."* That string did not appear in the pre-refactor output.
- **Fix:** Added an `anchored_to_driver` flag set inside the anchor branch.
  When true, a note is **inserted at index 0** of `car_specific_notes`:
  > `Rear ARB anchored to driver-loaded {size}/blade {blade} — model
  > LLTD={x}% vs target {y}% ({gap}% gap). Physics target unverifiable
  > without wheel-force telemetry; deferring to IBT-validated driver
  > setup.`

### Medium — Triplicated LLTD-target physics formula
- **File:line:** `solver/arb_solver.py:317-337`, `596-607`, `683-692`
  (pre-refactor)
- **Behavior:** The OptimumG physics formula
  `weight_dist + (tyre_sens/0.20)·(0.05+hs_correction)` plus the
  measured-target preference and the [0.30, 0.75] clamp existed in three
  places. Two of the three (`solve_candidates`, `solution_from_explicit_settings`)
  silently dropped the bounds clamp, so an extreme `lltd_offset` could
  produce a negative or >1.0 target on those code paths.
- **Fix:** Extracted `_resolve_target_lltd(lltd_offset)` as the single
  source of truth. All three sites now call it; bounds-check applies
  uniformly.

### Medium — Triplicated spring-roll-stiffness branch
- **File:line:** `solver/arb_solver.py:271-289`, `595-607`, `670-682`
  (pre-refactor)
- **Behavior:** The "Multimatic vs paired-corner" branch was duplicated
  three times. Two copies used `getattr(csm, "front_is_roll_spring", False)`
  and `getattr(csm, "front_roll_spring_installation_ratio", 1.0)` — silent
  fallbacks that violate Principle 8 (No silent fallbacks). The `solve()`
  copy already used direct attribute access (per the 2026-04-09 cleanup),
  so the duplicates were the regression.
- **Fix:** Extracted `_spring_roll_stiffness_pair(front_wr, rear_wr)`. All
  three call sites now use it. The architecture branch is direct-attribute
  (no `getattr` fallback) in the single source of truth.

### Medium — Triplicated `_build_constraints` block + magic numbers
- **File:line:** `solver/arb_solver.py:455-479`, `715-739` (pre-refactor)
- **Behavior:** The three-element `[LLTD target, slow-blade, RARB
  sensitivity]` constraint list was duplicated, with magic numbers
  (`0.05`, `0.005`, `0.05`) inline in both locations. The pass-gate `0.05`
  for LLTD was also inconsistent with `LLTD_DRIVER_ANCHOR_GATE=0.03` (a
  4.9% miss would PASS the constraint check while still being just below
  the anchor trigger — confusing semantics).
- **Fix:** Extracted `_build_constraints(...)` and module-level constants:
  - `LLTD_BASELINE_OFFSET = 0.05`
  - `LLTD_TYRE_SENS_REFERENCE = 0.20`
  - `LLTD_HS_CORRECTION_MAX = 0.01`
  - `LLTD_MIN = 0.30`, `LLTD_MAX = 0.75`
  - `LLTD_PREFER_SAME_SIZE_GATE = 0.015`
  - `LLTD_DRIVER_ANCHOR_GATE = 0.03`
  - `LLTD_CONSTRAINT_PASS_GATE = 0.05`
  - `RARB_SENS_MIN = 0.005`, `RARB_SENS_MAX = 0.05`

### Low — `self.car.name` vs `self.car.canonical_name` inconsistency
- **File:line:** `solver/arb_solver.py:557` (pre-refactor)
- **Behavior:** The else-branch note for unknown cars used `self.car.name`
  while every other branch and the `car_name` selector used
  `self.car.canonical_name`. Cosmetic, but the fields can differ
  (`name` is the iRacing display string, `canonical_name` is the slug).
- **Fix:** Standardised on `canonical_name`.

### Low — Unused `current_front_arb_size`/`current_front_arb_blade`
- **File:line:** `solver/arb_solver.py:246-264` (pre-refactor)
- **Behavior:** Both parameters are accepted and silently dropped. The
  docstring documents this gap, so it is not a bug, but a caller passing
  these expecting a symmetrical anchor would be surprised. FARB is
  intentionally pinned soft, so a front anchor would CONTRADICT the
  validated BMW/Multimatic strategy and is unlikely to be added.
- **Fix:** Added a `logger.debug(...)` line so the dropped values appear
  in debug logs. Kept the API and docstring; the audit does not recommend
  implementing a front anchor (would conflict with FARB-pinned strategy).

### Verified non-issues
- **`car.arb.rear_blade_count`** is correctly used in all six search loops
  (search-preferred-size, search-all, sensitivity, rarb_fast_blade,
  solve_candidates, solution_from_explicit_settings).
- **LLTD bounds [0.30, 0.75]** are now applied uniformly in
  `_resolve_target_lltd`.
- **The proxy is NOT used as the LLTD target.** The solver only consults
  `car.measured_lltd_target` (set per-car in `cars.py` from the OptimumG
  formula or hand-calibration) or the in-line physics formula. The
  `roll_distribution_proxy` epistemic gap from CLAUDE.md is contained in
  `analyzer/extract.py` and `auto_calibrate.py` — this slice does not
  re-introduce it.
- **Speed-dependent LLTD (200 kph)** is correct. The CLAUDE.md "150 kph
  boundary" remark refers to a different gap-elimination in the
  speed-bucketing for solver-to-solver hand-off, not this file.
- **Wheel-rate vs spring-rate convention.** Per CLAUDE.md, the ARB solver
  expects WHEEL rates for both axles. `_corner_spring_roll_stiffness()`
  retains its `motion_ratio=1.0` default — when given a wheel rate the
  MR² multiplier is a no-op. Call sites in `solve_chain.py` correctly
  pass `step3.front_wheel_rate_nmm` and `step3.rear_wheel_rate_nmm`
  (the `rear_wheel_rate_nmm` property added in the 2026-04-09 audit).

## Cross-slice referrals

- **None.** All findings were resolvable inside `solver/arb_solver.py`. The
  driver-anchor relies on `current_rear_arb_size`/`current_rear_arb_blade`
  being supplied by the caller (`solve_chain.py`); audit confirmed those
  are wired correctly. No edit to other files is required.

## Verification

- `pytest -k "arb or ARB or lltd or LLTD"` → 7 passed, 0 failed.
- Full `pytest` (excluding `tests/test_webapp_routes.py`, no fastapi
  installed): 346 passed, 17 skipped, 3 PRE-EXISTING failures unrelated
  to this slice (verified by `git stash` + re-run on baseline). The 3
  pre-existing failures are:
  - `test_acura_steps_1_3_runnable_4_6_blocked`
  - `test_full_report_blocked_steps_includes_cascaded`
  - `test_support_tier_mapping`
- E2E Porsche/Algarve smoke test: `PORSCHE OK`
  (LLTD achieved=0.522, target=0.521, error=0.001 — anchor did not fire,
  formula path verified). JSON output confirmed `lltd_target=0.521`,
  `rear_arb_size="Stiff"`, `rear_arb_blade_start=2`,
  `parameter_search_status` all set to `user_set`.
- E2E Ferrari/Hockenheim smoke test: `FERRARI OK` (Step 4 blocked by
  `spring_rates` calibration as expected per CLAUDE.md status).
