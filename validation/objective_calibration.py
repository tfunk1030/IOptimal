"""BMW/Sebring objective recalibration and ablation tooling.

This module does not auto-apply weights into the runtime solver.
It produces reproducible evidence for manual review:
  - current score correlation
  - weighted-term correlations
  - ablation results
  - coarse weight-search suggestions
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from car_model.cars import get_car
from solver.objective import ObjectiveFunction
from track_model.profile import TrackProfile
from validation.observation_mapping import normalize_setup_to_canonical_params


ROOT = Path(__file__).resolve().parent.parent
OBS_DIR = ROOT / "data" / "learnings" / "observations"
TRACK_DIR = ROOT / "data" / "tracks"
REPORT_MD = ROOT / "validation" / "calibration_report.md"
REPORT_JSON = ROOT / "validation" / "calibration_weights.json"


@dataclass
class ObservationRow:
    filename: str
    lap_time_s: float
    params: dict[str, float | int | str]
    telemetry: dict[str, Any]
    performance: dict[str, Any]


@dataclass
class ScoredObservation:
    filename: str
    lap_time_s: float
    vetoed: bool
    total_score_ms: float
    lap_gain_ms: float
    platform_risk_ms: float
    driver_mismatch_ms: float
    telemetry_uncertainty_ms: float
    envelope_penalty_ms: float
    staleness_penalty_ms: float
    empirical_penalty_ms: float
    weighted_lap_gain_ms: float
    weighted_platform_ms: float
    weighted_driver_ms: float
    weighted_uncertainty_ms: float
    weighted_envelope_ms: float
    weighted_staleness_ms: float
    weighted_empirical_ms: float
    lap_gain_components: dict[str, float] = field(default_factory=dict)
    w_lap_gain: float = 1.0
    w_platform: float = 1.0
    w_driver: float = 0.5
    w_uncertainty: float = 0.6
    w_envelope: float = 0.7
    w_staleness: float = 0.3
    w_empirical: float = 0.4


def slugify(value: str) -> str:
    return (
        str(value or "")
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
        .replace(".", "")
    )


def pearson_r(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2 or len(ys) < 2 or len(xs) != len(ys):
        return float("nan")
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return float("nan")
    return num / (sx * sy)


def spearman_r(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2 or len(ys) < 2 or len(xs) != len(ys):
        return float("nan")

    def _ranks(values: list[float]) -> list[float]:
        ordered = sorted(enumerate(values), key=lambda item: item[1])
        ranks = [0.0] * len(values)
        for rank, (idx, _) in enumerate(ordered, start=1):
            ranks[idx] = float(rank)
        return ranks

    return pearson_r(_ranks(xs), _ranks(ys))


def _find_track_profile() -> Path | None:
    candidates = [
        TRACK_DIR / "sebring_international_raceway_international.json",
        TRACK_DIR / "sebring_international_raceway.json",
        TRACK_DIR / "sebring.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_observations() -> list[ObservationRow]:
    rows: list[ObservationRow] = []
    for path in sorted(OBS_DIR.glob("bmw_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("car") or "").strip().lower() != "bmw":
            continue
        if slugify(str(payload.get("track") or "")) != "sebring_international_raceway":
            continue
        perf = payload.get("performance", {}) or {}
        lap_time = perf.get("best_lap_time_s") or perf.get("lap_time_s")
        try:
            lap_time_s = float(lap_time)
        except (TypeError, ValueError):
            continue
        setup = payload.get("setup", {}) or {}
        rows.append(
            ObservationRow(
                filename=path.name,
                lap_time_s=lap_time_s,
                params=normalize_setup_to_canonical_params(setup, car="bmw"),
                telemetry=payload.get("telemetry", {}) or {},
                performance=perf,
            )
        )
    return rows


def score_observations(
    rows: list[ObservationRow],
    *,
    track_mode: str,
) -> list[ScoredObservation]:
    track_profile = None
    if track_mode == "track_aware":
        track_path = _find_track_profile()
        track_profile = TrackProfile.load(str(track_path)) if track_path is not None else None

    objective = ObjectiveFunction(get_car("bmw"), track_profile, scenario_profile="single_lap_safe")
    scored: list[ScoredObservation] = []
    for row in rows:
        ev = objective.evaluate(row.params)
        bd = ev.breakdown
        scored.append(
            ScoredObservation(
                filename=row.filename,
                lap_time_s=row.lap_time_s,
                vetoed=ev.hard_vetoed,
                total_score_ms=ev.score,
                lap_gain_ms=bd.lap_gain_ms,
                platform_risk_ms=bd.platform_risk.total_ms,
                driver_mismatch_ms=bd.driver_mismatch.total_ms,
                telemetry_uncertainty_ms=bd.telemetry_uncertainty.total_ms,
                envelope_penalty_ms=bd.envelope_penalty.total_ms,
                staleness_penalty_ms=bd.staleness_penalty_ms,
                empirical_penalty_ms=bd.empirical_penalty_ms,
                weighted_lap_gain_ms=bd.w_lap_gain * bd.lap_gain_ms,
                weighted_platform_ms=-(bd.w_platform * bd.platform_risk.total_ms),
                weighted_driver_ms=-(bd.w_driver * bd.driver_mismatch.total_ms),
                weighted_uncertainty_ms=-(bd.w_uncertainty * bd.telemetry_uncertainty.total_ms),
                weighted_envelope_ms=-(bd.w_envelope * bd.envelope_penalty.total_ms),
                weighted_staleness_ms=-(bd.w_staleness * bd.staleness_penalty_ms),
                weighted_empirical_ms=-(bd.w_empirical * bd.empirical_penalty_ms),
                lap_gain_components=bd.lap_gain_detail.as_dict(),
                w_lap_gain=bd.w_lap_gain,
                w_platform=bd.w_platform,
                w_driver=bd.w_driver,
                w_uncertainty=bd.w_uncertainty,
                w_envelope=bd.w_envelope,
                w_staleness=bd.w_staleness,
                w_empirical=bd.w_empirical,
            )
        )
    return scored


def _non_vetoed(scored: list[ScoredObservation]) -> list[ScoredObservation]:
    return [row for row in scored if not row.vetoed and not math.isnan(float(row.total_score_ms))]


def _term_vectors(scored: list[ScoredObservation]) -> dict[str, list[float]]:
    return {
        "total_score_ms": [row.total_score_ms for row in scored],
        "lap_gain_ms": [row.lap_gain_ms for row in scored],
        "platform_risk_ms": [row.platform_risk_ms for row in scored],
        "driver_mismatch_ms": [row.driver_mismatch_ms for row in scored],
        "telemetry_uncertainty_ms": [row.telemetry_uncertainty_ms for row in scored],
        "envelope_penalty_ms": [row.envelope_penalty_ms for row in scored],
        "staleness_penalty_ms": [row.staleness_penalty_ms for row in scored],
        "empirical_penalty_ms": [row.empirical_penalty_ms for row in scored],
        "weighted_lap_gain_ms": [row.weighted_lap_gain_ms for row in scored],
        "weighted_platform_ms": [row.weighted_platform_ms for row in scored],
        "weighted_driver_ms": [row.weighted_driver_ms for row in scored],
        "weighted_uncertainty_ms": [row.weighted_uncertainty_ms for row in scored],
        "weighted_envelope_ms": [row.weighted_envelope_ms for row in scored],
        "weighted_staleness_ms": [row.weighted_staleness_ms for row in scored],
        "weighted_empirical_ms": [row.weighted_empirical_ms for row in scored],
    }


def term_correlations(scored: list[ScoredObservation]) -> list[dict[str, Any]]:
    rows = _non_vetoed(scored)
    lap_times = [row.lap_time_s for row in rows]
    terms = _term_vectors(rows)
    output: list[dict[str, Any]] = []
    for name, values in terms.items():
        output.append(
            {
                "term": name,
                "pearson_r": pearson_r(values, lap_times),
                "spearman_r": spearman_r(values, lap_times),
            }
        )
    output.sort(key=lambda item: abs(float(item["spearman_r"])), reverse=True)
    return output


def lap_gain_component_correlations(scored: list[ScoredObservation]) -> list[dict[str, Any]]:
    rows = _non_vetoed(scored)
    if not rows:
        return []
    lap_times = [row.lap_time_s for row in rows]
    component_names = sorted({name for row in rows for name in row.lap_gain_components.keys()})
    output: list[dict[str, Any]] = []
    for name in component_names:
        values = [float(row.lap_gain_components.get(name, 0.0)) for row in rows]
        output.append(
            {
                "component": name,
                "pearson_r": pearson_r(values, lap_times),
                "spearman_r": spearman_r(values, lap_times),
            }
        )
    output.sort(key=lambda item: abs(float(item["spearman_r"])), reverse=True)
    return output


def _component_adjusted_score(row: ScoredObservation, component_name: str) -> float:
    return row.total_score_ms + (row.w_lap_gain * float(row.lap_gain_components.get(component_name, 0.0)))


def score_from_weights(row: ScoredObservation, weights: dict[str, float]) -> float:
    return (
        weights["lap_gain"] * row.lap_gain_ms
        - weights["platform"] * row.platform_risk_ms
        - weights["driver"] * row.driver_mismatch_ms
        - weights["uncertainty"] * row.telemetry_uncertainty_ms
        - weights["envelope"] * row.envelope_penalty_ms
        - weights["staleness"] * row.staleness_penalty_ms
        - weights["empirical"] * row.empirical_penalty_ms
    )


def current_weights(scored: list[ScoredObservation]) -> dict[str, float]:
    if scored:
        row = scored[0]
        return {
            "lap_gain": row.w_lap_gain,
            "platform": row.w_platform,
            "driver": row.w_driver,
            "uncertainty": row.w_uncertainty,
            "envelope": row.w_envelope,
            "staleness": row.w_staleness,
            "empirical": row.w_empirical,
        }
    return {
        "lap_gain": 1.0,
        "platform": 1.0,
        "driver": 0.5,
        "uncertainty": 0.6,
        "envelope": 0.7,
        "staleness": 0.3,
        "empirical": 0.4,
    }


def ablation_report(scored: list[ScoredObservation]) -> list[dict[str, Any]]:
    rows = _non_vetoed(scored)
    lap_times = [row.lap_time_s for row in rows]
    base = current_weights(rows)
    variants: dict[str, dict[str, float]] = {
        "current": dict(base),
        "lap_gain_only": {
            "lap_gain": 1.0,
            "platform": 0.0,
            "driver": 0.0,
            "uncertainty": 0.0,
            "envelope": 0.0,
            "staleness": 0.0,
            "empirical": 0.0,
        },
        "penalties_only": {
            "lap_gain": 0.0,
            "platform": 1.0,
            "driver": 0.5,
            "uncertainty": 0.6,
            "envelope": 0.7,
            "staleness": 0.3,
            "empirical": 0.4,
        },
    }
    for term in list(base.keys()):
        dropped = dict(base)
        dropped[term] = 0.0
        variants[f"drop_{term}"] = dropped

    output: list[dict[str, Any]] = []
    for name, weights in variants.items():
        scores = [score_from_weights(row, weights) for row in rows]
        output.append(
            {
                "variant": name,
                "pearson_r": pearson_r(scores, lap_times),
                "spearman_r": spearman_r(scores, lap_times),
                "weights": weights,
            }
        )
    output.sort(key=lambda item: float(item["spearman_r"]))
    return output


def search_weight_profiles(scored: list[ScoredObservation]) -> dict[str, Any]:
    rows = _non_vetoed(scored)
    lap_times = [row.lap_time_s for row in rows]
    gain_grid = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
    penalty_grid = [0.0, 0.1, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2]
    best_spearman = float("inf")
    best_weights = current_weights(rows)

    for lap_gain in gain_grid:
        for platform in penalty_grid:
            for driver in penalty_grid:
                for uncertainty in penalty_grid:
                    for envelope in penalty_grid:
                        weights = {
                            "lap_gain": lap_gain,
                            "platform": platform,
                            "driver": driver,
                            "uncertainty": uncertainty,
                            "envelope": envelope,
                            "staleness": 0.0,
                            "empirical": 0.0,
                        }
                        scores = [score_from_weights(row, weights) for row in rows]
                        corr = spearman_r(scores, lap_times)
                        if corr < best_spearman:
                            best_spearman = corr
                            best_weights = weights

    current_scores = [score_from_weights(row, current_weights(rows)) for row in rows]
    current_spearman = spearman_r(current_scores, lap_times)
    return {
        "current_spearman_r": current_spearman,
        "best_spearman_r": best_spearman,
        "improvement": current_spearman - best_spearman,
        "best_weights": best_weights,
        "recommended_for_manual_review": bool(best_spearman <= -0.15 and best_spearman < current_spearman),
    }


def _fold_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "mean_spearman_r": float("nan"),
            "median_spearman_r": float("nan"),
            "best_spearman_r": float("nan"),
            "worst_spearman_r": float("nan"),
        }
    ordered = sorted(values)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
    return {
        "mean_spearman_r": sum(ordered) / len(ordered),
        "median_spearman_r": median,
        "best_spearman_r": min(ordered),
        "worst_spearman_r": max(ordered),
    }


def _fold_rows(rows: list[ScoredObservation], *, folds: int) -> list[list[ScoredObservation]]:
    fold_count = max(2, min(folds, len(rows)))
    return [rows[idx::fold_count] for idx in range(fold_count)]


def _holdout_summary_for_scores(
    rows: list[ScoredObservation],
    *,
    score_fn,
    folds: int = 5,
) -> dict[str, Any]:
    if len(rows) < 4:
        return _fold_summary([])
    fold_rows = _fold_rows(rows, folds=folds)
    fold_values: list[float] = []
    for test_rows in fold_rows:
        if len(test_rows) < 2:
            continue
        test_laps = [row.lap_time_s for row in test_rows]
        test_scores = [score_fn(row) for row in test_rows]
        corr = spearman_r(test_scores, test_laps)
        if not math.isnan(float(corr)):
            fold_values.append(corr)
    return _fold_summary(fold_values)


def lap_gain_component_ablations(scored: list[ScoredObservation], *, folds: int = 5) -> list[dict[str, Any]]:
    rows = sorted(_non_vetoed(scored), key=lambda row: row.filename)
    if not rows:
        return []

    current_scores = [row.total_score_ms for row in rows]
    lap_times = [row.lap_time_s for row in rows]
    current_spearman = spearman_r(current_scores, lap_times)
    current_holdout = _holdout_summary_for_scores(rows, score_fn=lambda row: row.total_score_ms, folds=folds)
    component_names = sorted({name for row in rows for name in row.lap_gain_components.keys()})
    output: list[dict[str, Any]] = []
    for name in component_names:
        adjusted_scores = [_component_adjusted_score(row, name) for row in rows]
        adjusted_holdout = _holdout_summary_for_scores(
            rows,
            score_fn=lambda row, component=name: _component_adjusted_score(row, component),
            folds=folds,
        )
        spearman = spearman_r(adjusted_scores, lap_times)
        output.append(
            {
                "component": name,
                "pearson_r": pearson_r(adjusted_scores, lap_times),
                "spearman_r": spearman,
                "spearman_improvement_vs_current": current_spearman - spearman,
                "holdout_mean_spearman_r": adjusted_holdout["mean_spearman_r"],
                "holdout_worst_spearman_r": adjusted_holdout["worst_spearman_r"],
                "holdout_mean_improvement_vs_current": (
                    current_holdout["mean_spearman_r"] - adjusted_holdout["mean_spearman_r"]
                ),
            }
        )
    output.sort(
        key=lambda item: (
            -float(item["holdout_mean_improvement_vs_current"]),
            -float(item["spearman_improvement_vs_current"]),
        )
    )
    return output


def holdout_validation(scored: list[ScoredObservation], *, folds: int = 5) -> dict[str, Any]:
    rows = sorted(_non_vetoed(scored), key=lambda row: row.filename)
    if len(rows) < 4:
        return {
            "fold_count": 0,
            "current_runtime": _fold_summary([]),
            "train_searched_tested": _fold_summary([]),
            "folds": [],
        }

    fold_rows = _fold_rows(rows, folds=folds)
    fold_count = len(fold_rows)
    current_values: list[float] = []
    searched_values: list[float] = []
    fold_reports: list[dict[str, Any]] = []

    for idx, test_rows in enumerate(fold_rows, start=1):
        train_rows = [row for fold in fold_rows if fold is not test_rows for row in fold]
        test_laps = [row.lap_time_s for row in test_rows]
        current_scores = [row.total_score_ms for row in test_rows]
        current_corr = spearman_r(current_scores, test_laps)
        if not math.isnan(float(current_corr)):
            current_values.append(current_corr)

        searched_corr = float("nan")
        searched_weights: dict[str, float] | None = None
        if len(train_rows) >= 3 and len(test_rows) >= 2:
            search = search_weight_profiles(train_rows)
            searched_weights = search["best_weights"]
            searched_scores = [score_from_weights(row, searched_weights) for row in test_rows]
            searched_corr = spearman_r(searched_scores, test_laps)
            if not math.isnan(float(searched_corr)):
                searched_values.append(searched_corr)

        fold_reports.append(
            {
                "fold": idx,
                "train_samples": len(train_rows),
                "test_samples": len(test_rows),
                "current_spearman_r": current_corr,
                "searched_test_spearman_r": searched_corr,
                "searched_weights": searched_weights,
            }
        )

    return {
        "fold_count": fold_count,
        "current_runtime": _fold_summary(current_values),
        "train_searched_tested": _fold_summary(searched_values),
        "folds": fold_reports,
    }


def build_mode_report(scored: list[ScoredObservation], *, track_mode: str, include_search: bool = True) -> dict[str, Any]:
    rows = _non_vetoed(scored)
    lap_times = [row.lap_time_s for row in rows]
    scores = [row.total_score_ms for row in rows]
    report = {
        "track_mode": track_mode,
        "samples": len(scored),
        "non_vetoed_samples": len(rows),
        "vetoed_samples": len(scored) - len(rows),
        "score_correlation": {
            "pearson_r": pearson_r(scores, lap_times),
            "spearman_r": spearman_r(scores, lap_times),
        },
        "term_correlations": term_correlations(scored),
        "lap_gain_component_correlations": lap_gain_component_correlations(scored),
        "lap_gain_component_ablations": lap_gain_component_ablations(scored),
        "ablations": ablation_report(scored),
        "holdout_validation": holdout_validation(scored),
    }
    if include_search:
        report["weight_search"] = search_weight_profiles(scored)
    return report


def build_calibration_report(*, include_search: bool = True) -> dict[str, Any]:
    rows = load_observations()
    if not rows:
        raise RuntimeError("No BMW/Sebring observations with lap times were found.")

    trackless = score_observations(rows, track_mode="trackless")
    track_aware = score_observations(rows, track_mode="track_aware")

    trackless_report = build_mode_report(trackless, track_mode="trackless", include_search=include_search)
    track_aware_report = build_mode_report(track_aware, track_mode="track_aware", include_search=include_search)

    preferred_runtime = "track_aware" if track_aware_report["score_correlation"]["spearman_r"] < 0 else "trackless"
    best_search = track_aware_report.get("weight_search") or {}
    if preferred_runtime == "trackless":
        best_search = trackless_report.get("weight_search") or {}
    preferred_current_weights = current_weights(track_aware if preferred_runtime == "track_aware" else trackless)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "bmw_sebring_samples": len(rows),
        "modes": {
            "track_aware": track_aware_report,
            "trackless": trackless_report,
        },
        "recommended_runtime_profile": {
            "mode": preferred_runtime,
            "weights": best_search.get("best_weights", preferred_current_weights),
            "manual_review_required": True,
            "auto_apply": False,
            "reason": "Calibration tooling is implemented, but runtime auto-application stays disabled until track-aware correlation is materially negative and stable under stronger validation.",
        },
    }


def write_report(report: dict[str, Any]) -> None:
    lines = [
        f"# Objective Recalibration Report",
        "",
        f"Generated: {report['generated_at_utc']}",
        "",
        f"BMW/Sebring samples: `{report['bmw_sebring_samples']}`",
        "",
    ]
    for mode_name in ("track_aware", "trackless"):
        mode = report["modes"][mode_name]
        corr = mode["score_correlation"]
        lines.extend(
            [
                f"## {mode_name.replace('_', ' ').title()}",
                "",
                f"- Samples: `{mode['samples']}` total, `{mode['non_vetoed_samples']}` non-vetoed",
                f"- Pearson: `{corr['pearson_r']:+.6f}`",
                f"- Spearman: `{corr['spearman_r']:+.6f}`",
                "",
                "### Term Correlations",
                "",
                "| Term | Pearson r | Spearman r |",
                "|------|-----------|------------|",
            ]
        )
        for term in mode["term_correlations"][:12]:
            lines.append(f"| {term['term']} | {term['pearson_r']:+.6f} | {term['spearman_r']:+.6f} |")
        lines.extend(
            [
                "",
                "### Lap-Gain Components",
                "",
                "| Component | Pearson r | Spearman r |",
                "|-----------|-----------|------------|",
            ]
        )
        for component in mode["lap_gain_component_correlations"]:
            lines.append(
                f"| {component['component']} | {component['pearson_r']:+.6f} | {component['spearman_r']:+.6f} |"
            )
        lines.extend(
            [
                "",
                "### Lap-Gain Component Ablations",
                "",
                "| Component Removed | Spearman r | Holdout Mean | Holdout Worst | In-Sample Improvement | Holdout Mean Improvement |",
                "|-------------------|------------|--------------|---------------|-----------------------|--------------------------|",
            ]
        )
        for component in mode["lap_gain_component_ablations"]:
            lines.append(
                f"| {component['component']} | {component['spearman_r']:+.6f} | "
                f"{component['holdout_mean_spearman_r']:+.6f} | {component['holdout_worst_spearman_r']:+.6f} | "
                f"{component['spearman_improvement_vs_current']:+.6f} | "
                f"{component['holdout_mean_improvement_vs_current']:+.6f} |"
            )
        holdout = mode["holdout_validation"]
        current_holdout = holdout["current_runtime"]
        searched_holdout = holdout["train_searched_tested"]
        lines.extend(
            [
                "",
                "### Holdout Validation",
                "",
                f"- Folds: `{holdout['fold_count']}`",
                f"- Current runtime mean test Spearman: `{current_holdout['mean_spearman_r']:+.6f}`",
                f"- Current runtime worst test Spearman: `{current_holdout['worst_spearman_r']:+.6f}`",
                f"- Train-searched mean test Spearman: `{searched_holdout['mean_spearman_r']:+.6f}`",
                f"- Train-searched worst test Spearman: `{searched_holdout['worst_spearman_r']:+.6f}`",
                "",
                "### Ablations",
                "",
                "| Variant | Pearson r | Spearman r |",
                "|---------|-----------|------------|",
            ]
        )
        for variant in mode["ablations"]:
            lines.append(f"| {variant['variant']} | {variant['pearson_r']:+.6f} | {variant['spearman_r']:+.6f} |")
        if "weight_search" in mode:
            search = mode["weight_search"]
            lines.extend(
                [
                    "",
                    "### Weight Search",
                    "",
                    f"- Current Spearman: `{search['current_spearman_r']:+.6f}`",
                    f"- Best Spearman found: `{search['best_spearman_r']:+.6f}`",
                    f"- Improvement: `{search['improvement']:+.6f}`",
                    f"- Manual review recommended: `{search['recommended_for_manual_review']}`",
                    "",
                    "| Weight | Suggested |",
                    "|--------|-----------|",
                ]
            )
            for name, value in search["best_weights"].items():
                lines.append(f"| {name} | {value:.2f} |")
        lines.append("")

    runtime = report["recommended_runtime_profile"]
    lines.extend(
        [
            "## Runtime Recommendation",
            "",
            f"- Preferred evidence mode today: `{runtime['mode']}`",
            f"- Auto-apply: `{runtime['auto_apply']}`",
            f"- Manual review required: `{runtime['manual_review_required']}`",
            f"- Reason: {runtime['reason']}",
            "",
        ]
    )

    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    REPORT_JSON.write_text(json.dumps(report, indent=2, allow_nan=True), encoding="utf-8")


def main() -> None:
    report = build_calibration_report()
    write_report(report)
    track_aware = report["modes"]["track_aware"]["score_correlation"]["spearman_r"]
    trackless = report["modes"]["trackless"]["score_correlation"]["spearman_r"]
    print(f"BMW/Sebring samples: {report['bmw_sebring_samples']}")
    print(f"Track-aware Spearman: {track_aware:+.6f}")
    print(f"Trackless Spearman: {trackless:+.6f}")
    print(f"Wrote {REPORT_MD}")
    print(f"Wrote {REPORT_JSON}")


if __name__ == "__main__":
    main()
