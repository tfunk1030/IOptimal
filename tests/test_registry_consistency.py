"""Tests that the setup registry is internally consistent and covers all old maps."""

import unittest

from car_model.setup_registry import (
    CAR_FIELD_SPECS,
    FIELD_REGISTRY,
    get_car_spec,
    get_field,
    iter_fields,
    validate_registry,
)


class RegistryConsistencyTests(unittest.TestCase):
    def test_validate_registry_reports_no_issues(self):
        issues = validate_registry()
        self.assertEqual(issues, [], f"Registry validation issues: {issues}")

    def test_field_registry_has_expected_count(self):
        self.assertGreaterEqual(len(FIELD_REGISTRY), 80)

    def test_all_cars_have_specs(self):
        for car in ("bmw", "ferrari", "porsche", "cadillac", "acura"):
            self.assertIn(car, CAR_FIELD_SPECS)

    def test_bmw_covers_all_settable_fields(self):
        settable = iter_fields(kind="settable")
        bmw = CAR_FIELD_SPECS["bmw"]
        missing = [f.canonical_key for f in settable if f.canonical_key not in bmw
                   and f.canonical_key != "front_diff_preload_nm"]  # Ferrari-only
        self.assertEqual(missing, [], f"BMW missing settable fields: {missing}")

    def test_get_field_returns_correct_type(self):
        f = get_field("front_heave_spring_nmm")
        self.assertIsNotNone(f)
        self.assertEqual(f.kind, "settable")
        self.assertEqual(f.solver_step, 2)
        self.assertEqual(f.unit, "N/mm")

    def test_get_car_spec_returns_correct_sto_id(self):
        spec = get_car_spec("bmw", "front_pushrod_offset_mm")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.sto_param_id, "CarSetup_Chassis_Front_PushrodLengthOffset")

        spec_f = get_car_spec("ferrari", "front_pushrod_offset_mm")
        self.assertIsNotNone(spec_f)
        self.assertEqual(spec_f.sto_param_id, "CarSetup_Chassis_Front_PushrodLengthDelta")

    def test_ferrari_has_front_diff(self):
        spec = get_car_spec("ferrari", "front_diff_preload_nm")
        self.assertIsNotNone(spec)
        self.assertIn("FrontDiffSpec", spec.sto_param_id)

    def test_porsche_has_minimal_but_valid_specs(self):
        porsche = CAR_FIELD_SPECS["porsche"]
        self.assertGreaterEqual(len(porsche), 15)
        # Must have core params
        for key in ("wing_angle_deg", "front_pushrod_offset_mm", "brake_bias_pct", "fuel_l"):
            self.assertIn(key, porsche)

    def test_cadillac_and_acura_inherit_bmw_with_arb_override(self):
        for car in ("cadillac", "acura"):
            spec = get_car_spec(car, "front_arb_blade")
            self.assertIn("[0]", spec.sto_param_id)
            # But heave should match BMW
            bmw_heave = get_car_spec("bmw", "front_heave_spring_nmm")
            car_heave = get_car_spec(car, "front_heave_spring_nmm")
            self.assertEqual(bmw_heave.sto_param_id, car_heave.sto_param_id)

    def test_iter_fields_filters_correctly(self):
        step1 = iter_fields(solver_step=1)
        self.assertTrue(all(f.solver_step == 1 for f in step1))
        self.assertGreaterEqual(len(step1), 3)

        settable = iter_fields(kind="settable")
        self.assertTrue(all(f.kind == "settable" for f in settable))

    def test_solver_steps_cover_all_six_steps(self):
        for step in range(1, 7):
            fields = iter_fields(solver_step=step)
            self.assertGreater(len(fields), 0, f"No fields for solver step {step}")

    def test_writer_param_ids_cover_old_bmw_map(self):
        """Verify registry covers the key BMW STO param IDs from setup_writer.py."""
        from output.setup_writer import _BMW_PARAM_IDS
        bmw_specs = CAR_FIELD_SPECS["bmw"]
        bmw_sto_ids = {spec.sto_param_id for spec in bmw_specs.values()}

        # Check that critical writer IDs are covered
        critical_ids = [
            "CarSetup_Chassis_Front_PushrodLengthOffset",
            "CarSetup_Chassis_Front_HeaveSpring",
            "CarSetup_Chassis_LeftFront_TorsionBarOD",
            "CarSetup_Chassis_LeftRear_SpringRate",
            "CarSetup_Chassis_Front_ArbSize",
            "CarSetup_Chassis_LeftFront_LsCompDamping",
            "CarSetup_BrakesDriveUnit_BrakeSpec_BrakePressureBias",
            "CarSetup_BrakesDriveUnit_RearDiffSpec_Preload",
            "CarSetup_BrakesDriveUnit_TractionControl_TractionControlGain",
        ]
        for sto_id in critical_ids:
            self.assertIn(sto_id, bmw_sto_ids, f"BMW registry missing STO ID: {sto_id}")


if __name__ == "__main__":
    unittest.main()
