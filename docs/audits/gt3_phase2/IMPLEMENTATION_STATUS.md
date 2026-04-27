# GT3 Phase 2 — Implementation Status

**Last updated:** 2026-04-27 (Wave 2.3 + Wave 3.1 shipped)
**Branch:** `claude/merge-audits-wave1-DDFyg` (mirrors `gt3-phase0-foundations` + 12 audit merges + 3 implementation commits)
**Plan source of truth:** [`SYNTHESIS.md`](SYNTHESIS.md) — 22 work units across 10 waves, ~511 h estimated.

This doc tracks which units have shipped, what was deferred, and the recommended next batch. It is updated after every work-unit batch lands. Each merged PR / batch commit is referenced by SHA + message so the diff can be inspected directly.

## Top-level progress

| Wave | Title | Units | Effort | Status |
|---|---|---|---|---|
| 1 | Foundation invariants | 3 | ~20 h | **DONE 2026-04-27** |
| 2 | Solver chain unblocks | 4 | ~76 h | W2.1 + W2.2 + W2.3 done; W2.4 remains (~24 h) |
| 3 | Solver chain crash fixes | 3 | ~30 h | W3.1 done; W3.2 + W3.3 remain (~22 h) |
| 4 | Output + writer | 3 | ~70 h | TODO |
| 5 | Pipeline + analyzer | 3 | ~62 h | TODO |
| 6 | Learner + scoring | 3 | ~56 h | TODO |
| 7 | Auto-calibrate + GarageOutputModel | 2 | ~80 h | TODO |
| 8 | Infra + DB + automation | 2 | ~43 h | TODO |
| 9 | UI + CLI + tests + docs | 2 | ~62 h | TODO |
| 10 | E2E smoke + remaining cars | 1 | ~80 h+ | TODO (gated on IBT capture) |

**Shipped so far:** 7 of 22 units (~120 h of ~511 h ≈ 23% of total estimated work).
**Remaining critical path:** W2.4 → W4.1 → W4.2 → W7.1 → W7.2 → W9.1 → W9.2 → W10.1 ≈ ~278 h.

## Wave 1 — DONE (2026-04-27)

Commit: `74b9509 feat(gt3): Wave 1 foundations — gate dispatch + step2.present + registry`
+746/-18 across 13 files. 56 new tests. Suite: 489 passed.

### W1.1 — Calibration gate emits `not_applicable` for GT3 Step 2 — DONE

**File:** `car_model/calibration_gate.py`

- F1: `check_step()` early-returns `not_applicable=True` when `step_number == 2 and not car.suspension_arch.has_heave_third`.
- F2: `_data_prior_step` property replaces class-level `_DATA_PRIOR_STEP` for cascade dispatch; GT3 chain is `{3:1, 4:3, 5:4, 6:3}`. `weak_upstream` propagation now ignores `not_applicable` priors.
- F3: `_build_subsystem_status()` filters deflection sub-models by `has_heave_third`; emits a `heave_third_deflection` N/A subsystem on GT3.
- F4–F8: report properties (`solved_steps` excludes N/A, new `not_applicable_steps`), `format_header` "NOT APPLICABLE STEPS" section, `summary_line` distinguishes N/A from blocked, `instructions_text` returns one-liner for N/A steps.

**Deferred (cosmetic, not blocking):**
- F9 (closed-set order list in `format_confidence_report`).
- F10 (`track_support` uncalibrated for every GT3 first-run).
- F11 (no `heave_third_not_applicable` instruction template).

**Tests:** 36 new in `tests/test_suspension_architecture.py` covering end-to-end gate dispatch on GT3 vs GTP cars. `TestGateDispatchStep2NotApplicable`, `TestGateCascadeRulesPerArchitecture`, `TestGateReportPropertiesGT3`, `TestGateGTPRegression`.

### W1.2 — `step2.present` consumers wired — DONE

**Files:** `solver/heave_solver.py`, `solver/params_util.py`, `solver/candidate_search.py`, `solver/decision_trace.py`, `solver/solve_chain.py`, `solver/bmw_rotation_search.py`, `pipeline/produce.py`.

- `HeaveSolver.__init__` now raises `ValueError` when `car.suspension_arch.has_heave_third == False` — defense-in-depth so any future GT3 caller fails loudly.
- `solver_steps_to_params` (PU1, PU2): step2 reads guarded on `getattr(step2, "present", True)`; step3 torsion bar guarded analogously.
- `_extract_target_maps` (CS3): returns `{}` for the step2 key when `s2.present` is False; downstream snap conditions naturally skip absent fields.
- `decision_trace.build_parameter_decisions` / `_legacy_build_parameter_decisions` (DT1, DT2): accept optional `car=` kwarg; heave/third/torsion specs are skipped explicitly when the car has no heave/third or no torsion bar (vs silently dropped via AttributeError catch).
- 3 call sites (`solve_chain.py`, `bmw_rotation_search.py`, `pipeline/produce.py`) now pass `car=` to `build_parameter_decisions`.

**Tests:** 12 new tests covering `solver_steps_to_params`, `_extract_target_maps`, decision_trace skip-list, and the `HeaveSolver` raise guard.

### W1.3 — Registry GT3 entries — DONE

**Files:** `car_model/registry.py`, `car_model/setup_registry.py`, `tests/test_registry.py`, `tests/test_registry_consistency.py`.

- `_CAR_REGISTRY` gains 3 GT3 `CarIdentity` entries (`bmw_m4_gt3`, `aston_martin_vantage_gt3`, `porsche_992_gt3r`). Substring fallback's longest-match rule already prefers the more specific names.
- `_car_name()` substring loop reordered so GT3 canonical names match BEFORE bare GTP names ("bmw_m4_gt3" no longer collapses to "bmw").
- `CAR_FIELD_SPECS` gains 3 empty stub dicts; `get_car_spec("bmw_m4_gt3", ...)` now returns `None` instead of silently returning the BMW spec.
- `detect_car_adapter()` recognises the GT3 `BumpRubberGap` / `CenterFrontSplitterHeight` fingerprint and emits a `logger.warning` on the BMW fallback path until Wave 4 adds per-car GT3 dispatch.
- `tests/test_registry.py:TestSupportedCarNames.test_returns_all_display_names` count assertion bumped 5 → 8 with explicit GT3 membership checks.

**Deferred:**
- BLOCKER 2 / Wave 4 (W4.1, W4.2, W4.3): full per-car GT3 `CarFieldSpec` dicts with real STO param IDs (`BumpRubberGap`, `CenterFrontSplitterHeight`, GT3 ARB, GT3 dampers, etc.).
- DEGRADED 14 per-car GT3 dispatch in `detect_car_adapter` — needs Aston `EpasSetting`, Porsche RR-fuel-cell-position fingerprints; W4.x territory.
- `_car_name(None) → "bmw"` legacy silent default — needs `solver/bmw_rotation_search.py:665` car-plumbing refactor; left with TODO comment.

**Tests:** 8 new `TestGT3RoutingRegression` tests + 3 `test_registry_consistency` tests.

## Wave 2 — partial (2026-04-27)

Commit: `2cbf1e8 feat(gt3): Wave 2 — Step 1 balance-only mode + Step 2 skip dispatch`
+1152/-134 across 12 files. 21 new tests. Suite: 510 passed.

### W2.1 — Step 2 skipped for GT3 in solve_chain — DONE

**Files:** `solver/solve_chain.py`, `solver/solve.py`, `solver/full_setup_optimizer.py`, `solver/heave_solver.py`, `pipeline/reason.py`, `tests/test_bmw_rotation_search.py`, `tests/test_solve_chain_gt3.py` (new).

Architecture-aware Step 2 dispatch wired into 5 orchestrators:

| Site | File:Line | Action on GT3 |
|---|---|---|
| `_run_sequential_solver` | `solve_chain.py:400` | `step2 = HeaveSolution.null(...)`, no HeaveSolver constructed |
| `_run_branching_solver` | `solve_chain.py:593` | Single null-heave candidate |
| `materialize_overrides` rebuild_step23 | `solve_chain.py:1170, 1228, 1343` | 3 sites branch on `heave_solver is None` |
| CLI Step 2 | `solve.py:434` | Same dispatch pattern |
| Pipeline reason | `pipeline/reason.py:3089, 3384` | Sequential + modifier-floor re-solve |
| `BMWSebringOptimizer.__init__` | `full_setup_optimizer.py:113` | `self.heave_solver = None` for GT3; `_evaluate_seed` early-returns when None |

`reconcile_solution` early-returns when `step2.present is False` (defense-in-depth on top of the W1.2 `__init__` raise). Analyzer guards (`_step2_real`) added in `solve.py` for stint/sector/sensitivity/multi_speed/bayesian/space-mapper. `_all_steps_present` honours `step2.present` via `_step_satisfied`. Legal-search `baseline_params` drops `front_heave_spring_nmm` / `rear_third_spring_nmm` axes for GT3.

**Deferred:**
- F4 (reconcile-rear) — that's `rake_solver.py` R-1..R-4 territory, covered by W2.2.
- F5 (materialize_overrides spring kwargs) — partial: rebuild_step23 branches but `corner_solver.solve(front_heave_nmm=step2.front_heave_nmm, ...)` still passes 0.0 for GT3. W2.3 will fix the corner solver.
- F6 (DamperSolver step2 numerics) — `damper_solver.solve(front_heave_nmm=0.0, ...)` is W2.4 territory.
- F19 (legal-search heave axis fallback scoring) — branching solver scoring fallback at `solve_chain.py:730` reads `s2_copy.front_bottoming_margin_mm`; on null both margins are 0.0 → score collapses to `-s4.lltd_error * 500` which the audit flagged as "incidentally fine for GT3".

**Tests:** 7 new in `tests/test_solve_chain_gt3.py` pinning the Step 2 contract end-to-end (`step2.present == False` for GT3, step1 RH propagated; `step2.present == True` for GTP regression; FullSetupOptimizer no longer raises on GT3).

### W2.2 — Rake (Step 1) balance-only mode for GT3 — DONE

**Files:** `aero_model/interpolator.py`, `solver/rake_solver.py`, `solver/objective.py`, `car_model/cars.py`, `tests/test_rake_solver_gt3.py` (new).

- **AeroSurface gains `has_ld: bool`** flag (read from parsed `balance_only` metadata). When False, `_ld_interp` is not constructed, `lift_drag()` returns NaN cleanly instead of raising on the all-NaN GT3 grid, `find_max_ld()` raises an explicit error.
- **RakeSolver dispatches per architecture** at the top of `solve()`. New `_solve_balance_only` path searches both axes for target balance, no L/D objective, no front-pinning, no vortex-burst constraint. Mode discriminator carried in `RakeSolution.mode = "balance_only_search"`. (Took surgical `if not has_heave_third` route over the audit's suggested `RakeSolverMode` enum — see commit message rationale.)
- **`heave_spring=None` guards** via new `_heave_perch_front/rear_baseline` helpers; covers R-1..R-4. `_use_calibrated_rh` gated on `has_heave_third` (calibrated-RH branch skipped for GT3).
- **`reconcile_ride_heights` early-returns for GT3** with `logger.warning`; covers R-9 (non-garage path) and R-10 (garage path) conservatively.
- **NaN-safe L/D**: `_query_aero`, `_find_free_max_ld`, `ld_cost_of_pinning`, RakeSolution serialization, garage-model L/D update at `reconcile` line 1286.
- **`car_model/cars.py:rh_excursion_p99` GT3 fallback**: when `heave_spring is None`, derives excursion from axle-share-of-sprung-mass + 2× corner-spring lower bound. Out of audit scope but discovered during smoke testing — Step 1 calls this before dispatch so without the fix the GT3 path can't reach `_solve_balance_only`.
- **`solver/objective.py`**: `has_ld` guard around `result.ld_ratio = surface.lift_drag(...)`. Default `ld_ratio=3.0` means GT3 candidates carry a constant offset (no signal, no penalty) — preserves GTP scoring exactly.

**Deferred:**
- R-1 fully (GT3-specific RideHeightModel feature schema rework — depends on real GT3 calibration data).
- R-7 / R-8 `vortex_burst_threshold_mm` placeholder cleanup. The `_solve_balance_only` path never reads it as a constraint, so 2.0 is harmless; setting to 0.0 would falsely claim a 0 mm floor; full removal cascades to dataclass + report. Left as-is (placeholder, unused for GT3).
- R-10 garage-model path of `reconcile_ride_heights` — full GT3 GarageOutputModel with new feature schema is Wave-3+ work. The new early-return covers both garage-model and non-garage-model paths conservatively.

**Tests:** 14 new in `tests/test_rake_solver_gt3.py` covering AeroSurface `has_ld` surfacing, GT3 RakeSolver dispatch (mode, NaN L/D, NaN ld_cost, pin_front kwarg ignored, static front not pinned), reconcile early-return, GTP regression (pinned_front + free_optimization unchanged).

## Wave 2.3 + Wave 3.1 — DONE (2026-04-27)

Commit: `c31f3be feat(gt3): Wave 2.3 + Wave 3.1 — Step 3 GT3 front-coil + heave_spring=None guards`
+984/-86 across 7 files. 33 new tests. Suite: 543 passed.

### W2.3 — Step 3 (corner spring) GT3 front-coil branch — DONE

**Files:** `car_model/cars.py`, `solver/corner_spring_solver.py`, `tests/test_corner_spring_solver_gt3.py` (new).

- **`CornerSpringModel` extended** with `front_spring_range_nmm: tuple`, `front_spring_resolution_nmm: float`, `front_baseline_rate_nmm: float`, and `snap_front_rate()` helper. Zero defaults preserve GTP behaviour.
- **GT3 stubs populated** with real ranges: BMW M4 GT3 (190, 340) N/mm step 10 base 220; Aston (180, 320); Porsche 992 (170, 320).
- **`CornerSpringSolution` extended** with `front_coil_rate_nmm` + `front_coil_perch_mm` (zero on GTP).
- **Three-way front-architecture cascade** in `solve()` and `solution_from_explicit_rates()`: GT3 paired-coil arm FIRST (because GT3 satisfies `front_torsion_c == 0.0` and would otherwise enter the legacy roll-spring branch), then GTP roll-spring, then GTP torsion bar.
- **C-1**: heave-ratio clamp gated on `front_heave_nmm > 0`; GT3 path clamps to corner-spring physical range (`csm.front_spring_range_nmm`).
- **C-2**: explicit GT3 elif arm in `solve()` sets front_rate from coil range, `front_torsion_od_mm=0.0`, populates `front_coil_rate_nmm`.
- **C-3/C-4**: rear-rate dispatch on `rear_third_nmm > 0`; GT3 uses `rate_for_freq` directly (frequency-isolation); driver-anchor `/0` guard via `if rear_third_nmm > 0`.
- **C-5**: same GT3 branching in `solution_from_explicit_rates`; `front_coil_rate_nmm` kwarg threaded through.
- **C-6**: `summary()` prints "TOTAL AXLE WHEEL RATE (2 × corner)" for GT3 instead of "TOTAL HEAVE STIFFNESS".
- **C-7**: `_apply_lltd_floor` early-return for non-roll-spring cars; GT3 paired-coils never hit the Porsche-specific helper.
- **C-9**: `solve_candidates` enumerates `front_spring_range_nmm` step for GT3; dedupe key switches to `(front_coil_rate_nmm, rear_spring_rate_nmm)`.

**Deferred:**
- C-8: rear motion ratio = 1.0 for all 3 GT3 stubs (`PENDING_IBT`, data-blocked).
- C-11: Ferrari preload-turns hook → registry/dispatch refactor (cosmetic; defer until 2nd car needs the same shape).
- A-2 assertion in `arb_solver` (Step 4 zero-front-rate guard): W2.4 territory.

**Tests:** 16 new in `tests/test_corner_spring_solver_gt3.py`. GTP regression locked: BMW `solve(180, 160, fuel=50)` → torsion 15.86, rear 100.

### W3.1 — `legal_space` / `modifiers` / `stint_model` `heave_spring=None` guards — DONE

**Files:** `solver/legal_space.py`, `solver/modifiers.py`, `solver/stint_model.py`, `tests/test_legal_modifiers_stint_gt3.py` (new).

- **`legal_space.py`** (L86-95, L141-143, L275-302, L810, L841-845):
  - `_car_spring_refs` returns `(0.0, 0.0, rear_spring_ref)` sentinel for GT3 — callers naturally skip via existing `> 0` checks (LS1).
  - `compute_perch_offsets` early-returns `{}` for GT3 — no perch math runs (LS2).
  - New `_GT3_EXCLUDED_KEYS` frozenset + `_tier_a_keys_for(car)` / `_perch_keys_for(car)` helpers filter heave/third/torsion search axes out of `LegalSpace.from_car()` and `_build_dimension()` for GT3 (LS3, LS4, LS5).
- **`modifiers.py`** (L116-134, L225-226, L288-304, L389-401):
  - `_has_heave_third` locally cached at top of `compute_modifiers`.
  - `_heave_min` and `_perch_baseline` extended: existing fallback fires for `car is None OR car.heave_spring is None` (was just `car is None`) — covers MD2, MD3.
  - All `front_heave_min_floor_nmm` / `front_heave_perch_target_mm` writes gated on `_has_heave_third` — modifier object never carries stale GT3 values (MD4).
- **`stint_model.py`** (L703-708, L745-757, L793-797):
  - `analyze_stint` sets `base_heave_nmm = None` / `base_third_nmm = None` for GT3; per-condition `heave_optimal_nmm = 0.0` sentinel (ST6).
  - `find_compromise_parameters` and `_compute_heave_recommendation` skip heave/third writes when `base_heave_nmm is None` (ST5).

**Deferred (TODO(W3.3) comments at L180, L274, L293, L718):**
- ST1: `f"    Full fuel ({89:.0f}L)"` hardcoded display string.
- ST2: `PUSHROD_CORRECTION_MM_PER_KG = 0.5/(77*…)` derives from BMW GTP fuel mass range.
- ST3: `[89.0, 50.0, 12.0]` default fuel levels.
- ST4, ST7: hardcoded RARB blade range 1–5 — needs per-car blade count.
- MD1: dataclass dead-fields on GT3 — harmless (heave_solver never constructed for GT3).

**Tests:** 17 new in `tests/test_legal_modifiers_stint_gt3.py`. `LegalSpace.from_car(BMW_M4_GT3)` excludes heave/third/torsion dimensions; `LegalSpace.from_car(BMW)` regression locked.

**Cross-file couplings:** none — all callers of `LegalSpace.from_car()` and `compute_perch_offsets()` (in `pipeline/produce.py`, `pipeline/reason.py`, `solver/legal_search.py`, `solver/grid_search.py`) already pass `car` positionally, so the new architecture-aware filtering kicks in automatically.

## Combined-state pipeline behavior

After Wave 1 + Wave 2.1 + Wave 2.2 + Wave 2.3 + Wave 3.1:

- **GT3 IBT through `pipeline.produce`** runs cleanly through Step 1 + Step 2 + Step 3.
  - Step 1: `_solve_balance_only` returns a `RakeSolution` with target balance hit, NaN L/D, `mode="balance_only_search"`.
  - Step 2: `HeaveSolution.null()` with step1's dynamic RH propagated, `present=False`.
  - Step 3: `CornerSpringSolution` with real `front_coil_rate_nmm` (from GT3 spring range) + real `rear_spring_rate_nmm` (from frequency-isolation), `front_torsion_od_mm = 0.0`.
- **Step 4 (ARB/LLTD)** still over-targets Porsche 992 RR LLTD (A-1 — RR `weight_dist_front=0.449` + 0.05 = 0.499 vs empirical 0.43). **W2.4 will fix this.** Front roll stiffness now correctly receives non-zero `front_wheel_rate_nmm` from W2.3, so the search loop executes — but rear blade encoding is still wrong (A-3, A-4: `rear_blade_count=1` for all GT3 stubs means the search loop iterates once).
- **Step 6 (dampers)** still uses BMW polarity for inverted-polarity GT3 cars (Audi/McLaren/Corvette). **W3.2 will fix this.**
- **Setup writer (`output/setup_writer.py`)** raises `ValueError` because GT3 PARAM_IDS are empty stubs. **W4.1 (BMW M4 GT3) and W4.2 (Aston, Porsche 992) will populate them.**
- **Legal-search (`legal_space.py`)** now correctly drops `front_heave_spring_nmm` / `rear_third_spring_nmm` / `front_torsion_od_mm` axes for GT3 — search space is GT3-shaped.
- **Modifier object** no longer carries dead GT3 heave-floor values.
- **Stint analysis** works on GT3 fuel curves but still labels with hardcoded `89` L (W3.3 territory).
- **Calibration gate** correctly reports Step 2 as `not_applicable` for GT3, Step 3 now `weak` (real coil range but motion ratio still placeholder = 1.0), Steps 4–6 still `uncalibrated` or `weak`.

## Recommended next batch

W2.4 + W3.2 in parallel — both touch disjoint files, both unblock the next leg of the GT3 chain.

| Unit | Files | Effort | Why batch together |
|---|---|---|---|
| **W2.4** | `solver/arb_solver.py` blade-vs-label dispatch (A-1..A-5); Porsche 992 LLTD physics formula in `car_model/cars.py` | 24 h | Critical-path: W4.1 setup writer reads `step4.front_arb_*` / `step4.rear_arb_*`. Without this, GT3 ARB output is `front_blade=1, rear_blade=1` — never tunes the ARB. |
| **W3.2** | `solver/damper_solver.py`, `car_model/cars.py` `DamperModel.click_polarity` + `click_range`, per-car overrides (Audi/McLaren/Corvette inverted; Porsche/Acura range mismatch) | 14 h | Independent of W2.4; protects against silent damper-direction bugs. Required before W4.1 because the writer encodes damper clicks. |

Combined ~38 h, no file overlap, both depend on Wave 1 (done). After this batch, **W4.1 (BMW M4 GT3 setup writer)** becomes runnable — it requires W1.3 (registry, done), W2.1 (step2 dispatch, done), W2.3 (front coil, done), W3.2 (damper polarity, after this batch).

## Top deferred-finding ledger (rolled up across waves)

| Audit ref | Risk | Earliest wave that owns it |
|---|---|---|
| R-1 GT3 RideHeightModel feature schema | DEGRADED → BLOCKER once a calibrated RH model lands | W7.1 (auto-calibrate / GarageOutputModel) |
| R-7 / R-8 vortex_burst_threshold cleanup | COSMETIC | Wave 9 docs/cleanup |
| C-8 GT3 rear motion ratio = 1.0 placeholder | DEGRADED → BLOCKER once Step 4/6 calibration matters | Data-blocked (PENDING_IBT); W7.2 territory |
| C-11 Ferrari preload-turns hook canonical-name gate | COSMETIC | Defer until 2nd car needs the same shape |
| ST1/ST2/ST3/ST4/ST7 stint_model fuel constants (89 L hardcoded) | DEGRADED (display drift on GT3) | W3.3 |
| MD1 modifier dataclass dead-fields on GT3 | DEGRADED (harmless) | Wave 9 cleanup |
| F19 legal-search heave axis fallback scoring | DEGRADED | W6.1 (objective + sensitivity GT3 guards) |
| `_car_name(None) → "bmw"` silent default | DEGRADED | Follow-up after W4.1 (needs `_extract_target_maps` car plumbing) |
| BMW fallback in `detect_car_adapter` (GT3 fingerprint logged but not dispatched per car) | DEGRADED | W4.x (per-car YAML fingerprints: Aston `EpasSetting`, Porsche RR fuel cell) |
| Cosmetic F9–F11 in calibration-gate | COSMETIC | Wave 9 |
| A-2 assertion `front_wheel_rate_nmm > 0` in arb_solver | COSMETIC (loud-fail safety net) | W2.4 |

## Test posture

- 543 tests pass (was 295 before this Phase 2 work began per CLAUDE.md 2026-04-11 entry; +56 from Wave 1, +21 from Wave 2.1+2.2, +33 from Wave 2.3+3.1 = 110 new GT3-specific tests).
- 32 skipped (mostly fastapi-dependent webapp tests in this sandbox).
- 1 deselected: `tests/test_run_trace.py::test_support_tier_mapping` — pre-existing data-dependent failure (BMW dataset has 26 sessions but test asserts ≥30); confirmed unchanged whether Wave 1/2/3.1 changes are present or stashed.
- 0 NEW regressions from any of the 7 shipped units.

## Branch strategy reminder

All Wave work lands on `claude/merge-audits-wave1-DDFyg`, which mirrors `gt3-phase0-foundations` plus the 12 audit merges. When this branch is ready to promote, fast-forward `gt3-phase0-foundations` to its tip (or merge with `--ff-only`).
