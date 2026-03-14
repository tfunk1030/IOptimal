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

    # ── Priority 5+: Brake & Hybrid Feedback ─────────────────────────────
    _check_brake_system(measured, setup, car, problems)

    # ── In-car adjustment warnings ────────────────────────────────────────
    _check_in_car_adjustments(measured, problems)

    # ── Speed-dependent balance (LLTD split) ──────────────────────────────
    _check_speed_dependent_balance(measured, car, problems)

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

    # Front bottoming — use clean-track count for severity assessment.
    # Kerb bottoming is a driving line choice, not a setup failure.
    front_bottom_thresh = t.bottoming_events_front
    front_clean = (m.bottoming_event_count_front_clean
                   if m.bottoming_event_count_front_clean > 0 or m.bottoming_event_count_front_kerb > 0
                   else m.bottoming_event_count_front)  # fallback for old data
    if front_clean > front_bottom_thresh:
        sev = "critical" if front_clean > front_bottom_thresh * 4 else "significant"
        problems.append(Problem(
            category="safety",
            severity=sev,
            symptom=f"{front_clean} front bottoming events (clean track)",
            cause=(
                "Front suspension hitting bump stops on clean track surface. "
                "Causes spike loads through chassis, unpredictable handling, "
                "and potential aero stall. Front heave spring too soft or ride "
                "height too low."
            ),
            speed_context="all",
            measured=float(front_clean),
            threshold=float(front_bottom_thresh),
            units="events",
            priority=0,
        ))
    elif m.bottoming_event_count_front_kerb > 0:
        problems.append(Problem(
            category="safety",
            severity="minor",
            symptom=f"{m.bottoming_event_count_front_kerb} front bottoming events on kerbs only",
            cause=(
                "Front bottoming occurs only on kerb strikes — this is a driving "
                "line choice, not a spring rate issue. Consider shallower kerb "
                "usage or accept the contact."
            ),
            speed_context="all",
            measured=float(m.bottoming_event_count_front_kerb),
            threshold=float(front_bottom_thresh),
            units="events",
            priority=0,
        ))

    # Rear bottoming — same clean vs kerb split
    rear_bottom_thresh = t.bottoming_events_rear
    rear_clean = (m.bottoming_event_count_rear_clean
                  if m.bottoming_event_count_rear_clean > 0 or m.bottoming_event_count_rear_kerb > 0
                  else m.bottoming_event_count_rear)
    if rear_clean > rear_bottom_thresh:
        sev = "critical" if rear_clean > rear_bottom_thresh * 4 else "significant"
        problems.append(Problem(
            category="safety",
            severity=sev,
            symptom=f"{rear_clean} rear bottoming events (clean track)",
            cause=(
                "Rear suspension hitting bump stops on clean track surface. "
                "Causes rear instability and diffuser stall risk. Stiffen rear "
                "third spring or raise rear ride height."
            ),
            speed_context="all",
            measured=float(rear_clean),
            threshold=float(rear_bottom_thresh),
            units="events",
            priority=0,
        ))
    elif m.bottoming_event_count_rear_kerb > 0:
        problems.append(Problem(
            category="safety",
            severity="minor",
            symptom=f"{m.bottoming_event_count_rear_kerb} rear bottoming events on kerbs only",
            cause=(
                "Rear bottoming occurs only on kerb strikes — this is a driving "
                "line choice, not a spring rate issue."
            ),
            speed_context="all",
            measured=float(m.bottoming_event_count_rear_kerb),
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

    # Rear heave spring travel exhaustion at speed
    if m.rear_heave_travel_used_pct > 85.0:
        sev = "critical" if m.rear_heave_travel_used_pct > 95.0 else "significant"
        problems.append(Problem(
            category="safety",
            severity=sev,
            symptom=(
                f"Rear third spring travel {m.rear_heave_travel_used_pct:.0f}% "
                f"used at speed (p99 defl {m.rear_heave_defl_p99_mm:.1f}mm)"
            ),
            cause=(
                "Rear third spring using most of its available travel at speed. "
                "Diffuser stall risk and rear instability if travel exhausts. "
                "Fix: stiffen rear third spring or adjust rear third perch."
            ),
            speed_context="high",
            measured=m.rear_heave_travel_used_pct,
            threshold=85.0,
            units="%",
            priority=0,
        ))

    # Splitter scrape detection (CFSRrideHeight — most important aero channel)
    if m.splitter_scrape_events > 0:
        sev = "critical" if m.splitter_scrape_events > 20 else "significant"
        problems.append(Problem(
            category="safety",
            severity=sev,
            symptom=(
                f"{m.splitter_scrape_events} splitter scrape events "
                f"(min splitter RH = {m.splitter_rh_min_mm:.1f}mm)"
            ),
            cause=(
                "Center front splitter approaching or touching the ground. "
                "Causes sudden aero stall (complete downforce loss) and potential "
                "structural damage. Raise front ride height, stiffen front heave "
                "spring, or reduce front aero compression."
            ),
            speed_context="high",
            measured=float(m.splitter_scrape_events),
            threshold=0.0,
            units="events",
            priority=0,
        ))

    if m.splitter_rh_mean_at_speed_mm > 0 and m.splitter_rh_p01_mm < 5.0:
        problems.append(Problem(
            category="safety",
            severity="significant",
            symptom=(
                f"Splitter p01 clearance only {m.splitter_rh_p01_mm:.1f}mm "
                f"(mean {m.splitter_rh_mean_at_speed_mm:.1f}mm)"
            ),
            cause=(
                "Splitter running dangerously close to ground. Any additional "
                "bump, kerb, or dirty air will likely cause scraping. "
                "Increase front ride height margin."
            ),
            speed_context="high",
            measured=m.splitter_rh_p01_mm,
            threshold=5.0,
            units="mm",
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

    # Rear heave bottoming events (from deflection channel)
    if m.heave_bottoming_events_rear > 0:
        sev = "critical" if m.heave_bottoming_events_rear > 10 else "significant"
        problems.append(Problem(
            category="safety",
            severity=sev,
            symptom=(
                f"{m.heave_bottoming_events_rear} rear third spring bottoming events "
                f"(deflection within 2mm of DeflMax)"
            ),
            cause=(
                "Rear third spring physically bottoming out — deflection at mechanical "
                "limit. Rear becomes rigid, diffuser stall risk increases. "
                "Fix: stiffen rear third spring or adjust perch offset."
            ),
            speed_context="all",
            measured=float(m.heave_bottoming_events_rear),
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


def _check_brake_system(
    m: MeasuredState, s: CurrentSetup, car: CarModel, problems: list[Problem],
) -> None:
    """Check brake system: measured bias vs setup, ABS intervention."""

    # Measured brake bias vs setup bias (from brake line pressures)
    if m.measured_brake_bias_pct > 0 and s.brake_bias_pct > 0:
        bias_delta = abs(m.measured_brake_bias_pct - s.brake_bias_pct)
        if bias_delta > 3.0:
            problems.append(Problem(
                category="grip",
                severity="significant" if bias_delta > 5.0 else "minor",
                symptom=(
                    f"Measured brake bias {m.measured_brake_bias_pct:.1f}% "
                    f"differs from setup {s.brake_bias_pct:.1f}% "
                    f"(delta {bias_delta:.1f}%)"
                ),
                cause=(
                    "Measured hydraulic brake bias from line pressures does not match "
                    "the garage setting. This may indicate ABS intervention is skewing "
                    "the effective bias, or the bias setting is not being applied correctly."
                ),
                speed_context="braking",
                measured=m.measured_brake_bias_pct,
                threshold=s.brake_bias_pct,
                units="%",
                priority=5,
            ))

    # Excessive ABS intervention
    if m.abs_active_pct > 30.0:
        problems.append(Problem(
            category="grip",
            severity="significant" if m.abs_active_pct > 50.0 else "minor",
            symptom=f"ABS active {m.abs_active_pct:.0f}% of braking time",
            cause=(
                "ABS intervening too frequently. Either brake bias is too far "
                "forward (locking fronts) or ABS level is set too aggressively. "
                "Shift bias rearward or increase ABS intervention threshold."
            ),
            speed_context="braking",
            measured=m.abs_active_pct,
            threshold=30.0,
            units="%",
            priority=5,
        ))

    # ABS cutting significant force
    if m.abs_cut_mean_pct > 15.0:
        problems.append(Problem(
            category="grip",
            severity="significant" if m.abs_cut_mean_pct > 25.0 else "minor",
            symptom=f"ABS cutting {m.abs_cut_mean_pct:.0f}% of brake force when active",
            cause=(
                "ABS is reducing brake force significantly. This means the "
                "braking system is consistently overloading the tyres. "
                "Reduce brake bias forward or lower ABS aggressiveness."
            ),
            speed_context="braking",
            measured=m.abs_cut_mean_pct,
            threshold=15.0,
            units="%",
            priority=5,
        ))


def _check_in_car_adjustments(
    m: MeasuredState, problems: list[Problem],
) -> None:
    """Check if driver is frequently adjusting in-car settings.

    Frequent adjustments indicate the base setup value is wrong.
    """
    if m.brake_bias_adjustments > 5:
        problems.append(Problem(
            category="balance",
            severity="minor",
            symptom=(
                f"Brake bias adjusted {m.brake_bias_adjustments} times during session "
                f"(range {m.brake_bias_range[0]:.1f}-{m.brake_bias_range[1]:.1f})"
            ),
            cause=(
                "Driver frequently changing brake bias suggests the base "
                "setting is not optimal. Set garage bias closer to the "
                "most-used value to reduce in-car workload."
            ),
            speed_context="all",
            measured=float(m.brake_bias_adjustments),
            threshold=5.0,
            units="adjustments",
            priority=2,
        ))

    if m.tc_adjustments > 5:
        problems.append(Problem(
            category="grip",
            severity="minor",
            symptom=f"Traction control adjusted {m.tc_adjustments} times during session",
            cause=(
                "Driver frequently changing TC suggests rear grip is inconsistent. "
                "If TC is being increased through the stint, rear tyres are "
                "degrading. Check rear pressures, diff preload, and camber."
            ),
            speed_context="traction",
            measured=float(m.tc_adjustments),
            threshold=5.0,
            units="adjustments",
            priority=5,
        ))


def _check_speed_dependent_balance(
    m: MeasuredState, car: CarModel, problems: list[Problem],
) -> None:
    """Check for speed-dependent LLTD shift (aero vs mechanical balance split)."""
    if m.lltd_low_speed > 0 and m.lltd_high_speed > 0:
        lltd_shift = (m.lltd_high_speed - m.lltd_low_speed) * 100  # in %
        if abs(lltd_shift) > 5.0:  # >5% LLTD shift between speed ranges
            if lltd_shift > 0:
                problems.append(Problem(
                    category="balance",
                    severity="significant" if abs(lltd_shift) > 8.0 else "minor",
                    symptom=(
                        f"LLTD shifts +{lltd_shift:.1f}% from low to high speed "
                        f"(low {m.lltd_low_speed*100:.1f}%, high {m.lltd_high_speed*100:.1f}%)"
                    ),
                    cause=(
                        "Front carries more lateral load transfer at high speed than low speed. "
                        "Aero roll resistance is adding to front roll stiffness at speed. "
                        "This causes more understeer at high speed. "
                        "Soften front ARB or increase rear aero balance."
                    ),
                    speed_context="high",
                    measured=lltd_shift,
                    threshold=5.0,
                    units="%",
                    priority=2,
                ))
            else:
                problems.append(Problem(
                    category="balance",
                    severity="significant" if abs(lltd_shift) > 8.0 else "minor",
                    symptom=(
                        f"LLTD shifts {lltd_shift:.1f}% from low to high speed "
                        f"(low {m.lltd_low_speed*100:.1f}%, high {m.lltd_high_speed*100:.1f}%)"
                    ),
                    cause=(
                        "Rear carries more lateral load transfer at high speed. "
                        "This causes more oversteer at high speed — dangerous. "
                        "Stiffen front heave spring or increase front aero balance."
                    ),
                    speed_context="high",
                    measured=lltd_shift,
                    threshold=-5.0,
                    units="%",
                    priority=2,
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
