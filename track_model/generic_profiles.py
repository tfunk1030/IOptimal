"""Generic Track Profile Generator.

Creates approximate TrackProfile objects for tracks without IBT telemetry data.
Uses track metadata (length, corner count, roughness) to estimate the key
parameters the solver needs: shock velocities, speed bands, median speeds.

This enables the solver to work on ANY track, even without a single IBT file.
Accuracy is lower than IBT-derived profiles but provides a reasonable starting
point that the learner can refine over time.

Usage:
    from track_model.generic_profiles import generate_generic_profile

    track = generate_generic_profile(
        name="Daytona International Speedway",
        config="Road Course",
        length_km=5.73,
        n_corners=12,
        roughness="smooth",  # smooth | medium | rough
        avg_speed_kph=200,    # optional: override
    )
"""

from __future__ import annotations

from dataclasses import replace

from track_model.profile import TrackProfile


# Base shock velocity spectra by roughness category (m/s)
# Calibrated from Sebring (rough) and scaled for smooth/medium
_SHOCK_VEL_TEMPLATES = {
    "smooth": {
        "p50_front": 0.015,
        "p95_front": 0.080,
        "p99_front": 0.140,
        "p50_rear": 0.020,
        "p95_rear": 0.100,
        "p99_rear": 0.180,
    },
    "medium": {
        "p50_front": 0.030,
        "p95_front": 0.140,
        "p99_front": 0.220,
        "p50_rear": 0.035,
        "p95_rear": 0.170,
        "p99_rear": 0.270,
    },
    "rough": {
        # Sebring-calibrated baseline
        "p50_front": 0.045,
        "p95_front": 0.190,
        "p99_front": 0.300,
        "p50_rear": 0.055,
        "p95_rear": 0.230,
        "p99_rear": 0.360,
    },
}

# Track classification database (name → roughness, corner style)
_KNOWN_TRACKS = {
    "daytona": ("smooth", "high_speed"),
    "le mans": ("smooth", "high_speed"),
    "monza": ("smooth", "high_speed"),
    "spa": ("medium", "mixed"),
    "sebring": ("rough", "mixed"),
    "road america": ("medium", "high_speed"),
    "laguna seca": ("medium", "technical"),
    "barber": ("smooth", "technical"),
    "lime rock": ("medium", "technical"),
    "watkins glen": ("medium", "mixed"),
    "cota": ("smooth", "mixed"),
    "nurburgring": ("medium", "mixed"),
    "suzuka": ("medium", "mixed"),
    "bathurst": ("rough", "mixed"),
    "brands hatch": ("medium", "technical"),
    "silverstone": ("smooth", "high_speed"),
    "imola": ("medium", "mixed"),
    "portimao": ("medium", "mixed"),
    "interlagos": ("rough", "mixed"),
    "fuji": ("smooth", "high_speed"),
}


def _estimate_speed_profile(
    n_corners: int,
    length_km: float,
    track_style: str,
    avg_speed_kph: float | None = None,
) -> dict:
    """Estimate speed band distribution from track characteristics."""
    # Corners per km gives us a track density metric
    corner_density = n_corners / max(length_km, 0.1)

    if avg_speed_kph is not None:
        median_speed = avg_speed_kph
    elif track_style == "high_speed":
        median_speed = 220 - corner_density * 10
    elif track_style == "technical":
        median_speed = 160 - corner_density * 5
    else:  # mixed
        median_speed = 190 - corner_density * 8

    median_speed = max(120, min(260, median_speed))

    # Estimate time in speed bands
    if track_style == "high_speed":
        pct_above_200 = max(0.1, min(0.6, 0.4 - corner_density * 0.03))
        pct_below_120 = max(0.05, corner_density * 0.02)
    elif track_style == "technical":
        pct_above_200 = max(0.02, 0.15 - corner_density * 0.02)
        pct_below_120 = max(0.10, corner_density * 0.05)
    else:
        pct_above_200 = max(0.05, 0.30 - corner_density * 0.025)
        pct_below_120 = max(0.08, corner_density * 0.03)

    return {
        "median_speed_kph": median_speed,
        "pct_above_200kph": pct_above_200,
        "pct_below_120kph": pct_below_120,
    }


def _estimate_lap_time(length_km: float, median_speed_kph: float) -> float:
    """Rough lap time estimate from length and average speed."""
    return (length_km / median_speed_kph) * 3600


def generate_generic_profile(
    name: str,
    config: str = "",
    length_km: float = 5.0,
    n_corners: int = 15,
    roughness: str = "medium",
    avg_speed_kph: float | None = None,
    car: str = "generic",
) -> TrackProfile:
    """Generate a TrackProfile from basic track characteristics.

    Args:
        name: Track name (e.g., "Daytona International Speedway")
        config: Track configuration (e.g., "Road Course")
        length_km: Track length in kilometers
        n_corners: Number of corners
        roughness: Surface roughness ("smooth", "medium", "rough")
        avg_speed_kph: Optional average speed override
        car: Car identifier (e.g., "bmw", "ferrari")
    """
    # Look up known track data
    name_lower = name.lower()
    known = None
    for key, data in _KNOWN_TRACKS.items():
        if key in name_lower:
            known = data
            break

    if known is not None:
        roughness = known[0]
        track_style = known[1]
    else:
        track_style = "mixed"

    # Get shock velocity template
    sv = _SHOCK_VEL_TEMPLATES.get(roughness, _SHOCK_VEL_TEMPLATES["medium"])

    # Estimate speed profile
    speed_info = _estimate_speed_profile(n_corners, length_km, track_style, avg_speed_kph)
    median_speed = speed_info["median_speed_kph"]
    lap_time = _estimate_lap_time(length_km, median_speed)

    return TrackProfile(
        track_name=name,
        track_config=config or "Generic",
        track_length_m=length_km * 1000,
        car=car,
        best_lap_time_s=lap_time,
        median_speed_kph=median_speed,
        max_speed_kph=median_speed * 1.4,
        # Shock velocities
        shock_vel_p50_front_mps=sv["p50_front"],
        shock_vel_p95_front_mps=sv["p95_front"],
        shock_vel_p99_front_mps=sv["p99_front"],
        shock_vel_p50_rear_mps=sv["p50_rear"],
        shock_vel_p95_rear_mps=sv["p95_rear"],
        shock_vel_p99_rear_mps=sv["p99_rear"],
        # Clean track ≈ same as overall for generic (no kerb data)
        shock_vel_p50_front_clean_mps=sv["p50_front"],
        shock_vel_p95_front_clean_mps=sv["p95_front"],
        shock_vel_p99_front_clean_mps=sv["p99_front"],
        shock_vel_p50_rear_clean_mps=sv["p50_rear"],
        shock_vel_p95_rear_clean_mps=sv["p95_rear"],
        shock_vel_p99_rear_clean_mps=sv["p99_rear"],
    )
