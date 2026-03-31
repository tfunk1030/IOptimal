"""Fit simple telemetry-response models from normalized telemetry samples."""

from __future__ import annotations

from calibration.feature_builders import feature_matrix_from_samples, fit_linear_model
from calibration.models import FittedModelArtifact, NormalizedTelemetrySample


DEFAULT_TELEMETRY_TARGETS = (
    "front_heave_travel_used_pct",
    "front_rh_excursion_measured_mm",
    "rear_rh_std_mm",
    "pitch_range_braking_deg",
    "front_braking_lock_ratio_p95",
    "rear_power_slip_ratio_p95",
    "body_slip_p95_deg",
    "understeer_low_speed_deg",
    "understeer_high_speed_deg",
)


def fit_telemetry_model(
    *,
    car: str,
    track: str,
    samples: list[NormalizedTelemetrySample],
    targets: tuple[str, ...] = DEFAULT_TELEMETRY_TARGETS,
) -> FittedModelArtifact:
    rows = [
        {
            **sample.canonical_inputs,
            **sample.measured,
        }
        for sample in samples
    ]
    matrix = feature_matrix_from_samples(rows)
    models = {}
    rmses: list[float] = []
    metrics = {"samples": len(samples)}
    for target in targets:
        model = fit_linear_model(matrix=matrix, target=target)
        if model is None:
            continue
        models[target] = model.to_dict()
        if model.rmse is not None:
            rmses.append(float(model.rmse))
    metrics["fitted_targets"] = sorted(models.keys())
    metrics["mean_rmse"] = round(sum(rmses) / len(rmses), 6) if rmses else None
    return FittedModelArtifact(
        car=car,
        track=track,
        model_type="telemetry_model",
        metadata={"targets": list(targets)},
        parameters={"models": models},
        metrics=metrics,
    )
