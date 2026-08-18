"""``/v1/estimate/graph`` — the multi-agent estimation flow (Session 13, live).

Same external contract posture as the other estimate endpoints, but the flow now
PAUSES at two human gates, so the contract is three verbs over one ``thread_id``
(``= estimation_id``):

* ``POST /v1/estimate/graph`` — START. Runs until the first human gate and returns a
  ``GraphRunState`` (``state="paused"``, ``pending_gate`` = the structure to review).
* ``POST /v1/estimate/graph/{estimation_id}/resume`` — RESUME. Feeds the human's
  decision via ``Command(resume=...)``; the run continues to the next gate or to
  completion. Idempotent-guarded: resuming a run with nothing pending → 409.
* ``GET /v1/estimate/graph/{estimation_id}/state`` — read the current snapshot (the
  pending gate + artifacts). Lets the UI recover a paused run after any delay.

The business backend (Rails) drives the human part: it renders each ``pending_gate``
in the platform UI and calls ``resume`` when the person approves. The service IA only
exposes the start and the resume points; the pattern is stack-agnostic (any HTTP
client can drive it). Auth reuses ``ESTIMATE_API_KEY``; graph/LLM failures → 502; a
graph that failed to build at startup (``app.state.graph is None``) → 503.
"""

from __future__ import annotations

from uuid import uuid4

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from langgraph.types import Command

from app.api.deps import get_request_id
from app.api.rate_limiting import limiter
from app.api.security import require_estimate_key
from app.config import Settings, get_settings
from app.dependencies import get_graph_activity
from app.domain.graph.activity import GraphActivityLog, describe_node
from app.domain.graph.agents.proposal import build_proposal
from app.domain.graph.personas import persona_for
from app.domain.schemas.graph_estimation import (
    GraphProposalRequest,
    ActivityEntry,
    GraphEstimateRequest,
    GraphProgress,
    GraphProposalResponse,
    GraphResumeRequest,
    GraphRunState,
    PendingGate,
)
from app.generation.rag.observability import log_stage

log = structlog.get_logger()

router = APIRouter(prefix="/v1/estimate", tags=["estimate-graph"])


def _require_graph(request: Request, request_id: str):
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        log.error("graph_unavailable", request_id=request_id)
        raise HTTPException(status_code=503, detail="Estimation graph is not available.")
    return graph


def _build_run_state(estimation_id: str, snapshot) -> GraphRunState:
    """Turn a LangGraph ``StateSnapshot`` into the public ``GraphRunState``."""
    values = snapshot.values or {}
    paused = bool(snapshot.next)
    pending_gate = None
    interrupts = getattr(snapshot, "interrupts", None) or ()
    if paused and interrupts:
        gate_value = interrupts[0].value or {}
        pending_gate = PendingGate(
            gate=gate_value.get("gate", "unknown"),
            estimation_id=estimation_id,
            payload={k: v for k, v in gate_value.items() if k not in ("gate", "estimation_id")},
        )
    return GraphRunState(
        estimation_id=estimation_id,
        state="paused" if paused else "completed",
        pending_gate=pending_gate,
        complexity=values.get("complexity"),
        structure=values.get("structure"),
        task_hours=values.get("task_hours") or [],
        estimate=values.get("estimate"),
        analysis_report=values.get("analysis_report"),
        proposal=values.get("proposal"),
        status=values.get("status"),
        errors=values.get("errors") or [],
    )


@router.post(
    "/graph",
    response_model=GraphRunState,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("10/minute")
async def estimate_graph(request: Request, payload: GraphEstimateRequest) -> GraphRunState:
    """START the multi-agent flow; runs to the first human gate."""
    request_id = get_request_id(request)
    graph = _require_graph(request, request_id)

    estimation_id = payload.estimation_id or str(uuid4())
    config = {"configurable": {"thread_id": estimation_id}}
    try:
        with log_stage("graph_estimate_start", request_id, estimation_id=estimation_id):
            await graph.ainvoke(
                {"transcript": payload.transcript, "estimation_id": estimation_id},
                config,
            )
            snapshot = await graph.aget_state(config)
    except Exception as exc:  # noqa: BLE001 — any node/LLM failure → 502.
        log.error(
            "graph_estimate_failed",
            request_id=request_id,
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        raise HTTPException(status_code=502, detail="Failed to produce an estimate.") from exc

    return _build_run_state(estimation_id, snapshot)


@router.post(
    "/graph/{estimation_id}/resume",
    response_model=GraphRunState,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("10/minute")
async def resume_graph(
    request: Request, estimation_id: str, payload: GraphResumeRequest
) -> GraphRunState:
    """RESUME a paused run with the human's decision; continues to the next gate/END."""
    request_id = get_request_id(request)
    graph = _require_graph(request, request_id)
    config = {"configurable": {"thread_id": estimation_id}}

    # Idempotency guard: only a run that is actually paused can be resumed.
    snapshot = await graph.aget_state(config)
    if not snapshot.next:
        raise HTTPException(
            status_code=409,
            detail="No pending human gate for this estimation_id (already completed or unknown).",
        )

    try:
        with log_stage("graph_estimate_resume", request_id, estimation_id=estimation_id):
            await graph.ainvoke(Command(resume=payload.decision), config)
            snapshot = await graph.aget_state(config)
    except Exception as exc:  # noqa: BLE001 — any node/LLM failure → 502.
        log.error(
            "graph_estimate_resume_failed",
            request_id=request_id,
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        raise HTTPException(status_code=502, detail="Failed to resume the estimate.") from exc

    return _build_run_state(estimation_id, snapshot)


@router.get(
    "/graph/{estimation_id}/state",
    response_model=GraphRunState,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("60/minute")
async def graph_state(request: Request, estimation_id: str) -> GraphRunState:
    """Read the current snapshot of a run (pending gate + artifacts)."""
    request_id = get_request_id(request)
    graph = _require_graph(request, request_id)
    config = {"configurable": {"thread_id": estimation_id}}
    snapshot = await graph.aget_state(config)
    if not snapshot.created_at and not snapshot.values:
        raise HTTPException(status_code=404, detail="Unknown estimation_id.")
    return _build_run_state(estimation_id, snapshot)


# --------------------------------------------------------------------------- #
# Session 13 (live) — the LIVE variant: run in the background with ``astream``  #
# and expose a per-agent activity feed for a "watch the agents work" panel.     #
# Additive: the three blocking verbs above are untouched (tests + the taught     #
# comparison path keep working); the wizard drives this trio instead.           #
# --------------------------------------------------------------------------- #
async def _stream_graph(
    *,
    graph,
    activity: GraphActivityLog,
    payload,
    config: dict,
    estimation_id: str,
    request_id: str,
) -> None:
    """BackgroundTask body: drive the graph with ``astream`` and log each node.

    ``stream_mode="updates"`` yields ``{node_name: update}`` as each node finishes
    (the fan-out yields one entry per parallel task). The stream ends at the next
    ``interrupt()`` (a human gate) or at END; the checkpointer has already persisted
    the state, so ``/progress``, ``/state`` and ``resume`` keep working unchanged.
    """
    try:
        async for chunk in graph.astream(payload, config, stream_mode="updates"):
            for node_name, update in chunk.items():
                for line in describe_node(node_name, update):
                    activity.append(
                        estimation_id, node=line["node"], label=line["label"], message=line["message"]
                    )
    except Exception as exc:  # noqa: BLE001 — surface the failure in the feed, don't crash the loop.
        log.error(
            "graph_stream_failed",
            request_id=request_id,
            estimation_id=estimation_id,
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        activity.append(estimation_id, node="error", label="Error", message=str(exc)[:200])


def _progress_state(snapshot) -> str:
    """running (mid-leg) | paused (at a gate) | completed (END)."""
    if not getattr(snapshot, "next", None):
        return "completed"
    interrupts = getattr(snapshot, "interrupts", None) or ()
    return "paused" if interrupts else "running"


@router.post(
    "/graph/stream",
    response_model=GraphProgress,
    status_code=202,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("10/minute")
async def estimate_graph_stream(
    request: Request,
    payload: GraphEstimateRequest,
    background: BackgroundTasks,
    activity: GraphActivityLog = Depends(get_graph_activity),
) -> GraphProgress:
    """START the flow in the background; returns 202 immediately, poll ``/progress``."""
    request_id = get_request_id(request)
    graph = _require_graph(request, request_id)
    estimation_id = payload.estimation_id or str(uuid4())
    config = {"configurable": {"thread_id": estimation_id}}

    activity.reset(estimation_id)  # fresh run (resume appends, START resets).
    background.add_task(
        _stream_graph,
        graph=graph,
        activity=activity,
        payload={"transcript": payload.transcript, "estimation_id": estimation_id},
        config=config,
        estimation_id=estimation_id,
        request_id=request_id,
    )
    log.info("graph_stream_started", request_id=request_id, estimation_id=estimation_id)
    return GraphProgress(estimation_id=estimation_id, state="running", activity=[])


@router.post(
    "/graph/{estimation_id}/resume-stream",
    response_model=GraphProgress,
    status_code=202,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("10/minute")
async def resume_graph_stream(
    request: Request,
    estimation_id: str,
    payload: GraphResumeRequest,
    background: BackgroundTasks,
    activity: GraphActivityLog = Depends(get_graph_activity),
) -> GraphProgress:
    """RESUME a paused run in the background; returns 202 immediately, poll ``/progress``."""
    request_id = get_request_id(request)
    graph = _require_graph(request, request_id)
    config = {"configurable": {"thread_id": estimation_id}}

    # Same idempotency guard as the blocking resume: only a paused run can resume.
    snapshot = await graph.aget_state(config)
    if not snapshot.next:
        raise HTTPException(
            status_code=409,
            detail="No pending human gate for this estimation_id (already completed or unknown).",
        )

    background.add_task(
        _stream_graph,
        graph=graph,
        activity=activity,
        payload=Command(resume=payload.decision),
        config=config,
        estimation_id=estimation_id,
        request_id=request_id,
    )
    log.info("graph_resume_stream_started", request_id=request_id, estimation_id=estimation_id)
    return GraphProgress(estimation_id=estimation_id, state="running", activity=[])


@router.get(
    "/graph/{estimation_id}/progress",
    response_model=GraphProgress,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("120/minute")
async def graph_progress(
    request: Request,
    estimation_id: str,
    activity: GraphActivityLog = Depends(get_graph_activity),
) -> GraphProgress:
    """Poll a background run: current state (running/paused/completed) + activity feed."""
    request_id = get_request_id(request)
    graph = _require_graph(request, request_id)
    config = {"configurable": {"thread_id": estimation_id}}
    snapshot = await graph.aget_state(config)

    entries = [ActivityEntry(**e) for e in activity.read(estimation_id)]
    # No 404 here: right after START the first checkpoint may not exist yet — report
    # "running" so the poller keeps going until a node commits or the run pauses.
    run_state = _build_run_state(estimation_id, snapshot)
    data = run_state.model_dump()
    data["state"] = _progress_state(snapshot)
    data["activity"] = entries
    return GraphProgress(**data)


@router.post(
    "/graph/{estimation_id}/proposal",
    response_model=GraphProposalResponse,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("10/minute")
async def graph_proposal(
    request: Request,
    estimation_id: str,
    payload: GraphProposalRequest | None = None,
    settings: Settings = Depends(get_settings),
) -> GraphProposalResponse:
    """Draft (or re-draft) the commercial proposal from the run's validated estimate.

    Stateless: reads the persisted estimate from the checkpointer snapshot and runs the
    proposal LLM directly — it does NOT re-run or mutate the graph. Lets the wizard
    produce a proposal after completion even if it was not asked for at gate 2."""
    request_id = get_request_id(request)
    graph = _require_graph(request, request_id)
    config = {"configurable": {"thread_id": estimation_id}}
    snapshot = await graph.aget_state(config)
    estimate = (snapshot.values or {}).get("estimate")
    if not estimate:
        raise HTTPException(
            status_code=409,
            detail="No validated estimate for this estimation_id (run not far enough / unknown).",
        )
    try:
        with log_stage("graph_proposal", request_id, estimation_id=estimation_id):
            persona = persona_for("proposal_agent", enabled=settings.GRAPH_PERSONAS_ENABLED)
            proposal = await build_proposal(
                estimate,
                (snapshot.values or {}).get("analysis_report") or {},
                persona=persona,
                # Read from the run's state, where gate 2 left it, so a proposal drafted
                # after the fact quotes the same price the in-graph agent would have.
                # The caller's current pricing wins over the one frozen at gate 2, so a
                # re-draft after changing the rate quotes the new figure.
                pricing=(payload.pricing if payload else None)
                or (snapshot.values or {}).get("pricing"),
                # Raw transcript first — the reformulation is translated to English by the
                # classifier, and the brief is what sets the proposal's language.
                brief=(snapshot.values or {}).get("transcript")
                or (snapshot.values or {}).get("reformulated_transcript"),
            )
    except Exception as exc:  # noqa: BLE001 — any LLM failure → 502.
        log.error(
            "graph_proposal_failed",
            request_id=request_id,
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        raise HTTPException(status_code=502, detail="Failed to draft the proposal.") from exc

    return GraphProposalResponse(
        estimation_id=estimation_id,
        title=proposal.title,
        executive_summary=proposal.executive_summary,
        scope=proposal.scope,
        total_engineer_days=proposal.total_engineer_days,
        total_price_eur=proposal.total_price_eur,
        body_markdown=proposal.body_markdown,
    )
