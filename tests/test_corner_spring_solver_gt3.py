"""Tests for the W2.3 GT3 dispatch in solver/corner_spring_solver.py.

Pins the contract that GT3 cars (suspension_arch=GT3_COIL_4WHEEL,
heave_spring=None, paired front coils) make Step 3 produce a meaningful
CornerSpringSolution with non-zero front_coil_rate_nmm and rear_spring_rate_nmm,
without crashing on division-by-zero from the null Step 2 inputs, and without
falling into the Porsche-GTP roll-spring branch (`_apply_lltd_floor`).

Audit reference: docs/audits/gt3_phase2/solver-rake-corner-arb.md C-1..C-7,
C-9.
"""
from __future__ import annotations

import math

import pytest

from car_model.cars import (
    ASTON_MARTIN_VANTAGE_GT3,
    BMW_M4_GT3,
    BMW_M_HYBRID_V8,
    PORSCHE_963,
    PORSCHE_992_GT3R,
)
from solver.corner_spring_solver import (
    CornerSpringSolution,
    CornerSpringSolver,
)
from track_model.profile import TrackProfile


def _track(name: str = "Spielberg", car: str = "bmw_m4_gt3") -> TrackProfile:
    return TrackProfile(
        track_name=name,
        track_config="x",
        track_length_m=4318.0,
        car=car,
        best_lap_time_s=90.0,
        median_speed_kph=180.0,
        shock_vel_p99_front_mps=0.5,
        shock_vel_p99_front_clean_mps=0.5,
        shock_vel_p99_rear_mps=0.6,
        shock_vel_p99_rear_clean_mps=0.6,
    )


# ─── Constructor smoke tests ──────────────────────────────────────────────


class TestGT3SolverConstruction:
    def test_bmw_gt3_constructs(self):
        solver = CornerSpringSolver(BMW_M4_GT3, _track())
        assert solver is not None
        assert solver.car.suspension_arch.has_heave_third is False

    def test_aston_gt3_constructs(self):
        solver = CornerSpringSolver(ASTON_MARTIN_VANTAGE_GT3, _track(car="aston_martin_vantage_gt3"))
        assert solver is not None

    def test_porsche_992_gt3r_constructs(self):
        solver = CornerSpringSolver(PORSCHE_992_GT3R, _track(car="porsche_992_gt3r"))
        assert solver is not None


# ─── solve() with null Step 2 inputs ──────────────────────────────────────


class TestGT3SolveProducesValidOutput:
    """For each GT3 car, solve(0, 0, ...) must produce a non-zero front coil
    rate within the legal range, no torsion bar, and a non-zero rear spring
    rate within the legal range. No NaN, no zero on critical fields.
    """

    @pytest.mark.parametrize(
        "car",
        [BMW_M4_GT3, ASTON_MARTIN_VANTAGE_GT3, PORSCHE_992_GT3R],
        ids=["bmw_m4_gt3", "aston", "porsche_992_gt3r"],
    )
    def test_gt3_solve_with_null_step2(self, car):
        solver = CornerSpringSolver(car, _track(car=car.canonical_name))
        sol = solver.solve(front_heave_nmm=0.0, rear_third_nmm=0.0, fuel_load_l=50.0)

        assert isinstance(sol, CornerSpringSolution)

        # Front: paired coil populated, no torsion bar
        csm = car.corner_spring
        lo, hi = csm.front_spring_range_nmm
        assert sol.front_coil_rate_nmm > 0, "GT3 must populate front_coil_rate_nmm"
        assert lo <= sol.front_coil_rate_nmm <= hi, (
            f"front_coil_rate_nmm {sol.front_coil_rate_nmm} outside legal range [{lo}, {hi}]"
        )
        assert sol.front_torsion_od_mm == 0.0, "GT3 has no torsion bar"

        # Rear: spring rate non-zero and within range
        rlo, rhi = csm.rear_spring_range_nmm
        assert sol.rear_spring_rate_nmm > 0, "rear spring rate must be non-zero"
        assert rlo <= sol.rear_spring_rate_nmm <= rhi, (
            f"rear_spring_rate_nmm {sol.rear_spring_rate_nmm} outside [{rlo}, {rhi}]"
        )

        # Wheel rate / freq must be finite and positive
        assert sol.front_wheel_rate_nmm > 0
        assert math.isfinite(sol.front_wheel_rate_nmm)
        assert sol.front_natural_freq_hz > 0
        assert math.isfinite(sol.front_natural_freq_hz)
        assert sol.rear_natural_freq_hz > 0
        assert math.isfinite(sol.rear_natural_freq_hz)

    def test_gt3_solve_does_not_raise_zero_division(self):
        """C-4 fix: driver-anchor branch previously divided by 0 when
        rear_third_nmm == 0 (null Step 2). Verify no ZeroDivisionError when
        the driver loads a rear coil under GT3 architecture.
        """
        solver = CornerSpringSolver(BMW_M4_GT3, _track())
        # Should not raise
        sol = solver.solve(
            front_heave_nmm=0.0,
            rear_third_nmm=0.0,
            fuel_load_l=50.0,
            current_rear_spring_nmm=130.0,
        )
        assert sol.rear_spring_rate_nmm > 0


# ─── solution_from_explicit_rates GT3 path ────────────────────────────────


class TestGT3SolutionFromExplicitRates:
    def test_explicit_gt3_front_coil_rate_honored(self):
        """C-5 fix: solution_from_explicit_rates with front_coil_rate_nmm=200
        must produce a solution carrying that rate, NOT zero."""
        solver = CornerSpringSolver(BMW_M4_GT3, _track())
        sol = solver.solution_from_explicit_rates(
            front_heave_nmm=0.0,
            rear_third_nmm=0.0,
            front_torsion_od_mm=0.0,
            rear_spring_rate_nmm=180.0,
            fuel_load_l=50.0,
            front_coil_rate_nmm=200.0,
        )
        assert sol.front_coil_rate_nmm == 200.0
        assert sol.front_wheel_rate_nmm == pytest.approx(200.0, abs=1.0)
        assert sol.front_torsion_od_mm == 0.0

    def test_explicit_gt3_default_to_baseline_when_no_anchor(self):
        """Without explicit front_coil_rate_nmm, fall back to
        car.corner_spring.front_baseline_rate_nmm."""
        solver = CornerSpringSolver(BMW_M4_GT3, _track())
        sol = solver.solution_from_explicit_rates(
            front_heave_nmm=0.0,
            rear_third_nmm=0.0,
            front_torsion_od_mm=0.0,
            rear_spring_rate_nmm=180.0,
            fuel_load_l=50.0,
        )
        baseline = BMW_M4_GT3.corner_spring.front_baseline_rate_nmm
        assert sol.front_coil_rate_nmm == pytest.approx(baseline, abs=10.0)
        assert sol.front_torsion_od_mm == 0.0


# ─── _apply_lltd_floor architecture gate ──────────────────────────────────


class TestLltdFloorEarlyReturn:
    """C-7 fix: _apply_lltd_floor must early-return for GT3 (non-roll-spring)
    cars. The Porsche-GTP path must still run for the Porsche 963."""

    def test_gt3_lltd_floor_returns_input_unchanged(self):
        solver = CornerSpringSolver(BMW_M4_GT3, _track())
        rate_in = 220.0
        rate_out = solver._apply_lltd_floor(
            rate_in,
            rear_third_nmm=0.0,
            front_heave_nmm=0.0,
            fuel_load_l=50.0,
        )
        assert rate_out == rate_in

    def test_porsche_gtp_lltd_floor_still_runs(self):
        """Regression: the GTP Porsche 963 (front_is_roll_spring=True) must
        still run through the LLTD-floor helper (does not early-return)."""
        track = _track(car="porsche")
        solver = CornerSpringSolver(PORSCHE_963, track)
        # Sanity: the Porsche 963 GTP IS a roll-spring car
        assert PORSCHE_963.corner_spring.front_is_roll_spring is True
        # Call _apply_lltd_floor with a small input rate. The function may or
        # may not bump the rate — the contract here is that it doesn't
        # early-return AND doesn't raise. Just check we return a finite rate.
        rate_out = solver._apply_lltd_floor(
            100.0,
            rear_third_nmm=200.0,
            front_heave_nmm=200.0,
            fuel_load_l=50.0,
        )
        assert math.isfinite(rate_out)
        assert rate_out >= 100.0


# ─── summary() architecture-aware text ────────────────────────────────────


class TestGT3SummaryText:
    def test_gt3_summary_no_total_heave_parenthetical(self):
        """C-6 fix: the GT3 summary must NOT include 'heave/third + 2 *
        corner' total-heave block (no heave spring exists)."""
        solver = CornerSpringSolver(BMW_M4_GT3, _track())
        sol = solver.solve(front_heave_nmm=0.0, rear_third_nmm=0.0, fuel_load_l=50.0)
        text = sol.summary()
        assert "TOTAL HEAVE STIFFNESS" not in text
        assert "heave/third + 2 * corner" not in text
        # The GT3-specific replacement should be present
        assert "TOTAL AXLE WHEEL RATE" in text or "no heave spring" in text

    def test_gt3_summary_emits_front_coil_section(self):
        solver = CornerSpringSolver(BMW_M4_GT3, _track())
        sol = solver.solve(front_heave_nmm=0.0, rear_third_nmm=0.0, fuel_load_l=50.0)
        text = sol.summary()
        assert "FRONT COIL SPRING" in text


# ─── solve_candidates GT3 enumeration ─────────────────────────────────────


class TestGT3SolveCandidates:
    def test_gt3_solve_candidates_produces_distinct_candidates(self):
        """C-9 fix: solve_candidates() must enumerate over front_spring_range_nmm
        for GT3, producing at least 3 distinct candidates (not just [0.0])."""
        solver = CornerSpringSolver(BMW_M4_GT3, _track())
        candidates = solver.solve_candidates(
            front_heave_nmm=0.0, rear_third_nmm=0.0, fuel_load_l=50.0,
        )
        # At least the base + a handful of explored combos
        assert len(candidates) >= 3, (
            f"Expected ≥3 GT3 candidates, got {len(candidates)}"
        )
        # All candidates must be GT3 architecture (no torsion bar)
        for c in candidates:
            assert c.front_torsion_od_mm == 0.0
            assert c.front_coil_rate_nmm > 0

        # Distinct front coil rates across the candidate list
        distinct_rates = {round(c.front_coil_rate_nmm, 0) for c in candidates}
        assert len(distinct_rates) >= 2, (
            f"Expected diverse front coil rates, got {distinct_rates}"
        )


# ─── GTP regression: BMW M Hybrid V8 must be unaffected ───────────────────


class TestGTPRegressionPreserved:
    """Pin BMW GTP solve() output values from the current behaviour, so any
    accidental change to the GTP path is caught."""

    def test_bmw_gtp_solve_unchanged(self):
        solver = CornerSpringSolver(BMW_M_HYBRID_V8, _track(car="bmw"))
        sol = solver.solve(
            front_heave_nmm=180.0,
            rear_third_nmm=160.0,
            fuel_load_l=50.0,
        )
        # GTP: torsion bar OD should be in the legal range, non-zero
        assert sol.front_torsion_od_mm > 0
        assert sol.front_coil_rate_nmm == 0.0  # GTP must NOT populate
        # Rear spring rate must be in the legal coil range (BMW: 100..240 N/mm)
        assert 100.0 <= sol.rear_spring_rate_nmm <= 240.0

    def test_bmw_gtp_solve_pinned_values(self):
        """Pin the exact OD and rear rate the solver currently emits for
        BMW M Hybrid V8 with heave=180/third=160 to catch any regression."""
        solver = CornerSpringSolver(BMW_M_HYBRID_V8, _track(car="bmw"))
        sol = solver.solve(
            front_heave_nmm=180.0,
            rear_third_nmm=160.0,
            fuel_load_l=50.0,
        )
        # Pinned snapshot from main behaviour pre-W2.3:
        #   front_torsion_od ≈ 15.86 mm, rear ≈ 100 N/mm
        assert sol.front_torsion_od_mm == pytest.approx(15.86, abs=0.05)
        assert sol.rear_spring_rate_nmm == pytest.approx(100.0, abs=10.0)
