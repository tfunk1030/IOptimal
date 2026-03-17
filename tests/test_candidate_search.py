import unittest
from types import SimpleNamespace

from solver.candidate_search import generate_candidate_families


class CandidateSearchTests(unittest.TestCase):
    def test_baseline_reset_candidate_wins_when_overhaul_requires_reset(self) -> None:
        authority = SimpleNamespace(label="S4")
        best = SimpleNamespace(label="S2")
        candidates = generate_candidate_families(
            authority_session=authority,
            best_session=best,
            overhaul_assessment=SimpleNamespace(classification="baseline_reset", confidence=0.82),
            legal_validation=SimpleNamespace(valid=True),
            authority_score={"score": 0.58},
            envelope_distance=2.8,
            setup_distance=2.1,
            produced_solution={},
        )

        selected = next(candidate for candidate in candidates if candidate.selected)
        self.assertEqual(selected.family, "baseline_reset")
        self.assertEqual(len(candidates), 2)

    def test_incremental_candidate_wins_for_minor_tweak_case(self) -> None:
        authority = SimpleNamespace(label="S2")
        best = SimpleNamespace(label="S2")
        candidates = generate_candidate_families(
            authority_session=authority,
            best_session=best,
            overhaul_assessment=SimpleNamespace(classification="minor_tweak", confidence=0.71),
            legal_validation=SimpleNamespace(valid=True),
            authority_score={"score": 0.86},
            envelope_distance=0.3,
            setup_distance=0.2,
            produced_solution={},
        )

        selected = next(candidate for candidate in candidates if candidate.selected)
        self.assertEqual(selected.family, "incremental")
        scores = {candidate.family: candidate.score.total for candidate in candidates}
        self.assertGreater(scores["incremental"], scores["baseline_reset"])


if __name__ == "__main__":
    unittest.main()
