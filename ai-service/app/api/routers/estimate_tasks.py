"""``POST /v1/estimate/tasks/hours`` — per-task hours by vector search (S10).

The wizard's structure-only generation yields modules → tasks WITHOUT hours.
This endpoint fills them in: each task is matched against the historical task
corpus and assigned a distance-weighted consensus of the nearest neighbours'
hours, plus a reliability score. Tasks with no neighbour under the threshold come
back with ``has_match=False`` (no hours) so the UI can flag the row red.

Thin transport, same posture as the sibling estimate routers: validation in
``TaskHoursRequest`` (422), auth in ``require_estimate_key`` (401), rate limiting
in the decorator (429), pipeline failures → 502. The per-task search reuses the
Session 9 retriever; the consensus lives in ``task_hours``.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_request_id
from app.api.rate_limiting import limiter
from app.api.security import require_estimate_key
from app.dependencies import get_embedder, get_runtime_retrieval_config
from app.generation.rag.errors import RetrievalError
from app.generation.rag.observability import log_stage
from app.generation.rag.schemas import TaskHoursRequest, TaskHoursResult
from app.generation.rag.task_hours import estimate_all

log = structlog.get_logger()

router = APIRouter(prefix="/v1/estimate/tasks", tags=["estimate-tasks"])


@router.post(
    "/hours",
    response_model=TaskHoursResult,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("30/minute")
async def hours(request: Request, payload: TaskHoursRequest) -> TaskHoursResult:
    """Estimate hours for every submitted task from the historical task corpus."""
    request_id = get_request_id(request)
    if get_embedder() is None:
        log.error("stage_failed", stage="task_hours", reason="embedder_unavailable")
        raise HTTPException(status_code=500, detail="Embedding service is not available.")

    runtime = get_runtime_retrieval_config()
    task_count = sum(len(m.tasks) for m in payload.modules)
    try:
        with log_stage("task_hours", request_id, tasks=task_count):
            return await estimate_all(
                payload.modules,
                top_k=runtime.effective_task_hours_top_k(),
                distance_threshold=runtime.effective_task_hours_distance_threshold(),
            )
    except RetrievalError as exc:
        raise HTTPException(status_code=502, detail="Retrieval failed.") from exc
    except Exception as exc:  # noqa: BLE001 — embedding/other failures → 502.
        log.error("stage_failed", stage="task_hours", error_type=type(exc).__name__)
        raise HTTPException(status_code=502, detail="Failed to estimate task hours.") from exc
