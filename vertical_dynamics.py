"""Backward-compatibility shim — canonical location is solver/vertical_dynamics.py."""

from solver.vertical_dynamics import *  # noqa: F401,F403

# Explicit re-exports for type checkers and IDE support
from solver.vertical_dynamics import (  # noqa: F401
    DEFAULT_DAMPER_ENERGY_COUPLING,
    DEFAULT_DAMPER_VELOCITY_FRACTION,
    axle_modal_rate_nmm,
    combined_suspension_rate_nmm,
    damped_excursion_mm,
    legacy_mass_to_shared_model_kg,
    series_rate_nmm,
)
