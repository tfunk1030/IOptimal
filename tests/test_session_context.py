import unittest
from types import SimpleNamespace

from analyzer.context import SessionContext, build_session_context
from analyzer.diagnose import Diagnosis
from analyzer.extract import MeasuredState
from analyzer.setup_reader import CurrentSetup
from analyzer.telemetry_truth import TelemetrySignal
from pipeline.reason import ReasoningState, _build_health_models, _resolve_authority_session


class SessionContextTests(unittest.TestCase):
    def test_build_session_context_flags_weak_thermal_and_comparability(self) -> None:
        measured = MeasuredState(
            front_carcass_mean_c=109.0,
            rear_carcass_mean_c=107.0,
            front_pressure_mean_kpa=181.0,
            rear_pressure_mean_kpa=179.0,
            telemetry_signals={
                "front_carcass_mean_c": TelemetrySignal(value=109.0, quality="trusted", confidence=0.8, source="test"),
                "rear_carcass_mean_c": TelemetrySignal(value=107.0, quality="trusted", confidence=0.8, source="test"),
                "front_pressure_mean_kpa": TelemetrySignal(value=181.0, quality="trusted", confidence=0.75, source="test"),
                "rear_pressure_mean_kpa": TelemetrySignal(value=179.0, quality="trusted", confidence=0.75, source="test"),
            },
        )
        diagnosis = Diagnosis(assessment="dangerous")
        setup = CurrentSetup(source="unit", fuel_l=89.0)

        context = build_session_context(measured, setup, diagnosis)

        self.assertEqual(context.tyre_state, "overheated")
        self.assertLess(context.thermal_validity, 0.55)
        self.assertFalse(context.comparable_to_baseline)

    def test_authority_scoring_prefers_healthier_session_over_raw_best_lap(self) -> None:
        weak_context = SessionContext(
            fuel_l=89.0,
            tyre_state="overheated",
            thermal_validity=0.35,
            pace_validity=0.2,
            traffic_confidence=0.55,
            weather_confidence=0.5,
            comparable_to_baseline=False,
            notes=["session is not a clean baseline authority candidate"],
        )
        strong_context = SessionContext(
            fuel_l=89.0,
            tyre_state="in_window",
            thermal_validity=0.92,
            pace_validity=0.95,
            traffic_confidence=0.8,
            weather_confidence=0.82,
            comparable_to_baseline=True,
            notes=["thermal state is representative"],
        )
        weak_measured = SimpleNamespace(
            telemetry_signals={
                "front_rh_std_mm": TelemetrySignal(value=6.0, quality="proxy", confidence=0.3, source="test"),
                "rear_rh_std_mm": TelemetrySignal(value=None, quality="unknown", confidence=0.0, source="test", invalid_reason="missing"),
            },
            metric_fallbacks=["front_braking_lock_ratio_p95=fallback_brake_mask"],
        )
        strong_measured = SimpleNamespace(
            telemetry_signals={
                "front_rh_std_mm": TelemetrySignal(value=4.0, quality="trusted", confidence=0.9, source="test"),
                "rear_rh_std_mm": TelemetrySignal(value=4.5, quality="trusted", confidence=0.88, source="test"),
            },
            metric_fallbacks=[],
        )
        state = ReasoningState(
            sessions=[
                SimpleNamespace(
                    label="S1",
                    lap_time_s=100.0,
                    diagnosis=Diagnosis(assessment="dangerous"),
                    session_context=weak_context,
                    measured=weak_measured,
                ),
                SimpleNamespace(
                    label="S2",
                    lap_time_s=100.3,
                    diagnosis=Diagnosis(assessment="fast"),
                    session_context=strong_context,
                    measured=strong_measured,
                ),
            ]
        )

        _resolve_authority_session(state)

        self.assertEqual(state.authority_session_idx, 1)
        self.assertEqual(state.solve_basis, "authority_score")
        self.assertTrue(state.authority_scores)
        self.assertEqual(state.authority_scores[0]["session"], "S2")

    def test_low_sample_envelope_does_not_penalize_authority(self) -> None:
        context = SessionContext(
            fuel_l=89.0,
            tyre_state="in_window",
            thermal_validity=0.9,
            pace_validity=0.9,
            traffic_confidence=0.8,
            weather_confidence=0.8,
            comparable_to_baseline=True,
            notes=[],
        )
        measured = SimpleNamespace(
            telemetry_signals={
                "front_rh_std_mm": TelemetrySignal(value=4.0, quality="trusted", confidence=0.9, source="test"),
                "rear_rh_std_mm": TelemetrySignal(value=4.5, quality="trusted", confidence=0.88, source="test"),
            },
            metric_fallbacks=[],
            front_rh_std_mm=4.0,
            rear_rh_std_mm=4.5,
            understeer_mean_deg=0.3,
            body_slip_p95_deg=2.0,
            front_heave_travel_used_pct=70.0,
            rear_power_slip_ratio_p95=0.05,
            front_braking_lock_ratio_p95=0.04,
        )
        setup = SimpleNamespace(
            front_pushrod_mm=-26.5,
            rear_pushrod_mm=-24.0,
            front_heave_nmm=40.0,
            rear_third_nmm=520.0,
            front_torsion_od_mm=13.9,
            rear_spring_nmm=160.0,
            front_arb_blade=1,
            rear_arb_blade=3,
            front_camber_deg=-2.9,
            rear_camber_deg=-1.9,
            front_toe_mm=-0.4,
            rear_toe_mm=0.0,
            brake_bias_pct=46.0,
            diff_preload_nm=20.0,
        )
        state = ReasoningState(
            sessions=[
                SimpleNamespace(label="S1", lap_time_s=100.0, diagnosis=Diagnosis(assessment="fast", state_issues=[], overhaul_assessment=None), session_context=context, measured=measured, setup=setup),
                SimpleNamespace(label="S2", lap_time_s=100.1, diagnosis=Diagnosis(assessment="fast", state_issues=[], overhaul_assessment=None), session_context=context, measured=measured, setup=setup),
            ]
        )
        _build_health_models(state)
        _resolve_authority_session(state)

        self.assertIsNotNone(state.telemetry_envelope)
        self.assertLess(state.telemetry_envelope.sample_count, 3)
        self.assertEqual(state.authority_scores[0]["envelope_distance"], 0.0)
        self.assertEqual(state.authority_scores[0]["setup_distance"], 0.0)


if __name__ == "__main__":
    unittest.main()
