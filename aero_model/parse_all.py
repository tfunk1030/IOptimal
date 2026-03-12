"""Parse all 33 aero map xlsx files and save structured data to JSON/npz.

Usage:
    python -m aero_model.parse_all
"""

import json
from pathlib import Path

import numpy as np

from aero_model.parse_xlsx import parse_aero_xlsx, CAR_NAMES

AEROMAPS_DIR = Path(__file__).parent.parent / "data" / "aeromaps"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "aeromaps_parsed"


def find_all_xlsx() -> list[Path]:
    """Find all aero map xlsx files across all car directories."""
    files = []
    for car_dir in sorted(AEROMAPS_DIR.iterdir()):
        if not car_dir.is_dir():
            continue
        for xlsx in sorted(car_dir.glob("*.xlsx")):
            if not xlsx.name.startswith("~"):  # skip temp files
                files.append(xlsx)
    return files


def parse_all() -> dict:
    """Parse all xlsx files, return dict keyed by car name.

    Returns:
        {
            "bmw": {
                "wing_angles": [12, 13, 14, 15, 16, 17],
                "front_rh": np.ndarray,
                "rear_rh": np.ndarray,
                "balance": {12: np.ndarray, 13: ..., ...},
                "ld": {12: np.ndarray, 13: ..., ...},
            },
            "cadillac": {...},
            ...
        }
    """
    xlsx_files = find_all_xlsx()
    print(f"Found {len(xlsx_files)} aero map files")

    cars = {}

    for filepath in xlsx_files:
        print(f"  Parsing: {filepath.parent.name}/{filepath.name}")
        try:
            data = parse_aero_xlsx(filepath)
        except Exception as e:
            print(f"    ERROR: {e}")
            continue

        car = data["car"]
        wing = data["wing_angle"]

        if car not in cars:
            cars[car] = {
                "wing_angles": [],
                "front_rh": data["front_rh"],
                "rear_rh": data["rear_rh"],
                "balance": {},
                "ld": {},
            }

        cars[car]["wing_angles"].append(wing)
        cars[car]["balance"][wing] = data["balance"]
        cars[car]["ld"][wing] = data["ld"]

    # Sort wing angles
    for car in cars.values():
        car["wing_angles"].sort()

    return cars


def save_parsed(cars: dict) -> None:
    """Save parsed data to npz (arrays) and JSON (metadata)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for car_name, car_data in cars.items():
        # Save numpy arrays as npz
        npz_path = OUTPUT_DIR / f"{car_name}_aero.npz"
        save_dict = {
            "front_rh": car_data["front_rh"],
            "rear_rh": car_data["rear_rh"],
        }
        for wing in car_data["wing_angles"]:
            save_dict[f"balance_{wing}"] = car_data["balance"][wing]
            save_dict[f"ld_{wing}"] = car_data["ld"][wing]

        np.savez_compressed(str(npz_path), **save_dict)

        # Save metadata as JSON
        meta_path = OUTPUT_DIR / f"{car_name}_aero.json"
        meta = {
            "car": car_name,
            "wing_angles": car_data["wing_angles"],
            "front_rh_range": [float(car_data["front_rh"][0]), float(car_data["front_rh"][-1])],
            "rear_rh_range": [float(car_data["rear_rh"][0]), float(car_data["rear_rh"][-1])],
            "grid_shape": list(car_data["balance"][car_data["wing_angles"][0]].shape),
        }
        meta_path.write_text(json.dumps(meta, indent=2))
        print(f"  Saved: {npz_path.name} + {meta_path.name}")


def main():
    cars = parse_all()
    print(f"\nParsed {len(cars)} cars:")
    for name, data in sorted(cars.items()):
        wings = data["wing_angles"]
        shape = data["balance"][wings[0]].shape
        print(f"  {name}: wings={wings}, grid={shape}, "
              f"front_rh=[{data['front_rh'][0]:.0f}-{data['front_rh'][-1]:.0f}], "
              f"rear_rh=[{data['rear_rh'][0]:.0f}-{data['rear_rh'][-1]:.0f}]")

    save_parsed(cars)
    print("\nDone.")


if __name__ == "__main__":
    main()
