"""Ingest the structured garage schema JSON from the webapp/frontend.

The webapp produces a JSON array where each row describes one garage parameter:
    - label: human-readable parameter name
    - tab / section: garage UI location
    - metric_value / imperial_value: current value (with units)
    - range_metric / range_imperial: legal min/max
    - is_mapped: True = settable parameter (not an internal flag)
    - is_derived: True = computed from other parameters (read-only)
    - row_id: stable hash of the parameter identity

This JSON is the most complete, accurate description of the garage we have:
  - It contains ALL settable parameters with their exact current values
  - It contains the exact legal ranges (min/max) as iRacing enforces them
  - It exposes raw internal fields (fSideSpringRateNpm, lrPerchOffsetm, etc.)
    that give the actual physical values iRacing is using internally
  - It separates the exposed garage settings from iRacing's internal simulation
    parameters

From this single JSON we can:
  1. Build a complete CurrentSetup without parsing the IBT YAML
  2. Extract legal ranges for every parameter (building GarageRanges)
  3. Derive physics constants from the raw internal fields
  4. Map the index-based parameters (Ferrari heave/torsion indices) to physical values
  5. Cross-check the calibrated car model against the actual simulation values

Usage:
    from car_model.garage_schema_ingester import (
        parse_garage_schema_json,
        derive_physics_from_schema,
        build_garage_ranges_from_schema,
    )

    with open("ferrari_setup.json") as f:
        schema = json.load(f)

    params = parse_garage_schema_json(schema)
    print(params.front_heave_index)        # 3
    print(params.front_spring_rate_nmm)    # 115.17 (from fSideSpringRateNpm)
    print(params.front_corner_weight_n)    # derived if available

    ranges = build_garage_ranges_from_schema(schema)
    print(ranges.front_heave_nmm)          # (0.0, 8.0) = index range

    physics = derive_physics_from_schema(schema, car_name="ferrari")
    print(physics.front_torsion_c)         # derived from spring rate + OD index
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ─── Unit parser ───────────────────────────────────────────────────────────────

def _parse_numeric(value_str: str | int | float | None, default: float = 0.0) -> float:
    """Extract float from a garage schema value string. Handles all unit formats."""
    if value_str is None:
        return default
    if isinstance(value_str, (int, float)):
        return float(value_str)
    s = str(value_str).strip()
    # Try the first numeric token
    # "20 clicks" → 20, "115170.265625" → 115170.265625, " 3" → 3, "53.00%" → 53.0
    # "-16.5 mm" → -16.5, "0.089 Turns" → 0.089
    match = re.match(r'^([+-]?\d+\.?\d*(?:e[+-]?\d+)?)', s, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    # Handle trailing-unit patterns: "Less Locking", " A", " C" → not numeric
    return default


def _parse_string(value_str: str | None) -> str:
    if value_str is None:
        return ""
    return str(value_str).strip()


def _parse_range(range_dict: dict | None) -> tuple[float, float] | None:
    if not range_dict:
        return None
    lo = _parse_numeric(range_dict.get("min"), float("-inf"))
    hi = _parse_numeric(range_dict.get("max"), float("inf"))
    if lo == float("-inf") or hi == float("inf"):
        return None
    return (lo, hi)


# ─── Structured result ──────────────────────────────────────────────────────────

@dataclass
class GarageSchemaParams:
    """All parameters extracted from a garage schema JSON.

    Raw fields are preserved as-is; derived fields are computed lazily.
    Physical units are explicitly documented for each field.
    """
    car_name: str = ""

    # ── Aero ──
    wing_angle_deg: float = 0.0
    front_rh_at_speed_mm: float = 0.0      # AeroCalculator input (mm)
    rear_rh_at_speed_mm: float = 0.0

    # ── Ride heights / Pushrod ──
    front_pushrod_mm: float = 0.0          # mm (PushrodLengthDelta)
    rear_pushrod_mm: float = 0.0

    # ── Heave / Third springs ── (may be indices for Ferrari)
    front_heave_index: float = 0.0         # raw garage index (Ferrari: 0-8)
    rear_heave_index: float = 0.0          # raw garage index (Ferrari: 0-9)
    front_heave_perch_mm: float = 0.0      # mm
    rear_heave_perch_mm: float = 0.0       # mm

    # ── Raw internal physics values (exposed by iRacing internals) ──
    # These are the actual simulation parameters — ground truth for calibration
    front_spring_rate_nmm: float | None = None    # N/mm from fSideSpringRateNpm
    rear_spring_rate_nmm: float | None = None     # N/mm from rSideSpringRateNpm
    lf_perch_offset_m: float | None = None        # m (raw) from lfPerchOffsetm
    lr_perch_offset_m: float | None = None        # m (raw) from lrPerchOffsetm
    hf_heave_defl_max_m: float | None = None      # m from hfPackerThicknessm (packer = stop limit)
    hr_heave_defl_max_m: float | None = None
    hf_ls_comp_setting: float | None = None       # internal heave damper LS (separate from corner dampers)
    hr_ls_comp_setting: float | None = None
    hf_hs_comp_setting: float | None = None
    hr_hs_comp_setting: float | None = None
    hf_ls_rbd_setting: float | None = None
    hr_ls_rbd_setting: float | None = None
    hf_hs_rbd_setting: float | None = None
    hr_hs_rbd_setting: float | None = None
    hf_hs_slope_setting: float | None = None
    hr_hs_slope_setting: float | None = None

    # ── Torsion bars (raw index for Ferrari) ──
    front_torsion_index: float = 0.0      # raw garage index
    rear_torsion_index: float = 0.0       # raw garage index
    torsion_bar_turns_lf: float = 0.0    # turns (preload)
    torsion_bar_turns_rf: float = 0.0
    torsion_bar_turns_lr: float = 0.0
    torsion_bar_turns_rr: float = 0.0

    # ── ARB ──
    front_arb_size: str = ""
    front_arb_blade: int = 0
    rear_arb_size: str = ""
    rear_arb_blade: int = 0

    # ── Geometry ──
    front_camber_lf_deg: float = 0.0    # per-corner (derived, may be asymmetric)
    front_camber_rf_deg: float = 0.0
    rear_camber_lr_deg: float = 0.0
    rear_camber_rr_deg: float = 0.0
    front_toe_fraction: float = 0.0     # raw toe value from schema (may be fraction or mm)
    rear_toe_fraction: float = 0.0
    front_toe_units: str = "unknown"
    rear_toe_units: str = "unknown"
    # Hub pitch angles (raw internal values)
    lf_hub_dpitch: float | None = None
    rf_hub_dpitch: float | None = None

    # ── Dampers — per-corner (LF/RF/LR/RR, 0-40 click scale) ──
    lf_ls_comp: int = 0
    rf_ls_comp: int = 0
    lr_ls_comp: int = 0
    rr_ls_comp: int = 0
    lf_hs_comp: int = 0
    rf_hs_comp: int = 0
    lr_hs_comp: int = 0
    rr_hs_comp: int = 0
    lf_hs_slope: int = 0
    rf_hs_slope: int = 0
    lr_hs_slope: int = 0
    rr_hs_slope: int = 0
    lf_ls_rbd: int = 0
    rf_ls_rbd: int = 0
    lr_ls_rbd: int = 0
    rr_ls_rbd: int = 0
    lf_hs_rbd: int = 0
    rf_hs_rbd: int = 0
    lr_hs_rbd: int = 0
    rr_hs_rbd: int = 0

    # ── Brakes / Systems ──
    brake_bias_pct: float = 0.0
    front_master_cyl_mm: float = 0.0
    rear_master_cyl_mm: float = 0.0
    pad_compound: str = ""
    brake_bias_migration: float = 0.0
    brake_bias_migration_gain: float = 0.0
    front_master_cyl_m: float | None = None   # raw SI value from brakeMasterCylDiaFm
    rear_master_cyl_m: float | None = None

    # ── Diff ──
    rear_diff_preload_nm: float = 0.0
    front_diff_preload_nm: float = 0.0   # Ferrari has front diff
    diff_ramp_option: str = ""
    diff_clutch_plates: int = 0

    # ── TC ──
    tc_slip: int = 0
    tc_gain: int = 0
    tc_lat_slip_setting: float | None = None  # raw internal

    # ── Fuel ──
    fuel_l: float = 0.0
    fuel_low_warning_l: float = 0.0
    fuel_target_l: float = 0.0

    # ── Tyres ──
    tyre_pressure_lf_kpa: float = 0.0
    tyre_pressure_rf_kpa: float = 0.0
    tyre_pressure_lr_kpa: float = 0.0
    tyre_pressure_rr_kpa: float = 0.0
    tyre_type: str = ""

    # ── Gear / Hybrid ──
    gear_stack: str = ""
    hybrid_rear_drive_enabled: str = ""
    hybrid_rear_drive_corner_pct: float = 0.0

    # ── Legal ranges (populated by build_garage_ranges_from_schema) ──
    legal_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)

    # ── Raw rows (preserved for downstream processing) ──
    raw_mapped_rows: list[dict] = field(default_factory=list)
    raw_internal_rows: list[dict] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)

    @property
    def front_camber_deg(self) -> float:
        """Mean of LF/RF camber (symmetric assumption)."""
        return (abs(self.front_camber_lf_deg) + abs(self.front_camber_rf_deg)) / 2.0 * -1.0

    @property
    def rear_camber_deg(self) -> float:
        """Mean of LR/RR camber (symmetric assumption)."""
        return (abs(self.rear_camber_lr_deg) + abs(self.rear_camber_rr_deg)) / 2.0 * -1.0

    def derived_front_spring_rate_nmm(self) -> float | None:
        """fSideSpringRateNpm (N/m) → N/mm."""
        if self.front_spring_rate_nmm is not None:
            return self.front_spring_rate_nmm / 1000.0
        return None

    def derived_rear_spring_rate_nmm(self) -> float | None:
        """rSideSpringRateNpm (N/m) → N/mm."""
        if self.rear_spring_rate_nmm is not None:
            return self.rear_spring_rate_nmm / 1000.0
        return None

    def summary(self) -> str:
        lines = [
            f"Garage Schema: {self.car_name}",
            f"  Wing: {self.wing_angle_deg:.0f}°  Fuel: {self.fuel_l:.0f}L",
            f"  Front heave idx={self.front_heave_index:.0f} perch={self.front_heave_perch_mm:.1f}mm",
            f"  Rear  heave idx={self.rear_heave_index:.0f} perch={self.rear_heave_perch_mm:.1f}mm",
            f"  Front torsion idx={self.front_torsion_index:.0f}  Rear idx={self.rear_torsion_index:.0f}",
            f"  ARB: F={self.front_arb_size}/{self.front_arb_blade}  R={self.rear_arb_size}/{self.rear_arb_blade}",
        ]
        if self.front_spring_rate_nmm is not None:
            lines.append(
                f"  [PHYSICS] Front corner spring: {self.front_spring_rate_nmm/1000:.2f} N/mm  "
                f"Rear: {(self.rear_spring_rate_nmm or 0)/1000:.2f} N/mm"
            )
        return "\n".join(lines)


# ─── Label → field routing table ───────────────────────────────────────────────

# Maps (label, tab, section) tuples to (attr, converter) pairs.
# Some fields appear multiple times with different tab/section — use tab+section to disambiguate.
# section=None means "match any section" (used when a label is globally unique).

_LABEL_MAP: dict[tuple[str, str | None, str | None], tuple[str, str]] = {
    # Aero
    ("Rear wing angle",             "Tires/Aero",   "Aero Settings"):   ("wing_angle_deg",          "float"),
    ("Front RH at speed",           "Tires/Aero",   "Aero Calculator"): ("front_rh_at_speed_mm",    "float"),
    ("Rear RH at speed",            "Tires/Aero",   "Aero Calculator"): ("rear_rh_at_speed_mm",     "float"),

    # Pushrod
    ("Pushrod length delta",        "Chassis",       "Front"):           ("front_pushrod_mm",        "float"),
    ("Pushrod length delta",        "Chassis",       "Rear"):            ("rear_pushrod_mm",         "float"),
    # BMW/Cadillac use "Pushrod length offset"
    ("Pushrod length offset",       "Chassis",       "Front"):           ("front_pushrod_mm",        "float"),
    ("Pushrod length offset",       "Chassis",       "Rear"):            ("rear_pushrod_mm",         "float"),

    # Heave springs (Ferrari: raw index; BMW: N/mm)
    ("Heave spring",                "Chassis",       "Front"):           ("front_heave_index",       "float"),
    ("Heave spring",                "Chassis",       "Rear"):            ("rear_heave_index",        "float"),
    ("Heave perch offset",          "Chassis",       "Front"):           ("front_heave_perch_mm",    "float"),
    ("Heave perch offset",          "Chassis",       "Rear"):            ("rear_heave_perch_mm",     "float"),

    # Torsion bar OD (Ferrari: raw index; BMW: mm)
    ("Torsion bar O.D.",            "Chassis",       "Left Front"):      ("front_torsion_index",     "float"),
    ("Torsion bar O.D.",            "Chassis",       "Left Rear"):       ("rear_torsion_index",      "float"),
    ("Torsion bar O.D.",            "Chassis",       "Right Front"):     ("front_torsion_index",     "float"),  # duplicate — same value
    ("Torsion bar O.D.",            "Chassis",       "Right Rear"):      ("rear_torsion_index",      "float"),

    # Torsion bar turns (preload for ride height)
    ("Torsion bar turns",           "Chassis",       "Left Front"):      ("torsion_bar_turns_lf",    "float"),
    ("Torsion bar turns",           "Chassis",       "Right Front"):     ("torsion_bar_turns_rf",    "float"),
    ("Torsion bar turns",           "Chassis",       "Left Rear"):       ("torsion_bar_turns_lr",    "float"),
    ("Torsion bar turns",           "Chassis",       "Right Rear"):      ("torsion_bar_turns_rr",    "float"),

    # ARB
    ("ARB size",                    "Chassis",       "Front"):           ("front_arb_size",          "string"),
    ("ARB size",                    "Chassis",       "Rear"):            ("rear_arb_size",           "string"),
    ("ARB blades",                  "Chassis",       "Front"):           ("front_arb_blade",         "int"),
    ("ARB blades",                  "Chassis",       "Rear"):            ("rear_arb_blade",          "int"),

    # Geometry — camber (per corner)
    ("Camber",                      "Chassis",       "Left Front"):      ("front_camber_lf_deg",     "float"),
    ("Camber",                      "Chassis",       "Right Front"):     ("front_camber_rf_deg",     "float"),
    ("Camber",                      "Chassis",       "Left Rear"):       ("rear_camber_lr_deg",      "float"),
    ("Camber",                      "Chassis",       "Right Rear"):      ("rear_camber_rr_deg",      "float"),

    # Geometry — toe
    ("Toe-in",                      "Chassis",       "Front"):           ("front_toe_fraction",      "float"),
    ("Toe-in",                      "Chassis",       "Left Front"):      ("front_toe_fraction",      "float"),
    ("Toe-in",                      "Chassis",       "Right Front"):     ("front_toe_fraction",      "float"),
    ("Toe-in",                      "Chassis",       "Left Rear"):       ("rear_toe_fraction",       "float"),
    ("Toe-in",                      "Chassis",       "Right Rear"):      ("rear_toe_fraction",       "float"),
    ("Toe-in",                      "Chassis",       "Rear"):            ("rear_toe_fraction",       "float"),

    # Dampers — per corner (LF/RF/LR/RR) — 0-40 scale
    ("LS comp damping",             "Dampers",       "Left Front Damper"):   ("lf_ls_comp",   "int"),
    ("LS comp damping",             "Dampers",       "Right Front Damper"):  ("rf_ls_comp",   "int"),
    ("LS comp damping",             "Dampers",       "Left Rear Damper"):    ("lr_ls_comp",   "int"),
    ("LS comp damping",             "Dampers",       "Right Rear Damper"):   ("rr_ls_comp",   "int"),
    ("HS comp damping",             "Dampers",       "Left Front Damper"):   ("lf_hs_comp",   "int"),
    ("HS comp damping",             "Dampers",       "Right Front Damper"):  ("rf_hs_comp",   "int"),
    ("HS comp damping",             "Dampers",       "Left Rear Damper"):    ("lr_hs_comp",   "int"),
    ("HS comp damping",             "Dampers",       "Right Rear Damper"):   ("rr_hs_comp",   "int"),
    ("HS comp damp slope",          "Dampers",       "Left Front Damper"):   ("lf_hs_slope",  "int"),
    ("HS comp damp slope",          "Dampers",       "Right Front Damper"):  ("rf_hs_slope",  "int"),
    ("HS comp damp slope",          "Dampers",       "Left Rear Damper"):    ("lr_hs_slope",  "int"),
    ("HS comp damp slope",          "Dampers",       "Right Rear Damper"):   ("rr_hs_slope",  "int"),
    ("LS rbd damping",              "Dampers",       "Left Front Damper"):   ("lf_ls_rbd",    "int"),
    ("LS rbd damping",              "Dampers",       "Right Front Damper"):  ("rf_ls_rbd",    "int"),
    ("LS rbd damping",              "Dampers",       "Left Rear Damper"):    ("lr_ls_rbd",    "int"),
    ("LS rbd damping",              "Dampers",       "Right Rear Damper"):   ("rr_ls_rbd",    "int"),
    ("HS rbd damping",              "Dampers",       "Left Front Damper"):   ("lf_hs_rbd",    "int"),
    ("HS rbd damping",              "Dampers",       "Right Front Damper"):  ("rf_hs_rbd",    "int"),
    ("HS rbd damping",              "Dampers",       "Left Rear Damper"):    ("lr_hs_rbd",    "int"),
    ("HS rbd damping",              "Dampers",       "Right Rear Damper"):   ("rr_hs_rbd",    "int"),

    # Brakes / Systems
    ("Brake pressure bias",         "Systems",       "Brake Spec"):      ("brake_bias_pct",          "float"),
    ("Front master cyl.",           "Systems",       "Brake Spec"):      ("front_master_cyl_mm",     "float"),
    ("Rear master cyl.",            "Systems",       "Brake Spec"):      ("rear_master_cyl_mm",      "float"),
    ("Pad compound",                "Systems",       "Brake Spec"):      ("pad_compound",            "string"),
    ("Bias migration",              "Systems",       "Brake Spec"):      ("brake_bias_migration",    "float"),
    ("Bias migration gain",         "Systems",       "Brake Spec"):      ("brake_bias_migration_gain","float"),

    # Diff
    ("Preload",                     "Systems",       "Rear Diff Spec"):  ("rear_diff_preload_nm",    "float"),
    ("Preload",                     "Systems",       "Front Diff Spec"): ("front_diff_preload_nm",   "float"),
    ("Coast/drive ramp options",    "Systems",       "Rear Diff Spec"):  ("diff_ramp_option",        "string"),
    ("Clutch friction plates",      "Systems",       "Rear Diff Spec"):  ("diff_clutch_plates",      "int"),

    # TC
    ("Traction control slip",       "Systems",       "Traction Control"):("tc_slip",                "int"),
    ("Traction control gain",       "Systems",       "Traction Control"):("tc_gain",                "int"),

    # Fuel
    ("Fuel level",                  "Chassis",       "Rear"):            ("fuel_l",                  "float"),
    ("Fuel level",                  "Systems",       "Fuel"):            ("fuel_l",                  "float"),
    ("Fuel low warning",            "Systems",       "Fuel"):            ("fuel_low_warning_l",      "float"),
    ("Fuel target",                 "Systems",       "Fuel"):            ("fuel_target_l",           "float"),

    # Tyres
    ("Starting pressure",           "Tires/Aero",    "Left Front"):      ("tyre_pressure_lf_kpa",    "float"),
    ("Starting pressure",           "Tires/Aero",    "Right Front"):     ("tyre_pressure_rf_kpa",    "float"),
    ("Starting pressure",           "Tires/Aero",    "Left Rear Tire"):  ("tyre_pressure_lr_kpa",    "float"),
    ("Starting pressure",           "Tires/Aero",    "Right Rear Tire"): ("tyre_pressure_rr_kpa",    "float"),
    ("Tire type",                   "Tires/Aero",    "Tire Type"):       ("tyre_type",               "string"),

    # Hybrid / Gear
    ("Gear stack",                  "Systems",       "Gear Ratios"):     ("gear_stack",              "string"),
    ("Hybrid rear drive enabled",   "Systems",       "Hybrid Config"):   ("hybrid_rear_drive_enabled","string"),
    ("Hybrid rear drive corner pct","Systems",       "Hybrid Config"):   ("hybrid_rear_drive_corner_pct","float"),
}

# Internal raw field label → attr mapping (is_mapped=False rows)
_INTERNAL_FIELD_MAP: dict[str, tuple[str, str]] = {
    # Physical spring rates (N/m — iRacing simulation values)
    "fSideSpringRateNpm":       ("front_spring_rate_nmm",    "float"),   # actual N/m stored, /1000 = N/mm
    "rSideSpringRateNpm":       ("rear_spring_rate_nmm",     "float"),
    # Perch offsets (meters)
    "lrPerchOffsetm":           ("lr_perch_offset_m",        "float"),
    "rrPerchOffsetm":           ("lr_perch_offset_m",        "float"),   # use same field (symmetric)
    # Packer thickness (heave spring bump stop / max defl) — m
    "hfPackerThicknessm":       ("hf_heave_defl_max_m",      "float"),
    "hrPackerThicknessm":       ("hr_heave_defl_max_m",      "float"),
    # Internal heave damper settings (separate from per-corner dampers)
    "hfLowSpeedCompDampSetting": ("hf_ls_comp_setting",      "float"),
    "hrLowSpeedCompDampSetting": ("hr_ls_comp_setting",      "float"),
    "hfHighSpeedCompDampSetting":("hf_hs_comp_setting",      "float"),
    "hrHighSpeedCompDampSetting":("hr_hs_comp_setting",      "float"),
    "hfLowSpeedRbdDampSetting":  ("hf_ls_rbd_setting",       "float"),
    "hrLowSpeedRbdDampSetting":  ("hr_ls_rbd_setting",       "float"),
    "hfHighSpeedRbdDampSetting": ("hf_hs_rbd_setting",       "float"),
    "hrHighSpeedRbdDampSetting": ("hr_hs_rbd_setting",       "float"),
    "hfHSSlopeCompDampSetting":  ("hf_hs_slope_setting",     "float"),
    "hrHSSlopeCompDampSetting":  ("hr_hs_slope_setting",     "float"),
    # Brake master cylinder diameter (m — raw physics value)
    "brakeMasterCylDiaFm":      ("front_master_cyl_m",       "float"),
    "brakeMasterCylDiaRm":      ("rear_master_cyl_m",        "float"),
    # Hub pitch angles (camber-related geometry)
    "lfhubDpitch":              ("lf_hub_dpitch",            "float"),
    "rfhubDpitch":              ("rf_hub_dpitch",            "float"),
    # TC internal
    "rTracControlLatSlipSetting":("tc_lat_slip_setting",     "float"),
}


def _normalize_label(label: str) -> str:
    """Normalize label for fuzzy matching (lowercase, strip extra spaces)."""
    return " ".join(label.strip().split()).lower()


def parse_garage_schema_json(data: dict | list) -> GarageSchemaParams:
    """Parse a garage schema JSON dict or list into GarageSchemaParams.

    Args:
        data: Either the full JSON object {"carName": ..., "rows": [...]}
              or just the rows list.

    Returns:
        GarageSchemaParams with all extracted values.
    """
    if isinstance(data, dict):
        car_name = str(data.get("carName", "")).lower()
        rows = data.get("rows", [])
    elif isinstance(data, list):
        car_name = ""
        rows = data
    else:
        raise ValueError(f"Expected dict or list, got {type(data)}")

    params = GarageSchemaParams(car_name=car_name)

    for row in rows:
        label = str(row.get("label", "")).strip()
        tab = row.get("tab")
        section = row.get("section")
        metric_value = row.get("metric_value")
        is_mapped = bool(row.get("is_mapped", False))
        range_metric = row.get("range_metric")

        # Record range for legal constraint building
        legal_range = _parse_range(range_metric)

        # ── Mapped (garage-visible) parameters ──
        if is_mapped:
            params.raw_mapped_rows.append(row)
            # Try to find a match in the label map
            # Exact match first: (label, tab, section)
            key = (label, tab, section)
            if key in _LABEL_MAP:
                attr, conv = _LABEL_MAP[key]
                val = _parse_numeric(metric_value) if conv in ("float", "int") else _parse_string(metric_value)
                if conv == "int":
                    val = int(round(float(val))) if isinstance(val, (int, float)) else 0
                setattr(params, attr, val)
                if legal_range:
                    params.legal_ranges[attr] = legal_range
                continue

            # Tab-only match: (label, tab, None)
            key2 = (label, tab, None)
            if key2 in _LABEL_MAP:
                attr, conv = _LABEL_MAP[key2]
                val = _parse_numeric(metric_value) if conv in ("float", "int") else _parse_string(metric_value)
                if conv == "int":
                    val = int(round(float(val))) if isinstance(val, (int, float)) else 0
                setattr(params, attr, val)
                if legal_range:
                    params.legal_ranges[attr] = legal_range
                continue

            # Label-only match (globally unique): (label, None, None)
            key3 = (label, None, None)
            if key3 in _LABEL_MAP:
                attr, conv = _LABEL_MAP[key3]
                val = _parse_numeric(metric_value) if conv in ("float", "int") else _parse_string(metric_value)
                if conv == "int":
                    val = int(round(float(val))) if isinstance(val, (int, float)) else 0
                setattr(params, attr, val)
                if legal_range:
                    params.legal_ranges[attr] = legal_range
                continue

        # ── Internal (is_mapped=False) raw physics fields ──
        if label in _INTERNAL_FIELD_MAP:
            params.raw_internal_rows.append(row)
            attr, conv = _INTERNAL_FIELD_MAP[label]
            val = _parse_numeric(metric_value)
            setattr(params, attr, val)
            continue

        # Unmatched — skip silently (many internal fields are irrelevant)

    return params


@dataclass
class GarageRangesFromSchema:
    """Legal parameter ranges extracted directly from the schema JSON."""
    wing_angle_deg: tuple[float, float] | None = None
    front_pushrod_mm: tuple[float, float] | None = None
    rear_pushrod_mm: tuple[float, float] | None = None
    front_heave_index: tuple[float, float] | None = None   # may be index range
    rear_heave_index: tuple[float, float] | None = None
    front_heave_perch_mm: tuple[float, float] | None = None
    rear_heave_perch_mm: tuple[float, float] | None = None
    front_torsion_index: tuple[float, float] | None = None
    rear_torsion_index: tuple[float, float] | None = None
    front_arb_blade: tuple[float, float] | None = None
    rear_arb_blade: tuple[float, float] | None = None
    front_camber_deg: tuple[float, float] | None = None
    rear_camber_deg: tuple[float, float] | None = None
    brake_bias_pct: tuple[float, float] | None = None
    front_master_cyl_mm: tuple[float, float] | None = None
    rear_master_cyl_mm: tuple[float, float] | None = None
    brake_bias_migration: tuple[float, float] | None = None
    brake_bias_migration_gain: tuple[float, float] | None = None
    rear_diff_preload_nm: tuple[float, float] | None = None
    front_diff_preload_nm: tuple[float, float] | None = None
    diff_clutch_plates: tuple[float, float] | None = None
    tc_slip: tuple[float, float] | None = None
    tc_gain: tuple[float, float] | None = None
    fuel_l: tuple[float, float] | None = None
    fuel_low_warning_l: tuple[float, float] | None = None
    fuel_target_l: tuple[float, float] | None = None
    tyre_pressure_kpa: tuple[float, float] | None = None
    damper_ls_click: tuple[float, float] | None = None
    damper_hs_click: tuple[float, float] | None = None
    damper_hs_slope: tuple[float, float] | None = None
    torsion_bar_turns: tuple[float, float] | None = None
    hybrid_corner_pct: tuple[float, float] | None = None
    front_rh_at_speed_mm: tuple[float, float] | None = None
    rear_rh_at_speed_mm: tuple[float, float] | None = None


def build_garage_ranges_from_schema(data: dict | list) -> GarageRangesFromSchema:
    """Extract legal parameter ranges from a schema JSON.

    These ranges are exactly what iRacing enforces in the garage.
    No more guessing or hardcoding — read them directly.
    """
    if isinstance(data, dict):
        rows = data.get("rows", [])
    else:
        rows = data

    ranges = GarageRangesFromSchema()

    for row in rows:
        label = str(row.get("label", "")).strip()
        tab = row.get("tab")
        section = row.get("section")
        is_mapped = bool(row.get("is_mapped", False))
        range_metric = row.get("range_metric")
        if not is_mapped or not range_metric:
            continue

        r = _parse_range(range_metric)
        if r is None:
            continue

        # Route ranges to the correct field
        if label == "Rear wing angle":
            ranges.wing_angle_deg = r
        elif label == "Pushrod length delta" or label == "Pushrod length offset":
            if section == "Front":
                ranges.front_pushrod_mm = r
            else:
                ranges.rear_pushrod_mm = r
        elif label == "Heave spring":
            if section == "Front":
                ranges.front_heave_index = r
            else:
                ranges.rear_heave_index = r
        elif label == "Heave perch offset":
            if section == "Front":
                ranges.front_heave_perch_mm = r
            else:
                ranges.rear_heave_perch_mm = r
        elif label == "Torsion bar O.D.":
            if section and "Front" in section:
                ranges.front_torsion_index = r
            else:
                ranges.rear_torsion_index = r
        elif label == "ARB blades":
            if section == "Front":
                ranges.front_arb_blade = r
            else:
                ranges.rear_arb_blade = r
        elif label == "Camber":
            if section and "Front" in section:
                ranges.front_camber_deg = r
            else:
                ranges.rear_camber_deg = r
        elif label == "Brake pressure bias":
            ranges.brake_bias_pct = r
        elif label == "Front master cyl.":
            ranges.front_master_cyl_mm = r
        elif label == "Rear master cyl.":
            ranges.rear_master_cyl_mm = r
        elif label == "Bias migration":
            ranges.brake_bias_migration = r
        elif label == "Bias migration gain":
            ranges.brake_bias_migration_gain = r
        elif label == "Preload":
            if section == "Rear Diff Spec":
                ranges.rear_diff_preload_nm = r
            elif section == "Front Diff Spec":
                ranges.front_diff_preload_nm = r
        elif label == "Clutch friction plates":
            ranges.diff_clutch_plates = r
        elif label == "Traction control slip":
            ranges.tc_slip = r
        elif label == "Traction control gain":
            ranges.tc_gain = r
        elif label == "Fuel level":
            ranges.fuel_l = r
        elif label == "Fuel low warning":
            ranges.fuel_low_warning_l = r
        elif label == "Fuel target":
            ranges.fuel_target_l = r
        elif label == "Starting pressure":
            ranges.tyre_pressure_kpa = r
        elif label == "LS comp damping":
            ranges.damper_ls_click = r
        elif label == "HS comp damping":
            ranges.damper_hs_click = r
        elif label == "HS comp damp slope":
            ranges.damper_hs_slope = r
        elif label == "Torsion bar turns":
            ranges.torsion_bar_turns = r
        elif label == "Hybrid rear drive corner pct":
            ranges.hybrid_corner_pct = r
        elif label == "Front RH at speed":
            ranges.front_rh_at_speed_mm = r
        elif label == "Rear RH at speed":
            ranges.rear_rh_at_speed_mm = r

    return ranges


@dataclass
class PhysicsFromSchema:
    """Physics constants derivable from a single schema JSON without any IBT.

    The schema contains iRacing's internal simulation values (the `is_mapped=False`
    rows) which expose the actual physics parameters iRacing uses. These are more
    accurate than anything we estimate from garage settings alone.
    """
    car_name: str = ""
    derivations: list[dict] = field(default_factory=list)

    # Torsion bar calibration: C constant and physical OD range
    front_torsion_c: float | None = None
    front_torsion_od_at_index: dict[int, float] | None = None  # index → physical OD mm

    # Spring rates (from fSideSpringRateNpm)
    front_corner_spring_rate_nmm: float | None = None
    rear_corner_spring_rate_nmm: float | None = None

    # Front heave index → spring rate mapping
    front_heave_rate_at_index: dict[int, float] | None = None

    # Camber (per corner — may be asymmetric)
    front_camber_lf_deg: float | None = None
    front_camber_rf_deg: float | None = None
    rear_camber_lr_deg: float | None = None
    rear_camber_rr_deg: float | None = None

    # Damper click scale (corner dampers)
    damper_click_max: int | None = None

    # Torsion bar turns range (preload)
    torsion_bar_turns_range: tuple[float, float] | None = None

    # Weight distribution (from front pushrod setup)
    weight_dist_front: float | None = None

    def summary(self) -> str:
        lines = [f"Physics from schema: {self.car_name}"]
        for d in self.derivations:
            lines.append(f"  {d['name']:40s} = {d['value']} ({d['method']})")
        return "\n".join(lines)


def derive_physics_from_schema(
    data: dict | list,
    car_name: str | None = None,
) -> PhysicsFromSchema:
    """Extract all physics-relevant information from a schema JSON.

    This is the key function for non-IBT calibration. When you have a garage
    schema JSON (produced by the webapp), this gives you:
    - The actual physical spring rates iRacing is simulating
    - The torsion bar C constant (by combining spring rate + OD index)
    - The complete legal range for every parameter

    Args:
        data: Garage schema JSON (dict or list)
        car_name: Override car name (if not in the schema)

    Returns:
        PhysicsFromSchema with all derived constants
    """
    params = parse_garage_schema_json(data)
    ranges = build_garage_ranges_from_schema(data)

    car = str(car_name or params.car_name or "").lower()
    result = PhysicsFromSchema(car_name=car)

    # ── 1. Physical corner spring rates (from raw internal fields) ──
    # fSideSpringRateNpm is N/m; divide by 1000 to get N/mm
    if params.front_spring_rate_nmm is not None and params.front_spring_rate_nmm > 0:
        rate_nmm = params.front_spring_rate_nmm / 1000.0
        result.front_corner_spring_rate_nmm = rate_nmm
        result.derivations.append({
            "name": "front_corner_spring_rate_nmm",
            "value": f"{rate_nmm:.3f} N/mm",
            "method": "fSideSpringRateNpm / 1000",
            "confidence": 1.0,  # exact — iRacing's own simulation value
            "source": "schema_internal",
        })

    if params.rear_spring_rate_nmm is not None and params.rear_spring_rate_nmm > 0:
        rate_nmm = params.rear_spring_rate_nmm / 1000.0
        result.rear_corner_spring_rate_nmm = rate_nmm
        result.derivations.append({
            "name": "rear_corner_spring_rate_nmm",
            "value": f"{rate_nmm:.3f} N/mm",
            "method": "rSideSpringRateNpm / 1000",
            "confidence": 1.0,
            "source": "schema_internal",
        })

    # ── 2. Torsion bar C constant (from corner spring rate + OD index) ──
    # Physics: k_wheel = C * OD^4  (front torsion bar, MR=1.0 for Ferrari)
    # We know k_wheel = front_corner_spring_rate_nmm and OD index = front_torsion_index
    # But we need physical OD, not index. For Ferrari, index maps to OD range (20-24mm).
    # With C already calibrated (0.001282), we can cross-check: OD = (k/C)^(1/4)
    if result.front_corner_spring_rate_nmm is not None and params.front_torsion_index > 0:
        # For Ferrari: use calibrated C=0.001282 to compute OD at this index
        # This gives us the physical OD → validates/refines the index mapping
        C_calibrated = 0.001282  # Ferrari calibrated value
        k = result.front_corner_spring_rate_nmm
        od_from_k = (k / C_calibrated) ** 0.25

        # Store as a calibration point: (index, OD_mm, k_nmm)
        idx = int(round(params.front_torsion_index))
        if result.front_torsion_od_at_index is None:
            result.front_torsion_od_at_index = {}
        result.front_torsion_od_at_index[idx] = od_from_k
        result.derivations.append({
            "name": f"front_torsion_od_at_index_{idx}",
            "value": f"{od_from_k:.3f} mm",
            "method": f"(k_nmm/C_calibrated)^(1/4) = ({k:.2f}/0.001282)^(1/4)",
            "confidence": 0.90,
            "source": "schema_physics",
            "notes": f"torsion_idx={idx} k={k:.2f}N/mm → OD={od_from_k:.3f}mm "
                     f"(validates index→OD mapping)"
        })

        # Cross-validate C if we have OD from a known calibration
        # OD index 2 = 20.0mm (from Ferrari calibration sweep)
        if 1 <= idx <= 5:
            # Low index → close to calibrated low end
            # Compute C from this data point alone
            od_approx = 20.0 + idx * 0.22  # rough linear from calibration sweep
            c_from_k = k / (od_approx ** 4)
            result.front_torsion_c = c_from_k
            result.derivations.append({
                "name": "front_torsion_c",
                "value": f"{c_from_k:.7f}",
                "method": f"k_nmm / OD_approx^4 = {k:.2f} / {od_approx:.2f}^4",
                "confidence": 0.70,  # moderate — OD approximation
                "source": "schema_physics",
                "notes": "Approximate OD from linear interpolation of index sweep. "
                         "Requires companion corner weight data for full accuracy."
            })

    # ── 3. Per-corner camber ──
    if params.front_camber_lf_deg != 0.0:
        result.front_camber_lf_deg = params.front_camber_lf_deg
        result.front_camber_rf_deg = params.front_camber_rf_deg
        result.derivations.append({
            "name": "front_camber_deg (per corner)",
            "value": f"LF={params.front_camber_lf_deg:.2f}° RF={params.front_camber_rf_deg:.2f}°",
            "method": "Camber (Left Front) + (Right Front) from schema",
            "confidence": 1.0,
            "source": "schema_mapped",
        })
    if params.rear_camber_lr_deg != 0.0:
        result.rear_camber_lr_deg = params.rear_camber_lr_deg
        result.rear_camber_rr_deg = params.rear_camber_rr_deg
        result.derivations.append({
            "name": "rear_camber_deg (per corner)",
            "value": f"LR={params.rear_camber_lr_deg:.2f}° RR={params.rear_camber_rr_deg:.2f}°",
            "method": "Camber (Left Rear) + (Right Rear) from schema",
            "confidence": 1.0,
            "source": "schema_mapped",
        })

    # ── 4. Damper click scale ──
    if ranges.damper_ls_click:
        result.damper_click_max = int(ranges.damper_ls_click[1])
        result.derivations.append({
            "name": "damper_click_max",
            "value": str(result.damper_click_max),
            "method": "LS comp damping range max from schema",
            "confidence": 1.0,
            "source": "schema_range",
        })

    # ── 5. Torsion bar turns range ──
    if ranges.torsion_bar_turns:
        result.torsion_turns_range = ranges.torsion_bar_turns

    # ── 6. Heave index → rate mapping (store this calibration point) ──
    # We know: front heave index N gives physical rate from heave_spring model
    # Cross-referencing with fSideSpringRateNpm gives us the real rate at this index
    if result.front_corner_spring_rate_nmm is not None and params.front_heave_index > 0:
        idx = int(round(params.front_heave_index))
        if result.front_heave_rate_at_index is None:
            result.front_heave_rate_at_index = {}
        result.front_heave_rate_at_index[idx] = result.front_corner_spring_rate_nmm
        result.derivations.append({
            "name": f"front_heave_rate_at_index_{idx}",
            "value": f"{result.front_corner_spring_rate_nmm:.3f} N/mm",
            "method": "fSideSpringRateNpm at known heave index",
            "confidence": 1.0,
            "source": "schema_calibration_point",
            "notes": f"heave_idx={idx} → k={result.front_corner_spring_rate_nmm:.3f}N/mm (exact from simulation)"
        })

    return result


# ─── Persistence ──────────────────────────────────────────────────────────────

_SCHEMA_CALIBRATION_DIR = Path(__file__).resolve().parent.parent / "data" / "garage_schemas"


def save_schema_calibration(
    params: GarageSchemaParams,
    physics: PhysicsFromSchema,
    car_name: str,
) -> Path:
    """Save schema-derived calibration to disk for accumulation."""
    _SCHEMA_CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    car_slug = car_name.lower().replace(" ", "_").replace("-", "_")

    import datetime, hashlib
    # Hash the key setup params for deduplication
    hash_input = (
        f"{params.front_torsion_index:.0f}|{params.rear_torsion_index:.0f}|"
        f"{params.front_heave_index:.0f}|{params.rear_heave_index:.0f}|"
        f"{params.front_arb_size}|{params.front_arb_blade}"
    )
    setup_hash = hashlib.md5(hash_input.encode()).hexdigest()[:10]
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    out = {
        "car_name": car_name,
        "timestamp": timestamp,
        "setup_hash": setup_hash,
        "params": {
            "front_torsion_index": params.front_torsion_index,
            "rear_torsion_index": params.rear_torsion_index,
            "front_heave_index": params.front_heave_index,
            "rear_heave_index": params.rear_heave_index,
            "front_heave_perch_mm": params.front_heave_perch_mm,
            "rear_heave_perch_mm": params.rear_heave_perch_mm,
            "front_spring_rate_nmm": params.front_spring_rate_nmm,
            "rear_spring_rate_nmm": params.rear_spring_rate_nmm,
            "front_arb_size": params.front_arb_size,
            "front_arb_blade": params.front_arb_blade,
            "rear_arb_size": params.rear_arb_size,
            "rear_arb_blade": params.rear_arb_blade,
            "front_camber_lf_deg": params.front_camber_lf_deg,
            "front_camber_rf_deg": params.front_camber_rf_deg,
            "rear_camber_lr_deg": params.rear_camber_lr_deg,
            "rear_camber_rr_deg": params.rear_camber_rr_deg,
            "torsion_bar_turns_lf": params.torsion_bar_turns_lf,
            "torsion_bar_turns_lr": params.torsion_bar_turns_lr,
            "lf_ls_comp": params.lf_ls_comp,
            "lf_hs_comp": params.lf_hs_comp,
            "lf_hs_slope": params.lf_hs_slope,
            "lf_ls_rbd": params.lf_ls_rbd,
            "lf_hs_rbd": params.lf_hs_rbd,
            "lr_ls_comp": params.lr_ls_comp,
            "lr_hs_comp": params.lr_hs_comp,
            "lr_ls_rbd": params.lr_ls_rbd,
            "lr_hs_rbd": params.lr_hs_rbd,
            "brake_bias_pct": params.brake_bias_pct,
            "front_master_cyl_mm": params.front_master_cyl_mm,
            "rear_diff_preload_nm": params.rear_diff_preload_nm,
            "diff_clutch_plates": params.diff_clutch_plates,
            "diff_ramp_option": params.diff_ramp_option,
            "tc_slip": params.tc_slip,
            "tc_gain": params.tc_gain,
            "wing_angle_deg": params.wing_angle_deg,
            "fuel_l": params.fuel_l,
            "tyre_pressure_lf_kpa": params.tyre_pressure_lf_kpa,
            "hf_ls_comp_setting": params.hf_ls_comp_setting,
            "hf_hs_comp_setting": params.hf_hs_comp_setting,
            "hf_ls_rbd_setting": params.hf_ls_rbd_setting,
            "hf_hs_rbd_setting": params.hf_hs_rbd_setting,
        },
        "legal_ranges": {k: list(v) for k, v in params.legal_ranges.items()},
        "physics_derivations": physics.derivations,
        "front_corner_spring_nmm": physics.front_corner_spring_rate_nmm,
        "rear_corner_spring_nmm": physics.rear_corner_spring_rate_nmm,
        "front_torsion_c": physics.front_torsion_c,
        "front_torsion_od_at_index": (
            {str(k): v for k, v in physics.front_torsion_od_at_index.items()}
            if physics.front_torsion_od_at_index else {}
        ),
        "front_heave_rate_at_index": (
            {str(k): v for k, v in physics.front_heave_rate_at_index.items()}
            if physics.front_heave_rate_at_index else {}
        ),
        "damper_click_max": physics.damper_click_max,
    }

    filename = f"{car_slug}_{setup_hash}_{timestamp}.json"
    out_path = _SCHEMA_CALIBRATION_DIR / filename
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)
    return out_path


def load_heave_index_map(car_name: str) -> dict[int, float]:
    """Load accumulated heave index → spring rate N/mm mapping for a car.

    Built from all saved schema calibrations. Each schema adds one data point:
    (heave_index, spring_rate_nmm). With multiple setups, we can fit the full
    index → rate curve instead of relying on the estimated anchor.
    """
    if not _SCHEMA_CALIBRATION_DIR.exists():
        return {}

    car_slug = car_name.lower().replace(" ", "_").replace("-", "_")
    index_map: dict[int, list[float]] = {}

    for f in sorted(_SCHEMA_CALIBRATION_DIR.glob(f"{car_slug}_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            rate_map = data.get("front_heave_rate_at_index", {})
            for idx_str, rate in rate_map.items():
                idx = int(idx_str)
                index_map.setdefault(idx, []).append(float(rate))
        except Exception:
            continue

    # Average rates per index (multiple sessions may have the same index)
    return {idx: sum(rates) / len(rates) for idx, rates in index_map.items()}


def apply_schema_to_car_model(car: Any, physics: PhysicsFromSchema) -> list[str]:
    """Apply schema-derived physics to a car model in-place.

    Unlike auto_calibrate (which needs IBT telemetry), this uses the
    exact simulation values iRacing exposes in the schema JSON — ground truth.

    Returns list of applied changes.
    """
    applied = []

    if physics.front_corner_spring_rate_nmm is not None:
        # The front corner spring rate from fSideSpringRateNpm is what iRacing
        # actually simulates. This replaces the index-based estimate.
        # We store it on the car model as the calibrated rate at the current index.
        # The solver reads front_torsion_c * OD^4; we can back-solve OD if C is known.
        C = getattr(car.corner_spring, "front_torsion_c", 0.001282)
        if C > 0 and physics.front_corner_spring_rate_nmm > 0:
            od_implied = (physics.front_corner_spring_rate_nmm / C) ** 0.25
            # Update torsion OD reference to the actual implied OD
            car.corner_spring.front_torsion_od_ref_mm = od_implied
            applied.append(f"front_torsion_od_ref={od_implied:.3f}mm (from fSideSpringRateNpm)")

    if physics.front_torsion_c is not None and physics.front_torsion_c > 0:
        car.corner_spring.front_torsion_c = physics.front_torsion_c
        applied.append(f"front_torsion_c={physics.front_torsion_c:.7f}")

    if physics.damper_click_max is not None:
        lo, hi = car.damper.ls_comp_range
        if int(physics.damper_click_max) != hi:
            car.damper.ls_comp_range = (lo, int(physics.damper_click_max))
            car.damper.ls_rbd_range = (lo, int(physics.damper_click_max))
            car.damper.hs_comp_range = (lo, int(physics.damper_click_max))
            car.damper.hs_rbd_range = (lo, int(physics.damper_click_max))
            applied.append(f"damper_click_max={physics.damper_click_max}")

    return applied
