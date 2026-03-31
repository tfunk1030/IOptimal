"""Fit a lightweight damper calibration artifact from telemetry samples."""

from __future__ import annotations

from calibration.models import FittedModelArtifact, NormalizedTelemetrySample


def fit_damper_model(
    *,
    car: str,
    track: str,
    samples: list[NormalizedTelemetrySample],
) -> FittedModelArtifact:
    """Publish a placeholder/calibrated-by-config damper artifact.

    The runtime damper solver already contains the underlying physics model.
    This artifact is used to override per-car click-to-force coefficients and
    architecture flags as those become empirically calibrated.
    """

    return FittedModelArtifact(
        car=car,
        track=track,
        model_type="damper_model",
        metadata={
            "fit_method": "manual_or_physics_default",
        },
        parameters={},
        metrics={"samples": len(samples)},
    )
