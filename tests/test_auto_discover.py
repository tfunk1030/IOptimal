"""Tests for car_model.auto_discover — validates extraction from setup JSON."""

import json
from pathlib import Path

import pytest

from car_model.auto_discover import (
    DiscoveredParameters,
    discover_car_parameters,
    print_discovery_report,
    compute_torsion_c_from_points,
    build_calibration_dataset,
)

FERRARI_SAMPLE_PATH = Path(__file__).parent / "fixtures" / "ferrari_setup_sample.json"


@pytest.fixture
def ferrari_data() -> dict:
    """Load the Ferrari 499P setup JSON sample."""
    return json.loads(FERRARI_SAMPLE_PATH.read_text())


@pytest.fixture
def ferrari_params(ferrari_data) -> DiscoveredParameters:
    return discover_car_parameters(ferrari_data)


class TestDiscovery:

    def test_car_name(self, ferrari_params):
        assert ferrari_params.car_name == "ferrari499p"

    def test_total_rows(self, ferrari_params):
        assert len(ferrari_params.rows) > 80

    def test_mapped_vs_unmapped(self, ferrari_params):
        mapped = sum(1 for r in ferrari_params.rows if r.is_mapped)
        unmapped = sum(1 for r in ferrari_params.rows if not r.is_mapped)
        assert mapped > 40
        assert unmapped > 20

    def test_hidden_front_spring_rate(self, ferrari_params):
        hp = ferrari_params.hidden
        assert hp.front_spring_rate_npm is not None
        assert hp.front_spring_rate_npm == pytest.approx(115170.27, abs=1.0)
        # This is ~115.17 N/mm — the ACTUAL corner spring rate from iRacing's physics
        assert hp.front_spring_rate_npm / 1000.0 == pytest.approx(115.17, abs=0.01)

    def test_hidden_rear_spring_rate(self, ferrari_params):
        hp = ferrari_params.hidden
        assert hp.rear_spring_rate_npm is not None
        assert hp.rear_spring_rate_npm == pytest.approx(105000.0, abs=1.0)
        assert hp.rear_spring_rate_npm / 1000.0 == pytest.approx(105.0, abs=0.01)

    def test_hidden_rear_perch_offset(self, ferrari_params):
        hp = ferrari_params.hidden
        assert hp.lr_perch_offset_m is not None
        # -0.02676 m = -26.76 mm
        assert hp.lr_perch_offset_m * 1000.0 == pytest.approx(-26.76, abs=0.01)

    def test_hidden_heave_damper_settings(self, ferrari_params):
        hp = ferrari_params.hidden
        assert hp.hf_ls_comp == 10
        assert hp.hr_ls_comp == 10
        assert hp.hf_hs_comp == 40
        assert hp.hr_hs_comp == 40
        assert hp.hf_ls_rbd == 10
        assert hp.hr_ls_rbd == 10
        assert hp.hf_hs_rbd == 40
        assert hp.hr_hs_rbd == 40

    def test_hidden_hs_slope_settings(self, ferrari_params):
        hp = ferrari_params.hidden
        assert hp.hf_hs_slope_comp == 5
        assert hp.hr_hs_slope_comp == 5
        assert hp.hf_hs_slope_rbd == 5
        assert hp.hr_hs_slope_rbd == 5
        assert hp.lf_hs_slope_rbd == 5
        assert hp.rf_hs_slope_rbd == 5
        assert hp.lr_hs_slope_rbd == 5
        assert hp.rr_hs_slope_rbd == 5

    def test_hidden_bop_fields(self, ferrari_params):
        hp = ferrari_params.hidden
        assert hp.d_cx_bop == pytest.approx(0.17)
        assert hp.d_cz_t_bop == pytest.approx(0.18)
        assert hp.d_cpxz_bop == pytest.approx(0.0)

    def test_hidden_hub_pitch(self, ferrari_params):
        hp = ferrari_params.hidden
        assert hp.lf_hub_dpitch == pytest.approx(-0.8)
        assert hp.rf_hub_dpitch == pytest.approx(-0.8)

    def test_indexed_springs(self, ferrari_params):
        assert ferrari_params.front_heave_index == 3.0
        assert ferrari_params.rear_heave_index == 5.0
        assert ferrari_params.front_torsion_bar_index == 2.0
        assert ferrari_params.rear_torsion_bar_index == 2.0

    def test_corner_spring_rate_derived(self, ferrari_params):
        assert ferrari_params.front_corner_spring_rate_nmm is not None
        assert ferrari_params.front_corner_spring_rate_nmm == pytest.approx(115.17, abs=0.01)
        assert ferrari_params.rear_corner_spring_rate_nmm is not None
        assert ferrari_params.rear_corner_spring_rate_nmm == pytest.approx(105.0, abs=0.01)

    def test_damper_ranges(self, ferrari_params):
        assert len(ferrari_params.dampers) >= 2  # fixture has LF + LR minimum
        lf = ferrari_params.dampers.get("Left Front Damper")
        assert lf is not None
        assert lf.ls_comp == 20
        assert lf.ls_comp_range == (0, 40)
        assert lf.hs_comp == 20
        assert lf.hs_comp_range == (0, 40)
        assert lf.hs_slope == 11
        assert lf.hs_slope_range == (0, 11)

    def test_garage_ranges(self, ferrari_params):
        rng = ferrari_params.garage_ranges
        assert len(rng) > 10
        # Should find pushrod range
        pushrod_keys = [k for k in rng if "pushrod" in k.lower()]
        assert len(pushrod_keys) >= 2
        for k in pushrod_keys:
            assert rng[k] == (-40.0, 40.0)

    def test_calibration_point(self, ferrari_params):
        cal = ferrari_params.corner_spring_calibration_point()
        assert cal is not None
        assert cal["axle"] == "front"
        assert cal["index"] == 2.0
        assert cal["spring_rate_nmm"] == pytest.approx(115.17, abs=0.01)

    def test_heave_calibration_point(self, ferrari_params):
        hcal = ferrari_params.heave_spring_calibration_point()
        assert hcal is not None
        assert hcal["axle"] == "front"
        assert hcal["index"] == 3.0
        # 115170.27 N/m = 115.17 N/mm — wait, this is the CORNER spring, not heave
        # Actually fSideSpringRateNpm is the front CORNER spring rate (torsion bar)
        # The heave spring rate would need a different hidden field

    def test_summary(self, ferrari_params):
        summary = ferrari_params.summary()
        assert "ferrari499p" in summary
        assert "115" in summary  # front spring rate

    def test_report(self, ferrari_params):
        report = print_discovery_report(ferrari_params)
        assert "AUTO-DISCOVERY REPORT" in report
        assert "ferrari499p" in report
        assert "HIDDEN PHYSICS" in report
        assert "DAMPER RANGES" in report


class TestCalibrationMath:

    def test_two_point_fit(self):
        """Two Ferrari torsion bar calibration points should yield a fit."""
        points = [
            {"index": 2.0, "spring_rate_nmm": 220.6},
            {"index": 18.0, "spring_rate_nmm": 444.8},
        ]
        result = compute_torsion_c_from_points(points)
        assert "k_quarter_intercept" in result
        assert result["n_points"] == 2
        assert result["r_squared"] == pytest.approx(1.0, abs=0.01)

    def test_multi_point_fit(self):
        """Six Ferrari torsion bar calibration points from garage screenshots."""
        points = [
            {"index": 2, "spring_rate_nmm": 220.6},
            {"index": 5, "spring_rate_nmm": 266.9},
            {"index": 9, "spring_rate_nmm": 296.6},
            {"index": 11, "spring_rate_nmm": 317.7},
            {"index": 15, "spring_rate_nmm": 360.7},
            {"index": 18, "spring_rate_nmm": 444.8},
        ]
        result = compute_torsion_c_from_points(points)
        assert result["n_points"] == 6
        assert result["r_squared"] > 0.95

    def test_build_calibration_dataset(self, ferrari_data):
        params = discover_car_parameters(ferrari_data)
        dataset = build_calibration_dataset([params])
        assert dataset["car_name"] == "ferrari499p"
        assert dataset["n_setups"] == 1
        assert "damper_ranges" in dataset


class TestFerrariInsights:
    """Verify the key insights from the Ferrari JSON that solve calibration gaps."""

    def test_front_spring_rate_reveals_wheel_rate(self, ferrari_params):
        """fSideSpringRateNpm = 115170.27 N/m at torsion index 2.

        The current codebase has C = 0.001282 and computes:
          k_wheel = 0.001282 * OD^4  where OD ≈ 20.0 + (idx * 4/18) = 20.44 mm
          k_wheel = 0.001282 * 20.44^4 = 0.001282 * 174,594 = 223.8 N/mm

        But iRacing says 115.17 N/mm! That's a 2:1 ratio.

        This means either:
          1. The C constant is wrong (it should be ~0.000660), OR
          2. fSideSpringRateNpm is a WHEEL rate at a different MR than expected, OR
          3. The OD mapping from index is wrong

        Either way, having this hidden field available PER SESSION means we
        can stop guessing and directly use the physics engine's value.
        """
        actual_rate = ferrari_params.front_corner_spring_rate_nmm
        assert actual_rate is not None

        # Current codebase computation at index 2:
        C = 0.001282
        od = 20.0 + (2.0 * (24.0 - 20.0) / 18.0)  # linear interp
        codebase_prediction = C * od ** 4

        ratio = codebase_prediction / actual_rate
        # The ratio should expose the calibration error
        assert ratio > 1.5, (
            f"Codebase predicts {codebase_prediction:.1f} N/mm but iRacing says "
            f"{actual_rate:.1f} N/mm — ratio {ratio:.2f}x"
        )

    def test_heave_damper_settings_are_separate(self, ferrari_params):
        """Ferrari has SEPARATE heave damper settings (unmapped in garage).

        Corner dampers: LS comp 20/16 (front/rear), visible in garage tab
        Heave dampers: LS comp 10/10 (front/rear), HIDDEN from garage

        The solver currently doesn't know about these separate heave damper
        settings and treats the corner damper values as the only dampers.
        """
        hp = ferrari_params.hidden
        # Corner damper LS comp values (from mapped rows)
        lf_damp = ferrari_params.dampers.get("Left Front Damper")
        assert lf_damp is not None
        corner_front_ls_comp = lf_damp.ls_comp  # 20

        # Heave damper LS comp values (from hidden rows)
        heave_front_ls_comp = hp.hf_ls_comp  # 10

        assert corner_front_ls_comp != heave_front_ls_comp, (
            "Corner and heave damper LS comp should be different!"
        )
        assert corner_front_ls_comp == 20
        assert heave_front_ls_comp == 10

    def test_rear_perch_offset_from_hidden(self, ferrari_params):
        """Hidden lrPerchOffsetm = -0.02676 m = -26.76 mm.

        This is the ACTUAL rear corner spring perch offset that the solver
        needs but currently can't extract from the mapped garage rows.
        """
        hp = ferrari_params.hidden
        assert hp.lr_perch_offset_m is not None
        perch_mm = hp.lr_perch_offset_m * 1000.0
        assert perch_mm == pytest.approx(-26.76, abs=0.01)
