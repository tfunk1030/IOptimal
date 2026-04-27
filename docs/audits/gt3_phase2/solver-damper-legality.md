# GT3 Phase 2 Audit — Solver: Step 6 dampers, legality, scenarios

## Scope

Audited modules:

- `solver/damper_solver.py`
- `solver/legality_engine.py`
- `solver/legal_space.py`
- `solver/scenario_profiles.py`
- `solver/candidate_search.py`
- `solver/modifiers.py`
- `solver/params_util.py`
- `solver/decision_trace.py`
- `solver/stint_model.py`

Lens: GT3 readiness. Where does GTP-shaped code break for the 11 GT3 cars? Specifically:
1. Per-corner vs per-axle damper handling (GT3 has 8 channels, not 16).
2. Damper click range / polarity assumptions (BMW 0–11 higher=stiff vs Audi/McLaren/Corvette inverted, Porsche 992 0–12 wider).
3. Reading `step2.*` heave-related fields without guarding on `step2.present` (GT3 returns `HeaveSolution.null()`, all numeric fields = 0.0).
4. Embedding heave/third spring keys in canonical params dicts / SetupDeltas / scoring routines.
5. GTP-specific fuel/scenario constants (89 L tank, GTP race-length assumptions).
6. `max_front_heave_travel_used_pct` and other heave-only sanity caps applied unconditionally.
7. `front_torsion_c` / `front_torsion_od_*` reads that should be guarded for GT3 (which has zero torsion bars).

Reference docs read:
- `CLAUDE.md`
- `docs/gt3_session_info_schema.md` (per-axle damper section + per-car field divergence)
- `docs/gt3_per_car_spec.md` (damper polarity table, click ranges)
- `car_model/cars.py` (`BMW_M4_GT3`, `ASTON_MARTIN_VANTAGE_GT3`, `PORSCHE_992_GT3R` definitions)
- `solver/heave_solver.py` (`HeaveSolution.null()` and `present: bool` flag)

## Summary table

| ID | File:line | Severity | One-liner | GT3 cars triggered |
|----|-----------|----------|-----------|--------------------|
| F1 | `solver/damper_solver.py:444-509` | BLOCKER | `solve()` always reads `front_heave_spring_nmm`/`rear_third_spring_nmm` from car model and computes axle-modal rates that include heave/third — for GT3 these are 0.0/None and `c_crit` collapses to corner-only physics silently. No `step2.present` guard. | All 11 GT3 cars |
| F2 | `solver/damper_solver.py:438-449` | BLOCKER | `solve()` constructs four `CornerDamperSettings` (LF/RF/LR/RR) — but GT3 IBT YAML exposes dampers PER-AXLE (8 channels). Solver still emits 16 click values (LF==RF, LR==RR) which is fine, but the L/R asymmetric adjustment branch (`lf_hs_comp_adj` / `rf_hs_comp_adj` based on per-corner shock vel) at lines 644-674 has no physical meaning in GT3 — the garage UI cannot apply asymmetric L/R clicks. | All 11 GT3 cars |
| F3 | `solver/damper_solver.py:444` | DEGRADED | Hardcoded `fuel_load_l: float = 89.0` default. GTP convention. BMW M4 GT3 / Porsche 992 GT3R = 100 L; Aston / Mercedes = 106 L; Lambo = 100 L; Ferrari 296 = 104 L. Affects corner-mass calculation when caller forgets to pass fuel. | All 11 GT3 cars (silent under-mass) |
| F4 | `solver/damper_solver.py:1004` | DEGRADED | `solution_from_explicit_settings()` same `fuel_load_l: float = 89.0` default. | All 11 GT3 cars |
| F5 | `solver/damper_solver.py:702-743` | DEGRADED | All five constraint checks (`Front LS damping ratio` etc.) use BMW-derived target values (0.88, 0.22) and tolerance bands hardcoded into the dataclass `target` field for GT3 the targets should come from `zeta_target_*` on the GT3 `DamperModel`. | All 11 GT3 cars |
| F6 | `solver/damper_solver.py:476-483` | COSMETIC | Strict-mode error message says "Run validation/calibrate_dampers". Will fire for all 11 GT3 cars at first run because `zeta_is_calibrated=False`. Acceptable but should reference `--zeta-is-uncalibrated` opt-out path or note the GT3 calibration gap explicitly. | All 11 GT3 cars |
| L1 | `solver/legality_engine.py:62-69` | BLOCKER | Ferrari deep-copy block reads `validation_step2.front_heave_nmm`, `rear_third_nmm`, and `validation_step3.front_torsion_od_mm` on the assumption these are non-zero indexed Ferrari values. For Ferrari 296 GT3 there is NO indexed mapping; for any GT3 car routed through this branch (none yet, but the gate is `canonical_name == "ferrari"`) a future Ferrari 296 GT3 onboarding with the same canonical name will hit decoder failures. | Ferrari 296 GT3 (when added) |
| L2 | `solver/legality_engine.py:139-203` | BLOCKER | `validate_candidate_legality()` reads `gr.front_heave_nmm`, `gr.front_heave_perch_mm`, `gr.rear_third_nmm`, `gr.rear_third_perch_mm`, `gr.rear_spring_nmm` unconditionally from `range_checks`. GT3 `garage_ranges` for these heave fields will either be (0,0) or absent. Candidate that omits `front_heave_spring_nmm` is fine (`if key not in params: continue`), but if anything WRITES heave keys into a GT3 candidate dict (e.g. `legal_space.PERCH_KEYS` or local-refine path) the range_check `[0,0]` will hard-veto every legal value. | All 11 GT3 cars |
| L3 | `solver/legality_engine.py:184-203` | BLOCKER | `damper_checks` dict uses one `(lo, hi)` pair per axle name (`front_ls_comp`, `rear_ls_comp`...). The `(lo, hi)` are pulled from `car.damper.{ls,hs}_*_range` which is GT3-correct for BMW/Aston/Porsche 992 (defaults to 0–11). However: (a) Porsche 992 GT3R driver IBT shows clicks at value 12 — needs range upgrade in `car_model/cars.py` to (0, 12), then this code is fine; (b) Audi/McLaren/Corvette are INVERTED (lower = stiffer) — the range check is symmetry-blind so it works, BUT the soft penalties at lines 218-227 ("front LS comp >= rear LS comp") are PHYSICALLY WRONG for inverted cars: lower LS comp means STIFFER, so `front >= rear` flips meaning. | Audi R8 LMS, McLaren 720S, Corvette Z06 |
| L4 | `solver/legality_engine.py:218-227` | BLOCKER | Soft penalty "Front LS comp < rear LS comp (entry instability)" assumes higher click = stiffer. INVERTED for Audi/McLaren/Corvette: their convention is lower = stiffer, so the inequality must FLIP for those three cars. Same bug for "Rear HS comp >> front HS comp (compliance hierarchy violation)". | Audi R8 LMS, McLaren 720S, Corvette Z06 |
| L5 | `solver/legality_engine.py:208-213` | DEGRADED | "Heave/third ratio" soft penalty (`fh / rt` between 0.02 and 0.25). For GT3 cars `fh` and `rt` are 0/None, so `fh/rt` either divides by zero or both are None. The `if fh is not None and rt is not None and rt > 0:` guard prevents the crash, but the bigger issue is that for GT3 cars these keys should never appear in `params` at all. Cosmetic in current state, but signals the absence of a GT3-aware param schema. | All 11 GT3 cars |
| LS1 | `solver/legal_space.py:64-95` | BLOCKER | Module-level constants `FRONT_HEAVE_PERCH_K = 0.001614` are hard-derived from BMW GTP 62-session calibration. The `_BMW_*_REF` constants are used as fallbacks in `_car_spring_refs()`. For GT3 cars the heave spring is None — `float(car.front_heave_spring_nmm)` will raise `TypeError` (None is not floatable). | All 11 GT3 cars |
| LS2 | `solver/legal_space.py:98-186` | BLOCKER | `compute_perch_offsets()` reads `params.get("front_heave_spring_nmm", front_heave_ref)` and `params.get("rear_third_spring_nmm", ...)` — for GT3, these keys are absent and the fallback is `float(car.front_heave_spring_nmm)` which is None → TypeError. The whole function should short-circuit for `SuspensionArchitecture.GT3_COIL_4WHEEL` before running ANY perch math. | All 11 GT3 cars |
| LS3 | `solver/legal_space.py:193-238` | BLOCKER | `TIER_A_KEYS` includes `front_heave_spring_nmm`, `rear_third_spring_nmm`, `front_torsion_od_mm` unconditionally. For GT3 cars these are not searchable parameters — heave/third don't exist, and torsion bars don't exist. `from_car()` (line 743) iterates over `TIER_A_KEYS` and tries to build SearchDimensions; with garage_ranges defaults of (0.0, 900.0) for GT3 it will create searchable dims that can never be applied. | All 11 GT3 cars |
| LS4 | `solver/legal_space.py:241-251` | BLOCKER | `PERCH_KEYS` and `LOCAL_REFINE_KEYS` reference perch keys that don't exist on GT3. Any code path that hits LOCAL_REFINE_KEYS (e.g. local refinement layer) will write heave perches into GT3 candidates and fail legality. | All 11 GT3 cars |
| LS5 | `solver/legal_space.py:781-921` | BLOCKER | `_build_dimension()` builds `range_map` containing `front_heave_spring_nmm`, `rear_third_spring_nmm`, `front_torsion_od_mm`, `rear_spring_perch_mm` etc. — all read from `gr.*` defaults that are present on every `GarageRanges` instance. For GT3 the heave values default to (0.0, 900.0) but heave isn't a real GT3 control. The damper sub-block at lines 824-836 uses `d.{ls,hs}_*_range` — fine for GT3 BMW/Aston/Porsche (default 0–11) but BREAKS for Audi/McLaren/Corvette where the resolution=1.0 step is fine but the polarity/encoding differs. No `click_polarity` field consumed anywhere in this module. | All 11 GT3 cars (heave keys); Audi R8, McLaren 720S, Corvette Z06 (polarity) |
| SP1 | `solver/scenario_profiles.py:71-83` | BLOCKER | `single_lap_safe.sanity.max_front_heave_travel_used_pct=96.0` — applied unconditionally in `prediction_passes_sanity()` (line 238). For GT3 cars, the prediction will not have `front_heave_travel_used_pct` populated (no heave spring), so `getattr(prediction, attr, None)` returns None and the check is skipped. SAFE today but the constant remains a GTP assumption baked into the canonical "default" profile. Should be paired with a GT3-only profile or moved to a per-architecture sanity dict. | All 11 GT3 cars (latent — silent skip) |
| SP2 | `solver/scenario_profiles.py:99-170` | DEGRADED | All four scenarios bake GTP-style heave-travel caps and pressure ranges. Pressure range 160–186 kPa (single_lap_safe) was derived from BMW M Hybrid V8. GT3 BMW M4 IBT shows cold pressure 159 kPa at Spielberg — close but unverified for hot range. Pressures don't cause hard veto since values out of range are just `issues`, but the message will fire spuriously. | All 11 GT3 cars |
| SP3 | `solver/scenario_profiles.py` (no GT3 race profile) | DEGRADED | Race profile assumes GTP race length (89 L tank fuel cap implicit through stint model). GT3 races at iRacing's 24h Spa equivalent run different stint lengths. Race scenario sanity (`max_front_heave_travel_used_pct=95.5`) doesn't apply. No GT3-specific scenario. | All 11 GT3 cars |
| CS1 | `solver/candidate_search.py:88-111` | BLOCKER | `_snap_targets_to_garage()` snaps `front_heave_nmm`, `rear_third_nmm`, `perch_offset_front_mm`, `perch_offset_rear_mm` unconditionally on every candidate. For GT3 cars `s2` is a null HeaveSolution dict — these keys may be present in the targets dict from `_extract_target_maps()` line 312-317 with value 0.0. Snapping 0.0 to BMW range (0, 900) yields 0.0 which then propagates as a "user wants 0 N/mm heave" to the writer. | All 11 GT3 cars |
| CS2 | `solver/candidate_search.py:113-142` | BLOCKER | Step 3 snap block reads `s3["front_torsion_od_mm"]`, snaps to `_TORSION_OD_OPTIONS = [13.90, ..., 18.20]` BMW-specific list. For GT3 cars `corner_spring.front_torsion_od_options` is empty (GT3 has NO torsion bars — coil at all 4 corners). The fallback path at line 130 (`csm is not None and getattr(csm, "front_torsion_c", 0.0) > 0`) reaches BMW grid; for GT3 with `front_torsion_c=0.0` it correctly leaves untouched, but a 0.0 sentinel value will still be written downstream. Add explicit `if car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL: skip step3 torsion snap`. | All 11 GT3 cars |
| CS3 | `solver/candidate_search.py:299-370` | BLOCKER | `_extract_target_maps()` reads `s2.front_heave_nmm`, `s2.rear_third_nmm`, `s2.perch_offset_front_mm`, `s2.perch_offset_rear_mm` from the step2 result without checking `s2.present`. For GT3 these are zero placeholders. Same for `s3.front_torsion_od_mm`. The dict that gets built embeds `0.0` for fields that don't exist on GT3 — these zeros then flow into legality, scoring, decision-trace, `.sto` writer. | All 11 GT3 cars |
| CS4 | `solver/candidate_search.py:388-409` | BLOCKER | `direct_step_map` includes `front_heave_spring_nmm → ("step2", "front_heave_nmm")`, `front_torsion_od_mm → ("step3", "front_torsion_od_mm")`, etc. These should not be in the GT3 routing table — if a candidate-search adds them they'll be silently snapped to BMW ranges and written to a GT3 .sto. | All 11 GT3 cars |
| CS5 | `solver/candidate_search.py:644-666` | DEGRADED | `_apply_family_state_adjustments()` heave-anchor block at lines 641-666: reads `_curr_setup.front_heave_nmm` and `_curr_setup.rear_third_nmm`. For GT3 IBT setups these are absent on the parsed `current_setup` (no `Heave` section). The `or 0.0` defaults make this robust — but the subsequent `_scale_numeric(targets["step2"], "front_heave_nmm", ...)` writes a non-zero value for a GT3 car that has no heave spring. | All 11 GT3 cars |
| CS6 | `solver/candidate_search.py:711-716` | BLOCKER | `_adjust_integer(targets["step6"][corner_name], "hs_comp", ..., lo=0, hi=20)` — hardcoded `hi=20` damper bound. BMW/Aston/Porsche range is 0–11 (12 total positions); writing 20 then snapping later via `_target_overrides` may silently clamp. For Audi/McLaren the real range is 0–40 / 0–50 — adjustments capped at 20 SHRINK the legal space. For Corvette range 0–30 / 0–22 — same problem. | BMW M4, Aston Vantage, Porsche 992, Mercedes, Ferrari, Lambo, Mustang, Acura (range too wide); Audi, McLaren, Corvette (range too narrow + INVERTED polarity not respected) |
| CS7 | `solver/candidate_search.py:712,716` | BLOCKER | Damper hierarchy heuristic: rear `ls_rbd` adjustment is symmetric with front (`lo=0, hi=20`). For Audi/McLaren/Corvette where lower=stiffer, the family adjustment direction is flipped. A "stiffer rear ls_rbd to fight oversteer" with intensity +N would actually SOFTEN the rear on these three cars, making the diagnosis worse. | Audi R8, McLaren 720S, Corvette Z06 |
| CS8 | `solver/candidate_search.py:858-864` | DEGRADED | `_estimate_candidate_disruption()` scales: `_append(getattr(setup, "front_heave_nmm", None), ..., 25.0)`, etc. For GT3 cars `setup.front_heave_nmm` is None → term skipped. OK, but the function then averages over fewer terms — for cars with NO heave/third, the disruption denominator is effectively halved compared to GTP, distorting the family selector. | All 11 GT3 cars |
| CS9 | `solver/candidate_search.py:861` | DEGRADED | `_append(getattr(setup, "front_torsion_od_mm", None), ..., 1.0)` — scale is 1.0 mm/unit (BMW torsion OD step). GT3 has no torsion bar, so this is None and skipped. Cosmetic. | All 11 GT3 cars |
| MD1 | `solver/modifiers.py:32-87` | BLOCKER | `SolverModifiers.front_heave_min_floor_nmm`, `rear_third_min_floor_nmm`, `front_heave_perch_target_mm` baked into the dataclass. For GT3 cars these will be set by the diagnosis block (lines 159-220) but have NO target in step 2. The modifier values are then read by the heave solver — which for GT3 returns `HeaveSolution.null()` BEFORE the modifier even has a chance to apply. The modifier values become dead — degraded but harmless. | All 11 GT3 cars |
| MD2 | `solver/modifiers.py:117-126` | DEGRADED | `_heave_min` calculated from `car.heave_spring.front_spring_range_nmm`. For GT3 cars `heave_spring=None` (per `car_model/cars.py:1592` and `SuspensionArchitecture.GT3_COIL_4WHEEL`). `car.heave_spring.front_spring_range_nmm` will raise `AttributeError`. The fallback `_heave_min = 30.0` only applies when `car is None`, not when `heave_spring is None`. | All 11 GT3 cars |
| MD3 | `solver/modifiers.py:208-220` | BLOCKER | `_perch_baseline = car.heave_spring.perch_offset_front_baseline_mm` — same `AttributeError` for GT3 (heave_spring is None). Fallback only fires for `car is None`. | All 11 GT3 cars |
| MD4 | `solver/modifiers.py:241-303` | DEGRADED | All `front_heave_min_floor_nmm` writes (lines 244, 258, 269, 273, 277, 300, 367, 370, 373, 376) operate on a field that's meaningless for GT3. No `if car.suspension_arch is GT3_COIL_4WHEEL: skip heave floor logic` short-circuit. The modifier object will carry stale heave values into a GT3 pipeline. | All 11 GT3 cars |
| PU1 | `solver/params_util.py:46-49` | BLOCKER | `solver_steps_to_params` writes `params["front_heave_spring_nmm"] = step2.front_heave_nmm` and `params["rear_third_spring_nmm"] = step2.rear_third_nmm` whenever `step2 is not None`. For GT3 a null HeaveSolution has `step2 is not None` (it's a real object) — the check is wrong. Need `if step2 is not None and step2.present` or guard on car suspension_arch. Result: 0.0 writes for heave/third into the canonical params dict, which then flows into ObjectiveFunction and scoring. | All 11 GT3 cars |
| PU2 | `solver/params_util.py:51-54` | BLOCKER | Same as PU1 for `front_torsion_od_mm` and `rear_spring_rate_nmm`. GT3 corner spring solution will have `front_torsion_od_mm=0.0` (no torsion bar). Writing 0.0 to the params dict labels GT3 as having a torsion bar of OD=0. | All 11 GT3 cars |
| PU3 | `solver/params_util.py:74-92` | DEGRADED | Step 6 averages "the first L corner" (LF/LR) into `params[f"{prefix}_{field}"]`. GT3 dampers ARE per-axle in the IBT YAML — LF==RF and LR==RR by definition. The averaging code happens to do the right thing but the comment "In practice LF==RF and LR==RR for axle-symmetric setups" is wrong for the GT3 ARCHITECTURE: in GT3 it's not "in practice" — it's literally the same garage control. Better to read directly from one axle representative. Cosmetic but suggests a misunderstanding. | All 11 GT3 cars |
| DT1 | `solver/decision_trace.py:36-47` | BLOCKER | `_estimate_gain_ms()` includes parameter branch for `front_heave_spring_nmm`, `front_heave_perch_mm`, `rear_third_spring_nmm`, `rear_third_perch_mm`. For GT3 these parameters will not appear in the decision list, so the branches are never reached. SAFE today, but the parameter spec at `_legacy_parameter_spec()` (line 132-153) explicitly lists `front_heave_nmm`, `front_heave_perch_mm`, `rear_third_nmm` — for GT3 these will trip the `current_value` lambda (`cs.front_heave_nmm`) since `setup.front_heave_nmm` is absent on a GT3 setup, raising AttributeError caught by the `except (AttributeError, TypeError): continue` block at line 280. Decisions for these params will be silently dropped — not surfaced as an explicit "not applicable for GT3" message. | All 11 GT3 cars |
| DT2 | `solver/decision_trace.py:156-167` | BLOCKER | Hard-coded `front_torsion_od_mm`/`front_torsion_bar_index` parameter spec. For GT3 (no torsion bar) the lambda throws AttributeError and the row is silently dropped. User-facing trace will be missing context that GT3 simply has no torsion bar. | All 11 GT3 cars |
| DT3 | `solver/decision_trace.py:114-119` | DEGRADED | `_legacy_parameter_spec` branches on `is_ferrari = car_name.lower() == "ferrari"`. GT3 cars use `bmw_m4_gt3`, `aston_martin_vantage_gt3`, `porsche_992_gt3r` canonical names — they fall through to BMW (non-Ferrari) branch silently and inherit BMW labels. | All 11 GT3 cars |
| ST1 | `solver/stint_model.py:181` | DEGRADED | `f"    Full fuel ({89:.0f}L): ..."` — hardcoded string `89` in summary output. For GT3 BMW M4 / Porsche 992 GT3R the tank is 100 L; Aston / Mercedes 106 L; Lambo 100 L; Acura PENDING; Mustang/Corvette PENDING. Header lies for GT3 cars. | All 11 GT3 cars (display only) |
| ST2 | `solver/stint_model.py:269-271` | DEGRADED | `PUSHROD_CORRECTION_MM_PER_KG = 0.5 / (77 * FUEL_DENSITY_KG_PER_L)` — derived from "BMW 89L → 12L needs ~0.5mm pushrod correction". GT3 fuel range is 100→12 = 88 kg vs GTP 77 kg — so the ratio per kg is approximately right (0.5/56 vs 0.5/64). But the named constant 77 (GTP fuel mass range) is a magic number that doesn't update for GT3. | All 11 GT3 cars (small numerical drift) |
| ST3 | `solver/stint_model.py:282,287-288,716` | BLOCKER | Default fuel levels list `[89.0, 50.0, 12.0]` baked into `compute_fuel_states()` and `analyze_stint()`. For GT3 cars the full fuel level should be 100/106/104 L per car. Currently uses 89 L start point regardless. | All 11 GT3 cars |
| ST4 | `solver/stint_model.py:430-447` | DEGRADED | `predict_tyre_degradation()` defaults: grip 3%/10laps, balance 0.5°/10laps, RARB offset clamped to a 1–5 blade range (line 547: `rarb_blade = max(1, min(5, 3 - rarb_adj))`). For GT3 the RARB blade range varies per car — Acura rear is 1–5, BMW rear is paired 1–7, Porsche 992 is 1–N (PENDING). Hardcoded 1–5 is BMW-derived. | All 11 GT3 cars |
| ST5 | `solver/stint_model.py:467-487` | BLOCKER | `find_compromise_parameters()` writes `params["front_heave_nmm"]` and `params["rear_third_nmm"]` from condition.heave_optimal_nmm. For GT3, `condition.heave_optimal_nmm` is computed from `base_heave_nmm * mass_ratio` — and `base_heave_nmm = float(car.front_heave_spring_nmm)` (line 694) will TypeError on GT3 (None). | All 11 GT3 cars |
| ST6 | `solver/stint_model.py:693-696` | BLOCKER | `base_heave_nmm = float(car.front_heave_spring_nmm)` — None for GT3 → TypeError on `analyze_stint()`. Not even guarded. | All 11 GT3 cars |
| ST7 | `solver/stint_model.py:541-548` | DEGRADED | `_build_balance_curve()` recommends RARB blade in 1–5 range. Cars with different rear blade counts (Acura 1–5 OK; BMW M4 GT3 1–7 paired; Porsche binary+blades; etc.) will have wrong recommendations. | All 11 GT3 cars |

## Findings

### F1 — `damper_solver.solve()` reads heave fields without `step2.present` guard

**File:** `solver/damper_solver.py:438-509`

**Severity:** BLOCKER

**Cars triggered:** All 11 GT3 cars (BMW M4, Mercedes, Aston Vantage, Ferrari 296, Lambo, McLaren, Porsche 992, Acura, Audi, Mustang, Corvette).

**Code shape (current):**

```python
def solve(self, ..., front_heave_nmm: float | None = None,
          rear_third_nmm: float | None = None) -> DamperSolution:
    front_axle_heave_nmm = (
        self.car.front_heave_spring_nmm  # GT3: None → TypeError
        if front_heave_nmm is None
        else float(front_heave_nmm)
    )
    rear_axle_heave_nmm = (
        self.car.rear_third_spring_nmm   # GT3: None → TypeError
        if rear_third_nmm is None
        else float(rear_third_nmm)
    )
    modal_front_rate_nmm = axle_modal_rate_nmm(
        front_wheel_rate_nmm,
        front_axle_heave_nmm,            # zero/None breaks modal calc
        self.car.tyre_vertical_rate_front_nmm,
    )
```

**Why it's a blocker:** GT3 cars have `front_heave_spring_nmm=None` and `rear_third_spring_nmm=None` (set via `SuspensionArchitecture.GT3_COIL_4WHEEL` invariant in `car_model/cars.py:1756-1769`). The `if X is None` branch silently substitutes `None`, which `axle_modal_rate_nmm` either crashes on or treats as 0 — producing a `c_crit` that's wrong by a factor of (1 + heave/wheel) = 1.0 instead of the correct GTP factor. Since GT3 has only corner springs, the correct modal rate IS just `front_wheel_rate_nmm + tyre_vertical_rate_front_nmm` in series — no axle-modal blending of heave is needed.

**Fix shape:**

```python
from car_model.cars import SuspensionArchitecture
if self.car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
    modal_front_rate_nmm = wheel_plus_tyre_modal_nmm(
        front_wheel_rate_nmm, self.car.tyre_vertical_rate_front_nmm,
    )
    modal_rear_rate_nmm = wheel_plus_tyre_modal_nmm(
        rear_wheel_rate_nmm, self.car.tyre_vertical_rate_rear_nmm,
    )
else:
    # existing GTP path
    ...
```

### F2 — Per-corner damper output for GT3 should be per-axle (cosmetic risk)

**File:** `solver/damper_solver.py:438-449, 644-699`

**Severity:** BLOCKER (correctness for L/R adjustment branch)

**Cars triggered:** All 11 GT3 cars.

GT3 IBT YAML exposes 8 damper channels (`Dampers.FrontDampers.LowSpeedCompressionDamping`, …, `Dampers.RearDampers.HighSpeedReboundDamping`). The solver constructs four `CornerDamperSettings` objects (LF/RF/LR/RR). For axle-symmetric setups LF==RF, LR==RR — that part is fine.

The L/R asymmetry branch at lines 644-674 explicitly DIVERGES LF from RF based on per-corner shock velocities. **For GT3 this divergence cannot be applied to the garage** — the iRacing UI only has a single front-axle adjuster and a single rear-axle adjuster. Any `lf_hs_comp_adj != rf_hs_comp_adj` will be silently lost when the .sto is written.

**Fix shape:**

```python
if self.car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
    # Per-axle: skip L/R asymmetric adjustment
    lf_hs_comp_adj = rf_hs_comp_adj = (lf_hs_comp_adj + rf_hs_comp_adj) // 2
    lr_hs_comp_adj = rr_hs_comp_adj = (lr_hs_comp_adj + rr_hs_comp_adj) // 2
```

Or, cleaner, skip the asymmetry block entirely for GT3 and emit single per-axle clicks.

### F3 / F4 — Hardcoded `fuel_load_l: float = 89.0` defaults

**File:** `solver/damper_solver.py:444, 1004`

**Severity:** DEGRADED

**Cars triggered:** All 11 GT3 cars (silent under-mass).

The default `89.0` is the GTP convention. GT3 fuel caps:

| Car | Tank L |
|---|---|
| BMW M4 GT3 | 100 |
| Mercedes-AMG | 106 |
| Aston Vantage | 106 |
| Ferrari 296 | 104 |
| Lamborghini Huracán | 100 (assumed) |
| McLaren 720S | 100 (assumed) |
| Porsche 992 GT3R | 100 |
| Acura NSX | 100 (assumed) |
| Audi R8 LMS | 100 (assumed) |
| Ford Mustang | 100 (assumed) |
| Corvette Z06 | 100 (assumed) |

When the caller forgets to pass `fuel_load_l`, GT3 cars will be modeled at 89 L which under-counts mass by 8–13 kg per car. Corner mass affects critical damping by a factor of √(m): a 10 kg shortfall on a 200 kg corner mass biases `c_crit` by ~2.5%.

**Fix shape:** Change default to `None` and require callers to pass it; if not passed, raise.

```python
def solve(self, ..., fuel_load_l: float | None = None) -> DamperSolution:
    if fuel_load_l is None:
        raise ValueError(f"DamperSolver.solve() requires fuel_load_l "
                         f"(car {self.car.name} max {self.car.fuel_capacity_l}L)")
```

### F5 — Constraint check targets hardcoded to BMW values

**File:** `solver/damper_solver.py:702-743`

**Severity:** DEGRADED

The `target=0.88` and `target=0.22` literals come from BMW M Hybrid V8 IBT calibration. For GT3 cars with `zeta_is_calibrated=False` (all 11 currently), these check `value` against an inappropriate target. Should pull from `self.car.damper.zeta_target_*` fields.

### L3 — Damper range check ignores Porsche 992 0–12 wider range

**File:** `solver/legality_engine.py:184-203`

**Severity:** BLOCKER

**Cars triggered:** Porsche 992 GT3R (driver IBT shows clicks at 12).

The `damper_checks` dict reads `d.{ls,hs}_*_range` which currently defaults to `(0, 11)` in `car_model/cars.py:1315-1319` and is inherited by `PORSCHE_992_GT3R` at `car_model/cars.py:3267-3273` (no override). However the IBT capture for Porsche 992 GT3R at Spielberg 2026-04-26 shows driver-loaded values of 12 (`docs/gt3_session_info_schema.md:144`, `R LSR 12`). This means either (a) the iRacing range is actually 0–12 for Porsche, or (b) the displayed "12" is a label for click index 11. **Action:** verify with click-sweep IBT, then update `PORSCHE_992_GT3R.damper.{ls,hs}_*_range` to `(0, 12)` if confirmed.

If the range is updated, `solver/legality_engine.py` is automatically correct. Until then, candidates with click 12 will be hard-vetoed.

### L4 — Soft penalties assume "higher click = stiffer" — INVERTED for 3 cars

**File:** `solver/legality_engine.py:218-227`

**Severity:** BLOCKER

**Cars triggered:** Audi R8 LMS evo II, McLaren 720S EVO, Corvette Z06 GT3.R.

Per `docs/gt3_per_car_spec.md` damper table:
- Audi R8 LMS: LSC/LSR 2–38, HSC/HSR 0–40, **lower = stiffer (INVERTED)**
- McLaren 720S: LSC/LSR 0–40, HSC/HSR 0–50, **lower = stiffer (INVERTED)**
- Corvette Z06: LSC/LSR 0–30, HSC/HSR 0–22, **lower = stiffer (Penske, INVERTED)**

Current code:

```python
fls = params.get("front_ls_comp")
rls = params.get("rear_ls_comp")
if fls is not None and rls is not None and fls < rls:
    soft_penalties.append("Front LS comp < rear LS comp (entry instability)")
```

For inverted cars, "front LS comp < rear LS comp" means front is STIFFER than rear (lower click = stiffer). The penalty fires for the physically CORRECT direction.

**Fix shape:**

```python
polarity = getattr(car.damper, "click_polarity", "higher_stiffer")
if polarity == "higher_stiffer":
    front_stiffer_than_rear = fls > rls
else:
    front_stiffer_than_rear = fls < rls
if not front_stiffer_than_rear:
    soft_penalties.append("Front LS comp softer than rear LS comp (entry instability)")
```

This requires adding `click_polarity: Literal["higher_stiffer", "lower_stiffer"] = "higher_stiffer"` to `DamperModel` (currently absent — see audit `docs/gt3_per_car_spec.md` "Solver implication: damper deltas are not portable across cars").

### LS1 / LS2 — `legal_space.compute_perch_offsets` crashes on GT3

**File:** `solver/legal_space.py:64-186`

**Severity:** BLOCKER

**Cars triggered:** All 11 GT3 cars.

```python
def _car_spring_refs(car: CarModel) -> tuple[float, float, float]:
    front_heave_ref = float(car.front_heave_spring_nmm)  # GT3: None → TypeError
    rear_third_ref = float(car.rear_third_spring_nmm)    # GT3: None → TypeError
```

GT3 cars have `front_heave_spring_nmm=None`. `compute_perch_offsets` is unreachable for GT3 today only because the upstream candidate generator never sends GT3 candidates with these keys — but the code is a landmine.

**Fix shape:** Short-circuit for GT3 architecture:

```python
def compute_perch_offsets(params: dict, car: CarModel) -> dict:
    if car is not None and car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
        return {}  # GT3 has no perch — coil-overs use direct spring rate
    # ... existing GTP path
```

### LS3 — `TIER_A_KEYS` includes heave/torsion keys for GT3

**File:** `solver/legal_space.py:193-238`

**Severity:** BLOCKER

GT3 should have a different `TIER_A_KEYS` — coil springs at 4 corners, no torsion bar, no heave/third. The current list assumes GTP architecture. `LegalSpace.from_car()` builds dimensions for keys that don't exist on GT3 cars, populating them from `gr.front_heave_nmm` defaults of (0.0, 900.0) — a phantom searchable parameter.

**Fix shape:** Branch in `LegalSpace.from_car()`:

```python
if car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
    active_keys = list(GT3_TIER_A_KEYS)  # subset: no heave/third, no torsion
else:
    active_keys = list(TIER_A_KEYS)
```

Define `GT3_TIER_A_KEYS` excluding `front_heave_spring_nmm`, `rear_third_spring_nmm`, `front_torsion_od_mm` and including `front_spring_rate_nmm` (coil at front) instead.

### LS5 — `_build_dimension` damper sub-block is polarity-blind

**File:** `solver/legal_space.py:824-836`

**Severity:** BLOCKER

**Cars triggered:** Audi R8, McLaren 720S, Corvette Z06.

```python
damper_ranges = {
    "front_ls_comp": d.ls_comp_range,
    "front_hs_comp": d.hs_comp_range,
    ...
}
```

For Audi (LSC 2–38 INVERTED) the dimension is built with lo=2, hi=38, resolution=1.0, kind=ordinal. A "stiffer LS comp" mutation would `dim.snap(current + 5)`. On Audi this MOVES IT TOWARDS SOFTER. Local-refine and family adjustments built on top of these dimensions will drive the car the wrong way.

**Fix shape:** Add a `polarity_aware_step()` helper that flips the adjustment direction when polarity is inverted, OR (better) re-encode the click range so that internal "stiffness index" is always monotonically increasing, with the polarity flip applied only at write-time in `output/setup_writer.py`.

### CS6 / CS7 — Hardcoded `lo=0, hi=20` damper bounds in family adjustments

**File:** `solver/candidate_search.py:711-716`

**Severity:** BLOCKER

```python
_adjust_integer(targets["step6"][corner_name], "hs_comp", int(round(1.5 * front_support * family_intensity)), lo=0, hi=20)
```

The `hi=20` is suspect for ALL cars — it's wider than every GT3 car's actual range (max is McLaren HSC 0–50). For BMW/Aston/Porsche (0–11), it allows the family to push values into illegal territory before snap. For Audi (LSC 2–38 INVERTED), the +N adjustment pushes towards STIFFER on a higher-stiffer assumption but produces SOFTER on Audi.

**Fix shape:** Pull bounds from `car.damper.{hs_comp,ls_rbd}_range` per axle, AND respect polarity:

```python
hs_comp_range = car.damper.hs_comp_range
polarity_sign = +1 if car.damper.click_polarity == "higher_stiffer" else -1
delta = polarity_sign * int(round(1.5 * front_support * family_intensity))
_adjust_integer(targets["step6"][corner_name], "hs_comp", delta,
                lo=hs_comp_range[0], hi=hs_comp_range[1])
```

### MD2 / MD3 — `modifiers.compute_modifiers` reads `car.heave_spring.*`

**File:** `solver/modifiers.py:121-126, 211`

**Severity:** BLOCKER

```python
_range = car.heave_spring.front_spring_range_nmm   # GT3: heave_spring=None → AttributeError
_perch_baseline = car.heave_spring.perch_offset_front_baseline_mm
```

GT3 cars have `heave_spring=None`. Fallbacks only fire when `car is None`, not when `car.heave_spring is None`.

**Fix shape:**

```python
heave_spring = getattr(car, "heave_spring", None)
if heave_spring is None:
    return mods  # GT3: no heave-related modifiers apply
```

### PU1 / PU2 — `solver_steps_to_params` writes heave/torsion zero placeholders

**File:** `solver/params_util.py:46-54`

**Severity:** BLOCKER

```python
if step2 is not None:
    params["front_heave_spring_nmm"] = step2.front_heave_nmm  # GT3: 0.0
    params["rear_third_spring_nmm"] = step2.rear_third_nmm    # GT3: 0.0

if step3 is not None:
    params["front_torsion_od_mm"] = step3.front_torsion_od_mm  # GT3: 0.0
```

Even when `step2` is `HeaveSolution.null()`, `step2 is not None` is True. The code writes 0.0 placeholders into the params dict, which then flow into `ObjectiveFunction.evaluate_physics()`. The objective will compute negative spring force, division-by-zero in compliance terms (`1/heave`), or arbitrary baseline fallback values — all silent.

**Fix shape:**

```python
if step2 is not None and getattr(step2, "present", True):
    params["front_heave_spring_nmm"] = step2.front_heave_nmm
    params["rear_third_spring_nmm"] = step2.rear_third_nmm

if step3 is not None and getattr(car, "suspension_arch", None) is not SuspensionArchitecture.GT3_COIL_4WHEEL:
    # GT3 has no torsion bar — write coil spring rate instead (when available)
    params["front_torsion_od_mm"] = step3.front_torsion_od_mm
```

### CS3 — `_extract_target_maps` reads step2/step3 unguarded

**File:** `solver/candidate_search.py:312-324`

**Severity:** BLOCKER

```python
"step2": {
    "front_heave_nmm": public_output_value(car, "front_heave_nmm", s2.front_heave_nmm),
    "rear_third_nmm": public_output_value(car, "rear_third_nmm", s2.rear_third_nmm),
    ...
} if s2 is not None else {},
```

Same `s2 is not None` problem — for GT3 a null HeaveSolution is non-None but every numeric field is 0.0. The extracted target dict embeds 0.0 N/mm heave, which then survives all downstream snapping / scoring / reporting / writing.

**Fix shape:**

```python
"step2": {
    "front_heave_nmm": public_output_value(car, "front_heave_nmm", s2.front_heave_nmm),
    ...
} if s2 is not None and getattr(s2, "present", True) else {},
```

### ST5 / ST6 — `stint_model.analyze_stint` crashes on GT3

**File:** `solver/stint_model.py:691-696`

**Severity:** BLOCKER

```python
if base_heave_nmm is None:
    base_heave_nmm = float(car.front_heave_spring_nmm)  # GT3: TypeError
if base_third_nmm is None:
    base_third_nmm = float(car.rear_third_spring_nmm)   # GT3: TypeError
```

`analyze_stint()` will hard-crash on GT3 cars unless the caller manually passes `base_heave_nmm=0.0` (which would still be wrong because `find_compromise_parameters` then writes `params["front_heave_nmm"] = 0`).

**Fix shape:** Short-circuit the heave-related paths for GT3:

```python
if car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
    return _analyze_stint_gt3(car, stint_laps, fuel_levels_l, ...)
# ... GTP path
```

The GT3 stint analysis would skip heave compromise and replace it with corner-spring fuel-load compromise (different physics).

### SP1 / SP2 / SP3 — Scenario profiles GTP-only

**File:** `solver/scenario_profiles.py`

**Severity:** DEGRADED → BLOCKER (when GT3 prediction starts populating heave fields with zero)

Three GT3-relevant issues:

1. `max_front_heave_travel_used_pct=96.0` is a GTP-specific safety cap. For GT3 the equivalent is bump-rubber-gap deflection (a per-corner garage parameter on Acura/Mustang/Corvette per `docs/gt3_per_car_spec.md`). No GT3-equivalent sanity check exists.
2. Pressure ranges (160–186 kPa) seeded from BMW M Hybrid V8 IBT. GT3 BMW M4 cold pressure 159 kPa per `docs/gt3_session_info_schema.md:134`, but hot operating range PENDING_IBT.
3. Race scenario assumes GTP race lengths and 89 L stint mass curve.

**Fix shape:** Add `architecture: Literal["gtp", "gt3"] = "gtp"` field to `ScenarioProfile`, and define GT3-specific scenarios with bump-rubber-gap-based sanity checks.

## GT3-correct-as-is paths

Calling out paths that are GT3-correct (or harmless):

- **`solver/damper_solver.py:289-322` (`_damping_ratio_ls/hs`)** — Reads `d.zeta_target_*` from car model. GT3 cars currently have `zeta_is_calibrated=False`, which the strict gate at line 476 catches with a clear error. CORRECT for GT3.
- **`solver/damper_solver.py:374-436` (`_hs_slope_from_surface`)** — Reads track p99/p95 ratios; physics is architecture-independent. CORRECT for GT3 (assumes the car model's `hs_slope_range` is correct).
- **`solver/damper_solver.py:786-892` (roll-damper block)** — Gated on `self.car.damper.has_roll_dampers`. All 11 GT3 cars have `has_roll_dampers=False, has_front_roll_damper=False, has_rear_roll_damper=False` (verified at `car_model/cars.py:3269-3271, 3387-3389, 3514-3516`). The block is correctly skipped. CORRECT for GT3.
- **`solver/damper_solver.py:937-963` (heave-damper block)** — Gated on `self.car.damper.has_heave_dampers`. GT3 cars don't set this — Ferrari 296 GT3 might in future, but that's a per-car opt-in. CORRECT for GT3.
- **`solver/legality_engine.py:175-181` (range_checks loop)** — Uses `if key not in params: continue` — silent skip when GT3 omits a heave key. CORRECT for GT3 IF the candidate generator stops emitting those keys (see CS3, CS4, PU1).
- **`solver/scenario_profiles.py:236-243` (sanity numeric_limits loop)** — Uses `getattr(prediction, attr, None)` then `if value is None: continue`. GT3 prediction objects without heave fields will gracefully skip the heave-related caps. SAFE for GT3 today.
- **`solver/legal_space.py:298-310` (`SearchDimension.snap/clamp`)** — Pure mathematical — no architecture assumption. CORRECT.
- **`solver/heave_solver.py:112-143` (`HeaveSolution.null()`)** — Already exists and sets `present=False`. CORRECT — but every consumer (params_util, candidate_search, decision_trace) needs to actually CHECK `present` before reading numeric fields. See PU1, CS3.
- **`solver/decision_trace.py:283-285`** — Catches AttributeError and silently `continue` when a parameter spec lambda fails. SAFE for GT3 in that it doesn't crash, but DEGRADED in that it silently drops decisions instead of explaining "GT3 has no heave/third — N/A".

## Risk summary

| Risk class | Count | Cars exposed |
|---|---|---|
| Crash on GT3 (`AttributeError`/`TypeError`) | 5 (LS1, LS2, MD2, MD3, ST6) | All 11 GT3 cars, hard-blocks pipeline |
| Silent zero placeholder for heave/torsion fields | 6 (F1, CS1, CS3, CS5, PU1, PU2) | All 11 GT3 cars, distorts scoring/output |
| Damper polarity wrong (lower=stiffer cars) | 3 (L4, LS5, CS7) | Audi R8, McLaren 720S, Corvette Z06 |
| Damper click range too narrow / wide | 1 (CS6) | All 11 (range mismatch); 3 cars (polarity) |
| Per-corner vs per-axle mismatch | 2 (F2, PU3) | All 11 GT3 cars (cosmetic) |
| Fuel-cap/scenario assumptions stale | 4 (F3, F4, ST1, ST3, SP3) | All 11 GT3 cars (small numerical drift) |
| GT3-aware sanity caps absent | 1 (SP1, SP2) | All 11 GT3 cars (latent) |
| Decision trace silently drops GT3 rows | 2 (DT1, DT2) | All 11 GT3 cars (UX) |

**Highest-impact blockers (pipeline crashes):** LS1, LS2, MD2, MD3, ST6. The first GT3 end-to-end run will crash in `compute_perch_offsets` or `compute_modifiers` or `analyze_stint`.

**Highest-impact correctness issues:** F1 (silent wrong critical-damping for all 11 cars), L4/CS7 (damper polarity flipped for 3 cars).

## Effort estimate

| Phase | Scope | Effort |
|---|---|---|
| 1. Crash fixes | LS1, LS2, MD2, MD3, ST6, F1 — add GT3 architecture short-circuits | 0.5 day |
| 2. Param dict guards | PU1, PU2, CS3, CS4 — guard on `step2.present` and `suspension_arch` | 0.5 day |
| 3. Damper polarity | L4, LS5, CS6, CS7 — add `click_polarity` field to `DamperModel`; wire through legality + family adjustment | 1 day |
| 4. Per-axle damper output | F2, PU3 — collapse L/R asymmetry for GT3; emit per-axle in writer | 0.5 day (writer is out of scope) |
| 5. Scenario profiles | SP1, SP2, SP3 — add GT3 scenario set with GT3-specific sanity caps (bump-rubber-gap based) | 1 day (but PENDING IBT bump-rubber range data) |
| 6. Stint model GT3 path | ST3, ST5, ST6, ST7 — `_analyze_stint_gt3()` separate from GTP | 1 day |
| 7. Decision trace GT3 spec | DT1, DT2, DT3 — surface "N/A for GT3" instead of silent drop | 0.5 day |
| 8. Cosmetic | F3, F4, F5, F6, ST1, ST2 — fuel default, constraint targets, summary header | 0.5 day |

**Total: ~5.5 days** assuming no further bugs surface during integration. Phase 1 is mandatory before any GT3 pipeline run.

## Dependencies

- **`docs/gt3_phase2/setup-writer.md`** (parallel audit) — must add per-axle GT3 damper output, GT3 PARAM_IDS dispatch table.
- **`docs/gt3_phase2/car-model.md`** (parallel audit) — must add `click_polarity` field to `DamperModel`. All polarity-aware fixes (L4, LS5, CS7) depend on this.
- **`car_model/cars.py`** — Porsche 992 GT3R click range needs verification: 0–11 vs 0–12. Aston damper range PENDING_IBT.
- **`solver/heave_solver.py`** — already provides `HeaveSolution.null()` with `present=False`. Phase 2 of consumers must actually read `present`.
- **`car_model/setup_registry.py`** (parallel audit) — GT3 field registry must NOT include heave/third/torsion keys; CarFieldSpec for GT3 must omit them. Otherwise the legal-space TIER_A_KEYS path stays leaky.
- **GT3 scenario calibration data** — bump-rubber-gap typical values, hot pressure ranges, stint length norms — currently PENDING_IBT for all 11 GT3 cars. Phase 5 (scenario profiles) blocked on this.
- **Wheel-force telemetry** for damper polarity validation — currently no IBT click-sweep for inverted-polarity cars (Audi/McLaren/Corvette). Polarity inference relies on user manuals only.
