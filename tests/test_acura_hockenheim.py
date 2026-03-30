"""Tests for Acura ARX-06 + Hockenheim Grand Prix wiring.

Validates that the Acura car model, Hockenheim track profile, solver pipeline,
aero maps, .sto output, and validation matrix are all correctly wired.
"""

import json
import math
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from aero_model import load_car_surfaces
from car_model.cars import get_car
from car_model.setup_registry import CAR_FIELD_SPECS, get_car_spec, iter_fields
from solver.solve import find_track_profile
from track_model.generic_profiles import _KNOWN_TRACKS
from track_model.profile import TrackProfile

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── 8a. Registry & Schema Tests ─────────────────────────────────────────────


class AcuraRegistryTests(unittest.TestCase):
    """Validate Acura setup registry wiring."""

    def test_acura_in_car_registry(self):
        car = get_car("acura")
        self.assertEqual(car.canonical_name, "acura")
        self.assertEqual(car.name, "Acura ARX-06")

    def test_acura_field_specs_exist(self):
        self.assertIn("acura", CAR_FIELD_SPECS)

    def test_acura_covers_settable_fields(self):
        settable = iter_fields(kind="settable")
        acura = CAR_FIELD_SPECS["acura"]
        missing = [
            f.canonical_key
            for f in settable
            if f.canonical_key not in acura
            and f.canonical_key != "front_diff_preload_nm"  # Ferrari-only
        ]
        self.assertEqual(missing, [], f"Acura missing settable fields: {missing}")

    def test_acura_arb_blade_sto_ids(self):
        spec = get_car_spec("acura", "front_arb_blade")
        self.assertIsNotNone(spec)
        self.assertIn("ArbBlades[0]", spec.sto_param_id)

    def test_acura_param_ids_in_sto_writer(self):
        from output.setup_writer import _CAR_PARAM_IDS

        self.assertIn("acura", _CAR_PARAM_IDS)
        ids = _CAR_PARAM_IDS["acura"]
        self.assertIn("wing_angle", ids)
        self.assertIn("front_heave_spring", ids)

    def test_acura_wing_range(self):
        car = get_car("acura")
        self.assertEqual(
            car.wing_angles,
            [6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0],
        )

    def test_acura_oreca_platform_consistency(self):
        """Acura keeps the ORECA heave+roll + rear torsion-bar layout encoded in the car model."""
    def test_acura_dallara_platform_consistency(self):
        """Acura shares Dallara platform values with BMW (torsion_c, CG height).

        Note: rear_motion_ratio intentionally differs — Acura ORECA rear torsion geometry
        bakes the motion ratio into the spring constant (ratio=1.0), while BMW uses
        a pushrod geometry (ratio=0.60). This is correct and by design.
        """
        acura = get_car("acura")
        bmw = get_car("bmw")
        self.assertEqual(
            acura.corner_spring.front_torsion_c,
            bmw.corner_spring.front_torsion_c,
        )
        self.assertTrue(acura.corner_spring.rear_is_torsion_bar)
        self.assertEqual(acura.corner_spring.rear_motion_ratio, 1.0)
        self.assertEqual(
            acura.corner_spring.cg_height_mm,
            350.0,
        )
        self.assertTrue(acura.damper.has_roll_dampers)


# ── 8b. Track Profile Tests ─────────────────────────────────────────────────


class HockenheimProfileTests(unittest.TestCase):
    """Validate Hockenheim generic track profile."""

    def test_hockenheim_in_known_tracks(self):
        self.assertIn("hockenheim", _KNOWN_TRACKS)

    def test_hockenheim_classified_smooth_mixed(self):
        roughness, style = _KNOWN_TRACKS["hockenheim"]
        self.assertEqual(roughness, "smooth")
        self.assertEqual(style, "mixed")

    def test_hockenheim_profile_loads(self):
        profile_path = REPO_ROOT / "data" / "tracks" / "hockenheim_grand_prix.json"
        self.assertTrue(profile_path.exists(), f"Missing: {profile_path}")
        profile = TrackProfile.load(profile_path)
        self.assertIn("Hockenheim", profile.track_name)

    def test_hockenheim_profile_values_sensible(self):
        profile = TrackProfile.load(
            REPO_ROOT / "data" / "tracks" / "hockenheim_grand_prix.json"
        )
        self.assertGreater(profile.track_length_m, 4000)
        self.assertLess(profile.track_length_m, 5000)
        self.assertGreater(profile.median_speed_kph, 150)
        self.assertLess(profile.median_speed_kph, 230)
        self.assertGreater(profile.shock_vel_p99_front_mps, 0)

    def test_find_track_profile_resolves_hockenheim(self):
        profile = find_track_profile("hockenheim")
        self.assertIn("Hockenheim", profile.track_name)


# ── 8c. Solver End-to-End Tests ─────────────────────────────────────────────


class AcuraSolverSmokeTests(unittest.TestCase):
    """End-to-end solver smoke tests for Acura + Hockenheim via CLI."""

    def _run_cli(self, wing: float, extra_args: list[str] | None = None) -> str:
        """Run the solver CLI and return stdout."""
        import subprocess
        cmd = [
            sys.executable, "-m", "solver.solve",
            "--car", "acura", "--track", "hockenheim",
            "--wing", str(wing), "--json",
        ]
        if extra_args:
            cmd.extend(extra_args)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(result.returncode, 0, f"Solver failed:\n{result.stderr}")
        return result.stdout

    def _parse_json(self, output: str) -> dict:
        """Extract JSON object from CLI output (skip preamble text)."""
        idx = output.index("{")
        return json.loads(output[idx:])

    def test_solver_completes_wing_8(self):
        output = self._run_cli(8.0)
        self.assertIn('"step1_rake"', output)
        self.assertIn('"step6_dampers"', output)

    def test_solver_completes_wing_extremes(self):
        for wing in [6.0, 10.0]:
            with self.subTest(wing=wing):
                output = self._run_cli(wing)
                self.assertIn('"step1_rake"', output)

    def test_solver_output_values_sensible(self):
        output = self._run_cli(8.0)
        data = self._parse_json(output)
        step1 = data["step1_rake"]
        self.assertGreater(step1["dynamic_front_rh_mm"], 0)
        self.assertGreater(step1["dynamic_rear_rh_mm"], 0)
        self.assertLess(step1["dynamic_front_rh_mm"], 50)
        self.assertLess(step1["dynamic_rear_rh_mm"], 60)
        self.assertAlmostEqual(step1["df_balance_pct"], 49.0, delta=1.0)


# ── 8d. .sto Output Tests ───────────────────────────────────────────────────


class AcuraStoOutputTests(unittest.TestCase):
    """Validate .sto file generation for Acura via CLI."""

    def test_sto_write_completes(self):
        import subprocess
        with TemporaryDirectory() as tmp:
            sto_path = Path(tmp) / "acura_test.sto"
            result = subprocess.run(
                [sys.executable, "-m", "solver.solve",
                 "--car", "acura", "--track", "hockenheim",
                  "--wing", "8", "--sto", str(sto_path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO_ROOT),
            )
            self.assertEqual(result.returncode, 0, f"Solver failed:\n{result.stderr}")
            self.assertTrue(sto_path.exists())
            content = sto_path.read_text()
            self.assertIn("CarSetup_", content)
            self.assertGreater(len(content), 500)

    def test_sto_contains_valid_xml(self):
        import subprocess
        import xml.etree.ElementTree as ET

        with TemporaryDirectory() as tmp:
            sto_path = Path(tmp) / "acura_xml.sto"
            result = subprocess.run(
                [sys.executable, "-m", "solver.solve",
                 "--car", "acura", "--track", "hockenheim",
                  "--wing", "8", "--sto", str(sto_path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO_ROOT),
            )
            self.assertEqual(result.returncode, 0, f"Solver failed:\n{result.stderr}")
            tree = ET.parse(sto_path)
            root = tree.getroot()
            self.assertEqual(root.tag, "LDXFile")


# ── 8e. Aero Map Tests ──────────────────────────────────────────────────────


class AcuraAeroMapTests(unittest.TestCase):
    """Validate Acura aero map data loads and interpolates."""

    def test_acura_aero_parsed_files_exist(self):
        self.assertTrue(
            (REPO_ROOT / "data" / "aeromaps_parsed" / "acura_aero.json").exists()
        )
        self.assertTrue(
            (REPO_ROOT / "data" / "aeromaps_parsed" / "acura_aero.npz").exists()
        )

    def test_acura_aero_loads(self):
        surfaces = load_car_surfaces("acura")
        self.assertGreater(len(surfaces), 0)

    def test_acura_aero_interpolation(self):
        surfaces = load_car_surfaces("acura")
        surface = surfaces[8.0]
        balance = surface.df_balance(front_rh=35.0, rear_rh=40.0)
        self.assertGreater(balance, 30)
        self.assertLess(balance, 70)

    def test_acura_aero_covers_wing_range(self):
        surfaces = load_car_surfaces("acura")
        for wing in [6.0, 8.0, 10.0]:
            with self.subTest(wing=wing):
                self.assertIn(wing, surfaces)
                balance = surfaces[wing].df_balance(front_rh=35.0, rear_rh=40.0)
                self.assertFalse(math.isnan(balance))


# ── 8f. Validation Matrix Tests ──────────────────────────────────────────────


class ValidationMatrixTests(unittest.TestCase):
    """Validate the support matrix includes Acura/Hockenheim."""

    def test_acura_hockenheim_in_support_matrix(self):
        with open(REPO_ROOT / "validation" / "objective_validation.json") as f:
            data = json.load(f)
        acura_entries = [e for e in data["support_matrix"] if e["car"] == "acura"]
        self.assertGreaterEqual(len(acura_entries), 1)
        hock = [e for e in acura_entries if "Hockenheim" in e["track"]]
        self.assertEqual(len(hock), 1)
        self.assertEqual(hock[0]["confidence_tier"], "unsupported")
        self.assertEqual(hock[0]["samples"], 0)

    def test_acura_in_sample_counts(self):
        with open(REPO_ROOT / "validation" / "objective_validation.json") as f:
            data = json.load(f)
        self.assertIn("acura", data["sample_counts_by_car_track"])


if __name__ == "__main__":
    unittest.main()
