"""Learn healthy setup regions from repeatedly good sessions.

Groups similar setups into clusters and labels them by outcome quality.
New setups can be compared to known clusters to determine if they are
within a tested region or exploring new territory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class SetupCluster:
    """A cluster of similar setups with known outcome quality."""

    center: dict[str, float] = field(default_factory=dict)
    spreads: dict[str, float] = field(default_factory=dict)
    member_sessions: list[str] = field(default_factory=list)
    label: str = ""  # e.g. "safe-fast sebring bmw baseline"

    def to_dict(self) -> dict[str, Any]:
        return {
            "center": self.center,
            "spreads": self.spreads,
            "member_sessions": self.member_sessions,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetupCluster:
        return cls(
            center=data.get("center", {}),
            spreads=data.get("spreads", {}),
            member_sessions=data.get("member_sessions", []),
            label=data.get("label", ""),
        )


@dataclass
class SetupDistance:
    """Distance of a setup from a cluster center."""

    distance_score: float = 0.0
    per_parameter_z: dict[str, float] = field(default_factory=dict)
    outlier_parameters: list[str] = field(default_factory=list)


def compute_setup_distance(
    cluster: SetupCluster,
    setup_values: dict[str, float],
) -> SetupDistance:
    """Compute how far a setup is from a cluster center.

    Uses z-scores per parameter. Parameters with |z| > 2.0 are flagged
    as outliers.

    Args:
        cluster: The reference cluster.
        setup_values: Dict of parameter_name -> value.

    Returns:
        SetupDistance with per-parameter z-scores and outlier list.
    """
    per_z: dict[str, float] = {}
    outliers: list[str] = []

    for param, value in setup_values.items():
        center = cluster.center.get(param)
        spread = cluster.spreads.get(param)
        if center is None or spread is None or spread < 1e-9:
            continue
        z = (value - center) / spread
        per_z[param] = round(z, 3)
        if abs(z) > 2.0:
            outliers.append(param)

    total = float(np.sqrt(np.mean([z ** 2 for z in per_z.values()]))) if per_z else 0.0

    return SetupDistance(
        distance_score=round(total, 3),
        per_parameter_z=per_z,
        outlier_parameters=outliers,
    )


def build_cluster_from_sessions(
    session_setups: list[dict[str, float]],
    session_ids: list[str],
    label: str = "",
) -> SetupCluster:
    """Build a cluster from a list of setup parameter dicts.

    Computes center (mean) and spread (std) for each parameter.
    """
    if not session_setups:
        return SetupCluster(label=label)

    all_params: set[str] = set()
    for s in session_setups:
        all_params.update(s.keys())

    center: dict[str, float] = {}
    spreads: dict[str, float] = {}

    for param in sorted(all_params):
        values = [s.get(param, 0.0) for s in session_setups]
        arr = np.array(values, dtype=float)
        center[param] = round(float(np.mean(arr)), 4)
        spreads[param] = round(float(np.std(arr)), 4)

    return SetupCluster(
        center=center,
        spreads=spreads,
        member_sessions=session_ids,
        label=label,
    )


def save_clusters(clusters: list[SetupCluster], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    data = [c.to_dict() for c in clusters]
    Path(path).write_text(json.dumps(data, indent=2))


def load_clusters(path: str | Path) -> list[SetupCluster]:
    data = json.loads(Path(path).read_text())
    return [SetupCluster.from_dict(d) for d in data]
