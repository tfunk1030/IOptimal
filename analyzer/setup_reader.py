"""Extract the current setup from an IBT file's session info YAML.

The IBT file embeds the complete garage setup under CarSetup.
Values include unit suffixes (e.g., "50 N/mm", "-2.0 deg") which
are stripped and converted to numeric types.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from track_model.ibt_parser import IBTFile


# GT3 canonical names (W5.2 — must be kept in sync with car_model/registry.py
# `_CAR_REGISTRY` GT3 entries and car_model/cars.py GT3 stubs).
GT3_CANONICALS: tuple[str, ...] = (
    "bmw_m4_gt3",
    "aston_martin_vantage_gt3",
    "porsche_992_gt3r",
)
GTP_CANONICALS: tuple[str, ...] = ("bmw", "ferrari", "cadillac", "porsche", "acura")


def _parse_indexed_label(value: Any) -> int:
    """Parse GT3 indexed-string fields like ``"5 (TC SLIP)"`` → ``5``.

    GT3 TC/ABS YAML values come through as ``"X (TC)"``, ``"X (TC SLIP)"``,
    ``"X (TC-LAT)"``, ``"X (ABS)"`` etc. The integer prefix is the actual
    setting; the parenthesised label varies per car.  Falls back to ``_parse_int``
    for plain integers.
    """
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        m = re.match(r"^\s*(-?\d+)\s*\(", value)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return 0
    return _parse_int(value)


def _parse_float(value: str | int | float | None, default: float = 0.0) -> float:
    """Extract a float from a YAML value that may include units."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    # Strip unit suffixes: "50 N/mm" -> "50", "-2.0 deg" -> "-2.0"
    # Also handles "45.50%" -> "45.50"
    s = str(value).strip()
    # Take only the first token (the number)
    parts = s.split()
    if not parts:
        return default
    try:
        return float(parts[0].rstrip("%"))
    except ValueError:
        return default


def _parse_int(value: str | int | float | None, default: int = 0) -> int:
    """Extract an int from a YAML value that may include units."""
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    s = str(value).strip()
    parts = s.split()
    if not parts:
        return default
    try:
        return int(float(parts[0]))
    except ValueError:
        return default


def _parse_defl(value: str | None) -> tuple[float, float]:
    """Parse deflection strings from IBT session info.

    Formats seen in iRacing IBT YAML:
        "15.0 mm 100.0 mm"  → (static=15.0, max=100.0)
        "11.1 mm of 97.7 mm" → (static=11.1, max=97.7)
        "24.5 mm"            → (static=24.5, max=0.0)
    """
    if value is None:
        return (0.0, 0.0)
    s = str(value).strip()
    # Try "X mm of Y mm" first
    if " of " in s:
        parts = s.split(" of ")
        return (_parse_float(parts[0]), _parse_float(parts[1]))
    # Try "X mm Y mm" (two numbers with units, space-separated)
    # Extract all numeric tokens
    tokens = s.replace("mm", "").replace("kPa", "").replace("N/", "").split()
    nums = []
    for t in tokens:
        try:
            nums.append(float(t))
        except ValueError:
            pass
    if len(nums) >= 2:
        return (nums[0], nums[1])
    if len(nums) == 1:
        return (nums[0], 0.0)
    return (0.0, 0.0)


def _get(d: dict, *keys, default=None):
    """Nested dict access: _get(d, 'Chassis', 'Front', 'HeaveSpring')."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, default)
    return d


def _read_gt3_setup(cs: dict, car_canonical: str) -> dict[str, Any]:
    """GT3 YAML extractor (W5.2).

    Maps the GT3 IBT session-info layout to the canonical ``CurrentSetup``
    field set.  GT3 cars (BMW M4 GT3, Aston Martin Vantage GT3 EVO,
    Porsche 911 GT3 R 992) share a per-axle damper / per-corner spring
    layout but differ in the location of brake / fuel / ARB blocks:

      * BMW       — ``Chassis.FrontBrakes`` (paired front toe + ARB blades)
      * Aston     — ``Chassis.FrontBrakesLights`` (FarbBlades / RarbBlades)
      * Porsche   — ``Chassis.FrontBrakesLights`` (ArbSetting integer; fuel here)

    Returns a dict of ``CurrentSetup``-attribute keys → values.  Heave/third
    fields and torsion-bar fields are intentionally absent (default zero) — GT3
    cars have no heave element nor torsion bars (per
    ``SuspensionArchitecture.GT3_COIL_4WHEEL`` invariants).
    """
    chassis = cs.get("Chassis", {})
    tires_aero = cs.get("TiresAero", {})
    dampers = cs.get("Dampers", {})

    lf = chassis.get("LeftFront", {})
    rf = chassis.get("RightFront", {})
    lr = chassis.get("LeftRear", {})
    rr = chassis.get("RightRear", {})
    rear = chassis.get("Rear", {})
    in_car = chassis.get("InCarAdjustments", {})
    diff = chassis.get("GearsDifferential", {})

    # Front section: BMW = "FrontBrakes"; Aston / Porsche = "FrontBrakesLights".
    front_brakes = chassis.get("FrontBrakes") or chassis.get("FrontBrakesLights") or {}

    # Aero balance calculator: BMW / Porsche = "AeroBalanceCalc"; Aston = "AeroBalanceCalculator".
    aero_calc = tires_aero.get("AeroBalanceCalc") or tires_aero.get("AeroBalanceCalculator") or {}

    # Wing angle: BMW & Porsche use "WingSetting"; Aston uses "RearWingAngle".
    wing_angle = (
        aero_calc.get("WingSetting")
        or aero_calc.get("RearWingAngle")
        or rear.get("WingAngle")
        or rear.get("WingSetting")
        or rear.get("RearWingAngle")
    )

    # ARB encoding varies by car:
    #   BMW       — front_brakes.ArbBlades / rear.ArbBlades   (paired blades)
    #   Aston     — front_brakes.FarbBlades / rear.RarbBlades
    #   Porsche   — front_brakes.ArbSetting / rear.RarbSetting (integer setting)
    if car_canonical == "porsche_992_gt3r":
        front_arb_value = front_brakes.get("ArbSetting")
        rear_arb_value = rear.get("RarbSetting")
    elif car_canonical == "aston_martin_vantage_gt3":
        front_arb_value = front_brakes.get("FarbBlades")
        rear_arb_value = rear.get("RarbBlades")
    else:  # bmw_m4_gt3
        front_arb_value = front_brakes.get("ArbBlades")
        rear_arb_value = rear.get("ArbBlades")

    # Per-axle dampers (8 channels total; no per-corner damper section in GT3 YAML).
    front_damp = dampers.get("FrontDampers", {})
    rear_damp = dampers.get("RearDampers", {})

    # Fuel: BMW + Aston put it under Chassis.Rear.FuelLevel.
    # Porsche puts it under Chassis.FrontBrakesLights.FuelLevel.
    fuel_value = rear.get("FuelLevel") or front_brakes.get("FuelLevel")

    # Toe — front is paired ("TotalToeIn") under FrontBrakes / FrontBrakesLights.
    # Rear toe:
    #   BMW + Aston — per-wheel ToeIn on LeftRear / RightRear
    #   Porsche     — paired Rear.TotalToeIn ONLY (no per-wheel ToeIn keys)
    if car_canonical == "porsche_992_gt3r":
        rear_toe = _parse_float(rear.get("TotalToeIn"))
    else:
        lr_toe = _parse_float(lr.get("ToeIn"))
        rr_toe = _parse_float(rr.get("ToeIn"))
        rear_toe = (lr_toe + rr_toe) / 2.0 if (lr_toe or rr_toe) else _parse_float(rear.get("TotalToeIn"))

    # Per-corner spring rates (the four corner coils).  Average left/right.
    lf_spring = _parse_float(lf.get("SpringRate"))
    rf_spring = _parse_float(rf.get("SpringRate"))
    lr_spring = _parse_float(lr.get("SpringRate"))
    rr_spring = _parse_float(rr.get("SpringRate"))

    # Bump rubber gaps (per corner) — average left/right.
    lf_bump = _parse_float(lf.get("BumpRubberGap"))
    rf_bump = _parse_float(rf.get("BumpRubberGap"))
    lr_bump = _parse_float(lr.get("BumpRubberGap"))
    rr_bump = _parse_float(rr.get("BumpRubberGap"))

    return {
        # --- Aero ---
        "wing_angle_deg": _parse_float(wing_angle),
        "front_rh_at_speed_mm": _parse_float(aero_calc.get("FrontRhAtSpeed")),
        "rear_rh_at_speed_mm": _parse_float(aero_calc.get("RearRhAtSpeed")),
        "df_balance_pct": _parse_float(aero_calc.get("FrontDownforce")),
        # GT3 YAML does not expose an L/D ratio.
        "ld_ratio": 0.0,

        # --- Ride heights (avg L/R) ---
        "static_front_rh_mm": (_parse_float(lf.get("RideHeight")) + _parse_float(rf.get("RideHeight"))) / 2.0,
        "static_rear_rh_mm": (_parse_float(lr.get("RideHeight")) + _parse_float(rr.get("RideHeight"))) / 2.0,

        # --- Corner springs (front + rear axle averages from per-corner SpringRate) ---
        # GT3 uses 4 coil-overs.  We surface front/rear axle spring rate via
        # the legacy ``rear_spring_nmm`` field (already used for legacy rear
        # coil cars) and reserve the fronts on a new contract: avg of LF/RF
        # SpringRate is stored to ``front_corner_spring_nmm`` (added to
        # CurrentSetup) — fall back to overloading ``rear_spring_nmm`` for
        # the rear axle.
        "front_corner_spring_nmm": (lf_spring + rf_spring) / 2.0 if (lf_spring or rf_spring) else 0.0,
        "rear_spring_nmm": (lr_spring + rr_spring) / 2.0 if (lr_spring or rr_spring) else 0.0,

        # --- Bump rubber gaps (per corner; avg by axle for compactness) ---
        "lf_bump_rubber_gap_mm": lf_bump,
        "rf_bump_rubber_gap_mm": rf_bump,
        "lr_bump_rubber_gap_mm": lr_bump,
        "rr_bump_rubber_gap_mm": rr_bump,

        # --- ARBs ---
        # ``front_arb_blade`` is the canonical numeric.  ``front_arb_setting``
        # is populated for Porsche where the garage shows a single integer.
        "front_arb_blade": _parse_int(front_arb_value),
        "rear_arb_blade": _parse_int(rear_arb_value),
        "front_arb_setting": _parse_int(front_arb_value) if car_canonical == "porsche_992_gt3r" else 0,
        "rear_arb_setting": _parse_int(rear_arb_value) if car_canonical == "porsche_992_gt3r" else 0,
        # GT3 YAML has no ArbSize labels — leave empty (matches GTP path when absent).
        "front_arb_size": "",
        "rear_arb_size": "",

        # --- Geometry ---
        "front_camber_deg": (_parse_float(lf.get("Camber")) + _parse_float(rf.get("Camber"))) / 2.0,
        "rear_camber_deg": (_parse_float(lr.get("Camber")) + _parse_float(rr.get("Camber"))) / 2.0,
        "front_toe_mm": _parse_float(front_brakes.get("TotalToeIn")),
        "rear_toe_mm": rear_toe,

        # --- Per-axle dampers (8 channels) ---
        "front_ls_comp": _parse_int(front_damp.get("LowSpeedCompressionDamping")),
        "front_hs_comp": _parse_int(front_damp.get("HighSpeedCompressionDamping")),
        "front_ls_rbd": _parse_int(front_damp.get("LowSpeedReboundDamping")),
        "front_hs_rbd": _parse_int(front_damp.get("HighSpeedReboundDamping")),
        "rear_ls_comp": _parse_int(rear_damp.get("LowSpeedCompressionDamping")),
        "rear_hs_comp": _parse_int(rear_damp.get("HighSpeedCompressionDamping")),
        "rear_ls_rbd": _parse_int(rear_damp.get("LowSpeedReboundDamping")),
        "rear_hs_rbd": _parse_int(rear_damp.get("HighSpeedReboundDamping")),
        # GT3 has no HS comp slope channel — leave 0.
        "front_hs_slope": 0,
        "rear_hs_slope": 0,

        # --- Brakes / Diff / TC ---
        "brake_bias_pct": _parse_float(in_car.get("BrakePressureBias")),
        "front_master_cyl_mm": _parse_float(front_brakes.get("FrontMasterCyl")),
        "rear_master_cyl_mm": _parse_float(front_brakes.get("RearMasterCyl")),
        "pad_compound": str(front_brakes.get("BrakePads", "") or ""),
        "splitter_height_mm": _parse_float(front_brakes.get("CenterFrontSplitterHeight")),
        "diff_preload_nm": _parse_float(diff.get("DiffPreload")),
        "diff_clutch_plates": _parse_int(diff.get("FrictionFaces")),
        "gear_stack": str(diff.get("GearStack", "") or ""),
        # GT3 has a single TcSetting integer (label varies: "X (TC)", "X (TC SLIP)", "X (TC-LAT)").
        "tc_setting": _parse_indexed_label(in_car.get("TcSetting")),
        "abs_setting": _parse_indexed_label(in_car.get("AbsSetting")),
        # Legacy ``tc_gain`` carries the same integer for backward-compat with
        # solver/diagnose code that expects ``tc_gain`` to be populated.
        "tc_gain": _parse_indexed_label(in_car.get("TcSetting")),
        "tc_slip": 0,
        "front_weight_dist_pct": _parse_float(in_car.get("FWtdist")),
        "cross_weight_pct": _parse_float(in_car.get("CrossWeight")),
        # Aston / Porsche specific extras.
        # Aston ``ThrottleResponse: "4 (RED)"`` lives under InCarAdjustments;
        # Porsche ``ThrottleShapeSetting: 3`` (plain int) also under InCarAdjustments.
        "epas_setting": _parse_indexed_label(in_car.get("EpasSetting")) if in_car.get("EpasSetting") is not None else 0,
        "throttle_map": _parse_indexed_label(
            in_car.get("ThrottleResponse") if in_car.get("ThrottleResponse") is not None
            else in_car.get("ThrottleShapeSetting")
        ) if (in_car.get("ThrottleResponse") is not None or in_car.get("ThrottleShapeSetting") is not None) else 0,

        # --- Fuel ---
        "fuel_l": _parse_float(fuel_value),

        # --- Corner weights (display) ---
        "lf_corner_weight_n": _parse_float(lf.get("CornerWeight")),
        "rf_corner_weight_n": _parse_float(rf.get("CornerWeight")),
        "lr_corner_weight_n": _parse_float(lr.get("CornerWeight")),
        "rr_corner_weight_n": _parse_float(rr.get("CornerWeight")),
    }


@dataclass
class CurrentSetup:
    """All garage-settable parameters extracted from IBT session info."""

    source: str  # "ibt" or "solver_json"

    # --- Aero ---
    wing_angle_deg: float = 0.0
    front_rh_at_speed_mm: float = 0.0     # AeroCalculator FrontRhAtSpeed
    rear_rh_at_speed_mm: float = 0.0      # AeroCalculator RearRhAtSpeed
    df_balance_pct: float = 0.0
    ld_ratio: float = 0.0

    # --- Ride heights & pushrod ---
    static_front_rh_mm: float = 0.0       # avg of LF/RF RideHeight
    static_rear_rh_mm: float = 0.0        # avg of LR/RR RideHeight
    front_pushrod_mm: float = 0.0
    rear_pushrod_mm: float = 0.0

    # --- Heave / Third ---
    front_heave_nmm: float = 0.0
    front_heave_perch_mm: float = 0.0
    rear_third_nmm: float = 0.0
    rear_third_perch_mm: float = 0.0

    # --- Corner springs ---
    front_torsion_od_mm: float = 0.0
    rear_spring_nmm: float = 0.0
    rear_spring_perch_mm: float = 0.0
    # GT3: per-axle paired-coil front spring rate (avg of LF/RF SpringRate).
    # Zero on GTP cars (which use heave/torsion-bar/roll-spring at the front).
    front_corner_spring_nmm: float = 0.0
    # GT3: per-corner bump rubber gaps (mm).
    lf_bump_rubber_gap_mm: float = 0.0
    rf_bump_rubber_gap_mm: float = 0.0
    lr_bump_rubber_gap_mm: float = 0.0
    rr_bump_rubber_gap_mm: float = 0.0
    # GT3: BMW M4 GT3 / Aston Vantage / Porsche 992 GT3 R have a front
    # splitter height adjustment under FrontBrakes(Lights).CenterFrontSplitterHeight.
    splitter_height_mm: float = 0.0

    # --- ARBs ---
    front_arb_size: str = ""
    front_arb_blade: int = 0
    rear_arb_size: str = ""
    rear_arb_blade: int = 0
    # GT3 Porsche-only: integer ARB setting (1..N) — distinct from blade idx.
    front_arb_setting: int = 0
    rear_arb_setting: int = 0

    # --- Geometry ---
    front_camber_deg: float = 0.0
    rear_camber_deg: float = 0.0
    front_toe_mm: float = 0.0
    rear_toe_mm: float = 0.0

    # --- Corner springs (rear torsion bar for ORECA cars) ---
    rear_torsion_od_mm: float = 0.0       # ORECA: rear also uses torsion bars

    # --- Dampers (front = LF or FrontHeave, rear = LR or RearHeave) ---
    front_ls_comp: int = 0
    front_ls_rbd: int = 0
    front_hs_comp: int = 0
    front_hs_rbd: int = 0
    front_hs_slope: int = 0
    rear_ls_comp: int = 0
    rear_ls_rbd: int = 0
    rear_hs_comp: int = 0
    rear_hs_rbd: int = 0
    rear_hs_slope: int = 0
    # Roll dampers (ORECA heave+roll architecture, also Porsche Multimatic)
    front_roll_ls: int = 0
    front_roll_hs: int = 0
    front_roll_hs_slope: int = 0      # Porsche front roll has HS slope
    rear_roll_ls: int = 0
    rear_roll_hs: int = 0
    # Rear 3rd dampers (Porsche Multimatic)
    rear_3rd_ls_comp: int = 0
    rear_3rd_hs_comp: int = 0
    rear_3rd_ls_rbd: int = 0
    rear_3rd_hs_rbd: int = 0
    # Front roll spring (Porsche Multimatic)
    front_roll_spring_nmm: float = 0.0
    front_roll_perch_mm: float = 0.0

    # --- Brakes / Diff / TC ---
    brake_bias_pct: float = 0.0
    brake_bias_target: float = 0.0
    brake_bias_migration: float = 0.0
    brake_bias_migration_gain: float = 0.0
    front_master_cyl_mm: float = 0.0
    rear_master_cyl_mm: float = 0.0
    pad_compound: str = ""
    front_diff_preload_nm: float = 0.0  # Ferrari only (has front diff)
    diff_preload_nm: float = 0.0        # Rear diff preload
    diff_ramp_angles: str = ""
    diff_clutch_plates: int = 0
    tc_gain: int = 0
    tc_slip: int = 0
    # GT3: single-integer settings parsed from "X (TC)" / "X (ABS)" labels.
    tc_setting: int = 0
    abs_setting: int = 0
    front_weight_dist_pct: float = 0.0
    cross_weight_pct: float = 0.0
    epas_setting: int = 0      # Aston only
    throttle_map: int = 0      # Aston ThrottleResponse / Porsche ThrottleShapeSetting
    fuel_l: float = 0.0
    fuel_low_warning_l: float = 0.0
    fuel_target_l: float = 0.0
    gear_stack: str = ""
    speed_in_first_kph: float = 0.0
    speed_in_second_kph: float = 0.0
    speed_in_third_kph: float = 0.0
    speed_in_fourth_kph: float = 0.0
    speed_in_fifth_kph: float = 0.0
    speed_in_sixth_kph: float = 0.0
    speed_in_seventh_kph: float = 0.0
    hybrid_rear_drive_enabled: str = ""
    hybrid_rear_drive_corner_pct: float = 0.0
    roof_light_color: str = ""

    # --- iRacing-computed garage display values ---
    # These are computed by iRacing from the settable parameters above.
    # Extracted from IBT session info for model calibration ground truth.
    torsion_bar_turns: float = 0.0
    rear_torsion_bar_turns: float = 0.0
    torsion_bar_defl_mm: float = 0.0      # front (LF)
    rear_torsion_bar_defl_mm: float = 0.0  # rear (LR) — Ferrari has rear TB
    front_shock_defl_static_mm: float = 0.0
    front_shock_defl_max_mm: float = 0.0
    rear_shock_defl_static_mm: float = 0.0
    rear_shock_defl_max_mm: float = 0.0
    heave_spring_defl_static_mm: float = 0.0
    heave_spring_defl_max_mm: float = 0.0
    heave_slider_defl_static_mm: float = 0.0
    heave_slider_defl_max_mm: float = 0.0
    rear_spring_defl_static_mm: float = 0.0
    rear_spring_defl_max_mm: float = 0.0
    third_spring_defl_static_mm: float = 0.0
    third_spring_defl_max_mm: float = 0.0
    third_slider_defl_static_mm: float = 0.0
    third_slider_defl_max_mm: float = 0.0
    lf_corner_weight_n: float = 0.0
    rf_corner_weight_n: float = 0.0
    lr_corner_weight_n: float = 0.0
    rr_corner_weight_n: float = 0.0
    adapter_name: str = ""
    extraction_attempts: list[dict[str, object]] = field(default_factory=list, repr=False)
    unresolved_fields: list[str] = field(default_factory=list)
    conflicted_fields: list[str] = field(default_factory=list)
    decode_warnings: list[str] = field(default_factory=list)
    raw_indexed_fields: dict[str, float] = field(default_factory=dict)
    sto_car_id: str = ""
    sto_sha256: str = ""
    sto_provider_name: str = ""
    sto_notes_text: str = ""
    raw_sto_metadata: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_sto(cls, path: str | Path) -> CurrentSetup:
        """Parse the current setup from a version-3 binary STO file."""

        from analyzer.sto_adapters import build_current_setup_fields
        from analyzer.sto_binary import decode_sto

        decoded = decode_sto(path)
        adapted = build_current_setup_fields(decoded)
        return cls(
            source="sto",
            adapter_name=adapted.adapter_name,
            extraction_attempts=[
                {
                    "path": "BinaryStoV3",
                    "status": "ok",
                    "source_path": str(decoded.source_path),
                }
            ],
            unresolved_fields=list(adapted.unresolved_fields),
            conflicted_fields=list(adapted.conflicted_fields),
            decode_warnings=list(adapted.decode_warnings),
            raw_indexed_fields=dict(adapted.raw_indexed_fields),
            sto_car_id=decoded.car_id,
            sto_sha256=decoded.sha256,
            sto_provider_name=decoded.provider_name,
            sto_notes_text=decoded.notes_text,
            raw_sto_metadata=dict(adapted.raw_sto_metadata),
            **adapted.values,
        )

    @classmethod
    def from_ibt(cls, ibt: IBTFile, car_canonical: str | None = None) -> CurrentSetup:
        """Parse the current setup from an IBT file's session info.

        The session info is a YAML dict parsed by the IBT parser.
        CarSetup is structured as nested dicts:
            CarSetup.TiresAero.AeroSettings.RearWingAngle
            CarSetup.Chassis.Front.HeaveSpring
            CarSetup.Chassis.LeftFront.TorsionBarOD
            etc.

        Args:
            ibt: IBT file object with parsed session info
            car_canonical: Canonical car name ("bmw", "ferrari", "cadillac", etc.)
                           When provided, bypasses structural layout detection for
                           adapter_name assignment. Required for Cadillac/Porsche
                           which share the same IBT structure as BMW.
        """
        si = ibt.session_info
        if not isinstance(si, dict):
            raise ValueError("IBT session info is not parsed (pyyaml missing?)")

        cs = si.get("CarSetup", {})

        # W5.2 — dispatch GT3 cars to the dedicated reader.  GT3 layout is
        # structurally distinct from any GTP car (per-axle dampers, no
        # heave/third element, no Systems block) so reuse of the GTP parsing
        # path below would yield zeros for nearly every field.
        canonical_lower = car_canonical.lower() if car_canonical else ""
        if canonical_lower in GT3_CANONICALS:
            gt3_values = _read_gt3_setup(cs, canonical_lower)
            attempts = [
                {"path": "CarSetup.Chassis", "status": "ok" if cs.get("Chassis") else "missing"},
                {"path": "CarSetup.TiresAero", "status": "ok" if cs.get("TiresAero") else "missing"},
                {"path": "CarSetup.Dampers", "status": "ok" if cs.get("Dampers") else "missing"},
            ]
            return cls(
                source="ibt",
                adapter_name=canonical_lower,
                extraction_attempts=attempts,
                **gt3_values,
            )

        chassis = cs.get("Chassis", {})
        tires_aero = cs.get("TiresAero", {})

        front = chassis.get("Front", {})
        rear = chassis.get("Rear", {})
        lf = chassis.get("LeftFront", {})
        rf = chassis.get("RightFront", {})
        lr = chassis.get("LeftRear", {})
        rr = chassis.get("RightRear", {})

        aero_settings = tires_aero.get("AeroSettings", {})
        aero_calc = tires_aero.get("AeroCalculator", {})

        # Ferrari uses "Systems" top-level key; BMW uses "BrakesDriveUnit"
        systems = cs.get("Systems", {})
        brakes = cs.get("BrakesDriveUnit", {})
        brake_spec = systems.get("BrakeSpec", {}) or brakes.get("BrakeSpec", {})
        diff_spec = systems.get("RearDiffSpec", {}) or brakes.get("RearDiffSpec", {})
        front_diff_spec = systems.get("FrontDiffSpec", {}) or brakes.get("FrontDiffSpec", {})
        tc = systems.get("TractionControl", {}) or brakes.get("TractionControl", {})
        fuel = systems.get("Fuel", {}) or brakes.get("Fuel", {})
        gear_ratios = systems.get("GearRatios", {}) or brakes.get("GearRatios", {})
        hybrid_config = systems.get("HybridConfig") or brakes.get("HybridConfig") or {}
        lighting = systems.get("Lighting", {}) or brakes.get("Lighting", {})

        # Damper layout varies by chassis:
        #   BMW/Cadillac (Dallara): per-corner under Chassis.LeftFront etc.
        #   Ferrari: per-corner under Dampers.LeftFrontDamper etc.
        #   Acura (ORECA): heave+roll under Dampers.FrontHeave/FrontRoll etc.
        #   Porsche (Multimatic): HYBRID — front heave+roll under Dampers,
        #     rear per-corner under Chassis.LeftRear/RightRear, rear 3rd separate.
        dampers = cs.get("Dampers", {})
        front_heave_damp = dampers.get("FrontHeave", {})
        rear_heave_damp = dampers.get("RearHeave", {})
        front_roll_damp = dampers.get("FrontRoll", {})
        rear_roll_damp = dampers.get("RearRoll", {})
        rear_3rd_damp = dampers.get("Rear3Rd", {}) or dampers.get("Rear3rd", {}) or dampers.get("RearThird", {})
        is_heave_roll_layout = bool(front_heave_damp)

        # Porsche 963 (Multimatic) has a hybrid layout:
        #   Front: heave+roll dampers (like ORECA)
        #   Rear: per-corner dampers (like BMW) + separate 3rd dampers
        # Detect: front heave exists AND rear per-corner exists (LeftRear has damper keys)
        is_porsche_layout = (
            is_heave_roll_layout
            and car_canonical
            and car_canonical.lower() == "porsche"
        )

        if is_porsche_layout:
            # Porsche (Multimatic): front from Dampers.FrontHeave,
            # rear from Dampers.LeftRear/RightRear (NOT Chassis.LeftRear!)
            lf_damp = front_heave_damp
            rf_damp = front_heave_damp
            lr_damp = dampers.get("LeftRear", lr)   # Dampers.LeftRear
            rr_damp = dampers.get("RightRear", rr)  # Dampers.RightRear
        elif is_heave_roll_layout:
            # ORECA: heave dampers carry the primary LS/HS comp+rbd+slope
            lf_damp = front_heave_damp
            rf_damp = front_heave_damp
            lr_damp = rear_heave_damp
            rr_damp = rear_heave_damp
        else:
            lf_damp = dampers.get("LeftFrontDamper", lf)
            rf_damp = dampers.get("RightFrontDamper", rf)
            lr_damp = dampers.get("LeftRearDamper", lr)
            rr_damp = dampers.get("RightRearDamper", rr)

        # Average left/right for symmetric parameters
        def avg_f(key: str) -> float:
            return (_parse_float(lf.get(key)) + _parse_float(rf.get(key))) / 2.0

        def avg_r(key: str) -> float:
            return (_parse_float(lr.get(key)) + _parse_float(rr.get(key))) / 2.0

        is_ferrari_layout = (not is_heave_roll_layout) and (bool(systems) or bool(dampers))
        attempts = [
            {"path": "CarSetup.TiresAero", "status": "ok" if tires_aero else "missing"},
            {"path": "CarSetup.Chassis", "status": "ok" if chassis else "missing"},
            {
                "path": "CarSetup.Systems",
                "status": "ok" if systems else "missing",
            },
            {
                "path": "CarSetup.BrakesDriveUnit",
                "status": "ok" if brakes else "missing",
            },
        ]

        setup = cls(
            source="ibt",

            # Aero
            wing_angle_deg=_parse_float(aero_settings.get("RearWingAngle")),
            front_rh_at_speed_mm=_parse_float(aero_calc.get("FrontRhAtSpeed")),
            rear_rh_at_speed_mm=_parse_float(aero_calc.get("RearRhAtSpeed")),
            df_balance_pct=_parse_float(aero_calc.get("DownforceBalance")),
            ld_ratio=_parse_float(aero_calc.get("LD")),

            # Ride heights (average left/right)
            static_front_rh_mm=avg_f("RideHeight"),
            static_rear_rh_mm=avg_r("RideHeight"),
            # Ferrari uses "PushrodLengthDelta", BMW uses "PushrodLengthOffset"
            front_pushrod_mm=_parse_float(front.get("PushrodLengthOffset") or front.get("PushrodLengthDelta")),
            rear_pushrod_mm=_parse_float(rear.get("PushrodLengthOffset") or rear.get("PushrodLengthDelta")),

            # Heave / Third — Ferrari rear uses HeaveSpring (no ThirdSpring)
            front_heave_nmm=_parse_float(front.get("HeaveSpring")),
            front_heave_perch_mm=_parse_float(front.get("HeavePerchOffset")),
            rear_third_nmm=_parse_float(rear.get("ThirdSpring") or rear.get("HeaveSpring")),
            rear_third_perch_mm=_parse_float(rear.get("ThirdPerchOffset") or rear.get("HeavePerchOffset")),

            # Corner springs (use LF/LR as representative)
            # Ferrari/Acura rear uses TorsionBarOD instead of coil SpringRate
            front_torsion_od_mm=_parse_float(lf.get("TorsionBarOD")),
            rear_spring_nmm=_parse_float(lr.get("SpringRate")) if lr.get("SpringRate") else 0.0,
            rear_spring_perch_mm=_parse_float(lr.get("SpringPerchOffset")),
            rear_torsion_od_mm=_parse_float(lr.get("TorsionBarOD")) if lr.get("TorsionBarOD") and not lr.get("SpringRate") else 0.0,

            # ARBs — Porsche uses ArbSetting/ArbAdj instead of ArbSize/ArbBlades
            front_arb_size=str(front.get("ArbSize", "") or front.get("ArbSetting", "")),
            front_arb_blade=_parse_int(front.get("ArbBlades") or front.get("ArbAdj")),
            rear_arb_size=str(rear.get("ArbSize", "")),
            rear_arb_blade=_parse_int(rear.get("ArbBlades") or rear.get("ArbAdj")),

            # Geometry (average left/right)
            front_camber_deg=avg_f("Camber"),
            rear_camber_deg=avg_r("Camber"),
            front_toe_mm=_parse_float(front.get("ToeIn")),
            # Acura (ORECA) stores rear toe under Chassis.Rear.ToeIn, not per-corner
            rear_toe_mm=(
                _parse_float(lr.get("ToeIn")) + _parse_float(rr.get("ToeIn"))
            ) / 2.0 or _parse_float(rear.get("ToeIn")),

            # Dampers (use LF for front, LR for rear)
            # Ferrari: Dampers.LeftFrontDamper; BMW: Chassis.LeftFront
            front_ls_comp=_parse_int(lf_damp.get("LsCompDamping")),
            front_ls_rbd=_parse_int(lf_damp.get("LsRbdDamping")),
            front_hs_comp=_parse_int(lf_damp.get("HsCompDamping")),
            front_hs_rbd=_parse_int(lf_damp.get("HsRbdDamping")),
            front_hs_slope=_parse_int(lf_damp.get("HsCompDampSlope")),
            rear_ls_comp=_parse_int(lr_damp.get("LsCompDamping")),
            rear_ls_rbd=_parse_int(lr_damp.get("LsRbdDamping")),
            rear_hs_comp=_parse_int(lr_damp.get("HsCompDamping")),
            rear_hs_rbd=_parse_int(lr_damp.get("HsRbdDamping")),
            rear_hs_slope=_parse_int(lr_damp.get("HsCompDampSlope")),
            # Roll dampers (ORECA heave+roll layout, also Porsche Multimatic)
            front_roll_ls=_parse_int(front_roll_damp.get("LsDamping")),
            front_roll_hs=_parse_int(front_roll_damp.get("HsDamping")),
            front_roll_hs_slope=_parse_int(front_roll_damp.get("HsDampSlope") or front_roll_damp.get("HsCompDampSlope")),
            rear_roll_ls=_parse_int(rear_roll_damp.get("LsDamping")),
            rear_roll_hs=_parse_int(rear_roll_damp.get("HsDamping")),
            # Rear 3rd dampers (Porsche Multimatic)
            rear_3rd_ls_comp=_parse_int(rear_3rd_damp.get("LsCompDamping")),
            rear_3rd_hs_comp=_parse_int(rear_3rd_damp.get("HsCompDamping")),
            rear_3rd_ls_rbd=_parse_int(rear_3rd_damp.get("LsRbdDamping")),
            rear_3rd_hs_rbd=_parse_int(rear_3rd_damp.get("HsRbdDamping")),
            # Front roll spring (Porsche Multimatic)
            front_roll_spring_nmm=_parse_float(front.get("RollSpring")),
            front_roll_perch_mm=_parse_float(front.get("RollPerchOffset")),

            # Brakes / Diff / TC
            brake_bias_pct=_parse_float(brake_spec.get("BrakePressureBias")),
            brake_bias_target=_parse_float(brake_spec.get("BrakeBiasTarget")),
            brake_bias_migration=_parse_float(
                brake_spec.get("BrakeBiasMigration") or brake_spec.get("BiasMigration")
            ),
            brake_bias_migration_gain=_parse_float(brake_spec.get("BiasMigrationGain")),
            front_master_cyl_mm=_parse_float(brake_spec.get("FrontMasterCyl")),
            rear_master_cyl_mm=_parse_float(brake_spec.get("RearMasterCyl")),
            pad_compound=str(brake_spec.get("PadCompound", "") or ""),
            front_diff_preload_nm=_parse_float(front_diff_spec.get("Preload")),
            diff_preload_nm=_parse_float(diff_spec.get("Preload")),
            diff_ramp_angles=str(diff_spec.get("CoastDriveRampAngles", "") or diff_spec.get("CoastDriveRampOptions", "") or diff_spec.get("DiffRampAngles", "")),
            diff_clutch_plates=_parse_int(diff_spec.get("ClutchFrictionPlates")),
            tc_gain=_parse_int(tc.get("TractionControlGain")),
            tc_slip=_parse_int(tc.get("TractionControlSlip")),
            fuel_l=_parse_float(fuel.get("FuelLevel")) or _parse_float(rear.get("FuelLevel")),
            fuel_low_warning_l=_parse_float(fuel.get("FuelLowWarning")),
            fuel_target_l=_parse_float(fuel.get("FuelTarget")),
            gear_stack=str(gear_ratios.get("GearStack", "") or ""),
            speed_in_first_kph=_parse_float(gear_ratios.get("SpeedInFirst")),
            speed_in_second_kph=_parse_float(gear_ratios.get("SpeedInSecond")),
            speed_in_third_kph=_parse_float(gear_ratios.get("SpeedInThird")),
            speed_in_fourth_kph=_parse_float(gear_ratios.get("SpeedInFourth")),
            speed_in_fifth_kph=_parse_float(gear_ratios.get("SpeedInFifth")),
            speed_in_sixth_kph=_parse_float(gear_ratios.get("SpeedInSixth")),
            speed_in_seventh_kph=_parse_float(gear_ratios.get("SpeedInSeventh")),
            hybrid_rear_drive_enabled=str(hybrid_config.get("HybridRearDriveEnabled", "") or ""),
            hybrid_rear_drive_corner_pct=_parse_float(hybrid_config.get("HybridRearDriveCornerPct")),
            roof_light_color=str(lighting.get("RoofIdLightColor", "") or ""),

            # iRacing-computed display values (ground truth for calibration)
            torsion_bar_turns=_parse_float(lf.get("TorsionBarTurns")),
            rear_torsion_bar_turns=_parse_float(lr.get("TorsionBarTurns")),
            torsion_bar_defl_mm=_parse_float(lf.get("TorsionBarDefl")),
            rear_torsion_bar_defl_mm=_parse_float(lr.get("TorsionBarDefl")),
            front_shock_defl_static_mm=_parse_defl(lf.get("ShockDefl"))[0],
            front_shock_defl_max_mm=_parse_defl(lf.get("ShockDefl"))[1],
            rear_shock_defl_static_mm=_parse_defl(lr.get("ShockDefl"))[0],
            rear_shock_defl_max_mm=_parse_defl(lr.get("ShockDefl"))[1],
            heave_spring_defl_static_mm=_parse_defl(front.get("HeaveSpringDefl"))[0],
            heave_spring_defl_max_mm=_parse_defl(front.get("HeaveSpringDefl"))[1],
            heave_slider_defl_static_mm=_parse_defl(front.get("HeaveSliderDefl"))[0],
            heave_slider_defl_max_mm=_parse_defl(front.get("HeaveSliderDefl"))[1],
            rear_spring_defl_static_mm=_parse_defl(lr.get("SpringDefl"))[0],
            rear_spring_defl_max_mm=_parse_defl(lr.get("SpringDefl"))[1],
            third_spring_defl_static_mm=_parse_defl(rear.get("ThirdSpringDefl") or rear.get("HeaveSpringDefl"))[0],
            third_spring_defl_max_mm=_parse_defl(rear.get("ThirdSpringDefl") or rear.get("HeaveSpringDefl"))[1],
            third_slider_defl_static_mm=_parse_defl(rear.get("ThirdSliderDefl") or rear.get("HeaveSliderDefl"))[0],
            third_slider_defl_max_mm=_parse_defl(rear.get("ThirdSliderDefl") or rear.get("HeaveSliderDefl"))[1],
            lf_corner_weight_n=_parse_float(lf.get("CornerWeight")),
            rf_corner_weight_n=_parse_float(rf.get("CornerWeight")),
            lr_corner_weight_n=_parse_float(lr.get("CornerWeight")),
            rr_corner_weight_n=_parse_float(rr.get("CornerWeight")),
            adapter_name=(
                # If caller explicitly knows the car, use that — avoids Cadillac/Porsche
                # being misidentified as BMW (they share the same IBT setup structure).
                # GT3 canonicals are handled by the early-return dispatch above; this
                # path only fires for GTP cars.
                car_canonical.lower()
                if car_canonical and car_canonical.lower() in GTP_CANONICALS + GT3_CANONICALS
                else (
                    "acura" if is_heave_roll_layout
                    else ("ferrari" if is_ferrari_layout else "unknown")
                )
            ),
            extraction_attempts=attempts,
        )
        if is_ferrari_layout and setup.rear_spring_nmm in (0.0,) and setup.rear_torsion_od_mm not in (0.0,):
            # Ferrari's rear raw index is exposed via torsion-bar OD, but the legacy
            # compatibility alias still expects it on rear_spring_nmm.
            setup.rear_spring_nmm = setup.rear_torsion_od_mm
        if is_ferrari_layout:
            # Convert N/mm → garage index BEFORE storing so that the delta card
            # current-vs-recommended comparison works in consistent index units.
            # public_output_value handles the N/mm → index mapping for Ferrari.
            from car_model.setup_registry import public_output_value as _pov
            setup.raw_indexed_fields = {
                "front_heave_index": _pov("ferrari", "front_heave_nmm", setup.front_heave_nmm),
                "rear_heave_index": _pov("ferrari", "rear_third_nmm", setup.rear_third_nmm),
                "front_torsion_bar_index": _pov("ferrari", "front_torsion_od_mm", setup.front_torsion_od_mm),
                "rear_torsion_bar_index": _pov("ferrari", "rear_spring_nmm", setup.rear_spring_nmm),
            }
            setup.decode_warnings.extend(
                [
                    "Ferrari indexed springs/torsion bars are preserved as authoritative raw indices.",
                    "Ferrari supporting outputs are sourced from Ferrari session values and Ferrari-only registry paths.",
                ]
            )
            if setup.rear_spring_perch_mm not in (0.0,):
                setup.conflicted_fields.append("rear_spring_perch_mm")
        else:
            if setup.fuel_l <= 0.0:
                setup.unresolved_fields.append("fuel_l")
        return setup

    def summary(self) -> str:
        """One-line summary of key setup parameters."""
        if self.adapter_name in GT3_CANONICALS:
            # GT3: no heave element; ARB exposed as either blade (BMW/Aston)
            # or single integer setting (Porsche).
            farb = (
                f"{self.front_arb_setting}"
                if self.adapter_name == "porsche_992_gt3r"
                else f"{self.front_arb_blade}"
            )
            rarb = (
                f"{self.rear_arb_setting}"
                if self.adapter_name == "porsche_992_gt3r"
                else f"{self.rear_arb_blade}"
            )
            return (
                f"Wing {self.wing_angle_deg:.0f}  "
                f"RH F{self.static_front_rh_mm:.0f}/R{self.static_rear_rh_mm:.0f}  "
                f"Spring F{self.front_corner_spring_nmm:.0f}/R{self.rear_spring_nmm:.0f}  "
                f"FARB {farb} RARB {rarb}  "
                f"Cam F{self.front_camber_deg:.1f}/R{self.rear_camber_deg:.1f}  "
                f"BB {self.brake_bias_pct:.1f}%"
            )
        return (
            f"Wing {self.wing_angle_deg:.0f}  "
            f"RH F{self.static_front_rh_mm:.0f}/R{self.static_rear_rh_mm:.0f}  "
            f"Heave {self.front_heave_nmm:.0f}/{self.rear_third_nmm:.0f}  "
            f"FARB {self.front_arb_size}/{self.front_arb_blade} "
            f"RARB {self.rear_arb_size}/{self.rear_arb_blade}  "
            f"Cam F{self.front_camber_deg:.1f}/R{self.rear_camber_deg:.1f}  "
            f"BB {self.brake_bias_pct:.1f}%"
        )
