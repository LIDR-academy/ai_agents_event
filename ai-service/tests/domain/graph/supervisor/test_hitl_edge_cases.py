"""Session 14 (live) — human-in-the-loop, tested by INVARIANT, not by path.

The route is chosen by the model and is non-deterministic, so these tests never assert
"agent X ran at step N". They assert the properties that must hold whatever the path:

* the pause fires when a trigger holds,
* the paused state is persisted in the checkpoint,
* resume continues from that checkpoint with the human's decision in the state,
* no agent acted before its preconditions (read from ``routing_history``),
* the step budget was never exceeded,
* a second resume on a finished run does not corrupt the result (idempotency).

Each trigger is forced deterministically through the scripted fakes (the fixtures under
``exercises/session-14/edge_cases/`` are what trigger the same signals against the REAL
retrieval/LLM in the live demo; here the facts are controlled so the run is network-free).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.config import get_settings
from app.domain.graph.supervisor.build import build_supervisor_graph
from app.domain.graph.supervisor.supervisor import _ORDER

from .conftest import FakeWrapper, wire

_EDGE_DIR = Path(__file__).resolve().parents[4] / "exercises" / "session-14" / "edge_cases"

FULL_ROUTE = [
    "requirements_extractor",
    "budget_searcher",
    "estimate_generator",
    "coherence_validator",
    "finish",
]

_GROUNDED = {"API": [80, 88], "App": [80, 88]}


def _estimate(confidence="high", api_days=10):
    return {
        "components": [
            {"name": "API", "engineer_days": api_days, "rationale": "from refs"},
            {"name": "App", "engineer_days": 10, "rationale": "from refs"},
        ],
        "total_engineer_days": api_days + 10,
        "confidence": confidence,
        "reasoning": "consolidated",
    }


# (fixture file, wrapper estimate, hours, substring that must appear in a review reason)
_SCENARIOS = {
    "low_confidence": (_estimate(confidence="low"), _GROUNDED, "below the"),
    "out_of_historical_range": (_estimate(api_days=300), _GROUNDED, "outside the plausible range"),
    "no_precedent": (_estimate(), {}, "have any precedent"),
}


async def _run_to_pause(monkeypatch, scenario: str, thread: str):
    estimate, hours, _needle = _SCENARIOS[scenario]
    wire(
        monkeypatch,
        wrapper=FakeWrapper(route_script=list(FULL_ROUTE), estimate=estimate),
        hours_by_component=hours,
    )
    graph = build_supervisor_graph(MemorySaver())
    config = {"configurable": {"thread_id": thread}}
    transcript = (_EDGE_DIR / f"{scenario}.txt").read_text(encoding="utf-8")
    await graph.ainvoke({"transcript": transcript, "estimation_id": thread}, config)
    return graph, config


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", list(_SCENARIOS))
async def test_each_signal_trips_the_pause(monkeypatch, scenario):
    graph, config = await _run_to_pause(monkeypatch, scenario, f"edge-{scenario}")
    snapshot = await graph.aget_state(config)

    # Invariant 1: the gate paused.
    assert snapshot.next == ("human_review_gate",)
    payload = snapshot.interrupts[0].value
    assert payload["gate"] == "low_confidence_review"
    # ...for the RIGHT reason (the signal this fixture is built to trigger).
    needle = _SCENARIOS[scenario][2]
    assert any(needle in reason for reason in payload["reasons"])


@pytest.mark.asyncio
async def test_paused_state_is_persisted_in_the_checkpoint(monkeypatch):
    graph, config = await _run_to_pause(monkeypatch, "low_confidence", "edge-persist")

    # A fresh read of the checkpoint (no in-memory handoff) still shows the paused run.
    snapshot = await graph.aget_state(config)
    assert snapshot.next == ("human_review_gate",)
    assert snapshot.values.get("estimate") is not None
    assert snapshot.values.get("confidence") is not None


@pytest.mark.asyncio
async def test_resume_continues_with_the_human_decision_in_state(monkeypatch):
    graph, config = await _run_to_pause(monkeypatch, "low_confidence", "edge-resume")

    await graph.ainvoke(Command(resume={"decision": "approve", "note": "checked"}), config)
    final = await graph.aget_state(config)

    assert final.next == ()
    assert final.values["human_decision"]["decision"] == "approve"
    assert final.values["status"] == "validated"


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", list(_SCENARIOS))
async def test_no_agent_acted_before_its_preconditions(monkeypatch, scenario):
    graph, config = await _run_to_pause(monkeypatch, scenario, f"edge-precond-{scenario}")
    snapshot = await graph.aget_state(config)

    dispatched = [
        row["next_agent"]
        for row in snapshot.values.get("routing_history") or []
        if row["next_agent"] in _ORDER
    ]
    # The dispatch order must be a subsequence of the dependency ladder: nobody ran before
    # the agent that produces its inputs, whatever order the model asked for.
    positions = [_ORDER.index(name) for name in dispatched]
    assert positions == sorted(positions)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", list(_SCENARIOS))
async def test_step_budget_was_never_exceeded(monkeypatch, scenario):
    graph, config = await _run_to_pause(monkeypatch, scenario, f"edge-budget-{scenario}")
    snapshot = await graph.aget_state(config)

    assert snapshot.values.get("supervisor_steps", 0) <= get_settings().SUPERVISOR_MAX_STEPS


@pytest.mark.asyncio
async def test_second_resume_on_a_finished_run_does_not_corrupt_it(monkeypatch):
    graph, config = await _run_to_pause(monkeypatch, "low_confidence", "edge-idem")

    await graph.ainvoke(Command(resume={"decision": "approve"}), config)
    finished = await graph.aget_state(config)
    assert finished.next == ()
    before = finished.values.get("estimate")

    # A second resume must be a no-op. The router refuses (409) when nothing is pending;
    # even if a client bypassed that, the finished result stays intact.
    if not finished.next:
        try:
            await graph.ainvoke(Command(resume={"decision": "reject"}), config)
        except Exception:
            pass
    after = await graph.aget_state(config)
    assert after.values.get("estimate") == before
    assert after.values.get("status") == "validated"
