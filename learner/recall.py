"""Recall — query interface for the accumulated knowledge.

This is what the solver and analyzer call to ask: "based on everything
we've learned, what should I expect / adjust?"

The key queries:
- "What's the empirical roll gradient for BMW at Sebring?"
- "Last time we softened the heave, what happened?"
- "What's our confidence in the m_eff value?"
- "Which parameters should I focus on for this track?"
- "What corrections should I apply to the physics model?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from learner.knowledge_store import KnowledgeStore
from learner.empirical_models import EmpiricalModelSet


@dataclass
class RecallResult:
    """Answer to a knowledge query."""
    query: str
    answer: Any
    confidence: str          # "high" | "medium" | "low" | "no_data"
    source_count: int        # how many observations inform this
    details: dict = field(default_factory=dict)


class KnowledgeRecall:
    """Query interface to the learning system."""

    def __init__(self, store: KnowledgeStore):
        self.store = store

    def _load_models(self, car: str, track: str) -> EmpiricalModelSet | None:
        track_key = track.lower().split()[0]  # Match ingest.py: first word only
        model_id = f"{car}_{track_key}_empirical".lower()
        data = self.store.load_model(model_id)
        if data:
            return EmpiricalModelSet.from_dict(data)
        return None

    # ── Correction Factors ────────────────────────────────────────

    def get_corrections(self, car: str, track: str) -> dict:
        """Get all empirical correction factors for a car/track combination.

        Returns a dict of correction_name → value. The solver can use these
        to adjust its physics predictions. Prediction-based corrections
        (from fit_prediction_errors) are included with 'prediction_correction_' prefix.
        """
        models = self._load_models(car, track)
        if models is None:
            return {}
        return models.corrections

    def get_prediction_corrections(self, car: str, track: str) -> dict:
        """Get prediction-vs-measurement corrections only.

        These are exponentially-weighted mean errors between what the solver
        predicted and what was actually measured. The solver should ADD these
        corrections to its predictions for better accuracy.

        Returns dict like: {"front_rh_std_mm": -0.3, "lltd_predicted": 0.01, ...}
        """
        corrections = self.get_corrections(car, track)
        result = {}
        prefix = "prediction_correction_"
        for key, value in corrections.items():
            if key.startswith(prefix) and not key.endswith(("_std", "_n")):
                clean_key = key[len(prefix):]
                result[clean_key] = value
        return result

    def get_correction(self, car: str, track: str, name: str) -> RecallResult:
        """Get a specific correction factor."""
        corrections = self.get_corrections(car, track)
        if name in corrections:
            return RecallResult(
                query=f"correction:{name}",
                answer=corrections[name],
                confidence="high" if isinstance(corrections[name], (int, float)) else "medium",
                source_count=corrections.get(f"{name}_sample_count",
                              corrections.get("observation_count", 0)),
            )
        return RecallResult(query=f"correction:{name}", answer=None,
                           confidence="no_data", source_count=0)

    # ── Predictions ───────────────────────────────────────────────

    def predict(self, car: str, track: str,
                relationship: str, x_value: float) -> RecallResult:
        """Use an empirical model to predict a value.

        Args:
            relationship: Name of the fitted relationship (e.g., "lltd_vs_rear_arb")
            x_value: Input value to predict from

        Returns:
            RecallResult with the prediction and confidence
        """
        models = self._load_models(car, track)
        if models is None:
            return RecallResult(
                query=f"predict:{relationship}({x_value})",
                answer=None, confidence="no_data", source_count=0,
            )

        rel = models.relationships.get(relationship)
        if rel is None:
            return RecallResult(
                query=f"predict:{relationship}({x_value})",
                answer=None, confidence="no_data", source_count=0,
            )

        prediction = rel.predict(x_value)
        confidence = rel.confidence_at(x_value)

        return RecallResult(
            query=f"predict:{relationship}({x_value})",
            answer=prediction,
            confidence=confidence,
            source_count=rel.sample_count,
            details={
                "r_squared": rel.r_squared,
                "residual_std": rel.residual_std,
                "x_range": [rel.x_min, rel.x_max],
            },
        )

    # ── History Queries ───────────────────────────────────────────

    def what_happened_when(
        self, car: str, track: str, parameter: str, direction: str = "+"
    ) -> RecallResult:
        """Query: 'What happened last time we changed <parameter> in <direction>?'

        Returns all deltas where this parameter changed in the given direction,
        with their observed effects.
        """
        deltas = self.store.list_deltas(car=car, track=track)
        relevant = []

        for d in deltas:
            for sc in d.get("setup_changes", []):
                if sc["parameter"] != parameter:
                    continue
                delta_val = sc.get("delta")
                if isinstance(delta_val, (int, float)):
                    if (direction == "+" and delta_val > 0) or \
                       (direction == "-" and delta_val < 0):
                        relevant.append({
                            "delta_id": f"{d['session_before']}->{d['session_after']}",
                            "change": delta_val,
                            "lap_time_delta": d.get("lap_time_delta_s", 0),
                            "confidence": d.get("confidence_level", "low"),
                            "effects": [
                                {
                                    "metric": e["metric"],
                                    "delta": e["delta"],
                                    "significance": e["significance"],
                                }
                                for e in d.get("telemetry_effects", [])
                                if e["significance"] != "noise"
                            ],
                            "key_finding": d.get("key_finding", ""),
                        })

        if not relevant:
            return RecallResult(
                query=f"what_happened_when:{parameter}:{direction}",
                answer=None, confidence="no_data", source_count=0,
            )

        return RecallResult(
            query=f"what_happened_when:{parameter}:{direction}",
            answer=relevant,
            confidence="high" if len(relevant) >= 3 else ("medium" if relevant else "low"),
            source_count=len(relevant),
        )

    def most_impactful_parameters(self, car: str, track: str) -> RecallResult:
        """Query: 'Which parameters have the biggest effect on lap time?'"""
        models = self._load_models(car, track)
        if models is None or not models.most_sensitive_parameters:
            return RecallResult(
                query="most_impactful_parameters",
                answer=None, confidence="no_data", source_count=0,
            )

        return RecallResult(
            query="most_impactful_parameters",
            answer=models.most_sensitive_parameters,
            confidence="medium" if models.observation_count >= 5 else "low",
            source_count=models.observation_count,
        )

    # ── Session Context ───────────────────────────────────────────

    def session_count(self, car: str = "", track: str = "") -> int:
        """How many sessions have we analyzed for this car/track?"""
        return self.store.session_count(car, track)

    def last_session_summary(self, car: str, track: str) -> RecallResult:
        """Get a summary of the most recent session for context."""
        obs_list = self.store.list_observations(car=car, track=track)
        if not obs_list:
            return RecallResult(
                query="last_session", answer=None,
                confidence="no_data", source_count=0,
            )

        latest = obs_list[-1]
        return RecallResult(
            query="last_session",
            answer={
                "session_id": latest["session_id"],
                "lap_time": latest.get("performance", {}).get("best_lap_time_s"),
                "assessment": latest.get("diagnosis", {}).get("assessment"),
                "problems": latest.get("diagnosis", {}).get("problem_count", 0),
                "setup_summary": {
                    k: v for k, v in latest.get("setup", {}).items()
                    if k != "dampers"
                },
            },
            confidence="high",
            source_count=len(obs_list),
        )

    # ── Insight Summaries ─────────────────────────────────────────

    def get_insights(self, car: str, track: str) -> RecallResult:
        """Get the distilled insights for a car/track combination."""
        insight_id = f"{car}_{track.lower().split()[0]}_insights"
        insights = self.store.load_insights(insight_id)
        if insights is None:
            return RecallResult(
                query="insights", answer=None,
                confidence="no_data", source_count=0,
            )

        return RecallResult(
            query="insights",
            answer=insights,
            confidence="high" if insights.get("session_count", 0) >= 5 else "medium",
            source_count=insights.get("session_count", 0),
        )

    # ── Full Knowledge Dump ───────────────────────────────────────

    def knowledge_summary(self, car: str, track: str) -> str:
        """Generate a human-readable summary of everything we know."""
        obs_list = self.store.list_observations(car=car, track=track)
        deltas = self.store.list_deltas(car=car, track=track)
        models = self._load_models(car, track)

        lines = [
            f"Knowledge Summary: {car} @ {track}",
            f"{'=' * 50}",
            f"Sessions analyzed: {len(obs_list)}",
            f"Session deltas computed: {len(deltas)}",
        ]

        if obs_list:
            lap_times = [o.get("performance", {}).get("best_lap_time_s", 0)
                         for o in obs_list if o.get("performance", {}).get("best_lap_time_s", 0) > 0]
            if lap_times:
                lines.append(f"Lap time range: {min(lap_times):.3f}s – {max(lap_times):.3f}s")
                lines.append(f"Best ever: {min(lap_times):.3f}s")

        if models and models.corrections:
            lines.append("")
            lines.append("Empirical Corrections:")
            for k, v in models.corrections.items():
                if isinstance(v, (int, float)):
                    lines.append(f"  {k}: {v:.4f}")

        if models and models.most_sensitive_parameters:
            lines.append("")
            lines.append("Most Impactful Parameters (lap time):")
            for param, sens in models.most_sensitive_parameters[:5]:
                lines.append(f"  {param}: {sens:+.4f} s/unit")

        if deltas:
            high_conf = [d for d in deltas if d.get("confidence_level") == "high"]
            lines.append(f"")
            lines.append(f"High-confidence findings: {len(high_conf)}")
            for d in high_conf[:5]:
                lines.append(f"  * {d.get('key_finding', 'N/A')}")

        return "\n".join(lines)
