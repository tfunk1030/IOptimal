"""Unified garage-output model for garage-visible iRacing setup values.

This module centralizes the BMW/Sebring regressions for:
  - static ride heights
  - torsion bar turns
  - front heave spring static deflection
  - front heave slider static position

It also wraps the existing display-only deflection helpers so solver,
reporting, and writer paths consume a single source of garage truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from car_model.cars import DeflectionModel


@dataclass(frozen=True)
class GarageSetupState:
    """Garage-settable inputs required to predict displayed garage outputs."""

    front_pushrod_mm: float
    rear_pushrod_mm: float
    front_heave_nmm: float
    front_heave_perch_mm: float
    rear_third_nmm: float
    rear_third_perch_mm: float
    front_torsion_od_mm: float
    rear_spring_nmm: float
    rear_spring_perch_mm: float
    front_camber_deg: float
    fuel_l: float = 0.0

    @classmethod
    def from_current_setup(cls, setup: Any) -> "GarageSetupState":
        """Build from analyzer.setup_reader.CurrentSetup-like objects."""
        return cls(
            front_pushrod_mm=float(getattr(setup, "front_pushrod_mm", 0.0)),
            rear_pushrod_mm=float(getattr(setup, "rear_pushrod_mm", 0.0)),
            front_heave_nmm=float(getattr(setup, "front_heave_nmm", 0.0)),
            front_heave_perch_mm=float(getattr(setup, "front_heave_perch_mm", 0.0)),
            rear_third_nmm=float(getattr(setup, "rear_third_nmm", 0.0)),
            rear_third_perch_mm=float(getattr(setup, "rear_third_perch_mm", 0.0)),
            front_torsion_od_mm=float(getattr(setup, "front_torsion_od_mm", 0.0)),
            rear_spring_nmm=float(getattr(setup, "rear_spring_nmm", 0.0)),
            rear_spring_perch_mm=float(getattr(setup, "rear_spring_perch_mm", 0.0)),
            front_camber_deg=float(getattr(setup, "front_camber_deg", 0.0)),
            fuel_l=float(getattr(setup, "fuel_l", 0.0)),
        )

    @classmethod
    def from_solver_steps(
        cls,
        step1: Any,
        step2: Any,
        step3: Any,
        step5: Any | None = None,
        fuel_l: float = 0.0,
        front_camber_deg: float | None = None,
    ) -> "GarageSetupState":
        """Build from solver outputs."""
        if front_camber_deg is None:
            front_camber_deg = (
                float(step5.front_camber_deg)
                if step5 is not None and hasattr(step5, "front_camber_deg")
                else 0.0
            )
        return cls(
            front_pushrod_mm=float(step1.front_pushrod_offset_mm),
            rear_pushrod_mm=float(step1.rear_pushrod_offset_mm),
            front_heave_nmm=float(step2.front_heave_nmm),
            front_heave_perch_mm=float(step2.perch_offset_front_mm),
            rear_third_nmm=float(step2.rear_third_nmm),
            rear_third_perch_mm=float(step2.perch_offset_rear_mm),
            front_torsion_od_mm=float(step3.front_torsion_od_mm),
            rear_spring_nmm=float(step3.rear_spring_rate_nmm),
            rear_spring_perch_mm=float(step3.rear_spring_perch_mm),
            front_camber_deg=float(front_camber_deg),
            fuel_l=float(fuel_l),
        )


@dataclass(frozen=True)
class GarageOutputs:
    """Predicted garage-visible outputs from a setup state."""

    front_static_rh_mm: float
    rear_static_rh_mm: float
    torsion_bar_turns: float
    torsion_bar_defl_mm: float
    front_shock_defl_static_mm: float
    front_shock_defl_max_mm: float
    rear_shock_defl_static_mm: float
    rear_shock_defl_max_mm: float
    heave_spring_defl_static_mm: float
    heave_spring_defl_max_mm: float
    heave_slider_defl_static_mm: float
    heave_slider_defl_max_mm: float
    rear_spring_defl_static_mm: float
    rear_spring_defl_max_mm: float
    third_spring_defl_static_mm: float
    third_spring_defl_max_mm: float
    third_slider_defl_static_mm: float
    available_travel_front_mm: float
    travel_margin_front_mm: float


@dataclass(frozen=True)
class GarageConstraintResult:
    """Hard-constraint evaluation for a garage candidate."""

    valid: bool
    front_static_rh_ok: bool
    heave_slider_ok: bool
    torsion_defl_ok: bool
    travel_margin_ok: bool
    bottoming_ok: bool
    vortex_ok: bool
    available_travel_front_mm: float
    travel_margin_front_mm: float
    messages: list[str] = field(default_factory=list)


@dataclass
class GarageOutputModel:
    """Single-source garage-output model for BMW/Sebring."""

    name: str
    track_keywords: tuple[str, ...] = field(default_factory=tuple)

    # Defaults used when earlier solver stages have not produced all values yet.
    default_front_pushrod_mm: float = -25.5
    default_rear_pushrod_mm: float = -29.0
    default_front_heave_nmm: float = 50.0
    default_front_heave_perch_mm: float = -13.0
    default_rear_third_nmm: float = 530.0
    default_rear_third_perch_mm: float = 42.0
    default_front_torsion_od_mm: float = 13.9
    default_rear_spring_nmm: float = 170.0
    default_rear_spring_perch_mm: float = 30.0
    default_front_camber_deg: float = -2.9
    default_front_shock_defl_max_mm: float = 100.0
    default_rear_shock_defl_max_mm: float = 150.0
    front_rh_floor_mm: float = 30.0
    max_slider_mm: float = 45.0
    min_static_defl_mm: float = 3.0
    max_torsion_bar_defl_mm: float | None = None
    torsion_bar_defl_safety_margin_mm: float = 0.0
    torsion_bar_rate_c: float = 0.0008036
    heave_spring_defl_max_intercept_mm: float = 106.43
    heave_spring_defl_max_slope: float = -0.310

    # Front static RH fit (linear terms)
    front_intercept: float = 0.0
    front_coeff_pushrod: float = 0.0
    front_coeff_heave_nmm: float = 0.0
    front_coeff_heave_perch_mm: float = 0.0
    front_coeff_torsion_od_mm: float = 0.0
    front_coeff_camber_deg: float = 0.0
    front_coeff_fuel_l: float = 0.0
    # Front compliance terms (physics-correct: ∝ 1/k under aero load)
    front_coeff_inv_heave_nmm: float = 0.0

    # Rear static RH fit (linear terms)
    rear_intercept: float = 0.0
    rear_coeff_pushrod: float = 0.0
    rear_coeff_third_nmm: float = 0.0
    rear_coeff_third_perch_mm: float = 0.0
    rear_coeff_rear_spring_nmm: float = 0.0
    rear_coeff_rear_spring_perch_mm: float = 0.0
    rear_coeff_front_heave_perch_mm: float = 0.0
    rear_coeff_fuel_l: float = 0.0
    # Rear compliance terms (physics-correct: ∝ 1/k under aero load)
    rear_coeff_inv_third_nmm: float = 0.0
    rear_coeff_inv_rear_spring_nmm: float = 0.0

    # Torsion turns fit
    torsion_turns_intercept: float = 0.0
    torsion_turns_coeff_heave_nmm: float = 0.0
    torsion_turns_coeff_heave_perch_mm: float = 0.0
    torsion_turns_coeff_torsion_od_mm: float = 0.0
    torsion_turns_coeff_front_rh_mm: float = 0.0

    # Front heave spring/slider display fit
    heave_defl_intercept: float = 0.0
    heave_defl_coeff_heave_nmm: float = 0.0
    heave_defl_coeff_heave_perch_mm: float = 0.0
    heave_defl_coeff_torsion_od_mm: float = 0.0
    heave_defl_coeff_front_pushrod_mm: float = 0.0
    heave_defl_coeff_front_rh_mm: float = 0.0
    heave_defl_coeff_torsion_turns: float = 0.0
    # Inverse-feature coefficients (Porsche: defl ~ 1/heave_nmm, not ~ heave_nmm)
    heave_defl_coeff_inv_heave_nmm: float = 0.0
    heave_defl_coeff_inv_od4: float = 0.0

    slider_intercept: float = 0.0
    slider_coeff_heave_nmm: float = 0.0
    slider_coeff_heave_perch_mm: float = 0.0
    slider_coeff_torsion_od_mm: float = 0.0
    slider_coeff_front_pushrod_mm: float = 0.0
    slider_coeff_front_rh_mm: float = 0.0
    slider_coeff_torsion_turns: float = 0.0

    deflection: "DeflectionModel | None" = None

    def applies_to_track(self, track_name: str | None) -> bool:
        """Whether this model is the authoritative garage path for the track."""
        if not self.track_keywords:
            return True
        if not track_name:
            return False
        haystack = track_name.lower().replace("_", " ")
        return any(keyword.lower() in haystack for keyword in self.track_keywords)

    def default_state(self, fuel_l: float = 0.0) -> GarageSetupState:
        """Baseline state used before later solver stages fill in all inputs."""
        return GarageSetupState(
            front_pushrod_mm=self.default_front_pushrod_mm,
            rear_pushrod_mm=self.default_rear_pushrod_mm,
            front_heave_nmm=self.default_front_heave_nmm,
            front_heave_perch_mm=self.default_front_heave_perch_mm,
            rear_third_nmm=self.default_rear_third_nmm,
            rear_third_perch_mm=self.default_rear_third_perch_mm,
            front_torsion_od_mm=self.default_front_torsion_od_mm,
            rear_spring_nmm=self.default_rear_spring_nmm,
            rear_spring_perch_mm=self.default_rear_spring_perch_mm,
            front_camber_deg=self.default_front_camber_deg,
            fuel_l=fuel_l,
        )

    def predict_front_static_rh_raw(self, setup: GarageSetupState) -> float:
        """Predict unclamped front static ride height from the garage state."""
        inv_heave = 0.0
        if abs(self.front_coeff_inv_heave_nmm) > 1e-9 and setup.front_heave_nmm > 0:
            inv_heave = 1.0 / setup.front_heave_nmm
        return (
            self.front_intercept
            + self.front_coeff_pushrod * setup.front_pushrod_mm
            + self.front_coeff_heave_nmm * setup.front_heave_nmm
            + self.front_coeff_inv_heave_nmm * inv_heave
            + self.front_coeff_heave_perch_mm * setup.front_heave_perch_mm
            + self.front_coeff_torsion_od_mm * setup.front_torsion_od_mm
            + self.front_coeff_camber_deg * setup.front_camber_deg
            + self.front_coeff_fuel_l * setup.fuel_l
        )

    def predict_front_static_rh(self, setup: GarageSetupState) -> float:
        """Predict front static ride height.

        Returns the raw prediction without floor clamping so that downstream
        code can detect when a setup combination would produce a sub-legal
        ride height.  Callers that need the floor-clamped value should use
        ``max(model.front_rh_floor_mm, prediction)`` explicitly.
        """
        return self.predict_front_static_rh_raw(setup)

    def predict_rear_static_rh(self, setup: GarageSetupState) -> float:
        """Predict rear static ride height."""
        inv_third = 0.0
        if abs(self.rear_coeff_inv_third_nmm) > 1e-9 and setup.rear_third_nmm > 0:
            inv_third = 1.0 / setup.rear_third_nmm
        inv_rspring = 0.0
        if abs(self.rear_coeff_inv_rear_spring_nmm) > 1e-9 and setup.rear_spring_nmm > 0:
            inv_rspring = 1.0 / setup.rear_spring_nmm
        return (
            self.rear_intercept
            + self.rear_coeff_pushrod * setup.rear_pushrod_mm
            + self.rear_coeff_third_nmm * setup.rear_third_nmm
            + self.rear_coeff_inv_third_nmm * inv_third
            + self.rear_coeff_third_perch_mm * setup.rear_third_perch_mm
            + self.rear_coeff_rear_spring_nmm * setup.rear_spring_nmm
            + self.rear_coeff_inv_rear_spring_nmm * inv_rspring
            + self.rear_coeff_rear_spring_perch_mm * setup.rear_spring_perch_mm
            + self.rear_coeff_front_heave_perch_mm * setup.front_heave_perch_mm
            + self.rear_coeff_fuel_l * setup.fuel_l
        )

    def predict_torsion_turns(
        self,
        setup: GarageSetupState,
        front_static_rh_mm: float | None = None,
    ) -> float:
        """Predict front torsion bar turns."""
        if front_static_rh_mm is None:
            front_static_rh_mm = self.predict_front_static_rh(setup)
        turns = (
            self.torsion_turns_intercept
            + self.torsion_turns_coeff_heave_nmm * setup.front_heave_nmm
            + self.torsion_turns_coeff_heave_perch_mm * setup.front_heave_perch_mm
            + self.torsion_turns_coeff_torsion_od_mm * setup.front_torsion_od_mm
            + self.torsion_turns_coeff_front_rh_mm * front_static_rh_mm
        )
        return max(0.0, turns)

    def predict_heave_spring_defl_static(
        self,
        setup: GarageSetupState,
        torsion_turns: float | None = None,
        front_static_rh_mm: float | None = None,
    ) -> float:
        """Predict front heave spring static compression."""
        if torsion_turns is None:
            torsion_turns = self.predict_torsion_turns(setup)
        if front_static_rh_mm is None:
            front_static_rh_mm = self.predict_front_static_rh(setup)
        return max(
            0.0,
            self.heave_defl_intercept
            + self.heave_defl_coeff_heave_nmm * setup.front_heave_nmm
            + self.heave_defl_coeff_heave_perch_mm * setup.front_heave_perch_mm
            + self.heave_defl_coeff_torsion_od_mm * setup.front_torsion_od_mm
            + self.heave_defl_coeff_front_pushrod_mm * setup.front_pushrod_mm
            + self.heave_defl_coeff_front_rh_mm * front_static_rh_mm
            + self.heave_defl_coeff_torsion_turns * torsion_turns
            + self.heave_defl_coeff_inv_heave_nmm / max(setup.front_heave_nmm, 1.0)
            + self.heave_defl_coeff_inv_od4 / max(setup.front_torsion_od_mm ** 4, 1.0),
        )

    def predict_heave_slider_defl_static(
        self,
        setup: GarageSetupState,
        torsion_turns: float | None = None,
        front_static_rh_mm: float | None = None,
    ) -> float:
        """Predict front heave slider static position."""
        if torsion_turns is None:
            torsion_turns = self.predict_torsion_turns(setup)
        if front_static_rh_mm is None:
            front_static_rh_mm = self.predict_front_static_rh(setup)
        return max(
            0.0,
            self.slider_intercept
            + self.slider_coeff_heave_nmm * setup.front_heave_nmm
            + self.slider_coeff_heave_perch_mm * setup.front_heave_perch_mm
            + self.slider_coeff_torsion_od_mm * setup.front_torsion_od_mm
            + self.slider_coeff_front_pushrod_mm * setup.front_pushrod_mm
            + self.slider_coeff_front_rh_mm * front_static_rh_mm
            + self.slider_coeff_torsion_turns * torsion_turns,
        )

    def front_pushrod_for_static_rh(
        self,
        target_rh_mm: float,
        *,
        front_heave_nmm: float,
        front_heave_perch_mm: float,
        front_torsion_od_mm: float,
        front_camber_deg: float,
        fuel_l: float = 0.0,
    ) -> float:
        """Invert the front RH regression to a pushrod offset."""
        if abs(self.front_coeff_pushrod) < 1e-9:
            return self.default_front_pushrod_mm
        target = max(target_rh_mm, self.front_rh_floor_mm)
        inv_heave = 1.0 / front_heave_nmm if (abs(self.front_coeff_inv_heave_nmm) > 1e-9 and front_heave_nmm > 0) else 0.0
        other = (
            self.front_intercept
            + self.front_coeff_heave_nmm * front_heave_nmm
            + self.front_coeff_inv_heave_nmm * inv_heave
            + self.front_coeff_heave_perch_mm * front_heave_perch_mm
            + self.front_coeff_torsion_od_mm * front_torsion_od_mm
            + self.front_coeff_camber_deg * front_camber_deg
            + self.front_coeff_fuel_l * fuel_l
        )
        return (target - other) / self.front_coeff_pushrod

    def rear_pushrod_for_static_rh(
        self,
        target_rh_mm: float,
        *,
        rear_third_nmm: float,
        rear_third_perch_mm: float,
        rear_spring_nmm: float,
        rear_spring_perch_mm: float,
        front_heave_perch_mm: float,
        fuel_l: float = 0.0,
    ) -> float:
        """Invert the rear RH regression to a pushrod offset."""
        if abs(self.rear_coeff_pushrod) < 1e-9:
            return self.default_rear_pushrod_mm
        inv_third = 1.0 / rear_third_nmm if (abs(self.rear_coeff_inv_third_nmm) > 1e-9 and rear_third_nmm > 0) else 0.0
        inv_rspring = 1.0 / rear_spring_nmm if (abs(self.rear_coeff_inv_rear_spring_nmm) > 1e-9 and rear_spring_nmm > 0) else 0.0
        other = (
            self.rear_intercept
            + self.rear_coeff_third_nmm * rear_third_nmm
            + self.rear_coeff_inv_third_nmm * inv_third
            + self.rear_coeff_third_perch_mm * rear_third_perch_mm
            + self.rear_coeff_rear_spring_nmm * rear_spring_nmm
            + self.rear_coeff_inv_rear_spring_nmm * inv_rspring
            + self.rear_coeff_rear_spring_perch_mm * rear_spring_perch_mm
            + self.rear_coeff_front_heave_perch_mm * front_heave_perch_mm
            + self.rear_coeff_fuel_l * fuel_l
        )
        return (target_rh_mm - other) / self.rear_coeff_pushrod

    def predict(
        self,
        setup: GarageSetupState,
        front_excursion_p99_mm: float = 0.0,
    ) -> GarageOutputs:
        """Predict the unified set of garage outputs for a setup."""
        front_static_rh = self.predict_front_static_rh(setup)
        rear_static_rh = self.predict_rear_static_rh(setup)
        torsion_turns = self.predict_torsion_turns(setup, front_static_rh)
        heave_defl_static = self.predict_heave_spring_defl_static(
            setup,
            torsion_turns,
            front_static_rh,
        )
        heave_slider_static = self.predict_heave_slider_defl_static(
            setup,
            torsion_turns,
            front_static_rh,
        )
        heave_defl_max = (
            self.heave_spring_defl_max_intercept_mm
            + self.heave_spring_defl_max_slope * setup.front_heave_nmm
        )
        available_travel = max(0.0, heave_defl_max - heave_defl_static)
        travel_margin = available_travel - front_excursion_p99_mm

        front_shock_defl_static = 0.0
        rear_shock_defl_static = 0.0
        torsion_bar_defl = 0.0
        rear_spring_defl_static = 0.0
        rear_spring_defl_max = 0.0
        third_spring_defl_static = 0.0
        third_spring_defl_max = 0.0
        third_slider_defl_static = 0.0
        if self.deflection is not None:
            front_shock_defl_static = self.deflection.shock_defl_front(setup.front_pushrod_mm)
            rear_shock_defl_static = self.deflection.shock_defl_rear(
                setup.rear_pushrod_mm,
                third_rate_nmm=setup.rear_third_nmm,
                spring_rate_nmm=setup.rear_spring_nmm,
                third_perch_mm=setup.rear_third_perch_mm,
                spring_perch_mm=setup.rear_spring_perch_mm,
            )
            torsion_bar_rate = self.torsion_bar_rate_c * setup.front_torsion_od_mm ** 4
            torsion_bar_defl = self.deflection.torsion_bar_defl(
                setup.front_heave_nmm,
                setup.front_heave_perch_mm,
                torsion_bar_rate,
            )
            rear_spring_defl_static = self.deflection.rear_spring_defl_static(
                setup.rear_spring_nmm,
                setup.rear_spring_perch_mm,
                third_rate_nmm=setup.rear_third_nmm,
                third_perch_mm=setup.rear_third_perch_mm,
                pushrod_mm=setup.rear_pushrod_mm,
            )
            rear_spring_defl_max = self.deflection.rear_spring_defl_max(
                setup.rear_spring_nmm,
                setup.rear_spring_perch_mm,
            )
            third_spring_defl_static = self.deflection.third_spring_defl_static(
                setup.rear_third_nmm,
                setup.rear_third_perch_mm,
                spring_rate_nmm=setup.rear_spring_nmm,
                spring_perch_mm=setup.rear_spring_perch_mm,
                pushrod_mm=setup.rear_pushrod_mm,
            )
            third_spring_defl_max = self.deflection.third_spring_defl_max(
                setup.rear_third_nmm,
                setup.rear_third_perch_mm,
            )
            third_slider_defl_static = self.deflection.third_slider_defl_static(
                third_spring_defl_static,
            )

        return GarageOutputs(
            front_static_rh_mm=round(front_static_rh, 3),
            rear_static_rh_mm=round(rear_static_rh, 3),
            torsion_bar_turns=round(torsion_turns, 4),
            torsion_bar_defl_mm=round(torsion_bar_defl, 3),
            front_shock_defl_static_mm=round(front_shock_defl_static, 3),
            front_shock_defl_max_mm=float(self.default_front_shock_defl_max_mm),
            rear_shock_defl_static_mm=round(rear_shock_defl_static, 3),
            rear_shock_defl_max_mm=float(self.default_rear_shock_defl_max_mm),
            heave_spring_defl_static_mm=round(heave_defl_static, 3),
            heave_spring_defl_max_mm=round(heave_defl_max, 3),
            heave_slider_defl_static_mm=round(heave_slider_static, 3),
            heave_slider_defl_max_mm=float(self.max_slider_mm),
            rear_spring_defl_static_mm=round(rear_spring_defl_static, 3),
            rear_spring_defl_max_mm=round(rear_spring_defl_max, 3),
            third_spring_defl_static_mm=round(third_spring_defl_static, 3),
            third_spring_defl_max_mm=round(third_spring_defl_max, 3),
            third_slider_defl_static_mm=round(third_slider_defl_static, 3),
            available_travel_front_mm=round(available_travel, 3),
            travel_margin_front_mm=round(travel_margin, 3),
        )

    def effective_torsion_bar_defl_limit_mm(self) -> float | None:
        """Operational torsion-bar limit after applying a safety buffer."""
        if self.max_torsion_bar_defl_mm is None:
            return None
        return max(
            0.0,
            float(self.max_torsion_bar_defl_mm) - max(0.0, float(self.torsion_bar_defl_safety_margin_mm)),
        )

    def validate(
        self,
        setup: GarageSetupState,
        *,
        front_excursion_p99_mm: float = 0.0,
        min_travel_margin_mm: float = 0.0,
        front_bottoming_margin_mm: float | None = None,
        vortex_burst_margin_mm: float | None = None,
    ) -> GarageConstraintResult:
        """Validate hard garage and platform constraints for a candidate."""
        outputs = self.predict(setup, front_excursion_p99_mm=front_excursion_p99_mm)
        front_static_raw = self.predict_front_static_rh_raw(setup)
        front_static_ok = front_static_raw >= self.front_rh_floor_mm - 1e-6
        slider_ok = outputs.heave_slider_defl_static_mm <= self.max_slider_mm + 1e-6
        torsion_limit = self.effective_torsion_bar_defl_limit_mm()
        torsion_defl_ok = (
            True if torsion_limit is None
            else outputs.torsion_bar_defl_mm <= torsion_limit + 1e-6
        )
        travel_ok = outputs.travel_margin_front_mm >= min_travel_margin_mm - 1e-6
        bottoming_ok = (
            True if front_bottoming_margin_mm is None
            else front_bottoming_margin_mm >= -1e-6
        )
        vortex_ok = (
            True if vortex_burst_margin_mm is None
            else vortex_burst_margin_mm >= -1e-6
        )
        messages: list[str] = []
        if not front_static_ok:
            messages.append(
                f"front static RH {front_static_raw:.2f}mm < floor {self.front_rh_floor_mm:.1f}mm"
            )
        if not slider_ok:
            messages.append(
                f"heave slider {outputs.heave_slider_defl_static_mm:.2f}mm > limit {self.max_slider_mm:.1f}mm"
            )
        if not torsion_defl_ok:
            if (
                self.max_torsion_bar_defl_mm is not None
                and torsion_limit is not None
                and torsion_limit < self.max_torsion_bar_defl_mm - 1e-6
            ):
                messages.append(
                    f"torsion bar defl {outputs.torsion_bar_defl_mm:.2f}mm > operating limit "
                    f"{torsion_limit:.1f}mm (hard limit {self.max_torsion_bar_defl_mm:.1f}mm)"
                )
            else:
                messages.append(
                    f"torsion bar defl {outputs.torsion_bar_defl_mm:.2f}mm > limit {torsion_limit:.1f}mm"
                )
        if not travel_ok:
            messages.append(
                f"front travel margin {outputs.travel_margin_front_mm:.2f}mm < {min_travel_margin_mm:.1f}mm"
            )
        if not bottoming_ok and front_bottoming_margin_mm is not None:
            messages.append(f"front bottoming margin {front_bottoming_margin_mm:.2f}mm < 0")
        if not vortex_ok and vortex_burst_margin_mm is not None:
            messages.append(f"front vortex margin {vortex_burst_margin_mm:.2f}mm < 0")
        valid = front_static_ok and slider_ok and torsion_defl_ok and travel_ok and bottoming_ok and vortex_ok
        return GarageConstraintResult(
            valid=valid,
            front_static_rh_ok=front_static_ok,
            heave_slider_ok=slider_ok,
            torsion_defl_ok=torsion_defl_ok,
            travel_margin_ok=travel_ok,
            bottoming_ok=bottoming_ok,
            vortex_ok=vortex_ok,
            available_travel_front_mm=outputs.available_travel_front_mm,
            travel_margin_front_mm=outputs.travel_margin_front_mm,
            messages=messages,
        )
