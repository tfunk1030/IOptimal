import unittest

try:
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    np = None

try:
    from solver.bayesian_optimizer import _SimpleGP
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    _SimpleGP = None


@unittest.skipIf(np is None or _SimpleGP is None, "numpy/scipy stack not installed")
class SimpleGPNumericsTests(unittest.TestCase):
    def test_fit_handles_duplicate_points_without_linalg_crash(self) -> None:
        gp = _SimpleGP(length_scale=1.0, noise=1e-12)

        # Duplicated rows can create singular kernels in naive inversion.
        X = np.array([[0.1, 0.2], [0.1, 0.2], [0.5, 0.9]], dtype=float)
        y = np.array([1.0, 1.0, 0.2], dtype=float)

        gp.fit(X, y)
        mean, std = gp.predict(np.array([[0.1, 0.2], [0.4, 0.8]], dtype=float))

        self.assertEqual(mean.shape, (2,))
        self.assertEqual(std.shape, (2,))
        self.assertTrue(np.all(np.isfinite(mean)))
        self.assertTrue(np.all(np.isfinite(std)))


if __name__ == "__main__":
    unittest.main()
