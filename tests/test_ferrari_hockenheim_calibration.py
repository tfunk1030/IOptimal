from pathlib import Path
import sys
import json
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ferrari_hockenheim_calibration import build_calibration_report


def test_build_calibration_report_without_telemetry_files():
    setup_json = REPO_ROOT / "tests" / "fixtures" / "ferrari_hockenheim_setupdelta.json"

    report = build_calibration_report(
        setup_json=setup_json,
        telemetry=[
            Path("/tmp/nonexistent_a.zip"),
            Path("/tmp/nonexistent_b.zip"),
        ],
    )

    assert report["car"] == "ferrari499p"
    assert report["track"] == "hockenheim_grand_prix"
    assert len(report["telemetry_sessions"]) == 0
    assert len(report["telemetry_files_missing"]) == 2

    rec = report["recommended_settings"]
    assert rec["rear_wing_angle_deg"]["baseline"] == 17.0
    assert rec["rear_wing_angle_deg"]["recommended"] == 15.0
    assert rec["brake_pressure_bias_pct"]["recommended"] == 50.0
    assert rec["aero_rake_target_mm"]["baseline"] == 25.0
    assert rec["aero_rake_target_mm"]["recommended"] == 23.0
    assert report["setting_correlations"]["dynamic_rake_mm"] == 25.0
    assert report["setup_validation"]["summary"]["out_of_range_rows"] == 0
    assert report["setup_validation"]["summary"]["duplicate_row_ids"] == 0


def test_rejects_non_ferrari_payload(tmp_path: Path):
    payload = {"carName": "bmwlmdh", "rows": []}
    setup_json = tmp_path / "bad.json"
    setup_json.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="carName='ferrari499p'"):
        build_calibration_report(setup_json=setup_json, telemetry=[])
