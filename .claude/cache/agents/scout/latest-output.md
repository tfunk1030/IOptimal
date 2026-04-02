# Codebase Audit: analyzer/ and car_model/ — Dead Code, Mismatches, Naming
Generated: 2026-04-02

## Summary

11 confirmed issues found. The most critical is a hard bug in solver/objective.py that silently deactivates fuel-CG logic (attribute name typo). The validator/ package is entirely orphaned. Two public functions in car_model/ are never called externally. Several analyzer/stint_analysis.py entry points are unreachable from outside the module.

---

## Issues Found

### CRITICAL — Bug (silent dead code path)

**1. solver/objective.py lines 664-672 — fuel_cg_fraction_front typo kills fuel-CG logic**

- Wrong: `hasattr(car, 'fuel_cg_fraction_front')` — this attribute does NOT exist on CarModel.
- Defined name: `car_model/cars.py` line 1235: `fuel_cg_frac` (CG position fraction from front axle, 0=front, 1=rear).
- Semantics differ too: `fuel_cg_frac=0.50` means CG halfway; `fuel_cg_fraction_front` was intended as the front-axle load fraction = `1.0 - fuel_cg_frac`.
- Effect: The if-branch NEVER executes. All fuel-weight-bias calculations silently fall through. Stint fuel modelling is a no-op.
- Fix: Replace `car.fuel_cg_fraction_front` with `1.0 - car.fuel_cg_frac` and remove the hasattr guard (field always exists on CarModel).

---

### HIGH — Orphaned Package

**2. validator/ package — entirely unreachable from rest of codebase**

- Files: validator/__init__.py, __main__.py, extract.py, recommend.py, compare.py, classify.py, report.py
- `grep -rn "from validator."` returns zero results outside the package itself.
- validator/extract.py defines its own MeasuredState (~30 fields) and extract_measurements() that duplicate analyzer/extract.py (60+ fields).
- Fix: Either register python -m validator as a CLI in __main__.py if still useful, or archive/delete to eliminate the duplicate MeasuredState confusion.

---

### HIGH — Missing Export (NameError at runtime)

**3. __main__.py line 702 — imports print_calibration_status which does not exist**

- Wrong: `from car_model.auto_calibrate import print_calibration_status`
- Defined name: `car_model/auto_calibrate.py` line 1584: `def print_status(car: str) -> None:`
- Effect: ImportError at runtime whenever the calibration-status CLI path is invoked.
- Fix: Change import to `from car_model.auto_calibrate import print_status` and update the call on line 706.

---

### MEDIUM — Dead Code (public functions never called externally)

**4. car_model/cars.py line 1429 — CarModel.front_weight_at_fuel() is never called externally**

- No external caller found anywhere (`grep -rn "front_weight_at_fuel"` returns only cars.py).
- Exists to support per-lap fuel-mass reasoning, but the objective function uses its own inline logic (which is broken per issue 1). Two divergent implementations.
- Fix: Wire front_weight_at_fuel() into solver/objective.py to replace the broken inline code, or delete it.

**5. car_model/legality.py — entire module is never imported**

- Module docstring claims: "Single source of truth for ALL legal constraints. Every solver imports from here."
- Reality: `grep -rn "from car_model.legality"` returns zero results. No file imports get_legality, check_setup_legality, or any name from this module.
- Legality checks use car_model/garage.py (GarageOutputModel.validate()) and solver/legality_engine.py instead.
- Fix: Either delete it (stale documentation), or wire get_legality() into solvers as the docstring claims.

**6. car_model/garage_params.py — get_param_schema() and CarParamSchema are never imported externally**

- `grep -rn "garage_params"` shows usage only in the file's own docstring example. No external caller.
- Defines complete per-car schemas for 5 cars and format_setup_card(), all unused.
- Fix: Document as future infrastructure or remove if it duplicates setup_registry.py.

---

### MEDIUM — Dead Code (functions never called externally)

**7. analyzer/stint_analysis.py — four public functions have no external callers**

- extract_stint_snapshots (line 275), compute_degradation_rates (line 302), filter_qualifying_laps (line 205), analyze_stint_evolution (line 772)
- External callers only use: build_stint_dataset, dataset_to_evolution, merge_stint_datasets, StintDataset, StintLapState, StintEvolution.
- Fix: Mark with leading underscore or add pipeline entry points.

**8. analyzer/sto_adapters.py line 453 — adapt_sto() is never called externally**

- build_current_setup_fields() (line 489) and build_diff_rows() (line 493) are used. adapt_sto() is not referenced anywhere outside the module.
- Fix: Delete adapt_sto() or add it to the public interface with a test.

---

### LOW — Naming Inconsistencies

**9. car_model/auto_calibrate.py line 103 — _setup_fingerprint alias for _setup_key with divergent use**

- _setup_key defined at line 65; `_setup_fingerprint = _setup_key` at line 103.
- Line 793 uses _setup_fingerprint; lines 1396, 1517, 1708, 1726, 1787 use _setup_key. Two names for same function, no documented reason.
- Fix: Pick one name (_setup_key is the original), replace all _setup_fingerprint references, delete the alias.

**10. analyzer/extract.py lines 96-97 — lltd_measured / roll_distribution_proxy dual alias adds maintenance burden**

- Both fields set to the same value (line 599). The fallback logic in diagnose.py (lines 100-102 and 689-691) that checks roll_distribution_proxy and falls back to lltd_measured can never be triggered because they are always equal when either is set.
- Fix: Collapse the fallback in diagnose.py to read only roll_distribution_proxy. Keep the alias field for serialization compatibility but document that the fallback branch is unreachable.

**11. analyzer/setup_schema.py lines 194, 209, 233 — three internal helpers have public names**

- ferrari_ldx_oracle(), find_matching_ferrari_ldx(), parse_ldx_setup_entries() have no external callers. All three are only called by _build_ldx_field() inside setup_schema.py.
- Fix: Rename with leading underscore to signal internal visibility.

---

## Architecture Notes

### Duplicate Implementations (analyzer vs validator)

Item | analyzer/ | validator/
MeasuredState | extract.py:36 (60+ fields, active) | extract.py:26 (~30 fields, orphaned)
extract_measurements() | extract.py:304 (active) | extract.py:126 (orphaned)
format_report() | report.py:43 (active) | report.py:34 (orphaned)

### Module Import Frequency (external callers only)

Module | External imports
car_model.cars | 83
analyzer.extract | 25
car_model.setup_registry | 25
analyzer.setup_reader | 24
analyzer.diagnose | 19
analyzer.driver_style | 18
analyzer.telemetry_truth | 16
car_model.garage | 11
analyzer.segment | 10
analyzer.adaptive_thresholds | 7
analyzer.context | 5
analyzer.stint_analysis | 4
analyzer.setup_schema | 4
car_model.auto_calibrate | 6
car_model.garage_model | 1
car_model.garage_params | 0 (external)
car_model.legality | 0
validator.* | 0

### Wiring of newer analyzer sub-modules

All newer modules (causal_graph, conflict_resolver, overhaul, state_inference,
adaptive_thresholds, context, stint_analysis, sto_adapters, sto_binary, setup_schema)
are properly wired via diagnose.py, recommend.py, or pipeline/produce.py / reason.py.
causal_graph and conflict_resolver are inside try/except guards so failures are
non-fatal — intentional but means errors there are silently swallowed.

### GarageConstraintResult and GarageOutputs

Both are returned from GarageOutputModel methods (validate() and predict() respectively)
and are used heavily throughout the codebase via duck typing without explicit imports.
They are NOT dead — just not imported by name. This is fine.
