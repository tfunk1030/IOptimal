import unittest

from validation.observation_mapping import normalize_setup_to_canonical_params, resolve_validation_signals
from validation.run_validation import build_validation_report


class ValidationReportingTests(unittest.TestCase):
    def test_normalize_setup_to_canonical_params_uses_registry_fields(self) -> None:
        params = normalize_setup_to_canonical_params(
            {
                "adapter_name": "bmw",
                "wing": 17,
                "front_heave_nmm": 50,
                "rear_third_nmm": 440,
                "rear_spring_nmm": 150,
                "torsion_bar_od_mm": 14.34,
                "front_pushrod": -26.0,
                "rear_pushrod": -22.0,
                "front_rh_static": 30.1,
                "rear_rh_static": 49.3,
                "front_camber_deg": -2.3,
                "rear_camber_deg": -1.8,
                "front_toe_mm": -0.5,
                "rear_toe_mm": 0.3,
                "front_arb_size": "Soft",
                "rear_arb_size": "Medium",
                "front_arb_blade": 1,
                "rear_arb_blade": 3,
                "brake_bias_pct": 46.2,
                "diff_preload_nm": 30,
                "diff_ramp_coast": 45,
                "diff_ramp_drive": 70,
                "tc_gain": 4,
                "tc_slip": 4,
                "fuel_level_l": 57.8,
                "gear_stack": "Short",
                "roof_light_color": "Orange",
                "dampers": {
                    "lf": {"ls_comp": 8, "ls_rbd": 8, "hs_comp": 6, "hs_rbd": 8, "hs_slope": 11},
                    "rf": {"ls_comp": 8, "ls_rbd": 8, "hs_comp": 6, "hs_rbd": 8, "hs_slope": 11},
                    "lr": {"ls_comp": 6, "ls_rbd": 7, "hs_comp": 6, "hs_rbd": 11, "hs_slope": 11},
                    "rr": {"ls_comp": 6, "ls_rbd": 7, "hs_comp": 6, "hs_rbd": 11, "hs_slope": 11},
                },
            }
        )

        self.assertEqual(params["front_heave_spring_nmm"], 50.0)
        self.assertEqual(params["rear_third_spring_nmm"], 440.0)
        self.assertEqual(params["front_torsion_od_mm"], 14.34)
        self.assertEqual(params["front_pushrod_offset_mm"], -26.0)
        self.assertEqual(params["rear_pushrod_offset_mm"], -22.0)
        self.assertEqual(params["front_rh_static_mm"], 30.1)
        self.assertEqual(params["rear_rh_static_mm"], 49.3)
        self.assertEqual(params["front_ls_comp"], 8.0)
        self.assertEqual(params["rear_hs_rbd"], 11.0)
        self.assertEqual(params["diff_ramp_option_idx"], 1)
        self.assertEqual(params["diff_ramp_angles"], "45/70")

    def test_resolve_validation_signals_prefers_direct_and_tracks_fallbacks(self) -> None:
        resolved = resolve_validation_signals(
            {
                "front_heave_travel_used_pct": 88.0,
                "front_heave_defl_p99_mm": 79.5,
                "pitch_range_deg": 1.6,
                "front_brake_pressure_peak_bar": 97.0,
                "tc_intervention_pct": 2.5,
                "body_slip_p95_deg": 4.1,
                "understeer_mean_deg": 1.2,
                "lf_pressure_kpa": 184.0,
                "rf_pressure_kpa": 182.0,
            }
        )

        self.assertEqual(resolved["front_heave_travel_used_pct"]["source"], "direct")
        self.assertEqual(resolved["front_excursion_mm"]["source"], "fallback")
        self.assertEqual(resolved["front_excursion_mm"]["value"], 79.5)
        self.assertEqual(resolved["braking_pitch_deg"]["source"], "fallback")
        self.assertEqual(resolved["front_lock_p95"]["source"], "fallback")
        self.assertEqual(resolved["rear_power_slip_p95"]["source"], "fallback")
        self.assertEqual(resolved["front_pressure_hot_kpa"]["source"], "fallback")
        self.assertEqual(resolved["front_pressure_hot_kpa"]["value"], 184.0)
        self.assertEqual(resolved["rear_pressure_hot_kpa"]["source"], "missing")

    def test_build_validation_report_recomputes_current_bmw_sebring_evidence(self) -> None:
        report = build_validation_report()
        bmw = report["bmw_sebring"]
        tiers = {(row["car"], row["track"]): row["confidence_tier"] for row in report["support_matrix"]}

        self.assertEqual(bmw["samples"], 73)
        self.assertEqual(bmw["non_vetoed_samples"], 72)
        self.assertEqual(tiers[("bmw", "Sebring International Raceway")], "calibrated")
        self.assertEqual(tiers[("ferrari", "Sebring International Raceway")], "partial")
        self.assertEqual(tiers[("cadillac", "Silverstone Circuit")], "exploratory")
        self.assertLess(abs(float(bmw["score_correlation"]["pearson_r_non_vetoed"])), 0.2)
        self.assertLess(abs(float(bmw["score_correlation"]["spearman_r_non_vetoed"])), 0.2)
        self.assertEqual(bmw["claim_audit"]["objective_ranking"]["status"], "unverified")
        self.assertTrue(all("error" not in row for row in bmw["rows"]))


if __name__ == "__main__":
    unittest.main()
