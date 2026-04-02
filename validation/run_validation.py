"""Recompute the current objective evidence from repo-local observations."""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from car_model.cars import get_car
from solver.objective import ObjectiveFunction
from track_model.profile import TrackProfile
from validation.objective_calibration import build_calibration_report
from validation.observation_mapping import normalize_setup_to_canonical_params, resolve_validation_signals


ROOT = Path(__file__).resolve().parent.parent
OBS_DIR = ROOT / "data" / "learnings" / "observations"
TRACK_DIR = ROOT / "data" / "tracks"
OUT_MD = ROOT / "validation" / "objective_validation.md"
OUT_JSON = ROOT / "validation" / "objective_validation.json"


@dataclass
class ObservationSample:
    path: Path
    session_id: str
    car: str
    track: str
    track_config: str
    lap_time_s: float
    params: dict[str, Any]
    telemetry: dict[str, Any]
    performance: dict[str, Any]
    signal_sources: dict[str, dict[str, Any]]


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


def _find_track_profile(track_name: str, track_config: str) -> Path | None:
    track_slug = slugify(track_name)
    config_slug = slugify(track_config)
    candidates = [
        TRACK_DIR / f"{track_slug}_{config_slug}.json",
        TRACK_DIR / f"{track_slug}.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_observations() -> list[ObservationSample]:
    rows: list[ObservationSample] = []
    for path in sorted(OBS_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        perf = payload.get("performance", {}) or {}
        lap_time = perf.get("best_lap_time_s") or perf.get("lap_time_s")
        try:
            lap_time_s = float(lap_time)
        except (TypeError, ValueError):
            continue
        if lap_time_s < 60.0:
            continue
        # Exclude "dangerous" observations from calibration — these have critical
        # safety issues (vortex burst, severe bottoming) that contaminate the
        # score-vs-laptime correlation signal.
        assessment = (payload.get("diagnosis", {}) or {}).get("assessment", "")
        if assessment == "dangerous":
            continue
        car = str(payload.get("car") or "").strip().lower()
        track = str(payload.get("track") or "").strip()
        track_config = str(payload.get("track_config") or "").strip()
        setup = payload.get("setup", {}) or {}
        telemetry = payload.get("telemetry", {}) or {}
        rows.append(
            ObservationSample(
                path=path,
                session_id=str(payload.get("session_id") or path.stem),
                car=car,
                track=track,
                track_config=track_config,
                lap_time_s=lap_time_s,
                params=normalize_setup_to_canonical_params(setup, car=car),
                telemetry=telemetry,
                performance=perf,
                signal_sources=resolve_validation_signals(telemetry),
            )
        )
    return rows


def _count_by_bucket(rows: list[ObservationSample]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        counts.setdefault(row.car, {})
        track_key = f"{row.track} ({row.track_config or 'default'})"
        counts[row.car][track_key] = counts[row.car].get(track_key, 0) + 1
    return counts


def _confidence_tier(row: ObservationSample, count: int) -> str:
    track_slug = slugify(row.track)
    if row.car == "bmw" and track_slug == "sebring_international_raceway":
        return "calibrated"
    if row.car == "ferrari" and track_slug == "sebring_international_raceway":
        return "partial"
    if row.car == "cadillac" and track_slug == "silverstone_circuit":
        return "exploratory"
    return "unsupported"


def _serialize_file_mtime(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    ts = path.stat().st_mtime
    return {
        "path": str(path),
        "exists": True,
        "modified_at_utc": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
    }


def _target_samples(rows: list[ObservationSample]) -> list[ObservationSample]:
    return [
        row
        for row in rows
        if row.car == "bmw" and slugify(row.track) == "sebring_international_raceway"
    ]


def build_validation_report() -> dict[str, Any]:
    observations = load_observations()
    if not observations:
        raise RuntimeError("No observation files with usable lap times were found.")

    target_rows = _target_samples(observations)
    if not target_rows:
        raise RuntimeError("No BMW/Sebring observations were found.")

    track_path = _find_track_profile(target_rows[0].track, target_rows[0].track_config)
    track_profile = TrackProfile.load(str(track_path)) if track_path is not None else None
    objective = ObjectiveFunction(get_car("bmw"), track_profile, scenario_profile="single_lap_safe")

    scored_rows: list[dict[str, Any]] = []
    for row in target_rows:
        status = {
            "session_id": row.session_id,
            "filename": row.path.name,
            "lap_time_s": row.lap_time_s,
            "vetoed": False,
            "score_ms": float("nan"),
            "lap_gain_ms": float("nan"),
            "platform_risk_ms": float("nan"),
            "telemetry_uncertainty_ms": float("nan"),
            "envelope_penalty_ms": float("nan"),
            "staleness_penalty_ms": float("nan"),
            "empirical_penalty_ms": float("nan"),
            "veto_reasons": [],
            "signal_sources": row.signal_sources,
            "params": row.params,
        }
        try:
            ev = objective.evaluate(row.params)
            status.update(
                {
                    "score_ms": ev.score,
                    "vetoed": ev.hard_vetoed,
                    "veto_reasons": list(ev.veto_reasons),
                    "lap_gain_ms": ev.breakdown.lap_gain_ms,
                    "platform_risk_ms": ev.breakdown.platform_risk.total_ms,
                    "telemetry_uncertainty_ms": ev.breakdown.telemetry_uncertainty.total_ms,
                    "envelope_penalty_ms": ev.breakdown.envelope_penalty.total_ms,
                    "staleness_penalty_ms": ev.breakdown.staleness_penalty_ms,
                    "empirical_penalty_ms": ev.breakdown.empirical_penalty_ms,
                }
            )
        except Exception as exc:
            status["error"] = str(exc)
        scored_rows.append(status)

    all_valid = [row for row in scored_rows if not math.isnan(float(row["score_ms"]))]
    non_vetoed = [row for row in all_valid if not bool(row["vetoed"])]
    lap_times_all = [float(row["lap_time_s"]) for row in all_valid]
    scores_all = [float(row["score_ms"]) for row in all_valid]
    lap_times_nv = [float(row["lap_time_s"]) for row in non_vetoed]
    scores_nv = [float(row["score_ms"]) for row in non_vetoed]

    param_correlations: list[dict[str, Any]] = []
    numeric_param_keys = sorted(
        key
        for key in target_rows[0].params.keys()
        if isinstance(target_rows[0].params.get(key), (int, float))
    )
    for key in numeric_param_keys:
        values = [row["params"].get(key) for row in non_vetoed]
        if any(value is None for value in values):
            continue
        numeric_values = [float(value) for value in values]
        if len(set(round(value, 6) for value in numeric_values)) < 2:
            continue
        param_correlations.append(
            {
                "field": key,
                "pearson_r": pearson_r(numeric_values, lap_times_nv),
                "spearman_r": spearman_r(numeric_values, lap_times_nv),
            }
        )
    param_correlations.sort(key=lambda item: abs(item["spearman_r"]), reverse=True)

    signal_usage: dict[str, dict[str, int]] = {}
    for row in scored_rows:
        for metric, resolved in row["signal_sources"].items():
            bucket = signal_usage.setdefault(metric, {"direct": 0, "fallback": 0, "missing": 0})
            source = str(resolved.get("source") or "missing")
            bucket[source] = bucket.get(source, 0) + 1

    newest_observation = max(sample.path.stat().st_mtime for sample in observations)
    model_paths = [
        ROOT / "data" / "learnings" / "heave_calibration_bmw_sebring.json",
        ROOT / "data" / "learnings" / "models" / "bmw_sebring_empirical.json",
        ROOT / "data" / "learnings" / "models" / "bmw_global_empirical.json",
    ]
    freshness = []
    for path in model_paths:
        entry = _serialize_file_mtime(path)
        if entry.get("exists"):
            entry["older_than_latest_observation_days"] = round(
                max(0.0, newest_observation - path.stat().st_mtime) / 86400.0,
                2,
            )
        freshness.append(entry)

    bmw_car = get_car("bmw")
    garage_model = bmw_car.active_garage_output_model("sebring")
    total_fallbacks = sum(bucket["fallback"] for bucket in signal_usage.values())
    total_missing = sum(bucket["missing"] for bucket in signal_usage.values())

    support_rows: list[dict[str, Any]] = []
    by_bucket: dict[tuple[str, str, str], int] = {}
    exemplar_rows: dict[tuple[str, str, str], ObservationSample] = {}
    for row in observations:
        bucket_key = (row.car, row.track, row.track_config)
        by_bucket[bucket_key] = by_bucket.get(bucket_key, 0) + 1
        exemplar_rows.setdefault(bucket_key, row)
    for bucket_key, count in sorted(by_bucket.items(), key=lambda item: (item[0][0], item[0][1])):
        exemplar = exemplar_rows[bucket_key]
        support_rows.append(
            {
                "car": exemplar.car,
                "track": exemplar.track,
                "track_config": exemplar.track_config,
                "samples": count,
                "confidence_tier": _confidence_tier(exemplar, count),
            }
        )

    claim_audit = {
        "garage_output_regressions": {
            "status": "supported" if garage_model is not None else "unsupported",
            "detail": "BMW/Sebring garage-output model is available for full rematerialized legality checks."
            if garage_model is not None
            else "No BMW/Sebring garage-output model is configured.",
        },
        "telemetry_extraction_proxies": {
            "status": "partial" if total_fallbacks > 0 or total_missing > 0 else "supported",
            "detail": f"{total_fallbacks} fallback signal resolutions and {total_missing} missing signal resolutions were observed across validation metrics.",
        },
        "learned_corrections": {
            "status": "supported" if any(entry.get("exists") for entry in freshness) else "unsupported",
            "detail": "Empirical and heave-calibration model files were found for BMW/Sebring."
            if any(entry.get("exists") for entry in freshness)
            else "No BMW/Sebring empirical or heave-calibration files were found.",
        },
        "predictor_directionality": {
            "status": "unverified",
            "detail": "Directional predictor claims remain downgraded until the objective ranking and full predictor sanity metrics show stable negative correlation with lap time.",
        },
        "objective_ranking": {
            "status": "unverified",
            "detail": "Current score-vs-lap correlation remains near zero, so objective rankings are not authoritative yet.",
        },
    }
    calibration_summary = build_calibration_report(include_search=False)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "workflow_map": [
            "IBT",
            "track/analyzer",
            "diagnosis/driver/style",
            "solve_chain/legality",
            "report/.sto",
            "webapp",
        ],
        "support_matrix": support_rows,
        "sample_counts_by_car_track": _count_by_bucket(observations),
        "bmw_sebring": {
            "samples": len(target_rows),
            "non_vetoed_samples": len(non_vetoed),
            "vetoed_samples": len(target_rows) - len(non_vetoed),
            "veto_rate": (len(target_rows) - len(non_vetoed)) / len(target_rows),
            "score_correlation": {
                "pearson_r_all_valid": pearson_r(scores_all, lap_times_all),
                "spearman_r_all_valid": spearman_r(scores_all, lap_times_all),
                "pearson_r_non_vetoed": pearson_r(scores_nv, lap_times_nv),
                "spearman_r_non_vetoed": spearman_r(scores_nv, lap_times_nv),
            },
            "top_parameter_correlations": param_correlations[:12],
            "signal_usage": signal_usage,
            "model_freshness": freshness,
            "claim_audit": claim_audit,
            "objective_recalibration": {
                "track_aware_spearman_r": calibration_summary["modes"]["track_aware"]["score_correlation"]["spearman_r"],
                "trackless_spearman_r": calibration_summary["modes"]["trackless"]["score_correlation"]["spearman_r"],
                "track_aware_holdout_mean_spearman_r": calibration_summary["modes"]["track_aware"]["holdout_validation"]["current_runtime"]["mean_spearman_r"],
                "track_aware_holdout_worst_spearman_r": calibration_summary["modes"]["track_aware"]["holdout_validation"]["current_runtime"]["worst_spearman_r"],
                "recommended_runtime_profile": calibration_summary["recommended_runtime_profile"],
            },
            "track_profile": str(track_path) if track_path is not None else None,
            "rows": scored_rows,
        },
    }


def write_report(report: dict[str, Any]) -> None:
    bmw = report["bmw_sebring"]
    score_corr = bmw["score_correlation"]
    lines = [
        f"## Objective Validation — {report['generated_at_utc'][:10]}",
        "",
        "### Workflow",
        "",
        f"`{' -> '.join(report['workflow_map'])}`",
        "",
        "### Support Tiers",
        "",
        "| Car | Track | Samples | Confidence |",
        "|-----|-------|---------|------------|",
    ]
    for row in report["support_matrix"]:
        lines.append(
            f"| {row['car']} | {row['track']} ({row['track_config'] or 'default'}) | {row['samples']} | {row['confidence_tier']} |"
        )

    lines.extend(
        [
            "",
            "### BMW/Sebring Evidence",
            "",
            f"- Samples: `{bmw['samples']}` total, `{bmw['non_vetoed_samples']}` non-vetoed",
            f"- Veto rate: `{bmw['veto_rate']:.3f}`",
            f"- Score correlation (all valid): Pearson `{score_corr['pearson_r_all_valid']:+.6f}`, Spearman `{score_corr['spearman_r_all_valid']:+.6f}`",
            f"- Score correlation (non-vetoed): Pearson `{score_corr['pearson_r_non_vetoed']:+.6f}`, Spearman `{score_corr['spearman_r_non_vetoed']:+.6f}`",
            "",
            "### Recalibration Snapshot",
            "",
            f"- Track-aware Spearman: `{bmw['objective_recalibration']['track_aware_spearman_r']:+.6f}`",
            f"- Trackless Spearman: `{bmw['objective_recalibration']['trackless_spearman_r']:+.6f}`",
            f"- Track-aware holdout mean Spearman: `{bmw['objective_recalibration']['track_aware_holdout_mean_spearman_r']:+.6f}`",
            f"- Track-aware holdout worst Spearman: `{bmw['objective_recalibration']['track_aware_holdout_worst_spearman_r']:+.6f}`",
            f"- Recommended runtime evidence mode: `{bmw['objective_recalibration']['recommended_runtime_profile']['mode']}`",
            f"- Auto-apply enabled: `{bmw['objective_recalibration']['recommended_runtime_profile']['auto_apply']}`",
            "",
            "### Claim Audit",
            "",
        ]
    )
    for claim_name, payload in bmw["claim_audit"].items():
        lines.append(f"- `{claim_name}`: **{payload['status']}** — {payload['detail']}")

    lines.extend(
        [
            "",
            "### Signal Usage",
            "",
            "| Metric | Direct | Fallback | Missing |",
            "|--------|--------|----------|---------|",
        ]
    )
    for metric, counts in sorted(bmw["signal_usage"].items()):
        lines.append(
            f"| {metric} | {counts['direct']} | {counts['fallback']} | {counts['missing']} |"
        )

    lines.extend(
        [
            "",
            "### Top Raw Setup Correlations",
            "",
            "| Field | Pearson r | Spearman r |",
            "|-------|-----------|------------|",
        ]
    )
    for row in bmw["top_parameter_correlations"]:
        lines.append(
            f"| {row['field']} | {row['pearson_r']:+.6f} | {row['spearman_r']:+.6f} |"
        )

    lines.extend(
        [
            "",
            "### Model Freshness",
            "",
            "| File | Exists | Modified (UTC) | Older Than Latest Observation (days) |",
            "|------|--------|----------------|--------------------------------------|",
        ]
    )
    for row in bmw["model_freshness"]:
        lines.append(
            f"| {Path(row['path']).name} | {row.get('exists', False)} | {row.get('modified_at_utc', 'N/A')} | {row.get('older_than_latest_observation_days', 'N/A')} |"
        )

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(json.dumps(report, indent=2, allow_nan=True), encoding="utf-8")


def main() -> None:
    os.chdir(ROOT)
    report = build_validation_report()
    write_report(report)
    bmw = report["bmw_sebring"]
    corr = bmw["score_correlation"]["spearman_r_non_vetoed"]
    print(f"BMW/Sebring samples: {bmw['samples']} (non-vetoed {bmw['non_vetoed_samples']})")
    print(f"BMW/Sebring Spearman (non-vetoed): {corr:+.6f}")
    print(f"Wrote {OUT_MD}")
    print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
