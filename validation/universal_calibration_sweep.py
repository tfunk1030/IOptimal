"""Universal calibration sweep: predict vs measure for every known setup.

Runs all calibration points for each calibrated car and compares prediction
outputs against iRacing ground-truth values captured in calibration_points.json.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from car_model.cars import get_car
from car_model.auto_calibrate import load_calibration_points, load_calibrated_models, apply_to_car
from car_model.garage import GarageSetupState


CARS_TO_VALIDATE = ["bmw", "porsche", "ferrari", "acura"]

# (output field, measured field, label, tolerance_abs)
PREDICTION_TARGETS = [
    ("front_static_rh_mm", "static_front_rh_mm", "Front Static RH", 0.5),
    ("rear_static_rh_mm", "static_rear_rh_mm", "Rear Static RH", 1.0),
    ("heave_spring_defl_static_mm", "heave_spring_defl_static_mm", "Heave Defl Static", 1.0),
    ("rear_spring_defl_static_mm", "rear_spring_defl_static_mm", "Rear Spring Defl", 1.0),
    ("third_spring_defl_static_mm", "third_spring_defl_static_mm", "Third Defl Static", 1.0),
    ("front_shock_defl_static_mm", "front_shock_defl_static_mm", "Front Shock Defl", 1.0),
    ("rear_shock_defl_static_mm", "rear_shock_defl_static_mm", "Rear Shock Defl", 1.0),
    ("torsion_bar_turns", "torsion_bar_turns", "Torsion Turns", 0.05),
    ("torsion_bar_defl_mm", "torsion_bar_defl_mm", "Torsion Defl", 0.5),
    ("heave_slider_defl_static_mm", "heave_slider_defl_static_mm", "Heave Slider", 1.0),
]


@dataclass
class ErrorRow:
    car: str
    session_id: str
    field: str
    label: str
    predicted: float
    measured: float
    error_abs: float
    error_signed: float
    error_pct: float | None
    tolerance: float
    within_tolerance: bool


def _r2(measured: list[float], predicted: list[float]) -> float | None:
    if len(measured) < 2:
        return None
    mu = statistics.mean(measured)
    ss_tot = sum((x - mu) ** 2 for x in measured)
    if ss_tot <= 1e-12:
        return None
    ss_res = sum((y - x) ** 2 for x, y in zip(measured, predicted, strict=False))
    return 1.0 - (ss_res / ss_tot)


def _decode_inputs(car, pt):
    """Decode raw calibration point setup fields to physical values used by models."""
    def _needs_index_decode(value: float, idx_range: tuple[float, float] | None) -> bool:
        if idx_range is None:
            return False
        return value <= idx_range[1] + 0.5

    front_heave_raw = float(pt.front_heave_setting)
    rear_third_raw = float(pt.rear_third_setting)
    front_torsion_raw = float(pt.front_torsion_od_mm)
    rear_spring_raw = float(pt.rear_spring_setting)

    front_heave_nmm = (
        car.heave_spring.front_rate_from_setting(front_heave_raw)
        if _needs_index_decode(front_heave_raw, car.heave_spring.front_setting_index_range)
        else front_heave_raw
    )
    rear_third_nmm = (
        car.heave_spring.rear_rate_from_setting(rear_third_raw)
        if _needs_index_decode(rear_third_raw, car.heave_spring.rear_setting_index_range)
        else rear_third_raw
    )

    front_torsion_od_mm = (
        car.corner_spring.front_torsion_od_from_setting(front_torsion_raw)
        if _needs_index_decode(front_torsion_raw, car.corner_spring.front_setting_index_range)
        else front_torsion_raw
    )
    rear_spring_nmm = (
        car.corner_spring.rear_bar_rate_from_setting(rear_spring_raw)
        if _needs_index_decode(rear_spring_raw, car.corner_spring.rear_setting_index_range)
        else rear_spring_raw
    )

    return GarageSetupState(
        front_pushrod_mm=float(pt.front_pushrod_mm),
        rear_pushrod_mm=float(pt.rear_pushrod_mm),
        front_heave_nmm=front_heave_nmm,
        front_heave_perch_mm=float(pt.front_heave_perch_mm),
        rear_third_nmm=rear_third_nmm,
        rear_third_perch_mm=float(pt.rear_third_perch_mm),
        front_torsion_od_mm=front_torsion_od_mm,
        rear_spring_nmm=rear_spring_nmm,
        rear_spring_perch_mm=float(pt.rear_spring_perch_mm),
        front_camber_deg=float(pt.front_camber_deg),
        fuel_l=float(pt.fuel_l),
    )


def run_sweep() -> dict:
    all_rows: list[ErrorRow] = []
    by_car: dict[str, dict] = {}

    for car_name in CARS_TO_VALIDATE:
        points = load_calibration_points(car_name)
        if not points:
            continue

        car = get_car(car_name, apply_calibration=False)
        models = load_calibrated_models(car_name)
        if models:
            apply_to_car(car, models)

        rows: list[ErrorRow] = []
        point_failures: dict[str, list[ErrorRow]] = {}

        for pt in points:
            setup = _decode_inputs(car, pt)
            out_dict: dict
            if car.garage_output_model is not None:
                out_dict = asdict(car.garage_output_model.predict(setup))
            else:
                # Fallback for cars without GarageOutputModel (e.g. partial Acura):
                # use RideHeightModel + DeflectionModel directly.
                front_rh = car.ride_height_model.predict_front_static_rh(
                    setup.front_heave_nmm, setup.front_camber_deg, setup.front_pushrod_mm, setup.front_heave_perch_mm
                )
                rear_rh = car.ride_height_model.predict_rear_static_rh(
                    setup.rear_pushrod_mm,
                    setup.rear_third_nmm,
                    setup.rear_spring_nmm,
                    setup.front_heave_perch_mm,
                    fuel_l=setup.fuel_l,
                    spring_perch_mm=setup.rear_spring_perch_mm,
                )
                torsion_turns = 0.0
                torsion_defl = 0.0
                if car.corner_spring.front_torsion_c > 0 and setup.front_torsion_od_mm > 0:
                    k_t = car.corner_spring.torsion_bar_rate(setup.front_torsion_od_mm)
                    torsion_defl = car.deflection.torsion_bar_defl(
                        setup.front_heave_nmm, setup.front_heave_perch_mm, k_t
                    )

                heave_defl = car.deflection.heave_spring_defl_static(
                    setup.front_heave_nmm, setup.front_heave_perch_mm, setup.front_torsion_od_mm
                )
                out_dict = {
                    "front_static_rh_mm": front_rh,
                    "rear_static_rh_mm": rear_rh,
                    "heave_spring_defl_static_mm": heave_defl,
                    "rear_spring_defl_static_mm": car.deflection.rear_spring_defl_static(
                        setup.rear_spring_nmm,
                        setup.rear_spring_perch_mm,
                        third_rate_nmm=setup.rear_third_nmm,
                        third_perch_mm=setup.rear_third_perch_mm,
                        pushrod_mm=setup.rear_pushrod_mm,
                    ),
                    "third_spring_defl_static_mm": car.deflection.third_spring_defl_static(
                        setup.rear_third_nmm,
                        setup.rear_third_perch_mm,
                        spring_rate_nmm=setup.rear_spring_nmm,
                        spring_perch_mm=setup.rear_spring_perch_mm,
                        pushrod_mm=setup.rear_pushrod_mm,
                    ),
                    "front_shock_defl_static_mm": car.deflection.shock_defl_front(setup.front_pushrod_mm),
                    "rear_shock_defl_static_mm": car.deflection.shock_defl_rear(
                        setup.rear_pushrod_mm,
                        third_rate_nmm=setup.rear_third_nmm,
                        spring_rate_nmm=setup.rear_spring_nmm,
                        third_perch_mm=setup.rear_third_perch_mm,
                        spring_perch_mm=setup.rear_spring_perch_mm,
                    ),
                    "torsion_bar_turns": torsion_turns,
                    "torsion_bar_defl_mm": torsion_defl,
                    "heave_slider_defl_static_mm": car.deflection.heave_slider_defl_static(
                        setup.front_heave_nmm, setup.front_heave_perch_mm, setup.front_torsion_od_mm
                    ),
                }

            for pred_field, measured_field, label, tol in PREDICTION_TARGETS:
                measured = float(getattr(pt, measured_field, 0.0))
                predicted = float(out_dict[pred_field])
                signed = predicted - measured
                err = abs(signed)
                denom = abs(measured)
                pct = (err / denom * 100.0) if denom > 1e-9 else None
                row = ErrorRow(
                    car=car_name,
                    session_id=pt.session_id,
                    field=measured_field,
                    label=label,
                    predicted=predicted,
                    measured=measured,
                    error_abs=err,
                    error_signed=signed,
                    error_pct=pct,
                    tolerance=tol,
                    within_tolerance=(err <= tol),
                )
                rows.append(row)
                if err > tol:
                    point_failures.setdefault(pt.session_id, []).append(row)

        summary = {}
        for pred_field, measured_field, label, tol in PREDICTION_TARGETS:
            rr = [r for r in rows if r.field == measured_field]
            meas = [r.measured for r in rr]
            pred = [r.predicted for r in rr]
            summary[label] = {
                "field": measured_field,
                "n": len(rr),
                "mean_error": statistics.mean(r.error_abs for r in rr),
                "max_error": max(r.error_abs for r in rr),
                "r2": _r2(meas, pred),
                "n_over_1mm": sum(1 for r in rr if r.error_abs > 1.0),
                "n_over_tol": sum(1 for r in rr if r.error_abs > tol),
                "tol": tol,
            }

        worst_points = sorted(
            point_failures.items(),
            key=lambda kv: max(r.error_abs for r in kv[1]),
            reverse=True,
        )[:8]

        by_car[car_name] = {
            "points": len(points),
            "summary": summary,
            "worst_points": [
                {
                    "session_id": sid,
                    "max_abs_error": max(r.error_abs for r in errs),
                    "mismatches": [asdict(e) for e in sorted(errs, key=lambda x: x.error_abs, reverse=True)],
                }
                for sid, errs in worst_points
            ],
        }
        all_rows.extend(rows)

    return {"by_car": by_car, "rows": [asdict(r) for r in all_rows]}


def print_report(report: dict) -> None:
    for car, payload in report["by_car"].items():
        if "error" in payload:
            print(f"=== {car.upper()} ({payload['points']} points) ===")
            print(f"ERROR: {payload['error']}")
            print()
            continue

        print(f"=== {car.upper()} ({payload['points']} points) ===")
        print("Field                  | Mean Error | Max Error | R²     | Points w/ >1mm error")
        print("-----------------------|-----------:|----------:|-------:|--------------------:")
        for label, row in payload["summary"].items():
            r2 = "n/a" if row["r2"] is None else f"{row['r2']:.3f}"
            print(
                f"{label:23} | {row['mean_error']:9.3f} | {row['max_error']:8.3f} | {r2:>6} | "
                f"{row['n_over_1mm']}/{row['n']}"
            )
        print("\nWorst mismatches:")
        if not payload["worst_points"]:
            print("  (none)")
        for wp in payload["worst_points"]:
            print(f"Point {wp['session_id']}: max_abs_error={wp['max_abs_error']:.3f} mm")
            for mm in wp["mismatches"][:4]:
                pct = "n/a" if mm["error_pct"] is None else f"{mm['error_pct']:.1f}%"
                sign = "+" if mm["error_signed"] >= 0 else ""
                print(
                    f"  {mm['label']}: predicted={mm['predicted']:.3f}, measured={mm['measured']:.3f}, "
                    f"error={sign}{mm['error_signed']:.3f} ({pct})"
                )
        print()


def main() -> int:
    report = run_sweep()
    out_path = PROJECT_ROOT / "validation" / "universal_calibration_sweep_report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_report(report)
    print(f"Wrote detailed JSON report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
