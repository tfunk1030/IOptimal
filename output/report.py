"""Output module — setup report and JSON summary.

Aggregates all 6 solver steps into a single coherent setup sheet
suitable for entering into iRacing garage, with engineering rationale.

Layout:
  1. Header
  2. SETUP TO ENTER — full garage parameter list, grouped for readability
  3. GARAGE CARD  — condensed overview
  4. TOP ACTIONS  — prioritized list with lap time impact
  5. STINT CARD   — condensed balance curve + pushrod schedule
  6. LAP TIME SENSITIVITY — ranked table
  7. SECTOR COMPROMISE — conflict table
  8. SETUP SPACE  — feasible range table (if --space)
  9. AERO / BALANCE checks
 10. VALIDATION CHECKLIST
"""

from __future__ import annotations

import json
import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from car_model.garage import GarageSetupState
from car_model.setup_registry import public_output_value, remap_public_output_keys


def _load_support_tier(car_slug: str, track_name: str) -> dict[str, Any] | None:
    """Load support tier for a car/track pair from validation evidence.

    Primary source: validation/objective_validation.json (explicit benchmark data).
    Fallback: data/calibration/<car>/calibration_points.json + models.json
      — synthesises a tier from session count when no benchmark entry exists.
    """
    validation_path = Path(__file__).resolve().parent.parent / "validation" / "objective_validation.json"
    if validation_path.exists():
        try:
            data = json.loads(validation_path.read_text())
            for entry in data.get("support_matrix", []):
                if (entry.get("car", "").lower() == car_slug.lower()
                        and track_name.lower().startswith(entry.get("track", "").lower()[:10])):
                    return entry
        except Exception:
            pass

    # Fallback: synthesise tier from calibration data on disk.
    # Uses the same logic as run_trace._compute_support_tier() so both displays agree.
    try:
        cal_dir = Path(__file__).resolve().parent.parent / "data" / "calibration" / car_slug.lower()
        if not cal_dir.exists():
            return None
        pts_file = cal_dir / "calibration_points.json"
        session_count = 0
        if pts_file.exists():
            raw = json.loads(pts_file.read_text(encoding="utf-8", errors="replace"))
            session_count = len(raw) if isinstance(raw, list) else len(raw.get("sessions", []))
        has_models = (cal_dir / "models.json").exists()
        if session_count == 0 and not has_models:
            return None
        # Map session count → confidence_tier label (mirrors aggregator thresholds).
        # Cap at "partial" — only the explicit validation/objective_validation.json
        # entries (checked above) may declare "calibrated".  This prevents arbitrary
        # car/track pairs from reaching calibrated status based on sample count alone.
        if has_models and session_count >= 30:
            tier = "partial"
        elif session_count >= 15:
            tier = "partial"
        elif session_count >= 5:
            tier = "exploratory"
        else:
            tier = "partial"  # has models.json but <1 session read
        return {
            "car": car_slug,
            "track": track_name,
            "confidence_tier": tier,
            "samples": session_count,
            "source": "calibration_data",
        }
    except Exception:
        pass
    return None

if TYPE_CHECKING:
    from solver.rake_solver import RakeSolution
    from solver.heave_solver import HeaveSolution
    from solver.corner_spring_solver import CornerSpringSolution
    from solver.arb_solver import ARBSolution
    from solver.wheel_geometry_solver import WheelGeometrySolution
    from solver.damper_solver import DamperSolution

W = 70  # report width


def _asdict_safe(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _asdict_safe(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_asdict_safe(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _asdict_safe(v) for k, v in obj.items()}
    return obj


def to_public_output_payload(car_name: str, obj: Any) -> Any:
    """Serialize solver output using the public per-car naming surface."""
    return remap_public_output_keys(car_name, _asdict_safe(obj))


def _hdr(title: str) -> str:
    pad = (W - len(title) - 2) // 2
    return "─" * pad + f" {title} " + "─" * (W - pad - len(title) - 2)


def _box_top(title: str) -> str:
    inner = W - 2
    t = f" {title} "
    pad = (inner - len(t)) // 2
    return "┌" + "─" * pad + t + "─" * (inner - pad - len(t)) + "┐"


def _box_bot() -> str:
    return "└" + "─" * (W - 2) + "┘"


def _row(left: str, right: str) -> str:
    """Two-column row inside a box, total inner width W-2."""
    col = (W - 2) // 2
    return "│" + left.ljust(col) + right.ljust(W - 2 - col) + "│"


def _full(text: str) -> str:
    """Full-width row inside a box."""
    return "│" + text.ljust(W - 2) + "│"


def _blank() -> str:
    return "│" + " " * (W - 2) + "│"


def _ok(val: bool) -> str:
    return "✓" if val else "✗"


def _changed_marker(param_key: str, new_val, current_params: dict | None) -> str:
    """Return ' ←' if this value differs from current_params."""
    if current_params is None or param_key not in current_params:
        return ""
    cur = current_params.get(param_key)
    try:
        if abs(float(new_val) - float(cur)) > 0.05:
            return " ←"
    except (TypeError, ValueError):
        if str(new_val) != str(cur):
            return " ←"
    return ""


def _setting(label: str, value: str, note: str = "", changed: str = "") -> str:
    text = f"  {label:<24} {value}"
    if note:
        text += f"  {note}"
    if changed:
        text += changed
    if len(text) > W - 2:
        text = text[:W - 5] + "..."
    return _full(text)


def _rotation_search_maps(step3: Any, step4: Any, step5: Any, supporting: Any) -> tuple[dict[str, str], dict[str, list[str]]]:
    status: dict[str, str] = {}
    evidence: dict[str, list[str]] = {}
    for container in (step3, step4, step5, supporting):
        if container is None:
            continue
        status.update(getattr(container, "parameter_search_status", {}) or {})
        evidence.update(getattr(container, "parameter_search_evidence", {}) or {})
    return status, evidence


def _rotation_search_lines(step3: Any, step4: Any, step5: Any, supporting: Any) -> list[str]:
    status, evidence = _rotation_search_maps(step3, step4, step5, supporting)
    if not status:
        return []
    lines: list[str] = []
    groups = [
        ("Diff search", ("diff_preload_nm", "diff_ramp_option_idx", "diff_clutch_plates")),
        ("Spring search", ("front_torsion_od_mm", "rear_spring_rate_nmm")),
        ("Geo search", ("front_toe_mm", "rear_toe_mm", "front_camber_deg", "rear_camber_deg")),
        ("RARB search", ("rear_arb_size", "rear_arb_blade")),
    ]
    for label, fields in groups:
        group_status = [status.get(field, "") for field in fields if status.get(field, "")]
        if not group_status:
            continue
        summary = max(set(group_status), key=group_status.count)
        lines.append(f"{label}: {summary}")
    sample_evidence = next((value for value in evidence.values() if value), [])
    if sample_evidence:
        if isinstance(sample_evidence, dict):
            sample_evidence = list(sample_evidence.values())
        try:
            items = list(sample_evidence)[:3]
            lines.append(f"Rotation evidence: {'; '.join(str(e) for e in items)}")
        except (TypeError, KeyError):
            pass
    return lines[:4]


def print_full_setup_report(
    car_name: str,
    track_name: str,
    wing: float,
    target_balance: float,
    step1: RakeSolution,
    step2: HeaveSolution,
    step3: CornerSpringSolution,
    step4: ARBSolution,
    step5: WheelGeometrySolution,
    step6: DamperSolution,
    stint_result: Any = None,
    sector_result: Any = None,
    sensitivity_result: Any = None,
    space_result: Any = None,
    supporting: Any = None,
    car: Any = None,
    fuel_l: float = 0.0,
    garage_outputs: Any = None,
    compact: bool = False,
    front_tb_turns_override: float | None = None,
    rear_tb_turns_override: float | None = None,
    front_camber_override: float | None = None,
    rear_camber_override: float | None = None,
    hybrid_enabled: bool | None = None,
    hybrid_corner_pct: float | None = None,
    front_diff_preload_nm: float | None = None,
    bias_migration_gain: float | None = None,
    current_params: dict | None = None,
    coupling_changes: list[Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    a = lines.append
    garage_model = None

    if car is not None:
        garage_model = getattr(car, "active_garage_output_model", lambda _track: None)(track_name)
    if garage_outputs is None and garage_model is not None:
        if garage_model is not None:
            garage_outputs = garage_model.predict(
                GarageSetupState.from_solver_steps(
                    step1=step1,
                    step2=step2,
                    step3=step3,
                    step5=step5,
                    fuel_l=fuel_l,
                ),
                front_excursion_p99_mm=step2.front_excursion_at_rate_mm,
            )

    tyre_fl = getattr(supporting, "tyre_cold_fl_kpa", 152.0) if supporting is not None else 152.0
    tyre_fr = getattr(supporting, "tyre_cold_fr_kpa", 152.0) if supporting is not None else 152.0
    tyre_rl = getattr(supporting, "tyre_cold_rl_kpa", 152.0) if supporting is not None else 152.0
    tyre_rr = getattr(supporting, "tyre_cold_rr_kpa", 152.0) if supporting is not None else 152.0
    brake_bias_val = getattr(supporting, "brake_bias_pct", None) if supporting is not None else None
    diff_preload_val = getattr(supporting, "diff_preload_nm", None) if supporting is not None else None
    diff_coast_val = getattr(supporting, "diff_ramp_coast", None) if supporting is not None else None
    diff_drive_val = getattr(supporting, "diff_ramp_drive", None) if supporting is not None else None
    diff_plates_val = getattr(supporting, "diff_clutch_plates", None) if supporting is not None else None
    tc_gain_val = getattr(supporting, "tc_gain", None) if supporting is not None else None
    tc_slip_val = getattr(supporting, "tc_slip", None) if supporting is not None else None
    brake_target_val = getattr(supporting, "brake_bias_target", None) if supporting is not None else None
    brake_migration_val = getattr(supporting, "brake_bias_migration", None) if supporting is not None else None
    front_master_cyl_val = getattr(supporting, "front_master_cyl_mm", None) if supporting is not None else None
    rear_master_cyl_val = getattr(supporting, "rear_master_cyl_mm", None) if supporting is not None else None
    pad_compound_val = getattr(supporting, "pad_compound", "") if supporting is not None else ""
    brake_hardware_status = getattr(supporting, "brake_hardware_status", "") if supporting is not None else ""
    brake_bias_status = getattr(supporting, "brake_bias_status", "solved") if supporting is not None else "solved"
    brake_target_status = getattr(supporting, "brake_bias_target_status", "pass-through") if supporting is not None else "pass-through"
    brake_migration_status = getattr(supporting, "brake_bias_migration_status", "pass-through") if supporting is not None else "pass-through"
    master_cyl_status = getattr(supporting, "master_cylinder_status", "pass-through") if supporting is not None else "pass-through"
    pad_compound_status = getattr(supporting, "pad_compound_status", "pass-through") if supporting is not None else "pass-through"
    diff_solution = getattr(supporting, "_diff_solution", None) if supporting is not None else None

    _is_ferrari = car is not None and getattr(car, "canonical_name", "") == "ferrari"
    _is_acura = car is not None and getattr(car, "canonical_name", "") == "acura"
    _has_rear_torsion = (car is not None and hasattr(car, "corner_spring")
                         and getattr(car.corner_spring, "rear_is_torsion_bar", False))
    _has_roll_dampers = (car is not None and hasattr(car, "damper")
                         and getattr(car.damper, "has_roll_dampers", False))
    display_front_heave = float(public_output_value(car, "front_heave_nmm", step2.front_heave_nmm))
    display_rear_heave = float(public_output_value(car, "rear_third_nmm", step2.rear_third_nmm))
    display_front_torsion = float(public_output_value(car, "front_torsion_od_mm", step3.front_torsion_od_mm))
    display_rear_torsion = float(public_output_value(car, "rear_spring_rate_nmm", step3.rear_spring_rate_nmm))

    brake_bias_str = f"{brake_bias_val:.1f}%" if brake_bias_val is not None else "(pipeline)"
    diff_preload_str = f"{diff_preload_val:.0f} Nm" if diff_preload_val is not None else "(pipeline)"
    if _is_ferrari and diff_coast_val is not None:
        # Ferrari uses "More Locking" / "Less Locking" labels, not degree values
        diff_coast_str = "More Locking" if diff_coast_val <= 45 else "Less Locking"
        diff_drive_str = "More Locking" if diff_drive_val is not None and diff_drive_val <= 70 else "Less Locking"
    else:
        diff_coast_str = f"{diff_coast_val} deg" if diff_coast_val is not None else "(pipeline)"
        diff_drive_str = f"{diff_drive_val} deg" if diff_drive_val is not None else "(pipeline)"
    diff_plates_str = f"{diff_plates_val}" if diff_plates_val is not None else "(pipeline)"
    tc_gain_str = f"{tc_gain_val}" if tc_gain_val is not None else "(pipeline)"
    tc_slip_str = f"{tc_slip_val}" if tc_slip_val is not None else "(pipeline)"
    brake_target_str = f"{brake_target_val:+.1f}" if brake_target_val is not None else "(pipeline)"
    brake_migration_str = f"{brake_migration_val:+.1f}" if brake_migration_val is not None else "(pipeline)"
    master_cyl_str = (
        f"{front_master_cyl_val:.1f}/{rear_master_cyl_val:.1f} mm"
        if front_master_cyl_val is not None and rear_master_cyl_val is not None
        else "(pipeline)"
    )
    pad_compound_str = pad_compound_val or "(pipeline)"
    if front_tb_turns_override is not None:
        _tb_turns = round(front_tb_turns_override, 3)
    elif garage_outputs is not None and getattr(garage_outputs, "torsion_bar_turns", 0) > 0:
        _tb_turns = round(float(garage_outputs.torsion_bar_turns), 3)
    else:
        _tb_turns = round(
            0.1089 - 0.1642 / max(step2.front_heave_nmm, 1) + 0.000368 * step2.perch_offset_front_mm, 3
        )
    # Rear torsion bar turns (Ferrari/Acura — passed through from IBT or computed)
    if rear_tb_turns_override is not None:
        _rear_tb_turns = round(rear_tb_turns_override, 3)
    elif garage_outputs is not None and getattr(garage_outputs, "rear_torsion_bar_turns", 0) != 0:
        _rear_tb_turns = round(float(garage_outputs.rear_torsion_bar_turns), 3)
    elif _has_rear_torsion:
        # Compute from same formula as front turns, but rear bars preload in the
        # opposite direction (negative turns). The formula is BMW-calibrated and
        # gives magnitude only — negate for rear.
        hsm = car.heave_spring
        _rear_tb_turns = -round(
            hsm.torsion_bar_turns_intercept + hsm.torsion_bar_turns_heave_coeff / max(step2.rear_third_nmm, 1), 3
        )
    else:
        _rear_tb_turns = None
    df_ok = abs(step1.df_balance_pct - target_balance) < 0.2
    stall_ok = step1.vortex_burst_margin_mm > 0
    garage_ok = getattr(step2, "garage_constraints_ok", True)

    # ── Header ────────────────────────────────────────────────────────
    a("═" * W)
    title = f"  {car_name}  ·  {track_name}  ·  Wing {wing}°"
    a(title)
    a(f"  Physics-based setup  ·  {now}")
    a("═" * W)
    a("")

    # ── CONFIDENCE & EVIDENCE ────────────────────────────────────────
    _car_slug = getattr(car, "canonical_name", car_name.lower().split()[0]) if car is not None else car_name.lower().split()[0]
    _tier_info = _load_support_tier(_car_slug, track_name)
    if _tier_info is not None:
        _tier = _tier_info.get("confidence_tier", "unknown")
        _samples = _tier_info.get("samples", 0)
        a(f"  Support: {_tier}  ·  {_samples} observations")
    else:
        a("  Support: unknown  ·  no validation evidence found")
    a("")

    # ── FULL PARAMETER SHEET ─────────────────────────────────────────
    a(_box_top("SETUP TO ENTER"))
    a(_full("  PLATFORM / SPRINGS"))
    a(_setting("Wing", f"{wing:.1f} deg", changed=_changed_marker("wing_angle_deg", wing, current_params)))
    a(_setting("Fuel load", f"{fuel_l:.0f} L"))
    a(_setting("Front static RH target", f"{step1.static_front_rh_mm:.1f} mm", changed=_changed_marker("front_rh_static", step1.static_front_rh_mm, current_params)))
    a(_setting("Rear static RH target", f"{step1.static_rear_rh_mm:.1f} mm", changed=_changed_marker("rear_rh_static", step1.static_rear_rh_mm, current_params)))
    a(_setting("Front pushrod", f"{step1.front_pushrod_offset_mm:+.1f} mm"))
    a(_setting("Rear pushrod", f"{step1.rear_pushrod_offset_mm:+.1f} mm"))
    if _is_ferrari:
        a(_setting("Front heave index", f"{display_front_heave:.0f} idx", changed=_changed_marker("front_heave_nmm", display_front_heave, current_params)))
        a(_setting("Front heave perch", f"{step2.perch_offset_front_mm:+.1f} mm"))
        a(_setting("Rear heave index", f"{display_rear_heave:.0f} idx", changed=_changed_marker("rear_third_nmm", display_rear_heave, current_params)))
        a(_setting("Rear heave perch", f"{step2.perch_offset_rear_mm:+.1f} mm"))
        a(_setting("Front torsion bar index", f"{display_front_torsion:.0f} idx", changed=_changed_marker("torsion_bar_od_mm", display_front_torsion, current_params)))
        a(_setting("Front torsion bar turns", f"{_tb_turns:.3f} turns"))
        a(_setting("Rear torsion bar index", f"{display_rear_torsion:.0f} idx", changed=_changed_marker("rear_spring_nmm", display_rear_torsion, current_params)))
        if _rear_tb_turns is not None:
            a(_setting("Rear torsion bar turns", f"{_rear_tb_turns:.3f} turns"))
    else:
        a(_setting("Front heave spring", f"{step2.front_heave_nmm:.0f} N/mm", changed=_changed_marker("front_heave_nmm", step2.front_heave_nmm, current_params)))
        a(_setting("Front heave perch", f"{step2.perch_offset_front_mm:+.1f} mm"))
        _rear_heave_label = "Rear heave spring" if _is_acura else "Rear third spring"
        _rear_perch_label = "Rear heave perch" if _is_acura else "Rear third perch"
        a(_setting(_rear_heave_label, f"{step2.rear_third_nmm:.0f} N/mm", changed=_changed_marker("rear_third_nmm", step2.rear_third_nmm, current_params)))
        a(_setting(_rear_perch_label, f"{step2.perch_offset_rear_mm:+.1f} mm"))
        _is_porsche = car is not None and getattr(car, "canonical_name", "") == "porsche"
        _no_front_torsion = (car is not None and hasattr(car, "corner_spring")
                             and getattr(car.corner_spring, "front_torsion_c", 1.0) == 0.0)
        if _no_front_torsion:
            # Porsche/Multimatic: front corner stiffness from roll spring, not torsion bar
            _roll_spring = getattr(step3, "front_roll_spring_nmm", None) or getattr(car.corner_spring, "front_roll_spring_rate_nmm", 0)
            a(_setting("Front roll spring", f"{_roll_spring:.0f} N/mm"))
        else:
            a(_setting("Front torsion bar OD", f"{step3.front_torsion_od_mm:.2f} mm", changed=_changed_marker("torsion_bar_od_mm", step3.front_torsion_od_mm, current_params)))
            a(_setting("Front torsion bar turns", f"{_tb_turns:.3f} turns"))
        if _has_rear_torsion and step3.rear_torsion_od_mm is not None:
            a(_setting("Rear torsion bar OD", f"{step3.rear_torsion_od_mm:.2f} mm", changed=_changed_marker("rear_spring_nmm", step3.rear_torsion_od_mm, current_params)))
            if _rear_tb_turns is not None:
                a(_setting("Rear torsion bar turns", f"{_rear_tb_turns:.3f} turns"))
        else:
            a(_setting("Rear coil spring", f"{step3.rear_spring_rate_nmm:.0f} N/mm", changed=_changed_marker("rear_spring_nmm", step3.rear_spring_rate_nmm, current_params)))
            a(_setting("Rear spring perch", f"{step3.rear_spring_perch_mm:.1f} mm"))
    if garage_outputs is not None:
        a(_setting(
            "Heave slider static",
            f"{garage_outputs.heave_slider_defl_static_mm:.1f} / {garage_outputs.heave_slider_defl_max_mm:.1f} mm",
        ))
        a(_setting(
            "Heave spring deflection",
            f"{garage_outputs.heave_spring_defl_static_mm:.1f} / {garage_outputs.heave_spring_defl_max_mm:.1f} mm",
        ))
        torsion_limit = (
            garage_model.effective_torsion_bar_defl_limit_mm()
            if garage_model is not None
            else None
        )
        torsion_limit_str = (
            f"{garage_outputs.torsion_bar_defl_mm:.1f} / {torsion_limit:.1f} mm"
            if torsion_limit is not None
            else f"{garage_outputs.torsion_bar_defl_mm:.1f} mm"
        )
        torsion_note = ""
        if (
            garage_model is not None
            and getattr(garage_model, "max_torsion_bar_defl_mm", None) is not None
            and torsion_limit is not None
            and torsion_limit < garage_model.max_torsion_bar_defl_mm - 1e-6
        ):
            torsion_note = f"[hard {garage_model.max_torsion_bar_defl_mm:.1f}]"
        a(_setting("Torsion bar deflection", torsion_limit_str, torsion_note))
    a(_blank())
    a(_full("  ARBS / GEOMETRY"))
    if step4 is not None:
        a(_setting("Front ARB size / blade", f"{step4.front_arb_size} / {step4.front_arb_blade_start}", changed=_changed_marker("front_arb_blade", step4.front_arb_blade_start, current_params)))
        a(_setting("Rear ARB size / blade", f"{step4.rear_arb_size} / {step4.rear_arb_blade_start}", changed=_changed_marker("rear_arb_blade", step4.rear_arb_blade_start, current_params)))
        a(_setting("Rear ARB live slow", f"blade {step4.rarb_blade_slow_corner}"))
        a(_setting("Rear ARB live fast", f"blade {step4.rarb_blade_fast_corner}"))
    else:
        a(_setting("ARBs", "(blocked — uncalibrated)"))
    if step5 is not None:
        _eff_front_camber = front_camber_override if front_camber_override is not None else step5.front_camber_deg
        _eff_rear_camber = rear_camber_override if rear_camber_override is not None else step5.rear_camber_deg
        a(_setting("Front camber", f"{_eff_front_camber:+.1f} deg", changed=_changed_marker("front_camber_deg", _eff_front_camber, current_params)))
        a(_setting("Rear camber", f"{_eff_rear_camber:+.1f} deg", changed=_changed_marker("rear_camber_deg", _eff_rear_camber, current_params)))
        a(_setting("Front toe", f"{step5.front_toe_mm:+.1f} mm", changed=_changed_marker("front_toe_mm", step5.front_toe_mm, current_params)))
        a(_setting("Rear toe", f"{step5.rear_toe_mm:+.1f} mm", changed=_changed_marker("rear_toe_mm", step5.rear_toe_mm, current_params)))
    else:
        # Defaults for blocked geometry — use car baselines or overrides
        _eff_front_camber = front_camber_override if front_camber_override is not None else -3.0
        _eff_rear_camber = rear_camber_override if rear_camber_override is not None else -2.0
        a(_setting("Geometry", "(blocked — uncalibrated)"))
    a(_blank())
    a(_full("  BRAKES / DIFF / TC / TYRES"))
    if _is_ferrari and hybrid_enabled is not None:
        _hybrid_status = "enabled" if hybrid_enabled else "DISABLED ← recommended"
        _hybrid_pct_str = f"  corner pct: {hybrid_corner_pct:.0f}%" if hybrid_corner_pct is not None else ""
        a(_setting("Hybrid rear drive", f"{_hybrid_status}{_hybrid_pct_str}"))
    a(_setting("Brake bias", brake_bias_str, changed=_changed_marker("brake_bias_pct", brake_bias_val, current_params) if brake_bias_val is not None else ""))
    a(_setting("Brake bias status", brake_bias_status))
    a(_setting("Brake target / migration", f"{brake_target_str} / {brake_migration_str}", f"[{brake_target_status}/{brake_migration_status}]"))
    if _is_ferrari and bias_migration_gain is not None:
        a(_setting("Bias migration gain", f"{bias_migration_gain}"))
    a(_setting("Master cyl F / R", master_cyl_str, f"[{master_cyl_status}]"))
    a(_setting("Pad compound", pad_compound_str, f"[{pad_compound_status}]"))
    if brake_hardware_status:
        a(_setting("Brake hardware status", brake_hardware_status))
    a(_setting("Diff preload", diff_preload_str, changed=_changed_marker("diff_preload_nm", diff_preload_val, current_params) if diff_preload_val is not None else ""))
    if _is_ferrari:
        # Ferrari: single coast/drive ramp label
        a(_setting("Diff coast/drive ramp", diff_coast_str))
        if front_diff_preload_nm is not None:
            a(_setting("Front diff preload", f"{front_diff_preload_nm:.0f} Nm"))
    else:
        a(_setting("Diff coast ramp", diff_coast_str))
        a(_setting("Diff drive ramp", diff_drive_str))
    a(_setting("Diff clutch plates", diff_plates_str, changed=_changed_marker("diff_clutch_plates", diff_plates_val, current_params) if diff_plates_val is not None else ""))
    if diff_solution is not None:
        a(_setting(
            "Diff lock coast / drive",
            f"{diff_solution.lock_pct_coast:.1f}% / {diff_solution.lock_pct_drive:.1f}%",
        ))
        a(_setting(
            "Diff preload / plate %",
            f"{diff_solution.preload_contribution_pct:.1f}% / {diff_solution.plate_contribution_pct:.1f}%",
        ))
    a(_setting("TC gain / slip", f"{tc_gain_str} / {tc_slip_str}", changed=_changed_marker("tc_gain", tc_gain_val, current_params) if tc_gain_val is not None else ""))
    a(_setting("Tyre cold FL / FR", f"{tyre_fl:.0f} / {tyre_fr:.0f} kPa"))
    a(_setting("Tyre cold RL / RR", f"{tyre_rl:.0f} / {tyre_rr:.0f} kPa"))
    a(_blank())
    a(_full("  DAMPERS"))
    if step6 is None:
        a(_setting("Dampers", "(blocked — uncalibrated)"))
    elif _has_roll_dampers:
        # ORECA heave+roll architecture: front/rear heave + roll dampers
        a(_setting("Front Heave LS comp / rbd", f"{step6.lf.ls_comp} / {step6.lf.ls_rbd} clicks"))
        a(_setting("Front Heave HS comp / rbd / slope", f"{step6.lf.hs_comp} / {step6.lf.hs_rbd} / {step6.lf.hs_slope}"))
        _roll_slope_f = getattr(step6, 'front_roll_hs_slope', None)
        _roll_slope_str = f" / {_roll_slope_f}" if _roll_slope_f is not None else ""
        a(_setting("Front Roll LS / HS" + (" / slope" if _roll_slope_f is not None else ""),
                   f"{step6.front_roll_ls or '-'} / {step6.front_roll_hs or '-'}{_roll_slope_str} clicks"))
        a(_setting("Rear Heave LS comp / rbd", f"{step6.lr.ls_comp} / {step6.lr.ls_rbd} clicks"))
        a(_setting("Rear Heave HS comp / rbd / slope", f"{step6.lr.hs_comp} / {step6.lr.hs_rbd} / {step6.lr.hs_slope}"))
        a(_setting("Rear Roll LS / HS", f"{step6.rear_roll_ls or '-'} / {step6.rear_roll_hs or '-'} clicks"))
        # Rear 3rd damper (Porsche only)
        _3rd_ls = getattr(step6, 'rear_3rd_ls_comp', None)
        _3rd_hs = getattr(step6, 'rear_3rd_hs_comp', None)
        _3rd_ls_rbd = getattr(step6, 'rear_3rd_ls_rbd', None)
        _3rd_hs_rbd = getattr(step6, 'rear_3rd_hs_rbd', None)
        if _3rd_ls is not None:
            a(_setting("Rear 3rd LS comp / rbd", f"{_3rd_ls} / {_3rd_ls_rbd} clicks"))
            a(_setting("Rear 3rd HS comp / rbd", f"{_3rd_hs} / {_3rd_hs_rbd} clicks"))
    else:
        for corner_name, corner in (
            ("LF", step6.lf),
            ("RF", step6.rf),
            ("LR", step6.lr),
            ("RR", step6.rr),
        ):
            a(_setting(f"{corner_name} LS comp / rbd", f"{corner.ls_comp} / {corner.ls_rbd} clicks"))
            a(_setting(f"{corner_name} HS comp / rbd / slope", f"{corner.hs_comp} / {corner.hs_rbd} / {corner.hs_slope}"))
    a(_blank())
    a(_full("  TARGETS / LIMITS"))
    a(_setting("DF balance", f"{step1.df_balance_pct:.2f}% (target {target_balance:.2f}%)", _ok(df_ok)))
    if step4 is not None:
        a(_setting("LLTD", f"{step4.lltd_achieved:.1%} (target {step4.lltd_target:.1%})"))
    else:
        a(_setting("LLTD", "(uncalibrated)"))
    a(_setting("Dynamic RH front / rear", f"{step1.dynamic_front_rh_mm:.1f} / {step1.dynamic_rear_rh_mm:.1f} mm"))
    a(_setting(
        "Heave travel margin",
        f"{(garage_outputs.travel_margin_front_mm if garage_outputs is not None else step2.travel_margin_front_mm):.1f} mm",
    ))
    a(_setting("Front bottoming margin", f"{step2.front_bottoming_margin_mm:.1f} mm"))
    a(_setting("Stall margin", f"{step1.vortex_burst_margin_mm:+.1f} mm", _ok(stall_ok)))
    if garage_outputs is not None:
        a(_setting(
            "Garage constraints",
            "OK" if garage_ok else "CHECK",
            "" if garage_ok else "; ".join(getattr(step2, "garage_constraint_notes", [])[:2]),
        ))
    a(_box_bot())
    a("")

    if compact:
        a(_box_top("VALIDATION SUMMARY"))
        a(_full(f"  DF bal: {step1.df_balance_pct:.2f}%  {_ok(df_ok)}    target {target_balance:.2f}%"))
        a(_full(f"  Front static RH: {step1.static_front_rh_mm:.1f} mm    Rear static RH: {step1.static_rear_rh_mm:.1f} mm"))
        if garage_outputs is not None:
            a(_full(
                f"  Heave slider: {garage_outputs.heave_slider_defl_static_mm:.1f}/{garage_outputs.heave_slider_defl_max_mm:.1f} mm"
                f"    Travel margin: {garage_outputs.travel_margin_front_mm:.1f} mm"
            ))
            torsion_limit = (
                garage_model.effective_torsion_bar_defl_limit_mm()
                if garage_model is not None
                else None
            )
            if torsion_limit is not None:
                torsion_line = (
                    f"  Torsion defl: {garage_outputs.torsion_bar_defl_mm:.1f}/{torsion_limit:.1f} mm"
                )
                if (
                    garage_model is not None
                    and getattr(garage_model, "max_torsion_bar_defl_mm", None) is not None
                    and torsion_limit < garage_model.max_torsion_bar_defl_mm - 1e-6
                ):
                    torsion_line += f"    hard {garage_model.max_torsion_bar_defl_mm:.1f} mm"
                a(_full(torsion_line))
        else:
            a(_full(
                f"  Heave slider: {step2.slider_static_front_mm:.1f} mm"
                f"    Travel margin: {step2.travel_margin_front_mm:.1f} mm"
            ))
        _lltd_str = f"LLTD: {step4.lltd_achieved:.1%}" if step4 is not None else "LLTD: [blocked]"
        a(_full(f"  Stall margin: {step1.vortex_burst_margin_mm:+.1f} mm  {_ok(stall_ok)}    {_lltd_str}"))
        a(_full(
            f"  Brake target/mig: {brake_target_str} / {brake_migration_str}    Master cyl: {master_cyl_str}"
        ))
        a(_full(
            f"  Brake semantics: bias={brake_bias_status}  target/mig={brake_target_status}/{brake_migration_status}"
        ))
        if brake_hardware_status:
            a(_full(f"  Brake hardware status: {brake_hardware_status}"))
        if diff_solution is not None:
            a(_full(
                f"  Diff lock coast/drive: {diff_solution.lock_pct_coast:.1f}% / {diff_solution.lock_pct_drive:.1f}%"
            ))
        if step4 is not None:
            a(_full(
                f"  RARB live: blade {step4.rarb_blade_slow_corner} slow  ->  blade {step4.rarb_blade_fast_corner} fast"
            ))
        for line in _rotation_search_lines(step3, step4, step5, supporting):
            a(_full(f"  {line}"))
        a(_box_bot())
        a("")
        a("═" * W)
        return "\n".join(lines)

    # ── GARAGE CARD ───────────────────────────────────────────────────
    a(_box_top("GARAGE CARD"))
    a(_blank())
    a(_row("  RIDE HEIGHTS & PUSHRODS", "  SPRINGS"))
    if _is_ferrari:
        a(_row(f"  Front static:  {step1.static_front_rh_mm:5.1f} mm",
               f"  Heave F:      {display_front_heave:3.0f} idx  perch {step2.perch_offset_front_mm:+.0f}mm"))
        a(_row(f"  Rear static:   {step1.static_rear_rh_mm:5.1f} mm",
               f"  Heave R:      {display_rear_heave:3.0f} idx  perch {step2.perch_offset_rear_mm:+.0f}mm"))
        a(_row(f"  Rake:          {step1.rake_static_mm:5.1f} mm",
               f"  F TB OD: {display_front_torsion:4.0f} idx  {_tb_turns:.3f} Turns"))
        a(_row(f"  Front pushrod: {step1.front_pushrod_offset_mm:5.1f} mm",
               f"  R TB OD: {display_rear_torsion:4.0f} idx"))
        a(_row(f"  Rear pushrod:  {step1.rear_pushrod_offset_mm:5.1f} mm",
               ""))
    else:
        a(_row(f"  Front static:  {step1.static_front_rh_mm:5.1f} mm",
               f"  Heave F:    {step2.front_heave_nmm:5.0f} N/mm  perch {step2.perch_offset_front_mm:+.0f}mm"))
        a(_row(f"  Rear static:   {step1.static_rear_rh_mm:5.1f} mm",
               f"  Third R:    {step2.rear_third_nmm:5.0f} N/mm  perch {step2.perch_offset_rear_mm:+.0f}mm"))
        if _no_front_torsion:
            a(_row(f"  Rake:          {step1.rake_static_mm:5.1f} mm",
                   f"  Roll spr:  {step3.front_roll_spring_nmm:5.0f} N/mm  {step3.front_natural_freq_hz:.2f}Hz"))
        else:
            a(_row(f"  Rake:          {step1.rake_static_mm:5.1f} mm",
                   f"  Torsion:   {step3.front_torsion_od_mm:6.2f} mm OD  {_tb_turns:.3f} Turns"))
        a(_row(f"  Front pushrod: {step1.front_pushrod_offset_mm:5.1f} mm",
               f"  Rear coil:  {step3.rear_spring_rate_nmm:5.0f} N/mm"))
        a(_row(f"  Rear pushrod:  {step1.rear_pushrod_offset_mm:5.1f} mm",
               f"  Rear perch:  {step3.rear_spring_perch_mm:5.1f} mm"))
    a(_blank())
    a(_row("  ANTI-ROLL BARS", "  WHEEL GEOMETRY"))
    if step4 is not None:
        a(_row(f"  FARB: {step4.front_arb_size:<6s} Blade {step4.front_arb_blade_start}  (locked)",
               f"  Front camber: {_eff_front_camber:+.1f}°"))
        live = f"[{step4.rarb_blade_slow_corner}→{step4.rarb_blade_fast_corner}]"
        a(_row(f"  RARB: {step4.rear_arb_size:<6s} Blade {step4.rear_arb_blade_start}  {live}",
               f"  Rear camber:  {_eff_rear_camber:+.1f}°"))
    else:
        a(_row("  FARB: [blocked — uncalibrated]",
               f"  Front camber: {_eff_front_camber:+.1f}°"))
        a(_row("  RARB: [blocked — uncalibrated]",
               f"  Rear camber:  {_eff_rear_camber:+.1f}°"))
    if step5 is not None:
        a(_row("",
               f"  Front toe:  {step5.front_toe_mm:+.1f} mm"))
        a(_row("",
               f"  Rear toe:   {step5.rear_toe_mm:+.1f} mm"))
    else:
        a(_row("",
               "  Front toe:  [blocked]"))
        a(_row("",
               "  Rear toe:   [blocked]"))
    a(_blank())
    # Diff & brakes row
    diff_str = ""
    bias_str = ""
    if supporting is not None:
        bias_str = f"  Brake bias: {supporting.brake_bias_pct:.1f}%"
        if _is_ferrari:
            _coast_lbl = "More" if supporting.diff_ramp_coast <= 45 else "Less"
            _drive_lbl = "More" if supporting.diff_ramp_drive <= 70 else "Less"
            diff_str = (f"  Diff: {supporting.diff_preload_nm:.0f} Nm  "
                        f"{_coast_lbl}/{_drive_lbl} Lock  "
                        f"{supporting.diff_clutch_plates}pl")
        else:
            diff_str = (f"  Diff: {supporting.diff_preload_nm:.0f} Nm  "
                        f"{supporting.diff_ramp_coast}°/{supporting.diff_ramp_drive}°  "
                        f"{supporting.diff_clutch_plates}pl")
    else:
        bias_str = f"  Brake bias: (see pipeline)"
        diff_str = ""

    a(_row("  BRAKES & DIFF", "  TYRES"))
    a(_row(bias_str,
           f"  Cold FL/FR: {tyre_fl:.0f}/{tyre_fr:.0f} kPa"))
    if diff_str:
        a(_row(diff_str, f"  Cold RL/RR: {tyre_rl:.0f}/{tyre_rr:.0f} kPa"))
    else:
        a(_row("", f"  Cold RL/RR: {tyre_rl:.0f}/{tyre_rr:.0f} kPa"))
    if brake_target_val is not None or brake_migration_val is not None:
        a(_row(
            f"  Target/Mig: {brake_target_str}/{brake_migration_str}",
            f"  Master cyl: {master_cyl_str}",
        ))
    if diff_solution is not None:
        a(_row(
            f"  Lock coast/drive: {diff_solution.lock_pct_coast:.1f}/{diff_solution.lock_pct_drive:.1f}%",
            f"  Pad: {pad_compound_str}",
        ))
        a(_row(
            f"  Lock preload/plate: {diff_solution.preload_contribution_pct:.1f}/{diff_solution.plate_contribution_pct:.1f}%",
            f"  Status: {brake_hardware_status[:18]}",
        ))
    a(_blank())

    # Dampers
    _lltd_line = f"  LLTD:   {step4.lltd_achieved:.1%}  (target {step4.lltd_target:.1%})" if step4 is not None else "  LLTD:   [blocked]"
    _camber_conf = step5.camber_confidence if step5 is not None else "blocked"
    _camber_line = f"  Camber: F{_eff_front_camber:+.1f}°  R{_eff_rear_camber:+.1f}°  [{_camber_conf}]"
    if step6 is None:
        a(_full("  DAMPERS (blocked — uncalibrated)     AERO STATUS"))
        a(_row("", f"  DF bal: {step1.df_balance_pct:.2f}%  {_ok(df_ok)}"))
        a(_row("", f"  L/D:    {step1.ld_ratio:.3f}"))
        a(_row("", f"  Stall:  {step1.vortex_burst_margin_mm:+.1f}mm  {_ok(stall_ok)}"))
        a(_row("", _lltd_line))
        a(_row("", f"  Dyn RH: F {step1.dynamic_front_rh_mm:.1f}  R {step1.dynamic_rear_rh_mm:.1f} mm"))
        a(_row("", _camber_line))
    elif _has_roll_dampers:
        a(_full("  DAMPERS (clicks)                     AERO STATUS"))
        a(_row(f"           FH   FR   RH   RR",
               f"  DF bal: {step1.df_balance_pct:.2f}%  {_ok(df_ok)}"))
        a(_row(f"  LS Comp: {step6.lf.ls_comp:3d}    -  {step6.lr.ls_comp:3d}    -",
               f"  L/D:    {step1.ld_ratio:.3f}"))
        a(_row(f"  LS Rbd:  {step6.lf.ls_rbd:3d}    -  {step6.lr.ls_rbd:3d}    -",
               f"  Stall:  {step1.vortex_burst_margin_mm:+.1f}mm  {_ok(stall_ok)}"))
        a(_row(f"  HS Comp: {step6.lf.hs_comp:3d}    -  {step6.lr.hs_comp:3d}    -",
               _lltd_line))
        a(_row(f"  Roll LS: {step6.front_roll_ls or '-':>3}    -  {step6.rear_roll_ls or '-':>3}    -",
               f"  Dyn RH: F {step1.dynamic_front_rh_mm:.1f}  R {step1.dynamic_rear_rh_mm:.1f} mm"))
        a(_row(f"  Roll HS: {step6.front_roll_hs or '-':>3}    -  {step6.rear_roll_hs or '-':>3}    -",
               _camber_line))
    else:
        a(_full("  DAMPERS (clicks)                     AERO STATUS"))
        a(_row(f"            LF   RF   LR   RR",
               f"  DF bal: {step1.df_balance_pct:.2f}%  {_ok(df_ok)}"))
        a(_row(f"  LS Comp: {step6.lf.ls_comp:3d}  {step6.rf.ls_comp:3d}  {step6.lr.ls_comp:3d}  {step6.rr.ls_comp:3d}",
               f"  L/D:    {step1.ld_ratio:.3f}"))
        a(_row(f"  LS Rbd:  {step6.lf.ls_rbd:3d}  {step6.rf.ls_rbd:3d}  {step6.lr.ls_rbd:3d}  {step6.rr.ls_rbd:3d}",
               f"  Stall:  {step1.vortex_burst_margin_mm:+.1f}mm  {_ok(stall_ok)}"))
        a(_row(f"  HS Comp: {step6.lf.hs_comp:3d}  {step6.rf.hs_comp:3d}  {step6.lr.hs_comp:3d}  {step6.rr.hs_comp:3d}",
               _lltd_line))
        a(_row(f"  HS Rbd:  {step6.lf.hs_rbd:3d}  {step6.rf.hs_rbd:3d}  {step6.lr.hs_rbd:3d}  {step6.rr.hs_rbd:3d}",
               f"  Dyn RH: F {step1.dynamic_front_rh_mm:.1f}  R {step1.dynamic_rear_rh_mm:.1f} mm"))
        a(_row(f"  HS Slope:{step6.lf.hs_slope:3d}  {step6.rf.hs_slope:3d}  {step6.lr.hs_slope:3d}  {step6.rr.hs_slope:3d}",
               _camber_line))
    a(_blank())
    a(_box_bot())
    a("")

    # ── TOP ACTIONS ───────────────────────────────────────────────────
    top_actions: list[tuple[str, str]] = []

    # Gather from sensitivity if available
    if sensitivity_result is not None:
        try:
            _PARAM_LABELS = {
                "rear_arb_blade":    "RARB blade",
                "torsion_bar_od_mm": "Torsion bar OD",
                "brake_bias_pct":    "Brake bias",
                "rear_camber_deg":   "Rear camber",
                "front_rh_mm":       "Front ride height",
                "rear_rh_mm":        "Rear ride height",
                "front_heave_nmm":   "Front heave spring",
            }
            for i, s in enumerate(sensitivity_result.top_n(3)):
                label = _PARAM_LABELS.get(s.parameter, s.parameter)
                impact = f"{abs(s.delta_per_unit_ms):.0f}ms/unit"
                desc = f"{label}: {s.current_value:.3g} {s.units}  ({s.mechanism[:40]})"
                top_actions.append((desc, impact))
        except Exception:
            pass

    # Always add RARB live strategy (if step4 is available)
    if step4 is not None:
        live_action = (
            f"RARB live: blade {step4.rear_arb_blade_start} start  →  "
            f"{step4.rarb_blade_slow_corner} slow / {step4.rarb_blade_fast_corner} fast",
            "live adjustment"
        )
        if not any("RARB" in a[0] or "arb" in a[0].lower() for a in top_actions):
            top_actions.insert(0, live_action)

    # Add stint recommendation
    if stint_result is not None:
        try:
            bias = stint_result.setup_bias
            if bias and bias != "balanced":
                us_end = stint_result.balance_curve.understeer_deg[-1]
                stint_action = (
                    f"Stint ({bias.upper()}): start RARB {stint_result.degradation.preemptive_rarb_offset:+d} blade  "
                    f"→ +{abs(us_end):.1f}° US drift by lap {stint_result.balance_curve.lap_numbers[-1]}",
                    "endurance"
                )
                top_actions.append(stint_action)
        except Exception:
            pass

    # Add sector recommendation
    if sector_result is not None:
        try:
            sc = sector_result.slow_sector_time_pct
            fc = sector_result.fast_sector_time_pct
            dominant = "fast" if fc > sc else "slow"
            pct = max(sc, fc)
            sector_action = (
                f"Sector: {pct:.0f}% {dominant}-corner → "
                + (f"aero platform priority (heave, RH stability)"
                   if dominant == "fast"
                   else f"mechanical grip priority (RARB, diff)"),
                "track-specific"
            )
            top_actions.append(sector_action)
        except Exception:
            pass

    if top_actions:
        a(_box_top("TOP ACTIONS"))
        for i, (action, impact) in enumerate(top_actions[:6], 1):
            impact_str = f"[{impact}]"
            # Wrap long action text
            max_action = W - 4 - len(impact_str) - 2
            if len(action) > max_action:
                action = action[:max_action - 1] + "…"
            row_str = f"  {i}. {action}"
            a("│" + row_str.ljust(W - 4 - len(impact_str)) + f"  {impact_str}" + "│")
        a(_box_bot())
        a("")

    # ── CURRENT vs RECOMMENDED COMPARISON ────────────────────────────
    # (populated by pipeline when current setup is available)
    # This section is conditionally added by the pipeline path.

    # ── STINT CARD ───────────────────────────────────────────────────
    if stint_result is not None:
        try:
            bc = stint_result.balance_curve
            deg = stint_result.degradation
            a(_box_top("STINT CARD"))
            a(_full("  Balance evolution:"))
            # Compact 5-point table
            step = max(1, len(bc.lap_numbers) // 5)
            sample_idxs = list(range(0, len(bc.lap_numbers), step))
            if (len(bc.lap_numbers) - 1) not in sample_idxs:
                sample_idxs.append(len(bc.lap_numbers) - 1)
            a(_full(f"  {'Lap':>5}  {'US drift':>10}  {'RARB rec':>10}  Note"))
            a(_full("  " + "─" * (W - 4)))
            for idx in sample_idxs[:6]:
                lap = bc.lap_numbers[idx]
                us = bc.understeer_deg[idx]
                rarb = bc.rarb_recommendation[idx] if bc.rarb_recommendation else "—"
                note = "← go softer" if us > 0.8 and idx > 0 else ""
                a(_full(f"  {lap:>5}   {us:>+7.1f}°   blade {rarb:<3}   {note}"))
            a(_blank())
            # Pushrod schedule
            a(_full(f"  Pushrod correction: +{abs(stint_result.conditions[-1].fuel_state.pushrod_correction_mm):.1f}mm over stint"))
            if len(stint_result.conditions) > 1:
                mid = stint_result.conditions[len(stint_result.conditions)//2]
                last = stint_result.conditions[-1]
                a(_full(f"  Lap ~{mid.lap_number}: +{abs(mid.fuel_state.pushrod_correction_mm):.1f}mm   "
                        f"Lap ~{last.lap_number}: +{abs(last.fuel_state.pushrod_correction_mm):.1f}mm"))
            a(_full(f"  Tyre pressure rise: +{deg.pressure_rise_per_10_laps_kpa:.0f} kPa/10 laps"))
            a(_box_bot())
            a("")
        except Exception:
            pass

    # ── LAP TIME SENSITIVITY ──────────────────────────────────────────
    if sensitivity_result is not None:
        try:
            a(_box_top("LAP TIME SENSITIVITY"))
            a(_full(f"  {'#':<3}  {'Parameter':<24}  {'Current':>8}  {'Impact':>12}  {'Conf'}"))
            a(_full("  " + "─" * (W - 4)))
            for i, s in enumerate(sensitivity_result.sensitivities, 1):  # ALL parameters
                sign = "+" if s.delta_per_unit_ms > 0 else ""
                impact = f"{sign}{s.delta_per_unit_ms:.0f}ms/unit"
                a(_full(f"  {i:<3}  {s.parameter:<24}  {s.current_value:>8.2g}  {impact:>12}  {s.confidence}"))
            a(_blank())
            try:
                top = sensitivity_result.top_n(1)[0]
                a(_full(f"  Biggest lever: {top.parameter} ({abs(top.delta_per_unit_ms):.0f}ms/unit)"))
            except Exception:
                pass
            a(_box_bot())
            a("")
        except Exception:
            pass

    # ── SECTOR COMPROMISE ─────────────────────────────────────────────
    if sector_result is not None:
        try:
            a(_box_top("SECTOR COMPROMISE"))
            sc_pct = sector_result.slow_sector_time_pct
            mc_pct = sector_result.medium_sector_time_pct
            fc_pct = sector_result.fast_sector_time_pct
            a(_full(f"  Track split:  slow {sc_pct:.0f}%  ·  medium {mc_pct:.0f}%  ·  fast {fc_pct:.0f}%"))
            a(_blank())
            a(_full(f"  {'Parameter':<18}  {'Slow best':>10}  {'Fast best':>10}  {'Compromise':>12}  Cost"))
            a(_full("  " + "─" * (W - 4)))
            total_cost = 0.0
            for pc in sector_result.parameter_conflicts:
                cost_ms = getattr(pc, "time_cost_ms", 0.0)
                total_cost += cost_ms
                a(_full(f"  {pc.parameter:<18}  {pc.slow_optimal:>10}  {pc.fast_optimal:>10}  {pc.compromise:>12}  {cost_ms:.0f}ms"))
                note = getattr(pc, "note", "")
                if note:
                    # Wrap note across multiple lines at word boundary
                    max_note = W - 8
                    while note:
                        chunk = note[:max_note]
                        if len(note) > max_note:
                            # break at last space
                            bp = chunk.rfind(" ")
                            if bp > 0:
                                chunk = chunk[:bp]
                        a(_full(f"    ↳ {chunk}"))
                        note = note[len(chunk):].lstrip()
            a(_blank())
            a(_full(f"  Total compromise cost: ~{total_cost:.0f}ms  ·  use live RARB to recover"))
            # Recommendations
            recs = getattr(sector_result, "compromise_recommendations", [])
            for rec in recs[:3]:
                a(_full(f"  → {rec[:W-6]}"))
            a(_box_bot())
            a("")
        except Exception:
            pass

    # ── SETUP SPACE ───────────────────────────────────────────────────
    if space_result is not None:
        try:
            a(_box_top("SETUP SPACE  (feasible range · flat bottom = <100ms of optimal)"))
            a(_full(f"  {'Parameter':<22}  {'Optimal':>8}  {'Min':>8}  {'Max':>8}  {'FlatW':>6}  Robust"))
            a(_full("  " + "─" * (W - 4)))
            for pr in space_result.parameter_ranges:
                fw = pr.flat_bottom_max - pr.flat_bottom_min
                a(_full(f"  {pr.parameter:<22}  {pr.optimal:>8.2g}  {pr.feasible_min:>8.2g}  "
                        f"{pr.feasible_max:>8.2g}  {fw:>6.2g}  {pr.robustness}"))
            a(_blank())
            try:
                a(_full(f"  Nail this: {space_result.tightest_constraint}  (smallest feasible range)"))
                a(_full(f"  Latitude:  {space_result.most_robust_parameter}  (wide flat bottom — adjust on track)"))
            except Exception:
                pass
            a(_box_bot())
            a("")
        except Exception:
            pass

    # ── BALANCE & PLATFORM CHECKS ─────────────────────────────────────
    a(_hdr("BALANCE & PLATFORM"))
    if step4 is not None:
        a(f"  LLTD achieved: {step4.lltd_achieved:.1%}  target: {step4.lltd_target:.1%}  "
          f"RARB sensitivity: {step4.rarb_sensitivity_per_blade:+.1%}/blade")
        a(f"  RARB 1→{step4.rarb_blade_fast_corner} range: {step4.lltd_at_rarb_min:.1%}→{step4.lltd_at_rarb_max:.1%}")
    else:
        a("  LLTD: [blocked — ARB uncalibrated]")
    if _is_ferrari:
        a(f"  Heave: {display_front_heave:.0f} idx  (bottom margin: {step2.front_bottoming_margin_mm:.1f}mm)")
    else:
        a(f"  Heave: {step2.front_heave_nmm:.0f} N/mm  (bottom margin: {step2.front_bottoming_margin_mm:.1f}mm)")
    if garage_outputs is not None:
        a(f"  Heave slider: {garage_outputs.heave_slider_defl_static_mm:.1f}/{garage_outputs.heave_slider_defl_max_mm:.1f} mm  "
          f"travel margin: {garage_outputs.travel_margin_front_mm:.1f} mm")
    if _is_ferrari:
        a(f"  F TB OD: {display_front_torsion:.0f} idx  {step3.front_natural_freq_hz:.2f}Hz  "
          f"heave/corner: {step3.front_heave_corner_ratio:.1f}x")
        a(f"  R TB OD: {display_rear_torsion:.0f} idx  {step3.rear_natural_freq_hz:.2f}Hz  "
          f"third/corner: {step3.rear_third_corner_ratio:.1f}x")
    elif _no_front_torsion:
        a(f"  Roll spr: {step3.front_roll_spring_nmm:.0f} N/mm  {step3.front_natural_freq_hz:.2f}Hz  "
          f"heave/corner: {step3.front_heave_corner_ratio:.1f}x")
        a(f"  Rear coil: {step3.rear_spring_rate_nmm:.0f} N/mm  {step3.rear_natural_freq_hz:.2f}Hz  "
          f"third/corner: {step3.rear_third_corner_ratio:.1f}x")
    else:
        a(f"  Torsion: {step3.front_torsion_od_mm:.2f}mm OD  {step3.front_natural_freq_hz:.2f}Hz  "
          f"heave/corner: {step3.front_heave_corner_ratio:.1f}x")
        a(f"  Rear coil: {step3.rear_spring_rate_nmm:.0f} N/mm  {step3.rear_natural_freq_hz:.2f}Hz  "
          f"third/corner: {step3.rear_third_corner_ratio:.1f}x")
    if step5 is not None:
        a(f"  Roll at peak {step5.peak_lat_g:.2f}g: {step5.body_roll_at_peak_deg:.1f}°  "
          f"Fcamber dynamic: {step5.front_dynamic_camber_at_peak_deg:+.2f}°  [{step5.camber_confidence}]")
        a(f"  Tyres to op temp: fronts ~{step5.expected_conditioning_laps_front:.0f} laps  "
          f"rears ~{step5.expected_conditioning_laps_rear:.0f} laps")
    else:
        a("  Roll / camber / conditioning: [blocked — geometry uncalibrated]")
    a("")

    # ── PARAMETER COUPLING (Unit C1) ──────────────────────────────────
    if coupling_changes:
        def _coup_get(ch: Any, key: str, default: Any = None) -> Any:
            return ch.get(key, default) if isinstance(ch, dict) else getattr(ch, key, default)

        def _coup_fmt(v: Any) -> str:
            if v is None:
                return "—"
            if isinstance(v, tuple):
                return "[" + ", ".join(str(x) for x in v) + "]"
            if isinstance(v, float):
                return f"{v:.2f}"
            return str(v)

        a(_hdr(f"PARAMETER COUPLING ({len(coupling_changes)} cascading adjustments)"))
        for ch in coupling_changes:
            param = _coup_get(ch, "param", "?")
            old = _coup_get(ch, "old")
            new = _coup_get(ch, "new")
            rationale = _coup_get(ch, "rationale", "")
            a(f"  {param}: {_coup_fmt(old)} → {_coup_fmt(new)}")
            if rationale:
                a(f"      {rationale}")
        a("")

    # ── VALIDATION CHECKLIST ──────────────────────────────────────────
    a(_hdr("VALIDATION CHECKLIST"))
    a("  [ ] 5 laps minimum before judging — tyres need conditioning")
    a("  [ ] Check IBT ride heights vs Step 1 targets (dyn vs static)")
    a(f"  [ ] Stall margin {step1.vortex_burst_margin_mm:.1f}mm — watch if RH drops on bumps")
    a("  [ ] Shock vel p99 >800mm/s → stiffen HS comp +1 click")
    a("  [ ] Tyre temp spread (inner-outer) → flag for camber calibration")
    if step4 is not None:
        a(f"  [ ] RARB live: blade {step4.rarb_blade_slow_corner} (slow) ↔ blade {step4.rarb_blade_fast_corner} (fast)")
    else:
        a("  [ ] RARB live: [calibrate ARBs first]")
    a("  [ ] Do NOT touch dampers until Steps 1-5 validated")
    a("")
    a("  Starting points only. Validate every step on track.")
    a("═" * W)

    return "\n".join(lines)


def print_comparison_table(
    current_setup: Any,
    recommended: dict[str, tuple[float, str]],
) -> str:
    """Print a side-by-side current vs recommended comparison table.

    Args:
        current_setup: CurrentSetup object with current garage values
        recommended: {param: (recommended_value, units)} dict
    """
    lines: list[str] = []
    a = lines.append

    a(_box_top("CURRENT vs RECOMMENDED"))
    a(_full(f"  {'Parameter':<24}  {'Current':>10}  {'Recommended':>12}  {'Change':>10}"))
    a(_full("  " + "─" * (W - 4)))

    # Detect Ferrari for label/unit adjustments
    _is_ferrari = hasattr(current_setup, "source") and getattr(current_setup, "_car_name", "") == "ferrari"
    _heave_unit = "idx" if _is_ferrari else "N/mm"
    _tb_unit = "idx" if _is_ferrari else "mm"
    _rear_spring_label = (
        "Rear torsion bar index" if _is_ferrari else "Rear torsion bar OD"
    ) if (_is_ferrari or _has_rear_torsion) else "Rear coil spring"
    _rear_heave_label = "Rear heave index" if _is_ferrari else ("Rear heave spring" if _is_acura else "Rear third spring")

    param_map = {
        "front_rh_mm":          ("Front static RH",    current_setup.front_rh_static_mm,    "mm"),
        "rear_rh_mm":           ("Rear static RH",     current_setup.rear_rh_static_mm,     "mm"),
        "front_heave_nmm":      (("Front heave index" if _is_ferrari else "Front heave spring"), current_setup.front_heave_nmm, _heave_unit),
        "rear_third_nmm":       (_rear_heave_label,    current_setup.rear_third_nmm,        _heave_unit),
        "torsion_bar_od_mm":    (("Front torsion bar index" if _is_ferrari else "Front torsion bar OD"), current_setup.front_torsion_od_mm, _tb_unit),
        "rear_spring_nmm":      (_rear_spring_label,   current_setup.rear_spring_rate_nmm,  _heave_unit),
        "rear_arb_blade":       ("RARB blade",         current_setup.rear_arb_blade_start,  "blade"),
        "front_camber_deg":     ("Front camber",       current_setup.front_camber_deg,      "°"),
        "rear_camber_deg":      ("Rear camber",        current_setup.rear_camber_deg,       "°"),
        "brake_bias_pct":       ("Brake bias",         current_setup.brake_bias_pct,        "%"),
    }

    any_change = False
    for param, (label, cur_val, units) in param_map.items():
        if param not in recommended or cur_val is None or cur_val == 0:
            continue
        rec_val, _ = recommended[param]
        delta = rec_val - cur_val
        if abs(delta) < 0.05:
            continue
        any_change = True
        arrow = "↑" if delta > 0 else "↓"
        change_str = f"{delta:+.1f} {units} {arrow}"
        a(_full(f"  {label:<24}  {cur_val:>10.2g}  {rec_val:>12.2g}  {change_str:>10}"))

    if not any_change:
        a(_full("  No significant changes from current setup."))

    a(_box_bot())
    return "\n".join(lines)


def save_json_summary(
    car_name: str,
    track_name: str,
    wing: float,
    step1: RakeSolution,
    step2: HeaveSolution,
    step3: CornerSpringSolution,
    step4: ARBSolution,
    step5: WheelGeometrySolution,
    step6: DamperSolution,
    output_path: str | Path,
) -> None:
    """Save all solver outputs as structured JSON."""
    summary = {
        "meta": {
            "car": car_name,
            "track": track_name,
            "wing": wing,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "step1_rake": to_public_output_payload(car_name, step1),
        "step2_heave": to_public_output_payload(car_name, step2),
        "step3_corner": to_public_output_payload(car_name, step3),
        "step4_arb": to_public_output_payload(car_name, step4),
        "step5_geometry": to_public_output_payload(car_name, step5),
        "step6_dampers": to_public_output_payload(car_name, step6),
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(summary, indent=2))
