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
# NOTE: wing_angle_deg is NOT pinned — track-dependent, let solver use aero map + k-NN
_PINNED: dict[str, list[str]] = {}

# Parameters with calibrated physics models (HIGH when sessions >= 10, MED when 3-9)
_CALIBRATED: dict[str, list[str]] = {
    "bmw": [
        "front_heave_nmm", "rear_third_nmm", "torsion_bar_od_mm",
        "rear_spring_nmm", "front_arb_blade", "rear_arb_blade",
        "front_arb_size", "rear_arb_size",
        "front_camber_deg", "rear_camber_deg",
        "front_toe_mm", "rear_toe_mm",
        "diff_preload_nm", "diff_clutch_plates",
        "front_pushrod_mm", "rear_pushrod_mm",
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
        # Pushrod always paired with spring/RH changes — geometry-driven, HIGH confidence
        "front_pushrod_mm", "rear_pushrod_mm",
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
    "front_pushrod_mm": {
        "increase": "pushrod raised — compensates spring/RH change to maintain target ride height",
        "decrease": "pushrod lowered — compensates spring/RH change to maintain target ride height",
    },
    "rear_pushrod_mm": {
        "increase": "rear pushrod raised — compensates spring/RH change",
        "decrease": "rear pushrod lowered — compensates spring/RH change",
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
    """Fallback generic why-text when solver reasoning is unavailable."""
    if param in _DAMPER_KEYS:
        # Extract channel from param name: lf_ls_comp_clicks -> ls_comp
        parts = param.replace("_clicks", "").split("_")
        if len(parts) >= 3:
            channel = "_".join(parts[1:])  # ls_comp, hs_rbd, etc.
            return f"[ESTIMATE] {_DAMPER_WHY.get(channel, 'damper adjustment')}"
        return "[ESTIMATE] damper directional adjustment"
    why_map = _WHY.get(param, _WHY["default"])
    return why_map.get(direction, why_map.get("increase", "solver recommendation"))


def build_solver_reasoning(
    step1: object | None = None,
    step2: object | None = None,
    step3: object | None = None,
    step4: object | None = None,
    step5: object | None = None,
    step6: object | None = None,
    supporting: object | None = None,
) -> dict[str, str]:
    """Build parameter-level constraint-based reasoning from solver step results.

    Returns a dict mapping parameter names to physics-justified why-strings
    referencing actual constraint values from the solver run.
    """
    reasons: dict[str, str] = {}

    # ── Step 1: Rake / Ride Heights ─────────────────────────────────
    if step1 is not None:
        _df_bal = getattr(step1, "df_balance_pct", None)
        _ld = getattr(step1, "ld_ratio", None)
        _vbm = getattr(step1, "vortex_burst_margin_mm", None)
        _bal_err = getattr(step1, "balance_error_pct", None)

        rh_detail = []
        if _df_bal is not None:
            rh_detail.append(f"DF balance={_df_bal:.1f}%")
        if _bal_err is not None:
            rh_detail.append(f"error={_bal_err:+.2f}%")
        if _vbm is not None:
            rh_detail.append(f"vortex margin={_vbm:.1f}mm")
        if _ld is not None:
            rh_detail.append(f"L/D={_ld:.2f}")
        _rh_why = f"rake target: {', '.join(rh_detail)}" if rh_detail else None
        if _rh_why:
            reasons["front_rh_static"] = _rh_why
            reasons["rear_rh_static"] = _rh_why
            reasons["front_pushrod_mm"] = f"pushrod compensates RH change ({_rh_why})"
            reasons["rear_pushrod_mm"] = f"pushrod compensates RH change ({_rh_why})"
            reasons["wing_angle_deg"] = _rh_why

    # ── Step 2: Heave / Third Springs ───────────────────────────────
    if step2 is not None:
        for prefix, param in [("front", "front_heave_nmm"), ("rear", "rear_third_nmm")]:
            _bind = getattr(step2, f"{prefix}_binding_constraint", None)
            _bm = getattr(step2, f"{prefix}_bottoming_margin_mm", None)
            _sigma = getattr(step2, f"{prefix}_sigma_at_rate_mm", None)
            _travel = getattr(step2, "travel_margin_front_mm", None) if prefix == "front" else None

            parts = []
            if _bind:
                parts.append(f"binding: {_bind}")
            if _bm is not None:
                parts.append(f"bottoming margin={_bm:.1f}mm")
            if _sigma is not None:
                parts.append(f"RH σ={_sigma:.2f}mm")
            if _travel is not None:
                parts.append(f"travel margin={_travel:.1f}mm")
            if parts:
                reasons[param] = "; ".join(parts)

    # ── Step 3: Corner Springs ──────────────────────────────────────
    if step3 is not None:
        _f_iso = getattr(step3, "front_freq_isolation_ratio", None)
        _r_iso = getattr(step3, "rear_freq_isolation_ratio", None)
        _f_hc = getattr(step3, "front_heave_corner_ratio", None)
        _r_hc = getattr(step3, "rear_third_corner_ratio", None)
        _bump_freq = getattr(step3, "track_bump_freq_hz", None)

        front_parts = []
        if _f_iso is not None:
            front_parts.append(f"freq isolation={_f_iso:.2f}")
        if _f_hc is not None:
            front_parts.append(f"heave/corner ratio={_f_hc:.2f}")
        if _bump_freq is not None:
            front_parts.append(f"bump freq={_bump_freq:.1f}Hz")
        if front_parts:
            reasons["torsion_bar_od_mm"] = "; ".join(front_parts)

        rear_parts = []
        if _r_iso is not None:
            rear_parts.append(f"freq isolation={_r_iso:.2f}")
        if _r_hc is not None:
            rear_parts.append(f"third/corner ratio={_r_hc:.2f}")
        if rear_parts:
            reasons["rear_spring_nmm"] = "; ".join(rear_parts)

    # ── Step 4: ARBs ────────────────────────────────────────────────
    if step4 is not None:
        _lltd_a = getattr(step4, "lltd_achieved", None)
        _lltd_t = getattr(step4, "lltd_target", None)
        _lltd_e = getattr(step4, "lltd_error", None)

        arb_parts = []
        if _lltd_t is not None:
            arb_parts.append(f"LLTD target={_lltd_t:.3f}")
        if _lltd_a is not None:
            arb_parts.append(f"achieved={_lltd_a:.3f}")
        if _lltd_e is not None:
            arb_parts.append(f"error={_lltd_e:+.4f}")
        _arb_why = "; ".join(arb_parts) if arb_parts else None
        if _arb_why:
            for k in ("front_arb_blade", "rear_arb_blade", "front_arb_size", "rear_arb_size"):
                reasons[k] = _arb_why

    # ── Step 5: Wheel Geometry ──────────────────────────────────────
    if step5 is not None:
        _fc = getattr(step5, "front_camber_deg", None)
        _rc = getattr(step5, "rear_camber_deg", None)
        if _fc is not None:
            reasons["front_camber_deg"] = f"optimised contact patch at limit (camber={_fc:.1f}°)"
        if _rc is not None:
            reasons["rear_camber_deg"] = f"rear tyre load distribution (camber={_rc:.1f}°)"

    # ── Step 6: Dampers ─────────────────────────────────────────────
    if step6 is not None:
        _zls_f = getattr(step6, "zeta_ls_front", None)
        _zls_r = getattr(step6, "zeta_ls_rear", None)
        _zhs_f = getattr(step6, "zeta_hs_front", None)
        _zhs_r = getattr(step6, "zeta_hs_rear", None)
        _slope = getattr(step6, "hs_slope_reasoning", None)

        for corner in ("lf", "rf", "lr", "rr"):
            is_front = corner.startswith("l") or corner.startswith("r")
            # Determine axle: lf/rf = front, lr/rr = rear
            axle = "front" if corner[1] == "f" else "rear"
            _zls = _zls_f if axle == "front" else _zls_r
            _zhs = _zhs_f if axle == "front" else _zhs_r
            parts = []
            if _zls is not None:
                parts.append(f"ζ_ls={_zls:.2f}")
            if _zhs is not None:
                parts.append(f"ζ_hs={_zhs:.2f}")
            if _slope and axle == "front":
                parts.append(f"slope: {_slope}")
            detail = "; ".join(parts) if parts else None
            for ch in ("ls_comp", "hs_comp", "ls_rbd", "hs_rbd"):
                key = f"{corner}_{ch}_clicks"
                base = _DAMPER_WHY.get(ch, ch)
                if detail:
                    reasons[key] = f"{base} ({detail})"
                else:
                    reasons[key] = base

    # ── Supporting: diff, TC, brake ─────────────────────────────────
    if supporting is not None:
        _diff_r = getattr(supporting, "diff_reasoning", None)
        if _diff_r:
            reasons["diff_preload_nm"] = _diff_r
            reasons["diff_clutch_plates"] = _diff_r
        _tc_r = getattr(supporting, "tc_reasoning", None)
        if _tc_r:
            reasons["tc_gain"] = _tc_r
            reasons["tc_slip"] = _tc_r
        _bb_r = getattr(supporting, "brake_bias_reasoning", None)
        if _bb_r:
            reasons["brake_bias_pct"] = _bb_r

    return reasons


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
    "front_pushrod_mm": "Front Pushrod",
    "rear_pushrod_mm": "Rear Pushrod",
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
    "front_pushrod_mm": "mm",
    "rear_pushrod_mm": "mm",
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
    solver_reasoning: dict[str, str] | None = None,
) -> list[ParameterChange]:
    """Compare current and recommended setups; return list of meaningful changes.

    When *solver_reasoning* is provided, constraint-based why-strings from the
    actual solver run are used instead of the static ``_WHY`` dictionary.
    """
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
        # Prefer run-specific constraint reasoning; fall back to generic text.
        if solver_reasoning and param in solver_reasoning:
            why = solver_reasoning[param]
        else:
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
    solver_reasoning: dict[str, str] | None = None,
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
        solver_reasoning: constraint-based why-strings from build_solver_reasoning()
    """
    all_changes = detect_changes(current, recommended, car, session_count,
                                 solver_reasoning=solver_reasoning)
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
