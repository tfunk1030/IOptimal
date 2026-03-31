"""Ferrari-specific setup-row aliasing and coverage rules.

The raw Ferrari row dumps contain repeated labels ("Heave spring", "ARB size",
"Toe-in", "Camber") that only become unambiguous once tab/section context is
considered.  This module centralizes those aliases so schema bootstrap,
normalization, and dataset validation can resolve Ferrari rows deterministically.
"""

from __future__ import annotations

from typing import Any


def _norm(value: str | None) -> str:
    return str(value or "").strip().lower()


FERRARI_ROW_ALIASES: dict[tuple[str, str, str], str] = {
    ("chassis", "front", "heave spring"): "front_heave_spring_nmm",
    ("chassis", "front", "heavespring"): "front_heave_spring_nmm",
    ("chassis", "rear", "heave spring"): "rear_third_spring_nmm",
    ("chassis", "rear", "heavespring"): "rear_third_spring_nmm",
    ("chassis", "front", "pushrod length delta"): "front_pushrod_offset_mm",
    ("chassis", "front", "pushrodlengthdelta"): "front_pushrod_offset_mm",
    ("chassis", "rear", "pushrod length delta"): "rear_pushrod_offset_mm",
    ("chassis", "rear", "pushrodlengthdelta"): "rear_pushrod_offset_mm",
    ("chassis", "front", "heave perch offset"): "front_heave_perch_mm",
    ("chassis", "front", "heaveperchoffset"): "front_heave_perch_mm",
    ("chassis", "rear", "heave perch offset"): "rear_third_perch_mm",
    ("chassis", "rear", "heaveperchoffset"): "rear_third_perch_mm",
    ("chassis", "left front", "torsion bar o.d."): "front_torsion_od_mm",
    ("chassis", "left front", "torsionbarod"): "front_torsion_od_mm",
    ("chassis", "right front", "torsion bar o.d."): "front_torsion_od_mm",
    ("chassis", "right front", "torsionbarod"): "front_torsion_od_mm",
    ("chassis", "left rear", "torsion bar o.d."): "rear_spring_rate_nmm",
    ("chassis", "left rear", "torsionbarod"): "rear_spring_rate_nmm",
    ("chassis", "right rear", "torsion bar o.d."): "rear_spring_rate_nmm",
    ("chassis", "right rear", "torsionbarod"): "rear_spring_rate_nmm",
    ("chassis", "left front", "torsion bar turns"): "torsion_bar_turns",
    ("chassis", "left front", "torsionbarturns"): "torsion_bar_turns",
    ("chassis", "right front", "torsion bar turns"): "torsion_bar_turns",
    ("chassis", "right front", "torsionbarturns"): "torsion_bar_turns",
    ("chassis", "left rear", "torsion bar turns"): "rear_torsion_bar_turns",
    ("chassis", "left rear", "torsionbarturns"): "rear_torsion_bar_turns",
    ("chassis", "right rear", "torsion bar turns"): "rear_torsion_bar_turns",
    ("chassis", "right rear", "torsionbarturns"): "rear_torsion_bar_turns",
    ("chassis", "front", "arb size"): "front_arb_size",
    ("chassis", "front", "arbsize"): "front_arb_size",
    ("chassis", "rear", "arb size"): "rear_arb_size",
    ("chassis", "rear", "arbsize"): "rear_arb_size",
    ("chassis", "front", "arb blades"): "front_arb_blade",
    ("chassis", "front", "arbblades"): "front_arb_blade",
    ("chassis", "rear", "arb blades"): "rear_arb_blade",
    ("chassis", "rear", "arbblades"): "rear_arb_blade",
    ("chassis", "left front", "camber"): "front_camber_deg",
    ("chassis", "right front", "camber"): "front_camber_deg",
    ("chassis", "left rear", "camber"): "rear_camber_deg",
    ("chassis", "right rear", "camber"): "rear_camber_deg",
    ("chassis", "front", "toe-in"): "front_toe_mm",
    ("chassis", "front", "toein"): "front_toe_mm",
    ("chassis", "left rear", "toe-in"): "rear_toe_mm",
    ("chassis", "left rear", "toein"): "rear_toe_mm",
    ("chassis", "right rear", "toe-in"): "rear_toe_mm",
    ("chassis", "right rear", "toein"): "rear_toe_mm",
    ("systems", "brake spec", "brake pressure bias"): "brake_bias_pct",
    ("systems", "brake spec", "brakepressurebias"): "brake_bias_pct",
    ("systems", "brake spec", "front master cyl."): "front_master_cyl_mm",
    ("systems", "brake spec", "frontmastercyl"): "front_master_cyl_mm",
    ("systems", "brake spec", "rear master cyl."): "rear_master_cyl_mm",
    ("systems", "brake spec", "rearmastercyl"): "rear_master_cyl_mm",
    ("systems", "brake spec", "pad compound"): "pad_compound",
    ("systems", "brake spec", "padcompound"): "pad_compound",
    ("systems", "brake spec", "bias migration"): "brake_bias_migration",
    ("systems", "brake spec", "biasmigration"): "brake_bias_migration",
    ("systems", "brake spec", "bias migration gain"): "brake_bias_migration_gain",
    ("systems", "brake spec", "biasmigrationgain"): "brake_bias_migration_gain",
    ("systems", "front diff spec", "preload"): "front_diff_preload_nm",
    ("systems", "rear diff spec", "preload"): "diff_preload_nm",
    ("systems", "rear diff spec", "coast/drive ramp options"): "diff_ramp_angles",
    ("systems", "rear diff spec", "coastdriverampoptions"): "diff_ramp_angles",
    ("systems", "rear diff spec", "clutch friction plates"): "diff_clutch_plates",
    ("systems", "rear diff spec", "clutchfrictionplates"): "diff_clutch_plates",
    ("systems", "traction control", "traction control gain"): "tc_gain",
    ("systems", "traction control", "tractioncontrolgain"): "tc_gain",
    ("systems", "traction control", "traction control slip"): "tc_slip",
    ("systems", "traction control", "tractioncontrolslip"): "tc_slip",
    ("systems", "fuel", "fuel level"): "fuel_l",
    ("systems", "fuel", "fuellevel"): "fuel_l",
    ("systems", "fuel", "fuel target"): "fuel_target_l",
    ("systems", "fuel", "fueltarget"): "fuel_target_l",
    ("systems", "fuel", "fuel low warning"): "fuel_low_warning_l",
    ("systems", "fuel", "fuellowwarning"): "fuel_low_warning_l",
    ("systems", "gear ratios", "gear stack"): "gear_stack",
    ("systems", "gear ratios", "gearstack"): "gear_stack",
    ("systems", "gear ratios", "speed in first"): "speed_in_first_kph",
    ("systems", "gear ratios", "speedinfirst"): "speed_in_first_kph",
    ("systems", "gear ratios", "speed in second"): "speed_in_second_kph",
    ("systems", "gear ratios", "speedinsecond"): "speed_in_second_kph",
    ("systems", "gear ratios", "speed in third"): "speed_in_third_kph",
    ("systems", "gear ratios", "speedinthird"): "speed_in_third_kph",
    ("systems", "gear ratios", "speed in fourth"): "speed_in_fourth_kph",
    ("systems", "gear ratios", "speedinfourth"): "speed_in_fourth_kph",
    ("systems", "gear ratios", "speed in fifth"): "speed_in_fifth_kph",
    ("systems", "gear ratios", "speedinfifth"): "speed_in_fifth_kph",
    ("systems", "gear ratios", "speed in sixth"): "speed_in_sixth_kph",
    ("systems", "gear ratios", "speedinsixth"): "speed_in_sixth_kph",
    ("systems", "gear ratios", "speed in seventh"): "speed_in_seventh_kph",
    ("systems", "gear ratios", "speedinseventh"): "speed_in_seventh_kph",
    ("systems", "hybrid config", "hybrid rear drive enabled"): "hybrid_rear_drive_enabled",
    ("systems", "hybrid config", "hybridreardriveenabled"): "hybrid_rear_drive_enabled",
    ("systems", "hybrid config", "hybrid rear drive corner pct"): "hybrid_rear_drive_corner_pct",
    ("systems", "hybrid config", "hybridreardrivecornerpct"): "hybrid_rear_drive_corner_pct",
    ("systems", "lighting", "roof id light color"): "roof_light_color",
    ("systems", "lighting", "roofidlightcolor"): "roof_light_color",
    ("tires/aero", "aero calculator", "front rh at speed"): "front_rh_at_speed_mm",
    ("tires/aero", "aero calculator", "frontrhatspeed"): "front_rh_at_speed_mm",
    ("tires/aero", "aero calculator", "rear rh at speed"): "rear_rh_at_speed_mm",
    ("tires/aero", "aero calculator", "rearrhatspeed"): "rear_rh_at_speed_mm",
    ("tires/aero", "aero settings", "rear wing angle"): "wing_angle_deg",
    ("tires/aero", "aero settings", "rearwingangle"): "wing_angle_deg",
}


FERRARI_REQUIRED_INPUTS: tuple[str, ...] = (
    "front_heave_spring_nmm",
    "rear_third_spring_nmm",
    "front_pushrod_offset_mm",
    "rear_pushrod_offset_mm",
    "front_heave_perch_mm",
    "rear_third_perch_mm",
    "front_torsion_od_mm",
    "rear_spring_rate_nmm",
    "front_arb_size",
    "rear_arb_size",
    "front_arb_blade",
    "rear_arb_blade",
    "front_camber_deg",
    "rear_camber_deg",
    "front_toe_mm",
    "rear_toe_mm",
    "brake_bias_pct",
    "front_master_cyl_mm",
    "rear_master_cyl_mm",
    "pad_compound",
    "front_diff_preload_nm",
    "diff_preload_nm",
    "diff_ramp_angles",
    "diff_clutch_plates",
    "tc_gain",
    "tc_slip",
    "fuel_l",
    "fuel_target_l",
    "fuel_low_warning_l",
)


def lookup_ferrari_alias(row: dict[str, Any]) -> str | None:
    tab = _norm(row.get("tab"))
    section = _norm(row.get("section"))
    label = _norm(row.get("label"))
    return FERRARI_ROW_ALIASES.get((tab, section, label))


def resolve_ferrari_canonical_key(row: dict[str, Any]) -> str | None:
    """Compatibility wrapper used by normalization/bootstrap code."""
    return lookup_ferrari_alias(row)


def resolve_ferrari_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return lightweight Ferrari row resolution metadata for validators/reporting."""
    canonical_key = lookup_ferrari_alias(row)
    return {
        "canonical_key": canonical_key,
        "is_required_input": canonical_key in FERRARI_REQUIRED_INPUTS if canonical_key else False,
        "tab": _norm(row.get("tab")),
        "section": _norm(row.get("section")),
        "label": _norm(row.get("label")),
    }


def flatten_ferrari_session_info(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert nested Ferrari-style CarSetup session-info payloads into row dicts.

    The screenshot/fixture payloads in this repo often mirror `IBT.session_info`
    instead of the flat `rows` export format.  This helper makes those payloads
    consumable by the calibration normalizer and schema bootstrap tooling.
    """
    car_setup = dict(payload.get("CarSetup") or {})
    rows: list[dict[str, Any]] = []

    def _append_rows(tab: str, section: str, mapping: dict[str, Any]) -> None:
        for key, value in mapping.items():
            rows.append(
                {
                    "label": key.replace("_", " "),
                    "tab": tab,
                    "section": section,
                    "metric_value": value,
                    "imperial_value": value,
                    "is_mapped": True,
                    "is_derived": False,
                }
            )

    chassis = dict(car_setup.get("Chassis") or {})
    for section_name, section_values in chassis.items():
        if isinstance(section_values, dict):
            _append_rows("Chassis", section_name.replace("Left", "Left ").replace("Right", "Right "), section_values)

    dampers = dict(car_setup.get("Dampers") or {})
    for section_name, section_values in dampers.items():
        if isinstance(section_values, dict):
            pretty = section_name.replace("Left", "Left ").replace("Right", "Right ").replace("Damper", " Damper")
            _append_rows("Dampers", pretty, section_values)

    systems = dict(car_setup.get("Systems") or {})
    for section_name, section_values in systems.items():
        if isinstance(section_values, dict):
            pretty = (
                section_name.replace("DiffSpec", " Diff Spec")
                .replace("BrakeSpec", "Brake Spec")
                .replace("TractionControl", "Traction Control")
                .replace("GearRatios", "Gear Ratios")
                .replace("HybridConfig", "Hybrid Config")
            )
            _append_rows("Systems", pretty, section_values)

    tires_aero = dict(car_setup.get("TiresAero") or {})
    for section_name, section_values in tires_aero.items():
        if isinstance(section_values, dict):
            pretty = section_name.replace("AeroSettings", "Aero Settings").replace("AeroCalculator", "Aero Calculator")
            _append_rows("Tires/Aero", pretty, section_values)

    return rows


def flatten_ferrari_carsetup_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Backward-compatible alias for flattening nested Ferrari CarSetup payloads."""
    return flatten_ferrari_session_info(payload)


def flatten_ferrari_carsetup_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Backward-compatible alias for validator/normalizer callers."""
    return flatten_ferrari_session_info(payload)
