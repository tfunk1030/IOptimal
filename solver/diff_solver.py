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
from dataclasses import dataclass, field
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


@dataclass
class DiffSolution:
    """Computed differential setup recommendation."""

    # Computed values
    lock_pct_coast: float       # % locked under coast/braking (0-100)
    lock_pct_drive: float       # % locked under drive/accel (0-100)
    preload_contribution_pct: float
    plate_contribution_pct: float
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

    # F2 provenance: per-parameter search status. Values are one of
    #   "physics_formula"           — emitted from the physics computation
    #   "fallback_preserve_driver"  — physics had no signal AND no fitted
    #                                 model existed; driver value used as
    #                                 last-resort fallback (with [FALLBACK]
    #                                 logged).
    parameter_search_status: dict[str, str] = field(default_factory=dict)

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
            f"  Lock contributions: preload={self.preload_contribution_pct:.1f}%  plates={self.plate_contribution_pct:.1f}%",
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
        current_clutch_plates: int | None = None,
        current_coast_ramp_deg: int | None = None,
        current_drive_ramp_deg: int | None = None,
        current_preload_nm: float | None = None,
    ) -> DiffSolution:
        """Compute differential setup recommendation.

        Per Unit F2 the diff solver emits the **physics-computed** preload
        and ramp values directly. The previous "if within 8 Nm of driver,
        prefer driver" anchor was removed — it was a Type-B/F preserve-driver
        fallback that masked physics signals. Driver values are still
        accepted as a last-resort fallback when the physics computation
        cannot run (e.g. missing required driver/measured data); the
        provenance label `fallback_preserve_driver` is set in that case.

        Args:
            driver: Driver behavior profile (throttle style, trail braking)
            measured: Measured telemetry state (body slip, slip ratios)
            track: Track demand profile (lateral g, corner speeds) — optional
            current_*: Driver-loaded current values. Used ONLY as a
                last-resort fallback when physics cannot compute a value.

        Returns:
            DiffSolution with recommended preload, ramps, and full reasoning
        """
        # ── Preload — physics-first ──
        preload_status: str
        preload_nm_raw, preload_reasoning = self._compute_preload(driver, measured, track)
        if math.isfinite(preload_nm_raw) and preload_nm_raw >= 0.0:
            preload_nm = round(preload_nm_raw / 5) * 5  # iRacing garage: 5 Nm increments
            preload_status = "physics_formula"
            preload_reasoning += " [confidence: medium — physics formula from car defaults + driver profile]"
        elif current_preload_nm is not None and float(current_preload_nm) > 0:
            preload_nm = round(float(current_preload_nm) / 5) * 5
            preload_status = "fallback_preserve_driver"
            preload_reasoning = (
                f"[FALLBACK — driver value preserved due to insufficient calibration] "
                f"physics returned no value; using driver-loaded {preload_nm:.0f} Nm"
            )
            import logging
            logging.getLogger(__name__).warning(
                "[FALLBACK] diff preload: physics formula unavailable — preserving "
                "driver-loaded %.0f Nm.", preload_nm,
            )
        else:
            preload_nm = round(float(self.car.default_diff_preload_nm) / 5) * 5
            preload_status = "physics_formula"
            preload_reasoning = (
                f"[confidence: low] Defaulted to car baseline {preload_nm:.0f} Nm "
                f"— no measured/driver inputs available."
            )

        # ── Coast / Drive ramps — physics-first ──
        coast_ramp, drive_ramp, ramp_reasoning = self._compute_ramps(driver)
        ramp_reasoning += " [confidence: medium — physics-driven from driver style]"

        # If physics couldn't pick legal ramps, fall back to driver values.
        coast_status = "physics_formula"
        drive_status = "physics_formula"
        if coast_ramp not in COAST_RAMP_OPTIONS:
            if (current_coast_ramp_deg is not None
                    and int(current_coast_ramp_deg) in COAST_RAMP_OPTIONS):
                coast_ramp = int(current_coast_ramp_deg)
                coast_status = "fallback_preserve_driver"
                ramp_reasoning += (
                    f"; [FALLBACK — coast preserved at driver={coast_ramp}]"
                )
                import logging
                logging.getLogger(__name__).warning(
                    "[FALLBACK] coast ramp: physics emitted illegal value, "
                    "preserving driver-loaded %d deg.", coast_ramp,
                )
            else:
                coast_ramp = COAST_RAMP_OPTIONS[0]
        if drive_ramp not in DRIVE_RAMP_OPTIONS:
            if (current_drive_ramp_deg is not None
                    and int(current_drive_ramp_deg) in DRIVE_RAMP_OPTIONS):
                drive_ramp = int(current_drive_ramp_deg)
                drive_status = "fallback_preserve_driver"
                ramp_reasoning += (
                    f"; [FALLBACK — drive preserved at driver={drive_ramp}]"
                )
                import logging
                logging.getLogger(__name__).warning(
                    "[FALLBACK] drive ramp: physics emitted illegal value, "
                    "preserving driver-loaded %d deg.", drive_ramp,
                )
            else:
                drive_ramp = DRIVE_RAMP_OPTIONS[0]

        default_plates = getattr(self.car, 'default_clutch_plates', BMW_DEFAULT_CLUTCH_PLATES)
        clutch_plates = current_clutch_plates or default_plates
        torque_input = self.max_torque_nm * 0.7  # typical cornering torque

        clutch_torque = getattr(self.car, 'clutch_torque_per_plate', CLUTCH_TORQUE_PER_PLATE)
        lock_pct_coast = self._lock_pct(preload_nm, clutch_plates, coast_ramp, torque_input, clutch_torque)
        lock_pct_drive = self._lock_pct(preload_nm, clutch_plates, drive_ramp, torque_input, clutch_torque)

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

        preload_component = self._lock_pct(preload_nm, 0, coast_ramp, torque_input)
        plate_component = max(0.0, lock_pct_coast - preload_component)

        return DiffSolution(
            lock_pct_coast=round(lock_pct_coast, 1),
            lock_pct_drive=round(lock_pct_drive, 1),
            preload_contribution_pct=round(preload_component, 1),
            plate_contribution_pct=round(plate_component, 1),
            preload_nm=preload_nm,
            coast_ramp_deg=coast_ramp,
            drive_ramp_deg=drive_ramp,
            clutch_plates=clutch_plates,
            exit_understeer_index=round(exit_understeer_index, 3),
            entry_rotation_index=round(entry_rotation_index, 3),
            parameter_search_status={
                "diff_preload_nm": preload_status,
                "diff_ramp_coast": coast_status,
                "diff_ramp_drive": drive_status,
            },
            preload_reasoning=(
                f"{preload_reasoning}; clutch plates={clutch_plates}"
                if current_clutch_plates is not None
                else preload_reasoning
            ),
            ramp_reasoning=ramp_reasoning,
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
        elif measured is not None and hasattr(measured, "peak_lat_g_measured") and measured.peak_lat_g_measured > 0:
            peak_lat_g = measured.peak_lat_g_measured
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

        # Baseline preload: per-car operating-point default (12 Nm BMW, 85 Nm Porsche).
        # Was a flat 12 Nm — that's a BMW-tuned baseline that produces 30 Nm output for
        # cars where the driver-validated operating point is 75-100 Nm (e.g., Porsche).
        car_default_preload = float(car.default_diff_preload_nm)
        preload = max(preload_min, car_default_preload)
        reasons = [
            f"Base: {preload:.1f} Nm (car default {car_default_preload:.0f} Nm; "
            f"lat transfer={lateral_load_transfer_n:.0f}N at peak lat_g={peak_lat_g:.2f}g)"
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
        clutch_torque_per_plate: float = CLUTCH_TORQUE_PER_PLATE,
    ) -> float:
        """Compute differential lock percentage.

        Formula:
            lock_torque = preload + (n_plates x clutch_torque_per_plate) / tan(ramp_angle)
            lock_pct = min(100, lock_torque / torque_input x 100)

        Note: lower ramp_deg -> larger 1/tan() -> more locking.
        """
        ramp_rad = math.radians(ramp_deg)
        lock_torque = preload + (n_plates * clutch_torque_per_plate) / math.tan(ramp_rad)
        return min(100.0, (lock_torque / max(torque_input, 1.0)) * 100.0)
