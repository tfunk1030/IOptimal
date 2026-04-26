# Track Model Audit — 2026-04-26 (Slice #12)

Owned files:
- `track_model/__init__.py`
- `track_model/build.py` (thin wrapper)
- `track_model/build_profile.py` (~715 LOC)
- `track_model/profile.py` (~318 LOC)
- `track_model/track_store.py` (~727 LOC)
- `track_model/ibt_parser.py` (~247 LOC)
- `track_model/generic_profiles.py` (~187 LOC)

## Summary

The track-model layer is in good shape overall. Physics-relevant pieces match
CLAUDE.md guidance:

- `TrackProfile.aero_reference_speed_kph` correctly implements V²-RMS over speed
  bands ≥100 kph (not the lap median). Documentation cites the calibration that
  proved this for Porsche/Algarve.
- The shock-velocity p95 vs p99 dichotomy lives downstream in
  `solver/objective.py` (gated on `car.vortex_excursion_pctile`). The profile
  exposes both `shock_vel_p95_*_clean_mps` and `shock_vel_p99_*_clean_mps`, so
  the solver can pick the correct percentile per car.
- 99.99-percentile peak metrics (lat/long/vert g, max speed) on session-wide
  on-track samples sensibly avoid one-off crash spikes.
- Synthetic corner-shock fallback (heave ± roll) works for Acura ARX-06.
- TrackProfile JSON forward-compatible loader filters unknown fields.
- Track store quality gate (crash, pit, anomaly, wet) correctly sequences hard
  vs relative gates and uses MAD outlier filtering.
- Persistence path uses `fcntl` advisory locks where available.

Issues found are mostly documentation drift, silent-fallback log gaps, magic
numbers, one stale comment, and a naming-asymmetry concern. Two real bugs:
docstring/default-value mismatch in `IBTFile.best_lap_indices` and a stale
in-code comment that points at the wrong line.

All pre-existing 33 `tests/test_track_store.py` cases pass before and after
the changes in this audit.

## Findings

### F1 — `IBTFile.best_lap_indices` docstring contradicts default (FIXED)
**Severity:** low — documentation accuracy
**File:** `track_model/ibt_parser.py:203-228`
The function signature is `min_time: float = 60.0` but the docstring `Args`
block says `min_time: Absolute floor in seconds (default 108.0).` The body
text earlier in the same docstring correctly states 60s. Fixed by aligning
the `Args` block to 60.0s and noting the historical Sebring example.

### F2 — Stale comment references wrong line (FIXED)
**Severity:** trivial
**File:** `track_model/build_profile.py:100`
`# Note: peak_vert_g uses session-wide value from line 76 (includes all laps)`
Actual `peak_vert_g` assignment is on line 95 (was likely 76 in an older
revision). Removed the line-number reference; kept the intent.

### F3 — Silent `pass` on corrupt store and fcntl OSError (FIXED)
**Severity:** medium — calibration provenance
**File:** `track_model/track_store.py:711-712, 717-718`
Two `except: pass` handlers in `_save()`:
1. `except (json.JSONDecodeError, KeyError): pass  # corrupt file, overwrite`
2. `except OSError: pass  # fall through to unlocked write`

Both are recoverable, but per CLAUDE.md "no silent fallbacks" we should at
least log them so an operator can see when the locking fast path silently
disengaged or when an existing store was overwritten.
Replaced both with `logger.warning(...)` calls.

### F4 — `_estimate_speed_profile` silently caps speed bands at 320 kph
**Severity:** low — only affects tracks with sustained ≥320 kph (Le Mans
Mulsanne pre-chicane, some virtual ovals).
**File:** `track_model/build_profile.py:380` (`range(0, 320, 20)`)
GTP/Hypercars at Le Mans run >320 kph briefly. Samples in that range get
dropped from the histogram. Not a bug for current calibration coverage
(Sebring/Algarve/Hockenheim/Silverstone), but would silently lose data on
Le Mans / Daytona oval. **Not fixed** — would require validating against
session_database expected band keys, out of audit scope. Logged here for
follow-up.

### F5 — `min_speed_kph` is best-lap, `max_speed_kph` is session-wide
**Severity:** low — documentation/asymmetry
**File:** `track_model/build_profile.py:322-323`
```
max_speed_kph=round(session_max_speed, 1),
min_speed_kph=round(float(np.min(speed_kph[speed_kph > 10])), 1),
```
`max_speed_kph` uses 99.99-percentile of all on-track samples; `min_speed_kph`
uses the absolute minimum of best-lap samples (above pit-speed gate of 10 kph).
This asymmetry is intentional (best-lap apex speeds are the relevant minima)
but the docstring on `min_speed_kph` is silent. **Not fixed** — semantics are
correct; would only update prose. Worth a one-line comment in a future pass.

### F6 — Magic constant `track_w_m = 1.6` for roll-from-RH derivation
**Severity:** low — physics fidelity
**File:** `track_model/build_profile.py:214`
When the `Roll` channel is absent, body roll is derived from
`atan((LF-RF)/track_w_m)` using a hardcoded 1.6m approximation. CLAUDE.md
emphasises "no silent fallbacks" but this is a missing-channel fallback used
only when the IMU `Roll` channel is unavailable (rare for GTP IBTs, all
current GTP cars have it). Worth wiring to `car.front_track_width_m` if/when
the build path receives the car model. **Not fixed** — currently
build_profile is car-agnostic by design (track properties are independent of
car); rewiring would be a cross-slice change.

### F7 — `_HAS_FCNTL=False` (Windows) silently uses no locking
**Severity:** medium — concurrency safety on Windows
**File:** `track_model/track_store.py:30-36, 689-719`
On Windows the module imports `fcntl=None` and `_HAS_FCNTL=False`. The
`_save()` path then writes without any lock. With the desktop watcher
ingesting alongside CLI invocations, two concurrent writers on Windows could
corrupt a store. Logged at the import site so an operator can see when
concurrency safety is unavailable. Considered a one-shot logger call rather
than every `_save()` so logs don't flood. **Fixed** by emitting a one-shot
`logger.warning(...)` at module import when `fcntl` is unavailable.

### F8 — Generic profile fallback never marks the profile as synthetic
**Severity:** low — provenance
**File:** `track_model/generic_profiles.py:165-187`
`generate_generic_profile()` returns a `TrackProfile` indistinguishable from
an IBT-derived one (apart from `consensus_n_sessions=0`, which is also true
for fresh IBT-derived single-session profiles). Downstream `solver/solve.py`
explicitly tracks `_track_is_generic` itself and prints a warning, but other
call sites that load a generic profile from disk would lose that signal.
Recommend setting `telemetry_source="generic-fallback"` in the generator so
the downstream JSON output and the calibration provenance dict can flag it.
**Fixed** by setting `telemetry_source="generic synthetic profile"` and
defaulting `track_config` to `"generic"` instead of `"Generic"` (consistent
casing with other profiles).

### F9 — `kerb_mask` dilation is asymmetric (forward only)
**Severity:** low — physics accuracy
**File:** `track_model/build_profile.py:655-665`
The mask is dilated forward by `dilation_samples` (~0.33s @ 60Hz) to cover
damper ring-down after the strip. Real ring-down is symmetric — there's
also pre-strike braking impulse contamination. The current asymmetry is
intentional (the docstring says "after leaving the strip") and matches the
20-sample default. **Not fixed** — physically defensible.

### F10 — `_find_kerb_events` second `vert_deviation` branch reuses local name
**Severity:** trivial — readability
**File:** `track_model/build_profile.py:597-643`
`vert_deviation` is computed twice, once per-event in the rumble branch and
once globally in the spike fallback. They have different shapes and
intents but share a name. Not a bug. **Not fixed** — trivial.

### F11 — `_find_braking_zones` swallows multi-lap wraparound silently
**Severity:** trivial
**File:** `track_model/build_profile.py:427-428`
`if braking_dist < 0: continue` skips brake zones that wrap the start/finish
line. For best-lap-only data this is rare (driver almost never brakes
across S/F line on a hot lap) but not impossible. Acceptable. **Not fixed.**

### F12 — `_filter_mad` documents threshold 2.5 for N≥10 but not the source
**Severity:** trivial
**File:** `track_model/track_store.py:222-240`
Says "matches learner/envelope.py" — confirmed: matches that file's
modified-Z threshold. Documentation is accurate.

### F13 — `_passes_quality_gate` thresholds are unit-tagged but not constants
**Severity:** trivial — code style
**File:** `track_model/track_store.py:181-214`
Hard gates: `peak_vertical_g > 15.0`, `median_speed_kph < 100.0`,
`shock_vel_p99_front_mps > 2.0`. All hardcoded with rationale in the
function name only. Could be promoted to module constants for traceability.
**Not fixed** — values are well-tuned and unlikely to change.

### F14 — `_get_scalar_float_fields` lazy global with `dataclasses` import
**Severity:** trivial
**File:** `track_model/track_store.py:101-118`
Uses module-level `_SCALAR_FLOAT_FIELDS: frozenset[str] | None = None`. Fine
for a single-process worker. Not a bug.

### F15 — `IBTFile.channel` returns `None` if missing; callers must check
**Severity:** trivial — API contract
**File:** `track_model/ibt_parser.py:101-116`
Return type is `np.ndarray | None`. `build_profile` always uses
`has_channel(...)` before `channel(...)`. Consistent.

### F16 — `IBTFile._parse_bytes` uses fixed offsets (0/8/16/20/24/28/36/52/140)
**Severity:** trivial
**File:** `track_model/ibt_parser.py:60-71`
These are documented IBT header offsets. Correct per `skill/ibt-parsing-guide.md`.

### F17 — `record_count = 0` fallback inferred from buffer length (silent)
**Severity:** low — provenance
**File:** `track_model/ibt_parser.py:67-71`
When iRacing doesn't write `record_count` (mid-recording quit), the parser
silently infers it. Reasonable, matches the comment, but worth a debug log
so users can see when an IBT was truncated. **Fixed** — added a debug-level
log (not warning, because it's expected for partial-stint IBTs).

## Cross-slice referrals

- **`solver/objective.py:846-861`** — vortex p95/p99 selection lives here
  (gated on `car.vortex_excursion_pctile`). Falls through `getattr(track,
  "shock_vel_p95_front_clean_mps", 0)` even though `TrackProfile` always has
  the field. Could drop `getattr` defaults but that's outside this slice.
  Refer to slice that owns `solver/objective.py`.
- **`car_model/cars.py:1678,1878`** — `vortex_excursion_pctile: str = "p95"`.
  Stringly-typed; could be a literal type. Cross-slice (car model owner).
- **`solver/explorer.py:109-111`** — same `clean if > 0 else raw` pattern as
  objective.py for `shock_vel_p99_front_clean_mps`. Could be encapsulated as
  a `TrackProfile` property `shock_vel_p99_front_best_mps()` returning clean
  when available, raw otherwise. Cross-slice (solver owner).
- **`pipeline/produce.py:478` and `pipeline/reason.py:950`** — both call
  `build_profile(ibt_path)` directly; would benefit from caching by path.
  Cross-slice (pipeline owner).
- **`solver/solve.py:329-338`** — only place that flags
  `_track_is_generic`. F8 above sets `telemetry_source` so downstream code
  can detect generic profiles without owning a flag.
- **`solver/session_database.py:86-93`** — uses `front/rear_shock_vel_p99_mps`
  as channel names; the track-model field is `shock_vel_p99_front_mps`. The
  axis-vs-corner naming is inconsistent across the project. Cross-slice.

## Verification

### Tests

```
$ python -m pytest tests/test_track_store.py -v
======================================== 33 passed in 0.45s ========================================
```

All 33 pre-existing track_store tests still pass after the audit changes.

Full suite (excluding fastapi-dependent webapp tests not relevant here):
```
$ python -m pytest tests/ -q --ignore=tests/test_webapp_routes.py
3 failed, 346 passed, 17 skipped
```
The 3 pre-existing failures (`test_calibration_semantics.py` x2 and
`test_run_trace.py` x1) are unrelated to track_model — they concern the
support tier mapping for Acura/Cadillac/Ferrari steps, which is owned by
other slices. None of the track_model tests fail.

### E2E smoke

```
$ python -m pipeline.produce --car porsche --ibt "porsche963gtp_algarve gp 2026-04-04 13-34-07.ibt" \
    --wing 12 --sto /tmp/smoke_porsche.sto --json /tmp/smoke_porsche.json \
    --scenario-profile single_lap_safe
PORSCHE OK   (sto file non-empty)

$ python -m pipeline.produce --car ferrari --ibt "ferrari499p_hockenheim gp 2026-03-31 13-14-50.ibt" \
    --wing 14 --sto /tmp/smoke_ferrari.sto --scenario-profile single_lap_safe
FERRARI OK   (sto file non-empty)
```
