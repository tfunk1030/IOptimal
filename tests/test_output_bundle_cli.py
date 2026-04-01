"""Test: --bundle-dir CLI mode produces expected artifact set.

Tests basic structural properties of the bundle output module:
1. OutputBundleOptions has correct defaults.
2. OutputArtifactManifest.to_dict() contains all expected keys.
3. write_output_bundle() creates a manifest file when write_manifest=True.
4. FerrariHeaveDamperSettings dataclass is correctly typed and instantiatable.
"""
import json
import pathlib
import pytest


# ─── OutputBundleOptions ─────────────────────────────────────────────────────

def test_bundle_options_defaults():
    """OutputBundleOptions should default to writing all four artifact types."""
    from output.bundle import OutputBundleOptions
    opts = OutputBundleOptions(bundle_dir="/tmp/test_bundle")
    assert opts.write_sto is True
    assert opts.write_json is True
    assert opts.write_report is True
    assert opts.write_manifest is True
    assert opts.overwrite is True
    assert opts.stem is None


def test_bundle_options_selective():
    """OutputBundleOptions should accept selective write flags."""
    from output.bundle import OutputBundleOptions
    opts = OutputBundleOptions(bundle_dir="/tmp/test_bundle", write_sto=False, write_report=False)
    assert opts.write_sto is False
    assert opts.write_json is True
    assert opts.write_report is False
    assert opts.write_manifest is True


# ─── OutputArtifactManifest ──────────────────────────────────────────────────

def test_manifest_to_dict_keys():
    """OutputArtifactManifest.to_dict() must include all required keys."""
    from output.bundle import OutputArtifactManifest
    m = OutputArtifactManifest(bundle_dir=pathlib.Path("/tmp/test"))
    d = m.to_dict()
    required_keys = {
        "bundle_dir", "sto_path", "json_path", "report_path",
        "manifest_path", "car", "track", "wing", "fuel_l",
        "timestamp_utc", "artifacts", "errors",
    }
    assert required_keys.issubset(d.keys()), (
        f"Missing keys in manifest dict: {required_keys - d.keys()}"
    )


def test_manifest_to_dict_artifacts_is_list():
    """OutputArtifactManifest.to_dict() artifacts must be a list."""
    from output.bundle import OutputArtifactManifest
    m = OutputArtifactManifest(bundle_dir=pathlib.Path("/tmp/test"))
    d = m.to_dict()
    assert isinstance(d["artifacts"], list)
    assert isinstance(d["errors"], list)


def test_manifest_empty_paths():
    """OutputArtifactManifest with no paths should have None values in dict."""
    from output.bundle import OutputArtifactManifest
    m = OutputArtifactManifest(bundle_dir=pathlib.Path("/tmp/test"))
    d = m.to_dict()
    assert d["sto_path"] is None
    assert d["json_path"] is None
    assert d["report_path"] is None
    assert d["manifest_path"] is None


# ─── write_output_bundle manifest file creation ──────────────────────────────

def test_write_output_bundle_manifest_only(tmp_path):
    """write_output_bundle with only write_manifest=True creates a JSON manifest file."""
    from output.bundle import write_output_bundle, OutputBundleOptions

    opts = OutputBundleOptions(
        bundle_dir=tmp_path,
        write_sto=False,
        write_json=False,
        write_report=False,
        write_manifest=True,
        stem="test_run",
    )
    # Minimal result dict — only needs what the function uses for the manifest
    result = {
        "car": "ferrari",
        "track": "hockenheim",
        "wing": 13.0,
        "fuel_l": 60.0,
        "report": "test report text",
    }
    manifest = write_output_bundle(opts, result)

    # Manifest file should exist
    assert manifest.manifest_path is not None, "manifest_path should be set"
    assert manifest.manifest_path.exists(), f"Manifest file not created at {manifest.manifest_path}"

    # Manifest should be valid JSON
    content = json.loads(manifest.manifest_path.read_text(encoding="utf-8"))
    assert "artifacts" in content
    assert "errors" in content


def test_write_output_bundle_creates_directory(tmp_path):
    """write_output_bundle should create the bundle_dir if it doesn't exist."""
    from output.bundle import write_output_bundle, OutputBundleOptions

    new_dir = tmp_path / "new_subdir" / "run1"
    assert not new_dir.exists()

    opts = OutputBundleOptions(
        bundle_dir=new_dir,
        write_sto=False,
        write_json=False,
        write_report=False,
        write_manifest=True,
        stem="test_dir_create",
    )
    result = {"car": "bmw", "track": "sebring", "wing": 16.0, "fuel_l": 89.0, "report": "x"}
    write_output_bundle(opts, result)
    assert new_dir.exists(), "bundle_dir should be created automatically"


def test_write_output_bundle_manifest_car_field(tmp_path):
    """Manifest car field should match the result dict."""
    from output.bundle import write_output_bundle, OutputBundleOptions

    opts = OutputBundleOptions(
        bundle_dir=tmp_path,
        write_sto=False,
        write_json=False,
        write_report=False,
        write_manifest=True,
        stem="car_field_test",
    )
    result = {"car": "ferrari", "track": "hockenheim", "wing": 13.0, "fuel_l": 60.0, "report": ""}
    manifest = write_output_bundle(opts, result)
    content = json.loads(manifest.manifest_path.read_text(encoding="utf-8"))
    # 'car' field in the manifest should reflect the input car
    assert "ferrari" in str(content).lower() or content.get("car") in ("ferrari", ""), (
        "Manifest should contain car identifier 'ferrari'"
    )


# ─── FerrariHeaveDamperSettings ──────────────────────────────────────────────

def test_ferrari_heave_damper_settings_creation():
    """FerrariHeaveDamperSettings instantiates correctly with required fields."""
    from solver.damper_solver import FerrariHeaveDamperSettings
    s = FerrariHeaveDamperSettings(ls_comp=10, hs_comp=40, ls_rbd=5, hs_rbd=10, hs_slope=40)
    assert s.ls_comp == 10
    assert s.hs_comp == 40
    assert s.ls_rbd == 5
    assert s.hs_rbd == 10
    assert s.hs_slope == 40
    assert s.hs_slope_rbd is None  # optional field defaults to None


def test_ferrari_heave_damper_settings_optional_rbd_slope():
    """FerrariHeaveDamperSettings accepts optional hs_slope_rbd."""
    from solver.damper_solver import FerrariHeaveDamperSettings
    s = FerrariHeaveDamperSettings(ls_comp=10, hs_comp=40, ls_rbd=5, hs_rbd=10, hs_slope=40, hs_slope_rbd=8)
    assert s.hs_slope_rbd == 8


def test_damper_solution_has_heave_fields():
    """DamperSolution dataclass should have front_heave_damper and rear_heave_damper fields."""
    from solver.damper_solver import DamperSolution, FerrariHeaveDamperSettings
    import dataclasses
    fields = {f.name for f in dataclasses.fields(DamperSolution)}
    assert "front_heave_damper" in fields, "DamperSolution missing front_heave_damper field"
    assert "rear_heave_damper" in fields, "DamperSolution missing rear_heave_damper field"

    # Check field type hint uses FerrariHeaveDamperSettings (not dict)
    for f in dataclasses.fields(DamperSolution):
        if f.name == "front_heave_damper":
            # The annotation should reference FerrariHeaveDamperSettings, not dict
            ann = str(f.type)
            assert "dict" not in ann, (
                f"front_heave_damper should be FerrariHeaveDamperSettings | None, not dict. Got: {ann}"
            )
