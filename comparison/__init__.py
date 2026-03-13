"""Comparison mode — multi-IBT setup analysis & synthesis.

Usage:
    python -m comparison --car bmw --ibt s1.ibt s2.ibt s3.ibt --wing 17
    python -m comparison --car bmw --ibt s1.ibt s2.ibt --wing 17 --sto optimal.sto
"""

from comparison.compare import SessionAnalysis, ComparisonResult, analyze_session, compare_sessions
from comparison.score import SessionScore, score_sessions
from comparison.synthesize import SynthesisResult, synthesize_setup

__all__ = [
    "SessionAnalysis",
    "ComparisonResult",
    "analyze_session",
    "compare_sessions",
    "SessionScore",
    "score_sessions",
    "SynthesisResult",
    "synthesize_setup",
]
