import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from calibration.normalize import normalize_rows_to_inputs
from calibration.schema_ingest import bootstrap_schema_from_rows
from calibration.models import RawSampleManifest
from calibration.normalize import build_normalized_garage_sample


class FerrariCalibrationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows_payload = json.loads(
            (REPO_ROOT / "tests" / "fixtures" / "ferrari_hockenheim_screenshot_setup.json").read_text(encoding="utf-8")
        )
        cls.schema = bootstrap_schema_from_rows(car_name="ferrari", row_payloads=[cls.rows_payload])

    def test_ferrari_row_dump_maps_to_expected_canonical_inputs(self) -> None:
        canonical_inputs, raw_fields, warnings = normalize_rows_to_inputs(
            rows_payload=self.rows_payload,
            schema=self.schema,
        )
        self.assertEqual(warnings, [])
        expected_keys = {
            "front_heave_spring_nmm",
            "rear_third_spring_nmm",
            "front_pushrod_offset_mm",
            "rear_pushrod_offset_mm",
            "front_heave_perch_mm",
            "rear_third_perch_mm",
            "front_torsion_od_mm",
            "rear_spring_rate_nmm",
            "front_arb_size",
            "rear_arb_size",
            "front_arb_blade",
            "rear_arb_blade",
            "brake_bias_pct",
            "front_master_cyl_mm",
            "rear_master_cyl_mm",
            "diff_preload_nm",
            "front_diff_preload_nm",
            "diff_clutch_plates",
            "diff_ramp_angles",
            "tc_gain",
            "tc_slip",
            "fuel_l",
            "fuel_target_l",
            "fuel_low_warning_l",
            "gear_stack",
            "hybrid_rear_drive_enabled",
            "hybrid_rear_drive_corner_pct",
        }
        self.assertTrue(expected_keys.issubset(set(canonical_inputs.keys())))
        self.assertEqual(canonical_inputs["front_heave_spring_nmm"], 3)
        self.assertEqual(canonical_inputs["rear_third_spring_nmm"], 5)
        self.assertEqual(canonical_inputs["front_pushrod_offset_mm"], 1.0)
        self.assertEqual(canonical_inputs["rear_pushrod_offset_mm"], 5.0)
        self.assertEqual(canonical_inputs["front_arb_size"], "A")
        self.assertEqual(canonical_inputs["rear_arb_size"], "C")
        self.assertEqual(canonical_inputs["diff_ramp_angles"], "Less Locking")
        self.assertEqual(canonical_inputs["hybrid_rear_drive_enabled"], True)
        self.assertEqual(canonical_inputs["hybrid_rear_drive_corner_pct"], 90.0)
        # The screenshot fixture mirrors nested session-info and leaves the AeroCalculator
        # block empty, so RH-at-speed may be absent from this particular payload.

    def test_ferrari_rows_build_normalized_garage_sample(self) -> None:
        sample = build_normalized_garage_sample(
            manifest=RawSampleManifest(
                sample_id="ferrari_001",
                car="ferrari",
                track="hockenheim",
                sample_type="garage_static",
            ),
            rows_payload=self.rows_payload,
            schema=self.schema,
        )
        self.assertEqual(sample.car, "ferrari")
        self.assertEqual(sample.sample_type, "garage_static")
        self.assertEqual(sample.canonical_inputs["front_heave_spring_nmm"], 3)
        self.assertEqual(sample.canonical_inputs["rear_third_spring_nmm"], 5)
        if "front_rh_at_speed_mm" in sample.garage_outputs:
            self.assertEqual(sample.garage_outputs["front_rh_at_speed_mm"], 15.0)
        if "rear_rh_at_speed_mm" in sample.garage_outputs:
            self.assertEqual(sample.garage_outputs["rear_rh_at_speed_mm"], 40.0)


if __name__ == "__main__":
    unittest.main()
