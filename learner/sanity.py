"""Shared lap-sanity helpers for the learning pipeline."""

from __future__ import annotations

import statistics
from typing import Iterable


_TRACK_LAP_BOUNDS: dict[tuple[str, str], tuple[float, float]] = {
    ("bmw", "sebring"): (105.0, 130.0),
    ("acura", "hockenheim"): (82.0, 105.0),
}


def plausible_lap_bounds(car: str, track: str) -> tuple[float, float]:
    """Track-specific plausible lap-time bounds in seconds."""
    car_key = car.lower().strip()
    track_key = track.lower()
    for (car_name, track_fragment), bounds in _TRACK_LAP_BOUNDS.items():
        if car_name == car_key and track_fragment in track_key:
            return bounds
    return (60.0, 600.0)


def is_plausible_lap_time(lap_time_s: float | int | None, car: str, track: str) -> bool:
    """True when a lap time is plausible for this car/track."""
    if lap_time_s is None:
        return False
    try:
        lap = float(lap_time_s)
    except (TypeError, ValueError):
        return False
    lo, hi = plausible_lap_bounds(car, track)
    return lo <= lap <= hi


def filter_plausible_lap_times(
    lap_times: Iterable[float],
    *,
    car: str,
    track: str,
) -> list[float]:
    """Return only the plausible lap times for this car/track."""
    return [float(lt) for lt in lap_times if is_plausible_lap_time(lt, car, track)]


def select_valid_lap(
    ibt,
    *,
    car: str,
    track: str,
    lap: int | None = None,
    min_time: float = 60.0,
    outlier_pct: float | None = 0.115,
) -> tuple[int, int, int, float]:
    """Select a valid lap using the same rules as best_lap_indices plus sanity bounds.

    Returns:
        (lap_number, start_idx, end_idx, lap_time_s)
    """
    valid = ibt.lap_times(min_time=min_time)
    if not valid:
        raise ValueError("No laps pass the minimum lap-time filter")

    if outlier_pct is not None and len(valid) >= 2:
        med = statistics.median(lt for _, lt, _, _ in valid)
        ceiling = med * (1.0 + outlier_pct)
        valid = [(ln, lt, s, e) for ln, lt, s, e in valid if lt <= ceiling]

    bounded = [(ln, lt, s, e) for ln, lt, s, e in valid if is_plausible_lap_time(lt, car, track)]
    if not bounded:
        raise ValueError("No laps remain after applying sanity bounds")
    valid = bounded

    if lap is not None:
        for ln, lt, s, e in valid:
            if ln == lap:
                return ln, s, e, lt
        raise ValueError(f"Lap {lap} failed validity or sanity checks")

    ln, lt, s, e = min(valid, key=lambda item: item[1])
    return ln, s, e, lt


def select_all_valid_laps(
    ibt,
    *,
    car: str,
    track: str,
    min_time: float = 60.0,
    outlier_pct: float | None = 0.115,
) -> list[tuple[int, int, int, float]]:
    """Return all valid laps sorted by lap time (fastest first).

    Same filtering as select_valid_lap but returns every qualifying lap
    instead of just the best one.

    Returns:
        List of (lap_number, start_idx, end_idx, lap_time_s)
    """
    valid = ibt.lap_times(min_time=min_time)
    if not valid:
        return []

    if outlier_pct is not None and len(valid) >= 2:
        med = statistics.median(lt for _, lt, _, _ in valid)
        ceiling = med * (1.0 + outlier_pct)
        valid = [(ln, lt, s, e) for ln, lt, s, e in valid if lt <= ceiling]

    bounded = [(ln, lt, s, e) for ln, lt, s, e in valid if is_plausible_lap_time(lt, car, track)]
    return sorted(bounded, key=lambda item: item[1])
