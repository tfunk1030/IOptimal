"""Aero sensitivity analysis — gradients and stability windows.

Computes the sensitivity of DF balance and L/D to ride height changes at the
solver's operating point using central-difference approximation. Enables:
- Aero window: how much RH can vary before balance shifts >0.5%
- L/D cost of variance: quantifies the tradeoff of softer springs
- Gradient-aware rake optimization
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aero_model.interpolator import AeroSurface
    from car_model.cars import CarModel


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
    h: float = 0.5,
) -> AeroGradients:
    """Compute aero gradients at an operating point using central differences.

    Parameters
    ----------
    surface : AeroSurface
        Interpolated aero surface for the current wing angle.
    car : CarModel
        Car model (for axis swap).
    front_rh, rear_rh : float
        Actual (not aero-coords) ride heights in mm at the operating point.
    front_rh_sigma_mm, rear_rh_sigma_mm : float
        Measured ride height standard deviations (mm). If nonzero, computes
        the DF balance variance and L/D cost from ride height oscillation.
    balance_window_pct : float
        Allowable DF balance shift for window computation (default 0.5%).
    h : float
        Step size for central-difference approximation (mm, default 0.5).

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

    # Central-difference gradients: ∂f/∂x ≈ (f(x+h) - f(x-h)) / (2h)
    dBal_dF = (_query_balance(front_rh + h, rear_rh) -
               _query_balance(front_rh - h, rear_rh)) / (2 * h)
    dBal_dR = (_query_balance(front_rh, rear_rh + h) -
               _query_balance(front_rh, rear_rh - h)) / (2 * h)
    dLD_dF = (_query_ld(front_rh + h, rear_rh) -
              _query_ld(front_rh - h, rear_rh)) / (2 * h)
    dLD_dR = (_query_ld(front_rh, rear_rh + h) -
              _query_ld(front_rh, rear_rh - h)) / (2 * h)

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

        # L/D cost: average L/D loss from oscillating around operating point
        # Second-order: L/D(x±σ) ≈ L/D(x) + ½ * d²L/d²x * σ²
        # For concave-down surfaces, this is always negative (loss)
        d2LD_dF2 = (_query_ld(front_rh + h, rear_rh) - 2 * ld_0 +
                     _query_ld(front_rh - h, rear_rh)) / (h ** 2)
        d2LD_dR2 = (_query_ld(front_rh, rear_rh + h) - 2 * ld_0 +
                     _query_ld(front_rh, rear_rh - h)) / (h ** 2)
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
