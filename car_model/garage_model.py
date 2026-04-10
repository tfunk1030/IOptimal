"""Garage model builder — auto-populated from every IBT analysis.

Every time an IBT file is processed, call:
    GarageModelBuilder.update_from_observation(car, track, observation_dict)

This builds and maintains per-car-per-track JSON files at:
    data/garage_models/{car}/{track_slug}.json

Each file records:
  1. Every parameter the car exposes and its legal/observed range
  2. Telemetry statistics (RH, shock velocity, LLTD) per track
  3. Best observed lap time and the setup that produced it
  4. Calibration confidence tier for each physics constant

Solvers query this instead of hardcoded constants. New car = drop an IBT,
garage model builds itself.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MODELS_DIR = Path(__file__).resolve().parent.parent / "data" / "garage_models"


# ─── Parameter metadata ───────────────────────────────────────────────────────

@dataclass
class ParamStats:
    """Statistics for one tunable parameter across all observed sessions."""
    name: str
    unit: str = ""
    param_type: str = "continuous"   # "continuous" | "indexed" | "discrete" | "label"
    n_sessions: int = 0
    min_observed: float | None = None
    max_observed: float | None = None
    mean_observed: float | None = None
    stdev_observed: float | None = None
    observed_values: list = field(default_factory=list)  # all unique values seen

    def update(self, value: Any) -> None:
        if value is None:
            return
        try:
            fv = float(value)
        except (TypeError, ValueError):
            # String/label param
            self.param_type = "label"
            sv = str(value)
            if sv not in self.observed_values:
                self.observed_values.append(sv)
            self.n_sessions += 1
            return

        self.n_sessions += 1
        if fv not in self.observed_values:
            self.observed_values.append(fv)
            self.observed_values.sort()
        if self.min_observed is None or fv < self.min_observed:
            self.min_observed = fv
        if self.max_observed is None or fv > self.max_observed:
            self.max_observed = fv


# ─── Telemetry channel statistics ─────────────────────────────────────────────

@dataclass
class TelemetryStats:
    """Running statistics for one telemetry channel across sessions at a track."""
    channel: str
    unit: str = ""
    n_sessions: int = 0
    mean: float | None = None
    stdev: float | None = None
    min_val: float | None = None
    max_val: float | None = None
    _values: list = field(default_factory=list, repr=False)

    def update(self, value: float | None) -> None:
        if value is None:
            return
        try:
            fv = float(value)
        except (TypeError, ValueError):
            return
        self._values.append(fv)
        self.n_sessions = len(self._values)
        self.mean = statistics.mean(self._values)
        self.stdev = statistics.stdev(self._values) if len(self._values) > 1 else 0.0
        self.min_val = min(self._values)
        self.max_val = max(self._values)

    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "unit": self.unit,
            "n_sessions": self.n_sessions,
            "mean": round(self.mean, 4) if self.mean is not None else None,
            "stdev": round(self.stdev, 5) if self.stdev is not None else None,
            "min": round(self.min_val, 3) if self.min_val is not None else None,
            "max": round(self.max_val, 3) if self.max_val is not None else None,
        }


# ─── Best lap tracking ────────────────────────────────────────────────────────

@dataclass
class BestLap:
    lap_time_s: float = float("inf")
    setup_snapshot: dict = field(default_factory=dict)
    telemetry_snapshot: dict = field(default_factory=dict)
    ibt_file: str = ""
    timestamp: str = ""


# ─── Garage model ────────────────────────────────────────────────────────────

@dataclass
class GarageModel:
    """Per-car-per-track garage model. Auto-built from IBT observations.

    Tracks:
      - Which parameters exist for this car and their observed ranges
      - Telemetry statistics at this track
      - Best lap and its setup
      - Physics calibration data (lltd, shock velocities, m_eff)
    """
    car: str
    track: str
    n_sessions: int = 0
    last_updated: str = ""

    # Parameter registry: param_name → ParamStats
    parameters: dict[str, ParamStats] = field(default_factory=dict)

    # Telemetry statistics — channels that feed physics model
    telemetry: dict[str, TelemetryStats] = field(default_factory=dict)

    # Best observed lap
    best_lap: BestLap = field(default_factory=BestLap)

    # Physics calibration extracted from IBT
    # These feed directly into the solver — supersede hardcoded ESTIMATE values
    physics: dict[str, Any] = field(default_factory=dict)

    def update_from_observation(self, obs: dict) -> None:
        """Ingest one observation dict (from learnings/observations/*.json).

        Updates parameter ranges, telemetry stats, best lap, and physics.
        Called automatically by the IBT analysis pipeline.
        """
        self.n_sessions += 1
        self.last_updated = datetime.now(timezone.utc).isoformat()

        # ── Setup parameters ──────────────────────────────────────────
        setup = obs.get("setup", {})
        for param_name, value in setup.items():
            if param_name in ("adapter_name", "roof_light_color"):
                continue
            if param_name not in self.parameters:
                self.parameters[param_name] = ParamStats(name=param_name)
            self.parameters[param_name].update(value)

        # ── Telemetry ─────────────────────────────────────────────────
        telem = obs.get("telemetry", {})
        _TELEMETRY_CHANNELS = {
            "dynamic_front_rh_mm":       "mm",
            "dynamic_rear_rh_mm":        "mm",
            "front_rh_std_mm":           "mm",
            "rear_rh_std_mm":            "mm",
            "front_shock_vel_p95_mps":   "m/s",
            "rear_shock_vel_p95_mps":    "m/s",
            "front_shock_vel_p99_mps":   "m/s",
            "rear_shock_vel_p99_mps":    "m/s",
            "lltd_measured":             "",
            "peak_lat_g":                "g",
            "body_roll_p95_deg":         "deg",
            "roll_gradient_deg_per_g":   "deg/g",
        }
        for channel, unit in _TELEMETRY_CHANNELS.items():
            val = telem.get(channel)
            if val is not None:
                if channel not in self.telemetry:
                    self.telemetry[channel] = TelemetryStats(channel=channel, unit=unit)
                self.telemetry[channel].update(float(val))

        # ── Physics calibration from telemetry ────────────────────────
        # Extract physics constants that calibrate the solver.
        # These are track+car specific and must come from real IBT.

        vp99f = telem.get("front_shock_vel_p99_mps")
        vp99r = telem.get("rear_shock_vel_p99_mps")
        lltd  = telem.get("lltd_measured")

        if vp99f is not None:
            self._update_physics_stat("vp99_front_mps", float(vp99f))
        if vp99r is not None:
            self._update_physics_stat("vp99_rear_mps", float(vp99r))
        if lltd is not None:
            lltd_f = float(lltd)
            if 0.30 < lltd_f < 0.70:  # sanity check
                self._update_physics_stat("lltd_measured", lltd_f)

        # m_eff back-calculation from shock velocity + spring rate + RH std
        # Only valid when all three are available AND the spring rate is a real
        # physical value in N/mm (not a raw garage index like Ferrari/Acura use).
        # The observation dict stores "front_heave_nmm" (decoded N/mm) when
        # available and "front_heave_index" (raw index) for indexed cars.
        # We only compute m_eff when we have a real N/mm value; raw indices like
        # Ferrari's 0–10 scale would produce wildly wrong mass estimates.
        k_front_nmm_raw = setup.get("front_heave_nmm")
        k_front_idx = setup.get("front_heave_index")
        frh_std = telem.get("front_rh_std_mm")

        # Use decoded N/mm if available and looks physically plausible (>5 N/mm).
        # Reject raw indices: a Ferrari index of 5 would give m_eff ~ 0.3 kg
        # instead of ~500 kg.  If front_heave_nmm is actually an index value
        # (same as front_heave_index), skip the computation.
        _use_k = None
        if k_front_nmm_raw is not None:
            k_val = float(k_front_nmm_raw)
            # Index values are typically integers ≤ 20; real spring rates ≥ 30 N/mm.
            # Guard: only accept values that are plausibly spring rates.
            is_likely_index = (
                k_front_idx is not None and abs(k_val - float(k_front_idx)) < 0.5
            )
            if k_val > 20.0 and not is_likely_index:
                _use_k = k_val

        if _use_k is not None and frh_std and float(frh_std) > 0:
            vp99f_val = telem.get("front_shock_vel_p99_mps")
            if vp99f_val is not None and float(vp99f_val) > 0.001:
                # m_eff = k_N/m * (excursion_m / vp99)^2, excursion = std * 2.33
                # k_N/m = k_nmm * 1000
                excursion_m = float(frh_std) * 2.33 / 1000.0
                ratio_sq = (excursion_m / float(vp99f_val)) ** 2
                m_eff_kg = _use_k * 1000.0 * ratio_sq
                # Plausibility gate: GTP sprung mass per corner ~ 100–600 kg
                if 80.0 < m_eff_kg < 700.0:
                    self._update_physics_stat("m_eff_front_ratio", ratio_sq)
                    self._update_physics_stat("m_eff_front_input_k_nmm", _use_k)

        # ── Best lap ──────────────────────────────────────────────────
        perf = obs.get("performance", {})
        lap_time = perf.get("best_lap_time_s", float("inf"))
        if isinstance(lap_time, (int, float)) and lap_time < self.best_lap.lap_time_s:
            self.best_lap = BestLap(
                lap_time_s=float(lap_time),
                setup_snapshot=dict(setup),
                telemetry_snapshot={
                    k: telem.get(k)
                    for k in ["dynamic_front_rh_mm", "dynamic_rear_rh_mm",
                               "lltd_measured", "front_shock_vel_p99_mps",
                               "front_rh_std_mm", "rear_rh_std_mm"]
                },
                ibt_file=obs.get("ibt_path", ""),
                timestamp=obs.get("timestamp", ""),
            )

    def _update_physics_stat(self, key: str, value: float) -> None:
        if key not in self.physics:
            self.physics[key] = {"values": [], "mean": None, "stdev": None}
        self.physics[key]["values"].append(value)
        vals = self.physics[key]["values"]
        self.physics[key]["mean"] = statistics.mean(vals)
        self.physics[key]["stdev"] = statistics.stdev(vals) if len(vals) > 1 else 0.0
        self.physics[key]["n"] = len(vals)

    def get_physics(self, key: str, default: float | None = None) -> float | None:
        """Get calibrated physics value (mean over all sessions). None if not calibrated."""
        entry = self.physics.get(key)
        if entry and entry.get("n", 0) >= 1:
            return entry["mean"]
        return default

    def calibration_summary(self) -> dict[str, str]:
        """Report calibration status of each physics constant."""
        def tier(key: str, min_sessions: int = 3) -> str:
            entry = self.physics.get(key)
            if not entry:
                return "UNKNOWN"
            n = entry.get("n", 0)
            if n >= min_sessions:
                return f"CALIBRATED ({n} sessions)"
            return f"ESTIMATED ({n} session{'s' if n != 1 else ''})"

        return {
            "vp99_front_mps":    tier("vp99_front_mps"),
            "vp99_rear_mps":     tier("vp99_rear_mps"),
            "lltd_measured":     tier("lltd_measured"),
            "m_eff_front_ratio": tier("m_eff_front_ratio"),
        }

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        def _param(p: ParamStats) -> dict:
            d = {
                "unit": p.unit,
                "type": p.param_type,
                "n_sessions": p.n_sessions,
            }
            if p.min_observed is not None:
                d["min_observed"] = round(p.min_observed, 3)
            if p.max_observed is not None:
                d["max_observed"] = round(p.max_observed, 3)
            if p.observed_values:
                d["values"] = [
                    round(v, 3) if isinstance(v, float) else v
                    for v in sorted(p.observed_values)
                ]
            return d

        return {
            "car": self.car,
            "track": self.track,
            "n_sessions": self.n_sessions,
            "last_updated": self.last_updated,
            "best_lap": {
                "lap_time_s": self.best_lap.lap_time_s,
                "setup": self.best_lap.setup_snapshot,
                "telemetry": self.best_lap.telemetry_snapshot,
                "ibt_file": self.best_lap.ibt_file,
            } if self.best_lap.lap_time_s < float("inf") else None,
            "physics": self.physics,
            "calibration_status": self.calibration_summary(),
            "parameters": {k: _param(v) for k, v in sorted(self.parameters.items())},
            "telemetry": {k: v.to_dict() for k, v in sorted(self.telemetry.items())},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GarageModel":
        """Deserialize from saved JSON."""
        model = cls(car=data["car"], track=data["track"])
        model.n_sessions = data.get("n_sessions", 0)
        model.last_updated = data.get("last_updated", "")
        model.physics = data.get("physics", {})

        # Restore best lap
        bl = data.get("best_lap")
        if bl and bl.get("lap_time_s") is not None:
            model.best_lap = BestLap(
                lap_time_s=bl["lap_time_s"],
                setup_snapshot=bl.get("setup", {}),
                telemetry_snapshot=bl.get("telemetry", {}),
                ibt_file=bl.get("ibt_file", ""),
            )

        # Restore parameter stats
        for name, pdata in data.get("parameters", {}).items():
            ps = ParamStats(name=name)
            ps.unit = pdata.get("unit", "")
            ps.param_type = pdata.get("type", "continuous")
            ps.n_sessions = pdata.get("n_sessions", 0)
            ps.min_observed = pdata.get("min_observed")
            ps.max_observed = pdata.get("max_observed")
            ps.observed_values = pdata.get("values", [])
            model.parameters[name] = ps

        # Restore telemetry stats
        for ch, tdata in data.get("telemetry", {}).items():
            ts = TelemetryStats(channel=ch, unit=tdata.get("unit", ""))
            ts.n_sessions = tdata.get("n_sessions", 0)
            ts.mean = tdata.get("mean")
            ts.stdev = tdata.get("stdev")
            ts.min_val = tdata.get("min")
            ts.max_val = tdata.get("max")
            model.telemetry[ch] = ts

        return model


# ─── Builder — single entry point called by IBT pipeline ─────────────────────

class GarageModelBuilder:
    """Manages the garage model registry. Called once per IBT analysis."""

    @staticmethod
    def _model_path(car: str, track: str) -> Path:
        from car_model.registry import track_slug
        slug = track_slug(track)
        return _MODELS_DIR / car.lower() / f"{slug}.json"

    @classmethod
    def load(cls, car: str, track: str) -> GarageModel:
        """Load existing garage model or return empty one."""
        path = cls._model_path(car, track)
        if path.exists():
            try:
                return GarageModel.from_dict(json.loads(path.read_text()))
            except Exception:
                pass  # corrupted file — start fresh
        return GarageModel(car=car.lower(), track=track)

    @classmethod
    def save(cls, model: GarageModel) -> Path:
        """Persist garage model to disk."""
        path = cls._model_path(model.car, model.track)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(model.to_dict(), indent=2))
        return path

    @classmethod
    def update_from_observation(cls, car: str, track: str, obs: dict) -> GarageModel:
        """Main entry point — call this after every IBT analysis.

        Loads the existing garage model for (car, track), adds the new
        observation, and saves. Returns the updated model.

        Args:
            car:   Canonical car name ("ferrari", "bmw", etc.)
            track: Track name from IBT (e.g. "Hockenheimring Baden-Württemberg")
            obs:   Observation dict from learnings/observations/*.json

        Returns:
            Updated GarageModel
        """
        model = cls.load(car, track)
        model.update_from_observation(obs)
        cls.save(model)
        return model

    @classmethod
    def rebuild_all(cls) -> dict[str, int]:
        """Rebuild all garage models from scratch from observation files.

        Use when observations have been added/corrected outside the normal
        pipeline. Returns {car_track: session_count} for each model built.
        """
        import glob
        obs_dir = _MODELS_DIR.parent / "learnings" / "observations"
        files = sorted(glob.glob(str(obs_dir / "*.json")))
        sessions = [f for f in files if "__lap_" not in f]

        # Clear existing models
        for p in _MODELS_DIR.rglob("*.json"):
            p.unlink()

        counts: dict[str, int] = {}
        for fpath in sessions:
            try:
                obs = json.loads(Path(fpath).read_text())
                car = obs.get("car", "").lower()
                track = obs.get("track", "unknown")
                if not car or not track:
                    continue
                cls.update_from_observation(car, track, obs)
                key = f"{car}/{track}"
                counts[key] = counts.get(key, 0) + 1
            except Exception as e:
                print(f"[garage_model] Skip {fpath}: {e}")

        return counts

    @classmethod
    def get_best_lap(cls, car: str, track: str) -> BestLap | None:
        """Return fastest observed setup at (car, track). None if no data."""
        model = cls.load(car, track)
        if model.best_lap.lap_time_s < float("inf"):
            return model.best_lap
        return None

    @classmethod
    def get_physics(cls, car: str, track: str, key: str, default=None):
        """Query a specific physics constant from garage model."""
        model = cls.load(car, track)
        return model.get_physics(key, default)

    @classmethod
    def get_track_profile(cls, car: str, track: str) -> dict:
        """Return track physics profile for solver consumption.

        Returns the telemetry-derived constants the solver needs for this
        car at this track. Falls back to None values if not yet calibrated.
        """
        model = cls.load(car, track)
        return {
            "car": car,
            "track": track,
            "n_sessions": model.n_sessions,
            "vp99_front_mps":    model.get_physics("vp99_front_mps"),
            "vp99_rear_mps":     model.get_physics("vp99_rear_mps"),
            "lltd_measured":     model.get_physics("lltd_measured"),
            "dynamic_front_rh_mm": model.telemetry.get("dynamic_front_rh_mm", TelemetryStats("")).mean,
            "dynamic_rear_rh_mm":  model.telemetry.get("dynamic_rear_rh_mm", TelemetryStats("")).mean,
            "front_rh_std_mm":     model.telemetry.get("front_rh_std_mm", TelemetryStats("")).mean,
            "calibration_status":  model.calibration_summary(),
            "best_lap_time_s":     model.best_lap.lap_time_s if model.best_lap.lap_time_s < float("inf") else None,
        }
