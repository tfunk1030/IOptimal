"""Backward-compatibility shim — canonical location is solver/vertical_dynamics.py."""
from solver.vertical_dynamics import *  # noqa: F401,F403
from solver.vertical_dynamics import (  # explicit re-exports for type checkers
    series_rate_nmm,
    combined_suspension_rate_nmm,
    axle_modal_rate_nmm,
    damped_excursion_mm,
    legacy_mass_to_shared_model_kg,
)
