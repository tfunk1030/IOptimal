"""Aero sensitivity analysis — gradients and stability windows.

Computes the sensitivity of DF balance and L/D to ride height changes at the
solver's operating point using central-difference approximation. Enables:
- Aero window: how much RH can vary before balance shifts >0.5%
- L/D cost of variance: quantifies the tradeoff of softer springs
- Gradient-aware rake optimization

Speed-of-query note: this module operates on **dynamic** ride heights at the
solver's operating-point speed. Aero compression scales with V², so callers
should compute static→dynamic compression using ``track.aero_reference_speed_kph``
(the V²-RMS over speed bands ≥100 kph) rather than the lap median or the
``car.aero_compression.ref_speed_kph`` calibration speed (commonly 230 kph).
The 230 kph reference is a *calibration* speed, not an operating-point speed,
and using it as a fallback under-predicts compression at high-speed tracks.
See ``track_model/profile.py:aero_reference_speed_kph`` for the proper source.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aero_model.interpolator import AeroSurface
    from car_model.cars import CarModel

logger = logging.getLogger(__name__)

# Adaptive central-difference: halve the step when the second-difference
# magnitude exceeds this %/mm² threshold (i.e. the balance surface is
# changing curvature faster than 0.20 %/mm over the step). 0.20 was picked
# to flag the steep-gradient regime (>2 %/mm linear gradient already; second
# difference of 0.20 %/mm² implies the linear gradient itself shifts by
# 0.20 %/mm over the step, which is a meaningful nonlinearity).
_CURVATURE_THRESHOLD_PCT_PER_MM2 = 0.20
_ADAPTIVE_INITIAL_STEP_MM = 1.0
_ADAPTIVE_MIN_STEP_MM = 0.0625  # 1.0 → 0.5 → 0.25 → 0.125 → 0.0625 (4 halvings)
_ADAPTIVE_MAX_HALVINGS = 4


def _adaptive_central_difference(
    f, x: float, h_initial: float = _ADAPTIVE_INITIAL_STEP_MM,
) -> tuple[float, float]:
    """Compute central-difference ``df/dx`` with adaptive step size.

    Halves ``h`` while the second-difference magnitude exceeds
    ``_CURVATURE_THRESHOLD_PCT_PER_MM2`` (capped at ``_ADAPTIVE_MAX_HALVINGS``
    iterations). Returns ``(gradient, h_used)``.

    ``f`` is a 1-D function ``float -> float``; the caller is responsible
    for partial-applying any other axes.
    """
    f0 = f(x)
    h = h_initial
    for _ in range(_ADAPTIVE_MAX_HALVINGS + 1):
        f_plus = f(x + h)
        f_minus = f(x - h)
        second_diff = (f_plus - 2 * f0 + f_minus) / (h * h)
        if abs(second_diff) <= _CURVATURE_THRESHOLD_PCT_PER_MM2:
            break
        next_h = h / 2.0
        if next_h < _ADAPTIVE_MIN_STEP_MM:
            break
        h = next_h
    gradient = (f_plus - f_minus) / (2 * h)
    return gradient, h


@dataclass
class AeroGradients:
    """Aero sensitivity at an operating point."""

    # Operating point
    front_rh_mm: float
    rear_rh_mm: float
    df_balance_pct: float
    ld_ratio: float

    # Sensitivity (central-difference gradients)
    dBalance_dFrontRH: float  # %/mm: DF balance change per mm front RH
    dBalance_dRearRH: float  # %/mm: DF balance change per mm rear RH
    dLD_dFrontRH: float  # ratio/mm: L/D change per mm front RH
    dLD_dRearRH: float  # ratio/mm: L/D change per mm rear RH

    # Aero window (± mm before 0.5% balance shift)
    front_rh_window_mm: float = 0.0
    rear_rh_window_mm: float = 0.0

    # Ride height excursion cost
    balance_variance_from_rh_pct: float = 0.0  # DF balance σ from measured RH σ
    ld_cost_of_variance: float = 0.0  # L/D loss from ride height variance

    def summary(self) -> str:
        """Multi-line summary of aero gradients."""
        lines = [
            f"Operating point: F{self.front_rh_mm:.1f}mm / R{self.rear_rh_mm:.1f}mm",
            f"  DF balance: {self.df_balance_pct:.2f}%  L/D: {self.ld_ratio:.3f}",
            f"Gradients:",
            f"  ∂(DF bal)/∂(Front RH): {self.dBalance_dFrontRH:+.4f} %/mm",
            f"  ∂(DF bal)/∂(Rear RH):  {self.dBalance_dRearRH:+.4f} %/mm",
            f"  ∂(L/D)/∂(Front RH):    {self.dLD_dFrontRH:+.5f} /mm",
            f"  ∂(L/D)/∂(Rear RH):     {self.dLD_dRearRH:+.5f} /mm",
            f"Aero window (±0.5% DF bal):",
            f"  Front RH: ±{self.front_rh_window_mm:.1f} mm",
            f"  Rear RH:  ±{self.rear_rh_window_mm:.1f} mm",
        ]
        if self.balance_variance_from_rh_pct > 0:
            lines.append(
                f"RH variance cost: DF σ={self.balance_variance_from_rh_pct:.3f}%, "
                f"L/D loss={self.ld_cost_of_variance:.4f}"
            )
        return "\n".join(lines)


def compute_gradients(
    surface: AeroSurface,
    car: CarModel,
    front_rh: float,
    rear_rh: float,
    front_rh_sigma_mm: float = 0.0,
    rear_rh_sigma_mm: float = 0.0,
    balance_window_pct: float = 0.5,
    h: float = _ADAPTIVE_INITIAL_STEP_MM,
) -> AeroGradients:
    """Compute aero gradients at an operating point using central differences.

    Step size is **adaptive**: starting from ``h`` (default 1.0 mm) the
    step is halved when the local second difference exceeds
    ``_CURVATURE_THRESHOLD_PCT_PER_MM2``. This trades the previous fixed
    0.5 mm step (which under-sampled steep maps and was noise-dominated on
    very shallow ones) for a per-axis step that fits the local curvature.
    Capped at 4 halvings (h ≥ 0.0625 mm) to avoid pathological loops.

    Parameters
    ----------
    surface : AeroSurface
        Interpolated aero surface for the current wing angle.
    car : CarModel
        Car model (for axis swap).
    front_rh, rear_rh : float
        Dynamic (not static-garage) ride heights in mm at the operating
        point. The caller is responsible for V² compression scaling — see
        the module docstring on speed-of-query.
    front_rh_sigma_mm, rear_rh_sigma_mm : float
        Measured ride height standard deviations (mm). If nonzero, computes
        the DF balance variance and L/D cost from ride height oscillation.
    balance_window_pct : float
        Allowable DF balance shift for window computation (default 0.5%).
    h : float
        **Initial** step size for central-difference approximation (mm,
        default 1.0). The actual step used per axis may be smaller after
        adaptive halving.

    Returns
    -------
    AeroGradients
    """

    def _query_balance(f_rh: float, r_rh: float) -> float:
        af, ar = car.to_aero_coords(f_rh, r_rh)
        return surface.df_balance(af, ar)

    def _query_ld(f_rh: float, r_rh: float) -> float:
        af, ar = car.to_aero_coords(f_rh, r_rh)
        return surface.lift_drag(af, ar)

    # Operating point values
    bal_0 = _query_balance(front_rh, rear_rh)
    ld_0 = _query_ld(front_rh, rear_rh)

    dBal_dF, _ = _adaptive_central_difference(
        lambda f_rh: _query_balance(f_rh, rear_rh), front_rh, h_initial=h,
    )
    dBal_dR, _ = _adaptive_central_difference(
        lambda r_rh: _query_balance(front_rh, r_rh), rear_rh, h_initial=h,
    )
    dLD_dF, h_lf = _adaptive_central_difference(
        lambda f_rh: _query_ld(f_rh, rear_rh), front_rh, h_initial=h,
    )
    dLD_dR, h_lr = _adaptive_central_difference(
        lambda r_rh: _query_ld(front_rh, r_rh), rear_rh, h_initial=h,
    )

    # Aero window: ± mm before balance_window_pct shift
    front_window = abs(balance_window_pct / dBal_dF) if abs(dBal_dF) > 1e-6 else 50.0
    rear_window = abs(balance_window_pct / dBal_dR) if abs(dBal_dR) > 1e-6 else 50.0
    # Cap at reasonable maximum
    front_window = min(front_window, 50.0)
    rear_window = min(rear_window, 50.0)

    # Variance cost: propagate RH sigma through gradients
    # σ(balance) ≈ sqrt((∂B/∂F * σ_F)² + (∂B/∂R * σ_R)²)
    bal_var = 0.0
    ld_cost = 0.0
    if front_rh_sigma_mm > 0 or rear_rh_sigma_mm > 0:
        bal_var = (
            (dBal_dF * front_rh_sigma_mm) ** 2 +
            (dBal_dR * rear_rh_sigma_mm) ** 2
        ) ** 0.5

        # L/D cost: second-order Taylor of L/D oscillation around the
        # operating point. Reuse the L/D adaptive step so the second
        # difference is sampled at the same h as the first derivative.
        d2LD_dF2 = (
            _query_ld(front_rh + h_lf, rear_rh) - 2 * ld_0
            + _query_ld(front_rh - h_lf, rear_rh)
        ) / (h_lf ** 2)
        d2LD_dR2 = (
            _query_ld(front_rh, rear_rh + h_lr) - 2 * ld_0
            + _query_ld(front_rh, rear_rh - h_lr)
        ) / (h_lr ** 2)
        ld_cost = abs(
            0.5 * d2LD_dF2 * front_rh_sigma_mm ** 2 +
            0.5 * d2LD_dR2 * rear_rh_sigma_mm ** 2
        )

    return AeroGradients(
        front_rh_mm=front_rh,
        rear_rh_mm=rear_rh,
        df_balance_pct=bal_0,
        ld_ratio=ld_0,
        dBalance_dFrontRH=round(dBal_dF, 6),
        dBalance_dRearRH=round(dBal_dR, 6),
        dLD_dFrontRH=round(dLD_dF, 6),
        dLD_dRearRH=round(dLD_dR, 6),
        front_rh_window_mm=round(front_window, 1),
        rear_rh_window_mm=round(rear_window, 1),
        balance_variance_from_rh_pct=round(bal_var, 4),
        ld_cost_of_variance=round(ld_cost, 6),
    )
