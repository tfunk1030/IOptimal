"""W2.1 tests: Step 2 (heave/third) GT3 architecture dispatch.

These tests pin the contract that `_run_sequential_solver`,
`_run_branching_solver`, `materialize_overrides`, and the BMW/Sebring
optimizer constructor all branch on `car.suspension_arch.has_heave_third`
and substitute `HeaveSolution.null(...)` for GT3 cars rather than calling
the GTP-only `HeaveSolver`.

Wave 1 (PR #102 / W1.x) shipped:
  * `HeaveSolver.__init__` raises ValueError on GT3 cars.
  * `HeaveSolution.null(front_dynamic_rh_mm, rear_dynamic_rh_mm)` factory.
  * `solver_steps_to_params`, `_extract_target_maps`, `decision_trace` all
    honour `step2.present`.
  * `CalibrationGate.check_step(2)` returns `not_applicable=True` on GT3.

Wave 2 Unit 1 (this file) verifies the orchestrators:
  - the constructor short-circuit propagates Step-1 RH through `null()`,
  - GTP cars still produce a real `present=True` Step 2,
  - `FullSetupOptimizer.__init__` no longer crashes on GT3 input.

Step 1 itself (rake_solver) reads `car.heave_spring` and crashes on GT3 —
that's W2.2's territory. To exercise W2.1 in isolation we monkeypatch
`RakeSolver.solve` to return a synthetic step1 with the dynamic RH fields
the heave dispatch consumes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from car_model.cars import BMW_M4_GT3, BMW_M_HYBRID_V8
from solver.heave_solver import HeaveSolution


@dataclass
class _StubStep1:
    """Minimal RakeSolution-shaped stub used by W2.1 dispatch tests."""

    dynamic_front_rh_mm: float = 22.0
    dynamic_rear_rh_mm: float = 60.0
    static_front_rh_mm: float = 60.0
    static_rear_rh_mm: float = 80.0
    rake_static_mm: float = 20.0
    front_pushrod_offset_mm: float = 0.0
    rear_pushrod_offset_mm: float = 0.0
    aero_compression_front_mm: float = 8.0
    aero_compression_rear_mm: float = 6.0
    target_balance: float = 44.0
    achieved_balance: float = 44.0
    df_per_speed_band: dict = field(default_factory=dict)
    aero_target_speed_kph: float = 200.0


@pytest.fixture
def stub_track():
    """A bare-bones TrackProfile sufficient for dispatch tests.

    The heave dispatch path doesn't read track-only fields beyond
    `track_name`, so a synthetic profile keeps the test fast and offline.
    """
    from track_model.profile import TrackProfile
    return TrackProfile(
        track_name="Spielberg",
        track_config="",
        track_length_m=4318.0,
        car="bmw_m4_gt3",
        best_lap_time_s=90.0,
    )


@pytest.fixture
def gtp_track():
    from track_model.profile import TrackProfile
    return TrackProfile(
        track_name="Sebring International Raceway",
        track_config="",
        track_length_m=6019.0,
        car="bmw",
        best_lap_time_s=110.0,
    )


def _make_inputs(car, track):
    """Build a SolveChainInputs that bypasses real telemetry / aero."""
    from solver.solve_chain import SolveChainInputs

    # Surface is an opaque object passed into RakeSolver; we monkeypatch
    # rake_solver.solve so the surface is never dereferenced.
    return SolveChainInputs(
        car=car,
        surface=object(),
        track=track,
        measured=None,
        driver=None,
        diagnosis=None,
        current_setup=None,
        target_balance=44.0,
        fuel_load_l=80.0,
        wing_angle=0.0,
    )


def _stub_rake_solve(monkeypatch):
    """Replace RakeSolver.solve so Step 1 returns synthetic dynamic RHs.

    The rake solver itself dereferences `car.heave_spring.front_m_eff_kg`
    on GT3 (out of scope for W2.1; covered in W2.2 audit findings R-1..R-4).
    Monkeypatching the method keeps these tests focused on the W2.1 fix.
    """
    from solver.rake_solver import RakeSolver

    def _solve(self, **kwargs):
        return _StubStep1()
    monkeypatch.setattr(RakeSolver, "solve", _solve)


class _StubSol:
    """Generic stub solution that swallows attribute access.

    Returns 0.0 for unknown numeric attributes and a chainable stub for
    object-typed children. We only need a few attributes for the chain
    to thread through W2.1's dispatch path; the actual physics is
    irrelevant to these tests.
    """

    def __init__(self, **fields):
        self.__dict__.update(fields)

    def __getattr__(self, name):
        # Default to 0.0 for any field the chain reads but we didn't set.
        # Returning a permissive default keeps the tests focused on the
        # Step-2 dispatch contract rather than every downstream solver.
        return 0.0


def _stub_corner_solve(monkeypatch):
    """Stub CornerSpringSolver.solve to bypass W2.3 corner-coil branch.

    With step2 = HeaveSolution.null(), corner_spring_solver receives 0.0
    for front_heave_nmm/rear_third_nmm — that path produces nonsense
    numbers until W2.3 lands. Stubbing keeps these tests focused on
    Step-2 dispatch.
    """
    from solver.corner_spring_solver import CornerSpringSolver

    def _solve(self, front_heave_nmm, rear_third_nmm, **kwargs):
        return _StubSol(
            front_torsion_od_mm=0.0,
            front_wheel_rate_nmm=200.0,
            rear_spring_rate_nmm=180.0,
            rear_motion_ratio=1.0,
            front_roll_spring_nmm=0.0,
            front_natural_freq_hz=2.5,
            rear_natural_freq_hz=2.5,
            front_freq_isolation_ratio=3.0,
            rear_freq_isolation_ratio=3.0,
            front_heave_corner_ratio=2.0,
            rear_third_corner_ratio=2.0,
            total_front_heave_nmm=400.0,
            total_rear_heave_nmm=540.0,
            track_bump_freq_hz=4.0,
            front_mass_per_corner_kg=300.0,
            rear_mass_per_corner_kg=300.0,
            rear_spring_perch_mm=0.0,
            front_heave_mode_freq_hz=2.5,
            rear_heave_mode_freq_hz=2.5,
            constraints=[],
            rear_torsion_od_mm=None,
            front_torsion_bar_turns=0.0,
            rear_torsion_bar_turns=0.0,
            parameter_search_status={},
            parameter_search_evidence={},
            # property aliases used directly by the chain
            rear_wheel_rate_nmm=180.0,
        )
    monkeypatch.setattr(CornerSpringSolver, "solve", _solve)


def _stub_arb_solve(monkeypatch):
    from solver.arb_solver import ARBSolver

    def _solve(self, **kwargs):
        return _StubSol(
            front_arb_size="Medium",
            rear_arb_size="Medium",
            front_arb_blade_start=5,
            rear_arb_blade_start=5,
            k_roll_front_total=1000.0,
            k_roll_rear_total=900.0,
            lltd_target=0.50,
            lltd_achieved=0.50,
            lltd_error=0.0,
            rarb_blade_slow_corner=5,
            rarb_blade_fast_corner=5,
            farb_blade_locked=5,
            rarb_sensitivity_per_blade=0.005,
        )
    monkeypatch.setattr(ARBSolver, "solve", _solve)


def _stub_geom_solve(monkeypatch):
    from solver.wheel_geometry_solver import WheelGeometrySolver

    def _solve(self, **kwargs):
        return _StubSol(
            front_camber_deg=-3.0,
            rear_camber_deg=-2.0,
            front_toe_mm=0.0,
            rear_toe_mm=0.5,
            front_camber_dynamic_deg=-2.5,
            rear_camber_dynamic_deg=-1.5,
            front_camber_target_deg=-3.0,
            rear_camber_target_deg=-2.0,
            roll_at_lateral_g=2.0,
        )
    monkeypatch.setattr(WheelGeometrySolver, "solve", _solve)


def _stub_damper_solve(monkeypatch):
    """Make DamperSolver.solve raise — the chain catches ValueError and
    sets step6 = None, which is fine for our dispatch-contract tests."""
    from solver.damper_solver import DamperSolver

    def _solve(self, **kwargs):
        raise ValueError("damper stubbed for W2.1 dispatch test")
    monkeypatch.setattr(DamperSolver, "solve", _solve)


def _stub_reconcile_rh(monkeypatch):
    """Stub `reconcile_ride_heights` — its garage-model path also touches
    car.heave_spring on GT3."""
    import solver.solve_chain as sc

    def _noop(*args, **kwargs):
        return None
    monkeypatch.setattr(sc, "reconcile_ride_heights", _noop)


# ─── W2.1 contract tests ─────────────────────────────────────────────────


class TestSequentialSolverGT3Dispatch:
    """Finding 1: `_run_sequential_solver` substitutes HeaveSolution.null()
    for cars without heave/third architecture."""

    def test_gt3_emits_null_step2(self, monkeypatch, stub_track):
        from solver.solve_chain import _run_sequential_solver

        _stub_rake_solve(monkeypatch)
        _stub_corner_solve(monkeypatch)
        _stub_arb_solve(monkeypatch)
        _stub_geom_solve(monkeypatch)
        _stub_damper_solve(monkeypatch)
        _stub_reconcile_rh(monkeypatch)

        inputs = _make_inputs(BMW_M4_GT3, stub_track)
        s1, s2, s3, s4, s5, s6, rwr = _run_sequential_solver(inputs)

        assert isinstance(s2, HeaveSolution)
        assert s2.present is False
        assert s2.front_heave_nmm == 0.0
        assert s2.rear_third_nmm == 0.0

    def test_gt3_step2_propagates_step1_dynamic_rh(self, monkeypatch, stub_track):
        """The null factory must carry step1's dynamic RH targets so
        downstream solvers can still read them off step2."""
        from solver.solve_chain import _run_sequential_solver

        _stub_rake_solve(monkeypatch)
        _stub_corner_solve(monkeypatch)
        _stub_arb_solve(monkeypatch)
        _stub_geom_solve(monkeypatch)
        _stub_damper_solve(monkeypatch)
        _stub_reconcile_rh(monkeypatch)

        inputs = _make_inputs(BMW_M4_GT3, stub_track)
        s1, s2, _, _, _, _, _ = _run_sequential_solver(inputs)

        assert s2.front_dynamic_rh_mm == s1.dynamic_front_rh_mm
        assert s2.rear_dynamic_rh_mm == s1.dynamic_rear_rh_mm
        assert s2.front_dynamic_rh_mm == 22.0
        assert s2.rear_dynamic_rh_mm == 60.0

    def test_gt3_step2_binding_constraints_marked_not_applicable(
        self, monkeypatch, stub_track,
    ):
        from solver.solve_chain import _run_sequential_solver

        _stub_rake_solve(monkeypatch)
        _stub_corner_solve(monkeypatch)
        _stub_arb_solve(monkeypatch)
        _stub_geom_solve(monkeypatch)
        _stub_damper_solve(monkeypatch)
        _stub_reconcile_rh(monkeypatch)

        inputs = _make_inputs(BMW_M4_GT3, stub_track)
        _, s2, _, _, _, _, _ = _run_sequential_solver(inputs)

        assert s2.front_binding_constraint == "not_applicable"
        assert s2.rear_binding_constraint == "not_applicable"


class TestSequentialSolverGTPRegression:
    """Counter-test: GTP cars MUST still produce a present=True Step 2.

    This pins that the W2.1 dispatch is purely additive — a real
    HeaveSolver.solve() runs for BMW M Hybrid V8 just as before.
    """

    def test_gtp_step2_present_true(self, monkeypatch, gtp_track):
        from solver.heave_solver import HeaveSolution as _Sol
        from solver.solve_chain import _run_sequential_solver

        _stub_rake_solve(monkeypatch)
        _stub_corner_solve(monkeypatch)
        _stub_arb_solve(monkeypatch)
        _stub_geom_solve(monkeypatch)
        _stub_damper_solve(monkeypatch)
        _stub_reconcile_rh(monkeypatch)

        # Avoid driver-anchor / measured-σ branches by monkeypatching the
        # heave solver directly: assert it's invoked AND returns a real
        # solution by pre-canning the result (we don't want to validate
        # spring rate physics here, only the present=True flag).
        from solver.heave_solver import HeaveSolver

        called = {"solve": 0}
        def _real_solve(self, **kwargs):
            called["solve"] += 1
            return _Sol(
                front_heave_nmm=180.0,
                rear_third_nmm=400.0,
                front_dynamic_rh_mm=kwargs["dynamic_front_rh_mm"],
                front_shock_vel_p99_mps=0.5,
                front_excursion_at_rate_mm=4.0,
                front_bottoming_margin_mm=2.0,
                front_sigma_at_rate_mm=1.0,
                front_binding_constraint="bottoming",
                rear_dynamic_rh_mm=kwargs["dynamic_rear_rh_mm"],
                rear_shock_vel_p99_mps=0.6,
                rear_excursion_at_rate_mm=5.0,
                rear_bottoming_margin_mm=3.0,
                rear_sigma_at_rate_mm=1.2,
                rear_binding_constraint="bottoming",
                perch_offset_front_mm=-13.0,
                perch_offset_rear_mm=42.0,
            )
        monkeypatch.setattr(HeaveSolver, "solve", _real_solve)
        # reconcile_solution must also be stubbed because it touches the
        # garage model.
        monkeypatch.setattr(HeaveSolver, "reconcile_solution", lambda self, *a, **kw: None)

        inputs = _make_inputs(BMW_M_HYBRID_V8, gtp_track)
        _, s2, _, _, _, _, _ = _run_sequential_solver(inputs)

        assert s2.present is True
        assert s2.front_heave_nmm == 180.0
        assert s2.rear_third_nmm == 400.0
        assert called["solve"] == 1


class TestFullSetupOptimizerGT3Constructor:
    """Finding (W2.1 spec): FullSetupOptimizer.__init__ must NOT crash on GT3.

    Pre-fix: `HeaveSolver(car, track)` in __init__ raised because
    `car.suspension_arch.has_heave_third` is False. Post-fix: the field
    is None on GT3 cars and method call sites guard with `is not None`.
    """

    def test_gt3_constructor_does_not_raise(self, monkeypatch, stub_track):
        """The optimizer is BMW/Sebring-only at runtime, but the
        constructor must be safe to invoke for any car so that future
        callers can probe support without exceptions."""
        from solver.full_setup_optimizer import BMWSebringOptimizer

        # Bypass the garage-model gate (which is unrelated to W2.1) by
        # injecting a sentinel — we only want to verify that the heave
        # constructor branch is the W2.1 fix.
        def _fake_garage_model(self, *args, **kwargs):
            return object()  # truthy sentinel
        monkeypatch.setattr(
            BMW_M4_GT3.__class__, "active_garage_output_model", _fake_garage_model,
        )

        opt = BMWSebringOptimizer(BMW_M4_GT3, surface=object(), track=stub_track)
        assert opt.heave_solver is None  # GT3: no heave solver

    def test_gtp_constructor_initialises_heave_solver(
        self, monkeypatch, gtp_track,
    ):
        """Counter-test: GTP cars still get a real HeaveSolver."""
        from solver.full_setup_optimizer import BMWSebringOptimizer

        def _fake_garage_model(self, *args, **kwargs):
            return object()
        monkeypatch.setattr(
            BMW_M_HYBRID_V8.__class__, "active_garage_output_model", _fake_garage_model,
        )

        opt = BMWSebringOptimizer(BMW_M_HYBRID_V8, surface=object(), track=gtp_track)
        assert opt.heave_solver is not None


class TestReconcileSolutionGuardsNullStep2:
    """Defense-in-depth: heave_solver.reconcile_solution(step2_null, ...)
    must return early even if a caller forgets the present-check guard.

    This pins the early-return behaviour added in heave_solver.py."""

    def test_reconcile_returns_immediately_on_null_step2(self):
        from solver.heave_solver import HeaveSolver
        from track_model.profile import TrackProfile

        track = TrackProfile(
            track_name="Sebring",
            track_config="",
            track_length_m=6019.0,
            car="bmw",
            best_lap_time_s=110.0,
        )
        # GTP solver — constructs OK
        solver = HeaveSolver(BMW_M_HYBRID_V8, track)

        null_step2 = HeaveSolution.null(
            front_dynamic_rh_mm=22.0,
            rear_dynamic_rh_mm=60.0,
        )

        # Call reconcile_solution with step1=None and step3=None — would
        # crash on attribute access if the early-return guard didn't fire.
        solver.reconcile_solution(
            step1=None,
            step2=null_step2,
            step3=None,
            fuel_load_l=80.0,
            verbose=False,
        )
        # If we reach here without exception, the guard worked.
        assert null_step2.present is False
