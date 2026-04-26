# Audit Slice 8: Solve Chain + Decision Trace + Lap-Time Sensitivity

**Date:** 2026-04-26
**Owned files:**
- `solver/solve_chain.py` (1500 LOC)
- `solver/decision_trace.py` (538 LOC)
- `solver/laptime_sensitivity.py` (1467 LOC)

## Summary

Codebase audit of the solve-chain orchestration, decision trace builder, and lap-time
sensitivity calculator. Two minor consistency fixes applied (bare `or 0.0` and bare
`except ValueError: pass` left over from prior audit rounds). All other previously
documented audit items (round 1 and round 2) verified still in place.

**Tests:** 346 pass, 17 skip, 3 pre-existing failures in unrelated calibration semantics
modules (Acura step-1 gating + run-trace tier mapping). My owned files contribute zero
failures.

## Findings

### F1 — `decision_trace.py:80` left bare `or 0.0` pattern (FIX APPLIED)

Round 1 fix #13 (commit 6831fd3) replaced 5 of 6 instances of the
`getattr(measured, "...", default) or default` anti-pattern with the explicit
`None`-check form. Line 80 (the `diff_preload_nm` / TC / master-cylinder branch) was
missed.

The pattern is dangerous for legitimate falsy-but-meaningful values (e.g.,
`rear_power_slip_ratio_p95 == 0.0` would be coerced to the default 0.0, which happens
to be harmless here, but the inconsistency is silent rot).

**Fix:** Replaced with the same `_raw = getattr(...); val = float(_raw) if _raw is not None else 0.0`
form used elsewhere in the file.

### F2 — `solve_chain.py:689-690` bare `except ValueError: pass` (FIX APPLIED)

Round 1 fix #8 promised "11 silent `except Exception: pass` handlers replaced with
`logger.debug()`." All 11 `Exception` handlers were converted. However, the branching
solver's damper-solve site (line 689-690) used `except ValueError: pass` (intentional —
damper solver raises ValueError when zeta is uncalibrated for a car) without a log line
or comment. The sibling site at line 511-515 has the comment but still uses bare `pass`.

**Fix at line 689:** Added `logger.debug("Damper solve in branching path failed (zeta uncalibrated?): %s", e)`.
**Left at line 511:** Already documented with a multi-line comment; logger noise on every
sequential solve would be high. Acceptable.

### F3 — Aero balance "over-correction" status (NO ACTION — by design)

Audit prompt asked to verify CLAUDE.md round 1 fix #1 ("Aero balance over-correction in
coupling refinement removed at line ~832") was preserved. Verified: it was removed in
commit 6831fd3, then partially reintroduced as a damped 0.6× factor in PR #51 merge
commit 4c86f76 with explicit conflict resolution rationale:

> "Conflict resolution: solve_chain.py: Kept PR #48's 0.6 damping factor for DF balance
> correction (prevents overshoot in nonlinear aero maps) over PR #51's removal of the
> correction entirely."

Current code at line 840-842:
```
correction = inputs.target_balance - actual_balance
# Damping factor 0.6 prevents full-gain overshoot in nonlinear aero maps
corrected_target = inputs.target_balance + 0.6 * correction
```

This is intentional. CLAUDE.md round 1 #1 description is now stale relative to actual
code state (and the merge commit explains why).

### F4 — Falsy-int diff ramp guard (VERIFIED CORRECT)

`solve_chain.py:243-252` (`_enforce_ramp_pair`):
```
_idx = diff_ramp_option_index(car, coast=..., drive=..., default=1)
supporting.diff_ramp_option_idx = 1 if _idx is None else int(_idx)
```

The legitimate `idx=0` case (which corresponds to legal pair `(40, 65)`) is preserved
because the check is `is None`, not `or 1`. Verified `diff_ramp_option_index` returns
`int | None` (registry function returns `default` only when pair is unparsable).

### F5 — `materialize_overrides` σ-cal anchoring (VERIFIED CORRECT)

`solve_chain.py:1188-1210` (the non-explicit Step 2 branch within `materialize_overrides`)
correctly passes `front_heave_current_nmm=_k_current` and `rear_third_current_nmm=_k_rear_current`
to `heave_solver.solve()`, where `_k_current` and `_k_rear_current` are read from
`inputs.current_setup`. The σ-calibration sticky pre-check therefore fires correctly
when materializing candidates.

### F6 — `static_front/rear_rh_mm` honored by rake_solver (VERIFIED CORRECT)

`solve_chain.py:1110-1119` (the `earliest == 1` branch):
```
step1 = rake_solver.solution_from_explicit_offsets(
    target_balance=inputs.target_balance,
    fuel_load_l=inputs.fuel_load_l,
    front_pushrod_offset_mm=overrides.step1.get("front_pushrod_offset_mm", step1.front_pushrod_offset_mm),
    rear_pushrod_offset_mm=overrides.step1.get("rear_pushrod_offset_mm", step1.rear_pushrod_offset_mm),
    static_front_rh_mm=overrides.step1.get("static_front_rh_mm", step1.static_front_rh_mm),
    static_rear_rh_mm=overrides.step1.get("static_rear_rh_mm", step1.static_rear_rh_mm),
)
```

This is the call site that the CLAUDE.md "solution_from_explicit_offsets must honor
caller-provided static (2026-04-07)" note describes. The pin is honored — verified
the `static_front_rh_mm` / `static_rear_rh_mm` defaults reuse the existing step1
value (so callers who didn't override get the seed pin preserved).

### F7 — `clamp_click` (line 143-144) (VERIFIED CORRECT)

The clamp is a 2-line one-shot helper inside `apply_damper_modifiers`. Could in
principle be lifted to `params_util.py` but no other call sites exist. Leave as-is.

### F8 — `_score_current` (line 789-799) (VERIFIED CORRECT)

Reads outer-scope `step1`-`step6` and `car`. Catches generic Exception with
logger.debug. Returns 0.0 on failure (acceptable — caller compares deltas with
`>= prev_score + 0.01`).

### F9 — Modifier application order (line 125-152) (VERIFIED CORRECT)

`apply_damper_modifiers` returns early when no offsets; iterates front then rear; clamps
into car's damper LS/HS ranges. Order is consistent with downstream solver assumptions.

### F10 — `justification_report` decomposition (NO ACTION — readable)

`laptime_sensitivity.py:162-207` is 47 lines of formatted ASCII output. Linear top-to-
bottom flow with no nested conditionals beyond `if s.justification:`/`if s.evidence:`/etc.
Decomposing would add helpers without reducing complexity. Leave as-is.

### F11 — Magic numbers (NO ACTION — documented constants)

`laptime_sensitivity.py` has many magic numbers (e.g., `dt_ms = 50.0 * track_scale` for
brake bias, `dt_ms = 8.0` for diff coast ramp). All are documented in the per-function
docstring with a research-calibrated range, and the module-level constants block (lines
36-77) names the most important ones. Acceptable for a heuristic sensitivity calculator.

### F12 — Decision trace None handling (VERIFIED CORRECT)

`build_parameter_decisions` (line 460+) handles `legality is None` via the
`_legality_status` helper. `_legacy_build_parameter_decisions` catches `AttributeError`/
`TypeError` when a step is None (calibration-blocked) and skips that parameter.
`legality_text` is set conditionally on `legality is not None`.

## Cross-slice referrals

- **slice tracking the analyzer/extract.py LLTD signal:** `decision_trace.py:382-385` and
  `laptime_sensitivity.py:382-385` reference `measured.lltd_measured`. CLAUDE.md confirms
  this is `roll_distribution_proxy` aliased — geometric, not actual LLTD. Both files print
  it as "measured LLTD proxy" / "measured LLTD = ..." in evidence text. Consider a slice
  that owns `analyzer/extract.py` renaming the alias (or removing it) so consumers stop
  surfacing it as if it were a real measurement. **Not in scope here.**

- **slice owning `solver/heave_solver.py`:** my `materialize_overrides` non-explicit
  branch passes `front_heave_current_nmm` correctly, but the σ-calibration sticky behavior
  only fires when the heave solver implements it. Verified the contract lives in the heave
  solver. **Not in scope here.**

- **slice owning `pipeline/produce.py`:** uses `notes` from `SolveChainResult` and
  `decision_trace`. No interface changes needed.

## Verification

```
pytest tests/ -q --ignore=tests/test_webapp_routes.py
346 passed, 17 skipped, 3 failed (3 unrelated: Acura step-1 gating + run-trace tier
mapping in test_calibration_semantics.py and test_run_trace.py — owned by other slices)
```

E2E recipe was attempted but blocked by missing IBT fixtures in this worktree
checkout — not a regression from these edits, as no behavior changed beyond two
log-line additions.

## Files modified

1. `solver/decision_trace.py:80` — replaced bare `or 0.0` with explicit None check.
2. `solver/solve_chain.py:689-690` — replaced bare `pass` with `logger.debug()`.
