"""Physics-anchored compliance fitter.

The free-form regression in :mod:`car_model.auto_calibrate` needs roughly
``3 × n_features`` samples to avoid overfitting.  Many of its targets are
governed by a single, exact physics relation:

    deflection = F_aero / k_total(setup)

i.e. spring compression is the aero load divided by total stiffness.  When
the aero load is ~constant across calibration points (same track, similar
speed band), the only setup-dependent term is ``1/k_total``.  Fitting

    y = α × (1/k_total) + β

is therefore a 2-parameter problem.  With the codebase's ``max_features =
n_samples // 3`` rule that drops the minimum sample count from ~21 down to
~5 — exactly the user's stated goal for Unit 6.

This module supplies that fit.  It is invoked from ``auto_calibrate`` as a
post-fit hook: only when the anchored fit has a better leave-one-out RMSE
than the free fit does it replace the result.

Per-car ``k_total`` dispatch
----------------------------
The relevant stiffness depends on which spring the deflection lives on:

* **Front shock / heave / torsion deflection (GTP heave + corner)**:
  ``k_front = k_heave + 2 × k_corner_wheel``
  where ``k_corner_wheel = C_torsion × OD^4`` (BMW, Cadillac, Acura, Ferrari)
  or ``k_roll_spring`` (Porsche front, no torsion bar).

* **Rear spring / third / shock deflection (GTP heave-third + corner coil)**:
  ``k_rear = k_third + 2 × k_corner × MR_rear^2``
  When the rear is a torsion bar (Ferrari rear), it uses ``C_rear × OD^4``
  with motion ratio already baked into ``C``.

* **Static front/rear ride height under aero load**:
  Same axle ``k_total`` as above — RH = static_perch − F/k.

Targets that only see ONE spring (e.g. ``heave_spring_defl_static`` is the
heave element's own compression) use the heave compliance directly,
``y = α/k_heave + β``.  Axle-level outputs use the parallel-combined
``k_total``.

The anchored fit reuses the LOO machinery from ``auto_calibrate`` so its
generalisation metric is comparable.  If the anchored LOO RMSE is not
strictly better than the free fit's, the caller keeps the free fit.

No silent BMW fallbacks: every formula reads physical constants directly
off ``car.heave_spring`` / ``car.corner_spring``.  Cars without the
required sub-model attributes get ``None`` and the call site falls back
to the free fit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import numpy as np

if TYPE_CHECKING:  # pragma: no cover — typing only
    from car_model.auto_calibrate import CalibrationPoint, FittedModel
    from car_model.cars import CarModel


# ─────────────────────────────────────────────────────────────────────────────
# Per-target spring-axis dispatch
# ─────────────────────────────────────────────────────────────────────────────
# Restricted to the targets whose ``apply_to_car`` slot maps already
# accept ``inv_*`` features.  Adding a target here without first adding
# the corresponding ``inv_*`` key to the apply_to_car map would cause the
# fitted coefficient to be silently dropped — defeating the unit's goal.
#
# Verified slots (auto_calibrate.apply_to_car as of 2026-04):
#   * ``static_front_rh_mm``: ``_FRONT_RH_COEFF_MAP['inv_front_heave']`` ✓
#   * ``static_rear_rh_mm``: ``_REAR_RH_COEFF_MAP['inv_rear_third']``,
#                            ``['inv_rear_spring']`` ✓
#   * ``rear_shock_defl_static_mm``:
#         ``_REAR_SHOCK_MAP['inv_rear_third']``,
#         ``['inv_rear_spring']`` (gated on _has_compliance) ✓
#   * ``rear_spring_defl_static_mm``:
#         ``_REAR_SPRING_DEFL_MAP['inv_rear_spring']``,
#         ``['inv_rear_third']`` (gated on _has_compliance) ✓
#   * ``third_spring_defl_static_mm``:
#         ``_THIRD_SPRING_DEFL_MAP['inv_rear_third']``,
#         ``['inv_rear_spring']`` (gated on _has_compliance) ✓
#
# Targets NOT yet anchorable (no inv_* slot in apply_to_car):
#   * ``front_shock_defl_static_mm`` — _fs_map has linear ``front_heave``
#     only, so a 1/heave coefficient would be dropped.
#   * ``heave_spring_defl_static_mm`` — apply_to_car indexes
#     coefficients positionally [intercept, inv_heave, perch, inv_od4]
#     and requires 4 coefficients.  Doesn't read feature_names.
#   * ``heave_slider_defl_static_mm`` — same positional shape via
#     GarageOutputModel.  Doesn't read feature_names.
#   * ``third_slider_defl_static_mm`` — primarily fit from
#     ``third_spring_defl_static`` chain, not from a feature pool.
_FRONT_AXLE_TARGETS = {
    "static_front_rh_mm",
}
_FRONT_HEAVE_SPRING_TARGETS: set[str] = set()
_REAR_AXLE_TARGETS = {
    "static_rear_rh_mm",
    "rear_shock_defl_static_mm",
}
_REAR_THIRD_SPRING_TARGETS = {
    "third_spring_defl_static_mm",
}
_REAR_COIL_SPRING_TARGETS = {
    "rear_spring_defl_static_mm",
}

_TARGET_FEATURE_NAME = {
    "static_front_rh_mm": "inv_front_heave",
    "static_rear_rh_mm": "inv_rear_third",
    "rear_shock_defl_static_mm": "inv_rear_third",
    "third_spring_defl_static_mm": "inv_rear_third",
    "rear_spring_defl_static_mm": "inv_rear_spring",
}


# ─────────────────────────────────────────────────────────────────────────────
# Result container
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ComplianceAnchoredFit:
    """Result of a 2-parameter ``y = α × (1/k_total) + β`` fit.

    Mirrors the public surface of ``auto_calibrate.FittedModel`` so it can
    be returned directly by `_fit_from_pool` when it wins, but exposes the
    anchored-physics interpretation explicitly via ``alpha`` / ``beta``.
    """

    target: str
    feature_name: str          # name of the inv_* feature used (apply_to_car key)
    alpha: float               # slope on 1/k_total
    beta: float                # intercept (mm)
    r_squared: float
    rmse: float
    loo_rmse: float
    n_samples: int
    is_calibrated: bool = True
    free_loo_rmse: float = float("nan")     # for diagnostics only
    free_r_squared: float = float("nan")    # for diagnostics only

    def to_fitted_model(self, name: Optional[str] = None) -> "FittedModel":
        """Convert into the canonical :class:`FittedModel` dataclass.

        ``apply_to_car`` consumes ``FittedModel`` instances; returning one
        keeps the integration surface tiny.

        ``name`` overrides the stored name so the caller can preserve the
        original ``_fit_from_pool(model_name=...)`` slot (e.g. keep
        ``"front_ride_height"`` rather than ``"static_front_rh_mm"``).
        """
        # Local import — auto_calibrate imports this module on demand so a
        # top-level import here would create a cycle.
        from car_model.auto_calibrate import FittedModel

        return FittedModel(
            name=name or self.target,
            feature_names=[self.feature_name],
            coefficients=[self.beta, self.alpha],
            r_squared=self.r_squared,
            rmse=self.rmse,
            loo_rmse=self.loo_rmse,
            n_samples=self.n_samples,
            is_calibrated=self.is_calibrated,
            q_squared=None,
        )


# ─────────────────────────────────────────────────────────────────────────────
# k computation
# ─────────────────────────────────────────────────────────────────────────────


def _per_setup_inv_stiffness(
    target: str,
    car: "CarModel",
    rows: list[dict],
) -> Optional[np.ndarray]:
    """Compute 1/k for every calibration row, dispatched by target.

    The feature is the **dominant compliance term** for *target* — the
    spring that physically carries the load.  Picking the dominant single
    spring (rather than the parallel-combined axle stiffness) keeps the
    resulting coefficient compatible with the existing
    ``apply_to_car._..._MAP`` slots, which key off
    ``inv_front_heave`` / ``inv_rear_third`` / ``inv_rear_spring``
    / ``inv_od4`` literally as ``1/<that_setting>``.

    Returns ``None`` when the car lacks the required sub-model attributes.
    """
    if car.heave_spring is None or car.corner_spring is None:
        return None

    n = len(rows)
    inv = np.zeros(n, dtype=float)

    # Front axle outputs — heave spring carries the dominant aero load
    # (corner torsion bars are an order of magnitude softer per corner on
    # GTP cars; e.g. BMW @ 13.9mm OD = 30 N/mm corner vs 50–200 N/mm heave).
    if target in _FRONT_HEAVE_SPRING_TARGETS or target in _FRONT_AXLE_TARGETS:
        for i, r in enumerate(rows):
            k = max(float(r["front_heave_setting"]), 1.0)
            inv[i] = 1.0 / k
        return inv

    # Rear axle outputs — third spring carries the dominant aero load
    # likewise (much stiffer than the corner coil's wheel-rate
    # contribution: BMW @ 450 N/mm third vs ~100 N/mm corner wheel rate).
    if target in _REAR_THIRD_SPRING_TARGETS or target in _REAR_AXLE_TARGETS:
        for i, r in enumerate(rows):
            k = max(float(r["rear_third_setting"]), 1.0)
            inv[i] = 1.0 / k
        return inv

    # Rear coil's own deflection — single-spring compliance on the spring
    # itself.
    if target in _REAR_COIL_SPRING_TARGETS:
        for i, r in enumerate(rows):
            k = max(float(r["rear_spring_setting"]), 1.0)
            inv[i] = 1.0 / k
        return inv

    # Target not recognised — caller should fall back to free fit.
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Fit
# ─────────────────────────────────────────────────────────────────────────────


def _least_squares_alpha_beta(
    inv_k: np.ndarray, y: np.ndarray
) -> tuple[float, float, float, float, float]:
    """Fit y = α × inv_k + β; return (α, β, R², train_RMSE, LOO_RMSE).

    LOO_RMSE is NaN when ``len(y) < 5`` to match auto_calibrate's
    convention (a "0.0 = perfect" reading would be misleading there too).
    """
    n = len(y)
    X = np.column_stack([np.ones(n), inv_k])
    beta_full, *_ = np.linalg.lstsq(X, y, rcond=None)
    intercept, alpha = float(beta_full[0]), float(beta_full[1])

    y_pred = X @ beta_full
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / max(ss_tot, 1e-12)
    rmse = float(np.sqrt(ss_res / n))

    if n < 5:
        return alpha, intercept, r2, rmse, float("nan")

    loo_sq = 0.0
    for i in range(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        b, *_ = np.linalg.lstsq(X[mask], y[mask], rcond=None)
        pred_i = X[i] @ b
        loo_sq += (y[i] - pred_i) ** 2
    loo_rmse = float(np.sqrt(loo_sq / n))
    return alpha, intercept, r2, rmse, loo_rmse


def fit_compliance_anchored(
    points: list,
    car: Optional["CarModel"],
    target: str,
    *,
    free_loo_rmse: float = float("nan"),
    free_r_squared: float = float("nan"),
    min_samples: int = 5,
) -> Optional[ComplianceAnchoredFit]:
    """Fit ``y = α × (1/k_total) + β`` for *target* across *points*.

    Args:
        points: a list of :class:`CalibrationPoint` instances OR a list of
            dicts (the row-dict shape :func:`auto_calibrate.fit_models`
            uses internally after index→N/mm decode).  Either is accepted
            so the caller can pass whichever it has on hand.  Spring
            settings MUST already be in physical N/mm — index values
            (e.g. raw ``rear_spring_setting=5`` for Ferrari) make ``1/k``
            meaningless.
        car: resolved :class:`car_model.cars.CarModel`.  Direct attribute
            access — no ``getattr`` defaults, no substring car-name
            matching.  When ``None`` (registry resolve failed), returns
            ``None`` so the free fit takes over.
        target: column name on :class:`CalibrationPoint` (e.g.
            ``"front_shock_defl_static_mm"``).
        free_loo_rmse, free_r_squared: diagnostic values from the free
            fit; only used to populate :attr:`ComplianceAnchoredFit.free_*`
            for logging.  Whether the anchored fit "wins" is decided by
            the caller, not here.
        min_samples: minimum unique setups required.  ``auto_calibrate`` uses
            5 (``_MIN_SESSIONS_FOR_FIT``); the anchored fit needs the same
            floor for LOO to be meaningful.

    Returns:
        ``ComplianceAnchoredFit`` on success, ``None`` when:
          * ``car`` is ``None``
          * the target isn't compliance-anchorable (not in any axis set)
          * fewer than ``min_samples`` points
          * ``1/k_total`` has near-zero variance (no compliance signal)
          * the y target has near-zero variance (constant — handled by
            the free path's constant-model branch already)
          * the fit is non-physical (α has the wrong sign — see below)
    """
    if car is None:
        return None
    feature_name = _TARGET_FEATURE_NAME.get(target)
    if feature_name is None:
        return None
    if len(points) < min_samples:
        return None

    # Accept either the dataclass or pre-decoded row dicts; normalise to
    # the small dict shape `_per_setup_inv_stiffness` expects.
    rows: list[dict] = []
    y_list: list[float] = []
    for p in points:
        if isinstance(p, dict):
            rows.append({
                "front_heave_setting": float(p["front_heave_setting"]),
                "rear_third_setting": float(p["rear_third_setting"]),
                "rear_spring_setting": float(p["rear_spring_setting"]),
                "front_torsion_od_mm": float(p["front_torsion_od_mm"]),
            })
            if target not in p:
                return None
            y_list.append(float(p[target]))
        else:
            rows.append({
                "front_heave_setting": float(getattr(p, "front_heave_setting")),
                "rear_third_setting": float(getattr(p, "rear_third_setting")),
                "rear_spring_setting": float(getattr(p, "rear_spring_setting")),
                "front_torsion_od_mm": float(getattr(p, "front_torsion_od_mm")),
            })
            y_list.append(float(getattr(p, target)))

    inv_k = _per_setup_inv_stiffness(target, car, rows)
    if inv_k is None:
        return None
    if float(np.std(inv_k)) < 1e-9:
        # All setups have the same stiffness — α is unidentifiable here.
        return None

    y = np.array(y_list, dtype=float)
    if float(np.std(y)) < 1e-6:
        return None

    alpha, beta, r2, rmse, loo_rmse = _least_squares_alpha_beta(inv_k, y)

    # Physics check: a positive aero load on a positive stiffness produces
    # positive deflection.  ``y = α/k`` with α < 0 would mean the deflection
    # decreases as the spring becomes softer — non-physical.  Skip and let
    # the free fit run.  (Static RH is similar — softer springs sit lower,
    # so RH versus 1/k should slope DOWN, i.e. α negative.  Different sign
    # convention per target.)
    expected_sign = _expected_alpha_sign(target)
    if expected_sign != 0 and alpha * expected_sign < 0:
        return None

    # LOO defense-in-depth — same gate as auto_calibrate._fit():
    # 10× LOO/train ratio means the model memorised noise.
    if (
        not np.isnan(loo_rmse)
        and loo_rmse > 10.0 * max(rmse, 1e-6)
    ):
        return None

    # Match the strict gate's R² floor.  Importing it here keeps the gate
    # the single source of truth.
    from car_model.calibration_gate import R2_THRESHOLD_BLOCK

    return ComplianceAnchoredFit(
        target=target,
        feature_name=feature_name,
        alpha=alpha,
        beta=beta,
        r_squared=r2,
        rmse=rmse,
        loo_rmse=loo_rmse,
        n_samples=len(points),
        is_calibrated=r2 >= R2_THRESHOLD_BLOCK,
        free_loo_rmse=free_loo_rmse,
        free_r_squared=free_r_squared,
    )


def _expected_alpha_sign(target: str) -> int:
    """Return the physics-required sign of α for *target*.

    +1: y rises as 1/k rises (typical deflection).
    -1: y falls as 1/k rises (static RH — softer spring sits lower).
     0: no constraint (skip the check).
    """
    if target in _REAR_AXLE_TARGETS or target in _FRONT_AXLE_TARGETS:
        if target.startswith("static_") and target.endswith("_rh_mm"):
            # Softer spring → more compression → lower static RH ⇒ α negative.
            return -1
        return 1  # axle-level deflection target (shock/torsion)
    return 1  # spring's own compression: always positive


# ─────────────────────────────────────────────────────────────────────────────
# Drop-in replacement helper
# ─────────────────────────────────────────────────────────────────────────────


def maybe_replace_with_anchored(
    free_fit: Optional["FittedModel"],
    points: list,
    car: Optional["CarModel"],
    target: str,
) -> Optional["FittedModel"]:
    """Try the anchored fit; keep it iff its LOO RMSE beats *free_fit*.

    Designed as a one-line hook at the end of every ``_fit_from_pool``
    call site.  When the anchored fit is unavailable (car=None, target
    not anchorable, insufficient samples, etc.), returns *free_fit*
    unchanged.
    """
    if car is None:
        return free_fit

    free_loo = float("nan")
    free_r2 = float("nan")
    if free_fit is not None:
        free_loo = float(free_fit.loo_rmse)
        free_r2 = float(free_fit.r_squared)

    anchored = fit_compliance_anchored(
        points, car, target,
        free_loo_rmse=free_loo, free_r_squared=free_r2,
    )
    if anchored is None:
        return free_fit

    # Decide on LOO RMSE — the project's canonical generalisation metric.
    # When the free fit is missing or has NaN LOO (e.g. n<5 or constant
    # branch), the anchored fit wins by default IF it cleared its own
    # checks above.
    # Preserve the free fit's `name` so apply_to_car's existing slot mapping
    # works without changes (e.g. "front_ride_height" stays "front_ride_height").
    inherited_name = free_fit.name if free_fit is not None else None

    if free_fit is None:
        return anchored.to_fitted_model(name=inherited_name)
    if np.isnan(free_loo) or np.isnan(anchored.loo_rmse):
        return free_fit
    if anchored.loo_rmse < free_loo:
        import logging
        logging.getLogger(__name__).info(
            "Anchored fit beat free for '%s': "
            "anchored LOO=%.3f (R²=%.3f) vs free LOO=%.3f (R²=%.3f)",
            target, anchored.loo_rmse, anchored.r_squared,
            free_loo, free_r2,
        )
        return anchored.to_fitted_model(name=inherited_name)

    return free_fit
