"""The two human gates (HITL) — ``interrupt()`` / ``Command(resume=...)``.

A gate node PAUSES the graph and surfaces a payload for a human to review. The run
stays paused — its state durably held by the checkpointer — until the business
backend resumes it with ``Command(resume=<decision>)``, at which point ``interrupt``
RETURNS that decision and the node finishes.

Critical discipline (the "state does not survive the pause" pitfall): a gate calls
``interrupt()`` FIRST and only writes plain, last-write-wins fields afterwards. It
never writes an accumulator (reducer) channel before interrupting — because on
resume LangGraph RE-EXECUTES the whole node from the top, so any write before the
``interrupt()`` would run twice and, for a reducer, double-append.
"""

from __future__ import annotations

import logfire
import structlog
from langgraph.types import interrupt

from app.domain.graph.agents._common import modules_from_structure

log = structlog.get_logger()


async def human_gate_structure(state: dict) -> dict:
    """HUMAN GATE 1 — review/edit the module→task breakdown, then approve.

    Resume decision shape::

        {"approved": bool, "modules": [ {"name": ..., "tasks": [{"name", "description"}]} ]}

    ``modules`` is the human-edited tree; omit it to accept the structure as proposed.
    """
    decision = interrupt(
        {
            "gate": "structure_review",
            "estimation_id": state.get("estimation_id"),
            "complexity": state.get("complexity"),
            "structure": state.get("structure"),
        }
    )
    with logfire.span("node: human_gate_structure"):
        decision = decision or {}
        modules = decision.get("modules") or modules_from_structure(state.get("structure"))
        log.info(
            "human_gate_structure_resumed",
            approved=decision.get("approved"),
            modules=len(modules),
        )
        return {"approved_modules": modules, "gate1_decision": decision}


async def human_gate_analysis(state: dict) -> dict:
    """HUMAN GATE 2 — final review: validate, complete missing data, decide.

    Resume decision shape::

        {"validated": bool, "estimate_overrides": {...}, "want_proposal": bool}

    ``estimate_overrides`` are the fields the human completed/edited (merged over the
    estimate); ``status`` becomes "validated" or "needs_review"; ``want_proposal``
    drives the conditional edge into the bonus proposal agent.
    """
    decision = interrupt(
        {
            "gate": "final_review",
            "estimation_id": state.get("estimation_id"),
            "estimate": state.get("estimate"),
            "analysis_report": state.get("analysis_report"),
        }
    )
    with logfire.span("node: human_gate_analysis"):
        decision = decision or {}
        overrides = decision.get("estimate_overrides") or {}
        estimate = {**(state.get("estimate") or {}), **overrides}
        # The override merge is shallow — if the human edited the module→task hours,
        # rederive the headline totals so days/ratio/confidence stay consistent with
        # the new hours (e.g. filling a "sin análogo" task raises the grounded ratio).
        if estimate.get("modules"):
            from app.domain.graph.agents._common import recompute_estimate_totals

            estimate = {**estimate, **recompute_estimate_totals(estimate["modules"])}
        status = "validated" if decision.get("validated") else "needs_review"
        log.info(
            "human_gate_analysis_resumed",
            validated=decision.get("validated"),
            want_proposal=decision.get("want_proposal"),
            overrides=len(overrides),
        )
        # Pricing rides along with the human's decision. Kept verbatim: the graph is a
        # carrier here, not a calculator.
        update = {"estimate": estimate, "gate2_decision": decision, "status": status}
        if decision.get("pricing"):
            update["pricing"] = decision["pricing"]
        return update
