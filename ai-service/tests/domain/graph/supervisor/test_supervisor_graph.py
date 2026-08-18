"""End-to-end runs over the compiled supervisor graph (MemorySaver, no network).

Covers the two paths that matter: a well-grounded estimate that runs unattended to
END, and an ungrounded one that pauses at the human gate and resumes with a decision.
The resume case additionally pins that the accumulators do NOT grow spurious rows —
the concrete payoff of the keyed reducers.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.domain.graph.supervisor.build import AGENT_NODES, build_supervisor_graph
from app.domain.graph.supervisor.privilege import AGENT_PRIVILEGES
from app.domain.graph.supervisor.state import privilege_violations

from .conftest import FULL_ROUTE, TRANSCRIPT, FakeWrapper, wire

# Plenty of precedent for both components → grounded, confident, no pause.
_GROUNDED = {"API": [80, 96, 88], "App": [120, 140, 130]}


def _graph():
    return build_supervisor_graph(MemorySaver())


async def _start(graph, thread="t1"):
    config = {"configurable": {"thread_id": thread}}
    await graph.ainvoke({"transcript": TRANSCRIPT, "estimation_id": thread}, config)
    return config


def test_the_node_table_matches_the_privilege_table():
    """A node without a declared privilege row would silently get no tools."""
    assert set(AGENT_NODES) == set(AGENT_PRIVILEGES) - {"supervisor"}


@pytest.mark.asyncio
async def test_happy_path_runs_every_agent_once_and_ends(monkeypatch):
    wrapper = FakeWrapper(route_script=list(FULL_ROUTE))
    wire(monkeypatch, wrapper=wrapper, hours_by_component=_GROUNDED)

    graph = _graph()
    config = await _start(graph)
    snapshot = await graph.aget_state(config)

    assert snapshot.next == ()  # reached END
    values = snapshot.values
    assert values["status"] == "validated"
    assert values["needs_human_review"] is False
    assert values["estimate"]["total_engineer_days"] == 20

    # Every specialist was dispatched exactly once, in dependency order.
    routed = [row["next_agent"] for row in values["routing_history"]]
    assert routed == [
        "requirements_extractor",
        "budget_searcher",
        "estimate_generator",
        "coherence_validator",
        "finish",
    ]


@pytest.mark.asyncio
async def test_routing_is_model_driven(monkeypatch):
    """The graph obeys the model's order, not a hard-coded one.

    The script asks for ``budget_searcher`` first — illegal, since nothing has been
    classified yet — so the guard corrects it. That correction being VISIBLE is the
    property under test: the model proposes, the cage disposes, and the trace says so.
    """
    wrapper = FakeWrapper(route_script=["budget_searcher"])
    wire(monkeypatch, wrapper=wrapper, hours_by_component=_GROUNDED)

    graph = _graph()
    config = await _start(graph, thread="t-routing")
    values = (await graph.aget_state(config)).values

    first = values["routing_history"][0]
    assert first["source"] == "fallback"
    assert first["next_agent"] == "requirements_extractor"


@pytest.mark.asyncio
async def test_requirements_extractor_never_calls_a_tool(monkeypatch):
    """Minimum privilege, observed over a whole run rather than asserted in a table."""
    wrapper = FakeWrapper(route_script=list(FULL_ROUTE))
    wire(monkeypatch, wrapper=wrapper, hours_by_component=_GROUNDED)

    graph = _graph()
    config = await _start(graph, thread="t-privilege")
    values = (await graph.aget_state(config)).values

    extractor_rows = [
        row for row in values["agent_contributions"] if row["agent"] == "requirements_extractor"
    ]
    assert len(extractor_rows) == 2  # extract + classify
    assert all(row["tool"] is None for row in extractor_rows)
    assert privilege_violations(values) == []


@pytest.mark.asyncio
async def test_the_audit_trail_covers_every_agent(monkeypatch):
    wrapper = FakeWrapper(route_script=list(FULL_ROUTE))
    wire(monkeypatch, wrapper=wrapper, hours_by_component=_GROUNDED)

    graph = _graph()
    config = await _start(graph, thread="t-audit")
    values = (await graph.aget_state(config)).values

    agents = {row["agent"] for row in values["agent_contributions"]}
    assert agents == {
        "requirements_extractor",
        "budget_searcher",
        "estimate_generator",
        "coherence_validator",
    }
    # Each tool call names the tool its agent is allowed to hold.
    for row in values["agent_contributions"]:
        if row["tool"] is not None:
            assert row["tool"] in AGENT_PRIVILEGES[row["agent"]]


# --------------------------------------------------------------------------- #
# The human-in-the-loop path                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_ungrounded_estimate_pauses_at_the_gate(monkeypatch):
    """No precedent at all → low confidence → the graph stops for a person."""
    wrapper = FakeWrapper(
        route_script=list(FULL_ROUTE),
        estimate={
            "components": [
                {"name": "API", "engineer_days": 10, "rationale": "guessed"},
                {"name": "App", "engineer_days": 10, "rationale": "guessed"},
            ],
            "total_engineer_days": 20,
            "confidence": "low",
            "reasoning": "no analogs found",
        },
    )
    wire(monkeypatch, wrapper=wrapper, hours_by_component={})  # nothing retrievable

    graph = _graph()
    config = await _start(graph, thread="t-pause")
    snapshot = await graph.aget_state(config)

    assert snapshot.next == ("human_review_gate",)
    payload = snapshot.interrupts[0].value
    assert payload["gate"] == "low_confidence_review"
    assert payload["estimation_id"] == "t-pause"
    # Both the confidence and the no-precedent conditions fired.
    assert len(payload["reasons"]) == 2
    assert payload["confidence"] == 0.0


@pytest.mark.asyncio
async def test_resume_completes_and_does_not_duplicate_accumulators(monkeypatch):
    """The payoff of the keyed reducers: a resume re-executes the gate node."""
    wrapper = FakeWrapper(
        route_script=list(FULL_ROUTE),
        estimate={
            "components": [{"name": "API", "engineer_days": 10, "rationale": "guessed"}],
            "total_engineer_days": 10,
            "confidence": "low",
            "reasoning": "no analogs",
        },
    )
    wire(monkeypatch, wrapper=wrapper, hours_by_component={})

    graph = _graph()
    config = await _start(graph, thread="t-resume")
    paused = (await graph.aget_state(config)).values
    contributions_before = len(paused["agent_contributions"])
    routing_before = len(paused["routing_history"])

    await graph.ainvoke(Command(resume={"decision": "approve", "note": "checked"}), config)
    snapshot = await graph.aget_state(config)
    values = snapshot.values

    assert snapshot.next == ()
    assert values["status"] == "validated"
    assert values["human_decision"]["note"] == "checked"
    # Exactly ONE new row (the human's), and routing untouched by the resume.
    assert len(values["agent_contributions"]) == contributions_before + 1
    assert len(values["routing_history"]) == routing_before
    assert values["agent_contributions"][-1]["agent"] == "human"


@pytest.mark.asyncio
async def test_resume_with_reject_marks_the_run_rejected(monkeypatch):
    wrapper = FakeWrapper(
        route_script=list(FULL_ROUTE),
        estimate={
            "components": [{"name": "API", "engineer_days": 10, "rationale": "guessed"}],
            "total_engineer_days": 10,
            "confidence": "low",
            "reasoning": "no analogs",
        },
    )
    wire(monkeypatch, wrapper=wrapper, hours_by_component={})

    graph = _graph()
    config = await _start(graph, thread="t-reject")
    await graph.ainvoke(Command(resume={"decision": "reject", "note": "not viable"}), config)
    values = (await graph.aget_state(config)).values

    assert values["status"] == "rejected"
    assert values["estimate"]["total_engineer_days"] == 10  # kept as evidence


@pytest.mark.asyncio
async def test_resume_with_adjust_rederives_the_total(monkeypatch):
    wrapper = FakeWrapper(
        route_script=list(FULL_ROUTE),
        estimate={
            "components": [{"name": "API", "engineer_days": 10, "rationale": "guessed"}],
            "total_engineer_days": 10,
            "confidence": "low",
            "reasoning": "no analogs",
        },
    )
    wire(monkeypatch, wrapper=wrapper, hours_by_component={})

    graph = _graph()
    config = await _start(graph, thread="t-adjust")
    await graph.ainvoke(
        Command(
            resume={
                "decision": "adjust",
                "estimate_overrides": {
                    "components": [
                        {"name": "API", "engineer_days": 25, "rationale": "human sized it"}
                    ]
                },
            }
        ),
        config,
    )
    values = (await graph.aget_state(config)).values

    assert values["status"] == "validated"
    assert values["estimate"]["total_engineer_days"] == 25
