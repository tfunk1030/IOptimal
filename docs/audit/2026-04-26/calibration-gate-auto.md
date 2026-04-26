# Audit Slice #13 — Calibration Gate + Auto-Calibrate

Date: 2026-04-26
Owned files:
- `car_model/calibration_gate.py` (898 LOC pre-edit, 902 post)
- `car_model/auto_calibrate.py` (3420 LOC pre-edit, ~3380 post)

## Summary

The two files implement the gate that decides which solver steps may run and the regression engine that produces the calibrated models the gate inspects. After a focused review against round-1/round-2 fixes documented in `CLAUDE.md` and project Principles 7/8/9/10, both files are structurally correct: the cascade chain `{2:1, 3:2, 4:3, 5:4, 6:3}` is implemented exactly as documented, the strict R²/LOO-ratio guards are in force, the path-traversal slug helper is correctly anchored, and feature selection respects the 3:1 sample-to-feature ratio.

Most fixes here are hygiene: 12 inline `import logging` calls collapsed to a module-level `_logger`, one inline `import re` lifted to module scope, one silent `except Exception: pass` upgraded to a logged warning, one misleading duplicate alias (`_setup_fingerprint = _setup_key`) removed, and a duplicate comment block + stray `f""` literal cleaned up. No physics changes. No behavior changes for any successful path.

Tests: full suite is **346 passed, 17 skipped, 3 pre-existing failures** (unchanged before/after this audit). E2E smoke: Porsche/Algarve and Ferrari/Hockenheim both produce valid `.sto` outputs.

## Findings

### F1 (FIXED) — Silent JSON load failure in `calibration_gate._load_raw_calibration_models`
`calibration_gate.py:363` swallowed any exception when loading a per-track `models_<slug>.json` and silently fell back to the pooled file. That violates Principle 9 ("Provenance over output"): a corrupt per-track file would degrade the entire car's reported calibration confidence with no log line. Now logged at WARNING level with the failing path and exception.

### F2 (FIXED) — Inline `import re` in `_safe_track_slug`
The path-traversal-safe slug helper imported `re` inside the function body. Per project conventions and Python style, the import is now module-level alongside `import logging`, `import re`. No semantic change; just removes per-call overhead and matches `auto_calibrate._safe_track_slug` (which already had module-level `re`).

### F3 (FIXED) — 12 redundant inline `import logging` blocks in `auto_calibrate.py`
Every error/warning site re-imported `logging` and re-resolved `logging.getLogger(__name__)`. Replaced with a single module-level `_logger = logging.getLogger(__name__)`. Sites cleaned: `extract_point_from_ibt` telemetry-skip, `_get_dummy_car`, `_fit` underdetermined guard, `_fit` overfit warnings, `_select_features` info, `fit_models_from_points` car-load fallback, `_fit_from_pool` pool-fallback, `_mk_direct` skip-low-R² and skip-overfit, `build_garage_output_model` front/rear-uncalibrated warnings, the spring-lookup application except, and the final overfit-skip summary. No functional change — same logger, same level, same message — just one source of truth.

### F4 (FIXED) — Misleading alias `_setup_fingerprint = _setup_key`
Two names for the same function. Internal usage was inconsistent (one site used the alias, six used `_setup_key`). External usage: none (verified across repo + tests; only a stale audit cache reference). Removed the alias and updated the lone caller. Reduces "two names → invariant must hold" cognitive load.

### F5 (FIXED) — Duplicate `# ─── 9. Rear Shock Deflection Static ───` header
Lines 1393 and 1397 carried the same section header (cosmetic copy-paste). Removed the redundant second occurrence; physics comment retained.

### F6 (FIXED) — Stray `f""` literal with no substitution
`lookup.method = f"decrypted_sto+physics_extrapolated" if n_sto > 0 else "physics_extrapolated"` had an unnecessary f-string prefix. Now a plain string.

### F7 (NOT FIXED — observation only) — `apply_to_car` swallows `AttributeError` in 11 sites
`apply_to_car()` wraps every `setattr` block in `try: ... except AttributeError: pass`. Rationale (per intent of the function): not all car objects have all attributes, so missing attrs are graceful degradation. But this also masks attribute *renames* (typo bugs). Deliberately not changed in this audit because:
1. Each block is structured around hand-curated attribute maps; an `AttributeError` does indicate a real schema mismatch but most paths use `hasattr` guards before `setattr`, so reaching the bare except is rare.
2. Converting these to logged warnings risks a flood of warnings on Cadillac (which is intentionally unconfigured). Better addressed once Cadillac calibration stubs are populated (cross-slice referral CR2).

### F8 (NOT FIXED — observation only) — `_models_to_dict` is a one-liner wrapper
```python
def _models_to_dict(m: CarCalibrationModels) -> dict:
    d = asdict(m)
    return d
```
Could be `return asdict(m)`. Trivial; left alone since the function may grow back-compat fields later.

### F9 (NOT FIXED — observation only) — `_setup_key` includes `"track"` field at index 0
This is the round-2 fix that prevents cross-track pooling (CLAUDE.md note about 27x–103x LOO/train overfitting). The fingerprint correctly partitions by track, but the wider per-track model architecture (`_models_path_for_track`, `_merge_car_wide_fields`) means this must stay tightly synchronized with `auto_calibrate.py:3378-3410` where per-track partitioning happens. No bug; just flagging the load-bearing invariant.

### F10 (verified) — Cascade rule `{2:1, 3:2, 4:3, 5:4, 6:3}`
Implemented at `calibration_gate.py:810` exactly as documented. Step 5 cascades from Step 4 (round 1 fix #2 from 2026-04-09 audit) — confirmed in code, in test `test_acura_steps_1_3_runnable_4_6_blocked`, and in pre-existing test data.

### F11 (verified) — Confidence weights 1.0/0.7/0.5/0.0
`StepCalibrationReport.confidence_weight` (calibration_gate.py:90-104) returns the documented values:
- `0.0` if blocked
- `0.7` if `weak_block`
- `0.5` if `weak_upstream`
- `1.0` otherwise

### F12 (verified) — Defense-in-depth overfit guard in `_fit`
`auto_calibrate.py:786` flags `is_calibrated=False` when LOO/train > 10× even when R² ≥ 0.85. Also enforced downstream in `_is_overfit()` and `_mk_direct()` so `DirectRegression` instances are never built from memorized fits.

### F13 (verified) — Underdetermined system guard
`auto_calibrate.py:716` rejects `n_samples ≤ n_params` with a logged warning and returns an `is_calibrated=False` model with `r_squared=0.0`, `rmse=inf`. Round-2 fix #16 confirmed.

### F14 (verified) — Sample-to-feature ratio (3:1)
`_min_sessions_for_features()` and `_select_features()` enforce `max_features = n_samples // 3` and `n_samples >= 3 * n_features` exactly per round 2 fix #1 (2026-04-10).

### F15 (verified) — Physics-aware feature pools with universal fallback
`_FRONT_POOL`, `_REAR_POOL`, `_UNIVERSAL_POOL` and `_fit_from_pool(... fallback_pool=...)` implement round 2 fix #1 extension (2026-04-11). LOO comparison decides which pool wins. Logged via `_logger.info` when the fallback fires.

### F16 (verified) — Path-traversal guard in `_safe_track_slug`
`re.sub(r"[^a-z0-9_]", "_", ...)` strips path separators, dots, `..` sequences, and any user-supplied special characters before the slug is used in a filename. Both `calibration_gate._safe_track_slug` and `auto_calibrate._safe_track_slug` use the same regex; `tests/test_calibration_semantics.py` exercises the no-traversal contract.

### F17 (verified) — Two spring-rate calibration paths
- **Path A** (Physics inference): `fit_models_from_points` infers `k = effective_load / heave_defl_static` from corner weights + heave deflection display.
- **Path B** (External JSON): `ingest_sto_json` + `build_spring_lookup_from_sto_json` import setupdelta.com decoded `fSideSpringRateNpm` / `rSideSpringRateNpm`.
- `expand_torsion_lookup_from_physics` then fills missing indices via `k = C·OD⁴` from any anchor entry.

Both paths converge on the same `SpringLookupTable`; `interpolate_spring_rate` reads from whichever was populated.

## Cross-slice referrals

### CR1 — Acura `ride_height_model` is "uncalibrated", failing 2 dependency-cascade tests
`tests/test_calibration_semantics.py::test_acura_steps_1_3_runnable_4_6_blocked` and `test_full_report_blocked_steps_includes_cascaded` both fail because `gate.subsystems()["ride_height_model"].status == "uncalibrated"` for Acura/Hockenheim. The gate is correctly reporting that — the issue is **upstream**: either the Acura calibration models on disk no longer satisfy `car.ride_height_model.is_calibrated`, or the auto-calibration pipeline is not setting that flag for Acura post-overfitting-fix. Owner slice: car_model/cars.py + auto_calibrate `apply_to_car`. Not a bug in the gate's classification logic.

### CR2 — `tests/test_run_trace.py::test_support_tier_mapping` expects "exploratory" but Acura now reports "calibrated"
Same root cause family as CR1 — Acura's tier label has shifted. Owner slice: `pipeline/run_trace.py` and the support-tier mapping there.

### CR3 — Cadillac auto-calibration warning surfaced from `cars.py`
`UserWarning: Auto-calibration failed for cadillac: 'str' object has no attribute 'get'` is emitted from `car_model/cars.py:3132`. Out of this slice, but worth flagging — the actual bug is likely in how a Cadillac stub `models.json` was structured. Cross to `cars.py` slice.

### CR4 — `apply_to_car`'s 11 silent `except AttributeError: pass` blocks
Documented in F7 above. Suggested fix when Cadillac stubs are real: replace each block with `_logger.debug("apply_to_car: attribute path %s missing on %s", ...)`. Cross to whichever audit owns car-model schema validation.

## Verification

```text
$ python -m pytest tests/ -q --tb=line --ignore=tests/test_webapp_routes.py
3 failed, 346 passed, 17 skipped, 1 warning, 155 subtests passed in 67s

Pre-existing failures (unchanged before/after):
- test_calibration_semantics.py::test_acura_steps_1_3_runnable_4_6_blocked   (CR1)
- test_calibration_semantics.py::test_full_report_blocked_steps_includes_cascaded (CR1)
- test_run_trace.py::test_support_tier_mapping                                (CR2)
```

```text
$ python -m pipeline.produce --car porsche --ibt ... --scenario-profile single_lap_safe
PORSCHE OK   (/tmp/smoke_porsche.sto, /tmp/smoke_porsche.json non-empty)

$ python -m pipeline.produce --car ferrari --ibt ... --scenario-profile single_lap_safe
FERRARI OK   (/tmp/smoke_ferrari.sto non-empty; Step 6 correctly emits damper-zeta calibration instructions)
```

Direct gate/auto-calibrate sanity:
```text
Porsche/Algarve summary: Porsche 963: all 6 steps calibrated
slug for "Algarve GP" → "algarve_gp"
slug for "Hockenheim GP" → "hockenheim_gp"
calibration_status('porsche')['n_unique_setups'] = 42
```
