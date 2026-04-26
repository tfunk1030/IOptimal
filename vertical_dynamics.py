"""Shared vertical-dynamics helpers for platform and damper calculations.

The repo previously mixed a frequency-domain ride-height excursion estimate,
an energy-only heave sizing model, and quarter-car damping calculations that
ignored the third/heave element. This module provides a small common layer
that keeps those calculations directionally consistent while preserving the
repo's BMW-first calibrated workflow.
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

# Below this effective sprung mass the energy-method excursion model is
# numerically degenerate (a 5 kg "axle" would imply ~200 Hz heave mode for
# realistic spring rates — physically meaningless for a GTP).
_MIN_EFFECTIVE_MASS_KG = 5.0
# Below this spring rate the excursion model becomes unreliable: the linear
# energy formula assumes elastic-dominant restoring force, but at very soft
# rates aero preload, bumpstops, and damper coupling dominate. iRacing GTP
# heave springs are ≥ 20 N/mm in practice; treat anything softer as
# out-of-domain for the model rather than returning a misleadingly small
# excursion (or 0.0 for k≤0, which silently lets the solver believe soft
# springs have zero travel).
_MIN_SPRING_RATE_NMM = 20.0


# BMW-calibrated damper energy constants for the energy-based excursion model.
# damper_velocity_fraction (0.25): average damper velocity during compression
#     stroke as a fraction of peak velocity (trapezoidal velocity profile).
# damper_energy_coupling (0.35): fraction of damper force acting against
#     compression (rest attributed to blow-down, gas spring, friction).
# These are NOT validated for non-BMW cars. Per-car calibration requires
# IBT-measured excursion data at multiple spring rates.
# Note: iRacing springs are perfectly linear (confirmed by Commodore's Garage),
# so the single-point sigma calibration in heave_solver is more reliable than
# it would be with real-world progressive springs.
DEFAULT_DAMPER_VELOCITY_FRACTION = 0.25
DEFAULT_DAMPER_ENERGY_COUPLING = 0.35


def series_rate_nmm(primary_nmm: float, secondary_nmm: float | None = None) -> float:
    """Return the equivalent series spring rate in N/mm."""
    if primary_nmm <= 0:
        return 0.0
    if secondary_nmm is None or secondary_nmm <= 0:
        return primary_nmm
    return 1.0 / ((1.0 / primary_nmm) + (1.0 / secondary_nmm))


def combined_suspension_rate_nmm(
    spring_rate_nmm: float,
    parallel_wheel_rate_nmm: float = 0.0,
) -> float:
    """Return the suspension rate before tyre compliance is applied."""
    return max(spring_rate_nmm, 0.0) + max(parallel_wheel_rate_nmm, 0.0)


def axle_modal_rate_nmm(
    corner_wheel_rate_nmm: float,
    axle_heave_rate_nmm: float,
    tyre_vertical_rate_nmm: float | None = None,
) -> float:
    """Approximate the axle heave/pitch modal rate seen by the sprung mass."""
    suspension_rate = max(corner_wheel_rate_nmm, 0.0) + max(axle_heave_rate_nmm, 0.0) * 0.5
    return series_rate_nmm(suspension_rate, tyre_vertical_rate_nmm)


def legacy_mass_to_shared_model_kg(
    legacy_effective_mass_kg: float,
    reference_spring_rate_nmm: float,
    *,
    tyre_vertical_rate_nmm: float | None = None,
    parallel_wheel_rate_nmm: float = 0.0,
) -> float:
    """Map a legacy spring-only effective mass onto the shared compliant model."""
    if legacy_effective_mass_kg <= 0 or reference_spring_rate_nmm <= 0:
        return max(legacy_effective_mass_kg, 0.0)

    suspension_ref_nmm = combined_suspension_rate_nmm(
        reference_spring_rate_nmm,
        parallel_wheel_rate_nmm,
    )
    compliant_ref_nmm = series_rate_nmm(suspension_ref_nmm, tyre_vertical_rate_nmm)
    if compliant_ref_nmm <= 0:
        return legacy_effective_mass_kg
    return legacy_effective_mass_kg * (compliant_ref_nmm / reference_spring_rate_nmm)


def damped_excursion_mm(
    velocity_p99_mps: float,
    effective_mass_kg: float,
    spring_rate_nmm: float,
    *,
    tyre_vertical_rate_nmm: float | None = None,
    parallel_wheel_rate_nmm: float = 0.0,
    damper_coeff_nsm: float = 0.0,
    damper_velocity_fraction: float = DEFAULT_DAMPER_VELOCITY_FRACTION,
    damper_energy_coupling: float = DEFAULT_DAMPER_ENERGY_COUPLING,
) -> float | None:
    """Estimate p99 vertical excursion from bump velocity using an energy model.

    Returns None when the inputs are outside the model's domain (degenerate
    effective mass or spring rate below the practical GTP minimum). Callers
    must treat None as "model has no opinion" — skip the constraint with an
    explicit reason rather than silently substituting 0.0, which would let
    the solver believe a 0 N/mm spring has zero travel.
    """
    if velocity_p99_mps <= 0:
        return 0.0

    if effective_mass_kg <= _MIN_EFFECTIVE_MASS_KG:
        logger.warning(
            "damped_excursion_mm: effective_mass_kg=%.3f below floor %.1f kg "
            "(degenerate physics — caller should skip this constraint)",
            effective_mass_kg, _MIN_EFFECTIVE_MASS_KG,
        )
        return None

    if spring_rate_nmm < _MIN_SPRING_RATE_NMM:
        logger.warning(
            "damped_excursion_mm: spring_rate_nmm=%.2f below model floor %.1f N/mm "
            "(energy model unreliable at very soft rates — caller should skip "
            "or apply soft-spring policy)",
            spring_rate_nmm, _MIN_SPRING_RATE_NMM,
        )
        return None

    suspension_rate_nmm = combined_suspension_rate_nmm(
        spring_rate_nmm,
        parallel_wheel_rate_nmm,
    )
    k_eff_nmm = series_rate_nmm(suspension_rate_nmm, tyre_vertical_rate_nmm)
    if k_eff_nmm <= 0:
        return None

    k_eff_nm = k_eff_nmm * 1000.0
    kinetic_energy_j = 0.5 * effective_mass_kg * velocity_p99_mps ** 2

    if damper_coeff_nsm <= 0 or damper_energy_coupling <= 0:
        return velocity_p99_mps * math.sqrt(effective_mass_kg / k_eff_nm) * 1000.0

    c_eff = damper_coeff_nsm * damper_energy_coupling
    v_avg = max(velocity_p99_mps * damper_velocity_fraction, 0.0)

    # Solve 0.5*k*x^2 + c*v_avg*x - E = 0 for the positive root.
    b = c_eff * v_avg
    discriminant = max(0.0, b * b + 2.0 * k_eff_nm * kinetic_energy_j)
    excursion_m = (-b + math.sqrt(discriminant)) / max(k_eff_nm, 1e-9)
    return max(excursion_m, 0.0) * 1000.0
