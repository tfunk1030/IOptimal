# GT3 Phase 2 Audit — `car_model/` registry, garage state, auto-calibrate

## Scope

This audit covers four files that together encode the per-car schema, garage prediction, auto-calibration pipeline, and canonical-name resolution:

- `car_model/setup_registry.py` — `FieldDefinition` registry + per-car `CarFieldSpec` maps (`_BMW_SPECS`, `_FERRARI_SPECS`, `_PORSCHE_SPECS`, `_CADILLAC_SPECS`, `_ACURA_SPECS`) used by setup reader, schema validator, setup writer.
- `car_model/garage.py` — `GarageSetupState` dataclass, `DirectRegression` extractor pool, `GarageOutputModel` (RH + deflection regressions), all pinned to GTP heave/third/torsion semantics.
- `car_model/auto_calibrate.py` (3,440 lines) — IBT → calibration-point ingestion, `_UNIVERSAL_POOL` / `_FRONT_POOL` / `_REAR_POOL` regression pools, `fit_models_from_points()`, `apply_to_car()`, CLI.
- `car_model/registry.py` — `_CAR_REGISTRY` (canonical name + screen name + STO id + aero folder) and `_TRACK_ALIASES`.

Reference data: `docs/gt3_session_info_schema.md`, `docs/gt3_session_info_porsche_992_gt3r_spielberg_2026-04-26.yaml`, `docs/gt3_per_car_spec.md`, `car_model/cars.py:3196-3554` (the three GT3 stubs).

## Summary table

| # | Severity | Module | Issue | Effort |
|---|---|---|---|---|
| 1 | BLOCKER | `car_model/registry.py:54-72` | `_CAR_REGISTRY` has 5 GTP entries; `resolve_car("BMW M4 GT3 EVO")` / `resolve_car("bmwm4gt3")` returns the GTP BMW (substring "bmw" matches). Three GT3 stubs in `car_model/cars.py` are unreachable from any IBT-driven path. | M |
| 2 | BLOCKER | `car_model/setup_registry.py:539-545` | `CAR_FIELD_SPECS` keyed only on GTP names. `get_car_spec("bmw_m4_gt3", ...)` returns `None`; the writer/reader fall back to BMW GTP YAML paths and emit invalid XML for GT3. | L |
| 3 | BLOCKER | `car_model/setup_registry.py:74-234` | No `FieldDefinition` exists for **any** of the new GT3 garage params: `BumpRubberGap` (per-corner mm), `CenterFrontSplitterHeight` (mm), `ThrottleResponse`, `ThrottleShapeSetting`, `EpasSetting` (Aston only), `EnduranceLights`, `NightLedStripColor`, `DashDisplayPage`. Setup reader/writer cannot ingest or emit them. | L |
| 4 | BLOCKER | `car_model/garage.py:38-113` | `GarageSetupState` is GTP-shaped: 11/13 fields require heave/third/torsion (e.g. `front_heave_nmm`, `rear_third_nmm`, `front_torsion_od_mm`, `front_heave_perch_mm`, `rear_third_perch_mm`, `torsion_bar_turns`). For GT3 these are all 0 by definition (`heave_spring=None`, `front_torsion_c=0.0`); `from_current_setup()` fills 0.0 for every one — the resulting state cannot drive any meaningful regression. | M |
| 5 | BLOCKER | `car_model/garage.py:159-228` | `DirectRegression._EXTRACTORS` keys (`front_heave`, `inv_heave`, `rear_third`, `inv_rear_third`, `torsion_od`, `inv_od4`, `torsion_turns`, …) are 100% GTP physics. GT3 needs front/rear coil compliance: `inv_front_spring`, `inv_rear_spring`, plus `bump_rubber_gap_*`, `splitter_height`. Today, fitting a GT3 regression on coil features produces the warning "DirectRegression: unknown feature 'inv_front_spring' — dropped from prediction" and the model collapses to its intercept. | M |
| 6 | BLOCKER | `car_model/auto_calibrate.py:75-114` | `_setup_key()` fingerprint includes `front_heave_setting`, `rear_third_setting`, `front_torsion_od_mm` — all 0 on GT3. Any two GT3 IBTs that vary front spring rate but keep coil-only fields equal will collapse to the **same** fingerprint and one of them is silently dropped. Effect: GT3 ingestion claims "0 unique setups" no matter how many IBTs are added. | M |
| 7 | BLOCKER | `car_model/auto_calibrate.py:124-205` | `CalibrationPoint` schema uses GTP-only fields: `front_heave_setting`, `rear_third_setting`, `front_torsion_od_mm`, `heave_spring_defl_*`, `heave_slider_defl_*`, `third_spring_defl_*`, `third_slider_defl_*`, `torsion_bar_turns`, `rear_torsion_bar_turns`. Missing for GT3: `front_spring_nmm` (per-corner coil), `rear_spring_nmm` already present but only for Porsche-963 semantics, `bump_rubber_gap_*` (LF/RF/LR/RR), `splitter_height_mm`. | M |
| 8 | BLOCKER | `car_model/auto_calibrate.py:1189-1232` | `_UNIVERSAL_POOL` features hard-code GTP physics: `front_heave`, `rear_third`, `inv_front_heave`, `inv_rear_third`, `inv_od4`, `od4`, `torsion_turns`, `rear_torsion_turns`. For GT3 there is no front heave spring → `col("front_heave_setting")` is all-zeros → the std filter at line 1273 silently drops every front-axis feature. The model then has no usable inputs and produces a constant intercept fit. | L |
| 9 | BLOCKER | `car_model/auto_calibrate.py:1242-1265` | `_FRONT_AXIS_NAMES` / `_REAR_AXIS_NAMES` are GTP-specific. GT3 has independent FL/FR/LR/RR coil corners — the front-axis pool needs `inv_front_spring` (compliance under aero) and `front_bump_rubber_gap`; the rear-axis pool needs `inv_rear_spring` for aero compliance and `rear_bump_rubber_gap`. None exist. | M |
| 10 | BLOCKER | `car_model/auto_calibrate.py:1404-1469` | Targets `heave_spring_defl_static`, `heave_spring_defl_max`, `heave_slider_defl_static`, `torsion_bar_turns`, `torsion_bar_defl`, `third_spring_defl_*`, `third_slider_defl_*` are all N/A on GT3. Fits are still attempted; if `np.std(col("heave_spring_defl_static_mm")) > 0.5` is False (because the column is all 0.0) the model is silently set to None — the user gets "calibration ran" with zero submodels, not "GT3 architecture skips Step 2". | S |
| 11 | DEGRADED | `car_model/auto_calibrate.py:3398-3401` | `from car_model.registry import track_key as _track_key`: returns `pt.track.replace(" ", "_")` for any unknown track. The Spielberg IBT comes through as `"Red Bull Ring Grand Prix"` (or similar) — there is no alias entry, so the per-track file becomes `models_red_bull_ring_grand_prix.json` instead of the conventional short slug. | XS |
| 12 | DEGRADED | `car_model/registry.py:126-150` | `_TRACK_ALIASES` has no entry for Spielberg / Red Bull Ring. The three sampled GT3 IBTs all reference this track. Without an alias, both `track_key()` and any code branching on `"spielberg"` (e.g. `BMW_M4_GT3.supported_track_keys=("spielberg",)` in `car_model/cars.py:3202`) will fail to match. | XS |
| 13 | DEGRADED | `car_model/auto_calibrate.py:3227-3229` | `--car` argparse `choices=["bmw", "cadillac", "ferrari", "acura", "porsche"]` — CLI rejects `--car bmw_m4_gt3` even though the car exists in `_CARS`. | XS |
| 14 | DEGRADED | `car_model/setup_registry.py:620-639` | `detect_car_adapter()` only branches on FrontHeave / Systems / Chassis tokens. GT3 cars have neither heave nor `Systems.*` — they share `Chassis.*` paths with BMW GTP, so the heuristic returns `"bmw"` and silently applies BMW GTP YAML mappings to GT3 IBTs. | XS |
| 15 | DEGRADED | `car_model/setup_registry.py:655-664` | `_car_name()` substring matches: `"bmw" in "bmw_m4_gt3"` returns canonical `"bmw"`, mapping GT3 cars onto the GTP BMW spec set. Every Ferrari helper (`_ferrari_public_numeric_value`, `public_output_value`, etc.) is exposed to GT3 BMW under the same canonical name. | XS |
| 16 | DEGRADED | `car_model/garage.py:65-113` | `GarageSetupState.from_current_setup()` uses `getattr(setup, "front_heave_nmm", 0.0)`. For GT3, the setup reader (once GT3-aware) is expected to populate `front_spring_nmm` (LF/RF) and `rear_spring_nmm` (LR/RR) — neither of which `from_current_setup()` reads. Default-0 fallback masks the missing data path. | S |
| 17 | DEGRADED | `car_model/auto_calibrate.py:1136-1176` | Index decode block runs `_csm.front_torsion_od_from_setting()` etc. unconditionally if `_csm.front_setting_index_range is not None`. For GT3 the entire torsion-bar pathway is dead code; passing through it for GT3 is a no-op but still calls into `car_model/cars.py` with `front_torsion_c=0.0`, leaving an audit trail of "decoded torsion bar" warnings on GT3 calibration runs. | XS |
| 18 | DEGRADED | `car_model/auto_calibrate.py:2654-2710` | `_CAR_PROTOCOL_HINTS` has hard-coded entries for the 5 GTP cars. CLI `--protocol` for any GT3 car silently falls through to the BMW hint block (line 2722: `_CAR_PROTOCOL_HINTS.get(car, _CAR_PROTOCOL_HINTS["bmw"])`). The instruction set tells a GT3 user to "Change front torsion bar OD to 13.90mm", which doesn't exist on a GT3 car. | XS |
| 19 | DEGRADED | `car_model/auto_calibrate.py:1505-1510` | ARB `spring_key` for the back-solve uses `front_heave_setting`, `front_torsion_od_mm`, `rear_third_setting` — all 0 on GT3. The grouping key for "same springs / different ARB" sessions collapses to a single bucket on GT3, so the ARB calibration always sees N=1 spring config and bails with `"insufficient variation"`. | M |
| 20 | DEGRADED | `car_model/auto_calibrate.py:1693-1769` | `m_eff` calibration assumes a heave spring (`pt.front_heave_setting > 0` / `pt.rear_third_setting > 0` gates). GT3 has no heave channel, so m_eff is never computed even with telemetry. For GT3 the equivalent is per-corner coil + tyre compliance — not implemented. | S |
| 21 | DEGRADED | `car_model/auto_calibrate.py:2655-2710` | Calibration-data filesystem layout assumes GTP CarPath naming (`data/calibration/{bmw,cadillac,ferrari,porsche,acura}/`). Per the GT3 onboarding convention (`docs/gt3_per_car_spec.md`), GT3 cars live under their full canonical name (`bmw_m4_gt3`, `aston_martin_vantage_gt3`, `porsche_992_gt3r`). `_CALIBRATION_DIR / car` works as long as `car == canonical_name`, but the existing 5 directories use the short GTP form. Cross-car fingerprints (e.g. Porsche GTP vs Porsche 992 GT3 R) MUST stay separate — verify no caller passes the short tag for a GT3 car. | S |
| 22 | DEGRADED | `car_model/auto_calibrate.py:2125-2647` | `apply_to_car()` writes only into GTP-shaped attributes: `car_obj.heave_spring.front_m_eff_kg`, `car_obj.deflection.heave_defl_*`, `car_obj.corner_spring.front_torsion_c`, etc. For GT3 (`heave_spring=None`, `front_torsion_c=0.0`), most blocks are wrapped in `try/except AttributeError` and silently skipped. There is no positive code path that writes a calibrated GT3 corner-spring or bump-rubber model. | M |
| 23 | DEGRADED | `car_model/garage.py:271-378` | `GarageOutputModel` defaults (`default_front_heave_nmm=50.0`, `default_front_heave_perch_mm=-13.0`, `default_front_torsion_od_mm=13.9`, etc.) are pure GTP. A GT3 instance constructed with these defaults will have a meaningless baseline state. `default_state()` returns a GarageSetupState with GTP-spec heave/third/torsion. | S |
| 24 | COSMETIC | `car_model/setup_registry.py:1004-1010` | `validate_registry()` allowlists `front_diff_preload_nm`, `rear_torsion_bar_turns`, `rear_torsion_bar_defl_mm`, `static_front_rh_mm`, `static_rear_rh_mm` — fields that exist in `FIELD_REGISTRY` without per-car specs. New GT3 fields will need to be added here too or the validator complains. | XS |
| 25 | COSMETIC | `car_model/auto_calibrate.py:78-94` | `_setup_key()` track field is included in the fingerprint comment ("different tracks produce different ride heights"). For GT3 cars whose IBTs all come from Spielberg today, the track key is the only differentiator across cars — fine, but worth re-reading once more tracks land. | XS |

## Findings (detail)

### BLOCKER 1 — `_CAR_REGISTRY` does not know about the 3 GT3 stubs

`car_model/registry.py:54-60`:

```python
_CAR_REGISTRY: list[CarIdentity] = [
    CarIdentity("bmw",      "BMW M Hybrid V8",    "BMW M Hybrid V8",    "bmwlmdh",         "bmw"),
    CarIdentity("porsche",  "Porsche 963",        "Porsche 963",        "porsche963",      "porsche"),
    CarIdentity("ferrari",  "Ferrari 499P",       "Ferrari 499P",       "ferrari499p",     "ferrari"),
    CarIdentity("cadillac", "Cadillac V-Series.R", "Cadillac V-Series.R", "cadillacvseriesr", "cadillac"),
    CarIdentity("acura",    "Acura ARX-06",       "Acura ARX-06",       "acuraarx06gtp",   "acura"),
]
```

`resolve_car("BMW M4 GT3 EVO")` flow:
1. Direct lookups (`_BY_CANONICAL`, `_BY_SCREEN_NAME`, `_BY_STO_ID`) miss.
2. `_BY_LOWER` miss.
3. Substring fallback (lines 91-105): `"bmw" in "bmw m4 gt3 evo"` → returns the GTP BMW. **Wrong car**.

Same for `resolve_car("bmwm4gt3")` (matches "bmw"), `resolve_car("Porsche 911 GT3 R (992)")` (matches "porsche", returns GTP 963), `resolve_car("porsche992rgt3")` (substring "porsche" wins). The Aston is the only GT3 that doesn't collide with a GTP screen name today, but the substring fallback is generally hostile.

Required fix shape:

```python
_CAR_REGISTRY: list[CarIdentity] = [
    # GTP class
    CarIdentity("bmw", ...),
    ...,
    # GT3 class — explicit canonical names match car_model/cars.py
    CarIdentity("bmw_m4_gt3", "BMW M4 GT3 EVO", "BMW M4 GT3 EVO", "bmwm4gt3", "bmw_m4_gt3"),
    CarIdentity("aston_martin_vantage_gt3", "Aston Martin Vantage GT3 EVO",
                "Aston Martin Vantage GT3 EVO", "amvantageevogt3", "aston_martin_vantage_gt3"),
    CarIdentity("porsche_992_gt3r", "Porsche 911 GT3 R (992)",
                "Porsche 911 GT3 R (992)", "porsche992rgt3", "porsche_992_gt3r"),
]
```

The substring fallback in `resolve_car()` (lines 91-104) MUST be tightened: either prefer exact matches and require longest-key match-or-nothing, or whitelist the substring rule to specifically known-aliased screen names. Currently the longest-match heuristic (`if len(key) > best_len`) does pick `"bmw m4 gt3 evo"` over `"bmw"` IF the GT3 is in the registry — so adding the entries plus keeping the longest-match rule SHOULD be sufficient. Verify with a unit test.

### BLOCKER 2 — `CAR_FIELD_SPECS` registry doesn't support GT3 canonical names

`car_model/setup_registry.py:539-545`:

```python
CAR_FIELD_SPECS: dict[str, dict[str, CarFieldSpec]] = {
    "bmw": _BMW_SPECS,
    "ferrari": _FERRARI_SPECS,
    "porsche": _PORSCHE_SPECS,
    "cadillac": _CADILLAC_SPECS,
    "acura": _ACURA_SPECS,
}
```

Required additions: `_BMW_M4_GT3_SPECS`, `_ASTON_VANTAGE_GT3_SPECS`, `_PORSCHE_992_GT3R_SPECS`. Each is roughly 40 entries: per-corner coils, dampers (per-axle, not per-corner — see schema doc), bump rubber gaps, splitter height, ARB encoding (varies per car), TC/ABS, brake bias.

Per `docs/gt3_session_info_schema.md`, these YAML paths diverge per car (BMW = `Chassis.FrontBrakes.ArbBlades`, Aston = `Chassis.FrontBrakesLights.FarbBlades`, Porsche = `Chassis.FrontBrakesLights.ArbSetting`) — a shared GT3 base spec dict + per-car overrides is the right pattern. Per the schema doc, the Porsche 992 has the most divergence and may not fit a "single base + minor overrides" layout cleanly.

The `_car_name()` helper at line 655 will additionally need updating to recognise the new canonical names (currently substring-matches `"bmw"` first and never reaches `"bmw_m4_gt3"`).

### BLOCKER 3 — Missing `FieldDefinition` entries for GT3-only garage params

The full list of fields present in the three GT3 IBTs that have NO `FieldDefinition` today:

| Canonical key (proposed) | YAML path (BMW M4 GT3) | Per-car YAML divergence | Notes |
|---|---|---|---|
| `lf_bump_rubber_gap_mm` | `Chassis.LeftFront.BumpRubberGap` | shared all 3 | Per-corner mm |
| `rf_bump_rubber_gap_mm` | `Chassis.RightFront.BumpRubberGap` | shared | Per-corner mm |
| `lr_bump_rubber_gap_mm` | `Chassis.LeftRear.BumpRubberGap` | shared | Per-corner mm |
| `rr_bump_rubber_gap_mm` | `Chassis.RightRear.BumpRubberGap` | shared | Per-corner mm |
| `front_bump_rubber_gap_mm` | (avg of LF/RF) | shared | Symmetric L/R, settable solver field |
| `rear_bump_rubber_gap_mm` | (avg of LR/RR) | shared | Symmetric L/R |
| `splitter_height_mm` | `Chassis.FrontBrakes.CenterFrontSplitterHeight` | BMW: `FrontBrakes.CenterFrontSplitterHeight`; Aston: `FrontBrakesLights.CenterFrontSplitterHeight`; Porsche: `FrontBrakesLights.CenterFrontSplitterHeight` | New aero parameter, mm |
| `endurance_lights` | `Chassis.FrontBrakes.EnduranceLights` | Aston only | string |
| `night_led_strip_color` | `Chassis.FrontBrakes.NightLedStripColor` | Aston + Porsche | string |
| `throttle_response_setting` | `Chassis.InCarAdjustments.ThrottleResponse` | Aston only | indexed/string ("4 (RED)") |
| `throttle_shape_setting` | `Chassis.InCarAdjustments.ThrottleShapeSetting` | Porsche only | indexed |
| `epas_setting` | `Chassis.InCarAdjustments.EpasSetting` | Aston only | indexed/string ("3 (PAS)") |
| `dash_display_page` | `Chassis.InCarAdjustments.DashDisplayPage` | Porsche only | string ("Race 2") |
| `tc_label` | `Chassis.InCarAdjustments.TcSetting` | shared (label format varies) | "n (TC)" / "n (TC SLIP)" / "n (TC-LAT)" |
| `abs_label` | `Chassis.InCarAdjustments.AbsSetting` | shared (label "n (ABS)") | indexed |
| `front_spring_nmm` | `Chassis.LeftFront.SpringRate` | shared | per-corner coil; pair with `lf_spring_nmm` per-corner if asymmetric |
| `lf_spring_nmm` | `Chassis.LeftFront.SpringRate` | shared | per-corner coil |
| `rf_spring_nmm` | `Chassis.RightFront.SpringRate` | shared | per-corner coil |
| `lr_spring_nmm` | `Chassis.LeftRear.SpringRate` | shared | per-corner coil |
| `rr_spring_nmm` | `Chassis.RightRear.SpringRate` | shared | per-corner coil |
| `friction_faces` | `Chassis.GearsDifferential.FrictionFaces` | shared | int |
| `cross_weight_pct` | `Chassis.InCarAdjustments.CrossWeight` | shared | percent |
| `f_wt_dist_pct` | `Chassis.InCarAdjustments.FWtdist` | shared | computed by iRacing — actual front weight distribution |

Damper paths also diverge from GTP: GT3 uses `Dampers.FrontDampers.LowSpeedCompressionDamping` (per-axle, written-out names) instead of GTP's per-corner `Chassis.LeftFront.LsCompDamping`. This requires either a parallel set of damper field definitions (keyed by axle, not corner) or a `per_corner=False` flag on the existing GTP damper definitions and per-car YAML override paths — recommend the latter.

The `tc_gain` / `tc_slip` GTP fields are not directly portable: GT3 IBTs expose only `TcSetting` (single integer with descriptive suffix), no separate gain/slip channels.

### BLOCKER 4-5 — `GarageSetupState` and `DirectRegression` are GTP-only

`car_model/garage.py:38-62`:

```python
@dataclass(frozen=True)
class GarageSetupState:
    front_pushrod_mm: float
    rear_pushrod_mm: float
    front_heave_nmm: float           # GTP only — GT3 has no heave spring
    front_heave_perch_mm: float      # GTP only
    rear_third_nmm: float            # GTP only
    rear_third_perch_mm: float       # GTP only
    front_torsion_od_mm: float       # GTP/Ferrari/Acura only
    rear_spring_nmm: float           # GTP-style "rear coil"; meaning differs for GT3
    rear_spring_perch_mm: float      # ?
    front_camber_deg: float
    ...
    torsion_bar_turns: float = 0.0           # Ferrari/Acura only
    rear_torsion_bar_turns: float = 0.0      # Ferrari/Acura only
```

For GT3, the "primary" spring channel is per-corner coil at all four corners, and `bump_rubber_gap_mm` per corner is a discrete gap that gates compression. Required new fields:

```python
# GT3 per-corner coils (None = car uses GTP heave instead)
lf_spring_nmm: float = 0.0
rf_spring_nmm: float = 0.0
lr_spring_nmm: float = 0.0
rr_spring_nmm: float = 0.0
# GT3 bump rubber gaps (None on GTP cars — they don't expose this)
lf_bump_rubber_gap_mm: float = 0.0
rf_bump_rubber_gap_mm: float = 0.0
lr_bump_rubber_gap_mm: float = 0.0
rr_bump_rubber_gap_mm: float = 0.0
# Splitter height (GT3 only)
front_splitter_height_mm: float = 0.0
```

`DirectRegression._EXTRACTORS` (`car_model/garage.py:193-222`) must add the matching feature names. Because the `predict()` path **drops unknown features with a warning** (line 178-186), a regression fit on `inv_lf_spring` today will silently zero out and the model becomes a useless intercept-only constant.

`from_current_setup()` (line 64-113) must read the new fields from a setup-reader output that doesn't yet exist — coordination with the `analyzer/setup_reader.py` GT3 update is required. Indexed cars (Ferrari/Acura) gate the heave/torsion decode on `index_range`; analogous gating for GT3 (no decode needed — GT3 N/mm values are direct) is absent, so the existing decode block needs an early-return when `car.suspension_arch is GT3_COIL_4WHEEL`.

### BLOCKER 6-10 — `car_model/auto_calibrate.py` pipeline is GTP-shaped end-to-end

The cascade is:

1. `_setup_key()` (lines 75-114) — fingerprint includes `front_heave_setting`, `rear_third_setting`, `front_torsion_od_mm`, `rear_spring_setting`, `front_heave_perch_mm`, `rear_third_perch_mm`, `rear_spring_perch_mm`. **For GT3 these are all 0.** Two GT3 IBTs differing only by `lf_spring_nmm` collapse to the same key.

2. `CalibrationPoint` dataclass (lines 124-205) — same fields. No coil corners, no bump rubber gaps. Adding them is straightforward but invasive: every call site that constructs a `CalibrationPoint` (lines 563-625) must pass the new values.

3. `extract_point_from_ibt()` (lines 496-625) — reads from the analyzer's `CurrentSetup` object. The setup reader does not yet expose GT3 coil rates or bump rubber gaps. This work depends on the analyzer audit.

4. `_UNIVERSAL_POOL` / `_FRONT_POOL` / `_REAR_POOL` (lines 1184-1265) — features like `inv_front_heave`, `inv_rear_third`, `inv_od4`, `torsion_turns`, `rear_torsion_turns` are GTP physics. Per `car_model/cars.py:3225` (BMW_M4_GT3) `heave_spring=None`, line 1182 `od4 = col("front_torsion_od_mm") ** 4 = 0`, and the std filter at line 1273 drops the feature. The model has no axis-specific feature inputs.

   GT3 needs:
   ```python
   _GT3_FRONT_POOL = [
       (col("front_pushrod_mm"), "front_pushrod"),
       (col("lf_spring_nmm"), "lf_spring"),
       (col("rf_spring_nmm"), "rf_spring"),
       (1.0 / np.maximum(col("lf_spring_nmm"), 1.0), "inv_lf_spring"),
       (1.0 / np.maximum(col("rf_spring_nmm"), 1.0), "inv_rf_spring"),
       (col("lf_bump_rubber_gap_mm"), "lf_bump_gap"),
       (col("rf_bump_rubber_gap_mm"), "rf_bump_gap"),
       (col("front_splitter_height_mm"), "splitter_h"),
       (col("front_camber_deg"), "front_camber"),
       (col("fuel_l"), "fuel"),
   ]
   ```
   Plus the symmetric rear pool. Pool selection should branch on `car.suspension_arch`:
   ```python
   if car_obj.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
       _FRONT_POOL = _GT3_FRONT_POOL
       _REAR_POOL = _GT3_REAR_POOL
       _UNIVERSAL_POOL = _GT3_FRONT_POOL + _GT3_REAR_POOL  # superset
   else:
       _FRONT_POOL = _filter_pool(_FRONT_AXIS_NAMES | _GLOBAL_NAMES)
       ...
   ```

5. `fit_models_from_points()` (lines 1326-1802) — fits `front_ride_height`, `rear_ride_height`, `torsion_bar_turns`, `torsion_bar_defl`, `heave_spring_defl_*`, `heave_slider_defl_*`, `third_spring_defl_*`, `third_slider_defl_*`, `front_shock_defl_*`, `rear_shock_defl_*`. Of these, GT3 only has the RH and shock-defl outputs; everything heave/third/torsion-related is N/A and should be **skipped early** based on `car_obj.suspension_arch`, not silently produce None models.

6. `apply_to_car()` (lines 2125-2647) — writes into GTP-shaped attributes. Because GT3 cars have `heave_spring=None` and `front_torsion_c=0.0`, the `try/except AttributeError` blocks silently swallow most of the writes. There is no GT3-shaped path that writes per-corner coil compliance back to the car.

The pipeline needs a top-level architecture branch:

```python
def fit_models_from_points(car: str, points: list[CalibrationPoint]) -> CarCalibrationModels:
    from car_model.cars import get_car
    car_obj = get_car(car)
    if car_obj.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL:
        return fit_gt3_models(car, points, car_obj)
    return fit_gtp_models(car, points, car_obj)  # existing logic
```

### BLOCKER vs DEGRADED triage rationale

- **BLOCKER** = the existing code path silently produces a wrong result OR rejects valid GT3 input. Any of these will leak into Step-1 production runs with bad data.
- **DEGRADED** = the GT3 path is non-functional but the failure mode is loud (CLI rejection) or the wrong-result is bounded (e.g. unknown track key, hint table fallback).

## Risk summary

The four target files are the **schema layer** under everything else: setup_reader → setup_writer → solver → garage_validator → JSON output → webapp all consume `FIELD_REGISTRY`, `CAR_FIELD_SPECS`, `GarageSetupState`, and `DirectRegression`. Each blocker compounds:

- Without registry GT3 entries (Blocker 1), the IBT-driven `pipeline/produce.py` cannot identify GT3 cars at all.
- With registry but without `CAR_FIELD_SPECS` GT3 entries (Blocker 2), `setup_writer` falls back to BMW GTP YAML paths and emits `Chassis.LeftFront.TorsionBarOD` for a car that has no torsion bar.
- Even with field specs, missing `GarageSetupState` fields (Blocker 4) mean every regression that DOES train on GT3 data is fitting against zero-valued features and produces garbage coefficients.
- Even with all of the above, `_UNIVERSAL_POOL` (Blocker 8) silently filters out every coil-spring feature because none exist in the pool definition.

The deepest dependency is BLOCKER 7 (`CalibrationPoint` schema): every other layer's GT3 fields ultimately must trace back to a stored value in `data/calibration/{gt3_car}/calibration_points.json`. Schema migration of existing JSON files (none exist for GT3 yet, so this is greenfield) needs a clean break.

A new `SuspensionArchitecture.GT3_COIL_4WHEEL` branch in `fit_models_from_points()` and `_pool_to_matrix()` is the cleanest split. The existing GTP code path stays untouched; the new GT3 path uses GT3-shaped pools and skips heave/third/torsion targets entirely.

**Coordination risk**: Blocker 4 and Blocker 7 both depend on the analyzer's setup reader exposing GT3 fields. The analyzer audit (worker 11 or similar) must land first OR jointly to avoid two-PR ping-pong.

## Effort estimate

| Finding | Effort |
|---|---|
| 1: Add GT3 entries to `_CAR_REGISTRY`, tighten substring fallback, add unit tests | M (4h) |
| 2: Three new `_GT3_*_SPECS` dicts, register in `CAR_FIELD_SPECS`, fix `_car_name()` | L (8h) |
| 3: ~25 new `FieldDefinition` entries; per-car YAML path overrides | L (8h) |
| 4: New `GarageSetupState` fields + GT3-aware `from_current_setup()` | M (4h) |
| 5: New `DirectRegression` extractors + tests for unknown-feature warning | M (3h) |
| 6: GT3 `_setup_key()` variant or unified key with coil corners | M (3h) |
| 7: `CalibrationPoint` schema additions + extract_point_from_ibt() updates | M (4h) |
| 8-10: GT3 pool, GT3 fit pipeline branch, skip-N/A-targets logic | L (12h) |
| 11-15: Track alias, CLI choices, detect_car_adapter, _car_name fixes | XS-S (1-2h each) |
| 16-23: Defaults + apply_to_car() GT3 paths + protocol hints | M (8h total) |
| 24-25: Validator allowlist + comments | XS (1h) |

Totals (engineer-days, conservative):

- Blocker tier (1-10): ~7-8 days for an engineer with familiarity.
- Degraded tier (11-23): ~3-4 days.
- Cosmetic (24-25): ~0.5 day.

Code volume estimate: ~600 LOC added (mostly per-car spec dicts), ~150 LOC modified (existing pools/regressions). Roughly 300 lines of new tests for parity with current GTP test coverage.

## Dependencies

This audit's BLOCKER tier depends on, or unblocks:

- **`analyzer/setup_reader.py`** — must expose GT3 coil rates and bump rubber gaps before BLOCKER 4/7 can land. Coordinate with the analyzer audit.
- **`output/setup_writer.py`** — GT3 PARAM_IDS dispatch table (per `docs/gt3_session_info_schema.md` next-steps section) — depends on BLOCKER 2 & 3.
- **`solver/heave_solver.py`** — already has a GT3 null-solution path (`HeaveSolution.null()` at line 117); audit was correct that Step 2 is a no-op for GT3.
- **`car_model/calibration_gate.py`** — needs GT3-aware logic that doesn't block on uncalibrated `damper_zeta` / `spring_rates_heave` for cars with no heave spring. The gate currently expects every car to have all 6 step subsystems.
- **`pipeline/produce.py`** — depends on BLOCKER 1 for `resolve_car_from_ibt()` to recognise GT3 IBTs.
- **`car_model/cars.py`** — already has the 3 GT3 stubs (lines 3196, 3325, 3450). New fields added in this audit (e.g. `bump_rubber_gap_mm` on a `GarageRanges` extension, or a `gt3_setup` sub-model on `CarModel`) will need corresponding `car_model/cars.py` plumbing.

Downstream consumers that will need testing once these blockers are fixed:

- `car_model/calibration_gate.py:provenance()` — must correctly classify GT3 cars on Step 2 (architecturally N/A, not "uncalibrated").
- `validation/run_validation.py` — fixture regeneration for the GT3 stubs once they have any IBT data.
- `tests/test_setup_regression.py` — needs a GT3 baseline `.sto` fixture once the writer dispatch table lands.
