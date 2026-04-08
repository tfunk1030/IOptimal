"""TrackProfile dataclass and extraction logic.

Extracts track characteristics from parsed IBT telemetry:
- Surface frequency spectrum (shock velocity histograms)
- Braking zone locations, entry speeds, deceleration demands
- Corner speeds, lateral g demands, radius estimates
- Speed profile (% of lap in speed bands)
- Kerb locations and severity
- Elevation changes
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np


@dataclass
class BrakingZone:
    """A single braking event on the track."""
    lap_dist_m: float           # Distance from S/F at brake application
    entry_speed_kph: float      # Speed when braking starts
    min_speed_kph: float        # Speed at corner apex (end of braking)
    peak_decel_g: float         # Peak longitudinal deceleration (positive = braking)
    braking_dist_m: float       # Distance from brake point to apex


@dataclass
class Corner:
    """A corner on the track."""
    lap_dist_m: float           # Apex distance from S/F
    speed_kph: float            # Apex speed
    peak_lat_g: float           # Peak lateral g through corner
    radius_m: float             # Estimated radius from v^2 / (lat_g * 9.81)
    direction: str              # "left" or "right"


@dataclass
class KerbEvent:
    """A kerb strike event."""
    lap_dist_m: float           # Distance from S/F
    severity: float             # Peak vertical acceleration spike (g)
    side: str                   # "left", "right", or "both"


def build_kerb_spatial_mask(
    lap_dist: np.ndarray,
    kerb_events: list[KerbEvent],
    buffer_m: float = 30.0,
) -> np.ndarray:
    """Boolean mask where True = sample is within a kerb zone.

    Uses KerbEvent lap_dist_m positions with a spatial buffer to mark
    samples near known kerb locations. Useful for filtering telemetry
    data in extract.py and segment.py which work with lap_dist arrays.

    Args:
        lap_dist: Per-sample lap distance array (m).
        kerb_events: List of detected kerb events with lap_dist_m.
        buffer_m: Spatial buffer around each kerb event center (m).

    Returns:
        Boolean array, same length as lap_dist.
    """
    mask = np.zeros(len(lap_dist), dtype=bool)
    for event in kerb_events:
        mask |= np.abs(lap_dist - event.lap_dist_m) <= buffer_m
    return mask


@dataclass
class TrackProfile:
    """Complete track demand profile extracted from telemetry."""

    # Identity
    track_name: str
    track_config: str
    track_length_m: float
    car: str
    best_lap_time_s: float

    # Speed profile
    speed_bands_kph: dict[str, float] = field(default_factory=dict)
    median_speed_kph: float = 0.0
    max_speed_kph: float = 0.0
    min_speed_kph: float = 0.0
    # Speed band fractions — used by multi-speed solver and explorer scoring
    pct_above_200kph: float = 0.0   # fraction of lap time above 200 kph
    pct_below_120kph: float = 0.0   # fraction of lap time below 120 kph

    # G-force envelope
    peak_lat_g: float = 0.0
    peak_braking_g: float = 0.0
    peak_accel_g: float = 0.0
    peak_vertical_g: float = 0.0

    # Braking zones
    braking_zones: list[BrakingZone] = field(default_factory=list)

    # Corners
    corners: list[Corner] = field(default_factory=list)

    # Surface frequency spectrum
    shock_vel_histogram_front: dict[str, int] = field(default_factory=dict)
    shock_vel_histogram_rear: dict[str, int] = field(default_factory=dict)
    shock_vel_by_sector: dict[str, dict] = field(default_factory=dict)
    shock_vel_p50_front_mps: float = 0.0
    shock_vel_p95_front_mps: float = 0.0
    shock_vel_p99_front_mps: float = 0.0
    shock_vel_p50_rear_mps: float = 0.0
    shock_vel_p95_rear_mps: float = 0.0
    shock_vel_p99_rear_mps: float = 0.0

    # Clean-track shock velocity (kerb strikes excluded)
    shock_vel_p50_front_clean_mps: float = 0.0
    shock_vel_p95_front_clean_mps: float = 0.0
    shock_vel_p99_front_clean_mps: float = 0.0
    shock_vel_p50_rear_clean_mps: float = 0.0
    shock_vel_p95_rear_clean_mps: float = 0.0
    shock_vel_p99_rear_clean_mps: float = 0.0

    # High-speed-only shock velocity (>200 kph) — for aero platform sizing.
    # At high speed, aero compression dominates and these values characterize
    # platform instability without low-speed bump contamination.
    shock_vel_p99_front_hs_mps: float = 0.0
    shock_vel_p99_rear_hs_mps: float = 0.0

    # Kerb-only shock velocity (for HS damper tuning)
    shock_vel_p95_front_kerb_mps: float = 0.0
    shock_vel_p99_front_kerb_mps: float = 0.0
    shock_vel_p95_rear_kerb_mps: float = 0.0
    shock_vel_p99_rear_kerb_mps: float = 0.0

    # Kerb filtering metadata
    kerb_sample_pct: float = 0.0  # % of lap samples on kerbs

    # Kerb events
    kerb_events: list[KerbEvent] = field(default_factory=list)

    # Elevation profile (sampled)
    elevation_profile: list[dict] = field(default_factory=list)
    elevation_change_m: float = 0.0

    # Lateral G distribution (extracted from IBT)
    lateral_g: dict[str, float] = field(default_factory=dict)
    # e.g. {"mean_abs": 0.94, "p90": 1.83, "p95": 2.02, "p99": 2.43, "max": 4.53}

    # Body roll distribution (from IMU Roll channel, degrees)
    body_roll_deg: dict[str, float] = field(default_factory=dict)
    # e.g. {"mean_abs": 0.72, "p95": 1.67, "max": 3.88}

    # Ride height statistics (mm)
    ride_heights_mm: dict[str, dict] = field(default_factory=dict)

    # Roll gradient: measured body roll per g of lateral acceleration (deg/g)
    # Derived from linear fit of |Roll| vs |LatAccel| at 1-2g cornering range
    roll_gradient_deg_per_g: float = 0.0

    # Measured LLTD from ride height deflection ratio in corners
    lltd_measured: float = 0.0

    # Surface profile (detailed shock velocity breakdown)
    surface_profile: dict = field(default_factory=dict)

    # Center front splitter ride height at speed (mm)
    splitter_rh_mean_mm: float = 0.0
    splitter_rh_min_mm: float = 0.0

    # Environmental conditions
    air_temp_c: float = 0.0
    track_temp_c: float = 0.0
    air_density_kg_m3: float = 0.0

    # Telemetry source description
    telemetry_source: str = ""

    @property
    def aero_reference_speed_kph(self) -> float:
        """V²-RMS speed for aero compression sizing.

        Aero downforce (and thus ride-height compression) scales with V². The
        relevant operating-point speed for ride-height targeting is therefore
        sqrt(<V²>) over the speed range where aero is meaningful — NOT the
        lap median. Below ~100 kph aero compression is essentially zero, so
        slow-corner samples shouldn't dilute the aero reference.

        Validated against Porsche/Algarve IBT-measured compression on
        2026-04-07: static→dynamic compression at brake-off >150 kph samples
        gave F=13.4mm R=16.4mm; this property gives 199.6 kph for Algarve,
        at which the aero compression model returns 12.2/16.5mm — within 1mm
        of measured for both axles. Median speed (174.5) gave 9.3/12.6mm
        (4mm under-prediction). V²-RMS over the full lap (187) gave 10.7/14.5
        — better than median but still under-predicts.

        Falls back to median_speed_kph when speed_bands_kph is unavailable.
        """
        if not self.speed_bands_kph:
            return self.median_speed_kph
        AERO_MIN_KPH = 100.0
        total_frac = 0.0
        v2_sum = 0.0
        for label, pct in self.speed_bands_kph.items():
            try:
                lo, hi = label.split("-")
                lo_f = float(lo); hi_f = float(hi)
            except ValueError:
                continue
            if lo_f < AERO_MIN_KPH:
                continue
            v_mid = (lo_f + hi_f) / 2.0
            frac = pct / 100.0
            v2_sum += frac * v_mid * v_mid
            total_frac += frac
        if total_frac <= 0.0:
            return self.median_speed_kph
        import math as _m
        return _m.sqrt(v2_sum / total_frac)

    def pct_time_above_kph(self, threshold_kph: float) -> float:
        """Fraction of lap time spent above *threshold_kph*.

        Derived from the ``speed_bands_kph`` histogram (20-kph bins, values
        in percent).  Returns 0.0 when no speed band data is available.
        """
        if not self.speed_bands_kph:
            return 0.0
        total = 0.0
        for label, pct in self.speed_bands_kph.items():
            lo = float(label.split("-")[0])
            if lo >= threshold_kph:
                total += pct
        return total / 100.0  # convert percent → fraction

    def pct_time_below_kph(self, threshold_kph: float) -> float:
        """Fraction of lap time spent below *threshold_kph*."""
        if not self.speed_bands_kph:
            return 0.0
        total = 0.0
        for label, pct in self.speed_bands_kph.items():
            hi = float(label.split("-")[1])
            if hi <= threshold_kph:
                total += pct
        return total / 100.0

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        d = asdict(self)
        # Convert BrakingZone/Corner/KerbEvent lists to plain dicts
        d["braking_zones"] = [asdict(bz) for bz in self.braking_zones]
        d["corners"] = [asdict(c) for c in self.corners]
        d["kerb_events"] = [asdict(k) for k in self.kerb_events]
        return d

    def save(self, path: str | Path) -> None:
        """Save profile as JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @staticmethod
    def load(path: str | Path) -> "TrackProfile":
        """Load profile from JSON.

        Gracefully handles unknown fields in the JSON (ignores them if
        they don't match a dataclass field, preserving forward compatibility).
        """
        data = json.loads(Path(path).read_text())
        # Reconstruct nested dataclasses
        data["braking_zones"] = [BrakingZone(**bz) for bz in data.get("braking_zones", [])]
        data["corners"] = [Corner(**c) for c in data.get("corners", [])]
        data["kerb_events"] = [KerbEvent(**k) for k in data.get("kerb_events", [])]
        # Filter to only known fields to handle forward/backward compatibility
        import dataclasses
        known_fields = {f.name for f in dataclasses.fields(TrackProfile)}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return TrackProfile(**filtered)

    def summary(self) -> str:
        """Human-readable summary."""
        mins = int(self.best_lap_time_s) // 60
        secs = self.best_lap_time_s - mins * 60
        lines = [
            f"Track: {self.track_name} — {self.track_config}",
            f"Car: {self.car}",
            f"Best lap: {mins}:{secs:06.3f}",
            f"Track length: {self.track_length_m:.0f} m",
            f"",
            f"Speed: {self.min_speed_kph:.0f}–{self.max_speed_kph:.0f} kph "
            f"(median {self.median_speed_kph:.0f} kph)",
            f"Peak lat: {self.peak_lat_g:.2f} g",
            f"Peak braking: {self.peak_braking_g:.2f} g",
            f"Peak accel: {self.peak_accel_g:.2f} g",
            f"",
            f"Braking zones: {len(self.braking_zones)}",
            f"Corners: {len(self.corners)}",
            f"Kerb events: {len(self.kerb_events)}",
            f"Elevation change: {self.elevation_change_m:.1f} m",
            f"",
            f"Shock velocity (front): p50={self.shock_vel_p50_front_mps*1000:.1f} mm/s, "
            f"p95={self.shock_vel_p95_front_mps*1000:.1f} mm/s, "
            f"p99={self.shock_vel_p99_front_mps*1000:.1f} mm/s",
            f"Shock velocity (rear):  p50={self.shock_vel_p50_rear_mps*1000:.1f} mm/s, "
            f"p95={self.shock_vel_p95_rear_mps*1000:.1f} mm/s, "
            f"p99={self.shock_vel_p99_rear_mps*1000:.1f} mm/s",
        ]
        if self.shock_vel_p99_front_clean_mps > 0:
            lines.extend([
                f"Shock vel clean (front): p99={self.shock_vel_p99_front_clean_mps*1000:.1f} mm/s",
                f"Shock vel clean (rear):  p99={self.shock_vel_p99_rear_clean_mps*1000:.1f} mm/s",
                f"Kerb samples: {self.kerb_sample_pct:.1f}%",
            ])
        return "\n".join(lines)
