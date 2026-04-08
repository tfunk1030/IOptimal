# Repo Audit

Generated: 2026-03-26T00:49:21.470977+00:00
Updated: 2026-04-08 (LLTD phantom proxy disabled, σ-cal driver anchor architecture, per-axle roll damper flags, rear roll damper writer bug fixed)

## 2026-04-08 Highlights

- **🚨 LLTD phantom proxy disabled** (`auto_calibrate.py:1360`, `data/calibration/porsche/models.json`, `cars.py` Porsche def). The field `lltd_measured` was a misnamed alias for `roll_distribution_proxy`, a geometric ratio insensitive to spring stiffness (verified across 5 IBTs with R_third varying 100%: spread 0.09 pp). The "11 pp model gap" the ARB solver was chasing was apples-to-oranges. Porsche now uses the OptimumG/Milliken physics formula `0.521` as the LLTD target. Open epistemic gap: no true LLTD measurement available from iRacing IBT.
- **σ-calibration architecture** (`solver/heave_solver.py:min_rate_for_sigma`): accepts `current_rate_nmm` + `current_meas_sigma_mm`, computes `cal_ratio = meas / model_at_current` (clamped [0.5, 2.0]), translates user σ-target to model space, sticky pre-check returns driver rate when its model_σ ≤ effective target + 0.05 mm. Wired through `_run_sequential_solver` and `materialize_overrides` paths in `solve_chain.py`.
- **Driver-anchor pattern** rolled out across 5 solvers: `heave_solver` (σ sticky), `corner_spring_solver` (direct R_coil), `arb_solver` (LLTD-fallback), `diff_solver` (coast/drive/preload), `supporting_solver` (TC + parses driver coast/drive from "40/65" string), `candidate_search` (skip-scale-when-anchored guard). All anchors are explicit, provenance-tracked, and never lap-time-driven.
- **Per-axle roll damper flags** (`car_model/cars.py:DamperModel`): added `has_front_roll_damper` and `has_rear_roll_damper`. Porsche set to `front=True / rear=False` (Multimatic has front roll damper but rear roll is implicit in per-corner shocks). Acura set to `both=True`. Setup writer (`output/setup_writer.py:1069`) and damper solver (`solver/damper_solver.py:790`) gate roll damper output on these flags. Fix removes phantom `CarSetup_Dampers_RearRoll_*` XML IDs from Porsche `.sto` output.
- **`solution_from_explicit_offsets` fix** (`solver/rake_solver.py:493`): when caller provides `static_front_rh_mm`/`static_rear_rh_mm`, USE THEM directly. Previously was recomputing from `garage_model.predict()` with **baseline** springs, drifting static_front from 30 → 32.78 for Porsche and propagating through reconcile.
- **`solver/objective.py:891`**: replaced `track.median_speed_kph` with `track.aero_reference_speed_kph` (V²-RMS over speed bands ≥100 kph). Compliance-based front static prediction honors candidate's `front_pushrod_offset_mm` (was hardcoded to `pushrod.front_pinned_rh_mm`).
- **Falsy-int bug fixed in 3 sites**: `solver/supporting_solver.py:303-313` and `:406`, `solver/solve_chain.py:240`. The pattern `diff_ramp_option_index(...) or 1` silently collapsed legal index 0 (= 40/65) to index 1 (= 45/70). Driver-correct ramps were being lost. Replaced with explicit `None` checks.
- **Porsche/Algarve driver-match score on newest IBT (14-23-44, B HOT, best lap 92.992 s)**: 14 exact / 7 close / 1 trailing (rear pushrod 23.5 vs driver 18, downstream of rear static 1.3 mm above driver).

## 2026-04-07 Highlights

- **Strict calibration gate** with 3-state classification (`calibrated`/`weak`/`uncalibrated`), R² thresholds, and provenance tracking. Cascade fixed so weak blocks don't propagate.
- **Compliance physics for static RH and deflection**: `defl ∝ F/k` (1/k features) — for Porsche this took rear RH R² from 0.61 → 0.94 and deflection R² from 0.67 → 0.97.
- **`apply_to_car` zero-stale-coefficients fix**: prevents BMW values from persisting alongside fresh non-BMW calibration.
- **18 silent BMW fallback patterns removed** from solver/objective.py, solver/sensitivity.py, solver/candidate_search.py, solver/sector_compromise.py, solver/legal_space.py, solver/damper_solver.py, solver/stint_model.py, solver/rake_solver.py, solver/arb_solver.py, solver/bayesian_optimizer.py, solver/explorer.py.
- **`damper_solver.py` strict mode**: 50-line baseline-fallback path replaced with `ValueError`. Gate blocks Step 6 BEFORE this path is reachable.
- **`pushrod_for_target_rh` strict mode**: -29.0 BMW fallback replaced with `ValueError`.
- **Garage feasibility cap** in rake_solver: caps rear static RH target to garage-achievable range, prevents impossible pushrod targets.
- **Per-corner tyre pressures**, **Front Roll HS slope propagation**, **Rear 3rd damper propagation**, **Porsche diff coast/drive ramp XML IDs** all shipped in setup_writer/solution_from_explicit_settings.
- **Regression test safety net**: `tests/test_setup_regression.py` runs full pipeline against committed BMW/Sebring and Porsche/Algarve baseline `.sto` fixtures. Both pass after every change in this batch.

## Workflow Map

`IBT -> track/analyzer -> diagnosis/driver/style -> calibration_gate -> solve_chain/legality -> report/.sto -> webapp`

The calibration gate (`car_model/calibration_gate.py`) sits between input loading and the solver. It classifies every subsystem as `calibrated`/`weak`/`uncalibrated` and either runs the solver, runs with a `WEAK CALIBRATION DETECTED` banner, or blocks with CLI calibration instructions. Provenance is surfaced via `gate.provenance()` and embedded in JSON output as `calibration_provenance`.

## Anchor Files

- `pipeline/produce.py`: single-session orchestration; calls calibration gate and prints `CALIBRATION CONFIDENCE` provenance block on every run.
- `solver/objective.py`: candidate ranking, breakdown weighting, scenario-aware scoring. Reads damper baselines, m_eff, tyre_load_sensitivity, fuel_capacity directly from `car.*` (no fallbacks).
- `solver/solve.py`: 6-step physics solver with calibration gate enforcement.
- `solver/damper_solver.py`: physics-pure damper solver. Raises ValueError if zeta uncalibrated (gate blocks Step 6 first).
- `solver/rake_solver.py`: rake/RH solver with garage feasibility cap and reconciliation. Uses compliance-aware RH model.
- `car_model/calibration_gate.py`: per-car, per-subsystem 3-state calibration with R² thresholds, weak_block, provenance dict.
- `car_model/cars.py`: `RideHeightModel` and `DeflectionModel` carry both linear AND compliance coefficient slots; each car uses whichever fits its data best.
- `car_model/auto_calibrate.py`: feature selection includes `1/k` candidates for RH and deflection regressions; `apply_to_car()` zeroes stale coefficient slots before applying new model.
- `tests/test_setup_regression.py`: pipeline regression test with committed `.sto` fixtures.
- `validation/run_validation.py`: reproducible BMW/Sebring evidence report and support tiers.

## Current BMW/Sebring Evidence (updated 2026-04-04)

- Samples: `99`
- Non-vetoed samples: `~97`
- Pearson (non-vetoed): `~0.226` (improved from 0.035 after zero-variance fix + damper signal)
- Spearman (non-vetoed): `~-0.298` (improved from -0.121 after calibration gate fixes)
- Current objective status: `improving` — correlation now materially negative, approaching actionable but not yet authoritative.

## Support Tiers (updated 2026-04-08)

| Car | Track | Tier | Samples | Calibrated Steps | Weak Steps | Blocked Steps |
|-----|-------|------|---------|-----------------|------------|---------------|
| BMW | Sebring | calibrated | 99 | 1-6 (all) | none | none |
| Porsche | Algarve | calibrated | 35 unique setups, 88 zeta sessions, 22 aero sessions | 1-6 (all) | none | none |
| Ferrari | Sebring | partial | 12 | 1-3 | — | 4, 5, 6 |
| Cadillac | Silverstone | exploratory | 4 | 2-3 | — | 1, 4, 5, 6 |
| Acura | Hockenheim | exploratory | 7 | — | — | 1-6 (all) |

**Porsche detail (2026-04-08)**: Front RH R²=1.00, rear RH R²=0.91, deflection R²=0.98, damper zeta from 88 click-sweep sessions, aero compression from 22 sessions. ARB stiffness MEDIUM hand-cal (auto-cal noise-floor inconclusive — model_predicted_ARB_delta < K_total measurement noise floor, so the back-solve cannot validate or invalidate; gate maps `arb_calibrated=None` to MEDIUM). LLTD target = 0.521 from OptimumG/Milliken physics formula (NOT from the geometric proxy that was previously stored as `lltd_measured`). Driver-match score on newest IBT (14-23-44, B HOT, best lap 92.992 s): 14 exact / 7 close / 1 trailing.

**LLTD epistemic gap**: We have NO direct LLTD measurement from iRacing IBT. The previously-stored `measured_lltd_target = 0.503` was the geometric proxy `(t_f³/(t_f³+t_r³))`, not real LLTD. The 13 pp gap between model k_front/k_total (0.391) and OptimumG physics target (0.521) is REAL but un-attributable without wheel-force telemetry or controlled per-axle ARB lap-time correlation. Porsche ARB anchors to driver-loaded values via the `arb_solver` LLTD-fallback when `lltd_error > 3 pp`.

## Value Classes

- Source of truth: `261` inventoried files
- Calibration evidence: `183` inventoried files
- Generated artifact: `29` inventoried files
- Disposable scratch/history: `60` inventoried files

## Excluded / Summarized Separately

- `node_modules`: `45` files (third-party, not first-party source)
- Generated artifacts: `56` files
- Scratch/history: `52` files

## Official iRacing Sources Used

- [BMW M Hybrid V8 user manual](https://s100.iracing.com/wp-content/uploads/2023/10/BMW-M-Hybrid-V8.pdf): Baseline setup workflow, aero calculator usage, hybrid modes, brake bias, TC, gear stack, and diff behavior.
- [2025 Season 1 release notes](https://support.iracing.com/support/solutions/articles/31000174324-2025-season-1-release-notes-2024-12-09-03-): GTP aerodynamic-property refresh and standardized ride-height sensor reference at the skid/axle measurement points.
- [2025 Season 4 Patch 2 release notes](https://support.iracing.com/support/solutions/articles/31000177221-2025-season-4-patch-2-release-notes-2025-09-24-01-): Current GTP hybrid/fuel-economy equivalence update plus BMW TC label/control fixes.
- [2025 Season 3 Patch 4 release notes](https://support.iracing.com/support/solutions/articles/31000176931-2025-season-3-patch-4-release-notes-2025-07-25-02-): GTP low-fuel warning trigger/clear behavior update in the garage workflow.
- [Load custom setups onto your racecar](https://support.iracing.com/support/solutions/articles/31000133513-load-custom-setups-onto-your-racecar-): Official garage/setup loading and sharing behavior for .sto workflows.
- [Filepath for active iRacing cars](https://support.iracing.com/support/solutions/articles/31000172625-filepath-for-active-iracing-cars): Canonical active-car folder names, including BMW/Cadillac/Acura/Ferrari/Porsche GTP entries.
- [iRacing car setup guide](https://ir-core-sites.iracing.com/members/pdfs/iRacing_Car_Setup_Guide_20100910.pdf): General setup-adjustment discipline: baseline first, one change at a time, no magic setup assumptions.

## Official Constraints Applied

- Legal-manifold search stays inside setup-registry and garage-validated ranges; it does not emit out-of-range `.sto` candidates.
- BMW M Hybrid V8 optimization treats aero ride height at speed as telemetry-derived, consistent with the official aero-calculator workflow.
- BMW scenario profiles only bias objective weights and seed assumptions; they do not bypass session-limited or garage-limited controls.
- Recent GTP release notes are treated as authority for ride-height reference, hybrid/fuel behavior, and low-fuel-control assumptions, and the repo now validates against those legal shapes instead of stale aliases.
- The general iRacing setup guide still applies: baseline first, deliberate changes, and no claim of a universal magic setup.

## Directory Summary

| Directory | Files | Default Value Class | Notes |
| --- | ---: | --- | --- |
| `(root)` | 60 | Source of truth | Top-level utilities, metadata, and ad-hoc helper files. |
| `.claude` | 1 | Source of truth | Repo files |
| `aero_model` | 5 | Source of truth | Aero surfaces, drag/downforce interpolation, and platform response models. |
| `analyzer` | 18 | Source of truth | Telemetry extraction, diagnosis, session context, and driver/style inference. |
| `car_model` | 5 | Source of truth | Car definitions, garage ranges, setup registry, and OEM/car-specific constraints. |
| `comparison` | 6 | Source of truth | Session comparison, scoring, and synthesized recommendation tooling. |
| `data` | 221 | Calibration evidence | Track profiles, observations, empirical models, and sample artifacts. |
| `docs` | 6 | Source of truth | User-facing repo documentation and research snapshots. |
| `learner` | 13 | Source of truth | Observation storage, empirical corrections, and prior-session knowledge. |
| `output` | 10 | Source of truth | Report rendering, garage correlation, and .sto export. |
| `outputs` | 1 | Generated artifact | Generated run outputs and saved reports. |
| `pipeline` | 6 | Source of truth | Top-level single-session and multi-session orchestration entrypoints. |
| `research` | 3 | Calibration evidence | Supporting engineering notes and manual calibration writeups. |
| `scripts` | 2 | Source of truth | Repo maintenance, diagnostics, and generated-document utilities. |
| `skill` | 4 | Source of truth | Repo files |
| `solver` | 44 | Source of truth | Setup solve chain, objective function, legality checks, and search strategies. |
| `tests` | 30 | Source of truth | Regression coverage and fixture-backed validation. |
| `tmp` | 60 | Disposable scratch/history | Scratch artifacts and temporary work products. |
| `track_model` | 5 | Source of truth | IBT parsing, track-profile building, and track metadata. |
| `validation` | 8 | Source of truth | Calibration/evidence reporting and schema normalization. |
| `validator` | 7 | Source of truth | Cross-checks for solver behavior and garage consistency. |
| `webapp` | 18 | Source of truth | FastAPI UI, service adapters, templates, and persistence. |

## High-Risk Files

- `analyzer/__init__.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus:   init  . Dependencies: None. Outputs: None.
- `analyzer/__main__.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus:   main  . Dependencies: car_model.cars, pipeline.produce. Outputs: None.
- `analyzer/adaptive_thresholds.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus: adaptive thresholds. Dependencies: analyzer.driver_style, car_model.cars, track_model.profile. Outputs: None.
- `analyzer/causal_graph.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus: causal graph. Dependencies: analyzer.diagnose. Outputs: None.
- `analyzer/conflict_resolver.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus: conflict resolver. Dependencies: analyzer.diagnose, analyzer.recommend. Outputs: None.
- `analyzer/context.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus: context. Dependencies: analyzer.diagnose, analyzer.extract, analyzer.setup_reader, analyzer.telemetry_truth. Outputs: None.
- `analyzer/diagnose.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus: diagnose. Dependencies: analyzer.adaptive_thresholds, analyzer.causal_graph, analyzer.extract, analyzer.overhaul. Outputs: None.
- `analyzer/driver_style.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus: driver style. Dependencies: analyzer.segment, car_model.cars, track_model.ibt_parser. Outputs: None.
- `analyzer/extract.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus: extract. Dependencies: analyzer.telemetry_truth, car_model.cars, track_model.build_profile, track_model.ibt_parser. Outputs: None.
- `analyzer/overhaul.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus: overhaul. Dependencies: analyzer.state_inference. Outputs: None.
- `analyzer/recommend.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus: recommend. Dependencies: analyzer.conflict_resolver, analyzer.diagnose, analyzer.setup_reader, car_model.cars. Outputs: None.
- `analyzer/report.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus: report. Dependencies: analyzer.diagnose, analyzer.extract, analyzer.recommend, analyzer.setup_reader. Outputs: None.
- `analyzer/segment.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus: segment. Dependencies: car_model.cars, track_model.ibt_parser. Outputs: None.
- `analyzer/setup_reader.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus: setup reader. Dependencies: track_model.ibt_parser. Outputs: None.
- `analyzer/setup_schema.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus: setup schema. Dependencies: None. Outputs: None.
- `analyzer/state_inference.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus: state inference. Dependencies: analyzer.diagnose, analyzer.driver_style, analyzer.extract, analyzer.segment. Outputs: None.
- `analyzer/stint_analysis.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus: stint analysis. Dependencies: analyzer.extract, car_model.cars, track_model.ibt_parser. Outputs: None.
- `analyzer/telemetry_truth.py`: Telemetry extraction, diagnosis, session context, and driver/style inference. File focus: telemetry truth. Dependencies: analyzer.extract, pipeline.reason. Outputs: None.
- `pipeline/__init__.py`: Top-level single-session and multi-session orchestration entrypoints. File focus:   init  . Dependencies: None. Outputs: None.
- `pipeline/__main__.py`: Top-level single-session and multi-session orchestration entrypoints. File focus:   main  . Dependencies: pipeline.produce. Outputs: None.
- `pipeline/preset_compare.py`: Scenario/preset comparison runner that aligns quali, sprint, and race flows. Dependencies: car_model.cars, pipeline.produce, pipeline.reason. Outputs: preset comparison reports, scenario-specific solver outputs.
- `pipeline/produce.py`: Single-IBT telemetry-backed setup pipeline and CLI entrypoint. Dependencies: aero_model, aero_model.gradient, analyzer.adaptive_thresholds, analyzer.context. Outputs: report text, .sto file, single-session JSON payload.
- `pipeline/reason.py`: Multi-IBT reasoning pipeline that aggregates sessions before solving. Dependencies: aero_model, aero_model.gradient, analyzer.adaptive_thresholds, analyzer.context. Outputs: report text, .sto file, multi-session JSON payload.
- `pipeline/report.py`: Top-level single-session and multi-session orchestration entrypoints. File focus: report. Dependencies: aero_model.gradient, analyzer.diagnose, analyzer.driver_style, analyzer.extract. Outputs: None.
- `solver/__init__.py`: Setup solve chain, objective function, legality checks, and search strategies. File focus:   init  . Dependencies: None. Outputs: None.
- `solver/arb_solver.py`: Setup solve chain, objective function, legality checks, and search strategies. File focus: arb solver. Dependencies: car_model.cars, track_model.profile. Outputs: None.
- `solver/bayesian_optimizer.py`: Setup solve chain, objective function, legality checks, and search strategies. File focus: bayesian optimizer. Dependencies: car_model.cars, track_model.profile. Outputs: None.
- `solver/bmw_coverage.py`: Setup solve chain, objective function, legality checks, and search strategies. File focus: bmw coverage. Dependencies: analyzer.telemetry_truth, car_model.setup_registry. Outputs: None.
- `solver/bmw_rotation_search.py`: Setup solve chain, objective function, legality checks, and search strategies. File focus: bmw rotation search. Dependencies: analyzer.segment, solver.candidate_search, solver.decision_trace, solver.solve_chain. Outputs: None.
- `solver/brake_solver.py`: Setup solve chain, objective function, legality checks, and search strategies. File focus: brake solver. Dependencies: analyzer.diagnose, analyzer.driver_style, analyzer.extract, analyzer.setup_reader. Outputs: None.
- `solver/candidate_ranker.py`: Setup solve chain, objective function, legality checks, and search strategies. File focus: candidate ranker. Dependencies: None. Outputs: None.
- `solver/candidate_search.py`: Candidate family generation and canonical-parameter override conversion. Dependencies: car_model.setup_registry, solver.candidate_ranker, solver.solve_chain. Outputs: None.
- `solver/corner_spring_solver.py`: Setup solve chain, objective function, legality checks, and search strategies. File focus: corner spring solver. Dependencies: car_model.cars, track_model.profile. Outputs: None.
- `solver/corner_strategy.py`: Setup solve chain, objective function, legality checks, and search strategies. File focus: corner strategy. Dependencies: analyzer.segment, car_model.cars. Outputs: None.
- `solver/coupling.py`: Setup solve chain, objective function, legality checks, and search strategies. File focus: coupling. Dependencies: None. Outputs: None.
- `solver/damper_solver.py`: Setup solve chain, objective function, legality checks, and search strategies. File focus: damper solver. Dependencies: car_model.cars, track_model.profile. Outputs: None.
- `solver/decision_trace.py`: Setup solve chain, objective function, legality checks, and search strategies. File focus: decision trace. Dependencies: analyzer.telemetry_truth, solver.bmw_coverage. Outputs: None.
- `solver/diff_solver.py`: Setup solve chain, objective function, legality checks, and search strategies. File focus: diff solver. Dependencies: analyzer.driver_style, analyzer.extract, car_model.cars, track_model.profile. Outputs: None.
- `solver/explorer.py`: Setup solve chain, objective function, legality checks, and search strategies. File focus: explorer. Dependencies: aero_model.interpolator, car_model.cars, track_model.profile. Outputs: None.
- `solver/full_setup_optimizer.py`: Setup solve chain, objective function, legality checks, and search strategies. File focus: full setup optimizer. Dependencies: car_model.garage, solver.arb_solver, solver.corner_spring_solver, solver.damper_solver. Outputs: None.

## Full Inventory

The exhaustive file-by-file inventory is written to `docs/repo_inventory.json`.
