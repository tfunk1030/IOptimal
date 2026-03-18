"""
session_database.py — Multi-dimensional empirical session database.

Replaces the single-axis HeaveCalibration with a full k-NN predictor
over the entire setup × telemetry parameter space.

Every IBT session is a data point:
  - SETUP VECTOR:  all setup parameters (heave, ARBs, camber, dampers, diff, …)
  - TELEMETRY VECTOR:  all measured outcomes (σ_front, understeer, LLTD, tyre
                       temps, heave travel, shock velocities, body slip, …)
  - PERFORMANCE:  best_lap_s, consistency_cv

Given a proposed setup, the database:
  1. Computes weighted Euclidean distance to all stored sessions
  2. Returns k-NN weighted predictions for every telemetry metric
  3. Scores each predicted metric against physics-based targets
  4. Produces a human-readable per-metric "why" explanation

Usage:
    db = SessionDatabase.load('bmw', 'sebring')
    pred = db.predict(setup_params)
    score, breakdown = db.score(pred)
    print(db.explain(setup_params, k=5))
"""

from __future__ import annotations

import json
import math
import os
import glob
from dataclasses import dataclass, field, asdict
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Setup parameter extraction from observation dict
# ─────────────────────────────────────────────────────────────────────────────

SETUP_KEYS_NUMERIC: list[str] = [
    "front_heave_nmm",
    "rear_third_nmm",
    "torsion_bar_od_mm",
    "rear_spring_nmm",
    "front_arb_blade",
    "rear_arb_blade",
    "front_camber_deg",
    "rear_camber_deg",
    "front_toe_mm",
    "rear_toe_mm",
    "brake_bias_pct",
    "diff_preload_nm",
    "diff_clutch_plates",
    "tc_gain",
    "tc_slip",
    "front_rh_static",
    "rear_rh_static",
]

# Per-corner damper keys (flattened: lf_ls_comp, etc.)
DAMPER_CORNERS = ["lf", "rf", "lr", "rr"]
DAMPER_AXES = ["ls_comp", "ls_rbd", "hs_comp", "hs_rbd", "hs_slope"]

# Telemetry fields to predict (must be numeric, present in most sessions)
TELEMETRY_PREDICT_KEYS: list[str] = [
    "front_rh_std_mm",
    "rear_rh_std_mm",
    "dynamic_front_rh_mm",
    "dynamic_rear_rh_mm",
    "lltd_measured",
    "lltd_low_speed",
    "lltd_high_speed",
    "understeer_mean_deg",
    "understeer_high_speed_deg",
    "understeer_low_speed_deg",
    "understeer_left_turn_deg",
    "understeer_right_turn_deg",
    "body_roll_p95_deg",
    "roll_gradient_deg_per_g",
    "body_slip_p95_deg",
    "front_heave_travel_used_pct",
    "front_heave_travel_used_braking_pct",
    "front_heave_defl_p99_mm",
    "heave_bottoming_events_front",
    "front_bottoming_events",
    "rear_bottoming_events",
    "front_shock_vel_p95_mps",
    "rear_shock_vel_p95_mps",
    "front_shock_vel_p99_mps",
    "rear_shock_vel_p99_mps",
    "lf_shock_vel_p95_mps",
    "rf_shock_vel_p95_mps",
    "lr_shock_vel_p95_mps",
    "rr_shock_vel_p95_mps",
    "front_shock_oscillation_hz",
    "rear_shock_oscillation_hz",
    "front_dominant_freq_hz",
    "rear_dominant_freq_hz",
    "front_heave_vel_p95_mps",
    "front_heave_vel_ls_pct",
    "front_heave_vel_hs_pct",
    "lf_pressure_kpa",
    "rf_pressure_kpa",
    "lr_pressure_kpa",
    "rr_pressure_kpa",
    "lf_temp_middle_c",
    "rf_temp_middle_c",
    "lr_temp_middle_c",
    "rr_temp_middle_c",
    "lf_wear_pct",
    "rf_wear_pct",
    "lr_wear_pct",
    "rr_wear_pct",
    "pitch_mean_at_speed_deg",
    "pitch_range_deg",
    "braking_decel_peak_g",
    "abs_active_pct",
    "tc_intervention_pct",
    "ers_battery_mean_pct",
    "ers_battery_min_pct",
    "splitter_rh_mean_at_speed_mm",
    "splitter_rh_min_mm",
    "splitter_scrape_events",
]

# Importance weights for setup parameter distance (higher = more influential)
SETUP_PARAM_WEIGHTS: dict[str, float] = {
    "front_heave_nmm": 3.0,
    "rear_third_nmm": 2.0,
    "front_arb_blade": 2.5,
    "rear_arb_blade": 2.5,
    "torsion_bar_od_mm": 2.0,
    "rear_spring_nmm": 1.5,
    "front_camber_deg": 1.5,
    "rear_camber_deg": 1.5,
    "diff_preload_nm": 1.5,
    "brake_bias_pct": 1.0,
    "front_rh_static": 2.0,
    "rear_rh_static": 2.0,
    "tc_gain": 0.5,
    "tc_slip": 0.5,
    # Dampers: moderate weight, symmetric penalties
    "lf_ls_comp": 1.2, "rf_ls_comp": 1.2,
    "lr_ls_comp": 1.2, "rr_ls_comp": 1.2,
    "lf_ls_rbd": 0.8, "rf_ls_rbd": 0.8,
    "lr_ls_rbd": 0.8, "rr_ls_rbd": 0.8,
    "lf_hs_comp": 1.0, "rf_hs_comp": 1.0,
    "lr_hs_comp": 1.0, "rr_hs_comp": 1.0,
    "lf_hs_rbd": 0.8, "rf_hs_rbd": 0.8,
    "lr_hs_rbd": 0.8, "rr_hs_rbd": 0.8,
}
DEFAULT_PARAM_WEIGHT = 0.5

# Per-metric: which setup params are most influential (for metric-specific k-NN)
# Keys not listed use DEFAULT_PARAM_WEIGHT; listed keys override SETUP_PARAM_WEIGHTS
METRIC_PARAM_FOCUS: dict[str, dict[str, float]] = {
    "front_rh_std_mm": {
        "front_heave_nmm": 10.0, "rear_third_nmm": 4.0,
        "front_arb_blade": 3.0, "rear_arb_blade": 2.0,
        "lf_ls_comp": 2.0, "rf_ls_comp": 2.0, "lf_hs_comp": 1.5,
    },
    "front_heave_travel_used_pct": {
        "front_heave_nmm": 10.0, "rear_third_nmm": 5.0,
        "front_rh_static": 3.0,
    },
    "heave_bottoming_events_front": {
        "front_heave_nmm": 10.0, "rear_third_nmm": 5.0,
        "front_rh_static": 3.0,
    },
    "front_bottoming_events": {
        "front_heave_nmm": 8.0, "rear_third_nmm": 4.0,
        "front_rh_static": 3.0,
    },
    "rear_rh_std_mm": {
        "rear_third_nmm": 8.0, "rear_spring_nmm": 4.0,
        "rear_arb_blade": 3.0, "lr_ls_comp": 2.0, "rr_ls_comp": 2.0,
    },
    "understeer_high_speed_deg": {
        "front_arb_blade": 5.0, "rear_arb_blade": 5.0,
        "front_camber_deg": 4.0, "rear_camber_deg": 4.0,
        "torsion_bar_od_mm": 3.0, "rear_spring_nmm": 2.0,
    },
    "understeer_low_speed_deg": {
        "front_arb_blade": 5.0, "rear_arb_blade": 5.0,
        "front_camber_deg": 4.0, "diff_preload_nm": 3.0,
    },
    "lltd_measured": {
        "front_arb_blade": 6.0, "rear_arb_blade": 6.0,
        "torsion_bar_od_mm": 4.0, "rear_spring_nmm": 3.0,
        "front_heave_nmm": 2.0,
    },
    "body_slip_p95_deg": {
        "rear_arb_blade": 4.0, "rear_third_nmm": 3.0,
        "rear_camber_deg": 3.0, "diff_preload_nm": 2.0,
        "lr_ls_comp": 2.0, "rr_ls_comp": 2.0,
    },
    "lf_pressure_kpa": {"front_heave_nmm": 3.0, "front_arb_blade": 2.0},
    "lr_pressure_kpa": {"rear_third_nmm": 3.0, "rear_arb_blade": 2.0},
}

# ─────────────────────────────────────────────────────────────────────────────
# Telemetry targets (physics-based)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TelemetryTargets:
    """Physics-based targets for every predicted telemetry metric."""
    front_rh_std_mm: tuple[float, float] = (0.0, 3.5)       # (ideal_max)
    rear_rh_std_mm: tuple[float, float] = (0.0, 5.0)
    lltd_measured: tuple[float, float] = (0.510, 0.540)     # 51-54% balanced
    understeer_high_speed_deg: tuple[float, float] = (-0.05, 0.12)
    understeer_low_speed_deg: tuple[float, float] = (-0.10, 0.08)
    body_slip_p95_deg: tuple[float, float] = (0.0, 3.5)
    front_heave_travel_used_pct: tuple[float, float] = (0.0, 78.0)  # <78% avoids nearstop
    heave_bottoming_events_front: tuple[float, float] = (0.0, 0.0)  # zero tolerance
    front_bottoming_events: tuple[float, float] = (0.0, 0.0)
    rear_bottoming_events: tuple[float, float] = (0.0, 2.0)         # some rear OK
    body_roll_p95_deg: tuple[float, float] = (0.0, 2.5)
    roll_gradient_deg_per_g: tuple[float, float] = (-0.20, -0.05)   # neg=understeer direction
    front_shock_vel_p99_mps: tuple[float, float] = (0.0, 0.35)      # LS/HS threshold ~0.25
    rear_shock_vel_p99_mps: tuple[float, float] = (0.0, 0.40)
    lf_pressure_kpa: tuple[float, float] = (183.0, 189.0)
    rf_pressure_kpa: tuple[float, float] = (183.0, 189.0)
    lr_pressure_kpa: tuple[float, float] = (180.0, 186.0)
    rr_pressure_kpa: tuple[float, float] = (180.0, 186.0)
    lf_temp_middle_c: tuple[float, float] = (70.0, 100.0)
    rf_temp_middle_c: tuple[float, float] = (70.0, 100.0)
    lr_temp_middle_c: tuple[float, float] = (70.0, 100.0)
    rr_temp_middle_c: tuple[float, float] = (70.0, 100.0)
    splitter_scrape_events: tuple[float, float] = (0.0, 0.0)
    tc_intervention_pct: tuple[float, float] = (0.0, 5.0)
    abs_active_pct: tuple[float, float] = (0.0, 3.0)


# Scoring weights per metric (contribution to total score)
METRIC_SCORE_WEIGHTS: dict[str, float] = {
    "front_rh_std_mm": 25.0,            # platform stability #1
    "rear_rh_std_mm": 15.0,             # platform stability #2
    "understeer_high_speed_deg": 15.0,  # aero balance
    "understeer_low_speed_deg": 12.0,   # mechanical balance
    "lltd_measured": 12.0,              # weight transfer distribution
    "body_slip_p95_deg": 10.0,          # rear rotation control
    "front_heave_travel_used_pct": 8.0, # travel margin safety
    "heave_bottoming_events_front": 8.0,# hard veto condition
    "front_bottoming_events": 6.0,
    "rear_bottoming_events": 3.0,
    "body_roll_p95_deg": 5.0,
    "front_shock_vel_p99_mps": 5.0,
    "rear_shock_vel_p99_mps": 5.0,
    "lf_pressure_kpa": 3.0,
    "rf_pressure_kpa": 3.0,
    "lr_pressure_kpa": 3.0,
    "rr_pressure_kpa": 3.0,
    "splitter_scrape_events": 4.0,
    "tc_intervention_pct": 3.0,
    "abs_active_pct": 2.0,
}

# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SessionRecord:
    session_id: str
    setup_vector: dict[str, float]     # normalised setup params
    telemetry_vector: dict[str, float] # measured outcomes
    best_lap_s: Optional[float]
    consistency_cv: Optional[float]


@dataclass
class PredictionResult:
    """k-NN weighted prediction for a proposed setup."""
    predicted: dict[str, float]        # metric → predicted value
    confidence: dict[str, float]       # metric → confidence [0-1]
    best_lap_predicted_s: Optional[float]
    k_used: int
    neighbor_ids: list[str]
    neighbor_distances: list[float]
    neighbor_setups: list[dict]        # for "why" explanations


@dataclass
class MetricScore:
    metric: str
    predicted: float
    target_lo: float
    target_hi: float
    penalty_ms: float
    status: str                        # "ok", "warn", "bad", "veto"
    note: str


@dataclass
class ScoreResult:
    total_penalty_ms: float
    metrics: list[MetricScore]
    best_lap_bonus_ms: float           # lap time delta from mean


# ─────────────────────────────────────────────────────────────────────────────
# SessionDatabase
# ─────────────────────────────────────────────────────────────────────────────

class SessionDatabase:
    """Full multi-dimensional empirical session database."""

    def __init__(self, car: str, track: str):
        self.car = car
        self.track = track
        self.sessions: list[SessionRecord] = []
        self._normalisation: dict[str, tuple[float, float]] = {}  # key → (mean, std)

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, car: str, track: str, obs_dir: str | None = None) -> "SessionDatabase":
        db = cls(car, track)
        if obs_dir is None:
            here = os.path.dirname(__file__)
            obs_dir = os.path.join(here, "..", "data", "learnings", "observations")
        obs_dir = os.path.normpath(obs_dir)
        pattern = os.path.join(obs_dir, f"{car}_{track.replace(' ', '_').lower()}*.json")
        files = sorted(glob.glob(pattern))
        for fpath in files:
            try:
                db._load_observation(fpath)
            except Exception:
                pass
        db._build_normalisation()
        return db

    def _load_observation(self, fpath: str) -> None:
        with open(fpath) as f:
            d = json.load(f)
        if d.get("car") != self.car:
            return
        setup_vec = self._extract_setup_vector(d.get("setup", {}))
        tel_vec = self._extract_telemetry_vector(d.get("telemetry", {}))
        if not setup_vec or not tel_vec:
            return
        perf = d.get("performance", {})
        rec = SessionRecord(
            session_id=d.get("session_id", os.path.basename(fpath)),
            setup_vector=setup_vec,
            telemetry_vector=tel_vec,
            best_lap_s=perf.get("best_lap_time_s"),
            consistency_cv=perf.get("consistency_cv"),
        )
        self.sessions.append(rec)

    @staticmethod
    def _extract_setup_vector(setup: dict) -> dict[str, float]:
        vec: dict[str, float] = {}
        for k in SETUP_KEYS_NUMERIC:
            v = setup.get(k)
            if v is not None:
                try:
                    vec[k] = float(v)
                except (ValueError, TypeError):
                    pass
        # Flatten dampers
        dampers = setup.get("dampers", {})
        for corner in DAMPER_CORNERS:
            cdict = dampers.get(corner, {})
            for axis in DAMPER_AXES:
                v = cdict.get(axis)
                if v is not None:
                    try:
                        vec[f"{corner}_{axis}"] = float(v)
                    except (ValueError, TypeError):
                        pass
        return vec

    @staticmethod
    def _extract_telemetry_vector(tel: dict) -> dict[str, float]:
        vec: dict[str, float] = {}
        for k in TELEMETRY_PREDICT_KEYS:
            v = tel.get(k)
            if v is not None:
                try:
                    vec[k] = float(v)
                except (ValueError, TypeError):
                    pass
        return vec

    def _build_normalisation(self) -> None:
        """
        Compute per-parameter (median, IQR-based scale) for distance normalisation.
        Uses IQR (Q75-Q25) instead of std to avoid outlier inflation (e.g. 900 N/mm heave).
        Falls back to range/4 if IQR is zero.
        """
        all_keys: set[str] = set()
        for s in self.sessions:
            all_keys.update(s.setup_vector.keys())
        for k in all_keys:
            vals = sorted(s.setup_vector[k] for s in self.sessions if k in s.setup_vector)
            n = len(vals)
            if n < 2:
                self._normalisation[k] = (vals[0] if vals else 0.0, 1.0)
                continue
            median = vals[n // 2]
            q1 = vals[n // 4]
            q3 = vals[(3 * n) // 4]
            iqr = q3 - q1
            if iqr < 1e-6:
                # Fall back: range / 4
                val_range = (vals[-1] - vals[0]) / 4.0
                scale = val_range if val_range > 1e-6 else 1.0
            else:
                scale = iqr
            self._normalisation[k] = (median, scale)

    # ── k-NN prediction ──────────────────────────────────────────────────────

    def _setup_distance(
        self,
        a: dict[str, float],
        b: dict[str, float],
        metric_focus: dict[str, float] | None = None,
        exclusive: bool = False,
    ) -> float:
        """
        Weighted Euclidean distance in IQR-normalised setup space.

        metric_focus:  per-param weight overrides for metric-specific k-NN.
        exclusive:     if True, ONLY use params listed in metric_focus (ignore rest).
                       This prevents the curse of dimensionality from drowning the
                       relevant params in high-dimensional noise.
        """
        total = 0.0
        keys = set(a.keys()) | set(b.keys())
        for k in keys:
            if metric_focus is not None:
                if exclusive and k not in metric_focus:
                    continue  # skip non-focus params entirely
                w = metric_focus.get(k, SETUP_PARAM_WEIGHTS.get(k, DEFAULT_PARAM_WEIGHT))
            else:
                w = SETUP_PARAM_WEIGHTS.get(k, DEFAULT_PARAM_WEIGHT)
            if w == 0.0:
                continue
            va = a.get(k, 0.0)
            vb = b.get(k, 0.0)
            _, scale = self._normalisation.get(k, (0.0, 1.0))
            diff = (va - vb) / scale
            total += w * diff * diff
        return math.sqrt(total)

    def _knn_for_metric(
        self,
        proposed_setup: dict[str, float],
        metric: str,
        k: int,
    ) -> list[tuple[float, "SessionRecord"]]:
        """Return k nearest neighbors using metric-specific parameter focus (exclusive mode)."""
        focus = METRIC_PARAM_FOCUS.get(metric)
        exclusive = focus is not None
        dists = [
            (self._setup_distance(
                proposed_setup, s.setup_vector,
                metric_focus=focus, exclusive=exclusive
            ), s)
            for s in self.sessions
            if metric in s.telemetry_vector
        ]
        dists.sort(key=lambda x: x[0])
        return dists[:min(k, len(dists))]

    def predict(self, proposed_setup: dict[str, float], k: int = 7) -> PredictionResult:
        """
        Per-metric k-NN weighted prediction using metric-specific parameter focus.

        For each metric, uses the most relevant setup params to find nearest neighbors.
        Weights: inverse-distance squared (IDW).
        Confidence: 1 / (1 + weighted_variance_of_neighbors).
        """
        if not self.sessions:
            return PredictionResult(
                predicted={}, confidence={}, best_lap_predicted_s=None,
                k_used=0, neighbor_ids=[], neighbor_distances=[], neighbor_setups=[]
            )

        predicted: dict[str, float] = {}
        confidence: dict[str, float] = {}
        eps = 1e-6

        # Collect all telemetry keys seen across all sessions
        all_tel_keys: set[str] = set()
        for s in self.sessions:
            all_tel_keys.update(s.telemetry_vector.keys())

        # For each metric, do metric-specific k-NN
        for metric in all_tel_keys:
            neighbors = self._knn_for_metric(proposed_setup, metric, k)
            if not neighbors:
                continue
            raw_weights = [1.0 / (d + eps) ** 2 for d, _ in neighbors]
            total_w = sum(raw_weights) or 1.0
            weights = [w / total_w for w in raw_weights]

            vals_normed = [
                (s.telemetry_vector[metric], w)
                for w, (_, s) in zip(weights, neighbors)
            ]
            pred_val = sum(v * w for v, w in vals_normed)
            var = sum(w * (v - pred_val) ** 2 for v, w in vals_normed)
            predicted[metric] = pred_val
            confidence[metric] = 1.0 / (1.0 + var)

        # Global k-NN for lap time (uses all params equally)
        global_dists = [
            (self._setup_distance(proposed_setup, s.setup_vector), s)
            for s in self.sessions
        ]
        global_dists.sort(key=lambda x: x[0])
        global_neighbors = global_dists[:min(k, len(global_dists))]
        raw_w = [1.0 / (d + eps) ** 2 for d, _ in global_neighbors]
        total_gw = sum(raw_w) or 1.0
        gweights = [w / total_gw for w in raw_w]

        lap_vals = [
            (s.best_lap_s, w)
            for w, (_, s) in zip(gweights, global_neighbors)
            if s.best_lap_s
        ]
        best_lap_pred: Optional[float] = None
        if lap_vals:
            total_lw = sum(w for _, w in lap_vals) or 1.0
            best_lap_pred = sum(l * w / total_lw for l, w in lap_vals)

        return PredictionResult(
            predicted=predicted,
            confidence=confidence,
            best_lap_predicted_s=best_lap_pred,
            k_used=min(k, len(self.sessions)),
            neighbor_ids=[s.session_id for _, s in global_neighbors],
            neighbor_distances=[d for d, _ in global_neighbors],
            neighbor_setups=[s.setup_vector for _, s in global_neighbors],
        )

    # ── Scoring ───────────────────────────────────────────────────────────────

    def score(self, pred: PredictionResult, targets: TelemetryTargets | None = None) -> ScoreResult:
        """
        Score predicted telemetry against targets.
        Returns total penalty (ms) + per-metric breakdown.
        """
        if targets is None:
            targets = TelemetryTargets()

        target_map: dict[str, tuple[float, float]] = {}
        for fname in targets.__dataclass_fields__:
            target_map[fname] = getattr(targets, fname)

        metrics: list[MetricScore] = []
        total_penalty = 0.0

        for metric, (lo, hi) in target_map.items():
            if metric not in pred.predicted:
                continue
            val = pred.predicted[metric]
            weight = METRIC_SCORE_WEIGHTS.get(metric, 1.0)
            conf = pred.confidence.get(metric, 0.5)

            if val < lo:
                excess = lo - val
                penalty = excess * weight * conf
                status = "warn" if penalty < weight * 0.5 else "bad"
                note = f"too low by {excess:.3f}"
            elif val > hi:
                excess = val - hi
                penalty = excess * weight * conf
                # Veto conditions
                veto_metrics = {"heave_bottoming_events_front", "front_bottoming_events",
                                "splitter_scrape_events"}
                if metric in veto_metrics and val > hi + 0.5:
                    status = "veto"
                    penalty *= 5.0  # Heavy veto penalty
                    note = f"VETO: {val:.1f} (limit {hi:.1f})"
                else:
                    status = "warn" if penalty < weight * 0.5 else "bad"
                    note = f"over by {excess:.3f}"
            else:
                penalty = 0.0
                status = "ok"
                note = f"{val:.3f} ∈ [{lo:.3f}, {hi:.3f}]"

            total_penalty += penalty
            metrics.append(MetricScore(
                metric=metric,
                predicted=val,
                target_lo=lo,
                target_hi=hi,
                penalty_ms=round(penalty, 2),
                status=status,
                note=note,
            ))

        # Lap time bonus (relative to fleet mean)
        mean_lap = self._mean_lap_time()
        lap_bonus = 0.0
        if pred.best_lap_predicted_s and mean_lap:
            lap_bonus = (mean_lap - pred.best_lap_predicted_s) * 1000.0  # ms faster than mean

        metrics.sort(key=lambda m: -m.penalty_ms)
        return ScoreResult(
            total_penalty_ms=round(total_penalty, 2),
            metrics=metrics,
            best_lap_bonus_ms=round(lap_bonus, 2),
        )

    def _mean_lap_time(self) -> Optional[float]:
        laps = [s.best_lap_s for s in self.sessions if s.best_lap_s]
        return sum(laps) / len(laps) if laps else None

    # ── Explain ───────────────────────────────────────────────────────────────

    def explain(
        self,
        proposed_setup: dict[str, float],
        k: int = 5,
        top_metrics: int = 12,
    ) -> str:
        """Human-readable explanation: what the data predicts and why."""
        pred = self.predict(proposed_setup, k=k)
        score_result = self.score(pred)

        lines: list[str] = []
        lines.append(f"  📊 Empirical prediction — {pred.k_used} nearest sessions")
        lines.append(f"  Nearest: {', '.join(pred.neighbor_ids[:3])}")
        lines.append(f"  Distances: {', '.join(f'{d:.2f}' for d in pred.neighbor_distances[:3])}")
        lines.append("")
        lines.append(f"  Lap prediction: {pred.best_lap_predicted_s:.3f}s" if pred.best_lap_predicted_s else "  Lap prediction: n/a")
        lines.append(f"  Total penalty: {score_result.total_penalty_ms:.1f}ms")
        lines.append("")

        # Show top penalised metrics first, then all OK
        shown_bad = [m for m in score_result.metrics if m.status != "ok"][:top_metrics]
        shown_ok = [m for m in score_result.metrics if m.status == "ok"][:6]

        for m in shown_bad:
            icon = "🔴" if m.status in ("bad", "veto") else "🟡"
            conf = pred.confidence.get(m.metric, 0.5)
            lines.append(f"  {icon} {m.metric:45s} {m.predicted:8.3f}  [{m.target_lo:.3f}–{m.target_hi:.3f}]  pen={m.penalty_ms:.1f}ms  conf={conf:.0%}  {m.note}")

        if shown_ok:
            lines.append("  ---")
            for m in shown_ok:
                conf = pred.confidence.get(m.metric, 0.5)
                lines.append(f"  ✅ {m.metric:45s} {m.predicted:8.3f}  [{m.target_lo:.3f}–{m.target_hi:.3f}]  conf={conf:.0%}")

        return "\n".join(lines)

    # ── Sensitivity analysis ─────────────────────────────────────────────────

    def sensitivity(self, param: str, values: list[float],
                    base_setup: dict[str, float]) -> dict:
        """
        How does changing `param` through `values` affect key telemetry metrics?
        Holds all other setup params at base_setup.
        Returns {metric: [predicted_val_at_each_value]}.
        """
        results: dict[str, list] = {}
        for v in values:
            test_setup = {**base_setup, param: v}
            pred = self.predict(test_setup, k=7)
            for metric, pval in pred.predicted.items():
                if metric not in results:
                    results[metric] = []
                results[metric].append((v, pval))
        return results

    def sensitivity_table(
        self, param: str, values: list[float], base_setup: dict[str, float],
        key_metrics: list[str] | None = None,
    ) -> str:
        """Pretty-print sensitivity of key metrics to one setup parameter."""
        if key_metrics is None:
            key_metrics = [
                "front_rh_std_mm", "understeer_high_speed_deg",
                "lltd_measured", "front_heave_travel_used_pct",
                "body_slip_p95_deg",
            ]
        results = self.sensitivity(param, values, base_setup)
        lines = [f"  Sensitivity: {param}"]
        header = f"  {'Value':>8}" + "".join(f"  {m[:14]:>14}" for m in key_metrics)
        lines.append(header)
        lines.append("  " + "-" * (8 + 16 * len(key_metrics)))
        for v in values:
            row = f"  {v:>8.1f}"
            for m in key_metrics:
                data = results.get(m, [])
                match = next((pv for val, pv in data if val == v), None)
                row += f"  {match:>14.3f}" if match is not None else f"  {'n/a':>14}"
            lines.append(row)
        return "\n".join(lines)

    # ── Statistics ───────────────────────────────────────────────────────────

    def summary(self) -> str:
        """One-line summary."""
        n = len(self.sessions)
        laps = [s.best_lap_s for s in self.sessions if s.best_lap_s]
        best = min(laps) if laps else None
        mean_lap = sum(laps) / len(laps) if laps else None
        heave_vals = set(
            s.setup_vector.get("front_heave_nmm") for s in self.sessions
            if "front_heave_nmm" in s.setup_vector
        )
        return (
            f"SessionDatabase({self.car}, {self.track}): "
            f"{n} sessions | heave tested: {sorted(heave_vals)} N/mm | "
            f"best lap: {best:.3f}s | mean: {mean_lap:.3f}s"
        ) if best else f"SessionDatabase({self.car}, {self.track}): {n} sessions"

    def __len__(self) -> int:
        return len(self.sessions)
