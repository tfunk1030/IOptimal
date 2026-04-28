"""Physics-anchored calibration helpers.

Companion package to :mod:`car_model.auto_calibrate`. Free-form linear
regression there has many parameters per output and needs ~21 setups to
generalize. Two physics-anchored techniques bring the minimum sample count
down dramatically:

- :mod:`compliance_anchored` — static deflection and ride-height-under-aero-load
  follow a simple compliance law (``defl = F / k_total``), so a 2-parameter
  ``α/β`` fit suffices and unblocks calibration with as few as ~5 setups.

- :mod:`virtual_anchors` — physics-self-consistent synthetic CalibrationPoint
  generator that anchors the regression intercept and asymptote behaviour
  when real-data sample counts are sparse.
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
