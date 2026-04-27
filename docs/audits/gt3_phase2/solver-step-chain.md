# GT3 Phase 2 Audit: Step 2 entry points and orchestration

This audit covers the four files that enter, orchestrate, or directly read
Step 2 (heave/third spring) results. The Phase 0 work shipped
`SuspensionArchitecture.GT3_COIL_4WHEEL`, `HeaveSolution.null()`, and the
`step2.present` sentinel; this audit identifies where downstream code still
assumes a real (`present=True`) heave solution and would crash, divide by
zero, or silently emit garbage on a GT3 car.

## Scope

- `solver/heave_solver.py` — Step 2 solver (HeaveSolver) and `HeaveSolution` dataclass; reads `car.heave_spring.*`, `car.front_heave_spring_nmm`, `car.rear_third_spring_nmm`, `car.tyre_vertical_rate_*_nmm` to size front heave + rear third springs.
- `solver/solve_chain.py` — main 6-step orchestrator; instantiates `HeaveSolver`, holds `_run_sequential_solver`, `_run_branching_solver`, `materialize_overrides`. Routes `step2.front_heave_nmm` / `step2.rear_third_nmm` into Steps 3, 5, 6.
- `solver/heave_calibration.py` — empirical heave spring → platform σ model from IBT data. File-keyed by `(car, track)` so it auto-skips for cars with no calibration; not architecture-gated.
- `solver/solve.py` — CLI entry point (`python -m solver.solve`); runs the 6 steps sequentially (gated only by `cal_gate.step_is_runnable`) and writes `.sto` / JSON / report. Also feeds `step2` into stint / sector / sensitivity / Bayesian / legal-search analyses.

## Summary table

| Risk | Count | Files |
|---|---|---|
| BLOCKER | 9 | `heave_solver.py` (5), `solve_chain.py` (2), `solve.py` (2) |
| DEGRADED | 7 | `heave_solver.py` (2), `solve_chain.py` (3), `solve.py` (2) |
| COSMETIC | 3 | `heave_solver.py` (1), `solve_chain.py` (1), `heave_calibration.py` (1) |

GT3 cars have `heave_spring=None` (enforced by the `__post_init__` invariant
in `car_model/cars.py:1767-1771`). Every BLOCKER below is a site that would
hit `AttributeError: 'NoneType' object has no attribute X` or compute on
`HeaveSolution.null()` placeholder zeros and silently propagate them.

## Findings

### Finding 1: HeaveSolver instantiated unconditionally for GT3 cars

- File: `solver/solve_chain.py:399` (sequential), `solver/solve_chain.py:577` (branching), `solver/solve_chain.py:1145` (materialize_overrides), `solver/solve.py:439` (CLI), `solver/solve.py:580` (CLI fallback)
- Code:
  ```python
  heave_solver = HeaveSolver(car, track)
  step2 = heave_solver.solve(...)
  ```
- What this expects: `car.heave_spring` is non-None. The body of `HeaveSolver.solve` reads `hsm = self.car.heave_spring` at line 984 and dereferences `hsm.sigma_target_mm`, `hsm.front_m_eff_kg`, `hsm.rear_spring_range_nmm`, `hsm.front_spring_range_nmm` (via `_heave_hard_bounds`), etc. on every path.
- Risk: BLOCKER
- Recommended GT3 handling: branch on `car.suspension_arch.has_heave_third`. If False, set `step2 = HeaveSolution.null(front_dynamic_rh_mm=step1.dynamic_front_rh_mm, rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm)` and SKIP the `HeaveSolver(...)` constructor call entirely. Concrete shape:
  ```python
  if car.suspension_arch.has_heave_third:
      heave_solver = HeaveSolver(car, track)
      step2 = heave_solver.solve(...)
  else:
      step2 = HeaveSolution.null(
          front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
          rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
      )
  ```
- Effort estimate: 3 hours (5 sites + tests).
- Notes: All five sites are independent — they cannot be DRY'd into one helper without rewriting the orchestrators. Each site has slightly different argument plumbing (current_setup anchors, prediction corrections, perch targets). The branch goes immediately AFTER Step 1 succeeds.

### Finding 2: `_run_sequential_solver` calls `heave_solver.reconcile_solution` after Step 3

- File: `solver/solve_chain.py:432-439`
- Code:
  ```python
  heave_solver.reconcile_solution(
      step1, step2, step3,
      fuel_load_l=fuel,
      front_camber_deg=_front_camber(inputs),
      verbose=False,
  )
  ```
- What this expects: `heave_solver` exists and `step2.front_heave_nmm > 0`. `reconcile_solution` (`heave_solver.py:1557-1648`) calls `self.car.active_garage_output_model(...)` and reads `self.car.heave_spring.sigma_target_mm`, `self.car.damper.front_ls_coefficient_nsm` to recompute travel budget for the FRONT heave spring.
- Risk: BLOCKER
- Recommended GT3 handling: skip when `step2.present is False`. Add early-return guard at the top of `reconcile_solution`:
  ```python
  if not step2.present:
      return  # GT3 cars have no heave to reconcile
  ```
  AND skip the call sites (`solve_chain.py:432, 635, 1262, 1366, 1480`, `solve.py:479`).
- Effort estimate: 1 hour.
- Notes: The function has six call sites. Add the guard once at the top of `reconcile_solution`; downstream callers can safely call it without checking `step2.present` themselves.

### Finding 3: `_run_branching_solver` passes step2 candidates into 144-path scoring loop

- File: `solver/solve_chain.py:580-594` and `614-739`
- Code:
  ```python
  heave_candidates = heave_solver.solve_candidates(
      dynamic_front_rh_mm=step1.dynamic_front_rh_mm, ...
  )
  ...
  for s2 in heave_candidates:
      ...
      _params = solver_steps_to_params(s1_copy, s2_copy, s3_copy, s4, s5, s6, car=car)
  ```
- What this expects: `heave_solver` exists and emits ≥1 real candidate; `solve_candidates` itself reads `self._heave_hard_bounds()` → `hsm.front_spring_range_nmm`. For GT3 there is no Step 2 to branch on — the front-axle stiffness is set in Step 3 by the corner spring instead.
- Risk: BLOCKER
- Recommended GT3 handling: branch on `car.suspension_arch.has_heave_third`. If False, set `heave_candidates = [HeaveSolution.null(...)]` (a single null candidate) so the outer loop runs ONCE and Step 3 still gets to fan out across corner spring candidates. The branching factor for GT3 becomes `max_corner × max_arb` instead of `max_heave × max_corner × max_arb`.
- Effort estimate: 2 hours (covers branching solver + path scoring sanity check).
- Notes: `solver_steps_to_params(s1, s2, ...)` is in `solver/objective.py` (not in this audit's scope) but it currently reads `s2.front_heave_nmm`. That helper needs its own GT3 handling — flag for the objective audit unit.

### Finding 4: `materialize_overrides` re-solves Step 2 unconditionally when earliest≤3

- File: `solver/solve_chain.py:1143-1232` and `1301-1336`
- Code:
  ```python
  rebuild_step23 = earliest <= 3
  if rebuild_step23:
      heave_solver = HeaveSolver(car, track)
      ...
      step2_targets = {
          "front_heave_nmm": overrides.step2.get(
              "front_heave_nmm",
              public_output_value(car, "front_heave_nmm", step2.front_heave_nmm),
          ), ...
      }
      ...
      step2 = heave_solver.solve(...)  # or solution_from_explicit_settings(...)
  ```
- What this expects: All re-solve paths assume `step2` carries real data. `step2.front_heave_nmm` and `step2.perch_offset_front_mm` are read from the prior solve; `public_output_value(car, "front_heave_nmm", 0.0)` on a GT3 car will return `None` or 0 and feed back into a `HeaveSolver.solve` that crashes on `car.heave_spring=None`.
- Risk: BLOCKER
- Recommended GT3 handling: gate the entire `if rebuild_step23` block on architecture, and replace the `step2 = heave_solver.solve(...)` lines with `step2 = HeaveSolution.null(...)` for GT3:
  ```python
  if rebuild_step23:
      if car.suspension_arch.has_heave_third:
          heave_solver = HeaveSolver(car, track)
          ...  # existing logic
      else:
          step2 = HeaveSolution.null(
              front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
              rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
          )
          # corner_solver still runs for Step 3 — front+rear coil
          corner_solver = CornerSpringSolver(car, track)
          step3 = corner_solver.solve(...)
  ```
- Effort estimate: 3 hours.
- Notes: This function is the legal-manifold search re-solver — it gets called once per candidate setup the search proposes. For GT3 the search space drops the `front_heave_nmm`/`rear_third_nmm` axes entirely; coordinate with the legal-search audit unit.

### Finding 5: Step 2 → Step 3 plumbing reads `step2.front_heave_nmm` / `step2.rear_third_nmm` directly

- File: `solver/solve_chain.py:424-429`, `467-472` (sequential); `solver/solve_chain.py:617-621` (branching); `solver/solve_chain.py:1255-1260`, `1359-1364` (materialize_overrides); `solver/solve.py:467-470`, `587-589` (CLI)
- Code:
  ```python
  step3 = corner_solver.solve(
      front_heave_nmm=step2.front_heave_nmm,
      rear_third_nmm=step2.rear_third_nmm,
      fuel_load_l=fuel,
      ...
  )
  ```
- What this expects: real heave/third rates. On `HeaveSolution.null()` both fields are 0.0. `CornerSpringSolver.solve` (out of scope for this audit) needs to handle 0 sensibly OR Step 2 → Step 3 plumbing must skip the heave/third arguments entirely for GT3.
- Risk: BLOCKER
- Recommended GT3 handling: branch on `step2.present`. Either
  - (a) make `front_heave_nmm` and `rear_third_nmm` Optional in `CornerSpringSolver.solve` and let it read None for GT3, or
  - (b) at the call sites pass 0.0 and ensure `corner_spring_solver.py` doesn't blend heave into corner stiffness when `car.suspension_arch.has_heave_third is False`.
  Option (a) is cleaner — flag for the corner spring audit unit. At THIS unit's call sites, the safer change is conditional argument:
  ```python
  step3 = corner_solver.solve(
      front_heave_nmm=step2.front_heave_nmm if step2.present else None,
      rear_third_nmm=step2.rear_third_nmm if step2.present else None,
      ...
  )
  ```
- Effort estimate: 2 hours (8 sites).
- Notes: Cross-cutting with the corner spring audit. Low risk of a silent wrong-answer for GT3 if `CornerSpringSolver.solve` does not actually use `front_heave_nmm` for non-GTP cars (which is the right architectural answer). But the call sites still need to pass legal arguments.

### Finding 6: Step 2 → Step 6 (DamperSolver) plumbing in solve_chain and solve.py

- File: `solver/solve_chain.py:507-509`, `685-687`, `964-965`, `1483-1484`, `1499-1500`; `solver/solve.py:587-589`
- Code:
  ```python
  step6 = damper_solver.solve(
      ...
      front_heave_nmm=step2.front_heave_nmm,
      rear_third_nmm=step2.rear_third_nmm,
  )
  ```
- What this expects: real spring rates, used to size HS damping coefficient via ζ-cal. On `null()` the DamperSolver receives 0/0 — most likely silently divides by zero or chooses wildly off-axis HS coefficients.
- Risk: BLOCKER
- Recommended GT3 handling: same pattern as Finding 5 — pass `None` (or skip the kwarg) when `step2.present is False`. The DamperSolver itself is GT3's authoritative spring source via Step 3 corner springs; flag for the damper audit unit.
- Effort estimate: 1 hour (call-site change only; DamperSolver internals out of scope).
- Notes: Six call sites across two files. Verify `damper_solver.solve(front_heave_nmm=None, rear_third_nmm=None)` is a legal signature before landing.

### Finding 7: `solver_steps_to_params` and `_apply_candidate_params_to_steps` write to step2 fields

- File: `solver/solve.py:91-94`
- Code:
  ```python
  direct_fields = {
      ...
      "front_heave_spring_nmm": (step2, "front_heave_nmm"),
      "front_heave_perch_mm": (step2, "perch_offset_front_mm"),
      "rear_third_spring_nmm": (step2, "rear_third_nmm"),
      "rear_third_perch_mm": (step2, "perch_offset_rear_mm"),
      ...
  }
  ```
  Also `solver/solve.py:793-794`:
  ```python
  baseline_params = {
      ...
      "front_heave_spring_nmm": step2.front_heave_nmm,
      "rear_third_spring_nmm": step2.rear_third_nmm,
      ...
  }
  ```
- What this expects: the legal-search candidate emits `front_heave_spring_nmm` and `rear_third_spring_nmm` as legal-manifold axes. For GT3 these axes don't exist — the legal-search must not propose values for them.
- Risk: DEGRADED (silently sets heave fields on `HeaveSolution.null()` from search candidates that the search shouldn't have proposed).
- Recommended GT3 handling: conditional inclusion in `direct_fields` and `baseline_params` when `car.suspension_arch.has_heave_third`:
  ```python
  direct_fields = {
      "front_pushrod_offset_mm": (step1, "front_pushrod_offset_mm"),
      ...
  }
  if car.suspension_arch.has_heave_third:
      direct_fields.update({
          "front_heave_spring_nmm": (step2, "front_heave_nmm"),
          "front_heave_perch_mm": (step2, "perch_offset_front_mm"),
          "rear_third_spring_nmm": (step2, "rear_third_nmm"),
          "rear_third_perch_mm": (step2, "perch_offset_rear_mm"),
      })
  ```
  Note: `_apply_candidate_params_to_steps` currently doesn't take `car` as a parameter — needs a signature update.
- Effort estimate: 1 hour.
- Notes: Legal-search itself sits in `solver/legal_search.py` (out of scope for this audit unit) and may be the better place to drop these axes. Coordinate with the legal-search audit unit.

### Finding 8: `apply_learned_corrections` writes to `car.heave_spring.front_m_eff_kg`

- File: `solver/solve.py:296-299`
- Code:
  ```python
  if learned.heave_m_eff_front_kg is not None:
      car.heave_spring.front_m_eff_kg = learned.heave_m_eff_front_kg
  if learned.heave_m_eff_rear_kg is not None:
      car.heave_spring.rear_m_eff_kg = learned.heave_m_eff_rear_kg
  ```
- What this expects: `car.heave_spring` is non-None.
- Risk: BLOCKER (NoneType.front_m_eff_kg = ... on GT3 if a learner correction file exists for the car).
- Recommended GT3 handling: null-guard around the access:
  ```python
  if car.heave_spring is not None:
      if learned.heave_m_eff_front_kg is not None:
          car.heave_spring.front_m_eff_kg = learned.heave_m_eff_front_kg
      if learned.heave_m_eff_rear_kg is not None:
          car.heave_spring.rear_m_eff_kg = learned.heave_m_eff_rear_kg
  ```
- Effort estimate: 15 min.
- Notes: `learned.heave_m_eff_front_kg` will be `None` for GT3 cars in practice (no IBT calibration data yet), so this rarely triggers — but a stale learner JSON for a GT3 car path could blow up at startup.

### Finding 9: `solve.py` Step 6 fallback declaration uses `damper_solver` from outer scope

- File: `solver/solve.py:577-580`
- Code:
  ```python
  try:
      damper_solver
  except NameError:
      damper_solver = DamperSolver(car, track)
  step6 = damper_solver.solve(...)
  ```
- What this expects: nothing GT3-specific, but Step 6 also reads `step2.front_heave_nmm` (line 587). Captured in Finding 6.
- Risk: COSMETIC (not GT3-specific, but the NameError dance is a code smell — would benefit from explicit init).
- Recommended GT3 handling: no change required for GT3 readiness.
- Effort estimate: N/A.
- Notes: Mention only because it's adjacent to other Step 2/Step 6 wiring.

### Finding 10: `HeaveSolver._rear_corner_wheel_rate_nmm` reads `car.corner_spring.rear_motion_ratio`

- File: `solver/heave_solver.py:258-268`
- Code:
  ```python
  def _rear_corner_wheel_rate_nmm(self, rear_spring_nmm: float | None = None) -> float:
      ...
      return max(rear_spring_rate_nmm, 0.0) * self.car.corner_spring.rear_motion_ratio ** 2
  ```
- What this expects: `car.corner_spring.rear_motion_ratio` exists. For GT3 it does (corner-coil cars also use motion ratios), so this is internal to HeaveSolver and only matters if HeaveSolver is reached. Once Finding 1 lands, this code is unreachable for GT3.
- Risk: COSMETIC — validate that GT3 corner_spring sub-models always populate `rear_motion_ratio`.
- Recommended GT3 handling: no change required if Finding 1 lands; HeaveSolver is never instantiated for GT3.
- Effort estimate: N/A.
- Notes: GT3 `rear_motion_ratio` is currently 1.0 in the BMW M4 GT3 / Aston / Porsche 992 stubs (per Phase 0 PR #102). The wheel-rate calculation for GT3 happens in CornerSpringSolver, not here.

### Finding 11: `HeaveSolver.excursion`, `min_rate_for_*`, `solve` all assume `car.heave_spring` non-None

- File: `solver/heave_solver.py:280-302` (`_shared_vertical_mass_kg`), `364-368` (`min_rate_for_no_bottoming`), `434-438` (`min_rate_for_constraint_set`), `528-532` (`min_rate_for_sigma`), `984-994` (`solve`)
- Code (representative):
  ```python
  reference_rate_nmm = (
      self.car.front_heave_spring_nmm
      if is_front
      else self.car.rear_third_spring_nmm
  )
  ...
  lo, hi = (
      self._heave_hard_bounds()
      if axle == "front"
      else self.car.heave_spring.rear_spring_range_nmm
  )
  ```
- What this expects: `car.front_heave_spring_nmm`, `car.rear_third_spring_nmm`, and `car.heave_spring.*` are all populated.
- Risk: BLOCKER (correctly enforced by Finding 1 if HeaveSolver is never instantiated for GT3; otherwise crash).
- Recommended GT3 handling: defensively raise `ValueError("HeaveSolver requires car.suspension_arch.has_heave_third")` in `__init__` so any accidental future call site fails loudly:
  ```python
  def __init__(self, car: CarModel, track: TrackProfile):
      if not car.suspension_arch.has_heave_third:
          raise ValueError(
              f"HeaveSolver does not apply to {car.canonical_name} "
              f"(suspension_arch={car.suspension_arch.value}). Use "
              f"HeaveSolution.null() instead."
          )
      self.car = car
      self.track = track
  ```
- Effort estimate: 30 min (constructor guard + targeted test).
- Notes: This is defense-in-depth. The primary fix is at the call sites (Finding 1).

### Finding 12: `HeaveSolution` dataclass field ordering — `present: bool = True` after default-factory fields

- File: `solver/heave_solver.py:50-110`
- Code:
  ```python
  @dataclass
  class HeaveSolution:
      front_heave_nmm: float
      rear_third_nmm: float
      ...
      safety_checks: list[SpringSafetyCheck] = field(default_factory=list)
      garage_constraints_ok: bool = True
      garage_constraint_notes: list[str] = field(default_factory=list)
      parameter_search_status: dict = None
      parameter_search_evidence: dict = None
      present: bool = True
  ```
- What this expects: `present=True` is the default sentinel for "real Step 2 output". `null()` builds a HeaveSolution with `present=False`. The default-True is a reasonable backward-compat choice, but it means EVERY non-`null()` construction site silently inherits `present=True` and won't produce a meaningful sentinel if a future bug zeros out other fields.
- Risk: COSMETIC.
- Recommended GT3 handling: no change required. The pattern is correct for backward-compat. Document the intent in the docstring (already done at lines 105-110).
- Effort estimate: N/A.
- Notes: The dataclass forbids any non-default field after `present`, which constrains future field additions to also have defaults. Worth a comment.

### Finding 13: `HeaveCalibration.load(car, track)` keyed by car string — silently empty for GT3

- File: `solver/heave_calibration.py:79-98`, `115-116`
- Code:
  ```python
  @classmethod
  def load(cls, car: str, track: str) -> "HeaveCalibration":
      cal = cls(car, track)
      path = cal._path()
      if not path.exists():
          return cal  # empty calibration
  ...
  def _path(self) -> Path:
      return _LEARNINGS_DIR / f"heave_calibration_{self.car}_{self.track}.json"
  ```
- What this expects: file may or may not exist. For GT3 cars no file will exist → returns an empty calibration → `predict_sigma` falls through to `_physics_fallback`.
- Risk: DEGRADED — `_physics_fallback` (lines 252-270) hardcodes BMW-tuned constants (`k_opt = 75.0`, `s_min = 5.0`, `alpha_soft=0.012`) that are wrong for GT3 (which doesn't have a heave spring) and would emit nonsense if a caller invoked `HeaveCalibration.predict_sigma(...)` for a GT3 car-track pair.
- Recommended GT3 handling: this module should NEVER be reached for GT3 cars because Step 2 is skipped. But add a defensive guard at module entry (`HeaveCalibration.load`) that raises if `car_model.cars.get_car(car).suspension_arch.has_heave_third is False`. Alternatively, leave the module untouched since no caller wires it up for GT3 (verify via Grep).
- Effort estimate: 30 min (audit grep + optional defensive guard).
- Notes: Search confirms no `solve_chain.py` or `solve.py` site calls `HeaveCalibration` directly. The module is currently used in `pipeline/produce.py` and `learner/`. Flag those for the pipeline / learner audit units.

### Finding 14: `solve.py` extra analyses (stint, sector, sensitivity, Bayesian, multi-speed) gated by `step2 is not None`

- File: `solver/solve.py:655-666` (stint), `669-685` (sector), `687-703` (sensitivity), `720-737` (multi-speed), `755-775` (Bayesian)
- Code (representative):
  ```python
  if step2 is not None:
      try:
          from solver.stint_model import analyze_stint
          stint_result = analyze_stint(
              car=car,
              ...
              base_heave_nmm=step2.front_heave_nmm,
              base_third_nmm=step2.rear_third_nmm,
              ...
          )
      except Exception as e:
          log(f"[stint] Skipped: {e}")
  ```
- What this expects: `step2 is not None` ⇒ real heave/third values. After Finding 1, GT3 will have `step2 = HeaveSolution.null()` (not None) with `step2.present=False` and zero rates.
- Risk: DEGRADED — stint/sector/sensitivity/Bayesian/multi-speed analyzers will receive 0.0 for `front_heave_nmm` / `rear_third_nmm` and either crash or produce nonsense.
- Recommended GT3 handling: replace `if step2 is not None:` with `if step2 is not None and step2.present:`. Five call sites in solve.py. Each analyzer also needs its own GT3 handling — flag for the analyzer audit units.
- Effort estimate: 1 hour (5 sites + verify analyzers don't independently fail on present=False).
- Notes: Same pattern applies to `solver/solve.py:705-717` (`--space`, `solver/setup_space.py`).

### Finding 15: `cal_gate.step_is_runnable(2)` is car-architecture-blind

- File: `solver/solve.py:434-456`, also `solver/solve_chain.py` (no direct check there — solve_chain trusts the optimizer/sequential path).
- Code:
  ```python
  if step1 is not None and cal_gate.step_is_runnable(2):
      log()
      log("Running Step 2: Heave / Third Springs...")
      heave_solver = HeaveSolver(car, track)
      step2 = heave_solver.solve(...)
  elif step1 is None:
      _steps_blocked.add(2)
      log("\n[BLOCKED] Step 2: Heave / Third Springs — depends on Step 1")
  else:
      _steps_blocked.add(2)
      log("\n[BLOCKED] Step 2: Heave / Third Springs — uncalibrated inputs")
  ```
- What this expects: `cal_gate.step_is_runnable(2)` returns True for runnable Step 2 inputs. A Grep of `car_model/calibration_gate.py` for `suspension_arch` returns nothing — the gate is architecture-blind. For GT3, `step_is_runnable(2)` will return whatever the (uncalibrated) heave subsystem says; either way the path is wrong because Step 2 should be SKIPPED, not BLOCKED.
- Risk: BLOCKER
- Recommended GT3 handling: short-circuit on architecture before consulting the gate:
  ```python
  if step1 is not None and not car.suspension_arch.has_heave_third:
      log()
      log("Skipping Step 2: GT3 architecture has no heave/third springs.")
      step2 = HeaveSolution.null(
          front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
          rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
      )
  elif step1 is not None and cal_gate.step_is_runnable(2):
      heave_solver = HeaveSolver(car, track)
      step2 = heave_solver.solve(...)
  elif step1 is None:
      _steps_blocked.add(2)
      log("\n[BLOCKED] Step 2: Heave / Third Springs — depends on Step 1")
  else:
      _steps_blocked.add(2)
      log("\n[BLOCKED] Step 2: Heave / Third Springs — uncalibrated inputs")
  ```
  ALSO update `car_model/calibration_gate.py` so `step_is_runnable(2)` returns True (always-runnable, vacuously) for GT3 cars. Flag for the calibration_gate audit unit.
- Effort estimate: 1 hour (`solver/solve.py` change + `car_model/calibration_gate.py` change + test).
- Notes: This is the canonical GT3-aware skip pattern. Once it lands, GT3 reports won't show "[BLOCKED] Step 2" — they'll show "Step 2 skipped (GT3 has no heave/third springs)".

### Finding 16: `--legal-search` baseline_params unconditionally writes Step 2 fields

- File: `solver/solve.py:779-810`
- Code:
  ```python
  _all_steps_present = all(s is not None for s in [step1, step2, step3, step4, step5, step6])
  if _all_steps_present and should_run_legal_manifold_search(...):
      ...
      baseline_params = {
          ...
          "front_heave_spring_nmm": step2.front_heave_nmm,
          "rear_third_spring_nmm": step2.rear_third_nmm,
          ...
      }
  ```
- What this expects: `_all_steps_present` was checking `step2 is not None`. After Finding 1, GT3 will have `step2.present=False` and `front_heave_nmm=0.0`. The baseline_params will carry zeros into the legal-search axes, which is wrong (search shouldn't treat heave as a perturbable axis at all on GT3).
- Risk: DEGRADED
- Recommended GT3 handling: conditionally include Step 2 fields:
  ```python
  baseline_params = {
      "front_pushrod_offset_mm": step1.front_pushrod_offset_mm,
      ...
  }
  if step2.present:
      baseline_params["front_heave_spring_nmm"] = step2.front_heave_nmm
      baseline_params["rear_third_spring_nmm"] = step2.rear_third_nmm
  ```
  ALSO update `_all_steps_present` to use `step2.present` instead of `step2 is not None`. Coordinate with legal-search audit unit.
- Effort estimate: 30 min.
- Notes: The legal-search itself is in `solver/legal_search.py` and needs its own architecture branch — that's the bigger fix.

### Finding 17: Step 2 perch field naming — `perch_offset_front_mm` vs garage `front_heave_perch_mm`

- File: `solver/heave_solver.py:74-75` (HeaveSolution fields), `solver/solve_chain.py:1147-1158` (override remap), `solver/solve.py:91-94` (param map)
- Code:
  ```python
  perch_offset_front_mm: float
  perch_offset_rear_mm: float
  ```
- What this expects: HeaveSolution carries front/rear PERCH offsets (mm) for the heave/third springs. GT3 cars use corner-spring perch offsets instead — different setup-registry field names. Setting `perch_offset_front_mm=0.0` in `HeaveSolution.null()` and writing it through `direct_fields` would silently zero a corner-spring perch.
- Risk: COSMETIC (mitigated by Finding 7 — direct_fields is conditional on `has_heave_third`).
- Recommended GT3 handling: no change required if Finding 7 lands.
- Effort estimate: N/A.
- Notes: Cross-cutting with the setup-writer audit (Phase 2 field-naming).

### Finding 18: HeaveSolver.solve_candidates returns 0 candidates if `solve()` raises

- File: `solver/heave_solver.py:1402-1422`
- Code:
  ```python
  def solve_candidates(self, ..., n_candidates: int = 4) -> list[HeaveSolution]:
      base = self.solve(...)
      candidates = [base]
      ...
  ```
- What this expects: `self.solve()` returns a real solution. If GT3 is somehow reached, the base call will fail at line 984 (`hsm = self.car.heave_spring` — None).
- Risk: BLOCKER (subsumed by Finding 1 once HeaveSolver is never instantiated for GT3, but defense-in-depth via Finding 11 also covers it).
- Recommended GT3 handling: covered by Findings 1 and 11 (no separate fix needed in `solver/heave_solver.py`).
- Effort estimate: N/A.
- Notes: Listed for completeness — if the constructor guard from Finding 11 lands, this site never runs for GT3.

### Finding 19: Branching solver fallback heuristic reads `s2_copy.front_bottoming_margin_mm`

- File: `solver/solve_chain.py:725-735`
- Code:
  ```python
  else:
      front_margin = s2_copy.front_bottoming_margin_mm
      rear_margin = s2_copy.rear_bottoming_margin_mm
      if front_margin < 0 or rear_margin < 0:
          score = -1e6
      else:
          score = (
              math.log1p(max(front_margin, 0)) * 10
              + math.log1p(max(rear_margin, 0)) * 10
              - s4.lltd_error * 500
          )
  ```
- What this expects: `s2_copy.front_bottoming_margin_mm` is a real heave-bottoming margin. On `HeaveSolution.null()`, both margins are 0.0 → score collapses to `-s4.lltd_error * 500`. The path picks the lowest-LLTD-error candidate, which is incidentally fine for GT3.
- Risk: DEGRADED — the score is degenerate but not actively wrong; needs explicit GT3 path.
- Recommended GT3 handling: branch on `step2.present` and use Step 3 corner-spring bottoming margin instead:
  ```python
  if s2_copy.present:
      front_margin = s2_copy.front_bottoming_margin_mm
      rear_margin = s2_copy.rear_bottoming_margin_mm
  else:
      # GT3 — Step 3 corner spring carries the bottoming check
      front_margin = getattr(s3_copy, "front_bottoming_margin_mm", 0.0)
      rear_margin = getattr(s3_copy, "rear_bottoming_margin_mm", 0.0)
  ```
  Note: the corner spring solver may not currently expose bottoming margins — flag for the corner-spring audit unit.
- Effort estimate: 1 hour.
- Notes: This is a fallback path (no `_branching_obj`). The primary scoring path uses `evaluate_physics()` from `solver/objective.py`, which is a separate audit.

## Risk summary

The single highest-risk class is **HeaveSolver instantiation in code paths
that don't check `car.suspension_arch.has_heave_third`** (Findings 1, 4, 15).
GT3 cars enforce `heave_spring=None` at construction, so any path that
reaches `HeaveSolver.__init__` → `HeaveSolver.solve` → `hsm.sigma_target_mm`
crashes with AttributeError. There are five entry points (`_run_sequential_solver`,
`_run_branching_solver`, `materialize_overrides`, `solver/solve.py` main loop x2)
and one indirect access (`apply_learned_corrections` on `car.heave_spring`).
Until those branch on architecture and substitute `HeaveSolution.null()`,
no GT3 setup can be produced end-to-end.

The second-highest-risk class is **downstream consumers of step2 fields**
(Findings 5, 6, 14). Once Finding 1 lands, `step2 = HeaveSolution.null()`
has zero rates and `present=False`. Six analyzer call sites in `solver/solve.py`
(stint/sector/sensitivity/multi-speed/Bayesian/space) and three Step-3/6
plumbing sites in `solver/solve_chain.py` read `step2.front_heave_nmm` directly —
they need `if step2.present:` guards or the analyzers themselves need
GT3 awareness. The fix is mechanical but spans many files.

The third risk class is **the calibration_gate is architecture-blind**
(Finding 15). `cal_gate.step_is_runnable(2)` will say "BLOCKED — uncalibrated"
for GT3 cars (because heave subsystems will be unset). The correct semantic
is "SKIPPED — not applicable". This is a UI/UX issue, not a runtime crash,
but it would produce confusing reports.

`solver/heave_calibration.py` is **GT3-safe by accident** because no
sequential or branching solver path calls it; it's only called from
`pipeline/produce.py` and `learner/`. Flag those audit units. The
`_physics_fallback` constants are BMW-tuned and would be wrong if a future
caller invoked `HeaveCalibration.predict_sigma` for a GT3 car-track pair.

## Effort estimate

| Cluster | Hours | Files |
|---|---|---|
| Architecture-aware HeaveSolver gating (Findings 1, 4, 11, 15) | 8h | `heave_solver.py`, `solve_chain.py`, `solve.py`, `calibration_gate.py` |
| `step2.present` guards on downstream consumers (Findings 2, 5, 6, 14, 16, 19) | 6h | `heave_solver.py`, `solve_chain.py`, `solve.py` |
| Param-map & legal-search axis exclusion (Findings 7, 16) | 1.5h | `solve.py` |
| Defensive learner guard (Finding 8) | 0.25h | `solve.py` |
| Tests (regression fixtures + 3 GT3 baselines) | 4h | `tests/test_setup_regression.py`, new GT3 fixtures |

**Total for this unit's recommended fixes: ~20 hours.**

## Dependencies

- **This audit's recommended fixes depend on:**
  - Phase 0 PR #102 (`SuspensionArchitecture.GT3_COIL_4WHEEL`, `HeaveSolution.null()`, `step2.present` flag, GT3 CarModel stubs) — DONE.
  - `car_model/calibration_gate.py` audit unit — needs `step_is_runnable(2)` to return True (vacuously) for GT3, OR an explicit GT3 short-circuit upstream of the gate.

- **Other audit units depend on this unit's findings:**
  - **Corner spring solver audit** — needs to handle `front_heave_nmm=None` / `front_heave_nmm=0.0` cleanly and provide front-axle stiffness from coil springs alone for GT3 (Finding 5). Also may need to expose `front_bottoming_margin_mm` for the branching solver fallback (Finding 19).
  - **Damper solver audit** — needs `front_heave_nmm=None` legal in the signature and a coil-only ζ-cal path for GT3 (Finding 6).
  - **Objective function audit** — `solver_steps_to_params` and `evaluate_physics` need `step2.present` guards (Finding 3).
  - **Legal-search audit** — needs to drop `front_heave_spring_nmm` / `rear_third_spring_nmm` from the search axes for GT3 (Findings 7, 16).
  - **Pipeline / produce.py audit** — `HeaveCalibration.load(...)` callers need GT3 short-circuits (Finding 13).
  - **Learner audit** — `apply_learned_corrections` writers for `heave_m_eff_*_kg` need GT3 short-circuits at the source (upstream of Finding 8).
  - **Setup writer audit** — should not emit `CarSetup_HeaveSpring_*` / `CarSetup_ThirdSpring_*` XML IDs for GT3 cars (out of scope here, but the symmetric problem to per-axle roll dampers).
  - **Sensitivity / stint / sector / Bayesian / multi-speed analyzer audits** — each needs a `step2.present` guard or per-architecture handling (Finding 14).
