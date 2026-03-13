"""Delta detector — find what changed between sessions and what resulted.

This is where learning happens. Given two observations (chronologically
ordered), the detector identifies:
1. Setup deltas: which parameters changed and by how much
2. Performance deltas: what improved or degraded
3. Telemetry deltas: how the car's behavior changed
4. Causal hypotheses: which setup changes likely caused which effects

The key insight is that CONTROLLED experiments (one change at a time) produce
high-confidence learnings, while multi-change sessions produce lower-confidence
hypotheses that need corroboration from other sessions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from learner.observation import Observation


# Setup parameters grouped by solver step for attribution
STEP_GROUPS = {
    "step1_rake": [
        "front_rh_static", "rear_rh_static", "front_pushrod", "rear_pushrod",
    ],
    "step2_heave": [
        "front_heave_nmm", "rear_third_nmm",
    ],
    "step3_springs": [
        "torsion_bar_od_mm", "rear_spring_nmm",
    ],
    "step4_arb": [
        "front_arb_size", "front_arb_blade", "rear_arb_size", "rear_arb_blade",
    ],
    "step5_geometry": [
        "front_camber_deg", "rear_camber_deg", "front_toe_mm", "rear_toe_mm",
    ],
    "step6_dampers": [],  # dampers are nested, handled separately
    "aero": ["wing"],
    "other": ["fuel_l", "brake_bias_pct"],
}

# Telemetry metrics that matter for causal attribution
EFFECT_METRICS = {
    "platform": [
        "front_rh_std_mm", "rear_rh_std_mm",
        "front_bottoming_events", "rear_bottoming_events",
        "front_shock_vel_p95_mps", "rear_shock_vel_p95_mps",
    ],
    "balance": [
        "lltd_measured", "understeer_mean_deg",
        "understeer_high_speed_deg", "understeer_low_speed_deg",
        "body_slip_p95_deg",
    ],
    "aero": [
        "dynamic_front_rh_mm", "dynamic_rear_rh_mm",
        "roll_gradient_deg_per_g",
    ],
    "damper": [
        "front_rh_settle_time_ms", "rear_rh_settle_time_ms",
        "front_dominant_freq_hz", "rear_dominant_freq_hz",
    ],
    "performance": [
        "best_lap_time_s",
    ],
}

# Known causal relationships: setup_param → expected effect direction
# Format: (param, direction_of_increase) → [(metric, expected_direction)]
# direction: "+" means metric increases, "-" means it decreases
KNOWN_CAUSALITY = {
    ("front_heave_nmm", "+"): [
        ("front_rh_std_mm", "-"),
        ("front_bottoming_events", "-"),
    ],
    ("rear_third_nmm", "+"): [
        ("rear_rh_std_mm", "-"),
        ("rear_bottoming_events", "-"),
    ],
    ("rear_arb_blade", "+"): [
        ("lltd_measured", "+"),         # more rear ARB → more rear roll resistance → higher front LLTD
        ("understeer_mean_deg", "+"),   # more understeer (front lighter)
    ],
    ("front_camber_deg", "-"): [        # more negative = more static camber
        ("body_roll_p95_deg", "~"),     # minimal direct effect
    ],
    ("torsion_bar_od_mm", "+"): [
        ("front_dominant_freq_hz", "+"),
        ("front_rh_std_mm", "-"),       # stiffer spring = less RH variance
    ],
    ("rear_spring_nmm", "+"): [
        ("rear_dominant_freq_hz", "+"),
    ],
    ("wing", "+"): [
        ("dynamic_front_rh_mm", "-"),   # more downforce = more compression
        ("dynamic_rear_rh_mm", "-"),
    ],
}


@dataclass
class SetupDelta:
    """A single setup parameter change between sessions."""
    parameter: str
    before: Any
    after: Any
    delta: float | str  # numeric delta or "Soft→Medium" for categorical
    step_group: str     # which solver step this belongs to
    significance: str   # "major" | "minor" | "trivial"


@dataclass
class EffectDelta:
    """A measured telemetry change between sessions."""
    metric: str
    category: str       # "platform" | "balance" | "aero" | "damper" | "performance"
    before: float
    after: float
    delta: float
    pct_change: float   # relative change
    significance: str   # "large" | "moderate" | "small" | "noise"


@dataclass
class CausalHypothesis:
    """A hypothesis linking a setup change to an observed effect."""
    cause_param: str
    cause_delta: float | str
    effect_metric: str
    effect_delta: float
    direction_match: bool   # does the effect match known causality?
    confidence: float       # 0-1: how confident we are in this link
    mechanism: str          # physics explanation
    corroborated_by: int = 0  # how many other deltas support this


@dataclass
class SessionDelta:
    """Complete delta analysis between two sessions."""
    session_before: str
    session_after: str
    car: str
    track: str

    setup_changes: list[SetupDelta] = field(default_factory=list)
    telemetry_effects: list[EffectDelta] = field(default_factory=list)
    hypotheses: list[CausalHypothesis] = field(default_factory=list)

    # Meta
    num_setup_changes: int = 0
    controlled_experiment: bool = False  # True if only 1 solver step changed
    changed_steps: list[str] = field(default_factory=list)
    lap_time_delta_s: float = 0.0
    driver_style_changed: bool = False

    # Summary
    key_finding: str = ""
    confidence_level: str = ""  # "high" | "medium" | "low"

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def _numeric_delta(before: Any, after: Any) -> float | None:
    """Compute numeric delta, or None if not numeric."""
    try:
        return float(after) - float(before)
    except (TypeError, ValueError):
        return None


def _classify_setup_significance(param: str, delta: float | None) -> str:
    """Classify how significant a setup change is."""
    if delta is None:
        return "major"  # categorical change
    thresholds = {
        "front_heave_nmm": (5.0, 20.0),
        "rear_third_nmm": (20.0, 100.0),
        "torsion_bar_od_mm": (0.1, 0.3),
        "rear_spring_nmm": (10.0, 30.0),
        "front_arb_blade": (1, 2),
        "rear_arb_blade": (1, 2),
        "front_camber_deg": (0.2, 0.5),
        "rear_camber_deg": (0.2, 0.5),
        "front_toe_mm": (0.1, 0.3),
        "rear_toe_mm": (0.1, 0.3),
        "wing": (0.5, 1.0),
        "brake_bias_pct": (0.5, 1.5),
    }
    abs_d = abs(delta)
    if param in thresholds:
        minor, major = thresholds[param]
        if abs_d >= major:
            return "major"
        elif abs_d >= minor:
            return "minor"
        return "trivial"
    return "minor"


def _classify_effect_significance(metric: str, delta: float, pct: float) -> str:
    """Classify how significant a telemetry change is."""
    thresholds = {
        "best_lap_time_s": (0.1, 0.3),
        "front_rh_std_mm": (0.5, 1.5),
        "rear_rh_std_mm": (0.5, 1.5),
        "lltd_measured": (0.01, 0.03),
        "understeer_mean_deg": (0.3, 1.0),
        "front_bottoming_events": (2, 10),
        "rear_bottoming_events": (2, 10),
        "front_rh_settle_time_ms": (20, 80),
        "roll_gradient_deg_per_g": (0.05, 0.15),
    }
    abs_d = abs(delta)
    if metric in thresholds:
        small, large = thresholds[metric]
        if abs_d >= large:
            return "large"
        elif abs_d >= small:
            return "moderate"
        return "noise"
    # Fallback: use percentage
    if abs(pct) > 15:
        return "large"
    elif abs(pct) > 5:
        return "moderate"
    elif abs(pct) > 1:
        return "small"
    return "noise"


def _find_step_group(param: str) -> str:
    """Find which solver step group a parameter belongs to."""
    for group, params in STEP_GROUPS.items():
        if param in params:
            return group
    return "other"


def detect_delta(obs_before: Observation, obs_after: Observation) -> SessionDelta:
    """Compare two observations and produce a structured delta.

    Args:
        obs_before: Earlier session (baseline)
        obs_after: Later session (the experiment)

    Returns:
        SessionDelta with setup changes, effects, and causal hypotheses
    """
    delta = SessionDelta(
        session_before=obs_before.session_id,
        session_after=obs_after.session_id,
        car=obs_after.car,
        track=obs_after.track,
    )

    # ── 1. Find setup changes ────────────────────────────────────────
    setup_b = obs_before.setup
    setup_a = obs_after.setup

    changed_steps = set()

    for param in set(list(setup_b.keys()) + list(setup_a.keys())):
        if param == "dampers":
            continue  # handle nested dampers separately
        val_b = setup_b.get(param)
        val_a = setup_a.get(param)
        if val_b is None or val_a is None:
            continue
        if val_b == val_a:
            continue

        num_delta = _numeric_delta(val_b, val_a)
        display_delta = num_delta if num_delta is not None else f"{val_b}->{val_a}"
        step_group = _find_step_group(param)
        significance = _classify_setup_significance(param, num_delta)

        if significance != "trivial":
            changed_steps.add(step_group)

        delta.setup_changes.append(SetupDelta(
            parameter=param,
            before=val_b,
            after=val_a,
            delta=display_delta,
            step_group=step_group,
            significance=significance,
        ))

    # Handle damper changes
    d_b = setup_b.get("dampers", {})
    d_a = setup_a.get("dampers", {})
    if d_b and d_a:
        for corner in ["lf", "rf", "lr", "rr"]:
            cb = d_b.get(corner, {})
            ca = d_a.get(corner, {})
            for click in ["ls_comp", "ls_rbd", "hs_comp", "hs_rbd", "hs_slope"]:
                vb = cb.get(click)
                va = ca.get(click)
                if vb is not None and va is not None and vb != va:
                    param_name = f"damper_{corner}_{click}"
                    delta.setup_changes.append(SetupDelta(
                        parameter=param_name,
                        before=vb,
                        after=va,
                        delta=va - vb,
                        step_group="step6_dampers",
                        significance="minor" if abs(va - vb) == 1 else "major",
                    ))
                    changed_steps.add("step6_dampers")

    delta.num_setup_changes = len([s for s in delta.setup_changes
                                    if s.significance != "trivial"])
    delta.changed_steps = sorted(changed_steps)
    delta.controlled_experiment = len(changed_steps) <= 1

    # ── 2. Find telemetry effects ────────────────────────────────────
    telem_b = obs_before.telemetry
    telem_a = obs_after.telemetry

    for category, metrics in EFFECT_METRICS.items():
        for metric in metrics:
            vb = telem_b.get(metric, 0.0)
            va = telem_a.get(metric, 0.0)
            if vb is None or va is None:
                continue

            try:
                d_val = float(va) - float(vb)
                pct = (d_val / abs(float(vb)) * 100) if abs(float(vb)) > 1e-6 else 0.0
            except (TypeError, ValueError):
                continue

            sig = _classify_effect_significance(metric, d_val, pct)
            if sig != "noise":
                delta.telemetry_effects.append(EffectDelta(
                    metric=metric,
                    category=category,
                    before=float(vb),
                    after=float(va),
                    delta=round(d_val, 4),
                    pct_change=round(pct, 1),
                    significance=sig,
                ))

    # ── 3. Performance delta ─────────────────────────────────────────
    lt_b = obs_before.performance.get("best_lap_time_s", 0)
    lt_a = obs_after.performance.get("best_lap_time_s", 0)
    if lt_b > 0 and lt_a > 0:
        delta.lap_time_delta_s = round(lt_a - lt_b, 3)

    # ── 4. Generate causal hypotheses ────────────────────────────────
    for sc in delta.setup_changes:
        if sc.significance == "trivial":
            continue
        if not isinstance(sc.delta, (int, float)):
            continue

        direction = "+" if sc.delta > 0 else "-"
        key = (sc.parameter, direction)

        for te in delta.telemetry_effects:
            if te.significance == "noise":
                continue

            # Check if this is a known causal relationship
            known = KNOWN_CAUSALITY.get(key, [])
            known_match = None
            for k_metric, k_dir in known:
                if k_metric == te.metric:
                    known_match = k_dir
                    break

            effect_dir = "+" if te.delta > 0 else "-"

            if known_match:
                direction_match = (known_match == effect_dir) or (known_match == "~")
                confidence = 0.8 if direction_match else 0.3
                if delta.controlled_experiment:
                    confidence = min(1.0, confidence + 0.15)
                mechanism = (f"{sc.parameter} {direction} -> expected "
                             f"{te.metric} {known_match}, observed {effect_dir}")
            else:
                # Unknown relationship — lower confidence, worth logging
                direction_match = False
                confidence = 0.2 if delta.controlled_experiment else 0.1
                mechanism = (f"{sc.parameter} {direction} -> observed "
                             f"{te.metric} {effect_dir} (no known causal link)")

            delta.hypotheses.append(CausalHypothesis(
                cause_param=sc.parameter,
                cause_delta=sc.delta,
                effect_metric=te.metric,
                effect_delta=te.delta,
                direction_match=direction_match,
                confidence=confidence,
                mechanism=mechanism,
            ))

    # ── 5. Driver style change detection ─────────────────────────────
    dp_b = obs_before.driver_profile
    dp_a = obs_after.driver_profile
    if dp_b.get("style") != dp_a.get("style"):
        delta.driver_style_changed = True

    # ── 6. Confidence and summary ────────────────────────────────────
    if delta.controlled_experiment and not delta.driver_style_changed:
        delta.confidence_level = "high"
    elif delta.num_setup_changes <= 3 and not delta.driver_style_changed:
        delta.confidence_level = "medium"
    else:
        delta.confidence_level = "low"

    # Generate key finding
    delta.key_finding = _summarize_delta(delta)

    return delta


def _summarize_delta(delta: SessionDelta) -> str:
    """Generate a one-line summary of the most important finding."""
    if not delta.setup_changes:
        return "No setup changes detected between sessions."

    # Find the biggest setup change
    major_changes = [s for s in delta.setup_changes if s.significance == "major"]
    if not major_changes:
        major_changes = [s for s in delta.setup_changes if s.significance == "minor"]

    if not major_changes:
        return "Only trivial setup differences."

    # Find the biggest performance/telemetry effect
    large_effects = [e for e in delta.telemetry_effects if e.significance == "large"]
    if not large_effects:
        large_effects = [e for e in delta.telemetry_effects if e.significance == "moderate"]

    change_str = ", ".join(f"{c.parameter}: {c.delta}" for c in major_changes[:3])

    if delta.lap_time_delta_s != 0:
        faster_slower = "faster" if delta.lap_time_delta_s < 0 else "slower"
        time_str = f" ({abs(delta.lap_time_delta_s):.3f}s {faster_slower})"
    else:
        time_str = ""

    effect_str = ""
    if large_effects:
        effect_str = "; effects: " + ", ".join(
            f"{e.metric} {e.delta:+.2f}" for e in large_effects[:3]
        )

    conf_str = f" [{delta.confidence_level} confidence]"

    return f"Changed {change_str}{time_str}{effect_str}{conf_str}"
