import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from xml.etree import ElementTree

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from output.setup_writer import write_sto


def _damper(ls_comp: int, hs_comp: int, hs_slope: int, ls_rbd: int, hs_rbd: int) -> SimpleNamespace:
    return SimpleNamespace(
        ls_comp=ls_comp,
        hs_comp=hs_comp,
        hs_slope=hs_slope,
        ls_rbd=ls_rbd,
        hs_rbd=hs_rbd,
    )


def _ferrari_step_bundle() -> tuple[SimpleNamespace, ...]:
    step1 = SimpleNamespace(
        dynamic_front_rh_mm=30.1,
        dynamic_rear_rh_mm=44.1,
        df_balance_pct=50.0,
        ld_ratio=4.0,
        static_front_rh_mm=30.1,
        static_rear_rh_mm=44.1,
        front_pushrod_offset_mm=1.0,
        rear_pushrod_offset_mm=5.0,
    )
    step2 = SimpleNamespace(
        front_heave_nmm=3.0,
        rear_third_nmm=5.0,
        perch_offset_front_mm=-16.5,
        perch_offset_rear_mm=-112.5,
        front_excursion_at_rate_mm=10.9,
        front_bottoming_margin_mm=5.0,
        rear_bottoming_margin_mm=12.0,
    )
    step3 = SimpleNamespace(
        front_torsion_od_mm=2.0,
        rear_spring_rate_nmm=2.0,
        rear_spring_perch_mm=0.0,
    )
    step4 = SimpleNamespace(
        front_arb_size="A",
        front_arb_blade_start=1,
        rear_arb_size="C",
        rear_arb_blade_start=3,
    )
    step5 = SimpleNamespace(
        front_camber_deg=-2.8,
        rear_camber_deg=-1.9,
        front_toe_mm=-0.7,
        rear_toe_mm=0.3,
    )
    step6 = SimpleNamespace(
        lf=_damper(20, 20, 11, 24, 28),
        rf=_damper(20, 20, 11, 24, 28),
        lr=_damper(16, 32, 11, 34, 35),
        rr=_damper(16, 32, 11, 34, 35),
    )
    return step1, step2, step3, step4, step5, step6


class FerrariSetupWriterTests(unittest.TestCase):
    def test_write_sto_uses_ferrari_native_ids_and_values(self) -> None:
        step1, step2, step3, step4, step5, step6 = _ferrari_step_bundle()

        with TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "ferrari_hockenheim.sto"
            write_sto(
                car_name="Ferrari 499P",
                track_name="Hockenheim",
                wing=15.0,
                fuel_l=58.0,
                step1=step1,
                step2=step2,
                step3=step3,
                step4=step4,
                step5=step5,
                step6=step6,
                output_path=out_path,
                car_canonical="ferrari",
                tyre_pressure_kpa=152.0,
                brake_bias_pct=53.0,
                brake_bias_target=0.0,
                brake_bias_migration=6.0,
                brake_bias_migration_gain=-1.0,
                pad_compound="Medium",
                front_master_cyl_mm=19.1,
                rear_master_cyl_mm=19.1,
                diff_coast_drive_ramp="Less Locking",
                diff_clutch_plates=4,
                diff_preload_nm=25.0,
                front_diff_preload_nm=-50.0,
                tc_gain=3,
                tc_slip=4,
                gear_stack="Short",
                speed_in_first_kph=121.7,
                speed_in_second_kph=157.5,
                speed_in_third_kph=190.0,
                speed_in_fourth_kph=222.7,
                speed_in_fifth_kph=256.6,
                speed_in_sixth_kph=291.0,
                speed_in_seventh_kph=329.2,
                fuel_low_warning_l=10.0,
                fuel_target_l=2.8,
                roof_light_color="Blue",
                hybrid_rear_drive_enabled="On",
                hybrid_rear_drive_corner_pct=90.0,
                front_tb_turns=0.089,
                rear_tb_turns=0.040,
                include_computed=True,
            )
            text = out_path.read_text(encoding="utf-8")

        self.assertNotIn("CarSetup_BrakesDriveUnit_", text)
        self.assertNotIn("CarSetup_Chassis_LeftFront_LsCompDamping", text)
        self.assertNotIn("CarSetup_Chassis_LeftRear_SpringPerchOffset", text)

        root = ElementTree.fromstring(text)
        entries = {
            node.attrib["Id"]: node.attrib
            for node in root.findall(".//Details/*")
            if "Id" in node.attrib
        }

        required_ids = [
            "CarSetup_Dampers_LeftFrontDamper_LsCompDamping",
            "CarSetup_Dampers_RightFrontDamper_LsCompDamping",
            "CarSetup_Dampers_LeftRearDamper_HsRbdDamping",
            "CarSetup_Dampers_RightRearDamper_HsRbdDamping",
            "CarSetup_Systems_BrakeSpec_BiasMigrationGain",
            "CarSetup_Systems_FrontDiffSpec_Preload",
            "CarSetup_Systems_RearDiffSpec_CoastDriveRampOptions",
            "CarSetup_Systems_Fuel_FuelTarget",
            "CarSetup_Systems_GearRatios_SpeedInFirst",
            "CarSetup_Systems_GearRatios_SpeedInSeventh",
            "CarSetup_Systems_HybridConfig_HybridRearDriveEnabled",
            "CarSetup_Systems_HybridConfig_HybridRearDriveCornerPct",
            "CarSetup_Systems_Lighting_RoofIdLightColor",
        ]
        for field_id in required_ids:
            self.assertIn(field_id, entries)

        self.assertEqual(entries["CarSetup_Chassis_Front_HeaveSpring"]["Value"], "3")
        self.assertEqual(entries["CarSetup_Chassis_Rear_HeaveSpring"]["Value"], "5")
        self.assertEqual(entries["CarSetup_Chassis_LeftFront_TorsionBarOD"]["Value"], "2")
        self.assertEqual(entries["CarSetup_Chassis_LeftRear_TorsionBarOD"]["Value"], "2")
        self.assertEqual(entries["CarSetup_Dampers_LeftFrontDamper_LsCompDamping"]["Value"], "20")
        self.assertEqual(entries["CarSetup_Dampers_RightRearDamper_HsRbdDamping"]["Value"], "35")
        self.assertEqual(entries["CarSetup_Systems_BrakeSpec_BrakePressureBias"]["Value"], "53.0")
        self.assertEqual(entries["CarSetup_Systems_BrakeSpec_BiasMigration"]["Value"], "6.0")
        self.assertEqual(entries["CarSetup_Systems_BrakeSpec_BiasMigrationGain"]["Value"], "-1.0")
        self.assertEqual(entries["CarSetup_Systems_FrontDiffSpec_Preload"]["Value"], "-50")
        self.assertEqual(entries["CarSetup_Systems_RearDiffSpec_CoastDriveRampOptions"]["Value"], "Less Locking")
        self.assertEqual(entries["CarSetup_Systems_RearDiffSpec_Preload"]["Value"], "25")
        self.assertEqual(entries["CarSetup_Systems_Fuel_FuelLevel"]["Value"], "58.0")
        self.assertEqual(entries["CarSetup_Systems_Fuel_FuelTarget"]["Value"], "2.8")
        self.assertEqual(entries["CarSetup_Systems_Fuel_FuelLowWarning"]["Value"], "10.0")
        self.assertEqual(entries["CarSetup_Systems_GearRatios_GearStack"]["Value"], "Short")
        self.assertEqual(entries["CarSetup_Systems_GearRatios_SpeedInFirst"]["Value"], "121.7")
        self.assertEqual(entries["CarSetup_Systems_GearRatios_SpeedInSeventh"]["Value"], "329.2")
        self.assertEqual(entries["CarSetup_Systems_HybridConfig_HybridRearDriveEnabled"]["Value"], "On")
        self.assertEqual(entries["CarSetup_Systems_HybridConfig_HybridRearDriveCornerPct"]["Value"], "90.0")
        self.assertEqual(entries["CarSetup_Systems_Lighting_RoofIdLightColor"]["Value"], "Blue")


if __name__ == "__main__":
    unittest.main()
