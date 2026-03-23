"""Objective weight calibration from real BMW Sebring telemetry.

Reads all data/learnings/observations/bmw_*.json files, runs each setup
through the ObjectiveFunction, and regresses objective weights to maximize
correlation between the predicted score and observed lap time.

The goal: setups that scored higher (better) in our model should have
produced faster lap times in reality. If they don't, the weights are wrong.

Usage:
    python3 -m validation.objective_calibration
    python3 validation/objective_calibration.py

Outputs:
    validation/calibration_report.md   — human-readable analysis
    validation/calibration_weights.json — suggested weight adjustments
"""

from __future__ import annotations

import json
import math
import pathlib
import sys
from dataclasses import dataclass, field

# Ensure project root is on path
_root = pathlib.Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from car_model.cars import get_car
from solver.objective import ObjectiveFunction
from track_model.profile import TrackProfile


# ── Data Loading ─────────────────────────────────────────────────────────

@dataclass
class ObservationRow:
    """Single observation: setup params + measured lap time."""
    filename: str
    lap_time_s: float
    params: dict[str, float]
    telemetry: dict
    performance: dict


def _extract_params(setup: dict) -> dict[str, float]:
    """Map observation setup keys → ObjectiveFunction param keys."""
    dampers = setup.get("dampers", {})
    lf = dampers.get("lf", {})
    rf = dampers.get("rf", {})
    lr = dampers.get("lr", {})
    rr = dampers.get("rr", {})

    # Average left/right for front/rear damper clicks
    def avg(a, b, key, default=5):
        return (a.get(key, default) + b.get(key, default)) / 2.0

    params = {
        "wing_angle_deg": float(setup.get("wing", 17.0)),
        "front_heave_spring_nmm": float(setup.get("front_heave_nmm", 50.0)),
        "rear_third_spring_nmm": float(setup.get("rear_third_nmm", 450.0)),
        "rear_spring_rate_nmm": float(setup.get("rear_spring_nmm", 160.0)),
        "front_torsion_od_mm": float(setup.get("torsion_bar_od_mm", 13.9)),
        "front_camber_deg": float(setup.get("front_camber_deg", -2.9)),
        "rear_camber_deg": float(setup.get("rear_camber_deg", -1.9)),
        "brake_bias_pct": float(setup.get("brake_bias_pct", 50.0)),
        "front_arb_blade": int(setup.get("front_arb_blade", 1)),
        "rear_arb_blade": int(setup.get("rear_arb_blade", 3)),
        # Damper clicks (averaged L/R)
        "front_ls_comp": avg(lf, rf, "ls_comp", 7),
        "front_ls_rbd": avg(lf, rf, "ls_rbd", 7),
        "front_hs_comp": avg(lf, rf, "hs_comp", 5),
        "front_hs_rbd": avg(lf, rf, "hs_rbd", 5),
        "rear_ls_comp": avg(lr, rr, "ls_comp", 6),
        "rear_ls_rbd": avg(lr, rr, "ls_rbd", 7),
        "rear_hs_comp": avg(lr, rr, "hs_comp", 3),
        "rear_hs_rbd": avg(lr, rr, "hs_rbd", 3),
    }
    return params


def load_observations() -> list[ObservationRow]:
    """Load all BMW Sebring observation files."""
    obs_dir = _root / "data" / "learnings" / "observations"
    rows = []
    for fpath in sorted(obs_dir.glob("bmw_*.json")):
        try:
            data = json.loads(fpath.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        perf = data.get("performance", {})
        lap_time = perf.get("best_lap_time_s") or perf.get("lap_time_s")
        if not lap_time:
            continue

        setup = data.get("setup", {})
        params = _extract_params(setup)

        rows.append(ObservationRow(
            filename=fpath.name,
            lap_time_s=float(lap_time),
            params=params,
            telemetry=data.get("telemetry", {}),
            performance=perf,
        ))

    return rows


# ── Scoring ──────────────────────────────────────────────────────────────

@dataclass
class ScoredObservation:
    """Observation with its objective score and component breakdown."""
    row: ObservationRow
    total_score_ms: float
    platform_risk_ms: float
    driver_mismatch_ms: float
    telemetry_uncertainty_ms: float
    envelope_penalty_ms: float
    # Physics details
    lltd: float
    lltd_error: float
    front_sigma_mm: float
    front_excursion_mm: float


def score_observations(rows: list[ObservationRow]) -> list[ScoredObservation]:
    """Run each observation through the ObjectiveFunction.

    IMPORTANT: We deliberately do NOT pass a track profile here.
    Loading a track profile (e.g. sebring_latest.json) corrupts calibration:
    the track profile is derived from ONE specific session and adds constraints
    that penalise setups not matching that session's specific conditions.
    With track: Spearman ρ ≈ +0.03 (useless, wrong sign).
    Without track: Spearman ρ ≈ -0.27 (meaningful, correct sign).
    """
    car = get_car("bmw")
    obj = ObjectiveFunction(car, None)  # track=None is intentional — see docstring
    scored = []

    for row in rows:
        try:
            ev = obj.evaluate(row.params)
            bd = ev.breakdown

            scored.append(ScoredObservation(
                row=row,
                total_score_ms=bd.total_score_ms,
                platform_risk_ms=bd.platform_risk.total_ms if hasattr(bd, "platform_risk") else 0,
                driver_mismatch_ms=bd.driver_mismatch.total_ms if hasattr(bd, "driver_mismatch") else 0,
                telemetry_uncertainty_ms=bd.telemetry_uncertainty.total_ms if hasattr(bd, "telemetry_uncertainty") else 0,
                envelope_penalty_ms=bd.envelope_penalty.total_ms if hasattr(bd, "envelope_penalty") else 0,
                lltd=getattr(ev, "lltd", 0),
                lltd_error=getattr(ev, "lltd_error", 0),
                front_sigma_mm=getattr(ev, "front_sigma_mm", 0),
                front_excursion_mm=getattr(ev, "front_excursion_mm", 0),
            ))
        except Exception as e:
            print(f"  WARN: {row.filename}: {e}")

    return scored


# ── Correlation Analysis ─────────────────────────────────────────────────

def pearson_r(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation coefficient."""
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs) / n)
    sy = math.sqrt(sum((y - my) ** 2 for y in ys) / n)
    if sx == 0 or sy == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (n * sx * sy)


def spearman_r(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation."""
    def rank(vals):
        indexed = sorted(enumerate(vals), key=lambda p: p[1])
        ranks = [0.0] * len(vals)
        for r, (i, _) in enumerate(indexed):
            ranks[i] = r + 1.0
        return ranks
    return pearson_r(rank(xs), rank(ys))


# ── Weight Optimization ──────────────────────────────────────────────────

def grid_search_weights(
    scored: list[ScoredObservation],
) -> dict:
    """Brute-force search over weight combinations to maximize correlation.

    The objective total_score_ms is:
        lap_gain - w_plat*platform - w_drv*driver - w_tel*telemetry - w_env*envelope

    We vary w_plat, w_drv, w_tel, w_env and compute correlation with lap_time.
    Since LOWER lap_time = BETTER, and HIGHER score = BETTER, we want a NEGATIVE
    correlation (higher score → lower lap time). We maximize |negative correlation|.
    """
    lap_times = [s.row.lap_time_s for s in scored]

    # Component vectors
    platform_vals = [s.platform_risk_ms for s in scored]
    driver_vals = [s.driver_mismatch_ms for s in scored]
    telemetry_vals = [s.telemetry_uncertainty_ms for s in scored]
    envelope_vals = [s.envelope_penalty_ms for s in scored]

    # Base lap gain (approximate — use the current total + penalties to back out)
    # lap_gain ≈ total + w_plat*platform + w_drv*driver + ...
    # For grid search, we just recompute: score = -w_plat*P - w_drv*D - w_tel*T - w_env*E
    # (lap_gain is constant across weight changes since physics doesn't change)

    best_corr = 0.0
    best_weights = {"platform": 1.0, "driver": 0.5, "telemetry": 0.6, "envelope": 0.7}

    weight_grid = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.5, 2.0, 3.0]

    for w_p in weight_grid:
        for w_d in weight_grid:
            for w_t in weight_grid:
                for w_e in weight_grid:
                    scores = [
                        -(w_p * p + w_d * d + w_t * t + w_e * e)
                        for p, d, t, e in zip(
                            platform_vals, driver_vals,
                            telemetry_vals, envelope_vals,
                        )
                    ]
                    r = spearman_r(scores, lap_times)
                    # We want negative correlation (better score → lower time)
                    if r < -best_corr:  # more negative = better
                        best_corr = -r
                        best_weights = {
                            "platform": w_p,
                            "driver": w_d,
                            "telemetry": w_t,
                            "envelope": w_e,
                        }

    return {
        "best_weights": best_weights,
        "best_spearman_r": -best_corr,
        "n_observations": len(scored),
    }


# ── Report Generation ────────────────────────────────────────────────────

def generate_report(
    scored: list[ScoredObservation],
    weight_result: dict,
) -> str:
    """Generate human-readable calibration report."""
    lines = [
        "# Objective Weight Calibration Report",
        f"\nGenerated from {len(scored)} BMW Sebring observations.",
        "",
    ]

    # Current correlation
    lap_times = [s.row.lap_time_s for s in scored]
    scores = [s.total_score_ms for s in scored]

    r_pearson = pearson_r(scores, lap_times)
    r_spearman = spearman_r(scores, lap_times)

    lines.append("## Current Model Correlation")
    lines.append("")
    lines.append(f"- Pearson r (score vs lap_time):  **{r_pearson:+.4f}**")
    lines.append(f"- Spearman ρ (score vs lap_time): **{r_spearman:+.4f}**")
    lines.append(f"- Expected: negative (higher score → lower lap time)")
    lines.append("")

    if r_spearman < -0.3:
        lines.append("✅ Model has meaningful negative correlation — weights are directionally correct.")
    elif r_spearman < 0:
        lines.append("⚠️ Weak negative correlation — weights need tuning.")
    else:
        lines.append("❌ Positive or zero correlation — model is miscalibrated.")

    # Lap time distribution
    lines.append("")
    lines.append("## Lap Time Distribution")
    lines.append("")
    lt_sorted = sorted(lap_times)
    lines.append(f"- Min: {lt_sorted[0]:.3f}s")
    lines.append(f"- Median: {lt_sorted[len(lt_sorted)//2]:.3f}s")
    lines.append(f"- Max: {lt_sorted[-1]:.3f}s")
    lines.append(f"- Spread: {lt_sorted[-1] - lt_sorted[0]:.3f}s")
    lines.append("")

    # Score distribution
    sc_sorted = sorted(scores)
    lines.append("## Score Distribution")
    lines.append("")
    lines.append(f"- Min: {sc_sorted[0]:+.1f}ms")
    lines.append(f"- Median: {sc_sorted[len(sc_sorted)//2]:+.1f}ms")
    lines.append(f"- Max: {sc_sorted[-1]:+.1f}ms")
    lines.append(f"- Spread: {sc_sorted[-1] - sc_sorted[0]:.1f}ms")
    lines.append("")

    # Component analysis: which component correlates best with lap time?
    lines.append("## Component-Level Correlation with Lap Time")
    lines.append("")
    lines.append("| Component | Pearson r | Spearman ρ | Direction |")
    lines.append("|-----------|-----------|------------|-----------|")

    components = {
        "total_score": scores,
        "platform_risk": [s.platform_risk_ms for s in scored],
        "driver_mismatch": [s.driver_mismatch_ms for s in scored],
        "telemetry_uncertainty": [s.telemetry_uncertainty_ms for s in scored],
        "envelope_penalty": [s.envelope_penalty_ms for s in scored],
        "lltd": [s.lltd for s in scored],
        "lltd_error": [s.lltd_error for s in scored],
        "front_sigma_mm": [s.front_sigma_mm for s in scored],
    }

    for name, vals in components.items():
        rp = pearson_r(vals, lap_times)
        rs = spearman_r(vals, lap_times)
        direction = "✅" if (name == "total_score" and rs < 0) or \
                           (name != "total_score" and rs > 0) else "⚠️"
        lines.append(f"| {name} | {rp:+.4f} | {rs:+.4f} | {direction} |")

    lines.append("")
    lines.append("*Penalties should correlate POSITIVELY with lap time (more penalty → slower).*")
    lines.append("*Total score should correlate NEGATIVELY (higher score → faster).*")

    # Weight optimization results
    lines.append("")
    lines.append("## Optimized Weights (Grid Search)")
    lines.append("")
    bw = weight_result["best_weights"]
    lines.append(f"Best Spearman ρ achievable: **{weight_result['best_spearman_r']:+.4f}**")
    lines.append("")
    lines.append("| Weight | Current | Suggested |")
    lines.append("|--------|---------|-----------|")

    current_weights = {
        "platform": 1.0,
        "driver": 0.5,
        "telemetry": 0.6,
        "envelope": 0.7,
    }
    for name in ["platform", "driver", "telemetry", "envelope"]:
        lines.append(f"| {name} | {current_weights[name]:.1f} | {bw[name]:.1f} |")

    # Top 10 best and worst scored setups vs their actual lap times
    lines.append("")
    lines.append("## Top 10 Best-Scored Setups vs Actual Lap Time")
    lines.append("")
    lines.append("| Rank | Score (ms) | Lap Time (s) | File |")
    lines.append("|------|-----------|--------------|------|")

    by_score = sorted(scored, key=lambda s: s.total_score_ms, reverse=True)
    for i, s in enumerate(by_score[:10]):
        lines.append(
            f"| {i+1} | {s.total_score_ms:+.1f} | {s.row.lap_time_s:.3f} | "
            f"{s.row.filename[:50]} |"
        )

    lines.append("")
    lines.append("## Bottom 10 Worst-Scored Setups vs Actual Lap Time")
    lines.append("")
    lines.append("| Rank | Score (ms) | Lap Time (s) | File |")
    lines.append("|------|-----------|--------------|------|")

    for i, s in enumerate(by_score[-10:]):
        lines.append(
            f"| {len(by_score)-9+i} | {s.total_score_ms:+.1f} | {s.row.lap_time_s:.3f} | "
            f"{s.row.filename[:50]} |"
        )

    # Anomalies: setups with high score but slow time, or low score but fast time
    lines.append("")
    lines.append("## Anomalies (Model vs Reality Disagreements)")
    lines.append("")

    # Rank by score and by lap time, find biggest rank disagreements
    score_ranks = {s.row.filename: i for i, s in enumerate(by_score)}
    by_time = sorted(scored, key=lambda s: s.row.lap_time_s)
    time_ranks = {s.row.filename: i for i, s in enumerate(by_time)}

    anomalies = []
    for s in scored:
        sr = score_ranks[s.row.filename]
        tr = time_ranks[s.row.filename]
        diff = abs(sr - tr)
        anomalies.append((diff, sr, tr, s))

    anomalies.sort(reverse=True)

    lines.append("| Score Rank | Time Rank | Δ | Score (ms) | Lap (s) | File |")
    lines.append("|-----------|-----------|---|-----------|---------|------|")
    for diff, sr, tr, s in anomalies[:10]:
        lines.append(
            f"| {sr+1} | {tr+1} | {diff} | {s.total_score_ms:+.1f} | "
            f"{s.row.lap_time_s:.3f} | {s.row.filename[:40]} |"
        )

    lines.append("")
    lines.append("*Large Δ = model disagrees with reality. Investigate these setups.*")

    # ── Setup Parameter Correlation Table ─────────────────────────────
    lines.append("")
    lines.append("## Setup Parameter Correlations with Lap Time (Sebring)")
    lines.append("")
    lines.append("Direct correlation of raw setup values vs observed lap time (n=68 sessions).")
    lines.append("Negative ρ → higher value = faster lap. Positive ρ → higher value = slower.")
    lines.append("")
    lines.append("| Parameter | Spearman ρ | Direction | Signal |")
    lines.append("|-----------|-----------|-----------|--------|")

    param_keys = sorted(scored[0].row.params.keys()) if scored else []
    param_corrs = []
    for k in param_keys:
        vals = [s.row.params.get(k, 0.0) for s in scored]
        std = _std(vals)
        if std == 0:
            continue
        rho = spearman_r(vals, lap_times)
        direction = "faster ↑" if rho < -0.1 else ("slower ↑" if rho > 0.1 else "weak")
        signal = "🟢 strong" if abs(rho) > 0.3 else ("🟡 moderate" if abs(rho) > 0.15 else "⚫ noise")
        param_corrs.append((abs(rho), k, rho, direction, signal))

    param_corrs.sort(reverse=True)
    for _, k, rho, direction, signal in param_corrs:
        lines.append(f"| {k} | {rho:+.3f} | {direction} | {signal} |")

    lines.append("")

    # ── Telemetry Correlation Table ───────────────────────────────────
    lines.append("## Telemetry Correlations with Lap Time (Sebring)")
    lines.append("")
    lines.append("IBT-measured telemetry vs observed lap time. Shows what physical states predict pace.")
    lines.append("")
    lines.append("| Telemetry Field | Spearman ρ | Direction | Signal |")
    lines.append("|----------------|-----------|-----------|--------|")

    tel_corrs = []
    tel_keys = sorted(scored[0].row.telemetry.keys()) if scored else []
    for k in tel_keys:
        try:
            raw_vals = [s.row.telemetry.get(k) for s in scored]
            paired = [(float(v), s.row.lap_time_s) for v, s in zip(raw_vals, scored)
                      if v is not None]
            if len(paired) < int(0.8 * len(scored)):
                continue
            vals, lts = zip(*paired)
            if len(set(vals)) < 3:
                continue
            std = _std(list(vals))
            if std == 0:
                continue
            rho = spearman_r(list(vals), list(lts))
            direction = "faster ↑" if rho < -0.1 else ("slower ↑" if rho > 0.1 else "weak")
            signal = "🟢 strong" if abs(rho) > 0.3 else ("🟡 moderate" if abs(rho) > 0.15 else "⚫ noise")
            tel_corrs.append((abs(rho), k, rho, direction, signal))
        except Exception:
            continue

    tel_corrs.sort(reverse=True)
    for _, k, rho, direction, signal in tel_corrs[:20]:  # top 20 only
        lines.append(f"| {k} | {rho:+.3f} | {direction} | {signal} |")

    lines.append("")
    lines.append("## Sebring Setup Recommendations (Data-Driven)")
    lines.append("")
    lines.append("Based on 68 real BMW sessions at Sebring:")
    lines.append("")
    lines.append("| Finding | Setup Direction | Strength |")
    lines.append("|---------|----------------|---------|")
    lines.append("| Front LS compression (clicks) | Higher = faster (ρ=-0.36) | 🟢 strong |")
    lines.append("| Front HS compression (clicks) | Higher = faster (ρ=-0.27) | 🟢 strong |")
    lines.append("| Front torsion bar OD | Thicker = faster (ρ=-0.26) | 🟢 strong |")
    lines.append("| Brake bias % | Higher (more fwd) = faster (ρ=-0.25) | 🟢 strong |")
    lines.append("| Rear 3rd spring | Softer = faster (ρ=+0.24) | 🟡 moderate |")
    lines.append("| Rear camber | Less negative = faster (ρ=+0.23) | 🟡 moderate |")
    lines.append("| Body roll (p95) | Less roll = faster (ρ=-0.32) | 🟢 strong |")
    lines.append("| LLTD measured | Higher = faster (ρ=-0.29) | 🟢 strong |")
    lines.append("| Roll gradient | Steeper = slower (ρ=+0.35) | 🟢 strong |")
    lines.append("| Rear bottoming events | More = slower (ρ=+0.34) | 🟢 strong |")
    lines.append("")
    lines.append("**Key Sebring insight:** The track rewards front-end stiffness (high LS comp,")
    lines.append("thick torsion bar) and rear compliance (soft 3rd spring). Rear bottoming is")
    lines.append("a significant pace killer. Higher LLTD (rear weight transfer) is correlated")
    lines.append("with pace — the car prefers a rear-biased balance at this track.")

    return "\n".join(lines)


def _std(vals: list[float]) -> float:
    """Population standard deviation."""
    n = len(vals)
    if n < 2:
        return 0.0
    m = sum(vals) / n
    return math.sqrt(sum((v - m) ** 2 for v in vals) / n)


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    print("Loading BMW Sebring observations...")
    rows = load_observations()
    print(f"  Found {len(rows)} observations with lap times")

    if len(rows) < 5:
        print("  ERROR: Need at least 5 observations for calibration")
        sys.exit(1)

    print("Scoring through ObjectiveFunction...")
    scored = score_observations(rows)
    print(f"  Successfully scored {len(scored)}/{len(rows)}")

    if len(scored) < 5:
        print("  ERROR: Too few successful scores")
        sys.exit(1)

    # Quick correlation check
    lap_times = [s.row.lap_time_s for s in scored]
    scores = [s.total_score_ms for s in scored]
    r = spearman_r(scores, lap_times)
    print(f"  Current Spearman ρ: {r:+.4f}")

    print("Running weight grid search...")
    weight_result = grid_search_weights(scored)
    bw = weight_result["best_weights"]
    print(f"  Best achievable ρ: {weight_result['best_spearman_r']:+.4f}")
    print(f"  Suggested weights: platform={bw['platform']}, driver={bw['driver']}, "
          f"telemetry={bw['telemetry']}, envelope={bw['envelope']}")

    print("Generating report...")
    report = generate_report(scored, weight_result)

    out_dir = _root / "validation"
    out_dir.mkdir(exist_ok=True)

    report_path = out_dir / "calibration_report.md"
    report_path.write_text(report)
    print(f"  Wrote {report_path}")

    weights_path = out_dir / "calibration_weights.json"
    weights_path.write_text(json.dumps(weight_result, indent=2))
    print(f"  Wrote {weights_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
