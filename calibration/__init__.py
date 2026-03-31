"""Calibration toolkit for setup schema and model fitting.

This package provides:
  - setup-schema bootstrap and normalization
  - raw calibration sample ingestion
  - garage / RH / telemetry / damper / diff fitters
  - validation and runtime publication helpers
"""

from calibration.models import (
    CalibrationReport,
    FittedModelArtifact,
    LinearMetricModel,
    NormalizedGarageSample,
    NormalizedTelemetrySample,
    RawSampleManifest,
    SetupFieldSchema,
    SetupSchemaFile,
)

__all__ = [
    "CalibrationReport",
    "FittedModelArtifact",
    "LinearMetricModel",
    "NormalizedGarageSample",
    "NormalizedTelemetrySample",
    "RawSampleManifest",
    "SetupFieldSchema",
    "SetupSchemaFile",
]
