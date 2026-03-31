"""Raw calibration sample ingestion and normalization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from calibration.models import (
    NormalizedGarageSample,
    NormalizedTelemetrySample,
    RawSampleManifest,
)
from calibration.normalize import (
    build_normalized_garage_sample,
    build_normalized_telemetry_sample,
    load_schema,
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def ingest_sample_dir(sample_dir: str | Path, schema_path: str | Path) -> NormalizedGarageSample | NormalizedTelemetrySample:
    sample_path = Path(sample_dir)
    manifest = RawSampleManifest.from_dict(_load_json(sample_path / "manifest.json"))
    schema = load_schema(schema_path)

    rows_rel = manifest.artifacts.get("setup_rows_json")
    if not rows_rel:
        raise FileNotFoundError(f"{sample_path}: manifest missing artifacts.setup_rows_json")
    rows_payload = _load_json(sample_path / rows_rel)

    if manifest.sample_type == "garage_static":
        return build_normalized_garage_sample(
            manifest=manifest,
            rows_payload=rows_payload,
            schema=schema,
        )

    measured_rel = manifest.artifacts.get("measured_json")
    if not measured_rel:
        raise FileNotFoundError(f"{sample_path}: telemetry/validation sample missing artifacts.measured_json")
    measured_payload = _load_json(sample_path / measured_rel)
    return build_normalized_telemetry_sample(
        manifest=manifest,
        rows_payload=rows_payload,
        measured_payload=measured_payload,
        schema=schema,
    )


def ingest_sample_tree(
    *,
    raw_root: str | Path,
    schema_path: str | Path,
) -> tuple[list[NormalizedGarageSample], list[NormalizedTelemetrySample]]:
    raw_root_path = Path(raw_root)
    garage_samples: list[NormalizedGarageSample] = []
    telemetry_samples: list[NormalizedTelemetrySample] = []
    for manifest_path in sorted(raw_root_path.glob("**/manifest.json")):
        sample = ingest_sample_dir(manifest_path.parent, schema_path)
        if isinstance(sample, NormalizedGarageSample):
            garage_samples.append(sample)
        else:
            telemetry_samples.append(sample)
    return garage_samples, telemetry_samples


def write_jsonl(samples: list[Any], path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(sample.to_dict(), sort_keys=True))
            handle.write("\n")
    return output


def load_jsonl(path: str | Path, *, sample_type: str) -> list[Any]:
    source = Path(path)
    if not source.exists():
        return []
    rows = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if sample_type == "garage":
                rows.append(NormalizedGarageSample.from_dict(payload))
            else:
                rows.append(NormalizedTelemetrySample.from_dict(payload))
    return rows


def ingest_samples(
    *,
    car: str,
    track: str,
    raw_root: str | Path,
    schema_path: str | Path,
    out_root: str | Path,
) -> dict[str, Any]:
    """Ingest a raw calibration tree and write normalized JSONL outputs."""
    garage_samples, telemetry_samples = ingest_sample_tree(
        raw_root=raw_root,
        schema_path=schema_path,
    )
    out_root_path = Path(out_root)
    written: dict[str, str] = {}
    if garage_samples:
        written["garage_samples"] = str(
            write_jsonl(garage_samples, out_root_path / "garage_samples.jsonl")
        )
    if telemetry_samples:
        written["telemetry_samples"] = str(
            write_jsonl(telemetry_samples, out_root_path / "telemetry_samples.jsonl")
        )
    return {
        "car": car,
        "track": track,
        "garage_samples": len(garage_samples),
        "telemetry_samples": len(telemetry_samples),
        "written_files": written,
    }
