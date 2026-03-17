"""GTP Setup Builder — 6-step constraint satisfaction solver.

The solver package intentionally avoids eager imports so that lightweight
submodules can be used in environments where optional heavy dependencies
for other solver stages are not installed.
"""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "RakeSolver", "RakeSolution",
    "HeaveSolver", "HeaveSolution",
    "CornerSpringSolver", "CornerSpringSolution",
    "ARBSolver", "ARBSolution",
    "WheelGeometrySolver", "WheelGeometrySolution",
    "DamperSolver", "DamperSolution",
]


_LAZY_IMPORTS = {
    "RakeSolver": ("solver.rake_solver", "RakeSolver"),
    "RakeSolution": ("solver.rake_solver", "RakeSolution"),
    "HeaveSolver": ("solver.heave_solver", "HeaveSolver"),
    "HeaveSolution": ("solver.heave_solver", "HeaveSolution"),
    "CornerSpringSolver": ("solver.corner_spring_solver", "CornerSpringSolver"),
    "CornerSpringSolution": ("solver.corner_spring_solver", "CornerSpringSolution"),
    "ARBSolver": ("solver.arb_solver", "ARBSolver"),
    "ARBSolution": ("solver.arb_solver", "ARBSolution"),
    "WheelGeometrySolver": ("solver.wheel_geometry_solver", "WheelGeometrySolver"),
    "WheelGeometrySolution": ("solver.wheel_geometry_solver", "WheelGeometrySolution"),
    "DamperSolver": ("solver.damper_solver", "DamperSolver"),
    "DamperSolution": ("solver.damper_solver", "DamperSolution"),
}


def __getattr__(name: str):
    if name not in _LAZY_IMPORTS:
        raise AttributeError(f"module 'solver' has no attribute {name!r}")
    module_name, attr_name = _LAZY_IMPORTS[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
