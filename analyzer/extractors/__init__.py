"""Extractor sub-package: stand-alone analyzer extractors.

Each module here exposes a single ``extract_*`` function that takes
``(ibt: IBTFile, car: CarModel)`` and returns a ``dict[str, float | int | None]``
of measurements. Extractors are physics-first, free of car-specific defaults,
and gate every channel access on ``ibt.has_channel``.
"""
