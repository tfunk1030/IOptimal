"""Feature builders for calibration model fitting."""

from __future__ import annotations

from typing import Any

import numpy as np

from calibration.models import LinearMetricModel, NormalizedGarageSample, NormalizedTelemetrySample


DEFAULT_GARAGE_TARGETS = (
    "static_front_rh_mm",
    "static_rear_rh_mm",
    "front_rh_at_speed_mm",
    "rear_rh_at_speed_mm",
    "torsion_bar_turns",
    "rear_torsion_bar_turns",
    "heave_spring_defl_static_mm",
    "heave_slider_defl_static_mm",
    "third_spring_defl_static_mm",
    "third_slider_defl_static_mm",
    "front_shock_defl_static_mm",
    "rear_shock_defl_static_mm",
)

DEFAULT_TELEMETRY_TARGETS = (
    "front_heave_travel_used_pct",
    "front_rh_std_mm",
    "rear_rh_std_mm",
    "front_rh_excursion_measured_mm",
    "understeer_low_speed_deg",
    "understeer_high_speed_deg",
    "body_slip_p95_deg",
    "rear_power_slip_ratio_p95",
    "front_braking_lock_ratio_p95",
    "pitch_range_braking_deg",
    "rear_shock_oscillation_hz",
)


def numeric_inputs(sample: NormalizedGarageSample | NormalizedTelemetrySample) -> dict[str, float]:
    values: dict[str, float] = {}
    for key, value in dict(sample.canonical_inputs).items():
        if isinstance(value, bool):
            values[key] = float(int(value))
        elif isinstance(value, (int, float)):
            values[key] = float(value)
    return values


def target_value(source: dict[str, Any], target: str) -> float | None:
    value = source.get(target)
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    return None


def feature_matrix_from_samples(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a numeric feature matrix from row dicts."""
    numeric_keys = sorted(
        {
            str(key)
            for row in rows
            for key, value in row.items()
            if isinstance(value, (int, float, bool))
        }
    )
    matrix = np.asarray(
        [
            [
                float(int(value)) if isinstance((value := row.get(key)), bool)
                else float(value) if isinstance(value, (int, float))
                else 0.0
                for key in numeric_keys
            ]
            for row in rows
        ],
        dtype=float,
    ) if rows and numeric_keys else np.zeros((len(rows), 0), dtype=float)
    return {"rows": rows, "feature_names": numeric_keys, "matrix": matrix}


def fit_linear_model(
    *,
    matrix: dict[str, Any],
    target: str,
    min_samples: int = 3,
    max_features: int = 24,
) -> LinearMetricModel | None:
    """Fit an ordinary least-squares model against a target key."""
    rows = list(matrix.get("rows") or [])
    feature_names = list(matrix.get("feature_names") or [])
    x = matrix.get("matrix")
    filtered_indices = []
    y_values = []
    for idx, row in enumerate(rows):
        value = target_value(row, target)
        if value is None:
            continue
        filtered_indices.append(idx)
        y_values.append(float(value))
    if len(filtered_indices) < max(1, min_samples):
        return None
    if feature_names:
        candidate_feature_indices = [
            idx for idx, feature_name in enumerate(feature_names)
            if feature_name != target
        ]
        filtered_x_all = (
            x[filtered_indices, :][:, candidate_feature_indices]
            if candidate_feature_indices
            else np.zeros((len(filtered_indices), 0), dtype=float)
        )
        candidate_feature_names = [feature_names[idx] for idx in candidate_feature_indices]
    else:
        filtered_x_all = np.zeros((len(filtered_indices), 0), dtype=float)
        candidate_feature_names = []

    y = np.asarray(y_values, dtype=float)
    selected_x = filtered_x_all
    selected_feature_names = candidate_feature_names
    if selected_x.shape[1] > 0:
        variances = np.var(selected_x, axis=0)
        non_constant_indices = [
            idx for idx, variance in enumerate(variances)
            if float(variance) > 1e-12
        ]
        selected_x = selected_x[:, non_constant_indices]
        selected_feature_names = [selected_feature_names[idx] for idx in non_constant_indices]

    if selected_x.shape[1] > 0 and selected_x.shape[1] > max_features:
        target_limit = max(1, min(max_features, len(filtered_indices) - 1))
        centered_y = y - np.mean(y)
        y_std = float(np.std(centered_y))
        corr_scores: list[float] = []
        for col_idx in range(selected_x.shape[1]):
            centered_col = selected_x[:, col_idx] - np.mean(selected_x[:, col_idx])
            col_std = float(np.std(centered_col))
            if col_std <= 1e-12 or y_std <= 1e-12:
                corr_scores.append(0.0)
                continue
            corr = float(np.dot(centered_col, centered_y) / (len(centered_y) * col_std * y_std))
            corr_scores.append(abs(corr))
        top_indices = list(np.argsort(corr_scores)[-target_limit:])
        selected_x = selected_x[:, top_indices]
        selected_feature_names = [selected_feature_names[idx] for idx in top_indices]

    design = np.column_stack([np.ones(len(filtered_indices)), selected_x])
    coeffs, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
    y_hat = design @ coeffs
    residual = y - y_hat
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    ss_res = float(np.sum(residual ** 2))
    r_squared = None if ss_tot <= 1e-9 else max(0.0, 1.0 - ss_res / ss_tot)
    rmse = float(np.sqrt(np.mean(residual ** 2)))
    return LinearMetricModel(
        target=target,
        intercept=float(coeffs[0]),
        coefficients={
            selected_feature_names[idx]: float(coeffs[idx + 1])
            for idx in range(len(selected_feature_names))
        },
        r_squared=r_squared,
        rmse=rmse,
        samples=len(filtered_indices),
    )


def build_garage_feature_matrix(samples: list[NormalizedGarageSample]) -> tuple[np.ndarray, list[str]]:
    rows = [numeric_inputs(sample) for sample in samples]
    matrix = feature_matrix_from_samples(rows)
    return matrix["matrix"], list(matrix["feature_names"])
