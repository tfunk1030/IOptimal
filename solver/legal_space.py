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
from typing import Sequence

from car_model.cars import CarModel
from car_model.setup_registry import (
    FIELD_REGISTRY,
    CAR_FIELD_SPECS,
    get_field,
    get_car_spec,
)


# ─── Perch offset computation ────────────────────────────────────────────────
#
# Perch offsets (front_heave_perch_mm, rear_third_perch_mm, rear_spring_perch_mm)
# are DEPENDENT variables — they are derived from the chosen spring rates and the
# target ride heights set by the rake solver. They are NOT independent search dims.
#
# Front static RH empirical model (BMW, 62 sessions, LOO RMSE = 0.066mm):
#   front_static_rh = 30.1458 + 0.001614 * heave_nmm + 0.074486 * camber_deg
#   Units: RH in mm, heave in N/mm, camber in degrees (negative = more negative)
#   R² = 0.71 on held-out data.
#
# Back-derivation of front heave perch:
#   The perch offset controls where the spring sits on the slider. Higher spring
#   rate → spring pushes harder → needs less perch to achieve same RH.
#   Empirically: Δperch ≈ -k_rh_per_nmm * Δheave_nmm
#   where k_rh_per_nmm = 0.001614 mm_RH / (N/mm)  (from the model coefficient)
#   Perch reference measured at heave = 50 N/mm, perch ≈ 0 mm (arbitrary zero).
#   front_heave_perch_mm ≈ perch_ref + (heave_ref - heave_nmm) * k_heave_to_perch
#
# Rear static RH empirical model (4-feature regression, R²=0.97, LOO RMSE=0.845mm):
#   rear_static_rh = f(pushrod, third_nmm, rear_spring, heave_perch)
#   The main lever is the spring perch — stiffer spring → more perch offset needed.
#
# For the search engine, we only need approximate perch values to:
#   1. Keep the setup physically consistent for scoring
#   2. Avoid perch being at an extreme that causes slider exhaustion
# Exact perch values are refined by the full solver after a candidate is selected.

FRONT_HEAVE_PERCH_K = 0.001614   # mm_RH / (N/mm)  — from front RH empirical model
REAR_SPRING_PERCH_K = 0.8        # mm_perch / (N/mm) — rough empirical, rear spring dominant
REAR_THIRD_PERCH_K = 0.3         # mm_perch / (N/mm) — rear third spring contribution

# BMW-Sebring fallback reference values (perch = 0 at these spring rates).
# These are used ONLY when the car doesn't define its own baselines.
# Per-car values are derived from car.heave_spring and car.corner_spring.
_BMW_FRONT_HEAVE_SPRING_REF = 50.0    # N/mm
_BMW_REAR_THIRD_SPRING_REF = 450.0    # N/mm
_BMW_REAR_SPRING_REF = 160.0          # N/mm


def _car_spring_refs(car: CarModel) -> tuple[float, float, float]:
    """Return (front_heave_ref, rear_third_ref, rear_spring_ref) for a car.

    Uses the car's baseline spring rates as reference points for perch offset
    computation. All fields are required on CarModel — no BMW fallbacks.
    """
    front_heave_ref = float(car.front_heave_spring_nmm)
    rear_third_ref = float(car.rear_third_spring_nmm)
    if car.corner_spring.rear_spring_range_nmm:
        rspr_lo, rspr_hi = car.corner_spring.rear_spring_range_nmm
        rear_spring_ref = float((rspr_lo + rspr_hi) / 2.0)
    else:
        rear_spring_ref = _BMW_REAR_SPRING_REF
    return front_heave_ref, rear_third_ref, rear_spring_ref


# Backward-compat aliases (legacy callers may import these names)
FRONT_HEAVE_SPRING_REF = _BMW_FRONT_HEAVE_SPRING_REF
REAR_THIRD_SPRING_REF = _BMW_REAR_THIRD_SPRING_REF
REAR_SPRING_REF = _BMW_REAR_SPRING_REF


def compute_perch_offsets(params: dict, car: CarModel) -> dict:
    """Compute dependent perch offsets from spring rates.

    Perch offsets are NOT independent search dimensions — they're derived from
    the spring rate + target ride height relationship. This function computes
    them from the chosen spring rates so every evaluated candidate has physically
    consistent perch values.

    Physics basis:
      Front: front_static_rh = 30.1458 + 0.001614*heave_nmm + 0.074486*camber_deg
             Δheave → ΔRHS → ΔPerch needed to maintain target RH.
             k_heave_to_rh = 0.001614 mm_RH per N/mm heave.
             Inverse: Δperch ≈ -Δheave_nmm / k_heave_to_rh * perch_sensitivity
      Rear:  Dominated by spring perch; third spring has secondary effect.
             Empirical: stiffer spring needs less negative perch to hold RH.

    Args:
        params: Dict with (at minimum) front_heave_spring_nmm, rear_third_spring_nmm,
                rear_spring_rate_nmm, front_camber_deg
        car:    CarModel for range clamping

    Returns:
        Dict with front_heave_perch_mm, rear_third_perch_mm, rear_spring_perch_mm
    """
    if car is not None and car.canonical_name == "ferrari":
        preserved: dict[str, float] = {}
        for key in ("front_heave_perch_mm", "rear_third_perch_mm", "rear_spring_perch_mm"):
            value = params.get(key)
            if isinstance(value, (int, float)):
                preserved[key] = round(float(value), 2)
        return preserved

    gr = car.garage_ranges
    front_heave_ref, rear_third_ref, rear_spring_ref = _car_spring_refs(car)

    heave_nmm = float(params.get("front_heave_spring_nmm", front_heave_ref))
    third_nmm = float(params.get("rear_third_spring_nmm", rear_third_ref))
    rear_nmm = float(params.get("rear_spring_rate_nmm", rear_spring_ref))

    # Front heave perch:
    # Stiffer heave spring raises front RH (model coeff +0.001614).
    # To compensate and keep the same RH, perch must be reduced (more negative).
    front_perch_ref = float(getattr(gr, "front_heave_perch_ref_mm", 0.0))
    delta_heave = heave_nmm - front_heave_ref
    front_heave_perch = front_perch_ref - delta_heave * 0.08
    if hasattr(gr, "front_heave_perch_mm"):
        lo, hi = gr.front_heave_perch_mm
        front_heave_perch = max(lo, min(hi, front_heave_perch))

    # Rear third perch: third spring rate drives third perch offset
    rear_third_perch_ref = float(getattr(gr, "rear_third_perch_ref_mm", 0.0))
    delta_third = third_nmm - rear_third_ref
    rear_third_perch = rear_third_perch_ref - delta_third * REAR_THIRD_PERCH_K * 0.01
    if hasattr(gr, "rear_third_perch_mm"):
        lo, hi = gr.rear_third_perch_mm
        rear_third_perch = max(lo, min(hi, rear_third_perch))

    # Rear spring perch: stiffer rear spring → less perch offset needed
    rear_spring_perch_ref = float(getattr(gr, "rear_spring_perch_ref_mm", 0.0))
    delta_rear = rear_nmm - rear_spring_ref
    rear_spring_perch = rear_spring_perch_ref - delta_rear * REAR_SPRING_PERCH_K * 0.01
    if hasattr(gr, "rear_spring_perch_mm"):
        lo, hi = gr.rear_spring_perch_mm
        rear_spring_perch = max(lo, min(hi, rear_spring_perch))

    return {
        "front_heave_perch_mm": round(front_heave_perch, 2),
        "rear_third_perch_mm": round(rear_third_perch, 2),
        "rear_spring_perch_mm": round(rear_spring_perch, 2),
    }


# ─── Tier classification ────────────────────────────────────────────────────
# Tier A: high-leverage searchable parameters (main optimizer)
# Tier B: contextual / less urgent (add in phase 2)

TIER_A_KEYS: list[str] = [
    # Step 1
    "wing_angle_deg",
    "front_pushrod_offset_mm",
    "rear_pushrod_offset_mm",
    # Step 2
    "front_heave_spring_nmm",
    # NOTE: front_heave_perch_mm REMOVED — it's a dependent variable computed
    # from spring rate + target RH via compute_perch_offsets(). Keeping it here
    # inflated the search space by ~600,000×. See compute_perch_offsets() below.
    "rear_third_spring_nmm",
    # NOTE: rear_third_perch_mm REMOVED — same reason as front_heave_perch_mm.
    # Step 3
    "front_torsion_od_mm",
    "rear_spring_rate_nmm",
    # NOTE: rear_spring_perch_mm REMOVED — same reason. Dependent on rear spring
    # rate and target rear RH via the rear static RH model.
    # Step 4
    "front_arb_blade",
    "rear_arb_blade",
    "front_arb_size",
    "rear_arb_size",
    # Step 5
    "front_camber_deg",
    "rear_camber_deg",
    "front_toe_mm",
    "rear_toe_mm",
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
    "diff_ramp_option_idx",
    "diff_clutch_plates",
    "tc_gain",
    "tc_slip",
]

# Keys that ARE searchable but are perch offsets — kept for reference / Tier B use
PERCH_KEYS: list[str] = [
    "front_heave_perch_mm",
    "rear_third_perch_mm",
    "rear_spring_perch_mm",
]

TIER_B_KEYS: list[str] = [
    "fuel_l",
]

LOCAL_REFINE_KEYS: list[str] = list(PERCH_KEYS)


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

    def sample_lhs(
        self,
        base_params: dict[str, float],
        n: int = 200,
        perturbation: float = 0.25,
        seed: int | None = None,
    ) -> list[LegalCandidate]:
        """Sample n candidates using Latin Hypercube Sampling around a seed.

        LHS provides better space-filling coverage than uniform random
        perturbation.  Each dimension's range is divided into n equal
        strata, and exactly one sample is drawn from each stratum.

        Falls back to ``sample_seeded`` if scipy is not available.

        Args:
            base_params: Seed parameter set (e.g., from physics solver)
            n: Number of candidates to generate
            perturbation: Fraction of range to explore around seed (0.25 = ±25%)
            seed: Random seed for reproducibility
        """
        dims = self.tier_a()
        if not dims:
            return []

        try:
            from scipy.stats.qmc import LatinHypercube
        except ImportError:
            return self.sample_seeded(base_params, n=n, perturbation=perturbation, seed=seed)

        sampler = LatinHypercube(d=len(dims), seed=seed)
        # LHS produces samples in [0, 1]^d — scale to dimension ranges.
        unit_samples = sampler.random(n=n)

        candidates: list[LegalCandidate] = []
        # Always include the base as candidate 0.
        snapped_base = self.snap(
            {k: v for k, v in base_params.items() if k in self._dim_map}
        )
        candidates.append(LegalCandidate(
            params=snapped_base,
            family="physics_baseline",
        ))

        for i in range(n):
            params: dict[str, float] = {}
            for j, dim in enumerate(dims):
                base_val = base_params.get(dim.name, (dim.lo + dim.hi) / 2)
                span = (dim.hi - dim.lo) * perturbation
                lo_bound = max(dim.lo, base_val - span)
                hi_bound = min(dim.hi, base_val + span)
                # Map [0,1] sample to [lo_bound, hi_bound]
                raw = lo_bound + unit_samples[i, j] * (hi_bound - lo_bound)
                params[dim.name] = dim.snap(raw)
            candidates.append(LegalCandidate(params=params, family="lhs"))

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

    def sobol_sample(
        self,
        keys: list[str],
        n: int,
        seed: int = 0,
    ) -> list[dict[str, float]]:
        """Quasi-random Sobol sequence over specified dimensions.

        Sobol sequences have much better space coverage than random sampling —
        they're designed to cover the unit hypercube evenly, unlike uniform random
        which tends to cluster. For N samples in D dimensions, Sobol achieves
        O(log(N)^D / N) discrepancy vs O(N^{-1/D}) for random.

        Args:
            keys: Canonical parameter keys to sample
            n:    Number of samples (internally rounded up to next power of 2)
            seed: Random seed for scrambling

        Returns:
            List of dicts, each containing the requested keys snapped to legal values.
            Falls back to uniform random sampling if scipy is unavailable.
        """
        try:
            from scipy.stats.qmc import Sobol
            dims = [self._dim_map[k] for k in keys if k in self._dim_map]
            if not dims:
                return []
            n_pow2 = 2 ** math.ceil(math.log2(max(n, 2)))
            sampler = Sobol(d=len(dims), scramble=True, seed=seed)
            raw = sampler.random(n_pow2)[:n]
            result: list[dict[str, float]] = []
            for row in raw:
                params: dict[str, float] = {}
                for dim, u in zip(dims, row):
                    # Map [0, 1] → [lo, hi] → snap to legal value
                    raw_val = dim.lo + float(u) * (dim.hi - dim.lo)
                    params[dim.name] = dim.snap(raw_val)
                result.append(params)
            return result
        except ImportError:
            # Fallback: uniform random (no scipy)
            rng = random.Random(seed)
            dims = [self._dim_map[k] for k in keys if k in self._dim_map]
            return [
                {dim.name: dim.sample(1, rng)[0] for dim in dims}
                for _ in range(n)
            ]

    def neighborhood(
        self,
        params: dict[str, float],
        steps: int = 1,
        keys: list[str] | None = None,
    ) -> list[dict[str, float]]:
        """All ±steps neighbors in every dimension (or specified keys).

        For 23 Tier A dims at ±1: 46 neighbors per candidate.
        Used in Layer 4 neighborhood polish for guaranteed local optimality.

        Args:
            params: Base parameter set
            steps:  Number of steps to take in each direction
            keys:   Dimensions to vary (default: all Tier A)

        Returns:
            List of neighbor dicts (each differs in exactly one dimension)
        """
        if keys is None:
            dims = self.tier_a()
        else:
            dims = [self._dim_map[k] for k in keys if k in self._dim_map]

        neighbors: list[dict[str, float]] = []
        for dim in dims:
            current = params.get(dim.name, (dim.lo + dim.hi) / 2)
            # Up direction
            vals = dim.legal_values()
            if vals:
                try:
                    idx = min(range(len(vals)), key=lambda i: abs(vals[i] - current))
                    # step up
                    up_idx = min(idx + steps, len(vals) - 1)
                    if up_idx != idx:
                        nb = dict(params)
                        nb[dim.name] = vals[up_idx]
                        neighbors.append(nb)
                    # step down
                    dn_idx = max(idx - steps, 0)
                    if dn_idx != idx:
                        nb = dict(params)
                        nb[dim.name] = vals[dn_idx]
                        neighbors.append(nb)
                except Exception:
                    pass
            else:
                # Continuous: step by resolution
                res = dim.resolution if dim.resolution > 0 else (dim.hi - dim.lo) * 0.05
                for direction in (+1, -1):
                    nb = dict(params)
                    nb[dim.name] = dim.snap(current + direction * steps * res)
                    if abs(nb[dim.name] - current) > 1e-9:
                        neighbors.append(nb)

        return neighbors

    def exhaustive_grid(
        self,
        keys: list[str],
        coarse_keys: dict[str, int] | None = None,
    ) -> list[dict[str, float]]:
        """Full Cartesian product of specified dimensions.

        Args:
            keys:        Dimension keys to enumerate fully
            coarse_keys: {dim_name: n_levels} for dimensions to coarsen to N levels
                         instead of full enumeration (e.g., camber at 3 levels).

        Returns:
            List of dicts — one per combination. Empty if cardinality > 1M.
        """
        from itertools import product as cart_product

        if coarse_keys is None:
            coarse_keys = {}

        dims = [self._dim_map[k] for k in keys if k in self._dim_map]
        if not dims:
            return []

        value_lists: list[list[float]] = []
        for dim in dims:
            if dim.name in coarse_keys:
                n_levels = coarse_keys[dim.name]
                if n_levels <= 1:
                    value_lists.append([(dim.lo + dim.hi) / 2])
                else:
                    step = (dim.hi - dim.lo) / (n_levels - 1)
                    value_lists.append([
                        dim.snap(dim.lo + i * step) for i in range(n_levels)
                    ])
            else:
                value_lists.append(dim.legal_values() or [dim.snap((dim.lo + dim.hi) / 2)])

        # Guard against explosion
        card = 1
        for vl in value_lists:
            card *= len(vl)
        if card > 1_000_000:
            return []  # caller must coarsen

        result: list[dict[str, float]] = []
        for combo in cart_product(*value_lists):
            result.append({dim.name: val for dim, val in zip(dims, combo)})
        return result

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
        "front_arb_size": (0.0, float(max(0, len(car.arb.front_size_labels) - 1))),
        "rear_arb_size": (0.0, float(max(0, len(car.arb.rear_size_labels) - 1))),
        "tc_gain": (1.0, 10.0),
        "tc_slip": (1.0, 10.0),
        "diff_ramp_option_idx": (0.0, float(max(0, len(car.garage_ranges.diff_coast_drive_ramp_options) - 1))),
        "front_camber_deg": gr.camber_front_deg,
        "rear_camber_deg": gr.camber_rear_deg,
        "front_toe_mm": gr.toe_front_mm,
        "rear_toe_mm": gr.toe_rear_mm,
        "brake_bias_pct": (40.0, 60.0),  # broad legal range; car.brake_bias_pct is default
        "diff_preload_nm": gr.diff_preload_nm,
        "diff_clutch_plates": (
            float(min(gr.diff_clutch_plates_options or [2, 4, 6])),
            float(max(gr.diff_clutch_plates_options or [2, 4, 6])),
        ),
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
    resolution_map["front_arb_size"] = 1.0
    resolution_map["rear_arb_size"] = 1.0
    resolution_map["tc_gain"] = 1.0
    resolution_map["tc_slip"] = 1.0
    resolution_map["diff_ramp_option_idx"] = 1.0
    resolution_map["diff_clutch_plates"] = 1.0

    resolution = resolution_map.get(key, spec.resolution if spec and spec.resolution else 1.0)

    # Discrete values for specific dimensions
    discrete_values: list[float] | None = None
    kind = field_def.value_type

    if key == "wing_angle_deg" and car.wing_angles:
        discrete_values = sorted(car.wing_angles)
        kind = "discrete"
    elif key == "front_arb_size":
        discrete_values = list(range(len(car.arb.front_size_labels)))
        kind = "ordinal"
    elif key == "rear_arb_size":
        discrete_values = list(range(len(car.arb.rear_size_labels)))
        kind = "ordinal"
    elif key in ("tc_gain", "tc_slip"):
        discrete_values = list(range(1, 11))
        kind = "ordinal"
    elif key == "diff_ramp_option_idx":
        discrete_values = list(range(len(car.garage_ranges.diff_coast_drive_ramp_options)))
        kind = "ordinal"
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
