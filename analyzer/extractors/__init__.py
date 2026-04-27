"""Single-IBT extractors that distill bulk telemetry into compact features.

Each extractor takes an :class:`IBTFile` plus a :class:`CarModel` and returns
a small flat ``dict[str, float]``. The goal is to collapse the ~324k raw
samples in a session into per-session calibration features so we don't need
to run multi-IBT sweeps for every coefficient.
"""
