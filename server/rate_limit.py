"""Per-API-key rate limiting using slowapi.

Applies a default ``IOPTIMAL_RATE_LIMIT_POST`` (default ``100/minute``) limit on
POSTs and ``IOPTIMAL_RATE_LIMIT_GET`` (default ``300/minute``) on GETs, keyed on
the Bearer token in the ``Authorization`` header.  Falls back to the client IP
when no token is present (e.g. unauthenticated routes such as ``/api/health``).

If ``slowapi`` isn't installed the module degrades gracefully:
``attach_rate_limiter`` becomes a no-op and the dependency hooks are no-ops too.
This keeps the existing dev workflow working without a hard new dependency.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request

logger = logging.getLogger(__name__)

try:
    from slowapi import Limiter
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware
    from slowapi.util import get_remote_address
    _SLOWAPI_AVAILABLE = True
except ImportError:  # pragma: no cover - optional runtime dependency
    Limiter = None  # type: ignore[misc,assignment]
    RateLimitExceeded = None  # type: ignore[misc,assignment]
    SlowAPIMiddleware = None  # type: ignore[misc,assignment]
    get_remote_address = None  # type: ignore[assignment]
    _SLOWAPI_AVAILABLE = False


def _api_key_or_ip(request: Request) -> str:
    """slowapi key function: prefer Bearer token, fall back to client IP."""
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() == "bearer" and token:
        return f"key:{token}"
    if get_remote_address is not None:
        return f"ip:{get_remote_address(request)}"
    return "ip:unknown"


def _rate_limit_post() -> str:
    return os.environ.get("IOPTIMAL_RATE_LIMIT_POST", "100/minute")


def _rate_limit_get() -> str:
    return os.environ.get("IOPTIMAL_RATE_LIMIT_GET", "300/minute")


# Module-level limiter (created lazily so import-time failures don't break tests).
limiter: "Limiter | None" = None
if _SLOWAPI_AVAILABLE:
    limiter = Limiter(key_func=_api_key_or_ip, default_limits=[])


def attach_rate_limiter(app: FastAPI) -> None:
    """Wire the limiter and exception handler into a FastAPI app."""
    if not _SLOWAPI_AVAILABLE or limiter is None:
        logger.warning("slowapi not installed — rate limiting disabled")
        return

    from slowapi import _rate_limit_exceeded_handler  # local import: optional dep

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)


def post_limit():
    """Decorator that applies the per-API-key POST rate limit to a route."""
    if not _SLOWAPI_AVAILABLE or limiter is None:
        def _passthrough(fn):
            return fn
        return _passthrough
    return limiter.limit(_rate_limit_post())


def get_limit():
    """Decorator that applies the per-API-key GET rate limit to a route."""
    if not _SLOWAPI_AVAILABLE or limiter is None:
        def _passthrough(fn):
            return fn
        return _passthrough
    return limiter.limit(_rate_limit_get())
