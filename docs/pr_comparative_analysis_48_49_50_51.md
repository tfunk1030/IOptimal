# Comparative Analysis: PRs #48, #49, #50, #51

**Date:** 2026-04-10  
**Branches audited:** `cursor/codebase-audit-2026-04-10-40db` (#48), `cursor/audit-fixes-c82e` (#49), `claude/audit-codebase-enhancements-rPV45` (#50), `cursor/full-audit-hardening-5b25` (#51)  
**Base:** `codextwo` @ `73c84a5`  
**Common ancestor (all PRs):** `2398fad`

---

## 1. Executive Summary

All four PRs are audit-driven enhancement branches spawned from the same `codextwo` base within a narrow time window (04:07–05:20 UTC, 2026-04-10). They share a common purpose — harden the calibration gate, fix silent exception handlers, clean up LLTD proxy contamination, and improve the pipeline — but each was authored by a different agent session and they have significant overlap. **10 files are touched by 2+ PRs**, including `pipeline/produce.py` and `car_model/calibration_gate.py` which are touched by **all four**.

The good news: the four PRs collectively contain approximately 35–40 distinct improvements, none of which are architecturally wrong. The bad news: merging all four as-is is impossible without conflict resolution across at least 10 files, and several changes are duplicated (same bug fixed in different ways) or partially contradictory (different approaches to the same problem).

---

## 2. Per-PR Deep Analysis

### PR #48 — `cursor/codebase-audit-2026-04-10-40db`
**Title:** "audit(2026-04-10): deep codebase audit + critical bug fixes"  
**Stats:** +1001 / -107, 24 files, 2 commits  
**Author agent:** Cursor Agent

**What it does (27 distinct changes):**

| # | Change | File | Severity | Physics Correct? |
|---|--------|------|----------|-----------------|
| 1 | Fix `driver_profile` NameError in grid-search | `pipeline/produce.py:1086` | **CRITICAL** | N/A (crash fix) |
| 2 | Fix dead thermal recommendation paths ("inner hot" → "inside too hot") | `analyzer/recommend.py` | **CRITICAL** | ✅ |
| 3 | Fix dead LLTD recommendation path ("lltd" → "roll distribution proxy") | `analyzer/recommend.py` | **HIGH** | ✅ |
| 4 | `_fit()` R² floor check (`is_calibrated=False` when R² < 0.85) | `car_model/auto_calibrate.py` | **HIGH** | ✅ |
| 5 | LOO RMSE → NaN when n < 5 (was misleading 0.0) | `car_model/auto_calibrate.py` | MEDIUM | ✅ |
| 6 | `RideHeightModel.is_calibrated` default True→False | `car_model/cars.py` | **HIGH** | ✅ |
| 7 | LLTD proxy contamination guard in `learned_corrections.py` | `solver/learned_corrections.py` | **HIGH** | ✅ |
| 8 | Roll gain "calibration" → `roll_gradient_stable` only | `car_model/auto_calibrate.py` | **HIGH** | ✅ |
| 9 | Ferrari m_eff plausibility gate (reject raw spring index) | `car_model/garage_model.py` | **HIGH** | ✅ |
| 10 | Step 6 provisional solve removed from `materialize_overrides` | `solver/solve_chain.py` | **HIGH** | ✅ |
| 11 | Modifier safety floors → car-relative multiples | `solver/modifiers.py` | **HIGH** | ✅ |
| 12 | Perch formula corrected (0.08 → calibrated RideHeightModel sensitivity) | `solver/legal_space.py` | **HIGH** | ✅ |
| 13 | DF balance correction damped (0.6 factor) | `solver/solve_chain.py` | MEDIUM | ✅ |
| 14 | `calibration_gate.py` docstring fixed (weak does NOT block) | `car_model/calibration_gate.py` | LOW | ✅ |
| 15 | Lap selection consistency (pass indices to `analyze_driver`) | `analyzer/driver_style.py`, `pipeline/produce.py`, `learner/ingest.py` | MEDIUM | N/A |
| 16 | Track key slug (collision fix) + `track_key_from_name()` | `learner/knowledge_store.py`, `learner/ingest.py`, `learner/recall.py`, `learner/report_section.py` | MEDIUM | N/A |
| 17 | Observation timestamp from IBT mtime, not ingest time | `learner/observation.py` | MEDIUM | N/A |
| 18 | `sensitivity.py` targets wired from caller (not hardcoded BMW) | `solver/sensitivity.py` | MEDIUM | ✅ |
| 19 | `PushrodGeometry.is_calibrated` flag + per-car gate | `car_model/cars.py`, `car_model/calibration_gate.py` | MEDIUM | ✅ |
| 20 | `setup_registry` car default `"bmw"` → `None` + ValueError | `car_model/setup_registry.py` | MEDIUM | N/A |
| 21 | ARB front anchor documented as unused | `solver/arb_solver.py` | LOW | N/A |
| 22 | Bayesian optimizer proxy objective documented | `solver/bayesian_optimizer.py` | LOW | N/A |
| 23 | Unused `t_avg_m` removed | `solver/wheel_geometry_solver.py` | LOW | N/A |
| 24 | Knowledge store file locking (`fcntl.flock`) | `learner/knowledge_store.py` | MEDIUM | N/A |
| 25 | Porsche legality `rear_heave_max` 1000→800 | `car_model/legality.py` | MEDIUM | ✅ |
| 26 | `detect_car_adapter` documented as structural heuristic | `car_model/setup_registry.py` | LOW | N/A |
| 27 | Full audit document `docs/audit_2026_04_10.md` (573 lines) | docs | N/A | N/A |

**Assessment:** This is the **highest-value, most comprehensive PR**. It contains nearly all the critical bug fixes (grid-search crash, dead recommendation paths), high-impact calibration integrity guards (R² floor, LLTD proxy guard, roll gain rename, Ferrari m_eff gate), and the most important workflow architecture fixes (provisional Step 6 removal, car-relative modifier floors, perch formula fix). The audit document is thorough. The changes are physics-correct.

**Weaknesses:** Large diff makes review harder. Some changes (track key slug, file locking) are orthogonal to the audit mission and inflate scope.

---

### PR #49 — `cursor/audit-fixes-c82e`
**Title:** "Fix calibration authority and LLTD provenance"  
**Stats:** +312 / -229, 13 files, 2 commits  
**Author agent:** Cursor Agent (different session)

**What it does (10 distinct changes):**

| # | Change | File | Severity | Physics Correct? |
|---|--------|------|----------|-----------------|
| 1 | Track-aware calibration authority (`supports_track()`, `supported_track_keys`) | `car_model/cars.py`, `car_model/calibration_gate.py` | **HIGH** | ✅ |
| 2 | `track_support` added to Step 1 requirements | `car_model/calibration_gate.py` | **HIGH** | ✅ |
| 3 | `lltd_target_source` provenance field on CarModel | `car_model/cars.py` | MEDIUM | ✅ |
| 4 | LLTD target loading blocked when status starts with "DISABLED" | `car_model/auto_calibrate.py` | MEDIUM | ✅ |
| 5 | Deprecated `validation/calibrate_lltd.py` (fail-fast with RuntimeError) | `validation/calibrate_lltd.py` | MEDIUM | ✅ |
| 6 | `should_run_legal_manifold_search()` requires explicit opt-in (scenario alone no longer triggers) | `solver/scenario_profiles.py` | MEDIUM | ✅ |
| 7 | `learner/ingest.py` refactored with `_update_auto_calibration()` helper | `learner/ingest.py` | LOW | N/A |
| 8 | LLTD calibration instructions updated (proxy warning) | `car_model/calibration_gate.py` | LOW | ✅ |
| 9 | `solver.solve` emits calibration confidence report | `solver/solve.py` | LOW | N/A |
| 10 | Legal-search skipped cleanly when calibration blocks steps | `pipeline/produce.py`, `pipeline/reason.py`, `pipeline/report.py` | MEDIUM | N/A |

**Assessment:** This PR has a **unique, valuable contribution** that no other PR provides: **track-aware calibration authority**. The `supports_track()` / `supported_track_keys` system prevents Sebring calibration from being silently applied to Algarve runs for a car that was only calibrated at Sebring. This is an architecturally important addition. The LLTD target source tracking (`lltd_target_source`) is also unique to this PR.

The `should_run_legal_manifold_search()` change (requiring explicit opt-in) is a policy choice that prevents scenario selection from implicitly triggering expensive legal search — this is a good safety change.

The `learner/ingest.py` refactoring extracts `_update_auto_calibration()` as a helper and uses it in both `ingest_ibt()` and `ingest_all_laps()`. This is a clean improvement but **drops the LLTD target preservation line** (`if existing_saved.measured_lltd_target is not None and models.measured_lltd_target is None: ...`) — which is correct given the LLTD proxy deprecation.

**Weaknesses:** The `calibrate_lltd.py` deprecation is aggressive (replaces entire function body with a RuntimeError). Some overlap with PR #48's LLTD proxy fixes.

---

### PR #50 — `claude/audit-codebase-enhancements-rPV45`
**Title:** "Codebase audit: 23 fixes across solver accuracy, calibration, and code safety"  
**Stats:** +277 / -76, 19 files, 2 commits  
**Author agent:** Claude (different tool, different session)

**What it does (23 changes):**

| # | Change | File | Severity | Physics Correct? |
|---|--------|------|----------|-----------------|
| 1 | DF balance over-correction removed (coupling refinement) | `solver/solve_chain.py` | **HIGH** | ✅* (see below) |
| 2 | `zeta_is_calibrated` default True→False | `solver/damper_solver.py` | **HIGH** | ✅ |
| 3 | Tyre vertical rate warning for suspension-only excursion | `solver/objective.py` | MEDIUM | ✅ |
| 4 | LLTD offset bounds-check [0.30, 0.75] | `solver/arb_solver.py` | MEDIUM | ✅ |
| 5 | Porsche roll damper backward-compat tightened | `output/setup_writer.py` | MEDIUM | ✅ |
| 6 | Ferrari rear torsion gated as `uncalibrated` (blocks Step 3) | `car_model/calibration_gate.py` | **HIGH** | ✅ |
| 7 | Speed-dependent LLTD gap eliminated (120-180→150 boundary) | `analyzer/extract.py` | MEDIUM | ✅ |
| 8 | 11 `except Exception: pass` → `logger.debug()` in `solve_chain.py` | `solver/solve_chain.py` | MEDIUM | N/A |
| 9 | Auto-calibrate overfit warning (LOO vs training RMSE) | `car_model/auto_calibrate.py` | MEDIUM | ✅ |
| 10 | `confidence_weight` property on `StepCalibrationReport` | `car_model/calibration_gate.py` | MEDIUM | N/A |
| 11 | `step_confidence` in pipeline JSON output | `pipeline/produce.py` | LOW | N/A |
| 12 | Hardcoded Windows paths removed from tests | `tests/test_bmw_sebring_garage_truth.py`, `run_tests.py` | MEDIUM | N/A |
| 13 | Cadillac calibration directory stubs | `data/calibration/cadillac/` | LOW | N/A |
| 14 | Ferrari setup_writer fallback now warns | `output/setup_writer.py` | LOW | N/A |
| 15 | `decision_trace.py` None handling fixed | `solver/decision_trace.py` | MEDIUM | N/A |
| 16 | Underdetermined fit guard (`n <= n_params` → uncalibrated) | `car_model/auto_calibrate.py` | **HIGH** | ✅ |
| 17 | ARB blade range uses `car.arb.rear_blade_count` | `solver/candidate_search.py` | **HIGH** | ✅ |
| 18 | `bmw_coverage._car_name()` default "bmw"→"unknown" | `solver/bmw_coverage.py` | LOW | N/A |
| 19 | 18 additional exception handlers now log | multiple | MEDIUM | N/A |
| 20 | Parallel wheel rate ×0.5 documented | `solver/objective.py` | LOW | ✅ |
| 21 | `_min_sessions_for_features()` scaling | `car_model/auto_calibrate.py` | MEDIUM | ✅ |
| 22 | Calibration load failure no longer silent in `pipeline/produce.py` | `pipeline/produce.py` | MEDIUM | N/A |
| 23 | CLAUDE.md updated with round 2 findings | `CLAUDE.md` | LOW | N/A |

**Assessment:** This is the **second-highest-value PR**. Its unique contributions include:
- **`zeta_is_calibrated` default fix** (True→False) — prevents uncalibrated damper solvers from running when the gate is bypassed
- **Underdetermined fit guard** — prevents n ≤ n_params regressions from producing fake R²=1.0
- **ARB blade range fix** — uses `car.arb.rear_blade_count` (Porsche 1-16 vs BMW 1-5)
- **Ferrari rear torsion gated as `uncalibrated`** (stronger than PR #48's `weak`)
- **Decision trace None handling** — replaces fragile `or 0.0` patterns
- **Speed-dependent LLTD gap fix** (120-180→150 boundary)

**Physics note on change #1:** PR #50 handles the DF balance correction differently than PR #48. PR #48 adds a 0.6 damping factor to the correction (`corrected_target = inputs.target_balance + 0.6 * correction`). PR #50 removes the correction entirely and just re-passes `inputs.target_balance`. The PR #50 approach is technically simpler but loses the iterative convergence mechanism. **PR #48's damped correction is the better physics approach** — it still converges but with less overshoot risk. PR #50's approach relies entirely on the rake solver finding the right answer with the original target after springs have changed, which may not converge in nonlinear aero map regions.

**Weaknesses:** The DF balance correction removal (vs PR #48's damping approach) is arguably worse physics. CLAUDE.md update is cosmetic.

---

### PR #51 — `cursor/full-audit-hardening-5b25`
**Title:** "Harden calibration gating and fix legal-search track/session bugs"  
**Stats:** +388 / -41, 9 files, 1 commit  
**Author agent:** Cursor Agent (different session)

**What it does (8 distinct changes):**

| # | Change | File | Severity | Physics Correct? |
|---|--------|------|----------|-----------------|
| 1 | `_apply_calibration_step_blocks()` helper + re-applied after rematerialization | `pipeline/produce.py` | **HIGH** | N/A (defensive) |
| 2 | `weak_upstream_steps` property on `CalibrationReport` | `car_model/calibration_gate.py` | MEDIUM | N/A |
| 3 | Weak-upstream section in `format_header()` | `car_model/calibration_gate.py` | LOW | N/A |
| 4 | `R2_THRESHOLD_WARN` now used for RH/deflection warning-tier | `car_model/calibration_gate.py` | MEDIUM | ✅ |
| 5 | `_resolve_track_inputs()` helper in `solver/legal_search.py` | `solver/legal_search.py` | MEDIUM | N/A |
| 6 | `solver.solve` JSON output includes weak+upstream+provenance | `solver/solve.py` | MEDIUM | N/A |
| 7 | Grid-search `driver_profile` → `driver` fix + track context fix | `pipeline/produce.py` | **CRITICAL** (partially) | N/A |
| 8 | Calibration loading warns instead of silently swallowing | `pipeline/produce.py` | MEDIUM | N/A |

**Assessment:** The **unique high-value contribution** here is the `_apply_calibration_step_blocks()` helper and its systematic re-application after every rematerialization path (stint compromise, candidate selection, grid search, legal search). This is a genuine gap — the base code nulls blocked steps once at the initial solve, but subsequent rematerialization paths (stint, candidate, grid, legal) can overwrite those nulled steps with fresh (but still uncalibrated) values. PR #51 closes this gap systematically.

The `_resolve_track_inputs()` helper for `legal_search.py` consolidates the repeated `getattr(track, "name", "")` pattern into a clean function — minor but correct.

**However:** Change #7 (grid-search `driver_profile` fix) is **also fixed by PR #48** (same 1-line fix). The grid-search track context fix (`hasattr(track, "name")` → direct `track` pass) is unique to #51 though.

**Weaknesses:** Much of this PR is defensive infrastructure (helpers, JSON fields) rather than physics fixes. The test files (`test_produce_calibration_gate.py`, `test_solver_solve_calibration_json.py`) duplicate the helper function instead of importing it from the module, which is fragile. The new tests add scipy stub blocks that are boilerplate-heavy.

---

## 3. Ranked Ordering (Best to Worst)

| Rank | PR | Impact | Quality | Unique Value |
|------|-----|--------|---------|-------------|
| **1** | **#48** | **Highest** — fixes 4 critical/high bugs, 12 high-impact changes | **Best** — physics-correct, comprehensive audit doc | Dead recommendation paths, m_eff gate, perch formula, modifier floors, file locking, track key slug, observation timestamps, sensitivity targets |
| **2** | **#50** | **High** — 7 high-impact changes including 3 unique ones | **Good** — mostly correct, one debatable physics choice | zeta default fix, underdetermined fit guard, ARB blade range, LLTD speed gap, decision_trace None fix, Cadillac stubs, Windows path cleanup |
| **3** | **#49** | **Medium-High** — track-aware calibration is architecturally important | **Good** — clean implementation, focused scope | Track support system, LLTD target source provenance, legal search explicit opt-in, calibrate_lltd deprecation |
| **4** | **#51** | **Medium** — defensive hardening, not physics | **Adequate** — correct but low density of high-value changes | Calibration step block re-application, _resolve_track_inputs, R2_THRESHOLD_WARN usage |

---

## 4. Dependency Graph

```
PR #48  ──┐
           │  (no hard dependencies between PRs — all branch from same base)
PR #49  ──┤
           │  But MERGE ORDER matters because of file conflicts:
PR #50  ──┤
           │
PR #51  ──┘

Recommended merge order:
  1. PR #48 (largest, most critical fixes, establishes the baseline)
  2. PR #50 (unique high-value fixes that complement #48)
  3. PR #49 (track-aware calibration — unique feature, fewer conflicts after #48+#50)
  4. PR #51 (defensive hardening — most changes are subsumed by #48+#50+#49)
```

None of the PRs have hard dependencies on each other. They all branch from the same `codextwo` base (`2398fad`). However, merge order matters because of file conflicts.

---

## 5. Conflicts and Overlaps

### 5.1 Files Touched by All 4 PRs (MUST be reconciled)

| File | PR #48 | PR #49 | PR #50 | PR #51 |
|------|--------|--------|--------|--------|
| `car_model/calibration_gate.py` | Docstring fix, pushrod gate | Track support, LLTD source, docstring fix | Ferrari uncalibrated, confidence_weight, step_confidence | weak_upstream, R2_THRESHOLD_WARN usage, docstring fix |
| `pipeline/produce.py` | `driver_profile`→`driver`, lap selection | Legal search skip, calibration fail warning | Calibration fail warning, step_confidence JSON | `_apply_calibration_step_blocks`, grid-search track fix, weak-upstream JSON |

### 5.2 Direct Contradictions

| Area | PR #48 | PR #50 | Resolution |
|------|--------|--------|-----------|
| DF balance correction in `solve_chain.py` | Damped by 0.6 factor: `corrected_target = inputs.target_balance + 0.6 * correction` | Removed entirely: just re-passes `inputs.target_balance` | **Use PR #48's approach** — damped correction is better physics (converges in nonlinear regions without overshoot) |
| Ferrari rear torsion status | Gate classifies as `"weak"` (still runs) | Gate classifies as `"uncalibrated"` (blocks Step 3) | **Use PR #50's approach** — with a potential 3.5x rate error, blocking is safer than running with bad values |
| `calibration_gate.py` docstring | "Step still runs but output carries [~~] flag" | Not modified | Same intent — not contradictory |
| `auto_calibrate._fit()` R² check | Checks `r2 >= R2_THRESHOLD_BLOCK` at write time | Does not add write-time check (adds underdetermined guard instead) | **Merge both** — the R² write-time check and underdetermined guard are complementary |

### 5.3 Duplicated Changes

| Change | PRs | Resolution |
|--------|-----|-----------|
| `driver_profile` → `driver` in grid-search | #48, #51 | Take from #48 (first merger) |
| Calibration load failure warning | #50, #51 | Slight differences: #50 uses `logging.getLogger()`, #51 uses `log()` helper. Take #51 (uses pipeline's own logging) |
| `calibration_gate.py` docstring (weak does not block) | #48, #49, #50, #51 | All say the same thing with different wording. Take #48's version (most precise) |
| `solver.solve` calibration provenance JSON | #49, #50, #51 | All add provenance fields. #51 is most complete (includes weak_upstream_by_step). Use #51's version |
| `test_calibration_semantics.py` Ferrari step 6 fix | #49, #51 | Same fix (step 6 blocked on own damper_zeta, not dependency). Use either |
| LOO RMSE improvement | #48 (NaN when n<5) | Only in #48, but #50's overfit warning complements it |

### 5.4 Non-Conflicting Unique Changes (safe to cherry-pick)

| PR | Unique Changes |
|----|---------------|
| #48 | recommend.py dead path fixes, learned_corrections LLTD guard, garage_model m_eff gate, modifiers car-relative floors, legal_space perch formula, sensitivity.py targets, knowledge_store file locking, track_key_from_name slug, observation timestamps, setup_registry car=None guard, arb/bayesian docs, wheel_geometry unused var, legality.py Porsche range, audit document |
| #49 | supports_track() / supported_track_keys, lltd_target_source field, calibrate_lltd deprecation, scenario_profiles explicit search opt-in, _update_auto_calibration helper, LLTD target DISABLED loading guard, reason.py legal-search skip |
| #50 | zeta_is_calibrated default, underdetermined fit guard, ARB blade range fix, LLTD speed gap fix, decision_trace None handling, Cadillac stubs, Windows path cleanup, setup_writer roll damper tightening, _min_sessions_for_features, CLAUDE.md update, 18 exception handler logging upgrades |
| #51 | _apply_calibration_step_blocks helper + 5 re-application sites, _resolve_track_inputs, R2_THRESHOLD_WARN in RH/deflection, weak_upstream_steps property, new test files |

---

## 6. Changes That Are Wrong or Should Be Reverted

| PR | Change | Issue | Recommendation |
|----|--------|-------|----------------|
| **#50** | DF balance correction removal (`solve_chain.py:832-837`) | Removes iterative convergence mechanism entirely. In nonlinear aero maps (vortex burst region), the rake solver may not converge to target balance without the correction. | **Revert** — use PR #48's 0.6 damping factor instead |
| **#50** | `CLAUDE.md` update | Cosmetic changelog update that will conflict with any other CLAUDE.md edit. Low value, high merge-conflict risk. | **Skip** — CLAUDE.md should be updated once after all merges |
| **#51** | `test_produce_calibration_gate.py` duplicates helper | The test redefines `_apply_calibration_step_blocks` instead of importing it. If the real function changes, this test won't catch regressions. | **Fix** — import from `pipeline.produce` instead of duplicating |
| **#51** | Heavy scipy stubs in test files | 30+ lines of scipy mock setup per test file. Brittle and will need updating whenever new scipy imports are added. | **Acceptable** for now but flagged as tech debt |

---

## 7. Merge Strategy

### Recommended Approach: **Cherry-pick merge, not squash-all**

Squash-merging all 4 PRs would lose the commit history and make it impossible to bisect regressions. Instead:

### Phase 1: Merge PR #48 (highest value, widest scope)

```bash
git checkout codextwo
git merge cursor/codebase-audit-2026-04-10-40db
```

This establishes the baseline with all critical bug fixes.

### Phase 2: Cherry-pick PR #50's unique changes (skip DF balance removal)

From `claude/audit-codebase-enhancements-rPV45`, cherry-pick:
- `solver/damper_solver.py` (zeta_is_calibrated default)
- `car_model/auto_calibrate.py` (underdetermined fit guard + _min_sessions_for_features + overfit warning + exception logging)
- `solver/candidate_search.py` (ARB blade range)
- `analyzer/extract.py` (LLTD speed gap 120-180→150)
- `solver/decision_trace.py` (None handling)
- `output/setup_writer.py` (roll damper tightening + Ferrari fallback warning)
- `data/calibration/cadillac/` (stubs)
- `tests/test_bmw_sebring_garage_truth.py` + `run_tests.py` (Windows path cleanup)
- `solver/objective.py` (tyre vertical rate warning + exception logging + parallel wheel rate doc)
- `solver/rake_solver.py` (exception logging)
- `solver/supporting_solver.py` (exception logging)
- `solver/bmw_coverage.py` (_car_name default)
- `car_model/calibration_gate.py` → Ferrari rear torsion `uncalibrated` (upgrade from #48's `weak`)
- `car_model/calibration_gate.py` → `confidence_weight` property + `step_confidence`

**Skip:** `solve_chain.py` DF balance change (use #48's damped version instead), `CLAUDE.md` update

### Phase 3: Merge PR #49's unique features

From `cursor/audit-fixes-c82e`:
- `car_model/cars.py` → `supported_track_keys`, `lltd_target_source`, `supports_track()`, `supported_tracks_label()`
- `car_model/calibration_gate.py` → `track_support` subsystem + Step 1 requirement
- `car_model/registry.py` → `track_key()` function
- `car_model/auto_calibrate.py` → LLTD target DISABLED loading guard + preserve removal
- `validation/calibrate_lltd.py` → deprecation (RuntimeError)
- `solver/scenario_profiles.py` → explicit search opt-in
- `learner/ingest.py` → `_update_auto_calibration` helper (reconcile with #48's lap selection changes)
- `pipeline/reason.py` → legal search skip
- `pipeline/report.py` → dynamic step reporting
- `docs/system_architecture.md` → dependency cascade docs

### Phase 4: Cherry-pick PR #51's unique defensive changes

From `cursor/full-audit-hardening-5b25`:
- `pipeline/produce.py` → `_apply_calibration_step_blocks()` + 5 re-application sites
- `solver/legal_search.py` → `_resolve_track_inputs()`
- `car_model/calibration_gate.py` → `weak_upstream_steps` property, R2_THRESHOLD_WARN in RH/deflection
- `solver/solve.py` → weak calibration banner + weak_upstream JSON fields
- Tests: `test_produce_calibration_gate.py`, `test_solver_solve_calibration_json.py` (fix import issue), `test_produce_errors.py` additions, `test_legal_search_scenarios.py` additions

### Phase 5: Final reconciliation

- Update `CLAUDE.md` once with all merged changes
- Run full test suite
- Regenerate regression baselines if needed

---

## 8. Final Recommendation

**Merge all four, in order, with targeted conflict resolution and one revert:**

1. **PR #48: Merge as-is.** Highest value, broadest scope, all changes are correct.
2. **PR #50: Merge with revert of the DF balance correction removal.** Use PR #48's 0.6 damping instead. Also upgrade Ferrari rear torsion from #48's `weak` to #50's `uncalibrated` in the reconciliation.
3. **PR #49: Merge as-is.** Track-aware calibration is a unique, architecturally important feature.
4. **PR #51: Merge as-is, then fix the duplicated helper in the test file.** The `_apply_calibration_step_blocks` re-application pattern is genuinely important for correctness.

**No PR should be skipped entirely.** Each contains unique improvements:
- #48: Critical crash/dead-path fixes + physics corrections
- #49: Track-aware calibration authority (prevents cross-track calibration contamination)
- #50: zeta default, underdetermined fit guard, ARB blade range, exception logging
- #51: Calibration block re-application after rematerialization

**The merge will require conflict resolution in 10 files**, primarily `calibration_gate.py` and `pipeline/produce.py`. The conflicts are all additive (different features added to the same file) rather than contradictory (except the DF balance correction, which should use PR #48's approach).

---

## Appendix: Cross-PR Change Coverage Matrix

| Subsystem | PR #48 | PR #49 | PR #50 | PR #51 |
|-----------|--------|--------|--------|--------|
| Critical crash fixes | ✅ `driver_profile`, dead paths | | | ✅ `driver_profile` (dup) |
| LLTD proxy contamination | ✅ learner guard | ✅ target source tracking | | |
| Calibration gate integrity | ✅ docstring, pushrod | ✅ track support | ✅ Ferrari uncal, confidence_weight | ✅ weak_upstream, R2_WARN |
| auto_calibrate hardening | ✅ R² floor, roll gradient | ✅ DISABLED guard | ✅ underdet guard, overfit warn | |
| solve_chain.py | ✅ provisional Step6, DF damp | | ✅ DF removal (WRONG), logging | |
| Exception logging | | | ✅ 29 handlers | |
| Pipeline resilience | ✅ lap selection | ✅ legal search skip | ✅ calibration load warn | ✅ block re-application |
| cars.py model changes | ✅ RHModel default, pushrod | ✅ track keys, LLTD source | | |
| Learner improvements | ✅ track slug, timestamps, locking | ✅ ingest refactor | | |
| Tests | | ✅ calibration semantics | ✅ garage truth paths | ✅ 3 new test files |
| Physics corrections | ✅ modifiers, perch, sensitivity | | ✅ zeta, ARB range, LLTD gap | |
