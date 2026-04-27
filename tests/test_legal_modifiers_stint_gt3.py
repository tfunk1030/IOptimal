"""GT3 W3.1 — heave_spring=None guards in legal_space, modifiers, stint_model.

Covers findings LS1, LS2, LS3, LS4, LS5 (legal_space.py), MD2, MD3, MD4
(modifiers.py), and ST5, ST6 (stint_model.py) from
docs/audits/gt3_phase2/solver-damper-legality.md.

The pattern: for GT3 cars (`car.suspension_arch.has_heave_third == False`),
all heave/third/torsion code paths must short-circuit. GTP regressions ensure
the existing path is untouched.
"""

from __future__ import annotations

import unittest

from analyzer.diagnose import Diagnosis, Problem
from analyzer.driver_style import DriverProfile
from analyzer.extract import MeasuredState
from car_model.cars import (
    ASTON_MARTIN_VANTAGE_GT3,
    BMW_M4_GT3,
    BMW_M_HYBRID_V8,
    PORSCHE_992_GT3R,
)
from solver.legal_space import (
    LegalSpace,
    _car_spring_refs,
    _tier_a_keys_for,
    compute_perch_offsets,
)
from solver.modifiers import compute_modifiers
from solver.stint_model import (
    FuelState,
    StintCondition,
    analyze_stint,
    find_compromise_parameters,
)


class LegalSpaceGT3Tests(unittest.TestCase):
    """LS1, LS2, LS3, LS4, LS5 — legal_space short-circuits for GT3."""

    def test_car_spring_refs_does_not_raise_on_gt3(self) -> None:
        # LS1: BMW-derived `_car_spring_refs` would TypeError on
        # `float(None)` for GT3. Now returns sentinel zeros for heave/third.
        front_heave_ref, rear_third_ref, rear_spring_ref = _car_spring_refs(
            BMW_M4_GT3
        )
        self.assertEqual(front_heave_ref, 0.0)
        self.assertEqual(rear_third_ref, 0.0)
        # Rear corner spring ref still valid — GT3 has rear coil springs.
        self.assertGreater(rear_spring_ref, 0.0)

    def test_car_spring_refs_gtp_regression(self) -> None:
        # GTP path unchanged.
        front, third, _ = _car_spring_refs(BMW_M_HYBRID_V8)
        self.assertGreater(front, 0.0)
        self.assertGreater(third, 0.0)

    def test_compute_perch_offsets_returns_empty_dict_on_gt3(self) -> None:
        # LS2: GT3 has no perch offsets. compute_perch_offsets() must short-
        # circuit before any `float(car.front_heave_spring_nmm)` is called.
        result = compute_perch_offsets({}, BMW_M4_GT3)
        self.assertEqual(result, {})
        # Ignores any keys that happen to be in params — they're not applicable.
        result2 = compute_perch_offsets(
            {"front_heave_spring_nmm": 100.0}, BMW_M4_GT3
        )
        self.assertEqual(result2, {})

    def test_compute_perch_offsets_gtp_regression(self) -> None:
        # GTP: returns non-empty perch dict.
        result = compute_perch_offsets(
            {
                "front_heave_spring_nmm": 60.0,
                "rear_third_spring_nmm": 450.0,
                "rear_spring_rate_nmm": 160.0,
            },
            BMW_M_HYBRID_V8,
        )
        self.assertIn("front_heave_perch_mm", result)
        self.assertIn("rear_third_perch_mm", result)

    def test_tier_a_keys_drops_heave_torsion_for_gt3(self) -> None:
        # LS3: TIER_A_KEYS includes heave/third/torsion unconditionally.
        # _tier_a_keys_for(car) filters them out for GT3.
        gt3_keys = _tier_a_keys_for(BMW_M4_GT3)
        for excluded in (
            "front_heave_spring_nmm",
            "rear_third_spring_nmm",
            "front_torsion_od_mm",
        ):
            self.assertNotIn(excluded, gt3_keys, f"{excluded} leaked into GT3 key set")
        # Other Tier A keys still present (dampers, ARB, geometry, supporting).
        self.assertIn("front_camber_deg", gt3_keys)
        self.assertIn("front_arb_blade", gt3_keys)
        self.assertIn("front_ls_comp", gt3_keys)
        self.assertIn("brake_bias_pct", gt3_keys)

    def test_tier_a_keys_gtp_regression(self) -> None:
        # GTP keys include heave/third/torsion as before.
        gtp_keys = _tier_a_keys_for(BMW_M_HYBRID_V8)
        self.assertIn("front_heave_spring_nmm", gtp_keys)
        self.assertIn("rear_third_spring_nmm", gtp_keys)
        self.assertIn("front_torsion_od_mm", gtp_keys)

    def test_legal_space_from_car_gt3_excludes_heave_dimensions(self) -> None:
        # LS3, LS5: LegalSpace.from_car() must not build dimensions for keys
        # that GT3 cars don't have.
        space = LegalSpace.from_car(BMW_M4_GT3)
        dim_names = {d.name for d in space.dimensions}
        self.assertNotIn("front_heave_spring_nmm", dim_names)
        self.assertNotIn("rear_third_spring_nmm", dim_names)
        self.assertNotIn("front_torsion_od_mm", dim_names)
        # Damper / ARB / geometry / supporting dims still present.
        self.assertIn("front_ls_comp", dim_names)
        self.assertIn("front_arb_blade", dim_names)
        self.assertIn("front_camber_deg", dim_names)

    def test_legal_space_from_car_gt3_no_crash_on_aston_porsche(self) -> None:
        # Other GT3 cars must also build without crashing.
        for car in (ASTON_MARTIN_VANTAGE_GT3, PORSCHE_992_GT3R):
            space = LegalSpace.from_car(car)
            dim_names = {d.name for d in space.dimensions}
            self.assertNotIn("front_heave_spring_nmm", dim_names)

    def test_legal_space_from_car_gtp_regression(self) -> None:
        # GTP space STILL includes heave/third/torsion dims.
        space = LegalSpace.from_car(BMW_M_HYBRID_V8)
        dim_names = {d.name for d in space.dimensions}
        self.assertIn("front_heave_spring_nmm", dim_names)
        self.assertIn("rear_third_spring_nmm", dim_names)
        self.assertIn("front_torsion_od_mm", dim_names)


class ModifiersGT3Tests(unittest.TestCase):
    """MD2, MD3, MD4 — modifiers.compute_modifiers short-circuits for GT3."""

    def _bottoming_diagnosis(self) -> Diagnosis:
        return Diagnosis(
            problems=[
                Problem(
                    category="safety",
                    severity="significant",
                    symptom="Front platform bottoming under braking",
                    cause="heave too soft",
                    speed_context="braking",
                    measured=10.0,
                    threshold=5.0,
                    units="events",
                    priority=0,
                ),
                Problem(
                    category="safety",
                    severity="significant",
                    symptom="Front heave travel exhausted under braking",
                    cause="travel exhausted",
                    speed_context="braking",
                    measured=92.0,
                    threshold=85.0,
                    units="%",
                    priority=0,
                ),
            ]
        )

    def test_compute_modifiers_does_not_crash_on_gt3(self) -> None:
        # MD2, MD3: `_heave_min` and `_perch_baseline` would AttributeError
        # because `car.heave_spring is None`.
        diag = self._bottoming_diagnosis()
        driver = DriverProfile()
        measured = MeasuredState(
            front_heave_travel_used_pct=92.0,
            pitch_range_deg=2.0,
            front_heave_vel_hs_pct=40.0,
            front_corner_defl_p99_mm=35.0,
        )
        mods = compute_modifiers(diag, driver, measured, car=BMW_M4_GT3)
        # MD4: heave-floor / perch-target writes must be skipped for GT3.
        self.assertEqual(mods.front_heave_min_floor_nmm, 0.0)
        self.assertEqual(mods.rear_third_min_floor_nmm, 0.0)
        self.assertIsNone(mods.front_heave_perch_target_mm)

    def test_compute_modifiers_gtp_writes_heave_floor(self) -> None:
        # Regression: GTP path STILL produces a non-zero heave floor for the
        # same diagnosis.
        diag = self._bottoming_diagnosis()
        driver = DriverProfile()
        measured = MeasuredState(
            front_heave_travel_used_pct=92.0,
            pitch_range_deg=2.0,
            front_heave_vel_hs_pct=40.0,
            front_corner_defl_p99_mm=35.0,
        )
        mods = compute_modifiers(diag, driver, measured, car=BMW_M_HYBRID_V8)
        self.assertGreater(mods.front_heave_min_floor_nmm, 0.0)
        self.assertIsNotNone(mods.front_heave_perch_target_mm)

    def test_compute_modifiers_other_gt3_cars(self) -> None:
        # All three currently-onboarded GT3 cars must run without crashing.
        diag = self._bottoming_diagnosis()
        driver = DriverProfile()
        measured = MeasuredState(front_heave_travel_used_pct=92.0)
        for car in (ASTON_MARTIN_VANTAGE_GT3, PORSCHE_992_GT3R):
            mods = compute_modifiers(diag, driver, measured, car=car)
            self.assertEqual(mods.front_heave_min_floor_nmm, 0.0)


class StintModelGT3Tests(unittest.TestCase):
    """ST5, ST6 — stint_model.analyze_stint short-circuits for GT3."""

    def test_analyze_stint_does_not_crash_on_gt3(self) -> None:
        # ST6: `base_heave_nmm = float(car.front_heave_spring_nmm)` would
        # TypeError on None. Must accept None and propagate sensibly.
        result = analyze_stint(BMW_M4_GT3, stint_laps=20)
        # Stint result is well-formed.
        self.assertIsNotNone(result)
        # Heave recommendation is a no-op (full_fuel_nmm == 0.0 default).
        self.assertEqual(result.heave_recommendation.full_fuel_nmm, 0.0)
        # ST5: compromise dict must NOT contain heave/third keys for GT3.
        self.assertNotIn("front_heave_nmm", result.compromise_parameters)
        self.assertNotIn("rear_third_nmm", result.compromise_parameters)

    def test_analyze_stint_gtp_regression(self) -> None:
        # GTP run still produces non-None heave values and compromise params.
        result = analyze_stint(BMW_M_HYBRID_V8, stint_laps=20)
        self.assertGreater(result.heave_recommendation.full_fuel_nmm, 0.0)
        self.assertIn("front_heave_nmm", result.compromise_parameters)
        self.assertIn("rear_third_nmm", result.compromise_parameters)

    def test_analyze_stint_gt3_with_explicit_fuel_levels(self) -> None:
        # Caller passes GT3-correct 100 L tank — still must not crash.
        result = analyze_stint(
            BMW_M4_GT3, stint_laps=15, fuel_levels_l=[100.0, 50.0, 12.0]
        )
        self.assertEqual(len(result.conditions), 3)
        # heave_optimal_nmm is the 0.0 sentinel because base_heave_nmm is None.
        for cond in result.conditions:
            self.assertEqual(cond.heave_optimal_nmm, 0.0)
            self.assertEqual(cond.third_optimal_nmm, 0.0)

    def test_find_compromise_parameters_skips_heave_when_zero(self) -> None:
        # ST5: when base_heave_nmm is None the per-condition heave_optimal_nmm
        # is set to 0.0; find_compromise_parameters must not write the key.
        fuel_state = FuelState(
            fuel_load_l=50.0,
            fuel_mass_kg=36.5,
            total_mass_kg=1100.0,
            front_weight_pct=46.0,
            cg_height_mm=300.0,
            pushrod_correction_mm=0.0,
        )
        gt3_conditions = [
            StintCondition(
                label="full_fuel",
                fuel_state=fuel_state,
                heave_optimal_nmm=0.0,
                third_optimal_nmm=0.0,
            ),
        ]
        params, _ = find_compromise_parameters(gt3_conditions)
        self.assertNotIn("front_heave_nmm", params)
        self.assertNotIn("rear_third_nmm", params)

    def test_find_compromise_parameters_writes_heave_when_nonzero(self) -> None:
        # GTP regression: positive heave values still produce compromise.
        fuel_state = FuelState(
            fuel_load_l=89.0,
            fuel_mass_kg=65.0,
            total_mass_kg=1300.0,
            front_weight_pct=46.0,
            cg_height_mm=300.0,
            pushrod_correction_mm=0.0,
        )
        gtp_conditions = [
            StintCondition(
                label="full_fuel",
                fuel_state=fuel_state,
                heave_optimal_nmm=50.0,
                third_optimal_nmm=450.0,
            ),
        ]
        params, _ = find_compromise_parameters(gtp_conditions)
        self.assertIn("front_heave_nmm", params)
        self.assertIn("rear_third_nmm", params)


if __name__ == "__main__":
    unittest.main()
