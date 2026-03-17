from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class SetupCluster:
    center: dict[str, float]
    spreads: dict[str, float]
    member_sessions: list[str] = field(default_factory=list)
    label: str = ""


@dataclass
class SetupDistance:
    distance_score: float
    per_parameter_z: dict[str, float] = field(default_factory=dict)
    outlier_parameters: list[str] = field(default_factory=list)


DEFAULT_SETUP_PARAMETERS = [
    "front_pushrod_mm",
    "rear_pushrod_mm",
    "front_heave_nmm",
    "rear_third_nmm",
    "front_torsion_od_mm",
    "rear_spring_nmm",
    "front_arb_blade",
    "rear_arb_blade",
    "front_camber_deg",
    "rear_camber_deg",
    "front_toe_mm",
    "rear_toe_mm",
    "brake_bias_pct",
    "diff_preload_nm",
]


def _extract_value(sample: Any, parameter: str) -> float | None:
    if isinstance(sample, dict):
        value = sample.get(parameter)
    else:
        value = getattr(sample, parameter, None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_setup_cluster(
    setups: list[Any],
    *,
    parameters: list[str] | None = None,
    member_sessions: list[str] | None = None,
    label: str = "",
) -> SetupCluster:
    parameters = parameters or DEFAULT_SETUP_PARAMETERS
    center: dict[str, float] = {}
    spreads: dict[str, float] = {}
    for parameter in parameters:
        values = [_extract_value(sample, parameter) for sample in setups]
        filtered = np.array([value for value in values if value is not None], dtype=float)
        if filtered.size == 0:
            continue
        center[parameter] = round(float(np.mean(filtered)), 4)
        spreads[parameter] = round(max(float(np.std(filtered)), 0.001), 4)
    return SetupCluster(
        center=center,
        spreads=spreads,
        member_sessions=list(member_sessions or []),
        label=label,
    )


def compute_setup_distance(setup: Any, cluster: SetupCluster) -> SetupDistance:
    if not cluster.center:
        return SetupDistance(distance_score=0.0, per_parameter_z={}, outlier_parameters=[])

    per_parameter_z: dict[str, float] = {}
    outliers: list[str] = []
    for parameter, center in cluster.center.items():
        value = _extract_value(setup, parameter)
        if value is None:
            continue
        spread = max(cluster.spreads.get(parameter, 0.001), 0.001)
        z = abs(value - center) / spread
        per_parameter_z[parameter] = round(float(z), 3)
        if z > 2.5:
            outliers.append(parameter)

    distance = round(float(np.mean(list(per_parameter_z.values()))) if per_parameter_z else 0.0, 3)
    return SetupDistance(
        distance_score=distance,
        per_parameter_z=per_parameter_z,
        outlier_parameters=outliers,
    )
