"""The agent's tools: flat Responses schemas + Python implementations.

Session 12 reshaped the agent to drive the two wizard phases. Phase 1
(structure) uses NO tools. Phase 2 (hours recovery) uses these three:

* ``search_budgets`` — WRAPS the Session 9/10 retrieval pipeline (via an injected
  backend); it does NOT reimplement retrieval. The backend is injectable so a
  student stub (``exercises/session-12/reference_retrieval.py``) can stand in when
  the DB is not up.
* ``derive_task_hours`` — deterministic, non-LLM: it runs the SAME distance-
  weighted consensus as the Session 10 per-task path (injected ``consensus_fn``)
  over the analogs the agent gathered from ``search_budgets``. The agent decides
  the search; the arithmetic stays deterministic.
* ``validate_estimate`` — optional S4-style guardrails over the recovered hours.

Layering: this module holds only STRUCTURAL callable types for its two injected
dependencies (``RetrievalBackend`` / ``ConsensusFn``) — it imports neither ``rag``
nor ``dependencies``. The conductor wires the concrete rag implementations in.

Schema shape matters: the Responses API uses a **flat** function schema
(``{"type": "function", "name": ..., "parameters": {...}}``), NOT the Chat
Completions shape that nests everything under a ``"function"`` key. Every schema
is ``strict: true``, which forces: every property listed in ``required`` (model
optionality via nullable unions, e.g. ``["object", "null"]``) and
``additionalProperties: false`` at *every* object level.

The tool descriptions are the ONLY thing the model reads to decide when to use a
tool — they are written for a model that never sees this code. Optimising them is
the live-session exercise.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog

from app.generation.agentic.agent_schemas import (
    DeriveTaskHoursArgs,
    SearchBudgetsArgs,
    ValidateEstimateArgs,
)

log = structlog.get_logger()

# A retrieval backend: ``(query, sectors) -> list[dict]``. Structural on purpose
# so this module never imports rag; the conductor injects rag's concrete closure.
RetrievalBackend = Callable[[str, list[str] | None], Awaitable[list[dict[str, Any]]]]

# A consensus function: ``[(hours, distance), ...] -> (hours, reliability, dispersion)``.
# The conductor injects rag's ``distance_weighted_consensus`` — the SAME math the
# deterministic per-task path uses.
ConsensusFn = Callable[[list[tuple[int, float]]], tuple[int, float, float]]


# --------------------------------------------------------------------------- #
# Flat Responses tool schemas                                                 #
# --------------------------------------------------------------------------- #
SEARCH_BUDGETS_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "search_budgets",
    "description": (
        "Search historical project tasks for work analogous to ONE task you are "
        "trying to cost, and return the matching items with their recorded effort "
        "in engineer-hours. Call this once per task you need to ground. Use a "
        "focused, task-specific query — reformulate it (different wording, "
        "synonyms, or drop/relax a filter) if the first search finds nothing. "
        "Returns a list of historical items, each with an id, a text preview, its "
        "sector, its recorded engineer-hours and its distance; pass those "
        "(hours + distance) as the neighbors for derive_task_hours."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Natural-language description of the single task to find "
                    "analogs for, e.g. 'OAuth2 authentication backend with JWT and "
                    "multi-tenant token isolation'."
                ),
            },
            "filters": {
                "type": ["object", "null"],
                "description": "Optional structural filters. Pass null to search across everything.",
                "properties": {
                    "sectors": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                        "description": "Restrict to these client sectors, e.g. ['logistics'].",
                    },
                    "component_type": {
                        "type": ["string", "null"],
                        "description": "Free-text hint about the kind of component, e.g. 'mobile app'.",
                    },
                },
                "required": ["sectors", "component_type"],
                "additionalProperties": False,
            },
        },
        "required": ["query", "filters"],
        "additionalProperties": False,
    },
    "strict": True,
}

DERIVE_TASK_HOURS_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "derive_task_hours",
    "description": (
        "Deterministically derive the engineer-hours for ONE task from the "
        "historical analogs you found with search_budgets. It computes a "
        "distance-weighted consensus of the neighbours' hours (closer analogs "
        "count more) plus a 0..1 reliability score — the SAME arithmetic the "
        "standard pipeline uses. Pass the neighbours exactly as search_budgets "
        "returned them (each with its estimated_hours AND its distance). Call this "
        "once you have gathered analogs for the task. This does NOT call a model — "
        "it is pure arithmetic, so its output is reproducible. If you found no "
        "usable analog, do NOT call this with an empty list; report the task as "
        "unresolved instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "module": {"type": "string", "description": "Module the task belongs to."},
            "task": {"type": "string", "description": "Task name (echoed back)."},
            "neighbors": {
                "type": "array",
                "description": "Historical analogs from search_budgets that anchor this task.",
                "items": {
                    "type": "object",
                    "properties": {
                        "estimated_hours": {
                            "type": "integer",
                            "description": "Recorded engineer-hours for the analog.",
                        },
                        "distance": {
                            "type": "number",
                            "description": "Cosine distance from search_budgets (lower = closer).",
                        },
                        "source_id": {
                            "type": ["integer", "null"],
                            "description": "DB id of the analog chunk (the search result 'id').",
                        },
                        "budget_id": {
                            "type": ["string", "null"],
                            "description": "Traceable parent budget id, if any.",
                        },
                    },
                    "required": ["estimated_hours", "distance", "source_id", "budget_id"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["module", "task", "neighbors"],
        "additionalProperties": False,
    },
    "strict": True,
}

VALIDATE_ESTIMATE_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "validate_estimate",
    "description": (
        "Run sanity-check guardrails over the hours you recovered before finishing. "
        "It flags tasks with no historical reference, tasks whose hours are far "
        "outside the range of their references, a total that does not match the sum "
        "of the tasks, and non-positive or implausibly large totals. Call this as "
        "the LAST step, once you have derived hours for the tasks you could ground, "
        "and address any issues it reports. Returns {ok, issues}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "components": {
                "type": "array",
                "description": "The recovered tasks, with their final hours and references.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "estimated_hours": {"type": "number"},
                        "reference_amounts": {
                            "type": "array",
                            "items": {"type": "number"},
                        },
                    },
                    "required": ["name", "estimated_hours", "reference_amounts"],
                    "additionalProperties": False,
                },
            },
            "total_hours": {
                "type": "number",
                "description": "The sum of the recovered task hours.",
            },
        },
        "required": ["components", "total_hours"],
        "additionalProperties": False,
    },
    "strict": True,
}

# Phase 1 (structure) uses no tools; phase 2 (hours recovery) uses all three.
STRUCTURE_TOOL_SCHEMAS: list[dict[str, Any]] = []
HOURS_TOOL_SCHEMAS: list[dict[str, Any]] = [
    SEARCH_BUDGETS_TOOL,
    DERIVE_TASK_HOURS_TOOL,
    VALIDATE_ESTIMATE_TOOL,
]


# --------------------------------------------------------------------------- #
# Tool implementations                                                        #
# --------------------------------------------------------------------------- #
async def search_budgets(raw_args: dict[str, Any], *, backend: RetrievalBackend) -> dict[str, Any]:
    """Retrieve historical analogs for one task via the injected backend."""
    args = SearchBudgetsArgs.model_validate(raw_args)
    sectors = args.filters.sectors if args.filters else None
    items = await backend(args.query, sectors)
    hours = [it["estimated_hours"] for it in items if it.get("estimated_hours") is not None]
    summary = (
        f"{len(items)} historical items for {args.query!r}; hours={hours}"
        if items
        else f"no historical items for {args.query!r}"
    )
    log.info("agent_tool_search_budgets", query=args.query, results=len(items))
    return {"items": items, "count": len(items), "summary": summary}


def derive_task_hours(raw_args: dict[str, Any], *, consensus_fn: ConsensusFn) -> dict[str, Any]:
    """Distance-weighted consensus over the analogs the agent found. No LLM."""
    args = DeriveTaskHoursArgs.model_validate(raw_args)
    if not args.neighbors:
        # Guard: an empty consensus is meaningless. Return a no-match so the agent
        # reports the task unresolved instead of fabricating a zero.
        return {
            "module": args.module,
            "task": args.task,
            "has_match": False,
            "summary": f"no neighbours supplied for {args.task!r}; task left unresolved",
        }
    pairs = [(n.estimated_hours, n.distance) for n in args.neighbors]
    hours, reliability, dispersion = consensus_fn(pairs)
    log.info(
        "agent_tool_derive_task_hours",
        task=args.task,
        neighbors=len(pairs),
        hours=hours,
        reliability=reliability,
    )
    return {
        "module": args.module,
        "task": args.task,
        "estimated_hours": hours,
        "reliability": reliability,
        "dispersion": dispersion,
        "has_match": True,
        "summary": f"{args.task!r}: {hours}h (reliability {reliability}) from {len(pairs)} analogs",
    }


def validate_estimate(raw_args: dict[str, Any]) -> dict[str, Any]:
    """S4-style guardrails over the recovered hours. No LLM."""
    args = ValidateEstimateArgs.model_validate(raw_args)
    issues: list[str] = []

    component_sum = 0.0
    for component in args.components:
        component_sum += component.estimated_hours
        if not component.reference_amounts:
            issues.append(f"{component.name!r} has no historical reference (unbudgeted).")
            continue
        low = min(component.reference_amounts) * 0.5
        high = max(component.reference_amounts) * 2.0
        if not (low <= component.estimated_hours <= high):
            issues.append(
                f"{component.name!r} estimate {component.estimated_hours}h is outside the "
                f"plausible range [{round(low, 1)}, {round(high, 1)}]h implied by its references."
            )

    if args.total_hours <= 0:
        issues.append("Total hours is non-positive.")
    if abs(component_sum - args.total_hours) > 0.5:
        issues.append(
            f"Total {args.total_hours}h does not match the sum of components "
            f"({round(component_sum, 1)}h)."
        )
    # A single-project estimate above ~10 person-years is almost certainly wrong.
    if args.total_hours > 20_000:
        issues.append(f"Total {args.total_hours}h is implausibly large for one project.")

    ok = not issues
    log.info("agent_tool_validate_estimate", ok=ok, issues=len(issues))
    return {
        "ok": ok,
        "issues": issues,
        "summary": "estimate passed all guardrails" if ok else f"{len(issues)} issue(s) found",
    }


# --------------------------------------------------------------------------- #
# Dispatch                                                                     #
# --------------------------------------------------------------------------- #
async def dispatch_tool(
    name: str,
    raw_args: dict[str, Any],
    *,
    backend: RetrievalBackend,
    consensus_fn: ConsensusFn,
) -> dict[str, Any]:
    """Route a tool call to its implementation.

    Raises for an unknown tool name; the loop maps any raised exception (including
    ``pydantic.ValidationError`` from bad arguments) to an error string it returns
    to the model, so a bad call never kills the loop.
    """
    if name == "search_budgets":
        return await search_budgets(raw_args, backend=backend)
    if name == "derive_task_hours":
        return derive_task_hours(raw_args, consensus_fn=consensus_fn)
    if name == "validate_estimate":
        return validate_estimate(raw_args)
    raise ValueError(f"Unknown tool: {name!r}")
