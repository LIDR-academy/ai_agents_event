"""Tests for the Session 12 conductor (agentic + rag composition).

Network-free: the agent entrypoints and the deterministic ``estimate_all`` are
stubbed, so the focus is the conductor's own logic — the structure→Estimate
mapping and the HYBRID merge (deterministic first, agent recovery only on the
flagged tasks, merge back).
"""

from __future__ import annotations

import app.domain.agent_estimation as conductor
from app.domain.agent_estimation import _structure_to_estimate, agent_estimate_task_hours
from app.domain.schemas.agent_trace import AgentStep, AgentTrace
from app.generation.agentic.agent_schemas import (
    AgentModuleNode,
    AgentStructure,
    AgentTaskDerivation,
    AgentTaskHoursRun,
    AgentTaskNode,
)
from app.generation.rag.schemas import (
    TaskHoursEstimate,
    TaskHoursModuleInput,
    TaskHoursResult,
    TaskHoursTaskInput,
)


# --- structure → Estimate mapping ------------------------------------------ #
def test_structure_maps_to_ungrounded_hourless_estimate():
    structure = AgentStructure(
        modules=[
            AgentModuleNode(
                name="Auth",
                description="Access",
                tasks=[AgentTaskNode(name="OAuth backend", description="JWT")],
            )
        ],
        confidence="high",
        reasoning="Standard.",
    )
    estimate = _structure_to_estimate(structure)
    assert estimate.total_engineer_days is None
    assert estimate.confidence == "high"
    task = estimate.modules[0].tasks[0]
    assert task.engineer_days is None
    assert task.grounded is False
    assert task.sources == []


def test_empty_structure_becomes_insufficient():
    structure = AgentStructure(modules=[], confidence="low", reasoning="Too vague.")
    estimate = _structure_to_estimate(structure)
    assert estimate.confidence == "insufficient"
    assert estimate.modules == []
    assert estimate.insufficient_context_explanation == "Too vague."


# --- hybrid merge ---------------------------------------------------------- #
def _modules():
    return [
        TaskHoursModuleInput(
            name="Auth",
            tasks=[TaskHoursTaskInput(name="OAuth backend"), TaskHoursTaskInput(name="RBAC")],
        )
    ]


def _base_result() -> TaskHoursResult:
    # One well-grounded task, one with no match → only the second should be flagged.
    return TaskHoursResult(
        tasks=[
            TaskHoursEstimate(
                module="Auth", task="OAuth backend", estimated_hours=120, reliability=0.8, has_match=True
            ),
            TaskHoursEstimate(module="Auth", task="RBAC", has_match=False),
        ]
    )


async def test_hybrid_recovers_only_flagged_and_merges(monkeypatch):
    async def fake_estimate_all(modules, *, top_k=None, distance_threshold=None):
        return _base_result()

    recovery_seen: dict = {}

    async def fake_recovery(flagged, **kwargs):
        recovery_seen["flagged"] = [(t.module, t.task) for t in flagged]
        return AgentTaskHoursRun(
            derivations=[
                AgentTaskDerivation(
                    module="Auth", task="RBAC", estimated_hours=64, reliability=0.55, has_match=True
                )
            ],
            trace=AgentTrace(
                steps=[AgentStep(step=1, tool="search_budgets", tool_args={}, observation="found")]
            ),
            iterations=3,
            stopped_reason="completed",
        )

    monkeypatch.setattr(conductor, "estimate_all", fake_estimate_all)
    monkeypatch.setattr(conductor, "run_task_hours_recovery_agent", fake_recovery)

    result = await agent_estimate_task_hours(_modules(), client=object(), model="gpt-5-mini")

    # Only the no-match task was handed to the agent.
    assert recovery_seen["flagged"] == [("Auth", "RBAC")]
    by_task = {t.task: t for t in result.tasks}
    # The already-grounded task is untouched.
    assert by_task["OAuth backend"].estimated_hours == 120
    # The flagged task was overwritten with the agent's recovered hours.
    assert by_task["RBAC"].estimated_hours == 64
    assert by_task["RBAC"].has_match is True
    assert result.agent_trace.steps  # the recovery trace rode back


async def test_hybrid_skips_agent_when_nothing_flagged(monkeypatch):
    async def fake_estimate_all(modules, *, top_k=None, distance_threshold=None):
        return TaskHoursResult(
            tasks=[
                TaskHoursEstimate(
                    module="Auth", task="OAuth backend", estimated_hours=120, reliability=0.9, has_match=True
                )
            ]
        )

    called = {"recovery": False}

    async def fake_recovery(flagged, **kwargs):
        called["recovery"] = True
        return AgentTaskHoursRun(derivations=[], trace=AgentTrace(), iterations=0)

    monkeypatch.setattr(conductor, "estimate_all", fake_estimate_all)
    monkeypatch.setattr(conductor, "run_task_hours_recovery_agent", fake_recovery)

    result = await agent_estimate_task_hours(_modules(), client=object(), model="gpt-5-mini")

    assert called["recovery"] is False  # zero extra cost in the happy path
    assert result.agent_trace is not None
    assert result.agent_trace.steps == []  # empty-step trace = "no recovery needed"


async def test_hybrid_keeps_deterministic_when_agent_finds_nothing(monkeypatch):
    async def fake_estimate_all(modules, *, top_k=None, distance_threshold=None):
        return _base_result()

    async def fake_recovery(flagged, **kwargs):
        # The agent searched but grounded nothing.
        return AgentTaskHoursRun(
            derivations=[AgentTaskDerivation(module="Auth", task="RBAC", has_match=False)],
            trace=AgentTrace(),
            iterations=2,
            stopped_reason="completed",
        )

    monkeypatch.setattr(conductor, "estimate_all", fake_estimate_all)
    monkeypatch.setattr(conductor, "run_task_hours_recovery_agent", fake_recovery)

    result = await agent_estimate_task_hours(_modules(), client=object(), model="gpt-5-mini")
    by_task = {t.task: t for t in result.tasks}
    assert by_task["RBAC"].has_match is False  # unchanged; no fabricated hours
    assert by_task["RBAC"].estimated_hours is None
