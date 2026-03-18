Build this in 4 phases, not all at once.

Phase 1 — Create a true legal search space
Goal
Replace ad hoc parameter tweaking with a reusable object that can answer:

what parameters are searchable?

which are discrete vs continuous?

what are the legal values?

how do we enumerate or sample them?

how do we snap candidates to legal garage values?

Why first
Right now the code has this logic spread across:

car_model/setup_registry.py for canonical field definitions and some per-car bounds. 

solver/candidate_search.py for snapping. 

output/garage_validator.py for clamp/fix behavior. 

That’s enough to prototype, but not enough for an optimizer that searches “the entire legal manifold.”

What to implement
Add a new module, something like:

solver/legal_space.py

With dataclasses like:

SearchDimension

LegalSpace

LegalCandidate

SearchBounds

Responsibilities
LegalSpace.from_car(car, track_name)
Build all searchable dimensions from:

FIELD_REGISTRY

per-car specs

garage ranges

active garage model availability. 

Each dimension should expose
name

solver_step

kind: discrete / ordinal / continuous

legal_values() for discrete fields

sample(n) for continuous fields

snap(value)

clamp(value)

Important design choice
Do not search every field at maximum cardinality from day one.

Instead split the manifold into:

Tier A: high-leverage searchable parameters
wing

front/rear pushrod

heave spring / perch

third spring / perch

corner spring / torsion OD / rear spring perch

front/rear ARB blade

camber / toe

dampers

brake bias

diff preload

TC gain/slip

Tier B: mostly contextual / less urgent
master cylinders

pad compounds

gear stack

fuel warnings

lighting / metadata

That lets you get the main optimizer working before you broaden the state space.

Phase 2 — Define the scoring function you described
Goal
Turn your objective into code as an explicit multi-term score.

You already described the right form:

score =
  expected_lap_gain
  - platform_risk
  - driver_mismatch_penalty
  - telemetry_uncertainty
  - out_of_envelope_penalty
  - sim_build_staleness_penalty
That should become a single canonical score object, not scattered heuristics.

What to add
Create:

solver/objective.py

With dataclasses like:

ObjectiveBreakdown

RiskBreakdown

CandidateEvaluation

Suggested formula
Use something like:

total_score =
    + lap_gain_ms
    - 0.9 * platform_risk_ms
    - 0.6 * driver_mismatch_ms
    - 0.7 * telemetry_uncertainty_ms
    - 0.8 * envelope_penalty_ms
    - 0.4 * staleness_penalty_ms
Not because those weights are perfect, but because they are explicit and tunable.

How to compute each term
1. expected_lap_gain
Use the existing predictor/candidate ranker stack as the starting point. solver/candidate_search.py already stores predicted outputs and scores, so extend that instead of replacing it. 

2. platform_risk
Use:

front/rear excursion margin,

bottoming margin,

vortex-burst margin,

garage slider/travel margin,

legality correction count.
These concepts already exist in legality and garage validation. 

3. driver_mismatch_penalty
Use driver-style outputs from the analyzer:

trail-brake depth,

smoothness,

aggression,

throttle style.
The pipeline already computes driver style before solving. 

4. telemetry_uncertainty
Use TelemetrySignal quality/confidence, not raw “metric present?” checks. The repo already has the right abstraction for this. 

5. out_of_envelope_penalty
Use the multi-session reasoning path’s envelope / setup-distance concepts. That’s already where the repo quantifies “this candidate is unlike what has behaved well before.” 

6. sim_build_staleness_penalty
Add sim-version metadata to calibration assets and observations. Right now that concept is missing. This should be new work.

Phase 3 — Build a two-stage search engine
Goal
Search broadly, then refine aggressively.

Do not start with full Cartesian enumeration of all legal combinations. It will explode.

Best search strategy
Use a hybrid:

Stage 1: legal-family generation
Start from:

current setup,

base solver result,

candidate families,

weird edge anchors.
This fits naturally into the existing generate_candidate_families() path. 

Add new anchor families like:

min_drag_edge

max_platform_edge

max_rotation_edge

max_stability_edge

extreme_soft_mech

extreme_stiff_aero

legal_boundary_scan

These are not final answers — they are starting islands in the legal manifold.

Stage 2: local manifold exploration
For each family:

enumerate all discrete neighbors,

sample continuous parameters around it,

snap to legal increments,

reject only on hard legality,

keep weird-but-legal candidates.

This is where solver/explorer.py becomes useful. Its philosophy already aligns with your goal: explore “crazy” but legal setups. 

Stage 3: surrogate refinement
Use solver/bayesian_optimizer.py as a refinement layer after you have a better objective function. Right now its scorer is too heuristic and too small-dimensional, but the scaffold is there. 

Search algorithm recommendation
Initial implementation
Latin hypercube or Sobol over the legal space

family-based seeded sampling

top-K retention

Second implementation
NSGA-II style Pareto frontier for:

lap gain

legality margin

robustness

confidence

Third implementation
Bayesian optimization over the retained legal manifold

Phase 4 — Change veto behavior
Goal
Veto only on:

hard legality,

hard garage-correlation invalidity,

strong measured contradiction.

That matches what you asked for.

Current good foundation
The repo already has:

candidate fingerprints,

validation clusters,

failed-session veto concepts in reasoning. 

What to change
Right now the system is still conceptually too conservative in places. You want:

Hard veto
illegal discrete value

illegal continuous value after snap/clamp

garage-model invalid

direct predicted bottoming / vortex collapse beyond threshold

explicit conflict with trusted telemetry truth

Soft penalty only
unusual setup distance

unconventional parameter ratio

weak prediction confidence

out-of-envelope but not contradictory

stale build calibration

That distinction is critical. Otherwise the optimizer will keep collapsing back to “safe and conventional.”

Concrete implementation plan by file
1. car_model/setup_registry.py
Change
Promote this from “registry” to “authoritative search metadata source.” Add helpers:

iter_searchable_fields(car)

field_legal_values(car, key)

field_snap(car, key, value)

field_resolution(car, key)

Why
This file already defines field kinds, units, and per-car bounds/options. It should become the base contract for legal search. 

2. solver/legal_space.py (new)
Add
LegalSpace

SearchDimension

sample_seeded()

enumerate_discrete_subspace()

snap_candidate()

mutate_candidate()

Why
This centralizes legal-manifold mechanics instead of scattering them across search modules.

3. solver/candidate_search.py
Change
Turn it into the family generator + legal seed creator, not the full optimizer.

Add
new candidate families for edge cases,

metadata for:

is_extreme

is_boundary

soft_penalties

hard_veto_reasons

This module already has snapping and the SetupCandidate container, so it should remain the candidate-entry point. 

4. solver/objective.py (new)
Add
Canonical multi-objective evaluation.

Methods:

evaluate_candidate(...) -> CandidateEvaluation

compute_platform_risk(...)

compute_driver_mismatch(...)

compute_telemetry_uncertainty(...)

compute_staleness_penalty(...)

5. solver/explorer.py
Change
Keep the philosophy, replace the scoring guts.

Right now it uses a mostly heuristic score. You want it to call solver/objective.py and return:

frontier candidates,

top robust candidate,

top aggressive candidate,

top weird-but-legal candidate. 

6. solver/bayesian_optimizer.py
Change
Keep as optional phase-2 refinement only.

Required upgrades
expand parameter set,

use LegalSpace,

use ObjectiveBreakdown,

model uncertainty from telemetry confidence too, not just GP variance.

The current version is a decent prototype but too heuristic to be your final authority. 

7. solver/legality_engine.py
Change
Return more than valid: bool.

Extend LegalValidation with:

hard_veto: bool

legality_margin

garage_corrections_count

corrected_fields

constraint_violations

This file should become search-time infrastructure, not just final serialization-time validation. 

8. output/garage_validator.py
Change
Split current behavior into:

check_garage_correlation() pure validation

fix_garage_correlation() — corrective rewrite

measure_legality_margin() — scoreable distance from hard edges

Why
Search should usually score proximity to dangerous edges, not auto-correct candidates invisibly. Auto-correction is fine at final output time, but during search it can hide real candidate intent. 

9. pipeline/produce.py
Change
Add a new solve mode, something like:

--explore-legal-space

--search-budget

--keep-weird

--objective-profile robust|aggressive|balanced

This file is already the orchestration hub, so it should dispatch to the new legal-manifold optimizer after telemetry extraction and modifier generation. 

10. pipeline/reason.py
Change
Use multi-session reasoning to set:

envelope penalty,

confidence weighting,

validation vetoes,

target telemetry aspirations.

This module is where “measured evidence veto” should live. It already has the right concepts. 

Suggested execution order
Milestone 1 — legal manifold MVP
Implement:

solver/legal_space.py

registry helpers

hard-veto legality checks

search over high-leverage fields only

Deliverable:

can generate 500–5,000 legal candidates,

can retain weird but legal ones,

can score them by objective breakdown.

Milestone 2 — objective score
Implement:

solver/objective.py

confidence/risk terms

report score breakdown

Deliverable:

every candidate shows exactly why it ranked where it did.

Milestone 3 — search refinement
Implement:

edge-anchor families

local neighborhood expansion

optional Bayesian refinement

Deliverable:

can find non-obvious setups that the sequential solver misses.

Milestone 4 — report + UX
Add output sections:

best robust setup

best aggressive setup

best weird-but-legal setup

vetoed candidates and why

legality margin / confidence bars

Practical scoring template
If you want a starting point, I’d use this:

lap_gain_ms = predicted_lap_gain_ms

platform_risk_ms = (
    bottoming_risk_ms
    + vortex_risk_ms
    + slider_exhaustion_risk_ms
    + ride_height_collapse_risk_ms
)

driver_mismatch_ms = (
    trail_brake_mismatch_ms
    + throttle_style_mismatch_ms
    + smoothness_mismatch_ms
)

telemetry_uncertainty_ms = (
    missing_signal_penalty_ms
    + proxy_signal_penalty_ms
    + conflict_signal_penalty_ms
)

out_of_envelope_penalty_ms = (
    telemetry_envelope_distance_ms
    + setup_cluster_distance_ms
)

staleness_penalty_ms = (
    sim_build_age_ms
    + calibration_age_ms
)

total_score_ms = (
    lap_gain_ms
    - platform_risk_ms
    - driver_mismatch_ms
    - telemetry_uncertainty_ms
    - out_of_envelope_penalty_ms
    - staleness_penalty_ms
)
Important
Keep both:

total_score

and the full breakdown.

Never rank on a black box.

What not to do
Don’t brute-force the full Cartesian space first
Even legal-only search will blow up if you fully enumerate:

dampers,

ARBs,

springs,

toe/camber,

brake/diff/TC,

ride heights,

wing.

Use staged search.

Don’t auto-correct candidates during scoring
That hides whether the optimizer actually found a good idea or found an invalid idea that got repaired.

Don’t hard-reject unconventional setups just because they are far from historical norms
That defeats the whole point.

Don’t use lap-time prediction alone
You’ll end up selecting fragile monsters.

Best MVP scope
If I were implementing this in the repo, I would target this first:

Search dimensions
wing

front pushrod

rear pushrod

front heave

rear third

front torsion OD

rear spring

front ARB blade

rear ARB blade

front camber

rear camber

brake bias

diff preload

Fixed initially
dampers

toe

TC

migration/master cylinders

Then add the rest in phase 2.

That gives you a strong optimizer without immediately exploding dimensionality.

Recommended next step
If you want, I can turn this plan into a concrete implementation checklist by module and function, e.g.:

exact new dataclasses,

exact function signatures,

exact CLI flags,

and a step-by-step patch plan for solver/legal_space.py, solver/objective.py, solver/candidate_search.py, and pipeline/produce.py.

That would be the fastest path from strategy to code.

Commands I used
nl -ba solver/candidate_search.py | sed -n '1,260p'

nl -ba solver/bayesian_optimizer.py | sed -n '1,260p'

nl -ba solver/explorer.py | sed -n '1,260p'

nl -ba pipeline/produce.py | sed -n '1,260p'

nl -ba pipeline/reason.py | sed -n '1,260p'

nl -ba solver/legality_engine.py | sed -n '1,220p'

nl -ba car_model/setup_registry.py | sed -n '1,260p'

nl -ba output/garage_validator.py | sed -n '1,220p'

nl -ba analyzer/telemetry_truth.py | sed -n '1,220p'

Testing

✅ nl -ba solver/candidate_search.py | sed -n '1,260p'

✅ nl -ba solver/bayesian_optimizer.py | sed -n '1,260p'

✅ nl -ba solver/explorer.py | sed -n '1,260p'

✅ nl -ba pipeline/produce.py | sed -n '1,260p'

✅ nl -ba pipeline/reason.py | sed -n '1,260p'

✅ nl -ba solver/legality_engine.py | sed -n '1,220p'

✅ nl -ba car_model/setup_registry.py | sed -n '1,260p'

✅ nl -ba output/garage_validator.py | sed -n '1,220p'

✅ nl -ba analyzer/telemetry_truth.py | sed -n '1,220p'

