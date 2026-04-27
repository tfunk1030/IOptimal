# GT3 Phase 2 Synthesis — v5 implementation plan

**Date:** 2026-04-26 (plan); last updated 2026-04-27 (status)
**Inputs:** 12 parallel audit PRs (#103 – #114) + 4 IBT-verified session_info YAMLs (BMW M4 GT3 EVO at Spielberg + Nürburgring, Aston Vantage GT3 EVO at Spielberg, Porsche 992 GT3 R at Spielberg) + 10 parsed GT3 aero maps.

The 12 audit docs are the source of truth — this is a synthesis layer that pulls them into one buildable Phase 2 implementation plan with ordering, dependencies, and concrete PR-sized work units.

> **Implementation status (2026-04-27, latest batch W2.3 + W3.1):** Wave 1 (W1.1, W1.2, W1.3) + Wave 2.1 + W2.2 + W2.3 + Wave 3.1 shipped on `claude/merge-audits-wave1-DDFyg`. All 12 audit PRs merged into the same branch. 543 tests pass, 0 new regressions. GT3 IBT now runs through Step 1 + Step 2 + Step 3 cleanly with real `front_coil_rate_nmm` and `rear_spring_rate_nmm` from frequency-isolation physics. Step 4 still over-targets Porsche 992 RR LLTD and ARB blade encoding is wrong (W2.4); damper polarity wrong for inverted-polarity cars (W3.2); setup writer still raises (W4.1, W4.2). See [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) for the full progress log.

## Aggregate findings

| Risk | Count |
|---|---|
| BLOCKER | 133 |
| DEGRADED | 150 |
| COSMETIC | 46 |
| **Total** | **329** |

Across 12 audit docs in `docs/audits/gt3_phase2/`:

| # | Audit unit | PR | Findings | Effort estimate |
|---|---|---|---|---|
| 1 | solver-step-chain | [#104](https://github.com/tfunk1030/IOptimal/pull/104) | 19 (9/7/3) | 20 h |
| 2 | solver-objective-sensitivity | [#109](https://github.com/tfunk1030/IOptimal/pull/109) | 34 (8/19/7) | 34 h |
| 3 | solver-rake-corner-arb | [#113](https://github.com/tfunk1030/IOptimal/pull/113) | 34 (19/13/2) | ~40 h |
| 4 | solver-damper-legality | [#107](https://github.com/tfunk1030/IOptimal/pull/107) | 41 (varies) | ~44 h |
| 5 | pipeline | [#110](https://github.com/tfunk1030/IOptimal/pull/110) | 24 (14/7/3) | 20–28 h |
| 6 | output | [#106](https://github.com/tfunk1030/IOptimal/pull/106) | 45 (24/16/5) | ~66 h (8.3 d for 3 sampled cars; 16 d for full grid) |
| 7 | car-model-registry | [#105](https://github.com/tfunk1030/IOptimal/pull/105) | 25 (10/13/2) | ~80 h (10 d) |
| 8 | calibration-gate | [#103](https://github.com/tfunk1030/IOptimal/pull/103) | 11 (3/5/3) | ~8 h |
| 9 | analyzer | [#112](https://github.com/tfunk1030/IOptimal/pull/112) | 39 (19/17/3) | ~104 h (13 d) |
| 10 | learner | [#108](https://github.com/tfunk1030/IOptimal/pull/108) | 16 (7/8/1) | ~10 h |
| 11 | infra-teamdb-watcher-desktop | [#111](https://github.com/tfunk1030/IOptimal/pull/111) | 17 (6/7/4) | ~43 h (5.4 d) |
| 12 | webapp-cli-tests-docs | [#114](https://github.com/tfunk1030/IOptimal/pull/114) | 24 (9/15/0) | ~42 h |
| **Total** | | | **329** | **~511 h ≈ 64 engineer-days** |

The v3 plan estimated 150–200 h. The v4 plan revised to ~40 h. **Reality: ~511 h (~64 days, ~3 months for one developer).** v3 was closer because the audits surfaced many subtle regressions and downstream cascade points that the schema-first analysis missed.

## Top 10 highest-impact findings (cross-cut)

These are the findings most likely to crash GT3 end-to-end runs or silently corrupt data:

1. **`car_model/registry.py` `resolve_car()` substring fallback returns GTP BMW for "BMW M4 GT3 EVO" queries.** Silent wrong-car retrieval. (Audit 7 + 11.) Fix: explicit `iracing_car_path` lookup; remove substring fallback for GT3 entries.
2. **`solver/legal_space.py:64-186` and `solver/stint_model.py:694-696` call `float(car.front_heave_spring_nmm)` — `None` for all GT3 cars → TypeError on first call.** (Audit 4.)
3. **`solver/modifiers.py:121,211` read `car.heave_spring.front_spring_range_nmm` — AttributeError on GT3 (`heave_spring=None`).** (Audit 4.)
4. **5 `HeaveSolver(car, track)` construction sites in `solver/solve_chain.py` are unconditional** — Step 2 runs for GT3 even though the car has no heave springs. (Audit 1.)
5. **`output/setup_writer.py` `_CAR_PARAM_IDS` has zero GT3 entries; `write_sto()` raises `ValueError` for every GT3 car.** (Audit 6.)
6. **`output/setup_writer.py` per-corner damper writes (5 × 4 = 20 channels) are incompatible with GT3's per-axle YAML (4 × 2 = 8 channels).** (Audit 6.)
7. **`car_model/calibration_gate.py` never emits `not_applicable=True` end-to-end.** Phase 0 plumbing exists; `check_step()` has no `car.suspension_arch.has_heave_third` dispatch. (Audit 8.)
8. **`analyzer/setup_reader.py` adapter_name whitelist excludes every GT3 canonical → falls through to "unknown" → `apply_live_control_overrides` breaks.** (Audit 9.)
9. **`analyzer/setup_reader.py` damper layout decision tree has no GT3 per-axle branch — `Dampers.FrontDampers`/`Dampers.RearDampers` (8-channel) misroutes to Ferrari `LeftFrontDamper` lookup → all damper fields zero.** (Audit 9.)
10. **`teamdb/aggregator.py:49` pools all observations into one regression with no architecture partition — first GT3 observation upload corrupts GTP empirical models.** (Audit 11.)

## Cross-cutting patterns

### Pattern A: Architecture dispatch missing
Most BLOCKERs are the same root cause: code that should branch on `car.suspension_arch.has_heave_third` (or `== GT3_COIL_4WHEEL`) doesn't. Examples in every solver/, pipeline/, output/ module. **Fix shape:** sentinel pattern via `step2.present` or direct enum dispatch.

### Pattern B: `step2.present` flag exists but no consumer reads it
Phase 0 added `HeaveSolution.present` and `.null()` factory specifically for this — but no Phase 1 PR wired the consumers. Audit 4 found 3 sites in `solver/params_util.py`, `solver/candidate_search.py`, `solver/decision_trace.py` that check `step2 is not None` (always True) and write 0.0 placeholders.

### Pattern C: Per-corner damper assumption
GTP exposes 16 damper channels (4 corners × 4 channels); GT3 exposes 8 (2 axles × 4 channels). The solver builds 16; the writer maps 16 to per-corner XML. GT3 silently drops L/R asymmetry on write.

### Pattern D: Per-car YAML field-name divergence
BMW: `FrontBrakes` / `ArbBlades`. Aston: `FrontBrakesLights` / `FarbBlades`+`RarbBlades` / `EpasSetting`+`ThrottleResponse`+`EnduranceLights`. Porsche: `FrontBrakesLights` / `ArbSetting` (integer NOT blade) / `RarbSetting` / paired rear `TotalToeIn` / `FuelLevel` in front section / `DashDisplayPage`. **Same divergence as GTP cars** — needs per-car PARAM_IDS dicts, not a "GT3 base + minor overrides" approach.

### Pattern E: Damper click polarity per car
BMW/Mercedes/Ferrari/Lambo/Mustang: standard 0–11 higher=stiff. Audi/McLaren/Corvette: inverted (lower=stiff) with different ranges. Porsche 992: reaches 12 (probably 0–12). Acura NSX: 1–16. **Solver assumes BMW convention; ~5 of 11 cars have the wrong polarity wired.**

### Pattern F: LLTD physics gap (RR layout)
Porsche 992 GT3 R is the ONLY rear-engine GT3 (`weight_dist_front=0.449`). OptimumG formula gives target ≈ 0.499 — **lower** than FR/MR cars at ~0.51. ARB solver currently defaults to GTP target ≈ 0.51, would over-target stiffness for Porsche 992. Same epistemic gap that already affects the Porsche 963 GTP. (Audit 3.)

### Pattern G: KNOWN_CAUSALITY heave-only
`learner/delta_detector.py` has zero GT3 entries. Audit 10 enumerated the 23 specific tuples needed (corner spring → variance/freq/settle/shock-vel/roll/understeer; bump rubber gap → contact %; splitter height → scrape events).

### Pattern H: Schema migration needed
`teamdb/CarDefinition` and `Observation` tables lack `iracing_car_path`, `bop_version`, `suspension_arch` columns. Audit 11 includes a complete migration script (`migrations/0001_gt3_phase2.sql`) with column adds, back-fills, NOT NULL promotion, uniqueness constraint replacement.

## Phase 2 implementation plan — 22 work units

Sequenced by dependency. Each work unit becomes one PR. Effort estimates per unit; ordering critical.

### Wave 1 — foundation invariants (must land first; 3 units; ~20 h) — **DONE (2026-04-27)**

| # | Title | Files | Effort | Status | Why first |
|---|---|---|---|---|---|
| W1.1 | Calibration gate emits `not_applicable` for GT3 Step 2 | `car_model/calibration_gate.py` | 8 h | **DONE** | Audit 8 — the gate is read by every solver and pipeline path; fixing this unblocks all downstream Step 2 skipping logic. |
| W1.2 | `step2.present` consumers wired | `solver/{params_util,candidate_search,decision_trace}.py` + `solver/heave_solver.py` defensive guard | 4 h | **DONE** | Audit 4 — 3 BLOCKER consumers + defense-in-depth. Tiny, surgical. |
| W1.3 | `car_model/registry.py` resolves GT3 names without falling back to GTP | `car_model/registry.py`, `car_model/setup_registry.py` `_car_name()`, `car_model/setup_registry.py` `CAR_FIELD_SPECS` GT3 entries | 8 h | **DONE** (specs are empty stubs; W4.1/4.2 will populate) | Audits 7+11 — silent GTP-fallback is the most dangerous data integrity issue. |

### Wave 2 — solver chain unblocks (4 units; ~76 h) — **W2.1 + W2.2 + W2.3 done; W2.4 remains**

| # | Title | Files | Effort | Status | Depends on |
|---|---|---|---|---|---|
| W2.1 | Step 2 (heave) skipped for GT3 in solve_chain | `solver/solve_chain.py` (5 sites), `solver/solve.py` step2-aware analyzers | 12 h | **DONE** | W1.1, W1.2 |
| W2.2 | Step 1 (rake) balance-only mode for GT3 (no L/D) | `solver/rake_solver.py`, `aero_model/parse_xlsx.py` (already has balance_only), `solver/objective.py` | 24 h | **DONE** | W1.1 |
| W2.3 | Step 3 (corner spring) GT3 front-coil branch | `solver/corner_spring_solver.py` extending `front_torsion_c == 0.0` path; `CornerSpringSolution.front_coil_rate_nmm` field; `CornerSpringModel.front_spring_range_nmm` | 16 h | **DONE** | W1.1, W1.2, W2.1 |
| W2.4 | Step 4 (ARB/LLTD) per-car blade encoding + RR LLTD target | `solver/arb_solver.py` blade-vs-label dispatch; Porsche 992 LLTD physics formula | 24 h | TODO (next critical-path) | W1.1, W2.3 |

### Wave 3 — solver chain crash fixes (3 units; ~30 h) — **W3.1 done; W3.2 + W3.3 remain**

| # | Title | Files | Effort | Status | Depends on |
|---|---|---|---|---|---|
| W3.1 | `legal_space`/`modifiers`/`stint_model` heave_spring=None guards | `solver/legal_space.py`, `solver/modifiers.py`, `solver/stint_model.py` | 8 h | **DONE** | W1.2 |
| W3.2 | Damper polarity + range per-car | `solver/damper_solver.py`, `car_model/cars.py` `DamperModel.click_polarity` + `click_range`, per-car overrides for Audi/McLaren/Corvette/Porsche/Acura | 14 h | TODO (next critical-path; batchable with W2.4) | W1.1 |
| W3.3 | Fuel constants generalized | `solver/scenario_profiles.py` per-class fuel cap, `solver/{damper,stint}_model.py` hardcoded 89L removed | 8 h | TODO | none |

### Wave 4 — output + writer (3 units; ~70 h)

| # | Title | Files | Effort | Depends on |
|---|---|---|---|---|
| W4.1 | Setup writer GT3 dispatch — BMW M4 GT3 EVO | `output/setup_writer.py` `_BMW_M4_GT3_PARAM_IDS` + per-axle damper collapse | 16 h | W1.3, W2.1, W3.2 |
| W4.2 | Setup writer GT3 dispatch — Aston Vantage + Porsche 992 | `output/setup_writer.py` `_ASTON_VANTAGE_GT3_PARAM_IDS`, `_PORSCHE_992_GT3R_PARAM_IDS` (Porsche has integer ARB encoding, paired rear TotalToeIn, FuelLevel-in-front) | 24 h | W4.1 |
| W4.3 | Output guards + GT3 garage validator + report | `output/garage_validator.py`, `output/report.py`, `output/bundle.py` step2.present guards; new GT3 GarageRanges fields (`bump_rubber_gap`, `splitter_height`) | 14 h | W1.2, W4.1 |

### Wave 5 — pipeline + analyzer (3 units; ~62 h)

| # | Title | Files | Effort | Depends on |
|---|---|---|---|---|
| W5.1 | Pipeline produce/reason/report GT3 conditional | `pipeline/produce.py` (heave_spring access, JSON output guards, alias dict), `pipeline/reason.py` (heave floor checks), `pipeline/report.py` (display panels) | 24 h | W1.1, W1.2, W2.1 |
| W5.2 | Analyzer setup_reader GT3 schema dispatch | `analyzer/setup_reader.py` adapter_name whitelist, GT3 per-axle damper branch, `analyzer/setup_schema.py` `_KNOWN_FIELD_MAP` GT3 paths, `analyzer/sto_adapters.py` GT3 entries | 24 h | W1.3 |
| W5.3 | Analyzer diagnose + extract GT3 awareness | `analyzer/diagnose.py` skip heave-bottoming for GT3, `analyzer/extract.py` heave channels optional, `analyzer/causal_graph.py` GT3 nodes | 14 h | W5.2 |

### Wave 6 — learner + scoring (3 units; ~56 h)

| # | Title | Files | Effort | Depends on |
|---|---|---|---|---|
| W6.1 | Objective + sensitivity GT3 guards | `solver/objective.py` `_compute_lltd_fuel_window` GT3 branch, `solver/sensitivity.py` `step2.present` guards, `solver/laptime_sensitivity.py` `_front_heave_sensitivity` skip | 14 h | W2.1, W3.1 |
| W6.2 | Learner KNOWN_CAUSALITY GT3 entries (23 tuples) + STEP_GROUPS | `learner/delta_detector.py` `STEP_GROUPS["step3_corner_combined"]` for GT3, 23 new causality tuples | 6 h | W5.3 |
| W6.3 | Learner empirical models + observation schema GT3 | `learner/observation.py` GT3 setup-dict fields, `learner/empirical_models.py` heave-fitter no-op + `_fit_corner_to_variance`, `learner/setup_clusters.py` GT3 cluster keys, `learner/recall.py` GT3 lookups | 18 h | W6.2, W5.3 |

### Wave 7 — auto-calibrate + GarageOutputModel (2 units; ~80 h)

| # | Title | Files | Effort | Depends on |
|---|---|---|---|---|
| W7.1 | `car_model/garage.py` GT3 GarageSetupState + GarageOutputModel | `car_model/garage.py` `GarageSetupState.from_current_setup` GT3 conditional extraction, `DirectRegression._EXTRACTORS` GT3 features (inv_lf_spring, splitter_h, bump_rubber_gap), `_setup_key()` GT3 fingerprint fields | 24 h | W1.3, W4.1 |
| W7.2 | `car_model/auto_calibrate.py` GT3 feature pools + apply_to_car | GT3 `_FRONT_POOL`/`_REAR_POOL`/`_UNIVERSAL_POOL` alternatives (corner-coil 1/k, no heave), `apply_to_car` GT3 attribute writes (no `car.heave_spring.*`), GT3 calibration-data layout under `data/calibration/{gt3_car}/` | 56 h | W7.1 |

### Wave 8 — infra + DB + automation (2 units; ~43 h)

| # | Title | Files | Effort | Depends on |
|---|---|---|---|---|
| W8.1 | `teamdb` schema migration + GT3 columns | `teamdb/models.py` `CarDefinition`+`Observation` `iracing_car_path`/`bop_version`/`suspension_arch` columns, `migrations/0001_gt3_phase2.sql` raw SQL, `teamdb/aggregator.py` per-architecture partition, `server/routes/observations.py` validation | 24 h | none |
| W8.2 | Watcher + desktop GT3 CarPath detection | `watcher/{monitor,service}.py` use `CarPath` not `CarScreenName`, register all GT3 paths (`bmwm4gt3`, `amvantageevogt3`, `porsche992rgt3`, etc.), `desktop/config.py` GT3 entries | 19 h | W1.3 |

### Wave 9 — UI + CLI + tests + docs (2 units; ~62 h)

| # | Title | Files | Effort | Depends on |
|---|---|---|---|---|
| W9.1 | webapp + CLI accept GT3 | `webapp/` GT3 car list + conditional setup display panels (hide heave/third for GT3), `__main__.py` + `pipeline/__main__.py` argparse choices, `validation/{run_validation,objective_calibration}.py` GT3 support tier rows | 30 h | W4.2, W5.1 |
| W9.2 | Tests + GT3 fixtures + docs | 3 GT3 regression baselines (`tests/fixtures/baselines/{bmw_m4_gt3_spielberg,aston_vantage_gt3_spielberg,porsche_992_gt3r_spielberg}_baseline.sto`), parameterize `tests/test_setup_regression.py`, `CLAUDE.md` GT3 section, `skill/per-car-quirks.md` GT3 quirks, `docs/calibration_guide.md` GT3 onboarding | 32 h | all prior |

### Wave 10 — end-to-end smoke + onboarding remaining cars (1 unit; ~80+ h)

| # | Title | Effort | Depends on |
|---|---|---|---|
| W10.1 | E2E smoke test (BMW M4 GT3 at Spielberg → valid `.sto`) + 7 remaining GT3 car stubs (Mercedes AMG, Acura NSX, Lambo Huracán, McLaren 720S, Mustang, Corvette Z06; Audi blocked on missing aero map) + per-car PARAM_IDS, IBT acquisition | 80 h+ | all prior; per-car IBT collection still required for full calibration |

## Dependency graph

```
W1.1 ─┬─→ W2.1 ─→ W2.3 ─→ W2.4 ─┐
      ├─→ W3.2                  │
      └─→ W5.1                  │
W1.2 ─┬─→ W2.1                  ├─→ W4.1 ─→ W4.2 ─┐
      ├─→ W3.1                  │     │            │
      └─→ W4.3                  │     ├─→ W7.1 ──→ W7.2
W1.3 ─┬─→ W4.1                  │     │            │
      ├─→ W5.2                  │     │            │
      └─→ W8.2                  │     │            │
W2.2  ────────────────────────  │     │            │
W3.1  → W6.1                    │     │            │
W3.2  → W4.1                    │     │            │
W3.3  (independent)             │     │            │
W5.2 ─→ W5.3 ─→ W6.2 ─→ W6.3    │     │            │
W6.1  (after W3.1)              │     │            │
W8.1  (independent)             │     │            │
                                ↓     ↓            ↓
                              W9.1 ─→ W9.2 ──→ W10.1
```

## Critical path

W1.1 → W2.1 → W2.3 → W2.4 → W4.1 → W4.2 → W7.1 → W7.2 → W9.1 → W9.2 → W10.1 ≈ 8h+12h+16h+24h+16h+24h+24h+56h+30h+32h+80h ≈ **322 hours one-developer critical path**.

Parallelizable tail: ~190 h of work in W2.2/W3.x/W5.x/W6.x/W8.x can run in parallel with the critical path. Realistic 2–3 developer team: **~3 months wall-clock.**

## What's de-risked vs what remains

**De-risked by audits (no longer unknown):**
- All Step 2 / heave / `step2.present` consumer sites mapped.
- All per-car YAML field paths documented (BMW/Aston/Porsche IBT-verified; other 8 cars need IBT capture).
- Per-axle damper architecture confirmed and quantified.
- LLTD physics gap for Porsche 992 RR layout flagged.
- DB migration plan written.
- 23 GT3 KNOWN_CAUSALITY tuples enumerated.
- 25 new FieldDefinitions enumerated.
- Substring-fallback risk in `resolve_car()` identified.

**Still unknown (require user-supplied data):**
- Spring rate ranges for Aston/McLaren/Audi (manuals don't publish; need garage screenshots or varied-setup IBTs).
- Damper click range for Porsche 992 (driver values reach 12; max unknown).
- ARB blade count for Aston/McLaren/Mustang/Corvette (manuals didn't enumerate; need garage capture).
- Aero compression coefficients for any GT3 car (need 3+ varied-spring or varied-pushrod IBTs at the same track; the BMW Spielberg+Nürburgring pair has IDENTICAL setup, so it doesn't help).
- Audi R8 LMS aero map (only car of 11 without xlsx in user's collection).
- iRacing CarPath strings for the 7 cars I haven't IBT-verified (Mercedes AMG, Acura NSX, Lambo Huracán, McLaren 720S, Mustang, Corvette Z06, Audi R8).

## Recommendation

1. ~~**Land the audit PRs (#103–#114) on `gt3-phase0-foundations`**~~ — DONE 2026-04-27 on `claude/merge-audits-wave1-DDFyg`. All 12 PRs merged with one resolvable add/add conflict on `webapp-cli-tests-docs.md`.
2. ~~**Begin Wave 1 work units in parallel**~~ — DONE 2026-04-27.
3. **Capture more IBTs in parallel** — varied-spring sweeps for BMW M4 GT3 at Spielberg unblock W7.2 (auto-calibrate). The remaining 7 GT3 cars each need at least one IBT to pin their YAML schemas.
4. **Defer Audi R8 LMS** until aero map is available.
5. ~~**Update CLAUDE.md** with a GT3 section after W2 lands~~ — DONE 2026-04-27 (see entry under "Current Codebase Status").

## What's next (post-Wave 2)

- **W2.3 (Step 3 corner spring GT3 front-coil branch)** is the next critical-path unit. Without it, GT3 IBT runs through Step 1 + Step 2 cleanly but Step 3 emits `front_torsion_od_mm=0.0` and `front_rate=0`, making Steps 4–6 non-physical. ~16 h.
- **W3.1 (legal_space / modifiers / stint_model heave_spring=None guards)** is the smallest remaining unit (~8 h), independent of W2.3, and would let the legal-search path run cleanly on GT3. Good fill-in work to batch with W2.3.
- **W3.2 (damper polarity per-car)** is a cross-cutting fix — affects 5/11 GT3 cars (Audi/McLaren/Corvette inverted polarity, Porsche/Acura range mismatch) plus protects against silent damper-direction bugs in candidate-search. ~14 h.
- **W4.1 (BMW M4 GT3 PARAM_IDS)** is what finally lets a GT3 `.sto` file be written. Depends on W2.1 (done), W3.2 (TODO), W1.3 (done). ~16 h.
- **Wave 5 (analyzer + pipeline)** is independent of Steps 3/4 fixes — could parallelize with W2.3/W2.4 if there's a second developer.

See [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) for shipped diff summary, deferred-finding ledger, and recommended next-batch sequencing.
