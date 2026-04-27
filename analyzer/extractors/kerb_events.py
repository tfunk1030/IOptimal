"""Kerb-event step-response damper identification.

Each kerb strike is a free step-input damper experiment. We isolate the
post-strike ride-height ringdown, fit a log-decrement to the consecutive
extrema, and recover ζ (damping ratio) and ω_n (natural frequency) per
strike. Aggregated p50/p95 statistics describe the as-driven damper
behaviour without touching any car-specific defaults.

Channel discovery is gated on ``IBTFile.has_channel`` so cars without
``Tire*_RumblePitch`` (or without per-corner ride-height channels) get an
all-``None`` result rather than a fabricated value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from car_model.cars import CarModel
    from track_model.ibt_parser import IBTFile


# ── Tunables ──
_POST_IMPACT_WINDOW_S = 1.0     # Ringdown window per strike
_PRE_IMPACT_GUARD_S = 0.05      # Skip a few samples after the rising edge before sampling
_MIN_AMPLITUDE_MM = 0.5         # Below this peak deviation, signal is noise
_MIN_CYCLES = 2                 # Need ≥ 2 visible cycles for log-decrement
_ZETA_MIN = 0.05                # Sane bound: under-damped car suspension
_ZETA_MAX = 1.0                 # At/above critical, log-decrement breaks down
_FREQ_MIN_HZ = 0.5
_FREQ_MAX_HZ = 10.0             # Sprung-mass + tyre modes both fall well under this
_COOLDOWN_S = 0.30              # Re-arm window after a rising edge


# Result schema. Always returned, even when no strikes are usable.
_NULL_RESULT: dict[str, float | int | None] = {
    "front_step_response_zeta_p50": None,
    "front_step_response_zeta_p95": None,
    "front_step_response_freq_hz_p50": None,
    "rear_step_response_zeta_p50": None,
    "rear_step_response_zeta_p95": None,
    "rear_step_response_freq_hz_p50": None,
    "kerb_strike_count": 0,
}


def extract_kerb_step_responses(
    ibt: "IBTFile",
    car: "CarModel",
) -> dict[str, float | int | None]:
    """Extract per-axle ζ and ω_n from kerb-strike ringdowns.

    Returns a dict with the keys listed in ``_NULL_RESULT``. Returns the
    null result (counts == 0, ζ/ω_n == None) when:
        - the IBT lacks ride-height or rumble channels,
        - no rumble rising edges are seen,
        - every candidate window fails the amplitude / cycle sanity bounds.
    """

    # 1) Channel availability ─ gate everything explicitly. Per-car: any car
    # without Tire*_RumblePitch (older recordings, modded cars, GT3 packs that
    # report the rumble channels by different names) bails cleanly.
    rh_channel_names = ("LFrideHeight", "RFrideHeight", "LRrideHeight", "RRrideHeight")
    if not all(ibt.has_channel(c) for c in rh_channel_names):
        return dict(_NULL_RESULT)

    rumble_names = (
        "TireLF_RumblePitch",
        "TireRF_RumblePitch",
        "TireLR_RumblePitch",
        "TireRR_RumblePitch",
    )
    rumbles = [ibt.channel(name) for name in rumble_names if ibt.has_channel(name)]
    if not rumbles:
        return dict(_NULL_RESULT)

    # 2) Build any-corner-on-kerb mask + rising edges.
    on_kerb = np.zeros(len(rumbles[0]), dtype=bool)
    for rumble in rumbles:
        if len(rumble) == len(on_kerb):
            on_kerb |= rumble > 0

    if not np.any(on_kerb):
        return dict(_NULL_RESULT)

    edges = np.diff(on_kerb.astype(np.int8))
    rising_indices = np.where(edges == 1)[0] + 1

    if rising_indices.size == 0:
        return dict(_NULL_RESULT)

    tick_rate = float(ibt.tick_rate) if ibt.tick_rate else 60.0
    cooldown_samples = max(1, int(_COOLDOWN_S * tick_rate))

    # Re-arm: drop strike candidates that fall inside the previous strike's window.
    deduped: list[int] = []
    last = -cooldown_samples
    for idx in rising_indices:
        if idx - last >= cooldown_samples:
            deduped.append(int(idx))
            last = int(idx)

    # 3) Ride-height channels (mm). Convert from m once.
    lf_rh = ibt.channel("LFrideHeight") * 1000.0
    rf_rh = ibt.channel("RFrideHeight") * 1000.0
    lr_rh = ibt.channel("LRrideHeight") * 1000.0
    rr_rh = ibt.channel("RRrideHeight") * 1000.0
    front_rh_full = (lf_rh + rf_rh) / 2.0
    rear_rh_full = (lr_rh + rr_rh) / 2.0

    n_samples = len(front_rh_full)
    window_samples = max(_MIN_CYCLES + 4, int(_POST_IMPACT_WINDOW_S * tick_rate))
    guard_samples = max(0, int(_PRE_IMPACT_GUARD_S * tick_rate))

    front_zetas: list[float] = []
    front_freqs: list[float] = []
    rear_zetas: list[float] = []
    rear_freqs: list[float] = []
    usable_strikes = 0

    for strike_idx in deduped:
        win_start = strike_idx + guard_samples
        win_end = win_start + window_samples
        if win_end > n_samples:
            continue

        front_fit = _fit_ringdown(front_rh_full[win_start:win_end], tick_rate)
        rear_fit = _fit_ringdown(rear_rh_full[win_start:win_end], tick_rate)

        # A strike is "usable" if at least one axle delivered a fit.
        if front_fit is None and rear_fit is None:
            continue
        usable_strikes += 1

        if front_fit is not None:
            zeta_f, freq_f = front_fit
            front_zetas.append(zeta_f)
            front_freqs.append(freq_f)
        if rear_fit is not None:
            zeta_r, freq_r = rear_fit
            rear_zetas.append(zeta_r)
            rear_freqs.append(freq_r)

    result: dict[str, float | int | None] = dict(_NULL_RESULT)
    result["kerb_strike_count"] = usable_strikes
    result["front_step_response_zeta_p50"] = _percentile(front_zetas, 50)
    result["front_step_response_zeta_p95"] = _percentile(front_zetas, 95)
    result["front_step_response_freq_hz_p50"] = _percentile(front_freqs, 50)
    result["rear_step_response_zeta_p50"] = _percentile(rear_zetas, 50)
    result["rear_step_response_zeta_p95"] = _percentile(rear_zetas, 95)
    result["rear_step_response_freq_hz_p50"] = _percentile(rear_freqs, 50)
    return result


def _fit_ringdown(
    window: np.ndarray,
    tick_rate: float,
) -> tuple[float, float] | None:
    """Fit ζ and ω_n to a single post-strike ride-height ringdown.

    - Detrend by subtracting the window mean.
    - Locate alternating extrema via sign-changes of the derivative.
    - log-decrement δ = mean of ln|peak[i] / peak[i+1]| over consecutive same-sign
      extrema (peaks-of-peaks); ζ = δ / sqrt(4π² + δ²).
    - ω_n from successive zero-crossings: T = 2 × mean Δt, ω_n = 2π / T,
      f_d = ω_n / (2π) reported (damped frequency, ≈ ω_n for small ζ).

    Returns ``None`` if the window fails amplitude / cycle bounds or the
    inferred values fall outside physical limits.
    """
    detrended = window - float(np.mean(window))
    peak_amplitude = float(np.max(np.abs(detrended)))
    if peak_amplitude < _MIN_AMPLITUDE_MM:
        return None

    # ── Extrema (peaks of |x|) via derivative sign change ──
    diff = np.diff(detrended)
    # Sign of derivative; an extremum is where it flips sign.
    sign = np.sign(diff)
    # Replace zeros so we don't double-count flat samples.
    sign[sign == 0] = 1
    flips = np.where(np.diff(sign) != 0)[0] + 1  # indices of extrema in `detrended`
    if flips.size < _MIN_CYCLES + 1:
        return None

    extrema_vals = detrended[flips]
    # Successive same-sign extrema (peak→peak or trough→trough): use abs values.
    abs_vals = np.abs(extrema_vals)
    # Skip negligible extrema (sub-noise wiggles after most of the ring has
    # decayed) to keep δ honest.
    significant = abs_vals > max(0.1 * peak_amplitude, 0.05)
    abs_vals = abs_vals[significant]
    if abs_vals.size < _MIN_CYCLES:
        return None

    ratios = abs_vals[:-1] / abs_vals[1:]
    ratios = ratios[np.isfinite(ratios) & (ratios > 1.0)]
    if ratios.size == 0:
        return None
    # Each adjacent extremum is half a period apart, so log-decrement here is
    # δ_half = ln(peak_i / peak_{i+1}); convert to per-cycle by ×2.
    delta_half = float(np.mean(np.log(ratios)))
    delta = 2.0 * delta_half
    zeta = delta / np.sqrt(4.0 * np.pi * np.pi + delta * delta)
    if not (_ZETA_MIN <= zeta <= _ZETA_MAX):
        return None

    # ── Frequency from zero-crossings of detrended signal ──
    sgn = np.sign(detrended)
    sgn[sgn == 0] = 1
    zc_indices = np.where(np.diff(sgn) != 0)[0]
    if zc_indices.size < 2:
        return None
    zc_intervals_samples = np.diff(zc_indices)
    if zc_intervals_samples.size == 0:
        return None
    mean_half_period_s = float(np.mean(zc_intervals_samples)) / tick_rate
    if mean_half_period_s <= 0.0:
        return None
    period_s = 2.0 * mean_half_period_s
    freq_hz = 1.0 / period_s
    if not (_FREQ_MIN_HZ <= freq_hz <= _FREQ_MAX_HZ):
        return None

    return zeta, freq_hz


def _percentile(values: list[float], pct: float) -> float | None:
    """Robust percentile that tolerates empty input."""
    if not values:
        return None
    return float(np.percentile(values, pct))
