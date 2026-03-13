"""Generate a "What We've Learned" section for the engineering report.

This is the human-readable output of the learning system. It tells the
engineer what accumulated knowledge says about this car/track/setup
combination, what to watch for, and what the empirical data suggests
differently from the physics model.
"""

from __future__ import annotations

from learner.knowledge_store import KnowledgeStore
from learner.recall import KnowledgeRecall


def generate_learning_section(
    car: str,
    track: str,
    width: int = 63,
) -> str:
    """Generate the "ACCUMULATED KNOWLEDGE" section for the report.

    Returns empty string if no data is available, so callers can
    just concatenate without checking.
    """
    store = KnowledgeStore()

    # Quick check — skip entirely if no learnings directory or no data
    if not store.base.exists():
        return ""

    recall = KnowledgeRecall(store)
    n_sessions = recall.session_count(car=car)
    if n_sessions == 0:
        return ""

    # Count for this specific track
    obs_list = store.list_observations(car=car, track=track)
    n_track = len(obs_list)

    lines = [
        "=" * width,
        "  ACCUMULATED KNOWLEDGE",
        f"  ({n_track} sessions this track, {n_sessions} total for {car})",
        "=" * width,
    ]

    # ── Insights ──
    track_key = track.lower().split()[0]
    insight_id = f"{car}_{track_key}_insights"
    insights = store.load_insights(insight_id)

    if insights:
        key_insights = insights.get("key_insights", [])
        if key_insights:
            lines.append("")
            lines.append("  KEY INSIGHTS:")
            for i, insight in enumerate(key_insights[:6]):
                # Wrap long insights
                if len(insight) > width - 6:
                    words = insight.split()
                    line = "    "
                    for w in words:
                        if len(line) + len(w) + 1 > width - 2:
                            lines.append(line)
                            line = "      " + w
                        else:
                            line += (" " if len(line) > 4 else "") + w
                    if line.strip():
                        lines.append(line)
                else:
                    lines.append(f"    * {insight}")

        trends = insights.get("setup_trends", [])
        if trends:
            lines.append("")
            lines.append("  SETUP TRENDS:")
            for t in trends[:5]:
                lines.append(f"    -> {t}")

        unresolved = insights.get("unresolved_questions", [])
        if unresolved:
            lines.append("")
            lines.append("  RECURRING ISSUES:")
            for u in unresolved[:4]:
                lines.append(f"    ! {u}")

    # ── Empirical corrections ──
    model_id = f"{car}_{track_key}_empirical"
    model = store.load_model(model_id)

    if model:
        corrections = model.get("corrections", {})
        relationships = model.get("relationships", {})
        sensitivity = model.get("most_sensitive_parameters", [])

        if corrections:
            physics_diffs = []
            rg = corrections.get("roll_gradient_measured_mean", 0)
            if rg > 0:
                physics_diffs.append(
                    f"Roll gradient: {rg:.3f} deg/g measured"
                )
            lltd = corrections.get("lltd_measured_mean", 0)
            if lltd > 0:
                physics_diffs.append(
                    f"LLTD baseline: {lltd*100:.1f}% measured"
                )
            m_eff = corrections.get("m_eff_front_empirical_mean", 0)
            if m_eff > 0:
                physics_diffs.append(
                    f"Front m_eff: {m_eff:.0f} kg (empirical)"
                )

            if physics_diffs:
                lines.append("")
                lines.append("  EMPIRICAL CALIBRATIONS:")
                for pd in physics_diffs:
                    lines.append(f"    {pd}")

        if sensitivity:
            lines.append("")
            lines.append("  LAP TIME SENSITIVITY (most impactful):")
            for param, sens in sensitivity[:5]:
                direction = "faster" if sens < 0 else "slower"
                lines.append(f"    {param}: {abs(sens):.3f}s/unit ({direction})")

    # ── Recent delta findings ──
    deltas = store.list_deltas(car=car, track=track)
    high_conf = [d for d in deltas if d.get("confidence_level") == "high"]

    if high_conf:
        lines.append("")
        lines.append("  HIGH-CONFIDENCE FINDINGS:")
        for d in high_conf[-3:]:  # last 3 most recent
            finding = d.get("key_finding", "")
            if finding:
                if len(finding) > width - 6:
                    lines.append(f"    * {finding[:width-8]}...")
                else:
                    lines.append(f"    * {finding}")

    # ── "What to try next" from unresolved problems ──
    if insights and insights.get("unresolved_questions"):
        lines.append("")
        lines.append("  SUGGESTED EXPERIMENTS:")
        seen = set()
        for uq in insights["unresolved_questions"][:3]:
            # Parse the recurring problem to suggest an experiment
            if "balance" in uq.lower() and "understeer" in uq.lower():
                sugg = "Try RARB +1 blade to shift LLTD rear"
            elif "bottoming" in uq.lower() and "front" in uq.lower():
                sugg = "Try front heave +10 N/mm (isolate platform effect)"
            elif "settle" in uq.lower() or "damper" in uq.lower():
                sugg = "Try LS rebound +1 front (isolate damper response)"
            else:
                sugg = "Change ONE parameter to isolate its effect"

            if sugg not in seen:
                lines.append(f"    -> {sugg}")
                seen.add(sugg)

        lines.append(f"    (Single-variable changes give highest")
        lines.append(f"     confidence learnings)")

    lines.append("")
    lines.append("=" * width)

    return "\n".join(lines)
