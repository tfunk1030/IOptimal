import unittest

import pytest

from validation.observation_mapping import normalize_setup_to_canonical_params, resolve_validation_signals
from validation.run_validation import build_validation_report

_SKIP_REASON = "BMW/Sebring observation data not available in this checkout"


def _load_validation_report() -> dict:
    """Load validation report, skipping test if data is missing."""
    try:
        return build_validation_report()
    except (RuntimeError, FileNotFoundError) as exc:
        pytest.skip(f"{_SKIP_REASON}: {exc}")


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
        # front_rh_std_mm is not present → falls through to front_heave_defl_p99_mm
        self.assertEqual(resolved["front_excursion_mm"]["value"], 79.5)
        self.assertIn("front_heave_defl_p99_mm", resolved["front_excursion_mm"]["fields"])
        self.assertEqual(resolved["braking_pitch_deg"]["source"], "fallback")
        self.assertEqual(resolved["front_lock_p95"]["source"], "fallback")
        self.assertEqual(resolved["rear_power_slip_p95"]["source"], "fallback")
        self.assertEqual(resolved["front_pressure_hot_kpa"]["source"], "fallback")
        self.assertEqual(resolved["front_pressure_hot_kpa"]["value"], 184.0)
        self.assertEqual(resolved["rear_pressure_hot_kpa"]["source"], "missing")

    def test_resolve_validation_signals_front_rh_std_preferred_over_heave_defl(self) -> None:
        """front_rh_std_mm is tier-1 fallback for front_excursion_mm (before heave_defl)."""
        resolved_with_std = resolve_validation_signals(
            {
                "front_rh_std_mm": 5.5,
                "front_heave_defl_p99_mm": 79.5,
            }
        )
        resolved_with_heave_only = resolve_validation_signals(
            {
                "front_heave_defl_p99_mm": 79.5,
            }
        )
        resolved_with_direct = resolve_validation_signals(
            {
                "front_rh_excursion_measured_mm": 22.3,
                "front_rh_std_mm": 5.5,
                "front_heave_defl_p99_mm": 79.5,
            }
        )

        # When both std and heave are present, std is chosen (tier-1 fallback).
        # front_rh_std_mm is scaled by 3.0 to estimate front_excursion_mm.
        self.assertEqual(resolved_with_std["front_excursion_mm"]["source"], "fallback")
        self.assertAlmostEqual(resolved_with_std["front_excursion_mm"]["value"], 16.5, places=1)  # 5.5 * 3.0
        self.assertIn("front_rh_std_mm", resolved_with_std["front_excursion_mm"]["fields"])

        # When only heave is present, heave is used (tier-2 fallback)
        self.assertEqual(resolved_with_heave_only["front_excursion_mm"]["source"], "fallback")
        self.assertEqual(resolved_with_heave_only["front_excursion_mm"]["value"], 79.5)

        # Direct always wins even when std and heave are present
        self.assertEqual(resolved_with_direct["front_excursion_mm"]["source"], "direct")
        self.assertEqual(resolved_with_direct["front_excursion_mm"]["value"], 22.3)

    def test_build_validation_report_recomputes_current_bmw_sebring_evidence(self) -> None:
        report = _load_validation_report()
        bmw = report["bmw_sebring"]
        tiers = {(row["car"], row["track"]): row["confidence_tier"] for row in report["support_matrix"]}

        self.assertGreaterEqual(bmw["samples"], 70)
        self.assertGreaterEqual(bmw["non_vetoed_samples"], 70)
        self.assertLessEqual(bmw["non_vetoed_samples"], bmw["samples"])
        self.assertEqual(bmw["samples"], len(bmw["rows"]))
        self.assertEqual(tiers[("bmw", "Sebring International Raceway")], "calibrated")
        self.assertEqual(tiers[("ferrari", "Sebring International Raceway")], "partial")
        self.assertEqual(tiers[("cadillac", "Silverstone Circuit")], "exploratory")
        self.assertLess(abs(float(bmw["score_correlation"]["pearson_r_non_vetoed"])), 0.3)
        self.assertLess(abs(float(bmw["score_correlation"]["spearman_r_non_vetoed"])), 0.4)
        self.assertLess(float(bmw["score_correlation"]["spearman_r_non_vetoed"]), 0.0)
        self.assertEqual(bmw["claim_audit"]["objective_ranking"]["status"], "unverified")
        self.assertIn("objective_recalibration", bmw)
        self.assertIn("track_aware_spearman_r", bmw["objective_recalibration"])
        self.assertIn("trackless_spearman_r", bmw["objective_recalibration"])
        self.assertIn("track_aware_holdout_mean_spearman_r", bmw["objective_recalibration"])
        self.assertIn("track_aware_holdout_worst_spearman_r", bmw["objective_recalibration"])
        self.assertLess(float(bmw["objective_recalibration"]["track_aware_spearman_r"]), 0.0)
        self.assertLess(float(bmw["objective_recalibration"]["track_aware_holdout_mean_spearman_r"]), 0.0)
        # Regression gate: worst holdout fold must not exceed +0.30.
        # As of 2026-03-29 worst fold is +0.248; this gate catches further regression
        # without blocking the current known-bad state. Target is eventually < 0.0.
        self.assertLess(
            float(bmw["objective_recalibration"]["track_aware_holdout_worst_spearman_r"]),
            0.30,
            "Worst holdout Spearman regressed above +0.30 — objective hardening is needed",
        )
        self.assertFalse(bmw["objective_recalibration"]["recommended_runtime_profile"]["auto_apply"])
        self.assertTrue(all("error" not in row for row in bmw["rows"]))

    def test_bmw_signal_quality_gates(self) -> None:
        """Regression gates on per-signal fallback and missing rates for BMW/Sebring.

        These gates do NOT require perfect direct coverage — the corpus contains older
        observations extracted before newer signals were added. Gates catch *regressions*
        where coverage degrades further compared to current known-good baseline.

        Current baseline (2026-03-30):
          front_excursion_mm: ~3% missing (after front_rh_std_mm added as tier-1 fallback)
          braking_pitch_deg / front_lock_p95 / rear_power_slip_p95: ~24% missing
          front_pressure_hot_kpa / rear_pressure_hot_kpa: ~24% missing

        Gate thresholds are set 10pp above current baseline to allow headroom while
        still catching meaningful regressions.
        """
        report = _load_validation_report()
        bmw = report["bmw_sebring"]
        total = bmw["samples"]
        signal_usage = bmw["signal_usage"]

        # Signals that must not regress past 15% missing
        # front_excursion_mm: improved to ~3% missing via front_rh_std_mm fallback
        low_missing_signals = ["front_excursion_mm"]
        for sig in low_missing_signals:
            counts = signal_usage.get(sig, {})
            missing = counts.get("missing", 0)
            missing_rate = missing / total if total > 0 else 0.0
            self.assertLess(
                missing_rate,
                0.15,
                f"{sig}: missing rate {missing_rate:.1%} regressed past 15% gate "
                f"({missing}/{total} samples missing — front_rh_std_mm fallback may be broken)",
            )

        # Signals with structural ~24% missing (old corpus, no backfill possible without IBT)
        # Gate: must not exceed 35% missing (10pp headroom above current ~24%)
        moderate_missing_signals = [
            "braking_pitch_deg",
            "front_lock_p95",
            "rear_power_slip_p95",
            "front_pressure_hot_kpa",
            "rear_pressure_hot_kpa",
        ]
        for sig in moderate_missing_signals:
            counts = signal_usage.get(sig, {})
            missing = counts.get("missing", 0)
            missing_rate = missing / total if total > 0 else 0.0
            self.assertLess(
                missing_rate,
                0.35,
                f"{sig}: missing rate {missing_rate:.1%} regressed past 35% gate "
                f"({missing}/{total} — extractor may have broken {sig} extraction)",
            )

        # All key signals must have at least 50% non-missing (direct + fallback)
        key_signals = list(signal_usage.keys())
        for sig in key_signals:
            counts = signal_usage.get(sig, {})
            missing = counts.get("missing", 0)
            non_missing = total - missing
            coverage = non_missing / total if total > 0 else 0.0
            self.assertGreaterEqual(
                coverage,
                0.50,
                f"{sig}: coverage {coverage:.1%} dropped below 50% — "
                f"signal extraction may be broken for this metric",
            )
