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
    tyre_pressure_kpa: float = 152.0,
    # --- Defaults for fields not computed by the solver ---
    brake_bias_pct: float = 46.5,
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
) -> Path:
    """Write an iRacing .sto setup file from solver output.

    Maps solver parameters to BMW M Hybrid V8 CarSetup_* IDs.
    Other cars may need different ID mappings (Ferrari uses indexed values,
    Porsche has different damper naming, etc.).

    Args:
        car_name: Car display name
        track_name: Track display name
        wing: Wing angle in degrees
        fuel_l: Fuel load in liters
        step1-step6: Solver step results
        output_path: Path to write .sto file
        tyre_pressure_kpa: Starting tyre pressure (default 152 kPa minimum)
        brake_bias_pct: Brake pressure bias (default 46.5%)
        diff_coast_drive_ramp: Diff coast/drive ramp angles (default "40/65")
        diff_clutch_plates: Diff clutch friction plates (default 6)
        diff_preload_nm: Diff preload in Nm (default 10)
        tc_gain: Traction control gain (default 4)
        tc_slip: Traction control slip (default 3)
        gear_stack: Gear stack selection (default "Short")

    Returns:
        Path to the written file
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

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

    # === Aero ===
    _numeric(details, "CarSetup_TiresAero_AeroSettings_RearWingAngle", wing, "deg")
    _numeric(details, "CarSetup_TiresAero_AeroCalculator_FrontRhAtSpeed",
             round(step1.dynamic_front_rh_mm, 0), "mm")
    _numeric(details, "CarSetup_TiresAero_AeroCalculator_RearRhAtSpeed",
             round(step1.dynamic_rear_rh_mm, 0), "mm")
    _numeric(details, "CarSetup_TiresAero_AeroCalculator_DownforceBalance",
             round(step1.df_balance_pct, 1), "%")
    _numeric(details, "CarSetup_TiresAero_AeroCalculator_LD",
             round(step1.ld_ratio, 1), "")

    # === Ride Heights ===
    _numeric(details, "CarSetup_Chassis_LeftFront_RideHeight",
             round(step1.static_front_rh_mm, 0), "mm")
    _numeric(details, "CarSetup_Chassis_RightFront_RideHeight",
             round(step1.static_front_rh_mm, 0), "mm")
    _numeric(details, "CarSetup_Chassis_LeftRear_RideHeight",
             round(step1.static_rear_rh_mm, 1), "mm")
    _numeric(details, "CarSetup_Chassis_RightRear_RideHeight",
             round(step1.static_rear_rh_mm, 1), "mm")

    # === Pushrod offsets ===
    # Pushrods use 0.5mm increments in iRacing garage
    _numeric(details, "CarSetup_Chassis_Front_PushrodLengthOffset",
             round(step1.front_pushrod_offset_mm * 2) / 2, "mm")
    _numeric(details, "CarSetup_Chassis_Rear_PushrodLengthOffset",
             round(step1.rear_pushrod_offset_mm * 2) / 2, "mm")

    # === Heave / Third springs ===
    _numeric(details, "CarSetup_Chassis_Front_HeaveSpring",
             int(round(step2.front_heave_nmm)), "N/mm")
    _numeric(details, "CarSetup_Chassis_Front_HeavePerchOffset",
             int(round(step2.perch_offset_front_mm)), "mm")
    _numeric(details, "CarSetup_Chassis_Front_HeaveSliderDeflMax", 200, "mm")
    _numeric(details, "CarSetup_Chassis_Rear_ThirdSpring",
             int(round(step2.rear_third_nmm)), "N/mm")
    _numeric(details, "CarSetup_Chassis_Rear_ThirdPerchOffset",
             int(round(step2.perch_offset_rear_mm)), "mm")
    _numeric(details, "CarSetup_Chassis_Rear_ThirdSliderDeflMax", 150, "mm")

    # === Corner springs ===
    # Front: torsion bar (BMW)
    _numeric(details, "CarSetup_Chassis_LeftFront_TorsionBarOD",
             step3.front_torsion_od_mm, "mm")
    _numeric(details, "CarSetup_Chassis_RightFront_TorsionBarOD",
             step3.front_torsion_od_mm, "mm")
    # Torsion bar turns (derived from OD — iRacing computes this internally,
    # but including it makes the .sto more complete for reference)
    _numeric(details, "CarSetup_Chassis_LeftFront_TorsionBarTurns", 0.098, "Turns")
    _numeric(details, "CarSetup_Chassis_RightFront_TorsionBarTurns", 0.098, "Turns")
    # Rear: coil spring (BMW)
    _numeric(details, "CarSetup_Chassis_LeftRear_SpringRate",
             int(round(step3.rear_spring_rate_nmm)), "N/mm")
    _numeric(details, "CarSetup_Chassis_RightRear_SpringRate",
             int(round(step3.rear_spring_rate_nmm)), "N/mm")
    _numeric(details, "CarSetup_Chassis_LeftRear_SpringPerchOffset",
             round(step3.rear_spring_perch_mm, 1), "mm")
    _numeric(details, "CarSetup_Chassis_RightRear_SpringPerchOffset",
             round(step3.rear_spring_perch_mm, 1), "mm")
    # Shock deflection maxes (fixed per car geometry)
    _numeric(details, "CarSetup_Chassis_LeftFront_ShockDeflMax", 100, "mm")
    _numeric(details, "CarSetup_Chassis_RightFront_ShockDeflMax", 100, "mm")
    _numeric(details, "CarSetup_Chassis_LeftRear_ShockDeflMax", 150, "mm")
    _numeric(details, "CarSetup_Chassis_RightRear_ShockDeflMax", 150, "mm")

    # === ARBs ===
    _string(details, "CarSetup_Chassis_Front_ArbSize", step4.front_arb_size)
    _numeric(details, "CarSetup_Chassis_Front_ArbBlades", step4.front_arb_blade_start, "")
    _string(details, "CarSetup_Chassis_Rear_ArbSize", step4.rear_arb_size)
    _numeric(details, "CarSetup_Chassis_Rear_ArbBlades", step4.rear_arb_blade_start, "")

    # === Cross weight ===
    _numeric(details, "CarSetup_Chassis_Rear_CrossWeight", 50, "%")

    # === Wheel geometry ===
    _numeric(details, "CarSetup_Chassis_LeftFront_Camber",
             step5.front_camber_deg, "deg")
    _numeric(details, "CarSetup_Chassis_RightFront_Camber",
             step5.front_camber_deg, "deg")
    _numeric(details, "CarSetup_Chassis_LeftRear_Camber",
             step5.rear_camber_deg, "deg")
    _numeric(details, "CarSetup_Chassis_RightRear_Camber",
             step5.rear_camber_deg, "deg")
    _numeric(details, "CarSetup_Chassis_Front_ToeIn",
             step5.front_toe_mm, "mm")
    _numeric(details, "CarSetup_Chassis_LeftRear_ToeIn",
             step5.rear_toe_mm, "mm")
    _numeric(details, "CarSetup_Chassis_RightRear_ToeIn",
             step5.rear_toe_mm, "mm")

    # === Dampers ===
    # Left Front
    _numeric(details, "CarSetup_Chassis_LeftFront_LsCompDamping",
             step6.lf.ls_comp, "clicks")
    _numeric(details, "CarSetup_Chassis_LeftFront_LsRbdDamping",
             step6.lf.ls_rbd, "clicks")
    _numeric(details, "CarSetup_Chassis_LeftFront_HsCompDamping",
             step6.lf.hs_comp, "clicks")
    _numeric(details, "CarSetup_Chassis_LeftFront_HsRbdDamping",
             step6.lf.hs_rbd, "clicks")
    _numeric(details, "CarSetup_Chassis_LeftFront_HsCompDampSlope",
             step6.lf.hs_slope, "clicks")
    # Right Front
    _numeric(details, "CarSetup_Chassis_RightFront_LsCompDamping",
             step6.rf.ls_comp, "clicks")
    _numeric(details, "CarSetup_Chassis_RightFront_LsRbdDamping",
             step6.rf.ls_rbd, "clicks")
    _numeric(details, "CarSetup_Chassis_RightFront_HsCompDamping",
             step6.rf.hs_comp, "clicks")
    _numeric(details, "CarSetup_Chassis_RightFront_HsRbdDamping",
             step6.rf.hs_rbd, "clicks")
    _numeric(details, "CarSetup_Chassis_RightFront_HsCompDampSlope",
             step6.rf.hs_slope, "clicks")
    # Left Rear
    _numeric(details, "CarSetup_Chassis_LeftRear_LsCompDamping",
             step6.lr.ls_comp, "clicks")
    _numeric(details, "CarSetup_Chassis_LeftRear_LsRbdDamping",
             step6.lr.ls_rbd, "clicks")
    _numeric(details, "CarSetup_Chassis_LeftRear_HsCompDamping",
             step6.lr.hs_comp, "clicks")
    _numeric(details, "CarSetup_Chassis_LeftRear_HsRbdDamping",
             step6.lr.hs_rbd, "clicks")
    _numeric(details, "CarSetup_Chassis_LeftRear_HsCompDampSlope",
             step6.lr.hs_slope, "clicks")
    # Right Rear
    _numeric(details, "CarSetup_Chassis_RightRear_LsCompDamping",
             step6.rr.ls_comp, "clicks")
    _numeric(details, "CarSetup_Chassis_RightRear_LsRbdDamping",
             step6.rr.ls_rbd, "clicks")
    _numeric(details, "CarSetup_Chassis_RightRear_HsCompDamping",
             step6.rr.hs_comp, "clicks")
    _numeric(details, "CarSetup_Chassis_RightRear_HsRbdDamping",
             step6.rr.hs_rbd, "clicks")
    _numeric(details, "CarSetup_Chassis_RightRear_HsCompDampSlope",
             step6.rr.hs_slope, "clicks")

    # === Tyres ===
    _numeric(details, "CarSetup_TiresAero_LeftFront_StartingPressure",
             tyre_pressure_kpa, "kPa")
    _numeric(details, "CarSetup_TiresAero_RightFront_StartingPressure",
             tyre_pressure_kpa, "kPa")
    _numeric(details, "CarSetup_TiresAero_LeftRearTire_StartingPressure",
             tyre_pressure_kpa, "kPa")
    _numeric(details, "CarSetup_TiresAero_RightRearTire_StartingPressure",
             tyre_pressure_kpa, "kPa")
    _string(details, "CarSetup_TiresAero_TireType_TireType", "Dry")

    # === Brakes ===
    _numeric(details, "CarSetup_BrakesDriveUnit_BrakeSpec_BrakePressureBias",
             brake_bias_pct, "%")
    _numeric(details, "CarSetup_BrakesDriveUnit_BrakeSpec_BrakeBiasMigration",
             brake_bias_migration, "")
    _numeric(details, "CarSetup_BrakesDriveUnit_BrakeSpec_BrakeBiasTarget",
             brake_bias_target, "")
    _string(details, "CarSetup_BrakesDriveUnit_BrakeSpec_PadCompound",
            pad_compound)
    _numeric(details, "CarSetup_BrakesDriveUnit_BrakeSpec_FrontMasterCyl",
             front_master_cyl_mm, "mm")
    _numeric(details, "CarSetup_BrakesDriveUnit_BrakeSpec_RearMasterCyl",
             rear_master_cyl_mm, "mm")

    # === Rear Diff ===
    _string(details, "CarSetup_BrakesDriveUnit_RearDiffSpec_CoastDriveRampAngles",
            diff_coast_drive_ramp)
    _numeric(details, "CarSetup_BrakesDriveUnit_RearDiffSpec_ClutchFrictionPlates",
             diff_clutch_plates, "")
    _numeric(details, "CarSetup_BrakesDriveUnit_RearDiffSpec_Preload",
             diff_preload_nm, "Nm")

    # === Traction Control ===
    _numeric(details, "CarSetup_BrakesDriveUnit_TractionControl_TractionControlGain",
             tc_gain, "(TCLAT)")
    _numeric(details, "CarSetup_BrakesDriveUnit_TractionControl_TractionControlSlip",
             tc_slip, "(TCLON)")

    # === Fuel ===
    _numeric(details, "CarSetup_BrakesDriveUnit_Fuel_FuelLevel", fuel_l, "L")
    _numeric(details, "CarSetup_BrakesDriveUnit_Fuel_FuelLowWarning",
             fuel_low_warning_l, "L")

    # === Gears ===
    _string(details, "CarSetup_BrakesDriveUnit_GearRatios_GearStack", gear_stack)

    # === Lighting ===
    _string(details, "CarSetup_BrakesDriveUnit_Lighting_RoofIdLightColor",
            roof_light_color)

    # Write XML
    tree = ElementTree(root)
    indent(tree, space="  ")

    # Write with XML declaration
    with open(output_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n')
        tree.write(f, encoding="unicode", xml_declaration=False)

    return output_path
