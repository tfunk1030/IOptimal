"""W4.3: GT3 architecture guards in `output/garage_validator.py` and
`output/report.py`.

Verifies:
  1. `validate_and_fix_garage_correlation` does not crash or mutate GT3
     step2/step3 toward GTP-only physics (heave perch, torsion OD).
  2. `_fix_slider`, `_fix_torsion_bar_defl`, `_fix_front_rh` early-return
     on GT3 (audit O19/O20/O21).
  3. GTP regression: BMW M Hybrid V8 still exercises the heave-slider /
     torsion-bar / front-RH fixers.
  4. `print_full_setup_report` for a GT3 car omits "Heave F:" / "Third R:"
     literal text and renders 4 corner spring rates instead.
  5. GTP regression: report for GTP BMW still renders "Heave F:" / "Third R:".
  6. GT3 GarageRanges has bump_rubber_gap_*_mm fields with non-default ranges.
  7. GTP GarageRanges has the default (0.0, 0.0) sentinel.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from car_model.cars import get_car
from output.garage_validator import (
    _fix_front_rh,
    _fix_slider,
    _fix_torsion_bar_defl,
    validate_and_fix_garage_correlation,
)


def _gt3_step_bundle():
    """GT3 step bundle: HeaveSolution.null()-equivalent step2 + GT3 step3."""
    step1 = SimpleNamespace(
        front_pushrod_offset_mm=0.0,
        rear_pushrod_offset_mm=0.0,
        static_front_rh_mm=72.6,
        static_rear_rh_mm=82.6,
        rake_static_mm=10.0,
    )
    step2 = SimpleNamespace(
        present=False,
        front_heave_nmm=0.0,
        rear_third_nmm=0.0,
        perch_offset_front_mm=0.0,
        perch_offset_rear_mm=0.0,
        front_excursion_at_rate_mm=0.0,
    )
    step3 = SimpleNamespace(
        front_torsion_od_mm=0.0,        # GT3: no torsion bar
        rear_spring_rate_nmm=180.0,
        rear_spring_perch_mm=0.0,
        front_coil_rate_nmm=250.0,
        front_coil_perch_mm=0.0,
    )
    step5 = SimpleNamespace(
        front_camber_deg=-4.0,
        rear_camber_deg=-2.8,
        front_toe_mm=-0.4,
        rear_toe_mm=1.5,
    )
    return step1, step2, step3, step5


def _gtp_bmw_step_bundle():
    """GTP BMW step bundle (regression baseline)."""
    step1 = SimpleNamespace(
        front_pushrod_offset_mm=-25.5,
        rear_pushrod_offset_mm=-23.0,
        static_front_rh_mm=30.2,
        static_rear_rh_mm=49.2,
        rake_static_mm=19.0,
    )
    step2 = SimpleNamespace(
        front_heave_nmm=50.0,
        rear_third_nmm=440.0,
        perch_offset_front_mm=-7.0,
        perch_offset_rear_mm=43.0,
        front_excursion_at_rate_mm=13.9,
    )
    step3 = SimpleNamespace(
        front_torsion_od_mm=13.9,
        rear_spring_rate_nmm=150.0,
        rear_spring_perch_mm=30.0,
    )
    step5 = SimpleNamespace(
        front_camber_deg=-2.1,
        rear_camber_deg=-1.8,
        front_toe_mm=-0.4,
        rear_toe_mm=0.3,
    )
    return step1, step2, step3, step5


class GT3GarageValidatorTests(unittest.TestCase):
    def test_validate_does_not_raise_on_gt3(self) -> None:
        car = get_car("bmw_m4_gt3")
        step1, step2, step3, step5 = _gt3_step_bundle()
        # Must not raise
        warnings = validate_and_fix_garage_correlation(
            car=car,
            step1=step1,
            step2=step2,
            step3=step3,
            step5=step5,
            fuel_l=100.0,
            track_name="Spielberg",
        )
        self.assertIsInstance(warnings, list)

    def test_validate_does_not_mutate_gt3_step2_step3(self) -> None:
        car = get_car("bmw_m4_gt3")
        step1, step2, step3, step5 = _gt3_step_bundle()
        validate_and_fix_garage_correlation(
            car=car,
            step1=step1,
            step2=step2,
            step3=step3,
            step5=step5,
            fuel_l=100.0,
            track_name="Spielberg",
        )
        # GT3 path must NOT push front_torsion_od_mm onto the BMW grid (13.9).
        self.assertEqual(step3.front_torsion_od_mm, 0.0,
                         "GT3 step3.front_torsion_od_mm must remain 0.0 (no torsion bar)")
        self.assertEqual(step2.front_heave_nmm, 0.0,
                         "GT3 step2.front_heave_nmm must remain 0.0 (no heave spring)")
        self.assertEqual(step2.rear_third_nmm, 0.0)

    def test_fix_slider_returns_empty_on_gt3(self) -> None:
        """_fix_slider early-returns [] on GT3 (no heave slider concept)."""
        car = get_car("bmw_m4_gt3")
        step1, step2, step3, step5 = _gt3_step_bundle()
        garage_model_stub = SimpleNamespace(max_slider_mm=45.0)
        gr = car.garage_ranges
        msgs = _fix_slider(garage_model_stub, car, step1, step2, step3, step5, 100.0, gr)
        self.assertEqual(msgs, [])

    def test_fix_torsion_bar_defl_returns_empty_on_gt3(self) -> None:
        """_fix_torsion_bar_defl early-returns [] on GT3 (no torsion bar)."""
        car = get_car("bmw_m4_gt3")
        step1, step2, step3, step5 = _gt3_step_bundle()
        garage_model_stub = SimpleNamespace(
            effective_torsion_bar_defl_limit_mm=lambda: 24.9,
        )
        gr = car.garage_ranges
        msgs = _fix_torsion_bar_defl(garage_model_stub, car, step1, step2, step3, step5, 100.0, gr)
        self.assertEqual(msgs, [])

    def test_fix_front_rh_returns_empty_on_gt3(self) -> None:
        """_fix_front_rh early-returns [] on GT3 (RH lever is spring perch / bump rubber)."""
        car = get_car("bmw_m4_gt3")
        step1, step2, step3, step5 = _gt3_step_bundle()
        garage_model_stub = SimpleNamespace(
            front_rh_floor_mm=30.0,
            front_coeff_pushrod=0.5,
        )
        gr = car.garage_ranges
        msgs = _fix_front_rh(garage_model_stub, car, step1, step2, step3, step5, 100.0, gr)
        self.assertEqual(msgs, [])

    def test_gtp_bmw_validator_still_works(self) -> None:
        """Regression: GTP BMW path is unchanged — fixers may still be invoked."""
        car = get_car("bmw")
        step1, step2, step3, step5 = _gtp_bmw_step_bundle()
        # No assertion on the warnings list — the existing
        # tests/test_garage_validator.py covers BMW behavior. We just
        # confirm validate_and_fix_garage_correlation still runs cleanly
        # AND keeps the heave/torsion-aware Phase 2/3 path active.
        warnings = validate_and_fix_garage_correlation(
            car=car,
            step1=step1,
            step2=step2,
            step3=step3,
            step5=step5,
            fuel_l=8.0,
            track_name="Sebring International Raceway",
        )
        self.assertIsInstance(warnings, list)
        # Phase 2/3 only runs on GTP — confirm step2 carries the
        # `garage_constraints_ok` attribute (set by the GTP path).
        self.assertTrue(hasattr(step2, "garage_constraints_ok"))


class GT3GarageRangesTests(unittest.TestCase):
    def test_bmw_m4_gt3_has_bump_rubber_gap_ranges(self) -> None:
        car = get_car("bmw_m4_gt3")
        gr = car.garage_ranges
        self.assertEqual(gr.bump_rubber_gap_front_mm, (0.0, 30.0),
                         "BMW M4 GT3 front bump rubber gap range")
        self.assertEqual(gr.bump_rubber_gap_rear_mm, (0.0, 60.0),
                         "BMW M4 GT3 rear bump rubber gap range")
        self.assertEqual(gr.splitter_height_mm, (0.0, 30.0),
                         "BMW M4 GT3 splitter height range")

    def test_aston_gt3_has_bump_rubber_gap_ranges(self) -> None:
        car = get_car("aston_martin_vantage_gt3")
        gr = car.garage_ranges
        self.assertEqual(gr.bump_rubber_gap_front_mm, (0.0, 30.0))
        self.assertEqual(gr.bump_rubber_gap_rear_mm, (0.0, 60.0))

    def test_porsche_gt3_has_bump_rubber_gap_ranges(self) -> None:
        car = get_car("porsche_992_gt3r")
        gr = car.garage_ranges
        # Porsche front uses a larger gap — RR layout sits high at the front.
        self.assertEqual(gr.bump_rubber_gap_front_mm, (0.0, 40.0))
        self.assertEqual(gr.bump_rubber_gap_rear_mm, (0.0, 60.0))

    def test_gtp_bmw_has_default_zero_bump_rubber_gap(self) -> None:
        """GTP cars must have the (0.0, 0.0) sentinel — no GT3 controls."""
        car = get_car("bmw")
        gr = car.garage_ranges
        self.assertEqual(gr.bump_rubber_gap_front_mm, (0.0, 0.0),
                         "GTP BMW must have default (0.0, 0.0) bump rubber gap")
        self.assertEqual(gr.bump_rubber_gap_rear_mm, (0.0, 0.0))
        self.assertEqual(gr.splitter_height_mm, (0.0, 0.0))


class GT3ReportTests(unittest.TestCase):
    """W4.3 report.py GT3 dispatch: heave/third literal text omitted; 4 corner
    spring rates rendered instead."""

    def _gt3_full_bundle(self):
        step1, step2, step3, step5 = _gt3_step_bundle()
        # Augment for full report rendering
        step1.df_balance_pct = 44.0
        step1.dynamic_front_rh_mm = 35.0
        step1.dynamic_rear_rh_mm = 80.0
        step1.vortex_burst_margin_mm = 5.0
        step1.ld_ratio = 4.0
        step3.front_natural_freq_hz = 0.0
        step3.rear_natural_freq_hz = 0.0
        step3.front_heave_corner_ratio = 0.0
        step3.rear_third_corner_ratio = 0.0
        step3.rear_torsion_od_mm = None
        step4 = SimpleNamespace(
            front_arb_size="D3-D3",
            front_arb_blade_start=4,
            rear_arb_size="D2-D2",
            rear_arb_blade_start=3,
            rarb_blade_slow_corner=3,
            rarb_blade_fast_corner=4,
            lltd_achieved=0.51,
            lltd_target=0.51,
            rarb_sensitivity_per_blade=0.01,
            lltd_at_rarb_min=0.50,
            lltd_at_rarb_max=0.52,
        )
        step5.peak_lat_g = 1.5
        step5.body_roll_at_peak_deg = 1.2
        step5.front_dynamic_camber_at_peak_deg = -3.5
        step5.camber_confidence = "MEDIUM"
        step5.expected_conditioning_laps_front = 3
        step5.expected_conditioning_laps_rear = 4
        # GT3 paired dampers
        front_d = SimpleNamespace(ls_comp=7, ls_rbd=5, hs_comp=3, hs_rbd=3, hs_slope=0)
        rear_d = SimpleNamespace(ls_comp=6, ls_rbd=7, hs_comp=4, hs_rbd=5, hs_slope=0)
        step6 = SimpleNamespace(lf=front_d, rf=front_d, lr=rear_d, rr=rear_d)
        supporting = SimpleNamespace(
            brake_bias_pct=52.0,
            tyre_cold_fl_kpa=180.0,
            tyre_cold_fr_kpa=180.0,
            tyre_cold_rl_kpa=180.0,
            tyre_cold_rr_kpa=180.0,
            diff_preload_nm=100.0,
            diff_ramp_coast=45,
            diff_ramp_drive=70,
            diff_clutch_plates=8,
            tc_gain=4,
            tc_slip=6,
        )
        return step1, step2, step3, step4, step5, step6, supporting

    def test_gt3_report_omits_heave_third_literals(self) -> None:
        from output.report import print_full_setup_report
        car = get_car("bmw_m4_gt3")
        step1, step2, step3, step4, step5, step6, supporting = self._gt3_full_bundle()
        out = print_full_setup_report(
            car_name="BMW M4 GT3 EVO",
            track_name="Spielberg",
            wing=8.0,
            target_balance=44.0,
            step1=step1, step2=step2, step3=step3,
            step4=step4, step5=step5, step6=step6,
            supporting=supporting,
            fuel_l=100.0,
            car=car,
            compact=False,
        )
        self.assertNotIn("Heave F:", out,
                         "GT3 report must NOT render 'Heave F:' (no heave spring)")
        self.assertNotIn("Third R:", out,
                         "GT3 report must NOT render 'Third R:' (no third spring)")
        # 4-corner spring display present
        self.assertIn("LF Spring", out,
                      "GT3 report must render 'LF Spring' line")
        self.assertIn("LR Spring", out,
                      "GT3 report must render 'LR Spring' line")

    def test_gtp_bmw_report_still_renders_heave_third(self) -> None:
        """Regression: GTP BMW report path must not be GT3-ified."""
        from output.report import print_full_setup_report
        car = get_car("bmw")
        step1 = SimpleNamespace(
            front_pushrod_offset_mm=-25.0,
            rear_pushrod_offset_mm=-23.0,
            static_front_rh_mm=30.0,
            static_rear_rh_mm=50.0,
            rake_static_mm=20.0,
            df_balance_pct=43.0,
            dynamic_front_rh_mm=20.0,
            dynamic_rear_rh_mm=45.0,
            vortex_burst_margin_mm=5.0,
            ld_ratio=4.0,
        )
        step2 = SimpleNamespace(
            front_heave_nmm=80.0,
            rear_third_nmm=440.0,
            perch_offset_front_mm=-7.0,
            perch_offset_rear_mm=43.0,
            front_excursion_at_rate_mm=10.0,
            travel_margin_front_mm=5.0,
            front_bottoming_margin_mm=3.0,
            slider_static_front_mm=20.0,
        )
        step3 = SimpleNamespace(
            front_torsion_od_mm=14.5,
            rear_spring_rate_nmm=150.0,
            rear_spring_perch_mm=30.0,
            rear_torsion_od_mm=None,
            front_natural_freq_hz=2.5,
            rear_natural_freq_hz=2.0,
            front_heave_corner_ratio=2.0,
            rear_third_corner_ratio=2.0,
        )
        step4 = SimpleNamespace(
            front_arb_size="D3-D3",
            front_arb_blade_start=3,
            rear_arb_size="D2-D2",
            rear_arb_blade_start=2,
            rarb_blade_slow_corner=2,
            rarb_blade_fast_corner=3,
            lltd_achieved=0.50,
            lltd_target=0.51,
            rarb_sensitivity_per_blade=0.01,
            lltd_at_rarb_min=0.49,
            lltd_at_rarb_max=0.52,
        )
        step5 = SimpleNamespace(
            front_camber_deg=-2.1,
            rear_camber_deg=-1.8,
            front_toe_mm=-0.4,
            rear_toe_mm=0.3,
            peak_lat_g=1.5,
            body_roll_at_peak_deg=1.2,
            front_dynamic_camber_at_peak_deg=-3.5,
            camber_confidence="MEDIUM",
            expected_conditioning_laps_front=3,
            expected_conditioning_laps_rear=4,
        )
        # GTP per-corner dampers
        corner = SimpleNamespace(ls_comp=6, ls_rbd=7, hs_comp=4, hs_rbd=5, hs_slope=11)
        step6 = SimpleNamespace(lf=corner, rf=corner, lr=corner, rr=corner)
        supporting = SimpleNamespace(
            brake_bias_pct=56.0,
            tyre_cold_fl_kpa=160.0,
            tyre_cold_fr_kpa=160.0,
            tyre_cold_rl_kpa=160.0,
            tyre_cold_rr_kpa=160.0,
            diff_preload_nm=80.0,
            diff_ramp_coast=45,
            diff_ramp_drive=70,
            diff_clutch_plates=4,
            tc_gain=2,
            tc_slip=2,
        )
        out = print_full_setup_report(
            car_name="BMW M Hybrid V8",
            track_name="Sebring International Raceway",
            wing=17.0,
            target_balance=43.0,
            step1=step1, step2=step2, step3=step3,
            step4=step4, step5=step5, step6=step6,
            supporting=supporting,
            fuel_l=8.0,
            car=car,
            compact=False,
        )
        self.assertIn("Heave F:", out,
                      "GTP BMW report MUST still render 'Heave F:'")
        self.assertIn("Third R:", out,
                      "GTP BMW report MUST still render 'Third R:'")


if __name__ == "__main__":
    unittest.main()
