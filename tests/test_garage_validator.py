import unittest
from types import SimpleNamespace

from car_model.cars import get_car
from output.garage_validator import (
    _clamp_step3,
    validate_and_fix_garage_correlation,
)
from solver.legality_engine import validate_solution_legality


class GarageValidatorTests(unittest.TestCase):
    def test_bmw_validator_reconciles_ride_heights(self) -> None:
        """Validator reconciles ride heights when garage model disagrees with solver.

        With low fuel (8 L) and these spring rates the calibrated torsion bar
        deflection model does not predict a constraint violation, so only RH
        reconciliation is expected."""
        car = get_car("bmw")
        step1 = SimpleNamespace(
            front_pushrod_offset_mm=-25.5,
            rear_pushrod_offset_mm=-23.0,
            static_front_rh_mm=30.2,
            static_rear_rh_mm=49.2,
            rake_static_mm=19.0,
        )
        step2 = SimpleNamespace(
            front_heave_nmm=50.0,
            rear_third_nmm=440.0,
            perch_offset_front_mm=-7.0,
            perch_offset_rear_mm=43.0,
            front_excursion_at_rate_mm=13.9,
        )
        step3 = SimpleNamespace(
            front_torsion_od_mm=13.9,
            rear_spring_rate_nmm=150.0,
            rear_spring_perch_mm=30.0,
        )
        step5 = SimpleNamespace(
            front_camber_deg=-2.1,
            rear_camber_deg=-1.8,
            front_toe_mm=-0.4,
            rear_toe_mm=0.3,
        )

        warnings = validate_and_fix_garage_correlation(
            car=car,
            step1=step1,
            step2=step2,
            step3=step3,
            step5=step5,
            fuel_l=8.0,
            track_name="Sebring International Raceway",
        )

        # No torsion bar constraint violation at low fuel — bar defl is below limit.
        self.assertFalse(any("torsion bar defl" in w for w in warnings))
        # Perch should be unchanged since no torsion bar fix was triggered.
        self.assertEqual(step2.perch_offset_front_mm, -7.0)
        # Garage constraints should be populated and valid after validation.
        self.assertTrue(hasattr(step2, "garage_constraints_ok"))
        self.assertTrue(step2.garage_constraints_ok)

    def test_bmw_validator_guards_soft_front_bar_full_race_edge_case(self) -> None:
        car = get_car("bmw")
        step1 = SimpleNamespace(
            front_pushrod_offset_mm=-26.0,
            rear_pushrod_offset_mm=-20.5,
            static_front_rh_mm=30.0,
            static_rear_rh_mm=49.3,
            rake_static_mm=19.3,
        )
        step2 = SimpleNamespace(
            front_heave_nmm=50.0,
            rear_third_nmm=440.0,
            perch_offset_front_mm=-7.5,
            perch_offset_rear_mm=44.0,
            front_excursion_at_rate_mm=14.0,
        )
        step3 = SimpleNamespace(
            front_torsion_od_mm=13.9,
            rear_spring_rate_nmm=160.0,
            rear_spring_perch_mm=30.0,
        )
        step5 = SimpleNamespace(
            front_camber_deg=-2.6,
            rear_camber_deg=-1.8,
            front_toe_mm=-0.6,
            rear_toe_mm=0.2,
        )

        warnings = validate_and_fix_garage_correlation(
            car=car,
            step1=step1,
            step2=step2,
            step3=step3,
            step5=step5,
            fuel_l=58.0,
            track_name="Sebring International Raceway",
        )

        self.assertGreater(step3.front_torsion_od_mm, 13.9)
        self.assertTrue(any("soft-front-bar guard" in warning for warning in warnings))

    def test_ferrari_legality_requires_active_garage_output_model(self) -> None:
        """Ferrari now has an active garage model (applies to all tracks), so
        validate_solution_legality returns ``validation_tier="full"`` and does
        NOT hard-veto.  The original step values must be left unchanged because
        the legality engine operates on deep copies."""
        car = get_car("ferrari")
        step1 = SimpleNamespace(
            front_pushrod_offset_mm=1.0,
            rear_pushrod_offset_mm=5.0,
            static_front_rh_mm=30.1,
            static_rear_rh_mm=44.1,
            rake_static_mm=14.0,
            vortex_burst_margin_mm=12.0,
        )
        step2 = SimpleNamespace(
            front_heave_nmm=190.0,
            rear_third_nmm=950.0,
            perch_offset_front_mm=-10.5,
            perch_offset_rear_mm=-104.0,
            front_excursion_at_rate_mm=3.0,
            front_bottoming_margin_mm=17.0,
        )
        step3 = SimpleNamespace(
            front_torsion_od_mm=20.0,
            rear_spring_rate_nmm=475.0,
            rear_spring_perch_mm=30.0,
        )
        step5 = SimpleNamespace(
            front_camber_deg=-1.2,
            rear_camber_deg=-1.1,
            front_toe_mm=-2.2,
            rear_toe_mm=0.3,
        )

        validation = validate_solution_legality(
            car=car,
            track_name="Hockenheimring Baden-Württemberg",
            step1=step1,
            step2=step2,
            step3=step3,
            step5=step5,
            fuel_l=58.0,
        )

        # Ferrari now has an active garage model — expect full validation, not a hard veto.
        self.assertEqual(validation.validation_tier, "full")
        self.assertFalse(validation.hard_veto)
        # Original physical values must be left unchanged (legality engine uses deep copies).
        self.assertEqual(step2.front_heave_nmm, 190.0)
        self.assertEqual(step2.rear_third_nmm, 950.0)
        self.assertEqual(step3.front_torsion_od_mm, 20.0)
        self.assertEqual(step3.rear_spring_rate_nmm, 475.0)
        self.assertEqual(step3.rear_spring_perch_mm, 30.0)

    def test_ferrari_validate_uses_physical_units_not_index_space(self) -> None:
        """Phase 2 garage-model validation must use physical units after Ferrari write-back.

        Before the fix, local step2/step3 references still pointed at the index-space
        deep copies after the Phase 1 write-back, so GarageSetupState received indices
        (e.g. heave=3 instead of 90 N/mm) and the garage model produced nonsensical
        predictions.  After the fix, step2/step3 are reassigned to the physical originals
        before any GarageSetupState is built in Phase 2.

        Concretely: calling validate_and_fix_garage_correlation with a Ferrari setup
        must NOT alter the physical step objects to index-space values.
        """
        car = get_car("ferrari")
        step1 = SimpleNamespace(
            front_pushrod_offset_mm=1.0,
            rear_pushrod_offset_mm=5.0,
            static_front_rh_mm=30.1,
            static_rear_rh_mm=44.1,
            rake_static_mm=14.0,
        )
        step2 = SimpleNamespace(
            front_heave_nmm=90.0,    # physical N/mm (index=3)
            rear_third_nmm=590.0,    # physical N/mm (index=3)
            perch_offset_front_mm=-10.5,
            perch_offset_rear_mm=-104.0,
            front_excursion_at_rate_mm=3.0,
        )
        step3 = SimpleNamespace(
            front_torsion_od_mm=20.667,   # physical OD mm (index=3)
            rear_spring_rate_nmm=401.7,   # physical N/mm (index=3)
            rear_spring_perch_mm=0.0,
        )
        step5 = SimpleNamespace(
            front_camber_deg=-1.2,
            rear_camber_deg=-1.1,
            front_toe_mm=-2.2,
            rear_toe_mm=0.3,
        )

        warnings = validate_and_fix_garage_correlation(
            car=car,
            step1=step1,
            step2=step2,
            step3=step3,
            step5=step5,
            fuel_l=58.0,
            track_name="Hockenheimring Baden-Württemberg",
        )

        # After Phase 1 write-back, step2/step3 must retain physical units.
        # If Phase 2 used index-space objects it would corrupt these to small numbers.
        self.assertGreater(step2.front_heave_nmm, 50.0,
            "front_heave_nmm must remain physical N/mm, not an index (~3)")
        self.assertGreater(step2.rear_third_nmm, 100.0,
            "rear_third_nmm must remain physical N/mm, not an index (~3)")
        self.assertGreater(step3.front_torsion_od_mm, 15.0,
            "front_torsion_od_mm must remain physical OD mm, not an index (~3)")
        self.assertGreater(step3.rear_spring_rate_nmm, 50.0,
            "rear_spring_rate_nmm must remain physical N/mm, not an index (~3)")


        """When a car has no active garage model the legality gate hard-vetos."""
        # BMW does not have a garage model for Hockenheim.
        car = get_car("bmw")
        step1 = SimpleNamespace(
            front_pushrod_offset_mm=-25.5,
            rear_pushrod_offset_mm=-23.0,
            static_front_rh_mm=30.2,
            static_rear_rh_mm=49.2,
            rake_static_mm=19.0,
            vortex_burst_margin_mm=5.0,
        )
        step2 = SimpleNamespace(
            front_heave_nmm=50.0,
            rear_third_nmm=440.0,
            perch_offset_front_mm=-7.0,
            perch_offset_rear_mm=43.0,
            front_excursion_at_rate_mm=13.9,
            front_bottoming_margin_mm=17.0,
        )
        step3 = SimpleNamespace(
            front_torsion_od_mm=13.9,
            rear_spring_rate_nmm=150.0,
            rear_spring_perch_mm=30.0,
        )
        step5 = SimpleNamespace(
            front_camber_deg=-2.1,
            rear_camber_deg=-1.8,
            front_toe_mm=-0.4,
            rear_toe_mm=0.3,
        )

        validation = validate_solution_legality(
            car=car,
            track_name="Hockenheimring Baden-Württemberg",
            step1=step1,
            step2=step2,
            step3=step3,
            step5=step5,
            fuel_l=8.0,
        )

        self.assertFalse(validation.valid)
        self.assertEqual(validation.validation_tier, "none")
        self.assertTrue(validation.hard_veto)
        self.assertTrue(validation.messages)
        self.assertIn("garage output model", validation.messages[0])

    def test_validator_raises_when_step1_is_none(self) -> None:
        """Calibration-gate-blocked step must surface as a typed ValueError so
        callers cannot silently degrade validation coverage."""
        car = get_car("bmw")
        step2 = SimpleNamespace(
            front_heave_nmm=50.0, rear_third_nmm=440.0,
            perch_offset_front_mm=-7.0, perch_offset_rear_mm=43.0,
            front_excursion_at_rate_mm=13.9,
        )
        step3 = SimpleNamespace(
            front_torsion_od_mm=13.9, rear_spring_rate_nmm=150.0,
            rear_spring_perch_mm=30.0,
        )
        with self.assertRaisesRegex(ValueError, r"step1 is None"):
            validate_and_fix_garage_correlation(
                car=car, step1=None, step2=step2, step3=step3, step5=None,
                fuel_l=8.0, track_name="Sebring International Raceway",
            )

    def test_validator_raises_when_step2_is_none(self) -> None:
        car = get_car("bmw")
        step1 = SimpleNamespace(
            front_pushrod_offset_mm=-25.5, rear_pushrod_offset_mm=-23.0,
            static_front_rh_mm=30.2, static_rear_rh_mm=49.2, rake_static_mm=19.0,
        )
        step3 = SimpleNamespace(
            front_torsion_od_mm=13.9, rear_spring_rate_nmm=150.0,
            rear_spring_perch_mm=30.0,
        )
        with self.assertRaisesRegex(ValueError, r"step2 is None"):
            validate_and_fix_garage_correlation(
                car=car, step1=step1, step2=None, step3=step3, step5=None,
                fuel_l=8.0, track_name="Sebring International Raceway",
            )

    def test_validator_raises_when_step3_is_none(self) -> None:
        car = get_car("bmw")
        step1 = SimpleNamespace(
            front_pushrod_offset_mm=-25.5, rear_pushrod_offset_mm=-23.0,
            static_front_rh_mm=30.2, static_rear_rh_mm=49.2, rake_static_mm=19.0,
        )
        step2 = SimpleNamespace(
            front_heave_nmm=50.0, rear_third_nmm=440.0,
            perch_offset_front_mm=-7.0, perch_offset_rear_mm=43.0,
            front_excursion_at_rate_mm=13.9,
        )
        with self.assertRaisesRegex(ValueError, r"step3 is None"):
            validate_and_fix_garage_correlation(
                car=car, step1=step1, step2=step2, step3=None, step5=None,
                fuel_l=8.0, track_name="Sebring International Raceway",
            )

    def test_clamp_step3_ferrari_index_domain_does_not_snap_to_physical_discretes(self) -> None:
        """Ferrari range is in INDEX space (0-18) but the discrete OD list contains
        PHYSICAL OD values (19.99-23.99 mm).  When the validator's Phase 1 hands an
        index-domain value to _clamp_step3, the function must NOT snap it to the
        nearest physical OD — that would produce nonsense like 'index=3 -> 19.99 mm'.
        Expected behaviour: keep the value in index space (round to 2 decimals).
        """
        car = get_car("ferrari")
        gr = car.garage_ranges
        # Sanity-check the test fixture: domains differ as documented.
        self.assertEqual(gr.front_torsion_od_mm, (0.0, 18.0))
        self.assertTrue(gr.front_torsion_od_discrete)
        self.assertGreater(min(gr.front_torsion_od_discrete), gr.front_torsion_od_mm[1])

        step3 = SimpleNamespace(
            front_torsion_od_mm=3.0,         # index 3 (physical = 20.66 mm)
            rear_spring_rate_nmm=3.0,        # index 3
            rear_spring_perch_mm=0.0,
        )
        msgs = _clamp_step3(step3, gr)

        # The value must remain near 3 (index space), NOT snap to ~19.99 (the
        # nearest physical OD).
        self.assertAlmostEqual(step3.front_torsion_od_mm, 3.0, places=2,
            msg=f"Ferrari index 3 must stay in index space, got {step3.front_torsion_od_mm}")
        self.assertFalse(any("19.99" in m or "20.66" in m for m in msgs),
            f"Should not snap index to physical OD; messages: {msgs}")

    def test_clamp_step3_bmw_physical_domain_snaps_to_discretes(self) -> None:
        """BMW range and discrete options share the same physical domain (mm),
        so _clamp_step3 SHOULD snap to the nearest discrete OD."""
        car = get_car("bmw")
        gr = car.garage_ranges
        # Sanity: both range and discretes are in physical mm.
        self.assertTrue(gr.front_torsion_od_discrete)
        self.assertLessEqual(min(gr.front_torsion_od_discrete), gr.front_torsion_od_mm[1])

        # Pick a value between two discrete options
        nearest = min(gr.front_torsion_od_discrete, key=lambda x: abs(x - 14.5))
        step3 = SimpleNamespace(
            front_torsion_od_mm=14.5,
            rear_spring_rate_nmm=150.0,
            rear_spring_perch_mm=30.0,
        )
        _clamp_step3(step3, gr)
        self.assertEqual(step3.front_torsion_od_mm, nearest,
            msg="BMW physical OD must snap to nearest discrete option")


if __name__ == "__main__":
    unittest.main()
