"""delta_card.py — Delta card formatter for iOptimal setup recommendations.

Produces:
  1. DELTA SECTION — only what changed, with confidence tiers and why
  2. FULL SETUP SECTION — complete parameter list (for sharing/reference)

Confidence tiers:
  PIN  🔒  — locked from IBT observations (e.g. Ferrari wing=17 always)
  HIGH ✅  — multiple IBT sessions confirm, calibrated physics model
  MED  ℹ️  — physics-derived, reasonable model, limited sessions
  EST  ⚠️  — estimate, uncalibrated car or parameter

Safe mode:  only HIGH + PIN changes shown
Aggressive: all changes, EST sorted last
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

W = 60  # card width

# ─── Confidence tier registry ────────────────────────────────────────────────

# Parameters that are PINNED from IBT observations
_PINNED: dict[str, list[str]] = {
    "ferrari": ["wing_angle_deg"],  # All 20 sessions wing=17
}

# Parameters with calibrated physics models (HIGH when sessions >= 10, MED when 3-9)
_CALIBRATED: dict[str, list[str]] = {
    "bmw": [
        "front_heave_nmm", "rear_third_nmm", "torsion_bar_od_mm",
        "rear_spring_nmm", "front_arb_blade", "rear_arb_blade",
        "front_arb_size", "rear_arb_size",
        "front_camber_deg", "rear_camber_deg",
        "front_toe_mm", "rear_toe_mm",
        "diff_preload_nm", "diff_clutch_plates",
        "lf_ls_comp_clicks", "rf_ls_comp_clicks", "lr_ls_comp_clicks", "rr_ls_comp_clicks",
        "lf_hs_comp_clicks", "rf_hs_comp_clicks", "lr_hs_comp_clicks", "rr_hs_comp_clicks",
        "lf_ls_rbd_clicks", "rf_ls_rbd_clicks", "lr_ls_rbd_clicks", "rr_ls_rbd_clicks",
        "lf_hs_rbd_clicks", "rf_hs_rbd_clicks", "lr_hs_rbd_clicks", "rr_hs_rbd_clicks",
    ],
    "ferrari": [
        "front_heave_nmm", "rear_third_nmm", "torsion_bar_od_mm",
        "rear_spring_nmm", "front_arb_blade", "rear_arb_blade",
        "front_arb_size", "rear_arb_size",
        "front_camber_deg", "rear_camber_deg",
        "front_toe_mm", "rear_toe_mm",
        "diff_preload_nm",
        "wing_angle_deg",
        "front_rh_static", "rear_rh_static",
    ],
    "cadillac": [
        "front_heave_nmm", "rear_third_nmm", "torsion_bar_od_mm",
        "front_arb_blade", "rear_arb_blade",
        "front_camber_deg", "rear_camber_deg",
    ],
}

# Parameters that are always ESTIMATE (uncalibrated physics)
_ESTIMATE: dict[str, list[str]] = {
    "ferrari": [
        "lf_ls_comp_clicks", "rf_ls_comp_clicks", "lr_ls_comp_clicks", "rr_ls_comp_clicks",
        "lf_hs_comp_clicks", "rf_hs_comp_clicks", "lr_hs_comp_clicks", "rr_hs_comp_clicks",
        "lf_ls_rbd_clicks", "rf_ls_rbd_clicks", "lr_ls_rbd_clicks", "rr_ls_rbd_clicks",
        "lf_hs_rbd_clicks", "rf_hs_rbd_clicks", "lr_hs_rbd_clicks", "rr_hs_rbd_clicks",
        "tc_gain", "tc_slip",
    ],
    "cadillac": [
        "lf_ls_comp_clicks", "rf_ls_comp_clicks", "lr_ls_comp_clicks", "rr_ls_comp_clicks",
        "lf_hs_comp_clicks", "rf_hs_comp_clicks", "lr_hs_comp_clicks", "rr_hs_comp_clicks",
        "lf_ls_rbd_clicks", "rf_ls_rbd_clicks", "lr_ls_rbd_clicks", "rr_ls_rbd_clicks",
        "lf_hs_rbd_clicks", "rf_hs_rbd_clicks", "lr_hs_rbd_clicks", "rr_hs_rbd_clicks",
        "diff_preload_nm", "diff_clutch_plates", "tc_gain", "tc_slip",
        "front_rh_static", "rear_rh_static",
    ],
    "porsche": ["__all__"],  # All parameters are ESTIMATE for Porsche
    "acura": [
        "lf_ls_comp_clicks", "rf_ls_comp_clicks", "lr_ls_comp_clicks", "rr_ls_comp_clicks",
        "lf_hs_comp_clicks", "rf_hs_comp_clicks", "lr_hs_comp_clicks", "rr_hs_comp_clicks",
        "lf_ls_rbd_clicks", "rf_ls_rbd_clicks", "lr_ls_rbd_clicks", "rr_ls_rbd_clicks",
        "lf_hs_rbd_clicks", "rf_hs_rbd_clicks", "lr_hs_rbd_clicks", "rr_hs_rbd_clicks",
        "diff_preload_nm", "tc_gain", "tc_slip",
    ],
}


def get_confidence_tier(param: str, car: str, session_count: int) -> str:
    """Return confidence tier: PIN / HIGH / MED / EST."""
    if param in _PINNED.get(car, []):
        return "PIN"
    estimate_list = _ESTIMATE.get(car, [])
    if "__all__" in estimate_list or param in estimate_list:
        return "EST"
    calibrated = _CALIBRATED.get(car, [])
    if param in calibrated:
        return "HIGH" if session_count >= 10 else ("MED" if session_count >= 3 else "EST")
    return "MED"


def _tier_icon(tier: str) -> str:
    return {"PIN": "🔒 PIN", "HIGH": "✅ HIGH", "MED": "ℹ️  MED", "EST": "⚠️  EST"}.get(tier, "❓")


# ─── Why text library ─────────────────────────────────────────────────────────

_WHY: dict[str, dict[str, str]] = {
    "front_heave_nmm": {
        "increase": "platform σ too high — stiffer reduces RH variance at speed",
        "decrease": "platform too stiff for this track — softer recovers mechanical grip",
    },
    "rear_third_nmm": {
        "increase": "rear platform instability — stiffer controls aero platform",
        "decrease": "rear third too stiff — softening aids kerb absorption",
    },
    "torsion_bar_od_mm": {
        "increase": "LLTD range shift — need more front roll stiffness authority",
        "decrease": "front too stiff — LLTD biased too far front, reduce OD",
    },
    "rear_spring_nmm": {
        "increase": "rear indexed rate increase — platform control",
        "decrease": "rear indexed rate decrease — compliance over bumps",
    },
    "front_arb_blade": {
        "increase": "more front roll resistance — shift LLTD toward front",
        "decrease": "less front roll resistance — reduce front LLTD",
    },
    "rear_arb_blade": {
        "increase": "more rear roll resistance — reduce oversteer tendency",
        "decrease": "less rear roll resistance — increase rear compliance",
    },
    "front_arb_size": {
        "increase": "LLTD range insufficient at current size — step up",
        "decrease": "oversized ARB for this track — step down for better LLTD range",
    },
    "rear_arb_size": {
        "increase": "rear roll stiffness increase — more rear-biased LLTD",
        "decrease": "rear ARB too stiff — reduce for better traction",
    },
    "diff_preload_nm": {
        "increase": "rear exit slip elevated — more preload for exit traction",
        "decrease": "diff too locked — ease preload for corner rotation",
    },
    "diff_clutch_plates": {
        "increase": "more lock authority needed — add plates for traction-limited track",
        "decrease": "reduce lock authority — too much diff locking rotation",
    },
    "front_camber_deg": {
        "increase": "more negative camber — increase front contact patch at limit",
        "decrease": "reduce negative camber — trim front inside edge wear",
    },
    "rear_camber_deg": {
        "increase": "more negative rear camber — rear tyre temp optimization",
        "decrease": "reduce rear camber — rear tyre wear",
    },
    "front_rh_static": {
        "increase": "raise front RH — reduce underfloor DF for this track",
        "decrease": "lower front RH — increase underfloor downforce",
    },
    "rear_rh_static": {
        "increase": "raise rear RH — reduce rake angle",
        "decrease": "lower rear RH — increase rake for more aero balance rear",
    },
    "wing_angle_deg": {
        "increase": "more wing DF — higher downforce requirement",
        "decrease": "less wing drag — lower DF track or priority is top speed",
    },
    "tc_gain": {
        "increase": "exit slip elevated — more TC intervention",
        "decrease": "TC too aggressive — clipping power on exit",
    },
    "default": {
        "increase": "directional recommendation from solver",
        "decrease": "directional recommendation from solver",
    },
}

_DAMPER_KEYS = {
    "lf_ls_comp_clicks", "rf_ls_comp_clicks", "lr_ls_comp_clicks", "rr_ls_comp_clicks",
    "lf_hs_comp_clicks", "rf_hs_comp_clicks", "lr_hs_comp_clicks", "rr_hs_comp_clicks",
    "lf_ls_rbd_clicks", "rf_ls_rbd_clicks", "lr_ls_rbd_clicks", "rr_ls_rbd_clicks",
    "lf_hs_rbd_clicks", "rf_hs_rbd_clicks", "lr_hs_rbd_clicks", "rr_hs_rbd_clicks",
}

_DAMPER_WHY: dict[str, str] = {
    "ls_comp": "low-speed compression — controls body roll entry rate",
    "hs_comp": "high-speed compression — controls kerb reaction",
    "ls_rbd": "low-speed rebound — controls roll recovery, weight transfer",
    "hs_rbd": "high-speed rebound — controls return from kerb",
}


def _get_why(param: str, direction: str) -> str:
    if param in _DAMPER_KEYS:
        # Extract channel from param name: lf_ls_comp_clicks -> ls_comp
        parts = param.replace("_clicks", "").split("_")
        if len(parts) >= 3:
            channel = "_".join(parts[1:])  # ls_comp, hs_rbd, etc.
            return f"[ESTIMATE] {_DAMPER_WHY.get(channel, 'damper adjustment')}"
        return "[ESTIMATE] damper directional adjustment"
    why_map = _WHY.get(param, _WHY["default"])
    return why_map.get(direction, why_map.get("increase", "solver recommendation"))


# ─── Display names ────────────────────────────────────────────────────────────

_DISPLAY_NAMES: dict[str, str] = {
    "front_heave_nmm": "Front Heave",
    "rear_third_nmm": "Rear Heave/Third",
    "torsion_bar_od_mm": "Front Torsion Bar",
    "rear_spring_nmm": "Rear Torsion Bar",
    "front_arb_blade": "FARB Blade",
    "rear_arb_blade": "RARB Blade",
    "front_arb_size": "FARB Size",
    "rear_arb_size": "RARB Size",
    "diff_preload_nm": "Diff Preload",
    "diff_clutch_plates": "Diff Plates",
    "front_camber_deg": "Front Camber",
    "rear_camber_deg": "Rear Camber",
    "front_toe_mm": "Front Toe",
    "rear_toe_mm": "Rear Toe",
    "front_rh_static": "Front RH Target",
    "rear_rh_static": "Rear RH Target",
    "wing_angle_deg": "Wing Angle",
    "brake_bias_pct": "Brake Bias",
    "tc_gain": "TC Gain",
    "tc_slip": "TC Slip",
    "lf_ls_comp_clicks": "LF LS Comp",
    "rf_ls_comp_clicks": "RF LS Comp",
    "lr_ls_comp_clicks": "LR LS Comp",
    "rr_ls_comp_clicks": "RR LS Comp",
    "lf_hs_comp_clicks": "LF HS Comp",
    "rf_hs_comp_clicks": "RF HS Comp",
    "lr_hs_comp_clicks": "LR HS Comp",
    "rr_hs_comp_clicks": "RR HS Comp",
    "lf_ls_rbd_clicks": "LF LS Rbd",
    "rf_ls_rbd_clicks": "RF LS Rbd",
    "lr_ls_rbd_clicks": "LR LS Rbd",
    "rr_ls_rbd_clicks": "RR LS Rbd",
    "lf_hs_rbd_clicks": "LF HS Rbd",
    "rf_hs_rbd_clicks": "RF HS Rbd",
    "lr_hs_rbd_clicks": "LR HS Rbd",
    "rr_hs_rbd_clicks": "RR HS Rbd",
}

_UNITS: dict[str, str] = {
    "front_heave_nmm": "idx",   # Ferrari indexed
    "rear_third_nmm": "idx",
    "torsion_bar_od_mm": "idx",
    "rear_spring_nmm": "idx",
    "front_camber_deg": "deg",
    "rear_camber_deg": "deg",
    "front_toe_mm": "mm",
    "rear_toe_mm": "mm",
    "front_rh_static": "mm",
    "rear_rh_static": "mm",
    "wing_angle_deg": "deg",
    "diff_preload_nm": "Nm",
    "brake_bias_pct": "%",
}


def _unit(param: str, car: str) -> str:
    if car == "bmw" and param in ("front_heave_nmm", "rear_third_nmm"):
        return "N/mm"
    if car in ("cadillac", "acura", "porsche") and param == "front_heave_nmm":
        return "N/mm"
    if param.endswith("_clicks"):
        return "clicks"
    return _UNITS.get(param, "")


# ─── Change detection ─────────────────────────────────────────────────────────

# Minimum change thresholds to report (noise filter)
_THRESHOLDS: dict[str, float] = {
    "front_camber_deg": 0.2,
    "rear_camber_deg": 0.2,
    "front_toe_mm": 0.3,
    "rear_toe_mm": 0.3,
    "brake_bias_pct": 0.3,
    "diff_preload_nm": 2.0,
    "front_rh_static": 0.5,
    "rear_rh_static": 0.5,
    "__default__": 0.05,
}


@dataclass
class ParameterChange:
    param: str
    display_name: str
    unit: str
    current: float | str
    recommended: float | str
    delta: float | None
    confidence: str  # PIN / HIGH / MED / EST
    why: str
    direction: str  # "increase" / "decrease" / "same"


def detect_changes(
    current: dict,
    recommended: dict,
    car: str,
    session_count: int,
) -> list[ParameterChange]:
    """Compare current and recommended setups; return list of meaningful changes."""
    changes = []
    all_keys = set(recommended.keys())

    for param in sorted(all_keys):
        rec_val = recommended.get(param)
        cur_val = current.get(param)

        if rec_val is None:
            continue

        # Skip if no current value to compare
        if cur_val is None:
            continue

        # Compute delta
        try:
            delta = float(rec_val) - float(cur_val)
            threshold = _THRESHOLDS.get(param, _THRESHOLDS["__default__"])
            if abs(delta) < threshold:
                continue  # not a meaningful change
            direction = "increase" if delta > 0 else "decrease"
        except (TypeError, ValueError):
            # String parameter (ARB size labels etc.)
            if str(rec_val) == str(cur_val):
                continue
            delta = None
            direction = "change"

        tier = get_confidence_tier(param, car, session_count)
        why = _get_why(param, direction)
        display = _DISPLAY_NAMES.get(param, param.replace("_", " ").title())
        unit = _unit(param, car)

        changes.append(ParameterChange(
            param=param,
            display_name=display,
            unit=unit,
            current=cur_val,
            recommended=rec_val,
            delta=delta,
            confidence=tier,
            why=why,
            direction=direction,
        ))

    return changes


def filter_by_mode(changes: list[ParameterChange], mode: str) -> list[ParameterChange]:
    """Filter and sort changes by mode."""
    if mode == "safe":
        return [c for c in changes if c.confidence in ("HIGH", "PIN")]
    else:
        order = {"PIN": 0, "HIGH": 1, "MED": 2, "EST": 3}
        return sorted(changes, key=lambda c: order.get(c.confidence, 4))


# ─── Formatter ────────────────────────────────────────────────────────────────

def format_delta_card(
    current: dict,
    recommended: dict,
    car: str,
    track: str,
    session_count: int,
    best_lap_s: float | None = None,
    mode: str = "safe",
    full_setup_str: str | None = None,
) -> str:
    """Format a complete delta card + full setup.

    Args:
        current: dict of current setup param values (from IBT)
        recommended: dict of recommended param values (from solver)
        car: canonical car name
        track: track name
        session_count: number of k-NN sessions available
        best_lap_s: fastest observed lap time for this car/track
        mode: "safe" or "aggressive"
        full_setup_str: pre-formatted full setup string (from print_full_setup_report)
    """
    all_changes = detect_changes(current, recommended, car, session_count)
    visible_changes = filter_by_mode(all_changes, mode)
    hidden_count = len(all_changes) - len(visible_changes)

    car_label = {
        "ferrari": "Ferrari 499P", "bmw": "BMW M Hybrid V8",
        "cadillac": "Cadillac V-Series.R", "porsche": "Porsche 963",
        "acura": "Acura ARX-06",
    }.get(car, car.upper())

    mode_label = "SAFE MODE" if mode == "safe" else "AGGRESSIVE MODE"
    mode_icon = "🛡️" if mode == "safe" else "⚡"

    lines: list[str] = []
    a = lines.append

    # Header
    a("═" * W)
    a(f"🏎️  {car_label}  |  {track}")
    a(f"    {mode_icon} {mode_label}  |  {session_count} sessions")
    if best_lap_s:
        a(f"    Best observed: {best_lap_s:.3f}s")
    a("═" * W)
    a("")

    if not visible_changes:
        if all_changes:
            a(f"✅ No HIGH-confidence changes to make.")
            a(f"   {len(all_changes)} EST/MED changes suppressed in safe mode.")
            a(f"   Run --mode aggressive to see them.")
        else:
            a("✅ Current setup matches recommendation. No changes needed.")
        a("")
    else:
        total_str = f"({len(visible_changes)} of {len(all_changes)})" if hidden_count > 0 else f"({len(visible_changes)})"
        a(f"┌─ CHANGES  {total_str} {'─' * (W - 14 - len(total_str))}┐")
        a("│")

        for chg in visible_changes:
            tier_str = _tier_icon(chg.confidence)

            # Format value line
            if chg.delta is not None:
                delta_str = f"({chg.delta:+.0f})" if abs(chg.delta) >= 1 else f"({chg.delta:+.1f})"
                val_line = f"│  {chg.display_name:<18}  {str(chg.current):>5}  →  {str(chg.recommended):<5}  {chg.unit:<6} {delta_str:<6}  {tier_str}"
            else:
                val_line = f"│  {chg.display_name:<18}  {str(chg.current):>8}  →  {str(chg.recommended):<8}       {tier_str}"

            a(val_line)
            # Why line — indented, wrapped
            why_prefix = "│    Why: "
            why_text = chg.why
            if len(why_prefix) + len(why_text) > W - 2:
                why_text = why_text[: W - len(why_prefix) - 5] + "..."
            a(f"{why_prefix}{why_text}")
            a("│")

        a("└" + "─" * (W - 2) + "┘")
        a("")

        if hidden_count > 0 and mode == "safe":
            a(f"⚠️  {hidden_count} EST/MED change(s) hidden in safe mode.")
            a("   Run --mode aggressive to see all recommendations.")
            a("")

    # Confidence legend
    a("  Confidence:  🔒 PIN=from IBT  ✅ HIGH=calibrated  ℹ️ MED=physics  ⚠️ EST=estimate")
    a("")
    a("─" * W)
    a("  FULL SETUP")
    a("─" * W)
    a("")

    if full_setup_str:
        a(full_setup_str)
    else:
        a("  [Full setup not available]")

    return "\n".join(lines)
