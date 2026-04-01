"""Legality registry for iRacing GTP class.

Single source of truth for ALL legal constraints. Every solver (objective,
heave_solver, legality_engine, corner_spring_solver) imports from here.

NO legality constants anywhere else in the codebase.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "legality"


# ─── Legality schema ──────────────────────────────────────────────────────────

@dataclass
class RideHeightLimits:
    front_static_min_mm: float = 30.0
    front_static_max_mm: float = 80.0
    rear_static_min_mm: float = 30.0
    rear_static_max_mm: float = 80.0
    front_dynamic_min_mm: float = 5.0
    rear_dynamic_min_mm: float = 25.0


@dataclass
class DeflectionLimits:
    heave_spring_min_mm: float = 0.6
    heave_spring_max_mm: float = 25.0
    heave_slider_min_mm: float = 25.0
    heave_slider_max_mm: float = 45.0


@dataclass
class WeightLimits:
    min_car_kg: float = 1030.0


@dataclass
class TyreLimits:
    min_cold_pressure_kpa: float = 165.0
    max_cold_pressure_kpa: float = 220.0


@dataclass
class ParameterSchema:
    """Legal ranges for each tunable parameter, per car.

    Ranges come from two sources:
      - iRacing garage hard limits (what the UI allows you to set)
      - Observed range from all IBT sessions for this car
    Auto-updated by GarageModelBuilder.update_from_observation().
    """
    # Ride height
    front_rh_static_mm: tuple[float, float] = (30.0, 80.0)
    rear_rh_static_mm: tuple[float, float] = (30.0, 80.0)

    # Wing
    wing_min: float = 12.0
    wing_max: float = 17.0
    wing_options: list[float] = field(default_factory=lambda: [
        12.0, 13.0, 14.0, 15.0, 16.0, 17.0
    ])

    # Heave springs — range in physical units (N/mm) or index depending on car
    front_heave_min: float = 0.0
    front_heave_max: float = 900.0
    rear_heave_min: float = 100.0
    rear_heave_max: float = 1000.0

    # Torsion bar — OD (mm) for BMW/Cadillac/Porsche, index (int) for Ferrari/Acura
    front_torsion_min: float = 0.0
    front_torsion_max: float = 18.0

    # ARB
    front_arb_blades: int = 5      # max blade count
    rear_arb_blades: int = 5

    # Dampers — max clicks per channel
    damper_max_clicks_ls: int = 11   # BMW/Cadillac/Porsche/Acura default
    damper_max_clicks_hs: int = 11

    # Camber
    front_camber_min_deg: float = -4.0
    front_camber_max_deg: float = 0.0
    rear_camber_min_deg: float = -3.0
    rear_camber_max_deg: float = 0.0

    # Toe
    front_toe_min_mm: float = -2.0
    front_toe_max_mm: float = 2.0
    rear_toe_min_mm: float = -2.0
    rear_toe_max_mm: float = 2.0

    # Diff
    diff_preload_min_nm: float = 0.0
    diff_preload_max_nm: float = 100.0

    # Brake bias
    brake_bias_min_pct: float = 44.0
    brake_bias_max_pct: float = 58.0


@dataclass
class CarLegalitySchema:
    """Complete legality spec for one car. Loaded by all solvers."""
    car: str
    ride_height: RideHeightLimits = field(default_factory=RideHeightLimits)
    deflections: DeflectionLimits = field(default_factory=DeflectionLimits)
    weight: WeightLimits = field(default_factory=WeightLimits)
    tyres: TyreLimits = field(default_factory=TyreLimits)
    params: ParameterSchema = field(default_factory=ParameterSchema)

    def check_static_rh(self, front_mm: float, rear_mm: float) -> list[str]:
        """Return list of violations (empty = legal)."""
        violations = []
        rh = self.ride_height
        if front_mm < rh.front_static_min_mm:
            violations.append(
                f"Front static RH {front_mm:.1f}mm < {rh.front_static_min_mm}mm minimum"
            )
        if front_mm > rh.front_static_max_mm:
            violations.append(
                f"Front static RH {front_mm:.1f}mm > {rh.front_static_max_mm}mm maximum"
            )
        if rear_mm < rh.rear_static_min_mm:
            violations.append(
                f"Rear static RH {rear_mm:.1f}mm < {rh.rear_static_min_mm}mm minimum"
            )
        return violations

    def check_deflections(
        self,
        heave_spring_mm: float | None,
        heave_slider_mm: float | None,
    ) -> list[str]:
        violations = []
        d = self.deflections
        if heave_spring_mm is not None:
            if heave_spring_mm < d.heave_spring_min_mm:
                violations.append(
                    f"Heave spring defl {heave_spring_mm:.2f}mm < {d.heave_spring_min_mm}mm"
                )
            if heave_spring_mm > d.heave_spring_max_mm:
                violations.append(
                    f"Heave spring defl {heave_spring_mm:.2f}mm > {d.heave_spring_max_mm}mm"
                )
        if heave_slider_mm is not None:
            if heave_slider_mm < d.heave_slider_min_mm:
                violations.append(
                    f"Heave slider defl {heave_slider_mm:.2f}mm < {d.heave_slider_min_mm}mm"
                )
            if heave_slider_mm > d.heave_slider_max_mm:
                violations.append(
                    f"Heave slider defl {heave_slider_mm:.2f}mm > {d.heave_slider_max_mm}mm"
                )
        return violations


# ─── Registry ────────────────────────────────────────────────────────────────

# GTP-wide defaults — all 5 cars share identical constraints
_GTP_DEFAULTS = CarLegalitySchema(car="gtp_shared")


# Per-car overrides (only where different from GTP defaults)
_CAR_OVERRIDES: dict[str, dict] = {
    "acura": {
        "params": {
            "wing_min": 6.0,
            "wing_max": 10.0,
            "wing_options": [6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0],
            "front_heave_min": 90.0,
            "front_heave_max": 400.0,
            "rear_heave_min": 60.0,
            "rear_heave_max": 300.0,
        }
    },
    "ferrari": {
        "params": {
            # Ferrari exposes indexed springs, not physical N/mm
            # front_heave_index: 0-8 → 30-190 N/mm
            # rear_heave_index:  0-9 → 410-950 N/mm
            # front_torsion_index: 0-18
            # rear_torsion_index:  0-18
            "front_heave_min": 0.0,   # index 0
            "front_heave_max": 8.0,   # index 8
            "rear_heave_min": 0.0,
            "rear_heave_max": 9.0,
            "front_torsion_min": 0.0,
            "front_torsion_max": 18.0,
            "damper_max_clicks_ls": 40,
            "damper_max_clicks_hs": 40,
        }
    },
    "bmw": {
        "params": {
            "front_heave_min": 0.0,
            "front_heave_max": 900.0,
            "rear_heave_min": 100.0,
            "rear_heave_max": 900.0,
            "damper_max_clicks_ls": 11,
            "damper_max_clicks_hs": 11,
        }
    },
    "cadillac": {
        "params": {
            "front_heave_min": 20.0,
            "front_heave_max": 200.0,
            "rear_heave_min": 100.0,
            "rear_heave_max": 1000.0,
            "damper_max_clicks_ls": 11,
            "damper_max_clicks_hs": 11,
        }
    },
    "porsche": {
        "params": {
            "front_heave_min": 20.0,
            "front_heave_max": 200.0,
            "rear_heave_min": 100.0,
            "rear_heave_max": 1000.0,
            # Porsche DSSV dampers — click count unknown, using conservative default
            "damper_max_clicks_ls": 20,
            "damper_max_clicks_hs": 20,
        }
    },
}


@lru_cache(maxsize=10)
def get_legality(car: str) -> CarLegalitySchema:
    """Load legality schema for a car.

    All cars inherit GTP shared constraints. Per-car overrides apply on top.
    Cached after first load. Call get_legality.cache_clear() to refresh.

    Args:
        car: Canonical car name ("bmw", "ferrari", "cadillac", "porsche", "acura")

    Returns:
        CarLegalitySchema for this car
    """
    import copy
    key = car.lower().strip()
    schema = copy.deepcopy(_GTP_DEFAULTS)
    schema.car = key

    overrides = _CAR_OVERRIDES.get(key, {})
    if "params" in overrides:
        for field_name, value in overrides["params"].items():
            if hasattr(schema.params, field_name):
                setattr(schema.params, field_name, value)

    return schema


def check_setup_legality(
    car: str,
    front_rh_static: float,
    rear_rh_static: float,
    heave_spring_defl: float | None = None,
    heave_slider_defl: float | None = None,
) -> list[str]:
    """Convenience: check all legality constraints for a complete setup.

    Returns list of violation strings. Empty = legal.
    Used by objective.py, legality_engine.py, and heave_solver.py.
    """
    schema = get_legality(car)
    violations = []
    violations.extend(schema.check_static_rh(front_rh_static, rear_rh_static))
    violations.extend(schema.check_deflections(heave_spring_defl, heave_slider_defl))
    return violations
