"""Sector-level setup compromise analysis.

Divides the lap into three speed sectors:
- Slow sector:   corners < 120 kph — mechanical grip dominant (ARB, diff)
- Medium sector: corners 120-180 kph — springs and ARBs both matter
- Fast sector:   corners > 180 kph — aero dominant (heave springs, ride height)

For each key setup parameter, identifies the optimal value per sector and
computes the conflict (and cost) of the lap-best compromise.

This helps answer: "Am I losing more time in slow or fast corners from
this RARB setting?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from solver.arb_solver import ARBSolution
    from solver.heave_solver import HeaveSolution
    from solver.rake_solver import RakeSolution
    from track_model.profile import TrackProfile


# ── LLTD effect per RARB blade change ────────────────────────────────────────
# From ARB solver: each RARB blade ≈ -3% LLTD (softening rear ARB shifts LLTD rearward)
RARB_LLTD_PER_BLADE = -0.030  # fraction (e.g., -0.030 = -3%)

# Lap time sensitivity: 1% LLTD shift ≈ 0.3° understeer ≈ 0.2s/lap at typical circuit
# So per RARB blade: ~0.9° understeer → ~0.18s (180ms)
RARB_LAPTIME_MS_PER_BLADE = 180.0  # ms per RARB blade deviation from optimal

# Ride height → DF change (approximate from aero map sensitivity)
RH_DF_SENSITIVITY_N_PER_MM = 0.5   # N DF change per mm rear RH change
FRONT_RH_DF_SENSITIVITY_N_PER_MM = 1.2  # front has larger effect on total DF

# Brake bias laptime sensitivity
BRAKE_BIAS_MS_PER_PCT = 50.0    # ms per 1% brake bias deviation from optimal

# Camber sensitivity
CAMBER_MS_PER_DEG = 30.0        # ms per 0.1° camber deviation


@dataclass
class SectorEffect:
    """Effect of a parameter change on one speed sector."""
    parameter: str
    delta: float                # the change (e.g., +1 blade)
    sector: str                 # "slow" | "medium" | "fast"
    delta_grip_pct: float       # estimated grip change in this sector
    delta_laptime_ms: float     # estimated lap time change (ms) in this sector
    direction: str              # "beneficial" | "detrimental" | "neutral"
    reasoning: str


@dataclass
class ParameterConflict:
    """Conflict between optimal values across speed sectors."""
    parameter: str
    slow_optimal: str           # e.g. "RARB blade 1-2"
    fast_optimal: str           # e.g. "RARB blade 4-5"
    compromise: str             # e.g. "RARB blade 3"
    time_cost_ms: float         # ms cost of compromise vs sector-perfect values
    note: str


@dataclass
class SectorCompromiseResult:
    """Complete sector-level setup compromise analysis."""
    slow_sector_time_pct: float     # % of lap time in slow corners
    medium_sector_time_pct: float
    fast_sector_time_pct: float

    parameter_conflicts: list[ParameterConflict] = field(default_factory=list)
    compromise_recommendations: list[str] = field(default_factory=list)

    def summary(self, width: int = 63) -> str:
        lines = [
            "=" * width,
            "  SECTOR-LEVEL SETUP COMPROMISE",
            "=" * width,
            "",
            "  SPEED SECTOR DISTRIBUTION",
            f"  {'Slow (<120 kph)':<28s}: {self.slow_sector_time_pct:.0f}% of lap time",
            f"  {'Medium (120-180 kph)':<28s}: {self.medium_sector_time_pct:.0f}%",
            f"  {'Fast (>180 kph)':<28s}: {self.fast_sector_time_pct:.0f}%",
            "",
        ]

        if self.parameter_conflicts:
            lines.append("  PARAMETER CONFLICTS")
            lines.append("  " + "-" * (width - 4))
            header = f"  {'Parameter':<22s} {'Slow opt':>9s} {'Fast opt':>9s} {'Compromise':>11s} {'Cost ms':>7s}"
            lines.append(header)
            lines.append("  " + "-" * (width - 4))
            for conflict in self.parameter_conflicts:
                lines.append(
                    f"  {conflict.parameter:<22s} {conflict.slow_optimal:>9s} "
                    f"{conflict.fast_optimal:>9s} {conflict.compromise:>11s} "
                    f"{conflict.time_cost_ms:>7.0f}"
                )
                if conflict.note:
                    lines.append(f"    {conflict.note[:width - 6]}")

        if self.compromise_recommendations:
            lines.append("")
            lines.append("  RECOMMENDATIONS")
            lines.append("  " + "-" * (width - 4))
            for rec in self.compromise_recommendations:
                lines.append(f"  -> {rec[:width - 6]}")

        lines.append("")
        lines.append("=" * width)
        return "\n".join(lines)


def _parse_speed_bands(track: "TrackProfile") -> tuple[float, float, float]:
    """Extract slow/medium/fast sector percentages from track speed bands.

    Returns:
        (slow_pct, medium_pct, fast_pct) — percentages summing to 100%
    """
    speed_bands = track.speed_bands_kph
    if not speed_bands:
        # Fallback from track corners
        if track.corners:
            apex_speeds = [c.speed_kph for c in track.corners]
            n = len(apex_speeds)
            n_slow = sum(1 for s in apex_speeds if s < 120)
            n_fast = sum(1 for s in apex_speeds if s >= 180)
            n_med = n - n_slow - n_fast
            slow_pct = n_slow / max(n, 1) * 100
            fast_pct = n_fast / max(n, 1) * 100
            med_pct = n_med / max(n, 1) * 100
            return slow_pct, med_pct, fast_pct
        # Default for a balanced circuit (Sebring-like)
        return 30.0, 40.0, 30.0

    slow_pct = 0.0
    fast_pct = 0.0
    total_pct = 0.0

    for band, pct in speed_bands.items():
        try:
            parts = band.split("-")
            lower = int(parts[0])
        except (ValueError, IndexError):
            # Handle "250+" style bands
            try:
                lower = int(band.rstrip("+"))
            except ValueError:
                continue

        total_pct += pct
        if lower < 120:
            slow_pct += pct
        elif lower >= 180:
            fast_pct += pct

    if total_pct <= 0:
        return 30.0, 40.0, 30.0

    # Normalize to 100%
    scale = 100.0 / total_pct
    slow_pct *= scale
    fast_pct *= scale
    med_pct = 100.0 - slow_pct - fast_pct

    return round(slow_pct, 1), round(max(med_pct, 0.0), 1), round(fast_pct, 1)


def _compute_rarb_conflict(
    step4: "ARBSolution",
    slow_pct: float,
    fast_pct: float,
) -> ParameterConflict:
    """RARB conflict: slow corners want blade 1-2, fast want 4-5."""
    current_blade = step4.rear_arb_blade_start
    slow_opt_blade = max(1, current_blade - 1)
    fast_opt_blade = min(5, current_blade + 1)

    # Compromise blade: weighted average by sector time
    slow_fraction = slow_pct / 100.0
    fast_fraction = fast_pct / 100.0
    weighted = slow_opt_blade * slow_fraction + fast_opt_blade * fast_fraction
    compromise_blade = max(1, min(5, round(weighted)))

    # Cost: deviation from optimal for each sector
    slow_cost = abs(compromise_blade - slow_opt_blade) * RARB_LAPTIME_MS_PER_BLADE * slow_fraction
    fast_cost = abs(compromise_blade - fast_opt_blade) * RARB_LAPTIME_MS_PER_BLADE * fast_fraction
    total_cost = slow_cost + fast_cost

    return ParameterConflict(
        parameter="RARB blade",
        slow_optimal=f"blade {slow_opt_blade}-{slow_opt_blade + 1}",
        fast_optimal=f"blade {fast_opt_blade}",
        compromise=f"blade {compromise_blade}",
        time_cost_ms=round(total_cost, 0),
        note=(
            f"Slow corners need softer RARB (rotation, mechanical grip). "
            f"Fast corners need stiffer RARB (stability, aero platform)."
        ),
    )


def _compute_front_heave_conflict(
    step2: "HeaveSolution",
    slow_pct: float,
    fast_pct: float,
) -> ParameterConflict | None:
    """Front heave conflict: fast corners prefer softer, kerb sections prefer stiffer."""
    heave = step2.front_heave_nmm
    # Mild conflict: stiff heave helps control platform in fast corners
    # but softer helps kerb absorption in slow sections
    # Only report if meaningful gap
    if abs(fast_pct - slow_pct) < 15:
        return None

    fast_opt = max(heave - 10, 20)
    slow_opt = min(heave + 10, 200)
    compromise = heave  # usually already the compromise

    cost = abs(fast_pct - slow_pct) * 0.5  # rough estimate
    return ParameterConflict(
        parameter="Front heave",
        slow_optimal=f"{slow_opt:.0f} N/mm",
        fast_optimal=f"{fast_opt:.0f} N/mm",
        compromise=f"{compromise:.0f} N/mm",
        time_cost_ms=round(cost, 0),
        note="Mild conflict: stiffer heave = better platform; softer = kerb absorption.",
    )


def _compute_brake_bias_conflict(
    slow_pct: float,
    fast_pct: float,
    base_bias_pct: float = 46.0,  # calibrated default; always pass from compute_brake_bias()
) -> ParameterConflict | None:
    """Brake bias conflict: slow corners slightly rearward, fast corners forward."""
    if abs(fast_pct - slow_pct) < 20:
        return None

    slow_opt = base_bias_pct - 0.5
    fast_opt = base_bias_pct + 0.5
    cost = abs(fast_pct - slow_pct) * 0.15

    return ParameterConflict(
        parameter="Brake bias",
        slow_optimal=f"{slow_opt:.1f}%",
        fast_optimal=f"{fast_opt:.1f}%",
        compromise=f"{base_bias_pct:.1f}%",
        time_cost_ms=round(cost, 0),
        note="Mild: rear bias helps slow rotation, forward bias helps stability at speed.",
    )


def _compute_camber_conflict(
    slow_pct: float,
    fast_pct: float,
    base_camber_deg: float = -2.9,
) -> ParameterConflict | None:
    """Front camber conflict: slow wants max mechanical grip, fast wants less drag."""
    if abs(fast_pct - slow_pct) < 25:
        return None

    slow_opt = base_camber_deg - 0.2
    fast_opt = base_camber_deg + 0.2
    cost = abs(fast_pct - slow_pct) * 0.1

    return ParameterConflict(
        parameter="Front camber",
        slow_optimal=f"{slow_opt:+.1f} deg",
        fast_optimal=f"{fast_opt:+.1f} deg",
        compromise=f"{base_camber_deg:+.1f} deg",
        time_cost_ms=round(cost, 0),
        note="Mild: more camber = max cornering; less camber = less drag on straights.",
    )


class SectorCompromise:
    """Compute sector-level setup compromise from track profile and solver outputs."""

    def __init__(self, track: "TrackProfile") -> None:
        self.track = track

    def analyze(
        self,
        step1: "RakeSolution | None" = None,
        step2: "HeaveSolution | None" = None,
        step4: "ARBSolution | None" = None,
        base_bias_pct: float = 46.0,  # pass from compute_brake_bias() for accuracy
        base_camber_deg: float = -2.9,
    ) -> SectorCompromiseResult:
        """Run sector compromise analysis.

        Args:
            step1: Rake solution (for ride heights)
            step2: Heave solution (for spring rates)
            step4: ARB solution (for RARB blade and LLTD)
            base_bias_pct: Baseline brake bias
            base_camber_deg: Baseline front camber

        Returns:
            SectorCompromiseResult with conflicts and compromise recommendations
        """
        slow_pct, med_pct, fast_pct = _parse_speed_bands(self.track)

        conflicts: list[ParameterConflict] = []

        # RARB conflict (always meaningful)
        if step4 is not None:
            rarb_conflict = _compute_rarb_conflict(step4, slow_pct, fast_pct)
            conflicts.append(rarb_conflict)

        # Front heave conflict (mild, conditional)
        if step2 is not None:
            heave_conflict = _compute_front_heave_conflict(step2, slow_pct, fast_pct)
            if heave_conflict is not None:
                conflicts.append(heave_conflict)

        # Brake bias conflict (mild)
        bias_conflict = _compute_brake_bias_conflict(slow_pct, fast_pct, base_bias_pct)
        if bias_conflict is not None:
            conflicts.append(bias_conflict)

        # Camber conflict (mild)
        camber_conflict = _compute_camber_conflict(slow_pct, fast_pct, base_camber_deg)
        if camber_conflict is not None:
            conflicts.append(camber_conflict)

        # Generate recommendations
        recommendations = self._generate_recommendations(
            conflicts, slow_pct, fast_pct, step4
        )

        return SectorCompromiseResult(
            slow_sector_time_pct=slow_pct,
            medium_sector_time_pct=med_pct,
            fast_sector_time_pct=fast_pct,
            parameter_conflicts=conflicts,
            compromise_recommendations=recommendations,
        )

    def _generate_recommendations(
        self,
        conflicts: list[ParameterConflict],
        slow_pct: float,
        fast_pct: float,
        step4: "ARBSolution | None",
    ) -> list[str]:
        """Generate actionable recommendations from the conflict analysis."""
        recs: list[str] = []

        dominant = "slow" if slow_pct > fast_pct * 1.3 else (
            "fast" if fast_pct > slow_pct * 1.3 else "balanced"
        )

        if dominant == "slow":
            recs.append(
                f"Slow-sector circuit ({slow_pct:.0f}% slow corners): "
                f"prioritize mechanical grip. Soft RARB, more diff rotation."
            )
            if step4 is not None:
                soft_blade = max(1, step4.rear_arb_blade_start - 1)
                recs.append(
                    f"Consider RARB blade {soft_blade} as baseline "
                    f"(softer than nominal for slow-corner priority)."
                )
        elif dominant == "fast":
            recs.append(
                f"Fast-sector circuit ({fast_pct:.0f}% fast corners): "
                f"prioritize aero platform. Stiffer RARB, more ride height control."
            )
            if step4 is not None:
                stiff_blade = min(5, step4.rear_arb_blade_start + 1)
                recs.append(
                    f"Consider RARB blade {stiff_blade} as baseline "
                    f"(stiffer than nominal for aero-platform priority)."
                )
        else:
            recs.append(
                f"Balanced circuit ({slow_pct:.0f}% slow / {fast_pct:.0f}% fast): "
                f"nominal solver values are the best compromise."
            )

        # RARB live adjustment strategy
        total_cost = sum(c.time_cost_ms for c in conflicts)
        if total_cost > 200:
            recs.append(
                f"Total compromise cost ~{total_cost:.0f} ms/lap. "
                f"Use RARB live adjustment aggressively: blade 1-2 in slow corners, "
                f"blade 4-5 in fast corners."
            )
        elif total_cost > 50:
            recs.append(
                f"Moderate compromise cost ~{total_cost:.0f} ms/lap. "
                f"RARB live adjustment recommended."
            )

        return recs
