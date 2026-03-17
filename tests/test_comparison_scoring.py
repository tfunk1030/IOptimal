import unittest
from types import SimpleNamespace

from analyzer.context import SessionContext
from comparison.compare import ComparisonResult, SessionAnalysis
from comparison.score import score_sessions


def _measured() -> SimpleNamespace:
    return SimpleNamespace(
        peak_lat_g_measured=2.0,
        rear_slip_ratio_p95=0.04,
        front_slip_ratio_p95=0.04,
        understeer_mean_deg=0.2,
        understeer_high_speed_deg=0.3,
        understeer_low_speed_deg=0.2,
        body_slip_p95_deg=2.0,
        speed_max_kph=300.0,
        aero_compression_front_mm=12.0,
        front_rh_std_mm=4.0,
        front_rh_settle_time_ms=125.0,
        rear_rh_settle_time_ms=125.0,
        yaw_rate_correlation=0.92,
        front_shock_vel_p99_mps=0.22,
        front_temp_spread_lf_c=8.0,
        front_temp_spread_rf_c=8.0,
        rear_temp_spread_lr_c=8.0,
        rear_temp_spread_rr_c=8.0,
        front_carcass_mean_c=92.0,
        rear_carcass_mean_c=93.0,
        front_pressure_mean_kpa=165.0,
        rear_pressure_mean_kpa=166.0,
    )


def _driver() -> SimpleNamespace:
    return SimpleNamespace(avg_peak_lat_g_utilization=0.9)


class ComparisonScoringTests(unittest.TestCase):
    def test_healthier_almost_as_fast_session_can_win(self) -> None:
        weak_context = SessionContext(
            fuel_l=89.0,
            tyre_state="overheated",
            thermal_validity=0.2,
            pace_validity=0.25,
            traffic_confidence=0.55,
            weather_confidence=0.5,
            comparable_to_baseline=False,
            notes=["unsafe and low-authority"],
        )
        strong_context = SessionContext(
            fuel_l=89.0,
            tyre_state="in_window",
            thermal_validity=0.95,
            pace_validity=0.95,
            traffic_confidence=0.8,
            weather_confidence=0.8,
            comparable_to_baseline=True,
            notes=["healthy benchmark"],
        )
        sessions = [
            SessionAnalysis(
                label="S1",
                ibt_path="s1.ibt",
                setup=SimpleNamespace(),
                measured=_measured(),
                corners=[],
                driver=_driver(),
                diagnosis=SimpleNamespace(problems=[]),
                track=SimpleNamespace(),
                lap_time_s=100.0,
                lap_number=4,
                session_context=weak_context,
            ),
            SessionAnalysis(
                label="S2",
                ibt_path="s2.ibt",
                setup=SimpleNamespace(),
                measured=_measured(),
                corners=[],
                driver=_driver(),
                diagnosis=SimpleNamespace(problems=[]),
                track=SimpleNamespace(),
                lap_time_s=100.3,
                lap_number=4,
                session_context=strong_context,
            ),
        ]
        comparison = ComparisonResult(
            sessions=sessions,
            setup_deltas={},
            telemetry_deltas={},
            corner_comparisons=[],
            problem_matrix={},
        )

        result = score_sessions(comparison)

        self.assertEqual(result.scores[0].session.label, "S2")
        self.assertGreater(
            result.scores[0].category_scores["context_health"],
            result.scores[1].category_scores["context_health"],
        )


if __name__ == "__main__":
    unittest.main()
