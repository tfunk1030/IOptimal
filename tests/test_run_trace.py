"""Tests for output/run_trace.py — runtime transparency system."""
from __future__ import annotations

import io
import sys
import unittest


class RunTraceBasicTests(unittest.TestCase):
    """Basic smoke tests for RunTrace instantiation and rendering."""

    def test_create_and_print_empty(self):
        from output.run_trace import RunTrace
        trace = RunTrace()
        trace.record_car_track("bmw", "Sebring International Raceway")
        # Should not raise
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            trace.print_report(verbose=False)
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout
        self.assertIn("BMW", output)
        self.assertIn("Sebring", output)
        self.assertIn("SOLVER STEPS", output)

    def test_support_tier_mapping(self):
        from output.run_trace import RunTrace
        trace = RunTrace()
        trace.record_car_track("bmw", "test")
        self.assertIn("calibrated", trace.car_support_tier)
        trace.record_car_track("ferrari", "test")
        self.assertIn("partial", trace.car_support_tier)
        trace.record_car_track("porsche", "test")
        self.assertIn("exploratory", trace.car_support_tier)
        trace.record_car_track("acura", "test")
        self.assertIn("unsupported", trace.car_support_tier)

    def test_record_solver_path(self):
        from output.run_trace import RunTrace
        trace = RunTrace()
        trace.record_solver_path("optimizer", reason="BMW/Sebring active")
        self.assertEqual(trace.solver_path, "optimizer")
        self.assertIn("BMW", trace.solver_path_reason)

    def test_record_step(self):
        from output.run_trace import RunTrace
        from types import SimpleNamespace
        trace = RunTrace()
        step1 = SimpleNamespace(
            dynamic_front_rh_mm=28.5,
            dynamic_rear_rh_mm=42.0,
            df_balance_pct=50.14,
            ld_ratio=3.8,
            vortex_burst_margin_mm=7.2,
            front_pushrod_offset_mm=0.0,
        )
        trace.record_step(1, step1)
        self.assertEqual(len(trace.solver_steps), 1)
        self.assertEqual(trace.solver_steps[0].step, 1)
        self.assertIn("dynamic_front_rh_mm", trace.solver_steps[0].key_outputs)
        self.assertAlmostEqual(trace.solver_steps[0].key_outputs["dynamic_front_rh_mm"], 28.5)

    def test_record_legality(self):
        from output.run_trace import RunTrace
        from types import SimpleNamespace
        trace = RunTrace()
        legal = SimpleNamespace(
            validation_tier="full",
            valid=True,
            messages=["All checks passed."],
            warnings=[],
        )
        trace.record_legality(legal)
        self.assertEqual(trace.legality_tier, "full")
        self.assertTrue(trace.legality_valid)

    def test_verbose_print_with_steps(self):
        from output.run_trace import RunTrace
        from types import SimpleNamespace
        trace = RunTrace()
        trace.record_car_track("ferrari", "Sebring")
        trace.record_solver_path("sequential", reason="No optimizer for Ferrari")
        step2 = SimpleNamespace(
            front_heave_nmm=45.0,
            rear_third_nmm=400.0,
            front_bottoming_margin_mm=5.0,
            rear_bottoming_margin_mm=12.0,
            front_excursion_at_rate_mm=8.0,
            perch_offset_front_mm=2.0,
        )
        trace.record_step(2, step2, physics_override=True, notes=["springs from IBT"])
        trace.add_warning("Ferrari springs not solver-optimized")

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            trace.print_report(verbose=True)
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout
        self.assertIn("PASSTHROUGH", output)
        self.assertIn("Ferrari", output)
        self.assertIn("springs from IBT", output)


class TorsionArbCouplingCarSpecificTests(unittest.TestCase):
    """Verify TORSION_ARB_COUPLING is car-specific after the fix."""

    def test_bmw_has_nonzero_coupling(self):
        from car_model.cars import get_car
        bmw = get_car("bmw")
        self.assertAlmostEqual(bmw.torsion_arb_coupling, 0.25)

    def test_ferrari_has_zero_coupling(self):
        from car_model.cars import get_car
        ferrari = get_car("ferrari")
        self.assertAlmostEqual(ferrari.torsion_arb_coupling, 0.0)

    def test_cadillac_has_zero_coupling(self):
        from car_model.cars import get_car
        cadillac = get_car("cadillac")
        self.assertAlmostEqual(cadillac.torsion_arb_coupling, 0.0)


class LegalityTierTests(unittest.TestCase):
    """Verify the validation_tier field exists on LegalValidation."""

    def test_default_tier_is_range_clamp(self):
        from solver.legality_engine import LegalValidation
        lv = LegalValidation(valid=True)
        self.assertEqual(lv.validation_tier, "range_clamp")

    def test_tier_in_dict(self):
        from solver.legality_engine import LegalValidation
        lv = LegalValidation(valid=True, validation_tier="full")
        d = lv.to_dict()
        self.assertEqual(d["validation_tier"], "full")


class SolveChainResultNewFieldsTests(unittest.TestCase):
    """Verify ferrari_passthrough and solver_path fields."""

    def test_default_values(self):
        from solver.solve_chain import SolveChainResult
        from solver.legality_engine import LegalValidation
        from solver.predictor import PredictionConfidence
        result = SolveChainResult(
            step1=None, step2=None, step3=None, step4=None,
            step5=None, step6=None, supporting=None,
            legal_validation=LegalValidation(valid=True),
            decision_trace=[],
            prediction=None,
            prediction_confidence=PredictionConfidence(overall=0.5),
        )
        self.assertFalse(result.ferrari_passthrough)
        self.assertEqual(result.solver_path, "sequential")


class LapGainCarcassTermTests(unittest.TestCase):
    """Verify carcass_ms field exists in LapGainBreakdown."""

    def test_carcass_ms_in_breakdown(self):
        from solver.objective import LapGainBreakdown
        lgb = LapGainBreakdown()
        self.assertAlmostEqual(lgb.carcass_ms, 0.0)
        lgb.carcass_ms = 5.0
        self.assertIn("carcass_ms", lgb.as_dict())
        self.assertAlmostEqual(lgb.as_dict()["carcass_ms"], 5.0)
        # Verify it's included in total_penalty_ms
        lgb2 = LapGainBreakdown(carcass_ms=10.0)
        self.assertAlmostEqual(lgb2.total_penalty_ms, 10.0)


if __name__ == "__main__":
    unittest.main()
