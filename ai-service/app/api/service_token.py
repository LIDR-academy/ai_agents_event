"""Service-to-service authentication for the whole AI service (Session 15).

Two layers of auth coexist, and they answer different questions:

``X-Service-Token`` (here)
    *Are you a service that may talk to me at all?* One shared secret between
    the business backend and the AI service, enforced as **middleware** so it
    covers every route — including the ones that predate the Session 9 routers
    and were never protected (``POST /api/v1/estimate``, ``/sessions``,
    ``/embeddings``, ``POST /search`` and, most importantly, the mutating
    ``PUT /api/v1/config/*``).

``X-API-Key`` (``app/api/security.py``, unchanged since Session 9)
    *Which endpoints may this caller use?* Two independent keys splitting the
    retrieval and estimate routers.

Being inside the compose network is NOT a credential. The network boundary stops
the host from reaching port 8000, but any other container on that network could
still call the service — the token is what makes "internal" mean "authorised".

Design notes worth keeping:

* An **unset token disables the check**. That is the opposite of the S9 keys
  (unset ⇒ 401 on everything), and it is deliberate: this guard sits in front of
  the entire application, so defaulting to "on" would break the test suite and
  every local ``uv run uvicorn`` the moment it shipped. Deployments set it.
* ``secrets.compare_digest`` rather than ``==``: a plain comparison short-circuits
  on the first differing byte and leaks the shared secret's prefix through
  response timing.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse

from app.config import get_settings

SERVICE_TOKEN_HEADER = "X-Service-Token"

# Paths that must stay reachable without the token.
#
# ``/health`` is the important one: the Docker HEALTHCHECK and the compose
# ``depends_on: service_healthy`` condition run it, and neither can carry a
# secret. It is safe precisely because it is cheap and reveals nothing — no LLM
# call, no database access, just liveness.
#
# The docs endpoints stay open so the interactive API browser keeps working; the
# operations they describe are still guarded.
EXEMPT_PATHS = frozenset(
    {
        "/health",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
    }
)

logger = structlog.get_logger()


def _is_exempt(path: str) -> bool:
    """Return True when ``path`` must bypass the token check."""
    return path in EXEMPT_PATHS


async def service_token_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Reject any request that does not carry the shared service token."""
    expected = get_settings().AI_SERVICE_TOKEN

    # Not configured → the guard is off. Local development and tests land here.
    if not expected:
        return await call_next(request)

    # CORS preflight never carries custom headers, so checking it would break
    # browsers before they ever get the chance to send the real request.
    if request.method == "OPTIONS" or _is_exempt(request.url.path):
        return await call_next(request)

    provided = request.headers.get(SERVICE_TOKEN_HEADER)

    if not provided or not secrets.compare_digest(provided, expected):
        logger.warning(
            "service_token_rejected",
            path=request.url.path,
            method=request.method,
            # Whether the header was absent or merely wrong is the first thing
            # you want when debugging a misconfigured client. The value itself
            # is never logged.
            reason="missing" if not provided else "mismatch",
            client=request.client.host if request.client else None,
        )
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "detail": {
                    "reason": "invalid_service_token",
                    "message": (
                        f"Missing or invalid {SERVICE_TOKEN_HEADER} header. "
                        "This service only accepts calls from authorised services."
                    ),
                }
            },
            headers={"WWW-Authenticate": SERVICE_TOKEN_HEADER},
        )

    return await call_next(request)
