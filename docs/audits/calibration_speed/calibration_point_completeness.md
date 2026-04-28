# CalibrationPoint Field Completeness Audit

**Date:** 2026-04-27
**Scope:** Per-car field-coverage of the IBT YAML → CurrentSetup → Observation → CalibrationPoint → `_setup_key` → solver → `.sto` chain.

This document is a static analysis of the **codebase as of `gt3-phase0-foundations`** (commit chain ending `79c1586`). It does not run the pipeline; it traces field flow by reading source.

## Sources analyzed

| File | Role |
|------|------|
| `analyzer/setup_reader.py` | `CurrentSetup` dataclass + `from_ibt` parser. Reads YAML at `CarSetup.*`. |
| `learner/observation.py` | `Observation.setup` dict + `build_observation`. Mostly a pass-through of CurrentSetup. |
| `car_model/auto_calibrate.py` | `CalibrationPoint` dataclass, `_setup_key()`, `extract_point_from_ibt()`. Persisted to `data/calibration/<car>/calibration_points.json`. |
| `output/setup_writer.py` | `_BMW_PARAM_IDS`, `_FERRARI_PARAM_IDS`, `_PORSCHE_PARAM_IDS`, `_CADILLAC_PARAM_IDS`, `_ACURA_PARAM_IDS` and `write_sto()`. |

## Legend

- **Y** — field is present in the indicated layer with a name that round-trips.
- **N** — field is absent from that layer.
- **Y\*** — present but only via `getattr(s, "...", default)` (silent zero on missing).
- **(suppressed)** — XML id in PARAM_IDS but value is `""` (intentionally not emitted).
- **(unmapped)** — neither read from IBT nor written to .sto for that car.

The `.sto` writer is checked against the per-car PARAM_IDS dict that actually fires. Helper key names (`lf_roll_spring`, `front_master_cyl`, etc.) are used in tables.

---

## 1. BMW M Hybrid V8 (Dallara)

### 1.1 Field-coverage matrix (BMW)

| # | Parameter (canonical) | IBT YAML path | CurrentSetup | Observation.setup | CalibrationPoint | `_setup_key` | `.sto` (XML id) | Notes |
|---|------|------|------|------|------|------|------|------|
| 1 | wing_angle_deg | `TiresAero.AeroSettings.RearWingAngle` | Y `wing_angle_deg` | Y `wing` | Y `wing_deg` | N | Y `…RearWingAngle` | not in setup_key by design |
| 2 | front_rh_at_speed_mm | `TiresAero.AeroCalculator.FrontRhAtSpeed` | Y | N | Y `front_rh_at_speed_mm` | N | Y (computed) | display value |
| 3 | rear_rh_at_speed_mm | `TiresAero.AeroCalculator.RearRhAtSpeed` | Y | N | Y | N | Y | display value |
| 4 | df_balance_pct | `TiresAero.AeroCalculator.DownforceBalance` | Y | N | Y `aero_df_balance_pct` | N | Y | display value |
| 5 | ld_ratio | `TiresAero.AeroCalculator.LD` | Y | N | Y `aero_ld_ratio` | N | Y | display value |
| 6 | static_front_rh_mm | avg(LF, RF).RideHeight | Y | Y `front_rh_static` | Y | N | Y (LF+RF) | step1 output |
| 7 | static_rear_rh_mm | avg(LR, RR).RideHeight | Y | Y `rear_rh_static` | Y | N | Y (LR+RR) | step1 output |
| 8 | front_pushrod_mm | `Chassis.Front.PushrodLengthOffset` | Y | Y | Y | Y | Y `…PushrodLengthOffset` | |
| 9 | rear_pushrod_mm | `Chassis.Rear.PushrodLengthOffset` | Y | Y | Y | Y | Y | |
| 10 | front_heave_nmm | `Chassis.Front.HeaveSpring` | Y | Y `front_heave_nmm` | Y `front_heave_setting` | Y | Y `…HeaveSpring` | |
| 11 | front_heave_perch_mm | `Chassis.Front.HeavePerchOffset` | Y | N (only via `setup_dict["front_heave_perch_mm"]` is **NOT** populated — see gap) | Y `front_heave_perch_mm` | Y | Y `…HeavePerchOffset` | **NOT in Observation.setup** |
| 12 | rear_third_nmm | `Chassis.Rear.ThirdSpring` | Y | Y `rear_third_nmm` | Y `rear_third_setting` | Y | Y `…ThirdSpring` | |
| 13 | rear_third_perch_mm | `Chassis.Rear.ThirdPerchOffset` | Y | N (gap) | Y | Y | Y | **NOT in Observation.setup** |
| 14 | front_torsion_od_mm | `Chassis.LeftFront.TorsionBarOD` | Y | Y `torsion_bar_od_mm` | Y | Y | Y `…TorsionBarOD` | |
| 15 | rear_spring_nmm | `Chassis.LeftRear.SpringRate` | Y | Y | Y `rear_spring_setting` | Y | Y `…SpringRate` | |
| 16 | rear_spring_perch_mm | `Chassis.LeftRear.SpringPerchOffset` | Y | N (gap) | Y | Y | Y | **NOT in Observation.setup** |
| 17 | front_arb_size | `Chassis.Front.ArbSize` | Y | Y | Y | Y | Y `…ArbSize` | string |
| 18 | front_arb_blade | `Chassis.Front.ArbBlades` | Y | Y | Y | Y | Y `…ArbBlades` | int |
| 19 | rear_arb_size | `Chassis.Rear.ArbSize` | Y | Y | Y | Y | Y | string |
| 20 | rear_arb_blade | `Chassis.Rear.ArbBlades` | Y | Y | Y | Y | Y | int |
| 21 | front_camber_deg | avg(LF, RF).Camber | Y | Y | Y | Y | Y (per-corner) | |
| 22 | rear_camber_deg | avg(LR, RR).Camber | Y | Y | Y | Y | Y (per-corner) | |
| 23 | front_toe_mm | `Chassis.Front.ToeIn` | Y | Y | N | N | Y `…Front_ToeIn` | **NOT in CalibrationPoint or _setup_key** |
| 24 | rear_toe_mm | avg(LR, RR).ToeIn | Y | Y | N | N | Y (per-corner) | **NOT in CalibrationPoint or _setup_key** |
| 25 | front_ls_comp | `Chassis.LeftFront.LsCompDamping` | Y | Y `dampers.lf.ls_comp` | N | N | Y | dampers not back-fed into calibration regressions |
| 26 | front_ls_rbd | `Chassis.LeftFront.LsRbdDamping` | Y | Y | N | N | Y | |
| 27 | front_hs_comp | `Chassis.LeftFront.HsCompDamping` | Y | Y | N | N | Y | |
| 28 | front_hs_rbd | `Chassis.LeftFront.HsRbdDamping` | Y | Y | N | N | Y | |
| 29 | front_hs_slope | `Chassis.LeftFront.HsCompDampSlope` | Y | Y | N | N | Y | |
| 30 | rear_ls_comp | `Chassis.LeftRear.LsCompDamping` | Y | Y `dampers.lr.ls_comp` | N | N | Y | |
| 31 | rear_ls_rbd / hs_comp / hs_rbd / hs_slope | `Chassis.LeftRear.*` | Y (×4) | Y (×4) | N | N | Y (×4) | |
| 32 | brake_bias_pct | `BrakesDriveUnit.BrakeSpec.BrakePressureBias` | Y | Y | N | N | Y | **NOT in CalibrationPoint** |
| 33 | brake_bias_target | `BrakesDriveUnit.BrakeSpec.BrakeBiasTarget` | Y | Y\* | N | N | Y | |
| 34 | brake_bias_migration | `…BrakeBiasMigration` | Y | Y\* | N | N | Y | |
| 35 | brake_bias_migration_gain | `…BiasMigrationGain` | Y | Y\* | N | N | Y | |
| 36 | front_master_cyl_mm | `…FrontMasterCyl` | Y | Y\* | N | N | Y `…FrontMasterCyl` | recently surfaced in engineering report; NOT yet in CalibrationPoint |
| 37 | rear_master_cyl_mm | `…RearMasterCyl` | Y | Y\* | N | N | Y | same |
| 38 | pad_compound | `…PadCompound` | Y | Y\* | N | N | Y `…PadCompound` | string |
| 39 | diff_preload_nm | `BrakesDriveUnit.RearDiffSpec.Preload` | Y | Y | N | N | Y | |
| 40 | diff_ramp_angles | `…CoastDriveRampAngles` | Y | Y | N | N | Y | string |
| 41 | diff_clutch_plates | `…ClutchFrictionPlates` | Y | Y\* | N | N | Y | |
| 42 | tc_gain | `BrakesDriveUnit.TractionControl.TractionControlGain` | Y | Y\* | N | N | Y | |
| 43 | tc_slip | `…TractionControlSlip` | Y | Y\* | N | N | Y | |
| 44 | fuel_l | `BrakesDriveUnit.Fuel.FuelLevel` | Y | Y | Y | Y | Y `…FuelLevel` | |
| 45 | fuel_low_warning_l | `…FuelLowWarning` | Y | Y\* | N | N | Y | |
| 46 | fuel_target_l | `…FuelTarget` | Y | Y\* | N | N | (unmapped for BMW) | Ferrari only |
| 47 | gear_stack | `BrakesDriveUnit.GearRatios.GearStack` | Y | Y\* | N | N | Y | string |
| 48 | speed_in_first_kph .. seventh | `…SpeedInFirst..Seventh` | Y (×7) | Y\* (×7) | N | N | Y (×7, computed) | |
| 49 | hybrid_rear_drive_enabled | `…HybridConfig.HybridRearDriveEnabled` | Y | Y\* | N | N | (unmapped for BMW) | mapped only on Ferrari |
| 50 | hybrid_rear_drive_corner_pct | `…HybridRearDriveCornerPct` | Y | Y\* | N | N | (unmapped for BMW) | mapped only on Ferrari |
| 51 | roof_light_color | `…Lighting.RoofIdLightColor` | Y | Y\* | N | N | Y | string |
| 52 | torsion_bar_turns | `Chassis.LeftFront.TorsionBarTurns` | Y | N | Y (computed display) | N | Y (computed) | display value |
| 53 | torsion_bar_defl_mm | `Chassis.LeftFront.TorsionBarDefl` | Y | N | Y | N | Y | display value |
| 54 | front/rear_shock_defl_static/max_mm | `Chassis.{LF,LR}.ShockDefl` | Y (×4) | N | Y (×4) | N | Y (×4) | display value |
| 55 | heave_spring_defl_static_mm | `Chassis.Front.HeaveSpringDefl` | Y | N | Y | N | Y (computed) | |
| 56 | heave_slider_defl_static_mm | `Chassis.Front.HeaveSliderDefl` | Y | N | Y | N | Y | |
| 57 | rear_spring_defl_static_mm / max | `Chassis.LeftRear.SpringDefl` | Y (×2) | N | Y (×2) | N | Y (×2) | |
| 58 | third_spring_defl_static / max | `Chassis.Rear.ThirdSpringDefl` | Y (×2) | N | Y (×2) | N | Y (×2) | |
| 59 | third_slider_defl_static / max | `Chassis.Rear.ThirdSliderDefl` | Y (×2) | N | Y (×2) | N | Y (×2) | |
| 60 | lf/rf/lr/rr corner_weight_n | `Chassis.{LF,RF,LR,RR}.CornerWeight` | Y (×4) | N | Y (×4) | N | Y (×4, computed) | |
| 61 | front_rh_settle_time_ms | telemetry-derived (not YAML) | N | (telemetry) | N | N | N | dynamic |
| 62 | rear_rh_settle_time_ms | telemetry | N | (telemetry) | N | N | N | dynamic |

### 1.2 Top BMW gaps (impact-ranked)

1. **`front_toe_mm` / `rear_toe_mm` are read and written but NOT in CalibrationPoint or `_setup_key`** — toe affects scrub radius, dynamic camber under load, and tyre temperature gradient. Two BMW IBTs that differ only in toe are currently treated as duplicates by the dedupe logic and one will be silently dropped.
2. **`brake_bias_pct` not in CalibrationPoint** — measured BB drift across a stint is one of the strongest signals for diff/coast-ramp calibration. The pipeline writes a recommended BB but no calibration regression can ever fit `f(brake_bias) → measured_brake_bias_pct` because BB is dropped at extract time.
3. **`{front,rear}_master_cyl_mm` recently added to engineering report but NOT to CalibrationPoint** — the W6 task description explicitly flags this. Master cylinder diameter changes peak brake pressure and can shift `lock_ratio_p95`. Without it in the regression pool, Ferrari's `front_braking_lock_ratio_p95` model will mis-attribute pressure-sensitivity to BB%.
4. **All damper clicks (LS/HS comp/rbd/slope) absent from CalibrationPoint** — dampers fully round-trip IBT → Observation → .sto, but never feed back into the regression layer. This is a deliberate design (Step 6 has its own zeta calibration in `models.front_ls_zeta` etc.) but it means damper-related observation deltas can't drive empirical correction of the deflection or RH models even when they should (e.g., LF rebound at 12 vs 0 changes p95 shock velocity which feeds damper_zeta).
5. **`diff_preload_nm`, `diff_ramp_angles`, `tc_gain`, `tc_slip` all absent from CalibrationPoint** — same pattern as dampers; Observation captures them, but the calibration-fit layer never sees them. This is acceptable for Step 1–3 calibration but blocks any future "diff coupling to lat-g balance" model.

---

## 2. Cadillac V-Series.R (Dallara, GTP no-data)

Cadillac shares the BMW IBT layout. Differences:

- ARB blades use indexed format `ArbBlades[0]` (vs BMW's plain `ArbBlades`) — handled in `_CADILLAC_PARAM_IDS` via `**_BMW_PARAM_IDS` plus 2 overrides.
- No calibration data on file (`models.json` is a stub, `calibration_points.json` has 1 fixture from 2026-03-17).

### 2.1 Field-coverage matrix (Cadillac)

Cadillac shares the BMW IBT layout. `setup_reader.from_ibt` has no Cadillac branch (falls through to the BMW shape). `extract_point_from_ibt` reads the same fields. `_CAR_PARAM_IDS["cadillac"]` is `_BMW_PARAM_IDS` plus 2 indexed-ARB overrides.

| # | Parameter (canonical) | IBT YAML path | CurrentSetup | Observation.setup | CalibrationPoint | `_setup_key` | `.sto` (XML id) | Notes |
|---|------|------|------|------|------|------|------|------|
| 1 | wing_angle_deg | `TiresAero.AeroSettings.RearWingAngle` | Y | Y `wing` | Y `wing_deg` | N | Y | |
| 2 | front_rh_at_speed_mm | `TiresAero.AeroCalculator.FrontRhAtSpeed` | Y | N | Y | N | Y | display |
| 3 | rear_rh_at_speed_mm | `TiresAero.AeroCalculator.RearRhAtSpeed` | Y | N | Y | N | Y | display |
| 4 | df_balance_pct | `TiresAero.AeroCalculator.DownforceBalance` | Y | N | Y `aero_df_balance_pct` | N | Y | display |
| 5 | ld_ratio | `TiresAero.AeroCalculator.LD` | Y | N | Y `aero_ld_ratio` | N | Y | display |
| 6 | static_front_rh_mm | avg(LF, RF).RideHeight | Y | Y | Y | N | Y (LF+RF) | |
| 7 | static_rear_rh_mm | avg(LR, RR).RideHeight | Y | Y | Y | N | Y (LR+RR) | |
| 8 | front_pushrod_mm | `Chassis.Front.PushrodLengthOffset` | Y | Y | Y | Y | Y | |
| 9 | rear_pushrod_mm | `Chassis.Rear.PushrodLengthOffset` | Y | Y | Y | Y | Y | |
| 10 | front_heave_nmm | `Chassis.Front.HeaveSpring` | Y | Y | Y | Y | Y | |
| 11 | front_heave_perch_mm | `Chassis.Front.HeavePerchOffset` | Y | N (gap) | Y | Y | Y | |
| 12 | rear_third_nmm | `Chassis.Rear.ThirdSpring` | Y | Y | Y | Y | Y | |
| 13 | rear_third_perch_mm | `Chassis.Rear.ThirdPerchOffset` | Y | N (gap) | Y | Y | Y | |
| 14 | front_torsion_od_mm | `Chassis.LeftFront.TorsionBarOD` | Y | Y | Y | Y | Y | |
| 15 | rear_spring_nmm | `Chassis.LeftRear.SpringRate` | Y | Y | Y | Y | Y | |
| 16 | rear_spring_perch_mm | `Chassis.LeftRear.SpringPerchOffset` | Y | N (gap) | Y | Y | Y | |
| 17 | front_arb_size | `Chassis.Front.ArbSize` | Y | Y | Y | Y | Y | string |
| 18 | front_arb_blade | `Chassis.Front.ArbBlades[0]` (indexed) | Y | Y | Y | Y | **Y `…ArbBlades[0]`** (NOT `…ArbBlades` like BMW) | **Cadillac delta vs BMW** |
| 19 | rear_arb_size | `Chassis.Rear.ArbSize` | Y | Y | Y | Y | Y | |
| 20 | rear_arb_blade | `Chassis.Rear.ArbBlades[0]` | Y | Y | Y | Y | **Y `…ArbBlades[0]`** | **Cadillac delta vs BMW** |
| 21 | front_camber_deg | avg(LF, RF).Camber | Y | Y | Y | Y | Y (per-corner) | |
| 22 | rear_camber_deg | avg(LR, RR).Camber | Y | Y | Y | Y | Y (per-corner) | |
| 23 | front_toe_mm | `Chassis.Front.ToeIn` | Y | Y | **N** | **N** | Y | gap |
| 24 | rear_toe_mm | avg(LR, RR).ToeIn | Y | Y | **N** | **N** | Y (per-corner) | gap |
| 25 | dampers (LF/RF/LR/RR × LS/HS comp/rbd/slope) | `Chassis.{LF,RF,LR,RR}.{Ls,Hs}{Comp,Rbd}Damping[+Slope]` | Y (×20) | Y (×20) | **N** | **N** | Y (×20) | gap |
| 26 | brake_bias_pct + target + migration + gain | `BrakesDriveUnit.BrakeSpec.…` | Y (×4) | Y\* (×4) | **N** | **N** | Y (×4) | gap |
| 27 | front_master_cyl_mm / rear | `BrakesDriveUnit.BrakeSpec.{Front,Rear}MasterCyl` | Y (×2) | Y\* (×2) | **N** | **N** | Y (×2) | gap (W6 audit) |
| 28 | pad_compound | `…PadCompound` | Y | Y\* | **N** | **N** | Y | gap |
| 29 | diff_preload_nm | `BrakesDriveUnit.RearDiffSpec.Preload` | Y | Y | **N** | **N** | Y | gap |
| 30 | diff_ramp_angles | `…CoastDriveRampAngles` | Y | Y | **N** | **N** | Y | gap |
| 31 | diff_clutch_plates | `…ClutchFrictionPlates` | Y | Y\* | **N** | **N** | Y | gap |
| 32 | tc_gain / tc_slip | `BrakesDriveUnit.TractionControl.{Gain,Slip}` | Y (×2) | Y\* (×2) | **N** | **N** | Y (×2) | gap |
| 33 | fuel_l + low_warning + target | `BrakesDriveUnit.Fuel.{Level,LowWarning,Target}` | Y (×3) | Y/Y\*/Y\* | Y `fuel_l` only | Y `fuel_l` only | Y (×2: level+low_warning) | low_warning + target gaps |
| 34 | gear_stack + speed_in_first..seventh | `BrakesDriveUnit.GearRatios.{…}` | Y (×8) | Y\* (×8) | **N** | **N** | Y (×8) | gap |
| 35 | hybrid_rear_drive_enabled / corner_pct | `BrakesDriveUnit.HybridConfig.…` | Y (×2) | Y\* (×2) | **N** | **N** | (unmapped — Cadillac inherits BMW which has no hybrid PARAM_IDS) | gap |
| 36 | roof_light_color | `…Lighting.RoofIdLightColor` | Y | Y\* | **N** | **N** | Y | |
| 37 | torsion_bar_turns / rear_torsion_bar_turns | `Chassis.{LF,LR}.TorsionBarTurns` | Y (×2) | N | Y (×2) | N | Y (×2 — Dallara formula at writer:917-925) | display |
| 38 | torsion_bar_defl / rear_torsion_bar_defl | `Chassis.{LF,LR}.TorsionBarDefl` | Y (×2) | N | Y (×2) | N | Y (×2) | display |
| 39 | front/rear_shock_defl_static/max_mm | `Chassis.{LF,LR}.ShockDefl` | Y (×4) | N | Y (×4) | N | Y (×4 computed) | display |
| 40 | heave_spring_defl_static / max | `Chassis.Front.HeaveSpringDefl` | Y (×2) | N | Y (×2) | N | Y (×2) | display |
| 41 | heave_slider_defl_static / max | `Chassis.Front.HeaveSliderDefl` | Y (×2) | N | Y (×2) | N | Y (×2) | display |
| 42 | rear_spring_defl_static / max | `Chassis.LeftRear.SpringDefl` | Y (×2) | N | Y (×2) | N | Y (×2) | display |
| 43 | third_spring_defl_static / max | `Chassis.Rear.ThirdSpringDefl` | Y (×2) | N | Y (×2) | N | Y (×2) | display |
| 44 | third_slider_defl_static / max | `Chassis.Rear.ThirdSliderDefl` | Y (×2) | N | Y (×2) | N | Y (×2) | display |
| 45 | corner_weight_n × 4 | `Chassis.{LF,RF,LR,RR}.CornerWeight` | Y (×4) | N | Y (×4) | N | Y (×4 computed) | display |

### 2.2 Top Cadillac gaps

1. **No real calibration data** — only one fixture. The `models.json` will produce `is_calibrated=False` for every regression. This isn't a CalibrationPoint completeness issue — it's a data-coverage issue.
2. **All BMW gaps inherited** — toe, brake_bias, master_cyl, dampers, diff, TC, all absent from CalibrationPoint.
3. **No Cadillac-specific PARAM_IDS audit ever happened** — every cell in `_CADILLAC_PARAM_IDS` except the 2 ARB overrides is a literal BMW XML id. If iRacing's Cadillac garage uses different IDs for, say, `RearMasterCyl` or `BrakeBiasMigration`, the .sto will silently encode under the wrong key. There is no integration test verifying the Cadillac .sto round-trips through iRacing.

---

## 3. Porsche 963 (Multimatic chassis, hybrid layout)

Porsche has the most divergent IBT structure:
- Front dampers under `Dampers.FrontHeave` + `Dampers.FrontRoll`.
- Rear dampers under `Dampers.LeftRear` / `Dampers.RightRear` (NOT `Chassis.LeftRear` like BMW).
- Rear 3rd dampers under `Dampers.Rear3Rd`.
- Rear roll motion is implicit in per-corner LR/RR shocks — `has_rear_roll_damper=False`.
- Diff goes under `BrakesDriveUnit.RearDiffSpec` (BMW path) but the XML id includes `DiffSpec` not `RearDiffSpec` for some fields per `_PORSCHE_PARAM_IDS:412-415`. **POTENTIAL BUG** flagged in §3.2.

### 3.1 Field-coverage matrix (Porsche)

| # | Parameter | IBT YAML | CurrentSetup | Observation | CalibrationPoint | `_setup_key` | .sto | Notes |
|---|----|----|----|----|----|----|----|----|
| 1 | wing_angle_deg | `TiresAero.AeroSettings.RearWingAngle` | Y | Y | Y `wing_deg` | N | Y | |
| 2 | df_balance_pct | `TiresAero.AeroCalculator.DownforceBalance` | Y | N | Y | N | Y | |
| 3 | ld_ratio | `TiresAero.AeroCalculator.LD` | Y | N | Y | N | Y | |
| 4 | front_rh_at_speed_mm / rear | `TiresAero.AeroCalculator.{Front,Rear}RhAtSpeed` | Y (×2) | N | Y (×2) | N | Y (×2) | |
| 5 | static_front_rh_mm / rear | `Chassis.{LF,RF,LR,RR}.RideHeight` | Y (×2 avg) | Y | Y | N | Y (×4) | |
| 6 | front_pushrod_mm / rear | `Chassis.{Front,Rear}.PushrodLengthOffset` | Y (×2) | Y (×2) | Y (×2) | Y (×2) | Y (×2) | |
| 7 | front_heave_nmm | `Chassis.Front.HeaveSpring` | Y | Y | Y `front_heave_setting` | Y | Y `…HeaveSpring` | |
| 8 | front_heave_perch_mm | `Chassis.Front.HeavePerchOffset` | Y | N (gap) | Y | Y | Y | gap |
| 9 | **front_roll_spring_nmm** | `Chassis.Front.RollSpring` | Y | Y | **N** | **N** | Y `…RollSpring` (LF/RF separate) | gap |
| 10 | **front_roll_perch_mm** | `Chassis.Front.RollPerchOffset` | Y | Y | **N** | **N** | Y `…RollPerchOffset` | gap |
| 11 | rear_third_nmm | `Chassis.Rear.HeaveSpring` (NOTE: Porsche rear is HeaveSpring, not ThirdSpring) | Y | Y | Y | Y | Y `…Rear.HeaveSpring` | |
| 12 | rear_third_perch_mm | `Chassis.Rear.HeavePerchOffset` | Y | N (gap) | Y | Y | Y `…HeavePerchOffset` | gap |
| 13 | rear_spring_nmm | `Chassis.LeftRear.SpringRate` | Y | Y | Y | Y | Y (LR+RR) | |
| 14 | rear_spring_perch_mm | `Chassis.LeftRear.SpringPerchOffset` | Y | N (gap) | Y | Y | Y | gap |
| 15 | front_arb_size | `Chassis.Front.ArbSetting` | Y | Y | Y | Y | Y `…ArbSize` | NOTE: Porsche stores under `ArbSetting` but writes to `ArbSize` — confirmed in `_PORSCHE_PARAM_IDS` |
| 16 | front_arb_blade | `Chassis.Front.ArbAdj` | Y | Y | Y | Y | Y `…ArbAdj` | |
| 17 | rear_arb_size / blade | `Chassis.Rear.{ArbSize,ArbAdj}` | Y (×2) | Y (×2) | Y (×2) | Y (×2) | Y (×2) | |
| 18 | front_camber / rear | avg corner Camber | Y (×2) | Y (×2) | Y (×2) | Y (×2) | Y (×4 per-corner) | |
| 19 | front_toe / rear_toe_mm | `Chassis.{Front,LeftRear,RightRear}.ToeIn` | Y (×2) | Y (×2) | **N** | **N** | Y (×3) | gap (same as BMW) |
| 20 | front_ls_comp..hs_slope | `Dampers.FrontHeave.{Ls,Hs}{Comp,Rbd}Damping` | Y (×4 — note no HS slope!) | Y | N | N | Y (4 channels) | front heave has NO HS slope by design |
| 21 | rear_ls_comp..hs_slope | `Dampers.{LeftRear,RightRear}.{Ls,Hs}…` | Y (LR + RR per-corner ×5 each) | Y (rolled to lr/rr) | N | N | Y (per-corner ×5) | |
| 22 | front_roll_ls / hs / hs_slope | `Dampers.FrontRoll.Ls/HsDamping` + `HsDampSlope` | Y (×3) | Y (`setup["roll_dampers"]`) | **N** | **N** | Y (×3) | gap |
| 23 | rear_roll_ls / hs | n/a (no rear roll damper on Porsche) | (parsed from `Dampers.RearRoll` if present, but iRacing doesn't expose) | (zero) | N | N | (suppressed via `has_rear_roll_damper=False`) | by design |
| 24 | rear_3rd_ls_comp / hs_comp / ls_rbd / hs_rbd | `Dampers.Rear3Rd.{…}Damping` | Y (×4) | Y (`setup["rear_3rd_dampers"]`) | **N** | **N** | Y (×4) | gap — same pattern as front roll |
| 25 | brake_bias_pct + target + migration + gain | `BrakesDriveUnit.BrakeSpec.*` | Y (×4) | Y\* (×4) | N | N | Y (×4) | gap |
| 26 | front_master_cyl_mm / rear | `BrakesDriveUnit.BrakeSpec.{Front,Rear}MasterCyl` | Y (×2) | Y\* (×2) | N | N | Y (×2) | gap |
| 27 | pad_compound | `…PadCompound` | Y | Y\* | N | N | Y | gap (string field) |
| 28 | diff_preload_nm | `BrakesDriveUnit.RearDiffSpec.Preload` (read) → writes to `…DiffSpec_DiffPreload` (potential mismatch) | Y | Y | N | N | Y `…DiffSpec_DiffPreload` | **POSSIBLE BUG** §3.2 |
| 29 | diff_ramp_angles | `…CoastDriveRampAngles` (read) → split to coast/drive in writer | Y (string) | Y | N | N | Y (×2 separate ramps) | Porsche-only special split logic in writer |
| 30 | diff_clutch_plates | `…ClutchFrictionPlates` (read) → `…DiffSpec_ClutchPlates` | Y | Y\* | N | N | Y `…DiffSpec_ClutchPlates` | **field-id mismatch** §3.2 |
| 31 | tc_gain / tc_slip | `BrakesDriveUnit.TractionControl.{Gain,Slip}` | Y (×2) | Y\* (×2) | N | N | Y (×2) | gap |
| 32 | fuel_l + low_warning + target | `BrakesDriveUnit.Fuel.{Level,LowWarning,Target}` | Y (×3) | Y/Y\*/Y\* | Y `fuel_l` only | Y `fuel_l` only | Y (only `fuel_level` + `fuel_low_warning`) | low_warning + target NOT in CalibrationPoint |
| 33 | gear_stack + speeds | `BrakesDriveUnit.GearRatios.…` | Y (×8) | Y\* (×8) | N | N | (unmapped — Porsche dict has no gear_stack) | **missing PARAM_ID** for Porsche |
| 34 | hybrid_rear_drive_* | `BrakesDriveUnit.HybridConfig.…` | Y (×2) | Y\* (×2) | N | N | (unmapped for Porsche) | mapped only Ferrari |
| 35 | roof_light_color | `BrakesDriveUnit.Lighting.RoofIdLightColor` | Y | Y\* | N | N | Y | |
| 36 | torsion_bar_turns / defl | (Porsche has no front torsion bar) | Y (zero) | N | Y (zero) | N | (suppressed via `lf_torsion_od=""`) | correctly suppressed |
| 37 | rear_torsion_bar_turns / defl | (n/a Porsche) | Y (zero) | N | Y (zero) | N | (suppressed) | correctly suppressed |
| 38 | front/rear_shock_defl_static/max_mm | `Chassis.{LF,LR}.ShockDefl` | Y (×4) | N | Y (×4) | N | Y (computed ×4) | |
| 39 | heave_spring_defl_static / max | `Chassis.Front.HeaveSpringDefl` | Y (×2) | N | Y (×2) | N | Y (×2) | |
| 40 | heave_slider_defl_static | `Chassis.Front.HeaveSliderDefl` | Y | N | Y | N | Y | |
| 41 | rear_spring_defl_static / max | `Chassis.LeftRear.SpringDefl` | Y (×2) | N | Y (×2) | N | Y (×2) | |
| 42 | third_spring_defl_static / max | `Chassis.Rear.HeaveSpringDefl` (Porsche) — fallback `…ThirdSpringDefl` | Y (×2) | N | Y (×2) | N | Y (×2) | |
| 43 | third_slider_defl_static | `Chassis.Rear.HeaveSliderDefl` | Y | N | Y | N | Y | |
| 44 | corner_weight_n × 4 | `Chassis.{LF,RF,LR,RR}.CornerWeight` | Y (×4) | N | Y (×4) | N | Y (×4 computed) | |

### 3.2 Top Porsche gaps

1. **`front_roll_spring_nmm` / `front_roll_perch_mm` are NOT in CalibrationPoint or `_setup_key`** — Porsche's front "corner spring" stiffness is the roll spring, but `_setup_key` only sees `front_torsion_od_mm` (which is zero for Porsche). Two Porsche IBTs that differ only in roll spring (e.g., 100 vs 220 N/mm) hash to the same `_setup_key` and one is silently dropped. **This is the single biggest impact gap on Porsche** because roll spring is the dominant corner-spring axis.
2. **DiffSpec XML id mismatch** — `_PORSCHE_PARAM_IDS` lines 412–415 emit `…DiffSpec_DiffPreload`, `…DiffSpec_CoastRampAngle`, `…DiffSpec_DriveRampAngle`, `…DiffSpec_ClutchPlates` (note: `DiffSpec`, not `RearDiffSpec`). The IBT YAML reads from `RearDiffSpec`. If iRacing's Porsche schema actually wants `RearDiffSpec` like BMW, every Porsche .sto is silently writing diff under the wrong section. Verify against a fresh Porsche .sto from iRacing garage.
3. **`gear_stack` PARAM_ID missing for Porsche** — `_PORSCHE_PARAM_IDS` includes `gear_stack` (line 417) but no `speed_in_first..seventh`. The `include_computed` block at writer:1218 calls `_w_num("speed_in_first", …)` which falls through to the `_comment(details, f"TODO: porsche speed_in_first not mapped")` branch (line 831). 7 TODO comments per Porsche .sto. Cosmetic, but pollutes XML.
4. **`rear_3rd_dampers` (4 channels) and `front_roll_dampers` (3 channels) NOT in CalibrationPoint or `_setup_key`** — same pattern as Acura roll dampers; they round-trip IBT → Observation → .sto but never inform calibration. Acceptable for current scope but blocks Step 6 zeta calibration on Porsche when 3rd-damper changes.
5. **Brake fields, fuel_target, hybrid fields, master_cyl all NOT in CalibrationPoint** — same pattern as BMW.

---

## 4. Ferrari 499P (indexed springs + torsion bars front+rear)

Ferrari is the most divergent of the GTP cars at the field level: heave springs, torsion bars (front AND rear), and ARB blades are all **indexed** integers in the IBT YAML. The `_FERRARI_PARAM_IDS` writes them as integer indices; the regression layer carries both `*_setting` (raw index) and `*_nmm` (decoded physics rate via `setup_registry.public_output_value`).

### 4.1 Field-coverage matrix (Ferrari)

| # | Parameter | IBT YAML | CurrentSetup | Observation | CalibrationPoint | `_setup_key` | .sto | Notes |
|---|----|----|----|----|----|----|----|----|
| 1 | wing_angle_deg | `TiresAero.AeroSettings.RearWingAngle` | Y | Y | Y | N | Y | |
| 2 | static_front_rh_mm / rear | `Chassis.{LF,RF,LR,RR}.RideHeight` | Y (×2) | Y (×2) | Y (×2) | N | Y (×4) | |
| 3 | front_pushrod_mm | `Chassis.Front.PushrodLengthDelta` (note: *Delta* not *Offset*) | Y | Y | Y | Y | Y `…PushrodLengthDelta` | |
| 4 | rear_pushrod_mm | `Chassis.Rear.PushrodLengthDelta` | Y | Y | Y | Y | Y | |
| 5 | front_heave_nmm | `Chassis.Front.HeaveSpring` (indexed int 0-18) | Y (decoded) + Y in `raw_indexed_fields["front_heave_index"]` | Y `front_heave_nmm` + Y `front_heave_index` | Y `front_heave_setting` (= raw index) | Y | Y (writes raw index, NOT N/mm) | indexed |
| 6 | rear_third_nmm | `Chassis.Rear.HeaveSpring` (Ferrari rear is heave, not third — indexed) | Y (decoded) + Y `raw_indexed_fields["rear_heave_index"]` | Y + Y `rear_heave_index` | Y `rear_third_setting` | Y | Y `…Rear.HeaveSpring` (raw index) | |
| 7 | front_torsion_od_mm | `Chassis.LeftFront.TorsionBarOD` (indexed) | Y + Y `raw_indexed_fields["front_torsion_bar_index"]` | Y + Y `front_torsion_bar_index` | Y | Y | Y `…TorsionBarOD` (raw index) | |
| 8 | rear_torsion_od_mm | `Chassis.LeftRear.TorsionBarOD` (indexed — Ferrari has REAR torsion too!) | Y `rear_torsion_od_mm` + falls back to `rear_spring_nmm` if not present | Y `rear_spring_nmm` + Y `rear_torsion_bar_index` | Y `rear_spring_setting` (decoded fallback) | Y | Y `…LeftRear.TorsionBarOD` | aliased awkwardly between rear_spring_nmm and rear_torsion_od_mm |
| 9 | torsion_bar_turns / rear_torsion_bar_turns | `Chassis.LeftFront/LeftRear.TorsionBarTurns` | Y (×2) | N | Y (×2) | N | Y (×2 — computed via `_tb_turns` formulae lines 907-916, 941-949) | display values |
| 10 | front_arb_size / blade | `Chassis.Front.{ArbSize, ArbBlades}` (indexed) | Y (×2) | Y (×2) | Y (×2) | Y (×2) | Y `ArbBlades[0]` indexed | |
| 11 | rear_arb_size / blade | `Chassis.Rear.{ArbSize, ArbBlades}` | Y (×2) | Y (×2) | Y (×2) | Y (×2) | Y `ArbBlades[0]` | |
| 12 | front_camber / rear / front_toe / rear_toe | as BMW | Y (×4) | Y (×4) | Y (camber ×2 only — toe missing) | Y (camber ×2) | Y (×4 per-corner) | toe gap as before |
| 13 | dampers (per-corner LF/RF/LR/RR ls/hs/comp/rbd/slope) | `Dampers.{LeftFront,RightFront,LeftRear,RightRear}Damper.…` | Y (×20 channels) | Y (×20) | N | N | Y (×20) | gap |
| 14 | brake_bias + target + migration + gain | `Systems.BrakeSpec.{BrakePressureBias,BrakeBiasTarget,BiasMigration,BiasMigrationGain}` | Y (×4) | Y\* (×4) | N | N | Y (×4) | gap |
| 15 | front_master_cyl / rear_master_cyl | `Systems.BrakeSpec.{Front,Rear}MasterCyl` | Y (×2) | Y\* (×2) | N | N | Y `…FrontMasterCyl` / `…RearMasterCyl` | gap (audited W6) |
| 16 | pad_compound | `Systems.BrakeSpec.PadCompound` | Y | Y\* | N | N | Y `…PadCompound` | gap |
| 17 | front_diff_preload_nm | `Systems.FrontDiffSpec.Preload` (Ferrari has FRONT diff) | Y | Y `front_diff_preload_nm` | N | N | Y `…FrontDiffSpec_Preload` | **only Ferrari has front diff**, gap |
| 18 | diff_preload_nm | `Systems.RearDiffSpec.Preload` | Y | Y | N | N | Y `…RearDiffSpec_Preload` | gap |
| 19 | diff_ramp_angles | `Systems.RearDiffSpec.CoastDriveRampOptions` (note *Options* not *Angles* on Ferrari) | Y (string) | Y | N | N | Y `…CoastDriveRampOptions` | string field |
| 20 | diff_clutch_plates | `…ClutchFrictionPlates` | Y | Y\* | N | N | Y | gap |
| 21 | tc_gain / tc_slip | `Systems.TractionControl.{Gain,Slip}` | Y (×2) | Y\* (×2) | N | N | Y (×2) | gap |
| 22 | fuel_l + low_warning + target | `Systems.Fuel.{Level,LowWarning,Target}` | Y (×3) | Y/Y\*/Y\* | Y `fuel_l` only | Y `fuel_l` only | Y (×3) | only `fuel_l` in CalibrationPoint |
| 23 | gear_stack + speed_in_first..seventh | `Systems.GearRatios.{GearStack,SpeedInFirst..Seventh}` | Y (×8) | Y\* (×8) | N | N | Y (×8) | gap |
| 24 | hybrid_rear_drive_enabled / corner_pct | `Systems.HybridConfig.{Enabled,CornerPct}` | Y (×2) | Y\* (×2) | N | N | Y (×2) | Ferrari is the only car with these mapped in PARAM_IDS |
| 25 | roof_light_color | `Systems.Lighting.RoofIdLightColor` | Y | Y\* | N | N | Y | |
| 26 | front/rear_shock_defl_static/max_mm | `Dampers.…Damper.ShockDefl` (uses `_parse_defl`) | Y (×4) | N | Y (×4) | N | Y (×4 computed) | |
| 27 | torsion_bar_defl / rear_torsion_bar_defl | `Chassis.{LF,LR}.TorsionBarDefl` | Y (×2) | N | Y (×2) | N | Y (×2 — Ferrari turns formula at writer:907-916, 941-949) | |
| 28 | heave_spring_defl_static / max | `Chassis.Front.HeaveSpringDefl` | Y (×2) | N | Y (×2) | N | Y (×2) | |
| 29 | heave_slider_defl_static | `Chassis.Front.HeaveSliderDefl` | Y | N | Y | N | Y | |
| 30 | rear_spring_defl_static / max | (Ferrari rear is torsion, no coil — these come from `Chassis.LR.SpringDefl` if present) | Y (×2) | N | Y (×2) | N | Y (×2) | likely zero |
| 31 | third_spring_defl_static / max | `Chassis.Rear.ThirdSpringDefl` (fallback `HeaveSpringDefl`) | Y (×2) | N | Y (×2) | N | Y (×2) | |
| 32 | third_slider_defl_static | `Chassis.Rear.ThirdSliderDefl` (fallback `HeaveSliderDefl`) | Y | N | Y | N | Y | |
| 33 | corner_weight × 4 | `Chassis.{LF,RF,LR,RR}.CornerWeight` | Y (×4) | N | Y (×4) | N | Y (×4 computed) | |

### 4.2 Top Ferrari gaps

1. **`front_diff_preload_nm` (Ferrari-only front diff) absent from CalibrationPoint** — Ferrari is the only GTP car with a front diff preload. Round-trips through CurrentSetup → Observation → .sto, but cannot drive any calibration regression. Pre-load asymmetry (front vs rear) is a known Ferrari handling quirk and the calibration layer is blind to it.
2. **`rear_torsion_od_mm` aliased awkwardly with `rear_spring_nmm`** — `setup_reader.py:512-515` overwrites `rear_spring_nmm = rear_torsion_od_mm` only when the Ferrari layout is detected AND `rear_spring_nmm == 0`. Any code that reads `rear_torsion_od_mm` directly on a non-Ferrari setup gets zero; any code that reads `rear_spring_nmm` on Ferrari gets a torsion bar OD index and may multiply by `rear_motion_ratio**2` thinking it's a spring rate. This isn't a CalibrationPoint completeness issue per se, but it's a structural soft-spot exposed by the audit.
3. **All damper clicks (20 channels) NOT in CalibrationPoint** — same pattern as BMW.
4. **Brake fields + master cylinders NOT in CalibrationPoint** — same pattern as BMW. Master cylinder W6 finding applies.
5. **`hybrid_rear_drive_enabled` / `corner_pct` round-trip but not in CalibrationPoint** — Ferrari's hybrid deployment can shift weight transfer and is a setup-affecting signal, but invisible to regression fitting.

---

## 5. Acura ARX-06 (ORECA chassis, heave+roll dampers)

Acura is unique:
- **Front and rear corner springs are torsion bars** (not coil springs) — the rear coil-spring perch fields are suppressed (lr_spring_perch="").
- **Rear toe is single value under `Chassis.Rear.ToeIn`** — not per-corner.
- **Front and rear dampers are heave+roll** — `Dampers.FrontHeave` + `Dampers.FrontRoll` + `Dampers.RearHeave` + `Dampers.RearRoll` (4 dampers, 2 channels each for roll, 5 each for heave including HS slope).
- **Diff is `Systems.RearDiffSpec.DiffRampAngles`** (not `CoastDriveRampAngles` like BMW).

### 5.1 Field-coverage matrix (Acura)

| # | Parameter | IBT YAML | CurrentSetup | Observation | CalibrationPoint | `_setup_key` | .sto | Notes |
|---|----|----|----|----|----|----|----|----|
| 1 | wing_angle_deg + aero calc fields | as BMW | Y | Y/N | Y | N | Y | |
| 2 | static_front_rh_mm / rear | as BMW | Y (×2) | Y (×2) | Y (×2) | N | Y (×4) | |
| 3 | front_pushrod_mm / rear | `Chassis.{Front,Rear}.PushrodLengthOffset` | Y (×2) | Y (×2) | Y (×2) | Y (×2) | Y (×2) | |
| 4 | front_heave_nmm | `Chassis.Front.HeaveSpring` (indexed for Acura) | Y | Y | Y | Y | Y | indexed similar to Ferrari |
| 5 | front_heave_perch_mm | `Chassis.Front.HeavePerchOffset` | Y | N (gap) | Y | Y | Y | gap |
| 6 | rear_third_nmm | `Chassis.Rear.ThirdSpring` (or `HeaveSpring` fallback) | Y | Y | Y | Y | Y `…ThirdSpring` | |
| 7 | rear_third_perch_mm | `Chassis.Rear.ThirdPerchOffset` | Y | N (gap) | Y | Y | Y | gap |
| 8 | front_torsion_od_mm | `Chassis.LeftFront.TorsionBarOD` | Y | Y | Y | Y | Y `…TorsionBarOD` | |
| 9 | rear_torsion_od_mm | `Chassis.LeftRear.TorsionBarOD` | Y `rear_torsion_od_mm` field | Y `rear_torsion_od_mm` (only if > 0) | Y `rear_spring_setting` (aliased same as Ferrari) | Y | Y `…LeftRear.TorsionBarOD` | aliased awkwardly |
| 10 | front_arb_size / blade | `Chassis.Front.{ArbSize,ArbBlades}` indexed | Y (×2) | Y (×2) | Y (×2) | Y (×2) | Y `ArbBlades[0]` | |
| 11 | rear_arb_size / blade | `Chassis.Rear.{ArbSize,ArbBlades}` indexed | Y (×2) | Y (×2) | Y (×2) | Y (×2) | Y `ArbBlades[0]` | |
| 12 | front_camber / rear | as BMW | Y (×2) | Y (×2) | Y (×2) | Y (×2) | Y (×4 per-corner) | |
| 13 | front_toe_mm | `Chassis.Front.ToeIn` | Y | Y | N | N | Y | gap (universal toe gap) |
| 14 | rear_toe_mm | `Chassis.Rear.ToeIn` (single value, not per-corner) | Y (single read at L417) | Y | N | N | Y `…Rear.ToeIn` (suppresses per-corner) | gap |
| 15 | front_ls_comp / ls_rbd / hs_comp / hs_rbd / hs_slope | `Dampers.FrontHeave.{Ls,Hs}{Comp,Rbd}Damping[+Slope]` | Y (×5) | Y (rolled to lf, rf=lf) | N | N | Y (×5 — RF suppressed) | gap |
| 16 | rear_ls_comp / ls_rbd / hs_comp / hs_rbd / hs_slope | `Dampers.RearHeave.…` | Y (×5) | Y (rolled to lr, rr=lr) | N | N | Y (×5 — RR suppressed) | gap |
| 17 | **front_roll_ls / front_roll_hs** | `Dampers.FrontRoll.{Ls,Hs}Damping` | Y (×2) | Y (`setup["roll_dampers"]["front"]`) | **N** | **N** | Y (×2 `front_roll_ls/hs`) | gap |
| 18 | front_roll_hs_slope | `Dampers.FrontRoll.HsDampSlope` (or `HsCompDampSlope`) | Y | Y `setup["roll_dampers"]["front"]["hs_slope"]` | **N** | **N** | (unmapped — `front_roll_hs_slope` not in `_ACURA_PARAM_IDS`) | **GAP — Acura has front roll HS slope but writer doesn't emit it** |
| 19 | rear_roll_ls / rear_roll_hs | `Dampers.RearRoll.{Ls,Hs}Damping` | Y (×2) | Y (`setup["roll_dampers"]["rear"]`) | **N** | **N** | Y (×2) | gap (Acura rear roll exists, unlike Porsche) |
| 20 | brake_bias_pct + target + migration + gain | `Systems.BrakeSpec.{…}` | Y (×4) | Y\* (×4) | N | N | Y (×4) | gap |
| 21 | front_master_cyl / rear | `Systems.BrakeSpec.{Front,Rear}MasterCyl` | Y (×2) | Y\* (×2) | N | N | Y (×2) | gap (W6 audit) |
| 22 | pad_compound | `Systems.BrakeSpec.PadCompound` | Y | Y\* | N | N | Y | gap |
| 23 | diff_preload_nm | `Systems.RearDiffSpec.Preload` | Y | Y | N | N | Y `…Preload` | gap |
| 24 | diff_ramp_angles | `Systems.RearDiffSpec.DiffRampAngles` (Acura-specific) | Y (string) | Y | N | N | Y `…DiffRampAngles` | gap |
| 25 | diff_clutch_plates | `…ClutchFrictionPlates` | Y | Y\* | N | N | Y | gap |
| 26 | tc_gain / tc_slip | `Systems.TractionControl.{Gain,Slip}` | Y (×2) | Y\* (×2) | N | N | Y (×2) | gap |
| 27 | fuel_l + low_warning + target | `Systems.Fuel.{Level,LowWarning,Target}` | Y (×3) | Y/Y\*/Y\* | Y `fuel_l` only | Y `fuel_l` only | Y (×3, although `fuel_target` PARAM_ID may not be in `_ACURA_PARAM_IDS`) | **POSSIBLE missing fuel_target id for Acura** |
| 28 | gear_stack + speed_in_first..seventh | `Systems.GearRatios.{…}` | Y (×8) | Y\* (×8) | N | N | (gear_stack mapped, speeds inherited from BMW which use `BrakesDriveUnit.GearRatios` — **WRONG for Acura**) | **GAP — speed_in_* XML ids inherited from BMW point at `BrakesDriveUnit.GearRatios.…` but Acura uses `Systems.GearRatios.…`** |
| 29 | hybrid_rear_drive_enabled / corner_pct | `Systems.HybridConfig.…` | Y (×2) | Y\* (×2) | N | N | (unmapped for Acura) | gap (Ferrari is the only mapped car) |
| 30 | roof_light_color | `Systems.Lighting.RoofIdLightColor` | Y | Y\* | N | N | Y | |
| 31 | torsion_bar_turns / rear_torsion_bar_turns | `Chassis.{LF,LR}.TorsionBarTurns` | Y (×2) | N | Y (×2) | N | Y (×2 — formula at writer:917-925, fallback for non-BMW) | **Acura uses BMW formula** — TBD if accurate |
| 32 | torsion_bar_defl / rear_torsion_bar_defl | `Chassis.{LF,LR}.TorsionBarDefl` | Y (×2) | N | Y (×2) | N | Y (×2) | |
| 33 | front/rear_shock_defl_static/max_mm | `Chassis.{LF,LR}.ShockDefl` (or rolled to FrontHeave/RearHeave?) | Y (×4) | N | Y (×4) | N | Y (×4 computed) | NOTE: Acura's per-corner shocks are SYNTHESIZED from heave±roll telemetry |
| 34 | heave_spring_defl_static / max + slider | `Chassis.Front.{HeaveSpringDefl, HeaveSliderDefl}` | Y (×3) | N | Y (×3) | N | Y (×3) | |
| 35 | rear_spring_defl_* (suppressed) | (Acura rear is torsion, perch suppressed) | Y from `Chassis.LR.SpringDefl` if present | N | Y (likely zero) | N | (suppressed) | |
| 36 | third_spring_defl_static/max + slider | `Chassis.Rear.{ThirdSpringDefl, ThirdSliderDefl}` | Y (×3) | N | Y (×3) | N | Y (×3) | |
| 37 | corner_weight × 4 | `Chassis.{LF,RF,LR,RR}.CornerWeight` | Y (×4) | N | Y (×4) | N | Y (×4 computed) | |

### 5.2 Top Acura gaps

1. **`front_roll_hs_slope` is parsed and stored in Observation but NOT in `_ACURA_PARAM_IDS`** — `setup_reader` reads `front_roll_damp.HsDampSlope` and `Observation.setup["roll_dampers"]["front"]["hs_slope"]` carries it, but `_ACURA_PARAM_IDS` only maps `front_roll_ls` and `front_roll_hs` (no `front_roll_hs_slope`). The writer's `if _has_front_roll and _roll_ls_f is not None:` block at line 1154-1158 **does** call `_w_num("front_roll_hs_slope", …)` — it falls through to the TODO comment branch. Acura .sto loses the front roll HS slope on every write.
2. **`speed_in_first..seventh` XML ids are inherited from BMW's `BrakesDriveUnit.GearRatios.SpeedInFirst..Seventh` but Acura's IBT YAML uses `Systems.GearRatios.SpeedInFirst..Seventh`** — `_ACURA_PARAM_IDS` overrides `gear_stack` to `…Systems_GearRatios_GearStack` (line 502) but leaves the speed entries inherited from `_BMW_PARAM_IDS` which point to `…BrakesDriveUnit_GearRatios_…`. Acura .sto writes 7 phantom XML ids that don't match the iRacing schema. **HIGH IMPACT — likely silently rejected by iRacing or written under wrong section.**
3. **Front + rear roll dampers (full ×4-channel set) NOT in CalibrationPoint or `_setup_key`** — Acura's heave+roll architecture means roll dampers are a primary tuning axis, not a secondary one. Two Acura IBTs differing only in `front_roll_hs` hash to the same `_setup_key` and one is dropped.
4. **Rear torsion bar (`rear_torsion_od_mm`) aliased with `rear_spring_setting`** — same Ferrari issue; the alias makes `rear_motion_ratio**2` conversions fragile.
5. **`fuel_target` and `hybrid_*` PARAM_IDS NOT mapped for Acura** — every Acura .sto emits 3 TODO comments instead of fields.
6. **All brakes/diff/TC/master_cyl gaps** — same as other cars.

---

## 6. BMW M4 GT3 EVO (GT3 paired-coil)

GT3 architecture: `SuspensionArchitecture.GT3_COIL_4WHEEL`. `car.suspension_arch.has_heave_third == False`, `car.heave_spring is None`. PARAM_IDS in `output/setup_writer.py:506+` (audited as `_BMW_M4_GT3_PARAM_IDS`).

GT3 unique fields:
- `BumpRubberGap` × 4 corners (`Chassis.{LF,RF,LR,RR}.BumpRubberGap`)
- `CenterFrontSplitterHeight` (single, `Chassis.Front.CenterFrontSplitterHeight`)
- 4 paired corner coils (LF==RF, LR==RR) — front via `Chassis.LeftFront.CoilSpring` (or similar) and rear via `Chassis.LeftRear.SpringRate`.
- TC label is indexed string `"n (TC)"` not plain integer.
- ABS label is indexed string `"n (ABS)"`.
- No heave spring, no third spring, no torsion bar.

### 6.1 Field-coverage matrix (BMW M4 GT3 EVO)

> Per CLAUDE.md, GT3 fields are layered onto the existing schema via W6.3 / W7.1 / W7.2 work. CurrentSetup reads them via the BMW path. The W6.3 work added 5 GT3 fields to Observation.setup. The W7.2 work added 5 GT3 fields to CalibrationPoint and `_setup_key`. The W4.1 work added the per-car PARAM_IDS dict.

| # | Parameter | IBT YAML | CurrentSetup | Observation | CalibrationPoint | `_setup_key` | .sto | Notes |
|---|----|----|----|----|----|----|----|----|
| 1 | wing_angle_deg | `TiresAero.AeroSettings.RearWingAngle` | Y | Y | Y | N | Y | |
| 2 | static_front_rh_mm | avg(LF, RF).RideHeight | Y | Y | Y | N | Y (LF+RF) | |
| 3 | static_rear_rh_mm | avg(LR, RR).RideHeight | Y | Y | Y | N | Y (LR+RR) | |
| 4 | front_pushrod_mm | `Chassis.Front.PushrodLengthOffset` | Y | Y | Y | Y | (suppressed for GT3 per W2.1) | GT3 has no pushrod offset adjustment |
| 5 | rear_pushrod_mm | `Chassis.Rear.PushrodLengthOffset` | Y | Y | Y | Y | (suppressed for GT3) | |
| 6 | **front_corner_spring_nmm** | `Chassis.LeftFront.SpringRate` (or `CoilSpring`) | (gap §6.2) | Y `front_corner_spring_nmm` (W6.3) | Y (W7.2) | Y new slot | Y (LF+RF paired, writer:1085-1103) | |
| 7 | **rear_corner_spring_nmm** | `Chassis.LeftRear.SpringRate` | Y `rear_spring_nmm` | Y `rear_corner_spring_nmm` (W6.3) | Y (W7.2) | Y | Y (LR+RR paired) | |
| 8 | **front_bump_rubber_gap_mm** | `Chassis.{LF,RF}.BumpRubberGap` (avg) | (gap §6.2 — not in dataclass) | Y (W6.3 default 15.0) | Y (W7.2) | Y | Y `…BumpRubberGap` × 4 | **HIGH IMPACT GAP §6.2** |
| 9 | rear_bump_rubber_gap_mm | `Chassis.{LR,RR}.BumpRubberGap` | (gap §6.2) | Y (W6.3 default 50.0) | Y (W7.2) | Y | Y | gap §6.2 |
| 10 | splitter_height_mm | `Chassis.Front.CenterFrontSplitterHeight` | (gap §6.2) | Y (W6.3 default 20.0) | Y (W7.2) | Y | Y `…CenterFrontSplitterHeight` (placeholder) | gap §6.2 |
| 11 | front_arb_size | `Chassis.Front.ArbSize` (string) | Y | Y | Y | Y | Y | |
| 12 | front_arb_blade | `Chassis.Front.{ArbBlades, FarbBlades}` (paired) | Y (read via `ArbBlades` fallback at L406) | Y | Y | Y | Y `…ArbBlades` / `…FarbBlades` | BMW M4 GT3 uses paired blades |
| 13 | rear_arb_size | `Chassis.Rear.ArbSize` | Y | Y | Y | Y | Y | |
| 14 | rear_arb_blade | `Chassis.Rear.{ArbBlades, RarbBlades}` | Y | Y | Y | Y | Y | |
| 15 | front_camber_deg | avg(LF, RF).Camber | Y | Y | Y | Y | Y (per-corner) | |
| 16 | rear_camber_deg | avg(LR, RR).Camber | Y | Y | Y | Y | Y (per-corner) | |
| 17 | front_toe_mm | `Chassis.Front.ToeIn` | Y | Y | **N** | **N** | Y `…Front_ToeIn` | gap |
| 18 | rear_toe_mm | per-wheel `Chassis.{LR,RR}.ToeIn` (BMW M4 GT3 is per-wheel) | Y (avg) | Y | **N** | **N** | Y per-wheel (averaged → asymmetry lost) | gap |
| 19 | front_ls_comp | per-axle (writer averages on .sto from per-corner reads) | Y (per-corner) | Y per-corner | **N** | **N** | Y per-axle (8 channels not 16) | gap; iRacing's GT3 garage is per-axle |
| 20 | front_ls_rbd | per-axle | Y | Y | **N** | **N** | Y per-axle | gap |
| 21 | front_hs_comp | per-axle | Y | Y | **N** | **N** | Y per-axle | gap |
| 22 | front_hs_rbd | per-axle | Y | Y | **N** | **N** | Y per-axle | gap |
| 23 | rear_ls_comp | per-axle | Y | Y | **N** | **N** | Y per-axle | gap |
| 24 | rear_ls_rbd | per-axle | Y | Y | **N** | **N** | Y per-axle | gap |
| 25 | rear_hs_comp | per-axle | Y | Y | **N** | **N** | Y per-axle | gap |
| 26 | rear_hs_rbd | per-axle | Y | Y | **N** | **N** | Y per-axle | gap |
| 27 | TC label | indexed string `"n (TC)"` | Y (parsed via `_parse_indexed_label`) | Y (int after parse) | Y `tc_slip` | N | Y (re-formats as `n (TC)`) | BMW M4 GT3 suffix |
| 28 | ABS label | indexed string `"n (ABS)"` | Y (parsed via `_parse_indexed_label`) | Y (int) | (gap) | N | Y (re-formats as `n (ABS)`) | gap — abs not in CalibrationPoint |
| 29 | brake_bias_pct | `BrakesDriveUnit.BrakeSpec.BrakePressureBias` | Y | Y | **N** | **N** | Y | gap |
| 30 | brake_bias_target | `…BrakeBiasTarget` | Y | Y\* | **N** | **N** | Y | gap |
| 31 | brake_bias_migration | `…BrakeBiasMigration` | Y | Y\* | **N** | **N** | Y | gap |
| 32 | brake_bias_migration_gain | `…BiasMigrationGain` | Y | Y\* | **N** | **N** | Y | gap |
| 33 | front_master_cyl_mm | `…FrontMasterCyl` | Y | Y\* | **N** | **N** | Y | gap (W6 audit) |
| 34 | rear_master_cyl_mm | `…RearMasterCyl` | Y | Y\* | **N** | **N** | Y | gap (W6) |
| 35 | pad_compound | `…PadCompound` | Y | Y\* | **N** | **N** | Y | gap |
| 36 | diff_preload_nm | `BrakesDriveUnit.RearDiffSpec.Preload` | Y | Y | **N** | **N** | Y | gap |
| 37 | diff_ramp_angles | `…CoastDriveRampAngles` | Y | Y | **N** | **N** | Y | gap |
| 38 | diff_clutch_plates | `…ClutchFrictionPlates` | Y | Y\* | **N** | **N** | Y | gap |
| 39 | tc_gain | `BrakesDriveUnit.TractionControl.TractionControlGain` | Y | Y\* | **N** | **N** | Y | gap |
| 40 | fuel_l | `Chassis.Rear.FuelLevel` (BMW M4 GT3 path) | Y | Y | Y | Y | Y | |
| 41 | fuel_low_warning_l + fuel_target_l | `…FuelLowWarning`/`…FuelTarget` | Y (×2) | Y\* (×2) | **N** | **N** | Y | gap |
| 42 | gear_stack + speed_in_first..seventh | `BrakesDriveUnit.GearRatios.{…}` | Y (×8) | Y\* (×8) | **N** | **N** | Y (×8) | gap |
| 43 | roof_light_color | `…Lighting.RoofIdLightColor` | Y | Y\* | **N** | **N** | Y | |
| 44 | corner_weight_n × 4 | `Chassis.{LF,RF,LR,RR}.CornerWeight` | Y (×4) | N | Y (×4) | N | Y (×4 computed) | display |
| 45 | torsion_bar_turns / defl, heave_spring_defl_*, shock_defl_*, third_spring_defl_* | (Suppressed — GT3 has no heave/torsion architecture; writer skips per W2.1, W4.1) | (zero/N) | N | N | N | (suppressed) | correctly suppressed |

### 6.2 Top BMW M4 GT3 gaps

1. **`bump_rubber_gap_front_mm`, `bump_rubber_gap_rear_mm`, `splitter_height_mm` are NOT defined as dataclass fields on `CurrentSetup`** — `learner/observation.py:build_observation` populates `front_bump_rubber_gap_mm` etc. via `getattr(s, "...", default)` which silently returns the default (15.0 / 50.0 / 20.0) on every GT3 IBT. The `CurrentSetup` dataclass needs explicit GT3 fields (or a `gt3_extras: dict` slot) so that real IBT values flow through to Observation and CalibrationPoint instead of constant defaults. **HIGH IMPACT — every BMW M4 GT3 calibration point currently has identical bump-rubber and splitter values regardless of what was actually loaded.**
2. **`front_corner_spring_nmm` reads via `lf.SpringRate` or `lf.CoilSpring`** — the IBT YAML field name varies; CLAUDE.md doesn't pin it. CurrentSetup currently reads `lf.TorsionBarOD` for front_torsion_od_mm and `lf.SpringRate` for rear_spring_nmm but does NOT read a paired front coil spring. Need to verify what the actual GT3 IBT YAML uses (the audit `output.md:294-365` should be ground truth — but wasn't readable here).
3. **TC label indexed string `"n (TC)"` parsing** — `setup_reader._parse_int` strips the suffix when reading, but on write the writer reformats `"n (TC)"`. Round-trip is asymmetric — the IBT-read int never carries the suffix info, and the .sto-write hardcodes `"(TC)"` for BMW. Acceptable for now.
4. **Pushrod offset suppression at writer** — pushrod is read into CurrentSetup and CalibrationPoint, but suppressed at write time. Two BMW M4 GT3 IBTs that differ in (zero-effect-on-GT3) pushrod will hash differently and be treated as distinct calibration points. Fine for now (no real GT3 pushrod is exposed by iRacing) but `_setup_key` could safely drop `front_pushrod_mm` / `rear_pushrod_mm` for GT3.
5. **All damper, brake, diff, TC, master_cyl, fuel gaps inherited from BMW** — same pattern.

---

## 7. Aston Martin Vantage GT3 EVO

Same `SuspensionArchitecture.GT3_COIL_4WHEEL` as BMW M4 GT3, but with these per-car deltas:
- TC label: `"n (TC SLIP)"` (not BMW's `"n (TC)"`)
- ARB encoding: paired blades (`ArbBlades` + `FarbBlades`) like BMW
- Aston-only fields: `EpasSetting`, `ThrottleResponse`, `EnduranceLights`, `NightLedStripColor` (all in `_ASTON_MARTIN_VANTAGE_GT3_PARAM_IDS`)
- Fuel section: `Chassis.Rear.FuelLevel` (BMW path)
- Rear toe: per-wheel (like BMW), not paired

### 7.1 Field-coverage matrix (Aston Vantage GT3 EVO)

| # | Parameter | IBT YAML | CurrentSetup | Observation | CalibrationPoint | `_setup_key` | .sto | Notes |
|---|----|----|----|----|----|----|----|----|
| 1 | wing_angle_deg | `TiresAero.AeroSettings.RearWingAngle` | Y | Y | Y | N | Y | |
| 2 | static_front_rh_mm / rear | as BMW | Y (×2) | Y (×2) | Y (×2) | N | Y (×4) | |
| 3 | front_pushrod_mm / rear | `Chassis.{Front,Rear}.PushrodLengthOffset` | Y (×2) | Y (×2) | Y (×2) | Y (×2) | (suppressed for GT3 per W2.1) | GT3 has no pushrod |
| 4 | front_corner_spring_nmm | `Chassis.LeftFront.SpringRate` (or `CoilSpring`) | (gap §6.2 — verify CurrentSetup field name) | Y (W6.3) | Y (W7.2) | Y new slot | Y (LF+RF paired) | |
| 5 | rear_corner_spring_nmm | `Chassis.LeftRear.SpringRate` | Y `rear_spring_nmm` | Y (W6.3) | Y (W7.2) | Y | Y (LR+RR paired) | |
| 6 | front_bump_rubber_gap_mm | `Chassis.{LF,RF}.BumpRubberGap` (avg) | (gap §6.2 — not in dataclass) | Y (W6.3 default 17.0) | Y (W7.2) | Y | Y | **GAP** silent default |
| 7 | rear_bump_rubber_gap_mm | `Chassis.{LR,RR}.BumpRubberGap` | (gap §6.2) | Y (W6.3 default 54.0) | Y (W7.2) | Y | Y | **GAP** |
| 8 | splitter_height_mm | `Chassis.Front.CenterFrontSplitterHeight` | (gap §6.2) | Y (W6.3 default 17.0) | Y (W7.2) | Y | Y | **GAP** |
| 9 | front_arb_size / blade | `Chassis.Front.{ArbBlades, FarbBlades}` (paired blades) | Y (×2) | Y (×2) | Y (×2) | Y (×2) | Y `…ArbBlades` / `…FarbBlades` | Aston uses paired blades like BMW M4 GT3 |
| 10 | rear_arb_size / blade | `Chassis.Rear.{ArbBlades, RarbBlades}` | Y (×2) | Y (×2) | Y (×2) | Y (×2) | Y | |
| 11 | front_camber / rear | as BMW | Y (×2) | Y (×2) | Y (×2) | Y (×2) | Y (per-corner) | |
| 12 | front_toe_mm | `Chassis.Front.ToeIn` | Y | Y | **N** | **N** | Y | gap |
| 13 | rear_toe_mm | per-wheel `Chassis.{LR,RR}.ToeIn` (Aston is per-wheel like BMW M4 GT3) | Y (avg of LR/RR) | Y | **N** | **N** | Y per-wheel (averaged → asymmetry lost) | gap + L/R asymmetry lost |
| 14 | front_ls_comp / ls_rbd / hs_comp / hs_rbd | per-axle (writer averages on .sto from per-corner reads) | Y (read per-corner) | Y per-corner | **N** | **N** | Y per-axle (8 channels not 16) | gap |
| 15 | rear_ls_comp / ls_rbd / hs_comp / hs_rbd | as front | Y per-corner | Y per-corner | **N** | **N** | Y per-axle | gap |
| 16 | TC label | indexed string `"n (TC SLIP)"` | Y (parsed via `_parse_indexed_label` W5.2) | Y (int after parse) | Y `tc_slip` | N | Y (re-formats as `n (TC SLIP)`) | Aston-specific suffix |
| 17 | brake_bias + target + migration + gain | `BrakesDriveUnit.BrakeSpec.…` | Y (×4) | Y\* (×4) | **N** | **N** | Y (×4) | gap |
| 18 | front_master_cyl / rear_master_cyl | `BrakesDriveUnit.BrakeSpec.{Front,Rear}MasterCyl` | Y (×2) | Y\* (×2) | **N** | **N** | Y (×2) | gap (W6) |
| 19 | pad_compound | `…PadCompound` | Y | Y\* | **N** | **N** | Y | gap |
| 20 | diff_preload_nm | `BrakesDriveUnit.RearDiffSpec.Preload` | Y | Y | **N** | **N** | Y | gap |
| 21 | diff_ramp_angles | `…CoastDriveRampAngles` | Y | Y | **N** | **N** | Y | gap |
| 22 | diff_clutch_plates | `…ClutchFrictionPlates` | Y | Y\* | **N** | **N** | Y | gap |
| 23 | tc_gain / tc_slip | `BrakesDriveUnit.TractionControl.{Gain,Slip}` | Y (×2) | Y\* (×2) | **N** | **N** | Y (×2) | gap |
| 24 | fuel_l | `Chassis.Rear.FuelLevel` (Aston shares the BMW M4 GT3 path, NOT the Porsche 992 GT3R `Chassis.FrontBrakesLights.FuelLevel`) | Y | Y | Y | Y | Y | |
| 25 | fuel_low_warning_l + fuel_target_l | `…FuelLowWarning` / `…FuelTarget` | Y (×2) | Y\* (×2) | **N** | **N** | Y (low_warning), maybe Y (target) | gap |
| 26 | gear_stack + speed_in_first..seventh | `BrakesDriveUnit.GearRatios.{…}` | Y (×8) | Y\* (×8) | **N** | **N** | Y (×8) | gap |
| 27 | **EpasSetting** | `Systems.…EpasSetting` (or similar) | **N** (not in CurrentSetup dataclass) | **N** | **N** | **N** | Y (write-only placeholder via `_ASTON_MARTIN_VANTAGE_GT3_PARAM_IDS`) | **GAP — write-only** |
| 28 | **ThrottleResponse** | (Aston-specific) | **N** | **N** | **N** | **N** | Y (placeholder) | **GAP — write-only** |
| 29 | **EnduranceLights** | (Aston-specific) | **N** | **N** | **N** | **N** | Y (placeholder) | **GAP — write-only** |
| 30 | **NightLedStripColor** | (Aston-specific) | **N** | **N** | **N** | **N** | Y (placeholder) | **GAP — write-only** |
| 31 | roof_light_color | `…Lighting.RoofIdLightColor` | Y | Y\* | **N** | **N** | Y | |
| 32 | corner_weight × 4 | `Chassis.{LF,RF,LR,RR}.CornerWeight` | Y (×4) | N | Y (×4) | N | Y (×4 computed) | display |
| 33 | static_rh × 4 | `Chassis.{LF,RF,LR,RR}.RideHeight` | Y (×4) | (see #2) | (see #2) | N | Y (×4) | display |
| 34 | torsion_bar_turns / heave_spring_defl_* | (Suppressed — GT3 has no heave/torsion architecture) | (zero/N) | N | N | N | (suppressed via GT3 writer gates) | correctly suppressed |

### 7.2 Top Aston Vantage GT3 gaps

1. **All BMW M4 GT3 gaps inherited**: bump_rubber/splitter not in CurrentSetup dataclass; every Aston calibration point has identical 17.0 / 54.0 default values regardless of IBT.
2. **`EpasSetting`, `ThrottleResponse`, `EnduranceLights`, `NightLedStripColor` are write-only** — the writer emits them with placeholder values, but `setup_reader.from_ibt` doesn't extract them. They never reach Observation/CalibrationPoint.
3. **Aston rear toe per-wheel** — IBT YAML stores per-wheel; CurrentSetup averages to `rear_toe_mm` (single field). The .sto writer emits per-wheel by re-using the averaged value. L/R asymmetry is silently lost on every Aston IBT round-trip.
4. **TC label suffix** — `"n (TC SLIP)"` vs `"n (TC)"` — handled correctly by the per-car indexed-label dispatcher per CLAUDE.md, but worth noting that `_parse_indexed_label` was W5.2.
5. **All BMW M4 GT3 framework gaps (master_cyl, dampers, brakes, diff, TC absent from CalibrationPoint)** apply identically.

---

## 8. Porsche 911 GT3 R (992)

Same `SuspensionArchitecture.GT3_COIL_4WHEEL` as BMW M4 GT3, but with per-car deltas:
- ARB: integer settings (`ArbSetting` 1–11, `RarbSetting` 1–11) — NOT paired blades
- TC label: `"n (TC-LAT)"`
- Rear toe paired (`Chassis.Rear.TotalToeIn`, avg of LR+RR) — NOT per-wheel
- Fuel under `Chassis.FrontBrakesLights.FuelLevel` (NOT `Chassis.Rear.FuelLevel`)
- Damper range `(0, 12)` not `(0, 20)`
- `ThrottleShapeSetting`, `DashDisplayPage` (Porsche 992 GT3R-only)
- LLTD measured target = 0.45 (RR adjustment)

### 8.1 Field-coverage matrix (Porsche 992 GT3R)

| # | Parameter | IBT YAML | CurrentSetup | Observation | CalibrationPoint | `_setup_key` | .sto | Notes |
|---|----|----|----|----|----|----|----|----|
| 1 | wing_angle_deg | `TiresAero.AeroSettings.RearWingAngle` | Y | Y | Y | N | Y | |
| 2 | static_front_rh_mm / rear | as BMW | Y (×2) | Y (×2) | Y (×2) | N | Y (×4) | |
| 3 | front_pushrod_mm / rear | as BMW | Y (×2) | Y (×2) | Y (×2) | Y (×2) | (suppressed for GT3 per W2.1) | GT3 has no pushrod |
| 4 | front_corner_spring_nmm | `Chassis.LeftFront.SpringRate` (or `CoilSpring`) | (gap §6.2) | Y (W6.3) | Y (W7.2) | Y new slot | Y (LF+RF paired) | |
| 5 | rear_corner_spring_nmm | `Chassis.LeftRear.SpringRate` | Y `rear_spring_nmm` | Y (W6.3) | Y (W7.2) | Y | Y (LR+RR paired) | |
| 6 | front_bump_rubber_gap_mm | `Chassis.{LF,RF}.BumpRubberGap` (avg) | (gap §6.2) | Y (W6.3 default 30.0) | Y (W7.2) | Y | Y | **GAP** silent default |
| 7 | rear_bump_rubber_gap_mm | `Chassis.{LR,RR}.BumpRubberGap` | (gap §6.2) | Y (W6.3 default 51.0) | Y (W7.2) | Y | Y | **GAP** |
| 8 | splitter_height_mm | `Chassis.Front.CenterFrontSplitterHeight` | (gap §6.2) | Y (W6.3 default ~30) | Y (W7.2) | Y | Y | **GAP** |
| 9 | front_arb_size / blade | `Chassis.Front.ArbSetting` (integer 1–11) | Y (read via `ArbSetting` fallback at L405) | Y | Y | Y | **Y `…ArbSetting`** (Porsche delta — int 1-11, NOT paired-blade) | Porsche uses integer settings |
| 10 | rear_arb_size / blade | `Chassis.Rear.RarbSetting` (integer 1–11) | Y (via `ArbAdj` fallback at L408) | Y | Y | Y | **Y `…RarbSetting`** | |
| 11 | front_camber / rear | as BMW | Y (×2) | Y (×2) | Y (×2) | Y (×2) | Y (per-corner) | |
| 12 | front_toe_mm | `Chassis.Front.ToeIn` | Y | Y | **N** | **N** | Y | gap |
| 13 | rear_toe_mm | **paired** `Chassis.Rear.TotalToeIn` (single value, NOT per-wheel like Aston/BMW M4 GT3) | Y (single read at L417) | Y | **N** | **N** | Y `…Rear.TotalToeIn` (single field) | Porsche delta |
| 14 | front_ls_comp / ls_rbd / hs_comp / hs_rbd | per-axle (range 0-12, not 0-20) | Y per-corner read, averaged on write | Y per-corner | **N** | **N** | Y per-axle range (0,12) | gap + Porsche range delta |
| 15 | rear_ls_comp / ls_rbd / hs_comp / hs_rbd | as front | Y per-corner | Y per-corner | **N** | **N** | Y per-axle | gap |
| 16 | TC label | indexed string `"n (TC-LAT)"` | Y (parsed via `_parse_indexed_label`) | Y | Y `tc_slip` | N | Y (re-formats `n (TC-LAT)`) | Porsche-specific suffix |
| 17 | brake_bias + target + migration + gain | `BrakesDriveUnit.BrakeSpec.…` | Y (×4) | Y\* (×4) | **N** | **N** | Y (×4) | gap |
| 18 | front_master_cyl / rear_master_cyl | `BrakesDriveUnit.BrakeSpec.{Front,Rear}MasterCyl` | Y (×2) | Y\* (×2) | **N** | **N** | Y (×2) | gap (W6) |
| 19 | pad_compound | `…PadCompound` | Y | Y\* | **N** | **N** | Y | gap |
| 20 | diff_preload_nm | `BrakesDriveUnit.RearDiffSpec.Preload` (read) | Y | Y | **N** | **N** | Y | gap |
| 21 | diff_ramp_angles | `…CoastDriveRampAngles` | Y | Y | **N** | **N** | Y | gap |
| 22 | diff_clutch_plates | `…ClutchFrictionPlates` | Y | Y\* | **N** | **N** | Y | gap |
| 23 | tc_gain | `BrakesDriveUnit.TractionControl.TractionControlGain` | Y | Y\* | **N** | **N** | Y | gap |
| 24 | tc_slip | `BrakesDriveUnit.TractionControl.TractionControlSlip` | Y | Y\* | Y `tc_slip` | N | Y (label `"n (TC-LAT)"`) | gap (only tc_slip lands in CalibrationPoint via mis-naming) |
| 25 | fuel_l | **`Chassis.FrontBrakesLights.FuelLevel`** (Porsche delta — NOT `Chassis.Rear.FuelLevel` like BMW M4 GT3 / Aston) | (gap §8.2 — reader doesn't probe this path) | N (silent zero on every Porsche 992 GT3R IBT) | N (silent zero) | N (silent zero) | Y `…FrontBrakesLights_FuelLevel` (writer mapped) | **HIGH IMPACT GAP §8.2** |
| 26 | fuel_low_warning_l + fuel_target_l | `Chassis.FrontBrakesLights.{FuelLowWarning,FuelTarget}` | (same gap §8.2) | N | N | N | Y (writer mapped) | gap |
| 27 | gear_stack + speed_in_first..seventh | `BrakesDriveUnit.GearRatios.{…}` | Y (×8) | Y\* (×8) | **N** | **N** | Y (×8) | gap |
| 28 | **ThrottleShapeSetting** | `Systems.InCarAdjustments.ThrottleShapeSetting` (audit correction in CLAUDE.md — initially thought to be under FrontBrakesLights) | **N** (not in CurrentSetup dataclass) | **N** | **N** | **N** | Y (write-only placeholder via `_PORSCHE_992_GT3R_PARAM_IDS`) | **GAP — write-only** |
| 29 | **DashDisplayPage** | (Porsche-specific) | **N** | **N** | **N** | **N** | Y (placeholder) | **GAP — write-only** |
| 30 | hybrid_rear_drive_* | (Porsche 992 GT3R has no hybrid system) | (zero/N) | N | N | N | (unmapped) | n/a |
| 31 | roof_light_color | `…Lighting.RoofIdLightColor` | Y | Y\* | **N** | **N** | Y | |
| 32 | corner_weight × 4 | `Chassis.{LF,RF,LR,RR}.CornerWeight` | Y (×4) | N | Y (×4) | N | Y (×4 computed) | display |
| 33 | static_rh × 4 | `Chassis.{LF,RF,LR,RR}.RideHeight` | Y (×4) | (see #2) | (see #2) | N | Y (×4) | display |
| 34 | torsion_bar_turns / heave_spring_defl_* | (Suppressed — GT3 has no heave/torsion architecture) | (zero/N) | N | N | N | (suppressed via GT3 writer gates) | correctly suppressed |
| 35 | LLTD calibration target | (configured at car_model layer, not setup) | n/a | n/a | n/a | n/a | n/a | `cars.py` Porsche 992 GT3R = 0.45 (RR adjustment) |

### 8.2 Top Porsche 992 GT3R gaps

1. **Fuel YAML path mismatch** — `setup_reader._parse_float(fuel.get("FuelLevel")) or _parse_float(rear.get("FuelLevel"))` reads from `Systems.Fuel.FuelLevel` or `BrakesDriveUnit.Fuel.FuelLevel` or `Chassis.Rear.FuelLevel`. **None of these match Porsche 992 GT3R's `Chassis.FrontBrakesLights.FuelLevel`.** Verify `setup_reader` has a Porsche 992 GT3R branch that reads the Front section, otherwise every Porsche 992 GT3R calibration point has `fuel_l = 0.0`. **HIGH IMPACT — calibration regressions need fuel_l for the `fuel_x_inv_third` feature.**
2. **All BMW M4 GT3 GT3-specific gaps inherited**: bump_rubber/splitter not in CurrentSetup dataclass — silent default values for every Porsche 992 GT3R IBT.
3. **Rear toe averaging asymmetry** — IBT stores `Chassis.Rear.TotalToeIn` (single value); CurrentSetup reads it correctly via the `_parse_float(rear.get("ToeIn"))` fallback in setup_reader.py:417, but the BMW-default per-wheel write path likely emits per-wheel ids that Porsche garage rejects. Verify `_PORSCHE_992_GT3R_PARAM_IDS` overrides `lr_toe`, `rr_toe` to empty and adds `rear_toe`.
4. **`ThrottleShapeSetting`, `DashDisplayPage` are write-only placeholders** — never extracted from IBT.
5. **LLTD measured target 0.45** — encoded in `cars.py` per W2.4 audit, not directly a CalibrationPoint completeness issue.
6. **All BMW framework gaps (master_cyl, dampers, brakes, TC absent from CalibrationPoint)** apply identically.

---

## 9. Cross-car summary of recommendations

| # | Recommendation | Affected cars | Layer to modify | Rough effort |
|---|----------------|---------------|-----------------|--------------|
| R1 | Add `front_toe_mm` / `rear_toe_mm` to `CalibrationPoint` and `_setup_key` | All 8 | `auto_calibrate.py` `CalibrationPoint` + `_setup_key` | XS |
| R2 | Add `front_master_cyl_mm` / `rear_master_cyl_mm` / `pad_compound` to CalibrationPoint | All 8 | `auto_calibrate.py` | XS |
| R3 | Add `brake_bias_pct` / `brake_bias_target` / `brake_bias_migration` / `brake_bias_migration_gain` to CalibrationPoint | All 8 | `auto_calibrate.py` | S |
| R4 | Add `front_roll_spring_nmm` / `front_roll_perch_mm` to CalibrationPoint and `_setup_key` (Porsche 963) | Porsche 963 | `auto_calibrate.py` (gated on `getattr(s, "front_roll_spring_nmm", 0) > 0`) | S |
| R5 | Add bump_rubber_gap_{front,rear}_mm / splitter_height_mm to **CurrentSetup dataclass** with explicit IBT YAML reads | 3 sampled GT3 cars (and 7 future) | `analyzer/setup_reader.py` `CurrentSetup` + `from_ibt` | M |
| R6 | Verify (and likely fix) Porsche 992 GT3R fuel YAML path — read from `Chassis.FrontBrakesLights.FuelLevel` | Porsche 992 GT3R | `analyzer/setup_reader.py` `from_ibt` | XS |
| R7 | Add `front_roll_hs_slope` to `_ACURA_PARAM_IDS` so Acura .sto emits the Acura-specific `Dampers_FrontRoll_HsDampSlope` field | Acura | `output/setup_writer.py` | XS |
| R8 | Override `speed_in_first_kph..speed_in_seventh_kph` PARAM_IDS in `_ACURA_PARAM_IDS` to point at `Systems.GearRatios.…` | Acura | `output/setup_writer.py` | XS |
| R9 | Add `front_diff_preload_nm` to CalibrationPoint (Ferrari front diff) | Ferrari | `auto_calibrate.py` | XS |
| R10 | Verify Porsche `RearDiffSpec` vs `DiffSpec` XML id discrepancy in `_PORSCHE_PARAM_IDS:412-415`. Either rename to `RearDiffSpec_*` or add an integration round-trip test to confirm iRacing accepts `DiffSpec_*` for Porsche | Porsche 963 | `output/setup_writer.py` | S |
| R11 | Add Porsche 963 `gear_stack` + `speed_in_first..seventh` PARAM_IDS so .sto stops emitting 7 TODO comments | Porsche 963 | `output/setup_writer.py` | XS |
| R12 | Decouple `rear_torsion_od_mm` from `rear_spring_nmm` aliasing in `setup_reader.from_ibt` — make them two distinct fields and let downstream code branch on suspension architecture | Ferrari, Acura | `analyzer/setup_reader.py` | M |
| R13 | Add `damper_clicks` (full per-corner LS/HS comp/rbd/slope) to CalibrationPoint and `_setup_key` for Step 6 zeta calibration feedback | All 8 | `auto_calibrate.py` | M |
| R14 | Add `front_diff_preload_nm` / `diff_ramp_angles` / `diff_clutch_plates` / `tc_gain` / `tc_slip` to CalibrationPoint | All 8 | `auto_calibrate.py` | S |
| R15 | Add Aston Vantage GT3 read-side support for `EpasSetting`, `ThrottleResponse`, `EnduranceLights`, `NightLedStripColor` so they round-trip rather than being write-only placeholders | Aston Vantage GT3 | `analyzer/setup_reader.py` + Observation pass-through | M |
| R16 | Add an integration test that round-trips an IBT through `from_ibt` → `extract_point_from_ibt` → asserting no field reaches CalibrationPoint at default-zero when the IBT YAML had a non-zero value | All 8 | `tests/` | M |

---

## Appendix A: How `_setup_key` deduplicates

`_setup_key()` (auto_calibrate.py:75-114) returns a tuple of 16 fields:
- `track`
- `front_heave_setting`, `rear_third_setting` (both rounded to 0.1)
- `front_heave_perch_mm`, `rear_third_perch_mm` (0.1)
- `front_torsion_od_mm` (0.001), `rear_spring_setting` (0.1), `rear_spring_perch_mm` (0.1)
- `front_pushrod_mm`, `rear_pushrod_mm` (0.1)
- `front_camber_deg`, `rear_camber_deg` (0.1)
- `fuel_l` (0)
- `front_arb_size` (string), `front_arb_blade` (int), `rear_arb_size` (string), `rear_arb_blade` (int)

Per W7.1 + W8.1, GT3 entries get 5 additional slots (front_corner_spring_nmm, rear_corner_spring_nmm, front_bump_rubber_gap_mm, rear_bump_rubber_gap_mm, splitter_height_mm) via `getattr` with default 0.0. GTP entries silently get 0.0 for those slots.

**Fields that are NOT in `_setup_key` but should/could be**:
- toe (front + rear)
- brake bias
- master cylinder
- damper clicks (×20)
- diff preload + ramps
- TC gain + slip
- (Porsche-only) front_roll_spring_nmm + roll perch + rear_3rd_dampers + roll_dampers
- (Acura-only) front_roll_hs_slope + rear roll dampers

The decision of what to include in `_setup_key` directly controls calibration-point dedupe. Two IBTs that hash to the same key → one is dropped at `_dedupe_points`. Adding fields makes IBTs more distinguishable; removing fields makes more IBTs fold into a single calibration point. R1, R3, R4, R13 all bear on this trade-off.
