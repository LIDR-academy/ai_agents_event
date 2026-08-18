"""Wire and compile the multi-agent estimation graph (Session 13, live).

The pre-exercise graph was five *component-level* nodes wired straight through. The
live session re-expresses it as a pipeline of SPECIALISED AGENTS with two explicit
handovers and two human gates:

    START → classifier_agent
    classifier_agent      ──Command(goto)──▶  structure_agent      (HANDOVER 1)
    structure_agent       ──edge──▶           human_gate_structure (interrupt #1)
    human_gate_structure  ──Send fan-out──▶   estimate_task_hours × N  (parallel)
    estimate_task_hours   ──edge──▶           recover_and_handover (join)
    recover_and_handover  ──Command(goto)──▶  analysis_agent       (HANDOVER 2)
    analysis_agent        ──edge──▶           human_gate_analysis  (interrupt #2)
    human_gate_analysis   ──conditional──▶    proposal_agent | END

Two nodes route with ``Command(goto=..., update=...)`` — the explicit handovers that
pass control AND state. Two nodes call ``interrupt()`` — the human gates. The fan-out
uses the ``Send`` API (one branch per approved task) with the keyed ``merge_task_hours``
reducer accumulating results. A checkpointer is REQUIRED here (not optional as in the
pre-exercise): the interrupts persist state across the human pauses.

The pre-exercise nodes remain in ``nodes.py`` as the "before" the live session grows
from; they are simply not wired here anymore.
"""

from __future__ import annotations

import structlog
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.config import get_settings
from app.domain.graph.agents import (
    analysis_agent,
    classifier_agent,
    estimate_task_hours,
    human_gate_analysis,
    human_gate_structure,
    proposal_agent,
    recover_and_handover,
    structure_agent,
)
from app.domain.graph.state import EstimationState

log = structlog.get_logger()


def fan_out_hours(state: EstimationState):
    """Conditional edge after gate 1: one ``Send`` per approved task (fan-out).

    Each ``Send`` carries a single task as the branch's input state, so
    ``estimate_task_hours`` runs once per task IN PARALLEL. With no approved tasks it
    routes straight to the join so the graph never stalls.

    The retrieval knobs are resolved HERE, once, and travel in the ``Send`` payload —
    deliberately not inside the branch. The branch runs once per task (~120 times in
    parallel), so reading the Redis-backed runtime config there would mean 120
    synchronous round-trips from inside the event loop, and an operator flipping the
    threshold mid-fan-out would give different branches different cuts. Resolving once
    also makes the whole fan-out reproducible from a single logged pair of values.
    """
    from app.dependencies import get_runtime_retrieval_config

    runtime = get_runtime_retrieval_config()
    top_k = runtime.effective_task_hours_top_k()
    distance_threshold = runtime.effective_task_hours_distance_threshold()

    modules = state.get("approved_modules") or []
    sends = [
        Send(
            "estimate_task_hours",
            {
                "module": m["name"],
                "task": t["name"],
                "description": t.get("description"),
                "top_k": top_k,
                "distance_threshold": distance_threshold,
            },
        )
        for m in modules
        for t in (m.get("tasks") or [])
        if t.get("name")
    ]
    log.info(
        "fan_out_hours",
        tasks=len(sends),
        top_k=top_k,
        distance_threshold=distance_threshold,
    )
    return sends or "recover_and_handover"


def route_after_gate2(state: EstimationState) -> str:
    """Conditional edge after gate 2: draft a proposal, or end.

    Routes to the bonus ``proposal_agent`` only when it is enabled AND the human asked
    for a proposal at the final gate; otherwise the run ends.
    """
    settings = get_settings()
    decision = state.get("gate2_decision") or {}
    if settings.GRAPH_PROPOSAL_ENABLED and decision.get("want_proposal"):
        return "proposal"
    return "end"


def build_graph(checkpointer=None):
    """Build and compile the multi-agent estimation graph.

    ``checkpointer`` persists state per ``thread_id`` (an ``AsyncPostgresSaver`` in the
    app, a ``MemorySaver`` in tests). It is REQUIRED for the interrupts to resume — a
    ``None`` checkpointer compiles but cannot pause/resume at the human gates.
    """
    builder = StateGraph(EstimationState)

    builder.add_node("classifier_agent", classifier_agent)
    builder.add_node("structure_agent", structure_agent)
    builder.add_node("human_gate_structure", human_gate_structure)
    builder.add_node("estimate_task_hours", estimate_task_hours)
    builder.add_node("recover_and_handover", recover_and_handover)
    builder.add_node("analysis_agent", analysis_agent)
    builder.add_node("human_gate_analysis", human_gate_analysis)
    builder.add_node("proposal_agent", proposal_agent)

    builder.add_edge(START, "classifier_agent")
    # classifier_agent → structure_agent is a Command(goto) HANDOVER (no static edge).
    builder.add_edge("structure_agent", "human_gate_structure")
    # Human gate 1 → fan-out one branch per approved task.
    builder.add_conditional_edges(
        "human_gate_structure",
        fan_out_hours,
        ["estimate_task_hours", "recover_and_handover"],
    )
    # Every fan-out branch joins at recover_and_handover (runs once per super-step).
    builder.add_edge("estimate_task_hours", "recover_and_handover")
    # recover_and_handover → analysis_agent is a Command(goto) HANDOVER (no static edge).
    builder.add_edge("analysis_agent", "human_gate_analysis")
    # Human gate 2 → proposal (bonus) or END.
    builder.add_conditional_edges(
        "human_gate_analysis",
        route_after_gate2,
        {"proposal": "proposal_agent", "end": END},
    )
    builder.add_edge("proposal_agent", END)

    return builder.compile(checkpointer=checkpointer)
