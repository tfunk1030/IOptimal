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


if __name__ == "__main__":
    unittest.main()
