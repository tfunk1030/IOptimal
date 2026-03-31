"""Canonical data contracts for calibration artifacts.

These models intentionally keep the calibration system file-oriented and
explicit.  They are used by:
  - schema bootstrap / ingestion
  - raw sample normalization
  - model fitting and validation
  - runtime publication of fitted artifacts
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SetupFieldSchema:
    canonical_key: str
    ui_label: str = ""
    raw_keys: list[str] = field(default_factory=list)
    tab: str | None = None
    section: str | None = None
    location: str | None = None
    field_role: str = "input"  # input | derived_output | internal_raw | ui_alias | context_only
    value_type: str = "float"  # float | int | bool | enum | indexed_option | string
    public_unit: str = ""
    internal_unit: str = ""
    range: dict[str, Any] | None = None
    snap_rule: str = "none"
    link_group: str | None = None
    link_rule: str | None = None
    is_solver_relevant: bool = False
    is_runtime_context_only: bool = False
    observed_aliases: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SetupSchemaFile:
    car_name: str
    display_name: str
    version: int = 1
    architecture: dict[str, Any] = field(default_factory=dict)
    fields: list[SetupFieldSchema] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "car_name": self.car_name,
            "display_name": self.display_name,
            "version": self.version,
            "architecture": dict(self.architecture),
            "fields": [field.to_dict() for field in self.fields],
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SetupSchemaFile":
        return cls(
            car_name=str(payload.get("car_name") or ""),
            display_name=str(payload.get("display_name") or payload.get("car_name") or ""),
            version=int(payload.get("version") or 1),
            architecture=dict(payload.get("architecture") or {}),
            fields=[
                SetupFieldSchema(**field_payload)
                for field_payload in list(payload.get("fields") or [])
            ],
            warnings=[str(item) for item in list(payload.get("warnings") or [])],
        )


@dataclass
class RawSampleManifest:
    sample_id: str
    car: str
    track: str
    track_config: str = ""
    sample_type: str = "garage_static"  # garage_static | telemetry | validation
    source_confidence: float = 1.0
    artifacts: dict[str, Any] = field(default_factory=dict)
    setup_context: dict[str, Any] = field(default_factory=dict)
    change_protocol: dict[str, Any] = field(default_factory=dict)
    driver_context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RawSampleManifest":
        return cls(
            sample_id=str(payload.get("sample_id") or ""),
            car=str(payload.get("car") or ""),
            track=str(payload.get("track") or ""),
            track_config=str(payload.get("track_config") or ""),
            sample_type=str(payload.get("sample_type") or "garage_static"),
            source_confidence=float(payload.get("source_confidence") or 1.0),
            artifacts=dict(payload.get("artifacts") or {}),
            setup_context=dict(payload.get("setup_context") or {}),
            change_protocol=dict(payload.get("change_protocol") or {}),
            driver_context=dict(payload.get("driver_context") or {}),
        )


@dataclass
class NormalizedGarageSample:
    sample_id: str
    car: str
    track: str
    sample_type: str
    canonical_inputs: dict[str, Any] = field(default_factory=dict)
    garage_outputs: dict[str, Any] = field(default_factory=dict)
    raw_fields: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "NormalizedGarageSample":
        return cls(
            sample_id=str(payload.get("sample_id") or ""),
            car=str(payload.get("car") or ""),
            track=str(payload.get("track") or ""),
            sample_type=str(payload.get("sample_type") or "garage_static"),
            canonical_inputs=dict(payload.get("canonical_inputs") or {}),
            garage_outputs=dict(payload.get("garage_outputs") or {}),
            raw_fields=dict(payload.get("raw_fields") or {}),
            provenance=dict(payload.get("provenance") or {}),
        )


@dataclass
class NormalizedTelemetrySample:
    sample_id: str
    car: str
    track: str
    lap_number: int = 0
    lap_time_s: float | None = None
    canonical_inputs: dict[str, Any] = field(default_factory=dict)
    measured: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "NormalizedTelemetrySample":
        lap_time = payload.get("lap_time_s")
        return cls(
            sample_id=str(payload.get("sample_id") or ""),
            car=str(payload.get("car") or ""),
            track=str(payload.get("track") or ""),
            lap_number=int(payload.get("lap_number") or 0),
            lap_time_s=None if lap_time is None else float(lap_time),
            canonical_inputs=dict(payload.get("canonical_inputs") or {}),
            measured=dict(payload.get("measured") or {}),
            context=dict(payload.get("context") or {}),
        )


@dataclass
class LinearMetricModel:
    target: str
    intercept: float = 0.0
    coefficients: dict[str, float] = field(default_factory=dict)
    r_squared: float | None = None
    rmse: float | None = None
    samples: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LinearMetricModel":
        return cls(
            target=str(payload.get("target") or ""),
            intercept=float(payload.get("intercept") or 0.0),
            coefficients={
                str(key): float(value)
                for key, value in dict(payload.get("coefficients") or {}).items()
            },
            r_squared=(
                None if payload.get("r_squared") is None else float(payload.get("r_squared"))
            ),
            rmse=None if payload.get("rmse") is None else float(payload.get("rmse")),
            samples=int(payload.get("samples") or 0),
        )


@dataclass
class FittedModelArtifact:
    car: str
    track: str
    model_type: str
    version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "car": self.car,
            "track": self.track,
            "model_type": self.model_type,
            "version": self.version,
            "metadata": dict(self.metadata),
            "parameters": dict(self.parameters),
            "metrics": dict(self.metrics),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FittedModelArtifact":
        return cls(
            car=str(payload.get("car") or ""),
            track=str(payload.get("track") or ""),
            model_type=str(payload.get("model_type") or ""),
            version=int(payload.get("version") or 1),
            metadata=dict(payload.get("metadata") or {}),
            parameters=dict(payload.get("parameters") or {}),
            metrics=dict(payload.get("metrics") or {}),
        )


@dataclass
class CalibrationReport:
    car: str
    track: str
    support_tier: str
    summary: dict[str, Any] = field(default_factory=dict)
    model_metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ensure_parent(path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target
