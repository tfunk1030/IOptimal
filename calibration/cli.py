"""CLI for calibration schema, fitting, validation, and publication."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from calibration.fit_damper_model import fit_damper_model
from calibration.fit_diff_model import fit_diff_model
from calibration.fit_garage_model import fit_garage_model
from calibration.fit_ride_height_model import fit_ride_height_model
from calibration.fit_telemetry_model import fit_telemetry_model
from calibration.models import FittedModelArtifact
from calibration.publish_models import publish_models
from calibration.dataset_validation import validate_raw_dataset
from calibration.scaffold import (
    build_sample_pack,
    write_schema_seed_files,
)
from calibration.sample_ingest import ingest_sample_tree, load_jsonl, write_jsonl
from calibration.schema_ingest import (
    bootstrap_schema_from_rows,
    save_schema,
    validate_schema_coverage,
)
from calibration.validate_models import build_validation_report


def _write_json(path: str | Path, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def cmd_bootstrap_schema(args: argparse.Namespace) -> int:
    input_paths = sorted(Path().glob(args.input_glob))
    row_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in input_paths]
    schema = bootstrap_schema_from_rows(car_name=args.car, row_payloads=row_payloads)
    save_schema(schema, args.output)
    print(f"Wrote schema -> {args.output}")
    return 0


def cmd_seed_schema_files(args: argparse.Namespace) -> int:
    written = write_schema_seed_files(output_root=args.output_dir)
    print(json.dumps({"written_files": [str(path) for path in written]}, indent=2, sort_keys=True))
    return 0


def cmd_create_sample_pack(args: argparse.Namespace) -> int:
    target = build_sample_pack(
        output_root=args.root_dir,
        car=args.car,
        track=args.track,
        track_config=args.track_config,
        sample_id=args.sample_id,
        sample_type=args.sample_type,
    )
    print(json.dumps(target, indent=2, sort_keys=True))
    return 0


def cmd_validate_schema(args: argparse.Namespace) -> int:
    schema_payload = json.loads(Path(args.schema).read_text(encoding="utf-8"))
    row_payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(Path().glob(args.input_glob))
    ]
    report = validate_schema_coverage(
        schema=bootstrap_schema_from_rows(
            car_name=str(schema_payload.get("car_name") or ""),
            row_payloads=row_payloads,
        )
        if not schema_payload.get("fields")
        else __import__("calibration.models", fromlist=["SetupSchemaFile"]).SetupSchemaFile.from_dict(schema_payload),
        row_payloads=row_payloads,
    )
    if args.output:
        _write_json(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def cmd_ingest_samples(args: argparse.Namespace) -> int:
    garages, telemetry = ingest_sample_tree(
        raw_root=args.raw_root,
        schema_path=args.schema,
    )
    out_root = Path(args.out_root)
    garage_path = write_jsonl(garages, out_root / "garage_samples.jsonl")
    telemetry_path = write_jsonl(telemetry, out_root / "telemetry_samples.jsonl")
    report = {
        "car": args.car,
        "track": args.track,
        "garage_samples": len(garages),
        "telemetry_samples": len(telemetry),
        "garage_path": str(garage_path),
        "telemetry_path": str(telemetry_path),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def cmd_validate_raw_dataset(args: argparse.Namespace) -> int:
    report = validate_raw_dataset(args.raw_root)
    if args.output:
        _write_json(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _write_artifact(path: str | Path, artifact: FittedModelArtifact) -> None:
    _write_json(path, artifact.to_dict())


def cmd_fit_garage_model(args: argparse.Namespace) -> int:
    samples = load_jsonl(args.samples)
    artifact = fit_garage_model(
        car=args.car,
        track=args.track,
        samples=samples,
    )
    _write_artifact(args.out, artifact)
    print(f"Wrote garage model -> {args.out}")
    return 0


def cmd_fit_ride_height_model(args: argparse.Namespace) -> int:
    samples = load_jsonl(args.samples)
    artifact = fit_ride_height_model(
        car=args.car,
        track=args.track,
        samples=samples,
    )
    _write_artifact(args.out, artifact)
    print(f"Wrote RH model -> {args.out}")
    return 0


def cmd_fit_telemetry_model(args: argparse.Namespace) -> int:
    samples = load_jsonl(args.samples)
    artifact = fit_telemetry_model(
        car=args.car,
        track=args.track,
        samples=samples,
    )
    _write_artifact(args.out, artifact)
    print(f"Wrote telemetry model -> {args.out}")
    return 0


def cmd_fit_damper_model(args: argparse.Namespace) -> int:
    samples = load_jsonl(args.samples)
    artifact = fit_damper_model(
        car=args.car,
        track=args.track,
        samples=samples,
    )
    _write_artifact(args.out, artifact)
    print(f"Wrote damper model -> {args.out}")
    return 0


def cmd_fit_diff_model(args: argparse.Namespace) -> int:
    samples = load_jsonl(args.samples)
    artifact = fit_diff_model(
        car=args.car,
        track=args.track,
        samples=samples,
    )
    _write_artifact(args.out, artifact)
    print(f"Wrote diff model -> {args.out}")
    return 0


def cmd_validate_models(args: argparse.Namespace) -> int:
    garage_model = FittedModelArtifact.from_dict(json.loads(Path(args.garage_model).read_text(encoding="utf-8")))
    rh_model = FittedModelArtifact.from_dict(json.loads(Path(args.rh_model).read_text(encoding="utf-8")))
    telemetry_model = FittedModelArtifact.from_dict(json.loads(Path(args.telemetry_model).read_text(encoding="utf-8")))
    report = build_validation_report(
        car=args.car,
        track=args.track,
        garage_model=garage_model,
        ride_height_model=rh_model,
        telemetry_model=telemetry_model,
    )
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    _write_json(report_dir / "calibration_report.json", report.to_dict())
    md_lines = [
        f"# Calibration Report — {report.car}/{report.track}",
        "",
        f"- Support tier: `{report.support_tier}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in sorted(report.summary.items()):
        md_lines.append(f"- {key}: `{value}`")
    md_lines.append("")
    md_lines.append("## Model Metrics")
    md_lines.append("")
    for key, value in sorted(report.model_metrics.items()):
        md_lines.append(f"- {key}: `{value}`")
    if report.warnings:
        md_lines.append("")
        md_lines.append("## Warnings")
        md_lines.append("")
        for warning in report.warnings:
            md_lines.append(f"- {warning}")
    (report_dir / "calibration_report.md").write_text("\n".join(md_lines), encoding="utf-8")
    print(f"Wrote reports -> {report_dir}")
    return 0


def cmd_publish_models(args: argparse.Namespace) -> int:
    result = publish_models(
        car=args.car,
        track=args.track,
        model_root=args.model_root,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calibration workflow CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("bootstrap-schema")
    p.add_argument("--car", required=True)
    p.add_argument("--input-glob", required=True)
    p.add_argument("--output", required=True)
    p.set_defaults(func=cmd_bootstrap_schema)

    p = sub.add_parser("seed-schema-files")
    p.add_argument("--output-dir", required=True)
    p.set_defaults(func=cmd_seed_schema_files)

    p = sub.add_parser("create-sample-pack")
    p.add_argument("--root-dir", required=True)
    p.add_argument("--car", required=True)
    p.add_argument("--track", required=True)
    p.add_argument("--sample-id", required=True)
    p.add_argument("--sample-type", default="garage_static", choices=["garage_static", "telemetry", "validation"])
    p.set_defaults(func=cmd_create_sample_pack)

    p = sub.add_parser("validate-schema")
    p.add_argument("--schema", required=True)
    p.add_argument("--input-glob", required=True)
    p.add_argument("--output")
    p.set_defaults(func=cmd_validate_schema)

    p = sub.add_parser("ingest-samples")
    p.add_argument("--car", required=True)
    p.add_argument("--track", required=True)
    p.add_argument("--raw-root", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--out-root", required=True)
    p.set_defaults(func=cmd_ingest_samples)

    p = sub.add_parser("validate-raw-dataset")
    p.add_argument("--raw-root", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--output")
    p.set_defaults(func=cmd_validate_raw_dataset)

    p = sub.add_parser("fit-garage-model")
    p.add_argument("--car", required=True)
    p.add_argument("--track", required=True)
    p.add_argument("--samples", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_fit_garage_model)

    p = sub.add_parser("fit-ride-height-model")
    p.add_argument("--car", required=True)
    p.add_argument("--track", required=True)
    p.add_argument("--samples", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_fit_ride_height_model)

    p = sub.add_parser("fit-telemetry-model")
    p.add_argument("--car", required=True)
    p.add_argument("--track", required=True)
    p.add_argument("--samples", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_fit_telemetry_model)

    p = sub.add_parser("fit-damper-model")
    p.add_argument("--car", required=True)
    p.add_argument("--track", required=True)
    p.add_argument("--samples", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_fit_damper_model)

    p = sub.add_parser("fit-diff-model")
    p.add_argument("--car", required=True)
    p.add_argument("--track", required=True)
    p.add_argument("--samples", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_fit_diff_model)

    p = sub.add_parser("validate-models")
    p.add_argument("--car", required=True)
    p.add_argument("--track", required=True)
    p.add_argument("--garage-model", required=True)
    p.add_argument("--rh-model", required=True)
    p.add_argument("--telemetry-model", required=True)
    p.add_argument("--validation-samples", required=True)
    p.add_argument("--report-dir", required=True)
    p.set_defaults(func=cmd_validate_models)

    p = sub.add_parser("publish-models")
    p.add_argument("--car", required=True)
    p.add_argument("--track", required=True)
    p.add_argument("--model-root", required=True)
    p.set_defaults(func=cmd_publish_models)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
