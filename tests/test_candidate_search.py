import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aero_model import load_car_surfaces
from car_model.cars import get_car
from learner.setup_clusters import SetupCluster
from solver.candidate_ranker import score_from_prediction
from solver.candidate_search import generate_candidate_families
from solver.solve_chain import SolveChainInputs, SolveChainOverrides, SolveChainResult, materialize_overrides, run_base_solve
from track_model.profile import TrackProfile


def _track() -> TrackProfile:
    return TrackProfile(
        track_name="Sebring",
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
        "front_heave_nmm": 40.0,
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
        "front_camber_deg": -2.9,
        "rear_camber_deg": -1.9,
        "front_toe_mm": -0.4,
        "rear_toe_mm": 0.0,
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
        "diff_preload_nm": 20.0,
        "tc_gain": 4,
        "tc_slip": 3,
        "brake_bias_target": 0.0,
        "brake_bias_migration": 0.0,
        "front_master_cyl_mm": 19.1,
        "rear_master_cyl_mm": 20.6,
        "pad_compound": "Medium",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _measured(**overrides):
    values = {
        "front_heave_travel_used_pct": 92.0,
        "front_rh_excursion_measured_mm": 12.0,
        "rear_rh_std_mm": 8.0,
        "pitch_range_braking_deg": 1.2,
        "front_braking_lock_ratio_p95": 0.08,
        "rear_power_slip_ratio_p95": 0.10,
        "body_slip_p95_deg": 4.2,
        "understeer_low_speed_deg": 1.4,
        "understeer_high_speed_deg": 1.8,
        "front_pressure_mean_kpa": 171.0,
        "rear_pressure_mean_kpa": 172.0,
        "bottoming_event_count_front_clean": 2,
        "bottoming_event_count_rear_clean": 1,
        "lf_shock_vel_p95_mps": 0.10,
        "rf_shock_vel_p95_mps": 0.10,
        "lr_shock_vel_p95_mps": 0.11,
        "rr_shock_vel_p95_mps": 0.11,
        "fallback_reasons": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _supporting():
    return SimpleNamespace(
        brake_bias_pct=46.0,
        brake_bias_target=0.0,
        brake_bias_migration=0.0,
        front_master_cyl_mm=19.1,
        rear_master_cyl_mm=20.6,
        pad_compound="Medium",
        diff_preload_nm=20.0,
        tc_gain=4,
        tc_slip=3,
    )


def _prediction_from_steps(step2, step3, step4):
    return SimpleNamespace(
        front_heave_travel_used_pct=max(0.0, 100.0 - 0.20 * step2.front_heave_nmm),
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


class CandidateSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.car = get_car("bmw")
        cls.surface = load_car_surfaces("bmw")[17.0]
        cls.track = _track()
        cls.current_setup = _setup()
        cls.measured = _measured()
        cls.authority_session = SimpleNamespace(
            label="S1",
            setup=cls.current_setup,
            measured=cls.measured,
            diagnosis=SimpleNamespace(state_issues=[]),
            driver=SimpleNamespace(style="smooth"),
        )
        cls.solve_inputs = SolveChainInputs(
            car=cls.car,
            surface=cls.surface,
            track=cls.track,
            measured=cls.measured,
            driver=cls.authority_session.driver,
            diagnosis=cls.authority_session.diagnosis,
            current_setup=cls.current_setup,
            target_balance=50.14,
            fuel_load_l=89.0,
            wing_angle=17.0,
        )
        with (
            patch("solver.solve_chain.optimize_if_supported", return_value=None),
            patch("solver.solve_chain._build_supporting", return_value=_supporting()),
            patch("solver.solve_chain._finalize_result", side_effect=_fake_finalize),
        ):
            cls.base_result = run_base_solve(cls.solve_inputs)

    def _materialize(self, overrides: SolveChainOverrides):
        with (
            patch("solver.solve_chain._build_supporting", return_value=_supporting()),
            patch("solver.solve_chain._finalize_result", side_effect=_fake_finalize),
        ):
            return materialize_overrides(self.base_result, overrides, self.solve_inputs)

    def test_materialize_overrides_recomputes_step2_dependent_margins(self) -> None:
        result = self._materialize(
            SolveChainOverrides(
                step2={
                    "front_heave_nmm": self.base_result.step2.front_heave_nmm + 40.0,
                    "perch_offset_front_mm": self.base_result.step2.perch_offset_front_mm + 1.5,
                }
            )
        )

        self.assertNotEqual(result.step2.front_bottoming_margin_mm, self.base_result.step2.front_bottoming_margin_mm)
        self.assertNotEqual(result.step2.travel_margin_front_mm, self.base_result.step2.travel_margin_front_mm)
        self.assertNotEqual(result.step2.front_excursion_at_rate_mm, self.base_result.step2.front_excursion_at_rate_mm)

    def test_materialize_overrides_recomputes_lltd_geometry_and_damper_derivatives(self) -> None:
        result = self._materialize(
            SolveChainOverrides(
                step4={
                    "rear_arb_blade_start": self.base_result.step4.rear_arb_blade_start + 1,
                    "rarb_blade_slow_corner": self.base_result.step4.rarb_blade_slow_corner + 1,
                    "rarb_blade_fast_corner": self.base_result.step4.rarb_blade_fast_corner + 1,
                },
                step5={
                    "front_camber_deg": self.base_result.step5.front_camber_deg - 0.3,
                },
                step6={
                    "lf": {"hs_comp": self.base_result.step6.lf.hs_comp + 2},
                    "rf": {"hs_comp": self.base_result.step6.rf.hs_comp + 2},
                },
            )
        )

        self.assertNotEqual(result.step4.lltd_achieved, self.base_result.step4.lltd_achieved)
        self.assertNotEqual(
            result.step5.front_dynamic_camber_at_peak_deg,
            self.base_result.step5.front_dynamic_camber_at_peak_deg,
        )
        self.assertNotEqual(result.step6.c_hs_front, self.base_result.step6.c_hs_front)

    def test_generate_candidate_families_returns_rematerialized_candidates(self) -> None:
        with (
            patch("solver.solve_chain._build_supporting", return_value=_supporting()),
            patch("solver.solve_chain._finalize_result", side_effect=_fake_finalize),
        ):
            candidates = generate_candidate_families(
                authority_session=self.authority_session,
                best_session=self.authority_session,
                overhaul_assessment=SimpleNamespace(classification="baseline_reset", confidence=0.82),
                authority_score={"score": 0.75},
                envelope_distance=2.0,
                setup_distance=1.8,
                base_result=self.base_result,
                solve_inputs=self.solve_inputs,
                setup_cluster=None,
            )

        self.assertEqual({candidate.family for candidate in candidates}, {"incremental", "compromise", "baseline_reset"})
        self.assertEqual(sum(1 for candidate in candidates if candidate.selected), 1)
        self.assertTrue(all(candidate.result is not None for candidate in candidates))
        self.assertTrue(any(candidate.overrides.step2 or candidate.overrides.step4 for candidate in candidates))

        incremental = next(candidate for candidate in candidates if candidate.family == "incremental")
        compromise = next(candidate for candidate in candidates if candidate.family == "compromise")
        self.assertNotEqual(incremental.step2.front_bottoming_margin_mm, self.base_result.step2.front_bottoming_margin_mm)
        self.assertNotEqual(compromise.step4.lltd_achieved, self.base_result.step4.lltd_achieved)

    def test_illegal_candidates_are_marked_unselectable(self) -> None:
        illegal_result = copy.deepcopy(self.base_result)
        illegal_result.legal_validation = SimpleNamespace(
            valid=False,
            messages=["illegal candidate"],
            to_dict=lambda: {"valid": False, "messages": ["illegal candidate"]},
        )

        with patch("solver.candidate_search.materialize_overrides", return_value=illegal_result):
            candidates = generate_candidate_families(
                authority_session=self.authority_session,
                best_session=self.authority_session,
                overhaul_assessment=SimpleNamespace(classification="minor_tweak", confidence=0.7),
                authority_score={"score": 0.75},
                envelope_distance=0.0,
                setup_distance=0.0,
                base_result=self.base_result,
                solve_inputs=self.solve_inputs,
                setup_cluster=None,
            )

        self.assertTrue(all(not candidate.selectable for candidate in candidates))
        self.assertTrue(all(candidate.status == "illegal" for candidate in candidates))
        self.assertFalse(any(candidate.selected for candidate in candidates))

    def test_ferrari_baseline_reset_candidate_is_blocked_for_implausible_cluster_center(self) -> None:
        ferrari_cluster = SetupCluster(
            center={"brake_bias_pct": 15.0},
            spreads={},
            member_sessions=["S1", "S2"],
            label="polluted ferrari cluster",
        )
        ferrari_inputs = SimpleNamespace(car=get_car("ferrari"))

        with patch("solver.candidate_search.materialize_overrides", return_value=self.base_result):
            candidates = generate_candidate_families(
                authority_session=self.authority_session,
                best_session=self.authority_session,
                overhaul_assessment=SimpleNamespace(classification="baseline_reset", confidence=0.82),
                authority_score={"score": 0.75},
                envelope_distance=2.0,
                setup_distance=1.8,
                base_result=self.base_result,
                solve_inputs=ferrari_inputs,
                setup_cluster=ferrari_cluster,
            )

        blocked = next(candidate for candidate in candidates if candidate.family == "baseline_reset")
        self.assertFalse(blocked.selectable)
        self.assertEqual(blocked.status, "blocked")
        self.assertIn("brake_bias_pct center 15.000 is implausible for ferrari", blocked.failure_reason)

    def test_signed_understeer_scoring_rewards_move_toward_zero(self) -> None:
        baseline = SimpleNamespace(
            front_heave_travel_used_pct=90.0,
            pitch_range_braking_deg=1.0,
            front_braking_lock_ratio_p95=0.08,
            rear_rh_std_mm=7.0,
            body_slip_p95_deg=3.5,
            understeer_low_speed_deg=-0.10,
            understeer_high_speed_deg=-0.10,
            rear_power_slip_ratio_p95=0.08,
        )
        toward_neutral = SimpleNamespace(
            front_heave_travel_used_pct=85.0,
            braking_pitch_deg=0.9,
            front_lock_p95=0.07,
            rear_rh_std_mm=6.5,
            body_slip_p95_deg=3.2,
            understeer_low_deg=0.0,
            understeer_high_deg=0.0,
            rear_power_slip_p95=0.07,
        )
        away_from_neutral = SimpleNamespace(
            front_heave_travel_used_pct=85.0,
            braking_pitch_deg=0.9,
            front_lock_p95=0.07,
            rear_rh_std_mm=6.5,
            body_slip_p95_deg=3.2,
            understeer_low_deg=-0.20,
            understeer_high_deg=-0.20,
            rear_power_slip_p95=0.07,
        )

        toward_score = score_from_prediction(
            baseline_measured=baseline,
            predicted=toward_neutral,
            prediction_confidence=0.8,
            disruption_cost=0.3,
        )
        away_score = score_from_prediction(
            baseline_measured=baseline,
            predicted=away_from_neutral,
            prediction_confidence=0.8,
            disruption_cost=0.3,
        )

        self.assertGreater(toward_score.performance, away_score.performance)


if __name__ == "__main__":
    unittest.main()
