"""Validation helpers for fitted calibration artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from calibration.models import CalibrationReport, FittedModelArtifact


def _safe_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _tier_from_metrics(*, garage_rmse: float | None, ride_rmse: float | None, telemetry_rmse: float | None) -> str:
    if garage_rmse is not None and ride_rmse is not None and telemetry_rmse is not None:
        if garage_rmse <= 1.0 and ride_rmse <= 1.0 and telemetry_rmse <= 1.0:
            return "calibrated"
        if garage_rmse <= 2.0 and ride_rmse <= 2.0 and telemetry_rmse <= 2.0:
            return "partial"
        return "exploratory"
    if garage_rmse is not None and ride_rmse is not None:
        return "partial" if garage_rmse <= 2.0 and ride_rmse <= 2.0 else "exploratory"
    return "unsupported"


def build_validation_report(
    *,
    car: str,
    track: str,
    garage_model: FittedModelArtifact | None = None,
    ride_height_model: FittedModelArtifact | None = None,
    telemetry_model: FittedModelArtifact | None = None,
) -> CalibrationReport:
    garage_rmse = _safe_float((garage_model.metrics if garage_model else {}).get("mean_rmse"))
    ride_rmse = _safe_float((ride_height_model.metrics if ride_height_model else {}).get("mean_rmse"))
    telemetry_rmse = _safe_float((telemetry_model.metrics if telemetry_model else {}).get("mean_rmse"))
    support_tier = _tier_from_metrics(
        garage_rmse=garage_rmse,
        ride_rmse=ride_rmse,
        telemetry_rmse=telemetry_rmse,
    )
    warnings: list[str] = []
    if garage_model is None:
        warnings.append("garage_model_missing")
    if ride_height_model is None:
        warnings.append("ride_height_model_missing")
    if telemetry_model is None:
        warnings.append("telemetry_model_missing")
    return CalibrationReport(
        car=car,
        track=track,
        support_tier=support_tier,
        summary={
            "car": car,
            "track": track,
            "support_tier": support_tier,
        },
        model_metrics={
            "garage_model_rmse": garage_rmse,
            "ride_height_model_rmse": ride_rmse,
            "telemetry_model_rmse": telemetry_rmse,
        },
        warnings=warnings,
    )


def validate_models(
    *,
    car: str,
    track: str,
    garage_model_path: str | Path,
    rh_model_path: str | Path,
    telemetry_model_path: str | Path,
    validation_samples_path: str | Path | None = None,
) -> CalibrationReport:
    """Load fitted artifacts and produce a support-tier report.

    The first implementation is intentionally metrics-driven and does not try
    to replay the full runtime pipeline over holdout sessions yet.  It allows
    the calibration package and runtime publication path to be exercised
    end-to-end while keeping the fitting artifacts explicit.
    """
    garage_model = FittedModelArtifact.from_dict(
        json.loads(Path(garage_model_path).read_text(encoding="utf-8"))
    ) if Path(garage_model_path).exists() else None
    ride_height_model = FittedModelArtifact.from_dict(
        json.loads(Path(rh_model_path).read_text(encoding="utf-8"))
    ) if Path(rh_model_path).exists() else None
    telemetry_model = FittedModelArtifact.from_dict(
        json.loads(Path(telemetry_model_path).read_text(encoding="utf-8"))
    ) if Path(telemetry_model_path).exists() else None

    report = build_validation_report(
        car=car,
        track=track,
        garage_model=garage_model,
        ride_height_model=ride_height_model,
        telemetry_model=telemetry_model,
    )
    if validation_samples_path is not None and Path(validation_samples_path).exists():
        report.summary["validation_samples_path"] = str(validation_samples_path)
    return report
