import unittest
from types import SimpleNamespace
from pathlib import Path
import argparse
import contextlib
import io

from output.report import print_full_setup_report
from pipeline.produce import produce_result


def _damper_corner() -> SimpleNamespace:
    return SimpleNamespace(ls_comp=6, ls_rbd=7, hs_comp=5, hs_rbd=8, hs_slope=10)


class OutputReportSupportingTests(unittest.TestCase):
    def test_compact_report_surfaces_brake_hardware_and_diff_lock(self) -> None:
        report = print_full_setup_report(
            car_name="BMW M Hybrid V8",
            track_name="Sebring International",
            wing=17.0,
            target_balance=50.14,
            step1=SimpleNamespace(
                df_balance_pct=50.14,
                static_front_rh_mm=30.1,
                static_rear_rh_mm=49.5,
                dynamic_front_rh_mm=15.0,
                dynamic_rear_rh_mm=40.0,
                ld_ratio=3.8,
                rake_static_mm=19.4,
                front_pushrod_offset_mm=-26.5,
                rear_pushrod_offset_mm=-24.0,
                vortex_burst_margin_mm=2.5,
            ),
            step2=SimpleNamespace(
                front_heave_nmm=50.0,
                rear_third_nmm=530.0,
                perch_offset_front_mm=-11.0,
                perch_offset_rear_mm=42.5,
                slider_static_front_mm=43.0,
                travel_margin_front_mm=6.0,
                front_excursion_at_rate_mm=9.0,
                front_bottoming_margin_mm=4.0,
            ),
            step3=SimpleNamespace(
                front_torsion_od_mm=13.9,
                rear_spring_rate_nmm=160.0,
                rear_spring_perch_mm=30.0,
            ),
            step4=SimpleNamespace(
                front_arb_size="Soft",
                front_arb_blade_start=1,
                rear_arb_size="Medium",
                rear_arb_blade_start=3,
                rarb_blade_slow_corner=1,
                rarb_blade_fast_corner=5,
                lltd_achieved=0.55,
                lltd_target=0.54,
            ),
            step5=SimpleNamespace(
                front_camber_deg=-2.9,
                rear_camber_deg=-1.9,
                front_toe_mm=-0.4,
                rear_toe_mm=0.0,
                camber_confidence="calibrated",
            ),
            step6=SimpleNamespace(
                lf=_damper_corner(),
                rf=_damper_corner(),
                lr=_damper_corner(),
                rr=_damper_corner(),
            ),
            supporting=SimpleNamespace(
                tyre_cold_fl_kpa=152.0,
                tyre_cold_fr_kpa=152.0,
                tyre_cold_rl_kpa=152.0,
                tyre_cold_rr_kpa=152.0,
                brake_bias_pct=46.0,
                brake_bias_target=1.5,
                brake_bias_migration=-0.5,
                front_master_cyl_mm=19.1,
                rear_master_cyl_mm=20.6,
                pad_compound="Medium",
                diff_preload_nm=20.0,
                diff_ramp_coast=40,
                diff_ramp_drive=65,
                diff_clutch_plates=4,
                tc_gain=4,
                tc_slip=3,
                _diff_solution=SimpleNamespace(
                    lock_pct_coast=47.0,
                    lock_pct_drive=31.0,
                    preload_contribution_pct=5.0,
                    plate_contribution_pct=42.0,
                ),
            ),
            compact=True,
        )

        self.assertIn("Brake target/mig: +1.5 / -0.5", report)
        self.assertIn("Master cyl: 19.1/20.6 mm", report)
        self.assertIn("Diff lock coast/drive: 47.0% / 31.0%", report)
        self.assertIn("Diff preload / plate %   5.0% / 42.0%", report)

    def test_pipeline_report_renders_predicted_improvements_on_bmw_fixture(self) -> None:
        fixture = Path("/workspace/ibtfiles/bmw151.ibt")
        if not fixture.exists():
            self.skipTest("bmw151 fixture unavailable")
        args = argparse.Namespace(
            car="bmw",
            ibt=str(fixture),
            wing=17.0,
            lap=None,
            balance=50.14,
            tolerance=0.1,
            fuel=None,
            free=False,
            sto=None,
            json=None,
            report_only=False,
            no_learn=True,
            legacy_solver=False,
            min_lap_time=108.0,
            outlier_pct=0.115,
            stint_laps=30,
        )
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            result = produce_result(args)
        report = result["report"]

        self.assertIn("PREDICTED IMPROVEMENTS", report)
        self.assertIn("Front travel used", report)


if __name__ == "__main__":
    unittest.main()
