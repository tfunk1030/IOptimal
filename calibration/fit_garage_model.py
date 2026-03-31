"""Fit garage-output regressions from normalized garage samples."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from calibration.feature_builders import build_garage_feature_matrix
from calibration.models import FittedModelArtifact, LinearMetricModel, NormalizedGarageSample


def _fit_linear(target_name: str, rows: list[NormalizedGarageSample], feature_names: list[str]) -> LinearMetricModel | None:
    filtered = [row for row in rows if target_name in row.garage_outputs and row.garage_outputs.get(target_name) is not None]
    if len(filtered) < max(3, min(6, len(feature_names) + 1)):
        return None
    x, _, = build_garage_feature_matrix(filtered)
    y = np.asarray([float(row.garage_outputs[target_name]) for row in filtered], dtype=float)
    design = np.column_stack([np.ones(len(filtered)), x])
    coeffs, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
    y_hat = design @ coeffs
    residual = y - y_hat
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    ss_res = float(np.sum(residual ** 2))
    r_squared = None if ss_tot <= 1e-9 else max(0.0, 1.0 - ss_res / ss_tot)
    rmse = float(np.sqrt(np.mean(residual ** 2)))
    return LinearMetricModel(
        target=target_name,
        intercept=float(coeffs[0]),
        coefficients={
            feature_names[idx]: float(coeffs[idx + 1])
            for idx in range(len(feature_names))
        },
        r_squared=r_squared,
        rmse=rmse,
        samples=len(filtered),
    )


def load_garage_samples(path: str | Path) -> list[NormalizedGarageSample]:
    rows: list[NormalizedGarageSample] = []
    sample_path = Path(path)
    if not sample_path.exists():
        raise FileNotFoundError(sample_path)
    for line in sample_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        rows.append(NormalizedGarageSample.from_dict(json.loads(text)))
    return rows


def fit_garage_model(*, car: str, track: str, samples: list[NormalizedGarageSample] | None = None, samples_path: str | Path | None = None) -> FittedModelArtifact:
    if samples is None:
        if samples_path is None:
            raise ValueError("Provide samples or samples_path.")
        samples = load_garage_samples(samples_path)
    x, feature_names = build_garage_feature_matrix(samples)
    _ = x  # feature_names are reused by _fit_linear over filtered subsets
    targets = [
        "static_front_rh_mm",
        "static_rear_rh_mm",
        "front_rh_at_speed_mm",
        "rear_rh_at_speed_mm",
        "torsion_bar_turns",
        "rear_torsion_bar_turns",
        "heave_spring_defl_static_mm",
        "heave_spring_defl_max_mm",
        "heave_slider_defl_static_mm",
        "heave_slider_defl_max_mm",
        "third_spring_defl_static_mm",
        "third_spring_defl_max_mm",
        "third_slider_defl_static_mm",
        "front_shock_defl_static_mm",
        "front_shock_defl_max_mm",
        "rear_shock_defl_static_mm",
        "rear_shock_defl_max_mm",
    ]
    fitted: dict[str, Any] = {}
    summary_metrics: dict[str, Any] = {"samples": len(samples), "targets": {}}
    rmses: list[float] = []
    for target in targets:
        model = _fit_linear(target, samples, feature_names)
        if model is None:
            continue
        fitted[target] = model.to_dict()
        if model.rmse is not None:
            rmses.append(float(model.rmse))
        summary_metrics["targets"][target] = {
            "samples": model.samples,
            "rmse": model.rmse,
            "r_squared": model.r_squared,
        }
    summary_metrics["mean_rmse"] = round(float(np.mean(rmses)), 6) if rmses else None
    return FittedModelArtifact(
        car=car,
        track=track,
        model_type="garage_model",
        metadata={
            "feature_names": feature_names,
            "description": "Linear/interpretable garage-output regressions from normalized garage samples.",
        },
        parameters={"models": fitted},
        metrics=summary_metrics,
    )
