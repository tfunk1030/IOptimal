"""Physics-self-consistent virtual data anchors for the calibration regression.

Sparse calibration datasets (Cadillac at 2 points, Acura at 8 unique setups)
produce unstable regression intercepts and slopes: a single noisy real point
can swing the fitted intercept by millimetres because there's no anchor pulling
it toward physical truth. This module synthesises ≤ 5 physics-anchored
``CalibrationPoint`` instances per regression target. Each virtual point pairs
a physically-realisable setup (within the car's setup_registry ranges) with an
expected output computed from physics only — never from a curve fit — using
the SAME aero-load math (``aero_compression``) and k_total formula
(``CornerSpringModel`` / ``HeaveSpringModel``) as real points.

Anchor types (additive; ``generate_virtual_anchors(car, target)`` returns only
the anchors that are physics-derivable for the given pair):

1. Zero-pushrod baseline — anchors the regression intercept at the
   geometry-baseline ride height (``pushrod.{front_pinned, rear_base}_rh_mm``).
2. Pushrod-slope samples — emitted when the car has a non-zero
   ``rear_pushrod_to_rh`` so the regression sees known (pushrod, RH) pairs.
3. Stiff-asymptote — for deflection targets, max-stiffness setup gives
   expected defl ≈ 0.5 mm (compliance physics ``defl ∝ F/k``; small non-zero
   floor reflects tyre/bushing compliance).
4. Compliance baseline — mid-stiffness setup gives expected defl =
   ``aero_compression`` at the reference speed.

All virtual points carry ``synthesized=True`` so callers can filter them out
of dedupe / display / min-session count logic.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from car_model.cars import CarModel
    from car_model.auto_calibrate import CalibrationPoint


# Targets we know how to synthesise anchors for. Each maps to a
# physics-derivation function below.
_SUPPORTED_TARGETS: frozenset[str] = frozenset({
    "front_static_rh",
    "rear_static_rh",
    "front_shock_defl_static",
    "rear_shock_defl_static",
    "rear_spring_defl_static",
    "third_spring_defl_static",
    "heave_spring_defl_static",
})


def _baseline_setup_dict(car: "CarModel") -> dict:
    """Return a baseline-physics setup dict, used as the kwargs to
    ``CalibrationPoint(**...)``.

    Only fields the regression actually consumes are populated. Other fields
    default to 0.0 from the dataclass — they're fingerprint-only.
    """
    hsm = car.heave_spring
    csm = car.corner_spring

    # Pick a mid-of-range physical rate so 1/k features are well-conditioned.
    fr_lo, fr_hi = hsm.front_spring_range_nmm
    rr_lo, rr_hi = hsm.rear_spring_range_nmm
    # Geometric mid (avoids picking near-zero front_heave for legacy GTP cars
    # whose lo bound is 0 by convention).
    fr_mid = max(fr_lo, 1.0) * 0.5 + fr_hi * 0.5
    rr_mid = max(rr_lo, 1.0) * 0.5 + rr_hi * 0.5

    # Front torsion baseline OD: prefer first option, else 0 (non-torsion cars).
    fr_torsion_od = 0.0
    options = getattr(csm, "front_torsion_od_options", None) or []
    if options:
        fr_torsion_od = float(options[0])

    # Rear coil baseline (for cars with coil rear): use mid of corner spring
    # range when defined, else fall back to rear_third mid (Ferrari uses an
    # indexed rear torsion bar — its rate is not directly settable here).
    rear_coil_mid = rr_mid
    rear_coil_range = getattr(csm, "rear_coil_spring_range_nmm", None)
    if rear_coil_range and len(rear_coil_range) == 2:
        rl, rh = rear_coil_range
        rear_coil_mid = float(rl) * 0.5 + float(rh) * 0.5

    return {
        "session_id": "_synth",
        "track": "_synth",
        "wing_deg": 0.0,
        "fuel_l": float(car.fuel_capacity_l) * 0.5,  # mid-stint
        "front_heave_setting": float(fr_mid),
        "rear_third_setting": float(rr_mid),
        "front_heave_perch_mm": float(hsm.perch_offset_front_baseline_mm),
        "rear_third_perch_mm": float(hsm.perch_offset_rear_baseline_mm),
        "front_torsion_od_mm": float(fr_torsion_od),
        "rear_spring_setting": float(rear_coil_mid),
        "rear_spring_perch_mm": 0.0,
        "front_pushrod_mm": 0.0,
        "rear_pushrod_mm": 0.0,
        "front_camber_deg": -3.0,
        "rear_camber_deg": -2.0,
        # ARB / corner weights / telemetry left at 0.0 — fingerprint-only.
    }


def _make_point(car: "CarModel", overrides: dict, target: str, value: float,
                tag: str) -> "CalibrationPoint":
    """Build a CalibrationPoint with the right output channel populated."""
    from car_model.auto_calibrate import CalibrationPoint

    setup = _baseline_setup_dict(car)
    setup.update(overrides)
    # Map target name → CalibrationPoint output column
    target_to_col = {
        "front_static_rh": "static_front_rh_mm",
        "rear_static_rh": "static_rear_rh_mm",
        "front_shock_defl_static": "front_shock_defl_static_mm",
        "rear_shock_defl_static": "rear_shock_defl_static_mm",
        "rear_spring_defl_static": "rear_spring_defl_static_mm",
        "third_spring_defl_static": "third_spring_defl_static_mm",
        "heave_spring_defl_static": "heave_spring_defl_static_mm",
    }
    col = target_to_col.get(target)
    if col is None:
        raise ValueError(f"unsupported target {target!r}")
    setup[col] = float(value)
    setup["session_id"] = f"_synth_{target}_{tag}"
    setup["assessment"] = f"virtual_anchor:{target}:{tag}"
    # Tag as synthesised so dedupe/count logic can filter.
    pt = CalibrationPoint(**{
        k: v for k, v in setup.items()
        if k in CalibrationPoint.__dataclass_fields__
    })
    if hasattr(pt, "synthesized"):
        pt = replace(pt, synthesized=True)
    return pt


def _front_static_rh_anchors(car: "CarModel") -> list["CalibrationPoint"]:
    """Anchor the front static RH intercept at the pinned-RH baseline.

    Front RH for GTP cars is sim-pinned at ``pushrod.front_pinned_rh_mm`` when
    the front pushrod is at zero offset. This is a *geometric* fact (not a
    fitted coefficient), so it's a clean intercept anchor.
    """
    pinned = float(car.pushrod.front_pinned_rh_mm)
    if pinned <= 0.0:
        return []
    out: list["CalibrationPoint"] = []

    # Anchor 1: zero-pushrod, mid-fuel baseline → static = pinned
    out.append(_make_point(car, {"front_pushrod_mm": 0.0},
                           "front_static_rh", pinned, "zero_pushrod"))

    # Anchor 2: zero-pushrod, low-fuel — fuel barely affects front RH so this
    # provides a second sample that pins the intercept under low-fuel conditions
    # and prevents the regression from fitting a phantom fuel slope.
    out.append(_make_point(car, {"front_pushrod_mm": 0.0,
                                  "fuel_l": float(car.fuel_stint_end_l)},
                           "front_static_rh", pinned, "zero_pushrod_low_fuel"))
    return out


def _rear_static_rh_anchors(car: "CarModel") -> list["CalibrationPoint"]:
    """Anchor the rear static RH intercept and pushrod slope at geometry."""
    base = float(car.pushrod.rear_base_rh_mm)
    slope = float(car.pushrod.rear_pushrod_to_rh)
    if base <= 0.0:
        return []
    out: list["CalibrationPoint"] = []

    # Anchor 1: zero rear pushrod → static_rear = base
    out.append(_make_point(car, {"rear_pushrod_mm": 0.0},
                           "rear_static_rh", base, "zero_pushrod"))

    # Anchor 2: rear pushrod = +5 mm → static_rear = base + 5*slope
    if abs(slope) > 1e-6:
        delta = 5.0
        expected = base + delta * slope
        out.append(_make_point(car, {"rear_pushrod_mm": delta},
                               "rear_static_rh", expected, "pushrod_plus5"))

    # Anchor 3: rear pushrod = -5 mm (symmetric, anchors slope on both sides)
    if abs(slope) > 1e-6:
        delta = -5.0
        expected = base + delta * slope
        out.append(_make_point(car, {"rear_pushrod_mm": delta},
                               "rear_static_rh", expected, "pushrod_minus5"))
    return out


def _stiff_setup_overrides(car: "CarModel") -> dict:
    """Override dict that drives every spring/torsion to its max-stiffness
    end of the legal range — used for asymptote anchors where defl → 0."""
    hsm = car.heave_spring
    csm = car.corner_spring
    overrides: dict = {
        "front_heave_setting": float(hsm.front_spring_range_nmm[1]),
        "rear_third_setting": float(hsm.rear_spring_range_nmm[1]),
    }
    options = getattr(csm, "front_torsion_od_options", None) or []
    if options:
        overrides["front_torsion_od_mm"] = float(max(options))
    rear_coil_range = getattr(csm, "rear_coil_spring_range_nmm", None)
    if rear_coil_range and len(rear_coil_range) == 2:
        overrides["rear_spring_setting"] = float(rear_coil_range[1])
    return overrides


def _deflection_anchors(car: "CarModel", target: str,
                         compression_attr: str) -> list["CalibrationPoint"]:
    """Two anchors for static deflection targets:
       - Stiff-asymptote: max-k → defl ≈ 0.5 mm (small non-zero floor)
       - Baseline: mid-k → defl = aero_compression (calibrated reference)
    """
    out: list["CalibrationPoint"] = []
    aero = car.aero_compression
    comp_value = getattr(aero, compression_attr, None)
    if comp_value is None or comp_value <= 0.0:
        return []

    # Asymptote anchor: max stiffness → defl ≈ 0.5 mm (not exactly 0
    # because tyre/bushing compliance always contributes a small offset and
    # forcing y=0 over-constrains the intercept relative to real data).
    out.append(_make_point(car, _stiff_setup_overrides(car),
                           target, 0.5, "stiff_asymptote"))

    # Baseline anchor: mid-rate setup → defl = aero_compression at reference
    # speed. This anchors the slope on 1/k compliance features.
    out.append(_make_point(car, {}, target, float(comp_value),
                           "baseline_compression"))
    return out


def generate_virtual_anchors(car: "CarModel", target: str) -> list["CalibrationPoint"]:
    """Return 3-5 virtual ``CalibrationPoint`` instances with physics-derived
    expected outputs for the given regression ``target``.

    Returns an empty list when the car's architecture or available data
    doesn't support sensible anchors (e.g. a deflection target on a car
    without a calibrated aero-compression value).

    Args:
        car: A fully-resolved ``CarModel`` instance (use
            ``car_model.cars.get_car(...)`` or ``registry.resolve_car`` to
            obtain). NEVER pass a substring-matched name.
        target: Output channel name (see ``_SUPPORTED_TARGETS``).

    The returned points carry ``synthesized=True`` and are physically
    realisable within the car's setup_registry ranges.
    """
    if target not in _SUPPORTED_TARGETS:
        return []
    if car is None:
        return []

    if target == "front_static_rh":
        return _front_static_rh_anchors(car)
    if target == "rear_static_rh":
        return _rear_static_rh_anchors(car)
    if target == "front_shock_defl_static":
        return _deflection_anchors(car, target, "front_compression_mm")
    if target == "rear_shock_defl_static":
        return _deflection_anchors(car, target, "rear_compression_mm")
    if target == "rear_spring_defl_static":
        return _deflection_anchors(car, target, "rear_compression_mm")
    if target == "third_spring_defl_static":
        return _deflection_anchors(car, target, "rear_compression_mm")
    if target == "heave_spring_defl_static":
        return _deflection_anchors(car, target, "front_compression_mm")
    return []


def supported_targets() -> Iterable[str]:
    """Public accessor for the list of supported regression targets."""
    return tuple(_SUPPORTED_TARGETS)
