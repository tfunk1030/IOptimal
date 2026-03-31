"""Tests for Sprint 4 — output/search_report.py

Tests the analysis and reporting module: sensitivity analysis,
Pareto frontier extraction, setup clustering, diff reports, and
the full report generator.
"""

from __future__ import annotations

import math
import pytest
from dataclasses import field
from unittest.mock import MagicMock, patch

from solver.objective import (
    CandidateEvaluation,
    ObjectiveBreakdown,
    PhysicsResult,
    PlatformRisk,
    DriverMismatch,
    TelemetryUncertainty,
    EnvelopePenalty,
)
from output.search_report import (
    SensitivityRow,
    SensitivityMatrix,
    ParetoPoint,
    CandidateCluster,
    compute_sensitivity,
    extract_pareto_frontier,
    cluster_candidates,
    format_sensitivity,
    format_pareto,
    format_clusters,
    format_diff_report,
    format_vetoed_summary,
    generate_search_report,
    _compute_pareto_2d,
    _kmeans,
)


# ── Test helpers ──────────────────────────────────────────────────

def _make_eval(
    score_offset: float = 0.0,
    family: str = "test",
    vetoed: bool = False,
    n_penalties: int = 0,
    lap_gain: float = 10.0,
    platform_risk: float = 2.0,
    lltd: float = 0.52,
    params: dict | None = None,
) -> CandidateEvaluation:
    """Build a minimal CandidateEvaluation for testing."""
    default_params = {
        "wing_angle_deg": 17.0,
        "front_heave_spring_nmm": 50.0 + score_offset,
        "rear_third_spring_nmm": 450.0,
        "rear_spring_rate_nmm": 160.0,
        "front_torsion_od_mm": 14.34,
        "front_camber_deg": -3.0,
        "rear_camber_deg": -2.0,
        "front_arb_blade": 3.0,
        "rear_arb_blade": 3.0,
        "front_ls_comp": 7.0,
        "front_ls_rbd": 7.0,
        "front_hs_comp": 5.0,
        "front_hs_rbd": 5.0,
        "rear_ls_comp": 6.0,
        "rear_ls_rbd": 7.0,
        "rear_hs_comp": 3.0,
        "rear_hs_rbd": 3.0,
        "brake_bias_pct": 56.0,
        "diff_preload_nm": 20.0,
    }
    if params:
        default_params.update(params)

    bd = ObjectiveBreakdown(
        lap_gain_ms=lap_gain + score_offset * 0.5,
        platform_risk=PlatformRisk(bottoming_risk_ms=platform_risk),
        driver_mismatch=DriverMismatch(),
        telemetry_uncertainty=TelemetryUncertainty(missing_signal_ms=15.0),
        envelope_penalty=EnvelopePenalty(),
    )

    physics = PhysicsResult(
        front_excursion_mm=12.0,
        rear_excursion_mm=8.0,
        front_bottoming_margin_mm=7.0,
        rear_bottoming_margin_mm=34.0,
        stall_margin_mm=3.0,
        lltd=lltd,
        lltd_error=abs(lltd - 0.52),
        zeta_ls_front=0.88,
        zeta_ls_rear=0.30,
        zeta_hs_front=0.45,
        zeta_hs_rear=0.14,
    )

    penalties = [f"penalty_{i}" for i in range(n_penalties)]
    veto_reasons = ["test veto"] if vetoed else []

    return CandidateEvaluation(
        params=default_params,
        family=family,
        breakdown=bd,
        physics=physics,
        hard_vetoed=vetoed,
        veto_reasons=veto_reasons,
        soft_penalties=penalties,
    )


# ── Pareto frontier tests ────────────────────────────────────────

class TestParetoFrontier:
    def test_pareto_empty(self):
        result = extract_pareto_frontier([])
        assert result == {}

    def test_pareto_all_vetoed(self):
        evals = [_make_eval(vetoed=True) for _ in range(5)]
        result = extract_pareto_frontier(evals)
        assert result == {}

    def test_pareto_identifies_frontier(self):
        """Candidates on the frontier should dominate non-frontier."""
        evals = [
            _make_eval(score_offset=10, lap_gain=20, platform_risk=1, family="fast_safe"),
            _make_eval(score_offset=5, lap_gain=15, platform_risk=0.5, family="moderate_safer"),
            _make_eval(score_offset=0, lap_gain=10, platform_risk=5, family="slow_risky"),
            _make_eval(score_offset=2, lap_gain=25, platform_risk=10, family="fastest_riskiest"),
        ]
        frontiers = extract_pareto_frontier(evals, top_n=10)

        assert "gain_vs_risk" in frontiers
        frontier = frontiers["gain_vs_risk"]
        assert len(frontier) >= 2

        # The frontier should include the fast_safe and fastest_riskiest
        frontier_families = {p.candidate.family for p in frontier}
        assert "fastest_riskiest" in frontier_families  # highest gain
        assert "moderate_safer" in frontier_families  # lowest risk with decent gain

    def test_pareto_2d_trivial(self):
        """A single point is always on the frontier."""
        p = ParetoPoint(
            candidate=_make_eval(),
            lap_gain_ms=10.0,
            platform_risk_ms=2.0,
            robustness_score=1.0,
        )
        result = _compute_pareto_2d(
            [p],
            key_maximize=lambda pp: pp.lap_gain_ms,
            key_minimize=lambda pp: pp.platform_risk_ms,
        )
        assert len(result) == 1

    def test_pareto_dominated_removed(self):
        """A dominated point should not be on the frontier."""
        # p1 dominates p2 (better on both axes)
        p1 = ParetoPoint(_make_eval(family="dominant"), 20.0, 1.0, 1.0)
        p2 = ParetoPoint(_make_eval(family="dominated"), 15.0, 5.0, 1.0)
        result = _compute_pareto_2d(
            [p1, p2],
            key_maximize=lambda pp: pp.lap_gain_ms,
            key_minimize=lambda pp: pp.platform_risk_ms,
        )
        families = {p.candidate.family for p in result}
        assert "dominant" in families
        # p2 is dominated by p1 (worse gain AND worse risk)
        assert "dominated" not in families


# ── Clustering tests ─────────────────────────────────────────────

class TestClustering:
    def test_kmeans_basic(self):
        """K-means should separate two clearly distinct groups."""
        import numpy as np
        # Two clusters: one near [0,0] and one near [1,1]
        X = np.array([
            [0.1, 0.1], [0.0, 0.2], [0.2, 0.0],
            [0.9, 0.9], [1.0, 0.8], [0.8, 1.0],
        ])
        labels = _kmeans(X, k=2, seed=42)
        # Points 0-2 should be in one cluster, 3-5 in another
        assert labels[0] == labels[1] == labels[2]
        assert labels[3] == labels[4] == labels[5]
        assert labels[0] != labels[3]

    def test_cluster_too_few_candidates(self):
        """Should handle gracefully when not enough candidates."""
        evals = [_make_eval(score_offset=i) for i in range(3)]
        # Mock a minimal space
        space = MagicMock()
        space.dimensions = []
        result = cluster_candidates(evals, space, n_clusters=4, top_n=3)
        # Should still produce something (single fallback cluster)
        # or empty (no dimensions)
        assert isinstance(result, list)

    def test_cluster_produces_distinct_groups(self):
        """Candidates with very different params should end up in different clusters."""
        # Soft setups
        soft_evals = [
            _make_eval(
                score_offset=i,
                family="soft",
                params={"front_heave_spring_nmm": 30.0, "rear_third_spring_nmm": 350.0,
                        "front_arb_blade": 1.0, "diff_preload_nm": 10.0},
            )
            for i in range(10)
        ]
        # Stiff setups
        stiff_evals = [
            _make_eval(
                score_offset=i,
                family="stiff",
                params={"front_heave_spring_nmm": 90.0, "rear_third_spring_nmm": 550.0,
                        "front_arb_blade": 5.0, "diff_preload_nm": 40.0},
            )
            for i in range(10)
        ]
        all_evals = soft_evals + stiff_evals

        # Create a mock space with the dimensions we need
        from solver.legal_space import SearchDimension
        dims = [
            SearchDimension("front_heave_spring_nmm", None, "continuous", "A", 20.0, 100.0, 10.0),
            SearchDimension("rear_third_spring_nmm", None, "continuous", "A", 300.0, 600.0, 10.0),
            SearchDimension("front_arb_blade", None, "ordinal", "A", 1.0, 5.0, 1.0,
                            [1.0, 2.0, 3.0, 4.0, 5.0]),
            SearchDimension("diff_preload_nm", None, "continuous", "A", 5.0, 50.0, 5.0),
        ]
        space = MagicMock()
        space.dimensions = dims

        clusters = cluster_candidates(all_evals, space, n_clusters=2, top_n=20)
        assert len(clusters) == 2

        # Each cluster should have members from roughly the same group
        for cluster in clusters:
            families = {m.family for m in cluster.members}
            # Clusters should be fairly homogeneous
            assert len(families) <= 2


# ── Sensitivity tests ────────────────────────────────────────────

class TestSensitivity:
    def test_sensitivity_matrix_ranking(self):
        """Rows should be sorted by score_range descending."""
        matrix = SensitivityMatrix(
            candidate_family="test",
            candidate_score=10.0,
            rows=[
                SensitivityRow("a", 1.0, 2.0, 10.0, 12.0, 5.0),
                SensitivityRow("b", 1.0, 2.0, 10.0, 20.0, 15.0),
                SensitivityRow("c", 1.0, 2.0, 10.0, 11.0, 2.0),
            ],
        )
        ranked = matrix.ranked_rows
        assert ranked[0].param_name == "b"
        assert ranked[1].param_name == "a"
        assert ranked[2].param_name == "c"


# ── Diff report tests ───────────────────────────────────────────

class TestDiffReport:
    def test_diff_report_shows_changes(self):
        """Diff report should include parameter names and deltas."""
        baseline = {
            "front_heave_spring_nmm": 50.0,
            "rear_third_spring_nmm": 450.0,
            "front_arb_blade": 3.0,
        }
        candidate = _make_eval(
            params={"front_heave_spring_nmm": 60.0, "front_arb_blade": 5.0}
        )
        report = format_diff_report(candidate, baseline, rank=1)
        assert "Front Heave Spring" in report
        assert "DIFF REPORT" in report

    def test_diff_report_with_baseline_eval(self):
        """Should show score delta vs baseline when provided."""
        baseline = {"front_heave_spring_nmm": 50.0}
        baseline_eval = _make_eval(score_offset=0)
        candidate = _make_eval(score_offset=5, family="improved")
        report = format_diff_report(candidate, baseline, baseline_eval, rank=1)
        assert "vs Baseline" in report


# ── Vetoed summary tests ────────────────────────────────────────

class TestVetoedSummary:
    def test_empty_when_no_vetoes(self):
        evals = [_make_eval() for _ in range(3)]
        result = format_vetoed_summary(evals)
        assert result == ""

    def test_shows_veto_reasons(self):
        evals = [
            _make_eval(vetoed=True),
            _make_eval(vetoed=True),
            _make_eval(vetoed=False),
        ]
        result = format_vetoed_summary(evals)
        assert "VETOED CANDIDATES" in result
        assert "2 candidates" in result


# ── Format functions smoke tests ─────────────────────────────────

class TestFormatFunctions:
    def test_format_sensitivity_output(self):
        matrix = SensitivityMatrix(
            candidate_family="test",
            candidate_score=10.0,
            rows=[
                SensitivityRow("front_heave_spring_nmm", 50.0, 60.0, 10.0, 12.0, 5.0),
                SensitivityRow("front_arb_blade", 3.0, 5.0, 10.0, 11.0, 2.0),
            ],
        )
        result = format_sensitivity([matrix])
        assert "PARAMETER SENSITIVITY" in result
        assert "front_heave_spring_nmm" in result

    def test_format_pareto_output(self):
        evals = [_make_eval(score_offset=i, family=f"f{i}") for i in range(5)]
        frontiers = extract_pareto_frontier(evals, top_n=5)
        if frontiers:
            result = format_pareto(frontiers)
            assert "PARETO" in result

    def test_format_clusters_output(self):
        cluster = CandidateCluster(
            cluster_id=0,
            label="Soft-Mechanical",
            members=[_make_eval()],
            centroid={"front_heave_spring_nmm": 40.0},
            avg_score=10.0,
            best_score=12.0,
            distinguishing_features=["front_heave lower (40.0)"],
        )
        result = format_clusters([cluster])
        assert "SETUP LANDSCAPE" in result
        assert "Soft-Mechanical" in result


# ── Integration test ─────────────────────────────────────────────

class TestFullReport:
    def test_generate_search_report_smoke(self):
        """Full report generator should not crash with synthetic data."""
        evals = [
            _make_eval(score_offset=i, family=f"fam_{i}", n_penalties=i % 3)
            for i in range(20)
        ] + [
            _make_eval(vetoed=True, family="bad")
        ]

        ls_result = MagicMock()
        ls_result.all_evaluations = evals
        ls_result.best_robust = evals[19]
        ls_result.best_aggressive = evals[19]
        ls_result.best_weird = evals[10]

        baseline = {
            "wing_angle_deg": 17.0,
            "front_heave_spring_nmm": 50.0,
            "rear_third_spring_nmm": 450.0,
            "rear_spring_rate_nmm": 160.0,
            "front_torsion_od_mm": 14.34,
            "front_camber_deg": -3.0,
            "rear_camber_deg": -2.0,
            "front_arb_blade": 3.0,
            "rear_arb_blade": 3.0,
            "front_ls_comp": 7.0,
            "rear_ls_comp": 6.0,
            "front_hs_comp": 5.0,
            "rear_hs_comp": 3.0,
            "brake_bias_pct": 56.0,
            "diff_preload_nm": 20.0,
        }

        # Mock space and objective to avoid needing real car model
        from solver.legal_space import SearchDimension
        dims = [
            SearchDimension("front_heave_spring_nmm", None, "continuous", "A", 20.0, 100.0, 10.0),
            SearchDimension("rear_third_spring_nmm", None, "continuous", "A", 300.0, 600.0, 10.0),
            SearchDimension("front_arb_blade", None, "ordinal", "A", 1.0, 5.0, 1.0,
                            [1.0, 2.0, 3.0, 4.0, 5.0]),
            SearchDimension("rear_arb_blade", None, "ordinal", "A", 1.0, 5.0, 1.0,
                            [1.0, 2.0, 3.0, 4.0, 5.0]),
            SearchDimension("diff_preload_nm", None, "continuous", "A", 5.0, 50.0, 5.0),
            SearchDimension("brake_bias_pct", None, "continuous", "A", 40.0, 60.0, 0.5),
        ]
        mock_space = MagicMock()
        mock_space.dimensions = dims
        mock_space._dim_map = {d.name: d for d in dims}

        # Mock objective that returns the eval as-is (sensitivity analysis
        # calls evaluate_batch)
        mock_obj = MagicMock()
        mock_obj.evaluate_batch.return_value = [_make_eval() for _ in range(10)]

        mock_car = MagicMock()

        # Patch compute_perch_offsets to avoid car model dependency
        with patch("output.search_report.compute_perch_offsets", return_value={}):
            report = generate_search_report(
                ls_result=ls_result,
                baseline_params=baseline,
                space=mock_space,
                objective=mock_obj,
                car=mock_car,
                sensitivity_top_n=2,
                diff_top_n=3,
                cluster_count=2,
            )

        assert "ANALYSIS REPORT" in report
        assert "PARAMETER SENSITIVITY" in report
        assert "PARETO" in report
        assert "DIFF REPORT" in report
        assert "VETOED" in report
        assert "END OF ANALYSIS REPORT" in report
