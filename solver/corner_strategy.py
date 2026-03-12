"""Corner-specific reasoning — per-corner live parameter strategies.

GTP cars have live-adjustable parameters (RARB blade, brake bias, TC, diff)
that should vary corner-to-corner based on speed, aero load, and handling
demands. This module:

1. Groups corners into strategy clusters (low/mid/high speed)
2. Computes per-cluster live parameter recommendations
3. Identifies which corner is the binding constraint for each static parameter
4. Generates a corner map table for the engineering report

Depends on: analyzer/segment.py for CornerAnalysis data
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analyzer.segment import CornerAnalysis
    from car_model.cars import CarModel


@dataclass
class CornerParameterMap:
    """Recommended live parameter settings for a single corner."""
    corner_id: int
    corner_name: str            # e.g. "T1", "T7"
    speed_class: str            # "low" | "mid" | "high"
    apex_speed_kph: float
    peak_lat_g: float
    direction: str

    # Recommended live settings
    rarb_blade: int = 3         # 1-5
    brake_bias_pct: float = 46.0
    tc_gain: int = 4
    tc_slip: int = 3
    diff_preload_offset_nm: float = 0.0  # delta from baseline

    # Why this corner matters
    notes: str = ""
    binding_for: str = ""       # which static parameter this corner constrains


@dataclass
class BindingCorner:
    """Identifies which corner constrains a specific static parameter."""
    parameter: str
    corner_id: int
    corner_name: str
    constraint_value: float
    constraint_limit: float
    margin_pct: float
    explanation: str


@dataclass
class CornerStrategy:
    """Complete corner-by-corner strategy for live adjustments."""
    corner_maps: list[CornerParameterMap] = field(default_factory=list)
    binding_corners: list[BindingCorner] = field(default_factory=list)
    cluster_summary: dict[str, dict] = field(default_factory=dict)

    def summary(self, width: int = 63) -> str:
        """ASCII corner map table for engineering report."""
        lines = [
            "=" * width,
            "  CORNER-SPECIFIC STRATEGY",
            "=" * width,
        ]

        # Cluster summary
        if self.cluster_summary:
            lines.append("")
            lines.append("  SPEED CLUSTERS")
            for cluster, info in self.cluster_summary.items():
                n = info.get("count", 0)
                speed = info.get("avg_speed", 0)
                lines.append(
                    f"    {cluster.upper():>5s}: {n} corners, "
                    f"avg {speed:.0f} kph"
                )

        # Corner map table
        lines.append("")
        lines.append("  CORNER MAP")
        lines.append("  " + "-" * (width - 4))
        lines.append(
            f"  {'Corner':<8s} {'Speed':>5s} {'Class':>5s} "
            f"{'RARB':>4s} {'Bias':>5s} {'TC':>4s} {'Notes'}"
        )
        lines.append("  " + "-" * (width - 4))

        for cm in self.corner_maps:
            tc_str = f"{cm.tc_gain}/{cm.tc_slip}"
            notes = cm.notes[:20] if cm.notes else ""
            if cm.binding_for:
                notes = f"[BIND:{cm.binding_for}]"
            lines.append(
                f"  {cm.corner_name:<8s} {cm.apex_speed_kph:>5.0f} "
                f"{cm.speed_class:>5s} {cm.rarb_blade:>4d} "
                f"{cm.brake_bias_pct:>5.1f} {tc_str:>4s} {notes}"
            )

        # Binding corners
        if self.binding_corners:
            lines.append("")
            lines.append("  BINDING CORNERS (constrain static parameters)")
            lines.append("  " + "-" * (width - 4))
            for bc in self.binding_corners:
                lines.append(
                    f"    {bc.corner_name}: constrains {bc.parameter} "
                    f"({bc.margin_pct:+.1f}% margin)"
                )
                lines.append(f"      {bc.explanation}")

        lines.append("=" * width)
        return "\n".join(lines)


# ── Corner classification ─────────────────────────────────────────────

def _rarb_blade_for_speed(
    speed_kph: float,
    peak_lat_g: float,
    has_heavy_braking: bool = False,
) -> int:
    """Compute RARB blade recommendation for a corner.

    Low speed (< 120 kph): blade 1-2 (soft, mechanical grip dominant)
    Mid speed (120-180 kph): blade 2-3 (transition)
    High speed (> 180 kph): blade 4-5 (stiff, aero platform dominant)

    Heavy braking entry: +1 blade (stiffer for platform control)
    """
    if speed_kph < 100:
        blade = 1
    elif speed_kph < 120:
        blade = 2
    elif speed_kph < 150:
        blade = 3
    elif speed_kph < 180:
        blade = 4
    else:
        blade = 5

    if has_heavy_braking and blade < 5:
        blade += 1

    return max(1, min(5, blade))


def _brake_bias_for_corner(
    entry_speed_kph: float,
    base_bias_pct: float = 46.0,
) -> float:
    """Compute brake bias offset for a braking zone.

    High-speed braking (entry > 250 kph): +1% (more front authority with aero)
    Low-speed braking (entry < 150 kph): baseline
    """
    if entry_speed_kph > 250:
        return base_bias_pct + 1.0
    elif entry_speed_kph > 200:
        return base_bias_pct + 0.5
    return base_bias_pct


def _tc_for_corner(
    speed_class: str,
    peak_lat_g: float,
    base_gain: int = 4,
    base_slip: int = 3,
) -> tuple[int, int]:
    """Compute TC settings for a corner.

    Tight hairpins: TC gain +1 (more help at low speed)
    Fast sweepers: baseline
    """
    gain = base_gain
    slip = base_slip

    if speed_class == "low" and peak_lat_g > 1.5:
        gain = min(gain + 1, 10)  # more TC help for slow tight corners
    elif speed_class == "high":
        slip = max(slip - 1, 1)  # tighter slip control at speed

    return gain, slip


def _diff_preload_offset(
    speed_class: str,
    trail_brake_pct: float,
) -> float:
    """Compute diff preload offset for a corner.

    Traction zones (low speed, long exit): +5 Nm
    Trail-braking corners: -5 Nm (more rotation)
    """
    if speed_class == "low" and trail_brake_pct < 0.2:
        return 5.0  # traction priority
    elif trail_brake_pct > 0.4:
        return -5.0  # rotation priority
    return 0.0


# ── Binding corner identification ─────────────────────────────────────

def _find_binding_corners(
    corners: list[CornerAnalysis],
    front_excursion_limit_mm: float = 15.0,
    rear_excursion_limit_mm: float = 30.0,
) -> list[BindingCorner]:
    """Find which corners are the binding constraint for static parameters."""
    bindings = []

    # Find corner with worst front shock velocity (binds heave spring)
    if corners:
        worst_front = max(corners, key=lambda c: c.front_shock_vel_p99_mps)
        if worst_front.front_shock_vel_p99_mps > 0:
            margin = (
                (front_excursion_limit_mm - worst_front.front_shock_vel_p99_mps * 50)
                / front_excursion_limit_mm * 100
            )
            bindings.append(BindingCorner(
                parameter="front_heave_nmm",
                corner_id=worst_front.corner_id,
                corner_name=f"T{worst_front.corner_id}",
                constraint_value=worst_front.front_shock_vel_p99_mps,
                constraint_limit=front_excursion_limit_mm,
                margin_pct=margin,
                explanation=(
                    f"Worst front shock vel p99 = "
                    f"{worst_front.front_shock_vel_p99_mps*1000:.0f} mm/s "
                    f"at {worst_front.apex_speed_kph:.0f} kph. "
                    f"If driver smooths this corner, entire car can run softer."
                ),
            ))

        # Find corner with lowest front RH (binds ride height / vortex burst)
        worst_rh = min(corners, key=lambda c: c.front_rh_min_mm if c.front_rh_min_mm > 0 else 999)
        if worst_rh.front_rh_min_mm > 0:
            margin = worst_rh.front_rh_min_mm / 2.0 * 100  # 2mm threshold
            bindings.append(BindingCorner(
                parameter="front_ride_height",
                corner_id=worst_rh.corner_id,
                corner_name=f"T{worst_rh.corner_id}",
                constraint_value=worst_rh.front_rh_min_mm,
                constraint_limit=2.0,
                margin_pct=margin,
                explanation=(
                    f"Lowest front RH = {worst_rh.front_rh_min_mm:.1f}mm "
                    f"at {worst_rh.apex_speed_kph:.0f} kph. "
                    f"Closest to vortex burst threshold."
                ),
            ))

        # Find corner with worst understeer (binds LLTD / ARB)
        worst_us = max(corners, key=lambda c: c.understeer_mean_deg)
        if worst_us.understeer_mean_deg > 1.5:
            bindings.append(BindingCorner(
                parameter="rear_arb_blade",
                corner_id=worst_us.corner_id,
                corner_name=f"T{worst_us.corner_id}",
                constraint_value=worst_us.understeer_mean_deg,
                constraint_limit=2.5,
                margin_pct=(2.5 - worst_us.understeer_mean_deg) / 2.5 * 100,
                explanation=(
                    f"Worst understeer = {worst_us.understeer_mean_deg:+.1f}° "
                    f"({worst_us.speed_class} speed, "
                    f"{worst_us.apex_speed_kph:.0f} kph). "
                    f"This corner limits how stiff RARB can be."
                ),
            ))

    return bindings


# ── Main strategy builder ─────────────────────────────────────────────

def build_corner_strategy(
    corners: list[CornerAnalysis],
    base_brake_bias_pct: float = 46.0,
    base_tc_gain: int = 4,
    base_tc_slip: int = 3,
    front_excursion_limit_mm: float = 15.0,
) -> CornerStrategy:
    """Build per-corner live parameter strategy.

    Args:
        corners: Corner analysis data from segment.py
        base_brake_bias_pct: Baseline brake bias
        base_tc_gain: Baseline TC gain setting
        base_tc_slip: Baseline TC slip setting
        front_excursion_limit_mm: Front dynamic RH limit for binding analysis

    Returns:
        CornerStrategy with per-corner maps and binding corners
    """
    corner_maps: list[CornerParameterMap] = []
    cluster_counts: dict[str, list[float]] = {"low": [], "mid": [], "high": []}

    for corner in corners:
        has_heavy_braking = corner.entry_speed_kph - corner.apex_speed_kph > 80

        rarb = _rarb_blade_for_speed(
            corner.apex_speed_kph, corner.peak_lat_g, has_heavy_braking
        )
        bias = _brake_bias_for_corner(corner.entry_speed_kph, base_brake_bias_pct)
        tc_gain, tc_slip = _tc_for_corner(
            corner.speed_class, corner.peak_lat_g, base_tc_gain, base_tc_slip
        )
        diff_offset = _diff_preload_offset(corner.speed_class, corner.trail_brake_pct)

        # Build notes
        notes_parts = []
        if corner.speed_class == "low" and corner.peak_lat_g > 1.5:
            notes_parts.append("tight hairpin")
        if has_heavy_braking:
            notes_parts.append("heavy braking")
        if corner.trail_brake_pct > 0.4:
            notes_parts.append("deep trail brake")
        if corner.body_slip_peak_deg > 4.0:
            notes_parts.append(f"body slip {corner.body_slip_peak_deg:.1f}°")

        cm = CornerParameterMap(
            corner_id=corner.corner_id,
            corner_name=f"T{corner.corner_id}",
            speed_class=corner.speed_class,
            apex_speed_kph=corner.apex_speed_kph,
            peak_lat_g=corner.peak_lat_g,
            direction=corner.direction,
            rarb_blade=rarb,
            brake_bias_pct=bias,
            tc_gain=tc_gain,
            tc_slip=tc_slip,
            diff_preload_offset_nm=diff_offset,
            notes=", ".join(notes_parts),
        )
        corner_maps.append(cm)

        # Track cluster stats
        cluster_counts.setdefault(corner.speed_class, []).append(
            corner.apex_speed_kph
        )

    # Build cluster summary
    cluster_summary = {}
    for cls, speeds in cluster_counts.items():
        if speeds:
            cluster_summary[cls] = {
                "count": len(speeds),
                "avg_speed": sum(speeds) / len(speeds),
                "min_speed": min(speeds),
                "max_speed": max(speeds),
            }

    # Find binding corners
    binding_corners = _find_binding_corners(
        corners, front_excursion_limit_mm
    )

    # Mark binding corners in the map
    for bc in binding_corners:
        for cm in corner_maps:
            if cm.corner_id == bc.corner_id:
                cm.binding_for = bc.parameter
                break

    return CornerStrategy(
        corner_maps=corner_maps,
        binding_corners=binding_corners,
        cluster_summary=cluster_summary,
    )
