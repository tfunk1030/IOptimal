from __future__ import annotations

from learner.delta_detector import EFFECT_METRICS, KNOWN_CAUSALITY
from learner.empirical_models import fit_models
from validator.compare import compare_all
from validator.extract import MeasuredState


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
    measured = MeasuredState(lltd_measured=0.50)
    solver_json = {"step4_arb": {"lltd_achieved": 0.62}}

    comparisons = compare_all(solver_json, measured)

    assert all(comp.parameter != "lltd" for comp in comparisons)
