"""Dump brake-system parameters across all 5 Cadillac/Laguna sessions
plus the cross-session deltas the driver actually ran."""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from track_model.ibt_parser import IBTFile

IBTS = [
    "cadillacvseriesrgtp_lagunaseca 2026-04-27 16-54-38.ibt",
    "cadillacvseriesrgtp_lagunaseca 2026-04-27 17-11-38.ibt",
    "cadillacvseriesrgtp_lagunaseca 2026-04-27 17-36-02.ibt",
    "cadillacvseriesrgtp_lagunaseca 2026-04-27 19-50-46.ibt",
    "cadillacvseriesrgtp_lagunaseca 2026-04-27 19-58-45.ibt",
]

print(f"{'parameter':<32s} | " + " | ".join(f"S{i+1:<8}" for i in range(len(IBTS))))
print("-" * 95)

rows: list[tuple[str, list]] = []
for ibt_name in IBTS:
    ibt = IBTFile(REPO / "ibtfiles" / ibt_name)
    cs = ibt.session_info.get("CarSetup", {})
    bs = cs.get("BrakesDriveUnit", {}).get("BrakeSpec", {})
    rows.append(("BrakePressureBias",       [str(bs.get("BrakePressureBias", "?"))[:8] for _ in (1,)]))

# Re-collect properly across all sessions
sessions = []
for ibt_name in IBTS:
    ibt = IBTFile(REPO / "ibtfiles" / ibt_name)
    cs = ibt.session_info.get("CarSetup", {})
    bs = cs.get("BrakesDriveUnit", {}).get("BrakeSpec", {})
    bdu = cs.get("BrakesDriveUnit", {})
    sessions.append({
        "BrakePressureBias":   bs.get("BrakePressureBias", "?"),
        "FrontMasterCyl":      bs.get("FrontMasterCyl", "?"),
        "RearMasterCyl":       bs.get("RearMasterCyl", "?"),
        "PadCompound":         bs.get("PadCompound", "?"),
        "BrakeBiasMigration":  bs.get("BrakeBiasMigration", "?"),
        "BrakeBiasTarget":     bs.get("BrakeBiasTarget", "?"),
        "DiffPreload":         bdu.get("DiffSpec", {}).get("Preload", "?"),
        "DiffCoastRamp":       bdu.get("DiffSpec", {}).get("DiffCoastRampAngle", "?"),
        "DiffDriveRamp":       bdu.get("DiffSpec", {}).get("DiffDriveRampAngle", "?"),
        "ClutchPlates":        bdu.get("DiffSpec", {}).get("ClutchFrictionPlates", "?"),
        "TcGain":              bdu.get("TcAndThrottle", {}).get("TractionControlGain", "?"),
        "TcSlip":              bdu.get("TcAndThrottle", {}).get("TractionControlSlip", "?"),
        "ThrottleShape":       bdu.get("TcAndThrottle", {}).get("ThrottleShape", "?"),
    })

keys = list(sessions[0].keys())
for k in keys:
    cells = [str(s.get(k, "?"))[:10] for s in sessions]
    print(f"{k:<32s} | " + " | ".join(f"{c:<10s}" for c in cells))
