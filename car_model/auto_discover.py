"""Auto-discover car setup parameters from iRacing setup JSON dumps.

iRacing exposes the FULL setup parameter tree in both the IBT session info
YAML and in a richer JSON format (available via the iRacing API or the
setup editor).  The JSON format contains:

  - Every row in the garage (mapped and unmapped)
  - The tab / section hierarchy
  - Human-readable labels, descriptions, metric+imperial values
  - Min/max ranges (when available)
  - Hidden/internal parameters (is_mapped=False) that include raw N/m rates,
    perch offsets in meters, packer thicknesses, heave damper settings, etc.

The current codebase only reads the structured YAML from IBT session info
(see analyzer/setup_reader.py), which maps named keys like
``CarSetup.Chassis.Front.HeaveSpring``.  That approach requires manually
coding the key path for every car.  More critically, it MISSES the unmapped
rows — the hidden internal parameters that iRacing computes but does not
surface in the garage UI.  These hidden params are often the raw physics
values (spring rates in N/m, offsets in meters) that the solver needs but
currently estimates or borrows from BMW.

This module solves three problems:

1. **Parameter discovery**: Given a setup JSON, identify EVERY settable
   and internal parameter, classify it, and extract its value + range.
2. **Garage model bootstrap**: From hidden ``fSideSpringRateNpm`` and
   similar fields, directly compute spring rates, motion ratios, and
   constants that currently require manual garage-screenshot calibration.
3. **Cross-car portability**: Since the JSON format is identical across
   all iRacing cars, one ingestion path works for BMW, Ferrari, Acura,
   Porsche, Cadillac, and any future car without car-specific parsing.

Usage::

    from car_model.auto_discover import discover_car_parameters
    params = discover_car_parameters(json_data)
    print(params.spring_rates)
    print(params.damper_ranges)
    print(params.hidden_physics)
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Value parsing ──────────────────────────────────────────────────────────

def _parse_metric_value(raw: str | None) -> float | None:
    """Extract a float from a metric_value string like '58.0 L' or '17 deg'."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    m = re.match(r"([+-]?\d+\.?\d*)", s)
    if m:
        return float(m.group(1))
    return None


def _parse_range(range_obj: dict | None) -> tuple[float, float] | None:
    """Parse a range_metric dict with 'min' and 'max' keys."""
    if range_obj is None:
        return None
    lo = _parse_metric_value(range_obj.get("min"))
    hi = _parse_metric_value(range_obj.get("max"))
    if lo is not None and hi is not None:
        return (lo, hi)
    return None


# ── Parameter classification ──────────────────────────────────────────────

@dataclass
class SetupRow:
    """One row from the setup JSON, classified."""
    row_id: str
    label: str
    tab: str | None
    section: str | None
    value_raw: str
    value: float | None
    range_metric: tuple[float, float] | None
    is_mapped: bool
    is_derived: bool
    description: str | None
    category: str = ""


@dataclass
class HiddenPhysics:
    """Physics values extracted from unmapped (hidden) rows."""
    front_spring_rate_npm: float | None = None   # fSideSpringRateNpm
    rear_spring_rate_npm: float | None = None     # rSideSpringRateNpm
    lr_perch_offset_m: float | None = None        # lrPerchOffsetm
    rr_perch_offset_m: float | None = None        # rrPerchOffsetm
    hf_packer_thickness_m: float | None = None
    hr_packer_thickness_m: float | None = None
    lf_packer_thickness_m: float | None = None
    rf_packer_thickness_m: float | None = None
    lr_packer_thickness_m: float | None = None
    rr_packer_thickness_m: float | None = None
    brake_master_cyl_front_m: float | None = None  # brakeMasterCylDiaFm
    brake_master_cyl_rear_m: float | None = None   # brakeMasterCylDiaRm
    # Heave damper settings (unmapped on Ferrari)
    hf_ls_comp: int | None = None
    hr_ls_comp: int | None = None
    hf_hs_comp: int | None = None
    hr_hs_comp: int | None = None
    hf_ls_rbd: int | None = None
    hr_ls_rbd: int | None = None
    hf_hs_rbd: int | None = None
    hr_hs_rbd: int | None = None
    hf_hs_slope_comp: int | None = None
    hr_hs_slope_comp: int | None = None
    hf_hs_slope_rbd: int | None = None
    hr_hs_slope_rbd: int | None = None
    # Per-corner HS rebound slope (unmapped on Ferrari)
    lf_hs_slope_rbd: int | None = None
    rf_hs_slope_rbd: int | None = None
    lr_hs_slope_rbd: int | None = None
    rr_hs_slope_rbd: int | None = None
    # BoP fields
    d_cx_bop: float | None = None
    d_cz_t_bop: float | None = None
    d_cpxz_bop: float | None = None
    # Hub pitch (caster-related)
    lf_hub_dpitch: float | None = None
    rf_hub_dpitch: float | None = None
    # Hybrid/ERS
    mguk_deploy_throttle: int | None = None
    deploy_setting_request: int | None = None
    mguk_regen_gain: int | None = None
    # Drive settings
    throttle_shape: int | None = None
    clutch_bite_point: int | None = None


_HIDDEN_FIELD_MAP: dict[str, str] = {
    "fSideSpringRateNpm": "front_spring_rate_npm",
    "rSideSpringRateNpm": "rear_spring_rate_npm",
    "lrPerchOffsetm": "lr_perch_offset_m",
    "rrPerchOffsetm": "rr_perch_offset_m",
    "hfPackerThicknessm": "hf_packer_thickness_m",
    "hrPackerThicknessm": "hr_packer_thickness_m",
    "lfPackerThicknessm": "lf_packer_thickness_m",
    "rfPackerThicknessm": "rf_packer_thickness_m",
    "lrPackerThicknessm": "lr_packer_thickness_m",
    "rrPackerThicknessm": "rr_packer_thickness_m",
    "brakeMasterCylDiaFm": "brake_master_cyl_front_m",
    "brakeMasterCylDiaRm": "brake_master_cyl_rear_m",
    "hfLowSpeedCompDampSetting": "hf_ls_comp",
    "hrLowSpeedCompDampSetting": "hr_ls_comp",
    "hfHighSpeedCompDampSetting": "hf_hs_comp",
    "hrHighSpeedCompDampSetting": "hr_hs_comp",
    "hfLowSpeedRbdDampSetting": "hf_ls_rbd",
    "hrLowSpeedRbdDampSetting": "hr_ls_rbd",
    "hfHighSpeedRbdDampSetting": "hf_hs_rbd",
    "hrHighSpeedRbdDampSetting": "hr_hs_rbd",
    "hfHSSlopeCompDampSetting": "hf_hs_slope_comp",
    "hrHSSlopeCompDampSetting": "hr_hs_slope_comp",
    "hfHSSlopeRbdDampSetting": "hf_hs_slope_rbd",
    "hrHSSlopeRbdDampSetting": "hr_hs_slope_rbd",
    "lfHSSlopeRbdDampSetting": "lf_hs_slope_rbd",
    "rfHSSlopeRbdDampSetting": "rf_hs_slope_rbd",
    "lrHSSlopeRbdDampSetting": "lr_hs_slope_rbd",
    "rrHSSlopeRbdDampSetting": "rr_hs_slope_rbd",
    "dCxBoP": "d_cx_bop",
    "dCzTBoP": "d_cz_t_bop",
    "dCPxzBoP": "d_cpxz_bop",
    "lfhubDpitch": "lf_hub_dpitch",
    "rfhubDpitch": "rf_hub_dpitch",
    "mgukDeployThrottleSetting": "mguk_deploy_throttle",
    "deploySettingRequest": "deploy_setting_request",
    "mgukRegenGainSetting": "mguk_regen_gain",
    "throttleShapeSetting": "throttle_shape",
    "clutchBitePointSetting": "clutch_bite_point",
}


@dataclass
class DamperSpec:
    """Discovered damper ranges and current values for one corner or axis."""
    ls_comp: int = 0
    ls_comp_range: tuple[int, int] = (0, 0)
    ls_rbd: int = 0
    ls_rbd_range: tuple[int, int] = (0, 0)
    hs_comp: int = 0
    hs_comp_range: tuple[int, int] = (0, 0)
    hs_rbd: int = 0
    hs_rbd_range: tuple[int, int] = (0, 0)
    hs_slope: int = 0
    hs_slope_range: tuple[int, int] = (0, 0)


@dataclass
class DiscoveredParameters:
    """Complete parameter set discovered from one setup JSON dump."""

    car_name: str
    rows: list[SetupRow]
    hidden: HiddenPhysics

    # Mapped garage values by (section_key, label)
    garage_values: dict[str, float | str] = field(default_factory=dict)
    garage_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)

    # Damper specs per corner/section
    dampers: dict[str, DamperSpec] = field(default_factory=dict)

    # Derived calibration data
    front_torsion_bar_index: float | None = None
    rear_torsion_bar_index: float | None = None
    front_heave_index: float | None = None
    rear_heave_index: float | None = None

    # Spring rate calibration from hidden physics
    front_corner_spring_rate_nmm: float | None = None
    rear_corner_spring_rate_nmm: float | None = None

    def corner_spring_calibration_point(self) -> dict[str, Any] | None:
        """If we have a torsion bar index AND a hidden spring rate, return
        a calibration point that can be used to compute C constant.

        Returns dict with keys: index, spring_rate_nmm, which can be used
        as: C = rate_nmm / OD_mm^4 once OD is known from the index mapping.
        """
        if (self.front_torsion_bar_index is not None
                and self.front_corner_spring_rate_nmm is not None):
            return {
                "axle": "front",
                "index": self.front_torsion_bar_index,
                "spring_rate_nmm": self.front_corner_spring_rate_nmm,
            }
        return None

    def heave_spring_calibration_point(self) -> dict[str, Any] | None:
        """If we have a heave spring index AND a hidden spring rate from
        the heave spring N/m field, return a calibration point.
        """
        if self.front_heave_index is not None and self.hidden.front_spring_rate_npm is not None:
            return {
                "axle": "front",
                "index": self.front_heave_index,
                "spring_rate_nmm": self.hidden.front_spring_rate_npm / 1000.0,
            }
        return None

    def summary(self) -> str:
        """Human-readable summary of discovered parameters."""
        mapped = sum(1 for r in self.rows if r.is_mapped)
        unmapped = sum(1 for r in self.rows if not r.is_mapped)
        hidden_count = sum(
            1 for v in vars(self.hidden).values() if v is not None
        )
        lines = [
            f"Car: {self.car_name}",
            f"Total rows: {len(self.rows)} ({mapped} mapped, {unmapped} unmapped/hidden)",
            f"Hidden physics values extracted: {hidden_count}",
            f"Garage ranges discovered: {len(self.garage_ranges)}",
            f"Damper sections: {len(self.dampers)}",
        ]
        if self.front_corner_spring_rate_nmm is not None:
            lines.append(f"Front corner spring rate: {self.front_corner_spring_rate_nmm:.1f} N/mm (from hidden fSideSpringRateNpm)")
        if self.rear_corner_spring_rate_nmm is not None:
            lines.append(f"Rear corner spring rate: {self.rear_corner_spring_rate_nmm:.1f} N/mm (from hidden rSideSpringRateNpm)")
        if self.front_heave_index is not None:
            lines.append(f"Front heave spring index: {self.front_heave_index}")
        if self.rear_heave_index is not None:
            lines.append(f"Rear heave spring index: {self.rear_heave_index}")
        if self.front_torsion_bar_index is not None:
            lines.append(f"Front torsion bar index: {self.front_torsion_bar_index}")
        if self.rear_torsion_bar_index is not None:
            lines.append(f"Rear torsion bar index: {self.rear_torsion_bar_index}")
        hp = self.hidden
        if hp.front_spring_rate_npm is not None:
            lines.append(f"Hidden front spring rate: {hp.front_spring_rate_npm:.1f} N/m = {hp.front_spring_rate_npm/1000:.2f} N/mm")
        if hp.rear_spring_rate_npm is not None:
            lines.append(f"Hidden rear spring rate: {hp.rear_spring_rate_npm:.1f} N/m = {hp.rear_spring_rate_npm/1000:.2f} N/mm")
        if hp.lr_perch_offset_m is not None:
            lines.append(f"Hidden LR perch offset: {hp.lr_perch_offset_m*1000:.2f} mm")
        if hp.hf_ls_comp is not None:
            lines.append(f"Hidden heave front LS comp: {hp.hf_ls_comp}")
        if hp.hf_hs_comp is not None:
            lines.append(f"Hidden heave front HS comp: {hp.hf_hs_comp}")
        cal = self.corner_spring_calibration_point()
        if cal:
            lines.append(f"CALIBRATION POINT: torsion idx {cal['index']} → {cal['spring_rate_nmm']:.1f} N/mm")
        hcal = self.heave_spring_calibration_point()
        if hcal:
            lines.append(f"HEAVE CALIBRATION: heave idx {hcal['index']} → {hcal['spring_rate_nmm']:.1f} N/mm")
        return "\n".join(lines)


def _section_key(tab: str | None, section: str | None) -> str:
    """Build a lookup key from tab+section."""
    t = (tab or "").strip()
    s = (section or "").strip()
    if t and s:
        return f"{t}/{s}"
    return t or s or "unknown"


def _classify_row(label: str, tab: str | None, section: str | None) -> str:
    """Assign a category to a row based on its label and location."""
    ll = label.lower()
    sl = (section or "").lower()

    if "damper" in sl or "damp" in ll:
        return "damper"
    if "torsion" in ll:
        return "corner_spring"
    if "heave" in ll or "third" in ll:
        return "heave_spring"
    if "arb" in ll:
        return "arb"
    if "camber" in ll:
        return "geometry"
    if "toe" in ll:
        return "geometry"
    if "pushrod" in ll:
        return "ride_height"
    if "perch" in ll:
        return "ride_height"
    if "pressure" in ll and "brake" not in ll:
        return "tyre"
    if "brake" in ll or "bias" in ll or "master" in ll or "pad" in ll:
        return "brake"
    if "diff" in sl or "preload" in ll or "clutch" in ll or "ramp" in ll:
        return "diff"
    if "traction" in ll or "tc" in ll:
        return "tc"
    if "fuel" in ll:
        return "fuel"
    if "wing" in ll:
        return "aero"
    if "ride" in ll and "height" in ll:
        return "ride_height"
    if "gear" in ll:
        return "gearing"
    if "hybrid" in ll or "mguk" in ll or "deploy" in ll or "regen" in ll:
        return "hybrid"
    if "bop" in ll.lower():
        return "bop"
    return "other"


def discover_car_parameters(data: dict[str, Any]) -> DiscoveredParameters:
    """Ingest a full setup JSON dump and discover all parameters.

    Args:
        data: The parsed JSON dict with keys 'carName' and 'rows'.

    Returns:
        DiscoveredParameters with classified rows, hidden physics,
        garage values/ranges, damper specs, and calibration points.
    """
    car_name = data.get("carName", "unknown")
    raw_rows = data.get("rows", [])

    rows: list[SetupRow] = []
    hidden = HiddenPhysics()
    garage_values: dict[str, float | str] = {}
    garage_ranges: dict[str, tuple[float, float]] = {}
    dampers: dict[str, DamperSpec] = {}

    front_torsion_idx: float | None = None
    rear_torsion_idx: float | None = None
    front_heave_idx: float | None = None
    rear_heave_idx: float | None = None

    for raw in raw_rows:
        label = raw.get("label", "")
        tab = raw.get("tab")
        section = raw.get("section")
        metric_val = raw.get("metric_value", "")
        is_mapped = raw.get("is_mapped", False)
        is_derived = raw.get("is_derived", False)
        desc = raw.get("description")
        range_m = _parse_range(raw.get("range_metric"))
        val = _parse_metric_value(metric_val)
        category = _classify_row(label, tab, section)

        row = SetupRow(
            row_id=raw.get("row_id", ""),
            label=label,
            tab=tab,
            section=section,
            value_raw=str(metric_val),
            value=val,
            range_metric=range_m,
            is_mapped=is_mapped,
            is_derived=is_derived,
            description=desc,
            category=category,
        )
        rows.append(row)

        # Extract hidden physics from unmapped rows
        if not is_mapped and label in _HIDDEN_FIELD_MAP:
            attr = _HIDDEN_FIELD_MAP[label]
            if val is not None:
                if attr.endswith(("_comp", "_rbd", "_slope_comp", "_slope_rbd",
                                  "_throttle", "_gain", "_shape", "_point")):
                    setattr(hidden, attr, int(val))
                else:
                    setattr(hidden, attr, val)

        # Track mapped garage values and ranges
        if is_mapped and val is not None:
            sk = _section_key(tab, section)
            key = f"{sk}/{label}"
            garage_values[key] = val
            if range_m:
                garage_ranges[key] = range_m

        # Identify indexed spring/torsion values
        if is_mapped and label == "Torsion bar O.D." and val is not None:
            s_lower = (section or "").lower()
            if "front" in s_lower:
                front_torsion_idx = val
            elif "rear" in s_lower:
                rear_torsion_idx = val
        if is_mapped and label == "Heave spring" and val is not None:
            s_lower = (section or "").lower()
            if "front" in s_lower:
                front_heave_idx = val
            elif "rear" in s_lower:
                rear_heave_idx = val

        # Collect damper specs
        if is_mapped and category == "damper" and section and val is not None:
            if section not in dampers:
                dampers[section] = DamperSpec()
            ds = dampers[section]
            ll = label.lower()
            iv = int(val)
            ir = (int(range_m[0]), int(range_m[1])) if range_m else (0, 0)
            if "ls comp" in ll:
                ds.ls_comp = iv
                ds.ls_comp_range = ir
            elif "ls rbd" in ll:
                ds.ls_rbd = iv
                ds.ls_rbd_range = ir
            elif "hs comp damp slope" in ll or "hs comp damp slope" in ll:
                ds.hs_slope = iv
                ds.hs_slope_range = ir
            elif "hs comp" in ll:
                ds.hs_comp = iv
                ds.hs_comp_range = ir
            elif "hs rbd" in ll:
                ds.hs_rbd = iv
                ds.hs_rbd_range = ir

    # Derive spring rates from hidden N/m values
    front_rate_nmm = None
    rear_rate_nmm = None
    if hidden.front_spring_rate_npm is not None:
        front_rate_nmm = hidden.front_spring_rate_npm / 1000.0
    if hidden.rear_spring_rate_npm is not None:
        rear_rate_nmm = hidden.rear_spring_rate_npm / 1000.0

    return DiscoveredParameters(
        car_name=car_name,
        rows=rows,
        hidden=hidden,
        garage_values=garage_values,
        garage_ranges=garage_ranges,
        dampers=dampers,
        front_torsion_bar_index=front_torsion_idx,
        rear_torsion_bar_index=rear_torsion_idx,
        front_heave_index=front_heave_idx,
        rear_heave_index=rear_heave_idx,
        front_corner_spring_rate_nmm=front_rate_nmm,
        rear_corner_spring_rate_nmm=rear_rate_nmm,
    )


def compute_torsion_c_from_points(
    points: list[dict[str, float]],
    od_from_index_fn: Any | None = None,
) -> dict[str, Any]:
    """Given multiple (index, spring_rate_nmm) points, fit C in k = C * OD^4.

    If ``od_from_index_fn`` is provided, it maps index → OD_mm.
    Otherwise, we try to fit both C and the linear OD-index relationship.

    Returns dict with: C, od_min, od_max, r_squared, residuals.
    """
    if len(points) < 2:
        return {"error": "need at least 2 calibration points"}

    if od_from_index_fn is not None:
        cs = []
        for pt in points:
            od = od_from_index_fn(pt["index"])
            rate = pt["spring_rate_nmm"]
            c = rate / (od ** 4)
            cs.append(c)
        c_mean = sum(cs) / len(cs)
        residuals = [(c - c_mean) / c_mean * 100 for c in cs]
        return {
            "C": c_mean,
            "residuals_pct": residuals,
            "n_points": len(points),
        }

    # Without known OD mapping, use k^(1/4) = a + b*index linear fit
    # then C = 1.0 (normalized), and OD = k^(1/4) / C^(1/4)
    xs = [pt["index"] for pt in points]
    ys = [pt["spring_rate_nmm"] ** 0.25 for pt in points]
    n = len(xs)
    sx = sum(xs)
    sy = sum(ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sxx = sum(x * x for x in xs)
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return {"error": "degenerate data"}
    b = (n * sxy - sx * sy) / denom
    a = (sy - b * sx) / n
    y_pred = [a + b * x for x in xs]
    ss_res = sum((yi - yp) ** 2 for yi, yp in zip(ys, y_pred))
    y_mean = sy / n
    ss_tot = sum((yi - y_mean) ** 2 for yi in ys)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    od_at_0 = a  # k^(1/4) at index 0
    od_at_max = a + b * max(xs)

    return {
        "k_quarter_intercept": a,
        "k_quarter_slope": b,
        "C_if_od_equals_k_quarter": 1.0,
        "od_range_proxy": (od_at_0, od_at_max),
        "r_squared": r2,
        "n_points": n,
    }


def build_calibration_dataset(
    discoveries: list[DiscoveredParameters],
) -> dict[str, Any]:
    """From multiple setup JSON ingestions, build a calibration dataset.

    Each DiscoveredParameters provides one (index, rate) calibration point.
    With 3+ points we can fit the torsion bar C constant and OD mapping,
    and with 2+ heave spring points we can fit the index→rate mapping.
    """
    car_name = discoveries[0].car_name if discoveries else "unknown"

    # Collect torsion bar calibration points
    torsion_points: list[dict[str, Any]] = []
    heave_points: list[dict[str, Any]] = []

    for d in discoveries:
        tp = d.corner_spring_calibration_point()
        if tp:
            torsion_points.append(tp)
        hp = d.heave_spring_calibration_point()
        if hp:
            heave_points.append(hp)

    result: dict[str, Any] = {
        "car_name": car_name,
        "n_setups": len(discoveries),
    }

    if len(torsion_points) >= 2:
        unique = {(p["index"], p["spring_rate_nmm"]) for p in torsion_points}
        unique_pts = [{"index": k[0], "spring_rate_nmm": k[1]} for k in sorted(unique)]
        result["torsion_bar_calibration"] = compute_torsion_c_from_points(unique_pts)
        result["torsion_bar_points"] = unique_pts
    else:
        result["torsion_bar_calibration"] = {
            "status": f"need more points ({len(torsion_points)}/2 minimum)"
        }

    if len(heave_points) >= 2:
        unique = {(p["index"], p["spring_rate_nmm"]) for p in heave_points}
        unique_pts = [{"index": k[0], "spring_rate_nmm": k[1]} for k in sorted(unique)]
        # Linear fit: rate = base + slope * index
        xs = [p["index"] for p in unique_pts]
        ys = [p["spring_rate_nmm"] for p in unique_pts]
        n = len(xs)
        sx, sy = sum(xs), sum(ys)
        sxy = sum(x * y for x, y in zip(xs, ys))
        sxx = sum(x * x for x in xs)
        denom = n * sxx - sx * sx
        if abs(denom) > 1e-12:
            slope = (n * sxy - sx * sy) / denom
            intercept = (sy - slope * sx) / n
        else:
            slope, intercept = 0.0, ys[0] if ys else 0.0
        result["heave_spring_calibration"] = {
            "rate_at_index_0_nmm": intercept,
            "rate_per_index_nmm": slope,
            "n_points": n,
            "points": unique_pts,
        }
    else:
        result["heave_spring_calibration"] = {
            "status": f"need more points ({len(heave_points)}/2 minimum)"
        }

    # Aggregate damper ranges across all setups
    all_damper_ranges: dict[str, dict[str, tuple[int, int]]] = {}
    for d in discoveries:
        for sec, ds in d.dampers.items():
            if sec not in all_damper_ranges:
                all_damper_ranges[sec] = {}
            for attr in ("ls_comp_range", "ls_rbd_range", "hs_comp_range",
                         "hs_rbd_range", "hs_slope_range"):
                rng = getattr(ds, attr)
                if rng != (0, 0):
                    all_damper_ranges[sec][attr] = rng
    result["damper_ranges"] = all_damper_ranges

    # Hidden perch offsets (in mm, converted from meters)
    perch_samples: list[dict[str, float]] = []
    for d in discoveries:
        h = d.hidden
        if h.lr_perch_offset_m is not None:
            perch_samples.append({
                "lr_perch_mm": h.lr_perch_offset_m * 1000.0,
                "rr_perch_mm": (h.rr_perch_offset_m or 0.0) * 1000.0,
            })
    if perch_samples:
        result["rear_perch_offsets_mm"] = perch_samples

    return result


def ingest_setup_json(path: str | Path) -> DiscoveredParameters:
    """Load and discover parameters from a setup JSON file on disk."""
    p = Path(path)
    data = json.loads(p.read_text())
    return discover_car_parameters(data)


def print_discovery_report(params: DiscoveredParameters) -> str:
    """Generate a detailed human-readable discovery report."""
    lines = [
        "=" * 63,
        f"  AUTO-DISCOVERY REPORT: {params.car_name}",
        "=" * 63,
        "",
        params.summary(),
        "",
    ]

    # Hidden physics section
    lines.append("─── HIDDEN PHYSICS (unmapped rows) ──────────────────────")
    hp = params.hidden
    for attr_name in sorted(vars(hp)):
        val = getattr(hp, attr_name)
        if val is not None:
            if isinstance(val, float) and "npm" in attr_name:
                lines.append(f"  {attr_name}: {val:.1f} N/m ({val/1000:.2f} N/mm)")
            elif isinstance(val, float) and attr_name.endswith("_m"):
                lines.append(f"  {attr_name}: {val:.6f} m ({val*1000:.2f} mm)")
            else:
                lines.append(f"  {attr_name}: {val}")

    # Damper ranges
    if params.dampers:
        lines.append("")
        lines.append("─── DAMPER RANGES ───────────────────────────────────────")
        for sec, ds in sorted(params.dampers.items()):
            lines.append(f"  {sec}:")
            lines.append(f"    LS Comp: {ds.ls_comp} (range {ds.ls_comp_range})")
            lines.append(f"    LS Rbd:  {ds.ls_rbd} (range {ds.ls_rbd_range})")
            lines.append(f"    HS Comp: {ds.hs_comp} (range {ds.hs_comp_range})")
            lines.append(f"    HS Rbd:  {ds.hs_rbd} (range {ds.hs_rbd_range})")
            lines.append(f"    HS Slope:{ds.hs_slope} (range {ds.hs_slope_range})")

    # Garage ranges
    if params.garage_ranges:
        lines.append("")
        lines.append("─── GARAGE RANGES ───────────────────────────────────────")
        for key, rng in sorted(params.garage_ranges.items()):
            lines.append(f"  {key}: [{rng[0]}, {rng[1]}]")

    # Calibration points
    cal = params.corner_spring_calibration_point()
    if cal:
        lines.append("")
        lines.append("─── CORNER SPRING CALIBRATION ───────────────────────────")
        lines.append(f"  Torsion bar index {cal['index']} → {cal['spring_rate_nmm']:.1f} N/mm")
        lines.append(f"  (from hidden fSideSpringRateNpm = {params.hidden.front_spring_rate_npm:.1f} N/m)")

    hcal = params.heave_spring_calibration_point()
    if hcal:
        lines.append("")
        lines.append("─── HEAVE SPRING CALIBRATION ────────────────────────────")
        lines.append(f"  Heave index {hcal['index']} → {hcal['spring_rate_nmm']:.1f} N/mm")

    lines.append("")
    lines.append("=" * 63)
    return "\n".join(lines)
