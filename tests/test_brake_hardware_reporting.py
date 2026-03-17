import unittest
from types import SimpleNamespace

from comparison.compare import SessionAnalysis, compare_sessions


class BrakeHardwareReportingTests(unittest.TestCase):
    def test_compare_sessions_surfaces_brake_and_diff_hardware_fields(self) -> None:
        base_kwargs = dict(
            measured=SimpleNamespace(),
            corners=[],
            driver=SimpleNamespace(),
            diagnosis=SimpleNamespace(problems=[]),
            track=SimpleNamespace(),
            lap_time_s=100.0,
            lap_number=4,
            track_name="Sebring International",
            wing_angle=17.0,
            session_context=None,
        )
        sessions = [
            SessionAnalysis(
                label="S1",
                ibt_path="s1.ibt",
                setup=SimpleNamespace(
                    brake_bias_pct=46.0,
                    brake_bias_target=1.5,
                    brake_bias_migration=-0.5,
                    front_master_cyl_mm=19.1,
                    rear_master_cyl_mm=20.6,
                    pad_compound="Medium",
                    diff_preload_nm=20.0,
                    diff_clutch_plates=4,
                ),
                **base_kwargs,
            ),
            SessionAnalysis(
                label="S2",
                ibt_path="s2.ibt",
                setup=SimpleNamespace(
                    brake_bias_pct=46.5,
                    brake_bias_target=0.0,
                    brake_bias_migration=0.0,
                    front_master_cyl_mm=20.6,
                    rear_master_cyl_mm=20.6,
                    pad_compound="Low",
                    diff_preload_nm=25.0,
                    diff_clutch_plates=6,
                ),
                **base_kwargs,
            ),
        ]

        result = compare_sessions(sessions)

        self.assertEqual(result.setup_deltas["Brake Bias Target"], [1.5, 0.0])
        self.assertEqual(result.setup_deltas["Brake Bias Migration"], [-0.5, 0.0])
        self.assertEqual(result.setup_deltas["Front Master Cyl"], [19.1, 20.6])
        self.assertEqual(result.setup_deltas["Pad Compound"], ["Medium", "Low"])
        self.assertEqual(result.setup_deltas["Diff Clutch Plates"], [4, 6])


if __name__ == "__main__":
    unittest.main()
