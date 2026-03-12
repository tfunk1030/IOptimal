"""Self-validation and learning — predict, measure, update.

Enables the solver to:
1. Store predictions alongside setup outputs
2. Compare predictions against actual telemetry from the next session
3. Bayesian-update model parameters based on prediction errors
4. Detect systematic model mismatch (bias)

This closes the feedback loop: solver predicts → driver runs → system
validates → model improves → next prediction is better.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analyzer.extract import MeasuredState


@dataclass
class PredictionEntry:
    """A single predicted quantity from the solver."""
    metric: str
    predicted: float
    units: str
    confidence: str  # "high" | "medium" | "low"
    solver_step: int


@dataclass
class SetupPrediction:
    """Complete set of predictions for a solver run."""
    car: str
    track: str
    wing: float
    fuel_l: float
    timestamp: str = ""
    session_id: str = ""

    predictions: list[PredictionEntry] = field(default_factory=list)

    # Model parameters used (for Bayesian updating)
    model_params: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, data: str) -> SetupPrediction:
        d = json.loads(data)
        pred = cls(
            car=d["car"], track=d["track"],
            wing=d["wing"], fuel_l=d["fuel_l"],
            timestamp=d.get("timestamp", ""),
            session_id=d.get("session_id", ""),
            model_params=d.get("model_params", {}),
        )
        for p in d.get("predictions", []):
            pred.predictions.append(PredictionEntry(**p))
        return pred


@dataclass
class ValidationEntry:
    """Comparison of a single predicted vs actual quantity."""
    metric: str
    predicted: float
    actual: float
    error: float           # actual - predicted
    error_pct: float       # error / |predicted| * 100
    status: str            # "ok" | "review" | "mismatch"
    units: str


@dataclass
class ValidationReport:
    """Complete validation of predictions against measurements."""
    entries: list[ValidationEntry] = field(default_factory=list)
    overall_status: str = "ok"
    model_updates: list[ModelUpdate] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)

    def summary(self, width: int = 63) -> str:
        lines = [
            "=" * width,
            "  PREDICTION VALIDATION",
            "=" * width,
            "",
            f"  {'Metric':<25s} {'Pred':>7s} {'Actual':>7s} "
            f"{'Error':>7s} {'Status':>8s}",
            "  " + "-" * (width - 4),
        ]

        for e in self.entries:
            lines.append(
                f"  {e.metric:<25s} {e.predicted:>7.1f} {e.actual:>7.1f} "
                f"{e.error_pct:>+6.0f}% {e.status:>8s}"
            )

        n_ok = sum(1 for e in self.entries if e.status == "ok")
        n_total = len(self.entries)
        lines.append("  " + "-" * (width - 4))
        lines.append(f"  Overall: {n_ok}/{n_total} within tolerance")
        lines.append(f"  Status: {self.overall_status.upper()}")

        if self.model_updates:
            lines.append("")
            lines.append("  MODEL PARAMETER UPDATES:")
            for mu in self.model_updates:
                lines.append(
                    f"    {mu.parameter}: {mu.old_value:.2f} → {mu.new_value:.2f} "
                    f"(from {mu.reason})"
                )

        if self.actions:
            lines.append("")
            lines.append("  RECOMMENDED ACTIONS:")
            for a in self.actions:
                lines.append(f"    → {a}")

        lines.append("=" * width)
        return "\n".join(lines)


@dataclass
class ModelUpdate:
    """A Bayesian update to a model parameter."""
    parameter: str
    old_value: float
    new_value: float
    old_sigma: float
    new_sigma: float
    reason: str


# ── Prediction generation ─────────────────────────────────────────────

def generate_predictions(
    car: str,
    track: str,
    wing: float,
    fuel_l: float,
    front_excursion_mm: float = 0.0,
    front_bottoming_margin_mm: float = 0.0,
    front_sigma_mm: float = 0.0,
    rear_sigma_mm: float = 0.0,
    front_heave_nmm: float = 0.0,
    rear_third_nmm: float = 0.0,
    lltd: float = 0.0,
    m_eff_front_kg: float = 228.0,
    m_eff_rear_kg: float = 2395.3,
) -> SetupPrediction:
    """Generate a prediction record from solver output.

    Called after solver completes. Stores expected telemetry values
    that can be validated against the next IBT session.
    """
    prediction = SetupPrediction(
        car=car,
        track=track,
        wing=wing,
        fuel_l=fuel_l,
        timestamp=datetime.now().isoformat(),
    )

    if front_excursion_mm > 0:
        prediction.predictions.append(PredictionEntry(
            metric="front_excursion_p99_mm",
            predicted=front_excursion_mm,
            units="mm",
            confidence="high",
            solver_step=2,
        ))

    if front_sigma_mm > 0:
        prediction.predictions.append(PredictionEntry(
            metric="front_rh_sigma_mm",
            predicted=front_sigma_mm,
            units="mm",
            confidence="high",
            solver_step=2,
        ))

    if rear_sigma_mm > 0:
        prediction.predictions.append(PredictionEntry(
            metric="rear_rh_sigma_mm",
            predicted=rear_sigma_mm,
            units="mm",
            confidence="high",
            solver_step=2,
        ))

    if lltd > 0:
        prediction.predictions.append(PredictionEntry(
            metric="lltd_pct",
            predicted=lltd * 100,
            units="%",
            confidence="medium",
            solver_step=4,
        ))

    # Store model parameters for updating
    prediction.model_params = {
        "m_eff_front_kg": m_eff_front_kg,
        "m_eff_rear_kg": m_eff_rear_kg,
        "front_heave_nmm": front_heave_nmm,
        "rear_third_nmm": rear_third_nmm,
    }

    return prediction


# ── Validation against actual telemetry ───────────────────────────────

# Tolerance thresholds for validation
VALIDATION_TOLERANCES = {
    "front_excursion_p99_mm": 0.20,   # 20% error is OK
    "front_rh_sigma_mm": 0.20,
    "rear_rh_sigma_mm": 0.25,
    "lltd_pct": 0.10,                 # 10% relative error
    "front_settle_time_ms": 0.30,
    "understeer_mean_deg": 0.30,
}

# Threshold for "mismatch" (systematic model problem)
MISMATCH_THRESHOLD = 0.40  # 40% error


def validate_predictions(
    prediction: SetupPrediction,
    measured: MeasuredState,
) -> ValidationReport:
    """Compare solver predictions against actual telemetry.

    Args:
        prediction: Predictions from generate_predictions()
        measured: Actual measurements from extract.py

    Returns:
        ValidationReport with entry-by-entry comparison
    """
    report = ValidationReport()

    # Map prediction metrics to measured values
    metric_map = {
        "front_excursion_p99_mm": measured.front_rh_excursion_measured_mm,
        "front_rh_sigma_mm": measured.front_rh_std_mm,
        "rear_rh_sigma_mm": measured.rear_rh_std_mm,
        "lltd_pct": measured.lltd_measured * 100 if measured.lltd_measured > 0 else 0,
    }

    for pred_entry in prediction.predictions:
        actual = metric_map.get(pred_entry.metric, 0.0)
        if actual <= 0:
            continue  # no measurement available

        error = actual - pred_entry.predicted
        error_pct = (error / abs(pred_entry.predicted) * 100) if pred_entry.predicted != 0 else 0

        tolerance = VALIDATION_TOLERANCES.get(pred_entry.metric, 0.25)

        if abs(error_pct) / 100 > MISMATCH_THRESHOLD:
            status = "mismatch"
        elif abs(error_pct) / 100 > tolerance:
            status = "review"
        else:
            status = "ok"

        report.entries.append(ValidationEntry(
            metric=pred_entry.metric,
            predicted=pred_entry.predicted,
            actual=actual,
            error=round(error, 2),
            error_pct=round(error_pct, 1),
            status=status,
            units=pred_entry.units,
        ))

    # Overall status
    if any(e.status == "mismatch" for e in report.entries):
        report.overall_status = "mismatch"
    elif any(e.status == "review" for e in report.entries):
        report.overall_status = "review"
    else:
        report.overall_status = "ok"

    # Generate model updates
    report.model_updates = _compute_model_updates(prediction, report.entries)

    # Generate action recommendations
    report.actions = _generate_actions(report)

    return report


# ── Bayesian parameter updating ───────────────────────────────────────

def _compute_model_updates(
    prediction: SetupPrediction,
    entries: list[ValidationEntry],
) -> list[ModelUpdate]:
    """Compute Bayesian parameter updates from prediction errors.

    Key update: if excursion is systematically wrong, update m_eff.
    excursion = v * sqrt(m/k), so:
        m_eff_new = m_eff_old * (actual_excursion / predicted_excursion)²
    """
    updates = []

    for entry in entries:
        if entry.status == "ok":
            continue

        if entry.metric == "front_excursion_p99_mm" and entry.predicted > 0:
            old_m = prediction.model_params.get("m_eff_front_kg", 228.0)
            ratio = (entry.actual / entry.predicted) ** 2
            new_m = old_m * ratio
            # Reduce uncertainty by observation
            old_sigma = old_m * 0.05
            new_sigma = old_sigma * 0.85  # 15% reduction per observation

            updates.append(ModelUpdate(
                parameter="m_eff_front_kg",
                old_value=round(old_m, 1),
                new_value=round(new_m, 1),
                old_sigma=round(old_sigma, 1),
                new_sigma=round(new_sigma, 1),
                reason=(
                    f"Front excursion: predicted {entry.predicted:.1f}mm, "
                    f"actual {entry.actual:.1f}mm → "
                    f"m_eff correction factor {ratio:.3f}"
                ),
            ))

        elif entry.metric == "front_rh_sigma_mm" and entry.predicted > 0:
            # sigma ~ 1/sqrt(k) * sqrt(m), so m_eff correction:
            old_m = prediction.model_params.get("m_eff_front_kg", 228.0)
            ratio = (entry.actual / entry.predicted) ** 2
            new_m = old_m * ratio
            old_sigma = old_m * 0.05
            new_sigma = old_sigma * 0.90

            updates.append(ModelUpdate(
                parameter="m_eff_front_kg_from_sigma",
                old_value=round(old_m, 1),
                new_value=round(new_m, 1),
                old_sigma=round(old_sigma, 1),
                new_sigma=round(new_sigma, 1),
                reason=(
                    f"Front sigma: predicted {entry.predicted:.1f}mm, "
                    f"actual {entry.actual:.1f}mm → "
                    f"m_eff correction factor {ratio:.3f}"
                ),
            ))

    return updates


def _generate_actions(report: ValidationReport) -> list[str]:
    """Generate recommended actions from validation results."""
    actions = []

    if report.overall_status == "mismatch":
        actions.append(
            "SYSTEMATIC MODEL MISMATCH detected. "
            "Re-calibrate effective mass and aero compression model "
            "using this session's data."
        )

    review_metrics = [e.metric for e in report.entries if e.status == "review"]
    if review_metrics:
        actions.append(
            f"Review predictions for: {', '.join(review_metrics)}. "
            f"Errors exceed normal tolerance but may be within noise."
        )

    # Check for consistent bias direction
    errors = [e.error_pct for e in report.entries if e.status != "ok"]
    if errors:
        all_positive = all(e > 0 for e in errors)
        all_negative = all(e < 0 for e in errors)
        if all_positive:
            actions.append(
                "All prediction errors are POSITIVE (actual > predicted). "
                "Model may be systematically under-predicting. "
                "Check if m_eff or shock velocity calibration is too low."
            )
        elif all_negative:
            actions.append(
                "All prediction errors are NEGATIVE (actual < predicted). "
                "Model may be systematically over-predicting. "
                "Check if m_eff or shock velocity calibration is too high."
            )

    return actions


# ── Persistence ───────────────────────────────────────────────────────

VALIDATION_DIR = Path(__file__).parent.parent / "data" / "validation"


def save_prediction(prediction: SetupPrediction, output_dir: Path | None = None) -> Path:
    """Save a prediction to disk for later validation."""
    target_dir = output_dir or VALIDATION_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = (
        f"{prediction.car}_{prediction.track}_"
        f"{prediction.timestamp.replace(':', '-')}.json"
    )
    filepath = target_dir / filename

    filepath.write_text(prediction.to_json())
    return filepath


def load_prediction(filepath: Path) -> SetupPrediction:
    """Load a prediction from disk."""
    return SetupPrediction.from_json(filepath.read_text())


def find_latest_prediction(
    car: str,
    track: str,
    search_dir: Path | None = None,
) -> SetupPrediction | None:
    """Find the most recent prediction for a car/track combination."""
    target_dir = search_dir or VALIDATION_DIR
    if not target_dir.exists():
        return None

    pattern = f"{car}_{track}_*.json"
    matches = sorted(target_dir.glob(pattern), reverse=True)

    if not matches:
        return None

    return load_prediction(matches[0])
