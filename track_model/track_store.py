"""Cumulative track profile store — accumulates sessions into a consensus model.

Each IBT session contributes to a per-(track, car) store. The consensus()
method returns a standard TrackProfile that becomes more precise as sessions
accumulate. Statistical filtering (MAD outlier detection, quality gates)
rejects bad data (wet sessions, crashes, pit-lane contamination).

Store key: ``data/tracks/{track_slug}_{car_slug}_store.json``

Shock velocities are car-specific (motion ratios differ 0.6-1.0 across cars),
so stores are keyed per (track, car) pair — no cross-car averaging.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from track_model.profile import TrackProfile, BrakingZone, Corner, KerbEvent

logger = logging.getLogger(__name__)

# --- fcntl portability (Unix-only file locking) ---
try:
    import fcntl as _fcntl
    _HAS_FCNTL = True
except ImportError:
    _fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False


# ── Field classification for aggregation ──

# Safety-critical: P75 (conservative but noise-reducing vs single session)
_P75_FIELDS = frozenset({
    "shock_vel_p99_front_mps",
    "shock_vel_p99_rear_mps",
    "shock_vel_p99_front_clean_mps",
    "shock_vel_p99_rear_clean_mps",
    "shock_vel_p99_front_hs_mps",
    "shock_vel_p99_rear_hs_mps",
    "shock_vel_p95_front_kerb_mps",
    "shock_vel_p99_front_kerb_mps",
    "shock_vel_p95_rear_kerb_mps",
    "shock_vel_p99_rear_kerb_mps",
})

# Envelope: max (true physical track limits)
_MAX_FIELDS = frozenset({
    "peak_lat_g",
    "peak_braking_g",
    "peak_accel_g",
    "peak_vertical_g",
    "max_speed_kph",
})

# Envelope: min
_MIN_FIELDS = frozenset({
    "min_speed_kph",
    "best_lap_time_s",
})

# Identity: first session's value (fixed per track)
_FIRST_FIELDS = frozenset({
    "track_name",
    "track_config",
    "track_length_m",
})

# All remaining scalar float fields use median
# (speed profile, environment, suspension response, characterization shock vel)

# Distribution fields (stored as-is in snapshots, aggregated specially)
_DISTRIBUTION_FIELDS = frozenset({
    "speed_bands_kph",
    "shock_vel_histogram_front",
    "shock_vel_histogram_rear",
    "shock_vel_by_sector",
    "lateral_g",
    "body_roll_deg",
    "ride_heights_mm",
    "surface_profile",
    "elevation_profile",
})

# Spatial event fields
_SPATIAL_FIELDS = frozenset({
    "braking_zones",
    "corners",
    "kerb_events",
})

# All scalar float fields on TrackProfile (computed once)
_SCALAR_FLOAT_FIELDS: frozenset[str] | None = None


def _get_scalar_float_fields() -> frozenset[str]:
    """Lazily compute the set of scalar float fields on TrackProfile."""
    global _SCALAR_FLOAT_FIELDS
    if _SCALAR_FLOAT_FIELDS is not None:
        return _SCALAR_FLOAT_FIELDS
    import dataclasses
    result = set()
    skip = _DISTRIBUTION_FIELDS | _SPATIAL_FIELDS | _FIRST_FIELDS | {"car", "telemetry_source", "consensus_n_sessions"}
    for f in dataclasses.fields(TrackProfile):
        if f.name in skip:
            continue
        if f.type in ("float", "int") or f.default == 0.0 or f.default == 0:
            result.add(f.name)
    _SCALAR_FLOAT_FIELDS = frozenset(result)
    return _SCALAR_FLOAT_FIELDS


# ── SessionSnapshot ──

@dataclass
class SessionSnapshot:
    """One IBT session's contribution to the track store."""
    session_id: str
    timestamp: str
    lap_time_s: float
    ibt_source: str
    car: str
    scalars: dict[str, float] = field(default_factory=dict)
    distributions: dict[str, Any] = field(default_factory=dict)
    braking_zones: list[dict] = field(default_factory=list)
    corners: list[dict] = field(default_factory=list)
    kerb_events: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "SessionSnapshot":
        return SessionSnapshot(**d)

    @staticmethod
    def from_profile(profile: TrackProfile, session_id: str) -> "SessionSnapshot":
        """Extract a snapshot from a TrackProfile."""
        d = profile.to_dict()
        scalars = {}
        for fname in _get_scalar_float_fields():
            val = d.get(fname)
            if val is not None and isinstance(val, (int, float)):
                scalars[fname] = float(val)
        distributions = {}
        for fname in _DISTRIBUTION_FIELDS:
            val = d.get(fname)
            if val is not None:
                distributions[fname] = val
        # Identity fields stored in scalars too for convenience
        for fname in _FIRST_FIELDS:
            val = d.get(fname)
            if val is None and fname in ("track_name", "track_config"):
                val = ""  # Required str fields must not be None
            if val is not None:
                scalars[fname] = val  # type: ignore[assignment]
        return SessionSnapshot(
            session_id=session_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            lap_time_s=profile.best_lap_time_s,
            ibt_source=profile.telemetry_source or session_id,
            car=profile.car,
            scalars=scalars,
            distributions=distributions,
            braking_zones=d.get("braking_zones", []),
            corners=d.get("corners", []),
            kerb_events=d.get("kerb_events", []),
        )


# ── Quality gate ──

def _passes_quality_gate(
    profile: TrackProfile,
    existing_best_lap: float | None,
    n_sessions: int,
) -> tuple[bool, str]:
    """Check whether a session should be accepted into the store.

    Hard gates always apply. Relative gates activate at n_sessions >= 3.

    Returns (accepted, reason).
    """
    # Hard gates
    if profile.peak_vertical_g > 15.0:
        return False, f"crash spike (peak_vertical_g={profile.peak_vertical_g:.1f})"
    if profile.median_speed_kph < 100.0:
        return False, f"pit-lane contamination (median_speed={profile.median_speed_kph:.0f} kph)"
    if profile.shock_vel_p99_front_mps > 2.0:
        return False, f"extreme shock anomaly (p99_front={profile.shock_vel_p99_front_mps:.3f} m/s)"

    # Relative gates (need baseline data)
    if n_sessions >= 3:
        if profile.air_density_kg_m3 > 1.25:
            return False, f"wet session proxy (air_density={profile.air_density_kg_m3:.3f})"
        if profile.track_temp_c < 10.0:
            return False, f"rain conditions (track_temp={profile.track_temp_c:.1f}C)"
        if existing_best_lap is not None and existing_best_lap > 0:
            ceiling = existing_best_lap * 1.15
            if profile.best_lap_time_s > ceiling:
                return False, (
                    f"lap time too slow ({profile.best_lap_time_s:.3f}s > "
                    f"{ceiling:.3f}s = 1.15 × {existing_best_lap:.3f}s)"
                )

    return True, "accepted"


# ── MAD outlier filtering ──

def _filter_mad(values: list[float], n_total: int) -> list[float]:
    """MAD-based outlier rejection. Works from N=3.

    Uses modified Z-score with adaptive threshold:
    - N < 10: z > 3.0 (conservative, avoids over-filtering small samples)
    - N >= 10: z > 2.5 (standard, matches learner/envelope.py)

    Fallback: if MAD=0 (all identical) or filtering removes >50%,
    use range check (0.5x-2.0x median) instead.
    """
    if len(values) < 3:
        return list(values)

    arr = np.array(values, dtype=np.float64)
    median = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median)))

    if mad < 1e-9:
        return list(values)  # all identical, no outliers

    scale = mad * 1.4826  # robust sigma estimate
    threshold = 3.0 if n_total < 10 else 2.5

    filtered = [v for v in values if abs(v - median) / scale <= threshold]

    # Safety: if filtering removed >50%, fall back to range check
    if len(filtered) < 0.5 * len(values):
        filtered = [v for v in values if 0.5 * median <= v <= 2.0 * median]

    return filtered if filtered else list(values)  # never return empty


def _aggregate_scalar(
    field_name: str,
    values: list[float],
    n_total: int,
) -> float:
    """Aggregate a scalar field across sessions using the field's strategy."""
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]

    filtered = _filter_mad(values, n_total)

    if field_name in _P75_FIELDS:
        return float(np.percentile(filtered, 75))
    elif field_name in _MAX_FIELDS:
        return float(max(filtered))
    elif field_name in _MIN_FIELDS:
        return float(min(filtered))
    else:
        # Default: median
        return float(np.median(filtered))


# ── Spatial event matching ──

def _merge_spatial_events(
    all_events: list[list[dict]],
    match_tolerance_m: float,
    numeric_fields: list[str],
    min_occurrence_frac: float = 0.3,
) -> list[dict]:
    """Merge spatial events across sessions using greedy nearest-neighbor.

    Args:
        all_events: List of event lists, one per session.
        match_tolerance_m: Max distance to consider same physical feature.
        numeric_fields: Fields to aggregate via MAD-filtered median.
        min_occurrence_frac: Drop events appearing in fewer sessions.

    Returns:
        Merged event list with aggregated numeric fields.
    """
    n_sessions = len(all_events)
    if n_sessions == 0:
        return []
    if n_sessions == 1:
        return list(all_events[0])

    # Reference events: start with first session
    reference: list[dict] = []
    # For each reference event, track all matched numeric values
    matched_values: list[dict[str, list[float]]] = []
    matched_counts: list[int] = []

    for session_events in all_events:
        used_refs = set()
        for event in session_events:
            event_dist = event.get("lap_dist_m", 0.0)
            best_idx = -1
            best_gap = float("inf")
            for i, ref in enumerate(reference):
                if i in used_refs:
                    continue
                gap = abs(ref["lap_dist_m"] - event_dist)
                if gap < match_tolerance_m and gap < best_gap:
                    best_gap = gap
                    best_idx = i
            if best_idx >= 0:
                used_refs.add(best_idx)
                matched_counts[best_idx] += 1
                for nf in numeric_fields:
                    val = event.get(nf)
                    if val is not None and isinstance(val, (int, float)):
                        matched_values[best_idx].setdefault(nf, []).append(float(val))
            else:
                # New reference event
                reference.append(dict(event))
                vals: dict[str, list[float]] = {}
                for nf in numeric_fields:
                    val = event.get(nf)
                    if val is not None and isinstance(val, (int, float)):
                        vals[nf] = [float(val)]
                matched_values.append(vals)
                matched_counts.append(1)

    # Filter by occurrence and aggregate
    result = []
    for i, ref in enumerate(reference):
        frac = matched_counts[i] / n_sessions
        if frac < min_occurrence_frac:
            continue
        merged = dict(ref)
        for nf in numeric_fields:
            vals = matched_values[i].get(nf, [])
            if vals:
                filtered = _filter_mad(vals, n_sessions)
                merged[nf] = float(np.median(filtered))
        result.append(merged)

    # Sort by lap_dist_m
    result.sort(key=lambda e: e.get("lap_dist_m", 0.0))
    return result


# ── Distribution aggregation ──

def _merge_distributions(
    field_name: str,
    all_dists: list[Any],
) -> Any:
    """Merge distribution-type fields across sessions.

    For dict[str, float] (speed_bands, lateral_g, body_roll_deg):
    average the values across sessions for each key.

    For dict[str, dict] (ride_heights_mm, surface_profile, shock_vel_by_sector):
    average the leaf numeric values.

    For list[dict] (elevation_profile): use the longest profile.
    """
    if not all_dists:
        return {}
    if len(all_dists) == 1:
        return all_dists[0]

    # elevation_profile: use the one with most detail
    if field_name == "elevation_profile":
        return max(all_dists, key=lambda x: len(x) if isinstance(x, list) else 0)

    # Dict fields: merge by key
    if not isinstance(all_dists[0], dict):
        return all_dists[0]

    merged: dict[str, Any] = {}
    all_keys: set[str] = set()
    for d in all_dists:
        if isinstance(d, dict):
            all_keys.update(d.keys())

    for key in sorted(all_keys):
        values = []
        for d in all_dists:
            if not isinstance(d, dict):
                continue
            val = d.get(key)
            if val is None:
                continue
            if isinstance(val, (int, float)):
                values.append(float(val))
            elif isinstance(val, dict):
                values.append(val)
        if values and isinstance(values[0], (int, float)):
            merged[key] = float(np.median(values))
        elif values and isinstance(values[0], dict):
            # Recurse for nested dicts
            merged[key] = _merge_distributions(f"{field_name}.{key}", values)
        elif values:
            merged[key] = values[0]

    return merged


# ── TrackProfileStore ──

class TrackProfileStore:
    """Accumulates IBT sessions into a per-(track, car) consensus model.

    Usage::

        store = TrackProfileStore("sebring_international_raceway", "porsche_963")
        store.add_session(profile, session_id="session_2026_04_12")
        consensus = store.consensus()  # -> TrackProfile (standard interface)
    """

    def __init__(
        self,
        track_slug: str,
        car_slug: str,
        base_dir: str | Path = "data/tracks",
    ):
        self._track_slug = track_slug
        self._car_slug = car_slug
        self._base_dir = Path(base_dir)
        self._snapshots: list[SessionSnapshot] = []
        self._load()

    def _store_path(self) -> Path:
        return self._base_dir / f"{self._track_slug}_{self._car_slug}_store.json"

    @property
    def n_sessions(self) -> int:
        return len(self._snapshots)

    @property
    def best_lap_time_s(self) -> float | None:
        if not self._snapshots:
            return None
        return min(s.lap_time_s for s in self._snapshots)

    def add_session(
        self,
        profile: TrackProfile,
        session_id: str | None = None,
    ) -> tuple[bool, str]:
        """Add a session to the store. Returns (accepted, reason).

        Deduplicates by session_id. Applies quality gate before accepting.
        """
        if session_id is None:
            session_id = profile.telemetry_source or datetime.now(timezone.utc).isoformat()

        # Dedup
        existing_ids = {s.session_id for s in self._snapshots}
        if session_id in existing_ids:
            return False, f"duplicate session_id: {session_id}"

        # Quality gate
        accepted, reason = _passes_quality_gate(
            profile,
            self.best_lap_time_s,
            self.n_sessions,
        )
        if not accepted:
            logger.info("Track store rejected session %s: %s", session_id, reason)
            return False, reason

        snapshot = SessionSnapshot.from_profile(profile, session_id)
        self._snapshots.append(snapshot)
        self._save()
        logger.info(
            "Track store accepted session %s (%d total)",
            session_id, self.n_sessions,
        )
        return True, "accepted"

    def consensus(self) -> TrackProfile:
        """Build consensus TrackProfile from accumulated sessions.

        Returns a TrackProfile fully compatible with existing solver interface.
        Single-session: returns that session's values unchanged.
        Multi-session: aggregates per field strategy (P75/median/max/min).
        """
        if not self._snapshots:
            raise ValueError("No sessions in store — cannot build consensus")

        n = len(self._snapshots)

        if n == 1:
            return self._profile_from_snapshot(self._snapshots[0], consensus_n=1)

        # Aggregate scalars
        scalar_fields = _get_scalar_float_fields()
        aggregated_scalars: dict[str, Any] = {}

        # Identity fields: use first session
        for fname in _FIRST_FIELDS:
            for s in self._snapshots:
                val = s.scalars.get(fname)
                if val is not None:
                    aggregated_scalars[fname] = val
                    break
            # Ensure required str fields have a value
            if fname not in aggregated_scalars and fname in ("track_name", "track_config"):
                aggregated_scalars[fname] = ""

        # Numeric scalars: aggregate per strategy
        for fname in scalar_fields:
            if fname in _FIRST_FIELDS:
                continue
            values = []
            for s in self._snapshots:
                val = s.scalars.get(fname)
                if val is not None and isinstance(val, (int, float)):
                    values.append(float(val))
            if values:
                aggregated_scalars[fname] = _aggregate_scalar(fname, values, n)
            else:
                aggregated_scalars[fname] = 0.0

        # Car field
        aggregated_scalars["car"] = self._snapshots[0].car

        # Aggregate distributions
        aggregated_dists: dict[str, Any] = {}
        for fname in _DISTRIBUTION_FIELDS:
            all_dists = []
            for s in self._snapshots:
                val = s.distributions.get(fname)
                if val is not None:
                    all_dists.append(val)
            if all_dists:
                aggregated_dists[fname] = _merge_distributions(fname, all_dists)
            else:
                aggregated_dists[fname] = {} if fname != "elevation_profile" else []

        # Aggregate spatial events
        braking = _merge_spatial_events(
            [s.braking_zones for s in self._snapshots],
            match_tolerance_m=25.0,
            numeric_fields=["entry_speed_kph", "min_speed_kph", "peak_decel_g", "braking_dist_m"],
        )
        corners = _merge_spatial_events(
            [s.corners for s in self._snapshots],
            match_tolerance_m=15.0,
            numeric_fields=["speed_kph", "peak_lat_g", "radius_m"],
        )
        kerbs = _merge_spatial_events(
            [s.kerb_events for s in self._snapshots],
            match_tolerance_m=15.0,
            numeric_fields=["severity"],
        )

        # Build the consensus profile
        return self._build_profile(aggregated_scalars, aggregated_dists, braking, corners, kerbs, n)

    def _profile_from_snapshot(self, snap: SessionSnapshot, consensus_n: int) -> TrackProfile:
        """Reconstruct a TrackProfile from a single snapshot."""
        kwargs: dict[str, Any] = {}
        # Scalars
        for k, v in snap.scalars.items():
            kwargs[k] = v
        # Distributions
        for k, v in snap.distributions.items():
            kwargs[k] = v
        # Spatial events
        kwargs["braking_zones"] = [BrakingZone(**bz) for bz in snap.braking_zones]
        kwargs["corners"] = [Corner(**c) for c in snap.corners]
        kwargs["kerb_events"] = [KerbEvent(**k) for k in snap.kerb_events]
        # Metadata
        kwargs["car"] = snap.car
        kwargs["telemetry_source"] = f"consensus ({consensus_n} session{'s' if consensus_n != 1 else ''})"
        kwargs["consensus_n_sessions"] = consensus_n
        # Filter to known fields; fix None for required str fields
        import dataclasses
        known_fields = {f.name: f for f in dataclasses.fields(TrackProfile)}
        filtered: dict[str, Any] = {}
        for k, v in kwargs.items():
            if k not in known_fields:
                continue
            if v is None and known_fields[k].type == "str":
                v = ""
            filtered[k] = v
        return TrackProfile(**filtered)

    def _build_profile(
        self,
        scalars: dict[str, Any],
        distributions: dict[str, Any],
        braking: list[dict],
        corners: list[dict],
        kerbs: list[dict],
        consensus_n: int,
    ) -> TrackProfile:
        """Construct a TrackProfile from aggregated data."""
        kwargs: dict[str, Any] = {}
        kwargs.update(scalars)
        kwargs.update(distributions)
        kwargs["braking_zones"] = [BrakingZone(**bz) for bz in braking]
        kwargs["corners"] = [Corner(**c) for c in corners]
        kwargs["kerb_events"] = [KerbEvent(**k) for k in kerbs]
        kwargs["telemetry_source"] = f"consensus ({consensus_n} sessions)"
        kwargs["consensus_n_sessions"] = consensus_n
        # Filter to known fields; fix None for required str fields
        import dataclasses
        known_fields = {f.name: f for f in dataclasses.fields(TrackProfile)}
        filtered: dict[str, Any] = {}
        for k, v in kwargs.items():
            if k not in known_fields:
                continue
            if v is None and known_fields[k].type == "str":
                v = ""
            filtered[k] = v
        return TrackProfile(**filtered)

    # ── Persistence ──

    def _load(self) -> None:
        """Load snapshots from disk. Attempts legacy migration if store doesn't exist."""
        path = self._store_path()
        if path.exists():
            try:
                data = json.loads(path.read_text())
                self._snapshots = [
                    SessionSnapshot.from_dict(s) for s in data.get("snapshots", [])
                ]
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning("Corrupt track store %s: %s", path, e)
                self._snapshots = []
        else:
            self._try_legacy_migration()

    def _try_legacy_migration(self) -> None:
        """Bootstrap from existing single-session JSON if car matches."""
        legacy_path = self._base_dir / f"{self._track_slug}.json"
        if not legacy_path.exists():
            return
        try:
            profile = TrackProfile.load(legacy_path)
            if profile.car.lower().replace(" ", "_") == self._car_slug:
                snap = SessionSnapshot.from_profile(profile, session_id="legacy_import")
                self._snapshots = [snap]
                self._save()
                logger.info(
                    "Migrated legacy profile %s into store (%s)",
                    legacy_path, self._store_path(),
                )
        except Exception as e:
            logger.debug("Legacy migration failed for %s: %s", legacy_path, e)

    def _save(self) -> None:
        """Save snapshots to disk with file lock to prevent concurrent corruption."""
        path = self._store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self._to_dict()

        lock_path = path.with_suffix(".lock")
        if _HAS_FCNTL:
            try:
                with open(lock_path, "w") as lf:
                    _fcntl.flock(lf, _fcntl.LOCK_EX)
                    try:
                        # Re-read inside lock to avoid lost updates
                        if path.exists():
                            try:
                                existing = json.loads(path.read_text())
                                existing_ids = {
                                    s["session_id"]
                                    for s in existing.get("snapshots", [])
                                }
                                # Merge any sessions added by other processes
                                for snap in self._snapshots:
                                    if snap.session_id not in existing_ids:
                                        existing.setdefault("snapshots", []).append(
                                            snap.to_dict()
                                        )
                                data = existing
                                data["track_slug"] = self._track_slug
                                data["car_slug"] = self._car_slug
                            except (json.JSONDecodeError, KeyError):
                                pass  # corrupt file, overwrite
                        path.write_text(json.dumps(data, indent=2))
                    finally:
                        _fcntl.flock(lf, _fcntl.LOCK_UN)
                return
            except OSError:
                pass  # fall through to unlocked write
        path.write_text(json.dumps(data, indent=2))

    def _to_dict(self) -> dict:
        return {
            "track_slug": self._track_slug,
            "car_slug": self._car_slug,
            "n_sessions": len(self._snapshots),
            "snapshots": [s.to_dict() for s in self._snapshots],
        }
