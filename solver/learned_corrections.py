"""Bridge between the learner's knowledge store and the physics solver.

Provides a function that loads empirical corrections and applies them
to the solver's inputs. The solver remains physics-first — corrections
only adjust target values and calibration constants, never override
the constraint satisfaction logic.

Usage in solver/solve.py:
    from solver.learned_corrections import apply_learned_corrections
    corrections = apply_learned_corrections(car, track_name)
    # corrections.heave_m_eff_front_kg overrides car.heave_spring.front_m_eff_kg
    # etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LearnedCorrections:
    """Corrections derived from accumulated session data.

    Each field is Optional — None means "no data, use physics default."
    A non-None value means "empirical data suggests this correction."
    """

    # Heave effective mass corrections (overrides car_model values)
    heave_m_eff_front_kg: float | None = None
    heave_m_eff_rear_kg: float | None = None

    # Roll gradient (measured deg/g, informs body roll predictions)
    roll_gradient_deg_per_g: float | None = None

    # LLTD measured baseline (informs ARB solver target)
    lltd_measured_baseline: float | None = None

    # Aero compression corrections
    aero_compression_front_mm: float | None = None
    aero_compression_rear_mm: float | None = None

    # Calibrated roll gains from tyre thermal spread analysis
    calibrated_front_roll_gain: float | None = None
    calibrated_rear_roll_gain: float | None = None

    # Damping ratio scale from driver history
    damping_ratio_scale: float | None = None

    # Number of sessions informing these corrections
    session_count: int = 0
    confidence: str = "no_data"  # "no_data" | "low" | "medium" | "high"

    # What was applied and why
    applied: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.applied:
            return "No learned corrections applied (insufficient data)."
        lines = [
            f"Learned Corrections ({self.session_count} sessions, {self.confidence} confidence):",
        ]
        for a in self.applied:
            lines.append(f"  • {a}")
        return "\n".join(lines)


def apply_learned_corrections(
    car_canonical: str,
    track_name: str,
    min_sessions: int = 3,
    verbose: bool = False,
) -> LearnedCorrections:
    """Load empirical corrections from the knowledge store.

    Args:
        car_canonical: Car canonical name (e.g., "bmw")
        track_name: Partial track name match (e.g., "sebring")
        min_sessions: Minimum sessions required to apply corrections.
            Below this threshold, returns empty corrections (physics-only).
            Note: 2 is a weak gate — corrections from only 2 sessions may be
            noisy (especially m_eff which uses lap-wide statistics). Consider
            raising to 3-5 once the knowledge base has enough data.
        verbose: Print what corrections are being applied.

    Returns:
        LearnedCorrections with empirical values (or None for no data)
    """
    result = LearnedCorrections()

    try:
        from learner.knowledge_store import KnowledgeStore
        from learner.recall import KnowledgeRecall
    except ImportError:
        return result

    store = KnowledgeStore()
    recall = KnowledgeRecall(store)

    # Find matching track name
    track_key = track_name.lower().split()[0]  # "sebring"

    # Count sessions for this specific car/track
    obs = store.list_observations(car=car_canonical)
    matching = [o for o in obs if track_key in o.get("track", "").lower()]
    result.session_count = len(matching)

    if result.session_count < min_sessions:
        result.confidence = "low" if result.session_count > 0 else "no_data"
        if verbose:
            print(f"  Learner: {result.session_count} sessions for {car_canonical}/{track_key} "
                  f"(need {min_sessions} for corrections)")
        return result

    result.confidence = "high" if result.session_count >= 8 else "medium"

    # Load empirical model
    model_id = f"{car_canonical}_{track_key}_empirical"
    model_data = store.load_model(model_id)
    if model_data is None:
        return result

    corrections = model_data.get("corrections", {})

    # ── Apply m_eff correction ──
    m_eff_mean = corrections.get("m_eff_front_empirical_mean")
    if m_eff_mean is not None and 100.0 < m_eff_mean < 4000.0:
        result.heave_m_eff_front_kg = m_eff_mean
        result.applied.append(
            f"Front m_eff: {m_eff_mean:.1f} kg (empirical, "
            f"std={corrections.get('m_eff_front_empirical_std', 0):.1f})"
        )

    # ── Apply roll gradient ──
    rg_mean = corrections.get("roll_gradient_measured_mean")
    if rg_mean is not None and rg_mean > 0.1:
        result.roll_gradient_deg_per_g = rg_mean
        result.applied.append(
            f"Roll gradient: {rg_mean:.3f} deg/g (measured across "
            f"{corrections.get('roll_gradient_sample_count', '?')} sessions)"
        )

    # ── Apply LLTD baseline ──
    lltd_mean = corrections.get("lltd_measured_mean")
    if lltd_mean is not None and lltd_mean > 0:
        result.lltd_measured_baseline = lltd_mean
        result.applied.append(
            f"LLTD baseline: {lltd_mean:.3f} (measured, "
            f"std={corrections.get('lltd_measured_std', 0):.3f})"
        )

    # ── Apply aero compression ──
    ac_front = corrections.get("aero_compression_front_mean_mm")
    if ac_front is not None and ac_front > 0:
        result.aero_compression_front_mm = ac_front
        result.applied.append(
            f"Aero compression front: {ac_front:.1f} mm (measured)"
        )

    ac_rear = corrections.get("aero_compression_rear_mean_mm")
    if ac_rear is not None and ac_rear > 0:
        result.aero_compression_rear_mm = ac_rear
        result.applied.append(
            f"Aero compression rear: {ac_rear:.1f} mm (measured)"
        )

    # ── Apply calibrated roll gains from tyre thermals ──
    front_rg = corrections.get("calibrated_front_roll_gain")
    rg_confidence = corrections.get("roll_gain_calibration_confidence", "insufficient")
    rg_samples = corrections.get("roll_gain_calibration_samples", 0)
    if front_rg is not None and rg_confidence in ("medium", "high"):
        result.calibrated_front_roll_gain = front_rg
        result.applied.append(
            f"Front roll_gain: {front_rg:.4f} (thermal calibration, "
            f"{rg_samples} sessions, {rg_confidence} confidence)"
        )
        rear_rg = corrections.get("calibrated_rear_roll_gain")
        if rear_rg is not None:
            result.calibrated_rear_roll_gain = rear_rg
            result.applied.append(
                f"Rear roll_gain: {rear_rg:.4f} (thermal calibration)"
            )

    if verbose and result.applied:
        print(result.summary())

    return result
