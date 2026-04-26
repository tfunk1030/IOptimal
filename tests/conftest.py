"""Shared pytest fixtures for the test suite.

Provides repository-relative path helpers, per-car ``CarModel`` fixtures, and
an IBT-file lookup that gracefully skips tests when telemetry isn't on disk
(LFS pointer state in fresh checkouts).
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ── Path fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repository root (parent of ``tests/``)."""
    return Path(__file__).resolve().parents[1]


# ── Car-model fixtures ─────────────────────────────────────────────────────

def _car_factory(name: str):
    """Build a per-car fixture function loading via ``car_model.cars.get_car``."""

    @pytest.fixture(scope="session")
    def _fixture():
        from car_model.cars import get_car
        return get_car(name)

    _fixture.__name__ = f"{name}_car"
    _fixture.__doc__ = f"Calibrated {name} CarModel (session-scoped, deep-copied at module level)."
    return _fixture


bmw_car = _car_factory("bmw")
porsche_car = _car_factory("porsche")
ferrari_car = _car_factory("ferrari")
acura_car = _car_factory("acura")
cadillac_car = _car_factory("cadillac")


# ── IBT telemetry fixtures ─────────────────────────────────────────────────

# IBT files are large and stored in LFS. In a fresh checkout they appear as
# 133-byte pointer files; tests that need real bytes must skip rather than
# crash. Real files are >1 MB, pointer files are <1 KB — a 10 KB threshold
# is a safe discriminator.
_LFS_POINTER_BYTES = 10_000


def _is_real_ibt(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > _LFS_POINTER_BYTES


# Per-car IBT search patterns (relative to repo root). The first match that
# is a real file (not an LFS pointer) wins.
_IBT_PATTERNS: dict[str, tuple[str, ...]] = {
    "bmw": (
        "data/telemetry/bmwlmdh_sebring international 2026-03-11 10-17-38.ibt",
        "data/telemetry/bmwlmdh*.ibt",
        "data/telemetry/bmw_*.ibt",
    ),
    "porsche": (
        "porsche963gtp_algarve gp 2026-04-04 13-34-07.ibt",
        "porsche963gtp_*.ibt",
        "ibtfiles/porsche963gtp_*.ibt",
    ),
    "ferrari": (
        "ferrari499p_hockenheim gp 2026-03-31 13-14-50.ibt",
        "ferrari499p_*.ibt",
        "ibtfiles/ferrari499p_*.ibt",
    ),
    "acura": (
        "acura*.ibt",
        "ibtfiles/acura*.ibt",
    ),
    "cadillac": (
        "cadillac*.ibt",
        "ibtfiles/cadillac*.ibt",
    ),
}


def _find_ibt(repo: Path, car_name: str) -> Path | None:
    patterns = _IBT_PATTERNS.get(car_name.lower())
    if not patterns:
        return None
    for pat in patterns:
        # Direct path first, then glob if it contains a wildcard
        if any(c in pat for c in "*?["):
            for candidate in sorted(repo.glob(pat)):
                if _is_real_ibt(candidate):
                    return candidate
        else:
            candidate = repo / pat
            if _is_real_ibt(candidate):
                return candidate
    return None


@pytest.fixture(scope="session")
def fixture_ibt(repo_root: Path):
    """Return a callable ``(car_name) -> Path`` that locates a real IBT or skips.

    Usage::

        def test_something(fixture_ibt):
            ibt = fixture_ibt("porsche")  # skips if no real Porsche IBT on disk
            ...
    """

    def _resolve(car_name: str) -> Path:
        path = _find_ibt(repo_root, car_name)
        if path is None:
            pytest.skip(
                f"No real IBT telemetry on disk for {car_name!r} "
                f"(LFS pointers don't count). Run `git lfs pull` to fetch.",
            )
        return path

    return _resolve
