import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analyzer.setup_reader import CurrentSetup
from analyzer.setup_schema import apply_live_control_overrides, build_setup_schema
from car_model.cars import get_car
from solver.solve_chain import _decode_ferrari_indexed_setup


class FakeIBT:
    def __init__(self, session_info: dict):
        self.session_info = session_info


class FerrariSetupSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture_path = REPO_ROOT / "tests" / "fixtures" / "ferrari_hockenheim_screenshot_setup.json"
        cls.fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        cls.car = get_car("ferrari")
        cls.current_setup = CurrentSetup.from_ibt(FakeIBT(cls.fixture))
        cls.measured = SimpleNamespace(
            live_brake_bias_pct=53.0,
            live_tc_gain=3,
            live_tc_slip=4,
            live_front_arb_blade=1,
            live_rear_arb_blade=3,
        )
        cls.live_override_notes = apply_live_control_overrides(cls.current_setup, cls.measured)
        cls.schema = build_setup_schema(
            car=cls.car,
            current_setup=cls.current_setup,
            measured=cls.measured,
        )
        cls.fields_by_id = {
            field.ldx_id: field
            for field in cls.schema.fields
            if field.ldx_id is not None
        }

    def _field(self, field_id: str):
        self.assertIn(field_id, self.fields_by_id)
        return self.fields_by_id[field_id]

    def test_fixture_parses_visible_ferrari_values(self) -> None:
        setup = self.current_setup

        expected_numeric = {
            "front_heave_nmm": 3.0,
            "front_heave_perch_mm": -16.5,
            "rear_third_nmm": 5.0,
            "rear_third_perch_mm": -112.5,
            "front_pushrod_mm": 1.0,
            "rear_pushrod_mm": 5.0,
            "front_torsion_od_mm": 2.0,
            "rear_spring_nmm": 2.0,
            "rear_torsion_od_mm": 2.0,
            "front_toe_mm": -0.7,
            "rear_toe_mm": 0.3,
            "front_camber_deg": -2.8,
            "rear_camber_deg": -1.9,
            "front_ls_comp": 20,
            "front_hs_comp": 20,
            "front_hs_slope": 11,
            "front_ls_rbd": 24,
            "front_hs_rbd": 28,
            "rear_ls_comp": 16,
            "rear_hs_comp": 32,
            "rear_hs_slope": 11,
            "rear_ls_rbd": 34,
            "rear_hs_rbd": 35,
            "brake_bias_pct": 53.0,
            "brake_bias_migration": 6.0,
            "brake_bias_migration_gain": -1.0,
            "front_master_cyl_mm": 19.1,
            "rear_master_cyl_mm": 19.1,
            "front_diff_preload_nm": -50.0,
            "diff_preload_nm": 25.0,
            "diff_clutch_plates": 4,
            "tc_gain": 3,
            "tc_slip": 4,
            "fuel_l": 58.0,
            "fuel_target_l": 2.8,
            "fuel_low_warning_l": 10.0,
            "hybrid_rear_drive_corner_pct": 90.0,
            "speed_in_first_kph": 121.7,
            "speed_in_second_kph": 157.5,
            "speed_in_third_kph": 190.0,
            "speed_in_fourth_kph": 222.7,
            "speed_in_fifth_kph": 256.6,
            "speed_in_sixth_kph": 291.0,
            "speed_in_seventh_kph": 329.2,
            "torsion_bar_turns": 0.089,
            "rear_torsion_bar_turns": 0.040,
            "torsion_bar_defl_mm": 12.1,
            "rear_torsion_bar_defl_mm": 8.9,
            "front_shock_defl_static_mm": 16.1,
            "front_shock_defl_max_mm": 100.0,
            "rear_shock_defl_static_mm": 17.4,
            "rear_shock_defl_max_mm": 150.0,
            "heave_spring_defl_static_mm": 10.9,
            "heave_spring_defl_max_mm": 80.3,
            "heave_slider_defl_static_mm": 42.0,
            "heave_slider_defl_max_mm": 200.0,
            "third_spring_defl_static_mm": 13.4,
            "third_spring_defl_max_mm": 67.0,
            "third_slider_defl_static_mm": 23.1,
            "third_slider_defl_max_mm": 300.0,
            "lf_corner_weight_n": 2669.0,
            "rf_corner_weight_n": 2669.0,
            "lr_corner_weight_n": 2938.0,
            "rr_corner_weight_n": 2938.0,
            "static_front_rh_mm": 30.1,
            "static_rear_rh_mm": 44.1,
        }
        for attr, expected in expected_numeric.items():
            self.assertAlmostEqual(getattr(setup, attr), expected, places=3, msg=attr)

        self.assertEqual(setup.front_arb_size, "A")
        self.assertEqual(setup.rear_arb_size, "C")
        self.assertEqual(setup.front_arb_blade, 1)
        self.assertEqual(setup.rear_arb_blade, 3)
        self.assertEqual(setup.pad_compound, "Medium")
        self.assertEqual(setup.diff_ramp_angles, "Less Locking")
        self.assertEqual(setup.gear_stack, "Short")
        self.assertEqual(setup.hybrid_rear_drive_enabled, "On")
        self.assertEqual(setup.roof_light_color, "Blue")
        self.assertEqual(
            setup.raw_indexed_fields,
            {
                "front_heave_index": 3.0,
                "rear_heave_index": 5.0,
                "front_torsion_bar_index": 2.0,
                "rear_torsion_bar_index": 2.0,
            },
        )

    def test_decode_ferrari_indexed_setup_converts_rear_torsion_index_to_physical_value(self) -> None:
        setup = copy.deepcopy(self.current_setup)
        self.assertLessEqual(setup.rear_spring_nmm, 18.0)
        setup.rear_spring_rate_nmm = setup.rear_spring_nmm

        _decode_ferrari_indexed_setup(self.car, setup)

        self.assertGreater(setup.rear_spring_nmm, 18.0)
        self.assertGreater(setup.rear_spring_rate_nmm, 18.0)
        self.assertIn(
            "Ferrari indexed springs/torsion bars are preserved as authoritative raw indices.",
            setup.decode_warnings,
        )
        self.assertIn(
            "Ferrari supporting outputs are sourced from Ferrari session values and Ferrari-only registry paths.",
            setup.decode_warnings,
        )

    def test_schema_covers_required_ferrari_ids(self) -> None:
        required_ids = [
            "CarSetup_Chassis_Front_HeaveSpring",
            "CarSetup_Chassis_Rear_HeaveSpring",
            "CarSetup_Chassis_LeftFront_TorsionBarOD",
            "CarSetup_Chassis_LeftRear_TorsionBarOD",
            "CarSetup_Dampers_LeftFrontDamper_LsCompDamping",
            "CarSetup_Dampers_LeftRearDamper_HsRbdDamping",
            "CarSetup_Systems_BrakeSpec_BrakePressureBias",
            "CarSetup_Systems_BrakeSpec_BiasMigration",
            "CarSetup_Systems_BrakeSpec_BiasMigrationGain",
            "CarSetup_Systems_FrontDiffSpec_Preload",
            "CarSetup_Systems_RearDiffSpec_CoastDriveRampOptions",
            "CarSetup_Systems_RearDiffSpec_Preload",
            "CarSetup_Systems_Fuel_FuelTarget",
            "CarSetup_Systems_GearRatios_SpeedInFirst",
            "CarSetup_Systems_HybridConfig_HybridRearDriveEnabled",
            "CarSetup_Systems_Lighting_RoofIdLightColor",
        ]
        for field_id in required_ids:
            self._field(field_id)

    def test_schema_uses_ferrari_public_aliases_and_raw_units(self) -> None:
        front_heave = self._field("CarSetup_Chassis_Front_HeaveSpring")
        rear_heave = self._field("CarSetup_Chassis_Rear_HeaveSpring")
        front_torsion = self._field("CarSetup_Chassis_LeftFront_TorsionBarOD")
        rear_torsion = self._field("CarSetup_Chassis_LeftRear_TorsionBarOD")
        rear_diff = self._field("CarSetup_Systems_RearDiffSpec_CoastDriveRampOptions")
        first_gear = self._field("CarSetup_Systems_GearRatios_SpeedInFirst")
        roof_light = self._field("CarSetup_Systems_Lighting_RoofIdLightColor")

        self.assertEqual(front_heave.canonical_key, "front_heave_index")
        self.assertEqual(front_heave.raw_unit, "idx")
        self.assertEqual(front_heave.decoded_unit, "idx")
        self.assertEqual(front_heave.allowed_range, {"min": 0.0, "max": 8.0, "source": "car_model"})
        self.assertEqual(front_heave.resolution, 1.0)

        self.assertEqual(rear_heave.canonical_key, "rear_heave_index")
        self.assertEqual(rear_heave.raw_unit, "idx")
        self.assertEqual(rear_heave.decoded_unit, "idx")
        self.assertEqual(rear_heave.allowed_range, {"min": 0.0, "max": 9.0, "source": "car_model"})
        self.assertEqual(rear_heave.resolution, 1.0)

        self.assertEqual(front_torsion.canonical_key, "front_torsion_bar_index")
        self.assertEqual(front_torsion.raw_unit, "idx")
        self.assertEqual(front_torsion.decoded_unit, "idx")
        self.assertEqual(front_torsion.allowed_range, {"min": 0.0, "max": 18.0, "source": "car_model"})
        self.assertEqual(front_torsion.resolution, 1.0)

        self.assertEqual(rear_torsion.canonical_key, "rear_torsion_bar_index")
        self.assertEqual(rear_torsion.raw_unit, "idx")
        self.assertEqual(rear_torsion.decoded_unit, "idx")
        self.assertEqual(rear_torsion.allowed_range, {"min": 0.0, "max": 18.0, "source": "car_model"})
        self.assertEqual(rear_torsion.resolution, 1.0)

        self.assertEqual(rear_diff.canonical_key, "rear_diff_ramp_label")
        self.assertEqual(rear_diff.raw_value, "Less Locking")
        self.assertEqual(rear_diff.decoded_value, "Less Locking")

        self.assertEqual(first_gear.canonical_key, "speed_in_first_kph")
        self.assertEqual(first_gear.raw_unit, "km/h")
        self.assertAlmostEqual(first_gear.raw_value, 121.7)

        self.assertEqual(roof_light.kind, "context")
        self.assertEqual(roof_light.raw_value, "Blue")

    def test_schema_uses_live_telemetry_for_supported_controls(self) -> None:
        brake_bias = self._field("CarSetup_Systems_BrakeSpec_BrakePressureBias")
        tc_gain = self._field("CarSetup_Systems_TractionControl_TractionControlGain")
        tc_slip = self._field("CarSetup_Systems_TractionControl_TractionControlSlip")
        front_arb = self._field("CarSetup_Chassis_Front_ArbBlades[0]")
        rear_arb = self._field("CarSetup_Chassis_Rear_ArbBlades[0]")

        self.assertEqual(brake_bias.authoritative_source, "telemetry")
        self.assertEqual(brake_bias.telemetry_channel, "dcBrakeBias")
        self.assertEqual(brake_bias.telemetry_value, 53.0)
        self.assertEqual(brake_bias.decoded_value, 53.0)

        self.assertEqual(tc_gain.authoritative_source, "telemetry")
        self.assertEqual(tc_gain.telemetry_channel, "dcTractionControl2")
        self.assertEqual(tc_gain.telemetry_value, 3)

        self.assertEqual(tc_slip.authoritative_source, "telemetry")
        self.assertEqual(tc_slip.telemetry_channel, "dcTractionControl")
        self.assertEqual(tc_slip.telemetry_value, 4)

        self.assertEqual(front_arb.authoritative_source, "telemetry")
        self.assertEqual(front_arb.telemetry_channel, "dcAntiRollFront")
        self.assertEqual(front_arb.telemetry_value, 1)

        self.assertEqual(rear_arb.authoritative_source, "telemetry")
        self.assertEqual(rear_arb.telemetry_channel, "dcAntiRollRear")
        self.assertEqual(rear_arb.telemetry_value, 3)


if __name__ == "__main__":
    unittest.main()
