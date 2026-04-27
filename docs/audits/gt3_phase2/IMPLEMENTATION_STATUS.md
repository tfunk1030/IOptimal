# GT3 Phase 2 — Implementation Status

**Last updated:** 2026-04-27 (Wave 7.2 + Wave 8.2 shipped — Wave 7 + Wave 8 COMPLETE)
**Branch:** `claude/merge-audits-wave1-DDFyg` (mirrors `gt3-phase0-foundations` + 12 audit merges + 11 implementation commits)
**Plan source of truth:** [`SYNTHESIS.md`](SYNTHESIS.md) — 22 work units across 10 waves, ~511 h estimated.

This doc tracks which units have shipped, what was deferred, and the recommended next batch. It is updated after every work-unit batch lands. Each merged PR / batch commit is referenced by SHA + message so the diff can be inspected directly.

## Top-level progress

| Wave | Title | Units | Effort | Status |
|---|---|---|---|---|
| 1 | Foundation invariants | 3 | ~20 h | **DONE 2026-04-27** |
| 2 | Solver chain unblocks | 4 | ~76 h | **DONE 2026-04-27** |
| 3 | Solver chain crash fixes | 3 | ~30 h | **DONE 2026-04-27** |
| 4 | Output + writer | 3 | ~70 h | **DONE 2026-04-27** |
| 5 | Pipeline + analyzer | 3 | ~62 h | **DONE 2026-04-27** |
| 6 | Learner + scoring | 3 | ~56 h | **DONE 2026-04-27** |
| 7 | Auto-calibrate + GarageOutputModel | 2 | ~80 h | **DONE 2026-04-27** (W7.2 scaffolding only — full fits gated on IBT) |
| 8 | Infra + DB + automation | 2 | ~43 h | **DONE 2026-04-27** |
| 9 | UI + CLI + tests + docs | 2 | ~62 h | TODO (next critical-path) |
| 10 | E2E smoke + remaining cars | 1 | ~80 h+ | TODO (gated on per-car IBT capture) |

**Shipped so far:** 21 of 22 unblocked units (~443 h of ~511 h ≈ 87% of total estimated work).
**Remaining critical path:** W9.1 → W9.2 → W10.1 ≈ ~174 h (W10.1 gated on IBT capture).

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

## Wave 4.1 + Wave 3.3 — DONE (2026-04-27)

Commit: `9f746be feat(gt3): Wave 4.1 + Wave 3.3 — BMW M4 GT3 .sto writer + fuel constants`
+994/-132 across 7 files. 29 new tests. Suite: 602 passed.

### W4.1 — BMW M4 GT3 setup writer — DONE

**Files:** `output/setup_writer.py`, `output/garage_validator.py`, `tests/test_setup_writer_gt3_bmw.py` (new).

- **`_BMW_M4_GT3_PARAM_IDS` dict (L506-583)** — verbatim from `output.md:294-365`. ~45 entries: aero, tyres (note `LeftRear` no `Tire` suffix), front brakes section, per-corner LF/RF/LR/RR (with `BumpRubberGap` + `SpringRate` replacing torsion_od), `Chassis_Rear_FuelLevel`, InCarAdjustments (BrakePressureBias, AbsSetting, TcSetting), GearsDifferential, per-axle dampers (8 channels).
- **`_CAR_PARAM_IDS` registered** with `"bmw_m4_gt3"` (L593).
- **`is_gt3` flag** at top of `write_sto` (L944-955); per-block gates on every GTP write.
- **GTP writes skipped on GT3:** heave/third spring + perch (L997-1083), torsion bar OD/turns, pushrod offsets (L989-995), per-corner damper writes (16 channels) replaced with per-axle (8 channels) at L1256-1294, roll-damper block (L1301), rear-3rd damper block (L1329), front roll spring write, ARB size string (L1229, 1234).
- **GT3-specific writes added:** 4 corner spring rates from `step3.front_coil_rate_nmm` / `step3.rear_spring_rate_nmm` paired (LF==RF, LR==RR per BMW M4 GT3 manual) at L1085-1103. `BumpRubberGap` × 4 + `CenterFrontSplitterHeight` (placeholder 0.0; W4.3 sources from garage state). TC/ABS as indexed string `"n (TC)"` / `"n (ABS)"` at L1364-1378.
- **`_validate_setup_values`** gains `_is_gt3_validation` guard (L641-665) to skip GT3-irrelevant heave/torsion clamps.
- **`output/garage_validator.py:_clamp_step2`** accepts `car=` kwarg and early-returns on GT3 (L284-302); caller at L126 threads through.

**Deferred (W4.2):** Aston Vantage GT3 + Porsche 992 GT3 R PARAM_IDS dicts (Porsche has integer ARB encoding, paired rear `TotalToeIn`, FuelLevel-in-front section). Per-car TC suffix dispatch (Aston `"n (TC SLIP)"`, Porsche `"n (TC-LAT)"`).

**Deferred (W4.3):** `BumpRubberGap` value sourcing from garage state. `_clamp_step3` / `_fix_slider` / `_fix_torsion_bar_defl` / `_fix_front_rh` GT3 early-returns (audit O17, O19, O20, O21). `.sto` round-trip into iRacing's actual schema validator.

**Tests:** 16 new in `tests/test_setup_writer_gt3_bmw.py`. Required field presence + forbidden field absence + GTP regression locked.

### W3.3 — Fuel constants generalized — DONE

**Files:** `solver/damper_solver.py`, `solver/stint_model.py`, `solver/scenario_profiles.py`, `tests/test_fuel_constants_gt3.py` (new).

- **`damper_solver.py`** (L444, L472-478, L1030, L1038-1040): `solve()` and `solution_from_explicit_settings()` default `fuel_load_l` changed from 89.0 to None; raises `ValueError` with car name + max capacity if caller forgets it. All 9 caller sites in `solver/` + `pipeline/` already passed explicit `fuel_load_l`, so 0 caller updates needed (F3, F4).
- **`stint_model.py`** (L113-120, L181-187, L295-307, L660-667, L740-746): `HeaveRecommendation` gains `full_fuel_l: float` field; display string reads from car-specific value (ST1). `compute_fuel_states()` and `analyze_stint()` default `fuel_levels_l` derived per-car: `[fuel_capacity_l, midpoint, fuel_stint_end_l]` (ST3). BMW (89, 10) → `[89, 49.5, 10]`. BMW M4 GT3 (100, 10) → `[100, 55, 10]`. Aston (106, 10) → `[106, 58, 10]`.
- **`stint_model.py:273-280`**: `PUSHROD_CORRECTION_MM_PER_KG` comment retagged TODO(W7.2) — the 77 constant (BMW GTP fuel-mass range) drifts ~10–14% on GT3 (ST2). Real fix needs IBT pushrod-vs-fuel sweeps.
- **`scenario_profiles.py:144-149`**: SP3 TODO comment block before `"race"` ScenarioProfile entry. GTP race profile's `max_front_heave_travel_used_pct=95.5` is GTP-physics; GT3 race profile would use `BumpRubberGap`-based limits. Deferred to W6.x.

**Tests:** 13 new in `tests/test_fuel_constants_gt3.py`. `DamperSolver(BMW_M4_GT3, ...)` raises with "100L"; analyze_stint covers `[100, 55, 10]` not `[89, 50, 12]`. GTP regression locked.

## Wave 4.2 + Wave 5.2 — DONE (2026-04-27)

Commit: `d124944 feat(gt3): Wave 4.2 + Wave 5.2 — Aston/Porsche writer + analyzer GT3 dispatch`
+1307/-24 across 9 files. 39 new tests. Suite: 641 passed.

### W4.2 — Aston Vantage + Porsche 992 GT3 R setup writer — DONE

**Files:** `output/setup_writer.py`, `tests/test_setup_writer_gt3_aston_porsche.py` (new).

- **`_ASTON_MARTIN_VANTAGE_GT3_PARAM_IDS` dict (L604-688)** — verbatim from `output.md:367-441`. ~50 entries with Aston-specific divergences: `FrontBrakesLights` section, `FarbBlades`/`RarbBlades`, `AeroBalanceCalculator` suffix, `EpasSetting`/`ThrottleResponse`/`EnduranceLights`/`NightLedStripColor`.
- **`_PORSCHE_992_GT3R_PARAM_IDS` dict (L691-768)** — verbatim from `output.md:443-518`. Porsche-unique: integer `ArbSetting`/`RarbSetting` (no blade), paired `Chassis.Rear.TotalToeIn`, `Chassis.FrontBrakesLights.FuelLevel`, `ThrottleShapeSetting`/`DashDisplayPage`.
- **Both registered** in `_CAR_PARAM_IDS` (L770-771).
- **Per-car GT3 sub-dispatch** with `is_aston_gt3` / `is_porsche_gt3` flags (L1141-1142):
  - ARB encoding: BMW/Aston use blade keys (`front_arb_blades` → ArbBlades / FarbBlades); Porsche uses `front_arb_setting` → ArbSetting (single int).
  - Rear toe: BMW + Aston per-wheel; Porsche paired (avg of LR/RR) at L1423.
  - TC label suffix dispatch (L1576-1583): "n (TC)" BMW, "n (TC SLIP)" Aston, "n (TC-LAT)" Porsche.
  - Aston-only fields written at L1594+.
  - Porsche-only fields written at L1600+ with defaults sourced from `docs/gt3_session_info_porsche_992_gt3r_spielberg_2026-04-26.yaml`.
  - Roll/3rd-damper guard now respects `is_porsche_gt3` (L1452) — phantom-write protection.

**Deferred (W4.3):** driver-`current_setup` passthrough for new display fields (currently placeholders), `BumpRubberGap` value sourcing, iRacing schema round-trip validation.

**Tests:** 23 new in `tests/test_setup_writer_gt3_aston_porsche.py`. Per-car field presence + forbidden field absence + BMW M4 GT3 regression.

### W5.2 — Analyzer setup_reader / setup_schema / sto_adapters GT3 dispatch — DONE

**Files:** `analyzer/setup_reader.py`, `analyzer/setup_schema.py`, `analyzer/sto_adapters.py`, `analyzer/sto_binary.py`, `analyzer/extract.py` (TODO markers), `analyzer/diagnose.py` (TODO markers), `tests/test_analyzer_setup_reader_gt3.py` (new).

- **`analyzer/setup_reader.py`** (L20-25, 28, 125, 338-355, 405-411, 519-537, 762-767, 802):
  - `GT3_CANONICALS` / `GTP_CANONICALS` constants.
  - `_parse_indexed_label` helper strips "X (TC SLIP)" / "X (TC-LAT)" / "X (ABS)" → int.
  - `_read_gt3_setup(cs, car_canonical)` per-car YAML-path dispatch (front section name, ARB encoding, fuel location, rear toe shape).
  - GT3 dataclass fields: `front_corner_spring_nmm`, bump rubber gaps × 4, splitter, ARB settings, EPAS/throttle/cross-weight.
  - `from_ibt` GT3 early-return; `adapter_name` whitelist accepts GT3 canonicals; `summary()` GT3 branch.
- **`analyzer/setup_schema.py`** (L88-209, 268-277, 452-470):
  - `_GT3_KNOWN_FIELD_MAP` per-car XML field-id table (BMW M4 GT3 / Aston / Porsche, ~35 entries each).
  - `get_known_fields(car)` dispatcher.
  - `_manual_constraints` `hasattr()` guards — early-return `(None, None, None)` when GT3 GarageRanges lacks heave/torsion fields.
- **`analyzer/sto_adapters.py`** (L447-451, 482-501):
  - `_GT3_CANONICALS` frozenset.
  - GT3 branch returns `"<canonical>_v3_container"` instead of generic fallback.
- **`analyzer/sto_binary.py`** (L28-34): 3 GT3 entries prepended to `_CAR_HINTS` (longer-match-first ordering).
- **TODO(W5.3) markers added** per spec: `extract.py:96` (lltd_measured alias / A16), `extract.py:1443+1450` (`_extract_heave_deflection` / A17), `diagnose.py:179` (`_check_safety` heave bottoming alarms / A18).

**Audit corrections discovered:**
- Porsche `ThrottleShapeSetting` actually under `Chassis.InCarAdjustments`, not `Chassis.FrontBrakesLights` as audit stated.
- `ThrottleShapeSetting` is plain integer, not indexed-label string.
- Wing angle appears redundantly in `Chassis.Rear` AND `TiresAero.AeroBalanceCalc`; reader prefers AeroBalanceCalc.

**Tests:** 16 new in `tests/test_analyzer_setup_reader_gt3.py`. Loaded existing `docs/gt3_session_info_*.yaml` files as fixtures directly. GTP regression locked.

## Wave 4.3 + Wave 5.3 — DONE (2026-04-27)

Commit: `2792f3b feat(gt3): Wave 4.3 + Wave 5.3 — output validators + analyzer extract/diagnose GT3`
+1127/-110 across 10 files. 24 new tests. Suite: 665 passed.

### W4.3 — Output guards + GT3 garage validator + report — DONE

**Files:** `output/garage_validator.py`, `output/report.py`, `output/setup_writer.py`, `car_model/cars.py`, `tests/test_output_gt3_w43.py` (new).

- **`output/garage_validator.py`**: `validate_and_fix_garage_correlation` GT3 short-circuit (L160-178) skips Phase 2+3 entirely. `_clamp_step3` GT3 guard (L350-377) skips torsion + perch clamps; still clamps rear coil rate. `_fix_slider` (L437-447), `_fix_front_rh` (L499-512), `_fix_torsion_bar_defl` (L593-605) all early-return `[]` on GT3 (audit O17/O19/O20/O21).
- **`output/report.py`**: New `_is_gt3(car)` helper (L112-124). 9 sites gated for GT3 (audit O28-O33): garage_outputs skip, display values init, _tb_turns sentinel, SETUP TO ENTER 4-corner spring rendering, DAMPERS per-axle, TARGETS heave-margin skip, VALIDATION SUMMARY GT3 spring summary, GARAGE CARD 4-corner display, BALANCE & PLATFORM springs + heave-ratio skip, `print_comparison_table` GT3 param_map. GT3 displays read `step3.front_coil_rate_nmm` + `step3.rear_spring_rate_nmm` (paired). BumpRubberGap and splitter are placeholders ("(pipeline) mm") until garage state plumbing lands.
- **`output/setup_writer.py:1131-1141`**: W4.3 NOTE block — iRacing schema round-trip validation deferred (no offline iRacing XSD copy in repo; manual driver-side QA required).
- **`car_model/cars.py:1554-1564`**: `GarageRanges` gains `bump_rubber_gap_front_mm`, `bump_rubber_gap_rear_mm`, `bump_rubber_gap_resolution_mm`, `splitter_height_mm`, `splitter_height_resolution_mm` fields. GTP defaults `(0.0, 0.0)`. 3 GT3 stubs populated with driver-bracketed ranges from audit `output.md:540-555`.

**Deferred:**
- GT3-specific `_fix_front_rh` (BumpRubberGap-aware): W7.x.
- iRacing schema round-trip: manual QA + future fixture-based test.
- Full GT3 deflection display (per-corner coil): W7.x.
- 4-corner step3 clamping (lf/rf/lr/rr_spring_rate independent): needs `CornerSpringSolution` dataclass extension first.

**Pre-existing bug noted (out of scope):** `print_comparison_table` (`output/report.py:1103`) referenced `_has_rear_torsion` and `_is_acura` from outer scope (only defined inside `print_full_setup_report`). W4.3 GT3 branch defaults them to `False` locally; GTP path still has the latent NameError for non-Ferrari cars with rear torsion.

**Tests:** 12 new in `tests/test_output_gt3_w43.py`. validate_and_fix_garage_correlation GT3 non-mutation; fixer early-returns; GTP regression; report content (no "Heave F:" / "Third R:" garbage; 4-corner spring display); GarageRanges field shape.

### W5.3 — Analyzer extract + diagnose + causal_graph GT3 awareness — DONE

**Files:** `analyzer/extract.py`, `analyzer/diagnose.py`, `analyzer/causal_graph.py`, `analyzer/report.py`, `tests/test_analyzer_gt3_w53.py` (new).

- **`analyzer/extract.py:1462`**: `_extract_heave_deflection` gated on `car.suspension_arch.has_heave_third` (audit A17). Returns immediately for GT3 — leaves `heave_*_pct` fields at None. Removes W5.2-placed TODO markers.
- **`analyzer/diagnose.py`** (A16 + A18):
  - A16: dropped `state.lltd_measured` fallback reads in `Diagnosis(...)` ctor (L99-105) and `_check_balance` roll-proxy fallback (L719). Now uses `roll_distribution_proxy` directly.
  - A18: 7 gate-points on heave-bottoming predicates (L219, 287, 347, 373, 397, 463, 486). `_check_safety` caches `has_heave_third` at top (L191). GT3 sessions no longer fire phantom "stiffen heave spring" critical alarms.
  - `analyze_causes(problems, car=car)` call site (L146) threads car through.
- **`analyzer/causal_graph.py`** (A19):
  - `CausalNode` gains `gtp_only` / `gt3_only` bool flags (L46-51).
  - `heave_too_soft` / `heave_too_stiff` / `third_too_soft` tagged `gtp_only=True` (L117-160).
  - 3 new GT3 nodes: `front_corner_spring_too_soft`, `front_corner_spring_too_stiff`, `rear_corner_spring_too_soft` (parameter `front_corner_spring_nmm` / `rear_corner_spring_nmm`).
  - 4 new GT3 causal edges (L365-378) mirror GTP heave_too_soft chain (skip `symptom_excursion_high` and `symptom_vortex_burst` — GTP aero-floor concepts).
  - New `applicable_nodes(car)` + `_is_node_applicable(node, car)` helpers (L502-528).
  - `analyze_causes(problems, car=None)` optional kwarg (L602-620, L642-645, L690-693). Filters by architecture during traversal AND disambiguation pass.
- **`analyzer/report.py:317`**: `lltd_measured` read kept for backward-compat with historical observation JSON; deprecation comment per A16 audit ledger.

**Audit corrections / discoveries:**
- `lltd_measured` alias is read by `analyzer/report.py` + `analyzer/telemetry_truth.py` + `solver/laptime_sensitivity.py` for display only; all guard on truthiness so None correctly degrades. No solver physics path consumes the alias.
- A20–A38 cosmetic findings deferred per audit's effort estimate.
- A34 recommend.py setattr dispatch: GT3 chain naturally never enters `_recommend_safety`'s heave block (A18 prevents the upstream Problem). GT3-aware recommend path emitting `front_corner_spring_nmm` changes is W6/W7 territory.

**Tests:** 12 new in `tests/test_analyzer_gt3_w53.py` across 4 classes:
- `A16AliasTests`: lltd_measured stays None after extraction; not aliased.
- `A17HeaveExtractTests`: `_extract_heave_deflection` skipped on GT3, runs on GTP.
- `A18DiagnoseHeaveAlarmsTests`: GT3 doesn't emit "stiffen heave spring"; GTP still does (regression locked).
- `A19CausalGraphTests`: `applicable_nodes(BMW_M4_GT3)` excludes heave nodes, includes GT3 corner-spring nodes; reverse for BMW LMDH.

## Wave 5.1 + Wave 6.1 — DONE (2026-04-27)

Commit: `eb8c2a0 feat(gt3): Wave 5.1 + Wave 6.1 — pipeline conditional + objective/sensitivity`
+1510/-229 across 9 files. 38 new tests. Suite: 703 passed.

### W5.1 — Pipeline produce/reason/report GT3 conditional — DONE

**Files:** `pipeline/produce.py`, `pipeline/reason.py`, `pipeline/report.py`, `pipeline/scan.py`, `tests/test_pipeline_gt3_w51.py` (new).

- **`pipeline/produce.py`** (194 LoC delta): module-level `_is_gt3_car(car)` and `_step2_present(step2)` helpers (L67-90). F1 alias map drops heave/third on GT3 (L93-138). F2 m_eff `car.heave_spring is not None` guard (L460-470). F4 analyze_stint heave/third = None on GT3 (L1011-1019). F7 JSON `step2_heave` sentinel (L1672-1681). F9 delta card + solver_predictions heave fields gated on `_step2_present` (L1814-1823, L1918-1949). F10 GT3 top-n column schema swap LF-Spg/RR-Spg (L1376-1429).
- **`pipeline/reason.py`** (limited delta): F4 analyze_stint GT3 None passthrough (L3427-3437). F7 JSON sentinel (L3777-3784). F13 heave-floor modifier section gated (L2174-2185). F11/F12 verified already W2.1-handled (no further edits).
- **`pipeline/report.py`** (118 LoC delta): module-level `_is_gt3` / `_step2_present` helpers (L47-72). F14 CURRENT vs RECOMMENDED 4 corner spring rows for GT3 (L513-553). F15 FRONT HEAVE TRAVEL BUDGET architecture-aware gate (L603-614). F16 `GarageSetupState.from_solver_steps` + `garage_model.predict` gated on `not _is_gt3_report` (L243-265).
- **`pipeline/scan.py`** (5 LoC): F23 `TODO(W5.1+)` marker for missing GT3 coil model row (L458-462).

**Stale audit findings already handled (verified):**
- F11, F12: W2.1 (commit `2cbf1e8`).
- F6 (.sto writer call site): W4.x (`output/setup_writer.py` `is_gt3` dispatch).
- F8 (Report-emission): output/report.py W4.3 GT3 awareness.

**Deferred:**
- DEGRADED 17 / 18 / 19: CLI ergonomics; track-slug heuristic — out of W5.1 architecture scope.
- DEGRADED 21 / 22: predictor cascading — W7+ territory.
- F23 cosmetic: needs new model fit per car + adapter wiring.

**Tests:** 20 new in `tests/test_pipeline_gt3_w51.py`. Alias map filtering, `_step2_present` sentinel, top-n schema swap, report 4-corner display, GTP regression.

### W6.1 — Objective + sensitivity GT3 guards — DONE

**Files:** `solver/objective.py`, `solver/sensitivity.py`, `solver/laptime_sensitivity.py`, `tests/test_objective_sensitivity_gt3_w61.py` (new).

- **`solver/objective.py`** (180 LoC delta):
  - F-O-1 (L884-905): m_eff GT3 fallback. Uses half-axle sprung mass (`total_mass × weight_dist / 2.0`) — same proxy as W2.2 `rh_excursion_p99` fallback. BMW M4 GT3 (1411 kg, 0.464 fwd) → m_eff_front ≈ 327 kg, m_eff_rear ≈ 378 kg.
  - F-O-2 (L926-959): excursion physics dispatch — `k_front_for_excursion` / `parallel_wheel_rate_for_excursion` uses corner-spring rate on GT3 instead of heave; cap relaxed (< 20 N/mm) for GT3.
  - F-O-3 (L686-696): `_compute_lltd_fuel_window` early-returns `(0, 0, 0)` on GT3. Corner coils are constant-rate; LLTD doesn't shift across stint.
  - F-O-4 (L1840-1882): `_compute_platform_risk` gates entire heave-spring deflection block on `_hsm is not None`.
  - F-O-5 (L417-422 + L428-431 + L2072-2086): `_heave_calibration_uncertainty_penalty_ms` / `_heave_realism_penalty_ms` / envelope ratio penalty all gated on `heave_spring is None` / `_is_gt3_envelope`; contribute 0 for GT3.
- **`solver/sensitivity.py`** (87 LoC delta):
  - F-S-1 (L224-231): `analyze_step2_constraints` early-return `[]` when `not step2.present`.
  - F-S-3/4/5 (L555-580): `build_sensitivity_report` `_heave_block_runnable` gate around heave sensitivities and confidence bands.
- **`solver/laptime_sensitivity.py`** (63 LoC delta):
  - F-LT-1 (L394-407, L598-611): `_front_heave_sensitivity` and `_rear_third_sensitivity` early-return on `not step2.present`.
  - F-LT-2 (L893-911, L919-936): `_heave_perch_sensitivity` and `_rear_third_perch_sensitivity` same.
  - Master aggregator (L1400-1432): `_step2_present` flag + conditional list entries + None filter.

**Audit corrections / discoveries:**
- GTP-existing bug noted: `_compute_lap_gain_breakdown:1664` hardcodes `diff_target=30.0` instead of `car.default_diff_preload_nm` (F-O-12). Affects all non-BMW cars; out of W6.1 scope.
- **GT3 LLTD scoring quality bias** (F-O-7): `front_wheel_rate` falls through to `car.corner_spring.front_roll_spring_rate_nmm = 0.0` on GT3, so the LLTD `k_front` term gets 0 and silently underweights candidates. GT3 score is non-crashing but uniformly biased until W6.x wires the GT3 `front_corner_spring_nmm` axis as a candidate variable.

**Deferred (per task instructions):**
- F-O-6 through F-O-15 (DEGRADED + COSMETIC).
- F-S-2, F-S-6, F-LT-3 through F-LT-11 (DEGRADED + COSMETIC).
- GT3-shape `_front_corner_spring_sensitivity` replacement function: W6.x.

**Tests:** 18 new in `tests/test_objective_sensitivity_gt3_w61.py`. `evaluate_physics(BMW_M4_GT3)` non-crash + finite Score. LLTD fuel window returns `(0, 0, 0)` for GT3. Heave-perch / rear-third sensitivity functions return None on GT3. GTP regression: BMW LMDH still includes heave/third rows + non-zero LLTD fuel window.

## Wave 6.2 + Wave 6.3 — DONE (2026-04-27)

Commit: `4fcb2c6 feat(gt3): Wave 6.2 + Wave 6.3 — learner GT3 awareness (causality + fits)`
+833/-16 across 7 files. 17 new tests. Suite: 720 passed.

### W6.2 — STEP_GROUPS dispatch + KNOWN_CAUSALITY GT3 — DONE

**Files:** `learner/delta_detector.py`.

- New `step_groups_for_arch(arch)` (L52-100). GT3 (`GT3_COIL_4WHEEL`) gets `step3_corner_combined` with the 5 GT3 setup parameters; GTP keeps legacy `step2_heave` + `step3_springs`. Backward-compat: module-level `STEP_GROUPS` constant preserved.
- 23 new GT3 KNOWN_CAUSALITY entries (L273+): front + rear `corner_spring_nmm` (8 effects each), `bump_rubber_gap_mm` (3 + 2), `splitter_height_mm` (1). Reverse-direction entries auto-generated.
- Per-arch dispatch helper at L519 looks up `car.suspension_arch`.
- delta-classification thresholds extended with `front_corner_spring_nmm` (5/20 N/mm) at L434.

### W6.3 — Empirical models + observation + clusters + recall GT3 — DONE

**Files:** `learner/empirical_models.py`, `learner/observation.py`, `learner/setup_clusters.py`, `learner/ingest.py`, `learner/recall.py`.

- **`empirical_models.py`**: new `_fit_corner_spring_to_variance(obs_list, models, axle)` (L347-391). Wired into `fit_models()` (L191-195) alongside `_fit_heave_to_variance` — both run for every fit_models call; whichever has samples produces a relationship.
- **`observation.py:build_observation`**: GT3 architecture detected structurally (`front_corner_spring_nmm > 0` AND `front_heave_nmm == 0` AND `front_torsion_od_mm == 0`). Setup dict populates 5 GT3 keys via getattr-with-defaults; downstream consumers gate off `> 0` thresholds in `delta_detector` to skip zero values.
- **`setup_clusters.py`**: new `setup_parameters_for_arch(arch)` (L74-105). GT3 parameter list includes corner-spring × 2, bump_rubber_gap × 2, splitter_height. Backward-compat: `DEFAULT_SETUP_PARAMETERS` preserved.
- **`ingest.py`**: GT3 setup keys added to field-extraction list (L767-770).
- **`recall.py`**: Documentation note for GT3 lookups; per-arch dispatch deferred to W7.x where solver-side feedback consumes the new GT3 corrections.

**Deferred:**
- Audit #4: GT3 m_eff path (corner-spring-derived formula). Today gated on `car.heave_spring is not None` — skips on GT3 cleanly. TODO(W7.x).
- Audit #6: `PREDICTION_METRICS` GT3-only signals (`splitter_scrape_events`, `front_bump_rubber_contact_pct`) — not yet wired; defer to W7.
- Audit #14: zero-sample warning logging (cosmetic).
- Audit #16: `most_impactful_parameters` cosmetic wording.

**Tests:** 17 new in `tests/test_learner_gt3_w62_w63.py` across 4 classes:
- `W62StepGroupsTests`: GT3 has `step3_corner_combined`; GTP has `step2_heave` + `step3_springs`; backward-compat.
- `W62KnownCausalityGT3Tests`: front + rear corner-spring + bump-rubber + splitter entries; reverse direction auto-generated.
- `W63CornerSpringFitterTests`: front + rear axle fits; empty + zero + invalid axle cases.
- `W63SetupClustersTests`: GT3 includes corner-spring keys; GTP keeps legacy.
- `W63BuildObservationGT3Tests`: build_observation populates GT3 keys (1 skip due to fixture stub gaps).

## Wave 7.1 + Wave 8.1 — DONE (2026-04-27)

Commit: `1d071b8 feat(gt3): Wave 7.1 + Wave 8.1 — GarageOutputModel + DB schema GT3`
+1261/-42 across 8 files. 36 new tests. Suite: 756 passed.

### W7.1 — GT3 GarageSetupState + GarageOutputModel — DONE

**Files:** `car_model/garage.py`, `car_model/auto_calibrate.py`, `tests/test_garage_gt3_w71.py` (new).

- **`car_model/garage.py:from_current_setup`** (L89-104): added `csm/hsm is not None` guards around the indexed-car decode block — was crashing on GT3 (heave_spring=None / corner_spring may be None). The substantive GT3 path (L105-161, L271-300) was already in place from earlier waves.
- **`GarageOutputModel`** (L378-389): 5 new GT3 default fields — `default_front_corner_spring_nmm=220.0`, `default_rear=180.0`, `default_front_bump_rubber_gap_mm=15.0`, `default_rear=50.0`, `default_splitter_height_mm=20.0` (BMW M4 GT3 mid-range).
- **`default_state(car=None)`** (L477-512): architecture-aware. GT3 cars receive a state with corner-spring + bump-rubber + splitter populated; GTP fields stay 0.0.
- **`auto_calibrate.py:_setup_key()`** (L123-133): 5 new tuple slots appended for GT3 fingerprint fields. `getattr`-with-defaults preserves GTP backward-compat (legacy CalibrationPoints get 0.0 in new slots — no collision since the fingerprint is a tuple, not a hash). W7.x cosmetic-#25 TODO marker on track-key (L91-93).

**Deferred:**
- Per-car `GarageOutputModel` defaults from fitted regressions (audit #23): W7.2.
- `CalibrationPoint` GT3 schema (audit Blocker #7): W7.2.

**Tests:** 16 new across 8 classes. GT3 vs GTP dispatch on `from_current_setup`; zero-safe `inv_*` extractors; `_setup_key` distinguishes GT3 corner-spring deltas; `default_state(car=BMW_M4_GT3)` populates GT3 defaults.

### W8.1 — DB schema migration + per-arch aggregator — DONE

**Files:** `teamdb/models.py`, `teamdb/aggregator.py`, `server/routes/observations.py`, `migrations/0001_gt3_phase2.sql` (new), `tests/test_teamdb_gt3_w81.py` (new).

- **`teamdb/models.py:CarDefinition`** (L208-216): 3 new nullable columns — `iracing_car_path` (indexed), `bop_version`, `suspension_arch` (indexed). New `Index("ix_car_definitions_iracing_path", ...)` + `ix_car_definitions_arch` at L182-183.
- **`teamdb/models.py:Observation`** (L259-272): `suspension_arch VARCHAR(48) NOT NULL DEFAULT 'gtp_heave_third_torsion_front'`, `bop_version`, `iracing_car_path`. New composite index `ix_observations_team_arch_track` at L240.
- **`migrations/0001_gt3_phase2.sql`** (new): raw SQL migration script (project uses raw SQL, not Alembic). `ADD COLUMN IF NOT EXISTS` for idempotence; `BEGIN/COMMIT` atomicity. Backfills GTP rows + Porsche 963 GTP-roll + NULL safety-net. Header documents that operator MUST run `psql -f` against Cloud SQL before next server image deploys (`Base.metadata.create_all` does NOT apply ALTER statements).
- **`teamdb/aggregator.py`** (full rewrite): `aggregate_observations(observations, car, track, *, suspension_arch=None)` (L155-186) partitions by `suspension_arch` (audit F3) — drops mismatching rows before forwarding to `learner.empirical_models.fit_models()`. Result dict carries `"suspension_arch"`. F10 fix: imports `car_model.registry.track_key` (instead of `track.lower().split()[0]`). F11 per-arch tier thresholds at L25-49 — GT3 uses 4/10/20 (vs GTP 5/15/30) with `TODO(W9.1)` marker for empirical recalibration.
- **`server/routes/observations.py`**: `ObservationCreateRequest` Pydantic schema (L25-41) gains `suspension_arch` (default `"gtp_heave_third_torsion_front"`), `bop_version`, `iracing_car_path`. POST handler (L69-93) validates `suspension_arch` matches the team's existing `CarDefinition.suspension_arch` (raises HTTPException(400) on mismatch). Persists all 3 new fields on auto-registered `CarDefinition` and the new `Observation` row.

**Deferred:**
- F5 watcher CarPath detection: W8.2.
- F7 `EmpiricalModel` UniqueConstraint extension to include `suspension_arch + bop_version`: DEGRADED, deferred to W9.x.
- F11 GT3 threshold calibration: real numbers from observed sessions (W9.1).

**Tests:** 20 new in `tests/test_teamdb_gt3_w81.py`. Schema fields + defaults; aggregator filters mixed observations to target arch only; aggregator uses canonical `track_key` not split-on-space; per-arch support tier thresholds; migration script presence + ALTER TABLE statements verified. F6 server-route mismatch: schema-level (Pydantic + handler logic); HTTP-level deferred to a TestClient fixture.

## Wave 7.2 + Wave 8.2 — DONE (2026-04-27)

Commit: `c262efa feat(gt3): Wave 7.2 + Wave 8.2 — auto-calibrate scaffolding + watcher CarPath`
+1078/-32 across 10 files. 49 new tests. Suite: 805 passed.

### W7.2 — Auto-calibrate GT3 scaffolding — DONE (intercept-only until IBT data lands)

**Files:** `car_model/auto_calibrate.py`, `car_model/registry.py`, `tests/test_auto_calibrate_gt3_w72.py` (new).

- **`CalibrationPoint`** (L228-244): 5 new GT3 fields — `front_corner_spring_nmm`, `rear_corner_spring_nmm`, `front_bump_rubber_gap_mm`, `rear_bump_rubber_gap_mm`, `splitter_height_mm`. Field names align with `learner/observation.py` (W6.3) and `car_model/garage.py:GarageSetupState` (W7.1).
- **New `_track_slug` helper** (L368-382): wraps `registry.track_key` with Spielberg / Red Bull Ring aliases (audit #12).
- **`fit_models_from_points`** (L1193-1202): GT3 guard around the index-decode block — skips heave_spring/torsion access for GT3.
- **`_UNIVERSAL_POOL`** (L1297-1322): 9 new GT3 features appended (corner_spring × 2, inv_corner_spring × 2, bump_rubber_gap × 2, splitter_height, fuel_x_inv × 2). Std-filter at L1310 auto-drops zero-variance features per call site so GTP IBTs silently drop GT3 features and vice-versa.
- **`_FRONT_AXIS_NAMES`** (L1264+): +4 GT3 entries.
- **`_REAR_AXIS_NAMES`** (L1264+): +5 GT3 entries (including `splitter_height` for aero balance shift).
- **`apply_to_car`** (L2239-2269): GT3 short-circuit. Detected via `car.suspension_arch.has_heave_third == False`. Returns immediately with applied note "GT3 calibration applied (intercept-only — N unique setups; varied-spring IBT data needed for full regression fit, see W10.1)". `TODO(W10.1)` marker documents the future write target (`car.corner_spring.front_baseline_rate_nmm`). garage_ranges fields NOT touched (driver-tuned, not regression outputs).
- **`_GT3_PROTOCOL_HINT` template + `_car_protocol_hint(car)` dispatcher** (L2790-2891): GT3 fast path replaces BMW fallback for `bmw_m4_gt3` / `aston_martin_vantage_gt3` / `porsche_992_gt3r`.
- **CLI `--car` argparse choices** (L3427-3438): pulled from `sorted(_CARS.keys())` so all 8 cars (5 GTP + 3 GT3) are accepted automatically.
- **`car_model/registry.py:_TRACK_ALIASES`**: Spielberg / Red Bull Ring aliases.

**Deferred / IBT-gated:**
- Actual non-intercept regression fits on GT3 data — gated on W10.1 (varied-front-coil IBTs at the same track).
- `apply_to_car()` write into `car.corner_spring.front_baseline_rate_nmm` from regression intercept — TODO(W10.1) marker in place.

**Tests:** 25 new across 6 classes covering schema, pool coverage, fingerprint, apply_to_car short-circuit, track slug, protocol hint, CLI choices.

### W8.2 — Watcher + desktop GT3 CarPath detection — DONE

**Files:** `car_model/registry.py`, `track_model/ibt_parser.py` (already had CarPath from W5.x — verified), `watcher/service.py`, `watcher/monitor.py`, `desktop/config.py`, `desktop/app.py`, `teamdb/sync_client.py`, `tests/test_watcher_gt3_w82.py` (new).

- **`car_model/registry.py`**: `_CAR_REGISTRY` rows (L62-77) populate `iracing_car_path` for all 8 entries (5 GTP + 3 GT3). New `_BY_IRACING_PATH` index (L86-90). `_BY_LOWER` build loop (L97) extended to include `iracing_car_path`. `resolve_car()` (L108-115) probes `_BY_IRACING_PATH` first. `resolve_car_from_ibt()` (L139-160) rewritten with CarPath → CarScreenName → None dispatch (audit F5).
- **`track_model/ibt_parser.py`**: `IBTFile.car_info()` already returns `car_path` + `iracing_car_path` keys (W5.x). No edits — verified.
- **`watcher/service.py`**: `_detect_car_and_track` (L46-79) extracts CarPath, performs CarPath → CarScreenName → None dispatch, returns 5-tuple. New `_class_for_canonical` helper (L82-110) uses `SuspensionArchitecture` to map cars to "GTP" / "GT3" class labels. `WatcherService.__init__` (L131-156) accepts `class_filter` kwarg (case-normalised). `_handle_new_ibt` (L201-251) consumes new tuple shape, applies `class_filter` after `car_filter`. `IngestResult` gains `iracing_car_path` field.
- **`watcher/monitor.py`** (L23-35): `TODO(W8.2-followup, audit F14)` doc block above `_STABLE_WAIT_S` documenting the GT3 short-session caveat. Cosmetic — no behavioural change.
- **`desktop/config.py`** (L60-66): `AppConfig.class_filter: list[str]` field (default `[]`). Round-trips through existing save/load via `__dataclass_fields__` filter.
- **`desktop/app.py`** (L66): `class_filter=self.config.class_filter or None` passed to `WatcherService`.
- **`teamdb/sync_client.py`** (L120-128): `TODO(W9.x, audit F12)` doc block above `pulled_models` PK — architecture-collision problem deferred to W9.x (parallels F7 server-side `EmpiricalModel.UniqueConstraint` deferred by W8.1).

**Deferred:**
- F12 (sync_client `pulled_models` PK extension): W9.x.
- F14 (monitor short-session window): no real-world false-stable reported; cosmetic TODO only.

**Tests:** 24 new across 6 classes. CarPath resolution via `_BY_IRACING_PATH`; `resolve_car("bmwm4gt3")` returns GT3; `_detect_car_and_track` resolves GT3 from CarPath even with misleading CarScreenName; `class_filter` round-trip; legacy config without `class_filter` loads cleanly.

## Combined-state pipeline behavior

After all 8 waves COMPLETE (Wave 1 + Wave 2 + Wave 3 + Wave 4 + Wave 5 + Wave 6 + Wave 7 + Wave 8):

- **GT3 IBT through `pipeline.produce`** runs cleanly through Step 1 → Step 6.
  - Step 1: `_solve_balance_only` returns `RakeSolution` with target balance hit, NaN L/D, `mode="balance_only_search"`.
  - Step 2: `HeaveSolution.null()` with step1's dynamic RH propagated, `present=False`.
  - Step 3: `CornerSpringSolution` with real `front_coil_rate_nmm` + real `rear_spring_rate_nmm` (frequency-isolation).
  - **Step 4: `ARBSolution` with size-label live tuning** — search rotates from baseline. Porsche 992 RR LLTD targets the empirically-correct 0.45 (was over-targeting at 0.499).
  - Step 5: Geometry runs (consumes step3 + step4).
  - **Step 6: Dampers respect per-car polarity + range.** GT3 L/R adjustments collapsed to per-axle averages (no silent .sto write loss).
- **Setup writer (`output/setup_writer.py`)** writes a valid `.sto` for all 3 sampled GT3 cars (BMW M4 GT3 EVO, Aston Vantage GT3 EVO, Porsche 911 GT3 R / 992) — well-formed XML, all required CarSetup_* fields present, no GTP-only fields leaked, per-car YAML divergences honoured. **iRacing schema round-trip not yet validated** — manual driver-side QA required.
- **`output/garage_validator.py`** now never mutates GT3 step data — all four fixers (`_clamp_step3`, `_fix_slider`, `_fix_front_rh`, `_fix_torsion_bar_defl`) early-return for GT3.
- **`output/report.py`** renders 4 corner spring rates instead of "Heave F: 0 N/mm" garbage on GT3. BumpRubberGap and splitter are placeholders until garage state plumbing.
- **`car_model/cars.py:GarageRanges`** has new `bump_rubber_gap_*_mm` / `splitter_height_mm` fields; 3 GT3 stubs populated with driver-bracketed ranges.
- **Analyzer setup_reader** parses GT3 YAML setup data correctly (was silently falling to BMW GTP path or "unknown"). `sto_binary` recognises GT3 STO filenames; `sto_adapters` returns car-specific adapter_name; `setup_schema` describes GT3 fields via `_GT3_KNOWN_FIELD_MAP`.
- **Analyzer extract** skips heave-deflection extraction for GT3 (was reading per-corner channels and emitting bogus heave_bottoming_events counts).
- **Analyzer diagnose** skips heave-bottoming alarms for GT3 (was firing phantom critical-severity "stiffen heave spring" recommendations).
- **Analyzer causal_graph** routes GT3 sessions through corner-spring nodes (`front_corner_spring_too_soft`, etc.) instead of GTP heave nodes; `applicable_nodes(car)` filters per architecture.
- **`lltd_measured` alias bug**: alias-write removed from `extract.py`. The geometric `roll_distribution_proxy` is still computed and exposed; the misnamed `lltd_measured` field stays None going forward (display-layer consumers degrade gracefully via truthiness checks).
- **Fuel constants** (`damper_solver`, `stint_model`) now derive per-car: GT3 cars (100/104/106 L) get correct stint analysis and damper corner-mass; no silent 89L BMW-GTP fallback.
- **Legal-search (`legal_space.py`)** drops heave/third/torsion axes for GT3.
- **Modifier object** no longer carries dead GT3 heave-floor values.
- **Calibration gate** correctly reports Step 2 as `not_applicable` for GT3, Step 3+ as `weak` (real coil range / ARB size labels but rear motion ratio still placeholder = 1.0).

## Recommended next batch

**W9.1 + W9.2 in parallel** — closes Wave 9 fully and reaches the project's "all unblocked work shipped" state.

| Unit | Files | Effort | Why |
|---|---|---|---|
| **W9.1** | `webapp/` GT3 car list + conditional setup display panels (hide heave/third for GT3), `__main__.py` + `pipeline/__main__.py` argparse choices, `validation/{run_validation,objective_calibration}.py` GT3 support tier rows | 30 h | User-facing surface. webapp car selector hardcodes 5 GTP cars; SETUP_GROUP_SPECS pins heave/third/torsion rows that don't apply to GT3. CLI subcommands carry GTP-only `--car` choices. Validation report support-tier classifier hardcoded to 3 GTP pairs. |
| **W9.2** | 3 GT3 regression baselines (`tests/fixtures/baselines/{bmw_m4_gt3_spielberg,aston_vantage_gt3_spielberg,porsche_992_gt3r_spielberg}_baseline.sto`), parameterize `tests/test_setup_regression.py`, `CLAUDE.md` GT3 section, `skill/per-car-quirks.md` GT3 quirks, `docs/calibration_guide.md` GT3 onboarding | 32 h | Test + docs hardening. Baselines lock the W4.x .sto write outputs; parameterized regression tests catch any future drift on GT3 cars. Docs orient new contributors. |

Combined ~62 h, mostly disjoint files. Closes Wave 9 fully.

After Wave 9, only **W10.1** (E2E smoke + 7 remaining GT3 cars: Mercedes AMG, Acura NSX, Lamborghini Huracán, McLaren 720S, Mustang, Corvette Z06, Audi R8 LMS — ~80 h+) remains. W10.1 is gated on per-car IBT collection (each new car needs at least one IBT to pin its YAML schema + CarPath; varied-spring sweeps for BMW M4 GT3 are also needed to light up the W7.2 regression fits).

Alternative batch: **W9.1 alone** (~30 h) if you'd rather defer baselines/docs. Stand-alone W9.2 is cheaper (~32 h) and could land first to lock the current `.sto` outputs against drift.

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

- 805 tests pass (was 295 before this Phase 2 work began per CLAUDE.md 2026-04-11 entry; +287 from Waves 1–6, +36 from Wave 7.1+8.1, +49 from Wave 7.2+8.2 = 372 new GT3-specific tests).
- 33 skipped (mostly fastapi-dependent webapp tests + 1 new skip on `W63BuildObservationGT3Tests`).
- 1 deselected: `tests/test_run_trace.py::test_support_tier_mapping` — pre-existing data-dependent failure (BMW dataset has 26 sessions but test asserts ≥30); confirmed unchanged across all batches.
- 0 NEW regressions from any of the 21 shipped units (W7.2 ships as scaffolding; counts as 1 unit but no actual regression fits until IBT data lands).

## Branch strategy reminder

All Wave work lands on `claude/merge-audits-wave1-DDFyg`, which mirrors `gt3-phase0-foundations` plus the 12 audit merges. When this branch is ready to promote, fast-forward `gt3-phase0-foundations` to its tip (or merge with `--ff-only`).
