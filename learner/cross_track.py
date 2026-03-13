"""Cross-track empirical models — combine data across all tracks for a car.

Some physical relationships are car-intrinsic (not track-dependent):
- Suspension motion ratios
- ARB stiffness constants
- Damper force-per-click calibration
- Torsion bar OD-to-rate constant

Others are track-influenced but still informative across tracks:
- Aero compression at speed (mostly car-dependent)
- Body roll characteristics (spring/ARB dependent)
- Heave effective mass (partially car, partially surface)

This module fits global models by pooling all observations for a car,
weighting by sample count and confidence, and flagging where a track
deviates significantly from the global trend (track-specific anomaly).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from learner.knowledge_store import KnowledgeStore
from learner.empirical_models import EmpiricalModelSet, _safe_linear_fit


@dataclass
class TrackAnomaly:
    """A track where a metric deviates significantly from the global model."""
    track: str
    metric: str
    global_mean: float
    track_mean: float
    deviation_sigma: float  # how many sigma away from global
    sample_count: int
    interpretation: str


@dataclass
class GlobalCarModel:
    """Cross-track empirical model for one car."""
    car: str
    total_sessions: int = 0
    tracks_included: list[str] = field(default_factory=list)

    # Global calibration corrections (car-intrinsic properties)
    aero_compression_front_mm: float | None = None
    aero_compression_rear_mm: float | None = None
    roll_gradient_global_deg_per_g: float | None = None
    m_eff_front_global_kg: float | None = None

    # Per-track anomalies
    anomalies: list[TrackAnomaly] = field(default_factory=list)

    # Confidence
    confidence: str = "no_data"
    last_updated: str = ""

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"Global Model: {self.car}",
            f"  Sessions: {self.total_sessions} across {len(self.tracks_included)} tracks",
            f"  Confidence: {self.confidence}",
        ]
        if self.aero_compression_front_mm is not None:
            lines.append(f"  Aero compression front (global): {self.aero_compression_front_mm:.1f} mm")
        if self.aero_compression_rear_mm is not None:
            lines.append(f"  Aero compression rear (global): {self.aero_compression_rear_mm:.1f} mm")
        if self.roll_gradient_global_deg_per_g is not None:
            lines.append(f"  Roll gradient (global): {self.roll_gradient_global_deg_per_g:.3f} deg/g")
        if self.m_eff_front_global_kg is not None:
            lines.append(f"  Front m_eff (global): {self.m_eff_front_global_kg:.1f} kg")
        if self.anomalies:
            lines.append(f"  Track anomalies: {len(self.anomalies)}")
            for a in self.anomalies[:5]:
                lines.append(f"    {a.track}: {a.metric} = {a.track_mean:.3f} "
                             f"(global {a.global_mean:.3f}, {a.deviation_sigma:+.1f}σ)")
                lines.append(f"      -> {a.interpretation}")
        return "\n".join(lines)


def build_global_model(car: str, store: KnowledgeStore | None = None) -> GlobalCarModel:
    """Build a cross-track empirical model for one car.

    Pools all observations across all tracks and fits global relationships.
    Identifies tracks that deviate significantly from the global trend.
    """
    store = store or KnowledgeStore()
    all_obs = store.list_observations(car=car)

    model = GlobalCarModel(
        car=car,
        total_sessions=len(all_obs),
        last_updated=datetime.now(timezone.utc).isoformat(),
    )

    if len(all_obs) < 2:
        model.confidence = "no_data"
        return model

    # Group by track
    by_track: dict[str, list[dict]] = {}
    for obs in all_obs:
        track = obs.get("track", "unknown")
        by_track.setdefault(track, []).append(obs)

    model.tracks_included = sorted(by_track.keys())

    # ── Global aero compression ──
    front_comp_all = []
    rear_comp_all = []
    front_comp_by_track: dict[str, list[float]] = {}
    rear_comp_by_track: dict[str, list[float]] = {}

    for obs in all_obs:
        sf = obs.get("setup", {}).get("front_rh_static", 0)
        sr = obs.get("setup", {}).get("rear_rh_static", 0)
        df = obs.get("telemetry", {}).get("dynamic_front_rh_mm", 0)
        dr = obs.get("telemetry", {}).get("dynamic_rear_rh_mm", 0)
        track = obs.get("track", "unknown")

        if sf > 0 and df > 0:
            fc = sf - df
            front_comp_all.append(fc)
            front_comp_by_track.setdefault(track, []).append(fc)
        if sr > 0 and dr > 0:
            rc = sr - dr
            rear_comp_all.append(rc)
            rear_comp_by_track.setdefault(track, []).append(rc)

    if front_comp_all:
        model.aero_compression_front_mm = float(np.mean(front_comp_all))
    if rear_comp_all:
        model.aero_compression_rear_mm = float(np.mean(rear_comp_all))

    # ── Global roll gradient ──
    rg_all = []
    rg_by_track: dict[str, list[float]] = {}

    for obs in all_obs:
        rg = obs.get("telemetry", {}).get("roll_gradient_deg_per_g", 0)
        track = obs.get("track", "unknown")
        if rg > 0.1:
            rg_all.append(rg)
            rg_by_track.setdefault(track, []).append(rg)

    if rg_all:
        model.roll_gradient_global_deg_per_g = float(np.mean(rg_all))

    # ── Global m_eff front ──
    m_eff_all = []
    for obs in all_obs:
        heave = obs.get("setup", {}).get("front_heave_nmm", 0)
        var = obs.get("telemetry", {}).get("front_rh_std_mm", 0)
        sv_p99 = obs.get("telemetry", {}).get("front_shock_vel_p99_mps", 0)
        if heave > 0 and var > 0 and sv_p99 > 0:
            exc = var * 2.33
            k_nm = heave * 1000
            m_eff = k_nm * (exc / 1000 / sv_p99) ** 2
            if 50 < m_eff < 5000:  # sanity bounds
                m_eff_all.append(m_eff)

    if m_eff_all:
        model.m_eff_front_global_kg = float(np.mean(m_eff_all))

    # ── Detect track anomalies ──
    _detect_anomalies(model, rg_by_track, "roll_gradient_deg_per_g",
                      float(np.mean(rg_all)) if rg_all else 0,
                      float(np.std(rg_all)) if len(rg_all) > 1 else 0)

    _detect_anomalies(model, front_comp_by_track, "aero_compression_front_mm",
                      float(np.mean(front_comp_all)) if front_comp_all else 0,
                      float(np.std(front_comp_all)) if len(front_comp_all) > 1 else 0)

    # Confidence
    if model.total_sessions >= 10 and len(model.tracks_included) >= 2:
        model.confidence = "high"
    elif model.total_sessions >= 4:
        model.confidence = "medium"
    else:
        model.confidence = "low"

    # Save
    model_id = f"{car}_global_empirical"
    store.save_model(model_id, model.to_dict())

    return model


def _detect_anomalies(
    model: GlobalCarModel,
    by_track: dict[str, list[float]],
    metric: str,
    global_mean: float,
    global_std: float,
    threshold_sigma: float = 1.5,
) -> None:
    """Flag tracks where a metric deviates significantly from global mean."""
    if global_std < 1e-6 or global_mean == 0:
        return

    for track, values in by_track.items():
        if len(values) < 2:
            continue
        track_mean = float(np.mean(values))
        deviation = (track_mean - global_mean) / global_std

        if abs(deviation) >= threshold_sigma:
            # Generate interpretation
            if "roll_gradient" in metric:
                if deviation > 0:
                    interpretation = (f"{track} produces more body roll — "
                                     "surface roughness or camber changes may "
                                     "require stiffer ARB baseline")
                else:
                    interpretation = (f"{track} produces less roll — "
                                     "smooth surface allows softer ARB for grip")
            elif "aero_compression" in metric:
                if deviation > 0:
                    interpretation = (f"{track} shows more aero compression — "
                                     "higher average speed loads the floor more")
                else:
                    interpretation = (f"{track} shows less compression — "
                                     "lower speeds or less efficient floor seal")
            else:
                direction = "higher" if deviation > 0 else "lower"
                interpretation = f"{metric} is {direction} than global average at {track}"

            model.anomalies.append(TrackAnomaly(
                track=track,
                metric=metric,
                global_mean=round(global_mean, 4),
                track_mean=round(track_mean, 4),
                deviation_sigma=round(deviation, 2),
                sample_count=len(values),
                interpretation=interpretation,
            ))
