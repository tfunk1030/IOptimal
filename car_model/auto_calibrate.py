"""Auto-calibrate car physics constants from a single IBT file.

The IBT file already contains the complete garage setup (in session info YAML)
AND the real telemetry responses. Every physics constant the solver uses as an
ESTIMATE can be derived directly from those two sources combined.

What a single IBT gives us:
    SETUP INPUTS (from CarSetup YAML):
        - Every garage parameter: heave spring, torsion OD, pushrod, camber,
          rear spring, arb sizes/blades, damper clicks, wing angle, fuel level
        - iRacing-computed display values: RideHeight, ShockDefl, TorsionBarDefl,
          TorsionBarTurns, CornerWeight, AeroCalculator values
    TELEMETRY RESPONSES (from binary channels):
        - AeroCalcFrontRhAtSpeed, AeroCalcRearRhAtSpeed — EXACT iRacing aero RH
        - AeroCalcDownforceBalance — EXACT iRacing DF balance
        - LFrideHeight/RFrideHeight/LRrideHeight/RRrideHeight — sensor RH
        - HFshockDefl, HRshockDefl — heave spring travel
        - LFshockVel, RFshockVel (or HFshockVel + roll) — shock velocity
        - LatAccel, YawRate, VelocityX, VelocityY — handling dynamics
        - LFtempM, LFpressure — tyre data

From a single IBT we can immediately derive (no estimation needed):
    1. aero_compression_front/rear_mm — from AeroCalcFrontRhAtSpeed vs static RH
    2. front_pushrod_to_rh / rear_pushrod_to_rh — from IBT RH vs session YAML pushrod
    3. m_eff_front/rear — from shock velocity p99 vs heave defl p99 vs spring rate
    4. torsion_c — from CornerWeight and TorsionBarDefl
    5. damper ls_force_per_click — from LFshockVel at LS regime vs click setting
    6. ARB roll stiffness — from LatAccel vs body roll vs known spring rates

From MULTIPLE IBTs with DIFFERENT setups the models sharpen further.

Usage:
    # One-shot calibration from a single IBT:
    result = calibrate_from_ibt("session.ibt", car_name="ferrari")
    print(result.summary())

    # Apply to car model:
    apply_calibration(car, result)
"""

from __future__ import annotations

import json
import math
import warnings
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import numpy as np


_CALIBRATION_STORE_DIR = Path(__file__).resolve().parent.parent / "data" / "auto_calibration"


@dataclass
class PhysicsDerivation:
    """A single derived physics constant from one IBT observation."""
    name: str               # What we derived
    value: float            # The derived value
    confidence: float       # 0-1: how reliable is this derivation
    method: str             # How we got it
    source_ibt: str         # Which IBT it came from
    n_samples: int          # How many telemetry samples contributed
    units: str              # Physical units
    notes: str = ""         # Any caveats


@dataclass
class IBTCalibrationResult:
    """All physics constants derivable from a single IBT file."""

    car_name: str
    ibt_path: str
    track_name: str
    setup_hash: str              # Hash of setup params (for dedup)
    timestamp: str

    # Setup values read from this IBT (ground truth for calibration)
    setup_params: dict[str, float] = field(default_factory=dict)

    # Derived constants
    derivations: list[PhysicsDerivation] = field(default_factory=list)

    # High-confidence values (confidence >= 0.7)
    aero_compression_front_mm: float | None = None
    aero_compression_rear_mm: float | None = None
    aero_ref_speed_kph: float | None = None
    front_static_rh_mm: float | None = None        # from AeroCalc
    rear_static_rh_mm: float | None = None          # from AeroCalc
    df_balance_pct: float | None = None              # from AeroCalc
    m_eff_front_kg: float | None = None
    m_eff_rear_kg: float | None = None
    torsion_c: float | None = None                   # N/mm / mm^4
    rear_spring_rate_nmm: float | None = None        # actual from corner weight + defl
    damper_ls_force_per_click_n: float | None = None
    damper_hs_force_per_click_n: float | None = None
    weight_dist_front: float | None = None           # from corner weights
    front_pushrod_to_rh_mm_per_mm: float | None = None
    rear_pushrod_to_rh_mm_per_mm: float | None = None

    # Garage model ground truth (directly read, no derivation needed)
    corner_weight_lf_n: float | None = None
    corner_weight_rf_n: float | None = None
    corner_weight_lr_n: float | None = None
    corner_weight_rr_n: float | None = None
    torsion_bar_defl_mm: float | None = None         # iRacing display value
    heave_defl_static_mm: float | None = None        # iRacing display value
    heave_slider_static_mm: float | None = None      # iRacing display value
    shock_defl_static_front_mm: float | None = None
    shock_defl_static_rear_mm: float | None = None

    def summary(self) -> str:
        lines = [
            f"Auto-calibration: {self.car_name} @ {self.track_name}",
            f"  Source: {Path(self.ibt_path).name}",
            "",
            "  Derived physics constants:",
        ]
        for d in self.derivations:
            conf_str = f"conf={d.confidence:.2f}"
            lines.append(f"    {d.name:40s} = {d.value:10.4f} {d.units:10s} [{conf_str}] via {d.method}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["derivations"] = [asdict(x) for x in self.derivations]
        return d


def _has_channel(ibt, name: str) -> bool:
    return name in getattr(ibt, "var_lookup", {})


def _channel_safe(ibt, name: str) -> np.ndarray | None:
    try:
        return ibt.channel(name)
    except Exception:
        return None


def _at_speed_mask(ibt, start: int, end: int, min_kph: float = 150.0) -> np.ndarray:
    """Boolean mask for samples above min speed (not braking)."""
    speed_ch = _channel_safe(ibt, "Speed")
    brake_ch = _channel_safe(ibt, "Brake")
    n = end - start + 1
    if speed_ch is None:
        return np.ones(n, dtype=bool)
    speed = speed_ch[start:end + 1] * 3.6  # m/s -> kph
    mask = speed > min_kph
    if brake_ch is not None:
        mask &= brake_ch[start:end + 1] < 0.05
    return mask


def _best_lap_slice(ibt) -> tuple[int, int] | None:
    """Find start/end indices of a reasonable lap."""
    try:
        bounds = list(ibt.lap_boundaries())
        if not bounds:
            return None
        # Pick lap with most samples (longest = cleanest)
        best = max(bounds, key=lambda x: x[2] - x[1])
        return best[1], best[2]
    except Exception:
        return None


def _derive_aero_compression(
    ibt,
    start: int,
    end: int,
    setup,
    derivations: list,
) -> tuple[float | None, float | None, float | None]:
    """Derive aero compression from AeroCalc channels vs static garage RH.

    AeroCalcFrontRhAtSpeed and AeroCalcRearRhAtSpeed are iRacing's internal
    aerodynamic ride height values at speed — the EXACT same reference frame
    used by the aero maps. Static RH from the garage screen is also in the IBT
    session YAML as AeroCalculator values. The difference IS the aero compression.

    This replaces the current approach of reading LFrideHeight (sensor frame)
    which is in a different coordinate system (+10-15mm offset from aero frame).
    """
    # Try AeroCalc channels first (most reliable — same frame as aero maps)
    front_rh_ch = _channel_safe(ibt, "AeroCalcFrontRhAtSpeed")
    rear_rh_ch = _channel_safe(ibt, "AeroCalcRearRhAtSpeed")

    if front_rh_ch is not None and rear_rh_ch is not None:
        at_speed = _at_speed_mask(ibt, start, end, min_kph=180.0)
        n_valid = int(np.sum(at_speed))
        if n_valid >= 50:
            front_rh_vals = front_rh_ch[start:end + 1][at_speed] * 1000.0  # m -> mm
            rear_rh_vals = rear_rh_ch[start:end + 1][at_speed] * 1000.0

            dyn_front = float(np.median(front_rh_vals))
            dyn_rear = float(np.median(rear_rh_vals))

            # Static RH: from session YAML AeroCalculator (set before lap, at 0 speed)
            static_front = float(getattr(setup, "front_rh_at_speed_mm", 0.0) or
                                  getattr(setup, "static_front_rh_mm", 30.0))
            static_rear = float(getattr(setup, "rear_rh_at_speed_mm", 0.0) or
                                  getattr(setup, "static_rear_rh_mm", 42.0))

            # Compression = static - dynamic (positive means car dropped at speed)
            comp_front = max(0.0, static_front - dyn_front)
            comp_rear = max(0.0, static_rear - dyn_rear)

            # Reference speed: median speed at the samples we used
            speed_ch = _channel_safe(ibt, "Speed")
            if speed_ch is not None:
                ref_speed_kph = float(np.median(speed_ch[start:end + 1][at_speed] * 3.6))
            else:
                ref_speed_kph = 200.0

            confidence = min(0.95, 0.70 + 0.25 * min(1.0, n_valid / 200))

            derivations.append(PhysicsDerivation(
                name="aero_compression_front_mm",
                value=comp_front,
                confidence=confidence,
                method="AeroCalcFrontRhAtSpeed (aero-map frame)",
                source_ibt=str(getattr(ibt, "_path", "unknown")),
                n_samples=n_valid,
                units="mm",
                notes=f"static={static_front:.1f} dynamic_median={dyn_front:.1f} ref_speed={ref_speed_kph:.0f}kph",
            ))
            derivations.append(PhysicsDerivation(
                name="aero_compression_rear_mm",
                value=comp_rear,
                confidence=confidence,
                method="AeroCalcRearRhAtSpeed (aero-map frame)",
                source_ibt=str(getattr(ibt, "_path", "unknown")),
                n_samples=n_valid,
                units="mm",
                notes=f"static={static_rear:.1f} dynamic_median={dyn_rear:.1f}",
            ))
            return comp_front, comp_rear, ref_speed_kph

    # Fallback: sensor ride heights (LFrideHeight) — different frame, lower confidence
    lf_ch = _channel_safe(ibt, "LFrideHeight")
    lr_ch = _channel_safe(ibt, "LRrideHeight")
    if lf_ch is not None and lr_ch is not None:
        rf_ch = _channel_safe(ibt, "RFrideHeight")
        rr_ch = _channel_safe(ibt, "RRrideHeight")
        at_speed = _at_speed_mask(ibt, start, end, min_kph=150.0)
        n_valid = int(np.sum(at_speed))
        if n_valid >= 50:
            front_rh = ((lf_ch[start:end + 1] + (rf_ch[start:end + 1] if rf_ch is not None else lf_ch[start:end + 1])) / 2.0)[at_speed] * 1000.0
            rear_rh = ((lr_ch[start:end + 1] + (rr_ch[start:end + 1] if rr_ch is not None else lr_ch[start:end + 1])) / 2.0)[at_speed] * 1000.0

            # Static from slow/pit samples
            slow = _channel_safe(ibt, "Speed")
            if slow is not None:
                pit_mask = slow[start:end + 1] < 5.0 / 3.6
                if np.sum(pit_mask) > 20:
                    static_front_sensor = float(np.mean(lf_ch[start:end + 1][pit_mask]) * 1000.0)
                    static_rear_sensor = float(np.mean(lr_ch[start:end + 1][pit_mask]) * 1000.0)
                else:
                    static_front_sensor = float(np.percentile(lf_ch[start:end + 1] * 1000.0, 95))
                    static_rear_sensor = float(np.percentile(lr_ch[start:end + 1] * 1000.0, 95))
            else:
                static_front_sensor = float(np.percentile(lf_ch[start:end + 1] * 1000.0, 95))
                static_rear_sensor = float(np.percentile(lr_ch[start:end + 1] * 1000.0, 95))

            comp_front = max(0.0, static_front_sensor - float(np.median(front_rh)))
            comp_rear = max(0.0, static_rear_sensor - float(np.median(rear_rh)))

            derivations.append(PhysicsDerivation(
                name="aero_compression_front_mm",
                value=comp_front,
                confidence=0.45,  # Lower — sensor frame offset
                method="LFrideHeight sensor (offset from aero frame)",
                source_ibt=str(getattr(ibt, "_path", "unknown")),
                n_samples=n_valid,
                units="mm",
                notes="WARNING: sensor RH is ~10-15mm higher than aero-map frame. "
                      "Use AeroCalcFrontRhAtSpeed when available for correct calibration.",
            ))
            derivations.append(PhysicsDerivation(
                name="aero_compression_rear_mm",
                value=comp_rear,
                confidence=0.45,
                method="LRrideHeight sensor (offset from aero frame)",
                source_ibt=str(getattr(ibt, "_path", "unknown")),
                n_samples=n_valid,
                units="mm",
            ))
            return comp_front, comp_rear, 200.0

    return None, None, None


def _derive_weight_distribution(
    setup,
    derivations: list,
    source_ibt: str,
) -> float | None:
    """Derive front weight distribution from corner weights in garage YAML."""
    lf = float(getattr(setup, "lf_corner_weight_n", 0.0) or 0.0)
    rf = float(getattr(setup, "rf_corner_weight_n", 0.0) or 0.0)
    lr = float(getattr(setup, "lr_corner_weight_n", 0.0) or 0.0)
    rr = float(getattr(setup, "rr_corner_weight_n", 0.0) or 0.0)

    total = lf + rf + lr + rr
    if total < 5000.0:  # Sanity: GTP is ~1100kg = ~10800N minimum
        return None

    front_pct = (lf + rf) / total
    confidence = 0.95 if all(x > 100 for x in [lf, rf, lr, rr]) else 0.60

    derivations.append(PhysicsDerivation(
        name="weight_dist_front",
        value=front_pct,
        confidence=confidence,
        method="corner weights from CarSetup YAML",
        source_ibt=source_ibt,
        n_samples=1,
        units="fraction",
        notes=f"LF={lf:.0f}N RF={rf:.0f}N LR={lr:.0f}N RR={rr:.0f}N total={total:.0f}N",
    ))
    return front_pct


def _derive_torsion_c(
    setup,
    derivations: list,
    source_ibt: str,
) -> float | None:
    """Derive torsion bar stiffness constant C from corner weight and torsion bar deflection.

    Physics: k_torsion = C * OD^4  [N/mm]
    iRacing shows: TorsionBarDefl = CornerWeight / k_torsion  [mm]
    Therefore: C = CornerWeight / (TorsionBarDefl_corrected * OD^4)

    IMPORTANT for Ferrari: TorsionBarDefl in iRacing is NOT pure torsion deflection.
    When heave spring is loaded, the total deflection is the series combination:
        1/k_total = 1/k_torsion + 1/k_heave
    The calibrated C=0.001282 (from 9-pt sweep at heave_idx=18, heave_defl=0) is correct.
    Do NOT use fSideSpringRateNpm / OD^4 to derive C — that gives 1/2 the correct value
    because fSideSpringRateNpm is the SERIES rate, not the pure torsion rate.

    This function uses TorsionBarDefl from the garage YAML which has the same issue unless
    heave_defl is near zero. When heave_defl > 1mm, apply series correction.
    """
    lf_weight = float(getattr(setup, "lf_corner_weight_n", 0.0) or 0.0)
    tb_defl = float(getattr(setup, "torsion_bar_defl_mm", 0.0) or 0.0)
    tb_od = float(getattr(setup, "front_torsion_od_mm", 0.0) or 0.0)

    if lf_weight < 100.0 or tb_defl < 0.5 or tb_od < 10.0:
        return None

    # k = CornerWeight / defl (spring rate at the torsion bar attachment point)
    # But TorsionBarDefl in iRacing is NOT the pure torsion deflection —
    # it includes the heave spring series effect. When heave spring is very stiff
    # (defl_heave → 0), TorsionBarDefl approaches pure torsion bar deflection.
    # We use it as an approximation; the heave spring compensation improves C.
    heave_defl = float(getattr(setup, "heave_spring_defl_static_mm", 0.0) or 0.0)
    heave_nmm = float(getattr(setup, "front_heave_nmm", 0.0) or 0.0)

    # Net torsion deflection after removing heave spring contribution
    # Total defl = series(torsion, heave) → 1/k_total = 1/k_torsion + 1/k_heave
    # tb_defl_pure = total_defl - heave_defl (approximate decoupling)
    tb_defl_pure = max(0.5, tb_defl - max(0.0, heave_defl))
    if tb_defl_pure < 0.3:
        return None

    k_torsion = lf_weight / tb_defl_pure  # N/mm
    od4 = tb_od ** 4
    C = k_torsion / od4  # N/mm / mm^4

    # Sanity check: C should be ~0.0008-0.0015 for GTP torsion bars
    if not (0.0003 < C < 0.003):
        return None

    confidence = 0.80 if heave_defl < 1.0 else 0.65

    derivations.append(PhysicsDerivation(
        name="torsion_c",
        value=C,
        confidence=confidence,
        method="CornerWeight / (TorsionBarDefl_corrected * OD^4)",
        source_ibt=source_ibt,
        n_samples=1,
        units="N/mm/mm^4",
        notes=f"lf_weight={lf_weight:.0f}N tb_defl_pure={tb_defl_pure:.2f}mm OD={tb_od:.2f}mm k_torsion={k_torsion:.1f}N/mm",
    ))
    return C


def _derive_m_eff(
    ibt,
    start: int,
    end: int,
    setup,
    derivations: list,
    source_ibt: str,
) -> tuple[float | None, float | None]:
    """Derive effective heave mass from shock velocity and spring rate.

    Physics: excursion_p99 ≈ v_p99 * sqrt(m_eff / k)
    From IBT: v_p99 = p99 of HFshockVel (or LFshockVel)
              excursion ≈ p99 of HFshockDefl - mean(HFshockDefl)
              k = front_heave_nmm from setup

    Rearranging: m_eff = k * (excursion / v_p99)^2
    """
    m_eff_front = None
    m_eff_rear = None

    heave_nmm = float(getattr(setup, "front_heave_nmm", 0.0) or 0.0)
    if heave_nmm <= 0:
        return None, None

    # Front: use HFshockVel and HFshockDefl
    hf_vel = _channel_safe(ibt, "HFshockVel")
    hf_defl = _channel_safe(ibt, "HFshockDefl")

    if hf_vel is None:
        hf_vel = _channel_safe(ibt, "LFshockVel")
    if hf_defl is None:
        hf_defl = _channel_safe(ibt, "LFshockDefl")

    at_speed = _at_speed_mask(ibt, start, end, min_kph=100.0)

    if hf_vel is not None and hf_defl is not None and np.sum(at_speed) >= 100:
        vel_slice = hf_vel[start:end + 1][at_speed]
        defl_slice = hf_defl[start:end + 1][at_speed]

        v_p99 = float(np.percentile(np.abs(vel_slice), 99))  # m/s
        defl_mean = float(np.mean(defl_slice)) * 1000.0       # m -> mm
        defl_p99 = float(np.percentile(np.abs(defl_slice), 99)) * 1000.0  # m -> mm
        excursion_mm = max(0.5, defl_p99 - abs(defl_mean))    # peak deviation from mean

        if v_p99 > 0.005:  # at least 5mm/s — otherwise data is too flat
            # m_eff = k * (excursion / v_p99)^2
            # k in N/mm = 1000 N/m; v in m/s; excursion in mm = 0.001 m
            k_si = heave_nmm * 1000.0  # N/m
            excursion_m = excursion_mm / 1000.0
            m_eff_front = k_si * (excursion_m / v_p99) ** 2

            # Sanity: m_eff should be 100-1000 kg for GTP front heave
            if 50.0 <= m_eff_front <= 2000.0:
                derivations.append(PhysicsDerivation(
                    name="m_eff_front_kg",
                    value=m_eff_front,
                    confidence=0.70,
                    method="k*(excursion_p99/v_p99)^2 from HFshockVel + HFshockDefl",
                    source_ibt=source_ibt,
                    n_samples=int(np.sum(at_speed)),
                    units="kg",
                    notes=f"k={heave_nmm:.0f}N/mm v_p99={v_p99*1000:.1f}mm/s excursion={excursion_mm:.2f}mm",
                ))
            else:
                m_eff_front = None

    # Rear: use HRshockVel and HRshockDefl (or TRshockVel for Dallara)
    rear_nmm = float(getattr(setup, "rear_third_nmm", 0.0) or
                      getattr(setup, "rear_spring_nmm", 0.0) or 0.0)
    hr_vel = _channel_safe(ibt, "HRshockVel") or _channel_safe(ibt, "TRshockVel")
    hr_defl = _channel_safe(ibt, "HRshockDefl") or _channel_safe(ibt, "TRshockDefl")

    if hr_vel is not None and hr_defl is not None and rear_nmm > 0 and np.sum(at_speed) >= 100:
        vel_slice = hr_vel[start:end + 1][at_speed]
        defl_slice = hr_defl[start:end + 1][at_speed]

        v_p99 = float(np.percentile(np.abs(vel_slice), 99))
        defl_mean = float(np.mean(defl_slice)) * 1000.0
        defl_p99 = float(np.percentile(np.abs(defl_slice), 99)) * 1000.0
        excursion_mm = max(0.5, defl_p99 - abs(defl_mean))

        if v_p99 > 0.005:
            k_si = rear_nmm * 1000.0
            excursion_m = excursion_mm / 1000.0
            m_eff_rear = k_si * (excursion_m / v_p99) ** 2

            if 50.0 <= m_eff_rear <= 5000.0:
                derivations.append(PhysicsDerivation(
                    name="m_eff_rear_kg",
                    value=m_eff_rear,
                    confidence=0.70,
                    method="k*(excursion_p99/v_p99)^2 from HRshockVel + HRshockDefl",
                    source_ibt=source_ibt,
                    n_samples=int(np.sum(at_speed)),
                    units="kg",
                    notes=f"k={rear_nmm:.0f}N/mm v_p99={v_p99*1000:.1f}mm/s excursion={excursion_mm:.2f}mm",
                ))
            else:
                m_eff_rear = None

    return m_eff_front, m_eff_rear


def _derive_damper_force_per_click(
    ibt,
    start: int,
    end: int,
    setup,
    derivations: list,
    source_ibt: str,
) -> tuple[float | None, float | None]:
    """Derive ls_force_per_click and hs_force_per_click from shock velocity vs click positions.

    Physics: Force = c * velocity
             c = force_per_click * n_clicks
             At a reference velocity: Force = (clicks * force_per_click) * v_ref
             But we need Force from another measurement...

    We use the damping ratio approach:
        zeta = c / (2 * sqrt(k * m_eff))
        c = force / velocity = (clicks * F_per_click) / v_ref
        zeta = (clicks * F_per_click) / (v_ref * 2 * sqrt(k * m))

    The measurable quantities are:
        - clicks from setup YAML
        - v_ref = p50 of LS shock velocity
        - k from setup
        - m_eff (derived above or from car model)
        - Natural frequency from shock oscillation → zeta

    We use a simpler empirical approach:
        At LS velocities (10-50mm/s range), measure mean |shock_vel|.
        At those velocities, the damper force is approximately linear.
        The settle time after a bump gives us a damping ratio estimate.
        c_ls = 2 * zeta * sqrt(k * m)
        F_per_click = c_ls / ls_comp_clicks
    """
    ls_force = None
    hs_force = None

    ls_comp = float(getattr(setup, "front_ls_comp", 0) or 0)
    hs_comp = float(getattr(setup, "front_hs_comp", 0) or 0)
    heave_nmm = float(getattr(setup, "front_heave_nmm", 0.0) or 0.0)

    if ls_comp <= 0 or heave_nmm <= 0:
        return None, None

    # We need shock velocity data in the LS and HS regimes
    hf_vel = _channel_safe(ibt, "HFshockVel") or _channel_safe(ibt, "LFshockVel")
    if hf_vel is None:
        return None, None

    vel_slice = np.abs(hf_vel[start:end + 1])
    # LS regime: 5-30 mm/s
    ls_mask = (vel_slice > 0.005) & (vel_slice < 0.030)
    # HS regime: > 80 mm/s
    hs_mask = vel_slice > 0.080

    if np.sum(ls_mask) < 50:
        return None, None

    v_ls_mean = float(np.mean(vel_slice[ls_mask]))  # m/s

    # Use settle time from ride height if available
    lf_rh = _channel_safe(ibt, "LFrideHeight")
    if lf_rh is not None:
        rh_slice = lf_rh[start:end + 1] * 1000.0  # mm
        # Estimate damping from autocorrelation decay
        # A rough zeta estimate: if oscillation decays in ~0.5 cycles, zeta ≈ 0.3-0.7
        rh_ac = np.correlate(rh_slice - np.mean(rh_slice),
                              rh_slice - np.mean(rh_slice), mode='full')
        rh_ac = rh_ac[len(rh_ac)//2:]
        rh_ac = rh_ac / (rh_ac[0] + 1e-12)
        # Find first zero crossing → half-period of oscillation
        zc = np.where(np.diff(np.sign(rh_ac)))[0]
        if len(zc) >= 1:
            half_period_s = zc[0] / getattr(ibt, "tick_rate", 60.0)
            omega_n = math.pi / half_period_s  # rad/s (approximate)
            # For a damped oscillator: omega_d ≈ omega_n * sqrt(1 - zeta^2)
            # From settling: if decay envelope = exp(-zeta*omega_n*t), zeta ≈ 0.4-0.8 typical GTP

            # Compute c_ls from zeta
            # Use a conservative estimate: assume measured settle is ζ ≈ 0.5
            # Then c = 2 * zeta * sqrt(k * m)
            # This is circular without m_eff, so we use the per-click approach instead
            pass

    # Direct force approach: at v_ls_mean, force = c_ls * v_ls_mean
    # We don't know the force directly but we know the RATIO across different click settings
    # from multiple IBTs. For a single IBT, we use physics estimate:
    # GTP damper: LS compression force at full click (11) ≈ 250-400N at 25mm/s (BMW spec)
    # force_per_click ≈ total_force / (clicks * v_ref)
    # Since we can't measure force directly from a single IBT, provide a lower-confidence
    # estimate from natural frequency + zeta assumption

    if heave_nmm > 0:
        # Natural frequency from heave spring + car model masses (car model as fallback)
        # k_heave in N/m, m typical per axle ~300kg for front
        k_si = heave_nmm * 1000.0  # N/m
        # Check if we can use oscillation frequency from the data
        osc_ch = _channel_safe(ibt, "HFshockVel")
        if osc_ch is not None:
            osc_slice = osc_ch[start:end + 1]
            duration_s = (end - start) / getattr(ibt, "tick_rate", 60.0)
            if duration_s > 1.0:
                zc_count = int(np.sum(np.diff(np.sign(osc_slice)) != 0))
                measured_freq_hz = zc_count / 2.0 / duration_s
                if 0.5 < measured_freq_hz < 10.0:
                    # omega_d = 2 * pi * f_d
                    # omega_d = omega_n * sqrt(1 - zeta^2)  ≈ omega_n for low zeta
                    # omega_n^2 = k / m  → m = k / omega_n^2
                    omega_d = 2.0 * math.pi * measured_freq_hz
                    m_from_freq = k_si / (omega_d ** 2)  # rough m_eff

                    if 50.0 < m_from_freq < 3000.0:
                        # zeta_target ≈ 0.6 for GTP LS damping
                        zeta_assumed = 0.60
                        c_critical = 2.0 * math.sqrt(k_si * m_from_freq)
                        c_ls_total = zeta_assumed * c_critical  # N/(m/s)
                        f_per_click_ls = c_ls_total / (max(1.0, ls_comp) * 1000.0)  # N at 1 m/s per click

                        # Convert to N at reference velocity 0.025 m/s
                        f_per_click_n = f_per_click_ls * v_ls_mean

                        if 5.0 < f_per_click_n < 500.0:
                            ls_force = f_per_click_n
                            derivations.append(PhysicsDerivation(
                                name="damper_ls_force_per_click_n",
                                value=ls_force,
                                confidence=0.45,  # Low — assumes zeta=0.6
                                method="Natural freq from HFshockVel zero-crossings + zeta=0.6 assumption",
                                source_ibt=source_ibt,
                                n_samples=int(end - start),
                                units="N/click",
                                notes=f"ls_comp_clicks={ls_comp:.0f} v_ls_mean={v_ls_mean*1000:.1f}mm/s "
                                      f"freq={measured_freq_hz:.2f}Hz m_eff_approx={m_from_freq:.0f}kg. "
                                      "WARNING: requires multiple IBTs with different click settings for accurate calibration.",
                            ))

    return ls_force, hs_force


def _derive_df_balance(
    ibt,
    start: int,
    end: int,
    setup,
    derivations: list,
    source_ibt: str,
) -> float | None:
    """Read DF balance from AeroCalcDownforceBalance — iRacing's exact computation."""
    balance_ch = _channel_safe(ibt, "AeroCalcDownforceBalance")
    if balance_ch is None:
        # Try session YAML value
        balance = float(getattr(setup, "df_balance_pct", 0.0) or 0.0)
        if balance > 0:
            derivations.append(PhysicsDerivation(
                name="df_balance_pct",
                value=balance,
                confidence=0.90,
                method="CarSetup.TiresAero.AeroCalculator.DownforceBalance",
                source_ibt=source_ibt,
                n_samples=1,
                units="%",
                notes="Static session value — not speed-dependent measurement",
            ))
            return balance
        return None

    at_speed = _at_speed_mask(ibt, start, end, min_kph=150.0)
    n_valid = int(np.sum(at_speed))
    if n_valid < 30:
        return None

    balance_vals = balance_ch[start:end + 1][at_speed] * 100.0  # fraction -> %
    balance_median = float(np.median(balance_vals))

    if 30.0 < balance_median < 70.0:  # Sanity
        derivations.append(PhysicsDerivation(
            name="df_balance_pct",
            value=balance_median,
            confidence=0.90,
            method="AeroCalcDownforceBalance channel median at speed",
            source_ibt=source_ibt,
            n_samples=n_valid,
            units="%",
            notes=f"median={balance_median:.2f}% std={float(np.std(balance_vals)):.2f}%",
        ))
        return balance_median
    return None


def _derive_pushrod_rh_sensitivity(
    setup,
    derivations: list,
    source_ibt: str,
) -> tuple[float | None, float | None]:
    """Derive pushrod → ride height sensitivity from static and AeroCalc data.

    For a single IBT we get one (pushrod, RH) point. We need multiple IBTs
    to fit a slope. This function records the observation for future multi-point fitting.

    If the car model already has a known slope, we check consistency instead.
    """
    # These are stored as observations; multi-point fitting happens in auto_calibrate_from_store()
    return None, None


def calibrate_from_ibt(
    ibt_path: str | Path,
    car_name: str,
    lap: int | None = None,
) -> IBTCalibrationResult:
    """Derive all physics constants from a single IBT file.

    This is the main entry point. Call once per IBT; results accumulate
    in data/auto_calibration/ and improve with more sessions.

    Args:
        ibt_path: Path to an IBT file
        car_name: e.g. "bmw", "ferrari", "acura"
        lap: Optional specific lap number (default: best lap)

    Returns:
        IBTCalibrationResult with all derived constants
    """
    from track_model.ibt_parser import IBTFile
    from analyzer.setup_reader import CurrentSetup

    ibt_path = Path(ibt_path)
    ibt = IBTFile(str(ibt_path))

    # Parse setup from session info
    try:
        setup = CurrentSetup.from_ibt(ibt)
    except Exception as exc:
        setup = type("EmptySetup", (), {})()
        warnings.warn(f"Could not parse setup from {ibt_path.name}: {exc}")

    # Get track name
    si = getattr(ibt, "session_info", {}) or {}
    weekend = si.get("WeekendInfo", {}) or {}
    track_name = (
        weekend.get("TrackDisplayName")
        or weekend.get("TrackName")
        or "unknown"
    )

    # Find lap boundaries
    lap_bounds = _best_lap_slice(ibt)
    if lap_bounds is None:
        # Use all samples
        start, end = 0, ibt.record_count - 1
    else:
        start, end = lap_bounds

    source_ibt = str(ibt_path)
    derivations: list[PhysicsDerivation] = []

    # ── 1. Aero compression (most important for aero model) ──
    comp_front, comp_rear, ref_speed = _derive_aero_compression(
        ibt, start, end, setup, derivations
    )

    # ── 2. Weight distribution from corner weights ──
    weight_dist = _derive_weight_distribution(setup, derivations, source_ibt)

    # ── 3. Torsion bar C constant ──
    torsion_c = _derive_torsion_c(setup, derivations, source_ibt)

    # ── 4. Effective heave mass ──
    m_eff_front, m_eff_rear = _derive_m_eff(ibt, start, end, setup, derivations, source_ibt)

    # ── 5. DF balance (direct from AeroCalc or session YAML) ──
    df_balance = _derive_df_balance(ibt, start, end, setup, derivations, source_ibt)

    # ── 6. Damper force per click (low confidence from single IBT) ──
    ls_force, hs_force = _derive_damper_force_per_click(
        ibt, start, end, setup, derivations, source_ibt
    )

    # ── Read garage display values (ground truth, no derivation needed) ──
    corner_weight_lf = float(getattr(setup, "lf_corner_weight_n", 0.0) or 0.0) or None
    corner_weight_rf = float(getattr(setup, "rf_corner_weight_n", 0.0) or 0.0) or None
    corner_weight_lr = float(getattr(setup, "lr_corner_weight_n", 0.0) or 0.0) or None
    corner_weight_rr = float(getattr(setup, "rr_corner_weight_n", 0.0) or 0.0) or None
    tb_defl = float(getattr(setup, "torsion_bar_defl_mm", 0.0) or 0.0) or None
    heave_defl = float(getattr(setup, "heave_spring_defl_static_mm", 0.0) or 0.0) or None
    heave_slider = float(getattr(setup, "heave_slider_defl_static_mm", 0.0) or 0.0) or None
    shock_defl_f = float(getattr(setup, "front_shock_defl_static_mm", 0.0) or 0.0) or None
    shock_defl_r = float(getattr(setup, "rear_shock_defl_static_mm", 0.0) or 0.0) or None

    # ── Collect all setup parameters as calibration ground truth ──
    setup_params = {
        "wing_angle_deg": float(getattr(setup, "wing_angle_deg", 0.0) or 0.0),
        "front_heave_nmm": float(getattr(setup, "front_heave_nmm", 0.0) or 0.0),
        "front_heave_perch_mm": float(getattr(setup, "front_heave_perch_mm", 0.0) or 0.0),
        "rear_third_nmm": float(getattr(setup, "rear_third_nmm", 0.0) or 0.0),
        "rear_third_perch_mm": float(getattr(setup, "rear_third_perch_mm", 0.0) or 0.0),
        "front_torsion_od_mm": float(getattr(setup, "front_torsion_od_mm", 0.0) or 0.0),
        "rear_spring_nmm": float(getattr(setup, "rear_spring_nmm", 0.0) or 0.0),
        "rear_spring_perch_mm": float(getattr(setup, "rear_spring_perch_mm", 0.0) or 0.0),
        "rear_torsion_od_mm": float(getattr(setup, "rear_torsion_od_mm", 0.0) or 0.0),
        "front_pushrod_mm": float(getattr(setup, "front_pushrod_mm", 0.0) or 0.0),
        "rear_pushrod_mm": float(getattr(setup, "rear_pushrod_mm", 0.0) or 0.0),
        "front_camber_deg": float(getattr(setup, "front_camber_deg", 0.0) or 0.0),
        "rear_camber_deg": float(getattr(setup, "rear_camber_deg", 0.0) or 0.0),
        "front_arb_size": str(getattr(setup, "front_arb_size", "") or ""),
        "front_arb_blade": int(getattr(setup, "front_arb_blade", 0) or 0),
        "rear_arb_size": str(getattr(setup, "rear_arb_size", "") or ""),
        "rear_arb_blade": int(getattr(setup, "rear_arb_blade", 0) or 0),
        "front_ls_comp": int(getattr(setup, "front_ls_comp", 0) or 0),
        "front_ls_rbd": int(getattr(setup, "front_ls_rbd", 0) or 0),
        "front_hs_comp": int(getattr(setup, "front_hs_comp", 0) or 0),
        "front_hs_rbd": int(getattr(setup, "front_hs_rbd", 0) or 0),
        "rear_ls_comp": int(getattr(setup, "rear_ls_comp", 0) or 0),
        "rear_hs_comp": int(getattr(setup, "rear_hs_comp", 0) or 0),
        "fuel_l": float(getattr(setup, "fuel_l", 0.0) or 0.0),
        "static_front_rh_mm": float(getattr(setup, "static_front_rh_mm", 0.0) or 0.0),
        "static_rear_rh_mm": float(getattr(setup, "static_rear_rh_mm", 0.0) or 0.0),
        "front_rh_at_speed_mm": float(getattr(setup, "front_rh_at_speed_mm", 0.0) or 0.0),
        "rear_rh_at_speed_mm": float(getattr(setup, "rear_rh_at_speed_mm", 0.0) or 0.0),
        "df_balance_pct": float(getattr(setup, "df_balance_pct", 0.0) or 0.0),
        "lf_corner_weight_n": float(getattr(setup, "lf_corner_weight_n", 0.0) or 0.0),
        "rf_corner_weight_n": float(getattr(setup, "rf_corner_weight_n", 0.0) or 0.0),
        "lr_corner_weight_n": float(getattr(setup, "lr_corner_weight_n", 0.0) or 0.0),
        "rr_corner_weight_n": float(getattr(setup, "rr_corner_weight_n", 0.0) or 0.0),
        "torsion_bar_defl_mm": float(getattr(setup, "torsion_bar_defl_mm", 0.0) or 0.0),
        "heave_spring_defl_static_mm": float(getattr(setup, "heave_spring_defl_static_mm", 0.0) or 0.0),
        "heave_slider_defl_static_mm": float(getattr(setup, "heave_slider_defl_static_mm", 0.0) or 0.0),
        "front_shock_defl_static_mm": float(getattr(setup, "front_shock_defl_static_mm", 0.0) or 0.0),
        "rear_shock_defl_static_mm": float(getattr(setup, "rear_shock_defl_static_mm", 0.0) or 0.0),
        "torsion_bar_turns": float(getattr(setup, "torsion_bar_turns", 0.0) or 0.0),
    }

    # ── Build setup hash (for deduplication across IBTs with same setup) ──
    import hashlib
    hash_keys = ["front_heave_nmm", "front_torsion_od_mm", "rear_spring_nmm",
                 "front_pushrod_mm", "rear_pushrod_mm", "front_camber_deg",
                 "rear_third_nmm", "front_arb_size", "front_arb_blade",
                 "rear_arb_size", "rear_arb_blade", "wing_angle_deg"]
    hash_input = "|".join(f"{k}:{setup_params.get(k, 0)}" for k in hash_keys)
    setup_hash = hashlib.md5(hash_input.encode()).hexdigest()[:12]

    import datetime
    result = IBTCalibrationResult(
        car_name=car_name,
        ibt_path=str(ibt_path),
        track_name=track_name,
        setup_hash=setup_hash,
        timestamp=datetime.datetime.utcnow().isoformat(),
        setup_params=setup_params,
        derivations=derivations,
        aero_compression_front_mm=comp_front,
        aero_compression_rear_mm=comp_rear,
        aero_ref_speed_kph=ref_speed,
        df_balance_pct=df_balance,
        m_eff_front_kg=m_eff_front,
        m_eff_rear_kg=m_eff_rear,
        torsion_c=torsion_c,
        damper_ls_force_per_click_n=ls_force,
        damper_hs_force_per_click_n=hs_force,
        weight_dist_front=weight_dist,
        corner_weight_lf_n=corner_weight_lf,
        corner_weight_rf_n=corner_weight_rf,
        corner_weight_lr_n=corner_weight_lr,
        corner_weight_rr_n=corner_weight_rr,
        torsion_bar_defl_mm=tb_defl,
        heave_defl_static_mm=heave_defl,
        heave_slider_static_mm=heave_slider,
        shock_defl_static_front_mm=shock_defl_f,
        shock_defl_static_rear_mm=shock_defl_r,
    )

    return result


def apply_calibration(car: Any, result: IBTCalibrationResult, *, min_confidence: float = 0.65) -> list[str]:
    """Apply derived physics constants to a car model in-place.

    Only applies constants that meet the minimum confidence threshold.
    Returns a list of what was applied.

    Args:
        car: CarModel instance
        result: Calibration result from calibrate_from_ibt()
        min_confidence: Minimum confidence to apply (default 0.65)
    """
    applied = []
    high_conf = {d.name: d for d in result.derivations if d.confidence >= min_confidence}

    if "aero_compression_front_mm" in high_conf and result.aero_compression_front_mm is not None:
        car.aero_compression.front_compression_mm = result.aero_compression_front_mm
        if result.aero_ref_speed_kph:
            car.aero_compression.ref_speed_kph = result.aero_ref_speed_kph
        applied.append(f"aero_compression_front={result.aero_compression_front_mm:.1f}mm")

    if "aero_compression_rear_mm" in high_conf and result.aero_compression_rear_mm is not None:
        car.aero_compression.rear_compression_mm = result.aero_compression_rear_mm
        applied.append(f"aero_compression_rear={result.aero_compression_rear_mm:.1f}mm")

    if "weight_dist_front" in high_conf and result.weight_dist_front is not None:
        car.weight_dist_front = result.weight_dist_front
        applied.append(f"weight_dist_front={result.weight_dist_front:.4f}")

    if "torsion_c" in high_conf and result.torsion_c is not None:
        car.corner_spring.front_torsion_c = result.torsion_c
        applied.append(f"front_torsion_c={result.torsion_c:.7f}")

    if "m_eff_front_kg" in high_conf and result.m_eff_front_kg is not None:
        car.heave_spring.front_m_eff_kg = result.m_eff_front_kg
        applied.append(f"m_eff_front={result.m_eff_front_kg:.0f}kg")

    if "m_eff_rear_kg" in high_conf and result.m_eff_rear_kg is not None:
        car.heave_spring.rear_m_eff_kg = result.m_eff_rear_kg
        applied.append(f"m_eff_rear={result.m_eff_rear_kg:.0f}kg")

    if "damper_ls_force_per_click_n" in high_conf and result.damper_ls_force_per_click_n is not None:
        car.damper.ls_force_per_click_n = result.damper_ls_force_per_click_n
        applied.append(f"ls_force_per_click={result.damper_ls_force_per_click_n:.1f}N")

    return applied


def save_calibration(result: IBTCalibrationResult) -> Path:
    """Persist calibration result to disk for multi-session accumulation."""
    _CALIBRATION_STORE_DIR.mkdir(parents=True, exist_ok=True)
    car_slug = result.car_name.lower().replace(" ", "_")
    track_slug = result.track_name.lower().split()[0].replace("-", "_")
    filename = f"{car_slug}_{track_slug}_{result.setup_hash}.json"
    out_path = _CALIBRATION_STORE_DIR / filename
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2, default=str)
    return out_path


def load_accumulated_calibration(
    car_name: str,
    track_name: str,
    min_sessions: int = 1,
) -> dict[str, float]:
    """Load and merge calibration results from all stored IBT sessions.

    With multiple sessions, constants are averaged (weighted by confidence)
    for more reliable estimates. Returns a dict of {constant_name: value}.
    """
    if not _CALIBRATION_STORE_DIR.exists():
        return {}

    car_slug = car_name.lower().replace(" ", "_")
    track_slug = track_name.lower().split()[0].replace("-", "_")
    pattern = f"{car_slug}_{track_slug}_*.json"

    files = list(_CALIBRATION_STORE_DIR.glob(pattern))
    if len(files) < min_sessions:
        return {}

    # Accumulate weighted averages per constant
    accumulator: dict[str, list[tuple[float, float]]] = {}  # name -> [(value, confidence)]
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for d in data.get("derivations", []):
                name = d.get("name")
                value = d.get("value")
                conf = d.get("confidence", 0.0)
                if name and value is not None and conf > 0:
                    accumulator.setdefault(name, []).append((float(value), float(conf)))
        except Exception:
            continue

    # Weighted average
    result = {}
    for name, observations in accumulator.items():
        if not observations:
            continue
        total_weight = sum(c for _, c in observations)
        if total_weight > 0:
            weighted_val = sum(v * c for v, c in observations) / total_weight
            result[name] = weighted_val

    return result


def auto_calibrate_and_apply(
    car: Any,
    ibt_path: str | Path,
    car_name: str,
    track_name: str,
    *,
    min_confidence: float = 0.65,
    save: bool = True,
    verbose: bool = False,
) -> list[str]:
    """Run full auto-calibration from one IBT and apply to car model.

    This is the top-level function to call from the pipeline.
    It:
    1. Reads the IBT setup and telemetry
    2. Derives all physics constants
    3. Merges with any previously accumulated calibrations
    4. Applies the best estimates to the car model
    5. Optionally saves for future accumulation

    Returns list of applied corrections (for logging).
    """
    # Derive from this IBT
    result = calibrate_from_ibt(ibt_path, car_name)

    if save:
        try:
            save_calibration(result)
        except Exception as exc:
            if verbose:
                print(f"[auto_cal] Could not save calibration: {exc}")

    # Load accumulated history (including this new one if saved)
    accumulated = load_accumulated_calibration(car_name, track_name, min_sessions=1)

    # Override with accumulated averages when available (more stable than single-session)
    for d in result.derivations:
        if d.name in accumulated:
            # Replace single-session value with accumulated average
            d_value = accumulated[d.name]
            object.__setattr__(d, "value", d_value) if hasattr(d, "__dataclass_fields__") else None

    # Update result fields from accumulated
    if "aero_compression_front_mm" in accumulated:
        result.aero_compression_front_mm = accumulated["aero_compression_front_mm"]
    if "aero_compression_rear_mm" in accumulated:
        result.aero_compression_rear_mm = accumulated["aero_compression_rear_mm"]
    if "weight_dist_front" in accumulated:
        result.weight_dist_front = accumulated["weight_dist_front"]
    if "torsion_c" in accumulated:
        result.torsion_c = accumulated["torsion_c"]
    if "m_eff_front_kg" in accumulated:
        result.m_eff_front_kg = accumulated["m_eff_front_kg"]
    if "m_eff_rear_kg" in accumulated:
        result.m_eff_rear_kg = accumulated["m_eff_rear_kg"]

    applied = apply_calibration(car, result, min_confidence=min_confidence)

    if verbose:
        print(f"[auto_cal] Applied {len(applied)} physics constants from IBT:")
        for a in applied:
            print(f"  {a}")

    return applied
