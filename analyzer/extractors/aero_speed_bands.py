"""Speed-band stratified aero compression extractor.

A single IBT collapses a full lap to one ``aero_compression_front_mm`` /
``aero_compression_rear_mm`` scalar (static_RH - mean_RH at speed). This
extractor stratifies the same lap by speed bin and reports compression
per bin, giving multiple independent ``(V^2, compression)`` pairs from a
single IBT.

Physically, aero compression scales as ``compression(V) = alpha * V^2 + beta``
where ``alpha`` is the per-axle aero compression coefficient previously
requiring multi-session sweeps to estimate. With four well-populated speed
bins from one IBT we can fit ``alpha`` directly.

The extractor is car-agnostic — it uses ``CarModel`` direct attribute
access for any car-specific values it needs (currently it does NOT need
any car attributes; the aero-map lookup is best-effort and gracefully
degrades to a V^2-only fit when the map is unavailable for the car).

Output dict keys (all values in mm except ``alpha_*`` which is mm per
``(km/h)^2`` and ``samples_*`` which is int)::

    front_100_150, front_150_200, front_200_250, front_250_400  # per-bin compression
    rear_100_150,  rear_150_200,  rear_200_250,  rear_250_400
    samples_100_150, samples_150_200, ...                       # samples per bin
    v2_mid_100_150, ...                                         # mean V^2 in bin
    alpha_front, beta_front, r2_front, n_bins_front             # fitted alpha (mm/(km/h)^2)
    alpha_rear,  beta_rear,  r2_rear,  n_bins_rear

If a bin has fewer than ``min_samples_per_bin`` clean-aero samples the
keys for that bin are omitted (caller must handle missing keys).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from car_model.cars import CarModel
    from track_model.ibt_parser import IBTFile


_DEFAULT_BINS_KPH: tuple[tuple[float, float], ...] = (
    (100.0, 150.0),
    (150.0, 200.0),
    (200.0, 250.0),
    (250.0, 400.0),
)

# Bins narrower than this often see no samples on tracks where the speed
# range is concentrated; we still emit keys for the populated bins.
_MIN_SAMPLES_PER_BIN = 30

# Brake threshold for "clean aero" (matches at_speed mask in extract.py).
_CLEAN_BRAKE_THRESHOLD = 0.05


def _bin_label(lo: float, hi: float) -> str:
    """Format a speed-bin label such as ``100_150`` from the tuple bounds."""
    return f"{int(round(lo))}_{int(round(hi))}"


def _fit_v2_line(
    v2_mids: list[float],
    compressions: list[float],
) -> tuple[float | None, float | None, float | None]:
    """Least-squares fit ``compression = alpha * V^2 + beta``.

    Returns ``(alpha, beta, r2)`` or ``(None, None, None)`` if the fit is
    underdetermined (fewer than two distinct V^2 points).
    """
    if len(v2_mids) < 2:
        return (None, None, None)
    x = np.asarray(v2_mids, dtype=float)
    y = np.asarray(compressions, dtype=float)
    if np.unique(x).size < 2:
        return (None, None, None)
    # np.polyfit deg=1 returns [slope, intercept]
    slope, intercept = np.polyfit(x, y, 1)
    y_pred = slope * x + intercept
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - float(np.mean(y))) ** 2))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 1.0
    return (float(slope), float(intercept), float(r2))


def extract_aero_compression_by_speed_band(
    ibt: "IBTFile",
    car: "CarModel",
    *,
    start: int = 0,
    end: int | None = None,
    speed_bins_kph: tuple[tuple[float, float], ...] = _DEFAULT_BINS_KPH,
    min_samples_per_bin: int = _MIN_SAMPLES_PER_BIN,
) -> dict[str, float | int | str]:
    """Return per-speed-band aero compression and a fitted V^2 coefficient.

    Args:
        ibt: Open ``IBTFile``. Caller is responsible for opening it.
        car: ``CarModel`` for the car. Used only for car-aware logging /
            future expected-DF computation; no BMW defaults are assumed.
        start: First sample index (inclusive). Defaults to whole-file.
        end: Last sample index (inclusive). ``None`` means last sample.
        speed_bins_kph: Tuple of ``(lo, hi)`` kph pairs. Default is
            ``((100,150),(150,200),(200,250),(250,400))``.
        min_samples_per_bin: Minimum clean-aero samples required to emit
            a per-bin compression value.

    Returns:
        Dict with per-bin keys (front/rear/samples/v2_mid) and fitted
        ``alpha_front`` / ``alpha_rear`` (mm per ``(km/h)^2``). Empty
        dict on missing channels.
    """
    required_rh = ("LFrideHeight", "RFrideHeight", "LRrideHeight", "RRrideHeight")
    if not all(ibt.has_channel(c) for c in required_rh):
        return {}
    if not ibt.has_channel("Speed"):
        return {}

    if end is None:
        end = int(ibt.channel("Speed").shape[0]) - 1
    if end < start:
        return {}

    sl = slice(start, end + 1)
    speed_kph = ibt.channel("Speed")[sl] * 3.6
    n = speed_kph.shape[0]
    if n < min_samples_per_bin:
        return {}

    # m -> mm, axle-averaged. Mirrors analyzer/extract.py L516-522.
    lf_rh = ibt.channel("LFrideHeight")[sl] * 1000.0
    rf_rh = ibt.channel("RFrideHeight")[sl] * 1000.0
    lr_rh = ibt.channel("LRrideHeight")[sl] * 1000.0
    rr_rh = ibt.channel("RRrideHeight")[sl] * 1000.0
    front_rh = (lf_rh + rf_rh) / 2.0
    rear_rh = (lr_rh + rr_rh) / 2.0

    if ibt.has_channel("Brake"):
        brake = ibt.channel("Brake")[sl]
    else:
        brake = np.zeros(n)

    # Static (sensor-frame) RH from pit-speed samples; fall back to p95
    # of all samples if the IBT lacks a long-enough pit segment. This
    # mirrors the existing scalar logic in analyzer/extract.py L566-572.
    pit_mask = speed_kph < 5.0
    if int(np.sum(pit_mask)) > 20:
        static_front = float(np.mean(front_rh[pit_mask]))
        static_rear = float(np.mean(rear_rh[pit_mask]))
    else:
        static_front = float(np.percentile(front_rh, 95))
        static_rear = float(np.percentile(rear_rh, 95))

    if not (static_front > 0 and static_rear > 0):
        return {}

    clean_aero = brake < _CLEAN_BRAKE_THRESHOLD

    out: dict[str, float | int | str] = {}
    v2_mids_front: list[float] = []
    comps_front: list[float] = []
    v2_mids_rear: list[float] = []
    comps_rear: list[float] = []

    for lo, hi in speed_bins_kph:
        bin_mask = (speed_kph >= lo) & (speed_kph < hi) & clean_aero
        n_bin = int(np.sum(bin_mask))
        label = _bin_label(lo, hi)
        if n_bin < min_samples_per_bin:
            continue

        bin_speed = speed_kph[bin_mask]
        v2_mid = float(np.mean(bin_speed * bin_speed))
        mean_front_bin = float(np.mean(front_rh[bin_mask]))
        mean_rear_bin = float(np.mean(rear_rh[bin_mask]))
        comp_front = static_front - mean_front_bin
        comp_rear = static_rear - mean_rear_bin

        out[f"front_{label}"] = round(comp_front, 4)
        out[f"rear_{label}"] = round(comp_rear, 4)
        out[f"samples_{label}"] = int(n_bin)
        out[f"v2_mid_{label}"] = round(v2_mid, 2)

        # Only feed positive compressions into the V^2 fit. Below 100 kph
        # aero load is negligible and the difference is dominated by
        # static-RH measurement noise, which can flip sign.
        if comp_front > 0:
            v2_mids_front.append(v2_mid)
            comps_front.append(comp_front)
        if comp_rear > 0:
            v2_mids_rear.append(v2_mid)
            comps_rear.append(comp_rear)

    alpha_f, beta_f, r2_f = _fit_v2_line(v2_mids_front, comps_front)
    alpha_r, beta_r, r2_r = _fit_v2_line(v2_mids_rear, comps_rear)

    if alpha_f is not None:
        out["alpha_front"] = round(alpha_f, 8)
        out["beta_front"] = round(beta_f, 4) if beta_f is not None else 0.0
        out["r2_front"] = round(r2_f, 4) if r2_f is not None else 0.0
        out["n_bins_front"] = int(len(v2_mids_front))
    if alpha_r is not None:
        out["alpha_rear"] = round(alpha_r, 8)
        out["beta_rear"] = round(beta_r, 4) if beta_r is not None else 0.0
        out["r2_rear"] = round(r2_r, 4) if r2_r is not None else 0.0
        out["n_bins_rear"] = int(len(v2_mids_rear))

    # Car name is recorded so multi-IBT aggregators can partition without
    # back-resolving from the IBT path.
    out["car_canonical"] = car.canonical_name

    return out
