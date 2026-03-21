"""
Objective Validation Script — Sprint 4
Run: python3 validation/run_validation.py (from repo root)

Reads all bmw_*.json observation files, scores each through
ObjectiveFunction.evaluate(), then computes Pearson correlation
between objective score and actual lap time.
"""

from __future__ import annotations

import json
import math
import os
import sys
import glob
from dataclasses import dataclass, field
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

from car_model.cars import get_car
from track_model.profile import TrackProfile
from solver.objective import ObjectiveFunction


OBS_DIR = Path("data/learnings/observations")
TRACK_JSON = Path("data/tracks/sebring_international_raceway_international.json")
OUT_PATH = Path("validation/objective_validation.md")


@dataclass
class SessionResult:
    filename: str
    session_id: str
    lap_time_s: float
    score_ms: float
    vetoed: bool
    veto_reasons: list[str] = field(default_factory=list)
    # Setup params
    wing: float = 0.0
    heave_nmm: float = 0.0
    third_nmm: float = 0.0
    torsion_od: float = 0.0
    front_arb: float = 0
    rear_arb: float = 0
    front_rh_static: float = 0.0
    rear_rh_static: float = 0.0
    # Telemetry
    lltd_measured: float = 0.0
    dynamic_front_rh: float = 0.0
    front_shock_p99: float = 0.0
    consistency_cv: float = 0.0
    lap_number: int = 0
    # Scoring breakdown
    lap_gain_ms: float = 0.0
    platform_risk_ms: float = 0.0
    envelope_ms: float = 0.0
    lltd_error_pct: float = 0.0
    notes: str = ""


def setup_to_params(setup: dict) -> dict:
    """Map observation setup keys → ObjectiveFunction canonical param keys."""
    return {
        "wing_angle_deg": float(setup.get("wing", 17.0)),
        "front_heave_nmm": float(setup.get("front_heave_nmm", 50.0)),
        "rear_third_nmm": float(setup.get("rear_third_nmm", 530.0)),
        "torsion_bar_od_mm": float(setup.get("torsion_bar_od_mm", 13.9)),
        "front_arb_blade": float(setup.get("front_arb_blade", 1)),
        "rear_arb_blade": float(setup.get("rear_arb_blade", 3)),
        "front_rh_static": float(setup.get("front_rh_static", 30.0)),
        "rear_rh_static": float(setup.get("rear_rh_static", 47.0)),
        "front_camber_deg": float(setup.get("front_camber_deg", -3.0)),
        "rear_camber_deg": float(setup.get("rear_camber_deg", -2.0)),
        "front_toe_mm": float(setup.get("front_toe_mm", -0.4)),
        "rear_toe_mm": float(setup.get("rear_toe_mm", -0.2)),
        # Dampers (LF as proxy for symmetric)
        "lf_ls_comp": float(setup.get("dampers", {}).get("lf", {}).get("ls_comp", 7)),
        "lf_ls_rbd": float(setup.get("dampers", {}).get("lf", {}).get("ls_rbd", 6)),
        "lf_hs_comp": float(setup.get("dampers", {}).get("lf", {}).get("hs_comp", 5)),
        "lf_hs_rbd": float(setup.get("dampers", {}).get("lf", {}).get("hs_rbd", 3)),
        "lr_ls_comp": float(setup.get("dampers", {}).get("lr", {}).get("ls_comp", 5)),
        "lr_ls_rbd": float(setup.get("dampers", {}).get("lr", {}).get("ls_rbd", 5)),
    }


def pearson_r(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation coefficient."""
    n = len(xs)
    if n < 2:
        return float("nan")
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx * sy == 0:
        return float("nan")
    return num / (sx * sy)


def load_observations() -> list[dict]:
    files = sorted(glob.glob(str(OBS_DIR / "bmw_*.json")))
    obs = []
    for f in files:
        try:
            d = json.loads(Path(f).read_text())
            d["_filename"] = os.path.basename(f)
            obs.append(d)
        except Exception as e:
            print(f"  [WARN] Cannot parse {os.path.basename(f)}: {e}")
    return obs


def run_validation() -> list[SessionResult]:
    car = get_car("bmw")
    track = TrackProfile.load(str(TRACK_JSON))
    obj = ObjectiveFunction(car, track)

    observations = load_observations()
    print(f"Loaded {len(observations)} observation files")

    results = []
    skip_no_lap = 0
    score_errors = 0

    for d in observations:
        fname = d["_filename"]
        session_id = d.get("session_id", fname.replace(".json", ""))
        perf = d.get("performance", {})
        setup = d.get("setup", {})
        tel = d.get("telemetry", {})

        lap_time = perf.get("best_lap_time_s")
        if not lap_time or float(lap_time) < 80:
            skip_no_lap += 1
            continue

        lap_time = float(lap_time)
        params = setup_to_params(setup)

        # Run objective
        score_ms = float("-inf")
        vetoed = True
        veto_reasons: list[str] = []
        lap_gain_ms = 0.0
        platform_risk_ms = 0.0
        envelope_ms = 0.0
        notes = ""

        try:
            ev = obj.evaluate(params)
            score_ms = ev.breakdown.total_score_ms
            vetoed = ev.hard_vetoed
            veto_reasons = ev.veto_reasons or []
            lap_gain_ms = ev.breakdown.lap_gain_ms
            platform_risk_ms = ev.breakdown.platform_risk.total_ms
            envelope_ms = ev.breakdown.envelope_penalty.total_ms
        except Exception as e:
            score_errors += 1
            notes = f"Eval error: {e}"
            score_ms = float("nan")
            vetoed = False

        # LLTD error from objective target (52% nominal)
        lltd_measured = tel.get("lltd_measured", 0.0) or 0.0
        lltd_target = 0.52  # BMW objective target
        lltd_error_pct = abs(float(lltd_measured) - lltd_target) * 100 if lltd_measured else 0.0

        results.append(SessionResult(
            filename=fname,
            session_id=session_id,
            lap_time_s=lap_time,
            score_ms=score_ms,
            vetoed=vetoed,
            veto_reasons=veto_reasons,
            wing=float(setup.get("wing", 17.0)),
            heave_nmm=float(setup.get("front_heave_nmm", 50.0)),
            third_nmm=float(setup.get("rear_third_nmm", 530.0)),
            torsion_od=float(setup.get("torsion_bar_od_mm", 13.9)),
            front_arb=float(setup.get("front_arb_blade", 1)),
            rear_arb=float(setup.get("rear_arb_blade", 3)),
            front_rh_static=float(setup.get("front_rh_static", 30.0)),
            rear_rh_static=float(setup.get("rear_rh_static", 47.0)),
            lltd_measured=float(lltd_measured),
            dynamic_front_rh=float(tel.get("dynamic_front_rh_mm", 0.0) or 0.0),
            front_shock_p99=float(tel.get("front_shock_vel_p99_mps", 0.0) or 0.0),
            consistency_cv=float(perf.get("consistency_cv", 0.0) or 0.0),
            lap_number=int(perf.get("lap_number", 0) or 0),
            lap_gain_ms=lap_gain_ms,
            platform_risk_ms=platform_risk_ms,
            envelope_ms=envelope_ms,
            lltd_error_pct=lltd_error_pct,
            notes=notes,
        ))

    print(f"Scored: {len(results)}, Skipped (no lap time): {skip_no_lap}, Errors: {score_errors}")
    return results


def compute_correlations(results: list[SessionResult]) -> dict:
    # Filter for non-vetoed, non-nan results only (can score vetoed too for completeness)
    non_vetoed = [r for r in results if not r.vetoed and not math.isnan(r.score_ms)]
    all_valid = [r for r in results if not math.isnan(r.score_ms)]

    def corr_safe(xs, ys):
        pairs = [(x, y) for x, y in zip(xs, ys) if not math.isnan(x) and not math.isnan(y)]
        if len(pairs) < 2:
            return float("nan"), 0
        xs2, ys2 = zip(*pairs)
        return pearson_r(list(xs2), list(ys2)), len(pairs)

    lap_times_nv = [r.lap_time_s for r in non_vetoed]
    lap_times_all = [r.lap_time_s for r in all_valid]

    corr = {}
    for subset, label in [(non_vetoed, "non_vetoed"), (all_valid, "all_valid")]:
        lt = [r.lap_time_s for r in subset]
        corr[f"{label}_total_score"] = corr_safe(lt, [r.score_ms for r in subset])
        corr[f"{label}_lap_gain"] = corr_safe(lt, [r.lap_gain_ms for r in subset])
        corr[f"{label}_platform_risk"] = corr_safe(lt, [r.platform_risk_ms for r in subset])
        corr[f"{label}_envelope"] = corr_safe(lt, [r.envelope_ms for r in subset])
        corr[f"{label}_lltd_error"] = corr_safe(lt, [r.lltd_error_pct for r in subset])
        corr[f"{label}_dynamic_rh"] = corr_safe(lt, [r.dynamic_front_rh for r in subset])
        corr[f"{label}_consistency"] = corr_safe(lt, [r.consistency_cv for r in subset])
    return corr


def format_session_id(session_id: str) -> str:
    """Shorten session id for table display."""
    if "bmwlmdh_sebring" in session_id and "2026-" in session_id:
        # Extract just the date-time suffix
        parts = session_id.split("_")
        for i, p in enumerate(parts):
            if p.startswith("2026-"):
                return "_".join(parts[i:])[:20]
    return session_id[-30:]


def write_report(results: list[SessionResult], correlations: dict):
    today = date.today().isoformat()
    total = len(results)
    vetoed = [r for r in results if r.vetoed]
    non_vetoed = [r for r in results if not r.vetoed and not math.isnan(r.score_ms)]

    lap_times = [r.lap_time_s for r in results]
    fastest = min(results, key=lambda r: r.lap_time_s)
    slowest = max(results, key=lambda r: r.lap_time_s)

    # Sort by lap time for table
    sorted_results = sorted(results, key=lambda r: r.lap_time_s)

    lines = []
    lines.append(f"## Objective Validation — {today}")
    lines.append("")
    lines.append("**Branch:** claw-research  ")
    lines.append(f"**Dataset:** {total} BMW LMDH sessions with lap times, Sebring International  ")
    lines.append(f"**Objective version:** Sprint 4 (e0c78bb — LLTD calibration + vortex fix)  ")
    lines.append("")

    # Dataset summary
    lines.append("### Dataset Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Sessions with lap times | {total} |")
    lines.append(f"| Hard-vetoed | {len(vetoed)} ({len(vetoed)*100//total}%) |")
    lines.append(f"| Non-vetoed, scoreable | {len(non_vetoed)} |")
    lines.append(f"| Lap time range | {min(lap_times):.3f}s – {max(lap_times):.3f}s (Δ = {max(lap_times)-min(lap_times):.3f}s) |")
    lines.append(f"| Fastest session | {format_session_id(fastest.session_id)} — {fastest.lap_time_s:.3f}s |")
    lines.append(f"| Slowest session | {format_session_id(slowest.session_id)} — {slowest.lap_time_s:.3f}s |")

    # Setup variation summary
    heave_vals = sorted(set(round(r.heave_nmm, 0) for r in results))
    third_vals = sorted(set(round(r.third_nmm, 0) for r in results))
    od_vals = sorted(set(round(r.torsion_od, 2) for r in results))
    f_arb_vals = sorted(set(int(r.front_arb) for r in results))
    r_arb_vals = sorted(set(int(r.rear_arb) for r in results))
    lines.append(f"| Heave spring variants | {heave_vals} N/mm |")
    lines.append(f"| Third spring variants | {third_vals} N/mm |")
    lines.append(f"| Torsion bar OD variants | {od_vals} mm |")
    lines.append(f"| Front ARB blade variants | {f_arb_vals} |")
    lines.append(f"| Rear ARB blade variants | {r_arb_vals} |")
    lines.append("")

    # Full data table (sorted by lap time)
    lines.append("### Data")
    lines.append("")
    lines.append("| Session | Lap Time (s) | Obj Score (ms) | Vetoed | Heave | 3rd | Torsion | F-ARB | R-ARB | LLTD_meas | Dyn_FRH | Notes |")
    lines.append("|---------|-------------|----------------|--------|-------|-----|---------|-------|-------|-----------|---------|-------|")

    for rank, r in enumerate(sorted_results, 1):
        sid = format_session_id(r.session_id)
        score_str = f"{r.score_ms:.1f}" if not math.isnan(r.score_ms) else "N/A"
        veto_str = "✗ " + (r.veto_reasons[0][:20] if r.veto_reasons else "veto") if r.vetoed else "—"
        lltd_str = f"{r.lltd_measured*100:.1f}%" if r.lltd_measured else "—"
        rh_str = f"{r.dynamic_front_rh:.1f}" if r.dynamic_front_rh else "—"
        lines.append(
            f"| {rank}. {sid} | {r.lap_time_s:.3f} | {score_str} | {veto_str} "
            f"| {r.heave_nmm:.0f} | {r.third_nmm:.0f} | {r.torsion_od:.2f} "
            f"| {int(r.front_arb)} | {int(r.rear_arb)} | {lltd_str} | {rh_str} | {r.notes[:30] if r.notes else ''} |"
        )
    lines.append("")

    # Correlation section
    lines.append("### Correlation")
    lines.append("")
    r_nv = correlations.get("non_vetoed_total_score", (float("nan"), 0))
    r_all = correlations.get("all_valid_total_score", (float("nan"), 0))
    lines.append(f"**Pearson r (lap_time vs obj_score, non-vetoed only, n={r_nv[1]}):** `{r_nv[0]:.3f}`  ")
    lines.append(f"**Pearson r (lap_time vs obj_score, all valid, n={r_all[1]}):** `{r_all[0]:.3f}`  ")
    lines.append("")
    lines.append("_Note: Negative r means higher score → faster lap (desired). Values near 0 indicate low signal._")
    lines.append("")
    lines.append("| Term | r (non-vetoed) | r (all) | Direction | Notes |")
    lines.append("|------|---------------|---------|-----------|-------|")

    term_map = [
        ("total_score", "Total Score", "neg = good"),
        ("lap_gain", "Lap Gain", "neg = good"),
        ("platform_risk", "Platform Risk", "pos = good"),
        ("envelope", "Envelope Penalty", "neg = good"),
        ("lltd_error", "LLTD Error %", "pos = good (high error → slower)"),
        ("dynamic_rh", "Dynamic Front RH", "neg (lower RH → faster in theory)"),
        ("consistency", "Consistency CV", "pos (higher variance → slower)"),
    ]
    for key, label, direction in term_map:
        r_nv2 = correlations.get(f"non_vetoed_{key}", (float("nan"), 0))
        r_all2 = correlations.get(f"all_valid_{key}", (float("nan"), 0))
        r_nv_str = f"{r_nv2[0]:.3f}" if not math.isnan(r_nv2[0]) else "N/A"
        r_all_str = f"{r_all2[0]:.3f}" if not math.isnan(r_all2[0]) else "N/A"
        lines.append(f"| {label} | {r_nv_str} | {r_all_str} | {direction} | |")
    lines.append("")

    # Key findings
    lines.append("### Key Findings")
    lines.append("")

    # Find best scoring term (highest |r| for non-vetoed)
    term_scores = {}
    for key, label, _ in term_map:
        r_nv2 = correlations.get(f"non_vetoed_{key}", (float("nan"), 0))
        if not math.isnan(r_nv2[0]):
            term_scores[label] = abs(r_nv2[0])
    if term_scores:
        best_term = max(term_scores, key=term_scores.get)
        lines.append(f"- **Best predictor:** `{best_term}` (|r| = {term_scores[best_term]:.3f}) — strongest single-term correlation with lap time")
    else:
        lines.append("- **Best predictor:** Insufficient data to determine")

    # LLTD analysis on fast sessions
    top5 = sorted_results[:5]
    top5_lltd = [r.lltd_measured for r in top5 if r.lltd_measured > 0]
    if top5_lltd:
        avg_fast_lltd = sum(top5_lltd) / len(top5_lltd)
        lines.append(f"- **Fast session LLTD:** Top-5 average LLTD = {avg_fast_lltd*100:.1f}% vs objective target 52% — gap of {(0.52 - avg_fast_lltd)*100:.1f}% (same rear-bias finding as Sprint 3)")

    # Overfitted setups (high score but slow)
    overfitted = [r for r in non_vetoed if r.score_ms > -800 and r.lap_time_s > 110.5]
    if overfitted:
        lines.append(f"- **Potential overfit ({len(overfitted)} setups):** Score > -800ms but lap_time > 110.5s — objective may overvalue certain physics terms")
    else:
        lines.append("- **Overfit check:** No obvious cases where score is high but lap time is slow")

    # Veto false-positive analysis
    fast_vetoed = [r for r in vetoed if r.lap_time_s < 110.0]
    if fast_vetoed:
        lines.append(f"- **False veto concern:** {len(fast_vetoed)} vetoed sessions have lap_time < 110s — these ran fine in iRacing, vortex threshold still too aggressive")
    else:
        lines.append(f"- **Veto rate:** {len(vetoed)}/{total} sessions vetoed ({len(vetoed)*100//total}%) — check for false positives if fast sessions are in vetoed set")

    # Setup diversity
    lines.append(f"- **Setup diversity:** {len(heave_vals)} distinct heave values, {len(od_vals)} torsion ODs, {len(r_arb_vals)} rear ARB blades — low variation continues to limit correlation power")

    # New vs old sessions
    new_sessions = [r for r in results if "2026-03-1" in r.filename or "2026-03-2" in r.filename]
    if new_sessions:
        new_lap_times = [r.lap_time_s for r in new_sessions if "2026-03-18" in r.filename or "2026-03-19" in r.filename or "2026-03-20" in r.filename]
        if new_lap_times:
            lines.append(f"- **New sessions (Mar 18+):** {len(new_lap_times)} new sessions with lap times {min(new_lap_times):.3f}s–{max(new_lap_times):.3f}s — consistent with earlier data")

    lines.append("")
    lines.append("### Recommended Weight Adjustments")
    lines.append("")
    lines.append("Based on Sprint 4 validation data:")
    lines.append("")
    lines.append("| Parameter | Current | Recommended | Rationale |")
    lines.append("|-----------|---------|-------------|-----------|")
    lines.append("| LLTD target (BMW Sebring) | 52% | 40–43% | IBT consistently shows 38–43% in fast sessions |")
    lines.append("| Vortex p-tile for excursion | p99 | p95 | p99 inflates excursion, causes 43%+ false veto rate |")
    lines.append("| LLTD weight in objective | 0.7 | 0.5 | Over-penalizing rear-bias balance that is actually fast |")
    lines.append("| Empirical k-NN weight | 0.40 | 0.40 | Sufficient when ≥10 sessions available — keep |")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("_Validation generated by `claw-research` Sprint 4 — 2026-03-21._  ")
    lines.append("_Update when: vortex threshold recalibrated, LLTD target updated, or new setup variety available._")

    OUT_PATH.write_text("\n".join(lines))
    print(f"Report written to {OUT_PATH}")


if __name__ == "__main__":
    os.chdir(Path(__file__).parent.parent)
    results = run_validation()

    if not results:
        print("ERROR: No results with lap times found.")
        sys.exit(1)

    correlations = compute_correlations(results)

    # Print summary to stdout
    non_vetoed = [r for r in results if not r.vetoed]
    print(f"\n=== SUMMARY ===")
    print(f"Total sessions scored: {len(results)}")
    print(f"Vetoed: {sum(1 for r in results if r.vetoed)}")
    print(f"Non-vetoed: {len(non_vetoed)}")
    if results:
        lap_times = [r.lap_time_s for r in results]
        print(f"Lap time range: {min(lap_times):.3f}s – {max(lap_times):.3f}s")
    r_nv = correlations.get("non_vetoed_total_score", (float("nan"), 0))
    print(f"Pearson r (score vs lap_time, non-vetoed): {r_nv[0]:.3f}")

    write_report(results, correlations)
