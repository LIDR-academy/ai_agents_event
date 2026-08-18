"""Failure mode #3 — interrupt() pauses, but the resume never continues.

SYMPTOM (live): the graph pauses at the human gate (``status = awaiting_human_review``),
the reviewer approves, the resume call returns 200 — and yet the original run is still
stuck. A second look shows a BRAND NEW run was created and the paused one never moved.

CAUSE: the ``thread_id`` differs between the initial invoke and the resume. A checkpointer
keys state by ``thread_id``; ``Command(resume=...)`` on a different thread does not answer
the pending interrupt on the first thread — it starts a fresh run.

FIX (str_replace on screen): derive the thread id the SAME way in both calls — from the
estimation id: ``{"configurable": {"thread_id": f"s14:{estimation_id}"}}``. That is exactly
what ``app/api/routers/estimate_supervisor.py`` does.

This is a one-node interrupting graph so the mechanics are visible without the rest of the
supervisor.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict


class _GateState(TypedDict, total=False):
    estimation_id: str
    decision: str
    done: bool


def _gate(state: _GateState) -> dict:
    # interrupt() FIRST, then fold the answer in — the resume re-executes this node.
    answer = interrupt({"gate": "review", "estimation_id": state.get("estimation_id")})
    return {"decision": (answer or {}).get("decision", "approve"), "done": True}


def build_gate_graph():
    builder = StateGraph(_GateState)
    builder.add_node("gate", _gate)
    builder.add_edge(START, "gate")
    builder.add_edge("gate", END)
    return builder.compile(checkpointer=MemorySaver())


def _thread(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


async def start_and_resume(
    graph, estimation_id: str, *, start_thread: str, resume_thread: str, decision: str = "approve"
) -> dict:
    """Start a run on ``start_thread``, resume on ``resume_thread``.

    Returns a small report the test asserts on. When the two threads match, the run
    resumes and ``done`` is True; when they differ, the original thread stays paused
    (``start_paused`` True) and the resume touched a different thread entirely.
    """
    start_cfg = _thread(start_thread)
    await graph.ainvoke({"estimation_id": estimation_id}, start_cfg)

    # BROKEN call sites pass a resume_thread that is NOT start_thread.
    resume_cfg = _thread(resume_thread)
    await graph.ainvoke(Command(resume={"decision": decision}), resume_cfg)

    start_state = await graph.aget_state(start_cfg)
    return {
        "start_thread": start_thread,
        "resume_thread": resume_thread,
        "start_paused": bool(start_state.next),  # True == the original run never moved
        "start_done": bool(start_state.values.get("done")),
        "start_decision": start_state.values.get("decision"),
    }
