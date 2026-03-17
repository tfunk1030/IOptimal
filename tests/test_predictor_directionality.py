import unittest
from types import SimpleNamespace

from solver.predictor import predict_candidate_telemetry


class PredictorDirectionalityTests(unittest.TestCase):
    def test_increasing_front_heave_reduces_predicted_front_travel_use(self) -> None:
        current_setup = SimpleNamespace(front_heave_nmm=40.0, rear_third_nmm=500.0, brake_bias_pct=46.0)
        baseline_measured = SimpleNamespace(
            front_heave_travel_used_pct=90.0,
            front_rh_excursion_measured_mm=11.0,
            rear_rh_std_mm=6.0,
            pitch_range_braking_deg=1.1,
            front_braking_lock_ratio_p95=0.07,
            rear_power_slip_ratio_p95=0.08,
            body_slip_p95_deg=3.2,
            understeer_low_speed_deg=1.2,
            understeer_high_speed_deg=1.4,
            front_pressure_mean_kpa=170.0,
            rear_pressure_mean_kpa=171.0,
        )
        softer_step2 = SimpleNamespace(front_heave_nmm=40.0, rear_third_nmm=500.0)
        stiffer_step2 = SimpleNamespace(front_heave_nmm=60.0, rear_third_nmm=500.0)

        soft_pred, _ = predict_candidate_telemetry(
            current_setup=current_setup,
            baseline_measured=baseline_measured,
            step2=softer_step2,
            step4=None,
            supporting=None,
        )
        stiff_pred, _ = predict_candidate_telemetry(
            current_setup=current_setup,
            baseline_measured=baseline_measured,
            step2=stiffer_step2,
            step4=None,
            supporting=None,
        )

        self.assertGreater(soft_pred.front_heave_travel_used_pct, stiff_pred.front_heave_travel_used_pct)
        self.assertGreater(soft_pred.front_excursion_mm, stiff_pred.front_excursion_mm)

    def test_increasing_rear_third_reduces_predicted_rear_variance(self) -> None:
        current_setup = SimpleNamespace(front_heave_nmm=40.0, rear_third_nmm=500.0, brake_bias_pct=46.0)
        baseline_measured = SimpleNamespace(
            front_heave_travel_used_pct=85.0,
            front_rh_excursion_measured_mm=10.0,
            rear_rh_std_mm=7.0,
            pitch_range_braking_deg=1.0,
            front_braking_lock_ratio_p95=0.06,
            rear_power_slip_ratio_p95=0.09,
            body_slip_p95_deg=4.0,
            understeer_low_speed_deg=1.0,
            understeer_high_speed_deg=1.2,
            front_pressure_mean_kpa=170.0,
            rear_pressure_mean_kpa=171.0,
        )
        soft_step2 = SimpleNamespace(front_heave_nmm=40.0, rear_third_nmm=500.0)
        stiff_step2 = SimpleNamespace(front_heave_nmm=40.0, rear_third_nmm=700.0)

        soft_pred, _ = predict_candidate_telemetry(
            current_setup=current_setup,
            baseline_measured=baseline_measured,
            step2=soft_step2,
            step4=None,
            supporting=None,
        )
        stiff_pred, _ = predict_candidate_telemetry(
            current_setup=current_setup,
            baseline_measured=baseline_measured,
            step2=stiff_step2,
            step4=None,
            supporting=None,
        )

        self.assertGreater(soft_pred.rear_rh_std_mm, stiff_pred.rear_rh_std_mm)
        self.assertGreater(soft_pred.rear_power_slip_p95, stiff_pred.rear_power_slip_p95)

    def test_rearward_brake_bias_reduces_predicted_front_lock(self) -> None:
        current_setup = SimpleNamespace(front_heave_nmm=40.0, rear_third_nmm=500.0, brake_bias_pct=46.5)
        baseline_measured = SimpleNamespace(
            front_heave_travel_used_pct=85.0,
            front_rh_excursion_measured_mm=10.0,
            rear_rh_std_mm=7.0,
            pitch_range_braking_deg=1.0,
            front_braking_lock_ratio_p95=0.080,
            rear_power_slip_ratio_p95=0.09,
            body_slip_p95_deg=4.0,
            understeer_low_speed_deg=1.0,
            understeer_high_speed_deg=1.2,
            front_pressure_mean_kpa=170.0,
            rear_pressure_mean_kpa=171.0,
        )
        more_forward_supporting = SimpleNamespace(brake_bias_pct=46.5)
        more_rearward_supporting = SimpleNamespace(brake_bias_pct=45.5)

        forward_pred, _ = predict_candidate_telemetry(
            current_setup=current_setup,
            baseline_measured=baseline_measured,
            step2=SimpleNamespace(front_heave_nmm=40.0, rear_third_nmm=500.0),
            step4=None,
            supporting=more_forward_supporting,
        )
        rearward_pred, _ = predict_candidate_telemetry(
            current_setup=current_setup,
            baseline_measured=baseline_measured,
            step2=SimpleNamespace(front_heave_nmm=40.0, rear_third_nmm=500.0),
            step4=None,
            supporting=more_rearward_supporting,
        )

        self.assertGreater(forward_pred.front_lock_p95, rearward_pred.front_lock_p95)


if __name__ == "__main__":
    unittest.main()
