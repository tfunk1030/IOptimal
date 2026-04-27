"""Physics-anchored calibration fitters.

Companion package to :mod:`car_model.auto_calibrate`. Free-form linear
regression there has many parameters per output and needs ~21 setups to
generalize.  Static deflection and ride-height-under-aero-load follow a
simple compliance law (``defl = F / k_total``), so a 2-parameter
``α/β`` fit suffices and unblocks calibration with as few as ~5 setups.

See :mod:`car_model.calibration.compliance_anchored`.
"""

from car_model.calibration.compliance_anchored import (
    ComplianceAnchoredFit,
    fit_compliance_anchored,
    maybe_replace_with_anchored,
)

__all__ = [
    "ComplianceAnchoredFit",
    "fit_compliance_anchored",
    "maybe_replace_with_anchored",
]
