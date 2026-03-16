"""Solver modifiers — feedback loop from diagnosis + driver style to solver targets.

Computes adjustments to solver targets/outputs based on:
- Handling diagnosis (understeer, bottoming, settle time)
- Driver style (smooth vs aggressive)
- Measured telemetry

Core physics in each solver is UNCHANGED. Modifiers only adjust:
- Target values (DF balance, LLTD)
- Floor constraints (minimum spring rates)
- Output adjustments (damper click offsets, ζ scaling)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analyzer.diagnose import Diagnosis
    from analyzer.driver_style import DriverProfile
    from analyzer.extract import MeasuredState


@dataclass
class SolverModifiers:
    """Adjustments applied to solver targets and outputs."""

    # Step 1: Rake
    df_balance_offset_pct: float = 0.0

    # Step 2: Heave
    front_heave_min_floor_nmm: float = 0.0
    rear_third_min_floor_nmm: float = 0.0
    # Perch offset override (from heave travel exhaustion diagnosis)
    front_heave_perch_target_mm: float | None = None

    # Step 4: ARBs
    lltd_offset: float = 0.0

    # Step 6: Dampers
    front_ls_rbd_offset: int = 0
    rear_ls_rbd_offset: int = 0
    front_hs_comp_offset: int = 0
    rear_hs_comp_offset: int = 0
    damping_ratio_scale: float = 1.0

    # Reasoning trace
    reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = ["Solver Modifiers:"]
        if self.df_balance_offset_pct != 0:
            lines.append(f"  DF balance offset: {self.df_balance_offset_pct:+.2f}%")
        if self.front_heave_min_floor_nmm > 0:
            lines.append(f"  Front heave floor: {self.front_heave_min_floor_nmm:.0f} N/mm")
        if self.rear_third_min_floor_nmm > 0:
            lines.append(f"  Rear third floor: {self.rear_third_min_floor_nmm:.0f} N/mm")
        if self.front_heave_perch_target_mm is not None:
            lines.append(f"  Front heave perch target: {self.front_heave_perch_target_mm:.1f} mm")
        if self.lltd_offset != 0:
            lines.append(f"  LLTD offset: {self.lltd_offset:+.3f}")
        if self.damping_ratio_scale != 1.0:
            lines.append(f"  Damping ratio scale: {self.damping_ratio_scale:.3f}")
        damper_offsets = []
        if self.front_ls_rbd_offset:
            damper_offsets.append(f"F LS rbd {self.front_ls_rbd_offset:+d}")
        if self.rear_ls_rbd_offset:
            damper_offsets.append(f"R LS rbd {self.rear_ls_rbd_offset:+d}")
        if self.front_hs_comp_offset:
            damper_offsets.append(f"F HS comp {self.front_hs_comp_offset:+d}")
        if self.rear_hs_comp_offset:
            damper_offsets.append(f"R HS comp {self.rear_hs_comp_offset:+d}")
        if damper_offsets:
            lines.append(f"  Damper offsets: {', '.join(damper_offsets)}")
        if self.reasons:
            lines.append("  Reasons:")
            for r in self.reasons:
                lines.append(f"    - {r}")
        return "\n".join(lines)


def compute_modifiers(
    diagnosis: Diagnosis,
    driver: DriverProfile,
    measured: MeasuredState,
) -> SolverModifiers:
    """Compute solver modifiers from diagnosis, driver profile, and measurements.

    Parameters
    ----------
    diagnosis : Diagnosis
        Handling problems from diagnose().
    driver : DriverProfile
        Driver behavior classification.
    measured : MeasuredState
        Raw telemetry measurements.

    Returns
    -------
    SolverModifiers
    """
    mods = SolverModifiers()

    # ── From Diagnosis Problems ──
    for problem in diagnosis.problems:
        cat = problem.category
        symptom = problem.symptom.lower()

        # Balance: understeer/oversteer → LLTD offset
        if cat == "balance":
            if "understeer" in symptom and problem.measured > 2.5:
                mods.lltd_offset -= 0.02
                mods.reasons.append(
                    f"Understeer {problem.measured:.1f}° > 2.5° → LLTD -0.02 (less front LT)"
                )
            elif "oversteer" in symptom and problem.measured < -1.5:
                mods.lltd_offset += 0.02
                mods.reasons.append(
                    f"Oversteer {problem.measured:.1f}° → LLTD +0.02 (stabilize rear)"
                )

        # Balance: speed gradient → DF balance offset
        if cat == "balance" and "speed" in symptom and "gradient" in symptom:
            gradient = measured.understeer_high_speed_deg - measured.understeer_low_speed_deg
            if gradient > 1.5:
                mods.df_balance_offset_pct += 0.5
                mods.reasons.append(
                    f"Speed gradient +{gradient:.1f}° → DF balance +0.5% (more front DF)"
                )
            elif gradient < -1.5:
                mods.df_balance_offset_pct -= 0.5
                mods.reasons.append(
                    f"Speed gradient {gradient:.1f}° → DF balance -0.5% (more rear DF)"
                )

        # Safety: bottoming → heave floor (only for clean-track bottoming,
        # not kerb-only — kerb bottoming is a driving choice, not a setup failure)
        if cat == "safety" and "bottoming" in symptom and "kerb" not in symptom:
            if "front" in symptom and problem.measured > 5:
                # Floor estimate: use heave spring natural frequency constraint
                # Higher shock velocity → stiffer spring needed to control platform
                # BMW heave range is 30-50 N/mm; scale from p99 shock velocity
                sv_floor = max(30.0, 35.0 + measured.front_shock_vel_p99_mps * 50)
                mods.front_heave_min_floor_nmm = max(
                    mods.front_heave_min_floor_nmm,
                    sv_floor,
                )
                mods.reasons.append(
                    f"Front bottoming {problem.measured:.0f} events → heave floor raised"
                )

        # Safety: heave spring travel exhaustion -> perch adjustment
        # Match both "exhausted under braking" and "used at speed" symptom strings
        if cat == "safety" and "travel" in symptom and ("exhausted" in symptom or "used" in symptom):
            travel_pct = problem.measured
            if travel_pct > 85:
                # More negative perch lowers the slider and consumes available
                # travel. When the front heave spring is running out of travel,
                # the target must move LESS negative so the slider regains room.
                mods.front_heave_perch_target_mm = -11.0
                mods.reasons.append(
                    f"Heave travel {travel_pct:.0f}% exhausted -> perch target -11mm "
                    f"(less negative perch preserves available slider travel)"
                )

        # Damper: settle time
        if cat == "damper" and "settle" in symptom:
            if problem.measured > 300:
                mods.front_ls_rbd_offset += 1
                mods.rear_ls_rbd_offset += 1
                mods.reasons.append(
                    f"Settle time {problem.measured:.0f}ms > 300ms → LS rbd +1 (underdamped)"
                )
            elif problem.measured < 50 and problem.severity != "minor":
                mods.front_ls_rbd_offset -= 1
                mods.rear_ls_rbd_offset -= 1
                mods.reasons.append(
                    f"Settle time {problem.measured:.0f}ms < 50ms → LS rbd -1 (overdamped)"
                )

    # ── From Heave Shock Velocity (platform stability) ──
    if measured.front_heave_vel_hs_pct > 33:
        # >33% of heave velocity in HS regime = platform is getting pounded by surface
        mods.front_heave_min_floor_nmm = max(mods.front_heave_min_floor_nmm, 40.0)
        mods.reasons.append(
            f"Heave HS regime {measured.front_heave_vel_hs_pct:.0f}% > 33% → heave floor 40 N/mm"
        )
    if measured.front_heave_vel_p95_mps > 0.35:
        # Very high heave velocity → increase HS damping to control platform
        mods.front_hs_comp_offset += 1
        mods.reasons.append(
            f"Heave vel p95 {measured.front_heave_vel_p95_mps:.3f} m/s > 0.35 → F HS comp +1"
        )

    # ── From Pitch Dynamics (platform stability) ──
    if measured.pitch_range_deg > 1.5:
        mods.front_heave_min_floor_nmm = max(mods.front_heave_min_floor_nmm, 38.0)
        mods.reasons.append(
            f"Pitch range {measured.pitch_range_deg:.2f}° > 1.5° → heave floor 38 N/mm"
        )

    # ── From Directional Understeer (balance weighting) ──
    us_left = measured.understeer_left_turn_deg
    us_right = measured.understeer_right_turn_deg
    if abs(us_left) > 0.05 and abs(us_right) > 0.05:
        directional_delta = us_left - us_right
        # If one direction has significantly more understeer, weight LLTD offset
        # toward fixing the dominant direction
        if abs(directional_delta) > 0.3:
            # Positive delta = more US in left turns; negative = more US in right
            # Scale LLTD offset by directional bias (max ±0.01 additional offset)
            directional_lltd_adj = -0.01 if directional_delta > 0 else 0.01
            mods.lltd_offset += directional_lltd_adj
            mods.reasons.append(
                f"Directional US asymmetry: left={us_left:.2f}° right={us_right:.2f}° "
                f"→ LLTD {directional_lltd_adj:+.3f}"
            )

    # ── From Corner Deflections (travel proximity) ──
    if measured.front_corner_defl_p99_mm > 30:
        mods.front_heave_min_floor_nmm = max(mods.front_heave_min_floor_nmm, 35.0)
        mods.reasons.append(
            f"Front corner defl p99 {measured.front_corner_defl_p99_mm:.1f}mm > 30mm "
            f"→ heave floor 35 N/mm (travel proximity)"
        )

    # ── From Driver Style ──
    if driver.steering_smoothness == "smooth":
        mods.damping_ratio_scale *= 0.92
        mods.reasons.append("Smooth driver → ζ × 0.92 (more compliance)")
    elif driver.steering_smoothness == "aggressive":
        mods.front_hs_comp_offset += 1
        mods.reasons.append("Aggressive steering → F HS comp +1 (HS event control)")

    if driver.cornering_aggression == "limit":
        mods.front_hs_comp_offset += 1
        mods.rear_hs_comp_offset += 1
        mods.reasons.append("Limit cornering → HS comp +1 F+R (peak load control)")

    # Aggressive + inconsistent → stiffer for forgiveness
    if driver.style.startswith("aggressive") and driver.consistency == "erratic":
        mods.damping_ratio_scale *= 1.05
        mods.reasons.append("Aggressive-erratic → ζ × 1.05 (forgiveness)")

    # Clamp cumulative offsets to reasonable ranges
    mods.lltd_offset = max(-0.05, min(0.05, mods.lltd_offset))
    mods.df_balance_offset_pct = max(-1.5, min(1.5, mods.df_balance_offset_pct))
    mods.front_ls_rbd_offset = max(-2, min(2, mods.front_ls_rbd_offset))
    mods.rear_ls_rbd_offset = max(-2, min(2, mods.rear_ls_rbd_offset))
    mods.front_hs_comp_offset = max(-2, min(2, mods.front_hs_comp_offset))
    mods.rear_hs_comp_offset = max(-2, min(2, mods.rear_hs_comp_offset))
    mods.damping_ratio_scale = max(0.80, min(1.20, mods.damping_ratio_scale))

    return mods
