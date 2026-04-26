# Step 1 Rake / Ride Heights — Audit (2026-04-26)

**Slice:** #1 of 18 — `solver/rake_solver.py` (1267 LOC)

## Summary

`solver/rake_solver.py` is structurally sound. The 2026-04-07 fix to
`solution_from_explicit_offsets()` (honoring caller-provided
`static_front_rh_mm` / `static_rear_rh_mm` rather than recomputing from
baseline springs) is in place and correct (lines 540-575). The
V²-RMS aero-reference-speed pattern is consistently applied in
`_build_solution()`, `_solve_pinned_front()`, and
`solution_from_explicit_offsets()`.

Six findings — one medium-severity physics bug in `_solve_free()`
(inconsistent compression scaling against the static floor), one
medium silent-fallback violation (Principle 8) on `fuel_capacity_l`,
plus four low/info-grade comment & log-message cleanups. All fixed in
this PR. `_find_free_max_ld()` is 22 LOC (not ~200 as the audit
prompt indicated), simple, and does not need decomposition.
`_compute_vortex_threshold_mm()` is owned by `solver/objective.py`
(not `rake_solver.py`) — referred cross-slice.

Test baseline: **346 passed, 3 failed (pre-existing, unrelated to
rake), 17 skipped**. Porsche & Ferrari E2E pipelines produce valid
.sto files. Post-fix counts unchanged.

## Findings

### F1 — `_solve_free` uses ref-speed compression for the static-floor constraint (medium)

`solver/rake_solver.py:782` (pre-fix):

```python
min_front_for_static = self.car.min_front_rh_static - comp.front_compression_mm
```

`comp.front_compression_mm` is the static field defined at
`comp.ref_speed_kph` (230 kph). Every other code path in this file
uses `comp.front_at_speed(track.aero_reference_speed_kph)` (V²-RMS
over speed bands ≥100 kph) — the consistent operating point validated
against IBT measurements (CLAUDE.md, Apr 7 2026).

Consequence: in free-optimization mode the lower bound on dynamic
front RH is set using the at-230-kph compression, which is larger
than the at-track-speed compression (Algarve 187 kph: 9.9mm vs the
15mm encoded). The static-floor constraint is therefore *too loose*
— the optimizer can choose a dynamic front RH that, when converted
back to static via the V²-RMS compression in `_build_solution()`,
falls below `car.min_front_rh_static`. The post-build static clamp
then snaps it back up, silently breaking the relationship the
optimizer was solving against.

**Fix:** Use the same V²-RMS reference speed used everywhere else.
Mirrors the pattern already in `_solve_pinned_front` (lines 657-666).

### F2 — Silent `getattr` fallback on `fuel_capacity_l` (medium, Principle 8)

`solver/rake_solver.py:441` (pre-fix):

```python
fuel_load_l = getattr(self.car, 'fuel_capacity_l', 89.0)
```

CLAUDE.md states "all LMDh GTP = 88.96L" and Principle 8 forbids
silent baseline fallbacks. `CarModel.fuel_capacity_l` is defined for
every car in `car_model/cars.py`. The hardcoded 89.0 default would
silently mask any car-model regression.

**Fix:** Direct attribute access `self.car.fuel_capacity_l`.

### F3 — Misleading log message in `_solve_free` SLSQP except handler (low)

`solver/rake_solver.py:833` (pre-fix):

```python
except Exception as e:
    logger.debug("Rear RH search iteration failed: %s", e)
```

This handler wraps the SLSQP minimizer over BOTH front and rear, not
a rear-only search. The message is copy-pasted from the
`_solve_pinned_front` fallback at line 703, where the rear-only
context is correct. In the free-optimization path the log misdirects
debugging.

**Fix:** "SLSQP free-optimization iteration failed".

### F4 — Stale `# Use track median speed for compression` comments (low)

`solver/rake_solver.py:252` and `:655` (pre-fix) say "Use track
median speed for compression instead of fixed reference speed", but
the code immediately below uses `track.aero_reference_speed_kph`,
which is the V²-RMS reference (NOT the median, per the explanatory
comment that follows). The leading "median speed" line contradicts
the actual implementation and confuses readers.

**Fix:** Remove the contradictory leading lines; the V²-RMS
explanation that follows is correct and self-contained.

### F5 — `import warnings` inside hot path (info)

`solver/rake_solver.py:691` and `:744` import `warnings` inside the
solver body — twice in `_solve_pinned_front`. Module-level import is
the conventional pattern.

**Fix:** Hoist `import warnings` to module top. (Negligible runtime
benefit; cleanliness only.)

### F6 — `_find_free_max_ld` audit-prompt size mismatch (info)

The audit prompt described `_find_free_max_ld()` as ~200 LOC and a
candidate for decomposition. The actual function is 22 LOC
(rake_solver.py:850-872) — a simple double-`for` grid search with
vortex-burst skip. No decomposition warranted. No change.

## Cross-slice referrals

- **`solver/objective.py:542 _compute_vortex_threshold_mm`** — listed
  in this slice's audit focus but lives in `objective.py`. Caching
  logic + 4-path fallback should be audited in the slice that owns
  `objective.py` (not this one). The vortex-burst constant in
  `rake_solver.py` reads `self.car.vortex_burst_threshold_mm`
  directly (CarModel field), not the wing-aware `objective.py`
  function — different semantic.

- **`car_model/cars.py:2734` Porsche `vortex_burst_threshold_mm=8.0`
  with comment "CORRECTED: 2mm never bound"** — physics rationale is
  encoded in a code comment rather than a calibration record.
  Worth surfacing in the car-model audit slice (the comment notes
  this matches BMW; verify the reasoning is documented somewhere
  durable).

- **`solver/solve_chain.py` and `pipeline/reason.py`** — call
  `reconcile_ride_heights(...)` repeatedly with `surface=` and
  `track=` sometimes omitted. When omitted, the rear-rebalance code
  path (lines 920-929) is skipped silently. This is by design but
  the call sites should be audited to ensure surface/track are
  always passed in the candidate-search path where stale dynamic
  rear is a real risk. Outside this slice's owned files.

- **`solver/objective.py:273` references "see
  `_compute_vortex_threshold_mm`"** — implies a per-wing dynamic
  threshold exists. `rake_solver.py` uses only the static
  `car.vortex_burst_threshold_mm`. If the per-wing version is the
  correct authority, `rake_solver` is using a coarser constraint —
  flag for cross-slice physics review.

## Verification

Before fixes:

```
346 passed, 3 failed, 17 skipped
PORSCHE OK (Algarve, wing 12, single_lap_safe)
FERRARI OK (Hockenheim, wing 14, single_lap_safe)
```

The 3 pre-existing failures are in
`tests/test_calibration_semantics.py` (Acura step expectations) and
`tests/test_run_trace.py` (support-tier text mismatch). Neither
touches `rake_solver.py`.

After fixes (re-verified): same counts; both E2E pipelines still
emit valid .sto files. The Porsche `solution_from_explicit_offsets`
caller-static path is exercised by the candidate generator in
`solve_chain.py` and the .sto round-trip is unchanged.
