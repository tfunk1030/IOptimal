"""Recommendation engine — generate parameter adjustments and model corrections.

For "tweak" discrepancies: suggest specific parameter changes within the
current model framework.

For "rethink" discrepancies: identify which model assumption failed and
what data is needed to fix it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from validator.compare import Comparison
from validator.classify import ValidationResult
from validator.extract import MeasuredState
from track_model.profile import TrackProfile
from car_model.cars import CarModel


@dataclass
class ParameterAdjustment:
    """A specific parameter change recommendation."""
    step: int
    parameter: str
    current_value: float
    recommended_value: float
    units: str
    reasoning: str


@dataclass
class ModelCorrection:
    """A model calibration constant that needs updating."""
    model_component: str         # e.g. "aero_compression.rear_compression_mm"
    current_value: float
    corrected_value: float
    units: str
    reasoning: str
    confidence: str              # "high" / "medium" / "low"


@dataclass
class FeedbackOutput:
    """Complete feedback for the next iteration."""
    parameter_adjustments: list[ParameterAdjustment] = field(default_factory=list)
    model_corrections: list[ModelCorrection] = field(default_factory=list)
    updated_track_profile: TrackProfile | None = None
    rerun_solver: bool = False
    rerun_from_step: int = 1     # Which step to restart from


def generate_recommendations(
    result: ValidationResult,
    measured: MeasuredState,
    solver_json: dict,
    car: CarModel,
) -> FeedbackOutput:
    """Generate actionable recommendations from validation results.

    Args:
        result: ValidationResult from classify_discrepancies()
        measured: MeasuredState from extract_measurements()
        solver_json: Loaded solver output JSON
        car: Car model for parameter ranges

    Returns:
        FeedbackOutput with adjustments, corrections, and next-iteration data
    """
    output = FeedbackOutput()

    step1 = solver_json.get("step1_rake", {})
    step2 = solver_json.get("step2_heave", {})

    all_issues = result.tweaks + result.rethinks

    for comp in all_issues:
        if comp.step == 1:
            _recommend_step1(comp, measured, step1, car, output)
        elif comp.step == 2:
            _recommend_step2(comp, measured, step2, car, output)
        elif comp.step == 3:
            _recommend_step3(comp, measured, output)
        elif comp.step == 4:
            _recommend_step4(comp, measured, solver_json, output)
        elif comp.step == 5:
            _recommend_step5(comp, measured, output)
        elif comp.step == 6:
            _recommend_step6(comp, measured, output)

    # If shock velocities changed significantly, provide updated track profile
    sv_changed = any(
        c.parameter.startswith("shock_vel_p99") and abs(c.delta_pct) > 10
        for c in all_issues
    )
    if sv_changed and measured.measured_track_profile is not None:
        output.updated_track_profile = measured.measured_track_profile
        output.rerun_solver = True
        output.rerun_from_step = min(output.rerun_from_step, 1)

    # Determine if re-run is needed
    if not output.rerun_solver and (output.model_corrections or output.parameter_adjustments):
        output.rerun_solver = True
        # Find the earliest step that needs correction
        steps_affected = set()
        for adj in output.parameter_adjustments:
            steps_affected.add(adj.step)
        for mc in output.model_corrections:
            # Model corrections typically require re-running from Step 1
            steps_affected.add(1)
        if steps_affected:
            output.rerun_from_step = min(steps_affected)

    return output


def _recommend_step1(
    comp: Comparison,
    measured: MeasuredState,
    step1: dict,
    car: CarModel,
    output: FeedbackOutput,
) -> None:
    """Recommendations for Step 1 (ride height) discrepancies."""

    if comp.parameter == "aero_compression_front_mm":
        # V^2 compression model is wrong — recalibrate
        output.model_corrections.append(ModelCorrection(
            model_component="aero_compression.front_compression_mm",
            current_value=comp.predicted,
            corrected_value=round(measured.aero_compression_front_mm, 1),
            units="mm",
            reasoning=(
                f"Measured front aero compression = {measured.aero_compression_front_mm:.1f}mm "
                f"vs predicted {comp.predicted:.1f}mm. "
                f"Update car_model aero compression to match."
            ),
            confidence="high" if abs(comp.delta) < 5 else "medium",
        ))

    elif comp.parameter == "aero_compression_rear_mm":
        output.model_corrections.append(ModelCorrection(
            model_component="aero_compression.rear_compression_mm",
            current_value=comp.predicted,
            corrected_value=round(measured.aero_compression_rear_mm, 1),
            units="mm",
            reasoning=(
                f"Measured rear aero compression = {measured.aero_compression_rear_mm:.1f}mm "
                f"vs predicted {comp.predicted:.1f}mm. "
                f"Update car_model aero compression to match."
            ),
            confidence="high" if abs(comp.delta) < 8 else "medium",
        ))

    elif comp.parameter == "bottoming_events_front":
        if comp.measured > 0:
            output.parameter_adjustments.append(ParameterAdjustment(
                step=1,
                parameter="front_heave_nmm",
                current_value=0,  # Will be filled from step2 data
                recommended_value=0,
                units="N/mm",
                reasoning=(
                    f"Front bottoming detected ({int(comp.measured)} events). "
                    f"Either increase front heave spring stiffness or raise "
                    f"front static ride height."
                ),
            ))

    elif comp.parameter == "bottoming_events_rear":
        if comp.measured > 0:
            output.parameter_adjustments.append(ParameterAdjustment(
                step=1,
                parameter="rear_third_nmm",
                current_value=0,  # Will be filled from step2 data
                recommended_value=0,
                units="N/mm",
                reasoning=(
                    f"Rear bottoming detected ({int(comp.measured)} events). "
                    f"Either increase rear third spring stiffness or raise "
                    f"rear static ride height."
                ),
            ))

    elif comp.parameter == "vortex_burst_events":
        if comp.measured > 0:
            output.parameter_adjustments.append(ParameterAdjustment(
                step=1,
                parameter="static_front_rh_mm",
                current_value=step1.get("static_front_rh_mm", 30.0),
                recommended_value=step1.get("static_front_rh_mm", 30.0) + 2.0,
                units="mm",
                reasoning=(
                    f"SAFETY: Vortex burst events detected ({int(comp.measured)}). "
                    f"Front RH dropping below threshold. Raise front static RH by 2mm "
                    f"or stiffen front heave spring."
                ),
            ))


def _recommend_step2(
    comp: Comparison,
    measured: MeasuredState,
    step2: dict,
    car: CarModel,
    output: FeedbackOutput,
) -> None:
    """Recommendations for Step 2 (platform stability) discrepancies."""

    if comp.parameter == "front_excursion_p99_mm":
        # Recalibrate m_eff from measured excursion
        # excursion = v_p99 * sqrt(m_eff / k) * 1000
        # m_eff = k * (excursion / (v_p99 * 1000))^2
        k_front = step2.get("front_heave_nmm", 50)
        v_p99 = measured.front_shock_vel_p99_mps
        exc_mm = measured.front_rh_excursion_measured_mm

        if v_p99 > 0 and exc_mm > 0:
            k_nm = k_front * 1000.0
            v_mm = v_p99 * 1000.0
            new_m_eff = k_nm * (exc_mm / v_mm) ** 2

            output.model_corrections.append(ModelCorrection(
                model_component="heave_spring.front_m_eff_kg",
                current_value=car.heave_spring.front_m_eff_kg,
                corrected_value=round(new_m_eff, 1),
                units="kg",
                reasoning=(
                    f"Measured excursion = {exc_mm:.1f}mm at v_p99 = {v_p99:.4f} m/s "
                    f"with k = {k_front:.0f} N/mm. "
                    f"Recalibrated m_eff = {new_m_eff:.1f} kg "
                    f"(was {car.heave_spring.front_m_eff_kg:.1f} kg)."
                ),
                confidence="high",
            ))

    elif comp.parameter.startswith("shock_vel_p99"):
        axle = "front" if "front" in comp.parameter else "rear"
        output.model_corrections.append(ModelCorrection(
            model_component=f"track_profile.shock_vel_p99_{axle}_mps",
            current_value=comp.predicted,
            corrected_value=comp.measured,
            units="m/s",
            reasoning=(
                f"{axle.capitalize()} shock vel p99 changed from "
                f"{comp.predicted:.4f} to {comp.measured:.4f} m/s "
                f"({comp.delta_pct:+.1f}%). Track surface has changed. "
                f"Rebuild track profile from this IBT session."
            ),
            confidence="high",
        ))


def _recommend_step3(
    comp: Comparison,
    measured: MeasuredState,
    output: FeedbackOutput,
) -> None:
    """Recommendations for Step 3 (corner spring) discrepancies."""

    if comp.parameter.endswith("_natural_freq_hz"):
        axle = "front" if "front" in comp.parameter else "rear"
        output.parameter_adjustments.append(ParameterAdjustment(
            step=3,
            parameter=f"{axle}_natural_freq_hz",
            current_value=comp.predicted,
            recommended_value=comp.measured,
            units="Hz",
            reasoning=(
                f"Measured {axle} natural frequency = {comp.measured:.2f} Hz "
                f"vs predicted {comp.predicted:.2f} Hz. "
                f"Check spring rate or effective mass for {axle} axle. "
                f"If mass is correct, the spring rate in the model may be "
                f"inaccurate (torsion constant C or motion ratio)."
            ),
        ))


def _recommend_step4(
    comp: Comparison,
    measured: MeasuredState,
    solver_json: dict,
    output: FeedbackOutput,
) -> None:
    """Recommendations for Step 4 (balance) discrepancies."""
    step4 = solver_json.get("step4_arb", {})

    if comp.parameter == "lltd":
        # The validator no longer produces LLTD comparisons from IBT data because
        # that signal is a roll-distribution proxy, not true wheel-load LLTD.
        # Keep this branch inert for backward-compatible saved validation files.
        return

    elif comp.parameter == "roll_gradient_deg_per_g":
        output.model_corrections.append(ModelCorrection(
            model_component="roll_stiffness_model",
            current_value=comp.predicted,
            corrected_value=comp.measured,
            units="deg/g",
            reasoning=(
                f"Roll gradient measured = {comp.measured:.3f} deg/g "
                f"vs predicted {comp.predicted:.3f} deg/g. "
                f"Total roll stiffness is {'too high' if comp.delta > 0 else 'too low'}. "
                f"Check CG height, spring-to-wheel motion ratios, "
                f"or ARB stiffness calibration."
            ),
            confidence="medium",
        ))

    elif comp.parameter == "body_roll_at_peak_g":
        output.model_corrections.append(ModelCorrection(
            model_component="body_roll_model",
            current_value=comp.predicted,
            corrected_value=comp.measured,
            units="deg",
            reasoning=(
                f"Body roll at peak g measured = {comp.measured:.1f} deg "
                f"vs predicted {comp.predicted:.1f} deg. "
                f"This directly affects camber calculation (Step 5). "
                f"Check roll stiffness distribution and CG height."
            ),
            confidence="medium" if abs(comp.delta) < 0.8 else "low",
        ))

    elif comp.parameter == "understeer_mean_deg":
        # Understeer angle too high or too low — balance problem
        if comp.measured < 0:
            # Oversteer
            output.parameter_adjustments.append(ParameterAdjustment(
                step=4,
                parameter="rear_arb_softer",
                current_value=comp.measured,
                recommended_value=1.0,
                units="deg",
                reasoning=(
                    f"OVERSTEER detected: mean understeer angle = {comp.measured:.1f} deg "
                    f"(negative = oversteer). Soften rear ARB or increase front roll "
                    f"stiffness to shift LLTD forward. If only at high speed, "
                    f"increase rear wing/DF balance."
                ),
            ))
        elif comp.measured > 2.5:
            # Excessive understeer
            output.parameter_adjustments.append(ParameterAdjustment(
                step=4,
                parameter="rear_arb_stiffer",
                current_value=comp.measured,
                recommended_value=1.0,
                units="deg",
                reasoning=(
                    f"UNDERSTEER detected: mean understeer angle = {comp.measured:.1f} deg. "
                    f"Stiffen rear ARB or soften front to shift LLTD rearward. "
                    f"If only at low speed, check diff preload."
                ),
            ))

    elif comp.parameter == "understeer_speed_gradient":
        if comp.measured > 1.0:
            output.parameter_adjustments.append(ParameterAdjustment(
                step=4,
                parameter="aero_balance",
                current_value=comp.measured,
                recommended_value=0.0,
                units="deg",
                reasoning=(
                    f"More understeer at high speed than low speed "
                    f"(gradient = {comp.measured:+.1f} deg). "
                    f"This means too much front aero load relative to rear. "
                    f"Reduce front DF balance (raise front RH or lower rear RH)."
                ),
            ))
        elif comp.measured < -1.0:
            output.parameter_adjustments.append(ParameterAdjustment(
                step=4,
                parameter="aero_balance",
                current_value=comp.measured,
                recommended_value=0.0,
                units="deg",
                reasoning=(
                    f"More oversteer at high speed than low speed "
                    f"(gradient = {comp.measured:+.1f} deg). "
                    f"DANGEROUS: rear aero insufficient. "
                    f"Increase rear DF balance (lower front RH or raise rear RH)."
                ),
            ))

    elif comp.parameter == "body_slip_p95_deg":
        if comp.measured > 4.0:
            output.parameter_adjustments.append(ParameterAdjustment(
                step=4,
                parameter="rear_stability",
                current_value=comp.measured,
                recommended_value=2.0,
                units="deg",
                reasoning=(
                    f"Excessive body slip angle ({comp.measured:.1f} deg p95). "
                    f"Rear axle is sliding excessively. Check rear spring rates, "
                    f"rear ARB stiffness, and rear damper rebound. "
                    f"May also indicate rear tyre pressure too low."
                ),
            ))


def _recommend_step5(
    comp: Comparison,
    measured: MeasuredState,
    output: FeedbackOutput,
) -> None:
    """Recommendations for Step 5 (geometry) discrepancies from tyre data."""

    if comp.parameter == "front_tyre_temp_spread":
        spread = comp.measured
        if spread > 5.0:
            output.parameter_adjustments.append(ParameterAdjustment(
                step=5,
                parameter="front_camber",
                current_value=spread,
                recommended_value=0.0,
                units="C",
                reasoning=(
                    f"Front inner-outer temp spread = {spread:+.1f}C. "
                    f"Inner tyres too hot -> too much negative camber. "
                    f"Reduce front negative camber by ~0.2 deg per 4C spread."
                ),
            ))
        elif spread < -5.0:
            output.parameter_adjustments.append(ParameterAdjustment(
                step=5,
                parameter="front_camber",
                current_value=spread,
                recommended_value=0.0,
                units="C",
                reasoning=(
                    f"Front inner-outer temp spread = {spread:+.1f}C. "
                    f"Outer tyres too hot -> not enough negative camber. "
                    f"Increase front negative camber by ~0.2 deg per 4C spread."
                ),
            ))

    elif comp.parameter == "rear_tyre_temp_spread":
        spread = comp.measured
        if spread > 5.0:
            output.parameter_adjustments.append(ParameterAdjustment(
                step=5,
                parameter="rear_camber",
                current_value=spread,
                recommended_value=0.0,
                units="C",
                reasoning=(
                    f"Rear inner-outer temp spread = {spread:+.1f}C. "
                    f"Too much negative camber on rears. "
                    f"Reduce rear negative camber by ~0.2 deg per 4C spread."
                ),
            ))
        elif spread < -5.0:
            output.parameter_adjustments.append(ParameterAdjustment(
                step=5,
                parameter="rear_camber",
                current_value=spread,
                recommended_value=0.0,
                units="C",
                reasoning=(
                    f"Rear inner-outer temp spread = {spread:+.1f}C. "
                    f"Not enough negative camber on rears. "
                    f"Increase rear negative camber by ~0.2 deg per 4C spread."
                ),
            ))


def _recommend_step6(
    comp: Comparison,
    measured: MeasuredState,
    output: FeedbackOutput,
) -> None:
    """Recommendations for Step 6 (damper) discrepancies."""

    if comp.parameter.startswith("shock_vel_p95"):
        axle = "front" if "front" in comp.parameter else "rear"
        output.parameter_adjustments.append(ParameterAdjustment(
            step=6,
            parameter=f"{axle}_hs_reference_velocity",
            current_value=comp.predicted,
            recommended_value=comp.measured,
            units="m/s",
            reasoning=(
                f"Measured {axle} p95 shock velocity = {comp.measured:.4f} m/s "
                f"vs track profile {comp.predicted:.4f} m/s. "
                f"HS damper reference velocity should use the measured value. "
                f"This may change HS comp/rbd click values."
            ),
        ))

    elif comp.parameter == "yaw_rate_correlation":
        if comp.measured < 0.80:
            output.parameter_adjustments.append(ParameterAdjustment(
                step=6,
                parameter="damper_tuning",
                current_value=comp.measured,
                recommended_value=0.90,
                units="R^2",
                reasoning=(
                    f"Low yaw rate correlation ({comp.measured:.3f}) indicates "
                    f"inconsistent transient response. The car does not track "
                    f"steering inputs predictably. Check LS rebound damping "
                    f"(controls weight transfer rate) and HS comp (controls "
                    f"ride height recovery after bumps). Increase LS rbd +1 "
                    f"if the car oscillates after direction changes."
                ),
            ))
