"""User-facing scaffolding helpers for calibration collection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from calibration.models import RawSampleManifest, SetupSchemaFile
from calibration.schema_ingest import _car_architecture, _car_display_name
from car_model.setup_registry import CAR_FIELD_SPECS, FIELD_REGISTRY


def generate_registry_seed_schema(car: str) -> SetupSchemaFile:
    """Generate a baseline schema seed from the runtime registry only."""
    specs = CAR_FIELD_SPECS.get(car, {})
    fields = []
    for canonical_key, spec in sorted(specs.items()):
        field_def = FIELD_REGISTRY.get(canonical_key)
        if field_def is None:
            continue
        field_role = (
            "input" if field_def.kind == "settable"
            else "derived_output" if field_def.kind == "computed"
            else "context_only"
        )
        if canonical_key in {"hybrid_rear_drive_enabled"}:
            value_type = "bool"
        else:
            value_type = (
                "string" if field_def.value_type == "string"
                else "indexed_option" if field_def.value_type == "indexed"
                else "int" if field_def.value_type == "discrete"
                else "float"
            )
        range_payload = None
        if spec.range_min is not None or spec.range_max is not None:
            range_payload = {}
            if spec.range_min is not None:
                range_payload["min"] = spec.range_min
            if spec.range_max is not None:
                range_payload["max"] = spec.range_max
            if spec.resolution is not None:
                range_payload["step"] = spec.resolution
        fields.append(
            {
                "canonical_key": canonical_key,
                "ui_label": canonical_key,
                "raw_keys": [canonical_key, spec.yaml_path, spec.sto_param_id],
                "tab": None,
                "section": None,
                "location": None,
                "field_role": field_role,
                "value_type": value_type,
                "public_unit": field_def.unit or "",
                "internal_unit": field_def.unit or "",
                "range": range_payload,
                "snap_rule": (
                    "nearest_step" if spec.resolution not in (None, 0)
                    else "enum" if spec.options else "none"
                ),
                "link_group": None,
                "link_rule": None,
                "is_solver_relevant": field_def.kind == "settable",
                "is_runtime_context_only": field_def.kind == "context",
                "observed_aliases": [],
                "notes": [field_def.formula_note] if field_def.formula_note else [],
            }
        )
    return SetupSchemaFile(
        car_name=car,
        display_name=_car_display_name(car),
        version=1,
        architecture=_car_architecture(car),
        fields=[
            __import__("calibration.models", fromlist=["SetupFieldSchema"]).SetupFieldSchema(**field)
            for field in fields
        ],
        warnings=["seeded_from_setup_registry_only"],
    )


def write_schema_seed_files(output_root: str | Path, *, cars: list[str] | None = None) -> list[Path]:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    car_list = cars or sorted(CAR_FIELD_SPECS.keys())
    for car in car_list:
        schema = generate_registry_seed_schema(car)
        path = root / f"{car}.json"
        path.write_text(json.dumps(schema.to_dict(), indent=2), encoding="utf-8")
        written.append(path)
    return written


def build_sample_pack(
    *,
    car: str,
    track: str,
    track_config: str,
    sample_id: str,
    sample_type: str,
    output_root: str | Path,
) -> dict[str, Any]:
    """Create a user-friendly raw sample directory scaffold."""
    root = Path(output_root) / car / track / sample_id
    screenshots = root / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)

    artifacts = {
        "setup_rows_json": "setup_rows.json",
        "ibt": "session.ibt",
        "sto": "setup.sto",
        "screenshots": [
            "screenshots/tires_aero.png",
            "screenshots/chassis_front.png",
            "screenshots/chassis_rear.png",
            "screenshots/dampers.png",
            "screenshots/systems.png",
        ],
    }
    if sample_type != "garage_static":
        artifacts["measured_json"] = "measured.json"

    manifest = RawSampleManifest(
        sample_id=sample_id,
        car=car,
        track=track,
        track_config=track_config,
        sample_type=sample_type,
        artifacts=artifacts,
        setup_context={
            "fuel_l": None,
            "wing_deg": None,
            "tire_type": None,
        },
        change_protocol={
            "baseline_sample_id": "",
            "intended_changes": [],
            "notes": "",
        },
        driver_context={
            "driver_name": "",
            "session_type": "test",
            "weather_locked": True,
        },
    )
    (root / "manifest.json").write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    (root / "setup_rows.json").write_text(json.dumps({"carName": car, "rows": []}, indent=2), encoding="utf-8")
    # Create zero-byte placeholders for optional binary artifacts so raw dataset
    # validation passes immediately after scaffolding and the user can replace
    # them in-place later.
    (root / "session.ibt").write_bytes(b"")
    (root / "setup.sto").write_bytes(b"")
    for screenshot_name in (
        "tires_aero.png",
        "chassis_front.png",
        "chassis_rear.png",
        "dampers.png",
        "systems.png",
    ):
        (screenshots / screenshot_name).write_bytes(b"")
    if sample_type != "garage_static":
        (root / "measured.json").write_text(
            json.dumps(
                {
                    "lap_number": 0,
                    "lap_time_s": None,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return {
        "sample_dir": str(root),
        "manifest": str(root / "manifest.json"),
        "setup_rows_json": str(root / "setup_rows.json"),
        "measured_json": str(root / "measured.json") if sample_type != "garage_static" else None,
        "screenshots_dir": str(screenshots),
    }

