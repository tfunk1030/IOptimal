# GT3 Phase 2 Audit — `car_model/calibration_gate.py`

## Scope

Single-file audit of `car_model/calibration_gate.py` (940 lines) — the central
source of truth for which solver steps may run. Phase 0 PR #102 added the
4th calibration state `not_applicable` and the `SuspensionArchitecture` enum
(`GTP_HEAVE_THIRD_TORSION_FRONT`, `GTP_HEAVE_THIRD_ROLL_FRONT`,
`GT3_COIL_4WHEEL`), plus three GT3 stub cars (BMW M4 GT3, Aston Vantage GT3,
Porsche 992 GT3 R) that all set `heave_spring=None`. This audit verifies
whether the gate itself dispatches on `car.suspension_arch.has_heave_third`
to emit `not_applicable` for Step 2 — and, by extension, for the heave/third
sub-fields of the deflection model.

Cross-referenced files:
- `car_model/cars.py` (SuspensionArchitecture enum L24-L53; `__post_init__`
  invariants L1751-L1771; GT3 car defs L3192+)
- `tests/test_suspension_architecture.py` (`TestCalibrationGateNotApplicable`
  L285-L301 — only verifies dataclass field plumbing, not gate emission)
- `docs/gt3_session_info_schema.md`, `docs/gt3_per_car_spec.md` (Step 2 N/A
  for all 11 GT3 cars; coil-only suspension)

## Summary table

| ID | Severity | Location | Issue |
|----|---|---|---|
| F1 | **BLOCKER** | `car_model/calibration_gate.py:770-777` (`STEP_REQUIREMENTS`) and `:864-909` (`check_step`) | Gate has no `suspension_arch.has_heave_third` dispatch. For GT3 cars Step 2 falls into the standard path: `spring_rates` is `calibrated` (default) so `check_step(2)` returns `blocked=False, not_applicable=False`. The 4-state contract is **never emitted by the gate** — `not_applicable=True` is only constructed by hand in tests. |
| F2 | **BLOCKER** | `car_model/calibration_gate.py:851` (`_DATA_PRIOR_STEP`) | Cascade `{2:1, 3:2, 4:3, 5:4, 6:3}` is GTP-only. For GT3 the chain must collapse: target `{3:1, 4:3, 5:4, 6:3}` (Step 2 dropped). Currently Step 3 cascades from Step 2 unconditionally — a GT3 car with Step 2 emitted as `not_applicable` (post-F1 fix) would propagate `weak_upstream` from a non-applicable step, polluting Step 3's confidence weight. |
| F3 | **BLOCKER** | `car_model/calibration_gate.py:566-620` (deflection_model subsystem) | Iterates `heave_spring_defl_static`, `heave_spring_defl_max`, `third_spring_defl_static` unconditionally. GT3 cars have `car.deflection` populated by factory default but the heave/third sub-models are physically nonexistent. The deflection subsystem should classify those three sub-models as `not_applicable` and base its overall status only on the corner-spring sub-models that exist for GT3. |
| F4 | DEGRADED | `car_model/calibration_gate.py:178-219` (`format_header`) | "WEAK STEPS / UNCALIBRATED STEPS / CALIBRATED STEPS" branches do not include a "NOT APPLICABLE STEPS" section. Steps with `not_applicable=True` slip through `solved_steps` (line 158: `not r.blocked`) and would render as `[OK]` or `[~~]` with no signal that the step was skipped for architectural reasons. |
| F5 | DEGRADED | `car_model/calibration_gate.py:147-176` (`CalibrationReport` properties) | Missing `not_applicable_steps` property. `solved_steps` (L158) treats `not_applicable` steps as solved (returns them in the list). `step_confidence` (L174-176) returns 0.0 for both `blocked` and `not_applicable` — same numeric weight, opposite semantics, ambiguous to consumers. |
| F6 | DEGRADED | `car_model/calibration_gate.py:925-927` (`all_calibrated`) | `step_is_runnable(s)` returns `not blocked`, so `all_calibrated()` returns True for a GT3 car where Step 2 is `not_applicable`. This is arguably correct (architectural skip ≠ uncalibrated) but the docstring says "all 6 steps calibrated", which is the wrong English for "5/6 + 1 N/A". |
| F7 | DEGRADED | `car_model/calibration_gate.py:929-939` (`summary_line`) | "all 6 steps calibrated" message hides architecture skips. For GT3 it should print "5/6 steps calibrated, Step 2 not applicable (GT3_COIL_4WHEEL)". |
| F8 | DEGRADED | `car_model/calibration_gate.py:116-135` (`instructions_text`) | If a GT3 step is incorrectly marked `blocked` (pre-F1 fix), `instructions_text` would emit calibration instructions for `spring_rates` (the Step 2 requirement) — "TO CALIBRATE SPRING RATES" — which is meaningless for a car that has no heave/third. Belt-and-suspenders: when `not_applicable=True`, return a one-liner "Step N — N/A for {arch}". |
| F9 | COSMETIC | `car_model/calibration_gate.py:240-251` (`format_confidence_report`) | Only shows the listed `order` subsystems. New GT3-flavored not-applicable subsystems (`heave_spring_defl_*`, `third_spring_defl_static`) wouldn't appear if added in F3. The `--` icon (L249) is correctly mapped, but the order list is closed-set — any GT3-specific subsystem name needs to be added here. |
| F10 | COSMETIC | `car_model/calibration_gate.py:457-482` (`_build_subsystem_status` track_support) | First subsystem built is `track_support` keyed off `car.supports_track`. For new GT3 cars no track is `supported_tracks` yet, so every GT3 run starts with `track_support=uncalibrated`. Not strictly a Phase 2 calibration-gate bug but it cascades into every step's display via the existing weak/uncalibrated path; worth a follow-up to either default GT3 to "no track yet" or add a separate `not_applicable` branch for "track support not yet onboarded". |
| F11 | COSMETIC | `car_model/calibration_gate.py:264-326` (`INSTRUCTIONS`) | No `heave_third_not_applicable` template. After F1 fix, an explanatory string ("This subsystem does not exist on cars with `SuspensionArchitecture.GT3_COIL_4WHEEL`. Skip Step 2.") would help operators reading the JSON provenance. |

## Findings

### F1 — BLOCKER: `check_step` does not dispatch on `suspension_arch`

**Location:** `car_model/calibration_gate.py:864-909`

`STEP_REQUIREMENTS[2] = ("Heave / Third Springs", ["spring_rates"])`. For a
GT3 car, `spring_rates` is built from `car.corner_spring.rear_torsion_unvalidated`
(line 646, defaults False) → status `calibrated`. So `check_step(2)` walks
the standard loop and returns `StepCalibrationReport(step_number=2, blocked=False,
not_applicable=False, ...)`. The gate never sets `not_applicable=True`.

The Phase 0 contract documented at `tests/test_suspension_architecture.py:285-301`
(`TestCalibrationGateNotApplicable`) only verifies that the **dataclass field
exists and has the right confidence weight** — there is no test asserting
`gate.check_step(2).not_applicable is True` for `BMW_M4_GT3`.

**Required fix shape:**

```python
# In check_step(), before the cascade lookup:
if step_number == 2 and not self.car.suspension_arch.has_heave_third:
    return StepCalibrationReport(
        step_number=2,
        step_name="Heave / Third Springs",
        not_applicable=True,
    )
```

This single early-return makes the dispatch explicit, keeps the GTP path
untouched, and is a clean extension point if more `not_applicable` cases
arrive later (e.g. front-roll-damper geometry on cars without one).

**Cascade rule reference (verbatim from L851):**
`_DATA_PRIOR_STEP: dict[int, int] = {2: 1, 3: 2, 4: 3, 5: 4, 6: 3}` — must
become `{3: 1, 4: 3, 5: 4, 6: 3}` for GT3, see F2.

### F2 — BLOCKER: cascade rules `_DATA_PRIOR_STEP` are GTP-specific

**Location:** `car_model/calibration_gate.py:842-851, 873-889`

The cascade `{2: 1, 3: 2, 4: 3, 5: 4, 6: 3}` encodes the GTP solver chain
where Step 3 (corner springs) needs Step 2's spring outputs. For GT3
(`SuspensionArchitecture.GT3_COIL_4WHEEL`) Step 2 is N/A, so the chain must be
`{3: 1, 4: 3, 5: 4, 6: 3}` — Step 3 cascades directly from Step 1.

**Current bug:** with F1 partially applied (Step 2 returns `not_applicable=True`,
`blocked=False`), the cascade check at L878 (`prior_hard_blocked = prior.blocked
and not prior.weak_block`) correctly skips the hard cascade. **But** L887-889
sets `weak_upstream` if the prior step has `weak_block` or `weak_upstream`. A
`not_applicable` step doesn't trip either flag, so practically Step 3 will run
clean. That's accidentally correct, but the cascade dict is still wrong on its
face — it implies a data dependency that does not exist for GT3.

**Required fix shape:**

```python
@property
def _data_prior_step(self) -> dict[int, int]:
    if self.car.suspension_arch.has_heave_third:
        return {2: 1, 3: 2, 4: 3, 5: 4, 6: 3}
    return {3: 1, 4: 3, 5: 4, 6: 3}
```

Replace the class-level `_DATA_PRIOR_STEP` constant lookup at L873 with
`self._data_prior_step.get(step_number)`.

Also: explicitly assert in the cascade branch at L884-889 that prior is not
`not_applicable` before propagating `weak_upstream`. The docstring at L94-98
states "Not applicable is HONEST absence … and does NOT cascade" — codify it.

### F3 — BLOCKER: deflection_model subsystem iterates GT3-nonexistent sub-models

**Location:** `car_model/calibration_gate.py:566-620`

```python
defl_cal = car.deflection.is_calibrated  # L566
for key in ("heave_spring_defl_static", "heave_spring_defl_max",
            "rear_spring_defl_static", "third_spring_defl_static",
            "rear_shock_defl_static"):
    model = raw_models.get(key)
    ...
```

Three of those five keys are physically nonexistent on a GT3 car. The current
code reads from `raw_models` (the JSON file) so it will silently get None and
not contribute to `defl_r2s`, but the subsystem is built by combining only the
corner-spring sub-models. That is `defl_cal` for an architecturally distinct
reason than for GTP, and the subsystem has no way to report "two of my
sub-models are N/A by architecture."

**Required fix shape:** filter the iteration list by `car.suspension_arch.has_heave_third`,
and emit a separate `heave_third_deflection` subsystem with status `not_applicable`
for GT3 cars (so the provenance dict at L809-840 exposes it honestly).

```python
defl_keys = ["rear_spring_defl_static", "rear_shock_defl_static"]
if car.suspension_arch.has_heave_third:
    defl_keys = ["heave_spring_defl_static", "heave_spring_defl_max",
                 "third_spring_defl_static"] + defl_keys
```

### F4 — DEGRADED: `format_header` ignores `not_applicable`

**Location:** `car_model/calibration_gate.py:178-219`

Lines 184-190 build "CALIBRATED STEPS" from `solved_steps`, which currently
includes `not_applicable` steps (see F5). For GT3 the report would print:

```
CALIBRATED STEPS:
  [OK] Step 1: Rake / Ride Heights
  [OK] Step 2: Heave / Third Springs        ← LIE: this step did not run
  [OK] Step 3: Corner Springs
  ...
```

**Required fix shape:** add a new section before/after WEAK STEPS:

```python
not_applicable = [r.step_number for r in self.step_reports if r.not_applicable]
if not_applicable:
    lines.append("")
    lines.append("NOT APPLICABLE STEPS (architecture skip — not a calibration gap):")
    for s in not_applicable:
        r = self.step_reports[s - 1]
        lines.append(f"  [--] Step {s}: {r.step_name}")
```

And exclude `not_applicable` from `solved_steps` (see F5).

### F5 — DEGRADED: `CalibrationReport` lacks `not_applicable_steps` and `solved_steps` over-reports

**Location:** `car_model/calibration_gate.py:147-176`

```python
@property
def solved_steps(self) -> list[int]:
    return [r.step_number for r in self.step_reports if not r.blocked]   # L158
```

A `not_applicable` step has `blocked=False`, so it gets returned as "solved".
That is conceptually wrong: it didn't solve anything; it was skipped.

```python
@property
def step_confidence(self) -> dict[int, float]:
    return {r.step_number: r.confidence_weight for r in self.step_reports}  # L174-176
```

Both `blocked` and `not_applicable` resolve to 0.0 via `confidence_weight`
(L100-114). Numerically identical, semantically different — consumers
downstream cannot distinguish "step refused to run" from "step doesn't apply".

**Required fix shape:**

```python
@property
def solved_steps(self) -> list[int]:
    return [r.step_number for r in self.step_reports
            if not r.blocked and not r.not_applicable]

@property
def not_applicable_steps(self) -> list[int]:
    return [r.step_number for r in self.step_reports if r.not_applicable]
```

Optionally extend `step_confidence` to return a structured value
(e.g. `{step: (weight, status_label)}`) so consumers can filter.

### F6 — DEGRADED: `all_calibrated` semantically misleading for GT3

**Location:** `car_model/calibration_gate.py:925-927`

```python
def all_calibrated(self) -> bool:
    return all(self.step_is_runnable(s) for s in range(1, 7))
```

`step_is_runnable` is `not check_step(s).blocked` (L921-923). For a fully
onboarded GT3 car, Step 2 is `not_applicable=True, blocked=False`, so
`all_calibrated()` returns True. The docstring says "all 6 steps calibrated"
— wrong language. Suggest: rename docstring to "all applicable steps
calibrated" or split into `all_applicable_calibrated()` and `is_runnable_e2e()`.

### F7 — DEGRADED: `summary_line` doesn't surface architectural skip

**Location:** `car_model/calibration_gate.py:929-939`

```python
if blocked == 0:
    return f"{self.car.name}: all 6 steps calibrated"
```

For a GT3 car this prints "all 6 steps calibrated" when Step 2 didn't run.
Should be:

```python
na = len(report.not_applicable_steps)  # after F5
if blocked == 0 and na == 0:
    return f"{self.car.name}: all 6 steps calibrated"
if blocked == 0:
    return (f"{self.car.name}: {solved}/{6 - na} applicable steps calibrated, "
            f"{na} not applicable (steps {report.not_applicable_steps})")
```

### F8 — DEGRADED: `instructions_text` emits misleading instructions for N/A steps

**Location:** `car_model/calibration_gate.py:116-135`

If F1 is **not** applied and `check_step(2)` for GT3 ever returns
`blocked=True` (e.g. via `spring_rates=uncalibrated` on a freshly-onboarded
GT3 car), `instructions_text` would emit "TO CALIBRATE SPRING RATES" — wrong
remediation, since GT3 has no heave/third to calibrate. Defensive fix even
after F1: when `not_applicable=True`, return a clean one-liner:

```python
if self.not_applicable:
    return f"  STEP {self.step_number}: {self.step_name} — N/A (architecture skip)\n"
```

### F9 — COSMETIC: `format_confidence_report` order list is closed-set

**Location:** `car_model/calibration_gate.py:233-237`

```python
order = [
    "aero_compression", "ride_height_model", "deflection_model",
    "spring_rates", "pushrod_geometry", "damper_zeta",
    "arb_stiffness", "lltd_target", "roll_gains",
]
```

If F3 introduces a new `heave_third_deflection` subsystem (or any GT3-specific
provenance entry), it won't render until the order list is updated. The `--`
status icon at L248-249 is correctly wired; the issue is purely the closed
list. Suggest extending or building dynamically from `subsystems.keys()`
with a partial ordering hint.

### F10 — COSMETIC: `track_support` is uncalibrated for every GT3 first-run

**Location:** `car_model/calibration_gate.py:462-482`

`car.supports_track(track_name)` is False for any GT3 car at any track until
`supported_tracks` is populated. The subsystem then has `status=uncalibrated`,
which is technically correct but cosmetically noisy on Phase 2 ramp-up runs
where no GT3 telemetry exists yet. Out of scope for this audit but worth
flagging since it interacts with the gate's display.

### F11 — COSMETIC: no `heave_third_not_applicable` instruction template

**Location:** `car_model/calibration_gate.py:264-327` (`INSTRUCTIONS`)

After F1+F3, GT3 step 2 reports will include a SubsystemCalibration with
status `not_applicable`. Adding a template like

```python
"heave_third_not_applicable": (
    "This subsystem does not exist on cars with "
    "SuspensionArchitecture.GT3_COIL_4WHEEL. Step 2 is skipped "
    "by architecture, not by calibration gap."
),
```

helps operators reading JSON provenance understand why the field is empty
without diving into the gate's source code.

## Risk summary

| Severity | Count | Risk if unfixed |
|---|---|---|
| BLOCKER | 3 | Phase 2 GT3 ships with Step 2 silently "running" against a meaningless `spring_rates` requirement; deflection model R² thresholds get computed against three nonexistent sub-models; cascade rules carry GTP-shape into a class where Step 2 doesn't exist. The 4-state contract (`not_applicable`) is plumbed but **never emitted by the gate itself**. |
| DEGRADED | 5 | Reports lie ("all 6 steps calibrated" when 5+1 N/A); consumers downstream of `step_confidence` cannot distinguish architectural skip from hard block; `solved_steps` over-counts; instructions could emit wrong remediation. |
| COSMETIC | 3 | Provenance display gaps and ramp-up noise. None block correctness. |

The top-of-the-pile fix is a single dispatch in `check_step` (F1) plus the
`_DATA_PRIOR_STEP` GT3 variant (F2) plus the deflection sub-model filter
(F3). Together they make `gate.check_step(2)` for `BMW_M4_GT3` return
`not_applicable=True` end-to-end and stop the cascade rules from claiming a
data dependency that doesn't exist.

## Effort estimate

| Item | Effort |
|---|---|
| F1 (early-return in `check_step`) | 15 min code + 30 min test (assert `gate.check_step(2).not_applicable` for BMW_M4_GT3, Aston, Porsche 992 GT3 R) |
| F2 (`_data_prior_step` property + cascade-block on N/A) | 30 min code + 30 min test |
| F3 (filter deflection sub-models, add `heave_third_deflection` subsystem) | 1 hr code + 30 min test |
| F4-F8 (display/property fixes) | 1.5 hr code + 1 hr test |
| F9-F11 (cosmetic) | 30 min |
| **Total** | **~5-6 hr** for a Phase 2 PR with full test coverage |

A first-cut PR could ship F1+F2+F3 only (~3 hr) and defer the cosmetic
report-formatting work to a follow-up.

## Dependencies

- Depends on Phase 0 PR #102 contract (`SuspensionArchitecture` enum and
  `has_heave_third` property) — already merged.
- Depends on existing tests in `tests/test_suspension_architecture.py`
  (`TestCalibrationGateNotApplicable` class) — these need to be **extended**
  to assert that the `gate.check_step(2)` path emits `not_applicable=True`
  for the three GT3 stub cars (currently they only test the dataclass).
- Blocks Phase 2 deflection-model and corner-spring solver dispatch for GT3
  cars: those solvers will need to consult `gate.check_step(2).not_applicable`
  to skip cleanly.
- No new file dependencies. All fixes are localized to
  `car_model/calibration_gate.py` and the existing test file.
