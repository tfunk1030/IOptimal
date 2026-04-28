"""Parameter coupling graph and coupled-adjuster pass (Unit C1).

Principle 5: Coupled evaluation. Every parameter change re-evaluates dependent
parameters. The 6-step solver chain runs forward only — Step 6 (dampers) cannot
revise Step 2 (heave). After the forward chain finishes, this module runs a
separate adjuster pass that propagates physical couplings between parameters.

Couplings modelled here:

  * front_heave_spring_nmm / rear_third_spring_nmm
        → modal axle ω_n changes (k_modal includes heave contribution)
        → critical damping c_crit = 2·sqrt(k·m) changes
        → at fixed ζ target, damper coefficient c = ζ·c_crit changes
        → damper clicks must be re-derived from the new c

  * rear_arb_blade_start
        → rear roll stiffness shifts → LLTD shifts rearward
        → rear damper rebound bias must compensate (LLTD shift rearward
          means more rear roll velocity at corner exit, demands stiffer
          rear HS rebound to avoid wheel-bounce on unloaded rear)

  * front_torsion_od_mm
        → front roll stiffness goes up steeply (k ∝ OD^4)
        → headroom for ARB to add roll stiffness shrinks; the feasible
          range collapses toward smaller blades

  * front_pushrod_offset_mm
        → static front RH changes → at high-speed cornering, dynamic RH
          changes by V² aero compression scaling
        → downstream aero balance and ride-height-dependent objectives
          need the updated dynamic RH

The pass is conservative: each rule is gated on a meaningful upstream delta
(>5% for spring rates, >0 for ARB blade, etc.) and clamps re-derived clicks
to the car's legal range. max_iters defaults to 3 to allow second-order
chains (heave→damper, then damper→ARB if a downstream rule cared, etc.) to
converge.

The pass NEVER mutates step objects directly — it operates on a flat
``solver_outputs`` dict so rules can compose without ordering issues. The
caller is responsible for applying the resulting changes back to step
objects via :func:`apply_coupled_changes_to_steps`.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable

from vertical_dynamics import axle_modal_rate_nmm

logger = logging.getLogger(__name__)

# Tolerance for "value didn't really change". Avoids infinite loops where a
# float round-trip (e.g. 4.0000001 vs 4.0) would otherwise count as a change.
_FLOAT_TOL = 1e-3
_INT_TOL = 0  # ints must match exactly

# Spring change must exceed this fraction of baseline before we re-derive
# downstream dampers. 5% covers normal solver step granularity (e.g. one
# 10 N/mm tick on a 200 N/mm baseline) but ignores rounding noise.
_SPRING_REL_THRESHOLD = 0.05


@dataclass
class CouplingChange:
    """One propagated change from the coupled-adjuster pass."""

    param: str
    old: Any
    new: Any
    rationale: str
    iteration: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "param": self.param,
            "old": self.old,
            "new": self.new,
            "rationale": self.rationale,
            "iteration": self.iteration,
        }


@dataclass
class CouplingRule:
    """One physical coupling between an upstream and a downstream parameter."""

    upstream_param: str
    downstream_param: str
    propagate_fn: Callable[[dict[str, Any], dict[str, Any], Any], Any]
    rationale: str  # human-readable physics explanation


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────

def _approx_equal(a: Any, b: Any) -> bool:
    """Tolerance-aware equality for ints, floats, None."""
    if a is None or b is None:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, int) and isinstance(b, int):
        return abs(a - b) <= _INT_TOL
    try:
        return abs(float(a) - float(b)) <= _FLOAT_TOL
    except (TypeError, ValueError):
        return a == b


def _changed_meaningfully(old: float, new: float, *, rel: float = _SPRING_REL_THRESHOLD) -> bool:
    """True if |new - old| / max(|old|, 1) exceeds relative threshold."""
    if old is None or new is None:
        return False
    base = max(abs(float(old)), 1.0)
    return abs(float(new) - float(old)) / base >= rel


def _clamp_int(value: float, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, round(value))))


# ──────────────────────────────────────────────────────────────────────────
# Per-rule physics functions
# ──────────────────────────────────────────────────────────────────────────

def _adjust_damper_for_new_spring(
    setup: dict[str, Any],
    outs: dict[str, Any],
    car: Any,
    *,
    axle: str,
) -> int | None:
    """Re-derive front/rear LS-comp damper clicks for the current spring rates.

    Triggered when heave (front) or third (rear) changes. The rule uses the
    same physics as :class:`solver.damper_solver.DamperSolver`:

        c_target = ζ · c_crit
        c_crit  = 2 · sqrt(k_modal · m_corner)
        clicks  = round( c_target · v_ref / force_per_click )

    Returns the new LS-comp click count, or ``None`` if any required field
    is missing (we don't fabricate values for incomplete car models).
    """
    if axle not in ("front", "rear"):
        raise ValueError(f"axle must be 'front' or 'rear', got {axle!r}")

    if car is None:
        return None
    damper = getattr(car, "damper", None)
    if damper is None:
        return None
    # Need calibrated ζ; otherwise downstream is meaningless.
    if not getattr(damper, "zeta_is_calibrated", False):
        return None
    # Need force-per-click + range to map clicks↔coefficient.
    fpc = getattr(damper, "ls_force_per_click_n", None)
    ls_range = getattr(damper, "ls_comp_range", None)
    if fpc is None or ls_range is None:
        return None

    is_front = axle == "front"
    heave_nmm = outs.get("front_heave_spring_nmm" if is_front else "rear_third_spring_nmm")
    corner_wheel_nmm = outs.get(
        "front_wheel_rate_nmm" if is_front else "rear_wheel_rate_nmm"
    )
    if heave_nmm is None or corner_wheel_nmm is None:
        return None

    tyre_nmm = (
        getattr(car, "tyre_vertical_rate_front_nmm", 0.0) if is_front
        else getattr(car, "tyre_vertical_rate_rear_nmm", 0.0)
    )
    fuel_l = float(setup.get("fuel_load_l", 0.0))
    total_mass = car.total_mass(fuel_l) if hasattr(car, "total_mass") else None
    if total_mass is None:
        return None
    weight_dist_front = float(getattr(car, "weight_dist_front", 0.5))
    if is_front:
        m_corner = total_mass * weight_dist_front / 2.0
        zeta = damper.zeta_target_ls_front
    else:
        m_corner = total_mass * (1.0 - weight_dist_front) / 2.0
        zeta = damper.zeta_target_ls_rear

    k_modal = axle_modal_rate_nmm(corner_wheel_nmm, heave_nmm, tyre_nmm)
    if k_modal <= 0 or m_corner <= 0:
        return None
    c_crit = 2.0 * math.sqrt(k_modal * 1000.0 * m_corner)
    c_target = zeta * c_crit

    # Reference velocity matches DamperSolver._coeff_to_clicks (LS regime).
    v_ls_ref = 0.025  # 25 mm/s
    n = float(getattr(damper, "digressive_exponent", 1.0))
    force_n = c_target * (v_ls_ref ** n)
    new_clicks = _clamp_int(force_n / max(fpc, 1.0), int(ls_range[0]), int(ls_range[1]))
    return new_clicks


def _adjust_damper_for_arb_lltd_shift(
    setup: dict[str, Any],
    outs: dict[str, Any],
    car: Any,
) -> int | None:
    """Bias rear HS rebound when ARB shifts LLTD rearward.

    ARB stiffer at the rear → more rear roll-stiffness fraction → LLTD
    rearward → more rear weight transfer in roll → unloaded rear extends
    faster on corner exit → must stiffen rear HS rebound to prevent wheel
    bounce. One click per ~5% blade-fraction change; clamped to the car's
    HS-comp range (rebound shares the comp range upper bound).
    """
    if car is None:
        return None
    damper = getattr(car, "damper", None)
    if damper is None:
        return None
    arb = getattr(car, "arb", None)
    if arb is None:
        return None

    blade = outs.get("rear_arb_blade_start")
    base_blade = outs.get("rear_arb_blade_baseline")
    cur_rear_hs_rbd = outs.get("rear_hs_rbd")
    if blade is None or base_blade is None or cur_rear_hs_rbd is None:
        return None

    blade_count = max(int(getattr(arb, "rear_blade_count", 0)), 1)
    delta_blade = int(blade) - int(base_blade)
    if delta_blade == 0:
        return cur_rear_hs_rbd
    # Each blade step ≈ 1 / blade_count of total rear ARB authority.
    blade_fraction = abs(delta_blade) / blade_count
    # 1 click bias per ~5% LLTD authority shift. Sign: stiffer rear ARB
    # (positive delta) → stiffer rear HS rebound.
    bias = int(math.copysign(round(blade_fraction / 0.05), delta_blade))
    if bias == 0:
        return cur_rear_hs_rbd

    hs_range = getattr(damper, "hs_comp_range", None)
    if hs_range is None:
        return None
    return _clamp_int(int(cur_rear_hs_rbd) + bias, int(hs_range[0]), int(hs_range[1]))


def _arb_range_for_torsion(
    setup: dict[str, Any],
    outs: dict[str, Any],
    car: Any,
) -> tuple[int, int] | None:
    """Recompute usable front-ARB blade range from current torsion-bar OD.

    Stiffer torsion bar → torsion contributes most front roll stiffness →
    less headroom for the ARB blade to add stiffness without overshooting
    target LLTD. We model this as a usable-blade range that shrinks as
    front_torsion_od_mm grows past the car's baseline OD. The output is a
    closed integer interval [lo, hi].
    """
    if car is None:
        return None
    arb = getattr(car, "arb", None)
    if arb is None:
        return None
    cs = getattr(car, "corner_spring", None)
    if cs is None:
        return None
    front_blade_count = int(getattr(arb, "front_blade_count", 0))
    if front_blade_count <= 1:
        return None  # Single-blade ARBs (GT3) — nothing to shrink.

    od_now = outs.get("front_torsion_od_mm")
    if od_now is None or od_now <= 0:
        return None
    # Baseline OD: if not in outs, fall back to mid-range from car spec.
    od_base = outs.get("front_torsion_od_baseline_mm")
    if od_base is None or od_base <= 0:
        od_base = float(od_now)

    # k ∝ OD^4 — so a 10% OD increase ≈ 46% stiffness increase.
    rel_stiffness = (float(od_now) / float(od_base)) ** 4 if od_base > 0 else 1.0
    # Each unit of relative stiffness above 1.0 removes one blade from the
    # high end. Cap the shrinkage at half the range so we don't degenerate.
    blades_to_drop = max(0, min(front_blade_count // 2, int(round((rel_stiffness - 1.0) / 0.10))))
    new_hi = max(1, front_blade_count - blades_to_drop)
    return (1, new_hi)


def _recompute_dynamic_rh(
    setup: dict[str, Any],
    outs: dict[str, Any],
    car: Any,
) -> tuple[float, float] | None:
    """Recompute dynamic ride heights at aero reference speed.

    front_pushrod_offset_mm change → static_front_rh shifts → at the track's
    aero reference speed, dynamic RH = static RH minus aero compression at
    that speed (V²-scaled from the 230 kph reference).
    """
    if car is None:
        return None
    static_front = outs.get("static_front_rh_mm")
    static_rear = outs.get("static_rear_rh_mm")
    if static_front is None or static_rear is None:
        return None
    aero_comp = getattr(car, "aero_compression", None)
    track = setup.get("track")
    speed_kph = None
    if track is not None:
        speed_kph = getattr(track, "aero_reference_speed_kph", None)
    if speed_kph is None or aero_comp is None:
        # Without speed-scaling we can't refine — just echo static.
        return (float(static_front), float(static_rear))
    try:
        front_dyn = float(static_front) - aero_comp.front_at_speed(speed_kph)
        rear_dyn = float(static_rear) - aero_comp.rear_at_speed(speed_kph)
    except (AttributeError, TypeError):
        return None
    return (front_dyn, rear_dyn)


# ──────────────────────────────────────────────────────────────────────────
# Rule registry
# ──────────────────────────────────────────────────────────────────────────

COUPLING_RULES: list[CouplingRule] = [
    CouplingRule(
        upstream_param="front_heave_spring_nmm",
        downstream_param="front_ls_comp",
        propagate_fn=lambda setup, outs, car: _adjust_damper_for_new_spring(
            setup, outs, car, axle="front"
        ),
        rationale=(
            "Heave change → axle modal rate changes → c_crit = 2·sqrt(k·m) "
            "shifts → damper c = ζ·c_crit must be re-derived"
        ),
    ),
    CouplingRule(
        upstream_param="rear_third_spring_nmm",
        downstream_param="rear_ls_comp",
        propagate_fn=lambda setup, outs, car: _adjust_damper_for_new_spring(
            setup, outs, car, axle="rear"
        ),
        rationale=(
            "Rear third change → rear axle modal rate changes → rear damper "
            "c = ζ·c_crit must be re-derived"
        ),
    ),
    CouplingRule(
        upstream_param="rear_arb_blade_start",
        downstream_param="rear_hs_rbd",
        propagate_fn=lambda setup, outs, car: _adjust_damper_for_arb_lltd_shift(
            setup, outs, car
        ),
        rationale=(
            "Rear ARB stiffer → LLTD shifts rearward → rear HS rebound "
            "must stiffen to prevent wheel-bounce on corner exit"
        ),
    ),
    CouplingRule(
        upstream_param="front_torsion_od_mm",
        downstream_param="front_arb_feasible_range",
        propagate_fn=lambda setup, outs, car: _arb_range_for_torsion(
            setup, outs, car
        ),
        rationale=(
            "Stiffer torsion bar (k ∝ OD⁴) consumes front roll stiffness "
            "headroom → usable front ARB blade range shrinks at the high end"
        ),
    ),
    CouplingRule(
        upstream_param="front_pushrod_offset_mm",
        downstream_param="dynamic_front_rh_mm_at_speed",
        propagate_fn=lambda setup, outs, car: _recompute_dynamic_rh(
            setup, outs, car
        ),
        rationale=(
            "Pushrod change → static RH change → dynamic RH at the track's "
            "aero reference speed shifts by V²-scaled aero compression"
        ),
    ),
]


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────

def propagate_couplings(
    setup_dict: dict[str, Any],
    solver_outputs: dict[str, Any],
    car: Any,
    *,
    max_iters: int = 3,
) -> tuple[dict[str, Any], list[CouplingChange]]:
    """Run the coupled-adjuster pass on the given solver outputs.

    Args:
        setup_dict:   Stable inputs (fuel, track, etc.) used by rules. Not
                      mutated.
        solver_outputs: Mutable dict of {param_name: value}. Rules read from
                      and write to this dict. Keys are flat (e.g. "front_ls_comp")
                      to keep rules composable.
        car:          The :class:`car_model.cars.CarModel` for physics constants.
        max_iters:    Cap on the fixed-point iteration. Defaults to 3.

    Returns:
        ``(updated_outputs, changes)`` where ``updated_outputs`` is the same
        dict mutated in place and ``changes`` is a flat list of
        :class:`CouplingChange` records, in the order they were applied.
    """
    changes: list[CouplingChange] = []
    # Map upstream param → baseline key in solver_outputs. Baselines are
    # the driver-loaded values (sourced via solver_outputs_to_dict from
    # current_setup); the upstream values are what the solver recommends.
    # Any meaningful gap triggers propagation.
    _BASELINE_KEY = {
        "front_heave_spring_nmm": "front_heave_spring_baseline_nmm",
        "rear_third_spring_nmm": "rear_third_spring_baseline_nmm",
        "front_torsion_od_mm": "front_torsion_od_baseline_mm",
        "rear_arb_blade_start": "rear_arb_blade_baseline",
        "front_pushrod_offset_mm": "front_pushrod_offset_baseline_mm",
    }

    def _has_delta(upstream_param: str) -> bool:
        up_now = solver_outputs.get(upstream_param)
        if up_now is None:
            return False
        baseline_key = _BASELINE_KEY.get(upstream_param)
        if baseline_key is None:
            return True  # no baseline registered → assume changed
        up_base = solver_outputs.get(baseline_key)
        if up_base is None:
            return True
        # Use relative threshold for spring-rate / OD floats; exact for ints
        if isinstance(up_now, int) and isinstance(up_base, int):
            return up_now != up_base
        return _changed_meaningfully(float(up_base), float(up_now), rel=_SPRING_REL_THRESHOLD)

    for iteration in range(1, max_iters + 1):
        iter_changes: list[CouplingChange] = []
        for rule in COUPLING_RULES:
            if not _has_delta(rule.upstream_param):
                continue

            try:
                new_val = rule.propagate_fn(setup_dict, solver_outputs, car)
            except Exception as e:
                logger.debug(
                    "Coupling rule %s→%s raised %s; skipping",
                    rule.upstream_param, rule.downstream_param, e,
                )
                continue
            if new_val is None:
                continue

            old_val = solver_outputs.get(rule.downstream_param)
            if old_val is not None and _approx_equal(old_val, new_val):
                continue

            iter_changes.append(CouplingChange(
                param=rule.downstream_param,
                old=old_val,
                new=new_val,
                rationale=rule.rationale,
                iteration=iteration,
            ))
            solver_outputs[rule.downstream_param] = new_val

        if not iter_changes:
            break
        changes.extend(iter_changes)
    else:
        # for-else fires when loop exits normally (no break) — i.e. we hit
        # max_iters without converging.
        if changes:
            logger.warning(
                "propagate_couplings did not converge in %d iterations "
                "(%d changes total); shipping last state.",
                max_iters, len(changes),
            )

    return solver_outputs, changes


def solver_outputs_to_dict(
    *,
    step1: Any,
    step2: Any,
    step3: Any,
    step4: Any,
    step5: Any,
    step6: Any,
    current_setup: Any | None = None,
) -> dict[str, Any]:
    """Flatten solver step objects into the dict shape :func:`propagate_couplings` expects.

    Pulls the upstream / downstream parameters used by COUPLING_RULES. Missing
    fields become ``None`` so rules can detect "data not available" cleanly.
    Also captures baseline values from ``current_setup`` (driver-loaded) under
    ``*_baseline`` keys for rules that need to compare against the unmodified
    chassis.
    """
    def gv(obj: Any, name: str) -> Any:
        return getattr(obj, name, None) if obj is not None else None

    def cur(name: str, default: Any = None) -> Any:
        return getattr(current_setup, name, default) if current_setup is not None else default

    rear_corner_wheel = None
    if step3 is not None and hasattr(step3, "rear_wheel_rate_nmm"):
        try:
            rear_corner_wheel = float(step3.rear_wheel_rate_nmm)
        except (TypeError, ValueError, AttributeError):
            rear_corner_wheel = None

    out: dict[str, Any] = {
        # Upstream (driving) parameters
        "front_heave_spring_nmm": gv(step2, "front_heave_nmm"),
        "rear_third_spring_nmm": gv(step2, "rear_third_nmm"),
        "front_torsion_od_mm": gv(step3, "front_torsion_od_mm"),
        "rear_arb_blade_start": gv(step4, "rear_arb_blade_start"),
        "front_pushrod_offset_mm": gv(step1, "front_pushrod_offset_mm"),

        # Downstream (driven) parameters
        "front_ls_comp": gv(gv(step6, "lf"), "ls_comp"),
        "rear_ls_comp": gv(gv(step6, "lr"), "ls_comp"),
        "rear_hs_rbd": gv(gv(step6, "lr"), "hs_rbd"),
        # The feasible-range key is set by the rule; start as None.
        "front_arb_feasible_range": None,
        "dynamic_front_rh_mm_at_speed": gv(step1, "dynamic_front_rh_mm"),

        # Auxiliary inputs the rules consume
        "front_wheel_rate_nmm": gv(step3, "front_wheel_rate_nmm"),
        "rear_wheel_rate_nmm": rear_corner_wheel,
        "static_front_rh_mm": gv(step1, "static_front_rh_mm"),
        "static_rear_rh_mm": gv(step1, "static_rear_rh_mm"),

        # Baselines for delta-detection — sourced from driver-loaded
        # current_setup so any solver recommendation that differs from what
        # the driver had will trigger downstream re-derivation. Falling back
        # to the post-solve value means "no delta" → no propagation.
        "front_heave_spring_baseline_nmm": cur("front_heave_nmm", gv(step2, "front_heave_nmm")),
        "rear_third_spring_baseline_nmm": cur("rear_third_nmm", gv(step2, "rear_third_nmm")),
        "front_pushrod_offset_baseline_mm": cur(
            "front_pushrod_offset_mm", gv(step1, "front_pushrod_offset_mm")
        ),
        "rear_arb_blade_baseline": cur(
            "rear_arb_blade_start", gv(step4, "rear_arb_blade_start")
        ),
        "front_torsion_od_baseline_mm": cur(
            "front_torsion_od_mm", gv(step3, "front_torsion_od_mm")
        ),
    }
    return out


def apply_coupled_changes_to_steps(
    *,
    step6: Any,
    coupled_outputs: dict[str, Any],
    changes: list[CouplingChange],
) -> None:
    """Apply propagated changes back to the live step objects.

    Currently only Step 6 (dampers) carries direct overrides — the front-ARB
    range and dynamic-RH outputs are advisory and surfaced in the report
    rather than mutating Step 4 / Step 1 directly. That matches the project
    convention of "coupling runs AFTER, never replacing the forward solver".
    """
    if step6 is None:
        return
    front_ls = coupled_outputs.get("front_ls_comp")
    rear_ls = coupled_outputs.get("rear_ls_comp")
    rear_hs_rbd = coupled_outputs.get("rear_hs_rbd")

    # Track which params actually changed so we don't write fields that
    # rules didn't touch.
    touched = {ch.param for ch in changes}

    if "front_ls_comp" in touched and front_ls is not None:
        for corner_name in ("lf", "rf"):
            corner = getattr(step6, corner_name, None)
            if corner is not None and hasattr(corner, "ls_comp"):
                corner.ls_comp = int(front_ls)
    if "rear_ls_comp" in touched and rear_ls is not None:
        for corner_name in ("lr", "rr"):
            corner = getattr(step6, corner_name, None)
            if corner is not None and hasattr(corner, "ls_comp"):
                corner.ls_comp = int(rear_ls)
    if "rear_hs_rbd" in touched and rear_hs_rbd is not None:
        for corner_name in ("lr", "rr"):
            corner = getattr(step6, corner_name, None)
            if corner is not None and hasattr(corner, "hs_rbd"):
                corner.hs_rbd = int(rear_hs_rbd)
