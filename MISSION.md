# MISSION — IOptimal Inviolable Principles

> "Use mass amounts of data from every lap in every IBT file to learn and build a physics
> model that can create an optimal setup for any track. Use physics. Calibrate each car.
> Build and learn from each lap using every data point. Break down corner by corner,
> phase by phase. Think through pros and cons and total net value. Know that changing
> one parameter changes others." — Project Owner

This document defines the six inviolable principles that govern every change in this
codebase. The codebase has previously drifted from this mission via hardcoded BMW
fallbacks, preserve-driver shortcuts, and per-IBT summarizing. These rules exist to
prevent re-drift. They are precise and testable, not aspirational.

Every PR, every refactor, every "small fix" must comply. When in doubt, re-read this
file. The rules below are listed in declarative form with rationale, FORBIDDEN
PATTERNS, and REQUIRED PATTERNS. The forbidden lists are not exhaustive; the spirit of
each principle binds even when a specific pattern is not enumerated.

---

## Principle 1 — Every lap is data

**Rule:** No best-lap-only collapsing. Per-lap variance is signal, not noise. The
pipeline must process every valid lap from every IBT, not summarize to per-IBT means.

**Rationale:** A driver running 30 laps on the same setup produces 30 independent
observations of how that setup responds to fuel burn, tyre wear, traffic, line
variation, and surface temperature. Collapsing to a single "best lap" or per-IBT mean
discards 96.7% of the available learning signal and biases the model toward
single-corner heroics rather than steady-state physics. Per-lap variance is itself a
direct input to damper, σ, and confidence calculations — the spread of front_rh_std
across laps tells us how robust a setup is, which a per-IBT mean erases.

### FORBIDDEN PATTERNS

- `best_lap = min(laps, key=lambda l: l.lap_time); return best_lap.metrics`
- `summary = ibt.aggregate(method="best")` when feeding the learner / regression fitter
- Any analyzer or learner code path that emits exactly one Observation per IBT when the
  IBT contains multiple valid laps
- Per-IBT means used as the unit of regression input
  (`fit(X=ibt.mean_features, y=ibt.mean_target)`)
- `lap_filter = lambda laps: [laps[lap_time.idxmin()]]` or any "fastest-lap-wins" filter
  in front of the learner
- `--all-laps` being optional or off-by-default in any ingest command

### REQUIRED PATTERNS

- `learner.ingest --all-laps` is the default mode; one Observation per VALID lap
- Per-lap dispersion (std, p95, p99 over the lap's samples) is preserved as a feature,
  not collapsed to a mean
- Lap validity filtering is explicit (off-track, in-pit, yellow-flag) and documented;
  filtering is never lap-time-based
- Aggregations across laps (when needed for plotting / reporting) are computed
  downstream of the per-lap Observation set, never as a substitute for it
- Multi-lap variance enters the calibration confidence calculation directly
  (more laps + lower variance = higher tier)

---

## Principle 2 — Physics-first

**Rule:** Every parameter recommendation comes from a fitted regression OR a physics
formula labeled with confidence. NEVER preserve-driver as a default. Preserve-driver
is a last-resort with explicit `[FALLBACK]` warning.

**Rationale:** The product promise is "explain why a setup should work" — that demands
a physics or data justification at every output. Preserve-driver as a default makes the
solver a conservative editor of the driver's intuition rather than an independent
physics-first recommender, which violates the entire reason this tool exists. When a
driver loads a known-bad setup, a preserve-driver default will recommend a known-bad
setup. When the user wants to know why a value is recommended, the answer must be a
physics statement (formula + inputs + confidence) or an empirical statement (regression
+ R² + sample count), never "the driver had it loaded."

### FORBIDDEN PATTERNS

- `if confidence < threshold: return current_setup.value` (silent preserve-driver
  fallback without `[FALLBACK]` provenance)
- `# preserve driver value as fallback` paired with no warning emission
- `recommendation = current_setup.foo` as the default branch of an `if model.is_calibrated` test
- Solver methods that read `self.current_setup` and short-circuit before any physics
  computation, with no provenance label
- Returning a driver-loaded value as `recommended_value` without `provenance="driver_anchor"`
  AND a `[FALLBACK]` warning in the report
- Anchors that fire on `if lap_time < X` or any lap-time-derived predicate (driver-anchor
  must trigger on σ-measurement, model self-test, or close-tolerance only — see
  Principle 11 in CLAUDE.md)

### REQUIRED PATTERNS

- Every `recommended_value` carries an explicit `provenance` field with one of:
  `"regression_fit"`, `"physics_formula"`, `"empirical_correction"`,
  `"driver_anchor_with_fallback"`, `"calibration_blocked"`
- When `provenance="driver_anchor_with_fallback"`, the report MUST emit a
  `[FALLBACK] reason=<why model could not produce a value>` line
- The solver's default code path computes a value from physics or regression; the
  driver-anchor path is reachable ONLY when an explicit gate predicate triggers
  (σ-measurement match, self-test failure, close-tolerance agreement)
- Recommendations include the formula or fitted-model identifier and its confidence
  tier so the reader can audit it

---

## Principle 3 — No hardcoded fallbacks

**Rule:** No `getattr(car, "field", BMW_DEFAULT)`. Per-car fields are required, not
optional. Missing per-car data must raise, not substitute.

**Rationale:** The codebase originated as a BMW M Hybrid V8 tool and silently grew
BMW-default fallbacks across `solver/`, `pipeline/`, and `analyzer/`. Every such
fallback is a silent multi-car bug: a Porsche, Ferrari, or Cadillac call site that
hits the fallback gets a recommendation derived from BMW's mass, geometry, motion
ratio, or master-cylinder bore — and the user has no way to know. Real examples:
`solver/supporting_solver.py:196` defaulted master cylinder bore to 19.1mm (BMW value)
for any car missing it; `solver/bmw_coverage.py:_car_name()` defaulted to `"bmw"`
when the car was unknown. This rule eliminates that entire class of silent
cross-contamination.

### FORBIDDEN PATTERNS

- `getattr(car, "front_master_cyl_mm", 19.1)` — BMW default substituted for missing field
- `getattr(car, "field", numeric_default)` for ANY physics/parameter field (the only
  legitimate getattr defaults are for optional sub-models and feature flags, e.g.
  `getattr(car, "has_roll_dampers", False)`)
- `default_car = "bmw"` or `default_car = bmw_model` anywhere in solver/pipeline code
- `_car_name(car) or "bmw"` patterns
- Hardcoded constants borrowed from BMW measurements as fallback when a car-specific
  value is absent (e.g. `mass_kg = car.mass_kg or 1030.0`)
- Try/except that swallows a missing-attribute error and substitutes a numeric default
  (`try: x = car.foo; except AttributeError: x = 1030.0`)

### REQUIRED PATTERNS

- Direct attribute access: `car.front_master_cyl_mm` — raises `AttributeError` if
  missing, which is the desired behavior (the calibration gate or car definition is
  incomplete and must be fixed)
- Per-car fields are declared on the `CarModel` dataclass without defaults; missing
  fields fail at object construction, not at runtime in the solver
- When a feature is genuinely optional across cars, use an explicit
  `Optional[Subsystem]` field and gate consumers on `if car.subsystem is not None:` —
  not on `getattr(...)` with a numeric fallback
- Unknown-car branches in dispatchers raise `NotImplementedError` with a clear message
  ("car X is not yet calibrated; see docs/calibration_guide.md") rather than
  silently routing to BMW

---

## Principle 4 — Continuous learning

**Rule:** Calibration is tiered (high/medium/low/insufficient), not binary
calibrated/uncalibrated. Solver uses ALL non-insufficient tiers.

**Rationale:** Binary calibrated/uncalibrated wastes data. A car with R²=0.72 on a
front-RH model is not as good as R²=0.95, but it is far better than no model at all,
and gating it out forces the user into preserve-driver territory (Principle 2
violation). Tiered confidence lets the solver use weaker models with appropriately
widened uncertainty bands rather than throwing them away. The user sees the tier on
every output and can audit which subsystems are pulling the most weight.

### FORBIDDEN PATTERNS

- `if not model.is_calibrated: return None` as the only gate (binary cliff)
- Boolean fields like `calibrated: bool` on calibration reports without a tier label
- Hardcoded R² thresholds with only two outcomes (≥X passes, <X fails) — must be at
  least three tiers
- Code paths that drop a regression fit entirely just because R² < 0.85 (must instead
  demote it to a lower tier and continue using it with widened uncertainty)
- Reports that print "uncalibrated" for any non-insufficient tier

### REQUIRED PATTERNS

- Calibration report carries a `tier: Literal["high", "medium", "low", "insufficient"]`
  field for every subsystem
- Tier mapping is explicit and documented (e.g. R² ≥ 0.95 → high, 0.85–0.95 → medium,
  0.50–0.85 → low, <0.50 or insufficient samples → insufficient)
- Solver consumes all tiers ≠ "insufficient"; uncertainty bands widen as tier
  decreases (low-tier predictions carry larger ± in the report)
- Provenance output (Principle 9 in CLAUDE.md) lists tier per subsystem so the user
  can audit confidence at a glance
- "Insufficient" is the ONLY tier that blocks the step; all others run with widened
  bands

---

## Principle 5 — Coupled evaluation

**Rule:** Every parameter change re-evaluates dependent parameters.
Heave → m_eff → ω_n → damper_zeta. ARB → LLTD → damper rebound.
No independent per-step optimization.

**Rationale:** Setup parameters are not independent. Stiffening the heave spring
changes the effective sprung mass that the corner spring sees, which changes the
natural frequency, which changes the damper coefficient that achieves a target ζ.
Step-independent optimization (pin Step 1, freeze, optimize Step 2 in isolation,
freeze, etc.) is mathematically wrong: the optimum of the joint problem is not the
sequence of per-step optima. The 6-step workflow exists for tractability and
explainability, but every step must propagate its outputs through the dependency graph
and re-evaluate downstream steps when an upstream value changes.

### FORBIDDEN PATTERNS

- `step6_dampers = solve_dampers(step3_springs)` without recomputing ω_n from the
  combined heave+spring system after step 2's effect on m_eff
- `step4_arb = solve_arb(target_lltd)` then writing the result without re-checking
  damper rebound balance against the new LLTD
- `final_setup = step1 | step2 | step3 | step4 | step5 | step6` (dict union with no
  recomputation passes)
- Locking step N's output before step N+1 has consumed it
- Any optimizer that searches over a single step's axes with all other steps frozen
  at their initial values (this is the non-coupled mode that this rule outlaws)
- Per-step "objective_value" reported as the standalone metric for that step's
  recommendation

### REQUIRED PATTERNS

- Solver chain runs at least one full back-pass after sequential step solving
  (`solver/solve_chain.py:reconcile_*` functions are the canonical example)
- Damper ζ is computed from `m_eff(step1, step2, step3)` AND `k_total(step3, step4)` —
  not from corner spring alone
- LLTD computation reads `k_front` and `k_rear` from step 3 wheel rates AND step 4 ARB
  contributions, never just one
- The objective scoring function evaluates the FULL joint state, not the sum of
  per-step costs
- When a candidate search varies any parameter, the candidate's downstream parameters
  are recomputed before scoring (`materialize_overrides` in `solver/solve_chain.py`
  is the reference implementation)

---

## Principle 6 — Corner-by-corner causal

**Rule:** Parameter recommendations include per-corner-phase predicted impact, not
just aggregate metrics. Tradeoffs surfaced explicitly: "stiffens turn-1 entry by X
but hurts turn-7 mid-phase by Y."

**Rationale:** Aggregate metrics (lap-wide front_rh_std, full-lap balance %) hide
tradeoffs that are obvious corner-by-corner. A heave spring change that helps high-
speed sweepers can hurt low-speed hairpins; a damper change that improves entry
stability can degrade mid-corner rotation. Reporting only the lap-aggregate metric
means the user can't see WHICH corners benefit and which corners pay the cost. The
analyzer already does corner-by-corner segmentation (`analyzer/segment.py`); the
recommendation layer must consume that segmentation and surface the per-corner-phase
delta in every recommendation.

### FORBIDDEN PATTERNS

- Recommendations reported as `Δfront_rh_std = -0.4 mm` with no corner-by-corner
  breakdown
- Solver objective scoring that uses only lap-aggregate metrics (mean, p95, p99 over
  the entire lap) and never reads `analyzer.segment.corners`
- Reports that say "improves balance" without listing which corners and which phase
  (entry / apex / exit)
- Recommendations that show only NET impact and hide compensating positives/negatives
  across corners
- "Trade-off" mentioned only in prose with no quantified per-corner delta

### REQUIRED PATTERNS

- Every parameter recommendation includes a `per_corner_impact` block listing
  `{corner_id, phase, predicted_delta, sign}` for at least the 3 most-affected corners
- Reports surface tradeoffs explicitly: "T1 entry +0.06s, T7 mid -0.04s, net +0.02s"
- The objective function ingests the corner-segmented telemetry and weights each
  corner's contribution; the per-corner contributions are persisted in the JSON output
  for auditing
- Aggregate metrics are reported alongside (not instead of) per-corner deltas
- The decision trace (`car_model/decision_trace.py`) records which corners drove each
  parameter recommendation

---

## ENFORCEMENT

These rules are tested by `tests/test_mission_compliance.py` (Unit V2) and any future
code that reintroduces forbidden patterns will fail the build. The test suite scans
the codebase for the FORBIDDEN PATTERNS listed above (string matches, AST patterns,
and runtime-output assertions) and fails CI when they reappear. New principles are
added by amending this document AND extending the test suite — never one without the
other.

If a real-world need pushes against one of these rules, the answer is to update this
document with the rationale, the test suite to encode the new boundary, and the
codebase in the same PR. Drift-by-exception is how this codebase ended up needing
this document in the first place.
