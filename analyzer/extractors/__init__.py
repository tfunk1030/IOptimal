"""Per-IBT signal extractors that distill bulk telemetry into compact features.

Each extractor in this package takes an :class:`IBTFile` plus a :class:`CarModel`
and returns a small flat ``dict[str, float]``. The goal is to collapse the ~324k
raw samples in a session into multiple structured signals per IBT, multiplying
the effective sample count for cars/tracks where collecting more sessions is
expensive.

Extractors must be car-agnostic: they accept a ``CarModel`` and use direct
attribute access (no ``getattr(car, "field", bmw_default)`` patterns).
"""
