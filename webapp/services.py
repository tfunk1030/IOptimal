"""Domain adapters and view-model builders for the IOptimal web app."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from car_model.cars import get_car
from comparison.compare import analyze_session, compare_sessions
from comparison.report import format_comparison_report, save_comparison_json
from comparison.score import score_sessions
from comparison.synthesize import SynthesisResult, synthesize_setup
from learner.knowledge_store import KnowledgeStore
from output.setup_writer import write_sto
from solver.predictor import predict_candidate_telemetry
from solver.solve import run_solver
from webapp.settings import AppSettings
from webapp.types import (
    ChangeView,
    ComparisonResultView,
    ComparisonTableRowView,
    CornerHighlightView,
    KnowledgeBucketView,
    KnowledgeSummaryView,
    MetricView,
    ProblemView,
    RankingView,
    RunCreateRequest,
    SessionResultView,
    SetupGroupView,
)


PhaseCallback = Callable[[str], None]


@dataclass(slots=True)
class GeneratedArtifact:
    kind: str
    label: str
    path: Path


@dataclass(frozen=True, slots=True)
class RowSpec:
    label: str
    current_paths: tuple[str, ...]
    recommended_paths: tuple[str, ...]
    units: str = ""
    digits: int = 1
    signed: bool = False


@dataclass(frozen=True, slots=True)
class GroupSpec:
    name: str
    help_text: str
    rows: tuple[RowSpec, ...]


SETUP_GROUP_SPECS: tuple[GroupSpec, ...] = (
    GroupSpec(
        "Platform",
        "Ride heights, springs, and pushrods that set the platform.",
        (
            RowSpec("Wing angle", ("current_setup.wing_angle_deg",), ("wing",), "deg", 0),
            RowSpec("Front pushrod", ("current_setup.front_pushrod_mm",), ("step1.front_pushrod_offset_mm",), "mm", 1, True),
            RowSpec("Rear pushrod", ("current_setup.rear_pushrod_mm",), ("step1.rear_pushrod_offset_mm",), "mm", 1, True),
            RowSpec("Rear ride height", ("current_setup.static_rear_rh_mm",), ("step1.static_rear_rh_mm",), "mm", 1),
            RowSpec("Front heave", ("current_setup.front_heave_nmm",), ("step2.front_heave_nmm",), "N/mm", 0),
            RowSpec("Rear third", ("current_setup.rear_third_nmm",), ("step2.rear_third_nmm",), "N/mm", 0),
            RowSpec("Front torsion", ("current_setup.front_torsion_od_mm",), ("step3.front_torsion_od_mm",), "mm", 2),
            RowSpec("Rear spring", ("current_setup.rear_spring_nmm",), ("step3.rear_spring_rate_nmm", "step3.rear_spring_nmm"), "N/mm", 0),
        ),
    ),
    GroupSpec(
        "Balance",
        "Roll balance, brake bias, and differential preload that tune rotation.",
        (
            RowSpec("Front ARB blade", ("current_setup.front_arb_blade",), ("step4.front_arb_blade_start", "step4.farb_blade_locked"), "", 0),
            RowSpec("Rear ARB blade", ("current_setup.rear_arb_blade",), ("step4.rear_arb_blade_start", "step4.rarb_blade_slow_corner"), "", 0),
            RowSpec("Brake bias", ("current_setup.brake_bias_pct",), ("supporting.brake_bias_pct",), "%", 1),
            RowSpec("Diff preload", ("current_setup.diff_preload_nm",), ("supporting.diff_preload_nm",), "Nm", 0),
        ),
    ),
    GroupSpec(
        "Geometry",
        "Camber and toe settings that control contact patch and turn-in shape.",
        (
            RowSpec("Front camber", ("current_setup.front_camber_deg",), ("step5.front_camber_deg",), "deg", 1, True),
            RowSpec("Rear camber", ("current_setup.rear_camber_deg",), ("step5.rear_camber_deg",), "deg", 1, True),
            RowSpec("Front toe", ("current_setup.front_toe_mm",), ("step5.front_toe_mm",), "mm", 1, True),
            RowSpec("Rear toe", ("current_setup.rear_toe_mm",), ("step5.rear_toe_mm",), "mm", 1, True),
        ),
    ),
    GroupSpec(
        "Dampers",
        "Low-speed and high-speed damping clicks grouped by axle.",
        (
            RowSpec("Front LS comp", ("current_setup.front_ls_comp",), ("step6.lf.ls_comp",), "click", 0),
            RowSpec("Front LS rebound", ("current_setup.front_ls_rbd",), ("step6.lf.ls_rbd",), "click", 0),
            RowSpec("Front HS comp", ("current_setup.front_hs_comp",), ("step6.lf.hs_comp",), "click", 0),
            RowSpec("Front HS rebound", ("current_setup.front_hs_rbd",), ("step6.lf.hs_rbd",), "click", 0),
            RowSpec("Rear LS comp", ("current_setup.rear_ls_comp",), ("step6.lr.ls_comp",), "click", 0),
            RowSpec("Rear LS rebound", ("current_setup.rear_ls_rbd",), ("step6.lr.ls_rbd",), "click", 0),
            RowSpec("Rear HS comp", ("current_setup.rear_hs_comp",), ("step6.lr.hs_comp",), "click", 0),
            RowSpec("Rear HS rebound", ("current_setup.rear_hs_rbd",), ("step6.lr.hs_rbd",), "click", 0),
        ),
    ),
    GroupSpec(
        "Driver Aids",
        "Drive-unit settings the driver will feel directly on throttle application.",
        (
            RowSpec("TC gain", ("current_setup.tc_gain",), ("supporting.tc_gain",), "", 0),
            RowSpec("TC slip", ("current_setup.tc_slip",), ("supporting.tc_slip",), "", 0),
        ),
    ),
)


class IOptimalWebService:
    """Thin service layer over the existing Python solver modules."""

    def __init__(self, settings: AppSettings):
        self.settings = settings

    def execute_run(
        self,
        run_id: str,
        request: RunCreateRequest,
        phase_callback: PhaseCallback,
    ) -> tuple[str, dict[str, Any], list[GeneratedArtifact]]:
        if request.mode == "single_session":
            return self._run_single_session(run_id, request, phase_callback)
        if request.mode == "comparison":
            return self._run_comparison(run_id, request, phase_callback)
        if request.mode == "track_solve":
            return self._run_track_solve(run_id, request, phase_callback)
        raise ValueError(f"Unsupported run mode: {request.mode}")

    def load_knowledge_summary(self) -> KnowledgeSummaryView:
        store = KnowledgeStore()
        idx = store.load_index()
        observations = store.list_observations()
        bucket_map: dict[tuple[str, str], list[dict[str, Any]]] = {}
        recent_learnings: list[str] = []

        for obs in observations:
            car = str(obs.get("car", "unknown")).strip() or "unknown"
            track = str(obs.get("track", "Unknown Track")).strip() or "Unknown Track"
            bucket_map.setdefault((car, track), []).append(obs)

        buckets: list[KnowledgeBucketView] = []
        for (car, track), items in sorted(bucket_map.items()):
            items.sort(key=lambda item: str(item.get("session_id", "")))
            slug = _slug_fragment(track).split("_")[0] if track else "track"
            insight_path = store.base / "insights" / f"{car}_{slug}_insights.json"
            model_path = store.base / "models" / f"{car}_{slug}_empirical.json"
            insights: list[str] = []
            corrections: list[str] = []

            if insight_path.exists():
                insight_payload = json.loads(insight_path.read_text())
                insights = [str(item) for item in insight_payload.get("key_insights", [])[:3]]
                recent_learnings.extend(insights[:2])

            if model_path.exists():
                model_payload = json.loads(model_path.read_text())
                for key, value in list((model_payload.get("corrections") or {}).items())[:3]:
                    if isinstance(value, (int, float)):
                        corrections.append(f"{key.replace('_', ' ')}: {value:+.4f}")
                    else:
                        corrections.append(f"{key.replace('_', ' ')}: {value}")

            buckets.append(
                KnowledgeBucketView(
                    car=car,
                    track=track,
                    observation_count=len(items),
                    last_session_id=str(items[-1].get("session_id", "")),
                    corrections=corrections,
                    insights=insights,
                )
            )

        return KnowledgeSummaryView(
            total_observations=int(idx.get("total_observations", len(observations))),
            total_deltas=int(idx.get("total_deltas", 0)),
            cars_seen=[str(item) for item in idx.get("cars_seen", [])],
            tracks_seen=[str(item) for item in idx.get("tracks_seen", [])],
            buckets=buckets,
            recent_learnings=recent_learnings[:6],
        )

    def _run_single_session(
        self,
        run_id: str,
        request: RunCreateRequest,
        phase_callback: PhaseCallback,
    ) -> tuple[str, dict[str, Any], list[GeneratedArtifact]]:
        if len(request.ibt_paths) != 1:
            raise ValueError("Single-session mode requires exactly one IBT file.")

        phase_callback("Running telemetry-backed setup analysis")
        run_dir = self.settings.artifact_dir_for(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        json_path = run_dir / "single_session.json"
        sto_path = run_dir / "single_session.sto"
        report_path = self.settings.report_path_for(run_id, "single_session")

        args = argparse.Namespace(
            car=request.car,
            ibt=str(request.ibt_paths[0]),
            wing=request.wing,
            lap=request.lap,
            balance=request.balance,
            tolerance=request.tolerance,
            fuel=request.fuel,
            free=request.free_opt,
            sto=str(sto_path),
            json=str(json_path),
            report_only=True,
            no_learn=not request.use_learning,
            legacy_solver=False,
            min_lap_time=108.0,
            outlier_pct=0.115,
            stint=False,
            stint_threshold=1.5,
            verbose=False,
            space=False,
        )

        from pipeline.produce import produce_result

        result = produce_result(args, emit_report=False, compact_report=False)
        report_text = str(result.get("report", ""))
        report_path.write_text(report_text, encoding="utf-8")

        phase_callback("Normalizing session results")
        predicted, prediction_conf = predict_candidate_telemetry(
            current_setup=result["current_setup"],
            baseline_measured=result["measured"],
            step1=result["step1"],
            step2=result["step2"],
            step3=result["step3"],
            step4=result["step4"],
            step5=result["step5"],
            step6=result["step6"],
            supporting=result["supporting"],
        )

        context = {
            "current_setup": result["current_setup"],
            "wing": result["wing"],
            "step1": result["step1"],
            "step2": result["step2"],
            "step3": result["step3"],
            "step4": result["step4"],
            "step5": result["step5"],
            "step6": result["step6"],
            "supporting": result["supporting"],
        }
        setup_groups = _build_setup_groups(context)
        top_changes = _pick_top_changes(setup_groups)
        telemetry = _build_telemetry_views(result["measured"], predicted, context)
        problems = _build_problem_views(result.get("diagnosis"))
        score_value = _safe_float(getattr(prediction_conf, "overall", None))
        track_name = _coalesce(
            _lookup(result["track"], "track_name"),
            _lookup(result["track"], "track_config"),
            "Unknown Track",
        )
        overview_badges = [
            f"Wing {result['wing']:.0f} deg" if result.get("wing") is not None else "Wing auto",
            f"Fuel {result.get('fuel_l', 0):.0f} L",
        ]
        if result.get("selected_candidate_family"):
            overview_badges.append(f"Family {str(result['selected_candidate_family']).replace('_', ' ')}")
        engineering_notes = list(result.get("solver_notes", []))
        if result.get("legal_validation") is not None:
            issues = getattr(result["legal_validation"], "issues", []) or []
            engineering_notes.append(
                "Legality check passed." if not issues else f"Legality check flagged {len(issues)} issue(s)."
            )

        summary = SessionResultView(
            result_kind="single_session",
            title="Single Session Analysis",
            subtitle=f"{result['car'].name} on {track_name}",
            car_name=result["car"].name,
            track_name=track_name,
            lap_label=f"Lap {result['lap_number']} · {result['lap_time_s']:.3f}s",
            assessment=str(getattr(result["diagnosis"], "assessment", "unknown")).replace("_", " ").title(),
            confidence_label=_score_label(score_value),
            confidence_value=score_value,
            overview_badges=overview_badges,
            problems=problems,
            top_changes=top_changes,
            setup_groups=setup_groups,
            telemetry=telemetry,
            engineering_notes=engineering_notes,
            report_text=report_text,
            candidate_family=result.get("selected_candidate_family"),
            candidate_score=_safe_float(result.get("selected_candidate_score")),
        )
        artifacts = [GeneratedArtifact("report", "Engineering report", report_path)]
        if json_path.exists():
            artifacts.append(GeneratedArtifact("json", "Session JSON", json_path))
        if sto_path.exists():
            artifacts.append(GeneratedArtifact("sto", ".sto setup", sto_path))
        return "single_session", asdict(summary), artifacts

    def _run_track_solve(
        self,
        run_id: str,
        request: RunCreateRequest,
        phase_callback: PhaseCallback,
    ) -> tuple[str, dict[str, Any], list[GeneratedArtifact]]:
        if not request.track:
            raise ValueError("Track-only solve requires a track name.")
        if request.wing is None:
            raise ValueError("Track-only solve requires a wing angle.")

        phase_callback("Running track-only physics solve")
        run_dir = self.settings.artifact_dir_for(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        json_path = run_dir / "track_solve.json"
        sto_path = run_dir / "track_solve.sto"
        report_path = self.settings.report_path_for(run_id, "track_solve")
        args = argparse.Namespace(
            car=request.car,
            track=request.track,
            wing=request.wing,
            balance=request.balance,
            tolerance=request.tolerance,
            fuel=request.fuel if request.fuel is not None else 89.0,
            free=request.free_opt,
            json=False,
            save=str(json_path),
            sto=str(sto_path),
            report_only=True,
            space=False,
            stint_laps=30,
            learn=request.use_learning,
            legacy_solver=False,
        )

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            run_solver(args)
        report_text = buffer.getvalue()
        report_path.write_text(report_text, encoding="utf-8")

        if not json_path.exists():
            raise RuntimeError("Track-only solve did not produce a JSON summary.")

        summary_payload = json.loads(json_path.read_text())
        car = get_car(request.car)
        context = {
            "current_setup": None,
            "wing": request.wing,
            "step1": summary_payload.get("step1_rake", {}),
            "step2": summary_payload.get("step2_heave", {}),
            "step3": summary_payload.get("step3_corner", {}),
            "step4": summary_payload.get("step4_arb", {}),
            "step5": summary_payload.get("step5_geometry", {}),
            "step6": summary_payload.get("step6_dampers", {}),
            "supporting": {},
        }
        setup_groups = _build_setup_groups(context)
        summary = SessionResultView(
            result_kind="track_solve",
            title="Track-Only Solve",
            subtitle=f"{car.name} on {request.track}",
            car_name=car.name,
            track_name=request.track,
            lap_label="Telemetry-free solve",
            assessment="Physics-only recommendation",
            confidence_label="Telemetry-free",
            confidence_value=None,
            overview_badges=[f"Wing {request.wing:.0f} deg", f"Fuel {(request.fuel if request.fuel is not None else 89.0):.0f} L"],
            problems=[],
            top_changes=_pick_top_changes(setup_groups),
            setup_groups=setup_groups,
            telemetry=[
                MetricView(
                    label="Telemetry",
                    baseline="Unavailable",
                    predicted="Unavailable",
                    delta="N/A",
                    note="Track-only solve does not have an IBT baseline.",
                )
            ],
            engineering_notes=["This solve uses track profile plus physics only; no telemetry diagnosis was available."],
            report_text=report_text,
        )
        artifacts = [GeneratedArtifact("report", "Engineering report", report_path)]
        if json_path.exists():
            artifacts.append(GeneratedArtifact("json", "Solver JSON", json_path))
        if sto_path.exists():
            artifacts.append(GeneratedArtifact("sto", ".sto setup", sto_path))
        return "track_solve", asdict(summary), artifacts

    def _run_comparison(
        self,
        run_id: str,
        request: RunCreateRequest,
        phase_callback: PhaseCallback,
    ) -> tuple[str, dict[str, Any], list[GeneratedArtifact]]:
        if len(request.ibt_paths) < 2:
            raise ValueError("Comparison mode requires at least two IBT files.")

        run_dir = self.settings.artifact_dir_for(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        json_path = run_dir / "comparison.json"
        sto_path = run_dir / "comparison_best.sto"
        report_path = self.settings.report_path_for(run_id, "comparison")

        phase_callback("Analyzing uploaded telemetry sessions")
        car = get_car(request.car)
        sessions = []
        for index, ibt_path in enumerate(request.ibt_paths, start=1):
            phase_callback(f"Analyzing session {index}/{len(request.ibt_paths)}")
            sessions.append(
                analyze_session(
                    ibt_path=str(ibt_path),
                    car=car,
                    wing=request.wing,
                    fuel=request.fuel,
                    lap=request.lap,
                    label=f"S{index}",
                )
            )

        phase_callback("Comparing sessions")
        comparison = compare_sessions(sessions)
        scoring = score_sessions(comparison)

        synthesis: SynthesisResult | None = None
        if request.synthesize:
            phase_callback("Synthesizing best setup")
            synthesis = synthesize_setup(
                comparison=comparison,
                scoring=scoring,
                car=car,
                wing=request.wing,
                fuel=request.fuel or 89.0,
                balance_target=request.balance,
            )

        report_text = format_comparison_report(comparison, scoring, synthesis)
        report_path.write_text(report_text, encoding="utf-8")
        save_comparison_json(comparison, scoring, synthesis, str(json_path))

        artifacts = [GeneratedArtifact("report", "Comparison report", report_path)]
        if json_path.exists():
            artifacts.append(GeneratedArtifact("json", "Comparison JSON", json_path))

        if synthesis is not None:
            write_sto(
                car_name=car.name,
                track_name=sessions[0].track_name,
                wing=synthesis.wing_angle,
                fuel_l=synthesis.fuel_l,
                step1=synthesis.step1,
                step2=synthesis.step2,
                step3=synthesis.step3,
                step4=synthesis.step4,
                step5=synthesis.step5,
                step6=synthesis.step6,
                output_path=str(sto_path),
                tyre_pressure_kpa=getattr(synthesis.supporting, "tyre_cold_fl_kpa", None),
                brake_bias_pct=getattr(synthesis.supporting, "brake_bias_pct", None),
                diff_coast_drive_ramp=(
                    f"{getattr(synthesis.supporting, 'diff_ramp_coast', '?')}/"
                    f"{getattr(synthesis.supporting, 'diff_ramp_drive', '?')}"
                ),
                diff_clutch_plates=getattr(synthesis.supporting, "diff_clutch_plates", None),
                diff_preload_nm=getattr(synthesis.supporting, "diff_preload_nm", None),
                tc_gain=getattr(synthesis.supporting, "tc_gain", None),
                tc_slip=getattr(synthesis.supporting, "tc_slip", None),
            )
            if sto_path.exists():
                artifacts.append(GeneratedArtifact("sto", ".sto setup", sto_path))

        phase_callback("Normalizing comparison results")
        summary = ComparisonResultView(
            result_kind="comparison",
            title="Multi-Session Compare",
            subtitle=f"{car.name} across {len(sessions)} sessions",
            car_name=car.name,
            track_name=sessions[0].track_name,
            sessions_count=len(sessions),
            winner_label=scoring.scores[0].session.label if scoring.scores else "N/A",
            overview_badges=_comparison_badges(sessions, synthesis),
            rankings=_build_rankings(scoring),
            setup_rows=_build_comparison_rows(comparison.setup_deltas),
            telemetry_rows=_build_comparison_rows(comparison.telemetry_deltas, include_delta=False),
            corner_highlights=_build_corner_highlights(comparison),
            synthesis_groups=_build_setup_groups(
                {
                    "current_setup": None,
                    "wing": synthesis.wing_angle if synthesis is not None else request.wing,
                    "step1": getattr(synthesis, "step1", {}),
                    "step2": getattr(synthesis, "step2", {}),
                    "step3": getattr(synthesis, "step3", {}),
                    "step4": getattr(synthesis, "step4", {}),
                    "step5": getattr(synthesis, "step5", {}),
                    "step6": getattr(synthesis, "step6", {}),
                    "supporting": getattr(synthesis, "supporting", {}),
                }
            ) if synthesis is not None else [],
            engineering_notes=list(getattr(synthesis, "solver_notes", [])) if synthesis is not None else [],
            report_text=report_text,
        )
        return "comparison", asdict(summary), artifacts


def _lookup(root: Any, path: str | Iterable[str], default: Any = None) -> Any:
    candidates = (path,) if isinstance(path, str) else path
    for candidate in candidates:
        current = root
        found = True
        for part in candidate.split("."):
            if current is None:
                found = False
                break
            if isinstance(current, dict):
                if part not in current:
                    found = False
                    break
                current = current[part]
                continue
            if hasattr(current, part):
                current = getattr(current, part)
                continue
            found = False
            break
        if found:
            return current
    return default


def _build_setup_groups(context: dict[str, Any]) -> list[SetupGroupView]:
    groups: list[SetupGroupView] = []
    for group_spec in SETUP_GROUP_SPECS:
        rows: list[ChangeView] = []
        for row_spec in group_spec.rows:
            current_value = _lookup(context, row_spec.current_paths)
            recommended_value = _lookup(context, row_spec.recommended_paths)
            rows.append(
                ChangeView(
                    label=row_spec.label,
                    current=_format_value(current_value, row_spec.units, row_spec.digits, row_spec.signed),
                    recommended=_format_value(recommended_value, row_spec.units, row_spec.digits, row_spec.signed),
                    delta=_format_delta(current_value, recommended_value, row_spec.units, row_spec.digits),
                    reason=group_spec.help_text,
                )
            )
        groups.append(SetupGroupView(name=group_spec.name, help_text=group_spec.help_text, rows=rows))
    return groups


def _pick_top_changes(groups: list[SetupGroupView]) -> list[ChangeView]:
    changed: list[ChangeView] = []
    for group in groups:
        for row in group.rows:
            if row.delta not in {"No change", "N/A"}:
                changed.append(row)
    if not changed:
        changed = [row for group in groups for row in group.rows]
    return changed[:5]


def _build_problem_views(diagnosis: Any) -> list[ProblemView]:
    problems = getattr(diagnosis, "problems", []) or []
    return [
        ProblemView(
            severity=str(getattr(problem, "severity", "note")).title(),
            symptom=str(getattr(problem, "symptom", "Unknown issue")),
            cause=str(getattr(problem, "cause", "")),
            speed_context=str(getattr(problem, "speed_context", "all")),
        )
        for problem in problems[:6]
    ]


def _build_telemetry_views(measured: Any, predicted: Any, context: dict[str, Any]) -> list[MetricView]:
    metric_specs = (
        ("Lap time", ("measured.lap_time_s",), (), "s", 3),
        ("Front bottoming", ("measured.bottoming_event_count_front",), ("step2.bottoming_events_front",), "", 0),
        ("Rear RH variance", ("measured.rear_rh_std_mm",), ("predicted.rear_rh_std_mm",), "mm", 2),
        ("Front excursion", ("measured.front_rh_excursion_measured_mm",), ("predicted.front_excursion_mm",), "mm", 2),
        ("Braking pitch", ("measured.pitch_range_braking_deg",), ("predicted.braking_pitch_deg",), "deg", 2),
        ("Front lock p95", ("measured.front_braking_lock_ratio_p95",), ("predicted.front_lock_p95",), "", 3),
        ("Rear slip p95", ("measured.rear_power_slip_ratio_p95",), ("predicted.rear_power_slip_p95",), "", 3),
        ("Understeer low", ("measured.understeer_low_speed_deg",), ("predicted.understeer_low_deg",), "deg", 2),
        ("Understeer high", ("measured.understeer_high_speed_deg",), ("predicted.understeer_high_deg",), "deg", 2),
        ("Front hot pressure", ("measured.front_pressure_mean_kpa",), ("predicted.front_pressure_hot_kpa",), "kPa", 1),
        ("Rear hot pressure", ("measured.rear_pressure_mean_kpa",), ("predicted.rear_pressure_hot_kpa",), "kPa", 1),
    )
    prediction_context = dict(context)
    prediction_context["measured"] = measured
    prediction_context["predicted"] = predicted
    rows: list[MetricView] = []
    for label, baseline_paths, predicted_paths, units, digits in metric_specs:
        baseline_value = _lookup(prediction_context, baseline_paths)
        predicted_value = _lookup(prediction_context, predicted_paths)
        if baseline_value is None and predicted_value is None:
            continue
        note = "Predicted lap time is not modeled directly." if baseline_paths == ("measured.lap_time_s",) else ""
        rows.append(
            MetricView(
                label=label,
                baseline=_format_value(baseline_value, units, digits),
                predicted=_format_value(predicted_value, units, digits),
                delta=_format_delta(baseline_value, predicted_value, units, digits),
                note=note,
            )
        )
    return rows


def _build_rankings(scoring: Any) -> list[RankingView]:
    rankings: list[RankingView] = []
    for score in getattr(scoring, "scores", []) or []:
        rankings.append(
            RankingView(
                label=str(score.session.label),
                lap_time=f"{score.session.lap_time_s:.3f}s",
                overall_score=f"{score.overall_score:.1%}",
                strengths=list(score.strengths[:3]),
                weaknesses=list(score.weaknesses[:3]),
            )
        )
    return rankings


def _build_comparison_rows(
    mapping: dict[str, list[Any]],
    *,
    include_delta: bool = True,
) -> list[ComparisonTableRowView]:
    rows: list[ComparisonTableRowView] = []
    for label, values in mapping.items():
        formatted_values = [_format_table_value(value) for value in values]
        numeric_values = [float(value) for value in values if isinstance(value, (int, float))]
        delta = ""
        if include_delta and len(numeric_values) >= 2:
            delta = f"{max(numeric_values) - min(numeric_values):.2f}"
        rows.append(ComparisonTableRowView(label=label, values=formatted_values, delta=delta))
    return rows[:18]


def _build_corner_highlights(comparison: Any) -> list[CornerHighlightView]:
    highlights: list[tuple[float, CornerHighlightView]] = []
    for corner in getattr(comparison, "corner_comparisons", []) or []:
        losses = [
            _safe_float(getattr(session_corner, "delta_to_min_time_s", None))
            for session_corner in (corner.per_session or [])
            if session_corner is not None
        ]
        filtered = [loss for loss in losses if loss is not None]
        if len(filtered) < 2:
            continue
        spread = max(filtered) - min(filtered)
        highlights.append(
            (
                spread,
                CornerHighlightView(
                    corner_label=f"Corner {corner.corner_id}",
                    summary=f"{corner.direction.title()} / {corner.speed_class}-speed",
                    spread=f"{spread * 1000:.0f} ms spread",
                ),
            )
        )
    highlights.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in highlights[:5]]


def _comparison_badges(sessions: list[Any], synthesis: SynthesisResult | None) -> list[str]:
    badges = [f"{len(sessions)} sessions"]
    wings = sorted({getattr(session, "wing_angle", 0.0) for session in sessions})
    if wings:
        badges.append("Wing " + ", ".join(f"{wing:.0f} deg" for wing in wings))
    if synthesis is not None and synthesis.solve_basis:
        badges.append(f"Solve basis: {synthesis.solve_basis.replace('_', ' ')}")
    return badges


def _format_table_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _score_label(score: float | None) -> str:
    if score is None:
        return "Advisory"
    if score >= 0.75:
        return "High"
    if score >= 0.45:
        return "Medium"
    return "Low"


def _format_value(value: Any, units: str = "", digits: int = 1, signed: bool = False) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number):
            return "-"
        if digits == 0:
            text = f"{int(round(number))}"
        else:
            text = f"{number:+.{digits}f}" if signed else f"{number:.{digits}f}"
        return text if not units else f"{text} {units}"
    return str(value)


def _format_delta(current_value: Any, recommended_value: Any, units: str, digits: int) -> str:
    if current_value is None or recommended_value is None:
        return "N/A"
    if not isinstance(current_value, (int, float)) or not isinstance(recommended_value, (int, float)):
        return "No change" if str(current_value) == str(recommended_value) else "Changed"
    delta = float(recommended_value) - float(current_value)
    if abs(delta) < 1e-9:
        return "No change"
    if digits == 0:
        text = f"{int(round(delta)):+d}"
    else:
        text = f"{delta:+.{digits}f}"
    return text if not units else f"{text} {units}"


def _safe_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _slug_fragment(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "unknown"
