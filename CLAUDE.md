# MISSION CONSTRAINTS — READ BEFORE ANY CHANGE

This codebase has 6 inviolable principles documented in [MISSION.md](MISSION.md). Every change must comply. The full text and rationale lives in MISSION.md; the headline rules are:

1. Every lap is data — no best-lap-only collapsing
2. Physics-first — every recommendation regression-derived or labeled estimate
3. No hardcoded fallbacks — per-car fields required, not optional
4. Continuous learning — confidence tiers (high/medium/low/insufficient)
5. Coupled evaluation — parameter changes propagate to dependents
6. Corner-by-corner causal — per-corner-phase impact in every recommendation

Violations are tested by `tests/test_mission_compliance.py`. Read MISSION.md for forbidden vs required patterns.

---

# GTP / GT3 Setup Builder — Physics-Based Setup Calculator for iRacing

## Project Goal
Build a physics-first setup solver for iRacing's GTP/Hypercar class **and (since 2026-04-27) the GT3 class** that searches only legal garage states and explains why a setup should work. The current authoritative implementation target is BMW M Hybrid V8 at Sebring International Raceway; Porsche 963 at Algarve is calibrated for Steps 1-5 (Step 6 needs `zeta_is_calibrated=True`). Ferrari, Cadillac, and Acura paths remain partial or exploratory until more telemetry and garage-truth coverage exists. GT3 (BMW M4 GT3 EVO, Aston Martin Vantage GT3 EVO, Porsche 911 GT3 R 992) ships through the pipeline end-to-end after the Wave 1–8 work, but calibration is currently **intercept-only**: real regression fits need varied-spring IBT sweeps at a single track (W7.2 unlocks once the data lands).

## Current Codebase Status (2026-04-27)

- **GT3 Phase 2 — 12 audit PRs merged + Wave 1–9 COMPLETE + GT3 hot-fix (2026-04-27):** All 12 audit PRs (#103–#114, ~329 findings, ~511 h estimated) merged into `claude/merge-audits-wave1-DDFyg` (mirrors `gt3-phase0-foundations`). **21 of 22 work units complete (~505 h shipped, ~6 h remaining ≈ 99% of total).** All 9 of the first 9 waves shipped; only **W10.1** (E2E + 7 remaining GT3 cars, gated on per-car IBT capture) remains. W7.2 ships as scaffolding (intercept-only fits until varied-spring IBT data lands). The branch carries the full Phase 2 audit corpus under `docs/audits/gt3_phase2/` plus a [`IMPLEMENTATION_STATUS.md`](docs/audits/gt3_phase2/IMPLEMENTATION_STATUS.md) tracking doc updated after every batch. Suite: **830 passed, 33 skipped, 1 deselected, 0 new regressions** (pre-existing `test_support_tier_mapping` data-dependent failure unchanged). +397 new GT3-specific tests vs the 2026-04-11 baseline of 295.
  - **Wave 1 — Foundation invariants (DONE; commit `74b9509`, +746/-18, 13 files):**
    - **W1.1 calibration gate dispatch (`car_model/calibration_gate.py`):** `check_step()` early-returns `not_applicable=True` when `step==2 and not car.suspension_arch.has_heave_third`; `_data_prior_step` property replaces the GTP-only class constant (GT3 cascade is `{3:1, 4:3, 5:4, 6:3}` — Step 2 dropped); `_build_subsystem_status()` filters deflection sub-models by `has_heave_third` and emits a `heave_third_deflection` N/A subsystem on GT3; `CalibrationReport` gains `not_applicable_steps` and `solved_steps` excludes N/A; `format_header` renders a "NOT APPLICABLE STEPS" section; `summary_line` distinguishes N/A from blocked; `instructions_text` returns a one-liner for N/A steps. F1–F8 from `docs/audits/gt3_phase2/calibration-gate.md` implemented; F9–F11 (cosmetic) deferred.
    - **W1.2 `step2.present` consumer wiring:** `HeaveSolver.__init__` raises `ValueError` when `car.suspension_arch.has_heave_third` is False — defense-in-depth so any future GT3 caller fails loudly. Four downstream consumers honour `step2.present`: `params_util.solver_steps_to_params` guards heave/third writes (PU1, PU2), `candidate_search._extract_target_maps` returns `{}` for the step2 key (CS3), `decision_trace.build_parameter_decisions` accepts `car=` and explicitly skips heave/third/torsion specs on GT3 (DT1, DT2 — vs silently-caught AttributeError), and `solve_chain.py` / `bmw_rotation_search.py` / `pipeline/produce.py` thread `car=` through the call sites.
    - **W1.3 registry GT3 entries:** `_CAR_REGISTRY` gains 3 GT3 `CarIdentity` entries (`bmw_m4_gt3`, `aston_martin_vantage_gt3`, `porsche_992_gt3r`) so `resolve_car("BMW M4 GT3 EVO")` no longer silently returns the GTP BMW. `_car_name()` substring loop reordered (longer GT3 names match first). `CAR_FIELD_SPECS` gains 3 empty stub dicts so `get_car_spec("bmw_m4_gt3", ...)` returns `None` instead of falling through to the BMW spec — the writer fails loudly until W4.1/W4.2 populate the real PARAM_IDS. `detect_car_adapter()` recognises the GT3 `BumpRubberGap` / `CenterFrontSplitterHeight` fingerprint and emits `logger.warning` on BMW fallback. `tests/test_registry.py:TestSupportedCarNames` count assertion bumped 5 → 8.
  - **Wave 2 — Solver chain unblocks (PARTIAL; commit `2cbf1e8`, +1152/-134, 12 files):**
    - **W2.1 Step 2 (heave) skipped for GT3 in solve_chain (DONE):** Architecture-aware Step 2 dispatch in 5 orchestrators — `_run_sequential_solver` (solve_chain.py:400), `_run_branching_solver` (:593), `materialize_overrides` rebuild_step23 (3 sites at :1170, :1228, :1343), CLI `solve.py:434`, `pipeline/reason.py:3089` + `:3384`, plus `BMWSebringOptimizer.__init__` (full_setup_optimizer.py:113). On GT3, `step2 = HeaveSolution.null(...)` carries Step 1's dynamic RH targets, no `HeaveSolver` constructed. `reconcile_solution` early-returns when `step2.present is False`. Analyzer guards (`_step2_real`) added in `solve.py` for stint/sector/sensitivity/multi_speed/bayesian/space-mapper. `_all_steps_present` honours `step2.present` via `_step_satisfied`. Legal-search `baseline_params` drops `front_heave_spring_nmm` / `rear_third_spring_nmm` axes for GT3.
    - **W2.2 Step 1 (rake) balance-only mode for GT3 (DONE):** `AeroSurface` gains a `has_ld: bool` flag (read from parsed `balance_only` metadata) — `_ld_interp` is not constructed and `lift_drag()` returns NaN cleanly instead of raising on the all-NaN GT3 grid. `RakeSolver.solve()` dispatches on `car.suspension_arch.has_heave_third` to a new `_solve_balance_only` path (mode `"balance_only_search"`) that searches both axes for target balance with no L/D objective, no front-pinning, no vortex-burst constraint. `heave_spring=None` guards via new `_heave_perch_*_baseline` helpers cover R-1..R-4. `reconcile_ride_heights` early-returns for GT3. NaN-safe L/D propagation through `RakeSolution.ld_cost_of_pinning` (NaN, not 0.0) and `solver/objective.py` (default `ld_ratio=3.0` → constant offset across GT3 candidates, no signal/penalty, GTP scoring exactly preserved). `car_model/cars.py:rh_excursion_p99` gains a GT3 fallback (axle-share-of-sprung-mass + 2× corner-spring lower bound) — discovered during smoke testing.
    - **Combined state after W2.1 + W2.2:** GT3 IBT runs end-to-end through Step 1 + Step 2 without crashing.
  - **Wave 2.3 + Wave 3.1 (DONE; commit `c31f3be`, +984/-86, 7 files):**
    - **W2.3 Step 3 (corner spring) GT3 front-coil branch (DONE):** `CornerSpringModel` extended with `front_spring_range_nmm: tuple`, `front_spring_resolution_nmm: float`, `front_baseline_rate_nmm: float`, and `snap_front_rate()` helper. GT3 stubs populated: BMW M4 GT3 (190, 340) N/mm step 10 base 220; Aston (180, 320); Porsche 992 (170, 320). `CornerSpringSolution` extended with `front_coil_rate_nmm` + `front_coil_perch_mm`. Three-way front-architecture cascade in `solve()` and `solution_from_explicit_rates()`: GT3 paired-coil arm FIRST (because GT3 satisfies `front_torsion_c == 0.0` and would otherwise enter the legacy roll-spring branch), then GTP roll-spring, then GTP torsion bar. C-1 (heave-ratio clamp gated on `front_heave_nmm > 0`), C-2 (GT3 elif arm), C-3/C-4 (rear-rate dispatch on `rear_third_nmm > 0` with frequency-isolation; driver-anchor /0 guard), C-5 (mirror branching in explicit-rates path), C-6 (summary text "TOTAL AXLE WHEEL RATE" for GT3), C-7 (`_apply_lltd_floor` early-return for non-roll-spring), C-9 (`solve_candidates` enumerates `front_spring_range_nmm` step). C-8 (rear MR=1.0 placeholder) and C-11 (Ferrari preload-turns hook) deferred.
    - **W3.1 legal_space / modifiers / stint_model `heave_spring=None` guards (DONE):** `legal_space.py:_car_spring_refs` returns `(0.0, 0.0, rear_spring_ref)` sentinel for GT3; `compute_perch_offsets` early-returns `{}`; new `_GT3_EXCLUDED_KEYS` frozenset + `_tier_a_keys_for(car)` / `_perch_keys_for(car)` helpers filter heave/third/torsion search axes (LS1–LS5). `modifiers.py:_heave_min` / `_perch_baseline` extended: existing fallback fires for `car is None OR car.heave_spring is None`; all `front_heave_min_floor_nmm` / `front_heave_perch_target_mm` writes gated on `_has_heave_third` (MD2–MD4). `stint_model.py:analyze_stint` sets `base_heave_nmm = None` / `base_third_nmm = None` for GT3; `find_compromise_parameters` and `_compute_heave_recommendation` skip heave/third writes when None (ST5, ST6). Fuel-constant findings (ST1–ST4, ST7) tagged with TODO(W3.3) comments — display drift, not crash fixes.
    - **Combined state after W2.3 + W3.1:** GT3 IBT runs through Step 1 + Step 2 + Step 3 cleanly with real `front_coil_rate_nmm` (from spring range) and real `rear_spring_rate_nmm` (from frequency-isolation). Legal-search now correctly drops heave/third/torsion axes for GT3. Modifier object no longer carries dead heave-floor values.
  - **Wave 2.4 + Wave 3.2 (DONE; commit `aa0beda`, +990/-99, 8 files):**
    - **W2.4 Step 4 ARB blade encoding + Porsche LLTD target (DONE):** `ARBModel.blade_factor` short-circuits to 1.0 when `max_blade <= 1` (A-9; was returning 0.30 for `(1,1)`, scaling all GT3 paired-blade ARB lookups by 0.30). New `ARBModel.arb_direction: Literal["ascending","descending"]` field (forward-compat for Corvette inverted encoding). GT3 `measured_lltd_target` set per car: BMW M4 GT3 = 0.51 (FR baseline), Aston = 0.53, Porsche 992 = **0.45** (RR adjustment per audit lines 350-365). `ARBSolution` extended with `rarb_size_slow_corner` / `rarb_size_fast_corner`. New `_front_spring_roll_stiffness` helper (A-7 — extracted from 3-way copy-paste). New `_iter_blade_options(blade_count)` returns `[1]` for GT3 (single-blade-per-label) else `range(1, count+1)` (GTP). New `_neighbor_size` walks `rear_size_labels` ±1/±2 for slow/fast tuning, honouring `arb_direction`. A-2 zero-front-rate assertion (loud-fail safety net). All blade loops in `solve()`/`solve_candidates()`/`solution_from_explicit_settings()` dispatch through helper. GT3 generic `car_specific_notes` branch (A-8). A-8 per-car wording deferred; Corvette descending application deferred (no stub yet).
    - **W3.2 Damper polarity + range per-car (DONE):** `DamperModel.click_polarity: Literal["higher_stiffer","lower_stiffer"]` field added (default preserves BMW). Porsche 992 GT3 R damper range confirmed at `(0, 12)` (driver IBT clicks reach 12). `legality_engine.py` (L215-276): polarity-dispatched 4-way hierarchy check (LS comp / LS rbd / HS comp / HS rbd); penalty wording changed from numeric to semantic ("softer than" vs "<") so it reads correctly under either polarity. `candidate_search.py` (L714-761): hardcoded `lo=0, hi=20` replaced with `car.damper.{ls_comp,ls_rbd,hs_comp,hs_rbd}_range`; polarity sign inversion ensures "stiffer" intent always means stiffer regardless of car convention (CS6, CS7). `damper_solver.py` (L676-687): GT3 L/R averaging — `lf_hs_comp_adj = rf_hs_comp_adj = (lf+rf)//2` so iRacing's per-axle GT3 garage doesn't silently lose L/R divergence on .sto write (F2 partial). L3 Audi/McLaren/Corvette CarModel stubs deferred to W10.1 (no stubs exist yet — polarity field wired so they'll just need `click_polarity="lower_stiffer"` + per-car ranges).
    - **Combined state after W2.4 + W3.2:** GT3 IBT runs through Step 1 → Step 6 cleanly. Step 4 ARB output rotates from baseline; Porsche 992 RR LLTD targets the empirically-correct 0.45. Damper output respects per-car polarity + range; GT3 dampers written per-axle (no silent L/R loss).
  - **Wave 4.1 + Wave 3.3 (DONE; commit `9f746be`, +994/-132, 7 files):**
    - **W4.1 BMW M4 GT3 setup writer (DONE):** New `_BMW_M4_GT3_PARAM_IDS` dict in `output/setup_writer.py:506-583` (~45 entries, verbatim from audit `output.md:294-365`) registered in `_CAR_PARAM_IDS` (L593). `is_gt3` flag at `write_sto:944` with per-block gates. GTP writes skipped on GT3 (heave/third + perch L997-1083, torsion bar, pushrod offsets L989-995, per-corner damper writes 16ch → per-axle 8ch at L1256-1294, roll-damper L1301, rear-3rd damper L1329, ARB size string L1229/1234). GT3 writes added: 4 corner spring rates from `step3.front_coil_rate_nmm` / `step3.rear_spring_rate_nmm` paired (L1085-1103), `BumpRubberGap`×4 + `CenterFrontSplitterHeight` placeholder, TC/ABS as indexed string `"n (TC)"` / `"n (ABS)"` (L1364-1378). `_validate_setup_values` gains `_is_gt3_validation` guard (L641-665). `output/garage_validator.py:_clamp_step2` accepts `car=` kwarg and early-returns on GT3 (L284-302). Writes well-formed XML; iRacing schema round-trip not yet validated (W4.3).
    - **W3.3 Fuel constants generalized (DONE):** `solver/damper_solver.py:444+1030` `solve()` and `solution_from_explicit_settings()` default `fuel_load_l` changed from 89.0 to None; raises `ValueError` with car name + max capacity if caller forgets it (F3, F4). All 9 caller sites already passed explicit `fuel_load_l`, 0 caller updates needed. `solver/stint_model.py`: `HeaveRecommendation` gains `full_fuel_l` field (L113); display string now reads from car-specific value (ST1, L181-187). `compute_fuel_states` and `analyze_stint` default `fuel_levels_l` derived per-car: `[fuel_capacity_l, midpoint, fuel_stint_end_l]` (ST3, L295-307 / L740-746) — BMW M4 GT3 (100, 10) → `[100, 55, 10]`. `solver/scenario_profiles.py:144-149` SP3 TODO comment. `PUSHROD_CORRECTION_MM_PER_KG` retagged TODO(W7.2) — small drift, real fix needs IBT pushrod-vs-fuel sweeps.
    - **Combined state after this batch:** GT3 IBT writes a valid `.sto` file end-to-end for BMW M4 GT3 EVO (well-formed XML, all required CarSetup_* fields present, no GTP-only fields leaked). All hardcoded 89L assumptions removed from solver chain — GT3 cars get correct stint analysis and damper corner-mass. **Setup writer for Aston Vantage GT3 + Porsche 992 GT3 R still raises** (no PARAM_IDS dicts yet — that's W4.2). iRacing schema round-trip validation pending (W4.3).
  - **Wave 4.2 + Wave 5.2 (DONE; commit `d124944`, +1307/-24, 9 files):**
    - **W4.2 Aston + Porsche 992 setup writer (DONE):** `_ASTON_MARTIN_VANTAGE_GT3_PARAM_IDS` (output/setup_writer.py:604-688) and `_PORSCHE_992_GT3R_PARAM_IDS` (L691-768) dicts verbatim from audit `output.md:367-518`, registered in `_CAR_PARAM_IDS`. Per-car GT3 sub-dispatch via `is_aston_gt3` / `is_porsche_gt3` flags (L1141-1142): BMW/Aston use blade-keyed front ARB → `ArbBlades` / `FarbBlades`; Porsche uses integer-keyed `ArbSetting` / `RarbSetting`. Rear toe BMW/Aston per-wheel; Porsche paired (`Chassis.Rear.TotalToeIn`, avg of LR/RR). TC label suffix dispatch — "n (TC)" BMW, "n (TC SLIP)" Aston, "n (TC-LAT)" Porsche. Aston-only fields: `EpasSetting`/`ThrottleResponse`/`EnduranceLights`/`NightLedStripColor`. Porsche-only: `ThrottleShapeSetting`/`DashDisplayPage`. Porsche fuel in `Chassis.FrontBrakesLights.FuelLevel` (NOT Rear). Roll/3rd-damper guard now respects `is_porsche_gt3` (phantom-write protection).
    - **W5.2 Analyzer setup_reader/schema/sto_adapters GT3 dispatch (DONE):** `analyzer/setup_reader.py` gains `_read_gt3_setup` per-car YAML helper (front section name, ARB encoding, fuel location, rear toe shape), `_parse_indexed_label` for "X (TC SLIP)" → int X, GT3 dataclass fields (front_corner_spring_nmm, bump rubber gaps × 4, splitter, ARB settings, EPAS/throttle), `from_ibt` GT3 early-return. `analyzer/setup_schema.py` gets `_GT3_KNOWN_FIELD_MAP` (122 lines, ~35 entries × 3 cars) + `get_known_fields(car)` dispatcher + `_manual_constraints` `hasattr()` guards. `analyzer/sto_adapters.py` GT3 branch returns `"<canonical>_v3_container"` instead of generic fallback. `analyzer/sto_binary.py:_CAR_HINTS` gains 3 GT3 entries (longer-match-first). TODO(W5.3) markers added on `extract.py:96` (lltd_measured alias / A16), `extract.py:1443+1450` (heave deflection / A17), `diagnose.py:179` (heave bottoming alarms / A18). Audit corrections discovered: Porsche `ThrottleShapeSetting` actually under `Chassis.InCarAdjustments` (audit said `FrontBrakesLights`); is plain int not indexed-label.
    - **Combined state after W4.2 + W5.2:** GT3 IBT writes a valid `.sto` for all 3 sampled GT3 cars with per-car YAML divergences honoured. Analyzer parses GT3 YAML correctly. `sto_binary` recognises GT3 STO filenames.
  - **Wave 4.3 + Wave 5.3 (DONE; commit `2792f3b`, +1127/-110, 10 files):**
    - **W4.3 Output validators + GT3 garage validator + report (DONE):** `output/garage_validator.py` `validate_and_fix_garage_correlation` GT3 short-circuit; `_clamp_step3` / `_fix_slider` / `_fix_front_rh` / `_fix_torsion_bar_defl` early-return for GT3 (audit O17/O19/O20/O21). `output/report.py` gains `_is_gt3(car)` helper; 9 sites gated for GT3 architecture (audit O28-O33) — engineering report renders 4 corner spring rates from `step3.front_coil_rate_nmm` / `step3.rear_spring_rate_nmm` (paired LF==RF, LR==RR) instead of "Heave F: 0 N/mm" garbage. `car_model/cars.py:GarageRanges` gains `bump_rubber_gap_front_mm` / `bump_rubber_gap_rear_mm` / `bump_rubber_gap_resolution_mm` / `splitter_height_mm` / `splitter_height_resolution_mm` fields; 3 GT3 stubs populated with driver-bracketed ranges from audit `output.md:540-555` (BMW F=15/R=52, Aston F=17/R=54, Porsche F=30/R=51). iRacing schema round-trip validation deferred to manual driver-side QA (no offline iRacing XSD copy in repo; W4.3 NOTE block at `setup_writer.py:1131`).
    - **W5.3 Analyzer extract / diagnose / causal_graph GT3 (DONE):** `analyzer/extract.py:1462` `_extract_heave_deflection` gated on `car.suspension_arch.has_heave_third` (A17). `analyzer/diagnose.py` (A16, A18): dropped `state.lltd_measured` fallback reads in `Diagnosis(...)` ctor and `_check_balance` roll-proxy; 7 gate-points on heave-bottoming predicates so GT3 sessions no longer fire phantom "stiffen heave spring" critical alarms. `analyzer/causal_graph.py` (A19): `CausalNode.gtp_only` / `gt3_only` flags; `heave_too_soft` / `heave_too_stiff` / `third_too_soft` tagged `gtp_only=True`; 3 new GT3 nodes (`front_corner_spring_too_soft` / `front_corner_spring_too_stiff` / `rear_corner_spring_too_soft`) with 4 new edges; new `applicable_nodes(car)` + `_is_node_applicable(node, car)` helpers; `analyze_causes(problems, car=None)` filters by architecture during traversal AND disambiguation. `lltd_measured` alias-write removed from extract; field stays None going forward (display-layer consumers degrade gracefully via truthiness checks). A20-A38 cosmetic findings deferred per audit's effort estimate; A34 recommend.py setattr dispatch is W6/W7 territory.
    - **Combined state after W4.3 + W5.3:** GT3 IBT runs end-to-end through pipeline → solver → writer → report cleanly. Engineering report renders 4 corner spring rates. Analyzer no longer fires phantom heave-bottoming alarms; causal graph routes GT3 through corner-spring nodes. `garage_validator` never mutates GT3 step data.
  - **Wave 5.1 + Wave 6.1 (DONE; commit `eb8c2a0`, +1510/-229, 9 files):**
    - **W5.1 Pipeline produce/reason/report GT3 conditional (DONE):** `pipeline/produce.py` gains module-level `_is_gt3_car(car)` and `_step2_present(step2)` helpers (L67-90). Alias map drops heave/third on GT3 (F1, L93-138); m_eff `car.heave_spring is not None` guard (F2, L460-470); analyze_stint heave/third = None on GT3 (F4, L1011-1019); JSON `step2_heave` sentinel (F7, L1672-1681); delta card + solver_predictions heave fields gated (F9, L1814-1823, L1918-1949); GT3 top-n column schema swap LF-Spg/RR-Spg (F10, L1376-1429). `pipeline/reason.py` gains heave-floor modifier section gate (F13, L2174-2185), analyze_stint passthrough (F4, L3427-3437), JSON sentinel (F7, L3777-3784); F11/F12 verified already W2.1-handled. `pipeline/report.py` gains `_is_gt3` helpers (L47-72), CURRENT vs RECOMMENDED 4 corner spring rows (F14, L513-553), FRONT HEAVE TRAVEL BUDGET architecture-aware gate (F15, L603-614), `GarageSetupState.from_solver_steps` GT3 short-circuit (F16, L243-265). `pipeline/scan.py` gets F23 TODO marker.
    - **W6.1 Objective + sensitivity GT3 guards (DONE):** `solver/objective.py` F-O-1 m_eff GT3 fallback uses half-axle sprung mass (`total_mass × weight_dist / 2.0`, L884-905); F-O-2 excursion physics dispatches to corner-spring rate on GT3 (L926-959); F-O-3 `_compute_lltd_fuel_window` early-returns `(0, 0, 0)` (L686-696, GT3 corner coils are constant-rate); F-O-4 `_compute_platform_risk` heave-deflection block gated on `_hsm is not None` (L1840-1882); F-O-5 `_heave_calibration_uncertainty_penalty_ms` / `_heave_realism_penalty_ms` / envelope ratio penalty all contribute 0 for GT3 (L417-431, L2072-2086). `solver/sensitivity.py` F-S-1 `analyze_step2_constraints` early-return on `not step2.present` (L224-231); F-S-3/4/5 `_heave_block_runnable` gate (L555-580). `solver/laptime_sensitivity.py` F-LT-1/2 heave/third sensitivity functions early-return on `not step2.present` (L394-407, L598-611, L893-911, L919-936). **Quality bias noted (F-O-7, deferred):** `front_wheel_rate` falls through to `car.corner_spring.front_roll_spring_rate_nmm = 0.0` on GT3 → LLTD `k_front` term gets 0 → silent candidate underweighting until W6.x wires GT3 corner-spring axis. F-O-6 through F-O-15 + F-LT-3 through F-LT-11 deferred (DEGRADED + COSMETIC).
    - **Combined state after this batch:** GT3 IBT runs end-to-end through pipeline → solver → objective scoring → writer → report cleanly. Pipeline orchestrator drops heave/third keys from JSON output and reports 4 corner spring rates. Objective scoring runs on GT3 without crashing. Sensitivity reporting (constraint proximity + lap-time) skips heave rows on GT3; GTP rows preserved. **Wave 5 COMPLETE (3/3 units). Wave 6 W6.1 done (1/3 units).** GT3 LLTD scoring still has a quality bias (F-O-7) — non-crashing but uniformly biased candidates until W6.x.
  - **Wave 6.2 + Wave 6.3 (DONE; commit `4fcb2c6`, +833/-16, 7 files):**
    - **W6.2 STEP_GROUPS dispatch + KNOWN_CAUSALITY GT3 (DONE):** `learner/delta_detector.py` gets `step_groups_for_arch(arch)` (L52-100) — GT3 returns `step3_corner_combined` with 5 GT3 setup parameters; GTP keeps legacy `step2_heave` + `step3_springs`. Backward-compat: `STEP_GROUPS` constant preserved. 23 new GT3 KNOWN_CAUSALITY entries (L273+): front + rear `corner_spring_nmm` (8 effects each), `bump_rubber_gap_mm` (3 + 2 effects), `splitter_height_mm` (1 effect). Reverse-direction entries auto-generated. delta-classification thresholds extended at L434.
    - **W6.3 Empirical models + observation + clusters GT3 (DONE):** `learner/empirical_models.py` adds `_fit_corner_spring_to_variance(obs_list, models, axle)` (L347-391); wired into `fit_models()` alongside heave fitter. `learner/observation.py:build_observation` detects GT3 architecture structurally (front_corner_spring_nmm > 0 AND front_heave_nmm == 0); setup dict populates 5 GT3 keys via getattr-with-defaults. `learner/setup_clusters.py:setup_parameters_for_arch(arch)` (L74-105) returns GT3 parameter list with corner-spring × 2, bump_rubber_gap × 2, splitter_height; `DEFAULT_SETUP_PARAMETERS` preserved for backward compat. `learner/ingest.py` GT3 setup keys added to field-extraction list. `learner/recall.py` per-arch dispatch deferred to W7.x where solver-side feedback consumers actually consume the new GT3 corrections.
    - **Combined state after this batch:** `learner.ingest` on a GT3 IBT now produces an Observation with the correct GT3 setup keys; `delta_detector` recognises corner-spring deltas and generates physical hypotheses (was silently dropping every GT3 hypothesis at L478). Empirical fits accumulate for front + rear corner_spring → RH std relationships. Setup clusters use the GT3-correct parameter list for fingerprinting. **Wave 6 COMPLETE (3/3 units).**
  - **Wave 7.1 + Wave 8.1 (DONE; commit `1d071b8`, +1261/-42, 8 files):**
    - **W7.1 GT3 GarageSetupState + GarageOutputModel (DONE):** `car_model/garage.py:from_current_setup` (L89-104) gains `csm/hsm is not None` guards around the indexed-car decode block — was crashing on GT3 (`heave_spring=None`, `corner_spring` may be None). Substantive GT3 path (L105-161, L271-300) was already in place from earlier waves. `GarageOutputModel` (L378-389) gets 5 new GT3 default fields (`default_front_corner_spring_nmm=220.0`, `default_rear=180.0`, `default_front_bump_rubber_gap_mm=15.0`, `default_rear=50.0`, `default_splitter_height_mm=20.0` — BMW M4 GT3 mid-range). `default_state(car=None)` (L477-512) is architecture-aware: GT3 cars receive a state with corner-spring + bump-rubber + splitter populated; GTP fields stay 0.0. `car_model/auto_calibrate.py:_setup_key()` (L123-133) gains 5 new tuple slots for GT3 fingerprint fields. `getattr`-with-defaults preserves GTP backward-compat — legacy `CalibrationPoints` get 0.0 in new slots without collisions. Per-car defaults from fitted regressions and `CalibrationPoint` GT3 schema deferred to W7.2.
    - **W8.1 DB schema migration + per-arch aggregator (DONE):** `teamdb/models.py:CarDefinition` (L208-216) gets 3 new nullable columns — `iracing_car_path` (indexed), `bop_version`, `suspension_arch` (indexed). `Observation` (L259-272) gets `suspension_arch VARCHAR(48) NOT NULL DEFAULT 'gtp_heave_third_torsion_front'`, `bop_version`, `iracing_car_path`. New composite index `ix_observations_team_arch_track`. `migrations/0001_gt3_phase2.sql` (new) is the raw SQL migration script (project uses raw SQL, not Alembic) with `ADD COLUMN IF NOT EXISTS` idempotence + GTP backfill (Porsche 963 patched to `gtp_heave_third_roll_front`); operator MUST run `psql -f` against Cloud SQL before next server image deploy (`Base.metadata.create_all` does NOT apply ALTER statements). `teamdb/aggregator.py` full rewrite: `aggregate_observations(observations, car, track, *, suspension_arch=None)` (L155-186) partitions by `suspension_arch` so GTP empirical fits never get corrupted by GT3 uploads (audit F3); F10 fix imports canonical `car_model.registry.track_key` (vs `track.lower().split()[0]` which broke "Red Bull Ring" / "WeatherTech Raceway Laguna Seca"); F11 per-arch tier thresholds (GT3: 4/10/20 vs GTP: 5/15/30, with `TODO(W9.1)` for empirical recalibration). `server/routes/observations.py:ObservationCreateRequest` Pydantic gains `suspension_arch` / `bop_version` / `iracing_car_path` fields with backward-compat defaults; POST handler validates `suspension_arch` matches the team's existing `CarDefinition.suspension_arch` (raises HTTPException(400) on mismatch).
    - **Combined state after W7.1 + W8.1:** `GarageSetupState` carries GT3 paired-coil + bump-rubber + splitter fields end-to-end. `DirectRegression` can fit on `inv_front_corner_spring` etc. without dropping features. `_setup_key()` distinguishes GT3 IBTs that vary only by front corner-spring (was collapsing). Team server's DB schema + aggregator partition GT3 vs GTP observations.
  - **Wave 7.2 + Wave 8.2 (DONE; commit `c262efa`, +1078/-32, 10 files):**
    - **W7.2 auto-calibrate GT3 scaffolding (DONE — intercept-only until IBT data):** `CalibrationPoint` (auto_calibrate.py:228-244) gets 5 new GT3 fields aligned with W6.3 / W7.1 conventions (front_corner_spring_nmm, rear_corner_spring_nmm, front_bump_rubber_gap_mm, rear_bump_rubber_gap_mm, splitter_height_mm). New `_track_slug` helper (L368-382) wraps registry.track_key with Spielberg / Red Bull Ring aliases (audit #12). `fit_models_from_points` GT3 guard (L1193-1202). `_UNIVERSAL_POOL` (L1297-1322) gets 9 new GT3 features; `_FRONT_AXIS_NAMES` +4; `_REAR_AXIS_NAMES` +5 (including splitter_height for aero balance shift). `apply_to_car` GT3 short-circuit (L2239-2269) returns "intercept-only" applied note with TODO(W10.1) marker for the future write target (`car.corner_spring.front_baseline_rate_nmm`). `_GT3_PROTOCOL_HINT` template + `_car_protocol_hint(car)` dispatcher (L2790-2891). CLI `--car` choices (L3427-3438) pulled from `sorted(_CARS.keys())` so all 8 cars accepted. Std-filter at L1310 auto-drops zero-variance features per call site so GTP IBTs silently drop GT3 features and vice-versa. `car_model/registry.py:_TRACK_ALIASES` gets Spielberg / Red Bull Ring aliases. **Actual non-intercept regression fits gated on W10.1 IBT capture (varied-front-coil sweeps at the same track).**
    - **W8.2 watcher + desktop GT3 CarPath detection (DONE):** `car_model/registry.py` populates `iracing_car_path` for all 8 entries (L62-77); new `_BY_IRACING_PATH` index (L86-90); `_BY_LOWER` build loop extended; `resolve_car()` probes `_BY_IRACING_PATH` first (L108-115); `resolve_car_from_ibt()` rewritten with CarPath → CarScreenName → None dispatch (L139-160, audit F5). `IBTFile.car_info()` already returned `iracing_car_path` from W5.x — verified. `watcher/service.py:_detect_car_and_track` (L46-79) extracts CarPath, performs the same dispatch, returns 5-tuple. New `_class_for_canonical` helper (L82-110) using `SuspensionArchitecture` maps cars to "GTP" / "GT3" labels. `WatcherService.__init__` accepts `class_filter` kwarg (L131-156). `_handle_new_ibt` consumes new tuple shape, applies class_filter (L201-251). `IngestResult.iracing_car_path` field. `desktop/config.py:AppConfig.class_filter: list[str]` (L60-66, default `[]`, round-trips through save/load). `desktop/app.py:66` passes `class_filter` into `WatcherService`. `watcher/monitor.py` gets a `TODO(W8.2-followup, audit F14)` block above `_STABLE_WAIT_S` (cosmetic — no behavioural change). `teamdb/sync_client.py:120-128` gets `TODO(W9.x, audit F12)` block above `pulled_models` PK (architecture-collision problem deferred to W9.x).
    - **Combined state after this batch:** Wave 7 + Wave 8 BOTH COMPLETE. Watcher + desktop ingest GT3 IBTs end-to-end via stable CarPath identifier. `class_filter=["GT3"]` lets users restrict ingestion to a single class. auto_calibrate scaffolding accepts GT3 IBTs without crashing, applies a documented "intercept-only" calibration result, waits for varied-spring IBT data to produce real regression fits.
  - **Wave 9.1 + Wave 9.2 + GT3 hot-fix (DONE; commits `4ec42b7` + `d2f6785` + (W9.2 pending), ~750 lines code + ~312 lines docs):**
    - **GT3 hot-fix (commit `4ec42b7`):** User ran the pipeline against a real BMW M4 GT3 IBT at Road Atlanta and surfaced two recommendation-quality issues from W2.2 placeholders flagged "PENDING_IBT verification". `min_front_rh_static` bumped from 50.0 → 60.0 mm for all 3 GT3 cars (driver IBT shows 70+ mm static; search was converging to floor and recommending 22 mm below driver). Camber baselines updated from GTP defaults (-2.9 / -1.9) to driver-loaded values from Spielberg IBTs: BMW M4 GT3 -4.0/-2.8, Aston -4.0/-2.8 (rear PENDING), Porsche 992 -4.0/-3.0. Localized to `car_model/cars.py`. No GTP changes.
    - **W9.1 webapp + CLI + validation accept GT3 (DONE; commit `d2f6785`, +681/-55, 11 files):** `webapp/services.py` `_GTP_SETUP_GROUP_SPECS` / `_GT3_SETUP_GROUP_SPECS` constants + `setup_group_specs_for(car_canonical)` dispatcher + `list_supported_cars(class_filter=None)` + GT3 PARAM_EXPLANATIONS. `webapp/templates/runs_new.html` `<optgroup>` Jinja loop. `webapp/app.py` `Form(...)` required (was Form("bmw")). `__main__.py` + 3 submodule entry points: `_car_choices()` / `_car_help()` helpers from `sorted(_CARS.keys())`; 10 sites total updated. `validation/run_validation.py` `_SUPPORT_TIER_REGISTRY` (10 entries; 4 GTP + 6 GT3 "exploratory"); `_target_samples(car=, track=)` parameterised. `validation/objective_calibration.py` `load_observations(car_filter=, track_filter=)` parameterised. `solver/scenario_profiles.py` F20 TODO expanded.
    - **W9.2 GT3 regression baselines + docs (DONE):** 3 GT3 baseline `.sto` fixtures regenerated against current pipeline output (BMW M4 GT3 / Aston Vantage GT3 / Porsche 992 GT3 R at Spielberg). Parameterized regression test was pre-existing — only the stale fixtures were refreshed (front_RH 50→60 mm, aero balance calc 65→70 mm drift from W2.2/W3.x rake refactors after they were last generated). `CLAUDE.md` (+31 lines): GT3 architecture subsection in "Important Implementation Details", GT3 pipeline-usage block, GT3 reference files. `skill/per-car-quirks.md` (+178 lines): 3 per-car GT3 sections (BMW M4 GT3 EVO, Aston Vantage GT3 EVO, Porsche 911 GT3 R 992) with verified driver-loaded baselines from the 3 Spielberg session-info YAMLs; class-architecture umbrella section; ToC reorganized GTP / GT3 / Cross-Car. `docs/calibration_guide.md` (+103 lines): "GT3 Onboarding (added 2026-04-27)" section with subsystem-status matrix, varied-spring IBT capture protocol, per-car spring-range targets, currently-shipped table, 4th-car onboarding workflow, gotchas, audit corpus pointer. Title updated to "GTP / GT3".
    - **Combined state after this batch:** Webapp + CLI + validation reports all GT3-aware. Engineering docs carry GT3 sections with audit corpus pointers. Regression test locks the W4.x writer outputs for all 3 sampled GT3 cars. **Wave 9 COMPLETE.**
  - **Remaining Phase 2 plan (~6 h, 1 of 22 units gated on IBT):** Only **W10.1** (E2E smoke + 7 remaining GT3 cars: Mercedes AMG, Acura NSX, Lambo Huracán, McLaren 720S, Mustang, Corvette Z06, Audi R8 LMS) remains — gated on per-car IBT capture (each car needs at least one IBT to pin its YAML schema + CarPath; varied-spring sweeps for BMW M4 GT3 are also needed to light up the W7.2 regression fits). The branch is at "all unblocked work shipped" status — every code path that doesn't require new IBT data has been touched. See [`docs/audits/gt3_phase2/SYNTHESIS.md`](docs/audits/gt3_phase2/SYNTHESIS.md) and [`docs/audits/gt3_phase2/IMPLEMENTATION_STATUS.md`](docs/audits/gt3_phase2/IMPLEMENTATION_STATUS.md).
  - **GT3 IBT corpus:** 4 IBTs in `data/gt3_ibts/` (gitignored) — BMW M4 GT3 EVO at Spielberg + Nürburgring (byte-identical setups, can't back-solve aero compression), Aston Martin Vantage GT3 EVO at Spielberg, Porsche 911 GT3 R (992) at Spielberg. Need varied-spring sweeps at the same track to unblock W7.2 auto-calibrate.

## Previous Status (2026-04-11)

- **Full codebase audit — 25 failing tests fixed, 6 production bugs corrected (2026-04-11):** Codex review identified broken contracts (Ferrari setup writer, track path resolution, Acura registry gaps). Fixed in PR #57: (1) `output/setup_writer.py` — `validate_and_fix_garage_correlation` now runs **before** Ferrari index conversion; fixes Ferrari HeaveSpring writing `"8"` instead of `"3"`. (2) `output/garage_validator.py` — `_clamp_step3` now guards against snapping index-space values to physical discrete OD values (the two domains are incompatible for Ferrari — range 0-18 indices vs discrete 19.99-23.99 mm); also uses `min()` not `[0]` for robustness. (3) `car_model/registry.py` — `track_slug()` no longer uses `_TRACK_ALIASES`; only `track_key()` does. All garage model files on disk are named `sebring_international_raceway.json` not `sebring.json`; the alias-based slug was causing `GarageModelBuilder` to write/read from the wrong path. (4) `car_model/cars.py` — Ferrari `torsion_arb_coupling` `0.15 → 0.0`; measured LLTD is empirically constant (range 0.508–0.514, σ=0.0016) regardless of bar changes — coupling is negligible. (5) `solver/predictor.py` — `rear_power_slip_p95` backward-compat alias added on `PredictedTelemetry`. (6) `car_model/setup_registry.py` — 9 missing Acura settable fields added (`front_roll_hs_slope`, `rear_3rd_{ls,hs}_{comp,rbd}`, `front_roll_spring_nmm`, `front_roll_perch_mm`, `front_arb_setting`, `rear_spring_nmm`). Test fixes: support tier expectations updated to actual session counts; Porsche rear RH mean tolerance 0.50→0.75mm (honest post-overfitting model R²=0.605); data-dependent tests now skip gracefully when observation files absent from checkout. Result: **295 passed, 17 skipped, 0 failures** (was 25 failures).

## Previous Status (2026-04-10)

- **Physics-aware feature pools with universal-pool fallback (2026-04-11):** Forward selection in `_select_features()` is physics-blind — it picks whatever feature minimizes LOO RMSE. With small datasets (8–36 setups), this causes **cross-axis pollution**: Ferrari `front_ride_height` was picking `inv_rear_spring` (coefficient **-21934**) and `rear_spring` for FRONT RH; BMW `front_shock_defl_static` was picking `fuel_x_inv_third` and `rear_camber`; Porsche `front_ride_height` was picking 6/12 features from the rear axis. Fix: split `_UNIVERSAL_POOL` into `_FRONT_POOL` (front-axis + global features only) and `_REAR_POOL` (rear-axis + global), then route each `_fit_from_pool()` call to the physics-aware pool. **Critical addition:** because per-output pools alone CAUSED regressions on Porsche (LOO 3x→68x) and Acura (R² 0.75→0.09) where cross-axis features were serving as effective regularization or genuine chassis-flex coupling, `_fit_from_pool()` now also accepts `fallback_pool=_UNIVERSAL_POOL` and keeps whichever fit has the lower LOO RMSE. Result: Ferrari `front_ride_height` R²=0.50→**0.72**, Ferrari `front_shock_defl_static` R²=0.93→**0.97**, BMW `front_shock_defl_static` LOO ratio 3.3x→**2.4x**, Porsche/Acura unchanged (universal fell back). Universal calibration sweep: Ferrari `Front Static RH 2.15mm→1.06mm` (was FAIL), Ferrari `Heave Slider 4.16mm→2.02mm` (was FAIL), Acura `Third Defl Static 7.20mm→1.88mm` (was FAIL) — 3 pre-existing FAILs fixed, 0 new regressions, 0 new test failures.
- **🚨 Systemic overfitting fix + pipeline crash guards (2026-04-10):** `_select_features()` threshold was too loose (`n_samples >= n_features + 5`), allowing 18-feature models on as few as 23 samples. LOO/train RMSE ratios were catastrophic: Ferrari 559x, Porsche 272x, BMW 48Mx, Acura 559Mx. Fixed by aligning with the project's own `_min_sessions_for_features()` 3:1 ratio: `max_features = n_samples // 3`, threshold = `3 * n_features`. Defense-in-depth: `_fit()` now marks models uncalibrated when LOO/train > 10x despite R² ≥ 0.85. All 4 cars refit — Ferrari worst LOO/train 579x→1.7x, Porsche 272x→3.2x, BMW 48Mx→30x (1 model caught by guard), Acura 559Mx→2.7x. Additional fixes: (1) `produce.py` uses `track_key()` instead of `.split()[0]` for track name resolution ("autodromo"→"algarve"). (2) `track_support` removed from Step 1 requirements — calibration is car-dependent, not track-dependent. (3) `garage_validator.py` null check moved before Ferrari index conversion. (4) `report.py` null guards for step1-step6 in CURRENT vs RECOMMENDED and HEAVE TRAVEL BUDGET sections.
- **Full codebase audit and enhancement round 2 (2026-04-10):** Deep audit continuation — 7 additional fixes on top of round 1. Key changes: (15) `candidate_search.py`: ARB blade range now uses `car.arb.rear_blade_count` instead of hardcoded BMW (1,5); added garage_ranges warning when missing for non-BMW cars. (16) `auto_calibrate.py:_fit()`: underdetermined system guard — rejects fits where n_samples ≤ n_parameters (returns `is_calibrated=False`). (17) `bmw_coverage.py:_car_name()` default changed "bmw"→"unknown" to prevent silent BMW assumption. (18) `produce.py`: 3 remaining silent exception handlers now log (calibration load, track profile comparison, veto clusters). (19) `auto_calibrate.py`: 4 remaining silent exception handlers now log. (20) `objective.py`: parallel_wheel_rate ×0.5 documented (per-corner = axle_rate/2). (21) `candidate_search.py`: logger added for BMW-fallback warnings.
- **Full codebase audit and enhancement round 1 (2026-04-10):** 3-agent audit (solver workflow, calibration system, code quality) — 20 findings, 0 critical bugs. Solver physics and 6-step workflow are correct. Key fixes: (1) Aero balance over-correction in coupling refinement removed (`solve_chain.py:832`). (2) `zeta_is_calibrated` default fixed from True→False in `damper_solver.py:476`. (3) Tyre vertical rate warning added to `objective.py` when excursion degrades to suspension-only. (4) LLTD offset bounds-checked to [0.30, 0.75] in `arb_solver.py`. (5) Phantom Porsche roll damper backward-compat fixed — only applies when `has_roll_dampers=True`. (6) Ferrari rear torsion 3.5x error now gated as `uncalibrated` (blocks Step 3). (7) Speed-dependent LLTD gap eliminated (120-180 kph → unified 150 kph boundary). (8) 11 `except Exception: pass` handlers in `solve_chain.py` replaced with `logger.debug()`. (9) Auto-calibrate overfit warning: LOO vs training RMSE check + sample-to-feature ratio warning. (10) Confidence weight property added to `StepCalibrationReport` (1.0/0.7/0.5/0.0) and surfaced in JSON output. (11) Hardcoded Windows paths removed from tests. (12) Cadillac calibration stubs added. (13) `decision_trace.py` None handling fixed. (14) Ferrari setup_writer fallback now warns.
- **Codebase audit and enhancement (2026-04-09):** Full 3-agent audit (solver workflow, calibration system, code quality). Key fixes: (1) `objective.py` tyre_vertical_rate_nmm was referencing a non-existent CarModel field — always None, meaning tyre compliance was never included in excursion calculations. Now uses per-axle `tyre_vertical_rate_front/rear_nmm`. (2) Calibration gate cascade fixed: Step 5 now depends on Step 4 (was Step 3), matching actual data flow where Step 5 consumes `step4.k_roll_total`. (3) `CornerSpringSolution.rear_wheel_rate_nmm` property added — eliminates 8 manual MR^2 conversion sites. (4) Weak-upstream propagation: downstream steps now know when input data has weak calibration. (5) Ferrari rear torsion 3.5x error now gated as `weak` in calibration gate. (6) Dead LLTD proxy code removed. (7) 19MB repomix-output.xml + 486 generated JSON files removed from git. (8) BMW-default fallbacks replaced with direct attribute access in 12 solver files.
- Workflow map: `IBT -> track/analyzer -> diagnosis/driver/style -> calibration_gate -> solve_chain/legality -> driver-anchor pass -> report/.sto -> webapp`
- **🚨 LLTD phantom proxy bug found and fixed (2026-04-08):** The field `lltd_measured` stored in `data/calibration/<car>/models.json` and consumed as `measured_lltd_target` was actually `analyzer/extract.py:roll_distribution_proxy` — a **geometric constant** (`= front_RH_diff × tw_f² / total_moment`) that collapses to `t_f³/(t_f³+t_r³)` for a rigid chassis and is **insensitive to spring stiffness**. Verified across 5 Porsche/Algarve IBTs with rear stiffness varying 100–300%: proxy varied 0.5047→0.5056 (spread **0.09 pp**). A real LLTD measurement would shift 5–15 pp. The "11 pp model gap" the ARB solver was chasing was apples-to-oranges. Fix: `auto_calibrate.py:1360` LLTD-from-proxy block disabled, `data/calibration/porsche/models.json` cleared, Porsche `cars.py:measured_lltd_target=0.521` set explicitly from the OptimumG/Milliken physics formula. **The model's k_front/k_total computation may now be correct in physics; we have NO direct LLTD measurement from IBT and cannot disambiguate without true wheel-force telemetry.** See "LLTD epistemic gap" in Known Limitations.
- **Driver-anchor pattern (2026-04-08):** When the driver loads a setup into iRacing, the IBT session_info captures it. Several solvers now read `current_setup` and prefer driver-loaded values as soft anchors when the model's recommendation is within tolerance OR when the model is admittedly broken/unverifiable. This is **explicit, provenance-tracked, and never lap-time-driven** — see Key Principle 11. Anchors live in: `solver/heave_solver.py` σ-cal sticky (front_heave + rear_third), `solver/corner_spring_solver.py` direct R_coil, `solver/arb_solver.py` LLTD-fallback ARB blade, `solver/diff_solver.py` coast/drive/preload, `solver/supporting_solver.py` TC gain/slip, `solver/candidate_search.py` skip-scale-when-anchored guard.
- **σ-calibration architecture (2026-04-08):** `solver/heave_solver.py:min_rate_for_sigma()` now accepts `current_rate_nmm` + `current_meas_sigma_mm` (driver-loaded rate + IBT-measured rear/front_rh_std). Computes `cal_ratio = meas_σ / model_σ_at_current_rate` (clamped [0.5, 2.0]) and translates the user σ-target to model space. A **sticky pre-check** returns the current rate when its model σ ≤ effective target + 0.05 mm — this prevents 1-step gradient drift. The σ MODEL is still physics; the TARGET is driver-anchored. Validated against Porsche/Algarve newest IBT (driver rate=160, σ_meas=7.6, model_σ=7.34, cal_ratio=1.036, sticky returns 160 exactly).
- **Per-axle roll damper architecture (2026-04-08):** `DamperModel` now carries `has_front_roll_damper` and `has_rear_roll_damper` flags (in addition to `has_roll_dampers`). Porsche 963 (Multimatic) has FRONT roll damper but NO rear roll damper — rear roll motion is implicit in the per-corner LR/RR shocks. Acura ARX-06 (ORECA) has BOTH. Setup writer (`output/setup_writer.py:1069`) and damper solver (`solver/damper_solver.py:790`) gate on these flags so Porsche stops emitting phantom `CarSetup_Dampers_RearRoll_*` XML IDs that don't exist in the iRacing schema. Backward-compat: cars with `has_roll_dampers=True` and neither per-axle flag set assume both (legacy Acura).
- **Strict calibration gate (2026-04-07):** `car_model/calibration_gate.py` classifies every subsystem as `calibrated`, `weak`, or `uncalibrated` and surfaces R² for every regression model. R² thresholds: `R2_THRESHOLD_BLOCK = 0.85`, `R2_THRESHOLD_WARN = 0.95`. The gate distinguishes:
  - **`calibrated`**: real measurement, R² ≥ 0.85 OR auto-cal validated. Step runs cleanly.
  - **`weak`**: R² < 0.85 OR manual override that auto-cal *contradicts*. Step still runs (legacy call sites assume steps exist) but is flagged `[~~]` and a `WEAK CALIBRATION DETECTED` banner is printed prominently. JSON output carries `calibration_provenance` and `calibration_weak_steps`.
  - **`uncalibrated`**: no measurement at all. Step blocks and outputs CLI calibration instructions.
  - **Cascade rule:** only TRUE blocks (uncalibrated, dependency-blocked) propagate to downstream steps. Weak blocks do NOT cascade. Dependency chain: `{2→1, 3→2, 4→3, 5→4, 6→3}` — Step 5 (Geometry) cascades from Step 4 (ARBs, because geometry uses `step4.k_roll_total`), Step 6 (Dampers) cascades from Step 3 (wheel rates).
- **Compliance physics (2026-04-07):** Static ride heights and deflections under aero load follow spring **compliance (1/k)**, not stiffness (k). The RH model and deflection model now use `1/heave`, `1/rear_third`, `1/rear_spring` features. For Porsche this took rear RH R² from **0.61 → 0.94**, deflection R² from **0.67 → 0.97**, with sub-half-mm prediction error across the operating range. BMW uses linear terms (its data is fit by a different functional form). Both forms coexist in the same `RideHeightModel`/`DeflectionModel` classes.
- **Provenance tracking (2026-04-07):** `CalibrationGate.provenance()` returns a JSON-friendly dict mapping every subsystem to `{status, source, confidence, r_squared, data_points, warnings}`. The pipeline embeds this in JSON output as `calibration_provenance` so the user can audit exactly where each value came from. The pipeline prints a `CALIBRATION CONFIDENCE — provenance per subsystem` block on every run.
- **Silent fallbacks partially removed (2026-04-07, extended 2026-04-09):** Dangerous `getattr(car, "field", bmw_default)` patterns in core solver steps have been replaced with direct attribute access. Files cleaned in Phase 1 (2026-04-07): `solver/objective.py`, `solver/sensitivity.py`, `solver/damper_solver.py`, `solver/stint_model.py`, `solver/rake_solver.py`, `solver/arb_solver.py`. Phase 2 (2026-04-09): `solver/legal_space.py` (BMW spring refs), `solver/diff_solver.py` (preload), `solver/modifiers.py` (heave minimum), `solver/heave_solver.py` (track fields), `solver/corner_spring_solver.py` (canonical_name), `solver/objective.py` (tyre_vertical_rate per-axle fix, vortex_excursion_pctile, torsion_arb_coupling), `car_model/calibration_gate.py` (weak_block direct access). **Remaining:** ~700 `getattr` calls in `solver/candidate_search.py` (188), `solver/bmw_rotation_search.py` (113), `solver/bmw_coverage.py` (78), `pipeline/reason.py` (69), and others. Most are legitimate optional-feature checks (car-type branching, sub-model access, telemetry field defaults), not physics-value fallbacks. The BMW-specific rotation/coverage files only run for BMW and are not cross-car risks.
- **Regression test safety net (2026-04-06):** `tests/test_setup_regression.py` runs the full pipeline against `tests/fixtures/baselines/bmw_sebring_baseline.sto` and `tests/fixtures/baselines/porsche_algarve_baseline.sto`. Every code change is verified to either preserve or intentionally update these fixtures. To regenerate after an intentional change, see the docstring in the test file.
- Scenario engine: `solver/scenario_profiles.py` defines `single_lap_safe`, `quali`, `sprint`, and `race`, and those profiles now drive `pipeline/produce.py`, `pipeline/reason.py`, `solver/solve.py`, preset comparison, and the webapp.
- Legal-manifold search: `--free`, `--explore-legal-space`, and `--legal-search` now mean "start from the pinned physics solve and search the full legal setup manifold". Accepted candidates must pass setup-registry legality, garage-output validation, and telemetry sanity checks. Legal search is gated on all 6 steps being present (not blocked by calibration).
- Current BMW/Sebring evidence: `99` observations, `~97` non-vetoed. Post-fix Pearson `~0.226`, Spearman `~-0.298`. Objective is improving but not yet authoritative.
- **Current calibration status (2026-04-10, post-overfitting-fix):**
  - **BMW/Sebring**: `calibrated` (6/6 steps run cleanly, 9 unique setups, 3 features/model, garage RMSE < 0.09mm). ARB has medium-confidence hand-calibration.
  - **Porsche/Algarve**: `calibrated` (5/6 steps — Step 6 blocked: `damper_zeta` uncalibrated in car model, needs `zeta_is_calibrated=True`). 36 unique setups, 7-12 features/model. Front RH: R²=0.999 LOO=0.078mm. Rear RH: R²=0.605 (weak, 7 features — honest after overfitting fix, was 0.983 with 18 overfit features). Aero compression from 24 sessions, LLTD target = **0.521 from OptimumG physics formula**.
  - **Ferrari/Hockenheim**: `partial` (Step 1 runs with weak RH model, Steps 2-6 blocked by `spring_rates` uncalibrated). 23 unique setups, 6-7 features/model. Front RH: R²=0.501 (honest after overfitting fix, was 0.999 with 18 overfit features — model needs more data). Garage RMSE 0.09-0.82mm across outputs. 6 contaminated BMW data points removed (2026-04-10).
  - **Acura/Hockenheim**: `partial` (Steps 1-3 runnable, Steps 4-6 blocked). 8 unique setups, 2 features/model. RH < 0.11mm, some deflections limited by rear torsion bar architecture.
  - **Cadillac/Silverstone**: `no data` (0 calibration points).
  - **Garage prediction architecture (2026-04-10):** `DirectRegression` class in `car_model/garage.py` evaluates fitted regressions directly from `GarageSetupState`, bypassing `DeflectionModel`'s rigid coefficient interface. Physics feature pool: 20 features (linear + compliance 1/k + pushrod² + fuel×compliance). `GarageSetupState.from_current_setup(setup, car=car)` handles indexed-car decoding (Ferrari/Acura indices → N/mm). See `CALIBRATION_GUIDE.md` for how to calibrate new cars.
- Current source-of-truth reports: `docs/repo_audit.md`, `docs/overhaul_plan_2026_04_06.md`, `validation/objective_validation.md`, `validation/calibration_report.md`.
- **Team tool deployed (2026-03-27):** Server live at `https://ioptimal-server-27191526338.us-central1.run.app`, team "SOELPEC Precision Racing" created (invite code `5a1c520b`), desktop app packaged at `dist/IOptimal/IOptimal.exe`. All 18 bugs fixed (12 original + 6 deployment). See `docs/team_tool_next_steps.md` for full deployment reference.
- **Acura ARX-06 onboarded (2026-03-30):** ORECA LMDh chassis with heave+roll damper architecture (not per-corner). Rear torsion bars, diff ramp angles, synthesized corner shocks from heave±roll telemetry. Pipeline functional end-to-end. Steps 1-3 runnable (aero compression calibrated, spring_rates calibrated), Steps 4-6 blocked by calibration gate (ARB/LLTD/geometry/damper uncalibrated). See `skill/per-car-quirks.md` Acura section for full calibration status.

## Architecture

### Core Modules

#### 1. `aero_model/` — Aerodynamic Response Surface
- Parse all 33 aero map spreadsheets (5 cars × 6-9 wing angles)
- Build interpolated surfaces: DF_balance(front_RH, rear_RH, wing_angle) and L_D(front_RH, rear_RH, wing_angle)
- For any ride height + wing combination, return: front DF, rear DF, total DF, drag, L/D, DF balance
- Support querying: "what ride height gives target DF balance X at wing Y?"
- Data format: rows = front RH (25-50mm), columns = rear RH (5-50mm), values = DF balance % and L/D

#### 2. `track_model/` — Track Demand Profile
- Parse IBT files to extract track characteristics:
  - Surface frequency spectrum (shock velocity histogram per sector)
  - Braking zone locations, entry speeds, deceleration demands
  - Corner speeds, lateral g demands, radius estimates
  - Speed profile (% of lap in speed bands)
  - Kerb locations and severity (ride height spike detection)
  - Elevation changes (from vertical g)
- Output a TrackProfile object that any solver can query

#### 3. `car_model/` — Vehicle Physical Model
- Per-car parameter definitions with valid ranges, units, and constraint relationships
- Mass, weight distribution, CG height, wheelbase, track width
- Suspension motion ratios (spring-to-wheel rate conversions)
- Tyre load sensitivity curves (derived from telemetry: grip vs vertical load)
- Parameter name mappings (BMW uses "TorsionBarOD", Ferrari uses indexed values, Porsche has roll springs)
- Hybrid system characteristics (deployment speed, power, front/rear)
- **Calibration gate** (`calibration_gate.py`): per-car, per-subsystem calibration status tracking. Checks whether each solver step's required subsystems are calibrated from real measured data. Blocked steps output calibration instructions instead of setup values. This enforces the rule: **never output a setup value from an uncalibrated model**.

#### 4. `solver/` — Constraint Satisfaction Engine
Follows the 6-step workflow. Each step has constraints and an objective:

**Step 1: Rake/Ride Heights**
- Input: target DF balance, car aero map, track speed profile
- Constraint: DF balance must match target at the track's median high-speed cornering RH
- Constraint: front RH must stay above vortex burst threshold for 99% of clean-track samples
- Objective: maximize L/D while meeting balance target
- Output: front RH, rear RH, pushrod offsets

**Step 2: Heave/Third Springs**
- Input: target ride heights from Step 1, track surface spectrum, car mass + aero loads
- Constraint: clean-track bottoming events < threshold (e.g., 5 per lap)
- Constraint: ride height variance (σ) below target at speed
- Objective: softest spring that meets bottoming constraint (maximize mechanical grip)
- Output: front heave rate, rear third rate, perch offsets

**Step 3: Corner Springs**
- Input: car mass, target roll stiffness distribution, track bump severity
- Constraint: combined roll + heave stiffness must control ride height under lateral load
- Constraint: must not bottom under combined lateral + longitudinal + vertical loading
- Objective: balance mechanical grip vs platform control
- Output: corner spring rates

**Step 4: ARBs**
- Input: target LLTD, car weight distribution, tyre load sensitivity
- Constraint: LLTD should be ~5% above static front weight distribution (OptimumG baseline)
- Objective: neutral steady-state cornering balance at the track's characteristic speed
- Output: front ARB, rear ARB baseline, recommended live RARB range

**Step 5: Wheel Geometry**
- Input: tyre model, corner speeds, lateral loads
- Constraint: camber must optimize contact patch across the roll range
- Constraint: toe must balance turn-in response vs straight-line drag/heat
- Output: camber F/R, toe F/R

**Step 6: Dampers**
- Input: track surface spectrum, spring rates, target transient response
- Constraint: p99 shock velocity should be controlled (not causing platform instability)
- Constraint: rebound/compression ratio ~2:1 at equivalent velocities
- Objective: fastest weight transfer rate that doesn't cause oscillation
- Output: all damper clicks (LS/HS comp/rbd, slope)
- NOTE: damper effects are speed-dependent. Low-speed corners and high-speed corners may need different reasoning.

**Supporting Parameters** (`solver/supporting_solver.py`):
- Brake bias: weight transfer baseline + driver trail braking adjustment + measured slip correction
- Diff preload: traction demand × driver throttle style + body slip correction (5–40 Nm)
- Diff ramps: coast from trail braking depth, drive from throttle progressiveness
- TC: gain/slip from rear slip ratio + driver consistency
- Tyre pressures: targeting 155–170 kPa hot window from measured hot data

**Solver Modifiers** (`solver/modifiers.py`):
- Feedback loop: diagnosis + driver style → adjust solver targets before physics runs
- DF balance offset (from speed gradient diagnosis)
- LLTD offset (from understeer/oversteer diagnosis)
- Heave floor constraints (from bottoming diagnosis)
- Damper click offsets + ζ scaling (from settle time diagnosis + driver smoothness)

**Aero Gradients** (`aero_model/gradient.py`):
- Central-difference ∂(DF balance)/∂(RH) and ∂(L/D)/∂(RH) at operating point
- Aero window: ± mm before 0.5% balance shift
- L/D cost of ride height variance (second-order curvature analysis)

#### 5. `analyzer/` — Telemetry Analysis & Diagnosis
- `extract.py` — Extract 60+ measured quantities from IBT (ride heights, shock vel, understeer, body slip, tyre thermals)
- `diagnose.py` — Identify handling problems from physics thresholds (6 priority categories: safety → grip)
- `recommend.py` — Generate physics-based setup change recommendations
- `setup_reader.py` — Parse current garage setup from IBT session info YAML
- `segment.py` — **Corner-by-corner lap segmentation**: detects corners (|lat_g| > 0.5g), computes per-corner suspension metrics (shock vel p95/p99, RH mean/min), handling metrics (understeer, body slip, trail brake %), speed classification (low/mid/high), and time-loss delta
- `driver_style.py` — **Driver behavior profiling**: trail braking depth/classification, throttle progressiveness (R² of linear ramp), steering jerk (smoothness), lap-to-lap consistency (apex speed CV), cornering aggression (g utilization). Produces a `DriverProfile` with style classification (e.g., "smooth-consistent", "aggressive-erratic")
- `report.py` — ASCII terminal report formatting (63-char width)

#### 6. `pipeline/` — Unified IBT→.sto Setup Producer
End-to-end pipeline that connects telemetry analysis to the 6-step solver:
```
IBT → extract → segment corners → driver style → diagnose
    → aero gradients → solver modifiers → 6-step solver
    → supporting params → .sto + JSON + engineering report
```
- `produce.py` — CLI orchestrator: `python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --sto output.sto`
- `produce.py` / `reason.py` now resolve a scenario profile, keep the base physics solve as the seed, optionally run legal-manifold search, and persist the selected candidate family plus decision trace.
- `report.py` — Engineering report: driver profile, handling diagnosis, aero analysis, 6-step solution summary, supporting parameters, setup comparison (current vs produced), confidence assessment
- `__main__.py` — Entry point for `python -m pipeline`

#### 7. `output/` — Setup File Generator
- Generate iRacing .sto setup files directly (BMW-specific CarSetup_* XML IDs)
- Generate human-readable setup reports with reasoning for each parameter
- Generate comparison reports (current setup vs solver recommendation)
- `write_sto()` accepts optional supporting parameter overrides (brake bias, diff, TC, pressures) via kwargs

#### 8. `learner/` — Cumulative Knowledge System
Treats every IBT session as an experiment. Extracts structured observations,
detects deltas between sessions, fits empirical models, and accumulates
knowledge that compounds over time.

```
IBT → analyzer pipeline → Observation (structured snapshot)
    → Delta detection (vs prior session: what changed, what resulted)
    → Empirical model fitting (corrections to physics from data)
    → Insight generation (recurring patterns, trends, sensitivities)
    → Knowledge store (persistent JSON in data/learnings/)
```

- `knowledge_store.py` — JSON-based persistent storage (observations, deltas, models, insights)
- `observation.py` — Extracts structured observation from one IBT analysis
- `delta_detector.py` — Compares consecutive sessions, finds setup→effect causality
- `empirical_models.py` — Fits lightweight regressions from accumulated data
- `recall.py` — Query interface: "what do we know about X?", corrections for solver
- `ingest.py` — CLI entry point: `python -m learner.ingest --car bmw --ibt session.ibt`
  - `--all-laps`: ingest every valid lap as a separate observation (1 IBT → N observations)

Key features:
- **Controlled experiment detection**: if only one solver step changed between sessions,
  causal confidence is high. Multi-change sessions get lower confidence.
- **Expanded causal knowledge**: `KNOWN_CAUSALITY` covers ~40 setup→effect pairs across
  all 6 solver steps plus supporting parameters. Unknown relationships are dropped (not
  stored at low confidence). Reverse-direction entries auto-generated.
- **Prediction-vs-measurement feedback loop**: pipeline stores solver predictions in each
  observation; `fit_prediction_errors()` computes exponentially-weighted corrections from
  the gap between predicted and measured values. Solver can query via `get_prediction_corrections()`.
- **Time decay**: recent observations carry more weight (0.95^days). 30-day-old sessions
  contribute ~22% vs 95% for yesterday's. Prevents stale data from dominating corrections.
- **Experiment gating for sensitivity**: lap time sensitivity only uses deltas with ≤2 setup
  changes (single-change weighted 1.0, two-change 0.5, multi-change excluded).
- **Empirical corrections**: measured roll gradient, LLTD, m_eff, aero compression
  accumulate and the solver can query them to refine its physics predictions.
  Minimum 5 sessions required for non-prediction corrections.
- **Lap time sensitivity**: tracks which parameters had the biggest lap time effect.
- **Recurring problem detection**: flags issues that appear in >50% of sessions.
- **Damper oscillation validation**: rear shock oscillation frequency extracted from
  telemetry; if >1.5× natural frequency, damper solver bumps ζ_hs_rear (0.14→0.21).

#### 9. `watcher/` — IBT Auto-Detection
- `monitor.py` — Filesystem event handler using watchdog; file stability check (3s no-growth)
- `service.py` — WatcherService orchestrates detection → ingestion → sync queue; car auto-detection from IBT headers

#### 10. `teamdb/` — Team Database & Sync
- `models.py` — SQLAlchemy 2.0 ORM (13 tables: Team, Member, Division, CarDefinition, Observation, Delta, EmpiricalModel, GlobalCarModel, SharedSetup, SetupRating, ActivityLog, Leaderboard, division_members)
- `sync_client.py` — Background push/pull with offline SQLite queue (~/.ioptimal_app/sync_queue.db), exponential backoff, 30s push / 300s pull intervals
- `aggregator.py` — Server-side empirical model fitting from team observations

#### 11. `server/` — Team REST API
- FastAPI app on Cloud Run (`server/app.py`), async SQLAlchemy with PostgreSQL (asyncpg)
- Auth: Bearer API key (SHA-256 hashed in Member.api_key_hash)
- Routes: `/api/team`, `/api/observations`, `/api/knowledge`, `/api/setups`, `/api/leaderboard`
- Deployed: `https://ioptimal-server-27191526338.us-central1.run.app`
- Dockerfile at project root (builds `server/` + `teamdb/`)

#### 12. `desktop/` — Desktop App
- `app.py` — Orchestrates watcher + sync + webapp; CLI entry point with `--no-tray`, `--bulk-import`
- `config.py` — AppConfig dataclass persisted to JSON (%APPDATA%/IOptimal/config.json)
- `tray.py` — System tray icon via pystray (pause watcher, sync now, status, quit)
- Packaged via PyInstaller: `dist/IOptimal/IOptimal.exe` (177 MB)

### Data Files
- `data/aeromaps/` — Raw xlsx files (provided)
- `data/aeromaps_parsed/` — Parsed JSON/numpy arrays
- `data/tracks/` — TrackProfile JSONs (built from IBT analysis)
- `data/cars/` — Car model definitions
- `data/telemetry/` — Reference IBT sessions for validation

### Validation Strategy
- Canonical validation lives in `validation/run_validation.py` and `validation/objective_calibration.py`.
- All evidence uses canonical registry-backed setup mappings (`validation/observation_mapping.py`) instead of stale aliases.
- Current authority is BMW/Sebring only: `73` observations, `72` non-vetoed, with objective correlation still weak enough that "optimal" claims are not yet allowed.
- Validation reports now track score correlation, top parameter correlations, signal usage, claim audit status, and scenario-aware recalibration metrics including holdout performance.
- Support tiers are explicit and enforced in documentation: BMW/Sebring `calibrated` (6/6), Porsche/Algarve `calibrated` (5/6, Step 6 blocked), Ferrari/Hockenheim `partial` (1/6), Acura/Hockenheim `partial` (3/6), Cadillac/Silverstone `no data`.

### Tech Stack
- Python 3.11+
- numpy/scipy for interpolation and optimization
- openpyxl for xlsx parsing
- Possibly React frontend for visualization (later phase)

### Key Principles
1. Physics first, not pattern matching. Every parameter value must be justified by a physical constraint.
2. The solver follows the 6-step workflow ALWAYS. No jumping to dampers before rake is set.
3. Speed-dependent reasoning. The same symptom at different speeds may require different solutions.
4. Uncertainty is OK. If the solver can't determine a value from physics, it says so and gives a range.
5. Validate against telemetry. Every prediction should be testable with an IBT file.
6. Driver-adaptive: different drivers on the same track should produce different setups.
7. **Calibrated or instruct, never guess.** If a model is not calibrated from real measured data for a specific car, the output must be calibration instructions — not a value derived from another car's coefficients, not a physics estimate, not a default presented as a recommendation. The calibration gate (`car_model/calibration_gate.py`) enforces this at every solver step.
8. **No silent fallbacks.** Every value the solver uses must come from one of: (a) measured data with R² ≥ 0.85, (b) first-principles physics computation, (c) car-specific hand calibration with explicit warning. The user explicitly asked for "no fallbacks to baselines or hardcoded values" — this is enforced via direct attribute access (no `getattr` with hardcoded defaults), strict gate classification, and the `WEAK CALIBRATION DETECTED` banner.
9. **Provenance over output.** Every solver run prints a `CALIBRATION CONFIDENCE` block that lists every subsystem with its source, R² (where applicable), and confidence label. JSON output carries the full provenance dict so the user can audit any value.
10. **Compliance physics for static loads.** Static ride heights and deflections under aero load follow `defl ∝ F/k` (compliance), not stiffness. Use `1/k` features in regressions for these models. This was the single biggest accuracy improvement of 2026-04-07.
11. **Driver-anchor as physics fallback, never lap-time.** When an internal model is admittedly broken (e.g., LLTD k_front/k_total can't be ground-truthed without wheel-force telemetry) OR when the model agrees with the driver-loaded value within tolerance, prefer the driver-loaded value as the recommendation **with explicit provenance** (`anchored to driver-loaded X`). This is NOT lap-time-driven — anchors trigger on σ-measurement, model self-test, or close-tolerance agreement, never on `if lap_time < X:`. The driver loading their best setup before each session creates an IMPLICIT lap-time signal, but the anchor logic does not consume lap_time. See `feedback_no_laptime_setup_selection.md` and the Phase 6/7 implementation in `solver/{heave,corner_spring,arb,diff,supporting}_solver.py`. **Honest naming**: when the anchor fires, the output line in step4/step6/etc. says "anchored to driver-loaded" so a reader can audit which values are model-derived vs driver-derived.

### Important Implementation Details

**Spring rate conventions (critical):**
- Front torsion bar: `CornerSpringSolution.front_wheel_rate_nmm` is already a wheel rate (MR baked into C*OD^4 formula, `front_motion_ratio=1.0` for all cars)
- Rear coil spring: `CornerSpringSolution.rear_spring_rate_nmm` is a RAW SPRING RATE. Must multiply by `car.corner_spring.rear_motion_ratio ** 2` to get wheel rate before passing to ARB/geometry/damper solvers.
- The ARB solver's `_corner_spring_roll_stiffness()` now expects wheel rates for both axles (no internal MR conversion).

**Aero compression is speed-dependent:**
- `AeroCompression` stores reference values at `ref_speed_kph` (230 kph)
- Use `comp.front_at_speed(speed)` / `comp.rear_at_speed(speed)` for V² scaling
- The rake solver and `solver/objective.py` use `track.aero_reference_speed_kph` (V²-RMS over speed bands ≥100 kph), NOT `median_speed_kph`. Median under-predicts compression by ~3 mm because compression is dominated by high-speed sections. Validated 2026-04-07 against 4 Porsche/Algarve IBTs: V²-RMS=200 kph for Algarve gives compression matching IBT-measured to within 1 mm both axles.

**solution_from_explicit_offsets must honor caller-provided static (2026-04-07):**
- `solver/rake_solver.py:solution_from_explicit_offsets()` previously recomputed static_front from `garage_model.predict()` with **baseline springs** (heave=180 default, etc.) regardless of what the caller had already chosen. When `materialize_overrides` (in solve_chain.py, called by the candidate generator) passed both `front_pushrod_offset_mm` AND `static_front_rh_mm` from a base solve that pinned static_front=30, the function was overwriting static_front with the baseline-spring prediction (~32.78 for Porsche), and `reconcile_ride_heights` then used that drifted value as a NEW target. Fix: when `static_front_rh_mm`/`static_rear_rh_mm` are explicitly provided, USE THEM directly. This was the single largest fix for the front pushrod / static drift in Phase 2.

**σ-calibration architecture (heave_solver.min_rate_for_sigma):**
- The synthetic σ model (`damped_excursion_mm` energy method) does NOT match IBT-measured rear/front_rh_std exactly. For Porsche/Algarve newest IBT at driver rate=160: model σ = 7.34 mm, IBT-measured = 7.6 mm. Gradient is also slightly off.
- `min_rate_for_sigma()` accepts optional `current_rate_nmm` and `current_meas_sigma_mm` (driver-loaded rate + IBT std). It computes:
  ```
  cal_ratio = current_meas_σ / model_σ_at_current_rate    # clamped [0.5, 2.0]
  effective_meas_target = min(user_target, current_meas_σ × target_margin)   # default margin 1.05
  effective_model_target = effective_meas_target / cal_ratio    # floored at 3 mm
  ```
- Then it searches for the minimum rate where model_σ ≤ effective_model_target.
- **Sticky pre-check**: if the current rate's model_σ is within 0.05 mm of the target, return the current rate directly (snapped to 10 N/mm). This prevents the gradient mismatch from drifting the recommendation 1 step softer than driver.
- Wired through both `_run_sequential_solver` and `materialize_overrides` paths in `solver/solve_chain.py` via `front_heave_current_nmm` + `rear_third_current_nmm` parameters.
- The σ MODEL is still physics-driven; the σ TARGET is driver-anchored when available. The driver's measured σ becomes "the σ the new setup must achieve or exceed".

**LLTD calibration target — physics formula, NOT IBT (2026-04-08):**
- `analyzer/extract.py:574-599` computes `roll_distribution_proxy` (aliased as `lltd_measured`) from `(front_RH_diff × tw_f²) / (front_RH_diff × tw_f² + rear_RH_diff × tw_r²)`. **This is NOT LLTD.** It is a geometric ratio that collapses to `t_f³/(t_f³+t_r³)` for a rigid chassis and is essentially insensitive to spring stiffness.
- Verified across 5 Porsche/Algarve IBTs varying R_third 160→320 N/mm and R_coil 150→180: proxy varied 0.5047→0.5056 (spread 0.09 pp).
- `auto_calibrate.py:1360` previously stored `mean(proxy)` as `models.measured_lltd_target` and the ARB solver used it as the calibration target. The "11 pp model gap" between true model LLTD (k_front/k_total) and the proxy was apples-to-oranges.
- **Fix**: `auto_calibrate.py:1360-1395` block disabled (`if False:`), `models.measured_lltd_target = None` for cars where this was the source. `cars.py` Porsche definition sets `measured_lltd_target = 0.521` explicitly from the OptimumG/Milliken physics formula `weight_dist_front + (tyre_sens/0.20)×0.05 + speed_correction`. The arb_solver's existing physics-fallback path computes the same formula when `measured_lltd_target` is None.
- **Open epistemic gap**: we still have NO direct LLTD measurement from IBT. iRacing doesn't expose individual wheel-load channels. Without wheel-force telemetry OR a controlled per-axle ARB lap-time correlation (10+ varied sessions), we cannot disambiguate three hypotheses: (A) OptimumG rule doesn't apply to GTP/Porsche tyres, (B) driver setup is suboptimal but lap time is still good, (C) one of the model's k_roll terms has a residual physics error. The ARB solver's driver-anchor fallback (Phase 6.6) currently fires for Porsche because model LLTD (0.391) is 13 pp below the OptimumG target (0.521). The anchor preserves driver Stiff/10 with HONEST justification ("physics target unverifiable, defer to driver-loaded value"), not the previous fake "model is broken" justification.

**Per-axle roll damper architecture:**
- `DamperModel` carries `has_front_roll_damper` and `has_rear_roll_damper` flags (in addition to the legacy `has_roll_dampers` boolean).
- **Porsche 963 (Multimatic)**: Front Heave (4 channels) + Front Roll (3 channels) + Left Rear corner (5 channels) + Right Rear corner (5 channels) + Rear 3rd (4 channels) = 21 channels. **No rear roll damper** — rear roll motion is implicit in the per-corner LR/RR shocks. `has_front_roll_damper=True`, `has_rear_roll_damper=False`.
- **Acura ARX-06 (ORECA)**: Front Heave + Front Roll + Rear Heave + Rear Roll. `has_front_roll_damper=True`, `has_rear_roll_damper=True`.
- The setup writer (`output/setup_writer.py:1069`) and damper solver (`solver/damper_solver.py:790`) gate roll damper writes/computation on the per-axle flag. Backward-compat: cars with `has_roll_dampers=True` and neither per-axle flag set assume both axles (legacy Acura behavior). Before this fix, Porsche was emitting phantom `CarSetup_Dampers_RearRoll_LsDamping/HsDamping` XML IDs that don't exist in iRacing's Porsche garage schema.

**GT3 architecture (`SuspensionArchitecture.GT3_COIL_4WHEEL`, added 2026-04-27 in Wave 1–8):**
- **Suspension topology:** 4 paired corner coils (LF==RF, LR==RR), no heave spring, no third spring, no torsion bar. `car.suspension_arch.has_heave_third == False` and `car.heave_spring is None` are the canonical invariants — every GTP-specific code path that touches heave/third gates on one of these.
- **Step dispatch:** the calibration gate cascade is `{3:1, 4:3, 5:4, 6:3}` — Step 2 (Heave/Third) is `not_applicable` and is skipped entirely. Step 1 (Rake) runs in **balance-only mode** (`AeroSurface.has_ld=False` for GT3 aero maps; no L/D objective, no front-pinning, no vortex-burst constraint). Step 3 (Corner Spring) takes the **GT3 paired-coil arm** (front coil from `front_spring_range_nmm`, rear coil from frequency-isolation off step1's targets). Steps 4 (ARB), 5 (Geometry), 6 (Dampers) run with per-car polarity / encoding overrides.
- **ARB encoding:** BMW M4 GT3 + Aston Vantage GT3 use **paired blades** (`ArbBlades` / `FarbBlades`), `ARBModel.blade_factor` short-circuits to 1.0 for `(1,1)` (was scaling by 0.30 silently). Porsche 992 GT3R uses **integer settings** (`ArbSetting` / `RarbSetting`, range 1–11). `_iter_blade_options(blade_count)` returns `[1]` on GT3 vs `range(1, n+1)` on GTP.
- **Damper polarity:** all 3 sampled GT3s use `click_polarity="higher_stiffer"` (same as BMW GTP). Porsche 992 GT3R range is `(0, 12)` (lower than BMW's `(0, 20)`). Per-axle averaging on .sto write — iRacing's GT3 garage schema is per-axle (8 damper channels) not per-corner (16 channels), so L/F+R/F dampers are averaged before writing.
- **Per-car LLTD targets:** BMW M4 GT3 = 0.51 (FR baseline), Aston Vantage GT3 = 0.53, Porsche 992 GT3R = **0.45** (RR adjustment per audit `solver-rake-corner-arb.md:350-365`). These bypass the OptimumG physics formula because the GT3 measured-LLTD evidence is more authoritative than the formula.
- **Setup writer per-car PARAM_IDS:** `_BMW_M4_GT3_PARAM_IDS`, `_ASTON_MARTIN_VANTAGE_GT3_PARAM_IDS`, `_PORSCHE_992_GT3R_PARAM_IDS` in `output/setup_writer.py`. Per-car YAML divergences honoured: TC label suffix `"n (TC)"` BMW / `"n (TC SLIP)"` Aston / `"n (TC-LAT)"` Porsche; rear toe paired (Porsche `Chassis.Rear.TotalToeIn`) vs per-wheel (BMW/Aston); fuel section `Chassis.FrontBrakesLights.FuelLevel` (Porsche) vs `Chassis.Rear.FuelLevel` (BMW/Aston).
- **Stable IBT identity via CarPath:** `IBTFile.car_info()` returns `iracing_car_path` (e.g. `bmwm4gt3`, `amvantageevogt3`, `porsche992rgt3`) which is the locale-independent `DriverInfo.Drivers[me].CarPath` from the IBT YAML. `resolve_car()` and `watcher.service._detect_car_and_track` probe CarPath FIRST before falling back to CarScreenName. This is why a single shared set of `BumpRubberGap` / `CenterFrontSplitterHeight` field names doesn't collide with the GTP BMW path.
- **Auto-calibrate scaffolding (W7.2, intercept-only):** `CalibrationPoint` carries 5 GT3 fields (`front_corner_spring_nmm`, `rear_corner_spring_nmm`, `front_bump_rubber_gap_mm`, `rear_bump_rubber_gap_mm`, `splitter_height_mm`). `_UNIVERSAL_POOL` includes 9 GT3 features. `apply_to_car` short-circuits with an "intercept-only" applied note until varied-spring IBT data unlocks real regression fits.
- **Aggregator partitioning (W8.1):** `teamdb.aggregator.aggregate_observations(... suspension_arch=...)` partitions GT3 vs GTP empirical fits so cross-class data never corrupts a same-named track key. `Observation` table gets a `suspension_arch VARCHAR(48)` column; backfill SQL in `migrations/0001_gt3_phase2.sql`.

**Static ride height models (RideHeightModel):**
- Front static RH is NOT sim-pinned — it varies with heave spring rate (compliance), front camber, pushrod, and perch.
- **Two functional forms coexist** in the same `RideHeightModel` class:
  - **BMW** (constant model): `front_static_rh ≈ 30.2` (LOO RMSE ≈ 0.031mm, 0 features — front RH barely varies across 9 setups)
  - **Porsche** (compliance, 12 features after overfitting fix): R²=0.9993, LOO RMSE = 0.078mm. Previously had 18 features with fake LOO=0.03mm from overfit (LOO/train was actually 271x).
- Rear model uses compliance for both spring and third spring on Porsche:
  - **BMW**: `rear = 48.96 + 0.226*pushrod + 0.139*heave_perch + 0.069*spring_perch`
  - **Porsche**: rear model now has 7 features (after overfitting fix), R²=0.605, LOO RMSE = 0.99mm. Previously 18 features with R²=0.98 but LOO/train was 85x (overfit). The honest model needs more data or different features to improve rear RH accuracy.
- The model carries both linear and compliance coefficient fields (`front_coeff_heave_nmm` AND `front_coeff_inv_heave`, `rear_coeff_third_nmm` AND `rear_coeff_inv_third`, `rear_coeff_rear_spring` AND `rear_coeff_inv_spring`). Each car uses whichever set its calibration data fits best.
- `auto_calibrate.py` feature selection now includes both `1/heave` and `1/spring` candidates and lets the regression pick whichever is non-zero. Feature selection uses a 3:1 sample-to-feature ratio (`max_features = n_samples // 3`, skip threshold = `3 * n_features`). Defense-in-depth: `_fit()` marks models uncalibrated when LOO/train > 10x despite R² ≥ 0.85.
- `apply_to_car()` zeroes ALL coefficients in `_FRONT_RH_COEFF_MAP` / `_REAR_RH_COEFF_MAP` before applying new values, so stale BMW defaults can never persist alongside fresh non-BMW calibration.
- `GarageOutputModel` was extended with `front_coeff_inv_heave_nmm`, `rear_coeff_inv_third_nmm`, `rear_coeff_inv_rear_spring_nmm` fields and uses them in both `predict_*_static_rh_raw()` and the inverse `*_pushrod_for_static_rh()` methods.
- Both models are reconciled after step2+step3 in `solver/rake_solver.py:reconcile_ride_heights()` (called from solve.py and produce.py).

**Deflection models (DeflectionModel):**
- Same compliance physics applies to spring deflection under aero load: `defl ∝ F/k`.
- `rear_spring_defl_static`, `third_spring_defl_static`, and `rear_shock_defl_static` now use `1/spring` + `1/third` + perches + pushrod features.
- For Porsche these models achieve R²=0.93-0.98 with 7-10 features (after overfitting fix). Previously showed R²=0.99+ with 18 features but LOO/train ratios of 78-95x indicated severe overfitting.
- `DeflectionModel` carries a `*_defl_direct` flag per submodel. When True, it uses the new compliance form; when False, it uses the legacy load-balance form. BMW continues to use legacy because its single-feature fits don't have compliance terms.
- `apply_to_car()` only sets `*_defl_direct=True` when the fitted model includes inverse features — avoids accidentally flipping BMW into the new path.

**Learner model ID convention:**
- Model IDs use first word of track name only: `{car}_{track_first_word}_empirical` (e.g., `bmw_sebring_empirical`)
- Both `ingest.py` and `recall.py` use `track_name.lower().split()[0]` for consistency

**Known limitations:**
- BMW/Sebring is the fully-calibrated car/track pair (6/6 steps). Porsche/Algarve has 5/6 steps (Step 6 blocked: `damper_zeta` needs `zeta_is_calibrated=True` set in car model).
- Other cars have partial calibration: Ferrari/Hockenheim 1/6 (Step 1 weak, Step 2 blocked by `spring_rates`), Acura/Hockenheim 3/6 (Steps 1-3 runnable, 4-6 blocked), Cadillac/Silverstone 0/6.
- **Garage prediction accuracy after overfitting fix (2026-04-10):** Previous claims of "<0.06mm" for Ferrari and "<0.07mm" for Porsche were on overfit models (18 features, LOO/train ratios 272-579x — models were memorizing training data). After fixing `_select_features()` threshold to 3:1 ratio and refitting: BMW <0.09mm (unchanged), Porsche front RH 0.078mm LOO but rear RH R²=0.60 (needs more data), Ferrari 0.08-0.82mm RMSE depending on output (front RH R²=0.50, needs more data). The honest models generalize to new setups; the old overfit models did not.
- The objective is improving but still not authoritative: current BMW/Sebring non-vetoed Spearman is `~-0.298` (improved from -0.06 after 2026-04-04 fixes). Holdout stability is not yet strong enough for automatic runtime weight application.
- Several BMW validation signals still lean on fallbacks for some rows (`front_excursion_mm`, `braking_pitch_deg`, `rear_power_slip_p95`, hot pressures, lock proxies), so some supporting heuristics remain lower confidence.
- Ferrari rear torsion bar is calibrated (C=0.001282, MR=0.612, 4-point fit, max 3.2% error). Corner spring and LLTD outputs are functional but need more observations (currently 9) to validate against lap time.
- `m_eff` empirical correction uses lap-wide statistics (not filtered to high-speed straights), causing overestimation. Treat as rough indicator.
- `min_sessions=5` gate for non-prediction learned corrections. Prediction-based corrections (from solver feedback loop) need only 3 sessions since they measure specific prediction errors.
- Knowledge store has no file locking — safe for single-user CLI but not concurrent access.
- **🚨 LLTD CALIBRATION GAP (2026-04-08):** `analyzer/extract.py:lltd_measured` is a misnamed alias for `roll_distribution_proxy` — a GEOMETRIC ratio (`(front_RH_diff×tw_f²)/(...+rear_RH_diff×tw_r²)`) that is **insensitive to spring stiffness**. We have **no real LLTD measurement** from iRacing IBT (no individual wheel-load channels). The ARB solver now uses the OptimumG/Milliken physics formula as the LLTD target, with a driver-anchor fallback when the model can't reach target. To upgrade to true LLTD calibration we need EITHER (a) wheel-force telemetry from iRacing's `LF/RF/LR/RR_LoadN` channels if/when exposed, OR (b) a controlled per-axle ARB lap-time correlation across 10+ varied-blade sessions on the same track. Current Porsche LLTD target = 0.521 (physics-derived), model says 0.391 with driver setup, 13 pp gap is REAL but un-attributable.
- High-speed m_eff filtering available via `front_heave_vel_p95_hs_mps` and `front_rh_std_hs_mm` (>200 kph only) but not yet used by the solver's m_eff correction — uses lap-wide stats.
- **ARB back-solve (auto_calibrate.py):** measures total roll stiffness per ARB config from roll gradient, but cannot split front/rear individually. The `models.status['arb_calibrated']` returns `True`/`False`/`None` based on a noise-floor check (False if predicted-vs-measured deltas disagree by >20%, None if signal is below the K_total noise floor). Porsche currently sits at `None` (signal-below-noise) which the gate maps to `MEDIUM` hand-cal — not weak.
- **Driver-anchor pattern caveats:** the anchors in `solver/{heave,corner_spring,arb,diff,supporting}_solver.py` reduce the solver's ability to RECOMMEND a setup substantially different from what the driver loaded. This is by design for the rear chain (where physics is unverifiable) but means that loading a fresh/unfamiliar setup will cause the solver to re-anchor on whatever is loaded. The anchors do NOT consume lap_time directly, but the driver's selection of "best so far" creates an implicit lap-time-anchored loop. Acceptable for current use; revisit if false-positive driver anchors block real solver improvements.
- **Porsche 963 (Multimatic chassis):** Real garage ranges (heave 150–600, third 0–800, rear spring 105–280, front ARB adj 1–13, rear ARB adj 1–16, roll spring 100–320). Damper architecture: Front Heave (4 channels) + Front Roll (3 channels) + Left Rear corner (5 channels) + Right Rear corner (5 channels) + Rear 3rd (4 channels) = 21 channels total. **No rear roll damper** — `has_rear_roll_damper=False` (per-axle flag set 2026-04-08). Roll perch offset (14–16) not modeled. Individual L/R rear spring perch offsets (-150 to +150) not modeled.
- **Trailing rear pushrod gap on newest IBT (2026-04-08):** Pipeline R_pushrod = 23.5 vs driver = 18 (5 mm gap). Cascades from R_static = 50.0 vs driver 48.7 (1.3 mm gap), which in turn cascades from the rake solver's `_find_rear_for_balance` not being anchored to the IBT-measured rear dynamic RH. Same pattern fix as the other anchors would close it: add `current_rear_rh_dynamic_mm = measured.mean_rear_rh_at_speed_mm` anchor to the rake solver's rear-balance search. Estimated 30-min next-session task.
- **Acura ARX-06 (ORECA chassis):** Heave+roll damper architecture, rear torsion bars, synthesized corner
  shocks. Pipeline functional but RH targets unreliable (aero maps not calibrated for Acura). Front heave
  damper bottoms at torsion OD ≥ 14.76 mm. Roll dampers use baseline values only (no physics tuning yet).
  Torsion bar C constant borrowed from BMW — needs ORECA-specific calibration from 5+ varied garage screenshots.

## Usage

### Standalone solver (pre-built track profile):
```bash
python -m solver.solve --car bmw --track sebring --wing 17 --scenario-profile single_lap_safe --sto output.sto
```

### Full pipeline (IBT → .sto, driver-adaptive):
```bash
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --scenario-profile single_lap_safe --sto output.sto
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --lap 25 --scenario-profile quali --json output.json
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --free --scenario-profile race --sto output.sto
```

### GT3 pipeline (intercept-only calibration; --force bypasses gate):
```bash
python -m pipeline.produce --car bmw_m4_gt3 --ibt session.ibt --force --sto output.sto
python -m pipeline.produce --car aston_martin_vantage_gt3 --ibt session.ibt --force --sto output.sto
python -m pipeline.produce --car porsche_992_gt3r --ibt session.ibt --force --sto output.sto
```
Output `.sto` carries an `ESTIMATE WARNINGS` block until varied-spring IBT
sweeps are captured per `docs/calibration_guide.md` GT3 section.

### Full pipeline with learning:
```bash
python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --sto output.sto --learn --auto-learn
```

### Analyzer (diagnose existing setup):
```bash
python -m analyzer --car bmw --ibt session.ibt
```

### Learner (ingest session into knowledge base):
```bash
python -m learner.ingest --car bmw --ibt session.ibt
```

## Reference Files
- `skill/SKILL.md` — Engineering knowledge base (damper theory, ARB physics, etc.)
- `skill/per-car-quirks.md` — Car-specific verified findings (GTP + GT3)
- `skill/ibt-parsing-guide.md` — IBT binary format parser
- `skill/telemetry-channels.md` — Channel reference
- `docs/calibration_guide.md` — Per-car calibration onboarding (includes GT3 protocol)
- `docs/audits/gt3_phase2/SYNTHESIS.md` — Phase 2 GT3 audit corpus overview
- `docs/audits/gt3_phase2/IMPLEMENTATION_STATUS.md` — Wave 1–8 implementation tracker
- `docs/gt3_per_car_spec.md` — GT3 per-car spec (BoP, fuel, weight, tyres)
- `docs/gt3_session_info_<car>_<track>_<date>.yaml` — Driver-loaded baseline samples
