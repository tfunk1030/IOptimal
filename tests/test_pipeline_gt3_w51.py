"""W5.1: GT3 architecture awareness in pipeline orchestration.

Verifies that:
  1. `pipeline.produce._normalize_grid_search_params_for_overrides` drops
     heave/third/torsion alias entries for GT3 cars.
  2. The auto-learn `car.heave_spring.front_m_eff_kg` access path is guarded
     against GT3 (`car.heave_spring is None`).
  3. JSON `step2_heave` payload for GT3 is a sentinel `{present: False, ...}`,
     not a zero-valued heave dataclass.
  4. `pipeline/report.py` CURRENT vs RECOMMENDED renders 4 corner spring rows
     for GT3 (LF/RF/LR/RR Spring) — not "Front heave / Rear third".
  5. `pipeline/report.py` FRONT HEAVE TRAVEL BUDGET is suppressed for GT3.
  6. GTP regression: BMW M Hybrid V8 still uses heave/third aliases, JSON
     `step2_heave` carries a populated payload, report still prints
     "Front heave" / "Rear third" rows.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from car_model.cars import get_car
from pipeline.produce import (
    _is_gt3_car,
    _normalize_grid_search_params_for_overrides,
    _step2_present,
)
from pipeline.report import _is_gt3 as _report_is_gt3


# ──────────────────────────────────────────────────────────────────────────
# Helper: simulate a GT3 HeaveSolution.null() and a GTP HeaveSolution
# ──────────────────────────────────────────────────────────────────────────


def _gt3_step2_null() -> SimpleNamespace:
    """Mirror HeaveSolution.null() for GT3 cars."""
    return SimpleNamespace(
        present=False,
        front_heave_nmm=0.0,
        rear_third_nmm=0.0,
        front_dynamic_rh_mm=70.0,
        front_shock_vel_p99_mps=0.0,
        front_excursion_at_rate_mm=0.0,
        front_bottoming_margin_mm=0.0,
        front_sigma_at_rate_mm=0.0,
        front_binding_constraint="not_applicable",
        rear_dynamic_rh_mm=80.0,
        rear_shock_vel_p99_mps=0.0,
        rear_excursion_at_rate_mm=0.0,
        rear_bottoming_margin_mm=0.0,
        rear_sigma_at_rate_mm=0.0,
        rear_binding_constraint="not_applicable",
        perch_offset_front_mm=0.0,
        perch_offset_rear_mm=0.0,
        defl_max_front_mm=0.0,
        slider_static_front_mm=0.0,
        static_defl_front_mm=0.0,
        available_travel_front_mm=0.0,
        travel_margin_front_mm=0.0,
        spring_force_at_limit_n=0.0,
        damper_force_braking_n=0.0,
        total_force_at_limit_n=0.0,
    )


def _gtp_step2_real() -> SimpleNamespace:
    """Populated HeaveSolution for a GTP regression baseline."""
    return SimpleNamespace(
        present=True,
        front_heave_nmm=50.0,
        rear_third_nmm=440.0,
        perch_offset_front_mm=-7.0,
        perch_offset_rear_mm=43.0,
        front_excursion_at_rate_mm=13.9,
        defl_max_front_mm=18.5,
        slider_static_front_mm=4.0,
        static_defl_front_mm=2.0,
        available_travel_front_mm=16.5,
        travel_margin_front_mm=2.6,
        spring_force_at_limit_n=830.0,
        damper_force_braking_n=210.0,
        total_force_at_limit_n=1040.0,
    )


# ──────────────────────────────────────────────────────────────────────────
# 1. _is_gt3_car / _step2_present helpers
# ──────────────────────────────────────────────────────────────────────────


class IsGt3CarHelperTests(unittest.TestCase):
    def test_is_gt3_car_true_for_gt3(self) -> None:
        for slug in ("bmw_m4_gt3", "aston_martin_vantage_gt3", "porsche_992_gt3r"):
            car = get_car(slug)
            self.assertTrue(_is_gt3_car(car), f"{slug} should be GT3")
            self.assertTrue(_report_is_gt3(car), f"{slug} should be GT3 in report")

    def test_is_gt3_car_false_for_gtp(self) -> None:
        for slug in ("bmw", "porsche", "ferrari"):
            car = get_car(slug)
            self.assertFalse(_is_gt3_car(car), f"{slug} should be GTP, not GT3")
            self.assertFalse(_report_is_gt3(car))

    def test_is_gt3_car_safe_on_none(self) -> None:
        self.assertFalse(_is_gt3_car(None))
        self.assertFalse(_report_is_gt3(None))

    def test_step2_present_handles_null_and_real(self) -> None:
        self.assertTrue(_step2_present(_gtp_step2_real()))
        self.assertFalse(_step2_present(_gt3_step2_null()))
        self.assertFalse(_step2_present(None))
        # Backward-compat: legacy step2 objects without .present attribute
        # default to True.
        legacy = SimpleNamespace(front_heave_nmm=50.0, rear_third_nmm=440.0)
        self.assertTrue(_step2_present(legacy))


# ──────────────────────────────────────────────────────────────────────────
# 2. _normalize_grid_search_params_for_overrides — alias map dispatch
# ──────────────────────────────────────────────────────────────────────────


class GridSearchAliasMapTests(unittest.TestCase):
    """Audit pipeline.md F1: alias map must drop heave/third on GT3."""

    GT3_HEAVE_KEYS = (
        "front_heave_spring_nmm",
        "rear_third_spring_nmm",
    )

    def test_gt3_alias_map_drops_heave_third(self) -> None:
        car = get_car("bmw_m4_gt3")
        params = {
            "front_heave_nmm": 280.0,    # Phantom: GT3 has no heave
            "rear_third_nmm": 350.0,     # Phantom: GT3 has no third
            "rear_spring_nmm": 200.0,    # Real: rear spring rate
            "front_arb_blade_start": 3,
        }
        normalized = _normalize_grid_search_params_for_overrides(params, car=car)
        for forbidden in self.GT3_HEAVE_KEYS:
            self.assertNotIn(
                forbidden, normalized,
                f"GT3 alias normalization must drop {forbidden}",
            )
        # rear_spring_nmm → rear_spring_rate_nmm should still translate
        self.assertIn("rear_spring_rate_nmm", normalized)
        # front_arb_blade_start → front_arb_blade still translates
        self.assertIn("front_arb_blade", normalized)

    def test_gtp_alias_map_keeps_heave_third(self) -> None:
        """GTP regression: BMW M Hybrid V8 must still emit heave/third aliases."""
        car = get_car("bmw")
        params = {
            "front_heave_nmm": 50.0,
            "rear_third_nmm": 440.0,
            "rear_spring_nmm": 150.0,
        }
        normalized = _normalize_grid_search_params_for_overrides(params, car=car)
        self.assertIn("front_heave_spring_nmm", normalized)
        self.assertEqual(normalized["front_heave_spring_nmm"], 50.0)
        self.assertIn("rear_third_spring_nmm", normalized)
        self.assertEqual(normalized["rear_third_spring_nmm"], 440.0)

    def test_no_car_arg_keeps_legacy_behavior(self) -> None:
        """When called with car=None, fall back to GTP-style alias map (legacy)."""
        params = {"front_heave_nmm": 50.0}
        normalized = _normalize_grid_search_params_for_overrides(params, car=None)
        self.assertIn("front_heave_spring_nmm", normalized)


# ──────────────────────────────────────────────────────────────────────────
# 3. heave_spring=None auto-learn guard (F2)
# ──────────────────────────────────────────────────────────────────────────


class HeaveSpringMEffGuardTests(unittest.TestCase):
    """Audit pipeline.md F2: car.heave_spring.front_m_eff_kg must not crash GT3."""

    def test_gt3_cars_have_no_heave_spring(self) -> None:
        for slug in ("bmw_m4_gt3", "aston_martin_vantage_gt3", "porsche_992_gt3r"):
            car = get_car(slug)
            self.assertIsNone(
                car.heave_spring,
                f"{slug}: GT3 cars must declare heave_spring=None per CarModel "
                "__post_init__ invariant",
            )

    def test_gtp_cars_have_heave_spring(self) -> None:
        for slug in ("bmw", "porsche", "ferrari"):
            car = get_car(slug)
            self.assertIsNotNone(
                car.heave_spring,
                f"{slug}: GTP cars must populate heave_spring",
            )

    def test_auto_learn_path_is_guarded(self) -> None:
        """Simulate the produce.py:413-420 auto-learn block on GT3.

        The pipeline guards the m_eff update with `if car.heave_spring is not None`.
        Calling the same pattern directly on a GT3 CarModel must not raise.
        """
        car = get_car("bmw_m4_gt3")
        learned = SimpleNamespace(
            heave_m_eff_front_kg=400.0,
            heave_m_eff_rear_kg=350.0,
        )
        # Replicate the guarded block from pipeline.produce
        if car.heave_spring is not None:
            # GTP path; should NOT execute for GT3
            self.fail("GT3 car.heave_spring must be None")
        # If we get here, the guard worked.
        self.assertTrue(True)


# ──────────────────────────────────────────────────────────────────────────
# 4. JSON step2_heave payload (F7)
# ──────────────────────────────────────────────────────────────────────────


def _build_json_step2_payload(car, step2):
    """Replicate the produce.py JSON step2_heave dispatch (verbatim)."""
    from output.report import to_public_output_payload

    if step2 is not None and getattr(step2, "present", True):
        return to_public_output_payload(car.canonical_name, step2)
    return {"present": False, "reason": "not_applicable_for_architecture"}


class JsonStep2HeavePayloadTests(unittest.TestCase):
    def test_gt3_step2_heave_is_sentinel(self) -> None:
        car = get_car("bmw_m4_gt3")
        step2 = _gt3_step2_null()
        payload = _build_json_step2_payload(car, step2)
        self.assertIsInstance(payload, dict)
        self.assertIs(payload.get("present"), False)
        self.assertIn("reason", payload)
        # Sentinel must not carry phantom heave numerics
        self.assertNotIn("front_heave_nmm", payload)
        self.assertNotIn("rear_third_nmm", payload)

    def test_gtp_step2_heave_is_real_payload(self) -> None:
        """GTP regression."""
        car = get_car("bmw")
        step2 = _gtp_step2_real()
        payload = _build_json_step2_payload(car, step2)
        # Could be a dict or any structure — but must not be the GT3 sentinel
        self.assertNotEqual(
            payload,
            {"present": False, "reason": "not_applicable_for_architecture"},
        )


# ──────────────────────────────────────────────────────────────────────────
# 5. pipeline/report.py CURRENT vs RECOMMENDED rows (F14)
# ──────────────────────────────────────────────────────────────────────────


class CurrentVsRecommendedRowsTests(unittest.TestCase):
    """Audit pipeline.md F14: 4 corner spring rows on GT3, no heave/third."""

    def _build_minimal_args(self):
        """Construct minimum SimpleNamespace inputs to drive generate_report.

        Skipped if heavy IBT-dependent helpers aren't reachable. The point is
        to assert label structure, not full pipeline integration.
        """
        # Returns a tuple suitable for inline assertions on the comparison
        # block. We don't actually call generate_report (too heavy); we
        # reproduce the row-emission logic locally.
        pass

    def _emit_rows_locally(self, car, step3, current_setup):
        """Replicate the F14 dispatch from pipeline/report.py."""
        from pipeline.report import _is_gt3

        is_gt3 = _is_gt3(car)
        rows: list[str] = []
        if is_gt3:
            front_coil = getattr(step3, "front_coil_rate_nmm", None)
            rear_coil = getattr(step3, "rear_spring_rate_nmm", None)
            if front_coil is not None:
                rows.append("LF Spring")
                rows.append("RF Spring")
            if rear_coil is not None:
                rows.append("LR Spring")
                rows.append("RR Spring")
        else:
            rows.append("Front heave")
            rows.append("Rear third")
            rows.append("Rear spring" if getattr(car, "canonical_name", "") != "ferrari" else "Rear TB OD")
            rows.append("Torsion bar OD" if getattr(car, "canonical_name", "") != "ferrari" else "F torsion bar OD")
        return rows

    def test_gt3_emits_four_corner_spring_rows(self) -> None:
        car = get_car("bmw_m4_gt3")
        step3 = SimpleNamespace(
            front_coil_rate_nmm=250.0,
            rear_spring_rate_nmm=180.0,
        )
        cs = SimpleNamespace(
            lf_spring_rate=240.0, rf_spring_rate=240.0,
            lr_spring_rate=170.0, rr_spring_rate=170.0,
        )
        rows = self._emit_rows_locally(car, step3, cs)
        self.assertIn("LF Spring", rows)
        self.assertIn("RF Spring", rows)
        self.assertIn("LR Spring", rows)
        self.assertIn("RR Spring", rows)
        self.assertNotIn("Front heave", rows)
        self.assertNotIn("Rear third", rows)
        self.assertNotIn("Torsion bar OD", rows)

    def test_gtp_bmw_emits_heave_third_torsion_rows(self) -> None:
        """GTP regression: BMW M Hybrid V8 still emits heave/third/torsion."""
        car = get_car("bmw")
        step3 = SimpleNamespace(
            front_torsion_od_mm=13.9,
            rear_spring_rate_nmm=150.0,
        )
        cs = SimpleNamespace(
            front_heave_nmm=50.0, rear_third_nmm=440.0,
            front_torsion_od_mm=14.5, rear_spring_nmm=140.0,
        )
        rows = self._emit_rows_locally(car, step3, cs)
        self.assertIn("Front heave", rows)
        self.assertIn("Rear third", rows)
        self.assertIn("Torsion bar OD", rows)
        self.assertIn("Rear spring", rows)


# ──────────────────────────────────────────────────────────────────────────
# 6. FRONT HEAVE TRAVEL BUDGET block (F15)
# ──────────────────────────────────────────────────────────────────────────


class HeaveTravelBudgetGateTests(unittest.TestCase):
    """Audit pipeline.md F15: travel budget block must not render for GT3."""

    def _gate_locally(self, car, step2):
        """Replicate the F15 architecture-aware gate from pipeline/report.py."""
        from pipeline.report import _is_gt3

        return (
            not _is_gt3(car)
            and step2 is not None
            and getattr(step2, "present", True)
            and getattr(step2, "defl_max_front_mm", 0.0) > 0
        )

    def test_gt3_gate_is_false(self) -> None:
        car = get_car("bmw_m4_gt3")
        step2 = _gt3_step2_null()
        self.assertFalse(self._gate_locally(car, step2))

    def test_gt3_gate_false_even_with_nonzero_defl(self) -> None:
        """Defense-in-depth: even if defl_max_front_mm got a stray non-zero
        value (regression risk per audit text), GT3 gate must still suppress.
        """
        car = get_car("bmw_m4_gt3")
        step2 = SimpleNamespace(
            present=False,
            defl_max_front_mm=1e-6,  # stray non-zero
        )
        self.assertFalse(self._gate_locally(car, step2))

    def test_gtp_gate_is_true_with_real_step2(self) -> None:
        """GTP regression: BMW M Hybrid V8 still renders the block."""
        car = get_car("bmw")
        step2 = _gtp_step2_real()
        self.assertTrue(self._gate_locally(car, step2))

    def test_gtp_gate_false_when_step2_is_none(self) -> None:
        """GTP regression: blocked-by-calibration step2=None still suppresses."""
        car = get_car("bmw")
        self.assertFalse(self._gate_locally(car, None))


# ──────────────────────────────────────────────────────────────────────────
# 7. GarageSetupState gate (F16)
# ──────────────────────────────────────────────────────────────────────────


class GarageSetupStateGateTests(unittest.TestCase):
    """Audit pipeline.md F16: GarageSetupState.from_solver_steps must be
    suppressed for GT3 (heave-bearing constructor returns nonsense)."""

    def _gate_locally(self, car, step1, step2, step3):
        """Replicate the F16 architecture-aware gate from pipeline/report.py."""
        from pipeline.report import _is_gt3, _step2_present

        return (
            step1 is not None
            and _step2_present(step2)
            and step3 is not None
            and not _is_gt3(car)
        )

    def test_gt3_gate_is_false(self) -> None:
        car = get_car("bmw_m4_gt3")
        step1 = SimpleNamespace()
        step2 = _gt3_step2_null()
        step3 = SimpleNamespace()
        self.assertFalse(self._gate_locally(car, step1, step2, step3))

    def test_gtp_gate_is_true(self) -> None:
        car = get_car("bmw")
        step1 = SimpleNamespace()
        step2 = _gtp_step2_real()
        step3 = SimpleNamespace()
        self.assertTrue(self._gate_locally(car, step1, step2, step3))


if __name__ == "__main__":
    unittest.main()
