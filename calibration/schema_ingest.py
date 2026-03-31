"""Bootstrap and validate per-car setup schema files.

This module turns raw setup-row dumps into a stable schema artifact that the
rest of the calibration/runtime stack can consume.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from calibration.models import SetupFieldSchema, SetupSchemaFile
from calibration.normalize import _normalize_key
from car_model.cars import get_car
from car_model.setup_registry import CAR_FIELD_SPECS, FIELD_REGISTRY


def _car_display_name(car_name: str) -> str:
    try:
        return get_car(car_name).name
    except Exception:
        return car_name


def _car_architecture(car_name: str) -> dict[str, Any]:
    defaults: dict[str, dict[str, Any]] = {
        "bmw": {
            "front_suspension": "front_torsion_plus_heave",
            "rear_suspension": "rear_coil_plus_third",
            "damper_layout": "per_corner",
            "diff_layout": "rear_diff",
            "aero_calc_outputs_present": True,
        },
        "cadillac": {
            "front_suspension": "front_torsion_plus_heave",
            "rear_suspension": "rear_coil_plus_third",
            "damper_layout": "per_corner",
            "diff_layout": "rear_diff",
            "aero_calc_outputs_present": True,
        },
        "ferrari": {
            "front_suspension": "front_torsion_plus_heave",
            "rear_suspension": "rear_torsion_plus_heave",
            "damper_layout": "per_corner",
            "diff_layout": "front_and_rear_diff",
            "aero_calc_outputs_present": True,
        },
        "acura": {
            "front_suspension": "front_torsion_plus_heave",
            "rear_suspension": "rear_torsion_plus_heave",
            "damper_layout": "heave_and_roll",
            "diff_layout": "rear_diff",
            "aero_calc_outputs_present": False,
        },
        "porsche": {
            "front_suspension": "front_torsion_plus_heave",
            "rear_suspension": "rear_coil_plus_third",
            "damper_layout": "per_corner",
            "diff_layout": "rear_diff",
            "aero_calc_outputs_present": True,
        },
    }
    return defaults.get(car_name, {})


def _value_type_from_registry(field_name: str) -> str:
    field_def = FIELD_REGISTRY.get(field_name)
    if field_def is None:
        return "string"
    if field_def.value_type == "string":
        return "string"
    if field_def.value_type == "indexed":
        return "indexed_option"
    if field_def.value_type == "discrete":
        return "int"
    return "float"


def _field_role_from_registry(field_name: str) -> str:
    field_def = FIELD_REGISTRY.get(field_name)
    if field_def is None:
        return "context_only"
    if field_def.kind == "settable":
        return "input"
    if field_def.kind == "computed":
        return "derived_output"
    if field_def.kind == "context":
        return "context_only"
    return "context_only"


def _range_from_spec(spec: Any) -> dict[str, Any] | None:
    if spec is None:
        return None
    if spec.range_min is None and spec.range_max is None:
        return None
    payload: dict[str, Any] = {}
    if spec.range_min is not None:
        payload["min"] = spec.range_min
    if spec.range_max is not None:
        payload["max"] = spec.range_max
    if spec.resolution is not None:
        payload["step"] = spec.resolution
    return payload


def bootstrap_schema_from_rows(
    *,
    car_name: str,
    row_payloads: list[dict[str, Any]],
) -> SetupSchemaFile:
    display_name = _car_display_name(car_name)
    observed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for payload in row_payloads:
        for row in list(payload.get("rows") or []):
            label = str(row.get("label") or "").strip()
            if not label:
                continue
            observed[label].append(row)

    car_specs = CAR_FIELD_SPECS.get(car_name, {})
    field_schemas: list[SetupFieldSchema] = []
    by_key: dict[str, SetupFieldSchema] = {}

    # Seed from the registry so every known runtime field has a schema row.
    for canonical_key, spec in sorted(car_specs.items()):
        field_def = FIELD_REGISTRY.get(canonical_key)
        if field_def is None:
            continue
        field = SetupFieldSchema(
            canonical_key=canonical_key,
            ui_label=canonical_key,
            raw_keys=[canonical_key, spec.yaml_path, spec.sto_param_id],
            field_role=_field_role_from_registry(canonical_key),
            value_type=_value_type_from_registry(canonical_key),
            public_unit=field_def.unit or "",
            internal_unit=field_def.unit or "",
            range=_range_from_spec(spec),
            snap_rule="nearest_step" if spec.resolution not in (None, 0) else ("enum" if spec.options else "none"),
            is_solver_relevant=field_def.kind == "settable",
            is_runtime_context_only=field_def.kind == "context",
            notes=[field_def.formula_note] if field_def.formula_note else [],
        )
        by_key[canonical_key] = field
        field_schemas.append(field)

    # Augment with observed labels and values from row dumps.
    for label, rows in sorted(observed.items()):
        key = _normalize_key(label)
        target = by_key.get(key)
        if target is None:
            target = SetupFieldSchema(
                canonical_key=key,
                ui_label=label,
                raw_keys=[label],
                field_role="context_only",
                value_type="string",
                is_solver_relevant=False,
                is_runtime_context_only=True,
            )
            by_key[key] = target
            field_schemas.append(target)
        elif label not in target.raw_keys:
            target.raw_keys.append(label)
        if not target.ui_label or target.ui_label == target.canonical_key:
            target.ui_label = label

        exemplar = rows[0]
        target.tab = exemplar.get("tab")
        target.section = exemplar.get("section")
        target.location = exemplar.get("section") or exemplar.get("tab")
        target.observed_aliases.extend(
            {
                "label": row.get("label"),
                "metric_value": row.get("metric_value"),
                "imperial_value": row.get("imperial_value"),
                "row_id": row.get("row_id"),
            }
            for row in rows[:5]
        )
        if exemplar.get("is_derived"):
            target.field_role = "derived_output"
            target.is_solver_relevant = False
        elif exemplar.get("is_mapped") and target.field_role == "context_only":
            target.field_role = "input"
        if exemplar.get("range_metric") and target.range is None:
            range_metric = exemplar.get("range_metric") or {}
            target.range = {
                "min": range_metric.get("min"),
                "max": range_metric.get("max"),
            }

    return SetupSchemaFile(
        car_name=car_name,
        display_name=display_name,
        version=1,
        architecture=_car_architecture(car_name),
        fields=sorted(field_schemas, key=lambda field: field.canonical_key),
    )


def validate_schema_coverage(
    *,
    schema: SetupSchemaFile,
    row_payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    observed_labels: set[str] = set()
    for payload in row_payloads:
        for row in list(payload.get("rows") or []):
            label = str(row.get("label") or "").strip()
            if label:
                observed_labels.add(_normalize_key(label))
    mapped = 0
    runtime_relevant = 0
    unresolved: list[str] = []
    for field in schema.fields:
        if not field.is_solver_relevant:
            continue
        runtime_relevant += 1
        candidates = {field.canonical_key}
        candidates.update(_normalize_key(raw_key) for raw_key in field.raw_keys)
        if candidates & observed_labels:
            mapped += 1
        else:
            unresolved.append(field.canonical_key)
    coverage = 1.0 if runtime_relevant == 0 else mapped / runtime_relevant
    return {
        "runtime_relevant_fields": runtime_relevant,
        "mapped_runtime_fields": mapped,
        "coverage_ratio": round(coverage, 4),
        "unresolved_runtime_fields": unresolved,
    }


def save_schema(schema: SetupSchemaFile, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(schema.to_dict(), indent=2), encoding="utf-8")
    return target


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def bootstrap_schema(*, car: str, input_glob: str) -> SetupSchemaFile:
    payloads = [_load_json(path) for path in sorted(Path().glob(input_glob))]
    return bootstrap_schema_from_rows(car_name=car, row_payloads=payloads)


def validate_schema_coverage_from_paths(*, schema_path: str | Path, input_glob: str) -> dict[str, Any]:
    schema_payload = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    schema = SetupSchemaFile.from_dict(schema_payload)
    payloads = [_load_json(path) for path in sorted(Path().glob(input_glob))]
    return validate_schema_coverage(schema=schema, row_payloads=payloads)
