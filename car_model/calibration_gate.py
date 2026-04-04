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
1. Run 5+ laps at consistent pace, capturing full lateral-g sweep (0.5g to peak)
2. Run: python -m learner.ingest --car {car} --ibt <session.ibt> --all-laps
3. System extracts roll gain from camber vs lateral-g regression
4. Minimum 3 sessions for stable calibration""",

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

def _build_subsystem_status(car: "CarModel", track_name: str) -> dict[str, SubsystemCalibration]:
    """Inspect a CarModel and return calibration status for every subsystem."""
    cn = car.canonical_name
    subs: dict[str, SubsystemCalibration] = {}

    # 1. Aero compression
    # BMW/Ferrari/Cadillac/Porsche have calibrated values from IBT sessions.
    # Acura has ESTIMATE (no aero map calibration).
    aero_status = "calibrated"
    aero_source = "IBT-derived"
    if cn == "acura":
        aero_status = "uncalibrated"
        aero_source = "ESTIMATE — no IBT aero calibration"
    subs["aero_compression"] = SubsystemCalibration(
        name="aero_compression",
        status=aero_status,
        source=aero_source,
        instructions=_fmt_instructions("aero_compression", cn, track_name),
    )

    # 2. Ride height model
    rh_cal = car.ride_height_model.is_calibrated
    subs["ride_height_model"] = SubsystemCalibration(
        name="ride_height_model",
        status="calibrated" if rh_cal else "uncalibrated",
        source="regression model" if rh_cal else "no calibration data",
        instructions=_fmt_instructions("ride_height_model", cn, track_name),
    )

    # 3. Deflection model
    defl_cal = car.deflection.is_calibrated
    subs["deflection_model"] = SubsystemCalibration(
        name="deflection_model",
        status="calibrated" if defl_cal else "uncalibrated",
        source="regression model" if defl_cal else "no calibration data",
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
    subs["damper_zeta"] = SubsystemCalibration(
        name="damper_zeta",
        status="calibrated" if zeta_cal else "uncalibrated",
        source="IBT click-sweep" if zeta_cal else "not calibrated from IBT",
        instructions=_fmt_instructions("damper_zeta", cn, track_name),
    )

    # 7. ARB stiffness — check if the car has measured (not estimated) ARB stiffness
    # BMW: calibrated from LLTD back-calculation (73 sessions)
    # Cadillac: inherited from BMW (Dallara platform) — treat as partial
    # Ferrari/Porsche/Acura: estimated
    arb_calibrated_cars = {"bmw"}
    arb_partial_cars = {"cadillac"}
    if cn in arb_calibrated_cars:
        arb_status = "calibrated"
        arb_source = "LLTD back-calculation from IBT"
    elif cn in arb_partial_cars:
        arb_status = "partial"
        arb_source = "inherited from BMW (Dallara platform)"
    else:
        arb_status = "uncalibrated"
        arb_source = "estimated — no measured data"
    subs["arb_stiffness"] = SubsystemCalibration(
        name="arb_stiffness",
        status=arb_status,
        source=arb_source,
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

    # 9. Roll gains (wheel geometry)
    # Only BMW has IBT-calibrated roll gains; others use estimates
    roll_gain_calibrated_cars = {"bmw"}
    subs["roll_gains"] = SubsystemCalibration(
        name="roll_gains",
        status="calibrated" if cn in roll_gain_calibrated_cars else "uncalibrated",
        source="IBT-calibrated" if cn in roll_gain_calibrated_cars else "estimated",
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
