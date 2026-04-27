# GT3 Phase 2 Audit — Pipeline orchestration, reasoning, reports

## Scope

Audited files (pipeline orchestration unit):

- `pipeline/produce.py` — top-level CLI orchestrator (2049 LoC)
- `pipeline/reason.py` — multi-IBT reasoning engine + heave floor handling (4083 LoC)
- `pipeline/report.py` — engineering report formatter (721 LoC)
- `pipeline/scan.py` — IBT-directory scanner (504 LoC)
- `pipeline/__main__.py` — deprecated CLI shim (19 LoC)

Lens: GT3 (`SuspensionArchitecture.GT3_COIL_4WHEEL`) cars have **no heave/third springs** and Step 2 must yield `HeaveSolution.null()` with `present=False`. Every read of `step2.front_heave_nmm` / `step2.rear_third_nmm`, every alias dict that pretends `front_heave_nmm` is meaningful, every garage-output read keyed off heave-spring deflection, every modifier that derives a "heave floor" from observed front_heave rates — all need conditional logic that branches on either `step2.present` or `car.suspension_arch.has_heave_third` (the helper exists at `car_model/cars.py:46-49`).

The pipeline currently treats Step 2 as **structurally always present** (an instance always exists, the calibration gate either nulls it to `None` or hands a populated dataclass). The "GT3 returns a null-but-typed `HeaveSolution`" case (`present=False`, all numerics zero) is **not handled anywhere in the pipeline**. Every consumer reads numeric fields directly with no `present` guard.

## Summary table

| Severity | Count |
|---|---|
| BLOCKER | 14 |
| DEGRADED | 7 |
| COSMETIC | 3 |

| # | Severity | File:line | Issue |
|---|---|---|---|
| 1 | BLOCKER | `pipeline/produce.py:67-90` | Alias map blindly converts `front_heave_nmm` ↔ `front_heave_spring_nmm`; no GT3 conditional |
| 2 | BLOCKER | `pipeline/produce.py:413-420` | `car.heave_spring.front_m_eff_kg` direct access — `car.heave_spring=None` for GT3 |
| 3 | BLOCKER | `pipeline/produce.py:863-908` | Step 2 unconditionally consumed/recorded into RunTrace with no `present` guard |
| 4 | BLOCKER | `pipeline/produce.py:964-967` | `analyze_stint` called with `step2.front_heave_nmm` / `rear_third_nmm` raw |
| 5 | BLOCKER | `pipeline/produce.py:1009-1024` | `step2 is None` block writes off `validate_solution_legality(step2=...)` — no `present=False` branch |
| 6 | BLOCKER | `pipeline/produce.py:1497-1517` | `.sto` writer gated on `step2 is None`; passes `step2=step2` to `write_sto()` (per-car PARAM_IDS in `output/setup_writer.py` will choke on null heave for GT3) |
| 7 | BLOCKER | `pipeline/produce.py:1571-1577` | JSON `step2_heave` payload written via `to_public_output_payload(...)` regardless of GT3 |
| 8 | BLOCKER | `pipeline/produce.py:1602-1620` | Report-emission gated on `step2 is None`; for GT3 step2 is not None but `present=False` |
| 9 | BLOCKER | `pipeline/produce.py:1693-1694, 1808, 1847` | Delta card / predicted-telemetry / heave_calibration auto-update all read `step2.front_heave_nmm`, `step2.rear_third_nmm`, `predicted_telemetry.front_heave_travel_used_pct` with no GT3 short-circuit |
| 10 | BLOCKER | `pipeline/produce.py:1320-1321` | `--top-n` table prints `front_heave_spring_nmm` / `rear_third_spring_nmm` columns with hardcoded layout |
| 11 | BLOCKER | `pipeline/reason.py:3088-3175` | `_run_sequential_solver()` always constructs `HeaveSolver` and consumes `_step2.front_heave_nmm` / `rear_third_nmm` for downstream steps |
| 12 | BLOCKER | `pipeline/reason.py:3373-3390` | Heave-floor enforcement `step2.front_heave_nmm < heave_floor` — GT3 step2 has zero numerics, would always trip |
| 13 | BLOCKER | `pipeline/reason.py:2208-2258` | Modifier derivation: `front_heave_min_floor_nmm` and `rear_third_min_floor_nmm` from observed session rates — pre-IBT GT3 sessions report zero, post-Phase 2 GT3 sessions don't have these fields at all |
| 14 | BLOCKER | `pipeline/report.py:458-484` | `CURRENT vs RECOMMENDED` table prints "Front heave" / "Rear third" rows unconditionally for any non-Ferrari car |
| 15 | BLOCKER | `pipeline/report.py:535-584` | `FRONT HEAVE TRAVEL BUDGET` block runs whenever `step2.defl_max_front_mm > 0`; for GT3 it's 0, so silent drop, but the surrounding gate has no GT3-class conditional and the printed labels still call it "Heave spring" |
| 16 | BLOCKER | `pipeline/report.py:215-223` | `GarageSetupState.from_solver_steps(step2=step2, …)` builds garage prediction from a heave-bearing step; GT3 needs a coil-only constructor |
| 17 | DEGRADED | `pipeline/produce.py:1919-1923` | `--car` and `--track` are free-form strings; no GT3-aware validation. With 11 GT3 cars + Spielberg/Red Bull Ring there is no track-profile gate to refuse "no calibration data" runs cleanly |
| 18 | DEGRADED | `pipeline/produce.py:402, 1851` | Track slug derived as `track_name.lower().split()[0]` — fragile for "Red Bull Ring", "Autodromo Internacional do Algarve". For GT3 IBTs the user task explicitly flags Spielberg/Red Bull Ring, where this collapses to "red" |
| 19 | DEGRADED | `pipeline/produce.py:438-440` | `if wing not in surfaces` — GT3 aero maps load via the same `load_car_surfaces`, but GT3 wings are 0.5° steps (Aston, McLaren) or come at offsets like 5.7° (Porsche 992 GT3 R). Float key lookup will miss; needs nearest-key matcher |
| 20 | DEGRADED | `pipeline/reason.py:3373-3390` | `heave_solver = HeaveSolver(car, track); step2 = heave_solver.solve(...)` — re-solves Step 2 even when already known absent. For GT3 cars the `HeaveSolver` constructor itself may not be designed to handle `heave_spring=None` |
| 21 | DEGRADED | `pipeline/report.py:32, 185` | `HeaveSolution` is a non-Optional positional argument in the `generate_report()` signature — function is structurally typed against the GTP shape |
| 22 | DEGRADED | `pipeline/report.py:106-117` | `predict_candidate_telemetry(step2=step2, …)` always passes step2; `solver/predictor.py` likely reads heave fields without guard (out of scope but cascading) |
| 23 | DEGRADED | `pipeline/scan.py:459` | `scan` loops over a hardcoded `["front_ride_height", "rear_ride_height", "heave_spring_defl_static"]` model-health list. GT3 has no `heave_spring_defl_static` model — health report will always log "uncalibrated" for that field |
| 24 | COSMETIC | `pipeline/produce.py:10-12` | Docstring examples all use `--car bmw` (the GTP BMW). After GT3 phase 2 these collide with the same canonical name pattern; consider clarifying once GT3 BMW M4 lands |
| 25 | COSMETIC | `pipeline/report.py:302` | `_car_slug = getattr(car, "canonical_name", "bmw")` — BMW default is GTP-class assumption; harmless because all CarModels have canonical_name set, but reads as if BMW were the ground state |
| 26 | COSMETIC | `pipeline/__main__.py:1-19` | Deprecated module — unaffected by GT3 |

## Findings

### BLOCKER 1 — Alias map for heave/third spring is unconditional

`pipeline/produce.py:67-90`

```python
def _normalize_grid_search_params_for_overrides(params: dict[str, object] | None) -> dict[str, object]:
    ...
    alias_map = {
        "front_heave_nmm": "front_heave_spring_nmm",
        "rear_third_nmm": "rear_third_spring_nmm",
        "rear_spring_nmm": "rear_spring_rate_nmm",
        ...
    }
```

The alias mapping pretends `front_heave_nmm` is always a meaningful key. For GT3 cars (`car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL`), there is no front heave spring; emitting these aliases into solve-chain overrides creates an override the solve chain cannot satisfy.

Fix shape: branch on `car.suspension_arch.has_heave_third`:

```python
if car.suspension_arch.has_heave_third:
    alias_map["front_heave_nmm"] = "front_heave_spring_nmm"
    alias_map["rear_third_nmm"] = "rear_third_spring_nmm"
# GT3: skip these aliases entirely; corner spring rates are the only spring controls
```

The function takes no `car` argument today — must be threaded through `_normalize_grid_search_params_for_overrides(params, *, car=car)` from the call site at `pipeline/produce.py:1226`.

### BLOCKER 2 — `car.heave_spring.front_m_eff_kg` direct access

`pipeline/produce.py:413-420`

```python
if learned.heave_m_eff_front_kg is not None:
    _existing_front = car.heave_spring.front_m_eff_kg
    if _existing_front > 0 and learned.heave_m_eff_front_kg <= _existing_front * 2.0:
        car.heave_spring.front_m_eff_kg = learned.heave_m_eff_front_kg
if learned.heave_m_eff_rear_kg is not None:
    _existing_rear = car.heave_spring.rear_m_eff_kg
    ...
```

Per `car_model/cars.py:1756-1770`, GT3 cars must set `heave_spring=None`. The `__post_init__` validator enforces this. Reading `car.heave_spring.front_m_eff_kg` will raise `AttributeError: 'NoneType' object has no attribute 'front_m_eff_kg'` for any GT3 car.

Fix shape:

```python
if car.suspension_arch.has_heave_third and car.heave_spring is not None:
    if learned.heave_m_eff_front_kg is not None:
        ...
# else: GT3 has no heave m_eff to update; skip silently
```

### BLOCKER 3 — Step 2 unconditionally recorded and consumed

`pipeline/produce.py:863-908` (and stint-rematerialization at 925-945)

```python
step2 = base_solve_result.step2
...
step1, step2, step3, step4, step5, step6 = _apply_calibration_step_blocks(...)
...
run_trace.record_step(2, step2, physics_override=False)
```

The calibration gate (`_apply_calibration_step_blocks`, defined at `pipeline/produce.py:230-255`) only sets `step2 = None` when `2 in blocked_steps`. For GT3 cars Step 2 should never run — it should resolve to `HeaveSolution.null()` with `present=False`, **and the calibration gate must not classify GT3 Step 2 as "blocked" (which prints calibration instructions)**.

Fix shape: add a pre-gate skip:

```python
if not car.suspension_arch.has_heave_third:
    # GT3: step2 is structurally absent, not "blocked"
    step2 = HeaveSolution.null(
        front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
        rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
    )
    run_trace.record_step(2, step2, physics_override=False, note="GT3: not applicable")
else:
    run_trace.record_step(2, step2, physics_override=False)
```

This must happen at the SolveChain level (`solver/solve_chain.py`) not just here, but the produce-side guard is needed for the recording/JSON/report path.

### BLOCKER 4 — analyze_stint

`pipeline/produce.py:964-967`

```python
stint_result = analyze_stint(
    car=car,
    stint_laps=getattr(args, "stint_laps", 30),
    base_heave_nmm=step2.front_heave_nmm,
    base_third_nmm=step2.rear_third_nmm,
    ...
)
```

`step2` for a GT3 car has both fields = 0.0 (per `HeaveSolution.null()`). Either `analyze_stint` will compute nonsense, or it will divide-by-zero, depending on how `solver/stint_model.py` consumes them.

Fix: branch on `step2.present`:

```python
if step2.present:
    stint_result = analyze_stint(
        car=car,
        base_heave_nmm=step2.front_heave_nmm,
        base_third_nmm=step2.rear_third_nmm,
        ...
    )
else:
    stint_result = analyze_stint(
        car=car,
        base_heave_nmm=None,    # GT3: no heave to evolve
        base_third_nmm=None,
        ...
    )
    # solver/stint_model.py will need an analogous None-aware path
```

The downstream change to `solver/stint_model.py` is out of scope for this audit unit but is a hard dependency.

### BLOCKER 5 — validate_solution_legality

`pipeline/produce.py:1009-1024`

```python
if step1 is None or step2 is None or step3 is None:
    legal_validation = LegalValidation(valid=False, ...)
else:
    legal_validation = validate_solution_legality(
        car=car,
        track_name=track.track_name,
        step1=step1,
        step2=step2,
        step3=step3,
        fuel_l=fuel,
        step5=step5,
    )
```

For GT3 `step2 is not None` (it's a `HeaveSolution.null()`), so the check passes and `validate_solution_legality` runs with a heave-zero step2. `solver/legality_engine.py` likely checks heave spring legality (range check, perch range check) and will declare GT3 setups illegal because `front_heave_nmm=0.0` falls outside `car.heave_spring.front_min_nmm`.

Fix shape: thread `step2.present` into `validate_solution_legality` so legal_space can skip heave-spring checks for GT3.

### BLOCKER 6 — write_sto

`pipeline/produce.py:1497-1517`

```python
if step1 is None or step2 is None or step3 is None:
    print("\n[sto] Cannot write .sto — steps 1-3 are required but blocked by calibration.")
else:
    sto_path = write_sto(
        car_name=car.name,
        ...
        step1=step1, step2=step2, step3=step3,
        step4=step4, step5=step5, step6=step6,
        ...
    )
```

Same null vs. `present=False` problem. `output/setup_writer.py` per-car PARAM_IDS dicts for GT3 (which Phase 2 of the GT3 work is supposed to add per `docs/gt3_session_info_schema.md` lines 9, 195) will not contain `CarSetup_HeaveSpring_*` IDs — but the writer still receives `step2` with bogus 0.0 numerics and may try to map them.

Fix: `if step1 is None or step3 is None or (car.suspension_arch.has_heave_third and step2 is None): refuse_sto`. The writer itself must consult `step2.present` before emitting heave/third XML IDs.

### BLOCKER 7 — JSON `step2_heave` payload

`pipeline/produce.py:1571-1577`, parallel block at `pipeline/reason.py:3745-3751`

```python
"step1_rake": to_public_output_payload(car.canonical_name, step1),
"step2_heave": to_public_output_payload(car.canonical_name, step2),
"step3_corner": to_public_output_payload(car.canonical_name, step3),
...
```

For GT3 this serializes a zero-valued heave dataclass under a key implying real data. Two acceptable fixes:

```python
"step2_heave": (
    to_public_output_payload(car.canonical_name, step2)
    if step2 is not None and getattr(step2, "present", True)
    else {"present": False, "reason": "GT3 has no heave/third springs"}
),
```

or omit the key entirely for GT3. Writing zero numerics under `step2_heave.front_heave_nmm` will mislead any downstream tooling (delta card at `pipeline/produce.py:1693-1694`, learner at 1847-1865, webapp readers).

### BLOCKER 8 — Report emission gate

`pipeline/produce.py:1602-1620`

```python
if step1 is None or step2 is None or step3 is None:
    report = "Solver steps blocked — no setup report available."
else:
    report = generate_report(...)
```

For GT3 this passes (step2 not None) and `generate_report` (signature at `pipeline/report.py:185` requires `step2: HeaveSolution`) is called with a null heave that the report formatter will then try to render.

### BLOCKER 9 — Delta card / predicted-telemetry / heave_calibration

`pipeline/produce.py:1693-1694, 1808, 1847-1865`

Three places read `step2.front_heave_nmm`, `step2.rear_third_nmm` or `_setup.get("front_heave_nmm")`:

```python
# 1693-1694: delta card
"front_heave_nmm": public_output_value(car, "front_heave_nmm", step2.front_heave_nmm),
"rear_third_nmm": public_output_value(car, "rear_third_nmm", step2.rear_third_nmm),

# 1808: solver predictions for learner
"front_heave_travel_used_pct": predicted_telemetry.front_heave_travel_used_pct,

# 1847-1868: heave_calibration auto-update
_heave = _setup.get("front_heave_nmm") or _setup.get("front_heave_spring_nmm")
_sigma = _tel.get("front_rh_std_mm")
if _heave and _sigma:
    _cal = HeaveCalibration.load(_car_slug, _track_slug)
    _cal.add_run(heave_nmm=float(_heave), sigma_mm=float(_sigma), ...)
```

For GT3 the third item is the most insidious: `_heave` will be `None` (GT3 IBT has no front_heave field per `docs/gt3_session_info_schema.md` line 41-42 — the only spring value is per-corner `SpringRate`), so the `if _heave and _sigma:` guard happens to skip cleanly. But the first two are unguarded.

Fix shape:

```python
# 1693-1694
if car.suspension_arch.has_heave_third:
    _recommended_dict["front_heave_nmm"] = public_output_value(...)
    _recommended_dict["rear_third_nmm"] = public_output_value(...)
# GT3: skip heave row entirely
```

And similar for the prediction dict.

### BLOCKER 10 — `--top-n` candidate table prints heave/third columns

`pipeline/produce.py:1310-1326`

```python
log(f"  {'Rank':<5} {'Score':>8}  {'Family':<18}  {'Wing':>5}  {'FH-Spg':>7}  {'R3-Spg':>7}  {'Trsn':>6}  {'FARB':>5}  {'RARB':>5}  Penalties")
...
log(
    f"  ...  "
    f"{p.get('front_heave_spring_nmm', 0):>7.1f}  "
    f"{p.get('rear_third_spring_nmm', 0):>7.1f}  "
    f"{p.get('front_torsion_od_mm', 0):>6.2f}  "
    ...
)
```

Hardcoded 9-column GTP table. For GT3 the `FH-Spg`, `R3-Spg`, and `Trsn` columns are meaningless (defaulting to 0 will print "0.0  0.0  0.00" rows, which is misinformation, not absence). Needs a per-car column schema.

### BLOCKER 11 — `_run_sequential_solver` constructs HeaveSolver unconditionally

`pipeline/reason.py:3088-3175`

```python
heave_solver = HeaveSolver(car, track)
_step2 = heave_solver.solve(
    dynamic_front_rh_mm=_step1.dynamic_front_rh_mm,
    dynamic_rear_rh_mm=_step1.dynamic_rear_rh_mm,
    front_heave_floor_nmm=mods.front_heave_min_floor_nmm,
    ...
)
...
corner_solver.solve(
    front_heave_nmm=_step2.front_heave_nmm,
    rear_third_nmm=_step2.rear_third_nmm,
    ...
)
```

Same pattern as `pipeline/produce.py` BLOCKER 3 but in the multi-IBT reason engine. The local sequential solver in `pipeline/reason.py` re-runs the whole 6-step chain in-line; it must also branch on `car.suspension_arch.has_heave_third` and substitute `HeaveSolution.null()` for GT3.

### BLOCKER 12 — Heave-floor enforcement on candidate

`pipeline/reason.py:3373-3390`

```python
heave_floor = mods.front_heave_min_floor_nmm
if heave_floor > 0 and step2.front_heave_nmm < heave_floor:
    state.solver_notes.append(
        f"Candidate heave {step2.front_heave_nmm:.0f} N/mm < floor {heave_floor:.0f} N/mm "
        ...
    )
    heave_solver = HeaveSolver(car, track)
    step2 = heave_solver.solve(...)
```

For GT3 `step2.front_heave_nmm == 0.0` will always trigger the re-solve (when any modifier-derived floor exists), which will then construct a `HeaveSolver` against `car.heave_spring=None` and fail.

Fix shape:

```python
if step2.present and heave_floor > 0 and step2.front_heave_nmm < heave_floor:
    ...
# GT3: no heave to floor
```

### BLOCKER 13 — Modifier `front_heave_min_floor_nmm` derivation from session medians

`pipeline/reason.py:2208-2258`

```python
if float(np.mean(front_bottoming)) > 5 and not kerb_dominant_bottoming:
    heave_rates = [
        s.setup.front_heave_nmm for s in analysis_sessions
        if s.setup.front_heave_nmm
    ]
    ...
    mods.front_heave_min_floor_nmm = min(good_rates)
```

Reads `s.setup.front_heave_nmm` from each analyzed session's parsed CurrentSetup. For GT3 IBTs the field will not exist (or will be 0/None). The truthy filter `if s.setup.front_heave_nmm` saves the day for absent GT3 fields, but the modifier's whole purpose evaporates — and the alternate path at line 2186-2191 (`new_floor = max(mods.front_heave_min_floor_nmm, 38.0)` triggered by pitch range) will set a floor that downstream solver code then tries to apply, with no GT3 short-circuit.

Fix: gate the entire heave-modifier section on `car.suspension_arch.has_heave_third`.

### BLOCKER 14 — `CURRENT vs RECOMMENDED` table

`pipeline/report.py:454-486`

```python
if (current_setup is not None and step1 is not None and step2 is not None
        and step3 is not None and step4 is not None and step5 is not None):
    ...
    _is_ferrari = getattr(car, "canonical_name", "") == "ferrari"
    _hu = "idx" if _is_ferrari else "N/mm"
    ...
    _cur_fh  = float(public_output_value(car, "front_heave_nmm",     current_setup.front_heave_nmm))
    _rec_fh  = float(public_output_value(car, "front_heave_nmm",     step2.front_heave_nmm))
    _cur_rh  = float(public_output_value(car, "rear_third_nmm",      current_setup.rear_third_nmm))
    _rec_rh  = float(public_output_value(car, "rear_third_nmm",      step2.rear_third_nmm))
    ...
    a(_cmp("Front heave",        _cur_fh,   _rec_fh,  _hu, ".0f"))
    _rear_heave_lbl = "Rear heave" if _is_ferrari else "Rear third"
    a(_cmp(_rear_heave_lbl,      _cur_rh,   _rec_rh,  _hu, ".0f"))
```

Hardcoded GTP-class assumption: every non-Ferrari car has a "Front heave" + "Rear third" row in N/mm. For GT3 these values are zero on the Recomm side and missing on the Current side. The table will print `—` or 0.0, conveying "the solver wants 0 N/mm for your heave spring".

Fix: branch on `car.suspension_arch.has_heave_third`:

```python
if car.suspension_arch.has_heave_third:
    a(_cmp("Front heave", _cur_fh, _rec_fh, _hu, ".0f"))
    a(_cmp(_rear_heave_lbl, _cur_rh, _rec_rh, _hu, ".0f"))
# GT3 cars: skip heave/third rows; the corner-spring rows below already cover the spring story
```

### BLOCKER 15 — `FRONT HEAVE TRAVEL BUDGET` block

`pipeline/report.py:535-584`

```python
if step2 is not None and step2.defl_max_front_mm > 0:
    ...
    a(_hdr("FRONT HEAVE TRAVEL BUDGET"))
    a(f"  Heave spring:       {step2.front_heave_nmm:.0f} {_hu2}")
    ...
```

The numeric guard `step2.defl_max_front_mm > 0` happens to fire-block this for GT3 (`HeaveSolution.null()` zeroes `defl_max_front_mm`), so the section silently disappears. Functionally it's a soft-break, not a hard failure, but it's still a BLOCKER because:

1. The guard pattern is fragile — any future change to populate `defl_max_front_mm` defensively (e.g., to `1e-6` to avoid zero-division elsewhere) immediately breaks GT3.
2. The pipeline lacks an analogous "GT3 travel budget" view for **per-corner shock travel**, which IS the relevant mechanical-grip constraint for GT3 cars per `docs/gt3_per_car_spec.md` line 10.

Fix shape: replace the implicit numeric gate with explicit architecture branching, and add a per-corner travel-budget block for GT3 (separate finding, out of this audit's scope but flagged).

```python
if car.suspension_arch.has_heave_third and step2 is not None and step2.present:
    a(_hdr("FRONT HEAVE TRAVEL BUDGET"))
    ...
elif not car.suspension_arch.has_heave_third:
    # GT3: travel budget is per-corner; emit a different block (TBD)
    pass
```

### BLOCKER 16 — `GarageSetupState.from_solver_steps`

`pipeline/report.py:215-223`

```python
_solver_state = GarageSetupState.from_solver_steps(
    step1=step1, step2=step2, step3=step3, step5=step5,
    fuel_l=report_fuel_l,
) if step1 is not None and step2 is not None and step3 is not None else None
if garage_model is not None and _solver_state is not None:
    garage_outputs = garage_model.predict(
        _solver_state,
        front_excursion_p99_mm=step2.front_excursion_at_rate_mm,
    )
```

`from_solver_steps` is the constructor that bridges solver output → garage state for the round-trip RH check. It assumes step2 carries heave/third spring data; per `car_model/garage.py` the resulting `GarageSetupState` has `front_heave_nmm` and `rear_third_nmm` fields. For GT3 these need to be `None` (not 0.0) so the `DirectRegression`-based `garage_model.predict()` does not feed zero into a regression that expects `1/k`.

Also, `front_excursion_p99_mm=step2.front_excursion_at_rate_mm` is GT3-meaningless (the relevant excursion is per-corner shock displacement, not heave-axis).

Fix: needs a `GarageSetupState.from_solver_steps_coil_only(...)` or a `present`-aware branch in the existing constructor; the pipeline call site here must not pass step2 for GT3.

### DEGRADED 17 — `--car` / `--track` validation

`pipeline/produce.py:1919-1923`

```python
parser.add_argument("--car", required=True, help="Car name (e.g., bmw)")
parser.add_argument("--track", type=str, default=None,
                    help="Track name hint (e.g., silverstone). If a saved profile exists at "
                         "data/tracks/{name}.json it will be loaded; otherwise the track "
                         "profile is derived from the IBT as usual.")
```

No validation against the GT3 catalog. With 11 GT3 cars (`docs/gt3_per_car_spec.md` line 5) and Spielberg / Red Bull Ring being the canonical Phase-2 IBT track, the user has no signal that calibration data does not exist for `--car bmw_m4_gt3 --track spielberg` (or however the canonical names land). The pipeline will execute, produce a setup, and only flag uncalibrated steps via the gate.

Fix shape: add an early sanity check after `_resolve_car_name_fn`:

```python
if car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
    log(f"  [GT3] {car.canonical_name}: GT3 class, scaffold-only as of Phase 2")
    log(f"        Known calibration: none. All steps will route through gate.")
```

### DEGRADED 18 — Track slug heuristic

`pipeline/produce.py:402, 1851`

```python
car_track_label = f"{car.canonical_name}/{track_name.lower().split()[0]}"
...
_track_slug = str(_track_raw).lower().split()[0]
```

This is the same bug class fixed for "autodromo" in 2026-04-10 (CLAUDE.md). For GT3 phase 2 the track is "Red Bull Ring" — `.split()[0]` collapses it to `"red"`. The same pattern at line 402 corrupts `car_track_label` to `bmw_m4_gt3/red`.

Fix: use `from car_model.registry import track_key` (already imported at line 341 as `_reg_track_key`) — `track_key("Red Bull Ring") → "spielberg"` per the alias table. CLAUDE.md note from 2026-04-10 explicitly says `produce.py uses track_key() instead of .split()[0]` was already done; this audit confirms the fix did NOT cover lines 402 and 1851.

### DEGRADED 19 — Wing key match

`pipeline/produce.py:438-440`

```python
surfaces = load_car_surfaces(car.canonical_name)
if wing not in surfaces:
    available = sorted(surfaces.keys())
    raise PipelineInputError(...)
```

GT3 wings include 0.5° steps (Aston Vantage, McLaren) and offset values like 5.7° for Porsche 992 GT3 R (`docs/gt3_session_info_schema.md` line 142). Float key equality on a `dict[float, ...]` is fragile. For McLaren 720S the parsed map is `2.5, 3.5, 4.5, ...` (per `docs/gt3_per_car_spec.md` line 141) — a user passing `--wing 5` will hit `KeyError`-equivalent.

Fix: nearest-key match within tolerance, or document the canonical wing values per car at the CLI.

### DEGRADED 20 — Re-solve heave on candidate

`pipeline/reason.py:3379-3390`

Same pattern as BLOCKER 12 but specifically the construction `heave_solver = HeaveSolver(car, track)`. Since GT3's `car.heave_spring is None`, this constructor will likely raise. The audit can't go deeper without inspecting `solver/heave_solver.py:HeaveSolver.__init__`, but the call needs to be gated on `car.suspension_arch.has_heave_third` either way.

### DEGRADED 21 — `generate_report` signature

`pipeline/report.py:32, 185`

```python
from solver.heave_solver import HeaveSolution
...
def generate_report(
    car: CarModel,
    track: TrackProfile,
    measured: MeasuredState,
    driver: DriverProfile,
    diagnosis: Diagnosis,
    corners: list[CornerAnalysis],
    aero_grad: AeroGradients,
    modifiers: SolverModifiers,
    step1: RakeSolution,
    step2: HeaveSolution,
    step3: CornerSpringSolution,
    ...
```

The annotation `step2: HeaveSolution` is structurally honest for GT3 (`HeaveSolution.null()` is still `HeaveSolution`). What's missing is documentation that `step2.present` may be `False`. Update the docstring and add the guard explicitly inside the function body. Pure annotation issue, but worth flagging because every consumer of this signature will silently treat it as a populated heave solution.

### DEGRADED 22 — predict_candidate_telemetry

`pipeline/report.py:106-117`

```python
predicted_telemetry, prediction_confidence = predict_candidate_telemetry(
    current_setup=current_setup,
    baseline_measured=measured,
    step1=step1,
    step2=step2,
    step3=step3,
    step4=step4,
    step5=step5,
    step6=step6,
    supporting=supporting,
    corrections=prediction_corrections,
)
```

`solver/predictor.py` is out of scope for this audit unit; cascading impact only. The call here is fine if and only if `predict_candidate_telemetry` itself guards on `step2.present`.

### DEGRADED 23 — Scan health-report metric list

`pipeline/scan.py:459`

```python
for name in ["front_ride_height", "rear_ride_height", "heave_spring_defl_static"]:
    m = getattr(models, name, None)
    if m and m.is_calibrated:
        ratio = m.loo_rmse / max(m.rmse, 0.001)
        health.append(f"{name}: R2={m.r_squared:.3f} LOO/train={ratio:.1f}x")
    elif m:
        health.append(f"{name}: uncalibrated")
```

For GT3 cars `models.heave_spring_defl_static` will be `None` (no such model exists), so the entire row drops silently. The user's scan output will simply lack the row — informative absence, but the user has no signal whether "no row" means "model missing because GT3" or "model missing because we forgot to fit". Add a `coil_spring_defl_static` row for GT3 (separate calibration model).

### COSMETIC 24 — Docstring uses `bmw`

`pipeline/produce.py:10-12`

```python
Usage:
    python -m pipeline.produce --car bmw --ibt path/to/session.ibt --wing 17
    python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --sto output.sto
    python -m pipeline.produce --car bmw --ibt session.ibt --wing 17 --lap 25 --json out.json
```

Once GT3 BMW M4 lands, `--car bmw` will be ambiguous between BMW M Hybrid V8 (GTP) and BMW M4 GT3 EVO. Disambiguation is a registry concern (out of scope), but the help text can already note "(GTP class)".

### COSMETIC 25 — `_car_slug` BMW default

`pipeline/report.py:302`

```python
_car_slug = getattr(car, "canonical_name", "bmw")
```

`canonical_name` is always set on `CarModel`, so the default never fires. Cosmetic only — the `"bmw"` literal reads as a GTP-class assumption embedded in the report formatter.

### COSMETIC 26 — `pipeline/__main__.py`

Deprecated; passes args through to `pipeline.produce`. No GT3 impact directly.

## GT3-correct-as-is paths

A few pipeline behaviors are already GT3-correct:

- `_apply_calibration_step_blocks` at `pipeline/produce.py:230-255` — orthogonal to architecture; correctly nulls steps the gate explicitly blocked. GT3 needs a separate "not applicable" code path, but this function should not be touched.
- `pipeline/produce.py:1009-1014` LegalValidation construction when steps blocked — the `step2 is None` short-circuit is GT3-irrelevant (GT3 step2 is non-None) and continues to work.
- `pipeline/report.py:507-508` damper section — `if step6 is not None` is GT3-correct (per `docs/gt3_session_info_schema.md` line 64-71, GT3 dampers are per-axle but still produce a `DamperSolution`).
- `pipeline/scan.py:156-168` — subprocess invocation of `pipeline.produce` is car/architecture-blind, fine.
- `pipeline/produce.py:1842-1870` heave_calibration auto-update — the `if _heave and _sigma:` truthy guard happens to skip cleanly when GT3 IBT has no heave field, but this is brittle (see BLOCKER 9).

## Risk summary

The pipeline is structurally GTP-shaped. Step 2 is treated as a load-bearing data dependency for: stint analysis (`analyze_stint`), legality validation (`validate_solution_legality`), .sto writing (`write_sto`), JSON output (`step2_heave` key), report rendering (CURRENT vs RECOMMENDED, FRONT HEAVE TRAVEL BUDGET), garage round-trip prediction (`GarageSetupState.from_solver_steps`), delta cards, and the empirical heave-σ calibration auto-updater.

Without per-call-site `present`/`has_heave_third` guards, GT3 runs will either:

1. **Silently emit zeroes** (delta card, JSON `step2_heave`, predicted telemetry) — misleading, violates Key Principle 8 ("no silent fallbacks").
2. **Crash** (BLOCKER 2: `car.heave_spring.front_m_eff_kg` AttributeError; BLOCKER 12: HeaveSolver init against `heave_spring=None`).
3. **Falsely fail legality** (BLOCKER 5: legal_engine flagging GT3 as illegal because front_heave_nmm=0 is below the absent car's heave min).

Top three highest-impact fixes (do these first):

1. **BLOCKER 2** — `car.heave_spring.front_m_eff_kg` direct access. Crashes on first GT3 run with auto-learn enabled.
2. **BLOCKER 11** — `_run_sequential_solver` constructing `HeaveSolver` for GT3. Crashes the multi-IBT path immediately.
3. **BLOCKER 14** — CURRENT vs RECOMMENDED table printing 0 N/mm for "Front heave". User-visible misinformation in every GT3 report.

After these three, the rest of the BLOCKER list is mechanical: thread `car.suspension_arch.has_heave_third` (or `step2.present`) through every call site flagged.

## Effort estimate

| Class | Files | Approx hours |
|---|---|---|
| Mechanical guards (BLOCKER 1, 2, 4, 7, 8, 9, 12, 13, 14) | produce.py, reason.py, report.py | 4–6 h |
| Calibration-gate / step2 lifecycle redesign (BLOCKER 3, 5, 6, 16) | produce.py + cross-cutting solver.solve_chain, output.setup_writer | 6–10 h |
| `_run_sequential_solver` GT3 branch (BLOCKER 11) | reason.py + heave_solver | 2–3 h |
| `--top-n` per-car column schema (BLOCKER 10) | produce.py | 2 h |
| Track slug + wing-key + CLI validation (DEGRADED 17, 18, 19) | produce.py | 1–2 h |
| Health metric list (DEGRADED 23) | scan.py | 0.5 h |
| Tests + regression-fixture refresh | tests/ | 4 h |
| **Total** | | **~20–28 h** |

## Dependencies

This audit unit's fixes cannot land in isolation. Hard dependencies on other audit units:

- `solver/solve_chain.py` — must short-circuit Step 2 to `HeaveSolution.null()` for GT3 cars before pipeline orchestration ever sees the result. Without that, the pipeline guards above paper over a missing solver behavior.
- `solver/heave_solver.py:HeaveSolver.__init__` — must accept `car.heave_spring=None` cleanly OR pipeline call sites must skip construction entirely. BLOCKER 11/12 hinge on this.
- `solver/legality_engine.py:validate_solution_legality` — must accept `step2.present=False` and skip heave-spring legality checks. BLOCKER 5.
- `solver/predictor.py:predict_candidate_telemetry` — must guard on `step2.present` for `front_heave_travel_used_pct` and related fields. DEGRADED 22.
- `solver/stint_model.py:analyze_stint` — must accept `base_heave_nmm=None` / `base_third_nmm=None`. BLOCKER 4.
- `output/setup_writer.py` — per-car PARAM_IDS dispatch for GT3 (already documented as Phase 2 work in `docs/gt3_session_info_schema.md` line 195). Must skip heave/third XML emission for GT3. BLOCKER 6.
- `car_model/garage.py:GarageSetupState.from_solver_steps` — needs a coil-only constructor or `step2.present`-aware path. BLOCKER 16.
- `car_model/calibration_gate.py` — must classify Step 2 as `not_applicable` (not `uncalibrated`) for GT3 cars, so the gate doesn't print "calibration instructions" telling the user to gather heave-σ data. BLOCKER 3.

Without those upstream pieces, fixing the pipeline alone yields a pipeline that politely declines to run for GT3, instead of one that produces useful output.
