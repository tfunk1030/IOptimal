# IOptimal — Remaining Work After 2026-04-09 Audit

## Context

A comprehensive 3-agent audit (solver workflow, calibration system, code quality) was completed on 2026-04-09. Three commits shipped fixing the highest-priority issues. This document tracks what remains.

### What Was Fixed (3 commits)

1. **Physics bug**: `objective.py` `tyre_vertical_rate_nmm` referenced a non-existent CarModel field — always `None`, meaning tyre compliance was never included in excursion calculations. Now uses per-axle `tyre_vertical_rate_front/rear_nmm`.
2. **Calibration gate cascade**: Step 5 now correctly depends on Step 4 (was Step 3), matching actual data flow where `solve.py:520` feeds `step4.k_roll_total` into the geometry solver.
3. **`CornerSpringSolution.rear_wheel_rate_nmm`** property added — eliminates 11 manual MR² conversion sites across 5 files. Single source of truth.
4. **Weak-upstream propagation**: downstream steps now know when input data has weak calibration via `weak_upstream` flag on `StepCalibrationReport`.
5. **Ferrari rear torsion 3.5x error**: now gated as `weak` in calibration gate `spring_rates` subsystem.
6. **getattr(car,...) elimination**: reduced from ~80+ to **1** (legitimate) across 14 core solver files. `objective.py`: 39→0, `solve_chain.py`: 31→0, `candidate_search.py`: car-specific patterns fixed.
7. **Exception handling**: 5 silent `except Exception: pass` blocks in `solve.py` now log descriptive messages. Calibration loading distinguishes `FileNotFoundError` from parse errors.
8. **Repo hygiene**: 19MB `repomix-output.xml`, debug files, and 486 generated learnings JSONs removed from git. `.gitignore` updated.
9. **Dead code**: `if False:` LLTD proxy block removed from `auto_calibrate.py`.
10. **Documentation**: `CLAUDE.md` updated with accurate getattr scope, `enhancementplan.md` tier table corrected (Porsche/Algarve is calibrated).

---

## Tier 1: Important Improvements

### 1.1 Function Decomposition — `auto_calibrate.py:fit_models_from_points`

**Problem**: 632-line monolithic function fitting 16 regression models sequentially. Hard to unit-test individual models.

**Approach**: Extract into `_fit_ride_height_models()`, `_fit_deflection_models()`, `_fit_spring_rate_models()`, `_fit_damper_models()`, `_fit_m_eff()`, etc. Each returns a partial dict merged into `CarCalibrationModels`.

**Files**: `car_model/auto_calibrate.py`
**Effort**: Medium — pure refactoring, must preserve exact behavior.

### 1.2 Function Decomposition — `pipeline/reason.py`

**Problem**: 4,071 lines in a single file. Mixes solver orchestration, output reasoning, report formatting, and comparison logic.

**Approach**: Split into `pipeline/reason_springs.py`, `pipeline/reason_aero.py`, `pipeline/reason_chassis.py`, `pipeline/reason_driver.py`. Keep `reason.py` as the orchestrator.

**Files**: `pipeline/reason.py` → 4-5 new sub-modules
**Effort**: Large — must preserve all output formatting exactly.

### 1.3 Structured Logging

**Problem**: Zero `import logging` in any solver module. All debugging uses `print()`. Silent failures are invisible. The `--json` flag suppresses print output but there's no log-level filtering.

**Approach**: Add `logging.getLogger(__name__)` to all solver modules. Replace `except Exception as e: log(...)` with `logger.warning(...)`. Add `--verbose` flag for DEBUG level.

**Files**: All `solver/*.py`, `pipeline/*.py`
**Effort**: Large (touches nearly every file, best done incrementally).

### 1.4 Regression Test Baseline Regeneration (BLOCKING)

**Problem**: The tyre compliance fix (`tyre_vertical_rate_front/rear_nmm` now passed to `damped_excursion_mm`) changes excursion/sigma calculations. For BMW with front heave ~50 N/mm and tyre ~300 N/mm, the effective series rate becomes `1/(1/50 + 1/300) = 42.9 N/mm` — a ~14% reduction. This shifts the objective function's platform risk, sigma targets, and potentially spring recommendations. The change is physically correct (tyre compliance was always intended to be modeled — the parameter existed but was never populated), but **existing `.sto` baselines will not match** and `test_setup_regression.py` will fail until baselines are regenerated.

**Impact**: `solver/objective.py:857-889` — front/rear excursion now uses softer effective rate → larger excursions → more conservative spring recommendations (springs move stiffer to compensate for tyre compliance).

**Approach**: After verifying the physics is correct on a real IBT run, regenerate baselines:
```bash
python -m pipeline.produce --car bmw --ibt <bmw_ibt> --wing 17 --sto tests/fixtures/baselines/bmw_sebring_baseline.sto
python -m pipeline.produce --car porsche --ibt <porsche_ibt> --fuel 58 --wing 17 --sto tests/fixtures/baselines/porsche_algarve_baseline.sto
```

**Files**: `tests/fixtures/baselines/`
**Effort**: Small — but requires IBT telemetry files.

---

## Tier 2: Code Quality

### 2.1 Remaining getattr Patterns in Auxiliary Files

**Status**: Core solver files are clean (1 legitimate `getattr(car,...)` remaining). Auxiliary files still have patterns:

| File | Count | Risk |
|------|-------|------|
| `solver/candidate_search.py` | ~170 (non-car) | Low — on `gr`, `candidate.*`, `setup`, `base_result` objects |
| `solver/bmw_rotation_search.py` | 113 | Low — BMW-only file |
| `solver/bmw_coverage.py` | 78 | Low — BMW-only file |
| `pipeline/reason.py` | ~65 | Low — on `measured`, `driver`, `diagnosis` objects |
| `solver/predictor.py` | 46 | Low — on prediction results |
| `solver/setup_fingerprint.py` | 55 | Low — on fingerprint/cluster data |

Most are legitimate optional-data access where the target objects may not have all fields (e.g., telemetry channels may be absent, solver steps may not have run).

**Recommendation**: Leave as-is unless a specific bug surfaces. These are not physics-value fallbacks.

### 2.2 Unit Tests for Core Solver Steps

**Missing dedicated tests for**:
- `solver/damper_solver.py` — Step 6
- `solver/corner_spring_solver.py` — Step 3
- `solver/heave_solver.py` — Step 2
- `solver/arb_solver.py` — Step 4
- `solver/wheel_geometry_solver.py` — Step 5

**Recommendation**: Add parametrized tests with synthetic track profiles and known-good physics outputs. Priority: damper and corner spring solvers (most complex physics).

### 2.3 CI/CD Pipeline

**Current state**: 42 test files exist but no GitHub Actions workflow. `pytest` not in requirements.

**Approach**:
1. Add `requirements-dev.txt` with pytest, pytest-cov
2. Create `.github/workflows/test.yml`
3. Start with syntax compilation and non-IBT-dependent tests
4. Add IBT-dependent tests as optional (manual trigger)

### 2.4 Error Handling in `output/report.py`

**Problem**: 13 `except Exception: pass` blocks — the highest concentration in the codebase. These suppress file I/O failures and validation data loading errors.

**Files**: `output/report.py`
**Effort**: Small — mechanical replacement with `logger.warning()`.

---

## Tier 3: Nice-to-Haves

### 3.1 `car_model/cars.py` Split

**Problem**: 3,057 lines mixing 5 car definitions with all dataclass definitions.

**Approach**: Move car instances to `car_model/cars/bmw.py`, `car_model/cars/porsche.py`, etc. Keep dataclasses in `car_model/cars.py`.

### 3.2 Stale Documentation Cleanup

**Files to archive or consolidate**:
- `currentjob.md` — stale, references early development TODOs
- `plan.md` — superseded by `docs/repo_audit.md` and `enhancementplan.md`
- `AUDIT_REPORT.md`, `ENGINEERING_AUDIT.md` (77 KB, 70 KB) — outdated post-calibration-gate

### 3.3 Server Security Hardening

- Restrict CORS `allow_origins=["*"]` in `server/app.py:34`
- Add request rate limiting to auth endpoints
- Consider bcrypt/argon2 for API key hashing (currently SHA-256, acceptable for bearer tokens)

### 3.4 Uncalibrated Car Weight Distributions

Cadillac, Acura, and Porsche use `weight_dist_front=0.45` (placeholder). If wrong, Steps 1 and 4 produce incorrect outputs. BMW (0.4727) and Ferrari (0.476) are calibrated from IBT corner weights.

---

## Calibration Status Summary

| Car/Track | Steps | Status | Next Action |
|-----------|-------|--------|-------------|
| BMW/Sebring | 6/6 | Fully calibrated | Maintain — regenerate baselines after tyre compliance fix |
| Porsche/Algarve | 6/6 | Fully calibrated | Same |
| Ferrari/Sebring | 3/6 | Steps 4-6 blocked | Collect 3+ varied-ARB IBT sessions for ARB calibration |
| Cadillac/Silverstone | 2/6 | Step 1 RH model uncalibrated | Collect 10+ garage screenshot sessions |
| Acura/Hockenheim | 0/6 | All blocked (Step 1 cascades) | Calibrate aero compression + ride height model first |
