"""``POST /v1/estimate/agent/{structure,hours}`` — the agent driving the wizard.

Session 12 wires the hand-written agent INTO the existing estimation wizard,
around the human review gate — it no longer runs one autonomous shot beside the
pipeline. Two endpoints, mirroring the two wizard steps the agent conducts:

* ``/structure`` (phase 1) — the agent decomposes the reformulated brief into the
  module→task tree. Returns the SAME ``GenerateResult`` as ``/stages/structure``
  (plus the agent's trace) so the wizard parses it unchanged; the human then
  reviews/edits the tree.
* ``/hours`` (phase 2) — HYBRID: the deterministic per-task consensus runs first,
  and the agent only re-searches the tasks it could not ground. Returns the SAME
  ``TaskHoursResult`` as ``/tasks/hours`` (plus the recovery trace).

The deterministic ``/stages/structure`` and ``/tasks/hours`` stay intact as the
live comparison and the hybrid's base. Composition of the ``agentic`` + ``rag``
siblings lives in the conductor (``app.domain.agent_estimation``), not here.

Thin transport, same posture as the sibling routers: validation in the request
schema (422), auth in ``require_estimate_key`` (401, same ``ESTIMATE_API_KEY``),
rate limiting in the decorator (429), loop/LLM failures → 502. The agent drives
the raw async Responses API, so it needs the async OpenAI client
(``get_async_openai_client``); a missing key is a 500 (misconfiguration).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_request_id
from app.api.rate_limiting import limiter
from app.api.security import require_estimate_key
from app.config import get_settings
from app.dependencies import (
    get_async_openai_client,
    get_embedder,
    get_runtime_retrieval_config,
)
from app.domain.agent_estimation import agent_estimate_task_hours, agent_propose_structure
from app.generation.rag.observability import log_stage
from app.generation.rag.schemas import (
    AgentHoursRequest,
    AgentStructureRequest,
    GenerateResult,
    TaskHoursResult,
)

log = structlog.get_logger()

router = APIRouter(prefix="/v1/estimate/agent", tags=["estimate-agent"])


def _require_async_client():
    client = get_async_openai_client()
    if client is None:
        log.error("agent_failed", reason="async_openai_client_unavailable")
        raise HTTPException(status_code=500, detail="OpenAI client is not available.")
    return client


@router.post(
    "/structure",
    response_model=GenerateResult,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("15/minute")
async def structure(request: Request, payload: AgentStructureRequest) -> GenerateResult:
    """Phase 1 — the agent proposes the module→task structure for the brief."""
    request_id = get_request_id(request)
    settings = get_settings()
    client = _require_async_client()
    try:
        with log_stage("agent_structure", request_id):
            return await agent_propose_structure(
                payload.query,
                client=client,
                model=payload.model or settings.AGENT_MODEL,
                reasoning_effort=payload.reasoning_effort or settings.AGENT_REASONING_EFFORT,
                persona=payload.persona,
            )
    except Exception as exc:  # noqa: BLE001 — any loop/LLM failure → 502.
        log.error("agent_failed", stage="structure", error_type=type(exc).__name__, error=str(exc)[:300])
        raise HTTPException(status_code=502, detail="Failed to propose the structure.") from exc


@router.post(
    "/hours",
    response_model=TaskHoursResult,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("15/minute")
async def hours(request: Request, payload: AgentHoursRequest) -> TaskHoursResult:
    """Phase 2 — deterministic hours, then agent recovery on the flagged tasks."""
    request_id = get_request_id(request)
    settings = get_settings()
    client = _require_async_client()
    if get_embedder() is None:
        log.error("agent_failed", stage="hours", reason="embedder_unavailable")
        raise HTTPException(status_code=500, detail="Embedding service is not available.")

    runtime = get_runtime_retrieval_config()
    top_k = payload.search_top_k if payload.search_top_k is not None else runtime.effective_task_hours_top_k()
    distance_threshold = (
        payload.search_distance_threshold
        if payload.search_distance_threshold is not None
        else runtime.effective_task_hours_distance_threshold()
    )
    task_count = sum(len(m.tasks) for m in payload.modules)
    try:
        with log_stage("agent_hours", request_id, tasks=task_count):
            return await agent_estimate_task_hours(
                payload.modules,
                client=client,
                model=payload.model or settings.AGENT_MODEL,
                reasoning_effort=payload.reasoning_effort or settings.AGENT_REASONING_EFFORT,
                max_iterations=payload.max_iterations or settings.AGENT_MAX_ITERATIONS,
                top_k=top_k,
                distance_threshold=distance_threshold,
                persona=payload.persona,
            )
    except Exception as exc:  # noqa: BLE001 — any loop/LLM failure → 502.
        log.error("agent_failed", stage="hours", error_type=type(exc).__name__, error=str(exc)[:300])
        raise HTTPException(status_code=502, detail="Failed to estimate task hours.") from exc
