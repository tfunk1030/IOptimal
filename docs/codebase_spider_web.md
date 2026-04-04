# iOptimal Codebase Spider Web Analysis
*Generated: 2026-04-01 18:00 UTC*  
*Updated: 2026-04-04 — Calibration gate, objective fixes*

---

## Architecture Overview

**159 Python files** across **17 packages**, totaling **~3,200 KB** of code.

```
                    ┌──────────────────────┐
                    │   __main__.py (39KB)  │   ← CLI entry point
                    └──────────┬───────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                 ▼
    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
    │   pipeline/  │  │   learner/   │  │   solver/    │
    │ produce 78KB │  │ ingest 31KB  │  │ solve  36KB  │
    │ reason 166KB │  │              │  │ obj.  102KB  │
    └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
           │                 │                  │
    ┌──────┴──────┐   ┌──────┴──────┐   ┌──────┴──────┐
    │  analyzer/  │   │  car_model/ │   │ track_model/│
    │ extract 80K │   │ cars  131KB │   │ profile 10K │
    │ diagnose 55 │   │ auto_cal 81 │   │ ibt_par  8K │
    │ setup_rd 22 │   │ registry 65 │   │ build   28K │
    └──────┬──────┘   │ calib_gate  │   └─────────────┘
           │          └──────┬──────┘
    ┌──────┴──────┐   ┌──────┴──────┐
    │   output/   │   │  aero_model │
    │ writer 55KB │   │ interp  9KB │
    │ report 49KB │   │ gradient 6K │
    └─────────────┘   └─────────────┘
```

---

## Critical Path: IBT File → Setup Recommendation

```
IBT binary ──→ track_model.ibt_parser ──→ session_info (YAML dict)
                                           │
              ┌────────────────────────────┘
              ▼
    analyzer.setup_reader.from_ibt()      ← adapter_name now car-aware
              │
              ▼
    CurrentSetup (dataclass)              ← 51 fields parsed from IBT
              │
    ┌─────────┴───────────┐
    ▼                     ▼
  learner.ingest()    pipeline.produce() / pipeline.reason()
    │                     │
    ▼                     ▼
  Observation JSON    CalibrationGate.check_step()  ← NEW (2026-04-04)
  (data/learnings/       │
   observations/)        ├── BLOCKED → calibration instructions (not setup values)
                         └── RUNNABLE → 6-step solver → ObjectiveFunction.evaluate()
                                                           │
                              ├── _estimate_lap_gain()     → lap_gain_ms (active)
                              ├── damper compression bonus → gated on zeta_is_calibrated
                              ├── platform_risk            → w set by scenario profile
                              ├── driver_mismatch          → w=0 when no driver profile
                              ├── empirical k-NN           → gated on ≥10 sessions
                              ├── envelope_penalty         → w set by scenario profile
                              └── uncertainty              → w set by scenario profile
```

**Key change (2026-04-04):** The calibration gate sits between input loading and the solver. Each solver step is blocked if any required subsystem is uncalibrated. Blocked steps output calibration instructions — never a guess.

---

## Top 25 Most-Imported Modules (Fan-In = Criticality)

| Module | Size | Imported By | Role |
|--------|------|-------------|------|
| **car_model.cars** | 131KB | **46 files** | Car physics models, THE central dependency |
| **track_model.profile** | 10KB | 33 files | Track demand profiles |
| track_model.ibt_parser | 8KB | 19 files | IBT binary reader |
| analyzer.extract | 80KB | 19 files | Telemetry extraction |
| analyzer.setup_reader | 22KB | 16 files | Setup parsing |
| analyzer.diagnose | 55KB | 15 files | Lap diagnosis |
| car_model.setup_registry | 65KB | 15 files | Parameter name mapping |
| analyzer.driver_style | 17KB | 13 files | Driver profiling |
| analyzer.telemetry_truth | 22KB | 13 files | Ground truth extraction |
| solver.rake_solver | 45KB | 12 files | Ride height/rake |
| solver.heave_solver | 51KB | 12 files | Heave spring physics |
| solver.arb_solver | 27KB | 12 files | ARB stiffness |

**Insight:** `car_model.cars` at 131KB imported by 46/158 files = **29% of the entire codebase depends on one file**. Any error in the CarModel propagates everywhere.

---

## Top 15 Highest Fan-Out (Most Coupled)

| Module | Size | Imports | Risk |
|--------|------|---------|------|
| **pipeline.produce** | 78KB | **54 modules** | God module — orchestrates everything |
| **pipeline.reason** | 166KB | **51 modules** | 2nd god module, largest file in codebase |
| solver.solve | 36KB | 31 modules | Full solve orchestration |
| pipeline.report | 31KB | 24 modules | Report generation |
| learner.ingest | 31KB | 19 modules | IBT ingestion pipeline |

**Insight:** `pipeline.reason` at 166KB imports 51 internal modules. A single change in any dependency can break this file. This is the #1 maintenance risk.

---

## 10 Circular Dependencies

| Module A | ⟷ | Module B | Severity |
|----------|---|----------|----------|
| analyzer.extract | ⟷ | analyzer.telemetry_truth | Medium — data extraction loop |
| pipeline.produce | ⟷ | pipeline.reason | **High** — the two god modules depend on each other |
| car_model.auto_calibrate | ⟷ | car_model.cars | **High** — calibration + model circular |
| car_model.cars | ⟷ | car_model.garage | Medium |
| analyzer.causal_graph | ⟷ | analyzer.diagnose | Low |
| analyzer.conflict_resolver | ⟷ | analyzer.recommend | Low |
| analyzer.diagnose | ⟷ | analyzer.state_inference | Low |
| analyzer.telemetry_truth | ⟷ | pipeline.reason | **High** — cross-package circular |
| solver.bmw_rotation_search | ⟷ | solver.solve_chain | Medium |
| desktop.app | ⟷ | desktop.tray | Low |

---

## BMW-Hardcoded Hotspots

**275 ESTIMATE markers** across the codebase.
**39 `if "ferrari"` branches**, **15 `if "bmw"` branches**, **13 `_ferrari_controls` guards**.

### Files with >2 "bmw" string literals:
| File | BMW refs | Risk |
|------|----------|------|
| car_model.setup_registry | 8 | High — default dispatch falls to BMW |
| car_model.cars | 5 | Medium — BMW is first defined car |
| solver.bmw_coverage | 5 | Named for BMW — not multi-car |
| car_model.auto_calibrate | 4 | Falls through to BMW calibration |
| car_model.garage_params | 4 | Schema references |
| validation.run_validation | 4 | Only validates BMW/Sebring |
| validation.objective_calibration | 3 | Only calibrates BMW |

### 34 Files Import car_model.cars But Have NO Per-Car Branching

These use whatever CarModel provides — if non-BMW cars carry BMW-copied constants, these files silently produce wrong results:

- **solver.heave_solver** (51KB, 15 car field reads) — NO car dispatch
- **solver.solve** (36KB, 12 car field reads) — NO car dispatch  
- **solver.damper_solver** (43KB, 9 car field reads) — has some ferrari checks but not comprehensive
- **analyzer.extract** (80KB) — silent BMW path
- **analyzer.diagnose** (55KB) — silent BMW path
- **car_model.auto_calibrate** (81KB) — silent BMW path

---

## Non-BMW Car Status: Calibration Gate Now Enforced (2026-04-04)

### Previous Problem (resolved)

Previously, the solver ran all 6 steps for every car and always produced a full setup, even when models were uncalibrated. BMW coefficients were silently applied to Porsche (producing -55.9mm deflection — impossible). Physics estimates were presented as recommendations. The scoring function had all penalty weights zeroed because they were anti-correlated with BMW data.

### Current State

The **calibration gate** (`car_model/calibration_gate.py`) now blocks solver steps per car:

| Car | Calibrated Steps | Blocked Steps | What User Sees |
|-----|-----------------|---------------|----------------|
| BMW | 1-6 (all) | none | Full setup output |
| Ferrari | 1-3 | 4, 5, 6 | Partial setup + calibration instructions for ARB/geometry/dampers |
| Cadillac | 2-3 | 1, 4, 5, 6 | Heave/spring output + instructions for RH model, ARB, geometry, dampers |
| Porsche | 1-3 | 4, 5, 6 | Partial setup + calibration instructions |
| Acura | — | 1-6 (all) | Calibration instructions only (step 1 blocked cascades) |

### Scoring Improvements (2026-04-04)

- **Damper compression signal added** — front LS comp (r=-0.447, strongest predictor) scored in `_estimate_lap_gain()`, gated on `zeta_is_calibrated`
- **k-NN gated on data quality** — `w_empirical` zeroed when < 10 sessions available (prevents noisy predictions)
- **Driver mismatch weight fix** — `w_driver=0.0` when no driver profile present (prevents wasted weight budget)
- **DeflectionModel gate** — uncalibrated cars skip deflection veto entirely instead of applying BMW coefficients
- **BMW/Sebring correlation improved** — Spearman from -0.06 to -0.30 (5x improvement)

### What Non-BMW Cars Need to Unblock Steps 4-6

Each car needs the following calibration data to unlock blocked steps:

1. **ARB stiffness** — 3+ IBT sessions with different ARB sizes (keep springs constant) → `python -m car_model.auto_calibrate --car <car> --ibt-dir <telemetry_dir>`
2. **LLTD target** — 10+ IBT sessions with varied settings → `python -m validation.calibrate_lltd --car <car> --track <track>`
3. **Roll gains** — 3+ IBT sessions with lateral-g data → `python -m learner.ingest --car <car> --ibt <session.ibt>`
4. **Damper zeta** — 5+ stints with varied LS comp clicks → `python -m validation.calibrate_dampers --car <car> --track <track>`

### The k-NN SessionDatabase

Ferrari Hockenheim has 17 sessions loaded. The k-NN is now gated on ≥10 sessions (Fix 5) rather than globally disabled. When Ferrari accumulates enough sessions at a track where `w_empirical > 0` in the scenario profile, k-NN will contribute.

---

## Architecture Problems (Ranked by Impact)

### 1. God Modules (pipeline.reason 166KB, pipeline.produce 78KB)
- Import 51 and 54 internal modules respectively
- Circular dependency between them
- Any change anywhere can break either one
- **Fix:** Extract sub-orchestrators per concern (heave workflow, damper workflow, etc.)

### 2. CarModel Monolith (car_model.cars 131KB)
- Single file defines ALL car physics for all 5 cars
- 46 files depend on it (29% of codebase)
- Circular with auto_calibrate (81KB) and garage (22KB)
- **Fix:** Split into per-car modules (`car_model/bmw.py`, `car_model/ferrari.py`, etc.)

### 3. BMW-First Architecture
- 34 files use car_model.cars without per-car branching
- `default="bmw"` in 2 critical dispatch points
- setup_registry returns "bmw" as fallback for unknown cars
- validation only tests BMW/Sebring
- **Fix:** Use `garage_params.py` schema (already created) as dispatch layer

### 4. Objective Function — Improved (2026-04-04)
- ~~All penalty weights zeroed~~ → damper compression signal added (r=-0.447), driver_mismatch weight zeroed when no profile
- ~~Only `lap_gain_ms` active~~ → lap_gain now includes damper compression bonus (gated on calibrated data)
- k-NN gated on ≥10 sessions (was globally disabled) — ready to enable via scenario profile
- BMW/Sebring Spearman improved from -0.06 to -0.30
- **Remaining:** Re-enable k-NN in `single_lap_safe` after holdout validation

### 5. Two Separate Empirical Systems
- `solver/session_database.py` → reads observations directly → **WORKS** (17 Ferrari sessions)
- `learner/empirical_models.py` → reads `*_empirical.json` → **POPULATED** (BMW: 86 observations, 41 corrections)
- Both systems serve different purposes: SessionDatabase for k-NN, empirical_models for learned corrections
- **Status:** Both functional. No consolidation needed.

---

## Data Flow (updated 2026-04-04)

```
IBT files → ibt_parser → session_info (YAML)
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                 ▼
         setup_reader    extract.py        build_profile.py
         (CurrentSetup)  (measurements)    (TrackProfile)
              │               │                 │
              ▼               ▼                 ▼
         observation     auto_calibrate    CalibrationGate
         JSON files      (m_eff, zeta)     (per-step blocking)
              │                                 │
              ▼                          ┌──────┴──────┐
         SessionDatabase                 ▼             ▼
         (k-NN predictions)        BLOCKED steps  RUNNABLE steps
              │                    → calibration  → 6-step solver
              ▼                      instructions → ObjectiveFunction
         k-NN scoring                               (scoring)
         (gated: ≥10 sessions)                       │
                                                     ▼
                                              CandidateEvaluation
                                              (lap_gain + damper comp
                                               + platform + k-NN)
```

**The calibration gate ensures no solver step runs with unproven data.** Blocked steps tell the user exactly what to collect. Runnable steps produce validated output.

---

## File Size Distribution

| Rank | File | Size | % of Total |
|------|------|------|------------|
| 1 | pipeline/reason.py | 166KB | 5.3% |
| 2 | car_model/cars.py | 131KB | 4.2% |
| 3 | solver/objective.py | 102KB | 3.3% |
| 4 | car_model/auto_calibrate.py | 81KB | 2.6% |
| 5 | analyzer/extract.py | 80KB | 2.5% |
| 6 | pipeline/produce.py | 78KB | 2.5% |
| 7 | car_model/setup_registry.py | 65KB | 2.1% |
| 8 | solver/laptime_sensitivity.py | 60KB | 1.9% |
| **Top 8** | | **763KB** | **24.2%** |

**8 files contain 24% of the codebase.** These are the ones that need structural fixes.

---

## Recommended Fix Priority (updated 2026-04-04)

| # | Fix | Impact | Effort | Status |
|---|-----|--------|--------|--------|
| 1 | Calibration gate — block uncalibrated steps | **CRITICAL** | Medium | **DONE** (2026-04-04) |
| 2 | Fix damper compression signal + zero-variance | **HIGH** | Low | **DONE** (2026-04-04) |
| 3 | k-NN gated on ≥10 sessions | **HIGH** | Low | **DONE** (2026-04-04) |
| 4 | DeflectionModel gate (no BMW coefficients on other cars) | **HIGH** | Low | **DONE** (2026-04-04) |
| 5 | Collect Ferrari calibration data for steps 4-6 | **HIGH** — unlocks 3 blocked steps | User action | PENDING |
| 6 | Collect Cadillac RH model data (10+ garage screenshots) | **HIGH** — unlocks step 1 cascade | User action | PENDING |
| 7 | Wire `garage_params.py` into solver dispatch | Medium — eliminates remaining BMW leakage | High | PENDING |
| 8 | Split car_model.cars into per-car modules | Medium — reduces blast radius | High | PENDING |
| 9 | Break pipeline.reason/produce god modules | Medium — long-term maintenance | Very High | PENDING |

---

## Next Steps (2026-04-04)

### 1. Collect calibration data for non-BMW cars

The calibration gate now tells users exactly what to collect. Priority data collection:

- **Ferrari steps 4-6:** ARB stiffness (3 garage screenshots), LLTD target (10+ IBT sessions), roll gains (3+ sessions), damper zeta (5-stint click-sweep)
- **Cadillac step 1:** Ride height model (10+ garage screenshots with varied spring/pushrod combinations)
- **Acura step 1:** Aero compression (3+ IBT sessions at different speeds), ride height model (10+ garage screenshots)

### 2. Continue BMW/Sebring objective hardening

Spearman improved from -0.06 to -0.30. Next targets:
- Reduce fallback dependence in signal extraction
- Test holdout stability across multiple validation runs
- Consider re-enabling w_empirical for BMW (99 sessions >> 10 threshold)

### 3. k-NN integration

The k-NN data quality gate (≥10 sessions) is implemented. Next: set `w_empirical > 0` in `single_lap_safe` scenario profile once holdout validation confirms it doesn't worsen BMW correlation.
