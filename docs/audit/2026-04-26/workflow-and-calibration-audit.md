# Workflow Accuracy & Calibration Audit — 2026-04-26

**Branch audited:** `main` (post PR #94 merge, Apr 26 2026 7:31 UTC)
**Scope:** Full repo — 6-step solver, calibration system (gate + auto-calibration + learner),
pipeline orchestration, code quality.
**Method:** Three parallel deep static-analysis sweeps (workflow, calibration, code quality)
cross-referenced against the prior 2026-04-10 audit (`docs/audit_2026_04_10.md`) and live code
on this commit. All quoted line numbers were re-verified against the in-tree files.

---

## Section 0 — TL;DR

The codebase has matured significantly since the 2026-04-10 audit. **Most critical bugs flagged
then are now fixed** (driver_profile NameError, shock-velocity concatenation, full-gain DF
correction, R²-floor-less calibration writes, pushrod blanket "calibrated", LOO=0 for n<5,
Step 6→Step 2 unguarded coupling, perch coefficient mismatch, sigma sticky pre-check,
RideHeightModel.is_calibrated default).

What remains is a smaller and more nuanced set of correctness, integrity, and maintainability
issues. The five highest-impact items are:

1. **The LLTD chain is still epistemically broken.** `analyzer/extract.py:lltd_measured`
   is a geometric proxy. The learner stores it without an `lltd_is_proxy` tag (and the
   solver's guard defaults to "skip" rather than "warn"). The objective function still
   has comments tying setup score to `lltd_measured`. ARB Step 4 quality is therefore
   unverifiable across all cars.
2. **`recommend.py` LLTD branch never fires.** Diagnose emits "Roll distribution proxy …
   front-heavy / rear-heavy" cause strings; recommend keys off `"too high" / "too low"
   in cause`. The whole ARB-balance recommendation chain is dead in production logs.
3. **Index-vs-N/mm contamination in the learner's `m_eff` correction.** `learner/empirical_models.py:794-823`
   computes `k_nm = heave * 1000` without per-car decode. For Ferrari/Acura where `front_heave_nmm`
   is actually an index (0-18 / 1-26), `m_eff` will be ~3 orders of magnitude wrong and silently
   feeds the heave solver via `learned_corrections.py` once any car accumulates 5+ sessions.
4. **Pipeline duplication: `produce.py` (2.0k lines) vs `reason.py` (4.1k lines, single
   1100-line `reason_and_solve` function).** Both load the car, apply calibration, resolve
   scenario, and orchestrate the solver; behavior already drifts (e.g. silent-pass blocks,
   error logging, lap selection).
5. **Bayesian optimizer is tuning a synthetic proxy, not the real `ObjectiveFunction`.**
   Documented limitation but improvement % is exposed in user-facing tools and is not
   physics-anchored.

The 6-step physics pipeline itself is sound. The objective function (`solver/objective.py`)
remains the single largest technical-debt surface (2,132 lines, 300+ line method, BMW-flavored
constants throughout) and is the most leveraged file for both correctness and maintenance.

---

## Section 1 — Status of 2026-04-10 audit findings

### Critical bugs — all FIXED

| ID | Description | Status | Note |
|----|-------------|--------|------|
| BUG-C1 | `driver_profile` NameError in `pipeline/produce.py` grid path | **FIXED** | Now passes `driver_profile=driver` to `run_legal_search` (`pipeline/produce.py:1343-1348`). |
| BUG-C2 | `np.concatenate` distortion of shock velocity for heave+roll cars | **FIXED** | `analyzer/extract.py:464-480` uses `np.maximum(lf_sv, rf_sv)` for synthesised case, `concatenate` only for true per-corner shocks. |
| BUG-C3 | Thermal recommendation strings mismatched | **FIXED** | `analyzer/recommend.py` matches `"inside too hot"` / `"too flat / outer loaded"` (verified). |
| BUG-C4 | LLTD recommendation branch dead | **STILL PRESENT** | Symptom check now allows `"roll distribution proxy"`, but the inner branches still require `"too high"` / `"too low"` in `problem.cause`, which `analyzer/diagnose.py:700-707` never emits for that case (it says "front-heavy" / "rear-heavy"). End result is identical: branch never fires. **See finding F-1 below.** |

### Calibration findings — mostly FIXED

| ID | Description | Status |
|----|-------------|--------|
| CAL-H1 | LLTD proxy contaminates learner | **PARTIALLY FIXED** — solver guards on `lltd_is_proxy`, but learner never sets the flag (defaults to "skip"). The `_fit_lltd_vs_arb` regression still runs against proxy data and stores it as `FittedRelationship` with `y_param="lltd_measured"`. |
| CAL-H2 | `_fit()` writes models without R² floor | **FIXED** — `car_model/auto_calibrate.py:766-807` sets `is_cal = r2 >= R2_THRESHOLD_BLOCK`, with LOO/train ratio guard. |
| CAL-H3 | Roll-gain "calibration" without fitting | **FIXED** — code path now informational only (`auto_calibrate.py:1644-1668`). |
| CAL-H4 | `m_eff` from raw spring index | **FIXED in `garage_model.py`** — guards on plausible N/mm range (`car_model/garage_model.py:211-248`). **Still broken in `learner/empirical_models.py:794-823`** — see F-3. |
| CAL-H5 | Gate docstring says weak BLOCKS | **ALIGNED** — code matches behavior. |

### Architecture findings — mostly FIXED

| ID | Description | Status |
|----|-------------|--------|
| ARCH-H1 | Provisional Step 6 before 4/5 in `materialize_overrides` | **FIXED / DOCUMENTED** — `solve_chain.py:1261-1277` now reuses `base_result.step6` HS coefficients only as a coupling estimate, with a clear comment about why a full re-solve here would violate ordering. |
| ARCH-H2 | Modifier floors in absolute N/mm | **FIXED** — `solver/modifiers.py:357-373` makes floors car-relative via `_heave_min` multiples. |
| ARCH-H3 | Perch coefficient `0.08` vs `FRONT_HEAVE_PERCH_K=0.001614` mismatch | **FIXED** — `solver/legal_space.py:145-160` computes `perch_sensitivity = -(k_heave_to_rh / k_perch_to_rh)` from the calibrated ride-height model, with the `FRONT_HEAVE_PERCH_K * 100.0` line as an explicit fallback only when no ride-height model exists. |
| ARCH-H4 | DF balance correction full-gain | **FIXED** — `solve_chain.py:840-845` uses `0.6 * correction`. |

### Medium findings

| ID | Description | Status |
|----|-------------|--------|
| MED-1 | Lap selection inconsistent (`driver_style` ignores filters) | NOT VERIFIED THIS PASS — flagged for follow-up. |
| MED-2 | `detect_car_adapter` defaults to `"bmw"` | **STILL PRESENT** but `yaml_path_to_canonical` / `sto_param_id_to_canonical` now require explicit `car=`. The legacy `detect_car_adapter` heuristic still returns `"bmw"` for unknown YAML (`car_model/setup_registry.py:620-639`). |
| MED-3 | Track key collisions (`split()[0]`) | **PARTIALLY FIXED** — `learner/knowledge_store.py:42-66` provides `track_key_from_name()`, but `learner/ingest.py`, `learner/recall.py`, `learner/report_section.py`, and `teamdb/aggregator.py:104` all still use `track.lower().split()[0]` for model IDs. Migration not finished. |
| MED-4 | Observation timestamp from ingest | **FIXED** — `learner/observation.py:484-498` uses session timestamp / file mtime. |
| MED-6 | `pushrod_geometry` blanket calibrated | **FIXED** — `calibration_gate.py:581-600` checks `car.pushrod.is_calibrated`. |
| MED-9 | LOO RMSE = 0 for n<5 | **FIXED** — `auto_calibrate.py:753-757` sets NaN. |
| MED-10 | `setup_registry` defaults to `car="bmw"` | **FIXED** — explicit `car` required. |

### Low findings

| ID | Description | Status |
|----|-------------|--------|
| LOW-1 | `R2_THRESHOLD_WARN` unused | **FIXED** — used in gate warning text. |
| LOW-2 | `t_avg_m` dead variable | **FIXED** — variable removed; comment is now stale (purely cosmetic). |
| LOW-3 | `sigma_tol_mm` parameter unused | **STILL PRESENT** — `solve_chain.py:_iterative_coupling_refinement` ignores σ in convergence check. |
| LOW-4 | `_num()` swallows errors | NOT VERIFIED THIS PASS. |
| LOW-5 | 500+ `getattr` in candidate_search etc. | **PARTIALLY ADDRESSED** — total `getattr(car,…)` calls under `solver/` reduced to 31 across 7 files; still concentrated in `candidate_search.py` (13) and `bmw_rotation_search.py` (8). |
| LOW-6 | Knowledge store concurrency | **PARTIALLY FIXED** — `_atomic_write` uses `fcntl` exclusive lock on Unix only (`learner/knowledge_store.py:100-121`). Windows path remains lock-free. |
| LOW-7 | `RideHeightModel.is_calibrated=True` default | **FIXED** — defaults to `False`. **`DeflectionModel` still defaults `True`** (`car_model/cars.py:609-622`). |
| LOW-8 | Porsche legality vs garage_params range | **PARTIALLY FIXED** — keys disambiguated, but `legality.py` Porsche `rear_heave_max` (1000 / "rear third") still pairs with `garage_params.py` Porsche `rear_spring_range_nmm` (100-400) in a way that is documented only by adjacent comments. |

---

## Section 2 — New findings (verified on this commit)

### Severity legend
**[CRIT]** wrong output / unreachable code on a primary path • **[HIGH]** silent quality
degradation or contract break • **[MED]** maintainability / future-bug risk • **[LOW]** cosmetic
or test scaffolding.

---

### F-1 [CRIT] — LLTD recommend branch is **still** dead despite the symptom-string patch

**File:** `analyzer/recommend.py:415-418` and `analyzer/diagnose.py:700-707`

```python
elif "lltd" in problem.symptom.lower() or "roll distribution proxy" in problem.symptom.lower():
    if "too high" in problem.cause.lower():
```

`diagnose.py` never puts `"too high"` or `"too low"` into the **cause** field for the
roll-proxy problem; the cause text is always either "Ride-height-derived roll support
proxy is front-heavy …" or its rear-heavy equivalent. Net effect: the entire ARB-balance
recommendation chain that would have routed Step 4 advice to the user is unreachable in
production telemetry.

**Fix:** match cause strings to what diagnose actually emits (`"front-heavy"` →
"too high", `"rear-heavy"` → "too low"), or unify on a single normalized symptom format.
Add a unit test that asserts every diagnose symptom string maps to at least one recommend
handler.

---

### F-2 [HIGH] — `learner/empirical_models.py` writes `lltd_measured_mean` without proxy tag

**File:** `learner/empirical_models.py:780-792` (and `_fit_lltd_vs_arb` ~261-287)

```python
lltd = obs.get("telemetry", {}).get("lltd_measured", 0)
…
models.corrections["lltd_measured_mean"] = float(np.average(lv, weights=lw))
models.corrections["lltd_measured_std"] = float(np.std(lv))
```

There is no `lltd_is_proxy` flag set anywhere on this output. The downstream guard in
`solver/learned_corrections.py:156-176` defaults to "treat as proxy → skip", which is
safe today, but:

- `_fit_lltd_vs_arb` still creates a `FittedRelationship` over proxy data with the
  misleading label `y_param="lltd_measured"`. Any future caller introspecting the model
  metadata will see a "calibrated" relationship between rear ARB blade and LLTD that is
  actually a regression of a constant.
- If a future change flips the guard default, the proxy will silently become a baseline
  correction.
- The objective function's docstring/comments at `solver/objective.py:1323-1327` still
  describe LLTD scoring as if `lltd_measured` were a real measurement.

**Fix:** stamp every learner output with `lltd_is_proxy=True` until true wheel-load
telemetry exists. Drop `_fit_lltd_vs_arb` entirely (the slope is statistically zero), or
rename the relationship to `roll_proxy_vs_arb` with a docstring explaining what it
measures.

---

### F-3 [HIGH/CRIT for Ferrari/Acura] — Indexed cars: `m_eff` correction multiplies index by 1000

**File:** `learner/empirical_models.py:794-823`

```python
heave = obs.get("setup", {}).get("front_heave_nmm", 0)
…
k_nm = heave * 1000
m_eff = k_nm * (exc / 1000 / sv_p99) ** 2
```

For BMW/Porsche, `front_heave_nmm` is an N/mm value and the `* 1000` correctly converts
to N/m. For Ferrari (front heave = 0…18 indices) and Acura (1…26 indices), the value
stored in observation `setup.front_heave_nmm` can be the index itself (depending on
ingestion path — the new `GarageSetupState.from_current_setup` does decode, but legacy
observations and `analyzer/setup_reader.py` paths may not). When that happens:

- `k_nm = index * 1000` ≈ 0–26000 instead of the actual ~150000–600000.
- `m_eff` computed from `k * (exc/v)^2` is wrong by 1–2 orders of magnitude.
- The `[100, 4000] kg` sanity guard at line 814 catches *some* cases (very small index
  values), but Ferrari index 5 with realistic `(exc/v)≈0.3` gives `m_eff = 5000 * 0.09 ≈ 450 kg`
  which **passes the guard** and silently corrupts the learner correction.

This survives until a Ferrari/Acura accumulates ≥5 sessions and `learned_corrections.py`
applies `m_eff_front_empirical_mean` to the heave solver.

**Fix:** In `_compute_corrections` (and `_fit_lltd_vs_arb`), explicitly decode through
`car.heave_spring.front_index_to_nmm()` (or equivalent) before any arithmetic. Add an
assertion `assert 50 <= heave_nmm <= 1500, "looks like an index not N/mm"`. Same fix
needed for `rear_heave_nmm` / `rear_third_nmm` if any rear m_eff path is added.

---

### F-4 [HIGH] — `recommend` LLTD/balance branch is the only consumer of an entire
`diagnose` problem class

**Files:** `analyzer/diagnose.py:700-721`, `analyzer/recommend.py:415-470`

This is the same root-cause as F-1 but stated as a contract problem: there is no
test that round-trips diagnose symptom strings through recommend handlers. As a result
two different developers can edit the strings on each side and never see a runtime
failure — the recommendations just stop appearing in reports.

**Fix:** Add `tests/test_diagnose_recommend_contract.py` that constructs each
`HandlingProblem` category and asserts at least one recommendation is generated.

---

### F-5 [HIGH] — `_fit_lltd_vs_arb` regresses a near-constant against a discrete control

**File:** `learner/empirical_models.py:261-287`

The "LLTD" target value (geometric proxy) was measured to vary by ≤0.1pp across an ARB
sweep that varied real LLTD by ~5pp. Any regression on that data has slope ≈ 0 with
huge standard error, but the function still:
- Returns a `FittedRelationship` with `is_calibrated=True` if R² > 0.5 (which is rare
  but possible by chance with small N).
- Writes the model to `models.json` as if it were a usable empirical relationship.

This is the same class of issue as CAL-H2 (auto_calibrate writing low-R² models),
caught and fixed there but not propagated to learner.

**Fix:** Apply the `R2_THRESHOLD_BLOCK = 0.85` floor here too. Also reject when the
*range* of the target variable is below noise (e.g., `target_range / target_std < 5`).

---

### F-6 [HIGH] — Bayesian optimizer's `_score()` is a heuristic, not the objective

**File:** `solver/bayesian_optimizer.py:179-199`

The proxy `_score()` lives behind a docstring warning, but:
- `BayesianResult.improvement_pct` is exposed in CLI tools and surfaced in run traces
  / decision traces.
- There is no calibration gate on this optimizer.
- Test `tests/test_bayesian_optimizer_numerics.py` only covers GP numerics, not whether
  the returned setup respects `car.garage_ranges` or improves the real `ObjectiveFunction`.

**Fix:** Either (a) wire `_score()` to a lightweight evaluator that calls
`ObjectiveFunction.evaluate_physics(predictor.predict(setup))`, or (b) gate the
optimizer behind `car.calibration_status == "calibrated"` and clearly mark its output
as exploratory in `pipeline/produce.py`.

---

### F-7 [HIGH] — `solver/objective.py` BMW-flavored damper defaults in `_estimate_lap_gain`

**File:** `solver/objective.py:1386-1393`

```python
f_ls_comp = params.get("front_ls_comp", 7)
f_ls_rbd  = params.get("front_ls_rbd", 6)
f_hs_comp = params.get("front_hs_comp", 5)
…
r_hs_comp = params.get("rear_hs_comp", 3)
```

These default click counts (7/6/5/5/5/5/3/3) come from BMW Sebring data. If a non-BMW
candidate is scored without all damper params populated, missing keys silently take
BMW values. For Porsche/Acura whose click ranges differ (Porsche ratios are different
clicks per Newton), these defaults bias the rebound-ratio penalty (lines 1437-1452)
toward BMW.

**Fix:** Read `car.damper.ls_comp_range` / `hs_comp_range` and use range midpoints, or
require every damper key to be present and raise a `KeyError` otherwise. The
`_rbd_penalty` function already takes `comp` from params; the failure mode is that
when comp itself is defaulted, the whole gradient is wrong.

---

### F-8 [HIGH] — `solver/sensitivity.py` defaults are still BMW Sebring

**File:** `solver/sensitivity.py:198-213, 376-384`

```python
def analyze_step2_constraints(
    step2: HeaveSolution,
    sigma_target_front_mm: float = 8.0,
    sigma_target_rear_mm: float = 10.0,
) -> list[ConstraintProximity]: …

INPUT_UNCERTAINTIES = {
    "ride_height_mm": 0.5,  # ±0.5mm sensor noise (calibrated from BMW Sebring validation)
    …
}
```

If callers (e.g. `output/run_trace.py`, `pipeline/report.py`) don't thread
`SolveChainInputs.heave_sigma_target` through, the sensitivity report displays
"binding" / "near binding" classifications against BMW targets for any car. That
misleads users about which Porsche/Ferrari constraints are actually tight.

**Fix:** Make `analyze_step2_constraints(step2, *, sigma_target_front_mm,
sigma_target_rear_mm)` keyword-only with no defaults; require callers to pass values.
Update all call sites in one sweep.

---

### F-9 [HIGH] — `pipeline/produce.py` and `pipeline/reason.py` duplicate orchestration

**Files:** `pipeline/produce.py` (2,045 lines), `pipeline/reason.py` (4,083 lines, of
which `reason_and_solve` itself is ~1,100 lines, lines 2789-3959 in this checkout).

Both perform: load car → load surface → extract → segment → driver style → adaptive
thresholds → diagnose → aero gradients → modifiers → run solver chain → supporting →
report → write `.sto`. The drift surface is large:

- Different silent-pass blocks (`produce.py:343-344`, `reason.py:2852-2853`).
- Different lap selection (`reason.py` re-implements `analyze_driver` invocation).
- Different scenario resolution; `reason.py` has its own auto lap-time floor scan.
- Different calibration application order.

This is the highest-leverage maintenance debt in the project.

**Fix:** extract a shared `OrchestrationContext` that runs Phases A–J as composable
steps, and have `produce` and `reason` call into it. Target: each orchestrator <500
lines.

---

### F-10 [MED] — `front_arb_*` driver-anchor parameters in `arb_solver.solve()` accepted but unused

**File:** `solver/arb_solver.py:255-264, 370-399`

The docstring is honest ("ACCEPTED BUT NOT CURRENTLY USED IN SEARCH"). But the
driver-anchor pattern that the rest of the codebase relies on is asymmetric: rear ARB
will reflect driver-loaded blade when LLTD physics is uncertain; front ARB ignores
driver intent. For a driver who has already converged on a front ARB and varies only
rear, the solver may recommend a different front blade than what they loaded with no
indication it disagrees.

**Fix:** symmetric front anchor or explicit log line when driver-loaded front blade
differs from solver recommendation by ≥1 stiffness step.

---

### F-11 [MED] — `sigma_tol_mm` declared in `_iterative_coupling_refinement` but ignored

**File:** `solver/solve_chain.py:750-828`

```python
def _iterative_coupling_refinement(…, sigma_tol_mm: float = 0.1) -> …:
    …
    converged = (df_residual <= df_tol and lltd_residual <= lltd_tol)
```

Convergence ignores σ. If σ is still oscillating between iterations (e.g. damper-spring
coupling pulls it around) but DF and LLTD have settled, the loop exits with σ violated.

**Fix:** include `sigma_residual` in the convergence check, or remove the parameter
from the signature.

---

### F-12 [MED] — Track-key `split()[0]` migration is unfinished

**Files:** `learner/ingest.py:283, 527, 547, 652`, `learner/recall.py:40, 238`,
`learner/report_section.py`, `teamdb/aggregator.py:104`, `solver/objective.py:352-362`,
`output/report.py:353`, `pipeline/produce.py:398, 1847`.

The safe `track_key_from_name()` exists in `learner/knowledge_store.py:42-66` but most
call sites still use `track.lower().split()[0]`. Cars at "Hockenheim Grand Prix Circuit"
vs "Hockenheim Short Circuit" share the same first token; same for any common-prefix
track family iRacing might add.

**Fix:** mass-replace `.split()[0]` with `track_key_from_name(track)` (or
`registry.track_key`) and add a regression test that constructs two distinct full
track names sharing a first word and asserts they map to different model IDs.

---

### F-13 [MED] — `DeflectionModel.is_calibrated` defaults `True`

**File:** `car_model/cars.py:609-622` (vs `RideHeightModel` default `False` at line 265)

`RideHeightModel` was fixed to default `False` (LOW-7); `DeflectionModel` was not.
Any new car that constructs `DeflectionModel()` without arguments will declare itself
calibrated even though all coefficients are zero. The gate's deflection check looks at
coefficient values, not the flag, so this is silent until a future caller relies on
the flag directly.

**Fix:** set `is_calibrated: bool = False` and update all explicit calibrated
instantiations (BMW/Porsche) to pass `is_calibrated=True`.

---

### F-14 [MED] — `auto_calibrate.py` `_get_dummy_car` falls back to BMW

**File:** `car_model/auto_calibrate.py:621-625`

When `car_name` is missing/empty in some helper paths, the dummy car loader uses
`get_car("bmw")`. Indexed-car decode functions called from that helper would silently
apply BMW physics. Today no production path appears to hit it with empty name, but
it's a footgun for the next CLI flag added.

**Fix:** Raise on empty/None instead of defaulting.

---

### F-15 [MED] — Mid-solve `car` model mutation

**Files:** `solver/solve.py:295-303` (with `--learn`), `pipeline/produce.py:347-350`,
`car_model/auto_calibrate.py:apply_to_car`, `solver/objective.py:1199-1201`
(`self._measured` / `self._driver` set inside `evaluate`).

The repo loads one `CarModel` per pipeline run and mutates it in-place when applying
calibration or learner corrections. `ObjectiveFunction` similarly stashes
session context on `self`. Implications:

- Concurrent solves over the same `CarModel` (e.g. parallel candidate evaluation in
  `legal_search`) can race.
- Test reproducibility: a test that invokes `apply_to_car` then constructs a second
  car runs against partially mutated global state from a prior test.

**Fix:** make `apply_to_car` return a `dataclasses.replace(car, …)` copy. Make
`ObjectiveFunction` accept session context via parameters not instance state, or
copy-on-write before evaluate.

---

### F-16 [MED] — `solver/candidate_search.py` BMW-flavored constants

**File:** `solver/candidate_search.py:26-27, 114, 986`

- `_BMW_TORSION_OD_OPTIONS_MM` hardcoded list of BMW torsion ODs.
- `front_torsion_od_mm` default `(13.9, 18.2)` (BMW range).
- `getattr(car, "canonical_name", "bmw")` fallback string.

The file is supposedly cross-car, but every place a feature is missing falls back to
BMW. This makes it impossible to detect a missing field for non-BMW cars during a
candidate search — the search just runs BMW-flavored.

**Fix:** raise on missing per-car torsion options; remove the BMW fallback string
("unknown" or raise is preferable, matching the 2026-04-10 audit follow-up #17).

---

### F-17 [LOW] — Stale `t_avg_m` comment in `wheel_geometry_solver.py:183-188`

The variable was removed but the comment "t_avg_m computed here for potential future
split-axle roll model" remains, falsely implying a computation. Cosmetic.

---

### F-18 [LOW] — Bare `except:` in `research/ferrari_calibration_mar21.py:68,72,89,164`

Research file, but bare excepts spread bad patterns to anyone copy-pasting from it.

---

### F-19 [LOW] — Test skips lack ticket references

`tests/test_validation_reporting.py:8,16`, `tests/test_objective_calibration.py:11,19`,
`tests/test_setup_regression.py:95,112`. Skips are descriptive but not actionable —
no one knows whether they're "permanent because of data dependency" or "TODO".

**Fix:** add either `pytest.mark.skipif` with a `reason="GH-#xx"` or a
`# permanent: requires …` marker comment.

---

### F-20 [LOW] — Stale-comment risk in `solver/objective.py:1505+` "ZEROED OUT, do NOT restore"

Reads like commented-out dead code; intentional but confusing for auditors. Replace
with a proper `def _legacy_arb_extreme_penalty():` stub that always returns 0 with a
docstring explaining the empirical regression direction.

---

## Section 3 — Workflow accuracy by step (current state)

### Step 1 — Rake / Ride heights

**Accuracy:** HIGH for BMW & Porsche; MEDIUM for Ferrari; LOW for Acura/Cadillac
(missing aero map calibration).

Compliance physics (1/k features) and V²-RMS aero reference speed are correct.
`solution_from_explicit_offsets` honors caller-provided static (April 7 fix). DF
balance correction is now damped (0.6 gain). One open gap: the rear-pushrod 5 mm
trailing error on newest Porsche/Algarve IBT — ARCH/P6.3 — would be closed by adding
a `current_rear_rh_dynamic_mm` anchor in the rake solver's rear-balance search.

### Step 2 — Heave / Third springs

**Accuracy:** HIGH for BMW & Porsche.

σ-calibration (`min_rate_for_sigma`) with sticky pre-check and cal_ratio remains the
strongest physics implementation in the repo. Open gap: `cal_ratio` is assumed
constant across rates — documented in code, but unverified for soft setups where damper
feedback is large.

### Step 3 — Corner springs

**Accuracy:** MEDIUM for BMW/Porsche.

Rear coil driver-anchor is "copy driver-loaded if within tolerance". This is by design
to preserve known-good setups, but it suppresses solver insight. Re-entrancy hazard
flagged in F-15 (Porsche csm mutation).

### Step 4 — ARBs

**Accuracy:** LOW (no true LLTD measurement).

OptimumG physics target stands; driver-anchor fallback fires for Porsche. The dead
recommend branch (F-1) and the unused front anchor (F-10) make this the most
operationally weak step.

### Step 5 — Geometry

**Accuracy:** MEDIUM.

Roll gains are still hand-tuned defaults; auto-calibrate informational only. No test
compares predicted-vs-measured tire temps after camber changes.

### Step 6 — Dampers

**Accuracy:** MEDIUM-HIGH for Porsche (88-session click sweep), MEDIUM for BMW.

`zeta_is_calibrated` strict-raise (F-7-adjacent: `damper_solver.py:476-483`) is
correct gate behavior. `_estimate_lap_gain` BMW-flavored default damper params
(F-7) are the new finding here.

---

## Section 4 — Prioritized enhancement plan

### Priority 1 — Correctness leaks (small surface, high impact)

| # | File | Change | Risk |
|---|------|--------|------|
| P1.1 | `analyzer/recommend.py:415-470` | Match `cause` strings to "front-heavy" / "rear-heavy"; add diagnose↔recommend contract test | Trivial |
| P1.2 | `learner/empirical_models.py:780-823` | Add `lltd_is_proxy=True` tag; decode index→N/mm before m_eff math; reject low-range LLTD regressions | Low (touch one file + test) |
| P1.3 | `learner/empirical_models.py:261-287` | Apply R²≥0.85 + min-range guard to `_fit_lltd_vs_arb` (or remove) | Trivial |
| P1.4 | `solver/objective.py:1386-1393` | Read damper defaults from `car.damper.*_range` midpoints, not hardcoded BMW clicks | Low |
| P1.5 | `solver/sensitivity.py:198-213` | Make σ targets keyword-only required parameters; thread from `solve_inputs` everywhere | Medium (find all callers) |
| P1.6 | `solver/solve_chain.py:_iterative_coupling_refinement` | Either include σ in convergence check or remove `sigma_tol_mm` parameter | Low |

### Priority 2 — Calibration integrity hardening

| # | Target | Change |
|---|--------|--------|
| P2.1 | `car_model/cars.py:609` | `DeflectionModel.is_calibrated` default → `False`, fix all explicit instantiations |
| P2.2 | `car_model/auto_calibrate.py:621` | Remove `_get_dummy_car` BMW fallback; raise on empty `car_name` |
| P2.3 | `learner/knowledge_store.py` | Add Windows file-locking shim (msvcrt.locking or portalocker package) |
| P2.4 | `learner/{ingest,recall,report_section}.py` and `teamdb/aggregator.py` | Replace `track.split()[0]` with `track_key_from_name()`; add regression test for collisions |
| P2.5 | `car_model/setup_registry.py:620-639` | `detect_car_adapter` raises on unknown YAML instead of returning "bmw" |

### Priority 3 — Workflow architecture / decoupling

| # | Target | Change |
|---|--------|--------|
| P3.1 | `pipeline/produce.py` ↔ `pipeline/reason.py` | Extract `OrchestrationContext` shared module; refactor both to call into it; target both files <800 lines |
| P3.2 | `solver/objective.py` | Split into `objective_core.py` (formula primitives) + `objective_evaluate.py` (`evaluate`/`evaluate_physics`) + `objective_lap_gain.py` (`_estimate_lap_gain`) |
| P3.3 | `solver/arb_solver.py:255-264` | Implement symmetric front-ARB driver anchor or remove unused parameters |
| P3.4 | `solver/bayesian_optimizer.py` | Replace `_score()` with real `ObjectiveFunction.evaluate_physics`; gate behind calibration status |
| P3.5 | `apply_to_car`, `ObjectiveFunction.evaluate` | Make pure (return new instance / accept context as parameter) — eliminates re-entrancy |

### Priority 4 — Per-car correctness

| # | Target | Change |
|---|--------|--------|
| P4.1 | `solver/candidate_search.py:26-27, 114, 986` | Per-car torsion option lists; raise on missing field; remove BMW fallback canonical_name |
| P4.2 | `output/setup_writer.py:892` (TODO stubs for non-BMW corner spring) | Replace stubs with proper implementation or raise; track in issue |
| P4.3 | `car_model/cars.py:2851` | Calibrate Porsche damper coefficients (DSSV click sweep) — the "BMW DEFAULT" comment is technical debt |
| P4.4 | All `car=bmw` heuristic fallbacks (`pipeline/report.py:302`, `webapp/services.py:442`, etc.) | Raise / log warning instead |

### Priority 5 — Testing coverage

| # | New test |
|---|----------|
| P5.1 | `tests/test_diagnose_recommend_contract.py` — every diagnose symptom routes to ≥1 recommend handler |
| P5.2 | `tests/test_track_key_collisions.py` — different full names with same first word |
| P5.3 | `tests/test_indexed_car_m_eff.py` — Ferrari/Acura indexed values produce sane m_eff |
| P5.4 | `tests/test_orchestration_parity.py` — `produce` and `reason` produce identical setups for the same inputs |
| P5.5 | `tests/test_bayesian_objective_match.py` — Bayesian optimizer scores correlate with `ObjectiveFunction.evaluate_physics` (post-P3.4) |
| P5.6 | `tests/test_sensitivity_per_car.py` — σ target windows differ between BMW and Porsche scenarios |

### Priority 6 — Documentation & cleanup

- Update `solver/objective.py:1323-1327` LLTD comment to reflect proxy reality.
- Remove the "ZEROED OUT, do NOT restore" commented block (`objective.py:1505+`); replace with stub function.
- Remove stale `t_avg_m` comment in `wheel_geometry_solver.py:183-188`.
- Migrate "BMW-only" comment fallbacks to `# CAR-SPECIFIC: needs calibration` and link to issue.
- Add ticket references to all `pytest.skip`/`SkipTest` calls.

---

## Section 5 — "Bad things" inventory (concise)

1. **The `lltd_measured` lie.** A geometric ratio is named like a measurement and threaded
   through analyzer → learner → objective. Until iRacing exposes wheel-load channels,
   keep the proxy quarantined and tagged.
2. **`pipeline/reason.py` monolith.** 4,083 lines, single 1,100-line function. Every fix to
   the orchestration pipeline has to be made twice (here and `produce.py`).
3. **`solver/objective.py` BMW-flavored hot path.** 2,132 lines with hardcoded constants
   (damper midpoints 7/6/5, BMW Sebring `INPUT_UNCERTAINTIES`, Spearman comments referring
   only to BMW data) — this is the file that determines whether non-BMW cars get correct
   scoring.
4. **Silent `except Exception: pass` in primary-path orchestration code.** `solver/solve.py:257-258`,
   `pipeline/produce.py:343-344`, `pipeline/reason.py:2852-2853`, `pipeline/optimize.py:120-121`,
   `output/run_trace.py:104,204,273`. These hide the exact failures the calibration system
   is designed to surface.
5. **`car_model/auto_calibrate.py` size (3,420 lines).** Mixes ingestion, fit, apply,
   CLI, and per-car branching. High regression risk per change.
6. **Dead/half-dead code.** Recommend LLTD branch (F-1), `_fit_lltd_vs_arb` (F-5), unused
   front-ARB anchor (F-10), `sigma_tol_mm` parameter (F-11), `t_avg_m` comment (F-17),
   ARB extreme-penalty zeroed block (F-20).
7. **Unfinished migrations.** Track-key `split()[0]` (F-12), `getattr(car,…)` BMW
   fallbacks (LOW-5 / F-16), per-axle roll damper flags (still has legacy
   `has_roll_dampers` for backward-compat).
8. **Re-entrancy hazards.** `apply_to_car` mutates car (F-15); `ObjectiveFunction.evaluate`
   stashes session context on `self`. Both are safe today (single-threaded CLI) but
   block parallel candidate evaluation.
9. **Test ergonomics.** No diagnose↔recommend contract test, no track-key collision test,
   no Ferrari/Acura indexed-car m_eff test. The bugs in F-1, F-3, F-12 would all have
   been caught by 50 lines of tests.
10. **Documentation drift.** Comments dated April 2026 in `objective.py`, `cars.py`, etc.
    describe state that has since changed (LLTD interpretation, R² thresholds, learner
    semantics).

---

## Section 6 — Numbers

| Metric | Value |
|--------|-------|
| Files with critical bugs (this audit) | 3 (recommend, learner empirical, sensitivity defaults) |
| Files with HIGH severity issues (this audit) | 9 |
| 2026-04-10 critical bugs still present | 1 (BUG-C4 LLTD branch — patched but still dead) |
| 2026-04-10 critical bugs FIXED | 3 |
| 2026-04-10 calibration findings still partial | 4 (CAL-H1, MED-2, MED-3, LOW-8) |
| `getattr(car,…)` calls under `solver/` | 31 (was ~700 originally; concentrated in `candidate_search.py` & `bmw_rotation_search.py`) |
| `except Exception: pass` count | ~12 in primary-path files (solve, produce, reason, optimize, run_trace, ingest) |
| Files >1,000 lines | 9 (`reason.py` 4083, `auto_calibrate.py` 3420, `cars.py` 3134, `objective.py` 2132, `produce.py` 2045, `extract.py` 1971, `solve_chain.py` 1500, `setup_writer.py` 1235, `candidate_search.py` 1226) |
| Functions >100 lines | `reason_and_solve` (~1100), `_estimate_lap_gain` (~315), `evaluate`/`evaluate_physics` (large), `auto_calibrate.main` (long) |
| TODO / FIXME / HACK / XXX in `.py` | 2 actionable (`output/setup_writer.py:892`, `car_model/cars.py:2851`) |
| Test files | 50 |

---

## Section 7 — Single-line summary per priority bucket

- **Priority 1** (correctness leaks): F-1, F-2, F-3, F-7, F-8, F-11 — all <1-day fixes,
  several user-visible.
- **Priority 2** (integrity hardening): F-13, F-14, MED-2, MED-3, LOW-6 — defense in depth.
- **Priority 3** (architecture): F-9 (orchestration dedup), F-6 (Bayesian), F-15
  (re-entrancy), `objective.py` split, `arb_solver` symmetry — multi-PR effort.
- **Priority 4** (per-car correctness): F-16, P4.2, P4.3 — needed before promoting Acura
  /Cadillac/Ferrari beyond exploratory.
- **Priority 5** (tests): six contract tests above; pre-empts regressions.
- **Priority 6** (docs): cosmetic but trust-affecting.

---

*Audit performed 2026-04-26 on `main` after merging PR #94. Methodology: three parallel
read-only static analyses (workflow, calibration, code quality) cross-referenced against
`docs/audit_2026_04_10.md` and live code. Subagent IDs preserved for follow-up:
8e24f5c3 (workflow), e8665d20 (calibration), 95bbf656 (code quality).*
