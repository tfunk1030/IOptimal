"""Bayesian Setup Optimizer — Surrogate-Model Guided Parameter Search.

Uses Gaussian Process regression (via scipy) to build a surrogate model of the
setup-to-performance mapping, then uses Expected Improvement to intelligently
explore the parameter space.

Usage:
    python -m solver.solve --car bmw --track sebring --wing 17 --bayesian

How it works:
    1. Start with the physics solver output as the first "observation"
    2. Generate initial samples (Latin Hypercube) around it
    3. Score each sample using a physics-based lap time proxy
    4. Fit a Gaussian Process to the scored samples
    5. Use Expected Improvement (EI) to pick the next point to evaluate
    6. Repeat for N iterations
    7. Return the best setup found

The surrogate model captures nonlinear interactions between parameters that
the sequential 6-step solver cannot see (e.g., heave-spring × ARB interactions).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial.distance import cdist

from car_model.cars import CarModel
from track_model.profile import TrackProfile


@dataclass
class BayesianCandidate:
    """A candidate evaluated by the Bayesian optimizer."""
    params: dict[str, float]
    predicted_score: float
    uncertainty: float
    acquisition_value: float
    iteration: int


@dataclass
class BayesianResult:
    """Result of Bayesian optimization."""
    best_params: dict[str, float]
    best_score: float
    physics_baseline_score: float
    improvement_pct: float
    iterations: int
    history: list[BayesianCandidate] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "=" * 63,
            "  BAYESIAN SETUP OPTIMIZATION",
            "=" * 63,
            f"  Iterations:        {self.iterations}",
            f"  Physics baseline:  {self.physics_baseline_score:.4f}",
            f"  Best found:        {self.best_score:.4f}",
            f"  Improvement:       {self.improvement_pct:+.2f}%",
            "",
            "  BEST SETUP PARAMETERS:",
        ]
        for k, v in self.best_params.items():
            lines.append(f"    {k}: {v:.2f}")
        lines.append("=" * 63)
        return "\n".join(lines)


class _SimpleGP:
    """Lightweight Gaussian Process regressor with RBF kernel.

    Avoids sklearn dependency. Uses squared-exponential kernel with fixed
    length scale. Suitable for low-dimensional (7-dim) parameter spaces.
    """

    def __init__(self, length_scale: float = 1.0, noise: float = 1e-4):
        self.length_scale = length_scale
        self.noise = noise
        self.X_train: np.ndarray | None = None
        self.y_train: np.ndarray | None = None
        self.K_inv: np.ndarray | None = None
        self.alpha: np.ndarray | None = None

    def _kernel(self, X1: np.ndarray, X2: np.ndarray) -> np.ndarray:
        dists = cdist(X1 / self.length_scale, X2 / self.length_scale, metric="sqeuclidean")
        return np.exp(-0.5 * dists)

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        self.X_train = X.copy()
        self.y_train = y.copy()
        K = self._kernel(X, X)
        jitter = max(self.noise, 1e-10)
        ident = np.eye(len(X))
        # Numerical guard: duplicated/snap-rounded samples can make K singular.
        # Escalate jitter progressively, then fall back to pseudo-inverse.
        for _ in range(6):
            try:
                self.K_inv = np.linalg.inv(K + jitter * ident)
                break
            except np.linalg.LinAlgError:
                jitter *= 10.0
        else:
            self.K_inv = np.linalg.pinv(K + jitter * ident, rcond=1e-8)
        self.alpha = self.K_inv @ y

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self.X_train is None:
            raise ValueError("GP not fitted yet")
        k_star = self._kernel(X, self.X_train)
        mean = k_star @ self.alpha
        v = self.K_inv @ k_star.T
        var = np.maximum(1.0 - np.sum(k_star.T * v, axis=0), 1e-10)
        return mean, np.sqrt(var)


def _expected_improvement(mean: np.ndarray, std: np.ndarray, best_y: float) -> np.ndarray:
    from scipy.stats import norm
    improvement = mean - best_y
    z = improvement / np.maximum(std, 1e-10)
    ei = improvement * norm.cdf(z) + std * norm.pdf(z)
    ei[std < 1e-10] = 0.0
    return ei


class BayesianOptimizer:
    """Bayesian optimization of GTP setup parameters."""

    # (name, step_size) — bounds read from car.garage_ranges
    PARAM_SPEC = [
        ("front_heave_nmm",  "front_heave_nmm",  10.0),
        ("rear_third_nmm",   "rear_third_nmm",   10.0),
        ("rear_spring_nmm",  "rear_spring_nmm",   5.0),
        ("front_camber_deg", "camber_front_deg",  0.1),
        ("rear_camber_deg",  "camber_rear_deg",   0.1),
        ("front_arb_blade",  "arb_blade",         1.0),
        ("rear_arb_blade",   "arb_blade",         1.0),
    ]

    def __init__(self, car: CarModel, track: TrackProfile):
        self.car = car
        self.track = track
        gr = car.garage_ranges

        self.param_names: list[str] = []
        bounds_lo: list[float] = []
        bounds_hi: list[float] = []
        self.steps: list[float] = []

        for name, gr_attr, step in self.PARAM_SPEC:
            rng_val = getattr(gr, gr_attr, (0.0, 100.0))
            self.param_names.append(name)
            bounds_lo.append(float(rng_val[0]))
            bounds_hi.append(float(rng_val[1]))
            self.steps.append(step)

        self.bounds_lo = np.array(bounds_lo)
        self.bounds_hi = np.array(bounds_hi)
        self.dim = len(self.param_names)

    def _normalize(self, X: np.ndarray) -> np.ndarray:
        span = self.bounds_hi - self.bounds_lo
        span[span == 0] = 1.0
        return (X - self.bounds_lo) / span

    def _denormalize(self, X_norm: np.ndarray) -> np.ndarray:
        return X_norm * (self.bounds_hi - self.bounds_lo) + self.bounds_lo

    def _snap(self, params: np.ndarray) -> np.ndarray:
        result = params.copy()
        for i, step in enumerate(self.steps):
            result[i] = round(result[i] / step) * step
            result[i] = max(self.bounds_lo[i], min(self.bounds_hi[i], result[i]))
        return result

    def _score(self, params: dict[str, float]) -> float:
        """Physics-based lap time proxy. Higher is better."""
        heave = params.get("front_heave_nmm", 50)
        rear_sp = params.get("rear_spring_nmm", 170)
        f_cam = params.get("front_camber_deg", -3.4)
        r_cam = params.get("rear_camber_deg", -2.0)
        f_arb = params.get("front_arb_blade", 1)
        r_arb = params.get("rear_arb_blade", 3)

        # Aero platform: lower excursion = better
        v_p99 = max(self.track.shock_vel_p99_front_mps, 0.01)
        m_eff = self.car.heave_spring.front_m_eff_kg
        if heave > 0:
            excursion = v_p99 * math.sqrt(m_eff / (heave * 1000))
            aero_score = max(0.0, 1.0 - excursion * 1000 / 20.0)
        else:
            aero_score = 0.0

        # Mechanical grip: softer springs = more grip
        grip_score = (max(0.0, (300 - rear_sp) / 200) * 0.5
                      + max(0.0, (200 - heave) / 200) * 0.5)

        # Balance: LLTD near optimal
        tyre_sens = getattr(self.car, "tyre_load_sensitivity", 0.20)
        optimal_lltd = self.car.weight_dist_front + (tyre_sens / 0.20) * 0.05
        lltd_est = self.car.weight_dist_front + 0.03 * f_arb - 0.03 * r_arb + 0.05
        balance_score = max(0.0, 1.0 - abs(lltd_est - optimal_lltd) / 0.10)

        # Camber: peak near -3.5F / -2.5R
        camber_score = (max(0.0, 1.0 - abs(f_cam + 3.5) / 2.0) * 0.5
                        + max(0.0, 1.0 - abs(r_cam + 2.5) / 2.0) * 0.5)

        return aero_score * 0.25 + grip_score * 0.35 + balance_score * 0.25 + camber_score * 0.15

    def optimize(
        self,
        n_initial: int = 50,
        n_iterations: int = 100,
        physics_baseline: dict[str, float] | None = None,
    ) -> BayesianResult:
        """Run Bayesian optimization.

        Args:
            n_initial: Number of initial Latin Hypercube samples
            n_iterations: Number of BO iterations after initial sampling
            physics_baseline: Physics solver output as starting point
        """
        rng = np.random.default_rng(42)

        # Latin Hypercube initial design
        lhs = np.zeros((n_initial, self.dim))
        for i in range(self.dim):
            perm = rng.permutation(n_initial)
            lhs[:, i] = (perm + rng.random(n_initial)) / n_initial
        X = self._denormalize(lhs)
        for i in range(n_initial):
            X[i] = self._snap(X[i])

        # Prepend physics baseline
        if physics_baseline is not None:
            baseline_vec = np.array([
                physics_baseline.get(n, (lo + hi) / 2)
                for n, lo, hi in zip(self.param_names, self.bounds_lo, self.bounds_hi)
            ])
            baseline_vec = self._snap(baseline_vec)
            X = np.vstack([baseline_vec.reshape(1, -1), X])

        y = np.array([
            self._score({n: X[i, j] for j, n in enumerate(self.param_names)})
            for i in range(len(X))
        ])

        history: list[BayesianCandidate] = []
        baseline_score = float(y[0]) if physics_baseline is not None else float(np.mean(y))

        gp = _SimpleGP(length_scale=0.3, noise=1e-3)

        for iteration in range(n_iterations):
            X_norm = self._normalize(X)
            gp.fit(X_norm, y)
            best_y = float(np.max(y))

            X_cand_norm = rng.random((500, self.dim))
            mean, std = gp.predict(X_cand_norm)
            ei = _expected_improvement(mean, std, best_y)

            best_idx = int(np.argmax(ei))
            next_x = self._denormalize(X_cand_norm[best_idx].reshape(1, -1)).flatten()
            next_x = self._snap(next_x)
            next_score = self._score({n: float(next_x[j]) for j, n in enumerate(self.param_names)})

            history.append(BayesianCandidate(
                params={n: float(next_x[j]) for j, n in enumerate(self.param_names)},
                predicted_score=float(mean[best_idx]),
                uncertainty=float(std[best_idx]),
                acquisition_value=float(ei[best_idx]),
                iteration=iteration,
            ))

            X = np.vstack([X, next_x.reshape(1, -1)])
            y = np.append(y, next_score)

        best_idx = int(np.argmax(y))
        best_params = {n: float(X[best_idx, j]) for j, n in enumerate(self.param_names)}
        best_score = float(y[best_idx])
        improvement = ((best_score - baseline_score) / max(abs(baseline_score), 1e-6)) * 100

        return BayesianResult(
            best_params=best_params,
            best_score=best_score,
            physics_baseline_score=baseline_score,
            improvement_pct=improvement,
            iterations=n_iterations,
            history=history,
        )
