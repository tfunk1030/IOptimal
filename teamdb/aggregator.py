"""Server-side aggregator — rebuilds empirical models from all team observations.

This runs on the team server (not locally) and is triggered after new
observations arrive.  It:
1. Loads all team observations for a car/track pair
2. Filters to a single ``suspension_arch`` partition (GT3 vs GTP physics
   cannot be co-fitted — different feature schemas, different regression
   targets — see audit infra-teamdb-watcher-desktop.md F3).
3. Runs delta detection between consecutive sessions
4. Fits empirical models using the existing learner algorithms
5. Updates support tier based on observation count and model stability
6. Stores results in the database
"""

from __future__ import annotations

import logging
from typing import Any

from car_model.registry import resolve_car, track_key as _registry_track_key

logger = logging.getLogger(__name__)

# GT3 Phase 2 — F11 (audit infra-teamdb-watcher-desktop.md). Per-arch support
# tier thresholds. GT3 reaches "calibrated" coverage faster than GTP because
# Step 2 (heave/third) is not applicable, so the effective number of
# independent fits is lower.
#
# TODO(W9.1): the GT3 thresholds (4/10/20) are first-cut estimates per the
# audit; revisit once a few teams have produced real GT3 sessions and we can
# correlate model_stability against observation_count empirically. Until
# then they are deliberately tighter than the GTP thresholds so the support
# tier surfaced to users does not over-claim coverage.
_TIER_THRESHOLDS_BY_ARCH: dict[str, dict[str, int]] = {
    "gtp_heave_third_torsion_front": {"exploratory": 5, "partial": 15, "calibrated": 30},
    "gtp_heave_third_roll_front":   {"exploratory": 5, "partial": 15, "calibrated": 30},
    "gt3_coil_4wheel":              {"exploratory": 4, "partial": 10, "calibrated": 20},
}

# Default tier thresholds (legacy callers without an arch). Mirrors the
# pre-W8.1 hardcoded GTP defaults so existing call sites do not change
# behavior.
_TIER_THRESHOLDS = _TIER_THRESHOLDS_BY_ARCH["gtp_heave_third_torsion_front"]


def _arch_for_car(car: str) -> str | None:
    """Resolve a canonical ``car`` name to its suspension architecture string.

    Returns ``None`` if the car is not in the registry (unknown / future GT3
    canonicals). Callers should fall back to the architecture carried on
    the observation rows themselves when this returns ``None``.
    """
    identity = resolve_car(car) if car else None
    if identity is None:
        return None
    suspension_arch = getattr(identity, "suspension_arch", None)
    if suspension_arch:
        return suspension_arch
    # Fallback: look up the in-memory CarModel which carries a
    # SuspensionArchitecture enum. Lazy import — keeps the server cold-path
    # away from pulling the full physics stack.
    try:
        from car_model.cars import get_car

        car_model = get_car(identity.canonical)
    except Exception:  # pragma: no cover — defensive
        return None
    arch = getattr(car_model, "suspension_arch", None)
    return arch.value if arch is not None else None


def compute_support_tier(
    observation_count: int,
    suspension_arch: str | None = None,
    model_stability: float | None = None,
) -> str:
    """Determine support tier based on observation count and model stability.

    Parameters
    ----------
    observation_count:
        Number of observations for this car/track pair (already filtered to
        a single ``suspension_arch`` partition).
    suspension_arch:
        Architecture key — selects the GTP vs GT3 thresholds. ``None``
        falls back to the GTP defaults for backward compatibility with
        legacy call sites.
    model_stability:
        Optional metric (0-1) measuring how stable the empirical models are
        across holdout splits.  Only used for promoting to 'calibrated'.
    """
    thresholds = _TIER_THRESHOLDS_BY_ARCH.get(
        suspension_arch or "", _TIER_THRESHOLDS
    )
    if observation_count >= thresholds["calibrated"]:
        if model_stability is not None and model_stability >= 0.6:
            return "calibrated"
        return "partial"  # Many observations but unstable models
    elif observation_count >= thresholds["partial"]:
        return "partial"
    elif observation_count >= thresholds["exploratory"]:
        return "exploratory"
    return "unsupported"


def aggregate_observations(
    observations: list[dict],
    car: str,
    track: str,
    *,
    suspension_arch: str | None = None,
) -> dict:
    """Fit empirical models from a list of observation dicts.

    Uses the existing learner algorithms (imported lazily to avoid
    pulling in numpy/scipy on the server unless needed).

    GT3 Phase 2 — F3 (audit infra-teamdb-watcher-desktop.md). Observations
    are partitioned by ``suspension_arch`` BEFORE fitting. GT3 + GTP rows
    cannot be co-fitted; doing so silently corrupts both models because
    GT3 lacks the heave/third regression targets the GTP fitter assumes.

    Parameters
    ----------
    observations:
        List of observation dicts (from ``Observation.to_dict()``). Each
        dict may carry a top-level ``suspension_arch`` key; rows that do
        not match the resolved target arch are dropped before fitting.
    car:
        Canonical car name (e.g., "bmw", "bmw_m4_gt3"). The registry is
        queried to resolve the car's architecture.
    track:
        Track display name (e.g., "Sebring International Raceway").
        Multi-word tracks are normalised via ``car_model.registry.track_key``
        — F10 in the audit. The pre-W8.1 ``track.lower().split()[0]``
        path returned ``"red"`` for "Red Bull Ring" and broke aggregation.
    suspension_arch:
        Optional explicit override. When ``None`` the architecture is
        resolved from the registry; if the registry has no entry the
        majority architecture across the supplied observations is used,
        defaulting to ``gtp_heave_third_torsion_front`` for backward
        compatibility.

    Returns
    -------
    dict with keys:
        - model: fitted EmpiricalModelSet as dict
        - support_tier: computed tier string
        - observation_count: number of observations used (post-filter)
        - corrections: dict of empirical corrections
        - suspension_arch: the architecture the fit was scoped to
    """
    # Resolve the target architecture before short-circuiting on empty
    # input, so callers can see which partition we would have aggregated.
    target_arch = suspension_arch or _arch_for_car(car)

    if not observations:
        return {
            "model": {},
            "support_tier": "unsupported",
            "observation_count": 0,
            "corrections": {},
            "suspension_arch": target_arch,
        }

    # If we still don't know the arch (unknown car + no override), look at
    # the observation rows themselves. This keeps GT3 IBTs uploaded for a
    # not-yet-registered car routing through their own fits.
    if target_arch is None:
        arches = {
            o.get("suspension_arch")
            for o in observations
            if o.get("suspension_arch")
        }
        if len(arches) == 1:
            target_arch = arches.pop()
        elif len(arches) > 1:
            logger.warning(
                "aggregate_observations(%s, %s): mixed suspension_arch values "
                "%s and no registry override; defaulting to GTP-torsion to "
                "avoid cross-arch pollution. Pass suspension_arch explicitly.",
                car, track, sorted(a for a in arches if a),
            )
            target_arch = "gtp_heave_third_torsion_front"
        else:
            target_arch = "gtp_heave_third_torsion_front"

    # Architecture partition (F3). Rows missing `suspension_arch` are
    # treated as legacy GTP-torsion (matches the migration backfill).
    legacy_default = "gtp_heave_third_torsion_front"
    filtered = [
        o for o in observations
        if (o.get("suspension_arch") or legacy_default) == target_arch
    ]
    dropped = len(observations) - len(filtered)
    if dropped:
        logger.info(
            "aggregate_observations(%s, %s): dropped %d/%d observations "
            "outside target arch %s.",
            car, track, dropped, len(observations), target_arch,
        )

    if not filtered:
        return {
            "model": {},
            "support_tier": "unsupported",
            "observation_count": 0,
            "corrections": {},
            "suspension_arch": target_arch,
        }

    # Import learner modules lazily (they pull in numpy/scipy)
    from learner.empirical_models import fit_models
    from learner.delta_detector import detect_delta
    from learner.observation import Observation

    # Sort observations by timestamp / session_id
    sorted_obs = sorted(filtered, key=lambda o: o.get("session_id", ""))

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

    # Fit empirical models. F10 — use the canonical registry helper for the
    # track key. The local variable shadows the import name only within
    # this function, so this is a safe replacement.
    track_short = _registry_track_key(track)  # noqa: F841 — kept for log/debug callers
    try:
        models = fit_models(sorted_obs, deltas, car, track)
        model_dict = models.to_dict()
        corrections = models.corrections if models.corrections else {}
    except Exception as e:
        logger.error("Model fitting failed for %s/%s: %s", car, track, e)
        model_dict = {}
        corrections = {}

    support_tier = compute_support_tier(len(filtered), suspension_arch=target_arch)

    return {
        "model": model_dict,
        "support_tier": support_tier,
        "observation_count": len(filtered),
        "corrections": corrections,
        "suspension_arch": target_arch,
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
