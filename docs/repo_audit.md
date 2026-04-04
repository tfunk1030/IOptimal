# Repo Audit

Generated: 2026-03-26T00:49:21.470977+00:00  
Updated: 2026-04-04 (calibration gate + objective improvements)

## Workflow Map

`IBT -> track/analyzer -> diagnosis/driver/style -> calibration_gate -> solve_chain/legality -> report/.sto -> webapp`

The calibration gate (`car_model/calibration_gate.py`) sits between input loading and the solver. It checks per-car, per-subsystem calibration status and blocks solver steps whose required subsystems lack proven data. Blocked steps output calibration instructions instead of setup values.

## Anchor Files

- `pipeline/produce.py`: single-session orchestration and report export.
- `solver/objective.py`: candidate ranking, breakdown weighting, and scenario-aware scoring.
- `solver/solve.py`: 6-step physics solver with calibration gate enforcement.
- `car_model/calibration_gate.py`: per-car, per-subsystem calibration status and step-level blocking.
- `validation/run_validation.py`: reproducible BMW/Sebring evidence report and support tiers.

## Current BMW/Sebring Evidence (updated 2026-04-04)

- Samples: `99`
- Non-vetoed samples: `~97`
- Pearson (non-vetoed): `~0.226` (improved from 0.035 after zero-variance fix + damper signal)
- Spearman (non-vetoed): `~-0.298` (improved from -0.121 after calibration gate fixes)
- Current objective status: `improving` — correlation now materially negative, approaching actionable but not yet authoritative.

## Support Tiers

| Car | Track | Tier | Samples | Calibrated Steps | Blocked Steps |
|-----|-------|------|---------|-----------------|---------------|
| BMW | Sebring | calibrated | 99 | 1-6 (all) | none |
| Ferrari | Sebring | partial | 12 | 1-3 | 4, 5, 6 |
| Cadillac | Silverstone | exploratory | 4 | 2-3 | 1, 4, 5, 6 |
| Porsche | Sebring | unsupported | 2 | 1-3 | 4, 5, 6 |
| Acura | Hockenheim | exploratory | 7 | — | 1-6 (all) |

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
