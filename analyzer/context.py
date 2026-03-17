from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from analyzer.telemetry_truth import get_signal

if TYPE_CHECKING:
    from analyzer.diagnose import Diagnosis
    from analyzer.extract import MeasuredState
    from analyzer.setup_reader import CurrentSetup


@dataclass
class SessionContext:
    fuel_l: float | None
    tyre_state: str
    thermal_validity: float
    pace_validity: float
    traffic_confidence: float
    weather_confidence: float
    comparable_to_baseline: bool
    notes: list[str] = field(default_factory=list)

    @property
    def overall_score(self) -> float:
        score = (
            self.thermal_validity * 0.35
            + self.pace_validity * 0.35
            + self.traffic_confidence * 0.15
            + self.weather_confidence * 0.15
        )
        if not self.comparable_to_baseline:
            score *= 0.75
        return round(score, 3)


def _mean_signal_confidence(measured: "MeasuredState", names: list[str]) -> float:
    scores: list[float] = []
    for name in names:
        sig = get_signal(measured, name)
        if sig.value is not None:
            scores.append(sig.confidence)
    return round(sum(scores) / len(scores), 3) if scores else 0.0


def build_session_context(
    measured: "MeasuredState",
    setup: "CurrentSetup",
    diagnosis: "Diagnosis",
) -> SessionContext:
    notes: list[str] = []

    front_temp = getattr(measured, "front_carcass_mean_c", 0.0)
    rear_temp = getattr(measured, "rear_carcass_mean_c", 0.0)
    temps = [t for t in (front_temp, rear_temp) if t > 0]
    if not temps:
        tyre_state = "unknown"
        notes.append("tyre thermal state unavailable")
    else:
        mean_temp = sum(temps) / len(temps)
        if mean_temp > 105.0:
            tyre_state = "overheated"
        elif mean_temp >= 85.0:
            tyre_state = "in_window"
        elif mean_temp >= 70.0:
            tyre_state = "warming"
        else:
            tyre_state = "cold"

    front_pressure = getattr(measured, "front_pressure_mean_kpa", 0.0)
    rear_pressure = getattr(measured, "rear_pressure_mean_kpa", 0.0)
    pressure_terms: list[float] = []
    for pressure in (front_pressure, rear_pressure):
        if pressure > 0:
            pressure_terms.append(max(0.0, 1.0 - abs(pressure - 165.0) / 20.0))
    temp_terms: list[float] = []
    for temp in temps:
        temp_terms.append(max(0.0, 1.0 - abs(temp - 92.5) / 20.0))
    thermal_signal = _mean_signal_confidence(
        measured,
        ["front_carcass_mean_c", "rear_carcass_mean_c", "front_pressure_mean_kpa", "rear_pressure_mean_kpa"],
    )
    base_thermal = (sum(temp_terms) + sum(pressure_terms)) / max(len(temp_terms) + len(pressure_terms), 1)
    thermal_validity = round(min(1.0, base_thermal * 0.75 + thermal_signal * 0.25), 3) if (temp_terms or pressure_terms) else 0.4

    # Warm-up discount: early laps have settling tyres, cannot be trusted as baseline authority.
    # lap1=60%, lap2=70%, lap3=80%, lap4=90%, lap5+=100%
    lap_number = getattr(measured, "lap_number", 0) or 0
    if 0 < lap_number < 5:
        warmup_factor = min(1.0, 0.5 + lap_number * 0.10)
        thermal_validity = round(thermal_validity * warmup_factor, 3)
        notes.append(f"Warm-up lap {lap_number}: thermal validity discounted (×{warmup_factor:.2f})")

    if thermal_validity < 0.55:
        notes.append("thermal state is weak for fair comparison")

    assessment_scores = {
        "fast": 1.0,
        "competitive": 0.8,
        "compromised": 0.45,
        "dangerous": 0.15,
    }
    critical_count = sum(1 for p in diagnosis.problems if p.severity == "critical")
    significant_count = sum(1 for p in diagnosis.problems if p.severity == "significant")
    pace_validity = assessment_scores.get(diagnosis.assessment, 0.6)
    pace_validity -= min(0.25, critical_count * 0.15 + significant_count * 0.05)
    pace_validity = round(max(0.0, pace_validity), 3)
    if critical_count:
        notes.append(f"{critical_count} critical handling problems reduce pace authority")

    weather_signal = _mean_signal_confidence(measured, ["front_rh_std_mm", "rear_rh_std_mm"])
    weather_valid_inputs = sum(
        1
        for value in (
            getattr(measured, "air_temp_c", 0.0),
            getattr(measured, "track_temp_c", 0.0),
            getattr(measured, "wind_speed_ms", 0.0),
        )
        if value != 0.0
    )
    weather_confidence = round(min(1.0, 0.35 + weather_valid_inputs * 0.18 + weather_signal * 0.2), 3)
    if weather_valid_inputs < 2:
        notes.append("weather context partially missing")

    traffic_confidence = 0.7
    if getattr(measured, "brake_bias_adjustments", 0) > 8 or getattr(measured, "tc_adjustments", 0) > 8:
        traffic_confidence = 0.55
        notes.append("heavy in-car adjustment activity reduces comparability confidence")
    elif getattr(measured, "yaw_rate_correlation", 0.0) > 0.85:
        traffic_confidence = 0.8

    # Comparability requires: reasonable thermal window, pace, weather,
    # plus tyre state must not be cold (pre-warm) or overheated (degraded),
    # and lap must be past the settling phase (lap >= 3 if known).
    tyre_not_valid = tyre_state in ("cold", "overheated")
    lap_too_early = 0 < lap_number < 3
    comparable = (
        thermal_validity >= 0.45
        and pace_validity >= 0.45
        and weather_confidence >= 0.45
        and not tyre_not_valid
        and not lap_too_early
    )
    if tyre_not_valid:
        notes.append(f"Tyre state '{tyre_state}' disqualifies session as baseline authority")
    if lap_too_early:
        notes.append(f"Lap {lap_number} is too early (settle phase); not a valid baseline")
    if not comparable:
        notes.append("session is not a clean baseline authority candidate")

    return SessionContext(
        fuel_l=setup.fuel_l or getattr(measured, "fuel_level_at_measurement_l", 0.0) or None,
        tyre_state=tyre_state,
        thermal_validity=thermal_validity,
        pace_validity=pace_validity,
        traffic_confidence=round(traffic_confidence, 3),
        weather_confidence=weather_confidence,
        comparable_to_baseline=comparable,
        notes=notes,
    )
