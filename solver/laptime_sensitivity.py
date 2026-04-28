"""Lap time sensitivity model.

For each key setup parameter, estimates: how many milliseconds of lap time
does ±1 unit change correspond to?

Physics chain:
1. Aero parameters → downforce → cornering speed → lap time
2. ARB → LLTD → balance → understeer → lap time
3. Ride height → aero DF → cornering speed → lap time
4. Heave spring → ride height variance → DF variance → RMS lap time
5. Torsion bar → wheel rate → LLTD → balance → lap time
6. Brake bias → braking efficiency → entry speed → lap time
7. Camber → contact patch → lateral grip → lap time

This helps answer: "Which parameter gives me the most time by getting it right?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from solver.arb_solver import ARBSolution
    from solver.damper_solver import DamperSolution
    from solver.heave_solver import HeaveSolution
    from solver.rake_solver import RakeSolution
    from solver.supporting_solver import SupportingSolution
    from solver.wheel_geometry_solver import WheelGeometrySolution
    from solver.corner_spring_solver import CornerSpringSolution
    from analyzer.extract import MeasuredState
    from track_model.profile import TrackProfile


# Ride height → DF sensitivity (N per mm)
RH_DF_SENSITIVITY_N_PER_MM = 0.5         # rear RH: ~0.5 N DF per mm
FRONT_RH_DF_SENSITIVITY_N_PER_MM = 1.2   # front RH: bigger effect on total DF

# Direct ride height lap time sensitivity (ms/mm)
# Research-calibrated: front RH 30-80 ms/mm (vortex burst cliff), rear 15-40 ms/mm
FRONT_RH_DIRECT_MS_PER_MM = 55.0    # midpoint of 30-80 range
REAR_RH_DIRECT_MS_PER_MM = 25.0     # midpoint of 15-40 range

# Physics constants
GTP_MASS_KG = 1050.0            # typical GTP car + driver + fuel mass
G = 9.81                        # m/s²
MU_TOTAL = 1.5                  # total grip coefficient (aero + mechanical)

# Fraction of lap limited by each mechanism
AERO_LIMITED_FRACTION = 0.35    # ~35% of lap where aero grip is binding
MECHANICAL_LIMITED_FRACTION = 0.45  # ~45% limited by mechanical balance

# Typical bottoming event cost (from community data + physics)
BOTTOMING_LAPTIME_COST_MS = 20.0  # ms per bottoming event
TYPICAL_BOTTOMING_EVENTS_PER_LAP = 0.5  # at soft spring limit

# RARB → LLTD → understeer → lap time chain
# Previous values (0.3 / 0.2) yielded ~180 ms/blade — far too high.
# RARB is a fine-tuning tool within an already-balanced window.
# Real-world iRacing experience: 1 blade ≈ 20-50 ms on a Sebring-length track.
LLTD_US_COEFF = 0.3            # deg understeer per 1% LLTD shift
US_LAPTIME_COEFF_S_PER_DEG = 0.04  # s/lap per degree understeer (calibrated to 5-15 ms/blade target)
RARB_LLTD_PER_BLADE = 0.008    # fraction LLTD change per RARB blade (absolute)
TORSION_LLTD_PER_NMM = 0.003   # fraction LLTD change per N/mm wheel rate (approximate)

# Research-calibrated sensitivity constants (March 2026)
# Heave spring: 20-60 ms per 10 N/mm (platform stability gatekeeper)
HEAVE_MS_PER_10NMM = 35.0          # midpoint of 20-60 range
# Corner spring (torsion bar OD): 15-40 ms/mm OD
TORSION_MS_PER_MM_OD = 25.0        # midpoint of 15-40 range
# Diff preload: 10-25 ms per 5 Nm
DIFF_MS_PER_5NM = 15.0             # midpoint of 10-25 range
# ARB blade (fine-tuning): 5-15 ms per click (NOT 33ms!)
ARB_BLADE_MS_PER_CLICK = 10.0      # midpoint of 5-15 range
# Damper clicks: 2-10 ms per click
DAMPER_MS_PER_CLICK = 5.0          # midpoint of 2-10 range


@dataclass
class ParameterSensitivity:
    """Lap time sensitivity for a single setup parameter."""
    parameter: str
    current_value: float
    units: str
    delta_per_unit_ms: float        # ms lap time change per +1 unit (+ = faster if you increase)
    confidence: str                 # "high" | "medium" | "low"
    mechanism: str                  # physics explanation
    justification: str = ""         # WHY this value was chosen (physics reason)
    telemetry_evidence: str = ""    # measured data backing the choice
    consequence_plus: str = ""      # what happens if you go +1-2 clicks/units
    consequence_minus: str = ""     # what happens if you go -1-2 clicks/units


@dataclass
class LaptimeSensitivityReport:
    """Lap time sensitivity for all key parameters, ranked by impact."""
    sensitivities: list[ParameterSensitivity] = field(default_factory=list)
    # (sorted by |delta_per_unit_ms|, descending)

    def top_n(self, n: int = 5) -> list[ParameterSensitivity]:
        """Return top N parameters by absolute sensitivity."""
        return sorted(
            self.sensitivities,
            key=lambda s: abs(s.delta_per_unit_ms),
            reverse=True,
        )[:n]

    def summary(self, width: int = 80) -> str:
        lines = [
            "=" * width,
            "  LAP TIME SENSITIVITY ANALYSIS  (ALL PARAMETERS)",
            "=" * width,
            "",
            "  Estimated ms/lap for ±1 unit change in each parameter.",
            "  Positive delta = faster if you increase the parameter.",
            "  Ranked by absolute impact (most critical first).",
            "",
        ]

        if not self.sensitivities:
            lines.append("  (no sensitivity data)")
            lines.append("=" * width)
            return "\n".join(lines)

        ranked = self.top_n(len(self.sensitivities))
        header = (
            f"  {'Parameter':<28s} {'Value':>8s} {'±ms/unit':>9s} "
            f"{'Conf':>6s}"
        )
        lines.append(header)
        lines.append("  " + "-" * (width - 4))

        for s in ranked:
            sign = "+" if s.delta_per_unit_ms > 0 else ""
            lines.append(
                f"  {s.parameter:<28s} {s.current_value:>8.2f} "
                f"{sign}{s.delta_per_unit_ms:>8.1f} "
                f"{s.confidence:>6s}"
            )
            # Truncate mechanism to fit width
            mech = s.mechanism[:width - 6]
            lines.append(f"    {mech}")

        lines.append("")
        lines.append("  INTERPRETATION")
        lines.append("  " + "-" * (width - 4))
        top5 = self.top_n(5)
        if top5:
            biggest = top5[0]
            lines.append(
                f"  Biggest lever: {biggest.parameter} "
                f"({abs(biggest.delta_per_unit_ms):.0f} ms/unit)"
            )
            lines.append("  Focus setup work on the top 2-3 parameters for best ROI.")
            lines.append("  Parameters below 30 ms/unit: marginal gains only.")

        lines.append("")
        lines.append(f"  Total parameters analyzed: {len(self.sensitivities)}")
        lines.append("=" * width)
        return "\n".join(lines)

    def justification_report(self, width: int = 80) -> str:
        """Generate a comprehensive parameter justification report.

        For every parameter, explains WHY the value was chosen,
        backed by telemetry evidence where available.
        """
        lines = [
            "=" * width,
            "  PARAMETER JUSTIFICATION  (ENGINEERING BRIEF)",
            "=" * width,
            "",
            "  Every parameter in the setup has a physics-based reason.",
            "  Telemetry evidence cited where measured data was available.",
            "",
        ]

        if not self.sensitivities:
            lines.append("  (no sensitivity data)")
            lines.append("=" * width)
            return "\n".join(lines)

        ranked = self.top_n(len(self.sensitivities))

        for i, s in enumerate(ranked, 1):
            sign = "+" if s.delta_per_unit_ms > 0 else ""
            lines.append(f"  {i:2d}. {s.parameter}")
            lines.append(f"      Value: {s.current_value:.2f} {s.units}")
            lines.append(f"      Sensitivity: {sign}{s.delta_per_unit_ms:.1f} ms/unit  [{s.confidence} confidence]")
            lines.append(f"      Physics: {s.mechanism[:width - 14]}")
            if s.justification:
                # Wrap justification at width
                just = s.justification
                while len(just) > width - 14:
                    lines.append(f"      WHY: {just[:width - 14]}")
                    just = just[width - 14:]
                lines.append(f"      WHY: {just}")
            if s.telemetry_evidence:
                lines.append(f"      EVIDENCE: {s.telemetry_evidence[:width - 16]}")
            if s.consequence_plus:
                lines.append(f"      +1-2 units: {s.consequence_plus[:width - 18]}")
            if s.consequence_minus:
                lines.append(f"      -1-2 units: {s.consequence_minus[:width - 18]}")
            lines.append("")

        lines.append("=" * width)
        return "\n".join(lines)


# ── Physics helper functions ─────────────────────────────────────────────────

def _df_to_laptime_delta(
    df_delta_n: float,
    track: "TrackProfile",
    aero_limited_fraction: float = AERO_LIMITED_FRACTION,
) -> float:
    """Convert a downforce change to lap time delta (ms).

    Physics:
        V_corner = sqrt(mu_total × g × r)
        delta_V / V = delta_mu / (2 × mu)
        delta_mu from DF: delta_mu = delta_DF / (m × g)
        delta_t = -(L / V_avg²) × delta_V_avg × aero_limited_fraction

    Args:
        df_delta_n: Change in downforce (N), positive = more DF
        track: Track profile
        aero_limited_fraction: Fraction of lap where aero grip is binding

    Returns:
        Lap time change in ms (negative = faster with more DF)
    """
    v_avg = (track.median_speed_kph or 160.0) / 3.6
    lap_length = track.track_length_m or 6000.0

    delta_mu = df_delta_n / (GTP_MASS_KG * G)
    delta_v = v_avg * delta_mu / (2.0 * MU_TOTAL)
    delta_t = -(lap_length / v_avg ** 2) * delta_v * aero_limited_fraction

    return delta_t * 1000.0  # ms


def _lltd_to_laptime_delta_ms(
    lltd_shift_fraction: float,
    track: "TrackProfile",
) -> float:
    """Convert LLTD shift to lap time delta.

    Physics chain:
        LLTD shift → understeer change → lap time

    Args:
        lltd_shift_fraction: LLTD change (fraction, e.g. 0.03 = 3%)
        track: Track profile

    Returns:
        Lap time change in ms (negative = faster)
    """
    # LLTD shift → understeer change
    us_change_deg = lltd_shift_fraction * 100.0 * LLTD_US_COEFF

    # Understeer → lap time (at current track — scale by track characteristics)
    # Typical: 1° understeer = ~0.2 s/lap at Sebring-length circuit
    # Scale by track length factor (Sebring = 6020m)
    track_scale = (track.track_length_m or 6000.0) / 6020.0

    laptime_change_s = us_change_deg * US_LAPTIME_COEFF_S_PER_DEG * track_scale
    return laptime_change_s * 1000.0  # ms


# ── Parameter sensitivity calculators ────────────────────────────────────────

def _rear_rh_sensitivity(
    step1: "RakeSolution",
    track: "TrackProfile",
    measured: "MeasuredState | None" = None,
) -> ParameterSensitivity:
    """Rear ride height: 1mm → ~0.5 N DF change → cornering speed → lap time."""
    # Rear RH primarily affects aero balance
    # Base sensitivity from research: 15-40 ms/mm (weight ~0.85)
    dt_ms = -REAR_RH_DIRECT_MS_PER_MM  # negative = lower RH costs time

    return ParameterSensitivity(
        parameter="rear_rh_mm",
        current_value=step1.dynamic_rear_rh_mm,
        units="mm",
        delta_per_unit_ms=round(dt_ms, 1),
        confidence="medium",
        mechanism=(
            f"1mm rear RH -> {abs(dt_ms):.0f}ms/lap "
            f"(research: 15-40 ms/mm, aero balance)"
        ),
        justification=(
            f"Rear RH {step1.dynamic_rear_rh_mm:.1f}mm sets rake angle "
            f"({step1.rake_static_mm:.1f}mm rake) which controls rear aero load distribution. "
            f"Value chosen to hit DF balance target while maintaining rear stability."
        ),
        telemetry_evidence=(
            f"dynamic rear RH = {step1.dynamic_rear_rh_mm:.1f}mm at speed"
            + (f", measured aero compression = {measured.aero_compression_rear_mm:.1f}mm"
               if measured and measured.aero_compression_rear_mm else "")
            + (f", measured rear RH std = {measured.rear_rh_std_mm:.2f}mm"
               if measured and measured.rear_rh_std_mm else "")
        ),
        consequence_plus="Higher rear = more rake = more rear DF but risk of front instability",
        consequence_minus="Lower rear = less rake = less rear DF, potential oversteer on exit",
    )


def _front_rh_sensitivity(
    step1: "RakeSolution",
    track: "TrackProfile",
    measured: "MeasuredState | None" = None,
) -> ParameterSensitivity:
    """Front ride height: 1mm → bigger DF effect (vortex sensitivity)."""
    # Base sensitivity from research: 30-80 ms/mm (weight 1.00)
    dt_ms = -FRONT_RH_DIRECT_MS_PER_MM  # negative = lower RH costs time

    # Additional cost: if approaching vortex burst, lap time can spike
    vortex_margin = step1.vortex_burst_margin_mm
    if vortex_margin < 3.0:
        # Close to vortex burst — penalty increases non-linearly
        vortex_factor = 1.0 + (3.0 - vortex_margin) * 1.0
        dt_ms *= vortex_factor

    return ParameterSensitivity(
        parameter="front_rh_mm",
        current_value=step1.dynamic_front_rh_mm,
        units="mm",
        delta_per_unit_ms=round(dt_ms, 1),
        confidence="medium",
        mechanism=(
            f"1mm front RH -> {abs(dt_ms):.0f}ms/lap "
            f"(research: 30-80 ms/mm, vortex margin {vortex_margin:.1f}mm)"
        ),
        justification=(
            f"Front RH {step1.dynamic_front_rh_mm:.1f}mm maintains "
            f"{vortex_margin:.1f}mm vortex burst margin while maximizing ground effect. "
            f"Lower = more DF but risk of aero stall on bumps/kerbs."
        ),
        telemetry_evidence=(
            f"dynamic front RH = {step1.dynamic_front_rh_mm:.1f}mm, vortex margin = {vortex_margin:.1f}mm"
            + (f", measured aero compression = {measured.aero_compression_front_mm:.1f}mm"
               if measured and measured.aero_compression_front_mm else "")
            + (f", measured front RH std = {measured.front_rh_std_mm:.2f}mm"
               if measured and measured.front_rh_std_mm else "")
            + (f", vortex burst events = {measured.vortex_burst_event_count}"
               if measured and measured.vortex_burst_event_count else "")
        ),
        consequence_plus="+1mm: lose ~55ms from reduced DF but gain stall safety",
        consequence_minus="-1mm: gain ~55ms from more DF but risk vortex burst on bumps",
    )


def _rear_arb_sensitivity(
    step4: "ARBSolution",
    track: "TrackProfile",
    measured: "MeasuredState | None" = None,
) -> ParameterSensitivity:
    """RARB blade (fine-tuning): 1 blade → ~3% LLTD → ~0.9° understeer → ~180ms at Sebring."""
    lltd_shift = RARB_LLTD_PER_BLADE  # fraction
    dt_ms = _lltd_to_laptime_delta_ms(lltd_shift, track)

    return ParameterSensitivity(
        parameter="rear_arb_blade",
        current_value=float(step4.rear_arb_blade_start),
        units="blade",
        delta_per_unit_ms=round(-dt_ms, 1),
        confidence="high",
        mechanism=(
            f"1 RARB blade -> {lltd_shift*100:.0f}% LLTD -> "
            f"{lltd_shift*100*LLTD_US_COEFF:.1f}deg US -> "
            f"{abs(dt_ms):.0f}ms/lap"
        ),
        justification=(
            f"RARB blade {step4.rear_arb_blade_start} chosen to hit LLTD target "
            f"{step4.lltd_target:.1%} (achieved {step4.lltd_achieved:.1%}). "
            f"Fine-tunes rear roll stiffness distribution for neutral balance."
        ),
        telemetry_evidence=(
            f"LLTD = {step4.lltd_achieved:.1%}, target = {step4.lltd_target:.1%}"
            + (f", measured LLTD proxy = {measured.lltd_measured:.1%}"
               if measured and measured.lltd_measured else "")
            + (f", measured roll gradient = {measured.roll_gradient_measured_deg_per_g:.3f}deg/g"
               if measured and measured.roll_gradient_measured_deg_per_g else "")
            + (f", body roll p95 = {measured.body_roll_p95_deg:.2f}deg"
               if measured and measured.body_roll_p95_deg else "")
        ),
        consequence_plus="+1 blade: stiffer rear -> more understeer, safer but slower mid-corner",
        consequence_minus="-1 blade: softer rear -> more oversteer, faster rotation but less stable",
    )


def _front_heave_sensitivity(
    step2: "HeaveSolution",
    track: "TrackProfile",
    measured: "MeasuredState | None" = None,
) -> "ParameterSensitivity | None":
    """Front heave (weight 0.75): 10 N/mm → bottoming margin → DF stability.

    W6.1 (F-LT-1): GT3 cars skip Step 2 (HeaveSolution.null(), present=False);
    return None so the caller can filter the entry out of the sensitivity
    table. A GT3-shaped corner-spring sensitivity is a Phase 3 follow-on
    (TODO(W6.x)).
    """
    if not getattr(step2, "present", True):
        return None
    # Softer heave → more bottoming → DF instability → lap time cost
    # Each bottoming event ≈ 20ms, at soft limit ~0.5 events/lap
    # Each 10 N/mm reduction ≈ 0.5 extra bottoming events → 10ms
    # But also softer = more mechanical grip on non-bottoming corners
    # Net effect per 10 N/mm softening ≈ -5ms (slight benefit until bottoming)
    unit_step = 10.0  # N/mm
    bottoming_events_change = 1.5  # estimated bottoming events per 10 N/mm (research-calibrated)
    bottoming_cost = bottoming_events_change * BOTTOMING_LAPTIME_COST_MS  # ms

    # Softer spring = better mechanical grip but worse platform stability
    # Research: 20-60 ms per 10 N/mm total sensitivity (weight 0.75)
    grip_benefit = -5.0  # ms (grip benefit from softer spring)

    net_change = bottoming_cost + grip_benefit  # positive = faster to stiffen

    # Current margin context
    margin = step2.front_bottoming_margin_mm

    return ParameterSensitivity(
        parameter="front_heave_nmm",
        current_value=step2.front_heave_nmm,
        units="N/mm",
        delta_per_unit_ms=round(net_change / unit_step, 1),
        confidence="low",
        mechanism=(
            f"10 N/mm change -> {bottoming_events_change:.1f} bottoming events "
            f"(margin={margin:.1f}mm) -> {net_change:.0f}ms/lap net (per 10 N/mm)"
        ),
        justification=(
            f"Front heave {step2.front_heave_nmm:.0f} N/mm maintains "
            f"{margin:.1f}mm bottoming margin at p99 excursion "
            f"{step2.front_excursion_at_rate_mm:.1f}mm. "
            f"Platform stability gatekeeper: too soft = bottoming, too stiff = no compliance."
        ),
        telemetry_evidence=(
            f"front excursion p99 = {step2.front_excursion_at_rate_mm:.1f}mm, "
            f"bottoming margin = {margin:.1f}mm, "
            f"travel margin = {step2.travel_margin_front_mm:.1f}mm"
            + (f", measured shock vel p99 = {measured.front_shock_vel_p99_mps*1000:.0f}mm/s"
               if measured and measured.front_shock_vel_p99_mps else "")
            + (f", measured excursion = {measured.front_rh_excursion_measured_mm:.1f}mm"
               if measured and measured.front_rh_excursion_measured_mm else "")
            + (f", heave travel used = {measured.front_heave_travel_used_pct:.0f}%"
               if measured and measured.front_heave_travel_used_pct else "")
        ),
        consequence_plus="+10 N/mm: stiffer platform, less bottoming risk but more kerb harshness",
        consequence_minus="-10 N/mm: softer, more mechanical grip but risk of bottoming/aero instability",
    )


def _torsion_bar_sensitivity(
    step3: "CornerSpringSolution",
    step4: "ARBSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Torsion bar OD (weight 0.55): 0.1mm → ~0.8 N/mm wheel rate → LLTD → balance → lap time."""
    # OD change → wheel rate change via k = C * OD^4
    # At OD=13.9mm, C≈0.0008036: dk/dOD = 4 * C * OD^3 ≈ 4 * 0.0008036 * 13.9^3 ≈ 8.6 N/mm/mm
    # 0.1mm OD change → ~0.86 N/mm wheel rate change
    wheel_rate_per_od_01 = 0.86  # N/mm per 0.1mm OD change

    # Wheel rate → LLTD (approximate: each N/mm front wheel rate = small LLTD increase)
    lltd_per_nmm = TORSION_LLTD_PER_NMM  # fraction LLTD per N/mm
    lltd_shift = wheel_rate_per_od_01 * lltd_per_nmm

    dt_ms = _lltd_to_laptime_delta_ms(lltd_shift, track)

    # Scale to 0.1mm unit (the garage increment)
    dt_ms_per_01mm = dt_ms  # already computed for 0.1mm change
    dt_ms_per_mm = dt_ms_per_01mm / 0.1  # scale to per mm for consistent units

    return ParameterSensitivity(
        parameter="torsion_bar_od_mm",
        current_value=step3.front_torsion_od_mm,
        units="mm",
        delta_per_unit_ms=round(dt_ms_per_mm, 1),
        confidence="medium",
        mechanism=(
            f"1mm OD -> {wheel_rate_per_od_01*10:.1f} N/mm wheel rate -> "
            f"{lltd_shift*1000:.2f}% LLTD -> {abs(dt_ms_per_mm):.0f}ms/lap per mm "
            f"(garage step: 0.1mm = {abs(dt_ms_per_01mm):.0f}ms)"
        ),
        justification=(
            f"Torsion OD {step3.front_torsion_od_mm:.2f}mm sets front wheel rate "
            f"to match target LLTD. Front wheel rate = {step3.front_wheel_rate_nmm:.1f} N/mm "
            f"from k = C * OD^4."
        ),
        telemetry_evidence=f"front wheel rate = {step3.front_wheel_rate_nmm:.1f} N/mm",
        consequence_plus="+0.1mm OD: stiffer front -> more front roll stiffness -> slight understeer shift",
        consequence_minus="-0.1mm OD: softer front -> more front compliance -> slight oversteer shift",
    )


def _front_roll_spring_sensitivity(
    step3: "CornerSpringSolution",
    step4: "ARBSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Front roll spring (weight 0.55): 10 N/mm -> LLTD -> balance -> lap time."""
    # Each N/mm front roll spring rate ≈ small LLTD increase
    lltd_per_nmm = TORSION_LLTD_PER_NMM
    lltd_shift_per_10 = 10.0 * lltd_per_nmm

    dt_ms_per_10 = _lltd_to_laptime_delta_ms(lltd_shift_per_10, track)
    dt_ms_per_nmm = dt_ms_per_10 / 10.0

    return ParameterSensitivity(
        parameter="front_roll_spring_nmm",
        current_value=step3.front_roll_spring_nmm,
        units="N/mm",
        delta_per_unit_ms=round(dt_ms_per_nmm, 1),
        confidence="medium",
        mechanism=(
            f"10 N/mm roll spring -> {lltd_shift_per_10*100:.2f}% LLTD -> "
            f"{abs(dt_ms_per_10):.0f}ms/lap per 10 N/mm step"
        ),
        justification=(
            f"Front roll spring {step3.front_roll_spring_nmm:.0f} N/mm sets front roll "
            f"stiffness to target LLTD. Front wheel rate = {step3.front_wheel_rate_nmm:.1f} N/mm."
        ),
        telemetry_evidence=f"front wheel rate = {step3.front_wheel_rate_nmm:.1f} N/mm",
        consequence_plus="+10 N/mm: stiffer front -> more front roll stiffness -> understeer shift",
        consequence_minus="-10 N/mm: softer front -> more compliance -> oversteer shift",
    )


def _brake_bias_sensitivity(
    supporting_bias_pct: float,
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Brake bias: 1% → entry stability → ~50ms/lap at balanced circuit."""
    # Brake bias deviation affects: braking efficiency, trail-braking stability,
    # and corner entry balance. Effect is roughly symmetric around optimal.
    # Community data: ~50ms/lap per 1% at a typical circuit.
    track_scale = (track.track_length_m or 6000.0) / 6020.0
    dt_ms = 50.0 * track_scale  # ms per 1% bias change

    return ParameterSensitivity(
        parameter="brake_bias_pct",
        current_value=supporting_bias_pct,
        units="%",
        delta_per_unit_ms=round(-dt_ms, 1),
        confidence="medium",
        mechanism=(
            f"1% bias deviation -> braking efficiency, entry balance "
            f"-> ~{dt_ms:.0f}ms/lap (symmetric around optimal)"
        ),
        justification=(
            f"Brake bias {supporting_bias_pct:.1f}% derived from weight transfer "
            f"distribution under braking. Optimizes front/rear lock balance for "
            f"maximum braking efficiency and trail-brake stability."
        ),
        telemetry_evidence="derived from weight transfer physics + driver trail-brake depth",
        consequence_plus="+1%: more front bias -> earlier front lock, shorter braking but less trail-brake",
        consequence_minus="-1%: more rear bias -> rear instability under braking, longer braking zone",
    )


def _rear_camber_sensitivity(
    step5: "WheelGeometrySolution",
    track: "TrackProfile",
    measured: "MeasuredState | None" = None,
) -> ParameterSensitivity:
    """Rear camber: 0.1° → contact patch → lateral grip → ~30ms/lap."""
    track_scale = (track.track_length_m or 6000.0) / 6020.0
    dt_ms_per_deg = 5.0 * track_scale  # ms per 0.1deg (research: 10-25ms per 0.3° (positive = more camber is faster)

    return ParameterSensitivity(
        parameter="rear_camber_deg",
        current_value=step5.rear_camber_deg,
        units="deg",
        delta_per_unit_ms=round(dt_ms_per_deg, 1),
        confidence="low",
        mechanism=(
            f"0.1 deg camber -> contact patch -> lateral grip "
            f"-> ~{dt_ms_per_deg:.0f}ms/lap (per 0.1 deg)"
        ),
        justification=(
            f"Rear camber {step5.rear_camber_deg:+.1f}deg optimizes rear contact patch "
            f"under cornering load. Balances peak grip vs tyre wear."
        ),
        telemetry_evidence=(
            "derived from lateral grip model + tyre wear analysis"
            + (f", measured peak lat g = {measured.peak_lat_g_measured:.2f}g"
               if measured and measured.peak_lat_g_measured else "")
            + (f", measured understeer = {measured.understeer_mean_deg:.2f}deg"
               if measured and measured.understeer_mean_deg else "")
        ),
        consequence_plus="+0.1deg (less negative): less peak grip, more even tyre wear",
        consequence_minus="-0.1deg (more negative): more peak lateral grip, faster inner edge wear",
    )


# ── Additional sensitivity calculators (comprehensive coverage) ─────────────

def _rear_third_sensitivity(
    step2: "HeaveSolution",
    track: "TrackProfile",
    measured: "MeasuredState | None" = None,
) -> "ParameterSensitivity | None":
    """Rear third spring: platform stability for rear axle.

    W6.1 (F-LT-1): GT3 cars skip Step 2; return None on null Step 2.
    """
    if not getattr(step2, "present", True):
        return None
    track_scale = (track.track_length_m or 6000.0) / 6020.0
    dt_ms = HEAVE_MS_PER_10NMM * 0.7 * track_scale  # rear third slightly less critical than front heave
    margin = step2.rear_bottoming_margin_mm if hasattr(step2, "rear_bottoming_margin_mm") else 40.0

    return ParameterSensitivity(
        parameter="rear_third_nmm",
        current_value=step2.rear_third_nmm,
        units="N/mm",
        delta_per_unit_ms=round(dt_ms / 10.0, 1),
        confidence="low",
        mechanism=f"10 N/mm -> rear platform stability -> ~{dt_ms:.0f}ms/lap per 10 N/mm",
        justification=(
            f"Rear third {step2.rear_third_nmm:.0f} N/mm controls rear axle heave response. "
            f"Rear bottoming margin = {margin:.1f}mm. Supports rear aero platform stability."
        ),
        telemetry_evidence=(
            f"rear bottoming margin = {margin:.1f}mm"
            + (f", measured rear shock vel p99 = {measured.rear_shock_vel_p99_mps*1000:.0f}mm/s"
               if measured and measured.rear_shock_vel_p99_mps else "")
            + (f", measured rear excursion = {measured.rear_rh_excursion_measured_mm:.1f}mm"
               if measured and measured.rear_rh_excursion_measured_mm else "")
        ),
        consequence_plus="+10 N/mm: stiffer rear platform, less rear squat under power",
        consequence_minus="-10 N/mm: softer rear, more traction but risk of rear bottoming",
    )


def _rear_spring_sensitivity(
    step3: "CornerSpringSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Rear coil spring rate: wheel rate → LLTD → balance."""
    track_scale = (track.track_length_m or 6000.0) / 6020.0
    lltd_per_10nmm = 0.003 * 10  # approx fraction LLTD per 10 N/mm rear wheel rate
    dt_ms = abs(_lltd_to_laptime_delta_ms(lltd_per_10nmm, track))
    rear_wr = step3.rear_wheel_rate_nmm if hasattr(step3, "rear_wheel_rate_nmm") else step3.rear_spring_rate_nmm * 0.5

    return ParameterSensitivity(
        parameter="rear_spring_nmm",
        current_value=step3.rear_spring_rate_nmm,
        units="N/mm",
        delta_per_unit_ms=round(dt_ms / 10.0, 1),
        confidence="medium",
        mechanism=f"10 N/mm rear spring -> rear wheel rate -> LLTD shift -> ~{dt_ms:.0f}ms per 10 N/mm",
        justification=(
            f"Rear spring {step3.rear_spring_rate_nmm:.0f} N/mm sets rear wheel rate "
            f"({rear_wr:.1f} N/mm) to match target LLTD distribution. "
            f"Paired with front torsion bar for balance."
        ),
        telemetry_evidence=f"rear wheel rate ~{rear_wr:.1f} N/mm",
        consequence_plus="+10 N/mm: stiffer rear corner -> more understeer (rear resists roll more)",
        consequence_minus="-10 N/mm: softer rear corner -> more oversteer tendency",
    )


def _front_pushrod_sensitivity(
    step1: "RakeSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Front pushrod offset: affects static front RH and aero performance."""
    dt_ms = FRONT_RH_DIRECT_MS_PER_MM * 0.3  # pushrod moves RH ~0.3mm per mm offset

    return ParameterSensitivity(
        parameter="front_pushrod_mm",
        current_value=step1.front_pushrod_offset_mm,
        units="mm",
        delta_per_unit_ms=round(-dt_ms * 0.3, 1),
        confidence="medium",
        mechanism=f"1mm pushrod -> ~0.3mm front RH change -> ~{dt_ms*0.3:.0f}ms/lap",
        justification=(
            f"Front pushrod {step1.front_pushrod_offset_mm:+.1f}mm sets static front RH "
            f"to {step1.static_front_rh_mm:.1f}mm. Adjusts front aero platform height."
        ),
        telemetry_evidence=f"static front RH = {step1.static_front_rh_mm:.1f}mm",
        consequence_plus="+1mm: raises front slightly, more stall safety but less DF",
        consequence_minus="-1mm: lowers front, more DF but less bump clearance",
    )


def _rear_pushrod_sensitivity(
    step1: "RakeSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Rear pushrod offset: affects static rear RH and rake."""
    dt_ms = REAR_RH_DIRECT_MS_PER_MM * 0.3

    return ParameterSensitivity(
        parameter="rear_pushrod_mm",
        current_value=step1.rear_pushrod_offset_mm,
        units="mm",
        delta_per_unit_ms=round(-dt_ms * 0.3, 1),
        confidence="medium",
        mechanism=f"1mm pushrod -> ~0.3mm rear RH change -> ~{dt_ms*0.3:.0f}ms/lap",
        justification=(
            f"Rear pushrod {step1.rear_pushrod_offset_mm:+.1f}mm sets static rear RH "
            f"to {step1.static_rear_rh_mm:.1f}mm. Controls rake angle with front pushrod."
        ),
        telemetry_evidence=f"static rear RH = {step1.static_rear_rh_mm:.1f}mm, rake = {step1.rake_static_mm:.1f}mm",
        consequence_plus="+1mm: more rake, changes rear aero balance",
        consequence_minus="-1mm: less rake, more front-biased DF distribution",
    )


def _front_arb_blade_sensitivity(
    step4: "ARBSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Front ARB blade: usually locked, fine-tunes front roll stiffness."""
    lltd_shift = RARB_LLTD_PER_BLADE * 0.8  # front ARB slightly less effect than rear
    dt_ms = abs(_lltd_to_laptime_delta_ms(lltd_shift, track))

    return ParameterSensitivity(
        parameter="front_arb_blade",
        current_value=float(step4.front_arb_blade_start),
        units="blade",
        delta_per_unit_ms=round(dt_ms, 1),
        confidence="medium",
        mechanism=f"1 FARB blade -> ~{lltd_shift*100:.1f}% LLTD -> ~{dt_ms:.0f}ms/lap",
        justification=(
            f"Front ARB blade {step4.front_arb_blade_start} (typically locked). "
            f"Front ARB primarily provides initial turn-in response. "
            f"Size {step4.front_arb_size} chosen for front roll stiffness target."
        ),
        telemetry_evidence=f"FARB size = {step4.front_arb_size}, blade = {step4.front_arb_blade_start}",
        consequence_plus="+1 blade: stiffer front roll -> sharper turn-in but less front compliance",
        consequence_minus="-1 blade: softer front roll -> smoother but less responsive",
    )


def _front_arb_size_sensitivity(
    step4: "ARBSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Front ARB size: coarse front roll stiffness setting."""
    dt_ms = ARB_BLADE_MS_PER_CLICK * 3  # size change ~3x blade effect

    return ParameterSensitivity(
        parameter="front_arb_size",
        current_value=float(getattr(step4, "front_arb_size_idx", 0)),
        units="size",
        delta_per_unit_ms=round(dt_ms, 1),
        confidence="low",
        mechanism=f"1 size step -> ~3 blades worth of roll stiffness -> ~{dt_ms:.0f}ms/lap",
        justification=(
            f"Front ARB size '{step4.front_arb_size}' sets base front roll stiffness. "
            f"Coarse adjustment; blade is the fine-tuning control."
        ),
        telemetry_evidence=f"FARB size = {step4.front_arb_size}",
        consequence_plus="+1 size: significantly stiffer front, major LLTD shift toward understeer",
        consequence_minus="-1 size: significantly softer front, major LLTD shift toward oversteer",
    )


def _rear_arb_size_sensitivity(
    step4: "ARBSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Rear ARB size: coarse rear roll stiffness setting."""
    dt_ms = ARB_BLADE_MS_PER_CLICK * 3

    return ParameterSensitivity(
        parameter="rear_arb_size",
        current_value=float(getattr(step4, "rear_arb_size_idx", 0)),
        units="size",
        delta_per_unit_ms=round(dt_ms, 1),
        confidence="low",
        mechanism=f"1 size step -> ~3 blades worth of roll stiffness -> ~{dt_ms:.0f}ms/lap",
        justification=(
            f"Rear ARB size '{step4.rear_arb_size}' sets base rear roll stiffness. "
            f"Paired with front ARB size for overall roll stiffness distribution."
        ),
        telemetry_evidence=f"RARB size = {step4.rear_arb_size}",
        consequence_plus="+1 size: much stiffer rear -> strong understeer tendency",
        consequence_minus="-1 size: much softer rear -> strong oversteer tendency",
    )


def _front_camber_sensitivity(
    step5: "WheelGeometrySolution",
    track: "TrackProfile",
    measured: "MeasuredState | None" = None,
) -> ParameterSensitivity:
    """Front camber: contact patch optimization for front lateral grip."""
    track_scale = (track.track_length_m or 6000.0) / 6020.0
    dt_ms_per_deg = 6.0 * track_scale  # front camber slightly more sensitive than rear

    return ParameterSensitivity(
        parameter="front_camber_deg",
        current_value=step5.front_camber_deg,
        units="deg",
        delta_per_unit_ms=round(dt_ms_per_deg, 1),
        confidence="low",
        mechanism=f"0.1 deg -> front contact patch -> lateral grip -> ~{dt_ms_per_deg:.0f}ms/lap per 0.1 deg",
        justification=(
            f"Front camber {step5.front_camber_deg:+.1f}deg maximizes front tyre contact patch "
            f"under cornering load for turn-in bite and mid-corner grip."
        ),
        telemetry_evidence=(
            "derived from lateral grip model"
            + (f", measured peak lat g = {measured.peak_lat_g_measured:.2f}g"
               if measured and measured.peak_lat_g_measured else "")
            + (f", measured understeer low = {measured.understeer_low_speed_deg:.2f}deg"
               if measured and measured.understeer_low_speed_deg else "")
        ),
        consequence_plus="+0.1deg (less negative): less peak front grip, slower turn-in",
        consequence_minus="-0.1deg (more negative): more front grip, faster inner edge wear",
    )


def _front_toe_sensitivity(
    step5: "WheelGeometrySolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Front toe: stability vs turn-in response."""
    track_scale = (track.track_length_m or 6000.0) / 6020.0
    dt_ms = 3.0 * track_scale  # toe is a fine-tuning parameter

    return ParameterSensitivity(
        parameter="front_toe_mm",
        current_value=step5.front_toe_mm,
        units="mm",
        delta_per_unit_ms=round(-dt_ms, 1),
        confidence="low",
        mechanism=f"1mm toe -> straight-line drag + turn-in response -> ~{dt_ms:.0f}ms/lap",
        justification=(
            f"Front toe {step5.front_toe_mm:+.1f}mm: toe-out aids turn-in response, "
            f"toe-in adds straight-line stability. Balance of drag vs responsiveness."
        ),
        telemetry_evidence="geometry solver output",
        consequence_plus="+1mm (more toe-in): more stable but slower turn-in, slight drag increase",
        consequence_minus="-1mm (more toe-out): quicker turn-in but less stable on straights",
    )


def _rear_toe_sensitivity(
    step5: "WheelGeometrySolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Rear toe: rear stability under braking and power."""
    track_scale = (track.track_length_m or 6000.0) / 6020.0
    dt_ms = 4.0 * track_scale

    return ParameterSensitivity(
        parameter="rear_toe_mm",
        current_value=step5.rear_toe_mm,
        units="mm",
        delta_per_unit_ms=round(-dt_ms, 1),
        confidence="low",
        mechanism=f"1mm rear toe -> rear stability + drag -> ~{dt_ms:.0f}ms/lap",
        justification=(
            f"Rear toe {step5.rear_toe_mm:+.1f}mm: slight toe-in stabilizes rear under braking "
            f"and power application. Too much = drag penalty on straights."
        ),
        telemetry_evidence="geometry solver output",
        consequence_plus="+1mm (more toe-in): more rear stability but drag penalty",
        consequence_minus="-1mm (less toe-in): less drag but risk of rear instability",
    )


def _wing_angle_sensitivity(
    wing: float,
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Wing angle: massive DF/drag tradeoff."""
    # 1 degree wing ≈ 80-200 N DF depending on car, ~150ms/deg at aero circuits
    track_scale = (track.track_length_m or 6000.0) / 6020.0
    median_speed = track.median_speed_kph or 160.0
    # Higher speed circuits are more wing-sensitive
    speed_factor = (median_speed / 160.0) ** 2
    dt_ms = 120.0 * track_scale * speed_factor

    return ParameterSensitivity(
        parameter="wing_angle_deg",
        current_value=wing,
        units="deg",
        delta_per_unit_ms=round(-dt_ms, 1),
        confidence="medium",
        mechanism=f"1 deg wing -> DF/drag tradeoff -> ~{dt_ms:.0f}ms/lap (speed-dependent)",
        justification=(
            f"Wing {wing:.0f}deg: balances downforce for corners vs drag on straights. "
            f"Track median speed {median_speed:.0f}kph determines optimal tradeoff point."
        ),
        telemetry_evidence=f"median speed = {median_speed:.0f}kph, track length = {track.track_length_m:.0f}m",
        consequence_plus="+1 deg: more DF, faster corners but slower straights",
        consequence_minus="-1 deg: less DF, faster straights but slower corners",
    )


def _heave_perch_sensitivity(
    step2: "HeaveSolution",
    track: "TrackProfile",
) -> "ParameterSensitivity | None":
    """Front heave perch offset: dependent variable, controls preload and slider position.

    W6.1 (F-LT-2): GT3 cars skip Step 2; return None on null Step 2.
    """
    if not getattr(step2, "present", True):
        return None
    return ParameterSensitivity(
        parameter="front_heave_perch_mm",
        current_value=step2.perch_offset_front_mm,
        units="mm",
        delta_per_unit_ms=round(-5.0, 1),
        confidence="medium",
        mechanism="1mm perch -> slider position change -> ride height and spring preload",
        justification=(
            f"Front heave perch {step2.perch_offset_front_mm:+.1f}mm: DEPENDENT variable computed from "
            f"front heave rate {step2.front_heave_nmm:.0f} N/mm + pushrod offset + target RH. "
            f"Sets heave spring preload and slider static position."
        ),
        telemetry_evidence=f"slider static = {step2.slider_static_front_mm:.1f}mm",
        consequence_plus="+1mm: raises slider position, changes spring preload point",
        consequence_minus="-1mm: lowers slider position, risk of slider bottoming",
    )


def _rear_third_perch_sensitivity(
    step2: "HeaveSolution",
    track: "TrackProfile",
) -> "ParameterSensitivity | None":
    """Rear third perch offset: dependent variable.

    W6.1 (F-LT-2): GT3 cars skip Step 2; return None on null Step 2.
    """
    if not getattr(step2, "present", True):
        return None
    return ParameterSensitivity(
        parameter="rear_third_perch_mm",
        current_value=step2.perch_offset_rear_mm,
        units="mm",
        delta_per_unit_ms=round(-3.0, 1),
        confidence="medium",
        mechanism="1mm perch -> rear heave preload + ride height adjustment",
        justification=(
            f"Rear third perch {step2.perch_offset_rear_mm:+.1f}mm: DEPENDENT variable computed from "
            f"rear third rate {step2.rear_third_nmm:.0f} N/mm + pushrod offset + target rear RH."
        ),
        telemetry_evidence=f"rear third spring rate = {step2.rear_third_nmm:.0f} N/mm",
        consequence_plus="+1mm: adjusts rear preload and static height",
        consequence_minus="-1mm: adjusts rear preload and static height",
    )


def _rear_spring_perch_sensitivity(
    step3: "CornerSpringSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Rear coil spring perch: sets corner spring preload."""
    return ParameterSensitivity(
        parameter="rear_spring_perch_mm",
        current_value=step3.rear_spring_perch_mm,
        units="mm",
        delta_per_unit_ms=round(-2.0, 1),
        confidence="low",
        mechanism="1mm perch -> rear corner spring preload adjustment",
        justification=(
            f"Rear spring perch {step3.rear_spring_perch_mm:.1f}mm: DEPENDENT variable. "
            f"Sets rear coil spring preload for target rear corner ride height."
        ),
        telemetry_evidence=f"rear spring rate = {step3.rear_spring_rate_nmm:.0f} N/mm",
        consequence_plus="+1mm: more preload, slightly stiffer initial response",
        consequence_minus="-1mm: less preload, softer initial response",
    )


def _diff_preload_sensitivity(
    supporting: "SupportingSolution",
    track: "TrackProfile",
    measured: "MeasuredState | None" = None,
) -> ParameterSensitivity:
    """Diff preload: traction vs rotation."""
    track_scale = (track.track_length_m or 6000.0) / 6020.0
    dt_ms = DIFF_MS_PER_5NM * track_scale

    return ParameterSensitivity(
        parameter="diff_preload_nm",
        current_value=supporting.diff_preload_nm,
        units="Nm",
        delta_per_unit_ms=round(dt_ms / 5.0, 1),
        confidence="medium",
        mechanism=f"5 Nm preload -> traction vs rotation -> ~{dt_ms:.0f}ms per 5 Nm",
        justification=(
            f"Diff preload {supporting.diff_preload_nm:.0f} Nm: {supporting.diff_reasoning}"
        ),
        telemetry_evidence=(
            "derived from driver style + traction demand analysis"
            + (f", measured rear power slip p95 = {measured.rear_power_slip_ratio_p95:.3f}"
               if measured and measured.rear_power_slip_ratio_p95 else "")
            + (f", measured body slip p95 = {measured.body_slip_p95_deg:.2f}deg"
               if measured and measured.body_slip_p95_deg else "")
        ),
        consequence_plus="+5 Nm: more locked -> better traction but tighter rotation",
        consequence_minus="-5 Nm: more open -> easier rotation but less traction on exit",
    )


def _diff_coast_ramp_sensitivity(
    supporting: "SupportingSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Diff coast ramp: trail-braking rotation."""
    track_scale = (track.track_length_m or 6000.0) / 6020.0
    dt_ms = 8.0 * track_scale  # 8ms per 5deg ramp change

    return ParameterSensitivity(
        parameter="diff_coast_ramp",
        current_value=float(supporting.diff_ramp_coast),
        units="deg",
        delta_per_unit_ms=round(-dt_ms / 5.0, 1),
        confidence="low",
        mechanism=f"5 deg coast ramp -> trail-brake rotation -> ~{dt_ms:.0f}ms per 5 deg",
        justification=(
            f"Coast ramp {supporting.diff_ramp_coast}deg: lower angle = more locking on decel. "
            f"Matched to driver trail-brake depth for controlled entry rotation."
        ),
        telemetry_evidence="matched to driver trail-brake classification",
        consequence_plus="+5 deg: less coast locking -> more rotation on entry but less stability",
        consequence_minus="-5 deg: more coast locking -> more stable entry but tighter rotation",
    )


def _diff_drive_ramp_sensitivity(
    supporting: "SupportingSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Diff drive ramp: power-on traction."""
    track_scale = (track.track_length_m or 6000.0) / 6020.0
    dt_ms = 6.0 * track_scale

    return ParameterSensitivity(
        parameter="diff_drive_ramp",
        current_value=float(supporting.diff_ramp_drive),
        units="deg",
        delta_per_unit_ms=round(dt_ms / 5.0, 1),
        confidence="low",
        mechanism=f"5 deg drive ramp -> power application behavior -> ~{dt_ms:.0f}ms per 5 deg",
        justification=(
            f"Drive ramp {supporting.diff_ramp_drive}deg: lower angle = more locking on accel. "
            f"Matched to driver throttle progressiveness."
        ),
        telemetry_evidence="matched to driver throttle classification",
        consequence_plus="+5 deg: less drive locking -> smoother power but less traction",
        consequence_minus="-5 deg: more drive locking -> more traction but risk of snap oversteer",
    )


def _diff_clutch_plates_sensitivity(
    supporting: "SupportingSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Diff clutch plates: overall diff aggression."""
    dt_ms = 3.0  # plates are a coarse adjustment

    return ParameterSensitivity(
        parameter="diff_clutch_plates",
        current_value=float(supporting.diff_clutch_plates),
        units="plates",
        delta_per_unit_ms=round(dt_ms, 1),
        confidence="low",
        mechanism=f"1 plate -> overall diff lock strength -> ~{dt_ms:.0f}ms/plate",
        justification=(
            f"Clutch plates {supporting.diff_clutch_plates}: more plates = higher max lock percentage. "
            f"Sets the ceiling for how aggressively the diff can lock."
        ),
        telemetry_evidence="standard physics model",
        consequence_plus="+1 plate: higher max lock, more aggressive diff behavior overall",
        consequence_minus="-1 plate: lower max lock, gentler diff behavior",
    )


def _tc_gain_sensitivity(
    supporting: "SupportingSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """TC gain: intervention aggressiveness."""
    track_scale = (track.track_length_m or 6000.0) / 6020.0
    dt_ms = 15.0 * track_scale  # TC can be worth 10-20ms per click

    return ParameterSensitivity(
        parameter="tc_gain",
        current_value=float(supporting.tc_gain),
        units="click",
        delta_per_unit_ms=round(-dt_ms, 1),
        confidence="low",
        mechanism=f"1 click TC gain -> intervention threshold -> ~{dt_ms:.0f}ms/click",
        justification=(
            f"TC gain {supporting.tc_gain}: {supporting.tc_reasoning}"
        ),
        telemetry_evidence="derived from rear slip analysis + traction demand",
        consequence_plus="+1 click: more intervention -> safer but slower exits",
        consequence_minus="-1 click: less intervention -> faster exits but risk of wheelspin",
    )


def _tc_slip_sensitivity(
    supporting: "SupportingSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """TC slip: allowed slip angle before intervention."""
    dt_ms = 8.0  # slip setting is secondary to gain

    return ParameterSensitivity(
        parameter="tc_slip",
        current_value=float(supporting.tc_slip),
        units="click",
        delta_per_unit_ms=round(-dt_ms, 1),
        confidence="low",
        mechanism=f"1 click TC slip -> slip tolerance -> ~{dt_ms:.0f}ms/click",
        justification=(
            f"TC slip {supporting.tc_slip}: sets how much wheelspin TC tolerates before cutting power. "
            f"Higher = more aggressive (allows more slip)."
        ),
        telemetry_evidence="matched to traction demand and driver smoothness",
        consequence_plus="+1 click: TC allows more slip -> potentially faster but less consistent",
        consequence_minus="-1 click: TC intervenes earlier -> more consistent but slower",
    )


def _tyre_pressure_front_sensitivity(
    supporting: "SupportingSolution",
    measured: "MeasuredState | None",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Front tyre cold pressure: grip window targeting."""
    avg_front = (supporting.tyre_cold_fl_kpa + supporting.tyre_cold_fr_kpa) / 2.0
    track_scale = (track.track_length_m or 6000.0) / 6020.0
    dt_ms = 20.0 * track_scale  # ~20ms per kPa off-target

    hot_evidence = ""
    if measured is not None:
        hot_p = getattr(measured, "front_pressure_mean_kpa", 0)
        if hot_p > 0:
            hot_evidence = f"measured hot pressure = {hot_p:.1f} kPa"

    return ParameterSensitivity(
        parameter="front_cold_pressure_kpa",
        current_value=avg_front,
        units="kPa",
        delta_per_unit_ms=round(-dt_ms, 1),
        confidence="medium",
        mechanism=f"1 kPa off-target -> front grip window -> ~{dt_ms:.0f}ms/kPa",
        justification=(
            f"Front cold {avg_front:.0f} kPa: {supporting.pressure_reasoning} "
            f"Targets optimal hot pressure window for maximum grip."
        ),
        telemetry_evidence=hot_evidence or "pressure reasoning from thermal model",
        consequence_plus="+1 kPa: higher hot pressure -> less contact patch, less grip",
        consequence_minus="-1 kPa: lower hot pressure -> more contact patch but risk of overheating",
    )


def _tyre_pressure_rear_sensitivity(
    supporting: "SupportingSolution",
    measured: "MeasuredState | None",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Rear tyre cold pressure: rear grip window targeting."""
    avg_rear = (supporting.tyre_cold_rl_kpa + supporting.tyre_cold_rr_kpa) / 2.0
    track_scale = (track.track_length_m or 6000.0) / 6020.0
    dt_ms = 18.0 * track_scale

    hot_evidence = ""
    if measured is not None:
        hot_p = getattr(measured, "rear_pressure_mean_kpa", 0)
        if hot_p > 0:
            hot_evidence = f"measured hot pressure = {hot_p:.1f} kPa"

    return ParameterSensitivity(
        parameter="rear_cold_pressure_kpa",
        current_value=avg_rear,
        units="kPa",
        delta_per_unit_ms=round(-dt_ms, 1),
        confidence="medium",
        mechanism=f"1 kPa off-target -> rear grip window -> ~{dt_ms:.0f}ms/kPa",
        justification=(
            f"Rear cold {avg_rear:.0f} kPa: targets optimal rear hot pressure for traction. "
            f"Rear pressures affect power-down grip and rear stability."
        ),
        telemetry_evidence=hot_evidence or "pressure reasoning from thermal model",
        consequence_plus="+1 kPa: less rear grip, more oversteer on power",
        consequence_minus="-1 kPa: more rear grip but risk of overheating",
    )


def _damper_sensitivity(
    param_name: str,
    click_value: int,
    corner_label: str,
    regime: str,
    direction: str,
    zeta: float,
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Generic damper click sensitivity."""
    track_scale = (track.track_length_m or 6000.0) / 6020.0
    dt_ms = DAMPER_MS_PER_CLICK * track_scale

    regime_desc = "body control (roll/pitch)" if regime == "LS" else "bump absorption (kerbs/surface)"
    dir_desc = "compression (bump hit)" if direction == "comp" else (
        "rebound (extension)" if direction == "rbd" else "high-speed transition slope"
    )

    return ParameterSensitivity(
        parameter=param_name,
        current_value=float(click_value),
        units="click",
        delta_per_unit_ms=round(dt_ms, 1),
        confidence="low",
        mechanism=f"1 click -> damping force change -> ~{dt_ms:.0f}ms/click ({regime} {direction})",
        justification=(
            f"{corner_label} {regime} {direction} = {click_value} clicks: "
            f"damping ratio ζ = {zeta:.2f}. "
            f"Controls {regime_desc} in {dir_desc} direction. "
            f"Derived from critical damping ratio target for {regime} regime."
        ),
        telemetry_evidence=f"ζ = {zeta:.2f} (target: {'0.55-0.70' if regime == 'LS' else '0.25-0.40'})",
        consequence_plus="+1 click: more damping force -> slower suspension response, more control",
        consequence_minus="-1 click: less damping force -> faster response, more compliance",
    )


def _master_cyl_sensitivity(
    supporting: "SupportingSolution",
    which: str,
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Master cylinder size: brake pedal feel and pressure distribution."""
    if which == "front":
        val = supporting.front_master_cyl_mm
        label = "front_master_cyl_mm"
    else:
        val = supporting.rear_master_cyl_mm
        label = "rear_master_cyl_mm"

    return ParameterSensitivity(
        parameter=label,
        current_value=val,
        units="mm",
        delta_per_unit_ms=round(-2.0, 1),
        confidence="low",
        mechanism="Master cyl size -> pedal pressure ratio -> brake feel",
        justification=(
            f"{which.title()} master cyl {val:.1f}mm: pass-through from IBT setup. "
            f"Affects brake pressure distribution and pedal travel. "
            f"Status: {supporting.master_cylinder_status}."
        ),
        telemetry_evidence=f"status = {supporting.master_cylinder_status}",
        consequence_plus="+0.5mm: less pressure per pedal travel (softer feel)",
        consequence_minus="-0.5mm: more pressure per pedal travel (firmer feel)",
    )


def _pad_compound_sensitivity(
    supporting: "SupportingSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Brake pad compound: friction characteristics."""
    # Map compound to a numeric value for display
    compound_map = {"low": 1, "medium": 2, "high": 3}
    val = compound_map.get(str(supporting.pad_compound).lower(), 2)

    return ParameterSensitivity(
        parameter="pad_compound",
        current_value=float(val),
        units="compound",
        delta_per_unit_ms=round(-5.0, 1),
        confidence="low",
        mechanism="Pad compound -> friction level -> braking performance",
        justification=(
            f"Pad compound '{supporting.pad_compound}': pass-through from IBT setup. "
            f"Higher friction compound = shorter braking zones but more heat/fade risk. "
            f"Status: {supporting.pad_compound_status}."
        ),
        telemetry_evidence=f"compound = {supporting.pad_compound}, status = {supporting.pad_compound_status}",
        consequence_plus="Higher friction: shorter braking but more heat",
        consequence_minus="Lower friction: longer braking but less heat/fade",
    )


def _brake_target_sensitivity(
    supporting: "SupportingSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Brake bias target (in-car adjustment)."""
    return ParameterSensitivity(
        parameter="brake_bias_target",
        current_value=supporting.brake_bias_target,
        units="clicks",
        delta_per_unit_ms=round(-3.0, 1),
        confidence="low",
        mechanism="Brake target -> in-car bias adjustment -> fine-tuning",
        justification=(
            f"Brake target {supporting.brake_bias_target:+.1f}: in-car bias fine-tune. "
            f"Status: {supporting.brake_bias_target_status}. "
            f"Used for live adjustment during a stint as conditions change."
        ),
        telemetry_evidence=f"status = {supporting.brake_bias_target_status}",
        consequence_plus="+1: shifts bias forward slightly during stint",
        consequence_minus="-1: shifts bias rearward slightly during stint",
    )


def _brake_migration_sensitivity(
    supporting: "SupportingSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Brake bias migration (pressure-dependent shift)."""
    return ParameterSensitivity(
        parameter="brake_bias_migration",
        current_value=supporting.brake_bias_migration,
        units="clicks",
        delta_per_unit_ms=round(-2.0, 1),
        confidence="low",
        mechanism="Migration -> bias shift under heavy braking -> entry stability",
        justification=(
            f"Brake migration {supporting.brake_bias_migration:+.1f}: how much bias shifts "
            f"as brake pressure increases. Affects trail-brake behavior. "
            f"Status: {supporting.brake_bias_migration_status}."
        ),
        telemetry_evidence=f"status = {supporting.brake_bias_migration_status}",
        consequence_plus="+1: more forward shift under heavy braking -> more stable",
        consequence_minus="-1: less shift -> more consistent bias but less entry protection",
    )


def _torsion_turns_sensitivity(
    step3: "CornerSpringSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Torsion bar turns (Ferrari-specific or derived): spring preload adjustment."""
    turns = getattr(step3, "torsion_bar_turns", 0.0)
    return ParameterSensitivity(
        parameter="torsion_bar_turns",
        current_value=turns,
        units="turns",
        delta_per_unit_ms=round(-3.0, 1),
        confidence="low",
        mechanism="Turns -> torsion bar preload -> ride height fine-tune",
        justification=(
            f"Torsion turns {turns:.3f}: DEPENDENT variable derived from "
            f"front heave rate and perch offset. Fine-tunes front corner spring preload."
        ),
        telemetry_evidence=f"computed from heave + perch geometry",
        consequence_plus="+0.01 turns: slightly more front preload",
        consequence_minus="-0.01 turns: slightly less front preload",
    )


# ── Main entry point ─────────────────────────────────────────────────────────

def compute_laptime_sensitivity(
    track: "TrackProfile",
    step1: "RakeSolution",
    step2: "HeaveSolution",
    step3: "CornerSpringSolution",
    step4: "ARBSolution",
    step5: "WheelGeometrySolution",
    brake_bias_pct: float = 56.0,
    step6: "DamperSolution | None" = None,
    supporting: "SupportingSolution | None" = None,
    measured: "MeasuredState | None" = None,
    wing: float = 17.0,
) -> LaptimeSensitivityReport:
    """Compute lap time sensitivity for ALL setup parameters.

    Every parameter in the output setup gets a sensitivity entry with
    justification, telemetry evidence, and consequence analysis.

    Parameters are ranked by absolute sensitivity (most impactful first).

    Args:
        track: Track profile
        step1: Rake solution (ride heights)
        step2: Heave solution (spring rates)
        step3: Corner spring solution (torsion bar OD)
        step4: ARB solution (rear ARB blade, LLTD)
        step5: Wheel geometry solution (camber, toe)
        brake_bias_pct: Current brake bias setting
        step6: Damper solution (all damper clicks)
        supporting: Supporting solution (brakes, diff, TC, tyres)
        measured: Measured telemetry state (for evidence)
        wing: Wing angle setting

    Returns:
        LaptimeSensitivityReport sorted by |delta_per_unit_ms|
    """
    # W6.1 (F-LT-1, F-LT-2): GT3 cars (suspension_arch=GT3_COIL_4WHEEL) skip
    # Step 2; HeaveSolution.null() carries `present=False` and zero numeric
    # fields. The four heave / third / perch sensitivity functions below now
    # return None when `step2.present is False` so the caller can filter the
    # phantom rows out of the table. A GT3-shaped corner-spring sensitivity
    # is a Phase 3 follow-on — TODO(W6.x).
    _step2_present = getattr(step2, "present", True)

    sensitivities: list["ParameterSensitivity | None"] = [
        # ── Ride heights (highest sensitivity) ──
        _rear_rh_sensitivity(step1, track, measured),
        _front_rh_sensitivity(step1, track, measured),
        # ── Wing ──
        _wing_angle_sensitivity(wing, track),
        # ── Springs (heave/third only when Step 2 was solved) ──
        _front_heave_sensitivity(step2, track, measured) if _step2_present else None,
        _rear_third_sensitivity(step2, track, measured) if _step2_present else None,
        # Front corner spring: torsion bar OR roll spring depending on car architecture
        *(
            [_front_roll_spring_sensitivity(step3, step4, track)]
            if (step3.front_torsion_od_mm == 0.0 and step3.front_roll_spring_nmm > 0)
            else [_torsion_bar_sensitivity(step3, step4, track),
                  _torsion_turns_sensitivity(step3, track)]
        ),
        _rear_spring_sensitivity(step3, track),
        # ── Perch offsets (dependent variables; heave-only on GTP) ──
        _heave_perch_sensitivity(step2, track) if _step2_present else None,
        _rear_third_perch_sensitivity(step2, track) if _step2_present else None,
        _rear_spring_perch_sensitivity(step3, track),
        # ── Pushrods ──
        _front_pushrod_sensitivity(step1, track),
        _rear_pushrod_sensitivity(step1, track),
        # ── ARBs ──
        _rear_arb_sensitivity(step4, track, measured),
        _front_arb_blade_sensitivity(step4, track),
        _front_arb_size_sensitivity(step4, track),
        _rear_arb_size_sensitivity(step4, track),
        # ── Geometry ──
        _front_camber_sensitivity(step5, track, measured),
        _rear_camber_sensitivity(step5, track, measured),
        _front_toe_sensitivity(step5, track),
        _rear_toe_sensitivity(step5, track),
        # ── Brakes ──
        _brake_bias_sensitivity(brake_bias_pct, track),
    ]
    # Drop the None placeholders (heave entries skipped on GT3 / null Step 2).
    sensitivities = [s for s in sensitivities if s is not None]

    # ── Supporting parameters (if available) ──
    if supporting is not None:
        sensitivities.extend([
            _diff_preload_sensitivity(supporting, track, measured),
            _diff_coast_ramp_sensitivity(supporting, track),
            _diff_drive_ramp_sensitivity(supporting, track),
            _diff_clutch_plates_sensitivity(supporting, track),
            _tc_gain_sensitivity(supporting, track),
            _tc_slip_sensitivity(supporting, track),
            _tyre_pressure_front_sensitivity(supporting, measured, track),
            _tyre_pressure_rear_sensitivity(supporting, measured, track),
            _master_cyl_sensitivity(supporting, "front", track),
            _master_cyl_sensitivity(supporting, "rear", track),
            _pad_compound_sensitivity(supporting, track),
            _brake_target_sensitivity(supporting, track),
            _brake_migration_sensitivity(supporting, track),
        ])

    # ── Dampers (if available) ──
    if step6 is not None:
        for corner_label, corner in [("Front", step6.lf), ("Rear", step6.lr)]:
            prefix = "front" if corner_label == "Front" else "rear"
            # Get damping ratios from step6 if available
            zeta_ls = getattr(corner, "zeta_ls", 0.55)
            zeta_hs = getattr(corner, "zeta_hs", 0.35)

            sensitivities.extend([
                _damper_sensitivity(
                    f"{prefix}_ls_comp", corner.ls_comp, corner_label, "LS", "comp",
                    zeta_ls, track,
                ),
                _damper_sensitivity(
                    f"{prefix}_ls_rbd", corner.ls_rbd, corner_label, "LS", "rbd",
                    zeta_ls, track,
                ),
                _damper_sensitivity(
                    f"{prefix}_hs_comp", corner.hs_comp, corner_label, "HS", "comp",
                    zeta_hs, track,
                ),
                _damper_sensitivity(
                    f"{prefix}_hs_rbd", corner.hs_rbd, corner_label, "HS", "rbd",
                    zeta_hs, track,
                ),
                _damper_sensitivity(
                    f"{prefix}_hs_slope", corner.hs_slope, corner_label, "HS", "slope",
                    zeta_hs, track,
                ),
            ])

    # Sort by absolute impact
    sensitivities.sort(key=lambda s: abs(s.delta_per_unit_ms), reverse=True)

    return LaptimeSensitivityReport(sensitivities=sensitivities)
