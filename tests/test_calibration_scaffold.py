import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from calibration.dataset_validation import validate_raw_dataset
from calibration.normalize import build_normalized_garage_sample
from calibration.models import RawSampleManifest
from calibration.scaffold import build_sample_pack, generate_registry_seed_schema, write_schema_seed_files
from calibration.schema_ingest import bootstrap_schema_from_rows


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

    def test_ferrari_fixture_normalizes_into_canonical_inputs(self) -> None:
        fixture_path = REPO_ROOT / "tests" / "fixtures" / "ferrari_hockenheim_screenshot_setup.json"
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        schema = bootstrap_schema_from_rows(car_name="ferrari", row_payloads=[payload])
        sample = build_normalized_garage_sample(
            manifest=RawSampleManifest(
                sample_id="fixture",
                car="ferrari",
                track="hockenheim",
            ),
            rows_payload=payload,
            schema=schema,
        )
        self.assertIn("front_pushrod_offset_mm", sample.canonical_inputs)
        self.assertIn("rear_pushrod_offset_mm", sample.canonical_inputs)
        self.assertIn("front_heave_spring_nmm", sample.canonical_inputs)
        self.assertIn("rear_third_spring_nmm", sample.canonical_inputs)
        self.assertIn("front_arb_size", sample.canonical_inputs)
        self.assertIn("rear_arb_size", sample.canonical_inputs)
        self.assertIn("diff_preload_nm", sample.canonical_inputs)
        self.assertIn("front_diff_preload_nm", sample.canonical_inputs)
        self.assertIn("tc_gain", sample.canonical_inputs)
        self.assertIn("tc_slip", sample.canonical_inputs)

    def test_ferrari_dataset_validator_reports_required_sweep_hints(self) -> None:
        with TemporaryDirectory() as tmpdir:
            result = build_sample_pack(
                car="ferrari",
                track="sebring",
                track_config="international_raceway",
                sample_id="sample_0001",
                sample_type="garage_static",
                output_root=tmpdir,
            )
            sample_dir = Path(result["sample_dir"])
            payload = {
                "carName": "ferrari",
                "rows": [
                    {
                        "row_id": "pushrod_front",
                        "label": "Pushrod length delta",
                        "tab": "Chassis",
                        "section": "Front",
                        "metric_value": "1.0 mm",
                        "imperial_value": "0.039 in",
                        "is_mapped": True,
                        "is_derived": False,
                    },
                    {
                        "row_id": "pushrod_rear",
                        "label": "Pushrod length delta",
                        "tab": "Chassis",
                        "section": "Rear",
                        "metric_value": "5.0 mm",
                        "imperial_value": "0.197 in",
                        "is_mapped": True,
                        "is_derived": False,
                    },
                    {
                        "row_id": "heave_front",
                        "label": "Heave spring",
                        "tab": "Chassis",
                        "section": "Front",
                        "metric_value": "3",
                        "imperial_value": "3",
                        "is_mapped": True,
                        "is_derived": False,
                    },
                ],
            }
            (sample_dir / "setup_rows.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
            report = validate_raw_dataset(tmpdir)
            self.assertEqual(report["manifest_count"], 1)
            self.assertEqual(report["valid_count"], 1)
            self.assertEqual(report["samples"][0]["issues"], [])


if __name__ == "__main__":
    unittest.main()
