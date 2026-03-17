import unittest
from types import SimpleNamespace

from analyzer.setup_reader import CurrentSetup
from analyzer.telemetry_truth import TelemetrySignal, summarize_signal_quality


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


if __name__ == "__main__":
    unittest.main()
