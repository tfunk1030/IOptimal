"""Session context normalization for fair comparison.

Describes the conditions under which telemetry was collected so that
comparisons between sessions account for fuel load, tyre state,
weather, and traffic. A session with low thermal_validity or
pace_validity should not be used to override a high-confidence baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SessionContext:
    """Normalized interpretation of session conditions."""

    fuel_l: float | None
    tyre_state: str  # cold | warming | in_window | overheated | unknown
    thermal_validity: float  # 0-1: how much to trust thermal data
    pace_validity: float  # 0-1: how representative the pace is
    traffic_confidence: float  # 0-1: confidence lap was clean
    weather_confidence: float  # 0-1: confidence weather was stable
    comparable_to_baseline: bool
    notes: list[str] = field(default_factory=list)

    @property
    def overall_confidence(self) -> float:
        """Combined confidence that this session is trustworthy for analysis."""
        return (
            self.thermal_validity * 0.30
            + self.pace_validity * 0.30
            + self.traffic_confidence * 0.20
            + self.weather_confidence * 0.20
        )


def build_session_context(
    fuel_l: float | None,
    front_carcass_c: float,
    rear_carcass_c: float,
    front_pressure_kpa: float,
    rear_pressure_kpa: float,
    air_temp_c: float,
    track_temp_c: float,
    wind_speed_ms: float,
    lap_time_s: float,
    best_theoretical_s: float | None = None,
) -> SessionContext:
    """Build a SessionContext from extracted telemetry values.

    Args:
        fuel_l: Fuel level during the analyzed lap.
        front_carcass_c / rear_carcass_c: Operating carcass temps.
        front_pressure_kpa / rear_pressure_kpa: Hot pressures.
        air_temp_c / track_temp_c: Ambient conditions.
        wind_speed_ms: Wind speed.
        lap_time_s: Lap time of the analyzed lap.
        best_theoretical_s: Optional best-known lap time for pace validity.
    """
    notes: list[str] = []

    # --- Tyre state classification ---
    avg_carcass = (front_carcass_c + rear_carcass_c) / 2.0 if (front_carcass_c > 0 and rear_carcass_c > 0) else 0.0
    if avg_carcass <= 0:
        tyre_state = "unknown"
        thermal_validity = 0.3
        notes.append("No carcass temperature data")
    elif avg_carcass < 50:
        tyre_state = "cold"
        thermal_validity = 0.4
        notes.append(f"Tyres cold ({avg_carcass:.0f} C)")
    elif avg_carcass < 70:
        tyre_state = "warming"
        thermal_validity = 0.7
    elif avg_carcass < 110:
        tyre_state = "in_window"
        thermal_validity = 1.0
    else:
        tyre_state = "overheated"
        thermal_validity = 0.5
        notes.append(f"Tyres overheated ({avg_carcass:.0f} C)")

    # --- Pace validity ---
    pace_validity = 0.8  # Default: assume reasonable
    if best_theoretical_s is not None and best_theoretical_s > 0 and lap_time_s > 0:
        delta_pct = (lap_time_s - best_theoretical_s) / best_theoretical_s * 100
        if delta_pct < 1.0:
            pace_validity = 1.0
        elif delta_pct < 3.0:
            pace_validity = 0.8
        elif delta_pct < 5.0:
            pace_validity = 0.5
            notes.append(f"Lap {delta_pct:.1f}% off theoretical best")
        else:
            pace_validity = 0.3
            notes.append(f"Lap {delta_pct:.1f}% off theoretical best — low pace validity")

    # --- Weather confidence ---
    weather_confidence = 1.0
    if wind_speed_ms > 5.0:
        weather_confidence -= 0.2
        notes.append(f"High wind ({wind_speed_ms:.1f} m/s)")
    if track_temp_c > 45:
        weather_confidence -= 0.1
        notes.append(f"Hot track ({track_temp_c:.0f} C)")
    weather_confidence = max(0.0, weather_confidence)

    # --- Traffic confidence (placeholder — needs sector data) ---
    traffic_confidence = 0.85

    # --- Comparable to baseline ---
    comparable = thermal_validity >= 0.6 and pace_validity >= 0.5

    return SessionContext(
        fuel_l=fuel_l,
        tyre_state=tyre_state,
        thermal_validity=round(thermal_validity, 3),
        pace_validity=round(pace_validity, 3),
        traffic_confidence=round(traffic_confidence, 3),
        weather_confidence=round(weather_confidence, 3),
        comparable_to_baseline=comparable,
        notes=notes,
    )
