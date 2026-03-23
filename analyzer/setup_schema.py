"""Canonical setup schema + Ferrari LDX correlation helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


_COMPUTED_TOKENS = (
    "AeroCalculator",
    "RideHeight",
    "CornerWeight",
    "ShockDeflStatic",
    "ShockDeflMax",
    "TorsionBarTurns",
    "TorsionBarDefl",
    "HeaveSpringDefl",
    "HeaveSliderDefl",
)
_CONTEXT_TOKENS = (
    "LastHotPressure",
    "LastTemps",
    "TreadRemaining",
)


@dataclass
class SetupField:
    """Canonical representation of one setup-related field."""

    canonical_key: str
    kind: str
    authoritative_source: str
    raw_value: Any = None
    raw_unit: str = ""
    raw_path: str | None = None
    decoded_value: Any = None
    decoded_unit: str = ""
    ldx_id: str | None = None
    oracle_value: Any = None
    oracle_unit: str = ""
    telemetry_channel: str | None = None
    telemetry_value: Any = None
    allowed_range: dict[str, Any] | None = None
    allowed_options: list[Any] | None = None
    resolution: float | int | None = None
    provenance: str = ""
    formula_note: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SetupSchema:
    """Read-only setup dump for auditing / correlation."""

    car: str
    adapter: str
    ibt_path: str = ""
    ldx_path: str = ""
    warnings: list[str] = field(default_factory=list)
    fields: list[SetupField] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "car": self.car,
            "adapter": self.adapter,
            "ibt_path": self.ibt_path,
            "ldx_path": self.ldx_path,
            "warnings": list(self.warnings),
            "fields": [field.to_dict() for field in self.fields],
        }


_KNOWN_FIELD_MAP: dict[str, tuple[str, str | None]] = {
    "CarSetup_TiresAero_AeroSettings_RearWingAngle": ("wing_angle_deg", "wing_angle_deg"),
    "CarSetup_Chassis_Front_PushrodLengthDelta": ("front_pushrod_mm", "front_pushrod_mm"),
    "CarSetup_Chassis_Rear_PushrodLengthDelta": ("rear_pushrod_mm", "rear_pushrod_mm"),
    "CarSetup_Chassis_Front_HeaveSpring": ("front_heave_index", None),
    "CarSetup_Chassis_Front_HeavePerchOffset": ("front_heave_perch_mm", "front_heave_perch_mm"),
    "CarSetup_Chassis_Rear_HeaveSpring": ("rear_heave_index", None),
    "CarSetup_Chassis_Rear_HeavePerchOffset": ("rear_heave_perch_mm", "rear_third_perch_mm"),
    "CarSetup_Chassis_LeftFront_TorsionBarOD": ("front_torsion_bar_index", None),
    "CarSetup_Chassis_LeftRear_TorsionBarOD": ("rear_torsion_bar_index", None),
    "CarSetup_Chassis_Front_ArbSize": ("front_arb_size", "front_arb_size"),
    "CarSetup_Chassis_Front_ArbBlades": ("front_arb_blade", "front_arb_blade"),
    "CarSetup_Chassis_Rear_ArbSize": ("rear_arb_size", "rear_arb_size"),
    "CarSetup_Chassis_Rear_ArbBlades": ("rear_arb_blade", "rear_arb_blade"),
    "CarSetup_Chassis_LeftFront_Camber": ("front_camber_deg", "front_camber_deg"),
    "CarSetup_Chassis_LeftRear_Camber": ("rear_camber_deg", "rear_camber_deg"),
    "CarSetup_Chassis_Front_ToeIn": ("front_toe_mm", "front_toe_mm"),
    "CarSetup_Chassis_LeftRear_ToeIn": ("rear_toe_mm", "rear_toe_mm"),
    "CarSetup_Dampers_LeftFrontDamper_LsCompDamping": ("front_ls_comp", "front_ls_comp"),
    "CarSetup_Dampers_LeftFrontDamper_LsRbdDamping": ("front_ls_rbd", "front_ls_rbd"),
    "CarSetup_Dampers_LeftFrontDamper_HsCompDamping": ("front_hs_comp", "front_hs_comp"),
    "CarSetup_Dampers_LeftFrontDamper_HsRbdDamping": ("front_hs_rbd", "front_hs_rbd"),
    "CarSetup_Dampers_LeftFrontDamper_HsCompDampSlope": ("front_hs_slope", "front_hs_slope"),
    "CarSetup_Dampers_LeftRearDamper_LsCompDamping": ("rear_ls_comp", "rear_ls_comp"),
    "CarSetup_Dampers_LeftRearDamper_LsRbdDamping": ("rear_ls_rbd", "rear_ls_rbd"),
    "CarSetup_Dampers_LeftRearDamper_HsCompDamping": ("rear_hs_comp", "rear_hs_comp"),
    "CarSetup_Dampers_LeftRearDamper_HsRbdDamping": ("rear_hs_rbd", "rear_hs_rbd"),
    "CarSetup_Dampers_LeftRearDamper_HsCompDampSlope": ("rear_hs_slope", "rear_hs_slope"),
    "CarSetup_Systems_BrakeSpec_BrakePressureBias": ("brake_bias_pct", "brake_bias_pct"),
    "CarSetup_Systems_BrakeSpec_BiasMigration": ("brake_bias_migration", "brake_bias_migration"),
    "CarSetup_Systems_BrakeSpec_BiasMigrationGain": ("brake_bias_migration_gain", "brake_bias_migration_gain"),
    "CarSetup_Systems_BrakeSpec_FrontMasterCyl": ("front_master_cyl_mm", "front_master_cyl_mm"),
    "CarSetup_Systems_BrakeSpec_RearMasterCyl": ("rear_master_cyl_mm", "rear_master_cyl_mm"),
    "CarSetup_Systems_BrakeSpec_PadCompound": ("pad_compound", "pad_compound"),
    "CarSetup_Systems_FrontDiffSpec_Preload": ("front_diff_preload_nm", "front_diff_preload_nm"),
    "CarSetup_Systems_RearDiffSpec_CoastDriveRampOptions": ("rear_diff_ramp_label", "diff_ramp_angles"),
    "CarSetup_Systems_RearDiffSpec_ClutchFrictionPlates": ("diff_clutch_plates", "diff_clutch_plates"),
    "CarSetup_Systems_RearDiffSpec_Preload": ("diff_preload_nm", "diff_preload_nm"),
    "CarSetup_Systems_TractionControl_TractionControlGain": ("tc_gain", "tc_gain"),
    "CarSetup_Systems_TractionControl_TractionControlSlip": ("tc_slip", "tc_slip"),
    "CarSetup_Systems_Fuel_FuelLevel": ("fuel_l", "fuel_l"),
    "CarSetup_Systems_Fuel_FuelLowWarning": ("fuel_low_warning_l", "fuel_low_warning_l"),
    "CarSetup_Systems_Fuel_FuelTarget": ("fuel_target_l", "fuel_target_l"),
    "CarSetup_Systems_GearRatios_GearStack": ("gear_stack", "gear_stack"),
    "CarSetup_Systems_HybridConfig_HybridRearDriveEnabled": ("hybrid_rear_drive_enabled", "hybrid_rear_drive_enabled"),
    "CarSetup_Systems_HybridConfig_HybridRearDriveCornerPct": ("hybrid_rear_drive_corner_pct", "hybrid_rear_drive_corner_pct"),
    "CarSetup_Systems_Lighting_RoofIdLightColor": ("roof_light_color", "roof_light_color"),
}

_TELEMETRY_CORRELATION = {
    "CarSetup_Systems_BrakeSpec_BrakePressureBias": ("dcBrakeBias", "live_brake_bias_pct"),
    "CarSetup_Systems_TractionControl_TractionControlGain": ("dcTractionControl2", "live_tc_gain"),
    "CarSetup_Systems_TractionControl_TractionControlSlip": ("dcTractionControl", "live_tc_slip"),
    "CarSetup_Chassis_Front_ArbBlades": ("dcAntiRollFront", "live_front_arb_blade"),
    "CarSetup_Chassis_Rear_ArbBlades": ("dcAntiRollRear", "live_rear_arb_blade"),
}

_FORMULA_NOTES = {
    "CarSetup_Chassis_LeftFront_RideHeight": "Static front ride height is computed as the average of left/right front RideHeight values.",
    "CarSetup_Chassis_LeftRear_RideHeight": "Static rear ride height is computed as the average of left/right rear RideHeight values.",
    "CarSetup_Chassis_LeftFront_TorsionBarTurns": "Computed display value from Ferrari indexed front torsion-bar selection and heave preload.",
    "CarSetup_Chassis_LeftRear_TorsionBarTurns": "Computed display value from Ferrari indexed rear torsion-bar selection and heave preload.",
    "CarSetup_Chassis_LeftFront_TorsionBarDefl": "Computed display value for front torsion-bar deflection under static garage load.",
    "CarSetup_Chassis_LeftRear_TorsionBarDefl": "Computed display value for rear torsion-bar deflection under static garage load.",
    "CarSetup_Chassis_LeftFront_ShockDeflStatic": "Computed static shock deflection shown by iRacing; not directly settable.",
    "CarSetup_Chassis_LeftRear_ShockDeflStatic": "Computed static shock deflection shown by iRacing; not directly settable.",
    "CarSetup_Chassis_Front_HeaveSpringDeflStatic": "Computed front heave-spring static deflection from indexed spring + perch + torsion bar state.",
    "CarSetup_Chassis_Front_HeaveSliderDeflStatic": "Computed front heave-slider static deflection from indexed spring + perch + torsion bar state.",
    "CarSetup_Chassis_Rear_HeaveSpringDeflStatic": "Computed rear heave-spring static deflection from indexed spring + perch state.",
    "CarSetup_Chassis_Rear_HeaveSliderDeflStatic": "Computed rear heave-slider static deflection from indexed spring + perch state.",
    "CarSetup_TiresAero_AeroCalculator_FrontRhAtSpeed": "Computed aero ride height at speed from iRacing's aero calculator, not a garage input.",
    "CarSetup_TiresAero_AeroCalculator_RearRhAtSpeed": "Computed aero ride height at speed from iRacing's aero calculator, not a garage input.",
    "CarSetup_TiresAero_AeroCalculator_DownforceBalance": "Computed aero balance from the active wing / ride-height state.",
    "CarSetup_TiresAero_AeroCalculator_LD": "Computed lift-to-drag ratio from the active aero state.",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _candidate_ferrari_data_dirs() -> list[Path]:
    root = _repo_root() / "data"
    candidates = []
    for name in ("ferraridata", "Ferraridata"):
        path = root / name
        if path.exists():
            candidates.append(path)
    return candidates


def _parse_scalar(value: str, tag: str) -> Any:
    if tag == "String":
        return value
    try:
        if any(ch in value for ch in (".", "e", "E")):
            return float(value)
        return int(value)
    except (TypeError, ValueError):
        return value


def find_matching_ferrari_ldx(ibt_path: str | Path | None) -> Path | None:
    """Return the Ferrari .ldx fixture that matches an IBT session name."""
    if ibt_path is None:
        return None
    stem = Path(ibt_path).stem
    for root in _candidate_ferrari_data_dirs():
        exact = root / f"{stem}_Stint_1.ldx"
        if exact.exists():
            return exact
        matches = sorted(root.glob(f"{stem}_Stint_*.ldx"))
        if matches:
            return matches[0]
    return None


def parse_ldx_setup_entries(ldx_path: str | Path | None) -> dict[str, dict[str, Any]]:
    """Parse CarSetup_* values from an LDX XML file."""
    if ldx_path is None:
        return {}
    path = Path(ldx_path)
    if not path.exists():
        return {}
    root = ElementTree.fromstring(path.read_text(encoding="utf-8"))
    entries: dict[str, dict[str, Any]] = {}
    for node in root.findall(".//Details/*"):
        tag = node.tag
        field_id = node.attrib.get("Id", "")
        if not field_id.startswith("CarSetup_"):
            continue
        value = node.attrib.get("Value", "")
        entries[field_id] = {
            "type": tag,
            "value": _parse_scalar(value, tag),
            "unit": node.attrib.get("Unit", ""),
        }
    return entries


@lru_cache(maxsize=1)
def ferrari_ldx_oracle() -> dict[str, dict[str, Any]]:
    """Observed Ferrari LDX values across the local fixture corpus."""
    catalog: dict[str, dict[str, Any]] = {}
    for root in _candidate_ferrari_data_dirs():
        for ldx_path in sorted(root.glob("*.ldx")):
            entries = parse_ldx_setup_entries(ldx_path)
            for field_id, entry in entries.items():
                bucket = catalog.setdefault(
                    field_id,
                    {
                        "type": entry["type"],
                        "unit": entry["unit"],
                        "values": [],
                    },
                )
                bucket["values"].append(entry["value"])
    return catalog


def _derive_kind(field_id: str) -> str:
    if any(token in field_id for token in _CONTEXT_TOKENS):
        return "context"
    if any(token in field_id for token in _COMPUTED_TOKENS):
        return "computed"
    return "settable"


def _canonical_key(field_id: str) -> str:
    if field_id in _KNOWN_FIELD_MAP:
        return _KNOWN_FIELD_MAP[field_id][0]
    key = field_id
    if key.startswith("CarSetup_"):
        key = key[len("CarSetup_"):]
    return key.replace("[", "_").replace("]", "").replace("'", "").lower()


def _resolution_from_values(values: list[Any]) -> float | int | None:
    numeric = []
    for value in values:
        if isinstance(value, (int, float)):
            numeric.append(float(value))
    unique = sorted({round(value, 6) for value in numeric})
    if len(unique) < 2:
        return None
    deltas = [round(b - a, 6) for a, b in zip(unique, unique[1:]) if round(b - a, 6) > 0]
    if not deltas:
        return None
    step = min(deltas)
    if abs(step - round(step)) < 1e-6:
        return int(round(step))
    return step


def _observed_constraints(field_id: str) -> tuple[dict[str, Any] | None, list[Any] | None, float | int | None]:
    oracle = ferrari_ldx_oracle().get(field_id, {})
    values = list(oracle.get("values", []))
    if not values:
        return None, None, None
    unique_values = list(dict.fromkeys(values))
    if all(isinstance(value, str) for value in unique_values):
        return None, sorted({str(value) for value in unique_values}), None
    numeric = [float(value) for value in unique_values if isinstance(value, (int, float))]
    if not numeric:
        return None, None, None
    value_range = {"min": min(numeric), "max": max(numeric), "source": "observed_ldx"}
    resolution = _resolution_from_values(unique_values)
    options = None
    if len(unique_values) <= 12:
        options = sorted(unique_values)
    return value_range, options, resolution


def _manual_constraints(car: Any, field_id: str) -> tuple[dict[str, Any] | None, list[Any] | None, float | int | None]:
    gr = getattr(car, "garage_ranges", None)
    if gr is None:
        return None, None, None
    if field_id == "CarSetup_TiresAero_AeroSettings_RearWingAngle":
        return None, list(getattr(car, "wing_angles", [])), None
    if field_id == "CarSetup_Chassis_Front_HeaveSpring":
        return {"min": gr.front_heave_nmm[0], "max": gr.front_heave_nmm[1], "source": "car_model"}, None, gr.heave_spring_resolution_nmm
    if field_id == "CarSetup_Chassis_Rear_HeaveSpring":
        return {"min": gr.rear_third_nmm[0], "max": gr.rear_third_nmm[1], "source": "car_model"}, None, gr.heave_spring_resolution_nmm
    if field_id == "CarSetup_Chassis_LeftFront_TorsionBarOD":
        return {"min": gr.front_torsion_od_mm[0], "max": gr.front_torsion_od_mm[1], "source": "car_model"}, None, gr.rear_spring_resolution_nmm
    if field_id == "CarSetup_Chassis_LeftRear_TorsionBarOD":
        return {"min": gr.rear_spring_nmm[0], "max": gr.rear_spring_nmm[1], "source": "car_model"}, None, gr.rear_spring_resolution_nmm
    if field_id in ("CarSetup_Chassis_Front_ArbBlades", "CarSetup_Chassis_Rear_ArbBlades"):
        return {"min": gr.arb_blade[0], "max": gr.arb_blade[1], "source": "car_model"}, None, 1
    if field_id in ("CarSetup_Chassis_Front_ArbSize", "CarSetup_Chassis_Rear_ArbSize"):
        labels = getattr(car.arb, "front_size_labels" if "Front" in field_id else "rear_size_labels", [])
        return None, list(labels), None
    if field_id in ("CarSetup_Chassis_LeftFront_Camber", "CarSetup_Chassis_RightFront_Camber"):
        return {"min": gr.camber_front_deg[0], "max": gr.camber_front_deg[1], "source": "car_model"}, None, 0.1
    if field_id in ("CarSetup_Chassis_LeftRear_Camber", "CarSetup_Chassis_RightRear_Camber"):
        return {"min": gr.camber_rear_deg[0], "max": gr.camber_rear_deg[1], "source": "car_model"}, None, 0.1
    if field_id == "CarSetup_Chassis_Front_ToeIn":
        return {"min": gr.toe_front_mm[0], "max": gr.toe_front_mm[1], "source": "car_model"}, None, 0.1
    if field_id in ("CarSetup_Chassis_LeftRear_ToeIn", "CarSetup_Chassis_RightRear_ToeIn"):
        return {"min": gr.toe_rear_mm[0], "max": gr.toe_rear_mm[1], "source": "car_model"}, None, 0.1
    if field_id.startswith("CarSetup_Dampers_"):
        upper = car.damper.hs_slope_range[1] if "HsCompDampSlope" in field_id else car.damper.ls_comp_range[1]
        return {"min": car.damper.ls_comp_range[0], "max": upper, "source": "car_model"}, None, 1
    if field_id == "CarSetup_Systems_RearDiffSpec_ClutchFrictionPlates":
        return None, list(gr.diff_clutch_plates_options), None
    if field_id == "CarSetup_Systems_RearDiffSpec_Preload":
        return {"min": gr.diff_preload_nm[0], "max": gr.diff_preload_nm[1], "source": "car_model"}, None, gr.diff_preload_step_nm
    if field_id == "CarSetup_Systems_Fuel_FuelLevel":
        return {"min": 0.0, "max": gr.max_fuel_l, "source": "car_model"}, None, 1
    return None, None, None


def _attr_value(obj: Any, attr_name: str | None) -> Any:
    if obj is None or not attr_name:
        return None
    return getattr(obj, attr_name, None)


def _build_ldx_field(field_id: str, entry: dict[str, Any], *, car: Any, current_setup: Any, measured: Any, ldx_path: Path | None) -> SetupField:
    canonical_key, setup_attr = _KNOWN_FIELD_MAP.get(field_id, (_canonical_key(field_id), None))
    telemetry_channel = None
    telemetry_value = None
    if field_id in _TELEMETRY_CORRELATION and measured is not None:
        telemetry_channel, measured_attr = _TELEMETRY_CORRELATION[field_id]
        telemetry_value = getattr(measured, measured_attr, None)

    manual_range, manual_options, manual_resolution = _manual_constraints(car, field_id)
    observed_range, observed_options, observed_resolution = _observed_constraints(field_id)
    allowed_range = manual_range or observed_range
    allowed_options = manual_options or observed_options
    resolution = manual_resolution if manual_resolution is not None else observed_resolution

    decoded_value = _attr_value(current_setup, setup_attr)
    decoded_unit = entry.get("unit", "")
    if canonical_key.endswith("_index") or "torsion_bar_index" in canonical_key:
        decoded_value = None
        decoded_unit = ""

    authoritative_source = "ldx"
    notes: list[str] = []
    if telemetry_value is not None:
        authoritative_source = "telemetry"
        notes.append(f"Stable live control observed on {telemetry_channel}.")

    provenance = "Matched Ferrari LDX fixture"
    if ldx_path is not None:
        provenance += f" {ldx_path.name}"
    return SetupField(
        canonical_key=canonical_key,
        kind=_derive_kind(field_id),
        authoritative_source=authoritative_source,
        raw_value=entry.get("value"),
        raw_unit=entry.get("unit", ""),
        raw_path=field_id,
        decoded_value=decoded_value,
        decoded_unit=decoded_unit,
        ldx_id=field_id,
        oracle_value=entry.get("value"),
        oracle_unit=entry.get("unit", ""),
        telemetry_channel=telemetry_channel,
        telemetry_value=telemetry_value,
        allowed_range=allowed_range,
        allowed_options=allowed_options,
        resolution=resolution,
        provenance=provenance,
        formula_note=_FORMULA_NOTES.get(field_id, ""),
        notes=notes,
    )


def _synthetic_fields(current_setup: Any, measured: Any) -> list[SetupField]:
    if current_setup is None:
        return []
    fields = [
        SetupField(
            canonical_key="static_front_rh_mm",
            kind="computed",
            authoritative_source="ibt",
            raw_value=getattr(current_setup, "static_front_rh_mm", 0.0),
            raw_unit="mm",
            raw_path="CurrentSetup.static_front_rh_mm",
            decoded_value=getattr(current_setup, "static_front_rh_mm", 0.0),
            decoded_unit="mm",
            provenance="Derived from IBT session info",
            formula_note="Average of CarSetup_Chassis_LeftFront_RideHeight and RightFront_RideHeight.",
        ),
        SetupField(
            canonical_key="static_rear_rh_mm",
            kind="computed",
            authoritative_source="ibt",
            raw_value=getattr(current_setup, "static_rear_rh_mm", 0.0),
            raw_unit="mm",
            raw_path="CurrentSetup.static_rear_rh_mm",
            decoded_value=getattr(current_setup, "static_rear_rh_mm", 0.0),
            decoded_unit="mm",
            provenance="Derived from IBT session info",
            formula_note="Average of CarSetup_Chassis_LeftRear_RideHeight and RightRear_RideHeight.",
        ),
    ]
    if measured is not None and getattr(measured, "live_brake_bias_pct", None) is not None:
        fields.append(
            SetupField(
                canonical_key="live_brake_bias_pct",
                kind="context",
                authoritative_source="telemetry",
                raw_value=getattr(measured, "live_brake_bias_pct"),
                raw_unit="%",
                raw_path="dcBrakeBias",
                decoded_value=getattr(current_setup, "brake_bias_pct", None),
                decoded_unit="%",
                telemetry_channel="dcBrakeBias",
                telemetry_value=getattr(measured, "live_brake_bias_pct"),
                provenance="Stable live control from IBT telemetry",
                formula_note="Direct in-car brake-bias channel; preferred over hydraulic brake split for Ferrari.",
            )
        )
    return fields


def build_setup_schema(
    *,
    car: Any,
    ibt_path: str | Path | None = None,
    current_setup: Any | None = None,
    measured: Any | None = None,
) -> SetupSchema:
    """Build a canonical setup schema for read-only inspection."""
    car_name = getattr(car, "canonical_name", str(car))
    if car_name != "ferrari":
        schema = SetupSchema(
            car=car_name,
            adapter="generic",
            ibt_path=str(ibt_path or ""),
            warnings=[],
        )
        if current_setup is not None and is_dataclass(current_setup):
            for key, value in asdict(current_setup).items():
                if isinstance(value, (int, float, str)) and key not in {"source", "adapter_name"}:
                    schema.fields.append(
                        SetupField(
                            canonical_key=key,
                            kind="settable",
                            authoritative_source="ibt",
                            raw_value=value,
                            raw_path=f"CurrentSetup.{key}",
                            provenance="CurrentSetup dataclass value",
                        )
                    )
        return schema

    ldx_path = find_matching_ferrari_ldx(ibt_path)
    schema = SetupSchema(
        car=car_name,
        adapter="ferrari_ldx_oracle",
        ibt_path=str(ibt_path or ""),
        ldx_path=str(ldx_path or ""),
    )
    if ldx_path is None:
        schema.warnings.append("No matching Ferrari LDX fixture found; schema is IBT-only.")
    entries = parse_ldx_setup_entries(ldx_path)
    for field_id in sorted(entries):
        schema.fields.append(
            _build_ldx_field(
                field_id,
                entries[field_id],
                car=car,
                current_setup=current_setup,
                measured=measured,
                ldx_path=ldx_path,
            )
        )
    schema.fields.extend(_synthetic_fields(current_setup, measured))
    schema.fields.sort(key=lambda field: field.canonical_key)
    return schema


def apply_live_control_overrides(current_setup: Any, measured: Any) -> list[str]:
    """Promote stable Ferrari live controls into the authoritative setup view."""
    if getattr(current_setup, "adapter_name", "") != "ferrari" or measured is None:
        return []
    applied: list[str] = []
    for setup_attr, measured_attr, label in (
        ("brake_bias_pct", "live_brake_bias_pct", "dcBrakeBias"),
        ("tc_gain", "live_tc_gain", "dcTractionControl2"),
        ("tc_slip", "live_tc_slip", "dcTractionControl"),
        ("front_arb_blade", "live_front_arb_blade", "dcAntiRollFront"),
        ("rear_arb_blade", "live_rear_arb_blade", "dcAntiRollRear"),
    ):
        value = getattr(measured, measured_attr, None)
        if value is None:
            continue
        if getattr(current_setup, setup_attr, None) != value:
            setattr(current_setup, setup_attr, value)
            applied.append(f"{setup_attr} <= {label} ({value})")
    if applied and hasattr(current_setup, "decode_warnings"):
        current_setup.decode_warnings.append(
            "Ferrari live controls were promoted from stable telemetry channels where available."
        )
    return applied
