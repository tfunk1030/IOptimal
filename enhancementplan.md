# IOptimal Enhancement Plan - Evidence-Driven GTP Expansion

## Purpose

This document is the current roadmap for improving IOptimal from a BMW/Sebring-focused prototype into a broader, evidence-backed GTP setup system. The priority is not "support every car/track on paper". The priority is:

1. make the objective trustworthy on the one path that already has meaningful data,
2. generalize the tooling so new cars and tracks can be onboarded without copy-paste heuristics,
3. promote each new car/track only when the evidence says it is ready.

## Current State

### What the codebase already does

- `pipeline/produce.py`, `pipeline/reason.py`, and `solver/solve.py` run a full physics solve and optional legal-manifold search.
- `solver/scenario_profiles.py` provides `single_lap_safe`, `quali`, `sprint`, and `race`.
- `solver/legal_search.py` now treats free optimization as a search over the full legal setup manifold from a pinned baseline seed.
- `validation/run_validation.py` and `validation/objective_calibration.py` produce reproducible BMW/Sebring evidence from canonical setup mappings.
- The webapp uses the same scenario-profile path as the CLI.

### What is still weak

- BMW/Sebring is the only calibrated path, and even there the objective is still not strong enough to justify "optimal" claims.
- Ferrari, Cadillac, Porsche, and Acura do not have equivalent setup-schema confidence, telemetry truth, garage-truth fixtures, or observation volume.
- Several validation signals still use fallbacks or proxies instead of direct telemetry on some rows.
- The optimizer is better constrained than before, but the score model remains the limiting factor.

### Current support tiers

- BMW/Sebring: calibrated
- Ferrari/Sebring: partial
- Cadillac/Silverstone: exploratory
- Porsche/Sebring: unsupported
- Acura: unsupported

## Improvement Goals

### Goal 1: Trust the score before widening scope

The program should not claim an "optimal" setup until the ranking objective is materially predictive on holdout data. Current BMW/Sebring validation is useful, but still too weak to serve as a final authority.

### Goal 2: Make onboarding repeatable

Every new car and every new track should follow the same onboarding pipeline:

- legal setup schema
- telemetry extraction and signal quality
- garage-truth correlation
- canonical observation storage
- calibration report
- support-tier promotion

### Goal 3: Learn from data without violating garage/legal constraints

The system should become more data-aware over time, but learning must only bias choices inside the legal setup manifold and must never bypass iRacing garage limits or validated parameter relationships.

## Workstream A: BMW/Sebring Objective Hardening

This remains the highest-value work because every later expansion depends on it.

### What to improve

- Simplify or remove lap-gain terms that do not improve holdout stability.
- Strengthen the relationship between raw lap-gain terms and real lap time.
- Reduce fallback dependence in signal extraction for braking pitch, lock proxies, rear power slip, front excursion, and hot pressures.
- Add validation gates that fail when correlation, holdout stability, or signal quality regresses.

### Files to extend

- `solver/objective.py`
- `validation/objective_calibration.py`
- `validation/run_validation.py`
- `analyzer/extract.py`
- `analyzer/telemetry_truth.py`
- `tests/test_objective_calibration.py`
- `tests/test_validation_reporting.py`

### Implementation steps

1. Keep term-by-term ablation and holdout reporting in `validation/objective_calibration.py`.
2. For every harmful term, test reduction, reshape, or deletion against both in-sample and holdout BMW/Sebring evidence.
3. Move weak heuristics out of raw lap gain into confidence or envelope penalties when they are better treated as uncertainty than pace.
4. Add hard regression thresholds in validation tests so future edits cannot silently flip the sign or worsen stability.

### Exit criteria

- Non-vetoed BMW/Sebring score correlation is materially negative and stable on holdout.
- Worst-fold holdout no longer flips strongly positive.
- Runtime auto-apply can be reconsidered only after validation stays stable across multiple updates.

## Workstream B: General Car-Onboarding Framework

Every GTP car needs the same six layers of implementation before it should influence solver authority.

### Layer 1: Setup schema and garage legality

Objective: make the solver understand every legal garage control for that car.

Implementation:

- expand `car_model/setup_registry.py`
- confirm canonical parameter names in `validation/observation_mapping.py`
- add setup-schema tests and garage-range tests
- verify `.sto` round-trip behavior in the output layer

Required evidence:

- official iRacing manual or garage output confirmation for each control
- at least one garage-truth fixture or validated setup JSON per major subsystem

### Layer 2: Physical model parity

Objective: encode the suspension, aero, diff, and hybrid details that make the car different.

Implementation:

- extend `car_model/cars.py`
- add or validate aero maps in `aero_model/`
- confirm motion ratios, spring conventions, ARB labels, damper ranges, and diff options
- add car-specific quirks only when supported by telemetry or official docs

Required evidence:

- official iRacing manual and release notes
- repo-local telemetry and setup observations

### Layer 3: Telemetry extraction parity

Objective: ensure the analyzer sees the same class of signals for every car.

Implementation:

- validate channel coverage in `analyzer/extract.py`
- add fallback/proxy accounting in `validation/run_validation.py`
- add car-specific telemetry truth tests where the raw channels differ

Required evidence:

- multiple IBT files with stable signal extraction
- no silent `None` handling failures in diagnosis or state inference

### Layer 4: Garage-truth correlation

Objective: prove the program can reproduce legal garage states and setup relationships for the car.

Implementation:

- add fixture-backed tests for ride heights, spring/perch interactions, brake controls, diff controls, and TC mappings
- compare solver output against actual garage screenshots, `.sto` files, or setup JSONs

Required evidence:

- at least 5 good garage-truth fixtures before moving out of unsupported

### Layer 5: Observation learning

Objective: store the car's sessions in canonical form and let the calibration/reporting pipeline reason about them.

Implementation:

- ingest observations into `data/learnings/observations/`
- ensure deltas and prediction-feedback corrections work for that car
- add support-tier rows to `validation/objective_validation.json`

Required evidence:

- enough sessions to distinguish exploratory from partial from calibrated

### Layer 6: Scenario unlock

Objective: allow `quali`, `sprint`, `race`, and `single_lap_safe` to drive candidate ranking for that car.

Implementation:

- only after the base objective is directionally correct for the car/track pair
- add scenario sanity checks in `solver/scenario_profiles.py`
- add legal-search regressions showing the scenario profiles produce distinct legal outputs

Required evidence:

- scenario differences are visible in legal setups and not just weight changes on a noisy score

## Workstream C: Car-by-Car Expansion Order

### Ferrari 499P

Why next:

- already has repo-local research and partial Sebring evidence
- closest to being promotable after BMW

Main blockers:

- rear suspension indexing and corner-spring fidelity
- validating Ferrari-specific garage schema against actual garage states

Implementation order:

1. finish Ferrari setup-schema parity
2. add Ferrari garage-truth fixtures
3. build Ferrari/Sebring calibration report
4. unlock Ferrari scenario search only after base ranking is credible

### Cadillac V-Series.R

Why next:

- exploratory Silverstone data already exists
- good candidate for proving the track/car onboarding process outside Sebring

Main blockers:

- low observation count
- not enough track-specific garage truth

Implementation order:

1. increase Cadillac observation coverage on Silverstone
2. validate setup schema and garage ranges
3. add track-aware calibration report for Cadillac/Silverstone
4. hold Cadillac at exploratory or partial until correlation and fixtures improve

### Porsche 963

Why later:

- unsupported today
- likely needs explicit roll-spring / setup-schema cleanup before calibration is meaningful

Implementation order:

1. confirm Porsche setup schema and roll-spring mapping
2. add Porsche telemetry truth fixtures
3. collect enough observations on one track before widening further

### Acura ARX-06

Why last:

- unsupported and lacking current repo-local evidence

Implementation order:

1. build complete setup registry and garage-output fixtures
2. confirm aero/hybrid/control mappings
3. collect observation baseline on one track
4. only then wire into calibration and scenario search

## Workstream D: Track-Onboarding Framework

Cars are only half the problem. Each track changes the target operating window.

### What a new track needs

- a canonical track profile in `data/tracks/`
- validated alias mapping so the same venue/config is not split across names
- enough IBT sessions to characterize surface severity, speed bands, braking demands, and aero platform needs
- track-specific validation rows in the evidence report

### Implementation steps

1. Normalize the track/config identity in `track_model/` and validation mapping.
2. Generate or refresh the track profile from IBT telemetry.
3. Verify that analyzer outputs for the track are stable enough to drive solver modifiers.
4. Run the solver against real setups from that track and compare to garage truth.
5. Promote the track only when at least one car/track pair has reproducible evidence.

### Promotion rule

A track should not be treated as broadly "supported". Support belongs to a car/track pair, not a track in isolation.

## Workstream E: Learning System Improvements

The current learner stores useful observations, but it needs stronger structure to scale across all GTP cars and tracks.

### Improvements

- partition empirical models by car/track pair and keep global priors separate from local evidence
- store signal-authority metadata on every learned observation
- down-rank or exclude corrections learned from fallback-only signals
- make prediction-feedback corrections visible in reports so users can see where physics and measurement disagree

### Files to extend

- `learner/knowledge_store.py`
- `learner/observation.py`
- `learner/empirical_models.py`
- `learner/recall.py`
- `validation/run_validation.py`

### Implementation rule

Learnings may bias target selection, priors, and uncertainty. They may not emit illegal parameter values or bypass garage validation.

## Workstream F: Optimizer and Search Improvements

Search quality matters, but it should follow evidence quality rather than race ahead of it.

### Near-term improvements

- batch evaluation and caching in `solver/objective.py`
- better candidate family coverage in `solver/legal_search.py`
- scenario-specific acceptance summaries in reports
- Pareto output: lap gain vs platform risk vs uncertainty

### Later improvements

- structured grid or Sobol search for the most sensitive subspaces
- local-neighborhood polish on accepted candidates
- sensitivity heatmaps and cluster reports for high-scoring regions

### Constraint

Search improvements should stay inside the legal-manifold framework already in place. Do not reintroduce out-of-range or garage-incoherent candidates in the name of exploration.

## Workstream G: User-Facing Confidence and Reporting

The UI and output files should make the program's authority obvious.

### Improvements

- show support tier per car/track pair in the webapp and reports
- show scenario profile, signal quality, and fallback usage on every run
- mark unsupported or exploratory outputs clearly instead of presenting them like calibrated setups
- expose the decision trace and accepted legal-search family in report output by default

### Files to extend

- `pipeline/report.py`
- `webapp/services.py`
- `webapp/templates/`
- `output/report.py`

## Evidence Thresholds for Promotion

These thresholds are intentionally conservative.

### Unsupported

- missing setup-schema confidence
- missing garage-truth fixtures
- fewer than 5 useful observations

### Exploratory

- legal schema mostly mapped
- at least 5 observations
- some telemetry truth exists
- no reliable ranking claim yet

### Partial

- at least 10 observations
- garage-truth coverage for the core setup controls
- directionally correct but still weak objective evidence

### Calibrated

- reproducible canonical mapping
- strong garage-truth coverage
- enough observations for stable holdout validation
- score is directionally and practically useful, not just negative in-sample

## Recommended Phase Order

### Phase 1

- BMW/Sebring objective hardening
- validation gates
- signal-quality reduction of fallback dependence

### Phase 2

- Ferrari/Sebring schema and garage-truth parity
- Ferrari calibration report

### Phase 3

- Cadillac/Silverstone evidence expansion
- Cadillac legality and garage-truth parity

### Phase 4

- Porsche setup-schema and telemetry truth foundation

### Phase 5

- Acura setup registry, telemetry truth, and first supported car/track pairing

### Phase 6

- track-onboarding pipeline generalization
- multi-track support tier reporting

### Phase 7

- optimizer speed and search improvements
- UI/reporting confidence surfacing

## Data Policy

Use only:

- official iRacing manuals and release notes
- repo-local telemetry, setups, observations, and garage outputs

Do not promote a new car or track on anecdotal setup lore alone.

## Definition of Success

The program is "working" when:

- every promoted car/track pair has explicit evidence behind it,
- the UI reports confidence honestly,
- legal-manifold search never emits illegal or garage-incoherent candidates,
- the objective ranking is good enough that setup recommendations beat or at least match known good baselines often enough to be trusted.
