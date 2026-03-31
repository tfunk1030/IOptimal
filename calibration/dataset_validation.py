"""Validation helpers for raw calibration dataset trees."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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
