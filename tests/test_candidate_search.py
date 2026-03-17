import unittest
from types import SimpleNamespace

from solver.candidate_search import generate_candidate_families


class CandidateSearchTests(unittest.TestCase):
    def test_baseline_reset_candidate_wins_when_overhaul_requires_reset(self) -> None:
        authority = SimpleNamespace(
            label="S4",
            setup=SimpleNamespace(front_heave_nmm=40.0, rear_third_nmm=520.0, brake_bias_pct=46.0),
            measured=SimpleNamespace(
                front_heave_travel_used_pct=92.0,
                front_rh_excursion_measured_mm=12.0,
                rear_rh_std_mm=8.0,
                pitch_range_braking_deg=1.2,
                front_braking_lock_ratio_p95=0.08,
                rear_power_slip_ratio_p95=0.10,
                body_slip_p95_deg=4.2,
                understeer_low_speed_deg=1.4,
                understeer_high_speed_deg=1.8,
                front_pressure_mean_kpa=171.0,
                rear_pressure_mean_kpa=172.0,
            ),
        )
        best = SimpleNamespace(label="S2")
        candidates = generate_candidate_families(
            authority_session=authority,
            best_session=best,
            overhaul_assessment=SimpleNamespace(classification="baseline_reset", confidence=0.82),
            legal_validation=SimpleNamespace(valid=True),
            authority_score={"score": 0.58},
            envelope_distance=2.8,
            setup_distance=2.1,
            produced_solution={"step2": SimpleNamespace(front_heave_nmm=55.0, rear_third_nmm=650.0), "step4": None, "supporting": None},
        )

        selected = next(candidate for candidate in candidates if candidate.selected)
        self.assertEqual(selected.family, "baseline_reset")
        self.assertGreaterEqual(len(candidates), 2)
        self.assertIn("compromise", {candidate.family for candidate in candidates})
        incremental = next(candidate for candidate in candidates if candidate.family == "incremental")
        self.assertNotEqual(
            getattr(incremental.step2, "front_heave_nmm", None),
            getattr(selected.step2, "front_heave_nmm", None),
        )

    def test_incremental_candidate_wins_for_minor_tweak_case(self) -> None:
        authority = SimpleNamespace(
            label="S2",
            setup=SimpleNamespace(front_heave_nmm=42.0, rear_third_nmm=520.0, brake_bias_pct=46.0),
            measured=SimpleNamespace(
                front_heave_travel_used_pct=76.0,
                front_rh_excursion_measured_mm=8.0,
                rear_rh_std_mm=5.5,
                pitch_range_braking_deg=0.9,
                front_braking_lock_ratio_p95=0.05,
                rear_power_slip_ratio_p95=0.05,
                body_slip_p95_deg=2.1,
                understeer_low_speed_deg=0.6,
                understeer_high_speed_deg=0.8,
                front_pressure_mean_kpa=166.0,
                rear_pressure_mean_kpa=167.0,
            ),
        )
        best = SimpleNamespace(label="S2")
        candidates = generate_candidate_families(
            authority_session=authority,
            best_session=best,
            overhaul_assessment=SimpleNamespace(classification="minor_tweak", confidence=0.71),
            legal_validation=SimpleNamespace(valid=True),
            authority_score={"score": 0.86},
            envelope_distance=0.3,
            setup_distance=0.2,
            produced_solution={"step2": SimpleNamespace(front_heave_nmm=45.0, rear_third_nmm=520.0), "step4": None, "supporting": None},
        )

        selected = next(candidate for candidate in candidates if candidate.selected)
        self.assertEqual(selected.family, "incremental")
        scores = {candidate.family: candidate.score.total for candidate in candidates}
        self.assertGreater(scores["incremental"], scores["baseline_reset"])
        compromise = next(candidate for candidate in candidates if candidate.family == "compromise")
        self.assertNotEqual(
            getattr(compromise.step2, "front_heave_nmm", None),
            getattr(candidates[0].step2, "front_heave_nmm", None),
        )

    def test_compromise_candidate_can_win_for_moderate_rework_case(self) -> None:
        authority = SimpleNamespace(
            label="S3",
            setup=SimpleNamespace(front_heave_nmm=40.0, rear_third_nmm=520.0, brake_bias_pct=46.0),
            measured=SimpleNamespace(
                front_heave_travel_used_pct=88.0,
                front_rh_excursion_measured_mm=10.0,
                rear_rh_std_mm=7.0,
                pitch_range_braking_deg=1.0,
                front_braking_lock_ratio_p95=0.07,
                rear_power_slip_ratio_p95=0.08,
                body_slip_p95_deg=3.4,
                understeer_low_speed_deg=1.1,
                understeer_high_speed_deg=1.4,
                front_pressure_mean_kpa=170.0,
                rear_pressure_mean_kpa=171.0,
            ),
        )
        best = SimpleNamespace(label="S2")
        candidates = generate_candidate_families(
            authority_session=authority,
            best_session=best,
            overhaul_assessment=SimpleNamespace(classification="moderate_rework", confidence=0.78),
            legal_validation=SimpleNamespace(valid=True),
            authority_score={"score": 0.72},
            envelope_distance=1.2,
            setup_distance=1.0,
            produced_solution={"step2": SimpleNamespace(front_heave_nmm=55.0, rear_third_nmm=650.0), "step4": None, "supporting": None},
        )

        families = {candidate.family for candidate in candidates}
        self.assertIn("compromise", families)
        selected = next(candidate for candidate in candidates if candidate.selected)
        self.assertEqual(selected.family, "compromise")

    def test_baseline_reset_family_can_seed_from_cluster_center(self) -> None:
        authority = SimpleNamespace(
            label="S4",
            setup=SimpleNamespace(front_heave_nmm=40.0, rear_third_nmm=520.0, brake_bias_pct=46.0),
            measured=SimpleNamespace(
                front_heave_travel_used_pct=92.0,
                front_rh_excursion_measured_mm=12.0,
                rear_rh_std_mm=8.0,
                pitch_range_braking_deg=1.2,
                front_braking_lock_ratio_p95=0.08,
                rear_power_slip_ratio_p95=0.10,
                body_slip_p95_deg=4.2,
                understeer_low_speed_deg=1.4,
                understeer_high_speed_deg=1.8,
                front_pressure_mean_kpa=171.0,
                rear_pressure_mean_kpa=172.0,
            ),
        )
        candidates = generate_candidate_families(
            authority_session=authority,
            best_session=SimpleNamespace(label="S2"),
            overhaul_assessment=SimpleNamespace(classification="baseline_reset", confidence=0.82),
            legal_validation=SimpleNamespace(valid=True),
            authority_score={"score": 0.58},
            envelope_distance=2.8,
            setup_distance=2.1,
            produced_solution={"step2": SimpleNamespace(front_heave_nmm=55.0, rear_third_nmm=650.0), "step4": None, "supporting": None},
            setup_cluster=SimpleNamespace(center={"front_heave_nmm": 48.0, "rear_third_nmm": 560.0}, member_sessions=["S1", "S2", "S3"]),
        )

        reset = next(candidate for candidate in candidates if candidate.family == "baseline_reset")
        self.assertEqual(reset.step2.front_heave_nmm, 48.0)
        self.assertEqual(reset.step2.rear_third_nmm, 560.0)

    def test_candidate_families_are_materially_distinct_across_multiple_steps(self) -> None:
        authority = SimpleNamespace(
            label="S3",
            setup=SimpleNamespace(
                front_pushrod_mm=-26.5,
                rear_pushrod_mm=-24.0,
                front_heave_nmm=40.0,
                front_heave_perch_mm=-11.0,
                rear_third_nmm=520.0,
                rear_third_perch_mm=42.5,
                front_torsion_od_mm=13.9,
                rear_spring_nmm=160.0,
                rear_arb_blade=2,
                front_camber_deg=-2.8,
                rear_camber_deg=-1.8,
                front_toe_mm=-0.4,
                rear_toe_mm=0.0,
                front_hs_comp=5,
                rear_hs_comp=5,
                brake_bias_pct=46.0,
                diff_preload_nm=20.0,
                tc_gain=4,
                tc_slip=3,
            ),
            measured=SimpleNamespace(
                front_heave_travel_used_pct=91.0,
                front_rh_excursion_measured_mm=11.5,
                rear_rh_std_mm=8.0,
                pitch_range_braking_deg=1.2,
                front_braking_lock_ratio_p95=0.08,
                rear_power_slip_ratio_p95=0.10,
                body_slip_p95_deg=4.2,
                understeer_low_speed_deg=1.4,
                understeer_high_speed_deg=1.8,
                front_pressure_mean_kpa=171.0,
                rear_pressure_mean_kpa=172.0,
                bottoming_event_count_front_clean=2,
                bottoming_event_count_rear_clean=1,
            ),
        )
        candidates = generate_candidate_families(
            authority_session=authority,
            best_session=SimpleNamespace(label="S2"),
            overhaul_assessment=SimpleNamespace(classification="moderate_rework", confidence=0.8),
            legal_validation=SimpleNamespace(valid=True),
            authority_score={"score": 0.68},
            envelope_distance=2.0,
            setup_distance=1.8,
            produced_solution={
                "step1": SimpleNamespace(front_pushrod_offset_mm=-24.0, rear_pushrod_offset_mm=-22.0, static_front_rh_mm=30.5, static_rear_rh_mm=49.5),
                "step2": SimpleNamespace(front_heave_nmm=52.0, rear_third_nmm=620.0, perch_offset_front_mm=-10.0, perch_offset_rear_mm=44.0),
                "step3": SimpleNamespace(front_torsion_od_mm=13.6, rear_spring_rate_nmm=170.0),
                "step4": SimpleNamespace(rear_arb_blade_start=4, rarb_blade_slow_corner=3, rarb_blade_fast_corner=5),
                "step5": SimpleNamespace(front_camber_deg=-3.1, rear_camber_deg=-2.0, front_toe_mm=-0.5, rear_toe_mm=0.0),
                "step6": SimpleNamespace(
                    lf=SimpleNamespace(hs_comp=6, ls_rbd=7),
                    rf=SimpleNamespace(hs_comp=6, ls_rbd=7),
                    lr=SimpleNamespace(hs_comp=6, ls_rbd=8),
                    rr=SimpleNamespace(hs_comp=6, ls_rbd=8),
                ),
                "supporting": SimpleNamespace(brake_bias_pct=45.5, diff_preload_nm=25.0, tc_gain=5, tc_slip=4),
            },
        )

        incremental = next(candidate for candidate in candidates if candidate.family == "incremental")
        compromise = next(candidate for candidate in candidates if candidate.family == "compromise")
        reset = next(candidate for candidate in candidates if candidate.family == "baseline_reset")

        self.assertNotEqual(incremental.step3.front_torsion_od_mm, compromise.step3.front_torsion_od_mm)
        self.assertNotEqual(compromise.step4.rear_arb_blade_start, reset.step4.rear_arb_blade_start)
        self.assertNotEqual(incremental.step5.front_camber_deg, reset.step5.front_camber_deg)
        self.assertNotEqual(incremental.supporting.brake_bias_pct, reset.supporting.brake_bias_pct)


if __name__ == "__main__":
    unittest.main()
