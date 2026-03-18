"""Multi-Speed Compromise Solver.

Instead of optimizing the setup for a single operating point (track median speed),
this solver evaluates performance at three speed regimes and finds the Pareto-optimal
compromise weighted by time spent in each regime.

Speed Regimes:
    LOW  (<120 kph): Mechanical grip dominates. Soft springs, low ARB help.
    MID  (120-200 kph): Transition zone. Balance between aero and mechanical.
    HIGH (>200 kph): Aero dominates. Stiff platform, max downforce needed.

The key insight: a setup that's 0.1s faster in slow corners but 0.3s slower on
straights is a NET LOSS. Time-weighted scoring prevents over-optimizing for one
regime at the expense of others.

Usage:
    Integrated into the solver pipeline. Called after the standard 6-step solve
    to provide a "compromise analysis" showing where the setup is strong/weak.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from car_model.cars import CarModel
from track_model.profile import TrackProfile


@dataclass
class SpeedRegimeScore:
    """Performance score for a single speed regime."""
    regime: str                  # "low", "mid", "high"
    speed_range_kph: tuple[float, float]
    time_fraction: float         # Fraction of lap spent in this regime
    aero_efficiency: float       # L/D at regime speed (normalized)
    platform_stability: float    # RH variance control (normalized)
    mechanical_grip: float       # Corner spring compliance (normalized)
    combined_score: float        # Weighted combination for this regime


@dataclass
class MultiSpeedResult:
    """Result of multi-speed compromise analysis."""
    regimes: list[SpeedRegimeScore]
    overall_score: float
    weakest_regime: str
    strongest_regime: str
    compromise_notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "=" * 63,
            "  MULTI-SPEED COMPROMISE ANALYSIS",
            "=" * 63,
            "",
            "  Regime     Time%  Aero  Platform  Grip  Combined",
            "  ─────────  ─────  ────  ────────  ────  ────────",
        ]
        for r in self.regimes:
            lines.append(
                f"  {r.regime:9s}  {r.time_fraction*100:4.0f}%  "
                f"{r.aero_efficiency:.2f}  {r.platform_stability:.2f}      "
                f"{r.mechanical_grip:.2f}  {r.combined_score:.2f}"
            )
        lines += [
            "",
            f"  Overall compromise score: {self.overall_score:.3f}",
            f"  Strongest regime: {self.strongest_regime}",
            f"  Weakest regime:   {self.weakest_regime}",
        ]
        if self.compromise_notes:
            lines.append("")
            for n in self.compromise_notes:
                lines.append(f"  > {n}")
        lines.append("=" * 63)
        return "\n".join(lines)


class MultiSpeedSolver:
    """Analyzes setup performance across speed regimes."""

    def __init__(self, car: CarModel, track: TrackProfile):
        self.car = car
        self.track = track

    def analyze(
        self,
        front_heave_nmm: float,
        rear_third_nmm: float,
        front_wheel_rate_nmm: float,
        rear_wheel_rate_nmm: float,
        dynamic_front_rh_mm: float,
    ) -> MultiSpeedResult:
        """Analyze setup across speed regimes.

        Args:
            front_heave_nmm: Front heave spring rate from Step 2
            rear_third_nmm: Rear third spring rate from Step 2
            front_wheel_rate_nmm: Front corner wheel rate from Step 3
            rear_wheel_rate_nmm: Rear corner wheel rate from Step 3
            dynamic_front_rh_mm: Front dynamic ride height from Step 1
        """
        regimes = [
            ("low", (0, 120)),
            ("mid", (120, 200)),
            ("high", (200, 350)),
        ]

        # Estimate time fractions from track profile speed bands
        pct_below_120 = self.track.pct_time_below_kph(120) or 0.15
        pct_above_200 = self.track.pct_time_above_kph(200) or 0.25
        pct_mid = 1.0 - pct_below_120 - pct_above_200

        time_fractions = {
            "low": max(0.05, pct_below_120),
            "mid": max(0.10, pct_mid),
            "high": max(0.05, pct_above_200),
        }
        # Normalize
        total = sum(time_fractions.values())
        time_fractions = {k: v / total for k, v in time_fractions.items()}

        comp = self.car.aero_compression
        m_eff = self.car.heave_spring.front_m_eff_kg
        v_p99 = max(self.track.shock_vel_p99_front_mps, 0.01)

        results = []
        for name, (speed_lo, speed_hi) in regimes:
            speed_mid = (speed_lo + speed_hi) / 2

            # Aero efficiency: compression at this speed
            front_comp = comp.front_at_speed(speed_mid)
            dynamic_rh = dynamic_front_rh_mm + (comp.front_at_speed(
                self.track.median_speed_kph) - front_comp)
            # L/D proxy: higher dynamic RH at low speed = less ground effect loss
            aero_eff = min(1.0, max(0.0, dynamic_rh / 25.0))
            if name == "high":
                # At high speed, stiffer platform matters more
                aero_eff = min(1.0, front_heave_nmm / 80.0)

            # Platform stability: excursion at this speed
            # Scale shock velocity by speed (higher speed = more aero excitation)
            v_scaled = v_p99 * (speed_mid / self.track.median_speed_kph) ** 0.5
            if front_heave_nmm > 0:
                excursion = v_scaled * math.sqrt(m_eff / (front_heave_nmm * 1000)) * 1000
                platform = max(0.0, min(1.0, 1.0 - excursion / dynamic_rh)) if dynamic_rh > 0 else 0.0
            else:
                platform = 0.0

            # Mechanical grip: softer springs = more grip, matters most at low speed
            total_front = front_heave_nmm + 2 * front_wheel_rate_nmm
            # At low speed, want low combined rate. At high speed, don't care as much.
            if name == "low":
                grip = max(0.0, min(1.0, (250 - total_front) / 200))
            elif name == "mid":
                grip = max(0.0, min(1.0, (350 - total_front) / 300))
            else:
                grip = 0.7  # At high speed, aero grip >> mechanical grip

            # Regime-specific weighting
            if name == "low":
                combined = grip * 0.50 + platform * 0.20 + aero_eff * 0.30
            elif name == "mid":
                combined = grip * 0.30 + platform * 0.35 + aero_eff * 0.35
            else:
                combined = grip * 0.10 + platform * 0.45 + aero_eff * 0.45

            results.append(SpeedRegimeScore(
                regime=name,
                speed_range_kph=(speed_lo, speed_hi),
                time_fraction=time_fractions[name],
                aero_efficiency=round(aero_eff, 3),
                platform_stability=round(platform, 3),
                mechanical_grip=round(grip, 3),
                combined_score=round(combined, 3),
            ))

        # Overall = time-weighted sum
        overall = sum(r.combined_score * r.time_fraction for r in results)

        # Find strongest/weakest
        sorted_regimes = sorted(results, key=lambda r: r.combined_score)
        weakest = sorted_regimes[0].regime
        strongest = sorted_regimes[-1].regime

        notes = []
        if sorted_regimes[0].combined_score < 0.3:
            notes.append(f"Warning: {weakest} speed regime score very low ({sorted_regimes[0].combined_score:.2f})")
        if weakest == "low" and front_heave_nmm > 80:
            notes.append("Heave spring may be too stiff for low-speed grip — consider softer if bottoming allows")
        if weakest == "high" and front_heave_nmm < 40:
            notes.append("Heave spring may be too soft for high-speed platform — consider stiffer")

        return MultiSpeedResult(
            regimes=results,
            overall_score=round(overall, 3),
            weakest_regime=weakest,
            strongest_regime=strongest,
            compromise_notes=notes,
        )
