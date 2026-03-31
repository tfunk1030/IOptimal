"""Publish validated calibration models into runtime-friendly artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from calibration.models import CalibrationReport, FittedModelArtifact, ensure_parent


def _read_artifact(path: Path) -> FittedModelArtifact:
    return FittedModelArtifact.from_dict(json.loads(path.read_text(encoding="utf-8")))


def publish_model_artifact(path: str | Path, artifact: FittedModelArtifact) -> Path:
    target = ensure_parent(path)
    target.write_text(json.dumps(artifact.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return target


def publish_report(path: str | Path, report: CalibrationReport) -> Path:
    target = ensure_parent(path)
    target.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return target


def publish_support_tier(path: str | Path, *, support_tier: str, metadata: dict[str, Any] | None = None) -> Path:
    target = ensure_parent(path)
    payload = {
        "support_tier": support_tier,
        "metadata": dict(metadata or {}),
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return target


def publish_models(*, car: str, track: str, model_root: str | Path) -> dict[str, Any]:
    root = Path(model_root)
    outputs: dict[str, str] = {}
    copied_types: list[str] = []
    runtime_root = Path("data/calibration/models") / car / track
    runtime_root.mkdir(parents=True, exist_ok=True)

    for model_name in (
        "garage_model.json",
        "ride_height_model.json",
        "telemetry_model.json",
        "damper_model.json",
        "diff_model.json",
    ):
        src = root / model_name
        if not src.exists():
            continue
        artifact = _read_artifact(src)
        dst = runtime_root / model_name
        publish_model_artifact(dst, artifact)
        outputs[model_name] = str(dst)
        copied_types.append(artifact.model_type)

    report_path = root / "calibration_report.json"
    support_tier = "unsupported"
    report_payload: dict[str, Any] | None = None
    if report_path.exists():
        report_payload = json.loads(report_path.read_text(encoding="utf-8"))
        support_tier = str(report_payload.get("support_tier") or "unsupported")
        publish_report(runtime_root / "calibration_report.json", CalibrationReport(**report_payload))

    support_path = publish_support_tier(
        runtime_root / "support_tier.json",
        support_tier=support_tier,
        metadata={
            "car": car,
            "track": track,
            "copied_model_types": copied_types,
        },
    )
    outputs["support_tier.json"] = str(support_path)

    return {
        "car": car,
        "track": track,
        "runtime_root": str(runtime_root),
        "published_files": outputs,
        "support_tier": support_tier,
        "copied_model_types": copied_types,
        "report_present": report_payload is not None,
    }
