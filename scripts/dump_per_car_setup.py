"""One-off audit script: dump CarSetup YAML from one IBT per GTP car for cross-car comparison."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from track_model.ibt_parser import IBTFile

CARS = {
    "BMW M Hybrid V8":       "ibtfiles/bmwbest.ibt",
    "Porsche 963":           "ibtfiles/porsche963gtp_algarve gp 2026-04-04 13-18-56.ibt",
    "Cadillac V-Series.R":   "ibtfiles/cadillacvseriesrgtp_lagunaseca 2026-04-27 16-54-38.ibt",
    "Ferrari 499P":          "ibtfiles/ferrari499p_algarve gp 2026-04-09 17-10-15.ibt",
    "Acura ARX-06":          "ibtfiles/acuraarx06gtp_hockenheim gp 2026-03-29 20-51-12.ibt",
}


def flatten(d: dict, prefix: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.extend(flatten(v, path))
        else:
            out.append((path, str(v)))
    return out


def car_info(ibt: IBTFile) -> dict:
    di = ibt.session_info.get("DriverInfo", {}) if isinstance(ibt.session_info, dict) else {}
    drivers = di.get("Drivers") or []
    me = next((d for d in drivers if d.get("CarIdx") == di.get("DriverCarIdx")), drivers[0] if drivers else {})
    return {
        "CarPath": me.get("CarPath"),
        "CarScreenName": me.get("CarScreenName"),
        "CarScreenNameShort": me.get("CarScreenNameShort"),
        "CarClassShortName": me.get("CarClassShortName"),
    }


def main() -> None:
    rows_by_car: dict[str, dict[str, str]] = {}
    info_by_car: dict[str, dict] = {}
    for label, rel in CARS.items():
        path = REPO / rel
        if not path.exists():
            print(f"[skip] {label} — IBT missing: {rel}")
            continue
        ibt = IBTFile(path)
        info_by_car[label] = car_info(ibt)
        si = ibt.session_info if isinstance(ibt.session_info, dict) else {}
        cs = si.get("CarSetup", {})
        rows_by_car[label] = dict(flatten(cs)) if isinstance(cs, dict) else {}

    print("=" * 110)
    print("PER-CAR IDENTITY")
    print("=" * 110)
    for label, info in info_by_car.items():
        print(f"\n{label}")
        for k, v in info.items():
            print(f"  {k:24s} = {v}")

    all_keys = sorted({k for d in rows_by_car.values() for k in d.keys()})
    cars = list(rows_by_car.keys())

    print("\n" + "=" * 110)
    print(f"PER-CAR CarSetup KEYS — {len(all_keys)} unique paths across {len(cars)} cars")
    print("=" * 110)
    print(f"\n{'Path':<70s} | " + " | ".join(f"{c[:12]:<12s}" for c in cars))
    print("-" * 110)
    for k in all_keys:
        cells = []
        for c in cars:
            v = rows_by_car[c].get(k)
            if v is None:
                cells.append(f"{'-':<12s}")
            else:
                cells.append(f"{v[:12]:<12s}")
        print(f"{k:<70s} | " + " | ".join(cells))

    print("\n" + "=" * 110)
    print("ARCHITECTURAL FEATURES — does each car have it?")
    print("=" * 110)

    def has_any(car: str, *substrs: str) -> bool:
        return any(any(s.lower() in k.lower() for s in substrs) for k in rows_by_car[car])

    feats = [
        ("HeaveSpring (front)",      ("Front.HeaveSpring", "FrontHeaveSpring")),
        ("ThirdSpring / RrtdSpring", ("ThirdSpring", "Rear.ThirdSpring", "RrtdSpring", "RearThirdSpring")),
        ("TorsionBar (front)",       ("TorsionBarOD", "FrontTorsion", "TorsionBar")),
        ("RollSpring",               ("RollSpring",)),
        ("PerCornerSpring (LF/RF)",  ("LeftFront.SpringRate", "RightFront.SpringRate", "LFSpringRate", "RFSpringRate")),
        ("PerCornerDamper",          ("LeftFront.LsCompDamping", "LeftFront.HsCompDamping", "RightFront.LsCompDamping", "LFLsCompDamping")),
        ("HeaveDamper",              ("HeaveDamper", "Front.HeaveDamper", "Rear.HeaveDamper")),
        ("RollDamper",               ("RollDamper", "Front.RollDamper", "Rear.RollDamper")),
        ("ThirdDamper",              ("ThirdDamper", "Rear.ThirdDamper")),
        ("BumpRubberGap",            ("BumpRubberGap",)),
        ("CenterSplitter",           ("CenterSplitter", "Splitter")),
        ("ARB blade",                ("ArbBlades", "FarbBlades", "ArbBlade")),
        ("ARB integer",              ("ArbSetting", "RarbSetting")),
        ("Diff Preload",             ("DiffPreload", "DiffPreloadTorque", "Differential")),
        ("Diff CoastRamp",           ("CoastRamp",)),
        ("Diff DriveRamp",           ("DriveRamp",)),
        ("HybridDeploy / ERS",       ("HybridDeploy", "Hybrid", "ErsDeployment", "MGUKDeploy")),
        ("BrakeBias",                ("BrakeBias", "BrakePressureBias")),
    ]
    print(f"\n{'Feature':<32s} | " + " | ".join(f"{c[:12]:<12s}" for c in cars))
    print("-" * 110)
    for name, substrs in feats:
        cells = []
        for c in cars:
            cells.append(f"{'YES' if has_any(c, *substrs) else '-':<12s}")
        print(f"{name:<32s} | " + " | ".join(cells))


if __name__ == "__main__":
    main()
