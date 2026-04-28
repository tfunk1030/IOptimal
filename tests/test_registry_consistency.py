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
        bmw_exceptions = {
            "front_diff_preload_nm",  # Ferrari-only
            "rear_torsion_od_mm",     # Acura ORECA rear torsion-bar field
            "front_roll_ls",          # Acura/Porsche roll dampers
            "front_roll_hs",
            "front_roll_hs_slope",    # Porsche Multimatic front roll damper
            "rear_roll_ls",
            "rear_roll_hs",
            "rear_3rd_ls_comp",       # Porsche Multimatic rear 3rd dampers
            "rear_3rd_hs_comp",
            "rear_3rd_ls_rbd",
            "rear_3rd_hs_rbd",
            "front_roll_spring_nmm",  # Porsche Multimatic roll spring
            "front_roll_perch_mm",
            "front_arb_setting",      # Porsche Connected/Disconnected toggle
            "rear_spring_nmm",        # Alias for rear_spring_rate_nmm
        }
        missing = [f.canonical_key for f in settable if f.canonical_key not in bmw and f.canonical_key not in bmw_exceptions]
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

    def test_bmw_brake_target_and_migration_use_whole_step_resolution(self):
        target_spec = get_car_spec("bmw", "brake_bias_target")
        migration_spec = get_car_spec("bmw", "brake_bias_migration")
        self.assertIsNotNone(target_spec)
        self.assertIsNotNone(migration_spec)
        self.assertEqual(target_spec.resolution, 1.0)
        self.assertEqual(migration_spec.resolution, 1.0)

    def test_porsche_has_minimal_but_valid_specs(self):
        porsche = CAR_FIELD_SPECS["porsche"]
        self.assertGreaterEqual(len(porsche), 15)
        # Must have core params
        for key in ("wing_angle_deg", "front_pushrod_offset_mm", "brake_bias_pct", "fuel_l"):
            self.assertIn(key, porsche)

    def test_cadillac_inherits_bmw_heave_with_arb_override(self):
        spec = get_car_spec("cadillac", "front_arb_blade")
        self.assertIn("[0]", spec.sto_param_id)
        bmw_heave = get_car_spec("bmw", "front_heave_spring_nmm")
        cadillac_heave = get_car_spec("cadillac", "front_heave_spring_nmm")
        self.assertEqual(bmw_heave.sto_param_id, cadillac_heave.sto_param_id)

    def test_acura_has_oreca_specific_overrides(self):
        arb_spec = get_car_spec("acura", "front_arb_blade")
        self.assertIn("[0]", arb_spec.sto_param_id)

        rear_torsion_spec = get_car_spec("acura", "rear_torsion_od_mm")
        self.assertIn("TorsionBarOD", rear_torsion_spec.sto_param_id)

        front_roll_ls = get_car_spec("acura", "front_roll_ls")
        rear_roll_hs = get_car_spec("acura", "rear_roll_hs")
        self.assertIn("FrontRoll", front_roll_ls.sto_param_id)
        self.assertIn("RearRoll", rear_roll_hs.sto_param_id)

        diff_spec = get_car_spec("acura", "diff_ramp_angles")
        self.assertIn("DiffRampAngles", diff_spec.sto_param_id)

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

    # ─── GT3 W1.3 routing regression guards ──────────────────────────────

    def test_car_name_gt3_does_not_collapse_to_gtp(self):
        """Pin W1.3 fix: ``_car_name`` MUST NOT silently map GT3 inputs to GTP.

        The pre-W1.3 substring loop ran ``("bmw", "ferrari", "cadillac",
        "porsche", "acura")`` in that order and returned on first hit, so
        ``_car_name("bmw_m4_gt3")`` returned ``"bmw"``.  That collapsed every
        GT3 caller into the GTP BMW spec set.
        """
        from car_model.setup_registry import _car_name
        self.assertEqual(_car_name("bmw_m4_gt3"), "bmw_m4_gt3")
        self.assertEqual(_car_name("aston_martin_vantage_gt3"), "aston_martin_vantage_gt3")
        self.assertEqual(_car_name("porsche_992_gt3r"), "porsche_992_gt3r")
        # And the GTP names still work.
        self.assertEqual(_car_name("bmw"), "bmw")
        self.assertEqual(_car_name("porsche"), "porsche")

    def test_car_name_none_returns_bmw_default_with_todo(self):
        """W1.3: ``_car_name(None)`` keeps the legacy BMW silent default.

        Principle 8 (no silent fallbacks) ideally wants a raise, but there
        is one real None caller today:
        ``solver/bmw_rotation_search.py:665`` calls ``_extract_target_maps``
        without a ``car`` argument, which propagates None to
        ``public_output_value`` to ``_car_name``.  That path is BMW-only
        (gated by ``_is_bmw_sebring``), so the silent default is
        contextually correct.  Wave-1 follow-up tracked in code comment.
        """
        from car_model.setup_registry import _car_name
        self.assertEqual(_car_name(None), "bmw")

    def test_get_car_spec_gt3_returns_none_not_bmw_spec(self):
        """W1.3: GT3 spec dicts are intentional empty stubs (Wave 4 will populate).

        ``get_car_spec("bmw_m4_gt3", ...)`` must return None — NOT silently
        return the BMW spec — so that downstream writers fail loudly when
        they hit a GT3 field they have no STO param ID for.
        """
        for car in ("bmw_m4_gt3", "aston_martin_vantage_gt3", "porsche_992_gt3r"):
            self.assertIn(car, CAR_FIELD_SPECS, f"{car} missing from CAR_FIELD_SPECS")
            self.assertEqual(
                CAR_FIELD_SPECS[car], {},
                f"{car} spec dict should be empty stub (populated in Wave 4)",
            )
            self.assertIsNone(
                get_car_spec(car, "front_heave_nmm"),
                f"get_car_spec({car!r}, 'front_heave_nmm') must NOT silently "
                "fall back to BMW spec",
            )

    def test_writer_param_ids_cover_ferrari_native_map(self):
        """Verify Ferrari registry uses Ferrari-native Systems and Dampers IDs."""
        ferrari_specs = CAR_FIELD_SPECS["ferrari"]
        ferrari_sto_ids = {spec.sto_param_id for spec in ferrari_specs.values() if spec.sto_param_id}

        critical_ids = [
            "CarSetup_Chassis_Front_HeaveSpring",
            "CarSetup_Chassis_Rear_HeaveSpring",
            "CarSetup_Chassis_LeftFront_TorsionBarOD",
            "CarSetup_Chassis_LeftRear_TorsionBarOD",
            "CarSetup_Dampers_LeftFrontDamper_LsCompDamping",
            "CarSetup_Dampers_LeftRearDamper_HsRbdDamping",
            "CarSetup_Systems_BrakeSpec_BrakePressureBias",
            "CarSetup_Systems_BrakeSpec_BiasMigrationGain",
            "CarSetup_Systems_FrontDiffSpec_Preload",
            "CarSetup_Systems_RearDiffSpec_CoastDriveRampOptions",
            "CarSetup_Systems_GearRatios_SpeedInFirst",
            "CarSetup_Systems_HybridConfig_HybridRearDriveEnabled",
            "CarSetup_Systems_Lighting_RoofIdLightColor",
        ]
        legacy_ids = [
            "CarSetup_BrakesDriveUnit_BrakeSpec_BrakePressureBias",
            "CarSetup_BrakesDriveUnit_RearDiffSpec_Preload",
            "CarSetup_BrakesDriveUnit_TractionControl_TractionControlGain",
            "CarSetup_Chassis_LeftFront_LsCompDamping",
        ]
        for sto_id in critical_ids:
            self.assertIn(sto_id, ferrari_sto_ids, f"Ferrari registry missing STO ID: {sto_id}")
        for sto_id in legacy_ids:
            self.assertNotIn(sto_id, ferrari_sto_ids, f"Ferrari registry should not use legacy STO ID: {sto_id}")


if __name__ == "__main__":
    unittest.main()
