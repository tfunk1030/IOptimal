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


def diagnose(
    measured: MeasuredState,
    setup: CurrentSetup,
    car: CarModel,
) -> Diagnosis:
    """Analyze telemetry and identify handling problems from physics.

    Args:
        measured: Telemetry-derived measurements from extract.py
        setup: Current garage setup from setup_reader.py
        car: Car physical model

    Returns:
        Diagnosis with prioritized problems list and overall assessment
    """
    diag = Diagnosis(
        lap_time_s=measured.lap_time_s,
        lap_number=measured.lap_number,
        lltd_pct=measured.lltd_measured * 100 if measured.lltd_measured > 0 else 0.0,
        weight_dist_front_pct=car.weight_dist_front * 100,
    )

    problems = []

    # ── Priority 0: Safety ──────────────────────────────────────────────
    _check_safety(measured, setup, car, problems)

    # ── Priority 1: Platform ────────────────────────────────────────────
    _check_platform(measured, setup, car, problems)

    # ── Priority 2: Balance ─────────────────────────────────────────────
    _check_balance(measured, setup, car, problems)

    # ── Priority 3: Dampers ─────────────────────────────────────────────
    _check_dampers(measured, setup, car, problems)

    # ── Priority 4: Thermal ─────────────────────────────────────────────
    _check_thermal(measured, setup, car, problems)

    # ── Priority 5: Grip ────────────────────────────────────────────────
    _check_grip(measured, setup, car, problems)

    # Sort by priority then severity
    severity_order = {"critical": 0, "significant": 1, "minor": 2}
    problems.sort(key=lambda p: (p.priority, severity_order.get(p.severity, 3)))
    diag.problems = problems

    # Overall assessment
    diag.assessment = _compute_assessment(problems)

    return diag


def _check_safety(
    m: MeasuredState, s: CurrentSetup, car: CarModel, problems: list[Problem]
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
    if m.bottoming_event_count_front > 5:
        sev = "critical" if m.bottoming_event_count_front > 20 else "significant"
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
            threshold=5.0,
            units="events",
            priority=0,
        ))

    # Rear bottoming
    if m.bottoming_event_count_rear > 5:
        sev = "critical" if m.bottoming_event_count_rear > 20 else "significant"
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
            threshold=5.0,
            units="events",
            priority=0,
        ))


def _check_platform(
    m: MeasuredState, s: CurrentSetup, car: CarModel, problems: list[Problem]
) -> None:
    """Check platform stability: ride height variance and excursion."""

    # Front variance
    if m.front_rh_std_mm > 8.0:
        sev = "significant" if m.front_rh_std_mm > 12.0 else "minor"
        problems.append(Problem(
            category="platform",
            severity=sev,
            symptom=f"Front RH variance {m.front_rh_std_mm:.1f}mm (threshold 8.0mm)",
            cause=(
                "Front platform oscillating too much at speed. "
                "Aero balance shifts with each oscillation cycle. "
                "Front heave spring too soft for this track surface."
            ),
            speed_context="high",
            measured=m.front_rh_std_mm,
            threshold=8.0,
            units="mm",
            priority=1,
        ))

    # Rear variance
    if m.rear_rh_std_mm > 10.0:
        sev = "significant" if m.rear_rh_std_mm > 15.0 else "minor"
        problems.append(Problem(
            category="platform",
            severity=sev,
            symptom=f"Rear RH variance {m.rear_rh_std_mm:.1f}mm (threshold 10.0mm)",
            cause=(
                "Rear platform oscillating too much at speed. "
                "Diffuser efficiency degrades with large RH variation. "
                "Rear third spring too soft for this track surface."
            ),
            speed_context="high",
            measured=m.rear_rh_std_mm,
            threshold=10.0,
            units="mm",
            priority=1,
        ))

    # Front excursion near bottoming
    # If excursion p99 is more than 80% of the dynamic ride height, we are close
    if s.front_rh_at_speed_mm > 0 and m.front_rh_excursion_measured_mm > 0:
        margin = m.front_rh_excursion_measured_mm / s.front_rh_at_speed_mm
        if margin > 0.80:
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
    m: MeasuredState, s: CurrentSetup, car: CarModel, problems: list[Problem]
) -> None:
    """Check balance: understeer, LLTD, body slip, speed gradient."""

    # Excessive understeer
    if m.understeer_mean_deg > 2.5:
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
            threshold=2.5,
            units="deg",
            priority=2,
        ))

    # Net oversteer (loose)
    if m.understeer_mean_deg < -0.5:
        sev = "significant" if m.understeer_mean_deg < -1.5 else "minor"
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
            threshold=-0.5,
            units="deg",
            priority=2,
        ))

    # Speed gradient: aero/mechanical mismatch
    if m.understeer_low_speed_deg != 0 and m.understeer_high_speed_deg != 0:
        gradient = m.understeer_high_speed_deg - m.understeer_low_speed_deg
        if abs(gradient) > 1.5:
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
                    threshold=1.5,
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
                    threshold=-1.5,
                    units="deg",
                    priority=2,
                ))

    # LLTD check
    if m.lltd_measured > 0:
        target_lltd = car.weight_dist_front + 0.03  # 3% above front weight dist
        lltd_delta = m.lltd_measured - target_lltd

        if lltd_delta > 0.08:
            problems.append(Problem(
                category="balance",
                severity="significant" if lltd_delta > 0.12 else "minor",
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
                threshold=(target_lltd + 0.08) * 100,
                units="%",
                priority=2,
            ))
        elif lltd_delta < -0.02:
            problems.append(Problem(
                category="balance",
                severity="significant" if lltd_delta < -0.06 else "minor",
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
                threshold=(target_lltd - 0.02) * 100,
                units="%",
                priority=2,
            ))

    # Body slip angle (rear instability)
    if m.body_slip_p95_deg > 4.0:
        problems.append(Problem(
            category="balance",
            severity="significant" if m.body_slip_p95_deg > 6.0 else "minor",
            symptom=f"Body slip angle p95 = {m.body_slip_p95_deg:.1f} deg (threshold 4.0)",
            cause=(
                "Rear of car sliding excessively. High body slip angle "
                "increases drag and risks snap oversteer. "
                "Rear grip insufficient: check diff preload, rear ARB, "
                "and rear tyre condition."
            ),
            speed_context="all",
            measured=m.body_slip_p95_deg,
            threshold=4.0,
            units="deg",
            priority=2,
        ))


def _check_dampers(
    m: MeasuredState, s: CurrentSetup, car: CarModel, problems: list[Problem]
) -> None:
    """Check damper response: settle time, yaw correlation, roll rate."""

    # Settle time too long (underdamped)
    if m.front_rh_settle_time_ms > 200:
        problems.append(Problem(
            category="damper",
            severity="significant" if m.front_rh_settle_time_ms > 350 else "minor",
            symptom=f"Front settle time {m.front_rh_settle_time_ms:.0f}ms (target <200ms)",
            cause=(
                "Front suspension takes too long to recover from bumps. "
                "Underdamped: platform oscillates instead of settling. "
                "Increase front LS rebound damping."
            ),
            speed_context="all",
            measured=m.front_rh_settle_time_ms,
            threshold=200.0,
            units="ms",
            priority=3,
        ))

    if m.rear_rh_settle_time_ms > 200:
        problems.append(Problem(
            category="damper",
            severity="significant" if m.rear_rh_settle_time_ms > 350 else "minor",
            symptom=f"Rear settle time {m.rear_rh_settle_time_ms:.0f}ms (target <200ms)",
            cause=(
                "Rear suspension takes too long to recover from bumps. "
                "Underdamped: platform oscillates instead of settling. "
                "Increase rear LS rebound damping."
            ),
            speed_context="all",
            measured=m.rear_rh_settle_time_ms,
            threshold=200.0,
            units="ms",
            priority=3,
        ))

    # Settle time too short (overdamped, losing compliance)
    if 0 < m.front_rh_settle_time_ms < 50:
        problems.append(Problem(
            category="damper",
            severity="minor",
            symptom=f"Front settle time {m.front_rh_settle_time_ms:.0f}ms (too fast, <50ms)",
            cause=(
                "Front suspension overdamped. Fast settle but loses "
                "compliance over bumps. Tyre load variation increases, "
                "reducing average grip. Reduce front LS rebound."
            ),
            speed_context="all",
            measured=m.front_rh_settle_time_ms,
            threshold=50.0,
            units="ms",
            priority=3,
        ))

    if 0 < m.rear_rh_settle_time_ms < 50:
        problems.append(Problem(
            category="damper",
            severity="minor",
            symptom=f"Rear settle time {m.rear_rh_settle_time_ms:.0f}ms (too fast, <50ms)",
            cause=(
                "Rear suspension overdamped. Fast settle but loses "
                "compliance over bumps. Reduce rear LS rebound."
            ),
            speed_context="all",
            measured=m.rear_rh_settle_time_ms,
            threshold=50.0,
            units="ms",
            priority=3,
        ))

    # Yaw rate correlation (transient predictability)
    if 0 < m.yaw_rate_correlation < 0.65:
        problems.append(Problem(
            category="damper",
            severity="significant" if m.yaw_rate_correlation < 0.50 else "minor",
            symptom=f"Yaw rate R^2 = {m.yaw_rate_correlation:.3f} (target >0.65)",
            cause=(
                "Yaw rate does not track steering input well. "
                "Unpredictable transient response. Could be damper "
                "mismatch (front/rear rates too different) or "
                "excessive body roll allowing geometry changes."
            ),
            speed_context="all",
            measured=m.yaw_rate_correlation,
            threshold=0.65,
            units="R^2",
            priority=3,
        ))

    # Excessive roll rate (LS rebound too soft)
    if m.roll_rate_p95_deg_per_s > 25.0:
        problems.append(Problem(
            category="damper",
            severity="minor",
            symptom=f"Roll rate p95 = {m.roll_rate_p95_deg_per_s:.1f} deg/s (target <25)",
            cause=(
                "Body rolling too fast during transitions. "
                "LS rebound damping not controlling weight transfer "
                "rate sufficiently. Increase LS rebound +1-2 clicks."
            ),
            speed_context="all",
            measured=m.roll_rate_p95_deg_per_s,
            threshold=25.0,
            units="deg/s",
            priority=3,
        ))


def _check_thermal(
    m: MeasuredState, s: CurrentSetup, car: CarModel, problems: list[Problem]
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

    for corner, spread, axle in spreads:
        if abs(spread) > 8.0:
            if spread > 0:
                problems.append(Problem(
                    category="thermal",
                    severity="significant" if abs(spread) > 12.0 else "minor",
                    symptom=f"{corner} inner hot by {spread:+.1f}C (threshold 8.0C)",
                    cause=(
                        f"{corner} inner edge overheating. Too much negative "
                        f"camber for this {axle} axle. Reduce camber magnitude "
                        f"by ~{abs(spread)/20.0:.1f} deg."
                    ),
                    speed_context="all",
                    measured=spread,
                    threshold=8.0,
                    units="C",
                    priority=4,
                ))
            else:
                problems.append(Problem(
                    category="thermal",
                    severity="significant" if abs(spread) > 12.0 else "minor",
                    symptom=f"{corner} outer hot by {abs(spread):.1f}C (threshold 8.0C)",
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
    m: MeasuredState, s: CurrentSetup, car: CarModel, problems: list[Problem]
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
