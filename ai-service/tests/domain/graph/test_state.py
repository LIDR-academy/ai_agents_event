"""The typed state's accumulator reducers (Level 1).

``budget_matches`` and ``errors`` are annotated with ``operator.add`` so LangGraph
CONCATENATES partial updates instead of overwriting. ``task_hours`` uses the KEYED
``merge_task_hours`` reducer instead — idempotent by ``(module, task)`` so a resume
that re-enters the fan-out cannot duplicate rows. We assert those on the annotation
metadata, the compiled channel behaviour and the reducer's own semantics (no LLM).
"""

from __future__ import annotations

import operator
import typing

from langgraph.channels import BinaryOperatorAggregate
from langgraph.graph import END, START, StateGraph

from app.domain.graph.state import EstimationState, merge_task_hours


def _channels():
    """Channel spec of a minimal compiled graph over the state."""
    builder = StateGraph(EstimationState)
    builder.add_node("noop", lambda state: {})
    builder.add_edge(START, "noop")
    builder.add_edge("noop", END)
    return builder.compile().channels


def test_accumulator_fields_are_annotated_with_operator_add():
    hints = typing.get_type_hints(EstimationState, include_extras=True)
    assert operator.add in getattr(hints["budget_matches"], "__metadata__", ())
    assert operator.add in getattr(hints["errors"], "__metadata__", ())
    assert getattr(hints["requirements"], "__metadata__", ()) == ()


def test_accumulator_fields_compile_to_a_reducer_channel():
    channels = _channels()
    assert isinstance(channels["budget_matches"], BinaryOperatorAggregate)
    assert isinstance(channels["errors"], BinaryOperatorAggregate)
    assert not isinstance(channels["requirements"], BinaryOperatorAggregate)


def test_reducer_concatenates_partial_updates():
    channel = _channels()["budget_matches"].copy()
    channel.update(
        [[{"component": "A", "reference_budget_id": "b1", "amount": 80.0, "distance": 0.1}]]
    )
    channel.update(
        [[{"component": "B", "reference_budget_id": "b2", "amount": 40.0, "distance": 0.2}]]
    )
    assert [m["component"] for m in channel.get()] == ["A", "B"]


def test_errors_reducer_appends_without_clobbering():
    channel = _channels()["errors"].copy()
    channel.update([["first issue"]])
    channel.update([["second issue"]])
    assert channel.get() == ["first issue", "second issue"]


def test_task_hours_reducer_is_keyed_not_operator_add():
    hints = typing.get_type_hints(EstimationState, include_extras=True)
    metadata = getattr(hints["task_hours"], "__metadata__", ())
    # Keyed reducer, NOT operator.add (that would double-append on a resume).
    assert merge_task_hours in metadata
    assert operator.add not in metadata


def test_merge_task_hours_dedupes_by_module_and_task():
    existing = [
        {"module": "Backend", "task": "API", "estimated_hours": 40},
        {"module": "Backend", "task": "Auth", "estimated_hours": 20},
    ]
    # Re-emitting "API" (e.g. after agentic recovery) REPLACES, never appends.
    new = [{"module": "Backend", "task": "API", "estimated_hours": 64, "has_match": True}]
    merged = merge_task_hours(existing, new)
    by_task = {(t["module"], t["task"]): t["estimated_hours"] for t in merged}
    assert by_task == {("Backend", "API"): 64, ("Backend", "Auth"): 20}
    assert len(merged) == 2  # idempotent: no duplicate row for API


def test_merge_task_hours_handles_empty_sides():
    assert merge_task_hours(None, [{"module": "M", "task": "T", "estimated_hours": 8}]) == [
        {"module": "M", "task": "T", "estimated_hours": 8}
    ]
    assert merge_task_hours([{"module": "M", "task": "T"}], None) == [{"module": "M", "task": "T"}]
