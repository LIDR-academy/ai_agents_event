"""``hours_retrieval_agent`` — per-task hours (fan-out) + agentic recovery (join).

Two nodes cooperate:

* ``estimate_task_hours`` is the FAN-OUT BRANCH. The graph dispatches one ``Send``
  per approved task, so many copies of this node run IN PARALLEL, each doing a single
  deterministic vector search over the historical-task corpus (reuses the Session 10
  ``estimate_one``). Each branch returns a one-element ``task_hours`` list; the keyed
  ``merge_task_hours`` reducer accumulates them idempotently.

* ``recover_and_handover`` is the JOIN, run once after every branch settles. It flags
  the doubtful tasks (no match / contradictory / low reliability) and, only if any,
  runs the Session 12 agentic recovery loop ONCE over the batch (reason→act→observe:
  reformulate the query, review analogs, decide) — this is where the agent REASONS
  instead of doing a single lookup. It merges the recovered hours, builds the
  structured estimate and performs the explicit HANDOVER to ``analysis_agent`` via
  ``Command(goto=..., update=...)``.

Keeping recovery in the join (one gpt-5 loop) rather than per-branch bounds gpt-5
concurrency and mirrors ``app/domain/agent_estimation.py::agent_estimate_task_hours``.
All reusable primitives are imported at module level so tests/offline runs can swap
them.
"""

from __future__ import annotations

import logfire
import structlog
from langgraph.types import Command

# Module-level imports = the monkeypatch seam for tests and the offline runner.
from app.generation.agentic.agent_loop import run_task_hours_recovery_agent
from app.generation.rag.agent_retrieval import make_retrieval_backend
from app.generation.rag.task_hours import distance_weighted_consensus, estimate_one

from app.config import get_settings
from app.domain.graph.agents._common import build_estimate, flag_reason
from app.domain.graph.personas import persona_for
from app.generation.agentic.agent_schemas import AgentTaskRef

log = structlog.get_logger()


async def estimate_task_hours(state: dict) -> dict:
    """FAN-OUT BRANCH: derive hours for ONE task (the ``Send`` arg is the state).

    ``state`` here is the ``Send`` argument ``{"module", "task", "description"}`` —
    NOT the whole graph state. A deterministic, embeddings-only search; no LLM.

    ``top_k``/``distance_threshold`` ride in that payload when ``fan_out_hours``
    resolved them from the runtime config; the settings defaults are the fallback, so
    a ``Send`` built without them (the offline runner, the tests) behaves as before.
    """
    with logfire.span("node: estimate_task_hours"):
        settings = get_settings()
        module = state["module"]
        task = state["task"]
        description = state.get("description")
        top_k = state.get("top_k") or settings.TASK_HOURS_TOP_K
        distance_threshold = state.get("distance_threshold")
        if distance_threshold is None:
            distance_threshold = settings.TASK_HOURS_DISTANCE_THRESHOLD
        est = await estimate_one(
            module,
            task,
            description,
            top_k=top_k,
            distance_threshold=distance_threshold,
        )
        log.info(
            "estimate_task_hours_branch",
            module=module,
            task=task,
            has_match=est.has_match,
            hours=est.estimated_hours,
            top_k=top_k,
            distance_threshold=distance_threshold,
        )
        # One-element list — the keyed reducer accumulates it into task_hours.
        return {"task_hours": [est.model_dump()]}


async def recover_and_handover(state: dict) -> Command:
    """JOIN: agentic recovery of doubtful tasks, build the estimate, hand over.

    Reads the full ``task_hours`` accumulator, flags the doubtful rows, runs one
    recovery loop over them (if any + a client is available), merges the recovered
    hours, assembles the estimate and hands over to ``analysis_agent``.
    """
    with logfire.span("node: recover_and_handover"):
        settings = get_settings()
        approved = state.get("approved_modules") or []
        task_hours = list(state.get("task_hours") or [])
        by_key = {(t.get("module"), t.get("task")): t for t in task_hours}
        descriptions = {
            (m.get("name"), t.get("name")): t.get("description")
            for m in approved
            for t in (m.get("tasks") or [])
        }

        flagged: list[AgentTaskRef] = []
        for row in task_hours:
            reason = flag_reason(row)
            if reason is None:
                continue
            flagged.append(
                AgentTaskRef(
                    module=row.get("module"),
                    task=row.get("task"),
                    description=descriptions.get((row.get("module"), row.get("task"))),
                    reason=reason,
                )
            )

        from app.dependencies import get_async_openai_client, get_runtime_retrieval_config

        client = get_async_openai_client()
        merged = task_hours
        recovered_count = 0
        # THE knob that decides how many tasks finish with no hours at all. The fan-out cut
        # only decides who is handed to this agent; this one decides who it can rescue, and
        # with a large corpus a loose value here means nothing is ever left unmatched. Read
        # from the runtime config so it can be calibrated against the structure of the day
        # during the gate-1 pause — this join runs ONCE per run, so unlike the fan-out
        # branch there is no per-task Redis traffic and no risk of a mid-flight change
        # splitting the run across two thresholds.
        agent_distance_threshold = (
            get_runtime_retrieval_config().effective_agent_search_distance_threshold()
        )
        stopped_reason = None
        iterations = 0
        if flagged and client is not None:
            log.info(
                "agentic_recovery_start",
                flagged=len(flagged),
                total=len(task_hours),
                distance_threshold=agent_distance_threshold,
            )
            run = await run_task_hours_recovery_agent(
                flagged,
                client=client,
                model=settings.AGENT_MODEL,
                reasoning_effort=settings.AGENT_REASONING_EFFORT,
                max_iterations=settings.AGENT_MAX_ITERATIONS,
                retrieval_backend=make_retrieval_backend(
                    settings.AGENT_SEARCH_TOP_K, agent_distance_threshold
                ),
                consensus_fn=distance_weighted_consensus,
                persona=persona_for("recover_and_handover", enabled=settings.GRAPH_PERSONAS_ENABLED),
            )
            recovered = {
                (d.module, d.task): d
                for d in run.derivations
                if d.has_match and d.estimated_hours is not None
            }
            recovered_count = len(recovered)
            merged_map = dict(by_key)
            for key, d in recovered.items():
                base = merged_map.get(key, {"module": key[0], "task": key[1]})
                merged_map[key] = {
                    **base,
                    "estimated_hours": d.estimated_hours,
                    "reliability": d.reliability,
                    "has_match": True,
                    # The agent recovered a point estimate; drop any stale range.
                    "hours_range": None,
                }
            merged = list(merged_map.values())
            stopped_reason = run.stopped_reason
            iterations = run.iterations

        estimate = build_estimate(approved, merged)
        log.info(
            "recover_and_handover_done",
            flagged=len(flagged),
            recovered=recovered_count,
            # Without these three, a recovery truncated by the iteration budget is
            # indistinguishable from one that genuinely found no analog — and the whole
            # point of the unmatched rows is that they mean "no precedent", not "ran out
            # of turns". ``max_iterations`` here invalidates the coverage number.
            stopped_reason=stopped_reason,
            iterations=iterations,
            distance_threshold=agent_distance_threshold,
            total_engineer_days=estimate.get("total_engineer_days"),
        )
        # Explicit handover: pass the estimate + merged hours to analysis_agent.
        return Command(
            goto="analysis_agent",
            update={"estimate": estimate, "task_hours": merged},
        )
