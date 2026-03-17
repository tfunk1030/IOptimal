from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from analyzer.extract import MeasuredState
    from pipeline.reason import SessionSnapshot


T = TypeVar("T")


@dataclass
class TelemetrySignal(Generic[T]):
    value: T | None = None
    quality: str = "unknown"  # trusted | proxy | broken | unknown
    confidence: float = 0.0
    source: str = ""
    invalid_reason: str = ""
    conflict_state: str = "clear"  # clear | conflicted
    retry_attempts: int = 1

    def usable(self, *, allow_proxy: bool = False) -> bool:
        if self.conflict_state != "clear":
            return False
        if self.quality == "trusted":
            return self.value is not None
        if allow_proxy and self.quality == "proxy":
            return self.value is not None
        return False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TelemetryBundle:
    aero_platform: dict[str, TelemetrySignal[Any]] = field(default_factory=dict)
    braking_platform: dict[str, TelemetrySignal[Any]] = field(default_factory=dict)
    traction_balance: dict[str, TelemetrySignal[Any]] = field(default_factory=dict)
    kerb_compliance: dict[str, TelemetrySignal[Any]] = field(default_factory=dict)
    tyre_support: dict[str, TelemetrySignal[Any]] = field(default_factory=dict)
    driver_inputs: dict[str, TelemetrySignal[Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, dict[str, dict[str, Any]]]:
        return {
            "aero_platform": signals_to_dict(self.aero_platform),
            "braking_platform": signals_to_dict(self.braking_platform),
            "traction_balance": signals_to_dict(self.traction_balance),
            "kerb_compliance": signals_to_dict(self.kerb_compliance),
            "tyre_support": signals_to_dict(self.tyre_support),
            "driver_inputs": signals_to_dict(self.driver_inputs),
        }


@dataclass
class SessionNormalization:
    fuel_delta_l: float = 0.0
    air_temp_delta_c: float = 0.0
    track_temp_delta_c: float = 0.0
    wind_delta_ms: float = 0.0
    tyre_state_delta: float = 0.0
    fuel_score: float = 1.0
    weather_score: float = 1.0
    track_state_score: float = 1.0
    lap_cleanliness_score: float = 1.0
    traffic_score: float = 1.0
    tyre_state_score: float = 1.0
    overall_score: float = 1.0
    comparable: bool = True
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParameterEvidence:
    telemetry: list[str] = field(default_factory=list)
    historical: list[str] = field(default_factory=list)
    physics_rationale: str = ""
    legality: str = ""
    expected_gain_ms: float = 0.0
    expected_cost_ms: float = 0.0
    confidence: float = 0.0
    source_tier: str = "telemetry"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParameterDecision:
    parameter: str
    current_value: float | int | str | None
    proposed_value: float | int | str | None
    unit: str = ""
    confidence: float = 0.0
    legality_status: str = "validated"
    blocked_reason: str = ""
    fallback_reason: str = ""
    evidence: ParameterEvidence = field(default_factory=ParameterEvidence)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = self.evidence.to_dict()
        return data


def signal_to_dict(signal: TelemetrySignal[Any] | None) -> dict[str, Any] | None:
    if signal is None:
        return None
    return signal.to_dict()


def signals_to_dict(signals: dict[str, TelemetrySignal[Any]]) -> dict[str, dict[str, Any]]:
    return {name: signal.to_dict() for name, signal in signals.items()}


def get_signal(measured: Any, name: str) -> TelemetrySignal[Any]:
    signal_map = getattr(measured, "telemetry_signals", {}) or {}
    signal = signal_map.get(name)
    if isinstance(signal, TelemetrySignal):
        return signal

    value = getattr(measured, name, None)
    quality = "trusted" if value not in (None, 0, 0.0, "") else "unknown"
    confidence = 0.8 if quality == "trusted" else 0.0
    return TelemetrySignal(
        value=value,
        quality=quality,
        confidence=confidence,
        source="legacy_measured_state",
        invalid_reason="" if quality == "trusted" else "not_extracted",
    )


def usable_signal_value(measured: Any, name: str, *, allow_proxy: bool = False) -> float | None:
    signal = get_signal(measured, name)
    if not signal.usable(allow_proxy=allow_proxy):
        return None
    try:
        return float(signal.value)
    except (TypeError, ValueError):
        return None


def build_signal_map(measured: "MeasuredState") -> dict[str, TelemetrySignal[Any]]:
    def _trusted(
        value: Any,
        *,
        source: str,
        confidence: float = 0.85,
        invalid_reason: str = "",
    ) -> TelemetrySignal[Any]:
        quality = "trusted" if value not in (None, "") else "unknown"
        if isinstance(value, (int, float)) and float(value) == 0.0 and invalid_reason:
            quality = "unknown"
            confidence_local = 0.0
        else:
            confidence_local = confidence if quality == "trusted" else 0.0
        return TelemetrySignal(
            value=value,
            quality=quality,
            confidence=confidence_local,
            source=source,
            invalid_reason=invalid_reason if quality != "trusted" else "",
        )

    def _proxy(value: Any, *, source: str, confidence: float = 0.6) -> TelemetrySignal[Any]:
        quality = "proxy" if value not in (None, "") else "unknown"
        return TelemetrySignal(
            value=value,
            quality=quality,
            confidence=confidence if quality == "proxy" else 0.0,
            source=source,
            invalid_reason="" if quality == "proxy" else "not_extracted",
        )

    front_settle_reason = getattr(measured, "front_settle_invalid_reason", "") or ""
    rear_settle_reason = getattr(measured, "rear_settle_invalid_reason", "") or ""
    front_settle_valid = getattr(measured, "front_settle_valid_clean_events", 0) >= 3
    rear_settle_valid = getattr(measured, "rear_settle_valid_clean_events", 0) >= 3

    signals: dict[str, TelemetrySignal[Any]] = {
        "mean_front_rh_at_speed_mm": _trusted(
            measured.mean_front_rh_at_speed_mm,
            source="ride_height_channels",
        ),
        "mean_rear_rh_at_speed_mm": _trusted(
            measured.mean_rear_rh_at_speed_mm,
            source="ride_height_channels",
        ),
        "front_rh_std_mm": _trusted(measured.front_rh_std_mm, source="ride_height_channels"),
        "rear_rh_std_mm": _trusted(measured.rear_rh_std_mm, source="ride_height_channels"),
        "front_rh_std_hs_mm": _trusted(measured.front_rh_std_hs_mm, source="ride_height_channels"),
        "front_shock_oscillation_hz": _trusted(
            measured.front_shock_oscillation_hz,
            source="shock_velocity_zero_crossing",
            confidence=0.8,
        ),
        "rear_shock_oscillation_hz": _trusted(
            measured.rear_shock_oscillation_hz,
            source="shock_velocity_zero_crossing",
            confidence=0.8,
        ),
        "front_heave_travel_used_pct": _trusted(
            measured.front_heave_travel_used_pct,
            source="heave_deflection_channel",
        ),
        "rear_heave_travel_used_pct": _trusted(
            measured.rear_heave_travel_used_pct,
            source="heave_deflection_channel",
        ),
        "front_braking_lock_ratio_p95": _trusted(
            measured.front_braking_lock_ratio_p95,
            source="wheel_speed_delta",
            confidence=0.82,
        ),
        "rear_power_slip_ratio_p95": _trusted(
            measured.rear_power_slip_ratio_p95,
            source="wheel_speed_delta",
            confidence=0.82,
        ),
        "bottoming_event_count_front_clean": _trusted(
            measured.bottoming_event_count_front_clean,
            source="ride_height_clean_track_filter",
        ),
        "bottoming_event_count_rear_clean": _trusted(
            measured.bottoming_event_count_rear_clean,
            source="ride_height_clean_track_filter",
        ),
        "bottoming_event_count_front_kerb": _trusted(
            measured.bottoming_event_count_front_kerb,
            source="ride_height_kerb_filter",
        ),
        "bottoming_event_count_rear_kerb": _trusted(
            measured.bottoming_event_count_rear_kerb,
            source="ride_height_kerb_filter",
        ),
        "front_pressure_mean_kpa": _trusted(measured.front_pressure_mean_kpa, source="tyre_pressure_channel"),
        "rear_pressure_mean_kpa": _trusted(measured.rear_pressure_mean_kpa, source="tyre_pressure_channel"),
        "front_carcass_mean_c": _trusted(measured.front_carcass_mean_c, source="tyre_temp_channel"),
        "rear_carcass_mean_c": _trusted(measured.rear_carcass_mean_c, source="tyre_temp_channel"),
        "splitter_rh_p01_mm": _trusted(measured.splitter_rh_p01_mm, source="splitter_ride_height_channel"),
        "pitch_range_braking_deg": _trusted(
            measured.pitch_range_braking_deg,
            source="pitch_from_ride_height",
            confidence=0.8,
        ),
        "pitch_mean_braking_deg": _trusted(
            measured.pitch_mean_braking_deg,
            source="pitch_from_ride_height",
            confidence=0.8,
        ),
        "understeer_mean_deg": _proxy(measured.understeer_mean_deg, source="steering_yaw_proxy"),
        "understeer_low_speed_deg": _proxy(measured.understeer_low_speed_deg, source="steering_yaw_proxy"),
        "understeer_high_speed_deg": _proxy(measured.understeer_high_speed_deg, source="steering_yaw_proxy"),
        "body_slip_p95_deg": _proxy(measured.body_slip_p95_deg, source="body_velocity_proxy"),
        "yaw_rate_correlation": _proxy(measured.yaw_rate_correlation, source="yaw_fit_proxy", confidence=0.7),
    }

    signals["front_rh_settle_time_ms"] = TelemetrySignal(
        value=measured.front_rh_settle_time_ms if front_settle_valid else None,
        quality="trusted" if front_settle_valid else "unknown",
        confidence=min(0.95, 0.55 + getattr(measured, "front_settle_valid_clean_events", 0) * 0.08)
        if front_settle_valid
        else 0.0,
        source="event_based_clean_disturbance_response",
        invalid_reason="" if front_settle_valid else (front_settle_reason or "insufficient_clean_events"),
    )
    signals["rear_rh_settle_time_ms"] = TelemetrySignal(
        value=measured.rear_rh_settle_time_ms if rear_settle_valid else None,
        quality="trusted" if rear_settle_valid else "unknown",
        confidence=min(0.95, 0.55 + getattr(measured, "rear_settle_valid_clean_events", 0) * 0.08)
        if rear_settle_valid
        else 0.0,
        source="event_based_clean_disturbance_response",
        invalid_reason="" if rear_settle_valid else (rear_settle_reason or "insufficient_clean_events"),
    )
    return signals


def build_telemetry_bundle(signals: dict[str, TelemetrySignal[Any]]) -> TelemetryBundle:
    return TelemetryBundle(
        aero_platform={
            "mean_front_rh_at_speed_mm": signals.get("mean_front_rh_at_speed_mm", TelemetrySignal()),
            "mean_rear_rh_at_speed_mm": signals.get("mean_rear_rh_at_speed_mm", TelemetrySignal()),
            "front_rh_std_mm": signals.get("front_rh_std_mm", TelemetrySignal()),
            "rear_rh_std_mm": signals.get("rear_rh_std_mm", TelemetrySignal()),
            "splitter_rh_p01_mm": signals.get("splitter_rh_p01_mm", TelemetrySignal()),
        },
        braking_platform={
            "front_braking_lock_ratio_p95": signals.get("front_braking_lock_ratio_p95", TelemetrySignal()),
            "pitch_range_braking_deg": signals.get("pitch_range_braking_deg", TelemetrySignal()),
            "pitch_mean_braking_deg": signals.get("pitch_mean_braking_deg", TelemetrySignal()),
            "front_rh_settle_time_ms": signals.get("front_rh_settle_time_ms", TelemetrySignal()),
        },
        traction_balance={
            "rear_power_slip_ratio_p95": signals.get("rear_power_slip_ratio_p95", TelemetrySignal()),
            "understeer_low_speed_deg": signals.get("understeer_low_speed_deg", TelemetrySignal()),
            "understeer_high_speed_deg": signals.get("understeer_high_speed_deg", TelemetrySignal()),
            "body_slip_p95_deg": signals.get("body_slip_p95_deg", TelemetrySignal()),
        },
        kerb_compliance={
            "bottoming_event_count_front_clean": signals.get("bottoming_event_count_front_clean", TelemetrySignal()),
            "bottoming_event_count_rear_clean": signals.get("bottoming_event_count_rear_clean", TelemetrySignal()),
            "bottoming_event_count_front_kerb": signals.get("bottoming_event_count_front_kerb", TelemetrySignal()),
            "bottoming_event_count_rear_kerb": signals.get("bottoming_event_count_rear_kerb", TelemetrySignal()),
            "front_rh_settle_time_ms": signals.get("front_rh_settle_time_ms", TelemetrySignal()),
            "rear_rh_settle_time_ms": signals.get("rear_rh_settle_time_ms", TelemetrySignal()),
        },
        tyre_support={
            "front_pressure_mean_kpa": signals.get("front_pressure_mean_kpa", TelemetrySignal()),
            "rear_pressure_mean_kpa": signals.get("rear_pressure_mean_kpa", TelemetrySignal()),
            "front_carcass_mean_c": signals.get("front_carcass_mean_c", TelemetrySignal()),
            "rear_carcass_mean_c": signals.get("rear_carcass_mean_c", TelemetrySignal()),
        },
        driver_inputs={
            "yaw_rate_correlation": signals.get("yaw_rate_correlation", TelemetrySignal()),
            "front_shock_oscillation_hz": signals.get("front_shock_oscillation_hz", TelemetrySignal()),
            "rear_shock_oscillation_hz": signals.get("rear_shock_oscillation_hz", TelemetrySignal()),
        },
    )


def build_session_normalization(before: "SessionSnapshot", after: "SessionSnapshot") -> SessionNormalization:
    fuel_delta = abs((before.setup.fuel_l or 0.0) - (after.setup.fuel_l or 0.0))
    air_delta = abs((before.measured.air_temp_c or 0.0) - (after.measured.air_temp_c or 0.0))
    track_delta = abs((before.measured.track_temp_c or 0.0) - (after.measured.track_temp_c or 0.0))
    wind_delta = abs((before.measured.wind_speed_ms or 0.0) - (after.measured.wind_speed_ms or 0.0))
    tyre_delta = (
        abs((before.measured.front_pressure_mean_kpa or 0.0) - (after.measured.front_pressure_mean_kpa or 0.0))
        + abs((before.measured.rear_pressure_mean_kpa or 0.0) - (after.measured.rear_pressure_mean_kpa or 0.0))
        + abs((before.measured.front_carcass_mean_c or 0.0) - (after.measured.front_carcass_mean_c or 0.0)) / 4.0
        + abs((before.measured.rear_carcass_mean_c or 0.0) - (after.measured.rear_carcass_mean_c or 0.0)) / 4.0
    )

    fuel_score = max(0.0, 1.0 - fuel_delta / 4.0)
    weather_score = max(0.0, 1.0 - air_delta / 10.0)
    track_state_score = max(0.0, 1.0 - track_delta / 10.0)
    tyre_state_score = max(0.0, 1.0 - tyre_delta / 18.0)
    wind_score = max(0.0, 1.0 - wind_delta / 6.0)

    notes: list[str] = []
    if fuel_delta > 2.0:
        notes.append(f"fuel delta {fuel_delta:.1f} L")
    if air_delta > 5.0:
        notes.append(f"air temp delta {air_delta:.1f} C")
    if track_delta > 5.0:
        notes.append(f"track temp delta {track_delta:.1f} C")
    if wind_delta > 3.0:
        notes.append(f"wind delta {wind_delta:.1f} m/s")
    if tyre_delta > 8.0:
        notes.append(f"tyre state delta {tyre_delta:.1f}")

    lap_cleanliness = 1.0
    traffic_score = 1.0
    overall = (
        fuel_score * 0.30
        + weather_score * 0.15
        + track_state_score * 0.20
        + wind_score * 0.10
        + tyre_state_score * 0.15
        + lap_cleanliness * 0.05
        + traffic_score * 0.05
    )

    return SessionNormalization(
        fuel_delta_l=round(fuel_delta, 2),
        air_temp_delta_c=round(air_delta, 2),
        track_temp_delta_c=round(track_delta, 2),
        wind_delta_ms=round(wind_delta, 2),
        tyre_state_delta=round(tyre_delta, 2),
        fuel_score=round(fuel_score, 3),
        weather_score=round(weather_score, 3),
        track_state_score=round(track_state_score, 3),
        lap_cleanliness_score=round(lap_cleanliness, 3),
        traffic_score=round(traffic_score, 3),
        tyre_state_score=round(tyre_state_score, 3),
        overall_score=round(overall, 3),
        comparable=overall >= 0.45,
        notes=notes,
    )


def summarize_signal_quality(measured: Any, *, limit: int = 8) -> list[str]:
    signal_map = getattr(measured, "telemetry_signals", {}) or {}
    if not signal_map:
        return []

    trusted = [name for name, sig in signal_map.items() if sig.quality == "trusted"]
    proxy = [name for name, sig in signal_map.items() if sig.quality == "proxy"]
    unresolved = [
        f"{name} ({sig.invalid_reason})".strip()
        for name, sig in signal_map.items()
        if sig.quality in {"unknown", "broken"} or sig.conflict_state != "clear"
    ]

    lines: list[str] = []
    if trusted:
        lines.append(f"Trusted: {', '.join(trusted[:limit])}")
    if proxy:
        lines.append(f"Proxy: {', '.join(proxy[:limit])}")
    if unresolved:
        lines.append(f"Unresolved: {', '.join(unresolved[:limit])}")
    return lines
