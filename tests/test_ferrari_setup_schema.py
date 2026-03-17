import unittest
from pathlib import Path

from analyzer.extract import extract_measurements
from analyzer.setup_reader import CurrentSetup
from analyzer.setup_schema import apply_live_control_overrides, build_setup_schema
from car_model.cars import get_car
from track_model.ibt_parser import IBTFile


class FerrariSetupSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cls.ibt_path = repo_root / "ibtfiles" / "ferrari499p_sebring international 2026-03-09 18-07-31.ibt"
        cls.car = get_car("ferrari")
        ibt = IBTFile(str(cls.ibt_path))
        cls.current_setup = CurrentSetup.from_ibt(ibt)
        cls.measured = extract_measurements(str(cls.ibt_path), cls.car)
        cls.live_override_notes = apply_live_control_overrides(cls.current_setup, cls.measured)
        cls.schema = build_setup_schema(
            car=cls.car,
            ibt_path=str(cls.ibt_path),
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

    def test_schema_covers_required_ferrari_ids(self) -> None:
        required_ids = [
            "CarSetup_Systems_BrakeSpec_BiasMigration",
            "CarSetup_Systems_BrakeSpec_BiasMigrationGain",
            "CarSetup_Dampers_LeftFrontDamper_LsCompDamping",
            "CarSetup_Systems_FrontDiffSpec_Preload",
            "CarSetup_Systems_RearDiffSpec_CoastDriveRampOptions",
            "CarSetup_Systems_RearDiffSpec_Preload",
            "CarSetup_Systems_Fuel_FuelLowWarning",
            "CarSetup_Systems_GearRatios_GearStack",
            "CarSetup_Systems_HybridConfig_HybridRearDriveEnabled",
            "CarSetup_Systems_Lighting_RoofIdLightColor",
        ]
        for field_id in required_ids:
            self._field(field_id)

    def test_schema_classifies_settable_computed_and_context_fields(self) -> None:
        front_heave = self._field("CarSetup_Chassis_Front_HeaveSpring")
        heave_deflection = self._field("CarSetup_Chassis_Front_HeaveSpringDeflStatic")
        hot_pressure = self._field("CarSetup_TiresAero_LeftFront_LastHotPressure")

        self.assertEqual(front_heave.kind, "settable")
        self.assertEqual(front_heave.allowed_range, {"min": 0.0, "max": 8.0, "source": "car_model"})
        self.assertEqual(front_heave.resolution, 1.0)
        self.assertEqual(heave_deflection.kind, "computed")
        self.assertTrue(heave_deflection.formula_note)
        self.assertEqual(hot_pressure.kind, "context")

    def test_schema_uses_live_brake_bias_telemetry_when_stable(self) -> None:
        brake_bias = self._field("CarSetup_Systems_BrakeSpec_BrakePressureBias")

        self.assertIsNotNone(self.measured.live_brake_bias_pct)
        self.assertEqual(brake_bias.authoritative_source, "telemetry")
        self.assertEqual(brake_bias.telemetry_channel, "dcBrakeBias")
        self.assertEqual(brake_bias.telemetry_value, self.measured.live_brake_bias_pct)
        self.assertEqual(brake_bias.decoded_value, self.current_setup.brake_bias_pct)


if __name__ == "__main__":
    unittest.main()
