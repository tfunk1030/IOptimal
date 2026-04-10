# Workflow + Calibration Audit (2026-04-10)

## Scope and baseline
- Requested baseline branch: `codextwo`.
- In this checkout, only branch `work` exists (`git branch -a`), with no configured remotes. So this audit uses the current tip of `work` as the nearest available baseline.

## Executive findings
1. **CI workflow is structurally incomplete for this repo layout.**
   - `.github/workflows/python-tests.yml` runs `python -m pytest -q tests` but does not set `PYTHONPATH=.` and does not install package(s) in editable mode.
   - Test modules import top-level packages (`car_model`, `analyzer`, `aero_model`, etc.) that are not importable by default in a clean environment.
   - Result: collection can fail before meaningful regression validation starts.

2. **Validation/calibration evidence path is BMW/Sebring constrained.**
   - `validation/run_validation.py` hard-targets BMW @ Sebring in `_target_samples` and raises runtime errors if those observations are absent.
   - Confidence/status taxonomy in the same file marks most non-BMW/non-Sebring paths as `unsupported` or exploratory.
   - This means current calibration confidence cannot be generalized across car/track combinations.

3. **Public docs already acknowledge critical accuracy risks.**
   - `CLI_GUIDE.md` warns `solver.solve` bypasses garage validation and learning pipeline.
   - Existing audit docs identify BMW-calibration leakage into other cars as a top systemic risk.

## Workflow setup accuracy (current state)

### What is correct
- Workflow exists and is wired for push + PR events.
- It installs development dependencies from `requirements-dev.txt`.

### What is inaccurate / brittle
- Missing import-path bootstrap for local top-level modules.
- Workflow assumes one flat test target (`tests`) without staged smoke gates.
- No dedicated calibration evidence check (`validation/run_validation.py`) in CI.
- No artifact publishing for validation JSON/markdown outputs, which prevents regression evidence tracking over time.

### Observed in this audit run
- Running `pytest -q tests` failed in collection with multiple `ModuleNotFoundError` (e.g., `aero_model`, `analyzer`, `car_model`, `fastapi`), indicating environment/bootstrap mismatch.
- Running focused tests with `PYTHONPATH=.` reduced import errors, but several logic/data-dependent tests still failed (predictor field mismatch and validation corpus assumptions).

## Calibration ability (current state)

### Strengths
- There is a real calibration/validation subsystem (`validation/`), including report generation and objective calibration helpers.
- Solver path has explicit calibration gating logs (`[BLOCKED] ... uncalibrated inputs`) to avoid false precision when data is missing.

### Gaps
- Validation report builder is effectively single-anchor (BMW/Sebring).
- Non-BMW support is partly documented but not backed by equally strong held-out validation evidence in current runtime.
- Test expectations are coupled to specific local observation corpora; portability is low.

## "Bad things" (highest-risk issues)
1. **False confidence risk in CI:** workflow can appear to run while not exercising intended solver/calibration logic due to import/bootstrap failures.
2. **Calibration scope risk:** scoring and validation claims can be over-interpreted outside BMW/Sebring.
3. **Entry-point inconsistency risk:** users can run `solver.solve` path that bypasses full validation flow.
4. **Data-coupling risk in tests:** tests that require local observation datasets fail hard in clean environments.

## Enhancement plan

### Phase 0 (Immediate hardening: 1 day)
- Set `PYTHONPATH=.` in CI test step (or package the repo and install with `pip install -e .`).
- Add a first-stage smoke suite (small deterministic tests) before full test collection.
- Add a CI preflight that prints Python version, working directory, and key import checks.

### Phase 1 (Calibration evidence reliability: 2–4 days)
- Refactor `validation/run_validation.py` to accept `--car` and `--track` targets with graceful "insufficient data" status instead of hard runtime failure.
- Persist validation outputs as CI artifacts (`objective_validation.json`, markdown summary).
- Add minimum evidence gates per supported combo (sample count, non-vetoed count, correlation floor).

### Phase 2 (Test architecture resilience: 3–5 days)
- Separate tests into:
  - **unit deterministic** (no corpus requirement),
  - **fixture-backed integration** (checked-in small datasets),
  - **local-data/regression** (optional, non-blocking in CI or run in scheduled jobs).
- Resolve naming drift in telemetry prediction API (e.g., `rear_power_slip_ratio_p95` vs legacy aliases).

### Phase 3 (Operational accuracy governance: 1 week)
- Enforce a policy: no claim of "optimized" for a car/track unless calibration gate status is supported + evidence thresholds pass.
- Add a support matrix generated from validation outputs, published in docs and surfaced in CLI.
- Keep explicit warnings on unvalidated paths and bypassed validation entrypoints.

## Suggested KPI dashboard
- CI collection success rate.
- Deterministic test pass rate.
- Validation sample count by car/track.
- Spearman/Pearson correlation for non-vetoed samples.
- Veto rate and missing/fallback signal rates.
- Drift of calibration weights over time.

## Recommended next action
Implement Phase 0 immediately, then Phase 1 in the next working session to convert current calibration evidence from static BMW/Sebring anchoring into parameterized, auditable support tiers.
