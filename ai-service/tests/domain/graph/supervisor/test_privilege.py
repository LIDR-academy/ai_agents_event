"""Minimum privilege + the audit trail (Level 3).

The load-bearing assertion in this file is ``test_denied_call_never_reaches_dispatch``:
rejection has to happen BEFORE execution, not be discovered afterwards. Everything else
is about the trail being complete enough to reconstruct a run from the log.
"""

from __future__ import annotations

import pytest

from structlog.testing import capture_logs

from app.domain.graph.supervisor import privilege
from app.domain.graph.supervisor.privilege import (
    AGENT_PRIVILEGES,
    CALCULATE_TOOL,
    PrivilegeViolation,
    allowed_tools,
    assert_allowed,
    guarded_dispatch,
    record_model_action,
)


def test_privilege_table_matches_the_exercise():
    assert AGENT_PRIVILEGES["requirements_extractor"] == frozenset()
    assert AGENT_PRIVILEGES["supervisor"] == frozenset()
    assert AGENT_PRIVILEGES["budget_searcher"] == frozenset({"search_budgets"})
    assert AGENT_PRIVILEGES["estimate_generator"] == frozenset({CALCULATE_TOOL})
    assert AGENT_PRIVILEGES["coherence_validator"] == frozenset({"validate_estimate"})


def test_every_agent_sees_at_most_one_tool():
    """The accuracy argument: a one-option decision space cannot be got wrong."""
    assert all(len(tools) <= 1 for tools in AGENT_PRIVILEGES.values())


def test_unknown_agent_has_no_privilege():
    assert allowed_tools("some_new_agent") == frozenset()


def test_assert_allowed_raises_with_a_useful_message():
    with pytest.raises(PrivilegeViolation) as excinfo:
        assert_allowed("budget_searcher", "validate_estimate")
    message = str(excinfo.value)
    assert "budget_searcher" in message and "validate_estimate" in message
    assert "search_budgets" in message  # what it IS allowed


def test_requirements_extractor_may_call_nothing():
    for tool in ("search_budgets", CALCULATE_TOOL, "validate_estimate"):
        with pytest.raises(PrivilegeViolation):
            assert_allowed("requirements_extractor", tool)


@pytest.mark.asyncio
async def test_denied_call_never_reaches_dispatch(monkeypatch):
    """Rejection happens BEFORE execution — the whole point of a privilege check."""
    calls: list[tuple] = []

    async def _spy(name, args, **kwargs):
        calls.append((name, args))
        return {"summary": "should never run"}

    monkeypatch.setattr(privilege, "dispatch_tool", _spy)

    result, contribution = await guarded_dispatch(
        "budget_searcher", "validate_estimate", {"components": [], "total_hours": 0}, step=3
    )

    assert calls == []  # the tool was never executed
    assert result["ok"] is False and result["error"] == "privilege_denied"
    assert contribution["outcome"] == "denied"
    assert contribution["agent"] == "budget_searcher"
    assert contribution["tool"] == "validate_estimate"
    assert contribution["step"] == 3


@pytest.mark.asyncio
async def test_denial_is_logged_at_error_level(monkeypatch):
    monkeypatch.setattr(privilege, "dispatch_tool", None)  # must not be called
    with capture_logs() as logs:
        await guarded_dispatch("requirements_extractor", "search_budgets", {"query": "x"}, step=1)
    denied = [entry for entry in logs if entry["event"] == "agent_privilege_denied"]
    assert len(denied) == 1
    assert denied[0]["log_level"] == "error"
    assert denied[0]["agent"] == "requirements_extractor"
    assert denied[0]["tool"] == "search_budgets"
    assert denied[0]["allowed"] == []


@pytest.mark.asyncio
async def test_strict_mode_raises_instead_of_returning_an_envelope(monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("SUPERVISOR_PRIVILEGE_STRICT", "true")
    try:
        with pytest.raises(PrivilegeViolation):
            await guarded_dispatch("budget_searcher", "validate_estimate", {}, step=0)
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_allowed_call_executes_and_audits(monkeypatch):
    seen: list[tuple] = []

    async def _spy(name, args, **kwargs):
        seen.append((name, args))
        return {"items": [], "count": 0, "summary": "3 analogs found"}

    monkeypatch.setattr(privilege, "dispatch_tool", _spy)

    with capture_logs() as logs:
        result, contribution = await guarded_dispatch(
            "budget_searcher", "search_budgets", {"query": "API"}, step=2, estimation_id="e1"
        )

    assert seen == [("search_budgets", {"query": "API"})]
    assert result["summary"] == "3 analogs found"
    assert contribution["outcome"] == "ok"
    assert len(contribution["args_digest"]) == 12
    assert contribution["duration_ms"] is not None

    actions = [entry for entry in logs if entry["event"] == "agent_action"]
    assert len(actions) == 1
    assert actions[0]["estimation_id"] == "e1"
    assert actions[0]["result_summary"] == "3 analogs found"


@pytest.mark.asyncio
async def test_same_args_produce_the_same_digest(monkeypatch):
    async def _ok(name, args, **kwargs):
        return {"summary": "ok"}

    monkeypatch.setattr(privilege, "dispatch_tool", _ok)
    _, first = await guarded_dispatch("budget_searcher", "search_budgets", {"query": "A"}, step=0)
    _, second = await guarded_dispatch("budget_searcher", "search_budgets", {"query": "A"}, step=1)
    _, other = await guarded_dispatch("budget_searcher", "search_budgets", {"query": "B"}, step=2)
    assert first["args_digest"] == second["args_digest"] != other["args_digest"]


@pytest.mark.asyncio
async def test_a_throwing_tool_becomes_an_error_row_not_a_crash(monkeypatch):
    async def _boom(name, args, **kwargs):
        raise RuntimeError("retrieval exploded")

    monkeypatch.setattr(privilege, "dispatch_tool", _boom)
    result, contribution = await guarded_dispatch(
        "budget_searcher", "search_budgets", {"query": "A"}, step=0
    )
    assert result["ok"] is False and result["error"] == "RuntimeError"
    assert contribution["outcome"] == "error"


def test_model_only_actions_appear_in_the_trail():
    """A tool-free agent must still be visible, or the trail has holes."""
    with capture_logs() as logs:
        contribution = record_model_action(
            "requirements_extractor", "extract_requirements", step=0, summary="7 requirements"
        )
    assert contribution["tool"] is None
    assert contribution["outcome"] == "ok"
    assert contribution["summary"] == "7 requirements"
    assert [entry["event"] for entry in logs] == ["agent_action"]
