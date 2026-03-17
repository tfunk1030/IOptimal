"""Generate multiple setup candidate families.

Instead of producing a single "best" answer, this module generates
multiple candidate setup families: incremental (minimal changes),
compromise (balanced tradeoffs), and baseline_reset (start fresh
from physics defaults).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from solver.predictor import PredictedTelemetry


@dataclass
class SetupCandidate:
    """A candidate setup from one family strategy."""

    family: str  # incremental | compromise | baseline_reset
    description: str
    step1: Any = None  # RakeSolution
    step2: Any = None  # HeaveSolution
    step3: Any = None  # CornerSpringSolution
    step4: Any = None  # ARBSolution
    step5: Any = None  # GeometrySolution
    step6: Any = None  # DamperSolution
    supporting: Any = None  # SupportingSolution
    predicted: PredictedTelemetry | None = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
