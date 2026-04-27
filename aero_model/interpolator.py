"""Interpolated aerodynamic response surfaces.

Provides AeroSurface class that wraps scipy RegularGridInterpolator for
querying DF balance and L/D at any (front_RH, rear_RH) within the grid.
"""

import json
import logging
from pathlib import Path

import numpy as np
from scipy.interpolate import RegularGridInterpolator

logger = logging.getLogger(__name__)

PARSED_DIR = Path(__file__).parent.parent / "data" / "aeromaps_parsed"


class AeroSurface:
    """Interpolated aero response surface for one car at one wing angle.

    Attributes:
        car: Canonical car name (e.g., "bmw")
        wing_angle: Wing angle in degrees
        front_rh: 1D array of front ride height grid points (mm)
        rear_rh: 1D array of rear ride height grid points (mm)
        has_ld: True if the aero map publishes L/D data; False for balance-only
            maps (GT3 cars). When False, ``lift_drag()`` returns NaN cleanly
            instead of raising — callers must branch on ``surface.has_ld``.
    """

    def __init__(self, car: str, wing_angle: float,
                 front_rh: np.ndarray, rear_rh: np.ndarray,
                 balance: np.ndarray, ld: np.ndarray,
                 *, has_ld: bool = True):
        self.car = car
        self.wing_angle = wing_angle
        self.front_rh = front_rh
        self.rear_rh = rear_rh
        self._balance_raw = balance
        self._ld_raw = ld
        self.has_ld = has_ld

        # Build interpolators (front_rh = axis 0, rear_rh = axis 1)
        self._balance_interp = RegularGridInterpolator(
            (front_rh, rear_rh), balance,
            method="cubic", bounds_error=False, fill_value=np.nan,
        )
        # Balance-only maps (GT3) carry an all-NaN L/D grid — the cubic
        # interpolator's solver rejects non-finite RHS values, so we skip
        # constructing the L/D interpolator entirely. lift_drag() returns NaN
        # via the has_ld guard.
        if has_ld:
            self._ld_interp = RegularGridInterpolator(
                (front_rh, rear_rh), ld,
                method="cubic", bounds_error=False, fill_value=np.nan,
            )
        else:
            self._ld_interp = None

    def _clamp_rh(self, front_rh: float, rear_rh: float) -> tuple[float, float]:
        """Clamp ride heights to the grid boundaries to prevent extrapolation."""
        clamped_front = float(np.clip(front_rh, self.front_rh[0], self.front_rh[-1]))
        clamped_rear = float(np.clip(rear_rh, self.rear_rh[0], self.rear_rh[-1]))
        # Log when clamping moves the value significantly (>2mm)
        # Use debug level since bisection search calls this repeatedly
        if abs(clamped_front - front_rh) > 2.0:
            logger.debug(
                "Aero query front_rh=%.1f clamped to %.1f (grid: %.0f-%.0f)",
                front_rh, clamped_front, self.front_rh[0], self.front_rh[-1],
            )
        if abs(clamped_rear - rear_rh) > 2.0:
            logger.debug(
                "Aero query rear_rh=%.1f clamped to %.1f (grid: %.0f-%.0f)",
                rear_rh, clamped_rear, self.rear_rh[0], self.rear_rh[-1],
            )
        return clamped_front, clamped_rear

    def _interp_or_raise(
        self, interp: RegularGridInterpolator, front_rh: float, rear_rh: float, label: str,
    ) -> float:
        """Interpolate and raise if result is NaN."""
        result = float(interp([[front_rh, rear_rh]])[0])
        if np.isnan(result):
            raise ValueError(
                f"Aero interpolation returned NaN for {label} at "
                f"front_rh={front_rh:.1f}, rear_rh={rear_rh:.1f}"
            )
        return result

    def df_balance(self, front_rh: float, rear_rh: float) -> float:
        """Query DF balance (%) at given ride heights."""
        front_rh, rear_rh = self._clamp_rh(front_rh, rear_rh)
        return self._interp_or_raise(self._balance_interp, front_rh, rear_rh, "df_balance")

    def lift_drag(self, front_rh: float, rear_rh: float) -> float:
        """Query L/D ratio at given ride heights.

        Returns NaN cleanly for balance-only aero maps (``has_ld=False``,
        e.g. GT3 cars). Callers that need a numeric L/D must branch on
        ``surface.has_ld`` first.
        """
        front_rh, rear_rh = self._clamp_rh(front_rh, rear_rh)
        if not self.has_ld:
            return float("nan")
        return self._interp_or_raise(self._ld_interp, front_rh, rear_rh, "L/D")

    def query(self, front_rh: float, rear_rh: float) -> dict:
        """Query both DF balance and L/D at given ride heights.

        Returns dict with: front_rh, rear_rh, df_balance, ld, front_df_pct, rear_df_pct
        """
        bal = self.df_balance(front_rh, rear_rh)
        ld = self.lift_drag(front_rh, rear_rh)
        return {
            "car": self.car,
            "wing_angle": self.wing_angle,
            "front_rh_mm": front_rh,
            "rear_rh_mm": rear_rh,
            "df_balance_pct": round(bal, 2),
            "ld_ratio": round(ld, 3),
            "front_df_pct": round(bal, 2),
            "rear_df_pct": round(100.0 - bal, 2),
        }

    # Stall model constants — calibrated for GTP ground effect vehicles
    # Source: BMW aero map gradient analysis at low front RH
    STALL_ONSET_MM = 8.0    # Front RH at which stall begins to develop (mm)
    STALL_HARD_MM = 2.0     # Front RH at which full stall is reached (mm)
    if STALL_HARD_MM >= STALL_ONSET_MM:
        raise ValueError(
            f"STALL_HARD_MM ({STALL_HARD_MM}) must be < STALL_ONSET_MM ({STALL_ONSET_MM})"
        )

    def stall_proximity(self, front_rh_mm: float) -> dict:
        """Compute aerodynamic stall proximity and effects at a given front ride height.

        GTP cars run ground-effect aerodynamics with a vortex-sealed diffuser.
        As the front ride height drops below a critical threshold, the vortex
        starts to collapse (onset), and below a hard limit it fully collapses
        (stall), catastrophically reducing downforce and shifting balance forward.

        Args:
            front_rh_mm: Dynamic front ride height in mm

        Returns:
            dict with:
                stall_factor: 0.0 (nominal) to 1.0 (full stall)
                aero_state: "nominal" | "stall_warning" | "stall_risk"
                df_balance_shift_pct: Additional front bias from stall (%)
                ld_degradation: L/D degradation fraction (0.0 to 0.15)
        """
        if front_rh_mm >= self.STALL_ONSET_MM:
            return {
                "stall_factor": 0.0,
                "aero_state": "nominal",
                "df_balance_shift_pct": 0.0,
                "ld_degradation": 0.0,
            }

        # Stall factor: 0 at onset, 1 at hard limit
        stall_factor = (
            (self.STALL_ONSET_MM - front_rh_mm)
            / (self.STALL_ONSET_MM - self.STALL_HARD_MM)
        )
        stall_factor = float(max(0.0, min(1.0, stall_factor)))

        # Aero state classification
        if stall_factor < 0.25:
            aero_state = "stall_warning"
        else:
            aero_state = "stall_risk"

        # DF balance shifts forward as rear DF collapses faster than front
        # Max +1.5% front at full stall (empirical from BMW stall data)
        df_balance_shift_pct = stall_factor * 1.5

        # L/D degrades up to 15% at full stall (total downforce loss)
        ld_degradation = stall_factor * 0.15

        return {
            "stall_factor": round(stall_factor, 4),
            "aero_state": aero_state,
            "df_balance_shift_pct": round(df_balance_shift_pct, 3),
            "ld_degradation": round(ld_degradation, 4),
        }

    def find_rh_for_balance(self, target_balance: float,
                            rear_rh: float,
                            front_rh_range: tuple[float, float] | None = None) -> float | None:
        """Find the front ride height that achieves target DF balance at a given rear RH.

        Uses bisection search over the front_rh axis.
        Returns None if no solution exists in the valid range.
        """
        lo = front_rh_range[0] if front_rh_range else float(self.front_rh[0])
        hi = front_rh_range[1] if front_rh_range else float(self.front_rh[-1])

        bal_lo = self.df_balance(lo, rear_rh)
        bal_hi = self.df_balance(hi, rear_rh)

        # DF balance increases with front RH (more front = more front-biased)
        # Check if target is in range
        if target_balance < min(bal_lo, bal_hi) or target_balance > max(bal_lo, bal_hi):
            return None

        # Bisection
        for _ in range(50):
            mid = (lo + hi) / 2
            bal_mid = self.df_balance(mid, rear_rh)
            if abs(bal_mid - target_balance) < 0.01:
                return round(mid, 2)
            if (bal_mid < target_balance) == (bal_lo < bal_hi):
                lo = mid
            else:
                hi = mid

        return round((lo + hi) / 2, 2)

    def find_max_ld(self, target_balance: float | None = None,
                    balance_tolerance: float = 1.0) -> dict:
        """Find the ride height combination that maximizes L/D.

        If target_balance is specified, constrain to within balance_tolerance
        of that value.

        For balance-only aero maps (``has_ld=False``) returns an explicit
        error dict — there is no L/D to maximise.
        """
        if not self.has_ld:
            return {"error": "Aero map is balance-only — no L/D data available"}
        best_ld = -np.inf
        best_frh = None
        best_rrh = None

        for frh in self.front_rh:
            for rrh in self.rear_rh:
                if target_balance is not None:
                    bal = self.df_balance(float(frh), float(rrh))
                    if abs(bal - target_balance) > balance_tolerance:
                        continue
                ld = self.lift_drag(float(frh), float(rrh))
                if ld > best_ld:
                    best_ld = ld
                    best_frh = float(frh)
                    best_rrh = float(rrh)

        if best_frh is None:
            return {"error": "No valid combination found within constraints"}

        return self.query(best_frh, best_rrh)

    def __repr__(self):
        return (f"AeroSurface({self.car}, wing={self.wing_angle}°, "
                f"front_rh=[{self.front_rh[0]:.0f}-{self.front_rh[-1]:.0f}], "
                f"rear_rh=[{self.rear_rh[0]:.0f}-{self.rear_rh[-1]:.0f}])")


def load_car_surfaces(car: str) -> dict[float, AeroSurface]:
    """Load all wing angle surfaces for a car from parsed npz files.

    Args:
        car: Canonical car name (e.g., "bmw", "ferrari")

    Returns:
        Dict mapping wing_angle -> AeroSurface
    """
    meta_path = PARSED_DIR / f"{car}_aero.json"
    npz_path = PARSED_DIR / f"{car}_aero.npz"

    if not meta_path.exists():
        raise FileNotFoundError(
            f"No parsed data for {car}. Run: python -m aero_model.parse_all"
        )

    meta = json.loads(meta_path.read_text())
    data = np.load(str(npz_path))

    surfaces = {}
    front_rh = data["front_rh"]
    rear_rh = data["rear_rh"]
    # GT3 balance-only maps store an all-NaN L/D grid. Surface that flag so the
    # rake solver / objective can branch instead of trying to interpolate NaN.
    balance_only = bool(meta.get("balance_only", False))

    for wing in meta["wing_angles"]:
        balance = data[f"balance_{wing}"]
        ld = data[f"ld_{wing}"]
        surfaces[wing] = AeroSurface(
            car, wing, front_rh, rear_rh, balance, ld,
            has_ld=not balance_only,
        )

    return surfaces
