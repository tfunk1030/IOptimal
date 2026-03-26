"""Canonical Setup Field Registry — single source of truth for setup parameters.

Defines every setup field once, with per-car specs for YAML paths, STO param IDs,
value ranges, and parse functions. Replaces the three divergent maps in
setup_reader.py, setup_schema.py, and setup_writer.py.

Usage:
    from car_model.setup_registry import FIELD_REGISTRY, get_car_spec, get_field
    field = get_field("front_heave_spring")
    spec = get_car_spec("bmw", "front_heave_spring")
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from decimal import Decimal, ROUND_HALF_UP
import re


@dataclass(frozen=True)
class FieldDefinition:
    """Canonical definition for a single setup parameter."""
    canonical_key: str          # "front_heave_spring"
    kind: str                   # "settable" | "computed" | "context"
    solver_step: int | str | None  # 1-6, "supporting", None
    unit: str                   # "N/mm", "mm", "deg", "clicks"
    value_type: str             # "continuous" | "discrete" | "indexed" | "string"
    symmetric: bool             # True = L/R averaged or identical
    per_corner: bool            # True = LF/RF/LR/RR separate
    current_setup_attr: str     # attribute name on CurrentSetup (or "" if N/A)
    formula_note: str = ""      # how computed fields derive from settable ones
    telemetry_channel: str | None = None


@dataclass(frozen=True)
class CarFieldSpec:
    """Per-car specification for a single field."""
    yaml_path: str              # "Chassis.Front.HeaveSpring"
    sto_param_id: str           # "CarSetup_Chassis_Front_HeaveSpring"
    range_min: float | None = None
    range_max: float | None = None
    resolution: float | None = None
    options: tuple | None = None       # discrete choices (frozen)
    index_map: dict | None = None      # Ferrari index→value decode (None = undecoded)
    parse_fn: str = "float"            # "float" | "int" | "string" | "defl"


DEFAULT_DIFF_RAMP_OPTIONS: tuple[tuple[int, int], ...] = (
    (40, 65),
    (45, 70),
    (50, 75),
)


# ─────────────────────────────────────────────────────────────────────────────
# Field Registry — ~90 canonical field definitions
# ─────────────────────────────────────────────────────────────────────────────

_FIELD_DEFS: list[FieldDefinition] = [
    # ── Step 1: Rake / Ride Heights ──
    FieldDefinition("wing_angle_deg", "settable", 1, "deg", "continuous", True, False, "wing_angle_deg"),
    FieldDefinition("front_pushrod_offset_mm", "settable", 1, "mm", "continuous", True, False, "front_pushrod_mm"),
    FieldDefinition("rear_pushrod_offset_mm", "settable", 1, "mm", "continuous", True, False, "rear_pushrod_mm"),
    FieldDefinition("static_front_rh_mm", "computed", 1, "mm", "continuous", True, False, "static_front_rh_mm",
                    formula_note="f(springs, pushrod, camber, fuel)"),
    FieldDefinition("static_rear_rh_mm", "computed", 1, "mm", "continuous", True, False, "static_rear_rh_mm",
                    formula_note="f(pushrod, third, rear_spring, heave_perch)"),
    FieldDefinition("front_rh_at_speed_mm", "computed", 1, "mm", "continuous", True, False, "front_rh_at_speed_mm",
                    formula_note="iRacing aero calculator"),
    FieldDefinition("rear_rh_at_speed_mm", "computed", 1, "mm", "continuous", True, False, "rear_rh_at_speed_mm",
                    formula_note="iRacing aero calculator"),
    FieldDefinition("df_balance_pct", "computed", 1, "%", "continuous", True, False, "df_balance_pct",
                    formula_note="iRacing aero calculator"),
    FieldDefinition("ld_ratio", "computed", 1, "", "continuous", True, False, "ld_ratio",
                    formula_note="iRacing aero calculator"),

    # ── Step 2: Heave / Third Springs ──
    FieldDefinition("front_heave_spring_nmm", "settable", 2, "N/mm", "continuous", True, False, "front_heave_nmm"),
    FieldDefinition("front_heave_perch_mm", "settable", 2, "mm", "continuous", True, False, "front_heave_perch_mm"),
    FieldDefinition("rear_third_spring_nmm", "settable", 2, "N/mm", "continuous", True, False, "rear_third_nmm"),
    FieldDefinition("rear_third_perch_mm", "settable", 2, "mm", "continuous", True, False, "rear_third_perch_mm"),
    FieldDefinition("heave_spring_defl_static_mm", "computed", 2, "mm", "continuous", True, False, "heave_spring_defl_static_mm",
                    formula_note="iRacing deflection display"),
    FieldDefinition("heave_spring_defl_max_mm", "computed", 2, "mm", "continuous", True, False, "heave_spring_defl_max_mm",
                    formula_note="iRacing deflection display"),
    FieldDefinition("heave_slider_defl_static_mm", "computed", 2, "mm", "continuous", True, False, "heave_slider_defl_static_mm",
                    formula_note="iRacing deflection display"),
    FieldDefinition("heave_slider_defl_max_mm", "computed", 2, "mm", "continuous", True, False, "heave_slider_defl_max_mm",
                    formula_note="iRacing deflection display"),
    FieldDefinition("third_spring_defl_static_mm", "computed", 2, "mm", "continuous", True, False, "third_spring_defl_static_mm",
                    formula_note="iRacing deflection display"),
    FieldDefinition("third_spring_defl_max_mm", "computed", 2, "mm", "continuous", True, False, "third_spring_defl_max_mm",
                    formula_note="iRacing deflection display"),
    FieldDefinition("third_slider_defl_static_mm", "computed", 2, "mm", "continuous", True, False, "third_slider_defl_static_mm",
                    formula_note="iRacing deflection display"),
    FieldDefinition("third_slider_defl_max_mm", "computed", 2, "mm", "continuous", True, False, "third_slider_defl_max_mm",
                    formula_note="iRacing deflection display"),

    # ── Step 3: Corner Springs ──
    FieldDefinition("front_torsion_od_mm", "settable", 3, "mm", "discrete", True, False, "front_torsion_od_mm"),
    FieldDefinition("rear_spring_rate_nmm", "settable", 3, "N/mm", "continuous", True, False, "rear_spring_nmm"),
    FieldDefinition("rear_spring_perch_mm", "settable", 3, "mm", "continuous", True, False, "rear_spring_perch_mm"),
    FieldDefinition("torsion_bar_turns", "computed", 3, "turns", "continuous", True, False, "torsion_bar_turns",
                    formula_note="iRacing display f(heave, perch, OD, front_RH)"),
    FieldDefinition("torsion_bar_defl_mm", "computed", 3, "mm", "continuous", True, False, "torsion_bar_defl_mm",
                    formula_note="iRacing deflection display"),
    FieldDefinition("rear_spring_defl_static_mm", "computed", 3, "mm", "continuous", True, False, "rear_spring_defl_static_mm",
                    formula_note="iRacing deflection display"),
    FieldDefinition("rear_spring_defl_max_mm", "computed", 3, "mm", "continuous", True, False, "rear_spring_defl_max_mm",
                    formula_note="iRacing deflection display"),

    # ── Step 4: ARBs ──
    FieldDefinition("front_arb_size", "settable", 4, "", "string", True, False, "front_arb_size"),
    FieldDefinition("front_arb_blade", "settable", 4, "clicks", "discrete", True, False, "front_arb_blade"),
    FieldDefinition("rear_arb_size", "settable", 4, "", "string", True, False, "rear_arb_size"),
    FieldDefinition("rear_arb_blade", "settable", 4, "clicks", "discrete", True, False, "rear_arb_blade"),

    # ── Step 5: Wheel Geometry ──
    FieldDefinition("front_camber_deg", "settable", 5, "deg", "continuous", True, False, "front_camber_deg"),
    FieldDefinition("rear_camber_deg", "settable", 5, "deg", "continuous", True, False, "rear_camber_deg"),
    FieldDefinition("front_toe_mm", "settable", 5, "mm", "continuous", True, False, "front_toe_mm"),
    FieldDefinition("rear_toe_mm", "settable", 5, "mm", "continuous", True, False, "rear_toe_mm"),

    # ── Step 6: Dampers (per-corner, L/R symmetric) ──
    FieldDefinition("front_ls_comp", "settable", 6, "clicks", "discrete", True, True, "front_ls_comp"),
    FieldDefinition("front_ls_rbd", "settable", 6, "clicks", "discrete", True, True, "front_ls_rbd"),
    FieldDefinition("front_hs_comp", "settable", 6, "clicks", "discrete", True, True, "front_hs_comp"),
    FieldDefinition("front_hs_rbd", "settable", 6, "clicks", "discrete", True, True, "front_hs_rbd"),
    FieldDefinition("front_hs_slope", "settable", 6, "clicks", "discrete", True, True, "front_hs_slope"),
    FieldDefinition("rear_ls_comp", "settable", 6, "clicks", "discrete", True, True, "rear_ls_comp"),
    FieldDefinition("rear_ls_rbd", "settable", 6, "clicks", "discrete", True, True, "rear_ls_rbd"),
    FieldDefinition("rear_hs_comp", "settable", 6, "clicks", "discrete", True, True, "rear_hs_comp"),
    FieldDefinition("rear_hs_rbd", "settable", 6, "clicks", "discrete", True, True, "rear_hs_rbd"),
    FieldDefinition("rear_hs_slope", "settable", 6, "clicks", "discrete", True, True, "rear_hs_slope"),

    # ── Supporting Parameters ──
    FieldDefinition("brake_bias_pct", "settable", "supporting", "%", "continuous", True, False, "brake_bias_pct"),
    FieldDefinition("brake_bias_target", "settable", "supporting", "", "continuous", True, False, "brake_bias_target"),
    FieldDefinition("brake_bias_migration", "settable", "supporting", "", "continuous", True, False, "brake_bias_migration"),
    FieldDefinition("brake_bias_migration_gain", "settable", "supporting", "", "continuous", True, False, "brake_bias_migration_gain"),
    FieldDefinition("front_master_cyl_mm", "settable", "supporting", "mm", "continuous", True, False, "front_master_cyl_mm"),
    FieldDefinition("rear_master_cyl_mm", "settable", "supporting", "mm", "continuous", True, False, "rear_master_cyl_mm"),
    FieldDefinition("pad_compound", "settable", "supporting", "", "string", True, False, "pad_compound"),
    FieldDefinition("diff_preload_nm", "settable", "supporting", "Nm", "continuous", True, False, "diff_preload_nm"),
    FieldDefinition("diff_ramp_angles", "settable", "supporting", "", "string", True, False, "diff_ramp_angles"),
    FieldDefinition("diff_ramp_option_idx", "settable", "supporting", "idx", "indexed", True, False, "diff_ramp_angles"),
    FieldDefinition("diff_clutch_plates", "settable", "supporting", "", "discrete", True, False, "diff_clutch_plates"),
    FieldDefinition("front_diff_preload_nm", "settable", "supporting", "Nm", "continuous", True, False, "front_diff_preload_nm"),
    FieldDefinition("tc_gain", "settable", "supporting", "clicks", "discrete", True, False, "tc_gain"),
    FieldDefinition("tc_slip", "settable", "supporting", "clicks", "discrete", True, False, "tc_slip"),
    FieldDefinition("fuel_l", "settable", "supporting", "L", "continuous", True, False, "fuel_l"),
    FieldDefinition("fuel_low_warning_l", "settable", "supporting", "L", "continuous", True, False, "fuel_low_warning_l"),
    FieldDefinition("fuel_target_l", "settable", "supporting", "L", "continuous", True, False, "fuel_target_l"),
    FieldDefinition("gear_stack", "settable", "supporting", "", "string", True, False, "gear_stack"),
    FieldDefinition("hybrid_rear_drive_enabled", "settable", "supporting", "", "string", True, False, "hybrid_rear_drive_enabled"),
    FieldDefinition("hybrid_rear_drive_corner_pct", "settable", "supporting", "%", "continuous", True, False, "hybrid_rear_drive_corner_pct"),
    FieldDefinition("roof_light_color", "context", None, "", "string", True, False, "roof_light_color"),

    # ── Computed Display Values (iRacing-generated) ──
    FieldDefinition("lf_corner_weight_n", "computed", None, "N", "continuous", False, True, "lf_corner_weight_n",
                    formula_note="iRacing physics"),
    FieldDefinition("rf_corner_weight_n", "computed", None, "N", "continuous", False, True, "rf_corner_weight_n",
                    formula_note="iRacing physics"),
    FieldDefinition("lr_corner_weight_n", "computed", None, "N", "continuous", False, True, "lr_corner_weight_n",
                    formula_note="iRacing physics"),
    FieldDefinition("rr_corner_weight_n", "computed", None, "N", "continuous", False, True, "rr_corner_weight_n",
                    formula_note="iRacing physics"),
    FieldDefinition("front_shock_defl_static_mm", "computed", None, "mm", "continuous", True, False, "front_shock_defl_static_mm",
                    formula_note="iRacing deflection display"),
    FieldDefinition("front_shock_defl_max_mm", "computed", None, "mm", "continuous", True, False, "front_shock_defl_max_mm",
                    formula_note="iRacing deflection display"),
    FieldDefinition("rear_shock_defl_static_mm", "computed", None, "mm", "continuous", True, False, "rear_shock_defl_static_mm",
                    formula_note="iRacing deflection display"),
    FieldDefinition("rear_shock_defl_max_mm", "computed", None, "mm", "continuous", True, False, "rear_shock_defl_max_mm",
                    formula_note="iRacing deflection display"),
    FieldDefinition("rear_torsion_bar_turns", "computed", None, "turns", "continuous", True, False, "rear_torsion_bar_turns",
                    formula_note="iRacing display (Ferrari only)"),
    FieldDefinition("rear_torsion_bar_defl_mm", "computed", None, "mm", "continuous", True, False, "rear_torsion_bar_defl_mm",
                    formula_note="iRacing deflection display (Ferrari only)"),

    # ── Per-corner ride heights (computed) ──
    FieldDefinition("lf_ride_height_mm", "computed", 1, "mm", "continuous", False, True, "",
                    formula_note="iRacing physics"),
    FieldDefinition("rf_ride_height_mm", "computed", 1, "mm", "continuous", False, True, "",
                    formula_note="iRacing physics"),
    FieldDefinition("lr_ride_height_mm", "computed", 1, "mm", "continuous", False, True, "",
                    formula_note="iRacing physics"),
    FieldDefinition("rr_ride_height_mm", "computed", 1, "mm", "continuous", False, True, "",
                    formula_note="iRacing physics"),

    # ── Gear speeds (computed display) ──
    FieldDefinition("speed_in_first_kph", "computed", None, "km/h", "continuous", True, False, "speed_in_first_kph"),
    FieldDefinition("speed_in_second_kph", "computed", None, "km/h", "continuous", True, False, "speed_in_second_kph"),
    FieldDefinition("speed_in_third_kph", "computed", None, "km/h", "continuous", True, False, "speed_in_third_kph"),
    FieldDefinition("speed_in_fourth_kph", "computed", None, "km/h", "continuous", True, False, "speed_in_fourth_kph"),
    FieldDefinition("speed_in_fifth_kph", "computed", None, "km/h", "continuous", True, False, "speed_in_fifth_kph"),
    FieldDefinition("speed_in_sixth_kph", "computed", None, "km/h", "continuous", True, False, "speed_in_sixth_kph"),
    FieldDefinition("speed_in_seventh_kph", "computed", None, "km/h", "continuous", True, False, "speed_in_seventh_kph"),
]

FIELD_REGISTRY: dict[str, FieldDefinition] = {f.canonical_key: f for f in _FIELD_DEFS}


# ─────────────────────────────────────────────────────────────────────────────
# Per-car field specs
# ─────────────────────────────────────────────────────────────────────────────

def _S(yaml_path: str, sto_param_id: str, **kwargs) -> CarFieldSpec:
    """Shorthand constructor for CarFieldSpec."""
    return CarFieldSpec(yaml_path=yaml_path, sto_param_id=sto_param_id, **kwargs)


# BMW M Hybrid V8 LMDh
_BMW_SPECS: dict[str, CarFieldSpec] = {
    # Step 1: Rake
    "wing_angle_deg":           _S("TiresAero.AeroSettings.RearWingAngle",         "CarSetup_TiresAero_AeroSettings_RearWingAngle"),
    "front_pushrod_offset_mm":  _S("Chassis.Front.PushrodLengthOffset",             "CarSetup_Chassis_Front_PushrodLengthOffset",        range_min=-40.0, range_max=40.0, resolution=0.5),
    "rear_pushrod_offset_mm":   _S("Chassis.Rear.PushrodLengthOffset",              "CarSetup_Chassis_Rear_PushrodLengthOffset",         range_min=-40.0, range_max=40.0, resolution=0.5),
    "front_rh_at_speed_mm":     _S("TiresAero.AeroCalculator.FrontRhAtSpeed",       "CarSetup_TiresAero_AeroCalculator_FrontRhAtSpeed"),
    "rear_rh_at_speed_mm":      _S("TiresAero.AeroCalculator.RearRhAtSpeed",        "CarSetup_TiresAero_AeroCalculator_RearRhAtSpeed"),
    "df_balance_pct":           _S("TiresAero.AeroCalculator.DownforceBalance",      "CarSetup_TiresAero_AeroCalculator_DownforceBalance"),
    "ld_ratio":                 _S("TiresAero.AeroCalculator.LD",                    "CarSetup_TiresAero_AeroCalculator_LD"),
    "lf_ride_height_mm":        _S("Chassis.LeftFront.RideHeight",                   "CarSetup_Chassis_LeftFront_RideHeight"),
    "rf_ride_height_mm":        _S("Chassis.RightFront.RideHeight",                  "CarSetup_Chassis_RightFront_RideHeight"),
    "lr_ride_height_mm":        _S("Chassis.LeftRear.RideHeight",                    "CarSetup_Chassis_LeftRear_RideHeight"),
    "rr_ride_height_mm":        _S("Chassis.RightRear.RideHeight",                   "CarSetup_Chassis_RightRear_RideHeight"),
    # Step 2: Heave / Third
    "front_heave_spring_nmm":   _S("Chassis.Front.HeaveSpring",                      "CarSetup_Chassis_Front_HeaveSpring",                range_min=0.0, range_max=900.0, resolution=10.0),
    "front_heave_perch_mm":     _S("Chassis.Front.HeavePerchOffset",                 "CarSetup_Chassis_Front_HeavePerchOffset",           resolution=0.5),
    "rear_third_spring_nmm":    _S("Chassis.Rear.ThirdSpring",                       "CarSetup_Chassis_Rear_ThirdSpring",                 range_min=0.0, range_max=900.0, resolution=10.0),
    "rear_third_perch_mm":      _S("Chassis.Rear.ThirdPerchOffset",                  "CarSetup_Chassis_Rear_ThirdPerchOffset",            resolution=1.0),
    "heave_spring_defl_static_mm": _S("Chassis.Front.HeaveSpringDefl",               "CarSetup_Chassis_Front_HeaveSpringDeflStatic",      parse_fn="defl"),
    "heave_spring_defl_max_mm": _S("Chassis.Front.HeaveSpringDefl",                  "CarSetup_Chassis_Front_HeaveSpringDeflMax",         parse_fn="defl"),
    "heave_slider_defl_static_mm": _S("Chassis.Front.HeaveSliderDefl",               "CarSetup_Chassis_Front_HeaveSliderDeflStatic",      parse_fn="defl"),
    "heave_slider_defl_max_mm": _S("Chassis.Front.HeaveSliderDefl",                  "CarSetup_Chassis_Front_HeaveSliderDeflMax",         parse_fn="defl"),
    "third_spring_defl_static_mm": _S("Chassis.Rear.ThirdSpringDefl",                "CarSetup_Chassis_Rear_ThirdSpringDeflStatic",       parse_fn="defl"),
    "third_spring_defl_max_mm": _S("Chassis.Rear.ThirdSpringDefl",                   "CarSetup_Chassis_Rear_ThirdSpringDeflMax",          parse_fn="defl"),
    "third_slider_defl_static_mm": _S("Chassis.Rear.ThirdSliderDefl",                "CarSetup_Chassis_Rear_ThirdSliderDeflStatic",       parse_fn="defl"),
    "third_slider_defl_max_mm": _S("Chassis.Rear.ThirdSliderDefl",                   "CarSetup_Chassis_Rear_ThirdSliderDeflMax",          parse_fn="defl"),
    # Step 3: Corner Springs
    "front_torsion_od_mm":      _S("Chassis.LeftFront.TorsionBarOD",                 "CarSetup_Chassis_LeftFront_TorsionBarOD",           range_min=13.9, range_max=18.2),
    "rear_spring_rate_nmm":     _S("Chassis.LeftRear.SpringRate",                    "CarSetup_Chassis_LeftRear_SpringRate",              range_min=100.0, range_max=300.0, resolution=5.0),
    "rear_spring_perch_mm":     _S("Chassis.LeftRear.SpringPerchOffset",             "CarSetup_Chassis_LeftRear_SpringPerchOffset",       resolution=0.5),
    "torsion_bar_turns":        _S("Chassis.LeftFront.TorsionBarTurns",              "CarSetup_Chassis_LeftFront_TorsionBarTurns"),
    "torsion_bar_defl_mm":      _S("Chassis.LeftFront.TorsionBarDefl",               "CarSetup_Chassis_LeftFront_TorsionBarDefl",         parse_fn="defl"),
    "rear_spring_defl_static_mm": _S("Chassis.LeftRear.SpringDefl",                  "CarSetup_Chassis_LeftRear_SpringDeflStatic",        parse_fn="defl"),
    "rear_spring_defl_max_mm":  _S("Chassis.LeftRear.SpringDefl",                    "CarSetup_Chassis_LeftRear_SpringDeflMax",           parse_fn="defl"),
    # Step 4: ARBs
    "front_arb_size":           _S("Chassis.Front.ArbSize",                          "CarSetup_Chassis_Front_ArbSize",                    parse_fn="string"),
    "front_arb_blade":          _S("Chassis.Front.ArbBlades",                        "CarSetup_Chassis_Front_ArbBlades",                  range_min=1, range_max=5, parse_fn="int"),
    "rear_arb_size":            _S("Chassis.Rear.ArbSize",                           "CarSetup_Chassis_Rear_ArbSize",                     parse_fn="string"),
    "rear_arb_blade":           _S("Chassis.Rear.ArbBlades",                         "CarSetup_Chassis_Rear_ArbBlades",                   range_min=1, range_max=5, parse_fn="int"),
    # Step 5: Geometry
    "front_camber_deg":         _S("Chassis.LeftFront.Camber",                       "CarSetup_Chassis_LeftFront_Camber",                 range_min=-2.9, range_max=0.0, resolution=0.1),
    "rear_camber_deg":          _S("Chassis.LeftRear.Camber",                        "CarSetup_Chassis_LeftRear_Camber",                  range_min=-1.9, range_max=0.0, resolution=0.1),
    "front_toe_mm":             _S("Chassis.Front.ToeIn",                            "CarSetup_Chassis_Front_ToeIn",                      range_min=-3.0, range_max=3.0, resolution=0.1),
    "rear_toe_mm":              _S("Chassis.LeftRear.ToeIn",                         "CarSetup_Chassis_LeftRear_ToeIn",                   range_min=-2.0, range_max=3.0, resolution=0.1),
    # Step 6: Dampers
    "front_ls_comp":            _S("Chassis.LeftFront.LsCompDamping",                "CarSetup_Chassis_LeftFront_LsCompDamping",          range_min=0, range_max=11, parse_fn="int"),
    "front_ls_rbd":             _S("Chassis.LeftFront.LsRbdDamping",                 "CarSetup_Chassis_LeftFront_LsRbdDamping",           range_min=0, range_max=11, parse_fn="int"),
    "front_hs_comp":            _S("Chassis.LeftFront.HsCompDamping",                "CarSetup_Chassis_LeftFront_HsCompDamping",          range_min=0, range_max=11, parse_fn="int"),
    "front_hs_rbd":             _S("Chassis.LeftFront.HsRbdDamping",                 "CarSetup_Chassis_LeftFront_HsRbdDamping",           range_min=0, range_max=11, parse_fn="int"),
    "front_hs_slope":           _S("Chassis.LeftFront.HsCompDampSlope",              "CarSetup_Chassis_LeftFront_HsCompDampSlope",        range_min=0, range_max=11, parse_fn="int"),
    "rear_ls_comp":             _S("Chassis.LeftRear.LsCompDamping",                 "CarSetup_Chassis_LeftRear_LsCompDamping",           range_min=0, range_max=11, parse_fn="int"),
    "rear_ls_rbd":              _S("Chassis.LeftRear.LsRbdDamping",                  "CarSetup_Chassis_LeftRear_LsRbdDamping",            range_min=0, range_max=11, parse_fn="int"),
    "rear_hs_comp":             _S("Chassis.LeftRear.HsCompDamping",                 "CarSetup_Chassis_LeftRear_HsCompDamping",           range_min=0, range_max=11, parse_fn="int"),
    "rear_hs_rbd":              _S("Chassis.LeftRear.HsRbdDamping",                  "CarSetup_Chassis_LeftRear_HsRbdDamping",            range_min=0, range_max=11, parse_fn="int"),
    "rear_hs_slope":            _S("Chassis.LeftRear.HsCompDampSlope",               "CarSetup_Chassis_LeftRear_HsCompDampSlope",         range_min=0, range_max=11, parse_fn="int"),
    # Supporting: Brakes
    "brake_bias_pct":           _S("BrakesDriveUnit.BrakeSpec.BrakePressureBias",     "CarSetup_BrakesDriveUnit_BrakeSpec_BrakePressureBias"),
    "brake_bias_target":        _S("BrakesDriveUnit.BrakeSpec.BrakeBiasTarget",       "CarSetup_BrakesDriveUnit_BrakeSpec_BrakeBiasTarget", range_min=-5.0, range_max=5.0, resolution=1.0),
    "brake_bias_migration":     _S("BrakesDriveUnit.BrakeSpec.BrakeBiasMigration",    "CarSetup_BrakesDriveUnit_BrakeSpec_BrakeBiasMigration", range_min=-5.0, range_max=5.0, resolution=1.0),
    "brake_bias_migration_gain": _S("BrakesDriveUnit.BrakeSpec.BiasMigrationGain",    "CarSetup_BrakesDriveUnit_BrakeSpec_BiasMigrationGain"),
    "front_master_cyl_mm":      _S("BrakesDriveUnit.BrakeSpec.FrontMasterCyl",        "CarSetup_BrakesDriveUnit_BrakeSpec_FrontMasterCyl"),
    "rear_master_cyl_mm":       _S("BrakesDriveUnit.BrakeSpec.RearMasterCyl",         "CarSetup_BrakesDriveUnit_BrakeSpec_RearMasterCyl"),
    "pad_compound":             _S("BrakesDriveUnit.BrakeSpec.PadCompound",           "CarSetup_BrakesDriveUnit_BrakeSpec_PadCompound",   parse_fn="string"),
    # Supporting: Diff
    "diff_preload_nm":          _S("BrakesDriveUnit.RearDiffSpec.Preload",            "CarSetup_BrakesDriveUnit_RearDiffSpec_Preload",    range_min=0.0, range_max=150.0, resolution=5.0),
    "diff_ramp_angles":         _S("BrakesDriveUnit.RearDiffSpec.CoastDriveRampAngles", "CarSetup_BrakesDriveUnit_RearDiffSpec_CoastDriveRampAngles", parse_fn="string"),
    "diff_ramp_option_idx":     _S("BrakesDriveUnit.RearDiffSpec.CoastDriveRampAngles", "CarSetup_BrakesDriveUnit_RearDiffSpec_CoastDriveRampAngles", range_min=0.0, range_max=2.0, resolution=1.0, options=(0, 1, 2), parse_fn="string"),
    "diff_clutch_plates":       _S("BrakesDriveUnit.RearDiffSpec.ClutchFrictionPlates", "CarSetup_BrakesDriveUnit_RearDiffSpec_ClutchFrictionPlates", parse_fn="int"),
    # Supporting: TC
    "tc_gain":                  _S("BrakesDriveUnit.TractionControl.TractionControlGain", "CarSetup_BrakesDriveUnit_TractionControl_TractionControlGain", range_min=1, range_max=10, parse_fn="int"),
    "tc_slip":                  _S("BrakesDriveUnit.TractionControl.TractionControlSlip", "CarSetup_BrakesDriveUnit_TractionControl_TractionControlSlip", range_min=1, range_max=10, parse_fn="int"),
    # Supporting: Fuel
    "fuel_l":                   _S("BrakesDriveUnit.Fuel.FuelLevel",                  "CarSetup_BrakesDriveUnit_Fuel_FuelLevel"),
    "fuel_low_warning_l":       _S("BrakesDriveUnit.Fuel.FuelLowWarning",             "CarSetup_BrakesDriveUnit_Fuel_FuelLowWarning"),
    "fuel_target_l":            _S("BrakesDriveUnit.Fuel.FuelTarget",                 "CarSetup_BrakesDriveUnit_Fuel_FuelTarget"),
    # Supporting: Gears
    "gear_stack":               _S("BrakesDriveUnit.GearRatios.GearStack",            "CarSetup_BrakesDriveUnit_GearRatios_GearStack",    parse_fn="string"),
    "speed_in_first_kph":       _S("BrakesDriveUnit.GearRatios.SpeedInFirst",         "CarSetup_BrakesDriveUnit_GearRatios_SpeedInFirst"),
    "speed_in_second_kph":      _S("BrakesDriveUnit.GearRatios.SpeedInSecond",        "CarSetup_BrakesDriveUnit_GearRatios_SpeedInSecond"),
    "speed_in_third_kph":       _S("BrakesDriveUnit.GearRatios.SpeedInThird",         "CarSetup_BrakesDriveUnit_GearRatios_SpeedInThird"),
    "speed_in_fourth_kph":      _S("BrakesDriveUnit.GearRatios.SpeedInFourth",        "CarSetup_BrakesDriveUnit_GearRatios_SpeedInFourth"),
    "speed_in_fifth_kph":       _S("BrakesDriveUnit.GearRatios.SpeedInFifth",         "CarSetup_BrakesDriveUnit_GearRatios_SpeedInFifth"),
    "speed_in_sixth_kph":       _S("BrakesDriveUnit.GearRatios.SpeedInSixth",         "CarSetup_BrakesDriveUnit_GearRatios_SpeedInSixth"),
    "speed_in_seventh_kph":     _S("BrakesDriveUnit.GearRatios.SpeedInSeventh",       "CarSetup_BrakesDriveUnit_GearRatios_SpeedInSeventh"),
    # Supporting: Hybrid
    "hybrid_rear_drive_enabled": _S("BrakesDriveUnit.HybridConfig.HybridRearDriveEnabled", "CarSetup_BrakesDriveUnit_HybridConfig_HybridRearDriveEnabled", parse_fn="string"),
    "hybrid_rear_drive_corner_pct": _S("BrakesDriveUnit.HybridConfig.HybridRearDriveCornerPct", "CarSetup_BrakesDriveUnit_HybridConfig_HybridRearDriveCornerPct"),
    # Context
    "roof_light_color":         _S("BrakesDriveUnit.Lighting.RoofIdLightColor",       "CarSetup_BrakesDriveUnit_Lighting_RoofIdLightColor", parse_fn="string"),
    # Computed: Corner weights
    "lf_corner_weight_n":       _S("Chassis.LeftFront.CornerWeight",                  "CarSetup_Chassis_LeftFront_CornerWeight"),
    "rf_corner_weight_n":       _S("Chassis.RightFront.CornerWeight",                 "CarSetup_Chassis_RightFront_CornerWeight"),
    "lr_corner_weight_n":       _S("Chassis.LeftRear.CornerWeight",                   "CarSetup_Chassis_LeftRear_CornerWeight"),
    "rr_corner_weight_n":       _S("Chassis.RightRear.CornerWeight",                  "CarSetup_Chassis_RightRear_CornerWeight"),
    # Computed: Shock deflections
    "front_shock_defl_static_mm": _S("Chassis.LeftFront.ShockDefl",                   "CarSetup_Chassis_LeftFront_ShockDeflStatic",       parse_fn="defl"),
    "front_shock_defl_max_mm":  _S("Chassis.LeftFront.ShockDefl",                     "CarSetup_Chassis_LeftFront_ShockDeflMax",          parse_fn="defl"),
    "rear_shock_defl_static_mm": _S("Chassis.LeftRear.ShockDefl",                     "CarSetup_Chassis_LeftRear_ShockDeflStatic",        parse_fn="defl"),
    "rear_shock_defl_max_mm":   _S("Chassis.LeftRear.ShockDefl",                      "CarSetup_Chassis_LeftRear_ShockDeflMax",           parse_fn="defl"),
}

# Ferrari 499P — overrides from BMW for different YAML paths and STO IDs
_FERRARI_SPECS: dict[str, CarFieldSpec] = {
    **{k: v for k, v in _BMW_SPECS.items()},  # Start from BMW as base
}
# Override Ferrari-specific paths
_FERRARI_SPECS.update({
    "front_pushrod_offset_mm":  _S("Chassis.Front.PushrodLengthDelta",               "CarSetup_Chassis_Front_PushrodLengthDelta",         range_min=-40.0, range_max=40.0, resolution=0.5),
    "rear_pushrod_offset_mm":   _S("Chassis.Rear.PushrodLengthDelta",                "CarSetup_Chassis_Rear_PushrodLengthDelta",          range_min=-40.0, range_max=40.0, resolution=0.5),
    "rear_third_spring_nmm":    _S("Chassis.Rear.HeaveSpring",                       "CarSetup_Chassis_Rear_HeaveSpring",                 range_min=0.0, range_max=900.0, resolution=10.0),
    "rear_third_perch_mm":      _S("Chassis.Rear.HeavePerchOffset",                  "CarSetup_Chassis_Rear_HeavePerchOffset",            resolution=1.0),
    "rear_spring_rate_nmm":     _S("Chassis.LeftRear.TorsionBarOD",                  "CarSetup_Chassis_LeftRear_TorsionBarOD",            index_map=None),
    "front_arb_blade":          _S("Chassis.Front.ArbBlades",                        "CarSetup_Chassis_Front_ArbBlades[0]",               range_min=1, range_max=5, parse_fn="int"),
    "rear_arb_blade":           _S("Chassis.Rear.ArbBlades",                         "CarSetup_Chassis_Rear_ArbBlades[0]",                range_min=1, range_max=5, parse_fn="int"),
    # Ferrari dampers (same Chassis.* paths for .sto, but Dampers.* paths for IBT YAML)
    "front_ls_comp":            _S("Dampers.LeftFrontDamper.LsCompDamping",          "CarSetup_Chassis_LeftFront_LsCompDamping",          range_min=0, range_max=40, parse_fn="int"),
    "front_ls_rbd":             _S("Dampers.LeftFrontDamper.LsRbdDamping",           "CarSetup_Chassis_LeftFront_LsRbdDamping",           range_min=0, range_max=40, parse_fn="int"),
    "front_hs_comp":            _S("Dampers.LeftFrontDamper.HsCompDamping",          "CarSetup_Chassis_LeftFront_HsCompDamping",          range_min=0, range_max=40, parse_fn="int"),
    "front_hs_rbd":             _S("Dampers.LeftFrontDamper.HsRbdDamping",           "CarSetup_Chassis_LeftFront_HsRbdDamping",           range_min=0, range_max=40, parse_fn="int"),
    "front_hs_slope":           _S("Dampers.LeftFrontDamper.HsCompDampSlope",        "CarSetup_Chassis_LeftFront_HsCompDampSlope",        range_min=0, range_max=40, parse_fn="int"),
    "rear_ls_comp":             _S("Dampers.LeftRearDamper.LsCompDamping",           "CarSetup_Chassis_LeftRear_LsCompDamping",           range_min=0, range_max=40, parse_fn="int"),
    "rear_ls_rbd":              _S("Dampers.LeftRearDamper.LsRbdDamping",            "CarSetup_Chassis_LeftRear_LsRbdDamping",            range_min=0, range_max=40, parse_fn="int"),
    "rear_hs_comp":             _S("Dampers.LeftRearDamper.HsCompDamping",           "CarSetup_Chassis_LeftRear_HsCompDamping",           range_min=0, range_max=40, parse_fn="int"),
    "rear_hs_rbd":              _S("Dampers.LeftRearDamper.HsRbdDamping",            "CarSetup_Chassis_LeftRear_HsRbdDamping",            range_min=0, range_max=40, parse_fn="int"),
    "rear_hs_slope":            _S("Dampers.LeftRearDamper.HsCompDampSlope",         "CarSetup_Chassis_LeftRear_HsCompDampSlope",         range_min=0, range_max=40, parse_fn="int"),
    # Ferrari brakes/diff/TC under Systems.* YAML path (same STO IDs as BMW)
    "brake_bias_pct":           _S("Systems.BrakeSpec.BrakePressureBias",             "CarSetup_BrakesDriveUnit_BrakeSpec_BrakePressureBias"),
    "brake_bias_target":        _S("Systems.BrakeSpec.BrakeBiasTarget",               "CarSetup_BrakesDriveUnit_BrakeSpec_BrakeBiasTarget"),
    "brake_bias_migration":     _S("Systems.BrakeSpec.BiasMigration",                 "CarSetup_BrakesDriveUnit_BrakeSpec_BrakeBiasMigration"),
    "brake_bias_migration_gain": _S("Systems.BrakeSpec.BiasMigrationGain",            "CarSetup_BrakesDriveUnit_BrakeSpec_BiasMigrationGain"),
    "front_master_cyl_mm":      _S("Systems.BrakeSpec.FrontMasterCyl",                "CarSetup_BrakesDriveUnit_BrakeSpec_FrontMasterCyl"),
    "rear_master_cyl_mm":       _S("Systems.BrakeSpec.RearMasterCyl",                 "CarSetup_BrakesDriveUnit_BrakeSpec_RearMasterCyl"),
    "pad_compound":             _S("Systems.BrakeSpec.PadCompound",                   "CarSetup_BrakesDriveUnit_BrakeSpec_PadCompound",   parse_fn="string"),
    "front_diff_preload_nm":    _S("Systems.FrontDiffSpec.Preload",                   "CarSetup_BrakesDriveUnit_FrontDiffSpec_Preload"),
    "diff_preload_nm":          _S("Systems.RearDiffSpec.Preload",                    "CarSetup_BrakesDriveUnit_RearDiffSpec_Preload",    range_min=0.0, range_max=150.0, resolution=5.0),
    "diff_ramp_angles":         _S("Systems.RearDiffSpec.CoastDriveRampOptions",      "CarSetup_BrakesDriveUnit_RearDiffSpec_CoastDriveRampAngles", parse_fn="string"),
    "diff_ramp_option_idx":     _S("Systems.RearDiffSpec.CoastDriveRampOptions",      "CarSetup_BrakesDriveUnit_RearDiffSpec_CoastDriveRampAngles", range_min=0.0, range_max=2.0, resolution=1.0, options=(0, 1, 2), parse_fn="string"),
    "diff_clutch_plates":       _S("Systems.RearDiffSpec.ClutchFrictionPlates",       "CarSetup_BrakesDriveUnit_RearDiffSpec_ClutchFrictionPlates", parse_fn="int"),
    "tc_gain":                  _S("Systems.TractionControl.TractionControlGain",     "CarSetup_BrakesDriveUnit_TractionControl_TractionControlGain", range_min=1, range_max=10, parse_fn="int"),
    "tc_slip":                  _S("Systems.TractionControl.TractionControlSlip",     "CarSetup_BrakesDriveUnit_TractionControl_TractionControlSlip", range_min=1, range_max=10, parse_fn="int"),
    "fuel_l":                   _S("Systems.Fuel.FuelLevel",                          "CarSetup_BrakesDriveUnit_Fuel_FuelLevel"),
    "fuel_low_warning_l":       _S("Systems.Fuel.FuelLowWarning",                     "CarSetup_BrakesDriveUnit_Fuel_FuelLowWarning"),
    "fuel_target_l":            _S("Systems.Fuel.FuelTarget",                         "CarSetup_BrakesDriveUnit_Fuel_FuelTarget"),
    "gear_stack":               _S("Systems.GearRatios.GearStack",                    "CarSetup_BrakesDriveUnit_GearRatios_GearStack",    parse_fn="string"),
    "hybrid_rear_drive_enabled": _S("Systems.HybridConfig.HybridRearDriveEnabled",    "CarSetup_BrakesDriveUnit_HybridConfig_HybridRearDriveEnabled", parse_fn="string"),
    "hybrid_rear_drive_corner_pct": _S("Systems.HybridConfig.HybridRearDriveCornerPct", "CarSetup_BrakesDriveUnit_HybridConfig_HybridRearDriveCornerPct"),
    "roof_light_color":         _S("Systems.Lighting.RoofIdLightColor",               "CarSetup_BrakesDriveUnit_Lighting_RoofIdLightColor", parse_fn="string"),
    # Ferrari third spring deflection uses HeaveSpringDefl / HeaveSliderDefl
    "third_spring_defl_static_mm": _S("Chassis.Rear.HeaveSpringDefl",                "CarSetup_Chassis_Rear_HeaveSpringDeflStatic",       parse_fn="defl"),
    "third_spring_defl_max_mm": _S("Chassis.Rear.HeaveSpringDefl",                   "CarSetup_Chassis_Rear_HeaveSpringDeflMax",          parse_fn="defl"),
    "third_slider_defl_static_mm": _S("Chassis.Rear.HeaveSliderDefl",                "CarSetup_Chassis_Rear_HeaveSliderDeflStatic",       parse_fn="defl"),
    "third_slider_defl_max_mm": _S("Chassis.Rear.HeaveSliderDefl",                   "CarSetup_Chassis_Rear_HeaveSliderDeflMax",          parse_fn="defl"),
})

# Porsche 963 GTP — minimal mapping
_PORSCHE_SPECS: dict[str, CarFieldSpec] = {
    "wing_angle_deg":           _S("TiresAero.AeroSettings.RearWingAngle",           "CarSetup_TiresAero_AeroSettings_RearWingAngle"),
    "front_pushrod_offset_mm":  _S("Chassis.Front.PushrodLengthOffset",              "CarSetup_Chassis_Front_PushrodLengthOffset",        range_min=-40.0, range_max=40.0, resolution=0.5),
    "rear_pushrod_offset_mm":   _S("Chassis.Rear.PushrodLengthOffset",               "CarSetup_Chassis_Rear_PushrodLengthOffset",         range_min=-40.0, range_max=40.0, resolution=0.5),
    "lf_ride_height_mm":        _S("Chassis.LeftFront.RideHeight",                   "CarSetup_Chassis_LeftFront_RideHeight"),
    "rf_ride_height_mm":        _S("Chassis.RightFront.RideHeight",                  "CarSetup_Chassis_RightFront_RideHeight"),
    "lr_ride_height_mm":        _S("Chassis.LeftRear.RideHeight",                    "CarSetup_Chassis_LeftRear_RideHeight"),
    "rr_ride_height_mm":        _S("Chassis.RightRear.RideHeight",                   "CarSetup_Chassis_RightRear_RideHeight"),
    "front_heave_spring_nmm":   _S("Chassis.Front.HeaveSpring",                      "CarSetup_Chassis_Front_HeaveSpring"),
    "rear_third_spring_nmm":    _S("Chassis.Rear.HeaveSpring",                       "CarSetup_Chassis_Rear_HeaveSpring"),
    "front_camber_deg":         _S("Chassis.LeftFront.Camber",                       "CarSetup_Chassis_LeftFront_Camber"),
    "rear_camber_deg":          _S("Chassis.LeftRear.Camber",                        "CarSetup_Chassis_LeftRear_Camber"),
    "front_toe_mm":             _S("Chassis.Front.ToeIn",                            "CarSetup_Chassis_Front_ToeIn"),
    "rear_toe_mm":              _S("Chassis.LeftRear.ToeIn",                         "CarSetup_Chassis_LeftRear_ToeIn"),
    "fuel_l":                   _S("BrakesDriveUnit.Fuel.FuelLevel",                 "CarSetup_BrakesDriveUnit_Fuel_FuelLevel"),
    "brake_bias_pct":           _S("BrakesDriveUnit.BrakeSpec.BrakePressureBias",    "CarSetup_BrakesDriveUnit_BrakeSpec_BrakePressureBias"),
    "tc_gain":                  _S("BrakesDriveUnit.TractionControl.TractionControlGain", "CarSetup_BrakesDriveUnit_TractionControl_TractionControlGain", parse_fn="int"),
    "tc_slip":                  _S("BrakesDriveUnit.TractionControl.TractionControlSlip", "CarSetup_BrakesDriveUnit_TractionControl_TractionControlSlip", parse_fn="int"),
}

# Cadillac — BMW base + indexed ARBs
_CADILLAC_SPECS: dict[str, CarFieldSpec] = {
    **_BMW_SPECS,
    "front_arb_blade":          _S("Chassis.Front.ArbBlades",                        "CarSetup_Chassis_Front_ArbBlades[0]",               range_min=1, range_max=5, parse_fn="int"),
    "rear_arb_blade":           _S("Chassis.Rear.ArbBlades",                         "CarSetup_Chassis_Rear_ArbBlades[0]",                range_min=1, range_max=5, parse_fn="int"),
    "brake_bias_target":        _S("BrakesDriveUnit.BrakeSpec.BrakeBiasTarget",      "CarSetup_BrakesDriveUnit_BrakeSpec_BrakeBiasTarget", range_min=-5.0, range_max=5.0, resolution=0.5),
    "brake_bias_migration":     _S("BrakesDriveUnit.BrakeSpec.BrakeBiasMigration",   "CarSetup_BrakesDriveUnit_BrakeSpec_BrakeBiasMigration", range_min=-5.0, range_max=5.0, resolution=0.5),
}

# Acura — same as Cadillac
_ACURA_SPECS: dict[str, CarFieldSpec] = {
    **_BMW_SPECS,
    "front_arb_blade":          _S("Chassis.Front.ArbBlades",                        "CarSetup_Chassis_Front_ArbBlades[0]",               range_min=1, range_max=5, parse_fn="int"),
    "rear_arb_blade":           _S("Chassis.Rear.ArbBlades",                         "CarSetup_Chassis_Rear_ArbBlades[0]",                range_min=1, range_max=5, parse_fn="int"),
    "brake_bias_target":        _S("BrakesDriveUnit.BrakeSpec.BrakeBiasTarget",      "CarSetup_BrakesDriveUnit_BrakeSpec_BrakeBiasTarget", range_min=-5.0, range_max=5.0, resolution=0.5),
    "brake_bias_migration":     _S("BrakesDriveUnit.BrakeSpec.BrakeBiasMigration",   "CarSetup_BrakesDriveUnit_BrakeSpec_BrakeBiasMigration", range_min=-5.0, range_max=5.0, resolution=0.5),
}

CAR_FIELD_SPECS: dict[str, dict[str, CarFieldSpec]] = {
    "bmw": _BMW_SPECS,
    "ferrari": _FERRARI_SPECS,
    "porsche": _PORSCHE_SPECS,
    "cadillac": _CADILLAC_SPECS,
    "acura": _ACURA_SPECS,
}


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

def get_field(canonical_key: str) -> FieldDefinition | None:
    """Look up a field definition by canonical key."""
    return FIELD_REGISTRY.get(canonical_key)


def get_car_spec(car: str, canonical_key: str) -> CarFieldSpec | None:
    """Look up a car-specific field spec."""
    return CAR_FIELD_SPECS.get(car, {}).get(canonical_key)


def iter_fields(
    *,
    kind: str | None = None,
    solver_step: int | str | None = None,
) -> list[FieldDefinition]:
    """Iterate fields with optional filters."""
    results = []
    for f in FIELD_REGISTRY.values():
        if kind is not None and f.kind != kind:
            continue
        if solver_step is not None and f.solver_step != solver_step:
            continue
        results.append(f)
    return results


def yaml_path_to_canonical(yaml_path: str, car: str = "bmw") -> str | None:
    """Look up canonical key from a YAML path for a given car."""
    specs = CAR_FIELD_SPECS.get(car, {})
    for key, spec in specs.items():
        if spec.yaml_path == yaml_path:
            return key
    return None


def sto_param_id_to_canonical(sto_param_id: str, car: str = "bmw") -> str | None:
    """Look up canonical key from a STO param ID for a given car."""
    specs = CAR_FIELD_SPECS.get(car, {})
    for key, spec in specs.items():
        if spec.sto_param_id == sto_param_id:
            return key
    return None


def detect_car_adapter(yaml_keys: set[str]) -> str:
    """Detect car adapter from YAML keys (Systems.* = Ferrari, else BMW-like)."""
    if any("Systems." in k or "Dampers." in k for k in yaml_keys):
        return "ferrari"
    return "bmw"


def get_writer_param_ids(car: str) -> dict[str, str]:
    """Get canonical_key → sto_param_id mapping for setup writer.

    Returns the same structure as the old _*_PARAM_IDS dicts but built from
    the registry. Keys use the writer's naming convention (no _mm/_deg suffixes).
    """
    specs = CAR_FIELD_SPECS.get(car, {})
    result: dict[str, str] = {}
    for canonical_key, spec in specs.items():
        result[canonical_key] = spec.sto_param_id
    return result


def _car_name(car_or_name: object | str | None) -> str:
    if isinstance(car_or_name, str):
        return car_or_name.lower()
    if car_or_name is None:
        return "bmw"
    return str(getattr(car_or_name, "canonical_name", "bmw")).lower()


def get_numeric_resolution(
    car_or_name: object | str | None,
    canonical_key: str,
    *,
    default: float | None = None,
) -> float | None:
    """Return the numeric garage resolution for a car field when defined."""
    spec = get_car_spec(_car_name(car_or_name), canonical_key)
    if spec is None or spec.resolution is None:
        return default
    return float(spec.resolution)


def snap_to_resolution(
    value: object,
    resolution: float | None,
    *,
    lo: float | None = None,
    hi: float | None = None,
) -> float:
    """Snap a numeric value to a garage step using half-up rounding."""
    numeric_value = float(value)
    if resolution is None or float(resolution) <= 0.0:
        snapped = numeric_value
    else:
        decimal_value = Decimal(str(numeric_value))
        decimal_step = Decimal(str(float(resolution)))
        snapped = float(
            (decimal_value / decimal_step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * decimal_step
        )
    if lo is not None:
        snapped = max(float(lo), snapped)
    if hi is not None:
        snapped = min(float(hi), snapped)
    if resolution is None:
        return float(snapped)
    resolution_text = format(float(resolution), "f").rstrip("0").rstrip(".")
    decimals = len(resolution_text.split(".", 1)[1]) if "." in resolution_text else 0
    return round(float(snapped), decimals)


def get_diff_ramp_options(car_or_name: object | str | None = None) -> tuple[tuple[int, int], ...]:
    """Return the legal coupled diff ramp pairs for a car."""
    if car_or_name is not None and not isinstance(car_or_name, str):
        garage_ranges = getattr(car_or_name, "garage_ranges", None)
        options = getattr(garage_ranges, "diff_coast_drive_ramp_options", None)
        if options:
            return tuple((int(pair[0]), int(pair[1])) for pair in options)

    car_name = _car_name(car_or_name)
    if car_name in CAR_FIELD_SPECS:
        return DEFAULT_DIFF_RAMP_OPTIONS
    return DEFAULT_DIFF_RAMP_OPTIONS


def parse_diff_ramp_pair(value: object) -> tuple[int, int] | None:
    """Parse a diff ramp pair from strings like '40/65' or '40 / 65'."""
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        try:
            return int(round(float(value[0]))), int(round(float(value[1])))
        except (TypeError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered == "more locking":
        return DEFAULT_DIFF_RAMP_OPTIONS[0]
    if lowered == "less locking":
        return DEFAULT_DIFF_RAMP_OPTIONS[-1]
    match = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def diff_ramp_option_index(
    car_or_name: object | str | None = None,
    *,
    coast: object | None = None,
    drive: object | None = None,
    diff_ramp_angles: object | None = None,
    default: int | None = None,
) -> int | None:
    """Map a coast/drive pair or ramp-angle string to the nearest legal option index."""
    options = get_diff_ramp_options(car_or_name)
    pair = parse_diff_ramp_pair(diff_ramp_angles)
    if pair is None and coast is not None and drive is not None:
        try:
            pair = int(round(float(coast))), int(round(float(drive)))
        except (TypeError, ValueError):
            pair = None
    if pair is None:
        return default
    best_idx = min(
        range(len(options)),
        key=lambda idx: abs(options[idx][0] - pair[0]) + abs(options[idx][1] - pair[1]),
    )
    return int(best_idx)


def diff_ramp_pair_for_option(
    car_or_name: object | str | None,
    option_idx: object | None,
    *,
    default_idx: int = 0,
) -> tuple[int, int]:
    """Resolve a legal diff ramp pair from an option index."""
    options = get_diff_ramp_options(car_or_name)
    if not options:
        return DEFAULT_DIFF_RAMP_OPTIONS[default_idx]
    try:
        idx = int(round(float(option_idx))) if option_idx is not None else default_idx
    except (TypeError, ValueError):
        idx = default_idx
    idx = max(0, min(len(options) - 1, idx))
    return int(options[idx][0]), int(options[idx][1])


def diff_ramp_string_for_option(
    car_or_name: object | str | None,
    option_idx: object | None,
    *,
    ferrari_label: bool = False,
) -> str:
    """Resolve the export string for a diff ramp option index."""
    coast, drive = diff_ramp_pair_for_option(car_or_name, option_idx, default_idx=1)
    if ferrari_label:
        return "More Locking" if coast <= 45 else "Less Locking"
    return f"{coast}/{drive}"


def validate_registry() -> list[str]:
    """Cross-check registry for internal consistency. Returns list of issues."""
    issues: list[str] = []

    # Every field in FIELD_REGISTRY should have at least BMW spec
    for key in FIELD_REGISTRY:
        if key not in _BMW_SPECS:
            # Some fields are Ferrari-only or optional
            if key in (
                "front_diff_preload_nm", "rear_torsion_bar_turns", "rear_torsion_bar_defl_mm",
                "static_front_rh_mm", "static_rear_rh_mm",  # averaged from per-corner RH
            ):
                continue
            # Check if it's in at least one car
            found = any(key in CAR_FIELD_SPECS[car] for car in CAR_FIELD_SPECS)
            if not found:
                issues.append(f"Field {key!r} in FIELD_REGISTRY but no car has a spec for it")

    # Every car spec key should be in FIELD_REGISTRY
    for car, specs in CAR_FIELD_SPECS.items():
        for key in specs:
            if key not in FIELD_REGISTRY:
                issues.append(f"Car {car!r} has spec for {key!r} not in FIELD_REGISTRY")

    # No duplicate STO param IDs within a car
    for car, specs in CAR_FIELD_SPECS.items():
        seen: dict[str, str] = {}
        for key, spec in specs.items():
            if spec.sto_param_id in seen:
                # Deflection fields share YAML paths but have distinct STO IDs (static vs max)
                pass  # Allow duplicates for defl fields that parse different components
            seen[spec.sto_param_id] = key

    return issues
