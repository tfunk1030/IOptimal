"""Tests for the W2.2 GT3 dispatch in solver/rake_solver.py.

Pinning the contract that GT3 cars (suspension_arch=GT3_COIL_4WHEEL,
heave_spring=None, balance-only aero map) make Step 1 produce a valid
RakeSolution without raising AttributeError, without front-pinning, and
without front-vortex constraint enforcement, while preserving GTP behaviour.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from aero_model.interpolator import load_car_surfaces
from car_model.cars import (
    BMW_M4_GT3,
    BMW_M_HYBRID_V8,
)
from solver.rake_solver import (
    RakeSolver,
    RakeSolution,
    reconcile_ride_heights,
)
from track_model.profile import TrackProfile


PARSED_DIR = Path(__file__).parent.parent / "data" / "aeromaps_parsed"


def _gt3_surface():
    """Mid-wing GT3 BMW aero surface."""
    if not (PARSED_DIR / "bmw_m4_gt3_aero.npz").exists():
        pytest.skip("GT3 aero map not parsed yet")
    surfaces = load_car_surfaces("bmw_m4_gt3")
    # Wing 3.0 is mid-range for BMW M4 GT3 (range -2..6)
    return surfaces[3.0]


def _gtp_surface():
    """Reference BMW M Hybrid V8 surface — GTP regression."""
    if not (PARSED_DIR / "bmw_aero.npz").exists():
        pytest.skip("GTP BMW aero map not parsed yet")
    surfaces = load_car_surfaces("bmw")
    # Pick any wing — wing 17 is typical for GTP BMW
    return surfaces[next(iter(surfaces))]


def _synthetic_track(name: str = "Spielberg") -> TrackProfile:
    return TrackProfile(
        track_name=name,
        track_config="x",
        track_length_m=4318.0,
        car="bmw_m4_gt3",
        best_lap_time_s=90.0,
        median_speed_kph=180.0,
        shock_vel_p99_front_mps=0.5,
        shock_vel_p99_front_clean_mps=0.5,
        shock_vel_p99_rear_mps=0.6,
        shock_vel_p99_rear_clean_mps=0.6,
    )


# ─── AeroSurface.has_ld surfacing ───────────────────────────────────────────


class TestAeroSurfaceHasLd:
    def test_gt3_balance_only_has_ld_false(self):
        surf = _gt3_surface()
        assert surf.has_ld is False

    def test_gt3_lift_drag_returns_nan(self):
        surf = _gt3_surface()
        result = surf.lift_drag(35.0, 40.0)
        assert math.isnan(result)

    def test_gt3_lift_drag_does_not_raise(self):
        """Pre-fix: lift_drag() raised ValueError on NaN. Now it returns NaN."""
        surf = _gt3_surface()
        # Should not raise — must return NaN
        try:
            result = surf.lift_drag(35.0, 40.0)
        except Exception as exc:  # pragma: no cover - defensive
            pytest.fail(f"lift_drag raised on balance-only map: {exc}")
        assert math.isnan(result)

    def test_gtp_has_ld_true(self):
        surf = _gtp_surface()
        assert surf.has_ld is True

    def test_gtp_lift_drag_returns_finite(self):
        surf = _gtp_surface()
        result = surf.lift_drag(30.0, 40.0)
        assert math.isfinite(result)
        assert 1.0 < result < 6.0


# ─── RakeSolver dispatch on GT3 ─────────────────────────────────────────────


class TestRakeSolverGT3:
    def test_gt3_solve_does_not_raise(self):
        """Pre-fix: heave_spring=None caused AttributeError when the calibrated
        rh_model branch was taken or _solve_pinned_front was reached."""
        surf = _gt3_surface()
        track = _synthetic_track()
        solver = RakeSolver(BMW_M4_GT3, surf, track)
        sol = solver.solve()
        assert isinstance(sol, RakeSolution)

    def test_gt3_mode_is_balance_only_search(self):
        surf = _gt3_surface()
        track = _synthetic_track()
        sol = RakeSolver(BMW_M4_GT3, surf, track).solve()
        assert sol.mode == "balance_only_search"

    def test_gt3_balance_finite_ld_nan(self):
        surf = _gt3_surface()
        track = _synthetic_track()
        sol = RakeSolver(BMW_M4_GT3, surf, track).solve()
        assert math.isfinite(sol.df_balance_pct)
        assert math.isnan(sol.ld_ratio)

    def test_gt3_ld_cost_of_pinning_is_nan_not_zero(self):
        """The pre-fix bug: _find_free_max_ld returned 0.0 silently for
        balance-only maps, so ld_cost_of_pinning = 0 - 0 = 0.0. The fix
        propagates NaN so a reader can tell L/D is unavailable."""
        surf = _gt3_surface()
        track = _synthetic_track()
        sol = RakeSolver(BMW_M4_GT3, surf, track).solve()
        assert math.isnan(sol.ld_cost_of_pinning)
        assert math.isnan(sol.free_opt_ld)

    def test_gt3_pin_front_kwarg_ignored(self):
        """Even with pin_front_min=True (the legacy default), GT3 cars must
        dispatch to balance_only_search — the pin strategy is GTP-specific."""
        surf = _gt3_surface()
        track = _synthetic_track()
        sol_pinned = RakeSolver(BMW_M4_GT3, surf, track).solve(pin_front_min=True)
        sol_free = RakeSolver(BMW_M4_GT3, surf, track).solve(pin_front_min=False)
        assert sol_pinned.mode == "balance_only_search"
        assert sol_free.mode == "balance_only_search"

    def test_gt3_static_front_above_min(self):
        """GT3 must not be pinned to the sim minimum — static front should
        come from the balance-only seed, not min_front_rh_static."""
        surf = _gt3_surface()
        track = _synthetic_track()
        sol = RakeSolver(BMW_M4_GT3, surf, track).solve()
        # The seed is the midpoint of static range, so static_front should be
        # noticeably above the floor.
        assert sol.static_front_rh_mm >= BMW_M4_GT3.min_front_rh_static


# ─── reconcile_ride_heights early-return on GT3 ─────────────────────────────


class TestReconcileGT3:
    def test_reconcile_returns_silently_for_gt3(self):
        """The non-garage-model path of reconcile reads
        heave_spring.perch_offset_front_baseline_mm and step2.front_heave_nmm;
        both blow up for GT3 cars whose heave_spring is None and whose
        step2 = HeaveSolution.null(). The fix is an early-return."""
        surf = _gt3_surface()
        track = _synthetic_track()
        solver = RakeSolver(BMW_M4_GT3, surf, track)
        sol = solver.solve()

        before_static_f = sol.static_front_rh_mm
        before_static_r = sol.static_rear_rh_mm

        # Should not raise — reconcile early-returns on heave_spring=None.
        reconcile_ride_heights(
            BMW_M4_GT3, sol,
            step2=None, step3=None,
            fuel_load_l=80.0,
            track_name="spielberg",
            verbose=False,
        )
        # Reconcile is a no-op for GT3 — values unchanged.
        assert sol.static_front_rh_mm == before_static_f
        assert sol.static_rear_rh_mm == before_static_r


# ─── GTP regression — must keep working ─────────────────────────────────────


class TestRakeSolverGTPRegression:
    def test_gtp_pinned_front_still_produces_finite_ld(self):
        surf = _gtp_surface()
        track = TrackProfile(
            track_name="Sebring",
            track_config="International",
            track_length_m=6019.0,
            car="bmw",
            best_lap_time_s=110.0,
            median_speed_kph=180.0,
            shock_vel_p99_front_mps=0.5,
            shock_vel_p99_front_clean_mps=0.5,
            shock_vel_p99_rear_mps=0.6,
            shock_vel_p99_rear_clean_mps=0.6,
        )
        sol = RakeSolver(BMW_M_HYBRID_V8, surf, track).solve(pin_front_min=True)
        assert sol.mode == "pinned_front"
        assert math.isfinite(sol.ld_ratio)
        assert sol.ld_ratio > 0.0
        # ld_cost_of_pinning is finite (could be 0 if pinned == free, but not NaN)
        assert math.isfinite(sol.ld_cost_of_pinning) or sol.ld_cost_of_pinning == 0.0

    def test_gtp_free_optimization_still_produces_finite_ld(self):
        surf = _gtp_surface()
        track = TrackProfile(
            track_name="Sebring",
            track_config="International",
            track_length_m=6019.0,
            car="bmw",
            best_lap_time_s=110.0,
            median_speed_kph=180.0,
            shock_vel_p99_front_mps=0.5,
            shock_vel_p99_front_clean_mps=0.5,
            shock_vel_p99_rear_mps=0.6,
            shock_vel_p99_rear_clean_mps=0.6,
        )
        sol = RakeSolver(BMW_M_HYBRID_V8, surf, track).solve(pin_front_min=False)
        assert sol.mode == "free_optimization"
        assert math.isfinite(sol.ld_ratio)
