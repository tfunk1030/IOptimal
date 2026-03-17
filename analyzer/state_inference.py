"""Convert many symptoms into a few root-cause car states.

Instead of a flat list of threshold violations, this module accumulates
evidence from multiple metrics to infer higher-level car states like
"front platform collapsing under braking" or "rear under-supported".
Each state carries severity, confidence, and recommended direction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analyzer.extract import MeasuredState
    from analyzer.setup_reader import CurrentSetup


@dataclass
class StateEvidence:
    """One piece of evidence supporting a car state inference."""

    metric: str
    value: float | None
    confidence: float  # 0-1: how much to trust this metric
    note: str


@dataclass
class CarStateIssue:
    """A diagnosed root-cause car state problem.

    Represents a higher-level understanding than individual threshold
    violations: e.g. "front_platform_collapse_braking" aggregates
    heave travel, pitch, and front lock evidence.
    """

    state_id: str
    severity: float  # 0-1: how bad is this
    confidence: float  # 0-1: how sure are we
    estimated_loss_ms: float  # estimated lap time cost
    implicated_steps: list[int]  # solver steps involved (1-6)
    evidence: list[StateEvidence] = field(default_factory=list)
    likely_causes: list[str] = field(default_factory=list)
    recommended_direction: str = ""


# --- State inference rules ---

# Each rule checks specific metrics and produces a CarStateIssue if
# the evidence is sufficient. Rules accumulate evidence from multiple
# sources for confidence.


def _check_front_platform_collapse_braking(measured: "MeasuredState") -> CarStateIssue | None:
    """Detect front platform collapse under braking.

    Evidence: high heave travel used under braking, excessive pitch,
    high front lock ratio.
    """
    evidence: list[StateEvidence] = []
    severity = 0.0

    braking_travel = measured.front_heave_travel_used_braking_pct
    if braking_travel > 80:
        severity += 0.4
        evidence.append(StateEvidence(
            metric="front_heave_travel_used_braking_pct",
            value=braking_travel,
            confidence=0.9,
            note=f"Braking heave travel {braking_travel:.0f}% (>80% threshold)",
        ))

    pitch_braking = measured.pitch_range_braking_deg
    if pitch_braking > 0.8:
        severity += 0.3
        evidence.append(StateEvidence(
            metric="pitch_range_braking_deg",
            value=pitch_braking,
            confidence=0.85,
            note=f"Braking pitch range {pitch_braking:.2f} deg (>0.8 threshold)",
        ))

    front_lock = measured.front_braking_lock_ratio_p95
    if front_lock > 0.06:
        severity += 0.3
        evidence.append(StateEvidence(
            metric="front_braking_lock_ratio_p95",
            value=front_lock,
            confidence=0.82,
            note=f"Front lock ratio {front_lock:.3f} (>0.06 threshold)",
        ))

    if len(evidence) < 2:
        return None

    return CarStateIssue(
        state_id="front_platform_collapse_braking",
        severity=min(1.0, severity),
        confidence=min(1.0, sum(e.confidence for e in evidence) / len(evidence)),
        estimated_loss_ms=severity * 200,
        implicated_steps=[1, 2],
        evidence=evidence,
        likely_causes=["front heave spring too soft", "front pushrod offset too low", "excessive brake bias forward"],
        recommended_direction="stiffen front heave spring or raise front ride height",
    )


def _check_front_platform_near_limit_high_speed(measured: "MeasuredState") -> CarStateIssue | None:
    """Detect front platform running near its travel limit at speed."""
    evidence: list[StateEvidence] = []
    severity = 0.0

    travel_pct = measured.front_heave_travel_used_pct
    if travel_pct > 75:
        sev_contribution = min(0.5, (travel_pct - 75) / 50)
        severity += sev_contribution
        evidence.append(StateEvidence(
            metric="front_heave_travel_used_pct",
            value=travel_pct,
            confidence=0.9,
            note=f"Heave travel {travel_pct:.0f}% used at speed (>75% threshold)",
        ))

    front_rh_std = measured.front_rh_std_mm
    if front_rh_std > 8.0:
        severity += 0.3
        evidence.append(StateEvidence(
            metric="front_rh_std_mm",
            value=front_rh_std,
            confidence=0.85,
            note=f"Front RH variance {front_rh_std:.1f}mm (>8.0 threshold)",
        ))

    splitter = measured.splitter_rh_p01_mm
    if 0 < splitter < 3.0:
        severity += 0.2
        evidence.append(StateEvidence(
            metric="splitter_rh_p01_mm",
            value=splitter,
            confidence=0.8,
            note=f"Splitter p01 {splitter:.1f}mm — near ground contact",
        ))

    if not evidence:
        return None

    return CarStateIssue(
        state_id="front_platform_near_limit_high_speed",
        severity=min(1.0, severity),
        confidence=min(1.0, sum(e.confidence for e in evidence) / len(evidence)),
        estimated_loss_ms=severity * 150,
        implicated_steps=[1, 2],
        evidence=evidence,
        likely_causes=["front heave spring too soft", "front ride height too low"],
        recommended_direction="stiffen front heave spring or raise front ride height",
    )


def _check_rear_platform_under_supported(measured: "MeasuredState") -> CarStateIssue | None:
    """Detect rear platform under-supported (too soft / too low)."""
    evidence: list[StateEvidence] = []
    severity = 0.0

    rear_rh_std = measured.rear_rh_std_mm
    if rear_rh_std > 10.0:
        severity += 0.4
        evidence.append(StateEvidence(
            metric="rear_rh_std_mm",
            value=rear_rh_std,
            confidence=0.85,
            note=f"Rear RH variance {rear_rh_std:.1f}mm (>10.0 threshold)",
        ))

    rear_travel = measured.rear_heave_travel_used_pct
    if rear_travel > 75:
        severity += 0.3
        evidence.append(StateEvidence(
            metric="rear_heave_travel_used_pct",
            value=rear_travel,
            confidence=0.9,
            note=f"Rear heave travel {rear_travel:.0f}% used (>75% threshold)",
        ))

    rear_slip = measured.rear_power_slip_ratio_p95
    if rear_slip > 0.06:
        severity += 0.2
        evidence.append(StateEvidence(
            metric="rear_power_slip_ratio_p95",
            value=rear_slip,
            confidence=0.8,
            note=f"Rear power slip {rear_slip:.3f} (>0.06 threshold)",
        ))

    body_slip = measured.body_slip_p95_deg
    if body_slip > 2.5:
        severity += 0.1
        evidence.append(StateEvidence(
            metric="body_slip_p95_deg",
            value=body_slip,
            confidence=0.6,
            note=f"Body slip {body_slip:.1f} deg — rear instability",
        ))

    if not evidence:
        return None

    return CarStateIssue(
        state_id="rear_platform_under_supported",
        severity=min(1.0, severity),
        confidence=min(1.0, sum(e.confidence for e in evidence) / len(evidence)),
        estimated_loss_ms=severity * 180,
        implicated_steps=[1, 2, 3],
        evidence=evidence,
        likely_causes=["rear third spring too soft", "rear ride height too low", "rear corner spring too soft"],
        recommended_direction="stiffen rear third spring or raise rear ride height",
    )


def _check_rear_platform_over_supported(measured: "MeasuredState") -> CarStateIssue | None:
    """Detect rear platform over-supported (too stiff — hurting traction)."""
    evidence: list[StateEvidence] = []
    severity = 0.0

    rear_rh_std = measured.rear_rh_std_mm
    if 0 < rear_rh_std < 3.0:
        severity += 0.3
        evidence.append(StateEvidence(
            metric="rear_rh_std_mm",
            value=rear_rh_std,
            confidence=0.7,
            note=f"Rear RH variance very low ({rear_rh_std:.1f}mm) — may be too stiff",
        ))

    rear_slip = measured.rear_power_slip_ratio_p95
    if rear_slip > 0.08:
        severity += 0.3
        evidence.append(StateEvidence(
            metric="rear_power_slip_ratio_p95",
            value=rear_slip,
            confidence=0.8,
            note=f"High rear power slip {rear_slip:.3f} despite low RH variance — mechanical grip limited",
        ))

    if len(evidence) < 2:
        return None

    return CarStateIssue(
        state_id="rear_platform_over_supported",
        severity=min(1.0, severity),
        confidence=0.6,
        estimated_loss_ms=severity * 100,
        implicated_steps=[2, 3],
        evidence=evidence,
        likely_causes=["rear third spring too stiff", "rear corner spring too stiff"],
        recommended_direction="soften rear springs for better traction compliance",
    )


def _check_entry_front_limited(measured: "MeasuredState") -> CarStateIssue | None:
    """Detect entry understeer (front limited on turn-in)."""
    evidence: list[StateEvidence] = []
    severity = 0.0

    us_low = measured.understeer_low_speed_deg
    if us_low > 1.5:
        severity += 0.4
        evidence.append(StateEvidence(
            metric="understeer_low_speed_deg",
            value=us_low,
            confidence=0.6,
            note=f"Low-speed understeer {us_low:+.1f} deg (>1.5 threshold)",
        ))

    us_mean = measured.understeer_mean_deg
    if us_mean > 1.0:
        severity += 0.3
        evidence.append(StateEvidence(
            metric="understeer_mean_deg",
            value=us_mean,
            confidence=0.6,
            note=f"Mean understeer {us_mean:+.1f} deg",
        ))

    if not evidence:
        return None

    return CarStateIssue(
        state_id="entry_front_limited",
        severity=min(1.0, severity),
        confidence=min(1.0, sum(e.confidence for e in evidence) / len(evidence)),
        estimated_loss_ms=severity * 120,
        implicated_steps=[4, 5],
        evidence=evidence,
        likely_causes=["LLTD too high", "front ARB too stiff", "insufficient front camber"],
        recommended_direction="soften front ARB or increase front camber",
    )


def _check_exit_traction_limited(measured: "MeasuredState") -> CarStateIssue | None:
    """Detect exit traction limitation (rear sliding on power)."""
    evidence: list[StateEvidence] = []
    severity = 0.0

    rear_slip = measured.rear_power_slip_ratio_p95
    if rear_slip > 0.05:
        sev = min(0.5, (rear_slip - 0.05) / 0.10)
        severity += sev
        evidence.append(StateEvidence(
            metric="rear_power_slip_ratio_p95",
            value=rear_slip,
            confidence=0.82,
            note=f"Rear power slip ratio {rear_slip:.3f} (>0.05 threshold)",
        ))

    body_slip = measured.body_slip_p95_deg
    if body_slip > 2.0:
        severity += 0.3
        evidence.append(StateEvidence(
            metric="body_slip_p95_deg",
            value=body_slip,
            confidence=0.6,
            note=f"Body slip {body_slip:.1f} deg — rear limited on exit",
        ))

    if not evidence:
        return None

    return CarStateIssue(
        state_id="exit_traction_limited",
        severity=min(1.0, severity),
        confidence=min(1.0, sum(e.confidence for e in evidence) / len(evidence)),
        estimated_loss_ms=severity * 150,
        implicated_steps=[3, 4],
        evidence=evidence,
        likely_causes=["diff preload too low", "rear ARB too stiff", "rear camber insufficient"],
        recommended_direction="increase diff preload or soften rear ARB",
    )


def _check_thermal_window_invalid(measured: "MeasuredState") -> CarStateIssue | None:
    """Flag when tyre thermal data suggests the window is not valid."""
    evidence: list[StateEvidence] = []

    front_c = measured.front_carcass_mean_c
    rear_c = measured.rear_carcass_mean_c

    if front_c > 0 and front_c < 50:
        evidence.append(StateEvidence(
            metric="front_carcass_mean_c",
            value=front_c,
            confidence=0.85,
            note=f"Front carcass {front_c:.0f} C — too cold for valid data",
        ))

    if rear_c > 0 and rear_c < 50:
        evidence.append(StateEvidence(
            metric="rear_carcass_mean_c",
            value=rear_c,
            confidence=0.85,
            note=f"Rear carcass {rear_c:.0f} C — too cold for valid data",
        ))

    if front_c > 115:
        evidence.append(StateEvidence(
            metric="front_carcass_mean_c",
            value=front_c,
            confidence=0.85,
            note=f"Front carcass {front_c:.0f} C — overheated",
        ))

    if rear_c > 115:
        evidence.append(StateEvidence(
            metric="rear_carcass_mean_c",
            value=rear_c,
            confidence=0.85,
            note=f"Rear carcass {rear_c:.0f} C — overheated",
        ))

    if not evidence:
        return None

    return CarStateIssue(
        state_id="thermal_window_invalid",
        severity=0.3,
        confidence=0.85,
        estimated_loss_ms=0,
        implicated_steps=[],
        evidence=evidence,
        likely_causes=["insufficient warmup", "overdriving", "ambient conditions"],
        recommended_direction="thermal data may not represent normal operating conditions",
    )


def _check_front_contact_patch_undercambered(measured: "MeasuredState") -> CarStateIssue | None:
    """Detect insufficient front camber from temperature spread."""
    evidence: list[StateEvidence] = []
    severity = 0.0

    # Positive spread = inner hotter than outer = potentially undercambered
    lf_spread = measured.front_temp_spread_lf_c
    rf_spread = measured.front_temp_spread_rf_c

    if lf_spread > 8.0:
        severity += 0.3
        evidence.append(StateEvidence(
            metric="front_temp_spread_lf_c",
            value=lf_spread,
            confidence=0.7,
            note=f"LF inner-outer spread {lf_spread:+.1f} C — inner running hot",
        ))

    if rf_spread > 8.0:
        severity += 0.3
        evidence.append(StateEvidence(
            metric="front_temp_spread_rf_c",
            value=rf_spread,
            confidence=0.7,
            note=f"RF inner-outer spread {rf_spread:+.1f} C — inner running hot",
        ))

    if not evidence:
        return None

    return CarStateIssue(
        state_id="front_contact_patch_undercambered",
        severity=min(1.0, severity),
        confidence=0.65,
        estimated_loss_ms=severity * 80,
        implicated_steps=[5],
        evidence=evidence,
        likely_causes=["insufficient front negative camber"],
        recommended_direction="increase front camber magnitude",
    )


def _check_brake_system_front_limited(measured: "MeasuredState") -> CarStateIssue | None:
    """Detect brake system issues (front bias too high, excessive lock)."""
    evidence: list[StateEvidence] = []
    severity = 0.0

    front_lock = measured.front_braking_lock_ratio_p95
    if front_lock > 0.08:
        severity += 0.4
        evidence.append(StateEvidence(
            metric="front_braking_lock_ratio_p95",
            value=front_lock,
            confidence=0.82,
            note=f"Front lock ratio {front_lock:.3f} — excessive front brake engagement",
        ))

    abs_pct = measured.abs_active_pct
    if abs_pct > 30:
        severity += 0.3
        evidence.append(StateEvidence(
            metric="abs_active_pct",
            value=abs_pct,
            confidence=0.75,
            note=f"ABS active {abs_pct:.0f}% of braking — front locking up frequently",
        ))

    if not evidence:
        return None

    return CarStateIssue(
        state_id="brake_system_front_limited",
        severity=min(1.0, severity),
        confidence=min(1.0, sum(e.confidence for e in evidence) / len(evidence)),
        estimated_loss_ms=severity * 100,
        implicated_steps=[],
        evidence=evidence,
        likely_causes=["brake bias too far forward", "front brake pressure too high"],
        recommended_direction="reduce brake bias or adjust brake pressure distribution",
    )


# --- Public API ---

ALL_STATE_CHECKS = [
    _check_front_platform_collapse_braking,
    _check_front_platform_near_limit_high_speed,
    _check_rear_platform_under_supported,
    _check_rear_platform_over_supported,
    _check_entry_front_limited,
    _check_exit_traction_limited,
    _check_thermal_window_invalid,
    _check_front_contact_patch_undercambered,
    _check_brake_system_front_limited,
]


def infer_car_states(
    measured: "MeasuredState",
    setup: "CurrentSetup | None" = None,
) -> list[CarStateIssue]:
    """Run all state inference rules and return detected issues.

    Args:
        measured: Extracted telemetry measurements.
        setup: Optional current setup for context-dependent rules.

    Returns:
        List of CarStateIssue, sorted by severity (highest first).
    """
    issues: list[CarStateIssue] = []
    for check in ALL_STATE_CHECKS:
        result = check(measured)
        if result is not None:
            issues.append(result)

    issues.sort(key=lambda x: x.severity, reverse=True)
    return issues
