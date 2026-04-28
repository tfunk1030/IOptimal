"""Profile fit_models_from_points() for each calibrated car.

Writes /tmp/<car>_profile.out and prints a summary.
"""
from __future__ import annotations

import cProfile
import io
import pstats
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from car_model.auto_calibrate import (  # noqa: E402
    load_calibration_points,
    fit_models_from_points,
)


def profile_car(car: str) -> dict:
    print(f"\n=== {car.upper()} ===")
    points = load_calibration_points(car)
    print(f"  {len(points)} calibration points loaded")
    if not points:
        return {"car": car, "n_points": 0, "skipped": True}

    profile_path = Path(f"/tmp/{car}_profile.out")
    profile_path.parent.mkdir(parents=True, exist_ok=True)

    prof = cProfile.Profile()
    t0 = time.perf_counter()
    prof.enable()
    try:
        models = fit_models_from_points(car, points)
    except Exception as e:
        prof.disable()
        print(f"  ERROR: {e}")
        return {"car": car, "n_points": len(points), "error": str(e)}
    prof.disable()
    elapsed = time.perf_counter() - t0
    prof.dump_stats(str(profile_path))

    print(f"  wall={elapsed:.2f}s n_unique={models.n_unique_setups}")
    print(f"  saved to {profile_path}")

    # Print top 15 cumulative
    s = io.StringIO()
    ps = pstats.Stats(prof, stream=s).sort_stats("cumulative")
    ps.print_stats(20)
    print(s.getvalue())

    return {
        "car": car,
        "n_points": len(points),
        "n_unique": models.n_unique_setups,
        "wall_s": elapsed,
        "profile_path": str(profile_path),
    }


def main() -> None:
    cars = ["bmw", "porsche", "ferrari", "cadillac", "acura"]
    results = []
    for car in cars:
        results.append(profile_car(car))
    print("\n=== SUMMARY ===")
    for r in results:
        print(r)


if __name__ == "__main__":
    main()
