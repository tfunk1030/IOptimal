"""Performance-oriented scoring and ranking of multiple sessions.

Scores each session across categories that matter for lap time:
grip, balance, aero efficiency, corner performance by speed class,
damper/platform stability, and thermal management.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from comparison.compare import (
    ComparisonResult,
    CornerComparison,
    SessionAnalysis,
    TELEMETRY_METRICS,
)


# ── Scoring categories and weights ──────────────────────────────

CATEGORY_WEIGHTS: dict[str, float] = {
    "lap_time": 0.15,
    "grip": 0.15,
    "balance": 0.15,
    "aero_efficiency": 0.10,
    "high_speed_corners": 0.10,
    "low_speed_corners": 0.10,
    "corner_performance": 0.10,
    "damper_platform": 0.05,
    "thermal": 0.05,
    "context_health": 0.05,
}

CATEGORY_LABELS: dict[str, str] = {
    "lap_time": "Lap Time",
    "grip": "Grip",
    "balance": "Balance",
    "aero_efficiency": "Aero Efficiency",
    "high_speed_corners": "High-Speed Corners",
    "low_speed_corners": "Low-Speed Corners",
    "corner_performance": "Corner Performance",
    "damper_platform": "Damper / Platform",
    "thermal": "Thermal",
    "context_health": "Context / Health",
}


@dataclass
class SessionScore:
    """Scoring result for a single session."""

    session: SessionAnalysis
    category_scores: dict[str, float]  # category → 0-1 (1 = best)
    overall_score: float
    rank: int = 0
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    corner_scores: dict[str, float] = field(default_factory=dict)  # speed_class → 0-1


@dataclass
class ScoringResult:
    """Complete scoring output."""

    scores: list[SessionScore]  # sorted by rank (1 = best)
    best_per_category: dict[str, int]  # category → session index (0-based)


# ── Normalization helpers ───────────────────────────────────────


def _normalize_lower_better(values: list[float]) -> list[float]:
    """Normalize so lowest value gets 1.0, highest gets 0.0."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0] * len(values)
    return [1.0 - (v - lo) / (hi - lo) for v in values]


def _normalize_higher_better(values: list[float]) -> list[float]:
    """Normalize so highest value gets 1.0, lowest gets 0.0."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def _normalize_closer_to_target(values: list[float], target: float) -> list[float]:
    """Normalize so value closest to target gets 1.0."""
    if not values:
        return []
    dists = [abs(v - target) for v in values]
    max_dist = max(dists) if max(dists) > 0 else 1.0
    return [1.0 - d / max_dist for d in dists]


def _safe_avg(values: list[float]) -> float:
    """Average, returning 0.5 for empty lists."""
    return sum(values) / len(values) if values else 0.5


# ── Per-category scorers ────────────────────────────────────────


def _score_lap_time(sessions: list[SessionAnalysis]) -> list[float]:
    """Lower lap time = better."""
    times = [s.lap_time_s for s in sessions]
    return _normalize_lower_better(times)


def _score_grip(sessions: list[SessionAnalysis]) -> list[float]:
    """Peak lateral g, low rear slip ratio, low front slip ratio."""
    scores_per_session: list[list[float]] = [[] for _ in sessions]

    lat_g = [s.measured.peak_lat_g_measured for s in sessions]
    rear_slip = [s.measured.rear_slip_ratio_p95 for s in sessions]
    front_slip = [s.measured.front_slip_ratio_p95 for s in sessions]

    lat_g_norm = _normalize_higher_better(lat_g)
    rear_slip_norm = _normalize_lower_better(rear_slip)
    front_slip_norm = _normalize_lower_better(front_slip)

    for i in range(len(sessions)):
        scores_per_session[i].extend([
            lat_g_norm[i],
            rear_slip_norm[i],
            front_slip_norm[i],
        ])

    # Driver g-utilization from driver profile
    g_util = [s.driver.avg_peak_lat_g_utilization for s in sessions]
    g_util_norm = _normalize_higher_better(g_util)
    for i in range(len(sessions)):
        scores_per_session[i].append(g_util_norm[i])

    return [_safe_avg(s) for s in scores_per_session]


def _score_balance(sessions: list[SessionAnalysis]) -> list[float]:
    """Understeer closer to 0, low speed gradient, low body slip."""
    scores_per_session: list[list[float]] = [[] for _ in sessions]

    us_mean = [abs(s.measured.understeer_mean_deg) for s in sessions]
    us_gradient = [
        abs(s.measured.understeer_high_speed_deg - s.measured.understeer_low_speed_deg)
        for s in sessions
    ]
    body_slip = [s.measured.body_slip_p95_deg for s in sessions]

    us_mean_norm = _normalize_lower_better(us_mean)
    us_grad_norm = _normalize_lower_better(us_gradient)
    body_slip_norm = _normalize_lower_better(body_slip)

    for i in range(len(sessions)):
        scores_per_session[i].extend([
            us_mean_norm[i],
            us_grad_norm[i],
            body_slip_norm[i],
        ])

    return [_safe_avg(s) for s in scores_per_session]


def _score_aero_efficiency(sessions: list[SessionAnalysis]) -> list[float]:
    """Top speed (proxy for drag), aero compression, RH stability at speed."""
    scores_per_session: list[list[float]] = [[] for _ in sessions]

    top_speed = [s.measured.speed_max_kph for s in sessions]
    aero_comp = [s.measured.aero_compression_front_mm for s in sessions]
    rh_std = [s.measured.front_rh_std_mm for s in sessions]

    top_speed_norm = _normalize_higher_better(top_speed)
    aero_comp_norm = _normalize_higher_better(aero_comp)  # more compression = more DF
    rh_std_norm = _normalize_lower_better(rh_std)

    for i in range(len(sessions)):
        scores_per_session[i].extend([
            top_speed_norm[i],
            aero_comp_norm[i],
            rh_std_norm[i],
        ])

    return [_safe_avg(s) for s in scores_per_session]


def _score_corners_by_speed_class(
    sessions: list[SessionAnalysis],
    corner_comps: list[CornerComparison],
    target_class: str,
) -> list[float]:
    """Score corner performance for a specific speed class.

    Uses min speed (apex), time loss, and understeer per corner.
    """
    relevant = [cc for cc in corner_comps if cc.speed_class == target_class]
    if not relevant:
        return [0.5] * len(sessions)

    scores_per_session: list[list[float]] = [[] for _ in sessions]

    for cc in relevant:
        apex_speeds: list[float] = []
        time_losses: list[float] = []
        understeer: list[float] = []

        for i, c in enumerate(cc.per_session):
            if c is not None:
                apex_speeds.append(c.apex_speed_kph)
                time_losses.append(c.delta_to_min_time_s)
                understeer.append(abs(c.understeer_mean_deg))
            else:
                apex_speeds.append(0.0)
                time_losses.append(999.0)
                understeer.append(999.0)

        apex_norm = _normalize_higher_better(apex_speeds)
        loss_norm = _normalize_lower_better(time_losses)
        us_norm = _normalize_lower_better(understeer)

        for i in range(len(sessions)):
            corner_score = (apex_norm[i] + loss_norm[i] + us_norm[i]) / 3.0
            scores_per_session[i].append(corner_score)

    return [_safe_avg(s) for s in scores_per_session]


def _score_corner_performance(
    sessions: list[SessionAnalysis],
    corner_comps: list[CornerComparison],
) -> list[float]:
    """Overall corner performance: total time loss, exit speeds, trail braking."""
    scores_per_session: list[list[float]] = [[] for _ in sessions]

    # Total time loss across all corners
    total_loss: list[float] = []
    for i, sess in enumerate(sessions):
        loss = sum(c.delta_to_min_time_s for c in sess.corners)
        total_loss.append(loss)
    loss_norm = _normalize_lower_better(total_loss)

    # Average exit speed across matched corners
    exit_speeds: list[list[float]] = [[] for _ in sessions]
    for cc in corner_comps:
        for i, c in enumerate(cc.per_session):
            if c is not None:
                exit_speeds[i].append(c.exit_speed_kph)

    avg_exit = [_safe_avg(es) if es else 0.0 for es in exit_speeds]
    exit_norm = _normalize_higher_better(avg_exit)

    for i in range(len(sessions)):
        scores_per_session[i].extend([loss_norm[i], exit_norm[i]])

    return [_safe_avg(s) for s in scores_per_session]


def _score_damper_platform(sessions: list[SessionAnalysis]) -> list[float]:
    """Settle time, yaw correlation, RH variance, shock velocity control."""
    scores_per_session: list[list[float]] = [[] for _ in sessions]

    settle_f = [s.measured.front_rh_settle_time_ms for s in sessions]
    settle_r = [s.measured.rear_rh_settle_time_ms for s in sessions]
    yaw_corr = [s.measured.yaw_rate_correlation for s in sessions]
    rh_var_f = [s.measured.front_rh_std_mm for s in sessions]
    shock_f = [s.measured.front_shock_vel_p99_mps for s in sessions]

    # Settle time: target is 50-200ms. Closer to 125ms center = better
    settle_f_norm = _normalize_closer_to_target(settle_f, 125.0)
    settle_r_norm = _normalize_closer_to_target(settle_r, 125.0)
    yaw_norm = _normalize_higher_better(yaw_corr)
    rh_var_norm = _normalize_lower_better(rh_var_f)
    shock_norm = _normalize_lower_better(shock_f)

    for i in range(len(sessions)):
        scores_per_session[i].extend([
            settle_f_norm[i],
            settle_r_norm[i],
            yaw_norm[i],
            rh_var_norm[i],
            shock_norm[i],
        ])

    return [_safe_avg(s) for s in scores_per_session]


def _score_thermal(sessions: list[SessionAnalysis]) -> list[float]:
    """Tyre temp spread (lower=better), carcass temp in window, pressure in window."""
    scores_per_session: list[list[float]] = [[] for _ in sessions]

    # Temp spreads — lower is better
    spreads = []
    for s in sessions:
        avg_spread = (
            abs(s.measured.front_temp_spread_lf_c)
            + abs(s.measured.front_temp_spread_rf_c)
            + abs(s.measured.rear_temp_spread_lr_c)
            + abs(s.measured.rear_temp_spread_rr_c)
        ) / 4.0
        spreads.append(avg_spread)
    spread_norm = _normalize_lower_better(spreads)

    # Carcass temp — target 80-105°C, closer to 92.5 center = better
    carcass_f = [s.measured.front_carcass_mean_c for s in sessions]
    carcass_r = [s.measured.rear_carcass_mean_c for s in sessions]
    carcass_f_norm = _normalize_closer_to_target(carcass_f, 92.5)
    carcass_r_norm = _normalize_closer_to_target(carcass_r, 92.5)

    # Pressure — target 155-175 kPa, closer to 165 center = better
    press_f = [s.measured.front_pressure_mean_kpa for s in sessions]
    press_r = [s.measured.rear_pressure_mean_kpa for s in sessions]
    press_f_norm = _normalize_closer_to_target(press_f, 165.0)
    press_r_norm = _normalize_closer_to_target(press_r, 165.0)

    for i in range(len(sessions)):
        scores_per_session[i].extend([
            spread_norm[i],
            carcass_f_norm[i],
            carcass_r_norm[i],
            press_f_norm[i],
            press_r_norm[i],
        ])

    return [_safe_avg(s) for s in scores_per_session]


def _score_context_health(sessions: list[SessionAnalysis]) -> list[float]:
    """Score session comparability and telemetry health."""
    scores: list[float] = []
    for sess in sessions:
        ctx = getattr(sess, "session_context", None)
        if ctx is None:
            scores.append(0.5)
            continue
        score = ctx.overall_score
        if not ctx.comparable_to_baseline:
            score *= 0.8
        scores.append(score)
    return scores


# ── Main scorer ─────────────────────────────────────────────────


def score_sessions(comparison: ComparisonResult) -> ScoringResult:
    """Score and rank all sessions across performance categories."""
    sessions = comparison.sessions
    n = len(sessions)
    corner_comps = comparison.corner_comparisons

    # Compute per-category scores
    cat_scores: dict[str, list[float]] = {
        "lap_time": _score_lap_time(sessions),
        "grip": _score_grip(sessions),
        "balance": _score_balance(sessions),
        "aero_efficiency": _score_aero_efficiency(sessions),
        "high_speed_corners": _score_corners_by_speed_class(
            sessions, corner_comps, "high"
        ),
        "low_speed_corners": _score_corners_by_speed_class(
            sessions, corner_comps, "low"
        ),
        "corner_performance": _score_corner_performance(sessions, corner_comps),
        "damper_platform": _score_damper_platform(sessions),
        "thermal": _score_thermal(sessions),
        "context_health": _score_context_health(sessions),
    }

    # Per-corner speed class scores for each session
    corner_class_scores: list[dict[str, float]] = []
    for i in range(n):
        cls_scores: dict[str, float] = {}
        for cls in ("low", "mid", "high"):
            cls_vals = _score_corners_by_speed_class(sessions, corner_comps, cls)
            cls_scores[cls] = cls_vals[i]
        corner_class_scores.append(cls_scores)

    # Best per category
    best_per_cat: dict[str, int] = {}
    for cat, vals in cat_scores.items():
        best_per_cat[cat] = vals.index(max(vals))

    # Overall weighted score
    overall: list[float] = [0.0] * n
    for cat, weight in CATEGORY_WEIGHTS.items():
        for i in range(n):
            overall[i] += weight * cat_scores[cat][i]

    # Build session scores
    session_scores: list[SessionScore] = []
    for i in range(n):
        per_cat = {cat: cat_scores[cat][i] for cat in CATEGORY_WEIGHTS}

        # Identify strengths and weaknesses
        strengths: list[str] = []
        weaknesses: list[str] = []
        for cat in CATEGORY_WEIGHTS:
            label = CATEGORY_LABELS[cat]
            if best_per_cat[cat] == i:
                strengths.append(f"Best {label} ({cat_scores[cat][i]:.0%})")
            # Worst in category?
            if cat_scores[cat][i] == min(cat_scores[cat]) and n > 1:
                if cat_scores[cat][i] < max(cat_scores[cat]) - 0.01:
                    weaknesses.append(f"Weakest {label} ({cat_scores[cat][i]:.0%})")

        session_scores.append(SessionScore(
            session=sessions[i],
            category_scores=per_cat,
            overall_score=overall[i],
            strengths=strengths,
            weaknesses=weaknesses,
            corner_scores=corner_class_scores[i],
        ))

    # Sort by overall score (descending) and assign ranks
    session_scores.sort(key=lambda s: s.overall_score, reverse=True)
    for rank, ss in enumerate(session_scores, start=1):
        ss.rank = rank

    return ScoringResult(
        scores=session_scores,
        best_per_category=best_per_cat,
    )
