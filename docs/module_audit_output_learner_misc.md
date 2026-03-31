# Deep Module Audit: output/, learner/, comparison/, validator/, root scripts, webapp/, aero_model/, track_model/

**Date:** 2026-03-31  
**Scope:** 8 directories + root-level scripts  
**Branch:** `cursor/gtp-solver-audit-25bf`

---

## 1. `output/` — Setup File Generation

### 1.1 `setup_writer.py` — .sto File Writer

**Purpose:** Converts solver step outputs (step1–step6) into iRacing-compatible `.sto` XML files.

**Car-specific mappings:** Five separate `_*_PARAM_IDS` dicts:
- `_BMW_PARAM_IDS` — 80+ parameters, fully mapped (lines 65–200)
- `_FERRARI_PARAM_IDS` — 92 parameters, fully mapped including indexed ARBs, rear torsion bars, hybrid config (lines 211–303)
- `_PORSCHE_PARAM_IDS` — Partial (~30 params). Missing: dampers, diff, spring perch, heave perch (lines 313–340)
- `_CADILLAC_PARAM_IDS` — Inherits from BMW with indexed ARB override only (lines 348–353)
- `_ACURA_PARAM_IDS` — Inherits from BMW with extensive overrides for ORECA heave+roll damper layout, rear torsion bars, suppressed per-corner entries (lines 355–425)

**Key function:** `write_sto()` (line 529) — 500+ line function handling:
- Pre-write garage validation via `validate_and_fix_garage_correlation()`
- Value clamping via `_validate_setup_values()`
- Garage output prediction (deflections, corner weights)
- Ferrari-specific `public_output_value()` index translation
- Acura-specific rear torsion bar OD, single rear toe, heave+roll damper suppression
- Computed/display-only fields guarded by `include_computed` flag

**Findings:**
- **Porsche mapping is skeletal.** Missing all damper params, diff, spring perch, heave perch. Attempting to generate a Porsche `.sto` will produce XML comment stubs (`<!-- TODO: porsche {param} not mapped -->`).
- **Cadillac mapping is essentially BMW.** Only ARB blade indexing differs. This is likely correct if Cadillac uses the same chassis XML IDs, but has never been verified against a real Cadillac `.sto`/`.ldx` file.
- **`write_sto()` is 500+ lines.** The BMW/Ferrari/Acura branching makes this function complex but functional. Could benefit from per-car writer subclasses but the current dispatch is adequate.
- **No dead code** in this module.

### 1.2 `garage_validator.py` — Pre-Write Garage Validation

**Purpose:** Ensures the parameter combination written to `.sto` is physically consistent — iRacing's garage would display legal slider positions, ride heights, and deflections.

**Key function:** `validate_and_fix_garage_correlation()` (line 76)

**Correction strategy (3 phases):**
1. **Phase 1:** Range-clamp and quantise individual parameters per step (`_clamp_step1` through `_clamp_step5`)
2. **Phase 2:** Garage-model correlation check (BMW/Sebring only via `car.active_garage_output_model()`)
   - Heave slider fix (`_fix_slider`, line 308)
   - Torsion bar deflection fix (`_fix_torsion_bar_defl`, line 431)
   - BMW soft front bar edge guard (`_fix_bmw_soft_front_bar_edge`, line 494)
   - Front RH floor fix (`_fix_front_rh`, line 361)
3. **Phase 3:** Reconcile step1 ride heights to match garage model predictions

**Findings:**
- **Only BMW/Sebring has a garage model.** All other car/track combinations skip Phase 2 entirely (`garage_model is None` at line 105).
- **Mutates step objects in-place** — documented behavior, but callers must be aware.
- **`_is_bmw_sebring_soft_front_bar_edge()`** (line 53) is a highly specific guard for one car/track/bar combination. This kind of special-casing is brittle but necessary given the non-linear garage behavior.

### 1.3 `run_trace.py` — Decision Trace Recording

**Purpose:** Captures complete data-provenance for one solver run: which signals drove which steps, solver path selection, objective score breakdown, legality validation, and calibration status.

**Key class:** `RunTrace` (line 118)

**Records:**
- Car/track support tier (hardcoded in `_SUPPORT_TIERS`, line 83)
- Signal-to-step mapping (`_SIGNAL_TO_STEPS`, line 34)
- Step key outputs via `_extract_step_key_outputs()` (line 448)
- Objective breakdown with nested platform risk, driver mismatch, uncertainty, envelope penalty
- Legality tier and messages
- Calibration status from `validation/calibration_report.md`

**Findings:**
- **`_SUPPORT_TIERS` is hardcoded** (line 83) and slightly stale — lists Acura as "unsupported — <1 session" but Acura now has 7 observations per CLAUDE.md. Should be dynamically loaded from `validation/objective_validation.json`.
- **`record_calibration()`** (line 219) parses a markdown file with regex. Fragile — any formatting change breaks it.
- **Well-used** — called from `pipeline/produce.py` and `solver/solve.py`.

### 1.4 `search_report.py` — Legal Manifold Search Analysis

**Purpose:** Produces interpretable reports explaining why certain setup regions are fast after legal-manifold search.

**Contains 6 analysis sections:**
1. **Parameter Sensitivity** (`compute_sensitivity()`, line 89) — sweeps each dimension ±3 steps
2. **Pareto Frontier** (`extract_pareto_frontier()`, line 269) — gain vs risk, gain vs robustness
3. **Setup Landscape Clusters** (`cluster_candidates()`, line 416) — K-means clustering with auto-labeling
4. **Diff Reports** (`format_diff_report()`, line 716) — parameter-by-parameter vs baseline
5. **Vetoed Summary** (`format_vetoed_summary()`, line 868)
6. **Full Report Generator** (`generate_search_report()`, line 909) — orchestrates all sections

**Findings:**
- **Custom K-means implementation** (`_kmeans()`, line 582) avoids sklearn dependency. Adequate for the ~200 candidate vectors.
- **`_auto_label_cluster()`** (line 534) provides readable labels like "Soft-Mechanical / Grip-Focused" based on centroid position. Useful for racing engineers.
- **Well-integrated** — called from `solver/legal_search.py` and `pipeline/produce.py`.

### 1.5 `report.py` — Engineering Report

**Purpose:** Full human-readable setup report with parameter sheet, garage card, top actions, stint card, sensitivity, sector compromise, and setup space tables.

**Key function:** `print_full_setup_report()` (line 149) — 700+ lines covering all car variants.

**Also provides:**
- `print_comparison_table()` (line 858) — current vs recommended side-by-side
- `save_json_summary()` (line 917) — JSON export with public output key remapping

**Findings:**
- **Handles Ferrari/Acura/BMW display differences correctly** including indexed values, heave+roll damper layout, rear torsion bars.
- **`_has_rear_torsion` and `_has_roll_dampers` flags** (lines 224–227) use car model introspection.
- **Variables `_is_acura`, `_has_rear_torsion`** used in `print_comparison_table()` (lines 881–883) reference outer scope variables from `print_full_setup_report()` — these are module-level references to `False` since the comparison function doesn't set them. **Bug:** `_has_rear_torsion` and `_is_acura` at line 882 reference the function's outer scope but `print_comparison_table` is a standalone function. These variables will be `NameError` if called outside `print_full_setup_report`. However, in practice this function is always called within the pipeline context where the car-detection variables exist.

---

## 2. `learner/` — Knowledge Accumulation System

### 2.1 `knowledge_store.py` — Persistent JSON Storage

**Purpose:** File-based knowledge store organized as `data/learnings/{observations,deltas,models,insights,calibration_updates}/`.

**Key class:** `KnowledgeStore` (line 36)

**Operations:** CRUD for observations, deltas, empirical models, insights, and calibration history. All JSON files, no locking.

**Findings:**
- **No file locking** — documented limitation. Safe for single-user CLI but not concurrent access.
- **Clean interface** — session ID generation, listing with car/track filtering.

### 2.2 `observation.py` — Structured Session Snapshots

**Purpose:** `build_observation()` extracts a structured snapshot from analyzer outputs (setup, telemetry, diagnosis, driver profile, corner analysis).

### 2.3 `delta_detector.py` — Session-to-Session Changes

**Purpose:** Compares consecutive sessions to find setup→effect causality. Uses `KNOWN_CAUSALITY` (~40 pairs) for hypothesis testing.

### 2.4 `empirical_models.py` — Lightweight Regressions

**Purpose:** Fits linear models from accumulated observations (roll gradient, LLTD, m_eff, aero compression). Exports corrections dict and sensitivity rankings.

### 2.5 `recall.py` — Solver Query Interface

**Purpose:** The solver's window into learned knowledge. Key methods:
- `get_corrections()` (line 49) — all empirical correction factors
- `get_prediction_corrections()` (line 61) — prediction-vs-measurement errors
- `predict()` (line 95) — use fitted relationship to predict values
- `what_happened_when()` (line 137) — causal history queries
- `most_impactful_parameters()` (line 186) — lap time sensitivity

### 2.6 `ingest.py` — Session Ingestion Pipeline

**Purpose:** Full ingest cycle: analyze → observe → delta → models → insights.

**Key functions:**
- `ingest_ibt()` (line 85) — single-lap ingest
- `ingest_all_laps()` (line 264) — multi-lap ingest (each lap as separate observation)
- `rebuild_track_learnings()` (line 419) — rebuild from stored observations

**Findings:**
- **5-phase pipeline** (analyze, compare, models, insights, global model)
- **Robust error handling** — skips invalid laps, rebuilds deltas on error
- **Cross-track global model** (`learner/cross_track.py`) built in Phase 5

### 2.7 `sanity.py` — Lap Time Validation

**Purpose:** Track-specific plausible lap-time bounds. Currently defines:
- BMW/Sebring: 105–130s
- Acura/Hockenheim: 82–105s
- Default: 60–600s

**Findings:**
- **Tight bounds** — useful for filtering out pit laps and warm-up laps.
- **Only 2 specific bounds defined.** Other car/track combos fall through to 60–600s default.

### 2.8 `envelope.py` — Telemetry Envelope

**Purpose:** Builds a statistical envelope from multiple sessions' telemetry (median, MAD, percentiles). Computes z-score distance for outlier detection.

**Key functions:**
- `build_telemetry_envelope()` (line 47) — 7 default metrics
- `compute_envelope_distance()` (line 79) — flags metrics >2.5 sigma from envelope

**Used by:** `comparison/score.py::_score_context_health()` and `pipeline/reason.py`

### 2.9 `setup_clusters.py` — Setup Clustering

**Purpose:** Builds mean/spread clusters from setup parameter vectors. Computes z-score distance for outlier detection.

**Key functions:**
- `build_setup_cluster()` (line 55) — 14 default setup parameters
- `compute_setup_distance()` (line 80) — per-parameter z-scores

**Used by:** `comparison/score.py` and `pipeline/reason.py`

### 2.10 `cross_track.py` — Cross-Track Global Models

**Purpose:** Pools observations across all tracks for car-intrinsic properties (aero compression, roll gradient, m_eff). Detects track-specific anomalies (>1.5σ).

**Key function:** `build_global_model()` (line 89) — produces `GlobalCarModel` with confidence tiers (no_data/low/medium/high).

### 2.11 `report_section.py` — Report Section Generator

**Purpose:** Generates "ACCUMULATED KNOWLEDGE" section for engineering reports. Includes key insights, setup trends, recurring issues, empirical calibrations, sensitivity rankings, high-confidence findings, and suggested experiments.

### Are Learned Corrections Actually Used by the Solver?

**Yes.** The path is:

1. **`solver/learned_corrections.py::apply_learned_corrections()`** — loads `KnowledgeStore` → `KnowledgeRecall` → `get_corrections()` → returns `LearnedCorrections` dataclass
2. **`solver/solve.py`** (line 263) — calls `apply_learned_corrections()` when `--learn` flag is set
3. **`pipeline/reason.py::_integrate_historical()`** (line 1672) — calls `recall.get_corrections()` and `recall.get_prediction_corrections()` for multi-IBT reasoning

**What corrections are applied:**
- `heave_m_eff_front_kg` — overrides car model's m_eff for heave sizing
- `roll_gradient_deg_per_g` — informs body roll predictions
- `lltd_measured_baseline` — informs ARB solver target
- `aero_compression_front/rear_mm` — calibrates ride height models
- `calibrated_front/rear_roll_gain` — from tyre thermal analysis
- `damping_ratio_scale` — from driver history

**Gate:** Corrections require `min_sessions >= 3` (configurable). Below this, physics defaults are used.

---

## 3. `comparison/` — Multi-Session Comparison & Synthesis

### Purpose

Standalone module for comparing 2+ IBT sessions side-by-side, scoring them across performance categories, and synthesizing an optimal setup from the combined analysis.

**Is it used?** Yes — invoked via:
- `python -m comparison --car bmw --ibt s1.ibt s2.ibt --wing 17`
- `webapp/services.py` imports `analyze_session`, `compare_sessions`, `score_sessions`, `synthesize_setup`
- Tests: `test_comparison_scoring.py`, `test_comparison_report.py`

### 3.1 `compare.py` — Per-Session Analysis & Comparison

**Key classes:**
- `SessionAnalysis` — complete results for one IBT (setup, measured, corners, driver, diagnosis)
- `ComparisonResult` — N-session comparison (setup deltas, telemetry deltas, corner-by-corner, problem matrix)

**`analyze_session()`** (line 75) — runs full analyzer pipeline on one IBT.  
**`compare_sessions()`** (line 334) — builds delta tables and matches corners across sessions by lap distance (50m tolerance).

**Defines 34 setup params and 32 telemetry metrics for comparison.**

### 3.2 `score.py` — Multi-Dimensional Scoring

**10 scoring categories with weights:**
- lap_time (12%), grip (14%), balance (14%), aero_efficiency (10%), high_speed_corners (10%), low_speed_corners (10%), corner_performance (10%), damper_platform (5%), thermal (5%), context_health (10%)

**`_score_context_health()`** uses `learner/envelope.py` and `learner/setup_clusters.py` for family-fit scoring.

### 3.3 `synthesize.py` — Setup Synthesis

**`synthesize_setup()`** (line 48) — delegates to `pipeline/reason.py::reason_and_solve()` with all IBT paths, then wraps result in `SynthesisResult` with explanations, source sessions, and confidence assessment.

### 3.4 `report.py` — ASCII Report & JSON Export

**8-section comparison report:** session overview, setup table, telemetry table, corner-by-corner, rankings, causal analysis, best setup, synthesized setup.

**Findings:**
- **Not dead code.** Actively used by webapp and CLI.
- **Some overlap with `pipeline/report.py`** — both generate engineering reports but for different use cases (single-session vs multi-session).
- **`_format_synthesis()` references `step4.farb_blade_locked`** (line 582) which may not exist on all `ARBSolution` objects — could cause `AttributeError` if field name changed.

---

## 4. `validator/` — Solver Prediction Feedback Loop

### How Does This Differ from `validation/`?

| Aspect | `validator/` | `validation/` |
|--------|-------------|--------------|
| **Purpose** | Compare solver predictions vs IBT telemetry for one session | Evaluate objective function correlation across all observations |
| **Scope** | Per-run feedback loop | Aggregate statistical validation |
| **Input** | Solver JSON + IBT | All stored observations + setup registry |
| **Output** | "good_setup" / "needs_tweaking" / "rethink" + parameter adjustments | Spearman correlation, holdout stability, calibration weights |
| **Usage** | `python -m validator --car bmw --ibt ... --setup solver_output.json` | `python -m validation.run_validation` |

### 4.1 `extract.py` — Telemetry Extraction

**Has its own `MeasuredState`** dataclass (line 26) — separate from `analyzer/extract.py::MeasuredState`. This is the **validator-specific** measured state focused on prediction comparison (ride height excursions, natural frequency via FFT, settle time after bumps).

**Finding:** **Overlapping `MeasuredState` class** with `analyzer/extract.py`. Different fields and purpose, but the name collision is confusing.

### 4.2 `compare.py` — Prediction vs Measurement

Builds comparison matrix mapping solver JSON fields to measured quantities. Each comparison has tolerance thresholds for classification.

### 4.3 `classify.py` — Discrepancy Classification

Classifies as confirmed/tweak/rethink. Detects cascade errors (Step 1 ride height errors propagate downstream).

### 4.4 `recommend.py` — Recommendation Engine

Generates parameter adjustments (tweaks within framework) and model corrections (rethinks requiring calibration updates).

### 4.5 `report.py` — Validation Report

ASCII report following same 63-char width convention.

**Findings:**
- **Self-contained module** — only imports from itself, `track_model`, and `car_model`.
- **Not imported by any other production code** outside its own package and tests.
- **Useful but potentially underused** — the feedback loop it implements (solver → IBT → compare → adjust → re-solve) is manual. No automated integration with the solver pipeline.
- **Potential overlap with `learner/delta_detector.py`** which also compares predictions vs measurements.

---

## 5. Root-Level Scripts — Classification

### Production Scripts: NONE

All root-level `run_*.py` scripts are **developer convenience wrappers** with hardcoded Windows paths.

### Experimental / Developer Convenience:

| Script | Status | Evidence |
|--------|--------|----------|
| `run_now.py` | **Dead** | Hardcoded Windows path `C:\Users\VYRAL\IOptimal\`, calls `__main__.py` via subprocess |
| `run_full_v2.py` | **Dead** | Hardcoded Windows path, specific IBT file, Tee class for dual output |
| `run_full_pipeline.py` | **Dead** | Hardcoded Windows path, subprocess call to `pipeline.produce` |
| `run_full_justified.py` | **Dead** | Hardcoded Windows path, specific IBT file |
| `run_exhaustive.py` | **Dead** | Hardcoded Windows path, exhaustive legal-space search wrapper |
| `run_tuned_search.py` | **Dead** | Hardcoded Windows path, tuned objective weights |
| `run_tests.py` | **Dead** | Hardcoded Windows path, runs specific subset of pytest tests |
| `vertical_dynamics.py` | **Production utility** | Shared vertical-dynamics helpers. Imported by `car_model/cars.py`, `solver/heave_solver.py`, `solver/damper_solver.py`, `solver/objective.py`, `tests/test_physics_corrections.py` |
| `test_camber.py` | **Dead / one-off test** | Standalone camber solver test using `BMW_M_HYBRID_V8` constant. Not in `tests/` dir, not run by pytest |

### Recommendation:
- **Delete:** `run_now.py`, `run_full_v2.py`, `run_full_pipeline.py`, `run_full_justified.py`, `run_exhaustive.py`, `run_tuned_search.py`, `run_tests.py`, `test_camber.py`
- **Keep:** `vertical_dynamics.py` (actively imported by 5 modules)

---

## 6. `webapp/` — Web Application

### 6.1 Architecture

- **`app.py`** — FastAPI application with Jinja2 templates, 3 run modes (single_session, comparison, track_solve)
- **`services.py`** — Domain logic: calls solver, analyzer, comparison pipeline, generates artifacts (.sto, .json, .txt)
- **`jobs.py`** — Single-worker thread pool for background run execution
- **`storage.py`** — SQLite-backed run repository (runs, summaries, artifacts)
- **`settings.py`** — Environment-based configuration
- **`types.py`** — View model dataclasses for template rendering

### Is the Webapp Connected to the Solver?

**Yes, deeply.**

`services.py` imports and calls:
- `solver/solve.py::run_solver()` — for track_solve mode
- `solver/predictor.py::predict_candidate_telemetry()` — for telemetry predictions
- `comparison/compare.py::analyze_session()`, `compare_sessions()` — for comparison mode
- `comparison/synthesize.py::synthesize_setup()` — for synthesis
- `output/setup_writer.py::write_sto()` — for .sto generation
- `learner/knowledge_store.py::KnowledgeStore` — for knowledge display

**Findings:**
- **Fully functional local web app.** Three modes cover single-session pipeline, multi-session comparison, and standalone solver.
- **Background job execution** — runs don't block the web server.
- **Auto-ingest** — monitors telemetry directory every 30s (when configured via `desktop/config.py`).
- **No dead code** — all routes and services are used.

---

## 7. `aero_model/` — Aerodynamic Response Surfaces

### 7.1 `parse_xlsx.py` — Excel Parser

Parses raw `.xlsx` aero map files. Maps directory names to canonical car names.

### 7.2 `parse_all.py` — Batch Parser

`parse_all()` finds all xlsx files across all car directories and saves to `data/aeromaps_parsed/`.

### 7.3 `interpolator.py` — Aero Surface

**`AeroSurface`** — wraps `scipy.RegularGridInterpolator` for querying DF balance and L/D at any (front_RH, rear_RH).

**Key methods:**
- `query(front_rh, rear_rh)` → (balance%, L/D)
- `rear_rh_for_balance(target_balance, front_rh)` — bisection solver
- `load(car, wing_angle)` — factory from parsed JSON

### 7.4 `gradient.py` — Aero Gradients

Central-difference ∂(DF balance)/∂(RH) and ∂(L/D)/∂(RH) at operating point. Computes aero window (mm before 0.5% balance shift) and L/D cost of RH variance.

### Which Cars Have Aero Maps?

| Car | Raw xlsx files | Parsed JSON | Wing angles | Status |
|-----|---------------|-------------|-------------|--------|
| BMW | 6 files (12–17°) | `bmw_aero.json` | 12, 13, 14, 15, 16, 17 | **Full** |
| Ferrari | 6 files (12–17°) | `ferrari_aero.json` | 12, 13, 14, 15, 16, 17 | **Full** |
| Porsche | 6 files (12–17°) | `porsche_aero.json` | 12, 13, 14, 15, 16, 17 | **Full** |
| Cadillac | 6 files (12–17°) | `cadillac_aero.json` | 12, 13, 14, 15, 16, 17 | **Full** |
| Acura | 9 files (6–10°) | `acura_aero.json` | 6, 6.5, 7, 7.5, 8, 8.5, 9, 9.5, 10 | **Full** (different range) |

**Are they equally calibrated?**

No. Per CLAUDE.md:
- BMW/Sebring aero maps are calibrated against IBT telemetry (73 observations)
- Acura aero maps are uncalibrated — "RH targets unreliable (aero maps not calibrated for Acura)"
- Ferrari, Cadillac, Porsche aero maps have raw data but limited/no IBT validation

All 5 cars have complete aero map coverage, but only BMW has validated the maps against telemetry.

---

## 8. `track_model/` — Track Profiles

### 8.1 `profile.py` — TrackProfile Dataclass

Core dataclass holding: braking zones, corners, speed bands, shock velocity spectra, surface data, median speeds.

### 8.2 `build_profile.py` — IBT-Based Profile Builder

`build_profile(ibt_path)` — parses IBT file and builds full TrackProfile from telemetry data.

### 8.3 `ibt_parser.py` — IBT Binary File Parser

Low-level binary parser for iRacing .ibt telemetry files.

### 8.4 `generic_profiles.py` — Generic Fallbacks

**`generate_generic_profile()`** — creates approximate TrackProfile for tracks without IBT data.

Parameters: name, config, length_km, n_corners, roughness (smooth/medium/rough), avg_speed_kph.

Three roughness templates calibrated from Sebring (rough) and scaled.

### What Tracks Are Supported?

| Track | Profile file | Source |
|-------|-------------|--------|
| Sebring International | `sebring_international_raceway_international.json` | IBT-derived |
| Sebring Latest | `sebring_latest.json` | IBT-derived (newer) |
| Hockenheim Grand Prix | `hockenheim_grand_prix.json` | IBT-derived |

**3 tracks with IBT-derived profiles.** All others must use `build_profile()` from a fresh IBT or `generate_generic_profile()` as a fallback.

**Findings:**
- **Generic profiles are functional** but lower accuracy (estimated shock velocities, speed bands).
- **No auto-detection** — the caller must specify roughness category.
- **Well-documented** limitations in the docstring.

---

## Summary: Dead Code, Overlap, and Experimental Modules

### Dead Code (Safe to Delete)

| File | Reason |
|------|--------|
| `run_now.py` | Hardcoded Windows paths, developer convenience wrapper |
| `run_full_v2.py` | Hardcoded Windows paths, developer convenience wrapper |
| `run_full_pipeline.py` | Hardcoded Windows paths, developer convenience wrapper |
| `run_full_justified.py` | Hardcoded Windows paths, developer convenience wrapper |
| `run_exhaustive.py` | Hardcoded Windows paths, developer convenience wrapper |
| `run_tuned_search.py` | Hardcoded Windows paths, developer convenience wrapper |
| `run_tests.py` | Hardcoded Windows paths, subset test runner |
| `test_camber.py` | One-off test script, not in tests/ dir |

### Overlapping Code

| Modules | Nature of Overlap | Assessment |
|---------|-------------------|------------|
| `validator/extract.py::MeasuredState` vs `analyzer/extract.py::MeasuredState` | Same class name, different fields | **Name collision.** Validator's is prediction-focused; analyzer's is diagnosis-focused. Should rename validator's to `ValidationMeasuredState`. |
| `validator/` vs `learner/delta_detector.py` | Both compare predicted vs measured | **Partial overlap.** Validator is per-run feedback loop; learner is cumulative. Different lifecycles. |
| `comparison/report.py` vs `output/report.py` | Both generate engineering reports | **Different scope.** Comparison is N-session; output is single-session. No duplication of logic. |
| `output/search_report.py::SetupCluster` vs `learner/setup_clusters.py::SetupCluster` | Same class name, different purpose | **Name collision.** Search report's is for candidate clustering; learner's is for session clustering. |

### Experimental / Partially Integrated

| Module | Status | Evidence |
|--------|--------|---------|
| `validator/` | **Functional but underused** | Self-contained CLI tool. Not automatically integrated into solver pipeline. Manual feedback loop. |
| `learner/cross_track.py` | **Functional** | Called during ingest Phase 5. Global model saved but not yet consumed by solver (corrections come from per-track models). |
| `track_model/generic_profiles.py` | **Functional but rarely used** | Provides fallback when no IBT available. Most users have IBT data. |

### Key Architecture Observations

1. **Learned corrections have a clear path into the solver** but are gated by session count and the `--learn` flag. Without the flag, the solver runs pure physics.

2. **The `comparison/` module is well-integrated** with both the webapp and CLI. It bridges analyzer, solver, and learner systems effectively.

3. **`vertical_dynamics.py` belongs in a package** — it's a utility module at the repo root that's imported by 5 other modules. Should move to `solver/vertical_dynamics.py` or `car_model/vertical_dynamics.py`.

4. **Porsche .sto writer is incomplete** — will produce commented-out stubs for most parameters. This matches the "unsupported" tier for Porsche.

5. **`run_trace.py::_SUPPORT_TIERS`** is hardcoded and drifting from reality. Should read from `validation/objective_validation.json`.
