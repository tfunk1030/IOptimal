"""Typed request and view-model objects for the IOptimal web app."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


RunMode = Literal["single_session", "comparison", "track_solve"]
RunState = Literal["queued", "running", "completed", "failed"]


@dataclass(slots=True)
class RunCreateRequest:
    """Normalized run creation payload used by the web job runner."""

    mode: RunMode
    car: str
    ibt_paths: list[Path] = field(default_factory=list)
    track: str | None = None
    wing: float | None = None
    lap: int | None = None
    fuel: float | None = None
    balance: float = 50.14
    tolerance: float = 0.1
    scenario_profile: str = "single_lap_safe"
    free_opt: bool = False
    use_learning: bool = True
    synthesize: bool = True


@dataclass(slots=True)
class RunStatusView:
    """Status row rendered on progress and history pages."""

    id: str
    mode: RunMode
    state: RunState
    phase: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    car: str | None = None
    track: str | None = None


@dataclass(slots=True)
class ArtifactLinkView:
    """Downloadable file linked from a result page."""

    id: str
    label: str
    kind: str


@dataclass(slots=True)
class ProblemView:
    severity: str
    symptom: str
    cause: str
    speed_context: str


@dataclass(slots=True)
class ChangeView:
    label: str
    current: str
    recommended: str
    delta: str
    reason: str = ""


@dataclass(slots=True)
class SetupGroupView:
    name: str
    help_text: str
    rows: list[ChangeView] = field(default_factory=list)


@dataclass(slots=True)
class MetricView:
    label: str
    baseline: str
    predicted: str
    delta: str
    note: str = ""


@dataclass(slots=True)
class SessionResultView:
    result_kind: Literal["single_session", "track_solve"]
    title: str
    subtitle: str
    car_name: str
    track_name: str
    lap_label: str
    assessment: str
    confidence_label: str
    confidence_value: float | None
    overview_badges: list[str] = field(default_factory=list)
    problems: list[ProblemView] = field(default_factory=list)
    top_changes: list[ChangeView] = field(default_factory=list)
    setup_groups: list[SetupGroupView] = field(default_factory=list)
    telemetry: list[MetricView] = field(default_factory=list)
    engineering_notes: list[str] = field(default_factory=list)
    report_text: str = ""
    candidate_family: str | None = None
    candidate_score: float | None = None
    artifact_links: list[ArtifactLinkView] = field(default_factory=list)


@dataclass(slots=True)
class RankingView:
    label: str
    lap_time: str
    overall_score: str
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ComparisonTableRowView:
    label: str
    values: list[str]
    delta: str = ""


@dataclass(slots=True)
class CornerHighlightView:
    corner_label: str
    summary: str
    spread: str


@dataclass(slots=True)
class ComparisonResultView:
    result_kind: Literal["comparison"]
    title: str
    subtitle: str
    car_name: str
    track_name: str
    sessions_count: int
    winner_label: str
    overview_badges: list[str] = field(default_factory=list)
    rankings: list[RankingView] = field(default_factory=list)
    setup_rows: list[ComparisonTableRowView] = field(default_factory=list)
    telemetry_rows: list[ComparisonTableRowView] = field(default_factory=list)
    corner_highlights: list[CornerHighlightView] = field(default_factory=list)
    synthesis_groups: list[SetupGroupView] = field(default_factory=list)
    engineering_notes: list[str] = field(default_factory=list)
    report_text: str = ""
    artifact_links: list[ArtifactLinkView] = field(default_factory=list)


@dataclass(slots=True)
class KnowledgeBucketView:
    car: str
    track: str
    observation_count: int
    last_session_id: str
    corrections: list[str] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)


@dataclass(slots=True)
class KnowledgeSummaryView:
    total_observations: int
    total_deltas: int
    cars_seen: list[str] = field(default_factory=list)
    tracks_seen: list[str] = field(default_factory=list)
    buckets: list[KnowledgeBucketView] = field(default_factory=list)
    recent_learnings: list[str] = field(default_factory=list)
