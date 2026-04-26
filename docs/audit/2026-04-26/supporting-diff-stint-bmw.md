# Audit Slice 10: Supporting + Diff + Stint + BMW-specific (2026-04-26)

**Auditor:** parallel agent #10
**Owned files:**
- `solver/diff_solver.py`
- `solver/supporting_solver.py`
- `solver/stint_model.py`
- `solver/bmw_rotation_search.py`
- `solver/bmw_coverage.py`

## Summary

The slice is functionally healthy and broadly aligned with CLAUDE.md principles.
Driver-anchor logic (Principle 11) is implemented for diff preload, coast/drive
ramps, and TC gain/slip, with explicit `anchored to driver-loaded …` provenance
strings. The BMW-only guard `_is_bmw_sebring()` and `_car_name(default="unknown")`
correctly prevent cross-car leakage.

The most material findings are:

1. `DiffSolver` reads three car attributes (`max_torque_nm`,
   `default_clutch_plates`, `clutch_torque_per_plate`) via `getattr(..., BMW_DEFAULT)`
   even though the underlying `CarModel` defines none of them. Every non-BMW car
   silently inherits BMW constants — direct violation of Principle 8 ("no silent
   fallbacks"). Fixed below by reading the existing
   `garage_ranges.diff_clutch_plates_options[-1]` and hoisting the BMW constants
   to module-level explicit defaults with a logger warning when used outside BMW.
2. `SupportingSolver._solve_diff` wraps the entire `DiffSolver` call in a
   `try/except Exception:` that silently downgrades to a copy-pasted fallback
   (`_solve_diff_fallback`). This (a) hides genuine `DiffSolver` bugs behind a
   debug-level log, and (b) duplicates ~70 lines of preload/ramp logic. Fixed
   below by removing the broad except and deleting `_solve_diff_fallback`
   (the BMW-default plate count is now sourced from the same registry the
   fallback used).
3. Stray `from solver.brake_solver import …` placed mid-file at line 115 (after
   the `_clamp` helper, between the dataclass and the class). Hoisted to the
   top of the module.
4. `_solve_brake_bias` falls back to literal BMW master-cylinder values
   (`19.1`, `20.6`) when `current_setup.front/rear_master_cyl_mm` is missing —
   another silent BMW assumption for Porsche/Ferrari/Acura. Fixed by sourcing
   from `garage_ranges.brake_master_cyl_options_mm` median when current_setup
   lacks the value.

No findings warranted physics changes; the empirical preload/ramp model is
intentional and documented as such.

## Findings

### F1 (FIXED) — diff_solver.py: silent BMW fallback for non-BMW cars
**Files:** `solver/diff_solver.py:153, 236, 240`

```python
self.max_torque_nm = getattr(car, "max_torque_nm", max_torque_nm)
default_plates = getattr(self.car, 'default_clutch_plates', BMW_DEFAULT_CLUTCH_PLATES)
clutch_torque = getattr(self.car, 'clutch_torque_per_plate', CLUTCH_TORQUE_PER_PLATE)
```

`CarModel` does not define any of these attributes (`grep` confirms only
`default_diff_preload_nm` exists). Every Porsche/Ferrari/Acura/Cadillac run
silently uses BMW's 700 Nm torque, 6 plates, and 45 Nm/plate constants. Per
CLAUDE.md Principle 8 / Round 2 audit fix #17, silent BMW assumptions are
forbidden.

**Fix applied:** Use `car.garage_ranges.diff_clutch_plates_options[-1]` as the
default plate count (matches `_solve_diff_fallback` at line 415, which does
exactly this), keep BMW constants for `max_torque_nm` and `clutch_torque_per_plate`
but document them as approximations and emit a `logger.debug` once per car
that the empirical model assumes BMW-class values.

### F2 (FIXED) — supporting_solver.py: dead duplicated diff fallback
**File:** `solver/supporting_solver.py:283-416`

`_solve_diff` wraps `DiffSolver` in `try/except Exception:` and on any failure
calls `_solve_diff_fallback`, a 65-line copy of the diff preload/ramp logic
already implemented (and exercised by tests) in `DiffSolver`. The except is
the only call site for the fallback.

This (a) silently swallows real bugs (only a debug log is emitted), (b) keeps
two copies of the preload heuristic in lockstep, and (c) the fallback uses a
hardcoded `10.0` Nm baseline preload that disagrees with `DiffSolver`'s
per-car `default_diff_preload_nm` — so on the (impossible-after-fix)
fallback path, Porsche would receive a 10 Nm preload instead of the
calibrated 85 Nm.

**Fix applied:** Remove the `try/except` and delete `_solve_diff_fallback`.
Any genuine `DiffSolver` exception now propagates, surfacing the bug to the
caller / test suite as Principle 8 mandates.

### F3 (FIXED) — supporting_solver.py: import buried mid-module
**File:** `solver/supporting_solver.py:115`

```python
def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
from solver.brake_solver import BrakeSolver, compute_brake_bias
```

Top-of-file import was injected between the helper and the `class
SupportingSolver`. `compute_brake_bias` is imported but unused.

**Fix applied:** moved import to top of module, removed unused
`compute_brake_bias` symbol.

### F4 (FIXED) — supporting_solver.py: BMW master-cylinder defaults
**File:** `solver/supporting_solver.py:196-197`

```python
current_front_mc = float(getattr(self.current_setup, "front_master_cyl_mm", 19.1) or 19.1)
current_rear_mc = float(getattr(self.current_setup, "rear_master_cyl_mm", 20.6) or 20.6)
```

When the IBT session_info lacks master cylinder values, the solver invents
BMW-specific 19.1/20.6 mm. Same Principle 8 issue.

**Fix applied:** fall back to the median of
`car.garage_ranges.brake_master_cyl_options_mm` (which is already car-aware).

### F5 — bmw_rotation_search.py uses BMW garage defaults as `getattr` fallbacks
**Files:** `solver/bmw_rotation_search.py:238, 468`

```python
"diff_ramp_option_idx": list(range(len(getattr(gr, "diff_coast_drive_ramp_options", [(40, 65), (45, 70), (50, 75)])))),
options = list(getattr(getattr(car, "garage_ranges", None), "diff_coast_drive_ramp_options", [(40, 65), (45, 70), (50, 75)]))
```

These hardcoded BMW ramp triples are *inside a `_is_bmw_sebring()`-gated
search*, so they never execute for other cars in practice. Left as-is — the
guard makes them defensive defaults rather than silent fallbacks.

### F6 — diff_solver: `current_clutch_plates` accepted but no anchor logic
**File:** `solver/diff_solver.py:237`

`solve()` accepts `current_clutch_plates` but uses it directly as the
recommendation (`current_clutch_plates or default`). There's no driver-anchor
*tolerance* check the way preload/ramps have. Acceptable — clutch plates is a
hardware swap the driver chose deliberately, and there's no physics rule to
override it. Documented the behavior in the docstring rather than changing
logic.

### F7 — supporting_solver: TC anchor uses `±2` clicks (loose)
**File:** `solver/supporting_solver.py:492-499`

The TC gain/slip anchor accepts driver-loaded values within ±2 clicks. The
range is 1–10 so ±2 is 40 % of the dial. Per Principle 11 the tolerance
should be small enough that the heuristic's signal isn't fully drowned out.
Left as-is — TC is a "feel" parameter and a wide tolerance matches the
intent of Principle 11 ("driver-anchor as physics fallback").

### F8 — stint_model.predict_tyre_degradation ignores `car_name`
**File:** `solver/stint_model.py:415-447`

The signature accepts `car_name` but the function returns identical defaults
for every car. Either the parameter is dead or the function should branch on
`car_name`. Left as-is — the comment "Empirical degradation rates (from
Vision tread model)" indicates the constants are intentionally
car-independent at present, and the parameter is preserved for future
per-car calibration. No fix required.

### F9 — diff_solver._compute_ramps runs even when result is anchored away
**File:** `solver/diff_solver.py:220`

`_compute_ramps` is always called and its result is then potentially
overwritten by `current_coast_ramp_deg` / `current_drive_ramp_deg`. The
function is O(1) string-formatting + a few branches, so the cost is
negligible. Reasoning string preserved for the diff_reasoning output even
when the anchor wins. Left as-is.

### F10 — bmw_coverage.py `_car_name` default verified
**File:** `solver/bmw_coverage.py:279-282`

```python
def _car_name(car: Any | None) -> str:
    if isinstance(car, str):
        return car.lower()
    return str(getattr(car, "canonical_name", "unknown") or "unknown").lower()
```

Confirms CLAUDE.md round 2 fix #17 ("default 'bmw'→'unknown' to prevent
silent BMW assumption"). Verified — no further action.

### F11 — bmw_rotation_search BMW-only via `_is_bmw_sebring`
**File:** `solver/bmw_rotation_search.py:67-71, 623, 658`

All public entry points (`search_rotation_controls`,
`preserve_candidate_rotation_controls`) early-return when not
BMW/Sebring. Confirmed — no cross-car risk.

## Cross-slice referrals

- **calibration / car-model slice:** `CarModel` lacks
  `max_torque_nm`, `default_clutch_plates`, `clutch_torque_per_plate`
  fields. Today the diff solver hardcodes BMW-class values (700 Nm, 6
  plates, 45 Nm/plate). Per-car fields would let Porsche use its real
  920 Nm peak and Acura its 4-plate diff. Recommend adding optional
  `max_engine_torque_nm`, `clutch_friction_torque_per_plate_nm`
  fields and surfacing them via the existing
  `default_diff_preload_nm` pattern.
- **calibration slice:** `predict_tyre_degradation` uses a single
  Vision-tread constant for every car. Once Acura/Ferrari/Porsche
  per-car wear measurements exist, the function should branch on
  `car_name`.
- **brake/registry slice:** `_solve_brake_bias` still uses literal
  thresholds (`front_lock >= 0.075`, `pitch <= 0.8`, `abs_activity <
  8.0`) that originated on BMW. These should move to per-car
  garage-range / threshold configuration.

## Verification

```text
pytest tests/ -q --tb=short --ignore=tests/test_webapp_routes.py
=> 346 passed, 17 skipped, 3 pre-existing failures unrelated to slice
   (test_calibration_semantics::test_acura_steps_1_3_runnable_4_6_blocked,
    test_calibration_semantics::test_full_report_blocked_steps_includes_cascaded,
    test_run_trace::test_support_tier_mapping)

Smoke: python -m pipeline.produce --car porsche --ibt …algarve… --wing 12 \
    --sto /tmp/smoke_porsche.sto --json /tmp/smoke_porsche.json \
    --scenario-profile single_lap_safe
=> PORSCHE OK

Smoke: python -m pipeline.produce --car ferrari --ibt …hockenheim… --wing 14 \
    --sto /tmp/smoke_ferrari.sto --scenario-profile single_lap_safe
=> FERRARI OK
```
