# GT3 Phase 2 Audit — Solver Rake / Corner Spring / ARB / Diff / Supporting

**Audit date**: 2026-04-26
**Branch**: `gt3-audit-solver-rake-corner-arb` (off `gt3-phase0-foundations` HEAD `8eaa343`)
**Scope**: `solver/rake_solver.py`, `solver/corner_spring_solver.py`, `solver/arb_solver.py`, `solver/diff_solver.py`, `solver/supporting_solver.py`
**Worker**: Phase-2 audit unit (rake / corner / ARB)

## Scope

This audit evaluates whether Steps 1, 3, 4 of the solver chain — plus the diff and supporting-parameter helpers — can run end-to-end against the three GT3 stubs (`BMW_M4_GT3`, `ASTON_MARTIN_VANTAGE_GT3`, `PORSCHE_992_GT3R`) without hitting GTP-only assumptions. The GT3 architecture (`SuspensionArchitecture.GT3_COIL_4WHEEL`) implies:

- `heave_spring=None`, no torsion-bar front (`front_torsion_c == 0.0`), no roll-spring front (`front_roll_spring_range_nmm == (0.0, 0.0)`).
- Step 2 returns `HeaveSolution.null()` with `present=False` and `front_heave_nmm = rear_third_nmm = 0.0`.
- Aero map is balance-only: `lift_drag()` returns NaN at every grid point (parser stores `np.nan`).
- ARB encodings differ per car: BMW paired-blade D-codes, Aston single-blade B-codes (count PENDING), Porsche single integer setting.
- `weight_dist_front` ranges 0.449 (Porsche RR) — 0.480 (Aston FR) — 0.464 (BMW), so any LLTD logic that assumed ~0.50 ± a few pp will mis-target the Porsche by 5+ pp.

Audit looked for: aero-map L/D dependencies, front torsion-bar physics, LLTD physics that assume specific weight distribution, hard-coded ARB encoding (paired vs single, ascending vs descending), Step-3 reads of `step2.front_heave_nmm` without `step2.present` guards, per-corner damper assumptions, missing `suspension_arch` branches, and tests that hard-code BMW / GTP cars.

## Summary table

| ID | File:line | Severity | Subsystem | Triggering car | One-line |
|---|---|---|---|---|---|
| R-1 | `solver/rake_solver.py:333` | BLOCKER | Step 1 | All 3 GT3 | `self.car.heave_spring.perch_offset_front_baseline_mm` — `heave_spring is None` → AttributeError |
| R-2 | `solver/rake_solver.py:358` | BLOCKER | Step 1 | All 3 GT3 | `self.car.front_heave_spring_nmm` — derived from `heave_spring` → AttributeError |
| R-3 | `solver/rake_solver.py:332` | BLOCKER | Step 1 | All 3 GT3 | `self.car.rear_third_spring_nmm` — derived from `heave_spring` → AttributeError |
| R-4 | `solver/rake_solver.py:730-734` | BLOCKER | Step 1 | All 3 GT3 | `_solve_pinned_front` re-reads `self.car.heave_spring.perch_offset_rear_baseline_mm` etc. when garage_model is None → AttributeError |
| R-5 | `solver/rake_solver.py:194-197` | BLOCKER | Step 1 | All 3 GT3 | `self.surface.lift_drag(...)` returns NaN on balance-only maps; `_query_aero` returns NaN L/D into Solution |
| R-6 | `solver/rake_solver.py:850-872` | BLOCKER | Step 1 | All 3 GT3 | `_find_free_max_ld` does `if ld > best_ld` against NaN — always False, returns 0.0 silently → meaningless `ld_cost_of_pinning` |
| R-7 | `solver/rake_solver.py:265-268, 322-323` | DEGRADED | Step 1 | All 3 GT3 | Front static RH "pin to min" methodology is GTP-specific (ground-effect floor); GT3 do not pin front and have no vortex floor |
| R-8 | `solver/rake_solver.py:443, 449` | DEGRADED | Step 1 | All 3 GT3 | `vortex_burst_threshold_mm` is GTP physics; for GT3 it is set to placeholder `2.0` (`PENDING_IBT`) but `_solve_pinned_front` enforces it as a hard floor |
| R-9 | `solver/rake_solver.py:1122-1267` | BLOCKER | Step 1 | All 3 GT3 | `reconcile_ride_heights` non-`garage_model` path reads `step2.front_heave_nmm`, `car.heave_spring.perch_offset_front_baseline_mm`, etc. — all crash for GT3 null Step 2 |
| R-10 | `solver/rake_solver.py:912-933` | DEGRADED | Step 1 | All 3 GT3 | `reconcile_ride_heights` garage-model path reads `step2.rear_third_nmm` (==0) into `garage_model.rear_pushrod_for_static_rh()` — pollutes prediction with zero spring rate |
| C-1 | `solver/corner_spring_solver.py:333-338` | BLOCKER | Step 3 | All 3 GT3 | `front_max_for_ratio = front_heave_nmm / ratio_lo` — when Step 2 is null, `front_heave_nmm=0` so the front-rate clamp collapses to 0 |
| C-2 | `solver/corner_spring_solver.py:341-371` | BLOCKER | Step 3 | All 3 GT3 | Front branching is `front_torsion_c > 0` (torsion) / `front_roll_spring_range_nmm[1] > 0` (Porsche GTP) / else `front_od=0.0` — GT3 falls into else and emits `front_od=0.0, front_rate=0` (no GT3 path) |
| C-3 | `solver/corner_spring_solver.py:378-441` | BLOCKER | Step 3 | All 3 GT3 | Rear branch uses `rear_target_rate = rear_third_nmm / rear_target_ratio` — `rear_third_nmm=0` for GT3 → `rear_target_rate=0` → snapped to `rear_spring_range_nmm[0]` (130 N/mm BMW) regardless of physics |
| C-4 | `solver/corner_spring_solver.py:399-401` | BLOCKER | Step 3 | All 3 GT3 | `_physics_rear_rate = rear_third_nmm / _physics_ratio` then `abs(driver - 0)/0` raises ZeroDivisionError when driver-anchor branch evaluates the relative gap |
| C-5 | `solver/corner_spring_solver.py:490-495` | BLOCKER | Step 3 | All 3 GT3 | `solution_from_explicit_rates` enters roll-spring branch via `front_torsion_c == 0.0`, then falls back to `csm.front_roll_spring_rate_nmm` (0.0 for GT3) — no GT3 front-rate path |
| C-6 | `solver/corner_spring_solver.py:516-517` | BLOCKER | Step 3 | All 3 GT3 | `total_front_heave = front_heave_nmm + 2 * front_rate` — when `front_heave_nmm=0` (null Step 2) the "total heave" reported is just `2*corner` and the Step-3 reasoning text claims a heave spring exists |
| C-7 | `solver/corner_spring_solver.py:728-827` | BLOCKER | Step 3 | All 3 GT3 | `_apply_lltd_floor` is gated on roll-spring car at line 351 — but uses `csm.front_roll_spring_installation_ratio` (0.882, defaults to Porsche GTP); incompatible with GT3 paired front coils |
| C-8 | `solver/corner_spring_solver.py:351-368, 444-454` | DEGRADED | Step 3 | BMW M4 GT3, Aston, Porsche 992 GT3 R | Rear motion ratio is `1.0` for all three GT3 stubs (`PENDING_IBT`); `rear_wheel_rate_nmm` therefore equals raw spring rate. The ARB / damper / objective stack downstream depends on this MR being calibrated |
| C-9 | `solver/corner_spring_solver.py:642-654` | DEGRADED | Step 3 | All 3 GT3 | `solve_candidates` enumerates `csm.front_torsion_od_options` (empty for GT3) OR `front_roll_spring_range_nmm` (zero for GT3) — falls through to `front_ods = [base.front_torsion_od_mm]` (single value 0.0); GT3 needs an enumeration over the front coil spring range (`front_spring_range_nmm`, currently absent from `CornerSpringModel`) |
| C-10 | `solver/corner_spring_solver.py:115-116, 177-181` | DEGRADED | Step 3 | All 3 GT3 | `CornerSpringSolution.front_torsion_od_mm` is the ONLY front-rate attribute exported. GT3 emits `0.0` for it and no field carries the front coil rate (the dataclass has no `front_spring_rate_nmm`) |
| C-11 | `solver/corner_spring_solver.py:553-564` | COSMETIC | Step 3 | n/a | Ferrari preload-turns hook is gated on canonical name; harmless but the gate pattern hard-codes one car — when GT3 cars get their own per-car logic, refactor to a registry/dispatch |
| A-1 | `solver/arb_solver.py:319-328` | BLOCKER | Step 4 | Porsche 992 GT3 R | LLTD physics formula `weight_dist_front + (tyre_sens/0.20)*(0.05+hs_correction)` produces 0.449+0.05 = ~0.50 for Porsche, but the OptimumG +5% RULE was VALIDATED for FR/MR cars (W_f ~0.50). For RR (W_f 0.449) the empirically-correct LLTD is closer to 0.43 (per `docs/gt3_per_car_spec.md` line 148) — applying +5pp pushes the rear ARB to over-loaded |
| A-2 | `solver/arb_solver.py:278-289` | BLOCKER | Step 4 | All 3 GT3 | Front spring roll-stiffness branch: `if csm.front_is_roll_spring` (Porsche GTP) else `_corner_spring_roll_stiffness(...)` (paired). GT3 has paired front coils → `else` branch is correct in spirit, but `front_wheel_rate_nmm` is computed from `step3.front_wheel_rate_nmm` which for GT3 is unset/zero (see C-2). Cascade BLOCKER from Step 3 |
| A-3 | `solver/arb_solver.py:269, 350-394` | BLOCKER | Step 4 | All 3 GT3 | Rear-search loop assumes `rear_blade_count >= 1` is the live tuning variable. GT3 ARB encoding is `front_blade_count=1, rear_blade_count=1` in all three stubs — the variable IS the size label, not the blade. The loop `for blade in range(1, arb.rear_blade_count + 1)` only iterates once. Needs to enumerate `rear_size_labels` instead |
| A-4 | `solver/arb_solver.py:351, 354` | BLOCKER | Step 4 | All 3 GT3 | `farb_blade = arb.front_baseline_blade` and `best_blade = arb.rear_baseline_blade` — both are `1` in every GT3 stub (since `front_blade_count=1`). Output of solve() will always be `front_blade=1, rear_blade=1`, never tuning the ARB at all |
| A-5 | `solver/arb_solver.py:441-442` | BLOCKER | Step 4 | All 3 GT3 | `rarb_slow_blade = 1`, `rarb_fast_blade = min(4, arb.rear_blade_count)`. With `rear_blade_count=1` for GT3, both end up 1 — no live tuning range. The "live RARB strategy" reasoning text in `summary()` becomes nonsensical |
| A-6 | `solver/arb_solver.py:431-437` | BLOCKER | Step 4 | All 3 GT3 | RARB sensitivity uses `min(best_blade + 1, arb.rear_blade_count)` and `max(best_blade - 1, 1)` — for GT3 `rear_blade_count=1` so both clamp to 1, sensitivity becomes 0/2 → 0; constraint check at line 472 will FAIL |
| A-7 | `solver/arb_solver.py:280-289, 600-607, 671-679` | DEGRADED | Step 4 | Porsche 992 GT3 R only | Three separate copies of the front-roll-spring vs paired-front roll-stiffness branch (`solve`, `solve_candidates`, `solution_from_explicit_settings`). Hard to evolve when GT3 needs a fourth arm |
| A-8 | `solver/arb_solver.py:482-521` | COSMETIC | Step 4 | All 3 GT3 | `car_specific_notes` branches on `bmw`/`ferrari`/`acura` canonical names; falls into the generic `else` for GT3. Missing GT3 guidance |
| A-9 | `solver/corner_spring_solver.py:776-783` | BLOCKER | Step 4 | Porsche 992 GT3 R, BMW M4 GT3 paired | `arb.blade_factor(arb.rear_blade_count, arb.rear_blade_count)` is called as a property of `ARBModel` (referenced from `solver/corner_spring_solver.py` line 780) — paired-blade GT3 ARBs need a different stiffness function (each label is the WHOLE config, not blade-size pair) |
| D-1 | `solver/diff_solver.py:34-48` | DEGRADED | Diff | All 3 GT3 | Module-level constants `CLUTCH_TORQUE_PER_PLATE=45`, `COAST_RAMP_OPTIONS=[40,45,50]`, `DRIVE_RAMP_OPTIONS=[65,70,75]` are BMW-GTP values. The IBT YAML for BMW M4 GT3 / Aston / Porsche 992 GT3 R shows `FrictionFaces: 8/10` and `DiffPreload: 100/110/110 Nm`. Ramp option set may differ; per-car overrides exist via `getattr(car, ...)` but defaults leak |
| D-2 | `solver/diff_solver.py:38-40` | DEGRADED | Diff | All 3 GT3 | `DEFAULT_MAX_TORQUE_NM = 700.0` (BMW M8 ~700). GT3 cars are 500-535 bhp / 580-664 Nm — closer to 600 Nm. Affects `lock_pct` denominator → over-reports lock % |
| D-3 | `solver/diff_solver.py:225-234` | DEGRADED | Diff | All 3 GT3 | Driver-anchor uses `current_coast_ramp_deg in COAST_RAMP_OPTIONS` (the BMW set). If a GT3 car exposes a different ramp option set the anchor silently fails to fire |
| D-4 | `solver/diff_solver.py:262-279` | COSMETIC | Diff | All 3 GT3 | `BMW_DEFAULT_CLUTCH_PLATES = 6` used as fallback when `car.default_clutch_plates` not set. BMW M4 GT3 IBT shows 8 plates as driver-loaded — GT3 stubs do not set `default_clutch_plates` |
| S-1 | `solver/supporting_solver.py:415` | DEGRADED | Supporting | All 3 GT3 | `sol.diff_clutch_plates = self.car.garage_ranges.diff_clutch_plates_options[-1]` — assumes `diff_clutch_plates_options` exists on GarageRanges; not set on GT3 stubs (would AttributeError when fallback runs) |
| S-2 | `solver/supporting_solver.py:389-414` | DEGRADED | Supporting | All 3 GT3 | Fallback `diff_coast_drive_ramp_options=[(40,65),(45,70),(50,75)]` — same BMW assumption as D-1 |
| S-3 | `solver/supporting_solver.py:483-484, 492-499` | DEGRADED | Supporting | Aston, Porsche 992 GT3 R, Corvette | TC clamp `int(_clamp(tc_gain, 1, 10))` and `tc_slip` — Acura NSX GT3 has TC range 1–12, Audi/Corvette use different off-position polarity (12=off, 0=off). For BMW M4 GT3 (TC 10 positions per manual) the upper bound 10 is fine; for Aston GT3 (TC 12) clamp truncates legal values 11, 12 |
| S-4 | `solver/supporting_solver.py:530-534` | DEGRADED | Supporting | All 3 GT3 | Tyre cold target `default_cold = 152.0` and hot window `155-170 kPa` — IBT YAML for all three GT3s shows cold 159 kPa loaded by drivers; GT3 minimum cold may differ. Currently a soft default but not GT3-specific |
| T-1 | `tests/test_bmw_rotation_search.py:7,217` | DEGRADED | Tests | n/a | `get_car("bmw")` is the only fixture for the rotation-search / sequential-solver chain; no GT3 fixture exists |

## Findings

### R-1 (BLOCKER, Step 1) — `solver/rake_solver.py:333`

```python
baseline_heave_perch = self.car.heave_spring.perch_offset_front_baseline_mm
```

**Expectation**: GT3 cars have `heave_spring=None`. Reading `.perch_offset_front_baseline_mm` raises `AttributeError: 'NoneType' object has no attribute …`. Step 1 cannot complete for any GT3 car when the calibrated `RideHeightModel` is fed (current model is `RideHeightModel.uncalibrated()` for GT3 stubs, so the `else` branch at line 351 fires instead — see R-9 — but the moment a GT3 RH model lands, this crashes).

**Risk**: 100% Step 1 failure once GT3 calibration produces a fitted RH model.
**Recommendation**: Branch on `car.suspension_arch` or `car.heave_spring is None`:
```python
if car.heave_spring is not None:
    baseline_heave_perch = car.heave_spring.perch_offset_front_baseline_mm
else:
    # GT3: feed front_spring_perch_baseline_mm (or 0.0) — RH model must be retrained
    # against GT3 features (front spring rate + perch + camber + pushrod), NOT heave.
    baseline_heave_perch = 0.0
```
Companion change: the calibrated RH model itself must be schema-aware. Today it expects a `heave_nmm` feature. For GT3 the regressor needs `front_spring_nmm` instead.
**Effort**: M (couples to RH-model schema rework).

### R-2 / R-3 (BLOCKER, Step 1) — `solver/rake_solver.py:332, 358`

```python
baseline_third_nmm = self.car.rear_third_spring_nmm   # property reads heave_spring
…
heave_nmm=self.car.front_heave_spring_nmm             # property reads heave_spring
```

**Expectation**: `front_heave_spring_nmm` and `rear_third_spring_nmm` are `CarModel` `@property`s that read `self.heave_spring.front_baseline_nmm` / `.rear_baseline_nmm` (per `car_model/cars.py`). For GT3 (`heave_spring=None`) these properties raise.
**Risk**: Both crash at first access whenever the calibrated RH path runs.
**Recommendation**: Have the properties return `None` when `heave_spring is None`, then guard call sites: `if car.heave_spring is None: skip RH-model heave features`. The cleaner fix is for the RH model itself to advertise its feature schema and the solver to feed only those features.
**Effort**: S (property + 4 call sites in this file).

### R-4 (BLOCKER, Step 1) — `solver/rake_solver.py:730-734` (`_solve_pinned_front`)

```python
baseline_third = self.car.rear_third_spring_nmm
baseline_spring = self.car.corner_spring.rear_spring_range_nmm[0]
baseline_perch = self.car.heave_spring.perch_offset_rear_baseline_mm
```

**Expectation**: The garage-feasibility cap re-uses heave-spring baselines even when computing a max rear pushrod. GT3 has no heave spring → AttributeError when this branch runs.
**Risk**: Same as R-1.
**Recommendation**: Gate the entire block on `if car.heave_spring is not None and rh_model.is_calibrated`. For GT3 use the corner-spring perch baseline + spring range, fed through the (yet-to-be-built) GT3 RH model.
**Effort**: S.

### R-5 / R-6 (BLOCKER, Step 1) — `solver/rake_solver.py:194-197`, `solver/rake_solver.py:850-872`

```python
def _query_aero(self, actual_front, actual_rear) -> tuple[float, float]:
    bal = self.surface.df_balance(...)
    ld = self.surface.lift_drag(...)        # NaN for GT3 balance-only maps
    return bal, ld
```

```python
def _find_free_max_ld(self, target_balance, front_excursion_p99) -> float:
    ...
    ld = self.surface.lift_drag(...)
    if ld > best_ld:                         # NaN > -inf → False
        best_ld = ld
    return best_ld if best_ld > 0 else 0.0   # silently 0.0
```

**Expectation**: The GT3 aero map is balance-only (`balance_only=True` in the parsed metadata; L/D grid is all-NaN per `aero_model/parse_xlsx.py:_BALANCE_ONLY_DIRS`). Any L/D-driven branch silently degrades:
- `RakeSolution.ld_ratio` is set from NaN → JSON writes `NaN` which most consumers will not handle.
- `_find_free_max_ld` returns `0.0` → `ld_cost_of_pinning = 0 - 0 = 0` is reported as "free is no better than pinned" when the truth is "we don't know".

**Risk**: Cascading garbage L/D values into the report; objective scoring (per Phase 2 audit of objective.py) downstream may dereference a NaN.
**Recommendation**: Add a `surface.has_ld: bool` flag (read from the parsed metadata `balance_only`), and in the rake solver:
```python
ld = self.surface.lift_drag(...) if self.surface.has_ld else math.nan
…
free_opt_ld = self._find_free_max_ld(...) if self.surface.has_ld else math.nan
```
Then have the RakeSolution `__post_init__` set `ld_cost_of_pinning = math.nan` when `ld_ratio` is NaN, and the summary print "L/D unknown — balance-only aero map" instead of "+0.000 cost".
**Effort**: S–M (also touches Solution serialization tests).

### R-7 / R-8 (DEGRADED, Step 1) — `solver/rake_solver.py:265-268, 322-323, 443, 449, 665-675`

GT3 cars do NOT pin the front static RH at the sim minimum. The IBT YAML for BMW M4 GT3 / Aston / Porsche 992 GT3 R shows static front 70-72 mm, dynamic front 68-70 mm — far above any "minimum" floor. The `pin_front_min=True` default is a GTP / ground-effect strategy; applying it on GT3 with `min_front_rh_static=50.0` (current stub) would push the front 20 mm below the driver-validated value.

The `vortex_burst_threshold_mm` is similarly GTP-physics — vortex burst is a venturi-tunnel phenomenon. GT3 cars run flat-floor non-ground-effect aero. Setting `vortex_burst_threshold_mm=2.0` for GT3 (current stub default) anchors a nonsense floor on Step 1.

**Risk**: Step 1 Output skewed front-low for GT3, balance pegged at the wrong front RH.
**Recommendation**: Add a `RakeSolverMode` per car (or per `suspension_arch`):
- GTP modes: `pinned_front` (default), `free_optimization`.
- GT3 mode: `balance_only_search` — search both front and rear for target balance, with no L/D objective (R-5/R-6 already handle this), no front-pinning, and no vortex-burst constraint. Use `default_df_balance_pct` as the only target.
**Effort**: M (new mode + tests).

### R-9 (BLOCKER, Step 1) — `solver/rake_solver.py:1122-1267` (non-garage-model path of `reconcile_ride_heights`)

```python
front_camber = car.geometry.front_camber_baseline_deg
_heave_for_rh = step2.front_heave_nmm                          # 0.0 for GT3 null
…
new_front_rh = rh_model.predict_front_static_rh(
    _heave_for_rh, front_camber,
    pushrod_mm=step1.front_pushrod_offset_mm,
    perch_mm=car.heave_spring.perch_offset_front_baseline_mm,  # AttributeError
)
```

**Expectation**: When the garage-output model is not active and only the legacy `RideHeightModel` is calibrated, this path re-derives the RH using `step2.front_heave_nmm` and the heave perch baseline. Both are GTP-only.
**Risk**: AttributeError → entire `reconcile_ride_heights` aborts → Step 1 statics never refined after Steps 2+3.
**Recommendation**: Early-return when `car.heave_spring is None` (until a GT3 reconcile path is built). Companion: log a `RECONCILE_SKIPPED — GT3 RH reconciliation pending` warning so the run is auditable.
**Effort**: S.

### R-10 (DEGRADED, Step 1) — `solver/rake_solver.py:912-933` (garage-model path of `reconcile_ride_heights`)

```python
_test_rear_pushrod = garage_model.rear_pushrod_for_static_rh(
    target_rear_rh,
    rear_third_nmm=step2.rear_third_nmm,                  # 0.0 for GT3
    rear_third_perch_mm=step2.perch_offset_rear_mm,       # 0.0
    rear_spring_nmm=step3.rear_spring_rate_nmm,           # OK from Step 3
    …
)
```

**Expectation**: When `step2` is `HeaveSolution.null()`, every `step2.*` field is 0.0. The garage-output regression treats those as legitimate predictor inputs and inverts them into a pushrod estimate — but the regression was fit with non-zero heave/third values, so a zero input extrapolates aggressively.
**Risk**: Reconciled rear pushrod can swing dozens of mm from the correct value because the regression is being asked about a setup it has never seen.
**Recommendation**: Build a separate `GarageOutputModel` schema for GT3 that omits `rear_third_nmm` / `front_heave_nmm` / `*_perch_mm` features and uses front+rear spring + pushrod + camber + fuel only. Until that exists, gate the reconcile loop on `step2.present` and skip with a banner.
**Effort**: M (couples to the RH model rework already flagged in R-1).

### C-1 (BLOCKER, Step 3) — `solver/corner_spring_solver.py:333-338`

```python
ratio_lo, ratio_hi = csm.heave_corner_ratio_range
front_max_for_ratio = front_heave_nmm / ratio_lo  # 0 / 1.5 = 0
front_min_for_ratio = front_heave_nmm / ratio_hi  # 0 / 3.5 = 0
front_rate = max(front_target_rate, front_min_for_ratio)
front_rate = min(front_rate, front_max_for_ratio)  # = 0
```

**Expectation**: When Step 2 is null (GT3), `front_heave_nmm = 0`. The clamp collapses `front_rate` to 0, invalidating any `front_target_rate` derived from frequency-isolation physics.
**Risk**: GT3 front rate always returns 0 N/mm. Step 3 emits zero stiffness, ARB stack downstream computes zero front roll stiffness → LLTD divides zero by zero or pegs at 0.
**Recommendation**: Gate the heave-ratio clamp on `front_heave_nmm > 0` (i.e. `step2.present`). For GT3 the front rate is bounded by the front coil spring range only. Until `CornerSpringModel.front_spring_range_nmm` exists, use `front_torsion_od_options` analogue:
```python
if front_heave_nmm > 0:
    front_rate = max(front_target_rate, front_heave_nmm / ratio_hi)
    front_rate = min(front_rate, front_heave_nmm / ratio_lo)
else:
    # GT3: no heave, clamp to corner-spring physical range
    front_rate = max(front_target_rate, csm.front_spring_range_nmm[0])
    front_rate = min(front_rate, csm.front_spring_range_nmm[1])
```
**Effort**: M (requires new field on CornerSpringModel).

### C-2 (BLOCKER, Step 3) — `solver/corner_spring_solver.py:341-371`

The front-architecture branching is currently:
```python
if csm.front_torsion_c > 0 and csm.front_torsion_od_options:    # GTP torsion bar
    ...
elif csm.front_roll_spring_range_nmm[1] > 0:                    # Porsche GTP
    ...
else:                                                            # falls through
    front_od = 0.0
```

GT3 stubs satisfy NEITHER condition: `front_torsion_c=0.0`, `front_torsion_od_options=[]`, `front_roll_spring_range_nmm=(0.0, 0.0)`. So GT3 enters the `else` and the solver leaves `front_rate` set to whatever the heave-ratio clamp produced (0, per C-1) and `front_od=0`. There is no GT3 `coil_paired_front` path.

**Risk**: Step 3 silently emits `front_od=0, front_rate=0` for every GT3 setup — no error, no warning.
**Recommendation**: Add an explicit GT3 arm:
```python
elif csm.front_spring_range_nmm[1] > 0:                         # GT3 paired coils
    front_rate = max(front_rate, csm.front_spring_range_nmm[0])
    front_rate = min(front_rate, csm.front_spring_range_nmm[1])
    front_rate = csm.snap_front_rate(front_rate)
    front_od = 0.0  # no torsion bar
```
And surface the front coil rate on `CornerSpringSolution.front_coil_rate_nmm` (new field). Update `summary()` to print "FRONT COIL SPRING" when this branch fires.
**Effort**: M.

### C-3 / C-4 (BLOCKER, Step 3) — `solver/corner_spring_solver.py:378-441, 399-401`

```python
rear_target_rate = rear_third_nmm / rear_target_ratio
…
_physics_rear_rate = rear_third_nmm / _physics_ratio
if abs(float(current_rear_spring_nmm) - _physics_rear_rate) / _physics_rear_rate <= 0.20:
```

**Expectation**: With `rear_third_nmm=0` (Step 2 null), `_physics_rear_rate=0`, then `abs(driver - 0) / 0` → `ZeroDivisionError`. Even without driver anchor, `rear_target_rate=0` → snapped to lower bound of `rear_spring_range_nmm` (130 N/mm BMW) regardless of any track surface input.
**Risk**: Either crash (driver anchor branch) or physics-blind output (no anchor branch). The "rear third / corner ratio" methodology is fundamentally unavailable without a third spring.
**Recommendation**: Replace the rear-rate selection logic with a frequency-isolation rule when `step2.present is False`:
```python
if rear_third_nmm > 0:
    # GTP path: third / corner ratio
    rear_target_rate = rear_third_nmm / rear_target_ratio
else:
    # GT3 path: target rear corner natural frequency directly
    rear_target_freq = bump_freq / rear_freq_ratio
    rear_target_rate = self.rate_for_freq(rear_target_freq, m_r_corner)
```
Then guard the driver-anchor with `if rear_third_nmm > 0` so the `/0` is unreachable.
**Effort**: M.

### C-5 (BLOCKER, Step 3) — `solver/corner_spring_solver.py:490-495`

```python
if csm.front_is_roll_spring or csm.front_torsion_c == 0.0:
    if front_roll_spring_nmm is not None and front_roll_spring_nmm > 0:
        front_rate = csm.snap_front_roll_spring(front_roll_spring_nmm)
    else:
        front_rate = csm.front_roll_spring_rate_nmm   # 0.0 for GT3
```

**Expectation**: GT3 satisfies `front_torsion_c == 0.0` so it enters this branch. `front_roll_spring_nmm` (the kwarg) is None for GT3 callers (no roll spring in solve()), so it falls back to `csm.front_roll_spring_rate_nmm = 0.0` — Step 3 emits zero front rate.
**Risk**: All explicit-rate construction paths (used by `materialize_overrides` and `solve_candidates`) emit zero front rate for GT3.
**Recommendation**: Mirror the architecture branching from C-2 here. The trigger predicate must distinguish `roll_spring` (Porsche GTP) from `gt3_paired_coil` from `torsion_bar` (GTP BMW/Ferrari/Acura):
```python
if csm.front_is_roll_spring:
    # Porsche GTP roll-spring path (existing)
    ...
elif csm.front_spring_range_nmm[1] > 0:
    # GT3 paired coils
    front_rate = csm.snap_front_rate(front_coil_nmm) if front_coil_nmm else csm.front_baseline_rate_nmm
    front_torsion_od_mm = 0.0
else:
    # GTP torsion bar (existing)
    ...
```
**Effort**: M (couples to C-2; same field additions).

### C-6 (BLOCKER, Step 3) — `solver/corner_spring_solver.py:516-517`

```python
total_front_heave = front_heave_nmm + 2 * front_rate            # 0 + 2*coil
total_rear_heave  = rear_third_nmm + 2 * rear_rate * MR**2      # 0 + 2*coil
```

**Expectation**: "Total heave stiffness" is meaningful in GTP architecture where heave + corner combine in heave mode. For GT3 there is no heave spring; "total heave" is just the paired corner contribution. Reporting it as "total" is misleading because the implicit context is "heave + corners together".
**Risk**: The `summary()` prints a "TOTAL HEAVE STIFFNESS (heave/third + 2 * corner)" line that is technically just `2*corner` for GT3 — confusing for the engineer reading the report.
**Recommendation**: Branch the summary text on `step2.present`. When False, label the row "TOTAL AXLE WHEEL RATE (2 × corner)" and skip the parenthetical "(heave alone: …)".
**Effort**: S.

### C-7 (BLOCKER, Step 3) — `solver/corner_spring_solver.py:728-827` (`_apply_lltd_floor`)

```python
ir = csm.front_roll_spring_installation_ratio   # 0.882 default — Porsche-specific
…
def _front_roll_k(rate_nmm: float) -> float:
    return (rate_nmm * 1000.0) * (ir ** 2) * (t_half_front_m ** 2) * (math.pi / 180)
```

**Expectation**: This helper assumes a single roll-spring kinematic (Multimatic Porsche layout: one spring, IR ≠ 1). For GT3 paired-front-coils the roll stiffness is `2 * k_wheel * (t_half)^2`, NOT `k * IR^2 * (t_half)^2`. Today the helper is only invoked when `csm.front_torsion_c == 0.0 AND csm.front_roll_spring_range_nmm[1] > 0` (line 351), so GT3 should not enter — but the gate was authored before GT3 existed and is fragile (any future fix that lets `_apply_lltd_floor` see GT3 cars will mis-compute K_roll).
**Risk**: Latent BLOCKER — fires incorrectly the moment the gate is loosened.
**Recommendation**: Add explicit `if not csm.front_is_roll_spring: return front_rate` at the top of `_apply_lltd_floor`. Long-term, dispatch on `car.suspension_arch`.
**Effort**: S.

### C-8 (DEGRADED, Step 3) — `solver/corner_spring_solver.py:351-368, 444-454`

`CornerSpringSolution.rear_motion_ratio` and `csm.rear_motion_ratio` default to `1.0` for all three GT3 stubs (`PENDING_IBT`). Wheel rate = raw spring rate. Every downstream consumer (ARB roll stiffness, damper natural frequency, objective.py excursion model) silently inherits this assumption.
**Risk**: Until calibrated, any downstream LLTD / damper / sigma reasoning is off by `(MR_true / 1.0)^2`. Real GT3 motion ratios are typically 0.85–0.95.
**Recommendation**: Mark this a calibration prerequisite. Until calibrated, downstream solvers should consult `car_model/calibration_gate.py` and degrade Step 4/Step 6 to `weak`.
**Effort**: N/A for the audit; data work blocks resolution.

### C-9 / C-10 (DEGRADED, Step 3) — `solver/corner_spring_solver.py:642-654, 115-181`

`solve_candidates()` cannot enumerate GT3 front options (empty torsion list, zero roll-spring range). The dataclass has `front_torsion_od_mm`, `front_wheel_rate_nmm`, `front_roll_spring_nmm` — but **no `front_coil_rate_nmm`**. The "front rate" for GT3 has no first-class home in the solution.
**Risk**: Reporting / writing / objective scoring all read `front_torsion_od_mm` (will be 0.0) or `front_wheel_rate_nmm` (will be 0). No clean way to emit "BMW M4 GT3 front coil = 220 N/mm" through this solution.
**Recommendation**: Add `front_coil_rate_nmm: float = 0.0` and `front_coil_perch_mm: float = 0.0` to `CornerSpringSolution`. Have `solve_candidates` enumerate `front_coil_rate_nmm` via `csm.front_spring_range_nmm` step.
**Effort**: M.

### C-11 (COSMETIC) — `solver/corner_spring_solver.py:553-564`

Gate is `if self.car.canonical_name == 'ferrari'`. When GT3 cars get their own per-car logic this pattern becomes a hash of `if name == 'X' or name == 'Y' …`. Refactor to dispatch via a `corner_spring_post_hook` on the car model.
**Effort**: S.

### A-1 (BLOCKER, Step 4) — `solver/arb_solver.py:319-328` — Porsche 992 GT3 R LLTD target

```python
tyre_sens = self.car.tyre_load_sensitivity        # 0.20 GT3 stub
pct_hs = self.track.pct_above_200kph
hs_correction = 0.01 * pct_hs
lltd_physics_offset = (tyre_sens / 0.20) * (0.05 + hs_correction)
target_lltd = self.car.weight_dist_front + lltd_physics_offset + lltd_offset
```

For Porsche 992 GT3 R: `0.449 + 0.05 = 0.499`. Per `docs/gt3_per_car_spec.md` line 148, the OptimumG formula is documented to give `0.43` for RR cars (the doc applies the formula but recognises empirical RR LLTD is 5–7 pp below FR/MR). The bare formula systematically over-targets the Porsche by 5–7 pp.

**Risk**: The Porsche rear ARB will be selected too soft and the front too stiff, mirroring the known GTP Porsche LLTD epistemic gap (per Key Principle 11 / `feedback_driver_anchor_pattern.md`).
**Recommendation**: Two-stage:
1. Add a GT3-specific physics offset modifier in `car_model/cars.py` (`measured_lltd_target` per car, sourced from per-car `docs/gt3_per_car_spec.md`):
   - BMW M4 GT3: 0.464 + 0.05 = ~0.51 (FR baseline)
   - Aston: 0.480 + 0.05 = ~0.53
   - Porsche 992 GT3 R: 0.449 + 0.005 (RR adjustment) = ~0.45 (per spec doc)
2. Long-term, when wheel-force telemetry or controlled ARB sweeps land, replace with measured. Until then, surface the gap via the driver-anchor fallback (line 410-425 already supports it for Porsche GTP).

**Effort**: S (set `measured_lltd_target` per GT3 car) + M (audit which cars need the RR offset).

### A-2 (BLOCKER, Step 4) — `solver/arb_solver.py:278-289` (front roll-stiffness branch)

GT3 has `front_is_roll_spring=False` so it enters the `else` branch (`_corner_spring_roll_stiffness`). That path is correct in form. The blocker is the **input**: `front_wheel_rate_nmm` arrives from `step3.front_wheel_rate_nmm`, which is 0 for GT3 (cascade from C-2/C-5). LLTD becomes `0 / (0 + k_rear)` = 0; rear ARB search then snaps to its softest config because every blade gives the same nonsense.
**Risk**: Cascade BLOCKER.
**Recommendation**: Once C-2/C-5 emit a real GT3 front rate this resolves automatically. Add an assertion `assert front_wheel_rate_nmm > 0, "Step 4 received zero front rate — Step 3 produced a null GT3 front coil"` so the failure mode is loud.
**Effort**: S (assertion only, real fix is in Step 3).

### A-3 / A-4 / A-5 / A-6 (BLOCKER, Step 4) — `solver/arb_solver.py:269, 350-394, 441-442, 431-437` (ARB blade encoding)

The solver assumes ARB tuning is `(size, blade)` where `size` selects a discrete BAR DIAMETER and `blade` is a continuous-ish blade-index 1..N within that size. The search loops:
```python
for rear_size in arb.rear_size_labels:
    for blade in range(1, arb.rear_blade_count + 1):
        lltd, ... = self._compute_lltd(farb_size, farb_blade, rear_size, blade, ...)
```

In every GT3 stub `front_blade_count=1, rear_blade_count=1` because the LABEL itself encodes the entire ARB configuration:
- **BMW M4 GT3** (paired blades): 11 front size labels `D1-D1`..`D6-D6`, 7 rear `D1-D1`..`D4-D4`. Blade=1 always.
- **Aston** (single blade, count PENDING): label is `B1`..`Bn` directly.
- **Porsche 992 GT3 R** (single integer setting): label is `0`..`10` (placeholder).

So the rear search degenerates to one iteration per size; the live RARB blade range (lines 441-442, 1, 4) is `1, 1` — there is no live tuning. The RARB sensitivity check (lines 431-437) returns 0 → constraint check at line 472 fails for all GT3.

**Risk**: Step 4 effectively returns the rear-ARB baseline and never tunes. The "live RARB strategy" reasoning text (`summary()` lines 150-156) is misleading.
**Recommendation**: Treat each `size_label` as the unit of search (the blade dimension is a no-op for GT3). Rewrite the search to enumerate labels and treat a single index as the live variable:
```python
# search over rear_size_labels with blade=1 fixed
for rear_size in arb.rear_size_labels:
    if rear_size.lower() == "disconnected": continue
    lltd, ... = self._compute_lltd(farb_size, 1, rear_size, 1, ...)
    err = abs(lltd - target_lltd)
    if err < best_err: best_size, best_err = rear_size, err

# live tuning: walk the label index
rear_label_idx = arb.rear_size_labels.index(best_size)
slow_idx = max(0, rear_label_idx - 2)            # softer
fast_idx = min(len(rear_size_labels)-1, rear_label_idx + 2)  # stiffer
```
Note: for **Corvette Z06 GT3.R** (per spec doc, `0=stiff → 6=soft`, INVERTED), the slow/fast direction reverses. Stamp a `direction: Literal["ascending", "descending"]` on `ARBModel` and have the live-blade chooser dispatch on it.
**Effort**: M-L (touches ARB stiffness function, `_compute_lltd`, summary text, and constraint checks).

### A-7 (DEGRADED) — `solver/arb_solver.py:280-289, 600-607, 671-679`

Three identical copies of the front-roll-spring vs paired-front roll-stiffness conditional (`solve`, `solve_candidates`, `solution_from_explicit_settings`). When GT3 needs a fourth arm (paired coil + IR ≠ 1, or future asymmetric track-width), all three must be edited.
**Recommendation**: Extract a helper `def _front_spring_roll_stiffness(self, k_wheel_nmm)` and have all three call it.
**Effort**: S.

### A-8 (COSMETIC) — `solver/arb_solver.py:482-521`

`car_specific_notes` branches on `bmw`/`ferrari`/`acura`. GT3 cars fall into the generic `else`. Add per-GT3-car notes (e.g., "BMW M4 GT3 paired-blade D-codes — adjust rear bar config; FARB locked at D3-D3 baseline").
**Effort**: S.

### A-9 (BLOCKER) — `solver/corner_spring_solver.py:776-783` calling `arb.blade_factor`

```python
k = arb.rear_stiffness_nmm_deg[i] * arb.blade_factor(arb.rear_blade_count, arb.rear_blade_count)
```

`ARBModel.blade_factor(n, total)` is the blade-position scaling for a single-bar with a moving arm. For GT3 paired-blade (BMW) the stiffness IS the table entry — there is no separate "blade factor" multiplier. `blade_factor(1, 1)` returns either 0 or undefined depending on its formula.
**Risk**: When Porsche 992 GT3 R (or any GT3 Porsche-style integer-setting car) routes through `_apply_lltd_floor` after a future gate fix, the rear-ARB stiffness term collapses.
**Recommendation**: Confirm `ARBModel.blade_factor(1, 1) == 1.0` (sanity check). For paired-blade encodings, the function should be `lambda blade, count: 1.0` because the stiffness lookup is already at the right granularity.
**Effort**: S — test only.

### D-1 / D-2 / D-3 / D-4 (DEGRADED, Diff)

Module-level constants are BMW-GTP defaults:
- `CLUTCH_TORQUE_PER_PLATE = 45.0` (BMW)
- `BMW_DEFAULT_CLUTCH_PLATES = 6`
- `DEFAULT_MAX_TORQUE_NM = 700.0` (BMW M8)
- `COAST_RAMP_OPTIONS = [40, 45, 50]` (BMW)
- `DRIVE_RAMP_OPTIONS = [65, 70, 75]` (BMW)

GT3 IBT YAML reveals different operating points: BMW M4 GT3 driver-loaded `FrictionFaces=8`, Aston `FrictionFaces=10`, all three with `DiffPreload` 100–110 Nm. Per-car overrides exist via `getattr(car, 'clutch_torque_per_plate', CLUTCH_TORQUE_PER_PLATE)` etc., but the GT3 stubs don't override them — so all GT3 cars fall back to BMW defaults.
**Risk**: Lock-percentage calculations are off by 10–20%; ramp anchor doesn't fire if a GT3 ships different ramp options.
**Recommendation**: Set `default_clutch_plates`, `clutch_torque_per_plate`, `max_torque_nm` per GT3 car. Move `COAST_RAMP_OPTIONS` / `DRIVE_RAMP_OPTIONS` onto `GarageRanges.diff_coast_drive_ramp_options` (already exists per supporting_solver.py:389) and read from the car. Add a per-car override, especially for Porsche 992 GT3 R if its ramp options differ.
**Effort**: S (config) — same shape as existing GTP per-car overrides.

### S-1 (DEGRADED, Supporting) — `solver/supporting_solver.py:415`

```python
sol.diff_clutch_plates = self.car.garage_ranges.diff_clutch_plates_options[-1]
```

GT3 stubs do not set `diff_clutch_plates_options` on `GarageRanges`. This is `[]` by default → `IndexError` at the fallback branch. Currently masked because `_solve_diff_fallback` is only reached when `DiffSolver` fails — but when it does fail, every GT3 car will crash here.
**Recommendation**: Use `getattr(self.car.garage_ranges, "diff_clutch_plates_options", [6])[-1]`.
**Effort**: XS.

### S-2 (DEGRADED, Supporting) — `solver/supporting_solver.py:389-414`

Same BMW ramp-option assumption as D-1.
**Effort**: S.

### S-3 (DEGRADED, Supporting) — `solver/supporting_solver.py:483-484, 492-499`

```python
sol.tc_gain = int(_clamp(tc_gain, 1, 10))
sol.tc_slip = int(_clamp(tc_slip, 1, 10))
```

Per `docs/gt3_per_car_spec.md` lines 38-50 and `docs/gt3_session_info_schema.md`:
- BMW M4 GT3: TC has 10 positions
- Aston, Mercedes, Ferrari 296, Lambo, McLaren, Mustang, Acura NSX, Audi, Porsche 992 GT3 R, Corvette: 12 positions
- TC off-position varies (1, 12, or 0 depending on car)

Clamping at `(1, 10)` is wrong for any 12-position TC car (truncates legal values 11, 12). The driver-anchor (lines 492-499) also uses `1 <= int(_curr_tc_gain) <= 10` — silently rejects driver-loaded values 11, 12.
**Recommendation**: Read `(min, max)` and off-position from `GarageRanges.tc_setting_range` / `_indexed_off_position` (per spec doc). For now, widen to `(1, 12)` as a starting point and add a TODO.
**Effort**: S (with Phase-2 setup_registry coupling).

### S-4 (DEGRADED, Supporting) — `solver/supporting_solver.py:530-534`

`default_cold = 152.0`, hot window 155-170 kPa — these are GTP-class values. IBT YAML for the three GT3 stubs all show **159 kPa** cold loaded by drivers. The 152 minimum is iRacing-class-wide; the 155-170 hot window may be car-dependent. Soft default; not a blocker.
**Effort**: S (post-calibration).

### T-1 (DEGRADED, Tests) — `tests/test_bmw_rotation_search.py:7,217`

The full sequential-solver chain (Step 1 → 6) only has BMW fixtures. No GT3 unit test exercises the chain end-to-end. Once C-1..C-6 and A-3..A-6 are addressed, add a `test_bmw_m4_gt3_step3_returns_nonzero_front_coil` (and equivalents) to lock in the GT3 paths.
**Effort**: M (depends on calibration data).

## Risk summary

| Severity | Count | Subsystems affected |
|---|---|---|
| BLOCKER | 19 | Step 1 (R-1..R-6, R-9), Step 3 (C-1..C-7), Step 4 (A-1..A-6, A-9) |
| DEGRADED | 13 | Step 1 (R-7..R-8, R-10), Step 3 (C-8..C-10), Step 4 (A-7), Diff (D-1..D-4), Supporting (S-1..S-4), Tests (T-1) |
| COSMETIC | 2 | Step 3 (C-11), Step 4 (A-8) |

**Top three highest-impact**:
1. **R-1/R-2/R-3/R-4/R-9 + C-2/C-5**: GT3 has no heave spring; the entire RH model schema, Step 2 sentinel handling, Step 3 architecture branching, and reconcile loop need a GT3 path. Without it Steps 1+3 either crash or emit zero.
2. **A-3/A-4/A-5/A-6**: ARB blade-vs-label encoding mismatch — solver assumes (size × blade), GT3 collapses to single-label; live tuning broken for every GT3 car.
3. **A-1**: LLTD physics formula gives wrong target for Porsche 992 GT3 R (RR layout) — same 5-pp epistemic gap as Porsche 963 GTP, will need driver-anchor fallback or direct measurement.

## Effort estimate

- **S** (≤ half-day): R-2, R-3, R-4, R-7, R-9 (gate), C-7, C-11, A-7, A-8, A-9, D-1..D-4, S-1, S-2, S-3, S-4 → ~16 fixes
- **M** (≤ 2 days): R-1 (couples to RH model), R-5, R-6, R-8 (mode), R-10, C-1, C-2, C-3, C-4, C-5, C-6, C-9, A-1, A-2, A-3, T-1 → ~15 fixes
- **L** (≥ 3 days): A-3..A-6 combined (ARB encoding rewrite + descending/ascending direction stamp + summary text) → 1 fix bundle

Critical path: **R-1 / C-2 / A-3** must land before any GT3 end-to-end smoke test of Step 1 → 4 can succeed.

## Dependencies

- **`car_model/cars.py`**: `CornerSpringModel` needs `front_spring_range_nmm`, `front_spring_step_nmm`, `front_spring_perch_baseline_mm`, `front_baseline_rate_nmm`. `GarageRanges` may need `diff_clutch_plates_options` and `tc_setting_range` per car (S-1, S-3).
- **`car_model/garage.py`**: `GarageOutputModel` schema must accept GT3 feature set (no heave/third). New regression model required.
- **`car_model/calibration_gate.py`**: Step 3 / Step 4 cascade needs to surface as `weak` while motion ratios + LLTD targets are PENDING_IBT.
- **`solver/heave_solver.py`**: `HeaveSolution.null()` already exists (line 113); audit's recommendation is to make every consumer guard on `step2.present`. Unblocked at the heave_solver side.
- **`output/setup_writer.py`**: out of scope for this audit; the per-car PARAM_IDS work for GT3 needs to land before any GT3 .sto can be emitted.
- **`aero_model/interpolator.py`**: Add `surface.has_ld: bool` (read from parsed metadata `balance_only`) to support R-5/R-6.
- **`docs/gt3_per_car_spec.md`**: source of truth for ARB encoding direction (ascending/descending) and TC/ABS off-position, both of which affect the solver branches in A-3..A-6 and S-3.
- **Phase-2 worker on `output/setup_writer.py`**: depends on this audit + Phase-2 corner-spring schema additions before per-car PARAM_IDS dispatch can be wired.
- **Phase-2 worker on `solver/heave_solver.py`**: minimal — the null sentinel is already in place; that worker should verify all `step2.*` reads are `present`-gated.
