"""``analysis_agent`` — validates the data and writes a reliability report.

After the hours agent hands over the estimate, this agent reads it and writes a short
report for the human's final review: an overall confidence, the fraction of tasks
that got grounded hours, and the specific weak points (ungrounded tasks, contradictory
analogs, low reliability). It does NOT change the numbers — it tells the human HOW
MUCH to trust them and WHERE the estimate is soft, so gate 2 is an informed review.

The deterministic grounded-task ratio is computed here and handed to the LLM so the
report's headline number is trustworthy; the LLM authors the prose weak-points.
"""

from __future__ import annotations

import asyncio

import logfire
import structlog

from app.config import get_settings
from app.domain.graph.personas import persona_for
from app.domain.graph.schemas import ReliabilityReport

log = structlog.get_logger()

_ANALYSIS_SYSTEM_PROMPT = (
    "You are an estimation reviewer. You are given a structured software estimate: "
    "modules → tasks, each task with derived engineer-hours, a reliability score "
    "(0..1) and whether it matched a historical analog. Write a RELIABILITY REPORT for "
    "the human who will approve it:\n"
    "- overall_confidence: how much to trust the estimate as a whole.\n"
    "- grounded_task_ratio: the fraction of tasks with grounded hours (use the value "
    "given in the input; do not recompute).\n"
    "- weak_points: the specific soft spots the human must check or complete — tasks "
    "with no match, low reliability, or contradictory analogs. Be concrete.\n"
    "- summary: a short honest prose read. Never invent numbers; only judge the ones given."
)


def _grounded_ratio(estimate: dict) -> float:
    tasks = [t for m in estimate.get("modules") or [] for t in (m.get("tasks") or [])]
    if not tasks:
        return 0.0
    grounded = sum(1 for t in tasks if t.get("estimated_hours") is not None)
    return round(grounded / len(tasks), 3)


def _estimate_digest(estimate: dict, ratio: float) -> str:
    """Compact, LLM-readable digest of the estimate for the report call."""
    lines = [
        f"total_engineer_days: {estimate.get('total_engineer_days')}",
        f"total_engineer_hours: {estimate.get('total_engineer_hours')}",
        f"grounded_task_ratio: {ratio}",
        "tasks:",
    ]
    for module in estimate.get("modules") or []:
        for task in module.get("tasks") or []:
            hours = task.get("estimated_hours")
            hours_text = f"{hours}h" if hours is not None else "NO MATCH"
            lines.append(
                f"  - [{module.get('name')}] {task.get('name')}: {hours_text} "
                f"(reliability={task.get('reliability')}, has_match={task.get('has_match')})"
            )
    return "\n".join(lines)


async def analysis_agent(state: dict) -> dict:
    """Estimate → reliability report (structured LLM call)."""
    with logfire.span("node: analysis_agent"):
        settings = get_settings()
        from app.dependencies import get_llm_wrapper

        wrapper = get_llm_wrapper()
        estimate = state.get("estimate") or {}
        ratio = _grounded_ratio(estimate)
        user_message = _estimate_digest(estimate, ratio)
        persona = persona_for("analysis_agent", enabled=settings.GRAPH_PERSONAS_ENABLED)
        system_prompt = f"{persona}\n\n{_ANALYSIS_SYSTEM_PROMPT}" if persona else _ANALYSIS_SYSTEM_PROMPT
        report, _meta = await asyncio.to_thread(
            wrapper.complete_structured,
            system_prompt=system_prompt,
            user_message=user_message,
            response_model=ReliabilityReport,
            model_override=settings.GRAPH_ANALYSIS_MODEL,
        )
        # Trust the deterministic ratio over whatever the model echoed.
        report.grounded_task_ratio = ratio
        log.info(
            "agent_analysis_done",
            overall_confidence=report.overall_confidence,
            grounded_task_ratio=ratio,
            weak_points=len(report.weak_points),
        )
        return {"analysis_report": report.model_dump()}
