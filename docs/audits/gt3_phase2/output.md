# GT3 Phase 2 Audit — Unit: `output/`

Setup writer (`output/setup_writer.py`), bundle (`output/bundle.py`), garage validator (`output/garage_validator.py`), and engineering report (`output/report.py`).

## Scope

Files audited (line counts):

| File | LOC | Role |
|---|---|---|
| `output/setup_writer.py` | 1,235 | XML `.sto` writer; 5 per-car `_PARAM_IDS` dicts; dispatch via `car_canonical.lower()` |
| `output/bundle.py` | 328 | Single-directory artifact bundler (sto + json + report + manifest) |
| `output/garage_validator.py` | 615 | Pre-write range clamping + garage-output correlation guard |
| `output/report.py` | 1,086 | ASCII engineering report formatter |

Reference docs consulted: `docs/gt3_session_info_schema.md`, `docs/gt3_per_car_spec.md`, the three Spielberg session-info YAMLs (BMW M4 GT3, Aston Vantage, Porsche 992 GT3 R), `car_model/cars.py` (SuspensionArchitecture, GT3 stubs).

The audit's central question: **what does `output/` need to do for a GT3 car (no Step 2, coil-only 4-corner, per-axle dampers, divergent YAML field names) before a `.sto` write of an 11-car GT3 grid is safe?**

## Summary table

| # | Severity | File | Symbol / line | Issue |
|---|---|---|---|---|
| O1 | BLOCKER | `output/setup_writer.py:507-513` | `_CAR_PARAM_IDS` dispatch | No GT3 entry for any of 11 GT3 canonical names; `write_sto()` raises `ValueError("No STO parameter ID mapping for car: …")` for every GT3 car |
| O2 | BLOCKER | `output/setup_writer.py:817` | `ids = _CAR_PARAM_IDS.get(car_canonical.lower())` | Dispatch is by `car_canonical` short name (bmw/ferrari/porsche/cadillac/acura). GT3 canonical names are `bmw_m4_gt3`, `aston_martin_vantage_gt3`, `porsche_992_gt3r`, … → `.lower()` does not collapse them to existing entries; collision risk: `porsche_992_gt3r.lower() != "porsche"` (correct), but a future call site might naively pass the GTP slug |
| O3 | BLOCKER | `output/setup_writer.py:846-847` | `is_porsche = car_canonical.lower() == "porsche"` | Polarity check is on the GTP Porsche 963. GT3 Porsche 992 has no Rear3rd damper, no roll dampers, no front roll spring — yet `is_porsche=True` will fire if anyone routes the GT3 Porsche through the `"porsche"` dispatch. Same brittle pattern at `:725` (`canonical_name == "ferrari"`) and `:845` (`is_acura = car_canonical.lower() == "acura"`) |
| O4 | BLOCKER | `output/setup_writer.py:885-889` | Heave/third XML write | Unconditionally emits `front_heave_spring`, `front_heave_perch`, `rear_third_spring`, `rear_third_perch`. For GT3 (`SuspensionArchitecture.GT3_COIL_4WHEEL`) Step 2 returns `HeaveSolution.null()` with all heave fields = 0 → writes `<HeaveSpring Value="0" Unit="N/mm">` which iRacing will silently accept then display "Spring rate: 0 N/mm". Need GT3 guard `if step2.present:` AND no GT3 heave param IDs |
| O5 | BLOCKER | `output/setup_writer.py:1109-1130` | Per-corner damper write loop | All 5 cars treat dampers as 4 independent corners (LF/RF/LR/RR × 5 channels = 20 writes). GT3 IBT YAML exposes dampers per-axle (8 channels: F LSC/HSC/LSR/HSR + R LSC/HSC/LSR/HSR). Writing per-corner XML IDs that don't exist in the GT3 garage schema → iRacing rejects file or silently drops fields |
| O6 | BLOCKER | `output/setup_writer.py:891-933` | Front torsion bar block | `_w_num("lf_torsion_od", …)` and `_w_num("lf_torsion_turns", …)` always write. GT3 cars have NO torsion bars (architecture invariant; `step3.front_torsion_od_mm` is unset/zero). The `car_canonical.lower() == "porsche"` Porsche-963 escape clause sets `_tb_turns = 0.0` but still writes `lf_torsion_od / rf_torsion_od` whose values are meaningless for GT3. Need `if SuspensionArchitecture.GT3_COIL_4WHEEL: skip torsion bar block entirely` |
| O7 | BLOCKER | `output/setup_writer.py:881-883` | Pushrod offset write | `_w_num("front_pushrod_offset", …)` snapped to half-mm. GT3 cars use perch-offset RH workflow but the field name in the GT3 YAML is `BumpRubberGap` per-corner + `CenterFrontSplitterHeight`. There is no `Front_PushrodLengthOffset` in any of the 3 sampled GT3 YAMLs |
| O8 | BLOCKER | `output/setup_writer.py:891-895` | Front torsion / corner spring | Step 3 outputs for GT3 must be 4 independent coil rates (`lf_spring_rate_nmm`, `rf_spring_rate_nmm`, `lr_spring_rate_nmm`, `rr_spring_rate_nmm`). The current `CornerSpringSolution` only has `front_torsion_od_mm` (single front) and `rear_spring_rate_nmm` (single rear, copied L/R). The XML must write 4 separate `Chassis.LeftFront.SpringRate` / `RightFront` / `LeftRear` / `RightRear` values, with paired or independent semantics depending on garage UI |
| O9 | BLOCKER | `output/setup_writer.py:1085-1090` | ARB write | Writes `front_arb_size` (string) + `front_arb_blades` (int). GT3 cars: BMW uses `Chassis.FrontBrakes.ArbBlades` (int), Aston uses `Chassis.FrontBrakesLights.FarbBlades` (int), **Porsche 992 uses `Chassis.FrontBrakesLights.ArbSetting` (single integer, NOT blade — completely different concept)**. The single ARB-size string field is GTP-only (Porsche 963 has 35/45 mm ARB sleeve). GT3 has no `front_arb_size` field on any car |
| O10 | BLOCKER | `output/setup_writer.py:1102-1107` | Toe write | BMW/Aston GT3: front uses `TotalToeIn` (paired), rear uses per-wheel `LeftRear.ToeIn` + `RightRear.ToeIn`. **Porsche 992 GT3 R: rear toe is `Chassis.Rear.TotalToeIn` (paired axle, NOT per-wheel)** — unique among the GT3s. The current writer chooses between Acura's `rear_toe` and BMW's `lr_toe`/`rr_toe`; needs a third Porsche-992 path |
| O11 | BLOCKER | `output/setup_writer.py:1175-1183` | Tyre + fuel | Tyre paths assumed `TiresAero.{LeftFront,RightFront,LeftRearTire,RightRearTire}.StartingPressure`. GT3 YAML uses `TiresAero.{LeftFront,LeftRear,RightFront,RightRear}` (no `Tire` suffix). **Fuel:** `fuel_level` ID `BrakesDriveUnit_Fuel_FuelLevel` does not exist in GT3 — BMW/Aston put it in `Chassis.Rear.FuelLevel`, **Porsche puts it in `Chassis.FrontBrakesLights.FuelLevel`** |
| O12 | BLOCKER | `output/setup_writer.py:1199-1200` | TC label format | Writes `tc_gain` / `tc_slip` as bare integers. GT3 `TcSetting` is an indexed STRING with a label suffix: BMW = `"4 (TC)"`, Aston = `"5 (TC SLIP)"`, Porsche = `"3 (TC-LAT)"`. Same for ABS = `"5 (ABS)"`. Bare-integer write is rejected or ignored by iRacing's GT3 schema |
| O13 | BLOCKER | `output/setup_writer.py:1183-1212` | Brake/diff/TC paths | All `BrakesDriveUnit_*` and `Systems_*` paths. GT3 schema collapses these into `Chassis.InCarAdjustments` (BrakePressureBias, AbsSetting, TcSetting, FWtdist, CrossWeight, plus aston-only ThrottleResponse/EpasSetting, porsche-only ThrottleShapeSetting/DashDisplayPage) and `Chassis.GearsDifferential` (GearStack, FrictionFaces, DiffPreload). None of the existing PARAM_IDS path prefixes match |
| O14 | BLOCKER | `output/setup_writer.py:516-610` | `_validate_setup_values` | Clamps `front_heave_nmm`, `rear_third_nmm`, `front_torsion_od_mm` from `gr.front_heave_nmm`, etc. For GT3 these `garage_ranges` fields will be 0/0 or unset (no heave springs). The clamp `_clamp_field(step2, "front_heave_nmm", *gr.front_heave_nmm, ...)` will either crash on missing field or silently clamp the GT3's null heave to range[0]. Needs an early bail when `suspension_arch == GT3_COIL_4WHEEL` |
| O15 | BLOCKER | `output/setup_writer.py:561-564` | `_validate_setup_values` heave clamp | `_clamp_field(step2, "front_heave_nmm", *gr.front_heave_nmm, "front_heave", " N/mm")` and same for `rear_third_nmm` / both perches. With `step2 = HeaveSolution.null()` and `gr.front_heave_nmm = (0, 0)` this becomes a no-op but the clamp still mutates. Brittle — explicit GT3 skip required |
| O16 | BLOCKER | `output/garage_validator.py:127` | `_clamp_step2(step2, gr)` | Always called regardless of car. Reads `step2.front_heave_nmm`, `step2.perch_offset_front_mm`, `step2.rear_third_nmm`, `step2.perch_offset_rear_mm`. For GT3, all four are 0/None (Step 2 returned `HeaveSolution.null()`). Should early-bail with "GT3 has no heave/third — skip step2 clamping" |
| O17 | BLOCKER | `output/garage_validator.py:179-186` | `garage_model.validate(state, front_excursion_p99_mm=step2.front_excursion_at_rate_mm)` | `step2.front_excursion_at_rate_mm` does not exist on a GT3 `HeaveSolution.null()` (or returns 0). The garage validation will report a phantom 0-mm excursion. GT3 has no heave; excursion concept is replaced by `BumpRubberGap`-managed travel — different physics, different validator |
| O18 | BLOCKER | `output/garage_validator.py:288-310` | `_clamp_step2` body | Reads `gr.front_heave_nmm`, `gr.rear_third_nmm`, `gr.heave_spring_resolution_nmm`, `gr.front_heave_perch_mm`, `gr.rear_third_perch_mm`, `gr.front_heave_perch_resolution_mm`, `gr.rear_third_perch_resolution_mm`. GT3 `GarageRanges` does not have any of these defined → AttributeError or zero-tuple no-op. Needs GT3 short-circuit |
| O19 | BLOCKER | `output/garage_validator.py:386-436` | `_fix_slider` heave-slider correction | Reads `garage_model.max_slider_mm`, mutates `step2.perch_offset_front_mm` and `step2.front_heave_nmm`. GT3 has no heave slider concept — entirely irrelevant for GT3 validation |
| O20 | BLOCKER | `output/garage_validator.py:520-580` | `_fix_torsion_bar_defl` | Mutates `step3.front_torsion_od_mm` from a discrete options list. GT3 has no torsion bars; should be skipped |
| O21 | BLOCKER | `output/garage_validator.py:439-517` | `_fix_front_rh` calls `front_pushrod_for_static_rh` | Reads `front_heave_nmm`, `front_heave_perch_mm`, `front_torsion_od_mm` from GT3-null step2/step3. GT3 RH-correction lever is the spring perch, not pushrod-offset. Needs GT3-specific fixer (not "skip") |
| O22 | DEGRADED | `output/garage_validator.py:160-177` | "no calibrated GarageOutputModel" suppression list | Allow-lists `('bmw', 'ferrari')` for warning suppression. New GT3 cars (`bmw_m4_gt3`, `porsche_992_gt3r`, …) will receive the noisy info-warning at every write until each gets a calibrated `GarageOutputModel`. Allow-list needs to grow per car or convert to `getattr(car, 'has_calibrated_garage_model', False)` |
| O23 | BLOCKER | `output/setup_writer.py:1132-1162` | Roll-damper block | `is_acura or is_porsche` (GTP names) — guards roll-damper writes. GT3 cars have **NO roll dampers** (`has_roll_dampers=False, has_front_roll_damper=False, has_rear_roll_damper=False`). With current dispatch, GT3 Porsche routed via canonical `porsche_992_gt3r` will not match `is_porsche` (since that test is `== "porsche"`), so the block is dead-code for GT3 — but if a future caller normalizes the canonical name to "porsche" the block fires phantom roll-damper writes. Needs explicit GT3 architecture guard |
| O24 | BLOCKER | `output/setup_writer.py:1163-1173` | Rear-3rd damper block | `if is_porsche` — only runs for GTP Porsche 963. Phantom output risk if canonical name normalized to "porsche" for GT3. Same fix as O23 |
| O25 | BLOCKER | `output/setup_writer.py:954-957` | Porsche front roll spring write | `if "lf_roll_spring" in ids` writes `step3.front_wheel_rate_nmm` to `Chassis.LeftFront.RollSpring`. GT3 Porsche 992 has NO roll spring (4 coil-overs). Same dispatch-collision risk |
| O26 | DEGRADED | `output/setup_writer.py:691-708` | `_get_car(car_canonical)` | Wraps `_get_car` in `try/except: _car = None`. Silent failure here cascades: garage validation skipped, brake bias falls back to magic 56.0%, corner weights = 0. GT3 cars are not yet registered in `_CAR_REGISTRY` lookup by short canonical → silent None car. Should explicitly fail loud for GT3 |
| O27 | DEGRADED | `output/setup_writer.py:893-899` | Ferrari int-cast for torsion OD | `int(round(step3.front_torsion_od_mm)) if car_canonical.lower() == "ferrari" else …` — string-equality car branch. Should be `if car.suspension_arch.uses_indexed_torsion_bar` or similar. Will need a GT3 branch added per car (or rather, removed) |
| O28 | DEGRADED | `output/report.py:88` | `from solver.heave_solver import HeaveSolution` | Imported but used everywhere implicitly. For GT3 (architecture has `heave_spring=None`), the report layer reads `step2.front_heave_nmm`, `step2.rear_third_nmm`, `step2.perch_offset_front_mm`, `step2.perch_offset_rear_mm`, `step2.travel_margin_front_mm`, `step2.front_bottoming_margin_mm`, `step2.front_excursion_at_rate_mm` directly with no GT3 branch. With `HeaveSolution.null()` these are 0 / None → report renders "Heave F: 0 N/mm" / "Third R: 0 N/mm" garbage |
| O29 | DEGRADED | `output/report.py:292-295` | `display_front_heave = float(public_output_value(car, "front_heave_nmm", step2.front_heave_nmm))` | Same — unconditionally reads heave/third for display. Need GT3 branch: render 4 corner spring rates instead |
| O30 | DEGRADED | `output/report.py:574-575` | `step2.slider_static_front_mm`, `step2.travel_margin_front_mm` | Hard-references HeaveSolution-only fields. GT3 will print "Heave slider: 0.0 mm    Travel margin: 0.0 mm" — misleading on a GT3 report |
| O31 | DEGRADED | `output/report.py:619-621` | Garage card heave/third strings | `f"  Heave F:    {step2.front_heave_nmm:5.0f} N/mm  perch {step2.perch_offset_front_mm:+.0f}mm"` — same. Need GT3 branch that prints `LF/RF/LR/RR Spring: NNN/NNN/NNN/NNN N/mm` instead |
| O32 | DEGRADED | `output/report.py:950-952` | Engineering summary "Heave: …" | Same hard reference. Need GT3 branch |
| O33 | DEGRADED | `output/report.py:1018-1029` | Current vs recommended diff table | Hardcoded `front_heave_nmm`, `rear_third_nmm`, `torsion_bar_od_mm` keys. GT3 setup-comparison must compare 4 corner spring rates and the perches under each LF/RF/LR/RR |
| O34 | DEGRADED | `output/setup_writer.py:1008-1014` | Deflection display fallback | `_dm.rear_spring_defl_static(step3.rear_spring_rate_nmm, …, third_rate_nmm=step2.rear_third_nmm, third_perch_mm=step2.perch_offset_rear_mm, …)`. GT3 has no third spring contribution — physics formula is wrong for GT3 |
| O35 | DEGRADED | `output/setup_writer.py:1016-1033` | Deflection display heuristic fallback | `_heave_defl_static = round(40.5 + (-0.55) * _fh, 1)` — BMW M Hybrid V8 GTP regression intercepts. Will silently produce wrong numbers if a GT3 falls into this branch |
| O36 | DEGRADED | `output/setup_writer.py:1218-1224` | Speed-in-gear display defaults | Hardcoded BMW M Hybrid V8 GTP values (`116/151/184/220/257/288/316 km/h`). GT3 cars have very different gear ratios (BMW M4 GT3 redline 7250 vs Aston 7000 vs Porsche 9500 — 30% range) — these defaults will display laughably wrong gear speeds. Should be PENDING per-car or pass-through-from-IBT |
| O37 | DEGRADED | `output/setup_writer.py:1132-1136` | Roll-damper docstring | Comment mentions "Porsche has FRONT roll damper but NO rear roll damper" — pre-GT3 statement that no longer holds for GT3 Porsche (which has zero roll dampers). Documentation drift |
| O38 | DEGRADED | `output/setup_writer.py:570-574` | Corner-spring clamp | `if gr.front_torsion_od_mm != (0.0, 0.0)` — uses tuple-equality with 0.0. For GT3 the GarageRanges tuple is unset/`(0.0, 0.0)` so the if-block is correctly skipped, BUT then `step3.rear_spring_rate_nmm` is clamped against `gr.rear_spring_nmm` — and GT3 has 4 different corner spring ranges (per-corner, not paired-rear). Clamps the wrong axle |
| O39 | DEGRADED | `output/setup_writer.py:825-831` | `_w_num` TODO comment fallback | Unmapped param emits XML comment `<!-- TODO: {car_canonical} {param} not mapped -->`. For GT3 with no `_PARAM_IDS` dict → ValueError at `:818` long before this fires. But once GT3 dicts exist, every unmapped GT3-specific field (CenterFrontSplitterHeight, BumpRubberGap × 4 corners, EnduranceLights, EpasSetting, ThrottleResponse, DashDisplayPage, NightLedStripColor, ThrottleShapeSetting) silently becomes a TODO comment. iRacing will reject the .sto if required GT3 fields are missing |
| O40 | DEGRADED | `output/setup_writer.py:597-608` | Damper clamp — per-corner, not per-axle | `_clamp_int_field(corner, "ls_comp", *d.ls_comp_range, …)` for `lf, rf, lr, rr` independently. GT3 dampers are per-axle (left/right tied) — but the solver still produces 4 corners. Need to either average or take left-side as authoritative AND rely on `damper_click_polarity` (lower=stiff for Audi/McLaren/Corvette). Polarity dispatch missing |
| O41 | DEGRADED | `output/setup_writer.py:602-608` | Damper fallback range | `d_lo, d_hi = gr.damper_click` — single tuple for all 5 channels. GT3 manual confirms LSC/HSC have different ranges on McLaren (40 vs 50), Audi (38 vs 40), Corvette (30 vs 22). Per-channel ranges already supported by `DamperModel.{ls_comp_range, hs_comp_range, ...}` (used at `:597-601`) but the fallback path collapses them — will under-clamp valid values for GT3 cars without a populated DamperModel |
| O42 | DEGRADED | `output/report.py:579-583` | Aero gradient block (referenced by task brief) | Lines 579-583 are actually brake-target/migration formatting, not aero. Task brief description "lines 579-583 aero model uses heave coefficients" appears outdated — current report does not lean on heave coefficients here. Worth a re-read before fixing |
| O43 | COSMETIC | `output/setup_writer.py:1024-1030` | "No calibrated model; skip display value" → `0.0` | Falls through to literal 0 with no warning emitted for GT3. Silent zero output |
| O44 | COSMETIC | `output/bundle.py:97-107` | `_make_stem` | track_slug capped at 30 chars — fine. No GT3-specific issue but worth noting GT3 + new circuits like Spielberg/Red Bull Ring will produce stems like `bmw_m4_gt3_red_bull_ring_full_circuit_20260426` truncated to 30 chars: `bmw_m4_gt3_red_bull_ring_full` — will collide if multiple configs |
| O45 | COSMETIC | `output/bundle.py:159-216` | bundle Ferrari special-case kwargs | `if car_name == "ferrari":` branch builds extra kwargs. GT3 cars need their own per-car kwarg branches (Aston EpasSetting/ThrottleResponse, Porsche ThrottleShapeSetting/DashDisplayPage). Currently a non-GT3-Ferrari car silently drops these |

## Findings

### O1 / O2 — `_CAR_PARAM_IDS` has no GT3 entry; dispatch by short name

`_CAR_PARAM_IDS` (`output/setup_writer.py:507-513`) contains only `bmw`, `ferrari`, `porsche`, `cadillac`, `acura`. `_CAR_PARAM_IDS.get(car_canonical.lower())` at `:817` returns `None` for every GT3 canonical name (`bmw_m4_gt3`, `aston_martin_vantage_gt3`, `porsche_992_gt3r`, `mercedes_amg_gt3`, `ferrari_296_gt3`, `lamborghini_huracan_gt3`, `mclaren_720s_gt3`, `acura_nsx_gt3`, `audi_r8_gt3`, `mustang_gt3`, `corvette_z06_gt3`).

```python
# current
ids = _CAR_PARAM_IDS.get(car_canonical.lower())
if ids is None:
    raise ValueError(f"No STO parameter ID mapping for car: {car_canonical}")
```

GT3 needs three new dicts (`_BMW_M4_GT3_PARAM_IDS`, `_ASTON_MARTIN_VANTAGE_GT3_PARAM_IDS`, `_PORSCHE_992_GT3R_PARAM_IDS`) registered into the dispatch table, plus eventually 8 more for the remainder of the GT3 grid. See **proposed PARAM_IDS dicts** at the bottom of this doc.

### O4 / O5 / O6 / O8 — Heave/third/torsion/per-corner damper writes are unconditional

`output/setup_writer.py:885-889` writes `front_heave_spring`, `front_heave_perch`, `rear_third_spring`, `rear_third_perch` for every car. GT3 architecture sets `heave_spring=None` and Step 2 returns `HeaveSolution.null()` → all values are 0. iRacing has no `Front_HeaveSpring` field for GT3.

Same pattern at `:891-933` for torsion bars (`lf_torsion_od`, `lf_torsion_turns`, `rf_torsion_od`, `rf_torsion_turns`) and `:1109-1130` for per-corner dampers (5 channels × 4 corners = 20 writes). GT3 dampers are **per-axle**: 4 channels × 2 axles = 8 writes total.

Required fix shape:

```python
from car_model.cars import SuspensionArchitecture

is_gt3 = (_car is not None
          and _car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL)

if not is_gt3:
    # GTP path — heave, third, torsion bar, per-corner dampers
    _w_num("front_heave_spring", int(round(step2.front_heave_nmm)), "N/mm")
    # ... (rest of existing block)
else:
    # GT3 path: 4 independent coil rates, no heave/third, per-axle dampers
    _w_num("lf_spring_rate", int(round(step3.lf_spring_rate_nmm)), "N/mm")
    _w_num("rf_spring_rate", int(round(step3.rf_spring_rate_nmm)), "N/mm")
    _w_num("lr_spring_rate", int(round(step3.lr_spring_rate_nmm)), "N/mm")
    _w_num("rr_spring_rate", int(round(step3.rr_spring_rate_nmm)), "N/mm")
    # Per-axle dampers
    _w_num("front_ls_comp", step6.front.ls_comp, "clicks")  # etc.
```

Note this requires upstream `CornerSpringSolution` to grow `lf/rf/lr/rr_spring_rate_nmm` (currently single `front_torsion_od_mm` + paired `rear_spring_rate_nmm`) and `DamperSolution` to grow `front`/`rear` per-axle objects (currently `lf/rf/lr/rr` per-corner) — coordinate with the solver-unit auditors.

### O9 / O11 / O12 / O13 — Per-car GT3 YAML divergence

The 3 sampled GT3 YAMLs disagree on field names AND section paths for the same concept. From the schema doc:

| Concept | BMW M4 GT3 | Aston Vantage | Porsche 992 GT3 R |
|---|---|---|---|
| Aero balance section | `TiresAero.AeroBalanceCalc` | `TiresAero.AeroBalanceCalculator` | `TiresAero.AeroBalanceCalc` |
| Wing field (in chassis rear) | `WingAngle` | `RearWingAngle` | `WingSetting` |
| Front brakes section | `Chassis.FrontBrakes` | `Chassis.FrontBrakesLights` | `Chassis.FrontBrakesLights` |
| Front ARB | `ArbBlades` (int blade) | `FarbBlades` (int blade) | **`ArbSetting` (int — NOT blade)** |
| Rear ARB | `ArbBlades` in `Rear` | `RarbBlades` in `Rear` | **`RarbSetting` in `Rear`** |
| Rear toe | per-wheel `LeftRear.ToeIn` | per-wheel `LeftRear.ToeIn` | **`Chassis.Rear.TotalToeIn` (paired)** |
| Fuel | `Chassis.Rear.FuelLevel` | `Chassis.Rear.FuelLevel` | **`Chassis.FrontBrakesLights.FuelLevel`** |
| TC label | `"4 (TC)"` | `"5 (TC SLIP)"` | `"3 (TC-LAT)"` |

Implication: **per-car PARAM_IDS dicts are mandatory for GT3** — a "GT3 base + minor overrides" approach is insufficient because the Porsche 992 diverges in 5+ structural ways. The 8 remaining GT3 cars likely cluster around the BMW or Aston templates with localised overrides; spec sheet's "ARB encoding varies" / "TC/ABS off position varies" / "damper polarity varies" cross-cuts confirm this.

### O14 / O15 / O18 — Validators read undefined/null heave fields for GT3

`output/setup_writer.py:_validate_setup_values` and `output/garage_validator.py:_clamp_step2` both read `step2.front_heave_nmm`, `step2.rear_third_nmm`, plus the perches and `step2.front_excursion_at_rate_mm`. For GT3 these are 0 / None / missing.

Required fix: at the top of each function, early-return when `_car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL`:

```python
def validate_and_fix_garage_correlation(car, step1, step2, step3, step5, fuel_l, track_name=None):
    warnings: list[str] = []
    if car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
        # GT3: no heave, no torsion, no third-spring slider — different validation surface
        warnings.extend(_clamp_step1(step1, car.garage_ranges))
        warnings.extend(_clamp_gt3_step3_corners(step3, car.garage_ranges))  # NEW
        if step5 is not None:
            warnings.extend(_clamp_step5(step5, car.garage_ranges))
        # GT3 garage check: bump-rubber gap, splitter height, RH floor — NEW fixers
        return warnings
    # ... existing GTP code
```

### O17 / O19 / O20 / O21 — Garage-correlation fixers reference GTP-only physics

`_fix_slider`, `_fix_torsion_bar_defl`, `_fix_front_rh` all mutate `step2`/`step3` in directions only meaningful for GTP (heave perch ± step, torsion OD ± step). GT3's RH-correction levers are `BumpRubberGap` per corner, `CenterFrontSplitterHeight`, and `SpringPerchOffset` per corner. Brand-new fixer set required.

### O22 — `('bmw', 'ferrari')` allow-list will spam GT3 warnings

```python
canonical = getattr(car, 'canonical_name', '')
if canonical not in ('bmw', 'ferrari'):
    warnings.append("NOTE: Garage correlation validation skipped for {canonical} ...")
```

GT3 cars (`bmw_m4_gt3`, `porsche_992_gt3r`) are not in this allow-list. Until each gets a calibrated `GarageOutputModel` (5+ varied IBT sessions per car needed), every `.sto` write emits this notice. Suggest converting to `if not getattr(car, 'has_calibrated_garage_model', False)` driven by a per-car model registry flag, or adding a second allow-list for "scaffold-stage GT3 cars where lack of calibration is expected and silent is fine".

### O23 / O24 / O25 — GTP roll/3rd damper writes guarded by string-equality

```python
is_acura = car_canonical.lower() == "acura"
is_porsche = car_canonical.lower() == "porsche"
has_roll_dampers = is_acura or is_porsche
```

Two latent bugs:
1. If a future caller normalizes the GT3 Porsche canonical to `"porsche"` for any reason (logging, registry hit, file naming), `is_porsche=True` fires on the GT3 → phantom `RearRoll`/`Rear3rd` damper writes for a car that has neither.
2. The architecture-correct guard is `_car.damper.has_roll_dampers` etc. — should already exist on the DamperModel. Use those flags directly instead of canonical-name string equality.

### O26 — Silent fallback when `_get_car()` fails

```python
try:
    _car = _get_car(car_canonical)
except Exception:
    _car = None
```

For GT3 cars not yet registered, this swallows the `KeyError` and proceeds with `_car=None` → garage validation skipped, brake bias falls to magic 56.0%, corner weights = 0. Per CLAUDE.md Key Principle 7 ("calibrated or instruct, never guess") and Principle 8 ("no silent fallbacks"), this should raise loudly for GT3. Existing GTP cars rely on the silent fallback so a wholesale change is risky; recommended pattern:

```python
try:
    _car = _get_car(car_canonical)
except Exception:
    _car = None
    if "_gt3" in car_canonical.lower() or car_canonical.lower().endswith("gt3"):
        raise  # GT3 must not silent-fallback
```

### O28 / O29 / O30 / O31 / O32 / O33 — Report layer reads heave/third unconditionally

`output/report.py` references step2.front_heave_nmm, step2.rear_third_nmm, step2.perch_offset_front_mm, step2.perch_offset_rear_mm, step2.travel_margin_front_mm, step2.front_bottoming_margin_mm, step2.front_excursion_at_rate_mm, step2.slider_static_front_mm in 12+ locations. None branch on architecture.

For GT3, the report should:
1. Replace the SPRINGS column of the GARAGE CARD with 4 corner spring rates + 4 perch offsets + 4 bump-rubber gaps.
2. Remove "Heave slider" / "Travel margin" / "Front bottoming margin" rows (no heave).
3. Replace "Heave/Third/Torsion" diff-table rows with per-corner spring diffs.
4. Render 4-channel × 2-axle damper grid instead of 5-channel × 4-corner.
5. Display GT3-only fields: `BumpRubberGap` per corner, `CenterFrontSplitterHeight`, `BrakePads` (Low/Medium/High), `FrictionFaces`, `GearStack`.

### O34 / O35 — Deflection display fallback is BMW-GTP regression

`_heave_defl_static = round(40.5 + (-0.55) * _fh, 1)` — those coefficients are the BMW M Hybrid V8 GTP fit. For any GT3 car falling into this fallback (e.g. uncalibrated `_car.deflection`), the displayed value is meaningless. Also irrelevant since GT3 has no heave.

### O36 — Hardcoded gear-speed defaults are BMW GTP

`speed_in_first=116, speed_in_seventh=316 km/h` — those are BMW M Hybrid V8 short-stack values. GT3 cars are 200-300 km/h top speed, fewer gears, very different progressions. Either pass through from IBT, mark PENDING per-car, or omit when unknown.

### O40 / O41 — Damper polarity not dispatched

GT3 spec table:

| Car | Polarity | LSC range | HSC range |
|---|---|---|---|
| BMW/Aston/Ferrari/Lambo/Mustang | higher = stiffer | 0–11 | 0–11 |
| Acura NSX | higher = stiffer | 1–16 | 1–16 |
| Porsche 992 | higher = stiffer (PENDING) | 0–12 (driver hit 12) | 0–12 |
| Audi R8 | **lower = stiffer** | 2–38 | 0–40 |
| McLaren 720S | **lower = stiffer** | 0–40 | 0–50 |
| Corvette Z06 | **lower = stiffer** | 0–30 | 0–22 |

The `_validate_setup_values` clamp at line 597 uses `d.ls_comp_range` — already supports per-car ranges via `DamperModel`. Polarity is NOT yet a writer concern (the solver decides what int to write); the writer just emits the int. But the DOWNSTREAM expectation matters: the solver must NOT pass a "20 = stiff" value when the car is McLaren (where 20 is mid-range soft). Coordinate with solver/damper unit auditor.

### O42 — Task-brief inaccuracy

The brief says "lines 579-583 aero model uses heave coefficients". Current `output/report.py:579-583` is actually:

```python
a(_full(f"  Brake target/mig: {brake_target_str} / {brake_migration_str}    Master cyl: {master_cyl_str}"))
a(_full(f"  Brake semantics: bias={brake_bias_status}  target/mig={brake_target_status}/{brake_migration_status}"))
```

Brake formatting, not aero. The aero block lives earlier in the report at the GARAGE CARD section. Worth re-reading the report.py source for the actual aero-coefficient dependency before action.

## Risk summary

- **Today (Phase 0 merged): write_sto raises `ValueError` for every GT3 car at `:818`.** No `.sto` is ever produced. Bundle.py catches and records this as `manifest.errors=["sto: No STO parameter ID mapping for car: bmw_m4_gt3"]` — silent at the user level if no one reads the manifest.
- **After PARAM_IDS land: silent partial outputs.** Until per-architecture branching is added in `_validate_setup_values` and `validate_and_fix_garage_correlation`, GT3 .sto files will contain `<HeaveSpring Value="0">`, `<TorsionBarOD Value="0">`, and 20 phantom per-corner damper IDs. iRacing may either reject the file or accept-and-display garbage.
- **Deflection/turns fallback path** uses BMW GTP regression coefficients. Any GT3 falling into the fallback will display physically nonsensical numbers without an obvious error.
- **GarageRanges field absence**: `gr.front_heave_nmm` etc. are read positionally as tuple-unpacks (`*gr.front_heave_nmm`). If a GT3 GarageRanges legitimately omits the field, this is `AttributeError`. Defensive zero-tuple defaults exist for some fields but coverage is incomplete.

## Effort estimate

Conservative scoping for the 3 GT3 cars with full IBT coverage at Spielberg (BMW, Aston, Porsche). Excludes solver/objective changes (other audit units).

| Work item | Effort |
|---|---|
| 3 new PARAM_IDS dicts (BMW M4 GT3 / Aston / Porsche 992) per the table at the end | 1 day |
| Wire dispatch in `_CAR_PARAM_IDS` and add `is_gt3` boolean | 0.25 day |
| GT3 branch in `_validate_setup_values` (skip heave/torsion clamps; clamp 4 corner springs + 4 perches + 4 bump-rubber gaps + splitter height) | 0.75 day |
| GT3 branch in `validate_and_fix_garage_correlation` (skip heave-slider / torsion-bar / pushrod-RH fixers; add bump-rubber + spring-perch RH fixer) | 1.5 days |
| GT3 branch in `_w_num`/`_w_str` for Step 3 4-corner spring writes (replace single torsion + paired-rear) | 0.5 day |
| GT3 branch for per-axle damper writes (replace 4-corner × 5-channel block) — gated on `solver/damper_solver` returning per-axle output, coordinate with that auditor | 0.5 day on writer side |
| GT3 toe-write Porsche-special path (Total rear toe vs per-wheel rear toe) | 0.25 day |
| GT3 fuel-level path divergence (Aston/BMW = `Chassis.Rear.FuelLevel`, Porsche = `Chassis.FrontBrakesLights.FuelLevel`) | 0.1 day |
| TC/ABS label format `"4 (TC)"` / `"3 (TC-LAT)"` / `"5 (TC SLIP)"` per-car suffix | 0.25 day |
| `output/report.py` GT3 branches (GARAGE CARD springs, dampers, garage diff table) | 1 day |
| Bundle.py per-car kwargs branches (Aston: EpasSetting/ThrottleResponse; Porsche: ThrottleShapeSetting/DashDisplayPage) | 0.25 day |
| Tests (golden .sto for BMW M4 GT3 Spielberg comparing emitted XML to schema doc) | 1 day |
| Buffer for surprises (per-car field that doesn't fit the table, schema-doc-vs-real-iRacing behaviour drift) | 1 day |

**Total: 8.3 person-days** for the 3 cars in `output/`. Remaining 8 GT3 cars likely 2 days each = 16 days for full grid, mostly cookie-cutter PARAM_IDS work after the architecture is right.

## Dependencies

Out-of-scope for this audit unit but blockers on `output/`:

1. **`solver/corner_spring_solver` per-corner output (4 independent coil rates)**: writer currently has only `front_torsion_od_mm` and a paired `rear_spring_rate_nmm`. GT3 needs `lf_spring_rate_nmm`, `rf_spring_rate_nmm`, `lr_spring_rate_nmm`, `rr_spring_rate_nmm` on `CornerSpringSolution`.
2. **`solver/damper_solver` per-axle output**: writer currently consumes `step6.lf, step6.rf, step6.lr, step6.rr`. GT3 needs `step6.front, step6.rear` (or backwards-compatible synthesis where `step6.lf == step6.rf == step6.front`).
3. **`solver/heave_solver.HeaveSolution.null()` semantics**: confirm `HeaveSolution.null()` truly has all fields = 0 / None, and confirm a `present: bool = False` flag (or equivalent) exists for early-bail at writer/validator.
4. **`car_model/cars.py` GT3 `GarageRanges`**: every field that the validator reads must be defined or have a documented zero/None contract per architecture. Current scaffolds have no `front_heave_perch_mm` etc. defined, so positional unpack `*gr.front_heave_nmm` will fail.
5. **`car_model/calibration_gate.py` Step 2 N/A handling**: gate must mark Step 2 as "N/A by architecture" not "blocked" / "uncalibrated" so downstream report.py doesn't print "Step 2 BLOCKED" on every GT3 run.
6. **`car_model/garage.GarageSetupState.from_solver_steps`**: currently encodes heave/third/torsion. GT3 state is 4 corner springs + 4 perches + 4 bump-rubber gaps + splitter height. Needs new GT3-aware constructor or polymorphism.

These dependencies are also issues called out by the parallel audit units; this audit's responsibility is to flag them as "writer cannot proceed without".

---

## Proposed PARAM_IDS dicts

Field path mappings derived from the 3 Spielberg session-info YAMLs and the schema doc. Each row maps a solver-output key (the keys consumed by `_w_num`/`_w_str`) to a CarSetup XML ID. **Note:** the YAMLs are pretty-printed YAML; the actual XML IDs will be `CarSetup_*` paths constructed by joining keys with `_`. Convention: section path segments concatenated with underscores, dropping `CarSetup` prefix in the table for readability — every value in production must be prefixed with `CarSetup_`.

### BMW M4 GT3 EVO (`bmw_m4_gt3` / `bmwm4gt3`)

```python
_BMW_M4_GT3_PARAM_IDS: dict[str, str] = {
    # ── Aero (TiresAero) ─────────────────────────────────────────────
    "wing_angle":               "CarSetup_Chassis_Rear_WingAngle",          # int degrees, ALSO writes TiresAero.AeroBalanceCalc.WingSetting
    "front_rh_at_speed":        "CarSetup_TiresAero_AeroBalanceCalc_FrontRhAtSpeed",
    "rear_rh_at_speed":         "CarSetup_TiresAero_AeroBalanceCalc_RearRhAtSpeed",
    "df_balance":               "CarSetup_TiresAero_AeroBalanceCalc_FrontDownforce",
    # ── Tyres ─────────────────────────────────────────────────────────
    "lf_pressure":              "CarSetup_TiresAero_LeftFront_StartingPressure",
    "rf_pressure":              "CarSetup_TiresAero_RightFront_StartingPressure",
    "lr_pressure":              "CarSetup_TiresAero_LeftRear_StartingPressure",   # NB: no `Tire` suffix
    "rr_pressure":              "CarSetup_TiresAero_RightRear_StartingPressure",
    "tyre_type":                "CarSetup_TiresAero_TireType_TireType",
    # ── Front brakes section ─────────────────────────────────────────
    "front_arb_blades":         "CarSetup_Chassis_FrontBrakes_ArbBlades",         # int 1..N
    "front_toe":                "CarSetup_Chassis_FrontBrakes_TotalToeIn",        # paired front toe, mm
    "front_master_cyl":         "CarSetup_Chassis_FrontBrakes_FrontMasterCyl",    # mm
    "rear_master_cyl":          "CarSetup_Chassis_FrontBrakes_RearMasterCyl",     # mm
    "pad_compound":             "CarSetup_Chassis_FrontBrakes_BrakePads",         # "Low friction"/"Medium friction"/"High friction"
    "front_splitter_height":    "CarSetup_Chassis_FrontBrakes_CenterFrontSplitterHeight",  # NEW GT3 param
    # ── Per-corner: LF/RF/LR/RR ──────────────────────────────────────
    "lf_corner_weight":         "CarSetup_Chassis_LeftFront_CornerWeight",        # display N
    "lf_ride_height":           "CarSetup_Chassis_LeftFront_RideHeight",          # mm
    "lf_bump_rubber_gap":       "CarSetup_Chassis_LeftFront_BumpRubberGap",       # mm — NEW GT3 param
    "lf_spring_rate":           "CarSetup_Chassis_LeftFront_SpringRate",          # N/mm — REPLACES front_torsion_od
    "lf_camber":                "CarSetup_Chassis_LeftFront_Camber",
    "rf_corner_weight":         "CarSetup_Chassis_RightFront_CornerWeight",
    "rf_ride_height":           "CarSetup_Chassis_RightFront_RideHeight",
    "rf_bump_rubber_gap":       "CarSetup_Chassis_RightFront_BumpRubberGap",
    "rf_spring_rate":           "CarSetup_Chassis_RightFront_SpringRate",
    "rf_camber":                "CarSetup_Chassis_RightFront_Camber",
    "lr_corner_weight":         "CarSetup_Chassis_LeftRear_CornerWeight",
    "lr_ride_height":           "CarSetup_Chassis_LeftRear_RideHeight",
    "lr_bump_rubber_gap":       "CarSetup_Chassis_LeftRear_BumpRubberGap",
    "lr_spring_rate":           "CarSetup_Chassis_LeftRear_SpringRate",
    "lr_camber":                "CarSetup_Chassis_LeftRear_Camber",
    "lr_toe":                   "CarSetup_Chassis_LeftRear_ToeIn",                # per-wheel rear toe, mm
    "rr_corner_weight":         "CarSetup_Chassis_RightRear_CornerWeight",
    "rr_ride_height":           "CarSetup_Chassis_RightRear_RideHeight",
    "rr_bump_rubber_gap":       "CarSetup_Chassis_RightRear_BumpRubberGap",
    "rr_spring_rate":           "CarSetup_Chassis_RightRear_SpringRate",
    "rr_camber":                "CarSetup_Chassis_RightRear_Camber",
    "rr_toe":                   "CarSetup_Chassis_RightRear_ToeIn",
    # ── Rear section ─────────────────────────────────────────────────
    "fuel_level":               "CarSetup_Chassis_Rear_FuelLevel",                # L — DIFFERENT from Porsche 992
    "rear_arb_blades":          "CarSetup_Chassis_Rear_ArbBlades",                # int
    # (rear wing angle alias to top — BMW also has Chassis.Rear.WingAngle int deg)
    # ── In-car adjustments ───────────────────────────────────────────
    "brake_bias":               "CarSetup_Chassis_InCarAdjustments_BrakePressureBias",  # %
    "abs_setting":              "CarSetup_Chassis_InCarAdjustments_AbsSetting",   # "n (ABS)" string
    "tc_setting":               "CarSetup_Chassis_InCarAdjustments_TcSetting",    # "n (TC)" — BMW format
    "fwt_dist":                 "CarSetup_Chassis_InCarAdjustments_FWtdist",      # display %
    "cross_weight":             "CarSetup_Chassis_InCarAdjustments_CrossWeight",  # display %
    # ── Gears + diff ──────────────────────────────────────────────────
    "gear_stack":               "CarSetup_Chassis_GearsDifferential_GearStack",
    "diff_friction_faces":      "CarSetup_Chassis_GearsDifferential_FrictionFaces",  # int 2..10
    "diff_preload":             "CarSetup_Chassis_GearsDifferential_DiffPreload",    # Nm
    # ── Dampers (per-axle, 4 channels each) ──────────────────────────
    "front_ls_comp":            "CarSetup_Dampers_FrontDampers_LowSpeedCompressionDamping",
    "front_hs_comp":            "CarSetup_Dampers_FrontDampers_HighSpeedCompressionDamping",
    "front_ls_rbd":             "CarSetup_Dampers_FrontDampers_LowSpeedReboundDamping",
    "front_hs_rbd":             "CarSetup_Dampers_FrontDampers_HighSpeedReboundDamping",
    "rear_ls_comp":             "CarSetup_Dampers_RearDampers_LowSpeedCompressionDamping",
    "rear_hs_comp":             "CarSetup_Dampers_RearDampers_HighSpeedCompressionDamping",
    "rear_ls_rbd":              "CarSetup_Dampers_RearDampers_LowSpeedReboundDamping",
    "rear_hs_rbd":              "CarSetup_Dampers_RearDampers_HighSpeedReboundDamping",
}
```

### Aston Martin Vantage GT3 EVO (`aston_martin_vantage_gt3` / `amvantageevogt3`)

Diverges from BMW in: aero balance section name (`AeroBalanceCalculator` vs `AeroBalanceCalc`), wing field name (`RearWingAngle` vs `WingAngle`/`WingSetting`), front section name (`FrontBrakesLights` vs `FrontBrakes`), front ARB field name (`FarbBlades` vs `ArbBlades`), rear ARB field name (`RarbBlades` vs `ArbBlades`), plus 4 Aston-only fields (`EnduranceLights`, `NightLedStripColor`, `ThrottleResponse`, `EpasSetting`).

```python
_ASTON_MARTIN_VANTAGE_GT3_PARAM_IDS: dict[str, str] = {
    # ── Aero ──────────────────────────────────────────────────────────
    "wing_angle":               "CarSetup_Chassis_Rear_RearWingAngle",            # int deg, ALSO mirrors to TiresAero.AeroBalanceCalculator.RearWingAngle
    "front_rh_at_speed":        "CarSetup_TiresAero_AeroBalanceCalculator_FrontRhAtSpeed",   # NB: Calculator suffix
    "rear_rh_at_speed":         "CarSetup_TiresAero_AeroBalanceCalculator_RearRhAtSpeed",
    "df_balance":               "CarSetup_TiresAero_AeroBalanceCalculator_FrontDownforce",
    # ── Tyres ─────────────────────────────────────────────────────────
    "lf_pressure":              "CarSetup_TiresAero_LeftFront_StartingPressure",
    "rf_pressure":              "CarSetup_TiresAero_RightFront_StartingPressure",
    "lr_pressure":              "CarSetup_TiresAero_LeftRear_StartingPressure",
    "rr_pressure":              "CarSetup_TiresAero_RightRear_StartingPressure",
    "tyre_type":                "CarSetup_TiresAero_TireType_TireType",
    # ── FrontBrakesLights ─────────────────────────────────────────────
    "front_arb_blades":         "CarSetup_Chassis_FrontBrakesLights_FarbBlades",  # NB: Farb*, not Arb*
    "front_toe":                "CarSetup_Chassis_FrontBrakesLights_TotalToeIn",
    "front_master_cyl":         "CarSetup_Chassis_FrontBrakesLights_FrontMasterCyl",
    "rear_master_cyl":          "CarSetup_Chassis_FrontBrakesLights_RearMasterCyl",
    "pad_compound":             "CarSetup_Chassis_FrontBrakesLights_BrakePads",
    "endurance_lights":         "CarSetup_Chassis_FrontBrakesLights_EnduranceLights",  # ASTON ONLY
    "night_led_strip_color":    "CarSetup_Chassis_FrontBrakesLights_NightLedStripColor",  # ASTON+PORSCHE
    "front_splitter_height":    "CarSetup_Chassis_FrontBrakesLights_CenterFrontSplitterHeight",
    # ── Per-corner ────────────────────────────────────────────────────
    "lf_corner_weight":         "CarSetup_Chassis_LeftFront_CornerWeight",
    "lf_ride_height":           "CarSetup_Chassis_LeftFront_RideHeight",
    "lf_bump_rubber_gap":       "CarSetup_Chassis_LeftFront_BumpRubberGap",
    "lf_spring_rate":           "CarSetup_Chassis_LeftFront_SpringRate",
    "lf_camber":                "CarSetup_Chassis_LeftFront_Camber",
    "rf_corner_weight":         "CarSetup_Chassis_RightFront_CornerWeight",
    "rf_ride_height":           "CarSetup_Chassis_RightFront_RideHeight",
    "rf_bump_rubber_gap":       "CarSetup_Chassis_RightFront_BumpRubberGap",
    "rf_spring_rate":           "CarSetup_Chassis_RightFront_SpringRate",
    "rf_camber":                "CarSetup_Chassis_RightFront_Camber",
    "lr_corner_weight":         "CarSetup_Chassis_LeftRear_CornerWeight",
    "lr_ride_height":           "CarSetup_Chassis_LeftRear_RideHeight",
    "lr_bump_rubber_gap":       "CarSetup_Chassis_LeftRear_BumpRubberGap",
    "lr_spring_rate":           "CarSetup_Chassis_LeftRear_SpringRate",
    "lr_camber":                "CarSetup_Chassis_LeftRear_Camber",
    "lr_toe":                   "CarSetup_Chassis_LeftRear_ToeIn",
    "rr_corner_weight":         "CarSetup_Chassis_RightRear_CornerWeight",
    "rr_ride_height":           "CarSetup_Chassis_RightRear_RideHeight",
    "rr_bump_rubber_gap":       "CarSetup_Chassis_RightRear_BumpRubberGap",
    "rr_spring_rate":           "CarSetup_Chassis_RightRear_SpringRate",
    "rr_camber":                "CarSetup_Chassis_RightRear_Camber",
    "rr_toe":                   "CarSetup_Chassis_RightRear_ToeIn",
    # ── Rear section ─────────────────────────────────────────────────
    "fuel_level":               "CarSetup_Chassis_Rear_FuelLevel",
    "rear_arb_blades":          "CarSetup_Chassis_Rear_RarbBlades",               # NB: Rarb*, not Arb*
    # ── In-car adjustments (Aston-extended) ──────────────────────────
    "brake_bias":               "CarSetup_Chassis_InCarAdjustments_BrakePressureBias",
    "abs_setting":              "CarSetup_Chassis_InCarAdjustments_AbsSetting",
    "tc_setting":               "CarSetup_Chassis_InCarAdjustments_TcSetting",     # "n (TC SLIP)" — Aston uses TC SLIP label
    "throttle_response":        "CarSetup_Chassis_InCarAdjustments_ThrottleResponse",  # ASTON ONLY: "n (RED)"
    "epas_setting":             "CarSetup_Chassis_InCarAdjustments_EpasSetting",   # ASTON ONLY: "n (PAS)"
    "fwt_dist":                 "CarSetup_Chassis_InCarAdjustments_FWtdist",
    "cross_weight":             "CarSetup_Chassis_InCarAdjustments_CrossWeight",
    # ── Gears + diff ──────────────────────────────────────────────────
    "gear_stack":               "CarSetup_Chassis_GearsDifferential_GearStack",
    "diff_friction_faces":      "CarSetup_Chassis_GearsDifferential_FrictionFaces",
    "diff_preload":             "CarSetup_Chassis_GearsDifferential_DiffPreload",
    # ── Dampers (per-axle, 4 channels each) — same as BMW ─────────────
    "front_ls_comp":            "CarSetup_Dampers_FrontDampers_LowSpeedCompressionDamping",
    "front_hs_comp":            "CarSetup_Dampers_FrontDampers_HighSpeedCompressionDamping",
    "front_ls_rbd":             "CarSetup_Dampers_FrontDampers_LowSpeedReboundDamping",
    "front_hs_rbd":             "CarSetup_Dampers_FrontDampers_HighSpeedReboundDamping",
    "rear_ls_comp":             "CarSetup_Dampers_RearDampers_LowSpeedCompressionDamping",
    "rear_hs_comp":             "CarSetup_Dampers_RearDampers_HighSpeedCompressionDamping",
    "rear_ls_rbd":              "CarSetup_Dampers_RearDampers_LowSpeedReboundDamping",
    "rear_hs_rbd":              "CarSetup_Dampers_RearDampers_HighSpeedReboundDamping",
}
```

### Porsche 911 GT3 R (992) (`porsche_992_gt3r` / `porsche992rgt3`)

Most divergent of the three. Differences from BMW: **front ARB is `ArbSetting` (single int — NOT a blade index)**, **rear ARB is `RarbSetting`**, **rear toe is paired (`Chassis.Rear.TotalToeIn`) NOT per-wheel**, **fuel level is in `FrontBrakesLights` section, not `Rear`**, plus 3 Porsche-only fields (`ThrottleShapeSetting`, `DashDisplayPage`, `NightLedStripColor`).

```python
_PORSCHE_992_GT3R_PARAM_IDS: dict[str, str] = {
    # ── Aero ──────────────────────────────────────────────────────────
    "wing_angle":               "CarSetup_Chassis_Rear_WingSetting",              # NB: WingSetting (Porsche uses this name in BOTH chassis-rear AND aero-balance)
    "front_rh_at_speed":        "CarSetup_TiresAero_AeroBalanceCalc_FrontRhAtSpeed",
    "rear_rh_at_speed":         "CarSetup_TiresAero_AeroBalanceCalc_RearRhAtSpeed",
    "df_balance":               "CarSetup_TiresAero_AeroBalanceCalc_FrontDownforce",
    # ── Tyres ─────────────────────────────────────────────────────────
    "lf_pressure":              "CarSetup_TiresAero_LeftFront_StartingPressure",
    "rf_pressure":              "CarSetup_TiresAero_RightFront_StartingPressure",
    "lr_pressure":              "CarSetup_TiresAero_LeftRear_StartingPressure",
    "rr_pressure":              "CarSetup_TiresAero_RightRear_StartingPressure",
    "tyre_type":                "CarSetup_TiresAero_TireType_TireType",
    # ── FrontBrakesLights ─────────────────────────────────────────────
    "front_arb_setting":        "CarSetup_Chassis_FrontBrakesLights_ArbSetting",  # **INT, not blade — Porsche-unique**
    "front_toe":                "CarSetup_Chassis_FrontBrakesLights_TotalToeIn",
    "fuel_level":               "CarSetup_Chassis_FrontBrakesLights_FuelLevel",   # **PORSCHE-UNIQUE: fuel here, not in Rear**
    "front_master_cyl":         "CarSetup_Chassis_FrontBrakesLights_FrontMasterCyl",
    "rear_master_cyl":          "CarSetup_Chassis_FrontBrakesLights_RearMasterCyl",
    "pad_compound":             "CarSetup_Chassis_FrontBrakesLights_BrakePads",
    "night_led_strip_color":    "CarSetup_Chassis_FrontBrakesLights_NightLedStripColor",
    "front_splitter_height":    "CarSetup_Chassis_FrontBrakesLights_CenterFrontSplitterHeight",
    # ── Per-corner ────────────────────────────────────────────────────
    "lf_corner_weight":         "CarSetup_Chassis_LeftFront_CornerWeight",
    "lf_ride_height":           "CarSetup_Chassis_LeftFront_RideHeight",
    "lf_bump_rubber_gap":       "CarSetup_Chassis_LeftFront_BumpRubberGap",
    "lf_spring_rate":           "CarSetup_Chassis_LeftFront_SpringRate",
    "lf_camber":                "CarSetup_Chassis_LeftFront_Camber",
    "rf_corner_weight":         "CarSetup_Chassis_RightFront_CornerWeight",
    "rf_ride_height":           "CarSetup_Chassis_RightFront_RideHeight",
    "rf_bump_rubber_gap":       "CarSetup_Chassis_RightFront_BumpRubberGap",
    "rf_spring_rate":           "CarSetup_Chassis_RightFront_SpringRate",
    "rf_camber":                "CarSetup_Chassis_RightFront_Camber",
    "lr_corner_weight":         "CarSetup_Chassis_LeftRear_CornerWeight",
    "lr_ride_height":           "CarSetup_Chassis_LeftRear_RideHeight",
    "lr_bump_rubber_gap":       "CarSetup_Chassis_LeftRear_BumpRubberGap",
    "lr_spring_rate":           "CarSetup_Chassis_LeftRear_SpringRate",
    "lr_camber":                "CarSetup_Chassis_LeftRear_Camber",
    # NO lr_toe / rr_toe per-wheel — Porsche uses paired rear toe (see below)
    "rr_corner_weight":         "CarSetup_Chassis_RightRear_CornerWeight",
    "rr_ride_height":           "CarSetup_Chassis_RightRear_RideHeight",
    "rr_bump_rubber_gap":       "CarSetup_Chassis_RightRear_BumpRubberGap",
    "rr_spring_rate":           "CarSetup_Chassis_RightRear_SpringRate",
    "rr_camber":                "CarSetup_Chassis_RightRear_Camber",
    # ── Rear section (Porsche-specific) ───────────────────────────────
    "rear_arb_setting":         "CarSetup_Chassis_Rear_RarbSetting",              # **INT, not blade**
    "rear_toe":                 "CarSetup_Chassis_Rear_TotalToeIn",               # **PAIRED rear toe — Porsche-unique**
    # NO Chassis.Rear.FuelLevel — see FrontBrakesLights.FuelLevel above
    # ── In-car adjustments (Porsche-extended) ────────────────────────
    "brake_bias":               "CarSetup_Chassis_InCarAdjustments_BrakePressureBias",
    "abs_setting":              "CarSetup_Chassis_InCarAdjustments_AbsSetting",
    "tc_setting":               "CarSetup_Chassis_InCarAdjustments_TcSetting",    # "n (TC-LAT)" — Porsche label
    "throttle_shape_setting":   "CarSetup_Chassis_InCarAdjustments_ThrottleShapeSetting",  # PORSCHE ONLY
    "dash_display_page":        "CarSetup_Chassis_InCarAdjustments_DashDisplayPage",       # PORSCHE ONLY
    "fwt_dist":                 "CarSetup_Chassis_InCarAdjustments_FWtdist",
    "cross_weight":             "CarSetup_Chassis_InCarAdjustments_CrossWeight",
    # ── Gears + diff ──────────────────────────────────────────────────
    "gear_stack":               "CarSetup_Chassis_GearsDifferential_GearStack",
    "diff_friction_faces":      "CarSetup_Chassis_GearsDifferential_FrictionFaces",
    "diff_preload":             "CarSetup_Chassis_GearsDifferential_DiffPreload",
    # ── Dampers (per-axle, 4 channels each) ──────────────────────────
    # Porsche driver values reach 12 (e.g. F HSC=12, R LSR=12) — implies 0–12 range,
    # not 0–11 like BMW/Aston. Confirm with sweep before pinning DamperModel ranges.
    "front_ls_comp":            "CarSetup_Dampers_FrontDampers_LowSpeedCompressionDamping",
    "front_hs_comp":            "CarSetup_Dampers_FrontDampers_HighSpeedCompressionDamping",
    "front_ls_rbd":             "CarSetup_Dampers_FrontDampers_LowSpeedReboundDamping",
    "front_hs_rbd":             "CarSetup_Dampers_FrontDampers_HighSpeedReboundDamping",
    "rear_ls_comp":             "CarSetup_Dampers_RearDampers_LowSpeedCompressionDamping",
    "rear_hs_comp":             "CarSetup_Dampers_RearDampers_HighSpeedCompressionDamping",
    "rear_ls_rbd":              "CarSetup_Dampers_RearDampers_LowSpeedReboundDamping",
    "rear_hs_rbd":              "CarSetup_Dampers_RearDampers_HighSpeedReboundDamping",
}
```

### Cross-cutting solver-output keys NEW for GT3

These keys do not exist in any current `_PARAM_IDS` dict and must be added to `solver/` step result dataclasses before the writer can consume them:

| Key | Owner | Notes |
|---|---|---|
| `lf_spring_rate`, `rf_spring_rate`, `lr_spring_rate`, `rr_spring_rate` | `CornerSpringSolution` | 4 independent N/mm rates (replaces single `front_torsion_od_mm` + paired `rear_spring_rate_nmm`) |
| `lf_bump_rubber_gap`, `rf_bump_rubber_gap`, `lr_bump_rubber_gap`, `rr_bump_rubber_gap` | New `BumpRubberSolution` (or fold into Step 1/Step 2 replacement) | mm — driver-loaded values: BMW F=15/R=52, Aston F=17/R=54, Porsche F=30/R=51 |
| `front_splitter_height` | `RakeSolution` (extend) | mm — `CenterFrontSplitterHeight` is a NEW GT3 garage parameter; affects front DF |
| `front_ls_comp`, `front_hs_comp`, `front_ls_rbd`, `front_hs_rbd`, `rear_ls_comp`, `rear_hs_comp`, `rear_ls_rbd`, `rear_hs_rbd` | `DamperSolution` (rework) | Per-axle 4-channel GT3 damper structure (replaces 4-corner × 5-channel GTP structure) |
| `front_arb_blades` (BMW/Aston) / `front_arb_setting` (Porsche) | `ARBSolution` | Both already exist as `front_arb_blade_start` (int); Porsche needs single-int variant |
| `abs_setting`, `tc_setting` | `SupportingParameters` | string with label suffix; need car-specific suffix dispatch (`(TC)` / `(TC SLIP)` / `(TC-LAT)`) |
| `throttle_response`, `epas_setting` | `SupportingParameters` (Aston only) | string with label suffix `(RED)` / `(PAS)` |
| `throttle_shape_setting`, `dash_display_page` | `SupportingParameters` (Porsche only) | int / string — display only |
| `endurance_lights`, `night_led_strip_color` | metadata pass-through | not solver outputs; pass-through from current_setup |
| `diff_friction_faces` | `DiffSolution` | int 2/4/6/8/10 — replaces GTP's `diff_clutch_plates` |

### Open questions for next session

1. **Where does `WingSetting` (in `TiresAero.AeroBalanceCalc`) come from vs `WingAngle` / `RearWingAngle` / `WingSetting` in `Chassis.Rear`?** Schema doc says BMW writes both `WingSetting` (aero calc) and `WingAngle` (chassis rear); Porsche writes `WingSetting` in both spots; Aston writes `RearWingAngle` in both. Need to confirm whether the writer must emit both fields or only one, and whether they must be identical numeric values.
2. **Is `FWtdist` writable or read-only?** Driver-loaded values are FWtdist 46.4% (BMW), 48.0% (Aston), 44.9% (Porsche). If read-only, omit from PARAM_IDS; if writable (i.e. user controls cross-weight via this field), include and let the solver target it.
3. **`DiffPreload` units**: YAML says `Nm`, current `_BMW_PARAM_IDS["diff_preload"] = "..._RearDiffSpec_Preload"` writes `Nm`. GT3 `Chassis.GearsDifferential.DiffPreload` consistent at Nm — confirmed.
4. **iRacing's tolerance for missing fields**: if PARAM_IDS dict omits `endurance_lights` for Aston, does iRacing reject the entire .sto or just leave that field at its previous value? Test before deciding "must-emit-all" vs "emit-only-solver-controlled".
