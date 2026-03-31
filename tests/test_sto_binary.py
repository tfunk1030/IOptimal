"""Tests for binary STO container decode and Acura adapters."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from analyzer.setup_reader import CurrentSetup
from analyzer.sto_adapters import build_diff_rows
from analyzer.sto_binary import decode_sto
from car_model.setup_registry import get_car_spec

REPO_ROOT = Path(__file__).resolve().parents[1]
ACURA_DIR = REPO_ROOT / "data" / "acura_sto"
P1DOKS_HOCK_R = ACURA_DIR / "P1Doks_AcuraGTP_Hockenheim_R_26S2W3.sto"
VRS_SEBRING_R1 = ACURA_DIR / "VRS_26S1MC_ARX06_IMSA_Sebring_R1.sto"
ARA_HOCK_R1 = ACURA_DIR / "ARA_26s2_ARXGTP_IMSA_Hock_R1.0.sto"
BMW_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "funkbmwidkjack3.sto"
BMW_SETUPDELTA_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "setupdelta_bmw_funkbmwidkjack3.json"


class StoBinaryDecodeTests(unittest.TestCase):
    def test_decode_p1doks_binary_layout(self):
        decoded = decode_sto(P1DOKS_HOCK_R)
        self.assertEqual(decoded.version, 3)
        self.assertEqual(decoded.header_words, (3, 19272, 3344, 15888))
        self.assertEqual(decoded.car_id, "acuraarx06gtp")
        self.assertEqual(decoded.provider_name, "p1doks")
        self.assertIn("Jaden Munoz", decoded.notes_text)
        self.assertIn("Hockenheim", decoded.notes_text)
        self.assertGreater(len(decoded.setup_blob), 3000)
        self.assertGreater(len(decoded.raw_entries), 4)

    def test_decode_vrs_binary_layout(self):
        decoded = decode_sto(VRS_SEBRING_R1)
        self.assertEqual(decoded.version, 3)
        self.assertEqual(decoded.header_words, (3, 9934, 3408, 6486))
        self.assertEqual(decoded.car_id, "acuraarx06gtp")
        self.assertEqual(decoded.provider_name, "vrs")
        self.assertIn("Virtual Racing School", decoded.notes_text)
        self.assertIn("Michele Costantini", decoded.notes_text)
        self.assertGreater(len(decoded.setup_blob), 3000)

    def test_bmw_decode_matches_setupdelta_snapshot_car_id(self):
        decoded = decode_sto(BMW_FIXTURE)
        snapshot = json.loads(BMW_SETUPDELTA_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(decoded.car_id, snapshot["carName"])
        self.assertEqual(decoded.provider_name, "grid-and-go")
        self.assertIn("Grid-and-Go.com", decoded.notes_text)
        self.assertGreater(len(snapshot["rows"]), 50)


class AcuraStoAdapterTests(unittest.TestCase):
    def test_current_setup_from_p1doks_oracle(self):
        setup = CurrentSetup.from_sto(P1DOKS_HOCK_R)
        self.assertEqual(setup.source, "sto")
        self.assertEqual(setup.sto_car_id, "acuraarx06gtp")
        self.assertEqual(setup.sto_provider_name, "p1doks")
        self.assertAlmostEqual(setup.wing_angle_deg, 10.0)
        self.assertAlmostEqual(setup.static_front_rh_mm, 30.2)
        self.assertAlmostEqual(setup.static_rear_rh_mm, 43.7)
        self.assertAlmostEqual(setup.front_pushrod_mm, -37.5)
        self.assertAlmostEqual(setup.rear_pushrod_mm, -35.0)
        self.assertEqual(setup.front_roll_ls, 2)
        self.assertEqual(setup.rear_roll_hs, 6)
        self.assertEqual(setup.roof_light_color, "Purple")
        self.assertIn("sha256", setup.raw_sto_metadata)

    def test_current_setup_from_vrs_oracle(self):
        setup = CurrentSetup.from_sto(VRS_SEBRING_R1)
        self.assertEqual(setup.sto_provider_name, "vrs")
        self.assertAlmostEqual(setup.front_heave_nmm, 160.0)
        self.assertAlmostEqual(setup.rear_third_nmm, 160.0)
        self.assertAlmostEqual(setup.front_torsion_od_mm, 15.1)
        self.assertAlmostEqual(setup.rear_torsion_od_mm, 13.9)
        self.assertEqual(setup.tc_gain, 5)
        self.assertEqual(setup.tc_slip, 2)
        self.assertEqual(setup.diff_ramp_angles, "45/70")
        self.assertAlmostEqual(setup.diff_preload_nm, 70.0)
        self.assertAlmostEqual(setup.fuel_l, 58.0)

    def test_all_acura_files_decode_and_identify_car(self):
        for path in sorted(ACURA_DIR.glob("*.sto")):
            with self.subTest(path=path.name):
                decoded = decode_sto(path)
                self.assertEqual(decoded.version, 3)
                self.assertEqual(decoded.car_id, "acuraarx06gtp")

    def test_diff_row_keyset_stable_across_acura_corpus(self):
        expected = None
        for path in sorted(ACURA_DIR.glob("*.sto")):
            with self.subTest(path=path.name):
                rows = build_diff_rows(decode_sto(path), car="acura")
                keyset = [(row.row_id, row.label, row.tab, row.section) for row in rows]
                if expected is None:
                    expected = keyset
                self.assertEqual(keyset, expected)

    def test_unknown_raw_blocks_are_retained(self):
        decoded = decode_sto(ARA_HOCK_R1)
        kinds = {entry.kind for entry in decoded.raw_entries}
        self.assertIn("opaque_setup_blob", kinds)
        self.assertIn("payload_segment", kinds)
        self.assertTrue(decoded.notes_text)

    def test_known_acura_values_stay_inside_registry_ranges(self):
        checks = [
            (CurrentSetup.from_sto(P1DOKS_HOCK_R), {
                "wing_angle_deg": "wing_angle_deg",
                "front_heave_spring_nmm": "front_heave_nmm",
                "rear_third_spring_nmm": "rear_third_nmm",
                "front_arb_blade": "front_arb_blade",
                "rear_arb_blade": "rear_arb_blade",
                "front_torsion_od_mm": "front_torsion_od_mm",
                "rear_torsion_od_mm": "rear_torsion_od_mm",
                "front_roll_ls": "front_roll_ls",
                "rear_roll_hs": "rear_roll_hs",
                "brake_bias_pct": "brake_bias_pct",
            }),
            (CurrentSetup.from_sto(VRS_SEBRING_R1), {
                "front_heave_spring_nmm": "front_heave_nmm",
                "rear_third_spring_nmm": "rear_third_nmm",
                "front_torsion_od_mm": "front_torsion_od_mm",
                "rear_torsion_od_mm": "rear_torsion_od_mm",
                "front_roll_hs": "front_roll_hs",
                "rear_roll_ls": "rear_roll_ls",
                "tc_gain": "tc_gain",
                "tc_slip": "tc_slip",
                "fuel_l": "fuel_l",
                "diff_preload_nm": "diff_preload_nm",
            }),
        ]
        for setup, mapping in checks:
            for field_key, attr_name in mapping.items():
                with self.subTest(field_key=field_key, attr_name=attr_name):
                    spec = get_car_spec("acura", field_key)
                    self.assertIsNotNone(spec)
                    value = getattr(setup, attr_name)
                    if spec.range_min is not None:
                        self.assertGreaterEqual(value, spec.range_min)
                    if spec.range_max is not None:
                        self.assertLessEqual(value, spec.range_max)

    def test_from_ibt_and_from_sto_share_current_setup_model(self):
        class StubIBT:
            def __init__(self) -> None:
                self.session_info = {
                    "CarSetup": {
                        "TiresAero": {
                            "AeroSettings": {"RearWingAngle": "10.0 deg"},
                            "AeroCalculator": {},
                        },
                        "Chassis": {
                            "Front": {
                                "HeaveSpring": "180 N/mm",
                                "HeavePerchOffset": "34.5 mm",
                                "ToeIn": "-0.3 mm",
                                "PushrodLengthDelta": "-37.5 mm",
                                "ArbSize": "Medium",
                                "ArbBlades": "1",
                            },
                            "Rear": {
                                "HeaveSpring": "120 N/mm",
                                "HeavePerchOffset": "35.0 mm",
                                "ToeIn": "-0.2 mm",
                                "PushrodLengthDelta": "-35.0 mm",
                                "ArbSize": "Medium",
                                "ArbBlades": "2",
                            },
                            "LeftFront": {
                                "RideHeight": "30.2 mm",
                                "TorsionBarOD": "13.9 mm",
                                "Camber": "-2.8 deg",
                            },
                            "RightFront": {
                                "RideHeight": "30.2 mm",
                                "TorsionBarOD": "13.9 mm",
                                "Camber": "-2.8 deg",
                            },
                            "LeftRear": {
                                "RideHeight": "43.7 mm",
                                "TorsionBarOD": "13.9 mm",
                                "Camber": "-1.8 deg",
                            },
                            "RightRear": {
                                "RideHeight": "43.7 mm",
                                "TorsionBarOD": "13.9 mm",
                                "Camber": "-1.8 deg",
                            },
                        },
                        "Systems": {
                            "BrakeSpec": {
                                "BrakePressureBias": "47.00%",
                                "FrontMasterCyl": "20.6 mm",
                                "RearMasterCyl": "22.2 mm",
                                "PadCompound": "Medium",
                            },
                            "TractionControl": {
                                "TractionControlGain": "4",
                                "TractionControlSlip": "4",
                            },
                            "Lighting": {"RoofIdLightColor": "Purple"},
                            "GearRatios": {"GearStack": "Short"},
                        },
                        "Dampers": {
                            "FrontHeave": {
                                "LsCompDamping": "2 clicks",
                                "HsCompDamping": "2 clicks",
                                "HsCompDampSlope": "10 clicks",
                                "LsRbdDamping": "2 clicks",
                                "HsRbdDamping": "3 clicks",
                            },
                            "RearHeave": {
                                "LsCompDamping": "9 clicks",
                                "HsCompDamping": "8 clicks",
                                "HsCompDampSlope": "10 clicks",
                                "LsRbdDamping": "5 clicks",
                                "HsRbdDamping": "3 clicks",
                            },
                            "FrontRoll": {"LsDamping": "2 clicks", "HsDamping": "3 clicks"},
                            "RearRoll": {"LsDamping": "9 clicks", "HsDamping": "6 clicks"},
                        },
                    }
                }

        ibt_setup = CurrentSetup.from_ibt(StubIBT())
        sto_setup = CurrentSetup.from_sto(P1DOKS_HOCK_R)
        self.assertIsInstance(ibt_setup, CurrentSetup)
        self.assertIsInstance(sto_setup, CurrentSetup)
        self.assertEqual(ibt_setup.source, "ibt")
        self.assertEqual(sto_setup.source, "sto")
        self.assertEqual(ibt_setup.sto_sha256, "")
        self.assertTrue(sto_setup.sto_sha256)
        self.assertEqual(ibt_setup.adapter_name, "acura")
        self.assertTrue(sto_setup.adapter_name)


if __name__ == "__main__":
    unittest.main()
