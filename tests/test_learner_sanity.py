import unittest

from learner.delta_detector import detect_delta
from learner.ingest import _generate_insights
from learner.observation import Observation
from learner.sanity import select_valid_lap


class LearnerSanityTests(unittest.TestCase):
    def test_select_valid_lap_rejects_implausible_only_sessions(self):
        class FakeIBT:
            @staticmethod
            def lap_times(min_time: float = 108.0):
                return [(8, 309.520, 1, 2108)]

        with self.assertRaises(ValueError):
            select_valid_lap(
                FakeIBT(),
                car="bmw",
                track="Sebring International Raceway",
            )

    def test_generate_insights_filters_implausible_laps(self):
        observations = [
            {"performance": {"best_lap_time_s": 61.218}},
            {"performance": {"best_lap_time_s": 109.812}},
            {"performance": {"best_lap_time_s": 1672.020}},
            {"performance": {"best_lap_time_s": 110.155}},
        ]
        insights = _generate_insights(
            observations=observations,
            deltas=[],
            models=None,
            car="bmw",
            track="Sebring International",
        )
        text = "\n".join(insights["key_insights"])
        self.assertIn("109.812s", text)
        self.assertNotIn("61.218s", text)
        self.assertNotIn("1672.020s", text)

    def test_delta_detector_ignores_implausible_lap_times(self):
        before = Observation(
            session_id="before",
            ibt_path="before.ibt",
            car="bmw",
            track="Sebring International",
            performance={"best_lap_time_s": 61.218},
            telemetry={},
        )
        after = Observation(
            session_id="after",
            ibt_path="after.ibt",
            car="bmw",
            track="Sebring International",
            performance={"best_lap_time_s": 109.812},
            telemetry={},
        )
        delta = detect_delta(before, after)
        self.assertEqual(delta.lap_time_delta_s, 0.0)
