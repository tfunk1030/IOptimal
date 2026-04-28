from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from solver.objective import (
    COHERENCE_METRICS,
    COHERENCE_PENALTY_MS_PER_METRIC,
    COHERENCE_THRESHOLD_WORSENING,
    compute_coherence_outcome,
)


@dataclass
class CandidateScore:
    total: float
    safety: float
    performance: float
    stability: float
    confidence: float
    disruption_cost: float
    notes: list[str] = field(default_factory=list)
    # Coherence audit: predicted-improvements vs baseline_measured.
    # Mission Principle 6 — per-corner-phase impact must be net-positive.
    coherence_penalty_ms: float = 0.0
    coherence_worsening: tuple[str, ...] = ()
    coherence_improving: tuple[str, ...] = ()
    coherence_detail: dict[str, tuple[float | None, float | None, str]] = field(default_factory=dict)


def _safe(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _improvement(before: float | None, after: float | None, *, lower_better: bool = True, scale: float = 1.0) -> float:
    if before is None or after is None:
        return 0.5
    delta = (before - after) if lower_better else (after - before)
    return max(0.0, min(1.0, 0.5 + delta / max(scale, 1e-6)))


def _target_distance_improvement(
    before: float | None,
    after: float | None,
    *,
    target: float = 0.0,
    scale: float = 1.0,
) -> float:
    if before is None or after is None:
        return 0.5
    before_distance = abs(before - target)
    after_distance = abs(after - target)
    delta = before_distance - after_distance
    return max(0.0, min(1.0, 0.5 + delta / max(scale, 1e-6)))


def combine_candidate_score(
    *,
    safety: float,
    performance: float,
    stability: float,
    confidence: float,
    disruption_cost: float,
    notes: list[str] | None = None,
) -> CandidateScore:
    safety = max(0.0, min(1.0, safety))
    performance = max(0.0, min(1.0, performance))
    stability = max(0.0, min(1.0, stability))
    confidence = max(0.0, min(1.0, confidence))
    disruption_cost = max(0.0, min(1.0, disruption_cost))
    total = (
        safety * 0.25
        + performance * 0.25
        + stability * 0.15
        + confidence * 0.10
        + (1.0 - disruption_cost) * 0.25
    )
    return CandidateScore(
        total=round(total, 3),
        safety=round(safety, 3),
        performance=round(performance, 3),
        stability=round(stability, 3),
        confidence=round(confidence, 3),
        disruption_cost=round(disruption_cost, 3),
        notes=list(notes or []),
    )


def score_from_prediction(
    *,
    baseline_measured: Any,
    predicted: Any | None,
    prediction_confidence: float,
    disruption_cost: float,
    envelope_distance: float = 0.0,
    setup_distance: float = 0.0,
    legal_ok: bool = True,
    authority_score: float | None = None,
    state_risk: float = 0.0,
    baseline_loss_ms: float = 0.0,
    notes: list[str] | None = None,
) -> CandidateScore:
    """Score a candidate from predicted telemetry changes."""
    notes = list(notes or [])
    if predicted is None:
        empty_score = combine_candidate_score(
            safety=0.45,
            performance=0.45,
            stability=0.45,
            confidence=prediction_confidence,
            disruption_cost=disruption_cost,
            notes=notes + ["No predicted telemetry available; using neutral score."],
        )
        # No predicted telemetry: coherence check cannot run (returns 0.0
        # penalty + unavailable detail for every metric).
        coh_penalty, coh_worsen, coh_improve, coh_detail = compute_coherence_outcome(
            None, baseline_measured,
        )
        empty_score.coherence_penalty_ms = coh_penalty
        empty_score.coherence_worsening = coh_worsen
        empty_score.coherence_improving = coh_improve
        empty_score.coherence_detail = coh_detail
        return empty_score

    safety = (
        _improvement(_safe(getattr(baseline_measured, "front_heave_travel_used_pct", None)), _safe(getattr(predicted, "front_heave_travel_used_pct", None)), lower_better=True, scale=20.0)
        + _improvement(_safe(getattr(baseline_measured, "pitch_range_braking_deg", None)), _safe(getattr(predicted, "braking_pitch_deg", None)), lower_better=True, scale=0.8)
        + _improvement(_safe(getattr(baseline_measured, "front_braking_lock_ratio_p95", None)), _safe(getattr(predicted, "front_lock_p95", None)), lower_better=True, scale=0.04)
    ) / 3.0
    stability = (
        _improvement(_safe(getattr(baseline_measured, "rear_rh_std_mm", None)), _safe(getattr(predicted, "rear_rh_std_mm", None)), lower_better=True, scale=3.0)
        + _improvement(_safe(getattr(baseline_measured, "body_slip_p95_deg", None)), _safe(getattr(predicted, "body_slip_p95_deg", None)), lower_better=True, scale=2.0)
    ) / 2.0
    performance = (
        _target_distance_improvement(
            _safe(getattr(baseline_measured, "understeer_low_speed_deg", None)),
            _safe(getattr(predicted, "understeer_low_deg", None)),
            target=0.0,
            scale=1.0,
        )
        + _target_distance_improvement(
            _safe(getattr(baseline_measured, "understeer_high_speed_deg", None)),
            _safe(getattr(predicted, "understeer_high_deg", None)),
            target=0.0,
            scale=1.0,
        )
        + _improvement(_safe(getattr(baseline_measured, "rear_power_slip_ratio_p95", None)), _safe(getattr(predicted, "rear_power_slip_ratio_p95", None)), lower_better=True, scale=0.05)
    ) / 3.0
    # Scale performance by how much time is at stake: higher loss = more weight on fixing it.
    # 500ms estimated loss → 30% boost, capped there. No effect below ~50ms.
    if baseline_loss_ms > 0.0:
        loss_urgency = min(0.30, baseline_loss_ms / 500.0)
        performance = min(1.0, performance * (1.0 + loss_urgency))
    confidence_score = max(0.0, min(1.0, prediction_confidence))
    if authority_score is not None:
        confidence_score = max(0.0, min(1.0, confidence_score * 0.8 + authority_score * 0.2))
    if not legal_ok:
        confidence_score *= 0.7
    if envelope_distance > 0.0:
        confidence_score *= max(0.7, 1.0 - min(0.2, envelope_distance * 0.03))
    if setup_distance > 0.0:
        confidence_score *= max(0.75, 1.0 - min(0.15, setup_distance * 0.025))
    if state_risk > 0.0:
        confidence_score *= max(0.7, 1.0 - min(0.2, state_risk * 0.15))

    loss_note = f" (urgency boost from {baseline_loss_ms:.0f}ms est. loss)" if baseline_loss_ms > 50.0 else ""
    notes.extend(
        [
            f"Predicted safety score from travel/pitch/lock = {safety:.2f}",
            f"Predicted stability score from RH variance/body slip = {stability:.2f}",
            f"Predicted performance score from understeer/slip = {performance:.2f}{loss_note}",
            f"Prediction confidence after context penalties = {confidence_score:.2f}",
        ]
    )
    score = combine_candidate_score(
        safety=safety,
        performance=performance,
        stability=stability,
        confidence=confidence_score,
        disruption_cost=disruption_cost,
        notes=notes,
    )
    if envelope_distance > 0.0:
        score.total = round(max(0.0, score.total - min(0.08, envelope_distance * 0.01)), 3)
    if setup_distance > 0.0:
        score.total = round(max(0.0, score.total - min(0.06, setup_distance * 0.008)), 3)
    if not legal_ok:
        score.total = round(max(0.0, score.total - 0.08), 3)
        score.notes.append("Legality warning reduced total score.")
    if state_risk > 0.0:
        score.total = round(max(0.0, score.total - min(0.05, state_risk * 0.01)), 3)

    # ── Coherence (Mission Principle 6) ──────────────────────────────────
    # Penalise candidates whose own predictions worsen 5+ measured axes vs
    # baseline_measured. The `total` here is on a 0–1 scale; we map the
    # ms-domain penalty onto it so a 100ms penalty (5 worsening) drops the
    # total by ~0.10, a 200ms penalty (6 worsening) drops by ~0.20, etc.
    # This guarantees orthogonal terms can never rescue a candidate that
    # is incoherent on its own predictions.
    coh_penalty, coh_worsen, coh_improve, coh_detail = compute_coherence_outcome(
        predicted, baseline_measured,
    )
    score.coherence_penalty_ms = coh_penalty
    score.coherence_worsening = coh_worsen
    score.coherence_improving = coh_improve
    score.coherence_detail = coh_detail
    if coh_penalty > 0.0:
        # 100ms ↔ 0.10 score units (max drop 0.50 at 9-of-9 worsening = 500ms)
        score.total = round(max(0.0, score.total - min(0.50, coh_penalty / 1000.0)), 3)
        score.notes.append(
            f"Coherence penalty {coh_penalty:.0f}ms — predicted to worsen on "
            f"{len(coh_worsen)} of {len(COHERENCE_METRICS)} measured axes "
            f"({', '.join(coh_worsen[:3])}{'...' if len(coh_worsen) > 3 else ''})"
        )
    return score
