import contextlib
import io
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from analyzer.setup_reader import CurrentSetup
from car_model.cars import get_car
from pipeline.reason import (
    PhysicsReasoning,
    ReasoningState,
    SpeedRegimeAnalysis,
    _build_validation_clusters,
    _reason_to_modifiers,
    _run_physics_validations,
    _score_categories,
    _selected_candidate_result,
    reason_and_solve,
)
from solver.setup_fingerprint import (
    fingerprint_from_current_setup,
    fingerprint_from_solver_steps,
    match_failed_cluster,
)
from solver.candidate_search import SetupCandidate
from track_model.ibt_parser import IBTFile


ROOT = Path(__file__).resolve().parents[1]
BMW_FILES = [
    ROOT / "ibtfiles" / "bmwtf.ibt",
    ROOT / "ibtfiles" / "bmw151.ibt",
    ROOT / "ibtfiles" / "bmw170.ibt",
    ROOT / "ibtfiles" / "bmwtry.ibt",
]


def _measured(**overrides):
    values = {
        "understeer_mean_deg": 0.0,
        "understeer_low_speed_deg": 0.0,
        "understeer_high_speed_deg": 0.0,
        "body_slip_p95_deg": 1.5,
        "rear_slip_ratio_p95": 0.05,
        "rear_power_slip_ratio_p95": 0.05,
        "peak_lat_g_measured": 1.9,
        "speed_max_kph": 300.0,
        "front_rh_std_mm": 4.0,
        "rear_rh_std_mm": 4.0,
        "front_rh_settle_time_ms": 125.0,
        "rear_rh_settle_time_ms": 125.0,
        "yaw_rate_correlation": 0.9,
        "front_temp_spread_lf_c": 0.0,
        "front_temp_spread_rf_c": 0.0,
        "rear_temp_spread_lr_c": 0.0,
        "rear_temp_spread_rr_c": 0.0,
        "front_carcass_mean_c": 92.5,
        "rear_carcass_mean_c": 92.5,
        "front_pressure_mean_kpa": 165.0,
        "rear_pressure_mean_kpa": 165.0,
        "bottoming_event_count_front": 0,
        "bottoming_event_count_rear": 0,
        "bottoming_event_count_front_clean": 0,
        "bottoming_event_count_rear_clean": 0,
        "bottoming_event_count_front_kerb": 0,
        "bottoming_event_count_rear_kerb": 0,
        "front_shock_vel_p99_mps": 0.2,
        "rear_shock_vel_p99_mps": 0.2,
        "front_dominant_freq_hz": 2.2,
        "rear_dominant_freq_hz": 2.4,
        "front_heave_defl_p99_mm": 0.0,
        "rear_heave_defl_p99_mm": 0.0,
        "front_heave_travel_used_pct": 0.0,
        "rear_heave_travel_used_pct": 0.0,
        "mean_front_rh_at_speed_mm": 20.0,
        "mean_rear_rh_at_speed_mm": 40.0,
        "rear_shock_oscillation_hz": 0.0,
        "front_shock_oscillation_hz": 0.0,
        "air_temp_c": 25.0,
        "track_temp_c": 35.0,
        "wind_speed_ms": 2.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _setup(**overrides):
    values = {
        "wing_angle_deg": 17.0,
        "fuel_l": 89.0,
        "front_pushrod_mm": -26.5,
        "rear_pushrod_mm": -24.0,
        "front_heave_nmm": 30.0,
        "front_heave_perch_mm": -14.0,
        "rear_third_nmm": 440.0,
        "rear_third_perch_mm": 42.0,
        "front_torsion_od_mm": 13.9,
        "rear_spring_nmm": 150.0,
        "rear_spring_perch_mm": 30.0,
        "front_arb_size": "Soft",
        "front_arb_blade": 1,
        "rear_arb_size": "Soft",
        "rear_arb_blade": 3,
        "front_camber_deg": -1.8,
        "rear_camber_deg": -1.5,
        "front_toe_mm": -0.4,
        "rear_toe_mm": 0.0,
        "front_ls_comp": 7,
        "front_ls_rbd": 6,
        "front_hs_comp": 4,
        "front_hs_rbd": 8,
        "front_hs_slope": 11,
        "rear_ls_comp": 6,
        "rear_ls_rbd": 7,
        "rear_hs_comp": 4,
        "rear_hs_rbd": 11,
        "rear_hs_slope": 11,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _session(label, lap_time_s, measured=None, setup=None):
    return SimpleNamespace(
        label=label,
        lap_time_s=lap_time_s,
        measured=measured or _measured(),
        setup=setup or _setup(),
        diagnosis=SimpleNamespace(problems=[]),
        driver=SimpleNamespace(style="smooth"),
    )


def _solver_fp_from_json(data: dict):
    step6 = data["step6_dampers"]
    return fingerprint_from_solver_steps(
        wing=17.0,
        fuel_l=89.0,
        step1=SimpleNamespace(**data["step1_rake"]),
        step2=SimpleNamespace(**data["step2_heave"]),
        step3=SimpleNamespace(**data["step3_corner"]),
        step4=SimpleNamespace(**data["step4_arb"]),
        step5=SimpleNamespace(**data["step5_geometry"]),
        step6=SimpleNamespace(
            lf=SimpleNamespace(**step6["lf"]),
            rf=SimpleNamespace(**step6["rf"]),
            lr=SimpleNamespace(**step6["lr"]),
            rr=SimpleNamespace(**step6["rr"]),
        ),
    )


@unittest.skipUnless(
    all(path.exists() for path in BMW_FILES) and importlib.util.find_spec("scipy") is not None,
    "BMW IBT fixtures or scipy not available",
)
class ReasoningVetoIntegrationTests(unittest.TestCase):
    def test_bmw_validation_run_becomes_authority_and_changes_output(self):
        with tempfile.TemporaryDirectory() as td:
            json_path = Path(td) / "reason.json"
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                reason_and_solve(
                    car_name="bmw",
                    ibt_paths=[str(path) for path in BMW_FILES],
                    wing=17.0,
                    json_path=str(json_path),
                    verbose=False,
                )

            data = json.loads(json_path.read_text())
            failed_clusters = [c for c in data["validation_clusters"] if c["validated_failed"]]
            s4_fp = fingerprint_from_current_setup(CurrentSetup.from_ibt(IBTFile(BMW_FILES[-1])))
            output_fp = _solver_fp_from_json(data)

            self.assertEqual(data["solve_basis"], "latest_validation_veto")
            self.assertEqual(data["authority_session"], "S4")
            self.assertTrue(failed_clusters)
            self.assertEqual(failed_clusters[0]["latest_session_label"], data["authority_session"])
            self.assertFalse(output_fp.matches_candidate(s4_fp))
            self.assertIn("solve authority", "\n".join(data["solver_notes"]).lower())
            self.assertFalse(
                any("Authority score selected" in note for note in data["solver_notes"]),
                "final solve notes should not retain the pre-veto authority choice",
            )
            self.assertFalse(
                {
                    "Selected BMW/Sebring constrained optimizer candidate.",
                    "Selected constrained optimizer candidate.",
                }.issubset(set(data["solver_notes"])),
                "final solve notes should not duplicate optimizer-selection messages",
            )
            self.assertIn("SOLVE CONTEXT", captured.getvalue())
            self.assertIn("veto", captured.getvalue().lower())


class ReasoningVetoUnitTests(unittest.TestCase):
    def test_selected_candidate_result_uses_rematerialized_result(self):
        rematerialized = SimpleNamespace(
            step1=SimpleNamespace(front_pushrod_offset_mm=-30.0),
            step2=SimpleNamespace(front_heave_nmm=60.0),
            step3=SimpleNamespace(front_torsion_od_mm=14.2),
            step4=SimpleNamespace(front_arb_blade_start=2),
            step5=SimpleNamespace(front_camber_deg=-3.2),
            step6=SimpleNamespace(lf=SimpleNamespace(ls_comp=9)),
            supporting=SimpleNamespace(brake_bias_pct=47.0),
            legal_validation=SimpleNamespace(valid=True),
            decision_trace=["rematerialized"],
        )
        selected = SetupCandidate(
            family="baseline_reset",
            description="selected",
            step1=SimpleNamespace(front_pushrod_offset_mm=-26.0),
            step2=SimpleNamespace(front_heave_nmm=55.0),
            step3=SimpleNamespace(front_torsion_od_mm=13.9),
            step4=SimpleNamespace(front_arb_blade_start=1),
            step5=SimpleNamespace(front_camber_deg=-2.9),
            step6=SimpleNamespace(lf=SimpleNamespace(ls_comp=7)),
            supporting=SimpleNamespace(brake_bias_pct=46.0),
            result=rematerialized,
            selectable=True,
            selected=True,
        )
        applied = _selected_candidate_result(selected)

        self.assertIs(applied, rematerialized)
        self.assertEqual(applied.step1.front_pushrod_offset_mm, -30.0)
        self.assertEqual(applied.step2.front_heave_nmm, 60.0)
        self.assertEqual(applied.supporting.brake_bias_pct, 47.0)

    def test_physics_validation_uses_split_bottoming_fields(self):
        state = ReasoningState(
            sessions=[
                _session(
                    "S1",
                    100.0,
                    measured=_measured(
                        understeer_mean_deg=1.8,
                        front_temp_spread_lf_c=0.0,
                        front_temp_spread_rf_c=0.0,
                        bottoming_event_count_front_kerb=4,
                        bottoming_event_count_front_clean=1,
                        bottoming_event_count_rear_kerb=3,
                        bottoming_event_count_rear_clean=0,
                    ),
                ),
                _session(
                    "S2",
                    100.1,
                    measured=_measured(
                        understeer_mean_deg=1.7,
                        front_temp_spread_lf_c=0.0,
                        front_temp_spread_rf_c=0.0,
                        bottoming_event_count_front_kerb=5,
                        bottoming_event_count_front_clean=1,
                        bottoming_event_count_rear_kerb=4,
                        bottoming_event_count_rear_clean=0,
                    ),
                ),
            ],
            physics=PhysicsReasoning(),
            speed_regime=SpeedRegimeAnalysis(understeer_gradient=0.0),
        )
        state.best_session_idx = 0

        _run_physics_validations(state)

        self.assertTrue(state.physics.validations)
        self.assertTrue(any("kerb-induced" in v.hypothesis.lower() for v in state.physics.validations))

    def test_modifier_cross_check_reads_oscillation_hz_field(self):
        car = get_car("bmw")
        state = ReasoningState(
            sessions=[
                _session(
                    "S1",
                    100.0,
                    measured=_measured(
                        front_rh_settle_time_ms=40.0,
                        rear_shock_oscillation_hz=9.5,
                    ),
                )
            ],
            physics=PhysicsReasoning(weakest_category="damper_platform"),
            speed_regime=SpeedRegimeAnalysis(dominant_regime="balanced"),
        )
        state.best_session_idx = 0
        state.authority_session_idx = 0

        mods, reasons = _reason_to_modifiers(state, car)

        self.assertEqual(mods.damping_ratio_scale, 1.0)
        self.assertTrue(any("Rear oscillation" in reason for reason in reasons))

    def test_modifier_cross_check_reads_front_oscillation_hz_field(self):
        car = get_car("bmw")
        state = ReasoningState(
            sessions=[
                _session(
                    "S1",
                    100.0,
                    measured=_measured(
                        front_rh_settle_time_ms=40.0,
                        front_shock_oscillation_hz=9.2,
                    ),
                )
            ],
            physics=PhysicsReasoning(weakest_category="damper_platform"),
            speed_regime=SpeedRegimeAnalysis(dominant_regime="balanced"),
        )
        state.best_session_idx = 0
        state.authority_session_idx = 0

        mods, reasons = _reason_to_modifiers(state, car)

        self.assertEqual(mods.damping_ratio_scale, 1.0)
        self.assertTrue(any("Front oscillation" in reason for reason in reasons))

    def test_category_scores_use_authority_session(self):
        session_best = _session(
            "S1",
            100.0,
            measured=_measured(
                front_rh_settle_time_ms=125.0,
                yaw_rate_correlation=0.95,
                front_rh_std_mm=4.0,
                rear_rh_std_mm=4.0,
                speed_max_kph=300.0,
            ),
        )
        session_authority = _session(
            "S2",
            100.0,
            measured=_measured(
                front_rh_settle_time_ms=320.0,
                yaw_rate_correlation=0.10,
                front_rh_std_mm=4.0,
                rear_rh_std_mm=4.0,
                speed_max_kph=300.0,
            ),
        )
        state = ReasoningState(
            sessions=[session_best, session_authority],
            physics=PhysicsReasoning(),
        )
        state.best_session_idx = 0
        state.authority_session_idx = 1
        state.solve_basis = "latest_validation_veto"

        _score_categories(state)

        self.assertLess(state.physics.category_scores["damper_platform"], 0.5)
        self.assertEqual(state.physics.weakest_category, "damper_platform")

    def test_failed_cluster_does_not_match_unrelated_candidate(self):
        best_setup = _setup(front_heave_nmm=30.0, rear_third_nmm=380.0)
        failed_setup = _setup(front_heave_nmm=35.0, rear_third_nmm=500.0)
        state = ReasoningState(
            sessions=[
                _session("S1", 100.0, measured=_measured(), setup=best_setup),
                _session(
                    "S2",
                    100.4,
                    measured=_measured(
                        understeer_mean_deg=0.12,
                        bottoming_event_count_rear_clean=4,
                    ),
                    setup=failed_setup,
                ),
            ],
        )
        state.setup_fingerprints = [
            fingerprint_from_current_setup(best_setup),
            fingerprint_from_current_setup(failed_setup),
        ]

        _build_validation_clusters(state)
        failed_clusters = [c for c in state.validation_clusters if c.validated_failed]

        self.assertTrue(failed_clusters)
        self.assertIsNone(match_failed_cluster(state.setup_fingerprints[0], failed_clusters))

    def test_validation_clusters_group_single_click_damper_reruns(self):
        baseline_setup = _setup()
        rerun_setup = _setup(front_ls_comp=8)
        alternate_setup = _setup(front_heave_nmm=35.0, rear_third_nmm=500.0)
        state = ReasoningState(
            sessions=[
                _session("S1", 100.0, measured=_measured(), setup=baseline_setup),
                _session(
                    "S2",
                    100.3,
                    measured=_measured(
                        understeer_low_speed_deg=0.35,
                        rear_power_slip_ratio_p95=0.07,
                    ),
                    setup=rerun_setup,
                ),
                _session("S3", 99.9, measured=_measured(), setup=alternate_setup),
            ],
        )
        state.setup_fingerprints = [
            fingerprint_from_current_setup(baseline_setup),
            fingerprint_from_current_setup(rerun_setup),
            fingerprint_from_current_setup(alternate_setup),
        ]

        _build_validation_clusters(state)

        self.assertEqual(len(state.validation_clusters), 2)
        failed_clusters = [c for c in state.validation_clusters if c.validated_failed]
        self.assertEqual(len(failed_clusters), 1)
        self.assertEqual(failed_clusters[0].session_labels, ["S1", "S2"])
