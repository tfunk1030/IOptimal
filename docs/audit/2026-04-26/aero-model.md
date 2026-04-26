# Audit slice 11 — `aero_model/`

Date: 2026-04-26
Owner: parallel agent #11
Files audited: `aero_model/__init__.py`, `aero_model/interpolator.py`,
`aero_model/gradient.py`, `aero_model/parse_xlsx.py`.

## Summary

`aero_model/` itself is in good shape. The interpolator builds cubic
`RegularGridInterpolator` surfaces with `bounds_error=False, fill_value=NaN`
and explicitly raises on NaN, which matches the audit map's
"NaN at edges = error" contract. Out-of-range queries are clamped to grid
boundaries with a `>2 mm` debug log. The gradient module computes the
documented central-difference derivatives, applies `car.to_aero_coords()`
correctly, and propagates RH variance through second-order curvature. The
xlsx parser handles both BMW/Cadillac/Acura/Porsche labeled and Ferrari
unlabeled formats and validates outputs against physical L/D and balance
ranges.

The single material problem found is **outside** this slice: two callers in
`solver/solve_chain.py` invoke `aero_surface.df_balance()` with the wrong
arity AND with un-swapped (actual) ride heights, then swallow the resulting
`TypeError` in a bare `except Exception`. This silently disables the entire
post-solve aero-balance + ARB coupling refinement loop. Detail below
(referral C-1).

No other coordinate-inversion violations were found in callers; the
documented contract (`car.to_aero_coords()` before query, `from_aero_coords()`
after) is honored elsewhere. No `TODO`/`FIXME`. Magic numbers in
`interpolator.py` (`STALL_ONSET_MM`, `STALL_HARD_MM`) are documented and have
a static guard. `gradient.py`'s `h=0.5 mm` is a physically-motivated step
(half the typical aero map grid spacing of 1 mm — large enough to step
across an interpolation cell, small enough to capture local slope).

## Caller inventory: coordinate-inversion contract

The contract: every car in `car_model/cars.py` has `aero_axes_swapped=True`
(BMW, Cadillac, Ferrari, Acura, Porsche). The aero map's "front_rh" axis
is physically the **rear** RH and vice-versa. Callers must convert via
`car.to_aero_coords(actual_front, actual_rear)` before
`AeroSurface.df_balance/lift_drag/find_rh_for_balance`, then via
`from_aero_coords()` to read out.

| Caller | Method | Coords as passed | Verdict |
| --- | --- | --- | --- |
| `aero_model/gradient.py:100,104` | `df_balance`, `lift_drag` | wraps with `car.to_aero_coords()` inside `_query_balance/_query_ld` | Correct |
| `solver/rake_solver.py:194-196` | `df_balance`, `lift_drag` (`_query_aero`) | `aero_frh, aero_rrh = car.to_aero_coords(actual_front, actual_rear)` | Correct |
| `solver/rake_solver.py:228-232` | `find_rh_for_balance` | passes `aero_front` as `rear_rh=` (axes-swap aware) | Correct, with comment |
| `solver/rake_solver.py:387,613` | `stall_proximity(actual_front_dyn)` | uses actual front RH, which is what the stall model expects | Correct (stall model is in physical units) |
| `solver/rake_solver.py:867-871` (`_find_free_max_ld`) | `df_balance`, `lift_drag` | iterates `surface.front_rh × surface.rear_rh` (aero coords), and uses `from_aero_coords()` only for the vortex check | Correct |
| `solver/rake_solver.py:1113-1117` (reconcile) | `df_balance`, `lift_drag` | `af, ar = car.to_aero_coords(...)` immediately before query | Correct |
| `solver/objective.py:1169-1171` | `df_balance`, `lift_drag` | `af, ar = car.to_aero_coords(dyn_f, dyn_r)` with explicit comment about BMW swap | Correct |
| `solver/explorer.py` (constructor takes surface) | (no direct query) | n/a | n/a |
| `pipeline/reason.py:2080-2095` | `compute_gradients(surface, car, f_rh, r_rh, …)` | passes actual coords to gradient module, which applies `to_aero_coords` internally | Correct |
| `pipeline/reason.py:3034-3060` | loads surface only, hands off to solvers | n/a | n/a |
| `tests/test_acura_hockenheim.py:245` | `surface.df_balance(front_rh=35.0, rear_rh=40.0)` | passes raw values; values land in interior of grid for both interpretations, so test would not catch a swap regression but it is a sanity test only | Acceptable (test sanity check, not a physics call) |
| **`solver/solve_chain.py:806-815`** | `df_balance` | `aero_surface.df_balance(step1.dynamic_front_rh_mm, step1.dynamic_rear_rh_mm, inputs.wing_angle)` — **3 args; no `to_aero_coords`** | **BROKEN — see C-1** |
| **`solver/solve_chain.py:833-841`** | `df_balance` | same wrong arity + missing axis-swap | **BROKEN — see C-1** |

## Findings (within `aero_model/`)

### A-1 — `parse_xlsx.py` lacks unit tests
Severity: low (informational)
File: `aero_model/parse_xlsx.py`
Behavior: 428-LOC parser handles two distinct xlsx layouts, includes a
`_align_ld_to_balance` extrapolation special-case for Porsche wing 13, and
contains hand-rolled format detection (`_detect_format`). There is **no
direct unit test** for the parser itself. `tests/test_aero_ld_validation.py`
validates the parsed npz/json artefacts post-hoc but never re-parses an
xlsx. Format-detection regressions therefore only surface when someone
manually re-runs `python -m aero_model.parse_xlsx`.
Fix: add at least a smoke test that calls `parse_aero_xlsx` on one
representative xlsx per car and asserts the parsed grid matches the on-disk
npz — no source change needed in `aero_model/` itself.

### A-2 — `_validate_parsed_data` silently passes through `np.nan`
Severity: low
File: `aero_model/parse_xlsx.py:317-326`
Behavior: validation uses `np.nanmin/np.nanmax`, so a grid that is mostly
NaN with a single in-range cell would pass. The earlier `np.all(np.isnan)`
check only rejects a fully-NaN grid.
Fix (out of scope for this audit): add a `np.isnan(ld).mean() < 0.05`
threshold. Not critical because the interpolator itself raises on NaN at
query time and the existing range guards have caught real regressions
(per `tests/test_aero_ld_validation.py` docstring referencing the Porsche
wing-13 corruption).

### A-3 — `_clamp_rh` uses 2 mm log threshold without a config knob
Severity: trivial (informational)
File: `aero_model/interpolator.py:55-64`
Behavior: clamp warnings only fire when the requested RH is more than 2 mm
outside the grid. This is intentional (bisection in `find_rh_for_balance`
and grid-walks in `_find_free_max_ld` repeatedly probe the corners) and is
already at `logger.debug`. No fix recommended; calling it out for the
record.

### A-4 — `find_rh_for_balance` uses 0.01% balance tolerance without an
exposed knob
Severity: trivial
File: `aero_model/interpolator.py:192-199`
Behavior: bisection terminates at `|bal_mid - target| < 0.01` percentage
points after up to 50 iterations. This matches what the rake solver wants
but is not parameterised. No callers currently need the knob.

### A-5 — `_align_ld_to_balance` linear extrapolation is silent
Severity: low
File: `aero_model/parse_xlsx.py:88-106`
Behavior: when the Porsche wing-13 xlsx is missing the `rrh=5 mm` L/D
column, the parser linearly extrapolates from the next two columns and
prints nothing. The behavior is documented in the docstring but produces
no log line, so nobody knows their L/D map's first column is synthetic.
Fix: add a single `logger.warning` (already imported) noting the
extrapolated rear-RH columns.

### A-6 — `_extract_wing_angle` raises generic `ValueError`
Severity: trivial
File: `aero_model/parse_xlsx.py:340-350`
Behavior: filename pattern is rigid (`<n> wing` substring). This is fine
for the existing 33 files but would be brittle if the user adds a file
named e.g. `Porsche LMDH 13deg wing.xlsx`. No change recommended; just
worth knowing if onboarding a new aero map.

## Cross-slice referrals

### C-1 — CRITICAL: `solve_chain.py` aero coupling refinement is dead code
Severity: high (silent physics regression)
File: `solver/solve_chain.py:805-815`, `832-841`
Owner slice: 9 (Solver) — referrer

The refinement loop in `_apply_coupling_refinement` (around line 800)
checks DF-balance residual after each iteration and re-solves Step 1 if
the residual exceeds tolerance. The check is implemented as:

```python
aero_surface = inputs.surface
if aero_surface is not None and hasattr(aero_surface, "df_balance"):
    try:
        actual_balance = aero_surface.df_balance(
            step1.dynamic_front_rh_mm,
            step1.dynamic_rear_rh_mm,
            inputs.wing_angle,                # ← 3rd positional arg
        )
        df_residual = abs(actual_balance - inputs.target_balance)
    except Exception as e:
        logger.debug("DF balance check failed: %s", e)
        df_residual = 0.0
```

`AeroSurface.df_balance(front_rh, rear_rh)` takes two arguments, not
three. Verified at the REPL:

```
TypeError: AeroSurface.df_balance() takes 3 positional arguments but 4
were given
```

So:
1. The call **always raises**.
2. The exception is swallowed at debug level (silent in normal logging).
3. `df_residual` is set to `0.0`, which is always `<= df_tol`, so the
   "DF balance drifted" branch on line 832 never fires.
4. Even the second call site at 833-841 has the same arity bug, so the
   re-solve path would also fail if the gate ever opened.
5. **Both calls also pass actual ride heights to a method that expects
   aero-coord inputs.** Every car has `aero_axes_swapped=True`, so even
   after fixing the arity bug, the caller must wrap with
   `car.to_aero_coords(...)` before querying — exactly as
   `solver/objective.py:1169` and `solver/rake_solver.py:_query_aero` do.

Net effect: the post-solve aero/ARB coupling refinement loop only ever
exits via the LLTD residual or the `max_iterations` ceiling, never via the
DF-balance condition. The aero coupling step is silently a no-op.

Suggested fix (not applied — outside this slice's edit boundary):

```python
from <ctx> import car  # already in scope as inputs.car
af, ar = car.to_aero_coords(
    step1.dynamic_front_rh_mm, step1.dynamic_rear_rh_mm
)
actual_balance = aero_surface.df_balance(af, ar)
```

Also recommend tightening `except Exception:` to
`except (TypeError, ValueError) as e:` and bumping the log level to
`warning` for the first occurrence — current `debug` hides exactly this
class of regression, in violation of the "no silent fallbacks" project
principle.

### C-2 — `solver/solve_chain.py` uses `hasattr(aero_surface, "df_balance")`
gate
Severity: low (defensive code that hides the contract)
File: `solver/solve_chain.py:806`
Owner slice: 9 (Solver) — referrer
Behavior: the `hasattr` check is a stringly-typed duck check. The only
producer of `inputs.surface` is `aero_model.load_car_surfaces`, which
always returns an `AeroSurface`. Drop the `hasattr` and rely on a typed
annotation; this prevents the hasattr branch from masking future renames
of the method.

### C-3 — `_find_free_max_ld` does an O(N×M) double loop on every solve
Severity: low (perf, not correctness)
File: `solver/rake_solver.py:855-872`
Owner slice: 9 (Solver) — referrer
Behavior: `_find_free_max_ld` iterates the full aero grid (typ. 51×46 =
2346 cells) and queries `df_balance` then `lift_drag` per cell. Each
query goes through `RegularGridInterpolator` which itself does a cubic
spline evaluation — ~9 ms per solve in profiling. The interpolator
already exposes `find_max_ld(target_balance, balance_tolerance)` doing
the equivalent, but it queries on the surface's native axes (no axis
swap). Either (a) add a `from_aero_coords`-aware variant to
`AeroSurface`, or (b) leave the rake-solver implementation but cache the
result per (target_balance, front_excursion_p99) tuple. Not blocking.

### C-4 — `tests/test_acura_hockenheim.py:245` does not exercise axis-swap
Severity: trivial
File: `tests/test_acura_hockenheim.py:243-247`
Owner slice: 17 (Tests) — referrer
Behavior: `surface.df_balance(front_rh=35.0, rear_rh=40.0)` lands inside
the grid regardless of which axis is which, so the test passes whether
or not the swap is honored. Not a regression risk for `aero_model/`
proper, but the test name implies a stronger check than it provides.

## Verification

* Read the full content of all four `aero_model/` files. Confirmed:
  cubic interpolation, NaN bounds-error contract, debug-level clamp
  logging, central-difference gradients with `h=0.5 mm`, second-order
  L/D variance cost, two xlsx parser paths, range/shape validation.
* Searched the codebase for `df_balance|lift_drag|find_rh_for_balance|
  find_max_ld|stall_proximity|AeroSurface|load_car_surfaces` and audited
  each call site (table above). All non-`solve_chain.py` callers honor
  the axis-swap contract.
* Reproduced the C-1 `TypeError` at the REPL (see snippet under C-1).
* Ran the full test suite: `python -m pytest tests/ -q --tb=short
  --ignore=tests/test_webapp_routes.py` — 346 passed, 17 skipped, 3
  pre-existing failures in `test_calibration_semantics.py` and
  `test_run_trace.py` that are unrelated to `aero_model/`. (Ignored
  `test_webapp_routes.py` because `fastapi` is not installed in this
  environment.)
* Ran the E2E smoke recipe:
  - `python -m pipeline.produce --car porsche --ibt
    porsche963gtp_algarve\ gp\ 2026-04-04\ 13-34-07.ibt --wing 12 --sto
    /tmp/smoke_porsche.sto …` → exit 0, .sto written → `PORSCHE OK`.
  - `python -m pipeline.produce --car ferrari --ibt
    ferrari499p_hockenheim\ gp\ 2026-03-31\ 13-14-50.ibt --wing 14 --sto
    /tmp/smoke_ferrari.sto …` → exit 0, .sto written → `FERRARI OK`.
* No edits made inside `aero_model/`. Findings A-1..A-6 are
  informational; the only material bug (C-1) is outside the slice
  boundary and has been written up for the solver-slice owner.
