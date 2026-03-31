import unittest

from car_model.cars import get_car
from solver.objective import ObjectiveFunction, PhysicsResult
from validation.objective_calibration import (
    ScoredObservation,
    build_calibration_report,
    search_weight_profiles,
)


class ObjectiveCalibrationTests(unittest.TestCase):
    def test_bmw_sebring_track_aware_single_lap_safe_uses_runtime_guard(self) -> None:
        track_aware = ObjectiveFunction(
            get_car("bmw"),
            {"track_name": "Sebring International Raceway"},
            scenario_profile="single_lap_safe",
        )
        trackless = ObjectiveFunction(
            get_car("bmw"),
            None,
            scenario_profile="single_lap_safe",
        )

        guarded = track_aware._new_breakdown()
        defaulted = trackless._new_breakdown()

        self.assertEqual(guarded.w_lap_gain, 0.25)
        self.assertEqual(guarded.w_envelope, 0.40)
        self.assertEqual(defaulted.w_lap_gain, 1.0)
        # single_lap_safe scenario profile sets w_envelope=0.55 (updated 2026-03-29;
        # previously expected 0.70 which was the ObjectiveBreakdown default before
        # scenario_profiles.py explicitly set this field to 0.55).
        self.assertEqual(defaulted.w_envelope, 0.55)

    def test_heave_realism_moves_to_envelope_not_raw_lap_gain(self) -> None:
        objective = ObjectiveFunction(
            get_car("bmw"),
            {"track_name": "Sebring International Raceway"},
            scenario_profile="single_lap_safe",
        )
        physics = PhysicsResult()
        nominal_params = {"front_heave_spring_nmm": 50.0, "rear_third_spring_nmm": 440.0}
        extreme_params = {"front_heave_spring_nmm": 300.0, "rear_third_spring_nmm": 440.0}

        nominal_gain = objective._estimate_lap_gain(nominal_params, physics)
        extreme_gain = objective._estimate_lap_gain(extreme_params, physics)
        nominal_penalty = objective._compute_envelope_penalty(nominal_params, physics, [])
        extreme_penalty = objective._compute_envelope_penalty(extreme_params, physics, [])

        self.assertEqual(nominal_gain, extreme_gain)
        self.assertGreater(extreme_penalty.total_ms, nominal_penalty.total_ms)

    def test_bmw_sebring_track_aware_single_lap_safe_halves_camber_penalty(self) -> None:
        track_aware = ObjectiveFunction(
            get_car("bmw"),
            {"track_name": "Sebring International Raceway"},
            scenario_profile="single_lap_safe",
        )
        trackless = ObjectiveFunction(
            get_car("bmw"),
            None,
            scenario_profile="single_lap_safe",
        )

        guarded = track_aware._camber_lap_penalty_ms(-2.0, -1.0)
        defaulted = trackless._camber_lap_penalty_ms(-2.0, -1.0)

        self.assertEqual(guarded, defaulted * 0.5)

    def test_build_calibration_report_includes_track_modes(self) -> None:
        report = build_calibration_report(include_search=False)
        track_aware = report["modes"]["track_aware"]

        self.assertGreaterEqual(report["bmw_sebring_samples"], 70)
        self.assertEqual(report["bmw_sebring_samples"], track_aware["samples"])
        self.assertEqual(track_aware["samples"], track_aware["non_vetoed_samples"] + track_aware["vetoed_samples"])
        self.assertIn("track_aware", report["modes"])
        self.assertIn("trackless", report["modes"])
        self.assertIn("ablations", track_aware)
        self.assertIn("term_correlations", report["modes"]["trackless"])
        self.assertIn("lap_gain_component_correlations", track_aware)
        self.assertIn("lap_gain_component_ablations", track_aware)
        self.assertIn("holdout_validation", track_aware)
        self.assertLess(track_aware["score_correlation"]["spearman_r"], 0.0)
        self.assertFalse(report["recommended_runtime_profile"]["auto_apply"])

    # ──────────────────────────────────────────────────────────────────────────
    # Regression threshold gates (Phase 1 — BMW/Sebring objective hardening)
    #
    # These tests lock the current holdout stability baseline so that future
    # objective edits cannot silently flip the sign or worsen holdout performance.
    #
    # Thresholds are set at current measured values + a small regression buffer.
    # They are NOT "this is good enough" claims — they are "don't make it worse"
    # guards. Tighten them as the objective improves.
    #
    # Current baselines (measured 2026-03-31):
    #   in-sample spearman_r    : -0.181
    #   mean holdout spearman_r : -0.172  (5-fold CV, non-vetoed BMW/Sebring)
    #   worst-fold holdout      : +0.248  (fold 1 flips positive — known weakness)
    #   best-fold holdout       : -0.543
    # ──────────────────────────────────────────────────────────────────────────

    def test_insample_correlation_stays_negative(self) -> None:
        """In-sample Spearman correlation must remain negative.

        A positive in-sample correlation means the objective ranks worse setups
        higher than better ones. This gate fires if that regression occurs.
        """
        report = build_calibration_report(include_search=False)
        track_aware = report["modes"]["track_aware"]
        r = track_aware["score_correlation"]["spearman_r"]
        self.assertLess(
            r,
            0.0,
            f"In-sample Spearman r={r:.4f} flipped positive — objective regression detected.",
        )

    def test_holdout_mean_spearman_stays_negative(self) -> None:
        """Mean 5-fold holdout Spearman must remain negative.

        The current mean is -0.172. If it drifts to ≥ 0.0 the objective has
        lost predictive directionality on out-of-sample BMW/Sebring data.
        """
        report = build_calibration_report(include_search=False)
        hv = report["modes"]["track_aware"]["holdout_validation"]
        mean_r = hv["current_runtime"]["mean_spearman_r"]
        self.assertLess(
            mean_r,
            0.0,
            f"Mean holdout Spearman r={mean_r:.4f} is no longer negative — "
            f"holdout directionality lost.",
        )

    def test_holdout_worst_fold_regression_gate(self) -> None:
        """Worst-fold holdout Spearman must not worsen beyond +0.40.

        Fold 1 currently sits at +0.248 (a known weakness — not a calibrated
        signal). This gate prevents the worst fold from silently sliding further
        positive. Threshold is set at current (+0.248) + 0.15 regression buffer.

        Exit criterion from enhancementplan.md Phase 1:
            'Worst-fold holdout no longer flips strongly positive.'
        Tighten this threshold as the objective improves.
        """
        report = build_calibration_report(include_search=False)
        hv = report["modes"]["track_aware"]["holdout_validation"]
        worst_r = hv["current_runtime"]["worst_spearman_r"]
        threshold = 0.40
        self.assertLess(
            worst_r,
            threshold,
            f"Worst-fold holdout Spearman r={worst_r:.4f} exceeds regression "
            f"threshold of {threshold:.2f} — objective has gotten worse on fold 1.",
        )

    def test_holdout_best_fold_stays_meaningful(self) -> None:
        """Best-fold holdout Spearman must remain below -0.30.

        The best fold sits at -0.543. If it rises above -0.30 the objective
        has lost its strongest predictive signal on at least one data split.
        """
        report = build_calibration_report(include_search=False)
        hv = report["modes"]["track_aware"]["holdout_validation"]
        best_r = hv["current_runtime"]["best_spearman_r"]
        threshold = -0.30
        self.assertLess(
            best_r,
            threshold,
            f"Best-fold holdout Spearman r={best_r:.4f} is weaker than threshold "
            f"{threshold:.2f} — the objective's strongest fold has regressed.",
        )

    def test_search_weight_profiles_ignores_vetoed_rows(self) -> None:
        scored = [
            ScoredObservation(
                filename="a.json",
                lap_time_s=100.0,
                vetoed=False,
                total_score_ms=-10.0,
                lap_gain_ms=-10.0,
                platform_risk_ms=1.0,
                driver_mismatch_ms=0.0,
                telemetry_uncertainty_ms=0.0,
                envelope_penalty_ms=0.0,
                staleness_penalty_ms=0.0,
                empirical_penalty_ms=0.0,
                weighted_lap_gain_ms=-10.0,
                weighted_platform_ms=-1.0,
                weighted_driver_ms=0.0,
                weighted_uncertainty_ms=0.0,
                weighted_envelope_ms=0.0,
                weighted_staleness_ms=0.0,
                weighted_empirical_ms=0.0,
            ),
            ScoredObservation(
                filename="b.json",
                lap_time_s=101.0,
                vetoed=False,
                total_score_ms=-20.0,
                lap_gain_ms=-20.0,
                platform_risk_ms=2.0,
                driver_mismatch_ms=0.0,
                telemetry_uncertainty_ms=0.0,
                envelope_penalty_ms=0.0,
                staleness_penalty_ms=0.0,
                empirical_penalty_ms=0.0,
                weighted_lap_gain_ms=-20.0,
                weighted_platform_ms=-2.0,
                weighted_driver_ms=0.0,
                weighted_uncertainty_ms=0.0,
                weighted_envelope_ms=0.0,
                weighted_staleness_ms=0.0,
                weighted_empirical_ms=0.0,
            ),
            ScoredObservation(
                filename="c.json",
                lap_time_s=105.0,
                vetoed=True,
                total_score_ms=-1e9,
                lap_gain_ms=999.0,
                platform_risk_ms=999.0,
                driver_mismatch_ms=0.0,
                telemetry_uncertainty_ms=0.0,
                envelope_penalty_ms=0.0,
                staleness_penalty_ms=0.0,
                empirical_penalty_ms=0.0,
                weighted_lap_gain_ms=999.0,
                weighted_platform_ms=-999.0,
                weighted_driver_ms=0.0,
                weighted_uncertainty_ms=0.0,
                weighted_envelope_ms=0.0,
                weighted_staleness_ms=0.0,
                weighted_empirical_ms=0.0,
            ),
        ]

        result = search_weight_profiles(scored)

        self.assertIn("best_weights", result)
        self.assertLessEqual(result["best_spearman_r"], result["current_spearman_r"])


if __name__ == "__main__":
    unittest.main()
