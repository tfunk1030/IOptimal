"""Dump current setup + key handling telemetry from each Cadillac/Laguna IBT.

For independent engineering analysis: I form an opinion on what the setup
needs based purely on telemetry (no solver), then compare with what the
solver recommends.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np
from track_model.ibt_parser import IBTFile

IBT_NAMES = [
    "cadillacvseriesrgtp_lagunaseca 2026-04-27 16-54-38.ibt",
    "cadillacvseriesrgtp_lagunaseca 2026-04-27 17-11-38.ibt",
    "cadillacvseriesrgtp_lagunaseca 2026-04-27 17-36-02.ibt",
]


def setup_summary(cs: dict) -> dict:
    """Pull just the setup parameters that matter for engineering analysis."""
    out = {}
    out["wing"]              = cs.get("TiresAero", {}).get("AeroSettings", {}).get("RearWingAngle", "?")
    aero = cs.get("TiresAero", {}).get("AeroCalculator", {})
    out["df_balance_pct"]    = aero.get("DownforceBalance", "?")
    out["ld_ratio"]          = aero.get("LD", "?")
    out["front_rh_at_speed"] = aero.get("FrontRhAtSpeed", "?")
    out["rear_rh_at_speed"]  = aero.get("RearRhAtSpeed", "?")

    chassis = cs.get("Chassis", {})
    front = chassis.get("Front", {})
    rear = chassis.get("Rear", {})
    out["heave_spring_F_nmm"] = front.get("HeaveSpring", "?")
    out["heave_perch_F_mm"]   = front.get("HeavePerchOffset", "?")
    out["pushrod_F_mm"]       = front.get("PushrodLengthOffset", "?")
    out["third_spring_R_nmm"] = rear.get("ThirdSpring", "?")
    out["third_perch_R_mm"]   = rear.get("ThirdPerchOffset", "?")
    out["pushrod_R_mm"]       = rear.get("PushrodLengthOffset", "?")

    lf = chassis.get("LeftFront", {})
    lr = chassis.get("LeftRear", {})
    out["torsion_OD_F_mm"]    = lf.get("TorsionBarOD", "?")
    out["torsion_turns_F"]    = lf.get("TorsionBarTurns", "?")
    out["coil_R_nmm"]         = lr.get("SpringRate", "?")
    out["coil_perch_R_mm"]    = lr.get("SpringPerchOffset", "?")
    out["camber_F_deg"]       = lf.get("Camber", "?")
    out["camber_R_deg"]       = lr.get("Camber", "?")
    out["toe_F_mm"]           = front.get("ToeIn", "?")
    out["toe_R_mm"]           = lr.get("ToeIn", "?")

    out["arb_F_size"]         = front.get("ArbSize", "?")
    out["arb_F_blade"]        = front.get("ArbBlades", "?")
    out["arb_R_size"]         = rear.get("ArbSize", "?")
    out["arb_R_blade"]        = rear.get("ArbBlades", "?")

    out["damper_LF_LSc"]      = lf.get("LsCompDamping", "?")
    out["damper_LF_HSc"]      = lf.get("HsCompDamping", "?")
    out["damper_LF_LSr"]      = lf.get("LsRbdDamping", "?")
    out["damper_LF_HSr"]      = lf.get("HsRbdDamping", "?")
    out["damper_LR_LSc"]      = lr.get("LsCompDamping", "?")
    out["damper_LR_HSc"]      = lr.get("HsCompDamping", "?")
    out["damper_LR_LSr"]      = lr.get("LsRbdDamping", "?")
    out["damper_LR_HSr"]      = lr.get("HsRbdDamping", "?")

    bdu = cs.get("BrakesDriveUnit", {})
    out["brake_bias_pct"]     = bdu.get("BrakeSpec", {}).get("BrakePressureBias", "?")
    diff = bdu.get("DiffSpec", {})
    out["diff_preload_nm"]    = diff.get("Preload", "?")
    out["diff_coast_deg"]     = diff.get("DiffCoastRampAngle", "?")
    out["diff_drive_deg"]     = diff.get("DiffDriveRampAngle", "?")
    tc = bdu.get("TcAndThrottle", {})
    out["tc_gain"]            = tc.get("TractionControlGain", "?")
    out["tc_slip"]            = tc.get("TractionControlSlip", "?")
    out["throttle_shape"]     = tc.get("ThrottleShape", "?")
    out["fuel_l"]             = bdu.get("Fuel", {}).get("FuelLevel", "?")

    return out


def telemetry_summary(ibt: IBTFile) -> dict:
    """Compute key handling/safety statistics from the IBT."""
    out: dict = {}
    speed_kph = ibt.channel("Speed") * 3.6  # m/s → kph
    n_above_100 = (speed_kph >= 100).sum()
    if n_above_100 == 0:
        return {"warning": "no samples above 100 kph"}

    mask = speed_kph >= 100  # ignore pit/grid

    # Lap time (best valid lap)
    if ibt.has_channel("LapCurrentLapTime") and ibt.has_channel("Lap"):
        lap_times = ibt.channel("LapLastLapTime")
        valid_lt = lap_times[(lap_times > 60) & (lap_times < 200)]
        if len(valid_lt):
            out["best_lap_s"] = float(np.min(valid_lt))
            out["mean_lap_s"] = float(np.mean(valid_lt))
            out["lap_count"] = int(len(np.unique(valid_lt)))
    out["max_speed_kph"] = float(np.max(speed_kph))
    out["median_speed_kph"] = float(np.median(speed_kph[mask]))
    out["pct_above_200_kph"] = float((speed_kph >= 200).sum() / len(speed_kph) * 100)

    # Ride heights
    if ibt.has_channel("LFrideHeight") and ibt.has_channel("RFrideHeight"):
        front_rh = (ibt.channel("LFrideHeight") + ibt.channel("RFrideHeight")) / 2 * 1000  # m → mm
        out["front_rh_mean_mm"] = float(np.mean(front_rh[mask]))
        out["front_rh_p01_mm"] = float(np.percentile(front_rh[mask], 1))
        out["front_rh_std_mm"] = float(np.std(front_rh[mask]))
        out["front_rh_min_mm"] = float(np.min(front_rh[mask]))
    if ibt.has_channel("LRrideHeight") and ibt.has_channel("RRrideHeight"):
        rear_rh = (ibt.channel("LRrideHeight") + ibt.channel("RRrideHeight")) / 2 * 1000
        out["rear_rh_mean_mm"] = float(np.mean(rear_rh[mask]))
        out["rear_rh_p01_mm"] = float(np.percentile(rear_rh[mask], 1))
        out["rear_rh_std_mm"] = float(np.std(rear_rh[mask]))
        out["rear_rh_min_mm"] = float(np.min(rear_rh[mask]))

    # Shock velocity (bumpiness)
    for ch, label in [("LFshockVel", "lf_sv"), ("RFshockVel", "rf_sv"),
                       ("LRshockVel", "lr_sv"), ("RRshockVel", "rr_sv")]:
        if ibt.has_channel(ch):
            v = np.abs(ibt.channel(ch)[mask]) * 1000  # m/s → mm/s
            out[f"{label}_p95_mmps"] = float(np.percentile(v, 95))
            out[f"{label}_p99_mmps"] = float(np.percentile(v, 99))

    # Lateral / handling
    if ibt.has_channel("LatAccel"):
        lat = ibt.channel("LatAccel") / 9.81
        out["peak_lat_g"] = float(np.max(np.abs(lat)))
        out["lat_g_p95"] = float(np.percentile(np.abs(lat), 95))

    # Body roll
    if ibt.has_channel("Roll"):
        roll = np.degrees(ibt.channel("Roll"))
        out["body_roll_p95_deg"] = float(np.percentile(np.abs(roll[mask]), 95))
        out["body_roll_max_deg"] = float(np.max(np.abs(roll[mask])))

    # Understeer (very rough proxy via steering vs lat g)
    if ibt.has_channel("SteeringWheelAngle") and ibt.has_channel("YawRate"):
        steer = ibt.channel("SteeringWheelAngle")
        yaw_rate = ibt.channel("YawRate")
        speed_ms = ibt.channel("Speed")
        # 16:1 steering ratio (Cadillac spec); 3.0m wheelbase
        ratio = 16.0
        wb = 3.0
        safe_speed = np.maximum(speed_ms, 5.0)
        rwa = steer / ratio
        understeer_rad = rwa - wb * yaw_rate / safe_speed
        understeer_deg = np.degrees(understeer_rad)
        cornering_mask = mask & (np.abs(ibt.channel("LatAccel") / 9.81) > 0.5)
        if cornering_mask.sum() > 100:
            out["understeer_mean_deg"] = float(np.mean(understeer_deg[cornering_mask]))
            out["understeer_p95_deg"] = float(np.percentile(np.abs(understeer_deg[cornering_mask]), 95))

    # Body slip
    if ibt.has_channel("VelocityX") and ibt.has_channel("VelocityY"):
        vx = ibt.channel("VelocityX")
        vy = ibt.channel("VelocityY")
        slip = np.degrees(np.arctan2(vy, np.maximum(vx, 5.0)))
        cornering_mask = mask & (np.abs(ibt.channel("LatAccel") / 9.81) > 0.5)
        if cornering_mask.sum() > 100:
            out["body_slip_p95_deg"] = float(np.percentile(np.abs(slip[cornering_mask]), 95))
            out["body_slip_max_deg"] = float(np.max(np.abs(slip[cornering_mask])))

    # Brake telemetry
    if ibt.has_channel("Brake"):
        brake = ibt.channel("Brake") * 100
        out["brake_max_pct"] = float(np.max(brake))
        out["pct_braking"] = float((brake > 10).sum() / len(brake) * 100)

    return out


def main() -> None:
    setups: list[dict] = []
    telemetries: list[dict] = []
    labels: list[str] = []

    for name in IBT_NAMES:
        path = REPO / "ibtfiles" / name
        if not path.exists():
            print(f"[skip] {name} — missing")
            continue
        labels.append(name)
        ibt = IBTFile(path)
        cs = ibt.session_info.get("CarSetup", {}) if isinstance(ibt.session_info, dict) else {}
        setups.append(setup_summary(cs))
        telemetries.append(telemetry_summary(ibt))

    print("=" * 110)
    print("CADILLAC V-SERIES.R AT WEATHERTECH RACEWAY LAGUNA SECA")
    print("=" * 110)

    # Per-IBT current setup
    print("\nCURRENT SETUPS (driver-loaded)")
    print("-" * 110)
    keys = list(setups[0].keys()) if setups else []
    print(f"{'parameter':<24s} | " + " | ".join(f"sess {i+1:<10}" for i in range(len(setups))))
    print("-" * 110)
    for k in keys:
        cells = [str(s.get(k, "?"))[:14] for s in setups]
        print(f"{k:<24s} | " + " | ".join(f"{c:<14s}" for c in cells))

    # Per-IBT telemetry
    print("\nTELEMETRY MEASUREMENTS")
    print("-" * 110)
    tel_keys = sorted({k for t in telemetries for k in t.keys()})
    print(f"{'metric':<28s} | " + " | ".join(f"sess {i+1:<10}" for i in range(len(telemetries))))
    print("-" * 110)
    for k in tel_keys:
        cells = []
        for t in telemetries:
            v = t.get(k)
            if isinstance(v, float):
                cells.append(f"{v:8.3f}    ")
            else:
                cells.append(f"{str(v)[:12]:<12s}")
        print(f"{k:<28s} | " + " | ".join(f"{c:<14s}" for c in cells))


if __name__ == "__main__":
    main()
