"""Per-IBT signal extractors that produce multiple independent measurements.

Each extractor in this package returns multiple structured signals from a
single IBT, multiplying the effective sample count for cars/tracks where
collecting more sessions is expensive. Extractors must be car-agnostic:
they accept a ``CarModel`` and use direct attribute access (no
``getattr(car, "field", bmw_default)`` patterns).
"""
