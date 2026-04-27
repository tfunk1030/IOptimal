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
from output.report import _load_support_tier
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


# GT3 Phase 2 W9.1 — F2 fix. The webapp Setup Diff card historically rendered a
# fixed list of platform rows assuming GTP architecture (heave / third spring /
# torsion bar). GT3 cars carry `heave_spring=None` and `front_torsion_c=0.0` so
# those rows render as '-' and waste UI real-estate; worse, GT3 corner springs
# diverge per-axle (BMW M4 GT3 driver-loaded F=252, R=179 N/mm) and were not
# represented at all. The selector below resolves the architecture from the
# canonical car name and returns the appropriate group specs.
_GTP_SETUP_GROUP_SPECS: tuple[GroupSpec, ...] = (
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


# GT3 platform layout — coil springs at all four corners, with bump rubber gap
# (front + rear) and splitter height as the iRacing-exposed garage parameters.
# Heave / third / torsion rows are intentionally omitted (those don't exist in
# GT3 garage XML). Step3 returns paired LF==RF and LR==RR coil rates per W3.1
# / W4.1; we surface them as Front spring + Rear spring rows here.
_GT3_SETUP_GROUP_SPECS: tuple[GroupSpec, ...] = (
    GroupSpec(
        "Platform",
        "Ride heights, coil springs, and bump rubber gap that set the GT3 platform.",
        (
            RowSpec("Wing angle", ("current_setup.wing_angle_deg",), ("wing",), "deg", 0),
            RowSpec("Front pushrod", ("current_setup.front_pushrod_mm",), ("step1.front_pushrod_offset_mm",), "mm", 1, True),
            RowSpec("Rear pushrod", ("current_setup.rear_pushrod_mm",), ("step1.rear_pushrod_offset_mm",), "mm", 1, True),
            RowSpec("Rear ride height", ("current_setup.static_rear_rh_mm",), ("step1.static_rear_rh_mm",), "mm", 1),
            RowSpec("Front spring", ("current_setup.front_corner_spring_nmm",), ("step3.front_coil_rate_nmm",), "N/mm", 0),
            RowSpec("Rear spring", ("current_setup.rear_corner_spring_nmm", "current_setup.rear_spring_nmm"), ("step3.rear_spring_rate_nmm", "step3.rear_spring_nmm"), "N/mm", 0),
            RowSpec("Front bump rubber gap", ("current_setup.front_bump_rubber_gap_mm",), ("step2.front_bump_rubber_gap_mm",), "mm", 1),
            RowSpec("Rear bump rubber gap", ("current_setup.rear_bump_rubber_gap_mm",), ("step2.rear_bump_rubber_gap_mm",), "mm", 1),
            RowSpec("Splitter height", ("current_setup.splitter_height_mm",), ("step1.splitter_height_mm",), "mm", 1),
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
        "Low-speed and high-speed damping clicks grouped by axle (per-axle on GT3, not per-corner).",
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


# Backward compatibility: legacy callers (and tests pre-W9.1) can still import
# ``SETUP_GROUP_SPECS`` and get the GTP layout. New code should prefer
# ``setup_group_specs_for(canonical_car_name)``.
SETUP_GROUP_SPECS: tuple[GroupSpec, ...] = _GTP_SETUP_GROUP_SPECS


def setup_group_specs_for(car_canonical: str | None) -> tuple[GroupSpec, ...]:
    """Return the architecture-appropriate ``SETUP_GROUP_SPECS`` for a car.

    GT3 cars (``car.suspension_arch.has_heave_third == False``) drop the
    heave / third / torsion rows and surface 4 corner spring rates plus
    bump rubber gap (front+rear) and splitter height instead. GTP cars
    keep the legacy layout.

    Falls back to the GTP layout when ``car_canonical`` is unknown so the
    UI always renders something — the platform mismatch will be caught
    downstream by the calibration gate / writer.
    """
    if not car_canonical:
        return _GTP_SETUP_GROUP_SPECS
    try:
        car = get_car(car_canonical)
    except (KeyError, AttributeError):
        return _GTP_SETUP_GROUP_SPECS
    arch = getattr(car, "suspension_arch", None)
    if arch is None:
        return _GTP_SETUP_GROUP_SPECS
    if not getattr(arch, "has_heave_third", True):
        return _GT3_SETUP_GROUP_SPECS
    return _GTP_SETUP_GROUP_SPECS


def list_supported_cars(class_filter: str | None = None) -> list[tuple[str, str, str]]:
    """Return all supported cars as ``(canonical, display_name, class)`` triples.

    ``class`` is "GTP" or "GT3" derived from the car's
    ``SuspensionArchitecture``. The webapp uses this to render an
    ``<optgroup>`` per class in the car selector. ``class_filter`` may be
    "GTP" or "GT3" to restrict the list to a single class.
    """
    from car_model.cars import _CARS  # canonical car registry

    rows: list[tuple[str, str, str]] = []
    for canonical, car in _CARS.items():
        arch = getattr(car, "suspension_arch", None)
        if arch is None:
            klass = "GTP"
        elif getattr(arch, "has_heave_third", True):
            klass = "GTP"
        else:
            klass = "GT3"
        if class_filter is not None and klass != class_filter:
            continue
        display = getattr(car, "name", canonical)
        rows.append((canonical, display, klass))
    rows.sort(key=lambda item: (item[2], item[1]))
    return rows


# Plain-English explanations for each setup parameter.
PARAM_EXPLANATIONS: dict[str, str] = {
    "Wing angle": "Rear wing angle. Higher adds downforce and drag — more grip in corners but lower top speed.",
    "Front pushrod": "Front pushrod length. Adjusts front ride height — affects aero balance at speed.",
    "Rear pushrod": "Rear pushrod length. Adjusts rear ride height and rake angle.",
    "Rear ride height": "Static rear ride height. Controls rake — more rake means more front downforce bias.",
    "Front heave": "Front heave spring rate. Stiffer keeps the front stable under braking and at high speed, but harsher over bumps.",
    "Rear third": "Rear third/heave spring rate. Stiffer prevents rear bottoming at speed, softer gives more rear grip over bumps.",
    "Front torsion": "Front torsion bar (corner spring). Stiffer reduces body roll but makes the front less compliant over kerbs.",
    "Rear spring": "Rear corner spring rate. Stiffer reduces roll and improves rear platform control, softer adds mechanical grip.",
    "Front ARB blade": "Front anti-roll bar stiffness. Stiffer adds understeer in corners by increasing front load transfer.",
    "Rear ARB blade": "Rear anti-roll bar stiffness. Stiffer adds oversteer by increasing rear load transfer in corners.",
    "Brake bias": "Front-to-rear brake balance. Higher means more front braking — more stable but less rotation on turn-in.",
    "Diff preload": "Differential preload torque. Higher locks the diff more — better traction but less mid-corner rotation.",
    "Front camber": "Front wheel camber. More negative keeps the outer tyre flat in corners for better grip.",
    "Rear camber": "Rear wheel camber. More negative improves rear corner grip but can reduce straight-line traction.",
    "Front toe": "Front toe angle. Toe-out sharpens turn-in response, toe-in adds straight-line stability.",
    "Rear toe": "Rear toe angle. Toe-in stabilises the rear, toe-out adds rotation but can feel nervous.",
    "Front LS comp": "Front low-speed compression damping. Higher slows weight transfer onto the front — calmer turn-in.",
    "Front LS rebound": "Front low-speed rebound damping. Higher slows weight coming off the front — more consistent on exit.",
    "Front HS comp": "Front high-speed compression damping. Controls front end over big bumps and kerbs.",
    "Front HS rebound": "Front high-speed rebound damping. Controls front recovery after bumps — too high causes skipping.",
    "Rear LS comp": "Rear low-speed compression damping. Higher slows rear squat under throttle — more composed traction.",
    "Rear LS rebound": "Rear low-speed rebound damping. Higher slows rear unloading on turn-in — less oversteer snap.",
    "Rear HS comp": "Rear high-speed compression damping. Controls rear over kerbs and big bumps.",
    "Rear HS rebound": "Rear high-speed rebound damping. Controls rear recovery — too high causes oscillation.",
    "TC gain": "Traction control gain. Higher intervenes earlier to prevent wheelspin.",
    "TC slip": "Traction control slip target. Higher allows more wheelspin before TC activates.",
    # GT3 Phase 2 W9.1 — F4 fix. GT3 cars use coil springs at all four corners
    # plus bump rubber gap and (BMW/Aston) splitter height as the iRacing-
    # exposed garage parameters. The labels below are surfaced by
    # ``_GT3_SETUP_GROUP_SPECS`` and use the same plain-English voice as the
    # GTP entries above. Initial copy reviewed against
    # ``docs/gt3_per_car_spec.md`` cross-cutting facts; revisit once telemetry
    # is available to refine the trade-off language.
    "Front spring": "Front corner spring (coil-over) rate. Stiffer reduces front compliance and roll, softer adds mechanical grip and bump compliance.",
    "Front bump rubber gap": "Gap to the front bump rubber. Smaller gap engages the rubber sooner under aero load — extra heave stiffness when needed without changing the coil rate. Larger gap keeps the platform on the spring across more of the operating range.",
    "Rear bump rubber gap": "Gap to the rear bump rubber. Smaller gap stiffens up the rear under load; larger keeps the rear on the coil rate longer.",
    "Splitter height": "Splitter ride height (BMW M4 GT3 / Aston Vantage). Lower splitter increases front downforce but raises stall risk if the front floats off the floor; higher reduces front DF for a safer aero window.",
}


def _change_reason(label: str, current: Any, recommended: Any) -> str:
    """Build a plain-English reason for a setup change."""
    base = PARAM_EXPLANATIONS.get(label, "")
    # Add directional prefix when both values are numeric
    try:
        c_val = float(current)
        r_val = float(recommended)
        diff = r_val - c_val
        if abs(diff) < 1e-6:
            return base or "No change."
        direction = "Increasing" if diff > 0 else "Decreasing"
        return f"{direction}. {base}"
    except (TypeError, ValueError):
        return base


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
            insight_path = _resolve_learning_file(
                store.base / "insights",
                car=car,
                track=track,
                suffix="insights.json",
            )
            model_path = _resolve_learning_file(
                store.base / "models",
                car=car,
                track=track,
                suffix="empirical.json",
            )
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
            scenario_profile=request.scenario_profile,
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
        setup_groups = _build_setup_groups(context, getattr(result.get("car"), "canonical_name", request.car))
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
            f"Scenario {request.scenario_profile.replace('_', ' ')}",
        ]
        if result.get("selected_candidate_family"):
            overview_badges.append(f"Family {str(result['selected_candidate_family']).replace('_', ' ')}")
        engineering_notes = list(result.get("solver_notes", []))
        if result.get("legal_validation") is not None:
            issues = getattr(result["legal_validation"], "issues", []) or []
            engineering_notes.append(
                "Legality check passed." if not issues else f"Legality check flagged {len(issues)} issue(s)."
            )

        _car_slug = getattr(result["car"], "canonical_name", "bmw")
        _tier_info = _load_support_tier(_car_slug, track_name) or {}
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
            support_tier=_tier_info.get("confidence_tier", "unknown"),
            observation_count=_tier_info.get("samples", 0),
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
            scenario_profile=request.scenario_profile,
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
        setup_groups = _build_setup_groups(context, getattr(car, "canonical_name", request.car))
        _tier_info_ts = _load_support_tier(getattr(car, "canonical_name", "bmw"), request.track) or {}
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
            support_tier=_tier_info_ts.get("confidence_tier", "unknown"),
            observation_count=_tier_info_ts.get("samples", 0),
            overview_badges=[
                f"Wing {request.wing:.0f} deg",
                f"Fuel {(request.fuel if request.fuel is not None else 89.0):.0f} L",
                f"Scenario {request.scenario_profile.replace('_', ' ')}",
            ],
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
                },
                getattr(car, "canonical_name", request.car),
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


def _build_setup_groups(context: dict[str, Any], car_canonical: str | None = None) -> list[SetupGroupView]:
    groups: list[SetupGroupView] = []
    # GT3 Phase 2 W9.1 — F2 fix. Resolve specs from the car's architecture
    # rather than a fixed module-level constant; falls back to GTP layout
    # for unknown / missing cars.
    specs = setup_group_specs_for(car_canonical) if car_canonical else SETUP_GROUP_SPECS
    for group_spec in specs:
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
                    reason=_change_reason(row_spec.label, current_value, recommended_value),
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
    # Sort by absolute delta magnitude so the biggest changes appear first.
    def _delta_magnitude(cv: ChangeView) -> float:
        try:
            # Strip units/signs and parse the numeric part of delta string
            return abs(float(cv.delta.split()[0].replace("+", "")))
        except (ValueError, IndexError):
            return 0.0
    changed.sort(key=_delta_magnitude, reverse=True)
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


def _resolve_learning_file(base_dir: Path, *, car: str, track: str, suffix: str) -> Path:
    full_slug = _slug_fragment(track) if track else "track"
    candidate_names = [f"{car}_{full_slug}_{suffix}"]
    if full_slug:
        legacy_slug = full_slug.split("_")[0]
        if legacy_slug != full_slug:
            candidate_names.append(f"{car}_{legacy_slug}_{suffix}")
    for filename in candidate_names:
        candidate = base_dir / filename
        if candidate.exists():
            return candidate
    return base_dir / candidate_names[0]
