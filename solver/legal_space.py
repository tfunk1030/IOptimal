"""Legal Search Space — reusable manifold of all searchable setup parameters.

Centralizes the mechanics of:
- What parameters are searchable (Tier A high-leverage vs Tier B contextual)
- Which are discrete vs continuous
- What are the legal values / ranges
- How to enumerate or sample them
- How to snap candidates to legal garage values

Built from car_model.setup_registry (canonical field definitions) and
car_model.cars (per-car ranges/options).

Usage:
    from solver.legal_space import LegalSpace
    space = LegalSpace.from_car(car, track_name="sebring")
    candidates = space.sample_seeded(base_params, n=1000)
    snapped = space.snap(candidate)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from itertools import product as cart_product
from typing import Sequence

import numpy as np

from car_model.cars import CarModel
from car_model.setup_registry import (
    FIELD_REGISTRY,
    CAR_FIELD_SPECS,
    get_field,
    get_car_spec,
)


# ─── Tier classification ────────────────────────────────────────────────────
# Tier A: high-leverage searchable parameters (main optimizer)
# Tier B: contextual / less urgent (add in phase 2)

TIER_A_KEYS: list[str] = [
    # Step 1
    "wing_angle_deg",
    "front_pushrod_offset_mm",
    "rear_pushrod_offset_mm",
    # Step 2 — perch offsets are DEPENDENT variables, computed from
    # spring rate + pushrod offset + target ride height. They are NOT
    # searched independently. See compute_perch_offsets().
    "front_heave_spring_nmm",
    "rear_third_spring_nmm",
    # Step 3
    "front_torsion_od_mm",
    "rear_spring_rate_nmm",
    # Step 4
    "front_arb_blade",
    "rear_arb_blade",
    # Step 5
    "front_camber_deg",
    "rear_camber_deg",
    # Step 6
    "front_ls_comp",
    "front_ls_rbd",
    "front_hs_comp",
    "front_hs_rbd",
    "front_hs_slope",
    "rear_ls_comp",
    "rear_ls_rbd",
    "rear_hs_comp",
    "rear_hs_rbd",
    "rear_hs_slope",
    # Supporting
    "brake_bias_pct",
    "diff_preload_nm",
]

# Perch offset keys — removed from Tier A because they are dependent
# variables computed from other parameters. Kept here for reference
# and for the compute_perch_offsets() helper.
DEPENDENT_PERCH_KEYS: list[str] = [
    "front_heave_perch_mm",
    "rear_third_perch_mm",
    "rear_spring_perch_mm",
]

TIER_B_KEYS: list[str] = [
    "front_toe_mm",
    "rear_toe_mm",
    "front_arb_size",
    "rear_arb_size",
    "tc_gain",
    "tc_slip",
    "diff_clutch_plates",
    "fuel_l",
]


@dataclass
class SearchDimension:
    """One searchable parameter in the legal manifold."""
    name: str                  # canonical_key
    solver_step: int | str | None
    kind: str                  # "discrete" | "ordinal" | "continuous"
    tier: str                  # "A" | "B"
    lo: float                  # minimum legal value
    hi: float                  # maximum legal value
    resolution: float          # minimum step size (0.0 = truly continuous)
    discrete_values: list[float] | None = None  # for discrete/ordinal types

    @property
    def n_values(self) -> int:
        """Number of distinct legal values in this dimension."""
        if self.discrete_values is not None:
            return len(self.discrete_values)
        if self.resolution > 0:
            return int(round((self.hi - self.lo) / self.resolution)) + 1
        return 0  # truly continuous

    def legal_values(self) -> list[float]:
        """Enumerate all legal values (for discrete/ordinal dimensions)."""
        if self.discrete_values is not None:
            return list(self.discrete_values)
        if self.resolution > 0:
            vals = []
            v = self.lo
            while v <= self.hi + 1e-9:
                vals.append(round(v, 6))
                v += self.resolution
            return vals
        return []

    def sample(self, n: int, rng: random.Random | None = None) -> list[float]:
        """Sample n values uniformly from this dimension."""
        rng = rng or random.Random()
        if self.discrete_values is not None:
            return [rng.choice(self.discrete_values) for _ in range(n)]
        if self.resolution > 0:
            vals = self.legal_values()
            return [rng.choice(vals) for _ in range(n)]
        return [rng.uniform(self.lo, self.hi) for _ in range(n)]

    def snap(self, value: float) -> float:
        """Snap a value to the nearest legal value."""
        clamped = max(self.lo, min(self.hi, value))
        if self.discrete_values is not None:
            return min(self.discrete_values, key=lambda v: abs(v - clamped))
        if self.resolution > 0:
            steps = round((clamped - self.lo) / self.resolution)
            return round(self.lo + steps * self.resolution, 6)
        return clamped

    def clamp(self, value: float) -> float:
        """Clamp to legal range without snapping to resolution."""
        return max(self.lo, min(self.hi, value))


@dataclass
class LegalCandidate:
    """A point in the legal search manifold."""
    params: dict[str, float]
    family: str = "sampled"
    score: float = 0.0
    is_extreme: bool = False
    is_boundary: bool = False
    soft_penalties: list[str] = field(default_factory=list)
    hard_veto_reasons: list[str] = field(default_factory=list)

    @property
    def vetoed(self) -> bool:
        return len(self.hard_veto_reasons) > 0


@dataclass
class SearchBounds:
    """Narrowed search bounds around a seed point."""
    center: dict[str, float]
    radius: dict[str, float]  # max deviation per dimension


def compute_perch_offsets(
    params: dict[str, float],
    car: CarModel,
) -> dict[str, float]:
    """Compute dependent perch offsets from independent parameters.

    Perch offsets (front_heave_perch_mm, rear_third_perch_mm,
    rear_spring_perch_mm) are NOT independent search dimensions — they
    are determined by the spring rates, pushrod offsets, and target ride
    heights. This collapses the search space by ~600,000×.

    The computation uses the car's heave spring model baselines as
    targets: the perch is set to maintain the same preload / travel
    budget relationship that the baseline setup achieves.

    Args:
        params: Candidate parameter dict with at least the spring rate
                and pushrod keys populated.
        car: CarModel with heave_spring and corner_spring attributes.

    Returns:
        Dict with the three computed perch offset values.
    """
    hsm = car.heave_spring
    gr = car.garage_ranges

    # --- Front heave perch ---
    # The front heave perch controls preload on the heave spring.
    # Use the baseline perch as starting point, then adjust based on
    # how far the heave spring rate deviates from baseline.
    # A stiffer heave spring needs less preload (less negative perch)
    # to maintain the same travel budget.
    front_heave_nmm = params.get("front_heave_spring_nmm", 50.0)
    baseline_heave_nmm = 0.5 * sum(hsm.front_spring_range_nmm)
    baseline_perch = hsm.perch_offset_front_baseline_mm

    # Use the slider/perch model if available (BMW has calibrated coefficients)
    if hsm.slider_perch_coeff > 0:
        # Target: place slider at safe distance below max
        target_slider = hsm.max_slider_mm - 3.0
        front_perch = (
            (target_slider - hsm.slider_intercept
             - hsm.slider_heave_coeff * front_heave_nmm)
            / hsm.slider_perch_coeff
        )
    else:
        # Fallback: scale baseline perch proportionally to spring rate change
        rate_ratio = front_heave_nmm / max(baseline_heave_nmm, 1.0)
        front_perch = baseline_perch * (2.0 - rate_ratio)

    # Snap to 0.5mm resolution, clamp to legal range
    front_perch = round(front_perch * 2) / 2
    perch_lo, perch_hi = gr.front_heave_perch_mm
    front_perch = max(perch_lo, min(perch_hi, front_perch))

    # --- Rear third perch ---
    # The rear third perch controls preload on the rear third spring.
    # Use baseline as anchor, adjust for spring rate difference.
    rear_third_nmm = params.get("rear_third_spring_nmm", 450.0)
    baseline_third_nmm = 0.5 * sum(hsm.rear_spring_range_nmm)
    rear_third_baseline_perch = hsm.perch_offset_rear_baseline_mm

    # Simple proportional scaling: stiffer spring → less preload needed
    rate_ratio_rear = rear_third_nmm / max(baseline_third_nmm, 1.0)
    rear_third_perch = rear_third_baseline_perch
    # Small correction: move perch toward center of range as spring stiffens
    perch_lo_r, perch_hi_r = gr.rear_third_perch_mm
    perch_mid_r = 0.5 * (perch_lo_r + perch_hi_r)
    rear_third_perch = rear_third_baseline_perch + (
        (rate_ratio_rear - 1.0) * (perch_mid_r - rear_third_baseline_perch) * 0.3
    )
    rear_third_perch = round(rear_third_perch)
    rear_third_perch = max(perch_lo_r, min(perch_hi_r, rear_third_perch))

    # --- Rear spring perch ---
    # Controls preload on the rear coil springs.
    rear_spring_nmm = params.get("rear_spring_rate_nmm", 160.0)
    lo_rs, hi_rs = car.corner_spring.rear_spring_range_nmm
    baseline_rear_spring = 0.5 * (lo_rs + hi_rs)
    perch_lo_s, perch_hi_s = gr.rear_spring_perch_mm
    rear_spring_baseline_perch = 0.5 * (perch_lo_s + perch_hi_s)

    rate_ratio_spring = rear_spring_nmm / max(baseline_rear_spring, 1.0)
    rear_spring_perch = rear_spring_baseline_perch + (
        (rate_ratio_spring - 1.0)
        * (rear_spring_baseline_perch - perch_lo_s) * 0.3
    )
    rear_spring_perch = round(rear_spring_perch * 2) / 2
    rear_spring_perch = max(perch_lo_s, min(perch_hi_s, rear_spring_perch))

    return {
        "front_heave_perch_mm": front_perch,
        "rear_third_perch_mm": rear_third_perch,
        "rear_spring_perch_mm": rear_spring_perch,
    }


class LegalSpace:
    """The full legal search manifold for a car/track combination.

    Builds SearchDimension objects from the registry and car model.
    Provides sampling, enumeration, snapping, and mutation operations.
    """

    def __init__(
        self,
        car: CarModel,
        dimensions: list[SearchDimension],
        track_name: str = "",
    ):
        self.car = car
        self.dimensions = dimensions
        self._dim_map: dict[str, SearchDimension] = {d.name: d for d in dimensions}
        self.track_name = track_name

    def __len__(self) -> int:
        return len(self.dimensions)

    def __getitem__(self, key: str) -> SearchDimension:
        return self._dim_map[key]

    def tier_a(self) -> list[SearchDimension]:
        """Return only Tier A (high-leverage) dimensions."""
        return [d for d in self.dimensions if d.tier == "A"]

    def tier_b(self) -> list[SearchDimension]:
        """Return only Tier B (contextual) dimensions."""
        return [d for d in self.dimensions if d.tier == "B"]

    @property
    def total_cardinality(self) -> int:
        """Rough estimate of total legal combinations (Tier A only)."""
        product = 1
        for d in self.tier_a():
            n = d.n_values
            if n > 0:
                product *= n
        return product

    def snap(self, params: dict[str, float]) -> dict[str, float]:
        """Snap all parameters to their nearest legal values."""
        result = dict(params)
        for key, val in params.items():
            if key in self._dim_map:
                result[key] = self._dim_map[key].snap(val)
        return result

    def is_legal(self, params: dict[str, float]) -> tuple[bool, list[str]]:
        """Check if a parameter set is within legal bounds.

        Returns (legal, list_of_violations).
        """
        violations: list[str] = []
        for key, val in params.items():
            if key not in self._dim_map:
                continue
            dim = self._dim_map[key]
            if val < dim.lo - 1e-9 or val > dim.hi + 1e-9:
                violations.append(
                    f"{key}={val:.3f} outside [{dim.lo:.3f}, {dim.hi:.3f}]"
                )
            if dim.discrete_values is not None:
                if not any(abs(val - dv) < 1e-6 for dv in dim.discrete_values):
                    violations.append(
                        f"{key}={val:.3f} not in discrete set {dim.discrete_values[:5]}..."
                    )
        return len(violations) == 0, violations

    def sample_uniform(
        self,
        n: int,
        tier: str = "A",
        seed: int | None = None,
    ) -> list[LegalCandidate]:
        """Sample n candidates uniformly from the legal space."""
        rng = random.Random(seed)
        dims = self.tier_a() if tier == "A" else self.dimensions
        candidates = []
        for _ in range(n):
            params: dict[str, float] = {}
            for dim in dims:
                params[dim.name] = dim.sample(1, rng)[0]
            candidates.append(LegalCandidate(params=params, family="uniform"))
        return candidates

    def sample_seeded(
        self,
        base_params: dict[str, float],
        n: int = 500,
        perturbation: float = 0.15,
        seed: int | None = None,
    ) -> list[LegalCandidate]:
        """Sample n candidates around a seed (base solver result).

        Each dimension is perturbed by up to ±perturbation of its range.
        Values are snapped to legal garage increments.

        Args:
            base_params: Seed parameter set (e.g., from physics solver)
            n: Number of candidates to generate
            perturbation: Fraction of range to perturb (0.15 = ±15%)
            seed: Random seed for reproducibility
        """
        rng = random.Random(seed)
        dims = self.tier_a()
        candidates: list[LegalCandidate] = []

        # Always include the base as candidate 0
        snapped_base = self.snap(
            {k: v for k, v in base_params.items() if k in self._dim_map}
        )
        candidates.append(LegalCandidate(
            params=snapped_base,
            family="physics_baseline",
        ))

        for _ in range(n - 1):
            params: dict[str, float] = {}
            for dim in dims:
                base_val = base_params.get(dim.name, (dim.lo + dim.hi) / 2)
                span = (dim.hi - dim.lo) * perturbation
                perturbed = base_val + rng.uniform(-span, span)
                params[dim.name] = dim.snap(perturbed)
            candidates.append(LegalCandidate(params=params, family="seeded"))

        return candidates

    def enumerate_discrete_subspace(
        self,
        fixed: dict[str, float] | None = None,
        keys: list[str] | None = None,
        max_combos: int = 10000,
    ) -> list[LegalCandidate]:
        """Enumerate all combinations of specified discrete dimensions.

        Non-specified dimensions use values from `fixed`.
        Stops early if cardinality exceeds max_combos.
        """
        if fixed is None:
            fixed = {}
        if keys is None:
            # Default: ARB blades + dampers (most impactful discrete params)
            keys = ["front_arb_blade", "rear_arb_blade"]

        dims = [self._dim_map[k] for k in keys if k in self._dim_map]
        if not dims:
            return []

        # Check cardinality
        card = 1
        for d in dims:
            card *= max(d.n_values, 1)
        if card > max_combos:
            return []  # too large to enumerate

        # Generate all combinations
        from itertools import product as cart_product
        value_lists = [d.legal_values() for d in dims]
        candidates: list[LegalCandidate] = []
        for combo in cart_product(*value_lists):
            params = dict(fixed)
            for dim, val in zip(dims, combo):
                params[dim.name] = val
            candidates.append(LegalCandidate(
                params=params,
                family="enumerated",
            ))

        return candidates

    def mutate_candidate(
        self,
        candidate: LegalCandidate,
        n_mutations: int = 2,
        step_size: float = 0.05,
        rng: random.Random | None = None,
    ) -> LegalCandidate:
        """Create a neighbor by mutating n_mutations random dimensions."""
        rng = rng or random.Random()
        dims = self.tier_a()
        mutated = dict(candidate.params)

        targets = rng.sample(dims, min(n_mutations, len(dims)))
        for dim in targets:
            current = mutated.get(dim.name, (dim.lo + dim.hi) / 2)
            span = (dim.hi - dim.lo) * step_size
            new_val = current + rng.uniform(-span, span)
            mutated[dim.name] = dim.snap(new_val)

        return LegalCandidate(params=mutated, family="mutated")

    def sobol_sample(
        self,
        keys: list[str],
        n: int,
        seed: int = 0,
    ) -> list[dict[str, float]]:
        """Quasi-random Sobol sequence over specified dimensions.

        Sobol sequences provide much better space coverage than random
        sampling — they fill the space quasi-uniformly, avoiding the
        clumping and gaps of pseudorandom sampling. For N samples in
        D dimensions, Sobol achieves O(log(N)^D / N) discrepancy vs
        O(1/sqrt(N)) for random.

        Args:
            keys: List of dimension names to sample over.
            n: Number of samples to generate. Rounded up to next
               power of 2 internally (Sobol requirement), but only
               the first n are returned.
            seed: Random seed for scrambled Sobol.

        Returns:
            List of n parameter dicts with values snapped to legal
            garage increments.
        """
        from scipy.stats.qmc import Sobol as SobolEngine

        dims = [self._dim_map[k] for k in keys if k in self._dim_map]
        if not dims:
            return []

        d = len(dims)
        # Sobol requires 2^m samples; we generate enough and truncate
        sampler = SobolEngine(d=d, scramble=True, seed=seed)
        # Sobol.random(n) handles non-power-of-2 n internally
        unit_samples = sampler.random(n)  # shape (n, d), values in [0, 1)

        results: list[dict[str, float]] = []
        for row in unit_samples:
            params: dict[str, float] = {}
            for i, dim in enumerate(dims):
                u = float(row[i])
                if dim.discrete_values is not None:
                    # Map [0,1) to index into discrete values
                    idx = int(u * len(dim.discrete_values))
                    idx = min(idx, len(dim.discrete_values) - 1)
                    params[dim.name] = dim.discrete_values[idx]
                elif dim.resolution > 0:
                    # Map [0,1) to legal value range, snap to resolution
                    raw = dim.lo + u * (dim.hi - dim.lo)
                    params[dim.name] = dim.snap(raw)
                else:
                    params[dim.name] = dim.lo + u * (dim.hi - dim.lo)
            results.append(params)

        return results

    def exhaustive_grid(
        self,
        keys: list[str],
        coarse_keys: dict[str, int] | None = None,
    ) -> list[dict[str, float]]:
        """Full Cartesian product of specified dimensions.

        For dimensions in `keys`, enumerates every legal value.
        For dimensions in `coarse_keys`, uses n evenly-spaced levels
        instead of the full resolution (for high-cardinality dims
        like camber or brake bias).

        Args:
            keys: Dimension names to enumerate at full resolution.
            coarse_keys: {dim_name: n_levels} for coarsened dimensions.
                         These are ALSO included in the grid.

        Returns:
            List of parameter dicts (Cartesian product).
            Returns empty list if total cardinality exceeds 100M.
        """
        if coarse_keys is None:
            coarse_keys = {}

        all_keys = list(keys) + [k for k in coarse_keys if k not in keys]
        value_lists: list[list[float]] = []
        dim_names: list[str] = []

        for key in all_keys:
            if key not in self._dim_map:
                continue
            dim = self._dim_map[key]
            dim_names.append(key)

            if key in coarse_keys:
                n_levels = coarse_keys[key]
                if dim.discrete_values is not None:
                    # Evenly sample from discrete values
                    vals = dim.discrete_values
                    if len(vals) <= n_levels:
                        value_lists.append(list(vals))
                    else:
                        indices = np.linspace(0, len(vals) - 1, n_levels, dtype=int)
                        value_lists.append([vals[i] for i in indices])
                else:
                    # Evenly space n_levels across range, snap to resolution
                    raw = np.linspace(dim.lo, dim.hi, n_levels)
                    value_lists.append([dim.snap(float(v)) for v in raw])
            else:
                vals = dim.legal_values()
                if not vals:
                    # Continuous dim without resolution — use 10 levels
                    raw = np.linspace(dim.lo, dim.hi, 10)
                    value_lists.append([float(v) for v in raw])
                else:
                    value_lists.append(vals)

        if not value_lists:
            return []

        # Safety check: don't blow up memory
        cardinality = 1
        for vl in value_lists:
            cardinality *= len(vl)
            if cardinality > 100_000_000:
                return []

        results: list[dict[str, float]] = []
        for combo in cart_product(*value_lists):
            params: dict[str, float] = {}
            for name, val in zip(dim_names, combo):
                params[name] = val
            results.append(params)

        return results

    def neighborhood(
        self,
        params: dict[str, float],
        steps: int = 1,
    ) -> list[dict[str, float]]:
        """All ±steps neighbors in every dimension.

        For 23 Tier A dims at ±1 step: 46 neighbors.
        Each neighbor differs from `params` in exactly one dimension.

        Args:
            params: Center point in parameter space.
            steps: Number of resolution steps to perturb (default 1).

        Returns:
            List of neighbor parameter dicts.
        """
        neighbors: list[dict[str, float]] = []

        for dim in self.dimensions:
            if dim.name not in params:
                continue

            center_val = params[dim.name]

            if dim.discrete_values is not None:
                # Find index of current value
                try:
                    idx = min(
                        range(len(dim.discrete_values)),
                        key=lambda i: abs(dim.discrete_values[i] - center_val),
                    )
                except ValueError:
                    continue
                for delta in range(-steps, steps + 1):
                    if delta == 0:
                        continue
                    new_idx = idx + delta
                    if 0 <= new_idx < len(dim.discrete_values):
                        neighbor = dict(params)
                        neighbor[dim.name] = dim.discrete_values[new_idx]
                        neighbors.append(neighbor)
            elif dim.resolution > 0:
                for delta in range(-steps, steps + 1):
                    if delta == 0:
                        continue
                    new_val = center_val + delta * dim.resolution
                    if dim.lo - 1e-9 <= new_val <= dim.hi + 1e-9:
                        neighbor = dict(params)
                        neighbor[dim.name] = dim.snap(new_val)
                        neighbors.append(neighbor)

        return neighbors

    @classmethod
    def from_car(
        cls,
        car: CarModel,
        track_name: str = "",
        include_tier_b: bool = False,
    ) -> LegalSpace:
        """Build the legal search space from a car model.

        Combines FIELD_REGISTRY definitions with per-car ranges from
        GarageRanges, CarFieldSpec, and the car's component models.
        """
        car_name = car.canonical_name
        specs = CAR_FIELD_SPECS.get(car_name, {})
        dimensions: list[SearchDimension] = []

        active_keys = list(TIER_A_KEYS)
        if include_tier_b:
            active_keys += TIER_B_KEYS

        for key in active_keys:
            field_def = FIELD_REGISTRY.get(key)
            if field_def is None:
                continue
            if field_def.kind != "settable":
                continue

            tier = "A" if key in TIER_A_KEYS else "B"
            spec = specs.get(key)

            # Determine range, resolution, and discrete values
            dim = _build_dimension(car, field_def, spec, key, tier)
            if dim is not None:
                dimensions.append(dim)

        return cls(car=car, dimensions=dimensions, track_name=track_name)


def _build_dimension(
    car: CarModel,
    field_def,
    spec,
    key: str,
    tier: str,
) -> SearchDimension | None:
    """Build a SearchDimension from field definition and car specs."""
    gr = car.garage_ranges

    # Map canonical keys to car model ranges
    range_map: dict[str, tuple[float, float]] = {
        "wing_angle_deg": (min(car.wing_angles) if car.wing_angles else 12,
                           max(car.wing_angles) if car.wing_angles else 17),
        "front_pushrod_offset_mm": gr.front_pushrod_mm,
        "rear_pushrod_offset_mm": gr.rear_pushrod_mm,
        "front_heave_spring_nmm": gr.front_heave_nmm,
        "front_heave_perch_mm": gr.front_heave_perch_mm,
        "rear_third_spring_nmm": gr.rear_third_nmm,
        "rear_third_perch_mm": gr.rear_third_perch_mm,
        "front_torsion_od_mm": gr.front_torsion_od_mm,
        "rear_spring_rate_nmm": gr.rear_spring_nmm,
        "rear_spring_perch_mm": gr.rear_spring_perch_mm,
        "front_arb_blade": gr.arb_blade,
        "rear_arb_blade": gr.arb_blade,
        "front_camber_deg": gr.camber_front_deg,
        "rear_camber_deg": gr.camber_rear_deg,
        "front_toe_mm": gr.toe_front_mm,
        "rear_toe_mm": gr.toe_rear_mm,
        "brake_bias_pct": (40.0, 60.0),  # broad legal range; car.brake_bias_pct is default
        "diff_preload_nm": gr.diff_preload_nm,
    }

    # Damper ranges from car.damper
    d = car.damper
    damper_ranges = {
        "front_ls_comp": d.ls_comp_range,
        "front_ls_rbd": d.ls_rbd_range,
        "front_hs_comp": d.hs_comp_range,
        "front_hs_rbd": d.hs_rbd_range,
        "front_hs_slope": d.hs_slope_range,
        "rear_ls_comp": d.ls_comp_range,
        "rear_ls_rbd": d.ls_rbd_range,
        "rear_hs_comp": d.hs_comp_range,
        "rear_hs_rbd": d.hs_rbd_range,
        "rear_hs_slope": d.hs_slope_range,
    }
    range_map.update(damper_ranges)

    if key not in range_map:
        # Use spec ranges if available
        if spec and spec.range_min is not None and spec.range_max is not None:
            lo, hi = spec.range_min, spec.range_max
        else:
            return None
    else:
        lo, hi = range_map[key]

    # Determine resolution
    resolution_map: dict[str, float] = {
        "wing_angle_deg": 1.0,
        "front_pushrod_offset_mm": 0.5,
        "rear_pushrod_offset_mm": 0.5,
        "front_heave_spring_nmm": getattr(gr, "heave_spring_resolution_nmm", 10.0),
        "front_heave_perch_mm": 0.5,
        "rear_third_spring_nmm": getattr(gr, "rear_spring_resolution_nmm", 10.0),
        "rear_third_perch_mm": 1.0,
        "rear_spring_rate_nmm": car.corner_spring.rear_spring_step_nmm,
        "rear_spring_perch_mm": 0.5,
        "front_camber_deg": car.geometry.front_camber_step_deg,
        "rear_camber_deg": car.geometry.rear_camber_step_deg,
        "front_toe_mm": car.geometry.front_toe_step_mm,
        "rear_toe_mm": car.geometry.rear_toe_step_mm,
        "brake_bias_pct": 0.5,
        "diff_preload_nm": 5.0,
    }
    # All damper and ARB blade dimensions are integer (resolution = 1)
    for dk in damper_ranges:
        resolution_map[dk] = 1.0
    resolution_map["front_arb_blade"] = 1.0
    resolution_map["rear_arb_blade"] = 1.0

    resolution = resolution_map.get(key, spec.resolution if spec and spec.resolution else 1.0)

    # Discrete values for specific dimensions
    discrete_values: list[float] | None = None
    kind = field_def.value_type

    if key == "wing_angle_deg" and car.wing_angles:
        discrete_values = sorted(car.wing_angles)
        kind = "discrete"
    elif key == "front_torsion_od_mm" and car.corner_spring.front_torsion_od_options:
        discrete_values = sorted(car.corner_spring.front_torsion_od_options)
        kind = "discrete"
    elif key == "diff_clutch_plates":
        if gr.diff_clutch_plates_options:
            discrete_values = [float(x) for x in gr.diff_clutch_plates_options]
            kind = "discrete"
    elif kind in ("discrete", "indexed"):
        # Integer range (dampers, ARB blades, TC)
        discrete_values = list(range(int(lo), int(hi) + 1))
        kind = "ordinal"

    return SearchDimension(
        name=key,
        solver_step=field_def.solver_step,
        kind=kind,
        tier=tier,
        lo=float(lo),
        hi=float(hi),
        resolution=resolution,
        discrete_values=[float(v) for v in discrete_values] if discrete_values else None,
    )
