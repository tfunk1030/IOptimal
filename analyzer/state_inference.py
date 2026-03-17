from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from analyzer.telemetry_truth import get_signal

if TYPE_CHECKING:
    from analyzer.diagnose import Problem
    from analyzer.driver_style import DriverProfile
    from analyzer.extract import MeasuredState
    from analyzer.segment import CornerAnalysis
    from analyzer.setup_reader import CurrentSetup


@dataclass
class StateEvidence:
    metric: str
    value: float | None
    confidence: float
    note: str


@dataclass
class CarStateIssue:
    state_id: str
    severity: float
    confidence: float
    estimated_loss_ms: float
    implicated_steps: list[int]
    evidence: list[StateEvidence] = field(default_factory=list)
    likely_causes: list[str] = field(default_factory=list)
    recommended_direction: str = ""


def _problem_matches(problems: list["Problem"], *fragments: str) -> bool:
    haystack = " ".join(f"{p.category} {p.symptom} {p.cause}".lower() for p in problems)
    return all(fragment.lower() in haystack for fragment in fragments)


def _signal_confidence(measured: "MeasuredState", name: str, *, default: float = 0.6) -> tuple[float, float | None]:
    signal = get_signal(measured, name)
    value = signal.value
    try:
        float_value = float(value) if value is not None else None
    except (TypeError, ValueError):
        float_value = None
    confidence = signal.confidence if signal.value is not None else default
    return confidence, float_value


def _append_issue(
    issues: list[CarStateIssue],
    *,
    state_id: str,
    severity: float,
    confidence: float,
    estimated_loss_ms: float,
    implicated_steps: list[int],
    evidence: list[StateEvidence],
    likely_causes: list[str],
    recommended_direction: str,
) -> None:
    if severity <= 0.0:
        return
    issues.append(
        CarStateIssue(
            state_id=state_id,
            severity=round(min(1.0, severity), 3),
            confidence=round(min(1.0, max(0.0, confidence)), 3),
            estimated_loss_ms=round(max(0.0, estimated_loss_ms), 1),
            implicated_steps=implicated_steps,
            evidence=evidence,
            likely_causes=likely_causes,
            recommended_direction=recommended_direction,
        )
    )


def infer_car_states(
    *,
    measured: "MeasuredState",
    setup: "CurrentSetup",
    problems: list["Problem"],
    driver: "DriverProfile | None" = None,
    corners: list["CornerAnalysis"] | None = None,
) -> list[CarStateIssue]:
    issues: list[CarStateIssue] = []

    front_travel_conf, front_travel = _signal_confidence(measured, "front_heave_travel_used_pct")
    braking_travel_conf, braking_travel = _signal_confidence(measured, "front_heave_travel_used_braking_pct")
    front_var_conf, front_var = _signal_confidence(measured, "front_rh_std_mm")
    rear_travel_conf, rear_travel = _signal_confidence(measured, "rear_heave_travel_used_pct")
    rear_var_conf, rear_var = _signal_confidence(measured, "rear_rh_std_mm")
    us_low_conf, us_low = _signal_confidence(measured, "understeer_low_speed_deg")
    us_high_conf, us_high = _signal_confidence(measured, "understeer_high_speed_deg")
    body_slip_conf, body_slip = _signal_confidence(measured, "body_slip_p95_deg")
    front_lock_conf, front_lock = _signal_confidence(measured, "front_braking_lock_ratio_p95")
    rear_slip_conf, rear_slip = _signal_confidence(measured, "rear_power_slip_ratio_p95")
    front_temp_conf, front_temp = _signal_confidence(measured, "front_carcass_mean_c")
    rear_temp_conf, rear_temp = _signal_confidence(measured, "rear_carcass_mean_c")
    front_pressure_conf, front_pressure = _signal_confidence(measured, "front_pressure_mean_kpa")
    rear_pressure_conf, rear_pressure = _signal_confidence(measured, "rear_pressure_mean_kpa")

    # Front platform collapse under braking
    if (braking_travel or 0.0) > 85.0 or _problem_matches(problems, "braking pitch range"):
        severity = max(
            ((braking_travel or 0.0) - 85.0) / 15.0,
            max(0.0, getattr(measured, "pitch_range_braking_deg", 0.0) - 0.9) / 0.8,
        )
        evidence = [
            StateEvidence(
                metric="front_heave_travel_used_braking_pct",
                value=braking_travel,
                confidence=braking_travel_conf,
                note="Front braking travel budget is being consumed under entry load.",
            ),
            StateEvidence(
                metric="pitch_range_braking_deg",
                value=getattr(measured, "pitch_range_braking_deg", 0.0),
                confidence=0.75 if getattr(measured, "pitch_range_braking_deg", 0.0) > 0 else 0.0,
                note="Large braking pitch range indicates entry platform migration.",
            ),
        ]
        _append_issue(
            issues,
            state_id="front_platform_collapse_braking",
            severity=severity,
            confidence=max(braking_travel_conf, 0.7),
            estimated_loss_ms=90.0 + severity * 140.0,
            implicated_steps=[2, 6],
            evidence=evidence,
            likely_causes=["front heave spring too soft", "front heave perch consuming travel", "front LS damping mismatch"],
            recommended_direction="increase front heave support and preserve braking travel margin",
        )

    # Front high-speed platform near limit
    if (front_travel or 0.0) > 80.0 or (front_var or 0.0) > 5.5 or _problem_matches(problems, "front bottoming"):
        severity = max(
            ((front_travel or 0.0) - 80.0) / 20.0,
            max(0.0, (front_var or 0.0) - 5.5) / 4.0,
            max(0.0, getattr(measured, "bottoming_event_count_front_clean", 0)) / 8.0,
        )
        evidence = [
            StateEvidence(
                metric="front_heave_travel_used_pct",
                value=front_travel,
                confidence=front_travel_conf,
                note="Front heave travel use at speed is near the limit.",
            ),
            StateEvidence(
                metric="front_rh_std_mm",
                value=front_var,
                confidence=front_var_conf,
                note="Front ride-height variance indicates aero-platform instability.",
            ),
        ]
        _append_issue(
            issues,
            state_id="front_platform_near_limit_high_speed",
            severity=severity,
            confidence=max(front_travel_conf, front_var_conf),
            estimated_loss_ms=80.0 + severity * 160.0,
            implicated_steps=[1, 2, 6],
            evidence=evidence,
            likely_causes=["front heave support too soft", "insufficient front ride-height margin", "HS damping not controlling aero events"],
            recommended_direction="increase high-speed front platform support",
        )

    # Rear platform under-supported
    if (rear_travel or 0.0) > 80.0 or (rear_var or 0.0) > 7.0 or _problem_matches(problems, "rear bottoming"):
        severity = max(
            ((rear_travel or 0.0) - 80.0) / 20.0,
            max(0.0, (rear_var or 0.0) - 7.0) / 5.0,
            max(0.0, getattr(measured, "bottoming_event_count_rear_clean", 0)) / 8.0,
        )
        evidence = [
            StateEvidence(
                metric="rear_heave_travel_used_pct",
                value=rear_travel,
                confidence=rear_travel_conf,
                note="Rear third-element travel use shows the rear platform is near its limit.",
            ),
            StateEvidence(
                metric="rear_rh_std_mm",
                value=rear_var,
                confidence=rear_var_conf,
                note="Rear ride-height variance indicates diffuser/platform instability.",
            ),
        ]
        _append_issue(
            issues,
            state_id="rear_platform_under_supported",
            severity=severity,
            confidence=max(rear_travel_conf, rear_var_conf),
            estimated_loss_ms=70.0 + severity * 150.0,
            implicated_steps=[2, 3, 6],
            evidence=evidence,
            likely_causes=["rear third spring too soft", "rear spring support too low", "rear HS damping too soft"],
            recommended_direction="increase rear platform support",
        )

    # Rear platform over-supported
    if (rear_var or 0.0) < 2.0 and (rear_slip or 0.0) > 0.08 and (body_slip or 0.0) < 3.5:
        severity = max(0.0, ((rear_slip or 0.0) - 0.08) / 0.05)
        evidence = [
            StateEvidence(
                metric="rear_rh_std_mm",
                value=rear_var,
                confidence=rear_var_conf,
                note="Very low rear ride-height variance suggests an overly rigid rear platform.",
            ),
            StateEvidence(
                metric="rear_power_slip_ratio_p95",
                value=rear_slip,
                confidence=rear_slip_conf,
                note="High exit slip despite rigid platform implies the rear is over-supported mechanically.",
            ),
        ]
        _append_issue(
            issues,
            state_id="rear_platform_over_supported",
            severity=severity,
            confidence=min(1.0, (rear_var_conf + rear_slip_conf) / 2.0),
            estimated_loss_ms=50.0 + severity * 90.0,
            implicated_steps=[2, 3],
            evidence=evidence,
            likely_causes=["rear third spring too stiff", "rear spring too stiff", "rear platform overly rigid for traction phase"],
            recommended_direction="reduce rear platform stiffness slightly",
        )

    # Entry front limited
    if (us_low or 0.0) > 1.3 or _problem_matches(problems, "understeer", "low"):
        severity = max(0.0, ((us_low or 0.0) - 1.3) / 1.5)
        corner_support = 0.0
        if corners:
            trail_corners = [c for c in corners if c.trail_brake_pct > 0.2 and c.understeer_mean_deg > 1.0]
            corner_support = min(1.0, len(trail_corners) / max(len(corners), 1))
            severity = max(severity, corner_support)
        evidence = [
            StateEvidence(
                metric="understeer_low_speed_deg",
                value=us_low,
                confidence=us_low_conf,
                note="Low-speed understeer indicates the front axle is entry-limited.",
            ),
        ]
        _append_issue(
            issues,
            state_id="entry_front_limited",
            severity=severity,
            confidence=max(us_low_conf, 0.6 + corner_support * 0.2),
            estimated_loss_ms=60.0 + severity * 120.0,
            implicated_steps=[4, 5, 6],
            evidence=evidence,
            likely_causes=["front contact patch not supporting entry", "front mechanical balance too lazy", "brake phase overloading front axle"],
            recommended_direction="increase front entry grip and rotation support",
        )

    # Exit traction limited
    if (rear_slip or 0.0) > 0.08 or (body_slip or 0.0) > 4.0:
        severity = max(
            max(0.0, ((rear_slip or 0.0) - 0.08) / 0.06),
            max(0.0, ((body_slip or 0.0) - 4.0) / 2.0),
        )
        corner_support = 0.0
        if corners:
            traction_corners = [c for c in corners if c.throttle_delay_s > 0.25 or "late_throttle" in c.traction_risk_flags]
            corner_support = min(1.0, len(traction_corners) / max(len(corners), 1))
            severity = max(severity, corner_support)
        evidence = [
            StateEvidence(
                metric="rear_power_slip_ratio_p95",
                value=rear_slip,
                confidence=rear_slip_conf,
                note="Rear axle slip under power shows exit traction is the limiting state.",
            ),
            StateEvidence(
                metric="body_slip_p95_deg",
                value=body_slip,
                confidence=body_slip_conf,
                note="High body slip confirms rear-end instability in the traction phase.",
            ),
        ]
        _append_issue(
            issues,
            state_id="exit_traction_limited",
            severity=severity,
            confidence=max(rear_slip_conf, body_slip_conf, 0.55 + corner_support * 0.25),
            estimated_loss_ms=80.0 + severity * 130.0,
            implicated_steps=[3, 4, 6],
            evidence=evidence,
            likely_causes=["rear support not matching traction demand", "diff/TC too aggressive or too open", "rear contact patch overloaded on exit"],
            recommended_direction="increase exit traction support and rear stability",
        )

    # High-speed aerodynamic balance issue
    if (us_high or 0.0) > 1.0 and ((us_high or 0.0) - (us_low or 0.0)) > 0.4:
        severity = max(0.0, ((us_high or 0.0) - (us_low or 0.0) - 0.4) / 1.2)
        evidence = [
            StateEvidence(
                metric="understeer_high_speed_deg",
                value=us_high,
                confidence=us_high_conf,
                note="High-speed understeer exceeds the low-speed baseline, pointing to aero/platform balance loss.",
            ),
        ]
        _append_issue(
            issues,
            state_id="front_platform_near_limit_high_speed",
            severity=severity,
            confidence=us_high_conf,
            estimated_loss_ms=50.0 + severity * 100.0,
            implicated_steps=[1, 2, 4],
            evidence=evidence,
            likely_causes=["rear aero balance dominance at speed", "front platform not holding aero window"],
            recommended_direction="shift high-speed balance toward front support or front aero confidence",
        )

    # Balance asymmetry
    left = getattr(measured, "understeer_left_turn_deg", 0.0)
    right = getattr(measured, "understeer_right_turn_deg", 0.0)
    if abs(left) > 0.05 and abs(right) > 0.05 and abs(left - right) > 0.35:
        severity = min(1.0, abs(left - right) / 1.5)
        evidence = [
            StateEvidence(
                metric="understeer_left_turn_deg",
                value=left,
                confidence=0.7,
                note="Left-turn balance measurement.",
            ),
            StateEvidence(
                metric="understeer_right_turn_deg",
                value=right,
                confidence=0.7,
                note="Right-turn balance measurement.",
            ),
        ]
        _append_issue(
            issues,
            state_id="balance_asymmetric",
            severity=severity,
            confidence=0.7,
            estimated_loss_ms=35.0 + severity * 60.0,
            implicated_steps=[5],
            evidence=evidence,
            likely_causes=["left/right tyre support imbalance", "camber or toe asymmetry interacting with track direction mix"],
            recommended_direction="check directional balance and geometry asymmetry before global balance changes",
        )

    # Front contact patch undercambered
    front_spreads = [
        getattr(measured, "front_temp_spread_lf_c", 0.0),
        getattr(measured, "front_temp_spread_rf_c", 0.0),
    ]
    spread_support = [v for v in front_spreads if v != 0.0]
    if spread_support and min(spread_support) < 6.0:
        severity = min(1.0, (6.0 - min(spread_support)) / 4.0)
        evidence = [
            StateEvidence(
                metric="front_temp_spread_lf_c",
                value=getattr(measured, "front_temp_spread_lf_c", 0.0),
                confidence=front_temp_conf,
                note="Flat or outer-loaded front temperature spread suggests insufficient negative camber.",
            ),
            StateEvidence(
                metric="front_temp_spread_rf_c",
                value=getattr(measured, "front_temp_spread_rf_c", 0.0),
                confidence=front_temp_conf,
                note="Flat or outer-loaded front temperature spread suggests insufficient negative camber.",
            ),
        ]
        _append_issue(
            issues,
            state_id="front_contact_patch_undercambered",
            severity=severity,
            confidence=max(0.55, front_temp_conf),
            estimated_loss_ms=40.0 + severity * 70.0,
            implicated_steps=[5],
            evidence=evidence,
            likely_causes=["insufficient front negative camber", "front tyre contact patch too flat under load"],
            recommended_direction="increase front negative camber or reduce front outer-shoulder overload",
        )

    # Thermal window invalid
    temp_score = 0.0
    pressure_score = 0.0
    for temp in (front_temp, rear_temp):
        if temp is not None and (temp < 80.0 or temp > 105.0):
            temp_score = max(temp_score, abs(temp - 92.5) / 20.0)
    for pressure in (front_pressure, rear_pressure):
        if pressure is not None and (pressure < 155.0 or pressure > 175.0):
            pressure_score = max(pressure_score, abs(pressure - 165.0) / 15.0)

    thermal_score = 0.0
    thermal_confidence = max(front_temp_conf, rear_temp_conf, front_pressure_conf, rear_pressure_conf)
    # Pressure-only deviation is advisory in GTP because the sim minimum cold
    # pressure often forces high hot pressures. Do not let it dominate reset logic.
    if temp_score > 0.0:
        thermal_score = max(temp_score, pressure_score * 0.2)
        if temp_score < 0.35:
            thermal_score *= 0.7
    elif pressure_score > 0.0:
        thermal_score = min(0.25, pressure_score * 0.2)
        thermal_confidence = min(thermal_confidence, 0.45)

    if thermal_score > 0.05:
        evidence = [
            StateEvidence("front_carcass_mean_c", front_temp, front_temp_conf, "Front tyre thermal window evidence."),
            StateEvidence("rear_carcass_mean_c", rear_temp, rear_temp_conf, "Rear tyre thermal window evidence."),
            StateEvidence("front_pressure_mean_kpa", front_pressure, front_pressure_conf, "Front hot-pressure window evidence."),
            StateEvidence("rear_pressure_mean_kpa", rear_pressure, rear_pressure_conf, "Rear hot-pressure window evidence."),
        ]
        _append_issue(
            issues,
            state_id="thermal_window_invalid",
            severity=thermal_score,
            confidence=thermal_confidence,
            estimated_loss_ms=45.0 + thermal_score * 85.0,
            implicated_steps=[5],
            evidence=evidence,
            likely_causes=["tyres operating outside thermal/pressure target window", "contact patch efficiency reduced"],
            recommended_direction=(
                "move thermal state back toward target window before fine-tuning balance"
                if temp_score > 0.0
                else "pressure-only deviation is advisory; tune around the sim pressure floor before declaring a reset"
            ),
        )

    # Brake system front limited
    if (front_lock or 0.0) > 0.06 or getattr(measured, "abs_active_pct", 0.0) > 20.0:
        severity = max(
            max(0.0, ((front_lock or 0.0) - 0.06) / 0.05),
            max(0.0, getattr(measured, "abs_active_pct", 0.0) - 20.0) / 35.0,
        )
        evidence = [
            StateEvidence(
                metric="front_braking_lock_ratio_p95",
                value=front_lock,
                confidence=front_lock_conf,
                note="Front braking lock proxy shows the front axle saturating under braking.",
            ),
            StateEvidence(
                metric="abs_active_pct",
                value=getattr(measured, "abs_active_pct", 0.0),
                confidence=0.75 if getattr(measured, "abs_active_pct", 0.0) > 0 else 0.0,
                note="Frequent ABS engagement indicates excessive front brake demand.",
            ),
        ]
        _append_issue(
            issues,
            state_id="brake_system_front_limited",
            severity=severity,
            confidence=max(front_lock_conf, 0.7),
            estimated_loss_ms=55.0 + severity * 95.0,
            implicated_steps=[6],
            evidence=evidence,
            likely_causes=["front brake demand exceeds tyre support", "bias too far forward", "entry platform collapsing into front lock"],
            recommended_direction="reduce front brake limitation before chasing corner-entry rotation",
        )

    issues.sort(key=lambda issue: (issue.severity * issue.confidence, issue.estimated_loss_ms), reverse=True)
    return issues
