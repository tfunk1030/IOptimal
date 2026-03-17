"""Comparison mode — multi-IBT setup analysis & synthesis.

Usage:
    python -m comparison --car bmw --ibt s1.ibt s2.ibt s3.ibt --wing 17
    python -m comparison --car bmw --ibt s1.ibt s2.ibt --wing 17 --sto optimal.sto
"""

from __future__ import annotations

from importlib import import_module

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


_LAZY_IMPORTS = {
    "SessionAnalysis": ("comparison.compare", "SessionAnalysis"),
    "ComparisonResult": ("comparison.compare", "ComparisonResult"),
    "analyze_session": ("comparison.compare", "analyze_session"),
    "compare_sessions": ("comparison.compare", "compare_sessions"),
    "SessionScore": ("comparison.score", "SessionScore"),
    "score_sessions": ("comparison.score", "score_sessions"),
    "SynthesisResult": ("comparison.synthesize", "SynthesisResult"),
    "synthesize_setup": ("comparison.synthesize", "synthesize_setup"),
}


def __getattr__(name: str):
    if name not in _LAZY_IMPORTS:
        raise AttributeError(f"module 'comparison' has no attribute {name!r}")
    module_name, attr_name = _LAZY_IMPORTS[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
