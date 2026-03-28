"""RunTrace — runtime transparency for the iOptimal solver pipeline.

Captures and formats a complete data-provenance report showing:
  • Which telemetry channels drove each solver step
  • Which solver path was selected and why
  • Full objective score breakdown for the final candidate
  • Signal quality summary (direct / proxy / missing)
  • Car/track support tier with explicit limitations
  • Calibration status for the objective function
  • Legality validation tier and messages

Usage:
    trace = RunTrace()
    trace.record_car_track(car.canonical_name, track.track_name)
    trace.record_signals(measured)
    trace.record_solver_path("optimizer", reason="BMW/Sebring garage model active")
    trace.record_step(1, step1)
    trace.record_legality(legal_validation)
    trace.record_objective(breakdown, score_ms, "ObjectiveFunction")
    trace.print_report(verbose=True)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


# ─── Signal → step mapping ────────────────────────────────────────────────────

_SIGNAL_TO_STEPS: dict[str, list[int]] = {
    "mean_front_rh_at_speed_mm": [1, 2],
    "mean_rear_rh_at_speed_mm": [1, 2],
    "front_rh_std_mm": [1, 2],
    "rear_rh_std_mm": [1, 2],
    "splitter_rh_p01_mm": [1, 2],
    "front_heave_travel_used_pct": [2],
    "rear_heave_travel_used_pct": [2],
    "bottoming_event_count_front_clean": [2],
    "bottoming_event_count_rear_clean": [2],
    "front_shock_oscillation_hz": [6],
    "rear_shock_oscillation_hz": [6],
    "understeer_mean_deg": [4],
    "understeer_low_speed_deg": [4],
    "understeer_high_speed_deg": [4],
    "lltd_measured": [4],
    "body_slip_p95_deg": [4],
    "body_roll_p95_deg": [4],
    "front_braking_lock_ratio_p95": [7],
    "rear_power_slip_ratio_p95": [7],
    "front_carcass_mean_c": [5],
    "rear_carcass_mean_c": [5],
    "front_pressure_mean_kpa": [7],
    "rear_pressure_mean_kpa": [7],
    "pitch_range_braking_deg": [6],
    "hydraulic_brake_split_confidence": [7],
}

_STEP_SIGNAL_DRIVERS: dict[int, list[str]] = {
    1: ["mean_front_rh_at_speed_mm", "mean_rear_rh_at_speed_mm", "front_rh_std_mm", "splitter_rh_p01_mm"],
    2: ["front_heave_travel_used_pct", "front_rh_std_mm", "bottoming_event_count_front_clean"],
    3: ["front_heave_spring_nmm (step2)", "rear_third_nmm (step2)"],
    4: ["lltd_measured", "understeer_mean_deg", "body_slip_p95_deg", "body_roll_p95_deg"],
    5: ["front_camber_deg (current setup)", "rear_camber_deg (current)", "front_carcass_mean_c"],
    6: ["shock_vel_p99 (track profile)", "wheel_rates (step3)", "front_shock_oscillation_hz"],
    7: ["front_braking_lock_ratio_p95", "rear_power_slip_ratio_p95", "front_pressure_mean_kpa"],
}

_STEP_NAMES: dict[int, str] = {
    1: "Rake / Ride Heights",
    2: "Heave / Third Springs",
    3: "Corner Springs / Torsion Bar",
    4: "ARBs / LLTD Balance",
    5: "Wheel Geometry (Camber / Toe)",
    6: "Dampers",
    7: "Supporting (Brake / Diff / TC / Fuel)",
}

# Car support tier descriptors
_SUPPORT_TIERS: dict[str, str] = {
    "bmw":     "calibrated  — 73 IBT sessions, garage model, k-NN, heave cal",
    "ferrari": "partial     — 9 sessions, spring passthrough (springs NOT solved)",
    "cadillac":"exploratory — 4 sessions, sequential solver only",
    "porsche": "exploratory — 2 sessions, sequential solver only",
    "acura":   "unsupported — <1 session, all terms at physics defaults",
}


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class SignalRecord:
    name: str
    value: Any
    quality: str          # "trusted" | "proxy" | "unknown" | "missing"
    confidence: float
    source: str
    affects_steps: list[int]
    fallback_used: bool = False


@dataclass
class SolverStepRecord:
    step: int
    name: str
    key_outputs: dict[str, Any]
    driven_by_signals: list[str]
    physics_override: bool = False  # True if passthrough (Ferrari indexed params)
    notes: list[str] = field(default_factory=list)


# ─── RunTrace ─────────────────────────────────────────────────────────────────

@dataclass
class RunTrace:
    """Full data-provenance record for one iOptimal solver run."""

    car_name: str = ""
    track_name: str = ""
    wing_angle: float | None = None
    car_support_tier: str = ""

    solver_path: str = ""        # "sequential" | "optimizer" | "grid_search" | "legal_search"
    solver_path_reason: str = ""
    search_mode: str | None = None

    signals: list[SignalRecord] = field(default_factory=list)
    solver_steps: list[SolverStepRecord] = field(default_factory=list)

    objective_breakdown: Any = None
    objective_score_ms: float | None = None
    objective_scoring_system: str = ""

    legality_tier: str = "none"
    legality_valid: bool = True
    legality_messages: list[str] = field(default_factory=list)
    legality_warnings: list[str] = field(default_factory=list)

    candidate_family: str | None = None
    candidate_score: float | None = None

    solve_notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    calibration_spearman: float | None = None
    calibration_auto_apply: bool = False
    calibration_manual_review: bool = False
    calibration_available: bool = False

    # ── Recording API ─────────────────────────────────────────────────────────

    def record_car_track(self, car_name: str, track_name: str, wing_angle: float | None = None) -> None:
        self.car_name = car_name.lower()
        self.track_name = track_name
        self.wing_angle = wing_angle
        self.car_support_tier = _SUPPORT_TIERS.get(self.car_name, "unknown — no data")

    def record_signals(self, measured: Any) -> None:
        """Extract signal quality map from MeasuredState."""
        try:
            from analyzer.telemetry_truth import build_signal_map
            signal_map = build_signal_map(measured)
        except Exception:
            signal_map = getattr(measured, "telemetry_signals", {}) or {}
        for name, sig in signal_map.items():
            if sig is None:
                continue
            self.signals.append(SignalRecord(
                name=name,
                value=getattr(sig, "value", None),
                quality=getattr(sig, "quality", "unknown"),
                confidence=float(getattr(sig, "confidence", 0.0) or 0.0),
                source=getattr(sig, "source", ""),
                affects_steps=_SIGNAL_TO_STEPS.get(name, []),
                fallback_used=bool(getattr(sig, "fallback_used", False)),
            ))

    def record_solver_path(self, path: str, reason: str = "") -> None:
        self.solver_path = path
        self.solver_path_reason = reason

    def record_step(
        self,
        step: int,
        step_obj: Any,
        *,
        physics_override: bool = False,
        notes: list[str] | None = None,
    ) -> None:
        key_outputs = _extract_step_key_outputs(step, step_obj)
        self.solver_steps.append(SolverStepRecord(
            step=step,
            name=_STEP_NAMES.get(step, f"Step {step}"),
            key_outputs=key_outputs,
            driven_by_signals=_STEP_SIGNAL_DRIVERS.get(step, []),
            physics_override=physics_override,
            notes=list(notes or []),
        ))

    def record_objective(
        self,
        breakdown: Any,
        score_ms: float,
        scoring_system: str = "ObjectiveFunction",
    ) -> None:
        self.objective_breakdown = breakdown
        self.objective_score_ms = score_ms
        self.objective_scoring_system = scoring_system

    def record_legality(self, legal: Any) -> None:
        self.legality_tier = getattr(legal, "validation_tier", "unknown")
        self.legality_valid = bool(getattr(legal, "valid", True))
        self.legality_messages = list(getattr(legal, "messages", []) or [])
        self.legality_warnings = list(getattr(legal, "warnings", []) or [])

    def record_calibration(self) -> None:
        """Read current objective calibration status from validation report."""
        try:
            from pathlib import Path
            report_path = Path(__file__).parent.parent / "validation" / "calibration_report.md"
            if not report_path.exists():
                return
            content = report_path.read_text(encoding="utf-8", errors="replace")
            self.calibration_available = True
            self.calibration_auto_apply = "Auto-apply: `True`" in content
            self.calibration_manual_review = "Manual review recommended: `True`" in content
            # Extract Spearman from "Track Aware" section
            import re
            m = re.search(r"Track Aware.*?Spearman.*?`([+-]?\d+\.\d+)`", content, re.DOTALL)
            if m:
                self.calibration_spearman = float(m.group(1))
        except Exception:
            pass

    def add_note(self, note: str) -> None:
        self.solve_notes.append(note)

    def add_warning(self, warning: str) -> None:
        self.warnings.append(warning)

    # ── Rendering ─────────────────────────────────────────────────────────────

    def print_report(self, verbose: bool = True) -> None:
        W = 74
        _dline = "═" * W
        _line  = "─" * W

        print(f"\n{_dline}")
        car_disp = self.car_name.upper() if self.car_name else "UNKNOWN"
        wing_str = f"  wing={self.wing_angle}°" if self.wing_angle is not None else ""
        print(f"  ◈  iOPTIMAL  RUN TRACE  —  {car_disp} @ {self.track_name}{wing_str}")
        print(f"{_dline}")

        # ── Support tier ──────────────────────────────────────────────────────
        tier_str = self.car_support_tier or "unknown"
        calib_icon = "✅" if "calibrated" in tier_str else ("⚠️ " if "exploratory" in tier_str or "partial" in tier_str else "❌")
        print(f"  {calib_icon} CAR SUPPORT   {tier_str}")

        # ── Solver path ───────────────────────────────────────────────────────
        path_icons = {
            "sequential": "⛓ ",
            "optimizer": "⚙️ ",
            "grid_search": "🔍",
            "legal_search": "🔍",
            "sequential_fallback": "⛓ ",
        }
        p_icon = path_icons.get(self.solver_path, "● ")
        print(f"  {p_icon} SOLVER PATH   {self.solver_path or 'unknown'}")
        if self.solver_path_reason:
            print(f"       reason:    {self.solver_path_reason}")
        if self.search_mode:
            print(f"       search:    {self.search_mode}")
        if self.candidate_family:
            score_str = f"  score={self.candidate_score:+.1f}ms" if self.candidate_score is not None else ""
            print(f"  🎯 CANDIDATE    {self.candidate_family}{score_str}")

        print(f"  {_line}")

        # ── Telemetry signals ─────────────────────────────────────────────────
        trusted  = [s for s in self.signals if s.quality == "trusted"]
        proxy    = [s for s in self.signals if s.quality == "proxy"]
        missing  = [s for s in self.signals if s.quality in ("unknown", "missing")]
        fallbacks = [s for s in self.signals if s.fallback_used]

        print(f"  📡 TELEMETRY    {len(self.signals)} channels read")
        print(f"     direct(trusted): {len(trusted):3d}  |  proxy: {len(proxy):3d}  |  missing: {len(missing):3d}  |  fallbacks: {len(fallbacks):3d}")

        if verbose and self.signals:
            KEY_SIGNALS = [
                "mean_front_rh_at_speed_mm", "mean_rear_rh_at_speed_mm",
                "front_rh_std_mm", "rear_rh_std_mm",
                "front_heave_travel_used_pct", "rear_heave_travel_used_pct",
                "front_braking_lock_ratio_p95", "rear_power_slip_ratio_p95",
                "understeer_mean_deg", "understeer_high_speed_deg",
                "lltd_measured", "body_slip_p95_deg",
                "front_carcass_mean_c", "rear_carcass_mean_c",
                "front_pressure_mean_kpa", "rear_pressure_mean_kpa",
            ]
            sig_map = {s.name: s for s in self.signals}
            printed = False
            for name in KEY_SIGNALS:
                sig = sig_map.get(name)
                if sig is None:
                    continue
                q_icon = "✓" if sig.quality == "trusted" else ("~" if sig.quality == "proxy" else "✗")
                fb_str = " [fallback]" if sig.fallback_used else ""
                try:
                    val_str = f"{float(sig.value):8.3f}" if sig.value is not None else "    None"
                except (TypeError, ValueError):
                    val_str = f"{str(sig.value):>8s}"
                conf_str = f"conf={sig.confidence:.0%}"
                print(f"     {q_icon} {name:<42s} {val_str}  {conf_str}{fb_str}")
                printed = True
            if not printed:
                print("     (no key signals matched — run with IBT for full telemetry)")

        print(f"  {_line}")

        # ── Solver steps ──────────────────────────────────────────────────────
        print(f"  ⛓  SOLVER STEPS")
        for rec in self.solver_steps:
            ov_str = "  ⚠️  PASSTHROUGH — not solver-optimized" if rec.physics_override else ""
            print(f"     Step {rec.step}: {rec.name}{ov_str}")
            for k, v in rec.key_outputs.items():
                try:
                    v_str = f"{float(v):.2f}" if isinstance(v, (int, float)) else str(v)
                except (TypeError, ValueError):
                    v_str = str(v)
                print(f"              {k:<38s} = {v_str}")
            if rec.driven_by_signals:
                drivers = ", ".join(rec.driven_by_signals[:4])
                print(f"              ← data from: {drivers}")
            for note in rec.notes:
                print(f"              ⚑ {note}")

        print(f"  {_line}")

        # ── Objective breakdown ───────────────────────────────────────────────
        if self.objective_score_ms is not None:
            print(f"  📊 OBJECTIVE    [{self.objective_scoring_system}]   total = {self.objective_score_ms:+.1f} ms")
            bd = self.objective_breakdown
            if bd is not None:
                _pt("  lap_gain_ms",          getattr(bd, "lap_gain_ms", None))
                pr = getattr(bd, "platform_risk", None)
                _pt("  platform_risk_ms",     getattr(pr, "total_ms", None),  neg=True)
                if pr is not None and verbose:
                    _pt("    bottoming_risk",   getattr(pr, "bottoming_risk_ms", None), neg=True, indent=6)
                    _pt("    vortex_risk",      getattr(pr, "vortex_risk_ms", None),    neg=True, indent=6)
                    _pt("    rh_collapse_risk", getattr(pr, "rh_collapse_risk_ms", None), neg=True, indent=6)
                dm = getattr(bd, "driver_mismatch", None)
                _pt("  driver_mismatch_ms",   getattr(dm, "total_ms", None), neg=True)
                tu = getattr(bd, "telemetry_uncertainty", None)
                _pt("  uncertainty_ms",       getattr(tu, "total_ms", None), neg=True)
                ep = getattr(bd, "envelope_penalty", None)
                _pt("  envelope_ms",          getattr(ep, "total_ms", None), neg=True)
                _pt("  staleness_ms",         getattr(bd, "staleness_penalty_ms", None), neg=True)
                emp = getattr(bd, "empirical_penalty_ms", None)
                w_emp = getattr(bd, "w_empirical", 0.0)
                if emp is not None and abs(emp) > 0.01:
                    print(f"     {'empirical_knn_ms':<30s}  {-w_emp * emp:+.1f} ms  (raw={emp:.1f}, w={w_emp:.2f})")
                # Lap gain detail
                lgd = getattr(bd, "lap_gain_detail", None)
                if lgd is not None:
                    print(f"     lap gain detail:")
                    for fname in ("lltd_balance_ms", "damping_ms", "rebound_ratio_ms",
                                  "df_balance_ms", "camber_ms", "diff_preload_ms",
                                  "arb_extreme_ms", "diff_ramp_ms", "diff_clutch_ms",
                                  "tc_ms", "carcass_ms"):
                        v = getattr(lgd, fname, None)
                        if v is not None and abs(v) > 0.1:
                            print(f"       {fname:<32s}  {v:+.1f} ms")
        else:
            print(f"  📊 OBJECTIVE    not scored (sequential deterministic path, no search active)")

        print(f"  {_line}")

        # ── Legality ──────────────────────────────────────────────────────────
        tier_icons = {"full": "✅", "range_clamp": "⚠️ ", "none": "❌", "unknown": "❓"}
        t_icon = tier_icons.get(self.legality_tier, "❓")
        ok_str = "VALID" if self.legality_valid else "INVALID ⛔"
        print(f"  {t_icon} LEGALITY      tier={self.legality_tier}  {ok_str}")
        for msg in self.legality_messages[:3]:
            if msg:
                print(f"     {msg[:100]}")
        if self.legality_warnings:
            for w in self.legality_warnings[:3]:
                print(f"     ⚠ {w}")

        print(f"  {_line}")

        # ── Calibration ───────────────────────────────────────────────────────
        if self.calibration_available:
            spear_str = f"{self.calibration_spearman:+.3f}" if self.calibration_spearman is not None else "n/a"
            auto_str  = "ON ✅" if self.calibration_auto_apply else "OFF ⚠️"
            rev_str   = "REQUIRED ⚠️" if self.calibration_manual_review else "ok"
            print(f"  📐 CALIBRATION  Spearman={spear_str}  auto-apply={auto_str}  manual-review={rev_str}")
            if self.calibration_manual_review and not self.calibration_auto_apply:
                print(f"     Note: weight search suggests lower w_lap_gain — see validation/calibration_report.md")
            print(f"  {_line}")

        # ── Warnings ─────────────────────────────────────────────────────────
        if self.warnings:
            print(f"  ⚠️  WARNINGS ({len(self.warnings)})")
            for w in self.warnings:
                print(f"     • {w}")

        # ── Solve notes ───────────────────────────────────────────────────────
        if self.solve_notes and verbose:
            print(f"  📝 NOTES ({len(self.solve_notes)})")
            for n in self.solve_notes[:12]:
                prefix = "  ⚠️  " if "⚠" in n else "     • "
                print(f"{prefix}{n[:120]}")

        print(f"{_dline}\n")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _pt(label: str, value: Any, *, neg: bool = False, indent: int = 5) -> None:
    """Print one objective term line."""
    if value is None:
        return
    try:
        v = float(value)
    except (TypeError, ValueError):
        return
    if abs(v) < 0.05:
        return
    pad = " " * indent
    print(f"{pad}{label:<32s}  {v:+.1f} ms")


def _extract_step_key_outputs(step: int, obj: Any) -> dict[str, Any]:
    """Pull the most important output values from a solver step result object."""
    if obj is None:
        return {}
    fields_by_step: dict[int, list[str]] = {
        1: [
            "dynamic_front_rh_mm", "dynamic_rear_rh_mm",
            "df_balance_pct", "ld_ratio",
            "vortex_burst_margin_mm", "front_pushrod_offset_mm",
        ],
        2: [
            "front_heave_nmm", "rear_third_nmm",
            "front_bottoming_margin_mm", "rear_bottoming_margin_mm",
            "front_excursion_at_rate_mm", "perch_offset_front_mm",
        ],
        3: [
            "front_torsion_od_mm", "rear_spring_rate_nmm",
            "front_wheel_rate_nmm", "rear_spring_perch_mm",
        ],
        4: [
            "front_arb_size", "rear_arb_size",
            "front_arb_blade_start", "rear_arb_blade_start",
            "lltd_error",
        ],
        5: [
            "front_camber_deg", "rear_camber_deg",
            "front_toe_mm", "rear_toe_mm",
        ],
        7: [
            "brake_bias_pct", "diff_preload_nm",
            "diff_ramp_angles", "tc_gain", "tc_slip",
            "tyre_cold_fl_kpa",
        ],
    }
    result: dict[str, Any] = {}
    for f in fields_by_step.get(step, []):
        v = getattr(obj, f, None)
        if v is not None:
            result[f] = v
    if step == 6:
        # Dampers: show per-corner LS/HS comp clicks
        for corner_name in ("lf", "rf", "lr", "rr"):
            corner = getattr(obj, corner_name, None)
            if corner is not None:
                for attr in ("ls_comp", "ls_rbd", "hs_comp", "hs_rbd"):
                    v = getattr(corner, attr, None)
                    if v is not None:
                        result[f"{corner_name}_{attr}"] = v
    return result
