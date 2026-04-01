"""Output bundle — write all pipeline artifacts to a single directory.

Provides a single-command way to produce every artifact the pipeline can
generate (.sto, .json, text report) in one directory with a consistent
naming scheme.

Usage (from pipeline.produce):
    from output.bundle import OutputBundleOptions, write_output_bundle
    opts = OutputBundleOptions(bundle_dir="output/ferrari_hockenheim_20260331")
    manifest = write_output_bundle(opts, pipeline_result)

Usage (standalone CLI):
    python -m pipeline.produce --car ferrari --ibt session.ibt --bundle-dir ./bundles/run1
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class OutputBundleOptions:
    """Configuration for a bundle output run.

    Attributes:
        bundle_dir:     Directory to write all artifacts into (created if missing).
        stem:           Base filename stem.  Defaults to ``{car}_{track_slug}_{ts}``.
        write_sto:      Write the iRacing .sto setup file.
        write_json:     Write the full JSON summary.
        write_report:   Write the plain-text engineering report.
        write_manifest: Write a machine-readable artifact manifest JSON.
        overwrite:      If False, skip files that already exist.
    """
    bundle_dir: str | Path
    stem: str | None = None
    write_sto: bool = True
    write_json: bool = True
    write_report: bool = True
    write_manifest: bool = True
    overwrite: bool = True


@dataclass
class OutputArtifactManifest:
    """Paths and metadata for every artifact written by write_output_bundle().

    Attributes:
        bundle_dir:    Absolute path to the output directory.
        sto_path:      Path to the .sto file, or None if not written.
        json_path:     Path to the JSON summary, or None if not written.
        report_path:   Path to the text report, or None if not written.
        manifest_path: Path to this manifest file, or None if not written.
        car:           Car canonical name (e.g. "ferrari").
        track:         Full track label (e.g. "Hockenheim GP — Full").
        wing:          Wing angle in degrees.
        fuel_l:        Fuel load in liters.
        timestamp_utc: ISO-8601 UTC timestamp of the bundle run.
        artifacts:     All successfully written artifact paths.
        errors:        Any non-fatal errors encountered during writing.
    """
    bundle_dir: Path
    sto_path: Path | None = None
    json_path: Path | None = None
    report_path: Path | None = None
    manifest_path: Path | None = None
    car: str = ""
    track: str = ""
    wing: float = 0.0
    fuel_l: float = 0.0
    timestamp_utc: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    artifacts: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_dir": str(self.bundle_dir),
            "sto_path": str(self.sto_path) if self.sto_path else None,
            "json_path": str(self.json_path) if self.json_path else None,
            "report_path": str(self.report_path) if self.report_path else None,
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "car": self.car,
            "track": self.track,
            "wing": self.wing,
            "fuel_l": self.fuel_l,
            "timestamp_utc": self.timestamp_utc,
            "artifacts": [str(p) for p in self.artifacts],
            "errors": self.errors,
        }


def _make_stem(car: str, track: str, ts: str | None = None) -> str:
    """Build a filename stem from car + track + optional timestamp."""
    track_slug = (
        track.lower()
        .replace(" — ", "_")
        .replace(" - ", "_")
        .replace(" ", "_")
        .replace("/", "_")
        [:30]  # cap length
    )
    if ts is None:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"{car}_{track_slug}_{ts}"


def write_output_bundle(
    opts: OutputBundleOptions,
    result: dict[str, Any],
    *,
    report_text: str | None = None,
) -> OutputArtifactManifest:
    """Write all requested artifacts for a pipeline result.

    Args:
        opts:        Bundle options controlling what gets written.
        result:      Dict returned by ``produce(_return_result=True)``.
                     Must contain keys: car, track, wing, fuel_l, step1..step6,
                     supporting, legal_validation, solver_notes, etc.
        report_text: Pre-formatted report string.  If None, ``result["report"]``
                     is used.

    Returns:
        OutputArtifactManifest describing what was written.
    """
    bundle_dir = Path(opts.bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    car_obj = result.get("car")
    car_name = getattr(car_obj, "canonical_name", str(car_obj)) if car_obj else "unknown"
    car_display = getattr(car_obj, "name", car_name) if car_obj else car_name
    track_obj = result.get("track")
    track_label = (
        f"{track_obj.track_name} — {track_obj.track_config}"
        if track_obj and hasattr(track_obj, "track_name")
        else str(track_obj) if track_obj else "unknown"
    )
    wing = float(result.get("wing", 0.0))
    fuel_l = float(result.get("fuel_l", 0.0))

    stem = opts.stem or _make_stem(car_name, track_label)

    manifest = OutputArtifactManifest(
        bundle_dir=bundle_dir.resolve(),
        car=car_name,
        track=track_label,
        wing=wing,
        fuel_l=fuel_l,
    )

    # ── .sto setup file ──────────────────────────────────────────────
    if opts.write_sto:
        sto_path = bundle_dir / f"{stem}.sto"
        if not sto_path.exists() or opts.overwrite:
            try:
                from output.setup_writer import write_sto
                from output.garage_validator import validate_and_fix_garage_correlation

                step1 = result["step1"]
                step2 = result["step2"]
                step3 = result["step3"]
                step4 = result["step4"]
                step5 = result["step5"]
                step6 = result["step6"]
                supporting = result["supporting"]

                # Run garage validation before writing
                garage_warnings = validate_and_fix_garage_correlation(
                    car_obj, step1, step2, step3, step5,
                    fuel_l=fuel_l, track_name=track_label,
                )

                # Build extra kwargs for Ferrari-specific fields
                _extra_kw: dict[str, Any] = {
                    "tyre_pressure_kpa": getattr(supporting, "tyre_cold_fl_kpa", None),
                    "brake_bias_pct": getattr(supporting, "brake_bias_pct", None),
                    "brake_bias_target": getattr(supporting, "brake_bias_target", None),
                    "brake_bias_migration": getattr(supporting, "brake_bias_migration", None),
                    "front_master_cyl_mm": getattr(supporting, "front_master_cyl_mm", None),
                    "rear_master_cyl_mm": getattr(supporting, "rear_master_cyl_mm", None),
                    "pad_compound": getattr(supporting, "pad_compound", None),
                    "diff_clutch_plates": getattr(supporting, "diff_clutch_plates", None),
                    "diff_preload_nm": getattr(supporting, "diff_preload_nm", None),
                    "tc_gain": getattr(supporting, "tc_gain", None),
                    "tc_slip": getattr(supporting, "tc_slip", None),
                    "fuel_low_warning_l": getattr(supporting, "fuel_low_warning_l", fuel_l),
                    "gear_stack": getattr(supporting, "gear_stack", ""),
                    "roof_light_color": getattr(supporting, "roof_light_color", ""),
                }
                if car_name == "ferrari":
                    _extra_kw.update({
                        "brake_bias_migration_gain": getattr(
                            result.get("current_setup"), "brake_bias_migration_gain", None
                        ),
                        "front_diff_preload_nm": getattr(
                            result.get("current_setup"), "front_diff_preload_nm", None
                        ),
                        "diff_coast_drive_ramp": (
                            getattr(supporting, "diff_ramp_angles", "")
                            or ("Less Locking" if getattr(supporting, "diff_ramp_coast", 50) >= 45 else "More Locking")
                        ),
                        "fuel_target_l": getattr(supporting, "fuel_target_l", None),
                        "hybrid_rear_drive_enabled": getattr(
                            result.get("current_setup"), "hybrid_rear_drive_enabled", None
                        ),
                        "hybrid_rear_drive_corner_pct": getattr(
                            result.get("current_setup"), "hybrid_rear_drive_corner_pct", None
                        ),
                    })
                else:
                    _extra_kw["diff_coast_drive_ramp"] = (
                        getattr(supporting, "diff_ramp_angles", "")
                        or f"{getattr(supporting, 'diff_ramp_coast', 45)}/{getattr(supporting, 'diff_ramp_drive', 70)}"
                    )

                # Remove None values to avoid writer errors
                _extra_kw = {k: v for k, v in _extra_kw.items() if v is not None}

                written = write_sto(
                    car_name=car_display,
                    track_name=track_label,
                    wing=wing,
                    fuel_l=fuel_l,
                    step1=step1, step2=step2, step3=step3,
                    step4=step4, step5=step5, step6=step6,
                    output_path=str(sto_path),
                    car_canonical=car_name,
                    **_extra_kw,
                )
                manifest.sto_path = Path(written).resolve()
                manifest.artifacts.append(manifest.sto_path)
            except Exception as exc:
                manifest.errors.append(f"sto: {exc}")

    # ── JSON summary ─────────────────────────────────────────────────
    if opts.write_json:
        json_path = bundle_dir / f"{stem}.json"
        if not json_path.exists() or opts.overwrite:
            try:
                from output.report import to_public_output_payload

                output: dict[str, Any] = {
                    "car": car_display,
                    "track": track_label,
                    "wing": wing,
                    "fuel_l": fuel_l,
                    "lap_time_s": result.get("lap_time_s"),
                    "lap_number": result.get("lap_number"),
                    "assessment": getattr(result.get("diagnosis"), "assessment", None),
                    "scenario_profile": result.get("scenario_profile"),
                    "selected_candidate_family": result.get("selected_candidate_family"),
                    "selected_candidate_score": result.get("selected_candidate_score"),
                    "selected_candidate_applied": result.get("selected_candidate_applied"),
                    "legal_validation": (
                        result["legal_validation"].to_dict()
                        if result.get("legal_validation") is not None
                        else None
                    ),
                    "solver_notes": result.get("solver_notes", []),
                    "step1_rake": to_public_output_payload(car_name, result.get("step1")),
                    "step2_heave": to_public_output_payload(car_name, result.get("step2")),
                    "step3_corner": to_public_output_payload(car_name, result.get("step3")),
                    "step4_arb": to_public_output_payload(car_name, result.get("step4")),
                    "step5_geometry": to_public_output_payload(car_name, result.get("step5")),
                    "step6_dampers": to_public_output_payload(car_name, result.get("step6")),
                    "supporting": to_public_output_payload(car_name, result.get("supporting")),
                    "bundle_timestamp_utc": manifest.timestamp_utc,
                }
                json_path.write_text(
                    json.dumps(output, indent=2, default=str), encoding="utf-8"
                )
                manifest.json_path = json_path.resolve()
                manifest.artifacts.append(manifest.json_path)
            except Exception as exc:
                manifest.errors.append(f"json: {exc}")

    # ── Text report ──────────────────────────────────────────────────
    if opts.write_report:
        rpt_path = bundle_dir / f"{stem}_report.txt"
        if not rpt_path.exists() or opts.overwrite:
            try:
                text = report_text or result.get("report", "")
                if text:
                    rpt_path.write_text(str(text), encoding="utf-8")
                    manifest.report_path = rpt_path.resolve()
                    manifest.artifacts.append(manifest.report_path)
            except Exception as exc:
                manifest.errors.append(f"report: {exc}")

    # ── Artifact manifest JSON ────────────────────────────────────────
    if opts.write_manifest:
        mf_path = bundle_dir / f"{stem}_manifest.json"
        if not mf_path.exists() or opts.overwrite:
            try:
                mf_path.write_text(
                    json.dumps(manifest.to_dict(), indent=2), encoding="utf-8"
                )
                manifest.manifest_path = mf_path.resolve()
                manifest.artifacts.append(manifest.manifest_path)
            except Exception as exc:
                manifest.errors.append(f"manifest: {exc}")

    return manifest


def bundle_from_pipeline_result(
    bundle_dir: str | Path,
    result: dict[str, Any],
    *,
    stem: str | None = None,
    report_text: str | None = None,
) -> OutputArtifactManifest:
    """Convenience wrapper: write a full bundle from a pipeline result dict.

    Args:
        bundle_dir:  Output directory path.
        result:      Dict from ``produce(_return_result=True)``.
        stem:        Optional file stem override.
        report_text: Optional pre-rendered report text.

    Returns:
        OutputArtifactManifest.
    """
    opts = OutputBundleOptions(bundle_dir=bundle_dir, stem=stem)
    return write_output_bundle(opts, result, report_text=report_text)
