"""Conductor for the Session 12 agent driving the two wizard phases.

This is the ONE place the ``agentic`` and ``rag`` generation siblings meet, so it
lives in ``domain`` (ARCHITECTURE.md §7: siblings compose only in the conductor).
It wires the raw agent loop to rag's real retrieval + consensus:

* :func:`agent_propose_structure` — phase 1. Runs ``run_structure_agent`` and maps
  its rag-free ``AgentStructure`` onto the heavy RAG ``Estimate`` the wizard
  renders (``engineer_days=None`` / ``grounded=False`` — hours come later).
* :func:`agent_estimate_task_hours` — phase 2, HYBRID. Runs the deterministic
  per-task consensus first, then hands ONLY the tasks it could not ground to the
  agent's recovery loop, and merges what the agent recovers back in.

The async OpenAI client is injected by the router (never imported here) — the
conductor stays out of the composition root.
"""

from __future__ import annotations

import structlog

from app.domain.schemas.agent_trace import AgentTrace
from app.generation.agentic.agent_loop import (
    run_structure_agent,
    run_task_hours_recovery_agent,
)
from app.generation.agentic.agent_schemas import AgentStructure, AgentTaskRef
from app.generation.rag.agent_retrieval import make_retrieval_backend
from app.generation.rag.prompt_builder import build_structure_user_message
from app.generation.rag.schemas import (
    Estimate,
    EstimationQuery,
    GenerateResult,
    TaskHoursEstimate,
    TaskHoursModuleInput,
    TaskHoursResult,
    TaskItem,
    WorkModule,
)
from app.generation.rag.task_hours import distance_weighted_consensus, estimate_all

log = structlog.get_logger()

# A grounded task below this reliability is worth a recovery re-search: the
# deterministic pass matched *something*, but so weakly the agent may do better.
_LOW_RELIABILITY = 0.35


def _structure_to_estimate(structure: AgentStructure) -> Estimate:
    """Map the agent's rag-free structure onto the wizard's ``Estimate`` contract.

    Structure-only: every task is ungrounded (no sources, ``engineer_days=None``),
    which the per-task hours step fills in afterwards.
    """
    modules = [
        WorkModule(
            name=m.name,
            description=m.description,
            tasks=[
                TaskItem(name=t.name, description=t.description, grounded=False, sources=[])
                for t in m.tasks
            ],
        )
        for m in structure.modules
    ]
    confidence = structure.confidence if modules else "insufficient"
    return Estimate(
        total_engineer_days=None,
        modules=modules,
        duration_weeks=None,
        sources=[],
        assumptions=[],
        confidence=confidence,
        reasoning=structure.reasoning,
        insufficient_context_explanation=None if modules else structure.reasoning,
    )


async def agent_propose_structure(
    query: EstimationQuery,
    *,
    client,
    model: str,
    reasoning_effort: str = "medium",
    persona: str | None = None,
) -> GenerateResult:
    """Phase 1 — the agent decides the module→task structure.

    Returns the same ``GenerateResult`` shape as ``/stages/structure`` (citations
    always clean) plus the agent's one-step trace, so the wizard parses it unchanged.
    """
    brief = build_structure_user_message(query)
    structure, trace = await run_structure_agent(
        brief,
        client=client,
        model=model,
        reasoning_effort=reasoning_effort,
        persona=persona,
    )
    estimate = _structure_to_estimate(structure)
    return GenerateResult(
        estimate=estimate,
        fabricated_source_ids=[],
        coherent=True,
        agent_trace=trace,
    )


def _flag_reason(est: TaskHoursEstimate) -> str | None:
    """Why (if at all) a deterministic estimate is worth handing to the agent."""
    if not est.has_match:
        return "no historical analog under the distance threshold"
    if est.hours_range is not None:
        return "historical analogs contradict (a range, not a point)"
    if est.reliability is not None and est.reliability < _LOW_RELIABILITY:
        return f"low reliability ({est.reliability})"
    return None


async def agent_estimate_task_hours(
    modules: list[TaskHoursModuleInput],
    *,
    client,
    model: str,
    reasoning_effort: str = "medium",
    max_iterations: int = 10,
    top_k: int | None = None,
    distance_threshold: float | None = None,
    persona: str | None = None,
) -> TaskHoursResult:
    """Phase 2 — deterministic per-task hours, then agent recovery on the flagged ones.

    (1) Run the Session 10 deterministic consensus over every task. (2) Select the
    tasks it could not ground (no match / contradiction / low reliability). (3) If
    any, run the agent's recovery loop over just those, reusing the SAME consensus
    math. (4) Merge the recovered hours over the deterministic result. The agent
    is never invoked when nothing needs recovery — zero extra cost in the happy path.
    """
    base = await estimate_all(modules, top_k=top_k, distance_threshold=distance_threshold)

    flagged: list[AgentTaskRef] = []
    reasons: dict[tuple[str, str], str] = {}
    descriptions = {
        (m.name, t.name): t.description for m in modules for t in m.tasks
    }
    for est in base.tasks:
        reason = _flag_reason(est)
        if reason is None:
            continue
        key = (est.module, est.task)
        reasons[key] = reason
        flagged.append(
            AgentTaskRef(
                module=est.module,
                task=est.task,
                description=descriptions.get(key),
                reason=reason,
            )
        )

    if not flagged or client is None:
        # Nothing to recover (or no client): return the deterministic result with an
        # empty-step trace so the wizard can say "no recovery was needed".
        return TaskHoursResult(tasks=base.tasks, agent_trace=AgentTrace())

    log.info("agent_hours_recovery_flagged", flagged=len(flagged), total=len(base.tasks))
    run = await run_task_hours_recovery_agent(
        flagged,
        client=client,
        model=model,
        reasoning_effort=reasoning_effort,
        max_iterations=max_iterations,
        retrieval_backend=make_retrieval_backend(top_k, distance_threshold),
        consensus_fn=distance_weighted_consensus,
        persona=persona,
    )

    # Merge: overwrite only the tasks the agent actually grounded.
    recovered = {
        (d.module, d.task): d for d in run.derivations if d.has_match and d.estimated_hours is not None
    }
    merged: list[TaskHoursEstimate] = []
    for est in base.tasks:
        d = recovered.get((est.module, est.task))
        if d is None:
            merged.append(est)
            continue
        merged.append(
            est.model_copy(
                update={
                    "estimated_hours": d.estimated_hours,
                    "reliability": d.reliability,
                    "has_match": True,
                    # The agent recovered a point estimate; drop the stale range.
                    "hours_range": None,
                }
            )
        )

    log.info(
        "agent_hours_recovery_merged",
        flagged=len(flagged),
        recovered=len(recovered),
        stopped_reason=run.stopped_reason,
    )
    return TaskHoursResult(tasks=merged, agent_trace=run.trace)
