"""Server-side aggregator — rebuilds empirical models from all team observations.

This runs on the team server (not locally) and is triggered after new
observations arrive.  It:
1. Loads all team observations for a car/track pair
2. Runs delta detection between consecutive sessions
3. Fits empirical models using the existing learner algorithms
4. Updates support tier based on observation count and model stability
5. Stores results in the database
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Support tier thresholds
_TIER_THRESHOLDS = {
    "exploratory": 5,
    "partial": 15,
    "calibrated": 30,
}


def compute_support_tier(observation_count: int, model_stability: float | None = None) -> str:
    """Determine support tier based on observation count and model stability.

    Parameters
    ----------
    observation_count:
        Number of observations for this car/track pair.
    model_stability:
        Optional metric (0-1) measuring how stable the empirical models are
        across holdout splits.  Only used for promoting to 'calibrated'.
    """
    if observation_count >= _TIER_THRESHOLDS["calibrated"]:
        if model_stability is not None and model_stability >= 0.6:
            return "calibrated"
        return "partial"  # Many observations but unstable models
    elif observation_count >= _TIER_THRESHOLDS["partial"]:
        return "partial"
    elif observation_count >= _TIER_THRESHOLDS["exploratory"]:
        return "exploratory"
    return "unsupported"


def aggregate_observations(
    observations: list[dict],
    car: str,
    track: str,
) -> dict:
    """Fit empirical models from a list of observation dicts.

    Uses the existing learner algorithms (imported lazily to avoid
    pulling in numpy/scipy on the server unless needed).

    Parameters
    ----------
    observations:
        List of observation dicts (from ``Observation.to_dict()``).
    car:
        Canonical car name (e.g., "bmw").
    track:
        Track name (e.g., "Sebring International Raceway").

    Returns
    -------
    dict with keys:
        - model: fitted EmpiricalModelSet as dict
        - support_tier: computed tier string
        - observation_count: number of observations used
        - corrections: dict of empirical corrections
    """
    if not observations:
        return {
            "model": {},
            "support_tier": "unsupported",
            "observation_count": 0,
            "corrections": {},
        }

    # Import learner modules lazily (they pull in numpy/scipy)
    from learner.empirical_models import fit_models
    from learner.delta_detector import detect_delta
    from learner.observation import Observation

    # Sort observations by timestamp / session_id
    sorted_obs = sorted(observations, key=lambda o: o.get("session_id", ""))

    # Detect deltas between consecutive sessions
    deltas = []
    for i in range(1, len(sorted_obs)):
        try:
            obs_before = Observation.from_dict(sorted_obs[i - 1])
            obs_after = Observation.from_dict(sorted_obs[i])
            delta = detect_delta(obs_before, obs_after)
            deltas.append(delta.to_dict())
        except Exception as e:
            logger.warning("Delta detection failed between sessions %d and %d: %s", i - 1, i, e)

    # Fit empirical models
    track_key = track.lower().split()[0]
    try:
        models = fit_models(sorted_obs, deltas, car, track)
        model_dict = models.to_dict()
        corrections = models.corrections if models.corrections else {}
    except Exception as e:
        logger.error("Model fitting failed for %s/%s: %s", car, track, e)
        model_dict = {}
        corrections = {}

    support_tier = compute_support_tier(len(observations))

    return {
        "model": model_dict,
        "support_tier": support_tier,
        "observation_count": len(observations),
        "corrections": corrections,
    }


def aggregate_global_car_model(
    all_observations: list[dict],
    car: str,
) -> dict:
    """Build a cross-track global model for a car from all observations.

    Groups observations by track and fits a GlobalCarModel.

    Parameters
    ----------
    all_observations:
        All observations for this car across all tracks.
    car:
        Canonical car name.

    Returns
    -------
    dict with global model data.
    """
    if not all_observations:
        return {
            "car": car,
            "total_sessions": 0,
            "tracks_included": [],
            "confidence": "no_data",
        }

    # Group by track
    by_track: dict[str, list] = {}
    for obs in all_observations:
        track = obs.get("track", "unknown")
        by_track.setdefault(track, []).append(obs)

    return {
        "car": car,
        "total_sessions": len(all_observations),
        "tracks_included": list(by_track.keys()),
        "track_session_counts": {t: len(obs_list) for t, obs_list in by_track.items()},
        "confidence": "low" if len(all_observations) < 10 else "medium" if len(all_observations) < 30 else "high",
    }
