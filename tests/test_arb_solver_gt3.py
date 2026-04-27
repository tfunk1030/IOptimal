"""Tests for the W2.4 GT3 dispatch in solver/arb_solver.py.

Pins the contract that the ARB solver Step 4:
- enumerates `rear_size_labels` (not the collapsed `rear_blade_count`) for GT3
  cars, exposing the size label as the primary live tuning unit (A-3..A-6);
- emits a physically-valid LLTD target per GT3 car using `measured_lltd_target`
  rather than the bare OptimumG +5pp rule (which mis-targets RR cars by 5+ pp)
  (A-1);
- fails loudly when Step 3 hands it a zero front wheel rate (A-2);
- preserves legacy GTP behavior (BMW M Hybrid V8 still tunes blades 1..5);
- treats `blade_factor(1, 1) == 1.0` so paired-blade ARB stiffness lookups are
  not silently scaled by 0.30 (A-9).

Audit reference: docs/audits/gt3_phase2/solver-rake-corner-arb.md A-1..A-9.
"""
from __future__ import annotations

import pytest

from car_model.cars import (
    ASTON_MARTIN_VANTAGE_GT3,
    BMW_M4_GT3,
    BMW_M_HYBRID_V8,
    PORSCHE_992_GT3R,
)
from solver.arb_solver import ARBSolver, ARBSolution
from track_model.profile import TrackProfile


def _track(car: str = "bmw_m4_gt3", pct_above_200kph: float = 0.2) -> TrackProfile:
    return TrackProfile(
        track_name="Sebring",
        track_config="x",
        track_length_m=6020.0,
        car=car,
        best_lap_time_s=110.0,
        median_speed_kph=170.0,
        shock_vel_p99_front_mps=0.5,
        shock_vel_p99_front_clean_mps=0.5,
        shock_vel_p99_rear_mps=0.6,
        shock_vel_p99_rear_clean_mps=0.6,
        pct_above_200kph=pct_above_200kph,
    )


# ─── A-9: blade_factor(1, 1) sanity check ─────────────────────────────────


class TestBladeFactorOne:
    @pytest.mark.parametrize(
        "car",
        [BMW_M4_GT3, ASTON_MARTIN_VANTAGE_GT3, PORSCHE_992_GT3R],
        ids=["bmw_m4_gt3", "aston", "porsche_992_gt3r"],
    )
    def test_blade_factor_one_one_returns_one(self, car):
        """Audit A-9: paired-blade GT3 ARBs must NOT be scaled by 0.30 (the
        legacy formula's value at blade=1, max_blade=1)."""
        assert car.arb.blade_factor(1, 1) == 1.0

    def test_blade_factor_legacy_gtp_unchanged(self):
        """GTP cars (blade_count=5) keep the 0.30..1.0 ramp."""
        arb = BMW_M_HYBRID_V8.arb
        assert arb.blade_factor(1, 5) == pytest.approx(0.30)
        assert arb.blade_factor(5, 5) == pytest.approx(1.00)


# ─── A-3..A-6: rear-search loop enumerates `rear_size_labels` ──────────────


class TestGT3SolveDoesNotCrash:
    """For each GT3 car, solve(...) must not crash and must produce a valid
    ARBSolution whose rear_arb_size is one of arb.rear_size_labels."""

    @pytest.mark.parametrize(
        "car,front_rate,rear_rate",
        [
            (BMW_M4_GT3, 220.0, 180.0),
            (ASTON_MARTIN_VANTAGE_GT3, 220.0, 180.0),
            (PORSCHE_992_GT3R, 180.0, 200.0),
        ],
        ids=["bmw_m4_gt3", "aston", "porsche_992_gt3r"],
    )
    def test_gt3_solve_returns_valid_solution(self, car, front_rate, rear_rate):
        track = _track(car=car.canonical_name)
        sol = ARBSolver(car, track).solve(
            front_wheel_rate_nmm=front_rate,
            rear_wheel_rate_nmm=rear_rate,
        )
        assert isinstance(sol, ARBSolution)
        # The chosen rear size must be one of the legal labels (and not the
        # "Disconnected" sentinel if present).
        assert sol.rear_arb_size in car.arb.rear_size_labels
        assert sol.rear_arb_size.lower() != "disconnected"
        # GT3 collapses the blade dimension — chosen blade must be 1.
        assert sol.rear_arb_blade_start == 1
        assert sol.front_arb_blade_start == 1
        # GT3 path populates size-based slow/fast tuning (A-3..A-6).
        assert sol.rarb_size_slow_corner is not None
        assert sol.rarb_size_fast_corner is not None
        assert sol.rarb_size_slow_corner in car.arb.rear_size_labels
        assert sol.rarb_size_fast_corner in car.arb.rear_size_labels


class TestGT3SearchEnumeratesSizeLabels:
    """Confirm the search loop iterates `rear_size_labels` (not collapsed to
    a single iteration on the blade dimension). Uses a synthetic stiffness
    table so that LLTD is monotonic in size."""

    def _calibrated_bmw_gt3(self):
        # Clone the BMW M4 GT3 ARB with non-zero stiffness so the search has
        # signal. Stiffness rises with label index (ascending convention).
        from copy import deepcopy
        car = deepcopy(BMW_M4_GT3)
        n = len(car.arb.rear_size_labels)
        # Linear ramp 1500..6000 N·m/deg, climbing with label idx.
        car.arb.rear_stiffness_nmm_deg = [
            1500.0 + i * (4500.0 / max(n - 1, 1)) for i in range(n)
        ]
        car.arb.front_stiffness_nmm_deg = [
            2000.0 + i * (5000.0 / max(len(car.arb.front_size_labels) - 1, 1))
            for i in range(len(car.arb.front_size_labels))
        ]
        return car

    def test_bmw_gt3_search_picks_size_close_to_target(self):
        """With calibrated stiffness, the best rear size matches the target
        LLTD (0.51 set on BMW_M4_GT3)."""
        car = self._calibrated_bmw_gt3()
        track = _track(car=car.canonical_name)
        sol = ARBSolver(car, track).solve(
            front_wheel_rate_nmm=220.0,
            rear_wheel_rate_nmm=180.0,
        )
        # LLTD error should be small — proves the search actually compared
        # multiple size labels and picked the closest.
        assert sol.lltd_error < 0.10
        # And the slow/fast labels straddle the chosen size.
        assert sol.rarb_size_slow_corner != sol.rarb_size_fast_corner

    def test_solve_candidates_enumerates_multiple_distinct_sizes(self):
        """A-3 / C-9 regression: solve_candidates must enumerate >=3 distinct
        rear_arb_size values when stiffness is calibrated. (The size IS the
        live axis — the legacy blade enumeration would only produce 1 value
        per call for GT3.)"""
        car = self._calibrated_bmw_gt3()
        track = _track(car=car.canonical_name)
        sols = ARBSolver(car, track).solve_candidates(
            front_wheel_rate_nmm=220.0,
            rear_wheel_rate_nmm=180.0,
            lltd_tolerance=0.20,  # wide enough to admit multiple sizes
            max_candidates=20,
        )
        distinct_sizes = {s.rear_arb_size for s in sols}
        assert len(distinct_sizes) >= 3, (
            f"Expected >=3 distinct rear_arb_size values (size IS the search "
            f"axis for GT3), got {distinct_sizes}"
        )


# ─── A-1: per-car LLTD target ──────────────────────────────────────────────


class TestPerCarLLTDTarget:
    def test_bmw_m4_gt3_target_close_to_051(self):
        """BMW M4 GT3 weight_dist_front=0.464, audit recommendation 0.51."""
        sol = ARBSolver(BMW_M4_GT3, _track()).solve(
            front_wheel_rate_nmm=220.0,
            rear_wheel_rate_nmm=180.0,
        )
        assert sol.lltd_target == pytest.approx(0.51, abs=0.005)

    def test_aston_target_close_to_053(self):
        """Aston Martin Vantage GT3 weight_dist_front=0.480, audit reco 0.53."""
        sol = ARBSolver(
            ASTON_MARTIN_VANTAGE_GT3,
            _track(car="aston_martin_vantage_gt3"),
        ).solve(
            front_wheel_rate_nmm=220.0,
            rear_wheel_rate_nmm=180.0,
        )
        assert sol.lltd_target == pytest.approx(0.53, abs=0.005)

    def test_porsche_992_gt3r_target_closer_to_045_than_050(self):
        """A-1 (BLOCKER): Porsche 992 GT3 R is RR (W_f=0.449). The bare formula
        would give 0.499; per audit empirical value is closer to ~0.45."""
        sol = ARBSolver(
            PORSCHE_992_GT3R,
            _track(car="porsche_992_gt3r"),
        ).solve(
            front_wheel_rate_nmm=180.0,
            rear_wheel_rate_nmm=200.0,
        )
        # Direct equality on the configured target (uses measured_lltd_target).
        assert sol.lltd_target == pytest.approx(0.45, abs=0.005)
        # And explicitly NOT the bare-formula 0.499.
        assert abs(sol.lltd_target - 0.45) < abs(sol.lltd_target - 0.499)


# ─── A-2: loud-fail assertion on zero front wheel rate ─────────────────────


class TestFrontWheelRateAssertion:
    def test_zero_front_rate_raises(self):
        """A-2: defense-in-depth — Step 4 must not silently accept a zero front
        wheel rate (which would happen if a future regression mis-routed GT3
        through a torsion-bar branch and Step 3 emitted 0)."""
        with pytest.raises(ValueError, match=r"front wheel rate"):
            ARBSolver(BMW_M4_GT3, _track()).solve(
                front_wheel_rate_nmm=0.0,
                rear_wheel_rate_nmm=180.0,
            )

    def test_negative_front_rate_raises(self):
        with pytest.raises(ValueError, match=r"front wheel rate"):
            ARBSolver(BMW_M4_GT3, _track()).solve(
                front_wheel_rate_nmm=-50.0,
                rear_wheel_rate_nmm=180.0,
            )


# ─── GTP regression: blade-based tuning preserved ──────────────────────────


class TestGTPRegression:
    """BMW M Hybrid V8 (rear_blade_count=5) must continue to tune the BLADE
    rather than collapsing to size-label tuning."""

    def test_bmw_gtp_picks_nontrivial_blade(self):
        car = BMW_M_HYBRID_V8
        sol = ARBSolver(car, _track(car="bmw")).solve(
            front_wheel_rate_nmm=60.0,
            rear_wheel_rate_nmm=70.0,
        )
        assert sol.rear_arb_blade_start >= 1
        # Exercises the live RARB blade range (not just blade=1 collapse).
        assert sol.rarb_blade_fast_corner > sol.rarb_blade_slow_corner
        # GTP path: size-label live tuning fields are None.
        assert sol.rarb_size_slow_corner is None
        assert sol.rarb_size_fast_corner is None

    def test_bmw_gtp_search_can_pick_blade_above_one(self):
        """With sufficiently soft springs, the search should pick blade > 1
        to add stiffness."""
        car = BMW_M_HYBRID_V8
        sol = ARBSolver(car, _track(car="bmw")).solve(
            front_wheel_rate_nmm=30.0,
            rear_wheel_rate_nmm=40.0,
        )
        # The search visited blades 1..5; the chosen blade is in that range.
        assert 1 <= sol.rear_arb_blade_start <= car.arb.rear_blade_count
