"""W3.3 — per-car fuel constants in stint_model + damper_solver.

Removes hardcoded 89.0 L (BMW GTP fuel cap) defaults from:
  * solver/damper_solver.py:444 (solve)         — now Option A: required kwarg
  * solver/damper_solver.py:1030 (solution_from_explicit_settings) — same
  * solver/stint_model.py:184 (display string)  — derived from car.fuel_capacity_l
  * solver/stint_model.py:296 (compute_fuel_states default) — per-car
  * solver/stint_model.py:730 (analyze_stint default)       — per-car

Audit reference: docs/audits/gt3_phase2/solver-damper-legality.md
findings F3, F4, ST1, ST2, ST3, ST4, ST7.
"""
from __future__ import annotations

import copy
import unittest

import pytest

from car_model.cars import (
    ASTON_MARTIN_VANTAGE_GT3,
    BMW_M4_GT3,
    BMW_M_HYBRID_V8,
    PORSCHE_992_GT3R,
)
from solver.damper_solver import DamperSolver
from solver.stint_model import (
    HeaveRecommendation,
    StintCondition,
    StintStrategy,
    analyze_stint,
    compute_fuel_states,
)
from track_model.profile import TrackProfile


# ─── 1. DamperSolver.solve() requires explicit fuel_load_l (Option A) ────


def _track_for_fuel_test(car_key: str = "bmw") -> TrackProfile:
    return TrackProfile(
        track_name="UnitTest",
        track_config="baseline",
        track_length_m=5000.0,
        car=car_key,
        best_lap_time_s=90.0,
        shock_vel_p95_front_mps=0.120,
        shock_vel_p95_rear_mps=0.160,
        shock_vel_p99_front_mps=0.260,
        shock_vel_p99_rear_mps=0.320,
    )


def _gt3_with_zeta_calibrated(base):
    """Bypass the zeta-calibration gate for fuel-default tests.

    The fuel_load_l guard fires BEFORE the zeta gate, so this preparation is
    only needed for the success path tests below.
    """
    car = copy.deepcopy(base)
    car.damper.zeta_is_calibrated = True
    car.damper.zeta_target_ls_front = 0.55
    car.damper.zeta_target_ls_rear = 0.50
    car.damper.zeta_target_hs_front = 0.30
    car.damper.zeta_target_hs_rear = 0.30
    return car


def test_damper_solver_raises_on_missing_fuel_load_for_gt3():
    """W3.3 (F3): GT3 caller forgetting fuel_load_l must raise with the car's
    own fuel capacity in the message — never silently fall back to GTP 89 L."""
    car = _gt3_with_zeta_calibrated(BMW_M4_GT3)
    solver = DamperSolver(car, _track_for_fuel_test("bmw_m4_gt3"))
    with pytest.raises(ValueError) as exc:
        solver.solve(
            front_wheel_rate_nmm=140.0,
            rear_wheel_rate_nmm=180.0,
            front_dynamic_rh_mm=20.0,
            rear_dynamic_rh_mm=40.0,
            # fuel_load_l intentionally omitted
        )
    msg = str(exc.value)
    assert "fuel_load_l" in msg
    # BMW M4 GT3 fuel capacity is 100 L per car_model/cars.py.
    assert "100" in msg, (
        f"Expected GT3 capacity (100L) in error message, got: {msg!r}"
    )


def test_damper_solver_raises_on_missing_fuel_load_for_gtp():
    """Backward-compat regression: GTP caller that previously relied on the
    89.0 default also gets a clean error now (GTP capacity = 88.96 L)."""
    car = BMW_M_HYBRID_V8  # zeta_is_calibrated=True per fixture
    solver = DamperSolver(car, _track_for_fuel_test("bmw"))
    with pytest.raises(ValueError) as exc:
        solver.solve(
            front_wheel_rate_nmm=140.0,
            rear_wheel_rate_nmm=180.0,
            front_dynamic_rh_mm=20.0,
            rear_dynamic_rh_mm=40.0,
            front_heave_nmm=180.0,
            rear_third_nmm=540.0,
        )
    msg = str(exc.value)
    assert "fuel_load_l" in msg
    # BMW GTP fuel capacity is 88.96 L (formatted as "89L" by .0f).
    assert "89" in msg, (
        f"Expected GTP capacity (~89L) in error message, got: {msg!r}"
    )


def test_damper_solver_accepts_explicit_fuel_load_for_gt3():
    """Sanity: solve() runs cleanly when caller passes a real fuel level."""
    car = _gt3_with_zeta_calibrated(BMW_M4_GT3)
    solver = DamperSolver(car, _track_for_fuel_test("bmw_m4_gt3"))
    sol = solver.solve(
        front_wheel_rate_nmm=140.0,
        rear_wheel_rate_nmm=180.0,
        front_dynamic_rh_mm=20.0,
        rear_dynamic_rh_mm=40.0,
        fuel_load_l=60.0,
    )
    assert sol is not None


# ─── 2. solution_from_explicit_settings forwards the same guard ──────────


def test_damper_solver_explicit_settings_raises_on_missing_fuel_load():
    """W3.3 (F4): solution_from_explicit_settings forwards to solve(), so the
    same fuel_load_l guard fires — no parallel BMW-GTP default leak."""
    from solver.damper_solver import CornerDamperSettings

    car = _gt3_with_zeta_calibrated(BMW_M4_GT3)
    solver = DamperSolver(car, _track_for_fuel_test("bmw_m4_gt3"))
    nominal = CornerDamperSettings(
        ls_comp=4, ls_rbd=6, hs_comp=4, hs_rbd=6, hs_slope=2
    )
    with pytest.raises(ValueError) as exc:
        solver.solution_from_explicit_settings(
            front_wheel_rate_nmm=140.0,
            rear_wheel_rate_nmm=180.0,
            front_dynamic_rh_mm=20.0,
            rear_dynamic_rh_mm=40.0,
            lf=nominal,
            rf=nominal,
            lr=nominal,
            rr=nominal,
            # fuel_load_l intentionally omitted
        )
    assert "fuel_load_l" in str(exc.value)


# ─── 3. compute_fuel_states default uses per-car capacity ────────────────


def test_compute_fuel_states_default_uses_gt3_capacity():
    """W3.3 (ST3): default fuel range derived from car.fuel_capacity_l +
    car.fuel_stint_end_l, not the GTP literals [89, 50, 12]."""
    states = compute_fuel_states(BMW_M4_GT3)  # no fuel_levels_l passed
    fuel_l = [s.fuel_load_l for s in states]
    # BMW M4 GT3: fuel_capacity_l=100.0, fuel_stint_end_l=10.0 -> mid=55.0
    assert fuel_l == [100.0, 55.0, 10.0], (
        f"Expected [100, 55, 10] for BMW M4 GT3 default fuel sweep, got {fuel_l}"
    )


def test_compute_fuel_states_default_uses_gtp_capacity():
    """GTP regression: BMW V8 default sweep is [88.96, 54.48, 20]."""
    states = compute_fuel_states(BMW_M_HYBRID_V8)
    fuel_l = [s.fuel_load_l for s in states]
    # BMW V8: fuel_capacity_l=88.96, fuel_stint_end_l=20.0 -> mid=54.48
    assert abs(fuel_l[0] - 88.96) < 0.01
    assert abs(fuel_l[1] - 54.48) < 0.01
    assert abs(fuel_l[2] - 20.0) < 0.01


def test_compute_fuel_states_default_uses_aston_capacity():
    """Aston Vantage GT3: 106 L tank, end-of-stint 10 L → mid 58 L."""
    states = compute_fuel_states(ASTON_MARTIN_VANTAGE_GT3)
    fuel_l = [s.fuel_load_l for s in states]
    assert fuel_l == [106.0, 58.0, 10.0], (
        f"Expected [106, 58, 10] for Aston Vantage GT3, got {fuel_l}"
    )


def test_compute_fuel_states_default_uses_porsche_992_capacity():
    """Porsche 992 GT3 R: 100 L tank, end 10 L → mid 55 L."""
    states = compute_fuel_states(PORSCHE_992_GT3R)
    fuel_l = [s.fuel_load_l for s in states]
    assert fuel_l == [100.0, 55.0, 10.0], (
        f"Expected [100, 55, 10] for Porsche 992 GT3 R, got {fuel_l}"
    )


# ─── 4. analyze_stint default fuel sweep mirrors compute_fuel_states ─────


def test_analyze_stint_default_fuel_sweep_for_gt3():
    """W3.3 (ST3 mirror in analyze_stint): GT3 default sweep covers 100/55/10
    L, not the GTP 89/50/12."""
    result = analyze_stint(BMW_M4_GT3, stint_laps=15)
    fuel_l = [c.fuel_state.fuel_load_l for c in result.conditions]
    assert fuel_l == [100.0, 55.0, 10.0], (
        f"Expected GT3 default [100, 55, 10], got {fuel_l}"
    )


def test_analyze_stint_default_fuel_sweep_gtp_regression():
    """GTP regression: BMW V8 default sweep covers ~89/54/20 (was 89/50/12).
    The 50→54.48 and 12→20 drift is acceptable since both are derived from
    the same car-model constants now (`fuel_capacity_l=88.96`,
    `fuel_stint_end_l=20.0`)."""
    result = analyze_stint(BMW_M_HYBRID_V8, stint_laps=15)
    fuel_l = [c.fuel_state.fuel_load_l for c in result.conditions]
    assert abs(fuel_l[0] - 88.96) < 0.01, (
        f"Expected GTP full ~89, got {fuel_l[0]}"
    )
    assert abs(fuel_l[1] - 54.48) < 0.01, (
        f"Expected GTP mid ~54.5, got {fuel_l[1]}"
    )
    assert abs(fuel_l[2] - 20.0) < 0.01, (
        f"Expected GTP end 20, got {fuel_l[2]}"
    )


# ─── 5. HeaveRecommendation carries per-car full_fuel_l for display ──────


def test_heave_recommendation_carries_full_fuel_l_for_gtp():
    """W3.3 (ST1): HeaveRecommendation gains `full_fuel_l` field; the stint
    summary's "Full fuel (XXL):" line now reads from this instead of a hard
    89 literal. GTP populates it from the default fuel sweep."""
    result = analyze_stint(BMW_M_HYBRID_V8, stint_laps=15)
    hr = result.heave_recommendation
    # GTP uses the default fuel sweep; full = car.fuel_capacity_l = 88.96.
    assert abs(hr.full_fuel_l - 88.96) < 0.01, (
        f"Expected hr.full_fuel_l ~88.96 for BMW V8, got {hr.full_fuel_l}"
    )


def test_stint_summary_display_uses_per_car_fuel_capacity():
    """End-to-end: rendered stint summary string contains the actual fuel
    capacity, not the literal 89 (which would be wrong for any non-GTP car).
    GT3 short-circuits to an empty HeaveRecommendation (full_fuel_nmm=0) so we
    test via the GTP path where the heave block actually renders."""
    result = analyze_stint(BMW_M_HYBRID_V8, stint_laps=15)
    text = result.summary()
    # The "Full fuel (XXL):" line must use the per-car capacity rounded to
    # 0 decimals — for GTP this is 89 (formatted from 88.96).
    assert "Full fuel (89L):" in text, (
        f"Expected 'Full fuel (89L):' in BMW V8 stint summary, got:\n{text}"
    )


def test_heave_recommendation_default_full_fuel_l_is_zero():
    """Default HeaveRecommendation has full_fuel_l=0 (sentinel for GT3 where
    no heave-spring tuning is applicable)."""
    hr = HeaveRecommendation()
    assert hr.full_fuel_l == 0.0


if __name__ == "__main__":
    unittest.main()
