import argparse
import contextlib
import io
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

from analyzer.setup_reader import CurrentSetup
from car_model import get_car
from car_model.garage import GarageSetupState
from output.setup_writer import write_sto
from pipeline.produce import produce_result
from track_model.ibt_parser import IBTFile


MARCH_11 = Path(r"C:\Users\tfunk\IOptimal\ibtfiles\bmwlmdh_sebring international 2026-03-11 20-40-35.ibt")
MARCH_14 = Path(r"C:\Users\tfunk\IOptimal\ibtfiles\bmwlmdh_sebring international 2026-03-14 09-44-24.ibt")


@unittest.skipUnless(MARCH_11.exists() and MARCH_14.exists(), "BMW/Sebring IBT fixtures not available")
class BMWSebringGarageTruthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.car = get_car("bmw")
        cls.garage_model = cls.car.active_garage_output_model("sebring")
        if cls.garage_model is None:
            raise unittest.SkipTest("BMW/Sebring garage model not configured")

    def _assert_fixture(self, path: Path, expected: dict[str, float]) -> None:
        setup = CurrentSetup.from_ibt(IBTFile(path))
        outputs = self.garage_model.predict(
            GarageSetupState.from_current_setup(setup),
            front_excursion_p99_mm=0.0,
        )
        self.assertAlmostEqual(outputs.front_static_rh_mm, expected["front_rh"], delta=0.25)
        self.assertAlmostEqual(outputs.rear_static_rh_mm, expected["rear_rh"], delta=0.25)
        self.assertAlmostEqual(outputs.torsion_bar_turns, expected["tb_turns"], delta=0.003)
        self.assertAlmostEqual(outputs.heave_slider_defl_static_mm, expected["slider"], delta=0.75)
        self.assertAlmostEqual(outputs.heave_spring_defl_static_mm, expected["heave_defl"], delta=0.75)

    def test_march_11_fixture_round_trips(self):
        self._assert_fixture(MARCH_11, {
            "front_rh": 30.1,
            "rear_rh": 49.5,
            "tb_turns": 0.096,
            "slider": 42.5,
            "heave_defl": 9.4,
        })

    def test_march_14_fixture_round_trips(self):
        self._assert_fixture(MARCH_14, {
            "front_rh": 30.2,
            "rear_rh": 49.7,
            "tb_turns": 0.101,
            "slider": 43.4,
            "heave_defl": 12.8,
        })

    def test_garage_constraints_fail_on_floor_and_slider_violations(self):
        base = GarageSetupState.from_current_setup(CurrentSetup.from_ibt(IBTFile(MARCH_14)))

        below_floor = replace(base, front_pushrod_mm=-40.0)
        floor_result = self.garage_model.validate(below_floor)
        self.assertFalse(floor_result.valid)
        self.assertFalse(floor_result.front_static_rh_ok)

        over_slider = replace(
            base,
            front_pushrod_mm=-15.0,
            front_heave_perch_mm=-5.0,
            front_heave_nmm=30.0,
        )
        slider_result = self.garage_model.validate(over_slider)
        self.assertFalse(slider_result.valid)
        self.assertFalse(slider_result.heave_slider_ok)

    def test_pipeline_report_and_writer_use_unified_garage_outputs(self):
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        args = argparse.Namespace(
            car="bmw",
            ibt=str(MARCH_14),
            wing=17.0,
            lap=None,
            balance=50.14,
            tolerance=0.1,
            fuel=None,
            free=False,
            sto=None,
            json=None,
            report_only=True,
            no_learn=True,
            legacy_solver=False,
            min_lap_time=108.0,
            outlier_pct=0.115,
            stint_laps=30,
        )
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            result = produce_result(args)
        output = captured.getvalue()
        report = result["report"]
        self.assertEqual(output.strip(), report.strip())
        self.assertIn("SETUP TO ENTER", report)
        self.assertIn("DF bal: 50.14%  ✓", report)
        self.assertIn("Heave slider:", report)
        self.assertIn("TC gain / slip", report)
        self.assertIn("Tyre cold FL / FR", report)
        self.assertIn("LF HS comp / rbd / slope", report)
        self.assertNotIn("DRIVER PROFILE", report)
        self.assertNotIn("CURRENT vs RECOMMENDED", report)
        self.assertNotIn("FRONT HEAVE TRAVEL BUDGET", report)
        self.assertGreaterEqual(result["step1"].static_front_rh_mm, 30.0)
        self.assertLessEqual(result["step2"].slider_static_front_mm, 45.0)

        expected_outputs = self.garage_model.predict(
            GarageSetupState.from_solver_steps(
                step1=result["step1"],
                step2=result["step2"],
                step3=result["step3"],
                step5=result["step5"],
                fuel_l=result["current_setup"].fuel_l,
            ),
            front_excursion_p99_mm=result["step2"].front_excursion_at_rate_mm,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            sto_path = Path(tmpdir) / "bmw_sebring_report.sto"
            write_sto(
                car_name=self.car.name,
                track_name="Sebring International — International",
                wing=17.0,
                fuel_l=result["current_setup"].fuel_l,
                step1=result["step1"],
                step2=result["step2"],
                step3=result["step3"],
                step4=result["step4"],
                step5=result["step5"],
                step6=result["step6"],
                output_path=sto_path,
                car_canonical="bmw",
                include_computed=True,
            )
            root = ET.parse(sto_path).getroot()
            nodes = root.findall(".//Numeric[@Id='CarSetup_Chassis_LeftFront_TorsionBarTurns']")
            self.assertEqual(len(nodes), 1)
            writer_turns = float(nodes[0].attrib["Value"])
            self.assertAlmostEqual(writer_turns, expected_outputs.torsion_bar_turns, delta=0.001)
