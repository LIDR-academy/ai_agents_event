"""Logfire observability for the graph (Level 2).

``configure_logfire`` wires Pydantic Logfire once at startup:

* per-node ``logfire.span("node: …")`` calls in ``nodes.py`` become one span per
  node inside the request trace;
* ``instrument_fastapi`` gives the HTTP request its root span;
* ``instrument_httpx`` captures the outbound OpenAI / embedding calls the nodes make
  (the OpenAI SDK uses httpx under the hood), so LLM latency shows up in the trace.

It is a **no-op without a token**: ``send_to_logfire="if-token-present"`` means a
run with no ``LOGFIRE_TOKEN`` still executes every span locally but exports nothing,
so the service never depends on Logfire being configured. Set ``LOGFIRE_TOKEN`` (and
optionally ``LOGFIRE_SEND_TO_LOGFIRE=1``) to get the trace link for the deliverable.

If you prefer LangSmith, this is the seam to swap: leave the ``logfire.span`` calls
(they are cheap no-ops) and configure the LangGraph tracer here instead.
"""

from __future__ import annotations

import structlog

from app.config import get_settings

log = structlog.get_logger()

_configured = False


def configure_logfire(app=None) -> bool:
    """Configure Logfire once. Returns ``True`` if configuration succeeded.

    Guarded so a missing package / token / network never breaks app startup — a
    failure is logged and swallowed. ``app`` is the FastAPI instance to instrument;
    pass ``None`` to skip FastAPI instrumentation (e.g. from a CLI run).
    """
    global _configured
    if _configured:
        return True
    try:
        import logfire

        settings = get_settings()
        logfire.configure(
            service_name=settings.LOGFIRE_SERVICE_NAME,
            # Export only when a token is configured; otherwise spans run locally
            # (still visible to any console exporter) but nothing is sent.
            send_to_logfire="if-token-present",
            console=False,
        )
        if app is not None:
            logfire.instrument_fastapi(app)
        # Captures the OpenAI Responses/Chat + embedding HTTP calls the nodes make.
        logfire.instrument_httpx()
        _configured = True
        log.info("logfire_configured", service_name=settings.LOGFIRE_SERVICE_NAME)
        return True
    except Exception as exc:  # noqa: BLE001 — observability must never break startup.
        log.warning("logfire_configure_failed", error=str(exc)[:200])
        return False
