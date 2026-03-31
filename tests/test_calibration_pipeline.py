import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from calibration.cli import build_parser
from calibration.fit_damper_model import fit_damper_model
from calibration.fit_diff_model import fit_diff_model
from calibration.fit_garage_model import fit_garage_model
from calibration.fit_ride_height_model import fit_ride_height_model
from calibration.fit_telemetry_model import fit_telemetry_model
from calibration.models import (
    CalibrationReport,
    FittedModelArtifact,
    NormalizedGarageSample,
    NormalizedTelemetrySample,
    RawSampleManifest,
    SetupSchemaFile,
)
from calibration.publish_models import publish_models
from calibration.sample_ingest import ingest_sample_dir, ingest_sample_tree, write_jsonl
from calibration.schema_ingest import bootstrap_schema_from_rows, validate_schema_coverage
from calibration.validate_models import build_validation_report


def _row(label, metric_value, **extra):
    payload = {
        "row_id": extra.pop("row_id", label.lower().replace(" ", "_")),
        "label": label,
        "metric_value": metric_value,
        "imperial_value": extra.pop("imperial_value", metric_value),
        "range_metric": extra.pop("range_metric", None),
        "range_imperial": extra.pop("range_imperial", None),
        "is_mapped": extra.pop("is_mapped", True),
        "is_derived": extra.pop("is_derived", False),
        "tab": extra.pop("tab", "Chassis"),
        "section": extra.pop("section", "Front"),
    }
    payload.update(extra)
    return payload


class CalibrationPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows_payload = {
            "carName": "ferrari",
            "rows": [
                _row("Heave spring", "3", row_id="front_heave", tab="Chassis", section="Front"),
                _row("Pushrod length delta", "1.0 mm", row_id="front_pushrod", tab="Chassis", section="Front", range_metric={"min": "-40.0 mm", "max": "40.0 mm"}),
                _row("Brake pressure bias", "53.00%", row_id="brake_bias", tab="Systems", section="Brake Spec"),
                _row("Front RH at speed", "15.0 mm", row_id="front_rh_speed", tab="Tires/Aero", section="Aero Calculator", is_derived=True),
                _row("Rear RH at speed", "40.0 mm", row_id="rear_rh_speed", tab="Tires/Aero", section="Aero Calculator", is_derived=True),
                _row("Traction control gain", "3 (TC2)", row_id="tc_gain", tab="Systems", section="Traction Control"),
                _row("Preload", "25 Nm", row_id="rear_preload", tab="Systems", section="Rear Diff Spec"),
            ],
        }
        self.schema = bootstrap_schema_from_rows(car_name="ferrari", row_payloads=[self.rows_payload])

    def test_bootstrap_schema_seeds_architecture_and_fields(self) -> None:
        self.assertEqual(self.schema.car_name, "ferrari")
        self.assertEqual(self.schema.architecture["diff_layout"], "front_and_rear_diff")
        fields = {field.canonical_key: field for field in self.schema.fields}
        self.assertIn("front_pushrod_offset_mm", fields)
        self.assertIn("brake_bias_pct", fields)
        self.assertTrue(any(field.field_role == "derived_output" for field in self.schema.fields))

    def test_validate_schema_coverage_reports_runtime_fields(self) -> None:
        report = validate_schema_coverage(schema=self.schema, row_payloads=[self.rows_payload])
        self.assertGreater(report["runtime_relevant_fields"], 0)
        self.assertGreaterEqual(report["mapped_runtime_fields"], 0)
        self.assertGreaterEqual(report["coverage_ratio"], 0.0)

    def test_sample_ingest_builds_normalized_samples(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            schema_path = root / "schema.json"
            schema_path.write_text(json.dumps(self.schema.to_dict(), indent=2), encoding="utf-8")

            garage_dir = root / "garage_sample"
            garage_dir.mkdir(parents=True)
            (garage_dir / "manifest.json").write_text(
                json.dumps(
                    RawSampleManifest(
                        sample_id="garage_1",
                        car="ferrari",
                        track="sebring",
                        artifacts={"setup_rows_json": "setup_rows.json"},
                    ).to_dict(),
                    indent=2,
                ),
                encoding="utf-8",
            )
            (garage_dir / "setup_rows.json").write_text(json.dumps(self.rows_payload, indent=2), encoding="utf-8")

            telemetry_dir = root / "telemetry_sample"
            telemetry_dir.mkdir(parents=True)
            (telemetry_dir / "manifest.json").write_text(
                json.dumps(
                    RawSampleManifest(
                        sample_id="telemetry_1",
                        car="ferrari",
                        track="sebring",
                        sample_type="telemetry",
                        artifacts={
                            "setup_rows_json": "setup_rows.json",
                            "measured_json": "measured.json",
                        },
                    ).to_dict(),
                    indent=2,
                ),
                encoding="utf-8",
            )
            (telemetry_dir / "setup_rows.json").write_text(json.dumps(self.rows_payload, indent=2), encoding="utf-8")
            (telemetry_dir / "measured.json").write_text(
                json.dumps(
                    {
                        "lap_number": 12,
                        "lap_time_s": 111.8,
                        "front_heave_travel_used_pct": 82.4,
                        "front_rh_excursion_measured_mm": 12.1,
                        "rear_rh_std_mm": 6.8,
                        "pitch_range_braking_deg": 1.2,
                        "front_braking_lock_ratio_p95": 0.07,
                        "rear_power_slip_ratio_p95": 0.11,
                        "body_slip_p95_deg": 3.5,
                        "understeer_low_speed_deg": 1.4,
                        "understeer_high_speed_deg": 0.7,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            garage_sample = ingest_sample_dir(garage_dir, schema_path)
            telemetry_sample = ingest_sample_dir(telemetry_dir, schema_path)
            self.assertIsInstance(garage_sample, NormalizedGarageSample)
            self.assertIsInstance(telemetry_sample, NormalizedTelemetrySample)

            garages, telemetry = ingest_sample_tree(raw_root=root, schema_path=schema_path)
            self.assertEqual(len(garages), 1)
            self.assertEqual(len(telemetry), 1)

            out_jsonl = write_jsonl(garages, root / "garage_samples.jsonl")
            self.assertTrue(out_jsonl.exists())

    def test_fitters_and_publish_pipeline(self) -> None:
        garage_samples = [
            NormalizedGarageSample(
                sample_id=f"g{i}",
                car="ferrari",
                track="sebring",
                sample_type="garage_static",
                canonical_inputs={
                    "front_pushrod_offset_mm": float(i),
                    "rear_pushrod_offset_mm": float(i) * 2.0,
                    "front_heave_spring_nmm": 100.0 + i * 10.0,
                    "front_heave_perch_mm": -10.0 - i,
                    "rear_third_spring_nmm": 500.0 + i * 20.0,
                    "rear_third_perch_mm": -100.0 - i,
                    "front_torsion_od_mm": 20.0 + i,
                    "rear_spring_rate_nmm": 450.0 + i * 5.0,
                    "front_camber_deg": -2.0 + i * 0.1,
                    "fuel_l": 50.0 + i,
                },
                garage_outputs={
                    "static_front_rh_mm": 30.0 + i * 0.5,
                    "static_rear_rh_mm": 44.0 + i * 0.8,
                    "front_rh_at_speed_mm": 15.0 + i * 0.1,
                    "rear_rh_at_speed_mm": 40.0 + i * 0.1,
                    "torsion_bar_turns": 0.08 + i * 0.005,
                    "heave_spring_defl_static_mm": 10.0 + i * 0.3,
                    "heave_slider_defl_static_mm": 40.0 + i * 0.4,
                    "front_shock_defl_static_mm": 15.0 + i * 0.2,
                    "rear_shock_defl_static_mm": 17.0 + i * 0.2,
                },
            )
            for i in range(1, 7)
        ]
        telemetry_samples = [
            NormalizedTelemetrySample(
                sample_id=f"t{i}",
                car="ferrari",
                track="sebring",
                lap_number=i,
                lap_time_s=111.0 + i * 0.1,
                canonical_inputs={
                    "front_pushrod_offset_mm": float(i),
                    "rear_pushrod_offset_mm": float(i) * 2.0,
                    "front_heave_spring_nmm": 100.0 + i * 10.0,
                    "rear_third_spring_nmm": 500.0 + i * 20.0,
                    "diff_preload_nm": 20.0 + i,
                    "tc_gain": 2 + i,
                    "tc_slip": 3 + i,
                },
                measured={
                    "front_heave_travel_used_pct": 80.0 - i,
                    "front_rh_excursion_measured_mm": 12.0 - i * 0.1,
                    "rear_rh_std_mm": 7.0 - i * 0.1,
                    "pitch_range_braking_deg": 1.4 - i * 0.02,
                    "front_braking_lock_ratio_p95": 0.09 - i * 0.002,
                    "rear_power_slip_ratio_p95": 0.12 - i * 0.003,
                    "body_slip_p95_deg": 3.8 - i * 0.05,
                    "understeer_low_speed_deg": 1.5 - i * 0.04,
                    "understeer_high_speed_deg": 0.9 - i * 0.03,
                },
            )
            for i in range(1, 7)
        ]

        garage_model = fit_garage_model(car="ferrari", track="sebring", samples=garage_samples)
        ride_height_model = fit_ride_height_model(car="ferrari", track="sebring", samples=garage_samples)
        telemetry_model = fit_telemetry_model(car="ferrari", track="sebring", samples=telemetry_samples)
        damper_model = fit_damper_model(car="ferrari", track="sebring", samples=telemetry_samples)
        diff_model = fit_diff_model(car="ferrari", track="sebring", samples=telemetry_samples)

        self.assertEqual(garage_model.model_type, "garage_model")
        self.assertIn("static_front_rh_mm", garage_model.parameters.get("models", {}))
        self.assertEqual(ride_height_model.model_type, "ride_height_model")
        self.assertEqual(telemetry_model.model_type, "telemetry_model")
        self.assertEqual(damper_model.model_type, "damper_model")
        self.assertEqual(diff_model.model_type, "diff_model")

        report = build_validation_report(
            car="ferrari",
            track="sebring",
            garage_model=garage_model,
            ride_height_model=ride_height_model,
            telemetry_model=telemetry_model,
        )
        self.assertIsInstance(report, CalibrationReport)
        self.assertIn(report.support_tier, {"calibrated", "partial", "exploratory", "unsupported"})

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for name, artifact in {
                "garage_model.json": garage_model,
                "ride_height_model.json": ride_height_model,
                "telemetry_model.json": telemetry_model,
                "damper_model.json": damper_model,
                "diff_model.json": diff_model,
            }.items():
                (root / name).write_text(json.dumps(artifact.to_dict(), indent=2), encoding="utf-8")
            (root / "calibration_report.json").write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
            result = publish_models(car="ferrari", track="sebring", model_root=root)
            self.assertEqual(result["car"], "ferrari")
            self.assertIn("support_tier.json", result["published_files"])

    def test_cli_parser_supports_full_workflow(self) -> None:
        parser = build_parser()
        commands = [
            ["bootstrap-schema", "--car", "ferrari", "--input-glob", "foo/*.json", "--output", "schema.json"],
            ["create-sample-pack", "--root-dir", "raw", "--car", "ferrari", "--track", "hockenheim", "--sample-id", "sample_001", "--sample-type", "garage_static"],
            ["validate-schema", "--schema", "schema.json", "--input-glob", "foo/*.json"],
            ["ingest-samples", "--car", "ferrari", "--track", "sebring", "--raw-root", "raw", "--schema", "schema.json", "--out-root", "out"],
            ["fit-garage-model", "--car", "ferrari", "--track", "sebring", "--samples", "garage.jsonl", "--out", "garage_model.json"],
            ["fit-ride-height-model", "--car", "ferrari", "--track", "sebring", "--samples", "garage.jsonl", "--out", "rh_model.json"],
            ["fit-telemetry-model", "--car", "ferrari", "--track", "sebring", "--samples", "telemetry.jsonl", "--out", "telemetry_model.json"],
            ["fit-damper-model", "--car", "ferrari", "--track", "sebring", "--samples", "telemetry.jsonl", "--out", "damper_model.json"],
            ["fit-diff-model", "--car", "ferrari", "--track", "sebring", "--samples", "telemetry.jsonl", "--out", "diff_model.json"],
            ["validate-models", "--car", "ferrari", "--track", "sebring", "--garage-model", "garage_model.json", "--rh-model", "rh_model.json", "--telemetry-model", "telemetry_model.json", "--validation-samples", "validation.jsonl", "--report-dir", "reports"],
            ["publish-models", "--car", "ferrari", "--track", "sebring", "--model-root", "reports"],
        ]
        for command in commands:
            args = parser.parse_args(command)
            self.assertIsNotNone(args.func)


if __name__ == "__main__":
    unittest.main()
