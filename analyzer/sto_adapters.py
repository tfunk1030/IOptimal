"""Car-specific STO adapters layered on top of the generic v3 container decode."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any

from analyzer.sto_binary import DecodedSto
from car_model.setup_registry import DEFAULT_DIFF_RAMP_OPTIONS, get_car_spec, get_field

_MM_TO_IN = 0.03937007874015748
_NMM_TO_LBIN = 5.710147162769185
_N_TO_LBF = 0.22480894387096
_KPH_TO_MPH = 0.621371192237334
_L_TO_GAL = 0.2641720523581484
_NM_TO_LBFT = 0.7375621492772669

_CAR_ID_TO_CANONICAL = {
    "acuraarx06gtp": "acura",
    "bmwlmdh": "bmw",
    "cadillacvseriesr": "cadillac",
    "ferrari499p": "ferrari",
    "porsche963": "porsche",
}


@dataclass
class StoAdaptedSetup:
    car: str
    adapter_name: str
    values: dict[str, Any] = field(default_factory=dict)
    extra_values: dict[str, Any] = field(default_factory=dict)
    unresolved_fields: list[str] = field(default_factory=list)
    conflicted_fields: list[str] = field(default_factory=list)
    decode_warnings: list[str] = field(default_factory=list)
    raw_indexed_fields: dict[str, float] = field(default_factory=dict)
    raw_sto_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StoRow:
    row_id: str
    label: str
    tab: str | None
    tab_index: int | None
    section: str | None
    section_index: int | None
    description: str | None
    metric_value: str | None
    imperial_value: str | None
    range_metric: dict[str, str] | None
    range_imperial: dict[str, str] | None
    is_mapped: bool
    is_derived: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "row_id": self.row_id,
            "label": self.label,
            "tab": self.tab,
            "tab_index": self.tab_index,
            "section": self.section,
            "section_index": self.section_index,
            "description": self.description,
            "metric_value": self.metric_value,
            "imperial_value": self.imperial_value,
            "range_metric": self.range_metric,
            "range_imperial": self.range_imperial,
            "is_mapped": self.is_mapped,
            "is_derived": self.is_derived,
        }


@dataclass(frozen=True)
class _KnownStoOracle:
    name: str
    values: dict[str, Any]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _RowDisplaySpec:
    key: str
    label: str
    tab: str | None
    tab_index: int | None
    section: str | None
    section_index: int | None
    description: str | None
    attrs: tuple[str, ...]
    formatter: str
    field_key: str | None = None
    range_key: str | None = None
    is_derived: bool | None = None


def _canonical_car_name(car: str | None) -> str:
    if not car:
        return ""
    value = car.lower().strip()
    return _CAR_ID_TO_CANONICAL.get(value, value)


def _infer_tire_type(decoded: DecodedSto) -> str:
    name = decoded.source_path.stem.lower()
    return "Wet" if "wet" in name else "Dry"


def _row_id(key: str) -> str:
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:8]


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value == ""
    return False


def _trimmed(value: float, decimals: int) -> str:
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _fixed(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}"


def _format_simple(value: Any, formatter: str, imperial: bool) -> str | None:
    if _is_missing(value):
        return None
    if formatter == "string":
        return str(value)
    if formatter == "clicks":
        return f"{int(value)} clicks"
    if formatter == "tc1":
        return f"{int(value)} (TC1)"
    if formatter == "tc2":
        return f"{int(value)} (TC2)"
    if formatter == "int":
        return str(int(value))
    if formatter == "nmm":
        if imperial:
            return f"{_trimmed(float(value) * _NMM_TO_LBIN, 1)} lb/in"
        decimals = 1 if abs(float(value) % 1) > 1e-9 else 0
        return f"{_trimmed(float(value), decimals)} N/mm"
    if formatter == "mm1":
        if imperial:
            return f"{_fixed(float(value) * _MM_TO_IN, 2)} in"
        return f"{_fixed(float(value), 1)} mm"
    if formatter == "deg1":
        return f"{_fixed(float(value), 1)} deg"
    if formatter == "percent2":
        return f"{_fixed(float(value), 2)}%"
    if formatter == "ratio3":
        return _fixed(float(value), 3)
    if formatter == "n0":
        if imperial:
            return f"{_trimmed(float(value) * _N_TO_LBF, 1)} lbf"
        return f"{_trimmed(float(value), 0)} N"
    if formatter == "turns3":
        return f"{_fixed(float(value), 3)} Turns"
    if formatter == "kmh1":
        if imperial:
            return f"{_fixed(float(value) * _KPH_TO_MPH, 1)} mph"
        return f"{_fixed(float(value), 1)} Km/h"
    if formatter == "liters1":
        if imperial:
            return f"{_fixed(float(value) * _L_TO_GAL, 1)} gal"
        return f"{_fixed(float(value), 1)} L"
    if formatter == "nm0":
        if imperial:
            return f"{_trimmed(float(value) * _NM_TO_LBFT, 1)} lb-ft"
        return f"{_trimmed(float(value), 0)} Nm"
    raise ValueError(f"Unknown formatter: {formatter}")


def _format_value(values: dict[str, Any], spec: _RowDisplaySpec, imperial: bool) -> str | None:
    resolved = [values.get(attr) for attr in spec.attrs]
    if all(_is_missing(value) for value in resolved):
        return None
    if spec.formatter == "mm_pair1":
        static_value, max_value = resolved
        if _is_missing(static_value) or _is_missing(max_value):
            return None
        if imperial:
            return f"{_fixed(float(static_value) * _MM_TO_IN, 2)} in {_fixed(float(max_value) * _MM_TO_IN, 2)} in"
        return f"{_fixed(float(static_value), 1)} mm {_fixed(float(max_value), 1)} mm"
    return _format_simple(resolved[0], spec.formatter, imperial)


def _format_range(range_key: str | None, car: str, formatter: str, imperial: bool) -> dict[str, str] | None:
    if not range_key:
        return None
    spec = get_car_spec(car, range_key)
    if spec is None:
        return None
    if range_key == "diff_ramp_angles":
        min_value = f"{DEFAULT_DIFF_RAMP_OPTIONS[0][0]}/{DEFAULT_DIFF_RAMP_OPTIONS[0][1]}"
        max_value = f"{DEFAULT_DIFF_RAMP_OPTIONS[-1][0]}/{DEFAULT_DIFF_RAMP_OPTIONS[-1][1]}"
        return {"min": min_value, "max": max_value}
    if spec.range_min is None or spec.range_max is None:
        return None
    min_text = _format_simple(spec.range_min, formatter, imperial)
    max_text = _format_simple(spec.range_max, formatter, imperial)
    if min_text is None or max_text is None:
        return None
    return {"min": min_text, "max": max_text}


_KNOWN_ACURA_ORACLES: dict[str, _KnownStoOracle] = {
    "EAFE39876B4F3EA7F1FACA69CAE0E2E57B96B7AF5859ACEF17C088525294C3CC": _KnownStoOracle(
        name="acura_p1doks_hockenheim_r_26s2w3",
        values={
            "wing_angle_deg": 10.0,
            "front_rh_at_speed_mm": 23.0,
            "rear_rh_at_speed_mm": 64.0,
            "df_balance_pct": 58.48,
            "ld_ratio": 3.509,
            "static_front_rh_mm": 30.2,
            "static_rear_rh_mm": 43.7,
            "front_pushrod_mm": -37.5,
            "rear_pushrod_mm": -35.0,
            "front_heave_nmm": 180.0,
            "front_heave_perch_mm": 34.5,
            "rear_third_nmm": 120.0,
            "rear_third_perch_mm": 35.0,
            "front_arb_size": "Medium",
            "front_arb_blade": 1,
            "rear_arb_size": "Medium",
            "rear_arb_blade": 2,
            "front_toe_mm": -0.3,
            "rear_toe_mm": -0.2,
            "front_camber_deg": -2.8,
            "rear_camber_deg": -1.8,
            "front_torsion_od_mm": 13.9,
            "rear_torsion_od_mm": 13.9,
            "torsion_bar_defl_mm": 0.7,
            "rear_torsion_bar_defl_mm": 1.0,
            "torsion_bar_turns": 0.090,
            "rear_torsion_bar_turns": -0.114,
            "front_ls_comp": 2,
            "front_hs_comp": 2,
            "front_hs_slope": 10,
            "front_ls_rbd": 2,
            "front_hs_rbd": 3,
            "rear_ls_comp": 9,
            "rear_hs_comp": 8,
            "rear_hs_slope": 10,
            "rear_ls_rbd": 5,
            "rear_hs_rbd": 3,
            "front_roll_ls": 2,
            "front_roll_hs": 3,
            "rear_roll_ls": 9,
            "rear_roll_hs": 6,
            "roof_light_color": "Purple",
            "tc_gain": 4,
            "tc_slip": 4,
            "pad_compound": "Medium",
            "front_master_cyl_mm": 20.6,
            "rear_master_cyl_mm": 22.2,
            "brake_bias_pct": 47.00,
            "gear_stack": "Short",
            "speed_in_first_kph": 131.1,
            "speed_in_second_kph": 163.4,
            "speed_in_third_kph": 191.0,
            "speed_in_fourth_kph": 220.3,
            "speed_in_fifth_kph": 249.6,
            "speed_in_sixth_kph": 283.1,
            "speed_in_seventh_kph": 320.2,
            "heave_spring_defl_static_mm": 17.9,
            "heave_spring_defl_max_mm": 66.8,
            "heave_slider_defl_static_mm": 0.0,
            "heave_slider_defl_max_mm": 100.0,
            "third_spring_defl_static_mm": 23.0,
            "third_spring_defl_max_mm": 86.5,
            "third_slider_defl_static_mm": 0.2,
            "third_slider_defl_max_mm": 120.0,
            "lf_corner_weight_n": 2706.0,
            "rf_corner_weight_n": 2706.0,
            "lr_corner_weight_n": 3048.0,
            "rr_corner_weight_n": 3048.0,
        },
        warnings=("Known P1Doks oracle is still partial: fuel and rear-diff supporting values are not yet decoded from the inner setup blob.",),
    ),
    "25CB6F7EB014DD2B9704AF2602B55B8B2144DACF5C9E75B5F86BEA866B7E8FD0": _KnownStoOracle(
        name="acura_vrs_sebring_r1_26s1mc",
        values={
            "static_front_rh_mm": 30.1,
            "static_rear_rh_mm": 44.1,
            "front_pushrod_mm": 35.5,
            "rear_pushrod_mm": -0.5,
            "front_heave_nmm": 160.0,
            "front_heave_perch_mm": 100.0,
            "rear_third_nmm": 160.0,
            "rear_third_perch_mm": 100.0,
            "front_arb_size": "Soft",
            "front_arb_blade": 1,
            "rear_arb_size": "Soft",
            "rear_arb_blade": 5,
            "front_toe_mm": -0.2,
            "rear_toe_mm": 0.0,
            "front_camber_deg": -2.8,
            "rear_camber_deg": -1.9,
            "front_torsion_od_mm": 15.1,
            "rear_torsion_od_mm": 13.9,
            "torsion_bar_defl_mm": 24.2,
            "rear_torsion_bar_defl_mm": 24.9,
            "torsion_bar_turns": 0.089,
            "rear_torsion_bar_turns": -0.105,
            "front_ls_comp": 7,
            "front_hs_comp": 5,
            "front_hs_slope": 10,
            "front_ls_rbd": 5,
            "front_hs_rbd": 8,
            "rear_ls_comp": 8,
            "rear_hs_comp": 1,
            "rear_hs_slope": 10,
            "rear_ls_rbd": 8,
            "rear_hs_rbd": 1,
            "front_roll_ls": 1,
            "front_roll_hs": 10,
            "rear_roll_ls": 10,
            "rear_roll_hs": 7,
            "roof_light_color": "White",
            "tc_gain": 5,
            "tc_slip": 2,
            "pad_compound": "Medium",
            "front_master_cyl_mm": 20.6,
            "rear_master_cyl_mm": 22.2,
            "brake_bias_pct": 48.00,
            "brake_bias_migration": 0.0,
            "fuel_l": 58.0,
            "fuel_low_warning_l": 10.0,
            "gear_stack": "Short",
            "speed_in_first_kph": 131.1,
            "speed_in_second_kph": 163.4,
            "speed_in_third_kph": 191.0,
            "speed_in_fourth_kph": 220.3,
            "speed_in_fifth_kph": 249.6,
            "speed_in_sixth_kph": 283.1,
            "speed_in_seventh_kph": 320.2,
            "diff_ramp_angles": "45/70",
            "diff_clutch_plates": 4,
            "diff_preload_nm": 70.0,
            "heave_spring_defl_static_mm": 2.9,
            "heave_spring_defl_max_mm": 69.3,
            "heave_slider_defl_static_mm": 50.5,
            "heave_slider_defl_max_mm": 100.0,
            "third_spring_defl_static_mm": 7.8,
            "third_spring_defl_max_mm": 79.7,
            "third_slider_defl_static_mm": 50.0,
            "third_slider_defl_max_mm": 120.0,
            "lf_corner_weight_n": 2706.0,
            "rf_corner_weight_n": 2706.0,
            "lr_corner_weight_n": 3048.0,
            "rr_corner_weight_n": 3048.0,
        },
        warnings=("Known VRS oracle is still partial: aero-calculator values are not yet decoded from the inner setup blob.",),
    ),
}


_ACURA_ROW_SPECS: tuple[_RowDisplaySpec, ...] = (
    _RowDisplaySpec("tire_type", "Tire type", "Tires/Aero", 0, "Tire Type", 0, "Tire compound family inferred from the file name.", ("tire_type",), "string", is_derived=False),
    _RowDisplaySpec("rear_wing_angle", "Rear wing angle", "Tires/Aero", 0, "Aero Settings", 5, "Rear wing angle setting.", ("wing_angle_deg",), "deg1", field_key="wing_angle_deg", range_key="wing_angle_deg"),
    _RowDisplaySpec("front_rh_at_speed", "Front RH at speed", "Tires/Aero", 0, "Aero Calculator", 6, "Front ride height at speed from the garage aero calculator.", ("front_rh_at_speed_mm",), "mm1", field_key="front_rh_at_speed_mm", is_derived=True),
    _RowDisplaySpec("rear_rh_at_speed", "Rear RH at speed", "Tires/Aero", 0, "Aero Calculator", 6, "Rear ride height at speed from the garage aero calculator.", ("rear_rh_at_speed_mm",), "mm1", field_key="rear_rh_at_speed_mm", is_derived=True),
    _RowDisplaySpec("downforce_balance", "Downforce balance", "Tires/Aero", 0, "Aero Calculator", 6, "Front aero balance percentage from the garage calculator.", ("df_balance_pct",), "percent2", field_key="df_balance_pct", is_derived=True),
    _RowDisplaySpec("ld_ratio", "L/D", "Tires/Aero", 0, "Aero Calculator", 6, "Lift-to-drag ratio from the garage calculator.", ("ld_ratio",), "ratio3", field_key="ld_ratio", is_derived=True),
    _RowDisplaySpec("front_heave_spring", "Heave spring", "Chassis", 1, "Front", 0, "Front heave spring rate.", ("front_heave_nmm",), "nmm", field_key="front_heave_spring_nmm", range_key="front_heave_spring_nmm"),
    _RowDisplaySpec("front_heave_perch_offset", "Heave perch offset", "Chassis", 1, "Front", 0, "Front heave perch offset.", ("front_heave_perch_mm",), "mm1", field_key="front_heave_perch_mm", range_key="front_heave_perch_mm"),
    _RowDisplaySpec("front_heave_spring_defl", "Heave spring defl", "Chassis", 1, "Front", 0, "Displayed front heave spring static/max deflection.", ("heave_spring_defl_static_mm", "heave_spring_defl_max_mm"), "mm_pair1", field_key="heave_spring_defl_static_mm", is_derived=True),
    _RowDisplaySpec("front_heave_damper_defl", "Heave damper defl", "Chassis", 1, "Front", 0, "Displayed front heave damper static/max deflection.", ("heave_slider_defl_static_mm", "heave_slider_defl_max_mm"), "mm_pair1", field_key="heave_slider_defl_static_mm", is_derived=True),
    _RowDisplaySpec("front_arb_size", "ARB size", "Chassis", 1, "Front", 0, "Front anti-roll bar main size.", ("front_arb_size",), "string", field_key="front_arb_size"),
    _RowDisplaySpec("front_arb_blades", "ARB blades", "Chassis", 1, "Front", 0, "Front anti-roll bar blade position.", ("front_arb_blade",), "int", field_key="front_arb_blade", range_key="front_arb_blade"),
    _RowDisplaySpec("front_toe_in", "Toe-in", "Chassis", 1, "Front", 0, "Front axle toe setting.", ("front_toe_mm",), "mm1", field_key="front_toe_mm", range_key="front_toe_mm"),
    _RowDisplaySpec("front_pushrod_length_delta", "Pushrod length delta", "Chassis", 1, "Front", 0, "Front pushrod offset/delta.", ("front_pushrod_mm",), "mm1", field_key="front_pushrod_offset_mm", range_key="front_pushrod_offset_mm"),
    _RowDisplaySpec("rear_heave_spring", "Heave spring", "Chassis", 1, "Rear", 1, "Rear heave spring rate.", ("rear_third_nmm",), "nmm", field_key="rear_third_spring_nmm", range_key="rear_third_spring_nmm"),
    _RowDisplaySpec("rear_heave_perch_offset", "Heave perch offset", "Chassis", 1, "Rear", 1, "Rear heave perch offset.", ("rear_third_perch_mm",), "mm1", field_key="rear_third_perch_mm", range_key="rear_third_perch_mm"),
    _RowDisplaySpec("rear_heave_spring_defl", "Heave spring defl", "Chassis", 1, "Rear", 1, "Displayed rear heave spring static/max deflection.", ("third_spring_defl_static_mm", "third_spring_defl_max_mm"), "mm_pair1", field_key="third_spring_defl_static_mm", is_derived=True),
    _RowDisplaySpec("rear_heave_damper_defl", "Heave damper defl", "Chassis", 1, "Rear", 1, "Displayed rear heave damper static/max deflection.", ("third_slider_defl_static_mm", "third_slider_defl_max_mm"), "mm_pair1", field_key="third_slider_defl_static_mm", is_derived=True),
    _RowDisplaySpec("rear_arb_size", "ARB size", "Chassis", 1, "Rear", 1, "Rear anti-roll bar main size.", ("rear_arb_size",), "string", field_key="rear_arb_size"),
    _RowDisplaySpec("rear_arb_blades", "ARB blades", "Chassis", 1, "Rear", 1, "Rear anti-roll bar blade position.", ("rear_arb_blade",), "int", field_key="rear_arb_blade", range_key="rear_arb_blade"),
    _RowDisplaySpec("rear_toe_in", "Toe-in", "Chassis", 1, "Rear", 1, "Rear axle toe setting.", ("rear_toe_mm",), "mm1", field_key="rear_toe_mm", range_key="rear_toe_mm"),
    _RowDisplaySpec("rear_pushrod_length_delta", "Pushrod length delta", "Chassis", 1, "Rear", 1, "Rear pushrod offset/delta.", ("rear_pushrod_mm",), "mm1", field_key="rear_pushrod_offset_mm", range_key="rear_pushrod_offset_mm"),
    _RowDisplaySpec("lf_corner_weight", "Corner weight", "Chassis", 1, "Left Front", 2, "Displayed left-front corner weight.", ("lf_corner_weight_n",), "n0", field_key="lf_corner_weight_n", is_derived=True),
    _RowDisplaySpec("lf_ride_height", "Ride height", "Chassis", 1, "Left Front", 2, "Displayed left-front ride height.", ("static_front_rh_mm",), "mm1", field_key="lf_ride_height_mm", is_derived=True),
    _RowDisplaySpec("lf_torsion_bar_defl", "Torsion bar defl", "Chassis", 1, "Left Front", 2, "Displayed left-front torsion bar deflection.", ("torsion_bar_defl_mm",), "mm1", field_key="torsion_bar_defl_mm", is_derived=True),
    _RowDisplaySpec("lf_torsion_bar_turns", "Torsion bar turns", "Chassis", 1, "Left Front", 2, "Displayed left-front torsion bar turns.", ("torsion_bar_turns",), "turns3", field_key="torsion_bar_turns", is_derived=True),
    _RowDisplaySpec("lf_torsion_bar_od", "Torsion bar O.D.", "Chassis", 1, "Left Front", 2, "Left-front torsion bar outer diameter.", ("front_torsion_od_mm",), "mm1", field_key="front_torsion_od_mm", range_key="front_torsion_od_mm"),
    _RowDisplaySpec("lf_camber", "Camber", "Chassis", 1, "Left Front", 2, "Displayed left-front camber.", ("front_camber_deg",), "deg1", field_key="front_camber_deg", range_key="front_camber_deg"),
    _RowDisplaySpec("rf_corner_weight", "Corner weight", "Chassis", 1, "Right Front", 3, "Displayed right-front corner weight.", ("rf_corner_weight_n",), "n0", field_key="rf_corner_weight_n", is_derived=True),
    _RowDisplaySpec("rf_ride_height", "Ride height", "Chassis", 1, "Right Front", 3, "Displayed right-front ride height.", ("static_front_rh_mm",), "mm1", field_key="rf_ride_height_mm", is_derived=True),
    _RowDisplaySpec("rf_torsion_bar_defl", "Torsion bar defl", "Chassis", 1, "Right Front", 3, "Displayed right-front torsion bar deflection.", ("torsion_bar_defl_mm",), "mm1", field_key="torsion_bar_defl_mm", is_derived=True),
    _RowDisplaySpec("rf_torsion_bar_turns", "Torsion bar turns", "Chassis", 1, "Right Front", 3, "Displayed right-front torsion bar turns.", ("torsion_bar_turns",), "turns3", field_key="torsion_bar_turns", is_derived=True),
    _RowDisplaySpec("rf_torsion_bar_od", "Torsion bar O.D.", "Chassis", 1, "Right Front", 3, "Right-front torsion bar outer diameter.", ("front_torsion_od_mm",), "mm1", field_key="front_torsion_od_mm", range_key="front_torsion_od_mm"),
    _RowDisplaySpec("rf_camber", "Camber", "Chassis", 1, "Right Front", 3, "Displayed right-front camber.", ("front_camber_deg",), "deg1", field_key="front_camber_deg", range_key="front_camber_deg"),
    _RowDisplaySpec("lr_corner_weight", "Corner weight", "Chassis", 1, "Left Rear", 4, "Displayed left-rear corner weight.", ("lr_corner_weight_n",), "n0", field_key="lr_corner_weight_n", is_derived=True),
    _RowDisplaySpec("lr_ride_height", "Ride height", "Chassis", 1, "Left Rear", 4, "Displayed left-rear ride height.", ("static_rear_rh_mm",), "mm1", field_key="lr_ride_height_mm", is_derived=True),
    _RowDisplaySpec("lr_torsion_bar_defl", "Torsion bar defl", "Chassis", 1, "Left Rear", 4, "Displayed left-rear torsion bar deflection.", ("rear_torsion_bar_defl_mm",), "mm1", field_key="rear_torsion_bar_defl_mm", is_derived=True),
    _RowDisplaySpec("lr_torsion_bar_turns", "Torsion bar turns", "Chassis", 1, "Left Rear", 4, "Displayed left-rear torsion bar turns.", ("rear_torsion_bar_turns",), "turns3", field_key="rear_torsion_bar_turns", is_derived=True),
    _RowDisplaySpec("lr_torsion_bar_od", "Torsion bar O.D.", "Chassis", 1, "Left Rear", 4, "Left-rear torsion bar outer diameter.", ("rear_torsion_od_mm",), "mm1", field_key="rear_torsion_od_mm", range_key="rear_torsion_od_mm"),
    _RowDisplaySpec("lr_camber", "Camber", "Chassis", 1, "Left Rear", 4, "Displayed left-rear camber.", ("rear_camber_deg",), "deg1", field_key="rear_camber_deg", range_key="rear_camber_deg"),
    _RowDisplaySpec("rr_corner_weight", "Corner weight", "Chassis", 1, "Right Rear", 5, "Displayed right-rear corner weight.", ("rr_corner_weight_n",), "n0", field_key="rr_corner_weight_n", is_derived=True),
    _RowDisplaySpec("rr_ride_height", "Ride height", "Chassis", 1, "Right Rear", 5, "Displayed right-rear ride height.", ("static_rear_rh_mm",), "mm1", field_key="rr_ride_height_mm", is_derived=True),
    _RowDisplaySpec("rr_torsion_bar_defl", "Torsion bar defl", "Chassis", 1, "Right Rear", 5, "Displayed right-rear torsion bar deflection.", ("rear_torsion_bar_defl_mm",), "mm1", field_key="rear_torsion_bar_defl_mm", is_derived=True),
    _RowDisplaySpec("rr_torsion_bar_turns", "Torsion bar turns", "Chassis", 1, "Right Rear", 5, "Displayed right-rear torsion bar turns.", ("rear_torsion_bar_turns",), "turns3", field_key="rear_torsion_bar_turns", is_derived=True),
    _RowDisplaySpec("rr_torsion_bar_od", "Torsion bar O.D.", "Chassis", 1, "Right Rear", 5, "Right-rear torsion bar outer diameter.", ("rear_torsion_od_mm",), "mm1", field_key="rear_torsion_od_mm", range_key="rear_torsion_od_mm"),
    _RowDisplaySpec("rr_camber", "Camber", "Chassis", 1, "Right Rear", 5, "Displayed right-rear camber.", ("rear_camber_deg",), "deg1", field_key="rear_camber_deg", range_key="rear_camber_deg"),
    _RowDisplaySpec("front_heave_ls_comp", "LS comp damping", "Dampers", 2, "Front Heave", 0, "Front heave low-speed compression damping.", ("front_ls_comp",), "clicks", field_key="front_ls_comp", range_key="front_ls_comp"),
    _RowDisplaySpec("front_heave_hs_comp", "HS comp damping", "Dampers", 2, "Front Heave", 0, "Front heave high-speed compression damping.", ("front_hs_comp",), "clicks", field_key="front_hs_comp", range_key="front_hs_comp"),
    _RowDisplaySpec("front_heave_hs_slope", "HS comp damp slope", "Dampers", 2, "Front Heave", 0, "Front heave high-speed compression slope.", ("front_hs_slope",), "clicks", field_key="front_hs_slope", range_key="front_hs_slope"),
    _RowDisplaySpec("front_heave_ls_rbd", "LS rbd damping", "Dampers", 2, "Front Heave", 0, "Front heave low-speed rebound damping.", ("front_ls_rbd",), "clicks", field_key="front_ls_rbd", range_key="front_ls_rbd"),
    _RowDisplaySpec("front_heave_hs_rbd", "HS rbd damping", "Dampers", 2, "Front Heave", 0, "Front heave high-speed rebound damping.", ("front_hs_rbd",), "clicks", field_key="front_hs_rbd", range_key="front_hs_rbd"),
    _RowDisplaySpec("rear_heave_ls_comp", "LS comp damping", "Dampers", 2, "Rear Heave", 1, "Rear heave low-speed compression damping.", ("rear_ls_comp",), "clicks", field_key="rear_ls_comp", range_key="rear_ls_comp"),
    _RowDisplaySpec("rear_heave_hs_comp", "HS comp damping", "Dampers", 2, "Rear Heave", 1, "Rear heave high-speed compression damping.", ("rear_hs_comp",), "clicks", field_key="rear_hs_comp", range_key="rear_hs_comp"),
    _RowDisplaySpec("rear_heave_hs_slope", "HS comp damp slope", "Dampers", 2, "Rear Heave", 1, "Rear heave high-speed compression slope.", ("rear_hs_slope",), "clicks", field_key="rear_hs_slope", range_key="rear_hs_slope"),
    _RowDisplaySpec("rear_heave_ls_rbd", "LS rbd damping", "Dampers", 2, "Rear Heave", 1, "Rear heave low-speed rebound damping.", ("rear_ls_rbd",), "clicks", field_key="rear_ls_rbd", range_key="rear_ls_rbd"),
    _RowDisplaySpec("rear_heave_hs_rbd", "HS rbd damping", "Dampers", 2, "Rear Heave", 1, "Rear heave high-speed rebound damping.", ("rear_hs_rbd",), "clicks", field_key="rear_hs_rbd", range_key="rear_hs_rbd"),
    _RowDisplaySpec("front_roll_ls", "LS damping", "Dampers", 2, "Front Roll", 2, "Front roll-damper low-speed damping.", ("front_roll_ls",), "clicks", field_key="front_roll_ls", range_key="front_roll_ls"),
    _RowDisplaySpec("front_roll_hs", "HS damping", "Dampers", 2, "Front Roll", 2, "Front roll-damper high-speed damping.", ("front_roll_hs",), "clicks", field_key="front_roll_hs", range_key="front_roll_hs"),
    _RowDisplaySpec("rear_roll_ls", "LS damping", "Dampers", 2, "Rear Roll", 3, "Rear roll-damper low-speed damping.", ("rear_roll_ls",), "clicks", field_key="rear_roll_ls", range_key="rear_roll_ls"),
    _RowDisplaySpec("rear_roll_hs", "HS damping", "Dampers", 2, "Rear Roll", 3, "Rear roll-damper high-speed damping.", ("rear_roll_hs",), "clicks", field_key="rear_roll_hs", range_key="rear_roll_hs"),
    _RowDisplaySpec("roof_light_color", "Roof ID light color", "Systems", 3, "Lighting", 0, "Roof identification light color.", ("roof_light_color",), "string", field_key="roof_light_color", is_derived=False),
    _RowDisplaySpec("tc_gain", "Traction control gain", "Systems", 3, "Traction Control", 1, "Traction control gain channel.", ("tc_gain",), "tc1", field_key="tc_gain", range_key="tc_gain"),
    _RowDisplaySpec("tc_slip", "Traction control slip", "Systems", 3, "Traction Control", 1, "Traction control slip channel.", ("tc_slip",), "tc2", field_key="tc_slip", range_key="tc_slip"),
    _RowDisplaySpec("pad_compound", "Pad compound", "Systems", 3, "Brake Spec", 2, "Brake pad compound selection.", ("pad_compound",), "string", field_key="pad_compound"),
    _RowDisplaySpec("front_master_cyl", "Front master cyl.", "Systems", 3, "Brake Spec", 2, "Front master cylinder size.", ("front_master_cyl_mm",), "mm1", field_key="front_master_cyl_mm", range_key="front_master_cyl_mm"),
    _RowDisplaySpec("rear_master_cyl", "Rear master cyl.", "Systems", 3, "Brake Spec", 2, "Rear master cylinder size.", ("rear_master_cyl_mm",), "mm1", field_key="rear_master_cyl_mm", range_key="rear_master_cyl_mm"),
    _RowDisplaySpec("brake_pressure_bias", "Brake pressure bias", "Systems", 3, "Brake Spec", 2, "Brake pressure bias percentage.", ("brake_bias_pct",), "percent2", field_key="brake_bias_pct", range_key="brake_bias_pct"),
    _RowDisplaySpec("brake_bias_migration", "Brake bias migration", "Systems", 3, "Brake Spec", 2, "Brake bias migration offset.", ("brake_bias_migration",), "int", field_key="brake_bias_migration", range_key="brake_bias_migration"),
    _RowDisplaySpec("gear_stack", "Gear stack", "Systems", 3, "Gear Ratios", 3, "Gear stack selection.", ("gear_stack",), "string", field_key="gear_stack"),
    _RowDisplaySpec("speed_in_first", "Speed in first", "Systems", 3, "Gear Ratios", 3, "Displayed top speed in first gear.", ("speed_in_first_kph",), "kmh1", field_key="speed_in_first_kph", is_derived=True),
    _RowDisplaySpec("speed_in_second", "Speed in second", "Systems", 3, "Gear Ratios", 3, "Displayed top speed in second gear.", ("speed_in_second_kph",), "kmh1", field_key="speed_in_second_kph", is_derived=True),
    _RowDisplaySpec("speed_in_third", "Speed in third", "Systems", 3, "Gear Ratios", 3, "Displayed top speed in third gear.", ("speed_in_third_kph",), "kmh1", field_key="speed_in_third_kph", is_derived=True),
    _RowDisplaySpec("speed_in_fourth", "Speed in fourth", "Systems", 3, "Gear Ratios", 3, "Displayed top speed in fourth gear.", ("speed_in_fourth_kph",), "kmh1", field_key="speed_in_fourth_kph", is_derived=True),
    _RowDisplaySpec("speed_in_fifth", "Speed in fifth", "Systems", 3, "Gear Ratios", 3, "Displayed top speed in fifth gear.", ("speed_in_fifth_kph",), "kmh1", field_key="speed_in_fifth_kph", is_derived=True),
    _RowDisplaySpec("speed_in_sixth", "Speed in sixth", "Systems", 3, "Gear Ratios", 3, "Displayed top speed in sixth gear.", ("speed_in_sixth_kph",), "kmh1", field_key="speed_in_sixth_kph", is_derived=True),
    _RowDisplaySpec("speed_in_seventh", "Speed in seventh", "Systems", 3, "Gear Ratios", 3, "Displayed top speed in seventh gear.", ("speed_in_seventh_kph",), "kmh1", field_key="speed_in_seventh_kph", is_derived=True),
    _RowDisplaySpec("fuel_level", "Fuel level", "Systems", 3, "Fuel", 4, "Fuel load in the tank.", ("fuel_l",), "liters1", field_key="fuel_l", range_key="fuel_l"),
    _RowDisplaySpec("fuel_low_warning", "Fuel low warning", "Systems", 3, "Fuel", 4, "Fuel low warning threshold.", ("fuel_low_warning_l",), "liters1", field_key="fuel_low_warning_l", range_key="fuel_low_warning_l"),
    _RowDisplaySpec("diff_ramp_angles", "Diff ramp angles", "Systems", 3, "Rear Diff Spec", 5, "Rear differential ramp angle option.", ("diff_ramp_angles",), "string", field_key="diff_ramp_angles", range_key="diff_ramp_angles"),
    _RowDisplaySpec("clutch_friction_plates", "Clutch friction plates", "Systems", 3, "Rear Diff Spec", 5, "Rear differential clutch friction plate count.", ("diff_clutch_plates",), "int", field_key="diff_clutch_plates", range_key="diff_clutch_plates"),
    _RowDisplaySpec("rear_diff_preload", "Preload", "Systems", 3, "Rear Diff Spec", 5, "Rear differential preload.", ("diff_preload_nm",), "nm0", field_key="diff_preload_nm", range_key="diff_preload_nm"),
)


def adapt_sto(decoded: DecodedSto, car: str | None = None) -> StoAdaptedSetup:
    canonical_car = _canonical_car_name(car or decoded.car_id)
    raw_metadata = {
        "source_path": str(decoded.source_path),
        "version": decoded.version,
        "header_words": list(decoded.header_words),
        "sha256": decoded.sha256,
        "car_id": decoded.car_id,
        "provider_name": decoded.provider_name,
        "notes_text": decoded.notes_text,
        "raw_entries": [entry.to_dict() for entry in decoded.raw_entries],
    }
    if canonical_car == "acura":
        oracle = _KNOWN_ACURA_ORACLES.get(decoded.sha256)
        values = dict(oracle.values) if oracle else {}
        warnings = list(decoded.warnings)
        if oracle:
            warnings.extend(oracle.warnings)
        if not oracle:
            warnings.append("Acura v3 container decoded, but canonical field mapping is only available for known hashed fixtures.")
        return StoAdaptedSetup(
            car=canonical_car,
            adapter_name=oracle.name if oracle else "acura_v3_container",
            values=values,
            extra_values={"tire_type": _infer_tire_type(decoded)},
            decode_warnings=warnings,
            raw_sto_metadata=raw_metadata,
        )
    return StoAdaptedSetup(
        car=canonical_car,
        adapter_name="v3_container_only",
        decode_warnings=list(decoded.warnings),
        raw_sto_metadata=raw_metadata,
    )


def build_current_setup_fields(decoded: DecodedSto, car: str | None = None) -> StoAdaptedSetup:
    return adapt_sto(decoded, car=car)


def build_diff_rows(decoded: DecodedSto, car: str | None = None) -> list[StoRow]:
    adapted = adapt_sto(decoded, car=car)
    if adapted.car != "acura":
        return []

    values = dict(adapted.values)
    values.update(adapted.extra_values)

    rows: list[StoRow] = []
    for spec in _ACURA_ROW_SPECS:
        metric_value = _format_value(values, spec, imperial=False)
        imperial_value = _format_value(values, spec, imperial=True)
        field_def = get_field(spec.field_key) if spec.field_key else None
        is_derived = spec.is_derived if spec.is_derived is not None else bool(field_def and field_def.kind != "settable")
        rows.append(
            StoRow(
                row_id=_row_id(spec.key),
                label=spec.label,
                tab=spec.tab,
                tab_index=spec.tab_index,
                section=spec.section,
                section_index=spec.section_index,
                description=spec.description,
                metric_value=metric_value,
                imperial_value=imperial_value,
                range_metric=_format_range(spec.range_key, adapted.car, spec.formatter, imperial=False),
                range_imperial=_format_range(spec.range_key, adapted.car, spec.formatter, imperial=True),
                is_mapped=True,
                is_derived=is_derived,
            )
        )
    return rows
