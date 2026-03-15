"""Output module — iRacing .sto setup file writer.

Generates iRacing-compatible setup files from solver output by mapping
solver parameters to CarSetup_* XML IDs used in iRacing's LDX/STO format.

The .sto format is XML with the same structure as .ldx (telemetry data files),
but only containing the CarSetup_* parameters that define the car's setup.

Usage:
    from output.setup_writer import write_sto
    write_sto(car, step1, step2, step3, step4, step5, step6,
              wing=17.0, fuel_l=89.0, output_path="output/bmw_sebring.sto")
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree, indent

from solver.rake_solver import RakeSolution
from solver.heave_solver import HeaveSolution
from solver.corner_spring_solver import CornerSpringSolution
from solver.arb_solver import ARBSolution
from solver.wheel_geometry_solver import WheelGeometrySolution
from solver.damper_solver import DamperSolution


def _numeric(parent: Element, param_id: str, value: float | int, unit: str) -> None:
    """Add a Numeric element to the XML."""
    SubElement(parent, "Numeric", Id=param_id, Value=str(value), Unit=unit)


def _string(parent: Element, param_id: str, value: str, unit: str = "") -> None:
    """Add a String element to the XML."""
    SubElement(parent, "String", Id=param_id, Value=value, Unit=unit)


def _comment(parent: Element, text: str) -> None:
    """Add an XML comment node (used for TODO stubs in unsupported cars)."""
    from xml.etree.ElementTree import Comment
    parent.append(Comment(f" {text} "))


# ─────────────────────────────────────────────────────────────────────────────
# BMW M Hybrid V8 LMDh — CarSetup_* ID mappings
# ─────────────────────────────────────────────────────────────────────────────

_BMW_PARAM_IDS: dict[str, str] = {
    # Aero
    "wing_angle":               "CarSetup_TiresAero_AeroSettings_RearWingAngle",
    "front_rh_at_speed":        "CarSetup_TiresAero_AeroCalculator_FrontRhAtSpeed",
    "rear_rh_at_speed":         "CarSetup_TiresAero_AeroCalculator_RearRhAtSpeed",
    "df_balance":               "CarSetup_TiresAero_AeroCalculator_DownforceBalance",
    "ld_ratio":                 "CarSetup_TiresAero_AeroCalculator_LD",
    # Ride heights
    "lf_ride_height":           "CarSetup_Chassis_LeftFront_RideHeight",
    "rf_ride_height":           "CarSetup_Chassis_RightFront_RideHeight",
    "lr_ride_height":           "CarSetup_Chassis_LeftRear_RideHeight",
    "rr_ride_height":           "CarSetup_Chassis_RightRear_RideHeight",
    # Pushrods
    "front_pushrod_offset":     "CarSetup_Chassis_Front_PushrodLengthOffset",
    "rear_pushrod_offset":      "CarSetup_Chassis_Rear_PushrodLengthOffset",
    # Heave/third springs
    "front_heave_spring":       "CarSetup_Chassis_Front_HeaveSpring",
    "front_heave_perch":        "CarSetup_Chassis_Front_HeavePerchOffset",
    "front_heave_defl_max":     "CarSetup_Chassis_Front_HeaveSliderDeflMax",
    "rear_third_spring":        "CarSetup_Chassis_Rear_ThirdSpring",
    "rear_third_perch":         "CarSetup_Chassis_Rear_ThirdPerchOffset",
    "rear_third_defl_max":      "CarSetup_Chassis_Rear_ThirdSliderDeflMax",
    # Corner springs — BMW uses torsion bar front, coil rear
    "lf_torsion_od":            "CarSetup_Chassis_LeftFront_TorsionBarOD",
    "rf_torsion_od":            "CarSetup_Chassis_RightFront_TorsionBarOD",
    "lf_torsion_turns":         "CarSetup_Chassis_LeftFront_TorsionBarTurns",
    "rf_torsion_turns":         "CarSetup_Chassis_RightFront_TorsionBarTurns",
    "lr_spring_rate":           "CarSetup_Chassis_LeftRear_SpringRate",
    "rr_spring_rate":           "CarSetup_Chassis_RightRear_SpringRate",
    "lr_spring_perch":          "CarSetup_Chassis_LeftRear_SpringPerchOffset",
    "rr_spring_perch":          "CarSetup_Chassis_RightRear_SpringPerchOffset",
    # Shock deflection maxes
    "lf_shock_defl_max":        "CarSetup_Chassis_LeftFront_ShockDeflMax",
    "rf_shock_defl_max":        "CarSetup_Chassis_RightFront_ShockDeflMax",
    "lr_shock_defl_max":        "CarSetup_Chassis_LeftRear_ShockDeflMax",
    "rr_shock_defl_max":        "CarSetup_Chassis_RightRear_ShockDeflMax",
    # ARBs
    "front_arb_size":           "CarSetup_Chassis_Front_ArbSize",
    "front_arb_blades":         "CarSetup_Chassis_Front_ArbBlades",
    "rear_arb_size":            "CarSetup_Chassis_Rear_ArbSize",
    "rear_arb_blades":          "CarSetup_Chassis_Rear_ArbBlades",
    # Cross weight
    "cross_weight":             "CarSetup_Chassis_Rear_CrossWeight",
    # Wheel geometry
    "lf_camber":                "CarSetup_Chassis_LeftFront_Camber",
    "rf_camber":                "CarSetup_Chassis_RightFront_Camber",
    "lr_camber":                "CarSetup_Chassis_LeftRear_Camber",
    "rr_camber":                "CarSetup_Chassis_RightRear_Camber",
    "front_toe":                "CarSetup_Chassis_Front_ToeIn",
    "lr_toe":                   "CarSetup_Chassis_LeftRear_ToeIn",
    "rr_toe":                   "CarSetup_Chassis_RightRear_ToeIn",
    # Dampers — BMW naming (LsCompDamping etc.)
    "lf_ls_comp":               "CarSetup_Chassis_LeftFront_LsCompDamping",
    "lf_ls_rbd":                "CarSetup_Chassis_LeftFront_LsRbdDamping",
    "lf_hs_comp":               "CarSetup_Chassis_LeftFront_HsCompDamping",
    "lf_hs_rbd":                "CarSetup_Chassis_LeftFront_HsRbdDamping",
    "lf_hs_slope":              "CarSetup_Chassis_LeftFront_HsCompDampSlope",
    "rf_ls_comp":               "CarSetup_Chassis_RightFront_LsCompDamping",
    "rf_ls_rbd":                "CarSetup_Chassis_RightFront_LsRbdDamping",
    "rf_hs_comp":               "CarSetup_Chassis_RightFront_HsCompDamping",
    "rf_hs_rbd":                "CarSetup_Chassis_RightFront_HsRbdDamping",
    "rf_hs_slope":              "CarSetup_Chassis_RightFront_HsCompDampSlope",
    "lr_ls_comp":               "CarSetup_Chassis_LeftRear_LsCompDamping",
    "lr_ls_rbd":                "CarSetup_Chassis_LeftRear_LsRbdDamping",
    "lr_hs_comp":               "CarSetup_Chassis_LeftRear_HsCompDamping",
    "lr_hs_rbd":                "CarSetup_Chassis_LeftRear_HsRbdDamping",
    "lr_hs_slope":              "CarSetup_Chassis_LeftRear_HsCompDampSlope",
    "rr_ls_comp":               "CarSetup_Chassis_RightRear_LsCompDamping",
    "rr_ls_rbd":                "CarSetup_Chassis_RightRear_LsRbdDamping",
    "rr_hs_comp":               "CarSetup_Chassis_RightRear_HsCompDamping",
    "rr_hs_rbd":                "CarSetup_Chassis_RightRear_HsRbdDamping",
    "rr_hs_slope":              "CarSetup_Chassis_RightRear_HsCompDampSlope",
    # Tyres
    "lf_pressure":              "CarSetup_TiresAero_LeftFront_StartingPressure",
    "rf_pressure":              "CarSetup_TiresAero_RightFront_StartingPressure",
    "lr_pressure":              "CarSetup_TiresAero_LeftRearTire_StartingPressure",
    "rr_pressure":              "CarSetup_TiresAero_RightRearTire_StartingPressure",
    "tyre_type":                "CarSetup_TiresAero_TireType_TireType",
    # Brakes
    "brake_bias":               "CarSetup_BrakesDriveUnit_BrakeSpec_BrakePressureBias",
    "brake_bias_migration":     "CarSetup_BrakesDriveUnit_BrakeSpec_BrakeBiasMigration",
    "brake_bias_target":        "CarSetup_BrakesDriveUnit_BrakeSpec_BrakeBiasTarget",
    "pad_compound":             "CarSetup_BrakesDriveUnit_BrakeSpec_PadCompound",
    "front_master_cyl":         "CarSetup_BrakesDriveUnit_BrakeSpec_FrontMasterCyl",
    "rear_master_cyl":          "CarSetup_BrakesDriveUnit_BrakeSpec_RearMasterCyl",
    # Diff
    "diff_coast_drive_ramp":    "CarSetup_BrakesDriveUnit_RearDiffSpec_CoastDriveRampAngles",
    "diff_clutch_plates":       "CarSetup_BrakesDriveUnit_RearDiffSpec_ClutchFrictionPlates",
    "diff_preload":             "CarSetup_BrakesDriveUnit_RearDiffSpec_Preload",
    # TC
    "tc_gain":                  "CarSetup_BrakesDriveUnit_TractionControl_TractionControlGain",
    "tc_slip":                  "CarSetup_BrakesDriveUnit_TractionControl_TractionControlSlip",
    # Fuel
    "fuel_level":               "CarSetup_BrakesDriveUnit_Fuel_FuelLevel",
    "fuel_low_warning":         "CarSetup_BrakesDriveUnit_Fuel_FuelLowWarning",
    # Gears
    "gear_stack":               "CarSetup_BrakesDriveUnit_GearRatios_GearStack",
    # Gear speeds (BMW Short stack — constants from real LDX data, Unit="Km/h")
    "speed_in_first":           "CarSetup_BrakesDriveUnit_GearRatios_SpeedInFirst",
    "speed_in_second":          "CarSetup_BrakesDriveUnit_GearRatios_SpeedInSecond",
    "speed_in_third":           "CarSetup_BrakesDriveUnit_GearRatios_SpeedInThird",
    "speed_in_fourth":          "CarSetup_BrakesDriveUnit_GearRatios_SpeedInFourth",
    "speed_in_fifth":           "CarSetup_BrakesDriveUnit_GearRatios_SpeedInFifth",
    "speed_in_sixth":           "CarSetup_BrakesDriveUnit_GearRatios_SpeedInSixth",
    "speed_in_seventh":         "CarSetup_BrakesDriveUnit_GearRatios_SpeedInSeventh",
    # Lighting
    "roof_light_color":         "CarSetup_BrakesDriveUnit_Lighting_RoofIdLightColor",
    # Update counter
    "update_count":             "CarSetup_UpdateCount",
    # Computed display values — static shock deflections
    "lf_shock_defl_static":     "CarSetup_Chassis_LeftFront_ShockDeflStatic",
    "rf_shock_defl_static":     "CarSetup_Chassis_RightFront_ShockDeflStatic",
    "lr_shock_defl_static":     "CarSetup_Chassis_LeftRear_ShockDeflStatic",
    "rr_shock_defl_static":     "CarSetup_Chassis_RightRear_ShockDeflStatic",
    # Computed display values — torsion bar deflections
    "lf_torsion_defl":          "CarSetup_Chassis_LeftFront_TorsionBarDefl",
    "rf_torsion_defl":          "CarSetup_Chassis_RightFront_TorsionBarDefl",
    # Computed display values — front heave spring/slider deflections
    "front_heave_spring_defl_static":  "CarSetup_Chassis_Front_HeaveSpringDeflStatic",
    "front_heave_spring_defl_max":     "CarSetup_Chassis_Front_HeaveSpringDeflMax",
    "front_heave_slider_defl_static":  "CarSetup_Chassis_Front_HeaveSliderDeflStatic",
    # Computed display values — rear coil spring deflections
    "lr_spring_defl_static":    "CarSetup_Chassis_LeftRear_SpringDeflStatic",
    "rr_spring_defl_static":    "CarSetup_Chassis_RightRear_SpringDeflStatic",
    "lr_spring_defl_max":       "CarSetup_Chassis_LeftRear_SpringDeflMax",
    "rr_spring_defl_max":       "CarSetup_Chassis_RightRear_SpringDeflMax",
    # Computed display values — rear third spring/slider deflections
    "rear_third_spring_defl_static":   "CarSetup_Chassis_Rear_ThirdSpringDeflStatic",
    "rear_third_spring_defl_max":      "CarSetup_Chassis_Rear_ThirdSpringDeflMax",
    "rear_third_slider_defl_static":   "CarSetup_Chassis_Rear_ThirdSliderDeflStatic",
    # Corner weights (physics-computed Newtons)
    "lf_corner_weight":         "CarSetup_Chassis_LeftFront_CornerWeight",
    "rf_corner_weight":         "CarSetup_Chassis_RightFront_CornerWeight",
    "lr_corner_weight":         "CarSetup_Chassis_LeftRear_CornerWeight",
    "rr_corner_weight":         "CarSetup_Chassis_RightRear_CornerWeight",
}


# ─────────────────────────────────────────────────────────────────────────────
# Ferrari 499P — partial mappings (known/verified IDs)
# Notable differences vs BMW:
#   - Front spring: indexed pushrod length delta (PushrodLengthDelta[0]/[1])
#   - ARBs: indexed arrays (ArbBlades[0], ArbBlades[1] for F/R)
#   - Rear spring: torsion bar (not decoded yet — placeholder)
# ─────────────────────────────────────────────────────────────────────────────

_FERRARI_PARAM_IDS: dict[str, str] = {
    "wing_angle":               "CarSetup_TiresAero_AeroSettings_RearWingAngle",
    "lf_ride_height":           "CarSetup_Chassis_LeftFront_RideHeight",
    "rf_ride_height":           "CarSetup_Chassis_RightFront_RideHeight",
    "lr_ride_height":           "CarSetup_Chassis_LeftRear_RideHeight",
    "rr_ride_height":           "CarSetup_Chassis_RightRear_RideHeight",
    "front_pushrod_offset":     "CarSetup_Chassis_Front_PushrodLengthDelta",
    "rear_pushrod_offset":      "CarSetup_Chassis_Rear_PushrodLengthDelta",
    # ARBs — Ferrari uses indexed blade arrays
    "front_arb_blades":         "CarSetup_Chassis_Front_ArbBlades[0]",
    "rear_arb_blades":          "CarSetup_Chassis_Rear_ArbBlades[0]",
    # Camber / toe (same as BMW)
    "lf_camber":                "CarSetup_Chassis_LeftFront_Camber",
    "rf_camber":                "CarSetup_Chassis_RightFront_Camber",
    "lr_camber":                "CarSetup_Chassis_LeftRear_Camber",
    "rr_camber":                "CarSetup_Chassis_RightRear_Camber",
    "front_toe":                "CarSetup_Chassis_Front_ToeIn",
    "lr_toe":                   "CarSetup_Chassis_LeftRear_ToeIn",
    "rr_toe":                   "CarSetup_Chassis_RightRear_ToeIn",
    # Fuel / brakes (same as BMW)
    "fuel_level":               "CarSetup_BrakesDriveUnit_Fuel_FuelLevel",
    "brake_bias":               "CarSetup_BrakesDriveUnit_BrakeSpec_BrakePressureBias",
    "tc_gain":                  "CarSetup_BrakesDriveUnit_TractionControl_TractionControlGain",
    "tc_slip":                  "CarSetup_BrakesDriveUnit_TractionControl_TractionControlSlip",
}


# ─────────────────────────────────────────────────────────────────────────────
# Porsche 963 GTP — partial mappings
# Notable differences:
#   - Front spring: RollSpring (not torsion bar OD)
#   - Heave spring: separate HeaveSpring vs BMW's combined model
# ─────────────────────────────────────────────────────────────────────────────

_PORSCHE_PARAM_IDS: dict[str, str] = {
    "wing_angle":               "CarSetup_TiresAero_AeroSettings_RearWingAngle",
    "lf_ride_height":           "CarSetup_Chassis_LeftFront_RideHeight",
    "rf_ride_height":           "CarSetup_Chassis_RightFront_RideHeight",
    "lr_ride_height":           "CarSetup_Chassis_LeftRear_RideHeight",
    "rr_ride_height":           "CarSetup_Chassis_RightRear_RideHeight",
    "front_pushrod_offset":     "CarSetup_Chassis_Front_PushrodLengthOffset",
    "rear_pushrod_offset":      "CarSetup_Chassis_Rear_PushrodLengthOffset",
    # Porsche uses RollSpring for corner spring (not TorsionBarOD)
    "lf_roll_spring":           "CarSetup_Chassis_LeftFront_RollSpring",
    "rf_roll_spring":           "CarSetup_Chassis_RightFront_RollSpring",
    # Heave springs (Porsche labels them HeaveSpring, same key as BMW)
    "front_heave_spring":       "CarSetup_Chassis_Front_HeaveSpring",
    "rear_heave_spring":        "CarSetup_Chassis_Rear_HeaveSpring",
    # Camber / toe
    "lf_camber":                "CarSetup_Chassis_LeftFront_Camber",
    "rf_camber":                "CarSetup_Chassis_RightFront_Camber",
    "lr_camber":                "CarSetup_Chassis_LeftRear_Camber",
    "rr_camber":                "CarSetup_Chassis_RightRear_Camber",
    "front_toe":                "CarSetup_Chassis_Front_ToeIn",
    "lr_toe":                   "CarSetup_Chassis_LeftRear_ToeIn",
    "rr_toe":                   "CarSetup_Chassis_RightRear_ToeIn",
    # Brakes / fuel (same as BMW)
    "fuel_level":               "CarSetup_BrakesDriveUnit_Fuel_FuelLevel",
    "brake_bias":               "CarSetup_BrakesDriveUnit_BrakeSpec_BrakePressureBias",
    "tc_gain":                  "CarSetup_BrakesDriveUnit_TractionControl_TractionControlGain",
    "tc_slip":                  "CarSetup_BrakesDriveUnit_TractionControl_TractionControlSlip",
}


# ─────────────────────────────────────────────────────────────────────────────
# Cadillac V-Series.R & Acura ARX-06 — similar to BMW, most IDs shared
# Notable: ARBs may use indexed format like Ferrari
# ─────────────────────────────────────────────────────────────────────────────

_CADILLAC_PARAM_IDS: dict[str, str] = {
    **_BMW_PARAM_IDS,  # Start from BMW as base — most IDs identical
    # Cadillac ARBs use indexed format (override BMW defaults)
    "front_arb_blades":         "CarSetup_Chassis_Front_ArbBlades[0]",
    "rear_arb_blades":          "CarSetup_Chassis_Rear_ArbBlades[0]",
}

_ACURA_PARAM_IDS: dict[str, str] = {
    **_BMW_PARAM_IDS,  # Similar to BMW
    "front_arb_blades":         "CarSetup_Chassis_Front_ArbBlades[0]",
    "rear_arb_blades":          "CarSetup_Chassis_Rear_ArbBlades[0]",
}

# Master dispatch table
_CAR_PARAM_IDS: dict[str, dict[str, str]] = {
    "bmw":      _BMW_PARAM_IDS,
    "ferrari":  _FERRARI_PARAM_IDS,
    "porsche":  _PORSCHE_PARAM_IDS,
    "cadillac": _CADILLAC_PARAM_IDS,
    "acura":    _ACURA_PARAM_IDS,
}


def _validate_setup_values(
    step1: RakeSolution,
    step2: HeaveSolution,
    step3: CornerSpringSolution,
    step4: ARBSolution,
    step5: WheelGeometrySolution,
    step6: DamperSolution,
) -> None:
    """Validate all numeric values are within iRacing accepted ranges.

    Prints a warning for any out-of-range value. Does not raise — file
    is still written so engineers can investigate.

    iRacing GTP accepted ranges:
        Ride heights: 10-80 mm
        Heave spring: 10-200 N/mm
        ARB blade: 1-5 (integer)
        Damper clicks: 1-20 (integer)
        Brake bias: 40-60%
        Tyre pressure: 130-180 kPa (cold)
    """
    warnings: list[str] = []

    def _chk(name: str, value: float, lo: float, hi: float, unit: str = "") -> None:
        if value < lo or value > hi:
            tag = "below" if value < lo else "above"
            clamp = lo if value < lo else hi
            warnings.append(
                f"setup_writer: {name}={value:.1f}{unit} {tag} iRacing "
                f"minimum ({lo:.0f} {unit}) — clamped to {clamp:.0f}"
            )

    # Ride heights (static)
    _chk("front_static_rh_mm", step1.static_front_rh_mm, 10.0, 80.0, "mm")
    _chk("rear_static_rh_mm", step1.static_rear_rh_mm, 10.0, 80.0, "mm")

    # Heave / third spring
    _chk("front_heave_nmm", step2.front_heave_nmm, 10.0, 200.0, "N/mm")
    _chk("rear_third_nmm", step2.rear_third_nmm, 10.0, 900.0, "N/mm")

    # ARB blades (1-5)
    _chk("front_arb_blade", step4.front_arb_blade_start, 1.0, 5.0, "")
    _chk("rear_arb_blade", step4.rear_arb_blade_start, 1.0, 5.0, "")

    # Damper clicks (1-20, representative corners)
    for corner_name, corner in [
        ("lf", step6.lf), ("rf", step6.rf), ("lr", step6.lr), ("rr", step6.rr)
    ]:
        _chk(f"{corner_name}_ls_comp", corner.ls_comp, 1.0, 20.0, "clicks")
        _chk(f"{corner_name}_ls_rbd", corner.ls_rbd, 1.0, 20.0, "clicks")
        _chk(f"{corner_name}_hs_comp", corner.hs_comp, 1.0, 20.0, "clicks")
        _chk(f"{corner_name}_hs_rbd", corner.hs_rbd, 1.0, 20.0, "clicks")

    for w in warnings:
        print(f"[warning] {w}")


def write_sto(
    car_name: str,
    track_name: str,
    wing: float,
    fuel_l: float,
    step1: RakeSolution,
    step2: HeaveSolution,
    step3: CornerSpringSolution,
    step4: ARBSolution,
    step5: WheelGeometrySolution,
    step6: DamperSolution,
    output_path: str | Path,
    car_canonical: str = "bmw",
    tyre_pressure_kpa: float = 152.0,
    # --- Defaults for fields not computed by the solver ---
    brake_bias_pct: float | None = None,  # None = compute from car physics
    brake_bias_migration: float = 0.0,
    brake_bias_target: float = 0.0,
    pad_compound: str = "Medium",
    front_master_cyl_mm: float = 19.1,
    rear_master_cyl_mm: float = 20.6,
    diff_coast_drive_ramp: str = "40/65",
    diff_clutch_plates: int = 6,
    diff_preload_nm: float = 10.0,
    tc_gain: int = 4,
    tc_slip: int = 3,
    gear_stack: str = "Short",
    fuel_low_warning_l: float = 8.0,
    roof_light_color: str = "Orange",
    include_computed: bool = False,
) -> Path:
    """Write an iRacing .sto setup file from solver output.

    Dispatches to the correct CarSetup_* ID mapping based on car_canonical.
    For partially-mapped cars (Ferrari, Porsche), known params are written
    and unknown params get a XML comment stub: <!-- TODO: {car} {param} not mapped -->

    Args:
        car_name: Car display name (for metadata)
        track_name: Track display name
        wing: Wing angle in degrees
        fuel_l: Fuel load in liters
        step1-step6: Solver step results
        output_path: Path to write .sto file
        car_canonical: Car canonical name for ID dispatch (default: "bmw")
        tyre_pressure_kpa: Starting tyre pressure (default 152 kPa minimum)
        brake_bias_pct: Brake pressure bias %
        diff_coast_drive_ramp: Diff coast/drive ramp angles (default "40/65")
        diff_clutch_plates: Diff clutch friction plates (default 6)
        diff_preload_nm: Diff preload in Nm (default 10)
        tc_gain: Traction control gain (default 4)
        tc_slip: Traction control slip (default 3)

    Returns:
        Path to the written file
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Pre-write validation: warn on out-of-range values ──────────────────
    _validate_setup_values(step1, step2, step3, step4, step5, step6)

    # ── Fetch car model for physics computations ───────────────────────────
    from car_model.cars import get_car as _get_car
    try:
        _car = _get_car(car_canonical)
    except Exception:
        _car = None

    # Compute brake bias from physics if not provided
    if brake_bias_pct is None:
        from solver.supporting_solver import compute_brake_bias
        try:
            if _car is not None:
                brake_bias_pct, _ = compute_brake_bias(_car, fuel_load_l=fuel_l)
            else:
                brake_bias_pct = 56.0
        except Exception:
            brake_bias_pct = 56.0  # fallback only if car model unavailable

    # ── Corner weights (physics-computed) ─────────────────────────────────
    if _car is not None:
        _total_mass   = _car.mass_car_kg + _car.mass_driver_kg + fuel_l * _car.fuel_density_kg_per_l
        _front_axle_n = _total_mass * 9.81 * _car.weight_dist_front
        _rear_axle_n  = _total_mass * 9.81 * (1.0 - _car.weight_dist_front)
        _lf_cw = round(_front_axle_n / 2.0, 0)
        _rf_cw = round(_front_axle_n / 2.0, 0)
        _lr_cw = round(_rear_axle_n  / 2.0, 0)
        _rr_cw = round(_rear_axle_n  / 2.0, 0)
    else:
        _lf_cw = _rf_cw = _lr_cw = _rr_cw = 0.0

    # Resolve ID mapping for this car
    ids = _CAR_PARAM_IDS.get(car_canonical.lower(), _BMW_PARAM_IDS)

    def _w_num(param: str, value: float | int, unit: str) -> None:
        """Write a numeric param using car-specific ID, or TODO comment if unmapped."""
        if param in ids:
            _numeric(details, ids[param], value, unit)
        else:
            _comment(details, f"TODO: {car_canonical} {param} not mapped")

    def _w_str(param: str, value: str, unit: str = "") -> None:
        """Write a string param using car-specific ID, or TODO comment if unmapped."""
        if param in ids:
            _string(details, ids[param], value, unit)
        else:
            _comment(details, f"TODO: {car_canonical} {param} not mapped")

    # Build XML tree
    root = Element("LDXFile", Version="1.6", Locale="English")
    maths = SubElement(root, "Maths", Id="Maths", Flags="184", Locale="English")
    SubElement(maths, "MathConstants")

    layers = SubElement(root, "Layers")
    details = SubElement(layers, "Details")

    # Session metadata
    now = datetime.now(timezone.utc)
    _string(details, "Event", "GTP Setup Builder")
    _string(details, "Session", f"Generated {now.strftime('%Y-%m-%d %H:%M')} UTC")
    _string(details, "Venue", track_name)
    _string(details, "Vehicle Type", "Car")

    # Setup update counter (iRacing increments this each save; we emit 1)
    if include_computed:
        _w_num("update_count", 1, "")

    # === Aero ===
    _w_num("wing_angle",       wing,                                 "deg")
    _w_num("front_rh_at_speed", round(step1.dynamic_front_rh_mm, 0), "mm")
    _w_num("rear_rh_at_speed",  round(step1.dynamic_rear_rh_mm, 0),  "mm")
    _w_num("df_balance",        round(step1.df_balance_pct, 1),       "%")
    _w_num("ld_ratio",          round(step1.ld_ratio, 1),             "")

    # === Ride Heights ===
    _w_num("lf_ride_height", round(step1.static_front_rh_mm, 0), "mm")
    _w_num("rf_ride_height", round(step1.static_front_rh_mm, 0), "mm")
    _w_num("lr_ride_height", round(step1.static_rear_rh_mm, 1),  "mm")
    _w_num("rr_ride_height", round(step1.static_rear_rh_mm, 1),  "mm")

    # === Pushrod offsets ===
    _w_num("front_pushrod_offset", round(step1.front_pushrod_offset_mm * 2) / 2, "mm")
    _w_num("rear_pushrod_offset",  round(step1.rear_pushrod_offset_mm * 2) / 2,  "mm")

    # === Heave / Third springs ===
    _w_num("front_heave_spring",   int(round(step2.front_heave_nmm)),      "N/mm")
    _w_num("front_heave_perch",    int(round(step2.perch_offset_front_mm)), "mm")
    _w_num("rear_third_spring",    int(round(step2.rear_third_nmm)),        "N/mm")
    _w_num("rear_third_perch",     int(round(step2.perch_offset_rear_mm)),  "mm")

    # === Corner springs ===
    # BMW: torsion bar OD + turns; other cars: fallback to TODO stubs
    _w_num("lf_torsion_od", step3.front_torsion_od_mm, "mm")
    _w_num("rf_torsion_od", step3.front_torsion_od_mm, "mm")
    # Torsion bar turns — computed by iRacing from heave/perch/OD.
    # Only write when include_computed=True (for engineering reports).
    if include_computed:
        # turns = 0.0989 + 0.432/heave + 0.000699*perch + 0.000002*OD
        # Calibrated from 31 unique setups across 41 BMW sessions, R²=0.800
        _tb_turns = round(
            0.0989
            + 0.432 / max(step2.front_heave_nmm, 1)
            + 0.000699 * step2.perch_offset_front_mm
            + 0.000002 * step3.front_torsion_od_mm,
            3,
        )
        _w_num("lf_torsion_turns", _tb_turns, "Turns")
        _w_num("rf_torsion_turns", _tb_turns, "Turns")
    # Porsche roll spring (only if car maps it)
    if "lf_roll_spring" in ids:
        _numeric(details, ids["lf_roll_spring"], int(round(step3.front_wheel_rate_nmm)), "N/mm")
        _numeric(details, ids["rf_roll_spring"], int(round(step3.front_wheel_rate_nmm)), "N/mm")
    # Rear spring
    _w_num("lr_spring_rate",   int(round(step3.rear_spring_rate_nmm)), "N/mm")
    _w_num("rr_spring_rate",   int(round(step3.rear_spring_rate_nmm)), "N/mm")
    _w_num("lr_spring_perch",  round(step3.rear_spring_perch_mm, 1),   "mm")
    _w_num("rr_spring_perch",  round(step3.rear_spring_perch_mm, 1),   "mm")
    # Shock deflection maxes (computed by iRacing)
    if include_computed:
        _w_num("lf_shock_defl_max", 100, "mm")
        _w_num("rf_shock_defl_max", 100, "mm")
        _w_num("lr_shock_defl_max", 150, "mm")
        _w_num("rr_shock_defl_max", 150, "mm")

    # === Computed / display deflections ===
    # These are display-only values that iRacing computes internally.
    # Including them in the .sto causes iRacing to reject the file.
    # Only write them when include_computed=True (for engineering reports).
    if include_computed:
        _fh = step2.front_heave_nmm
        _fh_perch = step2.perch_offset_front_mm
        _f_od = step3.front_torsion_od_mm

        if _car is not None:
            _dm = _car.deflection
            _k_torsion = _car.corner_spring.torsion_bar_rate(_f_od)

            _lf_sd = round(_dm.shock_defl_front(step1.front_pushrod_offset_mm), 1)
            _lr_sd = round(_dm.shock_defl_rear(step1.rear_pushrod_offset_mm), 1)
            _tb_defl = round(_dm.torsion_bar_defl(_fh, _fh_perch, _k_torsion), 1)
            _heave_defl_static = round(_dm.heave_spring_defl_static(_fh, _fh_perch, _f_od), 1)
            _heave_slider_static = round(_dm.heave_slider_defl_static(_fh, _fh_perch, _f_od), 1)
            _lr_spring_defl = round(_dm.rear_spring_defl_static(
                step3.rear_spring_rate_nmm, step3.rear_spring_perch_mm), 1)
            _r3_defl = round(_dm.third_spring_defl_static(
                step2.rear_third_nmm, step2.perch_offset_rear_mm), 1)
            _r3_slider = round(_dm.third_slider_defl_static(_r3_defl), 1)
        else:
            _lf_sd = round(step1.static_front_rh_mm * 0.487, 1)
            _lr_sd = round(step1.static_rear_rh_mm * 0.462, 1)
            _tb_defl = round(_tb_turns * 181.5, 1)
            _heave_defl_static = round(40.5 + (-0.55) * _fh, 1)
            _heave_slider_static = round(46.2 + 0.012 * _fh + 0.251 * _fh_perch, 1)
            _lr_spring_defl = round(8.5 * 180.0 / max(step3.rear_spring_rate_nmm, 1.0), 1)
            _r3_defl = round(19.2 * step1.static_rear_rh_mm / 48.9, 1)
            _r3_slider = _r3_defl

        _w_num("lf_shock_defl_static", _lf_sd, "mm")
        _w_num("rf_shock_defl_static", _lf_sd, "mm")
        _w_num("lr_shock_defl_static", _lr_sd, "mm")
        _w_num("rr_shock_defl_static", _lr_sd, "mm")

        _w_num("lf_torsion_defl", _tb_defl, "mm")
        _w_num("rf_torsion_defl", _tb_defl, "mm")

        # HeaveSpringDeflMax retains its linear model (well-calibrated from 19 sessions)
        if step2.defl_max_front_mm > 0:
            _heave_defl_max = step2.defl_max_front_mm
        elif _car is not None:
            _heave_defl_max = (_car.heave_spring.heave_spring_defl_max_intercept_mm
                               + _car.heave_spring.heave_spring_defl_max_slope * _fh)
        else:
            _heave_defl_max = 106.43 + (-0.310) * _fh
        _w_num("front_heave_spring_defl_static", _heave_defl_static, "mm")
        _w_num("front_heave_spring_defl_max", round(_heave_defl_max, 1), "mm")
        _w_num("front_heave_slider_defl_static", _heave_slider_static, "mm")

        _w_num("lr_spring_defl_static", _lr_spring_defl, "mm")
        _w_num("rr_spring_defl_static", _lr_spring_defl, "mm")
        if _car is not None:
            _lr_defl_max = round(_dm.rear_spring_defl_max(
                step3.rear_spring_rate_nmm, step3.rear_spring_perch_mm), 1)
            _r3_defl_max = round(_dm.third_spring_defl_max(
                step2.rear_third_nmm, step2.perch_offset_rear_mm), 1)
        else:
            _lr_defl_max = 76.8
            _r3_defl_max = 61.2
        _w_num("lr_spring_defl_max", _lr_defl_max, "mm")
        _w_num("rr_spring_defl_max", _lr_defl_max, "mm")

        _w_num("rear_third_spring_defl_static",  _r3_defl,  "mm")
        _w_num("rear_third_spring_defl_max",     _r3_defl_max,      "mm")
        _w_num("rear_third_slider_defl_static",  _r3_slider, "mm")

        # Corner weights (N)
        _w_num("lf_corner_weight", _lf_cw, "N")
        _w_num("rf_corner_weight", _rf_cw, "N")
        _w_num("lr_corner_weight", _lr_cw, "N")
        _w_num("rr_corner_weight", _rr_cw, "N")

    # === ARBs ===
    _w_str("front_arb_size",   step4.front_arb_size)
    _w_num("front_arb_blades", step4.front_arb_blade_start, "")
    _w_str("rear_arb_size",    step4.rear_arb_size)
    _w_num("rear_arb_blades",  step4.rear_arb_blade_start, "")

    # === Cross weight (computed by iRacing) ===
    if include_computed:
        _w_num("cross_weight", 50, "%")

    # === Wheel geometry ===
    _w_num("lf_camber",  step5.front_camber_deg, "deg")
    _w_num("rf_camber",  step5.front_camber_deg, "deg")
    _w_num("lr_camber",  step5.rear_camber_deg,  "deg")
    _w_num("rr_camber",  step5.rear_camber_deg,  "deg")
    _w_num("front_toe",  step5.front_toe_mm,     "mm")
    _w_num("lr_toe",     step5.rear_toe_mm,      "mm")
    _w_num("rr_toe",     step5.rear_toe_mm,      "mm")

    # === Dampers ===
    _w_num("lf_ls_comp",  step6.lf.ls_comp,  "clicks")
    _w_num("lf_ls_rbd",   step6.lf.ls_rbd,   "clicks")
    _w_num("lf_hs_comp",  step6.lf.hs_comp,  "clicks")
    _w_num("lf_hs_rbd",   step6.lf.hs_rbd,   "clicks")
    _w_num("lf_hs_slope", step6.lf.hs_slope, "clicks")
    _w_num("rf_ls_comp",  step6.rf.ls_comp,  "clicks")
    _w_num("rf_ls_rbd",   step6.rf.ls_rbd,   "clicks")
    _w_num("rf_hs_comp",  step6.rf.hs_comp,  "clicks")
    _w_num("rf_hs_rbd",   step6.rf.hs_rbd,   "clicks")
    _w_num("rf_hs_slope", step6.rf.hs_slope, "clicks")
    _w_num("lr_ls_comp",  step6.lr.ls_comp,  "clicks")
    _w_num("lr_ls_rbd",   step6.lr.ls_rbd,   "clicks")
    _w_num("lr_hs_comp",  step6.lr.hs_comp,  "clicks")
    _w_num("lr_hs_rbd",   step6.lr.hs_rbd,   "clicks")
    _w_num("lr_hs_slope", step6.lr.hs_slope, "clicks")
    _w_num("rr_ls_comp",  step6.rr.ls_comp,  "clicks")
    _w_num("rr_ls_rbd",   step6.rr.ls_rbd,   "clicks")
    _w_num("rr_hs_comp",  step6.rr.hs_comp,  "clicks")
    _w_num("rr_hs_rbd",   step6.rr.hs_rbd,   "clicks")
    _w_num("rr_hs_slope", step6.rr.hs_slope, "clicks")

    # === Tyres ===
    _w_num("lf_pressure", tyre_pressure_kpa, "kPa")
    _w_num("rf_pressure", tyre_pressure_kpa, "kPa")
    _w_num("lr_pressure", tyre_pressure_kpa, "kPa")
    _w_num("rr_pressure", tyre_pressure_kpa, "kPa")
    _w_str("tyre_type", "Dry")

    # === Fuel ===
    _w_num("fuel_level",       fuel_l,            "L")

    # === Brakes, Diff, TC, Gears, Lighting ===
    # iRacing rejects .sto files containing these parameters.
    # The proven a2.sto ends at FuelLevel — anything after causes load failure.
    # Only write these for engineering report mode (include_computed=True).
    if include_computed:
        _w_num("brake_bias",           brake_bias_pct,       "%")
        _w_num("brake_bias_migration", brake_bias_migration,  "")
        _w_num("brake_bias_target",    brake_bias_target,     "")
        _w_str("pad_compound",         pad_compound)
        _w_num("front_master_cyl",     front_master_cyl_mm,  "mm")
        _w_num("rear_master_cyl",      rear_master_cyl_mm,   "mm")
        _w_str("diff_coast_drive_ramp", diff_coast_drive_ramp)
        _w_num("diff_clutch_plates",    diff_clutch_plates,   "")
        _w_num("diff_preload",          diff_preload_nm,       "Nm")
        _w_num("tc_gain", tc_gain, "(TCLAT)")
        _w_num("tc_slip", tc_slip, "(TCLON)")
        _w_num("fuel_low_warning", fuel_low_warning_l, "L")
        _w_str("gear_stack", gear_stack)
        _w_num("speed_in_first",   116, "Km/h")
        _w_num("speed_in_second",  151, "Km/h")
        _w_num("speed_in_third",   184, "Km/h")
        _w_num("speed_in_fourth",  220, "Km/h")
        _w_num("speed_in_fifth",   257, "Km/h")
        _w_num("speed_in_sixth",   288, "Km/h")
        _w_num("speed_in_seventh", 316, "Km/h")
        _w_str("roof_light_color", roof_light_color)

    # Write XML
    tree = ElementTree(root)
    indent(tree, space="  ")

    # Write with XML declaration
    with open(output_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n')
        tree.write(f, encoding="unicode", xml_declaration=False)

    return output_path
