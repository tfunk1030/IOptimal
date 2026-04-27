# GT3 Phase 2 Audit — Solver Objective and Sensitivity

## Scope

Files audited for GT3-readiness:

- `solver/objective.py` (2142 lines) — multi-objective scoring function
- `solver/sensitivity.py` (600 lines) — constraint-proximity / parameter-sensitivity reporting
- `solver/laptime_sensitivity.py` (1467 lines) — lap-time sensitivity tables
- `solver/full_setup_optimizer.py` (582 lines) — top-level setup optimizer (BMW/Sebring constrained)

GT3 architectural facts driving this audit (from `docs/gt3_per_car_spec.md`,
`docs/gt3_session_info_schema.md`, and `car_model/cars.py:24-65, 1745-1772`):

- `SuspensionArchitecture.GT3_COIL_4WHEEL` — coil-overs at all four corners.
  No heave/third springs.
- `CarModel.heave_spring` is **None** for GT3 cars (enforced by `__post_init__`,
  `car_model/cars.py:1751-1771`). Reading `car.heave_spring.<anything>` AttributeErrors.
- `CornerSpringModel.front_torsion_c=0.0` and `front_torsion_od_options=[]`
  for GT3 (`car_model/cars.py:3231-3234`). The `if c_torsion > 0` paths already branch
  away from the OD^4 wheel-rate formula.
- Step 2 returns `HeaveSolution.null()` (`solver/heave_solver.py:113-143`) with
  `present=False` and zero numeric fields (`front_heave_nmm=0`,
  `front_dynamic_rh_mm=0` unless caller threads it through, etc.).
- GT3 dampers are **per-axle (8 channels)**, not per-corner (16) — see
  `docs/gt3_session_info_schema.md` lines 74-85. `DamperSolution.lf/rf/lr/rr`
  remains the public shape; per-axle dispatch is a setup_writer/damper_solver
  concern downstream. Per-axle is a meaningful flag for objective scoring
  (we should not assume LF≠RF asymmetry exists).
- No torsion bars anywhere in the GT3 grid.
- ARB encodings vary wildly (paired blade D-codes, single ascending,
  single descending, integer setting) — see `gt3_per_car_spec.md` "ARB blade
  encoding" table.

## Summary table

| File | BLOCKER | DEGRADED | COSMETIC | Total |
|---|---:|---:|---:|---:|
| `solver/objective.py` | 5 | 8 | 3 | 16 |
| `solver/sensitivity.py` | 1 | 4 | 1 | 6 |
| `solver/laptime_sensitivity.py` | 2 | 7 | 2 | 11 |
| `solver/full_setup_optimizer.py` | 0 (out-of-scope) | 0 | 1 | 1 |
| **Total** | **8** | **19** | **7** | **34** |

`solver/full_setup_optimizer.py` is gated by `_is_bmw_sebring()`
(`solver/full_setup_optimizer.py:95-99`) and explicitly returns `None` for any
non-BMW/Sebring car/track pair, so it has zero direct GT3 impact and its
findings are cosmetic only.

---

## Findings — `solver/objective.py`

### F-O-1 (BLOCKER): `evaluate_physics()` reads `car.heave_spring.front_m_eff_kg` unconditionally

`solver/objective.py:862-863`

```python
m_eff_front = car.heave_spring.front_m_eff_kg
m_eff_rear = car.heave_spring.rear_m_eff_kg
```

**What it expects**: a non-null `HeaveSpringModel` carrying per-axle effective
mass for the heave-spring excursion model.

**GT3 reality**: `car.heave_spring is None` for `GT3_COIL_4WHEEL`. This raises
`AttributeError: 'NoneType' object has no attribute 'front_m_eff_kg'` on the
first GT3 candidate evaluation, hard-killing the entire scoring path.

**Risk**: BLOCKER — every objective evaluation crashes for GT3.

**Recommended GT3 handling**: For GT3 cars, the relevant effective mass is
the corner mass (each wheel sees ~¼ of total mass plus rotating
unsprung). Compute from existing fields:

```python
if car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
    total = car.total_mass(car.fuel_capacity_l)  # or current fuel
    m_eff_front = total * car.weight_dist_front / 2.0
    m_eff_rear  = total * (1.0 - car.weight_dist_front) / 2.0
else:
    m_eff_front = car.heave_spring.front_m_eff_kg
    m_eff_rear  = car.heave_spring.rear_m_eff_kg
```

The damped-excursion model is then run on the corner-spring rate (Step 3
output) instead of `front_heave_clamped`. See F-O-2.

**Effort**: ~1 hour, including a smoke test from a GT3 fixture.

---

### F-O-2 (BLOCKER): Excursion / σ physics is built around heave-spring rate

`solver/objective.py:884-912, 996-1006`

```python
front_heave_clamped = max(5.0, front_heave_nmm)  # prevent div/zero physics
result.front_excursion_mm = damped_excursion_mm(
    v_p99_front, m_eff_front, front_heave_clamped,
    tyre_vertical_rate_nmm=tyre_vr_front,
    parallel_wheel_rate_nmm=front_wheel_rate * 0.5,
)
...
if front_heave_nmm < 20.0:
    result.front_excursion_mm = max(result.front_excursion_mm, 30.0)
```

**What it expects**: a non-zero `front_heave_spring_nmm` (default 50.0). For
GT3 the candidate dict will not contain this key (no GT3 candidate generator
should populate it), and `params.get("front_heave_spring_nmm", car.front_heave_spring_nmm)`
on line 786 will read `car.front_heave_spring_nmm` which doesn't exist on
`CarModel` (the field is `car.heave_spring.front_baseline_nmm` etc., and
CarModel does not expose a flat `front_heave_spring_nmm` attribute) — that
itself is a separate bug already lurking but masked by the `params.get`
fallback.

**GT3 reality**: For coil-only GT3 cars, the dominant vertical stiffness at
each corner IS the corner spring. The `damped_excursion_mm` call should run
with `k_front_corner_nmm` (= `front_wheel_rate`) as the primary spring and
`parallel_wheel_rate_nmm=0.0` (no second spring in parallel). The `< 20.0
N/mm → 30 mm cap` BMW heuristic is meaningless for GT3 (front coil rates
are 190-340 N/mm).

**Risk**: BLOCKER — even if F-O-1 is patched, the chosen rate is 0.0 (the
default `HeaveSolution.null()` value) which gets clamped to 5 N/mm and
produces nonsense excursions for stiff GT3 corners.

**Recommended GT3 handling**:

```python
if car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
    # Corner coil IS the dominant vertical stiffness; no parallel heave.
    k_front_for_excursion = front_wheel_rate  # already wheel rate (MR=1.0)
    k_rear_for_excursion  = rear_wheel_rate
    parallel_front = 0.0
    parallel_rear  = 0.0
else:
    k_front_for_excursion = max(5.0, front_heave_nmm)
    k_rear_for_excursion  = max(5.0, rear_third_nmm)
    parallel_front = front_wheel_rate * 0.5
    parallel_rear  = rear_wheel_rate * 0.5

result.front_excursion_mm = damped_excursion_mm(
    v_p99_front, m_eff_front, k_front_for_excursion,
    tyre_vertical_rate_nmm=tyre_vr_front,
    parallel_wheel_rate_nmm=parallel_front,
)
```

Drop the `< 20.0 N/mm` GTP-specific safety cap on the GT3 path.

**Effort**: 2 hours — fix, regression test against a GT3 fixture, and verify
the existing GTP regression baselines (`tests/fixtures/baselines/{bmw,porsche}_*.sto`)
still pass.

---

### F-O-3 (BLOCKER): `_compute_lltd_fuel_window()` reads heave/third params and torsion OD

`solver/objective.py:708-746`

```python
front_heave_nmm = params.get("front_heave_spring_nmm", 50.0)
rear_third_nmm = params.get("rear_third_spring_nmm", 450.0)
rear_spring_nmm = params.get("rear_spring_rate_nmm", 160.0)
front_torsion_od = params.get("front_torsion_od_mm",
                               car.corner_spring.front_torsion_od_options[0]
                               if car.corner_spring.front_torsion_od_options else 0.0)
...
c_torsion = car.corner_spring.front_torsion_c
if c_torsion > 0:
    front_wheel_rate = c_torsion * (front_torsion_od ** 4)
else:
    front_wheel_rate = car.corner_spring.front_roll_spring_rate_nmm
```

**What it expects**: front wheel rate comes from EITHER torsion OD^4 OR a
roll-spring fallback (Porsche 963 path).

**GT3 reality**: GT3 has neither — front coil corner spring is paired
left/right coils, not a single roll spring. The `front_roll_spring_rate_nmm`
fallback returns 0.0 (or whatever uninitialized default is on the GT3
`CornerSpringModel`), which collapses LLTD to ~0.0 and produces a 50 pp
"error" against the OptimumG target. Every GT3 candidate is then penalized
identically and the LLTD term contributes pure noise.

The corresponding code in `evaluate_physics()` (`solver/objective.py:826-833`) is
the same pattern.

**Risk**: BLOCKER — LLTD scoring is wrong for GT3, breaking any blade /
ARB-blade comparison and producing systematic fuel-window penalties.

**Recommended GT3 handling**: introduce a `front_corner_spring_nmm` param key
and a third branch:

```python
if _ferrari_controls is not None:
    # Ferrari indexed
    ...
elif car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
    front_corner_nmm = params.get(
        "front_corner_spring_nmm",
        car.corner_spring.front_spring_range_nmm[0],  # min as default
    )
    front_wheel_rate = front_corner_nmm * (car.corner_spring.front_motion_ratio ** 2)
    rear_wheel_rate  = rear_spring_nmm * (car.corner_spring.rear_motion_ratio ** 2)
elif car.corner_spring.front_torsion_c > 0:
    # GTP torsion-bar path (BMW/Ferrari/Cadillac/Acura)
    ...
else:
    # Porsche 963 roll-spring path
    ...
```

`car.corner_spring.front_spring_range_nmm` does not exist on the current
`CornerSpringModel` — Phase 0 added `rear_spring_range_nmm` only. A symmetric
front-spring field needs to be added when GT3 corner-spring physics gets
fleshed out, but this is a Phase 3 follow-on.

**Effort**: 2 hours for the LLTD branch in objective.py; another ~1 hour to
add the front-coil range field to `CornerSpringModel`.

---

### F-O-4 (BLOCKER): `_compute_platform_risk()` reads `car.heave_spring` for deflection legality

`solver/objective.py:1772-1796`

```python
_hsm = self.car.heave_spring        # ← AttributeError for GT3 (None)
_gr = self.car.garage_ranges
_k_front = params.get("front_heave_spring_nmm", 50.0)
...
_perch_front = _hsm.perch_offset_front_baseline_mm
_dm = self.car.deflection
_spring_defl = _dm.heave_spring_defl_static(_k_front, _perch_front, _od_mm)
```

**What it expects**: `_hsm` is the heave-spring model; the deflection
sub-model exposes `heave_spring_defl_static` and `heave_slider_defl_static`.

**GT3 reality**: `_hsm = None`. Even if guarded, `_dm` is
`DeflectionModel.uncalibrated()` for the BMW M4 GT3 stub (`car_model/cars.py:3280`),
so `_dm.is_calibrated == False` and the `_deflection_veto_enabled` guard at
`solver/objective.py:1806-1807` already short-circuits the veto. But the line that
reads `_hsm.perch_offset_front_baseline_mm` runs unconditionally before that
guard and will AttributeError.

**Risk**: BLOCKER — every GT3 candidate crashes inside `_compute_platform_risk()`.

**Recommended GT3 handling**: the entire heave-spring deflection block is
**not applicable** for GT3 cars — there is no heave spring to be illegal.
Wrap the block in:

```python
if self.car.heave_spring is not None and _dm.is_calibrated:
    # ... existing logic ...
```

The `front_static` / `front_pinned` reads later in the function (`_static_f =
_car.pushrod.front_pinned_rh_mm`, line 1758) also need a GT3 guard because
GT3 cars have no pinned front RH; the value is computed from corner
springs and pushrods. See F-O-9.

**Effort**: 2 hours, including testing that the GTP veto path is unchanged.

---

### F-O-5 (BLOCKER): `_compute_envelope_penalty()` builds a heave/third ratio penalty

`solver/objective.py:1985-2013`

```python
front_heave = params.get("front_heave_spring_nmm", 50.0)
rear_third = params.get("rear_third_spring_nmm", 450.0)
if rear_third > 0:
    ratio = front_heave / rear_third
    if ratio < 0.03 or ratio > 0.20:
        penalty.setup_distance_ms = 10.0
        soft_penalties.append(...)
...
uncertainty_penalty = self._heave_calibration_uncertainty_penalty_ms(front_heave)
...
realism_penalty = self._heave_realism_penalty_ms(front_heave)
```

**What it expects**: heave/third spring rates exist and are tunable inputs.

**GT3 reality**: `front_heave = 50.0` (default), `rear_third = 450.0`
(default), `ratio ≈ 0.111` — falls inside 0.03-0.20 → no penalty by accident
of defaults. `_heave_realism_penalty_ms` (line 422) reads
`self.car.heave_spring.front_realistic_range_nmm` which AttributeErrors on
GT3 (None).

**Risk**: BLOCKER — `_heave_realism_penalty_ms()` raises
`AttributeError: 'NoneType' object has no attribute 'front_realistic_range_nmm'`
on every GT3 candidate that reaches the envelope-penalty stage.

**Recommended GT3 handling**:

```python
if self.car.heave_spring is None:
    return 0.0   # GT3: no heave spring, no realism penalty
```

at the top of both `_heave_realism_penalty_ms` (line 422) and
`_heave_calibration_uncertainty_penalty_ms` (line 416). Also short-circuit
the heave/third ratio check in `_compute_envelope_penalty` since the
candidate dict will not legitimately carry those keys for GT3.

**Effort**: 30 minutes.

---

### F-O-6 (DEGRADED): `_torsion_arb_coupling_factor()` runs even with empty torsion options

`solver/objective.py:507-540`

```python
od_ref = self.car.corner_spring.front_torsion_od_ref_mm
if od_ref <= 0:
    return 1.0
coupling = self.car.torsion_arb_coupling
if coupling == 0.0:
    return 1.0
stiffness_ratio = (front_torsion_od / od_ref) ** 4
return 1.0 + coupling * (stiffness_ratio - 1.0)
```

**What it expects**: `od_ref > 0` AND non-zero coupling means BMW-style
correction applies.

**GT3 reality**: `front_torsion_od_ref_mm = 0.0` and
`torsion_arb_coupling = 0.0` for all GT3 stubs (`cars.py:3231-3232, 1667`),
so both early-return guards trigger and the function correctly returns 1.0
(no coupling). **GT3-correct as-is** by virtue of defensive defaults, but the
codepath should be made explicit: GT3 architecture should never enter this
function. Document or assert.

**Risk**: DEGRADED — works by coincidence; future code drift could re-enable
the BMW formula on GT3.

**Recommended GT3 handling**: add an architecture guard at function entry
for clarity and a short-circuit at the call sites
(`objective.py:746, 1030`):

```python
if self.car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
    return 1.0  # GT3: no torsion bar, coupling not applicable
```

**Effort**: 15 minutes.

---

### F-O-7 (DEGRADED): `evaluate_physics()` reads `front_torsion_od_mm` and `front_torsion_bar_index`

`solver/objective.py:794-833`

```python
_od_options = car.corner_spring.front_torsion_od_options
_od_default = _od_options[0] if _od_options else 0.0
front_torsion_od = params.get("front_torsion_od_mm", _od_default)
...
_ferrari_controls = car.ferrari_indexed_controls
if _ferrari_controls is not None:
    _ftb_idx = float(params.get("front_torsion_bar_index", 2.0))
    ...
else:
    c_torsion = car.corner_spring.front_torsion_c
    if c_torsion > 0:
        front_wheel_rate = c_torsion * (front_torsion_od ** 4)
    else:
        front_wheel_rate = car.corner_spring.front_roll_spring_rate_nmm
```

**What it expects**: front wheel rate comes from torsion OD or roll-spring.

**GT3 reality**: `front_torsion_od_options = []`, `front_torsion_c = 0.0`,
`front_roll_spring_rate_nmm = 0.0` (default). Fallthrough sets
`front_wheel_rate = 0.0`, which propagates into the LLTD denominator and
produces nonsense scoring (LLTD = 0 / k_rear ≈ 0).

This is a milder version of F-O-3 (LLTD fuel window) — same fix applies.

**Risk**: DEGRADED — physics is wrong but does not crash; LLTD term
contributes uniform negative bias to all GT3 candidates.

**Recommended GT3 handling**: extend the `if _ferrari_controls / elif
GT3 / elif torsion / else roll-spring` branching as in F-O-3.

**Effort**: covered by F-O-3 fix.

---

### F-O-8 (DEGRADED): Pinned front RH and aero compression assumes GTP architecture

`solver/objective.py:957-973, 1758-1761`

```python
_static_f = car.pushrod.front_pinned_rh_mm
_rear_static = car.pushrod.rear_rh_for_offset(...)
_comp_f = car.aero_compression.front_at_speed(_op_speed)
_rear_comp = car.aero_compression.rear_at_speed(_op_speed)
_static_f = max(_static_f, car.min_front_rh_static)
_rear_static = max(_rear_static, car.min_rear_rh_static)
dyn_front_rh = max(5.0, _static_f - _comp_f)
```

**What it expects**: front static RH is a fixed pinned value (BMW/Ferrari/Acura)
or a model output (Porsche). `pushrod.front_pinned_rh_mm` is set per car.

**GT3 reality**: `BMW_M4_GT3.pushrod.front_pinned_rh_mm = 0.0`
(`car_model/cars.py:3290`) — explicitly NOT pinned. The clamp `max(_static_f,
car.min_front_rh_static)` then forces `_static_f = 50.0` (BMW M4 GT3 manual
floor). This collides with the actual driver-loaded static front RH (~72.6
mm IBT-measured) by ~22 mm, and `dyn_front_rh = max(5.0, 50.0 - 10.0) = 40
mm` — far below the actual 68 mm measured at speed.

The aero map is queried at `(40, dyn_rear)` instead of `(68, ~70)`, reading
DF-balance from a region far outside the GT3 operating window. Score is
systematically wrong for every GT3 candidate.

**Risk**: DEGRADED — physics evaluation does not crash but DF-balance and
stall-margin inputs are 25-30 mm off.

**Recommended GT3 handling**: For GT3, derive static front RH from the
garage-output model OR from the candidate's `front_corner_spring_nmm` +
pushrod offset directly. Until a GT3 `GarageOutputModel` is calibrated,
fall back to the IBT-measured driver-loaded values when available
(driver-anchor pattern, Key Principle 11). The existing
`_gom is not None and _front_pushrod_param is not None` block at line 936
is the right home — but `_gom` will be None for GT3 until calibration data
arrives.

Suggested interim:

```python
if car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
    # No pinned RH; use measured if available, else mid of static range.
    _static_f = (
        params.get("front_rh_static_mm")
        or (self._measured.front_rh_static_mm if self._measured else None)
        or (car.min_front_rh_static + car.max_front_rh_static) / 2.0
    )
    _rear_static = (
        params.get("rear_rh_static_mm")
        or (self._measured.rear_rh_static_mm if self._measured else None)
        or (car.min_rear_rh_static + car.max_rear_rh_static) / 2.0
    )
else:
    _static_f = car.pushrod.front_pinned_rh_mm
    _rear_static = car.pushrod.rear_rh_for_offset(...)
```

**Effort**: 3 hours including a GT3 IBT smoke test.

---

### F-O-9 (DEGRADED): `_compute_platform_risk()` uses BMW-derived 30 mm front RH floor

`solver/objective.py:1745-1763`

```python
# Every competitive GTP setup pins front RH at ≥ 30mm.
# Below 30mm: risk of vortex stall + underfloor contact on bumps.
# Below 25mm: hard veto (cannot race, unsafe aero stall).
FRONT_RH_FLOOR_MM = 30.0
FRONT_RH_FLOOR_PENALTY_MS_PER_MM = 25.0
```

**What it expects**: 30 mm is a universal GTP front-RH floor.

**GT3 reality**: GT3 manuals publish a higher static floor (50 mm BMW M4
GT3) and dynamic operating-point F 35 ±2.5 mm. The 30 mm constant is
GTP-specific and would mis-score GT3 candidates whose 35 mm dynamic RH
shows up as "above the floor by 5 mm" when it should be at the operating
target. Note: the constant is **defined but not actually consumed** in the
code (search shows it is never read again in the current file). Still
worth scrubbing.

**Risk**: DEGRADED (currently dormant) — code reader confusion; future
re-introduction would mis-score GT3.

**Recommended GT3 handling**: read from `car.min_front_rh_static` or
`car.vortex_burst_threshold_mm` instead of the hard-coded 30 mm. Or delete
the unused constant and comment.

**Effort**: 15 minutes.

---

### F-O-10 (DEGRADED): Sigma target hard-coded at 3.0 mm for GTP

`solver/objective.py:1864-1873`

```python
sigma_target = 3.0  # mm — typical GTP target
if physics.front_sigma_mm > sigma_target * 1.5:
    risk.rh_collapse_risk_ms = 50.0 * (physics.front_sigma_mm - sigma_target)
```

**What it expects**: 3 mm front-RH std-dev is the platform-collapse target.

**GT3 reality**: GT3 cars run softer corner springs and don't have a heave
spring isolating the floor — 3 mm is unrealistic. A coil-only setup would
need 6-10 mm σ to stay competitive (this matches the BMW M4 GT3 IBT-measured
amplitude implied by the 35 ±2.5 mm dynamic RH spec). Using the GTP target
flags every GT3 candidate as a platform-collapse risk.

**Risk**: DEGRADED — every GT3 candidate gets a uniform 50-200 ms
`rh_collapse_risk_ms` penalty that wipes out the lap-gain term.

**Recommended GT3 handling**: pull σ target from the scenario profile or
add `car.sigma_target_front_mm` / `car.sigma_target_rear_mm` fields to
`CarModel`. PENDING_IBT until GT3 telemetry is calibrated; for now use 8.0
mm and 10.0 mm (the same fallback constants `analyze_step2_constraints`
already uses, see F-S-2).

**Effort**: 1 hour.

---

### F-O-11 (DEGRADED): Damper LS reference velocity 25 mm/s assumes per-corner GTP

`solver/objective.py:1093-1115`

```python
v_ls_ref = 0.025  # 25 mm/s — LS reference velocity
c_ls_front = _c_eff_harmonic(
    params.get("front_ls_comp", f_ls_comp),
    params.get("front_ls_rbd", f_ls_rbd),
    damper.ls_force_per_click_n, v_ls_ref,
)
```

**What it expects**: LS reference velocity is the same for all cars.

**GT3 reality**: 25 mm/s is plausible for GT3 too (corner shock velocity
P95 is in the 50-100 mm/s range from the IBT data documented in
`gt3_session_info_schema.md`), but the click-magnitude assumptions vary
wildly (BMW 0-11, Acura 1-16, Audi 0-40, McLaren 0-50, Corvette 0-30).
`damper.ls_force_per_click_n` will be zero for uncalibrated GT3 cars,
producing zero damping coefficients → zero ζ → falls through scoring.

**Risk**: DEGRADED — damping ζ scoring contributes zero for GT3 until
`zeta_is_calibrated=True` and per-car force/click is set, which is correct
behavior (already gated at line 1368). No crash, just no damper signal.

**Recommended GT3 handling**: leave as-is for now. When per-GT3-car damper
calibration arrives, also add `damper.click_polarity` (`"higher_stiffer"`
vs `"lower_stiffer"` per `gt3_per_car_spec.md`) so the score doesn't flip
sign for Corvette/McLaren/Audi.

**Effort**: 0 (current); 4 hours when polarity dispatch is added.

---

### F-O-12 (DEGRADED): Diff preload default 30 Nm assumes mid-GTP value

`solver/objective.py:1490-1492, 1657-1659`

```python
diff = params.get("diff_preload_nm", 20.0)
diff_target = getattr(self.car, "default_diff_preload_nm", 30.0)
gain -= min(8.0, abs(diff - diff_target) * 0.12)
```

**What it expects**: `default_diff_preload_nm` is set per car.

**GT3 reality**: `BMW_M4_GT3.default_diff_preload_nm = 100.0`
(`car_model/cars.py:3286`), Aston/Porsche probably similar (110 Nm, 110 Nm from IBTs).
Code is GT3-correct as long as each GT3 car definition sets this field.
Verified: BMW M4 GT3 already does. Aston and Porsche stubs set it (see
their constructors). **GT3-correct as-is**.

The duplicate at line 1659 hard-codes `diff_target = 30.0` and ignores the
car attribute. Fix that path too.

**Risk**: DEGRADED — `_compute_lap_gain_breakdown()` uses 30 Nm hard-coded
instead of `self.car.default_diff_preload_nm`.

**Recommended GT3 handling**: replace line 1658 with
`diff_target = getattr(self.car, "default_diff_preload_nm", 30.0)` to match
line 1491. Same fix benefits all non-BMW cars, not just GT3.

**Effort**: 5 minutes.

---

### F-O-13 (DEGRADED): `_arb_size_index()` assumes ascending labels

`solver/objective.py:463-479, 1500-1514`

```python
@classmethod
def _arb_size_index(cls, raw, labels, baseline, *, default=0):
    if labels:
        label = cls._arb_size_label(raw, labels, baseline)
        if label in labels:
            return labels.index(label)
    ...
```

**What it expects**: `labels.index(label)` returns a stiffness-ordered index
(0 = softest, last = stiffest).

**GT3 reality**: For Corvette Z06 GT3.R, ARB labels are `0=stiff → 6=soft`
(INVERTED, see `gt3_per_car_spec.md`). `f_arb_size_idx=0` would mean
"stiffest" instead of "softest", inverting the LLTD direction in any
penalty computation.

**Risk**: DEGRADED — only blocks the Corvette path; BMW M4 GT3 / Aston /
Porsche use ascending labels. Fix when adding Corvette.

**Recommended GT3 handling**: add `arb.front_size_polarity` field
(`"ascending"` | `"descending"`) and translate before scoring. Out of
scope for the BMW M4 GT3 + Aston onboarding; required before Corvette /
McLaren / Audi.

**Effort**: 2 hours when those cars come online.

---

### F-O-14 (COSMETIC): Comments / hierarchy text reference "GTP heave platform"

`solver/objective.py:238, 1284-1300`

```python
f"  [hierarchy: rake/RH > heave_platform > LLTD(ARB) > dampers > camber]"
...
3. HEAVE / THIRD SPRINGS  ← aero platform stability, 300-800ms range
   GTP cars deliberately run stiff heave springs (40-120 N/mm) for platform
```

**What it expects**: prose describing GTP setup priority.

**GT3 reality**: For GT3 the hierarchy is rake/RH → corner springs (LLTD) →
dampers → camber. Heave-platform tier is N/A.

**Risk**: COSMETIC — output prose only, no scoring impact.

**Recommended GT3 handling**: dispatch the summary string on
`car.suspension_arch` or print "N/A" for the heave_platform tier.

**Effort**: 30 minutes.

---

### F-O-15 (COSMETIC): Module docstring describes GTP-only platform sigma calibration

`solver/objective.py:1-30`

```python
Platform sigma is computed from empirical IBT calibration when available
(data/learnings/heave_calibration_<car>_<track>.json), falling back to
a physics model.
```

**Risk**: COSMETIC — docstring drift; readers may think GT3 uses the same
sigma calibration files.

**Recommended GT3 handling**: append a note that GT3 cars don't use the
heave-spring σ calibration; they use a corner-spring σ model (TBD).

**Effort**: 5 minutes.

---

### F-O-16 (COSMETIC): Hardcoded `front_heave_spring_nmm` / `rear_third_spring_nmm` keys throughout

`solver/objective.py:786-789, 943-948, 1066-1067, 1396-1403, 1986-1987`

The key strings `"front_heave_spring_nmm"` and `"rear_third_spring_nmm"` are
repeated as `params.get(...)` lookups. For GT3 candidates these keys are
absent and the defaults (50.0 / 450.0 / 5 N/mm clamps) silently take over
— quiet bias.

**Risk**: COSMETIC — caught upstream by F-O-1..F-O-5 once those are guarded.

**Recommended GT3 handling**: introduce a `GT3_PARAM_KEYS` constant
(or `car.suspension_arch`-driven dispatch) and refactor when GT3 search
parameters are wired up in `solver/candidate_search.py`.

**Effort**: covered as part of GT3 candidate-search Phase 2 work.

---

## Findings — `solver/sensitivity.py`

### F-S-1 (BLOCKER): `analyze_step2_constraints()` reads non-applicable HeaveSolution fields

`solver/sensitivity.py:198-308`

```python
def analyze_step2_constraints(step2, sigma_target_front_mm=None, ...):
    ...
    slack = step2.front_bottoming_margin_mm
    limit = step2.front_dynamic_rh_mm
    slack_pct = (slack / limit * 100) if limit > 0 else 0
    constraints.append(ConstraintProximity(
        name="Front bottoming margin",
        ...
        binding_explanation=("Stiffen front heave or raise front dynamic RH." if slack_pct < 10 else ""),
    ))
```

**What it expects**: `step2` is a populated `HeaveSolution` carrying real
front/rear bottoming margins, dynamic RH, σ at rate, and binding-constraint
labels.

**GT3 reality**: `HeaveSolution.null()` returns zero for every numeric
field and `front_binding_constraint = "not_applicable"`. Computing
`slack_pct` on `front_bottoming_margin_mm = 0.0` and `front_dynamic_rh_mm =
0.0` falls into the `if limit > 0 else 0` branch → `slack_pct = 0` →
classified BINDING. The report prints four spurious BINDING constraints
("Front bottoming margin 0.0/0.0 mm", σ "0.0 / 8.0 mm BINDING") and an
explanation suggesting "stiffen front heave" — directly violating Key
Principle 7 ("Calibrated or instruct, never guess").

**Risk**: BLOCKER — output text is wrong and misleading.

**Recommended GT3 handling**: skip the entire function body using the
`present` flag added in Phase 0 (`solver/heave_solver.py:103-110`):

```python
def analyze_step2_constraints(step2, ...):
    if not step2.present:
        return []  # GT3: no Step-2 constraints to report
    ...
```

The `present` flag was added on `HeaveSolution` (`solver/heave_solver.py:103-110`)
specifically for this guard — use it.

**Effort**: 15 minutes.

---

### F-S-2 (DEGRADED): Default σ targets 8.0 / 10.0 mm are BMW/Sebring values

`solver/sensitivity.py:222-231`

```python
if sigma_target_front_mm is None:
    sigma_target_front_mm = (
        step2.front_sigma_target_mm if step2.front_sigma_target_mm > 0 else 8.0
    )
if sigma_target_rear_mm is None:
    sigma_target_rear_mm = (
        step2.rear_sigma_target_mm if step2.rear_sigma_target_mm > 0 else 10.0
    )
```

**Risk**: DEGRADED — won't be hit if F-S-1 fix returns early, but if
sensitivity is repurposed for GT3 a different default is needed.

**Recommended GT3 handling**: same as F-O-10 — read from `car` or scenario.

**Effort**: covered by F-S-1.

---

### F-S-3 (DEGRADED): `compute_heave_sensitivities()` is GTP-physics

`solver/sensitivity.py:313-361, 555-562`

```python
def compute_heave_sensitivities(v_p99_front_mps, v_p99_rear_mps, m_eff_front_kg,
                                 m_eff_rear_kg, k_front_nmm, k_rear_nmm):
    """excursion = v_p99 * sqrt(m_eff / k)
       ∂excursion/∂k = -0.5 * v_p99 * sqrt(m_eff) * k^(-3/2)
    """
    ...
```

Called by `build_sensitivity_report()` with `step2.front_heave_nmm` and
`step2.rear_third_nmm` (both 0.0 on GT3 null). With `k=0`, the expression
`k^(-3/2)` divides by zero → `inf` derivative → garbage interpretation
text.

**Risk**: DEGRADED — math overflow, NaN/Inf in reports.

**Recommended GT3 handling**: short-circuit in `build_sensitivity_report`:

```python
if step2.present:
    report.sensitivities.extend(compute_heave_sensitivities(...))
    # confidence bands too
```

For GT3, add a different sensitivity function `compute_corner_sensitivities`
that takes corner-spring rate instead of heave rate (Phase 3 follow-on).

**Effort**: 30 minutes for the guard; 4 hours for GT3 corner-sensitivity
function.

---

### F-S-4 (DEGRADED): `compute_heave_confidence()` reads from null `HeaveSolution`

`solver/sensitivity.py:404-447, 578-591`

Same `k=0` math overflow as F-S-3. Confidence-band output for GT3 is
garbage.

**Risk**: DEGRADED — print-only; no scoring impact, but reader-confusing.

**Recommended GT3 handling**: covered by F-S-3 guard.

**Effort**: 0 (covered).

---

### F-S-5 (DEGRADED): `build_sensitivity_report()` reads `car.heave_spring`

`solver/sensitivity.py:548-554`

```python
if car is not None:
    _m_eff_front = float(car.heave_spring.front_m_eff_kg)
    _m_eff_rear = float(car.heave_spring.rear_m_eff_kg)
```

**What it expects**: `car.heave_spring` is non-null.

**GT3 reality**: AttributeError on every GT3 call.

**Risk**: DEGRADED (would be BLOCKER if not for F-S-3 guard short-circuiting
the section). Once F-S-3 is in, this code is no longer reached for GT3.
Belt-and-suspenders fix:

```python
if car is not None and car.heave_spring is not None:
    ...
```

**Effort**: 5 minutes.

---

### F-S-6 (COSMETIC): Comment "BMW Sebring validation" on `INPUT_UNCERTAINTIES`

`solver/sensitivity.py:392-401`

```python
# Input uncertainty assumptions (calibrated from BMW Sebring validation)
INPUT_UNCERTAINTIES = { ... }
```

**Risk**: COSMETIC — drift documentation only; values may need re-tuning
for GT3 once IBT data exists.

**Effort**: revisit during Phase 4 GT3 calibration.

---

## Findings — `solver/laptime_sensitivity.py`

### F-LT-1 (BLOCKER): `_front_heave_sensitivity()` and `_rear_third_sensitivity()` are heave-only

`solver/laptime_sensitivity.py:394-447, 595-625`

Both functions read `step2.front_heave_nmm`, `step2.rear_third_nmm`,
`step2.front_excursion_at_rate_mm`, `step2.front_bottoming_margin_mm`. On
GT3 (`HeaveSolution.null()`) every field is 0.0 and the reports emit
"Front heave 0 N/mm maintains 0.0mm bottoming margin..." — semantically
wrong but won't crash.

The aggregator at `compute_laptime_sensitivity()` line 1384-1385
unconditionally calls both functions.

**Risk**: BLOCKER — every GT3 lap-time-sensitivity report shows fake
heave/third entries, which will mislead users and pollute any downstream
ranking by `top_n()`.

**Recommended GT3 handling**: add a `step2.present` guard at line 1384:

```python
sensitivities = [
    _rear_rh_sensitivity(step1, track, measured),
    _front_rh_sensitivity(step1, track, measured),
    _wing_angle_sensitivity(wing, track),
    *([_front_heave_sensitivity(step2, track, measured),
       _rear_third_sensitivity(step2, track, measured)] if step2.present else []),
    *(
        [_front_corner_spring_sensitivity(step3, step4, track)]
        if not step2.present
        else ([_front_roll_spring_sensitivity(...)]
              if (step3.front_torsion_od_mm == 0.0 and step3.front_roll_spring_nmm > 0)
              else [_torsion_bar_sensitivity(...), _torsion_turns_sensitivity(...)])
    ),
    ...
]
```

A new `_front_corner_spring_sensitivity()` function for GT3 cars (15-40
ms / 10 N/mm front coil rate, similar weight to torsion bar OD) is the
right replacement.

**Effort**: 2 hours.

---

### F-LT-2 (BLOCKER): `_heave_perch_sensitivity()` and `_rear_third_perch_sensitivity()` similarly

`solver/laptime_sensitivity.py:889-933, 1395-1396`

Same pattern — both call `step2.perch_offset_front_mm`,
`step2.slider_static_front_mm`, etc. Returns "front heave perch +0.0 mm"
with `delta_per_unit_ms = -5.0`, polluting the sensitivity table.

**Risk**: BLOCKER — fake perch entries with non-zero ms appear in `top_n()`
ranking.

**Recommended GT3 handling**: gate these on `step2.present`, same as F-LT-1.

**Effort**: covered by F-LT-1.

---

### F-LT-3 (DEGRADED): `_torsion_bar_sensitivity()` runs the OD^4 formula

`solver/laptime_sensitivity.py:450-490`

```python
wheel_rate_per_od_01 = 0.86  # N/mm per 0.1mm OD change
```

Hard-coded BMW C and OD constants. If `step3.front_torsion_od_mm == 0.0`
(GT3), the dispatch at line 1389 already routes to
`_front_roll_spring_sensitivity` instead — but that path is for Porsche 963.
GT3 cars don't have a roll spring either; `step3.front_roll_spring_nmm` is
0.0 → sensitivity entry returns 0 ms.

**Risk**: DEGRADED — `step3.front_roll_spring_nmm > 0` check fails, so the
function falls through to `_torsion_bar_sensitivity` (the `else` branch in
line 1390-1391) which then divides by zero / uses the BMW constants on the
GT3 zeros. Output reads "Torsion OD 0.00mm sets front wheel rate to 0.0
N/mm".

**Recommended GT3 handling**: add a third arm to the conditional at
line 1387-1392:

```python
*(
    [_front_corner_spring_sensitivity(step3, step4, track)]
    if car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL  # or step3.front_corner_spring_nmm > 0
    else (
        [_front_roll_spring_sensitivity(...)]
        if (step3.front_torsion_od_mm == 0.0 and step3.front_roll_spring_nmm > 0)
        else [_torsion_bar_sensitivity(...), _torsion_turns_sensitivity(...)]
    )
),
```

`compute_laptime_sensitivity` does not currently take `car` as a parameter
— add it. Or pass `step3.suspension_arch` once Step 3 carries that info.

**Effort**: 2 hours.

---

### F-LT-4 (DEGRADED): `_torsion_turns_sensitivity()` reads `step3.torsion_bar_turns`

`solver/laptime_sensitivity.py:1316-1336`

```python
turns = getattr(step3, "torsion_bar_turns", 0.0)
```

GT3: returns 0.0; output prints "Torsion turns 0.000". Cosmetic noise only,
filtered out by F-LT-3 dispatch fix (Ferrari-only path).

**Risk**: DEGRADED — won't fire after F-LT-3 fix.

**Effort**: covered.

---

### F-LT-5 (DEGRADED): Damper iteration assumes per-corner

`solver/laptime_sensitivity.py:1435-1462`

```python
for corner_label, corner in [("Front", step6.lf), ("Rear", step6.lr)]:
    prefix = "front" if corner_label == "Front" else "rear"
    ...
```

**What it expects**: per-corner `DamperSolution` — picks LF as a stand-in
for the front axle, LR for rear. Even on GTP, this **silently discards**
RF and RR settings because the sensitivity is the same for paired corners.

**GT3 reality**: GT3 IS per-axle (`gt3_session_info_schema.md`:74-85).
Picking `step6.lf` is technically correct (LF and RF will hold identical
values once damper_solver returns axle-symmetric output for GT3). However,
the per-corner `DamperSolution` shape itself is the artefact — a downstream
audit (damper_solver) should add an `axle_paired: bool` flag.

**Risk**: DEGRADED — semantically OK for GT3 with axle-symmetric output;
fragile if damper_solver later starts emitting asymmetric L/R values for
some GT3 quirk.

**Recommended GT3 handling**: when GT3 dispatch is added to damper_solver,
make it explicit that LF==RF and LR==RR. Add an assertion in the audit's
final implementation, or change the iteration to pull explicitly from a
per-axle accessor (`step6.front_axle_dampers` for GT3).

**Effort**: 1 hour, deferred until damper_solver GT3 work.

---

### F-LT-6 (DEGRADED): `_damper_sensitivity()` ζ targets are GTP-numbered

`solver/laptime_sensitivity.py:1206`

```python
telemetry_evidence=f"ζ = {zeta:.2f} (target: {'0.55-0.70' if regime == 'LS' else '0.25-0.40'})",
```

Hard-coded ζ ranges (0.55-0.70 LS, 0.25-0.40 HS) come from BMW IBT.

**Risk**: DEGRADED — display string only; GT3 ζ targets PENDING_IBT.

**Recommended GT3 handling**: read from `car.damper.zeta_target_*` once
GT3 dampers are calibrated. For now, leave a comment noting BMW origin.

**Effort**: 30 minutes.

---

### F-LT-7 (DEGRADED): Constants block is GTP-specific

`solver/laptime_sensitivity.py:36-77`

```python
GTP_MASS_KG = 1050.0
RH_DF_SENSITIVITY_N_PER_MM = 0.5
FRONT_RH_DF_SENSITIVITY_N_PER_MM = 1.2
HEAVE_MS_PER_10NMM = 35.0
TORSION_MS_PER_MM_OD = 25.0
ARB_BLADE_MS_PER_CLICK = 10.0
```

`GTP_MASS_KG` is unused in the audit-relevant codepaths but documents
the calibration assumptions. `HEAVE_MS_PER_10NMM` is used by
`_front_heave_sensitivity()` and `_rear_third_sensitivity()`, which are
gated by F-LT-1.

**Risk**: DEGRADED — values may not transfer to GT3.

**Recommended GT3 handling**: add a `CORNER_SPRING_MS_PER_10NMM` constant
when implementing the GT3-specific
`_front_corner_spring_sensitivity()` (F-LT-1).

**Effort**: covered by F-LT-1.

---

### F-LT-8 (DEGRADED): `_rear_spring_sensitivity()` reuses BMW LLTD coefficient

`solver/laptime_sensitivity.py:628-653`

```python
lltd_per_10nmm = 0.003 * 10  # approx fraction LLTD per 10 N/mm rear wheel rate
```

The 0.003 factor is BMW-derived. For GT3 with no torsion bar, the
front/rear coil-spring rate ratio drives LLTD differently. Output value
will be in the right ballpark (LLTD physics is universal) but uncertain.

**Risk**: DEGRADED — magnitude approximate; sign correct.

**Recommended GT3 handling**: parameterize on `car.weight_dist_front` and
the actual front wheel rate from Step 3.

**Effort**: 1 hour.

---

### F-LT-9 (DEGRADED): `_front_arb_blade_sensitivity()` reads `step4.front_arb_blade_start`

`solver/laptime_sensitivity.py:704-727`

```python
current_value=float(step4.front_arb_blade_start),
```

GT3 ARB encoding varies (paired blade D-codes for BMW M4 GT3, single
ascending for Acura/McLaren, single descending for Corvette). The
`front_arb_blade_start` field is BMW-shape (single integer 1-5). GT3 BMW
stub stores ARB labels like `"D3-D3"`, not blades — `arb.front_blade_count
= 1` so the blade index is always 1.

**Risk**: DEGRADED — sensitivity output reads "1 FARB blade -> ~0.6%
LLTD" which is sane direction but wrong granularity for GT3 paired
labels.

**Recommended GT3 handling**: change parameter name from "blade" to
"setting" and read `step4.front_arb_size_idx` (the discrete label index)
instead of blade count. Same for `_rear_arb_blade`.

**Effort**: 1 hour, with ARB-encoding GT3 work.

---

### F-LT-10 (COSMETIC): `_wing_angle_sensitivity()` median-speed fallback

`solver/laptime_sensitivity.py:864-870`

`median_speed = track.median_speed_kph or 160.0` — 160 kph fallback
matches GT3 race-pace (well, Porsche 963 is more like 200). Marginal.

**Risk**: COSMETIC.

**Effort**: ignore.

---

### F-LT-11 (COSMETIC): Function docstrings reference "torsion bar" / "third spring"

`solver/laptime_sensitivity.py:599-625, 889-911`

Prose mentions "rear third spring" and "torsion bar" specifically. After
F-LT-1 / F-LT-3 fixes the GT3 path won't hit these functions, but the
remaining GTP path docstrings are still correct.

**Risk**: COSMETIC — docstring drift only.

**Effort**: minor cleanup with overall GT3 work.

---

## Findings — `solver/full_setup_optimizer.py`

### F-FSO-1 (COSMETIC): File is BMW/Sebring-only and explicitly gated

`solver/full_setup_optimizer.py:95-99, 565-566`

```python
def _is_bmw_sebring(car: Any, track: Any) -> bool:
    return (
        getattr(car, "canonical_name", "").lower() == "bmw"
        and "sebring" in getattr(track, "track_name", "").lower()
    )
...
def optimize_if_supported(...):
    if legacy_solver or not _is_bmw_sebring(car, track):
        return None
```

**What it expects**: explicit BMW M Hybrid V8 + Sebring; everything else
returns None.

**GT3 reality**: GT3 cars never enter this code; `optimize_if_supported`
is a documented no-op for them.

**Risk**: COSMETIC — no GT3 impact at all. The seed dataclass
`BMWSebringSeed` (`solver/full_setup_optimizer.py:37-49`) and the SLSQP objective
(`_optimize_continuous_state`, lines 211-295) are heave/third/torsion-OD
shaped, which is correct for the BMW M Hybrid V8.

**Recommended GT3 handling**: when GT3 wants a similar constrained
optimizer it should be a NEW class
(`BMWM4GT3SpielbergOptimizer` etc.), not a refactor of this file. Pattern
this one provides is fine.

**Effort**: 0.

---

## Risk summary

| Category | Count | Notes |
|---|---:|---|
| BLOCKER | 8 | All in `solver/objective.py` and the two sensitivity files; concentrated on heave-spring null pointers and Step 2 fields. Each is a hard crash on the first GT3 candidate. |
| DEGRADED | 19 | Mostly GTP-physics constants and BMW-derived defaults applied to GT3; many are masked by guards once the BLOCKERs are fixed. |
| COSMETIC | 7 | Comment / prose drift. |

The objective and sensitivity codepaths are the single largest GT3-readiness
gap in the solver: they are unconditionally heave/third-shaped and crash on
any GT3 input. Without BLOCKER fixes F-O-1, F-O-2, F-O-4, F-O-5, F-S-1, F-LT-1,
F-LT-2, F-O-3, the very first GT3 evaluation aborts before producing a setup.

## Effort estimate

| Phase | Findings | Engineer-hours |
|---|---|---:|
| **Phase 2a** — unblock GT3 evaluation | F-O-1, F-O-2, F-O-3, F-O-4, F-O-5, F-S-1, F-LT-1, F-LT-2 | **12** |
| **Phase 2b** — physics correctness | F-O-7, F-O-8, F-O-10, F-LT-3, F-LT-8, F-S-3, F-S-5 | **10** |
| **Phase 2c** — multi-GT3 dispatch | F-O-13 (Corvette ARB polarity), F-O-11 (damper polarity), F-LT-9 (ARB encoding) | **8** |
| **Phase 2d** — cleanup | F-O-6, F-O-9, F-O-12, F-O-14..16, F-S-2, F-S-4, F-S-6, F-LT-4..7, F-LT-10..11, F-FSO-1 | **4** |
| **Total** | 34 | **~34 hours** |

Phase 2a alone is enough to make the BMW M4 GT3 + Aston Vantage smoke
test runnable end-to-end (per `gt3_session_info_schema.md` line 198 "End-to-end
smoke test"). Phase 2b makes scoring meaningful. Phase 2c gates the
remaining 9 GT3 cars on the grid.

## Dependencies

This audit consumes:

- `car_model/cars.py:24-65` — `SuspensionArchitecture` enum and
  `has_heave_third` helper (Phase 0)
- `car_model/cars.py:1745-1772` — `__post_init__` invariants enforcing
  `heave_spring=None` for GT3
- `solver/heave_solver.py:113-143` — `HeaveSolution.null()` and the
  `present` flag (Phase 0)
- `solver/damper_solver.py:114-189` — per-corner `DamperSolution` shape
  (unchanged)
- `docs/gt3_per_car_spec.md` — manual-derived spring rate ranges, ARB
  encodings, damper polarity tables
- `docs/gt3_session_info_schema.md` — IBT YAML schema, per-axle damper
  finding, Spielberg driver-loaded values for BMW M4 GT3 / Aston / Porsche

This audit unblocks (downstream Phase 2 work):

- GT3 candidate generation (`solver/candidate_search.py`) — needs the
  `front_corner_spring_nmm` / `rear_spring_rate_nmm` param keys to be
  the relevant axes for GT3 cars
- GT3 setup_writer dispatch (`output/setup_writer.py`) — out of scope here
  but the per-car YAML field names from `gt3_session_info_schema.md`
  are already documented
- GT3 calibration onboarding (`car_model/auto_calibrate.py`) — F-O-1 /
  F-S-3 fixes provide a clean physics path that GT3 IBT regressions can
  populate

Authoritative target: BMW M4 GT3 EVO + Spielberg/Red Bull Ring. Five
calibrated GT3 cars (BMW, Aston, Ferrari, Lambo, McLaren) before the
remaining six (Porsche 992, Acura NSX, Audi R8, Mustang, Corvette).
