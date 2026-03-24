import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from learner.knowledge_store import KnowledgeStore
from webapp.services import IOptimalWebService
from webapp.settings import AppSettings
from webapp.types import RunCreateRequest


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


class WebAppServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.settings = AppSettings.from_env(self.tempdir.name)
        self.settings.ensure_directories()
        self.service = IOptimalWebService(self.settings)

    def test_single_session_adapter_returns_normalized_view(self) -> None:
        ibt_path = Path(self.tempdir.name) / "session.ibt"
        ibt_path.write_bytes(b"ibt")

        def fake_produce_result(args, emit_report=False, compact_report=False):
            Path(args.json).write_text("{}")
            Path(args.sto).write_text("sto")
            return {
                "car": _ns(name="BMW M Hybrid V8"),
                "track": _ns(track_name="Sebring International Raceway"),
                "report": "solver report",
                "lap_time_s": 100.123,
                "lap_number": 11,
                "measured": _ns(
                    lap_time_s=100.123,
                    bottoming_event_count_front=2,
                    rear_rh_std_mm=3.4,
                    front_rh_excursion_measured_mm=12.2,
                    pitch_range_braking_deg=1.3,
                    front_braking_lock_ratio_p95=0.08,
                    rear_power_slip_ratio_p95=0.11,
                    understeer_low_speed_deg=0.4,
                    understeer_high_speed_deg=0.6,
                    front_pressure_mean_kpa=165.2,
                    rear_pressure_mean_kpa=166.1,
                ),
                "diagnosis": _ns(
                    assessment="compromised",
                    problems=[
                        _ns(severity="critical", symptom="Bottoming on braking", cause="Front platform too soft", speed_context="braking"),
                    ],
                ),
                "current_setup": _ns(
                    wing_angle_deg=17.0,
                    front_pushrod_mm=-25.0,
                    rear_pushrod_mm=-18.0,
                    static_rear_rh_mm=48.0,
                    front_heave_nmm=40.0,
                    rear_third_nmm=380.0,
                    front_torsion_od_mm=14.3,
                    rear_spring_nmm=180.0,
                    front_arb_blade=1,
                    rear_arb_blade=2,
                    brake_bias_pct=46.0,
                    diff_preload_nm=20.0,
                    front_camber_deg=-2.8,
                    rear_camber_deg=-1.8,
                    front_toe_mm=-0.4,
                    rear_toe_mm=0.1,
                    front_ls_comp=8,
                    front_ls_rbd=7,
                    front_hs_comp=6,
                    front_hs_rbd=8,
                    rear_ls_comp=7,
                    rear_ls_rbd=6,
                    rear_hs_comp=5,
                    rear_hs_rbd=8,
                    tc_gain=4,
                    tc_slip=4,
                ),
                "wing": 17.0,
                "fuel_l": 89.0,
                "step1": _ns(front_pushrod_offset_mm=-24.0, rear_pushrod_offset_mm=-17.0, static_rear_rh_mm=49.0),
                "step2": _ns(front_heave_nmm=45.0, rear_third_nmm=410.0, bottoming_events_front=0),
                "step3": _ns(front_torsion_od_mm=14.6, rear_spring_rate_nmm=170.0),
                "step4": _ns(front_arb_blade_start=2, rear_arb_blade_start=3),
                "step5": _ns(front_camber_deg=-2.5, rear_camber_deg=-1.6, front_toe_mm=-0.2, rear_toe_mm=0.0),
                "step6": _ns(
                    lf=_ns(ls_comp=9, ls_rbd=8, hs_comp=7, hs_rbd=9),
                    lr=_ns(ls_comp=6, ls_rbd=5, hs_comp=4, hs_rbd=7),
                ),
                "supporting": _ns(brake_bias_pct=46.2, diff_preload_nm=18.0, tc_gain=3, tc_slip=5),
                "selected_candidate_family": "compromise",
                "selected_candidate_score": 0.71,
                "solver_notes": ["Using compromise family after telemetry veto."],
                "legal_validation": _ns(issues=[]),
            }

        predicted = _ns(
            rear_rh_std_mm=2.8,
            front_excursion_mm=10.5,
            braking_pitch_deg=1.0,
            front_lock_p95=0.06,
            rear_power_slip_p95=0.09,
            understeer_low_deg=0.3,
            understeer_high_deg=0.4,
            front_pressure_hot_kpa=166.0,
            rear_pressure_hot_kpa=167.0,
        )

        with patch("pipeline.produce.produce_result", side_effect=fake_produce_result), patch(
            "webapp.services.predict_candidate_telemetry",
            return_value=(predicted, _ns(overall=0.63)),
        ):
            kind, payload, artifacts = self.service.execute_run(
                "run1",
                RunCreateRequest(mode="single_session", car="bmw", ibt_paths=[ibt_path], use_learning=False),
                lambda phase: None,
            )

        self.assertEqual(kind, "single_session")
        self.assertEqual(payload["result_kind"], "single_session")
        self.assertEqual(payload["assessment"], "Compromised")
        self.assertEqual(payload["confidence_label"], "Medium")
        self.assertTrue(any(row["label"] == "Rear ride height" for group in payload["setup_groups"] for row in group["rows"]))
        self.assertEqual({artifact.kind for artifact in artifacts}, {"report", "json", "sto"})

    def test_track_solve_adapter_handles_report_and_json_artifacts(self) -> None:
        def fake_run_solver(args):
            Path(args.save).write_text(json.dumps({
                "step1_rake": {"front_pushrod_offset_mm": -24.0, "rear_pushrod_offset_mm": -18.0, "static_rear_rh_mm": 50.0},
                "step2_heave": {"front_heave_nmm": 45.0, "rear_third_nmm": 400.0},
                "step3_corner": {"front_torsion_od_mm": 14.5, "rear_spring_rate_nmm": 170.0},
                "step4_arb": {"front_arb_blade_start": 2, "rear_arb_blade_start": 3},
                "step5_geometry": {"front_camber_deg": -2.5, "rear_camber_deg": -1.7, "front_toe_mm": -0.2, "rear_toe_mm": 0.0},
                "step6_dampers": {"lf": {"ls_comp": 9, "ls_rbd": 8, "hs_comp": 7, "hs_rbd": 9}, "lr": {"ls_comp": 6, "ls_rbd": 5, "hs_comp": 4, "hs_rbd": 7}},
            }))
            Path(args.sto).write_text("sto")
            print("track solve report")

        with patch("webapp.services.run_solver", side_effect=fake_run_solver):
            kind, payload, artifacts = self.service.execute_run(
                "run2",
                RunCreateRequest(mode="track_solve", car="bmw", track="sebring", wing=17.0, use_learning=False),
                lambda phase: None,
            )

        self.assertEqual(kind, "track_solve")
        self.assertEqual(payload["confidence_label"], "Telemetry-free")
        self.assertEqual(len(artifacts), 3)

    def test_comparison_adapter_returns_rankings_and_synthesis(self) -> None:
        file_a = Path(self.tempdir.name) / "a.ibt"
        file_b = Path(self.tempdir.name) / "b.ibt"
        file_a.write_bytes(b"a")
        file_b.write_bytes(b"b")

        session_a = _ns(label="S1", ibt_path=str(file_a), lap_time_s=99.9, track_name="Sebring", wing_angle=17.0)
        session_b = _ns(label="S2", ibt_path=str(file_b), lap_time_s=100.3, track_name="Sebring", wing_angle=17.0)
        comparison = _ns(
            setup_deltas={"Rear ARB": [2, 3]},
            telemetry_deltas={"Lap Time": [99.9, 100.3]},
            corner_comparisons=[_ns(corner_id=1, direction="left", speed_class="high", per_session=[_ns(delta_to_min_time_s=0.01), _ns(delta_to_min_time_s=0.08)])],
        )
        scoring = _ns(scores=[
            _ns(session=session_a, overall_score=0.81, strengths=["stable entry"], weaknesses=["exit traction"], rank=1),
            _ns(session=session_b, overall_score=0.68, strengths=["rotation"], weaknesses=["platform"], rank=2),
        ])
        synthesis = _ns(
            wing_angle=17.0,
            fuel_l=89.0,
            solve_basis="latest_validation_veto",
            solver_notes=["Rematerialized compromise candidate."],
            step1=_ns(front_pushrod_offset_mm=-24.0, rear_pushrod_offset_mm=-17.0, static_rear_rh_mm=49.0),
            step2=_ns(front_heave_nmm=45.0, rear_third_nmm=410.0),
            step3=_ns(front_torsion_od_mm=14.6, rear_spring_rate_nmm=170.0),
            step4=_ns(front_arb_blade_start=2, rear_arb_blade_start=3),
            step5=_ns(front_camber_deg=-2.5, rear_camber_deg=-1.6, front_toe_mm=-0.2, rear_toe_mm=0.0),
            step6=_ns(lf=_ns(ls_comp=9, ls_rbd=8, hs_comp=7, hs_rbd=9), lr=_ns(ls_comp=6, ls_rbd=5, hs_comp=4, hs_rbd=7)),
            supporting=_ns(brake_bias_pct=46.2, diff_preload_nm=18.0, tc_gain=3, tc_slip=5, tyre_cold_fl_kpa=152.0, diff_ramp_coast=45, diff_ramp_drive=60, diff_clutch_plates=6),
        )

        def fake_save_comparison_json(_comparison, _scoring, _synthesis, output_path):
            Path(output_path).write_text("{}")

        def fake_write_sto(**kwargs):
            Path(kwargs["output_path"]).write_text("sto")
            return kwargs["output_path"]

        with patch("webapp.services.get_car", return_value=_ns(name="BMW M Hybrid V8")), patch(
            "webapp.services.analyze_session",
            side_effect=[session_a, session_b],
        ), patch("webapp.services.compare_sessions", return_value=comparison), patch(
            "webapp.services.score_sessions",
            return_value=scoring,
        ), patch("webapp.services.synthesize_setup", return_value=synthesis), patch(
            "webapp.services.format_comparison_report",
            return_value="comparison report",
        ), patch("webapp.services.save_comparison_json", side_effect=fake_save_comparison_json), patch(
            "webapp.services.write_sto",
            side_effect=fake_write_sto,
        ):
            kind, payload, artifacts = self.service.execute_run(
                "run3",
                RunCreateRequest(mode="comparison", car="bmw", ibt_paths=[file_a, file_b]),
                lambda phase: None,
            )

        self.assertEqual(kind, "comparison")
        self.assertEqual(payload["winner_label"], "S1")
        self.assertTrue(payload["synthesis_groups"])
        self.assertEqual({artifact.kind for artifact in artifacts}, {"report", "json", "sto"})

    def test_knowledge_summary_adapter_reads_bucketed_learnings(self) -> None:
        learn_dir = Path(self.tempdir.name) / "learn"
        store = KnowledgeStore(base_dir=learn_dir)
        idx = store.load_index()
        idx["total_observations"] = 1
        idx["total_deltas"] = 2
        idx["cars_seen"] = ["bmw"]
        idx["tracks_seen"] = ["Sebring International Raceway"]
        store.save_index(idx)
        store.save_observation("bmw_sebring_s1", {"session_id": "bmw_sebring_s1", "car": "bmw", "track": "Sebring International Raceway"})
        (learn_dir / "insights" / "bmw_sebring_insights.json").write_text(json.dumps({"key_insights": ["Rear third spring helped platform control."]}))
        (learn_dir / "models" / "bmw_sebring_empirical.json").write_text(json.dumps({"corrections": {"rear_third_nmm": 12.0}}))

        with patch("webapp.services.KnowledgeStore", return_value=store):
            summary = self.service.load_knowledge_summary()

        self.assertEqual(summary.total_observations, 1)
        self.assertEqual(summary.buckets[0].track, "Sebring International Raceway")
        self.assertTrue(summary.buckets[0].corrections)

    def test_knowledge_summary_uses_full_track_slug(self) -> None:
        learn_dir = Path(self.tempdir.name) / "learn-full-slug"
        store = KnowledgeStore(base_dir=learn_dir)
        store.save_observation(
            "bmw_road_atlanta_s1",
            {"session_id": "bmw_road_atlanta_s1", "car": "bmw", "track": "Road Atlanta"},
        )
        (learn_dir / "insights" / "bmw_road_atlanta_insights.json").write_text(
            json.dumps({"key_insights": ["Full slug lookup works."]})
        )
        (learn_dir / "models" / "bmw_road_atlanta_empirical.json").write_text(
            json.dumps({"corrections": {"rear_third_nmm": 8.0}})
        )

        with patch("webapp.services.KnowledgeStore", return_value=store):
            summary = self.service.load_knowledge_summary()

        self.assertEqual(summary.buckets[0].track, "Road Atlanta")
        self.assertEqual(summary.buckets[0].insights, ["Full slug lookup works."])


if __name__ == "__main__":
    unittest.main()
