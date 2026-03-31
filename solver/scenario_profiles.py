"""Scenario-specific objective weights and prediction sanity limits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ObjectiveWeightProfile:
    w_lap_gain: float = 1.0
    w_platform: float = 1.0
    w_driver: float = 0.5
    w_uncertainty: float = 0.6
    w_envelope: float = 0.7
    w_staleness: float = 0.3
    w_empirical: float = 0.40


@dataclass(frozen=True)
class PredictionSanityProfile:
    min_overall_confidence: float = 0.35
    max_front_heave_travel_used_pct: float | None = None
    max_front_excursion_mm: float | None = None
    max_rear_rh_std_mm: float | None = None
    max_braking_pitch_deg: float | None = None
    max_front_lock_p95: float | None = None
    max_rear_power_slip_p95: float | None = None
    max_body_slip_p95_deg: float | None = None
    max_understeer_low_abs_deg: float | None = None
    max_understeer_high_abs_deg: float | None = None
    front_pressure_hot_kpa_range: tuple[float, float] | None = None
    rear_pressure_hot_kpa_range: tuple[float, float] | None = None


@dataclass(frozen=True)
class ScenarioProfile:
    name: str
    label: str
    description: str
    objective: ObjectiveWeightProfile
    sanity: PredictionSanityProfile
    preferred_result_key: str = "best_robust"


DEFAULT_SCENARIO = "single_lap_safe"

_SCENARIOS: dict[str, ScenarioProfile] = {
    "single_lap_safe": ScenarioProfile(
        name="single_lap_safe",
        label="Single Lap Safe",
        description="Conservative telemetry-backed hotlap profile used as the default validation path.",
        # Balanced weights: chase lap time but penalise instability, bad drivability,
        # and low-confidence regions.  Sits below quali (0.90/0.35/0.45) so this
        # remains the least-penalised profile while rejecting degenerate edge-family
        # candidates (extreme_soft_mech, max_rotation, etc.).
        objective=ObjectiveWeightProfile(
            w_lap_gain=1.00,
            w_platform=0.75,
            w_driver=0.30,
            w_uncertainty=0.40,
            w_envelope=0.55,
            w_staleness=0.15,
            w_empirical=0.20,
        ),
        sanity=PredictionSanityProfile(
            min_overall_confidence=0.50,
            max_front_heave_travel_used_pct=96.0,
            max_front_excursion_mm=18.0,
            max_rear_rh_std_mm=9.0,
            max_braking_pitch_deg=1.50,
            max_front_lock_p95=0.11,
            max_rear_power_slip_p95=0.12,
            max_body_slip_p95_deg=4.5,
            max_understeer_low_abs_deg=2.0,
            max_understeer_high_abs_deg=2.0,
            front_pressure_hot_kpa_range=(160.0, 186.0),
            rear_pressure_hot_kpa_range=(160.0, 186.0),
        ),
        preferred_result_key="best_robust",
    ),
    "quali": ScenarioProfile(
        name="quali",
        label="Qualifying",
        description="Single-lap pace with lighter long-run penalties and a more aggressive accepted candidate bias.",
        objective=ObjectiveWeightProfile(
            w_lap_gain=1.00,
            w_platform=0.90,
            w_driver=0.35,
            w_uncertainty=0.45,
            w_envelope=0.50,
            w_staleness=0.20,
            w_empirical=0.25,
        ),
        sanity=PredictionSanityProfile(
            min_overall_confidence=0.35,
            max_front_heave_travel_used_pct=99.5,
            max_front_excursion_mm=19.0,
            max_rear_rh_std_mm=9.8,
            max_braking_pitch_deg=1.65,
            max_front_lock_p95=0.12,
            max_rear_power_slip_p95=0.13,
            max_body_slip_p95_deg=5.6,
            max_understeer_low_abs_deg=2.4,
            max_understeer_high_abs_deg=2.4,
            front_pressure_hot_kpa_range=(158.0, 188.0),
            rear_pressure_hot_kpa_range=(158.0, 188.0),
        ),
        preferred_result_key="best_aggressive",
    ),
    "sprint": ScenarioProfile(
        name="sprint",
        label="Sprint",
        description="Early to mid-stint compromise with moderate thermal and stability penalties.",
        objective=ObjectiveWeightProfile(
            w_lap_gain=1.00,
            w_platform=1.00,
            w_driver=0.45,
            w_uncertainty=0.55,
            w_envelope=0.70,
            w_staleness=0.30,
            w_empirical=0.35,
        ),
        sanity=PredictionSanityProfile(
            min_overall_confidence=0.40,
            max_front_heave_travel_used_pct=97.0,
            max_front_excursion_mm=18.2,
            max_rear_rh_std_mm=8.9,
            max_braking_pitch_deg=1.45,
            max_front_lock_p95=0.11,
            max_rear_power_slip_p95=0.11,
            max_body_slip_p95_deg=5.0,
            max_understeer_low_abs_deg=2.1,
            max_understeer_high_abs_deg=2.1,
            front_pressure_hot_kpa_range=(160.0, 186.0),
            rear_pressure_hot_kpa_range=(160.0, 186.0),
        ),
        preferred_result_key="best_robust",
    ),
    "race": ScenarioProfile(
        name="race",
        label="Race",
        description="Late-stint robustness with stronger platform, traction, and envelope penalties.",
        objective=ObjectiveWeightProfile(
            w_lap_gain=1.00,
            w_platform=1.20,
            w_driver=0.55,
            w_uncertainty=0.70,
            w_envelope=0.85,
            w_staleness=0.35,
            w_empirical=0.45,
        ),
        sanity=PredictionSanityProfile(
            min_overall_confidence=0.45,
            max_front_heave_travel_used_pct=95.5,
            max_front_excursion_mm=17.5,
            max_rear_rh_std_mm=8.4,
            max_braking_pitch_deg=1.35,
            max_front_lock_p95=0.10,
            max_rear_power_slip_p95=0.10,
            max_body_slip_p95_deg=4.6,
            max_understeer_low_abs_deg=1.9,
            max_understeer_high_abs_deg=1.9,
            front_pressure_hot_kpa_range=(160.0, 184.0),
            rear_pressure_hot_kpa_range=(160.0, 184.0),
        ),
        preferred_result_key="best_robust",
    ),
}

_ALIASES = {
    "balanced": DEFAULT_SCENARIO,
    "default": DEFAULT_SCENARIO,
    "safe": DEFAULT_SCENARIO,
    "validation": DEFAULT_SCENARIO,
    "qualifying": "quali",
}


def resolve_scenario_name(name: str | None) -> str:
    if name is None:
        return DEFAULT_SCENARIO
    normalized = str(name).strip().lower().replace("-", "_").replace(" ", "_")
    normalized = _ALIASES.get(normalized, normalized)
    return normalized if normalized in _SCENARIOS else DEFAULT_SCENARIO


def get_scenario_profile(name: str | None) -> ScenarioProfile:
    return _SCENARIOS[resolve_scenario_name(name)]


def iter_scenario_profiles() -> tuple[ScenarioProfile, ...]:
    return tuple(_SCENARIOS[name] for name in ("single_lap_safe", "quali", "sprint", "race"))


def should_run_legal_manifold_search(
    *,
    free_mode: bool = False,
    explicit_search: bool = False,
    search_mode: str | None = None,
    scenario_name: str | None = None,
) -> bool:
    resolved = resolve_scenario_name(scenario_name)
    return bool(free_mode or explicit_search or search_mode is not None or resolved != DEFAULT_SCENARIO)


def prediction_passes_sanity(
    prediction: Any,
    confidence: Any,
    scenario_name: str | None = None,
) -> tuple[bool, list[str]]:
    profile = get_scenario_profile(scenario_name)
    sanity = profile.sanity
    issues: list[str] = []

    overall_conf = getattr(confidence, "overall", None)
    try:
        if overall_conf is not None and float(overall_conf) < sanity.min_overall_confidence:
            issues.append(
                f"prediction confidence {float(overall_conf):.2f} below {sanity.min_overall_confidence:.2f}"
            )
    except (TypeError, ValueError):
        pass

    numeric_limits = (
        ("front_heave_travel_used_pct", sanity.max_front_heave_travel_used_pct, "front heave travel"),
        ("front_excursion_mm", sanity.max_front_excursion_mm, "front excursion"),
        ("rear_rh_std_mm", sanity.max_rear_rh_std_mm, "rear RH sigma"),
        ("braking_pitch_deg", sanity.max_braking_pitch_deg, "braking pitch"),
        ("front_lock_p95", sanity.max_front_lock_p95, "front lock"),
        ("rear_power_slip_ratio_p95", sanity.max_rear_power_slip_p95, "rear slip"),
        ("body_slip_p95_deg", sanity.max_body_slip_p95_deg, "body slip"),
    )
    for attr, limit, label in numeric_limits:
        if limit is None:
            continue
        value = getattr(prediction, attr, None)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric > limit:
            issues.append(f"{label} {numeric:.3f} exceeds {limit:.3f}")

    abs_limits = (
        ("understeer_low_deg", sanity.max_understeer_low_abs_deg, "low-speed understeer"),
        ("understeer_high_deg", sanity.max_understeer_high_abs_deg, "high-speed understeer"),
    )
    for attr, limit, label in abs_limits:
        if limit is None:
            continue
        value = getattr(prediction, attr, None)
        if value is None:
            continue
        try:
            numeric = abs(float(value))
        except (TypeError, ValueError):
            continue
        if numeric > limit:
            issues.append(f"{label} |{float(value):.3f}| exceeds {limit:.3f}")

    ranged_limits = (
        ("front_pressure_hot_kpa", sanity.front_pressure_hot_kpa_range, "front pressure"),
        ("rear_pressure_hot_kpa", sanity.rear_pressure_hot_kpa_range, "rear pressure"),
    )
    for attr, limits, label in ranged_limits:
        if limits is None:
            continue
        value = getattr(prediction, attr, None)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        lo, hi = limits
        if numeric < lo or numeric > hi:
            issues.append(f"{label} {numeric:.3f} outside {lo:.1f}-{hi:.1f}")

    return len(issues) == 0, issues
