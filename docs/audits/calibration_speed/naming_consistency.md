# Naming Consistency Audit — IBT YAML → Observation → CalibrationPoint → Solver → .sto

**Status:** Diagnostic. Generated 2026-04-27. No code modified by this audit.

**Scope:** Walks every settable / computed parameter through the full chain so divergences in
naming, semantics, and per-car branching can be reviewed in one place.

The seven hops audited:

| # | Layer | File / class |
|---|-------|---|
| 1 | IBT YAML key | `track_model.ibt_parser.IBTFile.session_info["CarSetup"]` |
| 2 | CurrentSetup field | `analyzer.setup_reader.CurrentSetup` (dataclass) |
| 3 | Observation.setup key | `learner.observation.build_observation` (dict) |
| 4 | CalibrationPoint field | `car_model.auto_calibrate.CalibrationPoint` |
| 5 | `_setup_key` slot index | `car_model.auto_calibrate._setup_key` (tuple) |
| 6 | Solver step output field | `solver.{rake,heave,corner_spring,arb,wheel_geometry,damper,supporting}_solver.*Solution` |
| 7 | setup_writer canonical key | `output.setup_writer._{BMW,FERRARI,PORSCHE,CADILLAC,ACURA}_PARAM_IDS` |
| 8 | .sto XML id | per-car PARAM_IDS values + setup_registry `CarFieldSpec.sto_param_id` |

**Cars covered:** `bmw` (M Hybrid V8), `ferrari` (499P), `porsche` (963), `cadillac` (V-Series.R),
`acura` (ARX-06).


## Section 1 — Master parameter table

Columns are abbreviated as:
- **Canonical** = `setup_registry.FieldDefinition.canonical_key`
- **YAML** = path under `CarSetup.…` in IBT session info (Pascal-case, dot-separated). `BMW = ` indicates the BMW path; per-car overrides shown inline.
- **CurrentSetup** = field on `CurrentSetup` dataclass
- **Obs.setup** = key in `Observation.setup` dict (`build_observation`)
- **CalibPoint** = field on `CalibrationPoint`
- **Slot** = index in `_setup_key()` tuple (0-based; only fields used for fingerprinting)
- **Step out** = `step{N}.<field>` on the relevant `*Solution`
- **Writer key** = key in `_<CAR>_PARAM_IDS`
- **.sto XML id** = `CarSetup_*` string (BMW shown; per-car overrides in §3)

Legend in **YAML** column: `=` means same as BMW for all cars; otherwise per-car forks
listed. Empty cells mean "not present at that layer".

### 1.1 — Aero (Step 1 inputs / outputs)

| Canonical | YAML | CurrentSetup | Obs.setup | CalibPoint | Slot | Step out | Writer key | .sto XML id (BMW) |
|---|---|---|---|---|---|---|---|---|
| `wing_angle_deg` | `TiresAero.AeroSettings.RearWingAngle` (=) | `wing_angle_deg` | `wing` | `wing_deg` | — | — (input) | `wing_angle` | `CarSetup_TiresAero_AeroSettings_RearWingAngle` |
| `front_rh_at_speed_mm` | `TiresAero.AeroCalculator.FrontRhAtSpeed` (=) | `front_rh_at_speed_mm` | — | `front_rh_at_speed_mm` | — | (display) | `front_rh_at_speed` | `CarSetup_TiresAero_AeroCalculator_FrontRhAtSpeed` |
| `rear_rh_at_speed_mm` | `TiresAero.AeroCalculator.RearRhAtSpeed` (=) | `rear_rh_at_speed_mm` | — | `rear_rh_at_speed_mm` | — | (display) | `rear_rh_at_speed` | `CarSetup_TiresAero_AeroCalculator_RearRhAtSpeed` |
| `df_balance_pct` | `TiresAero.AeroCalculator.DownforceBalance` (=) | `df_balance_pct` | — | `aero_df_balance_pct` | — | `step1.df_balance_pct` | `df_balance` | `CarSetup_TiresAero_AeroCalculator_DownforceBalance` |
| `ld_ratio` | `TiresAero.AeroCalculator.LD` (=) | `ld_ratio` | — | `aero_ld_ratio` | — | `step1.ld_ratio` | `ld_ratio` | `CarSetup_TiresAero_AeroCalculator_LD` |

### 1.2 — Ride heights & pushrods (Step 1 outputs)

| Canonical | YAML | CurrentSetup | Obs.setup | CalibPoint | Slot | Step out | Writer key | .sto XML id |
|---|---|---|---|---|---|---|---|---|
| `lf_ride_height_mm` | `Chassis.LeftFront.RideHeight` (=) | `static_front_rh_mm` (avg LF/RF) | `front_rh_static` | `static_front_rh_mm` | — | `step1.static_front_rh_mm` | `lf_ride_height` | `CarSetup_Chassis_LeftFront_RideHeight` |
| `rf_ride_height_mm` | `Chassis.RightFront.RideHeight` (=) | (rolled into avg) | (rolled into avg) | (rolled) | — | (rolled) | `rf_ride_height` | `CarSetup_Chassis_RightFront_RideHeight` |
| `lr_ride_height_mm` | `Chassis.LeftRear.RideHeight` (=) | `static_rear_rh_mm` (avg LR/RR) | `rear_rh_static` | `static_rear_rh_mm` | — | `step1.static_rear_rh_mm` | `lr_ride_height` | `CarSetup_Chassis_LeftRear_RideHeight` |
| `rr_ride_height_mm` | `Chassis.RightRear.RideHeight` (=) | (rolled) | (rolled) | (rolled) | — | (rolled) | `rr_ride_height` | `CarSetup_Chassis_RightRear_RideHeight` |
| `front_pushrod_offset_mm` | BMW/Porsche/Cadillac/Acura `Chassis.Front.PushrodLengthOffset`; Ferrari `Chassis.Front.PushrodLengthDelta` | `front_pushrod_mm` | `front_pushrod` | `front_pushrod_mm` | 7 | `step1.front_pushrod_offset_mm` | `front_pushrod_offset` | `CarSetup_Chassis_Front_PushrodLengthOffset` (Ferrari: `…PushrodLengthDelta`) |
| `rear_pushrod_offset_mm` | BMW/Porsche/Cadillac/Acura `Chassis.Rear.PushrodLengthOffset`; Ferrari `Chassis.Rear.PushrodLengthDelta` | `rear_pushrod_mm` | `rear_pushrod` | `rear_pushrod_mm` | 8 | `step1.rear_pushrod_offset_mm` | `rear_pushrod_offset` | `CarSetup_Chassis_Rear_PushrodLengthOffset` (Ferrari: `…PushrodLengthDelta`) |

### 1.3 — Heave / Third spring (Step 2 outputs)

| Canonical | YAML | CurrentSetup | Obs.setup | CalibPoint | Slot | Step out | Writer key | .sto XML id |
|---|---|---|---|---|---|---|---|---|
| `front_heave_spring_nmm` | `Chassis.Front.HeaveSpring` (= all cars) | `front_heave_nmm` | `front_heave_nmm` + `front_heave_index` (Ferrari only) | `front_heave_setting` | 1 | `step2.front_heave_nmm` | `front_heave_spring` | `CarSetup_Chassis_Front_HeaveSpring` |
| `front_heave_perch_mm` | `Chassis.Front.HeavePerchOffset` (=) | `front_heave_perch_mm` | (in dampers? no — implied) | `front_heave_perch_mm` | 3 | `step2.perch_offset_front_mm` | `front_heave_perch` | `CarSetup_Chassis_Front_HeavePerchOffset` |
| `rear_third_spring_nmm` | BMW `Chassis.Rear.ThirdSpring`; Ferrari/Porsche `Chassis.Rear.HeaveSpring`; Acura/Cadillac `Chassis.Rear.ThirdSpring` | `rear_third_nmm` | `rear_third_nmm` + `rear_heave_index` (Ferrari) | `rear_third_setting` | 2 | `step2.rear_third_nmm` | `rear_third_spring` | BMW/Cadillac: `CarSetup_Chassis_Rear_ThirdSpring`; Ferrari/Porsche: `CarSetup_Chassis_Rear_HeaveSpring` |
| `rear_third_perch_mm` | BMW/Cadillac/Acura `Chassis.Rear.ThirdPerchOffset`; Ferrari/Porsche `Chassis.Rear.HeavePerchOffset` | `rear_third_perch_mm` | (implied) | `rear_third_perch_mm` | 4 | `step2.perch_offset_rear_mm` | `rear_third_perch` | BMW/Cadillac/Acura: `…ThirdPerchOffset`; Ferrari/Porsche: `…HeavePerchOffset` |

### 1.4 — Corner springs (Step 3 outputs)

| Canonical | YAML | CurrentSetup | Obs.setup | CalibPoint | Slot | Step out | Writer key | .sto XML id |
|---|---|---|---|---|---|---|---|---|
| `front_torsion_od_mm` | BMW/Cadillac/Acura `Chassis.LeftFront.TorsionBarOD`; Ferrari same path but values are *indices* 0–18; Porsche has no front TB | `front_torsion_od_mm` | `torsion_bar_od_mm` + `front_torsion_bar_index` (Ferrari) | `front_torsion_od_mm` | 5 | `step3.front_torsion_od_mm` | `lf_torsion_od` (suppressed for Porsche) | `CarSetup_Chassis_LeftFront_TorsionBarOD` |
| `rear_spring_rate_nmm` (alias `rear_spring_nmm`) | BMW/Porsche `Chassis.LeftRear.SpringRate`; Ferrari `Chassis.LeftRear.TorsionBarOD`; Acura `Chassis.LeftRear.TorsionBarOD` | `rear_spring_nmm` (BMW/Porsche), `rear_torsion_od_mm` (Acura/Ferrari fallback) | `rear_spring_nmm` + `rear_torsion_bar_index` (Ferrari) + `rear_torsion_od_mm` (Acura) | `rear_spring_setting` | 6 | `step3.rear_spring_rate_nmm` | `lr_spring_rate` (Ferrari/Acura: TorsionBarOD; BMW/Porsche/Cadillac: SpringRate) | BMW/Cadillac/Porsche: `CarSetup_Chassis_LeftRear_SpringRate`; Ferrari/Acura: `CarSetup_Chassis_LeftRear_TorsionBarOD` |
| `rear_spring_perch_mm` | `Chassis.LeftRear.SpringPerchOffset` (BMW/Porsche/Cadillac); not present on Ferrari (torsion bar) or Acura (torsion bar) | `rear_spring_perch_mm` | (implied) | `rear_spring_perch_mm` | (no slot — `_setup_key` includes it but rounded to 0 for index cars) | `step3.rear_spring_perch_mm` | `lr_spring_perch` (suppressed `""` for Ferrari, Acura) | `CarSetup_Chassis_LeftRear_SpringPerchOffset` |
| `front_roll_spring_nmm` (Porsche/Acura) | `Chassis.Front.RollSpring` | `front_roll_spring_nmm` | `front_roll_spring_nmm` (conditional — only if > 0) | — (not in CalibPoint) | — | (not in step3 dataclass; carried via current_setup) | `lf_roll_spring` (Porsche only) | `CarSetup_Chassis_LeftFront_RollSpring` (Porsche), `CarSetup_Chassis_Front_RollSpring` (Acura) |
| `front_roll_perch_mm` (Porsche/Acura) | `Chassis.Front.RollPerchOffset` | `front_roll_perch_mm` | `front_roll_perch_mm` (conditional) | — | — | — | `front_roll_perch` (Porsche) | `CarSetup_Chassis_Front_RollPerchOffset` |
| `rear_torsion_od_mm` (Acura, Ferrari) | `Chassis.LeftRear.TorsionBarOD` | `rear_torsion_od_mm` (Acura), Ferrari aliases via `rear_spring_nmm` (see CONFUSING-1) | `rear_torsion_od_mm` (Acura conditional) | (carried in `rear_spring_setting`) | (carried in slot 6) | (carried in `step3.rear_spring_rate_nmm`) | `lr_spring_rate` (Acura override = TorsionBarOD) | `CarSetup_Chassis_LeftRear_TorsionBarOD` |
| `torsion_bar_turns` (computed) | `Chassis.LeftFront.TorsionBarTurns` | `torsion_bar_turns` | — | `torsion_bar_turns` | — | (display) | `lf_torsion_turns` | `CarSetup_Chassis_LeftFront_TorsionBarTurns` |
| `rear_torsion_bar_turns` (computed, Ferrari) | `Chassis.LeftRear.TorsionBarTurns` | `rear_torsion_bar_turns` | — | `rear_torsion_bar_turns` | — | (display) | `lr_torsion_turns` | `CarSetup_Chassis_LeftRear_TorsionBarTurns` |

### 1.5 — ARBs (Step 4 outputs)

| Canonical | YAML | CurrentSetup | Obs.setup | CalibPoint | Slot | Step out | Writer key | .sto XML id |
|---|---|---|---|---|---|---|---|---|
| `front_arb_size` | BMW/Cadillac/Acura/Ferrari `Chassis.Front.ArbSize`; Porsche `Chassis.Front.ArbSetting` | `front_arb_size` (string, Porsche reads `ArbSetting` here) | `front_arb_size` | `front_arb_size` | 12 | `step4.front_arb_size` | `front_arb_size` | BMW/Cadillac/Acura/Ferrari: `CarSetup_Chassis_Front_ArbSize`; Porsche: `CarSetup_Chassis_Front_ArbSetting` (Connected/Disconnected) |
| `front_arb_blade` | BMW `Chassis.Front.ArbBlades`; Cadillac/Acura `Chassis.Front.ArbBlades[0]`; Ferrari `Chassis.Front.ArbBlades[0]`; Porsche `Chassis.Front.ArbAdj` | `front_arb_blade` (also reads `ArbAdj` for Porsche) | `front_arb_blade` | `front_arb_blade` | 13 | `step4.front_arb_blade_start` | `front_arb_blades` | BMW: `…Front_ArbBlades`; Cadillac/Acura/Ferrari: `…Front_ArbBlades[0]`; Porsche: `…Front_ArbAdj` |
| `rear_arb_size` | `Chassis.Rear.ArbSize` (=) | `rear_arb_size` | `rear_arb_size` | `rear_arb_size` | 14 | `step4.rear_arb_size` | `rear_arb_size` | `CarSetup_Chassis_Rear_ArbSize` |
| `rear_arb_blade` | BMW `Chassis.Rear.ArbBlades`; Cadillac/Acura `Chassis.Rear.ArbBlades[0]`; Ferrari `Chassis.Rear.ArbBlades[0]`; Porsche `Chassis.Rear.ArbAdj` | `rear_arb_blade` | `rear_arb_blade` | `rear_arb_blade` | 15 | `step4.rear_arb_blade_start` | `rear_arb_blades` | BMW: `…Rear_ArbBlades`; Cadillac/Acura/Ferrari: `…Rear_ArbBlades[0]`; Porsche: `…Rear_ArbAdj` |
| `front_arb_setting` (Porsche/Acura) | `Chassis.Front.ArbSetting` | (folded into `front_arb_size`) | (folded) | — | — | — | (folded) | (covered above) |

### 1.6 — Wheel geometry (Step 5 outputs)

| Canonical | YAML | CurrentSetup | Obs.setup | CalibPoint | Slot | Step out | Writer key | .sto XML id |
|---|---|---|---|---|---|---|---|---|
| `front_camber_deg` | `Chassis.LeftFront.Camber` (=, plus avg LF/RF in CurrentSetup) | `front_camber_deg` | `front_camber_deg` | `front_camber_deg` | 9 | `step5.front_camber_deg` | `lf_camber` | `CarSetup_Chassis_LeftFront_Camber` |
| `rear_camber_deg` | `Chassis.LeftRear.Camber` (=) | `rear_camber_deg` | `rear_camber_deg` | `rear_camber_deg` | 10 | `step5.rear_camber_deg` | `lr_camber` | `CarSetup_Chassis_LeftRear_Camber` |
| `front_toe_mm` | `Chassis.Front.ToeIn` (=) | `front_toe_mm` | `front_toe_mm` | — | — | `step5.front_toe_mm` | `front_toe` | `CarSetup_Chassis_Front_ToeIn` |
| `rear_toe_mm` | BMW/Ferrari/Porsche/Cadillac per-corner `Chassis.LeftRear.ToeIn`+`Chassis.RightRear.ToeIn` (avg); Acura uses `Chassis.Rear.ToeIn` (single) | `rear_toe_mm` (avg, falls back to single) | `rear_toe_mm` | — | — | `step5.rear_toe_mm` | `lr_toe`/`rr_toe` (BMW), `rear_toe` (Acura) | `CarSetup_Chassis_LeftRear_ToeIn`+`…RightRear_ToeIn` (BMW); `CarSetup_Chassis_Rear_ToeIn` (Acura) |

### 1.7 — Dampers (Step 6 outputs)

Damper layout splits the field. **Layout A (BMW / Cadillac):** per-corner under
`Chassis.{LeftFront,RightFront,LeftRear,RightRear}.*`. **Layout B (Ferrari):** per-corner under
`Dampers.{LeftFrontDamper,RightFrontDamper,LeftRearDamper,RightRearDamper}.*`. **Layout C
(Acura ORECA):** heave+roll grouped under `Dampers.{FrontHeave,FrontRoll,RearHeave,RearRoll}`.
**Layout D (Porsche Multimatic):** hybrid — front heave+roll grouped, rear per-corner under
`Chassis.{LeftRear,RightRear}`, plus rear 3rd dampers under `Dampers.Rear3rd`.

| Canonical | YAML (per-layout) | CurrentSetup | Obs.setup (under `dampers`) | CalibPoint | Slot | Step out | Writer key | .sto XML id (BMW) |
|---|---|---|---|---|---|---|---|---|
| `front_ls_comp` | A: `Chassis.LeftFront.LsCompDamping`; B: `Dampers.LeftFrontDamper.LsCompDamping`; C: `Dampers.FrontHeave.LsCompDamping`; D: `Dampers.FrontHeave.LsCompDamping` | `front_ls_comp` | `lf.ls_comp` (and `rf.ls_comp` mirrored) | — | — | `step6.lf.ls_comp` (and `.rf.ls_comp`) | `lf_ls_comp` | `CarSetup_Chassis_LeftFront_LsCompDamping` |
| `front_ls_rbd` | A: `Chassis.LeftFront.LsRbdDamping`; B: `Dampers.LeftFrontDamper.LsRbdDamping`; C/D: `Dampers.FrontHeave.LsRbdDamping` | `front_ls_rbd` | `lf.ls_rbd` | — | — | `step6.lf.ls_rbd` | `lf_ls_rbd` | `CarSetup_Chassis_LeftFront_LsRbdDamping` |
| `front_hs_comp` | (analogous) | `front_hs_comp` | `lf.hs_comp` | — | — | `step6.lf.hs_comp` | `lf_hs_comp` | `CarSetup_Chassis_LeftFront_HsCompDamping` |
| `front_hs_rbd` | (analogous) | `front_hs_rbd` | `lf.hs_rbd` | — | — | `step6.lf.hs_rbd` | `lf_hs_rbd` | `CarSetup_Chassis_LeftFront_HsRbdDamping` |
| `front_hs_slope` | (analogous; Porsche has no `lf_hs_slope`) | `front_hs_slope` | `lf.hs_slope` | — | — | `step6.lf.hs_slope` | `lf_hs_slope` (suppressed `""` for Porsche) | `CarSetup_Chassis_LeftFront_HsCompDampSlope` |
| `rear_ls_comp` | A: `Chassis.LeftRear.LsCompDamping`; B: `Dampers.LeftRearDamper.LsCompDamping`; C: `Dampers.RearHeave.LsCompDamping`; D: `Chassis.LeftRear.LsCompDamping` | `rear_ls_comp` | `lr.ls_comp` | — | — | `step6.lr.ls_comp` | `lr_ls_comp` | `CarSetup_Chassis_LeftRear_LsCompDamping` |
| `rear_ls_rbd`, `rear_hs_comp`, `rear_hs_rbd`, `rear_hs_slope` | (analogous) | `rear_ls_rbd` etc | `lr.ls_rbd` etc | — | — | `step6.lr.ls_rbd` etc | `lr_ls_rbd` etc | (analogous) |
| `front_roll_ls` (Porsche/Acura) | `Dampers.FrontRoll.LsDamping` | `front_roll_ls` | `roll_dampers.front.ls` | — | — | (carries via current_setup or solver-set) | `front_roll_ls` | `CarSetup_Dampers_FrontRoll_LsDamping` |
| `front_roll_hs` | `Dampers.FrontRoll.HsDamping` | `front_roll_hs` | `roll_dampers.front.hs` | — | — | — | `front_roll_hs` | `CarSetup_Dampers_FrontRoll_HsDamping` |
| `front_roll_hs_slope` | `Dampers.FrontRoll.HsDampSlope` (also `HsCompDampSlope` accepted) | `front_roll_hs_slope` | `roll_dampers.front.hs_slope` | — | — | — | `front_roll_hs_slope` (Porsche/Acura) | `CarSetup_Dampers_FrontRoll_HsDampSlope` |
| `rear_roll_ls` (Acura only — Porsche has NO rear roll) | `Dampers.RearRoll.LsDamping` | `rear_roll_ls` | `roll_dampers.rear.ls` | — | — | — | `rear_roll_ls` | `CarSetup_Dampers_RearRoll_LsDamping` |
| `rear_roll_hs` (Acura only) | `Dampers.RearRoll.HsDamping` | `rear_roll_hs` | `roll_dampers.rear.hs` | — | — | — | `rear_roll_hs` | `CarSetup_Dampers_RearRoll_HsDamping` |
| `rear_3rd_ls_comp` (Porsche/Acura) | `Dampers.Rear3rd.LsCompDamping` (also accepted: `Dampers.Rear3Rd.…`, `Dampers.RearThird.…`) | `rear_3rd_ls_comp` | `rear_3rd_dampers.ls_comp` (conditional) | — | — | — | `rear_3rd_ls_comp` | `CarSetup_Dampers_Rear3rd_LsCompDamping` |
| `rear_3rd_hs_comp` | `Dampers.Rear3rd.HsCompDamping` | `rear_3rd_hs_comp` | `rear_3rd_dampers.hs_comp` | — | — | — | `rear_3rd_hs_comp` | `CarSetup_Dampers_Rear3rd_HsCompDamping` |
| `rear_3rd_ls_rbd` | (analogous) | `rear_3rd_ls_rbd` | `rear_3rd_dampers.ls_rbd` | — | — | — | `rear_3rd_ls_rbd` | `CarSetup_Dampers_Rear3rd_LsRbdDamping` |
| `rear_3rd_hs_rbd` | (analogous) | `rear_3rd_hs_rbd` | `rear_3rd_dampers.hs_rbd` | — | — | — | `rear_3rd_hs_rbd` | `CarSetup_Dampers_Rear3rd_HsRbdDamping` |

### 1.8 — Brakes (supporting)

| Canonical | YAML | CurrentSetup | Obs.setup | CalibPoint | Slot | Step out | Writer key | .sto XML id |
|---|---|---|---|---|---|---|---|---|
| `brake_bias_pct` | BMW/Porsche/Cadillac `BrakesDriveUnit.BrakeSpec.BrakePressureBias`; Ferrari/Acura `Systems.BrakeSpec.BrakePressureBias` | `brake_bias_pct` | `brake_bias_pct` | — | — | `supporting.brake_bias_pct` | `brake_bias` | BMW/Porsche/Cadillac: `CarSetup_BrakesDriveUnit_BrakeSpec_BrakePressureBias`; Ferrari/Acura: `CarSetup_Systems_BrakeSpec_BrakePressureBias` |
| `brake_bias_target` | (analogous BrakesDriveUnit / Systems split) | `brake_bias_target` | `brake_bias_target` | — | — | `supporting.brake_bias_target` | `brake_bias_target` | (analogous) |
| `brake_bias_migration` | BMW: `…BrakeBiasMigration`; Ferrari: `…BiasMigration`; Porsche: `…BrakeBiasMigration`; Acura: `…BrakeBiasMigration` | `brake_bias_migration` | `brake_bias_migration` | — | — | `supporting.brake_bias_migration` | `brake_bias_migration` | BMW: `…BrakeBiasMigration`; Ferrari: `…BiasMigration` |
| `brake_bias_migration_gain` | BMW: `…BiasMigrationGain`; Ferrari: `…BiasMigrationGain` | `brake_bias_migration_gain` | `brake_bias_migration_gain` | — | — | — | (only Ferrari writer key) | `CarSetup_Systems_BrakeSpec_BiasMigrationGain` |
| `front_master_cyl_mm` | (BrakesDriveUnit / Systems split) | `front_master_cyl_mm` | `front_master_cyl_mm` | — | — | `supporting.front_master_cyl_mm` | `front_master_cyl` | BMW: `CarSetup_BrakesDriveUnit_BrakeSpec_FrontMasterCyl` |
| `rear_master_cyl_mm` | (split) | `rear_master_cyl_mm` | `rear_master_cyl_mm` | — | — | `supporting.rear_master_cyl_mm` | `rear_master_cyl` | (analogous) |
| `pad_compound` | (split) | `pad_compound` | `pad_compound` | — | — | `supporting.pad_compound` | `pad_compound` | (analogous) |

### 1.9 — Differential / TC / Fuel (supporting)

| Canonical | YAML | CurrentSetup | Obs.setup | CalibPoint | Slot | Step out | Writer key | .sto XML id |
|---|---|---|---|---|---|---|---|---|
| `diff_preload_nm` | BMW/Cadillac/Acura: `BrakesDriveUnit.RearDiffSpec.Preload` (Acura: `Systems.RearDiffSpec.Preload`); Ferrari: `Systems.RearDiffSpec.Preload`; Porsche: `BrakesDriveUnit.RearDiffSpec.Preload` (read), but writer uses `BrakesDriveUnit.DiffSpec.DiffPreload` (see BROKEN-1) | `diff_preload_nm` | `diff_preload_nm` | — | — | `supporting.diff_preload_nm` | `diff_preload` | BMW: `CarSetup_BrakesDriveUnit_RearDiffSpec_Preload`; Ferrari/Acura: `CarSetup_Systems_RearDiffSpec_Preload`; Porsche WRITER: `CarSetup_BrakesDriveUnit_DiffSpec_DiffPreload` |
| `front_diff_preload_nm` (Ferrari only) | `Systems.FrontDiffSpec.Preload` | `front_diff_preload_nm` | `front_diff_preload_nm` | — | — | `supporting.front_diff_preload_nm` | `front_diff_preload` (Ferrari only) | `CarSetup_Systems_FrontDiffSpec_Preload` |
| `diff_ramp_angles` | BMW: `…CoastDriveRampAngles`; Ferrari: `…CoastDriveRampOptions`; Porsche: `…CoastDriveRampAngles`; Acura: `…DiffRampAngles` | `diff_ramp_angles` (reader accepts all 3 keys) | `diff_ramp_label` | — | — | `supporting.diff_ramp_angles` | `diff_coast_drive_ramp` | BMW: `…RearDiffSpec_CoastDriveRampAngles`; Ferrari: `…RearDiffSpec_CoastDriveRampOptions`; Porsche WRITER uses `diff_coast_ramp`+`diff_drive_ramp` SEPARATELY (see CONFUSING-2); Acura: `…RearDiffSpec_DiffRampAngles` |
| `diff_clutch_plates` | (BrakesDriveUnit / Systems split) | `diff_clutch_plates` | `diff_clutch_plates` | — | — | `supporting.diff_clutch_plates` | `diff_clutch_plates` | (analogous) |
| `tc_gain` | (split) | `tc_gain` | `tc_gain` | — | — | `supporting.tc_gain` | `tc_gain` | (analogous) |
| `tc_slip` | (split) | `tc_slip` | `tc_slip` | — | — | `supporting.tc_slip` | `tc_slip` | (analogous) |
| `fuel_l` | (BrakesDriveUnit.Fuel / Systems.Fuel split) | `fuel_l` | `fuel_l` | `fuel_l` | 11 | (input) | `fuel_level` | (analogous) |
| `fuel_low_warning_l` | (split) | `fuel_low_warning_l` | `fuel_low_warning_l` | — | — | — | `fuel_low_warning` | (analogous) |
| `fuel_target_l` | (split, Ferrari only on read) | `fuel_target_l` | `fuel_target_l` | — | — | — | `fuel_target` (Ferrari) | `CarSetup_Systems_Fuel_FuelTarget` |
| `gear_stack` | (split) | `gear_stack` | `gear_stack` | — | — | — | `gear_stack` | (analogous) |
| `speed_in_first_kph` … `speed_in_seventh_kph` | (split) | `speed_in_*_kph` | `speed_in_*_kph` | — | — | (display) | `speed_in_*` | (analogous) |
| `hybrid_rear_drive_enabled` | `BrakesDriveUnit.HybridConfig.HybridRearDriveEnabled` (BMW), `Systems.HybridConfig.HybridRearDriveEnabled` (Ferrari) | `hybrid_rear_drive_enabled` | `hybrid_rear_drive_enabled` | — | — | — | `hybrid_rear_drive_enabled` | (analogous) |
| `hybrid_rear_drive_corner_pct` | (analogous) | `hybrid_rear_drive_corner_pct` | `hybrid_rear_drive_corner_pct` | — | — | — | `hybrid_rear_drive_corner_pct` | (analogous) |
| `roof_light_color` | (split) | `roof_light_color` | `roof_light_color` | — | — | — | `roof_light_color` | (analogous) |

### 1.10 — Computed display values

These are read for calibration ground truth but are not solver outputs:

| Canonical | YAML | CurrentSetup | CalibPoint | Writer key | .sto XML id |
|---|---|---|---|---|---|
| `lf_corner_weight_n` | `Chassis.LeftFront.CornerWeight` | `lf_corner_weight_n` | `lf_corner_weight_n` | `lf_corner_weight` | `CarSetup_Chassis_LeftFront_CornerWeight` |
| `rf_corner_weight_n` | `Chassis.RightFront.CornerWeight` | `rf_corner_weight_n` | `rf_corner_weight_n` | `rf_corner_weight` | `CarSetup_Chassis_RightFront_CornerWeight` |
| `lr_corner_weight_n` | `Chassis.LeftRear.CornerWeight` | `lr_corner_weight_n` | `lr_corner_weight_n` | `lr_corner_weight` | `CarSetup_Chassis_LeftRear_CornerWeight` |
| `rr_corner_weight_n` | `Chassis.RightRear.CornerWeight` | `rr_corner_weight_n` | `rr_corner_weight_n` | `rr_corner_weight` | `CarSetup_Chassis_RightRear_CornerWeight` |
| `front_shock_defl_static_mm` | `Chassis.LeftFront.ShockDefl` (parsed `_parse_defl` → static, max) | `front_shock_defl_static_mm` | `front_shock_defl_static_mm` | `lf_shock_defl_static` | `CarSetup_Chassis_LeftFront_ShockDeflStatic` |
| `front_shock_defl_max_mm` | (same channel, [1]) | `front_shock_defl_max_mm` | `front_shock_defl_max_mm` | `lf_shock_defl_max` | `CarSetup_Chassis_LeftFront_ShockDeflMax` |
| `rear_shock_defl_static_mm` | `Chassis.LeftRear.ShockDefl` | `rear_shock_defl_static_mm` | `rear_shock_defl_static_mm` | `lr_shock_defl_static` | `CarSetup_Chassis_LeftRear_ShockDeflStatic` |
| `rear_shock_defl_max_mm` | (same channel) | `rear_shock_defl_max_mm` | `rear_shock_defl_max_mm` | `lr_shock_defl_max` | `CarSetup_Chassis_LeftRear_ShockDeflMax` |
| `heave_spring_defl_static_mm` | `Chassis.Front.HeaveSpringDefl` | `heave_spring_defl_static_mm` | `heave_spring_defl_static_mm` | `front_heave_spring_defl_static` | `CarSetup_Chassis_Front_HeaveSpringDeflStatic` |
| `heave_spring_defl_max_mm` | (same) | `heave_spring_defl_max_mm` | `heave_spring_defl_max_mm` | `front_heave_spring_defl_max` | `CarSetup_Chassis_Front_HeaveSpringDeflMax` |
| `heave_slider_defl_static_mm` | `Chassis.Front.HeaveSliderDefl` | `heave_slider_defl_static_mm` | `heave_slider_defl_static_mm` | `front_heave_slider_defl_static` | `CarSetup_Chassis_Front_HeaveSliderDeflStatic` |
| `heave_slider_defl_max_mm` | (same) | `heave_slider_defl_max_mm` | `heave_slider_defl_max_mm` | (BMW only) | `CarSetup_Chassis_Front_HeaveSliderDeflMax` |
| `rear_spring_defl_static_mm` | `Chassis.LeftRear.SpringDefl` (BMW/Porsche/Cadillac) | `rear_spring_defl_static_mm` | `rear_spring_defl_static_mm` | `lr_spring_defl_static` | `CarSetup_Chassis_LeftRear_SpringDeflStatic` |
| `rear_spring_defl_max_mm` | (same) | `rear_spring_defl_max_mm` | `rear_spring_defl_max_mm` | `lr_spring_defl_max` | `CarSetup_Chassis_LeftRear_SpringDeflMax` |
| `third_spring_defl_static_mm` | `Chassis.Rear.ThirdSpringDefl` (BMW/Acura/Cadillac); `Chassis.Rear.HeaveSpringDefl` (Ferrari/Porsche fallback) | `third_spring_defl_static_mm` | `third_spring_defl_static_mm` | `rear_third_spring_defl_static` | `CarSetup_Chassis_Rear_ThirdSpringDeflStatic` (BMW); `CarSetup_Chassis_Rear_HeaveSpringDeflStatic` (Ferrari) |
| `third_slider_defl_static_mm` | `Chassis.Rear.ThirdSliderDefl` (BMW); `Chassis.Rear.HeaveSliderDefl` (Ferrari fallback) | `third_slider_defl_static_mm` | `third_slider_defl_static_mm` | `rear_third_slider_defl_static` | `CarSetup_Chassis_Rear_ThirdSliderDeflStatic` |
| `torsion_bar_turns` | `Chassis.LeftFront.TorsionBarTurns` | `torsion_bar_turns` | `torsion_bar_turns` | `lf_torsion_turns` | `CarSetup_Chassis_LeftFront_TorsionBarTurns` |
| `torsion_bar_defl_mm` | `Chassis.LeftFront.TorsionBarDefl` | `torsion_bar_defl_mm` | `torsion_bar_defl_mm` | `lf_torsion_defl` | `CarSetup_Chassis_LeftFront_TorsionBarDefl` |
| `rear_torsion_bar_turns` (Ferrari) | `Chassis.LeftRear.TorsionBarTurns` | `rear_torsion_bar_turns` | `rear_torsion_bar_turns` | `lr_torsion_turns` | `CarSetup_Chassis_LeftRear_TorsionBarTurns` |
| `rear_torsion_bar_defl_mm` (Ferrari) | `Chassis.LeftRear.TorsionBarDefl` | `rear_torsion_bar_defl_mm` | `rear_torsion_bar_defl_mm` | (Ferrari) | `CarSetup_Chassis_LeftRear_TorsionBarDefl` |

### 1.11 — Tyre starting pressures (read-only at writer)

| Canonical | YAML | CurrentSetup | Obs.setup | Writer key | .sto XML id |
|---|---|---|---|---|---|
| `lf_pressure_kpa` (start) | `TiresAero.LeftFront.StartingPressure` | (not separately on CurrentSetup; comes from telemetry hot/cold) | — | `lf_pressure` | `CarSetup_TiresAero_LeftFront_StartingPressure` |
| `rf_pressure_kpa` | `TiresAero.RightFront.StartingPressure` | — | — | `rf_pressure` | `CarSetup_TiresAero_RightFront_StartingPressure` |
| `lr_pressure_kpa` | `TiresAero.LeftRearTire.StartingPressure` | — | — | `lr_pressure` | `CarSetup_TiresAero_LeftRearTire_StartingPressure` |
| `rr_pressure_kpa` | `TiresAero.RightRearTire.StartingPressure` | — | — | `rr_pressure` | `CarSetup_TiresAero_RightRearTire_StartingPressure` |
| `tyre_type` | `TiresAero.TireType.TireType` | — | — | `tyre_type` | `CarSetup_TiresAero_TireType_TireType` |

**Note:** the YAML keys are inconsistent between front (`LeftFront`, `RightFront`) and rear
(`LeftRearTire`, `RightRearTire`) — front omits the `Tire` suffix. Treated as part of the
iRacing schema, not under our control. See HARMLESS-3.


## Section 2 — Per-car CalibrationPoint slot index

`_setup_key()` produces a 16-tuple. Slot 0 is always `track`. The cells below show the rounding
or transformation applied:

| Slot | Field on `pt` | Round | Notes |
|---|---|---|---|
| 0 | `track` | str | Different tracks are separate calibration pools — pooling cross-track data caused 27x–103x LOO/train overfitting |
| 1 | `front_heave_setting` | 1 dp | N/mm (BMW/Porsche/Cadillac) OR index (Ferrari/Acura) — see CONFUSING-3 |
| 2 | `rear_third_setting` | 1 dp | N/mm or index |
| 3 | `front_heave_perch_mm` | 1 dp | always mm |
| 4 | `rear_third_perch_mm` | 1 dp | always mm |
| 5 | `front_torsion_od_mm` | 3 dp | physical OD mm (BMW/Porsche/Cadillac/Acura) OR index 0–18 (Ferrari) |
| 6 | `rear_spring_setting` | 1 dp | N/mm (BMW/Porsche/Cadillac) OR torsion-bar OD index (Ferrari/Acura) |
| 7 | `rear_spring_perch_mm` | 1 dp | only meaningful for coil-spring rear |
| 8 | `front_pushrod_mm` | 1 dp | mm (Ferrari path is `PushrodLengthDelta`) |
| 9 | `rear_pushrod_mm` | 1 dp | mm |
| 10 | `front_camber_deg` | 1 dp | degrees |
| 11 | `rear_camber_deg` | 1 dp | degrees |
| 12 | `fuel_l` | 0 dp | litres |
| 13 | `front_arb_size` | str | empty string when absent |
| 14 | `front_arb_blade` | int | |
| 15 | `rear_arb_size` | str | |
| 16 | `rear_arb_blade` | int | |

**Note:** The "Slot" column in §1 used 1-based indexing into the post-track tuple
(slot 1 = `front_heave_setting`). The numbering in this section uses 0-based indexing
into the full tuple (slot 0 = `track`). Section 1 is offset by +1 vs this table.

## Section 3 — Per-car YAML / writer divergences

The same canonical key takes a different YAML path or .sto XML id depending on car. Listed
in flat reference form:

| Canonical | BMW | Ferrari | Porsche | Cadillac | Acura |
|---|---|---|---|---|---|
| Pushrod path | `Chassis.{Front,Rear}.PushrodLengthOffset` | `…PushrodLengthDelta` | `…PushrodLengthOffset` | `…PushrodLengthOffset` | `…PushrodLengthOffset` |
| Rear third spring path | `Chassis.Rear.ThirdSpring` | `Chassis.Rear.HeaveSpring` | `Chassis.Rear.HeaveSpring` | `Chassis.Rear.ThirdSpring` | `Chassis.Rear.ThirdSpring` |
| Rear third perch path | `…Rear.ThirdPerchOffset` | `…Rear.HeavePerchOffset` | `…Rear.ThirdPerchOffset` (writer) / `…HeavePerchOffset` (reader fallback) | `…Rear.ThirdPerchOffset` | `…Rear.ThirdPerchOffset` |
| Rear spring rate | `Chassis.LeftRear.SpringRate` (coil) | `Chassis.LeftRear.TorsionBarOD` (rear is torsion bar) | `Chassis.LeftRear.SpringRate` | `Chassis.LeftRear.SpringRate` | `Chassis.LeftRear.TorsionBarOD` |
| Front roll spring | — | — | `Chassis.Front.RollSpring` | — | `Chassis.Front.RollSpring` |
| Front damper layout | per-corner under `Chassis.{LeftFront,RightFront}` | per-corner under `Dampers.{LeftFrontDamper,RightFrontDamper}` | grouped `Dampers.FrontHeave` + `Dampers.FrontRoll` | per-corner | grouped `Dampers.FrontHeave` + `Dampers.FrontRoll` |
| Rear damper layout | per-corner under `Chassis.{LeftRear,RightRear}` | per-corner under `Dampers.{LeftRearDamper,RightRearDamper}` | per-corner under `Chassis.{LeftRear,RightRear}` + `Dampers.Rear3rd` | per-corner | grouped `Dampers.RearHeave` + `Dampers.RearRoll` |
| ARB blade | `…ArbBlades` (un-indexed scalar) | `…ArbBlades[0]` (indexed array) | `…ArbAdj` | `…ArbBlades[0]` | `…ArbBlades[0]` |
| Front ARB selector | `…ArbSize` (string) | `…ArbSize` | `…ArbSetting` (Connected/Disconnected) | `…ArbSize` | `…ArbSize` (also has `ArbSetting`) |
| Brakes / TC / Diff / Fuel parent | `BrakesDriveUnit.*` | `Systems.*` | `BrakesDriveUnit.*` | `BrakesDriveUnit.*` | `Systems.*` |
| Diff ramp keyname | `RearDiffSpec.CoastDriveRampAngles` | `RearDiffSpec.CoastDriveRampOptions` | `RearDiffSpec.CoastDriveRampAngles` | `RearDiffSpec.CoastDriveRampAngles` | `RearDiffSpec.DiffRampAngles` |
| Diff preload (writer) | `…RearDiffSpec.Preload` | `…RearDiffSpec.Preload` | `…DiffSpec.DiffPreload` (note: `DiffSpec` not `RearDiffSpec`, `DiffPreload` not `Preload`) | `…RearDiffSpec.Preload` | `…RearDiffSpec.Preload` |
| Bias migration keyname | `…BrakeBiasMigration` + `…BiasMigrationGain` | `…BiasMigration` + `…BiasMigrationGain` | `…BrakeBiasMigration` | `…BrakeBiasMigration` | `…BrakeBiasMigration` |
| Rear toe shape | per-corner `LeftRear.ToeIn`/`RightRear.ToeIn` | per-corner | per-corner | per-corner | single `Chassis.Rear.ToeIn` |
| Rear roll damper | n/a | n/a | NOT PRESENT (`has_rear_roll_damper=False`) | n/a | `Dampers.RearRoll.{LsDamping,HsDamping}` |
| Rear 3rd damper | n/a | n/a | `Dampers.Rear3rd.*` (4 channels, no slope) | n/a | `Dampers.Rear3rd.*` (4 channels) |


## Section 4 — Divergence catalog

Each entry classifies the divergence and points to the file/line responsible.

### GOOD (semantic translation, intentional)

**GOOD-1 — Pascal→snake at every layer.** YAML uses `Chassis.Front.HeaveSpring`,
canonical/CurrentSetup/Observation/CalibrationPoint use `front_heave_spring_nmm` /
`front_heave_nmm` / `front_heave_setting`. Standard Python convention; no risk.

**GOOD-2 — Average per-corner → axle on read.** `_avg_f("RideHeight")` collapses
`LeftFront.RideHeight` + `RightFront.RideHeight` to a single `static_front_rh_mm` on
`CurrentSetup`. The setup writer expands back to per-corner `lf_ride_height` / `rf_ride_height`
on emit. Symmetric round-trip.

**GOOD-3 — Per-car YAML path overrides at the reader.** `setup_reader.py:299-321` accepts
multiple alternative paths in `_get_with_fallback` style (`systems.get(...) or brakes.get(...)`)
so the same `CurrentSetup.brake_bias_pct` field receives data from either the BMW
`BrakesDriveUnit.BrakeSpec.…` or Ferrari/Acura `Systems.BrakeSpec.…` path.

**GOOD-4 — Damper layout dispatch in reader.** Layout A/B/C/D dispatch in
`setup_reader.py:316-352` is structural: which YAML node the LF/RF/LR/RR damper data is read
from depends on the chassis, but the destination fields (`front_ls_comp` etc.) are uniform.

**GOOD-5 — Index-vs-N/mm dual storage on `CurrentSetup`.** `raw_indexed_fields` dict is
set for Ferrari (`front_heave_index`, `rear_heave_index`, `front_torsion_bar_index`,
`rear_torsion_bar_index`) and the underlying `front_heave_nmm` fields carry the same numeric
value (still in index space) so downstream readers can keep working.

**GOOD-6 — Display values flow through unchanged.** `lf_corner_weight_n`,
`heave_spring_defl_static_mm`, etc. are the same name at every layer. They are read for
calibration ground truth and emitted by writer for round-trip parity.

**GOOD-7 — Ferrari `PushrodLengthDelta` vs BMW `PushrodLengthOffset`.** Ferrari uses a
different YAML key for the same canonical field. The setup_reader handles it with
`front.get("PushrodLengthOffset") or front.get("PushrodLengthDelta")`; the setup_registry
and setup_writer per-car dicts override the path. Documented per-car.

### HARMLESS (different name, same data)

**HARMLESS-1 — `front_heave_spring_nmm` (registry) vs `front_heave_nmm` (CurrentSetup) vs
`front_heave_setting` (CalibrationPoint).** Three different field names referring to the same
spring rate — `front_heave_setting` is intentionally unit-agnostic on `CalibrationPoint`
because Ferrari/Acura store it as an index, not as N/mm. Documented in the field's docstring.
No data loss.

**HARMLESS-2 — Observation key renames.** `Observation.setup["wing"]` for `wing_angle_deg`,
`Observation.setup["front_rh_static"]` for `static_front_rh_mm`, `Observation.setup["torsion_bar_od_mm"]`
for `front_torsion_od_mm`, `Observation.setup["diff_ramp_label"]` for `diff_ramp_angles`. All
mappings are explicit in `learner.observation.build_observation` lines 162-213. The trim is
cosmetic shortening for store readability. Confirmed used consistently throughout
`learner.delta_detector` and `learner.empirical_models`.

**HARMLESS-3 — Tyre pressure node-name asymmetry: front uses `LeftFront`/`RightFront`, rear
uses `LeftRearTire`/`RightRearTire`.** Inherited from the iRacing schema, not from us. No
fix possible without breaking IBT compatibility.

**HARMLESS-4 — Writer key `lf_torsion_od` carries Ferrari's index 0–18 (not OD mm).** The
caller (`setup_writer.write_sto`) converts via `setup_registry.public_output_value` before
emit so the .sto file gets the right value. Internally inconsistent name (`_od` suffix
suggests millimetres), but the conversion does the right thing.

**HARMLESS-5 — `front_heave_nmm` carries an index for Ferrari.** Same pattern as above:
`CurrentSetup.front_heave_nmm = 4.0` (where 4 is an index, not 4 N/mm) for Ferrari, with
`raw_indexed_fields["front_heave_index"] = 4` providing the canonical reading. Confusing
to a fresh reader but the warnings list (`decode_warnings`) and the dual storage make it
explicit.

**HARMLESS-6 — Ferrari damper range 0–40 vs BMW 0–11.** Different per-car ranges in
`setup_registry` are correctly per-car, no cross-contamination. Same canonical key
`front_ls_comp` etc. in both.

**HARMLESS-7 — Porsche front heave damper has no `HsCompDampSlope`.** `setup_writer`
suppresses by mapping `lf_hs_slope` to `""` for Porsche. The CurrentSetup `front_hs_slope`
field stays 0 for Porsche; no missing data because the channel doesn't exist on the chassis.

**HARMLESS-8 — Porsche rear roll damper absent (`has_rear_roll_damper=False`).** The reader
falls back to 0; the writer's PORSCHE_PARAM_IDS still has `rear_roll_ls`/`rear_roll_hs` mapped
to `CarSetup_Dampers_RearRoll_*` because they EXIST in the `_PORSCHE_PARAM_IDS` dict but the
writer's gate at `setup_writer.py:1069` (per CLAUDE.md) skips writing them. Defensive.
Verify the per-axle gate is still firing — if `_PORSCHE_PARAM_IDS["rear_roll_ls"]` is set
but the gate fails, phantom XML IDs would be written.

**HARMLESS-9 — Acura rear toe is single (`Chassis.Rear.ToeIn`) but BMW splits per-corner
`LeftRear`+`RightRear`.** Reader handles both (avg of pair OR single). Writer suppresses
`lr_toe`/`rr_toe` for Acura (`""`) and adds new `rear_toe` key. No collision.

**HARMLESS-10 — Cadillac inherits BMW writer dict via `**_BMW_PARAM_IDS`.** Only the ARB
blade format differs (indexed `[0]`). Listed cleanly as override in
`_CADILLAC_PARAM_IDS:427-432`.

### CONFUSING (different name suggests different semantics)

**CONFUSING-1 — `rear_third_nmm` for Ferrari/Porsche carries REAR-HEAVE-SPRING data, not
rear THIRD spring.** Ferrari and Porsche both store the rear central spring under
`Chassis.Rear.HeaveSpring`. Our internal name has been `rear_third_*` since the BMW model
was authored (BMW genuinely has a separate "ThirdSpring" element on the rear). This
propagates: `CurrentSetup.rear_third_nmm`, `Observation.setup["rear_third_nmm"]`,
`CalibrationPoint.rear_third_setting`, `step2.rear_third_nmm`, writer
`rear_third_spring`, .sto `Chassis_Rear_HeaveSpring` (Ferrari/Porsche) / `Chassis_Rear_ThirdSpring` (BMW).
The XML round-trip is correct but the canonical name is misleading on Ferrari/Porsche. This
is the single biggest readability tax.

**Recommendation:** see §5 / R-1.

**CONFUSING-2 — Porsche writer splits `diff_ramp_angles` into `diff_coast_ramp` and
`diff_drive_ramp`.** Setup_writer line 413-414 has separate keys for Porsche only; the
canonical / CurrentSetup / CalibrationPoint layers carry a single `diff_ramp_angles` string.
A reader looking at `CurrentSetup.diff_ramp_angles` would expect a single value, but the
Porsche writer needs two angles. The split happens silently inside the Porsche writer path,
not anywhere visible to the user.

**Recommendation:** see §5 / R-2.

**CONFUSING-3 — `front_heave_setting` slot semantics.** Slot 1 of `_setup_key()` holds
either an N/mm rate (BMW: 180.0) or an index 0–8 (Ferrari: 4.0). Two BMW points with
`front_heave_setting=4.0` would never match a Ferrari point with `front_heave_setting=4.0`
because they're keyed by `track` first (slot 0) — but if a fitter ever pools across cars
the units mismatch would silently corrupt the regression. There is no defensive guard.

**Recommendation:** see §5 / R-3.

**CONFUSING-4 — `rear_spring_nmm` (canonical alias) vs `rear_spring_rate_nmm` (registry).**
`setup_registry.py:169` defines `rear_spring_nmm` as a separate FieldDefinition with the
same `solver_step=3` as `rear_spring_rate_nmm`. The writer uses `lr_spring_rate` keyed off
`step3.rear_spring_rate_nmm`. CurrentSetup carries `rear_spring_nmm`. Observation carries
`rear_spring_nmm`. CalibrationPoint carries `rear_spring_setting`. Five different names
for the same axle's coil spring rate.

**Recommendation:** see §5 / R-4.

**CONFUSING-5 — `front_torsion_od_mm` carries an INDEX for Ferrari.** Slot 5 of
`_setup_key()` is `front_torsion_od_mm` rounded to 3 dp. For BMW this is a real OD in mm
(13.9–18.2 range, e.g. 16.5). For Ferrari, the YAML field `Chassis.LeftFront.TorsionBarOD`
holds an integer 0–18 INDEX (per `_FERRARI_SPECS:361 range_min=0.0, range_max=18.0,
resolution=1.0`). The CalibrationPoint field name `front_torsion_od_mm` therefore lies
about its unit on Ferrari.

**Recommendation:** see §5 / R-3 (same fix as CONFUSING-3).

**CONFUSING-6 — `lr_spring_rate` writer key on Ferrari/Acura is actually `TorsionBarOD`.**
The writer key reads "spring_rate" but the .sto XML id is
`CarSetup_Chassis_LeftRear_TorsionBarOD`. The writer key name should reflect what's being
written, not what BMW uses. Same for Cadillac (which inherits BMW's spring rate, OK) but
NOT Acura (rear torsion bar).

**Recommendation:** see §5 / R-5.

**CONFUSING-7 — `Observation.setup["torsion_bar_od_mm"]` is BMW-shaped.** It carries
the front torsion-bar OD (canonical `front_torsion_od_mm`), not the rear. For Ferrari it
holds an index, for Acura it holds the front bar OD. A "torsion bar" without a directional
prefix is ambiguous when both axles can have torsion bars (Ferrari, Acura).

**Recommendation:** see §5 / R-1.

**CONFUSING-8 — Reader honours both `HsDampSlope` and `HsCompDampSlope` for front roll
slope.** `setup_reader.py:434` does `front_roll_damp.get("HsDampSlope") or
front_roll_damp.get("HsCompDampSlope")`. Two different YAML keys for the same channel,
inconsistent across cars. Hard-coded fallback OR is a smell.

### BROKEN (data doesn't reach the right place)

**BROKEN-1 — Porsche diff preload writer uses `BrakesDriveUnit.DiffSpec.DiffPreload` while
the reader looks at `BrakesDriveUnit.RearDiffSpec.Preload`.** The setup_writer
line 412 emits `CarSetup_BrakesDriveUnit_DiffSpec_DiffPreload`, but the YAML reader path
matches BMW (`RearDiffSpec.Preload`). According to the recent-landed-work note, the prior
agent already fixed `RearDiffSpec`→`DiffSpec` for the writer side, but the audit confirms
the read side is still pointed at `RearDiffSpec.Preload`. **Verify with a real Porsche IBT
session info dump that the actual YAML key is `DiffSpec.DiffPreload` and update the reader
accordingly. If iRacing actually emits both, document which is canonical.**

**BROKEN-2 — Porsche writer also has `diff_coast_ramp` and `diff_drive_ramp` separately**
(lines 413-414), but the canonical and registry have only one `diff_ramp_angles`. If the
solver returns a single string "1-1-65-65" the writer needs to split it into two values for
Porsche. There's no visible split logic, so the Porsche writer probably emits empty
strings for those keys (the lookup does `current_setup.get("diff_coast_ramp")`-style and
gets nothing). Verify with a Porsche `.sto` round-trip.

**BROKEN-3 — `compute_ideal_mc_sizes` and `nominal_mc_ratio` are referenced by the prompt
but only `nominal_mc_ratio` exists.** `solver/brake_solver.py:167` reads
`getattr(self.car, "nominal_mc_ratio", 1.0)` (which is a `getattr` fallback —
violates Key Principle 8). There is no `compute_ideal_mc_sizes` function anywhere in the
repo. The prompt's claim of a "shipped MC physics" function is incorrect; the codebase has
the *threshold check* (`abs(mc_ratio - nominal_ratio) > 0.05`) but no recommendation function.
This is BROKEN with respect to the documentation, not necessarily broken with respect to
runtime behaviour (the brake_solver does pass through the current_setup's MC values).
Verify with the user whether `compute_ideal_mc_sizes` is in-flight or this prompt was wrong.

**BROKEN-4 — Acura rear-spring slot semantics.** Slot 6 is `rear_spring_setting`. For
Acura this is the rear torsion-bar OD index. The CalibrationPoint extractor at line 527
reads `rear_spring_setting = raw.get("rear_torsion_bar_index", setup.rear_spring_nmm)`,
which is correct for Ferrari (rear is torsion bar, index lives in raw_indexed_fields), but
Acura does NOT populate `raw_indexed_fields` (only Ferrari does — see `setup_reader.py:516`).
So for Acura the slot reads `setup.rear_spring_nmm` which `setup_reader.py:400` only sets
when `lr.get("SpringRate")` is truthy. Acura has no SpringRate — only TorsionBarOD — so
`rear_spring_nmm = 0.0` always. Slot 6 silently collapses for Acura. **Fingerprint
collision risk:** all Acura points appear identical at slot 6 even when the torsion bar
varies.

**Verify by running:**
```python
from car_model.auto_calibrate import extract_point_from_ibt, _setup_key
pt1 = extract_point_from_ibt("acura_with_tb_18mm.ibt", car_name="acura")
pt2 = extract_point_from_ibt("acura_with_tb_15mm.ibt", car_name="acura")
print(_setup_key(pt1)[6], _setup_key(pt2)[6])  # If both 0.0 → collision
```

**Recommendation:** R-6.

**BROKEN-5 — `front_torsion_od_mm` field path for Acura doesn't pull rear torsion bar.**
The CalibrationPoint extractor at line 526 takes `front_torsion_setting = raw.get("front_torsion_bar_index", setup.front_torsion_od_mm)`.
For Acura: `raw_indexed_fields` is empty, so it falls back to `setup.front_torsion_od_mm`,
which gets `lf.get("TorsionBarOD")` — this is correct for the front. But the
*rear* torsion bar (Acura) is only read in `setup.rear_torsion_od_mm` — and that field is
NEVER fed into a CalibrationPoint slot. Cross-reference: CalibrationPoint has no
`rear_torsion_od_mm` field. So Acura calibration data drops the rear torsion bar entirely.

**Recommendation:** R-7.


## Section 5 — Recommendations

### R-1 — Rename `rear_third_*` to `rear_central_spring_*` everywhere (CONFUSING-1, CONFUSING-7)

The current name "third spring" is BMW-specific terminology. Ferrari and Porsche have
exactly one rear central spring (heave-style), not a third one over a pair of coil rears.
Proposed renames (in dependency order):

1. `setup_registry.FieldDefinition`: `rear_third_spring_nmm` → `rear_central_spring_nmm`
   (or keep as-is and add a comment block).
2. `CurrentSetup.rear_third_nmm` → `rear_central_spring_nmm`.
3. `Observation.setup["rear_third_nmm"]` → keep as alias for back-compat, add new key
   `rear_central_spring_nmm` and dual-write for one release cycle.
4. `CalibrationPoint.rear_third_setting` → `rear_central_spring_setting`.
5. `HeaveSolution.rear_third_nmm` → `rear_central_spring_nmm`.
6. Setup writer: keep BMW key `rear_third_spring` (XML id literally is `ThirdSpring`),
   add Ferrari/Porsche key `rear_central_spring` mapped to their `Chassis_Rear_HeaveSpring`
   XML id.

**Effort:** medium-large (touches every layer); **risk:** breaks pickled observations and
any user-visible names. Alternative: leave names alone, add a prominent banner-comment in
`CurrentSetup`, `CalibrationPoint`, and `HeaveSolution` describing the BMW-shape leak.
Document the canonical mapping in `docs/calibration_guide.md`.

### R-2 — Combine `diff_coast_ramp` + `diff_drive_ramp` Porsche-writer keys (CONFUSING-2, BROKEN-2)

Either:

(a) Replace the two Porsche writer keys with a single `diff_ramp_angles` (matching the
    canonical) and have the writer split it just before XML emit. Mirrors how Ferrari does it
    via `CoastDriveRampOptions`. Audit needed: confirm Porsche's `.sto` schema actually
    accepts a single combined `CoastDriveRampAngles` field rather than two separate
    `CoastRampAngle`+`DriveRampAngle` fields.

(b) If iRacing's Porsche schema really requires the split, expose the two values
    separately on `CurrentSetup.diff_coast_ramp_deg` and `CurrentSetup.diff_drive_ramp_deg`
    so the reader and writer agree, and deprecate the combined `diff_ramp_angles` for Porsche.

### R-3 — Distinguish index vs N/mm at every layer (CONFUSING-3, CONFUSING-5)

Add a unit suffix to clarify which storage form a field uses. Short version: keep
`front_heave_nmm` for the N/mm cars, add `front_heave_index` as a top-level
CalibrationPoint field (currently only on `setup_reader.raw_indexed_fields`), and have
`_setup_key()` use whichever field is non-zero, with a defensive sanity check (`assert not
(front_heave_nmm > 0 and front_heave_index > 0)`).

### R-4 — Pick ONE name for rear coil spring rate (CONFUSING-4)

Pick whichever is most prominent (`rear_spring_rate_nmm` in registry / step3 output, OR
`rear_spring_nmm` in CurrentSetup / Observation) and align everywhere else. The
`rear_spring_nmm` alias in `setup_registry.py:169` was added in the 2026-04-11 audit (per
CLAUDE.md) — that suggests it's the new preferred name. Promote it to step3 output and
leave the registry alias.

### R-5 — Per-car writer key names (CONFUSING-6)

Either:

(a) Per-car PARAM_IDS dicts use car-specific writer keys: `_FERRARI_PARAM_IDS["lr_torsion_od"]`
instead of inheriting `lr_spring_rate` from BMW.

(b) Add a comment block to each per-car PARAM_IDS dict listing which BMW keys are
"semantically wrong but XML-id correct" (e.g. `lr_spring_rate → CarSetup_..._TorsionBarOD`
on Ferrari).

### R-6 — Fix Acura rear-spring slot collapse (BROKEN-4)

Update `auto_calibrate.extract_point_from_ibt` to populate `rear_spring_setting` from
`setup.rear_torsion_od_mm` when it's the dominant signal. Concretely, change line 527 from:

```python
rear_spring_setting = raw.get("rear_torsion_bar_index", setup.rear_spring_nmm)
```

to:

```python
rear_spring_setting = (
    raw.get("rear_torsion_bar_index")
    or setup.rear_spring_nmm
    or setup.rear_torsion_od_mm  # Acura ORECA fallback
)
```

with an explicit `assert` or warning in `_setup_key` when slot 6 is 0.0 across the entire
calibration pool.

### R-7 — Add `rear_torsion_od_mm` to CalibrationPoint and `_setup_key` (BROKEN-5)

Acura calibration cannot disambiguate two sessions that vary only by the rear torsion bar.
Add a new field to `CalibrationPoint` and a new slot in `_setup_key`. Slot order matters
for backward compatibility (tuple comparison is positional), so append at slot 17.

### R-8 — Verify Porsche diff preload reader/writer alignment (BROKEN-1)

Run an end-to-end test:

```bash
python -m pipeline.produce --car porsche --ibt porsche_session.ibt --sto out.sto
# verify out.sto contains DiffSpec.DiffPreload (writer)
# verify the reader recovers the same value when loaded back
```

If the reader and writer disagree on the YAML key, fix the reader to look at
`BrakesDriveUnit.DiffSpec.DiffPreload` for Porsche.

### R-9 — Resolve `compute_ideal_mc_sizes` documentation discrepancy (BROKEN-3)

Either:

(a) The function exists in flight on a feature branch — wait for it to land.

(b) The prompt was incorrect — update the prompt's "RECENT LANDED WORK" section.

(c) Implement the function: take `nominal_mc_ratio` and the available MC option set
(`brake_master_cyl_options_mm`, `cars.py:1439`), return the closest legal pair. Currently
the brake solver only WARNS when ratio drifts; it doesn't recommend a fix.

### R-10 — Defensive guard against cross-car CalibrationPoint pooling (CONFUSING-3)

Add a `car_canonical: str` field to `CalibrationPoint` (currently absent — only `track` is
keyed). Make `_setup_key()` include it as slot 0a (between `track` and the spring fields).
This ensures pooling can never accidentally mix BMW (rate-units) and Ferrari (index-units)
calibration points.


## Section 6 — Recap (severity tally)

| Severity | Count | Examples |
|---|---|---|
| GOOD (intentional translation) | 7 | Pascal→snake, average per-corner, per-car YAML override at reader, damper layout dispatch |
| HARMLESS (different name, same data) | 10 | `front_heave_setting` vs `_nmm`, observation key shortenings, Porsche missing slope, tyre node-name asymmetry |
| CONFUSING (name suggests different semantics) | 8 | `rear_third_nmm` for Ferrari/Porsche, Porsche diff ramp split, Ferrari index in `_od_mm` field, multiple aliases for rear coil spring rate |
| BROKEN (data drops or mismatched) | 5 | Porsche diff preload writer/reader path mismatch (verify), Porsche `diff_coast_ramp`/`diff_drive_ramp` no split logic, missing `compute_ideal_mc_sizes`, Acura rear spring slot collapse, Acura rear torsion bar dropped from CalibrationPoint |

Total: **30 divergences** across 7 layers and 5 cars; **5 are BROKEN and warrant a fix
PR**, **8 are CONFUSING and warrant a rename or comment**, the rest are intentional
translations or harmless aliases.


## Section 7 — Out-of-scope notes

These were noticed during the audit but are not naming-consistency issues:

- **`SuspensionArchitecture` enum and the Phase-2 GT3 work** are referenced in the worktree
  CLAUDE.md but the gate-cascade `{2→1, 3→2, 4→3, 5→4, 6→3}` in this branch's CLAUDE.md
  matches GTP-only. The branch-name `gt3-phase0-foundations` suggests GT3 work in flight;
  if `SuspensionArchitecture.GT3_COIL_4WHEEL` exists, the audit table needs a "GT3 cars"
  column. Defer until GT3 lands per per-car schemas.
- **`solver/heave_solver.py`** does not have a clean dataclass for the perch_offset_front_mm
  / perch_offset_rear_mm contract — they're just floats. Likely fine but a typed wrapper
  (e.g. `PerchOffsets` namedtuple) would prevent argument-order bugs.
- **`Observation.solver_predictions`** is a free-form dict; there's no schema. Adding a
  typed predicate set would help the prediction-vs-measurement feedback loop be auditable.
- **`Observation.setup_fingerprint`** uses `(wing bucket, RH bucket, spring bucket)` — a
  3-tuple. `_setup_key` uses a 16+-tuple. The two are NOT comparable; fingerprint covers
  veto-cluster matching while `_setup_key` covers calibration uniqueness. They're
  intentionally distinct, but an integrator might confuse them — add docstring linking the
  two.

---

**Audit complete.** Generated by Unit 9 of the GT3 Phase 0 9-unit batch. No code modified.
