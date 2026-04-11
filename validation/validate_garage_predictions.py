"""Universal calibration sweep: predict vs measure for every known setup.

For each car with calibration data, constructs a GarageSetupState from each
unique calibration point, runs GarageOutputModel.predict(), and compares
predicted values against iRacing ground-truth values stored in the
calibration point.

Usage:
    python -m validation.validate_garage_predictions
    python -m validation.validate_garage_predictions --car porsche
    python -m validation.validate_garage_predictions --verbose
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

# Ensure project root is on sys.path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from car_model.auto_calibrate import (
    CalibrationPoint,
    apply_to_car,
    load_calibrated_models,
    load_calibration_points,
    fit_models_from_points,
    build_garage_output_model,
)
from car_model.cars import get_car
from car_model.garage import GarageOutputModel, GarageSetupState

CARS_TO_VALIDATE = ["bmw", "porsche", "ferrari", "acura"]

# ── Prediction targets ──────────────────────────────────────────────────────
# (output_attr, truth_field, label)
TARGETS = [
    ("front_static_rh_mm", "static_front_rh_mm", "Front Static RH"),
    ("rear_static_rh_mm", "static_rear_rh_mm", "Rear Static RH"),
    ("heave_spring_defl_static_mm", "heave_spring_defl_static_mm", "Heave Defl Static"),
    ("heave_spring_defl_max_mm", "heave_spring_defl_max_mm", "Heave Defl Max"),
    ("heave_slider_defl_static_mm", "heave_slider_defl_static_mm", "Heave Slider"),
    ("front_shock_defl_static_mm", "front_shock_defl_static_mm", "Front Shock Defl"),
    ("rear_shock_defl_static_mm", "rear_shock_defl_static_mm", "Rear Shock Defl"),
    ("torsion_bar_turns", "torsion_bar_turns", "Torsion Turns"),
    ("torsion_bar_defl_mm", "torsion_bar_defl_mm", "Torsion Defl"),
    ("rear_spring_defl_static_mm", "rear_spring_defl_static_mm", "Rear Spring Defl"),
    ("rear_spring_defl_max_mm", "rear_spring_defl_max_mm", "Rear Spring Defl Max"),
    ("third_spring_defl_static_mm", "third_spring_defl_static_mm", "Third Defl Static"),
    ("third_spring_defl_max_mm", "third_spring_defl_max_mm", "Third Defl Max"),
    ("third_slider_defl_static_mm", "third_slider_defl_static_mm", "Third Slider"),
]


# ── Index → physical conversion ─────────────────────────────────────────────

def _needs_index_decode(value: float, idx_range: tuple[float, float] | None) -> bool:
    """Return True if value looks like a raw index (within the index range)."""
    if idx_range is None:
        return False
    return value <= idx_range[1] + 0.5


def _decode_settings(car_obj, pt: CalibrationPoint) -> dict:
    """Convert a calibration point's raw settings to physical N/mm and mm values.

    For indexed cars (Ferrari), this converts indices to physical rates.
    For physical-rate cars (BMW, Porsche, Acura), returns the values as-is.
    Handles mixed data (some points with indices, some with physical rates)
    by checking if the value is within the index range.

    Returns a dict with keys: front_heave_nmm, rear_third_nmm, rear_spring_nmm,
    front_torsion_od_mm.
    """
    hsm = car_obj.heave_spring
    csm = car_obj.corner_spring

    # Front heave: index → N/mm (only if value is within index range)
    front_heave_nmm = pt.front_heave_setting
    if _needs_index_decode(front_heave_nmm, hsm.front_setting_index_range):
        front_heave_nmm = hsm.front_rate_from_setting(front_heave_nmm)

    # Rear third/heave: index → N/mm
    rear_third_nmm = pt.rear_third_setting
    if _needs_index_decode(rear_third_nmm, hsm.rear_setting_index_range):
        rear_third_nmm = hsm.rear_rate_from_setting(rear_third_nmm)

    # Rear spring / rear torsion bar
    rear_spring_nmm = pt.rear_spring_setting
    if (hasattr(csm, 'rear_setting_index_range')
            and _needs_index_decode(rear_spring_nmm, csm.rear_setting_index_range)):
        rear_spring_nmm = csm.rear_bar_rate_from_setting(rear_spring_nmm)

    # Front torsion OD: index → mm
    front_torsion_od_mm = pt.front_torsion_od_mm
    if (hasattr(csm, 'front_setting_index_range')
            and _needs_index_decode(front_torsion_od_mm, csm.front_setting_index_range)):
        front_torsion_od_mm = csm.front_torsion_od_from_setting(front_torsion_od_mm)

    return {
        "front_heave_nmm": front_heave_nmm,
        "rear_third_nmm": rear_third_nmm,
        "rear_spring_nmm": rear_spring_nmm,
        "front_torsion_od_mm": front_torsion_od_mm,
    }


def _build_garage_state(car_obj, pt: CalibrationPoint) -> GarageSetupState:
    """Build a GarageSetupState from a calibration point, with index decoding."""
    decoded = _decode_settings(car_obj, pt)
    return GarageSetupState(
        front_pushrod_mm=pt.front_pushrod_mm,
        rear_pushrod_mm=pt.rear_pushrod_mm,
        front_heave_nmm=decoded["front_heave_nmm"],
        front_heave_perch_mm=pt.front_heave_perch_mm,
        rear_third_nmm=decoded["rear_third_nmm"],
        rear_third_perch_mm=pt.rear_third_perch_mm,
        front_torsion_od_mm=decoded["front_torsion_od_mm"],
        rear_spring_nmm=decoded["rear_spring_nmm"],
        rear_spring_perch_mm=pt.rear_spring_perch_mm,
        front_camber_deg=pt.front_camber_deg,
        rear_camber_deg=pt.rear_camber_deg,
        fuel_l=pt.fuel_l,
    )


# ── Error statistics ─────────────────────────────────────────────────────────

@dataclass
class FieldStats:
    label: str
    n_points: int = 0
    sum_sq_err: float = 0.0
    sum_abs_err: float = 0.0
    max_abs_err: float = 0.0
    max_err_point: str = ""
    n_outliers: int = 0  # points exceeding outlier threshold
    outlier_threshold: float = 1.0

    @property
    def rmse(self) -> float:
        return math.sqrt(self.sum_sq_err / max(self.n_points, 1))

    @property
    def mean_abs_err(self) -> float:
        return self.sum_abs_err / max(self.n_points, 1)


@dataclass
class PointError:
    session_id: str
    field_label: str
    predicted: float
    measured: float
    error: float


def _deduplicate(points: list[CalibrationPoint]) -> list[CalibrationPoint]:
    """Deduplicate by setup fingerprint (mirrors auto_calibrate)."""
    from car_model.auto_calibrate import _setup_key
    seen: set = set()
    unique: list[CalibrationPoint] = []
    for pt in points:
        key = _setup_key(pt)
        if key not in seen:
            seen.add(key)
            unique.append(pt)
    return unique


# ── Main validation loop ─────────────────────────────────────────────────────

def validate_car(car_name: str, verbose: bool = False) -> tuple[dict[str, FieldStats], list[PointError]]:
    """Validate all calibration points for one car.

    Returns (field_stats_dict, worst_errors_list).
    """
    # Load car and apply calibration
    car = get_car(car_name)
    models = load_calibrated_models(car_name)
    if models is not None:
        try:
            applied = apply_to_car(car, models)
            if verbose:
                for note in applied:
                    print(f"  [apply] {note}")
        except Exception as e:
            print(f"  WARNING: apply_to_car failed: {e}")
            applied = []

    # Load calibration points first (needed for track name)
    points = load_calibration_points(car_name)
    if not points:
        print(f"  WARNING: No calibration points for {car_name} — skipping")
        return {}, []

    # Get or build GarageOutputModel
    # Try with track name from calibration points, then fallback to None
    track_name = points[0].track or None
    garage_model = car.active_garage_output_model(track_name)
    if garage_model is None:
        # Try without track filter
        garage_model = car.garage_output_model
    if garage_model is None:
        print(f"  WARNING: No GarageOutputModel for {car_name} — skipping")
        return {}, []

    unique = _deduplicate(points)

    # Initialize stats
    stats: dict[str, FieldStats] = {}
    for _, truth_field, label in TARGETS:
        stats[truth_field] = FieldStats(label=label)

    all_errors: list[PointError] = []

    # Run predictions
    for pt in unique:
        try:
            state = _build_garage_state(car, pt)
        except Exception as e:
            print(f"  ERROR building state for {pt.session_id}: {e}")
            continue

        try:
            outputs = garage_model.predict(state, front_excursion_p99_mm=0.0)
        except Exception as e:
            print(f"  ERROR predicting for {pt.session_id}: {e}")
            continue

        for output_attr, truth_field, label in TARGETS:
            measured = getattr(pt, truth_field, None)
            if measured is None or measured == 0.0:
                # Skip fields that weren't measured / aren't applicable
                continue

            predicted = getattr(outputs, output_attr, None)
            if predicted is None:
                continue

            error = predicted - measured
            abs_err = abs(error)

            s = stats[truth_field]
            s.n_points += 1
            s.sum_sq_err += error ** 2
            s.sum_abs_err += abs_err
            if abs_err > s.max_abs_err:
                s.max_abs_err = abs_err
                s.max_err_point = pt.session_id

            all_errors.append(PointError(
                session_id=pt.session_id,
                field_label=label,
                predicted=round(predicted, 3),
                measured=round(measured, 3),
                error=round(error, 3),
            ))

    return stats, all_errors


def print_car_report(car_name: str, stats: dict[str, FieldStats],
                     all_errors: list[PointError], verbose: bool = False) -> None:
    """Print a formatted report for one car."""
    n_unique = max((s.n_points for s in stats.values()), default=0)
    print(f"\n{'=' * 72}")
    print(f"  {car_name.upper()} ({n_unique} unique setups)")
    print(f"{'=' * 72}")

    # Summary table
    print(f"{'Field':<22} | {'RMSE':>8} | {'MeanAbs':>8} | {'MaxAbs':>8} | {'Points':>6} | {'Worst Point'}")
    print(f"{'-' * 22}-+-{'-' * 8}-+-{'-' * 8}-+-{'-' * 8}-+-{'-' * 6}-+-{'-' * 20}")

    for _, truth_field, label in TARGETS:
        s = stats.get(truth_field)
        if s is None or s.n_points == 0:
            continue
        flag = " **" if s.max_abs_err > 2.0 else " *" if s.max_abs_err > 1.0 else ""
        short_id = s.max_err_point[:16] if s.max_err_point else ""
        print(
            f"{label:<22} | {s.rmse:>7.3f}m | {s.mean_abs_err:>7.3f}m | "
            f"{s.max_abs_err:>7.3f}m | {s.n_points:>6} | {short_id}{flag}"
        )

    # Per-point detail for worst mismatches
    if verbose:
        worst = sorted(all_errors, key=lambda e: abs(e.error), reverse=True)[:15]
        if worst:
            print(f"\n  TOP 15 WORST MISMATCHES:")
            for e in worst:
                print(
                    f"    {e.field_label:<22}  pred={e.predicted:>8.3f}  "
                    f"meas={e.measured:>8.3f}  err={e.error:>+8.3f}mm  "
                    f"[{e.session_id[:20]}]"
                )


def main():
    parser = argparse.ArgumentParser(description="Validate garage predictions for all cars")
    parser.add_argument("--car", type=str, help="Validate only this car")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show per-point details")
    args = parser.parse_args()

    cars = [args.car] if args.car else CARS_TO_VALIDATE

    summary: dict[str, dict[str, FieldStats]] = {}

    for car_name in cars:
        print(f"\nValidating {car_name}...")
        stats, errors = validate_car(car_name, verbose=args.verbose)
        if stats:
            summary[car_name] = stats
            print_car_report(car_name, stats, errors, verbose=args.verbose)
        else:
            print(f"  (no results)")

    # Cross-car summary matrix
    if len(summary) > 1:
        print(f"\n{'=' * 72}")
        print(f"  CROSS-CAR SUMMARY — RMSE (mm)")
        print(f"{'=' * 72}")
        header_cars = list(summary.keys())
        print(f"{'Field':<22} | " + " | ".join(f"{c:>8}" for c in header_cars))
        print(f"{'-' * 22}-+-" + "-+-".join(f"{'-' * 8}" for _ in header_cars))

        for _, truth_field, label in TARGETS:
            vals = []
            for c in header_cars:
                s = summary[c].get(truth_field)
                if s and s.n_points > 0:
                    vals.append(f"{s.rmse:>8.3f}")
                else:
                    vals.append(f"{'---':>8}")
            print(f"{label:<22} | " + " | ".join(vals))

    # Overall pass/fail
    print(f"\n{'=' * 72}")
    all_pass = True
    for car_name, car_stats in summary.items():
        for _, truth_field, label in TARGETS:
            s = car_stats.get(truth_field)
            if s and s.n_points > 0 and s.max_abs_err > 3.0:
                print(f"  FAIL: {car_name}/{label} max error {s.max_abs_err:.2f}mm > 3.0mm")
                all_pass = False

    if all_pass:
        print("  ALL CARS WITHIN LOOSE TOLERANCE (max_abs < 3.0mm)")
    print(f"{'=' * 72}")


if __name__ == "__main__":
    main()
