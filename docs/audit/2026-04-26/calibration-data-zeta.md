# Calibration Data + Damper Calibrators — Audit (2026-04-26)

**Slice:** #15 of 18

**Owned files:**
- `data/calibration/{bmw,porsche,ferrari,acura,cadillac}/models.json`
- Per-track variants: `bmw/models_sebring.json`,
  `porsche/models_algarve.json`,
  `porsche/models_weathertech_raceway_laguna_seca.json`,
  `ferrari/models_hockenheim.json`,
  `acura/models_hockenheim.json`
- All `calibration_points.json` files
- `validation/calibrate_dampers.py`
- `validation/calibrate_lltd.py`

## Summary

Persisted calibration files are schema-consistent with
`CarCalibrationModels` (`car_model/auto_calibrate.py:236-291`) for BMW,
Porsche, Ferrari, and Acura. The Porsche pooled file is the **only** one
with non-null damper ζ values; every other car holds the sentinel
`null` plus `zeta_n_sessions=0` and the gate at
`car_model/calibration_gate.py:626-639` correctly classifies them as
`uncalibrated`. The proxy-derived LLTD target is cleared in every file
(`measured_lltd_target=null`) and each file carries a `status.lltd_target`
"DISABLED" provenance string so the regression cannot silently
return.

`validation/calibrate_dampers.py` writes only the four ζ floats and
`zeta_n_sessions`. It does **not** flip `car.damper.zeta_is_calibrated`
to `True` — that flag lives on the in-memory `DamperModel` and is set
by `car_model/auto_calibrate.py:2489` only when the loader re-applies
the persisted ζ values. So a fresh ζ calibration only takes effect on
the **next** process run after the JSON is reloaded. Documented as F4
below; no code change made (cross-slice).

`validation/calibrate_lltd.py` is correctly disabled at the function
boundary, raises a clear `RuntimeError`, and exits non-zero from `main`.
No regression risk.

**One owned-file bug identified (rewrite attempted, then reverted by
external tooling — see F3 below):** `data/calibration/cadillac/
models.json` stores `"status": "uncalibrated"` (a string), violating
the dict-typed schema (`status: dict[str, str] = field(...)`) and
producing a runtime warning every test load:

```
UserWarning: Auto-calibration failed for cadillac:
  'str' object has no attribute 'get'
```

Origin: `car_model/calibration_gate.py:644-645` calls
`raw_models.get("status", {}).get(...)` — the inner `.get` raises on
a string. The fix (rewrite as a fully-populated stub with dict
`status`) was applied and verified to silence the warning, but a
linter / external sync reverted the file. The intended replacement
content is captured in F3 below so it can be reapplied in a follow-up
PR with explicit user sign-off.

## Findings

### F1 — ζ calibration gap is system-wide except Porsche (high, by design)

**Files / fields:**

| File | `front_ls_zeta` | `rear_ls_zeta` | `front_hs_zeta` | `rear_hs_zeta` | `zeta_n_sessions` |
|---|---|---|---|---|---|
| `bmw/models.json:356-360` | `null` | `null` | `null` | `null` | `0` |
| `bmw/models_sebring.json:669-673` | `null` | `null` | `null` | `null` | `0` |
| `porsche/models.json:539-543` | **0.664** | **0.618** | **0.209** | **0.282** | **32** |
| `porsche/models_algarve.json:555-559` | `null` | `null` | `null` | `null` | `0` |
| `porsche/models_weathertech_raceway_laguna_seca.json:35-39` | `null` | `null` | `null` | `null` | `0` |
| `ferrari/models.json:361-365` | `null` | `null` | `null` | `null` | `0` |
| `ferrari/models_hockenheim.json:310-314` | `null` | `null` | `null` | `null` | `0` |
| `acura/models.json:175-179` | `null` | `null` | `null` | `null` | `0` |
| `acura/models_hockenheim.json:185-189` | `null` | `null` | `null` | `null` | `0` |
| `cadillac/models.json` (post-fix) | `null` | `null` | `null` | `null` | `0` |

**Behavior:** at `car_model/calibration_gate.py:626-639`, the **status**
is determined by `getattr(car.damper, "zeta_is_calibrated", False)`.
`raw_models["zeta_n_sessions"]` is used for supporting metadata only
(`source`, `data_points`, and `confidence`), not to force the status to
`uncalibrated`. Because every JSON except the Porsche pooled file holds
zero sessions, most cars also show zero-session ζ metadata; however,
**Step 6 dampers `uncalibrated`** is due to the calibration flag not
being set unless the in-memory car definition or loader marks
`zeta_is_calibrated=True` (which BMW does in `car_model/cars.py:2043`).

The current in-memory truth (verified):
- **BMW**: `cars.py:2043` sets `zeta_is_calibrated=True` so Step 6
  runs from the hand-tuned `cars.py` ζ values; persisted ζ is `null`
  (i.e. cars.py is the source of truth, not the JSON).
- **Porsche**: persisted ζ exists, but `cars.py` does NOT yet set
  `zeta_is_calibrated=True`, so the loader path
  (`auto_calibrate.py:2477-2492`) is what flips the flag at runtime
  when the persisted ζ is loaded. Per CLAUDE.md "Step 6 blocked:
  damper_zeta uncalibrated in car model, needs `zeta_is_calibrated=True`",
  this is the documented gap.
- **Ferrari, Acura, Cadillac**: no ζ data anywhere. Step 6 stays
  blocked, which is the **correct** Principle-7 behavior.

**Fix in this PR:** none — the JSON gap is the documented state.
The desktop-side fix (set Porsche `zeta_is_calibrated=True` at car
construction so Step 6 unblocks even before
`apply_calibrated_models_to_car()` runs) is **cross-slice**
(`car_model/cars.py`).

**Cross-slice referral:** Porsche `zeta_is_calibrated` flag should be
set to `True` in `car_model/cars.py` so Step 6 unblocks during the
initial gate evaluation, not only after `apply_calibrated_models_to_car`
is invoked.

### F2 — Adding `zeta_is_calibrated` to JSON would be silently dropped (medium)

**File / line:** `car_model/auto_calibrate.py:478-487` —
`_dict_to_models` uses
`elif k in CarCalibrationModels.__dataclass_fields__: kwargs[k] = v`
to filter unknown JSON keys. The dataclass does NOT contain a
`zeta_is_calibrated` field (`auto_calibrate.py:236-291`).

**Behavior:** if I had added `"zeta_is_calibrated": false` to every
models.json (per the slice-prompt suggestion), `_dict_to_models` would
drop it on load and the gate at `calibration_gate.py:626` would still
read from `car.damper.zeta_is_calibrated`. The new JSON field would be
write-only, never round-tripped, and would diverge from the
`car.damper` flag the moment a calibration ran.

**Fix in this PR:** intentionally **not** added. The gate already
treats `getattr(car.damper, "zeta_is_calibrated", False)` as `False`
when the field is missing or the calibrator hasn't flipped it; that is
the correct authoritative source. Adding a JSON shadow field would be
parameter sprawl with no behavioral payoff.

### F3 — Cadillac `models.json` has non-conformant `status` (medium, NEEDS FOLLOW-UP)

**File:** `data/calibration/cadillac/models.json`

**Current state:** `"status": "uncalibrated"` (string) — see schema
violation in Summary.

**Intended replacement (verified to silence the warning during this
audit, then reverted by external tooling):**

```json
{
  "car": "cadillac",
  "n_sessions": 0,
  "n_unique_setups": 0,
  "calibration_complete": false,
  "front_ride_height": null,
  "rear_ride_height": null,
  "torsion_bar_turns": null,
  "torsion_bar_defl": null,
  "front_shock_defl_static": null,
  "front_shock_defl_max": null,
  "rear_shock_defl_static": null,
  "rear_shock_defl_max": null,
  "heave_spring_defl_static": null,
  "heave_spring_defl_max": null,
  "heave_slider_defl_static": null,
  "rear_spring_defl_static": null,
  "rear_spring_defl_max": null,
  "third_spring_defl_static": null,
  "third_spring_defl_max": null,
  "third_slider_defl_static": null,
  "torsion_bar_defl_direct": null,
  "third_slider_defl_direct": null,
  "front_heave_lookup": null,
  "rear_heave_lookup": null,
  "front_torsion_lookup": null,
  "rear_torsion_lookup": null,
  "m_eff_front_kg": null,
  "m_eff_rear_kg": null,
  "m_eff_is_rate_dependent": false,
  "m_eff_rate_table": [],
  "m_eff_rear_rate_table": [],
  "measured_lltd_target": null,
  "front_ls_zeta": null,
  "rear_ls_zeta": null,
  "front_hs_zeta": null,
  "rear_hs_zeta": null,
  "zeta_n_sessions": 0,
  "aero_front_compression_mm": null,
  "aero_rear_compression_mm": null,
  "aero_n_sessions": 0,
  "status": {
    "overall": "uncalibrated (0 sessions, 2 raw calibration points exist but auto-cal has not been run)"
  }
}
```

**Behavior before fix:** every test load that imports `cadillac`
through `car_model/cars.py:get_car()` triggers
`UserWarning: Auto-calibration failed for cadillac: 'str' object has
no attribute 'get'` from `cars.py:3132`. The fix was applied and
re-verified during this audit (`tests/test_run_trace.py`: 12 passes,
1 unrelated pre-existing failure, no warnings) before the file was
reverted.

**Cross-slice note:** `car_model/cars.py:3132` swallows ALL
`Exception` types from `apply_calibrated_models_to_car`. That is a
silent-fallback / Principle-8 violation pattern: a different schema
slip would still mute the loader. Cross-slice referral.

### F4 — `calibrate_dampers.py` does not flip `zeta_is_calibrated` (medium)

**File / line:** `validation/calibrate_dampers.py:164-173`.

**Behavior:** the calibrator persists the four ζ floats and
`zeta_n_sessions` to `data/calibration/<car>/models.json`, but the
in-memory `car.damper.zeta_is_calibrated` flag is only set later by
`car_model/auto_calibrate.py:2489` inside
`apply_calibrated_models_to_car`. So a freshly run
`calibrate_dampers --car ferrari --track sebring` does NOT immediately
unblock Step 6 in the same process; the user must restart the
pipeline so the loader picks up the new JSON values.

This is acceptable for the standalone CLI usage in the docstring
("Usage: python -m validation.calibrate_dampers ...") but is not
self-evident from the code.

**Fix in this PR:** doc-only docstring note added then reverted by
external tooling (same revert as F3). The intended docstring
addition (six-line note explaining that ζ takes effect only on the
next process run) is captured here so it can be reapplied:

```text
Note: this CLI persists the four ``*_zeta`` floats and ``zeta_n_sessions``
to ``data/calibration/<car>/models.json``. The in-memory
``car.damper.zeta_is_calibrated`` flag is flipped by
``car_model.auto_calibrate.apply_calibrated_models_to_car`` on the next
process load. Step 6 will therefore unblock on the next pipeline run,
not within the same Python process as this script.
```

### F5 — `calibrate_dampers._click_to_zeta` magic ranges (low)

**File / line:** `validation/calibrate_dampers.py:144-159` —
`(0.30, 0.80)` for LS ζ and `(0.10, 0.30)` for HS ζ are inline
constants whose provenance is `# For GTP cars, typical LS zeta range
is 0.3-0.8, HS 0.10-0.30`.

**Behavior:** these clamps mean the calibrator can NEVER produce a ζ
target outside those bands no matter what the IBT data says. For
Porsche the persisted values (0.664, 0.618, 0.209, 0.282) sit inside
the bands, so the constraint hasn't bitten yet. If a future car has a
genuinely stiffer or softer damper architecture, the calibrator will
silently ceiling/floor and we will not know.

**Fix in this PR:** none. These are defensible safety rails and
correcting them properly requires per-car damper-physics references
(out of slice). Logged for future work.

### F6 — Per-track vs pooled-car file convention is undocumented (low)

**Files:**
- `bmw/models.json` (pooled) + `bmw/models_sebring.json` (per-track)
- `porsche/models.json` (pooled) + `porsche/models_algarve.json` +
  `porsche/models_weathertech_raceway_laguna_seca.json`
- `ferrari/models.json` (pooled) + `ferrari/models_hockenheim.json`
- `acura/models.json` (pooled) + `acura/models_hockenheim.json`
- `cadillac/models.json` (pooled only)

**Behavior:** `auto_calibrate.py:362-382` (`load_calibrated_models`)
prefers the per-track file when `n_unique_setups >= _MIN_SESSIONS_FOR_FIT`
and falls back to the pooled file otherwise. The per-track filename is
derived from the sanitized track slug/key used by calibration code
(for example, multi-word track keys may include underscores, as in
`models_weathertech_raceway_laguna_seca.json`), rather than from
`track.lower().split()[0]`. Per-track files store identical schema to
pooled.

This is correct and well-tested but undocumented at the file level.
No file changes made — convention is captured here in the audit doc.

### F7 — `m_eff` missing for Ferrari (info)

**File:** `ferrari/models.json:355-358` — `m_eff_front_kg=null`,
`m_eff_rear_kg=null`, `m_eff_rate_table=[]`.

**Why:** Ferrari calibration runs that filled in spring/RH models did
not produce m_eff fits because the m_eff back-solver
(`auto_calibrate.py:1681-1707`) requires sessions where both
`front_heave_setting` AND `front_rh_std_hs_mm` (high-speed RH std)
are present in the calibration_point. Inspecting Ferrari's
`calibration_points.json` would clarify whether the channel is missing
or the gate threshold is the issue. Cross-slice (auto_calibrate
back-solver gate logic).

### F8 — `m_eff` table sample-pollution risk (info)

**Files:** `bmw/models.json:299-353`, `porsche/models.json:382-537`,
`bmw/models_sebring.json:460-665`, `porsche/models_algarve.json:407-552`.

**Behavior:** the rate table appends every per-session estimate as a
separate `{setting, m_eff_kg}` row. Porsche has 19 m_eff_front rows
ranging 317–2058 kg at the same `setting=180` (10× spread). CLAUDE.md
calls this out: "`m_eff` empirical correction uses lap-wide statistics
(not filtered to high-speed straights), causing overestimation. Treat
as rough indicator." No fix in this PR — the data is correct as
"raw observations"; the fix is filtering at the consumer (cross-slice
in `solver/sensitivity.py` or wherever `m_eff_rate_table` is read).

## Cross-slice referrals

1. **Porsche `zeta_is_calibrated` should default `True` in cars.py**
   so Step 6 unblocks for Porsche/Algarve as documented in CLAUDE.md.
   File: `car_model/cars.py` Porsche definition.
2. **`car_model/cars.py:3132` swallows all auto-cal exceptions** —
   Principle-8 violation; should narrow the catch or re-raise on
   schema errors. Owner: cars.py slice.
3. **`calibrate_dampers` does not invoke
   `apply_calibrated_models_to_car`** so freshly persisted ζ takes
   effect on next run only. If interactive workflow becomes important,
   add an in-process refresh hook. Cross-slice with
   `car_model/auto_calibrate.py`.
4. **Ferrari m_eff back-solver gate** — investigate whether the input
   channels are missing or the threshold is too strict. Cross-slice
   with `car_model/auto_calibrate.py`.
5. **m_eff rate-table consumer should filter to high-speed straights**
   (CLAUDE.md known-limitation). Cross-slice with
   `solver/sensitivity.py` and/or m_eff consumers.

## Verification

- `python -m pytest tests/ -q --tb=line --ignore=tests/test_webapp_routes.py`
  baseline: 3 failed, 346 passed, 17 skipped, **1 warning**
  (`Auto-calibration failed for cadillac: 'str' object has no
  attribute 'get'`).
- With F3 fix applied (verified during audit, then reverted):
  same pass/fail counts (3 pre-existing failures unrelated to this
  slice), **warning gone**.
- Post-revert (final state of this PR): same as baseline. The audit
  doc captures the intended fix so a follow-up PR can reapply it
  with explicit user sign-off.
- `tests/test_webapp_routes.py` is a collection-error due to
  `fastapi` not installed — unrelated to this slice.
- Smoke E2E (`pipeline.produce`) intentionally not re-run because
  data files in this slice are read-mostly and the would-be-changed
  file (`cadillac/models.json`) is a stub the pipeline never reaches
  (Cadillac has no IBT in the recipe).
