"""W4.1: GT3 BMW M4 GT3 setup writer tests.

Verifies that `output.setup_writer.write_sto()` produces a valid `.sto` file
for the BMW M4 GT3 EVO (GT3 architecture), and that the GTP BMW M Hybrid V8
path is not regressed.

Scope is BMW M4 GT3 only. Aston Vantage GT3 and Porsche 992 GT3 R are W4.2.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from car_model.cars import get_car
from output.setup_writer import write_sto


# ─── Test fixtures ───────────────────────────────────────────────────────────

def _gt3_corner_damper(ls_comp=7, ls_rbd=6, hs_comp=4, hs_rbd=8, hs_slope=0):
    """GT3 dampers carry hs_slope=0 placeholder (no slope channel exposed)."""
    return SimpleNamespace(
        ls_comp=ls_comp,
        ls_rbd=ls_rbd,
        hs_comp=hs_comp,
        hs_rbd=hs_rbd,
        hs_slope=hs_slope,
    )


def _gt3_step_bundle():
    """GT3 BMW M4 GT3 step1..step6 fixtures.

    step2 is a HeaveSolution.null()-equivalent (all zeros, present=False).
    step3 carries front_coil_rate_nmm + rear_spring_rate_nmm (W2.3).
    step6 carries paired LF==RF and LR==RR per-axle dampers (W3.2).
    """
    step1 = SimpleNamespace(
        dynamic_front_rh_mm=35.0,
        dynamic_rear_rh_mm=80.0,
        df_balance_pct=44.0,
        ld_ratio=4.0,
        static_front_rh_mm=72.6,
        static_rear_rh_mm=82.6,
        front_pushrod_offset_mm=0.0,  # GT3: no pushrod
        rear_pushrod_offset_mm=0.0,
    )
    # Mimic HeaveSolution.null() — present=False, all zeros
    step2 = SimpleNamespace(
        present=False,
        front_heave_nmm=0.0,
        rear_third_nmm=0.0,
        perch_offset_front_mm=0.0,
        perch_offset_rear_mm=0.0,
        front_excursion_at_rate_mm=0.0,
        defl_max_front_mm=0.0,
    )
    step3 = SimpleNamespace(
        front_torsion_od_mm=0.0,            # GT3: no torsion bar
        front_wheel_rate_nmm=220.0,
        # Rounded to the GarageRanges default 5 N/mm step (W4.1 leaves
        # _clamp_step3 with GTP defaults; using 180 avoids the spurious
        # 179 -> 180 snap warning from garage_validator).
        rear_spring_rate_nmm=180.0,
        rear_spring_perch_mm=0.0,
        rear_motion_ratio=1.0,
        front_coil_rate_nmm=250.0,          # IBT measured 252 -> snap to 250 step
        front_coil_perch_mm=0.0,
        front_torsion_bar_turns=0.0,
        rear_torsion_bar_turns=0.0,
    )
    step4 = SimpleNamespace(
        front_arb_size="D3-D3",
        front_arb_blade_start=4,            # ArbBlades int (BMW M4 GT3 uses single ArbBlades)
        rear_arb_size="D2-D2",
        rear_arb_blade_start=3,
        lltd_achieved=0.51,
    )
    step5 = SimpleNamespace(
        front_camber_deg=-4.0,
        rear_camber_deg=-2.8,
        front_toe_mm=-0.4,
        rear_toe_mm=1.5,
    )
    # Per-axle paired (LF==RF, LR==RR after W3.2 collapse)
    front_damper = _gt3_corner_damper(ls_comp=7, ls_rbd=5, hs_comp=3, hs_rbd=3, hs_slope=0)
    rear_damper = _gt3_corner_damper(ls_comp=6, ls_rbd=7, hs_comp=4, hs_rbd=5, hs_slope=0)
    step6 = SimpleNamespace(
        lf=front_damper, rf=front_damper,
        lr=rear_damper, rr=rear_damper,
        c_hs_front=1200.0, c_hs_rear=1400.0,
    )
    supporting = SimpleNamespace(
        brake_bias_pct=52.0,
        diff_preload_nm=100.0,
        diff_clutch_plates=8,                # FrictionFaces on GT3
        diff_ramp_angles=None,
        tc_gain=4,
        tc_slip=6,                           # Used as ABS index
        fuel_l=100.0,
        fuel_low_warning_l=8.0,
        gear_stack="FIA",
        roof_light_color="Orange",
        tyre_cold_fl_kpa=180.0,
        pad_compound="Medium",
        front_master_cyl_mm=22.2,
        rear_master_cyl_mm=22.2,
    )
    return step1, step2, step3, step4, step5, step6, supporting


def _gtp_bmw_step_bundle():
    """GTP BMW M Hybrid V8 step1..step6 fixtures (regression baseline)."""

    def _corner(ls_comp=7, ls_rbd=6, hs_comp=4, hs_rbd=8, hs_slope=11):
        return SimpleNamespace(
            ls_comp=ls_comp, ls_rbd=ls_rbd,
            hs_comp=hs_comp, hs_rbd=hs_rbd, hs_slope=hs_slope,
        )

    step1 = SimpleNamespace(
        dynamic_front_rh_mm=20.0, dynamic_rear_rh_mm=42.0,
        df_balance_pct=45.6, ld_ratio=4.2,
        static_front_rh_mm=31.0, static_rear_rh_mm=48.5,
        front_pushrod_offset_mm=-26.0, rear_pushrod_offset_mm=-24.0,
    )
    step2 = SimpleNamespace(
        present=True,
        front_heave_nmm=55.0, rear_third_nmm=500.0,
        perch_offset_front_mm=-10.0, perch_offset_rear_mm=43.0,
        front_excursion_at_rate_mm=12.0, defl_max_front_mm=90.0,
    )
    step3 = SimpleNamespace(
        front_torsion_od_mm=13.9, front_wheel_rate_nmm=180.0,
        rear_spring_rate_nmm=170.0, rear_spring_perch_mm=31.0,
        front_coil_rate_nmm=0.0,
    )
    step4 = SimpleNamespace(
        front_arb_size="Medium", front_arb_blade_start=2,
        rear_arb_size="Stiff", rear_arb_blade_start=4,
        lltd_achieved=0.515,
    )
    step5 = SimpleNamespace(
        front_camber_deg=-2.9, rear_camber_deg=-1.9,
        front_toe_mm=-0.4, rear_toe_mm=0.1,
    )
    step6 = SimpleNamespace(
        lf=_corner(7, 6, 4, 9, 11), rf=_corner(7, 6, 4, 9, 11),
        lr=_corner(6, 7, 5, 11, 12), rr=_corner(6, 7, 5, 11, 12),
        c_hs_front=1200.0, c_hs_rear=1400.0,
    )
    return step1, step2, step3, step4, step5, step6


# ─── Tests ───────────────────────────────────────────────────────────────────


class GT3BMWSetupWriterTests(unittest.TestCase):
    """W4.1 deliverables: write_sto() does not raise for GT3 BMW M4 GT3,
    produces valid XML with the GT3-correct CarSetup_* IDs, and avoids any
    GTP-only fields (HeaveSpring, TorsionBar, per-corner dampers,
    Systems_Fuel_FuelLevel)."""

    def _write_gt3(self, *, include_computed: bool = False) -> tuple[Path, str]:
        step1, step2, step3, step4, step5, step6, supporting = _gt3_step_bundle()
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "bmw_m4_gt3_spielberg.sto"
        write_sto(
            car_name="BMW M4 GT3 EVO",
            track_name="Spielberg",
            wing=-2.0,
            fuel_l=supporting.fuel_l,
            step1=step1, step2=step2, step3=step3,
            step4=step4, step5=step5, step6=step6,
            output_path=out,
            car_canonical="bmw_m4_gt3",
            tyre_pressure_kpa=supporting.tyre_cold_fl_kpa,
            brake_bias_pct=supporting.brake_bias_pct,
            pad_compound=supporting.pad_compound,
            front_master_cyl_mm=supporting.front_master_cyl_mm,
            rear_master_cyl_mm=supporting.rear_master_cyl_mm,
            diff_preload_nm=supporting.diff_preload_nm,
            tc_gain=supporting.tc_gain,
            tc_slip=supporting.tc_slip,
            fuel_low_warning_l=supporting.fuel_low_warning_l,
            gear_stack=supporting.gear_stack,
            include_computed=include_computed,
        )
        text = out.read_text(encoding="utf-8")
        return out, text

    def test_write_sto_does_not_raise_for_gt3_bmw(self) -> None:
        out, text = self._write_gt3()
        self.assertTrue(out.exists(), "sto file was not written")
        self.assertGreater(len(text), 0, "sto file is empty")

    def test_written_sto_is_valid_xml(self) -> None:
        out, text = self._write_gt3()
        # Strip the leading <?xml ...?> and re-parse to confirm
        # well-formedness.  The writer prepends an explicit declaration.
        ET.fromstring(text.split("?>", 1)[1] if text.startswith("<?xml") else text)

    def test_gt3_spring_rates_paired_lf_rf_lr_rr(self) -> None:
        _, text = self._write_gt3()
        self.assertIn(
            'CarSetup_Chassis_LeftFront_SpringRate" Value="250"', text,
            "LF spring rate (250 N/mm) missing"
        )
        self.assertIn(
            'CarSetup_Chassis_RightFront_SpringRate" Value="250"', text,
            "RF spring rate must equal LF (paired front coil)"
        )
        self.assertIn(
            'CarSetup_Chassis_LeftRear_SpringRate" Value="180"', text,
            "LR spring rate (180 N/mm) missing"
        )
        self.assertIn(
            'CarSetup_Chassis_RightRear_SpringRate" Value="180"', text,
            "RR spring rate must equal LR (paired rear coil)"
        )

    def test_gt3_arb_blades_in_front_brakes_section(self) -> None:
        _, text = self._write_gt3()
        # BMW M4 GT3 ArbBlades is in Chassis.FrontBrakes (not Front), and
        # rear is in Chassis.Rear.
        self.assertIn("CarSetup_Chassis_FrontBrakes_ArbBlades", text)
        self.assertIn("CarSetup_Chassis_Rear_ArbBlades", text)

    def test_gt3_fuel_in_chassis_rear_not_brakes_drive_unit(self) -> None:
        _, text = self._write_gt3()
        self.assertIn("CarSetup_Chassis_Rear_FuelLevel", text)
        # BMW M Hybrid V8 GTP path must NOT bleed through.
        self.assertNotIn("CarSetup_BrakesDriveUnit_Fuel_FuelLevel", text)
        self.assertNotIn("CarSetup_Systems_Fuel_FuelLevel", text)

    def test_gt3_in_car_adjustments_path(self) -> None:
        _, text = self._write_gt3()
        self.assertIn("CarSetup_Chassis_InCarAdjustments_BrakePressureBias", text)
        self.assertIn("CarSetup_Chassis_InCarAdjustments_TcSetting", text)

    def test_gt3_tc_abs_emit_indexed_strings(self) -> None:
        _, text = self._write_gt3()
        # BMW M4 GT3 format: "n (TC)" / "n (ABS)"
        self.assertIn('Value="4 (TC)"', text, "TcSetting must be '4 (TC)'")
        self.assertIn('Value="6 (ABS)"', text, "AbsSetting must be '6 (ABS)' (sourced from tc_slip)")

    def test_gt3_per_axle_dampers_8_channels(self) -> None:
        _, text = self._write_gt3()
        for chan in (
            "CarSetup_Dampers_FrontDampers_LowSpeedCompressionDamping",
            "CarSetup_Dampers_FrontDampers_HighSpeedCompressionDamping",
            "CarSetup_Dampers_FrontDampers_LowSpeedReboundDamping",
            "CarSetup_Dampers_FrontDampers_HighSpeedReboundDamping",
            "CarSetup_Dampers_RearDampers_LowSpeedCompressionDamping",
            "CarSetup_Dampers_RearDampers_HighSpeedCompressionDamping",
            "CarSetup_Dampers_RearDampers_LowSpeedReboundDamping",
            "CarSetup_Dampers_RearDampers_HighSpeedReboundDamping",
        ):
            self.assertIn(chan, text, f"GT3 damper channel missing: {chan}")

    def test_gt3_emits_no_gtp_only_fields(self) -> None:
        _, text = self._write_gt3()
        # No heave/third spring writes
        self.assertNotIn("HeaveSpring", text)
        self.assertNotIn("ThirdSpring", text)
        self.assertNotIn("HeavePerchOffset", text)
        self.assertNotIn("ThirdPerchOffset", text)
        # No torsion bar writes
        self.assertNotIn("TorsionBarOD", text)
        self.assertNotIn("TorsionBarTurns", text)
        # No per-corner damper IDs (those are GTP)
        self.assertNotIn("CarSetup_Chassis_LeftFront_LsCompDamping", text)
        self.assertNotIn("CarSetup_Chassis_RightRear_HsRbdDamping", text)
        # No GTP pushrod IDs
        self.assertNotIn("CarSetup_Chassis_Front_PushrodLengthOffset", text)
        self.assertNotIn("CarSetup_Chassis_Rear_PushrodLengthOffset", text)

    def test_gt3_bump_rubber_gap_4_corners(self) -> None:
        _, text = self._write_gt3()
        for corner in ("LeftFront", "RightFront", "LeftRear", "RightRear"):
            self.assertIn(f"CarSetup_Chassis_{corner}_BumpRubberGap", text)
        # CenterFrontSplitterHeight is also part of the GT3 garage.
        self.assertIn(
            "CarSetup_Chassis_FrontBrakes_CenterFrontSplitterHeight", text
        )

    def test_gt3_rear_toe_per_wheel(self) -> None:
        # BMW M4 GT3 rear toe is per-wheel (LeftRear.ToeIn / RightRear.ToeIn),
        # NOT paired (Porsche 992 is the paired one — W4.2). Front toe is paired
        # (Chassis.FrontBrakes.TotalToeIn).
        _, text = self._write_gt3()
        self.assertIn("CarSetup_Chassis_LeftRear_ToeIn", text)
        self.assertIn("CarSetup_Chassis_RightRear_ToeIn", text)
        self.assertIn("CarSetup_Chassis_FrontBrakes_TotalToeIn", text)


class GTPBMWRegressionTests(unittest.TestCase):
    """Confirm the existing GTP BMW M Hybrid V8 path is not regressed
    by the GT3 changes."""

    def _write_gtp(self) -> str:
        step1, step2, step3, step4, step5, step6 = _gtp_bmw_step_bundle()
        with TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "bmw_gtp.sto"
            write_sto(
                car_name="BMW M Hybrid V8",
                track_name="Sebring",
                wing=17.0,
                fuel_l=88.0,
                step1=step1, step2=step2, step3=step3,
                step4=step4, step5=step5, step6=step6,
                output_path=out,
                car_canonical="bmw",
                tyre_pressure_kpa=152.0,
                brake_bias_pct=45.8,
                diff_coast_drive_ramp="50/75",
                diff_clutch_plates=6,
                diff_preload_nm=25.0,
                tc_gain=5,
                tc_slip=4,
            )
            return out.read_text(encoding="utf-8")

    def test_gtp_bmw_still_emits_heave_spring(self) -> None:
        text = self._write_gtp()
        self.assertIn("CarSetup_Chassis_Front_HeaveSpring", text)
        self.assertIn("CarSetup_Chassis_Rear_ThirdSpring", text)

    def test_gtp_bmw_still_emits_torsion_bar(self) -> None:
        text = self._write_gtp()
        self.assertIn("CarSetup_Chassis_LeftFront_TorsionBarOD", text)
        self.assertIn("CarSetup_Chassis_LeftFront_TorsionBarTurns", text)

    def test_gtp_bmw_still_emits_per_corner_dampers(self) -> None:
        text = self._write_gtp()
        for corner in ("LeftFront", "RightFront", "LeftRear", "RightRear"):
            self.assertIn(f"CarSetup_Chassis_{corner}_LsCompDamping", text)

    def test_gtp_bmw_still_emits_systems_fuel_path(self) -> None:
        # GTP BMW puts fuel under BrakesDriveUnit (not Chassis.Rear like GT3).
        text = self._write_gtp()
        self.assertIn("CarSetup_BrakesDriveUnit_Fuel_FuelLevel", text)
        self.assertNotIn("CarSetup_Chassis_Rear_FuelLevel", text)


class GT3CarRegistryTests(unittest.TestCase):
    """Sanity check: BMW M4 GT3 is registered and its suspension architecture
    flag flips the writer's GT3 path."""

    def test_bmw_m4_gt3_resolves(self) -> None:
        car = get_car("bmw_m4_gt3")
        self.assertEqual(car.canonical_name, "bmw_m4_gt3")
        self.assertFalse(car.suspension_arch.has_heave_third)
        self.assertIsNone(car.heave_spring)


if __name__ == "__main__":
    unittest.main()
