# Quick Fix: Per-Speed-Band LLTD Targets in ARB Solver
Generated: 2026-03-15

## Change Made
- File: `solver/arb_solver.py`
- Lines modified: multiple sections
- Change: Added per-speed-band LLTD targets (low/mid/high speed) to ARBSolution dataclass, ARBSolver, and summary output

## Summary of Changes

1. **ARBSolution dataclass** (after `car_specific_notes`): Added three new float fields with default 0.0:
   - `lltd_target_low_speed` — < 120 kph (mechanical domain)
   - `lltd_target_mid_speed` — 120-200 kph (transition)
   - `lltd_target_high_speed` — > 200 kph (aero domain)

2. **ARBSolver._speed_band_lltd_targets()** (new method before `solve`): Computes +/-0.02 offsets from base target for low/high speed bands. Low speed gets -0.02 for rear compliance without aero; high speed gets +0.02 for front load transfer with aero.

3. **ARBSolver.solve()**: Calls `_speed_band_lltd_targets(target_lltd)` immediately after computing `target_lltd`. Passes results into `ARBSolution` constructor and into car-specific notes.

4. **ARBSolution.summary()**: Added "PER-SPEED LLTD TARGETS" section between LLTD ANALYSIS and ROLL STIFFNESS BREAKDOWN.

5. **Car-specific notes**: Added a note showing the three speed-band targets and noting RARB blade maps them across corner speeds.

## Verification
- Syntax check: PASS (all f-strings and dict lookups consistent)
- Pattern followed: Matches existing dataclass field style, method style, and summary formatting

## Files Modified
1. `C:\Users\tfunk\IOptimal\solver\arb_solver.py` — Added per-speed-band LLTD target fields, method, solve call, summary section, and car note
