# Audit: Objective + Sensitivity + Modifiers (Slice #7)

Date: 2026-04-26
Files in scope:
- `solver/objective.py` (~2132 LOC)
- `solver/sensitivity.py` (~582 LOC)
- `solver/modifiers.py` (~387 LOC)

## Summary

The three modules are physics-clean overall: every term in the score formula
is documented with provenance, weights are tunable per scenario profile, and
the vortex threshold is properly cached and falls back to a single class
constant. Forward physics in `evaluate_physics()` (~300 LOC) handles tyre
compliance, dynamic ride heights, LLTD computation, damping ratios, and
aero-map balance correctly.

Eight findings are addressed in-place in this PR (5 bug-class, 3 quality):

1. **Real bug — driver mismatch dead code.** `_compute_driver_mismatch`
   reads `trail_brake_depth` and `smoothness` via `getattr` with float
   defaults (0.5), but `DriverProfile` exposes neither attribute (it has
   `trail_brake_depth_p95`/`trail_brake_depth_mean` and a STRING
   `steering_smoothness`). The conditions `> 0.7` / `< 0.3` and `< 0.3`
   could therefore never fire for any real driver profile — the trail-brake
   and smoothness mismatch terms were silently zero for every candidate.
2. **Silent BMW fallback.** `getattr(self.car, "default_diff_preload_nm",
   30.0)` (line 1481) violates Key Principle 8. Every car defines this
   attribute (BMW 12.0, Porsche 85.0, etc.); direct access exposes
   misconfigurations instead of papering them over.
3. **Duplicate `_rbd_penalty`.** Defined verbatim in both
   `_estimate_lap_gain` and `_compute_lap_gain_breakdown`. Consolidated
   into a private static method.
4. **Dead variables.** `f_arb_size_idx`, `r_arb_size_idx`, `f_arb_blade`,
   `r_arb_blade`, `max_blade` computed in two places and never used (the
   `arb_extreme_ms` term is intentionally zeroed out per the 2026-03-28
   calibration).
5. **Per-evaluation logging spam.** Tyre vertical rate warnings re-import
   `logging` and call `getLogger(__name__).warning(...)` on every
   `evaluate_physics()` call (potentially millions of times). Use the
   module-level `logger` and gate to once per car instance.
6. **Per-call imports.** `import pathlib, json` inside
   `_compute_vortex_threshold_mm`. Moved to module-level.
7. **Per-evaluation magic constant `30.0`.** `diff_target = 30.0` in
   `_compute_lap_gain_breakdown` while `_estimate_lap_gain` uses the
   per-car `default_diff_preload_nm`. The breakdown should mirror the
   raw-score path (otherwise the breakdown disagrees with the score).
8. **Unused dict-pop fallbacks.** `params.get("front_ls_comp", f_ls_comp)`
   pattern in `evaluate_physics` line 1095+ is redundant: `f_ls_comp` was
   already pulled from the same dict 80 lines earlier. Refactored to use
   the local cache.

## Findings (severity / file:line / behavior / fix)

### F1 (HIGH): driver_mismatch reads non-existent attributes
- `solver/objective.py:1909` — `getattr(driver_profile, "trail_brake_depth", 0.5)` always returns 0.5 (`DriverProfile` defines `trail_brake_depth_mean`/`trail_brake_depth_p95`).
- `solver/objective.py:1924` — `getattr(driver_profile, "smoothness", 0.5)` always returns 0.5; `DriverProfile.steering_smoothness` is a STRING category, not a float.
- Behaviour: `mismatch.trail_brake_ms` and `mismatch.smoothness_ms` were silently zero for every candidate of every car.
- Fix: read `trail_brake_depth_p95` and convert `steering_smoothness` ∈ {smooth, moderate, aggressive} to a numeric proxy (smooth→0.8, moderate→0.5, aggressive→0.2) so the `< 0.3` guard maps to `aggressive`.

### F2 (MEDIUM): silent BMW default for diff preload target
- `solver/objective.py:1481` — `getattr(self.car, "default_diff_preload_nm", 30.0)`.
- All cars define this on the dataclass (12.0 BMW, 85.0 Porsche). Fallback is dead but masks misconfiguration.
- Fix: direct attribute access `self.car.default_diff_preload_nm` (Key Principle 8).
- Same constant `30.0` is hardcoded in `_compute_lap_gain_breakdown` line 1648 — replaced with the per-car value to keep raw-score and breakdown in sync.

### F3 (LOW): duplicate `_rbd_penalty` nested function
- `solver/objective.py:1441` and `solver/objective.py:1629` — verbatim duplication.
- Fix: lift to a `@staticmethod` `_rbd_penalty()` on `ObjectiveFunction`.

### F4 (LOW): dead variables left from arb_extreme_ms removal
- `solver/objective.py:1491-1504` and `solver/objective.py:1651-1665`.
- `f_arb_size_idx`, `r_arb_size_idx`, `f_arb_blade`, `r_arb_blade`, `max_blade` computed but never read.
- Fix: removed.

### F5 (LOW): per-call `import logging` and per-call warnings
- `solver/objective.py:867,874` — `import logging` inside `evaluate_physics`.
- Fix: use module-level `logger`; gate the warning to once per car instance via `_tyre_vr_warned: set[str]`.

### F6 (LOW): per-call `import pathlib, json`
- `solver/objective.py:576-577`.
- Fix: hoisted to module-level.

### F7 (CONFIRMED, NO FIX): vortex threshold has 4 fallback paths
- `_compute_vortex_threshold_mm` returns `VORTEX_BURST_THRESHOLD_MM` (8.0) on (a) no aero file, (b) empty axes/table, (c) <2 b_vals, (d) any exception.
- All paths converge on the single class constant; this is a single source of truth and is correct.

### F8 (CONFIRMED, NO FIX): vortex fallback constant
- Audit description called out `13.5mm` at line 600 — actual constant is `VORTEX_BURST_THRESHOLD_MM = 8.0` (line 277). The audit description appears outdated; the code is fine.

### F9 (CONFIRMED, NO FIX): per-axle tyre compliance
- Per CLAUDE.md 2026-04-09 fix, `tyre_vertical_rate_front_nmm` / `tyre_vertical_rate_rear_nmm` are correctly used in `damped_excursion_mm`. Verified at lines 864-865 and 891-892, 902-903, 910-911.

### F10 (CONFIRMED, NO FIX): `parallel_wheel_rate * 0.5`
- Per CLAUDE.md round-2 fix #20, the `× 0.5` is documented at lines 885-888 (per-corner = axle/2). Confirmed.

### F11 (CONFIRMED, NO FIX): LLTD k_total fallback to 0.5
- `solver/objective.py:750` (and 1040) — `if (k_front_total + k_rear_total) > 0 else 0.5`.
- Fires only when both axle roll stiffnesses are zero (degenerate setup with k_torsion=0 and k_arb=0). The 0.5 fallback is mathematically the only honest answer (50/50 unknowable). No fix needed.

### F12 (NOTED): TC target attributes never set
- `getattr(self._measured, "_tc_gain_recommendation", None)` — these underscore-prefixed dynamic attributes are NEVER set anywhere in the codebase (verified via repo grep). The TC mismatch penalty for gain/slip never fires from the recommendation channel; only the direct `rear_slip_p95 > 0.10` path is active.
- Not fixed in this PR (cross-slice — the recommendation should be set by `solver/supporting_solver.py`).

### F13 (NOTED): R²_THRESHOLD claim in audit task
- Audit instructions said `R²_THRESHOLD_BLOCK=0.85, R²_THRESHOLD_WARN=0.95 in sensitivity.py:66-67`. They are not in `sensitivity.py`; they live in `car_model/calibration_gate.py:66-67` (out of slice). Sensitivity.py:66-67 is a `ConfidenceBand` dataclass field. No-op.

## Cross-slice referrals

- **`analyzer/driver_style.py`** — consider exposing a `steering_smoothness_score: float` (0-1) alongside the categorical `steering_smoothness`, so consumers like `_compute_driver_mismatch` don't have to map strings to numbers.
- **`solver/supporting_solver.py`** — `_tc_gain_recommendation` / `_tc_slip_recommendation` attributes referenced by `objective.py:1582-1583` are never set. Either supporting_solver should attach them to `MeasuredState` (or a dedicated context dict) or the objective dead branch should be removed.
- **`car_model/calibration_gate.py`** — owns the `R2_THRESHOLD_BLOCK/WARN` constants the audit instructions misattributed to sensitivity.py. No change needed.

## Verification

- `python -m pytest tests/ -q --tb=short --ignore=tests/test_webapp_routes.py` — same 3 pre-existing failures before and after the changes (`test_acura_steps_1_3_runnable_4_6_blocked`, `test_full_report_blocked_steps_includes_cascaded`, `test_support_tier_mapping`) — none in our slice.
- `python -m pipeline.produce --car porsche ... --scenario-profile single_lap_safe` → PORSCHE OK.
- `python -m pipeline.produce --car ferrari ... --scenario-profile single_lap_safe` → FERRARI OK.
