# Audit slice 14 — Garage Prediction + Cars + Registry + Setup Registry

Date: 2026-04-26
Scope:
- `car_model/garage.py`
- `car_model/garage_model.py`
- `car_model/garage_params.py`
- `car_model/cars.py`
- `car_model/registry.py`
- `car_model/setup_registry.py`
- `car_model/legality.py`

## Summary

The slice is largely in good shape: round-1 fixes (registry `track_slug` no
longer routes through `_TRACK_ALIASES`, Acura settable fields backfilled,
Ferrari index-conversion ordering intact, per-axle roll-damper flags wired
into `DamperModel`) all verified present. One real bug fixed in this pass:
Ferrari `supported_track_keys` was stale (`("sebring",)`) even though the
only calibrated Ferrari track is Hockenheim — leaving the calibration gate
pointing at the wrong track and silently advertising un-validated support.

Everything else is either documented behaviour, defensive defaults that
return the same value the bare attribute would have, or out-of-slice (e.g.
the legacy unicode-encoded duplicate Ferrari Hockenheim garage-model file).

## Findings

### F1 (FIXED) — Ferrari `supported_track_keys` pointed at wrong track

`car_model/cars.py:2335` declared
`supported_track_keys=("sebring",)` for `FERRARI_499P`. Concrete Ferrari
calibration evidence on disk lives at:

- `data/calibration/ferrari/models_hockenheim.json` (60 sessions, 23
  unique setups, `calibration_complete: true`).
- `data/garage_models/ferrari/hockenheimring_baden-württemberg.json`.

There is no Ferrari calibration data for Sebring. With the stale value the
calibration gate's `track_support` subsystem advertised "Sebring" as the
authoritative track and would block Hockenheim runs while silently green-
lighting Sebring runs. End-to-end runs still produced a setup because the
gate's `track_support` is informational rather than gating; but the
provenance string was lying about where the calibration came from
(CLAUDE.md Principle 9: provenance over output).

Fix: changed to `("hockenheim",)` with an inline comment pointing at the
calibration file so future renames keep the two in sync.

Verified the Ferrari/Hockenheim end-to-end pipeline still produces a
non-empty `.sto` after the change.

### F2 (NO ACTION) — `zeta_is_calibrated` only set True for BMW

CLAUDE.md hint mentioned "Porsche has `zeta_is_calibrated=True`"; the
codebase reality is the opposite — BMW (`cars.py:2043`) is the only car
with `zeta_is_calibrated=True`. Porsche, Ferrari, Cadillac, Acura all
default to `False` (`cars.py:1353`). The calibration gate
(`car_model/calibration_gate.py:626`) reads
`getattr(car.damper, "zeta_is_calibrated", False)` and downgrades step 6
when the flag is False — exactly what CLAUDE.md describes as Porsche
"Step 6 blocked". The slice's behaviour matches the documented intent;
the audit hint was the stale piece.

### F3 (NO ACTION) — `garage_model.GarageModelBuilder._model_path` uses
config-less slug

`garage_model.py:386-388` calls `track_slug(track)` (no config arg),
producing files like `sebring_international_raceway.json`. This is the
schema the on-disk files use today, so the call is correct. The data-
hygiene oddity that Ferrari has both
`hockenheimring_baden-württemberg.json` and
`hockenheimring_baden_w_rttemberg.json` predates this slice and is a
filesystem leftover from an earlier unicode-mishandling fix. Out of scope
to delete here.

### F4 (NO ACTION) — `garage.py` `_extract_or_warn` and the `or 0` falsy
guards on ARB blade

`garage.py:109-110` uses `getattr(setup, "front_arb_blade", 0) or 0`.
"Index 0" is not a legal ARB blade (real ranges start at 1 — see
`garage_params.py` per-car ARB defs and `legality.py:ParameterSchema`),
so the falsy short-circuit cannot misclassify a legal value. The pattern
is defensive against `None` and missing attribute and is benign.

### F5 (NO ACTION) — `setup_registry.diff_ramp_option_index` falsy-int
risk

The pairing block correctly uses `coast is not None and drive is not
None`, so `coast=0` (or `drive=0`) is preserved. The `option_idx`
fallback in `diff_ramp_pair_for_option` likewise tests `is not None`. No
falsy-int hazards remain.

### F6 (NO ACTION) — `RideHeightModel` / `DeflectionModel` carry both
linear and `1/k` coefficient slots

Verified `GarageOutputModel` in `garage.py` reads both
`front_coeff_inv_heave_nmm` (lines 311, 491-499) and
`rear_coeff_inv_third_nmm` / `rear_coeff_inv_rear_spring_nmm`
(lines 322-324, 526-543). The inverse-pushrod helpers
`front_pushrod_for_static_rh` and `rear_pushrod_for_static_rh` use the
same `1/k` terms (lines 665, 730), satisfying the bisection consistency
requirement in CLAUDE.md (Phase 2 fix). DirectRegression's
`_EXTRACTORS` dictionary has compliance entries for all three axes
(`inv_heave`, `inv_rear_third`, `inv_rear_spring`, `inv_od4`,
`fuel_x_inv_third`, `fuel_x_inv_spring`).

### F7 (NO ACTION) — Acura settable-field round-1 fix is in place

All nine fields called out in the round-1 audit (`front_roll_hs_slope`,
`rear_3rd_{ls,hs}_{comp,rbd}`, `front_roll_spring_nmm`,
`front_roll_perch_mm`, `front_arb_setting`, `rear_spring_nmm`) have
both `FieldDefinition` entries and `_ACURA_SPECS` rows in
`setup_registry.py`. Confirmed via grep.

### F8 (NO ACTION) — `track_slug` no longer routes through
`_TRACK_ALIASES`

Confirmed `registry.track_slug` (lines 152-177) only does
`display_name.lower().replace(" ", "_")`; only `track_key` (lines
180-190) consults `_TRACK_ALIASES`. Matches CLAUDE.md round-1 entry.

### F9 (NO ACTION) — Ferrari index decoding consistent across layers

`GarageSetupState.from_current_setup(setup, car=car)` (garage.py:64-113)
decodes Ferrari/Acura indexed values to physical N/mm using
`heave_spring.front_rate_from_setting`,
`corner_spring.rear_bar_rate_from_setting`, etc. — same lookup tables
also used by `validate_and_fix_garage_correlation` in
`output/garage_validator.py` and the Ferrari public-output remapping in
`setup_registry._ferrari_public_numeric_value`. The
`FerrariIndexedControlModel` 4-table calibration (cars.py:2267-2320) is
the single source of truth; the index↔physical interpolation is shared.

### F10 (NO ACTION) — TODO/FIXME / silent except / magic numbers

- `cars.py:2851` carries a "TODO: Calibrate via DSSV click-sweep when
  data available." This is a documented limitation (BMW shim-stack
  coefficients fall back when DSSV measurements aren't yet available)
  and matches CLAUDE.md "Porsche 963" caveats. Leave in place.
- `garage_model.py:397` swallows `Exception` from `from_dict` to recover
  from corrupted JSON. Acceptable for a builder that auto-rebuilds, but
  noted; not in this slice's mandate to fix.
- Magic-number defaults in `garage.py:280-300`
  (`default_front_pushrod_mm = -25.5`, `front_rh_floor_mm = 30.0`,
  etc.) are the documented BMW/Sebring baselines used only when later
  solver stages haven't filled in real values; per-car overrides come
  from `apply_to_car()` after calibration. No action.

## Cross-slice referrals

- **Output / setup writer** (slice owning `output/setup_writer.py` and
  `output/garage_validator.py`): consider de-duplicating the Ferrari
  Hockenheim garage-model JSON files (`data/garage_models/ferrari/`).
  The `hockenheimring_baden_w_rttemberg.json` (with the `w_rttemberg`
  unicode-replacement form) appears to be a stale orphan — current
  writer uses the proper unicode form.
- **Solver** (slice owning `solver/supporting_solver.py`): pre-existing
  collection error `ImportError: cannot import name
  'compute_brake_bias' from 'solver.supporting_solver'` blocks four
  test files at collection. Not a regression from this slice.
- **Calibration gate** (slice owning `car_model/calibration_gate.py`):
  the `track_support` subsystem now correctly reflects Ferrari →
  Hockenheim (was previously misadvertising Sebring). No code change in
  the gate is required, but downstream coverage tests that hard-coded
  the old Ferrari support tier should be re-checked.

## Verification

- `pytest tests/ -q --tb=line` excluding the four pre-existing
  collection-error files: **317 passed, 3 failed, 16 skipped**. The
  three failures are pre-existing (calibration-gate semantics + support
  tier mapping in test_run_trace) and are independent of this slice's
  edits — they reproduce on `main` HEAD before the change.
- Ferrari/Hockenheim end-to-end smoke
  (`pipeline.produce --car ferrari --ibt
  ferrari499p_hockenheim ... --wing 14 --sto /tmp/smoke_ferrari.sto`):
  **PASS** (non-empty .sto written; same blocked-step output as
  pre-change run).
- Porsche/Algarve end-to-end smoke
  (`pipeline.produce --car porsche --ibt
  porsche963gtp_algarve ... --wing 12 --sto /tmp/smoke_porsche.sto`):
  **PASS** (non-empty .sto written).
