import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from calibration.dataset_validation import validate_raw_dataset
from calibration.scaffold import build_sample_pack, generate_registry_seed_schema, write_schema_seed_files


class CalibrationScaffoldTests(unittest.TestCase):
    def test_generate_registry_seed_schema(self) -> None:
        schema = generate_registry_seed_schema("ferrari")
        self.assertEqual(schema.car_name, "ferrari")
        self.assertGreater(len(schema.fields), 10)
        self.assertIn("seeded_from_setup_registry_only", schema.warnings)

    def test_write_schema_seed_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            written = write_schema_seed_files(tmpdir, cars=["bmw", "ferrari"])
            self.assertEqual(len(written), 2)
            self.assertTrue((Path(tmpdir) / "bmw.json").exists())
            self.assertTrue((Path(tmpdir) / "ferrari.json").exists())

    def test_build_sample_pack(self) -> None:
        with TemporaryDirectory() as tmpdir:
            result = build_sample_pack(
                car="ferrari",
                track="sebring",
                track_config="international_raceway",
                sample_id="sample_0001",
                sample_type="telemetry",
                output_root=tmpdir,
            )
            sample_dir = Path(result["sample_dir"])
            self.assertTrue((sample_dir / "manifest.json").exists())
            self.assertTrue((sample_dir / "setup_rows.json").exists())
            self.assertTrue((sample_dir / "measured.json").exists())
            manifest = json.loads((sample_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["car"], "ferrari")
            self.assertEqual(manifest["sample_type"], "telemetry")

    def test_validate_raw_dataset(self) -> None:
        with TemporaryDirectory() as tmpdir:
            result = build_sample_pack(
                car="bmw",
                track="sebring",
                track_config="international_raceway",
                sample_id="sample_0001",
                sample_type="garage_static",
                output_root=tmpdir,
            )
            sample_dir = Path(result["sample_dir"])
            # Create the optional placeholder files so validation passes.
            (sample_dir / "session.ibt").write_text("", encoding="utf-8")
            (sample_dir / "setup.sto").write_text("", encoding="utf-8")
            for screenshot in (
                sample_dir / "screenshots" / "tires_aero.png",
                sample_dir / "screenshots" / "chassis_front.png",
                sample_dir / "screenshots" / "chassis_rear.png",
                sample_dir / "screenshots" / "dampers.png",
                sample_dir / "screenshots" / "systems.png",
            ):
                screenshot.write_text("", encoding="utf-8")
            report = validate_raw_dataset(tmpdir)
            self.assertEqual(report["manifest_count"], 1)
            self.assertEqual(report["invalid_count"], 0)
            self.assertEqual(report["valid_count"], 1)


if __name__ == "__main__":
    unittest.main()
