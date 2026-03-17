from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from car_model.garage import GarageSetupState
from output.garage_validator import validate_and_fix_garage_correlation


@dataclass
class LegalValidation:
    valid: bool
    warnings: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    snapped_or_corrected: bool = False
    source: str = "garage_validator"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_solution_legality(
    *,
    car: Any,
    track_name: str,
    step1: Any,
    step2: Any,
    step3: Any,
    step5: Any,
    fuel_l: float,
) -> LegalValidation:
    warnings = validate_and_fix_garage_correlation(
        car=car,
        step1=step1,
        step2=step2,
        step3=step3,
        step5=step5,
        fuel_l=fuel_l,
        track_name=track_name,
    )

    garage_model = car.active_garage_output_model(track_name)
    if garage_model is None:
        return LegalValidation(
            valid=True,
            warnings=warnings,
            messages=["No active garage model; range-clamp validation only."],
            snapped_or_corrected=bool(warnings),
        )

    state = GarageSetupState.from_solver_steps(
        step1=step1,
        step2=step2,
        step3=step3,
        step5=step5,
        fuel_l=fuel_l,
    )
    constraint = garage_model.validate(
        state,
        front_excursion_p99_mm=getattr(step2, "front_excursion_at_rate_mm", 0.0),
        front_bottoming_margin_mm=getattr(step2, "front_bottoming_margin_mm", 0.0),
        vortex_burst_margin_mm=getattr(step1, "vortex_burst_margin_mm", 0.0),
    )
    return LegalValidation(
        valid=bool(constraint.valid),
        warnings=warnings,
        messages=list(getattr(constraint, "messages", [])),
        snapped_or_corrected=bool(warnings),
    )
