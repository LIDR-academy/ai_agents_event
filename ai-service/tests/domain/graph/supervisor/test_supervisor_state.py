"""The supervisor state: inherited reducers + the two new keyed accumulators.

The point of these assertions is that ``SupervisorState`` really is Session 13's state
EXTENDED — the parent's ``operator.add`` channels survive subclassing — and that the
new audit/routing accumulators are IDEMPOTENT, which is what keeps them honest across
a human pause.
"""

from __future__ import annotations

import operator
import typing

from langgraph.channels import BinaryOperatorAggregate
from langgraph.graph import END, START, StateGraph

from app.domain.graph.supervisor.state import (
    SupervisorState,
    append_contributions,
    append_routing,
    privilege_violations,
)


def _channels():
    builder = StateGraph(SupervisorState)
    builder.add_node("noop", lambda state: {})
    builder.add_edge(START, "noop")
    builder.add_edge("noop", END)
    return builder.compile().channels


def test_inherits_session_13_accumulators():
    """Subclassing must not lose the parent's reducers."""
    hints = typing.get_type_hints(SupervisorState, include_extras=True)
    assert operator.add in getattr(hints["budget_matches"], "__metadata__", ())
    assert operator.add in getattr(hints["errors"], "__metadata__", ())


def test_new_accumulators_use_the_keyed_reducers():
    hints = typing.get_type_hints(SupervisorState, include_extras=True)
    assert append_contributions in getattr(hints["agent_contributions"], "__metadata__", ())
    assert append_routing in getattr(hints["routing_history"], "__metadata__", ())
    # Explicitly NOT operator.add: a concat trail would duplicate across a resume.
    assert operator.add not in getattr(hints["agent_contributions"], "__metadata__", ())


def test_step_counter_has_no_reducer():
    """``supervisor_steps`` has one writer; a reducer would break the step budget."""
    hints = typing.get_type_hints(SupervisorState, include_extras=True)
    assert getattr(hints["supervisor_steps"], "__metadata__", ()) == ()


def test_accumulators_compile_to_reducer_channels():
    channels = _channels()
    assert isinstance(channels["agent_contributions"], BinaryOperatorAggregate)
    assert isinstance(channels["routing_history"], BinaryOperatorAggregate)
    assert isinstance(channels["budget_matches"], BinaryOperatorAggregate)


def test_append_contributions_accumulates_distinct_rows():
    first = [{"step": 0, "agent": "budget_searcher", "action": "tool:search_budgets"}]
    second = [{"step": 1, "agent": "coherence_validator", "action": "tool:validate_estimate"}]
    assert len(append_contributions(first, second)) == 2


def test_append_contributions_is_idempotent_on_the_same_key():
    """Re-emitting the same (step, agent, action) REPLACES — the resume guarantee."""
    row = {"step": 0, "agent": "budget_searcher", "action": "tool:search_budgets", "outcome": "ok"}
    merged = append_contributions([row], [row])
    assert len(merged) == 1
    # A re-run that adds detail merges into the existing row rather than appending.
    merged = append_contributions([row], [{**row, "duration_ms": 42}])
    assert len(merged) == 1 and merged[0]["duration_ms"] == 42


def test_repeated_tool_calls_in_one_step_are_kept_apart():
    """One agent calls one tool once per component — those are distinct rows.

    Regression: keying only on ``(step, agent, action)`` made the second search
    REPLACE the first, so a two-component run showed one search in the audit trail.
    """
    rows = [
        {
            "step": 1,
            "agent": "budget_searcher",
            "action": "tool:search_budgets",
            "args_digest": "aaaaaaaaaaaa",
            "summary": "3 analogs for API",
        },
        {
            "step": 1,
            "agent": "budget_searcher",
            "action": "tool:search_budgets",
            "args_digest": "bbbbbbbbbbbb",
            "summary": "0 analogs for App",
        },
    ]
    assert len(append_contributions([], rows)) == 2
    # Still idempotent: the SAME call re-executed on a resume collapses.
    assert len(append_contributions(rows, [rows[0]])) == 2


def test_append_routing_keys_on_step():
    first = [{"step": 0, "next_agent": "requirements_extractor", "source": "llm"}]
    again = [{"step": 0, "next_agent": "requirements_extractor", "source": "fallback"}]
    merged = append_routing(first, again)
    assert len(merged) == 1 and merged[0]["source"] == "fallback"


def test_privilege_violations_filters_denied_rows():
    state = {
        "agent_contributions": [
            {"agent": "budget_searcher", "outcome": "ok"},
            {"agent": "budget_searcher", "outcome": "denied", "tool": "validate_estimate"},
            {"agent": "coherence_validator", "outcome": "error"},
        ]
    }
    violations = privilege_violations(state)
    assert len(violations) == 1
    assert violations[0]["tool"] == "validate_estimate"
