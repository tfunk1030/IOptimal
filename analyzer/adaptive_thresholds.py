"""Adaptive diagnostic thresholds — track/car/driver-scaled.

Instead of static thresholds (understeer > 2.5°, RH variance > 8mm, etc.),
this module computes thresholds that adapt to:
- Track characteristics (bumpy Sebring vs smooth Monza)
- Car-specific baselines (different nominal handling)
- Driver style (smooth drivers feel smaller differences)
- Speed regime (high-speed understeer is more dangerous)

Usage:
    thresholds = compute_adaptive_thresholds(track, car, driver)
    diagnosis = diagnose(measured, setup, car, thresholds=thresholds)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analyzer.driver_style import DriverProfile
    from car_model.cars import CarModel
    from track_model.profile import TrackProfile


# ── Baseline thresholds (same as current diagnose.py) ──────────────────

BASELINE_THRESHOLDS = {
    # Platform
    "front_rh_variance_mm": 8.0,
    "rear_rh_variance_mm": 10.0,
    "excursion_pct": 80.0,
    # Balance
    "understeer_all_deg": 2.5,
    "understeer_low_deg": 3.0,
    "understeer_mid_deg": 2.5,
    "understeer_high_deg": 2.0,
    "oversteer_deg": -0.5,
    "speed_gradient_deg": 1.5,
    "lltd_high_delta": 0.08,
    "lltd_low_delta": -0.02,
    "body_slip_p95_deg": 4.0,
    # Safety
    "bottoming_events_front": 5,
    "bottoming_events_rear": 5,
    # Damper
    "settle_time_upper_ms": 200.0,
    "settle_time_lower_ms": 50.0,
    "yaw_correlation_r2": 0.65,
    "roll_rate_p95_deg_per_s": 25.0,
    # Thermal
    "temp_spread_c": 8.0,
    "carcass_upper_c": 105.0,
    "carcass_lower_c": 80.0,
    "pressure_upper_kpa": 175.0,
    "pressure_lower_kpa": 155.0,
    # Grip
    "rear_slip_ratio_p95": 0.08,
    "front_slip_ratio_p95": 0.06,
}

# ── Baseline surface severity reference ────────────────────────────────
# Shock velocity p99 reference: Sebring = 260 mm/s (front), "typical" baseline
BASELINE_SURFACE_SEVERITY_MPS = 0.200  # 200 mm/s as moderate baseline


@dataclass
class AdaptiveThresholds:
    """Adapted thresholds for a specific track/car/driver combination."""

    # Platform
    front_rh_variance_mm: float = 8.0
    rear_rh_variance_mm: float = 10.0
    excursion_pct: float = 80.0

    # Balance (speed-specific)
    understeer_all_deg: float = 2.5
    understeer_low_deg: float = 3.0
    understeer_mid_deg: float = 2.5
    understeer_high_deg: float = 2.0
    oversteer_deg: float = -0.5
    speed_gradient_deg: float = 1.5
    lltd_high_delta: float = 0.08
    lltd_low_delta: float = -0.02
    body_slip_p95_deg: float = 4.0

    # Safety
    bottoming_events_front: int = 5
    bottoming_events_rear: int = 5

    # Damper
    settle_time_upper_ms: float = 200.0
    settle_time_lower_ms: float = 50.0
    yaw_correlation_r2: float = 0.65
    roll_rate_p95_deg_per_s: float = 25.0

    # Thermal
    temp_spread_c: float = 8.0
    carcass_upper_c: float = 105.0
    carcass_lower_c: float = 80.0
    pressure_upper_kpa: float = 175.0
    pressure_lower_kpa: float = 155.0

    # Grip
    rear_slip_ratio_p95: float = 0.08
    front_slip_ratio_p95: float = 0.06

    # Scaling factors applied (for reporting)
    track_scale: float = 1.0
    driver_scale: float = 1.0
    adaptations: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = ["Adaptive Thresholds:"]
        lines.append(f"  Track scale: {self.track_scale:.2f}")
        lines.append(f"  Driver scale: {self.driver_scale:.2f}")
        if self.adaptations:
            for a in self.adaptations:
                lines.append(f"  - {a}")
        return "\n".join(lines)


# ── Track-adaptive scaling ─────────────────────────────────────────────

# Per-threshold sensitivity to surface severity
# α > 0 means threshold relaxes as surface gets rougher
TRACK_SENSITIVITY = {
    "front_rh_variance_mm": 0.30,
    "rear_rh_variance_mm": 0.30,
    "excursion_pct": 0.15,
    "bottoming_events_front": 0.50,
    "bottoming_events_rear": 0.50,
    "settle_time_upper_ms": 0.20,
    "roll_rate_p95_deg_per_s": 0.15,
}


def _track_scale_factor(
    shock_vel_p99_front_mps: float,
    baseline_severity: float = BASELINE_SURFACE_SEVERITY_MPS,
) -> float:
    """Compute track-adaptive scale factor from surface severity.

    Returns >1.0 for bumpy tracks (relax thresholds),
    <1.0 for smooth tracks (tighten thresholds).
    """
    if baseline_severity <= 0:
        return 1.0
    # Clamp to prevent extreme scaling
    severity_ratio = shock_vel_p99_front_mps / baseline_severity
    return max(0.7, min(1.5, severity_ratio))


# ── Driver-adaptive scaling ────────────────────────────────────────────

def _driver_scale_factor(driver: DriverProfile) -> float:
    """Compute driver-adaptive scale factor.

    Smooth-consistent drivers feel smaller issues → tighten thresholds.
    Aggressive-erratic drivers mask issues with input noise → relax.
    """
    scale = 1.0

    if driver.steering_smoothness == "smooth" and driver.consistency == "consistent":
        scale *= 0.85  # tighten — they can feel small differences
    elif driver.steering_smoothness == "aggressive" and driver.consistency == "erratic":
        scale *= 1.20  # relax — noise in inputs masks subtle issues
    elif driver.steering_smoothness == "aggressive":
        scale *= 1.10
    elif driver.consistency == "erratic":
        scale *= 1.10

    return scale


# ── Car-specific baselines ─────────────────────────────────────────────

# Per-car nominal handling baselines (from per-car-quirks.md and telemetry)
CAR_BASELINES = {
    "bmw": {
        "understeer_nominal_deg": 1.5,
        "body_slip_nominal_deg": 2.5,
        "notes": "Neutral baseline, RARB primary balance lever",
    },
    "ferrari": {
        "understeer_nominal_deg": 2.0,
        "body_slip_nominal_deg": 2.0,
        "notes": "Off-throttle understeer below 190 kph is inherent (hybrid cutoff)",
    },
    "porsche": {
        "understeer_nominal_deg": 2.0,
        "body_slip_nominal_deg": 2.0,
        "notes": "Aero-dominant, slow-corner understeer is inherent",
    },
    "cadillac": {
        "understeer_nominal_deg": 1.5,
        "body_slip_nominal_deg": 2.0,
        "notes": "Most forgiving, best all-rounder",
    },
    "acura": {
        "understeer_nominal_deg": 1.5,
        "body_slip_nominal_deg": 3.0,
        "notes": "Diff preload is THE setup parameter, narrow tolerance",
    },
}

# Default baseline for unknown cars
DEFAULT_CAR_BASELINE = {
    "understeer_nominal_deg": 1.5,
    "body_slip_nominal_deg": 2.5,
}


# ── Main computation ───────────────────────────────────────────────────

def compute_adaptive_thresholds(
    track: TrackProfile,
    car: CarModel,
    driver: DriverProfile | None = None,
) -> AdaptiveThresholds:
    """Compute adaptive thresholds for a specific track/car/driver.

    Args:
        track: Track profile with surface characteristics
        car: Car model
        driver: Driver profile (optional; defaults to moderate-consistent)

    Returns:
        AdaptiveThresholds with adjusted values and explanation
    """
    thresholds = AdaptiveThresholds()
    adaptations = []

    # ── Track scaling ──
    track_scale = _track_scale_factor(track.shock_vel_p99_front_mps)
    thresholds.track_scale = track_scale

    if track_scale != 1.0:
        direction = "rougher" if track_scale > 1.0 else "smoother"
        adaptations.append(
            f"Track surface {direction} than baseline "
            f"(shock p99={track.shock_vel_p99_front_mps*1000:.0f} mm/s, "
            f"scale={track_scale:.2f})"
        )

    # Apply track scaling to relevant thresholds
    for param, alpha in TRACK_SENSITIVITY.items():
        base = BASELINE_THRESHOLDS[param]
        adjusted = base * (1.0 + alpha * (track_scale - 1.0))
        setattr(thresholds, param, adjusted if isinstance(base, float) else int(round(adjusted)))

    # ── Driver scaling ──
    if driver is not None:
        driver_scale = _driver_scale_factor(driver)
        thresholds.driver_scale = driver_scale

        if driver_scale != 1.0:
            adaptations.append(
                f"Driver style {driver.style}: "
                f"threshold scale={driver_scale:.2f}"
            )

        # Apply driver scaling to handling thresholds
        thresholds.understeer_all_deg *= driver_scale
        thresholds.understeer_low_deg *= driver_scale
        thresholds.understeer_mid_deg *= driver_scale
        thresholds.understeer_high_deg *= driver_scale
        thresholds.body_slip_p95_deg *= driver_scale
        thresholds.settle_time_upper_ms *= driver_scale

        # Aggressive drivers need faster settle time
        if driver.steering_smoothness == "aggressive":
            thresholds.settle_time_upper_ms *= 0.85
            adaptations.append(
                "Aggressive steering → tighter settle time target"
            )
    else:
        thresholds.driver_scale = 1.0

    # ── Car-specific baselines ──
    car_key = car.canonical_name.lower()
    baseline = CAR_BASELINES.get(car_key, DEFAULT_CAR_BASELINE)

    # Adjust understeer threshold based on car's nominal behavior
    nominal_us = baseline.get("understeer_nominal_deg", 1.5)
    allowable_deviation = 1.5  # degrees above nominal before it's a problem
    car_adjusted_us = nominal_us + allowable_deviation
    if abs(car_adjusted_us - thresholds.understeer_all_deg) > 0.3:
        thresholds.understeer_all_deg = car_adjusted_us
        adaptations.append(
            f"Car baseline understeer {nominal_us:.1f}° → "
            f"threshold set to {car_adjusted_us:.1f}°"
        )

    # Speed-specific understeer: high-speed is more dangerous
    thresholds.understeer_low_deg = car_adjusted_us + 0.5   # more lenient at low speed
    thresholds.understeer_mid_deg = car_adjusted_us
    thresholds.understeer_high_deg = car_adjusted_us - 0.5  # stricter at high speed

    # Car-specific body slip baseline
    nominal_bs = baseline.get("body_slip_nominal_deg", 2.5)
    thresholds.body_slip_p95_deg = nominal_bs + 2.0  # allow 2° above nominal

    thresholds.adaptations = adaptations
    return thresholds
