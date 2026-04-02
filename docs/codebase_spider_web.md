# iOptimal Codebase Spider Web Analysis
*Generated: 2026-04-01 18:00 UTC*

---

## Architecture Overview

**158 Python files** across **17 packages**, totaling **3,152 KB** of code.

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
    └──────┬──────┘   └──────┬──────┘   └─────────────┘
           │                 │
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
    analyzer.setup_reader.from_ibt()      ← [BUG FIXED] adapter_name now car-aware
              │
              ▼
    CurrentSetup (dataclass)              ← 51 fields parsed from IBT
              │
    ┌─────────┴───────────┐
    ▼                     ▼
  learner.ingest()    pipeline.produce() / pipeline.reason()
    │                     │
    ▼                     ▼
  Observation JSON    ObjectiveFunction.evaluate()
  (data/learnings/       │
   observations/)        ├── _estimate_lap_gain() → lap_gain_ms  ← ONLY ACTIVE SIGNAL
                         ├── platform_risk        → w=0.0 (ZEROED)
                         ├── driver_mismatch      → w=0.0 (ZEROED)
                         ├── empirical k-NN       → w=0.0 (ZEROED)
                         ├── envelope_penalty     → w=0.0 (ZEROED)
                         └── uncertainty          → w=0.0 (ZEROED)
```

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

## 🔴 ROOT CAUSE: Why Ferrari (and All Non-BMW) Setups Are Bad

### The Scoring Function Is Deaf

The `single_lap_safe` scenario profile (the default) sets ALL penalty weights to **0.0**:

```python
# solver/scenario_profiles.py — single_lap_safe
w_lap_gain=1.00,     # ← ONLY this is non-zero
w_platform=0.0,      # ← ZEROED
w_driver=0.0,        # ← ZEROED
w_uncertainty=0.0,    # ← ZEROED
w_envelope=0.0,       # ← ZEROED
w_staleness=0.0,      # ← ZEROED
w_empirical=0.0,      # ← ZEROED
```

This was done because the calibration report showed penalty terms are anti-correlated with lap time for BMW/Sebring:
- `damping_ms` Pearson: **+0.278** (higher penalty = FASTER laps — backwards)
- `rebound_ratio_ms` Pearson: **+0.138** (backwards)
- `lltd_balance_ms` Pearson: **+0.022** (noise)

**Why they're anti-correlated:** The penalty terms use BMW-calibrated targets (zeta=0.88/0.30, etc.) that don't reflect actual physics. More damping → higher zeta → lower penalty, but IBT data shows the optimal damping is at clicks 8-9, not at maximum. The monotonic assumption is wrong.

### `_estimate_lap_gain()` Is the Only Active Scorer

With all other weights at zero, `lap_gain_ms` is the ONLY differentiator. For Ferrari:

1. **LLTD:** Fixed at 0.510 (car constant) → `lltd_error=0.0` → **0ms contribution** ✅ (correct fix)
2. **Dampers:** `zeta_is_calibrated=False` for Ferrari → **damper scoring SKIPPED entirely**
3. **Rebound ratios:** Compute from raw click ratios → **4.8ms total range** (the only real differentiation)
4. **DF balance:** Uses aero map → some differentiation but small for wing=17
5. **Camber:** Fixed target → small differentiation
6. **Diff:** Fixed 30 Nm target → small
7. **TC:** No measured data → no gradient

**Total lap_gain_ms range for Ferrari: ~5ms** across the entire parameter space. For context, the actual lap time spread at Hockenheim is 87.5s to 90.5s = **3000ms**.

### The k-NN SessionDatabase Works But Is Unused

Ferrari Hockenheim has **17 sessions loaded** in the SessionDatabase. But `w_empirical=0.0`, so the k-NN prediction is **never applied to the score**. This is the most valuable signal available — real lap time data correlated with setups — and it's turned off.

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

### 4. Objective Function Collapse
- All penalty weights zeroed because they're anti-correlated with BMW data
- Only `lap_gain_ms` active → 5ms total differentiation for Ferrari
- k-NN empirical data (17 Ferrari sessions) completely unused
- **Fix:** Re-enable k-NN (`w_empirical`) for cars with ≥10 sessions; fix anti-correlated penalty terms individually

### 5. Two Separate Empirical Systems
- `solver/session_database.py` → reads observations directly → **WORKS** (17 Ferrari sessions)
- `learner/empirical_models.py` → reads `*_empirical.json` → **ALL EMPTY** (0 sessions everywhere)
- `*_empirical.json` files are created but NEVER populated by any pipeline
- **Fix:** Either wire the ingestion pipeline to populate `*_empirical.json`, or consolidate on SessionDatabase only

---

## Data Flow Bottlenecks

```
IBT files → ibt_parser → session_info (YAML)
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                 ▼
         setup_reader    extract.py        build_profile.py
         (CurrentSetup)  (measurements)    (TrackProfile)
              │               │                 │
              ▼               ▼                 ▼
         observation     auto_calibrate    ObjectiveFunction
         JSON files      (m_eff, zeta)     (scoring)
              │                                 │
              ▼                                 ▼
         SessionDatabase                   CandidateEvaluation
         (k-NN, 17 Ferrari)               (score = lap_gain_ms only)
              │
              ▼
         [UNUSED — w_empirical=0.0]
```

**The data is there. The scoring just doesn't use it.**

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

## Recommended Fix Priority

| # | Fix | Impact | Effort | Files |
|---|-----|--------|--------|-------|
| 1 | Re-enable `w_empirical` for cars with ≥10 k-NN sessions | **HIGH** — unlocks 17 Ferrari data points | Low | scenario_profiles.py |
| 2 | Fix anti-correlated damper penalty direction | **HIGH** — largest single penalty term | Medium | objective.py |
| 3 | Wire `garage_params.py` into solver dispatch | **HIGH** — eliminates BMW leakage | High | 34 files |
| 4 | Split car_model.cars into per-car modules | Medium — reduces blast radius | High | 46 dependents |
| 5 | Populate `*_empirical.json` or remove dead code | Medium — removes confusion | Low | learner/ |
| 6 | Break pipeline.reason/produce god modules | Medium — long-term maintenance | Very High | 50+ imports |
| 7 | Add Porsche Roll Spring to solver | Medium — Porsche currently unsolvable | Medium | solver/, car_model/ |
| 8 | Validate Cadillac torsion OD + adapter fix | Low (only 4 sessions) | Low | cars.py |

---

## Quick Wins (Can Implement Today)

### 1. Re-enable k-NN for Ferrari

```python
# scenario_profiles.py — single_lap_safe
# Change w_empirical from 0.0 to conditional:
# If car has ≥10 sessions in SessionDatabase → w_empirical=0.30
# This alone gives Ferrari 17 real data points of signal
```

### 2. Use `quali` scenario for grid search

The `quali` profile has ALL weights non-zero:
```python
w_platform=0.90, w_driver=0.35, w_uncertainty=0.45,
w_envelope=0.50, w_empirical=0.25
```

This profile would immediately give Ferrari meaningful differentiation. The reason `single_lap_safe` was zeroed is that it was tested only against BMW/Sebring — the penalty terms may work fine for Ferrari where the physics model is calibrated differently.
