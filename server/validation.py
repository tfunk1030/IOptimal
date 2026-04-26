"""Shared input validation helpers for routes."""

from __future__ import annotations

from fastapi import HTTPException

from car_model.registry import resolve_car, supported_car_names


def require_known_car(car: str) -> None:
    """Raise 400 if ``car`` is not in the canonical car registry."""
    if resolve_car(car) is not None:
        return
    supported = ", ".join(supported_car_names())
    raise HTTPException(
        status_code=400,
        detail=f"Unknown car '{car}'. Supported cars: {supported}.",
    )
