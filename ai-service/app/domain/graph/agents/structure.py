"""``structure_agent`` — proposes the module → task breakdown.

Reuses the Session 12 phase-1 agent verbatim (``run_structure_agent``): a single
Responses-API call over the reformulated brief that returns an ``AgentStructure``
(modules → tasks, no hours, no sources). The classifier's ``complexity`` is mapped to
the agent's reasoning effort, so a richer transcript gets more thinking budget.

The gpt-5 agent is native ``async`` (it awaits ``client.responses.parse``), so it is
awaited directly — NOT wrapped in ``asyncio.to_thread`` (that is only for the sync
``LLMWrapper`` path used by the smaller structured-output agents).
"""

from __future__ import annotations

import logfire
import structlog

# Module-level import so the offline runner / tests can monkeypatch it.
from app.generation.agentic.agent_loop import run_structure_agent

from app.config import get_settings
from app.domain.graph.personas import persona_for

log = structlog.get_logger()


def _effort_for_complexity(complexity: str | None) -> str:
    """Map the classifier's complexity to a reasoning effort (data, not a branch)."""
    settings = get_settings()
    mapping = settings.GRAPH_STRUCTURE_EFFORT_BY_COMPLEXITY
    return mapping.get(complexity or "medium", settings.AGENT_REASONING_EFFORT)


async def structure_agent(state: dict) -> dict:
    """Reformulated brief → module→task structure (reuses the S12 structure agent)."""
    with logfire.span("node: structure_agent"):
        settings = get_settings()
        from app.dependencies import get_async_openai_client

        client = get_async_openai_client()
        if client is None:
            raise RuntimeError("Async OpenAI client is not available (no OpenAI key).")

        brief = state.get("reformulated_transcript") or state.get("transcript") or ""
        effort = _effort_for_complexity(state.get("complexity"))
        structure, _trace = await run_structure_agent(
            brief,
            client=client,
            model=settings.AGENT_MODEL,
            reasoning_effort=effort,
            persona=persona_for("structure_agent", enabled=settings.GRAPH_PERSONAS_ENABLED),
        )
        task_count = sum(len(m.tasks) for m in structure.modules)
        log.info(
            "agent_structure_node_done",
            modules=len(structure.modules),
            tasks=task_count,
            effort=effort,
        )
        return {"structure": structure.model_dump()}
