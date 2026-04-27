"""Tests for the SuspensionArchitecture enum scaffolding.

These tests pin the additive Phase 0 contract for GT3 support:
  - The enum exists with the three intended values.
  - has_heave_third / has_front_torsion_bar properties classify correctly.
  - Existing GTP cars default to GTP_HEAVE_THIRD_TORSION_FRONT.
  - Porsche 963 declares GTP_HEAVE_THIRD_ROLL_FRONT explicitly.
  - __post_init__ invariants reject misconfigurations.
  - BMW M4 GT3 scaffold exists and respects the GT3 invariants.
"""
from __future__ import annotations

import pytest

from car_model.cars import (
    BMW_M_HYBRID_V8,
    BMW_M4_GT3,
    PORSCHE_963,
    CarModel,
    CornerSpringModel,
    SuspensionArchitecture,
)


class TestSuspensionArchitectureEnum:
    def test_three_values_exist(self):
        assert SuspensionArchitecture.GTP_HEAVE_THIRD_TORSION_FRONT.value == "gtp_heave_third_torsion_front"
        assert SuspensionArchitecture.GTP_HEAVE_THIRD_ROLL_FRONT.value == "gtp_heave_third_roll_front"
        assert SuspensionArchitecture.GT3_COIL_4WHEEL.value == "gt3_coil_4wheel"

    def test_has_heave_third_true_for_gtp(self):
        assert SuspensionArchitecture.GTP_HEAVE_THIRD_TORSION_FRONT.has_heave_third is True
        assert SuspensionArchitecture.GTP_HEAVE_THIRD_ROLL_FRONT.has_heave_third is True

    def test_has_heave_third_false_for_gt3(self):
        assert SuspensionArchitecture.GT3_COIL_4WHEEL.has_heave_third is False

    def test_has_front_torsion_bar_only_torsion_variant(self):
        assert SuspensionArchitecture.GTP_HEAVE_THIRD_TORSION_FRONT.has_front_torsion_bar is True
        assert SuspensionArchitecture.GTP_HEAVE_THIRD_ROLL_FRONT.has_front_torsion_bar is False
        assert SuspensionArchitecture.GT3_COIL_4WHEEL.has_front_torsion_bar is False


class TestExistingCarDefaults:
    def test_bmw_defaults_to_torsion_front(self):
        assert BMW_M_HYBRID_V8.suspension_arch is SuspensionArchitecture.GTP_HEAVE_THIRD_TORSION_FRONT

    def test_porsche_declares_roll_front(self):
        assert PORSCHE_963.suspension_arch is SuspensionArchitecture.GTP_HEAVE_THIRD_ROLL_FRONT


class TestInvariants:
    def test_gt3_arch_rejects_front_torsion_c(self):
        with pytest.raises(ValueError, match="GT3_COIL_4WHEEL"):
            CarModel(
                name="Bad GT3",
                canonical_name="bad_gt3",
                mass_car_kg=1300.0,
                suspension_arch=SuspensionArchitecture.GT3_COIL_4WHEEL,
                heave_spring=None,
                corner_spring=CornerSpringModel(
                    front_torsion_c=0.0008036,
                    front_torsion_od_ref_mm=13.9,
                ),
            )

    def test_gt3_arch_accepts_zero_front_torsion_c(self):
        car = CarModel(
            name="OK GT3 stub",
            canonical_name="ok_gt3_stub",
            mass_car_kg=1300.0,
            suspension_arch=SuspensionArchitecture.GT3_COIL_4WHEEL,
            heave_spring=None,
            corner_spring=CornerSpringModel(
                front_torsion_c=0.0,
                front_torsion_od_ref_mm=0.0,
                front_torsion_od_options=[],
            ),
        )
        assert car.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL

    def test_gt3_arch_rejects_non_null_heave_spring(self):
        """GT3 cars MUST set heave_spring=None — forgetting to is silently
        wrong (factory default builds a non-null heave model). Catch at
        construction."""
        with pytest.raises(ValueError, match="heave_spring=None"):
            CarModel(
                name="Bad GT3 default heave",
                canonical_name="bad_gt3_default_heave",
                mass_car_kg=1300.0,
                suspension_arch=SuspensionArchitecture.GT3_COIL_4WHEEL,
                # heave_spring not set → factory default builds a non-null one
                corner_spring=CornerSpringModel(
                    front_torsion_c=0.0,
                    front_torsion_od_ref_mm=0.0,
                    front_torsion_od_options=[],
                ),
            )

    def test_gtp_arch_rejects_null_heave_spring(self):
        """Inverse invariant: GTP cars must NOT set heave_spring=None."""
        with pytest.raises(ValueError, match="requires non-null heave_spring"):
            CarModel(
                name="Bad GTP no heave",
                canonical_name="bad_gtp_no_heave",
                mass_car_kg=1030.0,
                suspension_arch=SuspensionArchitecture.GTP_HEAVE_THIRD_TORSION_FRONT,
                heave_spring=None,
            )


class TestBMWM4GT3:
    def test_canonical_name_and_arch(self):
        assert BMW_M4_GT3.canonical_name == "bmw_m4_gt3"
        assert BMW_M4_GT3.suspension_arch is SuspensionArchitecture.GT3_COIL_4WHEEL

    def test_heave_spring_is_none(self):
        assert BMW_M4_GT3.heave_spring is None

    def test_front_torsion_disabled(self):
        assert BMW_M4_GT3.corner_spring.front_torsion_c == 0.0
        assert BMW_M4_GT3.corner_spring.front_torsion_od_options == []

    def test_no_roll_dampers(self):
        d = BMW_M4_GT3.damper
        assert d.has_roll_dampers is False
        assert d.has_front_roll_damper is False
        assert d.has_rear_roll_damper is False

    def test_bop_version_set(self):
        assert BMW_M4_GT3.bop_version == "2026s2_p3"

    def test_arb_blade_counts_match_manual(self):
        """Manual: 11 front configs (D1-D1..D6-D6), 7 rear (D1-D1..D4-D4)."""
        assert len(BMW_M4_GT3.arb.front_size_labels) == 11
        assert len(BMW_M4_GT3.arb.rear_size_labels) == 7

    def test_rear_spring_range_from_manual(self):
        """Manual V3: rear spring 130-250 N/mm, 10 N/mm step."""
        lo, hi = BMW_M4_GT3.corner_spring.rear_spring_range_nmm
        assert lo == 130.0
        assert hi == 250.0

    def test_wing_angles_match_aero_map(self):
        """Wing angles -2..+6 (9 angles) parsed from data/aeromaps_parsed/bmw_m4_gt3_aero.npz."""
        assert BMW_M4_GT3.wing_angles == [-2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]

    def test_registry_lookup(self):
        from car_model.cars import get_car
        car = get_car("bmw_m4_gt3", apply_calibration=False)
        assert car.canonical_name == "bmw_m4_gt3"
        assert car.heave_spring is None


class TestNullHeaveSolution:
    def test_null_factory_returns_present_false(self):
        from solver.heave_solver import HeaveSolution
        h = HeaveSolution.null()
        assert h.present is False
        assert h.front_heave_nmm == 0.0
        assert h.rear_third_nmm == 0.0
        assert h.front_binding_constraint == "not_applicable"
        assert h.rear_binding_constraint == "not_applicable"

    def test_real_solution_defaults_present_true(self):
        """Sanity check: existing call sites that don't set `present` get the
        default True, preserving the implicit contract for GTP cars."""
        from solver.heave_solver import HeaveSolution
        h = HeaveSolution(
            front_heave_nmm=180.0,
            rear_third_nmm=400.0,
            front_dynamic_rh_mm=30.0,
            front_shock_vel_p99_mps=0.5,
            front_excursion_at_rate_mm=4.0,
            front_bottoming_margin_mm=2.0,
            front_sigma_at_rate_mm=1.0,
            front_binding_constraint="bottoming",
            rear_dynamic_rh_mm=50.0,
            rear_shock_vel_p99_mps=0.6,
            rear_excursion_at_rate_mm=5.0,
            rear_bottoming_margin_mm=3.0,
            rear_sigma_at_rate_mm=1.2,
            rear_binding_constraint="bottoming",
            perch_offset_front_mm=-13.0,
            perch_offset_rear_mm=42.0,
        )
        assert h.present is True


class TestCalibrationGateNotApplicable:
    def test_subsystem_status_includes_not_applicable(self):
        from car_model.calibration_gate import SubsystemCalibration
        sub = SubsystemCalibration(name="heave_third", status="not_applicable")
        assert sub.status == "not_applicable"

    def test_step_report_not_applicable_zero_confidence(self):
        from car_model.calibration_gate import StepCalibrationReport
        r = StepCalibrationReport(
            step_number=2,
            step_name="Heave/Third Springs",
            not_applicable=True,
        )
        assert r.confidence_weight == 0.0
        assert r.blocked is False  # N/A is distinct from blocked
        assert r.not_applicable is True
