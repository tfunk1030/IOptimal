"""W4.2: GT3 Aston Vantage + Porsche 992 GT3 R setup writer tests.

Verifies that `output.setup_writer.write_sto()` produces valid `.sto` files
for Aston Vantage GT3 EVO and Porsche 911 GT3 R (992), and that BMW M4 GT3
(W4.1) and GTP paths are not regressed.

Per-car divergences exercised:
- Aston: `FrontBrakesLights` section, `FarbBlades`/`RarbBlades`, `AeroBalanceCalculator`
  suffix, `EpasSetting`/`ThrottleResponse`/`EnduranceLights`, TC label "n (TC SLIP)".
- Porsche: integer `ArbSetting`/`RarbSetting` (no blade), paired `Chassis.Rear.TotalToeIn`,
  `Chassis.FrontBrakesLights.FuelLevel` (NOT in Rear), dual wing write, TC "n (TC-LAT)",
  `ThrottleShapeSetting`/`DashDisplayPage`.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from car_model.cars import get_car
from output.setup_writer import write_sto


# ─── Test fixtures ───────────────────────────────────────────────────────────

def _gt3_corner_damper(ls_comp=7, ls_rbd=6, hs_comp=4, hs_rbd=8, hs_slope=0):
    return SimpleNamespace(
        ls_comp=ls_comp, ls_rbd=ls_rbd,
        hs_comp=hs_comp, hs_rbd=hs_rbd, hs_slope=hs_slope,
    )


def _gt3_step_bundle():
    """Generic GT3 step1..step6 fixtures usable for Aston + Porsche tests."""
    step1 = SimpleNamespace(
        dynamic_front_rh_mm=35.0, dynamic_rear_rh_mm=80.0,
        df_balance_pct=44.0, ld_ratio=4.0,
        static_front_rh_mm=72.0, static_rear_rh_mm=82.0,
        front_pushrod_offset_mm=0.0, rear_pushrod_offset_mm=0.0,
    )
    step2 = SimpleNamespace(
        present=False,
        front_heave_nmm=0.0, rear_third_nmm=0.0,
        perch_offset_front_mm=0.0, perch_offset_rear_mm=0.0,
        front_excursion_at_rate_mm=0.0, defl_max_front_mm=0.0,
    )
    step3 = SimpleNamespace(
        front_torsion_od_mm=0.0, front_wheel_rate_nmm=220.0,
        rear_spring_rate_nmm=180.0, rear_spring_perch_mm=0.0,
        rear_motion_ratio=1.0,
        front_coil_rate_nmm=240.0, front_coil_perch_mm=0.0,
        front_torsion_bar_turns=0.0, rear_torsion_bar_turns=0.0,
    )
    step4 = SimpleNamespace(
        front_arb_size="3", front_arb_blade_start=4,
        rear_arb_size="2", rear_arb_blade_start=3,
        lltd_achieved=0.50,
    )
    step5 = SimpleNamespace(
        front_camber_deg=-4.0, rear_camber_deg=-2.8,
        front_toe_mm=-0.4, rear_toe_mm=1.5,
    )
    front_damper = _gt3_corner_damper(ls_comp=7, ls_rbd=5, hs_comp=3, hs_rbd=3)
    rear_damper = _gt3_corner_damper(ls_comp=6, ls_rbd=7, hs_comp=4, hs_rbd=5)
    step6 = SimpleNamespace(
        lf=front_damper, rf=front_damper,
        lr=rear_damper, rr=rear_damper,
        c_hs_front=1200.0, c_hs_rear=1400.0,
    )
    supporting = SimpleNamespace(
        brake_bias_pct=52.0,
        diff_preload_nm=100.0, diff_clutch_plates=8,
        diff_ramp_angles=None,
        tc_gain=4, tc_slip=6,
        fuel_l=100.0, fuel_low_warning_l=8.0,
        gear_stack="FIA", roof_light_color="Orange",
        tyre_cold_fl_kpa=180.0,
        pad_compound="Medium",
        front_master_cyl_mm=22.2, rear_master_cyl_mm=22.2,
    )
    return step1, step2, step3, step4, step5, step6, supporting


def _write_and_parse(car_canonical: str):
    """Build the GT3 fixture, write a .sto, return text + parsed XML root."""
    car = get_car(car_canonical)
    step1, step2, step3, step4, step5, step6, supporting = _gt3_step_bundle()
    with TemporaryDirectory() as tmp:
        sto_path = Path(tmp) / "test.sto"
        write_sto(
            car_name=car.name,
            track_name="Spielberg",
            wing=-2.0,
            fuel_l=supporting.fuel_l,
            step1=step1, step2=step2, step3=step3,
            step4=step4, step5=step5, step6=step6,
            output_path=sto_path,
            car_canonical=car_canonical,
            tyre_pressure_kpa=supporting.tyre_cold_fl_kpa,
            brake_bias_pct=supporting.brake_bias_pct,
            pad_compound=supporting.pad_compound,
            front_master_cyl_mm=supporting.front_master_cyl_mm,
            rear_master_cyl_mm=supporting.rear_master_cyl_mm,
            diff_preload_nm=supporting.diff_preload_nm,
            tc_gain=supporting.tc_gain,
            tc_slip=supporting.tc_slip,
            fuel_low_warning_l=supporting.fuel_low_warning_l,
            gear_stack=supporting.gear_stack,
        )
        text = sto_path.read_text(encoding="utf-8")
    # Strip leading <?xml ...?> declaration if present (matches W4.1 pattern)
    xml_text = text.split("?>", 1)[1] if text.startswith("<?xml") else text
    return text, ET.fromstring(xml_text)


# ─── Aston Vantage GT3 ───────────────────────────────────────────────────────

class GT3AstonSetupWriterTests(unittest.TestCase):

    def setUp(self):
        self.text, self.root = _write_and_parse("aston_martin_vantage_gt3")

    def test_write_does_not_raise(self):
        # _write_and_parse already exercised write_sto; reaching setUp is enough.
        self.assertTrue(self.text)

    def test_xml_is_well_formed(self):
        self.assertIsNotNone(self.root)

    def test_aston_uses_farb_blades_under_front_brakes_lights(self):
        self.assertIn("CarSetup_Chassis_FrontBrakesLights_FarbBlades", self.text)
        # NOT BMW's plain "ArbBlades" under FrontBrakes
        self.assertNotIn("CarSetup_Chassis_FrontBrakes_ArbBlades", self.text)

    def test_aston_uses_rarb_blades_in_rear(self):
        self.assertIn("CarSetup_Chassis_Rear_RarbBlades", self.text)
        self.assertNotIn("CarSetup_Chassis_Rear_ArbBlades", self.text)

    def test_aston_aero_balance_calculator_suffix(self):
        # Aston uses AeroBalanceCalculator (full word) vs BMW/Porsche AeroBalanceCalc
        self.assertIn("CarSetup_TiresAero_AeroBalanceCalculator_", self.text)

    def test_aston_front_toe_under_front_brakes_lights(self):
        self.assertIn("CarSetup_Chassis_FrontBrakesLights_TotalToeIn", self.text)

    def test_aston_fuel_in_chassis_rear(self):
        self.assertIn("CarSetup_Chassis_Rear_FuelLevel", self.text)
        # Aston puts fuel in Rear, NOT in front (that's Porsche's path)
        self.assertNotIn("CarSetup_Chassis_FrontBrakesLights_FuelLevel", self.text)

    def test_aston_tc_label_uses_tc_slip_suffix(self):
        # Find the TcSetting line and check its value
        match_lines = [l for l in self.text.splitlines() if "TcSetting" in l]
        self.assertTrue(match_lines, "TcSetting not emitted")
        self.assertTrue(
            any("TC SLIP" in l for l in match_lines),
            f"Aston TC label should be 'TC SLIP', got: {match_lines!r}",
        )

    def test_aston_emits_epas_setting(self):
        # ASTON ONLY: EpasSetting + ThrottleResponse
        self.assertIn("CarSetup_Chassis_InCarAdjustments_EpasSetting", self.text)
        self.assertIn("CarSetup_Chassis_InCarAdjustments_ThrottleResponse", self.text)

    def test_aston_rear_toe_is_per_wheel(self):
        # BMW + Aston use per-wheel rear toe
        self.assertIn("CarSetup_Chassis_LeftRear_ToeIn", self.text)
        self.assertIn("CarSetup_Chassis_RightRear_ToeIn", self.text)
        # NOT Porsche-paired
        self.assertNotIn("CarSetup_Chassis_Rear_TotalToeIn", self.text)

    def test_aston_emits_no_gtp_only_fields(self):
        forbidden = [
            "HeaveSpring", "ThirdSpring", "TorsionBarOD", "TorsionBarTurns",
            "Systems_Fuel_FuelLevel", "BrakesDriveUnit_BrakeSpec",
        ]
        for tag in forbidden:
            self.assertNotIn(tag, self.text, f"GTP-only tag {tag!r} leaked into Aston .sto")


# ─── Porsche 992 GT3 R ───────────────────────────────────────────────────────

class GT3Porsche992SetupWriterTests(unittest.TestCase):

    def setUp(self):
        self.text, self.root = _write_and_parse("porsche_992_gt3r")

    def test_write_does_not_raise(self):
        self.assertTrue(self.text)

    def test_porsche_integer_arb_setting(self):
        # Porsche uses ArbSetting (single int) not ArbBlades
        self.assertIn("CarSetup_Chassis_FrontBrakesLights_ArbSetting", self.text)
        self.assertIn("CarSetup_Chassis_Rear_RarbSetting", self.text)
        # NOT blade-style fields
        self.assertNotIn("CarSetup_Chassis_FrontBrakesLights_FarbBlades", self.text)
        self.assertNotIn("CarSetup_Chassis_Rear_RarbBlades", self.text)
        self.assertNotIn("CarSetup_Chassis_FrontBrakes_ArbBlades", self.text)

    def test_porsche_paired_rear_toe(self):
        # Porsche uses paired Chassis.Rear.TotalToeIn
        self.assertIn("CarSetup_Chassis_Rear_TotalToeIn", self.text)
        # NOT per-wheel
        self.assertNotIn("CarSetup_Chassis_LeftRear_ToeIn", self.text)
        self.assertNotIn("CarSetup_Chassis_RightRear_ToeIn", self.text)

    def test_porsche_fuel_in_front_brakes_lights(self):
        # Porsche puts fuel in front
        self.assertIn("CarSetup_Chassis_FrontBrakesLights_FuelLevel", self.text)
        # NOT in Rear (BMW/Aston)
        self.assertNotIn("CarSetup_Chassis_Rear_FuelLevel", self.text)
        # NOT in Systems (GTP)
        self.assertNotIn("CarSetup_Systems_Fuel_FuelLevel", self.text)

    def test_porsche_tc_label_uses_tc_lat_suffix(self):
        match_lines = [l for l in self.text.splitlines() if "TcSetting" in l]
        self.assertTrue(match_lines, "TcSetting not emitted")
        self.assertTrue(
            any("TC-LAT" in l for l in match_lines),
            f"Porsche TC label should be 'TC-LAT', got: {match_lines!r}",
        )

    def test_porsche_emits_throttle_shape_and_dash_display(self):
        self.assertIn("CarSetup_Chassis_InCarAdjustments_ThrottleShapeSetting", self.text)
        self.assertIn("CarSetup_Chassis_InCarAdjustments_DashDisplayPage", self.text)

    def test_porsche_does_not_emit_aston_only_fields(self):
        self.assertNotIn("EpasSetting", self.text)
        self.assertNotIn("ThrottleResponse", self.text)
        self.assertNotIn("EnduranceLights", self.text)

    def test_porsche_emits_no_gtp_only_fields(self):
        forbidden = [
            "HeaveSpring", "ThirdSpring", "TorsionBarOD", "TorsionBarTurns",
            "Systems_Fuel_FuelLevel", "BrakesDriveUnit_BrakeSpec",
        ]
        for tag in forbidden:
            self.assertNotIn(tag, self.text, f"GTP-only tag {tag!r} leaked into Porsche .sto")

    def test_porsche_emits_per_axle_dampers(self):
        # 8 channels (per-axle), not 16 (per-corner)
        for chan in [
            "CarSetup_Dampers_FrontDampers_LowSpeedCompressionDamping",
            "CarSetup_Dampers_FrontDampers_HighSpeedCompressionDamping",
            "CarSetup_Dampers_FrontDampers_LowSpeedReboundDamping",
            "CarSetup_Dampers_FrontDampers_HighSpeedReboundDamping",
            "CarSetup_Dampers_RearDampers_LowSpeedCompressionDamping",
            "CarSetup_Dampers_RearDampers_HighSpeedCompressionDamping",
            "CarSetup_Dampers_RearDampers_LowSpeedReboundDamping",
            "CarSetup_Dampers_RearDampers_HighSpeedReboundDamping",
        ]:
            self.assertIn(chan, self.text, f"Missing per-axle damper channel {chan!r}")
        # And no per-corner damper IDs
        self.assertNotIn("Chassis_LeftFront_LsCompDamping", self.text)


# ─── BMW M4 GT3 regression (W4.1 baseline) ──────────────────────────────────

class GT3BMWRegressionTests(unittest.TestCase):
    """W4.1 BMW M4 GT3 must continue to work after W4.2 dispatch was added."""

    def setUp(self):
        self.text, self.root = _write_and_parse("bmw_m4_gt3")

    def test_bmw_uses_arb_blades_under_front_brakes(self):
        self.assertIn("CarSetup_Chassis_FrontBrakes_ArbBlades", self.text)
        # NOT FrontBrakesLights (Aston/Porsche)
        self.assertNotIn("CarSetup_Chassis_FrontBrakesLights_FarbBlades", self.text)
        self.assertNotIn("CarSetup_Chassis_FrontBrakesLights_ArbSetting", self.text)

    def test_bmw_fuel_in_chassis_rear(self):
        self.assertIn("CarSetup_Chassis_Rear_FuelLevel", self.text)
        self.assertNotIn("CarSetup_Chassis_FrontBrakesLights_FuelLevel", self.text)

    def test_bmw_tc_label_plain_tc(self):
        match_lines = [l for l in self.text.splitlines() if "TcSetting" in l]
        self.assertTrue(match_lines)
        joined = "\n".join(match_lines)
        # BMW uses bare "(TC)" without TC SLIP / TC-LAT
        self.assertIn("(TC)", joined)
        self.assertNotIn("TC SLIP", joined)
        self.assertNotIn("TC-LAT", joined)


if __name__ == "__main__":
    unittest.main()
