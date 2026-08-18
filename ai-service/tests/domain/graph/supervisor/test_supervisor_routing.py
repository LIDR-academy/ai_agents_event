"""The hand-built router: model-driven, but caged.

These tests pin the three brakes that make an LLM router safe in a cyclic graph — the
step budget, the legality guard and the deterministic fallback — and prove that every
decision lands in the state where a trace reader can find it.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from app.domain.graph.supervisor.supervisor import (
    _fallback_next,
    _is_legal,
    supervisor,
)

from .conftest import FakeWrapper, wire

_TRANSCRIPT = "A" * 200


def _state(**overrides) -> dict:
    base = {"transcript": _TRANSCRIPT, "estimation_id": "e1", "supervisor_steps": 0}
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# The deterministic ladder                                                     #
# --------------------------------------------------------------------------- #
def _ran(*agents) -> list[dict]:
    """A routing history in which ``agents`` have already been dispatched."""
    return [{"step": i, "next_agent": a, "source": "llm"} for i, a in enumerate(agents)]


def test_fallback_walks_the_dependency_ladder():
    state = _state()
    assert _fallback_next(state) == "requirements_extractor"

    state["routing_history"] = _ran("requirements_extractor")
    state["components"] = [{"name": "API", "category": "backend"}]
    assert _fallback_next(state) == "budget_searcher"

    state["routing_history"] = _ran("requirements_extractor", "budget_searcher")
    state["budget_matches"] = [
        {"component": "API", "reference_budget_id": "B1", "amount": 80.0, "distance": 0.1}
    ]
    assert _fallback_next(state) == "estimate_generator"

    state["routing_history"] = _ran(
        "requirements_extractor", "budget_searcher", "estimate_generator"
    )
    state["estimate"] = {"components": [], "total_engineer_days": 10}
    assert _fallback_next(state) == "coherence_validator"

    state["routing_history"] = _ran(
        "requirements_extractor", "budget_searcher", "estimate_generator", "coherence_validator"
    )
    state["validation"] = {"ok": True, "issues": []}
    assert _fallback_next(state) == "finish"


def test_legality_rejects_unmet_preconditions():
    state = _state()
    assert not _is_legal("budget_searcher", state)  # no components yet
    assert not _is_legal("estimate_generator", state)
    assert _is_legal("requirements_extractor", state)


def test_legality_rejects_an_agent_that_already_acted():
    """Completion is 'did it act', not 'did it produce' — see ``_already_ran``."""
    state = _state(
        components=[{"name": "API", "category": "backend"}],
        routing_history=_ran("requirements_extractor"),
    )
    assert not _is_legal("requirements_extractor", state)
    assert _is_legal("budget_searcher", state)


def test_an_empty_search_result_does_not_loop_the_router():
    """A search that legitimately finds nothing must still count as done.

    Regression: keying completion on ``budget_matches`` being non-empty sent the
    router back to ``budget_searcher`` forever whenever a project had no precedent —
    burning the whole step budget on the exact case the human gate exists to catch.
    """
    state = _state(
        components=[{"name": "API", "category": "backend"}],
        budget_matches=[],  # searched, found nothing
        routing_history=_ran("requirements_extractor", "budget_searcher"),
    )
    assert not _is_legal("budget_searcher", state)
    assert _fallback_next(state) == "estimate_generator"


def test_finishing_is_always_legal():
    """The gate downstream owns the 'is this good enough' question, not the router."""
    assert _is_legal("finish", _state())


# --------------------------------------------------------------------------- #
# The node                                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_model_choice_is_recorded_in_the_state(monkeypatch):
    """Routing that is not in the state is routing nobody can audit."""
    wrapper = FakeWrapper(route_script=["requirements_extractor"])
    wire(monkeypatch, wrapper=wrapper)

    command = await supervisor(_state())

    assert command.goto == "requirements_extractor"
    assert command.update["next_agent"] == "requirements_extractor"
    assert command.update["supervisor_steps"] == 1
    row = command.update["routing_history"][0]
    assert row["step"] == 0
    assert row["source"] == "llm"
    assert "scripted route to requirements_extractor" in row["reason"]


@pytest.mark.asyncio
async def test_finish_routes_to_the_gate_not_to_end(monkeypatch):
    """'finish' means 'hand to the gate' — the gate decides whether to pause."""
    wrapper = FakeWrapper(route_script=["finish"])
    wire(monkeypatch, wrapper=wrapper)

    command = await supervisor(_state(validation={"ok": True}))

    assert command.goto == "human_review_gate"
    assert command.update["next_agent"] == "finish"


@pytest.mark.asyncio
async def test_an_illegal_model_choice_is_overridden(monkeypatch):
    """The model asks for an agent whose inputs do not exist; the guard corrects it."""
    wrapper = FakeWrapper(route_script=["estimate_generator"])  # no budget_matches yet
    wire(monkeypatch, wrapper=wrapper)

    with capture_logs() as logs:
        command = await supervisor(_state())

    assert command.goto == "requirements_extractor"
    row = command.update["routing_history"][0]
    assert row["source"] == "fallback"
    assert "estimate_generator" in row["reason"]  # the override is explained
    assert any(entry["event"] == "supervisor_route_overridden" for entry in logs)


@pytest.mark.asyncio
async def test_router_outage_falls_back_and_the_graph_still_moves(monkeypatch):
    wrapper = FakeWrapper(route_error=RuntimeError("router down"))
    wire(monkeypatch, wrapper=wrapper)

    command = await supervisor(_state())

    assert command.goto == "requirements_extractor"
    row = command.update["routing_history"][0]
    assert row["source"] == "fallback"
    assert "RuntimeError" in row["reason"]


@pytest.mark.asyncio
async def test_step_budget_forces_a_finish(monkeypatch):
    """The hard ceiling on routing loops."""
    from app.config import get_settings

    wrapper = FakeWrapper(route_script=["requirements_extractor"])
    wire(monkeypatch, wrapper=wrapper)
    limit = get_settings().SUPERVISOR_MAX_STEPS

    command = await supervisor(_state(supervisor_steps=limit))

    assert command.goto == "human_review_gate"
    row = command.update["routing_history"][0]
    assert row["source"] == "limit"
    # The model was never consulted once the budget was gone.
    assert "SupervisorDecision" not in wrapper.calls
