"""Validation helpers for raw calibration dataset trees."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from calibration.ferrari_aliases import flatten_ferrari_carsetup_rows, resolve_ferrari_row


REQUIRED_MANIFEST_KEYS = ("sample_id", "car", "track", "sample_type", "artifacts")


def validate_raw_dataset(raw_root: str | Path) -> dict[str, Any]:
    root = Path(raw_root)
    manifests = sorted(root.glob("**/manifest.json"))
    report: dict[str, Any] = {
        "raw_root": str(root),
        "manifest_count": len(manifests),
        "valid_count": 0,
        "invalid_count": 0,
        "samples": [],
        "warnings": [],
    }
    if not manifests:
        report["warnings"].append("no_manifest_files_found")
        return report

    for manifest_path in manifests:
        sample_report = {
            "manifest": str(manifest_path),
            "valid": True,
            "issues": [],
            "artifacts": {},
        }
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            sample_report["valid"] = False
            sample_report["issues"].append(f"manifest_parse_error:{exc}")
            report["invalid_count"] += 1
            report["samples"].append(sample_report)
            continue

        for key in REQUIRED_MANIFEST_KEYS:
            if key not in payload:
                sample_report["valid"] = False
                sample_report["issues"].append(f"missing_manifest_key:{key}")

        artifacts = dict(payload.get("artifacts") or {})
        for name, relative_path in artifacts.items():
            if isinstance(relative_path, list):
                missing = []
                for item in relative_path:
                    target = manifest_path.parent / str(item)
                    if not target.exists():
                        missing.append(str(target))
                sample_report["artifacts"][name] = {
                    "kind": "list",
                    "missing": missing,
                    "count": len(relative_path),
                }
                if missing:
                    sample_report["valid"] = False
                    sample_report["issues"].append(f"missing_artifacts:{name}:{len(missing)}")
                continue
            target = manifest_path.parent / str(relative_path)
            exists = target.exists()
            sample_report["artifacts"][name] = {
                "path": str(target),
                "exists": exists,
            }
            if not exists:
                sample_report["valid"] = False
                sample_report["issues"].append(f"missing_artifact:{name}")

        if sample_report["valid"]:
            report["valid_count"] += 1
        else:
            report["invalid_count"] += 1
        report["samples"].append(sample_report)

    return report


def _rows_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = list(payload.get("rows") or [])
    if rows:
        return rows
    if isinstance(payload.get("CarSetup"), dict):
        return flatten_ferrari_carsetup_rows(payload)
    return []


def summarize_ferrari_setup_rows(rows_payload: dict[str, Any]) -> dict[str, Any]:
    """Summarize Ferrari row-dump coverage against known canonical aliases."""
    coverage: dict[str, int] = {}
    unresolved: list[str] = []
    for row in list(rows_payload.get("rows") or []):
        resolved = resolve_ferrari_row(row)
        if resolved is None:
            label = str(row.get("label") or "").strip()
            if label:
                unresolved.append(label)
            continue
        coverage[resolved.canonical_key] = coverage.get(resolved.canonical_key, 0) + 1
    required = {
        "front_heave_spring_nmm",
        "rear_third_spring_nmm",
        "front_pushrod_offset_mm",
        "rear_pushrod_offset_mm",
        "front_heave_perch_mm",
        "rear_third_perch_mm",
        "front_torsion_od_mm",
        "rear_spring_rate_nmm",
        "front_arb_size",
        "rear_arb_size",
        "front_arb_blade",
        "rear_arb_blade",
        "front_camber_deg",
        "rear_camber_deg",
        "front_toe_mm",
        "rear_toe_mm",
        "brake_bias_pct",
        "front_master_cyl_mm",
        "rear_master_cyl_mm",
        "diff_preload_nm",
        "front_diff_preload_nm",
        "diff_ramp_angles",
        "diff_clutch_plates",
        "tc_gain",
        "tc_slip",
        "fuel_l",
        "fuel_target_l",
        "front_rh_at_speed_mm",
        "rear_rh_at_speed_mm",
    }
    missing = sorted(required - set(coverage))
    return {
        "resolved_fields": coverage,
        "resolved_count": len(coverage),
        "missing_required_fields": missing,
        "unresolved_labels": sorted(set(unresolved)),
        "coverage_ratio": round(1.0 - (len(missing) / max(len(required), 1)), 4),
    }
