import unittest
from pathlib import Path
from types import SimpleNamespace

from analyzer.adaptive_thresholds import AdaptiveThresholds, compute_adaptive_thresholds
from analyzer.diagnose import diagnose
from analyzer.driver_style import DriverProfile
from analyzer.extract import MeasuredState, extract_measurements
from analyzer.setup_reader import CurrentSetup
from analyzer.state_inference import infer_car_states
from analyzer.telemetry_truth import TelemetrySignal
from car_model import get_car
from track_model.build_profile import build_profile
from track_model.ibt_parser import IBTFile
from analyzer.segment import segment_lap
from analyzer.driver_style import analyze_driver, refine_driver_with_measured


class StateInferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.car = get_car("bmw")
        self.setup = CurrentSetup(source="unit")
        self.thresholds = AdaptiveThresholds()
        self.driver = DriverProfile()

    def test_diagnosis_augments_problem_list_with_root_states_and_overhaul(self) -> None:
        measured = MeasuredState(
            front_heave_travel_used_braking_pct=96.0,
            front_heave_travel_used_pct=91.0,
            front_rh_std_mm=7.2,
            rear_heave_travel_used_pct=89.0,
            rear_rh_std_mm=10.4,
            rear_power_slip_ratio_p95=0.11,
            body_slip_p95_deg=4.8,
            front_braking_lock_ratio_p95=0.10,
            abs_active_pct=36.0,
            front_temp_spread_lf_c=2.5,
            front_temp_spread_rf_c=3.0,
            front_carcass_mean_c=109.0,
            rear_carcass_mean_c=107.0,
            telemetry_signals={
                "front_heave_travel_used_braking_pct": TelemetrySignal(value=96.0, quality="trusted", confidence=0.91, source="test"),
                "front_heave_travel_used_pct": TelemetrySignal(value=91.0, quality="trusted", confidence=0.9, source="test"),
                "front_rh_std_mm": TelemetrySignal(value=7.2, quality="trusted", confidence=0.88, source="test"),
                "rear_heave_travel_used_pct": TelemetrySignal(value=89.0, quality="trusted", confidence=0.86, source="test"),
                "rear_rh_std_mm": TelemetrySignal(value=10.4, quality="trusted", confidence=0.84, source="test"),
                "rear_power_slip_ratio_p95": TelemetrySignal(value=0.11, quality="trusted", confidence=0.82, source="test"),
                "body_slip_p95_deg": TelemetrySignal(value=4.8, quality="proxy", confidence=0.65, source="test"),
                "front_braking_lock_ratio_p95": TelemetrySignal(value=0.10, quality="trusted", confidence=0.8, source="test"),
                "front_carcass_mean_c": TelemetrySignal(value=109.0, quality="trusted", confidence=0.78, source="test"),
                "rear_carcass_mean_c": TelemetrySignal(value=107.0, quality="trusted", confidence=0.78, source="test"),
            },
        )

        diagnosis = diagnose(
            measured,
            self.setup,
            self.car,
            self.thresholds,
            driver=self.driver,
            corners=[],
        )

        self.assertTrue(diagnosis.problems)
        self.assertTrue(diagnosis.state_issues)
        state_ids = {issue.state_id for issue in diagnosis.state_issues}
        self.assertIn("front_platform_collapse_braking", state_ids)
        self.assertIn("rear_platform_under_supported", state_ids)
        self.assertIn("exit_traction_limited", state_ids)
        self.assertIn("brake_system_front_limited", state_ids)
        self.assertIsNotNone(diagnosis.overhaul_assessment)
        self.assertEqual(diagnosis.overhaul_assessment.classification, "baseline_reset")
        self.assertGreater(diagnosis.evidence_strength, 0.6)

    def test_low_confidence_signal_produces_low_confidence_state(self) -> None:
        measured = MeasuredState(
            front_heave_travel_used_pct=90.0,
            telemetry_signals={
                "front_heave_travel_used_pct": TelemetrySignal(
                    value=90.0,
                    quality="proxy",
                    confidence=0.2,
                    source="fallback_test",
                    fallback_used=True,
                )
            },
        )

        issues = infer_car_states(
            measured=measured,
            setup=self.setup,
            problems=[],
            driver=self.driver,
            corners=[],
        )

        target = next(issue for issue in issues if issue.state_id == "front_platform_near_limit_high_speed")
        self.assertLessEqual(target.confidence, 0.25)

    def test_bmw_fixture_overhaul_classifications_are_recalibrated(self) -> None:
        expectations = {
            "bmw151.ibt": {"minor_tweak", "moderate_rework"},
            "bmwbad.ibt": {"baseline_reset", "moderate_rework"},
            "bmw170.ibt": {"moderate_rework", "minor_tweak"},
            "bmwtf.ibt": {"moderate_rework"},
        }

        for fixture_name, allowed in expectations.items():
            fixture = Path("/workspace/ibtfiles") / fixture_name
            if not fixture.exists():
                self.skipTest(f"{fixture_name} fixture unavailable")

            ibt = IBTFile(fixture)
            setup = CurrentSetup.from_ibt(ibt)
            measured = extract_measurements(fixture, self.car)
            lap_idx = ibt.best_lap_indices(min_time=108.0, outlier_pct=0.115)
            corners = []
            if lap_idx is not None:
                start, end = lap_idx
                corners = segment_lap(ibt, start, end, car=self.car, tick_rate=ibt.tick_rate)
            driver = analyze_driver(ibt, corners, self.car, tick_rate=ibt.tick_rate)
            refine_driver_with_measured(driver, measured)
            diag = diagnose(
                measured,
                setup,
                self.car,
                compute_adaptive_thresholds(build_profile(fixture), self.car, driver),
                driver=driver,
                corners=corners,
            )
            self.assertIn(
                diag.overhaul_assessment.classification,
                allowed,
                msg=f"{fixture_name} classified as {diag.overhaul_assessment.classification}",
            )

    def test_driver_noise_reduces_phase_based_state_confidence(self) -> None:
        measured = MeasuredState(
            understeer_low_speed_deg=1.8,
            rear_power_slip_ratio_p95=0.10,
            body_slip_p95_deg=4.5,
            telemetry_signals={
                "understeer_low_speed_deg": TelemetrySignal(value=1.8, quality="proxy", confidence=0.65, source="test"),
                "rear_power_slip_ratio_p95": TelemetrySignal(value=0.10, quality="trusted", confidence=0.8, source="test"),
                "body_slip_p95_deg": TelemetrySignal(value=4.5, quality="proxy", confidence=0.6, source="test"),
            },
        )
        corners = [
            SimpleNamespace(
                trail_brake_pct=0.3,
                understeer_mean_deg=1.4,
                corner_confidence=0.9,
                entry_pitch_severity=0.7,
                entry_loss_s=0.10,
                throttle_delay_s=0.32,
                traction_risk_flags=["late_throttle"],
                exit_slip_severity=0.8,
            ),
            SimpleNamespace(
                trail_brake_pct=0.28,
                understeer_mean_deg=1.2,
                corner_confidence=0.85,
                entry_pitch_severity=0.6,
                entry_loss_s=0.08,
                throttle_delay_s=0.28,
                traction_risk_flags=["late_throttle"],
                exit_slip_severity=0.75,
            ),
        ]
        smooth_driver = DriverProfile(classification_confidence=0.92, driver_noise_index=0.05)
        noisy_driver = DriverProfile(classification_confidence=0.35, driver_noise_index=0.8)

        smooth_issues = infer_car_states(
            measured=measured,
            setup=self.setup,
            problems=[],
            driver=smooth_driver,
            corners=corners,
        )
        noisy_issues = infer_car_states(
            measured=measured,
            setup=self.setup,
            problems=[],
            driver=noisy_driver,
            corners=corners,
        )

        smooth_entry = next(issue for issue in smooth_issues if issue.state_id == "entry_front_limited")
        noisy_entry = next(issue for issue in noisy_issues if issue.state_id == "entry_front_limited")
        smooth_exit = next(issue for issue in smooth_issues if issue.state_id == "exit_traction_limited")
        noisy_exit = next(issue for issue in noisy_issues if issue.state_id == "exit_traction_limited")

        self.assertGreater(smooth_entry.confidence, noisy_entry.confidence)
        self.assertGreater(smooth_exit.confidence, noisy_exit.confidence)


if __name__ == "__main__":
    unittest.main()
