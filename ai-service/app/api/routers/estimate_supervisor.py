"""``/v1/estimate/supervisor`` — the Session 14 multi-agent flow.

Three verbs over one ``thread_id``, the same posture as the Session 13 graph router:

* ``POST /v1/estimate/supervisor`` — START. Runs until the estimate is done, or until
  the review gate trips. A tripped gate comes back ``status="awaiting_human_review"``.
* ``POST /v1/estimate/supervisor/{estimation_id}/resume`` — RESUME with the human's
  decision (approve / adjust / reject). 409 when nothing is pending.
* ``GET /v1/estimate/supervisor/{estimation_id}/state`` — read the snapshot, so a UI
  can recover a paused run after any delay.

The contract toward the business backend is unchanged — transcript in, estimate plus
``status`` out. The supervisor, the privilege table and the gate are implementation;
the only thing the client must newly understand is that a run may pause.

Auth reuses ``ESTIMATE_API_KEY``. Graph/LLM failures → 502; a graph that failed to
build at startup (``app.state.supervisor_graph is None``) → 503.
"""

from __future__ import annotations

from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from langgraph.types import Command

from app.api.deps import get_request_id
from app.api.rate_limiting import limiter
from app.api.security import require_estimate_key
from app.domain.graph.supervisor.state import privilege_violations
from app.domain.schemas.supervisor_estimation import (
    PendingHumanReview,
    SupervisorEstimateRequest,
    SupervisorResumeRequest,
    SupervisorRunState,
)
from app.generation.rag.observability import log_stage

log = structlog.get_logger()

router = APIRouter(prefix="/v1/estimate", tags=["estimate-supervisor"])

# Both graphs share ONE AsyncPostgresSaver and therefore one ``checkpoints`` table.
# Without this namespace, the same estimation_id sent to /graph and to /supervisor
# would interleave two incompatible states on one thread — and LangGraph would happily
# try to resume a Session 14 state into the Session 13 topology.
_THREAD_PREFIX = "s14"


def _thread_config(estimation_id: str) -> dict:
    return {"configurable": {"thread_id": f"{_THREAD_PREFIX}:{estimation_id}"}}


def _require_graph(request: Request, request_id: str):
    graph = getattr(request.app.state, "supervisor_graph", None)
    if graph is None:
        log.error("supervisor_graph_unavailable", request_id=request_id)
        raise HTTPException(status_code=503, detail="Supervisor graph is not available.")
    return graph


def _build_run_state(estimation_id: str, snapshot) -> SupervisorRunState:
    """Turn a LangGraph ``StateSnapshot`` into the public ``SupervisorRunState``."""
    values = snapshot.values or {}
    paused = bool(snapshot.next)
    interrupts = getattr(snapshot, "interrupts", None) or ()

    pending_review = None
    # Guarded on BOTH: right after START the interrupt list can still be empty while
    # ``next`` is populated (the run is mid-leg, not paused for a human).
    if paused and interrupts:
        payload = interrupts[0].value or {}
        pending_review = PendingHumanReview(
            gate=payload.get("gate", "low_confidence_review"),
            estimation_id=estimation_id,
            reasons=payload.get("reasons") or [],
            confidence=payload.get("confidence"),
            threshold=payload.get("threshold"),
            estimate=payload.get("estimate"),
            validation=payload.get("validation"),
        )

    # "awaiting_human_review" is DERIVED, never stored: while paused the run is
    # genuinely mid-node, and writing it into the state before the interrupt would
    # break the interrupt-first discipline the gate depends on.
    status = (
        "awaiting_human_review"
        if pending_review is not None
        else (values.get("status") or "needs_review")
    )

    return SupervisorRunState(
        estimation_id=estimation_id,
        state="paused" if paused else "completed",
        status=status,
        pending_review=pending_review,
        estimate=values.get("estimate"),
        confidence=values.get("confidence"),
        requirements=values.get("requirements") or [],
        components=values.get("components") or [],
        budget_matches=values.get("budget_matches") or [],
        validation=values.get("validation"),
        human_decision=values.get("human_decision"),
        routing_history=values.get("routing_history") or [],
        agent_contributions=values.get("agent_contributions") or [],
        privilege_violations=privilege_violations(values),
        errors=values.get("errors") or [],
    )


@router.post(
    "/supervisor",
    response_model=SupervisorRunState,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("10/minute")
async def estimate_supervisor(
    request: Request, payload: SupervisorEstimateRequest
) -> SupervisorRunState:
    """START a supervisor run; runs to completion or to the human gate."""
    request_id = get_request_id(request)
    graph = _require_graph(request, request_id)

    estimation_id = payload.estimation_id or str(uuid4())
    config = _thread_config(estimation_id)
    try:
        with log_stage("supervisor_estimate_start", request_id, estimation_id=estimation_id):
            await graph.ainvoke(
                {"transcript": payload.transcript, "estimation_id": estimation_id}, config
            )
            snapshot = await graph.aget_state(config)
    except Exception as exc:  # noqa: BLE001 — any node/LLM failure → 502.
        log.error(
            "supervisor_estimate_failed",
            request_id=request_id,
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        raise HTTPException(status_code=502, detail="Failed to produce an estimate.") from exc

    return _build_run_state(estimation_id, snapshot)


@router.post(
    "/supervisor/{estimation_id}/resume",
    response_model=SupervisorRunState,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("10/minute")
async def resume_supervisor(
    request: Request, estimation_id: str, payload: SupervisorResumeRequest
) -> SupervisorRunState:
    """RESUME a paused run with the reviewer's decision; continues to the end."""
    request_id = get_request_id(request)
    graph = _require_graph(request, request_id)
    config = _thread_config(estimation_id)

    # Only a run that is actually paused can be resumed.
    snapshot = await graph.aget_state(config)
    if not snapshot.next:
        raise HTTPException(
            status_code=409,
            detail="No pending human review for this estimation_id (already completed or unknown).",
        )

    try:
        with log_stage("supervisor_estimate_resume", request_id, estimation_id=estimation_id):
            await graph.ainvoke(Command(resume=payload.model_dump()), config)
            snapshot = await graph.aget_state(config)
    except Exception as exc:  # noqa: BLE001 — any node/LLM failure → 502.
        log.error(
            "supervisor_estimate_resume_failed",
            request_id=request_id,
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        raise HTTPException(status_code=502, detail="Failed to resume the estimate.") from exc

    return _build_run_state(estimation_id, snapshot)


@router.get(
    "/supervisor/{estimation_id}/state",
    response_model=SupervisorRunState,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("60/minute")
async def supervisor_state(request: Request, estimation_id: str) -> SupervisorRunState:
    """Read the current snapshot of a run (pending review + artifacts)."""
    request_id = get_request_id(request)
    graph = _require_graph(request, request_id)
    config = _thread_config(estimation_id)
    snapshot = await graph.aget_state(config)
    if not snapshot.created_at and not snapshot.values:
        raise HTTPException(status_code=404, detail="Unknown estimation_id.")
    return _build_run_state(estimation_id, snapshot)
