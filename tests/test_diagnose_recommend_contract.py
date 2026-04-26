"""Contract tests between `analyzer/diagnose.py` and `analyzer/recommend.py`.

Background: F-1 / BUG-C4 in `docs/audit/2026-04-26/...`. Every problem class
that `diagnose.py` emits must trigger at least one recommendation handler in
`recommend.py`. The bug class these tests guard against is "symptom string or
cause string drifted on one side; the other side silently stops matching" —
which makes ARB / thermal / LLTD recommendations disappear from production
output without any test failure.

We construct synthetic ``Problem`` instances mirroring the strings that
diagnose.py actually emits today and assert that ``_recommend_for_problem``
returns at least one change for each.
"""

from __future__ import annotations

import pytest

from analyzer.diagnose import Problem
from analyzer.recommend import _recommend_for_problem
from analyzer.setup_reader import CurrentSetup
from car_model.cars import get_car


@pytest.fixture
def car():
    """Use BMW for these contract tests — every car has the same diagnose
    surface, BMW just has the most complete model on disk."""
    return get_car("bmw")


@pytest.fixture
def setup():
    """A neutral mid-range setup so both 'increase' and 'decrease' branches
    have headroom to fire."""
    return CurrentSetup(
        source="test",
        wing_angle_deg=15.0,
        front_rh_at_speed_mm=30.0,
        rear_rh_at_speed_mm=45.0,
        static_front_rh_mm=55.0,
        static_rear_rh_mm=65.0,
        front_pushrod_mm=0.0,
        rear_pushrod_mm=0.0,
        front_heave_nmm=180.0,
        front_heave_perch_mm=4.0,
        rear_third_nmm=200.0,
        rear_third_perch_mm=4.0,
        front_torsion_od_mm=14.5,
        rear_spring_nmm=160.0,
        rear_spring_perch_mm=4.0,
        front_arb_size="Medium",
        front_arb_blade=3,
        rear_arb_size="Medium",
        rear_arb_blade=3,
        front_camber_deg=-2.5,
        rear_camber_deg=-1.8,
        front_toe_mm=0.0,
        rear_toe_mm=0.4,
    )


def _roll_proxy_problem(direction: str) -> Problem:
    """Build a balance problem whose strings match diagnose.py exactly.

    See ``analyzer/diagnose.py:700-732`` — the cause text says "front-heavy"
    or "rear-heavy", NOT "too high" / "too low". F-1 fixed recommend.py to
    accept both; this test pins that contract.
    """
    if direction == "front-heavy":
        return Problem(
            category="balance",
            severity="minor",
            symptom="Roll distribution proxy 53.2% vs target 51% (delta +2.2%)",
            cause=(
                "Ride-height-derived roll support proxy is front-heavy. This often correlates "
                "with mechanical understeer, but it is not a direct LLTD measurement."
            ),
            speed_context="all",
            measured=53.2,
            threshold=51.0,
            units="%",
            priority=2,
        )
    return Problem(
        category="balance",
        severity="minor",
        symptom="Roll distribution proxy 47.5% vs target 51% (delta -3.5%)",
        cause=(
            "Ride-height-derived roll support proxy is rear-heavy. This can line up with "
            "oversteer risk, but it is still only a proxy."
        ),
        speed_context="all",
        measured=47.5,
        threshold=51.0,
        units="%",
        priority=2,
    )


def test_lltd_front_heavy_routes_to_recommend(car, setup):
    """Front-heavy roll proxy must produce a setup change.

    Regression for F-1: previously the 'roll distribution proxy' symptom was
    matched by recommend.py but the inner branches required 'too high' /
    'too low' in the cause string which diagnose.py never emits.
    """
    problem = _roll_proxy_problem("front-heavy")
    changes = _recommend_for_problem(problem, setup, setup, car)
    assert changes, (
        "front-heavy roll proxy produced no recommendations — the "
        "diagnose↔recommend cause-string contract is broken"
    )
    # The recommendation should target Step 4 (ARBs) and either soften front
    # ARB or stiffen rear ARB.
    arb_changes = [c for c in changes if c.step == 4]
    assert arb_changes, "no Step 4 (ARB) recommendation generated"
    params = {c.parameter for c in arb_changes}
    assert params & {"front_arb_blade", "rear_arb_blade"}


def test_lltd_rear_heavy_routes_to_recommend(car, setup):
    problem = _roll_proxy_problem("rear-heavy")
    changes = _recommend_for_problem(problem, setup, setup, car)
    assert changes, (
        "rear-heavy roll proxy produced no recommendations — the "
        "diagnose↔recommend cause-string contract is broken"
    )
    arb_changes = [c for c in changes if c.step == 4]
    assert arb_changes
    # Stiffening front ARB raises LLTD.
    assert any(c.parameter == "front_arb_blade" for c in arb_changes)


def test_understeer_low_speed_routes_to_recommend(car, setup):
    problem = Problem(
        category="balance",
        severity="significant",
        symptom="Understeer +1.4 deg in mid-corner",
        cause="Front grip exceeded; chassis pushes wide.",
        speed_context="low",
        measured=1.4,
        threshold=0.5,
        units="deg",
        priority=2,
    )
    changes = _recommend_for_problem(problem, setup, setup, car)
    assert changes, "low-speed understeer produced no recommendations"


def test_oversteer_routes_to_recommend(car, setup):
    """A loose / oversteer balance problem should produce an ARB change."""
    problem = Problem(
        category="balance",
        severity="significant",
        symptom="Loose on corner exit",
        cause="Rear stepping out under throttle.",
        speed_context="low",
        measured=-1.2,
        threshold=-0.5,
        units="deg",
        priority=2,
    )
    changes = _recommend_for_problem(problem, setup, setup, car)
    assert changes, "oversteer produced no recommendations"


def test_unknown_category_returns_empty(car, setup):
    """Sanity: an unknown category should silently return no changes (not crash)."""
    problem = Problem(
        category="not-a-real-category",
        severity="minor",
        symptom="x",
        cause="y",
        speed_context="all",
        measured=0.0,
        threshold=0.0,
        units="",
        priority=2,
    )
    assert _recommend_for_problem(problem, setup, setup, car) == []
