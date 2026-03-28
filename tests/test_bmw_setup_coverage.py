import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from car_model.cars import get_car
from output.setup_writer import write_sto
from solver.bmw_coverage import (
    build_parameter_coverage,
    build_search_baseline,
    build_telemetry_coverage,
    bmw_coverage_fields,
)
from solver.decision_trace import build_parameter_decisions
from solver.legal_space import LegalSpace


def _corner(ls_comp=7, ls_rbd=6, hs_comp=4, hs_rbd=8, hs_slope=11):
    return SimpleNamespace(
        ls_comp=ls_comp,
        ls_rbd=ls_rbd,
        hs_comp=hs_comp,
        hs_rbd=hs_rbd,
        hs_slope=hs_slope,
    )


def _step_bundle():
    step1 = SimpleNamespace(
        dynamic_front_rh_mm=20.0,
        dynamic_rear_rh_mm=42.0,
        df_balance_pct=45.6,
        ld_ratio=4.2,
        static_front_rh_mm=31.0,
        static_rear_rh_mm=48.5,
        front_pushrod_offset_mm=-26.0,
        rear_pushrod_offset_mm=-24.0,
    )
    step2 = SimpleNamespace(
        front_heave_nmm=55.0,
        perch_offset_front_mm=-10.0,
        rear_third_nmm=500.0,
        perch_offset_rear_mm=43.0,
        front_excursion_at_rate_mm=12.0,
        defl_max_front_mm=90.0,
    )
    step3 = SimpleNamespace(
        front_torsion_od_mm=13.9,
        front_wheel_rate_nmm=180.0,
        rear_spring_rate_nmm=170.0,
        rear_spring_perch_mm=31.0,
    )
    step4 = SimpleNamespace(
        front_arb_size="Medium",
        front_arb_blade_start=2,
        farb_blade_locked=2,
        rear_arb_size="Stiff",
        rear_arb_blade_start=4,
        rarb_blade_slow_corner=4,
        rarb_blade_fast_corner=4,
        lltd_achieved=0.515,
    )
    step5 = SimpleNamespace(
        front_camber_deg=-2.9,
        rear_camber_deg=-1.9,
        front_toe_mm=-0.4,
        rear_toe_mm=0.1,
    )
    step6 = SimpleNamespace(
        lf=_corner(ls_comp=7, ls_rbd=6, hs_comp=4, hs_rbd=9, hs_slope=11),
        rf=_corner(ls_comp=7, ls_rbd=6, hs_comp=4, hs_rbd=9, hs_slope=11),
        lr=_corner(ls_comp=6, ls_rbd=7, hs_comp=5, hs_rbd=11, hs_slope=12),
        rr=_corner(ls_comp=6, ls_rbd=7, hs_comp=5, hs_rbd=11, hs_slope=12),
        c_hs_front=1200.0,
        c_hs_rear=1400.0,
    )
    supporting = SimpleNamespace(
        brake_bias_pct=45.8,
        brake_bias_target=1.0,
        brake_bias_migration=-1.0,
        brake_bias_migration_gain=0.5,
        brake_bias_target_status="seeded_from_telemetry",
        brake_bias_migration_status="seeded_from_telemetry",
        front_master_cyl_mm=17.8,
        rear_master_cyl_mm=22.2,
        master_cylinder_status="seeded_from_telemetry",
        pad_compound="Low",
        pad_compound_status="seeded_from_telemetry",
        diff_preload_nm=25.0,
        diff_ramp_option_idx=2,
        diff_ramp_angles="50/75",
        diff_ramp_coast=50,
        diff_ramp_drive=75,
        diff_clutch_plates=6,
        tc_gain=5,
        tc_slip=4,
        fuel_l=88.0,
        fuel_low_warning_l=9.0,
        fuel_target_l=90.0,
        gear_stack="Short",
        hybrid_rear_drive_enabled="Off",
        hybrid_rear_drive_corner_pct=0.0,
        roof_light_color="Orange",
        tyre_cold_fl_kpa=152.0,
    )
    return step1, step2, step3, step4, step5, step6, supporting


class BMWSetupCoverageTests(unittest.TestCase):
    def test_bmw_search_fields_exist_in_registry_and_legal_space(self) -> None:
        car = get_car("bmw")
        space = LegalSpace.from_car(car)
        allowed = {"search", "local_refine", "deterministic_context", "computed_display"}
        coverage_fields = bmw_coverage_fields()

        self.assertIn("diff_ramp_option_idx", coverage_fields)
        self.assertNotIn("diff_coast_ramp_idx", coverage_fields)
        self.assertNotIn("diff_drive_ramp_idx", coverage_fields)
        self.assertTrue(all(build_parameter_coverage(
            car=car,
            wing=17.0,
            current_setup=SimpleNamespace(),
            step1=SimpleNamespace(),
            step2=SimpleNamespace(),
            step3=SimpleNamespace(),
            step4=SimpleNamespace(),
            step5=SimpleNamespace(),
            step6=SimpleNamespace(lf=SimpleNamespace(), lr=SimpleNamespace()),
            supporting=SimpleNamespace(),
        )[field]["classification"] in allowed for field in coverage_fields))

        coverage = build_parameter_coverage(
            car=car,
            wing=17.0,
            current_setup=SimpleNamespace(),
            step1=SimpleNamespace(),
            step2=SimpleNamespace(),
            step3=SimpleNamespace(),
            step4=SimpleNamespace(),
            step5=SimpleNamespace(),
            step6=SimpleNamespace(lf=SimpleNamespace(), lr=SimpleNamespace()),
            supporting=SimpleNamespace(),
        )
        expected_legal_dims = {
            "front_heave_spring_nmm",
            "rear_third_spring_nmm",
            "front_arb_size",
            "rear_arb_size",
            "front_hs_rbd",
            "front_hs_slope",
            "rear_hs_rbd",
            "rear_hs_slope",
            "diff_ramp_option_idx",
            "diff_clutch_plates",
            "diff_preload_nm",
            "tc_gain",
            "tc_slip",
        }
        self.assertTrue(expected_legal_dims.issubset(set(space._dim_map)))

    def test_build_search_baseline_covers_full_bmw_tier_a_surface(self) -> None:
        car = get_car("bmw")
        step1, step2, step3, step4, step5, step6, supporting = _step_bundle()
        baseline = build_search_baseline(
            car=car,
            wing=17.0,
            current_setup=SimpleNamespace(),
            step1=step1,
            step2=step2,
            step3=step3,
            step4=step4,
            step5=step5,
            step6=step6,
            supporting=supporting,
        )

        for key in (
            "front_arb_size",
            "rear_arb_size",
            "front_hs_rbd",
            "front_hs_slope",
            "rear_hs_rbd",
            "rear_hs_slope",
            "diff_ramp_option_idx",
            "diff_clutch_plates",
            "tc_gain",
            "tc_slip",
        ):
            self.assertIn(key, baseline)
            self.assertIsNotNone(baseline[key])

    def test_parameter_coverage_marks_local_refine_context_and_display_fields(self) -> None:
        car = get_car("bmw")
        step1, step2, step3, step4, step5, step6, supporting = _step_bundle()
        current_setup = SimpleNamespace(
            front_pushrod_mm=-26.5,
            rear_pushrod_mm=-24.0,
            front_heave_nmm=50.0,
            front_heave_perch_mm=-11.0,
            rear_third_nmm=480.0,
            rear_third_perch_mm=42.0,
            front_torsion_od_mm=13.9,
            rear_spring_nmm=160.0,
            rear_spring_perch_mm=30.0,
            front_arb_size="Soft",
            front_arb_blade=1,
            rear_arb_size="Soft",
            rear_arb_blade=3,
            front_camber_deg=-2.8,
            rear_camber_deg=-1.9,
            front_toe_mm=-0.3,
            rear_toe_mm=0.0,
            front_ls_comp=7,
            front_ls_rbd=6,
            front_hs_comp=4,
            front_hs_rbd=8,
            front_hs_slope=11,
            rear_ls_comp=6,
            rear_ls_rbd=7,
            rear_hs_comp=4,
            rear_hs_rbd=11,
            rear_hs_slope=11,
            brake_bias_pct=46.0,
            brake_bias_target=1.5,
            brake_bias_migration=-0.5,
            front_master_cyl_mm=19.1,
            rear_master_cyl_mm=20.6,
            pad_compound="Medium",
            diff_preload_nm=20.0,
            diff_ramp_angles="40/65",
            diff_clutch_plates=4,
            tc_gain=4,
            tc_slip=3,
            fuel_l=89.0,
            fuel_low_warning_l=8.0,
            fuel_target_l=89.0,
            gear_stack="Short",
            brake_bias_migration_gain=0.5,
            hybrid_rear_drive_enabled="Off",
            hybrid_rear_drive_corner_pct=0.0,
            roof_light_color="Orange",
        )
        coverage = build_parameter_coverage(
            car=car,
            wing=17.0,
            current_setup=current_setup,
            step1=step1,
            step2=step2,
            step3=step3,
            step4=step4,
            step5=step5,
            step6=step6,
            supporting=supporting,
        )

        self.assertEqual(coverage["front_heave_perch_mm"]["classification"], "local_refine")
        self.assertEqual(coverage["fuel_low_warning_l"]["classification"], "deterministic_context")
        self.assertEqual(coverage["diff_ramp_angles"]["classification"], "computed_display")
        self.assertEqual(coverage["brake_bias_target"]["classification"], "search")

    def test_telemetry_coverage_includes_brake_and_damper_requirements(self) -> None:
        measured = SimpleNamespace(
            telemetry_signals={},
            front_braking_lock_ratio_p95=0.08,
            hydraulic_brake_split_pct=46.2,
            pitch_range_braking_deg=1.1,
            abs_active_pct=18.0,
            front_shock_oscillation_hz=8.9,
            front_shock_vel_p99_mps=0.42,
            rear_shock_oscillation_hz=9.1,
            rear_shock_vel_p99_mps=0.45,
        )
        coverage = build_telemetry_coverage(measured=measured)
        self.assertIn("front_braking_lock_ratio_p95", coverage["front_master_cyl_mm"]["required_signals"])
        self.assertIn("front_shock_oscillation_hz", coverage["front_hs_rbd"]["required_signals"])
        self.assertGreater(coverage["front_master_cyl_mm"]["coverage_ratio"], 0.0)

    def test_build_parameter_decisions_surfaces_diff_option_and_damper_changes(self) -> None:
        step1, step2, step3, step4, step5, step6, supporting = _step_bundle()
        current_setup = SimpleNamespace(
            front_pushrod_mm=-26.5,
            rear_pushrod_mm=-24.0,
            front_heave_nmm=50.0,
            front_heave_perch_mm=-11.0,
            rear_third_nmm=480.0,
            rear_third_perch_mm=42.0,
            front_torsion_od_mm=13.9,
            rear_spring_nmm=160.0,
            rear_spring_perch_mm=30.0,
            front_arb_size="Soft",
            front_arb_blade=1,
            rear_arb_size="Soft",
            rear_arb_blade=3,
            front_camber_deg=-2.8,
            rear_camber_deg=-1.9,
            front_toe_mm=-0.3,
            rear_toe_mm=0.0,
            front_ls_comp=7,
            front_ls_rbd=6,
            front_hs_comp=4,
            front_hs_rbd=8,
            front_hs_slope=11,
            rear_ls_comp=6,
            rear_ls_rbd=7,
            rear_hs_comp=4,
            rear_hs_rbd=11,
            rear_hs_slope=11,
            brake_bias_pct=46.0,
            brake_bias_target=1.5,
            brake_bias_migration=-0.5,
            front_master_cyl_mm=19.1,
            rear_master_cyl_mm=20.6,
            pad_compound="Medium",
            diff_preload_nm=20.0,
            diff_ramp_angles="40/65",
            diff_clutch_plates=4,
            tc_gain=4,
            tc_slip=3,
        )
        measured = SimpleNamespace(
            telemetry_signals={},
            front_rh_std_mm=4.0,
            rear_rh_std_mm=6.0,
            front_heave_travel_used_pct=88.0,
            bottoming_event_count_front_clean=1,
            rear_power_slip_ratio_p95=0.10,
            understeer_low_speed_deg=1.2,
            body_slip_p95_deg=3.5,
            front_braking_lock_ratio_p95=0.08,
            hydraulic_brake_split_pct=46.5,
            pitch_range_braking_deg=1.1,
            abs_active_pct=18.0,
            front_shock_oscillation_hz=8.9,
            front_shock_vel_p99_mps=0.42,
            rear_shock_oscillation_hz=9.1,
            rear_shock_vel_p99_mps=0.45,
        )
        decisions = build_parameter_decisions(
            car_name="bmw",
            current_setup=current_setup,
            measured=measured,
            step1=step1,
            step2=step2,
            step3=step3,
            step4=step4,
            step5=step5,
            step6=step6,
            supporting=supporting,
            legality=SimpleNamespace(valid=True),
            fallback_reasons=[],
        )
        by_parameter = {decision.parameter: decision for decision in decisions}
        for key in ("diff_ramp_option_idx", "diff_ramp_angles", "front_hs_rbd", "rear_hs_slope", "brake_bias_target"):
            self.assertIn(key, by_parameter)

    def test_write_sto_writes_bmw_brake_hardware_and_context_fields(self) -> None:
        step1, step2, step3, step4, step5, step6, supporting = _step_bundle()
        with TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "bmw_setup.sto"
            write_sto(
                car_name="BMW M Hybrid V8",
                track_name="Sebring",
                wing=17.0,
                fuel_l=supporting.fuel_l,
                step1=step1,
                step2=step2,
                step3=step3,
                step4=step4,
                step5=step5,
                step6=step6,
                output_path=out_path,
                car_canonical="bmw",
                tyre_pressure_kpa=supporting.tyre_cold_fl_kpa,
                brake_bias_pct=supporting.brake_bias_pct,
                brake_bias_target=supporting.brake_bias_target,
                brake_bias_migration=supporting.brake_bias_migration,
                front_master_cyl_mm=supporting.front_master_cyl_mm,
                rear_master_cyl_mm=supporting.rear_master_cyl_mm,
                pad_compound=supporting.pad_compound,
                diff_coast_drive_ramp=supporting.diff_ramp_angles,
                diff_clutch_plates=supporting.diff_clutch_plates,
                diff_preload_nm=supporting.diff_preload_nm,
                tc_gain=supporting.tc_gain,
                tc_slip=supporting.tc_slip,
                fuel_low_warning_l=supporting.fuel_low_warning_l,
                gear_stack=supporting.gear_stack,
                roof_light_color=supporting.roof_light_color,
            )
            text = out_path.read_text(encoding="utf-8")

        self.assertIn("CarSetup_BrakesDriveUnit_BrakeSpec_BrakeBiasTarget", text)
        self.assertIn("CarSetup_BrakesDriveUnit_BrakeSpec_FrontMasterCyl", text)
        self.assertIn("CarSetup_BrakesDriveUnit_GearRatios_GearStack", text)
        self.assertIn("CarSetup_BrakesDriveUnit_Lighting_RoofIdLightColor", text)

    def test_write_sto_preserves_half_mm_bmw_front_heave_perch(self) -> None:
        step1, step2, step3, step4, step5, step6, supporting = _step_bundle()
        step2.perch_offset_front_mm = -7.5
        with TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "bmw_half_step.sto"
            write_sto(
                car_name="BMW M Hybrid V8",
                track_name="Sebring",
                wing=17.0,
                fuel_l=supporting.fuel_l,
                step1=step1,
                step2=step2,
                step3=step3,
                step4=step4,
                step5=step5,
                step6=step6,
                output_path=out_path,
                car_canonical="bmw",
                tyre_pressure_kpa=supporting.tyre_cold_fl_kpa,
                brake_bias_pct=supporting.brake_bias_pct,
                diff_coast_drive_ramp=supporting.diff_ramp_angles,
                diff_clutch_plates=supporting.diff_clutch_plates,
                diff_preload_nm=supporting.diff_preload_nm,
                tc_gain=supporting.tc_gain,
                tc_slip=supporting.tc_slip,
            )
            text = out_path.read_text(encoding="utf-8")

        self.assertIn('CarSetup_Chassis_Front_HeavePerchOffset" Value="-7.5"', text)


if __name__ == "__main__":
    unittest.main()
