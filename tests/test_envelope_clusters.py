import unittest
from types import SimpleNamespace

from learner.envelope import build_telemetry_envelope, compute_envelope_distance
from learner.setup_clusters import build_setup_cluster, compute_setup_distance


class EnvelopeClusterTests(unittest.TestCase):
    def test_outlier_telemetry_session_scores_farther_from_healthy_envelope(self) -> None:
        healthy_samples = [
            SimpleNamespace(
                front_rh_std_mm=4.0,
                rear_rh_std_mm=5.0,
                understeer_mean_deg=0.2,
                body_slip_p95_deg=2.0,
                front_heave_travel_used_pct=70.0,
                rear_power_slip_ratio_p95=0.05,
                front_braking_lock_ratio_p95=0.04,
            ),
            SimpleNamespace(
                front_rh_std_mm=4.2,
                rear_rh_std_mm=5.3,
                understeer_mean_deg=0.3,
                body_slip_p95_deg=2.2,
                front_heave_travel_used_pct=72.0,
                rear_power_slip_ratio_p95=0.05,
                front_braking_lock_ratio_p95=0.045,
            ),
        ]
        envelope = build_telemetry_envelope(healthy_samples, source_sessions=["S1", "S2"])

        healthy_distance = compute_envelope_distance(healthy_samples[0], envelope)
        outlier_distance = compute_envelope_distance(
            SimpleNamespace(
                front_rh_std_mm=8.5,
                rear_rh_std_mm=10.0,
                understeer_mean_deg=2.0,
                body_slip_p95_deg=5.5,
                front_heave_travel_used_pct=95.0,
                rear_power_slip_ratio_p95=0.12,
                front_braking_lock_ratio_p95=0.09,
            ),
            envelope,
        )

        self.assertLess(healthy_distance.total_score, outlier_distance.total_score)
        self.assertTrue(outlier_distance.notes)

    def test_outlier_setup_scores_farther_from_cluster(self) -> None:
        healthy_setups = [
            SimpleNamespace(
                front_pushrod_mm=-26.5,
                rear_pushrod_mm=-24.0,
                front_heave_nmm=40.0,
                rear_third_nmm=500.0,
                front_torsion_od_mm=13.9,
                rear_spring_nmm=160.0,
                front_arb_blade=1,
                rear_arb_blade=3,
                front_camber_deg=-2.8,
                rear_camber_deg=-1.9,
                front_toe_mm=-0.4,
                rear_toe_mm=0.0,
                brake_bias_pct=46.0,
                diff_preload_nm=20.0,
            ),
            SimpleNamespace(
                front_pushrod_mm=-26.0,
                rear_pushrod_mm=-24.5,
                front_heave_nmm=42.0,
                rear_third_nmm=520.0,
                front_torsion_od_mm=13.9,
                rear_spring_nmm=165.0,
                front_arb_blade=1,
                rear_arb_blade=3,
                front_camber_deg=-2.9,
                rear_camber_deg=-1.8,
                front_toe_mm=-0.4,
                rear_toe_mm=0.0,
                brake_bias_pct=46.3,
                diff_preload_nm=22.0,
            ),
        ]
        cluster = build_setup_cluster(healthy_setups, member_sessions=["S1", "S2"], label="healthy cluster")

        healthy_distance = compute_setup_distance(healthy_setups[0], cluster)
        outlier_distance = compute_setup_distance(
            SimpleNamespace(
                front_pushrod_mm=-20.0,
                rear_pushrod_mm=-18.0,
                front_heave_nmm=20.0,
                rear_third_nmm=800.0,
                front_torsion_od_mm=14.8,
                rear_spring_nmm=220.0,
                front_arb_blade=4,
                rear_arb_blade=1,
                front_camber_deg=-1.5,
                rear_camber_deg=-1.0,
                front_toe_mm=0.4,
                rear_toe_mm=0.8,
                brake_bias_pct=50.0,
                diff_preload_nm=45.0,
            ),
            cluster,
        )

        self.assertLess(healthy_distance.distance_score, outlier_distance.distance_score)
        self.assertTrue(outlier_distance.outlier_parameters)


if __name__ == "__main__":
    unittest.main()
