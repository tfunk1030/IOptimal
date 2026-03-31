"""Sprint 4 — Output + Analysis for legal-manifold search results.

Produces an interpretable, actionable report explaining WHY certain
regions of the setup space are fast. Includes:

1. Parameter sensitivity heatmaps — which clicks matter most
2. Pareto frontier — lap gain vs platform risk, lap gain vs robustness
3. Setup landscape clusters — distinct "philosophies" found
4. Full diff reports — parameter-by-parameter vs physics baseline
5. Vetoed candidate summary — what was rejected and why

Usage:
    from output.search_report import generate_search_report
    report = generate_search_report(ls_result, baseline_params, space, objective, car)
    print(report)
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field

from solver.legal_space import LegalSpace, compute_perch_offsets
from solver.objective import CandidateEvaluation, ObjectiveFunction

logger = logging.getLogger(__name__)

# ── Report dimensions ──────────────────────────────────────────────
W = 72  # report line width


def _hdr(title: str) -> str:
    pad = (W - len(title) - 2) // 2
    return "─" * pad + f" {title} " + "─" * (W - pad - len(title) - 2)


def _box_top(title: str) -> str:
    inner = W - 2
    t = f" {title} "
    pad = (inner - len(t)) // 2
    return "┌" + "─" * pad + t + "─" * (inner - pad - len(t)) + "┐"


def _box_bot() -> str:
    return "└" + "─" * (W - 2) + "┘"


def _full(text: str) -> str:
    """Full-width row inside a box."""
    if len(text) > W - 2:
        text = text[: W - 5] + "..."
    return "│" + text.ljust(W - 2) + "│"


def _blank() -> str:
    return "│" + " " * (W - 2) + "│"


# ── 1. Parameter Sensitivity ──────────────────────────────────────

@dataclass
class SensitivityRow:
    """Sensitivity of one parameter for one candidate."""
    param_name: str
    current_value: float
    best_neighbor_value: float
    score_at_current: float
    score_at_best_neighbor: float
    score_range: float  # max_score - min_score across sweep
    scores_by_step: list[tuple[float, float]] = field(default_factory=list)
    # (value, score) pairs across the sweep


@dataclass
class SensitivityMatrix:
    """Sensitivity analysis for a single candidate."""
    candidate_family: str
    candidate_score: float
    rows: list[SensitivityRow] = field(default_factory=list)

    @property
    def ranked_rows(self) -> list[SensitivityRow]:
        """Rows sorted by score_range descending (most sensitive first)."""
        return sorted(self.rows, key=lambda r: r.score_range, reverse=True)


def compute_sensitivity(
    candidate: CandidateEvaluation,
    space: LegalSpace,
    objective: ObjectiveFunction,
    car,
    steps: int = 3,
) -> SensitivityMatrix:
    """Compute how each parameter affects score when varied ±steps.

    For each Tier A dimension, holds all other params fixed at the
    candidate's values and sweeps this dimension ±steps. Records
    the score at each step to build a sensitivity profile.

    Args:
        candidate: The candidate to analyze.
        space: Legal search space (for dimension info).
        objective: Objective function for scoring.
        car: Car model (for perch computation).
        steps: How many resolution steps to vary (default 3).

    Returns:
        SensitivityMatrix with one row per parameter.
    """
    matrix = SensitivityMatrix(
        candidate_family=candidate.family,
        candidate_score=candidate.score,
    )

    base_params = dict(candidate.params)
    base_score = candidate.score

    for dim in space.dimensions:
        if dim.name not in base_params:
            continue

        center_val = base_params[dim.name]
        sweep_points: list[tuple[float, float]] = []  # (value, score)

        # Generate sweep values
        if dim.discrete_values is not None:
            try:
                idx = min(
                    range(len(dim.discrete_values)),
                    key=lambda i: abs(dim.discrete_values[i] - center_val),
                )
            except ValueError:
                continue
            for delta in range(-steps, steps + 1):
                new_idx = idx + delta
                if 0 <= new_idx < len(dim.discrete_values):
                    val = dim.discrete_values[new_idx]
                    sweep_points.append((val, 0.0))
        elif dim.resolution > 0:
            for delta in range(-steps, steps + 1):
                new_val = center_val + delta * dim.resolution
                if dim.lo - 1e-9 <= new_val <= dim.hi + 1e-9:
                    sweep_points.append((dim.snap(new_val), 0.0))
        else:
            continue

        if len(sweep_points) <= 1:
            continue

        # Evaluate each sweep point
        batch: list[dict[str, float]] = []
        for val, _ in sweep_points:
            trial = dict(base_params)
            trial[dim.name] = val
            perches = compute_perch_offsets(trial, car)
            trial.update(perches)
            batch.append(trial)

        evals = objective.evaluate_batch(batch, layer=4, family="sensitivity")
        scored_points: list[tuple[float, float]] = []
        for (val, _), ev in zip(sweep_points, evals):
            s = ev.score if not ev.hard_vetoed else -1e9
            scored_points.append((val, s))

        # Filter out vetoed points for range calculation
        viable_scores = [s for _, s in scored_points if s > -1e6]
        if not viable_scores:
            continue

        score_range = max(viable_scores) - min(viable_scores)
        best_val, best_score = max(scored_points, key=lambda x: x[1])

        matrix.rows.append(SensitivityRow(
            param_name=dim.name,
            current_value=center_val,
            best_neighbor_value=best_val,
            score_at_current=base_score,
            score_at_best_neighbor=best_score,
            score_range=score_range,
            scores_by_step=scored_points,
        ))

    return matrix


def format_sensitivity(matrices: list[SensitivityMatrix]) -> str:
    """Format sensitivity matrices as a text report section."""
    lines: list[str] = []
    lines.append("")
    lines.append(_box_top("PARAMETER SENSITIVITY ANALYSIS"))
    lines.append(_full("  Which clicks matter most for each top candidate?"))
    lines.append(_full("  Score range = how much the score changes when"))
    lines.append(_full("  sweeping ±3 steps with all else held fixed."))
    lines.append(_blank())

    for matrix in matrices:
        lines.append(_full(
            f"  Candidate: {matrix.candidate_family} "
            f"(score={matrix.candidate_score:+.1f}ms)"
        ))
        lines.append(_full(
            f"  {'Parameter':<28} {'Current':>8} {'Range':>8} {'Best@':>8} {'Δ':>6}"
        ))
        lines.append(_full("  " + "─" * 62))

        for row in matrix.ranked_rows:  # ALL parameters, ranked by sensitivity
            delta = row.score_at_best_neighbor - row.score_at_current
            delta_str = f"{delta:+.1f}" if abs(delta) > 0.05 else "  ≈0"

            # Format current value
            if row.current_value == int(row.current_value):
                cur_str = f"{int(row.current_value)}"
            else:
                cur_str = f"{row.current_value:.1f}"

            # Format best neighbor value
            if row.best_neighbor_value == int(row.best_neighbor_value):
                best_str = f"{int(row.best_neighbor_value)}"
            else:
                best_str = f"{row.best_neighbor_value:.1f}"

            # Sensitivity bar (visual)
            bar_len = min(12, max(0, int(row.score_range / 2)))
            bar = "█" * bar_len + "░" * (12 - bar_len)

            lines.append(_full(
                f"  {row.param_name:<28} {cur_str:>8} "
                f"{row.score_range:>6.1f}ms {best_str:>7} {delta_str:>6}"
            ))

        lines.append(_blank())

    # Cross-candidate summary: which params matter most overall
    all_sensitivities: dict[str, list[float]] = defaultdict(list)
    for matrix in matrices:
        for row in matrix.rows:
            all_sensitivities[row.param_name].append(row.score_range)

    if all_sensitivities:
        avg_sens = {
            name: sum(vals) / len(vals)
            for name, vals in all_sensitivities.items()
        }
        ranked = sorted(avg_sens.items(), key=lambda x: x[1], reverse=True)

        lines.append(_full("  ── Global sensitivity ranking (avg across candidates) ──"))
        for i, (name, avg) in enumerate(ranked, 1):  # ALL parameters
            bar_len = min(20, max(0, int(avg / 1.5)))
            bar = "█" * bar_len
            lines.append(_full(f"  {i:2d}. {name:<28} {avg:>5.1f}ms  {bar}"))

    lines.append(_box_bot())
    return "\n".join(lines)


# ── 2. Pareto Frontier ────────────────────────────────────────────

@dataclass
class ParetoPoint:
    """A point on the Pareto frontier."""
    candidate: CandidateEvaluation
    lap_gain_ms: float
    platform_risk_ms: float
    robustness_score: float  # inverse of soft penalty count


def extract_pareto_frontier(
    evaluations: list[CandidateEvaluation],
    top_n: int = 200,
) -> dict[str, list[ParetoPoint]]:
    """Identify Pareto-optimal candidates across multiple objective pairs.

    Returns frontiers for:
    - "gain_vs_risk": lap_gain vs platform_risk (higher gain, lower risk)
    - "gain_vs_robustness": lap_gain vs robustness (higher gain, higher robustness)

    Args:
        evaluations: All candidate evaluations.
        top_n: Consider top N by score for frontier analysis.

    Returns:
        Dict mapping frontier name to list of ParetoPoints.
    """
    viable = [e for e in evaluations if not e.hard_vetoed]
    viable.sort(key=lambda e: e.score, reverse=True)
    candidates = viable[:top_n]

    if not candidates:
        return {}

    # Build Pareto points
    points: list[ParetoPoint] = []
    for c in candidates:
        b = c.breakdown
        n_penalties = len(c.soft_penalties)
        robustness = 1.0 / (1.0 + n_penalties)  # higher = more robust
        points.append(ParetoPoint(
            candidate=c,
            lap_gain_ms=b.lap_gain_ms,
            platform_risk_ms=b.platform_risk.total_ms,
            robustness_score=robustness,
        ))

    frontiers: dict[str, list[ParetoPoint]] = {}

    # Frontier 1: maximize lap_gain, minimize platform_risk
    frontiers["gain_vs_risk"] = _compute_pareto_2d(
        points,
        key_maximize=lambda p: p.lap_gain_ms,
        key_minimize=lambda p: p.platform_risk_ms,
    )

    # Frontier 2: maximize lap_gain, maximize robustness
    frontiers["gain_vs_robustness"] = _compute_pareto_2d(
        points,
        key_maximize=lambda p: p.lap_gain_ms,
        key_minimize=lambda p: -p.robustness_score,  # negate to minimize → maximize
    )

    return frontiers


def _compute_pareto_2d(
    points: list[ParetoPoint],
    key_maximize,
    key_minimize,
) -> list[ParetoPoint]:
    """2D Pareto frontier: maximize one axis, minimize another.

    A point is Pareto-optimal if no other point is better on BOTH axes.
    """
    if not points:
        return []

    # Sort by the maximize axis descending
    sorted_pts = sorted(points, key=key_maximize, reverse=True)

    frontier: list[ParetoPoint] = []
    best_min_so_far = float("inf")

    for p in sorted_pts:
        min_val = key_minimize(p)
        if min_val <= best_min_so_far:
            frontier.append(p)
            best_min_so_far = min_val

    return frontier


def format_pareto(frontiers: dict[str, list[ParetoPoint]]) -> str:
    """Format Pareto frontier data as a text report section."""
    lines: list[str] = []
    lines.append("")
    lines.append(_box_top("PARETO FRONTIER ANALYSIS"))
    lines.append(_full("  Multi-objective tradeoffs across top candidates."))
    lines.append(_full("  Frontier points are optimal — you can't improve one"))
    lines.append(_full("  axis without sacrificing the other."))
    lines.append(_blank())

    # Frontier 1: Lap gain vs Platform risk
    f1 = frontiers.get("gain_vs_risk", [])
    if f1:
        lines.append(_full("  ── Lap Gain vs Platform Risk ──"))
        lines.append(_full(
            f"  {'#':>3} {'Family':<20} {'Gain':>8} {'Risk':>8} "
            f"{'Score':>8} {'LLTD':>6}"
        ))
        lines.append(_full("  " + "─" * 58))
        for i, p in enumerate(f1[:15], 1):
            c = p.candidate
            lltd_str = (f"{c.physics.lltd:.1%}" if c.physics else "  n/a")
            lines.append(_full(
                f"  {i:3d} {c.family:<20} {p.lap_gain_ms:+7.1f}ms "
                f"{p.platform_risk_ms:>6.1f}ms {c.score:+7.1f}ms {lltd_str:>6}"
            ))
        lines.append(_blank())

    # Frontier 2: Lap gain vs Robustness
    f2 = frontiers.get("gain_vs_robustness", [])
    if f2:
        lines.append(_full("  ── Lap Gain vs Robustness ──"))
        lines.append(_full(
            f"  {'#':>3} {'Family':<20} {'Gain':>8} {'Robust':>8} "
            f"{'Penalties':>9}"
        ))
        lines.append(_full("  " + "─" * 52))
        for i, p in enumerate(f2[:15], 1):
            c = p.candidate
            n_pen = len(c.soft_penalties)
            lines.append(_full(
                f"  {i:3d} {c.family:<20} {p.lap_gain_ms:+7.1f}ms "
                f"{p.robustness_score:>7.2f} {n_pen:>8d}"
            ))
        lines.append(_blank())

    lines.append(_box_bot())
    return "\n".join(lines)


# ── 3. Setup Landscape Clusters ───────────────────────────────────

@dataclass
class CandidateCluster:
    """A cluster of similar candidate setups representing a 'philosophy'."""
    cluster_id: int
    label: str  # auto-generated description
    members: list[CandidateEvaluation]
    centroid: dict[str, float]  # average parameter values
    avg_score: float
    best_score: float
    distinguishing_features: list[str]  # what makes this cluster unique


def cluster_candidates(
    evaluations: list[CandidateEvaluation],
    space: LegalSpace,
    n_clusters: int = 4,
    top_n: int = 200,
) -> list[CandidateCluster]:
    """Group top candidates by parameter similarity using K-means.

    Normalizes all Tier A parameters to [0,1] range before clustering.
    Then labels each cluster based on its distinguishing characteristics.

    Args:
        evaluations: All candidate evaluations.
        space: Legal search space (for normalization ranges).
        n_clusters: Number of clusters to find.
        top_n: How many top candidates to cluster.

    Returns:
        List of CandidateCluster objects, sorted by best_score descending.
    """
    import numpy as np

    viable = [e for e in evaluations if not e.hard_vetoed]
    viable.sort(key=lambda e: e.score, reverse=True)
    selected = viable[:top_n]

    if len(selected) < n_clusters * 2:
        # Not enough candidates to meaningfully cluster
        if selected:
            return [CandidateCluster(
                cluster_id=0,
                label="All candidates (too few to cluster)",
                members=selected,
                centroid={},
                avg_score=sum(c.score for c in selected) / len(selected),
                best_score=selected[0].score,
                distinguishing_features=[],
            )]
        return []

    # Build parameter vectors from Tier A dimensions that appear in candidates
    dim_names: list[str] = []
    dim_ranges: list[tuple[float, float]] = []  # (lo, hi) for normalization
    for dim in space.dimensions:
        if dim.tier != "A":
            continue
        if dim.name not in selected[0].params:
            continue
        dim_names.append(dim.name)
        span = dim.hi - dim.lo
        dim_ranges.append((dim.lo, max(span, 1e-9)))

    if not dim_names:
        return []

    # Normalize to [0,1]
    vectors = np.zeros((len(selected), len(dim_names)))
    for i, cand in enumerate(selected):
        for j, (name, (lo, span)) in enumerate(zip(dim_names, dim_ranges)):
            val = cand.params.get(name, lo + span / 2)
            vectors[i, j] = (val - lo) / span

    # Simple K-means (avoid sklearn dependency)
    labels = _kmeans(vectors, n_clusters, max_iter=50, seed=42)

    # Build clusters
    clusters_map: dict[int, list[int]] = defaultdict(list)
    for i, label in enumerate(labels):
        clusters_map[label].append(i)

    clusters: list[CandidateCluster] = []
    # Compute global centroid for distinguishing features
    global_centroid = np.mean(vectors, axis=0)

    for cid, indices in sorted(clusters_map.items()):
        members = [selected[i] for i in indices]
        cluster_vectors = vectors[indices]
        centroid = np.mean(cluster_vectors, axis=0)

        # Compute distinguishing features: dimensions where this
        # cluster's centroid differs most from the global centroid
        diffs = centroid - global_centroid
        abs_diffs = np.abs(diffs)
        top_diff_indices = np.argsort(abs_diffs)[-5:][::-1]

        features: list[str] = []
        for idx in top_diff_indices:
            if abs_diffs[idx] < 0.05:
                break
            name = dim_names[idx]
            direction = "higher" if diffs[idx] > 0 else "lower"
            lo, span = dim_ranges[idx]
            actual_val = centroid[idx] * span + lo
            features.append(f"{name} {direction} ({actual_val:.1f})")

        # Auto-label based on distinguishing features
        label = _auto_label_cluster(features, centroid, dim_names, dim_ranges)

        centroid_dict = {
            dim_names[j]: centroid[j] * dim_ranges[j][1] + dim_ranges[j][0]
            for j in range(len(dim_names))
        }

        scores = [m.score for m in members]
        clusters.append(CandidateCluster(
            cluster_id=cid,
            label=label,
            members=members,
            centroid=centroid_dict,
            avg_score=sum(scores) / len(scores),
            best_score=max(scores),
            distinguishing_features=features,
        ))

    clusters.sort(key=lambda c: c.best_score, reverse=True)
    return clusters


def _auto_label_cluster(
    features: list[str],
    centroid,
    dim_names: list[str],
    dim_ranges: list[tuple[float, float]],
) -> str:
    """Generate a human-readable label for a setup cluster."""
    import numpy as np

    # Check for known patterns
    heave_idx = next((i for i, n in enumerate(dim_names) if n == "front_heave_spring_nmm"), None)
    third_idx = next((i for i, n in enumerate(dim_names) if n == "rear_third_spring_nmm"), None)
    arb_f_idx = next((i for i, n in enumerate(dim_names) if n == "front_arb_blade"), None)
    arb_r_idx = next((i for i, n in enumerate(dim_names) if n == "rear_arb_blade"), None)
    diff_idx = next((i for i, n in enumerate(dim_names) if n == "diff_preload_nm"), None)
    ls_f_idx = next((i for i, n in enumerate(dim_names) if n == "front_ls_comp"), None)

    is_soft = (heave_idx is not None and centroid[heave_idx] < 0.35
               and third_idx is not None and centroid[third_idx] < 0.35)
    is_stiff = (heave_idx is not None and centroid[heave_idx] > 0.65
                and third_idx is not None and centroid[third_idx] > 0.65)
    is_loose = (arb_r_idx is not None and centroid[arb_r_idx] < 0.3
                and diff_idx is not None and centroid[diff_idx] < 0.3)
    is_stable = (arb_f_idx is not None and centroid[arb_f_idx] < 0.3
                 and diff_idx is not None and centroid[diff_idx] > 0.7)
    is_heavy_damped = (ls_f_idx is not None and centroid[ls_f_idx] > 0.7)

    if is_soft and is_loose:
        return "Soft-Mechanical / High Rotation"
    elif is_soft:
        return "Soft-Mechanical / Grip-Focused"
    elif is_stiff and is_stable:
        return "Stiff-Aero / Stability-Focused"
    elif is_stiff:
        return "Stiff-Aero / Platform-Priority"
    elif is_loose:
        return "Loose-Balance / Rotation-Heavy"
    elif is_stable:
        return "Tight-Balance / Stability-Heavy"
    elif is_heavy_damped:
        return "Heavy-Damped / Control-Focused"
    elif features:
        # Use the top distinguishing feature
        return f"Variant ({features[0]})"
    else:
        return "Mixed-Strategy"


def _kmeans(X, k: int, max_iter: int = 50, seed: int = 42):
    """Simple K-means clustering (no sklearn dependency).

    Args:
        X: numpy array (n_samples, n_features), values in [0,1].
        k: Number of clusters.
        max_iter: Maximum iterations.
        seed: Random seed.

    Returns:
        numpy array of cluster labels (n_samples,).
    """
    import numpy as np

    rng = np.random.RandomState(seed)
    n = X.shape[0]

    # K-means++ initialization
    centers = np.empty((k, X.shape[1]))
    centers[0] = X[rng.randint(n)]
    for c in range(1, k):
        dists = np.min(
            np.sum((X[:, None, :] - centers[:c][None, :, :]) ** 2, axis=2),
            axis=1,
        )
        probs = dists / dists.sum()
        idx = rng.choice(n, p=probs)
        centers[c] = X[idx]

    labels = np.zeros(n, dtype=int)
    for _ in range(max_iter):
        # Assign to nearest center
        dists = np.sum((X[:, None, :] - centers[None, :, :]) ** 2, axis=2)
        new_labels = np.argmin(dists, axis=1)

        if np.array_equal(new_labels, labels):
            break
        labels = new_labels

        # Update centers
        for c in range(k):
            mask = labels == c
            if mask.any():
                centers[c] = X[mask].mean(axis=0)

    return labels


def format_clusters(clusters: list[CandidateCluster]) -> str:
    """Format cluster analysis as a text report section."""
    lines: list[str] = []
    lines.append("")
    lines.append(_box_top("SETUP LANDSCAPE CLUSTERS"))
    lines.append(_full("  Distinct setup 'philosophies' found in the search."))
    lines.append(_full("  Each cluster represents a genuinely different approach"))
    lines.append(_full("  to the same track, not just minor variations."))
    lines.append(_blank())

    for cluster in clusters:
        n = len(cluster.members)
        lines.append(_full(
            f"  Cluster {cluster.cluster_id + 1}: {cluster.label}"
        ))
        lines.append(_full(
            f"    Members: {n} | Best: {cluster.best_score:+.1f}ms | "
            f"Avg: {cluster.avg_score:+.1f}ms"
        ))

        if cluster.distinguishing_features:
            lines.append(_full("    What makes this cluster different:"))
            for feat in cluster.distinguishing_features[:4]:
                lines.append(_full(f"      • {feat}"))

        # Show key centroid values
        key_params = [
            ("front_heave_spring_nmm", "Heave", "N/mm"),
            ("rear_third_spring_nmm", "Third", "N/mm"),
            ("front_torsion_od_mm", "Torsion", "mm"),
            ("front_arb_blade", "ARB_F", ""),
            ("rear_arb_blade", "ARB_R", ""),
            ("diff_preload_nm", "Diff", "Nm"),
            ("front_ls_comp", "LS_F", ""),
            ("rear_ls_comp", "LS_R", ""),
        ]
        if cluster.centroid:
            parts = []
            for key, short, unit in key_params:
                if key in cluster.centroid:
                    val = cluster.centroid[key]
                    if val == int(val):
                        parts.append(f"{short}={int(val)}{unit}")
                    else:
                        parts.append(f"{short}={val:.1f}{unit}")
            if parts:
                mid = len(parts) // 2
                lines.append(_full(f"    Centroid: {', '.join(parts[:mid])}"))
                lines.append(_full(f"             {', '.join(parts[mid:])}"))

        lines.append(_blank())

    lines.append(_box_bot())
    return "\n".join(lines)


# ── 4. Full Diff Reports ─────────────────────────────────────────

# Human-readable param names and units
PARAM_DISPLAY: dict[str, tuple[str, str]] = {
    "wing_angle_deg": ("Wing Angle", "°"),
    "front_pushrod_offset_mm": ("Front Pushrod", "mm"),
    "rear_pushrod_offset_mm": ("Rear Pushrod", "mm"),
    "front_heave_spring_nmm": ("Front Heave Spring", "N/mm"),
    "rear_third_spring_nmm": ("Rear Third Spring", "N/mm"),
    "rear_spring_rate_nmm": ("Rear Spring Rate", "N/mm"),
    "front_torsion_od_mm": ("Front Torsion OD", "mm"),
    "front_camber_deg": ("Front Camber", "°"),
    "rear_camber_deg": ("Rear Camber", "°"),
    "front_arb_blade": ("Front ARB Blade", "clicks"),
    "rear_arb_blade": ("Rear ARB Blade", "clicks"),
    "front_ls_comp": ("Front LS Comp", "clicks"),
    "front_ls_rbd": ("Front LS Rbd", "clicks"),
    "front_hs_comp": ("Front HS Comp", "clicks"),
    "front_hs_rbd": ("Front HS Rbd", "clicks"),
    "front_hs_slope": ("Front HS Slope", "clicks"),
    "rear_ls_comp": ("Rear LS Comp", "clicks"),
    "rear_ls_rbd": ("Rear LS Rbd", "clicks"),
    "rear_hs_comp": ("Rear HS Comp", "clicks"),
    "rear_hs_rbd": ("Rear HS Rbd", "clicks"),
    "rear_hs_slope": ("Rear HS Slope", "clicks"),
    "brake_bias_pct": ("Brake Bias", "%"),
    "diff_preload_nm": ("Diff Preload", "Nm"),
}


def format_diff_report(
    candidate: CandidateEvaluation,
    baseline_params: dict[str, float],
    baseline_eval: CandidateEvaluation | None = None,
    rank: int = 1,
) -> str:
    """Full parameter-by-parameter diff of a candidate vs baseline.

    Shows direction + magnitude of every change, plus the objective
    breakdown explaining why this candidate scored differently.

    Args:
        candidate: The candidate to report on.
        baseline_params: Physics solver baseline parameter values.
        baseline_eval: Optional baseline evaluation for score comparison.
        rank: Candidate rank number.

    Returns:
        Formatted text block.
    """
    lines: list[str] = []
    c = candidate

    lines.append("")
    lines.append(_box_top(f"CANDIDATE #{rank} DIFF REPORT"))
    lines.append(_full(
        f"  Family: {c.family} | Score: {c.score:+.1f}ms"
    ))
    if baseline_eval:
        delta = c.score - baseline_eval.score
        lines.append(_full(
            f"  vs Baseline: {delta:+.1f}ms "
            f"({'better' if delta > 0 else 'worse'})"
        ))
    lines.append(_blank())

    # Parameter diff table
    lines.append(_full(
        f"  {'Parameter':<24} {'Base':>8} {'Cand':>8} {'Δ':>8} {'Dir':>5}"
    ))
    lines.append(_full("  " + "─" * 58))

    changes: list[tuple[str, float, float, float]] = []  # (name, base, cand, delta)

    for key in sorted(PARAM_DISPLAY.keys()):
        if key not in c.params and key not in baseline_params:
            continue
        base_val = baseline_params.get(key)
        cand_val = c.params.get(key)
        if base_val is None or cand_val is None:
            continue

        delta = cand_val - base_val
        changes.append((key, base_val, cand_val, delta))

    # Sort by absolute delta (largest changes first)
    changes.sort(key=lambda x: abs(x[3]), reverse=True)

    for key, base_val, cand_val, delta in changes:
        display_name, unit = PARAM_DISPLAY.get(key, (key, ""))

        # Direction arrow
        if abs(delta) < 1e-6:
            arrow = "  ="
        elif delta > 0:
            arrow = "  ↑"
        else:
            arrow = "  ↓"

        # Format values
        def _fmt(v):
            if v == int(v):
                return f"{int(v)}"
            return f"{v:.1f}"

        delta_str = f"{delta:+.1f}" if abs(delta) > 0.05 else "  0"

        # Highlight significant changes
        marker = " ◄" if abs(delta) > 0.5 else ""

        lines.append(_full(
            f"  {display_name:<24} {_fmt(base_val):>8} "
            f"{_fmt(cand_val):>8} {delta_str:>8} {arrow}{marker}"
        ))

    lines.append(_blank())

    # Objective breakdown
    lines.append(_full("  ── Objective Breakdown ──"))
    b = c.breakdown
    lines.append(_full(f"    Lap gain:           {b.lap_gain_ms:+.1f}ms"))
    lines.append(_full(
        f"    Platform risk:      "
        f"{-b.w_platform * b.platform_risk.total_ms:+.1f}ms "
        f"(bottom={b.platform_risk.bottoming_risk_ms:.0f}, "
        f"vortex={b.platform_risk.vortex_risk_ms:.0f}, "
        f"rh_col={b.platform_risk.rh_collapse_risk_ms:.0f})"
    ))
    lines.append(_full(
        f"    Driver mismatch:    "
        f"{-b.w_driver * b.driver_mismatch.total_ms:+.1f}ms"
    ))
    lines.append(_full(
        f"    Telemetry uncert:   "
        f"{-b.w_uncertainty * b.telemetry_uncertainty.total_ms:+.1f}ms"
    ))
    lines.append(_full(
        f"    Envelope penalty:   "
        f"{-b.w_envelope * b.envelope_penalty.total_ms:+.1f}ms"
    ))
    lines.append(_full(f"    ─────────────────────────────"))
    lines.append(_full(f"    TOTAL:              {c.score:+.1f}ms"))
    lines.append(_blank())

    # Physics comparison
    if c.physics:
        p = c.physics
        lines.append(_full("  ── Physics ──"))
        lines.append(_full(
            f"    Excursion: F={p.front_excursion_mm:.1f}mm "
            f"R={p.rear_excursion_mm:.1f}mm"
        ))
        lines.append(_full(
            f"    Bottom margin: F={p.front_bottoming_margin_mm:+.1f}mm "
            f"R={p.rear_bottoming_margin_mm:+.1f}mm"
        ))
        lines.append(_full(
            f"    Stall margin: {p.stall_margin_mm:+.1f}mm"
        ))
        lines.append(_full(
            f"    LLTD: {p.lltd:.1%} (error={p.lltd_error:.3f})"
        ))
        lines.append(_full(
            f"    Damping: ζ_LS F={p.zeta_ls_front:.2f} R={p.zeta_ls_rear:.2f}"
            f" | ζ_HS F={p.zeta_hs_front:.2f} R={p.zeta_hs_rear:.2f}"
        ))
        lines.append(_blank())

    # Soft penalties
    if c.soft_penalties:
        lines.append(_full(f"  ── Soft Penalties ({len(c.soft_penalties)}) ──"))
        for sp in c.soft_penalties[:5]:
            if len(sp) > W - 8:
                sp = sp[: W - 11] + "..."
            lines.append(_full(f"    • {sp}"))

    lines.append(_box_bot())
    return "\n".join(lines)


# ── 5. Vetoed Candidates Summary ──────────────────────────────────

def format_vetoed_summary(evaluations: list[CandidateEvaluation]) -> str:
    """Summarize vetoed candidates and their rejection reasons."""
    vetoed = [e for e in evaluations if e.hard_vetoed]
    if not vetoed:
        return ""

    lines: list[str] = []
    lines.append("")
    lines.append(_box_top("VETOED CANDIDATES"))
    lines.append(_full(f"  {len(vetoed)} candidates were hard-vetoed."))
    lines.append(_blank())

    # Group by veto reason
    reason_counts: dict[str, int] = defaultdict(int)
    for ev in vetoed:
        for reason in ev.veto_reasons:
            # Truncate and normalize reason for grouping
            key = reason.split(":")[0].strip() if ":" in reason else reason[:40]
            reason_counts[key] += 1

    lines.append(_full("  Veto reasons (by frequency):"))
    for reason, count in sorted(reason_counts.items(), key=lambda x: x[1], reverse=True):
        if len(reason) > W - 16:
            reason = reason[: W - 19] + "..."
        lines.append(_full(f"    {count:4d}× {reason}"))

    # Show a few example vetoed candidates with their families
    lines.append(_blank())
    lines.append(_full("  Example vetoed candidates:"))
    for ev in vetoed[:5]:
        reasons = "; ".join(ev.veto_reasons[:2])
        if len(reasons) > W - 30:
            reasons = reasons[: W - 33] + "..."
        lines.append(_full(f"    [{ev.family:<16}] {reasons}"))

    lines.append(_box_bot())
    return "\n".join(lines)


# ── 6. Full Report Generator ─────────────────────────────────────

def generate_search_report(
    ls_result,
    baseline_params: dict[str, float],
    space: LegalSpace,
    objective: ObjectiveFunction,
    car,
    sensitivity_top_n: int = 3,
    diff_top_n: int = 5,
    cluster_count: int = 4,
) -> str:
    """Generate the comprehensive Sprint 4 search report.

    Orchestrates all analysis sections into a single report string.

    Args:
        ls_result: LegalSearchResult (or GridSearchResult).
        baseline_params: Physics solver baseline parameters.
        space: Legal search space.
        objective: Objective function.
        car: Car model.
        sensitivity_top_n: Number of top candidates for sensitivity analysis.
        diff_top_n: Number of top candidates for diff reports.
        cluster_count: Number of clusters to find.

    Returns:
        Complete formatted report string.
    """
    all_evals = ls_result.all_evaluations
    viable = [e for e in all_evals if not e.hard_vetoed]
    viable.sort(key=lambda e: e.score, reverse=True)

    sections: list[str] = []

    # Report header
    sections.append("")
    sections.append("=" * W)
    sections.append("  LEGAL-MANIFOLD SEARCH — ANALYSIS REPORT (Sprint 4)")
    sections.append("=" * W)
    sections.append(f"  Total evaluated: {len(all_evals):,}")
    sections.append(f"  Viable:          {len(viable):,}")
    sections.append(f"  Vetoed:          {len(all_evals) - len(viable):,}")
    if viable:
        sections.append(f"  Best score:      {viable[0].score:+.1f}ms")
        sections.append(f"  Worst viable:    {viable[-1].score:+.1f}ms")
        sections.append(f"  Score spread:    {viable[0].score - viable[-1].score:.1f}ms")
    sections.append("")

    # Evaluate the baseline for comparison
    baseline_eval: CandidateEvaluation | None = None
    try:
        from solver.legal_space import compute_perch_offsets
        bp = dict(baseline_params)
        perches = compute_perch_offsets(bp, car)
        bp.update(perches)
        baseline_evals = objective.evaluate_batch([bp], layer=4, family="baseline")
        if baseline_evals:
            baseline_eval = baseline_evals[0]
            sections.append(f"  Baseline score:  {baseline_eval.score:+.1f}ms")
            if viable:
                delta = viable[0].score - baseline_eval.score
                sections.append(
                    f"  Best vs base:    {delta:+.1f}ms "
                    f"({'improvement' if delta > 0 else 'regression'})"
                )
            sections.append("")
    except Exception as e:
        logger.debug(f"Could not evaluate baseline: {e}")

    # Section 1: Sensitivity analysis
    if viable:
        logger.info(f"Computing sensitivity for top {sensitivity_top_n} candidates...")
        matrices: list[SensitivityMatrix] = []
        for cand in viable[:sensitivity_top_n]:
            try:
                matrix = compute_sensitivity(cand, space, objective, car, steps=3)
                matrices.append(matrix)
            except Exception as e:
                logger.warning(f"Sensitivity failed for {cand.family}: {e}")
        if matrices:
            sections.append(format_sensitivity(matrices))

    # Section 2: Pareto frontier
    if viable:
        logger.info("Extracting Pareto frontiers...")
        try:
            frontiers = extract_pareto_frontier(all_evals, top_n=200)
            if frontiers:
                sections.append(format_pareto(frontiers))
        except Exception as e:
            logger.warning(f"Pareto extraction failed: {e}")

    # Section 3: Setup clusters
    if len(viable) >= 8:
        logger.info(f"Clustering top candidates into {cluster_count} groups...")
        try:
            clusters = cluster_candidates(
                all_evals, space,
                n_clusters=min(cluster_count, len(viable) // 4),
                top_n=200,
            )
            if clusters:
                sections.append(format_clusters(clusters))
        except Exception as e:
            logger.warning(f"Clustering failed: {e}")

    # Section 4: Diff reports for top candidates
    if viable:
        top_for_diff: list[CandidateEvaluation] = []
        # Always include best robust, aggressive, weird if available
        for attr in ("best_robust", "best_aggressive", "best_weird"):
            ev = getattr(ls_result, attr, None)
            if ev is not None and not ev.hard_vetoed:
                if not any(id(e) == id(ev) for e in top_for_diff):
                    top_for_diff.append(ev)

        # Fill remaining slots from top-ranked
        for cand in viable:
            if len(top_for_diff) >= diff_top_n:
                break
            if not any(id(e) == id(cand) for e in top_for_diff):
                top_for_diff.append(cand)

        for i, cand in enumerate(top_for_diff[:diff_top_n], 1):
            sections.append(format_diff_report(
                cand, baseline_params, baseline_eval, rank=i
            ))

    # Section 5: Vetoed summary
    vetoed_text = format_vetoed_summary(all_evals)
    if vetoed_text:
        sections.append(vetoed_text)

    sections.append("")
    sections.append("=" * W)
    sections.append("  END OF ANALYSIS REPORT")
    sections.append("=" * W)

    return "\n".join(sections)
