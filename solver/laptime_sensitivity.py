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
    from solver.heave_solver import HeaveSolution
    from solver.rake_solver import RakeSolution
    from solver.supporting_solver import SupportingSolution
    from solver.wheel_geometry_solver import WheelGeometrySolution
    from solver.corner_spring_solver import CornerSpringSolution
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

    def summary(self, width: int = 63) -> str:
        lines = [
            "=" * width,
            "  LAP TIME SENSITIVITY ANALYSIS",
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
            f"  {'Parameter':<24s} {'Value':>7s} {'±ms/unit':>9s} "
            f"{'Conf':>5s}"
        )
        lines.append(header)
        lines.append("  " + "-" * (width - 4))

        for s in ranked:
            sign = "+" if s.delta_per_unit_ms > 0 else ""
            lines.append(
                f"  {s.parameter:<24s} {s.current_value:>7.2f} "
                f"{sign}{s.delta_per_unit_ms:>8.1f} "
                f"{s.confidence:>5s}"
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
    )


def _front_rh_sensitivity(
    step1: "RakeSolution",
    track: "TrackProfile",
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
    )


def _rear_arb_sensitivity(
    step4: "ARBSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """RARB blade (fine-tuning): 1 blade → ~3% LLTD → ~0.9° understeer → ~180ms at Sebring."""
    lltd_shift = RARB_LLTD_PER_BLADE  # fraction
    dt_ms = _lltd_to_laptime_delta_ms(lltd_shift, track)

    return ParameterSensitivity(
        parameter="rear_arb_blade",
        current_value=float(step4.rear_arb_blade_start),
        units="blade",
        delta_per_unit_ms=round(-dt_ms, 1),  # +1 blade stiffens → more understeer → negative
        confidence="high",
        mechanism=(
            f"1 RARB blade -> {lltd_shift*100:.0f}% LLTD -> "
            f"{lltd_shift*100*LLTD_US_COEFF:.1f}deg US -> "
            f"{abs(dt_ms):.0f}ms/lap"
        ),
    )


def _front_heave_sensitivity(
    step2: "HeaveSolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Front heave (weight 0.75): 10 N/mm → bottoming margin → DF stability."""
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
        delta_per_unit_ms=round(-dt_ms, 1),  # deviation from optimal hurts
        confidence="medium",
        mechanism=(
            f"1% bias deviation -> braking efficiency, entry balance "
            f"-> ~{dt_ms:.0f}ms/lap (symmetric around optimal)"
        ),
    )


def _rear_camber_sensitivity(
    step5: "WheelGeometrySolution",
    track: "TrackProfile",
) -> ParameterSensitivity:
    """Rear camber: 0.1° → contact patch → lateral grip → ~30ms/lap."""
    track_scale = (track.track_length_m or 6000.0) / 6020.0
    dt_ms_per_deg = 5.0 * track_scale  # ms per 0.1deg (research: 10-25ms per 0.3° (positive = more camber is faster)

    return ParameterSensitivity(
        parameter="rear_camber_deg",
        current_value=step5.rear_camber_deg,
        units="deg",
        delta_per_unit_ms=round(dt_ms_per_deg, 1),  # more negative = faster (per 0.1°)
        confidence="low",
        mechanism=(
            f"0.1 deg camber -> contact patch -> lateral grip "
            f"-> ~{dt_ms_per_deg:.0f}ms/lap (per 0.1 deg)"
        ),
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
) -> LaptimeSensitivityReport:
    """Compute lap time sensitivity for all key parameters.

    Parameters are ranked by absolute sensitivity (most impactful first).

    Args:
        track: Track profile
        step1: Rake solution (ride heights)
        step2: Heave solution (spring rates)
        step3: Corner spring solution (torsion bar OD)
        step4: ARB solution (rear ARB blade, LLTD)
        step5: Wheel geometry solution (camber)
        brake_bias_pct: Current brake bias setting

    Returns:
        LaptimeSensitivityReport sorted by |delta_per_unit_ms|
    """
    sensitivities = [
        _rear_rh_sensitivity(step1, track),
        _front_rh_sensitivity(step1, track),
        _rear_arb_sensitivity(step4, track),
        _front_heave_sensitivity(step2, track),
        _torsion_bar_sensitivity(step3, step4, track),
        _brake_bias_sensitivity(brake_bias_pct, track),
        _rear_camber_sensitivity(step5, track),
    ]

    # Sort by absolute impact
    sensitivities.sort(key=lambda s: abs(s.delta_per_unit_ms), reverse=True)

    return LaptimeSensitivityReport(sensitivities=sensitivities)
