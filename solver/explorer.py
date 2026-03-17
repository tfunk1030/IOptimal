"""Unconstrained Parameter Space Explorer.

Explores the FULL legal iRacing garage parameter space without "best practice"
soft constraints. The solver's normal mode applies engineering heuristics
(heave-to-corner ratios, LLTD targets, damping ratio ranges) that may leave
performance on the table. This module ignores those heuristics and lets
mathematics find potentially unconventional but fast setups.

Usage:
    python -m solver.solve --car bmw --track sebring --wing 17 --explore

Philosophy:
    iRacing is a simulation with specific numerical models. Real-world
    engineering rules (OptimumG, Milliken, etc.) are approximations.
    The sim may reward setups that violate textbook guidelines:
    - Extreme rake for maximum ground effect
    - Ultra-stiff heave + ultra-soft corners for aero platform + grip
    - Aggressive camber beyond "standard" ranges
    - Minimum damping if the sim's damper model is forgiving
    - Unconventional ARB split if tyre load sensitivity differs from reality

    The explorer treats ALL parameters as free variables within iRacing's
    legal ranges, scores candidates on a physics-based lap time proxy,
    and returns the top configurations — even "crazy" ones.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from aero_model.interpolator import AeroSurface
from car_model.cars import CarModel
from track_model.profile import TrackProfile


@dataclass
class ExplorerCandidate:
    """A single candidate setup from the explorer."""
    # Setup parameters
    front_heave_nmm: float
    rear_third_nmm: float
    front_torsion_od_mm: float
    rear_spring_nmm: float
    front_arb_blade: int
    rear_arb_blade: int
    front_camber_deg: float
    rear_camber_deg: float
    front_toe_mm: float
    rear_toe_mm: float

    # Predicted performance
    aero_score: float = 0.0      # Downforce efficiency (L/D weighted by DF)
    grip_score: float = 0.0      # Mechanical grip proxy
    balance_score: float = 0.0   # LLTD proximity to optimal
    platform_score: float = 0.0  # Ride height stability
    total_score: float = 0.0     # Weighted combination

    # Flags
    is_conventional: bool = True  # True if within standard solver ranges
    unconventional_params: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class ExplorerResult:
    """Result of the parameter space exploration."""
    candidates: list[ExplorerCandidate]
    total_evaluated: int
    physics_baseline_score: float  # Score of the standard solver output
    best_score: float
    improvement_pct: float        # % better than physics baseline
    search_bounds: dict[str, tuple[float, float]] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            "=" * 63,
            "  UNCONSTRAINED PARAMETER SPACE EXPLORATION",
            "=" * 63,
            f"  Candidates evaluated: {self.total_evaluated}",
            f"  Physics baseline score: {self.physics_baseline_score:.4f}",
            f"  Best explorer score:    {self.best_score:.4f}",
            f"  Improvement:            {self.improvement_pct:+.2f}%",
            "",
        ]
        for i, c in enumerate(self.candidates[:5], 1):
            lines += [
                f"  --- Candidate {i} (score: {c.total_score:.4f}) ---",
                f"    Heave: {c.front_heave_nmm:.0f}/{c.rear_third_nmm:.0f} N/mm",
                f"    Torsion OD: {c.front_torsion_od_mm:.2f} mm, "
                f"Rear spring: {c.rear_spring_nmm:.0f} N/mm",
                f"    ARB blades: F{c.front_arb_blade}/R{c.rear_arb_blade}",
                f"    Camber: F{c.front_camber_deg:.1f}/R{c.rear_camber_deg:.1f} deg",
                f"    Toe: F{c.front_toe_mm:.1f}/R{c.rear_toe_mm:.1f} mm",
            ]
            if c.unconventional_params:
                lines.append(f"    ** Unconventional: {', '.join(c.unconventional_params)}")
            if c.notes:
                for n in c.notes:
                    lines.append(f"    > {n}")
            lines.append("")
        lines.append("=" * 63)
        return "\n".join(lines)


class SetupExplorer:
    """Explores the full legal parameter space for unconventional fast setups."""

    def __init__(
        self,
        car: CarModel,
        surface: AeroSurface,
        track: TrackProfile,
    ):
        self.car = car
        self.surface = surface
        self.track = track
        self.gr = car.garage_ranges

    def _score_aero(
        self,
        front_heave_nmm: float,
        rear_third_nmm: float,
    ) -> float:
        """Score aero platform stability.

        Stiffer heave/third = more stable aero platform = more consistent downforce.
        But diminishing returns beyond what's needed for bottoming prevention.
        """
        # Excursion at this heave rate
        v_p99 = (self.track.shock_vel_p99_front_clean_mps
                 if self.track.shock_vel_p99_front_clean_mps > 0
                 else self.track.shock_vel_p99_front_mps)
        if v_p99 <= 0 or front_heave_nmm <= 0:
            return 0.0

        m_eff = self.car.heave_spring.front_m_eff_kg
        # Simple excursion model (no damper for speed)
        excursion = v_p99 * math.sqrt(m_eff / (front_heave_nmm * 1000))
        dynamic_rh = 15.0  # typical front dynamic RH

        # Score: 1.0 if well within margin, 0.0 if bottoming
        margin = dynamic_rh - excursion * 1000
        if margin < 0:
            return max(0.0, 0.5 + margin / 20.0)  # Penalize bottoming
        return min(1.0, 0.5 + margin / 20.0)

    def _score_grip(
        self,
        front_torsion_od_mm: float,
        rear_spring_nmm: float,
        front_camber_deg: float,
        rear_camber_deg: float,
    ) -> float:
        """Score mechanical grip.

        Softer corner springs = more mechanical grip (better bump compliance).
        More camber = better contact patch at lean (up to a point).
        """
        # Corner spring softness reward (softer = better grip, normalized)
        c_torsion = self.car.corner_spring.front_torsion_c
        front_wheel_rate = c_torsion * (front_torsion_od_mm ** 4)
        # Normalize: 20 N/mm = 1.0 (very soft), 80 N/mm = 0.0 (very stiff)
        front_grip = max(0.0, min(1.0, (80 - front_wheel_rate) / 60))

        # Rear: 100 N/mm = 1.0, 300 N/mm = 0.0
        rear_grip = max(0.0, min(1.0, (300 - rear_spring_nmm) / 200))

        # Camber benefit: peak around -3.5 for front, -2.5 for rear
        front_camber_score = 1.0 - abs(front_camber_deg - (-3.5)) / 3.0
        rear_camber_score = 1.0 - abs(rear_camber_deg - (-2.5)) / 3.0
        front_camber_score = max(0.0, min(1.0, front_camber_score))
        rear_camber_score = max(0.0, min(1.0, rear_camber_score))

        return (front_grip * 0.3 + rear_grip * 0.3 +
                front_camber_score * 0.2 + rear_camber_score * 0.2)

    def _score_balance(
        self,
        front_wheel_rate_nmm: float,
        rear_wheel_rate_nmm: float,
        front_arb_blade: int,
        rear_arb_blade: int,
    ) -> float:
        """Score mechanical balance via LLTD proximity to optimal."""
        arb = self.car.arb

        # Roll stiffness from springs
        t_f = arb.track_width_front_mm / 2000.0
        t_r = arb.track_width_rear_mm / 2000.0
        k_springs_front = 2 * front_wheel_rate_nmm * 1000 * t_f ** 2 * (180 / math.pi)
        k_springs_rear = 2 * rear_wheel_rate_nmm * 1000 * t_r ** 2 * (180 / math.pi)

        # ARB stiffness
        front_size_idx = arb.front_baseline_size_idx
        rear_size_idx = 1  # Medium default
        k_arb_front = arb.front_stiffness_nmm_deg[front_size_idx] * front_arb_blade / 3.0
        k_arb_rear = arb.rear_stiffness_nmm_deg[rear_size_idx] * rear_arb_blade / 3.0

        k_front = k_springs_front + k_arb_front
        k_rear = k_springs_rear + k_arb_rear

        if k_front + k_rear <= 0:
            return 0.0

        lltd = k_front / (k_front + k_rear)

        # Optimal LLTD from tyre sensitivity
        tyre_sens = getattr(self.car, "tyre_load_sensitivity", 0.20)
        optimal_lltd = self.car.weight_dist_front + tyre_sens * (0.5 - self.car.weight_dist_front)

        # Score: 1.0 at optimal, 0.0 at ±0.10 from optimal
        error = abs(lltd - optimal_lltd)
        return max(0.0, 1.0 - error / 0.10)

    def _is_conventional(self, candidate: ExplorerCandidate) -> tuple[bool, list[str]]:
        """Check if a candidate is within conventional solver ranges."""
        unconventional = []

        # Heave-to-corner ratio check
        c_torsion = self.car.corner_spring.front_torsion_c
        front_wr = c_torsion * (candidate.front_torsion_od_mm ** 4)
        if candidate.front_heave_nmm > 0 and front_wr > 0:
            ratio = candidate.front_heave_nmm / front_wr
            if ratio < 1.5 or ratio > 3.5:
                unconventional.append(f"heave/corner ratio {ratio:.1f}x (norm: 1.5-3.5)")

        # Extreme camber
        if candidate.front_camber_deg < -4.0:
            unconventional.append(f"extreme front camber {candidate.front_camber_deg:.1f}")
        if candidate.rear_camber_deg < -3.5:
            unconventional.append(f"extreme rear camber {candidate.rear_camber_deg:.1f}")

        # Non-standard ARB strategy (front not at blade 1)
        if candidate.front_arb_blade > 2:
            unconventional.append(f"stiff front ARB blade {candidate.front_arb_blade}")

        # Very soft or very stiff heave
        if candidate.front_heave_nmm < 30:
            unconventional.append(f"very soft heave {candidate.front_heave_nmm:.0f}")
        if candidate.front_heave_nmm > 150:
            unconventional.append(f"very stiff heave {candidate.front_heave_nmm:.0f}")

        return len(unconventional) == 0, unconventional

    def explore(
        self,
        target_balance: float = 50.0,
        fuel_load_l: float = 89.0,
        n_samples: int = 5000,
        baseline_score: float | None = None,
    ) -> ExplorerResult:
        """Explore the parameter space using Latin Hypercube Sampling.

        Returns the top 10 candidates ranked by predicted performance.
        """
        gr = self.gr
        rng = np.random.default_rng(42)

        # Define parameter ranges
        torsion_options = (
            list(gr.front_torsion_od_discrete)
            if gr.front_torsion_od_discrete
            else list(getattr(self.car.corner_spring, "front_torsion_od_options", [13.9]))
        )

        bounds = {
            "front_heave_nmm": gr.front_heave_nmm,
            "rear_third_nmm": gr.rear_third_nmm,
            "rear_spring_nmm": gr.rear_spring_nmm,
            "front_camber_deg": gr.camber_front_deg,
            "rear_camber_deg": gr.camber_rear_deg,
            "front_toe_mm": gr.toe_front_mm,
            "rear_toe_mm": gr.toe_rear_mm,
        }

        # Generate random candidates using Latin Hypercube sampling
        n_continuous = len(bounds)
        # LHS: divide each dimension into n_samples equal strata
        lhs = np.zeros((n_samples, n_continuous))
        for i in range(n_continuous):
            perm = rng.permutation(n_samples)
            lhs[:, i] = (perm + rng.random(n_samples)) / n_samples

        # Map to parameter ranges
        param_names = list(bounds.keys())
        samples = np.zeros_like(lhs)
        for i, name in enumerate(param_names):
            lo, hi = bounds[name]
            samples[:, i] = lo + lhs[:, i] * (hi - lo)

        # Snap to garage resolution
        step_map = {
            "front_heave_nmm": gr.heave_spring_resolution_nmm,
            "rear_third_nmm": gr.heave_spring_resolution_nmm,
            "rear_spring_nmm": gr.rear_spring_resolution_nmm,
            "front_camber_deg": 0.1,
            "rear_camber_deg": 0.1,
            "front_toe_mm": 0.5,
            "rear_toe_mm": 0.5,
        }
        for i, name in enumerate(param_names):
            step = step_map.get(name, 1.0)
            lo, hi = bounds[name]
            samples[:, i] = np.clip(
                np.round(samples[:, i] / step) * step, lo, hi
            )

        # Discrete parameters: torsion OD and ARB blades
        torsion_indices = rng.integers(0, len(torsion_options), size=n_samples)
        arb_lo, arb_hi = gr.arb_blade
        front_arb = rng.integers(arb_lo, arb_hi + 1, size=n_samples)
        rear_arb = rng.integers(arb_lo, arb_hi + 1, size=n_samples)

        # Score all candidates
        candidates: list[ExplorerCandidate] = []
        c_torsion = self.car.corner_spring.front_torsion_c
        mr_rear = self.car.corner_spring.rear_motion_ratio

        for idx in range(n_samples):
            row = {name: samples[idx, i] for i, name in enumerate(param_names)}
            torsion_od = torsion_options[torsion_indices[idx]]
            f_arb = int(front_arb[idx])
            r_arb = int(rear_arb[idx])

            front_wr = c_torsion * (torsion_od ** 4)
            rear_wr = row["rear_spring_nmm"] * mr_rear ** 2

            aero = self._score_aero(row["front_heave_nmm"], row["rear_third_nmm"])
            grip = self._score_grip(
                torsion_od, row["rear_spring_nmm"],
                row["front_camber_deg"], row["rear_camber_deg"],
            )
            balance = self._score_balance(front_wr, rear_wr, f_arb, r_arb)
            platform = aero  # Platform score is primarily aero stability

            # Weighted total — grip matters most, then balance, then aero platform
            # Track-dependent weighting: high-speed tracks weight aero more
            hs_pct = getattr(self.track, "pct_above_200kph", 0.3)
            aero_weight = 0.20 + 0.15 * hs_pct
            grip_weight = 0.40 - 0.10 * hs_pct
            balance_weight = 0.25
            platform_weight = 0.15

            total = (aero * aero_weight + grip * grip_weight +
                     balance * balance_weight + platform * platform_weight)

            c = ExplorerCandidate(
                front_heave_nmm=row["front_heave_nmm"],
                rear_third_nmm=row["rear_third_nmm"],
                front_torsion_od_mm=torsion_od,
                rear_spring_nmm=row["rear_spring_nmm"],
                front_arb_blade=f_arb,
                rear_arb_blade=r_arb,
                front_camber_deg=row["front_camber_deg"],
                rear_camber_deg=row["rear_camber_deg"],
                front_toe_mm=row["front_toe_mm"],
                rear_toe_mm=row["rear_toe_mm"],
                aero_score=aero,
                grip_score=grip,
                balance_score=balance,
                platform_score=platform,
                total_score=total,
            )
            is_conv, unconv = self._is_conventional(c)
            c.is_conventional = is_conv
            c.unconventional_params = unconv

            candidates.append(c)

        # Sort by total score, take top 10
        candidates.sort(key=lambda x: x.total_score, reverse=True)
        top = candidates[:10]

        # Annotate top candidates
        for c in top:
            if c.front_heave_nmm > 100 and c.front_torsion_od_mm <= 14.0:
                c.notes.append("Ultra-stiff heave + soft corners: aero platform with grip")
            if c.front_camber_deg < -4.0:
                c.notes.append("Extreme camber may exploit iRacing contact patch model")
            if c.front_arb_blade >= 3 and c.rear_arb_blade <= 2:
                c.notes.append("Reversed ARB strategy: stiff front, soft rear")
            if c.rear_third_nmm > 700:
                c.notes.append("Very stiff rear third — locks rear platform for traction")

        _baseline = baseline_score if baseline_score is not None else 0.5
        best = top[0].total_score if top else 0.0
        improvement = ((best - _baseline) / max(_baseline, 1e-6)) * 100 if _baseline > 0 else 0.0

        return ExplorerResult(
            candidates=top,
            total_evaluated=n_samples,
            physics_baseline_score=_baseline,
            best_score=best,
            improvement_pct=improvement,
            search_bounds=bounds,
        )
