"""Tests for the SuspensionArchitecture enum scaffolding.

These tests pin the additive Phase 0 contract for GT3 support:
  - The enum exists with the three intended values.
  - has_heave_third / has_front_torsion_bar properties classify correctly.
  - Existing GTP cars default to GTP_HEAVE_THIRD_TORSION_FRONT.
  - Porsche 963 declares GTP_HEAVE_THIRD_ROLL_FRONT explicitly.
  - __post_init__ invariants reject misconfigurations.
  - BMW M4 GT3 scaffold exists and respects the GT3 invariants.
"""
from __future__ import annotations

import pytest

from car_model.cars import (
    ASTON_MARTIN_VANTAGE_GT3,
    BMW_M_HYBRID_V8,
    BMW_M4_GT3,
    PORSCHE_963,
    PORSCHE_992_GT3R,
    CarModel,
    CornerSpringModel,
    SuspensionArchitecture,
)


class TestSuspensionArchitectureEnum:
    def test_three_values_exist(self):
        assert SuspensionArchitecture.GTP_HEAVE_THIRD_TORSION_FRONT.value == "gtp_heave_third_torsion_front"
        assert SuspensionArchitecture.GTP_HEAVE_THIRD_ROLL_FRONT.value == "gtp_heave_third_roll_front"
        assert SuspensionArchitecture.GT3_COIL_4WHEEL.value == "gt3_coil_4wheel"

    def test_has_heave_third_true_for_gtp(self):
        assert SuspensionArchitecture.GTP_HEAVE_THIRD_TORSION_FRONT.has_heave_third is True
        assert SuspensionArchitecture.GTP_HEAVE_THIRD_ROLL_FRONT.has_heave_third is True

    def test_has_heave_third_false_for_gt3(self):
        assert SuspensionArchitecture.GT3_COIL_4WHEEL.has_heave_third is False

    def test_has_front_torsion_bar_only_torsion_variant(self):
        assert SuspensionArchitecture.GTP_HEAVE_THIRD_TORSION_FRONT.has_front_torsion_bar is True
        assert SuspensionArchitecture.GTP_HEAVE_THIRD_ROLL_FRONT.has_front_torsion_bar is False
        assert SuspensionArchitecture.GT3_COIL_4WHEEL.has_front_torsion_bar is False


class TestExistingCarDefaults:
    def test_bmw_defaults_to_torsion_front(self):
        assert BMW_M_HYBRID_V8.suspension_arch is SuspensionArchitecture.GTP_HEAVE_THIRD_TORSION_FRONT

    def test_porsche_declares_roll_front(self):
        assert PORSCHE_963.suspension_arch is SuspensionArchitecture.GTP_HEAVE_THIRD_ROLL_FRONT


class TestInvariants:
    def test_gt3_arch_rejects_front_torsion_c(self):
        with pytest.raises(ValueError, match="GT3_COIL_4WHEEL"):
            CarModel(
                name="Bad GT3",
                canonical_name="bad_gt3",
                mass_car_kg=1300.0,
                suspension_arch=SuspensionArchitecture.GT3_COIL_4WHEEL,
                heave_spring=None,
                corner_spring=CornerSpringModel(
                    front_torsion_c=0.0008036,
                    front_torsion_od_ref_mm=13.9,
                ),
            )

    def test_gt3_arch_accepts_zero_front_torsion_c(self):
        car = CarModel(
            name="OK GT3 stub",
            canonical_name="ok_gt3_stub",
            mass_car_kg=1300.0,
            suspension_arch=SuspensionArchitecture.GT3_COIL_4WHEEL,
            heave_spring=None,
            corner_spring=CornerSpringModel(
                front_torsion_c=0.0,
                front_torsion_od_ref_mm=0.0,
                front_torsion_od_options=[],
            ),
        )
        assert car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL

    def test_gt3_arch_rejects_non_null_heave_spring(self):
        """GT3 cars MUST set heave_spring=None — forgetting to is silently
        wrong (factory default builds a non-null heave model). Catch at
        construction."""
        with pytest.raises(ValueError, match="heave_spring=None"):
            CarModel(
                name="Bad GT3 default heave",
                canonical_name="bad_gt3_default_heave",
                mass_car_kg=1300.0,
                suspension_arch=SuspensionArchitecture.GT3_COIL_4WHEEL,
                # heave_spring not set → factory default builds a non-null one
                corner_spring=CornerSpringModel(
                    front_torsion_c=0.0,
                    front_torsion_od_ref_mm=0.0,
                    front_torsion_od_options=[],
                ),
            )

    def test_gtp_arch_rejects_null_heave_spring(self):
        """Inverse invariant: GTP cars must NOT set heave_spring=None."""
        with pytest.raises(ValueError, match="requires non-null heave_spring"):
            CarModel(
                name="Bad GTP no heave",
                canonical_name="bad_gtp_no_heave",
                mass_car_kg=1030.0,
                suspension_arch=SuspensionArchitecture.GTP_HEAVE_THIRD_TORSION_FRONT,
                heave_spring=None,
            )


class TestBMWM4GT3:
    def test_canonical_name_and_arch(self):
        assert BMW_M4_GT3.canonical_name == "bmw_m4_gt3"
        assert BMW_M4_GT3.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL

    def test_heave_spring_is_none(self):
        assert BMW_M4_GT3.heave_spring is None

    def test_front_torsion_disabled(self):
        assert BMW_M4_GT3.corner_spring.front_torsion_c == 0.0
        assert BMW_M4_GT3.corner_spring.front_torsion_od_options == []

    def test_no_roll_dampers(self):
        d = BMW_M4_GT3.damper
        assert d.has_roll_dampers is False
        assert d.has_front_roll_damper is False
        assert d.has_rear_roll_damper is False

    def test_bop_version_set(self):
        assert BMW_M4_GT3.bop_version == "2026s2_p3"

    def test_arb_blade_counts_match_manual(self):
        """Manual: 11 front configs (D1-D1..D6-D6), 7 rear (D1-D1..D4-D4)."""
        assert len(BMW_M4_GT3.arb.front_size_labels) == 11
        assert len(BMW_M4_GT3.arb.rear_size_labels) == 7

    def test_rear_spring_range_from_manual(self):
        """Manual V3: rear spring 130-250 N/mm, 10 N/mm step."""
        lo, hi = BMW_M4_GT3.corner_spring.rear_spring_range_nmm
        assert lo == 130.0
        assert hi == 250.0

    def test_wing_angles_match_aero_map(self):
        """Wing angles -2..+6 (9 angles) parsed from data/aeromaps_parsed/bmw_m4_gt3_aero.npz."""
        assert BMW_M4_GT3.wing_angles == [-2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]

    def test_registry_lookup(self):
        from car_model.cars import get_car
        car = get_car("bmw_m4_gt3", apply_calibration=False)
        assert car.canonical_name == "bmw_m4_gt3"
        assert car.heave_spring is None

    def test_iracing_car_path_set(self):
        """IBT DriverInfo.CarPath = 'bmwm4gt3' — needed for IBT auto-detection."""
        assert BMW_M4_GT3.iracing_car_path == "bmwm4gt3"

    def test_ibt_verified_constants(self):
        """Values pulled from a real IBT session_info dump (Spielberg 2026-04-26).
        These are GROUND TRUTH from iRacing, not manual-derived estimates."""
        assert BMW_M4_GT3.weight_dist_front == pytest.approx(0.464)
        assert BMW_M4_GT3.fuel_capacity_l == pytest.approx(100.0)
        assert BMW_M4_GT3.brake_bias_pct == pytest.approx(52.0)
        assert BMW_M4_GT3.default_diff_preload_nm == pytest.approx(100.0)


class TestAstonMartinVantageGT3:
    def test_canonical_name_and_arch(self):
        assert ASTON_MARTIN_VANTAGE_GT3.canonical_name == "aston_martin_vantage_gt3"
        assert ASTON_MARTIN_VANTAGE_GT3.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL

    def test_iracing_car_path(self):
        assert ASTON_MARTIN_VANTAGE_GT3.iracing_car_path == "amvantageevogt3"

    def test_heave_spring_is_none(self):
        assert ASTON_MARTIN_VANTAGE_GT3.heave_spring is None

    def test_no_roll_dampers(self):
        d = ASTON_MARTIN_VANTAGE_GT3.damper
        assert d.has_roll_dampers is False
        assert d.has_front_roll_damper is False
        assert d.has_rear_roll_damper is False

    def test_ibt_verified_constants(self):
        """Values from Spielberg IBT session_info 2026-04-26."""
        assert ASTON_MARTIN_VANTAGE_GT3.weight_dist_front == pytest.approx(0.480)
        assert ASTON_MARTIN_VANTAGE_GT3.fuel_capacity_l == pytest.approx(106.0)
        assert ASTON_MARTIN_VANTAGE_GT3.brake_bias_pct == pytest.approx(55.8)
        assert ASTON_MARTIN_VANTAGE_GT3.default_diff_preload_nm == pytest.approx(110.0)

    def test_wing_angles_match_aero_map(self):
        """Wing angles 5..13 (9 angles) parsed from aston_martin_vantage_gt3_aero.npz."""
        assert ASTON_MARTIN_VANTAGE_GT3.wing_angles == [5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0]

    def test_registry_lookup(self):
        from car_model.cars import get_car
        car = get_car("aston_martin_vantage_gt3", apply_calibration=False)
        assert car.iracing_car_path == "amvantageevogt3"
        assert car.heave_spring is None


class TestPorsche992GT3R:
    """The only RR-layout GT3 — critical for de-risking the LLTD physics path."""

    def test_canonical_name_and_arch(self):
        assert PORSCHE_992_GT3R.canonical_name == "porsche_992_gt3r"
        assert PORSCHE_992_GT3R.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL

    def test_iracing_car_path(self):
        assert PORSCHE_992_GT3R.iracing_car_path == "porsche992rgt3"

    def test_heave_spring_is_none(self):
        assert PORSCHE_992_GT3R.heave_spring is None

    def test_no_roll_dampers(self):
        d = PORSCHE_992_GT3R.damper
        assert d.has_roll_dampers is False
        assert d.has_front_roll_damper is False
        assert d.has_rear_roll_damper is False

    def test_rr_weight_distribution(self):
        """The defining RR-layout signature — front weight fraction <0.46.
        BMW 0.464, Aston 0.480, Porsche 0.449 — clearly the lightest front."""
        assert PORSCHE_992_GT3R.weight_dist_front < 0.46
        assert PORSCHE_992_GT3R.weight_dist_front == pytest.approx(0.449)
        assert PORSCHE_992_GT3R.weight_dist_front < BMW_M4_GT3.weight_dist_front
        assert PORSCHE_992_GT3R.weight_dist_front < ASTON_MARTIN_VANTAGE_GT3.weight_dist_front

    def test_ibt_verified_constants(self):
        assert PORSCHE_992_GT3R.fuel_capacity_l == pytest.approx(100.0)
        assert PORSCHE_992_GT3R.brake_bias_pct == pytest.approx(51.7)
        assert PORSCHE_992_GT3R.default_diff_preload_nm == pytest.approx(110.0)

    def test_wing_angles_with_offset(self):
        """Porsche uses 0.7-degree offsets (5.7, 6.7, ..., 12.7) — distinct
        from BMW (-2..6 integer) and Aston (5..13 integer)."""
        assert PORSCHE_992_GT3R.wing_angles == [5.7, 6.7, 7.7, 8.7, 9.7, 10.7, 11.7, 12.7]
        assert all(round(w - int(w), 1) == 0.7 for w in PORSCHE_992_GT3R.wing_angles)

    def test_registry_lookup(self):
        from car_model.cars import get_car
        car = get_car("porsche_992_gt3r", apply_calibration=False)
        assert car.iracing_car_path == "porsche992rgt3"
        assert car.heave_spring is None
        assert car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL


class TestNullHeaveSolution:
    def test_null_factory_returns_present_false(self):
        from solver.heave_solver import HeaveSolution
        h = HeaveSolution.null()
        assert h.present is False
        assert h.front_heave_nmm == 0.0
        assert h.rear_third_nmm == 0.0
        assert h.front_binding_constraint == "not_applicable"
        assert h.rear_binding_constraint == "not_applicable"

    def test_real_solution_defaults_present_true(self):
        """Sanity check: existing call sites that don't set `present` get the
        default True, preserving the implicit contract for GTP cars."""
        from solver.heave_solver import HeaveSolution
        h = HeaveSolution(
            front_heave_nmm=180.0,
            rear_third_nmm=400.0,
            front_dynamic_rh_mm=30.0,
            front_shock_vel_p99_mps=0.5,
            front_excursion_at_rate_mm=4.0,
            front_bottoming_margin_mm=2.0,
            front_sigma_at_rate_mm=1.0,
            front_binding_constraint="bottoming",
            rear_dynamic_rh_mm=50.0,
            rear_shock_vel_p99_mps=0.6,
            rear_excursion_at_rate_mm=5.0,
            rear_bottoming_margin_mm=3.0,
            rear_sigma_at_rate_mm=1.2,
            rear_binding_constraint="bottoming",
            perch_offset_front_mm=-13.0,
            perch_offset_rear_mm=42.0,
        )
        assert h.present is True


class TestCalibrationGateNotApplicable:
    def test_subsystem_status_includes_not_applicable(self):
        from car_model.calibration_gate import SubsystemCalibration
        sub = SubsystemCalibration(name="heave_third", status="not_applicable")
        assert sub.status == "not_applicable"

    def test_step_report_not_applicable_zero_confidence(self):
        from car_model.calibration_gate import StepCalibrationReport
        r = StepCalibrationReport(
            step_number=2,
            step_name="Heave/Third Springs",
            not_applicable=True,
        )
        assert r.confidence_weight == 0.0
        assert r.blocked is False  # N/A is distinct from blocked
        assert r.not_applicable is True


# ─── End-to-end CalibrationGate dispatch on GT3 vs GTP ─────────────────────

# Track names used for the dispatch tests. These cars do not yet have any
# calibrated track, so we pass an arbitrary string — the gate's Step 2
# dispatch is purely architectural and does not consult the track.
_GT3_TRACK = "spielberg"
_GTP_TRACK = "sebring"


@pytest.fixture(params=[
    "bmw_m4_gt3",
    "aston_martin_vantage_gt3",
    "porsche_992_gt3r",
])
def gt3_car(request):
    """Each GT3 stub car, looked up via the canonical registry."""
    from car_model.cars import get_car
    return get_car(request.param, apply_calibration=False)


class TestGateDispatchStep2NotApplicable:
    """F1: gate.check_step(2) emits not_applicable=True for every GT3 car."""

    def test_gt3_step2_not_applicable(self, gt3_car):
        from car_model.calibration_gate import CalibrationGate
        gate = CalibrationGate(gt3_car, _GT3_TRACK)
        report = gate.check_step(2)
        assert report.not_applicable is True
        assert report.blocked is False  # N/A is distinct from blocked
        assert report.confidence_weight == 0.0
        assert report.step_number == 2

    def test_gt3_step2_step_name_set(self, gt3_car):
        """Even when N/A, the step name must be populated for display."""
        from car_model.calibration_gate import CalibrationGate
        gate = CalibrationGate(gt3_car, _GT3_TRACK)
        report = gate.check_step(2)
        assert "Heave" in report.step_name

    def test_gt3_step2_instructions_is_na_oneliner(self, gt3_car):
        """F8: instructions_text should be a clean one-liner for N/A steps,
        NOT 'TO CALIBRATE SPRING RATES'."""
        from car_model.calibration_gate import CalibrationGate
        gate = CalibrationGate(gt3_car, _GT3_TRACK)
        text = gate.check_step(2).instructions_text()
        assert "N/A" in text
        assert "architecture skip" in text
        assert "TO CALIBRATE" not in text  # no misleading remediation

    def test_gtp_bmw_step2_not_na(self):
        """Counter-test: GTP cars must STILL go through the standard path."""
        from car_model.calibration_gate import CalibrationGate
        gate = CalibrationGate(BMW_M_HYBRID_V8, _GTP_TRACK)
        report = gate.check_step(2)
        assert report.not_applicable is False


class TestGateCascadeRulesPerArchitecture:
    """F2: _data_prior_step is GTP {2:1, 3:2, 4:3, 5:4, 6:3} vs
    GT3 {3:1, 4:3, 5:4, 6:3} (Step 2 dropped, Step 3 cascades from Step 1)."""

    def test_gtp_cascade_step3_from_step2(self):
        from car_model.calibration_gate import CalibrationGate
        gate = CalibrationGate(BMW_M_HYBRID_V8, _GTP_TRACK)
        chain = gate._data_prior_step
        assert chain[3] == 2
        assert chain == {2: 1, 3: 2, 4: 3, 5: 4, 6: 3}

    def test_gt3_cascade_step3_from_step1(self, gt3_car):
        from car_model.calibration_gate import CalibrationGate
        gate = CalibrationGate(gt3_car, _GT3_TRACK)
        chain = gate._data_prior_step
        assert chain[3] == 1
        assert 2 not in chain  # Step 2 has no upstream because it doesn't exist
        assert chain == {3: 1, 4: 3, 5: 4, 6: 3}

    def test_gt3_step3_does_not_cascade_from_na_step2(self, gt3_car):
        """Even though Step 2 is N/A, Step 3 must NOT inherit weak_upstream
        or be dependency_blocked because of it. Step 3 cascades from Step 1."""
        from car_model.calibration_gate import CalibrationGate
        gate = CalibrationGate(gt3_car, _GT3_TRACK)
        s2 = gate.check_step(2)
        s3 = gate.check_step(3)
        assert s2.not_applicable is True
        # Step 3 may or may not be blocked depending on its OWN data, but if
        # blocked it must be by Step 1, not Step 2.
        if s3.dependency_blocked:
            assert s3.blocked_by_step != 2
        # weak_upstream must not point at the N/A step
        assert s3.weak_upstream_step != 2


class TestGateReportPropertiesGT3:
    """F4 / F5 / F6 / F7: report properties and formatters honour N/A."""

    def test_not_applicable_steps_lists_step2(self, gt3_car):
        from car_model.calibration_gate import CalibrationGate
        gate = CalibrationGate(gt3_car, _GT3_TRACK)
        report = gate.full_report()
        assert report.not_applicable_steps == [2]

    def test_solved_steps_excludes_na_step(self, gt3_car):
        """solved_steps must NOT include Step 2 even though blocked=False."""
        from car_model.calibration_gate import CalibrationGate
        gate = CalibrationGate(gt3_car, _GT3_TRACK)
        report = gate.full_report()
        assert 2 not in report.solved_steps

    def test_format_header_renders_na_section(self, gt3_car):
        from car_model.calibration_gate import CalibrationGate
        gate = CalibrationGate(gt3_car, _GT3_TRACK)
        header = gate.full_report().format_header()
        assert "NOT APPLICABLE STEPS" in header
        assert "[--] Step 2" in header

    def test_summary_line_mentions_not_applicable_when_no_blocks(self, monkeypatch, gt3_car):
        """When blocked == 0 and na > 0, summary_line must say 'not applicable'.

        The GT3 stub cars currently have several uncalibrated subsystems, so
        we monkeypatch step_is_runnable / blocked_steps via a stubbed
        full_report to exercise the blocked==0 branch deterministically.
        """
        from car_model.calibration_gate import (
            CalibrationGate,
            CalibrationReport,
            StepCalibrationReport,
        )

        gate = CalibrationGate(gt3_car, _GT3_TRACK)
        fake_report = CalibrationReport(
            car_name=gt3_car.name,
            track_name=_GT3_TRACK,
            step_reports=[
                StepCalibrationReport(step_number=1, step_name="Rake"),
                StepCalibrationReport(
                    step_number=2,
                    step_name="Heave / Third Springs",
                    not_applicable=True,
                ),
                StepCalibrationReport(step_number=3, step_name="Corner Springs"),
                StepCalibrationReport(step_number=4, step_name="ARBs"),
                StepCalibrationReport(step_number=5, step_name="Wheel Geometry"),
                StepCalibrationReport(step_number=6, step_name="Dampers"),
            ],
        )
        monkeypatch.setattr(gate, "full_report", lambda: fake_report)
        line = gate.summary_line()
        assert "not applicable" in line
        # And it must report applicable=5 not 6
        assert "/5" in line


class TestGateGTPRegression:
    """Counter-tests: GTP cars (BMW M Hybrid V8) MUST behave exactly as before."""

    def test_gtp_step2_goes_through_standard_path(self):
        from car_model.calibration_gate import CalibrationGate
        gate = CalibrationGate(BMW_M_HYBRID_V8, _GTP_TRACK)
        report = gate.check_step(2)
        assert report.not_applicable is False
        # spring_rates is calibrated for BMW → should not be blocked
        # (subject to its own subsystem state, but never NA).

    def test_gtp_full_report_has_no_na_steps(self):
        from car_model.calibration_gate import CalibrationGate
        gate = CalibrationGate(BMW_M_HYBRID_V8, _GTP_TRACK)
        report = gate.full_report()
        assert report.not_applicable_steps == []


# ─── W1.2: downstream consumers honour HeaveSolution.null() (present=False) ──


class TestSolverStepsToParamsHonoursPresentFlag:
    """PU1: solver_steps_to_params must skip step2 fields for null heave."""

    def test_null_heave_omits_heave_keys_from_params(self):
        from solver.heave_solver import HeaveSolution
        from solver.params_util import solver_steps_to_params

        null_heave = HeaveSolution.null(
            front_dynamic_rh_mm=12.0,
            rear_dynamic_rh_mm=45.0,
        )
        params = solver_steps_to_params(
            step1=None,
            step2=null_heave,
            step3=None,
        )
        assert "front_heave_spring_nmm" not in params
        assert "rear_third_spring_nmm" not in params

    def test_real_heave_writes_keys_to_params(self):
        """Counter-test: GTP-style HeaveSolution (present=True) still writes."""
        from solver.heave_solver import HeaveSolution
        from solver.params_util import solver_steps_to_params

        real = HeaveSolution(
            front_heave_nmm=180.0,
            rear_third_nmm=400.0,
            front_dynamic_rh_mm=30.0,
            front_shock_vel_p99_mps=0.5,
            front_excursion_at_rate_mm=4.0,
            front_bottoming_margin_mm=2.0,
            front_sigma_at_rate_mm=1.0,
            front_binding_constraint="bottoming",
            rear_dynamic_rh_mm=50.0,
            rear_shock_vel_p99_mps=0.6,
            rear_excursion_at_rate_mm=5.0,
            rear_bottoming_margin_mm=3.0,
            rear_sigma_at_rate_mm=1.2,
            rear_binding_constraint="bottoming",
            perch_offset_front_mm=-13.0,
            perch_offset_rear_mm=42.0,
        )
        params = solver_steps_to_params(step1=None, step2=real, step3=None)
        assert params["front_heave_spring_nmm"] == 180.0
        assert params["rear_third_spring_nmm"] == 400.0

    def test_legacy_object_without_present_attr_treated_as_present(self):
        """Backwards-compat: pre-Phase-0 mocks/objects with no `.present`
        attribute must keep the legacy GTP behaviour (default True)."""

        class LegacyHeave:
            front_heave_nmm = 200.0
            rear_third_nmm = 500.0

        from solver.params_util import solver_steps_to_params

        params = solver_steps_to_params(step1=None, step2=LegacyHeave(), step3=None)
        assert params["front_heave_spring_nmm"] == 200.0
        assert params["rear_third_spring_nmm"] == 500.0

    def test_step3_front_torsion_skipped_for_gt3_car(self):
        """PU2: when `car` is GT3, step3.front_torsion_od_mm must NOT be
        written — GT3 has no front torsion bar."""
        from solver.params_util import solver_steps_to_params

        class FakeStep3:
            front_torsion_od_mm = 0.0
            rear_spring_rate_nmm = 175.0

        params = solver_steps_to_params(
            step1=None, step2=None, step3=FakeStep3(), car=BMW_M4_GT3,
        )
        assert "front_torsion_od_mm" not in params
        assert params["rear_spring_rate_nmm"] == 175.0

    def test_step3_front_torsion_written_for_gtp_car(self):
        """Counter-test: GTP cars (with torsion bar arch) still write OD."""
        from solver.params_util import solver_steps_to_params

        class FakeStep3:
            front_torsion_od_mm = 14.5
            rear_spring_rate_nmm = 170.0

        params = solver_steps_to_params(
            step1=None, step2=None, step3=FakeStep3(), car=BMW_M_HYBRID_V8,
        )
        assert params["front_torsion_od_mm"] == 14.5
        assert params["rear_spring_rate_nmm"] == 170.0


class TestExtractTargetMapsHonoursPresentFlag:
    """CS3: candidate_search _extract_target_maps must emit empty dict for
    HeaveSolution.null()."""

    def _stub_chain_result(self, step2_value):
        """Build a minimal SolveChainResult-like stub with only step2 / supporting."""
        class StubSupporting:
            brake_bias_pct = 52.0
            brake_bias_target = 0.0
            brake_bias_migration = 0.0
            front_master_cyl_mm = 0.0
            rear_master_cyl_mm = 0.0
            pad_compound = ""
            diff_preload_nm = 100.0
            tc_gain = 1.0
            tc_slip = 4.0
            diff_clutch_plates = 6
            diff_ramp_option_idx = 1
            diff_ramp_angles = ""
            fuel_l = 80.0
            fuel_low_warning_l = 5.0
            fuel_target_l = 80.0
            gear_stack = ""
            roof_light_color = ""

        class StubChainResult:
            step1 = None
            step2 = step2_value
            step3 = None
            step4 = None
            step5 = None
            step6 = None
            supporting = StubSupporting()

        return StubChainResult()

    def test_null_heave_yields_empty_step2_dict(self):
        from solver.candidate_search import _extract_target_maps
        from solver.heave_solver import HeaveSolution

        null_heave = HeaveSolution.null(
            front_dynamic_rh_mm=12.0,
            rear_dynamic_rh_mm=45.0,
        )
        targets = _extract_target_maps(self._stub_chain_result(null_heave), car=BMW_M4_GT3)
        assert targets["step2"] == {}

    def test_real_heave_populates_step2_dict(self):
        """Counter-test: a present=True HeaveSolution still produces a populated
        target dict (the legacy code path)."""
        from solver.candidate_search import _extract_target_maps
        from solver.heave_solver import HeaveSolution

        real = HeaveSolution(
            front_heave_nmm=180.0,
            rear_third_nmm=400.0,
            front_dynamic_rh_mm=30.0,
            front_shock_vel_p99_mps=0.5,
            front_excursion_at_rate_mm=4.0,
            front_bottoming_margin_mm=2.0,
            front_sigma_at_rate_mm=1.0,
            front_binding_constraint="bottoming",
            rear_dynamic_rh_mm=50.0,
            rear_shock_vel_p99_mps=0.6,
            rear_excursion_at_rate_mm=5.0,
            rear_bottoming_margin_mm=3.0,
            rear_sigma_at_rate_mm=1.2,
            rear_binding_constraint="bottoming",
            perch_offset_front_mm=-13.0,
            perch_offset_rear_mm=42.0,
        )
        targets = _extract_target_maps(self._stub_chain_result(real), car=BMW_M_HYBRID_V8)
        s2 = targets["step2"]
        assert s2 != {}
        # public_output_value passes BMW values straight through (no indexing).
        assert s2["front_heave_nmm"] == 180.0
        assert s2["rear_third_nmm"] == 400.0
        assert s2["perch_offset_front_mm"] == -13.0
        assert s2["perch_offset_rear_mm"] == 42.0


class TestHeaveSolverArchitectureGuard:
    """Defense-in-depth: HeaveSolver must refuse to construct on GT3 cars."""

    def _track(self):
        from track_model.profile import TrackProfile
        return TrackProfile(
            track_name="GuardTest",
            track_config="x",
            track_length_m=4000.0,
            car="bmw",
            best_lap_time_s=90.0,
        )

    def test_raises_on_gt3_car(self):
        from solver.heave_solver import HeaveSolver

        with pytest.raises(ValueError, match="has_heave_third"):
            HeaveSolver(BMW_M4_GT3, self._track())

    def test_raises_on_aston_gt3_car(self):
        from solver.heave_solver import HeaveSolver

        with pytest.raises(ValueError, match="GT3_COIL_4WHEEL"):
            HeaveSolver(ASTON_MARTIN_VANTAGE_GT3, self._track())

    def test_raises_on_porsche_gt3_car(self):
        from solver.heave_solver import HeaveSolver

        with pytest.raises(ValueError):
            HeaveSolver(PORSCHE_992_GT3R, self._track())

    def test_does_not_raise_on_gtp_car(self):
        """Counter-test: BMW M Hybrid V8 (GTP) must construct without error."""
        from solver.heave_solver import HeaveSolver

        solver = HeaveSolver(BMW_M_HYBRID_V8, self._track())
        assert solver.car is BMW_M_HYBRID_V8

    def test_does_not_raise_on_porsche_963_gtp(self):
        """Counter-test: Porsche 963 (GTP_HEAVE_THIRD_ROLL_FRONT) must
        construct — has_heave_third is True for both GTP architectures."""
        from solver.heave_solver import HeaveSolver

        solver = HeaveSolver(PORSCHE_963, self._track())
        assert solver.car is PORSCHE_963
