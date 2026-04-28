"""W5.2 — analyzer/setup_reader, setup_schema, sto_adapters GT3 dispatch tests.

Covers BMW M4 GT3 EVO, Aston Martin Vantage GT3 EVO, and Porsche 911 GT3 R (992)
parsing of session-info YAML fixtures plus the schema field-id maps and
sto_binary GT3 filename hint catalog.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analyzer.setup_reader import CurrentSetup, GT3_CANONICALS  # noqa: E402
from analyzer.setup_schema import _GT3_KNOWN_FIELD_MAP, get_known_fields  # noqa: E402
from analyzer.sto_binary import _CAR_HINTS, _infer_car_id  # noqa: E402


GT3_YAMLS = {
    "bmw_m4_gt3": REPO_ROOT / "docs" / "gt3_session_info_bmw_m4_gt3_spielberg_2026-04-26.yaml",
    "aston_martin_vantage_gt3": REPO_ROOT / "docs" / "gt3_session_info_aston_vantage_spielberg_2026-04-26.yaml",
    "porsche_992_gt3r": REPO_ROOT / "docs" / "gt3_session_info_porsche_992_gt3r_spielberg_2026-04-26.yaml",
}


class _FakeIBT:
    """Stand-in for an IBT file that just exposes ``session_info``."""

    def __init__(self, session_info: dict) -> None:
        self.session_info = session_info


def _load_gt3_session_info(car_canonical: str) -> dict:
    """Wrap the GT3 sample YAML inside a ``CarSetup`` envelope (matches IBT)."""
    raw = yaml.safe_load(GT3_YAMLS[car_canonical].read_text(encoding="utf-8"))
    return {"CarSetup": raw}


class GT3SetupReaderTests(unittest.TestCase):
    """Per-car YAML→CurrentSetup parsing checks."""

    def test_bmw_m4_gt3_yaml_parses_settable_values(self) -> None:
        si = _load_gt3_session_info("bmw_m4_gt3")
        setup = CurrentSetup.from_ibt(_FakeIBT(si), car_canonical="bmw_m4_gt3")

        self.assertEqual(setup.adapter_name, "bmw_m4_gt3")
        # Per-corner spring rate (LF + RF avg from BMW Spielberg fixture).
        self.assertEqual(setup.front_corner_spring_nmm, 252.0)
        self.assertEqual(setup.rear_spring_nmm, 179.0)
        # ARB blades read from FrontBrakes.ArbBlades / Rear.ArbBlades.
        self.assertEqual(setup.front_arb_blade, 5)
        self.assertEqual(setup.rear_arb_blade, 4)
        # Paired front toe under FrontBrakes.TotalToeIn.
        self.assertEqual(setup.front_toe_mm, -2.8)
        # Per-axle dampers under Dampers.FrontDampers / Dampers.RearDampers.
        self.assertEqual(setup.front_ls_comp, 7)
        self.assertEqual(setup.front_hs_comp, 3)
        self.assertEqual(setup.front_ls_rbd, 5)
        self.assertEqual(setup.front_hs_rbd, 3)
        self.assertEqual(setup.rear_ls_comp, 6)
        self.assertEqual(setup.rear_hs_comp, 4)
        # TC / ABS — parse "X (TC)" / "X (ABS)" labels.
        self.assertEqual(setup.tc_setting, 4)
        self.assertEqual(setup.abs_setting, 6)
        # Fuel under Chassis.Rear.FuelLevel for BMW.
        self.assertEqual(setup.fuel_l, 100.0)
        # Splitter height + brake bias + diff preload.
        self.assertEqual(setup.splitter_height_mm, 70.0)
        self.assertEqual(setup.brake_bias_pct, 52.0)
        self.assertEqual(setup.diff_preload_nm, 100.0)
        # Heave/torsion fields stay zero — GT3 has no such elements.
        self.assertEqual(setup.front_heave_nmm, 0.0)
        self.assertEqual(setup.front_torsion_od_mm, 0.0)
        self.assertEqual(setup.rear_third_nmm, 0.0)

    def test_aston_vantage_gt3_yaml_parses_settable_values(self) -> None:
        si = _load_gt3_session_info("aston_martin_vantage_gt3")
        setup = CurrentSetup.from_ibt(
            _FakeIBT(si), car_canonical="aston_martin_vantage_gt3"
        )

        self.assertEqual(setup.adapter_name, "aston_martin_vantage_gt3")
        self.assertEqual(setup.front_corner_spring_nmm, 200.0)
        self.assertEqual(setup.rear_spring_nmm, 180.0)
        # Aston uses FarbBlades / RarbBlades.
        self.assertEqual(setup.front_arb_blade, 5)
        self.assertEqual(setup.rear_arb_blade, 5)
        # No "front_arb_setting" for Aston.
        self.assertEqual(setup.front_arb_setting, 0)
        # Paired front toe under FrontBrakesLights.
        self.assertEqual(setup.front_toe_mm, -3.0)
        # TC label on Aston is "X (TC SLIP)".
        self.assertEqual(setup.tc_setting, 5)
        self.assertEqual(setup.abs_setting, 5)
        # Aston-specific extras.
        self.assertEqual(setup.epas_setting, 3)
        self.assertEqual(setup.throttle_map, 4)
        # Fuel under Chassis.Rear.FuelLevel for Aston.
        self.assertEqual(setup.fuel_l, 106.0)
        self.assertEqual(setup.splitter_height_mm, 70.4)
        # Aero balance source is "AeroBalanceCalculator" (note the trailing -ator).
        self.assertEqual(setup.df_balance_pct, 40.5)
        # Wing angle field for Aston is RearWingAngle.
        self.assertEqual(setup.wing_angle_deg, 5.0)
        # No heave / torsion elements.
        self.assertEqual(setup.front_heave_nmm, 0.0)
        self.assertEqual(setup.front_torsion_od_mm, 0.0)

    def test_porsche_992_gt3r_yaml_parses_settable_values(self) -> None:
        si = _load_gt3_session_info("porsche_992_gt3r")
        setup = CurrentSetup.from_ibt(_FakeIBT(si), car_canonical="porsche_992_gt3r")

        self.assertEqual(setup.adapter_name, "porsche_992_gt3r")
        self.assertEqual(setup.front_corner_spring_nmm, 220.0)
        self.assertEqual(setup.rear_spring_nmm, 260.0)
        # Porsche uses ArbSetting / RarbSetting (single integer, NOT blade).
        self.assertEqual(setup.front_arb_setting, 7)
        self.assertEqual(setup.rear_arb_setting, 7)
        # Paired front toe under FrontBrakesLights.TotalToeIn.
        self.assertEqual(setup.front_toe_mm, -3.9)
        # Porsche rear toe is paired at Chassis.Rear.TotalToeIn (NOT per-wheel).
        self.assertEqual(setup.rear_toe_mm, 3.0)
        # TC label on Porsche is "X (TC-LAT)".
        self.assertEqual(setup.tc_setting, 3)
        self.assertEqual(setup.abs_setting, 5)
        # Porsche fuel lives under FrontBrakesLights.FuelLevel (NOT Chassis.Rear).
        self.assertEqual(setup.fuel_l, 99.0)
        self.assertEqual(setup.splitter_height_mm, 76.6)
        # ThrottleShapeSetting plain integer under InCarAdjustments (NOT label).
        self.assertEqual(setup.throttle_map, 3)
        # Wing angle field for Porsche is WingSetting.
        self.assertEqual(setup.wing_angle_deg, 5.7)
        # No heave / torsion elements.
        self.assertEqual(setup.front_heave_nmm, 0.0)
        self.assertEqual(setup.front_torsion_od_mm, 0.0)

    def test_summary_renders_without_heave_for_gt3(self) -> None:
        si = _load_gt3_session_info("bmw_m4_gt3")
        setup = CurrentSetup.from_ibt(_FakeIBT(si), car_canonical="bmw_m4_gt3")
        line = setup.summary()
        self.assertIn("Spring F252", line)
        self.assertIn("FARB 5", line)
        # Must NOT contain heave/0/0 confusion text (no GT3 cars have heave springs).
        self.assertNotIn("Heave 0/0", line)


class GTPRegressionTests(unittest.TestCase):
    """Ensure GTP code paths still work after the GT3 dispatch was added."""

    def test_ferrari_fixture_still_parses_with_gtp_path(self) -> None:
        fixture_path = REPO_ROOT / "tests" / "fixtures" / "ferrari_hockenheim_screenshot_setup.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        # Ferrari fixture is the JSON-encoded session info dict (no top-level CarSetup wrapper).
        setup = CurrentSetup.from_ibt(_FakeIBT(fixture))

        # Adapter classification + heave/torsion fields populated as before.
        self.assertEqual(setup.adapter_name, "ferrari")
        # Ferrari heave / torsion bar values from existing fixture.
        self.assertEqual(setup.front_heave_nmm, 3.0)
        self.assertEqual(setup.rear_third_nmm, 5.0)
        self.assertEqual(setup.front_torsion_od_mm, 2.0)


class GT3SetupSchemaTests(unittest.TestCase):
    """Schema-side checks: GT3 LDX field-id maps surface the right keys."""

    def test_get_known_fields_returns_gt3_map_for_bmw_m4_gt3(self) -> None:
        fields = get_known_fields("bmw_m4_gt3")
        self.assertIn("CarSetup_Chassis_LeftFront_SpringRate", fields)
        self.assertIn("CarSetup_Chassis_FrontBrakes_ArbBlades", fields)
        self.assertIn(
            "CarSetup_Dampers_FrontDampers_LowSpeedCompressionDamping", fields
        )
        # Front spring rate maps to the GT3 attribute.
        self.assertEqual(
            fields["CarSetup_Chassis_LeftFront_SpringRate"][0],
            "front_corner_spring_nmm",
        )

    def test_get_known_fields_returns_aston_specific_arb(self) -> None:
        fields = get_known_fields("aston_martin_vantage_gt3")
        self.assertIn("CarSetup_Chassis_FrontBrakesLights_FarbBlades", fields)
        self.assertIn("CarSetup_Chassis_Rear_RarbBlades", fields)
        # Aston has Throttle/Epas under InCarAdjustments.
        self.assertIn(
            "CarSetup_Chassis_InCarAdjustments_ThrottleResponse", fields
        )

    def test_get_known_fields_returns_porsche_specific_arb_and_paired_rear_toe(self) -> None:
        fields = get_known_fields("porsche_992_gt3r")
        self.assertIn("CarSetup_Chassis_FrontBrakesLights_ArbSetting", fields)
        self.assertIn("CarSetup_Chassis_Rear_RarbSetting", fields)
        # Porsche-specific paired rear toe.
        self.assertIn("CarSetup_Chassis_Rear_TotalToeIn", fields)
        # Porsche fuel under FrontBrakesLights.
        self.assertIn("CarSetup_Chassis_FrontBrakesLights_FuelLevel", fields)

    def test_get_known_fields_falls_back_to_gtp_for_unknown_car(self) -> None:
        # GTP map still surfaces heave / torsion / Systems-block field-ids.
        fields = get_known_fields("bmw")
        self.assertIn("CarSetup_Chassis_Front_HeaveSpring", fields)
        self.assertIn("CarSetup_Chassis_LeftFront_TorsionBarOD", fields)
        # GT3 keys must NOT appear in the GTP map.
        self.assertNotIn(
            "CarSetup_Dampers_FrontDampers_LowSpeedCompressionDamping", fields
        )

    def test_gt3_known_field_map_covers_all_three_cars(self) -> None:
        for car in GT3_CANONICALS:
            self.assertIn(car, _GT3_KNOWN_FIELD_MAP)
            self.assertGreater(len(_GT3_KNOWN_FIELD_MAP[car]), 20)


class GT3StoBinaryHintTests(unittest.TestCase):
    """sto_binary regex catalog must recognise GT3 STO filenames."""

    def test_bmwm4gt3_filename_resolves_to_gt3_id(self) -> None:
        # Pattern alone — short stem.
        self.assertEqual(_infer_car_id("bmwm4gt3-spielberg-stintsetup", ""), "bmwm4gt3")

    def test_amvantageevogt3_filename_resolves_to_gt3_id(self) -> None:
        self.assertEqual(
            _infer_car_id("amvantageevogt3-spielberg-quick", ""),
            "amvantageevogt3",
        )

    def test_porsche992rgt3_filename_resolves_to_gt3_id(self) -> None:
        self.assertEqual(
            _infer_car_id("porsche992rgt3-spielberg-baseline", ""),
            "porsche992rgt3",
        )

    def test_porsche963_lmdh_still_resolves_to_gtp_porsche_id(self) -> None:
        # Must not be hijacked by the more general porsche regex sitting after GT3.
        self.assertEqual(
            _infer_car_id("porsche963-sebring", ""), "porsche963"
        )

    def test_bmw_lmdh_still_resolves_to_gtp_bmw_id(self) -> None:
        # GTP BMW LMDh stays on the legacy bmwlmdh path.
        self.assertEqual(_infer_car_id("bmwlmdh-sebring", ""), "bmwlmdh")

    def test_gt3_hints_are_first_in_catalog(self) -> None:
        # Defense-in-depth: ensure the GT3 hints come before the bare "bmw"/"porsche"
        # patterns so the longer-match-first rule still holds.
        ids = [car_id for _, car_id in _CAR_HINTS]
        self.assertLess(ids.index("bmwm4gt3"), ids.index("bmwlmdh"))
        self.assertLess(ids.index("porsche992rgt3"), ids.index("porsche963"))


if __name__ == "__main__":
    unittest.main()
