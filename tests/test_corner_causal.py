"""Unit tests for solver/corner_causal.py (Unit P1).

Verifies per-corner phase causal regression evaluation, Pareto-frontier
candidate filtering, and report formatting helpers degrade gracefully
when D3's ``corner_phase_models`` data is absent.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from solver.corner_causal import (
    METRIC_BETTER_DIRECTION,
    ParetoSummary,
    _evaluate_model,
    _feature_value,
    any_corner_worse,
    find_pareto_dominant,
    format_corner_impact_lines,
    format_pareto_tradeoff_lines,
    parse_cpm_key,
    pareto_summary,
    predict_corner_phase_impact,
    setup_dict_from_current,
    setup_dict_from_steps,
)


def _model(name: str, feature_names: list[str], coefficients: list[float],
           is_calibrated: bool = True):
    return SimpleNamespace(
        name=name,
        feature_names=feature_names,
        coefficients=coefficients,
        is_calibrated=is_calibrated,
    )


class TestFeatureValue(unittest.TestCase):
    def test_direct_lookup(self) -> None:
        setup = {"front_heave": 200.0, "rear_third": 400.0}
        self.assertEqual(_feature_value(setup, "front_heave"), 200.0)
        self.assertEqual(_feature_value(setup, "rear_third"), 400.0)

    def test_inverse_features(self) -> None:
        setup = {"front_heave": 200.0, "rear_third": 400.0, "rear_spring": 150.0,
                 "torsion_od": 14.0}
        self.assertAlmostEqual(_feature_value(setup, "inv_front_heave"), 1.0 / 200.0)
        self.assertAlmostEqual(_feature_value(setup, "inv_rear_third"), 1.0 / 400.0)
        self.assertAlmostEqual(_feature_value(setup, "inv_rear_spring"), 1.0 / 150.0)
        self.assertAlmostEqual(_feature_value(setup, "inv_od4"), 1.0 / (14.0 ** 4))

    def test_inverse_clamps_at_one(self) -> None:
        # Zero rate must NOT cause divide-by-zero — the impl uses max(rate, 1.0).
        self.assertAlmostEqual(_feature_value({"front_heave": 0.0}, "inv_front_heave"), 1.0)

    def test_engineered_squares(self) -> None:
        setup = {"front_pushrod": -3.0, "rear_pushrod": 4.0}
        self.assertEqual(_feature_value(setup, "front_pushrod_sq"), 9.0)
        self.assertEqual(_feature_value(setup, "rear_pushrod_sq"), 16.0)

    def test_fuel_compliance_interaction(self) -> None:
        setup = {"fuel": 50.0, "rear_spring": 200.0, "rear_third": 400.0}
        self.assertAlmostEqual(_feature_value(setup, "fuel_x_inv_spring"), 50.0 / 200.0)
        self.assertAlmostEqual(_feature_value(setup, "fuel_x_inv_third"), 50.0 / 400.0)

    def test_unknown_feature_zero(self) -> None:
        self.assertEqual(_feature_value({}, "no_such_thing"), 0.0)


class TestEvaluateModel(unittest.TestCase):
    def test_intercept_only(self) -> None:
        m = _model("understeer", [], [3.0])
        self.assertEqual(_evaluate_model(m, {}), 3.0)

    def test_simple_linear(self) -> None:
        # y = 1.0 + 2.0 * front_heave + 0.5 * rear_third
        m = _model("y", ["front_heave", "rear_third"], [1.0, 2.0, 0.5])
        setup = {"front_heave": 10.0, "rear_third": 20.0}
        self.assertAlmostEqual(_evaluate_model(m, setup), 1.0 + 2.0 * 10.0 + 0.5 * 20.0)

    def test_no_coefficients_zero(self) -> None:
        m = _model("y", [], [])
        self.assertEqual(_evaluate_model(m, {"front_heave": 100.0}), 0.0)

    def test_mismatched_lengths_uses_min(self) -> None:
        # 3 names, 2 betas (+intercept) — only 2 features should contribute.
        m = _model("y", ["a", "b", "c"], [10.0, 1.0, 1.0])
        setup = {"a": 5.0, "b": 7.0, "c": 9.0}
        # 10 + 1*5 + 1*7 = 22 (no contribution from c because beta missing)
        self.assertEqual(_evaluate_model(m, setup), 22.0)


class TestPredictCornerPhaseImpact(unittest.TestCase):
    def test_no_models_returns_empty(self) -> None:
        car_models = SimpleNamespace()  # no corner_phase_models attribute
        self.assertEqual(predict_corner_phase_impact(car_models, {}, {}), {})

    def test_empty_dict_returns_empty(self) -> None:
        car_models = SimpleNamespace(corner_phase_models={})
        self.assertEqual(predict_corner_phase_impact(car_models, {}, {}), {})

    def test_none_car_models(self) -> None:
        self.assertEqual(predict_corner_phase_impact(None, {}, {}), {})

    def test_predicts_delta_from_change(self) -> None:
        # y = 0 + 1 * front_heave  → +50 N/mm change should yield +50 delta.
        m = _model("c4_entry_understeer", ["front_heave"], [0.0, 1.0])
        car_models = SimpleNamespace(corner_phase_models={
            "corner4__entry__understeer_deg": m,
        })
        baseline = {"front_heave": 200.0}
        changes = {"front_heave": 250.0}
        impacts = predict_corner_phase_impact(car_models, changes, baseline)
        self.assertEqual(len(impacts), 1)
        self.assertAlmostEqual(impacts["corner4__entry__understeer_deg"], 50.0)

    def test_skips_uncalibrated(self) -> None:
        m_good = _model("a", ["front_heave"], [0.0, 1.0])
        m_bad = _model("b", ["front_heave"], [0.0, 1.0], is_calibrated=False)
        car_models = SimpleNamespace(corner_phase_models={
            "corner1__entry__a": m_good,
            "corner2__entry__b": m_bad,
        })
        impacts = predict_corner_phase_impact(
            car_models, {"front_heave": 250.0}, {"front_heave": 200.0}
        )
        self.assertIn("corner1__entry__a", impacts)
        self.assertNotIn("corner2__entry__b", impacts)


class TestParseKey(unittest.TestCase):
    def test_parses_valid_key(self) -> None:
        self.assertEqual(parse_cpm_key("corner4__entry__understeer_deg"),
                         (4, "entry", "understeer_deg"))

    def test_parses_complex_metric(self) -> None:
        # metric segment may contain underscores
        result = parse_cpm_key("corner12__mid__front_shock_vel_p95_mps")
        self.assertEqual(result, (12, "mid", "front_shock_vel_p95_mps"))

    def test_returns_none_on_mismatch(self) -> None:
        self.assertIsNone(parse_cpm_key("garbage"))
        self.assertIsNone(parse_cpm_key("corner__entry__metric"))


class TestParetoFrontier(unittest.TestCase):
    def testany_corner_worse_lower_is_better(self) -> None:
        # understeer: lower is better → +0.2 degree is WORSE
        impacts = {"corner4__entry__understeer_deg": 0.2}
        self.assertTrue(any_corner_worse(impacts, None))
        # negative delta = better (improved)
        impacts2 = {"corner4__entry__understeer_deg": -0.2}
        self.assertFalse(any_corner_worse(impacts2, None))

    def testany_corner_worse_higher_is_better(self) -> None:
        # apex_speed_kph: higher is better → -2 kph is WORSE
        impacts = {"corner4__exit__apex_speed_kph": -2.0}
        self.assertTrue(any_corner_worse(impacts, None))
        # +2 kph = better
        impacts2 = {"corner4__exit__apex_speed_kph": 2.0}
        self.assertFalse(any_corner_worse(impacts2, None))

    def test_below_threshold_not_worsening(self) -> None:
        impacts = {"corner4__entry__understeer_deg": 1e-12}
        self.assertFalse(any_corner_worse(impacts, None, worse_threshold=1e-6))

    def test_find_pareto_dominant(self) -> None:
        cand_clean = SimpleNamespace(corner_impacts={
            "corner1__entry__understeer_deg": -0.1,  # better
            "corner2__exit__apex_speed_kph": 0.5,   # better
        })
        cand_bad = SimpleNamespace(corner_impacts={
            "corner1__entry__understeer_deg": 0.1,  # WORSE
            "corner2__exit__apex_speed_kph": 0.5,
        })
        cand_no_data = SimpleNamespace()  # no corner_impacts attr

        dominant = find_pareto_dominant([cand_clean, cand_bad, cand_no_data])
        self.assertEqual(dominant, [cand_clean])

    def test_pareto_summary_buckets(self) -> None:
        impacts = {
            "corner1__entry__understeer_deg": -0.5,  # improves
            "corner2__entry__understeer_deg": 0.5,   # worsens
            "corner3__entry__understeer_deg": 0.0,   # neutral
        }
        summary = pareto_summary(impacts)
        self.assertIsInstance(summary, ParetoSummary)
        self.assertEqual(summary.improved, 1)
        self.assertEqual(summary.worsened, 1)
        self.assertEqual(summary.unchanged, 1)
        self.assertEqual(summary.total, 3)


class TestSetupDictBuilders(unittest.TestCase):
    def test_setup_dict_from_steps_basic(self) -> None:
        step1 = SimpleNamespace(front_pushrod_offset_mm=-3.5, rear_pushrod_offset_mm=2.0)
        step2 = SimpleNamespace(
            front_heave_nmm=200.0, rear_third_nmm=400.0,
            perch_offset_front_mm=-1.0, perch_offset_rear_mm=2.0,
        )
        step3 = SimpleNamespace(
            front_torsion_od_mm=14.5, rear_spring_rate_nmm=150.0,
            rear_spring_perch_mm=30.0,
        )
        step5 = SimpleNamespace(front_camber_deg=-3.0, rear_camber_deg=-2.0)
        setup = setup_dict_from_steps(
            step1=step1, step2=step2, step3=step3, step5=step5,
            fuel_l=58.0, wing_deg=17.0,
        )
        self.assertEqual(setup["front_pushrod"], -3.5)
        self.assertEqual(setup["front_heave"], 200.0)
        self.assertEqual(setup["torsion_od"], 14.5)
        self.assertEqual(setup["fuel"], 58.0)
        self.assertEqual(setup["wing"], 17.0)

    def test_setup_dict_from_current_none_safe(self) -> None:
        self.assertEqual(setup_dict_from_current(None), {})


class TestReportFormatting(unittest.TestCase):
    def test_format_lines_groups_by_corner(self) -> None:
        impacts = {
            "corner1__entry__understeer_deg": -0.1,
            "corner1__exit__apex_speed_kph": 0.3,
            "corner3__entry__understeer_deg": 0.2,
        }
        corner1 = SimpleNamespace(corner_id=1, speed_class="high",
                                   direction="left", apex_speed_kph=210.0)
        corner3 = SimpleNamespace(corner_id=3, speed_class="low",
                                   direction="right", apex_speed_kph=85.0)
        lines = format_corner_impact_lines([corner1, corner3], impacts)
        # Both corners should appear with metadata
        joined = "\n".join(lines)
        self.assertIn("Corner 1", joined)
        self.assertIn("210 kph", joined)
        self.assertIn("Corner 3", joined)
        self.assertIn("85 kph", joined)
        # Net verdict for corner 3 (one worsening metric only) → clear regression
        self.assertIn("clear regression", joined)

    def test_format_lines_empty_impacts(self) -> None:
        self.assertEqual(format_corner_impact_lines([], {}), [])

    def test_pareto_tradeoff_lines_dominant(self) -> None:
        impacts = {
            "corner1__entry__understeer_deg": -0.2,
            "corner2__exit__apex_speed_kph": 1.5,
        }
        lines = format_pareto_tradeoff_lines(impacts)
        joined = "\n".join(lines)
        self.assertIn("Pareto-dominant", joined)

    def test_pareto_tradeoff_lines_mixed(self) -> None:
        impacts = {
            "corner1__entry__understeer_deg": -0.5,  # improve
            "corner2__entry__understeer_deg": 0.1,   # worsen
        }
        lines = format_pareto_tradeoff_lines(impacts)
        joined = "\n".join(lines)
        self.assertIn("improves 1 corner", joined)
        self.assertIn("worsens 1 corner", joined)


class TestMetricDirectionMap(unittest.TestCase):
    def test_known_metrics(self) -> None:
        # Smoke test that direction map is populated for the headline metrics.
        for key in ("understeer_deg", "body_slip", "apex_speed_kph",
                    "front_shock_vel_p95_mps"):
            self.assertIn(key, METRIC_BETTER_DIRECTION)


class TestParetoReselect(unittest.TestCase):
    """End-to-end verification of pareto_reselect_winner from candidate_search."""

    def _candidate(self, family: str, score_total: float, impacts: dict[str, float],
                   selectable: bool = True, selected: bool = False):
        return SimpleNamespace(
            family=family,
            selectable=selectable,
            selected=selected,
            score=SimpleNamespace(total=score_total),
            corner_impacts=impacts,
            notes=[],
        )

    def test_no_impacts_keeps_existing_selection(self) -> None:
        from solver.candidate_search import pareto_reselect_winner
        # Pre-existing selection is honoured when no impact data exists, so
        # the upstream score-based winner stays selected.
        a = self._candidate("a", 0.6, {}, selected=True)
        b = self._candidate("b", 0.7, {})
        winner = pareto_reselect_winner([a, b])
        self.assertEqual(winner.family, "a")

    def test_prefers_pareto_dominant_over_higher_score(self) -> None:
        from solver.candidate_search import pareto_reselect_winner
        # Higher-score candidate worsens corner 5; lower-score candidate is Pareto-dominant.
        worsens = self._candidate("higher_score", 0.9, {
            "corner1__entry__understeer_deg": -0.1,
            "corner5__entry__understeer_deg": 0.5,  # WORSE
        })
        dominant = self._candidate("clean", 0.6, {
            "corner1__entry__understeer_deg": -0.05,
            "corner5__entry__understeer_deg": -0.02,
        })
        winner = pareto_reselect_winner([worsens, dominant])
        self.assertEqual(winner.family, "clean")
        self.assertTrue(winner.selected)
        self.assertFalse(worsens.selected)
        self.assertTrue(any("Pareto-dominant" in n for n in winner.notes))

    def test_falls_back_to_score_with_tradeoff_note(self) -> None:
        from solver.candidate_search import pareto_reselect_winner
        a = self._candidate("a", 0.6, {
            "corner1__entry__understeer_deg": 0.2,  # worsens
        })
        b = self._candidate("b", 0.8, {
            "corner1__entry__understeer_deg": 0.1,  # also worsens but smaller
            "corner2__entry__understeer_deg": -0.3,  # improves
        })
        winner = pareto_reselect_winner([a, b])
        # No Pareto-dominant candidate; highest score wins with tradeoff note.
        self.assertEqual(winner.family, "b")
        self.assertTrue(any("tradeoff" in n.lower() for n in winner.notes))


if __name__ == "__main__":
    unittest.main()
