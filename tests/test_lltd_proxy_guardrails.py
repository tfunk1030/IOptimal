from __future__ import annotations

from types import SimpleNamespace

from learner.delta_detector import EFFECT_METRICS, KNOWN_CAUSALITY
from learner.empirical_models import fit_models
from validator.compare import compare_all


def _obs(idx: int, rear_arb: int, proxy: float) -> dict:
    return {
        "session_id": f"s{idx}",
        "timestamp": "2026-04-26T00:00:00+00:00",
        "setup": {"rear_arb_blade": rear_arb},
        "telemetry": {
            "lltd_measured": proxy,
            "roll_distribution_proxy": proxy,
            "roll_gradient_deg_per_g": 1.8,
            "peak_lat_g": 2.1,
        },
        "performance": {"best_lap_time_s": 110.0 + idx},
    }


def test_learner_does_not_fit_lltd_from_roll_proxy() -> None:
    observations = [_obs(i, rear_arb=1 + i, proxy=0.50 + i * 0.01) for i in range(5)]

    models = fit_models(observations, deltas=[], car="bmw", track="sebring")

    assert "lltd_vs_rear_arb" not in models.relationships
    assert models.corrections["lltd_is_proxy"] is True
    assert "roll_distribution_proxy_mean" in models.corrections


def test_delta_detector_uses_proxy_name_not_lltd_measured_for_arb() -> None:
    assert "lltd_measured" not in EFFECT_METRICS["balance"]
    assert "roll_distribution_proxy" in EFFECT_METRICS["balance"]

    front_effects = KNOWN_CAUSALITY[("front_arb_blade", "+")]
    rear_effects = KNOWN_CAUSALITY[("rear_arb_blade", "+")]

    assert ("lltd_measured", "-") not in front_effects
    assert ("lltd_measured", "+") not in rear_effects
    assert ("roll_distribution_proxy", "~") in front_effects
    assert ("roll_distribution_proxy", "~") in rear_effects


def test_validator_does_not_compare_predicted_lltd_to_roll_proxy() -> None:
    measured = SimpleNamespace(
        mean_speed_at_speed_kph=0.0,
        static_front_rh_sensor_mm=0.0,
        static_rear_rh_sensor_mm=0.0,
        aero_compression_front_mm=0.0,
        aero_compression_rear_mm=0.0,
        bottoming_event_count_front=0,
        bottoming_event_count_rear=0,
        vortex_burst_event_count=0,
        front_shock_vel_p99_mps=0.0,
        rear_shock_vel_p99_mps=0.0,
        front_rh_excursion_measured_mm=0.0,
        rear_rh_excursion_measured_mm=0.0,
        front_dominant_freq_hz=0.0,
        rear_dominant_freq_hz=0.0,
        lltd_measured=0.50,
        roll_gradient_measured_deg_per_g=0.0,
        body_roll_at_peak_g_deg=0.0,
        peak_lat_g_measured=0.0,
        front_shock_vel_p95_mps=0.0,
        rear_shock_vel_p95_mps=0.0,
        front_rh_settle_time_ms=0.0,
        rear_rh_settle_time_ms=0.0,
    )
    solver_json = {"step4_arb": {"lltd_achieved": 0.62}}

    comparisons = compare_all(solver_json, measured)

    assert all(comp.parameter != "lltd" for comp in comparisons)
