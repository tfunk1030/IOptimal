"""ASCII terminal report for multi-session comparison analysis.

Follows the existing 63-char width ASCII report style used in
analyzer/report.py and pipeline/report.py.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from comparison.compare import (
    ComparisonResult,
    CornerComparison,
    SETUP_PARAMS,
    TELEMETRY_METRICS,
)
from comparison.score import CATEGORY_LABELS, ScoringResult, SessionScore
from comparison.synthesize import SynthesisResult

WIDTH = 63


def _header(title: str) -> str:
    return "\n" + "=" * WIDTH + f"\n  {title}\n" + "=" * WIDTH


def _subheader(title: str) -> str:
    return f"\n  {title}\n  " + "-" * (WIDTH - 4)


def _best_marker(values: list, idx: int, lower_is_better: bool | None) -> str:
    """Return '*' if this value is the best among the list."""
    if lower_is_better is None or not values:
        return " "
    filtered = [v for v in values if v is not None and v != 0]
    if not filtered:
        return " "
    if lower_is_better:
        best = min(filtered)
    else:
        best = max(filtered)
    val = values[idx]
    if val is not None and val == best and len(set(filtered)) > 1:
        return "*"
    return " "


# ── Main report formatter ──────────────────────────────────────


def format_comparison_report(
    comparison: ComparisonResult,
    scoring: ScoringResult,
    synthesis: SynthesisResult | None = None,
) -> str:
    """Generate the full multi-session comparison report.

    Sections:
    1. Header + session overview
    2. Setup comparison table
    3. Telemetry comparison
    4. Corner-by-corner comparison
    5. Category rankings
    6. Causal analysis
    7. Best setup
    8. Synthesized setup (if available)
    """
    lines: list[str] = []
    sessions = comparison.sessions
    n = len(sessions)

    # Truncated session labels for table columns
    col_labels = [s.label[:10] for s in sessions]
    col_w = max(8, max(len(l) for l in col_labels) + 1)

    # ── Section 1: Header ──
    lines.append("=" * WIDTH)
    lines.append("  MULTI-SESSION COMPARISON ANALYSIS")
    lines.append("=" * WIDTH)
    lines.append(f"  Car:      {sessions[0].car_name or 'Unknown'}")
    lines.append(f"  Track:    {sessions[0].track_name}")
    lines.append(f"  Sessions: {n}")

    # Check for different wing angles
    wings = set(s.wing_angle for s in sessions)
    if len(wings) > 1:
        lines.append(f"  NOTE: Different wing angles detected: {sorted(wings)}")

    lines.append("")

    # ── Session overview table ──
    lines.append(_subheader("Session Overview"))
    lines.append(f"  {'Session':<15s} {'Lap':>7s} {'Time':>8s} {'Assessment':>12s} {'Problems':>8s}")
    lines.append("  " + "-" * (WIDTH - 4))
    for i, s in enumerate(sessions):
        rank = ""
        for ss in scoring.scores:
            if ss.session.label == s.label:
                rank = f"#{ss.rank}"
                break
        lines.append(
            f"  {s.label:<15s} {s.lap_number:>7d} "
            f"{s.lap_time_s:>8.3f} {s.diagnosis.assessment:>12s} "
            f"{len(s.diagnosis.problems):>8d}"
        )

    # ── Section 2: Setup Comparison ──
    lines.append(_header("SETUP COMPARISON"))
    lines.append(_format_setup_table(comparison, col_labels, col_w))

    # ── Section 3: Telemetry Comparison ──
    lines.append(_header("TELEMETRY COMPARISON"))
    lines.append(_format_telemetry_table(comparison, col_labels, col_w))

    # ── Section 4: Corner-by-Corner ──
    if comparison.corner_comparisons:
        lines.append(_header("CORNER-BY-CORNER COMPARISON"))
        lines.append(_format_corner_comparison(comparison, col_labels))

    # ── Section 5: Category Rankings ──
    lines.append(_header("PERFORMANCE RANKINGS"))
    lines.append(_format_rankings(scoring, col_labels, col_w))

    # ── Section 6: Causal Analysis ──
    lines.append(_header("CAUSAL ANALYSIS"))
    lines.append(_format_causal_analysis(comparison, scoring))

    # ── Section 7: Best Setup ──
    lines.append(_header("BEST SETUP"))
    best = scoring.scores[0]
    lines.append(f"  Winner: {best.session.label}")
    lines.append(f"  Lap Time: {best.session.lap_time_s:.3f}s")
    lines.append(f"  Overall Score: {best.overall_score:.1%}")
    lines.append("")
    if best.strengths:
        lines.append("  Strengths:")
        for s in best.strengths:
            lines.append(f"    + {s}")
    if best.weaknesses:
        lines.append("  Weaknesses:")
        for w in best.weaknesses:
            lines.append(f"    - {w}")

    # ── Section 8: Synthesized Setup ──
    if synthesis is not None:
        lines.append(_header("SYNTHESIZED OPTIMAL SETUP"))
        lines.append(_format_synthesis(synthesis, best))

    lines.append("")
    lines.append("=" * WIDTH)
    lines.append("  * = best value across sessions")
    lines.append("=" * WIDTH)

    return "\n".join(lines)


# ── Table formatters ────────────────────────────────────────────


def _format_setup_table(
    comparison: ComparisonResult,
    col_labels: list[str],
    col_w: int,
) -> str:
    """N-way setup parameter comparison table."""
    lines: list[str] = []
    n = len(comparison.sessions)

    # Header row
    hdr = f"  {'Parameter':<20s}"
    for lbl in col_labels:
        hdr += f" {lbl:>{col_w}s}"
    hdr += f" {'Delta':>{col_w}s}"
    lines.append(hdr)
    lines.append("  " + "-" * (WIDTH - 4))

    for name, attr, units in SETUP_PARAMS:
        vals = comparison.setup_deltas.get(name, [None] * n)
        # Skip if all values are the same or all None/0
        non_none = [v for v in vals if v is not None]
        if not non_none:
            continue
        if len(set(non_none)) <= 1:
            continue

        row = f"  {name:<20s}"
        for v in vals:
            if v is None:
                row += f" {'N/A':>{col_w}s}"
            elif isinstance(v, float):
                row += f" {v:>{col_w}.1f}"
            else:
                row += f" {v:>{col_w}}"

        # Delta (max - min)
        numeric = [v for v in vals if isinstance(v, (int, float))]
        if len(numeric) >= 2:
            delta = max(numeric) - min(numeric)
            row += f" {delta:>{col_w}.1f}"
        else:
            row += f" {'':>{col_w}s}"

        lines.append(row)

    return "\n".join(lines)


def _format_telemetry_table(
    comparison: ComparisonResult,
    col_labels: list[str],
    col_w: int,
) -> str:
    """N-way telemetry metric comparison with best markers."""
    lines: list[str] = []
    n = len(comparison.sessions)

    hdr = f"  {'Metric':<20s}"
    for lbl in col_labels:
        hdr += f" {lbl:>{col_w}s}"
    lines.append(hdr)
    lines.append("  " + "-" * (WIDTH - 4))

    for name, attr, units, lower_better in TELEMETRY_METRICS:
        vals = comparison.telemetry_deltas.get(name, [None] * n)
        non_none = [v for v in vals if v is not None and v != 0]
        if not non_none:
            continue

        row = f"  {name:<20s}"
        for i, v in enumerate(vals):
            if v is None or v == 0:
                row += f" {'N/A':>{col_w}s}"
            else:
                marker = _best_marker(vals, i, lower_better)
                if isinstance(v, float):
                    formatted = f"{v:.2f}{marker}"
                else:
                    formatted = f"{v}{marker}"
                row += f" {formatted:>{col_w}s}"
        lines.append(row)

    return "\n".join(lines)


def _format_corner_comparison(
    comparison: ComparisonResult,
    col_labels: list[str],
) -> str:
    """Top corners by time-loss variance across sessions."""
    lines: list[str] = []
    n = len(comparison.sessions)
    corners = comparison.corner_comparisons

    # Sort corners by variance in time loss across sessions
    corner_variance: list[tuple[float, CornerComparison]] = []
    for cc in corners:
        losses = []
        for c in cc.per_session:
            if c is not None:
                losses.append(c.delta_to_min_time_s)
        if len(losses) >= 2:
            variance = max(losses) - min(losses)
            corner_variance.append((variance, cc))

    corner_variance.sort(key=lambda x: x[0], reverse=True)
    top_corners = corner_variance[:8]

    if not top_corners:
        lines.append("  No matched corners with time loss data.")
        return "\n".join(lines)

    lines.append("  Top corners by setup sensitivity (time loss delta):")
    lines.append("")

    for _var, cc in top_corners:
        lines.append(f"  Corner {cc.corner_id} ({cc.direction}, {cc.speed_class})")

        for i, c in enumerate(cc.per_session):
            lbl = col_labels[i] if i < len(col_labels) else f"S{i+1}"
            if c is None:
                lines.append(f"    {lbl:<12s} (not detected)")
            else:
                apex = f"{c.apex_speed_kph:.0f}kph"
                loss = f"dt={c.delta_to_min_time_s:+.3f}s"
                us = f"US={c.understeer_mean_deg:.1f}°"
                lines.append(f"    {lbl:<12s} {apex:>8s} {loss:>12s} {us:>10s}")
        lines.append("")

    return "\n".join(lines)


def _format_rankings(
    scoring: ScoringResult,
    col_labels: list[str],
    col_w: int,
) -> str:
    """Category scores table showing each session's performance."""
    lines: list[str] = []

    # Map scores back to original session order
    scores_by_label = {ss.session.label: ss for ss in scoring.scores}

    # Header
    hdr = f"  {'Category':<20s}"
    for lbl in col_labels:
        hdr += f" {lbl:>{col_w}s}"
    lines.append(hdr)
    lines.append("  " + "-" * (WIDTH - 4))

    for cat_key, cat_label in CATEGORY_LABELS.items():
        row = f"  {cat_label:<20s}"
        best_idx = scoring.best_per_category.get(cat_key)
        for i, lbl in enumerate(col_labels):
            # Find this session's score
            sess_label = None
            for s in scoring.scores:
                if s.session.label[:10] == lbl:
                    sess_label = s.session.label
                    break
            if sess_label and sess_label in scores_by_label:
                val = scores_by_label[sess_label].category_scores.get(cat_key, 0)
                marker = "*" if i == best_idx else " "
                row += f" {val:>{col_w - 1}.0%}{marker}"
            else:
                row += f" {'N/A':>{col_w}s}"
        lines.append(row)

    # Overall
    lines.append("  " + "-" * (WIDTH - 4))
    row = f"  {'OVERALL':<20s}"
    for lbl in col_labels:
        for ss in scoring.scores:
            if ss.session.label[:10] == lbl:
                row += f" {ss.overall_score:>{col_w - 1}.0%} "
                break
    lines.append(row)

    # Ranks
    row = f"  {'RANK':<20s}"
    for lbl in col_labels:
        for ss in scoring.scores:
            if ss.session.label[:10] == lbl:
                row += f" {'#' + str(ss.rank):>{col_w}s}"
                break
    lines.append(row)

    return "\n".join(lines)


def _format_causal_analysis(
    comparison: ComparisonResult,
    scoring: ScoringResult,
) -> str:
    """Identify the most impactful setup differences and explain their effects."""
    lines: list[str] = []
    sessions = comparison.sessions
    n = len(sessions)

    if n < 2:
        lines.append("  Need at least 2 sessions for causal analysis.")
        return "\n".join(lines)

    # Find parameters with the largest spread that correlate with performance
    best = scoring.scores[0].session
    worst = scoring.scores[-1].session

    lines.append(f"  Comparing best ({best.label}) vs worst ({worst.label}):")
    lines.append("")

    # Key setup differences
    diffs: list[tuple[str, float, float, str]] = []
    for name, attr, units in SETUP_PARAMS:
        best_val = getattr(best.setup, attr, None)
        worst_val = getattr(worst.setup, attr, None)
        if best_val is None or worst_val is None:
            continue
        if isinstance(best_val, (int, float)) and isinstance(worst_val, (int, float)):
            delta = best_val - worst_val
            if abs(delta) > 0.01:
                diffs.append((name, best_val, worst_val, units))

    # Sort by absolute delta magnitude (normalized by typical range)
    diffs.sort(key=lambda d: abs(d[1] - d[2]), reverse=True)

    # Show top differences with their telemetry effects
    for name, best_val, worst_val, units in diffs[:6]:
        delta = best_val - worst_val
        direction = "higher" if delta > 0 else "lower"
        lines.append(f"  {name}: {best_val:.1f} vs {worst_val:.1f} {units} ({direction} in best)")

        # Find correlated telemetry effects
        effects = _find_telemetry_effects(name, best, worst)
        for effect in effects[:2]:
            lines.append(f"    -> {effect}")
        lines.append("")

    return "\n".join(lines)


def _find_telemetry_effects(
    param_name: str,
    best_session,
    worst_session,
) -> list[str]:
    """Find telemetry metric changes that correlate with a setup parameter change."""
    effects: list[str] = []

    # Map setup parameter domains to related telemetry metrics
    param_effects: dict[str, list[tuple[str, str, bool]]] = {
        "Front Heave": [
            ("front_rh_std_mm", "Front RH variance", True),
            ("bottoming_event_count_front", "Front bottoming", True),
            ("front_rh_excursion_measured_mm", "Front RH excursion", True),
        ],
        "Rear Third": [
            ("rear_rh_std_mm", "Rear RH variance", True),
            ("bottoming_event_count_rear", "Rear bottoming", True),
        ],
        "Front ARB Blade": [
            ("lltd_measured", "LLTD", None),
            ("understeer_mean_deg", "Understeer", True),
        ],
        "Rear ARB Blade": [
            ("lltd_measured", "LLTD", None),
            ("understeer_mean_deg", "Understeer", True),
            ("body_slip_p95_deg", "Body slip", True),
        ],
        "Front Camber": [
            ("front_temp_spread_lf_c", "LF temp spread", True),
            ("front_temp_spread_rf_c", "RF temp spread", True),
        ],
        "Rear Camber": [
            ("rear_temp_spread_lr_c", "LR temp spread", True),
            ("rear_temp_spread_rr_c", "RR temp spread", True),
        ],
        "Front LS Rbd": [
            ("front_rh_settle_time_ms", "Front settle time", True),
            ("yaw_rate_correlation", "Yaw correlation", False),
        ],
        "Rear LS Rbd": [
            ("rear_rh_settle_time_ms", "Rear settle time", True),
        ],
        "Brake Bias": [
            ("front_slip_ratio_p95", "Front braking slip", True),
            ("rear_slip_ratio_p95", "Rear traction slip", True),
        ],
    }

    related = param_effects.get(param_name, [])
    for attr, label, lower_better in related:
        best_val = getattr(best_session.measured, attr, None)
        worst_val = getattr(worst_session.measured, attr, None)
        if best_val is None or worst_val is None:
            continue
        delta = best_val - worst_val
        if abs(delta) < 0.01:
            continue
        better = ""
        if lower_better is True:
            better = " (better)" if delta < 0 else " (worse)"
        elif lower_better is False:
            better = " (better)" if delta > 0 else " (worse)"
        effects.append(f"{label}: {best_val:.2f} vs {worst_val:.2f}{better}")

    return effects


def _format_synthesis(synthesis: SynthesisResult, best_score: SessionScore) -> str:
    """Format the synthesized setup section."""
    lines: list[str] = []

    lines.append("  Based on: reasoning-driven multi-session solve")
    lines.append(f"  Wing: {synthesis.wing_angle}°  Fuel: {synthesis.fuel_l:.0f}L")
    if synthesis.solve_basis:
        lines.append(
            f"  Basis: {synthesis.solve_basis}  "
            f"Authority: {synthesis.authority_session_label or '?'}  "
            f"Best: {synthesis.best_session_label or '?'}"
        )
    if synthesis.selected_candidate_family is not None:
        line = f"  Candidate family: {synthesis.selected_candidate_family}"
        if synthesis.selected_candidate_score is not None:
            line += f"  (score {synthesis.selected_candidate_score:.3f})"
        lines.append(line)
    lines.append("")

    # Merged modifiers
    if synthesis.modifiers.reasons:
        lines.append("  Solve Modifiers:")
        for r in synthesis.modifiers.reasons:
            lines.append(f"    {r}")
        lines.append("")

    # Key solver outputs
    step1 = synthesis.step1
    step2 = synthesis.step2
    step3 = synthesis.step3
    step4 = synthesis.step4
    step5 = synthesis.step5
    step6 = synthesis.step6

    lines.append("  Synthesized Setup Values:")
    lines.append(f"    Dynamic Front RH: {step1.dynamic_front_rh_mm:.1f} mm")
    lines.append(f"    Dynamic Rear RH:  {step1.dynamic_rear_rh_mm:.1f} mm")
    lines.append(f"    DF Balance:       {step1.df_balance_pct:.2f}%")
    lines.append(f"    Front Heave:      {step2.front_heave_nmm:.0f} N/mm")
    lines.append(f"    Rear Third:       {step2.rear_third_nmm:.0f} N/mm")
    lines.append(f"    Front Torsion OD: {step3.front_torsion_od_mm:.1f} mm")
    lines.append(f"    Rear Spring:      {step3.rear_spring_rate_nmm:.0f} N/mm")
    lines.append(f"    LLTD:             {step4.lltd_achieved:.1%}")
    lines.append(f"    Front Camber:     {step5.front_camber_deg:.1f}°")
    lines.append(f"    Rear Camber:      {step5.rear_camber_deg:.1f}°")
    lines.append(f"    Front Toe:        {step5.front_toe_mm:.2f} mm")
    lines.append(f"    Rear Toe:         {step5.rear_toe_mm:.2f} mm")
    lines.append("")

    # Dampers
    lines.append("    Dampers:       LF    RF    LR    RR")
    lines.append(
        f"      LS Comp: {step6.lf.ls_comp:5d} {step6.rf.ls_comp:5d} "
        f"{step6.lr.ls_comp:5d} {step6.rr.ls_comp:5d}"
    )
    lines.append(
        f"      LS Rbd:  {step6.lf.ls_rbd:5d} {step6.rf.ls_rbd:5d} "
        f"{step6.lr.ls_rbd:5d} {step6.rr.ls_rbd:5d}"
    )
    lines.append(
        f"      HS Comp: {step6.lf.hs_comp:5d} {step6.rf.hs_comp:5d} "
        f"{step6.lr.hs_comp:5d} {step6.rr.hs_comp:5d}"
    )
    lines.append(
        f"      HS Rbd:  {step6.lf.hs_rbd:5d} {step6.rf.hs_rbd:5d} "
        f"{step6.lr.hs_rbd:5d} {step6.rr.hs_rbd:5d}"
    )
    lines.append("")

    # Supporting
    if synthesis.supporting:
        sup = synthesis.supporting
        lines.append(f"    Brake Bias:  {sup.brake_bias_pct:.1f}%")
        lines.append(f"    Diff Preload: {sup.diff_preload_nm:.0f} Nm")
        lines.append(f"    TC: gain={sup.tc_gain} slip={sup.tc_slip}")
        lines.append("")

    # Explanations
    if synthesis.explanations:
        lines.append("  Synthesis Reasoning:")
        for exp in synthesis.explanations:
            sources = ", ".join(exp.influenced_by)
            lines.append(f"    {exp.parameter}: {exp.value}")
            lines.append(f"      {exp.reasoning}")
            lines.append(f"      Source: {sources}")
        lines.append("")

    if synthesis.solver_notes:
        lines.append("  Solve Context:")
        for note in synthesis.solver_notes[:6]:
            lines.append(f"    {note}")
        lines.append("")

    # Confidence
    if synthesis.confidence:
        lines.append("  Confidence Assessment:")
        for area, level in synthesis.confidence.items():
            tag = {"high": "[HIGH]", "medium": "[MED]", "low": "[LOW]"}.get(level, level)
            lines.append(f"    {tag:>6s}  {area}")

    # Comparison: best session vs synthesized
    lines.append("")
    lines.append(_subheader("Best Session vs Synthesized"))
    best_setup = best_score.session.setup
    lines.append(f"  {'Parameter':<22s} {'Best Sess':>10s} {'Synth':>10s} {'Delta':>8s}")
    lines.append("  " + "-" * (WIDTH - 4))

    diffs = [
        ("Front Heave", best_setup.front_heave_nmm, step2.front_heave_nmm, "N/mm"),
        ("Rear Third", best_setup.rear_third_nmm, step2.rear_third_nmm, "N/mm"),
        ("Front Torsion OD", best_setup.front_torsion_od_mm, step3.front_torsion_od_mm, "mm"),
        ("Rear Spring", best_setup.rear_spring_nmm, step3.rear_spring_rate_nmm, "N/mm"),
        ("Front ARB Blade", float(best_setup.front_arb_blade), float(step4.farb_blade_locked), ""),
        ("Rear ARB Blade", float(best_setup.rear_arb_blade), float(step4.rarb_blade_slow_corner), ""),
        ("Front Camber", best_setup.front_camber_deg, step5.front_camber_deg, "°"),
        ("Rear Camber", best_setup.rear_camber_deg, step5.rear_camber_deg, "°"),
    ]
    for name, old_val, new_val, units in diffs:
        delta = new_val - old_val
        marker = " **" if abs(delta) > 0.01 else ""
        lines.append(
            f"  {name:<22s} {old_val:>10.1f} {new_val:>10.1f} "
            f"{delta:>+8.1f}{units}{marker}"
        )

    return "\n".join(lines)


# ── JSON export ─────────────────────────────────────────────────


def save_comparison_json(
    comparison: ComparisonResult,
    scoring: ScoringResult,
    synthesis: SynthesisResult | None,
    output_path: str,
) -> None:
    """Save comparison results as JSON."""
    data: dict = {
        "sessions": [],
        "rankings": [],
        "best_per_category": scoring.best_per_category,
    }

    for s in comparison.sessions:
        data["sessions"].append({
            "label": s.label,
            "ibt_path": s.ibt_path,
            "lap_time_s": s.lap_time_s,
            "lap_number": s.lap_number,
            "track_name": s.track_name,
            "wing_angle": s.wing_angle,
            "assessment": s.diagnosis.assessment,
            "problems": len(s.diagnosis.problems),
            "driver_style": s.driver.style,
        })

    for ss in scoring.scores:
        data["rankings"].append({
            "label": ss.session.label,
            "rank": ss.rank,
            "overall_score": ss.overall_score,
            "category_scores": ss.category_scores,
            "strengths": ss.strengths,
            "weaknesses": ss.weaknesses,
        })

    if synthesis is not None:
        data["synthesis"] = {
            "wing_angle": synthesis.wing_angle,
            "fuel_l": synthesis.fuel_l,
            "best_session": synthesis.best_session_label,
            "authority_session": synthesis.authority_session_label,
            "solve_basis": synthesis.solve_basis,
            "selected_candidate_family": synthesis.selected_candidate_family,
            "selected_candidate_score": synthesis.selected_candidate_score,
            "modifiers": {
                "df_balance_offset_pct": synthesis.modifiers.df_balance_offset_pct,
                "lltd_offset": synthesis.modifiers.lltd_offset,
                "front_heave_floor_nmm": synthesis.modifiers.front_heave_min_floor_nmm,
                "damping_ratio_scale": synthesis.modifiers.damping_ratio_scale,
            },
            "explanations": [
                {
                    "parameter": e.parameter,
                    "value": e.value,
                    "reasoning": e.reasoning,
                    "influenced_by": e.influenced_by,
                }
                for e in synthesis.explanations
            ],
            "confidence": synthesis.confidence,
            "solver_notes": synthesis.solver_notes,
        }

    # Setup deltas
    data["setup_deltas"] = {}
    for name, vals in comparison.setup_deltas.items():
        data["setup_deltas"][name] = [
            v if not isinstance(v, float) or v == v else None  # handle NaN
            for v in vals
        ]

    # Telemetry deltas
    data["telemetry_deltas"] = {}
    for name, vals in comparison.telemetry_deltas.items():
        data["telemetry_deltas"][name] = [
            v if not isinstance(v, float) or v == v else None
            for v in vals
        ]

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
