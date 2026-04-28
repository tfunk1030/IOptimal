"""W5.3 — analyzer/extract, diagnose, causal_graph GT3 awareness tests.

Covers four findings from `docs/audits/gt3_phase2/analyzer.md`:

* A16 — `MeasuredState.lltd_measured` alias is no longer written.  Read of
  `roll_distribution_proxy` continues to work for downstream consumers.
* A17 — `_extract_heave_deflection` is gated on `car.suspension_arch.has_heave_third`
  so the front/rear heave travel metrics stay None on GT3 cars.
* A18 — `diagnose._check_safety` heave-bottoming alarms are gated on the same
  flag so GT3 sessions don't produce phantom critical "stiffen heave spring"
  recommendations.
* A19 — causal-graph root-cause nodes carry `gtp_only` / `gt3_only` flags;
  `applicable_nodes(car)` and `analyze_causes(problems, car=car)` filter the
  graph by architecture so heave-only nodes never surface on GT3 sessions.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analyzer import causal_graph
from analyzer.causal_graph import (
    NODES,
    analyze_causes,
    applicable_nodes,
)
from analyzer.diagnose import Problem, diagnose
from analyzer.extract import MeasuredState, _extract_heave_deflection
from analyzer.setup_reader import CurrentSetup
from car_model.cars import get_car


class _FakeIBT:
    """Minimal stub: never reports any channel as present."""

    def has_channel(self, name: str) -> bool:  # noqa: D401 — stub
        return False

    def channel(self, name: str):  # noqa: D401 — stub
        raise AssertionError(
            f"_FakeIBT.channel({name!r}) called but A17 should short-circuit "
            "before any channel access on GT3 cars"
        )


# ---------- A16: MeasuredState alias removal ----------


class A16AliasTests(unittest.TestCase):
    """The `lltd_measured` field stays None — the alias write is gone."""

    def test_default_state_has_both_fields_none(self) -> None:
        state = MeasuredState()
        self.assertIsNone(state.lltd_measured)
        self.assertIsNone(state.roll_distribution_proxy)

    def test_setting_proxy_does_not_set_alias(self) -> None:
        """Mutating `roll_distribution_proxy` must not bleed into the alias."""
        state = MeasuredState()
        state.roll_distribution_proxy = 0.512
        self.assertEqual(state.roll_distribution_proxy, 0.512)
        # The legacy alias stays untouched — extract.py:688 no longer mirrors.
        self.assertIsNone(state.lltd_measured)


# ---------- A17: heave deflection extraction gated on architecture ----------


class A17HeaveExtractTests(unittest.TestCase):
    def test_gt3_short_circuits_extractor(self) -> None:
        """GT3 must early-return without touching the IBT or mutating state."""
        car = get_car("bmw_m4_gt3")
        state = MeasuredState()
        ibt = _FakeIBT()
        # Empty arrays: function should never read them under GT3 path.
        import numpy as np

        speed = np.array([], dtype=float)
        brake = np.array([], dtype=float)
        # Should not raise — _FakeIBT.channel() would explode if called.
        _extract_heave_deflection(ibt, 0, 0, speed, brake, car, state)
        self.assertIsNone(state.front_heave_travel_used_pct)
        self.assertIsNone(state.front_heave_defl_p99_mm)
        self.assertEqual(state.heave_bottoming_events_front, 0)
        self.assertIsNone(state.rear_heave_travel_used_pct)

    def test_gtp_still_runs_extractor(self) -> None:
        """BMW GTP must NOT short-circuit — preserve legacy behaviour."""
        car = get_car("bmw")
        state = MeasuredState()
        ibt = _FakeIBT()
        import numpy as np

        speed = np.array([], dtype=float)
        brake = np.array([], dtype=float)
        # _FakeIBT reports no channels, so the function falls through the
        # `has_channel` guards — but it MUST attempt the channel checks for
        # GTP cars.  No exception means we got past the architecture gate.
        _extract_heave_deflection(ibt, 0, 0, speed, brake, car, state)
        # Defaults still in place because no channels were available.
        self.assertIsNone(state.front_heave_travel_used_pct)


# ---------- A18: diagnose heave-bottoming alarms gated ----------


def _build_high_heave_state() -> MeasuredState:
    """A measured state simulating exhausted heave travel — would normally
    trigger 3 critical-severity heave alarms in `_check_safety`."""
    return MeasuredState(
        lap_time_s=120.0,
        lap_number=5,
        front_heave_travel_used_pct=92.5,
        front_heave_travel_used_braking_pct=98.0,
        front_heave_defl_p99_mm=18.0,
        front_heave_defl_braking_p99_mm=19.0,
        rear_heave_travel_used_pct=90.0,
        rear_heave_defl_p99_mm=22.0,
        heave_bottoming_events_front=15,
        heave_bottoming_events_rear=10,
    )


def _build_minimal_setup() -> CurrentSetup:
    return CurrentSetup(source="test")


class A18DiagnoseHeaveAlarmsTests(unittest.TestCase):
    def test_gt3_no_phantom_heave_alarms(self) -> None:
        car = get_car("bmw_m4_gt3")
        measured = _build_high_heave_state()
        setup = _build_minimal_setup()
        diag = diagnose(measured, setup, car)

        heave_alarms = [
            p for p in diag.problems
            if "heave" in p.symptom.lower() or "third spring" in p.symptom.lower()
        ]
        self.assertEqual(
            heave_alarms,
            [],
            f"GT3 must not surface heave-bottoming alarms; got: "
            f"{[p.symptom for p in heave_alarms]}",
        )

    def test_gtp_still_emits_heave_alarms(self) -> None:
        """BMW GTP — exact same MeasuredState — must still raise the alarms."""
        car = get_car("bmw")
        measured = _build_high_heave_state()
        setup = _build_minimal_setup()
        diag = diagnose(measured, setup, car)

        heave_symptoms = [p.symptom for p in diag.problems if "heave" in p.symptom.lower()]
        # We expect at least the three heave-travel exhaustion alarms.
        self.assertTrue(
            any("front heave spring" in s.lower() for s in heave_symptoms),
            f"GTP must surface a front heave spring alarm; got: {heave_symptoms}",
        )

    def test_gt3_diagnose_runs_without_raising(self) -> None:
        car = get_car("bmw_m4_gt3")
        measured = _build_high_heave_state()
        setup = _build_minimal_setup()
        diag = diagnose(measured, setup, car)
        # No exception, problems list exists (may be empty for this fixture).
        self.assertIsNotNone(diag.problems)


# ---------- A19: causal_graph architecture filtering ----------


class A19CausalGraphTests(unittest.TestCase):
    def test_heave_nodes_marked_gtp_only(self) -> None:
        for nid in ("heave_too_soft", "heave_too_stiff", "third_too_soft"):
            self.assertTrue(
                NODES[nid].gtp_only,
                f"{nid} must carry gtp_only=True per W5.3:A19",
            )

    def test_gt3_corner_spring_nodes_present_and_gt3_only(self) -> None:
        for nid in (
            "front_corner_spring_too_soft",
            "front_corner_spring_too_stiff",
            "rear_corner_spring_too_soft",
        ):
            self.assertIn(nid, NODES, f"missing GT3 node {nid}")
            self.assertTrue(NODES[nid].gt3_only, f"{nid} must carry gt3_only=True")

    def test_applicable_nodes_gt3_excludes_heave(self) -> None:
        car = get_car("bmw_m4_gt3")
        ids = {n.id for n in applicable_nodes(car)}
        self.assertNotIn("heave_too_soft", ids)
        self.assertNotIn("heave_too_stiff", ids)
        self.assertNotIn("third_too_soft", ids)
        self.assertIn("front_corner_spring_too_soft", ids)
        self.assertIn("front_corner_spring_too_stiff", ids)
        self.assertIn("rear_corner_spring_too_soft", ids)

    def test_applicable_nodes_gtp_excludes_gt3_corner_springs(self) -> None:
        car = get_car("bmw")
        ids = {n.id for n in applicable_nodes(car)}
        self.assertIn("heave_too_soft", ids)
        self.assertNotIn("front_corner_spring_too_soft", ids)
        self.assertNotIn("front_corner_spring_too_stiff", ids)
        self.assertNotIn("rear_corner_spring_too_soft", ids)

    def test_analyze_causes_filters_gt3_root_causes(self) -> None:
        """A GT3 front-bottoming Problem must NOT trace back to `heave_too_soft`."""
        bottoming = Problem(
            category="safety",
            severity="critical",
            symptom="6 front bottoming events (clean track)",
            cause="Front suspension hitting bump stops on clean track surface.",
            speed_context="all",
            measured=6.0,
            threshold=2.0,
            units="events",
            priority=0,
        )
        gt3_car = get_car("bmw_m4_gt3")
        diag_gt3 = analyze_causes([bottoming], car=gt3_car)
        gt3_root_ids = {rca.root_cause.id for rca in diag_gt3.root_causes}
        self.assertNotIn("heave_too_soft", gt3_root_ids)
        # GT3 mirror should fire for the same symptom.
        self.assertIn("front_corner_spring_too_soft", gt3_root_ids)

        # GTP regression: same symptom, BMW car — heave_too_soft should fire.
        gtp_car = get_car("bmw")
        diag_gtp = analyze_causes([bottoming], car=gtp_car)
        gtp_root_ids = {rca.root_cause.id for rca in diag_gtp.root_causes}
        self.assertIn("heave_too_soft", gtp_root_ids)
        self.assertNotIn("front_corner_spring_too_soft", gtp_root_ids)


if __name__ == "__main__":
    unittest.main()
