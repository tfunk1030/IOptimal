"""Driver-anchor pattern tests.

Verifies the physics-only nature of the driver-anchor design:
1. ``HeaveSolver.min_rate_for_sigma`` sticky pre-check returns the driver-loaded
   rate when its model σ is within ``STICKY_EPSILON_MM`` of the effective target.
2. ``ARBSolver`` falls back to the driver-loaded rear ARB blade when the model's
   best-search LLTD error exceeds the 3 pp anchor threshold.
3. None of the anchor inputs flowing into these solvers carry a lap-time signal
   (Key Principle 11: anchors are physics-driven, never lap-time-driven).
"""

from __future__ import annotations

import inspect

import pytest

from solver.arb_solver import ARBSolver
from solver.heave_solver import HeaveSolver
from track_model.profile import TrackProfile


# ── Track-profile helpers ──────────────────────────────────────────────────

def _sebring_profile() -> TrackProfile:
    return TrackProfile(
        track_name="Sebring International",
        track_config="International",
        track_length_m=6000.0,
        car="bmw",
        best_lap_time_s=110.0,
        shock_vel_p95_front_mps=0.120,
        shock_vel_p95_rear_mps=0.160,
        shock_vel_p99_front_mps=0.260,
        shock_vel_p99_rear_mps=0.320,
        shock_vel_p99_front_clean_mps=0.260,
        shock_vel_p99_rear_clean_mps=0.320,
    )


def _algarve_profile() -> TrackProfile:
    return TrackProfile(
        track_name="Algarve International",
        track_config="Grand Prix",
        track_length_m=4684.0,
        car="porsche",
        best_lap_time_s=98.0,
        median_speed_kph=180.0,
        pct_above_200kph=0.30,
        shock_vel_p95_front_mps=0.110,
        shock_vel_p95_rear_mps=0.140,
        shock_vel_p99_front_mps=0.230,
        shock_vel_p99_rear_mps=0.290,
        shock_vel_p99_front_clean_mps=0.230,
        shock_vel_p99_rear_clean_mps=0.290,
    )


# ── σ-cal sticky pre-check ─────────────────────────────────────────────────

def _model_sigma_at(solver: HeaveSolver, rate: float, *, axle: str) -> tuple[float, float, float]:
    """Return ``(v_p99, m_eff, model_sigma)`` for a given solver/rate/axle."""
    v_p99 = solver.track.shock_vel_p99_rear_clean_mps if axle == "rear" \
        else solver.track.shock_vel_p99_front_clean_mps
    m_eff = solver.car.heave_spring.m_eff_at_rate(axle, rate)
    sigma = solver.sigma_from_excursion(
        solver.excursion(v_p99, m_eff, rate, axle=axle),
    )
    return v_p99, m_eff, sigma


class TestSigmaStickyAnchor:
    """``min_rate_for_sigma`` sticks to the driver-loaded rate when good enough."""

    def test_sticky_returns_current_rate_when_model_sigma_meets_target(
        self, bmw_car,
    ) -> None:
        # Driver rate inside BMW rear-heave range [100, 900].
        solver = HeaveSolver(bmw_car, _sebring_profile())
        current_rate = 200.0
        v_p99, m_eff, anchor_sigma = _model_sigma_at(solver, current_rate, axle="rear")

        # Use the model's own σ as the "measured" σ (cal_ratio == 1.0) and set
        # the user target loose enough that the sticky pre-check fires.
        result = solver.min_rate_for_sigma(
            v_p99,
            m_eff,
            sigma_target_mm=anchor_sigma + 1.0,
            axle="rear",
            current_rate_nmm=current_rate,
            current_meas_sigma_mm=anchor_sigma,
        )

        # Sticky path snaps to nearest 10 N/mm — the current rate is already on
        # the grid, so the result must equal it.
        assert result == pytest.approx(current_rate, abs=1e-6)

    def test_sticky_releases_when_target_far_below_current(self, bmw_car) -> None:
        solver = HeaveSolver(bmw_car, _sebring_profile())
        current_rate = 200.0
        v_p99, m_eff, anchor_sigma = _model_sigma_at(solver, current_rate, axle="rear")

        # Target much tighter than current σ — sticky pre-check must NOT fire,
        # and the search should pick a stiffer rate (>= current).
        result = solver.min_rate_for_sigma(
            v_p99,
            m_eff,
            sigma_target_mm=anchor_sigma * 0.5,
            axle="rear",
            current_rate_nmm=current_rate,
            current_meas_sigma_mm=anchor_sigma,
        )

        assert result >= current_rate


# ── ARB driver-anchor fallback ─────────────────────────────────────────────

class TestARBDriverAnchor:
    """ARB solver pins to the driver-loaded blade when the model can't reach target."""

    def test_anchor_pins_to_driver_blade_when_model_lltd_gap_large(
        self, porsche_car,
    ) -> None:
        track = _algarve_profile()
        solver = ARBSolver(porsche_car, track)

        # Pick a driver-loaded ARB inside the legal range.
        rear_size = porsche_car.arb.rear_baseline_size
        rear_blade = porsche_car.arb.rear_baseline_blade

        # Push the LLTD target far from anything reachable by the model so the
        # 3 pp gap threshold trips. The arb_solver clamps target_lltd to
        # [0.30, 0.75] internally, so a +0.20 offset on Porsche's 0.521 base
        # lands at the upper clamp and the model's best search remains far
        # below — guaranteed >3 pp gap.
        solution = solver.solve(
            front_wheel_rate_nmm=80.0,
            rear_wheel_rate_nmm=140.0,
            lltd_offset=0.20,
            current_rear_arb_size=rear_size,
            current_rear_arb_blade=rear_blade,
        )

        assert solution.rear_arb_size == rear_size
        assert solution.rear_arb_blade_start == rear_blade

    def test_anchor_does_not_fire_when_model_within_tolerance(self, bmw_car) -> None:
        """When the search finds a rear setup within the tight tolerance
        (≤1.5% LLTD error), the model is calibrated and the anchor stays
        dormant — the solver returns its model-derived best.

        Validated on BMW/Sebring: measured LLTD target=0.41 is reachable to
        0.5% error at the baseline rear ARB.
        """
        track = _sebring_profile()
        solver = ARBSolver(bmw_car, track)

        # Driver blade well off the model's best to make the assertion strong.
        driver_blade = 5
        baseline_blade = bmw_car.arb.rear_baseline_blade
        assert driver_blade != baseline_blade  # sanity

        solution = solver.solve(
            front_wheel_rate_nmm=80.0,
            rear_wheel_rate_nmm=140.0,
            lltd_offset=0.0,
            current_rear_arb_size=bmw_car.arb.rear_baseline_size,
            current_rear_arb_blade=driver_blade,
        )

        # Model is within tight tolerance (≤1.5%) → no anchor fallback,
        # solver picks the model's best blade rather than the driver's.
        assert solution.lltd_error <= 0.015
        assert solution.rear_arb_blade_start != driver_blade


# ── No-lap-time invariant ──────────────────────────────────────────────────

class TestNoLapTimeDependency:
    """Driver-anchor solver entry points must not consume ``lap_time`` arguments.

    This guards Key Principle 11: anchors fire on σ-measurement, model self-test,
    or close-tolerance agreement — never on lap time. A new ``lap_time`` parameter
    on these signatures would silently violate the contract.
    """

    @pytest.mark.parametrize(
        "func",
        [
            HeaveSolver.min_rate_for_sigma,
            ARBSolver.solve,
        ],
    )
    def test_signature_has_no_lap_time_parameter(self, func) -> None:
        sig = inspect.signature(func)
        offending = [name for name in sig.parameters if "lap_time" in name.lower()]
        assert not offending, (
            f"{func.__qualname__} accepts a lap-time parameter ({offending}) — "
            "violates Key Principle 11 (driver-anchor must be physics-only)."
        )
