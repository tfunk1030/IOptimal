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

import copy
from datetime import datetime, timezone
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree, indent

from car_model.garage import GarageSetupState
from car_model.setup_registry import public_output_value, get_car_spec


def _registry_sto_id(car: str, key: str, fallback: str = "") -> str:
    """Resolve STO param ID from setup_registry, with fallback (audit M5)."""
    spec = get_car_spec(car, key)
    return spec.sto_param_id if spec else fallback
from solver.rake_solver import RakeSolution
from solver.heave_solver import HeaveSolution
from solver.corner_spring_solver import CornerSpringSolution
from solver.arb_solver import ARBSolution
from solver.wheel_geometry_solver import WheelGeometrySolution
from solver.damper_solver import DamperSolution


def _numeric(parent: Element, param_id: str, value: float | int, unit: str) -> None:
    """Add a Numeric element to the XML."""
    SubElement(parent, "Numeric", Id=param_id, Value=str(value), Unit=unit)


def _decimal_1(value: float) -> float:
    """Format to one decimal place without binary-rounding surprises."""
    return float(f"{value:.1f}")


def _snap_to_step(value: float, step: float) -> float:
    """Snap a numeric value to an arbitrary garage step."""
    if step <= 0:
        return value
    snapped = round(value / step) * step
    return float(f"{snapped:.4f}")


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
    # Ferrari uses "PushrodLengthDelta" (not "PushrodLengthOffset" like BMW)
    "front_pushrod_offset":     "CarSetup_Chassis_Front_PushrodLengthDelta",
    "rear_pushrod_offset":      "CarSetup_Chassis_Rear_PushrodLengthDelta",
    # Heave springs — Ferrari rear has HeaveSpring (not ThirdSpring)
    "front_heave_spring":       "CarSetup_Chassis_Front_HeaveSpring",
    "front_heave_perch":        "CarSetup_Chassis_Front_HeavePerchOffset",
    "rear_third_spring":        "CarSetup_Chassis_Rear_HeaveSpring",
    "rear_third_perch":         "CarSetup_Chassis_Rear_HeavePerchOffset",
    # Torsion bars — both front and rear (Ferrari has no rear coil spring)
    "lf_torsion_od":            "CarSetup_Chassis_LeftFront_TorsionBarOD",
    "rf_torsion_od":            "CarSetup_Chassis_RightFront_TorsionBarOD",
    "lf_torsion_turns":         "CarSetup_Chassis_LeftFront_TorsionBarTurns",
    "rf_torsion_turns":         "CarSetup_Chassis_RightFront_TorsionBarTurns",
    "lr_spring_rate":           "CarSetup_Chassis_LeftRear_TorsionBarOD",
    "rr_spring_rate":           "CarSetup_Chassis_RightRear_TorsionBarOD",
    "lr_torsion_turns":         "CarSetup_Chassis_LeftRear_TorsionBarTurns",
    "rr_torsion_turns":         "CarSetup_Chassis_RightRear_TorsionBarTurns",
    # NO lr_spring_perch / rr_spring_perch — Ferrari rear is torsion bar
    # ARBs — Ferrari uses indexed blade arrays + size dropdown
    "front_arb_size":           "CarSetup_Chassis_Front_ArbSize",
    "front_arb_blades":         "CarSetup_Chassis_Front_ArbBlades[0]",
    "rear_arb_size":            "CarSetup_Chassis_Rear_ArbSize",
    "rear_arb_blades":          "CarSetup_Chassis_Rear_ArbBlades[0]",
    # Camber / toe
    "lf_camber":                "CarSetup_Chassis_LeftFront_Camber",
    "rf_camber":                "CarSetup_Chassis_RightFront_Camber",
    "lr_camber":                "CarSetup_Chassis_LeftRear_Camber",
    "rr_camber":                "CarSetup_Chassis_RightRear_Camber",
    "front_toe":                "CarSetup_Chassis_Front_ToeIn",
    "lr_toe":                   "CarSetup_Chassis_LeftRear_ToeIn",
    "rr_toe":                   "CarSetup_Chassis_RightRear_ToeIn",
    # Dampers
    "lf_ls_comp":               "CarSetup_Dampers_LeftFrontDamper_LsCompDamping",
    "lf_ls_rbd":                "CarSetup_Dampers_LeftFrontDamper_LsRbdDamping",
    "lf_hs_comp":               "CarSetup_Dampers_LeftFrontDamper_HsCompDamping",
    "lf_hs_rbd":                "CarSetup_Dampers_LeftFrontDamper_HsRbdDamping",
    "lf_hs_slope":              "CarSetup_Dampers_LeftFrontDamper_HsCompDampSlope",
    "rf_ls_comp":               "CarSetup_Dampers_RightFrontDamper_LsCompDamping",
    "rf_ls_rbd":                "CarSetup_Dampers_RightFrontDamper_LsRbdDamping",
    "rf_hs_comp":               "CarSetup_Dampers_RightFrontDamper_HsCompDamping",
    "rf_hs_rbd":                "CarSetup_Dampers_RightFrontDamper_HsRbdDamping",
    "rf_hs_slope":              "CarSetup_Dampers_RightFrontDamper_HsCompDampSlope",
    "lr_ls_comp":               "CarSetup_Dampers_LeftRearDamper_LsCompDamping",
    "lr_ls_rbd":                "CarSetup_Dampers_LeftRearDamper_LsRbdDamping",
    "lr_hs_comp":               "CarSetup_Dampers_LeftRearDamper_HsCompDamping",
    "lr_hs_rbd":                "CarSetup_Dampers_LeftRearDamper_HsRbdDamping",
    "lr_hs_slope":              "CarSetup_Dampers_LeftRearDamper_HsCompDampSlope",
    "rr_ls_comp":               "CarSetup_Dampers_RightRearDamper_LsCompDamping",
    "rr_ls_rbd":                "CarSetup_Dampers_RightRearDamper_LsRbdDamping",
    "rr_hs_comp":               "CarSetup_Dampers_RightRearDamper_HsCompDamping",
    "rr_hs_rbd":                "CarSetup_Dampers_RightRearDamper_HsRbdDamping",
    "rr_hs_slope":              "CarSetup_Dampers_RightRearDamper_HsCompDampSlope",
    # Tyres
    "lf_pressure":              "CarSetup_TiresAero_LeftFront_StartingPressure",
    "rf_pressure":              "CarSetup_TiresAero_RightFront_StartingPressure",
    "lr_pressure":              "CarSetup_TiresAero_LeftRearTire_StartingPressure",
    "rr_pressure":              "CarSetup_TiresAero_RightRearTire_StartingPressure",
    "tyre_type":                "CarSetup_TiresAero_TireType_TireType",
    # Fuel / brakes / diff / TC / hybrid
    "fuel_level":               "CarSetup_Systems_Fuel_FuelLevel",
    "fuel_low_warning":         "CarSetup_Systems_Fuel_FuelLowWarning",
    "fuel_target":              "CarSetup_Systems_Fuel_FuelTarget",
    "brake_bias":               "CarSetup_Systems_BrakeSpec_BrakePressureBias",
    "brake_bias_target":        "CarSetup_Systems_BrakeSpec_BrakeBiasTarget",
    "brake_bias_migration":     "CarSetup_Systems_BrakeSpec_BiasMigration",
    "brake_bias_migration_gain":"CarSetup_Systems_BrakeSpec_BiasMigrationGain",
    "pad_compound":             "CarSetup_Systems_BrakeSpec_PadCompound",
    "front_master_cyl":         "CarSetup_Systems_BrakeSpec_FrontMasterCyl",
    "rear_master_cyl":          "CarSetup_Systems_BrakeSpec_RearMasterCyl",
    "tc_gain":                  "CarSetup_Systems_TractionControl_TractionControlGain",
    "tc_slip":                  "CarSetup_Systems_TractionControl_TractionControlSlip",
    "front_diff_preload":       "CarSetup_Systems_FrontDiffSpec_Preload",
    "diff_preload":             "CarSetup_Systems_RearDiffSpec_Preload",
    "diff_coast_drive_ramp":    "CarSetup_Systems_RearDiffSpec_CoastDriveRampOptions",
    "diff_clutch_plates":       "CarSetup_Systems_RearDiffSpec_ClutchFrictionPlates",
    "gear_stack":               "CarSetup_Systems_GearRatios_GearStack",
    "speed_in_first":           "CarSetup_Systems_GearRatios_SpeedInFirst",
    "speed_in_second":          "CarSetup_Systems_GearRatios_SpeedInSecond",
    "speed_in_third":           "CarSetup_Systems_GearRatios_SpeedInThird",
    "speed_in_fourth":          "CarSetup_Systems_GearRatios_SpeedInFourth",
    "speed_in_fifth":           "CarSetup_Systems_GearRatios_SpeedInFifth",
    "speed_in_sixth":           "CarSetup_Systems_GearRatios_SpeedInSixth",
    "speed_in_seventh":         "CarSetup_Systems_GearRatios_SpeedInSeventh",
    "roof_light_color":         "CarSetup_Systems_Lighting_RoofIdLightColor",
    "hybrid_rear_drive_enabled":"CarSetup_Systems_HybridConfig_HybridRearDriveEnabled",
    "hybrid_rear_drive_corner_pct":"CarSetup_Systems_HybridConfig_HybridRearDriveCornerPct",
}


# ─────────────────────────────────────────────────────────────────────────────
# Porsche 963 GTP — partial mappings
# Notable differences:
#   - Front spring: RollSpring (not torsion bar OD)
#   - Heave spring: separate HeaveSpring vs BMW's combined model
# ─────────────────────────────────────────────────────────────────────────────

_PORSCHE_PARAM_IDS: dict[str, str] = {
    "wing_angle":               "CarSetup_TiresAero_AeroSettings_RearWingAngle",
    # Aero calculator display values (same XML IDs as BMW)
    "front_rh_at_speed":        "CarSetup_TiresAero_AeroCalculator_FrontRhAtSpeed",
    "rear_rh_at_speed":         "CarSetup_TiresAero_AeroCalculator_RearRhAtSpeed",
    "df_balance":               "CarSetup_TiresAero_AeroCalculator_DownforceBalance",
    "ld_ratio":                 "CarSetup_TiresAero_AeroCalculator_LD",
    "lf_ride_height":           "CarSetup_Chassis_LeftFront_RideHeight",
    "rf_ride_height":           "CarSetup_Chassis_RightFront_RideHeight",
    "lr_ride_height":           "CarSetup_Chassis_LeftRear_RideHeight",
    "rr_ride_height":           "CarSetup_Chassis_RightRear_RideHeight",
    "front_pushrod_offset":     "CarSetup_Chassis_Front_PushrodLengthOffset",
    "rear_pushrod_offset":      "CarSetup_Chassis_Rear_PushrodLengthOffset",
    # Porsche uses RollSpring for corner spring (not TorsionBarOD)
    "lf_roll_spring":           "CarSetup_Chassis_LeftFront_RollSpring",
    "rf_roll_spring":           "CarSetup_Chassis_RightFront_RollSpring",
    # Heave / third springs and perch offsets
    "front_heave_spring":       "CarSetup_Chassis_Front_HeaveSpring",
    "front_heave_perch":        "CarSetup_Chassis_Front_HeavePerchOffset",
    "rear_third_spring":        "CarSetup_Chassis_Rear_HeaveSpring",
    "rear_third_perch":         "CarSetup_Chassis_Rear_HeavePerchOffset",
    # Rear corner springs (L/R individual)
    "lr_spring_rate":           "CarSetup_Chassis_LeftRear_SpringRate",
    "rr_spring_rate":           "CarSetup_Chassis_RightRear_SpringRate",
    "lr_spring_perch":          "CarSetup_Chassis_LeftRear_SpringPerchOffset",
    "rr_spring_perch":          "CarSetup_Chassis_RightRear_SpringPerchOffset",
    # Roll spring perch
    "front_roll_perch":         "CarSetup_Chassis_Front_RollPerchOffset",
    # ARB
    "front_arb_size":           "CarSetup_Chassis_Front_ArbSize",
    "front_arb_blades":         "CarSetup_Chassis_Front_ArbAdj",
    "rear_arb_size":            "CarSetup_Chassis_Rear_ArbSize",
    "rear_arb_blades":          "CarSetup_Chassis_Rear_ArbAdj",
    # Camber / toe
    "lf_camber":                "CarSetup_Chassis_LeftFront_Camber",
    "rf_camber":                "CarSetup_Chassis_RightFront_Camber",
    "lr_camber":                "CarSetup_Chassis_LeftRear_Camber",
    "rr_camber":                "CarSetup_Chassis_RightRear_Camber",
    "front_toe":                "CarSetup_Chassis_Front_ToeIn",
    "lr_toe":                   "CarSetup_Chassis_LeftRear_ToeIn",
    "rr_toe":                   "CarSetup_Chassis_RightRear_ToeIn",
    # Suppress fields Porsche doesn't have
    "lf_torsion_od":            "",  # no front torsion bar
    "rf_torsion_od":            "",
    "lf_torsion_turns":         "",
    "rf_torsion_turns":         "",
    "lf_hs_slope":              "",  # front heave has no HS slope
    # Front heave dampers (4 channels, NO HS slope on Porsche front heave)
    "lf_ls_comp":               "CarSetup_Dampers_FrontHeave_LsCompDamping",
    "lf_ls_rbd":                "CarSetup_Dampers_FrontHeave_LsRbdDamping",
    "lf_hs_comp":               "CarSetup_Dampers_FrontHeave_HsCompDamping",
    "lf_hs_rbd":                "CarSetup_Dampers_FrontHeave_HsRbdDamping",
    # lf_hs_slope NOT mapped — Porsche front heave has no HS slope in garage
    "rf_ls_comp":               "",  # suppress — Porsche front heave is single unit (same as lf)
    "rf_ls_rbd":                "",
    "rf_hs_comp":               "",
    "rf_hs_rbd":                "",
    "rf_hs_slope":              "",
    # Front roll dampers (3 channels: LS, HS, HS slope)
    "front_roll_ls":            "CarSetup_Dampers_FrontRoll_LsDamping",
    "front_roll_hs":            "CarSetup_Dampers_FrontRoll_HsDamping",
    "front_roll_hs_slope":      "CarSetup_Dampers_FrontRoll_HsDampSlope",
    # Rear L/R corner dampers (5 channels: LS comp, HS comp, HS slope, LS rbd, HS rbd)
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
    # Rear roll dampers (2 channels: LS, HS — no slope, no rbd split)
    "rear_roll_ls":             "CarSetup_Dampers_RearRoll_LsDamping",
    "rear_roll_hs":             "CarSetup_Dampers_RearRoll_HsDamping",
    # Rear 3rd dampers (4 channels, NO HS slope, range 0-5)
    "rear_3rd_ls_comp":         "CarSetup_Dampers_Rear3rd_LsCompDamping",
    "rear_3rd_hs_comp":         "CarSetup_Dampers_Rear3rd_HsCompDamping",
    "rear_3rd_ls_rbd":          "CarSetup_Dampers_Rear3rd_LsRbdDamping",
    "rear_3rd_hs_rbd":          "CarSetup_Dampers_Rear3rd_HsRbdDamping",
    # Tyre pressures + type (same XML IDs as BMW)
    "lf_pressure":              "CarSetup_TiresAero_LeftFront_StartingPressure",
    "rf_pressure":              "CarSetup_TiresAero_RightFront_StartingPressure",
    "lr_pressure":              "CarSetup_TiresAero_LeftRearTire_StartingPressure",
    "rr_pressure":              "CarSetup_TiresAero_RightRearTire_StartingPressure",
    "tyre_type":                "CarSetup_TiresAero_TireType_TireType",
    # Brakes / fuel (same as BMW)
    "fuel_level":               "CarSetup_BrakesDriveUnit_Fuel_FuelLevel",
    "fuel_low_warning":         "CarSetup_BrakesDriveUnit_Fuel_FuelLowWarning",
    "brake_bias":               "CarSetup_BrakesDriveUnit_BrakeSpec_BrakePressureBias",
    "brake_bias_migration":     "CarSetup_BrakesDriveUnit_BrakeSpec_BrakeBiasMigration",
    "brake_bias_target":        "CarSetup_BrakesDriveUnit_BrakeSpec_BrakeBiasTarget",
    "pad_compound":             "CarSetup_BrakesDriveUnit_BrakeSpec_PadCompound",
    "front_master_cyl":         "CarSetup_BrakesDriveUnit_BrakeSpec_FrontMasterCyl",
    "rear_master_cyl":          "CarSetup_BrakesDriveUnit_BrakeSpec_RearMasterCyl",
    "tc_gain":                  "CarSetup_BrakesDriveUnit_TractionControl_TractionControlGain",
    "tc_slip":                  "CarSetup_BrakesDriveUnit_TractionControl_TractionControlSlip",
    # Diff — sourced from setup_registry canonical specs (audit M5)
    "diff_preload":             _registry_sto_id("porsche", "diff_preload_nm", "CarSetup_BrakesDriveUnit_DiffSpec_DiffPreload"),
    "diff_coast_ramp":          _registry_sto_id("porsche", "diff_ramp_angles", "CarSetup_BrakesDriveUnit_DiffSpec_CoastRampAngle"),
    "diff_drive_ramp":          "CarSetup_BrakesDriveUnit_DiffSpec_DriveRampAngle",  # Porsche uses separate coast/drive STO fields
    "diff_clutch_plates":       _registry_sto_id("porsche", "diff_clutch_plates", "CarSetup_BrakesDriveUnit_DiffSpec_ClutchPlates"),
    # Gears / lighting (same as BMW)
    "gear_stack":               "CarSetup_BrakesDriveUnit_GearRatios_GearStack",
    "roof_light_color":         "CarSetup_BrakesDriveUnit_Lighting_RoofIdLightColor",
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
    **_BMW_PARAM_IDS,  # Base — then override Acura-specific mappings below

    # ARB blades use indexed format (like Ferrari)
    "front_arb_blades":         "CarSetup_Chassis_Front_ArbBlades[0]",
    "rear_arb_blades":          "CarSetup_Chassis_Rear_ArbBlades[0]",

    # Rear uses torsion bars, NOT coil springs (ORECA chassis)
    "lr_spring_rate":           "CarSetup_Chassis_LeftRear_TorsionBarOD",
    "rr_spring_rate":           "CarSetup_Chassis_RightRear_TorsionBarOD",
    "lr_torsion_turns":         "CarSetup_Chassis_LeftRear_TorsionBarTurns",
    "rr_torsion_turns":         "CarSetup_Chassis_RightRear_TorsionBarTurns",
    # No rear coil spring perch — torsion bars don't use spring perch
    "lr_spring_perch":          "",  # suppress
    "rr_spring_perch":          "",  # suppress

    # Rear toe is single value under Chassis.Rear, not per-corner
    "rear_toe":                 "CarSetup_Chassis_Rear_ToeIn",
    "lr_toe":                   "",  # suppress per-corner
    "rr_toe":                   "",  # suppress per-corner

    # Dampers: ORECA heave+roll layout (NOT per-corner)
    # Front heave damper
    "lf_ls_comp":               "CarSetup_Dampers_FrontHeave_LsCompDamping",
    "lf_ls_rbd":                "CarSetup_Dampers_FrontHeave_LsRbdDamping",
    "lf_hs_comp":               "CarSetup_Dampers_FrontHeave_HsCompDamping",
    "lf_hs_rbd":                "CarSetup_Dampers_FrontHeave_HsRbdDamping",
    "lf_hs_slope":              "CarSetup_Dampers_FrontHeave_HsCompDampSlope",
    # RF = same as LF (heave is single unit, not per-corner)
    "rf_ls_comp":               "",  # suppress — heave is single
    "rf_ls_rbd":                "",
    "rf_hs_comp":               "",
    "rf_hs_rbd":                "",
    "rf_hs_slope":              "",
    # Rear heave damper
    "lr_ls_comp":               "CarSetup_Dampers_RearHeave_LsCompDamping",
    "lr_ls_rbd":                "CarSetup_Dampers_RearHeave_LsRbdDamping",
    "lr_hs_comp":               "CarSetup_Dampers_RearHeave_HsCompDamping",
    "lr_hs_rbd":                "CarSetup_Dampers_RearHeave_HsRbdDamping",
    "lr_hs_slope":              "CarSetup_Dampers_RearHeave_HsCompDampSlope",
    # RR = same as LR (heave is single unit)
    "rr_ls_comp":               "",
    "rr_ls_rbd":                "",
    "rr_hs_comp":               "",
    "rr_hs_rbd":                "",
    "rr_hs_slope":              "",

    # Front roll spring + perch (ORECA chassis — separate roll spring)
    "front_roll_spring_nmm":    "CarSetup_Chassis_Front_RollSpring",
    "front_roll_perch_mm":      "CarSetup_Chassis_Front_RollPerchOffset",

    # Roll dampers (Acura-specific, no BMW equivalent)
    "front_roll_ls":            "CarSetup_Dampers_FrontRoll_LsDamping",
    "front_roll_hs":            "CarSetup_Dampers_FrontRoll_HsDamping",
    "rear_roll_ls":             "CarSetup_Dampers_RearRoll_LsDamping",
    "rear_roll_hs":             "CarSetup_Dampers_RearRoll_HsDamping",

    # Diff: Acura uses "DiffRampAngles" (not "CoastDriveRampAngles" like BMW)
    "diff_coast_drive_ramp":    "CarSetup_Systems_RearDiffSpec_DiffRampAngles",
    "diff_clutch_plates":       "CarSetup_Systems_RearDiffSpec_ClutchFrictionPlates",
    "diff_preload":             "CarSetup_Systems_RearDiffSpec_Preload",

    # Brakes/TC/Fuel under Systems (not BrakesDriveUnit like BMW)
    "brake_bias":               "CarSetup_Systems_BrakeSpec_BrakePressureBias",
    "brake_bias_migration":     "CarSetup_Systems_BrakeSpec_BrakeBiasMigration",
    "pad_compound":             "CarSetup_Systems_BrakeSpec_PadCompound",
    "front_master_cyl":         "CarSetup_Systems_BrakeSpec_FrontMasterCyl",
    "rear_master_cyl":          "CarSetup_Systems_BrakeSpec_RearMasterCyl",
    "tc_gain":                  "CarSetup_Systems_TractionControl_TractionControlGain",
    "tc_slip":                  "CarSetup_Systems_TractionControl_TractionControlSlip",
    "fuel_level":               "CarSetup_Systems_Fuel_FuelLevel",
    "fuel_low_warning":         "CarSetup_Systems_Fuel_FuelLowWarning",
    "gear_stack":               "CarSetup_Systems_GearRatios_GearStack",
    "roof_light_color":         "CarSetup_Systems_Lighting_RoofIdLightColor",
}

# ─────────────────────────────────────────────────────────────────────────────
# BMW M4 GT3 EVO — GT3 architecture (W4.1)
# Notable differences vs GTP BMW:
#   - 4 paired coil-overs (no heave/third springs, no torsion bars)
#   - Per-axle dampers (not per-corner) — 8 channels total
#   - BumpRubberGap per corner (replaces pushrod offset RH workflow)
#   - CenterFrontSplitterHeight (new aero param)
#   - In-car adjusters collapsed into Chassis.InCarAdjustments
#   - Gears + diff under Chassis.GearsDifferential
#   - Rear toe is per-wheel (LeftRear.ToeIn / RightRear.ToeIn) — same as BMW GTP
#   - TC/ABS labels use indexed strings ("4 (TC)", "6 (ABS)")
#   - Fuel under Chassis.Rear.FuelLevel (NOT BrakesDriveUnit_Fuel_FuelLevel)
# Source: docs/audits/gt3_phase2/output.md:294-365 (verbatim).
# ─────────────────────────────────────────────────────────────────────────────

_BMW_M4_GT3_PARAM_IDS: dict[str, str] = {
    # ── Aero (TiresAero) ─────────────────────────────────────────────
    "wing_angle":               "CarSetup_Chassis_Rear_WingAngle",
    "front_rh_at_speed":        "CarSetup_TiresAero_AeroBalanceCalc_FrontRhAtSpeed",
    "rear_rh_at_speed":         "CarSetup_TiresAero_AeroBalanceCalc_RearRhAtSpeed",
    "df_balance":               "CarSetup_TiresAero_AeroBalanceCalc_FrontDownforce",
    # ── Tyres ─────────────────────────────────────────────────────────
    "lf_pressure":              "CarSetup_TiresAero_LeftFront_StartingPressure",
    "rf_pressure":              "CarSetup_TiresAero_RightFront_StartingPressure",
    "lr_pressure":              "CarSetup_TiresAero_LeftRear_StartingPressure",   # NB: no `Tire` suffix
    "rr_pressure":              "CarSetup_TiresAero_RightRear_StartingPressure",
    "tyre_type":                "CarSetup_TiresAero_TireType_TireType",
    # ── Front brakes section (BMW: `FrontBrakes` — Aston/Porsche use `FrontBrakesLights`) ─
    "front_arb_blades":         "CarSetup_Chassis_FrontBrakes_ArbBlades",
    "front_toe":                "CarSetup_Chassis_FrontBrakes_TotalToeIn",
    "front_master_cyl":         "CarSetup_Chassis_FrontBrakes_FrontMasterCyl",
    "rear_master_cyl":          "CarSetup_Chassis_FrontBrakes_RearMasterCyl",
    "pad_compound":             "CarSetup_Chassis_FrontBrakes_BrakePads",
    "front_splitter_height":    "CarSetup_Chassis_FrontBrakes_CenterFrontSplitterHeight",
    # ── Per-corner: LF/RF/LR/RR ──────────────────────────────────────
    "lf_corner_weight":         "CarSetup_Chassis_LeftFront_CornerWeight",
    "lf_ride_height":           "CarSetup_Chassis_LeftFront_RideHeight",
    "lf_bump_rubber_gap":       "CarSetup_Chassis_LeftFront_BumpRubberGap",
    "lf_spring_rate":           "CarSetup_Chassis_LeftFront_SpringRate",
    "lf_camber":                "CarSetup_Chassis_LeftFront_Camber",
    "rf_corner_weight":         "CarSetup_Chassis_RightFront_CornerWeight",
    "rf_ride_height":           "CarSetup_Chassis_RightFront_RideHeight",
    "rf_bump_rubber_gap":       "CarSetup_Chassis_RightFront_BumpRubberGap",
    "rf_spring_rate":           "CarSetup_Chassis_RightFront_SpringRate",
    "rf_camber":                "CarSetup_Chassis_RightFront_Camber",
    "lr_corner_weight":         "CarSetup_Chassis_LeftRear_CornerWeight",
    "lr_ride_height":           "CarSetup_Chassis_LeftRear_RideHeight",
    "lr_bump_rubber_gap":       "CarSetup_Chassis_LeftRear_BumpRubberGap",
    "lr_spring_rate":           "CarSetup_Chassis_LeftRear_SpringRate",
    "lr_camber":                "CarSetup_Chassis_LeftRear_Camber",
    "lr_toe":                   "CarSetup_Chassis_LeftRear_ToeIn",
    "rr_corner_weight":         "CarSetup_Chassis_RightRear_CornerWeight",
    "rr_ride_height":           "CarSetup_Chassis_RightRear_RideHeight",
    "rr_bump_rubber_gap":       "CarSetup_Chassis_RightRear_BumpRubberGap",
    "rr_spring_rate":           "CarSetup_Chassis_RightRear_SpringRate",
    "rr_camber":                "CarSetup_Chassis_RightRear_Camber",
    "rr_toe":                   "CarSetup_Chassis_RightRear_ToeIn",
    # ── Rear section ─────────────────────────────────────────────────
    "fuel_level":               "CarSetup_Chassis_Rear_FuelLevel",
    "rear_arb_blades":          "CarSetup_Chassis_Rear_ArbBlades",
    # ── In-car adjustments ───────────────────────────────────────────
    "brake_bias":               "CarSetup_Chassis_InCarAdjustments_BrakePressureBias",
    "abs_setting":              "CarSetup_Chassis_InCarAdjustments_AbsSetting",
    "tc_setting":               "CarSetup_Chassis_InCarAdjustments_TcSetting",
    "fwt_dist":                 "CarSetup_Chassis_InCarAdjustments_FWtdist",
    "cross_weight":             "CarSetup_Chassis_InCarAdjustments_CrossWeight",
    # ── Gears + diff ──────────────────────────────────────────────────
    "gear_stack":               "CarSetup_Chassis_GearsDifferential_GearStack",
    "diff_friction_faces":      "CarSetup_Chassis_GearsDifferential_FrictionFaces",
    "diff_preload":             "CarSetup_Chassis_GearsDifferential_DiffPreload",
    # ── Dampers (per-axle, 4 channels each) ──────────────────────────
    "front_ls_comp":            "CarSetup_Dampers_FrontDampers_LowSpeedCompressionDamping",
    "front_hs_comp":            "CarSetup_Dampers_FrontDampers_HighSpeedCompressionDamping",
    "front_ls_rbd":             "CarSetup_Dampers_FrontDampers_LowSpeedReboundDamping",
    "front_hs_rbd":             "CarSetup_Dampers_FrontDampers_HighSpeedReboundDamping",
    "rear_ls_comp":             "CarSetup_Dampers_RearDampers_LowSpeedCompressionDamping",
    "rear_hs_comp":             "CarSetup_Dampers_RearDampers_HighSpeedCompressionDamping",
    "rear_ls_rbd":              "CarSetup_Dampers_RearDampers_LowSpeedReboundDamping",
    "rear_hs_rbd":              "CarSetup_Dampers_RearDampers_HighSpeedReboundDamping",
}


# ─────────────────────────────────────────────────────────────────────────────
# Aston Martin Vantage GT3 EVO — GT3 architecture (W4.2)
# Notable differences vs BMW M4 GT3:
#   - Aero balance section is `AeroBalanceCalculator` (not `AeroBalanceCalc`)
#   - Wing field is `RearWingAngle` (mirrors to TiresAero.AeroBalanceCalculator
#     too, but the dict only maps the chassis copy — single emit for now)
#   - Front section is `FrontBrakesLights` (not `FrontBrakes`)
#   - Front ARB is `FarbBlades` (not `ArbBlades`)
#   - Rear ARB is `RarbBlades` (not `ArbBlades`)
#   - 4 Aston-only fields: EnduranceLights, NightLedStripColor,
#     ThrottleResponse ("n (RED)"), EpasSetting ("n (PAS)")
#   - TC label is "n (TC SLIP)" (not "n (TC)")
#   - Rear toe is per-wheel (LeftRear.ToeIn / RightRear.ToeIn) — same as BMW
# Source: docs/audits/gt3_phase2/output.md:367-435 (verbatim).
# ─────────────────────────────────────────────────────────────────────────────

_ASTON_MARTIN_VANTAGE_GT3_PARAM_IDS: dict[str, str] = {
    # ── Aero ──────────────────────────────────────────────────────────
    "wing_angle":               "CarSetup_Chassis_Rear_RearWingAngle",            # int deg, ALSO mirrors to TiresAero.AeroBalanceCalculator.RearWingAngle
    "front_rh_at_speed":        "CarSetup_TiresAero_AeroBalanceCalculator_FrontRhAtSpeed",   # NB: Calculator suffix
    "rear_rh_at_speed":         "CarSetup_TiresAero_AeroBalanceCalculator_RearRhAtSpeed",
    "df_balance":               "CarSetup_TiresAero_AeroBalanceCalculator_FrontDownforce",
    # ── Tyres ─────────────────────────────────────────────────────────
    "lf_pressure":              "CarSetup_TiresAero_LeftFront_StartingPressure",
    "rf_pressure":              "CarSetup_TiresAero_RightFront_StartingPressure",
    "lr_pressure":              "CarSetup_TiresAero_LeftRear_StartingPressure",
    "rr_pressure":              "CarSetup_TiresAero_RightRear_StartingPressure",
    "tyre_type":                "CarSetup_TiresAero_TireType_TireType",
    # ── FrontBrakesLights ─────────────────────────────────────────────
    "front_arb_blades":         "CarSetup_Chassis_FrontBrakesLights_FarbBlades",  # NB: Farb*, not Arb*
    "front_toe":                "CarSetup_Chassis_FrontBrakesLights_TotalToeIn",
    "front_master_cyl":         "CarSetup_Chassis_FrontBrakesLights_FrontMasterCyl",
    "rear_master_cyl":          "CarSetup_Chassis_FrontBrakesLights_RearMasterCyl",
    "pad_compound":             "CarSetup_Chassis_FrontBrakesLights_BrakePads",
    "endurance_lights":         "CarSetup_Chassis_FrontBrakesLights_EnduranceLights",  # ASTON ONLY
    "night_led_strip_color":    "CarSetup_Chassis_FrontBrakesLights_NightLedStripColor",  # ASTON+PORSCHE
    "front_splitter_height":    "CarSetup_Chassis_FrontBrakesLights_CenterFrontSplitterHeight",
    # ── Per-corner ────────────────────────────────────────────────────
    "lf_corner_weight":         "CarSetup_Chassis_LeftFront_CornerWeight",
    "lf_ride_height":           "CarSetup_Chassis_LeftFront_RideHeight",
    "lf_bump_rubber_gap":       "CarSetup_Chassis_LeftFront_BumpRubberGap",
    "lf_spring_rate":           "CarSetup_Chassis_LeftFront_SpringRate",
    "lf_camber":                "CarSetup_Chassis_LeftFront_Camber",
    "rf_corner_weight":         "CarSetup_Chassis_RightFront_CornerWeight",
    "rf_ride_height":           "CarSetup_Chassis_RightFront_RideHeight",
    "rf_bump_rubber_gap":       "CarSetup_Chassis_RightFront_BumpRubberGap",
    "rf_spring_rate":           "CarSetup_Chassis_RightFront_SpringRate",
    "rf_camber":                "CarSetup_Chassis_RightFront_Camber",
    "lr_corner_weight":         "CarSetup_Chassis_LeftRear_CornerWeight",
    "lr_ride_height":           "CarSetup_Chassis_LeftRear_RideHeight",
    "lr_bump_rubber_gap":       "CarSetup_Chassis_LeftRear_BumpRubberGap",
    "lr_spring_rate":           "CarSetup_Chassis_LeftRear_SpringRate",
    "lr_camber":                "CarSetup_Chassis_LeftRear_Camber",
    "lr_toe":                   "CarSetup_Chassis_LeftRear_ToeIn",
    "rr_corner_weight":         "CarSetup_Chassis_RightRear_CornerWeight",
    "rr_ride_height":           "CarSetup_Chassis_RightRear_RideHeight",
    "rr_bump_rubber_gap":       "CarSetup_Chassis_RightRear_BumpRubberGap",
    "rr_spring_rate":           "CarSetup_Chassis_RightRear_SpringRate",
    "rr_camber":                "CarSetup_Chassis_RightRear_Camber",
    "rr_toe":                   "CarSetup_Chassis_RightRear_ToeIn",
    # ── Rear section ─────────────────────────────────────────────────
    "fuel_level":               "CarSetup_Chassis_Rear_FuelLevel",
    "rear_arb_blades":          "CarSetup_Chassis_Rear_RarbBlades",               # NB: Rarb*, not Arb*
    # ── In-car adjustments (Aston-extended) ──────────────────────────
    "brake_bias":               "CarSetup_Chassis_InCarAdjustments_BrakePressureBias",
    "abs_setting":              "CarSetup_Chassis_InCarAdjustments_AbsSetting",
    "tc_setting":               "CarSetup_Chassis_InCarAdjustments_TcSetting",     # "n (TC SLIP)" — Aston uses TC SLIP label
    "throttle_response":        "CarSetup_Chassis_InCarAdjustments_ThrottleResponse",  # ASTON ONLY: "n (RED)"
    "epas_setting":             "CarSetup_Chassis_InCarAdjustments_EpasSetting",   # ASTON ONLY: "n (PAS)"
    "fwt_dist":                 "CarSetup_Chassis_InCarAdjustments_FWtdist",
    "cross_weight":             "CarSetup_Chassis_InCarAdjustments_CrossWeight",
    # ── Gears + diff ──────────────────────────────────────────────────
    "gear_stack":               "CarSetup_Chassis_GearsDifferential_GearStack",
    "diff_friction_faces":      "CarSetup_Chassis_GearsDifferential_FrictionFaces",
    "diff_preload":             "CarSetup_Chassis_GearsDifferential_DiffPreload",
    # ── Dampers (per-axle, 4 channels each) — same as BMW M4 GT3 ─────
    "front_ls_comp":            "CarSetup_Dampers_FrontDampers_LowSpeedCompressionDamping",
    "front_hs_comp":            "CarSetup_Dampers_FrontDampers_HighSpeedCompressionDamping",
    "front_ls_rbd":             "CarSetup_Dampers_FrontDampers_LowSpeedReboundDamping",
    "front_hs_rbd":             "CarSetup_Dampers_FrontDampers_HighSpeedReboundDamping",
    "rear_ls_comp":             "CarSetup_Dampers_RearDampers_LowSpeedCompressionDamping",
    "rear_hs_comp":             "CarSetup_Dampers_RearDampers_HighSpeedCompressionDamping",
    "rear_ls_rbd":              "CarSetup_Dampers_RearDampers_LowSpeedReboundDamping",
    "rear_hs_rbd":              "CarSetup_Dampers_RearDampers_HighSpeedReboundDamping",
}


# ─────────────────────────────────────────────────────────────────────────────
# Porsche 911 GT3 R (992) — GT3 architecture (W4.2)
# Most divergent of the three GT3 cars.  Differences from BMW M4 GT3:
#   - Front ARB is `ArbSetting` (single int — NOT a blade index)
#   - Rear ARB is `RarbSetting` (single int)
#   - Rear toe is paired (`Chassis.Rear.TotalToeIn`) — NOT per-wheel
#   - Fuel level is in `FrontBrakesLights` section, NOT `Rear`
#   - Wing field is `WingSetting` and is mirrored in BOTH `Chassis.Rear` AND
#     `TiresAero.AeroBalanceCalc` (driver YAML shows both copies)
#   - 3 Porsche-only fields: ThrottleShapeSetting, DashDisplayPage,
#     NightLedStripColor
#   - TC label is "n (TC-LAT)"
#   - Damper range 0–12 (already wired in W3.2 DamperModel)
# Source: docs/audits/gt3_phase2/output.md:443-513 (verbatim).
# ─────────────────────────────────────────────────────────────────────────────

_PORSCHE_992_GT3R_PARAM_IDS: dict[str, str] = {
    # ── Aero ──────────────────────────────────────────────────────────
    "wing_angle":               "CarSetup_Chassis_Rear_WingSetting",              # NB: WingSetting (Porsche uses this name in BOTH chassis-rear AND aero-balance)
    "front_rh_at_speed":        "CarSetup_TiresAero_AeroBalanceCalc_FrontRhAtSpeed",
    "rear_rh_at_speed":         "CarSetup_TiresAero_AeroBalanceCalc_RearRhAtSpeed",
    "df_balance":               "CarSetup_TiresAero_AeroBalanceCalc_FrontDownforce",
    # ── Tyres ─────────────────────────────────────────────────────────
    "lf_pressure":              "CarSetup_TiresAero_LeftFront_StartingPressure",
    "rf_pressure":              "CarSetup_TiresAero_RightFront_StartingPressure",
    "lr_pressure":              "CarSetup_TiresAero_LeftRear_StartingPressure",
    "rr_pressure":              "CarSetup_TiresAero_RightRear_StartingPressure",
    "tyre_type":                "CarSetup_TiresAero_TireType_TireType",
    # ── FrontBrakesLights ─────────────────────────────────────────────
    "front_arb_setting":        "CarSetup_Chassis_FrontBrakesLights_ArbSetting",  # **INT, not blade — Porsche-unique**
    "front_toe":                "CarSetup_Chassis_FrontBrakesLights_TotalToeIn",
    "fuel_level":               "CarSetup_Chassis_FrontBrakesLights_FuelLevel",   # **PORSCHE-UNIQUE: fuel here, not in Rear**
    "front_master_cyl":         "CarSetup_Chassis_FrontBrakesLights_FrontMasterCyl",
    "rear_master_cyl":          "CarSetup_Chassis_FrontBrakesLights_RearMasterCyl",
    "pad_compound":             "CarSetup_Chassis_FrontBrakesLights_BrakePads",
    "night_led_strip_color":    "CarSetup_Chassis_FrontBrakesLights_NightLedStripColor",
    "front_splitter_height":    "CarSetup_Chassis_FrontBrakesLights_CenterFrontSplitterHeight",
    # ── Per-corner ────────────────────────────────────────────────────
    "lf_corner_weight":         "CarSetup_Chassis_LeftFront_CornerWeight",
    "lf_ride_height":           "CarSetup_Chassis_LeftFront_RideHeight",
    "lf_bump_rubber_gap":       "CarSetup_Chassis_LeftFront_BumpRubberGap",
    "lf_spring_rate":           "CarSetup_Chassis_LeftFront_SpringRate",
    "lf_camber":                "CarSetup_Chassis_LeftFront_Camber",
    "rf_corner_weight":         "CarSetup_Chassis_RightFront_CornerWeight",
    "rf_ride_height":           "CarSetup_Chassis_RightFront_RideHeight",
    "rf_bump_rubber_gap":       "CarSetup_Chassis_RightFront_BumpRubberGap",
    "rf_spring_rate":           "CarSetup_Chassis_RightFront_SpringRate",
    "rf_camber":                "CarSetup_Chassis_RightFront_Camber",
    "lr_corner_weight":         "CarSetup_Chassis_LeftRear_CornerWeight",
    "lr_ride_height":           "CarSetup_Chassis_LeftRear_RideHeight",
    "lr_bump_rubber_gap":       "CarSetup_Chassis_LeftRear_BumpRubberGap",
    "lr_spring_rate":           "CarSetup_Chassis_LeftRear_SpringRate",
    "lr_camber":                "CarSetup_Chassis_LeftRear_Camber",
    # NO lr_toe / rr_toe per-wheel — Porsche uses paired rear toe (see below)
    "rr_corner_weight":         "CarSetup_Chassis_RightRear_CornerWeight",
    "rr_ride_height":           "CarSetup_Chassis_RightRear_RideHeight",
    "rr_bump_rubber_gap":       "CarSetup_Chassis_RightRear_BumpRubberGap",
    "rr_spring_rate":           "CarSetup_Chassis_RightRear_SpringRate",
    "rr_camber":                "CarSetup_Chassis_RightRear_Camber",
    # ── Rear section (Porsche-specific) ───────────────────────────────
    "rear_arb_setting":         "CarSetup_Chassis_Rear_RarbSetting",              # **INT, not blade**
    "rear_toe":                 "CarSetup_Chassis_Rear_TotalToeIn",               # **PAIRED rear toe — Porsche-unique**
    # NO Chassis.Rear.FuelLevel — see FrontBrakesLights.FuelLevel above
    # ── In-car adjustments (Porsche-extended) ────────────────────────
    "brake_bias":               "CarSetup_Chassis_InCarAdjustments_BrakePressureBias",
    "abs_setting":              "CarSetup_Chassis_InCarAdjustments_AbsSetting",
    "tc_setting":               "CarSetup_Chassis_InCarAdjustments_TcSetting",    # "n (TC-LAT)" — Porsche label
    "throttle_shape_setting":   "CarSetup_Chassis_InCarAdjustments_ThrottleShapeSetting",  # PORSCHE ONLY
    "dash_display_page":        "CarSetup_Chassis_InCarAdjustments_DashDisplayPage",       # PORSCHE ONLY
    "fwt_dist":                 "CarSetup_Chassis_InCarAdjustments_FWtdist",
    "cross_weight":             "CarSetup_Chassis_InCarAdjustments_CrossWeight",
    # ── Gears + diff ──────────────────────────────────────────────────
    "gear_stack":               "CarSetup_Chassis_GearsDifferential_GearStack",
    "diff_friction_faces":      "CarSetup_Chassis_GearsDifferential_FrictionFaces",
    "diff_preload":             "CarSetup_Chassis_GearsDifferential_DiffPreload",
    # ── Dampers (per-axle, 4 channels each).  Range 0–12 (W3.2). ─────
    "front_ls_comp":            "CarSetup_Dampers_FrontDampers_LowSpeedCompressionDamping",
    "front_hs_comp":            "CarSetup_Dampers_FrontDampers_HighSpeedCompressionDamping",
    "front_ls_rbd":             "CarSetup_Dampers_FrontDampers_LowSpeedReboundDamping",
    "front_hs_rbd":             "CarSetup_Dampers_FrontDampers_HighSpeedReboundDamping",
    "rear_ls_comp":             "CarSetup_Dampers_RearDampers_LowSpeedCompressionDamping",
    "rear_hs_comp":             "CarSetup_Dampers_RearDampers_HighSpeedCompressionDamping",
    "rear_ls_rbd":              "CarSetup_Dampers_RearDampers_LowSpeedReboundDamping",
    "rear_hs_rbd":              "CarSetup_Dampers_RearDampers_HighSpeedReboundDamping",
}


# Master dispatch table
_CAR_PARAM_IDS: dict[str, dict[str, str]] = {
    "bmw":                       _BMW_PARAM_IDS,
    "ferrari":                   _FERRARI_PARAM_IDS,
    "porsche":                   _PORSCHE_PARAM_IDS,
    "cadillac":                  _CADILLAC_PARAM_IDS,
    "acura":                     _ACURA_PARAM_IDS,
    "bmw_m4_gt3":                _BMW_M4_GT3_PARAM_IDS,
    "aston_martin_vantage_gt3":  _ASTON_MARTIN_VANTAGE_GT3_PARAM_IDS,
    "porsche_992_gt3r":          _PORSCHE_992_GT3R_PARAM_IDS,
}


def _validate_setup_values(
    step1: RakeSolution,
    step2: HeaveSolution,
    step3: CornerSpringSolution,
    step4: ARBSolution,
    step5: WheelGeometrySolution,
    step6: DamperSolution,
    car=None,
) -> list[str]:
    """Validate and clamp all numeric values to iRacing accepted ranges.

    Mutates step objects in-place when clamping is needed.
    Returns a list of warning messages for any values that were changed.
    """
    from car_model.cars import GarageRanges
    gr = car.garage_ranges if car is not None else GarageRanges()
    warnings: list[str] = []

    def _clamp_field(obj, attr: str, lo: float, hi: float, name: str, unit: str = "") -> None:
        value = getattr(obj, attr)
        clamped = max(lo, min(hi, value))
        if abs(clamped - value) > 1e-6:
            warnings.append(
                f"setup_writer: {name}={value:.1f}{unit} out of range "
                f"[{lo:.0f}, {hi:.0f}]{unit} — clamped to {clamped:.1f}"
            )
            setattr(obj, attr, clamped)

    def _clamp_int_field(obj, attr: str, lo: int, hi: int, name: str, unit: str = "") -> None:
        value = getattr(obj, attr)
        clamped = max(lo, min(hi, int(round(value))))
        if clamped != int(round(value)):
            warnings.append(
                f"setup_writer: {name}={value}{unit} out of range "
                f"[{lo}, {hi}]{unit} — clamped to {clamped}"
            )
        setattr(obj, attr, clamped)

    # GT3 architecture: no heave/third springs, no torsion bars. The clamp
    # block below would be a no-op (sentinel ranges from W3.1 are (0.0, 0.0)
    # and step2 is a HeaveSolution.null()), but skipping it explicitly avoids
    # mutating null fields and makes the GT3 path auditable. Audit: O14/O15.
    _is_gt3_validation = (
        car is not None
        and getattr(getattr(car, "suspension_arch", None), "has_heave_third", True) is False
    )

    # Ride heights (static)
    _clamp_field(step1, "static_front_rh_mm", *gr.static_rh_mm, "front_static_rh", "mm")
    _clamp_field(step1, "static_rear_rh_mm", *gr.static_rh_mm, "rear_static_rh", "mm")

    # Pushrod offsets
    if not _is_gt3_validation:
        # GT3 has no pushrod-offset RH workflow (uses BumpRubberGap/spring
        # perch). Step1 carries 0.0 placeholders; skip clamping.
        _clamp_field(step1, "front_pushrod_offset_mm", *gr.front_pushrod_mm, "front_pushrod", "mm")
        _clamp_field(step1, "rear_pushrod_offset_mm", *gr.rear_pushrod_mm, "rear_pushrod", "mm")

    if not _is_gt3_validation:
        # Heave / third spring
        _clamp_field(step2, "front_heave_nmm", *gr.front_heave_nmm, "front_heave", " N/mm")
        _clamp_field(step2, "rear_third_nmm", *gr.rear_third_nmm, "rear_third", " N/mm")

        # Perch offsets
        _clamp_field(step2, "perch_offset_front_mm", *gr.front_heave_perch_mm, "front_heave_perch", "mm")
        _clamp_field(step2, "perch_offset_rear_mm", *gr.rear_third_perch_mm, "rear_third_perch", "mm")

        # Corner springs — skip torsion OD clamping for roll-spring cars (Porsche)
        if gr.front_torsion_od_mm != (0.0, 0.0):
            _clamp_field(step3, "front_torsion_od_mm", *gr.front_torsion_od_mm, "front_torsion_od", "mm")
        _clamp_field(step3, "rear_spring_rate_nmm", *gr.rear_spring_nmm, "rear_spring_rate", " N/mm")
        _clamp_field(step3, "rear_spring_perch_mm", *gr.rear_spring_perch_mm, "rear_spring_perch", "mm")

    # ARB blades
    if step4 is not None:
        _clamp_int_field(step4, "front_arb_blade_start", *gr.arb_blade, "front_arb_blade")
        _clamp_int_field(step4, "rear_arb_blade_start", *gr.arb_blade, "rear_arb_blade")

    # Wheel geometry
    if step5 is not None:
        _clamp_field(step5, "front_camber_deg", *gr.camber_front_deg, "front_camber", " deg")
        _clamp_field(step5, "rear_camber_deg", *gr.camber_rear_deg, "rear_camber", " deg")
        _clamp_field(step5, "front_toe_mm", *gr.toe_front_mm, "front_toe", "mm")
        _clamp_field(step5, "rear_toe_mm", *gr.toe_rear_mm, "rear_toe", "mm")

    # Damper clicks — use per-parameter ranges from DamperModel when available
    # (Ferrari has 0-40 comp/rbd but 0-11 slope; BMW is 0-11 all)
    d = car.damper if car is not None else None
    if step6 is None:
        return warnings
    for corner_name, corner in [
        ("lf", step6.lf), ("rf", step6.rf), ("lr", step6.lr), ("rr", step6.rr)
    ]:
        if d is not None:
            _clamp_int_field(corner, "ls_comp", *d.ls_comp_range, f"{corner_name}_ls_comp", " clicks")
            _clamp_int_field(corner, "ls_rbd", *d.ls_rbd_range, f"{corner_name}_ls_rbd", " clicks")
            _clamp_int_field(corner, "hs_comp", *d.hs_comp_range, f"{corner_name}_hs_comp", " clicks")
            _clamp_int_field(corner, "hs_rbd", *d.hs_rbd_range, f"{corner_name}_hs_rbd", " clicks")
            _clamp_int_field(corner, "hs_slope", *d.hs_slope_range, f"{corner_name}_hs_slope", " clicks")
        else:
            d_lo, d_hi = gr.damper_click
            _clamp_int_field(corner, "ls_comp", d_lo, d_hi, f"{corner_name}_ls_comp", " clicks")
            _clamp_int_field(corner, "ls_rbd", d_lo, d_hi, f"{corner_name}_ls_rbd", " clicks")
            _clamp_int_field(corner, "hs_comp", d_lo, d_hi, f"{corner_name}_hs_comp", " clicks")
            _clamp_int_field(corner, "hs_rbd", d_lo, d_hi, f"{corner_name}_hs_rbd", " clicks")
            _clamp_int_field(corner, "hs_slope", d_lo, d_hi, f"{corner_name}_hs_slope", " clicks")

    return warnings


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
    tyre_pressure_fl_kpa: float | None = None,
    tyre_pressure_fr_kpa: float | None = None,
    tyre_pressure_rl_kpa: float | None = None,
    tyre_pressure_rr_kpa: float | None = None,
    # --- Defaults for fields not computed by the solver ---
    brake_bias_pct: float | None = None,  # None = compute from car physics
    brake_bias_migration: float | None = None,
    brake_bias_target: float | None = None,
    brake_bias_migration_gain: float | None = None,
    pad_compound: str | None = None,
    front_master_cyl_mm: float | None = None,
    rear_master_cyl_mm: float | None = None,
    diff_coast_drive_ramp: str | None = None,
    diff_clutch_plates: int | None = None,
    diff_preload_nm: float | None = None,
    front_diff_preload_nm: float | None = None,
    tc_gain: int | None = None,
    tc_slip: int | None = None,
    gear_stack: str = "Short",
    speed_in_first_kph: float | None = None,
    speed_in_second_kph: float | None = None,
    speed_in_third_kph: float | None = None,
    speed_in_fourth_kph: float | None = None,
    speed_in_fifth_kph: float | None = None,
    speed_in_sixth_kph: float | None = None,
    speed_in_seventh_kph: float | None = None,
    fuel_low_warning_l: float = 8.0,
    fuel_target_l: float | None = None,
    roof_light_color: str = "Orange",
    hybrid_rear_drive_enabled: str | None = None,
    hybrid_rear_drive_corner_pct: float | None = None,
    include_computed: bool = False,
    front_tb_turns: float | None = None,
    rear_tb_turns: float | None = None,
    front_roll_perch_mm: float = 0.0,
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

    # ── Fetch car model ─────────────────────────────────────────────────────
    from car_model.cars import get_car as _get_car
    try:
        _car = _get_car(car_canonical)
    except Exception:
        _car = None
    front_perch_step = 1.0
    rear_third_perch_step = 1.0
    if _car is not None:
        front_perch_step = (
            getattr(_car.garage_ranges, "front_heave_perch_resolution_mm", None)
            or getattr(_car.garage_ranges, "perch_resolution_mm", 1.0)
            or 1.0
        )
        rear_third_perch_step = (
            getattr(_car.garage_ranges, "rear_third_perch_resolution_mm", None)
            or getattr(_car.garage_ranges, "perch_resolution_mm", 1.0)
            or 1.0
        )

    # ── Pre-write validation: garage correlation fix + range clamping ─────
    # Run BEFORE the Ferrari index conversion so the validator always receives
    # physical units (N/mm, mm OD).  The validator handles its own Ferrari
    # conversion internally and writes corrected physical values back to the
    # caller-supplied step objects.
    from output.garage_validator import validate_and_fix_garage_correlation
    if _car is not None:
        garage_warnings = validate_and_fix_garage_correlation(
            _car, step1, step2, step3, step5,
            fuel_l=fuel_l, track_name=track_name,
        )
        for w in garage_warnings:
            print(f"[garage] {w}")

    if _car is not None:
        if getattr(_car, "canonical_name", "") == "ferrari":
            # Convert Ferrari indexed values to public output indices for the
            # writer. The torsion-bar-turns formulas previously consumed
            # physical (pre-conversion) values; that path now lives in
            # solver.corner_spring_solver._solve_ferrari_torsion_bar_turns
            # and the result is carried via step3.front/rear_torsion_bar_turns.
            step2 = copy.deepcopy(step2)
            step3 = copy.deepcopy(step3)
            step2.front_heave_nmm = float(public_output_value(_car, "front_heave_nmm", step2.front_heave_nmm))
            step2.rear_third_nmm = float(public_output_value(_car, "rear_third_nmm", step2.rear_third_nmm))
            step3.front_torsion_od_mm = float(public_output_value(_car, "front_torsion_od_mm", step3.front_torsion_od_mm))
            step3.rear_spring_rate_nmm = float(public_output_value(_car, "rear_spring_rate_nmm", step3.rear_spring_rate_nmm))
            step3.rear_spring_perch_mm = 0.0
    clamp_warnings = _validate_setup_values(
        step1, step2, step3, step4, step5, step6, car=_car,
    )
    for w in clamp_warnings:
        print(f"[warning] {w}")
    garage_outputs = None
    if _car is not None:
        garage_model = _car.active_garage_output_model(track_name)
        if garage_model is not None:
            garage_outputs = garage_model.predict(
                GarageSetupState.from_solver_steps(
                    step1=step1,
                    step2=step2,
                    step3=step3,
                    step5=step5,
                    fuel_l=fuel_l,
                ),
                front_excursion_p99_mm=step2.front_excursion_at_rate_mm,
            )

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

    # ── Validate and clamp brake/diff/TC kwargs against garage ranges ─────
    if _car is not None:
        _gr = _car.garage_ranges
        # Brake bias target/migration
        if brake_bias_target is not None:
            brake_bias_target = max(_gr.brake_bias_target[0], min(_gr.brake_bias_target[1], brake_bias_target))
        if brake_bias_migration is not None:
            brake_bias_migration = max(_gr.brake_bias_migration[0], min(_gr.brake_bias_migration[1], brake_bias_migration))
        # Diff preload
        if diff_preload_nm is not None:
            diff_preload_nm = max(_gr.diff_preload_nm[0], min(_gr.diff_preload_nm[1], diff_preload_nm))
            diff_preload_nm = round(diff_preload_nm / _gr.diff_preload_step_nm) * _gr.diff_preload_step_nm
        # Master cylinders — snap to nearest valid option
        _mc_opts = _gr.brake_master_cyl_options_mm
        if front_master_cyl_mm is not None:
            front_master_cyl_mm = min(_mc_opts, key=lambda x: abs(x - front_master_cyl_mm))
        if rear_master_cyl_mm is not None:
            rear_master_cyl_mm = min(_mc_opts, key=lambda x: abs(x - rear_master_cyl_mm))
        # Pad compound
        if pad_compound is not None and pad_compound not in _gr.brake_pad_compound_options:
            pad_compound = "Medium"

    # ── Fallback defaults from car model for omitted supporting params ────
    # Hierarchy: solver-computed value > car model default > skip (truly optional only)
    if _car is not None:
        _gr = _car.garage_ranges

        # Diff preload — use car-specific calibrated default, clamped to garage range
        if diff_preload_nm is None:
            diff_preload_nm = _car.default_diff_preload_nm
        if diff_preload_nm is not None:
            _dp_lo, _dp_hi = _gr.diff_preload_nm
            diff_preload_nm = max(_dp_lo, min(_dp_hi, diff_preload_nm))
            diff_preload_nm = round(diff_preload_nm / _gr.diff_preload_step_nm) * _gr.diff_preload_step_nm

        # Diff coast/drive ramp — middle option from garage range
        if diff_coast_drive_ramp is None and _gr.diff_coast_drive_ramp_options:
            _mid = _gr.diff_coast_drive_ramp_options[len(_gr.diff_coast_drive_ramp_options) // 2]
            diff_coast_drive_ramp = f"{_mid[0]} / {_mid[1]}"

        # Diff clutch plates — middle option from garage range
        if diff_clutch_plates is None and _gr.diff_clutch_plates_options:
            diff_clutch_plates = _gr.diff_clutch_plates_options[len(_gr.diff_clutch_plates_options) // 2]

        # Brake master cylinders — middle option from garage range
        _mc_opts = _gr.brake_master_cyl_options_mm
        if front_master_cyl_mm is None and _mc_opts:
            front_master_cyl_mm = _mc_opts[len(_mc_opts) // 2]
        if rear_master_cyl_mm is None and _mc_opts:
            rear_master_cyl_mm = _mc_opts[len(_mc_opts) // 2]
        # Clamp MC values to nearest legal option (audit M1)
        if front_master_cyl_mm is not None and _mc_opts:
            front_master_cyl_mm = min(_mc_opts, key=lambda x: abs(x - front_master_cyl_mm))
        if rear_master_cyl_mm is not None and _mc_opts:
            rear_master_cyl_mm = min(_mc_opts, key=lambda x: abs(x - rear_master_cyl_mm))

        # Pad compound — middle option from garage range
        if pad_compound is None and _gr.brake_pad_compound_options:
            pad_compound = _gr.brake_pad_compound_options[len(_gr.brake_pad_compound_options) // 2]

        # TC gain / TC slip — default to mid-range, clamped to [1, 10] (audit M1)
        if tc_gain is None:
            tc_gain = 3
        tc_gain = max(1, min(10, tc_gain))
        if tc_slip is None:
            tc_slip = 3
        tc_slip = max(1, min(10, tc_slip))

        # Fuel target — default to full tank, clamped to [0, max_fuel] (audit M1)
        if fuel_target_l is None:
            fuel_target_l = _gr.max_fuel_l
        if fuel_target_l is not None:
            fuel_target_l = max(0.0, min(_gr.max_fuel_l, fuel_target_l))

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
    ids = _CAR_PARAM_IDS.get(car_canonical.lower())
    if ids is None:
        raise ValueError(f"No STO parameter ID mapping for car: {car_canonical}")

    def _w_num(param: str, value: float | int, unit: str) -> None:
        """Write a numeric param using car-specific ID, or TODO comment if unmapped."""
        if value is None:
            return
        if param in ids:
            pid = ids[param]
            if not pid:  # empty string = suppressed for this car
                return
            _numeric(details, pid, value, unit)
        else:
            _comment(details, f"TODO: {car_canonical} {param} not mapped")

    def _w_str(param: str, value: str, unit: str = "") -> None:
        """Write a string param using car-specific ID, or TODO comment if unmapped."""
        if value is None:
            return
        if param in ids:
            pid = ids[param]
            if not pid:
                return
            _string(details, pid, value, unit)
        else:
            _comment(details, f"TODO: {car_canonical} {param} not mapped")

    is_acura = car_canonical.lower() == "acura"
    is_porsche = car_canonical.lower() == "porsche"
    has_roll_dampers = is_acura or is_porsche
    # GT3 architecture flag (W4.1). True when the car has no heave/third
    # spring (SuspensionArchitecture.GT3_COIL_4WHEEL). Gates every GTP-only
    # write block below: heave/third spring writes, torsion bar writes,
    # pushrod offsets, per-corner damper writes, GTP roll/3rd damper paths.
    # Replaces those with GT3-equivalent writes (4 corner spring rates,
    # BumpRubberGap × 4, splitter height, per-axle dampers, indexed TC/ABS
    # label strings).
    is_gt3 = (
        _car is not None
        and getattr(getattr(_car, "suspension_arch", None), "has_heave_third", True) is False
    )
    # W4.3 NOTE: iRacing schema round-trip validation requires loading the
    # written .sto into a live iRacing client and confirming the garage UI
    # accepts every field without dropping or overriding values.  The repo
    # has no offline copy of iRacing's CarSetup XSD; round-trip validation
    # is a manual driver-side QA step performed before each GT3 release.
    # The current .sto write is well-formed XML with all required CarSetup_*
    # fields present (verified in tests/test_setup_writer_gt3_*.py); the open
    # question is whether iRacing's parser accepts the BumpRubberGap +
    # CenterFrontSplitterHeight + per-car ARB encodings without further
    # massaging.  TODO(W4.3-deferred): add a fixture-based round-trip test
    # once we have a known-good iRacing-emitted GT3 .sto to compare against.
    # W4.2: GT3 sub-dispatch.  All three GT3 cars share the GT3 architecture
    # (single `is_gt3` flag), but diverge inside the GT3 path on:
    #  - rear toe per-wheel (BMW/Aston) vs paired (Porsche `Chassis.Rear.TotalToeIn`)
    #  - ARB blade int (BMW/Aston use `*ArbBlades`/`*Blades`) vs single int
    #    (Porsche `ArbSetting`/`RarbSetting`)
    #  - fuel section (BMW/Aston in `Chassis.Rear`, Porsche in `Chassis.FrontBrakesLights`)
    #  - TC label suffix (`(TC)` BMW / `(TC SLIP)` Aston / `(TC-LAT)` Porsche)
    #  - Aston-only display fields: throttle_response, epas_setting, endurance_lights
    #  - Porsche-only display fields: throttle_shape_setting, dash_display_page
    is_bmw_gt3 = is_gt3 and car_canonical.lower() == "bmw_m4_gt3"
    is_aston_gt3 = is_gt3 and car_canonical.lower() == "aston_martin_vantage_gt3"
    is_porsche_gt3 = is_gt3 and car_canonical.lower() == "porsche_992_gt3r"

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
    _w_num("lf_ride_height", _decimal_1(step1.static_front_rh_mm), "mm")
    _w_num("rf_ride_height", _decimal_1(step1.static_front_rh_mm), "mm")
    _w_num("lr_ride_height", _decimal_1(step1.static_rear_rh_mm),  "mm")
    _w_num("rr_ride_height", _decimal_1(step1.static_rear_rh_mm),  "mm")

    # === Pushrod offsets ===
    if not is_gt3:
        # GT3 has no pushrod-offset RH workflow; static RH is set via
        # SpringPerchOffset + BumpRubberGap (per corner). The GT3 PARAM_IDS
        # dict has no `front_pushrod_offset` key so this would TODO-comment;
        # gate explicitly so the comment isn't emitted.
        _w_num("front_pushrod_offset", round(step1.front_pushrod_offset_mm * 2) / 2, "mm")
        _w_num("rear_pushrod_offset",  round(step1.rear_pushrod_offset_mm * 2) / 2,  "mm")

    if not is_gt3:
        # === Heave / Third springs (GTP) ===
        _w_num("front_heave_spring",   int(round(step2.front_heave_nmm)),      "N/mm")
        _w_num("front_heave_perch",    _snap_to_step(step2.perch_offset_front_mm, front_perch_step), "mm")
        _w_num("rear_third_spring",    int(round(step2.rear_third_nmm)),        "N/mm")
        _w_num("rear_third_perch",     _snap_to_step(step2.perch_offset_rear_mm, rear_third_perch_step),  "mm")

        # === Corner springs (GTP — torsion bar front, coil/torsion rear) ===
        _front_torsion_value = (
            int(round(step3.front_torsion_od_mm))
            if car_canonical.lower() == "ferrari"
            else step3.front_torsion_od_mm
        )
        _w_num("lf_torsion_od", _front_torsion_value, "mm")
        _w_num("rf_torsion_od", _front_torsion_value, "mm")
        # Torsion bar turns — Ferrari has a calibrated regression solver
        # (`_solve_ferrari_torsion_bar_turns`); BMW / Cadillac / Acura preserve
        # the driver's loaded value (no calibrated solver — formula-derived
        # values drifted from driver ground truth, see Unit 3); Porsche has
        # no front torsion bar (writes 0.0 by default). The corner-spring
        # solver's `_solve_torsion_bar_turns` dispatcher populates
        # step3.front_torsion_bar_turns / step3.rear_torsion_bar_turns; the
        # explicit override (front_tb_turns / rear_tb_turns kwargs) wins
        # when a caller wants to force a value.
        if front_tb_turns is not None:
            _tb_turns = round(front_tb_turns, 3)
        elif hasattr(step3, 'front_torsion_bar_turns'):
            _tb_turns = round(step3.front_torsion_bar_turns, 3)
        elif garage_outputs is not None:
            _tb_turns = round(garage_outputs.torsion_bar_turns, 3)
        else:
            _tb_turns = 0.0
        _w_num("lf_torsion_turns", _tb_turns, "Turns")
        _w_num("rf_torsion_turns", _tb_turns, "Turns")
        # Ferrari + Acura have rear torsion bar turns (BMW / Cadillac /
        # Porsche do not map lr_torsion_turns / rr_torsion_turns).
        if "lr_torsion_turns" in ids:
            if rear_tb_turns is not None:
                _rear_tb_turns = round(rear_tb_turns, 3)
            elif hasattr(step3, 'rear_torsion_bar_turns'):
                _rear_tb_turns = round(step3.rear_torsion_bar_turns, 3)
            else:
                _rear_tb_turns = 0.0
            _w_num("lr_torsion_turns", _rear_tb_turns, "Turns")
            _w_num("rr_torsion_turns", _rear_tb_turns, "Turns")
        # Porsche roll spring (only if car maps it)
        if "lf_roll_spring" in ids:
            _numeric(details, ids["lf_roll_spring"], int(round(step3.front_wheel_rate_nmm)), "N/mm")
            _numeric(details, ids["rf_roll_spring"], int(round(step3.front_wheel_rate_nmm)), "N/mm")
        # Front roll perch offset (Porsche — preloads the roll spring)
        if "front_roll_perch" in ids:
            _w_num("front_roll_perch", _snap_to_step(front_roll_perch_mm, 0.5), "mm")
        # Acura front roll spring + perch (ORECA chassis)
        if "front_roll_spring_nmm" in ids:
            _roll_spring = getattr(step3, 'front_roll_spring_nmm', 0.0)
            if _roll_spring > 0:
                _w_num("front_roll_spring_nmm", round(_roll_spring, 1), "N/mm")
                _w_num("front_roll_perch_mm", round(
                    _snap_to_step(front_roll_perch_mm, 0.5), 1), "mm")
        # Rear spring / torsion bar
        if is_acura:
            # Acura rear uses torsion bar OD (mapped via lr_spring_rate -> TorsionBarOD)
            _rear_od = step3.rear_torsion_od_mm if hasattr(step3, 'rear_torsion_od_mm') and step3.rear_torsion_od_mm else 13.9
            _w_num("lr_spring_rate", _rear_od, "mm")
            _w_num("rr_spring_rate", _rear_od, "mm")
        else:
            _w_num("lr_spring_rate",   int(round(step3.rear_spring_rate_nmm)), "N/mm")
            _w_num("rr_spring_rate",   int(round(step3.rear_spring_rate_nmm)), "N/mm")
        # Ferrari/Acura have no rear coil spring perch — skip if unmapped or suppressed
        if ids.get("lr_spring_perch"):
            _w_num("lr_spring_perch",  round(step3.rear_spring_perch_mm, 1),   "mm")
            _w_num("rr_spring_perch",  round(step3.rear_spring_perch_mm, 1),   "mm")
    else:
        # === GT3 corner springs (W4.1): 4 paired coil-overs ===
        # Front + rear axles each pair LF==RF and LR==RR. Source rates from
        # step3 (W2.3 added front_coil_rate_nmm; rear_spring_rate_nmm is the
        # rear coil rate). The PARAM_IDS dict has no torsion_od / torsion_turns
        # entries so the GTP block above would TODO-comment them.
        _front_rate_nmm = float(step3.front_coil_rate_nmm)
        _rear_rate_nmm = float(step3.rear_spring_rate_nmm)
        _w_num("lf_spring_rate", int(round(_front_rate_nmm)), "N/mm")
        _w_num("rf_spring_rate", int(round(_front_rate_nmm)), "N/mm")  # paired
        _w_num("lr_spring_rate", int(round(_rear_rate_nmm)),  "N/mm")
        _w_num("rr_spring_rate", int(round(_rear_rate_nmm)),  "N/mm")  # paired
        # === GT3 BumpRubberGap × 4 + CenterFrontSplitterHeight ===
        # Defaults to 0.0 mm placeholder. W4.3 will source these from the GT3
        # garage state once the plumbing exists. The fields MUST appear in the
        # .sto so iRacing's schema validator doesn't reject the file for
        # missing required GT3 garage params.
        for _corner in ("lf", "rf", "lr", "rr"):
            _w_num(f"{_corner}_bump_rubber_gap", 0.0, "mm")
        _w_num("front_splitter_height", 0.0, "mm")
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
    # GT3 cars don't have heave/third/torsion display fields — skip the whole
    # block. Corner weights for GT3 are emitted separately below.
    if include_computed and not is_gt3:
        if garage_outputs is not None:
            _lf_sd = round(garage_outputs.front_shock_defl_static_mm, 1)
            _lr_sd = round(garage_outputs.rear_shock_defl_static_mm, 1)
            _tb_defl = round(garage_outputs.torsion_bar_defl_mm, 1)
            _heave_defl_static = round(garage_outputs.heave_spring_defl_static_mm, 1)
            _heave_slider_static = round(garage_outputs.heave_slider_defl_static_mm, 1)
            _lr_spring_defl = round(garage_outputs.rear_spring_defl_static_mm, 1)
            _r3_defl = round(garage_outputs.third_spring_defl_static_mm, 1)
            _r3_slider = round(garage_outputs.third_slider_defl_static_mm, 1)
        elif _car is not None:
            _fh = step2.front_heave_nmm
            _fh_perch = step2.perch_offset_front_mm
            _f_od = step3.front_torsion_od_mm
            _dm = _car.deflection
            _k_torsion = _car.corner_spring.torsion_bar_rate(_f_od)

            _lf_sd = round(_dm.shock_defl_front(step1.front_pushrod_offset_mm), 1)
            _lr_sd = round(_dm.shock_defl_rear(step1.rear_pushrod_offset_mm), 1)
            _tb_defl = round(_dm.torsion_bar_defl(_fh, _fh_perch, _k_torsion), 1)
            _heave_defl_static = round(_dm.heave_spring_defl_static(_fh, _fh_perch, _f_od), 1)
            _heave_slider_static = round(_dm.heave_slider_defl_static(_fh, _fh_perch, _f_od), 1)
            _lr_spring_defl = round(_dm.rear_spring_defl_static(
                step3.rear_spring_rate_nmm, step3.rear_spring_perch_mm,
                third_rate_nmm=step2.rear_third_nmm,
                third_perch_mm=step2.perch_offset_rear_mm,
                pushrod_mm=step1.rear_pushrod_offset_mm), 1)
            _r3_defl = round(_dm.third_spring_defl_static(
                step2.rear_third_nmm, step2.perch_offset_rear_mm,
                spring_rate_nmm=step3.rear_spring_rate_nmm,
                spring_perch_mm=step3.rear_spring_perch_mm,
                pushrod_mm=step1.rear_pushrod_offset_mm), 1)
            _r3_slider = round(_dm.third_slider_defl_static(_r3_defl), 1)
        else:
            _fh = step2.front_heave_nmm
            _fh_perch = step2.perch_offset_front_mm
            _lf_sd = round(step1.static_front_rh_mm * 0.487, 1)
            _lr_sd = round(step1.static_rear_rh_mm * 0.462, 1)
            _tb_defl = round(_tb_turns * 181.5, 1)
            if hasattr(_car, 'deflection') and getattr(_car.deflection, 'is_calibrated', False):
                _heave_defl_static = round(40.5 + (-0.55) * _fh, 1)
            else:
                _heave_defl_static = 0.0  # No calibrated model; skip display value
            _heave_defl_static = max(0.0, _heave_defl_static)  # Safety clamp
            if hasattr(_car, 'deflection') and getattr(_car.deflection, 'is_calibrated', False):
                _heave_slider_static = round(46.2 + 0.012 * _fh + 0.251 * _fh_perch, 1)
            else:
                _heave_slider_static = 0.0  # No calibrated model; skip display value
            _heave_slider_static = max(0.0, _heave_slider_static)  # Safety clamp
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
        if garage_outputs is not None:
            _heave_defl_max = garage_outputs.heave_spring_defl_max_mm
        elif getattr(step2, "defl_max_front_mm", 0) > 0:
            _heave_defl_max = getattr(step2, "defl_max_front_mm")
        elif _car is not None:
            _fh = step2.front_heave_nmm
            _heave_defl_max = (_car.heave_spring.heave_spring_defl_max_intercept_mm
                               + _car.heave_spring.heave_spring_defl_max_slope * _fh)
        else:
            _fh = step2.front_heave_nmm
            _heave_defl_max = 106.43 + (-0.310) * _fh
        _w_num("front_heave_spring_defl_static", _heave_defl_static, "mm")
        _w_num("front_heave_spring_defl_max", round(_heave_defl_max, 1), "mm")
        _w_num("front_heave_slider_defl_static", _heave_slider_static, "mm")

        _w_num("lr_spring_defl_static", _lr_spring_defl, "mm")
        _w_num("rr_spring_defl_static", _lr_spring_defl, "mm")
        if garage_outputs is not None:
            _lr_defl_max = round(garage_outputs.rear_spring_defl_max_mm, 1)
            _r3_defl_max = round(garage_outputs.third_spring_defl_max_mm, 1)
        elif _car is not None:
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

    if include_computed and is_gt3:
        # GT3 corner weights still emit; heave/torsion deflections do not.
        _w_num("lf_corner_weight", _lf_cw, "N")
        _w_num("rf_corner_weight", _rf_cw, "N")
        _w_num("lr_corner_weight", _lr_cw, "N")
        _w_num("rr_corner_weight", _rr_cw, "N")

    # === ARBs ===
    if step4 is not None:
        if not is_gt3:
            # GTP cars: separate size string + blade int.
            _w_str("front_arb_size",   step4.front_arb_size)
            _w_num("front_arb_blades", step4.front_arb_blade_start, "")
            _w_str("rear_arb_size",    step4.rear_arb_size)
            _w_num("rear_arb_blades",  step4.rear_arb_blade_start, "")
        elif is_porsche_gt3:
            # W4.2: Porsche 992 GT3 R uses a single integer ArbSetting (not
            # blade index).  Both axles read off step4.*_arb_blade_start
            # (already an int; the GT3 ARB solver writes ints from W2.4).
            _w_num("front_arb_setting", step4.front_arb_blade_start, "")
            _w_num("rear_arb_setting",  step4.rear_arb_blade_start, "")
        else:
            # BMW M4 GT3 + Aston Vantage GT3: blade ints under car-specific
            # XML IDs (BMW: ArbBlades, Aston: FarbBlades / RarbBlades).  The
            # PARAM_IDS dict handles the XML name divergence.
            _w_num("front_arb_blades", step4.front_arb_blade_start, "")
            _w_num("rear_arb_blades",  step4.rear_arb_blade_start, "")

    # === Cross weight (computed by iRacing) ===
    if include_computed:
        _w_num("cross_weight", 50, "%")

    # === Wheel geometry ===
    if step5 is not None:
        _w_num("lf_camber",  step5.front_camber_deg, "deg")
        _w_num("rf_camber",  step5.front_camber_deg, "deg")
        _w_num("lr_camber",  step5.rear_camber_deg,  "deg")
        _w_num("rr_camber",  step5.rear_camber_deg,  "deg")
        _w_num("front_toe",  step5.front_toe_mm,     "mm")
        # Rear toe dispatch:
        #   - Acura: paired (`rear_toe`)
        #   - Porsche 992 GT3 R: paired (`rear_toe` -> `Chassis.Rear.TotalToeIn`) — W4.2
        #   - BMW M4 GT3 + Aston Vantage GT3: per-wheel (LeftRear.ToeIn / RightRear.ToeIn)
        #   - GTP BMW/Ferrari/Cadillac: per-wheel
        if is_acura or is_porsche_gt3:
            _w_num("rear_toe", step5.rear_toe_mm, "mm")
        else:
            _w_num("lr_toe",     step5.rear_toe_mm,      "mm")
            _w_num("rr_toe",     step5.rear_toe_mm,      "mm")

    # === Dampers ===
    if step6 is not None and not is_gt3:
        # GTP per-corner dampers (5 channels × 4 corners = 20 writes)
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
    elif step6 is not None and is_gt3:
        # === GT3 per-axle dampers (W4.1) ===
        # GT3 cars expose dampers per-axle (4 channels × 2 axles = 8 writes).
        # W3.2 already collapsed L/R adjustments to per-axle averages in
        # damper_solver, so step6.lf.* == step6.rf.* and step6.lr.* == step6.rr.*
        # for GT3. We use the LF/LR side as authoritative; no L/R divergence
        # is preserved on .sto write (the GT3 garage UI doesn't expose it).
        _w_num("front_ls_comp", step6.lf.ls_comp, "clicks")
        _w_num("front_hs_comp", step6.lf.hs_comp, "clicks")
        _w_num("front_ls_rbd",  step6.lf.ls_rbd,  "clicks")
        _w_num("front_hs_rbd",  step6.lf.hs_rbd,  "clicks")
        _w_num("rear_ls_comp",  step6.lr.ls_comp, "clicks")
        _w_num("rear_hs_comp",  step6.lr.hs_comp, "clicks")
        _w_num("rear_ls_rbd",   step6.lr.ls_rbd,  "clicks")
        _w_num("rear_hs_rbd",   step6.lr.hs_rbd,  "clicks")

    # === Roll dampers (Porsche / Acura — heave+roll architecture) ===
    # Per-axle gating: Porsche has FRONT roll damper but NO rear roll damper
    # (rear roll motion is implicit in per-corner shocks). Acura ARX-06 has
    # both. Writing CarSetup_Dampers_RearRoll_* for Porsche emits XML IDs
    # that don't exist in iRacing's Porsche garage schema — phantom output.
    # GT3 cars have NO roll dampers (per audit O23) — guard against canonical
    # name collision (e.g. if dispatch ever normalizes to "porsche").
    if has_roll_dampers and step6 is not None and not is_gt3:
        _has_front_roll = bool(getattr(getattr(_car, "damper", None), "has_front_roll_damper", False))
        _has_rear_roll = bool(getattr(getattr(_car, "damper", None), "has_rear_roll_damper", False))
        # Backward-compat: if has_roll_dampers is True but neither per-axle flag
        # is explicitly set, assume both (legacy Acura before per-axle flags were added).
        # Only apply when the car's DamperModel has has_roll_dampers=True — a car
        # missing all three flags gets neither (fail-safe).
        if not _has_front_roll and not _has_rear_roll:
            _legacy_roll = bool(getattr(getattr(_car, "damper", None), "has_roll_dampers", False))
            if _legacy_roll:
                _has_front_roll = True
                _has_rear_roll = True
        _roll_ls_f = getattr(step6, 'front_roll_ls', None)
        _roll_hs_f = getattr(step6, 'front_roll_hs', None)
        _roll_ls_r = getattr(step6, 'rear_roll_ls', None)
        _roll_hs_r = getattr(step6, 'rear_roll_hs', None)
        _roll_hs_slope_f = getattr(step6, 'front_roll_hs_slope', None)
        if _has_front_roll and _roll_ls_f is not None:
            _w_num("front_roll_ls", _roll_ls_f, "clicks")
            _w_num("front_roll_hs", _roll_hs_f, "clicks")
            if _roll_hs_slope_f is not None:
                _w_num("front_roll_hs_slope", _roll_hs_slope_f, "clicks")
        if _has_rear_roll and _roll_ls_r is not None:
            _w_num("rear_roll_ls",  _roll_ls_r,  "clicks")
            _w_num("rear_roll_hs",  _roll_hs_r,  "clicks")

    # === Rear 3rd damper (Porsche only) ===
    # GT3 has no 3rd damper (audit O24).
    if is_porsche and step6 is not None and not is_gt3:
        _3rd_ls = getattr(step6, 'rear_3rd_ls_comp', None)
        _3rd_hs = getattr(step6, 'rear_3rd_hs_comp', None)
        _3rd_ls_rbd = getattr(step6, 'rear_3rd_ls_rbd', None)
        _3rd_hs_rbd = getattr(step6, 'rear_3rd_hs_rbd', None)
        if _3rd_ls is not None:
            _w_num("rear_3rd_ls_comp", _3rd_ls, "clicks")
            _w_num("rear_3rd_hs_comp", _3rd_hs, "clicks")
            _w_num("rear_3rd_ls_rbd",  _3rd_ls_rbd, "clicks")
            _w_num("rear_3rd_hs_rbd",  _3rd_hs_rbd, "clicks")

    # === Tyres ===
    _w_num("lf_pressure", tyre_pressure_fl_kpa if tyre_pressure_fl_kpa is not None else tyre_pressure_kpa, "kPa")
    _w_num("rf_pressure", tyre_pressure_fr_kpa if tyre_pressure_fr_kpa is not None else tyre_pressure_kpa, "kPa")
    _w_num("lr_pressure", tyre_pressure_rl_kpa if tyre_pressure_rl_kpa is not None else tyre_pressure_kpa, "kPa")
    _w_num("rr_pressure", tyre_pressure_rr_kpa if tyre_pressure_rr_kpa is not None else tyre_pressure_kpa, "kPa")
    _w_str("tyre_type", "Dry")

    # === Fuel ===
    _w_num("fuel_level",       fuel_l,            "L")

    # === Brakes, Diff, TC — settable parameters ===
    _w_num("brake_bias",           brake_bias_pct,       "%")
    # Porsche uses separate coast/drive ramp XML fields; other cars use combined string.
    # (M5: IDs sourced from registry for coast; drive_ramp is Porsche-specific STO field.)
    if car_canonical == "porsche" and diff_coast_drive_ramp:
        parts = diff_coast_drive_ramp.replace(" ", "").split("/")
        if len(parts) == 2:
            _w_num("diff_coast_ramp", int(parts[0]), "deg")
            _w_num("diff_drive_ramp", int(parts[1]), "deg")
        else:
            _w_str("diff_coast_drive_ramp", diff_coast_drive_ramp)
    else:
        _w_str("diff_coast_drive_ramp", diff_coast_drive_ramp)
    _w_num("diff_clutch_plates",    diff_clutch_plates,   "")
    _w_num("diff_preload",          None if diff_preload_nm is None else int(round(diff_preload_nm)), "Nm")
    if is_gt3:
        # GT3: TC/ABS are indexed STRINGS with car-specific labels.  W4.2
        # extends the per-car suffix dispatch from W4.1's BMW-only path:
        #   - BMW M4 GT3:        "n (TC)"      / "n (ABS)"
        #   - Aston Vantage GT3: "n (TC SLIP)" / "n (ABS)"
        #   - Porsche 992 GT3 R: "n (TC-LAT)"  / "n (ABS)"
        # tc_slip is reused as the ABS index (single combined TC integer on
        # GT3 — the GTP TC slip channel doesn't exist).  Source values:
        # YAML samples in docs/gt3_session_info_*.yaml line up with this.
        if is_porsche_gt3:
            _tc_suffix = "TC-LAT"
        elif is_aston_gt3:
            _tc_suffix = "TC SLIP"
        else:  # is_bmw_gt3 (or future GT3 fallback)
            _tc_suffix = "TC"
        if tc_gain is not None:
            _w_str("tc_setting", f"{int(tc_gain)} ({_tc_suffix})")
        # ABS suffix is "(ABS)" for all three GT3 cars (verified in YAMLs).
        _abs_value = tc_slip if tc_slip is not None else tc_gain
        if _abs_value is not None:
            _w_str("abs_setting", f"{int(_abs_value)} (ABS)")
        # === Aston-only display fields ===
        # Defaults sourced from docs/gt3_session_info_aston_vantage_spielberg_2026-04-26.yaml:
        #   ThrottleResponse: "4 (RED)"  EpasSetting: "3 (PAS)"
        #   EnduranceLights:  "Not Fitted"   NightLedStripColor: "false"
        # TODO(W4.3): wire current_setup passthrough so driver-loaded values
        # take precedence over these statics.
        if is_aston_gt3:
            _w_str("throttle_response",   "4 (RED)")
            _w_str("epas_setting",        "3 (PAS)")
            _w_str("endurance_lights",    "Not Fitted")
            _w_str("night_led_strip_color", "false")
        # === Porsche 992-only display fields ===
        # Defaults sourced from docs/gt3_session_info_porsche_992_gt3r_spielberg_2026-04-26.yaml:
        #   ThrottleShapeSetting: 3   DashDisplayPage: "Race 2"
        #   NightLedStripColor:   "White"
        # TODO(W4.3): wire current_setup passthrough so driver-loaded values
        # take precedence over these statics.
        if is_porsche_gt3:
            _w_num("throttle_shape_setting", 3, "")
            _w_str("dash_display_page",      "Race 2")
            _w_str("night_led_strip_color",  "White")
            # Porsche emits WingSetting in BOTH chassis-rear AND aero-balance.
            # The dict only maps `wing_angle` to chassis-rear; emit the
            # aero-balance copy explicitly here. (Audit Open Question 1.)
            if wing is not None:
                _numeric(
                    details,
                    "CarSetup_TiresAero_AeroBalanceCalc_WingSetting",
                    wing,
                    "deg",
                )
        # Aston also mirrors RearWingAngle to TiresAero.AeroBalanceCalculator
        # (per audit comment on the wing_angle line).  The dict maps the
        # chassis-rear copy; emit the aero-balance copy explicitly.
        if is_aston_gt3 and wing is not None:
            _numeric(
                details,
                "CarSetup_TiresAero_AeroBalanceCalculator_RearWingAngle",
                wing,
                "deg",
            )
    else:
        _w_num("tc_gain", tc_gain, "")
        _w_num("tc_slip", tc_slip, "")
    _w_num("brake_bias_migration", brake_bias_migration,  "")
    _w_num("brake_bias_target",    brake_bias_target,     "")
    _w_num("brake_bias_migration_gain", brake_bias_migration_gain, "")
    _w_str("pad_compound",         pad_compound)
    _w_num("front_master_cyl",     front_master_cyl_mm,  "mm")
    _w_num("rear_master_cyl",      rear_master_cyl_mm,   "mm")
    _w_num("front_diff_preload",   None if front_diff_preload_nm is None else int(round(front_diff_preload_nm)), "Nm")
    _w_num("fuel_low_warning", fuel_low_warning_l, "L")
    _w_num("fuel_target", fuel_target_l, "L")
    _w_str("gear_stack", gear_stack)
    _w_str("roof_light_color", roof_light_color)
    _w_str("hybrid_rear_drive_enabled", hybrid_rear_drive_enabled)
    _w_num("hybrid_rear_drive_corner_pct", hybrid_rear_drive_corner_pct, "%")

    # === Computed / display-only brake & drive parameters ===
    # These may cause iRacing to reject the .sto if unexpected.
    if include_computed:
        _w_num("speed_in_first",   116 if speed_in_first_kph is None else speed_in_first_kph, "Km/h")
        _w_num("speed_in_second",  151 if speed_in_second_kph is None else speed_in_second_kph, "Km/h")
        _w_num("speed_in_third",   184 if speed_in_third_kph is None else speed_in_third_kph, "Km/h")
        _w_num("speed_in_fourth",  220 if speed_in_fourth_kph is None else speed_in_fourth_kph, "Km/h")
        _w_num("speed_in_fifth",   257 if speed_in_fifth_kph is None else speed_in_fifth_kph, "Km/h")
        _w_num("speed_in_sixth",   288 if speed_in_sixth_kph is None else speed_in_sixth_kph, "Km/h")
        _w_num("speed_in_seventh", 316 if speed_in_seventh_kph is None else speed_in_seventh_kph, "Km/h")

    # Write XML
    tree = ElementTree(root)
    indent(tree, space="  ")

    # Write with XML declaration
    with open(output_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n')
        tree.write(f, encoding="unicode", xml_declaration=False)

    return output_path
