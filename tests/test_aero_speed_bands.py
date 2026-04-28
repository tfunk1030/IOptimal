"""Unit tests for analyzer.extractors.aero_speed_bands.

These tests use a synthetic IBT-shaped object so we don't depend on
checked-in IBT files. Real IBTs are exercised via the pipeline regression
tests (test_setup_regression.py).
"""

from __future__ import annotations

import numpy as np
import pytest

from analyzer.extractors.aero_speed_bands import (
    extract_aero_compression_by_speed_band,
)
from car_model.cars import get_car


class _FakeIBT:
    """Minimal IBT-shaped stub for testing extractors.

    Channels are passed in as ``np.ndarray``. Ride-height channels are
    expected in METRES (matching iRacing's IBT convention) so the
    extractor's ``* 1000`` conversion is exercised.
    """

    def __init__(self, channels: dict[str, np.ndarray]):
        self._channels = channels

    def has_channel(self, name: str) -> bool:
        return name in self._channels

    def channel(self, name: str) -> np.ndarray:
        return self._channels[name]


def _build_synthetic_ibt(
    *,
    static_front_mm: float = 50.0,
    static_rear_mm: float = 60.0,
    alpha_front: float = 6e-4,  # mm per (km/h)^2
    alpha_rear: float = 4e-4,
    n_per_speed: int = 200,
    speeds_kph: tuple[float, ...] = (5.0, 120.0, 170.0, 220.0, 280.0),
) -> _FakeIBT:
    """Build a fake IBT where compression follows alpha*V^2 exactly."""
    speed_blocks = []
    front_blocks = []
    rear_blocks = []
    brake_blocks = []
    for v in speeds_kph:
        speed_blocks.append(np.full(n_per_speed, v))
        # static_RH (in mm) - alpha*V^2 (in mm) -> mm, divide by 1000 for metres
        front_dyn_mm = static_front_mm - alpha_front * (v * v)
        rear_dyn_mm = static_rear_mm - alpha_rear * (v * v)
        # Add tiny noise so std isn't zero
        rng = np.random.default_rng(int(v * 1000) + 17)
        front_blocks.append(
            (front_dyn_mm + rng.normal(0.0, 0.05, n_per_speed)) / 1000.0
        )
        rear_blocks.append(
            (rear_dyn_mm + rng.normal(0.0, 0.05, n_per_speed)) / 1000.0
        )
        brake_blocks.append(np.zeros(n_per_speed))

    speed = np.concatenate(speed_blocks) / 3.6  # kph -> m/s for IBT "Speed"
    lf = np.concatenate(front_blocks)
    rf = lf + 0.0  # symmetric
    lr = np.concatenate(rear_blocks)
    rr = lr + 0.0
    brake = np.concatenate(brake_blocks)
    return _FakeIBT({
        "Speed": speed,
        "Brake": brake,
        "LFrideHeight": lf,
        "RFrideHeight": rf,
        "LRrideHeight": lr,
        "RRrideHeight": rr,
    })


@pytest.mark.parametrize("car_name", ["bmw", "porsche", "ferrari", "cadillac", "acura"])
def test_extractor_emits_per_bin_keys_and_alpha(car_name):
    """All five cars: extractor returns per-bin compression and alpha fits.

    The synthetic data is generated with a known alpha, so the fitted
    alpha must agree to within ~5%.
    """
    car = get_car(car_name)
    alpha_f_true = 6e-4
    alpha_r_true = 4e-4
    ibt = _build_synthetic_ibt(alpha_front=alpha_f_true, alpha_rear=alpha_r_true)

    out = extract_aero_compression_by_speed_band(ibt, car)

    # Three high-speed bins should be populated (120 in 100-150, 170 in
    # 150-200, 220 in 200-250, 280 in 250-400).
    for label in ("100_150", "150_200", "200_250", "250_400"):
        assert f"front_{label}" in out, f"missing front_{label} for {car_name}"
        assert f"rear_{label}" in out, f"missing rear_{label} for {car_name}"
        assert f"samples_{label}" in out
        assert f"v2_mid_{label}" in out

    assert "alpha_front" in out
    assert "alpha_rear" in out
    assert "r2_front" in out
    assert "r2_rear" in out
    assert out["car_canonical"] == car_name

    # Synthetic data is V^2-perfect so alpha must match closely.
    assert abs(out["alpha_front"] - alpha_f_true) / alpha_f_true < 0.05
    assert abs(out["alpha_rear"] - alpha_r_true) / alpha_r_true < 0.05
    # R^2 should be near 1.0 for synthetic clean data.
    assert out["r2_front"] > 0.99
    assert out["r2_rear"] > 0.99


def test_extractor_returns_empty_when_channels_missing():
    car = get_car("bmw")
    ibt = _FakeIBT({"Speed": np.array([100.0, 200.0])})  # no RH channels
    assert extract_aero_compression_by_speed_band(ibt, car) == {}


def test_extractor_skips_underpopulated_bins():
    car = get_car("bmw")
    # Only 5 samples in the 200-250 bin (below min_samples_per_bin=30).
    ibt = _build_synthetic_ibt(speeds_kph=(5.0, 120.0, 170.0), n_per_speed=200)
    # Add a tiny 200-250 burst at the end
    extra = _build_synthetic_ibt(speeds_kph=(220.0,), n_per_speed=5)
    merged = {
        k: np.concatenate([ibt._channels[k], extra._channels[k]])
        for k in ibt._channels
    }
    fake = _FakeIBT(merged)
    out = extract_aero_compression_by_speed_band(fake, car)
    # 200-250 must be absent; 100-150 and 150-200 must be present.
    assert "front_200_250" not in out
    assert "front_100_150" in out
    assert "front_150_200" in out
