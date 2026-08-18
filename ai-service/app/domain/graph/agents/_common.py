"""Pure helpers shared by the multi-agent nodes.

Kept free of I/O so they are trivially unit-testable and can be reused by the
fan-out branch, the recovery join and the estimate builder without duplicating the
module→task bookkeeping.
"""

from __future__ import annotations

# References are historical engineer-HOURS; the headline estimate is also expressed
# in engineer-DAYS. One working day = 8 hours.
HOURS_PER_DAY = 8.0

# A grounded task below this reliability is doubtful enough to hand to the recovery
# agent (mirrors ``app/domain/agent_estimation.py::_LOW_RELIABILITY``).
LOW_RELIABILITY = 0.35


def modules_from_structure(structure: dict | None) -> list[dict]:
    """``AgentStructure`` dump → ``TaskHoursModuleInput``-shaped list of dicts.

    ``[{"name": ..., "tasks": [{"name": ..., "description": ...}]}]`` — the shape the
    human gate hands to the fan-out and that ``estimate_one`` consumes per task.
    """
    modules: list[dict] = []
    for module in (structure or {}).get("modules") or []:
        modules.append(
            {
                "name": module.get("name"),
                "tasks": [
                    {"name": task.get("name"), "description": task.get("description")}
                    for task in (module.get("tasks") or [])
                    if task.get("name")
                ],
            }
        )
    return modules


def flag_reason(task_hours: dict) -> str | None:
    """Why (if at all) a per-task hours row is worth agentic recovery.

    Mirrors ``agent_estimation._flag_reason`` but over the plain dict the fan-out
    accumulated: no match / contradictory range / low reliability.
    """
    if not task_hours.get("has_match"):
        return "no historical analog under the distance threshold"
    if task_hours.get("hours_range") is not None:
        return "historical analogs contradict (a range, not a point)"
    reliability = task_hours.get("reliability")
    if reliability is not None and reliability < LOW_RELIABILITY:
        return f"low reliability ({reliability})"
    return None


def recompute_estimate_totals(modules: list[dict]) -> dict:
    """The four headline totals derived from a module→task tree's ``estimated_hours``.

    A task is "grounded" when its ``estimated_hours`` is not ``None`` (so a human who
    fills a previously-unmatched task at gate 2 grounds it). Shared by ``build_estimate``
    and the gate-2 override path so the arithmetic lives in exactly one place.
    """
    total_hours = 0.0
    grounded = 0
    total_tasks = 0
    for module in modules or []:
        for task in module.get("tasks") or []:
            total_tasks += 1
            hours = task.get("estimated_hours")
            if hours is not None:
                total_hours += hours
                grounded += 1

    ratio = round(grounded / total_tasks, 3) if total_tasks else 0.0
    if total_tasks and grounded == total_tasks:
        confidence = "high"
    elif grounded == 0:
        confidence = "low"
    else:
        confidence = "medium"
    return {
        "total_engineer_hours": round(total_hours, 1),
        "total_engineer_days": round(total_hours / HOURS_PER_DAY),
        "grounded_task_ratio": ratio,
        "confidence": confidence,
    }


def build_estimate(approved_modules: list[dict], task_hours: list[dict]) -> dict:
    """Assemble the structured estimate from the approved tree + per-task hours.

    Walks the human-approved module→task tree and grafts each task's grounded hours
    (matched by ``(module, task)``), then sums to totals. A task with no match keeps
    ``estimated_hours=None`` (flagged for the human at gate 2).
    """
    by_key = {(t.get("module"), t.get("task")): t for t in task_hours}
    out_modules: list[dict] = []
    for module in approved_modules:
        tasks_out: list[dict] = []
        for task in module.get("tasks") or []:
            est = by_key.get((module.get("name"), task.get("name")))
            tasks_out.append(
                {
                    "name": task.get("name"),
                    "description": task.get("description"),
                    "estimated_hours": est.get("estimated_hours") if est else None,
                    "reliability": est.get("reliability") if est else None,
                    "has_match": bool(est and est.get("has_match")),
                }
            )
        out_modules.append({"name": module.get("name"), "tasks": tasks_out})

    return {"modules": out_modules, **recompute_estimate_totals(out_modules)}
