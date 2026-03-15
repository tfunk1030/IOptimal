"""Extract the current setup from an IBT file's session info YAML.

The IBT file embeds the complete garage setup under CarSetup.
Values include unit suffixes (e.g., "50 N/mm", "-2.0 deg") which
are stripped and converted to numeric types.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from track_model.ibt_parser import IBTFile


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

    # --- ARBs ---
    front_arb_size: str = ""
    front_arb_blade: int = 0
    rear_arb_size: str = ""
    rear_arb_blade: int = 0

    # --- Geometry ---
    front_camber_deg: float = 0.0
    rear_camber_deg: float = 0.0
    front_toe_mm: float = 0.0
    rear_toe_mm: float = 0.0

    # --- Dampers (front = LF values, rear = LR values) ---
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

    # --- Brakes / Diff / TC ---
    brake_bias_pct: float = 0.0
    diff_preload_nm: float = 0.0
    diff_ramp_angles: str = ""
    diff_clutch_plates: int = 0
    tc_gain: int = 0
    tc_slip: int = 0
    fuel_l: float = 0.0

    # --- iRacing-computed garage display values ---
    # These are computed by iRacing from the settable parameters above.
    # Extracted from IBT session info for model calibration ground truth.
    torsion_bar_turns: float = 0.0
    torsion_bar_defl_mm: float = 0.0
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

    @classmethod
    def from_ibt(cls, ibt: IBTFile) -> CurrentSetup:
        """Parse the current setup from an IBT file's session info.

        The session info is a YAML dict parsed by the IBT parser.
        CarSetup is structured as nested dicts:
            CarSetup.TiresAero.AeroSettings.RearWingAngle
            CarSetup.Chassis.Front.HeaveSpring
            CarSetup.Chassis.LeftFront.TorsionBarOD
            etc.
        """
        si = ibt.session_info
        if not isinstance(si, dict):
            raise ValueError("IBT session info is not parsed (pyyaml missing?)")

        cs = si.get("CarSetup", {})
        chassis = cs.get("Chassis", {})
        tires_aero = cs.get("TiresAero", {})
        brakes = cs.get("BrakesDriveUnit", {})

        front = chassis.get("Front", {})
        rear = chassis.get("Rear", {})
        lf = chassis.get("LeftFront", {})
        rf = chassis.get("RightFront", {})
        lr = chassis.get("LeftRear", {})
        rr = chassis.get("RightRear", {})

        aero_settings = tires_aero.get("AeroSettings", {})
        aero_calc = tires_aero.get("AeroCalculator", {})
        brake_spec = brakes.get("BrakeSpec", {})
        diff_spec = brakes.get("RearDiffSpec", {})
        tc = brakes.get("TractionControl", {})
        fuel = brakes.get("Fuel", {})

        # Average left/right for symmetric parameters
        def avg_f(key: str) -> float:
            return (_parse_float(lf.get(key)) + _parse_float(rf.get(key))) / 2.0

        def avg_r(key: str) -> float:
            return (_parse_float(lr.get(key)) + _parse_float(rr.get(key))) / 2.0

        return cls(
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
            front_pushrod_mm=_parse_float(front.get("PushrodLengthOffset")),
            rear_pushrod_mm=_parse_float(rear.get("PushrodLengthOffset")),

            # Heave / Third
            front_heave_nmm=_parse_float(front.get("HeaveSpring")),
            front_heave_perch_mm=_parse_float(front.get("HeavePerchOffset")),
            rear_third_nmm=_parse_float(rear.get("ThirdSpring")),
            rear_third_perch_mm=_parse_float(rear.get("ThirdPerchOffset")),

            # Corner springs (use LF/LR as representative)
            front_torsion_od_mm=_parse_float(lf.get("TorsionBarOD")),
            rear_spring_nmm=_parse_float(lr.get("SpringRate")),
            rear_spring_perch_mm=_parse_float(lr.get("SpringPerchOffset")),

            # ARBs
            front_arb_size=str(front.get("ArbSize", "")),
            front_arb_blade=_parse_int(front.get("ArbBlades")),
            rear_arb_size=str(rear.get("ArbSize", "")),
            rear_arb_blade=_parse_int(rear.get("ArbBlades")),

            # Geometry (average left/right)
            front_camber_deg=avg_f("Camber"),
            rear_camber_deg=avg_r("Camber"),
            front_toe_mm=_parse_float(front.get("ToeIn")),
            rear_toe_mm=(_parse_float(lr.get("ToeIn")) + _parse_float(rr.get("ToeIn"))) / 2.0,

            # Dampers (use LF for front, LR for rear)
            front_ls_comp=_parse_int(lf.get("LsCompDamping")),
            front_ls_rbd=_parse_int(lf.get("LsRbdDamping")),
            front_hs_comp=_parse_int(lf.get("HsCompDamping")),
            front_hs_rbd=_parse_int(lf.get("HsRbdDamping")),
            front_hs_slope=_parse_int(lf.get("HsCompDampSlope")),
            rear_ls_comp=_parse_int(lr.get("LsCompDamping")),
            rear_ls_rbd=_parse_int(lr.get("LsRbdDamping")),
            rear_hs_comp=_parse_int(lr.get("HsCompDamping")),
            rear_hs_rbd=_parse_int(lr.get("HsRbdDamping")),
            rear_hs_slope=_parse_int(lr.get("HsCompDampSlope")),

            # Brakes / Diff / TC
            brake_bias_pct=_parse_float(brake_spec.get("BrakePressureBias")),
            diff_preload_nm=_parse_float(diff_spec.get("Preload")),
            diff_ramp_angles=str(diff_spec.get("CoastDriveRampAngles", "")),
            diff_clutch_plates=_parse_int(diff_spec.get("ClutchFrictionPlates")),
            tc_gain=_parse_int(tc.get("TractionControlGain")),
            tc_slip=_parse_int(tc.get("TractionControlSlip")),
            fuel_l=_parse_float(fuel.get("FuelLevel")),

            # iRacing-computed display values (ground truth for calibration)
            torsion_bar_turns=_parse_float(lf.get("TorsionBarTurns")),
            torsion_bar_defl_mm=_parse_float(lf.get("TorsionBarDefl")),
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
            third_spring_defl_static_mm=_parse_defl(rear.get("ThirdSpringDefl"))[0],
            third_spring_defl_max_mm=_parse_defl(rear.get("ThirdSpringDefl"))[1],
            third_slider_defl_static_mm=_parse_defl(rear.get("ThirdSliderDefl"))[0],
            third_slider_defl_max_mm=_parse_defl(rear.get("ThirdSliderDefl"))[1],
            lf_corner_weight_n=_parse_float(lf.get("CornerWeight")),
            rf_corner_weight_n=_parse_float(rf.get("CornerWeight")),
            lr_corner_weight_n=_parse_float(lr.get("CornerWeight")),
            rr_corner_weight_n=_parse_float(rr.get("CornerWeight")),
        )

    def summary(self) -> str:
        """One-line summary of key setup parameters."""
        return (
            f"Wing {self.wing_angle_deg:.0f}  "
            f"RH F{self.static_front_rh_mm:.0f}/R{self.static_rear_rh_mm:.0f}  "
            f"Heave {self.front_heave_nmm:.0f}/{self.rear_third_nmm:.0f}  "
            f"FARB {self.front_arb_size}/{self.front_arb_blade} "
            f"RARB {self.rear_arb_size}/{self.rear_arb_blade}  "
            f"Cam F{self.front_camber_deg:.1f}/R{self.rear_camber_deg:.1f}"
        )
