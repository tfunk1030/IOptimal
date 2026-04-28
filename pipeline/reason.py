"""Enhanced multi-session reasoning engine (9-phase pipeline).

Reads N IBT files, builds an evolving understanding via all-pairs delta
analysis, per-corner profiling, speed-regime separation, target telemetry
construction, historical knowledge integration, physics cross-validation,
and confidence-gated modifier generation.

Pipeline:
  Phase 1: Extract         (analyze each IBT)
  Phase 2: All-Pairs Delta (compare every pair, weighted by quality)
  Phase 3: Corner Profiling (per-corner weakness map across sessions)
  Phase 4: Speed-Regime    (separate HS aero vs LS mechanical problems)
  Phase 5: Target Profile  (cherry-pick best metrics → ideal car state)
  Phase 6: Historical      (query learner for prior knowledge)
  Phase 7: Physics Reason  (cross-validate, category scoring, quantify trade-offs)
  Phase 8: Modifiers       (sensitivity-scaled, confidence-gated)
  Phase 9: Solve + Report  (6-step solver, enhanced report)

Usage:
    python -m pipeline.reason --car bmw --wing 17 --ibt s1.ibt s2.ibt s3.ibt
    python -m pipeline.reason --car bmw --wing 17 --ibt s1.ibt s2.ibt --sto optimal.sto
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analyzer.adaptive_thresholds import compute_adaptive_thresholds
from analyzer.context import SessionContext, build_session_context
from analyzer.diagnose import diagnose
from analyzer.driver_style import DriverProfile, analyze_driver, refine_driver_with_measured
from analyzer.extract import MeasuredState, extract_measurements
from analyzer.segment import CornerAnalysis, segment_lap
from analyzer.stint_analysis import build_stint_dataset, dataset_to_evolution, merge_stint_datasets
from analyzer.setup_reader import CurrentSetup
from analyzer.setup_schema import apply_live_control_overrides, build_setup_schema
from analyzer.telemetry_truth import (
    ParameterDecision,
    SessionNormalization,
    build_session_normalization,
    get_signal,
    signals_to_dict,
    summarize_signal_quality,
    usable_signal_value,
)
from car_model.cars import CarModel, get_car
from learner.delta_detector import (
    KNOWN_CAUSALITY, EFFECT_METRICS,
    detect_delta, SessionDelta,
)
from learner.envelope import EnvelopeDistance, TelemetryEnvelope, build_telemetry_envelope, compute_envelope_distance
from learner.observation import Observation, build_observation
from learner.setup_clusters import SetupCluster, SetupDistance, build_setup_cluster, compute_setup_distance
from solver.setup_fingerprint import (
    CandidateVeto,
    SetupFingerprint,
    ValidationCluster,
    fingerprint_from_current_setup,
    fingerprint_from_solver_steps,
    match_failed_cluster,
)
from solver.legality_engine import LegalValidation
from solver.bmw_coverage import (
    build_parameter_coverage,
    build_search_baseline,
    build_telemetry_coverage,
)
from solver.bmw_rotation_search import preserve_candidate_rotation_controls
from solver.candidate_search import SetupCandidate, candidate_to_dict, generate_candidate_families
from solver.scenario_profiles import resolve_scenario_name, should_run_legal_manifold_search
from solver.solve_chain import SolveChainInputs, run_base_solve
from solver.stint_reasoner import aggregate_stint_recommendations, solve_stint_compromise
from track_model.build_profile import build_profile
from track_model.ibt_parser import IBTFile
from track_model.profile import TrackProfile


# ── Data structures ─────────────────────────────────────────────


@dataclass
class SessionSnapshot:
    """Everything extracted from one IBT session."""
    label: str
    ibt_path: str
    setup: CurrentSetup
    setup_schema: object | None
    measured: MeasuredState
    driver: DriverProfile
    diagnosis: object  # Diagnosis
    session_context: SessionContext | None
    track: TrackProfile
    corners: list
    observation: Observation
    lap_time_s: float
    lap_number: int
    input_order: int = 0
    sort_timestamp: float = 0.0
    sort_source: str = "cli"
    fingerprint: SetupFingerprint | None = None
    live_override_notes: list[str] = field(default_factory=list)
    stint_dataset: object | None = None
    stint_evolution: object | None = None


@dataclass
class WeightedDelta:
    """A session delta with quality weighting."""
    delta: SessionDelta
    pair: tuple[int, int]  # (before_idx, after_idx) into sessions list
    weight: float          # quality weight 0-1
    normalization: SessionNormalization | None = None


@dataclass
class ParameterLearning:
    """What we've learned about one setup parameter across sessions."""
    parameter: str
    values_seen: list[float] = field(default_factory=list)
    lap_times_at_value: list[float] = field(default_factory=list)

    # Weighted directional evidence
    increase_helped_weight: float = 0.0
    increase_hurt_weight: float = 0.0
    decrease_helped_weight: float = 0.0
    decrease_hurt_weight: float = 0.0

    # What metrics did it affect?
    affects: dict[str, list[float]] = field(default_factory=dict)

    # Lap time sensitivity: ms per unit of this parameter
    lap_time_sensitivity_ms: float = 0.0
    sensitivity_samples: int = 0

    # Best value seen (at best lap time)
    best_value: float = 0.0
    best_lap_time: float = 999.0

    # Current recommendation
    direction: str = ""  # "increase" | "decrease" | "hold" | "unknown"
    confidence: float = 0.0
    reasoning: str = ""


@dataclass
class CornerProfile:
    """Aggregated corner analysis across sessions."""
    corner_id: int
    lap_dist_m: float
    direction: str
    speed_class: str

    # Per-session data (None if corner not matched in that session)
    understeer_per_session: list[float | None] = field(default_factory=list)
    body_slip_per_session: list[float | None] = field(default_factory=list)
    time_loss_per_session: list[float | None] = field(default_factory=list)
    entry_loss_per_session: list[float | None] = field(default_factory=list)
    apex_loss_per_session: list[float | None] = field(default_factory=list)
    exit_loss_per_session: list[float | None] = field(default_factory=list)
    shock_vel_front_per_session: list[float | None] = field(default_factory=list)
    shock_vel_rear_per_session: list[float | None] = field(default_factory=list)
    front_rh_min_per_session: list[float | None] = field(default_factory=list)
    kerb_severity_per_session: list[float | None] = field(default_factory=list)
    platform_flags_per_session: list[list[str]] = field(default_factory=list)
    traction_flags_per_session: list[list[str]] = field(default_factory=list)

    # Aggregates
    mean_time_loss: float = 0.0
    total_time_loss: float = 0.0
    mean_entry_loss: float = 0.0
    mean_apex_loss: float = 0.0
    mean_exit_loss: float = 0.0
    is_consistent_weakness: bool = False
    primary_issue: str = ""  # "aero_balance" | "mechanical_balance" | "oversteer" | "platform" | "kerb" | ""

    def _compute_aggregates(self) -> None:
        """Compute aggregates from per-session data."""
        valid_losses = [t for t in self.time_loss_per_session if t is not None]
        if valid_losses:
            self.mean_time_loss = float(np.mean(valid_losses))
            self.total_time_loss = sum(valid_losses)
        valid_entry = [t for t in self.entry_loss_per_session if t is not None]
        valid_apex = [t for t in self.apex_loss_per_session if t is not None]
        valid_exit = [t for t in self.exit_loss_per_session if t is not None]
        if valid_entry:
            self.mean_entry_loss = float(np.mean(valid_entry))
        if valid_apex:
            self.mean_apex_loss = float(np.mean(valid_apex))
        if valid_exit:
            self.mean_exit_loss = float(np.mean(valid_exit))

        # Consistent weakness: >50% of sessions have time loss > 0.05s
        n_valid = len(valid_losses)
        if n_valid >= 2:
            n_slow = sum(1 for t in valid_losses if t > 0.05)
            self.is_consistent_weakness = n_slow >= n_valid * 0.5

        # Classify primary issue
        valid_us = [u for u in self.understeer_per_session if u is not None]
        valid_bs = [b for b in self.body_slip_per_session if b is not None]
        valid_sv = [s for s in self.shock_vel_front_per_session if s is not None]
        valid_kerb = [k for k in self.kerb_severity_per_session if k is not None]
        platform_flags = [flag for flags in self.platform_flags_per_session for flag in flags]
        traction_flags = [flag for flags in self.traction_flags_per_session for flag in flags]

        us_mean = float(np.mean(valid_us)) if valid_us else 0.0
        bs_mean = float(np.mean(valid_bs)) if valid_bs else 0.0
        sv_mean = float(np.mean(valid_sv)) if valid_sv else 0.0
        kerb_mean = float(np.mean(valid_kerb)) if valid_kerb else 0.0

        if kerb_mean > 2.0:
            self.primary_issue = "kerb"
        elif us_mean > 1.5 and self.speed_class == "high":
            self.primary_issue = "aero_balance"
        elif us_mean > 1.5 and self.speed_class in ("low", "mid"):
            self.primary_issue = "mechanical_balance"
        elif bs_mean > 2.0:
            self.primary_issue = "oversteer"
        elif "late_throttle" in traction_flags:
            self.primary_issue = "traction"
        elif "front_rh_collapse" in platform_flags or sv_mean > 0.3:
            self.primary_issue = "platform"


@dataclass
class SpeedRegimeAnalysis:
    """Speed-regime separated analysis."""
    # Per-regime understeer
    hs_understeer_mean: float = 0.0
    ls_understeer_mean: float = 0.0
    understeer_gradient: float = 0.0  # hs - ls

    # Per-regime LLTD
    hs_lltd_mean: float = 0.0
    ls_lltd_mean: float = 0.0

    # Time loss by speed class
    hs_time_loss_total: float = 0.0
    ls_time_loss_total: float = 0.0
    mid_time_loss_total: float = 0.0

    # RH stability at high speed
    hs_rh_std_mean: float = 0.0

    # Dominant problem regime
    dominant_regime: str = ""  # "high_speed" | "low_speed" | "balanced"

    def _determine_dominant(self) -> None:
        """Determine which speed regime is the bigger problem."""
        if self.hs_time_loss_total > 0 and self.ls_time_loss_total > 0:
            ratio = self.hs_time_loss_total / max(self.ls_time_loss_total, 0.001)
            if ratio > 1.5:
                self.dominant_regime = "high_speed"
            elif ratio < 1.0 / 1.5:
                self.dominant_regime = "low_speed"
            else:
                self.dominant_regime = "balanced"
        elif self.hs_time_loss_total > 0:
            self.dominant_regime = "high_speed"
        elif self.ls_time_loss_total > 0:
            self.dominant_regime = "low_speed"
        else:
            self.dominant_regime = "balanced"


# Metric catalog for target profile with polarity
METRIC_CATALOG: list[tuple[str, str, float | None]] = [
    # (attr, polarity, target_value_or_None)
    # polarity: "lower" | "higher" | "target"
    ("lap_time_s", "lower", None),
    ("front_rh_std_mm", "lower", None),
    ("rear_rh_std_mm", "lower", None),
    ("bottoming_event_count_front", "lower", None),
    ("bottoming_event_count_rear", "lower", None),
    ("understeer_mean_deg", "lower", None),
    ("understeer_high_speed_deg", "lower", None),
    ("understeer_low_speed_deg", "lower", None),
    ("body_slip_p95_deg", "lower", None),
    ("front_shock_vel_p99_mps", "lower", None),
    ("rear_shock_vel_p99_mps", "lower", None),
    ("peak_lat_g_measured", "higher", None),
    ("speed_max_kph", "higher", None),
    ("yaw_rate_correlation", "higher", None),
    ("front_rh_settle_time_ms", "target", 125.0),
    ("rear_rh_settle_time_ms", "target", 125.0),
    ("front_carcass_mean_c", "target", 92.5),
    ("rear_carcass_mean_c", "target", 92.5),
    ("front_pressure_mean_kpa", "target", 165.0),
    ("rear_pressure_mean_kpa", "target", 165.0),
]


@dataclass
class TargetGap:
    """One metric where there's room for improvement."""
    metric: str
    best_value: float
    best_session_idx: int
    gap_from_ideal: float  # distance from ideal (0 = perfect)
    estimated_ms_per_lap: float  # estimated lap time impact


@dataclass
class TargetProfile:
    """Cherry-picked best metrics across sessions."""
    priority_gaps: list[TargetGap] = field(default_factory=list)


@dataclass
class HistoricalContext:
    """Knowledge from the learner system."""
    session_count: int = 0
    corrections: dict = field(default_factory=dict)
    prediction_corrections: dict = field(default_factory=dict)
    impactful_parameters: list = field(default_factory=list)
    recurring_problems: list[str] = field(default_factory=list)
    historical_evidence: dict[str, list] = field(default_factory=dict)
    insights: dict | None = None
    has_data: bool = False


@dataclass
class ValidationChain:
    """A physics cross-validation result."""
    hypothesis: str
    confirmed: bool
    evidence: str
    confidence: float  # 0-1


@dataclass
class QuantifiedTradeoff:
    """A parameter trade-off with ms/lap quantification."""
    parameter: str
    benefit_metric: str
    benefit_ms: float
    cost_metric: str
    cost_ms: float
    net_ms: float
    recommendation: str  # "apply" | "skip" | "marginal"


@dataclass
class PhysicsReasoning:
    """Results of physics cross-validation and analysis."""
    validations: list[ValidationChain] = field(default_factory=list)
    category_scores: dict[str, float] = field(default_factory=dict)
    weakest_category: str = ""
    tradeoffs: list[QuantifiedTradeoff] = field(default_factory=list)


@dataclass
class ModifierWithConfidence:
    """A solver modifier with its confidence and reasoning."""
    field_name: str
    value: float
    confidence: float
    reasoning: list[str] = field(default_factory=list)


@dataclass
class ReasoningState:
    """Accumulated understanding across all sessions."""
    sessions: list[SessionSnapshot] = field(default_factory=list)
    skipped_sessions: list["SkippedSession"] = field(default_factory=list)

    # Phase 2: All-pairs deltas
    weighted_deltas: list[WeightedDelta] = field(default_factory=list)
    parameter_learnings: dict[str, ParameterLearning] = field(default_factory=dict)

    # Phase 3: Corner profiling
    corner_profiles: list[CornerProfile] = field(default_factory=list)
    top_weakness_corners: list[CornerProfile] = field(default_factory=list)

    # Phase 4: Speed-regime
    speed_regime: SpeedRegimeAnalysis = field(default_factory=SpeedRegimeAnalysis)
    telemetry_envelope: TelemetryEnvelope | None = None
    setup_cluster: SetupCluster | None = None
    envelope_distances: dict[str, EnvelopeDistance] = field(default_factory=dict)
    setup_distances: dict[str, SetupDistance] = field(default_factory=dict)

    # Phase 5: Target profile
    target_profile: TargetProfile = field(default_factory=TargetProfile)

    # Phase 6: Historical
    historical: HistoricalContext = field(default_factory=HistoricalContext)

    # Phase 7: Physics reasoning
    physics: PhysicsReasoning = field(default_factory=PhysicsReasoning)

    # Aggregate understanding
    best_session_idx: int = 0
    worst_session_idx: int = 0
    authority_session_idx: int = 0
    reference_session_idx: int = 0   # best telemetry/diagnosis quality (for physics validation)
    current_session_idx: int = 0     # most recent session (what user is running now)
    latest_session_idx: int = 0
    solve_basis: str = "best_session"
    aggregate_measured: dict[str, float] = field(default_factory=dict)
    persistent_problems: list[str] = field(default_factory=list)
    resolved_problems: list[str] = field(default_factory=list)
    setup_fingerprints: list[SetupFingerprint] = field(default_factory=list)
    validation_clusters: list[ValidationCluster] = field(default_factory=list)
    candidate_vetoes: list[CandidateVeto] = field(default_factory=list)
    generated_candidates: list[SetupCandidate] = field(default_factory=list)
    solver_notes: list[str] = field(default_factory=list)
    authority_scores: list[dict[str, object]] = field(default_factory=list)
    normalization_scores: list[dict[str, object]] = field(default_factory=list)
    decision_trace: list[ParameterDecision] = field(default_factory=list)
    legal_validation: LegalValidation | None = None
    stint_datasets: list[object] = field(default_factory=list)
    stint_phase_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    stint_recommendations: list[dict[str, Any]] = field(default_factory=list)
    merged_stint_dataset: object | None = None
    stint_solve_result: object | None = None

    # Phase 8: Confidence-gated modifiers
    modifier_details: list[ModifierWithConfidence] = field(default_factory=list)

    # Reasoning log
    reasoning_log: list[str] = field(default_factory=list)

    # Final solve outputs
    final_modifiers: object | None = None
    final_step1: object | None = None
    final_step2: object | None = None
    final_step3: object | None = None
    final_step4: object | None = None
    final_step5: object | None = None
    final_step6: object | None = None
    final_supporting: object | None = None
    final_report: str = ""
    final_wing_angle: float = 0.0
    final_fuel_l: float = 0.0
    final_selected_candidate_family: str | None = None
    final_selected_candidate_score: float | None = None
    final_selected_candidate_applied: bool = False


@dataclass
class SkippedSession:
    """A session that could not be analyzed and was excluded."""
    label: str
    ibt_path: str
    reason: str


_FILENAME_TIMESTAMP_PATTERNS = (
    re.compile(r"(\d{4}-\d{2}-\d{2})[ _](\d{2})-(\d{2})-(\d{2})"),
    re.compile(r"(\d{4}-\d{2}-\d{2})[ _](\d{2}):(\d{2}):(\d{2})"),
)


def _parse_filename_timestamp(path: str | Path) -> float | None:
    stem = Path(path).stem
    for pattern in _FILENAME_TIMESTAMP_PATTERNS:
        match = pattern.search(stem)
        if match:
            date_str, hour, minute, second = match.groups()
            try:
                parsed = dt.datetime.fromisoformat(f"{date_str} {hour}:{minute}:{second}")
            except ValueError:
                return None
            return parsed.timestamp()
    return None


def _resolve_sort_metadata(path: str | Path) -> tuple[float, str]:
    parsed = _parse_filename_timestamp(path)
    if parsed is not None:
        return parsed, "filename"
    try:
        return Path(path).stat().st_mtime, "mtime"
    except OSError:
        return 0.0, "cli"


def _relabel_sessions(sessions: list[SessionSnapshot]) -> None:
    for idx, snap in enumerate(sessions):
        label = f"S{idx + 1}"
        snap.label = label
        snap.observation.session_id = label


def _sort_sessions_chronologically(state: ReasoningState) -> None:
    priority = {"filename": 0, "mtime": 1, "cli": 2}
    state.sessions.sort(
        key=lambda s: (
            priority.get(s.sort_source, 2),
            s.sort_timestamp,
            s.input_order,
        )
    )
    _relabel_sessions(state.sessions)
    state.setup_fingerprints = [
        snap.fingerprint if snap.fingerprint is not None else fingerprint_from_current_setup(snap.setup)
        for snap in state.sessions
    ]


def _settle_deviation(value: float) -> float:
    return abs(float(value) - 125.0)


def _metric_regressions(latest: SessionSnapshot, reference: SessionSnapshot) -> list[str]:
    latest_m = latest.measured
    ref_m = reference.measured
    regressions: list[str] = []

    if (latest_m.understeer_low_speed_deg - ref_m.understeer_low_speed_deg) >= 0.3:
        regressions.append(
            f"low_speed_understeer {latest_m.understeer_low_speed_deg:+.2f} vs {ref_m.understeer_low_speed_deg:+.2f}"
        )

    latest_slip = latest_m.rear_power_slip_ratio_p95 or latest_m.rear_slip_ratio_p95
    ref_slip = ref_m.rear_power_slip_ratio_p95 or ref_m.rear_slip_ratio_p95
    if (latest_slip - ref_slip) >= 0.01:
        regressions.append(f"rear_traction_slip {latest_slip:.3f} vs {ref_slip:.3f}")

    if (latest_m.front_rh_std_mm - ref_m.front_rh_std_mm) >= 0.5:
        regressions.append(f"front_rh_std {latest_m.front_rh_std_mm:.2f} vs {ref_m.front_rh_std_mm:.2f}")
    if (latest_m.rear_rh_std_mm - ref_m.rear_rh_std_mm) >= 0.5:
        regressions.append(f"rear_rh_std {latest_m.rear_rh_std_mm:.2f} vs {ref_m.rear_rh_std_mm:.2f}")

    latest_front_settle = usable_signal_value(latest_m, "front_rh_settle_time_ms")
    ref_front_settle = usable_signal_value(ref_m, "front_rh_settle_time_ms")
    latest_rear_settle = usable_signal_value(latest_m, "rear_rh_settle_time_ms")
    ref_rear_settle = usable_signal_value(ref_m, "rear_rh_settle_time_ms")

    if (
        latest_front_settle is not None
        and ref_front_settle is not None
        and (_settle_deviation(latest_front_settle) - _settle_deviation(ref_front_settle)) >= 20.0
    ):
        regressions.append(
            f"front_settle_dev {_settle_deviation(latest_front_settle):.0f}ms vs "
            f"{_settle_deviation(ref_front_settle):.0f}ms"
        )
    if (
        latest_rear_settle is not None
        and ref_rear_settle is not None
        and (_settle_deviation(latest_rear_settle) - _settle_deviation(ref_rear_settle)) >= 20.0
    ):
        regressions.append(
            f"rear_settle_dev {_settle_deviation(latest_rear_settle):.0f}ms vs "
            f"{_settle_deviation(ref_rear_settle):.0f}ms"
        )

    if (latest_m.bottoming_event_count_front_clean - ref_m.bottoming_event_count_front_clean) >= 2:
        regressions.append(
            f"front_clean_bottoming {latest_m.bottoming_event_count_front_clean} vs "
            f"{ref_m.bottoming_event_count_front_clean}"
        )
    if (latest_m.bottoming_event_count_rear_clean - ref_m.bottoming_event_count_rear_clean) >= 2:
        regressions.append(
            f"rear_clean_bottoming {latest_m.bottoming_event_count_rear_clean} vs "
            f"{ref_m.bottoming_event_count_rear_clean}"
        )

    if (latest_m.understeer_mean_deg - ref_m.understeer_mean_deg) >= 0.08:
        regressions.append(
            f"understeer_mean {latest_m.understeer_mean_deg:+.2f} vs {ref_m.understeer_mean_deg:+.2f}"
        )
    if (latest_m.body_slip_p95_deg - ref_m.body_slip_p95_deg) >= 0.20:
        regressions.append(
            f"body_slip {latest_m.body_slip_p95_deg:.2f} vs {ref_m.body_slip_p95_deg:.2f}"
        )

    return regressions


def _conditions_shifted(latest: SessionSnapshot, reference: SessionSnapshot) -> bool:
    fuel_shift = abs((latest.setup.fuel_l or 0.0) - (reference.setup.fuel_l or 0.0)) > 2.0
    air_latest = getattr(latest.measured, "air_temp_c", 0.0) or 0.0
    air_ref = getattr(reference.measured, "air_temp_c", 0.0) or 0.0
    track_latest = getattr(latest.measured, "track_temp_c", 0.0) or 0.0
    track_ref = getattr(reference.measured, "track_temp_c", 0.0) or 0.0
    wind_latest = getattr(latest.measured, "wind_speed_ms", 0.0) or 0.0
    wind_ref = getattr(reference.measured, "wind_speed_ms", 0.0) or 0.0
    return (
        fuel_shift
        or abs(air_latest - air_ref) > 5.0
        or abs(track_latest - track_ref) > 5.0
        or abs(wind_latest - wind_ref) > 3.0
    )


def _find_validation_cluster(
    clusters: list[ValidationCluster],
    fingerprint: SetupFingerprint,
) -> ValidationCluster | None:
    for cluster in clusters:
        if cluster.fingerprint.matches_candidate(fingerprint):
            return cluster
    return None


def _build_validation_clusters(state: ReasoningState) -> None:
    clusters: list[ValidationCluster] = []
    for idx, fp in enumerate(state.setup_fingerprints):
        cluster = _find_validation_cluster(clusters, fp)
        if cluster is None:
            cluster = ValidationCluster(fingerprint=fp)
            clusters.append(cluster)
        cluster.fingerprint = fp
        cluster.session_indices.append(idx)
        cluster.session_labels.append(state.sessions[idx].label)

    # Quality key: diagnosis assessment dominates, lap time breaks ties.
    _diag_rank = {"fast": 0, "competitive": 1, "compromised": 2, "dangerous": 3}

    def _session_quality_key(i: int) -> tuple:
        snap = state.sessions[i]
        diag = getattr(getattr(snap, "diagnosis", None), "assessment", "competitive")
        return (_diag_rank.get(diag, 1), snap.lap_time_s if snap.lap_time_s is not None else 999.0)

    for cluster in clusters:
        latest_idx = max(cluster.session_indices)
        best_cluster_idx = min(cluster.session_indices, key=_session_quality_key)
        cluster.latest_session_idx = latest_idx
        cluster.latest_session_label = state.sessions[latest_idx].label
        cluster.best_cluster_session_idx = best_cluster_idx
        cluster.best_cluster_session_label = state.sessions[best_cluster_idx].label

        other_indices = [i for i in range(len(state.sessions)) if i not in cluster.session_indices]
        if other_indices:
            ref_idx = min(other_indices, key=_session_quality_key)
            latest = state.sessions[latest_idx]
            reference = state.sessions[ref_idx]
            cluster.comparison_session_idx = ref_idx
            cluster.comparison_session_label = reference.label
            cluster.lap_delta_s = round(latest.lap_time_s - reference.lap_time_s, 3)
            cluster.metric_regressions = _metric_regressions(latest, reference)
            if cluster.lap_delta_s >= 0.10 and len(cluster.metric_regressions) >= 2:
                cluster.validated_failed = True
                cluster.penalty_mode = "soft" if _conditions_shifted(latest, reference) else "hard"
                cluster.reason = (
                    f"{latest.label} validated this setup and was {cluster.lap_delta_s:+.3f}s slower than "
                    f"{reference.label}; regressions: {', '.join(cluster.metric_regressions[:4])}"
                )
    clusters.sort(key=lambda c: c.latest_session_idx)
    state.validation_clusters = clusters


def _build_health_models(state: ReasoningState) -> None:
    healthy_sessions = [
        snap for snap in state.sessions
        if snap.session_context is not None
        and snap.session_context.comparable_to_baseline
        and snap.diagnosis.assessment in {"fast", "competitive"}
    ]
    if len(healthy_sessions) < 3:
        ranked = sorted(state.sessions, key=lambda snap: snap.lap_time_s)
        healthy_sessions = ranked[: min(3, len(ranked))]

    source_labels = [snap.label for snap in healthy_sessions]
    state.telemetry_envelope = build_telemetry_envelope(
        [snap.measured for snap in healthy_sessions],
        source_sessions=source_labels,
    )
    state.setup_cluster = build_setup_cluster(
        [snap.setup for snap in healthy_sessions],
        member_sessions=source_labels,
        label="healthy baseline cluster",
    )
    state.envelope_distances = {
        snap.label: compute_envelope_distance(snap.measured, state.telemetry_envelope)
        for snap in state.sessions
    }
    state.setup_distances = {
        snap.label: compute_setup_distance(snap.setup, state.setup_cluster)
        for snap in state.sessions
    }
    min_sample_gate = 3
    if state.telemetry_envelope is not None and state.telemetry_envelope.sample_count < min_sample_gate:
        state.reasoning_log.append("Telemetry envelope sample count below gate; distances are advisory only.")
    if state.setup_cluster is not None and len(state.setup_cluster.member_sessions) < min_sample_gate:
        state.reasoning_log.append("Setup cluster sample count below gate; distances are advisory only.")

    from analyzer.overhaul import assess_overhaul

    for snap in state.sessions:
        env_distance = state.envelope_distances.get(snap.label)
        setup_distance = state.setup_distances.get(snap.label)
        gated_env = env_distance.total_score if env_distance is not None and state.telemetry_envelope.sample_count >= min_sample_gate else None
        gated_setup = setup_distance.distance_score if setup_distance is not None and len(state.setup_cluster.member_sessions) >= min_sample_gate else None
        snap.diagnosis.overhaul_assessment = assess_overhaul(
            snap.diagnosis.state_issues,
            telemetry_envelope_distance=gated_env,
            setup_cluster_distance=gated_setup,
        )


def _selected_candidate_result(selected_candidate: SetupCandidate | None) -> object | None:
    if selected_candidate is None or not getattr(selected_candidate, "selectable", False):
        return None
    return getattr(selected_candidate, "result", None)


def _session_signal_quality_score(snapshot: SessionSnapshot) -> tuple[float, list[str]]:
    signal_map = getattr(snapshot.measured, "telemetry_signals", {}) or {}
    if not signal_map:
        return 0.45, ["no telemetry signal map available"]

    trusted = [sig.confidence for sig in signal_map.values() if sig.quality == "trusted" and sig.value is not None]
    proxy = [sig.confidence for sig in signal_map.values() if sig.quality == "proxy" and sig.value is not None]
    unresolved = [
        name for name, sig in signal_map.items()
        if sig.quality in {"unknown", "broken"} or sig.conflict_state != "clear"
    ]
    score = 0.0
    if trusted:
        score += min(0.75, sum(trusted) / len(trusted) * 0.75)
    if proxy:
        score += min(0.2, sum(proxy) / len(proxy) * 0.2)
    score = max(0.0, score - min(0.2, len(unresolved) * 0.02))
    notes = [
        f"trusted={len(trusted)}",
        f"proxy={len(proxy)}",
        f"unresolved={len(unresolved)}",
    ]
    if getattr(snapshot.measured, "metric_fallbacks", None):
        notes.append(f"fallbacks={len(snapshot.measured.metric_fallbacks)}")
    return round(min(1.0, score), 3), notes


def _compute_authority_scores(state: ReasoningState) -> None:
    lap_times = [s.lap_time_s for s in state.sessions]
    fastest = min(lap_times)
    lap_window = max(fastest * 0.015, 0.001)
    assessment_scores = {
        "fast": 1.0,
        "competitive": 0.8,
        "compromised": 0.45,
        "dangerous": 0.1,
    }
    authority_rows: list[dict[str, object]] = []
    for idx, snap in enumerate(state.sessions):
        lap_component = max(0.0, 1.0 - max(0.0, snap.lap_time_s - fastest) / lap_window)
        diagnosis_component = assessment_scores.get(snap.diagnosis.assessment, 0.6)
        context_component = snap.session_context.overall_score if snap.session_context is not None else 0.5
        signal_component, signal_notes = _session_signal_quality_score(snap)
        critical_count = sum(1 for problem in snap.diagnosis.problems if getattr(problem, "severity", "") == "critical")
        significant_count = sum(1 for problem in snap.diagnosis.problems if getattr(problem, "severity", "") == "significant")
        hard_failure_penalty = min(0.35, critical_count * 0.18 + significant_count * 0.05)
        state_risk = sum(
            getattr(issue, "severity", 0.0) * getattr(issue, "confidence", 0.0)
            for issue in getattr(snap.diagnosis, "state_issues", [])[:6]
        )
        state_component = max(0.0, 1.0 - min(1.0, state_risk / 3.0))
        envelope_penalty = (
            state.envelope_distances.get(snap.label).total_score
            if snap.label in state.envelope_distances and state.telemetry_envelope is not None and state.telemetry_envelope.sample_count >= 3
            else 0.0
        )
        setup_penalty = (
            state.setup_distances.get(snap.label).distance_score
            if snap.label in state.setup_distances and state.setup_cluster is not None and len(state.setup_cluster.member_sessions) >= 3
            else 0.0
        )
        score = (
            lap_component * 0.28
            + diagnosis_component * 0.2
            + context_component * 0.18
            + signal_component * 0.16
            + state_component * 0.18
        )
        score -= hard_failure_penalty
        score -= min(0.18, envelope_penalty * 0.035)
        score -= min(0.12, setup_penalty * 0.025)
        if snap.session_context is not None and not snap.session_context.comparable_to_baseline:
            score *= 0.82
        if any(
            cluster.validated_failed and snap.label in cluster.session_labels
            for cluster in state.validation_clusters
        ):
            score -= 0.12
        row = {
            "session_idx": idx,
            "session": snap.label,
            "score": round(score, 3),
            "lap_component": round(lap_component, 3),
            "diagnosis_component": round(diagnosis_component, 3),
            "context_component": round(context_component, 3),
            "signal_component": round(signal_component, 3),
            "state_component": round(state_component, 3),
            "hard_failure_penalty": round(hard_failure_penalty, 3),
            "state_risk": round(state_risk, 3),
            "envelope_distance": round(envelope_penalty, 3),
            "setup_distance": round(setup_penalty, 3),
            "notes": (
                list((snap.session_context.notes if snap.session_context is not None else [])[:4])
                + signal_notes
                + ([f"critical={critical_count}"] if critical_count else [])
                + ([f"state_risk={state_risk:.2f}"] if state_risk > 0 else [])
            ),
        }
        authority_rows.append(row)

    authority_rows.sort(key=lambda row: row["score"], reverse=True)
    state.authority_scores = authority_rows


def _resolve_authority_session(state: ReasoningState) -> None:
    state.latest_session_idx = max(len(state.sessions) - 1, 0)
    # Current session = always the most recent (what user has in the car)
    state.current_session_idx = state.latest_session_idx
    _compute_authority_scores(state)
    if state.authority_scores:
        top = state.authority_scores[0]
        state.authority_session_idx = int(top["session_idx"])
        # Reference = highest authority score winner (best telemetry quality)
        state.reference_session_idx = state.authority_session_idx
        state.solve_basis = "authority_score"
    else:
        state.authority_session_idx = state.best_session_idx
        state.reference_session_idx = state.best_session_idx
        state.solve_basis = "best_session"
    state.solver_notes = []
    if state.solve_basis == "authority_score" and state.authority_scores:
        top = state.authority_scores[0]
        state.solver_notes.append(
            f"Authority score selected {top['session']} ({top['score']:.3f}) over raw best lap."
        )
        for note in top["notes"][:2]:
            state.solver_notes.append(note)

    for cluster in state.validation_clusters:
        if cluster.latest_session_idx == state.latest_session_idx and cluster.validated_failed:
            # Veto: don't force authority = latest. Instead, flag the veto so solver
            # avoids reissuing that setup, but keep reference as the best quality session.
            state.solve_basis = "latest_validation_veto"
            mode = "soft" if cluster.penalty_mode == "soft" else "hard"
            state.solver_notes = []
            state.solver_notes.append(
                f"Validation {mode} veto on {cluster.latest_session_label}'s setup — "
                f"solver will avoid reissuing it."
            )
            state.solver_notes.append(cluster.reason)
            break
    skipped_summary = _skipped_session_summary(state)
    if skipped_summary:
        state.solver_notes.insert(0, skipped_summary)


def _build_aggregate_measured(state: ReasoningState) -> dict[str, float]:
    """Combine ALL sessions' telemetry into safety-binding and reliability-weighted metrics.

    Safety metrics (bottoming, travel, slip): worst-case across sessions (must solve for all).
    Balance metrics (understeer, oversteer): authority-score-weighted mean (most reliable).
    """
    if not state.sessions:
        return {}

    merged_stint = getattr(state, "merged_stint_dataset", None)
    if merged_stint is not None and getattr(merged_stint, "usable_laps", None):
        laps = list(merged_stint.usable_laps)
        weights = [
            max(0.05, float(getattr(lap.quality, "direct_weight", 1.0)) * (1.0 + 0.25 * float(getattr(lap, "progress", 0.0))))
            for lap in laps
        ]
        total_weight = sum(weights) or 1.0

        def _lap_worst(attr: str) -> float:
            vals = [float(getattr(lap.measured, attr, 0.0) or 0.0) for lap in laps]
            return max(vals) if vals else 0.0

        def _lap_weighted_mean(attr: str) -> float:
            total = 0.0
            for idx, lap in enumerate(laps):
                total += float(getattr(lap.measured, attr, 0.0) or 0.0) * weights[idx]
            return total / total_weight

        return {
            "front_heave_travel_used_pct": _lap_worst("front_heave_travel_used_pct"),
            "pitch_range_braking_deg": _lap_worst("pitch_range_braking_deg"),
            "bottoming_event_count_front_clean": _lap_worst("bottoming_event_count_front_clean"),
            "rear_rh_std_mm": _lap_worst("rear_rh_std_mm"),
            "bottoming_event_count_rear_clean": _lap_worst("bottoming_event_count_rear_clean"),
            "rear_power_slip_ratio_p95": _lap_worst("rear_power_slip_ratio_p95"),
            "body_slip_p95_deg": _lap_worst("body_slip_p95_deg"),
            "front_braking_lock_ratio_p95": _lap_worst("front_braking_lock_ratio_p95"),
            "understeer_low_speed_deg": _lap_weighted_mean("understeer_low_speed_deg"),
            "understeer_high_speed_deg": _lap_weighted_mean("understeer_high_speed_deg"),
        }

    # Build authority weights for reliability-weighted averaging
    score_map: dict[int, float] = {}
    for row in state.authority_scores:
        idx = int(row.get("session_idx", -1))
        score_map[idx] = float(row.get("score", 0.5) or 0.5)
    weights = [score_map.get(i, 0.5) for i in range(len(state.sessions))]
    total_weight = sum(weights) or 1.0

    def _worst_case(attr: str) -> float:
        vals = [float(getattr(s.measured, attr, 0) or 0) for s in state.sessions]
        return max(vals) if vals else 0.0

    def _weighted_mean(attr: str) -> float:
        total = 0.0
        for i, s in enumerate(state.sessions):
            val = float(getattr(s.measured, attr, 0) or 0)
            total += val * weights[i]
        return total / total_weight

    return {
        # Safety (worst-case across all sessions)
        "front_heave_travel_used_pct": _worst_case("front_heave_travel_used_pct"),
        "pitch_range_braking_deg": _worst_case("pitch_range_braking_deg"),
        "bottoming_event_count_front_clean": _worst_case("bottoming_event_count_front_clean"),
        "rear_rh_std_mm": _worst_case("rear_rh_std_mm"),
        "bottoming_event_count_rear_clean": _worst_case("bottoming_event_count_rear_clean"),
        "rear_power_slip_ratio_p95": _worst_case("rear_power_slip_ratio_p95"),
        "body_slip_p95_deg": _worst_case("body_slip_p95_deg"),
        "front_braking_lock_ratio_p95": _worst_case("front_braking_lock_ratio_p95"),
        # Balance (authority-weighted mean — most reliable measurement)
        "understeer_low_speed_deg": _weighted_mean("understeer_low_speed_deg"),
        "understeer_high_speed_deg": _weighted_mean("understeer_high_speed_deg"),
    }


# ── Phase 1: Session analysis ──────────────────────────────────


def _analyze_session(
    ibt_path: str,
    car: CarModel,
    label: str,
    min_lap_time: float = 60.0,
    *,
    stint: bool = False,
    stint_select: str = "all",
    stint_max_laps: int = 40,
    stint_threshold: float = 1.5,
) -> SessionSnapshot:
    """Run full analysis on one IBT file."""
    ibt = IBTFile(ibt_path)
    sort_timestamp, sort_source = _resolve_sort_metadata(ibt_path)
    track = build_profile(ibt_path)
    setup = CurrentSetup.from_ibt(ibt, car_canonical=car.canonical_name)
    measured = extract_measurements(ibt_path, car, min_lap_time=min_lap_time)
    live_override_notes = apply_live_control_overrides(setup, measured)
    setup_schema = build_setup_schema(
        car=car,
        ibt_path=ibt_path,
        current_setup=setup,
        measured=measured,
    )

    lap_indices = ibt.best_lap_indices(min_time=min_lap_time)
    corners = []
    if lap_indices:
        start, end = lap_indices
        corners = segment_lap(ibt, start, end, car=car, tick_rate=ibt.tick_rate)

    driver = analyze_driver(ibt, corners, car, tick_rate=ibt.tick_rate)
    refine_driver_with_measured(driver, measured)

    adaptive = compute_adaptive_thresholds(track, car, driver)
    diag = diagnose(
        measured,
        setup,
        car,
        thresholds=adaptive,
        driver=driver,
        corners=corners,
    )
    session_context = build_session_context(measured, setup, diag)

    obs = build_observation(
        session_id=label,
        ibt_path=ibt_path,
        car_name=car.canonical_name,
        track_profile=track,
        measured_state=measured,
        current_setup=setup,
        driver_profile_obj=driver,
        diagnosis_obj=diag,
        corners=corners,
    )

    stint_dataset = None
    stint_evolution = None
    if stint:
        stint_dataset = build_stint_dataset(
            ibt_path=ibt_path,
            car=car,
            stint_select=stint_select,
            stint_max_laps=stint_max_laps,
            threshold_pct=stint_threshold,
            min_lap_time=min_lap_time,
            ibt=ibt,
            source_label=label,
        )
        stint_evolution = dataset_to_evolution(stint_dataset)

    return SessionSnapshot(
        label=label,
        ibt_path=ibt_path,
        setup=setup,
        setup_schema=setup_schema,
        measured=measured,
        driver=driver,
        diagnosis=diag,
        session_context=session_context,
        track=track,
        corners=corners,
        observation=obs,
        lap_time_s=measured.lap_time_s,
        lap_number=measured.lap_number,
        input_order=max(int(label[1:]) - 1, 0),
        sort_timestamp=sort_timestamp,
        sort_source=sort_source,
        fingerprint=fingerprint_from_current_setup(setup),
        live_override_notes=live_override_notes,
        stint_dataset=stint_dataset,
        stint_evolution=stint_evolution,
    )


def _skip_session(
    state: ReasoningState,
    *,
    label: str,
    ibt_path: str,
    error: Exception,
    log,
) -> None:
    reason = str(error).strip() or error.__class__.__name__
    state.skipped_sessions.append(
        SkippedSession(
            label=label,
            ibt_path=ibt_path,
            reason=reason,
        )
    )
    log(f"  Skipping {label}: {Path(ibt_path).name} ({reason})")


def _load_sessions_into_state(
    state: ReasoningState,
    *,
    ibt_paths: list[str],
    car: CarModel,
    min_lap_time: float,
    stint: bool,
    stint_select: str,
    stint_max_laps: int,
    stint_threshold: float,
    log,
) -> None:
    for i, ibt_path in enumerate(ibt_paths):
        label = f"S{i+1}"
        log(f"[Phase 1] Reading {label}: {Path(ibt_path).name}...")
        try:
            snap = _analyze_session(
                ibt_path,
                car,
                label,
                min_lap_time=min_lap_time,
                stint=stint,
                stint_select=stint_select,
                stint_max_laps=stint_max_laps,
                stint_threshold=stint_threshold,
            )
        except Exception as exc:
            _skip_session(
                state,
                label=label,
                ibt_path=ibt_path,
                error=exc,
                log=log,
            )
            continue

        state.sessions.append(snap)
        log(
            f"  Lap {snap.lap_number}: {snap.lap_time_s:.3f}s | "
            f"{snap.driver.style} | {len(snap.diagnosis.problems)} problems | "
            f"{len(snap.corners)} corners"
        )
        for note in snap.live_override_notes:
            log(f"    Live override: {note}")
        if stint and snap.stint_dataset is not None:
            log(
                f"    Stint: {len(snap.stint_dataset.selected_segments)} segment(s), "
                f"{len(snap.stint_dataset.usable_laps)} usable laps, "
                f"{len(snap.stint_dataset.evaluation_laps)} scored"
            )


def _skipped_session_summary(state: ReasoningState) -> str | None:
    if not state.skipped_sessions:
        return None
    skipped_names = ", ".join(
        f"{Path(skipped.ibt_path).name} ({skipped.reason})"
        for skipped in state.skipped_sessions[:3]
    )
    if len(state.skipped_sessions) > 3:
        skipped_names += f", +{len(state.skipped_sessions) - 3} more"
    return f"Skipped {len(state.skipped_sessions)} unanalyzable session(s): {skipped_names}"


def _setup_schema_dump_payload(state: ReasoningState, car: CarModel) -> dict[str, object]:
    authority_label = None
    authority_schema = None
    if state.sessions:
        authority = state.sessions[state.authority_session_idx]
        authority_label = authority.label
        authority_schema = (
            authority.setup_schema.to_dict()
            if authority.setup_schema is not None
            else None
        )
    return {
        "car": car.name,
        "car_canonical": car.canonical_name,
        "authority_session": authority_label,
        "authority_setup_schema": authority_schema,
        "sessions": [
            {
                "label": snap.label,
                "ibt_path": snap.ibt_path,
                "live_override_notes": list(snap.live_override_notes),
                "setup_schema": (
                    snap.setup_schema.to_dict()
                    if snap.setup_schema is not None
                    else None
                ),
            }
            for snap in state.sessions
        ],
        "skipped_sessions": [
            {
                "label": skipped.label,
                "ibt_path": skipped.ibt_path,
                "reason": skipped.reason,
            }
            for skipped in state.skipped_sessions
        ],
    }


def _stint_selection_payload(dataset: Any | None) -> dict[str, object] | None:
    if dataset is None:
        return None
    return {
        "mode": getattr(dataset, "stint_select", "all"),
        "segments": [
            {
                "segment_id": segment.segment_id,
                "start_lap": segment.start_lap,
                "end_lap": segment.end_lap,
                "lap_count": segment.lap_count,
                "source_label": segment.source_label,
                "break_reasons": list(segment.break_reasons),
            }
            for segment in getattr(dataset, "segments", [])
        ],
        "selected_segments": [
            {
                "segment_id": segment.segment_id,
                "start_lap": segment.start_lap,
                "end_lap": segment.end_lap,
                "lap_count": segment.lap_count,
                "source_label": segment.source_label,
            }
            for segment in getattr(dataset, "selected_segments", [])
        ],
        "notes": list(getattr(dataset, "selection_notes", [])),
    }


def _stint_lap_payload(dataset: Any | None) -> list[dict[str, object]]:
    if dataset is None:
        return []
    return [
        {
            "lap_number": lap.lap_number,
            "lap_time_s": lap.lap_time_s,
            "fuel_level_l": lap.fuel_level_l,
            "progress": lap.progress,
            "phase": lap.phase,
            "source_label": lap.source_label,
            "quality": {
                "status": lap.quality.status,
                "direct_weight": lap.quality.direct_weight,
                "trend_weight": lap.quality.trend_weight,
                "flags": list(lap.quality.flags),
            },
            "selected_for_evaluation": lap.selected_for_evaluation,
        }
        for lap in getattr(dataset, "usable_laps", [])
    ]


# ── Phase 2: All-pairs delta analysis ──────────────────────────


def _compute_delta_weight(
    delta: SessionDelta,
    before: SessionSnapshot,
    after: SessionSnapshot,
) -> tuple[float, SessionNormalization]:
    """Compute quality weight for a delta based on experiment control and normalization."""
    if delta.controlled_experiment:
        base = 1.0
    else:
        n = max(delta.num_setup_changes, 1)
        if n <= 2:
            base = 0.5 / n
        else:
            base = 0.3 / n

    normalization = build_session_normalization(before, after)
    weight = base * normalization.overall_score
    if not normalization.comparable:
        weight *= 0.5
    return weight, normalization


def _all_pairs_deltas(state: ReasoningState) -> None:
    """Compare every pair of sessions, weighted by quality."""
    n = len(state.sessions)
    for i in range(n):
        for j in range(i + 1, n):
            obs_i = state.sessions[i].observation
            obs_j = state.sessions[j].observation
            delta = detect_delta(obs_i, obs_j)
            weight, normalization = _compute_delta_weight(delta, state.sessions[i], state.sessions[j])
            state.weighted_deltas.append(WeightedDelta(
                delta=delta, pair=(i, j), weight=weight, normalization=normalization,
            ))
            state.normalization_scores.append(
                {
                    "before": state.sessions[i].label,
                    "after": state.sessions[j].label,
                    "overall_score": normalization.overall_score,
                    "comparable": normalization.comparable,
                    "notes": normalization.notes,
                }
            )
            _update_learnings_weighted(state, delta, weight, i, j)


def _update_learnings_weighted(
    state: ReasoningState,
    delta: SessionDelta,
    weight: float,
    before_idx: int,
    after_idx: int,
) -> None:
    """Update parameter learnings from one delta with quality weighting."""
    after = state.sessions[after_idx]
    lap_improved = delta.lap_time_delta_s < -0.05
    lap_worsened = delta.lap_time_delta_s > 0.05

    for sc in delta.setup_changes:
        if sc.significance == "trivial":
            continue
        if not isinstance(sc.delta, (int, float)):
            continue

        param = sc.parameter
        if param not in state.parameter_learnings:
            state.parameter_learnings[param] = ParameterLearning(parameter=param)
        pl = state.parameter_learnings[param]

        pl.values_seen.append(float(sc.after))
        pl.lap_times_at_value.append(after.lap_time_s)

        if after.lap_time_s < pl.best_lap_time:
            pl.best_lap_time = after.lap_time_s
            pl.best_value = float(sc.after)

        # Lap time sensitivity: ms per unit of this parameter
        if abs(sc.delta) > 1e-6 and abs(delta.lap_time_delta_s) > 0.01:
            sensitivity = (delta.lap_time_delta_s * 1000.0) / sc.delta
            # Weighted running average
            old_n = pl.sensitivity_samples
            pl.lap_time_sensitivity_ms = (
                (pl.lap_time_sensitivity_ms * old_n + sensitivity * weight) /
                (old_n + weight)
            )
            pl.sensitivity_samples += 1

        increased = sc.delta > 0

        # Check which metrics improved/worsened
        for hyp in delta.hypotheses:
            if hyp.cause_param != param:
                continue
            if hyp.confidence < 0.3:
                continue
            metric = hyp.effect_metric
            if metric not in pl.affects:
                pl.affects[metric] = []
            pl.affects[metric].append(hyp.effect_delta)

        # Directional evidence with weighting
        key_metrics_improved = sum(
            1 for h in delta.hypotheses
            if h.cause_param == param and h.direction_match and h.confidence >= 0.5
        )
        key_metrics_hurt = sum(
            1 for h in delta.hypotheses
            if h.cause_param == param and not h.direction_match and h.confidence >= 0.5
        )

        if increased:
            if lap_improved or (key_metrics_improved > key_metrics_hurt and not lap_worsened):
                pl.increase_helped_weight += weight
            elif lap_worsened or key_metrics_hurt > key_metrics_improved:
                pl.increase_hurt_weight += weight
        else:
            if lap_improved or (key_metrics_improved > key_metrics_hurt and not lap_worsened):
                pl.decrease_helped_weight += weight
            elif lap_worsened or key_metrics_hurt > key_metrics_improved:
                pl.decrease_hurt_weight += weight


def _determine_directions(state: ReasoningState) -> None:
    """For each parameter, determine recommended direction from accumulated evidence."""
    for param, pl in state.parameter_learnings.items():
        total_inc = pl.increase_helped_weight + pl.increase_hurt_weight
        total_dec = pl.decrease_helped_weight + pl.decrease_hurt_weight
        total = total_inc + total_dec

        if total < 0.1:
            pl.direction = "unknown"
            pl.confidence = 0.0
            pl.reasoning = "No directional evidence"
            continue

        inc_score = pl.increase_helped_weight - pl.increase_hurt_weight
        dec_score = pl.decrease_helped_weight - pl.decrease_hurt_weight

        if inc_score > dec_score and inc_score > 0:
            pl.direction = "increase"
            pl.confidence = min(1.0, inc_score / max(total, 0.1))
            pl.reasoning = (
                f"Increasing helped {pl.increase_helped_weight:.1f}w, "
                f"hurt {pl.increase_hurt_weight:.1f}w across {len(state.weighted_deltas)} pairs"
            )
        elif dec_score > inc_score and dec_score > 0:
            pl.direction = "decrease"
            pl.confidence = min(1.0, dec_score / max(total, 0.1))
            pl.reasoning = (
                f"Decreasing helped {pl.decrease_helped_weight:.1f}w, "
                f"hurt {pl.decrease_hurt_weight:.1f}w across {len(state.weighted_deltas)} pairs"
            )
        elif total >= 0.5:
            pl.direction = "hold"
            pl.confidence = 0.3
            pl.reasoning = (
                f"Mixed evidence: inc helped/hurt "
                f"{pl.increase_helped_weight:.1f}/{pl.increase_hurt_weight:.1f}, "
                f"dec helped/hurt "
                f"{pl.decrease_helped_weight:.1f}/{pl.decrease_hurt_weight:.1f}"
            )
        else:
            pl.direction = "unknown"
            pl.confidence = 0.1
            pl.reasoning = f"Insufficient evidence (total weight {total:.2f})"


# ── Phase 3: Corner profiling ──────────────────────────────────


def _find_matching_corner(
    ref_mid: float,
    corners: list[CornerAnalysis],
    *,
    tolerance_m: float,
) -> CornerAnalysis | None:
    matched = None
    best_dist = tolerance_m
    for corner in corners:
        corner_mid = (corner.lap_dist_start_m + corner.lap_dist_end_m) / 2.0
        dist = abs(corner_mid - ref_mid)
        if dist < best_dist:
            best_dist = dist
            matched = corner
    return matched


def _corner_component_losses(ref_corner: CornerAnalysis, corner: CornerAnalysis) -> tuple[float, float, float, float]:
    entry_speed_deficit_ms = max(0.0, (ref_corner.entry_speed_kph - corner.entry_speed_kph) / 3.6)
    brake_overlap_deficit = max(0.0, corner.trail_brake_pct - ref_corner.trail_brake_pct)
    entry_loss = min(1.5, brake_overlap_deficit * max(ref_corner.entry_phase_s, 0.1) + entry_speed_deficit_ms / 12.0)

    apex_speed_deficit_ms = max(0.0, (ref_corner.apex_speed_kph - corner.apex_speed_kph) / 3.6)
    apex_avg_speed = max((ref_corner.apex_speed_kph + corner.apex_speed_kph) / 7.2, 5.0)
    apex_loss = min(
        1.5,
        apex_speed_deficit_ms * max(ref_corner.apex_phase_s, corner.apex_phase_s, 0.1) / apex_avg_speed,
    )

    throttle_delay_deficit = max(0.0, corner.throttle_delay_s - ref_corner.throttle_delay_s)
    exit_speed_deficit_ms = max(0.0, (ref_corner.exit_speed_kph - corner.exit_speed_kph) / 3.6)
    exit_avg_speed = max((ref_corner.exit_speed_kph + corner.exit_speed_kph) / 7.2, 8.0)
    exit_loss = min(
        1.5,
        throttle_delay_deficit
        + exit_speed_deficit_ms * max(ref_corner.exit_phase_s, corner.exit_phase_s, 0.1) / exit_avg_speed,
    )

    total = min(2.5, entry_loss + apex_loss + exit_loss)
    if total <= 0.0:
        return 0.0, 0.0, 0.0, 0.0
    scale = total / max(entry_loss + apex_loss + exit_loss, 1e-9)
    return entry_loss * scale, apex_loss * scale, exit_loss * scale, total


def _scale_corner_budget(
    profiles: list[CornerProfile],
    sessions: list[SessionSnapshot],
    ref_idx: int,
) -> None:
    n = len(sessions)
    totals = [0.0] * n
    for cp in profiles:
        for idx in range(min(n, len(cp.time_loss_per_session))):
            total = cp.time_loss_per_session[idx]
            if total is not None:
                totals[idx] += total

    ref_lap = sessions[ref_idx].lap_time_s
    for idx, session in enumerate(sessions):
        if idx == ref_idx:
            continue
        actual_delta = max(0.0, session.lap_time_s - ref_lap)
        if actual_delta <= 0.0:
            continue
        budget = actual_delta * 1.25
        total = totals[idx]
        if total <= budget or total <= 0.0:
            continue
        scale = budget / total
        for cp in profiles:
            if idx >= len(cp.time_loss_per_session) or cp.time_loss_per_session[idx] is None:
                continue
            cp.time_loss_per_session[idx] *= scale
            if idx < len(cp.entry_loss_per_session) and cp.entry_loss_per_session[idx] is not None:
                cp.entry_loss_per_session[idx] *= scale
            if idx < len(cp.apex_loss_per_session) and cp.apex_loss_per_session[idx] is not None:
                cp.apex_loss_per_session[idx] *= scale
            if idx < len(cp.exit_loss_per_session) and cp.exit_loss_per_session[idx] is not None:
                cp.exit_loss_per_session[idx] *= scale


def _match_corners_across_sessions(
    sessions: list[SessionSnapshot],
    *,
    reference_idx: int,
) -> list[CornerProfile]:
    """Match corners across sessions and build bounded opportunity components."""
    if not sessions or reference_idx >= len(sessions) or not sessions[reference_idx].corners:
        return []

    ref_corners = sessions[reference_idx].corners
    profiles: list[CornerProfile] = []
    match_tolerance_m = 50.0

    for ref_corner in ref_corners:
        mid = (ref_corner.lap_dist_start_m + ref_corner.lap_dist_end_m) / 2.0
        cp = CornerProfile(
            corner_id=ref_corner.corner_id,
            lap_dist_m=mid,
            direction=ref_corner.direction,
            speed_class=ref_corner.speed_class,
        )

        for sess_idx, sess in enumerate(sessions):
            matched = ref_corner if sess_idx == reference_idx else _find_matching_corner(
                mid,
                sess.corners,
                tolerance_m=match_tolerance_m,
            )

            if matched is None:
                cp.understeer_per_session.append(None)
                cp.body_slip_per_session.append(None)
                cp.time_loss_per_session.append(None)
                cp.entry_loss_per_session.append(None)
                cp.apex_loss_per_session.append(None)
                cp.exit_loss_per_session.append(None)
                cp.shock_vel_front_per_session.append(None)
                cp.shock_vel_rear_per_session.append(None)
                cp.front_rh_min_per_session.append(None)
                cp.kerb_severity_per_session.append(None)
                cp.platform_flags_per_session.append([])
                cp.traction_flags_per_session.append([])
                continue

            if sess_idx == reference_idx:
                entry_loss = 0.0
                apex_loss = 0.0
                exit_loss = 0.0
                total_loss = 0.0
            else:
                entry_loss, apex_loss, exit_loss, total_loss = _corner_component_losses(ref_corner, matched)

            cp.understeer_per_session.append(matched.understeer_mean_deg)
            cp.body_slip_per_session.append(matched.body_slip_peak_deg)
            cp.time_loss_per_session.append(total_loss)
            cp.entry_loss_per_session.append(entry_loss)
            cp.apex_loss_per_session.append(apex_loss)
            cp.exit_loss_per_session.append(exit_loss)
            cp.shock_vel_front_per_session.append(matched.front_shock_vel_p95_mps)
            cp.shock_vel_rear_per_session.append(matched.rear_shock_vel_p95_mps)
            cp.front_rh_min_per_session.append(matched.front_rh_min_mm)
            cp.kerb_severity_per_session.append(matched.kerb_severity_max)
            cp.platform_flags_per_session.append(list(getattr(matched, "platform_risk_flags", [])))
            cp.traction_flags_per_session.append(list(getattr(matched, "traction_risk_flags", [])))

        profiles.append(cp)

    _scale_corner_budget(profiles, sessions, reference_idx)
    for cp in profiles:
        cp._compute_aggregates()
    return profiles


def _build_corner_profiles(state: ReasoningState) -> None:
    """Build corner profiles and identify top weakness corners."""
    state.corner_profiles = _match_corners_across_sessions(
        state.sessions,
        reference_idx=state.reference_session_idx,
    )

    # Top 5 weakness corners by bounded opportunity
    weakness = [cp for cp in state.corner_profiles if cp.is_consistent_weakness]
    weakness.sort(key=lambda cp: cp.mean_time_loss, reverse=True)
    state.top_weakness_corners = weakness[:5]


# ── Phase 4: Speed-regime analysis ─────────────────────────────


def _analyze_speed_regimes(state: ReasoningState) -> None:
    """Separate high-speed aero problems from low-speed mechanical problems."""
    sra = state.speed_regime

    # Understeer by speed regime from MeasuredState
    hs_us = [s.measured.understeer_high_speed_deg for s in state.sessions]
    ls_us = [s.measured.understeer_low_speed_deg for s in state.sessions]
    sra.hs_understeer_mean = float(np.mean(hs_us)) if hs_us else 0.0
    sra.ls_understeer_mean = float(np.mean(ls_us)) if ls_us else 0.0
    sra.understeer_gradient = sra.hs_understeer_mean - sra.ls_understeer_mean

    # LLTD by speed regime
    hs_lltd = [s.measured.lltd_high_speed for s in state.sessions
               if hasattr(s.measured, 'lltd_high_speed') and s.measured.lltd_high_speed]
    ls_lltd = [s.measured.lltd_low_speed for s in state.sessions
               if hasattr(s.measured, 'lltd_low_speed') and s.measured.lltd_low_speed]
    sra.hs_lltd_mean = float(np.mean(hs_lltd)) if hs_lltd else 0.0
    sra.ls_lltd_mean = float(np.mean(ls_lltd)) if ls_lltd else 0.0

    # RH stability at high speed
    hs_rh = [s.measured.front_rh_std_hs_mm for s in state.sessions
             if hasattr(s.measured, 'front_rh_std_hs_mm') and s.measured.front_rh_std_hs_mm]
    sra.hs_rh_std_mean = float(np.mean(hs_rh)) if hs_rh else 0.0

    # Time loss by speed class from corner profiles
    for cp in state.corner_profiles:
        valid_losses = [t for t in cp.time_loss_per_session if t is not None]
        if not valid_losses:
            continue
        mean_loss = float(np.mean(valid_losses))
        if cp.speed_class == "high":
            sra.hs_time_loss_total += mean_loss
        elif cp.speed_class == "low":
            sra.ls_time_loss_total += mean_loss
        else:
            sra.mid_time_loss_total += mean_loss

    sra._determine_dominant()


# ── Phase 5: Target telemetry profile ──────────────────────────


# Rough lap time impact estimates per metric (ms per unit improvement)
_METRIC_SENSITIVITY_MS: dict[str, float] = {
    "front_rh_std_mm": 50.0,        # per mm reduction in RH variance
    "rear_rh_std_mm": 30.0,
    "bottoming_event_count_front": 15.0,  # per event reduction
    "bottoming_event_count_rear": 10.0,
    "understeer_mean_deg": 50.0,     # per degree reduction
    "understeer_high_speed_deg": 80.0,
    "understeer_low_speed_deg": 40.0,
    "body_slip_p95_deg": 30.0,
    "front_shock_vel_p99_mps": 100.0,  # per m/s reduction
    "rear_shock_vel_p99_mps": 60.0,
    "peak_lat_g_measured": 200.0,    # per g improvement
    "speed_max_kph": 5.0,            # per kph improvement
    "yaw_rate_correlation": 100.0,   # per 0.01 improvement
    "front_rh_settle_time_ms": 0.5,  # per ms closer to 125
    "rear_rh_settle_time_ms": 0.3,
    "front_carcass_mean_c": 2.0,     # per degree closer to 92.5
    "rear_carcass_mean_c": 2.0,
    "front_pressure_mean_kpa": 3.0,  # per kPa closer to 165
    "rear_pressure_mean_kpa": 3.0,
}


def _build_target_profile(state: ReasoningState) -> None:
    """Construct ideal car state by cherry-picking best metrics."""
    tp = state.target_profile
    n = len(state.sessions)

    for attr, polarity, target_val in METRIC_CATALOG:
        values = []
        for i, s in enumerate(state.sessions):
            if attr in {"front_rh_settle_time_ms", "rear_rh_settle_time_ms"}:
                v = usable_signal_value(s.measured, attr)
            else:
                v = getattr(s.measured, attr, None)
            if v is not None and isinstance(v, (int, float)):
                values.append((i, float(v)))

        if not values:
            continue

        if polarity == "lower":
            best_idx, best_val = min(values, key=lambda x: x[1])
            # Gap: how far the worst session is from the best
            worst_val = max(v for _, v in values)
            gap = worst_val - best_val
        elif polarity == "higher":
            best_idx, best_val = max(values, key=lambda x: x[1])
            worst_val = min(v for _, v in values)
            gap = best_val - worst_val
        else:  # target
            assert target_val is not None
            best_idx, best_val = min(values, key=lambda x: abs(x[1] - target_val))
            gap = abs(best_val - target_val)

        if gap < 1e-6:
            continue

        sensitivity = _METRIC_SENSITIVITY_MS.get(attr, 10.0)
        estimated_ms = gap * sensitivity

        tp.priority_gaps.append(TargetGap(
            metric=attr,
            best_value=best_val,
            best_session_idx=best_idx,
            gap_from_ideal=gap,
            estimated_ms_per_lap=estimated_ms,
        ))

    # Sort by estimated ms/lap impact
    tp.priority_gaps.sort(key=lambda g: g.estimated_ms_per_lap, reverse=True)


# ── Phase 6: Historical integration ───────────────────────────


def _integrate_historical(state: ReasoningState, car: CarModel) -> None:
    """Query learner knowledge base for historical context."""
    try:
        from learner.knowledge_store import KnowledgeStore
        from learner.recall import KnowledgeRecall
    except ImportError:
        return

    hc = state.historical
    store = KnowledgeStore()
    recall = KnowledgeRecall(store)

    # Use reference session (authority-selected) for track identification; fall back to best.
    ref_idx = state.reference_session_idx if state.reference_session_idx is not None else state.best_session_idx
    best = state.sessions[ref_idx]
    car_name = car.canonical_name
    track_name = best.track.track_name

    hc.session_count = recall.session_count(car=car_name, track=track_name)
    if hc.session_count < 3:
        return

    hc.has_data = True

    # Corrections
    hc.corrections = recall.get_corrections(car_name, track_name)
    hc.prediction_corrections = recall.get_prediction_corrections(car_name, track_name)

    # Most impactful parameters
    impact = recall.most_impactful_parameters(car_name, track_name)
    if impact.answer:
        hc.impactful_parameters = impact.answer

    # Insights
    insights = recall.get_insights(car_name, track_name)
    if insights.answer:
        hc.insights = insights.answer
        # Check for recurring problems
        rec = insights.answer.get("recurring_problems", [])
        if rec:
            hc.recurring_problems = rec

    # Historical evidence for parameters we have learnings about
    for param in list(state.parameter_learnings.keys())[:10]:
        for direction in ["+", "-"]:
            result = recall.what_happened_when(car_name, track_name, param, direction)
            if result.answer:
                key = f"{param}:{direction}"
                hc.historical_evidence[key] = result.answer


# ── Phase 7: Physics reasoning ─────────────────────────────────


def _run_physics_validations(state: ReasoningState) -> None:
    """Cross-validate findings using physics chains."""
    pr = state.physics
    best = state.sessions[state.reference_session_idx]
    m = best.measured

    # 7a. Cross-validation chains

    # Camber validation: understeer + inner tyre hot → camber issue
    us_mean = float(np.mean([s.measured.understeer_mean_deg for s in state.sessions]))
    front_spreads = [
        (s.measured.front_temp_spread_lf_c, s.measured.front_temp_spread_rf_c)
        for s in state.sessions
    ]
    avg_inner_hot = float(np.mean([
        min(lf, rf) for lf, rf in front_spreads  # negative spread = inner hotter
    ])) if front_spreads else 0.0

    if us_mean > 1.5 and avg_inner_hot < -3.0:
        pr.validations.append(ValidationChain(
            hypothesis="Excessive front camber causing understeer",
            confirmed=True,
            evidence=f"Understeer {us_mean:.1f}° + inner tyre hotter by {abs(avg_inner_hot):.1f}°C",
            confidence=0.7,
        ))
    elif us_mean > 1.5 and avg_inner_hot >= 0:
        pr.validations.append(ValidationChain(
            hypothesis="Front camber is not the understeer cause",
            confirmed=True,
            evidence=f"Understeer {us_mean:.1f}° but inner tyre not hot (spread {avg_inner_hot:+.1f}°C)",
            confidence=0.6,
        ))

    # Bottoming source validation: kerb vs clean
    avg_kerb_bottoming = float(np.mean([
        s.measured.bottoming_event_count_front_kerb for s in state.sessions
        if hasattr(s.measured, "bottoming_event_count_front_kerb")
        and s.measured.bottoming_event_count_front_kerb is not None
    ])) if any(
        hasattr(s.measured, "bottoming_event_count_front_kerb")
        and s.measured.bottoming_event_count_front_kerb is not None
        for s in state.sessions
    ) else 0.0
    avg_clean_bottoming = float(np.mean([
        s.measured.bottoming_event_count_front_clean for s in state.sessions
        if hasattr(s.measured, "bottoming_event_count_front_clean")
        and s.measured.bottoming_event_count_front_clean is not None
    ])) if any(
        hasattr(s.measured, "bottoming_event_count_front_clean")
        and s.measured.bottoming_event_count_front_clean is not None
        for s in state.sessions
    ) else 0.0

    avg_kerb_bottoming += float(np.mean([
        s.measured.bottoming_event_count_rear_kerb for s in state.sessions
        if hasattr(s.measured, "bottoming_event_count_rear_kerb")
        and s.measured.bottoming_event_count_rear_kerb is not None
    ])) if any(
        hasattr(s.measured, "bottoming_event_count_rear_kerb")
        and s.measured.bottoming_event_count_rear_kerb is not None
        for s in state.sessions
    ) else 0.0
    avg_clean_bottoming += float(np.mean([
        s.measured.bottoming_event_count_rear_clean for s in state.sessions
        if hasattr(s.measured, "bottoming_event_count_rear_clean")
        and s.measured.bottoming_event_count_rear_clean is not None
    ])) if any(
        hasattr(s.measured, "bottoming_event_count_rear_clean")
        and s.measured.bottoming_event_count_rear_clean is not None
        for s in state.sessions
    ) else 0.0

    if avg_kerb_bottoming > avg_clean_bottoming * 2 and avg_kerb_bottoming > 2:
        pr.validations.append(ValidationChain(
            hypothesis="Bottoming is kerb-induced, not clean-track",
            confirmed=True,
            evidence=f"Kerb bottoming {avg_kerb_bottoming:.0f} >> clean {avg_clean_bottoming:.0f}",
            confidence=0.8,
        ))
    elif avg_clean_bottoming > 3:
        pr.validations.append(ValidationChain(
            hypothesis="Clean-track bottoming requires heave floor raise",
            confirmed=True,
            evidence=f"Clean bottoming {avg_clean_bottoming:.0f} events (kerb: {avg_kerb_bottoming:.0f})",
            confidence=0.7,
        ))

    # Speed gradient validation
    speed_gradient = state.speed_regime.understeer_gradient
    if abs(speed_gradient) > 0.5:
        pr.validations.append(ValidationChain(
            hypothesis="Aero balance issue (not just LLTD)" if speed_gradient > 0
                else "Low-speed mechanical balance issue",
            confirmed=True,
            evidence=f"Speed gradient {speed_gradient:+.2f}° (HS-LS understeer)",
            confidence=0.7 if abs(speed_gradient) > 1.0 else 0.5,
        ))

    # Shock asymmetry check
    for s in state.sessions:
        lf_shock = getattr(s.measured, 'front_shock_vel_p95_mps', 0) or 0
        rf_shock = getattr(s.measured, 'front_shock_vel_p95_mps', 0) or 0
        # These are typically averaged, so we check LR vs RR for rear
        lr_shock = getattr(s.measured, 'rear_shock_vel_p95_mps', 0) or 0
        rr_shock = getattr(s.measured, 'rear_shock_vel_p95_mps', 0) or 0
        # Can't reliably detect L/R asymmetry from averaged values
        break


def _score_categories(state: ReasoningState) -> None:
    """Score best session across performance categories (adapted from score.py)."""
    pr = state.physics
    sessions = state.sessions
    selected_idx = state.best_session_idx if state.solve_basis == "best_session" else state.authority_session_idx

    # Score each category using the best session's measured state relative to all
    def _norm_lower(vals: list[float]) -> list[float]:
        lo, hi = min(vals), max(vals)
        return [1.0 - (v - lo) / (hi - lo) if hi > lo else 1.0 for v in vals]

    def _norm_higher(vals: list[float]) -> list[float]:
        lo, hi = min(vals), max(vals)
        return [(v - lo) / (hi - lo) if hi > lo else 1.0 for v in vals]

    def _norm_target(vals: list[float], t: float) -> list[float]:
        dists = [abs(v - t) for v in vals]
        mx = max(dists) if max(dists) > 0 else 1.0
        return [1.0 - d / mx for d in dists]

    n = len(sessions)

    # Lap time
    lt_scores = _norm_lower([s.lap_time_s for s in sessions])
    pr.category_scores["lap_time"] = lt_scores[selected_idx]

    # Grip
    lat_g = _norm_higher([s.measured.peak_lat_g_measured for s in sessions])
    r_slip = _norm_lower([s.measured.rear_slip_ratio_p95 for s in sessions])
    grip = [(lat_g[i] + r_slip[i]) / 2 for i in range(n)]
    pr.category_scores["grip"] = grip[selected_idx]

    # Balance
    us_abs = _norm_lower([abs(s.measured.understeer_mean_deg) for s in sessions])
    bs = _norm_lower([s.measured.body_slip_p95_deg for s in sessions])
    balance = [(us_abs[i] + bs[i]) / 2 for i in range(n)]
    pr.category_scores["balance"] = balance[selected_idx]

    # Aero efficiency
    top_spd = _norm_higher([s.measured.speed_max_kph for s in sessions])
    rh_var = _norm_lower([s.measured.front_rh_std_mm for s in sessions])
    aero = [(top_spd[i] + rh_var[i]) / 2 for i in range(n)]
    pr.category_scores["aero_efficiency"] = aero[selected_idx]

    # High-speed corners (from corner profiles)
    hs_loss = [0.0] * n
    ls_loss = [0.0] * n
    for cp in state.corner_profiles:
        for i in range(min(n, len(cp.time_loss_per_session))):
            t = cp.time_loss_per_session[i]
            if t is None:
                continue
            if cp.speed_class == "high":
                hs_loss[i] += t
            elif cp.speed_class == "low":
                ls_loss[i] += t

    hs_scores = _norm_lower(hs_loss) if any(h > 0 for h in hs_loss) else [0.5] * n
    ls_scores = _norm_lower(ls_loss) if any(l > 0 for l in ls_loss) else [0.5] * n
    pr.category_scores["high_speed_corners"] = hs_scores[selected_idx]
    pr.category_scores["low_speed_corners"] = ls_scores[selected_idx]

    # Damper platform
    settle_values: list[float] = []
    settle_known: list[bool] = []
    for session in sessions:
        settle_val = usable_signal_value(session.measured, "front_rh_settle_time_ms")
        settle_known.append(settle_val is not None)
        settle_values.append(settle_val if settle_val is not None else 125.0)
    settle = _norm_target(settle_values, 125.0)
    settle = [settle[i] if settle_known[i] else 0.5 for i in range(n)]
    yaw = _norm_higher([s.measured.yaw_rate_correlation for s in sessions])
    damper = [(settle[i] + yaw[i]) / 2 for i in range(n)]
    pr.category_scores["damper_platform"] = damper[selected_idx]

    # Thermal
    spreads = []
    for s in sessions:
        avg_spread = (
            abs(s.measured.front_temp_spread_lf_c) +
            abs(s.measured.front_temp_spread_rf_c) +
            abs(s.measured.rear_temp_spread_lr_c) +
            abs(s.measured.rear_temp_spread_rr_c)
        ) / 4.0
        spreads.append(avg_spread)
    spread_norm = _norm_lower(spreads)
    carc_f = _norm_target([s.measured.front_carcass_mean_c for s in sessions], 92.5)
    thermal = [(spread_norm[i] + carc_f[i]) / 2 for i in range(n)]
    pr.category_scores["thermal"] = thermal[selected_idx]

    # Find weakest category
    if pr.category_scores:
        pr.weakest_category = min(pr.category_scores, key=pr.category_scores.get)


def _find_tradeoffs(state: ReasoningState) -> None:
    """Identify trade-offs with ms/lap quantification."""
    pr = state.physics

    lower_better = {
        "front_rh_std_mm", "rear_rh_std_mm",
        "front_bottoming_events", "rear_bottoming_events",
        "understeer_mean_deg", "body_slip_p95_deg",
        "front_heave_defl_p99_mm", "rear_heave_defl_p99_mm",
        "front_heave_travel_used_pct", "rear_heave_travel_used_pct",
        "front_rh_settle_time_ms", "rear_rh_settle_time_ms",
    }

    for param, pl in state.parameter_learnings.items():
        if not pl.affects:
            continue

        improved_metrics: list[tuple[str, float]] = []
        worsened_metrics: list[tuple[str, float]] = []

        for metric, deltas in pl.affects.items():
            if not deltas:
                continue
            mean_delta = float(np.mean(deltas))
            is_lower = metric in lower_better
            if is_lower:
                if mean_delta < 0:
                    improved_metrics.append((metric, abs(mean_delta)))
                elif mean_delta > 0:
                    worsened_metrics.append((metric, abs(mean_delta)))
            else:
                if mean_delta > 0:
                    improved_metrics.append((metric, abs(mean_delta)))
                elif mean_delta < 0:
                    worsened_metrics.append((metric, abs(mean_delta)))

        if improved_metrics and worsened_metrics:
            # Quantify: best benefit vs worst cost
            best_benefit = max(improved_metrics, key=lambda x: x[1])
            worst_cost = max(worsened_metrics, key=lambda x: x[1])

            benefit_ms = best_benefit[1] * _METRIC_SENSITIVITY_MS.get(best_benefit[0], 10.0)
            cost_ms = worst_cost[1] * _METRIC_SENSITIVITY_MS.get(worst_cost[0], 10.0)
            net = benefit_ms - cost_ms

            rec = "apply" if net > 5 else ("skip" if net < -5 else "marginal")

            pr.tradeoffs.append(QuantifiedTradeoff(
                parameter=param,
                benefit_metric=best_benefit[0],
                benefit_ms=round(benefit_ms, 1),
                cost_metric=worst_cost[0],
                cost_ms=round(cost_ms, 1),
                net_ms=round(net, 1),
                recommendation=rec,
            ))

    pr.tradeoffs.sort(key=lambda t: abs(t.net_ms), reverse=True)


def _run_physics_reasoning(state: ReasoningState) -> None:
    """Phase 7: Full physics reasoning pipeline."""
    _run_physics_validations(state)
    _score_categories(state)
    _find_tradeoffs(state)


# ── Phase 8: Enhanced modifier generation ──────────────────────


def _compute_modifier_confidence(
    state: ReasoningState,
    field_name: str,
    supporting_params: list[str] | None = None,
) -> float:
    """Compute confidence score for a modifier."""
    conf = 0.0

    # +0.1 per supporting delta (max 0.5)
    supporting_count = 0
    for wd in state.weighted_deltas:
        for hyp in wd.delta.hypotheses:
            if supporting_params and hyp.cause_param in supporting_params:
                if hyp.confidence >= 0.4:
                    supporting_count += 1
    conf += min(0.5, supporting_count * 0.1)

    # +0.2 if historical corroboration
    if state.historical.has_data:
        for param in (supporting_params or []):
            for direction in ["+", "-"]:
                key = f"{param}:{direction}"
                if key in state.historical.historical_evidence:
                    conf += 0.2
                    break
            if conf >= 0.2:
                break

    # +0.2 if physics cross-validation confirmed
    for vc in state.physics.validations:
        if vc.confirmed and vc.confidence >= 0.5:
            conf += 0.2
            break

    # +0.1 if any controlled experiment supports
    for wd in state.weighted_deltas:
        if wd.delta.controlled_experiment and wd.weight >= 0.8:
            conf += 0.1
            break

    return min(1.0, conf)


def _reason_to_modifiers(
    state: ReasoningState,
    car: CarModel,
) -> tuple:
    """Convert accumulated reasoning into confidence-gated solver modifiers.

    Returns (SolverModifiers, list[str] reasons, list[ModifierWithConfidence]).
    """
    from solver.modifiers import SolverModifiers

    mods = SolverModifiers()
    reasons: list[str] = []
    details: list[ModifierWithConfidence] = []

    best = state.sessions[state.best_session_idx]
    authority = state.sessions[state.authority_session_idx]
    current = state.sessions[state.current_session_idx]
    analysis_sessions = state.sessions  # always use ALL sessions for modifier computation
    sra = state.speed_regime
    pr = state.physics
    hc = state.historical

    if state.solve_basis == "latest_validation_veto":
        reasons.append(
            f"Validation veto on {current.label}'s setup — solver will avoid reissuing it."
        )

    # Determine if weakest category should widen clamp ranges
    weak = pr.weakest_category
    wide_balance = weak in ("balance", "low_speed_corners", "high_speed_corners")
    wide_platform = weak in ("damper_platform", "aero_efficiency")

    # ── 8a. DF balance (aero sensitivity-scaled) ──

    # Try to get aero gradient for scaling
    gradient_scale = 0.15  # default
    aero_gradient_steep = False
    try:
        from aero_model import load_car_surfaces
        from aero_model.gradient import compute_gradients
        surfaces = load_car_surfaces(car.canonical_name)
        wing = current.setup.wing_angle_deg
        if wing and wing in surfaces:
            surface = surfaces[wing]
            f_rh = current.measured.mean_front_rh_at_speed_mm
            r_rh = current.measured.mean_rear_rh_at_speed_mm
            if f_rh and r_rh:
                grads = compute_gradients(
                    surface, car, f_rh, r_rh,
                    front_rh_sigma_mm=current.measured.front_rh_std_mm,
                    rear_rh_sigma_mm=current.measured.rear_rh_std_mm,
                )
                # Scale based on gradient steepness
                max_grad = max(abs(grads.dBalance_dFrontRH), abs(grads.dBalance_dRearRH))
                if max_grad > 0.5:
                    gradient_scale = 0.08  # steep gradient → conservative
                    aero_gradient_steep = True
                elif max_grad < 0.2:
                    gradient_scale = 0.20  # shallow gradient → can push harder
    except Exception:
        pass

    us_values = [s.measured.understeer_mean_deg for s in analysis_sessions]
    us_hs = [s.measured.understeer_high_speed_deg for s in analysis_sessions]
    us_ls = [s.measured.understeer_low_speed_deg for s in analysis_sessions]

    mean_us = float(np.mean(us_values)) if us_values else 0.0
    mean_gradient = float(np.mean(us_hs)) - float(np.mean(us_ls)) if us_hs and us_ls else 0.0

    df_conf = _compute_modifier_confidence(
        state, "df_balance_offset_pct",
        supporting_params=["front_rh_mm", "rear_rh_mm", "wing_angle"],
    )

    if abs(mean_gradient) > 0.3:
        offset = min(0.5, abs(mean_gradient) * gradient_scale)
        if mean_gradient > 0:
            offset = -offset  # HS understeer → shift balance rearward
        mods.df_balance_offset_pct = offset

        steep_note = " (steep gradient → conservative)" if aero_gradient_steep else ""
        reasons.append(
            f"DF balance {offset:+.2f}%: speed gradient {mean_gradient:+.2f}° "
            f"(scale {gradient_scale}){steep_note}"
        )
        details.append(ModifierWithConfidence(
            field_name="df_balance_offset_pct",
            value=offset,
            confidence=df_conf,
            reasoning=[f"Speed gradient {mean_gradient:+.2f}°",
                       f"Aero gradient scale: {gradient_scale}"],
        ))

    # ── 8b. LLTD offset (regime-weighted) ──

    lltd_conf = _compute_modifier_confidence(
        state, "lltd_offset",
        supporting_params=["front_arb_blade", "rear_arb_blade"],
    )

    if sra.dominant_regime == "high_speed" and abs(mean_gradient) > 0.5:
        # HS dominant problem + speed gradient → LLTD is wrong tool
        lltd_adj = 0.0
        reasons.append(
            "LLTD offset 0.000: dominant HS problem with speed gradient — "
            "aero balance, not mechanical LLTD"
        )
    elif abs(mean_us) > 0.3:
        # Weight by regime
        if sra.dominant_regime == "low_speed":
            us_for_lltd = float(np.mean(us_ls)) if us_ls else mean_us
        else:
            us_for_lltd = mean_us

        lltd_range = 0.05 if wide_balance else 0.03
        lltd_adj = -us_for_lltd * 0.01
        lltd_adj = max(-lltd_range, min(lltd_range, lltd_adj))
        mods.lltd_offset = round(lltd_adj, 3)
        reasons.append(
            f"LLTD offset {lltd_adj:+.3f}: mean understeer {us_for_lltd:+.2f}° "
            f"(regime: {sra.dominant_regime}, range ±{lltd_range})"
        )
        details.append(ModifierWithConfidence(
            field_name="lltd_offset",
            value=lltd_adj,
            confidence=lltd_conf,
            reasoning=[f"Understeer {us_for_lltd:+.2f}°",
                       f"Regime: {sra.dominant_regime}"],
        ))

    # ── 8c. Heave floors (bottoming-source validated + pitch-based) ──

    # Pitch-based floor: same threshold as single-session modifier (1.5°).
    # Uses the authority session's pitch range because that's the car state we're solving for.
    auth_measured = state.sessions[state.authority_session_idx].measured
    auth_pitch_range = usable_signal_value(auth_measured, "pitch_range_deg", allow_proxy=True)
    if auth_pitch_range is None:
        try:
            raw_pitch_range = getattr(auth_measured, "pitch_range_deg", None)
            auth_pitch_range = float(raw_pitch_range) if raw_pitch_range is not None else None
        except (TypeError, ValueError):
            auth_pitch_range = None
    if auth_pitch_range is not None and auth_pitch_range > 1.5:
        new_floor = max(mods.front_heave_min_floor_nmm, 38.0)
        if new_floor > mods.front_heave_min_floor_nmm:
            mods.front_heave_min_floor_nmm = new_floor
            reasons.append(
                f"Pitch range {auth_pitch_range:.2f}° > 1.5° → heave floor 38 N/mm"
            )

    # Check physics validation for bottoming source
    kerb_dominant_bottoming = False
    for vc in pr.validations:
        if "kerb-induced" in vc.hypothesis.lower() and vc.confirmed:
            kerb_dominant_bottoming = True
            break

    front_bottoming = [s.measured.bottoming_event_count_front for s in analysis_sessions]
    rear_bottoming = [s.measured.bottoming_event_count_rear for s in analysis_sessions]

    heave_conf = _compute_modifier_confidence(
        state, "front_heave_min_floor_nmm",
        supporting_params=["front_heave_nmm", "rear_third_nmm"],
    )

    if float(np.mean(front_bottoming)) > 5 and not kerb_dominant_bottoming:
        heave_rates = [
            s.setup.front_heave_nmm for s in analysis_sessions
            if s.setup.front_heave_nmm
        ]
        bottoming_at_rate = list(zip(heave_rates, front_bottoming[:len(heave_rates)]))
        if bottoming_at_rate:
            good_rates = [r for r, b in bottoming_at_rate if b <= 3]
            if good_rates:
                mods.front_heave_min_floor_nmm = min(good_rates)
            else:
                mods.front_heave_min_floor_nmm = max(heave_rates)
            reasons.append(
                f"Front heave floor {mods.front_heave_min_floor_nmm:.0f} N/mm: "
                f"clean-track bottoming validated"
            )
            details.append(ModifierWithConfidence(
                field_name="front_heave_min_floor_nmm",
                value=mods.front_heave_min_floor_nmm,
                confidence=heave_conf,
                reasoning=["Clean-track bottoming confirmed by physics validation"],
            ))
    elif kerb_dominant_bottoming and float(np.mean(front_bottoming)) > 5:
        reasons.append(
            f"Front heave floor NOT raised: bottoming is kerb-induced "
            f"({float(np.mean(front_bottoming)):.0f} events) — driving line issue"
        )

    # Rear third floor (currently never set — now we do)
    if float(np.mean(rear_bottoming)) > 5 and not kerb_dominant_bottoming:
        third_rates = [
            s.setup.rear_third_nmm for s in analysis_sessions
            if hasattr(s.setup, 'rear_third_nmm') and s.setup.rear_third_nmm
        ]
        bottoming_at_rate = list(zip(third_rates, rear_bottoming[:len(third_rates)]))
        if bottoming_at_rate:
            good_rates = [r for r, b in bottoming_at_rate if b <= 3]
            if good_rates:
                mods.rear_third_min_floor_nmm = min(good_rates)
            else:
                mods.rear_third_min_floor_nmm = max(third_rates)
            reasons.append(
                f"Rear third floor {mods.rear_third_min_floor_nmm:.0f} N/mm: "
                f"rear clean-track bottoming"
            )
            details.append(ModifierWithConfidence(
                field_name="rear_third_min_floor_nmm",
                value=mods.rear_third_min_floor_nmm,
                confidence=heave_conf,
                reasoning=["Rear clean-track bottoming across sessions"],
            ))

    # ── 8d. Damper clicks (speed-regime targeted) ──

    settle_times = []
    for session in analysis_sessions:
        val = usable_signal_value(session.measured, "front_rh_settle_time_ms", allow_proxy=True)
        if val is None:
            try:
                raw_val = getattr(session.measured, "front_rh_settle_time_ms", None)
                val = float(raw_val) if raw_val is not None else None
            except (TypeError, ValueError):
                val = None
        if val is not None:
            settle_times.append(val)
    mean_settle = float(np.mean(settle_times)) if settle_times else 125.0

    damper_conf = _compute_modifier_confidence(
        state, "damping_ratio_scale",
        supporting_params=["damper_lf_ls_rbd", "damper_rf_ls_rbd",
                           "damper_lr_ls_rbd", "damper_rr_ls_rbd"],
    )

    # Damping ratio scale from settle time
    if settle_times and mean_settle > 200:
        scale = 1.15 if wide_platform else 1.10
        mods.damping_ratio_scale = scale
        reasons.append(
            f"Damping scale {scale:.2f}: slow settle time {mean_settle:.0f}ms (target 100-150ms)"
        )
    elif settle_times and mean_settle < 60:
        scale = 0.88 if wide_platform else 0.92
        mods.damping_ratio_scale = scale
        reasons.append(
            f"Damping scale {scale:.2f}: overdamped settle time {mean_settle:.0f}ms"
        )
    elif not settle_times:
        reasons.append("Damping scale unchanged: clean-event settle signal unavailable, using oscillation/shock cross-check only")

    # Oscillation frequency cross-check
    front_osc_freqs = []
    for snapshot in analysis_sessions:
        osc = usable_signal_value(snapshot.measured, "front_shock_oscillation_hz", allow_proxy=True)
        if osc is None:
            try:
                raw_osc = getattr(snapshot.measured, "front_shock_oscillation_hz", None)
                osc = float(raw_osc) if raw_osc is not None else None
            except (TypeError, ValueError):
                osc = None
        if osc is not None and osc > 0:
            front_osc_freqs.append(osc)
    if front_osc_freqs:
        mean_front_osc = float(np.mean(front_osc_freqs))
        if mean_front_osc > 8.0 and mods.damping_ratio_scale < 1.0:
            reasons.append(
                f"  [cross-check] Front oscillation {mean_front_osc:.1f}Hz conflicts with "
                f"overdamped diagnosis - maintaining current scale"
            )
            mods.damping_ratio_scale = 1.0

    osc_freqs = []
    for snapshot in analysis_sessions:
        osc = usable_signal_value(snapshot.measured, "rear_shock_oscillation_hz", allow_proxy=True)
        if osc is None:
            try:
                raw_osc = getattr(snapshot.measured, "rear_shock_oscillation_hz", None)
                osc = float(raw_osc) if raw_osc is not None else None
            except (TypeError, ValueError):
                osc = None
        if osc is not None and osc > 0:
            osc_freqs.append(osc)
    if osc_freqs:
        mean_osc = float(np.mean(osc_freqs))
        # If oscillation is high, validate damping scale decision
        if mean_osc > 8.0 and mods.damping_ratio_scale < 1.0:
            # Contradiction: settle time says overdamped but oscillation is high
            reasons.append(
                f"  [cross-check] Rear oscillation {mean_osc:.1f}Hz conflicts with "
                f"overdamped diagnosis — maintaining current scale"
            )
            mods.damping_ratio_scale = 1.0

    # HS comp offsets from high-speed corner shock data
    hs_shock_fronts = []
    hs_shock_rears = []
    for cp in state.corner_profiles:
        if cp.speed_class != "high":
            continue
        if state.solve_basis == "latest_validation_veto":
            auth_idx = state.authority_session_idx
            if auth_idx < len(cp.shock_vel_front_per_session):
                sv = cp.shock_vel_front_per_session[auth_idx]
                if sv is not None:
                    hs_shock_fronts.append(sv)
            if auth_idx < len(cp.shock_vel_rear_per_session):
                sv = cp.shock_vel_rear_per_session[auth_idx]
                if sv is not None:
                    hs_shock_rears.append(sv)
            continue
        for sv in cp.shock_vel_front_per_session:
            if sv is not None:
                hs_shock_fronts.append(sv)
        for sv in cp.shock_vel_rear_per_session:
            if sv is not None:
                hs_shock_rears.append(sv)

    if hs_shock_fronts and float(np.mean(hs_shock_fronts)) > 0.35:
        mods.front_hs_comp_offset = 1
        reasons.append(
            f"Front HS comp +1: HS corner shock vel {float(np.mean(hs_shock_fronts)):.2f} m/s (>0.35)"
        )
        details.append(ModifierWithConfidence(
            field_name="front_hs_comp_offset", value=1,
            confidence=damper_conf,
            reasoning=[f"HS corner front shock vel {float(np.mean(hs_shock_fronts)):.2f} m/s"],
        ))

    if hs_shock_rears and float(np.mean(hs_shock_rears)) > 0.35:
        mods.rear_hs_comp_offset = 1
        reasons.append(
            f"Rear HS comp +1: HS corner shock vel {float(np.mean(hs_shock_rears)):.2f} m/s (>0.35)"
        )
        details.append(ModifierWithConfidence(
            field_name="rear_hs_comp_offset", value=1,
            confidence=damper_conf,
            reasoning=[f"HS corner rear shock vel {float(np.mean(hs_shock_rears)):.2f} m/s"],
        ))

    # LS rebound offsets from parameter learnings
    for param in ["damper_lf_ls_rbd", "damper_rf_ls_rbd"]:
        pl = state.parameter_learnings.get(param)
        if pl and pl.direction == "increase" and pl.confidence >= 0.4:
            mods.front_ls_rbd_offset = 1
            reasons.append(f"Front LS rbd +1: increasing consistently helped ({pl.reasoning})")
            break
        elif pl and pl.direction == "decrease" and pl.confidence >= 0.4:
            mods.front_ls_rbd_offset = -1
            reasons.append(f"Front LS rbd -1: decreasing consistently helped ({pl.reasoning})")
            break

    for param in ["damper_lr_ls_rbd", "damper_rr_ls_rbd"]:
        pl = state.parameter_learnings.get(param)
        if pl and pl.direction == "increase" and pl.confidence >= 0.4:
            mods.rear_ls_rbd_offset = 1
            reasons.append(f"Rear LS rbd +1: ({pl.reasoning})")
            break
        elif pl and pl.direction == "decrease" and pl.confidence >= 0.4:
            mods.rear_ls_rbd_offset = -1
            reasons.append(f"Rear LS rbd -1: ({pl.reasoning})")
            break

    # ── 8e. Confidence gating ──

    # Filter modifiers below confidence threshold
    gated_reasons: list[str] = []
    for md in details:
        if md.confidence < 0.3:
            gated_reasons.append(
                f"  [GATED] {md.field_name}={md.value}: conf={md.confidence:.2f} < 0.3, "
                f"not applied"
            )
            # Reset the modifier
            setattr(mods, md.field_name, type(getattr(mods, md.field_name))())

    if gated_reasons:
        reasons.extend(gated_reasons)

    # ── 8f. Historical corroboration notes ──

    if hc.has_data and hc.prediction_corrections:
        corrections_applied = []
        for key, val in hc.prediction_corrections.items():
            if abs(val) > 0.01:
                corrections_applied.append(f"{key}: {val:+.3f}")
        if corrections_applied:
            reasons.append(
                f"Historical corrections available: {', '.join(corrections_applied[:5])}"
            )

    state.modifier_details = details
    mods.reasons = reasons
    return mods, reasons


# ── Persistent problems ────────────────────────────────────────


def _find_persistent_problems(state: ReasoningState) -> None:
    """Find problems that appear in >50% of sessions."""
    problem_counts: dict[str, int] = {}
    n = len(state.sessions)

    for snap in state.sessions:
        for p in snap.diagnosis.problems:
            key = f"[{p.category}] {p.symptom}"
            problem_counts[key] = problem_counts.get(key, 0) + 1

    for prob, count in sorted(problem_counts.items(), key=lambda x: -x[1]):
        if count >= n * 0.5:
            state.persistent_problems.append(f"{prob} ({count}/{n} sessions)")

    # Problems that appeared early but disappeared
    if n >= 3:
        early_problems = set()
        late_problems = set()
        for p in state.sessions[0].diagnosis.problems:
            early_problems.add(f"[{p.category}] {p.symptom}")
        for p in state.sessions[-1].diagnosis.problems:
            late_problems.add(f"[{p.category}] {p.symptom}")
        resolved = early_problems - late_problems
        for r in resolved:
            state.resolved_problems.append(r)


# ── Report generation ───────────────────────────────────────────


def _print_reasoning_report(state: ReasoningState, width: int = 63) -> str:
    """Generate the enhanced iterative reasoning report."""
    lines = [
        "=" * width,
        "  ITERATIVE REASONING REPORT (ENHANCED)",
        f"  {len(state.sessions)} sessions, "
        f"{len(state.weighted_deltas)} all-pairs deltas",
        "=" * width,
        "",
    ]

    # Session summary
    lines.append("  SESSION TIMELINE")
    lines.append("  " + "-" * (width - 4))
    for i, snap in enumerate(state.sessions):
        marker = " <-- BEST" if i == state.best_session_idx else ""
        marker = " <-- WORST" if i == state.worst_session_idx and not marker else marker
        if i == state.reference_session_idx:
            marker = f"{marker} <-- REF".rstrip()
        if i == state.current_session_idx:
            marker = f"{marker} <-- CURRENT".rstrip()
        lines.append(
            f"  S{i+1}: {snap.lap_time_s:.3f}s  "
            f"{snap.driver.style}  "
            f"[{len(snap.diagnosis.problems)} problems]{marker}"
        )
    lines.append("")

    lines.append("  SOLVE AUTHORITY")
    lines.append("  " + "-" * (width - 4))
    ref_label = state.sessions[state.reference_session_idx].label if state.sessions else "?"
    cur_label = state.sessions[state.current_session_idx].label if state.sessions else "?"
    lines.append(
        f"  Basis: physics_optimal (all {len(state.sessions)} sessions)"
    )
    lines.append(
        f"  Reference (telemetry): S{state.reference_session_idx+1} ({ref_label})"
    )
    lines.append(
        f"  Current (in car):      S{state.current_session_idx+1} ({cur_label})"
    )
    if state.solver_notes:
        for note in state.solver_notes[:3]:
            lines.append(f"  {note[:width-2]}")
    for row in state.authority_scores[:3]:
        lines.append(
            f"  {row['session']}: score={row['score']:.3f} "
            f"(lap={row['lap_component']:.2f}, health={row['diagnosis_component']:.2f}, "
            f"context={row['context_component']:.2f}, signal={row['signal_component']:.2f}, "
            f"env={row['envelope_distance']:.2f}, setup={row['setup_distance']:.2f})"
        )
    lines.append("")

    if state.telemetry_envelope is not None or state.setup_cluster is not None:
        lines.append("  HEALTHY ENVELOPE / CLUSTER")
        lines.append("  " + "-" * (width - 4))
        if state.telemetry_envelope is not None:
            lines.append(
                f"  Telemetry envelope from {state.telemetry_envelope.sample_count} "
                f"session(s): {', '.join(state.telemetry_envelope.source_sessions[:3])}"
            )
        if state.setup_cluster is not None:
            lines.append(
                f"  Setup cluster members: {', '.join(state.setup_cluster.member_sessions[:3]) or 'n/a'}"
            )
        for snap in state.sessions[: min(4, len(state.sessions))]:
            env = state.envelope_distances.get(snap.label)
            setup = state.setup_distances.get(snap.label)
            lines.append(
                f"  {snap.label}: env={getattr(env, 'total_score', 0.0):.2f}  "
                f"setup={getattr(setup, 'distance_score', 0.0):.2f}"
            )
        lines.append("")

    if state.generated_candidates:
        lines.append("  CANDIDATE FAMILIES")
        lines.append("  " + "-" * (width - 4))
        for candidate in state.generated_candidates:
            selected = " <-- SELECTED" if candidate.selected else ""
            score = candidate.score.total if candidate.score is not None else 0.0
            lines.append(
                f"  {candidate.family}: score={score:.3f} "
                f"conf={candidate.confidence:.2f}{selected}"
            )
            if candidate.notes:
                lines.append(f"    {candidate.notes[0][:width-6]}")
        lines.append("")

    lines.append("  SIGNAL CONFIDENCE")
    lines.append("  " + "-" * (width - 4))
    authority = state.sessions[state.authority_session_idx]
    signal_lines = summarize_signal_quality(authority.measured)
    if signal_lines:
        lines.append(f"  Authority {authority.label}:")
        for line in signal_lines[:4]:
            lines.append(f"    {line[:width-6]}")
        for fallback in authority.measured.metric_fallbacks[:4]:
            lines.append(f"    Fallback: {fallback[:width-16]}")
    else:
        lines.append("  No telemetry signal summary available.")
    lines.append("")

    failed_clusters = [c for c in state.validation_clusters if c.validated_failed]
    if failed_clusters:
        lines.append("  VALIDATION CLUSTERS")
        lines.append("  " + "-" * (width - 4))
        for cluster in failed_clusters[:4]:
            compare = (
                f" vs {cluster.comparison_session_label}"
                if cluster.comparison_session_label is not None
                else ""
            )
            lines.append(
                f"  {cluster.latest_session_label}{compare}: "
                f"{cluster.penalty_mode.upper()} veto ({cluster.lap_delta_s:+.3f}s)"
            )
            for metric in cluster.metric_regressions[:3]:
                lines.append(f"    {metric[:width-6]}")
        lines.append("")

    # Delta summary (top quality deltas)
    if state.weighted_deltas:
        lines.append("  ALL-PAIRS DELTA ANALYSIS")
        lines.append("  " + "-" * (width - 4))
        lines.append(
            f"  Total pairs: {len(state.weighted_deltas)} "
            f"(N*(N-1)/2 = {len(state.sessions)*(len(state.sessions)-1)//2})"
        )

        # Show top 5 most informative deltas
        top_deltas = sorted(state.weighted_deltas, key=lambda wd: wd.weight, reverse=True)
        for wd in top_deltas[:5]:
            i, j = wd.pair
            d = wd.delta
            dt = d.lap_time_delta_s
            if abs(dt) > 0.05:
                time_str = f"{abs(dt):.3f}s {'FASTER' if dt < 0 else 'SLOWER'}"
            else:
                time_str = "~same pace"
            ctrl = "controlled" if d.controlled_experiment else "multi-change"
            lines.append(
                f"  S{i+1}->S{j+1}: {time_str} "
                f"(w={wd.weight:.2f}, {ctrl})"
            )
            good_hyps = [h for h in d.hypotheses if h.confidence >= 0.5]
            for h in good_hyps[:2]:
                match = "OK" if h.direction_match else "!!"
                lines.append(f"    [{match}] {h.mechanism[:width-10]}")
        lines.append("")

    # Corner weakness map (Phase 3)
    if state.top_weakness_corners:
        lines.append("  CORNER WEAKNESS MAP")
        lines.append("  " + "-" * (width - 4))
        for cp in state.top_weakness_corners:
            issue = cp.primary_issue or "mixed"
            lines.append(
                f"  T{cp.corner_id:02d} ({cp.speed_class:>4s} "
                f"{cp.direction:>5s}): "
                f"avg opportunity {cp.mean_time_loss:.3f}s  "
                f"(entry {cp.mean_entry_loss:.3f} / apex {cp.mean_apex_loss:.3f} / exit {cp.mean_exit_loss:.3f})  [{issue}]"
            )
        lines.append("")

    # Speed-regime verdict (Phase 4)
    sra = state.speed_regime
    lines.append("  SPEED-REGIME ANALYSIS")
    lines.append("  " + "-" * (width - 4))
    lines.append(f"  Understeer gradient (HS-LS): {sra.understeer_gradient:+.2f} deg")
    lines.append(
        f"  Time loss — HS: {sra.hs_time_loss_total:.3f}s  "
        f"LS: {sra.ls_time_loss_total:.3f}s  "
        f"Mid: {sra.mid_time_loss_total:.3f}s"
    )
    lines.append(f"  Dominant problem: {sra.dominant_regime.upper()}")
    if sra.hs_rh_std_mean > 0:
        lines.append(f"  HS ride height std: {sra.hs_rh_std_mean:.2f} mm")
    lines.append("")

    # Target profile gaps (Phase 5)
    if state.target_profile.priority_gaps:
        lines.append("  TARGET PROFILE GAPS (top 5 by ms/lap)")
        lines.append("  " + "-" * (width - 4))
        for gap in state.target_profile.priority_gaps[:5]:
            lines.append(
                f"  {gap.metric}: gap={gap.gap_from_ideal:.2f}  "
                f"~{gap.estimated_ms_per_lap:.0f} ms/lap  "
                f"(best in S{gap.best_session_idx+1})"
            )
        lines.append("")

    # Physics validation results (Phase 7)
    if state.physics.validations:
        lines.append("  PHYSICS VALIDATION")
        lines.append("  " + "-" * (width - 4))
        for vc in state.physics.validations:
            tag = "CONFIRMED" if vc.confirmed else "REFUTED"
            lines.append(
                f"  [{tag}] {vc.hypothesis[:width-16]}"
            )
            lines.append(
                f"    Evidence: {vc.evidence[:width-14]}"
            )
        lines.append("")

    # Category scores (Phase 7b)
    if state.physics.category_scores:
        title = "  CATEGORY SCORES (selected solve session)"
        lines.append(title)
        lines.append("  " + "-" * (width - 4))
        for cat, score in sorted(state.physics.category_scores.items(),
                                  key=lambda x: x[1]):
            bar = "#" * int(score * 20)
            weak = " <-- WEAKEST" if cat == state.physics.weakest_category else ""
            lines.append(f"  {cat:>22s}: {score:.0%} {bar}{weak}")
        lines.append("")

    # Trade-off analysis (Phase 7c)
    if state.physics.tradeoffs:
        lines.append("  QUANTIFIED TRADE-OFFS")
        lines.append("  " + "-" * (width - 4))
        for t in state.physics.tradeoffs[:5]:
            lines.append(
                f"  {t.parameter}: benefit={t.benefit_ms:+.0f}ms "
                f"({t.benefit_metric}) "
                f"cost={t.cost_ms:+.0f}ms ({t.cost_metric})"
            )
            lines.append(
                f"    Net: {t.net_ms:+.0f} ms/lap → {t.recommendation.upper()}"
            )
        lines.append("")

    # Parameter learnings
    learned = [
        (p, pl) for p, pl in state.parameter_learnings.items()
        if pl.direction != "unknown" and pl.confidence >= 0.3
    ]
    if learned:
        learned.sort(key=lambda x: -x[1].confidence)
        lines.append("  PARAMETER INSIGHTS")
        lines.append("  " + "-" * (width - 4))
        for param, pl in learned[:12]:
            arrow = {"increase": "^", "decrease": "v", "hold": "=", "unknown": "?"}
            sens_str = ""
            if pl.sensitivity_samples > 0:
                sens_str = f" [{pl.lap_time_sensitivity_ms:+.0f}ms/unit]"
            lines.append(
                f"  [{arrow.get(pl.direction, '?')}] {param}: "
                f"{pl.direction} (conf={pl.confidence:.0%}){sens_str}"
            )
            lines.append(f"      {pl.reasoning}")
            if pl.best_value and pl.best_lap_time < 999:
                lines.append(
                    f"      Best value: {pl.best_value} "
                    f"(at {pl.best_lap_time:.3f}s)"
                )
        lines.append("")

    # Modifier confidence table (Phase 8)
    if state.modifier_details:
        lines.append("  MODIFIER CONFIDENCE TABLE")
        lines.append("  " + "-" * (width - 4))
        for md in state.modifier_details:
            gated = " [GATED]" if md.confidence < 0.3 else ""
            lines.append(
                f"  {md.field_name}: {md.value}  "
                f"conf={md.confidence:.0%}{gated}"
            )
            for r in md.reasoning[:2]:
                lines.append(f"    {r}")
        lines.append("")

    # Historical corroboration (Phase 6)
    if state.historical.has_data:
        lines.append("  HISTORICAL KNOWLEDGE")
        lines.append("  " + "-" * (width - 4))
        lines.append(f"  Prior sessions: {state.historical.session_count}")
        if state.historical.impactful_parameters:
            lines.append("  Most impactful params:")
            for param, sens in state.historical.impactful_parameters[:5]:
                lines.append(f"    {param}: {sens:+.4f} s/unit")
        if state.historical.recurring_problems:
            lines.append("  Recurring problems:")
            for rp in state.historical.recurring_problems[:3]:
                lines.append(f"    ! {rp}")
        if state.historical.prediction_corrections:
            lines.append("  Prediction corrections:")
            for k, v in list(state.historical.prediction_corrections.items())[:5]:
                lines.append(f"    {k}: {v:+.3f}")
        lines.append("")

    # Persistent problems
    if state.persistent_problems:
        lines.append("  PERSISTENT PROBLEMS (>50% of sessions)")
        lines.append("  " + "-" * (width - 4))
        for p in state.persistent_problems[:8]:
            lines.append(f"  ! {p}")
        lines.append("")

    # Resolved problems
    if state.resolved_problems:
        lines.append("  PROBLEMS RESOLVED (present early, gone later)")
        lines.append("  " + "-" * (width - 4))
        for p in state.resolved_problems[:5]:
            lines.append(f"  + {p}")
        lines.append("")

    lines.append("=" * width)
    return "\n".join(lines)


# ── Main pipeline ───────────────────────────────────────────────


def reason_and_solve(
    car_name: str,
    ibt_paths: list[str],
    wing: float | None = None,
    fuel: float | None = None,
    balance_target: float = 50.14,
    sto_path: str | None = None,
    json_path: str | None = None,
    setup_json_path: str | None = None,
    verbose: bool = True,
    emit_report: bool = True,
    explore_legal_space: bool = False,
    search_budget: int = 1000,
    keep_weird: bool = False,
    search_mode: str | None = None,   # "quick" | "standard" | "exhaustive" → full garage card
    top_n: int = 1,
    explore: bool = False,            # zero k-NN weight + widen Sobol sampling
    scenario_profile: str | None = None,
    stint: bool = False,
    stint_select: str = "all",
    stint_max_laps: int = 40,
    stint_threshold: float = 1.5,
    force_physics_estimate: bool = False,
) -> ReasoningState:
    """Run the full 9-phase reasoning pipeline.

    Phase 1: Extract each IBT
    Phase 2: All-pairs delta analysis (weighted)
    Phase 3: Corner profiling (per-corner weakness map)
    Phase 4: Speed-regime analysis (HS vs LS separation)
    Phase 5: Target telemetry profile (cherry-pick best metrics)
    Phase 6: Historical integration (query learner)
    Phase 7: Physics reasoning (cross-validate, score categories, trade-offs)
    Phase 8: Enhanced modifier generation (confidence-gated)
    Phase 9: Solve + report
    """
    car = get_car(car_name)
    resolved_scenario = resolve_scenario_name(
        scenario_profile or ("sprint" if stint and stint_select == "last" else "race" if stint else None)
    )
    state = ReasoningState()

    def log(msg: str = "") -> None:
        if verbose:
            print(msg)

    # ── Phase 1: Sequential analysis ──
    log(f"\n{'='*60}")
    log(f"  ENHANCED REASONING ENGINE (9-PHASE)")
    log(f"  {len(ibt_paths)} sessions to analyze")
    log(f"{'='*60}\n")

    # Auto-detect minimum lap time floor from the fastest lap observed across all sessions.
    # Default (108.0s) was calibrated for BMW at Sebring — wrong for faster tracks/cars.
    # Use fastest_lap * 0.95, floored at 60s, so partial/installation laps are excluded
    # but all legitimate racing laps are accepted regardless of track or car.
    _all_plausible: list[float] = []
    for _p in ibt_paths:
        try:
            _ibt_tmp = IBTFile(_p)
            _lts = _ibt_tmp.lap_times(min_time=30.0)  # 30s = absolute floor
            for _, _t, _, _ in _lts:
                if 30.0 < _t < 300.0:
                    _all_plausible.append(_t)
        except Exception:
            pass
    if _all_plausible:
        import statistics as _stat
        _median_lap = _stat.median(_all_plausible)
        # Only consider laps within 50% of the median to avoid pit/partial laps
        # distorting the floor calculation
        _racing_laps = [t for t in _all_plausible if t >= _median_lap * 0.85]
        _fastest_any = min(_racing_laps) if _racing_laps else min(_all_plausible)
        _auto_min = max(60.0, _fastest_any * 0.95)
    else:
        _fastest_any = None
        _auto_min = 60.0
    if _fastest_any is not None:
        log(f"  Lap time floor: {_auto_min:.1f}s (fastest observed: {_fastest_any:.3f}s × 0.95)")
    else:
        log(f"  Lap time floor: {_auto_min:.1f}s (no valid laps found)")

    _load_sessions_into_state(
        state,
        ibt_paths=ibt_paths,
        car=car,
        min_lap_time=_auto_min,
        stint=stint,
        stint_select=stint_select,
        stint_max_laps=stint_max_laps,
        stint_threshold=stint_threshold,
        log=log,
    )

    if not state.sessions:
        skipped_summary = "; ".join(
            f"{Path(skipped.ibt_path).name}: {skipped.reason}"
            for skipped in state.skipped_sessions
        )
        raise ValueError(
            "No analyzable IBT sessions found."
            + (f" Skipped sessions: {skipped_summary}" if skipped_summary else "")
        )

    if state.skipped_sessions:
        skipped_summary = _skipped_session_summary(state)
        if skipped_summary:
            log(f"  {skipped_summary}")

    _sort_sessions_chronologically(state)

    # Find best/worst sessions
    lap_times = [s.lap_time_s for s in state.sessions]
    state.best_session_idx = int(np.argmin(lap_times))
    state.worst_session_idx = int(np.argmax(lap_times))
    _build_validation_clusters(state)
    _build_health_models(state)
    _resolve_authority_session(state)
    if stint:
        state.stint_datasets = [
            snap.stint_dataset
            for snap in state.sessions
            if snap.stint_dataset is not None and getattr(snap.stint_dataset, "usable_laps", None)
        ]
        if state.stint_datasets:
            state.merged_stint_dataset = merge_stint_datasets(
                state.stint_datasets,
                stint_max_laps=stint_max_laps,
                label="reasoning_stints",
            )
            state.stint_phase_summaries = dict(getattr(state.merged_stint_dataset, "phase_summaries", {}))
            state.stint_recommendations = aggregate_stint_recommendations(state.stint_datasets)
            log(
                f"  Stint aggregate: {len(state.stint_datasets)} dataset(s), "
                f"{len(state.merged_stint_dataset.usable_laps)} usable laps, "
                f"{len(state.merged_stint_dataset.evaluation_laps)} scored"
            )
            for recommendation in state.stint_recommendations[:4]:
                log(
                    f"  Stint consensus: {recommendation['phase']} -> {recommendation['issue']} "
                    f"({recommendation['count']}/{recommendation['stint_count']})"
                )

    if setup_json_path:
        import json

        setup_json_target = Path(setup_json_path)
        setup_json_target.parent.mkdir(parents=True, exist_ok=True)
        with open(setup_json_target, "w", encoding="utf-8") as f:
            json.dump(_setup_schema_dump_payload(state, car), f, indent=2, default=str)
        log(f"  Setup schema JSON: {setup_json_target}")
        if not sto_path and not json_path:
            return state

    # ── Phase 2: All-pairs delta analysis ──
    log(f"\n[Phase 2] All-pairs delta analysis...")
    _all_pairs_deltas(state)
    n = len(state.sessions)
    expected = n * (n - 1) // 2
    log(f"  {len(state.weighted_deltas)} deltas computed (expected {expected})")
    controlled = sum(1 for wd in state.weighted_deltas if wd.delta.controlled_experiment)
    log(f"  Controlled experiments: {controlled}")

    _determine_directions(state)
    _find_persistent_problems(state)

    # ── Phase 3: Corner profiling ──
    log(f"\n[Phase 3] Corner profiling...")
    _build_corner_profiles(state)
    log(f"  {len(state.corner_profiles)} corners matched across sessions")
    log(f"  {len(state.top_weakness_corners)} consistent weakness corners")
    for cp in state.top_weakness_corners[:3]:
        log(
            f"    T{cp.corner_id:02d} ({cp.speed_class}): "
            f"avg opportunity {cp.mean_time_loss:.3f}s "
            f"(E {cp.mean_entry_loss:.3f} / A {cp.mean_apex_loss:.3f} / X {cp.mean_exit_loss:.3f}) "
            f"[{cp.primary_issue}]"
        )

    # ── Phase 4: Speed-regime analysis ──
    log(f"\n[Phase 4] Speed-regime analysis...")
    _analyze_speed_regimes(state)
    sra = state.speed_regime
    log(f"  Understeer gradient (HS-LS): {sra.understeer_gradient:+.2f} deg")
    log(f"  Time loss — HS: {sra.hs_time_loss_total:.3f}s  "
        f"LS: {sra.ls_time_loss_total:.3f}s")
    log(f"  Dominant problem: {sra.dominant_regime.upper()}")

    # ── Phase 5: Target profile ──
    log(f"\n[Phase 5] Target telemetry profile...")
    _build_target_profile(state)
    if state.target_profile.priority_gaps:
        top = state.target_profile.priority_gaps[0]
        log(f"  Top gap: {top.metric} (~{top.estimated_ms_per_lap:.0f} ms/lap)")

    # ── Phase 6: Historical integration ──
    log(f"\n[Phase 6] Historical knowledge integration...")
    _integrate_historical(state, car)
    if state.historical.has_data:
        log(f"  {state.historical.session_count} prior sessions in knowledge base")
        if state.historical.impactful_parameters:
            log(f"  Most impactful: {state.historical.impactful_parameters[0][0]}")
    else:
        log(f"  No historical data (or < 3 sessions)")

    # ── Phase 7: Physics reasoning ──
    log(f"\n[Phase 7] Physics reasoning...")
    _run_physics_reasoning(state)
    pr = state.physics
    log(f"  {len(pr.validations)} validation chains")
    log(f"  Weakest category: {pr.weakest_category}")
    log(f"  {len(pr.tradeoffs)} quantified trade-offs")

    # ── Phase 8: Generate modifiers ──
    log(f"\n[Phase 8] Enhanced modifier generation...")
    mods, mod_reasons = _reason_to_modifiers(state, car)
    if mod_reasons:
        for r in mod_reasons:
            log(f"  {r}")
    else:
        log("  No modifier adjustments from reasoning")

    # ── Phase 9: Print report + run solver ──
    report = _print_reasoning_report(state)
    if verbose:
        print(report)

    best = state.sessions[state.best_session_idx]
    authority = state.sessions[state.authority_session_idx]
    reference = state.sessions[state.reference_session_idx]  # best telemetry quality
    current = state.sessions[state.current_session_idx]      # most recent = what user has
    track = reference.track    # best track profile data
    detected_wing = wing or current.setup.wing_angle_deg     # what user has NOW
    detected_fuel = fuel or current.setup.fuel_l or 89.0     # current fuel

    # Build aggregate measured from ALL sessions for candidate state adjustments
    state.aggregate_measured = _build_aggregate_measured(state)

    log(
        f"\n[Phase 9] Running 6-step solver (basis={state.solve_basis}, "
        f"track from {reference.label}, current={current.label}, "
        f"wing {detected_wing}, fuel {detected_fuel:.0f}L)..."
    )
    for note in state.solver_notes:
        log(f"  {note}")

    from aero_model import load_car_surfaces
    from solver.rake_solver import RakeSolver, reconcile_ride_heights
    from solver.heave_solver import HeaveSolver
    from solver.corner_spring_solver import CornerSpringSolver
    from solver.arb_solver import ARBSolver
    from solver.wheel_geometry_solver import WheelGeometrySolver
    from solver.damper_solver import DamperSolver
    from solver.supporting_solver import SupportingSolver
    from solver.full_setup_optimizer import optimize_if_supported
    from solver.solve_chain import apply_damper_modifiers as _apply_damper_modifiers

    surfaces = load_car_surfaces(car.canonical_name)
    if detected_wing not in surfaces:
        available = sorted(surfaces.keys())
        detected_wing = min(available, key=lambda w: abs(w - detected_wing))
        log(f"  Wing snapped to {detected_wing}")
    surface = surfaces[detected_wing]

    target_balance = balance_target + mods.df_balance_offset_pct

    # Use the reference session's measured state for damper telemetry validation
    authority_measured = reference.measured

    def _candidate_veto_for_solution(step1, step2, step3, step4, step5, step6) -> CandidateVeto | None:
        fingerprint = fingerprint_from_solver_steps(
            wing=detected_wing,
            fuel_l=detected_fuel,
            step1=step1,
            step2=step2,
            step3=step3,
            step4=step4,
            step5=step5,
            step6=step6,
        )
        matched = match_failed_cluster(fingerprint, state.validation_clusters)
        if matched is None:
            return None
        penalty = 1e6 if matched.penalty_mode == "hard" else 5e4
        return CandidateVeto(
            fingerprint=fingerprint,
            matched_session_label=matched.latest_session_label,
            matched_session_idx=matched.latest_session_idx,
            reason=matched.reason,
            penalty=penalty,
            penalty_mode=matched.penalty_mode,
        )

    def _run_sequential_solver():
        rake_solver = RakeSolver(car, surface, track)
        _step1 = rake_solver.solve(
            target_balance=target_balance,
            fuel_load_l=detected_fuel,
            pin_front_min=True,
        )

        heave_solver = HeaveSolver(car, track)
        _step2 = heave_solver.solve(
            dynamic_front_rh_mm=_step1.dynamic_front_rh_mm,
            dynamic_rear_rh_mm=_step1.dynamic_rear_rh_mm,
            front_heave_floor_nmm=mods.front_heave_min_floor_nmm,
            rear_third_floor_nmm=mods.rear_third_min_floor_nmm,
            front_heave_perch_target_mm=mods.front_heave_perch_target_mm,
            front_pushrod_mm=_step1.front_pushrod_offset_mm,
            rear_pushrod_mm=_step1.rear_pushrod_offset_mm,
            fuel_load_l=detected_fuel,
            front_camber_deg=authority.setup.front_camber_deg or car.geometry.front_camber_baseline_deg,
        )

        corner_solver = CornerSpringSolver(car, track)
        _step3 = corner_solver.solve(
            front_heave_nmm=_step2.front_heave_nmm,
            rear_third_nmm=_step2.rear_third_nmm,
            fuel_load_l=detected_fuel,
        )

        _rear_wheel_rate_nmm = _step3.rear_wheel_rate_nmm

        heave_solver.reconcile_solution(
            _step1,
            _step2,
            _step3,
            fuel_load_l=detected_fuel,
            front_camber_deg=authority.setup.front_camber_deg or car.geometry.front_camber_baseline_deg,
            verbose=False,
        )
        reconcile_ride_heights(
            car,
            _step1,
            _step2,
            _step3,
            fuel_load_l=detected_fuel,
            track_name=track.track_name,
            verbose=False,
            surface=surface,
            track=track,
            target_balance=target_balance,
        )

        arb_solver = ARBSolver(car, track)
        _step4 = arb_solver.solve(
            front_wheel_rate_nmm=_step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=_rear_wheel_rate_nmm,
            lltd_offset=mods.lltd_offset,
            current_rear_arb_size=getattr(current.setup, "rear_arb_size", None),
        )

        geom_solver = WheelGeometrySolver(car, track)
        _step5 = geom_solver.solve(
            k_roll_total_nm_deg=_step4.k_roll_front_total + _step4.k_roll_rear_total,
            front_wheel_rate_nmm=_step3.front_wheel_rate_nmm,
            rear_wheel_rate_nmm=_rear_wheel_rate_nmm,
            fuel_load_l=detected_fuel,
        )

        reconcile_ride_heights(
            car,
            _step1,
            _step2,
            _step3,
            step5=_step5,
            fuel_load_l=detected_fuel,
            track_name=track.track_name,
            verbose=False,
            surface=surface,
            track=track,
            target_balance=target_balance,
        )

        damper_solver = DamperSolver(car, track)
        try:
            _step6 = damper_solver.solve(
                front_wheel_rate_nmm=_step3.front_wheel_rate_nmm,
                rear_wheel_rate_nmm=_rear_wheel_rate_nmm,
                front_dynamic_rh_mm=_step1.dynamic_front_rh_mm,
                rear_dynamic_rh_mm=_step1.dynamic_rear_rh_mm,
                fuel_load_l=detected_fuel,
                damping_ratio_scale=mods.damping_ratio_scale,
                measured=authority_measured,
                front_heave_nmm=_step2.front_heave_nmm,
                rear_third_nmm=_step2.rear_third_nmm,
                force_physics_estimate=force_physics_estimate,
            )
            _apply_damper_modifiers(_step6, mods, car)
        except ValueError:
            _step6 = None
        return _step1, _step2, _step3, _step4, _step5, _step6, _rear_wheel_rate_nmm

    # Try constrained optimizer first
    optimized = optimize_if_supported(
        car=car,
        surface=surface,
        track=track,
        target_balance=target_balance,
        balance_tolerance=0.1,
        fuel_load_l=detected_fuel,
        pin_front_min=True,
        wing_angle=detected_wing,
        damping_ratio_scale=mods.damping_ratio_scale,
        lltd_offset=mods.lltd_offset,
        measured=authority_measured,
        failed_validation_clusters=state.validation_clusters,
    )
    state.candidate_vetoes = list(optimized.candidate_vetoes) if optimized is not None else []
    solver_selection_note = ""

    if optimized is not None and not optimized.all_candidates_vetoed:
        step1 = optimized.step1
        step2 = optimized.step2
        step3 = optimized.step3
        step4 = optimized.step4
        step5 = optimized.step5
        step6 = optimized.step6
        _apply_damper_modifiers(step6, mods, car)
        rear_wheel_rate_nmm = step3.rear_wheel_rate_nmm
        solver_selection_note = "Selected BMW/Sebring constrained optimizer candidate."
    else:
        if optimized is not None and optimized.all_candidates_vetoed:
            log("  All optimizer candidates matched a failed validation cluster; trying sequential fallback...")

        step1, step2, step3, step4, step5, step6, rear_wheel_rate_nmm = _run_sequential_solver()
        sequential_veto = _candidate_veto_for_solution(step1, step2, step3, step4, step5, step6)
        if sequential_veto is None:
            solver_selection_note = "Rejected vetoed optimizer candidate; selected sequential fallback."
        elif optimized is None:
            state.candidate_vetoes.append(sequential_veto)
            solver_selection_note = (
                "Sequential solver also matched a failed validation cluster; using best available fallback with warning."
            )
        else:
            state.candidate_vetoes.append(sequential_veto)
            step1 = optimized.step1
            step2 = optimized.step2
            step3 = optimized.step3
            step4 = optimized.step4
            step5 = optimized.step5
            step6 = optimized.step6
            _apply_damper_modifiers(step6, mods, car)
            rear_wheel_rate_nmm = step3.rear_wheel_rate_nmm
            solver_selection_note = (
                "Sequential fallback also matched the rejected setup; returning lowest-penalty optimizer candidate with warning."
            )

    if solver_selection_note:
        state.solver_notes.append(solver_selection_note)

    # Supporting params: use most consistent driver profile
    drivers_by_consistency = sorted(
        state.sessions,
        key=lambda s: getattr(s.driver, "apex_speed_cv", 999),
    )
    best_driver = drivers_by_consistency[0]

    supporting_solver = SupportingSolver(
        car,
        best_driver.driver,
        best_driver.measured,
        best_driver.diagnosis,
        track=track,
        current_setup=current.setup,
    )
    supporting = supporting_solver.solve()

    solve_inputs = SolveChainInputs(
        car=car,
        surface=surface,
        track=track,
        measured=authority_measured,
        driver=reference.driver,
        diagnosis=current.diagnosis,
        current_setup=current.setup,
        target_balance=target_balance,
        fuel_load_l=detected_fuel,
        wing_angle=detected_wing,
        modifiers=mods,
        prediction_corrections=dict(state.historical.prediction_corrections),
        scenario_profile=resolved_scenario,
        failed_validation_clusters=state.validation_clusters,
        supporting_driver=best_driver.driver,
        supporting_measured=best_driver.measured,
        supporting_diagnosis=best_driver.diagnosis,
        corners=reference.corners,
        force_physics_estimate=force_physics_estimate,
    )
    solve_result = run_base_solve(solve_inputs)
    if stint and state.merged_stint_dataset is not None:
        state.stint_solve_result = solve_stint_compromise(
            dataset=state.merged_stint_dataset,
            base_inputs=solve_inputs,
            base_result=solve_result,
        )
        solve_result = state.stint_solve_result.result
        state.solver_notes.extend(state.stint_solve_result.notes)
    step1 = solve_result.step1
    step2 = solve_result.step2
    step3 = solve_result.step3
    step4 = solve_result.step4
    step5 = solve_result.step5
    step6 = solve_result.step6
    supporting = solve_result.supporting
    state.candidate_vetoes = list(solve_result.candidate_vetoes)
    state.legal_validation = solve_result.legal_validation
    state.decision_trace = solve_result.decision_trace
    solve_result_notes = list(solve_result.notes)
    if solver_selection_note.startswith("Selected BMW/Sebring constrained optimizer candidate."):
        solve_result_notes = [
            note for note in solve_result_notes
            if note != "Selected constrained optimizer candidate."
        ]
    state.solver_notes.extend(solve_result_notes)
    state.generated_candidates = generate_candidate_families(
        authority_session=authority,
        best_session=best,
        overhaul_assessment=authority.diagnosis.overhaul_assessment,
        authority_score=next((row for row in state.authority_scores if row["session"] == authority.label), None),
        envelope_distance=state.envelope_distances.get(authority.label).total_score
        if authority.label in state.envelope_distances
        else 0.0,
        setup_distance=state.setup_distances.get(authority.label).distance_score
        if authority.label in state.setup_distances
        else 0.0,
        base_result=solve_result,
        solve_inputs=solve_inputs,
        setup_cluster=state.setup_cluster if state.setup_cluster is not None and len(state.setup_cluster.member_sessions) >= 3 else None,
        current_session=current,
        aggregate_measured=state.aggregate_measured,
    )
    selected_candidate = next((candidate for candidate in state.generated_candidates if candidate.selected), None)
    selected_candidate_applied = False
    selected_candidate_family_output = getattr(selected_candidate, "family", None)
    selected_candidate_score_output = (
        selected_candidate.score.total
        if selected_candidate is not None and selected_candidate.score is not None
        else None
    )
    if selected_candidate is not None:
        state.solver_notes.append(
            f"Candidate family selected: {selected_candidate.family} "
            f"(score {selected_candidate.score.total if selected_candidate.score else 0.0:.3f})"
        )
        candidate_result = _selected_candidate_result(selected_candidate)
        if candidate_result is not None:
            candidate_result, preserved_rotation_controls = preserve_candidate_rotation_controls(
                rotation_result=solve_result,
                candidate_result=candidate_result,
                inputs=solve_inputs,
            )
            if candidate_result is None:
                candidate_result = _selected_candidate_result(selected_candidate)
                preserved_rotation_controls = False
            selected_candidate.result = candidate_result
            selected_candidate.step1 = candidate_result.step1
            selected_candidate.step2 = candidate_result.step2
            selected_candidate.step3 = candidate_result.step3
            selected_candidate.step4 = candidate_result.step4
            selected_candidate.step5 = candidate_result.step5
            selected_candidate.step6 = candidate_result.step6
            selected_candidate.supporting = candidate_result.supporting
            selected_candidate.legality = candidate_result.legal_validation
            selected_candidate.predicted = candidate_result.prediction
            selected_candidate_applied = True
            step1 = candidate_result.step1
            step2 = candidate_result.step2
            step3 = candidate_result.step3
            step4 = candidate_result.step4
            step5 = candidate_result.step5
            step6 = candidate_result.step6
            supporting = candidate_result.supporting
            state.legal_validation = candidate_result.legal_validation
            state.decision_trace = candidate_result.decision_trace
            selected_candidate_family_output = selected_candidate.family
            selected_candidate_score_output = (
                selected_candidate.score.total if selected_candidate.score is not None else None
            )
            state.solver_notes.append(
                f"Applied rematerialized {selected_candidate.family} candidate result to final report/JSON/export payloads."
            )
            if preserved_rotation_controls:
                state.solver_notes.append(
                    "Preserved BMW/Sebring second-stage rotation controls after candidate-family rematerialization."
                )

            # Enforce modifier safety floors on candidate result.
            # Candidates may set spring rates from observed session medians, which
            # can violate physics-derived minimums (heave floor, pitch floor, etc.).
            heave_floor = mods.front_heave_min_floor_nmm
            if heave_floor > 0 and step2.front_heave_nmm < heave_floor:
                state.solver_notes.append(
                    f"Candidate heave {step2.front_heave_nmm:.0f} N/mm < floor {heave_floor:.0f} N/mm "
                    f"(from {selected_candidate.family} selection) — re-solving with floor constraint."
                )
                heave_solver = HeaveSolver(car, track)
                step2 = heave_solver.solve(
                    dynamic_front_rh_mm=step1.dynamic_front_rh_mm,
                    dynamic_rear_rh_mm=step1.dynamic_rear_rh_mm,
                    front_heave_floor_nmm=heave_floor,
                    rear_third_floor_nmm=mods.rear_third_min_floor_nmm,
                    front_heave_perch_target_mm=mods.front_heave_perch_target_mm,
                    front_pushrod_mm=step1.front_pushrod_offset_mm,
                    rear_pushrod_mm=step1.rear_pushrod_offset_mm,
                    fuel_load_l=detected_fuel,
                    front_camber_deg=authority.setup.front_camber_deg or car.geometry.front_camber_baseline_deg,
                )

    stint_report_result = None
    merged_stint_evolution = None
    if state.merged_stint_dataset is not None and getattr(state.merged_stint_dataset, "usable_laps", None):
        merged_stint_evolution = dataset_to_evolution(state.merged_stint_dataset)
        try:
            from solver.stint_model import analyze_stint

            stint_report_result = analyze_stint(
                car=car,
                stint_laps=max(1, len(state.merged_stint_dataset.usable_laps)),
                base_heave_nmm=step2.front_heave_nmm,
                base_third_nmm=step2.rear_third_nmm,
                v_p99_front_mps=getattr(track, "shock_vel_p99_front_mps", 0.0),
                v_p99_rear_mps=getattr(track, "shock_vel_p99_rear_mps", 0.0),
                evolution=merged_stint_evolution,
            )
        except Exception:
            stint_report_result = None

    # ── Legal-manifold search (--explore-legal-space) ──
    _search_ready = all(s is not None for s in (step1, step2, step3, step4, step5, step6))
    _should_run_search = should_run_legal_manifold_search(
        free_mode=False,
        explicit_search=explore_legal_space,
        search_mode=search_mode,
        scenario_name=resolved_scenario,
    )
    if _should_run_search and not _search_ready:
        _search_reason = (
            f"blocked steps {sorted(state.calibration_blocked_steps)}"
            if state.calibration_blocked_steps
            else "base solve did not materialize all 6 steps"
        )
        log(f"[legal-search] Skipped: requires all 6 calibrated solver steps ({_search_reason}).")
        state.solver_notes.append(
            f"Skipped legal-manifold search because not all 6 calibrated steps were available ({_search_reason})."
        )
    elif _should_run_search:
        try:
            from solver.legal_search import run_legal_search

            baseline_params = {
                key: value
                for key, value in build_search_baseline(
                    car=car,
                    wing=detected_wing,
                    current_setup=authority.setup,
                    step1=step1,
                    step2=step2,
                    step3=step3,
                    step4=step4,
                    step5=step5,
                    step6=step6,
                    supporting=supporting,
                ).items()
                if value is not None
            }
            ls_result = run_legal_search(
                car=car,
                track=track,
                baseline_params=baseline_params,
                budget=search_budget,
                measured=authority.measured,
                driver_profile=authority.driver,
                session_count=len(state.sessions),
                keep_weird=keep_weird,
                base_result=solve_result,
                solve_inputs=solve_inputs,
                scenario_profile=resolved_scenario,
            )
            print()
            print(ls_result.summary())
            if ls_result.accepted_best_result is not None and ls_result.accepted_best is not None:
                accepted_result = ls_result.accepted_best_result
                step1 = accepted_result.step1
                step2 = accepted_result.step2
                step3 = accepted_result.step3
                step4 = accepted_result.step4
                step5 = accepted_result.step5
                step6 = accepted_result.step6
                supporting = accepted_result.supporting
                state.legal_validation = accepted_result.legal_validation
                state.decision_trace = accepted_result.decision_trace
                selected_candidate_family_output = f"{resolved_scenario}:{ls_result.accepted_best.family}"
                selected_candidate_score_output = ls_result.accepted_best.score
                selected_candidate_applied = True
                state.solver_notes.append(
                    f"Applied legal-manifold scenario pick {ls_result.accepted_best.family} "
                    f"for {resolved_scenario} after full legality + prediction sanity checks."
                )
            else:
                state.solver_notes.append(
                    f"Legal-manifold search found no fully accepted {resolved_scenario} candidate."
                )
        except Exception as e:
            print(f"[legal-search] Skipped: {e}")

    # ── Output ──
    if sto_path:
        from output.setup_writer import write_sto
        from output.garage_validator import validate_and_fix_garage_correlation

        garage_warnings = validate_and_fix_garage_correlation(
            car, step1, step2, step3, step5,
            fuel_l=detected_fuel, track_name=track.track_name,
        )
        for w in garage_warnings:
            log(f"[garage] {w}")

        _extra_kw = {}
        if car.canonical_name == "ferrari":
            _cs = authority.setup
            _extra_kw["front_tb_turns"] = _cs.torsion_bar_turns
            _extra_kw["rear_tb_turns"] = _cs.rear_torsion_bar_turns
            _extra_kw["brake_bias_migration_gain"] = _cs.brake_bias_migration_gain
            _extra_kw["front_diff_preload_nm"] = _cs.front_diff_preload_nm
            _extra_kw["fuel_target_l"] = getattr(supporting, "fuel_target_l", None)
            _extra_kw["hybrid_rear_drive_enabled"] = _cs.hybrid_rear_drive_enabled
            _extra_kw["hybrid_rear_drive_corner_pct"] = _cs.hybrid_rear_drive_corner_pct
            _extra_kw["speed_in_first_kph"] = _cs.speed_in_first_kph
            _extra_kw["speed_in_second_kph"] = _cs.speed_in_second_kph
            _extra_kw["speed_in_third_kph"] = _cs.speed_in_third_kph
            _extra_kw["speed_in_fourth_kph"] = _cs.speed_in_fourth_kph
            _extra_kw["speed_in_fifth_kph"] = _cs.speed_in_fifth_kph
            _extra_kw["speed_in_sixth_kph"] = _cs.speed_in_sixth_kph
            _extra_kw["speed_in_seventh_kph"] = _cs.speed_in_seventh_kph
        _extra_kw["tyre_pressure_kpa"] = supporting.tyre_cold_fl_kpa
        _extra_kw["brake_bias_pct"] = supporting.brake_bias_pct
        _extra_kw["brake_bias_target"] = supporting.brake_bias_target
        _extra_kw["brake_bias_migration"] = supporting.brake_bias_migration
        _extra_kw["front_master_cyl_mm"] = supporting.front_master_cyl_mm
        _extra_kw["rear_master_cyl_mm"] = supporting.rear_master_cyl_mm
        _extra_kw["pad_compound"] = supporting.pad_compound
        _extra_kw["diff_coast_drive_ramp"] = (
            getattr(supporting, "diff_ramp_angles", "")
            or (
                ("More Locking" if supporting.diff_ramp_coast <= 45 else "Less Locking")
                if car.canonical_name == "ferrari"
                else f"{supporting.diff_ramp_coast}/{supporting.diff_ramp_drive}"
            )
        )
        _extra_kw["diff_clutch_plates"] = supporting.diff_clutch_plates
        _extra_kw["diff_preload_nm"] = supporting.diff_preload_nm
        _extra_kw["tc_gain"] = supporting.tc_gain
        _extra_kw["tc_slip"] = supporting.tc_slip
        _extra_kw["fuel_low_warning_l"] = getattr(supporting, "fuel_low_warning_l", detected_fuel)
        _extra_kw["gear_stack"] = getattr(supporting, "gear_stack", "")
        _extra_kw["roof_light_color"] = getattr(supporting, "roof_light_color", "")

        write_sto(
            car_name=car.name,
            track_name=f"{track.track_name} — {track.track_config}",
            wing=detected_wing,
            fuel_l=detected_fuel,
            step1=step1, step2=step2, step3=step3,
            step4=step4, step5=step5, step6=step6,
            output_path=sto_path,
            car_canonical=car.canonical_name,
            **_extra_kw,
        )
        log(f"\n.sto setup saved to: {sto_path}")

    if json_path:
        import json
        from output.report import to_public_output_payload
        parameter_coverage = build_parameter_coverage(
            car=car,
            wing=detected_wing,
            current_setup=authority.setup,
            step1=step1,
            step2=step2,
            step3=step3,
            step4=step4,
            step5=step5,
            step6=step6,
            supporting=supporting,
        )
        telemetry_coverage = build_telemetry_coverage(measured=authority.measured)
        output = {
            "car": car.name,
            "sessions_analyzed": len(state.sessions),
            "best_session": state.sessions[state.best_session_idx].label,
            "authority_session": state.sessions[state.authority_session_idx].label,
            "reference_session": state.sessions[state.reference_session_idx].label,
            "current_session": state.sessions[state.current_session_idx].label,
            "solve_basis": state.solve_basis,
            "setup_schemas": _setup_schema_dump_payload(state, car),
            "stint_selection": _stint_selection_payload(state.merged_stint_dataset),
            "stint_laps": _stint_lap_payload(state.merged_stint_dataset),
            "stint_phases": dict(state.stint_phase_summaries),
            "stint_recommendations": list(state.stint_recommendations),
            "stint_objective": (
                state.stint_solve_result.objective
                if state.stint_solve_result is not None
                else None
            ),
            "stint_confidence": (
                state.stint_solve_result.confidence
                if state.stint_solve_result is not None
                else getattr(state.merged_stint_dataset, "confidence", None)
            ),
            "fallback_mode": (
                state.stint_solve_result.fallback_mode
                if state.stint_solve_result is not None
                else getattr(state.merged_stint_dataset, "fallback_mode", None)
            ),
            "reasoning_modifiers": {
                "df_balance_offset": mods.df_balance_offset_pct,
                "lltd_offset": mods.lltd_offset,
                "damping_ratio_scale": mods.damping_ratio_scale,
                "front_heave_floor": mods.front_heave_min_floor_nmm,
                "rear_third_floor": mods.rear_third_min_floor_nmm,
                "front_hs_comp_offset": mods.front_hs_comp_offset,
                "rear_hs_comp_offset": mods.rear_hs_comp_offset,
                "reasons": mods.reasons,
            },
            "speed_regime": {
                "dominant": state.speed_regime.dominant_regime,
                "understeer_gradient": state.speed_regime.understeer_gradient,
                "hs_time_loss": state.speed_regime.hs_time_loss_total,
                "ls_time_loss": state.speed_regime.ls_time_loss_total,
            },
            "top_weakness_corners": [
                {
                    "corner_id": cp.corner_id,
                    "speed_class": cp.speed_class,
                    "mean_time_loss": cp.mean_time_loss,
                    "primary_issue": cp.primary_issue,
                }
                for cp in state.top_weakness_corners
            ],
            "target_profile_gaps": [
                {
                    "metric": g.metric,
                    "gap": g.gap_from_ideal,
                    "estimated_ms": g.estimated_ms_per_lap,
                    "best_session": g.best_session_idx,
                }
                for g in state.target_profile.priority_gaps[:10]
            ],
            "physics_validations": [
                {
                    "hypothesis": vc.hypothesis,
                    "confirmed": vc.confirmed,
                    "confidence": vc.confidence,
                }
                for vc in state.physics.validations
            ],
            "category_scores": state.physics.category_scores,
            "weakest_category": state.physics.weakest_category,
            "authority_scores": state.authority_scores,
            "tradeoffs": [
                {
                    "parameter": t.parameter,
                    "net_ms": t.net_ms,
                    "recommendation": t.recommendation,
                }
                for t in state.physics.tradeoffs
            ],
            "modifier_confidence": [
                {
                    "field": md.field_name,
                    "value": md.value,
                    "confidence": md.confidence,
                }
                for md in state.modifier_details
            ],
            "telemetry_envelope": (
                {
                    "sample_count": state.telemetry_envelope.sample_count,
                    "source_sessions": state.telemetry_envelope.source_sessions,
                    "metrics": state.telemetry_envelope.metrics,
                }
                if state.telemetry_envelope is not None
                else None
            ),
            "setup_cluster": (
                {
                    "label": state.setup_cluster.label,
                    "member_sessions": state.setup_cluster.member_sessions,
                    "center": state.setup_cluster.center,
                    "spreads": state.setup_cluster.spreads,
                }
                if state.setup_cluster is not None
                else None
            ),
            "envelope_distances": {
                label: {
                    "total_score": distance.total_score,
                    "per_metric": distance.per_metric,
                    "notes": distance.notes,
                }
                for label, distance in state.envelope_distances.items()
            },
            "setup_distances": {
                label: {
                    "distance_score": distance.distance_score,
                    "per_parameter_z": distance.per_parameter_z,
                    "outlier_parameters": distance.outlier_parameters,
                }
                for label, distance in state.setup_distances.items()
            },
            "parameter_insights": {
                p: {
                    "direction": pl.direction,
                    "confidence": pl.confidence,
                    "reasoning": pl.reasoning,
                    "best_value": pl.best_value,
                    "sensitivity_ms": pl.lap_time_sensitivity_ms,
                }
                for p, pl in state.parameter_learnings.items()
                if pl.direction != "unknown"
            },
            "persistent_problems": state.persistent_problems,
            "setup_fingerprints": [
                {
                    "session": state.sessions[idx].label,
                    "fingerprint": fp.to_dict(),
                }
                for idx, fp in enumerate(state.setup_fingerprints)
            ],
            "session_signal_quality": [
                {
                    "session": snap.label,
                    "summary": summarize_signal_quality(snap.measured),
                    "telemetry_bundle": snap.measured.telemetry_bundle,
                    "telemetry_signals": signals_to_dict(snap.measured.telemetry_signals),
                }
                for snap in state.sessions
            ],
            "skipped_sessions": [
                {
                    "label": skipped.label,
                    "ibt_path": skipped.ibt_path,
                    "reason": skipped.reason,
                }
                for skipped in state.skipped_sessions
            ],
            "validation_clusters": [cluster.to_dict() for cluster in state.validation_clusters],
            "candidate_vetoes": [veto.to_dict() for veto in state.candidate_vetoes],
            "generated_candidates": [
                candidate_to_dict(candidate)
                for candidate in state.generated_candidates
            ],
            "parameter_coverage": parameter_coverage,
            "telemetry_coverage": telemetry_coverage,
            "scenario_profile": resolved_scenario,
            "selected_candidate_family": selected_candidate_family_output,
            "selected_candidate_score": selected_candidate_score_output,
            "selected_candidate_applied": selected_candidate_applied,
            "legal_validation": state.legal_validation.to_dict() if state.legal_validation is not None else None,
            "decision_trace": [decision.to_dict() for decision in state.decision_trace],
            "solver_notes": state.solver_notes,
            "step1_rake": to_public_output_payload(car.canonical_name, step1),
            "step2_heave": to_public_output_payload(car.canonical_name, step2),
            "step3_corner": to_public_output_payload(car.canonical_name, step3),
            "step4_arb": to_public_output_payload(car.canonical_name, step4),
            "step5_geometry": to_public_output_payload(car.canonical_name, step5),
            "step6_dampers": to_public_output_payload(car.canonical_name, step6),
            "supporting": to_public_output_payload(car.canonical_name, supporting),
        }
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w") as f:
            json.dump(output, f, indent=2, default=str)
        log(f"\nJSON saved to: {json_path}")

    state.final_modifiers = mods
    state.final_step1 = step1
    state.final_step2 = step2
    state.final_step3 = step3
    state.final_step4 = step4
    state.final_step5 = step5
    state.final_step6 = step6
    state.final_supporting = supporting
    state.final_wing_angle = detected_wing
    state.final_fuel_l = detected_fuel
    state.final_selected_candidate_family = selected_candidate_family_output
    state.final_selected_candidate_score = selected_candidate_score_output
    state.final_selected_candidate_applied = selected_candidate_applied

    # Build the final setup report
    from pipeline.report import generate_report
    report = generate_report(
        car=car,
        track=track,
        measured=authority_measured,
        driver=authority.driver,
        diagnosis=authority.diagnosis,
        corners=authority.corners,
        aero_grad=None,
        modifiers=mods,
        step1=step1, step2=step2, step3=step3,
        step4=step4, step5=step5, step6=step6,
        supporting=supporting,
        current_setup=authority.setup,
        wing=detected_wing,
        fuel_l=detected_fuel,
        target_balance=target_balance,
        stint_result=stint_report_result,
        stint_evolution=merged_stint_evolution,
        stint_compromise_info=(
            list(state.stint_solve_result.notes)
            if state.stint_solve_result is not None
            else None
        ),
        prediction_corrections=dict(state.historical.prediction_corrections),
        selected_candidate_family=selected_candidate_family_output,
        selected_candidate_score=selected_candidate_score_output,
        solve_context_lines=state.solver_notes + [
            f"Authority session: {authority.label}",
            f"Benchmark best session: {best.label}",
        ] + [
            f"Stint consensus {rec['phase']}: {rec['issue']} ({rec['count']}/{rec['stint_count']})"
            for rec in state.stint_recommendations[:3]
        ] + [
            f"Rejected prior candidate matching {v.matched_session_label}: {v.reason}"
            for v in state.candidate_vetoes[:2]
        ],
        compact=(search_mode is None),   # compact only when NOT doing grid search
    )
    state.final_report = report

    # ── Full garage card via GridSearchEngine (--search-mode) ─────────────
    # When search_mode is set, re-run the grid search using the 9-phase
    # modifiers as constraints, then output the full setup sheet.
    if search_mode is not None and emit_report:
        print()
        print("=" * 70)
        print(f"Running full grid search ({search_mode}) on authority session...")
        print(f"  9-phase cross-session modifiers applied as solver constraints")
        print(f"  Authority: {authority.label}")
        print("=" * 70)
        try:
            from solver.grid_search import GridSearchEngine
            from solver.legal_space import LegalSpace
            from solver.objective import ObjectiveFunction

            space = LegalSpace.from_car(car, track_name=getattr(track, "name", ""))
            objective = ObjectiveFunction(car, track if hasattr(track, "name") else None,
                                          explore=explore)
            # Pre-stash authority session telemetry so batch scoring uses correct signals
            objective.set_session_context(
                measured=authority_measured,
                driver=authority.driver,
            )

            engine = GridSearchEngine(
                space=space,
                objective=objective,
                car=car,
                track=track if hasattr(track, "name") else None,
                progress_cb=print,
            )
            # Widen Sobol sampling in explore mode
            gs_family = None if explore else getattr(state, "_search_family", None)
            gs_result = engine.run(budget=search_mode, progress=True, family=gs_family,
                                   explore=explore)
            print()
            print(gs_result.summary())

            # --save-setup: write flat recommended params as JSON
            _save_setup_path = getattr(args, "save_setup", None) if "args" in locals() else None
            if _save_setup_path and gs_result.best_overall is not None:
                import json as _json
                _rec = {
                    "car": car.canonical_name if hasattr(car, "canonical_name") else str(car),
                    "track": getattr(track, "name", str(track)) if track else None,
                    "wing_deg": wing,
                    "authority_session": authority.label,
                    "authority_lap_s": getattr(authority, "fastest_lap_s", None) or getattr(authority, "sort_timestamp", None),
                    "score_ms": round(gs_result.best_overall.score, 1) if hasattr(gs_result.best_overall, "score") else None,
                    "recommended": {k: round(v, 4) if isinstance(v, float) else v
                                    for k, v in gs_result.best_overall.params.items()},
                }
                with open(_save_setup_path, "w") as _f:
                    _json.dump(_rec, _f, indent=2)
                print(f"\n  Recommended setup saved to: {_save_setup_path}")

            # Output full setup sheet for each top candidate
            from pipeline.report import generate_report as _gen_report
        except Exception as _gs_err:
            import traceback
            print(f"[WARN] Grid search failed: {_gs_err} — compact report above is the result")
            traceback.print_exc()

    # ── Concise summary (always printed, even when verbose=False) ──
    if emit_report and not verbose:
        summary_lines = []
        summary_lines.append("")
        summary_lines.append(f"  {len(state.sessions)} sessions analyzed")
        # Session list
        for snap in state.sessions:
            markers = []
            if snap is best:
                markers.append("fastest")
            if snap is authority:
                markers.append("authority")
            marker_str = f"  ({', '.join(markers)})" if markers else ""
            summary_lines.append(
                f"    {snap.label}: {snap.lap_time_s:.3f}s  "
                f"{snap.diagnosis.assessment}{marker_str}"
            )
        # Why authority was chosen over best
        if authority is not best:
            auth_score = next(
                (r["score"] for r in state.authority_scores if r["session"] == authority.label),
                0.0,
            )
            best_score = next(
                (r["score"] for r in state.authority_scores if r["session"] == best.label),
                0.0,
            )
            summary_lines.append(
                f"  Authority {authority.label} (score {auth_score:.2f}) chosen over "
                f"fastest {best.label} (score {best_score:.2f})"
            )
        # Candidate family
        if selected_candidate is not None:
            cand_score = (
                selected_candidate.score.total
                if selected_candidate.score is not None
                else 0.0
            )
            summary_lines.append(
                f"  Strategy: {selected_candidate.family.replace('_', ' ')} "
                f"(confidence {cand_score:.2f})"
            )
        # Top improvements / trade-offs from prediction
        if selected_candidate is not None and selected_candidate.predicted is not None:
            pred = selected_candidate.predicted
            auth_m = authority_measured
            changes = []
            ft = getattr(pred, "front_heave_travel_used_pct", None)
            ft_base = getattr(auth_m, "front_heave_travel_used_pct", None)
            if ft is not None and ft_base is not None and abs(ft - ft_base) > 1:
                changes.append(f"front travel {ft_base:.0f}→{ft:.0f}%")
            fe = getattr(pred, "front_excursion_mm", None)
            fe_base = getattr(auth_m, "front_heave_defl_p99_mm", None)
            if fe is not None and fe_base is not None and abs(fe - fe_base) > 0.5:
                changes.append(f"front excursion {fe_base:.1f}→{fe:.1f}mm")
            us = getattr(pred, "understeer_high_deg", None)
            us_base = getattr(auth_m, "understeer_high_speed_deg", None)
            if us is not None and us_base is not None and abs(us - us_base) > 0.02:
                changes.append(f"HS understeer {us_base:.2f}→{us:.2f}°")
            if changes:
                summary_lines.append(f"  Predicted: {', '.join(changes)}")
        # Weakest area
        if state.persistent_problems:
            summary_lines.append(f"  Persistent: {state.persistent_problems[0]}")
        elif state.top_weakness_corners:
            wc = state.top_weakness_corners[0]
            corner_name = f"T{wc.corner_id:02d} ({wc.speed_class})"
            summary_lines.append(
                f"  Weakest corner: {corner_name} [{wc.primary_issue}]"
                if wc.primary_issue
                else f"  Weakest corner: {corner_name}"
            )
        summary_lines.append("")
        print("\n".join(summary_lines))

    if emit_report:
        print(report)

    return state


# ── CLI ─────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pipeline.reason",
        description=(
            "Enhanced reasoning engine: reads N IBT files, performs all-pairs "
            "delta analysis, corner profiling, speed-regime analysis, physics "
            "cross-validation, and produces a confidence-gated optimal setup."
        ),
    )
    parser.add_argument("--car", required=True, help="Car name (e.g., bmw)")
    parser.add_argument("--ibt", required=True, nargs="+", help="IBT files (2+)")
    parser.add_argument("--wing", type=float, default=None, help="Wing angle override")
    parser.add_argument("--fuel", type=float, default=None, help="Fuel load override (L)")
    parser.add_argument("--balance", type=float, default=50.14, help="Target DF balance %%")
    parser.add_argument("--sto", type=str, default=None, help="Output .sto file")
    parser.add_argument("--json", type=str, default=None, help="Output JSON file")
    parser.add_argument(
        "--setup-json",
        type=str,
        default=None,
        help="Output canonical setup schema correlation JSON and exit if used alone",
    )
    parser.add_argument("--learn", action="store_true",
                        help="Ingest sessions into learner knowledge base after solving")
    parser.add_argument("--verbose", action="store_true",
                        help="Show full reasoning dump instead of concise summary")
    parser.add_argument("--stint", action="store_true",
                        help="Enable full-stint reasoning across the selected green-run stint(s) in each IBT.")
    parser.add_argument("--stint-select", type=str, default="all", choices=["longest", "last", "all"],
                        help="Which stint segment(s) to use from each IBT when --stint is enabled (default: all)")
    parser.add_argument("--stint-max-laps", type=int, default=40,
                        help="Maximum number of stint laps to score directly per merged solve (default: 40)")
    parser.add_argument("--stint-threshold", type=float, default=1.5,
                        help="Backward-compatible soft outlier/reporting threshold for stint lap quality (default: 1.5)")
    parser.add_argument(
        "--search-mode", type=str, default=None,
        choices=["quick", "standard", "exhaustive"],
        dest="search_mode",
        help=(
            "After 9-phase cross-session analysis, run a full grid search on the "
            "authority session and output a complete garage card. "
            "quick=~30s, standard=~4min, exhaustive=~80min."
        ),
    )
    parser.add_argument(
        "--top-n", type=int, default=1, dest="top_n",
        help="Number of full setup cards to output (default: 1).",
    )
    parser.add_argument(
        "--explore", action="store_true",
        help=(
            "Exploration mode: zeros k-NN empirical weight and widens Sobol sampling. "
            "Use to validate that the current setup isn't a local minimum. "
            "Requires --search-mode."
        ),
    )
    parser.add_argument(
        "--save-setup", type=str, default=None, dest="save_setup",
        metavar="FILE",
        help="Write the recommended setup params as a flat JSON file (e.g. setup.json). Requires --search-mode.",
    )
    parser.add_argument(
        "--scenario-profile",
        type=str,
        default="single_lap_safe",
        choices=["single_lap_safe", "quali", "sprint", "race"],
        dest="scenario_profile",
        help="Scenario objective profile for candidate scoring and legal-manifold search.",
    )
    parser.add_argument(
        "--objective-profile",
        type=str,
        choices=["single_lap_safe", "quali", "sprint", "race"],
        dest="scenario_profile",
        help="Legacy alias for --scenario-profile.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Bypass uncalibrated-zeta gate in the damper solver: use textbook "
            "physics defaults (zeta_LS=0.5, zeta_HS=0.85) and label step6 as "
            "an estimate. Use for cars whose damper zeta targets are not yet "
            "calibrated (Cadillac, Acura, Ferrari)."
        ),
    )

    args = parser.parse_args()

    if len(args.ibt) < 2:
        print("ERROR: Need at least 2 IBT files.")
        sys.exit(1)

    for p in args.ibt:
        if not Path(p).exists():
            print(f"ERROR: IBT not found: {p}")
            sys.exit(1)

    state = reason_and_solve(
        car_name=args.car,
        ibt_paths=args.ibt,
        wing=args.wing,
        fuel=args.fuel,
        balance_target=args.balance,
        sto_path=args.sto,
        json_path=args.json,
        setup_json_path=args.setup_json,
        verbose=args.verbose,
        search_mode=getattr(args, "search_mode", None),
        top_n=getattr(args, "top_n", 1),
        explore=getattr(args, "explore", False),
        stint=getattr(args, "stint", False),
        stint_select=getattr(args, "stint_select", "all"),
        stint_max_laps=getattr(args, "stint_max_laps", 40),
        stint_threshold=getattr(args, "stint_threshold", 1.5),
        scenario_profile=getattr(args, "scenario_profile", "single_lap_safe"),
        force_physics_estimate=getattr(args, "force", False),
    )

    if args.learn:
        from learner.ingest import ingest_ibt
        print("\n[learn] Ingesting sessions into knowledge base...")
        for p in args.ibt:
            try:
                ingest_ibt(car_name=args.car, ibt_path=p, wing=args.wing)
                print(f"  [learn] Ingested: {Path(p).name}")
            except Exception as e:
                print(f"  [learn] Failed: {Path(p).name}: {e}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
