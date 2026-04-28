"""Shared utility for converting solver step outputs to canonical params dicts.

The canonical params dict is the interface between solver steps and the
ObjectiveFunction.evaluate_physics() method.  This module extracts the
mapping logic so it can be used by both the branching solver (for
in-solve scoring) and candidate_search (for post-hoc scoring).
"""
from __future__ import annotations

from typing import Any


def solver_steps_to_params(
    step1: Any,
    step2: Any,
    step3: Any,
    step4: Any | None = None,
    step5: Any | None = None,
    step6: Any | None = None,
    car: Any | None = None,
) -> dict[str, float]:
    """Convert solver step solution objects to a flat canonical params dict.

    The returned dict uses the same keys that ``ObjectiveFunction.evaluate_physics()``
    reads via ``params.get(...)``.  Steps 4-6 are optional -- missing steps are
    simply omitted from the dict, and evaluate_physics() falls back to car-model
    baselines for those parameters.

    Parameters
    ----------
    step1 : RakeSolution
    step2 : HeaveSolution
    step3 : CornerSpringSolution
    step4 : ARBSolution, optional
    step5 : WheelGeometrySolution, optional
    step6 : DamperSolution, optional
    car : CarModel, optional -- used for Ferrari indexed control translation
    """
    params: dict[str, float] = {}

    # ── Step 1: Rake / Ride Heights ──
    if step1 is not None:
        params["front_pushrod_offset_mm"] = step1.front_pushrod_offset_mm
        params["rear_pushrod_offset_mm"] = step1.rear_pushrod_offset_mm

    # ── Step 2: Heave / Third Springs ──
    # Skip when step2 is a HeaveSolution.null() instance (present=False) — for GT3
    # cars there is no heave/third architecture and the numeric fields are zero
    # placeholders. Reading them into params would write 0.0 garbage that
    # downstream consumers treat as a real driver-loaded value (Key Principle 8:
    # no silent fallbacks). Default `present=True` keeps GTP behaviour unchanged
    # and tolerates legacy/mock objects that lack the attribute.
    if step2 is not None and getattr(step2, "present", True):
        params["front_heave_spring_nmm"] = step2.front_heave_nmm
        params["rear_third_spring_nmm"] = step2.rear_third_nmm

    # ── Step 3: Corner Springs ──
    # On GT3 (suspension_arch=GT3_COIL_4WHEEL) the front torsion bar does not
    # exist — `step3.front_torsion_od_mm` is a zero placeholder. Skip writing it
    # to avoid polluting params with a fake torsion OD that downstream consumers
    # would treat as real. Rear spring is still valid on GT3 (coil-over rear).
    # Guarded on `car.suspension_arch.has_front_torsion_bar` when `car` is
    # available; falls back to the legacy GTP behaviour if `car` is None.
    if step3 is not None:
        _has_front_torsion = True
        if car is not None:
            _arch = getattr(car, "suspension_arch", None)
            if _arch is not None:
                _has_front_torsion = bool(getattr(_arch, "has_front_torsion_bar", True))
        if _has_front_torsion:
            params["front_torsion_od_mm"] = step3.front_torsion_od_mm
        params["rear_spring_rate_nmm"] = step3.rear_spring_rate_nmm

    # ── Step 4: ARBs ──
    if step4 is not None:
        params["front_arb_blade"] = float(step4.front_arb_blade_start)
        params["rear_arb_blade"] = float(step4.rear_arb_blade_start)
        # Size labels are strings in the garage but evaluate_physics uses them
        # for stiffness lookup -- pass through as-is for cars that need them.
        if hasattr(step4, "front_arb_size"):
            params["front_arb_size"] = step4.front_arb_size
        if hasattr(step4, "rear_arb_size"):
            params["rear_arb_size"] = step4.rear_arb_size

    # ── Step 5: Geometry ──
    if step5 is not None:
        params["front_camber_deg"] = step5.front_camber_deg
        params["rear_camber_deg"] = step5.rear_camber_deg
        params["front_toe_mm"] = step5.front_toe_mm
        params["rear_toe_mm"] = step5.rear_toe_mm

    # ── Step 6: Dampers ──
    if step6 is not None:
        # Average front/rear pairs -- evaluate_physics uses axle-level clicks.
        for corner_pair, prefix in [
            (("lf", "rf"), "front"),
            (("lr", "rr"), "rear"),
        ]:
            for field in ("ls_comp", "ls_rbd", "hs_comp", "hs_rbd", "hs_slope"):
                values = []
                for corner_name in corner_pair:
                    corner = getattr(step6, corner_name, None)
                    if corner is not None:
                        val = getattr(corner, field, None)
                        if val is not None:
                            values.append(float(val))
                if values:
                    # Use the first corner (L side) as the axle representative.
                    # In practice LF==RF and LR==RR for axle-symmetric setups.
                    params[f"{prefix}_{field}"] = values[0]

    return params
