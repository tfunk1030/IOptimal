"""Calibration gate — blocks solver steps whose models lack proven data.

Every solver step requires specific subsystems to be calibrated from real
measured data (IBT telemetry, garage screenshots, etc.).  If ANY required
subsystem is uncalibrated for the current car, that step is BLOCKED and
the system outputs calibration instructions instead of a setup value.

Usage:
    gate = CalibrationGate(car, track_name)
    report = gate.check_step(step_number)
    if report.blocked:
        print(report.instructions_text())
    else:
        # run solver step
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from car_model.registry import track_key

if TYPE_CHECKING:
    from car_model.cars import CarModel


# ─── Calibration status for a single subsystem ──────────────────────────────

@dataclass
class SubsystemCalibration:
    """Calibration status of one subsystem for a specific car.

    Status semantics (strict mode — no silent fallbacks):
      - "calibrated":      Real measurement, R² >= 0.85 (or no R² applicable),
                           auto-cal validated. Step runs.
      - "weak":            Calibrated but R² < 0.85, or manual override that
                           disagrees with auto-cal. Step still runs with explicit
                           warning, and propagates weak-upstream status.
                           Weak blocks do NOT cascade to downstream steps.
      - "uncalibrated":    No measurement at all. Step BLOCKS and cascades.
      - "not_applicable":  Subsystem doesn't exist for this car's suspension
                           architecture (e.g. heave/third springs on a GT3 car).
                           This is HONEST absence — distinct from "uncalibrated"
                           which means "should be calibrated but isn't yet".
                           Step is skipped, not blocked. Does NOT cascade.
    """
    name: str
    status: str                      # "calibrated" | "weak" | "uncalibrated" | "not_applicable"
    source: str = ""
    data_points: int = 0
    instructions: str = ""
    r_squared: float | None = None
    q_squared: float | None = None   # LOO R² — generalisation quality metric
    confidence: str = "unknown"      # "high" | "medium" | "low" | "manual_override" | "unknown"
    warnings: list[str] = field(default_factory=list)

    def confidence_label(self) -> str:
        """Short label for display, e.g. '[HIGH R²=0.97 Q²=0.91]'."""
        parts = []
        if self.confidence != "unknown":
            parts.append(self.confidence.upper())
        if self.r_squared is not None:
            parts.append(f"R²={self.r_squared:.2f}")
        if self.q_squared is not None:
            parts.append(f"Q²={self.q_squared:.2f}")
        return "[" + " ".join(parts) + "]" if parts else ""


# Quality thresholds (strict mode)
R2_THRESHOLD_BLOCK = 0.85   # below this, model is too weak to trust (status=weak)
R2_THRESHOLD_WARN = 0.95    # below this, calibrated model still gets warning


# ─── Per-step calibration check result ───────────────────────────────────────

@dataclass
class StepCalibrationReport:
    """Result of checking whether a solver step can run."""
    step_number: int
    step_name: str
    blocked: bool = False
    missing: list[SubsystemCalibration] = field(default_factory=list)
    # Set when this step is blocked because a prior step is blocked
    dependency_blocked: bool = False
    blocked_by_step: int | None = None
    # True when this step's block is "weak only" (low R² or manual
    # override). Weak blocks don't cascade to downstream steps.
    weak_block: bool = False
    # True when an upstream step has weak calibration. This step still
    # runs but its provenance should note reduced input confidence.
    weak_upstream: bool = False
    weak_upstream_step: int | None = None
    # True when this step does not apply to this car's suspension architecture
    # (e.g. Step 2 heave/third springs on a GT3 car with coil-only suspension).
    # Not applicable is HONEST absence — the step is skipped, not blocked,
    # and does NOT cascade. Distinct from `blocked`.
    not_applicable: bool = False

    @property
    def confidence_weight(self) -> float:
        """Confidence weight for this step's output.

        1.0 = fully calibrated, 0.7 = weak calibration,
        0.5 = weak upstream (input data has reduced confidence),
        0.0 = blocked (no output) or not applicable (architecture skip).
        """
        if self.blocked or self.not_applicable:
            return 0.0
        if self.weak_block:
            return 0.7
        if self.weak_upstream:
            return 0.5
        return 1.0

    def instructions_text(self) -> str:
        """Format calibration instructions for all missing subsystems."""
        if self.not_applicable:
            return f"  STEP {self.step_number}: {self.step_name} — N/A (architecture skip)\n"
        if self.dependency_blocked:
            return (
                f"  STEP {self.step_number}: {self.step_name} — BLOCKED\n"
                f"    Depends on Step {self.blocked_by_step} (which is also blocked)\n"
                f"    Resolve Step {self.blocked_by_step} first.\n"
            )
        if not self.missing:
            return ""
        lines = [
            f"  STEP {self.step_number}: {self.step_name} — BLOCKED",
        ]
        for sub in self.missing:
            lines.append(f"    Missing: {sub.name} ({sub.status})")
            if sub.instructions:
                for line in sub.instructions.strip().splitlines():
                    lines.append(f"      {line}")
            lines.append("")
        return "\n".join(lines)


# ─── Full calibration report for a car + track ──────────────────────────────

@dataclass
class CalibrationReport:
    """Full calibration report across all 6 solver steps."""
    car_name: str
    track_name: str
    step_reports: list[StepCalibrationReport] = field(default_factory=list)

    @property
    def any_blocked(self) -> bool:
        return any(r.blocked for r in self.step_reports)

    @property
    def any_weak(self) -> bool:
        """True if any step has weak (non-blocking) data quality issues."""
        return any(r.weak_block for r in self.step_reports)

    @property
    def solved_steps(self) -> list[int]:
        return [
            r.step_number for r in self.step_reports
            if not r.blocked and not r.not_applicable
        ]

    @property
    def weak_steps(self) -> list[int]:
        return [r.step_number for r in self.step_reports if r.weak_block]

    @property
    def blocked_steps(self) -> list[int]:
        return [r.step_number for r in self.step_reports if r.blocked]

    @property
    def not_applicable_steps(self) -> list[int]:
        """Steps skipped because they don't apply to this car's suspension
        architecture (e.g. Step 2 heave/third on a GT3 coil-only car).
        Distinct from blocked: this is HONEST absence, not a calibration gap."""
        return [r.step_number for r in self.step_reports if r.not_applicable]

    @property
    def weak_upstream_steps(self) -> list[int]:
        """Steps that run with weak upstream dependency inputs."""
        return [r.step_number for r in self.step_reports if r.weak_upstream]

    @property
    def step_confidence(self) -> dict[int, float]:
        """Confidence weight per step (1.0=calibrated, 0.7=weak, 0.5=weak_upstream, 0.0=blocked)."""
        return {r.step_number: r.confidence_weight for r in self.step_reports}

    def format_header(self) -> str:
        """Format the calibration status header for report output."""
        lines = []
        solved = self.solved_steps
        weak = self.weak_steps
        blocked = self.blocked_steps
        if solved:
            lines.append("CALIBRATED STEPS (producing validated output):")
            for s in solved:
                r = self.step_reports[s - 1]
                marker = "[~~]" if r.weak_block else "[OK]"
                weak_tag = "  (WEAK DATA — see warnings below)" if r.weak_block else ""
                lines.append(f"  {marker} Step {s}: {r.step_name}{weak_tag}")
        if weak and not blocked:
            lines.append("")
            lines.append("WEAK STEPS (output produced but calibration data is below threshold):")
            for s in weak:
                r = self.step_reports[s - 1]
                lines.append(f"  Step {s} ({r.step_name}):")
                for sub in r.missing:
                    lines.append(f"    - {sub.name}: {sub.confidence_label()} {sub.source}")
                    for w in sub.warnings:
                        lines.append(f"      ! {w}")
        weak_upstream = self.weak_upstream_steps
        if weak_upstream and not blocked:
            lines.append("")
            lines.append("WEAK-UPSTREAM STEPS (ran using weaker upstream calibration):")
            for s in weak_upstream:
                r = self.step_reports[s - 1]
                if r.weak_upstream_step is not None:
                    lines.append(
                        f"  Step {s} ({r.step_name}): upstream dependency from Step {r.weak_upstream_step} is weak"
                    )
                else:
                    lines.append(f"  Step {s} ({r.step_name}): upstream dependency is weak")
        not_applicable = self.not_applicable_steps
        if not_applicable:
            lines.append("")
            lines.append("NOT APPLICABLE STEPS (architecture skip — not a calibration gap):")
            for s in not_applicable:
                r = self.step_reports[s - 1]
                lines.append(f"  [--] Step {s}: {r.step_name}")
        if blocked:
            lines.append("")
            lines.append("UNCALIBRATED STEPS (calibration required):")
            for s in blocked:
                r = self.step_reports[s - 1]
                lines.append(r.instructions_text())
        return "\n".join(lines)

    def format_confidence_report(
        self, subsystems: dict[str, SubsystemCalibration] | None = None
    ) -> str:
        """Format a per-subsystem confidence report with R² and warnings.

        This is additive to the block/pass gate — it surfaces weak models
        and manual overrides without changing what blocks. Pass in the
        subsystems dict from CalibrationGate.subsystems() for details.
        """
        if not subsystems:
            return ""
        lines = ["CALIBRATION CONFIDENCE — provenance per subsystem:"]
        order = [
            "aero_compression", "ride_height_model", "deflection_model",
            "spring_rates", "pushrod_geometry", "damper_zeta",
            "arb_stiffness", "lltd_target", "roll_gains",
        ]
        warnings_to_show: list[str] = []
        for name in order:
            sub = subsystems.get(name)
            if sub is None:
                continue
            label = sub.confidence_label()
            if sub.status == "calibrated":
                status_icon = "OK "
            elif sub.status == "weak":
                status_icon = "~~ "
            elif sub.status == "not_applicable":
                status_icon = "-- "
            else:
                status_icon = "!! "
            lines.append(f"  {status_icon}{name:<22} {label}  {sub.source}")
            for w in sub.warnings:
                warnings_to_show.append(f"    - {name}: {w}")
        if warnings_to_show:
            lines.append("")
            lines.append("CONFIDENCE WARNINGS (non-blocking):")
            lines.extend(warnings_to_show)
        return "\n".join(lines)


# ─── Calibration instruction templates ───────────────────────────────────────

INSTRUCTIONS = {
    "aero_compression": """\
TO CALIBRATE AERO COMPRESSION:
1. Record 3+ IBT sessions at different speed profiles (qualifying pace, race pace, traffic)
2. Run: python -m learner.ingest --car {car} --ibt <each_file>
3. The system extracts ride height vs speed^2 regression to derive aero compression
4. Minimum 3 sessions needed for reliable fit""",

    "ride_height_model": """\
TO CALIBRATE RIDE HEIGHT MODEL:
1. In iRacing garage, set 10+ different spring/pushrod/perch combinations
2. Record an IBT session for each setting (drive 3+ clean laps; the IBT header captures displayed ride heights)
3. Run: python -m car_model.auto_calibrate --car {car} --ibt-dir <telemetry_dir>
4. This fits a multi-variable regression from IBT session header values
5. Minimum 10 unique configurations for reliable fit (R^2 > 0.95)""",

    "deflection_model": """\
TO CALIBRATE DEFLECTION MODEL:
1. In iRacing garage, set 5+ different heave spring settings
   (keep torsion bar constant, vary heave spring: e.g., 50, 100, 150, 200, 250 N/mm)
2. Record an IBT session for each setting (drive 3+ clean laps; the IBT header captures deflection values)
3. Run: python -m car_model.auto_calibrate --car {car} --ibt-dir <telemetry_dir>
4. Minimum 5 varied settings for reliable fit""",

    "damper_zeta": """\
TO CALIBRATE DAMPER ZETA TARGETS:
1. Run dedicated damper click-sweep: 5+ stints with LS compression at varied clicks
   (e.g., clicks 2, 5, 8, 11, 14 -- spread across the full range)
2. Keep ALL other setup parameters identical between stints
3. Record IBT per stint (minimum 3 clean laps each)
4. Run: python -m learner.ingest --car {car} --ibt <each_file> --all-laps
5. After ingestion: python -m validation.calibrate_dampers --car {car} --track {track}
6. This extracts optimal zeta targets from the click -> platform stability relationship""",

    "arb_stiffness": """\
TO CALIBRATE ARB STIFFNESS:
1. Record 3+ IBT sessions with different front/rear ARB sizes (keep springs constant between sessions)
2. Drive 3+ clean laps per session (the telemetry roll gradient data is needed for ARB back-solve)
3. Run: python -m car_model.auto_calibrate --car {car} --ibt-dir <telemetry_dir>
4. This back-solves ARB stiffness from telemetry roll data across varied ARB configurations""",

    "lltd_target": """\
TO CALIBRATE LLTD TARGET:
1. Do NOT use IBT lltd_measured / roll_distribution_proxy as a calibration target.
2. Establish a car/track LLTD target from either:
   - wheel-force telemetry that exposes true LF/RF/LR/RR load channels, or
   - an explicit engineering hand-calibration documented in cars.py.
3. Until true LLTD telemetry exists, keep the car's physics-derived or
   hand-calibrated target and treat ARB output as lower-authority.""",

    "roll_gains": """\
TO CALIBRATE ROLL GAINS:
1. Record 3+ IBT sessions with varied speeds and lateral-g (qualifying + race pace)
2. Run: python -m car_model.auto_calibrate --car {car} --ibt-dir <telemetry_dir>
3. Auto-calibrate extracts roll gradient from telemetry and validates geometry roll gains
4. Minimum 3 sessions with consistent roll gradient (CV < 30%) for calibration""",

    "track_profile": """\
TO GENERATE TRACK PROFILE:
1. Run 5+ clean laps at the target track in your car
2. Record the IBT file
3. Run: python -m track_model.build --car {car} --ibt <session.ibt>
4. This extracts surface spectrum, corner speeds, braking zones, kerb severity""",
}


def _fmt_instructions(key: str, car: str, track: str) -> str:
    """Format calibration instructions with car/track names."""
    template = INSTRUCTIONS.get(key, "")
    return template.format(car=car, track=track)


# ─── Build subsystem calibration status from a CarModel ──────────────────────

def _safe_track_slug(track: str) -> str:
    """Return a filesystem-safe slug for *track*.

    Mirrors :func:`auto_calibrate._safe_track_slug` so the gate reads
    the same per-track model files that auto-calibrate writes.
    """
    import re

    slug = re.sub(r"[^a-z0-9_]", "_", track.lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "unknown"


def _load_raw_calibration_models(car_canonical: str, track: str = "") -> dict:
    """Load raw models JSON for a car to read R² values and status flags.

    When *track* is provided, try the per-track model file first
    (``models_{slug}.json``).  Fall back to the pooled ``models.json``
    if the per-track file doesn't exist.

    Returns an empty dict if no calibration file exists. Used for honest
    reporting — does NOT override what the live CarModel says.
    """
    from pathlib import Path
    import json

    repo_root = Path(__file__).resolve().parents[1]
    cal_dir = repo_root / "data" / "calibration" / car_canonical

    # Try per-track model file first when a track is specified
    if track:
        slug = _safe_track_slug(track)
        track_path = cal_dir / f"models_{slug}.json"
        if track_path.exists():
            try:
                with open(track_path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:
                pooled = {}
                path = cal_dir / "models.json"
                if path.exists():
                    try:
                        with open(path, encoding="utf-8") as f:
                            pooled = json.load(f)
                    except Exception as pooled_exc:
                        pooled = {
                            "__load_error__": f"{type(pooled_exc).__name__}: {pooled_exc}",
                        }
                # Keep this load failure visible without changing the status
                # derived from the live CarModel.  Some cars intentionally have
                # hand-calibrated live models that are stronger than stale raw
                # JSON metadata.
                pooled.setdefault("__track_load_error__", (
                    f"{track_path.name}: {type(exc).__name__}: {exc}"
                ))
                pooled.setdefault("__calibration_model_source__", "pooled_after_track_load_error")
                return pooled

    # Pooled / fallback
    path = cal_dir / "models.json"
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data
    except Exception as exc:
        return {
            "__load_error__": f"{type(exc).__name__}: {exc}",
        }


def _classify_r_squared(r2: float | None, threshold_high: float = 0.90,
                        threshold_low: float = 0.70) -> str:
    """Map an R² value to a confidence label."""
    if r2 is None:
        return "unknown"
    if r2 >= threshold_high:
        return "high"
    if r2 >= threshold_low:
        return "medium"
    return "low"


def _weaker_q2(front_q2: float | None, rear_q2: float | None) -> float | None:
    """Return the weaker (lower) of two Q² values, or whichever is available."""
    if front_q2 is not None and rear_q2 is not None:
        return min(front_q2, rear_q2)
    return front_q2 if front_q2 is not None else rear_q2


# Q² thresholds (conservative — warn only, do not change gate status)
Q2_THRESHOLD_WARN = 0.60


def _append_q2_warnings(
    warnings: list[str],
    label: str,
    r2: float | None,
    q2: float | None,
) -> None:
    """Append Q²-based warnings if Q² diverges significantly from R².

    Conservative: warnings are informational and do NOT change gate status.
    """
    if q2 is None:
        return
    if q2 < Q2_THRESHOLD_WARN:
        warnings.append(
            f"{label} Q²={q2:.2f} < {Q2_THRESHOLD_WARN} — "
            f"model may not generalise well to new setups"
        )
    elif r2 is not None and r2 - q2 > 0.15:
        warnings.append(
            f"{label} R²={r2:.2f} vs Q²={q2:.2f} (gap {r2 - q2:.2f}) — "
            f"possible overfit"
        )


def _build_subsystem_status(car: "CarModel", track_name: str) -> dict[str, SubsystemCalibration]:
    """Inspect a CarModel and return calibration status for every subsystem."""
    cn = car.canonical_name
    raw_models = _load_raw_calibration_models(cn, track=track_name)
    subs: dict[str, SubsystemCalibration] = {}
    track_supported = car.supports_track(track_name)
    subs["track_support"] = SubsystemCalibration(
        name="track_support",
        status="calibrated" if track_supported else "uncalibrated",
        source=(
            f"supported track '{track_key(track_name)}'"
            if track_supported
            else (
                f"unsupported track '{track_key(track_name)}' "
                f"(supported: {car.supported_tracks_label()})"
            )
        ),
        instructions=(
            ""
            if track_supported
            else (
                f"This car's calibration is only validated for {car.supported_tracks_label()}. "
                f"Collect dedicated telemetry and garage-truth data for {track_name} before trusting Step 1."
            )
        ),
    )

    # 1. Aero compression — check the is_calibrated flag on the car's AeroCompression
    aero_cal = getattr(car.aero_compression, "is_calibrated", False)
    aero_sessions = raw_models.get("aero_n_sessions", 0)
    subs["aero_compression"] = SubsystemCalibration(
        name="aero_compression",
        status="calibrated" if aero_cal else "uncalibrated",
        source=(
            f"IBT-derived ({aero_sessions} sessions)"
            if aero_cal and aero_sessions
            else ("IBT-derived" if aero_cal else "ESTIMATE — no IBT aero calibration")
        ),
        data_points=aero_sessions,
        confidence="high" if aero_cal and aero_sessions >= 5 else ("medium" if aero_cal else "unknown"),
        instructions=_fmt_instructions("aero_compression", cn, track_name),
    )

    # 2. Ride height model — surface front+rear R² separately when available
    rh_cal = car.ride_height_model.is_calibrated
    front_rh = (raw_models.get("front_ride_height") or {}) if isinstance(raw_models, dict) else {}
    rear_rh = (raw_models.get("rear_ride_height") or {}) if isinstance(raw_models, dict) else {}
    front_r2 = front_rh.get("r_squared") if isinstance(front_rh, dict) else None
    rear_r2 = rear_rh.get("r_squared") if isinstance(rear_rh, dict) else None
    front_q2 = front_rh.get("q_squared") if isinstance(front_rh, dict) else None
    rear_q2 = rear_rh.get("q_squared") if isinstance(rear_rh, dict) else None
    # Use the weaker of the two models as the subsystem confidence
    weaker_r2 = None
    if front_r2 is not None and rear_r2 is not None:
        weaker_r2 = min(front_r2, rear_r2)
    elif front_r2 is not None:
        weaker_r2 = front_r2
    elif rear_r2 is not None:
        weaker_r2 = rear_r2
    weaker_q2 = _weaker_q2(front_q2, rear_q2)
    rh_warnings: list[str] = []
    if front_r2 is not None and front_r2 < R2_THRESHOLD_BLOCK:
        rh_warnings.append(f"Front RH model weak: R²={front_r2:.2f} < {R2_THRESHOLD_BLOCK}")
    elif front_r2 is not None and front_r2 < R2_THRESHOLD_WARN:
        rh_warnings.append(
            f"Front RH model below warn threshold: R²={front_r2:.2f} < {R2_THRESHOLD_WARN}"
        )
    if rear_r2 is not None and rear_r2 < R2_THRESHOLD_BLOCK:
        rh_warnings.append(f"Rear RH model weak: R²={rear_r2:.2f} < {R2_THRESHOLD_BLOCK}")
    elif rear_r2 is not None and rear_r2 < R2_THRESHOLD_WARN:
        rh_warnings.append(
            f"Rear RH model below warn threshold: R²={rear_r2:.2f} < {R2_THRESHOLD_WARN}"
        )
    # Q² warnings — conservative: warn but don't change gate status
    _append_q2_warnings(rh_warnings, "Front RH", front_r2, front_q2)
    _append_q2_warnings(rh_warnings, "Rear RH", rear_r2, rear_q2)
    # Strict mode: weak fits are surfaced with warnings and marked weak.
    if not rh_cal and weaker_r2 is None:
        rh_status = "uncalibrated"
    elif not rh_cal:
        # Raw per-track regression evidence exists, but the live CarModel was
        # not marked calibrated (for example when one axis is weak and
        # apply_to_car skipped the all-or-nothing RH update).  Surface this as
        # weak evidence instead of a hard block so the gate matches the
        # calibrated/weak/uncalibrated contract.
        rh_status = "weak"
        rh_warnings.append(
            "Ride height regression data present, but live car model is not marked calibrated"
        )
    elif weaker_r2 is not None and weaker_r2 < R2_THRESHOLD_BLOCK:
        rh_status = "weak"
    else:
        rh_status = "calibrated"
    subs["ride_height_model"] = SubsystemCalibration(
        name="ride_height_model",
        status=rh_status,
        source=(
            f"regression (front R²={front_r2:.2f}, rear R²={rear_r2:.2f})"
            if front_r2 is not None and rear_r2 is not None
            else ("regression model" if rh_cal else "no calibration data")
        ),
        r_squared=weaker_r2,
        q_squared=weaker_q2,
        confidence=_classify_r_squared(weaker_r2) if rh_cal else "unknown",
        warnings=rh_warnings,
        instructions=_fmt_instructions("ride_height_model", cn, track_name),
    )

    # 3. Deflection model — report best-model R² from raw JSON.
    # Filter heave/third sub-models out for cars whose suspension architecture
    # has no heave/third springs (e.g. GT3 coil-only); those sub-models are
    # physically nonexistent and would otherwise pull non-data into the
    # subsystem's R² statistics.
    defl_cal = car.deflection.is_calibrated
    defl_r2s: list[float] = []
    defl_q2s: list[float] = []
    defl_keys: list[str] = ["rear_spring_defl_static", "rear_shock_defl_static"]
    if car.suspension_arch.has_heave_third:
        defl_keys = [
            "heave_spring_defl_static",
            "heave_spring_defl_max",
            "third_spring_defl_static",
        ] + defl_keys
    for key in defl_keys:
        model = raw_models.get(key)
        if isinstance(model, dict) and "r_squared" in model:
            try:
                defl_r2s.append(float(model["r_squared"]))
            except (TypeError, ValueError):
                pass
        if isinstance(model, dict) and "q_squared" in model:
            try:
                val = model["q_squared"]
                if val is not None:
                    defl_q2s.append(float(val))
            except (TypeError, ValueError):
                pass
    weakest_defl_r2 = min(defl_r2s) if defl_r2s else None
    weakest_defl_q2 = min(defl_q2s) if defl_q2s else None
    defl_warnings: list[str] = []
    if weakest_defl_r2 is not None and weakest_defl_r2 < R2_THRESHOLD_BLOCK:
        defl_warnings.append(
            f"Weakest deflection sub-model R²={weakest_defl_r2:.2f} < {R2_THRESHOLD_BLOCK}"
        )
    elif weakest_defl_r2 is not None and weakest_defl_r2 < R2_THRESHOLD_WARN:
        defl_warnings.append(
            f"Weakest deflection sub-model below warn threshold: "
            f"R²={weakest_defl_r2:.2f} < {R2_THRESHOLD_WARN}"
        )
    # Q² warnings — conservative: warn but don't change gate status
    if weakest_defl_q2 is not None and weakest_defl_r2 is not None:
        _append_q2_warnings(defl_warnings, "Weakest deflection sub-model",
                            weakest_defl_r2, weakest_defl_q2)
    if not defl_cal:
        defl_status = "uncalibrated"
    elif weakest_defl_r2 is not None and weakest_defl_r2 < R2_THRESHOLD_BLOCK:
        defl_status = "weak"
    else:
        defl_status = "calibrated"
    subs["deflection_model"] = SubsystemCalibration(
        name="deflection_model",
        status=defl_status,
        source=(
            f"regression (weakest R²={weakest_defl_r2:.2f})"
            if weakest_defl_r2 is not None
            else ("regression model" if defl_cal else "no calibration data")
        ),
        r_squared=weakest_defl_r2,
        q_squared=weakest_defl_q2,
        confidence=_classify_r_squared(weakest_defl_r2) if defl_cal else "unknown",
        warnings=defl_warnings,
        instructions=_fmt_instructions("deflection_model", cn, track_name),
    )

    # GT3-style architectures expose an honest "this doesn't exist" provenance
    # entry so the JSON dump shows the absence as architectural, not a gap.
    if not car.suspension_arch.has_heave_third:
        subs["heave_third_deflection"] = SubsystemCalibration(
            name="heave_third_deflection",
            status="not_applicable",
            source=f"architecture skip ({car.suspension_arch.name})",
        )

    # 4. Pushrod geometry — check the per-car PushrodGeometry.is_calibrated flag.
    # When True: pushrod coefficients come from measured garage data / IBT screenshots.
    # When False: only the defaults are set (front_pinned_rh, rear_base_rh, etc.)
    # but the sensitivity slopes (front_pushrod_to_rh, rear_pushrod_to_rh) may not
    # reflect the actual car. Step 1 can still run but the pushrod sensitivity will
    # be approximate.
    pushrod_is_cal = getattr(car.pushrod, "is_calibrated", False)
    subs["pushrod_geometry"] = SubsystemCalibration(
        name="pushrod_geometry",
        status="calibrated" if pushrod_is_cal else "weak",
        source="garage screenshots" if pushrod_is_cal else "estimated defaults",
        warnings=(
            []
            if pushrod_is_cal
            else [
                "Pushrod geometry not calibrated from garage data. "
                "Front/rear pushrod-to-RH sensitivity uses default estimates. "
                "Collect 3+ garage screenshots at different pushrod settings to calibrate."
            ]
        ),
    )

    # 5. Spring rates / torsion bar constants
    # Check for unvalidated rear torsion bar (Ferrari has a 3.5x rate error flag)
    rear_torsion_unvalidated = getattr(car.corner_spring, "rear_torsion_unvalidated", False)
    spring_warnings: list[str] = []
    if rear_torsion_unvalidated:
        spring_status = "uncalibrated"
        spring_source = "car-specific model (rear torsion bar UNVALIDATED — potential 3.5x rate error)"
        spring_warnings.append(
            "Rear torsion bar model has potential 3.5x rate error — corner spring "
            "output BLOCKED until validated. Collect 5+ garage screenshots with "
            "varied rear spring indices to calibrate."
        )
    else:
        spring_status = "calibrated"
        spring_source = "car-specific model"
    subs["spring_rates"] = SubsystemCalibration(
        name="spring_rates",
        status=spring_status,
        source=spring_source,
        warnings=spring_warnings,
    )

    # 6. Damper zeta
    zeta_cal = getattr(car.damper, "zeta_is_calibrated", False)
    zeta_n = raw_models.get("zeta_n_sessions", 0)
    subs["damper_zeta"] = SubsystemCalibration(
        name="damper_zeta",
        status="calibrated" if zeta_cal else "uncalibrated",
        source=(
            f"IBT click-sweep ({zeta_n} sessions)"
            if zeta_cal and zeta_n
            else ("IBT click-sweep" if zeta_cal else "not calibrated from IBT")
        ),
        data_points=zeta_n,
        confidence="high" if zeta_cal and zeta_n >= 20 else ("medium" if zeta_cal else "unknown"),
        instructions=_fmt_instructions("damper_zeta", cn, track_name),
    )

    # 7. ARB stiffness — check car.arb.is_calibrated but also compare against
    # the auto-calibration result to detect manual-override conflicts.
    arb_cal = getattr(car.arb, "is_calibrated", False)
    arb_status_from_data = raw_models.get("status", {}).get("arb_calibrated")
    arb_status_note = raw_models.get("status", {}).get("arb_stiffness", "")
    arb_warnings: list[str] = []
    if not arb_cal:
        # Car definition says ARB is uncalibrated → block.
        arb_confidence = "unknown"
        arb_source = "estimated — no measured data"
        arb_status = "uncalibrated"
    elif arb_status_from_data is True:
        # Car says calibrated AND auto-cal agrees → high confidence.
        arb_confidence = "high"
        arb_source = "measured from IBT roll data (auto-cal validated)"
        arb_status = "calibrated"
    elif arb_status_from_data is False:
        # Car says calibrated but auto-cal explicitly FAILED → contradiction.
        # Strict mode: this is "weak" → BLOCK until the contradiction is resolved.
        arb_confidence = "manual_override"
        arb_source = "manual override (auto-cal CONTRADICTS car definition)"
        arb_status = "weak"
        arb_warnings.append(
            "AUTO-CAL CONTRADICTS car_model ARB stiffness. Either correct "
            "the values in cars.py to match measured data, or collect more "
            f"roll-gradient telemetry. Details: {arb_status_note}"
        )
    else:
        # Car says calibrated, auto-cal hasn't been run for this car
        # (no arb_calibrated key in models.json). This is the BMW situation:
        # ARB stiffness was hand-calibrated from real data, just not via the
        # auto-cal pipeline. Trust the car definition; surface as medium
        # confidence so the user knows it's not auto-validated.
        arb_confidence = "medium"
        arb_source = "car_model hand-calibration (no auto-cal validation)"
        arb_status = "calibrated"
        arb_warnings.append(
            "ARB has not been auto-validated. To upgrade to high confidence, "
            "run auto_calibrate with 3+ IBT sessions varying ARB sizes."
        )
    subs["arb_stiffness"] = SubsystemCalibration(
        name="arb_stiffness",
        status=arb_status,
        source=arb_source,
        confidence=arb_confidence,
        warnings=arb_warnings,
        instructions=_fmt_instructions("arb_stiffness", cn, track_name),
    )

    # 8. LLTD target — requires measured_lltd_target to be set
    # Source may be IBT observations (BMW), session averages (Ferrari),
    # or physics formula (Porsche OptimumG/Milliken) — label accurately.
    lltd_set = car.measured_lltd_target is not None
    lltd_from_models = raw_models.get("measured_lltd_target") is not None
    lltd_source_type = getattr(car, "lltd_target_source", "physics_formula")
    if lltd_set and lltd_source_type == "track_observation":
        lltd_source = f"track-observed hand calibration (target={car.measured_lltd_target:.3f})"
    elif lltd_set and lltd_source_type == "physics_formula":
        lltd_source = f"physics formula (target={car.measured_lltd_target:.3f})"
    elif lltd_set and lltd_from_models:
        lltd_source = f"legacy model file target (target={car.measured_lltd_target:.3f})"
    elif lltd_set:
        lltd_source = f"hand-calibrated (target={car.measured_lltd_target:.3f})"
    else:
        lltd_source = "no measured data"
    subs["lltd_target"] = SubsystemCalibration(
        name="lltd_target",
        status="calibrated" if lltd_set else "uncalibrated",
        source=lltd_source,
        instructions=_fmt_instructions("lltd_target", cn, track_name),
    )

    # 9. Roll gains (wheel geometry) — check the roll_gains_calibrated flag
    roll_cal = getattr(car.geometry, "roll_gains_calibrated", False)
    subs["roll_gains"] = SubsystemCalibration(
        name="roll_gains",
        status="calibrated" if roll_cal else "uncalibrated",
        source="IBT-calibrated" if roll_cal else "estimated",
        instructions=_fmt_instructions("roll_gains", cn, track_name),
    )

    return subs


# ─── Step-level calibration requirements ─────────────────────────────────────

# Each solver step maps to required subsystems.
# "calibrated" runs cleanly, "weak" runs with warnings, "uncalibrated" blocks.
STEP_REQUIREMENTS: dict[int, tuple[str, list[str]]] = {
    1: ("Rake / Ride Heights", ["aero_compression", "ride_height_model", "pushrod_geometry"]),
    2: ("Heave / Third Springs", ["spring_rates"]),
    3: ("Corner Springs", ["spring_rates"]),
    4: ("Anti-Roll Bars", ["arb_stiffness", "lltd_target"]),
    5: ("Wheel Geometry", ["roll_gains"]),
    6: ("Dampers", ["damper_zeta"]),
}


# ─── Main calibration gate ───────────────────────────────────────────────────

class CalibrationGate:
    """Check calibration status for a car + track and gate solver steps.

    Usage:
        gate = CalibrationGate(car, "sebring")
        report = gate.full_report()
        for step_num in range(1, 7):
            step_report = gate.check_step(step_num)
            if step_report.blocked:
                print(step_report.instructions_text())
    """

    def __init__(self, car: "CarModel", track_name: str) -> None:
        self.car = car
        self.track_name = track_name
        self._subsystems = _build_subsystem_status(car, track_name)

    def subsystem(self, name: str) -> SubsystemCalibration:
        """Get calibration status for a named subsystem."""
        return self._subsystems.get(name, SubsystemCalibration(
            name=name, status="uncalibrated", source="unknown",
        ))

    def subsystems(self) -> dict[str, SubsystemCalibration]:
        """Return the full subsystems dict for confidence reporting."""
        return self._subsystems

    def provenance(self) -> dict[str, dict]:
        """JSON-friendly provenance for every calibrated subsystem.

        Returns a dict like:
            {
              "ride_height_model": {
                "status": "calibrated",
                "source": "regression (front R²=1.00, rear R²=0.94)",
                "confidence": "high",
                "r_squared": 0.94,
                "data_points": 0,
                "warnings": [],
              },
              ...
            }
        Used by the pipeline to embed provenance in JSON output so the user
        can audit exactly where each value came from.
        """
        out: dict[str, dict] = {}
        for name, sub in self._subsystems.items():
            entry: dict = {
                "status": sub.status,
                "source": sub.source,
                "confidence": sub.confidence,
                "r_squared": sub.r_squared,
                "data_points": sub.data_points,
                "warnings": list(sub.warnings),
            }
            if sub.q_squared is not None:
                entry["q_squared"] = sub.q_squared
            out[name] = entry
        return out

    # Solver chain data dependencies (not calibration dependencies):
    # Step 2 needs Step 1's dynamic RH targets.
    # Step 3 needs Step 2's spring rates.
    # Step 4 needs Step 3's wheel rates.
    # Step 5 needs Step 3's wheel rates AND Step 4's roll stiffness (k_roll_total).
    # Step 6 needs Step 3's wheel rates.
    # Cascade tracks the HIGHEST dependency — if Step 4 is blocked, Step 5 is
    # also blocked because solve.py:520 feeds step4.k_roll_front/rear_total
    # into the geometry solver.
    #
    # NOTE: This class-level constant is the GTP variant and is preserved for
    # backwards compatibility with any external module that still imports it.
    # Per-instance dispatch (which honours suspension_arch) goes through the
    # _data_prior_step property below; check_step uses that, NOT this constant.
    _DATA_PRIOR_STEP: dict[int, int] = {2: 1, 3: 2, 4: 3, 5: 4, 6: 3}

    @property
    def _data_prior_step(self) -> dict[int, int]:
        """Cascade table per suspension architecture.

        GTP cars (heave/third present): {2: 1, 3: 2, 4: 3, 5: 4, 6: 3}
        GT3 cars (no heave/third — Step 2 is N/A):  {3: 1, 4: 3, 5: 4, 6: 3}
          Step 3 cascades directly from Step 1 because Step 2 doesn't exist.
        """
        if self.car.suspension_arch.has_heave_third:
            return {2: 1, 3: 2, 4: 3, 5: 4, 6: 3}
        return {3: 1, 4: 3, 5: 4, 6: 3}

    def check_step(self, step_number: int) -> StepCalibrationReport:
        """Check if a solver step can run with calibrated data.

        A step blocks when any required subsystem is uncalibrated.
        Weak subsystems (R² below threshold, manual override disagreements)
        do not block, but are surfaced loudly on the report.

        Cascade: only TRUE data blocks (uncalibrated, dependency-blocked)
        propagate to downstream steps. A "weak" block does NOT cascade —
        downstream steps with their own valid data still run. Likewise a
        "not_applicable" prior step does NOT cascade: not_applicable is HONEST
        absence (architectural skip), not a calibration gap, and does not
        reduce downstream confidence.
        """
        # GT3 dispatch: Step 2 (heave/third springs) does not apply to cars
        # whose suspension architecture lacks heave/third springs.
        if step_number == 2 and not self.car.suspension_arch.has_heave_third:
            return StepCalibrationReport(
                step_number=2,
                step_name=STEP_REQUIREMENTS[2][0],
                not_applicable=True,
            )

        step_name, required = STEP_REQUIREMENTS.get(
            step_number, (f"Step {step_number}", [])
        )
        report = StepCalibrationReport(
            step_number=step_number,
            step_name=step_name,
        )

        # Data dependency cascade (only on TRUE blocks, not weak, not N/A)
        prior_num = self._data_prior_step.get(step_number)
        if prior_num is not None:
            prior = self.check_step(prior_num)
            # Only cascade if prior step is BLOCKED for a hard reason
            # (uncalibrated subsystem or its own dependency block).
            prior_hard_blocked = prior.blocked and not prior.weak_block
            if prior_hard_blocked:
                report.blocked = True
                report.dependency_blocked = True
                report.blocked_by_step = prior_num
                return report
            # Propagate weak-upstream: if prior step has weak calibration
            # or itself has weak upstream, flag this step so provenance
            # reflects reduced confidence in input data. NOT_APPLICABLE
            # priors are excluded — architecture skip is not a quality gap.
            if (prior.weak_block or prior.weak_upstream) and not prior.not_applicable:
                report.weak_upstream = True
                report.weak_upstream_step = prior_num

        # Check this step's own subsystems.
        # Strict-mode classification: any "weak" subsystem marks the step
        # as having weak data, but currently does NOT block (because legacy
        # call sites assume blocked steps don't exist). Truly uncalibrated
        # subsystems still block.
        weak_subsystems: list[SubsystemCalibration] = []
        for req in required:
            sub = self.subsystem(req)
            if sub.status == "uncalibrated":
                report.blocked = True
                report.missing.append(sub)
            elif sub.status == "weak":
                weak_subsystems.append(sub)
        # Surface weak subsystems on the report so callers can warn loudly
        # without crashing on missing step output.
        if weak_subsystems and not report.blocked:
            report.weak_block = True
            report.missing.extend(weak_subsystems)
        return report

    def full_report(self) -> CalibrationReport:
        """Generate calibration report for all 6 solver steps."""
        report = CalibrationReport(
            car_name=self.car.name,
            track_name=self.track_name,
        )
        for step_num in range(1, 7):
            report.step_reports.append(self.check_step(step_num))
        return report

    def step_is_runnable(self, step_number: int) -> bool:
        """Quick check: can this step run?"""
        return not self.check_step(step_number).blocked

    def all_calibrated(self) -> bool:
        """True if all APPLICABLE steps can run with calibrated data.

        Architecture skips (e.g. Step 2 on a GT3 coil-only car) do NOT count
        as a failure: they are not_applicable, not blocked. A car with one
        not_applicable step and the rest calibrated still returns True.
        """
        return all(self.step_is_runnable(s) for s in range(1, 7))

    def summary_line(self) -> str:
        """One-line summary of calibration status."""
        report = self.full_report()
        solved = len(report.solved_steps)
        blocked = len(report.blocked_steps)
        na = len(report.not_applicable_steps)
        applicable = 6 - na
        if blocked == 0 and na == 0:
            return f"{self.car.name}: all 6 steps calibrated"
        if blocked == 0:
            return (
                f"{self.car.name}: {solved}/{applicable} applicable steps calibrated, "
                f"{na} not applicable (steps {report.not_applicable_steps})"
            )
        return (
            f"{self.car.name}: {solved}/{applicable} steps calibrated, "
            f"{blocked} blocked (steps {report.blocked_steps})"
        )
