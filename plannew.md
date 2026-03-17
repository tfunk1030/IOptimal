# PR16: Make Candidate Families Physically Real and Use Them in Both Pipelines

## Summary
- Replace family "copy solved outputs and mutate fields" behavior with override-based family materialization through one canonical solve chain.
- Run that same family-selection flow in both multi-session (`pipeline.reason`) and single-session (`pipeline.produce`) paths.
- Fix candidate ranking so signed understeer is scored by movement toward neutral, not by raw numeric decrease.

## Key Changes
- Extract the canonical base solve into a shared solve-chain module that both pipeline entrypoints call. It should own:
  - optimizer-vs-sequential selection
  - the existing fixed-point heave/damper refinement from `pipeline.produce`
  - ride-height reconciliation, damper modifier application, Ferrari passthrough guards, supporting solve, legality validation, and decision-trace refresh
- Replace `solver/candidate_search.py`'s deep-copy mutation flow with a `FamilyOverrides`-style model. Family heuristics still decide incremental/compromise/reset targets, but they populate overrides instead of mutating `step1..step6`.
- Materialize each family by rerunning downstream computations from those overrides. The candidate object should hold:
  - the overrides used
  - fresh `step1..step6` outputs
  - a fresh supporting solution, then family-specific supporting adjustments applied on top
  - candidate-specific legality, prediction, score, and notes
- Add explicit builder paths where copied fields are currently stale:
  - `RakeSolver`: build step1 from explicit pushrod overrides
  - `ARBSolver`: build step4 from explicit bar/blade settings
  - `WheelGeometrySolver`: build step5 from explicit camber/toe settings
  - `DamperSolver`: build step6 from explicit click/slope settings
  - keep using `CornerSpringSolver.solution_from_explicit_rates(...)` for explicit step3 materialization
- Remove the raw swap contract in `_apply_selected_candidate_outputs`. `pipeline.reason` must apply the selected family by materializing it through the shared solve chain, not by returning `selected_candidate.step*` directly.
- In `pipeline.produce`, run candidate generation/selection after the base solve. For single-session mode use:
  - authority session = current session
  - best session = current session
  - `envelope_distance=0`
  - `setup_distance=0`
  - `setup_cluster=None`
  - `prediction_corrections={}`
  - neutral authority score default `0.75`
- Align single-session output with multi-session output:
  - remove the placeholder `"candidate_family_selection"` string
  - emit `generated_candidates`, `selected_candidate_family`, `selected_candidate_score`, and `selected_candidate_applied`
  - pass selected-family context into the report so the compact report shows the same candidate-selection section as `pipeline.reason`
- In `solver/candidate_ranker.py`, replace the current understeer scoring with a target-distance helper that treats smaller absolute distance to `0.0` as better for both low-speed and high-speed understeer. Leave rear power slip on the existing lower-is-better path.
- Failure handling:
  - if a family cannot be materialized or is illegal, mark it unselectable and continue
  - if all families fail, keep the base solution and set `selected_candidate_applied=false`

## Interfaces / Types
- Add a shared solve-context/result contract for the canonical solve chain so both pipelines and candidate search use the same entrypoint.
- Add a family-override type in `solver/candidate_search.py`; `SetupCandidate` should expose both overrides and fully materialized outputs.
- Retire the internal "selected candidate outputs replace final payload" helper contract.

## Test Plan
- Update candidate-family tests to prove recomputation of derived fields:
  - spring/ARB changes update `lltd_achieved`
  - geometry overrides update dynamic camber fields/checks
  - damper overrides update derived damper metadata
  - candidates no longer inherit stale travel/bottoming margins from the base solve
- Add ranking regressions showing:
  - `-0.10 -> 0.00` is an improvement
  - `-0.10 -> -0.20` is a regression
- Replace the current reasoning test that asserts raw payload swapping with one that asserts rematerialization through the shared chain.
- Add a single-session regression proving `produce_result(...)` now generates/applies candidate families and emits structured candidate metadata.
- Validate with `python -m unittest tests.test_candidate_search tests.test_reasoning_veto tests.test_bmw_sebring_garage_truth tests.test_output_report_supporting`.

## Assumptions
- Single-session mode stays on neutral priors rather than inventing new envelope/cluster heuristics in this PR.
- Ferrari indexed parameters stay pinned wherever there is no physical solver/builder path; family materialization only changes fields with a real physical path.
- No CLI changes are required; this PR changes solver behavior and output consistency only.
