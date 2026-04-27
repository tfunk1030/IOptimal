# GT3 Phase 2 — Implementation Status

**Last updated:** 2026-04-27 (Wave 2.4 + Wave 3.2 shipped)
**Branch:** `claude/merge-audits-wave1-DDFyg` (mirrors `gt3-phase0-foundations` + 12 audit merges + 4 implementation commits)
**Plan source of truth:** [`SYNTHESIS.md`](SYNTHESIS.md) — 22 work units across 10 waves, ~511 h estimated.

This doc tracks which units have shipped, what was deferred, and the recommended next batch. It is updated after every work-unit batch lands. Each merged PR / batch commit is referenced by SHA + message so the diff can be inspected directly.

## Top-level progress

| Wave | Title | Units | Effort | Status |
|---|---|---|---|---|
| 1 | Foundation invariants | 3 | ~20 h | **DONE 2026-04-27** |
| 2 | Solver chain unblocks | 4 | ~76 h | **DONE 2026-04-27** |
| 3 | Solver chain crash fixes | 3 | ~30 h | W3.1 + W3.2 done; W3.3 remains (~8 h) |
| 4 | Output + writer | 3 | ~70 h | TODO (W4.1 unblocked, next critical-path) |
| 5 | Pipeline + analyzer | 3 | ~62 h | TODO |
| 6 | Learner + scoring | 3 | ~56 h | TODO |
| 7 | Auto-calibrate + GarageOutputModel | 2 | ~80 h | TODO |
| 8 | Infra + DB + automation | 2 | ~43 h | TODO |
| 9 | UI + CLI + tests + docs | 2 | ~62 h | TODO |
| 10 | E2E smoke + remaining cars | 1 | ~80 h+ | TODO (gated on IBT capture) |

**Shipped so far:** 9 of 22 units (~158 h of ~511 h ≈ 31% of total estimated work).
**Remaining critical path:** W4.1 → W4.2 → W7.1 → W7.2 → W9.1 → W9.2 → W10.1 ≈ ~254 h.

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

## Wave 2.4 + Wave 3.2 — DONE (2026-04-27)

Commit: `aa0beda feat(gt3): Wave 2.4 + Wave 3.2 — ARB blade encoding + damper polarity`
+990/-99 across 8 files. 30 new tests. Suite: 573 passed.

### W2.4 — Step 4 ARB blade encoding + Porsche LLTD target — DONE

**Files:** `car_model/cars.py`, `solver/arb_solver.py`, `tests/test_arb_solver_gt3.py` (new).

- **`ARBModel.blade_factor` short-circuits to 1.0 when `max_blade <= 1`** (A-9). Previously returned 0.30 for `blade_factor(1, 1)` — was scaling all GT3 paired-blade ARB stiffness lookups by 0.30. BMW GTP regression cases (`blade_factor(1, 5) = 0.30`, `blade_factor(5, 5) = 1.0`) preserved.
- **New `ARBModel.arb_direction: Literal["ascending", "descending"]`** field (default `"ascending"`) — forward-compat for Corvette Z06 GT3.R inverted encoding when its stub lands in W10.1.
- **GT3 `measured_lltd_target` set per car**: BMW M4 GT3 = 0.51, Aston Vantage GT3 = 0.53, **Porsche 992 GT3 R = 0.45** (RR adjustment from OptimumG +5pp rule). Without these, the bare formula over-targeted Porsche 992 by 5–7pp.
- **`ARBSolution`** gains `rarb_size_slow_corner` / `rarb_size_fast_corner` (size-label live tuning for collapsed-blade encodings).
- **New `_front_spring_roll_stiffness` helper** extracted from 3-way copy-paste (A-7).
- **New `_iter_blade_options(blade_count)` helper**: returns `[1]` when count<=1 (GT3 single-blade-per-label) else `range(1, count+1)` (GTP). Self-documenting GT3 vs GTP intent without flag plumbing.
- **New `_neighbor_size` helper**: walks `rear_size_labels` ±1/±2 indices for slow/fast tuning, honouring `arb_direction`.
- **A-2 zero-front-rate assertion**: loud-fail safety net for any future W2.3 regression.
- **All blade loops** in `solve()` / `solve_candidates()` / `solution_from_explicit_settings()` dispatch through `_iter_blade_options`. GT3 vs GTP slow/fast tuning split: blade-walk for GTP, size-label-walk for GT3 collapsed-blade.
- **GT3 generic `car_specific_notes` branch (A-8)** emits "size label is the live tuning unit" guidance.
- **`summary()`**: GT3 / collapsed-blade branch.

**Deferred:**
- A-8 per-car-name notes (BMW M4 GT3 / Aston / Porsche 992 specific wording) — generic GT3 branch sufficient for now.
- Corvette descending-direction application — no Corvette stub yet (W10.1); forward-compat hook in place via `arb_direction` field.

**Tests:** 16 new in `tests/test_arb_solver_gt3.py`. GTP regression: BMW search returns sensible blade values.

### W3.2 — Damper polarity + range per-car — DONE

**Files:** `car_model/cars.py`, `solver/legality_engine.py`, `solver/candidate_search.py`, `solver/damper_solver.py`, `solver/legal_space.py`, `tests/test_damper_polarity_gt3.py` (new).

- **`DamperModel.click_polarity: Literal["higher_stiffer", "lower_stiffer"]`** field added (default `"higher_stiffer"` preserves BMW behaviour).
- **Porsche 992 GT3 R damper range** confirmed at `(0, 12)` (driver IBT clicks reach 12; matches L3).
- **`legality_engine.py`** (L215-276, polarity at L224): replaced single LS-comp hierarchy check with polarity-dispatched 4-way check (LS comp, LS rbd, HS comp, HS rbd) (L4). Penalty wording changed from numeric to semantic ("Front LS comp softer than rear LS comp") so it reads correctly under either polarity.
- **`candidate_search.py`** (L714-761, polarity at L722): replaced hardcoded `lo=0, hi=20` in `_adjust_integer` with `car.damper.{ls_comp,ls_rbd,hs_comp,hs_rbd}_range` per axle (CS6). Added polarity sign inversion: `polarity_sign = 1 if higher_stiffer else -1` multiplied into delta so "stiffer" intent always means stiffer regardless of car convention (CS7).
- **`damper_solver.py`** (L676-687): GT3 L/R averaging — when `car.suspension_arch.has_heave_third` is False, `lf_hs_comp_adj = rf_hs_comp_adj = (lf+rf)//2` (and lr/rr same). iRacing GT3 garage has only per-axle dampers (8 channels) — without this collapse, the asymmetric .sto write would silently lose L/R divergence (F2 partial fix).
- **`legal_space.py`** (L881-887): TODO(W6.1) comment for polarity-aware search-dimension scoring.

**Deferred:**
- L3 Audi/McLaren/Corvette stubs — those `CarModel` definitions don't exist yet (W10.1). The polarity field is wired so when stubs land, only `click_polarity="lower_stiffer"` + per-car ranges (e.g. McLaren HS=0–50) need to be set — no further code change.
- LS5 polarity-aware search-dimension scoring (TODO comment in `legal_space.py`).
- F2 deeper fix (skip asymmetric calc entirely on GT3 vs collapse-to-average) — current collapse preserves intent.

**Tests:** 14 new in `tests/test_damper_polarity_gt3.py`. GTP regression: `damper_solver.solve(BMW, ...)` does NOT collapse L/R.

## Combined-state pipeline behavior

After Wave 1 + Wave 2 (all 4 units) + Wave 3.1 + Wave 3.2:

- **GT3 IBT through `pipeline.produce`** runs cleanly through Step 1 → Step 6.
  - Step 1: `_solve_balance_only` returns `RakeSolution` with target balance hit, NaN L/D, `mode="balance_only_search"`.
  - Step 2: `HeaveSolution.null()` with step1's dynamic RH propagated, `present=False`.
  - Step 3: `CornerSpringSolution` with real `front_coil_rate_nmm` + real `rear_spring_rate_nmm` (frequency-isolation).
  - **Step 4: `ARBSolution` with size-label live tuning** — search rotates from baseline. Porsche 992 RR LLTD targets the empirically-correct 0.45 (was over-targeting at 0.499).
  - Step 5: Geometry runs (consumes step3 + step4).
  - **Step 6: Dampers respect per-car polarity + range.** GT3 L/R adjustments collapsed to per-axle averages (no silent .sto write loss).
- **Setup writer (`output/setup_writer.py`)** raises `ValueError` because GT3 PARAM_IDS are empty stubs. **W4.1 (BMW M4 GT3) is now fully unblocked and is the next critical-path unit.**
- **Legal-search (`legal_space.py`)** drops heave/third/torsion axes for GT3.
- **Modifier object** no longer carries dead GT3 heave-floor values.
- **Stint analysis** works on GT3 fuel curves but still labels with hardcoded `89` L (W3.3 territory).
- **Calibration gate** correctly reports Step 2 as `not_applicable` for GT3, Step 3+ as `weak` (real coil range / ARB size labels but rear motion ratio still placeholder = 1.0).

## Recommended next batch

**W4.1 alone** is the next critical-path unit (~16 h). Options for batching:

| Combo | Files | Effort | Why batch together |
|---|---|---|---|
| **W4.1 + W3.3** | `output/setup_writer.py` `_BMW_M4_GT3_PARAM_IDS` + per-axle damper collapse; `solver/scenario_profiles.py` per-class fuel cap + `solver/{damper,stint}_model.py` hardcoded 89L removed | 16 h + 8 h = 24 h | Disjoint files. W3.3 is independent (no upstream blockers) and removes the last GT3 display-drift / modeling drift. After this batch, the BMW M4 GT3 .sto write is end-to-end correct. |
| **W4.1 alone** | `output/setup_writer.py` only | 16 h | Cleanest critical-path step. Then W4.2 (Aston + Porsche 992 PARAM_IDS) can land next, then W4.3 (output guards + GT3 garage validator). |

Recommend **W4.1 + W3.3 in parallel** — disjoint files, W3.3 closes Wave 3, and the combined batch gets the BMW M4 GT3 to a fully-correct .sto round-trip.

After this batch the next critical-path is **W4.2** (Aston + Porsche 992 PARAM_IDS — single agent, ~24h) followed by **W4.3** (output guards + GT3 garage validator — ~14h) and then **W5.x** (pipeline + analyzer GT3 awareness — ~62h, can parallelize across 3 agents).

## Top deferred-finding ledger (rolled up across waves)

| Audit ref | Risk | Earliest wave that owns it |
|---|---|---|
| R-1 GT3 RideHeightModel feature schema | DEGRADED → BLOCKER once a calibrated RH model lands | W7.1 (auto-calibrate / GarageOutputModel) |
| R-7 / R-8 vortex_burst_threshold cleanup | COSMETIC | Wave 9 docs/cleanup |
| C-8 GT3 rear motion ratio = 1.0 placeholder | DEGRADED → BLOCKER once Step 4/6 calibration matters | Data-blocked (PENDING_IBT); W7.2 territory |
| C-11 Ferrari preload-turns hook canonical-name gate | COSMETIC | Defer until 2nd car needs the same shape |
| ST1/ST2/ST3/ST4/ST7 stint_model fuel constants (89 L hardcoded) | DEGRADED (display drift on GT3) | W3.3 (next batch) |
| MD1 modifier dataclass dead-fields on GT3 | DEGRADED (harmless) | Wave 9 cleanup |
| F19 legal-search heave axis fallback scoring | DEGRADED | W6.1 (objective + sensitivity GT3 guards) |
| `_car_name(None) → "bmw"` silent default | DEGRADED | Follow-up after W4.1 (needs `_extract_target_maps` car plumbing) |
| BMW fallback in `detect_car_adapter` (GT3 fingerprint logged but not dispatched per car) | DEGRADED | W4.x (per-car YAML fingerprints: Aston `EpasSetting`, Porsche RR fuel cell) |
| Cosmetic F9–F11 in calibration-gate | COSMETIC | Wave 9 |
| A-8 per-car-name ARB notes (BMW M4 GT3 / Aston / Porsche 992 wording) | COSMETIC | Wave 9 |
| Corvette `arb_direction="descending"` application | DEGRADED (forward-compat hook in place, no Corvette stub yet) | W10.1 |
| L3 inverted-polarity car stubs (Audi, McLaren, Corvette `click_polarity="lower_stiffer"`) | BLOCKER for those 3 cars (no stub exists yet) | W10.1 |
| LS5 polarity-aware search-dimension scoring in `legal_space.py` | DEGRADED (TODO comment in code) | W6.1 |
| F2 deeper fix (skip asymmetric damper calc on GT3 vs collapse-to-average) | DEGRADED (current collapse preserves intent) | Future cleanup |

## Test posture

- 573 tests pass (was 295 before this Phase 2 work began per CLAUDE.md 2026-04-11 entry; +56 from Wave 1, +21 from Wave 2.1+2.2, +33 from Wave 2.3+3.1, +30 from Wave 2.4+3.2 = 140 new GT3-specific tests).
- 32 skipped (mostly fastapi-dependent webapp tests in this sandbox).
- 1 deselected: `tests/test_run_trace.py::test_support_tier_mapping` — pre-existing data-dependent failure (BMW dataset has 26 sessions but test asserts ≥30); confirmed unchanged whether Wave 1/2/3.1/3.2 changes are present or stashed.
- 0 NEW regressions from any of the 9 shipped units.

## Branch strategy reminder

All Wave work lands on `claude/merge-audits-wave1-DDFyg`, which mirrors `gt3-phase0-foundations` plus the 12 audit merges. When this branch is ready to promote, fast-forward `gt3-phase0-foundations` to its tip (or merge with `--ff-only`).
