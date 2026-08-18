"""Session 14 (live) — agent-level sandboxing (the three containment layers).

Covers: startup grant verification, ``guard_action`` (privilege + tenancy + irreversible),
``execute_guarded`` auditing DENIED and deferred writes, and the end-to-end persistence
leg where the human pause authorises the one irreversible write.
"""

from __future__ import annotations

import pytest
import structlog
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.config import get_settings
from app.domain.graph.supervisor import sandbox
from app.domain.graph.supervisor.build import build_supervisor_graph
from app.domain.graph.supervisor.sandbox import (
    ActionRequest,
    GrantVerificationError,
    ToolRisk,
    execute_guarded,
    guard_action,
    verify_tool_grants,
)

from .conftest import CONFIG, TRANSCRIPT, FakeWrapper, wire

FULL_ROUTE = [
    "requirements_extractor",
    "budget_searcher",
    "estimate_generator",
    "coherence_validator",
    "finish",
]


# --------------------------------------------------------------------------- #
# Layer 1 — grant verification fails the startup                              #
# --------------------------------------------------------------------------- #
def test_verify_tool_grants_accepts_the_real_table():
    verify_tool_grants()  # must not raise


def test_verify_tool_grants_rejects_a_tool_with_no_declared_risk(monkeypatch):
    monkeypatch.setattr(
        sandbox,
        "AGENT_TOOL_GRANTS",
        {"rogue_agent": frozenset({"delete_everything"})},
    )
    with pytest.raises(GrantVerificationError):
        verify_tool_grants()


def test_verify_tool_grants_rejects_an_unknown_tool(monkeypatch):
    # Declared risk but not in the known-tool set passed in.
    with pytest.raises(GrantVerificationError):
        verify_tool_grants(known_tools={"search_budgets"})


# --------------------------------------------------------------------------- #
# Layer 2 — guard_action (privilege + tenancy + irreversibility)              #
# --------------------------------------------------------------------------- #
def _req(tool="save_estimate", *, agent="persistence_agent", estimation_id="run-1", args=None):
    return ActionRequest(
        agent=agent,
        tool=tool,
        args=args if args is not None else {"estimation_id": estimation_id, "estimate": {"x": 1}},
        estimation_id=estimation_id,
        step=1,
    )


def test_ungranted_tool_is_denied():
    # budget_searcher may not write.
    decision = guard_action(
        _req(tool="save_estimate", agent="budget_searcher"), {"estimation_id": "run-1"}
    )
    assert decision.allowed is False
    assert "not granted" in decision.reason


def test_estimation_id_mismatch_is_denied():
    decision = guard_action(_req(estimation_id="run-1"), {"estimation_id": "run-2"})
    assert decision.allowed is False
    assert "does not match" in decision.reason


def test_argument_estimation_id_must_match_too():
    req = _req(args={"estimation_id": "OTHER", "estimate": {"x": 1}})
    decision = guard_action(req, {"estimation_id": "run-1"})
    assert decision.allowed is False


def test_irreversible_without_human_approval_requires_it():
    decision = guard_action(_req(), {"estimation_id": "run-1"})
    assert decision.allowed is True
    assert decision.requires_human_approval is True
    assert decision.risk == ToolRisk.IRREVERSIBLE


def test_irreversible_with_human_approval_is_cleared():
    state = {"estimation_id": "run-1", "human_decision": {"decision": "approve"}}
    decision = guard_action(_req(), state)
    assert decision.allowed is True
    assert decision.requires_human_approval is False


# --------------------------------------------------------------------------- #
# Layer 3 — execute_guarded audits everything, including denials              #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_execute_guarded_audits_a_denied_write():
    with structlog.testing.capture_logs() as logs:
        result, contribution = await execute_guarded(
            _req(tool="save_estimate", agent="budget_searcher"), {"estimation_id": "run-1"}
        )
    assert result["ok"] is False
    assert contribution["outcome"] == "denied"
    assert any(entry["event"] == "agent_privilege_denied" for entry in logs)


@pytest.mark.asyncio
async def test_execute_guarded_defers_an_unauthorised_irreversible_write():
    result, contribution = await execute_guarded(_req(), {"estimation_id": "run-1"})
    assert result["error"] == "awaiting_human_approval"
    assert contribution["outcome"] == "deferred"


@pytest.mark.asyncio
async def test_execute_guarded_writes_through_an_injected_sink_when_authorised():
    seen = {}

    def sink(estimation_id, estimate):
        seen["id"] = estimation_id
        return {"ok": True}

    state = {"estimation_id": "run-1", "human_decision": {"decision": "approve"}}
    result, contribution = await execute_guarded(_req(), state, sink=sink)
    assert result["ok"] is True
    assert contribution["outcome"] == "ok"
    assert seen["id"] == "run-1"


# --------------------------------------------------------------------------- #
# End-to-end — the sandboxed graph routes the write through the human gate     #
# --------------------------------------------------------------------------- #
@pytest.fixture
def persistence_enabled(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_PERSISTENCE_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_queued_write_forces_the_gate_and_approval_persists(monkeypatch, persistence_enabled):
    wire(
        monkeypatch,
        wrapper=FakeWrapper(route_script=list(FULL_ROUTE)),
        hours_by_component={"API": [80, 88], "App": [80, 88]},
    )
    graph = build_supervisor_graph(MemorySaver(), sandboxed=True)

    await graph.ainvoke({"transcript": TRANSCRIPT, "estimation_id": "s14-test"}, CONFIG)
    snapshot = await graph.aget_state(CONFIG)

    # A well-grounded estimate would not pause on confidence; the queued irreversible
    # write is what forces the gate.
    assert snapshot.next == ("human_review_gate",)
    reasons = snapshot.interrupts[0].value["reasons"]
    assert any("irreversible save_estimate is queued" in r for r in reasons)

    await graph.ainvoke(Command(resume={"decision": "approve"}), CONFIG)
    final = await graph.aget_state(CONFIG)
    assert final.next == ()
    assert final.values["saved"]["ok"] is True


@pytest.mark.asyncio
async def test_rejected_estimate_is_never_persisted(monkeypatch, persistence_enabled):
    wire(
        monkeypatch,
        wrapper=FakeWrapper(route_script=list(FULL_ROUTE)),
        hours_by_component={"API": [80, 88], "App": [80, 88]},
    )
    graph = build_supervisor_graph(MemorySaver(), sandboxed=True)

    thread = {"configurable": {"thread_id": "s14-reject"}}
    await graph.ainvoke({"transcript": TRANSCRIPT, "estimation_id": "s14-reject"}, thread)
    await graph.ainvoke(Command(resume={"decision": "reject"}), thread)

    final = await graph.aget_state(thread)
    # The write was DEFERRED (no human approval), so nothing was persisted.
    assert final.values["saved"]["ok"] is False
    assert final.values["saved"]["error"] == "awaiting_human_approval"
