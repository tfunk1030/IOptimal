"""Local-first web interface for IOptimal."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = ["create_app"]


def create_app(*args, **kwargs) -> "FastAPI":
    """Lazily import the FastAPI app factory when it is actually needed."""

    from webapp.app import create_app as _create_app

    return _create_app(*args, **kwargs)
