"""Session 14 — the estimation flow as a SUPERVISOR + specialised agents.

Session 13 expressed the flow as a graph: five nodes wired in a fixed order (and, in
the live session, a pipeline of agents with fixed ``Command`` handovers). In both
cases the control flow lives in the CODE — someone wrote, at authoring time, that
``search_budgets`` runs after ``classify_components``.

This package crosses the frontier the session is about: **the model decides what runs
next**. A hand-built ``supervisor`` node reads the shared state, picks the next
specialist and hands over with ``Command(goto=..., update=...)``; each specialist
returns control to the supervisor. That is the whole difference — not the number of
nodes, but who owns the control flow.

Three things make it a multi-agent system rather than architecture theatre:

* **Minimum privilege** (``privilege.py``): every agent declares the tools it may
  call, and a guarded dispatcher rejects anything else BEFORE executing it. The
  requirements extractor holds no business tools at all.
* **Visible routing** (``supervisor.py`` + ``state.routing_history``): every routing
  decision is written into the state with the model's own reason, so it survives in
  the checkpoint and shows up in the trace. A decision nobody can inspect is an act
  of faith.
* **Human-in-the-loop** (``gate.py``): when the estimate is not trustworthy enough —
  low confidence, out of historical range, or no precedent at all — the graph
  ``interrupt()``s and waits for a person, with the pause durably held by the
  Session 13 checkpointer.

It COEXISTS with the Session 13 graph: ``app/domain/graph/build.py``, ``agents/`` and
the ``/v1/estimate/graph`` endpoints are untouched. Both graphs share one
checkpointer, so the router namespaces thread ids (``s14:<estimation_id>``).
"""

from __future__ import annotations

from app.domain.graph.supervisor.build import build_supervisor_graph
from app.domain.graph.supervisor.privilege import (
    AGENT_PRIVILEGES,
    PrivilegeViolation,
    allowed_tools,
)
from app.domain.graph.supervisor.state import SupervisorState, privilege_violations

__all__ = [
    "AGENT_PRIVILEGES",
    "PrivilegeViolation",
    "SupervisorState",
    "allowed_tools",
    "build_supervisor_graph",
    "privilege_violations",
]
