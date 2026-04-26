# Audit slice #2: Step 2 Heave / Third Springs

**Owned files:** `solver/heave_solver.py`, `vertical_dynamics.py`
**Auditor:** parallel agent #2
**Date:** 2026-04-26

## Summary

The Step 2 solver (`HeaveSolver`) is correct on the points the audit
flagged: the σ-anchor sticky pre-check, `cal_ratio` clamp, and the
double-counting fix in `damped_excursion_mm()` are all live and
documented in-code. The 2.33 Gaussian factor was duplicated four times
inside the file (and another six times across the repo) — extracted to
`vertical_dynamics.GAUSSIAN_P99_SIGMA_FACTOR` and reused inside the slice.
Several other inline magic numbers and one dead keyword parameter were
cleaned up. No physics changes; pre-existing test pass rate preserved
(346 passed, 17 skipped, 3 pre-existing failures unrelated to this slice).

## Findings

### F1 — duplicated 2.33 Gaussian factor (audit-flagged) — minor — fixed
**File:** `solver/heave_solver.py` lines 21, 297, 299, 476 (originals);
`vertical_dynamics.py`.
**Behavior:** The constant 2.33 (Gaussian σ → p99 multiplier) appeared in
docstrings and three computations inside `heave_solver.py`, plus six more
sites in `solver/objective.py`, `car_model/garage_model.py`, and
`car_model/auto_calibrate.py`. A future change to use a different
percentile (e.g., 2.58 for p99.5) would silently miss these duplicates.
**Fix:** Added `GAUSSIAN_P99_SIGMA_FACTOR = 2.33` to `vertical_dynamics.py`
with an explanatory comment. Replaced all four uses inside
`heave_solver.py`. Cross-slice referrals filed below for the remaining
sites in other slices.

### F2 — inline magic numbers (sticky epsilon, cal-ratio bounds, target floor) — minor — fixed
**File:** `solver/heave_solver.py:454, 506, 518, 527`.
**Behavior:** `STICKY_EPSILON_MM = 0.50`, `max(0.5, min(2.0, raw_ratio))`,
`max(effective_target, 3.0)` were all undeclared local literals scattered
across `min_rate_for_sigma()`. The CLAUDE.md docstring describes them as
the calibration design but the code didn't surface them as named
constants reviewers could find easily.
**Fix:** Promoted to module-level named constants
`STICKY_SIGMA_EPSILON_MM`, `SIGMA_CAL_RATIO_BOUNDS`, and
`MIN_MODEL_SIGMA_TARGET_MM`, each with a short docstring explaining
the physics rationale.

### F3 — duplicated `v_braking_mps = 0.020` literal — minor — fixed
**File:** `solver/heave_solver.py:1263, 1570`.
**Behavior:** The "typical LS-regime braking compression velocity"
(20 mm/s) is computed in two places (the `solve()` body and the
`reconcile_solution()` body) using a re-declared local. If the modeling
assumption changes (e.g., a different car needs a different LS regime),
both copies would have to be updated.
**Fix:** Promoted to `BRAKING_COMPRESSION_VELOCITY_MPS` module constant.

### F4 — dead keyword parameter on `_shared_vertical_mass_kg` — minor — fixed
**File:** `solver/heave_solver.py:221-253` (originals).
**Behavior:** `_shared_vertical_mass_kg(self, axle, legacy_m_eff_kg, *,
parallel_wheel_rate_nmm: float = 0.0)` accepted a `parallel_wheel_rate_nmm`
keyword that was deliberately ignored (passed `0.0` to
`legacy_mass_to_shared_model_kg` regardless). The single caller forwarded
its own `parallel_wheel_rate_nmm` into this dead slot. The "double-counting
risk" the audit flagged is about exactly this — and the existing comment
already explains why it must be 0.0. The parameter therefore was a
parameter-sprawl trap inviting a future regression.
**Fix:** Removed the dead `parallel_wheel_rate_nmm` keyword from
`_shared_vertical_mass_kg`; updated the single caller (`excursion()`) to
stop forwarding it. Existing physics commentary collapsed into the new
docstring.

### F5 — `excursion_limit` computed but only used for `<= 0` guard — trivial — fixed
**File:** `solver/heave_solver.py:476-478` (original).
**Behavior:** `excursion_limit = sigma_target_mm * 2.33; if excursion_limit
<= 0:` is logically equivalent to `if sigma_target_mm <= 0:` because the
factor is positive. The variable was not reused.
**Fix:** Replaced with `if sigma_target_mm <= 0:` directly.

### F6 — Gaussian factor docstring drift — trivial — fixed
**File:** `solver/heave_solver.py` module + `HeaveSolver` class docstrings.
**Behavior:** Comments hard-coded "2.33" inline, divorcing the prose from
the constant.
**Fix:** Updated docstrings to refer to `GAUSSIAN_P99_SIGMA_FACTOR`.

### F7 — hard rear-third 900 N/mm limit (audit-flagged) — confirmed not magic
**File:** module docstring, `solver/heave_solver.py:29`.
**Behavior:** The reference is to BMW's `rear_spring_range_nmm=(100.0,
900.0)` defined in `car_model/cars.py:1935`. It is a real iRacing garage
range, not an empirical guess — confirmed in `cars.py` per-car ranges
(BMW 100-900, Porsche 100-300, Acura 100-1000, Cadillac 105-300, Ferrari
364-590 bar rates). No fix needed.

### F8 — legacy m_eff remapping (audit-flagged) — confirmed correct
**Files:** `solver/heave_solver.py:_shared_vertical_mass_kg`,
`vertical_dynamics.legacy_mass_to_shared_model_kg`.
**Behavior:** The "legacy" naming refers to BMW heave calibration that
predates the shared compliant model (suspension + tyre series) — not dead
code. `legacy_mass_to_shared_model_kg` translates the calibrated single-
spring m_eff into the equivalent compliant-model m_eff so the same
energy-method excursion formula gives the same answer at the calibration
point. No fix needed; clarifying comments retained.

### F9 — `damped_excursion_mm` parallel-spring contribution (audit-flagged) — confirmed not double-counted
**Files:** `vertical_dynamics.py`, `solver/heave_solver.py:_shared_vertical_mass_kg`.
**Behavior:** `damped_excursion_mm` adds parallel rate inside `k_eff`
only. `_shared_vertical_mass_kg` is now hard-pinned to
`parallel_wheel_rate_nmm=0.0` (post-fix F4 makes this structurally
unchangeable from callers). No double-count present.

### F10 — "Validation: front heave 30 N/mm (known unsafe)" check — informational
**File:** `solver/heave_solver.py:1117-1123`.
**Behavior:** The solver always appends a synthetic safety-check entry
for the BMW-known-unsafe rate of 30 N/mm. This is BMW-specific
hand-validation data; for other cars it is meaningless noise. Left in
place because the entry is clearly labeled and the solver doesn't act on
it (it is purely informational). Worth re-evaluating once the validation
report consumes per-car bad-rate fixtures rather than a single hardcoded
value, but out of scope for this audit.

### F11 — silent excepts / TODO / FIXME — none found
**Files:** both. `grep` for `except`, `TODO`, `FIXME`, `XXX`, `HACK`
returned zero matches in either file.

## Cross-slice referrals

These are duplicates of `2.33` outside this slice; recommend each owner
import `GAUSSIAN_P99_SIGMA_FACTOR` from `vertical_dynamics`:

- `solver/objective.py:996, 997` — `result.front_excursion_mm / 2.33`,
  `result.rear_excursion_mm / 2.33`. Already imports
  `damped_excursion_mm` from `vertical_dynamics` so the new constant
  import is one-line.
- `car_model/garage_model.py:240-242` — `excursion = std * 2.33`.
- `car_model/auto_calibrate.py:1688-1726` — multiple `sigma_mm * 2.33`
  computations during m_eff back-solve.
- `car_model/cars.py:412-414` — docstring reference.

These are referrals only — not edited from this slice.

## Verification

1. `python -c "from solver.heave_solver import HeaveSolver, HeaveSolution;
   from vertical_dynamics import GAUSSIAN_P99_SIGMA_FACTOR; print('OK')"`
   succeeds.
2. `pytest tests/ --ignore=tests/test_webapp_routes.py` →
   **346 passed, 17 skipped, 3 pre-existing failures**
   (`test_acura_steps_1_3_runnable_4_6_blocked`,
   `test_full_report_blocked_steps_includes_cascaded`,
   `test_support_tier_mapping`). The same 3 fail on the unmodified
   baseline (verified before edits) — they are not regressions from this
   slice.
3. Porsche/Algarve E2E (`pipeline.produce` with
   `porsche963gtp_algarve gp 2026-04-04 13-34-07.ibt`, wing 12,
   `single_lap_safe`) → produces a non-empty `.sto`, prints the standard
   Step 2 report.
4. Ferrari/Hockenheim E2E (`pipeline.produce` with
   `ferrari499p_hockenheim gp 2026-03-31 13-14-50.ibt`, wing 14,
   `single_lap_safe`) → produces a non-empty `.sto`, calibration gate
   correctly blocks Step 6.
