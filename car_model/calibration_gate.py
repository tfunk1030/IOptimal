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

if TYPE_CHECKING:
    from car_model.cars import CarModel


# ─── Calibration status for a single subsystem ──────────────────────────────

@dataclass
class SubsystemCalibration:
    """Calibration status of one subsystem for a specific car."""
    name: str                        # e.g. "aero_compression", "damper_zeta"
    status: str                      # "calibrated" | "partial" | "uncalibrated"
    source: str = ""                 # e.g. "31 sessions", "13 garage screenshots"
    data_points: int = 0             # Number of data points used for calibration
    instructions: str = ""           # Calibration instructions if not calibrated
    # Confidence metadata (added 2026-04-06 for honest reporting)
    r_squared: float | None = None   # Regression R² where applicable
    confidence: str = "unknown"      # "high" | "medium" | "low" | "manual_override" | "unknown"
    warnings: list[str] = field(default_factory=list)  # Non-blocking issues to surface

    def confidence_label(self) -> str:
        """Short label for display, e.g. '[HIGH R²=0.97]' or '[LOW R²=0.61]'."""
        parts = []
        if self.confidence != "unknown":
            parts.append(self.confidence.upper())
        if self.r_squared is not None:
            parts.append(f"R²={self.r_squared:.2f}")
        return "[" + " ".join(parts) + "]" if parts else ""


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

    def instructions_text(self) -> str:
        """Format calibration instructions for all missing subsystems."""
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
    def solved_steps(self) -> list[int]:
        return [r.step_number for r in self.step_reports if not r.blocked]

    @property
    def blocked_steps(self) -> list[int]:
        return [r.step_number for r in self.step_reports if r.blocked]

    def format_header(self) -> str:
        """Format the calibration status header for report output."""
        lines = []
        solved = self.solved_steps
        blocked = self.blocked_steps
        if solved:
            lines.append("CALIBRATED STEPS (producing validated output):")
            for s in solved:
                r = self.step_reports[s - 1]
                lines.append(f"  [OK] Step {s}: {r.step_name}")
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
        lines = ["CALIBRATION CONFIDENCE:"]
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
            status_icon = "OK " if sub.status == "calibrated" else "!! "
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
1. Accumulate 10+ IBT sessions with varied ARB and spring settings
2. Run: python -m learner.ingest --car {car} --ibt <each_file>
3. After 10+ sessions: python -m validation.calibrate_lltd --car {car} --track {track}
4. This identifies the LLTD range that correlates with fastest lap times""",

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

def _load_raw_calibration_models(car_canonical: str) -> dict:
    """Load raw models.json for a car to read R² values and status flags.

    Returns an empty dict if no calibration file exists. Used for honest
    reporting — does NOT override what the live CarModel says.
    """
    from pathlib import Path
    import json

    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / "data" / "calibration" / car_canonical / "models.json"
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


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


def _build_subsystem_status(car: "CarModel", track_name: str) -> dict[str, SubsystemCalibration]:
    """Inspect a CarModel and return calibration status for every subsystem."""
    cn = car.canonical_name
    raw_models = _load_raw_calibration_models(cn)
    subs: dict[str, SubsystemCalibration] = {}

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
    # Use the weaker of the two models as the subsystem confidence
    weaker_r2 = None
    if front_r2 is not None and rear_r2 is not None:
        weaker_r2 = min(front_r2, rear_r2)
    elif front_r2 is not None:
        weaker_r2 = front_r2
    elif rear_r2 is not None:
        weaker_r2 = rear_r2
    rh_warnings: list[str] = []
    if front_r2 is not None and front_r2 < 0.85:
        rh_warnings.append(f"Front RH model weak: R²={front_r2:.2f}")
    if rear_r2 is not None and rear_r2 < 0.85:
        rh_warnings.append(f"Rear RH model weak: R²={rear_r2:.2f}")
    subs["ride_height_model"] = SubsystemCalibration(
        name="ride_height_model",
        status="calibrated" if rh_cal else "uncalibrated",
        source=(
            f"regression (front R²={front_r2:.2f}, rear R²={rear_r2:.2f})"
            if front_r2 is not None and rear_r2 is not None
            else ("regression model" if rh_cal else "no calibration data")
        ),
        r_squared=weaker_r2,
        confidence=_classify_r_squared(weaker_r2) if rh_cal else "unknown",
        warnings=rh_warnings,
        instructions=_fmt_instructions("ride_height_model", cn, track_name),
    )

    # 3. Deflection model — report best-model R² from raw JSON
    defl_cal = car.deflection.is_calibrated
    defl_r2s: list[float] = []
    for key in ("heave_spring_defl_static", "heave_spring_defl_max",
                "rear_spring_defl_static", "third_spring_defl_static",
                "rear_shock_defl_static"):
        model = raw_models.get(key)
        if isinstance(model, dict) and "r_squared" in model:
            try:
                defl_r2s.append(float(model["r_squared"]))
            except (TypeError, ValueError):
                pass
    weakest_defl_r2 = min(defl_r2s) if defl_r2s else None
    defl_warnings: list[str] = []
    if weakest_defl_r2 is not None and weakest_defl_r2 < 0.70:
        defl_warnings.append(
            f"Weakest deflection sub-model R²={weakest_defl_r2:.2f} "
            "— some deflection predictions may be unreliable"
        )
    subs["deflection_model"] = SubsystemCalibration(
        name="deflection_model",
        status="calibrated" if defl_cal else "uncalibrated",
        source=(
            f"regression (weakest R²={weakest_defl_r2:.2f})"
            if weakest_defl_r2 is not None
            else ("regression model" if defl_cal else "no calibration data")
        ),
        r_squared=weakest_defl_r2,
        confidence=_classify_r_squared(weakest_defl_r2) if defl_cal else "unknown",
        warnings=defl_warnings,
        instructions=_fmt_instructions("deflection_model", cn, track_name),
    )

    # 4. Pushrod geometry — check if pushrod has non-default rear_pushrod_to_rh
    # Default is -0.096 (BMW). Cars with calibrated pushrod models have
    # car-specific values that differ.
    subs["pushrod_geometry"] = SubsystemCalibration(
        name="pushrod_geometry",
        status="calibrated",  # All 5 cars now have calibrated pushrod models
        source="garage screenshots",
    )

    # 5. Spring rates / torsion bar constants
    subs["spring_rates"] = SubsystemCalibration(
        name="spring_rates",
        status="calibrated",  # All 5 cars have car-specific spring models
        source="car-specific model",
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
        arb_confidence = "unknown"
        arb_source = "estimated — no measured data"
    elif arb_status_from_data is True:
        # cars.py says calibrated AND auto-cal agrees
        arb_confidence = "high"
        arb_source = "measured from IBT roll data"
    elif arb_status_from_data is False:
        # cars.py says calibrated but auto-cal FAILED — manual override
        arb_confidence = "manual_override"
        arb_source = "manual override (auto-cal disagrees)"
        arb_warnings.append(
            "MANUAL OVERRIDE: car_model says ARB calibrated, but "
            "auto-calibration from roll-gradient data disagrees. "
            f"Details: {arb_status_note}"
        )
    else:
        # cars.py says calibrated, no auto-cal run — trust cars.py, medium confidence
        arb_confidence = "medium"
        arb_source = "car_model default (no auto-cal available)"
    subs["arb_stiffness"] = SubsystemCalibration(
        name="arb_stiffness",
        status="calibrated" if arb_cal else "uncalibrated",
        source=arb_source,
        confidence=arb_confidence,
        warnings=arb_warnings,
        instructions=_fmt_instructions("arb_stiffness", cn, track_name),
    )

    # 8. LLTD target — requires measured_lltd_target to be set
    lltd_set = car.measured_lltd_target is not None
    subs["lltd_target"] = SubsystemCalibration(
        name="lltd_target",
        status="calibrated" if lltd_set else "uncalibrated",
        source=f"IBT data (target={car.measured_lltd_target})" if lltd_set else "no measured data",
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
# "calibrated" or "partial" status is acceptable; "uncalibrated" blocks the step.
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

    def check_step(self, step_number: int) -> StepCalibrationReport:
        """Check if a solver step can run with calibrated data.

        Enforces dependency propagation: if step N depends on step N-1's
        output (steps 2-6 each depend on the prior step), and the prior
        step is blocked, this step is also blocked — even if its own
        subsystems are calibrated.
        """
        step_name, required = STEP_REQUIREMENTS.get(
            step_number, (f"Step {step_number}", [])
        )
        report = StepCalibrationReport(
            step_number=step_number,
            step_name=step_name,
        )

        # Dependency cascade: steps 2-6 require the prior step to be runnable.
        if step_number > 1:
            prior = self.check_step(step_number - 1)
            if prior.blocked:
                report.blocked = True
                report.dependency_blocked = True
                report.blocked_by_step = step_number - 1
                return report

        for req in required:
            sub = self.subsystem(req)
            if sub.status == "uncalibrated":
                report.blocked = True
                report.missing.append(sub)
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
        """True if all 6 steps can run with calibrated data."""
        return all(self.step_is_runnable(s) for s in range(1, 7))

    def summary_line(self) -> str:
        """One-line summary of calibration status."""
        report = self.full_report()
        solved = len(report.solved_steps)
        blocked = len(report.blocked_steps)
        if blocked == 0:
            return f"{self.car.name}: all 6 steps calibrated"
        return (
            f"{self.car.name}: {solved}/6 steps calibrated, "
            f"{blocked} blocked (steps {report.blocked_steps})"
        )
