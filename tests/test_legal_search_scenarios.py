import unittest
from types import SimpleNamespace
from unittest.mock import patch

from car_model.cars import get_car
from solver.legal_search import run_legal_search
from solver.legal_space import LegalCandidate
from solver.objective import CandidateEvaluation, ObjectiveBreakdown


def _eval(family: str, score: float, front_heave: float) -> CandidateEvaluation:
    return CandidateEvaluation(
        params={"front_heave_spring_nmm": front_heave},
        family=family,
        breakdown=ObjectiveBreakdown(lap_gain_ms=score),
    )


def _prediction(**overrides):
    values = {
        "front_heave_travel_used_pct": 95.0,
        "front_excursion_mm": 17.0,
        "rear_rh_std_mm": 8.2,
        "braking_pitch_deg": 1.30,
        "front_lock_p95": 0.09,
        "rear_power_slip_p95": 0.09,
        "body_slip_p95_deg": 4.4,
        "understeer_low_deg": 1.2,
        "understeer_high_deg": 1.2,
        "front_pressure_hot_kpa": 170.0,
        "rear_pressure_hot_kpa": 170.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _result(valid: bool, prediction):
    return SimpleNamespace(
        step1=SimpleNamespace(),
        step2=SimpleNamespace(),
        step3=SimpleNamespace(),
        step4=SimpleNamespace(),
        step5=SimpleNamespace(),
        step6=SimpleNamespace(),
        supporting=SimpleNamespace(),
        legal_validation=SimpleNamespace(valid=valid, messages=[] if valid else ["illegal"]),
        decision_trace=[],
        prediction=prediction,
        prediction_confidence=SimpleNamespace(overall=0.8),
    )


class LegalSearchScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.car = get_car("bmw")
        self.base_result = SimpleNamespace()
        self.solve_inputs = SimpleNamespace()

    def test_scenario_profile_changes_accepted_pick(self) -> None:
        aggressive = _eval("aggressive_family", 18.0, 70.0)
        robust = _eval("robust_family", 12.0, 50.0)
        candidates = [
            LegalCandidate(params=aggressive.params, family=aggressive.family),
            LegalCandidate(params=robust.params, family=robust.family),
        ]
        evaluations = [aggressive, robust]

        def fake_materialize(_base_result, overrides, _solve_inputs):
            front_heave = overrides.step2.get("front_heave_nmm", 50.0)
            if front_heave >= 70.0:
                return _result(True, _prediction(front_heave_travel_used_pct=98.8, body_slip_p95_deg=5.4))
            return _result(True, _prediction())

        with patch("solver.legal_search.LegalSpace.from_car", return_value=SimpleNamespace()), patch(
            "solver.legal_search._generate_family_seeds",
            return_value=candidates,
        ), patch(
            "solver.legal_search._evaluate_candidates",
            return_value=evaluations,
        ), patch(
            "solver.legal_search.canonical_params_to_overrides",
            side_effect=lambda _base, params, car=None: SimpleNamespace(step2={"front_heave_nmm": params["front_heave_spring_nmm"]}),
        ), patch(
            "solver.legal_search.materialize_overrides",
            side_effect=fake_materialize,
        ):
            race_result = run_legal_search(
                car=self.car,
                track="sebring",
                baseline_params={"front_heave_spring_nmm": 50.0},
                budget=20,
                base_result=self.base_result,
                solve_inputs=self.solve_inputs,
                scenario_profile="race",
            )
            quali_result = run_legal_search(
                car=self.car,
                track="sebring",
                baseline_params={"front_heave_spring_nmm": 50.0},
                budget=20,
                base_result=self.base_result,
                solve_inputs=self.solve_inputs,
                scenario_profile="quali",
            )

        self.assertEqual(race_result.accepted_best.family, "robust_family")
        self.assertEqual(quali_result.accepted_best.family, "aggressive_family")

    def test_full_legality_rejection_prevents_illegal_pick(self) -> None:
        top = _eval("top_family", 20.0, 60.0)
        backup = _eval("backup_family", 10.0, 50.0)
        candidates = [
            LegalCandidate(params=top.params, family=top.family),
            LegalCandidate(params=backup.params, family=backup.family),
        ]
        evaluations = [top, backup]

        def fake_materialize(_base_result, overrides, _solve_inputs):
            front_heave = overrides.step2.get("front_heave_nmm", 50.0)
            if front_heave >= 60.0:
                return _result(False, _prediction())
            return _result(True, _prediction())

        with patch("solver.legal_search.LegalSpace.from_car", return_value=SimpleNamespace()), patch(
            "solver.legal_search._generate_family_seeds",
            return_value=candidates,
        ), patch(
            "solver.legal_search._evaluate_candidates",
            return_value=evaluations,
        ), patch(
            "solver.legal_search.canonical_params_to_overrides",
            side_effect=lambda _base, params, car=None: SimpleNamespace(step2={"front_heave_nmm": params["front_heave_spring_nmm"]}),
        ), patch(
            "solver.legal_search.materialize_overrides",
            side_effect=fake_materialize,
        ):
            result = run_legal_search(
                car=self.car,
                track="sebring",
                baseline_params={"front_heave_spring_nmm": 50.0},
                budget=20,
                base_result=self.base_result,
                solve_inputs=self.solve_inputs,
                scenario_profile="single_lap_safe",
            )

        self.assertEqual(result.accepted_candidates_count, 1)
        self.assertEqual(result.accepted_best.family, "backup_family")


if __name__ == "__main__":
    unittest.main()
