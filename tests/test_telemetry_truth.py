import unittest
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from pathlib import Path

from analyzer.diagnose import Diagnosis
from analyzer.extract import MeasuredState
from analyzer.report import format_report, save_analysis_json
from analyzer.recommend import AnalysisResult
from analyzer.setup_reader import CurrentSetup
from analyzer.telemetry_truth import TelemetrySignal, build_signal_map, summarize_signal_quality


class FakeIBT:
    def __init__(self, session_info: dict):
        self.session_info = session_info


class SetupReaderBrakeFieldTests(unittest.TestCase):
    def test_parses_brake_hardware_fields_from_bmw_layout(self) -> None:
        ibt = FakeIBT(
            {
                "CarSetup": {
                    "TiresAero": {"AeroSettings": {}, "AeroCalculator": {}},
                    "Chassis": {
                        "Front": {},
                        "Rear": {},
                        "LeftFront": {},
                        "RightFront": {},
                        "LeftRear": {},
                        "RightRear": {},
                    },
                    "BrakesDriveUnit": {
                        "BrakeSpec": {
                            "BrakePressureBias": "46.0%",
                            "BrakeBiasTarget": "1.5",
                            "BrakeBiasMigration": "-0.5",
                            "FrontMasterCyl": "19.1 mm",
                            "RearMasterCyl": "20.6 mm",
                            "PadCompound": "Medium",
                        },
                        "RearDiffSpec": {},
                        "TractionControl": {},
                        "Fuel": {},
                    },
                }
            }
        )

        setup = CurrentSetup.from_ibt(ibt)

        self.assertEqual(setup.brake_bias_pct, 46.0)
        self.assertEqual(setup.brake_bias_target, 1.5)
        self.assertEqual(setup.brake_bias_migration, -0.5)
        self.assertEqual(setup.front_master_cyl_mm, 19.1)
        self.assertEqual(setup.rear_master_cyl_mm, 20.6)
        self.assertEqual(setup.pad_compound, "Medium")

    def test_parses_brake_hardware_fields_from_ferrari_layout(self) -> None:
        ibt = FakeIBT(
            {
                "CarSetup": {
                    "TiresAero": {"AeroSettings": {}, "AeroCalculator": {}},
                    "Chassis": {
                        "Front": {},
                        "Rear": {},
                        "LeftFront": {},
                        "RightFront": {},
                        "LeftRear": {},
                        "RightRear": {},
                    },
                    "Systems": {
                        "BrakeSpec": {
                            "BrakePressureBias": "53.0%",
                            "BrakeBiasTarget": "0.0",
                            "BrakeBiasMigration": "1.0",
                            "FrontMasterCyl": "17.8 mm",
                            "RearMasterCyl": "17.8 mm",
                            "PadCompound": "Low",
                        },
                        "RearDiffSpec": {},
                        "FrontDiffSpec": {},
                        "TractionControl": {},
                        "Fuel": {},
                    },
                    "Dampers": {},
                }
            }
        )

        setup = CurrentSetup.from_ibt(ibt)

        self.assertEqual(setup.brake_bias_pct, 53.0)
        self.assertEqual(setup.brake_bias_target, 0.0)
        self.assertEqual(setup.brake_bias_migration, 1.0)
        self.assertEqual(setup.front_master_cyl_mm, 17.8)
        self.assertEqual(setup.rear_master_cyl_mm, 17.8)
        self.assertEqual(setup.pad_compound, "Low")


class TelemetryTruthSummaryTests(unittest.TestCase):
    def test_summarize_signal_quality_groups_trusted_proxy_and_unresolved(self) -> None:
        measured = SimpleNamespace(
            telemetry_signals={
                "front_rh_std_mm": TelemetrySignal(
                    value=3.8,
                    quality="trusted",
                    confidence=0.92,
                    source="ride_height_channels",
                ),
                "body_slip_p95_deg": TelemetrySignal(
                    value=2.1,
                    quality="proxy",
                    confidence=0.61,
                    source="body_velocity_proxy",
                ),
                "front_rh_settle_time_ms": TelemetrySignal(
                    value=None,
                    quality="unknown",
                    confidence=0.0,
                    source="event_based_clean_disturbance_response",
                    invalid_reason="insufficient_clean_events",
                ),
            }
        )

        lines = summarize_signal_quality(measured)
        joined = "\n".join(lines)

        self.assertIn("Trusted: front_rh_std_mm", joined)
        self.assertIn("Proxy: body_slip_p95_deg", joined)
        self.assertIn("front_rh_settle_time_ms (insufficient_clean_events)", joined)

    def test_analyzer_report_surfaces_signal_quality_and_brake_hardware(self) -> None:
        setup = CurrentSetup(
            source="unit",
            brake_bias_pct=46.0,
            brake_bias_target=1.5,
            brake_bias_migration=-0.5,
            front_master_cyl_mm=19.1,
            rear_master_cyl_mm=20.6,
            pad_compound="Medium",
        )
        diagnosis = Diagnosis(assessment="competitive", lap_time_s=109.8, lap_number=4)
        measured = MeasuredState(
            telemetry_signals={
                "front_rh_std_mm": TelemetrySignal(
                    value=3.8,
                    quality="trusted",
                    confidence=0.92,
                    source="ride_height_channels",
                ),
                "body_slip_p95_deg": TelemetrySignal(
                    value=2.1,
                    quality="proxy",
                    confidence=0.61,
                    source="body_velocity_proxy",
                ),
                "front_rh_settle_time_ms": TelemetrySignal(
                    value=None,
                    quality="unknown",
                    confidence=0.0,
                    source="event_based_clean_disturbance_response",
                    invalid_reason="insufficient_clean_events",
                ),
            },
            metric_fallbacks=["front_braking_lock_ratio_p95=fallback_brake_mask"],
        )
        result = AnalysisResult(diagnosis=diagnosis, current_setup=setup, improved_setup=setup)

        report = format_report(result, "BMW", "Sebring", "session.ibt", measured=measured)

        self.assertIn("SIGNAL CONFIDENCE", report)
        self.assertIn("Trusted: front_rh_std_mm", report)
        self.assertIn("Proxy: body_slip_p95_deg", report)
        self.assertIn("Brake target: +1.5", report)
        self.assertIn("Master cyl: F 19.1 / R 20.6 mm", report)

    def test_analysis_json_includes_signal_quality_metadata(self) -> None:
        setup = CurrentSetup(source="unit")
        diagnosis = Diagnosis(assessment="fast", lap_time_s=109.8, lap_number=4)
        measured = MeasuredState(
            telemetry_signals={
                "front_rh_std_mm": TelemetrySignal(
                    value=3.8,
                    quality="trusted",
                    confidence=0.92,
                    source="ride_height_channels",
                ),
            },
            telemetry_bundle={"aero_platform": {"front_rh_std_mm": {"quality": "trusted"}}},
        )
        result = AnalysisResult(diagnosis=diagnosis, current_setup=setup, improved_setup=setup)

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "analysis.json"
            save_analysis_json(result, "BMW", "Sebring", measured, path)
            text = path.read_text()

        self.assertIn("\"signal_quality_summary\"", text)
        self.assertIn("\"telemetry_signals\"", text)
        self.assertIn("\"telemetry_bundle\"", text)

    def test_build_signal_map_marks_fallback_metrics_as_proxy(self) -> None:
        measured = MeasuredState(
            front_braking_lock_ratio_p95=0.071,
            rear_power_slip_ratio_p95=0.083,
            hydraulic_brake_split_pct=46.2,
            hydraulic_brake_split_confidence=0.45,
            metric_fallbacks=[
                "front_braking_lock_ratio_p95=fallback_brake_mask",
                "rear_power_slip_ratio_p95=legacy_speed_mask",
            ],
        )

        signals = build_signal_map(measured)

        self.assertEqual(signals["front_braking_lock_ratio_p95"].quality, "proxy")
        self.assertTrue(signals["front_braking_lock_ratio_p95"].fallback_used)
        self.assertEqual(signals["rear_power_slip_ratio_p95"].quality, "proxy")
        self.assertTrue(signals["rear_power_slip_ratio_p95"].fallback_used)
        self.assertEqual(signals["hydraulic_brake_split_pct"].quality, "proxy")


if __name__ == "__main__":
    unittest.main()
