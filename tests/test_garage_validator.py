import unittest
from types import SimpleNamespace

from car_model.cars import get_car
from car_model.garage import GarageSetupState
from output.garage_validator import validate_and_fix_garage_correlation
from solver.legality_engine import validate_solution_legality


class GarageValidatorTests(unittest.TestCase):
    def test_bmw_validator_moves_edge_case_off_torsion_limit(self) -> None:
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

        self.assertLess(step2.perch_offset_front_mm, -7.0)
        self.assertEqual(step2.perch_offset_front_mm, -7.5)
        self.assertTrue(any("torsion bar defl" in warning for warning in warnings))

        garage_model = car.active_garage_output_model("Sebring International Raceway")
        state = GarageSetupState.from_solver_steps(
            step1=step1,
            step2=step2,
            step3=step3,
            step5=step5,
            fuel_l=8.0,
        )
        constraint = garage_model.validate(
            state,
            front_excursion_p99_mm=step2.front_excursion_at_rate_mm,
        )
        self.assertTrue(constraint.valid)
        self.assertTrue(getattr(step2, "garage_constraints_ok", False))

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

    def test_ferrari_legality_validation_does_not_mutate_internal_solver_units(self) -> None:
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

        self.assertTrue(validation.valid)
        self.assertEqual(validation.warnings, [])
        self.assertEqual(step2.front_heave_nmm, 190.0)
        self.assertEqual(step2.rear_third_nmm, 950.0)
        self.assertEqual(step3.front_torsion_od_mm, 20.0)
        self.assertEqual(step3.rear_spring_rate_nmm, 475.0)
        self.assertEqual(step3.rear_spring_perch_mm, 30.0)


if __name__ == "__main__":
    unittest.main()
