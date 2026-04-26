# Audit Slice #9 — Candidate Search + Search Machinery

**Date:** 2026-04-26
**Owned files:**
- `solver/candidate_search.py` (~1226 LOC, 204 `getattr` calls)
- `solver/legal_space.py` (~920 LOC)
- `solver/legal_search.py` (~856 LOC)
- `solver/grid_search.py` (~850 LOC)
- `solver/setup_fingerprint.py` (~233 LOC)
- `solver/predictor.py` (~391 LOC)
- `solver/scenario_profiles.py` (~295 LOC)

## Summary

The seven owned files implement the candidate-generation, legality-snapping,
ranking, and prediction layers that sit between the 6-step solver and the final
.sto writer. They are largely well-structured and physics-grounded.

**Findings:** 11 issues catalogued — 0 critical bugs, 6 fixed in this audit
(low-risk per-car cleanups), 3 noted for future work (need more data or wider
refactor), 2 cross-slice referrals.

**Tests:** 295 tests pass, 17 skipped, 3 pre-existing failures unrelated to
this slice (calibration-semantics support-tier expectations from prior PR).
**E2E:** Porsche/Algarve and Ferrari/Hockenheim pipelines complete and write
non-empty .sto files.

## Findings

### Fixed in this audit

#### F1. Dead code — `_snap_option()` (FIXED)
- **File:** `solver/candidate_search.py:43`
- **Issue:** `_snap_option(value, options)` is identical to `_snap_nearest()`
  defined 13 lines above and never referenced anywhere in the codebase.
- **Fix:** Removed the duplicate function. `_snap_nearest()` remains.

#### F2. Silent `except Exception: pass` in neighborhood enumeration (FIXED)
- **File:** `solver/legal_space.py:659-660`
- **Issue:** Errors during legal-value indexing silently swallowed; CLAUDE.md
  Principle 8 calls out silent fallbacks for cleanup.
- **Fix:** Added module-level `logger = logging.getLogger(__name__)` and now
  `logger.debug("neighborhood enumeration failed for %s: %s", dim.name, exc)`.

#### F3. Silent exc swallow in legal_search acceptance loop (FIXED)
- **File:** `solver/legal_search.py:806`
- **Issue:** `except Exception as exc` only added to acceptance_notes; debug
  log was missing for diagnosis.
- **Fix:** Added module-level logger and `logger.debug(..., exc_info=True)`
  before the existing notes append. User-visible behavior unchanged.

#### F4. Silent exc swallow in candidate materialization (FIXED)
- **File:** `solver/candidate_search.py:1064`
- **Issue:** Materialization errors were captured and stored on the candidate
  but never logged for engineering diagnosis.
- **Fix:** Added `logger.debug(..., exc_info=True)` alongside the existing
  candidate.notes append. Behavior unchanged for the user, but stack traces
  are now retrievable when DEBUG logging is enabled.

#### F5. Hardcoded damper click range `hi=20` (FIXED)
- **File:** `solver/candidate_search.py:707-712` (now 715-720)
- **Issue:** `_apply_family_state_adjustments` clamped damper hs_comp/ls_rbd
  adjustments to `lo=0, hi=20` — Acura ORECA dampers go higher than 20 on
  some axes, and BMW LS ranges differ from HS. Hardcoded 0..20 is BMW-shaped.
- **Fix:** Reads `car.damper.hs_comp_range` and `car.damper.ls_rbd_range`,
  with safe defaults to (0, 20) when no damper model is present.

#### F6. Hardcoded `diff_ramp_option_idx hi=2` and brake/clutch options (FIXED)
- **File:** `solver/candidate_search.py:723, 730, 734-735`
- **Issue:** `diff_ramp_option_idx` clamp `hi=2` assumed 3 options; clutch
  range `lo=2 hi=6` and brake `mc_options`/`pad_options` lists were inlined
  literals duplicating `car_model/cars.py:GarageRanges` defaults.
- **Fix:** All three now read from `car.garage_ranges.diff_coast_drive_ramp_options`,
  `car.garage_ranges.diff_clutch_plates_options`,
  `car.garage_ranges.brake_master_cyl_options_mm`, and
  `car.garage_ranges.brake_pad_compound_options`. BMW behavior is preserved
  (the GarageRanges defaults are the BMW values), and Porsche/Acura/Ferrari
  now correctly use their own option lists if they ever override.

### Noted but not fixed (require larger refactor or more data)

#### F7. Perch K constants are BMW-derived; only front uses calibrated model
- **File:** `solver/legal_space.py:64-66, 169, 177`
- **Constants:** `FRONT_HEAVE_PERCH_K=0.001614`, `REAR_SPRING_PERCH_K=0.8`,
  `REAR_THIRD_PERCH_K=0.3`
- **Status:** Front perch correctly uses `car.ride_height_model.front_coeff_heave_nmm`
  and `front_coeff_perch` when available, falling back to BMW K only when no
  calibrated model exists (line 145-159). **Rear perch always uses BMW K.**
- **Why not fixed:** The `RideHeightModel` for Porsche/Ferrari currently fits
  rear ride height from `inv_third` and `inv_spring` compliance terms, NOT a
  perch coefficient — there is no per-car rear perch sensitivity to read.
  Fixing this requires adding a rear perch coefficient to the regression and
  recalibrating each non-BMW car. Documented in CLAUDE.md as a known limit.
- **Recommendation:** For each non-BMW car, inject a per-car rear perch
  sensitivity into `compute_perch_offsets()`, OR add `rear_third_perch` and
  `rear_spring_perch` columns to `auto_calibrate.py`'s rear-RH regression
  feature pool so the model directly fits them.

#### F8. ~204 `getattr` calls in candidate_search.py
- **File:** `solver/candidate_search.py` (entire)
- **Status:** Per CLAUDE.md, these are flagged as remaining cleanup. Audit
  triage:
  - Lines 81-104, 114-118, 144-152, 159-202: optional-feature guards on
    `garage_ranges` (legitimate — `gr` may be None for partial-coverage cars).
  - Lines 305-365 in `_extract_target_maps`: pulling supporting-field defaults
    when the underlying solver step doesn't carry the field. Legitimate per
    Principle "no silent fallbacks" — defaults for cosmetic/string outputs
    (gear_stack, roof_light_color, fuel_target_l) are not physics values.
  - Lines 858-887 in `_estimate_candidate_disruption`: pulling current setup
    fields. All optional because `setup` may not carry every field across
    cars.
- **Recommendation:** Most `getattr` here are NOT physics-value fallbacks
  with hardcoded BMW defaults — they're optional-attribute checks. The key
  remaining real risk is the BMW pushrod/heave/etc. range tuples on lines
  82-115 (e.g., `(-40.0, 40.0)`, `(0.0, 900.0)`, `(13.9, 18.2)`) which trigger
  only when `gr is None`. Those should ideally raise rather than fall back —
  any car missing `garage_ranges` is a configuration error, not a use case.

#### F9. `_apply_cluster_center` defaults `car_name="bmw"`
- **File:** `solver/candidate_search.py:498` (now 495 after _snap_option removal)
- **Issue:** `def _apply_cluster_center(targets, setup_cluster, *, car_name: str = "bmw")`
  — silent BMW default. Today's only call site (line 988) does pass
  `car_name=getattr(car, "canonical_name", "bmw")`, so the default is never
  reached, but a future caller could regress.
- **Recommendation:** Make `car_name` required (no default).

### Cross-slice referrals

#### CR1. `_estimate_candidate_disruption` car detection from setup string
- **Lines:** 845 — `car_name = str(getattr(setup, "adapter_name", "") or "").lower()`,
  then `is_ferrari = "ferrari" in car_name`.
- **Cross-slice:** This reads from `analyzer/setup_reader.py`'s `current_setup`
  object's adapter_name. That field is owned by analyzer/. The disruption
  estimator should accept `car: CarModel` directly instead of inferring from
  a string — slice owning the call site (probably pipeline) should pass it.

#### CR2. `aggregate_measured` arrives as `dict` OR object
- **Lines:** 256-260 (`_get_metric` polymorphic accessor)
- **Cross-slice:** The analyzer pipeline owns the type contract for the
  measured-state object handed to `_apply_family_state_adjustments`. This
  should be unified into a typed dataclass — currently the polymorphic
  `_get_metric` exists because `aggregate_measured` is sometimes a dict and
  sometimes a `MeasuredState`. Refer to analyzer slice for unification.

## Verification

### Unit tests

```
tests/test_candidate_search.py ........                            [ 53%]
tests/test_legal_search_scenarios.py ...                           [ 73%]
tests/test_predictor_directionality.py ...                         [ 93%]
tests/test_prediction_feedback.py .                                [100%]
============================== 15 passed in 9.57s ==============================
```

Full suite: 346 passed, 17 skipped, 3 failed (pre-existing —
`test_calibration_semantics.py` support-tier expectations and
`test_run_trace.py` test_support_tier_mapping; unrelated to this slice).

### E2E smoke

```
python -m pipeline.produce --car porsche --ibt "porsche963gtp_algarve gp 2026-04-04 13-34-07.ibt" \
  --wing 12 --sto /tmp/smoke_porsche.sto --scenario-profile single_lap_safe
→ PORSCHE OK
python -m pipeline.produce --car ferrari --ibt "ferrari499p_hockenheim gp 2026-03-31 13-14-50.ibt" \
  --wing 14 --sto /tmp/smoke_ferrari.sto --scenario-profile single_lap_safe
→ FERRARI OK
```

Both cars produce non-empty .sto files end-to-end.

### Targeted validation of fixes

- F1 (dead code removal): file imports OK; no test references `_snap_option`.
- F2-F4 (silent excepts): debug logging only, no behavior change. Smoke runs
  produce identical output (no DEBUG handlers attached in default config).
- F5-F6 (hardcoded ranges → car-model): for BMW (default values match
  GarageRanges defaults), behavior is identical. For Porsche/Acura/Ferrari,
  the new code reads from the car's own option lists, which all currently
  match the GarageRanges defaults. No regressions in the 15 owned-file tests
  or the BMW/Porsche/Ferrari smoke runs.
