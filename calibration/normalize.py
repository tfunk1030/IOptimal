"""Normalization helpers for calibration artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from calibration.ferrari_aliases import flatten_ferrari_carsetup_payload, resolve_ferrari_canonical_key
from calibration.models import (
    NormalizedGarageSample,
    NormalizedTelemetrySample,
    RawSampleManifest,
    SetupFieldSchema,
    SetupSchemaFile,
)


_METRIC_NUMBER_RE = re.compile(r"[-+]?\d*\.?\d+")


def load_schema(path: str | Path) -> SetupSchemaFile:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return SetupSchemaFile.from_dict(payload)


def schema_field_index(schema: SetupSchemaFile) -> dict[str, SetupFieldSchema]:
    index: dict[str, SetupFieldSchema] = {}
    for field in schema.fields:
        index[field.canonical_key] = field
    return index


def raw_key_lookup(schema: SetupSchemaFile) -> dict[str, SetupFieldSchema]:
    lookup: dict[str, SetupFieldSchema] = {}
    for field in schema.fields:
        if field.ui_label:
            lookup.setdefault(_normalize_key(field.ui_label), field)
        for raw_key in field.raw_keys:
            lookup.setdefault(_normalize_key(raw_key), field)
    return lookup


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def parse_typed_value(field: SetupFieldSchema, raw_value: Any) -> Any:
    if raw_value is None:
        return None
    value_type = field.value_type
    if value_type == "string":
        return str(raw_value).strip()
    if value_type == "bool":
        text = str(raw_value).strip().lower()
        return text in {"1", "true", "yes", "on", "enabled"}
    if value_type in {"float", "int", "indexed_option"}:
        if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            numeric = float(raw_value)
        else:
            text = str(raw_value).strip()
            matches = _METRIC_NUMBER_RE.findall(text)
            if not matches:
                if value_type == "string":
                    return text
                return None
            numeric = float(matches[0])
        if value_type == "int":
            return int(round(numeric))
        if value_type == "indexed_option":
            step = None
            if field.range is not None:
                step = field.range.get("step")
            if step not in (None, 0):
                return int(round(numeric / float(step)) * float(step))
            return int(round(numeric))
        return float(numeric)
    if value_type == "enum":
        return str(raw_value).strip()
    return raw_value


def normalize_rows_to_inputs(
    *,
    rows_payload: dict[str, Any],
    schema: SetupSchemaFile,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    rows = list(rows_payload.get("rows") or [])
    if not rows and schema.car_name == "ferrari":
        rows = flatten_ferrari_carsetup_payload(rows_payload)
    lookup = raw_key_lookup(schema)
    canonical_inputs: dict[str, Any] = {}
    raw_fields: dict[str, Any] = {}
    warnings: list[str] = []
    car_name = str(rows_payload.get("carName") or schema.car_name or "").strip().lower()

    schema_index = schema_field_index(schema)
    for row in rows:
        label = str(row.get("label") or "")
        raw_key = _normalize_key(label)
        field = None
        if car_name == "ferrari":
            ferrari_key = resolve_ferrari_canonical_key(row)
            if ferrari_key is not None:
                field = schema_index.get(ferrari_key)
        if field is None:
            field = lookup.get(raw_key)
        if field is None:
            if label:
                raw_fields[label] = row.get("metric_value")
            continue
        parsed_value = parse_typed_value(field, row.get("metric_value"))
        if field.field_role == "input":
            canonical_inputs[field.canonical_key] = parsed_value
        elif field.field_role in {"internal_raw", "derived_output", "context_only", "ui_alias"}:
            raw_fields[field.canonical_key] = parsed_value
        else:
            warnings.append(f"unknown_field_role:{field.canonical_key}:{field.field_role}")

    return canonical_inputs, raw_fields, warnings


def build_normalized_garage_sample(
    *,
    manifest: RawSampleManifest,
    rows_payload: dict[str, Any],
    schema: SetupSchemaFile,
) -> NormalizedGarageSample:
    canonical_inputs, raw_fields, warnings = normalize_rows_to_inputs(
        rows_payload=rows_payload,
        schema=schema,
    )
    garage_output_keys = {
        "front_rh_at_speed_mm",
        "rear_rh_at_speed_mm",
        "static_front_rh_mm",
        "static_rear_rh_mm",
        "torsion_bar_turns",
        "rear_torsion_bar_turns",
        "torsion_bar_defl_mm",
        "rear_torsion_bar_defl_mm",
        "front_shock_defl_static_mm",
        "front_shock_defl_max_mm",
        "rear_shock_defl_static_mm",
        "rear_shock_defl_max_mm",
        "heave_spring_defl_static_mm",
        "heave_spring_defl_max_mm",
        "heave_slider_defl_static_mm",
        "heave_slider_defl_max_mm",
        "third_spring_defl_static_mm",
        "third_spring_defl_max_mm",
        "third_slider_defl_static_mm",
        "third_slider_defl_max_mm",
    }
    garage_outputs = {
        key: value
        for key, value in raw_fields.items()
        if key in garage_output_keys
        or key.endswith("_rh_at_speed_mm")
        or key.startswith("static_")
        or key.endswith("_defl_static_mm")
        or key.endswith("_defl_max_mm")
        or key.endswith("_turns")
    }
    return NormalizedGarageSample(
        sample_id=manifest.sample_id,
        car=manifest.car,
        track=manifest.track,
        sample_type=manifest.sample_type,
        canonical_inputs=canonical_inputs,
        garage_outputs=garage_outputs,
        raw_fields=raw_fields,
        provenance={
            "source_confidence": manifest.source_confidence,
            "warnings": warnings,
            "setup_context": dict(manifest.setup_context),
            "change_protocol": dict(manifest.change_protocol),
        },
    )


def build_normalized_telemetry_sample(
    *,
    manifest: RawSampleManifest,
    rows_payload: dict[str, Any],
    measured_payload: dict[str, Any],
    schema: SetupSchemaFile,
) -> NormalizedTelemetrySample:
    canonical_inputs, _, warnings = normalize_rows_to_inputs(
        rows_payload=rows_payload,
        schema=schema,
    )
    return NormalizedTelemetrySample(
        sample_id=manifest.sample_id,
        car=manifest.car,
        track=manifest.track,
        lap_number=int(measured_payload.get("lap_number") or 0),
        lap_time_s=(
            None
            if measured_payload.get("lap_time_s") is None
            else float(measured_payload.get("lap_time_s"))
        ),
        canonical_inputs=canonical_inputs,
        measured=dict(measured_payload),
        context={
            "source_confidence": manifest.source_confidence,
            "warnings": warnings,
            "setup_context": dict(manifest.setup_context),
            "change_protocol": dict(manifest.change_protocol),
            "driver_context": dict(manifest.driver_context),
        },
    )
