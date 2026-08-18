"""Shared FastAPI dependencies for the Session 9 routers."""

from __future__ import annotations

from uuid import uuid4

from starlette.requests import Request


def get_request_id(request: Request) -> str:
    """Return the correlation id bound by the X-Request-ID middleware.

    Falls back to a fresh UUID if the middleware did not run (e.g. a direct
    TestClient call that bypasses it)."""
    return getattr(request.state, "request_id", None) or str(uuid4())
