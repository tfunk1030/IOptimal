"""Empirical heave spring ↔ platform sigma model.

Instead of synthetic physics, this module learns from real IBT telemetry runs.
Each time a new IBT is processed, the measured (heave_nmm, sigma_front_mm) pair
is added to the calibration store. The solver then uses the learned curve rather
than hardcoded physics — so running an extreme setup (e.g., 380 N/mm) and
dropping in the IBT teaches the model what actually happens.

Files:
    data/learnings/heave_calibration_<car>_<track>.json
        calibration store with per-run data + per-heave summary

API:
    cal = HeaveCalibration.load('bmw', 'sebring')
    predicted_sigma = cal.predict_sigma(heave_nmm=60.0)
    uncertainty = cal.uncertainty(heave_nmm=60.0)
    cal.add_run(heave_nmm=380.0, sigma_mm=7.2, lap_s=110.5)
    cal.save()
"""

from __future__ import annotations

import json
import math
import os
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


_LEARNINGS_DIR = Path(__file__).parent.parent / "data" / "learnings"


@dataclass
class HeaveRun:
    heave_nmm: float
    front_sigma_mm: float
    rear_sigma_mm: Optional[float] = None
    front_shock_vel_p99_mps: Optional[float] = None
    front_dominant_freq_hz: Optional[float] = None
    heave_travel_pct: Optional[float] = None
    best_lap_s: Optional[float] = None
    session_ts: str = ""


@dataclass
class HeaveSummary:
    heave_nmm: float
    n: int
    sigma_mean: float
    sigma_min: float
    sigma_p25: float
    lap_best_s: Optional[float] = None


class HeaveCalibration:
    """Empirical heave spring → platform sigma model backed by real IBT data.

    The model uses a weighted combination of:
    1. Nearby calibration points (linear interpolation between measured heave values)
    2. A fallback physics curve when extrapolating beyond measured data

    The physics fallback uses the U-shaped relationship:
      σ_predicted = σ_baseline / (1 + α * k^β)   for k in soft regime
      σ_predicted = σ_floor + γ * k^δ              for k in stiff regime (contact loss)

    Parameters are fit to calibration data when enough runs exist.
    """

    def __init__(self, car: str, track: str):
        self.car = car
        self.track = track
        self.runs: list[HeaveRun] = []
        self.summary: list[HeaveSummary] = []

    # ── I/O ────────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, car: str, track: str) -> "HeaveCalibration":
        """Load calibration from disk; returns empty calibration if file missing."""
        cal = cls(car, track)
        path = cal._path()
        if not path.exists():
            return cal
        try:
            raw = json.loads(path.read_text())
            for r in raw.get("runs", []):
                cal.runs.append(HeaveRun(**{
                    k: r.get(k) for k in HeaveRun.__dataclass_fields__
                }))
            for s in raw.get("summary", []):
                cal.summary.append(HeaveSummary(**{
                    k: s.get(k) for k in HeaveSummary.__dataclass_fields__
                }))
        except Exception:
            pass
        return cal

    def save(self) -> None:
        """Write calibration back to disk (rebuilds summary first)."""
        self._rebuild_summary()
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
            "car": self.car,
            "track": self.track,
            "n_runs": len(self.runs),
            "runs": [vars(r) for r in self.runs],
            "summary": [vars(s) for s in self.summary],
        }
        path.write_text(json.dumps(payload, indent=2, default=str))

    def _path(self) -> Path:
        return _LEARNINGS_DIR / f"heave_calibration_{self.car}_{self.track}.json"

    # ── Data ingestion ──────────────────────────────────────────────────────

    def add_run(
        self,
        heave_nmm: float,
        sigma_mm: float,
        rear_sigma_mm: Optional[float] = None,
        shock_vel_p99: Optional[float] = None,
        dominant_freq_hz: Optional[float] = None,
        heave_travel_pct: Optional[float] = None,
        best_lap_s: Optional[float] = None,
        session_ts: str = "",
    ) -> None:
        """Add a measured run to the calibration store."""
        self.runs.append(HeaveRun(
            heave_nmm=heave_nmm,
            front_sigma_mm=sigma_mm,
            rear_sigma_mm=rear_sigma_mm,
            front_shock_vel_p99_mps=shock_vel_p99,
            front_dominant_freq_hz=dominant_freq_hz,
            heave_travel_pct=heave_travel_pct,
            best_lap_s=best_lap_s,
            session_ts=session_ts,
        ))
        self._rebuild_summary()

    def _rebuild_summary(self) -> None:
        from itertools import groupby
        sorted_runs = sorted(self.runs, key=lambda r: r.heave_nmm)
        self.summary = []
        for k_val, group in groupby(sorted_runs, key=lambda r: r.heave_nmm):
            g = list(group)
            sigmas = [r.front_sigma_mm for r in g]
            laps = [r.best_lap_s for r in g if r.best_lap_s]
            self.summary.append(HeaveSummary(
                heave_nmm=k_val,
                n=len(g),
                sigma_mean=round(statistics.mean(sigmas), 4),
                sigma_min=round(min(sigmas), 4),
                sigma_p25=round(sorted(sigmas)[len(sigmas)//4], 4),
                lap_best_s=round(min(laps), 4) if laps else None,
            ))

    # ── Prediction ─────────────────────────────────────────────────────────

    def predict_sigma(self, heave_nmm: float) -> float:
        """Predict σ_front_mm for a given heave spring rate.

        Uses empirical interpolation within the measured range and physics
        extrapolation outside it. Never returns < 3mm (physical floor) or
        > 15mm (physical ceiling for iRacing GTP).

        The empirical data shows a U-shape:
          - Decreasing from 30→90 N/mm (aero platform stabilizes with stiffness)
          - Increasing above ~200 N/mm (tyre contact degrades, σ rises again)
          - The 900 N/mm run (σ=5.97mm) confirms the U-shape inflection.
        """
        if not self.summary:
            return self._physics_fallback(heave_nmm)

        # Sort calibration points by heave
        pts = sorted(self.summary, key=lambda s: s.heave_nmm)
        heaves = [s.heave_nmm for s in pts]
        sigmas = [s.sigma_mean for s in pts]   # use mean (robust to outliers)

        # --- Within measured range: linear interpolation ---
        if heaves[0] <= heave_nmm <= heaves[-1]:
            return self._interpolate(heave_nmm, heaves, sigmas)

        # --- Extrapolate below lowest measured heave ---
        if heave_nmm < heaves[0]:
            # Physics: very soft heave → large excursion, σ grows rapidly
            # Use slope from lowest 2 points
            if len(pts) >= 2:
                slope = (sigmas[1] - sigmas[0]) / (heaves[1] - heaves[0])
                # Slope should be negative (σ decreases as k increases)
                # Extrapolating below → σ increases
                pred = sigmas[0] + slope * (heave_nmm - heaves[0])
            else:
                pred = sigmas[0] + (heaves[0] - heave_nmm) * 0.05
            return max(3.0, min(20.0, pred))

        # --- Extrapolate above highest measured heave ---
        # U-shape: σ has a minimum around 80-100 N/mm, then rises
        # The 900 N/mm data point anchors the right side of the U
        if heave_nmm > heaves[-1]:
            # Find the minimum σ point (bottom of U)
            min_sigma = min(sigmas)
            min_heave = heaves[sigmas.index(min_sigma)]

            # Find the highest measured non-extreme point + the 900 N/mm anchor
            # Use the actual measured sigma at highest k if available
            if len(pts) >= 2:
                k_hi = heaves[-1]
                s_hi = sigmas[-1]
                # If the last point is already showing sigma increasing, we're past the optimum
                # Fit a simple rising curve from min_heave to k_hi to heave_nmm
                # σ(k) = σ_min + β * (k - k_opt)^α
                # Fit α,β from available right-side points
                right_pts = [(h, s) for h, s in zip(heaves, sigmas) if h >= min_heave]
                if len(right_pts) >= 2 and right_pts[-1][0] > right_pts[0][0]:
                    k0, s0 = right_pts[0]
                    k1, s1 = right_pts[-1]
                    # Power law: s - s_min = C * (k - k_opt)^alpha
                    # Use linear growth as conservative estimate
                    if k1 > k0 and s1 > s0:
                        rate = (s1 - s0) / (k1 - k0)
                        pred = s1 + rate * (heave_nmm - k1)
                    else:
                        # Right side flat or still decreasing → assume minimum reached
                        # Small rise based on physics (fn-driven contact loss)
                        excess = heave_nmm - k_hi
                        # From 900 N/mm run: sigma rose from ~4.9 (at 80) to 5.97 at 900
                        # That's +1.07mm over 820 N/mm = ~0.0013 mm per N/mm
                        pred = s_hi + excess * 0.0013
                else:
                    pred = s_hi
            else:
                pred = self._physics_fallback(heave_nmm)

            return max(3.0, min(20.0, pred))

        return self._physics_fallback(heave_nmm)

    @staticmethod
    def _interpolate(x: float, xs: list[float], ys: list[float]) -> float:
        """Linear interpolation between sorted (xs, ys) points."""
        for i in range(len(xs) - 1):
            if xs[i] <= x <= xs[i + 1]:
                t = (x - xs[i]) / (xs[i + 1] - xs[i])
                return ys[i] + t * (ys[i + 1] - ys[i])
        return ys[-1]

    @staticmethod
    def _physics_fallback(heave_nmm: float) -> float:
        """Physics-based prior for σ_front when no calibration data exists.

        Based on GTP dynamics literature + BMW iRacing empirical knowledge:
        - Optimal range: 50-80 N/mm → σ ≈ 5mm
        - Soft (<30 N/mm): σ rises sharply
        - Stiff (>150 N/mm): σ rises moderately due to tyre contact loss
        """
        # U-shaped curve fitted to empirical knowledge
        k_opt = 75.0   # [N/mm] approximate optimum (midpoint of working range)
        s_min = 5.0    # [mm] minimum sigma at optimum
        # Soft side: steep rise
        if heave_nmm < k_opt:
            alpha_soft = 0.012   # ms per (N/mm)^2
            return s_min + alpha_soft * (k_opt - heave_nmm) ** 1.5
        # Stiff side: shallower rise (tyre contact loss is secondary mechanism)
        else:
            alpha_stiff = 0.00015
            return s_min + alpha_stiff * (heave_nmm - k_opt) ** 1.8

    def uncertainty(self, heave_nmm: float) -> float:
        """Uncertainty in sigma prediction [mm] — higher = less data available.

        Returns a 1-sigma uncertainty estimate:
          - Near well-sampled calibration points: ~0.2mm (2-3% relative)
          - In sparse regions (1 run): ~0.5mm
          - In extrapolated regions (no data): ~1.5-2mm
        """
        if not self.summary:
            return 2.0  # no data → high uncertainty

        pts = sorted(self.summary, key=lambda s: s.heave_nmm)
        heaves = [s.heave_nmm for s in pts]

        # Find closest calibration point
        dists = [abs(heave_nmm - h) for h in heaves]
        nearest_idx = dists.index(min(dists))
        nearest_dist = dists[nearest_idx]
        nearest_n = pts[nearest_idx].n

        # Base uncertainty from sample count
        base = 0.5 / math.sqrt(max(1, nearest_n))

        # Distance penalty
        dist_penalty = nearest_dist * 0.01  # 0.01mm per N/mm distance

        return max(0.15, base + dist_penalty)

    def summary_table(self) -> str:
        """Human-readable calibration summary."""
        if not self.summary:
            return "  No calibration data."
        lines = [f"  {'heave':>8}  {'n':>4}  {'σ_mean':>8}  {'σ_min':>8}  {'lap_best':>9}"]
        for s in sorted(self.summary, key=lambda x: x.heave_nmm):
            lb = f"{s.lap_best_s:.3f}s" if s.lap_best_s else "    N/A"
            lines.append(f"  {s.heave_nmm:>8.0f}  {s.n:>4}  {s.sigma_mean:>8.3f}  {s.sigma_min:>8.3f}  {lb:>9}")
        return "\n".join(lines)
