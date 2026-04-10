"""Universal calibration sweep: predict vs measure for every known setup.

Enhanced version of validate_garage_predictions.py with:
  - R² per prediction target
  - Error correlation with input variables
  - Bias vs variance decomposition
  - Per-point detail dump for worst mismatches
  - Data contamination detection
  - JSON output option

Usage:
    python -m validation.universal_calibration_sweep
    python -m validation.universal_calibration_sweep --car porsche
    python -m validation.universal_calibration_sweep --verbose
    python -m validation.universal_calibration_sweep --json output.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np

from car_model.auto_calibrate import (
    CalibrationPoint,
    apply_to_car,
    load_calibrated_models,
    load_calibration_points,
    _setup_key,
)
from car_model.cars import get_car
from car_model.garage import GarageSetupState

CARS_TO_VALIDATE = ["bmw", "porsche", "ferrari", "acura"]

# (output_attr, truth_field, label, tolerance_mm)
TARGETS = [
    ("front_static_rh_mm", "static_front_rh_mm", "Front Static RH", 0.5),
    ("rear_static_rh_mm", "static_rear_rh_mm", "Rear Static RH", 1.0),
    ("heave_spring_defl_static_mm", "heave_spring_defl_static_mm", "Heave Defl Static", 1.0),
    ("heave_spring_defl_max_mm", "heave_spring_defl_max_mm", "Heave Defl Max", 1.0),
    ("heave_slider_defl_static_mm", "heave_slider_defl_static_mm", "Heave Slider", 1.0),
    ("front_shock_defl_static_mm", "front_shock_defl_static_mm", "Front Shock Defl", 1.0),
    ("rear_shock_defl_static_mm", "rear_shock_defl_static_mm", "Rear Shock Defl", 1.0),
    ("torsion_bar_turns", "torsion_bar_turns", "Torsion Turns", 0.05),
    ("torsion_bar_defl_mm", "torsion_bar_defl_mm", "Torsion Defl", 0.5),
    ("rear_spring_defl_static_mm", "rear_spring_defl_static_mm", "Rear Spring Defl", 1.0),
    ("rear_spring_defl_max_mm", "rear_spring_defl_max_mm", "Rear Spring Defl Max", 1.0),
    ("third_spring_defl_static_mm", "third_spring_defl_static_mm", "Third Defl Static", 1.0),
    ("third_spring_defl_max_mm", "third_spring_defl_max_mm", "Third Defl Max", 1.0),
    ("third_slider_defl_static_mm", "third_slider_defl_static_mm", "Third Slider", 1.0),
]

# Input fields for correlation analysis
INPUT_FIELDS = [
    ("front_heave_nmm", "Heave"),
    ("rear_third_nmm", "Third"),
    ("rear_spring_nmm", "R.Spring"),
    ("front_torsion_od_mm", "Tors.OD"),
    ("front_pushrod_mm", "Push.F"),
    ("rear_pushrod_mm", "Push.R"),
    ("front_heave_perch_mm", "Perch.F"),
    ("rear_third_perch_mm", "Perch.RT"),
    ("rear_spring_perch_mm", "Perch.RS"),
    ("front_camber_deg", "Camber"),
    ("fuel_l", "Fuel"),
]


# ── Index → physical conversion ─────────────────────────────────────────────

def _needs_index_decode(value: float, idx_range: tuple[float, float] | None) -> bool:
    if idx_range is None:
        return False
    return value <= idx_range[1] + 0.5


def _build_garage_state(car_obj, pt: CalibrationPoint) -> GarageSetupState:
    hsm = car_obj.heave_spring
    csm = car_obj.corner_spring

    front_heave_nmm = pt.front_heave_setting
    if _needs_index_decode(front_heave_nmm, hsm.front_setting_index_range):
        front_heave_nmm = hsm.front_rate_from_setting(front_heave_nmm)

    rear_third_nmm = pt.rear_third_setting
    if _needs_index_decode(rear_third_nmm, hsm.rear_setting_index_range):
        rear_third_nmm = hsm.rear_rate_from_setting(rear_third_nmm)

    rear_spring_nmm = pt.rear_spring_setting
    if (hasattr(csm, 'rear_setting_index_range')
            and _needs_index_decode(rear_spring_nmm, csm.rear_setting_index_range)):
        rear_spring_nmm = csm.rear_bar_rate_from_setting(rear_spring_nmm)

    front_torsion_od_mm = pt.front_torsion_od_mm
    if (hasattr(csm, 'front_setting_index_range')
            and _needs_index_decode(front_torsion_od_mm, csm.front_setting_index_range)):
        front_torsion_od_mm = csm.front_torsion_od_from_setting(front_torsion_od_mm)

    return GarageSetupState(
        front_pushrod_mm=pt.front_pushrod_mm,
        rear_pushrod_mm=pt.rear_pushrod_mm,
        front_heave_nmm=front_heave_nmm,
        front_heave_perch_mm=pt.front_heave_perch_mm,
        rear_third_nmm=rear_third_nmm,
        rear_third_perch_mm=pt.rear_third_perch_mm,
        front_torsion_od_mm=front_torsion_od_mm,
        rear_spring_nmm=rear_spring_nmm,
        rear_spring_perch_mm=pt.rear_spring_perch_mm,
        front_camber_deg=pt.front_camber_deg,
        rear_camber_deg=pt.rear_camber_deg,
        fuel_l=pt.fuel_l,
        wing_deg=pt.wing_deg,
        front_arb_blade=float(pt.front_arb_blade or 0),
        rear_arb_blade=float(pt.rear_arb_blade or 0),
    )


# ── Statistics ───────────────────────────────────────────────────────────────

@dataclass
class FieldStats:
    label: str
    n_points: int = 0
    tolerance_mm: float = 1.0
    predicted: list[float] = field(default_factory=list)
    measured: list[float] = field(default_factory=list)
    errors: list[float] = field(default_factory=list)
    session_ids: list[str] = field(default_factory=list)

    @property
    def rmse(self) -> float:
        if not self.errors:
            return 0.0
        return float(np.sqrt(np.mean(np.array(self.errors) ** 2)))

    @property
    def mean_abs_err(self) -> float:
        if not self.errors:
            return 0.0
        return float(np.mean(np.abs(self.errors)))

    @property
    def max_abs_err(self) -> float:
        if not self.errors:
            return 0.0
        return float(np.max(np.abs(self.errors)))

    @property
    def mean_signed_err(self) -> float:
        """Bias: positive = overprediction."""
        if not self.errors:
            return 0.0
        return float(np.mean(self.errors))

    @property
    def r_squared(self) -> float:
        if len(self.predicted) < 3:
            return float("nan")
        p = np.array(self.predicted)
        m = np.array(self.measured)
        ss_res = np.sum((p - m) ** 2)
        ss_tot = np.sum((m - np.mean(m)) ** 2)
        if ss_tot < 1e-12:
            return float("nan")
        return float(1.0 - ss_res / ss_tot)

    @property
    def n_outliers(self) -> int:
        return sum(1 for e in self.errors if abs(e) > self.tolerance_mm)

    @property
    def worst_point_idx(self) -> int:
        if not self.errors:
            return -1
        return int(np.argmax(np.abs(self.errors)))

    def add(self, predicted: float, measured: float, session_id: str):
        err = predicted - measured
        self.predicted.append(predicted)
        self.measured.append(measured)
        self.errors.append(err)
        self.session_ids.append(session_id)
        self.n_points += 1


@dataclass
class PointDetail:
    session_id: str
    field_label: str
    predicted: float
    measured: float
    error: float
    setup: dict = field(default_factory=dict)


def _deduplicate(points: list[CalibrationPoint]) -> list[CalibrationPoint]:
    seen: set = set()
    unique: list[CalibrationPoint] = []
    for pt in points:
        key = _setup_key(pt)
        if key not in seen:
            seen.add(key)
            unique.append(pt)
    return unique


def _detect_contamination(car_name: str, points: list[CalibrationPoint]) -> list[str]:
    """Detect calibration points that look like they belong to a different car."""
    warnings = []
    if car_name == "ferrari":
        for pt in points:
            issues = []
            # Ferrari rear spring range is 364-590 N/mm; BMW is ~120-280
            if pt.rear_spring_setting > 18.5 and pt.rear_spring_setting < 300:
                issues.append(f"rear_spring={pt.rear_spring_setting} (Ferrari range 364-590)")
            # Ferrari pushrod is typically positive (0-10); BMW is negative (-20 to -30)
            if pt.front_pushrod_mm < -10:
                issues.append(f"pushrod_f={pt.front_pushrod_mm} (looks like BMW)")
            if issues:
                warnings.append(
                    f"  CONTAMINATION? {pt.session_id[:24]}: {'; '.join(issues)}"
                )
    return warnings


# ── Main validation ──────────────────────────────────────────────────────────

def validate_car(
    car_name: str,
    verbose: bool = False,
) -> tuple[dict[str, FieldStats], list[PointDetail], dict[str, list[float]]]:
    """Validate all calibration points for one car.

    Returns (field_stats, worst_details, input_values_per_point).
    """
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

    points = load_calibration_points(car_name)
    if not points:
        print(f"  WARNING: No calibration points for {car_name}")
        return {}, [], {}

    # Contamination check
    contam = _detect_contamination(car_name, points)
    if contam:
        print(f"  DATA QUALITY WARNINGS:")
        for w in contam:
            print(w)

    track_name = points[0].track or None
    garage_model = car.active_garage_output_model(track_name)
    if garage_model is None:
        garage_model = car.garage_output_model
    if garage_model is None:
        print(f"  WARNING: No GarageOutputModel for {car_name} — skipping")
        return {}, [], {}

    unique = _deduplicate(points)

    # Initialize stats
    stats: dict[str, FieldStats] = {}
    for _, truth_field, label, tol in TARGETS:
        stats[truth_field] = FieldStats(label=label, tolerance_mm=tol)

    all_details: list[PointDetail] = []
    # Collect input values for correlation analysis
    input_arrays: dict[str, list[float]] = {name: [] for name, _ in INPUT_FIELDS}

    for pt in unique:
        try:
            state = _build_garage_state(car, pt)
        except Exception as e:
            print(f"  ERROR building state for {pt.session_id[:24]}: {e}")
            continue

        try:
            outputs = garage_model.predict(state, front_excursion_p99_mm=0.0)
        except Exception as e:
            print(f"  ERROR predicting for {pt.session_id[:24]}: {e}")
            continue

        # Record input values
        for attr_name, _ in INPUT_FIELDS:
            input_arrays[attr_name].append(getattr(state, attr_name, 0.0))

        setup_summary = {
            "heave": state.front_heave_nmm,
            "third": state.rear_third_nmm,
            "spring": state.rear_spring_nmm,
            "torsion_od": state.front_torsion_od_mm,
            "pushrod_f": state.front_pushrod_mm,
            "pushrod_r": state.rear_pushrod_mm,
            "perch_f": state.front_heave_perch_mm,
            "perch_rt": state.rear_third_perch_mm,
            "perch_rs": state.rear_spring_perch_mm,
            "camber_f": state.front_camber_deg,
            "fuel": state.fuel_l,
        }

        for output_attr, truth_field, label, tol in TARGETS:
            measured = getattr(pt, truth_field, None)
            if measured is None or measured == 0.0:
                continue
            predicted = getattr(outputs, output_attr, None)
            if predicted is None:
                continue

            stats[truth_field].add(predicted, measured, pt.session_id)
            all_details.append(PointDetail(
                session_id=pt.session_id,
                field_label=label,
                predicted=round(predicted, 3),
                measured=round(measured, 3),
                error=round(predicted - measured, 3),
                setup=setup_summary,
            ))

    return stats, all_details, input_arrays


def _compute_correlations(
    stats: dict[str, FieldStats],
    input_arrays: dict[str, list[float]],
) -> dict[str, list[tuple[str, float]]]:
    """For each prediction target, compute correlation of error with each input."""
    correlations: dict[str, list[tuple[str, float]]] = {}

    for _, truth_field, label, _ in TARGETS:
        s = stats.get(truth_field)
        if s is None or s.n_points < 5:
            continue
        errs = np.array(s.errors)
        corrs: list[tuple[str, float]] = []
        for attr_name, short_name in INPUT_FIELDS:
            vals = np.array(input_arrays.get(attr_name, []))
            if len(vals) != len(errs):
                continue
            if np.std(vals) < 1e-9:
                continue
            r = float(np.corrcoef(errs, vals)[0, 1])
            if not math.isnan(r):
                corrs.append((short_name, r))
        # Sort by absolute correlation
        corrs.sort(key=lambda x: abs(x[1]), reverse=True)
        correlations[truth_field] = corrs

    return correlations


# ── Reporting ────────────────────────────────────────────────────────────────

def print_car_report(
    car_name: str,
    stats: dict[str, FieldStats],
    all_details: list[PointDetail],
    correlations: dict[str, list[tuple[str, float]]],
    verbose: bool = False,
) -> None:
    n_unique = max((s.n_points for s in stats.values()), default=0)
    print(f"\n{'=' * 90}")
    print(f"  {car_name.upper()} ({n_unique} unique setups)")
    print(f"{'=' * 90}")

    # Summary table
    hdr = f"{'Field':<22} | {'RMSE':>7} | {'Bias':>7} | {'MaxAbs':>7} | {'R²':>6} | {'Pts':>4} | {'Out':>4} | {'Top Correlations'}"
    print(hdr)
    print("-" * len(hdr))

    for _, truth_field, label, tol in TARGETS:
        s = stats.get(truth_field)
        if s is None or s.n_points == 0:
            continue
        r2_str = f"{s.r_squared:.3f}" if not math.isnan(s.r_squared) else "  n/a"
        flag = " **" if s.max_abs_err > 2.0 * tol else " *" if s.max_abs_err > tol else ""

        # Top 2 correlations
        corr_info = correlations.get(truth_field, [])
        corr_str = ", ".join(
            f"{name}={r:+.2f}" for name, r in corr_info[:2] if abs(r) > 0.3
        ) or "-"

        print(
            f"{label:<22} | {s.rmse:>6.3f}m | {s.mean_signed_err:>+6.3f} | "
            f"{s.max_abs_err:>6.3f}m | {r2_str} | {s.n_points:>4} | "
            f"{s.n_outliers:>4} | {corr_str}{flag}"
        )

    # Per-point worst mismatches
    if verbose:
        worst = sorted(all_details, key=lambda e: abs(e.error), reverse=True)[:15]
        if worst:
            print(f"\n  TOP 15 WORST MISMATCHES:")
            for e in worst:
                print(
                    f"    {e.field_label:<22}  pred={e.predicted:>8.3f}  "
                    f"meas={e.measured:>8.3f}  err={e.error:>+8.3f}mm  "
                    f"[{e.session_id[:24]}]"
                )
                if e.setup:
                    s = e.setup
                    print(
                        f"      heave={s['heave']:.0f} third={s['third']:.0f} "
                        f"spring={s['spring']:.0f} torsion_od={s['torsion_od']:.2f} "
                        f"push_f={s['pushrod_f']:.1f} push_r={s['pushrod_r']:.1f}"
                    )


def build_json_report(
    all_results: dict[str, tuple[dict, list, dict]],
) -> dict:
    """Build a JSON-serializable report from all car results."""
    report = {}
    for car_name, (stats, details, input_arrays) in all_results.items():
        car_report = {"fields": {}, "worst_points": []}
        for _, truth_field, label, tol in TARGETS:
            s = stats.get(truth_field)
            if s is None or s.n_points == 0:
                continue
            car_report["fields"][label] = {
                "rmse": round(s.rmse, 4),
                "mean_abs_err": round(s.mean_abs_err, 4),
                "max_abs_err": round(s.max_abs_err, 4),
                "bias": round(s.mean_signed_err, 4),
                "r_squared": round(s.r_squared, 4) if not math.isnan(s.r_squared) else None,
                "n_points": s.n_points,
                "n_outliers": s.n_outliers,
                "tolerance_mm": tol,
            }
        # Top 10 worst
        worst = sorted(details, key=lambda e: abs(e.error), reverse=True)[:10]
        for d in worst:
            car_report["worst_points"].append({
                "session_id": d.session_id,
                "field": d.field_label,
                "predicted": d.predicted,
                "measured": d.measured,
                "error": d.error,
                "setup": d.setup,
            })
        report[car_name] = car_report
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Universal calibration sweep — predict vs measure for every known setup"
    )
    parser.add_argument("--car", type=str, help="Validate only this car")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show per-point details")
    parser.add_argument("--json", type=str, metavar="PATH", help="Write JSON report to file")
    args = parser.parse_args()

    cars = [args.car] if args.car else CARS_TO_VALIDATE
    all_results: dict[str, tuple[dict, list, dict]] = {}

    for car_name in cars:
        print(f"\nValidating {car_name}...")
        stats, details, input_arrays = validate_car(car_name, verbose=args.verbose)
        if not stats:
            print(f"  (no results)")
            continue
        all_results[car_name] = (stats, details, input_arrays)
        correlations = _compute_correlations(stats, input_arrays)
        print_car_report(car_name, stats, details, correlations, verbose=args.verbose)

    # Cross-car summary
    if len(all_results) > 1:
        print(f"\n{'=' * 90}")
        print(f"  CROSS-CAR SUMMARY — RMSE (mm)")
        print(f"{'=' * 90}")
        header_cars = list(all_results.keys())
        print(f"{'Field':<22} | " + " | ".join(f"{c:>8}" for c in header_cars))
        print(f"{'-' * 22}-+-" + "-+-".join(f"{'-' * 8}" for _ in header_cars))

        for _, truth_field, label, _ in TARGETS:
            vals = []
            for c in header_cars:
                s = all_results[c][0].get(truth_field)
                if s and s.n_points > 0:
                    vals.append(f"{s.rmse:>8.3f}")
                else:
                    vals.append(f"{'---':>8}")
            print(f"{label:<22} | " + " | ".join(vals))

    # Pass/fail summary
    print(f"\n{'=' * 90}")
    all_pass = True
    for car_name in all_results:
        stats = all_results[car_name][0]
        for _, truth_field, label, tol in TARGETS:
            s = stats.get(truth_field)
            if s and s.n_points > 0 and s.max_abs_err > 3.0 * tol:
                print(f"  FAIL: {car_name}/{label} max error {s.max_abs_err:.2f}mm > {3.0 * tol:.1f}mm")
                all_pass = False
    if all_pass:
        print("  ALL CARS WITHIN TOLERANCE")
    print(f"{'=' * 90}")

    # JSON output
    if args.json:
        report = build_json_report(all_results)
        out_path = Path(args.json)
        out_path.write_text(json.dumps(report, indent=2))
        print(f"\nJSON report written to {out_path}")


if __name__ == "__main__":
    main()
