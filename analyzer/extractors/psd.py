"""Suspension PSD → ζ + ω_n extractor.

Estimates undamped natural frequency ``f_n`` and damping ratio ``ζ`` per
suspension mode from a single IBT, by running Welch's PSD on the relevant
shock-deflection channels and reading the peak's centre frequency and FWHM.

Physics
-------
A second-order spring-mass-damper system has a transfer-function magnitude
peak at ``f_n = (1/2π)√(k/m_eff)``. The peak's quality factor

    Q = f_n / Δf_(-3 dB)

relates to damping by ``ζ = 1/(2Q)`` for ζ ≲ 0.5 (the ``Q ≈ 1/(2ζ)``
relationship breaks down for heavily damped systems, where the peak is
broad and shallow). FWHM here is the -3 dB width on the PSD (since PSD
is power, the peak is bracketed by half-magnitude → -3 dB → 0.5×peak_PSD).

Per-car channel selection
-------------------------
The function gates on which channels are present in the IBT and on the
car's damper architecture so heave/roll-only cars (Acura ORECA) and
purely per-corner cars (BMW, Ferrari, Cadillac, GT3) each get the right
modal decomposition.

* Per-corner shocks (BMW, Ferrari, Cadillac, Porsche 963, GT3 cars):
  ``LFshockDefl`` etc. → front_heave_mode = (LF + RF)/2,
  rear_heave_mode = (LR + RR)/2, plus per-corner f_n/ζ.
* Heave + roll architecture (Acura ARX-06): ``HFshockDefl``,
  ``HRshockDefl``, ``FROLLshockDefl``, ``RROLLshockDefl`` provide the
  modes directly, and per-corner shocks (if present) give per-corner
  metrics on top.
* GT3 (no heave third spring per the architecture flag): per-corner
  channels are still summed for an axle-mode estimate, but the dict
  keys for ``front_heave_*`` / ``rear_heave_*`` are omitted by
  default — gated on ``car.suspension_arch.has_heave_third`` when that
  attribute exists; otherwise we fall back to "channels present"
  detection so older car models that don't carry the field still get
  a heave reading.

Output keys
-----------
For each available mode we emit ``<mode>_natural_freq_hz``,
``<mode>_damping_ratio``, ``<mode>_q_factor``. Damping ratios outside
[0.05, 1.0] are clamped and the entry is also flagged in
``<mode>_psd_warning``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from car_model.cars import CarModel
    from track_model.ibt_parser import IBTFile


# Frequency band in which we look for the suspension peak.
# Below 1.5 Hz: aero load envelope, fuel burn, tilt, lap-scale drift —
# none are sprung-mass modes. Below 0.5 Hz the PSD is also dominated
# by Welch-window leakage of the DC-removed mean.
# Above 8 Hz: tyre/structure modes, not the sprung mass on the spring.
_BAND_LO_HZ = 1.5
_BAND_HI_HZ = 8.0

# ζ sanity bounds. Below 0.05 ⇒ effectively undamped (likely a sensor noise
# spike); above 1.0 ⇒ overdamped or no real peak (broad PSD floor).
_ZETA_MIN = 0.05
_ZETA_MAX = 1.0

# Welch parameters. nperseg=256 at 60 Hz ⇒ ≈4.27 s segments, df ≈ 0.234 Hz —
# enough resolution to separate front and rear modes (typically 2–5 Hz apart).
_NPERSEG = 256


def _welch_peak(
    signal: np.ndarray, fs: float
) -> tuple[float, float, float] | None:
    """Run Welch's PSD on ``signal`` and return ``(f_peak, q, zeta)``.

    Returns ``None`` if the signal is too short, all-zero, or the peak is
    outside the suspension band.
    """
    if signal is None or len(signal) < _NPERSEG * 2:
        return None
    # Detrend removes any DC offset / slow drift that would dump energy at f=0.
    sig = np.asarray(signal, dtype=np.float64)
    if not np.any(np.isfinite(sig)):
        return None
    sig = sig - np.nanmean(sig)
    if not np.any(sig):
        return None

    # Welch's method — averaged periodogram with Hann window. Imported lazily
    # so this module stays cheap to import in environments without scipy.
    try:
        from scipy.signal import welch
    except ImportError:
        return None

    nperseg = min(_NPERSEG, len(sig))
    freqs, psd = welch(sig, fs=fs, nperseg=nperseg)

    band = (freqs >= _BAND_LO_HZ) & (freqs <= _BAND_HI_HZ)
    if not np.any(band):
        return None
    band_freqs = freqs[band]
    band_psd = psd[band]

    peak_idx = int(np.argmax(band_psd))
    peak_psd = float(band_psd[peak_idx])
    if peak_psd <= 0.0:
        return None
    f_peak = float(band_freqs[peak_idx])

    # FWHM at half-power (-3 dB on PSD ⇒ half the peak value, since PSD is
    # already power). Walk left and right from the peak.
    half = peak_psd * 0.5
    left = peak_idx
    while left > 0 and band_psd[left] > half:
        left -= 1
    right = peak_idx
    n_band = len(band_psd)
    while right < n_band - 1 and band_psd[right] > half:
        right += 1
    if right <= left:
        return None
    fwhm = float(band_freqs[right] - band_freqs[left])
    if fwhm <= 0.0:
        return None

    q = f_peak / fwhm
    zeta = 1.0 / (2.0 * q) if q > 0.0 else float("nan")
    return f_peak, q, zeta


def _safe_load(ibt: "IBTFile", name: str) -> np.ndarray | None:
    """Return the channel array (best-lap only) or ``None`` if unavailable."""
    if not ibt.has_channel(name):
        return None
    arr = ibt.channel(name)
    if arr is None or len(arr) == 0:
        return None
    return np.asarray(arr, dtype=np.float64)


def _best_lap_slice(
    ibt: "IBTFile", arr: np.ndarray, slice_range: tuple[int, int] | None
) -> np.ndarray:
    """Slice ``arr`` to the requested range, defaulting to whole signal."""
    if slice_range is None:
        return arr
    start, end = slice_range
    return arr[start : end + 1]


def _emit_mode(
    out: dict[str, float | str], prefix: str, peak: tuple[float, float, float] | None
) -> None:
    """Write f_n / ζ / Q triple under ``<prefix>_*`` keys, with bounds-flagging."""
    if peak is None:
        return
    f_peak, q, zeta = peak
    warning = ""
    if zeta < _ZETA_MIN:
        warning = "underdamped_or_noisy"
    elif zeta > _ZETA_MAX:
        warning = "overdamped_or_noisy"
    zeta_clamped = float(np.clip(zeta, _ZETA_MIN, _ZETA_MAX))
    out[f"{prefix}_natural_freq_hz"] = float(f_peak)
    out[f"{prefix}_damping_ratio"] = zeta_clamped
    out[f"{prefix}_q_factor"] = float(q)
    if warning:
        out[f"{prefix}_psd_warning"] = warning


def _has_heave_third(car: "CarModel") -> bool:
    """Detect whether the car uses heave/third springs.

    Newer GT3 car models carry an explicit ``suspension_arch.has_heave_third``
    flag. Older car models (this branch) don't — fall back to "True" so
    legacy GTP cars keep emitting the heave-mode keys.
    """
    arch = getattr(car, "suspension_arch", None)
    if arch is None:
        return True
    flag = getattr(arch, "has_heave_third", None)
    if flag is None:
        return True
    return bool(flag)


def extract_suspension_psd(
    ibt: "IBTFile",
    car: "CarModel",
    *,
    lap_range: tuple[int, int] | None = None,
) -> dict[str, float | str]:
    """Compute ζ and ω_n per suspension mode for one IBT.

    Args:
        ibt: Open IBT file (provides ``channel`` / ``has_channel`` /
            ``tick_rate`` / per-car CarPath in ``car_info()``).
        car: Resolved :class:`CarModel`. Used only for architecture
            decisions — never to subselect by canonical name. Channel
            availability drives the actual extraction.
        lap_range: Optional ``(start_idx, end_idx)`` slice (e.g. the best-
            lap window already computed by ``extract_measurements``). If
            ``None``, the full IBT is used (gives a more stable PSD with
            more averaging segments).

    Returns:
        Flat dict of ``{<mode>_<metric>: value}``. Empty if no usable
        deflection channels are present.
    """
    fs = float(getattr(ibt, "tick_rate", 60.0)) or 60.0
    out: dict[str, float | str] = {}

    # Per-corner deflection channels (BMW, Porsche, Ferrari, Cadillac, GT3).
    lf = _safe_load(ibt, "LFshockDefl")
    rf = _safe_load(ibt, "RFshockDefl")
    lr = _safe_load(ibt, "LRshockDefl")
    rr = _safe_load(ibt, "RRshockDefl")

    has_per_corner = all(arr is not None for arr in (lf, rf, lr, rr))
    use_heave_third = _has_heave_third(car)

    if has_per_corner:
        lf = _best_lap_slice(ibt, lf, lap_range)
        rf = _best_lap_slice(ibt, rf, lap_range)
        lr = _best_lap_slice(ibt, lr, lap_range)
        rr = _best_lap_slice(ibt, rr, lap_range)

        _emit_mode(out, "lf", _welch_peak(lf, fs))
        _emit_mode(out, "rf", _welch_peak(rf, fs))
        _emit_mode(out, "lr", _welch_peak(lr, fs))
        _emit_mode(out, "rr", _welch_peak(rr, fs))

        # Axle-heave mode = sum of the two corners (rejects roll, isolates
        # heave). Only emit under the "heave" name when the architecture
        # actually carries a heave/third spring; for GT3 (no heave/third)
        # this is still a useful axle-mode reading, but we leave the
        # heave-named keys empty to avoid implying a separate spring.
        if use_heave_third:
            front_heave_sig = (lf + rf) * 0.5
            rear_heave_sig = (lr + rr) * 0.5
            _emit_mode(out, "front_heave", _welch_peak(front_heave_sig, fs))
            _emit_mode(out, "rear_heave", _welch_peak(rear_heave_sig, fs))
        else:
            front_axle_sig = (lf + rf) * 0.5
            rear_axle_sig = (lr + rr) * 0.5
            _emit_mode(out, "front_axle", _welch_peak(front_axle_sig, fs))
            _emit_mode(out, "rear_axle", _welch_peak(rear_axle_sig, fs))

    # Heave / roll architecture (Acura ORECA). Use the explicit channels.
    hf = _safe_load(ibt, "HFshockDefl")
    hr = _safe_load(ibt, "HRshockDefl")
    froll = _safe_load(ibt, "FROLLshockDefl")
    rroll = _safe_load(ibt, "RROLLshockDefl")

    if hf is not None and use_heave_third:
        _emit_mode(out, "front_heave", _welch_peak(_best_lap_slice(ibt, hf, lap_range), fs))
    if hr is not None and use_heave_third:
        _emit_mode(out, "rear_heave", _welch_peak(_best_lap_slice(ibt, hr, lap_range), fs))
    if froll is not None:
        _emit_mode(out, "front_roll", _welch_peak(_best_lap_slice(ibt, froll, lap_range), fs))
    if rroll is not None:
        _emit_mode(out, "rear_roll", _welch_peak(_best_lap_slice(ibt, rroll, lap_range), fs))

    return out
