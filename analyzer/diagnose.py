"""Handling diagnosis engine -- identify problems from physics thresholds.

Evaluates telemetry (MeasuredState) against physics-derived thresholds.
No solver predictions needed. No baselines. Every problem identified from
what the data shows and what physics says should be different.

Priority order follows the 6-step workflow:
  0 = safety (bottoming, vortex burst)
  1 = platform (ride height variance, excursion)
  2 = balance (understeer, LLTD, body slip)
  3 = damper (settle time, yaw correlation, roll rate)
  4 = thermal (camber, pressure, carcass temp)
  5 = grip (traction slip, braking slip)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from analyzer.adaptive_thresholds import AdaptiveThresholds
from analyzer.extract import MeasuredState
from analyzer.setup_reader import CurrentSetup
from car_model.cars import CarModel


@dataclass
class Problem:
    """A single diagnosed handling problem."""
    category: str       # "safety" | "platform" | "balance" | "damper" | "thermal" | "grip"
    severity: str       # "critical" | "significant" | "minor"
    symptom: str        # what the data shows
    cause: str          # physics explanation
    speed_context: str  # "all" | "low" | "high" | "braking" | "traction"
    measured: float     # measured value
    threshold: float    # threshold that was exceeded
    units: str
    priority: int       # 0=safety ... 5=grip


@dataclass
class Diagnosis:
    """Complete handling diagnosis from telemetry."""
    problems: list[Problem] = field(default_factory=list)    # sorted by priority
    assessment: str = "competitive"    # "fast" | "competitive" | "compromised" | "dangerous"
    lap_time_s: float = 0.0
    lap_number: int = 0
    # Summary metrics for the report
    lltd_pct: float = 0.0
    weight_dist_front_pct: float = 0.0
    # Causal analysis (populated after problems are identified)
    causal_diagnosis: object = field(default=None, repr=False)  # CausalDiagnosis | None


def diagnose(
    measured: MeasuredState,
    setup: CurrentSetup,
    car: CarModel,
    thresholds: AdaptiveThresholds | None = None,
) -> Diagnosis:
    """Analyze telemetry and identify handling problems from physics.

    Args:
        measured: Telemetry-derived measurements from extract.py
        setup: Current garage setup from setup_reader.py
        car: Car physical model
        thresholds: Adaptive thresholds scaled for track/car/driver.
            If None, uses baseline defaults.

    Returns:
        Diagnosis with prioritized problems list and overall assessment
    """
    if thresholds is None:
        thresholds = AdaptiveThresholds()

    diag = Diagnosis(
        lap_time_s=measured.lap_time_s,
        lap_number=measured.lap_number,
        lltd_pct=measured.lltd_measured * 100 if measured.lltd_measured > 0 else 0.0,
        weight_dist_front_pct=car.weight_dist_front * 100,
    )

    problems = []

    # ── Priority 0: Safety ──────────────────────────────────────────────
    _check_safety(measured, setup, car, problems, thresholds)

    # ── Priority 1: Platform ────────────────────────────────────────────
    _check_platform(measured, setup, car, problems, thresholds)

    # ── Priority 2: Balance ─────────────────────────────────────────────
    _check_balance(measured, setup, car, problems, thresholds)

    # ── Priority 3: Dampers ─────────────────────────────────────────────
    _check_dampers(measured, setup, car, problems, thresholds)

    # ── Priority 4: Thermal ─────────────────────────────────────────────
    _check_thermal(measured, setup, car, problems, thresholds)

    # ── Priority 5: Grip ────────────────────────────────────────────────
    _check_grip(measured, setup, car, problems, thresholds)

    # Sort by priority then severity
    severity_order = {"critical": 0, "significant": 1, "minor": 2}
    problems.sort(key=lambda p: (p.priority, severity_order.get(p.severity, 3)))
    diag.problems = problems

    # Causal analysis: trace problems back to root causes
    if problems:
        try:
            from analyzer.causal_graph import analyze_causes
            diag.causal_diagnosis = analyze_causes(problems)
        except Exception:
            diag.causal_diagnosis = None  # causal analysis is advisory — never block
    else:
        diag.causal_diagnosis = None

    # Overall assessment
    diag.assessment = _compute_assessment(problems)

    return diag


def _check_safety(
    m: MeasuredState, s: CurrentSetup, car: CarModel, problems: list[Problem],
    t: AdaptiveThresholds = AdaptiveThresholds(),
) -> None:
    """Check safety-critical items: vortex burst and bottoming."""

    # Vortex burst events at speed
    if m.vortex_burst_event_count > 0:
        problems.append(Problem(
            category="safety",
            severity="critical",
            symptom=f"{m.vortex_burst_event_count} vortex burst events detected at speed",
            cause=(
                "Front ride height dropped below critical aero threshold. "
                "Sudden downforce loss causes snap oversteer. "
                "Front heave spring too soft or static RH too low for this track."
            ),
            speed_context="high",
            measured=float(m.vortex_burst_event_count),
            threshold=0.0,
            units="events",
            priority=0,
        ))

    # Front bottoming
    front_bottom_thresh = t.bottoming_events_front
    if m.bottoming_event_count_front > front_bottom_thresh:
        sev = "critical" if m.bottoming_event_count_front > front_bottom_thresh * 4 else "significant"
        problems.append(Problem(
            category="safety",
            severity=sev,
            symptom=f"{m.bottoming_event_count_front} front bottoming events",
            cause=(
                "Front suspension hitting bump stops. Causes spike loads through "
                "chassis, unpredictable handling, and potential aero stall. "
                "Front heave spring too soft or ride height too low."
            ),
            speed_context="all",
            measured=float(m.bottoming_event_count_front),
            threshold=float(front_bottom_thresh),
            units="events",
            priority=0,
        ))

    # Rear bottoming
    rear_bottom_thresh = t.bottoming_events_rear
    if m.bottoming_event_count_rear > rear_bottom_thresh:
        sev = "critical" if m.bottoming_event_count_rear > rear_bottom_thresh * 4 else "significant"
        problems.append(Problem(
            category="safety",
            severity=sev,
            symptom=f"{m.bottoming_event_count_rear} rear bottoming events",
            cause=(
                "Rear suspension hitting bump stops. Causes rear instability "
                "and diffuser stall risk. Stiffen rear third spring or raise "
                "rear ride height."
            ),
            speed_context="all",
            measured=float(m.bottoming_event_count_rear),
            threshold=float(rear_bottom_thresh),
            units="events",
            priority=0,
        ))

    # Front heave spring travel exhaustion (direct deflection measurement)
    # When deflection approaches DeflMax, the spring bottoms out.
    # Under braking this causes: entry rotation (spring compressing) → mid-corner push (bottomed).
    if m.front_heave_travel_used_braking_pct > 85.0:
        sev = "critical" if m.front_heave_travel_used_braking_pct > 95.0 else "significant"
        problems.append(Problem(
            category="safety",
            severity=sev,
            symptom=(
                f"Front heave spring travel {m.front_heave_travel_used_braking_pct:.0f}% "
                f"exhausted under braking (p99 defl {m.front_heave_defl_braking_p99_mm:.1f}mm)"
            ),
            cause=(
                "Front heave spring travel nearly exhausted under braking. "
                "High deflection (preload) consumes static travel, leaving "
                "insufficient dynamic range for weight transfer compression. "
                "Symptom: entry rotation followed by mid-corner push as spring "
                "bottoms out and car becomes rigid. "
                "Fix: reduce heave perch offset (lower deflection) or stiffen heave spring."
            ),
            speed_context="braking",
            measured=m.front_heave_travel_used_braking_pct,
            threshold=85.0,
            units="%",
            priority=0,
        ))

    # Front heave spring travel exhaustion at speed (non-braking)
    if m.front_heave_travel_used_pct > 85.0:
        sev = "critical" if m.front_heave_travel_used_pct > 95.0 else "significant"
        problems.append(Problem(
            category="safety",
            severity=sev,
            symptom=(
                f"Front heave spring travel {m.front_heave_travel_used_pct:.0f}% "
                f"used at speed (p99 defl {m.front_heave_defl_p99_mm:.1f}mm)"
            ),
            cause=(
                "Front heave spring using most of its available travel at speed. "
                "Bump events or kerbs will exhaust remaining travel, causing "
                "spike loads through chassis. "
                "Fix: reduce heave perch offset or stiffen heave spring."
            ),
            speed_context="high",
            measured=m.front_heave_travel_used_pct,
            threshold=85.0,
            units="%",
            priority=0,
        ))

    # Direct heave bottoming events (from deflection channel, not ride height proxy)
    if m.heave_bottoming_events_front > 0:
        sev = "critical" if m.heave_bottoming_events_front > 10 else "significant"
        problems.append(Problem(
            category="safety",
            severity=sev,
            symptom=(
                f"{m.heave_bottoming_events_front} front heave spring bottoming events "
                f"(deflection within 2mm of DeflMax)"
            ),
            cause=(
                "Front heave spring physically bottoming out — deflection at mechanical "
                "limit. Car becomes a rigid board with no vertical compliance. "
                "Fix: reduce heave perch offset (preload) or stiffen heave spring."
            ),
            speed_context="all",
            measured=float(m.heave_bottoming_events_front),
            threshold=0.0,
            units="events",
            priority=0,
        ))


def _check_platform(
    m: MeasuredState, s: CurrentSetup, car: CarModel, problems: list[Problem],
    t: AdaptiveThresholds = AdaptiveThresholds(),
) -> None:
    """Check platform stability: ride height variance and excursion."""

    # Front variance (adaptive: relaxed on bumpy tracks, tightened on smooth)
    front_var_thresh = t.front_rh_variance_mm
    if m.front_rh_std_mm > front_var_thresh:
        sev = "significant" if m.front_rh_std_mm > front_var_thresh * 1.5 else "minor"
        problems.append(Problem(
            category="platform",
            severity=sev,
            symptom=f"Front RH variance {m.front_rh_std_mm:.1f}mm (threshold {front_var_thresh:.1f}mm)",
            cause=(
                "Front platform oscillating too much at speed. "
                "Aero balance shifts with each oscillation cycle. "
                "Front heave spring too soft for this track surface."
            ),
            speed_context="high",
            measured=m.front_rh_std_mm,
            threshold=front_var_thresh,
            units="mm",
            priority=1,
        ))

    # Rear variance
    rear_var_thresh = t.rear_rh_variance_mm
    if m.rear_rh_std_mm > rear_var_thresh:
        sev = "significant" if m.rear_rh_std_mm > rear_var_thresh * 1.5 else "minor"
        problems.append(Problem(
            category="platform",
            severity=sev,
            symptom=f"Rear RH variance {m.rear_rh_std_mm:.1f}mm (threshold {rear_var_thresh:.1f}mm)",
            cause=(
                "Rear platform oscillating too much at speed. "
                "Diffuser efficiency degrades with large RH variation. "
                "Rear third spring too soft for this track surface."
            ),
            speed_context="high",
            measured=m.rear_rh_std_mm,
            threshold=rear_var_thresh,
            units="mm",
            priority=1,
        ))

    # Front excursion near bottoming
    excursion_thresh = t.excursion_pct / 100.0
    if s.front_rh_at_speed_mm > 0 and m.front_rh_excursion_measured_mm > 0:
        margin = m.front_rh_excursion_measured_mm / s.front_rh_at_speed_mm
        if margin > excursion_thresh:
            problems.append(Problem(
                category="platform",
                severity="significant",
                symptom=(
                    f"Front excursion p99 {m.front_rh_excursion_measured_mm:.1f}mm "
                    f"= {margin*100:.0f}% of dynamic RH ({s.front_rh_at_speed_mm:.0f}mm)"
                ),
                cause=(
                    "Front suspension is using >80% of available travel. "
                    "Any additional bump loads will cause bottoming. "
                    "Insufficient margin for kerbs or dirty air."
                ),
                speed_context="high",
                measured=margin * 100,
                threshold=80.0,
                units="%",
                priority=1,
            ))


def _check_balance(
    m: MeasuredState, s: CurrentSetup, car: CarModel, problems: list[Problem],
    t: AdaptiveThresholds = AdaptiveThresholds(),
) -> None:
    """Check balance: understeer, LLTD, body slip, speed gradient."""

    # Excessive understeer (adaptive: car/driver-specific threshold)
    us_thresh = t.understeer_all_deg
    if m.understeer_mean_deg > us_thresh:
        problems.append(Problem(
            category="balance",
            severity="significant",
            symptom=f"Mean understeer {m.understeer_mean_deg:+.1f} deg (car pushing)",
            cause=(
                "Car understeering through corners. Front tyres saturated before "
                "rears. Check LLTD (too high pushes front), front aero balance, "
                "and front mechanical grip."
            ),
            speed_context="all",
            measured=m.understeer_mean_deg,
            threshold=us_thresh,
            units="deg",
            priority=2,
        ))

    # Net oversteer (loose)
    os_thresh = t.oversteer_deg
    if m.understeer_mean_deg < os_thresh:
        sev = "significant" if m.understeer_mean_deg < os_thresh - 1.0 else "minor"
        problems.append(Problem(
            category="balance",
            severity=sev,
            symptom=f"Mean understeer {m.understeer_mean_deg:+.1f} deg (car loose)",
            cause=(
                "Car oversteering on average. Rear tyres saturating before "
                "fronts. LLTD may be too low, or rear mechanical grip is "
                "insufficient. Check rear spring rates and ARB."
            ),
            speed_context="all",
            measured=m.understeer_mean_deg,
            threshold=os_thresh,
            units="deg",
            priority=2,
        ))

    # Speed gradient: aero/mechanical mismatch
    grad_thresh = t.speed_gradient_deg
    if m.understeer_low_speed_deg != 0 and m.understeer_high_speed_deg != 0:
        gradient = m.understeer_high_speed_deg - m.understeer_low_speed_deg
        if abs(gradient) > grad_thresh:
            if gradient > 0:
                problems.append(Problem(
                    category="balance",
                    severity="significant",
                    symptom=(
                        f"Speed gradient {gradient:+.1f} deg "
                        f"(low {m.understeer_low_speed_deg:+.1f}, "
                        f"high {m.understeer_high_speed_deg:+.1f})"
                    ),
                    cause=(
                        "More understeer at high speed than low speed. "
                        "Aero balance is pushing the front: too much rear downforce "
                        "relative to front at speed. Increase front DF balance "
                        "(lower front RH or raise rear RH)."
                    ),
                    speed_context="high",
                    measured=gradient,
                    threshold=grad_thresh,
                    units="deg",
                    priority=2,
                ))
            else:
                problems.append(Problem(
                    category="balance",
                    severity="significant",
                    symptom=(
                        f"Speed gradient {gradient:+.1f} deg "
                        f"(low {m.understeer_low_speed_deg:+.1f}, "
                        f"high {m.understeer_high_speed_deg:+.1f})"
                    ),
                    cause=(
                        "More oversteer at high speed than low speed. "
                        "Insufficient front downforce at speed, or rear "
                        "downforce dropping. Decrease front DF balance "
                        "(raise front RH or lower rear RH), or add wing."
                    ),
                    speed_context="high",
                    measured=gradient,
                    threshold=-grad_thresh,
                    units="deg",
                    priority=2,
                ))

    # LLTD check
    if m.lltd_measured > 0:
        target_lltd = car.weight_dist_front + 0.05  # 5% above front weight dist (OptimumG baseline)
        lltd_delta = m.lltd_measured - target_lltd

        if lltd_delta > t.lltd_high_delta:
            problems.append(Problem(
                category="balance",
                severity="significant" if lltd_delta > t.lltd_high_delta * 1.5 else "minor",
                symptom=(
                    f"LLTD {m.lltd_measured*100:.1f}% vs target "
                    f"{target_lltd*100:.0f}% (delta +{lltd_delta*100:.1f}%)"
                ),
                cause=(
                    "LLTD too high: front axle carries too much lateral load "
                    "transfer. Causes mechanical understeer. "
                    "Soften front ARB or stiffen rear ARB."
                ),
                speed_context="all",
                measured=m.lltd_measured * 100,
                threshold=(target_lltd + t.lltd_high_delta) * 100,
                units="%",
                priority=2,
            ))
        elif lltd_delta < t.lltd_low_delta:
            problems.append(Problem(
                category="balance",
                severity="significant" if lltd_delta < t.lltd_low_delta * 3 else "minor",
                symptom=(
                    f"LLTD {m.lltd_measured*100:.1f}% vs target "
                    f"{target_lltd*100:.0f}% (delta {lltd_delta*100:+.1f}%)"
                ),
                cause=(
                    "LLTD too low: rear axle carries too much lateral load "
                    "transfer. Risk of snap oversteer at the limit. "
                    "Stiffen front ARB or soften rear ARB."
                ),
                speed_context="all",
                measured=m.lltd_measured * 100,
                threshold=(target_lltd + t.lltd_low_delta) * 100,
                units="%",
                priority=2,
            ))

    # Body slip angle (rear instability)
    bs_thresh = t.body_slip_p95_deg
    if m.body_slip_p95_deg > bs_thresh:
        problems.append(Problem(
            category="balance",
            severity="significant" if m.body_slip_p95_deg > bs_thresh * 1.5 else "minor",
            symptom=f"Body slip angle p95 = {m.body_slip_p95_deg:.1f} deg (threshold {bs_thresh:.1f})",
            cause=(
                "Rear of car sliding excessively. High body slip angle "
                "increases drag and risks snap oversteer. "
                "Rear grip insufficient: check diff preload, rear ARB, "
                "and rear tyre condition."
            ),
            speed_context="all",
            measured=m.body_slip_p95_deg,
            threshold=bs_thresh,
            units="deg",
            priority=2,
        ))


def _check_dampers(
    m: MeasuredState, s: CurrentSetup, car: CarModel, problems: list[Problem],
    t: AdaptiveThresholds = AdaptiveThresholds(),
) -> None:
    """Check damper response: settle time, yaw correlation, roll rate."""

    # Settle time too long (underdamped)
    settle_upper = t.settle_time_upper_ms
    if m.front_rh_settle_time_ms > settle_upper:
        problems.append(Problem(
            category="damper",
            severity="significant" if m.front_rh_settle_time_ms > settle_upper * 1.75 else "minor",
            symptom=f"Front settle time {m.front_rh_settle_time_ms:.0f}ms (target <{settle_upper:.0f}ms)",
            cause=(
                "Front suspension takes too long to recover from bumps. "
                "Underdamped: platform oscillates instead of settling. "
                "Increase front LS rebound damping."
            ),
            speed_context="all",
            measured=m.front_rh_settle_time_ms,
            threshold=settle_upper,
            units="ms",
            priority=3,
        ))

    if m.rear_rh_settle_time_ms > settle_upper:
        problems.append(Problem(
            category="damper",
            severity="significant" if m.rear_rh_settle_time_ms > settle_upper * 1.75 else "minor",
            symptom=f"Rear settle time {m.rear_rh_settle_time_ms:.0f}ms (target <{settle_upper:.0f}ms)",
            cause=(
                "Rear suspension takes too long to recover from bumps. "
                "Underdamped: platform oscillates instead of settling. "
                "Increase rear LS rebound damping."
            ),
            speed_context="all",
            measured=m.rear_rh_settle_time_ms,
            threshold=settle_upper,
            units="ms",
            priority=3,
        ))

    # Settle time too short (overdamped, losing compliance)
    settle_lower = t.settle_time_lower_ms
    if 0 < m.front_rh_settle_time_ms < settle_lower:
        problems.append(Problem(
            category="damper",
            severity="minor",
            symptom=f"Front settle time {m.front_rh_settle_time_ms:.0f}ms (too fast, <{settle_lower:.0f}ms)",
            cause=(
                "Front suspension overdamped. Fast settle but loses "
                "compliance over bumps. Tyre load variation increases, "
                "reducing average grip. Reduce front LS rebound."
            ),
            speed_context="all",
            measured=m.front_rh_settle_time_ms,
            threshold=settle_lower,
            units="ms",
            priority=3,
        ))

    if 0 < m.rear_rh_settle_time_ms < settle_lower:
        problems.append(Problem(
            category="damper",
            severity="minor",
            symptom=f"Rear settle time {m.rear_rh_settle_time_ms:.0f}ms (too fast, <{settle_lower:.0f}ms)",
            cause=(
                "Rear suspension overdamped. Fast settle but loses "
                "compliance over bumps. Reduce rear LS rebound."
            ),
            speed_context="all",
            measured=m.rear_rh_settle_time_ms,
            threshold=settle_lower,
            units="ms",
            priority=3,
        ))

    # Yaw rate correlation (transient predictability)
    yaw_thresh = t.yaw_correlation_r2
    if 0 < m.yaw_rate_correlation < yaw_thresh:
        problems.append(Problem(
            category="damper",
            severity="significant" if m.yaw_rate_correlation < yaw_thresh * 0.77 else "minor",
            symptom=f"Yaw rate R^2 = {m.yaw_rate_correlation:.3f} (target >{yaw_thresh:.2f})",
            cause=(
                "Yaw rate does not track steering input well. "
                "Unpredictable transient response. Could be damper "
                "mismatch (front/rear rates too different) or "
                "excessive body roll allowing geometry changes."
            ),
            speed_context="all",
            measured=m.yaw_rate_correlation,
            threshold=yaw_thresh,
            units="R^2",
            priority=3,
        ))

    # Excessive roll rate (LS rebound too soft)
    roll_thresh = t.roll_rate_p95_deg_per_s
    if m.roll_rate_p95_deg_per_s > roll_thresh:
        problems.append(Problem(
            category="damper",
            severity="minor",
            symptom=f"Roll rate p95 = {m.roll_rate_p95_deg_per_s:.1f} deg/s (target <{roll_thresh:.0f})",
            cause=(
                "Body rolling too fast during transitions. "
                "LS rebound damping not controlling weight transfer "
                "rate sufficiently. Increase LS rebound +1-2 clicks."
            ),
            speed_context="all",
            measured=m.roll_rate_p95_deg_per_s,
            threshold=roll_thresh,
            units="deg/s",
            priority=3,
        ))


def _check_thermal(
    m: MeasuredState, s: CurrentSetup, car: CarModel, problems: list[Problem],
    t: AdaptiveThresholds = AdaptiveThresholds(),
) -> None:
    """Check tyre thermal: temp spread, carcass temp, pressure."""

    # Temperature spread per corner (inner - outer)
    # Positive = inner hot = too much camber magnitude
    # Negative = outer hot = not enough camber
    spreads = [
        ("LF", m.front_temp_spread_lf_c, "front"),
        ("RF", m.front_temp_spread_rf_c, "front"),
        ("LR", m.rear_temp_spread_lr_c, "rear"),
        ("RR", m.rear_temp_spread_rr_c, "rear"),
    ]

    temp_spread_thresh = t.temp_spread_c
    for corner, spread, axle in spreads:
        if abs(spread) > temp_spread_thresh:
            if spread > 0:
                problems.append(Problem(
                    category="thermal",
                    severity="significant" if abs(spread) > temp_spread_thresh * 1.5 else "minor",
                    symptom=f"{corner} inner hot by {spread:+.1f}C (threshold {temp_spread_thresh:.0f}C)",
                    cause=(
                        f"{corner} inner edge overheating. Too much negative "
                        f"camber for this {axle} axle. Reduce camber magnitude "
                        f"by ~{abs(spread)/20.0:.1f} deg."
                    ),
                    speed_context="all",
                    measured=spread,
                    threshold=temp_spread_thresh,
                    units="C",
                    priority=4,
                ))
            else:
                problems.append(Problem(
                    category="thermal",
                    severity="significant" if abs(spread) > temp_spread_thresh * 1.5 else "minor",
                    symptom=f"{corner} outer hot by {abs(spread):.1f}C (threshold {temp_spread_thresh:.0f}C)",
                    cause=(
                        f"{corner} outer edge overheating. Not enough negative "
                        f"camber for this {axle} axle. Increase camber magnitude "
                        f"by ~{abs(spread)/20.0:.1f} deg."
                    ),
                    speed_context="all",
                    measured=spread,
                    threshold=-8.0,
                    units="C",
                    priority=4,
                ))

    # Carcass temperature window (target 80-105 C)
    if m.front_carcass_mean_c > 0:
        if m.front_carcass_mean_c > 105:
            problems.append(Problem(
                category="thermal",
                severity="significant" if m.front_carcass_mean_c > 115 else "minor",
                symptom=f"Front carcass temp {m.front_carcass_mean_c:.0f}C (target <105C)",
                cause=(
                    "Front tyres overheating. Carcass degradation accelerates "
                    "above 105C. Check pressures, camber, and driving style."
                ),
                speed_context="all",
                measured=m.front_carcass_mean_c,
                threshold=105.0,
                units="C",
                priority=4,
            ))
        elif m.front_carcass_mean_c < 80:
            problems.append(Problem(
                category="thermal",
                severity="minor",
                symptom=f"Front carcass temp {m.front_carcass_mean_c:.0f}C (target >80C)",
                cause=(
                    "Front tyres not reaching operating temperature. "
                    "Grip is below potential. Increase front toe magnitude "
                    "for more scrub heating, or check cold pressures."
                ),
                speed_context="all",
                measured=m.front_carcass_mean_c,
                threshold=80.0,
                units="C",
                priority=4,
            ))

    if m.rear_carcass_mean_c > 0:
        if m.rear_carcass_mean_c > 105:
            problems.append(Problem(
                category="thermal",
                severity="significant" if m.rear_carcass_mean_c > 115 else "minor",
                symptom=f"Rear carcass temp {m.rear_carcass_mean_c:.0f}C (target <105C)",
                cause=(
                    "Rear tyres overheating. Carcass degradation accelerates "
                    "above 105C. Check rear pressures, diff preload (excess "
                    "wheelspin generates heat), and camber."
                ),
                speed_context="all",
                measured=m.rear_carcass_mean_c,
                threshold=105.0,
                units="C",
                priority=4,
            ))
        elif m.rear_carcass_mean_c < 80:
            problems.append(Problem(
                category="thermal",
                severity="minor",
                symptom=f"Rear carcass temp {m.rear_carcass_mean_c:.0f}C (target >80C)",
                cause=(
                    "Rear tyres not reaching operating temperature. "
                    "Grip is below potential. Increase rear toe or "
                    "diff preload for more heat generation."
                ),
                speed_context="all",
                measured=m.rear_carcass_mean_c,
                threshold=80.0,
                units="C",
                priority=4,
            ))

    # Hot pressure window (target 155-175 kPa)
    if m.front_pressure_mean_kpa > 0:
        if m.front_pressure_mean_kpa > 175:
            problems.append(Problem(
                category="thermal",
                severity="minor",
                symptom=f"Front hot pressure {m.front_pressure_mean_kpa:.0f} kPa (target <175)",
                cause="Front pressures too high. Reduce cold pressure.",
                speed_context="all",
                measured=m.front_pressure_mean_kpa,
                threshold=175.0,
                units="kPa",
                priority=4,
            ))
        elif m.front_pressure_mean_kpa < 155:
            problems.append(Problem(
                category="thermal",
                severity="minor",
                symptom=f"Front hot pressure {m.front_pressure_mean_kpa:.0f} kPa (target >155)",
                cause="Front pressures too low. Increase cold pressure.",
                speed_context="all",
                measured=m.front_pressure_mean_kpa,
                threshold=155.0,
                units="kPa",
                priority=4,
            ))

    if m.rear_pressure_mean_kpa > 0:
        if m.rear_pressure_mean_kpa > 175:
            problems.append(Problem(
                category="thermal",
                severity="minor",
                symptom=f"Rear hot pressure {m.rear_pressure_mean_kpa:.0f} kPa (target <175)",
                cause="Rear pressures too high. Reduce cold pressure.",
                speed_context="all",
                measured=m.rear_pressure_mean_kpa,
                threshold=175.0,
                units="kPa",
                priority=4,
            ))
        elif m.rear_pressure_mean_kpa < 155:
            problems.append(Problem(
                category="thermal",
                severity="minor",
                symptom=f"Rear hot pressure {m.rear_pressure_mean_kpa:.0f} kPa (target >155)",
                cause="Rear pressures too low. Increase cold pressure.",
                speed_context="all",
                measured=m.rear_pressure_mean_kpa,
                threshold=155.0,
                units="kPa",
                priority=4,
            ))


def _check_grip(
    m: MeasuredState, s: CurrentSetup, car: CarModel, problems: list[Problem],
    t: AdaptiveThresholds = AdaptiveThresholds(),
) -> None:
    """Check grip utilization: traction slip and braking slip."""

    # Rear traction slip (limited by rear grip)
    if m.rear_slip_ratio_p95 > 0.08:
        problems.append(Problem(
            category="grip",
            severity="significant" if m.rear_slip_ratio_p95 > 0.12 else "minor",
            symptom=f"Rear traction slip p95 = {m.rear_slip_ratio_p95:.3f} (target <0.08)",
            cause=(
                "Rear tyres spinning excessively under power. "
                "Traction limited. Lower TC slip, increase diff preload, "
                "or reduce power application aggressiveness."
            ),
            speed_context="traction",
            measured=m.rear_slip_ratio_p95,
            threshold=0.08,
            units="ratio",
            priority=5,
        ))

    # Front braking slip
    if m.front_slip_ratio_p95 > 0.06:
        problems.append(Problem(
            category="grip",
            severity="significant" if m.front_slip_ratio_p95 > 0.10 else "minor",
            symptom=f"Front braking slip p95 = {m.front_slip_ratio_p95:.3f} (target <0.06)",
            cause=(
                "Front tyres locking under braking. Brake bias too far "
                "forward. Shift brake bias rearward 0.5-1.0%."
            ),
            speed_context="braking",
            measured=m.front_slip_ratio_p95,
            threshold=0.06,
            units="ratio",
            priority=5,
        ))


def _compute_assessment(problems: list[Problem]) -> str:
    """Compute overall assessment from problem list."""
    if not problems:
        return "fast"

    has_critical = any(p.severity == "critical" for p in problems)
    has_significant = any(p.severity == "significant" for p in problems)
    n_significant = sum(1 for p in problems if p.severity == "significant")

    if has_critical:
        return "dangerous"
    if n_significant >= 3:
        return "compromised"
    if has_significant:
        return "competitive"
    return "fast"
