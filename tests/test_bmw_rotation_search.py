import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aero_model import load_car_surfaces
from car_model.cars import get_car
from solver.bmw_coverage import build_parameter_coverage
from solver.bmw_rotation_search import preserve_candidate_rotation_controls, search_rotation_controls
from solver.solve_chain import SolveChainInputs, SolveChainResult
from track_model.profile import TrackProfile


def _track() -> TrackProfile:
    return TrackProfile(
        track_name="Sebring International Raceway",
        track_config="International",
        track_length_m=6000.0,
        car="bmw",
        best_lap_time_s=109.0,
        speed_bands_kph={},
        shock_vel_p95_front_mps=0.18,
        shock_vel_p99_front_mps=0.22,
        shock_vel_p95_rear_mps=0.20,
        shock_vel_p99_rear_mps=0.24,
        shock_vel_p95_front_clean_mps=0.16,
        shock_vel_p99_front_clean_mps=0.20,
        shock_vel_p95_rear_clean_mps=0.18,
        shock_vel_p99_rear_clean_mps=0.22,
        peak_lat_g=2.1,
        lateral_g={"p95": 1.95},
        body_roll_deg={"p95": 1.6},
        roll_gradient_deg_per_g=0.72,
        lltd_measured=0.55,
        ride_heights_mm={},
        surface_profile={},
        telemetry_source="test",
    )


def _setup(**overrides):
    values = {
        "front_pushrod_mm": -26.5,
        "rear_pushrod_mm": -24.0,
        "front_heave_nmm": 50.0,
        "front_heave_perch_mm": -11.0,
        "rear_third_nmm": 520.0,
        "rear_third_perch_mm": 42.5,
        "front_torsion_od_mm": 13.9,
        "rear_spring_nmm": 160.0,
        "rear_spring_perch_mm": 30.0,
        "front_arb_size": "Soft",
        "front_arb_blade": 1,
        "rear_arb_size": "Medium",
        "rear_arb_blade": 3,
        "front_camber_deg": -2.1,
        "rear_camber_deg": -1.8,
        "front_toe_mm": -0.4,
        "rear_toe_mm": 0.3,
        "front_ls_comp": 6,
        "front_ls_rbd": 7,
        "front_hs_comp": 5,
        "front_hs_rbd": 8,
        "front_hs_slope": 10,
        "rear_ls_comp": 6,
        "rear_ls_rbd": 7,
        "rear_hs_comp": 5,
        "rear_hs_rbd": 8,
        "rear_hs_slope": 10,
        "brake_bias_pct": 46.0,
        "brake_bias_target": 0.0,
        "brake_bias_migration": -0.5,
        "front_master_cyl_mm": 19.1,
        "rear_master_cyl_mm": 20.6,
        "pad_compound": "Medium",
        "diff_preload_nm": 30.0,
        "diff_ramp_angles": "45/70",
        "diff_clutch_plates": 6,
        "tc_gain": 4,
        "tc_slip": 3,
        "fuel_l": 89.0,
        "fuel_target_l": 89.0,
        "fuel_low_warning_l": 8.0,
        "gear_stack": "Short",
        "roof_light_color": "Orange",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _measured(**overrides):
    values = {
        "front_heave_travel_used_pct": 84.0,
        "front_rh_excursion_measured_mm": 10.0,
        "rear_rh_std_mm": 7.0,
        "front_rh_std_mm": 4.5,
        "pitch_range_braking_deg": 1.1,
        "front_braking_lock_ratio_p95": 0.06,
        "rear_power_slip_ratio_p95": 0.06,
        "rear_slip_ratio_p95": 0.06,
        "body_slip_p95_deg": 2.8,
        "understeer_low_speed_deg": 0.9,
        "understeer_high_speed_deg": 1.1,
        "understeer_mean_deg": 1.0,
        "front_pressure_mean_kpa": 166.0,
        "rear_pressure_mean_kpa": 166.0,
        "front_carcass_mean_c": 93.0,
        "rear_carcass_mean_c": 92.0,
        "bottoming_event_count_front_clean": 0,
        "bottoming_event_count_rear_clean": 0,
        "yaw_rate_correlation": 0.95,
        "lf_shock_vel_p95_mps": 0.10,
        "rf_shock_vel_p95_mps": 0.10,
        "lr_shock_vel_p95_mps": 0.11,
        "rr_shock_vel_p95_mps": 0.11,
        "fuel_level_at_measurement_l": 58.0,
        "fuel_used_per_lap_l": 2.1,
        "abs_active_pct": 6.0,
        "hydraulic_brake_split_pct": 46.0,
        "telemetry_signals": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _corner(exit_loss_s: float, throttle_delay_s: float, understeer_mean_deg: float, exit_speed_kph: float = 180.0):
    return SimpleNamespace(
        corner_id=17,
        exit_phase_s=1.4,
        throttle_delay_s=throttle_delay_s,
        exit_loss_s=exit_loss_s,
        understeer_mean_deg=understeer_mean_deg,
        exit_speed_kph=exit_speed_kph,
    )


def _supporting(**overrides):
    values = {
        "brake_bias_pct": 46.0,
        "brake_bias_target": 0.0,
        "brake_bias_migration": -0.5,
        "front_master_cyl_mm": 19.1,
        "rear_master_cyl_mm": 20.6,
        "pad_compound": "Medium",
        "diff_preload_nm": 30.0,
        "diff_ramp_option_idx": 1,
        "diff_ramp_angles": "45/70",
        "diff_ramp_coast": 45,
        "diff_ramp_drive": 70,
        "diff_clutch_plates": 6,
        "tc_gain": 4,
        "tc_slip": 3,
        "fuel_l": 58.0,
        "fuel_low_warning_l": 5.0,
        "fuel_target_l": 58.0,
        "gear_stack": "Short",
        "roof_light_color": "Orange",
        "parameter_search_status": {},
        "parameter_search_evidence": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _prediction_from_steps(step2, step3, step4):
    return SimpleNamespace(
        front_heave_travel_used_pct=max(0.0, 100.0 - 0.15 * step2.front_heave_nmm),
        front_excursion_mm=max(0.0, 20.0 - 0.03 * step2.front_heave_nmm),
        rear_rh_std_mm=max(0.0, 12.0 - 0.008 * step2.rear_third_nmm),
        braking_pitch_deg=max(0.0, 2.0 - 0.01 * step2.front_heave_nmm),
        front_lock_p95=max(0.0, 0.12 - 0.0006 * step2.front_heave_nmm),
        rear_power_slip_p95=max(0.0, 0.12 - 0.0001 * step2.rear_third_nmm),
        body_slip_p95_deg=max(0.0, 5.0 - 0.01 * step3.rear_spring_rate_nmm),
        understeer_low_deg=1.6 - 0.1 * step4.rear_arb_blade_start,
        understeer_high_deg=1.9 - 0.1 * step4.rear_arb_blade_start,
        front_pressure_hot_kpa=170.0,
        rear_pressure_hot_kpa=171.0,
        to_dict=lambda: {},
    )


def _fake_finalize(
    inputs,
    *,
    step1,
    step2,
    step3,
    step4,
    step5,
    step6,
    supporting,
    notes=None,
    candidate_vetoes=None,
    optimizer_used=False,
):
    legal = SimpleNamespace(valid=True, messages=[], to_dict=lambda: {"valid": True, "messages": []})
    confidence = SimpleNamespace(overall=0.8, to_dict=lambda: {"overall": 0.8, "per_metric": {}})
    return SolveChainResult(
        step1=step1,
        step2=step2,
        step3=step3,
        step4=step4,
        step5=step5,
        step6=step6,
        supporting=supporting,
        legal_validation=legal,
        decision_trace=[],
        prediction=_prediction_from_steps(step2, step3, step4),
        prediction_confidence=confidence,
        notes=list(notes or []),
        candidate_vetoes=list(candidate_vetoes or []),
        optimizer_used=optimizer_used,
    )


def _build_base_result(current_setup, measured, supporting, *, corners):
    car = get_car("bmw")
    surface = load_car_surfaces("bmw")[17.0]
    track = _track()
    solve_inputs = SolveChainInputs(
        car=car,
        surface=surface,
        track=track,
        measured=measured,
        driver=SimpleNamespace(style="smooth"),
        diagnosis=SimpleNamespace(state_issues=[]),
        current_setup=current_setup,
        target_balance=50.14,
        fuel_load_l=58.0,
        wing_angle=17.0,
        corners=corners,
    )
    with (
        patch("solver.solve_chain.optimize_if_supported", return_value=None),
        patch("solver.solve_chain._build_supporting", side_effect=lambda _inputs: copy.deepcopy(supporting)),
        patch("solver.solve_chain._finalize_result", side_effect=_fake_finalize),
        patch("solver.bmw_rotation_search.search_rotation_controls", return_value=None),
    ):
        from solver.solve_chain import run_base_solve

        return solve_inputs, run_base_solve(solve_inputs)


class SequentialSolveTelemetryTests(unittest.TestCase):
    def test_base_sequential_path_passes_measured_into_step5(self) -> None:
        captured = {}

        class FakeRakeSolver:
            def __init__(self, *_args, **_kwargs):
                pass

            def solve(self, **_kwargs):
                return SimpleNamespace(
                    dynamic_front_rh_mm=18.0,
                    dynamic_rear_rh_mm=42.0,
                    static_front_rh_mm=30.0,
                    static_rear_rh_mm=49.0,
                    front_pushrod_offset_mm=-26.0,
                    rear_pushrod_offset_mm=-24.0,
                    df_balance_pct=50.1,
                    ld_ratio=4.0,
                    vortex_burst_margin_mm=1.5,
                )

        class FakeHeaveSolver:
            def __init__(self, *_args, **_kwargs):
                pass

            def solve(self, **_kwargs):
                return SimpleNamespace(
                    front_heave_nmm=50.0,
                    rear_third_nmm=520.0,
                    perch_offset_front_mm=-10.0,
                    perch_offset_rear_mm=43.0,
                )

            def reconcile_solution(self, *_args, **_kwargs):
                return None

        class FakeCornerSpringSolver:
            def __init__(self, *_args, **_kwargs):
                pass

            def solve(self, **_kwargs):
                return SimpleNamespace(
                    front_wheel_rate_nmm=180.0,
                    front_torsion_od_mm=13.9,
                    rear_spring_rate_nmm=160.0,
                    rear_spring_perch_mm=30.0,
                )

        class FakeDamperSolver:
            def __init__(self, *_args, **_kwargs):
                pass

            def solve(self, **_kwargs):
                corner = SimpleNamespace(ls_comp=6, ls_rbd=7, hs_comp=5, hs_rbd=8, hs_slope=10)
                return SimpleNamespace(
                    c_hs_front=1200.0,
                    c_hs_rear=1400.0,
                    lf=copy.deepcopy(corner),
                    rf=copy.deepcopy(corner),
                    lr=copy.deepcopy(corner),
                    rr=copy.deepcopy(corner),
                )

        class FakeARBSolver:
            def __init__(self, *_args, **_kwargs):
                pass

            def solve(self, **_kwargs):
                return SimpleNamespace(
                    front_arb_size="Soft",
                    front_arb_blade_start=1,
                    rear_arb_size="Medium",
                    rear_arb_blade_start=3,
                    rarb_blade_slow_corner=1,
                    rarb_blade_fast_corner=4,
                    farb_blade_locked=1,
                    k_roll_front_total=1000.0,
                    k_roll_rear_total=1200.0,
                )

        class FakeWheelGeometrySolver:
            def __init__(self, *_args, **_kwargs):
                pass

            def solve(self, **kwargs):
                captured["measured"] = kwargs.get("measured")
                return SimpleNamespace(
                    front_camber_deg=-2.1,
                    rear_camber_deg=-1.8,
                    front_toe_mm=-0.4,
                    rear_toe_mm=0.3,
                )

        sentinel_measured = object()
        fake_car = SimpleNamespace(
            canonical_name="bmw",
            corner_spring=SimpleNamespace(rear_motion_ratio=1.0),
        )
        fake_inputs = SolveChainInputs(
            car=fake_car,
            surface=SimpleNamespace(),
            track=SimpleNamespace(track_name="Sebring"),
            measured=sentinel_measured,
            driver=SimpleNamespace(),
            diagnosis=SimpleNamespace(),
            current_setup=SimpleNamespace(front_camber_deg=-2.1, rear_arb_size="Medium"),
            target_balance=50.14,
            fuel_load_l=58.0,
            wing_angle=17.0,
        )

        with (
            patch("solver.solve_chain.RakeSolver", FakeRakeSolver),
            patch("solver.solve_chain.HeaveSolver", FakeHeaveSolver),
            patch("solver.solve_chain.CornerSpringSolver", FakeCornerSpringSolver),
            patch("solver.solve_chain.DamperSolver", FakeDamperSolver),
            patch("solver.solve_chain.ARBSolver", FakeARBSolver),
            patch("solver.solve_chain.WheelGeometrySolver", FakeWheelGeometrySolver),
            patch("solver.solve_chain.reconcile_ride_heights", return_value=None),
        ):
            from solver.solve_chain import _run_sequential_solver

            _run_sequential_solver(fake_inputs)

        self.assertIs(captured["measured"], sentinel_measured)


class BMWSebringRotationSearchTests(unittest.TestCase):
    def _run_rotation_search(self, *, current_setup, measured, supporting, corners):
        solve_inputs, base_result = _build_base_result(current_setup, measured, supporting, corners=corners)
        with (
            patch("solver.solve_chain._build_supporting", side_effect=lambda _inputs: copy.deepcopy(supporting)),
            patch("solver.solve_chain._finalize_result", side_effect=_fake_finalize),
        ):
            return solve_inputs, base_result, search_rotation_controls(base_result=base_result, inputs=solve_inputs)

    def test_exit_understeer_can_move_diff_toward_less_lock(self) -> None:
        current_setup = _setup(diff_preload_nm=30.0, diff_ramp_angles="45/70", diff_clutch_plates=6)
        supporting = _supporting(diff_preload_nm=30.0, diff_ramp_option_idx=1, diff_ramp_angles="45/70", diff_ramp_coast=45, diff_ramp_drive=70, diff_clutch_plates=6)
        measured = _measured(
            understeer_low_speed_deg=1.9,
            understeer_high_speed_deg=2.2,
            understeer_mean_deg=1.8,
            yaw_rate_correlation=0.84,
            rear_power_slip_ratio_p95=0.04,
            rear_slip_ratio_p95=0.04,
            body_slip_p95_deg=2.5,
            front_carcass_mean_c=92.0,
            front_pressure_mean_kpa=165.0,
        )
        _inputs, base_result, rotation = self._run_rotation_search(
            current_setup=current_setup,
            measured=measured,
            supporting=supporting,
            corners=[_corner(exit_loss_s=0.30, throttle_delay_s=0.45, understeer_mean_deg=1.7)],
        )
        self.assertIsNotNone(rotation)
        result = rotation.result
        self.assertGreaterEqual(result.supporting.diff_ramp_option_idx, base_result.supporting.diff_ramp_option_idx)
        self.assertLessEqual(result.supporting.diff_clutch_plates, base_result.supporting.diff_clutch_plates)
        self.assertLessEqual(result.supporting.diff_preload_nm, base_result.supporting.diff_preload_nm)

    def test_exit_instability_can_move_diff_toward_more_lock(self) -> None:
        current_setup = _setup(diff_preload_nm=10.0, diff_ramp_angles="50/75", diff_clutch_plates=2)
        supporting = _supporting(diff_preload_nm=10.0, diff_ramp_option_idx=2, diff_ramp_angles="50/75", diff_ramp_coast=50, diff_ramp_drive=75, diff_clutch_plates=2)
        measured = _measured(
            understeer_low_speed_deg=0.5,
            understeer_high_speed_deg=0.7,
            understeer_mean_deg=0.6,
            yaw_rate_correlation=0.87,
            rear_power_slip_ratio_p95=0.12,
            rear_slip_ratio_p95=0.12,
            body_slip_p95_deg=4.6,
            rear_carcass_mean_c=96.0,
            rear_pressure_mean_kpa=171.0,
        )
        _inputs, base_result, rotation = self._run_rotation_search(
            current_setup=current_setup,
            measured=measured,
            supporting=supporting,
            corners=[],
        )
        self.assertIsNotNone(rotation)
        result = rotation.result
        self.assertLessEqual(result.supporting.diff_ramp_option_idx, base_result.supporting.diff_ramp_option_idx)
        self.assertGreaterEqual(result.supporting.diff_clutch_plates, base_result.supporting.diff_clutch_plates)
        self.assertGreaterEqual(result.supporting.diff_preload_nm, base_result.supporting.diff_preload_nm)

    def test_rotation_search_can_change_geometry_and_rear_arb(self) -> None:
        current_setup = _setup()
        supporting = _supporting()
        measured = _measured(
            understeer_low_speed_deg=1.8,
            understeer_high_speed_deg=2.0,
            understeer_mean_deg=1.7,
            yaw_rate_correlation=0.85,
            rear_power_slip_ratio_p95=0.05,
            body_slip_p95_deg=2.7,
            front_carcass_mean_c=91.0,
            front_pressure_mean_kpa=164.0,
        )
        _inputs, base_result, rotation = self._run_rotation_search(
            current_setup=current_setup,
            measured=measured,
            supporting=supporting,
            corners=[_corner(exit_loss_s=0.28, throttle_delay_s=0.40, understeer_mean_deg=1.6)],
        )
        self.assertIsNotNone(rotation)
        result = rotation.result
        changed = (
            result.step5.front_toe_mm != base_result.step5.front_toe_mm
            or result.step5.rear_toe_mm != base_result.step5.rear_toe_mm
            or result.step5.front_camber_deg != base_result.step5.front_camber_deg
            or result.step5.rear_camber_deg != base_result.step5.rear_camber_deg
            or result.step4.rear_arb_blade_start != base_result.step4.rear_arb_blade_start
        )
        self.assertTrue(changed)

    def test_rotation_search_can_change_torsion_and_rear_spring(self) -> None:
        current_setup = _setup(front_torsion_od_mm=14.34, rear_spring_nmm=150.0)
        supporting = _supporting()
        measured = _measured(
            understeer_low_speed_deg=2.0,
            understeer_high_speed_deg=2.2,
            understeer_mean_deg=1.9,
            yaw_rate_correlation=0.86,
            rear_power_slip_ratio_p95=0.04,
            rear_slip_ratio_p95=0.04,
            body_slip_p95_deg=2.5,
            front_carcass_mean_c=94.0,
            front_pressure_mean_kpa=167.0,
        )
        _inputs, base_result, rotation = self._run_rotation_search(
            current_setup=current_setup,
            measured=measured,
            supporting=supporting,
            corners=[_corner(exit_loss_s=0.34, throttle_delay_s=0.46, understeer_mean_deg=1.8)],
        )
        self.assertIsNotNone(rotation)
        result = rotation.result
        self.assertTrue(
            result.step3.rear_spring_rate_nmm != base_result.step3.rear_spring_rate_nmm
            or result.step3.front_torsion_od_mm != base_result.step3.front_torsion_od_mm
        )
        self.assertIn(
            result.step3.parameter_search_status.get("front_torsion_od_mm"),
            {"searched_and_changed", "searched_and_kept"},
        )
        self.assertIn(
            result.step3.parameter_search_status.get("rear_spring_rate_nmm"),
            {"searched_and_changed", "searched_and_kept"},
        )

    def test_rotation_search_can_move_step3_toward_stability_when_rear_is_nervous(self) -> None:
        current_setup = _setup(front_torsion_od_mm=13.9, rear_spring_nmm=170.0)
        supporting = _supporting()
        measured = _measured(
            understeer_low_speed_deg=0.6,
            understeer_high_speed_deg=0.8,
            understeer_mean_deg=0.7,
            yaw_rate_correlation=0.84,
            rear_power_slip_ratio_p95=0.13,
            rear_slip_ratio_p95=0.13,
            body_slip_p95_deg=4.8,
            rear_carcass_mean_c=97.0,
            rear_pressure_mean_kpa=172.0,
        )
        _inputs, base_result, rotation = self._run_rotation_search(
            current_setup=current_setup,
            measured=measured,
            supporting=supporting,
            corners=[],
        )
        self.assertIsNotNone(rotation)
        result = rotation.result
        self.assertGreaterEqual(result.step3.front_torsion_od_mm, base_result.step3.front_torsion_od_mm)
        self.assertLessEqual(result.step3.rear_spring_rate_nmm, base_result.step3.rear_spring_rate_nmm)

    def test_parameter_coverage_surfaces_searched_kept_status_and_evidence(self) -> None:
        step1 = SimpleNamespace(front_pushrod_offset_mm=-26.0, rear_pushrod_offset_mm=-24.0)
        step2 = SimpleNamespace(front_heave_nmm=50.0, perch_offset_front_mm=-11.0, rear_third_nmm=520.0, perch_offset_rear_mm=42.0)
        step3 = SimpleNamespace(
            front_torsion_od_mm=13.9,
            rear_spring_rate_nmm=160.0,
            rear_spring_perch_mm=30.0,
            parameter_search_status={"front_torsion_od_mm": "searched_and_kept"},
            parameter_search_evidence={"front_torsion_od_mm": ["front_platform_margin=0.42"]},
        )
        step4 = SimpleNamespace(
            front_arb_size="Soft",
            front_arb_blade_start=1,
            rear_arb_size="Medium",
            rear_arb_blade_start=3,
            rarb_blade_slow_corner=1,
            rarb_blade_fast_corner=4,
            parameter_search_status={"rear_arb_blade": "searched_and_kept"},
            parameter_search_evidence={"rear_arb_blade": ["exit_push=1.10", "long_exit_bias=0.80"]},
        )
        step5 = SimpleNamespace(
            front_camber_deg=-2.1,
            rear_camber_deg=-1.8,
            front_toe_mm=-0.4,
            rear_toe_mm=0.3,
            parameter_search_status={"front_toe_mm": "searched_and_kept"},
            parameter_search_evidence={"front_toe_mm": ["yaw_rate_correlation=0.91"]},
        )
        step6 = SimpleNamespace(
            lf=SimpleNamespace(ls_comp=6, ls_rbd=7, hs_comp=5, hs_rbd=8, hs_slope=10),
            lr=SimpleNamespace(ls_comp=6, ls_rbd=7, hs_comp=5, hs_rbd=8, hs_slope=10),
        )
        supporting = SimpleNamespace(
            brake_bias_pct=46.0,
            diff_preload_nm=30.0,
            diff_ramp_option_idx=1,
            diff_ramp_angles="45/70",
            diff_clutch_plates=6,
            tc_gain=4,
            tc_slip=3,
            parameter_search_status={"diff_preload_nm": "searched_and_kept"},
            parameter_search_evidence={"diff_preload_nm": ["exit_push=1.10", "instability=0.22"]},
        )
        current_setup = _setup()
        coverage = build_parameter_coverage(
            car=get_car("bmw"),
            wing=17.0,
            current_setup=current_setup,
            step1=step1,
            step2=step2,
            step3=step3,
            step4=step4,
            step5=step5,
            step6=step6,
            supporting=supporting,
        )

        self.assertFalse(coverage["diff_preload_nm"]["changed"])
        self.assertEqual(coverage["diff_preload_nm"]["search_status"], "searched_and_kept")
        self.assertIn("exit_push=1.10", coverage["diff_preload_nm"]["search_evidence"])
        self.assertEqual(coverage["front_torsion_od_mm"]["search_status"], "searched_and_kept")
        self.assertIn("front_platform_margin=0.42", coverage["front_torsion_od_mm"]["search_evidence"])
        self.assertEqual(coverage["front_toe_mm"]["search_status"], "searched_and_kept")
        self.assertEqual(coverage["rear_arb_blade"]["search_status"], "searched_and_kept")

    def test_candidate_family_application_preserves_rotation_control_result(self) -> None:
        current_setup = _setup()
        supporting = _supporting()
        measured = _measured(
            understeer_low_speed_deg=1.8,
            understeer_high_speed_deg=2.1,
            understeer_mean_deg=1.7,
            yaw_rate_correlation=0.84,
            rear_power_slip_ratio_p95=0.05,
            body_slip_p95_deg=2.7,
            front_carcass_mean_c=91.0,
            front_pressure_mean_kpa=164.0,
        )
        solve_inputs, base_result, rotation = self._run_rotation_search(
            current_setup=current_setup,
            measured=measured,
            supporting=supporting,
            corners=[_corner(exit_loss_s=0.32, throttle_delay_s=0.44, understeer_mean_deg=1.7)],
        )
        self.assertIsNotNone(rotation)
        rotation_result = rotation.result
        candidate_result = copy.deepcopy(rotation_result)
        candidate_result.step2.front_heave_nmm = rotation_result.step2.front_heave_nmm + 10.0
        candidate_result.step3.front_torsion_od_mm = rotation_result.step3.front_torsion_od_mm + 0.44
        candidate_result.step3.rear_spring_rate_nmm = rotation_result.step3.rear_spring_rate_nmm + 10.0
        candidate_result.step4.rear_arb_blade_start = max(1, rotation_result.step4.rear_arb_blade_start - 1)
        candidate_result.step4.rarb_blade_slow_corner = max(1, rotation_result.step4.rarb_blade_slow_corner - 1)
        candidate_result.step4.rarb_blade_fast_corner = max(1, rotation_result.step4.rarb_blade_fast_corner - 1)
        candidate_result.step5.front_toe_mm = rotation_result.step5.front_toe_mm + 0.2
        candidate_result.supporting.diff_preload_nm = rotation_result.supporting.diff_preload_nm + 10.0
        candidate_result.supporting.diff_ramp_option_idx = 0
        candidate_result.supporting.diff_ramp_coast = 40
        candidate_result.supporting.diff_ramp_drive = 65
        candidate_result.supporting.diff_ramp_angles = "40/65"
        candidate_result.supporting.diff_clutch_plates = 6

        def _fake_materialize(candidate_base_result, overrides, _inputs):
            result = copy.deepcopy(candidate_base_result)
            for field_name, value in overrides.step3.items():
                setattr(result.step3, field_name, value)
            for field_name, value in overrides.step4.items():
                setattr(result.step4, field_name, value)
            for field_name, value in overrides.step5.items():
                setattr(result.step5, field_name, value)
            for field_name, value in overrides.supporting.items():
                setattr(result.supporting, field_name, value)
            if "diff_ramp_option_idx" in overrides.supporting:
                ramp_options = list(
                    getattr(getattr(_inputs.car, "garage_ranges", None), "diff_coast_drive_ramp_options", [(40, 65), (45, 70), (50, 75)])
                )
                idx = int(overrides.supporting["diff_ramp_option_idx"])
                coast, drive = ramp_options[idx]
                result.supporting.diff_ramp_coast = coast
                result.supporting.diff_ramp_drive = drive
                result.supporting.diff_ramp_angles = f"{coast}/{drive}"
            result.notes = ["fake materialized candidate"]
            return result

        with (
            patch("solver.solve_chain.materialize_overrides", side_effect=_fake_materialize),
            patch("solver.decision_trace.build_parameter_decisions", return_value=["rebuilt-trace"]),
        ):
            preserved_result, preserved_controls = preserve_candidate_rotation_controls(
                rotation_result=rotation_result,
                candidate_result=candidate_result,
                inputs=solve_inputs,
            )

        self.assertTrue(preserved_controls)
        self.assertIsNotNone(preserved_result)
        self.assertEqual(preserved_result.step2.front_heave_nmm, candidate_result.step2.front_heave_nmm)
        self.assertEqual(preserved_result.step3.front_torsion_od_mm, rotation_result.step3.front_torsion_od_mm)
        self.assertEqual(preserved_result.step3.rear_spring_rate_nmm, rotation_result.step3.rear_spring_rate_nmm)
        self.assertEqual(preserved_result.step5.front_toe_mm, rotation_result.step5.front_toe_mm)
        self.assertEqual(preserved_result.step4.rear_arb_blade_start, rotation_result.step4.rear_arb_blade_start)
        self.assertEqual(preserved_result.supporting.diff_preload_nm, rotation_result.supporting.diff_preload_nm)
        self.assertEqual(preserved_result.supporting.diff_ramp_option_idx, rotation_result.supporting.diff_ramp_option_idx)
        self.assertEqual(preserved_result.supporting.diff_clutch_plates, rotation_result.supporting.diff_clutch_plates)
        self.assertEqual(
            preserved_result.step5.parameter_search_status.get("front_toe_mm"),
            rotation_result.step5.parameter_search_status.get("front_toe_mm"),
        )
        self.assertEqual(preserved_result.decision_trace, ["rebuilt-trace"])


if __name__ == "__main__":
    unittest.main()
