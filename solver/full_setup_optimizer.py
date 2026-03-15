"""BMW/Sebring constrained setup optimizer.

This optimizer is intentionally scoped to BMW at Sebring, where the repo has
fixture-backed garage truth. Other cars/tracks stay on the legacy sequential
solver path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize

from car_model.garage import GarageSetupState
from solver.arb_solver import ARBSolver
from solver.corner_spring_solver import CornerSpringSolver
from solver.damper_solver import DamperSolver
from solver.heave_solver import HeaveSolver
from solver.rake_solver import RakeSolution, RakeSolver, reconcile_ride_heights
from solver.wheel_geometry_solver import WheelGeometrySolver


_CALIBRATION_PATH = Path(__file__).resolve().parent.parent / "data" / "calibration_dataset.json"


@dataclass(frozen=True)
class BMWSebringSeed:
    front_heave_nmm: float
    rear_third_nmm: float
    front_torsion_od_mm: float
    rear_spring_nmm: float
    rear_third_perch_mm: float
    rear_spring_perch_mm: float
    front_pushrod_mm: float
    rear_pushrod_mm: float
    front_heave_perch_mm: float
    front_camber_deg: float


@dataclass
class OptimizerResult:
    step1: Any
    step2: Any
    step3: Any
    step4: Any
    step5: Any
    step6: Any
    score: float
    garage_outputs: Any


@lru_cache(maxsize=1)
def _load_bmw_sebring_seeds() -> list[BMWSebringSeed]:
    data = json.loads(_CALIBRATION_PATH.read_text())
    unique: dict[tuple[float, ...], BMWSebringSeed] = {}
    for row in data:
        key = (
            float(row["heave_nmm"]),
            float(row["rear_third_nmm"]),
            float(row["torsion_od_mm"]),
            float(row["rear_spring_nmm"]),
            float(row["rear_third_perch_mm"]),
            float(row["rear_spring_perch_mm"]),
        )
        unique[key] = BMWSebringSeed(
            front_heave_nmm=float(row["heave_nmm"]),
            rear_third_nmm=float(row["rear_third_nmm"]),
            front_torsion_od_mm=float(row["torsion_od_mm"]),
            rear_spring_nmm=float(row["rear_spring_nmm"]),
            rear_third_perch_mm=float(row["rear_third_perch_mm"]),
            rear_spring_perch_mm=float(row["rear_spring_perch_mm"]),
            front_pushrod_mm=float(row["front_pushrod_mm"]),
            rear_pushrod_mm=float(row["rear_pushrod_mm"]),
            front_heave_perch_mm=float(row["heave_perch_mm"]),
            front_camber_deg=float(row["front_camber_deg"]),
        )
    return list(unique.values())


def _is_bmw_sebring(car: Any, track: Any) -> bool:
    return (
        getattr(car, "canonical_name", "").lower() == "bmw"
        and "sebring" in getattr(track, "track_name", "").lower()
    )


class BMWSebringOptimizer:
    """Constrained candidate search for BMW/Sebring."""

    def __init__(self, car: Any, surface: Any, track: Any):
        self.car = car
        self.surface = surface
        self.track = track
        self.garage_model = car.active_garage_output_model(track.track_name)
        if self.garage_model is None:
            raise ValueError("BMW/Sebring optimizer requires an active garage-output model")
        self.rake_solver = RakeSolver(car, surface, track)
        self.heave_solver = HeaveSolver(car, track)
        self.corner_solver = CornerSpringSolver(car, track)
        self.arb_solver = ARBSolver(car, track)
        self.geom_solver = WheelGeometrySolver(car, track)
        self.damper_solver = DamperSolver(car, track)

    def optimize(
        self,
        *,
        target_balance: float,
        balance_tolerance: float,
        fuel_load_l: float,
        pin_front_min: bool,
        damping_ratio_scale: float = 1.0,
        lltd_offset: float = 0.0,
        measured: Any = None,
        camber_confidence: str = "estimated",
    ) -> OptimizerResult:
        base_step1 = self.rake_solver.solve(
            target_balance=target_balance,
            balance_tolerance=balance_tolerance,
            fuel_load_l=fuel_load_l,
            pin_front_min=pin_front_min,
        )

        target_front_static = max(
            self.car.min_front_rh_static,
            base_step1.dynamic_front_rh_mm + base_step1.aero_compression_front_mm,
        )
        target_rear_static = max(
            self.car.min_rear_rh_static,
            base_step1.dynamic_rear_rh_mm + base_step1.aero_compression_rear_mm,
        )

        best: OptimizerResult | None = None
        for seed in _load_bmw_sebring_seeds():
            candidate = self._evaluate_seed(
                base_step1=base_step1,
                seed=seed,
                target_front_static=target_front_static,
                target_rear_static=target_rear_static,
                target_balance=target_balance,
                fuel_load_l=fuel_load_l,
                damping_ratio_scale=damping_ratio_scale,
                lltd_offset=lltd_offset,
                measured=measured,
                camber_confidence=camber_confidence,
            )
            if candidate is None:
                continue
            if best is None or candidate.score < best.score:
                best = candidate

        if best is None:
            raise RuntimeError("BMW/Sebring optimizer failed to find a feasible setup")
        return best

    def _optimize_continuous_state(
        self,
        seed: BMWSebringSeed,
        *,
        target_front_static: float,
        target_rear_static: float,
        fuel_load_l: float,
    ) -> GarageSetupState | None:
        """Solve continuous garage variables for a discrete platform seed."""

        def objective(x: np.ndarray) -> float:
            state = GarageSetupState(
                front_pushrod_mm=float(x[0]),
                rear_pushrod_mm=float(x[1]),
                front_heave_nmm=seed.front_heave_nmm,
                front_heave_perch_mm=float(x[2]),
                rear_third_nmm=seed.rear_third_nmm,
                rear_third_perch_mm=seed.rear_third_perch_mm,
                front_torsion_od_mm=seed.front_torsion_od_mm,
                rear_spring_nmm=seed.rear_spring_nmm,
                rear_spring_perch_mm=float(x[3]),
                front_camber_deg=float(x[4]),
                fuel_l=fuel_load_l,
            )
            outputs = self.garage_model.predict(state)
            penalty = 0.0
            penalty += (outputs.front_static_rh_mm - target_front_static) ** 2 * 400.0
            penalty += (outputs.rear_static_rh_mm - target_rear_static) ** 2 * 40.0
            penalty += max(0.0, outputs.heave_slider_defl_static_mm - self.garage_model.max_slider_mm) ** 2 * 800.0
            penalty += max(0.0, -outputs.travel_margin_front_mm) ** 2 * 400.0
            penalty += abs(x[4] - seed.front_camber_deg) * 2.0
            return float(penalty)

        x0 = np.array([
            seed.front_pushrod_mm,
            seed.rear_pushrod_mm,
            seed.front_heave_perch_mm,
            seed.rear_spring_perch_mm,
            seed.front_camber_deg,
        ], dtype=float)
        bounds = [
            (-28.5, -22.0),
            (-32.0, -15.0),
            (-20.0, -5.0),
            (30.0, 42.5),
            (-3.5, -2.0),
        ]
        result = minimize(
            objective,
            x0=x0,
            method="SLSQP",
            bounds=bounds,
            options={"maxiter": 120, "ftol": 1e-9},
        )
        if not result.success:
            return None
        x = result.x
        return GarageSetupState(
            front_pushrod_mm=round(float(x[0]) * 2) / 2,
            rear_pushrod_mm=round(float(x[1]) * 2) / 2,
            front_heave_nmm=seed.front_heave_nmm,
            front_heave_perch_mm=round(float(x[2]) * 2) / 2,
            rear_third_nmm=seed.rear_third_nmm,
            rear_third_perch_mm=seed.rear_third_perch_mm,
            front_torsion_od_mm=seed.front_torsion_od_mm,
            rear_spring_nmm=seed.rear_spring_nmm,
            rear_spring_perch_mm=round(float(x[3]) * 2) / 2,
            front_camber_deg=round(float(x[4]) / 0.1) * 0.1,
            fuel_l=fuel_load_l,
        )

    def _evaluate_seed(
        self,
        *,
        base_step1: RakeSolution,
        seed: BMWSebringSeed,
        target_front_static: float,
        target_rear_static: float,
        target_balance: float,
        fuel_load_l: float,
        damping_ratio_scale: float,
        lltd_offset: float,
        measured: Any,
        camber_confidence: str,
    ) -> OptimizerResult | None:
        state = self._optimize_continuous_state(
            seed,
            target_front_static=target_front_static,
            target_rear_static=target_rear_static,
            fuel_load_l=fuel_load_l,
        )
        if state is None:
            return None

        step1 = replace(base_step1)
        step1.front_pushrod_offset_mm = state.front_pushrod_mm
        step1.rear_pushrod_offset_mm = state.rear_pushrod_mm
        step1.static_front_rh_mm = round(target_front_static, 1)
        step1.static_rear_rh_mm = round(target_rear_static, 1)
        step1.rake_static_mm = round(step1.static_rear_rh_mm - step1.static_front_rh_mm, 1)

        step2 = self.heave_solver.solve(
            dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
            dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
            front_heave_floor_nmm=seed.front_heave_nmm,
            rear_third_floor_nmm=seed.rear_third_nmm,
            front_heave_perch_target_mm=state.front_heave_perch_mm,
            front_pushrod_mm=state.front_pushrod_mm,
            rear_pushrod_mm=state.rear_pushrod_mm,
            front_torsion_od_mm=seed.front_torsion_od_mm,
            rear_spring_nmm=seed.rear_spring_nmm,
            rear_spring_perch_mm=state.rear_spring_perch_mm,
            rear_third_perch_mm=seed.rear_third_perch_mm,
            fuel_load_l=fuel_load_l,
            front_camber_deg=state.front_camber_deg,
        )
        if not step2.garage_constraints_ok:
            return None

        step3 = self.corner_solver.solution_from_explicit_rates(
            front_heave_nmm=step2.front_heave_nmm,
            rear_third_nmm=step2.rear_third_nmm,
            front_torsion_od_mm=seed.front_torsion_od_mm,
            rear_spring_rate_nmm=seed.rear_spring_nmm,
            fuel_load_l=fuel_load_l,
            rear_spring_perch_mm=state.rear_spring_perch_mm,
        )

        self.heave_solver.reconcile_solution(
            step1,
            step2,
            step3,
            fuel_load_l=fuel_load_l,
            front_camber_deg=state.front_camber_deg,
            verbose=False,
        )
        reconcile_ride_heights(
            self.car,
            step1,
            step2,
            step3,
            fuel_load_l=fuel_load_l,
            track_name=self.track.track_name,
            verbose=False,
        )

        rear_wheel_rate_nmm = step3.rear_spring_rate_nmm * self.car.corner_spring.rear_motion_ratio ** 2
        provisional_step6 = self.damper_solver.solve(
            front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
            front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
            rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
            fuel_load_l=fuel_load_l,
            damping_ratio_scale=damping_ratio_scale,
            measured=measured,
            front_heave_nmm=step2.front_heave_nmm,
            rear_third_nmm=step2.rear_third_nmm,
        )
        refined_step2 = self.heave_solver.solve(
            dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
            dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
            front_heave_floor_nmm=seed.front_heave_nmm,
            rear_third_floor_nmm=seed.rear_third_nmm,
            front_heave_perch_target_mm=state.front_heave_perch_mm,
            front_pushrod_mm=state.front_pushrod_mm,
            rear_pushrod_mm=state.rear_pushrod_mm,
            front_torsion_od_mm=seed.front_torsion_od_mm,
            rear_spring_nmm=seed.rear_spring_nmm,
            rear_spring_perch_mm=state.rear_spring_perch_mm,
            rear_third_perch_mm=seed.rear_third_perch_mm,
            fuel_load_l=fuel_load_l,
            front_camber_deg=state.front_camber_deg,
            front_hs_damper_nsm=provisional_step6.c_hs_front,
            rear_hs_damper_nsm=provisional_step6.c_hs_rear,
        )
        if (
            abs(refined_step2.front_heave_nmm - step2.front_heave_nmm) > 0.05
            or abs(refined_step2.rear_third_nmm - step2.rear_third_nmm) > 0.05
            or abs(refined_step2.perch_offset_front_mm - step2.perch_offset_front_mm) > 0.05
        ):
            step2 = refined_step2
            step3 = self.corner_solver.solution_from_explicit_rates(
                front_heave_nmm=step2.front_heave_nmm,
                rear_third_nmm=step2.rear_third_nmm,
                front_torsion_od_mm=seed.front_torsion_od_mm,
                rear_spring_rate_nmm=seed.rear_spring_nmm,
                fuel_load_l=fuel_load_l,
                rear_spring_perch_mm=state.rear_spring_perch_mm,
            )
            self.heave_solver.reconcile_solution(
                step1,
                step2,
                step3,
                fuel_load_l=fuel_load_l,
                front_camber_deg=state.front_camber_deg,
                front_hs_damper_nsm=provisional_step6.c_hs_front,
                verbose=False,
            )
            reconcile_ride_heights(
                self.car,
                step1,
                step2,
                step3,
                fuel_load_l=fuel_load_l,
                track_name=self.track.track_name,
                verbose=False,
            )
            rear_wheel_rate_nmm = step3.rear_spring_rate_nmm * self.car.corner_spring.rear_motion_ratio ** 2

        step4 = self.arb_solver.solve(
            front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
            lltd_offset=lltd_offset,
        )
        step5 = self.geom_solver.solve(
            k_roll_total_nm_deg=step4.k_roll_front_total + step4.k_roll_rear_total,
            front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
            fuel_load_l=fuel_load_l,
            camber_confidence=camber_confidence,
        )
        reconcile_ride_heights(
            self.car,
            step1,
            step2,
            step3,
            step5=step5,
            fuel_load_l=fuel_load_l,
            track_name=self.track.track_name,
            verbose=False,
        )
        step6 = self.damper_solver.solve(
            front_wheel_rate_nmm=step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=rear_wheel_rate_nmm,
            front_dynamic_rh_mm=step1.dynamic_front_rh_mm,
            rear_dynamic_rh_mm=step1.dynamic_rear_rh_mm,
            fuel_load_l=fuel_load_l,
            damping_ratio_scale=damping_ratio_scale,
            measured=measured,
            front_heave_nmm=step2.front_heave_nmm,
            rear_third_nmm=step2.rear_third_nmm,
        )

        garage_outputs = self.garage_model.predict(
            GarageSetupState.from_solver_steps(
                step1=step1,
                step2=step2,
                step3=step3,
                step5=step5,
                fuel_l=fuel_load_l,
            ),
            front_excursion_p99_mm=step2.front_excursion_at_rate_mm,
        )
        constraint = self.garage_model.validate(
            GarageSetupState.from_solver_steps(
                step1=step1,
                step2=step2,
                step3=step3,
                step5=step5,
                fuel_l=fuel_load_l,
            ),
            front_excursion_p99_mm=step2.front_excursion_at_rate_mm,
            front_bottoming_margin_mm=step2.front_bottoming_margin_mm,
            vortex_burst_margin_mm=step1.vortex_burst_margin_mm,
        )
        if not constraint.valid:
            return None

        score = 0.0
        score += abs(step1.df_balance_pct - target_balance) * 60.0
        score += step4.lltd_error * 200.0
        score += max(0.0, -step2.front_bottoming_margin_mm) * 100.0
        score += max(0.0, -step2.rear_bottoming_margin_mm) * 60.0
        score += max(0.0, -step1.vortex_burst_margin_mm) * 120.0
        score += max(0.0, self.garage_model.max_slider_mm - garage_outputs.heave_slider_defl_static_mm) * -0.2
        score += step2.front_heave_nmm * 0.04
        score += step2.rear_third_nmm * 0.004
        score += step3.rear_spring_rate_nmm * 0.02
        score -= step1.ld_ratio * 2.0
        score += abs(step5.front_camber_deg - state.front_camber_deg) * 4.0
        return OptimizerResult(
            step1=step1,
            step2=step2,
            step3=step3,
            step4=step4,
            step5=step5,
            step6=step6,
            score=float(score),
            garage_outputs=garage_outputs,
        )


def optimize_if_supported(
    *,
    car: Any,
    surface: Any,
    track: Any,
    target_balance: float,
    balance_tolerance: float,
    fuel_load_l: float,
    pin_front_min: bool,
    legacy_solver: bool = False,
    damping_ratio_scale: float = 1.0,
    lltd_offset: float = 0.0,
    measured: Any = None,
    camber_confidence: str = "estimated",
) -> OptimizerResult | None:
    """Run the BMW/Sebring optimizer when supported and not explicitly disabled."""
    if legacy_solver or not _is_bmw_sebring(car, track):
        return None
    optimizer = BMWSebringOptimizer(car, surface, track)
    return optimizer.optimize(
        target_balance=target_balance,
        balance_tolerance=balance_tolerance,
        fuel_load_l=fuel_load_l,
        pin_front_min=pin_front_min,
        damping_ratio_scale=damping_ratio_scale,
        lltd_offset=lltd_offset,
        measured=measured,
        camber_confidence=camber_confidence,
    )
