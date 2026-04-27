# GT3 Phase 2 Audit — Analyzer

## Scope

This audit covers the analyzer subtree for GT3 readiness:

- `analyzer/setup_schema.py`
- `analyzer/setup_reader.py`
- `analyzer/sto_adapters.py`
- `analyzer/extract.py`
- `analyzer/diagnose.py`
- `analyzer/segment.py`
- `analyzer/recommend.py`
- `analyzer/sto_binary.py`
- `analyzer/causal_graph.py`
- `analyzer/driver_style.py`

Cross-referenced against `docs/gt3_session_info_schema.md`, `docs/gt3_per_car_spec.md`, the three Spielberg session-info YAMLs (BMW M4 GT3, Aston Vantage GT3 EVO, Porsche 992 GT3 R), and `car_model/cars.py` `SuspensionArchitecture.GT3_COIL_4WHEEL`.

The analyzer subtree was written before GT3 entered the codebase. It assumes (a) GTP-class field names (`HeaveSpring`, `TorsionBarOD`, `ArbBlades`/`ArbSize`, `BrakesDriveUnit`/`Systems`), (b) per-corner damper exposure under `Dampers.LeftFrontDamper` / `Chassis.LeftFront`, and (c) closed enumeration `("bmw", "ferrari", "cadillac", "porsche", "acura")` for `adapter_name`. None of these hold for GT3.

## Summary table

| ID | Severity | File:line | One-line |
|---|---|---|---|
| A1 | BLOCKER | `analyzer/setup_reader.py:500-509` | `adapter_name` whitelist excludes every GT3 car — `from_ibt` falls through to `"unknown"` for BMW M4 GT3 / Aston / Porsche 992 GT3 R |
| A2 | BLOCKER | `analyzer/setup_reader.py:404-408` | ARB read tries only `ArbSize`/`ArbBlades`/`ArbAdj`/`ArbSetting`; misses Aston `FarbBlades`/`RarbBlades` and never decodes Porsche GT3 R's integer `ArbSetting`/`RarbSetting` correctly |
| A3 | BLOCKER | `analyzer/setup_reader.py:392-394` | Reads `front.HeaveSpring` / `rear.ThirdSpring|HeaveSpring` for ALL cars; GT3 uses per-corner `LeftFront.SpringRate` / `LeftRear.SpringRate` and has NO heave/third — silently leaves `front_heave_nmm=0`, `rear_third_nmm=0` |
| A4 | BLOCKER | `analyzer/setup_reader.py:399` | Reads `lf.TorsionBarOD` for `front_torsion_od_mm`; GT3 has no torsion bars — wrong field, no warning |
| A5 | BLOCKER | `analyzer/setup_reader.py:301-304` | Looks for `Systems.BrakeSpec` / `BrakesDriveUnit.BrakeSpec`; GT3 places brake fields under `Chassis.FrontBrakes` (BMW) / `Chassis.FrontBrakesLights` (Aston, Porsche) — every brake field is silently zero |
| A6 | BLOCKER | `analyzer/setup_reader.py:295-296` | Reads aero from `TiresAero.AeroSettings`; GT3 has no `AeroSettings` — wing/aero values silently zero. Aero balance lives in `TiresAero.AeroBalanceCalc` (BMW/Porsche) or `AeroBalanceCalculator` (Aston) |
| A7 | BLOCKER | `analyzer/setup_reader.py:447-461` | Diff/TC/fuel resolved against `Systems.RearDiffSpec` / `Systems.TractionControl` / `Systems.Fuel`; GT3 places diff under `Chassis.GearsDifferential`, TC/ABS under `Chassis.InCarAdjustments`, fuel under `Chassis.Rear.FuelLevel` (BMW/Aston) or `Chassis.FrontBrakesLights.FuelLevel` (Porsche) |
| A8 | BLOCKER | `analyzer/setup_reader.py:316-351` | Damper layout decision tree has no GT3 branch. GT3 emits `Dampers.FrontDampers` / `Dampers.RearDampers` (per-axle, 8 channels). Code falls through to `else` Ferrari path, looks for `LeftFrontDamper` and gets `{}` — every damper field zero |
| A9 | BLOCKER | `analyzer/setup_reader.py:413` | `front_toe_mm = front.ToeIn` — only set for cars exposing `Chassis.Front.ToeIn`. GT3 BMW/Aston/Porsche put paired front toe under `Chassis.FrontBrakes(Lights).TotalToeIn` |
| A10 | BLOCKER | `analyzer/setup_reader.py:415-417` | Rear toe always averaged from `lr.ToeIn`/`rr.ToeIn`; for Porsche 992 GT3 R rear toe is paired at `Chassis.Rear.TotalToeIn` (per schema) — value is silently zero |
| A11 | BLOCKER | `analyzer/setup_reader.py:303` | `front_diff_spec = systems.get("FrontDiffSpec")` — GT3 has no front diff at all; no impact on correctness, but adapter assumes Ferrari shape and may emit phantom front-diff data |
| A12 | BLOCKER | `analyzer/setup_schema.py:82-137` | `_KNOWN_FIELD_MAP` keys are all `CarSetup_Chassis_Front_HeaveSpring`, `_TorsionBarOD`, `_ArbSize`, `LeftFrontDamper_*` — no GT3 field IDs. Schema cannot describe a GT3 setup |
| A13 | BLOCKER | `analyzer/setup_schema.py:311-318` | `_manual_constraints` indexes `gr.front_heave_nmm`, `gr.front_torsion_od_mm`, etc. — GT3 cars do not populate these fields, so `_manual_constraints` returns nonsense or KeyErrors for GT3 |
| A14 | BLOCKER | `analyzer/sto_adapters.py:208-358` | Only Acura and a `v3_container_only` fall-through are wired. GT3 STO files will get `adapter_name="v3_container_only"` and zero values; `_ACURA_ROW_SPECS` (60 rows) hard-codes torsion bars + heave springs and will not generalize |
| A15 | BLOCKER | `analyzer/sto_binary.py:28-34` | `_CAR_HINTS` regex catalog enumerates only GTP cars (acura/bmwlmdh/cadillac/ferrari/porsche963). GT3 STO filenames (`bmwm4gt3*.sto`, `amvantageevogt3*`, `porsche992rgt3*`) decode to `car_id=""` |
| A16 | BLOCKER | `analyzer/extract.py:96-97, 678` | `lltd_measured` aliased to `roll_distribution_proxy` — KNOWN BAD per project memory. The geometric `(front_RH_diff × tw_f²) / (... + rear_RH_diff × tw_r²)` proxy is insensitive to spring stiffness; do NOT carry the alias forward into GT3 — it has no GT3 calibration baseline at all |
| A17 | BLOCKER | `analyzer/extract.py:1413-1514` (`_extract_heave_deflection`) | Reads `HFshockDefl` / `HRshockDefl` and emits `front_heave_travel_used_pct`, `heave_bottoming_events_front`, `front_heave_defl_braking_p99_mm` for ALL cars. GT3 has no heave element; these channels either don't exist or carry per-corner travel and the metrics will be bogus and trigger `_check_safety` heave bottoming alarms (A19) |
| A18 | BLOCKER | `analyzer/diagnose.py:204-208, 266-267, 321-388` | Heave travel exhaustion safety alarms fire whenever `front_heave_travel_used_pct > 85`. For GT3 this metric is meaningless — must skip on `car.suspension_arch is GT3_COIL_4WHEEL` (or `car.heave_spring is None`). Otherwise GT3 sessions will produce phantom critical-severity heave bottoming "Fix: stiffen heave spring" recommendations |
| A19 | BLOCKER | `analyzer/causal_graph.py:117-120, 122-125, 307-313` | Root-cause nodes `heave_too_soft` / `heave_too_stiff` map to parameter `front_heave_nmm`. For GT3 this parameter does not exist — `analyzer/recommend.py` cannot apply any change; the entire causal chain is dead. Need GT3 equivalents (`front_corner_spring_too_soft` → `front_spring_nmm`) |
| A20 | DEGRADED | `analyzer/setup_reader.py:454-455` | `front_master_cyl_mm` from `brake_spec.FrontMasterCyl`. For GT3 this is at `Chassis.FrontBrakes(Lights).FrontMasterCyl` — fixable as part of A5 but worth listing because every car has it |
| A21 | DEGRADED | `analyzer/setup_reader.py:447` | `BrakePressureBias` lives at `Chassis.InCarAdjustments.BrakePressureBias` for GT3, not `BrakeSpec` |
| A22 | DEGRADED | `analyzer/setup_reader.py:459` | `diff_clutch_plates = ClutchFrictionPlates`; GT3 schema names this `FrictionFaces` under `Chassis.GearsDifferential` |
| A23 | DEGRADED | `analyzer/setup_reader.py:465` | `gear_stack` from `gear_ratios.GearStack`; GT3 places it at `Chassis.GearsDifferential.GearStack` (no `GearRatios` section, no per-gear speed echo) |
| A24 | DEGRADED | `analyzer/setup_reader.py:431-444` | Roll-damper / 3rd-damper / `RollSpring` reads are unconditional; for GT3 these YAML keys are absent and parse to 0. Harmless but adds confusing zero fields to `CurrentSetup` |
| A25 | DEGRADED | `analyzer/setup_reader.py:546-547` | `summary()` prints `Heave {front}/{rear}` and `FARB {size}/{blade} RARB {size}/{blade}` — meaningless for GT3 (no heave; ARB has only blade or only setting depending on car). Should branch on architecture |
| A26 | DEGRADED | `analyzer/extract.py:661-662` | `tw_f = getattr(car.arb, "track_width_front_mm", 1730.0)` — fallback to BMW GTP track width for any car missing the field. GT3 cars (esp. Corvette Z06 GT3.R 1648/1586 mm asymmetric) need their own widths. Project policy is "no silent fallbacks" — should `raise` or warn |
| A27 | DEGRADED | `analyzer/extract.py:739` | `track_w_m = car.arb.track_width_front_mm / 1000` direct access without fallback — fine, but only protects body-roll path; the LLTD-proxy path (A26) silently uses 1730 mm BMW |
| A28 | DEGRADED | `analyzer/diagnose.py:99-103` | `Diagnosis.lltd_pct` derived from `roll_distribution_proxy`/`lltd_measured`. For GT3 this is doubly suspect: (a) no GT3 calibration of the proxy exists, (b) GT3 cars (Porsche RR ~38% front, Audi 12=off TC, etc.) will land far from the BMW/Sebring baseline this code was tuned against. Display only, but downstream report shows it as authoritative |
| A29 | DEGRADED | `analyzer/setup_schema.py:519-539` | `build_setup_schema` `if car_name != "ferrari"` returns a generic dataclass dump with no allowed-range/options/resolution information. GT3 cars all hit this branch; no field-level constraints surface |
| A30 | DEGRADED | `analyzer/setup_schema.py:402-408` | `_FERRARI_PUBLIC_ALIASES` maps `front_heave_spring_nmm`/`rear_third_spring_nmm`/`front_torsion_od_mm` to indexed Ferrari aliases. GT3 reads will hit the aliases and emit `idx` units even though GT3 springs are direct N/mm |
| A31 | DEGRADED | `analyzer/sto_adapters.py:208-358` | `_KNOWN_ACURA_ORACLES` carries hard-coded torsion-bar/heave-spring values; not a GT3 leak today, but the `_ACURA_ROW_SPECS` shape (Front Heave / Rear Heave / Front Roll / Rear Roll under `Dampers`) is the wrong template to inherit for GT3. New GT3 row specs needed (Front/Rear axle damper tabs only) |
| A32 | DEGRADED | `analyzer/extract.py:446-462, 492-509, 869-872` | Per-corner shock-velocity decode (`LF/RF/LR/RRshockVel`) is correct for GT3 if those channels exist (per-corner physical dampers, even though garage exposes axle-paired). Heave/roll synthesis fallback (`HFshockVel + FROLLshockVel`) is only triggered when corner channels are missing — should be safe for GT3 but only after we confirm BMW M4 GT3 IBTs actually expose `LFshockVel` etc. (PENDING IBT verification) |
| A33 | DEGRADED | `analyzer/setup_reader.py:279-283` | `from_ibt` requires `car_canonical` to disambiguate BMW vs Cadillac vs Porsche. For GT3 the same disambiguation matters across 11 cars (3 with sampled IBTs, 8 PENDING). Today only 5 GTP names are accepted in the whitelist — the function should accept canonical GT3 names too (`bmw_m4_gt3`, `aston_martin_vantage_gt3`, `porsche_992_gt3r`, etc.) |
| A34 | DEGRADED | `analyzer/recommend.py` | `_recommend_for_problem` maps causal-graph parameters back to `CurrentSetup` attributes via setattr. `front_heave_nmm` is a no-op for GT3 (always 0); recommendation engine will emit "no-change" or numerical garbage |
| A35 | DEGRADED | `analyzer/segment.py:42-77` | `CornerAnalysis` carries `front_rh_min_mm` (splitter proximity check). GT3 splitters exist (`CenterFrontSplitterHeight`) but the CFSRrideHeight channel availability for GT3 is unconfirmed; segment-level kerb-overlap and platform_risk_flags need GT3 fixture verification, not architecture changes |
| A36 | COSMETIC | `analyzer/setup_reader.py:96-104, 113-117` | Dataclass field comments embed GTP/Ferrari/Porsche-specific notes ("Ferrari only (has front diff)", "ORECA: rear also uses torsion bars"). New comments needed for GT3 (per-axle dampers; coil-4-corner) but no behavior impact |
| A37 | COSMETIC | `analyzer/causal_graph.py:117-313` | Causal nodes referencing "heave" are the only GTP-specific entries — the rest of the graph (understeer, oversteer, body slip, traction loss) translates to GT3 unchanged. Adding GT3 nodes is mostly additive |
| A38 | COSMETIC | `analyzer/driver_style.py:1-100` | Module is architecture-agnostic (only consumes `CornerAnalysis` + raw IBT). Should work for GT3 with no changes once the upstream extract/setup_reader cracks the new YAML |

## Findings

### A1 — BLOCKER — `analyzer/setup_reader.py:500-509`

`adapter_name` is assigned via:

```python
car_canonical.lower()
if car_canonical and car_canonical.lower() in ("bmw", "ferrari", "cadillac", "porsche", "acura")
else (
    "acura" if is_heave_roll_layout
    else ("ferrari" if is_ferrari_layout else "unknown")
)
```

GT3 canonical names (`bmw_m4_gt3`, `aston_martin_vantage_gt3`, `porsche_992_gt3r`, `acura_nsx_gt3`, ...) are NOT in the whitelist. The function falls through to the structural sniffer, which will misclassify GT3 BMW as `unknown` (no `is_heave_roll_layout`, no `is_ferrari_layout` since GT3 has no `Systems` block — see A5). Downstream `apply_live_control_overrides` (line 571) only acts when `adapter_name in {"bmw", "ferrari"}`, so live ARB/TC/brake-bias telemetry promotion silently breaks for ALL GT3 cars.

**Fix shape**: extend the whitelist to a tuple that resolves through `car_model.registry`, e.g.:

```python
GT3_CANONICALS = ("bmw_m4_gt3", "aston_martin_vantage_gt3", "porsche_992_gt3r", ...)
GTP_CANONICALS = ("bmw", "ferrari", "cadillac", "porsche", "acura")
... if car_canonical and car_canonical.lower() in GTP_CANONICALS + GT3_CANONICALS else ...
```

Then teach `apply_live_control_overrides` to dispatch on the GT3 family.

### A2 — BLOCKER — `analyzer/setup_reader.py:404-408`

```python
front_arb_size=str(front.get("ArbSize", "") or front.get("ArbSetting", "")),
front_arb_blade=_parse_int(front.get("ArbBlades") or front.get("ArbAdj")),
rear_arb_size=str(rear.get("ArbSize", "")),
rear_arb_blade=_parse_int(rear.get("ArbBlades") or rear.get("ArbAdj")),
```

Per `docs/gt3_session_info_schema.md`:

| Car | Front ARB path | Rear ARB path |
|---|---|---|
| BMW M4 GT3 | `Chassis.FrontBrakes.ArbBlades` | `Chassis.Rear.ArbBlades` |
| Aston | `Chassis.FrontBrakesLights.FarbBlades` | `Chassis.Rear.RarbBlades` |
| Porsche 992 GT3 R | `Chassis.FrontBrakesLights.ArbSetting` (single integer, NOT blade) | `Chassis.Rear.RarbSetting` |

Today's code reads `front.ArbBlades` (i.e. `Chassis.Front.ArbBlades`) — which does not exist in any GT3 YAML. ALL three GT3 cars get `front_arb_blade=0` and `rear_arb_blade=0` silently. `front_arb_size` is also empty for all GT3 (no `ArbSize` field).

**Fix shape**: per-car YAML-path table keyed by car canonical, mapped to `front_arb_blade` / `rear_arb_blade`, and either drop `front_arb_size` for GT3 or repurpose it as `front_arb_setting` for Porsche.

### A3 — BLOCKER — `analyzer/setup_reader.py:392-394`

```python
front_heave_nmm=_parse_float(front.get("HeaveSpring")),
front_heave_perch_mm=_parse_float(front.get("HeavePerchOffset")),
rear_third_nmm=_parse_float(rear.get("ThirdSpring") or rear.get("HeaveSpring")),
```

GT3 cars have no heave/third spring — `car_model/cars.py:1758-1769` enforces `heave_spring=None` for `GT3_COIL_4WHEEL`. The four GT3 corners use `Chassis.LeftFront.SpringRate`, `Chassis.RightFront.SpringRate`, `Chassis.LeftRear.SpringRate`, `Chassis.RightRear.SpringRate` (paired left/right per axle in the garage UI, but exposed per-corner in YAML).

The current code leaves `front_heave_nmm=0`, `rear_third_nmm=0`, then `summary()` prints `Heave 0/0` and the auto_calibrate / heave_solver layers receive zero-stiffness inputs.

The mapping target solver fields are `front_corner_spring_nmm` / `rear_corner_spring_nmm` (or whatever the GT3 step-3 solver expects after Phase 2 spring layer lands). For GT3 today, `rear_spring_nmm` (line 400) IS read from `lr.SpringRate` so the rear path is partially fine, but the front side reads `lf.TorsionBarOD` (A4) instead of `lf.SpringRate`.

**Fix shape**: branch on `car.suspension_arch is GT3_COIL_4WHEEL`:

```python
if car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
    front_corner_spring_nmm = (_parse_float(lf.get("SpringRate")) + _parse_float(rf.get("SpringRate"))) / 2
    rear_corner_spring_nmm  = (_parse_float(lr.get("SpringRate")) + _parse_float(rr.get("SpringRate"))) / 2
    # leave heave/third at 0 — they don't exist
```

Add `front_corner_spring_nmm` to `CurrentSetup` (or reuse an existing field with a clear architectural contract).

### A4 — BLOCKER — `analyzer/setup_reader.py:399`

```python
front_torsion_od_mm=_parse_float(lf.get("TorsionBarOD")),
```

GT3 has no torsion bars at any corner. `lf.TorsionBarOD` does not exist in any GT3 YAML. Result: `front_torsion_od_mm = 0.0`. Downstream `analyzer/setup_schema.py:316` then asks `gr.front_torsion_od_mm[0]` for range information — for GT3 cars `garage_ranges.front_torsion_od_mm` is unset and AttributeError or default-tuple noise leaks into the schema. Source YAML path: there is no GT3 source for this — solver target must be `front_corner_spring_nmm` (see A3).

### A5 — BLOCKER — `analyzer/setup_reader.py:301-304`

```python
brake_spec = systems.get("BrakeSpec", {}) or brakes.get("BrakeSpec", {})
diff_spec = systems.get("RearDiffSpec", {}) or brakes.get("RearDiffSpec", {})
front_diff_spec = systems.get("FrontDiffSpec", {}) or brakes.get("FrontDiffSpec", {})
tc = systems.get("TractionControl", {}) or brakes.get("TractionControl", {})
```

GT3 IBT YAML has NO `Systems` block and NO `BrakesDriveUnit`. Per `docs/gt3_session_info_schema.md`:

- BMW: brake fields under `Chassis.FrontBrakes` (`FrontMasterCyl`, `RearMasterCyl`, `BrakePads`, `CenterFrontSplitterHeight`)
- Aston / Porsche: brake fields under `Chassis.FrontBrakesLights`
- Diff under `Chassis.GearsDifferential` (`GearStack`, `FrictionFaces`, `DiffPreload`)
- TC/ABS under `Chassis.InCarAdjustments` (`AbsSetting`, `TcSetting`, `BrakePressureBias`, `FWtdist`, `CrossWeight`)
- Fuel under `Chassis.Rear.FuelLevel` (BMW/Aston) or `Chassis.FrontBrakesLights.FuelLevel` (Porsche)

Result for every GT3 IBT today: `brake_spec = {}`, `diff_spec = {}`, `tc = {}`, `fuel = {}`. Every brake/diff/TC/fuel field on `CurrentSetup` is silently zero. `apply_live_control_overrides` then sees `brake_bias_pct=0` and overwrites it from telemetry — the only reason GT3 brake-bias appears at all today is the live-control rescue path (and only for "bmw" / "ferrari" adapters per A1 — which GT3 cars don't match).

**Source YAML path → target solver field** mapping required:

| Source path (BMW/Aston/Porsche) | Target solver field |
|---|---|
| `Chassis.FrontBrakes.FrontMasterCyl` (B); `Chassis.FrontBrakesLights.FrontMasterCyl` (A,P) | `front_master_cyl_mm` |
| `Chassis.FrontBrakes(Lights).RearMasterCyl` | `rear_master_cyl_mm` |
| `Chassis.FrontBrakes(Lights).BrakePads` | `pad_compound` |
| `Chassis.FrontBrakes(Lights).CenterFrontSplitterHeight` | NEW — `splitter_height_mm` |
| `Chassis.GearsDifferential.GearStack` | `gear_stack` |
| `Chassis.GearsDifferential.FrictionFaces` | `diff_clutch_plates` (rename or alias) |
| `Chassis.GearsDifferential.DiffPreload` | `diff_preload_nm` |
| `Chassis.InCarAdjustments.BrakePressureBias` | `brake_bias_pct` |
| `Chassis.InCarAdjustments.AbsSetting` | NEW — `abs_setting` (today only on telemetry side) |
| `Chassis.InCarAdjustments.TcSetting` | `tc_gain` (BMW "n (TC)") / `tc_slip` (Aston "n (TC SLIP)" — schema notes the label varies) |
| `Chassis.InCarAdjustments.FWtdist` | NEW — `front_weight_dist_pct` (real measurement, not derived) |
| `Chassis.InCarAdjustments.CrossWeight` | NEW — `cross_weight_pct` |
| `Chassis.InCarAdjustments.EpasSetting` (Aston only) | NEW — `epas_setting` |
| `Chassis.InCarAdjustments.ThrottleResponse` (Aston) / `Chassis.FrontBrakesLights.ThrottleShapeSetting` (Porsche) | NEW — `throttle_map` |
| `Chassis.Rear.FuelLevel` (B,A); `Chassis.FrontBrakesLights.FuelLevel` (P) | `fuel_l` |

### A6 — BLOCKER — `analyzer/setup_reader.py:295-296`

```python
aero_settings = tires_aero.get("AeroSettings", {})
aero_calc = tires_aero.get("AeroCalculator", {})
```

Per schema there is no `AeroSettings` for GT3. Aero balance lives under `TiresAero.AeroBalanceCalc` (BMW, Porsche) or `TiresAero.AeroBalanceCalculator` (Aston). Wing-angle field is `WingSetting` (BMW, Porsche) or `RearWingAngle` (Aston) — see A2-style per-car table.

Source paths:

- `TiresAero.AeroBalanceCalc.WingSetting` / `AeroBalanceCalculator.RearWingAngle` → `wing_angle_deg`
- `TiresAero.AeroBalanceCalc.FrontRhAtSpeed` → `front_rh_at_speed_mm`
- `TiresAero.AeroBalanceCalc.RearRhAtSpeed` → `rear_rh_at_speed_mm`
- `TiresAero.AeroBalanceCalc.FrontDownforce` → `df_balance_pct` (note: schema is `FrontDownforce`, not `DownforceBalance`)
- L/D ratio: NOT exposed in GT3 YAML — leave None

Aero calculator under chassis is also at `Chassis.Rear.WingAngle` (BMW) / `Chassis.Rear.RearWingAngle` (Aston) / `Chassis.Rear.WingSetting` (Porsche). Today's code reads neither.

### A7 — BLOCKER — `analyzer/setup_reader.py:447-461`

Covered by A5 (Systems is gone for GT3). Specifically:

- `brake_bias_pct = brake_spec.BrakePressureBias` → for GT3 source is `Chassis.InCarAdjustments.BrakePressureBias`
- `diff_preload_nm = diff_spec.Preload` → GT3 source is `Chassis.GearsDifferential.DiffPreload`
- `diff_clutch_plates = diff_spec.ClutchFrictionPlates` → GT3 source is `Chassis.GearsDifferential.FrictionFaces`
- `tc_gain = tc.TractionControlGain` and `tc_slip = tc.TractionControlSlip` → GT3 has only `Chassis.InCarAdjustments.TcSetting` (single integer), no separate gain/slip channels
- `fuel_l = fuel.FuelLevel` → GT3 source per car (see A5)
- `gear_stack = gear_ratios.GearStack` → GT3 source `Chassis.GearsDifferential.GearStack`
- per-gear `SpeedInFirst`...`SpeedInSeventh` are NOT exposed in GT3 YAML (per schema "no `GearRatios` section, no per-gear speed echo")

### A8 — BLOCKER — `analyzer/setup_reader.py:316-351`

The damper-layout decision tree:

```python
front_heave_damp = dampers.get("FrontHeave", {})
...
is_heave_roll_layout = bool(front_heave_damp)
is_porsche_layout = is_heave_roll_layout and car_canonical.lower() == "porsche"
if is_porsche_layout: ...
elif is_heave_roll_layout: ...
else:
    lf_damp = dampers.get("LeftFrontDamper", lf)
    ...
```

GT3 IBT YAML has `Dampers.FrontDampers` and `Dampers.RearDampers` (per-axle, 8 channels total — schema's "Critical finding" section). Neither `FrontHeave` nor `LeftFrontDamper` exists. The code falls through to the `else` Ferrari path, which sets `lf_damp = dampers.get("LeftFrontDamper", lf)` — defaults to `lf` (i.e. `Chassis.LeftFront`), which on a GT3 IBT does NOT have damper keys. Every damper field becomes 0.

**Fix shape**: add a fourth branch:

```python
elif car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
    front_axle_damp = dampers.get("FrontDampers", {})
    rear_axle_damp = dampers.get("RearDampers", {})
    lf_damp = rf_damp = front_axle_damp
    lr_damp = rr_damp = rear_axle_damp
```

Then read `LowSpeedCompressionDamping` / `HighSpeedCompressionDamping` / `LowSpeedReboundDamping` / `HighSpeedReboundDamping` (note: GT3 channel names are spelled out, not abbreviated `LsCompDamping`).

**Source YAML path → target solver field**:

| Source | Target |
|---|---|
| `Dampers.FrontDampers.LowSpeedCompressionDamping` | `front_ls_comp` |
| `Dampers.FrontDampers.HighSpeedCompressionDamping` | `front_hs_comp` |
| `Dampers.FrontDampers.LowSpeedReboundDamping` | `front_ls_rbd` |
| `Dampers.FrontDampers.HighSpeedReboundDamping` | `front_hs_rbd` |
| `Dampers.RearDampers.*` | `rear_*` (same four channels) |

`HsCompDampSlope` does NOT exist for GT3 — `front_hs_slope` and `rear_hs_slope` should be `None` for GT3 cars (or a sentinel ignoring the field).

### A9, A10 — BLOCKER — `analyzer/setup_reader.py:413-417`

```python
front_toe_mm=_parse_float(front.get("ToeIn")),
rear_toe_mm=(
    _parse_float(lr.get("ToeIn")) + _parse_float(rr.get("ToeIn"))
) / 2.0 or _parse_float(rear.get("ToeIn")),
```

GT3 paths:

- BMW front toe: `Chassis.FrontBrakes.TotalToeIn` (paired)
- Aston front toe: `Chassis.FrontBrakesLights.TotalToeIn`
- Porsche front toe: `Chassis.FrontBrakesLights.TotalToeIn`
- BMW/Aston rear toe: per-wheel `Chassis.LeftRear.ToeIn` + `Chassis.RightRear.ToeIn`
- Porsche rear toe: paired `Chassis.Rear.TotalToeIn` ONLY (per schema "Porsche-exception")

The current code reads `Chassis.Front.ToeIn` (does not exist for GT3) for front, and averages per-wheel rear (works for BMW/Aston but Porsche has `Chassis.Rear.TotalToeIn` instead — the `or _parse_float(rear.get("ToeIn"))` rescue almost works, but the Porsche field is `TotalToeIn`, not `ToeIn`, so it returns 0).

### A11 — BLOCKER — `analyzer/setup_reader.py:303`

`front_diff_spec = systems.get("FrontDiffSpec", {}) or brakes.get("FrontDiffSpec", {})` — GT3 has only one diff (rear). `front_diff_spec` is always `{}` for GT3, yielding `front_diff_preload_nm=0` which is correct, but the field is misleading. Recommend gating in the GT3 branch and setting `front_diff_preload_nm=None` so report code distinguishes "no front diff" from "front diff at 0 Nm".

### A12, A13 — BLOCKER — `analyzer/setup_schema.py:82-137, 311-318`

`_KNOWN_FIELD_MAP` and `_manual_constraints` enumerate GTP-only LDX field IDs:

```
CarSetup_Chassis_Front_HeaveSpring
CarSetup_Chassis_Front_HeavePerchOffset
CarSetup_Chassis_LeftFront_TorsionBarOD
CarSetup_Chassis_Front_ArbSize
CarSetup_Chassis_Front_ArbBlades
CarSetup_Dampers_LeftFrontDamper_*
CarSetup_Systems_BrakeSpec_*
CarSetup_Systems_RearDiffSpec_*
CarSetup_Systems_TractionControl_*
CarSetup_Systems_Fuel_*
CarSetup_Systems_GearRatios_*
CarSetup_Systems_HybridConfig_*  ← GT3 has no hybrid
```

For GT3 LDX (when those land) the field IDs will be `CarSetup_Chassis_FrontBrakes_ArbBlades`, `CarSetup_Chassis_LeftFront_SpringRate`, `CarSetup_Dampers_FrontDampers_LowSpeedCompressionDamping`, etc. The schema cannot describe a GT3 setup until the map is extended. `_manual_constraints` reaches into `gr.front_heave_nmm` and `gr.front_torsion_od_mm` — these attributes are not populated on GT3 `garage_ranges`, so the `if`-tree returns `(None, None, None)` for every GT3 field, meaning no constraint metadata reaches `SetupField`.

### A14, A15 — BLOCKER — `analyzer/sto_adapters.py:208-358`, `analyzer/sto_binary.py:28-34`

- `_KNOWN_ACURA_ORACLES` only handles two specific Acura GTP setup hashes. Every GT3 STO falls to `adapter_name="v3_container_only"` with empty `values={}`.
- `_ACURA_ROW_SPECS` (60 rows) hard-codes ORECA-style `Front Heave / Front Roll / Rear Heave / Rear Roll` damper tabs, torsion bar fields, Acura ARB labels. This template cannot be inherited for GT3 — GT3 needs a new row-spec list (4-corner spring, 8-channel per-axle damper, no heave/roll dampers).
- `_CAR_HINTS` regex catalog (`acura|arx06`, `bmw|bmwlmdh`, `cadillac|vseries`, `ferrari|499p`, `porsche|963`) does not match GT3 STO filenames. GT3 STO filename hints (per `iracing_car_path` strings in `car_model/cars.py`): `bmwm4gt3`, `amvantageevogt3`, `porsche992rgt3`. Add a new section to `_CAR_HINTS` keyed on canonical_name.

### A16 — BLOCKER — `analyzer/extract.py:96-97, 678`

```python
lltd_measured: float | None = None              # Backward-compatible alias of roll_distribution_proxy
roll_distribution_proxy: float | None = None    # RH-based proxy, not true LLTD
...
state.roll_distribution_proxy = front_moment / total_moment
state.lltd_measured = state.roll_distribution_proxy
```

This is the documented "phantom proxy" bug from CLAUDE.md / project memory. The proxy `(front_RH_diff × tw_f²) / (... + rear_RH_diff × tw_r²)` is geometric and insensitive to spring rate; setting `lltd_measured = roll_distribution_proxy` propagates a misnamed value into every downstream consumer that thinks `lltd_measured` is real LLTD.

For GT3 there is no LLTD calibration whatsoever (`measured_lltd_target=None` for every GT3 stub in `car_model/cars.py`). Carrying the alias forward into GT3 is doubly wrong: (a) it is not LLTD; (b) GT3 has no validated baseline against which to even sanity-check the proxy.

**Fix shape**: drop the `state.lltd_measured = state.roll_distribution_proxy` assignment for GT3 cars (or globally, in line with the project memory direction). Solver code already migrated to `roll_distribution_proxy`; analyzer/diagnose.py:99-103 still keys off the alias and should be updated too.

### A17, A18 — BLOCKER — `analyzer/extract.py:1413-1514`, `analyzer/diagnose.py:204-208, 266-267, 321-388`

`_extract_heave_deflection`:

```python
if ibt.has_channel("HFshockDefl"):
    hf_defl = np.abs(ibt.channel("HFshockDefl")[start:end + 1]) * 1000
    ...
    state.front_heave_travel_used_pct = round(full_p99 / defl_max_ref * 100, 1)
    state.heave_bottoming_events_front = int(np.sum(hf_defl > bottom_thresh))
    state.front_heave_defl_braking_p99_mm = ...
    state.front_heave_travel_used_braking_pct = ...
```

For GT3 there is no heave element. If iRacing GT3 IBTs do not emit `HFshockDefl` / `HRshockDefl`, the function early-exits and the metrics stay None — that path is fine. **But** if the channels exist but mean something different (e.g. mapped to corner-shock travel for legacy compatibility), the function will set `front_heave_travel_used_pct` to a value that has no physical meaning, and `_check_safety` (diagnose.py:321-388) will fire critical-severity heave bottoming alarms emitting "Fix: stiffen heave spring" — a parameter that does not exist on GT3.

The corresponding diagnose.py predicates (`front_direct_bottoming = ... or m.front_heave_travel_used_pct > 85.0`) need an architecture gate:

```python
if car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
    # Skip heave-element checks; use corner spring travel instead.
    front_direct_bottoming = (
        ... or m.heave_bottoming_events_front > 0 is replaced with
        per-corner shock-defl travel-used percentage from LF/RF/LR/RRshockDefl
    )
```

GT3 still has bottoming risk (every coil hits bump rubber gap if too soft), but the channel and metric names are different. A new `_extract_corner_travel` function is needed and `_check_safety` should branch on `car.suspension_arch`.

### A19 — BLOCKER — `analyzer/causal_graph.py:117-125, 307-313`

Causal nodes:

```python
"heave_too_soft": CausalNode(
    "heave_too_soft", "Front heave spring too soft",
    "root_cause", "platform",
    parameter="front_heave_nmm", fix_direction="increase",
),
"heave_too_stiff": CausalNode(
    "heave_too_stiff", "Front heave spring too stiff",
    "root_cause", "platform",
    parameter="front_heave_nmm", fix_direction="decrease",
),
```

`parameter="front_heave_nmm"` is a no-op for GT3. Need parallel root causes:

```python
"front_corner_spring_too_soft": CausalNode(
    "front_corner_spring_too_soft", "Front corner springs too soft",
    "root_cause", "platform",
    parameter="front_corner_spring_nmm", fix_direction="increase",
),
```

Plus all `CausalEdge` entries pointing into `heave_too_soft` (lines 307-313) need GT3 equivalents pointing into `front_corner_spring_too_soft`. Root-cause selection in `analyze_causes` should dispatch on architecture so the GT3 chain doesn't include heave nodes (and the GTP chain doesn't include corner-spring-only nodes).

### A20 — DEGRADED — `analyzer/setup_reader.py:454-455`

`front_master_cyl_mm=_parse_float(brake_spec.get("FrontMasterCyl"))`. Because of A5 `brake_spec` is `{}` for GT3, so this is 0. Fix is part of A5 (re-route to `Chassis.FrontBrakes(Lights).FrontMasterCyl`).

### A21 — DEGRADED — `analyzer/setup_reader.py:447`

`brake_bias_pct=_parse_float(brake_spec.get("BrakePressureBias"))`. GT3 source is `Chassis.InCarAdjustments.BrakePressureBias`. Live-control rescue (line 580 mappings) only applies for `adapter_name in {"bmw","ferrari"}` — GT3 cars never get the live override either.

### A22 — DEGRADED — `analyzer/setup_reader.py:459`

`diff_clutch_plates=_parse_int(diff_spec.get("ClutchFrictionPlates"))`. GT3 schema names this `FrictionFaces` under `Chassis.GearsDifferential`. Either rename the field or add a per-architecture field-name mapping.

### A23 — DEGRADED — `analyzer/setup_reader.py:465`

`gear_stack=str(gear_ratios.get("GearStack", "") or "")` — gear stack on GT3 is at `Chassis.GearsDifferential.GearStack`. Per-gear `SpeedInFirst` etc. do not exist in GT3 YAML; setting them to 0 is fine but report code that prints them needs a None gate.

### A24 — DEGRADED — `analyzer/setup_reader.py:431-444`

Roll-damper / 3rd-damper / `RollSpring` reads (`front_roll_ls`, `rear_3rd_ls_comp`, `front_roll_spring_nmm`, etc.) are unconditional. GT3 has none of those keys; values stay 0. Harmless except for noise in the dataclass dump (A29 / A38).

### A25 — DEGRADED — `analyzer/setup_reader.py:546-547`

```python
f"Heave {self.front_heave_nmm:.0f}/{self.rear_third_nmm:.0f}  "
f"FARB {self.front_arb_size}/{self.front_arb_blade} "
```

For GT3: `Heave 0/0` and `FARB /5` (no size). `summary()` should branch on architecture.

### A26 — DEGRADED — `analyzer/extract.py:661-662`

```python
tw_f = getattr(car.arb, "track_width_front_mm", 1730.0)
tw_r = getattr(car.arb, "track_width_rear_mm", 1650.0)
```

This violates the project's "no silent fallbacks" rule (CLAUDE.md key principle 8). The defaults are BMW GTP values; for any GT3 car missing `track_width_front_mm` / `track_width_rear_mm`, the LLTD-proxy computation silently uses BMW geometry. Combined with A16, the LLTD proxy on GT3 is wrong on multiple axes simultaneously.

**Fix**: direct attribute access; raise / log if absent. Per `gt3_per_car_spec.md`, e.g. Corvette Z06 GT3.R has asymmetric tracks 1648/1586 mm.

### A27 — DEGRADED — `analyzer/extract.py:739`

`track_w_m = car.arb.track_width_front_mm / 1000` — direct access, no fallback. Fine for the body-roll path; only flagged for symmetry with A26 (one path is safe, the other isn't).

### A28 — DEGRADED — `analyzer/diagnose.py:99-103`

`Diagnosis.lltd_pct = _positive_or_zero(measured.roll_distribution_proxy) * 100 ...`. Display-only, but the report header treats `lltd_pct` as authoritative ("LLTD measured: 51.2%"). For GT3 there is no validated proxy baseline; report should label this as `roll_distribution_proxy_pct` and warn that it is geometric.

### A29 — DEGRADED — `analyzer/setup_schema.py:519-539`

`build_setup_schema` short-circuits non-Ferrari cars to a generic `is_dataclass(current_setup)` dump. Allowed-range, options, and resolution metadata never reach the GT3 schema rows. Should call `_manual_constraints` for GT3 too — but that requires A12/A13 (per-car field-id table) first.

### A30 — DEGRADED — `analyzer/setup_schema.py:402-408`

`_FERRARI_PUBLIC_ALIASES` maps `front_heave_spring_nmm`, `rear_third_spring_nmm`, `front_torsion_od_mm` to indexed Ferrari aliases with unit `"idx"`. This dict is keyed by `canonical_key` not by car, so any car-agnostic schema consumer that asks for `front_heave_spring_nmm` will receive the Ferrari `front_heave_index` alias. Currently only used inside `_registry_backed_fields` which is gated on `car_name == "ferrari"`, so the leak is contained — but the dict belongs in the Ferrari-only section, not the module top-level.

### A31 — DEGRADED — `analyzer/sto_adapters.py:208-358`

`_KNOWN_ACURA_ORACLES` and `_ACURA_ROW_SPECS` are GTP-only. For GT3 a parallel `_KNOWN_GT3_ORACLES` and `_GT3_ROW_SPECS` will be needed, with row-specs reflecting the per-axle damper tabs (`Dampers.FrontDampers`, `Dampers.RearDampers` instead of `Front Heave / Front Roll / Rear Heave / Rear Roll`).

### A32 — DEGRADED — `analyzer/extract.py:446-462, 492-509, 869-872`

Per-corner shock-velocity decode is per-corner, which matches GT3 physics (each corner has its own damper internally even though the garage UI exposes per-axle adjusters). PENDING: confirm BMW M4 GT3 IBT exposes `LFshockVel`, `RFshockVel`, `LRshockVel`, `RRshockVel` channels — if so, no change needed. If GT3 IBT exposes only per-axle `FrontDamperVel` / `RearDamperVel`, the extract layer needs a third architectural branch alongside the per-corner / heave-roll synthesis branches. We do not currently have GT3 IBT channel inventory in this repo.

### A33 — DEGRADED — `analyzer/setup_reader.py:279-283`

`from_ibt(ibt, car_canonical=...)` whitelist is `("bmw","ferrari","cadillac","porsche","acura")`. GT3 canonicals (e.g. `bmw_m4_gt3`) are NOT whitelisted, so even if a caller passes the right canonical, the function falls through to structural sniffing (A1).

### A34 — DEGRADED — `analyzer/recommend.py`

`_recommend_for_problem` (truncated in this audit but visible in the file) maps the causal-graph `parameter` field back onto `CurrentSetup` attributes via `setattr`/`getattr`. For GT3 the causal graph (A19) maps `heave_too_soft` to `front_heave_nmm`, which on a GT3 `CurrentSetup` is 0 and unsettable — recommendations either no-op or emit numerical noise.

### A35 — DEGRADED — `analyzer/segment.py:42-77`

`CornerAnalysis.front_rh_min_mm` (splitter proximity) — depends on `LFrideHeight` / `RFrideHeight` channels existing in GT3 IBT. PENDING IBT verification.

### A36 — COSMETIC — `analyzer/setup_reader.py:96-104, 113-117`

Comments in the dataclass embed GTP-specific notes ("Ferrari only (has front diff)"). Update for GT3 once fields settle.

### A37 — COSMETIC — `analyzer/causal_graph.py`

Apart from `heave_too_*` (A19), the causal graph is architecture-agnostic. Symptom nodes (`symptom_front_bottoming`, `symptom_excursion_high`, `symptom_understeer_low_speed`, etc.) translate to GT3 unchanged.

### A38 — COSMETIC — `analyzer/driver_style.py`

Module is architecture-agnostic; consumes only `CornerAnalysis` and raw IBT channels. No changes expected once A1-A18 land.

## Risk summary

- **19 BLOCKER findings**. With current code, every GT3 IBT analyzed today produces a `CurrentSetup` that has correct corner shock velocities and tyre pressures and almost nothing else: zero springs, zero dampers, zero ARB blades, zero brake bias, zero diff preload, `adapter_name="unknown"`, no live-control overrides, and false-positive "Front heave spring too soft" critical-severity safety alarms. The pipeline downstream of analyzer cannot do anything sensible with this output.
- **17 DEGRADED findings**. The display, schema, and STO-decoder layers have no GT3 entry points. Reports built on top of analyzer output for a GT3 IBT will be silently misleading (zero or "unknown" everywhere, plus phantom heave alarms).
- **3 COSMETIC findings**. Driver-style and most of the causal graph carry over cleanly; only naming/comment updates needed.

The single most damaging cluster is A1+A5+A8+A18: GT3 sessions misidentify as `unknown`, lose ALL Systems-block fields, lose ALL damper fields, and trigger false heave-bottoming criticals — i.e. the analyzer says nothing useful and shouts about a non-existent component. Recommend gating all heave/third/torsion logic on `car.suspension_arch is GT3_COIL_4WHEEL` BEFORE shipping any other Phase 2 GT3 work.

## Effort estimate

- A1, A33 (adapter_name whitelist + GT3 canonical acceptance): **0.5 day**
- A2, A6, A7 (per-car YAML path table for ARB / aero / Systems): **1.5 days** — needs a new `_GT3_FIELD_MAP[car_canonical]` lookup with three populated entries (BMW/Aston/Porsche) and stubs for the other 8 cars
- A3, A4, A9, A10, A11, A20, A21, A22, A23 (corner-spring/toe/diff/brake re-routing inside `from_ibt`): **2 days** — bulk of the architectural branch
- A8 (damper layout fourth branch + per-axle field reads): **1 day**
- A12, A13, A29, A30 (`analyzer/setup_schema.py` per-car field-id tables): **2 days**
- A14, A15, A31 (STO-adapter GT3 oracles + row specs + filename hints): **2 days** — assumes we eventually get one canonical GT3 STO per car to anchor against
- A16 (drop `lltd_measured` alias for GT3, ideally globally): **0.5 day** — already on the project's known-debt list
- A17, A18 (architecture gate on heave deflection extract + diagnose checks; new corner-travel extract for GT3): **2 days**
- A19, A34 (causal-graph GT3 nodes + recommend-engine dispatch): **1 day**
- A24, A25, A26, A27, A28, A35, A36, A37, A38 (cleanup, comments, defensive fallback removal): **1 day**

Total: **~13 person-days** for a solid GT3 analyzer cut. If we accept Step 1 (rake/RH-only) plus per-axle dampers as the v1 GT3 deliverable, the critical-path subset is A1 + A2 + A3 + A5 + A6 + A8 + A18 + A19, roughly **5-6 days**.

## Dependencies

- **Phase 0 (already shipped, PR gt3-phase0-foundations)**: `SuspensionArchitecture.GT3_COIL_4WHEEL` enum, `BMW_M4_GT3` / `ASTON_MARTIN_VANTAGE_GT3` / `PORSCHE_992_GT3R` car stubs, `iracing_car_path` fields, parsed aero NPZ files. ✅
- **`car_model/setup_registry.py`**: needs GT3-aware canonical → YAML-path tables. Today `CAR_FIELD_SPECS` has only GTP entries (BMW/Ferrari/Acura). Required by `analyzer/setup_schema.py` and `output/setup_writer.py`.
- **`car_model/cars.py:GarageRanges`**: needs `front_corner_spring_nmm` (per-axle paired range), `splitter_height_mm`, `bump_rubber_gap_*_mm`, `front_weight_dist_pct` etc. populated for at least BMW M4 GT3. Without these, `analyzer/setup_schema.py:_manual_constraints` cannot produce range metadata for GT3 fields.
- **`output/setup_writer.py`**: per-car GT3 PARAM_IDS dispatch table (per `gt3_session_info_schema.md` "Implication for the solver" section). Setup-writer audit is a sibling to this analyzer audit and must land before round-tripping IBT → analyzer → solver → setup.
- **GT3 IBT channel inventory**: need to confirm `LFshockVel` / `LFshockDefl` / `LFrideHeight` etc. exist on GT3 IBTs before deciding whether `_extract_heave_deflection` and the per-corner shock-velocity paths in `analyzer/extract.py` can be reused as-is. PR description notes 3 GT3 IBTs are now in `ibtfiles/` for reference but this audit did not open them.
- **STO-adapter audit (sibling)**: `analyzer/sto_adapters.py` GT3 work depends on having canonical STO files per GT3 car to anchor `_KNOWN_GT3_ORACLES`. Until then the adapter can only do the v3-container outer decode, which is already car-agnostic.
- **`pipeline/produce.py`**: today calls `CurrentSetup.from_ibt(ibt, car_canonical=...)` with the canonical name from `car_model.registry`. Once A1/A33 land, the same site needs to pass the GT3 canonical name through; verify pipeline's existing hand-off doesn't re-canonicalize to a GTP family name.
