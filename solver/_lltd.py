"""Centralized LLTD (Lateral Load Transfer Distribution) target formula.

The OptimumG/Milliken "Magic Number" baseline for steady-state cornering balance:

    LLTD_target = front_weight_dist + (tyre_sens / 0.20) * (0.05 + hs_correction)

where:
    front_weight_dist : static front weight fraction (0..1)
    tyre_sens         : tyre load sensitivity coefficient (λ, typically 0.10–0.30)
    hs_correction     : speed-dependent correction (0.0 for slow tracks,
                        up to +0.01 for ~100% above-200kph tracks)

Per CLAUDE.md (LLTD epistemic gap, 2026-04-08): we have no direct LLTD
measurement from IBT. The OptimumG formula is the current authoritative
target when no `measured_lltd_target` is set on the car model.

This module is the single source of truth for that formula. Call sites:
    solver/arb_solver.py   — primary consumer (Step 4 ARB sizing)
    solver/explorer.py     — Latin Hypercube balance scoring
    solver/objective.py    — physics evaluation LLTD target
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Physically plausible LLTD bounds. A real GTP/Hypercar can't operate
# meaningfully outside this range — values past the bounds usually mean
# a bad input (negative tyre sensitivity, extreme lltd_offset, etc.).
LLTD_MIN = 0.30
LLTD_MAX = 0.75


def optimal_lltd(
    front_weight_dist: float,
    tyre_sens: float | None,
    pct_above_200kph: float = 0.0,
    *,
    car_name: str | None = None,
) -> float:
    """Return the OptimumG/Milliken LLTD target, clamped to [0.30, 0.75].

    Args:
        front_weight_dist: Static front weight fraction (0..1).
        tyre_sens: Tyre load sensitivity coefficient λ. None or negative
            values are treated as 0 with a logger.warning (the formula
            collapses to `front_weight_dist`, which preserves callers that
            don't have a calibrated sensitivity yet).
        pct_above_200kph: Fraction of lap time spent above 200 kph (0..1).
            Drives the high-speed correction (up to +1% LLTD on Le Mans /
            Monza-class tracks).
        car_name: Optional identifier for diagnostic logging when clamping.

    Returns:
        LLTD target in [LLTD_MIN, LLTD_MAX].
    """
    if tyre_sens is None or tyre_sens < 0.0:
        logger.warning(
            "optimal_lltd: tyre_sens=%r is invalid (None or negative); "
            "treating as 0 — LLTD target will collapse to front_weight_dist",
            tyre_sens,
        )
        tyre_sens = 0.0

    hs_correction = 0.01 * max(0.0, min(1.0, pct_above_200kph))
    target = front_weight_dist + (tyre_sens / 0.20) * (0.05 + hs_correction)

    clamped = max(LLTD_MIN, min(LLTD_MAX, target))
    if clamped != target:
        logger.warning(
            "optimal_lltd: target %.3f out of [%.2f, %.2f] — clamped to %.3f "
            "(car=%s, front_wd=%.3f, tyre_sens=%.3f, pct_hs=%.2f)",
            target, LLTD_MIN, LLTD_MAX, clamped,
            car_name or "unknown", front_weight_dist, tyre_sens, pct_above_200kph,
        )
    return clamped
