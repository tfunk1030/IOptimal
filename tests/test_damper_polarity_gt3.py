"""W3.2 — per-car damper click polarity + range tests.

Pins the contract that:
  * `DamperModel.click_polarity` defaults to "higher_stiffer" (BMW convention).
  * BMW M4 GT3 / Aston / Porsche 992 GT3 R inherit the BMW polarity.
  * Porsche 992 GT3 R click ranges are 0–12 (driver IBT confirms clicks at 12;
    BMW/Aston remain 0–11).
  * `legality_engine.validate_candidate_legality` flips the front-vs-rear
    "softer" inequality based on per-car click polarity.
  * `candidate_search._adjust_integer` clamps to per-car bounds (not the old
    hardcoded 0–20) and inverts adjustment sign for inverted-polarity cars.
  * `damper_solver.solve()` collapses asymmetric L/R damper adjustments to
    per-axle averages on GT3 (per-axle garage YAML), but preserves true
    per-corner asymmetry on GTP.

Audit reference: docs/audits/gt3_phase2/solver-damper-legality.md L3, L4, F2,
CS6, CS7, LS5.
"""
from __future__ import annotations

import copy

import pytest

from analyzer.extract import MeasuredState
from car_model.cars import (
    ASTON_MARTIN_VANTAGE_GT3,
    BMW_M4_GT3,
    BMW_M_HYBRID_V8,
    DamperModel,
    PORSCHE_992_GT3R,
)
from solver.candidate_search import _adjust_integer
from solver.damper_solver import DamperSolver
from solver.legality_engine import validate_candidate_legality
from track_model.profile import TrackProfile


# ─── 1. DamperModel default polarity ─────────────────────────────────────


def test_damper_model_default_polarity_is_higher_stiffer():
    """Default polarity preserves BMW behaviour for any not-yet-stubbed car."""
    d = DamperModel()
    assert d.click_polarity == "higher_stiffer"


# ─── 2. BMW M4 GT3 polarity regression ───────────────────────────────────


def test_bmw_m4_gt3_uses_higher_stiffer_polarity():
    """BMW M4 GT3 uses BMW Penske convention (more clicks = stiffer)."""
    assert BMW_M4_GT3.damper.click_polarity == "higher_stiffer"


def test_aston_vantage_gt3_uses_higher_stiffer_polarity():
    """Aston Vantage GT3 EVO uses higher-stiffer convention (Penske-style)."""
    assert ASTON_MARTIN_VANTAGE_GT3.damper.click_polarity == "higher_stiffer"


def test_porsche_992_gt3r_uses_higher_stiffer_polarity():
    """Porsche 992 GT3 R uses higher-stiffer convention.

    NOTE: The Porsche 963 GTP is *higher-stiffer* (Multimatic), and the 992 GT3 R
    follows the same Porsche convention. Only Audi R8 LMS evo II / McLaren 720S /
    Corvette Z06 GT3.R use the Penske inverted (lower-stiffer) convention; those
    cars are deferred to W10.1 stub creation.
    """
    assert PORSCHE_992_GT3R.damper.click_polarity == "higher_stiffer"


# ─── 3. Porsche 992 click-range fix (driver IBT shows clicks at 12) ──────


def test_porsche_992_gt3r_click_ranges_extend_to_12():
    """Driver IBT shows LSC=12 and LSR=12 — range must include 12."""
    d = PORSCHE_992_GT3R.damper
    assert d.ls_comp_range == (0, 12)
    assert d.ls_rbd_range == (0, 12)
    assert d.hs_comp_range == (0, 12)
    assert d.hs_rbd_range == (0, 12)


def test_bmw_m4_gt3_click_ranges_remain_default_0_11():
    """BMW M4 GT3 has no per-car-spec source for wider range — default (0,11)."""
    d = BMW_M4_GT3.damper
    assert d.ls_comp_range == (0, 11)
    assert d.ls_rbd_range == (0, 11)
    assert d.hs_comp_range == (0, 11)
    assert d.hs_rbd_range == (0, 11)


def test_aston_vantage_gt3_click_ranges_remain_default_0_11():
    """Aston driver-loaded values cap at 11 — default (0,11)."""
    d = ASTON_MARTIN_VANTAGE_GT3.damper
    assert d.ls_comp_range == (0, 11)
    assert d.ls_rbd_range == (0, 11)


# ─── 4. legality_engine polarity dispatch ────────────────────────────────


def _inverted_polarity_car():
    """Build an inverted-polarity test fixture by deepcopying BMW GTP and
    flipping the polarity bit. We don't need a real Audi/McLaren/Corvette stub
    to exercise the legality dispatch — only the polarity flag matters."""
    car = copy.deepcopy(BMW_M_HYBRID_V8)
    car.damper.click_polarity = "lower_stiffer"
    return car


def test_legality_higher_stiffer_front_softer_than_rear_fires_penalty():
    """Higher-stiffer car: fls < rls means front is softer — penalty fires."""
    params = {"front_ls_comp": 4, "rear_ls_comp": 8}
    result = validate_candidate_legality(params, BMW_M_HYBRID_V8)
    assert any("LS comp softer" in p for p in result.soft_penalties)


def test_legality_higher_stiffer_front_stiffer_than_rear_no_penalty():
    """Higher-stiffer car: fls > rls means front is stiffer — no penalty."""
    params = {"front_ls_comp": 8, "rear_ls_comp": 4}
    result = validate_candidate_legality(params, BMW_M_HYBRID_V8)
    assert not any("LS comp softer" in p for p in result.soft_penalties)


def test_legality_lower_stiffer_inverts_softer_inequality():
    """Inverted-polarity car: same numeric values flip semantic interpretation.

    With click_polarity="lower_stiffer", front_ls_comp=8 and rear_ls_comp=4
    means the FRONT is the SOFTER one (higher click → softer in the inverted
    convention) — the penalty must fire even though the numeric inequality
    is reversed from the higher-stiffer case.
    """
    car = _inverted_polarity_car()
    params = {"front_ls_comp": 8, "rear_ls_comp": 4}
    result = validate_candidate_legality(params, car)
    assert any("LS comp softer" in p for p in result.soft_penalties)

    # And the OPPOSITE direction (front = 4, rear = 8) does NOT fire on an
    # inverted-polarity car, because front is now the stiffer side.
    params2 = {"front_ls_comp": 4, "rear_ls_comp": 8}
    result2 = validate_candidate_legality(params2, car)
    assert not any("LS comp softer" in p for p in result2.soft_penalties)


def test_legality_polarity_dispatch_applies_to_all_four_hierarchies():
    """LS comp, LS rbd, HS comp, HS rbd hierarchies all dispatch on polarity."""
    car = _inverted_polarity_car()
    # On inverted-polarity car, "front softer than rear" = front_clicks > rear_clicks
    params = {
        "front_ls_comp": 8, "rear_ls_comp": 4,
        "front_ls_rbd": 8, "rear_ls_rbd": 4,
        # rear "much stiffer" than front (HS): rear_clicks << front_clicks (i.e. -3)
        "front_hs_comp": 8, "rear_hs_comp": 4,
        "front_hs_rbd": 8, "rear_hs_rbd": 4,
    }
    result = validate_candidate_legality(params, car)
    # Should fire all four soft-penalty hierarchy violations.
    assert any("LS comp softer" in p for p in result.soft_penalties)
    assert any("LS rbd softer" in p for p in result.soft_penalties)
    assert any("HS comp" in p and "stiffer" in p for p in result.soft_penalties)
    assert any("HS rbd" in p and "stiffer" in p for p in result.soft_penalties)


# ─── 5. candidate_search bounds clamp uses per-car damper range ──────────


def test_adjust_integer_clamps_to_bmw_range_not_hardcoded_20():
    """Pre-W3.2 the bounds were hardcoded lo=0 hi=20. Now they come from
    car.damper.{hs_comp,ls_rbd}_range. With BMW.hs_comp_range=(0,11), an
    aggressive +15 adjustment must clamp to 11, not 20.
    """
    d = BMW_M_HYBRID_V8.damper
    # BMW GTP has hs_comp_range=(0, 11) per the standard spec.
    hi = d.hs_comp_range[1]
    assert hi == 11, "test invariant: BMW GTP hs_comp_range is (0, 11)"

    mapping = {"hs_comp": 5}
    _adjust_integer(mapping, "hs_comp", 15, lo=d.hs_comp_range[0], hi=d.hs_comp_range[1])
    assert mapping["hs_comp"] == 11

    # And the per-Porsche-992 wider range allows clamping at 12 instead.
    pd = PORSCHE_992_GT3R.damper
    mapping2 = {"hs_comp": 5}
    _adjust_integer(mapping2, "hs_comp", 15, lo=pd.hs_comp_range[0], hi=pd.hs_comp_range[1])
    assert mapping2["hs_comp"] == 12


# ─── 6 + 7. damper_solver.solve() L/R averaging on GT3, asymmetric on GTP ─


def _track_for_asymmetric_test(car_key: str = "bmw") -> TrackProfile:
    return TrackProfile(
        track_name="UnitTest",
        track_config="baseline",
        track_length_m=5000.0,
        car=car_key,
        best_lap_time_s=90.0,
        shock_vel_p95_front_mps=0.120,
        shock_vel_p95_rear_mps=0.160,
        shock_vel_p99_front_mps=0.260,
        shock_vel_p99_rear_mps=0.320,
    )


def _measured_with_asymmetry() -> MeasuredState:
    """Build a MeasuredState with strongly-asymmetric L vs R shock velocities so
    the damper_solver's per-corner adjustment branch fires."""
    return MeasuredState(
        lf_shock_vel_p95_mps=0.300,    # busier
        rf_shock_vel_p95_mps=0.150,    # quieter
        lr_shock_vel_p95_mps=0.200,    # busier
        rr_shock_vel_p95_mps=0.100,    # quieter
        rear_shock_oscillation_hz=0.0,
    )


def test_damper_solver_collapses_lr_asymmetry_for_gt3():
    """GT3 garage YAML is per-axle (FrontDampers/RearDampers). Asymmetric L/R
    adjustments would be silently dropped on the .sto write — collapse to
    per-axle averages so the solver intent is preserved.
    """
    car = copy.deepcopy(BMW_M4_GT3)
    # Bypass the zeta-calibration gate for the test; we only care about the
    # L/R averaging behaviour, not real damping values.
    car.damper.zeta_is_calibrated = True
    # Provide minimum zeta targets so DamperSolver doesn't choke on Nones.
    car.damper.zeta_target_ls_front = 0.55
    car.damper.zeta_target_ls_rear = 0.50
    car.damper.zeta_target_hs_front = 0.30
    car.damper.zeta_target_hs_rear = 0.30

    solver = DamperSolver(car, _track_for_asymmetric_test("bmw_m4_gt3"))
    sol = solver.solve(
        front_wheel_rate_nmm=140.0,
        rear_wheel_rate_nmm=180.0,
        front_dynamic_rh_mm=20.0,
        rear_dynamic_rh_mm=40.0,
        fuel_load_l=60.0,
        measured=_measured_with_asymmetry(),
    )

    assert sol.lf.hs_comp == sol.rf.hs_comp, (
        f"GT3 must collapse front L/R hs_comp (got LF={sol.lf.hs_comp}, "
        f"RF={sol.rf.hs_comp})"
    )
    assert sol.lr.hs_comp == sol.rr.hs_comp, (
        f"GT3 must collapse rear L/R hs_comp (got LR={sol.lr.hs_comp}, "
        f"RR={sol.rr.hs_comp})"
    )


def test_damper_solver_preserves_lr_asymmetry_for_gtp():
    """GTP exposes per-corner damper YAML. The L/R asymmetric adjustment must
    NOT be collapsed — that would drop physics signal where the simulator
    actually consumes it.
    """
    car = BMW_M_HYBRID_V8  # zeta_is_calibrated=True per fixture
    solver = DamperSolver(car, _track_for_asymmetric_test("bmw"))
    sol = solver.solve(
        front_wheel_rate_nmm=140.0,
        rear_wheel_rate_nmm=180.0,
        front_dynamic_rh_mm=20.0,
        rear_dynamic_rh_mm=40.0,
        fuel_load_l=60.0,
        measured=_measured_with_asymmetry(),
        front_heave_nmm=180.0,
        rear_third_nmm=540.0,
    )

    # With LF >> RF and LR >> RR shock velocities, the busy side gets softened
    # by 1+ click. GTP must retain the asymmetry.
    assert sol.lf.hs_comp != sol.rf.hs_comp, (
        f"GTP must preserve front L/R asymmetry (LF={sol.lf.hs_comp}, "
        f"RF={sol.rf.hs_comp}) — got identical, collapse leaked into GTP path"
    )
    assert sol.lr.hs_comp != sol.rr.hs_comp, (
        f"GTP must preserve rear L/R asymmetry (LR={sol.lr.hs_comp}, "
        f"RR={sol.rr.hs_comp}) — got identical, collapse leaked into GTP path"
    )
