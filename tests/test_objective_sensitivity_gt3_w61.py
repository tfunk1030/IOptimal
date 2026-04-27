"""Tests for W6.1 GT3 dispatch in objective / sensitivity / lap-time tables.

Pins the contract that GT3 cars (suspension_arch=GT3_COIL_4WHEEL,
heave_spring=None) flow through the multi-objective scoring function and
the two sensitivity reporters without crashing on heave-spring null reads,
and that GTP regression behaviour is preserved.

Audit reference: docs/audits/gt3_phase2/solver-objective-sensitivity.md
F-O-1, F-O-2, F-O-3, F-O-4, F-O-5, F-S-1, F-S-3, F-S-4, F-S-5, F-LT-1, F-LT-2.
"""
from __future__ import annotations

import math

import pytest

from car_model.cars import (
    BMW_M4_GT3,
    BMW_M_HYBRID_V8,
)
from solver.heave_solver import HeaveSolution
from solver.objective import ObjectiveFunction
from solver.sensitivity import (
    analyze_step2_constraints,
    build_sensitivity_report,
)
from track_model.profile import TrackProfile


# ─── Track + Step fixtures ────────────────────────────────────────────────


def _track(name: str = "Spielberg", car: str = "bmw_m4_gt3") -> TrackProfile:
    return TrackProfile(
        track_name=name,
        track_config="x",
        track_length_m=4318.0,
        car=car,
        best_lap_time_s=90.0,
        median_speed_kph=180.0,
        shock_vel_p99_front_mps=0.45,
        shock_vel_p99_front_clean_mps=0.45,
        shock_vel_p99_rear_mps=0.55,
        shock_vel_p99_rear_clean_mps=0.55,
        shock_vel_p95_front_mps=0.30,
        shock_vel_p95_front_clean_mps=0.30,
        shock_vel_p95_rear_mps=0.35,
    )


def _make_rake_solution(static_f=68.0, static_r=78.0, wing=6.0, mode="balance_only_search"):
    from solver.rake_solver import RakeSolution
    return RakeSolution(
        dynamic_front_rh_mm=static_f - 10.0,
        dynamic_rear_rh_mm=static_r - 8.0,
        rake_dynamic_mm=(static_r - 8.0) - (static_f - 10.0),
        df_balance_pct=44.0,
        ld_ratio=3.0,
        front_rh_excursion_p99_mm=8.0,
        front_rh_min_p99_mm=static_f - 10.0 - 8.0,
        vortex_burst_threshold_mm=2.0,
        vortex_burst_margin_mm=10.0,
        static_front_rh_mm=static_f,
        static_rear_rh_mm=static_r,
        rake_static_mm=static_r - static_f,
        front_pushrod_offset_mm=0.0,
        rear_pushrod_offset_mm=0.0,
        aero_compression_front_mm=10.0,
        aero_compression_rear_mm=8.0,
        compression_ref_speed_kph=200.0,
        balance_error_pct=0.0,
        converged=True,
        iterations=1,
        mode=mode,
    )


def _make_arb_solution(lltd=0.51):
    from solver.arb_solver import ARBSolution
    return ARBSolution(
        front_arb_size="D3-D3",
        front_arb_blade_start=1,
        rear_arb_size="D2-D2",
        rear_arb_blade_start=1,
        lltd_achieved=lltd,
        lltd_target=lltd,
        lltd_error=0.0,
        static_front_weight_dist=0.464,
        k_roll_front_springs=1000.0,
        k_roll_rear_springs=900.0,
        k_roll_front_arb=200.0,
        k_roll_rear_arb=300.0,
        k_roll_front_total=1200.0,
        k_roll_rear_total=1200.0,
        rarb_sensitivity_per_blade=0.008,
        rarb_blade_slow_corner=1,
        rarb_blade_fast_corner=1,
        farb_blade_locked=1,
        lltd_at_rarb_min=0.49,
        lltd_at_rarb_max=0.53,
        constraints=[],
    )


def _make_geom_solution():
    from solver.wheel_geometry_solver import WheelGeometrySolution
    return WheelGeometrySolution(
        front_camber_deg=-3.0,
        rear_camber_deg=-2.0,
        front_toe_mm=0.0,
        rear_toe_mm=0.0,
        peak_lat_g=2.0,
        body_roll_at_peak_deg=1.5,
        front_camber_change_at_peak_deg=0.5,
        rear_camber_change_at_peak_deg=0.4,
        front_dynamic_camber_at_peak_deg=-2.5,
        rear_dynamic_camber_at_peak_deg=-1.6,
        front_camber_delta_from_baseline=0.0,
        rear_camber_delta_from_baseline=0.0,
        front_toe_delta_from_baseline=0.0,
        rear_toe_delta_from_baseline=0.0,
        expected_conditioning_laps_front=2.0,
        expected_conditioning_laps_rear=2.0,
        k_roll_total_nm_deg=2400.0,
        constraints=[],
    )


def _make_gt3_corner_solution():
    """GT3 paired-coil corner spring solution (front_torsion_od_mm=0,
    front_coil_rate_nmm > 0)."""
    from solver.corner_spring_solver import CornerSpringSolution
    return CornerSpringSolution(
        front_torsion_od_mm=0.0,
        front_wheel_rate_nmm=220.0,
        front_natural_freq_hz=2.5,
        front_heave_corner_ratio=0.0,
        front_mass_per_corner_kg=350.0,
        rear_spring_rate_nmm=180.0,
        rear_natural_freq_hz=2.4,
        rear_third_corner_ratio=0.0,
        rear_mass_per_corner_kg=380.0,
        total_front_heave_nmm=440.0,
        total_rear_heave_nmm=360.0,
        front_heave_mode_freq_hz=2.5,
        rear_heave_mode_freq_hz=2.4,
        track_bump_freq_hz=4.0,
        front_freq_isolation_ratio=1.0,
        rear_freq_isolation_ratio=1.0,
        rear_spring_perch_mm=0.0,
        constraints=[],
        front_coil_rate_nmm=220.0,
        rear_motion_ratio=1.0,
    )


def _make_gtp_corner_solution():
    """GTP torsion-bar corner spring solution (front_torsion_od_mm=13.9)."""
    from solver.corner_spring_solver import CornerSpringSolution
    return CornerSpringSolution(
        front_torsion_od_mm=13.9,
        front_wheel_rate_nmm=180.0,
        front_natural_freq_hz=2.5,
        front_heave_corner_ratio=3.0,
        front_mass_per_corner_kg=300.0,
        rear_spring_rate_nmm=160.0,
        rear_natural_freq_hz=2.4,
        rear_third_corner_ratio=2.6,
        rear_mass_per_corner_kg=320.0,
        total_front_heave_nmm=540.0,
        total_rear_heave_nmm=580.0,
        front_heave_mode_freq_hz=2.5,
        rear_heave_mode_freq_hz=2.4,
        track_bump_freq_hz=4.0,
        front_freq_isolation_ratio=1.0,
        rear_freq_isolation_ratio=1.0,
        rear_spring_perch_mm=0.0,
        constraints=[],
        rear_motion_ratio=0.6,
    )


def _make_real_step2():
    return HeaveSolution(
        front_heave_nmm=180.0,
        rear_third_nmm=420.0,
        front_dynamic_rh_mm=15.0,
        front_shock_vel_p99_mps=0.4,
        front_excursion_at_rate_mm=8.0,
        front_bottoming_margin_mm=7.0,
        front_sigma_at_rate_mm=3.5,
        front_binding_constraint="variance",
        rear_dynamic_rh_mm=40.0,
        rear_shock_vel_p99_mps=0.5,
        rear_excursion_at_rate_mm=10.0,
        rear_bottoming_margin_mm=30.0,
        rear_sigma_at_rate_mm=4.5,
        rear_binding_constraint="variance",
        perch_offset_front_mm=2.0,
        perch_offset_rear_mm=-3.0,
        travel_margin_front_mm=5.0,
    )


# ─── F-O-1, F-O-2: evaluate_physics on GT3 must not crash ─────────────────


class TestEvaluatePhysicsGT3:
    """W6.1 F-O-1 + F-O-2: forward physics evaluation handles
    car.heave_spring=None (GT3) without AttributeError, and the excursion
    model substitutes corner-spring rates for the heave / third primary
    spring on GT3."""

    def test_gt3_evaluate_physics_does_not_raise(self):
        obj = ObjectiveFunction(BMW_M4_GT3, _track())
        result = obj.evaluate_physics({
            "front_torsion_od_mm": 0.0,
            "wing_angle_deg": 6.0,
        })
        assert math.isfinite(result.front_excursion_mm)
        assert math.isfinite(result.rear_excursion_mm)
        assert math.isfinite(result.front_sigma_mm)
        assert math.isfinite(result.rear_sigma_mm)

    def test_gt3_evaluate_returns_finite_score(self):
        """The full evaluate() call (incl. all penalty terms) must not
        crash and must return a finite total_score_ms for GT3."""
        obj = ObjectiveFunction(BMW_M4_GT3, _track())
        candidate = {
            "front_torsion_od_mm": 0.0,
            "front_camber_deg": -3.0,
            "rear_camber_deg": -2.0,
            "wing_angle_deg": 6.0,
        }
        ev = obj.evaluate(candidate)
        assert math.isfinite(ev.breakdown.total_score_ms), (
            f"GT3 score is non-finite: {ev.breakdown.total_score_ms}"
        )


# ─── F-O-3: _compute_lltd_fuel_window flat-lines on GT3 ───────────────────


class TestLltdFuelWindowGT3:
    """F-O-3: GT3 corner coils are constant-rate; the fuel-window LLTD
    error must be (0, 0, 0) — no signal, no penalty."""

    def test_gt3_returns_zero_window(self):
        obj = ObjectiveFunction(BMW_M4_GT3, _track())
        err_start, err_end, worst = obj._compute_lltd_fuel_window({})
        assert err_start == 0.0
        assert err_end == 0.0
        assert worst == 0.0

    def test_gtp_still_runs_window(self):
        obj = ObjectiveFunction(BMW_M_HYBRID_V8, _track(name="Sebring", car="bmw"))
        err_start, err_end, worst = obj._compute_lltd_fuel_window({})
        assert math.isfinite(err_start)
        assert math.isfinite(err_end)
        assert math.isfinite(worst)


# ─── F-O-4: _compute_platform_risk on GT3 must not crash ──────────────────


class TestPlatformRiskGT3:
    """F-O-4: heave-spring deflection block must be skipped for GT3
    (car.heave_spring is None)."""

    def test_gt3_compute_platform_risk_no_crash(self):
        obj = ObjectiveFunction(BMW_M4_GT3, _track())
        physics = obj.evaluate_physics({
            "front_torsion_od_mm": 0.0,
            "wing_angle_deg": 6.0,
        })
        veto: list[str] = []
        soft: list[str] = []
        risk = obj._compute_platform_risk(
            {"front_torsion_od_mm": 0.0, "wing_angle_deg": 6.0},
            physics, veto, soft,
        )
        assert math.isfinite(risk.bottoming_risk_ms)
        assert math.isfinite(risk.vortex_risk_ms)
        assert math.isfinite(risk.rh_collapse_risk_ms)
        assert math.isfinite(risk.slider_exhaustion_ms)


# ─── F-O-5: _compute_envelope_penalty on GT3 must not crash ───────────────


class TestEnvelopePenaltyGT3:
    """F-O-5: heave realism / calibration uncertainty penalties must be 0
    for GT3, and the heave/third ratio penalty must not fire on default
    values."""

    def test_gt3_envelope_penalty_no_crash(self):
        obj = ObjectiveFunction(BMW_M4_GT3, _track())
        physics = obj.evaluate_physics({
            "front_torsion_od_mm": 0.0,
            "wing_angle_deg": 6.0,
        })
        soft: list[str] = []
        penalty = obj._compute_envelope_penalty(
            {"front_torsion_od_mm": 0.0, "wing_angle_deg": 6.0},
            physics, soft,
        )
        assert math.isfinite(penalty.setup_distance_ms)

    def test_gt3_heave_realism_penalty_returns_zero(self):
        obj = ObjectiveFunction(BMW_M4_GT3, _track())
        # Even at extreme front-heave values, GT3 must return 0.0
        for k in (10.0, 50.0, 200.0, 1000.0):
            assert obj._heave_realism_penalty_ms(k) == 0.0
        for k in (10.0, 50.0, 200.0):
            assert obj._heave_calibration_uncertainty_penalty_ms(k) == 0.0


# ─── F-S-1: analyze_step2_constraints returns [] on GT3 / null Step 2 ─────


class TestAnalyzeStep2ConstraintsGT3:
    def test_null_step2_returns_empty_list(self):
        null = HeaveSolution.null(front_dynamic_rh_mm=35.0, rear_dynamic_rh_mm=80.0)
        assert null.present is False
        constraints = analyze_step2_constraints(null)
        assert constraints == []

    def test_real_step2_returns_constraints(self):
        sol = _make_real_step2()
        assert sol.present is True
        constraints = analyze_step2_constraints(sol)
        assert len(constraints) >= 4


# ─── F-S-5: build_sensitivity_report on GT3 must not crash ────────────────


class TestBuildSensitivityReportGT3:
    """W6.1 F-S-3 / F-S-4 / F-S-5: GT3 step2 is null and GT3 has
    car.heave_spring=None — building the sensitivity report must NOT
    raise and must NOT include heave-spring sensitivity / confidence
    rows."""

    def test_gt3_build_report_no_crash(self):
        rake = _make_rake_solution()
        null_step2 = HeaveSolution.null(
            front_dynamic_rh_mm=35.0, rear_dynamic_rh_mm=80.0,
        )
        report = build_sensitivity_report(
            step1=rake,
            step2=null_step2,
            arb_lltd=0.51,
            arb_lltd_target=0.51,
            rarb_sensitivity=0.008,
            car=BMW_M4_GT3,
            target_df_balance_pct=44.0,
        )

        # No heave/third spring sensitivity entries on GT3 / null Step 2
        for s in report.sensitivities:
            assert "heave" not in s.input_name.lower(), (
                f"GT3 should not emit heave sensitivity rows, got {s.input_name}"
            )
            assert "third" not in s.input_name.lower(), (
                f"GT3 should not emit third-spring sensitivity rows, got {s.input_name}"
            )
        # No heave/excursion confidence bands either
        for cb in report.confidence_bands:
            assert "heave" not in cb.parameter.lower()
            assert "excursion" not in cb.parameter.lower()


# ─── F-LT-1, F-LT-2: heave/third/perch sensitivity returns None on GT3 ────


class TestLaptimeSensitivityHeaveGuards:
    """The four heave-axis sensitivity functions must return None when
    given a null Step-2 (so the master aggregator can filter them
    out)."""

    def test_front_heave_sensitivity_returns_none_on_null_step2(self):
        from solver.laptime_sensitivity import _front_heave_sensitivity
        null = HeaveSolution.null()
        assert _front_heave_sensitivity(null, _track()) is None

    def test_rear_third_sensitivity_returns_none_on_null_step2(self):
        from solver.laptime_sensitivity import _rear_third_sensitivity
        null = HeaveSolution.null()
        assert _rear_third_sensitivity(null, _track()) is None

    def test_heave_perch_sensitivity_returns_none_on_null_step2(self):
        from solver.laptime_sensitivity import _heave_perch_sensitivity
        null = HeaveSolution.null()
        assert _heave_perch_sensitivity(null, _track()) is None

    def test_rear_third_perch_sensitivity_returns_none_on_null_step2(self):
        from solver.laptime_sensitivity import _rear_third_perch_sensitivity
        null = HeaveSolution.null()
        assert _rear_third_perch_sensitivity(null, _track()) is None


class TestComputeLaptimeSensitivityGT3:
    """compute_laptime_sensitivity must not include any heave/third/perch
    rows for GT3 (where step2 is null)."""

    def test_gt3_no_heave_or_third_rows(self):
        from solver.laptime_sensitivity import compute_laptime_sensitivity
        report = compute_laptime_sensitivity(
            track=_track(),
            step1=_make_rake_solution(),
            step2=HeaveSolution.null(
                front_dynamic_rh_mm=35.0, rear_dynamic_rh_mm=80.0,
            ),
            step3=_make_gt3_corner_solution(),
            step4=_make_arb_solution(),
            step5=_make_geom_solution(),
            brake_bias_pct=52.0,
        )

        names = [s.parameter for s in report.sensitivities]
        for forbidden in (
            "front_heave_nmm",
            "rear_third_nmm",
            "front_heave_perch_mm",
            "rear_third_perch_mm",
        ):
            assert forbidden not in names, (
                f"GT3 sensitivity table should not include {forbidden}; "
                f"got names={names}"
            )

    def test_gt3_table_is_non_empty(self):
        """Sanity: removing heave entries doesn't leave the GT3 table empty."""
        from solver.laptime_sensitivity import compute_laptime_sensitivity
        report = compute_laptime_sensitivity(
            track=_track(),
            step1=_make_rake_solution(),
            step2=HeaveSolution.null(),
            step3=_make_gt3_corner_solution(),
            step4=_make_arb_solution(),
            step5=_make_geom_solution(),
            brake_bias_pct=52.0,
        )
        assert len(report.sensitivities) >= 8


# ─── GTP regression — heave/third entries must remain on GTP ──────────────


class TestLaptimeSensitivityGTPRegression:
    """For GTP cars with a real (present=True) HeaveSolution, the heave /
    third / perch entries must still be emitted in the sensitivity table.
    """

    def test_gtp_heave_entries_present(self):
        from solver.laptime_sensitivity import compute_laptime_sensitivity
        report = compute_laptime_sensitivity(
            track=_track(name="Sebring", car="bmw"),
            step1=_make_rake_solution(static_f=30.0, static_r=48.0, wing=17.0, mode="full"),
            step2=_make_real_step2(),
            step3=_make_gtp_corner_solution(),
            step4=_make_arb_solution(lltd=0.52),
            step5=_make_geom_solution(),
            brake_bias_pct=56.0,
        )
        names = [s.parameter for s in report.sensitivities]
        # GTP should still include the heave-axis entries
        assert "front_heave_nmm" in names
        assert "rear_third_nmm" in names
        assert "front_heave_perch_mm" in names
        assert "rear_third_perch_mm" in names

    def test_gtp_build_sensitivity_report_includes_heave(self):
        """build_sensitivity_report on a real GTP step2 must emit heave
        sensitivity rows AND confidence bands."""
        rake = _make_rake_solution(static_f=30.0, static_r=48.0, wing=17.0, mode="full")
        real_step2 = _make_real_step2()
        report = build_sensitivity_report(
            step1=rake,
            step2=real_step2,
            arb_lltd=0.52,
            arb_lltd_target=0.52,
            rarb_sensitivity=0.008,
            car=BMW_M_HYBRID_V8,
            target_df_balance_pct=50.14,
        )
        # GTP path must produce heave sensitivity rows
        heave_inputs = [s.input_name for s in report.sensitivities
                        if "heave" in s.input_name.lower()]
        third_inputs = [s.input_name for s in report.sensitivities
                        if "third" in s.input_name.lower()]
        assert len(heave_inputs) >= 1
        assert len(third_inputs) >= 1
        # And heave confidence bands
        heave_bands = [cb.parameter for cb in report.confidence_bands
                       if "heave" in cb.parameter.lower()]
        assert len(heave_bands) >= 1
