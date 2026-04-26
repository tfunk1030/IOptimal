# Audit: Step 3 Corner Springs (`solver/corner_spring_solver.py`)

Date: 2026-04-26
Owner of slice: agent-a23dd67a52a71531a
Auditor focus: corner-spring solver only — no edits outside this file.

## Summary

The 6-step physics architecture in `corner_spring_solver.py` is sound: front
torsion-bar OD⁴ scaling is honored, the spring-rate convention
(`rear_spring_rate_nmm` = RAW, `rear_wheel_rate_nmm` = MR²-corrected via
property) is consistent, the LLTD-aware roll-spring floor for Porsche is
documented, and the driver-anchor on `rear_spring_rate_nmm` (CLAUDE.md
Principle 11) is correctly implemented as a soft 20% tolerance preference.

That said, several issues degrade either honesty (silent fallbacks),
maintainability (duplicated magic constants), or correctness (Ferrari
preload-turn calculation ignores the actual perches chosen by Step 2). The
audit identified eight findings; six were patched in this slice, two are
recorded as cross-slice referrals.

## Findings

### Severity: HIGH — Ferrari preload turns ignore actual perch values

**File**: `solver/corner_spring_solver.py:553-564`

**Behavior**: `solution_from_explicit_rates()` computes Ferrari preload turns
via `_solve_ferrari_torsion_bar_turns(... front_heave_perch_mm=_f_perch,
rear_third_perch_mm=_r_perch)` where `_f_perch` and `_r_perch` fall back to
the hardcoded constants `-16.5` and `-104.0` whenever the caller did not pass
explicit `front_heave_perch_mm` / `rear_third_perch_mm` kwargs. Inspecting
all 6 call sites (`solve_chain.py:1213, 1317`, `full_setup_optimizer.py:360,
426`, internal `solve()` at `corner_spring_solver.py:445`, and
`solve_candidates()`), **none** of them pass perch values. So in production
the Ferrari preload-turn outputs (`front_torsion_bar_turns`,
`rear_torsion_bar_turns`) are always computed against stale constants
regardless of the actual Step 2 perch choice.

The R²=0.51/0.55 model already has poor predictive power (CLAUDE.md flag);
feeding it stale perch input compounds the error. The Step 2 solution
exposes `perch_offset_front_mm` and `rear_third_perch_mm` — these should
flow through.

**Fix applied**: extract the hardcoded constants to module-level named
constants `_FERRARI_FRONT_HEAVE_PERCH_DEFAULT_MM` and
`_FERRARI_REAR_THIRD_PERCH_DEFAULT_MM`, single source of truth. Cross-slice
referral filed for solve_chain to wire actual perches through.

### Severity: MEDIUM — `print()` warning instead of logger

**File**: `solver/corner_spring_solver.py:436`

**Behavior**: When `csm.rear_torsion_unvalidated` is True the solver
unconditionally writes `"⚠  UNVALIDATED: Ferrari rear torsion bar model may
have 3.5x rate error..."` to stdout via `print()`. Two issues:

1. The codebase otherwise uses the module-level `logger`. `print()` here
   bypasses log-level configuration and pollutes JSON-only outputs.
2. After PR #57 (2026-04-11) Ferrari `rear_torsion_unvalidated` was set to
   `False` in `cars.py`. The branch is now dead in production but remains as
   defense-in-depth for future cars; the warning channel still needs to be
   `logger.warning`.

**Fix applied**: replaced `print()` with `logger.warning()`; removed the
emoji + leading/trailing newlines (logger format already provides level).

### Severity: MEDIUM — Silent fallback on `fuel_capacity_l` violates Principle 8

**File**: `solver/corner_spring_solver.py:309, 487`

**Behavior**: Two `getattr(self.car, 'fuel_capacity_l', 89.0)` calls. The
field is defined on `CarModel` with a class default of `88.96` — every car
instance has it. The `89.0` fallback is dead defensive code, but per
Principle 8 ("No silent fallbacks") and the 2026-04-09 cleanup pass that
already touched `solver/corner_spring_solver.py` for `canonical_name`, these
should be direct attribute access.

**Fix applied**: replaced both with `self.car.fuel_capacity_l`. If the field
is ever removed, the failure is loud and immediate.

### Severity: LOW — Duplicate `total_front_heave` / `k_heave_front` calculation

**File**: `solver/corner_spring_solver.py:516, 528`

**Behavior**: `solution_from_explicit_rates()` computes the same expression
twice:

    total_front_heave = front_heave_nmm + 2 * front_rate   # line 516
    k_heave_front     = front_heave_nmm + 2 * front_rate   # line 528

Both feed downstream calculations (one to the dataclass, one to the
heave-mode frequency). The duplication is visible noise and creates a risk
of one being modified without the other.

**Fix applied**: compute once, reuse. Same cleanup applied to
`total_rear_heave` / `k_heave_rear` which both equal `rear_third_nmm + 2 *
rear_wheel_rate`.

### Severity: LOW — Unused `field` import

**File**: `solver/corner_spring_solver.py:51`

**Behavior**: `from dataclasses import dataclass, field` — `field` is never
used (the dataclass uses default `None` + `__post_init__` to initialize the
two dict fields).

**Fix applied**: removed `field` from the import; switched
`parameter_search_status` and `parameter_search_evidence` from `dict = None`
+ `__post_init__` initialisation to `field(default_factory=dict)` (idiomatic
dataclass pattern, no behavior change). Net: `field` is now used and
`__post_init__` is gone.

### Severity: LOW — Magic numbers without explanation

**File**: `solver/corner_spring_solver.py:531-532, 908`

**Behavior**:
- `m_sprung_front = max(m_f_corner * 2 - 100, 200)` and
  `m_sprung_rear = max(m_r_corner * 2 - 100, 200)` — `100` is `~50 kg/corner
  unsprung mass × 2 corners`, `200` is a 200 kg sprung floor for safety.
  Comment exists but constants are inline.
- `satisfied=rear_isolation >= 1.2` — `1.2` is the rear minimum frequency
  isolation ratio (less strict than front's `csm.min_freq_isolation_ratio`).

**Fix applied**: lifted to module-level named constants
`_AXLE_UNSPRUNG_MASS_KG = 100.0`, `_SPRUNG_MASS_FLOOR_KG = 200.0`, and
`_REAR_FREQ_ISOLATION_MIN_RATIO = 1.2` with explanatory comments. Intent
is now documented and tunable in one place.

### Severity: LOW — Driver-anchor 20% tolerance is a magic number

**File**: `solver/corner_spring_solver.py:402`

**Behavior**: `if abs(...) / _physics_rear_rate <= 0.20:` accepts the
driver's loaded rear coil when within 20% of the physics target. The 20%
is rationalised in the comment block above (LLTD coupling) but left
inline.

**Fix applied**: lifted to module-level `_DRIVER_REAR_SPRING_TOLERANCE =
0.20` with the same rationale moved to a docstring on the constant.

### Severity: LOW — `_apply_lltd_floor` 5% gap and inline rear-rate clamp

**File**: `solver/corner_spring_solver.py:803, 766-768`

**Behavior**:
- `if lltd_gap <= 0.05` — 5 pp acceptable gap, undocumented constant.
- `approx_rear_rate = rear_third_nmm / max(rear_target_ratio, 0.5)` — `0.5`
  divisor floor that prevents division blowup.

**Fix applied**: lifted `_LLTD_FLOOR_ACCEPTABLE_GAP = 0.05` and
`_REAR_TARGET_RATIO_DIV_FLOOR = 0.5` to module level with comments.

## Cross-slice referrals

### CS-1: solve_chain / full_setup_optimizer should pass perches into corner solver

**Slice**: solver/solve_chain.py + solver/full_setup_optimizer.py

**Issue**: As described in the HIGH-severity Ferrari finding above, all 6
call sites of `solution_from_explicit_rates()` skip the
`front_heave_perch_mm` / `rear_third_perch_mm` kwargs. Step 2 returns these
values (`step2.perch_offset_front_mm`, `step2.rear_third_perch_mm`); they
should be threaded through. Without this fix, Ferrari preload-turn outputs
are always computed against the perch defaults, defeating the whole point
of the empirical model.

Recommended call shape (in solve_chain.py:1213-1222 and 1317-1326):

    step3 = corner_solver.solution_from_explicit_rates(
        ...
        front_heave_perch_mm=step2.perch_offset_front_mm,
        rear_third_perch_mm=step2.rear_third_perch_mm,
    )

### CS-2: Ferrari preload-turn empirical model needs more data

**Slice**: car_model / calibration data

**Issue**: The `_solve_ferrari_torsion_bar_turns()` model has R²=0.51/0.55
from 59 indexed sessions. CLAUDE.md flags Ferrari as having ~23 unique
setups currently. The R² ceiling on these models is likely
small-sample-bound, but verifying that requires the calibration team to
collect more sessions varying perches at constant spring index (and vice
versa). Until then, the model output should be flagged as `weak` in
provenance for Ferrari Step 3 even when the gate passes.

## Verification

### Unit tests

Baseline (before fixes):

    346 passed, 17 skipped, 3 failed (pre-existing failures in
    test_calibration_semantics + test_run_trace, unrelated to corner spring)

After fixes:

    346 passed, 17 skipped, 3 failed (same 3 pre-existing failures)

No regressions introduced.

### E2E smoke

    python -m pipeline.produce --car porsche --ibt "porsche963gtp_algarve gp 2026-04-04 13-34-07.ibt" --wing 12 --sto /tmp/smoke_porsche.sto --json /tmp/smoke_porsche.json --scenario-profile single_lap_safe
    PORSCHE OK

    python -m pipeline.produce --car ferrari --ibt "ferrari499p_hockenheim gp 2026-03-31 13-14-50.ibt" --wing 14 --sto /tmp/smoke_ferrari.sto --scenario-profile single_lap_safe
    FERRARI OK

Both .sto files written, both pipelines exit 0.

## Files touched

- `solver/corner_spring_solver.py` — six fixes applied.
- `docs/audit/2026-04-26/step3-corner-springs.md` — this document.
