"""BMW-first empirical differential model for GTP cars.

The locking differential controls corner exit speed by managing torque
distribution between driven wheels.

Lock torque formula:
    lock_torque_Nm = preload_Nm + (n_plates × CLUTCH_TORQUE_PER_PLATE) / tan(ramp_angle_rad)

Key differential behaviour:
- Lower ramp angle = MORE locking (sharper wedge). Counterintuitive but correct.
- Coast ramp: active during braking/coast (entry behaviour)
- Drive ramp: active during acceleration (exit behaviour)

BMW constants:
- CLUTCH_TORQUE_PER_PLATE = 45 Nm per plate
- Valid coast ramp angles: 40, 45, 50 degrees
- Valid drive ramp angles: 65, 70, 75 degrees
- Clutch plate count: 4-8 (default 6)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analyzer.driver_style import DriverProfile
    from analyzer.extract import MeasuredState
    from car_model.cars import CarModel
    from track_model.profile import TrackProfile

# BMW-verified clutch plate torque constant
CLUTCH_TORQUE_PER_PLATE = 45.0  # Nm per plate

# BMW GTP default clutch plate count
BMW_DEFAULT_CLUTCH_PLATES = 6

# Typical GTP max engine torque (Nm) — BMW M8 ~700 Nm
DEFAULT_MAX_TORQUE_NM = 700.0

# Tyre constants
FRICTION_COEFF = 1.5         # GTP tyre peak friction coefficient
TYRE_RADIUS_M = 0.350        # GTP tyre radius (m)

# Valid ramp angle steps
COAST_RAMP_OPTIONS = [40, 45, 50]
DRIVE_RAMP_OPTIONS = [65, 70, 75]


def _clamp(v: float, lo: float, hi: float) -> float:
    """Clamp v to the range [lo, hi]."""
    return max(lo, min(hi, v))


def _snap_to_options(value: int, options: list[int]) -> int:
    """Snap value to the nearest element in options list."""
    return min(options, key=lambda x: abs(x - value))


def compute_effective_lock_pct(
    preload_nm: float,
    n_plates: int,
    ramp_deg: int,
    torque_input_nm: float = DEFAULT_MAX_TORQUE_NM * 0.7,
) -> float:
    """Public utility: compute effective diff lock percentage.

    Useful for comparing diff setups without instantiating DiffSolver.

    Args:
        preload_nm: Diff preload in Nm.
        n_plates: Number of clutch plates.
        ramp_deg: Ramp angle in degrees.
        torque_input_nm: Input torque (default: 70% of max).

    Returns:
        Lock percentage 0-100.
    """
    ramp_rad = math.radians(ramp_deg)
    lock_torque = preload_nm + (n_plates * CLUTCH_TORQUE_PER_PLATE) / math.tan(ramp_rad)
    return min(100.0, (lock_torque / max(torque_input_nm, 1.0)) * 100.0)


def compare_diff_configs(
    config_a: tuple[float, int, int],
    config_b: tuple[float, int, int],
    torque_nm: float = DEFAULT_MAX_TORQUE_NM * 0.7,
) -> dict[str, float]:
    """Compare two diff configurations: (preload_nm, n_plates, ramp_deg).

    Returns dict with lock percentages and delta for both coast and drive.
    """
    preload_a, plates_a, ramp_a = config_a
    preload_b, plates_b, ramp_b = config_b
    lock_a = compute_effective_lock_pct(preload_a, plates_a, ramp_a, torque_nm)
    lock_b = compute_effective_lock_pct(preload_b, plates_b, ramp_b, torque_nm)
    return {
        "lock_pct_a": round(lock_a, 1),
        "lock_pct_b": round(lock_b, 1),
        "delta_pct": round(lock_b - lock_a, 1),
    }


@dataclass
class DiffSolution:
    """Computed differential setup recommendation."""

    # Computed values
    lock_pct_coast: float       # % locked under coast/braking (0-100)
    lock_pct_drive: float       # % locked under drive/accel (0-100)
    preload_nm: float           # recommended preload (Nm)
    coast_ramp_deg: int         # recommended coast ramp (40/45/50 deg)
    drive_ramp_deg: int         # recommended drive ramp (65/70/75 deg)
    clutch_plates: int          # number of clutch plates

    # Predicted handling effects
    exit_understeer_index: float   # 0=neutral, +ve=understeer on exit
    entry_rotation_index: float    # 0=neutral, +ve=rotation on entry

    # Reasoning
    preload_reasoning: str
    ramp_reasoning: str

    # Effective lock comparison tools
    effective_lock_coast_torque_nm: float = 0.0
    effective_lock_drive_torque_nm: float = 0.0
    lock_reasoning: str = ""

    def summary(self, width: int = 63) -> str:
        lines = [
            "=" * width,
            "  DIFFERENTIAL EMPIRICAL MODEL",
            "=" * width,
            "",
            f"  Preload:       {self.preload_nm:.0f} Nm",
            f"  Coast ramp:    {self.coast_ramp_deg} deg  "
            f"(lower = more coast locking)",
            f"  Drive ramp:    {self.drive_ramp_deg} deg  "
            f"(lower = more drive locking)",
            f"  Clutch plates: {self.clutch_plates}",
            "",
            f"  Lock % coast/entry: {self.lock_pct_coast:.1f}%",
            f"  Lock % drive/exit:  {self.lock_pct_drive:.1f}%",
            "",
            f"  Exit understeer index:  {self.exit_understeer_index:+.3f}",
            f"  Entry rotation index:   {self.entry_rotation_index:+.3f}",
            "",
            "  PRELOAD:",
            *[f"    {line}" for line in self.preload_reasoning.split("; ")],
            "",
            "  RAMPS:",
            *[f"    {line}" for line in self.ramp_reasoning.split("; ")],
            "",
            "  INTERPRETATION:",
        ]

        # Interpret exit understeer index
        if self.exit_understeer_index > 0.15:
            lines.append("    -> Exit: leans understeer. Soften diff or reduce preload")
            lines.append("       if car pushes wide on exit.")
        elif self.exit_understeer_index < -0.05:
            lines.append("    -> Exit: leans oversteer. Stiffen diff or increase preload")
            lines.append("       if car snaps on exit.")
        else:
            lines.append("    -> Exit: near-neutral. Diff well-matched to driving style.")

        # Interpret entry rotation
        if self.entry_rotation_index > 0.3:
            lines.append("    -> Entry: good rotation. Trail braking will rotate car.")
        elif self.entry_rotation_index < 0.1:
            lines.append("    -> Entry: low rotation. Car may push on entry.")

        lines.append("")
        lines.append("=" * width)
        return "\n".join(lines)


class DiffSolver:
    """BMW-first differential solver calibrated for iRacing telemetry.

    Derives preload, ramp angles, and lock percentages from:
    - Car mass and a coarse rear-axle load-transfer proxy at corner exit
    - Driver style (trail braking depth, throttle application)
    - Measured traction state (body slip, rear power slip ratios)
    - Track demand (peak lateral g)

    The preload model is intentionally empirical. The available telemetry does
    not contain enough chassis and tyre-state detail to justify a full
    first-principles locking-torque derivation.
    """

    def __init__(
        self,
        car: "CarModel",
        max_torque_nm: float = DEFAULT_MAX_TORQUE_NM,
    ) -> None:
        self.car = car
        self.max_torque_nm = getattr(car, "max_torque_nm", max_torque_nm)

    @classmethod
    def solve_defaults(cls, car: "CarModel", track: "TrackProfile | None" = None) -> DiffSolution:
        """Solve with conservative defaults for a neutral driver.

        Used by the standalone solver (no IBT data). Produces a sensible
        baseline differential setup for an average smooth driver.

        Args:
            car: Car physical model
            track: Track profile (used for peak lateral g if provided)

        Returns:
            DiffSolution with neutral/conservative baseline settings
        """
        from analyzer.driver_style import DriverProfile
        from analyzer.extract import MeasuredState

        # Conservative neutral defaults: moderate trail braking, moderate throttle
        neutral_driver = DriverProfile(
            trail_brake_depth_mean=0.3,
            trail_brake_classification="moderate",
            throttle_progressiveness=0.6,
            throttle_classification="moderate",
        )
        # Nominal measured state: low body slip, typical rear power slip
        nominal_measured = MeasuredState(
            body_slip_p95_deg=2.0,
            rear_power_slip_ratio_p95=0.03,
            rear_slip_ratio_p95=0.03,
        )

        return cls(car).solve(neutral_driver, nominal_measured, track)

    def solve(
        self,
        driver: "DriverProfile",
        measured: "MeasuredState",
        track: "TrackProfile | None" = None,
        setup: object = None,
    ) -> DiffSolution:
        """Compute differential setup recommendation.

        Args:
            driver: Driver behavior profile (throttle style, trail braking)
            measured: Measured telemetry state (body slip, slip ratios)
            track: Track demand profile (lateral g, corner speeds) — optional

        Returns:
            DiffSolution with recommended preload, ramps, and full reasoning
        """
        preload_nm, preload_reasoning = self._compute_preload(driver, measured, track)
        coast_ramp, drive_ramp, ramp_reasoning = self._compute_ramps(driver)

        # Use actual clutch plate count: setup (IBT) > car model > garage > default
        clutch_plates = 0
        if setup is not None:
            clutch_plates = getattr(setup, 'diff_clutch_plates', 0) or 0
        if clutch_plates <= 0:
            clutch_plates = getattr(self.car, 'diff_clutch_plates', 0)
        if clutch_plates <= 0:
            garage = getattr(self.car, 'garage_ranges', None)
            if garage is not None and hasattr(garage, 'diff_clutch_plates_options'):
                clutch_plates = garage.diff_clutch_plates_options[-1]
            else:
                clutch_plates = BMW_DEFAULT_CLUTCH_PLATES
        torque_input = self.max_torque_nm * 0.7  # typical cornering torque

        lock_pct_coast = self._lock_pct(preload_nm, clutch_plates, coast_ramp, torque_input)
        lock_pct_drive = self._lock_pct(preload_nm, clutch_plates, drive_ramp, torque_input)

        # Compute effective lock torques for comparison
        coast_ramp_rad = math.radians(coast_ramp)
        drive_ramp_rad = math.radians(drive_ramp)
        effective_lock_coast_torque = preload_nm + (clutch_plates * CLUTCH_TORQUE_PER_PLATE) / math.tan(coast_ramp_rad)
        effective_lock_drive_torque = preload_nm + (clutch_plates * CLUTCH_TORQUE_PER_PLATE) / math.tan(drive_ramp_rad)

        # Exit understeer index: how much the diff tends to push on exit.
        # Higher drive lock -> more understeer tendency on exit.
        rear_power_slip = (
            measured.rear_power_slip_ratio_p95
            if measured.rear_power_slip_ratio_p95 > 0
            else measured.rear_slip_ratio_p95
        )
        exit_oversteer_factor = min(rear_power_slip * 2.0, 0.5)
        exit_understeer_index = (lock_pct_drive / 100.0) * 0.6 - exit_oversteer_factor

        # Entry rotation index: how much the diff allows rotation on entry
        # Higher coast lock → less rotation (more stable)
        # 50% coast lock is neutral — more = stable (less rotation), less = rotating
        entry_rotation_index = (50.0 - lock_pct_coast) / 100.0

        lock_reasoning = (
            f"Coast: {lock_pct_coast:.1f}% locked ({clutch_plates} plates × "
            f"{CLUTCH_TORQUE_PER_PLATE:.0f} Nm/plate, ramp={coast_ramp}°, "
            f"preload={preload_nm:.0f} Nm); "
            f"Drive: {lock_pct_drive:.1f}% locked (ramp={drive_ramp}°)"
        )

        return DiffSolution(
            lock_pct_coast=round(lock_pct_coast, 1),
            lock_pct_drive=round(lock_pct_drive, 1),
            preload_nm=round(preload_nm / 5) * 5,  # iRacing garage: 5 Nm increments
            coast_ramp_deg=coast_ramp,
            drive_ramp_deg=drive_ramp,
            clutch_plates=clutch_plates,
            exit_understeer_index=round(exit_understeer_index, 3),
            entry_rotation_index=round(entry_rotation_index, 3),
            effective_lock_coast_torque_nm=round(effective_lock_coast_torque, 1),
            effective_lock_drive_torque_nm=round(effective_lock_drive_torque, 1),
            preload_reasoning=preload_reasoning,
            ramp_reasoning=ramp_reasoning,
            lock_reasoning=lock_reasoning,
        )

    def _compute_preload(
        self,
        driver: "DriverProfile",
        measured: "MeasuredState",
        track: "TrackProfile | None",
    ) -> tuple[float, str]:
        """Compute preload from a rear-axle load-transfer proxy and driver style.

        This is an empirical preload baseline informed by corner-exit demand and
        measured traction behaviour. It is not a complete clutch-diff torque model.
        """
        car = self.car

        # Peak lateral g from track profile or measured, with fallback
        if track is not None and track.peak_lat_g > 0:
            peak_lat_g = track.peak_lat_g
        elif measured is not None and hasattr(measured, "peak_lat_g_p99") and measured.peak_lat_g_p99 > 0:
            peak_lat_g = measured.peak_lat_g_p99
        else:
            peak_lat_g = 2.0  # GTP typical

        mass = car.total_mass(89.0)  # use full fuel as conservative case
        track_width_m = car.corner_spring.track_width_mm / 1000.0

        # Coarse corner-exit rear load-transfer proxy (N). This is used only to
        # scale the baseline preload into a plausible GTP range.
        lateral_load_transfer_n = mass * peak_lat_g * 9.81 * track_width_m / (2.0 * car.wheelbase_m)

        # Minimum preload to maintain controlled slip on the inside wheel:
        #   preload_min ≈ load_transfer * slip_fraction * coupling_factor
        # Where slip_fraction (~0.01-0.02) represents the fraction of tyre slip
        # that the diff must control, and coupling_factor is the preload-to-torque ratio.
        # Empirically calibrated for GTP: 10-15 Nm baseline covers typical cornering.
        preload_min = lateral_load_transfer_n * 0.002  # yields ~5-15 Nm for GTP
        preload_min = max(preload_min, 0.0)  # absolute minimum

        # Baseline preload: 12 Nm gives neutral rotation for most GTP drivers
        preload = max(preload_min, 12.0)
        reasons = [
            f"Base: {preload:.1f} Nm (lat transfer={lateral_load_transfer_n:.0f}N "
            f"at peak lat_g={peak_lat_g:.2f}g)"
        ]

        # Driver throttle style adjustment
        if driver.throttle_classification == "binary":
            preload += 10.0
            reasons.append("+10 Nm for binary throttle (traction protection)")
        elif driver.throttle_classification == "progressive":
            preload -= 3.0
            reasons.append("-3 Nm for progressive throttle (allows rotation)")

        # Body slip → more diff locking to reduce oversteer
        if measured.body_slip_p95_deg > 4.0:
            preload += 5.0
            reasons.append(f"+5 Nm for body slip p95={measured.body_slip_p95_deg:.1f} deg")

        # Deep trail braker → less preload (needs rotation on entry)
        if driver.trail_brake_classification == "deep":
            preload -= 5.0
            reasons.append("-5 Nm for deep trail braking (rotation on coast)")

        rear_power_slip = (
            measured.rear_power_slip_ratio_p95
            if measured.rear_power_slip_ratio_p95 > 0
            else measured.rear_slip_ratio_p95
        )
        if rear_power_slip > 0.05:
            preload += 5.0
            reasons.append(
                f"+5 Nm for rear power slip p95={rear_power_slip:.3f}"
            )

        preload = round(_clamp(preload, 0.0, 150.0), 0)
        return preload, "; ".join(reasons)

    def _compute_ramps(
        self,
        driver: "DriverProfile",
    ) -> tuple[int, int, str]:
        """Compute coast and drive ramp angles from driver style.

        Coast ramp:
          - Deep trail braker → 40 deg (more coast locking = stable entry)
          - Light trail braker → 50 deg (less coast locking = rotation on entry)

        Drive ramp:
          - Progressive throttle → 75 deg (less drive locking = natural rotation)
          - Binary throttle → 65 deg (more drive locking = traction protection)
        """
        reasons = []

        # ── Coast ramp ──
        if driver.trail_brake_classification == "deep":
            coast_ramp = 40
            reasons.append("Coast 40 deg (deep trail braker, more entry lock = stability)")
        elif driver.trail_brake_classification == "light":
            coast_ramp = 50
            reasons.append("Coast 50 deg (light trail braker, less entry lock = rotation)")
        else:
            # Interpolate from trail brake depth (0.0=none, 1.0=maximum)
            depth = getattr(driver, "trail_brake_depth_mean", 0.3)
            raw = int(50 - depth * 10)
            coast_ramp = _snap_to_options(raw, COAST_RAMP_OPTIONS)
            reasons.append(
                f"Coast {coast_ramp} deg (from trail brake depth={depth:.2f})"
            )

        # ── Drive ramp ──
        if driver.throttle_classification == "progressive":
            drive_ramp = 75
            reasons.append(
                "Drive 75 deg (progressive throttle, less exit lock = rotation)"
            )
        elif driver.throttle_classification == "binary":
            drive_ramp = 65
            reasons.append(
                "Drive 65 deg (binary throttle, more exit lock = wheelspin protection)"
            )
        else:
            prog = getattr(driver, "throttle_progressiveness", 0.5)  # R², 0-1
            raw_drive = int(65 + prog * 10)
            drive_ramp = _snap_to_options(raw_drive, DRIVE_RAMP_OPTIONS)
            reasons.append(
                f"Drive {drive_ramp} deg (from throttle R^2={prog:.2f})"
            )

        # Throttle onset rate: fast onset → more abrupt power → open diff more
        onset_rate = getattr(driver, "throttle_onset_rate_pct_per_s", 0.0)
        if onset_rate > 300:
            drive_ramp = _snap_to_options(
                min(drive_ramp + 5, DRIVE_RAMP_OPTIONS[-1]), DRIVE_RAMP_OPTIONS
            )
            reasons.append(f"Drive +5 deg for fast throttle onset {onset_rate:.0f}%/s")

        return coast_ramp, drive_ramp, "; ".join(reasons)

    @staticmethod
    def _lock_pct(
        preload: float,
        n_plates: int,
        ramp_deg: int,
        torque_input: float,
    ) -> float:
        """Compute differential lock percentage.

        Formula:
            lock_torque = preload + (n_plates × CLUTCH_TORQUE_PER_PLATE) / tan(ramp_angle)
            lock_pct = min(100, lock_torque / torque_input × 100)

        Note: lower ramp_deg → larger 1/tan() → more locking.
        """
        ramp_rad = math.radians(ramp_deg)
        lock_torque = preload + (n_plates * CLUTCH_TORQUE_PER_PLATE) / math.tan(ramp_rad)
        return min(100.0, (lock_torque / max(torque_input, 1.0)) * 100.0)

    @staticmethod
    def compute_effective_lock(
        preload_nm: float,
        n_plates: int,
        ramp_deg: int,
        torque_input_nm: float = 490.0,
    ) -> float:
        """Public utility: compute effective diff lock % for any configuration.

        Useful for comparing setups or exploring parameter space.

        Args:
            preload_nm: Diff preload in Nm
            n_plates: Number of clutch plates
            ramp_deg: Ramp angle in degrees
            torque_input_nm: Input torque (default: 70% of 700 Nm)

        Returns:
            Lock percentage (0-100)
        """
        return DiffSolver._lock_pct(preload_nm, n_plates, ramp_deg, torque_input_nm)

    @staticmethod
    def compare_diff_configs(
        config_a: dict,
        config_b: dict,
        torque_nm: float = 490.0,
    ) -> dict:
        """Compare two diff configurations and return lock % differences.

        Each config dict should have: preload_nm, n_plates, coast_ramp_deg, drive_ramp_deg

        Returns dict with coast_lock_a/b, drive_lock_a/b, and deltas.
        """
        coast_a = DiffSolver._lock_pct(
            config_a.get("preload_nm", 10), config_a.get("n_plates", 6),
            config_a.get("coast_ramp_deg", 45), torque_nm,
        )
        coast_b = DiffSolver._lock_pct(
            config_b.get("preload_nm", 10), config_b.get("n_plates", 6),
            config_b.get("coast_ramp_deg", 45), torque_nm,
        )
        drive_a = DiffSolver._lock_pct(
            config_a.get("preload_nm", 10), config_a.get("n_plates", 6),
            config_a.get("drive_ramp_deg", 70), torque_nm,
        )
        drive_b = DiffSolver._lock_pct(
            config_b.get("preload_nm", 10), config_b.get("n_plates", 6),
            config_b.get("drive_ramp_deg", 70), torque_nm,
        )
        return {
            "coast_lock_a": round(coast_a, 1),
            "coast_lock_b": round(coast_b, 1),
            "drive_lock_a": round(drive_a, 1),
            "drive_lock_b": round(drive_b, 1),
            "coast_delta": round(coast_b - coast_a, 1),
            "drive_delta": round(drive_b - drive_a, 1),
        }
