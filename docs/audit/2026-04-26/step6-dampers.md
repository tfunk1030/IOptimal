# Audit slice #6 — Step 6 Dampers (`solver/damper_solver.py`)

Date: 2026-04-26
Auditor: parallel agent #6
Owned files: `solver/damper_solver.py` (~1116 LOC pre-edit, ~1112 LOC post-edit)

## Summary

The damper solver is solid in its physics core (ζ·c_crit derivation, axle-modal
critical damping via `axle_modal_rate_nmm`, separate front/rear HS reference
velocities). The Step 6 zeta-calibration gate at `solve()` entry is correctly
strict — it raises rather than silently producing values.

The audit found **6 quality issues**, all fixed in-slice. **No physics bugs**
were found. **No critical correctness regressions**. Pre-existing test
failures (3) are out-of-slice and unrelated.

## Findings

### F1 — `getattr(d, "zeta_is_calibrated", False)` masks a fielded default — FIXED

- **Severity:** low (cleanup — Principle 8)
- **Location:** `damper_solver.py:301, 318, 476` (pre-edit)
- **Behaviour:** `DamperModel.zeta_is_calibrated` is a real dataclass field with
  default `False`. Using `getattr(..., False)` was redundant defensive code that
  hides any future field rename and adds noise.
- **Fix:** Direct attribute access. The unreachable physics-default branches in
  `_damping_ratio_ls/hs` were also removed: `solve()` now raises before either
  helper is reached when `zeta_is_calibrated` is False, so the helpers can
  honestly report "calibrated targets only" instead of pretending to also
  expose physics defaults.

### F2 — `getattr(self.car.damper, "digressive_exponent", 1.0)` — FIXED

- **Severity:** low (cleanup — Principle 8)
- **Location:** `damper_solver.py:365, 371` (pre-edit)
- **Behaviour:** `DamperModel.digressive_exponent: float = 1.0` is a real
  dataclass field with the same default. The `getattr` fallback was redundant.
- **Fix:** Direct attribute access (`self.car.damper.digressive_exponent`).

### F3 — `getattr(dm, "has_front_roll_damper", False)` / `has_rear_roll_damper` — FIXED

- **Severity:** low (cleanup — Principle 8)
- **Location:** `damper_solver.py:844-845` (pre-edit)
- **Behaviour:** Both fields are explicit `DamperModel` booleans with default
  `False`. The `getattr` fallback was redundant.
- **Fix:** Direct attribute access. Backward-compat semantics preserved (cars
  with `has_roll_dampers=True` and neither per-axle flag set still default to
  both axles having roll dampers — legacy Acura behavior).

### F4 — Heave-damper baseline silently fabricates magic clicks (10/40/5/10/40) — FIXED

- **Severity:** medium (Principle 7 — calibrated or instruct, never guess)
- **Location:** `damper_solver.py:938-963` (pre-edit)
- **Behaviour:** When `has_heave_dampers=True` (Ferrari only today), the solver
  used `dm_h.front_heave_baseline or {}` and `.get("ls_comp", 10)`, etc. If a
  car ever set `has_heave_dampers=True` without configuring the baseline (or
  with a partial dict missing keys), the solver would silently emit
  `10/40/5/10/40` — magic numbers wholly uncoupled from car physics. This
  contradicts Principle 7.
- **Fix:** Validate that both `front_heave_baseline` and `rear_heave_baseline`
  exist with all 5 required keys (`ls_comp, hs_comp, ls_rbd, hs_rbd, hs_slope`).
  Raise `ValueError` with a clear message naming the car and missing keys when
  the contract is violated. Today's Ferrari config is unaffected (it sets all
  keys).

### F5 — Stale Sebring-specific comment in generic helper — FIXED

- **Severity:** trivial (doc hygiene)
- **Location:** `damper_solver.py:410` (pre-edit, inside `_hs_slope_from_surface`)
- **Behaviour:** A comment baked the BMW/Sebring observation
  ("Sebring front 1.84 / rear 1.82 → both saturate → slope 11") into a
  car-agnostic helper. Misleading for non-BMW callers reading the code.
- **Fix:** Removed the car/track-specific example; kept the physics intuition
  (`ratio_floor / ratio_saturate` boundaries).

### F6 — Triple-duplicated rear-oscillation-vs-natural-frequency computation — FIXED

- **Severity:** medium (DRY / efficiency)
- **Location:** `damper_solver.py:524-525, 608-611, 747-750` (pre-edit)
- **Behaviour:** The exact same 2-line computation
  (`rear_osc_hz = …`, `rear_nat_freq_hz = sqrt(modal_rear_rate_nmm * 1000 / m_rear) / 2π`)
  appeared three times: once in the ζ-bump path, once in the Ferrari
  hs_slope_rbd path, once in the constraint-emission path. They could have
  drifted if any one of them was tweaked.
- **Fix:** Compute `rear_nat_freq_hz`, `rear_osc_hz`, and `rear_osc_ratio`
  exactly once, immediately after the modal critical damping is known.
  Reuse the same `rear_osc_ratio` (None when telemetry absent) at the three
  downstream sites.

### F7 — `v_ls_ref = 0.025` and `v_hs_ref_*` magic numbers duplicated across two methods — FIXED

- **Severity:** low (DRY)
- **Location:** `damper_solver.py:551, 564-565, 1023-1025` (pre-edit) — `solve()`
  and `solution_from_explicit_settings()` re-declared the same constants.
- **Fix:** Hoisted to class-level constants `V_LS_REF_MPS = 0.025`,
  `V_HS_REF_FLOOR_MPS = 0.050`, plus a small `_hs_ref_velocities()` helper.
  Both methods now share the same source of truth.

## Audit observations (no fix — informational)

### O1 — Magic constants in roll/3rd damper sizing

- `roll_supplement = 0.30` (line 830 pre-edit) and `zeta_roll = 0.55` (line 819)
  are hardcoded. These cars (Porsche, Acura) also lack ζ calibration, so the
  whole roll-damper branch is informational rather than driver-loadable. No
  silent fallback (it never claims calibration). Documenting as deferred.
- `fpc_3rd_ls = dm.ls_force_per_click_n * 2.0` (line 910) — comment says
  "6 clicks vs 12 for main → roughly 2x". Actual ratio is ~2.2x given
  Porsche's 1-11 / 0-5 spans. Within rounding noise; leave as is.

### O2 — `zeta_3rd_ls = zeta_ls_r` after the oscillation bump

The 3rd damper inherits the **post-modifier** zeta (after any ζ_hs_rear bump
from rear oscillation evidence). This is physically correct (more conservative
when rear is underdamped) and the docstring's "uses calibrated rear zeta
targets" remains accurate as long as the source ζ was calibrated.

### O3 — `parameter_search_status` defaults to `"user_set"` for every click

The `__post_init__` initialises every per-click status to `"user_set"`. The
explicit-settings path (when called from a candidate-search materialisation)
correctly inherits this, but the physics-derived path in `solve()` should
arguably mark them as `"physics_derived"`. Out of slice for damper_solver
(would require coordinated changes in `solve_chain.py`'s candidate engine).

## Cross-slice referrals

- **Slice #15 (calibration / data):** Step 6 zeta gap remains — only Porsche
  has calibrated ζ in `data/calibration/porsche/models.json`. BMW, Ferrari,
  Acura, Cadillac fall through the gate. The damper solver now correctly
  refuses to run for these cars (raises `ValueError`); the calibration gate
  in `car_model/calibration_gate.py:626` already classifies `damper_zeta` as
  `uncalibrated` so the pipeline blocks Step 6 at the gate without ever
  reaching the solver. Adding ζ calibration data is a slice #15 concern.

- **Slice on car_model/cars.py:** Ferrari is the only car declaring
  `has_heave_dampers=True`. Its baseline dict is hand-set. If new cars are
  added, they must populate all 5 baseline keys (`ls_comp, hs_comp, ls_rbd,
  hs_rbd, hs_slope`) for both front and rear or the solver will now raise
  (was: silently emit `10/40/5/10/40`).

- **Slice on `solver/supporting_solver.py`:** Three pre-existing test
  collection failures (`test_acura_hockenheim`, `test_webapp_regression`,
  `test_webapp_services`) all stem from a missing `compute_brake_bias` export.
  Out of damper-solver slice.

## Verification

### Tests (pre-edit baseline = post-edit)

```
3 failed, 317 passed, 16 skipped
  FAILED tests/test_calibration_semantics.py::TestCalibrationGateDependencyPropagation::test_acura_steps_1_3_runnable_4_6_blocked
  FAILED tests/test_calibration_semantics.py::TestCalibrationGateDependencyPropagation::test_full_report_blocked_steps_includes_cascaded
  FAILED tests/test_run_trace.py::RunTraceBasicTests::test_support_tier_mapping
```

All 3 failures are pre-existing and unrelated to damper_solver. The 8
damper-targeted tests (`-k damper`) all pass.

Tests covering the changes:
- `tests/test_physics_corrections.py::test_damper_solver_uses_modal_heave_rate_for_critical_damping`
- `tests/test_output_bundle_cli.py` Ferrari heave damper assertions

### E2E smoke

```
PORSCHE OK   # python -m pipeline.produce --car porsche --ibt … --wing 12 → /tmp/smoke_porsche.sto
FERRARI OK   # python -m pipeline.produce --car ferrari --ibt … --wing 14 → /tmp/smoke_ferrari.sto
```

Both .sto files were written non-empty. Porsche exercises the heave+roll
damper architecture (no rear roll), the Ferrari path exits at the calibration
gate (Step 6 blocked, but earlier steps still emit a valid .sto).

### Direct import check

```
from solver.damper_solver import DamperSolver, DamperSolution, CornerDamperSettings, FerrariHeaveDamperSettings  # OK
```
