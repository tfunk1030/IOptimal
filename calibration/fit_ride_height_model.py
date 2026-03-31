"""Fit simplified ride-height models from normalized garage samples."""

from __future__ import annotations

from calibration.fit_garage_model import fit_garage_model
from calibration.models import FittedModelArtifact, NormalizedGarageSample


def fit_ride_height_model(
    *,
    car: str,
    track: str,
    samples: list[NormalizedGarageSample],
) -> FittedModelArtifact:
    garage_artifact = fit_garage_model(car=car, track=track, samples=samples)
    front_model = dict(garage_artifact.parameters.get("models", {}).get("static_front_rh_mm", {}))
    rear_model = dict(garage_artifact.parameters.get("models", {}).get("static_rear_rh_mm", {}))
    front_coeffs = {
        key: value
        for key, value in dict(front_model.get("coefficients") or {}).items()
        if key in {"front_heave_setting_index", "front_heave_nmm", "front_camber_deg", "front_heave_spring_nmm"}
    }
    rear_coeffs = dict(rear_model.get("coefficients") or {})
    metrics = {
        "samples": len(samples),
        "front_rmse": front_model.get("rmse"),
        "rear_rmse": rear_model.get("rmse"),
        "mean_rmse": _mean_defined([front_model.get("rmse"), rear_model.get("rmse")]),
    }
    return FittedModelArtifact(
        car=car,
        track=track,
        model_type="ride_height_model",
        parameters={
            "front": {
                "intercept": front_model.get("intercept", 0.0),
                "coefficients": front_coeffs,
            },
            "rear": {
                "intercept": rear_model.get("intercept", 0.0),
                "coefficients": rear_coeffs,
            },
        },
        metrics=metrics,
    )


def _mean_defined(values: list[object]) -> float | None:
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return None
    return float(sum(numeric) / len(numeric))
